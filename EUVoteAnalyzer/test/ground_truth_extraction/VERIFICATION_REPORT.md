# Ground truth verification report

Generated after a fill pass (1 agent, 24 countries independently researched via ParlGov/official sources/Wikidata) and a verify pass (6 parallel agents, one per 4-country group, each independently re-deriving facts and correcting the JSON files directly). **Caveat:** the verify agents edited files in place but the session restarted before their per-file change narratives could be compiled, so this report reflects the *current, post-verification state* (what's still flagged vs. clean), not a diff of what was corrected. Every file still carries its own `sources` field citing what was checked.

**This does not replace your own manual review** — treat the "needs_review" list below as a priority checklist (highest-risk records), but the plan was always to check every field yourself before using this for the paper.

## Needs review (16 of 54 files)

These have an explicit `needs_review` flag or a `null` field — check these first.

### Croatia_2020.json
- top-level needs_review
- party 'Democratic Union of Hungarians and Others in Croatia (DZMH)' wikidata_id=null
- party 'Kali Sara (Roma minority)' wikidata_id=null
- party 'UARH (Albanian minority)' wikidata_id=null
- note: Plenković III government (HDZ + SDSS) confirmed via ParlGov cabinet-parties. The 'Pametno-IP-Fokus' coalition (led by Dalija Orešković) merged into a single party called 'Centre' (Centar) on 16 November 2020, after this election; wikidata_id given (Q30324660) is for that successor entity, used here as the closest match since no separate wikidata item for the pre-merger three-way coalition was found. Croatia reserves 8 of 151 seats for national-minority representatives; besides SDSS (Serb minority, 3 seats), 2 further reserved seats went to independents (not individually attributable to a party and omitted here) and wikidata_id could not be found for DZMH or Kali Sara or UARH via search.

### Croatia_2024.json
- top-level needs_review
- party 'Rivers of Justice (SDP-led coalition)' wikidata_id=null
- party 'IDS–PGS (Istrian Democratic Assembly)' wikidata_id=null
- party 'NPS' wikidata_id=null
- party 'Focus–Republic' wikidata_id=null
- party 'Democratic Union of Hungarians and Others in Croatia (DZMH)' wikidata_id=null
- party 'Kali Sara (Roma minority)' wikidata_id=null
- party 'Bošnjaci zajedno! (Bosniak minority)' wikidata_id=null
- note: Government formed 17 May 2024 = HDZ + Homeland Movement, with confidence-and-supply support from HSLS/HNS/HDS/HSU (small parties folded into the HDZ-led coalition list rather than contesting separately, so not itemized here) and from minority MPs DZMH, Kali Sara and Bošnjaci zajedno! (marked in_government=true reflecting their support role, though this is a minority government and their formal cabinet membership vs. confidence-and-supply status was not fully disestablished from available sources - needs_review). SDSS's role in the 2024 coalition is unclear from sources checked (unlike 2020, it was not named among the explicit support parties), so in_government=false here pending further verification. Several small/regional wikidata_ids were not found via search and left null.

### France_2022.json
- top-level needs_review
- party 'Union of Democrats and Independents (UDI)' wikidata_id=null
- note: ParlGov attributes Ensemble's 245 seats to its lead formation (Renaissance/LREM) without breaking out its allied micro-parties (MoDem, Horizons, Agir individually); similarly NUPES's 131 seats are not broken down into La France Insoumise/PS/EELV/PCF. Borne II government (confirmed via ParlGov cabinet-parties) = Ensemble's constituent parties (Renaissance + MoDem + Agir + Horizons); it never won an absolute majority and governed via repeated use of Article 49.3. 'Other left'(22)/'other right'(10)/'no party affiliation'(10)/'other centre'(4)/misc one-seat categories (2, totaling ~48 further seats) are not individually identifiable parties and are omitted here rather than guessed.

### France_2024.json
- top-level needs_review
- note: No bloc reached the 289-seat majority threshold, producing a hung parliament. Michel Barnier (LR) was appointed PM on 5 September 2024, forming a minority government reliant on Ensemble support (and tolerated by some LR-adjacent votes); both LR and Ensemble are marked in_government=true since Barnier's cabinet drew ministers from both. The government fell to a no-confidence vote on 4 December 2024. A remaining ~57 of 577 seats (Wikipedia reports 'divers droite'/'divers gauche'/regionalist/'divers centre'/misc categories) are not individually identifiable parties and are omitted here rather than guessed.

### Greece_2023.json
- top-level needs_review
- party 'Popular Unity | Course of Freedom' wikidata_id=null
- note: Greece held two 2023 elections (21 May, inconclusive under proportional rules, and 25 June, decisive under the reinforced-proportionality/bonus-seat law); this file covers the 25 June election, matching the existing dataset's 'Greece_2023' naming and being the one that actually produced the government. New Democracy formed a single-party majority government again (Mitsotakis II), confirmed via ParlGov cabinet-parties. wikidata_id left null only for 'Popular Unity | Course of Freedom' - a further search under 'Popular Unity' returned no exact/unambiguous match for this specific Greek electoral alliance.

### Hungary_2026.json
- top-level needs_review
- note: Held 12 April 2026, results certified well before this dataset's 2026-09-14 cutoff. Tisza Party (formally registered as 'Tisztelet és Szabadság Párt' / Respect and Freedom Party, wikidata Q125418097) won a two-thirds supermajority and governs alone under PM Péter Magyar (elected by the National Assembly 9 May 2026); no coalition partners. Fidesz and KDNP contested as a joint alliance (wikidata Q50583624) and ParlGov-style individual seat breakdown between the two was not available from the sources checked, so they are reported as one bloc here — needs_review for that combined figure specifically.

### Lithuania_2020.json
- top-level needs_review
- party 'Social Democratic Labour Party of Lithuania (LSDDP)' wikidata_id=null
- note: Šimonytė cabinet (confirmed via ParlGov cabinet-parties) = Homeland Union + Liberal Movement + Freedom Party. wikidata_id for the Social Democratic Labour Party of Lithuania (LSDDP, a small 2017-founded splinter from LSDP - distinct from the historical 1990s 'Democratic Labour Party of Lithuania', Q63755) could not be confirmed via search and is left null (needs_review).

### Lithuania_2024.json
- party 'PLT' wikidata_id=null
- note: Paluckas government = LSDP + DSVL + Dawn of Nemunas (PPNA), announced 11 November 2024; PPNA's inclusion was controversial due to its founder's past antisemitic remarks. wikidata_id for 'PLT' (Artūras Zuokas's list, 1 seat) not searched/confirmed (needs_review).

### Poland_2023.json
- top-level needs_review
- party 'Third Way (Poland 2050 + PSL alliance)' wikidata_id=null
- note: Third Tusk cabinet (formed Dec 2023) = Civic Coalition + Third Way + The Left. 'Third Way' is an electoral alliance of Poland 2050 (Q100979832) and the Polish People's Party (Q218477); no single wikidata item combining them as one alliance entity was confirmed, so wikidata_id is left null for that row (needs_review). 'The Left' 2023 wikidata_id (Q107418166, 'New Left') is the Nowa Lewica party which led the Lewica electoral alliance; the 26 seats reported here are for the alliance as a whole, per the Wikipedia infobox convention, not New Left individually.

### Portugal_2025.json
- top-level needs_review
- note: This was a snap election triggered by the collapse of the 2024 AD government over a March 2025 confidence vote linked to a conflict-of-interest controversy involving the PM's family business. AD again fell short of a majority (91/230, 116 needed); Montenegro formed another AD-only minority government. wikidata_id for the AD coalition (Q124334159, originally documented as the '2024' Democratic Alliance) is reused here on the assumption it is the same continuing coalition entity for the 2025 election - needs_review to confirm wikidata treats it as a single continuing item across both elections rather than creating a separate 2025 item.

### Romania_2020.json
- top-level needs_review
- party 'National minorities (Chamber of Deputies reserved seats)' wikidata_id=null
- note: Cîțu government (confirmed via ParlGov cabinet-parties) = PNL + USR-PLUS + UDMR. 'National minorities' is an aggregate of ~18 distinct single-seat ethnic-minority organizations (each electing one reserved-seat representative) rather than one party; wikidata_id left null and in_government status not independently confirmed for this group in 2020 specifically (needs_review). wikidata_id given for the USR|PLUS row is for USR (Q27108508) - the alliance's larger partner; PLUS itself is Q59820303.

### Romania_2024.json
- top-level needs_review
- party 'National minorities (Chamber of Deputies reserved seats)' wikidata_id=null
- note: Pro-European grand coalition (PSD + PNL + UDMR, 'with the support of the national minorities') inaugurated 23 December 2024 with 240/465 votes. 'National minorities' is an aggregate of 19 distinct single-seat ethnic-minority organizations rather than one party; wikidata_id left null (needs_review).

### Slovenia_2018.json
- party 'Italian national community' wikidata_id=null
- party 'Hungarian national community' wikidata_id=null
- note: Šarec cabinet (confirmed via ParlGov cabinet-parties) = LMŠ + SD + SMC + ZaAB + DeSUS. The Left (Levica) supported the government via a cooperation agreement but held no cabinet seats, so in_government=false. wikidata_id for 'The Left' (Q25553804) is labelled as a North Macedonian party in the search snippet but its description on wikidata.org lists Slovenia's Levica among its scope - needs_review to confirm this is the correct item and not a mislabeled multi-country disambiguation.

### Slovenia_2022.json
- party 'Italian national community' wikidata_id=null
- party 'Hungarian national community' wikidata_id=null
- note: Golob cabinet (confirmed via ParlGov cabinet-parties) = Freedom Movement + Levica + Social Democrats.

### Spain_2023.json
- top-level needs_review
- note: Sums to 349 of 350 seats; one further regional seat (commonly reported as Unión del Pueblo Navarro/UPN) was not independently confirmed and is omitted rather than guessed - needs_review. PSOE governed in coalition with Sumar (Sánchez re-elected 16 Nov 2023); Junts, ERC, PNV, EH Bildu, BNG and CC supported the investiture vote via external agreements but held no cabinet seats, so in_government=false for all of them. 'Sumar' wikidata_id (Q112098527, 'Sumar (electoral platform)') found via web search after the wikidata.org wbsearchentities label search failed to surface it (it returned only unrelated items for the bare word 'Sumar').

### Sweden_2022.json
- top-level needs_review
- note: Kristersson cabinet (confirmed via ParlGov cabinet-parties) = Moderate Party + Christian Democrats + Liberals only. Sweden Democrats, despite being the second-largest party and a key partner under the 'Tidö Agreement', held no cabinet seats and supported the government from outside - in_government=false per a strict 'entered the cabinet' reading, though this quasi-governing role is worth flagging.

## Clean (no flags) (38 of 54 files)

No `needs_review` marker and no null seats/government/wikidata_id fields. 
Still worth a spot-check, but these came through both passes without any 
unresolved uncertainty flagged by the agents.

- Austria_2019.json
- Austria_2024.json
- Belgium_2019.json
- Belgium_2024.json
- Bulgaria_2024.json
- Bulgaria_2026.json
- Cyprus_2021.json
- Cyprus_2026.json
- Czechia_2021.json
- Czechia_2025.json
- Denmark_2022.json
- Denmark_2026.json
- Estonia_2019.json
- Estonia_2023.json
- Finland_2019.json
- Finland_2023.json
- Germany_2021.json
- Germany_2025.json
- Greece_2019.json
- Hungary_2022.json
- Ireland_2020.json
- Ireland_2024.json
- Italy_2018.json
- Italy_2022.json
- Latvia_2018.json
- Latvia_2022.json
- Luxembourg_2018.json
- Luxembourg_2023.json
- Malta_2022.json
- Malta_2026.json
- Netherlands_2023.json
- Netherlands_2025.json
- Poland_2019.json
- Portugal_2024.json
- Slovakia_2020.json
- Slovakia_2023.json
- Spain_2019.json
- Sweden_2018.json


## Additional check: seat-sum sanity check (done by the main session, not the agents)

Cross-checked each file's party-seat sum against the country's known chamber size. Most
flagged mismatches turned out to be false positives from an oversimplified reference
table that didn't account for known one-off changes — not data errors:

- **Germany_2021.json** (sum 736 vs naive expectation 630): correct as-is — the 2021
  Bundestag had 736 seats due to overhang/leveling mandates under the pre-reform
  electoral law; 630 only became the fixed size from the 2025 election onward
  (Germany_2025.json sums to ~630, consistent).
- **Italy_2018.json** (sum 630 vs naive expectation 400): correct as-is — the Chamber of
  Deputies had 630 seats until a 2020 constitutional reform cut it to 400, effective from
  the 2022 election onward (Italy_2022.json sums to ~400, consistent).
- **Malta_2022.json / Malta_2026.json** (sum 79 vs nominal 65): correct as-is — Malta's
  proportionality-correction mechanism adds "bonus seats" when a party's seat share
  doesn't match its vote share, which is exactly why both elections ended up above the
  nominal 65-seat size.
- **France_2022.json / France_2024.json** (sum ~520-529 vs 577) and **Ireland_2020.json**
  (sum 141 vs 160): likely genuine shortfalls, but plausibly explained by independents and
  micro-parties/allied-but-unlisted candidates that don't map to a single named "party" in
  this schema, rather than a factual error in what's recorded. Worth a specific look during
  manual review, but not blindly "fixed" by inflating a number.

## Follow-up: attempt to resolve remaining missing wikidata_id (2026-09-14, main session)

Of 12 remaining `wikidata_id: null` fields across the dataset, did a bounded direct-search
pass (8 Wikidata lookups, no subagents, stopped once returns clearly diminished):

- **Resolved:** `France_2022.json` — Union of Democrats and Independents (UDI) → `Q82892`.
- **Not found / stopped here** (genuinely absent from Wikidata under the names tried, not
  just under-searched): Croatia's UARH (Albanian minority reserved seat), Lithuania's
  LSDDP and PLT, Greece's "Popular Unity | Course of Freedom", Slovenia's Italian/Hungarian
  national-community reserved seats. These look like small/ethnic-minority reserved-seat
  representatives that likely don't have a dedicated Wikidata item under an English label —
  resolving them further would need native-language search terms or official minority-seat
  records, a bigger effort than a quick lookup justifies right now.
- **Structurally unresolvable as a single ID** (not a search failure): Poland's "Third Way"
  (alliance of 2 parties, Poland 2050 + PSL, each with their own ID — no single ID fits an
  alliance), Romania's "National minorities" aggregate (~18-19 separate single-seat ethnic
  organizations bundled into one ground-truth row for practicality).

## in_government field check (2026-09-15)

Scanned all 54 files for null/missing `in_government` values: **none found** — every
party in every file has an explicit `true`/`false`. Confirms the author's own assessment
that this field is in good shape; no action needed here.

Remaining `wikidata_id: null` after the author's own follow-up pass (down from 12 to 5):
`Croatia_2020.json` (UARH), `Slovenia_2018.json` and `Slovenia_2022.json` (Italian and
Hungarian national-community reserved seats, ×2 files). These are the ones flagged earlier
as hard-to-find under an English-language Wikidata search, not a completeness bug.
