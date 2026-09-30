"""
Project-wide constants, type aliases, enumerations, and static data.

Everything defined here is imported by multiple modules across the package.
Keeping all configuration in one place makes it easy to adjust thresholds,
add new Eurostat indicators, or extend the LLM prompt library without
touching business logic.
"""

from datetime import datetime
from enum import Enum
from typing import Dict, List, Any, Union, Callable

# ── Type aliases ─────────────────────────────────────────────────────────────
# Recursive types used throughout the codebase.

JsonSchema = Union[
    Dict[str, "JsonSchema"],
    List["JsonSchema"],
    str,
    int,
    bool
]

JsonValue = Union[
    Dict[str, "JsonValue"], 
    List["JsonValue"], 
    str, int, float, bool, None
]

APIValue = Union[
    Dict[str, "APIValue"],
    List["APIValue"],
    str
]

EntityList = List[Dict[str, Any]]


# ── Enumerations ──────────────────────────────────────────────────────────────

class ValueType(Enum):
    """Indicates whether an API field is mandatory or may be absent."""
    REQUIRED = 1
    OPTIONAL = 2


class JsonSchemaType(Enum):
    """Primitive JSON types used in schema validation helpers."""
    INTEGER = 1
    STRING = 2


class CacheFileType(Enum):
    """Determines how a cached artefact is serialised to and from disk."""
    JSON = 1
    DATAFRAME = 2
    HTML = 3


class HTMLMode(Enum):
    """Controls how aggressively HTML is stripped before being fed to an LLM."""
    OVERVIEW = 1   # maximum reduction — navigation, images, and attributes removed
    NORMAL = 2     # moderate reduction — inline spans and empty divs unwrapped
    DETAILED = 3   # light reduction — only media and script tags removed


class HTMLExtractOutput(Enum):
    """Target format produced by WikiLoader.simplification."""
    HTML = 1
    MARKDOWN = 2


class HTMLVerbosity(Enum):
    """Controls which sections of a Wikipedia page are retained."""
    ALL = 1       # keep everything including navboxes and infoboxes
    SIDEBAR = 2   # strip navboxes
    PAGE = 3      # strip navboxes and infoboxes
# ── Runtime configuration ─────────────────────────────────────────────────────

CACHE_VALIDITY_DAYS = 100  # cached files older than this are re-fetched

DEBUG : bool = False  # when True, every fetcher exits after its first item

LANGUAGE : str = "en"  # ISO 639-1 code sent to EP API and Wikipedia requests

PARTIAL_LIMIT : int = 1  # page size for paginated EP API calls (increase to speed up fetching)

MAX_LLM_ATTEMPTS : int = 5  # retries for an LLM call whose response fails schema validation (see GeminiStrategy.generate)

# Remote resource base URLs — all fetchers build their requests from these.
ENDPOINTS : Dict[str, str] = {
    "MEPS" : "https://data.europarl.europa.eu/api/v2/meps",
    "EVENTS" : "https://data.europarl.europa.eu/api/v2/events",
    "VOTES" : "https://data.europarl.europa.eu/api/v2/meetings",
    "MEETINGS" : "https://data.europarl.europa.eu/api/v2/meetings",
    "CORPORATE_BODIES" : "https://data.europarl.europa.eu/api/v2/corporate-bodies",
    "WIKI" : "https://en.wikipedia.org/wiki",
    "EP_sparql" : "https://data.europa.eu/sparql",
    "WIKI_sparql": "https://query.wikidata.org/sparql",
    "HOW_THEY_VOTE" : "https://github.com/HowTheyVote/data/releases/latest/download"
}

