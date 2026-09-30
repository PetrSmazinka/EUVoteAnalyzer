"""
Build the EP entity-linking ground truth (test/ground_truth_ep_linking/).

Independent reconciliation source: maps each unique (country, party_name) from
test/ground_truth_extraction/ to European Parliament Open Data corporate-bodies
records (classification=NATIONAL_POLITICAL_GROUP), read entirely from the local
cache (no network calls).

Key fix over the first version of this script: EP's `label` field is the party's
native-language ABBREVIATION (e.g. "SPÖ"), not an English full name, and its
`prefLabel` is the native-language FULL name (e.g. "Sozialdemokratische Partei
Österreichs") -- matching against ground truth's English `party_name` alone
mostly failed on this translation gap. Ground truth now carries a separate
`abbreviation` field (native-language, added after this dataset was first
built); matching that against EP's `label` closes most of the gap without any
fuzzy/similarity-score matching (deliberately not used -- see README).
"""
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GT_DIR = ROOT / "EUVoteAnalyzer" / "test" / "ground_truth_extraction"
EP_LIST = ROOT / "cache/data.europarl.europa.eu/api/v2/corporate-bodies--body-classification=NATIONAL_POLITICAL_GROUP.json"
EP_DETAIL_DIR = ROOT / "cache/data.europarl.europa.eu/api/v2/corporate-bodies"
OUT_DIR = ROOT / "EUVoteAnalyzer" / "test" / "ground_truth_ep_linking"
WIKIDATA_CACHE = ROOT / "cache/www.wikidata.org/labels_aliases_batch.json"

# The 24 official EU languages -- EP's own labels are always in one of these, so
# restricting Wikidata's label/alias languages to this set avoids noise from
# Wikipedia-only regional/dialect language codes (e.g. Venetian "vec", Silesian "szl",
# Low German "nds"), which caused a confirmed false match: an Italian Wikidata alias
# for "National Movement" (Polish RN) rendered in Venetian as "Movimento Nasionałe
# (Połònia)" lost its "ł" to diacritic-stripping and produced a spurious "po" token,
# which then matched Poland's unrelated "PO" (Platforma Obywatelska/Civic Platform).
EU_OFFICIAL_LANGUAGES = {
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "ga",
    "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv",
}

ISO3_TO_COUNTRY = {
    "AUT": "Austria", "BEL": "Belgium", "BGR": "Bulgaria", "HRV": "Croatia",
    "CYP": "Cyprus", "CZE": "Czechia", "DNK": "Denmark", "EST": "Estonia",
    "FIN": "Finland", "FRA": "France", "DEU": "Germany", "GRC": "Greece",
    "HUN": "Hungary", "IRL": "Ireland", "ITA": "Italy", "LVA": "Latvia",
    "LTU": "Lithuania", "LUX": "Luxembourg", "MLT": "Malta", "NLD": "Netherlands",
    "POL": "Poland", "PRT": "Portugal", "ROU": "Romania", "SVK": "Slovakia",
    "SVN": "Slovenia", "ESP": "Spain", "SWE": "Sweden",
}


