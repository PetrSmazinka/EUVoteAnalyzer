"""
LLM abstraction layer with rate-limiting and optional disk caching.

Provides two public interfaces:
  LLM       – stateless façade; calls the configured provider directly.
  SmartLLM  – caching façade; stores responses on disk and replays them on
               repeated calls with the same cache key, avoiding redundant
               API charges.

Supported providers: Gemini (via google-genai) and Ollama (local inference).
New providers can be added by subclassing LLMStrategy.
"""

import json
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import List, Dict, Any, cast

from EUVoteAnalyzer.core.common import MAX_LLM_ATTEMPTS
from EUVoteAnalyzer.core.utils import ProgressPrint
from EUVoteAnalyzer.core.storage import LocalCache


def _matches_schema(value: Any, schema: Dict[str, Any]) -> bool:
    """
    Check *value* against *schema*, using the same small dict-based schema
    format as ``LLM_RESPONSE_SCHEMAS`` (``type`` one of ARRAY/OBJECT/STRING/
    INTEGER/NUMBER/BOOLEAN, plus ``items``/``properties``/``required``).

    Not a full JSON-Schema implementation -- only the subset this project's
    schemas actually use. An unrecognised ``type`` is treated as a pass
    (permissive default) rather than a failure.
    """
    t = schema.get("type")
    if t == "ARRAY":
        if not isinstance(value, list):
            return False
        items_schema = schema.get("items")
        return items_schema is None or all(_matches_schema(v, items_schema) for v in value)
    if t == "OBJECT":
        if not isinstance(value, dict):
            return False
        for required_key in schema.get("required", []):
            if required_key not in value:
                return False
        properties = schema.get("properties", {})
        return all(
            _matches_schema(v, properties[k]) for k, v in value.items() if k in properties
        )
    if t == "STRING":
        return isinstance(value, str)
    if t == "BOOLEAN":
        return isinstance(value, bool)
    if t == "INTEGER":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "NUMBER":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def response_matches_schema(raw_text: str, schema: Dict[str, Any] | None) -> bool:
    """``True`` if *raw_text* parses as JSON and matches *schema* (or *schema* is None)."""
    if schema is None:
        return True
    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return False
    return _matches_schema(parsed, schema)

class RateLimiter:
    """
    Token-bucket rate limiter enforcing per-minute and per-day API quotas.

    All limits are optional (0 = unlimited).  Exceeding a limit blocks the
    calling thread via ``time.sleep`` rather than raising an exception, so
    long batch jobs complete unattended without manual restart.

    Args:
        rpm: Maximum requests per minute.
        tpm: Maximum tokens per minute (estimated from prompt length).
        rpd: Maximum requests per day.
    """

    def __init__(self, rpm: int = 0, tpm: int = 0, rpd: int = 0):
        self.rpm = rpm
        self.tpm = tpm
        self.rpd = rpd
        self.requests_this_minute = 0
        self.tokens_this_minute = 0
        self.requests_today = 0
        self.minute_start = time.time()
        self.day_start = datetime.now().date()

    def wait_if_needed(self, estimated_tokens: int):
        """
        Block the caller until the current request fits within all active quotas.

        Resets per-minute counters on window rollover and sleeps until midnight
        when the daily limit is exhausted.

        Args:
            estimated_tokens: Rough token count for the outgoing prompt
                              (prompt length ÷ 4 + expected output tokens).
        """
        if self.rpm == 0 and self.tpm == 0 and self.rpd == 0:
            return

        now = time.time()
        today = datetime.now().date()

        if today > self.day_start:
            self.day_start = today
            self.requests_today = 0

        if now - self.minute_start >= 60:
            self.minute_start = now
            self.requests_this_minute = 0
            self.tokens_this_minute = 0

        if self.rpd > 0 and self.requests_today >= self.rpd:
            sleep_seconds = (datetime.combine(today + timedelta(days=1), datetime.min.time()) - datetime.now()).total_seconds()
            ProgressPrint.log(f"Daily rate limit (RPD={self.rpd}) reached. Sleeping for {sleep_seconds:.0f}s", "WARN")
            time.sleep(sleep_seconds)
            self.requests_today = 0
            self.day_start = datetime.now().date()
            self.minute_start = time.time()
            self.requests_this_minute = 0
            self.tokens_this_minute = 0

        if self.tpm > 0 and estimated_tokens > self.tpm:
            ProgressPrint.log(f"Request estimated at {estimated_tokens} tokens exceeds TPM={self.tpm} — sending anyway", "WARN")
        else:
            while (self.rpm > 0 and self.requests_this_minute >= self.rpm) or \
                  (self.tpm > 0 and self.tokens_this_minute + estimated_tokens > self.tpm):
                sleep_seconds = 60.0 - (time.time() - self.minute_start)
                if sleep_seconds > 0:
                    ProgressPrint.log(f"Minute rate limit (RPM={self.rpm}, TPM={self.tpm}) reached. Sleeping for {sleep_seconds:.1f}s", "WARN")
                    time.sleep(sleep_seconds)
                self.minute_start = time.time()
                self.requests_this_minute = 0
                self.tokens_this_minute = 0

        self.requests_this_minute += 1
        self.requests_today += 1
        self.tokens_this_minute += estimated_tokens