# ── Eurostat dataset descriptors ─────────────────────────────────────────────
# Each entry maps a logical indicator name to an Eurostat dataset code, the
# column filters needed to extract the relevant time series, and a prediction
# method used to fill missing years (lin, linlog, or mean).
EUROSTAT : Dict[str, Dict[str, Dict[str, str]|str]] = {
    "HDP" : {
        "dataset" : "nama_10_pc",
        "filter" : {
            "unit" : "CP_PPS_EU27_2020_HAB",
            "na_item" : "B1GQ"
        },
        "prediction" : "linlog"
    },
    "DEBT" : {
        "dataset" : "teina225",
        "filter" : {
            "unit" : "PC_GDP",
            "sector" : "S13",
            "na_item" : "GD"
        },
        "prediction" : "lin"
    },
    "INFLATION": {
        "dataset": "prc_hicp_aind",
        "filter": {
            "unit" : "RCH_A_AVG",
            "coicop" : "CP00"
        },
        "prediction" : "mean"
    },
    "UNEMPLOYMENT" : {
        "dataset" : "une_rt_a",
        "filter" : {
            "unit" : "PC_ACT",
            "age" : "Y15-74",
            "sex" : "T"
        },
        "prediction" : "lin"
    },
    "POVERTY" : {
        "dataset" : "ilc_peps01n",
        "filter" : {
            "unit" : "PC", 
            "age" : "TOTAL", 
            "sex" : "T"
        },
        "prediction" : "lin"
    },
    "MIGRATION" : {
        "dataset" : "migr_asyappctza",
        "filter" : {
            "citizen" : "TOTAL", 
            "applicant" : "FRST", 
            "sex" : "T", 
            "unit" : "PER", 
            "age" : "TOTAL"
        },
        "prediction" : "linlog"
    },
    "EMISSIONS" : {
        "dataset" : "env_air_gge",
        "filter" : {
            "unit" : "MIO_T", 
            "airpol" : "GHG", 
            "src_crf" : "TOTXMEMO"
        },
        "prediction" : "linlog"
    },
    "GREEN_ENERGY" : {
        "dataset" : "nrg_ind_ren",
        "filter" : {
            "nrg_bal" : "REN", 
            "unit" : "PC"
        },
        "prediction" : "lin"
    },
    "ENERGETIC_MIX" : {
        "dataset" : "nrg_ind_fecf",
        "filter" : {
            "nrg_bal" : "FC_E", 
            "siec" : "X9900", 
            "unit" : "PC"
        },
        "prediction" : "lin"
    }
}

# ── EU member-state reference data ───────────────────────────────────────────
# Eurostat alpha-2 codes; note that Greece is 'EL', not 'GR'.
EU_COUNTRIES = ['AT', 'BE', 'BG', 'CY', 'CZ', 'DE', 'DK', 'EE', 'EL', 'ES', 'FI',
                'FR', 'HR', 'HU', 'IE', 'IT', 'LT', 'LU', 'LV', 'MT', 'NL', 'PL',
                'PT', 'RO', 'SE', 'SI', 'SK']

EU_COUNTRIES_NAMES = ['Austria', 'Belgium', 'Bulgaria', 'Cyprus', 'Czechia', 'Germany', 'Denmark', 'Estonia',
                      'Greece', 'Spain', 'Finland', 'France', 'Croatia', 'Hungary', 'Ireland', 'Italy', 'Lithuania',
                      'Luxembourg', 'Latvia', 'Malta', 'Netherlands', 'Poland', 'Portugal', 'Romania', 'Sweden',
                      'Slovenia', 'Slovakia']

EU_COUNTRIES_WIKI_IDS = {
  "AT": "Q40",
  "BE": "Q31",
  "BG": "Q219",
  "CY": "Q229",
  "CZ": "Q213",
  "DE": "Q183",
  "DK": "Q35",
  "EE": "Q191",
  "EL": "Q41",
  "ES": "Q29",
  "FI": "Q33",
  "FR": "Q142",
  "HR": "Q224",
  "HU": "Q28",
  "IE": "Q27",
  "IT": "Q38",
  "LT": "Q37",
  "LU": "Q32",
  "LV": "Q211",
  "MT": "Q233",
  "NL": "Q55",
  "PL": "Q36",
  "PT": "Q45",
  "RO": "Q218",
  "SE": "Q34",
  "SI": "Q215",
  "SK": "Q214"
}

