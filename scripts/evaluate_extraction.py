"""
Evaluate the NationalFetch extraction pipeline against manually verified ground truth.

Loads test/ground_truth_extraction/*.json (skips any file where `parties` has not yet
been filled in by a human) and the matching logs/extraction_eval/*.json produced by
`python main.py --eval-log`, pairs parties by normalized name (not position), and
computes per-phase metrics both overall and per-country.

Usage:
    python scripts/evaluate_extraction.py
    python scripts/evaluate_extraction.py --eval-log-dir logs/extraction_eval_ablation_markdown
"""

import argparse
import glob
import json
import os
import re
import sys
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from EUVoteAnalyzer.logic.fetchers import NationalFetch

DEFAULT_GT_DIR = "EUVoteAnalyzer/test/ground_truth_extraction"
DEFAULT_LOG_DIR = "logs/extraction_eval"
DEFAULT_OUT_JSON = "results/extraction_evaluation.json"
DEFAULT_OUT_MD = "results/extraction_evaluation_table.md"


def normalize_name(name: Optional[str]) -> str:
    """Lowercase, strip diacritics and parenthetical asides, for lenient party-name pairing."""
    if not name:
        return ""
    name = re.sub(r"\([^)]*\)", "", name)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def load_ground_truth(gt_dir: str) -> List[Dict[str, Any]]:
    """Load ground-truth records, skipping any whose `parties` field is still a TODO placeholder."""
    records = []
    skipped = []
    for path in sorted(glob.glob(os.path.join(gt_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            gt = json.load(f)
        if not isinstance(gt.get("parties"), list):
            skipped.append(os.path.basename(path))
            continue
        gt["_file"] = os.path.basename(path)
        records.append(gt)
    if skipped:
        print(f"Skipping {len(skipped)} not-yet-verified ground truth file(s): {', '.join(skipped)}", file=sys.stderr)
    return records


def load_eval_log(log_dir: str, country: str, year: int) -> Optional[Dict[str, Any]]:
    path = os.path.join(log_dir, f"{country}_{year}.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extract_aliases(name: Optional[str]) -> List[str]:
    """Normalized candidate keys for a party name: the full name, plus any
    parenthetical content (often an abbreviation) normalized on its own --
    e.g. "Croatian Democratic Union (HDZ)" -> ["croatian democratic union", "hdz"]."""
    if not name:
        return []
    aliases = [normalize_name(name)]
    for paren in re.findall(r"\(([^)]*)\)", name):
        norm = normalize_name(paren)
        if norm:
            aliases.append(norm)
    return [a for a in aliases if a]


def match_parties(
    gt_parties: List[Dict[str, Any]], extracted_parties: List[Dict[str, Any]]
) -> Tuple[List[Tuple[Dict[str, Any], Dict[str, Any]]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Pair ground-truth and extracted parties, not by position. Three tiers, tried in
    order for each ground-truth party:

      1. Exact match on the full normalized name.
      2. Exact match against a parenthetical alias, or ground truth's `abbreviation`
         field, on either side (tier 1 and 2 share one alias-exact lookup below) -- e.g.
         ground truth "Croatian Democratic Union" / abbreviation "HDZ" against extracted
         "HDZ".
      3. Token-containment fallback: a single-word extracted or ground-truth name that
         appears as a whole word inside the other side's full normalized name (e.g.
         extracted "Reform" inside ground truth "Estonian Reform Party") -- applied only
         when exactly one remaining candidate qualifies, so an ambiguous short token
         (matching more than one remaining party) is left unmatched rather than guessed.

    `abbreviation` is compared as plain text, on equal footing with the name-derived
    aliases already used here -- no fuzzy/similarity scoring, no external lookups. This
    only changes which of the *LLM's own extracted strings* get recognized as correct;
    it does not give the scorer (or the pipeline) access to anything beyond what each
    ground-truth record already states. Deliberately excludes anything resembling the
    entity-linking heuristics used to build `test/ground_truth_ep_linking/` (multilingual
    alias lookups, manual rename/alliance overrides) -- see that dataset's README for why
    those must stay out of any code path this evaluation also measures.

    Known residual gap: abbreviations that aren't derivable from the ground-truth
    party_name or abbreviation field at all are not resolvable by either tier and remain
    unmatched -- this is a real limitation, not a bug, and should be reported as such
    rather than patched with a lookup table.
    """
    alias_index: Dict[str, List[Dict[str, Any]]] = {}
    for p in extracted_parties:
        for alias in extract_aliases(p.get("party_name")):
            alias_index.setdefault(alias, []).append(p)

    ext_full_tokens = [(p, set(normalize_name(p.get("party_name")).split())) for p in extracted_parties]

    def find_match(gtp: Dict[str, Any], used_ids: set) -> Optional[Dict[str, Any]]:
        gt_aliases = extract_aliases(gtp.get("party_name"))
        abbrev_norm = normalize_name(gtp.get("abbreviation"))
        if abbrev_norm and abbrev_norm not in gt_aliases:
            gt_aliases.append(abbrev_norm)
        for alias in gt_aliases:
            cand = next((c for c in alias_index.get(alias, []) if id(c) not in used_ids), None)
            if cand is not None:
                return cand

        gt_tokens = set(normalize_name(gtp.get("party_name")).split()) | set(abbrev_norm.split())
        candidates: Dict[int, Dict[str, Any]] = {}
        for alias in gt_aliases:
            if " " in alias or len(alias) < 3:
                continue
            for p, tokens in ext_full_tokens:
                if id(p) not in used_ids and alias in tokens:
                    candidates[id(p)] = p
        for p in extracted_parties:
            if id(p) in used_ids:
                continue
            ext_norm = normalize_name(p.get("party_name"))
            if ext_norm and " " not in ext_norm and len(ext_norm) >= 3 and ext_norm in gt_tokens:
                candidates[id(p)] = p
        if len(candidates) == 1:
            return next(iter(candidates.values()))
        return None

    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    unmatched_gt: List[Dict[str, Any]] = []
    used_ids: set = set()
    for gtp in gt_parties:
        cand = find_match(gtp, used_ids)
        if cand is not None:
            used_ids.add(id(cand))
            pairs.append((gtp, cand))
        else:
            unmatched_gt.append(gtp)
    unmatched_extracted = [p for p in extracted_parties if id(p) not in used_ids]
    return pairs, unmatched_gt, unmatched_extracted


def find_phase4_attempt(attempts: List[Dict[str, Any]], extracted_party: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for a in attempts:
        if a.get("url") and a.get("url") == extracted_party.get("url"):
            return a
        if normalize_name(a.get("party_name")) == normalize_name(extracted_party.get("party_name")):
            return a
    return None


def evaluate_record(gt: Dict[str, Any], log: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    country, year = gt["country"], gt["election_year"]
    result: Dict[str, Any] = {"country": country, "election_year": year}

    if log is None:
        result["error"] = "no matching eval log found -- run `python main.py --eval-log` first"
        return result

    p1 = log.get("phase_1_source_acquisition") or {}
    result["phase_1"] = {
        "success": bool(p1.get("success")),
        "matches_ground_truth": p1.get("matches_ground_truth"),
        "used_ground_truth_fallback": p1.get("used_ground_truth_fallback"),
    }

    p2 = log.get("phase_2_content_reduction")
    result["phase_2"] = {"compression_ratio": p2.get("compression_ratio") if p2 else None}

    p3 = log.get("phase_3_llm_extraction")
    extracted_parties = (p3 or {}).get("parsed_parties") or []
    result["phase_3"] = {"schema_valid": bool((p3 or {}).get("schema_valid"))}

    gt_parties = gt.get("parties") or []
    pairs, unmatched_gt, unmatched_extracted = match_parties(gt_parties, extracted_parties)

    existence_tp = len(pairs)
    existence_fp = len(unmatched_extracted)
    existence_fn = len(unmatched_gt)

    seats_correct = 0
    government_correct = 0
    government_evaluable = 0
    strict_tp = 0

    p4_attempts = ((log.get("phase_4_entity_linking") or {}).get("attempts")) or []
    linking_methods: List[str] = []
    linking_attempted = 0

    for gtp, exp in pairs:
        seats_ok = gtp.get("seats") == exp.get("seats")
        gov_gt = gtp.get("in_government")
        gov_ok = None
        if gov_gt is not None and exp.get("in_government") is not None:
            government_evaluable += 1
            gov_ok = bool(gov_gt) == bool(exp.get("in_government"))
            if gov_ok:
                government_correct += 1
        if seats_ok:
            seats_correct += 1
        if seats_ok and (gov_ok is not False):
            strict_tp += 1

        if gtp.get("wikidata_id"):
            linking_attempted += 1
            attempt = find_phase4_attempt(p4_attempts, exp)
            if attempt is not None:
                pool_wiki = {str(gtp["wikidata_id"]): 1}
                pool_name = {}
                if gtp.get("party_name"):
                    pool_name[str(gtp["party_name"]).lower()] = 1
                if gtp.get("abbreviation"):
                    pool_name[str(gtp["abbreviation"]).lower()] = 1
                _, method = NationalFetch._match_party_entity(
                    attempt.get("scraped_wiki_id"),
                    attempt.get("scraped_full_name"),
                    attempt.get("scraped_abbreviation"),
                    pool_wiki,
                    pool_name,
                )
                linking_methods.append(method or "unmatched")

    def rate(n: int, d: int) -> Optional[float]:
        return (n / d) if d else None

    def f1_score(precision: Optional[float], recall: Optional[float]) -> Optional[float]:
        """Harmonic mean of precision and recall.

        `precision`/`recall` may legitimately be exactly 0.0 (not missing) --
        an `if precision and recall` truthy check would treat that 0.0 the
        same as None and silently drop the record from any F1 average built
        from this, while precision/recall themselves stay in their own
        average. That asymmetry is exactly what produced an aggregate F1 that
        didn't match 2PR/(P+R) computed from the reported aggregate P and R.
        """
        if precision is None or recall is None:
            return None
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    precision = rate(existence_tp, existence_tp + existence_fp)
    recall = rate(existence_tp, existence_tp + existence_fn)
    f1 = f1_score(precision, recall)

    strict_precision = rate(strict_tp, existence_tp + existence_fp)
    strict_recall = rate(strict_tp, existence_tp + existence_fn)
    strict_f1 = f1_score(strict_precision, strict_recall)

    result["extraction"] = {
        "existence_precision": precision,
        "existence_recall": recall,
        "existence_f1": f1,
        "strict_precision": strict_precision,
        "strict_recall": strict_recall,
        "strict_f1": strict_f1,
        "seat_accuracy": rate(seats_correct, existence_tp),
        "government_status_accuracy": rate(government_correct, government_evaluable),
        "n_matched": existence_tp,
        "n_strict_matched": strict_tp,
        "n_unmatched_ground_truth": existence_fn,
        "n_unmatched_extracted": existence_fp,
        "unmatched_ground_truth_parties": [p.get("party_name") for p in unmatched_gt],
        "unmatched_extracted_parties": [p.get("party_name") for p in unmatched_extracted],
    }

    result["entity_linking"] = {
        "attempted": linking_attempted,
        "matched": sum(1 for m in linking_methods if m != "unmatched"),
        "via_wiki_id": linking_methods.count("wiki_id"),
        "via_full_name": linking_methods.count("full_name"),
        "via_abbreviation": linking_methods.count("abbreviation"),
        "success_rate": rate(sum(1 for m in linking_methods if m != "unmatched"), linking_attempted),
    }

    return result


def aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate per-record metrics.

    Precision/recall/F1 (existence and strict) are **micro-averaged**: TP/FP/FN are
    summed across every record first, then precision, recall, and F1 are each computed
    once from those sums. This guarantees F1 == 2PR/(P+R) for the P and R in the same
    report row by construction -- a macro-average (averaging each record's own P, R, F1)
    can't guarantee that, and previously didn't: a record with zero correct matches has a
    well-defined precision/recall of 0.0, but a now-fixed bug (see f1_score in
    evaluate_record) was silently excluding exactly those records from the F1 average
    while still counting their 0.0 in the P/R averages, inflating the reported F1 above
    what its own P and R implied.

    Metrics that are single ratios per record (seat accuracy, government-status accuracy,
    entity linking success rate, compression ratio) don't have a P-vs-R-vs-F1 consistency
    constraint to satisfy, so they stay macro-averaged (mean of each applicable record's
    own ratio, skipping records where that ratio is undefined -- e.g. no matched parties
    to check seat counts on).
    """
    def avg(values: List[Optional[float]]) -> Optional[float]:
        vals = [v for v in values if v is not None]
        return sum(vals) / len(vals) if vals else None

    def micro_prf(tp: int, fp: int, fn: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        precision = (tp / (tp + fp)) if (tp + fp) else None
        recall = (tp / (tp + fn)) if (tp + fn) else None
        if precision is None or recall is None:
            f1 = None
        elif precision + recall == 0:
            f1 = 0.0
        else:
            f1 = 2 * precision * recall / (precision + recall)
        return precision, recall, f1

    valid = [r for r in records if "error" not in r]

    fp_sum = sum(r["extraction"]["n_unmatched_extracted"] for r in valid)
    fn_sum = sum(r["extraction"]["n_unmatched_ground_truth"] for r in valid)
    existence_tp_sum = sum(r["extraction"]["n_matched"] for r in valid)
    strict_tp_sum = sum(r["extraction"]["n_strict_matched"] for r in valid)

    existence_precision, existence_recall, existence_f1 = micro_prf(existence_tp_sum, fp_sum, fn_sum)
    strict_precision, strict_recall, strict_f1 = micro_prf(strict_tp_sum, fp_sum, fn_sum)

    return {
        "n_records": len(records),
        "n_evaluable": len(valid),
        "source_acquisition_success_rate": avg([r["phase_1"]["success"] for r in valid]),
        "source_acquisition_discovery_match_rate": avg(
            [r["phase_1"]["matches_ground_truth"] for r in valid if r["phase_1"]["matches_ground_truth"] is not None]
        ),
        "avg_compression_ratio": avg([r["phase_2"]["compression_ratio"] for r in valid]),
        "existence_precision": existence_precision,
        "existence_recall": existence_recall,
        "existence_f1": existence_f1,
        "strict_precision": strict_precision,
        "strict_recall": strict_recall,
        "strict_f1": strict_f1,
        "seat_accuracy": avg([r["extraction"]["seat_accuracy"] for r in valid]),
        "government_status_accuracy": avg([r["extraction"]["government_status_accuracy"] for r in valid]),
        "entity_linking_success_rate": avg([r["entity_linking"]["success_rate"] for r in valid]),
        "entity_linking_via_wiki_id": sum(r["entity_linking"]["via_wiki_id"] for r in valid),
        "entity_linking_via_full_name": sum(r["entity_linking"]["via_full_name"] for r in valid),
        "entity_linking_via_abbreviation": sum(r["entity_linking"]["via_abbreviation"] for r in valid),
    }


def fmt_pct(v: Optional[float]) -> str:
    return f"{v * 100:.1f}%" if v is not None else "N/A"


def write_markdown(path: str, overall: Dict[str, Any], by_country: Dict[str, Dict[str, Any]], records: List[Dict[str, Any]]) -> None:
    lines = ["# Extraction evaluation results\n"]
    lines.append(f"n = {overall['n_evaluable']} of {overall['n_records']} ground-truth records evaluated.\n")
    lines.append("## Overall\n")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Source acquisition success rate | {fmt_pct(overall['source_acquisition_success_rate'])} |")
    lines.append(f"| Source acquisition discovery-match rate | {fmt_pct(overall['source_acquisition_discovery_match_rate'])} |")
    lines.append(f"| Extraction precision (existence) | {fmt_pct(overall['existence_precision'])} |")
    lines.append(f"| Extraction recall (existence) | {fmt_pct(overall['existence_recall'])} |")
    lines.append(f"| Extraction F1 (existence) | {fmt_pct(overall['existence_f1'])} |")
    lines.append(f"| Extraction precision (strict: existence+seats+government) | {fmt_pct(overall['strict_precision'])} |")
    lines.append(f"| Extraction recall (strict: existence+seats+government) | {fmt_pct(overall['strict_recall'])} |")
    lines.append(f"| Extraction F1 (strict: existence+seats+government) | {fmt_pct(overall['strict_f1'])} |")
    lines.append(f"| Seat-count accuracy (of matched parties) | {fmt_pct(overall['seat_accuracy'])} |")
    lines.append(f"| Government-status accuracy (of matched parties) | {fmt_pct(overall['government_status_accuracy'])} |")
    lines.append(f"| Entity linking success rate | {fmt_pct(overall['entity_linking_success_rate'])} |")
    lines.append(
        f"| Entity linking method breakdown | wiki_id={overall['entity_linking_via_wiki_id']}, "
        f"full_name={overall['entity_linking_via_full_name']}, abbreviation={overall['entity_linking_via_abbreviation']} |"
    )
    lines.append(f"| Avg. compression ratio (phase 2, informative) | {fmt_pct(overall['avg_compression_ratio'])} |")

    lines.append("\n## Per country\n")
    lines.append("| Country | Existence F1 | Strict F1 | Seat acc. | Gov. status acc. | Entity linking |")
    lines.append("|---|---|---|---|---|---|")
    for country, agg in sorted(by_country.items()):
        lines.append(
            f"| {country} | {fmt_pct(agg['existence_f1'])} | {fmt_pct(agg['strict_f1'])} | "
            f"{fmt_pct(agg['seat_accuracy'])} | {fmt_pct(agg['government_status_accuracy'])} | "
            f"{fmt_pct(agg['entity_linking_success_rate'])} |"
        )

    lines.append("\n## Per-record detail\n")
    for r in records:
        lines.append(f"### {r['country']} {r['election_year']}\n")
        if "error" in r:
            lines.append(f"_{r['error']}_\n")
            continue
        ex = r["extraction"]
        if ex["unmatched_ground_truth_parties"]:
            lines.append(f"- Missed by extraction: {', '.join(ex['unmatched_ground_truth_parties'])}")
        if ex["unmatched_extracted_parties"]:
            lines.append(f"- Extracted but not in ground truth: {', '.join(ex['unmatched_extracted_parties'])}")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth-dir", default=DEFAULT_GT_DIR)
    parser.add_argument("--eval-log-dir", default=DEFAULT_LOG_DIR)
    parser.add_argument("--out-json", default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    gt_records = load_ground_truth(args.ground_truth_dir)
    if not gt_records:
        print("No verified ground-truth records found (all files still have TODO `parties`). Nothing to evaluate.", file=sys.stderr)

    per_record = []
    by_country: Dict[str, List[Dict[str, Any]]] = {}
    for gt in gt_records:
        log = load_eval_log(args.eval_log_dir, gt["country"], gt["election_year"])
        result = evaluate_record(gt, log)
        per_record.append(result)
        by_country.setdefault(gt["country"], []).append(result)

    overall = aggregate(per_record)
    by_country_agg = {c: aggregate(rs) for c, rs in by_country.items()}

    os.makedirs(os.path.dirname(args.out_json) or ".", exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump({"overall": overall, "by_country": by_country_agg, "records": per_record}, f, ensure_ascii=False, indent=2)
    write_markdown(args.out_md, overall, by_country_agg, per_record)

    print(f"Wrote {args.out_json} and {args.out_md}")
    print(f"Evaluated {overall['n_evaluable']}/{overall['n_records']} records.")


if __name__ == "__main__":
    main()
