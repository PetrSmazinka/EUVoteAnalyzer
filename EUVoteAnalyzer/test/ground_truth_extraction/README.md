# Ground truth dataset — NationalFetch extraction evaluation

This dataset is **independent of `test/data`/`test/ground_truth`** (which belong to
`Tester`, the analytics-pipeline test harness from DP kap. 7.1). It exists to evaluate
the Wikipedia → LLM extraction pipeline (`NationalFetch`), phase by phase, not the
downstream cohesion/loyalty analyses.

## Status: manually created and verified by the human author

All 54 files (27 EU countries × last 2 elections) have `parties` filled in and have gone
through a full manual review by the human author. An initial agent-assisted research
pass (independent web search per record — ParlGov / official results / Wikidata, never
from this pipeline's own cache/output) was used to draft the first version of most files
faster, but every one of the 54 files subsequently went through a complete manual
correction pass, not just a review of the agent's flagged exceptions. That manual pass
included (see the project's paper-handoff notes for the full account): systematic
duplicate-`wikidata_id` scans, fixing 36 reversed full-name/abbreviation parenthetical
splits, catching and reverting an over-broad automated "recheck" that had overwritten
already-correct data, a ParlGov cross-check of the `abbreviation` field across 250
parties, and country-by-country resolution of every flagged discrepancy. The dataset
should be described as **manually created**, with agent assistance as a drafting aid for
the initial lookups, not as the source of its correctness.

**Do not copy from `cache/llm/parliamentary_results/<country>/<year>.html`** — that file
*is* the pipeline's own (unverified) extraction output. Copying it here would make the
"ground truth" identical to what we're trying to test, silently inflating every accuracy
number to ~100%.

**General firewall rule (applies to every dataset under `test/`, not just this one):**
the same logic cuts the other way too — no matching/lookup heuristic developed *while
building* a ground-truth dataset (e.g. the abbreviation/Wikidata-alias matching used to
build `test/ground_truth_ep_linking/`) may be ported back into the evaluated pipeline's
own code (`NationalFetch`/`_match_party_entity` in `EUVoteAnalyzer/logic/fetchers.py`).
Either direction of leakage — pipeline output flowing into ground truth, or
ground-truth-construction techniques flowing into the pipeline — lets the system under
test converge on the same answer key it's graded against, for reasons that have nothing
to do with its actual extraction/linking capability. See
`test/ground_truth_ep_linking/README.md` for the concrete case that prompted writing this
down explicitly.

### Where to verify

- **Seats and government status:** cross-check against ParlGov
  (`https://parlgov.fly.dev/data/elections/<iso3>/`, e.g. `deu` for Germany, `aut` for
  Austria) or an official electoral-commission source — not Wikipedia alone, per the
  assignment's constraint (otherwise we'd only measure agreement with one source, not
  with reality). Use the Wikipedia infobox as a cross-check/tiebreaker, not the primary
  source.
- **`wikidata_id`:** look up each party directly on `https://www.wikidata.org` (search by
  name) and copy its `Q` identifier. Do not use the id the pipeline scraped from the
  party's own Wikipedia page (`get_wikidata_id` / JSON-LD) — that is exactly the phase-4
  output being evaluated.
- Once verified, set `source_verified_at` to the verification date and fill in the
  `sources` object with the actual URLs you checked (audit trail for the paper).

## Country/election selection

**Scope: all 27 EU member states, last 2 elections to the lower/unicameral house each =
54 records.** (Earlier drafts of this dataset covered a representative 10-country subset,
chosen so the baseline evaluation could reuse an existing production LLM cache with zero
new API calls; that constraint was dropped in favor of full EU coverage.)

**Bulgaria** had two elections in 2024 (June and October); we use the October 2024 and
2026 elections. The pipeline's own cached term list stored these slugs **with spaces
instead of underscores** (`"October 2024 Bulgarian parliamentary election"`) — an invalid
Wikipedia path. We corrected the slug to valid underscore form for `wikipedia_url` here,
but the malformed original is itself a data point worth mentioning in the paper as a
discovery-phase robustness issue for Bulgaria specifically.

**Finland**: uses the true last two elections (2019, 2023) — an earlier draft had
mistakenly used 2003/2007 based on a pipeline coverage gap (`EXTRACT_PARTY_RESULTS` was
never cached past 2007 in production), which was a bug in that draft's selection logic,
not a reflection of Finland's real election history.

**Very recent elections** (several countries have a 2025/2026 election as their most
recent): each was checked for whether it had actually occurred with certified results as
of 2026-09-14 (dataset creation date) before being used — see `VERIFICATION_REPORT.md`
for any country where the second-most-recent election was used instead because of this.

**Chamber-size quirks**: don't be alarmed if a country's party-seat sum doesn't match its
"nominal" seat count — Germany (2021: 736 seats due to overhang/leveling mandates, pre-2025
reform), Italy (630 seats until the 2020 reform cut it to 400 from 2022 onward), and Malta
(79 vs. nominal 65, due to its proportionality-correction bonus seats) are all legitimate,
not data errors. See `VERIFICATION_REPORT.md`.

## Audit trail

Verification dates and primary sources below (auto-generated from each file's
`source_verified_at`/`sources` fields). All 54 files have been manually created/verified
by the human author (see Status above) — this table records *when* and *from what source*,
not an outstanding review step.

| File | Verified at | Primary source (seats/government) |
|---|---|---|
| Austria_2019.json | 2026-09-06 | https://parlgov.fly.dev/data/elections/aut/2019-09-29/ |
| Austria_2024.json | 2026-09-06 | Not yet indexed in ParlGov (fly.dev mirror's Austria data stops at 2019). Sourced from ... |
| Belgium_2019.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/bel/2019-05-26/, cross-checked against Wikipedia... |
| Belgium_2024.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Belgium data stops at 2019). Sourced from ... |
| Bulgaria_2024.json | 2026-09-14 | Not yet indexed in ParlGov. Sourced from Wikipedia infobox (October 2024 election), cro... |
| Bulgaria_2026.json | 2026-09-14 | Not yet indexed in ParlGov. Sourced from Wikipedia infobox, cross-checked by summing to... |
| Croatia_2020.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/hrv/2020-07-05/ and https://parlgov.fly.dev/data... |
| Croatia_2024.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Croatia data stops at 2020). Sourced from ... |
| Cyprus_2021.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/cyp/2021-05-30/ and https://parlgov.fly.dev/data... |
| Cyprus_2026.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Cyprus data stops at 2021). Seats: Wikiped... |
| Czechia_2021.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/cze/2021-10-09/ and https://parlgov.fly.dev/data... |
| Czechia_2025.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Czechia data stops at 2021). Sourced from ... |
| Denmark_2022.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/dnk/2022-11-01/ and https://parlgov.fly.dev/data... |
| Denmark_2026.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Denmark data stops at 2022). Sourced from ... |
| Estonia_2019.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/est/2019-03-03/ and https://parlgov.fly.dev/data... |
| Estonia_2023.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/est/2023-03-05/ and https://parlgov.fly.dev/data... |
| Finland_2019.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/fin/2019-04-14/ and cabinets Rinne (2019-06-06) ... |
| Finland_2023.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/fin/2023-04-02/ and https://parlgov.fly.dev/data... |
| France_2022.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/fra/2022-06-19/ and https://parlgov.fly.dev/data... |
| France_2024.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's France data stops at 2022). Sourced from W... |
| Germany_2021.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/deu/2021-09-26/ and https://parlgov.fly.dev/data... |
| Germany_2025.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Germany data stops at 2021). Sourced from ... |
| Greece_2019.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/grc/2019-07-07/ and https://parlgov.fly.dev/data... |
| Greece_2023.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/grc/2023-06-25/ and https://parlgov.fly.dev/data... |
| Hungary_2022.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/hun/2022-04-03/ and https://parlgov.fly.dev/data... |
| Hungary_2026.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror data currently stops around 2021-2023 for mo... |
| Ireland_2020.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/irl/2020-02-08/ and https://parlgov.fly.dev/data... |
| Ireland_2024.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Ireland data stops at 2020). Sourced from ... |
| Italy_2018.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/ita/2018-03-04/ and https://parlgov.fly.dev/data... |
| Italy_2022.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/ita/2022-09-25/ and https://parlgov.fly.dev/data... |
| Latvia_2018.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/lva/2018-10-06/ and https://parlgov.fly.dev/data... |
| Latvia_2022.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/lva/2022-10-01/ and https://parlgov.fly.dev/data... |
| Lithuania_2020.json | 2026-09-22 | https://parlgov.fly.dev/data/elections/ltu/2020-10-11/ and https://parlgov.fly.dev/data... |
| Lithuania_2024.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Lithuania data stops at 2020). Sourced fro... |
| Luxembourg_2018.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/lux/2018-10-14/ and https://parlgov.fly.dev/data... |
| Luxembourg_2023.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Luxembourg data stops at 2018). Sourced fr... |
| Malta_2022.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/mlt/2022-03-26/ and https://parlgov.fly.dev/data... |
| Malta_2026.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Malta data stops at 2022). Sourced from Wi... |
| Netherlands_2023.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Netherlands data stops at 2021). Sourced f... |
| Netherlands_2025.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Netherlands data stops at 2021). Sourced f... |
| Poland_2019.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/pol/2019-10-13/ and https://parlgov.fly.dev/data... |
| Poland_2023.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Poland data stops at 2019). Sourced from W... |
| Portugal_2024.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Portugal data stops at Jan 2022). Sourced ... |
| Portugal_2025.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Portugal data stops at Jan 2022). Sourced ... |
| Romania_2020.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/rou/2020-12-06/ and https://parlgov.fly.dev/data... |
| Romania_2024.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Romania data stops at 2020). Sourced from ... |
| Slovakia_2020.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/svk/2020-02-29/ and https://parlgov.fly.dev/data... |
| Slovakia_2023.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Slovakia data stops at 2020). Sourced from... |
| Slovenia_2018.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/svn/2018-06-03/ and https://parlgov.fly.dev/data... |
| Slovenia_2022.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/svn/2022-04-22/ and https://parlgov.fly.dev/data... |
| Spain_2019.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/esp/2019-11-10/ and https://parlgov.fly.dev/data... |
| Spain_2023.json | 2026-09-14 | Not yet indexed in ParlGov (fly.dev mirror's Spain data stops at Nov 2019). Sourced fro... |
| Sweden_2018.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/swe/2018-09-09/ and https://parlgov.fly.dev/data... |
| Sweden_2022.json | 2026-09-14 | https://parlgov.fly.dev/data/elections/swe/2022-09-11/ and https://parlgov.fly.dev/data... |

Full source URLs are in each file's own `sources` field (truncated here for table width).
