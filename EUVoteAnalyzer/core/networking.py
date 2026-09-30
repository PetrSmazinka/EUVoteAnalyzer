"""
HTTP and SPARQL networking utilities.

Provides:
  SparqlQuery    – wrapper around SPARQLWrapper with retry logic.
  URLBuilder     – fluent builder for constructing and serialising URLs.
  Loader         – abstract base for data-fetching strategies.
  URLLoader      – fetches JSON-LD from the EP Open Data API.
  EurostatLoader – downloads datasets via the ``eurostat`` package.
  TextLoader     – fetches raw HTML with optional server-side simplification.
  WikiLoader     – fetches Wikipedia pages and converts them to Markdown tables.
  SmartLoader    – cache-aware wrapper that persists responses through LocalCache.
  SmartSparqlQuery – cache-aware SPARQL wrapper.
"""

import eurostat # type: ignore
import os
import pandas as pd
import re
import requests
import time
from abc import ABC, abstractmethod
from bs4 import BeautifulSoup, Comment, Tag
from SPARQLWrapper import SPARQLWrapper, JSON
from typing import List, Dict, Any, Self, Tuple, Optional, cast
from urllib.parse import urlencode
from urllib.error import HTTPError

from EUVoteAnalyzer.core.common import DEBUG, ENDPOINTS, HTMLMode, HTMLExtractOutput, HTMLVerbosity
from EUVoteAnalyzer.core.utils import ProgressPrint, GeneratorManager

class SparqlQuery:
    """
    Thin wrapper around SPARQLWrapper that normalises raw JSON-LD bindings
    into a flat list of string-valued dictionaries.

    Args:
        endpoint: SPARQL endpoint URL. Defaults to the Wikidata query service.
    """

    def __init__(self, endpoint : str = ENDPOINTS["WIKI_sparql"]):
        self._endpoint = endpoint

    def query(self, query : str) -> List[Dict[str, str]]:
        """
        Execute *query* and return normalised bindings.

        Args:
            query: A complete SPARQL SELECT query string.

        Returns:
            List of row dicts with string values; empty list on parse failure.
        """
        sparql = SPARQLWrapper(self._endpoint)
        sparql.setReturnFormat(JSON)
        sparql.addCustomHttpHeader(
            "Accept",
            "application/sparql-results+json"
        )
        sparql.addCustomHttpHeader(
            "User-Agent",
            "EUVoteAnalyzer/2.0"
        )
        sparql.setQuery(query)
        raw_response: Any = sparql.query().convert()
        if not isinstance(raw_response, dict):
            return []
        response_dict = cast(Dict[str, Any], raw_response)
        results_node = response_dict.get("results")
        if not isinstance(results_node, dict):
            return []
        results_dict = cast(Dict[str, Any], results_node)
        bindings = results_dict.get("bindings")
        if not isinstance(bindings, list):
            return []
        result_list: List[Dict[str, str]] = []
        safe_bindings = cast(List[Dict[str, Dict[str, Any]]], bindings)
        for row in safe_bindings:
            processed_row: Dict[str, str] = {}
            for key, value_obj in row.items():
                val = value_obj.get("value")
                if val is not None:
                    processed_row[key] = str(val)
                else:
                    processed_row[key] = ""
            result_list.append(processed_row)
        return result_list
    
    def query_with_retry(self, query_str: str, retries: int=3, delay: int=2):
        """
        Execute *query_str* with exponential back-off on 429 and 5xx errors.

        Args:
            query_str: A complete SPARQL SELECT query string.
            retries:   Maximum number of attempts before giving up.
            delay:     Initial sleep interval in seconds; doubles on each retry.

        Returns:
            Query results on success, or ``None`` if all retries are exhausted.
        """
        for _ in range(retries):
            try:
                return self.query(query_str)
            except HTTPError as e:
                if e.code == 429:
                    wait = delay * 5
                    ProgressPrint.log(f"Rate limited (429), retry in {wait}s...", "WARN")
                    time.sleep(wait)
                    delay *= 2
                elif e.code in [500, 502, 503, 504]:
                    ProgressPrint.log(f"Server not responding (error {e.code}), retry in {delay}s...", "WARN")
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise e
        return None

