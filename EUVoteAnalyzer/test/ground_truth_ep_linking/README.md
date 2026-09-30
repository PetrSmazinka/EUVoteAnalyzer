# EP API entity-linking ground truth

Independent second reconciliation source for evaluating entity linking, alongside
Wikidata (`get_wikidata_id`/JSON-LD). Maps each unique party from
`test/ground_truth_extraction/` (290 unique country/party_name pairs across all 54
election records) to the corresponding European Parliament Open Data
`corporate-bodies` record(s) (`cache/data.europarl.europa.eu/api/v2/corporate-bodies/`,
`classification = NATIONAL_POLITICAL_GROUP`), when one exists. Built entirely from the
local cache by `scripts/build_ep_linking.py` (no network calls; rerun any time the
underlying ground truth changes).

**Why this source specifically:** EP API is genuinely independent of Wikipedia/Wikidata —
different maintaining organization (European Parliament, not Wikimedia), different
naming convention (predominantly native-language abbreviations, tracked per
parliamentary term), and a different reason to exist (it only lists parties that
actually had at least one MEP). Addresses the concern that Wikidata+Wikipedia is "almost
one source." HowTheyVote.eu was also considered but **does not track national-party
affiliation at all** (only EU-level political group membership, e.g. "RENEW") — verified
by downloading and inspecting `members.csv`, `member_votes.csv`, `groups.csv`,
`group_memberships.csv` from its latest GitHub release; not usable for this purpose.

**Caveat inherited from EP's data model:** EP issues a new `corporate-bodies` identifier
for the same real-world national party in every parliamentary term it holds MEPs in (a
long-lived party like SPD has 6 separate ids, one per term). This dataset groups those
per-term records back into one real-world party wherever EP's own `label` stays constant
across terms; a handful of parties that renamed mid-history (see Manual resolutions
below) needed a term-crossing merge that constant-label grouping alone can't catch.

**Firewall between ground-truth construction and the evaluated pipeline (important for
validity):** building this dataset required matching heuristics — abbreviation matching,
a multilingual Wikidata-alias enrichment pass, manual alliance/rename resolutions — that
go well beyond what `NationalFetch`'s own entity-linking step (`_match_party_entity` in
`EUVoteAnalyzer/logic/fetchers.py`) does. None of that logic has been, or should be,
ported into the evaluated pipeline. Doing so would let the system under test resolve
entities via the same external authorities (Wikidata's multilingual labels, EP's own
alliance/rename structure) used to construct its own answer key, inflating any
entity-linking accuracy metric toward the coverage ceiling of this dataset rather than
measuring the pipeline's actual linking capability — a textbook evaluation-leakage risk.
`_match_party_entity` has not changed since it was mechanically extracted from
`NationalFetch._process()` (see `results/` matcher-fix branch history), independent of
all ground-truth work described here. This dataset's legitimate uses are (a) an
independent cross-check of the primary ground truth's `wikidata_id` values against a
separately-maintained registry, and (b) a secondary entity-linking metric computed only
over the subset of records this dataset actually resolves (see Results) — records left
`no_match`/`ambiguous` here must be excluded from that metric's denominator, not counted
as pipeline failures, since the gap is this dataset's coverage limit, not the pipeline's.

Relatedly: **perfect (100%) coverage was never the target and should not be expected.**
A meaningful share of the parties in `test/ground_truth_extraction/` are small, new, or
regional and genuinely never held an EP seat — their absence from EP's data is a correct
outcome, not a gap in this dataset's matching logic. Continuing to chase full coverage
past that point would itself be a sign of overfitting the matcher to this specific
dataset rather than of a better general method.