def normalize(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_wikidata_aliases() -> dict[str, set[str]]:
    """QID -> set of normalized strings from every language's label and alias.

    Supplementary alias source: EP's corporate-bodies records are predominantly
    native-language (label = native abbreviation, prefLabel = native full name), while
    ground truth's party_name is English and abbreviation is sometimes English too
    (e.g. Wikipedia infobox short-form). Wikidata carries the genuine native-language
    name/aliases for almost every party, closing that gap -- e.g. Denmark's "Danish
    People's Party" only matches EP's prefLabel "Dansk Folkeparti" via Wikidata's
    Danish-language label, not via the English party_name or abbreviation "DF" (EP's
    own `label` for it is the ballot letter "O", not "DF").
    Kept as a separate, deliberately over-inclusive (all languages) source rather than
    picking "the" native language, since matching is still gated by exact-string or
    unique-token-containment against a country-scoped candidate list, so accidental
    cross-language collisions are not a realistic risk.
    """
    if not WIKIDATA_CACHE.exists():
        return {}
    raw = json.loads(WIKIDATA_CACHE.read_text(encoding="utf-8"))
    out = {}
    for qid, ent in raw.items():
        strings = set()
        for lang, lang_entry in ent.get("labels", {}).items():
            if lang in EU_OFFICIAL_LANGUAGES:
                strings.add(lang_entry["value"])
        for lang, alias_list in ent.get("aliases", {}).items():
            if lang in EU_OFFICIAL_LANGUAGES:
                for a in alias_list:
                    strings.add(a["value"])
        out[qid] = {normalize(s) for s in strings if normalize(s)}
    return out


def gt_aliases(party_name: str, abbreviation: str | None, wikidata_aliases: set[str] | None = None) -> set[str]:
    aliases = set()
    full = normalize(party_name)
    if full:
        aliases.add(full)
    paren = re.findall(r"\(([^)]+)\)", party_name)
    for p in paren:
        n = normalize(p)
        if n:
            aliases.add(n)
    outside_paren = re.sub(r"\([^)]*\)", "", party_name)
    n = normalize(outside_paren)
    if n:
        aliases.add(n)
    if abbreviation:
        n = normalize(abbreviation)
        if n:
            aliases.add(n)
    if wikidata_aliases:
        aliases |= wikidata_aliases
    return {a for a in aliases if a}


def load_ep_records():
    """Returns {country_name: [{"ids": [...], "label": str, "prefLabel": str, "aliases": set}]}"""
    listing = json.loads(EP_LIST.read_text(encoding="utf-8"))
    by_country_label = defaultdict(lambda: defaultdict(lambda: {"ids": [], "label": None, "prefLabels": set(), "altLabels": set()}))

    for entry in listing:
        ident = entry["identifier"]
        detail_path = EP_DETAIL_DIR / f"{ident}.json"
        detail = json.loads(detail_path.read_text(encoding="utf-8"))
        if not detail:
            continue
        rec = detail[0]
        represents = rec.get("represents") or []
        countries = set()
        for r in represents:
            iso3 = r.rsplit("/", 1)[-1]
            if iso3 in ISO3_TO_COUNTRY:
                countries.add(ISO3_TO_COUNTRY[iso3])
        if not countries:
            continue

        label = rec.get("label", "")
        label_norm = normalize(label)
        pref_labels = set(rec.get("prefLabel", {}).values())
        alt_labels = set(rec.get("altLabel", {}).values())

        for country in countries:
            group = by_country_label[country][label_norm]
            group["ids"].append(ident)
            group["label"] = label
            group["prefLabels"] |= pref_labels
            group["altLabels"] |= alt_labels

    result = {}
    for country, groups in by_country_label.items():
        out = []
        for label_norm, g in groups.items():
            aliases = {label_norm} if label_norm else set()
            for pl in g["prefLabels"]:
                n = normalize(pl)
                if n:
                    aliases.add(n)
            for al in g["altLabels"]:
                n = normalize(al)
                if n:
                    aliases.add(n)
            out.append({
                "ids": sorted(g["ids"], key=int),
                "label": g["label"],
                "pref_label": next(iter(g["prefLabels"]), None),
                "aliases": aliases,
            })
        result[country] = out
    return result


# Manually verified resolutions for cases the automatic tiers correctly flagged as
# ambiguous (multiple exact-alias or token-containment candidates) but where individual
# inspection of EP's prefLabel/altLabel/date ranges resolves the real-world identity.
# Each entry documents the evidence; this is deliberately a small, hand-reviewed
# override list, not a fuzzy-matching fallback -- consistent with the "no guessing"
# policy in the README.
MANUAL_RESOLUTIONS = {
    # Same real party, EP split its record across a name change; ground truth's own
    # wikidata_id (Q939354) is identical for both the 2019 and 2024 Belgium records,
    # confirming continuity. Official rename sp.a -> Vooruit was 2021.
    ("Belgium", "Vooruit"): {"merge_labels": ["SP.A", "Vooruit"]},

    # Ground truth's abbreviation is the combined "CDU/CSU" bloc used by Wikipedia's
    # infobox convention for German federal elections (seats reported as one combined
    # row) -- CDU and CSU are legally distinct sister parties, so the correct EP-side
    # representation is the union of both parties' ids, not a choice between them.
    ("Germany", "Christian Democratic Union"): {"merge_labels": ["CDU", "CSU"]},

    # Denmark's Venstre: EP used the bare label "Venstre" (id 922) for a single term and
    # "V" for every other term of the same continuously-existing party (prefLabel
    # identical: "Venstre, Danmarks Liberale Parti" / "Venstre").
    ("Denmark", "Venstre"): {"merge_labels": ["V", "Venstre"]},

    # Irish Labour Party: EP prefLabel is "Labour Party" under both the older "Lab."
    # label and the current "LABOUR" label -- same party, EP just reformatted its
    # label convention over time.
    ("Ireland", "Labour Party"): {"merge_labels": ["Lab.", "LABOUR"]},

    # Forza Italia: identical prefLabel ("Forza Italia") under both the older "Forza
    # Italia" label and the newer "FI" label -- same party across eras.
    ("Italy", "Forza Italia"): {"merge_labels": ["FI", "Forza Italia"]},

    # Lega Nord / Lega: EP's "LN" cluster (1994-2019, prefLabel "Lega Nord [...]") is
    # the pre-2018-rebrand party. EP's "L" cluster is contaminated: it also contains an
    # unrelated 1950s "Liberale" body (id 927, active 1954-1957, decades before Lega
    # Nord existed) alongside the genuine post-rebrand "Lega" terms (ids 5576, 6870,
    # both dated 2019 onward, prefLabel "Lega"). Merge LN with only the verified
    # post-rebrand "Lega" ids, explicitly excluding the unrelated 1950s record.
    ("Italy", "Lega Nord"): {"merge_ids": ["662", "1461", "2767", "4082", "5267", "5576", "6870"]},

    # Poland's SLD: EP's "SLD-UP" body (the 2001-2005 joint list with Labour Union) also
    # carries "SLD" as an altLabel in every language, which spuriously matches ground
    # truth's abbreviation "SLD". Ground truth's party_name ("Democratic Left Alliance",
    # no Labour Union mention) and the separate "SLD" cluster's own prefLabel
    # ("Sojusz Lewicy Demokratycznej", no "Unia Pracy") confirm the plain SLD cluster is
    # the correct match; the joint list is a different, unrelated GT-year configuration.
    ("Poland", "Democratic Left Alliance"): {"merge_labels": ["SLD"]},

    # Bulgaria's GERB-SDS ground truth row explicitly represents the GERB+SDS electoral
    # alliance -- both are separately EP-tracked parties, so the correct representation
    # is the union of both.
    ("Bulgaria", "GERB-SDS"): {"merge_labels": ["GERB", "SDS"]},

    # Romania's USR/PLUS alliance row: EP tracks the joint "USR-PLUS" list plus each
    # constituent party's own standalone-era record. The GT row represents the alliance
    # as a whole, so all three are included (expected to overlap with USR's and PLUS's
    # own separate GT-entry ep_ids in other election years -- not a bug).
    ("Romania", "Save Romania Union (USR) / Liberty, Unity and Solidarity Party (PLUS) alliance"): {
        "merge_labels": ["USR-PLUS", "PLUS", "USR"]
    },

    # Spain's Citizens (Ciudadanos): "DCE" ("Delegación Ciudadanos Europeos" / "European
    # Citizens' Delegation") is an unrelated body that only coincidentally shares the
    # word "ciudadanos" -- not the Ciudadanos party. Resolve to "CS" alone.
    ("Spain", "Citizens"): {"merge_labels": ["CS"]},

    # --- Added after enriching matching with Wikidata native-language aliases ---

    # Denmark's Det Radikale Venstre: ground truth uses two different English
    # translations across election years ("Danish Social Liberal Party" and "Social
    # Liberals") for the same continuous party; EP's "B" (current ballot letter) and
    # "R.V." (older label) both refer to it.
    ("Denmark", "Danish Social Liberal Party"): {"merge_labels": ["B", "R.V."]},
    ("Denmark", "Social Liberals"): {"merge_labels": ["B", "R.V."]},

    # Estonian Centre Party (Eesti Keskerakond): EP's "KE" and "K" are the same party's
    # label across different terms (identical prefLabel "Eesti Keskerakond" for both).
    ("Estonia", "Estonian Centre Party"): {"merge_labels": ["KE", "K"]},

    # Brothers of Italy (Fratelli d'Italia): founded 2012, ran 2014-2017 under the name
    # "Fratelli d'Italia - Alleanza Nazionale" before reverting to the plain name in
    # 2017 (confirmed via Wikipedia) -- all three EP labels are the same continuous
    # party across its own name history.
    ("Italy", "Brothers of Italy"): {"merge_labels": ["Fratelli d'Italia - AN", "FdI", "Fratelli d'Italia"]},

    # Luxembourg's déi gréng formed from the 1995 merger of two 1985-split predecessor
    # parties, Gréng Lëscht Ekologesch Initiativ (GLEI) and Gréng Alternativ Partei
    # (GAP), which ran a joint list from 1994 (confirmed via Wikipedia) -- same
    # continuous green movement across the merger.
    ("Luxembourg", "The Greens"): {"merge_labels": ["Déi Gréng", "GLEI/GAP"]},
}


def match(gt_alias_set: set[str], ep_groups: list[dict]):
    # Tier 1: exact alias match
    exact_candidates = [g for g in ep_groups if gt_alias_set & g["aliases"]]
    if len(exact_candidates) == 1:
        return exact_candidates[0], "exact_alias"
    if len(exact_candidates) > 1:
        return exact_candidates, "ambiguous"

    # Tier 2: single-word alias contained as a whole word in the other side's string(s)
    single_word_gt = {a for a in gt_alias_set if " " not in a and len(a) >= 2}
    token_candidates = set()
    for g in ep_groups:
        ep_strings = g["aliases"]
        for a in single_word_gt:
            for es in ep_strings:
                if re.search(rf"\b{re.escape(a)}\b", es):
                    token_candidates.add(id(g))
        for es_word in {w for s in ep_strings if " " not in s and len(s) >= 2 for w in [s]}:
            for gt_full in gt_alias_set:
                if re.search(rf"\b{re.escape(es_word)}\b", gt_full):
                    token_candidates.add(id(g))
    matched_groups = [g for g in ep_groups if id(g) in token_candidates]
    if len(matched_groups) == 1:
        return matched_groups[0], "token_containment"
    if len(matched_groups) > 1:
        return matched_groups, "ambiguous"

    return None, "no_match"


def main():
    ep_by_country = load_ep_records()
    wikidata_aliases = load_wikidata_aliases()

    unique_parties = {}  # (country, party_name) -> abbreviation, wikidata_id
    for gt_file in sorted(GT_DIR.glob("*.json")):
        if gt_file.name in ("ALL.json",):
            continue
        d = json.loads(gt_file.read_text(encoding="utf-8"))
        country = d["country"]
        for p in d["parties"]:
            key = (country, p["party_name"])
            if key not in unique_parties:
                unique_parties[key] = {
                    "abbreviation": p.get("abbreviation"),
                    "wikidata_id": p.get("wikidata_id"),
                }

    by_country = defaultdict(list)
    stats = defaultdict(int)
    for (country, party_name), meta in sorted(unique_parties.items()):
        ep_groups = ep_by_country.get(country, [])
        base_aliases = gt_aliases(party_name, meta["abbreviation"])
        result, method = match(base_aliases, ep_groups)

        via_wikidata = False
        if method in ("no_match", "ambiguous") and meta["wikidata_id"] in wikidata_aliases:
            enriched_aliases = gt_aliases(party_name, meta["abbreviation"], wikidata_aliases[meta["wikidata_id"]])
            enriched_result, enriched_method = match(enriched_aliases, ep_groups)
            if enriched_method in ("exact_alias", "token_containment"):
                result, method, via_wikidata = enriched_result, enriched_method, True
            elif enriched_method == "ambiguous" and method == "no_match":
                result, method = enriched_result, enriched_method

        override = MANUAL_RESOLUTIONS.get((country, party_name))
        if override is not None:
            if "merge_labels" in override:
                merged = [g for g in ep_groups if g["label"] in override["merge_labels"]]
                merged_ids = sorted({i for g in merged for i in g["ids"]}, key=int)
                merged_label = "+".join(sorted({g["label"] for g in merged}))
            else:
                merged_ids = sorted(override["merge_ids"], key=int)
                merged_label = "+".join(
                    sorted({g["label"] for g in ep_groups if set(g["ids"]) & set(merged_ids)})
                )
            method = "manual_alliance_merge" if len(override.get("merge_labels", override.get("merge_ids", []))) > 1 else "manual_resolved"
            entry = {
                "party_name": party_name,
                "wikidata_id": meta["wikidata_id"],
                "ep_ids": merged_ids,
                "ep_matched_label": merged_label,
                "match_method": method,
            }
            stats[method] += 1
            by_country[country].append(entry)
            continue

        reported_method = f"{method}_via_wikidata_alias" if via_wikidata else method

        if method == "ambiguous":
            entry = {
                "party_name": party_name,
                "wikidata_id": meta["wikidata_id"],
                "ep_ids": [],
                "ep_matched_label": None,
                "match_method": reported_method,
                "ambiguous_candidates": [g["label"] for g in result],
            }
        elif result is None:
            entry = {
                "party_name": party_name,
                "wikidata_id": meta["wikidata_id"],
                "ep_ids": [],
                "ep_matched_label": None,
                "match_method": "no_match",
            }
        else:
            entry = {
                "party_name": party_name,
                "wikidata_id": meta["wikidata_id"],
                "ep_ids": result["ids"],
                "ep_matched_label": result["label"],
                "match_method": reported_method,
            }
        stats[reported_method] += 1
        by_country[country].append(entry)

    for country, parties in by_country.items():
        out_path = OUT_DIR / f"{country}.json"
        out_path.write_text(
            json.dumps({"country": country, "parties": parties}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    total = sum(stats.values())
    print(f"Total unique parties: {total}")
    for method, count in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {method}: {count} ({count/total*100:.1f}%)")


if __name__ == "__main__":
    main()