class URLBuilder:
    """
    Fluent, immutable-style builder for REST API URLs.

    Separates path segments from query-string parameters and provides
    ``clone()`` so a single base builder can be reused safely across multiple
    requests without mutation.  ``endpoint()`` and ``relative_path()`` produce
    filesystem-safe strings used as LocalCache keys.
    """

    def __init__(self, endpoint: str = ""):
        self._endpoint = endpoint.rstrip("?").rstrip("/")
        self._query_params: Dict[str, str] = {}
        self._path_params: List[str] = []

    def path_param(self, value: str) -> Self:
        """Append *value* as the next path segment."""
        self._path_params.append(value)
        return self

    def query_param(self, key: str, value: Any) -> Self:
        """Add or overwrite a query-string parameter; ``None`` values are silently ignored."""
        if value is not None:
            self._query_params[key] = str(value)
        return self

    def reset_params(self) -> Self:
        self._query_params = {}
        self._path_params = []
        return self
    
    def move_up(self) -> Self:
        self._path_params.pop()
        return self

    def bind(self, key: str, value: str) -> Self:
        """Replace the ``:key`` placeholder in the path with *value*."""
        placeholder = f":{key}"
        try:
            index = self._path_params.index(placeholder)
            self._path_params[index] = value
        except ValueError:
            ProgressPrint.log(f"Parameter {key} (placeholder {placeholder}) is not in path parameters", "ERR!")
        
        return self

    def build(self) -> str:
        """Return the fully-formed URL string including path and query string."""
        url = self._endpoint
        if self._path_params:
            path_string = "/".join(str(p).strip("/") for p in self._path_params)
            url = f"{url}/{path_string}"
        if self._query_params:
            query_string = urlencode(self._query_params)
            url = f"{url}?{query_string}"
        return url
    
    def endpoint(self) -> str:
        """
        Return a filesystem-safe string derived from the full URL.

        Used as a LocalCache key: the scheme is stripped and characters that
        are illegal in file paths are replaced with underscores.
        """
        url = self._endpoint
        url = re.sub(r'^https?://', '', url)
        if self._path_params:
            path_string = "/".join(str(p).strip("/") for p in self._path_params)
            url = f"{url}/{path_string}"
        if self._query_params:
            query_string = urlencode(self._query_params)
            url = f"{url}--{query_string}"
        return re.sub(r'[<>:"\\|?*]', '_', url)
    
    def relative_path(self) -> str:
        url = ""
        if self._path_params:
            path_string = "/".join(str(p).strip("/") for p in self._path_params)
            url = f"{path_string}"
        return url
    
    def basename(self) -> str:
        url = self.build()
        return os.path.basename(url)

    def join(self, url_builder : Self) -> Self:
        query_params = url_builder.get_query_params()
        path_params = url_builder.get_path_params()
        for query_param in query_params:
            self.query_param(query_param[0], query_param[1])
        for path_param in path_params:
            self.path_param(path_param)
        return self
    
    def get_query_params(self) -> Dict[str, str]:
        return self._query_params
    
    def get_path_params(self) -> List[str]:
        return self._path_params

    def __str__(self) -> str:
        return self.build()
    
    def clone(self) -> Self:
        new_builder = self.__class__(self._endpoint)
        new_builder._query_params = self._query_params.copy()
        new_builder._path_params = self._path_params.copy()
        return new_builder

class Loader(ABC):
    """Abstract resource-fetching strategy consumed by SmartLoader."""

    @abstractmethod
    def load(self, resource: str) -> List[Dict[str, Any]]|pd.DataFrame|str|None:
        """
        Retrieve *resource* and return its content in the appropriate type.

        Args:
            resource: URL or dataset identifier to fetch.

        Returns:
            Parsed content, or ``None`` on unrecoverable error.
        """
        pass


class URLLoader(Loader):
    """Fetches JSON-LD data from the EP Open Data REST API."""

    def load(self, resource : str) -> List[Dict[str, Any]]|None:
        headers = {
            "Accept": "application/ld+json",
            "User-Agent": "EUVoteAnalyzer-dev-1.0"
        }
        response = requests.get(resource, headers=headers)
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                ProgressPrint.log("Data in unexpected format (not JSON)", "ERR!")
                return None
            content = data.get("data", [])
            if content:
                return cast(List[Dict[str, Any]]|None, content)
            else:
                ProgressPrint.log("Data in unexpected format", "ERR!")
                return None
        elif response.status_code == 204:
            ProgressPrint.log(f"Empty response for URL {resource}", "WARN")
            return []
        else:
            ProgressPrint.log(f"Network error ({response.status_code})", "ERR!")
            return None
        
