"""
Disk-backed caching and JSON traversal utilities.

LocalCache       – stores and retrieves JSON, pandas DataFrames, and raw HTML
                   on the local filesystem, keyed by a URL-derived path.
JSONWalker       – cursor-style navigator for deeply nested JSON structures.
SmartLoader      – cache-aware wrapper around any Loader; avoids redundant
                   HTTP requests by writing each response to LocalCache.
SmartSparqlQuery – cache-aware wrapper around SparqlQuery.
"""

import json
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Self

from EUVoteAnalyzer.core.common import JsonValue, CacheFileType, CACHE_VALIDITY_DAYS
from EUVoteAnalyzer.core.utils import ProgressPrint
from EUVoteAnalyzer.core.networking import Loader, URLBuilder

class LocalCache:
    """
    Filesystem cache that persists JSON, DataFrames, and raw HTML by URL-derived key.

    Files are stored under a configurable root directory with extensions that encode
    the content type (``.json``, ``.pd``, ``.html``).  A modification-time check gates
    cache hits so stale entries are transparently ignored without manual invalidation.
    """

    def __init__(self, local_path : str = "cache", valid_since : int = CACHE_VALIDITY_DAYS):
        """
        Args:
            local_path:  Root directory for all cached files.
            valid_since: Maximum age (in days) of a cache file before it is treated
                         as a miss.  Files older than this threshold are ignored.
        """
        self._local_path = local_path
        self._validity = datetime.now() - timedelta(days=valid_since)

    def store(self, key: str, content: Dict[str, Any]|List[Dict[str, Any]]|pd.DataFrame|str) -> None:
        """
        Persist *content* to disk under the given *key*.

        The content type determines the file extension and serialisation format:
        ``str`` → ``.html``, ``pd.DataFrame`` → ``.pd`` (JSON-records), anything
        else → ``.json``.

        Args:
            key:     Filesystem-safe path relative to the cache root (e.g.
                     ``"api/votes/2024-01-01"``).
            content: Data to persist; the concrete type selects the format.
        """
        file_base_path = Path(f"{self._local_path}/{key}")
        file_base_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            with open(f"{file_base_path}.html", "w", encoding="utf-8") as f:
                f.write(content)
        elif isinstance(content, pd.DataFrame):
            content.to_json(f"{file_base_path}.pd", orient='records', indent=4, force_ascii=False)
        else:
            with open(f"{file_base_path}.json", "w", encoding="utf-8") as f:
                json_content = json.dumps(content, indent=4, ensure_ascii=False)
                f.write(json_content)

    def load(self, key: str) -> None|List[Dict[str, Any]]|pd.DataFrame|str:
        """
        Retrieve a previously cached value, or return ``None`` on miss or expiry.

        Tries each extension (``.json``, ``.pd``, ``.html``) in order and checks the
        file's modification time against the validity window before deserialising.

        Args:
            key: The same key that was used in the corresponding :meth:`store` call.

        Returns:
            The deserialised content, or ``None`` if no fresh cache file is found.
        """
        file_json_path = Path(f"{self._local_path}/{key}.json")
        file_pd_path = Path(f"{self._local_path}/{key}.pd")
        file_txt_path = Path(f"{self._local_path}/{key}.html")
        if file_json_path.is_file():
            mode = CacheFileType.JSON
            path = file_json_path
        elif file_pd_path.is_file():
            mode = CacheFileType.DATAFRAME
            path = file_pd_path
        elif file_txt_path.is_file():
            mode = CacheFileType.HTML
            path = file_txt_path
        else:
            return None
        mtime_timestamp = path.stat().st_mtime
        file_mtime = datetime.fromtimestamp(mtime_timestamp)
        if(file_mtime < self._validity):
            return None
        if mode == CacheFileType.JSON:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        elif mode == CacheFileType.DATAFRAME:
            return pd.read_json(str(path), orient='records')
        if mode == CacheFileType.HTML:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
            
    @staticmethod
    def clear_cache(path: str):
        """
        Delete every ``.json``, ``.pd``, and ``.html`` file found under *path*.

        Walks the directory tree recursively.  Failures on individual files are
        caught and logged rather than aborting the whole operation.

        Args:
            path: Root directory to sweep.
        """
        directory = Path(path)
        extensions = {'.json', '.pd', '.html'}
        files_to_delete = [f for f in directory.rglob('*') if f.is_file() and f.suffix in extensions]
        total_length = len(files_to_delete)
        ProgressPrint.initialize("Deleting files from cache")
        for i, file in enumerate(files_to_delete):
            ProgressPrint.progress(i, total_length, "Deleting")
            ProgressPrint.log(f"Deleting file {ProgressPrint.BOLD}{file}{ProgressPrint.END}")
            try:
                file.unlink()
                ProgressPrint.log(f"File {ProgressPrint.BOLD}{file}{ProgressPrint.END} has been deleted", "OK")
            except:
                ProgressPrint.log(f"An error has occured when deleting {ProgressPrint.BOLD}{file}{ProgressPrint.END}")
        ProgressPrint.finalize(total_length)