INDENTATION : int = 4  # spaces used when generating CREATE TABLE SQL

# ── Vote-option display labels ────────────────────────────────────────────────
# Stored alongside the symbol (+/-/0) and displayed in the frontend.
VOTE_OPTIONS: List[Dict[str, str]] = [
    {"text": "+", "text_muz": "Hlasoval pro",   "text_zena": "Hlasovala pro",   "text_univerzalni": "Hlasoval(a) pro"},
    {"text": "-", "text_muz": "Hlasoval proti", "text_zena": "Hlasovala proti", "text_univerzalni": "Hlasoval(a) proti"},
    {"text": "0", "text_muz": "Zdržel se",       "text_zena": "Zdržela se",       "text_univerzalni": "Zdržel(a) se"},
]

# ── LLM prompt templates ──────────────────────────────────────────────────────
# Placeholders ([HTML_CODE], [PROCEDURE_TYPE], etc.) are replaced at call-site
# before the string is sent to the language model.
STORED_QUERIES : Dict[str, str] = {
    "DISCOVER_NATIONAL_PARLIAMENTS" : """You are an expert in data extraction from HTML. Your task is to process the provided HTML code and create a valid JSON object.

### Input Parameters:
1. List of countries (EU_COUNTRIES): """ + ",".join(EU_COUNTRIES_NAMES) +"""
2. HTML Code: [HTML_CODE]

### Task:
- Find all anchor tags (<a>) in the HTML where the link text matches a country name from the EU_COUNTRIES list.
- Extract the 'href' attribute for each matching country.
- Clean the URL: Extract ONLY the part of the URL that follows "/wiki/". 
  Example: If the link is "/wiki/Elections_in_Austria", the value must be "Elections_in_Austria".

### Output Requirements:
- The output must be ONLY a valid JSON object.
- Format: {"Country Name": "Slug"}
- Do not include any explanatory text, introductory sentences, or Markdown formatting blocks (no backticks).
- If a country from the list is not found in the HTML, do not include it in the JSON.

""",
    "EXTRACT_PARLIAMENTARY_TERMS" : """You are an expert in data extraction from HTML. Your task is to process the provided HTML code representing a country's election history and create a valid JSON object.

### Task:
1. Locate the section or table rows specifically related to "Parliamentary elections", "Legislative elections", or the national lower house (e.g., "National Council", "Chamber of Deputies").
2. Extract election year links for the modern national parliament
3. Ignore "Presidential elections", "European elections", and "Referendums".
4. Extract all valid election year links.
5. Clean the URL: Extract ONLY the part that follows "/wiki/".
6. Exclusion: Do not include links that point to non-existent pages (redlinks containing "action=edit").
7. Do not include elections from historical predecessor states or empires (e.g., skip "Cisleithania", "Imperial Council", "Kingdom of...", "USSR era"). Focus only on the modern Republic/State.

### Output Requirements:
- The output must be ONLY a valid JSON object.
- Format: {"Year": "Slug"}
- Do not include any explanatory text, introductory sentences, or Markdown formatting blocks (no backticks).
- Order the JSON chronologically by year.

### Input HTML:
[HTML_CODE]

""",
    "EXTRACT_PARTY_RESULTS": """You are an expert in political data extraction. Your task is to analyze the provided HTML of a specific election result page and extract a list of political parties.

### Task:
1. Identify all political parties that won at least one seat (chair) in the national parliament (typically found in the "Results" table).
2. Extract the party name and the exact number of seats (chairs) won.
   - The "name" must be exactly the visible/display text used for that party in the
     Results table (e.g. in a Markdown link "[N-VA](https://en.wikipedia.org/wiki/New_Flemish_Alliance)",
     the name is "N-VA" -- the text before the parenthesis, not anything derived from the
     URL inside the parenthesis, even if the URL looks like a fuller or more complete name).
3. Extract the URL for the party's specific Wikipedia page:
   - Extract ONLY the part of the URL that follows "/wiki/". 
   - Example: If the link is "/wiki/Social_Democratic_Party_of_Austria", the value must be "Social_Democratic_Party_of_Austria".
4. Determine "inGovernment" status:
   - Mark as true if the text indicates the party formed the government, was part of the "governing coalition", "grand coalition", or if their leader was "Elected Chancellor/Prime Minister".
   - Check the introductory text and the "Results" section for mentions of cabinet formation.
   - If the government formation is not yet decided use null.
   - If the information for government is not available for the party, but is available for other parties, use false.

### Output Requirements:
- The output must be a valid JSON array of objects.
- Each object must follow this structure: {"name": str, "chairs": int, "inGovernment": bool|null, "url": str}
- Do not include explanatory text or markdown backticks.

### Input HTML:
[HTML_CODE]

""",
"EXTRACT_PARTY_METADATA": """You are an expert in political data extraction and web scraping. Your task is to analyze the provided HTML/Markdown of a political party's own Wikipedia page (not a results table) and extract identity and branding information about ONLY the party this specific page is about — ignore any other parties merely mentioned in the text (rivals, coalition partners, election context, etc.).

### Task:
1. **Identify the subject party:** The page's title/infobox subject — not any other party mentioned in passing.
2. **Extract Full Name:** Get the official full name of that party.
3. **Extract Abbreviation:** Extract its short-form name or acronym (e.g., "SPD", "CDU", "ANC"). If no abbreviation is explicitly listed, use the full name.
4. **Extract Party Color:** Look for a colored box, a `background-color` style attribute, or a "color" field in the infobox.
   - Return the value as a Hex code (e.g., "#FF0000") if available.
   - If the color is defined by a CSS class name (e.g., "party-socialist"), return that class name string instead.
   - If no color information is found, use an empty string.

### Output Requirements:
- The output must be a single valid JSON object (not an array) describing only the subject party.
- Structure: {"name": str, "abbreviation": str, "color": str}
- Do not include explanatory text or markdown backticks.

### Input:
[HTML_CODE]
""",
"THEMATIC_LABEL":  """\
You are classifying European Parliament legislative votes into thematic categories.

Procedure type code: [PROCEDURE_TYPE]
Procedure title: [PROCEDURE_TITLE]

Based on the procedure type and title above, select the single most appropriate category \
from the list below and reply with only the 4-letter key:

ECON – Economy, taxes and financial markets
IMCO – Internal market and consumer protection
TECH – Industry, digital agenda and AI
ENVI – Environment and climate
ENER – Energy
AGRI – Agriculture, fisheries and food
TRAN – Transport and tourism
EMPL – Employment and social affairs
SANT – Public health
CULT – Education, culture and youth
LIBE – Civil liberties, justice and migration
AFET – Foreign affairs and human rights
SEDE – Security and defence
INTA – International trade
INST – Institutional affairs and budget

Reply with exactly one 4-letter key (e.g. ECON). No other text.\
"""
}

