"""
General-purpose utilities: unique-name generation, progress reporting, and data helpers.

Generator        – produces sequentially unique string identifiers with a shared prefix.
GeneratorManager – process-wide registry of Generator instances, keyed by prefix.
ProgressPrint    – ANSI-coloured console output with progress-bar support and
                   configurable verbosity levels.
ErrorHandler     – accumulates non-fatal errors and prints a formatted summary on exit.

Module-level helper functions cover common data-transformation tasks used across
the pipeline: URL basename extraction, gender resolution from EP URI suffixes,
parliamentary-term parsing, safe integer conversion, and ISO-datetime normalisation.
"""

import inspect
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, cast

class Generator:
    """
    Produces sequentially unique string identifiers sharing a common prefix.

    The first call returns ``prefix`` unmodified; subsequent calls append
    ``_1``, ``_2``, … to guarantee uniqueness within its scope.
    """

    def __init__(self, prefix: str):
        """
        Args:
            prefix: Base string prepended to every generated identifier.
        """
        self._prefix : str = prefix
        self._counter : int = 0

    def get(self) -> str:
        """Return the next unique identifier for this generator's prefix."""
        suffix = f"_{self._counter}" if self._counter > 0 else ""
        self._counter = self._counter + 1
        return f"{self._prefix}{suffix}"
    
class GeneratorManager:
    """
    Process-wide registry of :class:`Generator` instances, keyed by prefix.

    Centralises identifier generation so that any module can obtain collision-free
    names without managing its own counter.
    """

    _generators : Dict[str, Generator] = {}

    @staticmethod
    def get(prefix: str) -> str:
        """
        Return the next unique identifier for *prefix*, creating a generator on first use.

        Args:
            prefix: The shared prefix for the requested identifier family.

        Returns:
            A unique string of the form ``prefix`` or ``prefix_N``.
        """
        if not prefix in GeneratorManager._generators:
            GeneratorManager._generators[prefix] = Generator(prefix)
        return GeneratorManager._generators[prefix].get()