class LLMStrategy(ABC):
    """Abstract base for provider-specific generation backends."""

    @abstractmethod
    def generate(self, prompt: str, schema: Dict[str, Any]|None = None) -> str:
        """
        Submit *prompt* to the model and return its raw text response.

        Args:
            prompt: The complete prompt string to send.
            schema: Optional structured-output schema (provider-specific format).

        Returns:
            The model's text response, or an empty string on failure.
        """
        pass

    def clean_json_string(self, raw_output: str) -> str:
        """Strip markdown code fences that some models wrap around JSON responses."""
        clean = raw_output.strip()
        if clean.startswith("```json"):
            clean = clean[7:]
        elif clean.startswith("```"):
            clean = clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]

        return clean.strip()

class GeminiStrategy(LLMStrategy):
    """
    LLMStrategy implementation backed by Google's Gemini API (google-genai SDK).

    Retries on 503 (server unavailable) and 429 (quota exceeded) with
    exponential back-off.  The retry delay for 429 is taken from the
    ``retryDelay`` field of the API error response when available.

    Args:
        api_key:      Google API key with Gemini access.
        model_name:   Gemini model identifier (e.g. ``"gemma-3-27b-it"``).
        support_json: When True, uses native structured-output mode (requires
                      the model to support ``response_schema``).
    """

    def __init__(self, api_key: str, model_name: str, support_json: bool = False):
        try:
            from google import genai
            from google.genai import types, errors  # Added errors
            self.types = types
            self.errors = errors
            self.client = genai.Client(api_key=api_key)
            self.model_name = model_name
            self.support_json = support_json
        except ImportError:
            ProgressPrint.log("Library google-genai is not installed", "ERR!")

    def generate(self, prompt: str, schema: Dict[str, Any]|None = None) -> str:
        """
        Generate a response, retrying on schema-invalid output.

        When *schema* is given, a response that isn't valid JSON matching it
        (e.g. empty, truncated, or containing a stray token that breaks JSON
        syntax -- all observed in practice) triggers a fresh call, up to
        ``MAX_LLM_ATTEMPTS`` times total. Each individual call still has its
        own retry-with-backoff for transient 503/429 errors (unrelated
        concern, handled by :meth:`_call_once`). If every attempt fails
        validation, the *last* attempt's raw text is returned anyway (not
        empty) so callers/logs can still see what the model actually said.
        """
        config = self.types.GenerateContentConfig(
            response_mime_type='application/json',
            response_schema=schema,
            temperature=0,
        )

        attempts = MAX_LLM_ATTEMPTS if schema is not None else 1
        text = ""
        for schema_attempt in range(attempts):
            text = self._call_once(prompt, config)
            if response_matches_schema(text, schema):
                return text
            if schema_attempt + 1 < attempts:
                ProgressPrint.log(
                    f"Response failed schema validation (attempt {schema_attempt + 1}/{attempts}); retrying",
                    "WARN",
                )
        return text

    def _call_once(self, prompt: str, config: Any) -> str:
        """Issue one ``generate_content`` call, retrying on transient 503/429 errors."""
        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config if self.support_json else None
                )
                return self.clean_json_string(response.text or "")

            except self.errors.ServerError as e:
                if e.code == 503:
                    wait_seconds = (2 ** (attempt + 1)) + 5
                    ProgressPrint.log(f"Server unavailable (503). Retrying in {wait_seconds}s (attempt {attempt + 1}/{max_retries})", "WARN")
                    time.sleep(wait_seconds)
                else:
                    raise e

            except self.errors.ClientError as e:
                if e.code == 429:
                    wait_seconds = (2 ** (attempt + 1)) + 5

                    try:
                        details_list = cast(List[Dict[str, str]], e.details) #type: ignore
                        for detail in details_list:
                            if detail.get('@type') == 'type.googleapis.com/google.rpc.RetryInfo':
                                raw_delay = detail.get('retryDelay', "0s")
                                wait_seconds = float(raw_delay.rstrip('s')) + 0.5
                                break
                    except Exception:
                        pass

                    ProgressPrint.log(f"Quota exceeded. API requested wait: {wait_seconds}s", "WARN")
                    time.sleep(wait_seconds)
                else:
                    raise e

        return ""
    