# ── Structured-output schemas (Gemini native JSON mode) ──────────────────────
LLM_RESPONSE_SCHEMAS : Dict[str, Any] = {
    "PARTY_RESULTS_SCHEMA" : {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING"},
                "chairs": {"type": "INTEGER"},
                "inGovernment": {"type": "BOOLEAN"},
                "url": {"type": "STRING"}
            },
            "required": ["name", "chairs", "inGovernment", "url"]
        }
    }
}

# ── Thematic category registry ────────────────────────────────────────────────
# Maps the 4-letter code produced by the THEMATIC_LABEL LLM prompt to a
# human-readable label shown in the frontend.
THEMATIC_LABELS = {
    "ECON": "Economy, Taxes, and Financial Markets",
    "IMCO": "Internal Market and Consumer Protection",
    "TECH": "Industry, Digital Agenda, and AI",
    "ENVI": "Environment and Climate",
    "ENER": "Energy",
    "AGRI": "Agriculture, Fisheries, and Food",
    "TRAN": "Transport and Tourism",
    "EMPL": "Employment and Social Affairs",
    "SANT": "Public Health",
    "CULT": "Education, Culture, and Youth",
    "LIBE": "Civil Liberties, Internal Affairs, and Migration",
    "AFET": "Foreign Affairs and Human Rights",
    "SEDE": "Security and Defence",
    "INTA": "International Trade",
    "INST": "Institutional Affairs and Budget"
}