class JSONWalker:
    """
    Cursor-style navigator for deeply nested JSON structures.

    Maintains an internal path stack that is applied lazily on :meth:`get`.
    The wildcard segment ``"*"`` expands every key in a dict or every element
    in a list, enabling bulk extraction from repeated structures.

    Typical usage::

        walker = JSONWalker(data)
        values = walker.enter("results/*/label").get()
    """

    def __init__(self, data: JsonValue, path: List[str] = []):
        """
        Args:
            data: The root JSON value to navigate.
            path: Initial path stack (segments already descended into).
        """
        self._data = data
        self._path = list(path)

    def clone(self) -> Self:
        """Return an independent copy of this walker at the same position."""
        return self.__class__(self._data, list(self._path))

    def enter(self, path: URLBuilder|str) -> Self:
        """
        Descend into one or more nested keys, returning *self* for chaining.

        Args:
            path: A ``"/"``-separated key sequence or a :class:`URLBuilder` whose
                  relative path is used.  The wildcard ``"*"`` is supported at any
                  level.

        Returns:
            *self* (mutated in-place) to allow fluent chaining.
        """
        if isinstance(path, str):
            array = path.split("/")
        else:
            array = path.relative_path().split("/")
        for item in array:
            self._path.append(item)
        return self
    
    def leave(self, levels: int = 1) -> Self:
        """
        Ascend *levels* steps up the path stack, returning *self* for chaining.

        Args:
            levels: Number of path segments to pop.

        Returns:
            *self* (mutated in-place).
        """
        for _ in range(levels):
            self._path.pop()
        return self
    
    def get(self) -> JsonValue:
        """
        Evaluate the current path against the root data and return the result.

        When the path contains a ``"*"`` segment the return value is a flat list
        of all matching leaves; otherwise a single value (or ``None``) is returned.

        Returns:
            Extracted value(s), or ``None`` if the path does not resolve.
        """
        candidates : JsonValue = [self._data]
        for element in self._path:
            next_candidates : JsonValue = []
            for item in candidates:
                if item is None:
                    continue
                if element == "*":
                    if isinstance(item, dict):
                        next_candidates.extend(item.values())
                    elif isinstance(item, list):
                        next_candidates.extend(item)
                elif isinstance(item, dict):
                    if element in item:
                        next_candidates.append(item.get(element))
                elif isinstance(item, list):
                    if element.isdigit():
                        idx = int(element)
                        if 0 <= idx < len(item):
                            next_candidates.append(item[idx])       
            candidates = next_candidates
            if not candidates:
                break
        if "*" in self._path:
            return candidates
        return candidates[0] if candidates else None