class ProgressPrint:
    """
    ANSI-coloured console output with an optional progress bar and verbosity filtering.

    All output is directed to ``stdout`` so it coexists cleanly with captured
    ``stderr`` in pipeline environments.  Verbosity is controlled globally via
    :meth:`setup`; levels suppress increasingly detailed message types:

    * ``0`` – show everything (INFO, OK, WARN, ERR!)
    * ``1`` – suppress INFO
    * ``2`` – suppress INFO and OK
    * ``3`` – suppress INFO, OK, and WARN (errors only)

    ANSI escape-code constants (``BLUE``, ``GREEN``, etc.) are public so callers
    can embed colour directly in log message strings.
    """

    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    END = "\033[0m"
    BOLD = "\033[1m"
    SPECIAL = "\033[97;101m"

    _initialized = False
    _last_current = 0
    _last_total = 0
    _last_prefix = ""
    _verbose_level = 1

    @staticmethod
    def setup(verbose_level : int):
        """
        Configure the global verbosity level.

        Args:
            verbose_level: 0 = all messages, 1 = hide INFO, 2 = hide INFO+OK,
                           3 = errors only.
        """
        ProgressPrint._verbose_level = verbose_level

    @staticmethod
    def initialize(message:str = ""):
        """
        Begin a new progress-bar session, optionally printing a header message.

        Args:
            message: Optional title printed above the bar (highlighted in yellow).
        """
        if message != "":
            sys.stdout.write(f"{ProgressPrint.color(message, ProgressPrint.YELLOW)}\n")
            sys.stdout.flush()
        ProgressPrint._initialized = True
        ProgressPrint._last_current = 0
        ProgressPrint._last_total = 0

    @staticmethod
    def progress(current: int, total: int, prefix: str = "Processing"):
        """
        Redraw the in-place progress bar on the current terminal line.

        Args:
            current: Number of items completed so far.
            total:   Total number of items.
            prefix:  Label printed to the left of the bar.
        """
        ProgressPrint._last_current = current
        ProgressPrint._last_total = total
        ProgressPrint._last_prefix = prefix
        
        bar_length = 30
        percent = (current / total) * 100 if total > 0 else 100
        filled = int(bar_length * current // total) if total > 0 else bar_length
        
        bar = f"{ProgressPrint.YELLOW}█{ProgressPrint.END}" * filled + \
              f"{ProgressPrint.BLUE}-{ProgressPrint.END}" * (bar_length - filled)
        status = f"{ProgressPrint.BOLD}{percent:5.1f}%{ProgressPrint.END} ({current}/{total})"
        sys.stdout.write(f"\r{ProgressPrint.YELLOW}{prefix}{ProgressPrint.END}: |{bar}| {status}\033[K")
        sys.stdout.flush()

    @staticmethod
    def log(message: str, type: str|None = "INFO", source_line : str = ""):
        """
        Emit a single log message, subject to the active verbosity filter.

        ``ERR!`` messages are also forwarded to :class:`ErrorHandler` for the
        end-of-run summary.  When a progress bar is active the current line is
        cleared before printing so the bar is not corrupted.

        Args:
            message:     Human-readable log text.
            type:        Severity tag — one of ``"INFO"``, ``"OK"``, ``"WARN"``,
                         or ``"ERR!"``.  ``None`` prints without a prefix.
            source_line: Optional extra context appended to error messages.
        """
        if ProgressPrint._verbose_level == 1 and type == "INFO": return
        if ProgressPrint._verbose_level == 2 and type in ["INFO", "OK"]: return
        if ProgressPrint._verbose_level == 3 and type in ["INFO", "OK", "WARN"]: return

        if type == "ERR!":
            ErrorHandler.occured(message)
            line = ""
            frame = inspect.currentframe()
            if frame and frame.f_back:
                line = frame.f_back.f_lineno
            formatted_msg = ProgressPrint.color(message, type)+" | "+ProgressPrint.SPECIAL+"Line: "+str(line)
            if source_line != "":
                formatted_msg = formatted_msg + ", " + source_line
            formatted_msg = formatted_msg+ProgressPrint.END
        else:
            formatted_msg = ProgressPrint.color(message, type)


        if not ProgressPrint._initialized:
            sys.stdout.write(f"{formatted_msg}\n")
            sys.stdout.flush()
            return
        sys.stdout.write(f"\r\033[K{formatted_msg}\n")
        sys.stdout.flush()
        if ProgressPrint._last_total > 0:
            ProgressPrint.progress(ProgressPrint._last_current, ProgressPrint._last_total, ProgressPrint._last_prefix)

    @staticmethod
    def color(message: str, type: str|None = "INFO") -> str:
        """
        Wrap *message* in ANSI colour codes appropriate for *type*.

        Args:
            message: The text to colour.
            type:    Severity tag or a raw ANSI escape constant.

        Returns:
            The coloured string, ready for terminal output.
        """
        if type is None:
            return message
        if type in [ProgressPrint.BLUE, ProgressPrint.BOLD, ProgressPrint.GREEN, ProgressPrint.RED, ProgressPrint.YELLOW]:
            return f"{type}{message}{ProgressPrint.END}"
        colors = {"INFO": ProgressPrint.BLUE, "WARN": ProgressPrint.YELLOW, "ERR!": ProgressPrint.RED, "OK": ProgressPrint.GREEN}
        if type not in colors:
            return f"[{ProgressPrint.center(type)}] {message}"
        color = colors.get(type)
        return f"[{color}{ProgressPrint.center(type)}{ProgressPrint.END}] {message}"
    
    @staticmethod
    def center(message : str, width: int = 4) -> str:
        """
        Pad *message* with spaces to exactly *width* characters, centred.

        Args:
            message: Text to centre.
            width:   Target column width (default 4, matching severity tag length).

        Returns:
            The padded string.
        """
        spaces = width-len(message)
        half = spaces // 2
        rest = spaces - half
        return f"{" "*half}{message}{" "*rest}"

    @staticmethod
    def finalize(total: int, message: str = "Completed"):
        """
        Complete the progress-bar session by drawing a 100 % bar and moving to the next line.

        Args:
            total:   The final item count used to fill the bar to 100 %.
            message: Label shown while the bar is at completion (default ``"Completed"``).
        """
        if ProgressPrint._initialized:
            ProgressPrint.progress(total, total, message)
            sys.stdout.write("\n")
            sys.stdout.flush()
            ProgressPrint._initialized = False
            ProgressPrint._last_total = 0
    
class ErrorHandler:
    """
    Accumulates non-fatal runtime errors and prints a formatted summary at process exit.

    Errors are registered via :meth:`occured` (typically called by
    :meth:`ProgressPrint.log` on ``"ERR!"`` messages) and displayed in a
    bordered box by :meth:`print`.
    """

    _errors: List[str] = []

    @staticmethod
    def occured(message:str):
        """
        Record a non-fatal error for later display.

        Args:
            message: Human-readable description of the error.
        """
        ErrorHandler._errors.append(message)

    @staticmethod
    def print():
        """Print a bordered summary of all accumulated errors (or a green OK banner if none)."""
        width = 80
        useful_width = width-4
        border = "#" * width
        empty = f"#{' ' * (width - 2)}#"
        if not ErrorHandler._errors:
            text_width = 30
            status = f"{ProgressPrint.GREEN}OK{ProgressPrint.END}: No errors during execution"
        else:
            text_width = 41
            status = f"{ProgressPrint.RED}ERROR{ProgressPrint.END}: There were errors during execution"
        print(f"\n{border}")
        print(empty)
        print(f"# {status}{' ' * (useful_width - text_width)} #")
        print(empty)
        for error in ErrorHandler._errors:
            chunks = [error[i:i+useful_width] for i in range(0, len(error), useful_width)]
            for chunk in chunks:
                print(f"# {ProgressPrint.RED}{chunk}{ProgressPrint.END}{' ' * (useful_width - len(chunk))} #")
            print(empty)
        print(border)

def basename(object: Any):
    """
    Return the final path component of a URI or filesystem string, leaving non-strings unchanged.

    Args:
        object: Value to process; non-strings are returned as-is.

    Returns:
        The basename string, or *object* unchanged if it is not a ``str``.
    """
    if not isinstance(object, str):
        return object
    return os.path.basename(object)

def listbasename(object: Any):
    """
    Apply :func:`basename` to every element of a list, leaving non-lists unchanged.

    Args:
        object: A list of values, or any other type.

    Returns:
        A new list of basenames, or *object* unchanged if it is not a ``list``.
    """
    if not isinstance(object, list):
        return object
    safe_list = cast(List[Any], object)
    return [basename(item) for item in safe_list]

def is_female(object: Any):
    """
    Resolve the gender encoded in an EP gender URI.

    The EP API encodes gender as a URI whose final segment is ``"FEMALE"`` or
    ``"MALE"``.  This function extracts that segment and returns a boolean.

    Args:
        object: EP gender URI string, or any other value.

    Returns:
        ``True`` if the URI resolves to ``"FEMALE"``, ``False`` if ``"MALE"``,
        or ``None`` if *object* is not a string.
    """
    if not isinstance(object, str):
        return None
    return os.path.basename(object) == "FEMALE"

def ep_term(object: Any):
    """
    Extract the numerical term identifier from an EP parliamentary-term URI.

    EP term URIs end with a segment such as ``"ep8"`` or ``"ep10"``.  This
    function strips the leading ``"ep"`` prefix (three characters) and returns
    the remainder.

    Args:
        object: EP term URI string, or any other value.

    Returns:
        The term number string (e.g. ``"10"``), or *object* unchanged if it is
        not a ``str``.
    """
    if not isinstance(object, str):
        return object
    return basename(object)[3:]

def str_to_int(text: str) -> int:
    """
    Extract all digit characters from *text* and interpret them as a single integer.

    Args:
        text: String potentially containing non-numeric characters (e.g. ``"ep10"``).

    Returns:
        Integer formed by concatenating every digit found in *text*.
    """
    numeric_str = "".join(re.findall(r'\d+', text))
    return int(numeric_str)

def to_mysql_datetime(object: Any) -> str | None:
    """
    Normalise an ISO-8601 date/datetime string to MySQL's ``DATETIME`` format.

    Strips any URL path prefix via :func:`basename`, parses the result with
    :meth:`datetime.fromisoformat`, and formats it as ``YYYY-MM-DD HH:MM:SS``.
    Falls back to appending ``" 00:00:00"`` to date-only strings when full
    parsing fails.

    Args:
        object: ISO-8601 string (possibly a full URI) or any other value.

    Returns:
        A ``"YYYY-MM-DD HH:MM:SS"`` string, or ``None`` if the value cannot be
        interpreted as a date.
    """
    if not isinstance(object, str):
        return object
    try:
        dt = datetime.fromisoformat(basename(object))
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return f"{object[:10]} 00:00:00" if len(object) >= 10 else None
 
def debug(data : Any, is_json: bool = False):
    """
    Pretty-print *data* to the console for development inspection.

    Handles dicts, lists, and scalar values.  When *is_json* is ``True`` the
    value is assumed to already be a well-formed JSON string and is printed
    verbatim via :func:`_debug`.

    Args:
        data:    Value to inspect.
        is_json: When ``True``, skip JSON serialisation and print *data* as-is.
    """
    if is_json:
        _debug(data, is_json)
        return
    elif isinstance(data, Dict):
        data = cast(Dict[str, Any], data)
        for key , value in data.items():
            try:
                serialized = json.dumps(value, indent=4, ensure_ascii=False)
            except:
                serialized = value
            ProgressPrint.log(f"{ProgressPrint.BLUE}{f"{key} : {serialized}"}{ProgressPrint.END}", "████")
            return
    elif isinstance(data, List):
        data = cast(List[Any], data)
        for value in data:
            _debug(value)
        return
    _debug(data)

def _debug(data: Any, is_json: bool = False):
    """
    Internal helper: serialise *data* to a JSON string (unless *is_json*) and emit it.

    Args:
        data:    Value to print.
        is_json: When ``True``, print *data* without further serialisation.
    """
    to_print = data
    if not is_json:
        try: 
            to_print = json.dumps(data, indent=4, ensure_ascii=False)
        except:
            pass
    ProgressPrint.log(f"{ProgressPrint.BLUE}{to_print}{ProgressPrint.END}", "████")