class OllamaStrategy(LLMStrategy):
    """
    LLMStrategy implementation backed by a locally running Ollama server.

    Requires the ``ollama`` Python package and an Ollama daemon reachable on
    the default port.  No rate limiting or retry logic is applied — the local
    server is assumed to be always available.

    Args:
        model_name: Ollama model tag (e.g. ``"llama3.2:latest"``).
    """

    def __init__(self, model_name: str):
        try:
            import ollama
            self.ollama = ollama
            self.model_name = model_name
        except ImportError:
            ProgressPrint.log("Library ollama is not installed", "ERR!")

    def generate(self, prompt: str, schema: Dict[str, Any]|None = None) -> str:
        response: Any = self.ollama.chat( # type: ignore
            model=self.model_name,
            messages=[{
                'role': 'user', 
                'content': prompt
            }],
            format=schema or 'json'
        )
        try:
            return self.clean_json_string(str(response.message.content or ""))
        except AttributeError:
            return str(response['message']['content'])
    
class LLM:
    """
    Stateless LLM façade that calls the active provider directly.

    Configure once via ``LLM.setup(...)`` before the first ``LLM.generate``
    call.  The class holds its state in class-level attributes so it behaves
    as a singleton regardless of where it is imported.
    """

    _strategy: LLMStrategy|None = None
    _rate_limiter: RateLimiter|None = None

    @classmethod
    def setup(cls, provider: str, api_key: str|None = None, model: str|None = None, support_json: bool = False, rpm: int = 0, tpm: int = 0, rpd: int = 0):
        """
        Initialise the active provider and rate limiter.

        Args:
            provider:     ``"gemini"`` or ``"ollama"``.
            api_key:      Required for Gemini; ignored for Ollama.
            model:        Model identifier; defaults to a sensible per-provider value.
            support_json: Pass True to use Gemini's native structured-output mode.
            rpm:          Requests per minute limit (0 = unlimited).
            tpm:          Tokens per minute limit (0 = unlimited).
            rpd:          Requests per day limit (0 = unlimited).
        """
        p = provider.lower()
        if p == "gemini":
            if not api_key:
                ProgressPrint.log("Gemini requires setting API key", "ERR!")
                return
            cls._strategy = GeminiStrategy(api_key, model or "gemini-2.5-flash", support_json)
        elif p == "ollama":
            cls._strategy = OllamaStrategy(model or "llama3.2:latest")
        else:
            ProgressPrint.log("Unknown LLM provider", "ERR!")
        
        cls._rate_limiter = RateLimiter(rpm, tpm, rpd)

    _ESTIMATED_OUTPUT_TOKENS = 500

    @classmethod
    def generate(cls, prompt: str, schema: Dict[str, Any]|None = None) -> str:
        """
        Send *prompt* to the active provider and return its response.

        Applies rate limiting before dispatching.  Returns an empty string
        if the provider has not been configured via ``LLM.setup``.

        Args:
            prompt: The complete prompt string.
            schema: Optional structured-output schema.

        Returns:
            The model's text response.
        """
        if cls._strategy is None:
            ProgressPrint.log("LLM is not set up", "ERR!")
            return ""
        if cls._rate_limiter is not None:
            cls._rate_limiter.wait_if_needed(len(prompt) // 4 + cls._ESTIMATED_OUTPUT_TOKENS)
        return cls._strategy.generate(prompt, schema)


class SmartLLM:
    """
    Cache-aware LLM façade that persists responses to disk via LocalCache.

    On each ``generate`` call the cache is checked first.  A cache hit skips
    the API call entirely, making repeated pipeline runs fast and free.  On a
    cache miss the response is fetched from the provider and immediately stored
    so subsequent runs can replay it.

    When *cache* is ``None`` the class falls back to direct provider calls,
    behaving identically to :class:`LLM`.
    """

    _strategy: LLMStrategy|None = None
    _rate_limiter: RateLimiter|None = None
    _local_cache: LocalCache|None = None

    @classmethod
    def setup(cls, cache: LocalCache|None, provider: str, api_key: str|None = None, model: str|None = None, support_json: bool = False, rpm: int = 0, tpm: int = 0, rpd: int = 0):
        """
        Initialise the cache, provider, and rate limiter.

        Args:
            cache:        LocalCache instance to use, or None to disable caching.
            provider:     ``"gemini"`` or ``"ollama"``.
            api_key:      Required for Gemini.
            model:        Model identifier.
            support_json: Enable Gemini native structured-output mode.
            rpm / tpm / rpd: Rate limits (0 = unlimited).
        """
        cls._local_cache = cache
        p = provider.lower()
        if p == "gemini":
            if not api_key:
                ProgressPrint.log("Gemini requires setting API key", "ERR!")
                return
            cls._strategy = GeminiStrategy(api_key, model or "gemini-2.5-flash", support_json)
        elif p == "ollama":
            cls._strategy = OllamaStrategy(model or "llama3.2:latest")
        else:
            ProgressPrint.log("Unknown LLM provider", "ERR!")
        
        cls._rate_limiter = RateLimiter(rpm, tpm, rpd)

    _ESTIMATED_OUTPUT_TOKENS = 500

    @classmethod
    def generate(cls, cache_path: str, prompt: str, schema: Dict[str, Any]|None = None) -> str:
        """
        Return the cached response for *cache_path* or call the provider.

        The cache key is a filesystem-safe path relative to the cache root
        (e.g. ``"llm/topics/12345"``).  If a cached file exists and is within
        the validity window, no API call is made.

        Args:
            cache_path: Unique key identifying this prompt in the cache.
            prompt:     The complete prompt string (used only on cache miss).
            schema:     Optional structured-output schema.

        Returns:
            The model's text response (possibly from cache).
        """
        if cls._local_cache is None:
            if cls._strategy is None:
                ProgressPrint.log("LLM is not set up", "ERR!")
                return ""
            if cls._rate_limiter is not None:
                cls._rate_limiter.wait_if_needed(len(prompt) // 4 + cls._ESTIMATED_OUTPUT_TOKENS)
            ProgressPrint.log(f"Query {cache_path} succesfully executed", "OK")
            response = cls._strategy.generate(prompt, schema)
            return response
        else:
            cached_result = cls._local_cache.load(cache_path)
            ProgressPrint.log(f"Fetching LLM query {cache_path} from cache", "INFO")
            if cached_result is not None:
                ProgressPrint.log(f"Query {cache_path} successfully fetched from cache", "OK")
                return cast(str, cached_result)
            ProgressPrint.log(f"Query {cache_path} not found in cache", "WARN")
            if cls._strategy is None:
                ProgressPrint.log("LLM is not set up", "ERR!")
                return ""
            if cls._rate_limiter is not None:
                cls._rate_limiter.wait_if_needed(len(prompt) // 4 + cls._ESTIMATED_OUTPUT_TOKENS)
            ProgressPrint.log(f"Query {cache_path} succesfully executed", "OK")
            response = cls._strategy.generate(prompt, schema)
            cls._local_cache.store(cache_path, response)
            return response

    