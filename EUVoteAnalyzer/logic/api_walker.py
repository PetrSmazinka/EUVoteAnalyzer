"""
Fluent builder for iterating and extracting structured data from JSON-LD API responses.

APIWalker provides a declarative, chainable interface for navigating the EP Open
Data API (JSON-LD format).  Each method accumulates extraction rules; a final
:meth:`execute` call fetches the endpoint (via :class:`SmartLoader`) and applies
all rules to produce a normalised list of record dicts.

Supported extraction modes:
  select()          – extract one or more scalar fields by JSON path.
  select_one_of()   – extract the first non-null value from a list of candidate paths.
  select_all()      – pass each top-level record through unchanged.
  select_recursive()– recurse into a nested list/dict using a child APIWalker.
  join()            – follow a value as a new endpoint URL and merge the response.
  filter()          – discard records that do not satisfy a predicate.
  transform()       – apply a post-processing function to the entire result list.
"""

from typing import Any, Callable, Dict, List, Self, Tuple, cast

from EUVoteAnalyzer.core.common import JsonValue, ValueType
from EUVoteAnalyzer.core.networking import URLBuilder, URLLoader
from EUVoteAnalyzer.core.storage import SmartLoader
from EUVoteAnalyzer.core.utils import ProgressPrint

class APIWalker:
    """
    Fluent builder that extracts normalised records from a JSON-LD API endpoint.

    Instantiate, chain builder methods to declare what to extract, then call
    :meth:`execute` to fetch the data and apply all extraction rules.  The
    result is always a list of flat record dicts.

    All builder methods return *self* so calls can be chained::

        walker = (
            APIWalker()
            .endpoint(ep_url)
            .select(ValueType.REQUIRED, path_to_id, "id")
            .select(ValueType.OPTIONAL, path_to_name, "name")
            .filter(lambda r: r["id"] is not None)
        )
        records = walker.execute()
    """

    def __init__(self):
        """Initialise an empty walker with no extraction rules configured."""
        self._items : List[Tuple[ValueType, List[URLBuilder|str], List[str]|None, List[Callable[[Any], Any]]|None]] = []
        self._group_items : List[Tuple[ValueType, List[URLBuilder], str|None, Callable[[Any], Any]|None]] = []
        self._recursive_items : List[Tuple[ValueType, URLBuilder, APIWalker, str|None, Callable[[Any], Any]|None]] = []
        self._join_items : List[Tuple[ValueType, URLBuilder, APIWalker, str|None]] = []
        self._filters : List[Callable[[Any], bool]] = []
        self._transformers : List[Callable[[List[Dict[str, Any]]],Any]] = []   
        self._url_builder : URLBuilder|None = None
        self._binding : str|None = None
        self._transform_func : Callable[[str], str]|None = None
        self._select_all = False
        self._loader : SmartLoader = SmartLoader(URLLoader())

    @property
    def used_cache(self) -> bool:
        """``True`` if the most recent :meth:`execute` call was served from cache."""
        return self._loader.used_cache

    def select(self, type : ValueType, path : List[URLBuilder]|URLBuilder|str, \
               aliases : List[str]|str|None = None, transformers : List[Callable[[Any], Any]]|None = None) -> Self:
        """
        Register extraction of one or more scalar fields by JSON path.

        Args:
            type:         :class:`~EUVoteAnalyzer.core.common.ValueType` controlling
                          how a missing value is handled (REQUIRED aborts; OPTIONAL
                          produces ``None``).
            path:         One or several :class:`URLBuilder` paths (or plain strings
                          used as literal output values).
            aliases:      Output key names; defaults to the path basename.
            transformers: Optional callables applied element-wise to the extracted
                          values before storing them in the result dict.

        Returns:
            *self* for chaining.
        """
        final_path: List[URLBuilder | str]
        if isinstance(path, (URLBuilder, str)):
            final_path = [path]
        else:
            final_path = list(path) 
        final_aliases: List[str] | None = None
        if isinstance(aliases, str):
            final_aliases = [aliases]
        else:
            final_aliases = aliases
        self._items.append((type, final_path, final_aliases, transformers))
        return self
    
    def select_one_of(self, type : ValueType, paths : List[URLBuilder], \
                      alias : str|None = None, transformer : Callable[[Any], Any]|None = None) -> Self:
        """
        Extract the first non-null value found across a list of candidate paths.

        Args:
            type:        Handling of the case where no candidate path resolves.
            paths:       Ordered list of paths tried left-to-right.
            alias:       Output key; defaults to the first path's basename.
            transformer: Optional callable applied to the resolved value.

        Returns:
            *self* for chaining.
        """
        self._group_items.append((type, paths, alias, transformer))
        return self
    
    def select_all(self):
        """Pass each top-level JSON record through to the result set unchanged."""
        self._select_all = True
        return self
    
    def select_recursive(self, type : ValueType, path : URLBuilder, api_walker: Self, \
                         alias: str|None = None, transformer : Callable[[Any], Any]|None = None) -> Self:
        """
        Navigate to a nested list or dict at *path* and extract its contents with a child walker.

        Args:
            type:        Handling of a missing nested value.
            path:        JSON path to the nested structure.
            api_walker:  Child :class:`APIWalker` applied to each element of the
                         nested list (or to the dict itself).
            alias:       Output key; defaults to the path's basename.
            transformer: Optional callable applied to the assembled child result list.

        Returns:
            *self* for chaining.
        """
        self._recursive_items.append((type, path, api_walker, alias, transformer))
        return self
    
    def join(self, type : ValueType, path : URLBuilder, api_walker: Self, alias: str|None = None) -> Self:
        """
        Follow a value at *path* as a new API endpoint URL and merge the response inline.

        The value at *path* may be a string URL or a list of URLs; each is fetched
        via the child *api_walker* and the result stored under *alias*.

        Args:
            type:       Handling of a missing URL value.
            path:       Path to the URL value within the current record.
            api_walker: Child walker used to process the joined endpoint.
            alias:      Output key; defaults to the path's basename.

        Returns:
            *self* for chaining.
        """
        self._join_items.append((type, path, api_walker, alias))
        return self
    
    def filter(self, filter_func : Callable[[Dict[str, Any]], bool]) -> Self:
        """
        Add a predicate that discards records for which it returns ``False``.

        Args:
            filter_func: Callable receiving a single result-dict and returning
                         ``True`` to keep it or ``False`` to discard it.

        Returns:
            *self* for chaining.
        """
        self._filters.append(filter_func)
        return self
    
    def transform(self, transform_func : Callable[[List[Any]],Any]) -> Self:
        """
        Add a post-processing function applied to the entire result list after filtering.

        Args:
            transform_func: Callable receiving the full result list and returning
                            the transformed value (which replaces the list).

        Returns:
            *self* for chaining.
        """
        self._transformers.append(transform_func)
        return self

    def endpoint(self, url_builder : URLBuilder) -> Self:
        """
        Set the default API endpoint used when :meth:`execute` is called without a URL.

        Args:
            url_builder: :class:`URLBuilder` representing the data source.

        Returns:
            *self* for chaining.
        """
        self._url_builder = url_builder
        return self
    
    def binding(self, binding: str, transform_func: Callable[[str], str]|None = None) -> Self:
        """
        Declare a URL template variable that :meth:`execute` fills in from its *value* argument.

        Args:
            binding:        The placeholder name in the URL template (e.g. ``"id"``).
            transform_func: Optional callable applied to the bound value before
                            substitution (e.g. to strip a URI prefix).

        Returns:
            *self* for chaining.
        """
        self._binding = binding
        self._transform_func = transform_func
        return self   

    def execute(self, url_builder : URLBuilder|None = None, value : str|None = None, local_dataset : List[Dict[str, Any]]|None = None) -> Any:
        """
        Fetch the endpoint (or use *local_dataset*) and apply all registered extraction rules.

        The method resolves which data source to use in this priority order:
        1. *local_dataset* — caller-provided records (skips network entirely).
        2. *url_builder* — explicit URL overrides the stored one.
        3. The URL stored via :meth:`endpoint`.

        Args:
            url_builder:   Optional URL override for this call.
            value:         Concrete value bound to the template variable declared by
                           :meth:`binding` (injected into the URL before fetching).
            local_dataset: Pre-loaded record list; bypasses network and cache.

        Returns:
            A list of result-dict records after all rules, filters, and transforms
            have been applied, or ``None`` on a fatal configuration error.
        """
        if local_dataset is None:
            if url_builder is None:
                if self._url_builder is not None:
                    url_builder = self._url_builder
                else:
                    ProgressPrint.log("Not specified endpoint URL", "ERR!")
                    return None
            url_builder = url_builder.clone()
            if self._binding is not None and value is not None:
                if self._transform_func is not None:
                    value = self._transform_func(value)
                url_builder.bind(self._binding, value)
            dataset = cast(List[Dict[str, Any]]|None, self._loader.load(url_builder))
            if dataset is None:
                dataset = []
        else:
            dataset = local_dataset
        result_set : List[Dict[str, Any]]= []
        for dataset_part in dataset:
            result_dict : Dict[str, Any] = {}
            if self._select_all:
                result_set.append(dataset_part)
            else:
                # select()
                for item in self._items:
                    (type, paths, aliases, transformers) = item
                    for i, path in enumerate(paths):
                        alias = aliases[i] if aliases is not None and i < len(aliases) else path.basename() if isinstance(path, URLBuilder) else path
                        transformer = transformers[i] if transformers is not None and i < len(transformers) else None
                        path_array = path.relative_path().split("/") if isinstance(path, URLBuilder) else [path]
                        if alias in result_dict:
                            ProgressPrint.log(f"Name '{alias}' already set, maybe try alias", "ERR!")
                            return None
                        iter_dataset : JsonValue = cast(JsonValue, dataset_part)
                        for element in path_array:
                            if isinstance(iter_dataset, dict):
                                iter_dataset = iter_dataset.get(element)
                            elif isinstance(iter_dataset, list):
                                if element.isdigit():
                                    try:
                                        iter_dataset = iter_dataset[int(element)]
                                    except IndexError:
                                        ProgressPrint.log(f"Index {element} out of bound", "ERR!")
                                        iter_dataset = None
                                else:
                                    ProgressPrint.log(f"Cannot index list by string {element}", "ERR!")
                                    iter_dataset = None
                            else:
                                ProgressPrint.log("Too long path, no dict or list to iterate", "ERR!")
                                iter_dataset = None
                            if iter_dataset is None:
                                break
                        if iter_dataset is None and type == ValueType.REQUIRED and isinstance(path, URLBuilder):
                            ProgressPrint.log(f"Item '{alias}' is REQUIRED but was not found in path {path.relative_path()}", "ERR!")
                            return None
                        if iter_dataset is None and isinstance(path, str):
                            inter_result = path
                        else:
                            inter_result = iter_dataset
                        if transformer is None:
                            result_dict[alias] = inter_result
                        else:
                            result_dict[alias] = transformer(inter_result)
                result_set.append(result_dict)
                # select_one_of()
                for group_item in self._group_items:
                    (step_type, paths, alias, transformer) = group_item
                    found_value = None
                    found = False
                    for path in paths:
                        actual_alias = alias if alias is not None else path.basename()
                        path_array = path.relative_path().split("/")
                        iter_dataset: JsonValue = cast(JsonValue, dataset_part)
                        for element in path_array:
                            if isinstance(iter_dataset, dict):
                                iter_dataset = iter_dataset.get(element)
                            elif isinstance(iter_dataset, list) and element.isdigit():
                                try:
                                    iter_dataset = iter_dataset[int(element)]
                                except IndexError:
                                    iter_dataset = None
                            else:
                                iter_dataset = None

                            if iter_dataset is None:
                                break
                        if iter_dataset is not None:
                            found_value = iter_dataset
                            found = True
                            break
                    actual_alias = alias if alias is not None else paths[0].basename()
                    if not found and step_type == ValueType.REQUIRED:
                        ProgressPrint.log(f"Item '{actual_alias}' is REQUIRED but wasn't found in any of the paths", "ERR!")
                        return None
                    if found:
                        if transformer is None:
                            result_dict[actual_alias] = found_value
                        else:
                            result_dict[actual_alias] = transformer(found_value)
                # select_recursive()
                for recursive_item in self._recursive_items:
                    (type, path, api_walker, alias, transformer) = recursive_item
                    alias = alias if alias is not None else path.basename()
                    path_array = path.relative_path().split("/")
                    if alias in result_dict:
                        ProgressPrint.log(f"Name '{alias}' already set, maybe try alias", "ERR!")
                        return None
                    iter_dataset : JsonValue = cast(JsonValue, dataset_part)    
                    for element in path_array:
                        if isinstance(iter_dataset, dict):
                            iter_dataset = iter_dataset.get(element)
                        elif isinstance(iter_dataset, list):
                            if element.isdigit():
                                try:
                                    iter_dataset = iter_dataset[int(element)]
                                except IndexError:
                                    ProgressPrint.log(f"Index {element} out of bound", "ERR!")
                                    iter_dataset = None
                            else:
                                ProgressPrint.log(f"Cannot index list by string {element}", "ERR!")
                                iter_dataset = None
                        else:
                            ProgressPrint.log("Too long path, no dict or list to iterate", "ERR!")
                            iter_dataset = None
                        if iter_dataset is None:
                            break
                    if iter_dataset is None and type == ValueType.REQUIRED:
                        ProgressPrint.log(f"Item '{alias}' is REQUIRED but was not found in path {path.relative_path()}", "ERR!")
                        return None
                    result_list : List[Any] = []
                    if isinstance(iter_dataset, list):
                        for iter_item in iter_dataset:
                            local_dataset = cast(List[Dict[str, Any]], [iter_item])
                            res = api_walker.execute(None, None, local_dataset=local_dataset)
                            if res:
                                result_list.append(res[0])
                        if transformer is None:
                            result_dict[alias] = result_list
                        else:
                            result_dict[alias] = transformer(result_list)
                    elif isinstance(iter_dataset, dict):
                        res = api_walker.execute(None, None, local_dataset=[iter_dataset])
                        if res:
                            if transformer is None:
                                result_dict[alias] = res[0]
                            else:
                                result_dict[alias] = transformer(res[0])
                    else:
                        ProgressPrint.log("Cannot call recursive walk on dictionary")
                        return None
                # select_join()
                for join_item in self._join_items:
                    (type, path, api_walker, alias) = join_item
                    alias = alias if alias is not None else path.basename()
                    path_array = path.relative_path().split("/")
                    if alias in result_dict:
                        ProgressPrint.log(f"Name '{alias}' already set, maybe try alias", "ERR!")
                        return None
                    iter_dataset : JsonValue = cast(JsonValue, dataset_part)    
                    for element in path_array:
                        if isinstance(iter_dataset, dict):
                            iter_dataset = iter_dataset.get(element)
                        elif isinstance(iter_dataset, list):
                            if element.isdigit():
                                try:
                                    iter_dataset = iter_dataset[int(element)]
                                except IndexError:
                                    ProgressPrint.log(f"Index {element} out of bound", "ERR!")
                                    iter_dataset = None
                            else:
                                ProgressPrint.log(f"Cannot index list by string {element}", "ERR!")
                                iter_dataset = None
                        else:
                            ProgressPrint.log("Too long path, no dict or list to iterate", "ERR!")
                            iter_dataset = None
                        if iter_dataset is None:
                            break
                    if iter_dataset is None and type == ValueType.REQUIRED:
                        ProgressPrint.log(f"Item '{alias}' is REQUIRED but was not found in path {path.relative_path()}", "ERR!")
                        return None
                    result_list : List[Any] = []
                    if isinstance(iter_dataset, list):
                        for iter_item in iter_dataset:
                            result_list.append(api_walker.execute(None, str(iter_item)))
                        result_dict[alias] = result_list
                    elif isinstance(iter_dataset, str):
                        result_dict[alias] = api_walker.execute(None, iter_dataset)
                    else:
                        ProgressPrint.log("Cannot call recursive walk on dictionary")
                        return None
        for filter_func in self._filters:
            result_set = [item for item in result_set if filter_func(item)]
        for transform_func in self._transformers:
            result_set = transform_func(result_set)
        return result_set