**Finding: EP's declared language parameter is not reliable for `NATIONAL_POLITICAL_GROUP`
records.** The Corporate Bodies API nominally supports per-language content negotiation
(`prefLabel.<lang>`), and this is exactly what `PartiesFetch` (`EUVoteAnalyzer/logic/
fetchers.py`) requests via `prefLabel[LANGUAGE]` with `LANGUAGE = "en"`. In practice, across
all 2012 cached `NATIONAL_POLITICAL_GROUP` records, `prefLabel.en` is byte-identical to the
record's modal (most common) language value — i.e. not a translation at all, just the
native-language name copied into the `en` slot — in **98.7%** of records (1986/2012); most
of the remaining 1.3% differ only in capitalization, not content (e.g. `MODERATERNE` vs.
`Moderaterne`). A concrete example: requesting Austria's SPÖ by `prefLabel.en` returns
`"Sozialdemokratische Partei Österreichs"` verbatim, not an English rendering. This means
any matching approach that trusts EP's declared language tag will systematically fail for
the large majority of non-English-named national parties — not because the matching logic
is weak, but because the source doesn't provide what it appears to offer. This both
motivates the Wikidata enrichment pass above (anchor on the Wikidata QID, independent of
EP's own language tagging, rather than trusting `prefLabel.en`) and retroactively explains
an existing, already-conservative design choice in `PartiesFetch`: national parties are
deliberately not Wikidata-enriched there, "to avoid unreliable matches" (see that class's
docstring) — a decision made before this finding was quantified, now backed by a concrete
number.

## Methodology

1. For each ground-truth party, generate alias candidates: the full normalized
   `party_name`, any parenthetical content normalized separately (e.g. `"Social
   Democratic Party of Germany (SPD)"` → `["social democratic party of germany",
   "spd"]`), and the normalized `abbreviation` field from `test/ground_truth_extraction/`.
2. EP records for the party's country are grouped by shared normalized `label` across
   parliamentary terms. Each group's alias set is its `label`, every `prefLabel`
   translation, and every `altLabel` translation it has ever carried.
3. **Wikidata enrichment pass** (only used for parties the first pass leaves
   `no_match`/`ambiguous`): EP's own `label`/`prefLabel` are predominantly
   native-language (e.g. Austria's ÖVP/SPÖ/FPÖ/Greens are all tracked under their
   German abbreviations, not English names), and ground truth's `party_name` is always
   English with an `abbreviation` that is sometimes English too (e.g. sourced from a
   Wikipedia infobox's English short form rather than the party's own native
   abbreviation). Where `party_name`/`abbreviation` alone don't reach EP's
   native-language string, the party's `wikidata_id` is used to pull every label/alias
   Wikidata has for it in the 24 official EU languages (`cache/www.wikidata.org/
   labels_aliases_batch.json`, one batched API call per ~50 ids, cached locally) and
   those are added as extra aliases before re-matching. Restricted to official EU
   languages deliberately: an early version used all ~30 Wikidata language variants and
   produced one confirmed false match — an obscure regional-dialect alias for a Polish
   party lost a diacritic during normalization and coincidentally produced the token
   "po", matching an unrelated party literally abbreviated PO. A match found only via
   this enrichment pass is tagged `_via_wikidata_alias` in `match_method` so it stays
   distinguishable from the direct-alias matches.
4. Match tiers, **no fuzzy/similarity-score matching** — only exact or unambiguous:
   - `exact_alias`: one of the party's aliases exactly equals one of a candidate group's
     aliases.
   - `token_containment`: a single-word alias appears as a whole word inside the other
     side's full alias string, **only if it identifies exactly one candidate group** —
     multiple equally-good candidates are left as `ambiguous`, not guessed.
   - Anything else: `no_match`.

   An earlier version of this script also tried `difflib.SequenceMatcher` fuzzy scoring
   as a fallback tier and produced a confirmed wrong match ("Social Democratic Party of
   Germany (SPD)" → "F.D.P." at a 0.567 score) before being caught by manual spot-check.
   Fuzzy matching was removed entirely rather than tuned, since a silently-wrong ground
   truth entry is worse than an honestly-missing one.

### Manual resolutions (17 cases)

The automatic tiers correctly flag several real parties as `ambiguous` because EP
genuinely gives the same real-world party more than one distinct `label`/cluster (a
mid-history rename, or a ground-truth row that itself represents a multi-party alliance).
`scripts/build_ep_linking.py` carries a small, hand-reviewed `MANUAL_RESOLUTIONS` table
for exactly these cases — each entry documents the evidence used (matching Wikidata id
across the rename, identical `prefLabel` across EP's two label variants, or explicit
alliance structure already recorded in ground truth):

- **Merged as the same continuously-existing party** (rename mid-history, confirmed via
  identical `prefLabel`, a stable `wikidata_id` across the ground-truth years that span
  the rename, and/or a direct Wikipedia check of the party's name history): Belgium
  *Vooruit* (sp.a → Vooruit), Denmark *Venstre*, Denmark *Danish Social Liberal
  Party*/*Social Liberals* (same party, ground truth uses two different English
  translations across election years), Estonia *Estonian Centre Party*, Ireland
  *Labour Party*, Italy *Forza Italia*, Italy *Brothers of Italy* (ran 2014–2017 as
  "Fratelli d'Italia – Alleanza Nazionale" before reverting to the plain name),
  Luxembourg *The Greens* (déi gréng formed from the 1995 merger of predecessor parties
  GLEI and GAP, which ran a joint list from 1994).
- **Merged as an explicit multi-party alliance** (the ground-truth row itself represents
  more than one EP-tracked party): Germany *Christian Democratic Union* (ground truth's
  own `abbreviation` is the combined "CDU/CSU" bloc, matching the Wikipedia-infobox
  convention of reporting CDU+CSU as one row — CDU and CSU are legally distinct sister
  parties, correctly given separate EP records), Bulgaria *GERB-SDS*, Romania *Save
  Romania Union (USR) / [...] (PLUS) alliance*.
- **Resolved to one candidate, excluding a false positive**: Italy *Lega Nord* (EP's "L"
  cluster conflates the genuine 2019+ post-rebrand "Lega" with an unrelated 1950s
  "Liberale" party under the same label — merged with only the date-verified post-rebrand
  ids), Poland *Democratic Left Alliance* (EP's altLabel spuriously cross-labels the
  unrelated 2001–2005 "SLD-UP" joint list as "SLD" in every language; ground truth's
  party_name and the real SLD cluster's own `prefLabel` disambiguate), Spain *Citizens*
  (EP's "DCE" / "Delegación Ciudadanos Europeos" only coincidentally shares the word
  "ciudadanos" with the Ciudadanos party).
- **Left genuinely ambiguous, not forced** (2 cases): Belgium *DéFI* (its EP-tracked
  predecessor "FDF" only appears as part of two different historical joint-list labels,
  "PRL/FDF" and "FDF-RW", with no clean single-party record to point to), Hungary
  *Fidesz* (EP's older "FIDESZ-MPSZ" pre-dates its now-permanent joint list with the
  separately-tracked KDNP under "Fidesz-KDNP"; picking one risks either dropping real
  history or double-counting KDNP's own ground-truth entry).

## Results

| Outcome | Count | Share |
|---|---|---|
| Matched (`exact_alias`, `token_containment`, either found directly or via the Wikidata enrichment pass, or manually resolved) | 230 | 79.3% |
| No match found | 58 | 20.0% |
| Ambiguous (left unresolved) | 2 | 0.7% |

Improved in two stages from the first version's 52.2% matched / 45.4% no-match / 2.4%
ambiguous (out of 293 unique parties at the time):
1. Adding the `abbreviation` field as a match alias (70.3% matched) — EP's `label` is
   almost always a native-language abbreviation (e.g. "SPÖ", "GRÜNE"), not an English
   full name, so matching against `party_name` alone missed most non-English-speaking
   countries' major parties entirely (e.g. all of Austria's ÖVP/SPÖ/FPÖ/Greens were
   `no_match` in the first version).
2. Adding the Wikidata native-language enrichment pass (79.3% matched) — closed cases
   where ground truth's own `abbreviation` field also turned out to be an English
   translation rather than the party's native one (e.g. Denmark's "Danish People's
   Party" only matched once Wikidata supplied the Danish label "Dansk Folkeparti",
   matching EP's `prefLabel` for it — EP's own `label` for this party is the ballot
   letter "O", not "DF").

**Verification level:** the matching logic only accepts exact or unambiguous-token
matches (no fuzzy guessing) for the bulk of matches, plus the 17 individually-verified
manual resolutions documented above (each checked against EP's `prefLabel`, `temporal`
date ranges, and/or ground truth's `wikidata_id`/Wikipedia). The 21 Wikidata-enrichment
matches were spot-checked in the same way the false Poland/PO match was caught (see
Methodology) — 2 further spot-checks (Denmark's DF, Lithuania's LSDDP→LRP) confirmed
correct. Not manually spot-checked beyond that for the remaining ~190 direct-alias
automatic matches. Known limitation categories among the 58 remaining "no match": parties
that genuinely never had MEP representation (correctly absent from EP data — mostly
small/new/regional parties on inspection of the current list), and a residual few whose
ground-truth name, abbreviation, and every official-language Wikidata label/alias all
still fail to overlap with EP's label.

**Recommendation:** if these numbers are going into the paper as a headline result (not
just a supporting/exploratory measurement), spot-check a larger random sample of the
automatic matches before finalizing — the current level of verification is adequate for
an exploratory measurement, not for a claim treated with the same rigor as the primary
ground truth dataset.