class EurostatLoader(Loader):
    """Downloads a dataset from Eurostat via the official ``eurostat`` package."""

    def load(self, resource : str) -> pd.DataFrame|None:
        dataset_code = resource.split("/")[-1]
        try:
            result: Optional[pd.DataFrame] = eurostat.get_data_df(dataset_code) # type: ignore
        except:
            return None
        if result is None:
            return pd.DataFrame()
        return result

class TextLoader(Loader):
    """
    Fetches raw HTML pages with optional server-side simplification.

    The ``simplify`` property activates BeautifulSoup-based HTML reduction
    before the content is returned.  The level of reduction is controlled
    by ``html_mode`` (OVERVIEW / NORMAL / DETAILED) and ``is_in_navbox``.
    """

    def __init__(self) -> None:
        self._simplify = False
        self._is_in_navbox = False
        self._html_mode = HTMLMode.OVERVIEW

    @property
    def simplify(self) -> bool:
        return self._simplify
    
    @simplify.setter
    def simplify(self, value : bool) -> None:
        self._simplify = value

    @property
    def is_in_navbox(self) -> bool:
        return self._is_in_navbox
    
    @is_in_navbox.setter
    def is_in_navbox(self, value : bool) -> None:
        self._is_in_navbox = value

    @property
    def html_mode(self) -> HTMLMode:
        return self._html_mode
    
    @html_mode.setter
    def html_mode(self, value : HTMLMode) -> None:
        self._html_mode = value

    def load(self, resource : str) -> str|None:
        headers = {
            "User-Agent": "EUVoteAnalyzer/1.0"
        }
        response = requests.get(resource, headers=headers)
        if response.status_code == 200:
            if self.simplify:
                return self.simplification(response.text)
            return response.text
        elif response.status_code == 204:
            ProgressPrint.log(f"Empty response for URL {resource}", "WARN")
            return ""
        else:
            ProgressPrint.log(f"Network error ({response.status_code})", "ERR!")
            return None

    def simplification(self, text : str) -> str:
        """
        Strip non-essential HTML elements from *text* according to the current mode.

        OVERVIEW: removes all media, scripts, and element attributes except anchors.
        NORMAL: additionally unwraps inline span and div elements.
        DETAILED: retains inline styles and class attributes on spans.
        """
        if not self.simplify:
            return text
        soup = BeautifulSoup(text, 'html.parser')
        if self.is_in_navbox:
            content = soup.find(class_="navbox") or soup.find("main") or soup
        else:
            content = soup.find("main") or soup

        comments = soup.find_all(string=lambda text: isinstance(text, Comment))
        for comment in comments:
            comment.extract()
        if self.html_mode == HTMLMode.OVERVIEW:
            unwanted_tags = [
                "script", "style", "img", "svg", "form", "noscript", 
                "header", "footer", "aside", "meta", "link"
            ]
            for tag in content.find_all(unwanted_tags):
                tag.decompose()
            ui_classes = [
                "mw-editsection", "noprint", "mw-empty-elt", "catlinks", 
                "printfooter", "mw-jump-link", "mw-indicators"
            ]
            for extra in content.find_all(class_=ui_classes):
                extra.decompose()
            for tag in content.find_all(True):
                if tag.name == 'a':
                    attrs = {k: v for k, v in tag.attrs.items() if k in ['href', 'title']}
                    tag.attrs = attrs
                else:
                    tag.attrs = {}
        elif self.html_mode == HTMLMode.NORMAL:
            unwanted_tags = [
                "script", "style", "img", "svg", "form", "noscript",
                "header", "footer", "aside", "meta"
            ]
            for tag in content.find_all(unwanted_tags):
                tag.decompose()

            ui_classes = [
                "mw-editsection", "noprint", "mw-empty-elt", "catlinks", 
                "printfooter", "mw-jump-link", "mw-indicators"
            ]
            for extra in content.find_all(class_=ui_classes):
                extra.decompose()
            for tag in content.find_all(True):
                if tag.name == 'a':
                    attrs = {k: v for k, v in tag.attrs.items() if k in ['href', 'title']}
                    tag.attrs = attrs
                else:
                    tag.attrs = {}
                if tag.name in ['span', 'div']:
                    tag.unwrap()
        elif self.html_mode == HTMLMode.DETAILED:
            unwanted_tags = [
                "script", "style", "img", "svg", "form", "noscript",
                "header", "footer", "aside", "meta"
            ]
            for tag in content.find_all(unwanted_tags):
                tag.decompose()

            ui_classes = [
                "mw-editsection", "noprint", "mw-empty-elt", "catlinks", 
                "printfooter", "mw-jump-link", "mw-indicators"
            ]
            for extra in content.find_all(class_=ui_classes):
                extra.decompose()
            for tag in content.find_all(True):
                if tag.name == 'a':
                    attrs = {k: v for k, v in tag.attrs.items() if k in ['href', 'title']}
                    tag.attrs = attrs
                elif tag.name == 'span':
                    attrs = {k: v for k, v in tag.attrs.items() if k in ['class', 'style']}
                    tag.attrs = attrs
                else:
                    tag.attrs = {}
                if tag.name in ['span', 'div']:
                    tag.unwrap()
        cleaned_html = content.decode_contents()
        return re.sub(r'\s+', ' ', cleaned_html).strip()