class SmartLoader:
    """
    Cache-aware wrapper around any :class:`~EUVoteAnalyzer.core.networking.Loader`.

    On each :meth:`load` call the class first checks the shared :class:`LocalCache`
    instance (set via :meth:`setup`).  Only when the cache misses does it delegate
    to the underlying ``Loader`` and then persist the response so subsequent runs
    are served from disk.

    The class-level cache is configured once for the whole process via
    :meth:`SmartLoader.setup`.
    """

    _cache : LocalCache|None = None

    @property
    def used_cache(self) -> bool:
        """``True`` if the most recent :meth:`load` call was served from cache."""
        return self._used_cache

    @staticmethod
    def setup(local_cache: LocalCache|None):
        """
        Set the shared cache for all :class:`SmartLoader` instances.

        Args:
            local_cache: A configured :class:`LocalCache`, or ``None`` to disable
                         caching (every request will hit the network).
        """
        SmartLoader._cache = local_cache

    def __init__(self, loader: Loader):
        """
        Args:
            loader: Concrete :class:`~EUVoteAnalyzer.core.networking.Loader`
                    implementation used for network fetches on cache misses.
        """
        self._loader = loader
        self._used_cache = False

    def load(self, url_builder : URLBuilder, loading : str|None = None, not_in_cache : str|None = None,
             fetching : str|None = None, fetched : str|None = None) -> List[Dict[str, Any]]|pd.DataFrame|str|None:
        """
        Return the resource identified by *url_builder*, hitting the cache first.

        Emits structured progress messages at each stage.  The optional string
        parameters let callers override the default log messages with more
        context-specific phrasing.

        Args:
            url_builder:  Builder that produces the full URL and the cache key
                          (via :meth:`~URLBuilder.endpoint`).
            loading:      Custom message for the cache-lookup stage.
            not_in_cache: Custom message logged on a cache miss.
            fetching:     Custom message logged just before the network request.
            fetched:      Custom message logged on successful retrieval.

        Returns:
            The resource content (JSON list, DataFrame, or HTML string),
            or ``None`` if both the cache and the network request fail.
        """
        url = url_builder.build()
        short_url = url_builder.build()
        endpoint = url_builder.endpoint()
        self._used_cache = False
        if SmartLoader._cache is not None:
            if loading is not None:
                ProgressPrint.log(loading, "INFO")
            else:
                ProgressPrint.log(f"Loading {ProgressPrint.BOLD}{short_url}{ProgressPrint.END} from local cache", "INFO")
            response = SmartLoader._cache.load(endpoint)
            if response is not None:
                if fetched is not None:
                    ProgressPrint.log(fetched, "OK")
                else:
                    ProgressPrint.log(f"Resource {ProgressPrint.BOLD}{short_url}{ProgressPrint.END} retrieved", "OK")
                self._used_cache = True
                return response
            if not_in_cache is not None:
                ProgressPrint.log(not_in_cache, "WARN")
            else:
                ProgressPrint.log(f"Resource {ProgressPrint.BOLD}{short_url}{ProgressPrint.END} is not in local cache", "WARN")
        if fetching is not None:
            ProgressPrint.log(fetching, "INFO")
        else:
            ProgressPrint.log(f"Downloading resource {ProgressPrint.BOLD}{short_url}{ProgressPrint.END}", "INFO")
        response = self._loader.load(url)
        if response is not None:
            if SmartLoader._cache is not None:
                SmartLoader._cache.store(endpoint, response)
            if fetched is not None:
                ProgressPrint.log(fetched, "OK")
            else:
                ProgressPrint.log(f"Resource {ProgressPrint.BOLD}{short_url}{ProgressPrint.END} retrieved", "OK")
            return response
        ProgressPrint.log(f"Couldn't retrieve resource {ProgressPrint.BOLD}{short_url}{ProgressPrint.END}", "ERR!")
        return None

class SmartSparqlQuery:
    """
    Cache-aware wrapper around :class:`~EUVoteAnalyzer.core.networking.SparqlQuery`.

    SPARQL queries can be expensive and quota-limited.  This class serialises
    query results to the shared :class:`LocalCache` so repeated pipeline runs
    replay from disk rather than re-querying the endpoint.

    Like :class:`SmartLoader`, the cache is configured once at process start
    via :meth:`setup`.
    """

    _cache: LocalCache | None = None

    @staticmethod
    def setup(local_cache: LocalCache | None) -> None:
        """
        Set the shared cache for all :class:`SmartSparqlQuery` instances.

        Args:
            local_cache: A configured :class:`LocalCache`, or ``None`` to disable
                         caching and always query the SPARQL endpoint directly.
        """
        SmartSparqlQuery._cache = local_cache

    def __init__(self) -> None:
        """Instantiate a new :class:`SmartSparqlQuery` backed by a fresh :class:`SparqlQuery`."""
        from EUVoteAnalyzer.core.networking import SparqlQuery
        self._sparql = SparqlQuery()
        self._used_cache = False

    @property
    def used_cache(self) -> bool:
        """``True`` if the most recent :meth:`query` call was served from cache."""
        return self._used_cache

    def query(self, query: str, cache_path: str, ignore_cache: bool = False) -> List[Dict[str, Any]] | None:
        """
        Execute a SPARQL query and return its result rows, using the cache when available.

        Args:
            query:        The complete SPARQL query string.
            cache_path:   Filesystem-safe key used to store and retrieve the result
                          (e.g. ``"sparql/wikidata/mep_birthplaces"``).
            ignore_cache: When ``True``, skip the cache lookup and always query live.

        Returns:
            A list of result-row dicts, or ``None`` if the query fails.
        """
        self._used_cache = False
        if SmartSparqlQuery._cache is not None and not ignore_cache:
            cached = SmartSparqlQuery._cache.load(cache_path)
            if cached is not None:
                ProgressPrint.log(f"SPARQL {ProgressPrint.BOLD}{cache_path}{ProgressPrint.END} retrieved from cache", "OK")
                self._used_cache = True
                return cached  # type: ignore[return-value]
            ProgressPrint.log(f"SPARQL {ProgressPrint.BOLD}{cache_path}{ProgressPrint.END} not in cache, querying", "WARN")
        result = self._sparql.query_with_retry(query)
        if result is not None:
            if SmartSparqlQuery._cache is not None:
                SmartSparqlQuery._cache.store(cache_path, result if result else [])
            ProgressPrint.log(f"SPARQL {ProgressPrint.BOLD}{cache_path}{ProgressPrint.END} retrieved", "OK")
        else:
            ProgressPrint.log(f"SPARQL {ProgressPrint.BOLD}{cache_path}{ProgressPrint.END} failed", "ERR!")
        return result