# ── SPARQL query factory functions ────────────────────────────────────────────
# Each value is a lambda that accepts a search term and returns a complete
# SPARQL query string ready to be sent to the appropriate endpoint.
SPARQL_QUERIES : Dict[str, Callable[[str], str]] = {
    "POLITICAL_PARTY" : lambda x: f"""
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX ps: <http://www.wikidata.org/prop/statement/>
PREFIX pr: <http://www.wikidata.org/prop/reference/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?item ?itemLabel ?color WHERE {{
  SERVICE wikibase:mwapi {{
      bd:serviceParam wikibase:api "EntitySearch" .
      bd:serviceParam wikibase:endpoint "www.wikidata.org" .
      bd:serviceParam mwapi:search '''{x}''' .
      bd:serviceParam mwapi:language "en" .
      ?item wikibase:apiOutputItem mwapi:item .
  }}
  ?item wdt:P31 wd:Q7278 . 
  OPTIONAL {{
    ?item p:P465 ?statement .
    ?statement ps:P465 ?color .
    ?statement wikibase:rank ?rank . 
    BIND(IF(?rank = wikibase:PreferredRank, 1, 0) AS ?isPreferred)
    BIND(IF(EXISTS {{ ?statement prov:wasDerivedFrom ?refNode }}, 1, 0) AS ?hasReference)
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
ORDER BY DESC(?hasReference) DESC(?isPreferred)
LIMIT 1
""",
    "NATIONAL_ELECTIONS": lambda x: f"""
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX ps: <http://www.wikidata.org/prop/statement/>
PREFIX pr: <http://www.wikidata.org/prop/reference/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?country ?countryLabel ?legislature ?legislatureLabel ?chamber ?chamberLabel ?seats ?member ?election ?electionLabel WHERE {{
  BIND({x} AS ?country)
  ?country wdt:P194 ?legislature . 
  {{
    {{
      ?legislature wdt:P527 ?chamber .
      ?chamber wdt:P31/wdt:P279* wd:Q375928 .
    }}
    UNION
    {{
      ?legislature p:P527 ?statement .
      ?statement ps:P527 ?chamber .
      ?statement pq:P3831 wd:Q375928 . 
    }}
  }}
  UNION
  {{
    VALUES ?unicameralType {{ wd:Q37002670 wd:Q15238777 wd:Q3102743 }}
    ?legislature wdt:P31 ?unicameralType .
    BIND(?legislature AS ?chamber)
  }}
  ?chamber wdt:P1342 ?seats .
  ?member wdt:P361 ?chamber .
  ?election wdt:P541 ?member .
  ?election wdt:P585 ?time .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "cs,en". }}
}}
ORDER BY ?time
""",
    "EU_POLITICAL_GROUP" : lambda x: f"""
SELECT ?item ?itemLabel ?acronym ?color WHERE {{
  ?item wdt:P31 wd:Q779079.
  ?item wdt:P1813 ?acronym.
  OPTIONAL {{ ?item wdt:P465 ?color. }}
  FILTER(LCASE(STR(?acronym)) = '''{x}''')
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "en".
  }}
}}
""",
    "EU_POLITICAL_GROUP_ALL" : lambda _: """
SELECT ?item ?acronym ?color WHERE {
  ?item wdt:P31 wd:Q779079.
  ?item wdt:P1813 ?acronym.
  OPTIONAL { ?item wdt:P465 ?color. }
}
"""
}

# Year range for Eurostat data fetching and for VotesFetch pagination.
START_YEAR = 1990

END_YEAR = datetime.now().year