class WikiLoader(Loader):
    """
    Downloads a Wikipedia article and converts it to either cleaned HTML or
    a structured Markdown representation with tables preserved.

    The Markdown conversion reconstructs HTML tables as pipe-separated Markdown,
    handles rowspan / colspan by expanding cells into a 2-D grid, and extracts
    nested sub-tables as named references (``[[Table_subtable]]``).

    Args:
        mode:       Target output format — HTML or MARKDOWN.
        strictness: Controls which page sections are retained before conversion.
    """

    def __init__(self, mode : HTMLExtractOutput = HTMLExtractOutput.HTML, strictness : HTMLVerbosity = HTMLVerbosity.ALL):
        self.mode = mode
        self.strictness = strictness

    def load(self, resource : str) -> str|None:
        headers = {
            "User-Agent": "EUVoteAnalyzer/1.0"
        }
        response = requests.get(resource, headers=headers)
        if response.status_code == 200:
            return self.simplification(response.text)
        elif response.status_code == 204:
            ProgressPrint.log(f"Empty response for URL {resource}", "WARN")
            return ""
        else:
            ProgressPrint.log(f"Network error ({response.status_code})", "ERR!")
            return None
        
    def _replace_a(self, a : Tag) -> str:
        """Convert an anchor element to ``[text](href)`` Markdown notation."""
        href = a.get("href", "")
        text = a.get_text(strip=True)
        return f"[{text}]({href})" if text and text.strip() != "" else ""
        
    def _html2markdown(self, content : BeautifulSoup|Tag, indentation : int = 0):
        """Recursively traverse *content* and emit Markdown lines for paragraphs, headings, and tables."""
        lines: List[str] = []
        for child in content.children:
            if isinstance(child, Tag):
                if child.name == "p":
                    for a in child.find_all("a"):
                        a.replace_with(self._replace_a(a))
                    text = child.get_text()
                    lines.append(f"{text}\n")
                elif child.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
                    lines.append(f"{'#'*int(child.name[1:])} {child.get_text(strip=True)}\n")
                elif child.name == "table":
                    lines.append(self._table_to_smart_format(child))
                else:
                    # Recurse into container elements (div/section/etc.) -- current
                    # Wikipedia markup nests paragraphs/tables inside wrapper divs
                    # rather than making them direct children of <main>, so without
                    # this the walk finds nothing below the first wrapper level.
                    nested = self._html2markdown(child, indentation)
                    if nested:
                        lines.append(nested)
        return "\n".join(lines)
    
    def _table_to_smart_format(self, table: BeautifulSoup|Tag) -> str:
        caption = table.find('caption')
        caption_content = caption.get_text(strip=True) if caption else None
        caption_text = caption_content if caption_content and caption_content != "" else GeneratorManager.get("Table")
        return self._process_table(table, caption_text)
        
    @staticmethod
    def _safe_span(value: Any) -> int:
        """Parse a colspan/rowspan attribute defensively; malformed real-world HTML
        (e.g. two attributes merged without a separator) can yield non-numeric
        values -- fall back to 1 (no span) rather than raising."""
        match = re.match(r"\d+", str(value or "1"))
        return int(match.group(0)) if match else 1

    def _process_table(self, table: BeautifulSoup|Tag, tbl_name: str) -> str:
        """
        Convert *table* to a pipe-delimited Markdown table.

        Expands rowspan/colspan attributes by filling a 2-D grid before
        serialising, so every visual cell appears in the correct column.
        Nested ``<table>`` elements are extracted as named references.
        """
        subtables : List[str] = []
       
        tbody = table.find('tbody', recursive=False)
        if tbody is not None:
            rows = tbody.find_all('tr', recursive=False)
        else:
            rows = table.find_all('tr', recursive=False)
        if not rows:
            return ""
        max_rows = len(rows)
        max_cols = 0
        for row in rows:
            current_row_cols = 0
            for cell in row.find_all(['td', 'th'], recursive=False):
                current_row_cols += self._safe_span(cell.get('colspan', "1"))
            max_cols = max(max_cols, current_row_cols)
        table_grid: List[List[str|None]] = [[None for _ in range(max_cols)] for _ in range(max_rows)]
        for r_idx, row in enumerate(rows):
            c_idx = 0
            for cell in row.find_all(['td', 'th'], recursive=False):
                while c_idx < max_cols and table_grid[r_idx][c_idx] != None:
                    c_idx += 1
                if c_idx >= max_cols:
                    break
                value, sub_subtables = self.cell_value(cell, tbl_name)
                subtables.extend(sub_subtables)
                r_span = self._safe_span(cell.get('rowspan', "1"))
                c_span = self._safe_span(cell.get('colspan', "1"))
                for r_offset in range(r_span):
                    for c_offset in range(c_span):
                        target_r = r_idx + r_offset
                        target_c = c_idx + c_offset
                        if target_r < max_rows and target_c < max_cols:
                            table_grid[target_r][target_c] = value if (r_offset == 0 and c_offset == 0) else ""
                c_idx += c_span
        for r_idx, row in enumerate(table_grid):
            for c_idx, col in enumerate(row):
                if col is None:
                    table_grid[r_idx][c_idx] = ""
        final_table = cast(List[List[str]], table_grid)
        heading : List[str] = final_table[0]
        rows_values : List[List[str]] = final_table[1:]
        filtered_rows = [row for row in rows_values if any(cell.strip() for cell in row)]
        if len(filtered_rows) == 0:
            result = [f"**{tbl_name}**\n- {'\n- '.join([h.strip() for h in heading])}\n\n"]
        else:
            result = [f"""**{tbl_name}**:
        
| {' | '.join([h.strip() for h in heading])} |
| {' | '.join(['---']*len(heading))} |
| {' |\n| '.join([' | '.join([c.strip() for c in row]) for row in filtered_rows])}|

"""]
        result.extend(subtables)
        return "\n\n".join(result)
     
    def cell_value(self, cell : BeautifulSoup|Tag, tbl_name : str) -> Tuple[str, List[str]]:
        """
        Extract the text content of a single table cell.

        Returns a ``(text, sub_tables)`` tuple where *sub_tables* contains
        any nested table strings discovered inside the cell.  Pipe characters
        in cell text are escaped so they do not break the Markdown table syntax.
        """
        sub_tables : List[str] = []
        tag_content : List[str] = []

        for child in cell.children:
            if isinstance(child, Tag):
                if child.name == "table":
                    ref_name = GeneratorManager.get(f"{tbl_name}_subtable")
                    sub_tables.append(self._process_table(child, ref_name))
                    tag_content.append(f"[[{ref_name}]]")
                elif child.name == "a":
                    link_md = self._replace_a(child)
                    if link_md.strip():
                        tag_content.append(link_md.strip())
                else:
                    text = child.get_text(separator=" ", strip=True)
                    if text:
                        tag_content.append(text)
            else:
                text_node = str(child).replace("\n", " ").strip()
                if text_node:
                    tag_content.append(text_node)
        final_parts = [p for p in tag_content if p and p.strip()]
        result_text = " ".join(final_parts).replace("|", "\\|")
        return result_text if result_text else " ", sub_tables
    
    def simplification(self, text : str) -> str:
        """
        Reduce a full Wikipedia HTML page to its essential content.

        Removes UI chrome (toolbars, edit links, navboxes, infoboxes based on
        *strictness*), strips all element attributes except anchors and
        background-colour styles, then either returns cleaned HTML or converts
        the result to Markdown via ``_html2markdown``.
        """
        soup = BeautifulSoup(text, 'html.parser')
        content = soup.find("main") or soup
        unwanted_classes = ["mw-editsection", "mw-references-wrap", "vector-page-toolbar",
                            "vector-column-end", "vector-body-before-content", "printfooter",
                            "sidebar", "vcard", "vector-toc-landmark"]
        unwanted_ids = ["References", "contentSub", "catlinks", "p-lang-btn"]
        unwanted_selectors = [".navbar.plainlinks", 'div[role="note"]', 'div:has(> span[typeof="mw:File"])']
        unwanted_tags = ["meta", "script", "style", "sup", "noscript", "img", "br", "hr"]
        flatten_tags = ["b", "ul", "header"]
        special_flatten_tags = ["div", "span", "p"]
        flatten_selectors = ['#bodyContent', '#mw-content-text']
        force_flatten_tags = ["li", "abbr","link"]

        if self.strictness == HTMLVerbosity.SIDEBAR or self.strictness == HTMLVerbosity.PAGE:
            unwanted_selectors.append('.navbox[role="navigation"]')
            if self.strictness == HTMLVerbosity.PAGE:
                unwanted_classes.append('infobox')

        # 1. Remove specified classes
        for tag in soup.find_all(class_=unwanted_classes):
            tag.decompose()

        # 2. Remove specified IDs
        for tag in soup.find_all(id=unwanted_ids):
            tag.decompose()

        # 3. Remove by complex selectors
        for selector in unwanted_selectors:
            for tag in soup.select(selector):
                tag.decompose()

        # 4. Remove unnecesarry attributes
        for tag in content.find_all(True):
            if tag.name == 'a':
                tag.attrs = {k: v for k, v in tag.attrs.items() if k in ['href', 'title']}
            elif tag.name in ['span', 'div']:
                new_attrs = {}
                if 'id' in tag.attrs:
                    new_attrs['id'] = tag.attrs['id']
                raw_style = tag.attrs.get('style', '')
                if isinstance(raw_style, str) and 'background-color' in raw_style:
                    parts = [p.strip() for p in raw_style.split(';') if 'background-color' in p]
                    if parts:
                        new_attrs['style'] = f"{parts[0]};"
                tag.attrs = new_attrs
            elif tag.name in ['th', 'td'] and self.mode == HTMLExtractOutput.MARKDOWN:
                tag.attrs = {k: v for k, v in tag.attrs.items() if k in ['colspan', 'rowspan']}
            else:
                tag.attrs = {k: v for k, v in tag.attrs.items() if k in ['id']}

        # 5. Remove unwanted tags
        for tag in content.find_all(unwanted_tags):
                tag.decompose()

        # 6. Remove comments
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

        # 7. Flatten tags
        for tag in content.find_all(flatten_tags):
            if not tag.attrs:
                tag.unwrap()

        # 7. Flatten special tags
        for tag in content.find_all(special_flatten_tags):
            if not tag.attrs:
                direct_texts = tag.find_all(string=True, recursive=False)
                has_real_text = any(t.strip() for t in direct_texts)
                if not has_real_text:
                    tag.unwrap()

        # 8. Force flatten tags
        for tag in content.find_all(force_flatten_tags):
            direct_texts = tag.find_all(string=True, recursive=False)
            has_real_text = any(t.strip() for t in direct_texts)
            if not has_real_text:
                if tag.parent:
                    tag.unwrap()

        # 9. Flatten by complex selectors
        for selector in flatten_selectors:
            for tag in content.select(selector):
                direct_texts = tag.find_all(string=True, recursive=False)
                has_real_text = any(t.strip() for t in direct_texts)
                if not has_real_text:
                    if tag.parent:
                        tag.unwrap()

        html_content = content.decode_contents()
        clean_html = html_content.replace('\n', '').replace('\r', '')
        clean_html = re.sub(r'>\s+<', '><', clean_html).strip()

        if DEBUG:
            with open("_wiki.html", "w", encoding="utf-8") as f:
                f.write(clean_html)

        content = self._html2markdown(content)

        if DEBUG:
            with open("_wiki.md", "w", encoding="utf-8") as f:
                f.write(content)

        return clean_html if self.mode == HTMLExtractOutput.HTML else content
