# EUVoteAnalyzer

EUVoteAnalyzer is an automatic tool that fetches data about European Parliament voting from multiple sources, runs statistical analyses, and visualises results through a built-in web application.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

---

## Table of contents

1. [Project overview](#1-project-overview)
2. [Prerequisites](#2-prerequisites)
3. [Configuration](#3-configuration)
4. [Installation](#4-installation)
5. [Docker services](#5-docker-services)
6. [Running the pipeline](#6-running-the-pipeline)
7. [CLI reference](#7-cli-reference)
8. [Web application – user manual](#8-web-application--user-manual)
9. [Testing](#9-testing)
10. [Reproducing the evaluation](#10-reproducing-the-evaluation)
11. [Project structure](#11-project-structure)

---

## 1 Project overview

The project consists of two main components:

- **EUVoteAnalyzer** – a Python pipeline that collects raw data (EP Open Data API, Wikidata SPARQL, Eurostat, Wikipedia/LLM), stores it in a MariaDB database, pre-computes derived statistics, runs analyses, and writes the results as static JSON files consumed by the web application.
- **Web application** (`web/`) – a PHP/JavaScript single-page dashboard that reads the pre-computed JSON files and provides a live API for on-demand queries (MEP comparison, member/subject statistics, anomaly detection). Online version is available also on [https://research.smazinka.eu/EUVoteAnalyzer/](https://research.smazinka.eu/EUVoteAnalyzer/)

```
┌─────────────────────────────────────────────────────────────┐
│                       Data sources                          │
│   EP Open Data API · Wikidata · Eurostat · Wikipedia/LLM    │
└──────────────────────────┬──────────────────────────────────┘
                           │  python main.py --all
                           ▼
               ┌───────────────────────┐
               │   MariaDB (port 3330) │
               └───────────┬───────────┘
                           │  python main.py --prepare
                           │  python main.py --analyse
                           ▼
               ┌───────────────────────┐
               │   web/data/<term>/    │  (static JSON files)
               └───────────┬───────────┘
                           │
                           ▼
               ┌───────────────────────┐
               │  Web app (port 8082)  │  (PHP + JS dashboard)
               └───────────────────────┘
```

---

## 2 Prerequisites

| Requirement | Minimum | Recommended |
|---|---|---|
| Docker | any current | latest |
| Python | 3.10 | **3.13** (tested and optimised) |
| Free disk space | 10 GB | 20 GB (cache + DB volumes) |

Note: Requires Gemini API key ([Google AI Studio](https://aistudio.google.com/api-keys)) or locally running Ollama 

Useful links: [Docker](https://www.docker.com/), [Python 3.13](https://www.python.org/downloads/release/python-31313/)

---

## 3 Configuration

### 3.1 Python pipeline

Inside `EUVoteAnalyzer/core/` create `secret.py` with the following content:

```python
GEMINI_API_KEY = "<YOUR_API_KEY>"

DB_USER     = "zastupko"
DB_PASSWORD = "zastupko"
DB_DB       = "ep"
DB_SERVER   = "localhost"
DB_PORT     = 3330
```

Replace `<YOUR_API_KEY>` with a key obtained from [Google AI Studio](https://aistudio.google.com/api-keys). The remaining values are the defaults set in `docker-compose.yml`; change them only if you modify the Compose file.

### 3.2 Web application

The web application reads its database credentials from `web/lib/settings.php`. The default values match the Docker Compose configuration:

```php
define("DB_SERVER",   "mariadb_eu");
define("DB_USERNAME", "zastupko");
define("DB_PASSWORD", "zastupko");
define("DB_DATABASE", "ep");
define("DEBUG",       false);   // set to true only for development
```

`DB_SERVER` must be the Docker service name (`mariadb_eu`), not `localhost`, because the PHP container connects to the database over the internal Docker network.

---

## 4 Installation

### Linux

```bash
make setup    # create venv and install Python dependencies
make start    # start all Docker containers
```

Alternatively, without Make:

```bash
python3.13 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
docker compose up -d
```

### Windows

```ps
py -3.13 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
docker compose up -d
```

Verify that all containers are running:

```bash
docker compose ps
```

You should see four services in the `running` state: `mariadb_eu`, `mariadb_test`, `phpmyadmin_eu`, and `eu_vote_web`.

---

## 5 Docker services

| Service | Container | Host port | Purpose |
|---|---|---|---|
| MariaDB (main) | `mariadb_eu` | **3330** | Production database used by the pipeline and the web application |
| MariaDB (test) | `mariadb_test` | **3331** | Isolated database used exclusively by the test suite |
| phpMyAdmin | `phpmyadmin_eu` | **8081** | Database management UI (accessible at `http://localhost:8081`) |
| Web application | `eu_vote_web` | **8082** | PHP + JavaScript dashboard (accessible at `http://localhost:8082`) |

To stop all containers:

```bash
make stop          # stops containers, preserves data volumes
make clean         # stops containers AND deletes all data volumes and venv
```

---

## 6 Running the pipeline

The pipeline is designed to be run in phases. Each phase can be executed independently or combined. The recommended full-pipeline order for a fresh installation is:

```bash
# 1. Fetch reference data (fast, ~minutes)
make run ARGS="--terms --parties --meps"

# 2. Import vote records (recommended: HowTheyVote dump, much faster than the EP API)
make run ARGS="--import --htv"

# 3. Alternatively, fetch votes directly from the EP API (slow — may take many hours)
#    make run ARGS="--votes"

# 4. Fetch Eurostat socioeconomic indicators (needed for correlation analysis)
make run ARGS="--stats"

# 5. Fetch national parliament composition via Wikipedia + LLM (optional)
make run ARGS="--national"

# 6. Pre-compute derived statistics for all terms
make run ARGS="--prepare"

# 7. Run all analyses for a specific term and write JSON output files
make run ARGS="--analyse --analyse-term 10 --analyse-type all"
```

> **Note:** Step 7 must be repeated for each parliamentary term you want to visualise. The output is written to `web/data/term_<N>/`. Once JSON files are present, the web application at `http://localhost:8082` will display the results automatically.

### Useful utility commands

```bash
# Re-run only a specific analysis type
make run ARGS="--analyse --analyse-term 10 --analyse-type cohesion"

# Invalidate stale cache entries older than 30 days and re-fetch
make run ARGS="--terms --cache-since 30"

# Drop and recreate the database schema (WARNING: deletes all data)
make run ARGS="--clear-database"
```

---

## 7 CLI reference

All options are passed via `ARGS` on Linux (`make run ARGS="..."`) or directly to `python main.py` on Windows.

### Data collection

| Option | Description |
|---|---|
| `--all` | Run all data-collection phases |
| `--terms` | Fetch parliamentary-term metadata from the EP Open Data API |
| `--parties` | Fetch EP political groups and national party records (enriched via Wikidata) |
| `--meps` | Fetch MEP biographies and group memberships |
| `--votes` | Fetch plenary-session and roll-call vote records from the EP API *(slow)* |
| `--stats` | Download Eurostat socioeconomic indicators |
| `--national` | Collect national parliament composition via Wikipedia and LLM |
| `--import` | Import data from external dumps |
| `--htv` | Import the latest HowTheyVote release automatically *(recommended instead of `--votes`)* |
| `--htv-votes <PATH>` | Path to a local HowTheyVote `votes.csv` (or `.csv.gz`); overrides auto-download |
| `--htv-member-votes <PATH>` | Path to a local HowTheyVote `member_votes.csv` (or `.csv.gz`) |
| `--eval-log` | Run the `NationalFetch` extraction-evaluation harness over `--eval-ground-truth-dir` and write per-record logs to `--eval-log-dir` (see [§10](#10-reproducing-the-evaluation)) |
| `--eval-content-mode <html\|markdown\|raw>` | Content-reduction mode for `--eval-log`: `html` (production baseline), `markdown` (ablation), `raw` (no reduction, ablation) — default `html` |
| `--eval-ground-truth-dir <PATH>` | Ground-truth directory driving `--eval-log` (default `EUVoteAnalyzer/test/ground_truth_extraction`) |
| `--eval-log-dir <PATH>` | Output directory for `--eval-log` records (default `logs/extraction_eval`) |

### Pre-computation

| Option | Description |
|---|---|
| `--prepare` | Pre-compute derived statistics (`statistika_clen`, `statistika_subjekt`, `byl_pritomen`, `soudrznost`) |
| `--prepare-term <TERM>` | Limit `--prepare` to a single parliamentary term ordinal |

### Analysis

| Option | Description |
|---|---|
| `--analyse` | Run analyses (requires `--analyse-term`) |
| `--analyse-term <TERM>` | Parliamentary term ordinal (e.g. `10`) |
| `--analyse-type <TYPE>` | Analysis type — see table below (default: `all`) |
| `--output-dir <PATH>` | Root output directory for JSON files (default: `web/data`) |
| `--show-dumps` | Print pre-computed JSON to stdout after writing |

Available `--analyse-type` values:

| Type | Output file | Description |
|---|---|---|
| `cohesion` | `party_cohesion.json` | Agreement Index per EP political group |
| `country` | `country_cohesion.json` | Agreement Index per member-state delegation |
| `inter-faction` | `inter_faction_cohesion.json` | Pairwise cohesion between EP groups |
| `participation` | `mep_participation.json` | Voting participation rate per MEP |
| `loyalty` | `mep_loyalty.json` | MEP loyalty to their EP group per term |
| `government-loyalty` | `government_opposition_loyalty.json` | MEP loyalty to their EP group, split by whether their national party was in government or opposition at the time of the vote |
| `category-correlate` | `category_correlations.json` | Correlation of voting with Eurostat socioeconomic indicators |
| `topic-profile` | `party_topic_profile.json` | Party voting profiles across policy categories (heatmap data) |
| `deviation-rules` | `topic_deviation_rules.json` | Association rules for within-party topic deviations |
| `opposition-rules` | `faction_opposition_rules.json` | Association rules for inter-faction opposition patterns |
| `anomally` | `anomalous_votes/<id>.json` | Per-MEP anomalous vote detection |
| `mep-pairs` | `mep_comparison/` | Pairwise MEP agreement rankings (overall + per category) |
| `all` | *(all of the above)* | Run every analysis type |

### Infrastructure

| Option | Description |
|---|---|
| `--local-cache <PATH>` | Path to the local cache directory (default: `cache/`) |
| `--cache-since <DAYS>` | Consider cache entries valid for this many days (default: `100`) |
| `--disable-cache` | Bypass cache for this run; cached files are preserved |
| `--clear-cache` | Delete all cached files |
| `--clear-database` | Drop all database tables |
| `--data-schema <PATH>` | Path to the schema JSON file (default: `data-model/schema-EU.json`) |
| `--log-level <0–3>` | Verbosity: `0` INFO · `1` OK · `2` WARN · `3` ERR! |

---

## 8 Web application – user manual

Open `http://localhost:8082` in a browser. The dashboard displays analysis results for European Parliament terms. Use the **term selector** in the top-right corner to switch between available terms and the **language selector** to change the interface language (24 EU languages supported).

> **Important:** A tab shows data only after the corresponding analysis type has been run for the selected term (see [Section 6](#6-running-the-pipeline)). If a tab shows a loading spinner that never resolves, the JSON file for that term is missing.

### 8.1 Party cohesion

Displays the **Agreement Index** (Hix–Noury–Roland metric) for each EP political group — the share of votes in which the majority of the group's members voted together. A higher value indicates stronger internal discipline.

The table lists each group with its cohesion score, vote count, and colour indicator. Rows are sortable and searchable via DataTables.

### 8.2 Country cohesion

Same metric applied to **national delegations**: MEPs are grouped by citizenship rather than party affiliation, showing how consistently members from the same country vote together regardless of their EP group.

### 8.3 Inter-faction cohesion

A **pairwise cohesion matrix** between all EP groups. Each cell shows how often two groups voted the same way on votes where both participated. High values indicate ideological proximity; low values indicate consistent opposition.

### 8.4 MEP participation

Ranks all MEPs by their **participation rate** — the proportion of votes they cast out of all votes held during their active membership in the term. Useful for identifying frequently absent members.

### 8.5 MEP loyalty

Shows each MEP's **loyalty score** to their EP group: the share of votes in which the MEP voted the same way as their group's majority position. A score below 50 % indicates systematic dissent.

### 8.6 Category correlations

Scatter plots and regression lines for the correlation between **voting patterns in policy categories** and **Eurostat socioeconomic indicators** (GDP per capita, unemployment rate, etc.) at the member-state level.

Use the two dropdowns to select the policy category and the Eurostat indicator. The chart updates immediately. Statistical significance is marked with asterisks (*, **, ***) next to the Pearson *r* value.

### 8.7 MEP comparison

Select two MEPs from the searchable dropdowns and click **Compare**. The panel shows:

- An **overall agreement percentage** badge (colour-coded from red to green).
- A **per-policy-category breakdown** table showing agreement and shared vote count for each area.

The **Rankings** accordion (below the comparison form) lists all pre-computed MEP pairs sorted by agreement score for the selected term and category view.

### 8.8 Topic profile

Three sub-tabs share a common data load:

| Sub-tab | Content |
|---|---|
| **Heatmap** | Party × policy-category heatmap of normalised FOR/AGAINST/ABSTAIN shares. Use the group and country filter dropdowns to narrow the view. |
| **Deviation rules** | Association rules (FP-Growth) describing which policy categories a party consistently deviates from their usual position on. Columns: antecedent categories, consequent, support, confidence, lift. |
| **Opposition rules** | Association rules describing which combinations of group+direction tend to appear together across votes (faction-level opposition patterns). |

### 8.9 Member statistics

A **histogram** of MEP FOR-vote ratios (binned into 10 % deciles) and a full DataTable listing every MEP with their raw FOR / AGAINST / ABSTAIN / TOTAL vote counts for the term.

### 8.10 Subject statistics

A **stacked horizontal bar chart** for EP political groups showing the percentage breakdown of FOR / ABSTAIN / AGAINST votes. Below it, a DataTable lists national parties with the same counts plus a FOR % column.

### 8.11 Anomaly detection

Identifies votes in which an MEP voted **against their group's majority position** (anomalous votes).

1. Type a name in the search box to filter the MEP list.
2. Select an MEP from the dropdown.
3. Click **Detect anomalies**.

The result panel shows three summary tiles (total votes, anomalous count, anomaly rate), a bar chart of anomalous votes by policy category, and a detail table with the date, category, MEP vote, group majority vote, and vote subject for each anomalous vote.

---

## 9 Testing

The test suite seeds an isolated MariaDB instance (port 3331) with synthetic fixture data, runs the full preprocessing pipeline, executes every analyser, and compares output against stored ground-truth JSON files.

```bash
# Full test cycle (seed + run)
make test

# Seed only (populate the test database without running analyses)
make run ARGS="--test-init"

# Run analyses against an already-seeded test database
make run ARGS="--test-run"
```

All fixture data lives in `EUVoteAnalyzer/test/data/` and ground-truth expectations in `EUVoteAnalyzer/test/ground_truth/`. The synthetic parliamentary term ordinal `99` is reserved exclusively for testing and never collides with real data.

---

## 10 Reproducing the evaluation

The accompanying paper evaluates the `NationalFetch` source-acquisition and LLM-extraction
pipeline against two independently built ground-truth datasets. This repository contains the
evaluation **framework** (`scripts/`) needed to reproduce those numbers; it does not ship the
generated results themselves — running the steps below regenerates `results/` locally.

```bash
# 1. Generate per-record extraction logs against ground truth (one content-reduction
#    mode at a time). Re-running against an already-cached country/year makes no new
#    LLM calls — see EUVoteAnalyzer/logic/fetchers.py's eval_extract_one.
make run ARGS="--eval-log --eval-content-mode html   --eval-log-dir logs/extraction_eval"
make run ARGS="--eval-log --eval-content-mode markdown --eval-log-dir logs/extraction_eval_ablation_markdown"
make run ARGS="--eval-log --eval-content-mode raw      --eval-log-dir logs/extraction_eval_ablation_raw"

# 2. Score each mode's logs against test/ground_truth_extraction/ (existence, seat-count,
#    and government-status precision/recall/F1, micro-averaged; writes results/*.json + *_table.md)
./venv/bin/python scripts/evaluate_extraction.py --eval-log-dir logs/extraction_eval             --out-json results/extraction_evaluation_html.json          --out-md results/extraction_evaluation_html_table.md
./venv/bin/python scripts/evaluate_extraction.py --eval-log-dir logs/extraction_eval_ablation_markdown --out-json results/extraction_evaluation_markdown.json     --out-md results/extraction_evaluation_markdown_table.md
./venv/bin/python scripts/evaluate_extraction.py --eval-log-dir logs/extraction_eval_ablation_raw      --out-json results/extraction_evaluation_raw_sample.json   --out-md results/extraction_evaluation_raw_sample_table.md

# 3. Bootstrap 95% confidence intervals (and paired html-vs-markdown/raw significance
#    tests) over the results produced in step 2 — no API cost, pure post-processing
./venv/bin/python scripts/bootstrap_ci.py

# 4. Build the independent EP-linking reconciliation dataset (test/ground_truth_ep_linking/)
#    from local cache only — no network calls
./venv/bin/python scripts/build_ep_linking.py
```

`EUVoteAnalyzer/test/ground_truth_extraction/` and `EUVoteAnalyzer/test/ground_truth_ep_linking/`
are the two ground-truth datasets themselves (manually verified / agent-built-and-validated
respectively — see each directory's own `README.md` for provenance and methodology) and **are**
version-controlled, since they are fixed evaluation inputs, not generated output.

---

## 11 Project structure

```
.
├── main.py                      # CLI entry point
├── Makefile                     # convenience targets (setup, start, run, test, …)
├── requirements.txt             # Python dependencies
├── docker-compose.yml           # MariaDB × 2 + phpMyAdmin + web server
├── Dockerfile.web               # Apache + PHP image for the web application
├── data-model/
│   └── schema-EU.json           # Database schema definition
├── EUVoteAnalyzer/
│   ├── core/
│   │   ├── secret.py            # credentials (not in VCS)
│   │   ├── storage.py           # LocalCache, JSONWalker, SmartLoader, SmartSparqlQuery
│   │   ├── utils.py             # ProgressPrint, ErrorHandler, helper functions
│   │   ├── llm.py               # LLM / SmartLLM abstraction (Gemini, Ollama)
│   │   ├── networking.py        # HTTP helpers
│   │   └── common.py            # shared constants and imports
│   ├── database/
│   │   ├── orm.py               # DB, DBColumn, DBObject, DBObjects, SchemaExplorer
│   │   └── schema.py            # SchemaManager (create / drop tables)
│   ├── logic/
│   │   ├── fetchers.py          # TermsFetch, PartiesFetch, MepsFetch, VotesFetch,
│   │   │                        #   StatsFetch, NationalFetch
│   │   ├── importers.py         # HtvImport (HowTheyVote CSV import)
│   │   ├── preprocess.py        # Preprocessor (derived statistics)
│   │   ├── analyser.py          # Analyser (all analysis methods)
│   │   └── api_walker.py        # APIWalker fluent builder for JSON-LD traversal
│   └── test/
│       ├── tester.py                  # Tester (seed + verify)
│       ├── data/                      # fixture JSON files
│       ├── ground_truth/              # expected analyser output (synthetic test data)
│       ├── ground_truth_extraction/   # manually verified election-results ground truth (§10)
│       └── ground_truth_ep_linking/   # independent EP-linking reconciliation dataset (§10)
├── scripts/
│   ├── evaluate_extraction.py   # scores --eval-log output against ground truth (§10)
│   ├── bootstrap_ci.py          # confidence intervals over evaluate_extraction.py's output (§10)
│   └── build_ep_linking.py      # builds ground_truth_ep_linking/ from local cache (§10)
├── results/                  # evaluation output (git-ignored — regenerate via §10)
└── web/
    ├── index.php             # single-page application shell
    ├── methodology/          # English-only evaluation methodology & results summary
    ├── lib/
    │   ├── api.php           # JSON API router (terms, member_stats, compare, …)
    │   ├── data.php          # data-access helpers
    │   ├── db.php            # PDO database connection
    │   ├── lib.php           # shared PHP utilities
    │   └── settings.php      # database credentials and debug flag
    ├── assets/
    │   ├── js/
    │   │   ├── app.js        # main dashboard logic (Chart.js, DataTables)
    │   │   └── library.js    # Request HTTP client and Dictionary i18n manager
    │   ├── css/               # stylesheets
    │   └── locales/           # locale JSON files (24 EU languages)
    └── data/
        └── term_<N>/          # pre-computed JSON output (git-ignored)
```

---

**Notes:**

- The `web/data/` directory is not included in the repository. It must be populated by running `--analyse` before the web application can display results.
- The `results/` directory (evaluation scores, confidence intervals, per-record detail) is likewise not included — see [§10](#10-reproducing-the-evaluation) to regenerate it locally.
- As part of this project, components for visualising the analysis as an extension of the Zastupko.cz project were also implemented; however, they are not part of this repository as they cannot be published.
