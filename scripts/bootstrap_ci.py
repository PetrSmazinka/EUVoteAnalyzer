"""
Bootstrap confidence intervals over ground-truth *records* (not LLM calls -- free,
no API cost) for the extraction-evaluation metrics.

Addresses two distinct kinds of uncertainty separately:
  1. Sampling/dataset uncertainty: how much would the reported metric vary with a
     different sample of countries/elections? Answered here via a nonparametric
     bootstrap over the n evaluated records.
  2. Model run-to-run (stochastic-sampling) variance: deliberately NOT addressed by
     repeated LLM calls -- temperature=0 is used specifically as a substitute for this
     (see PAPER_HANDOFF.md sec. 6.4), so a single run per record is representative by
     construction, not a gap.

For content_mode pairs evaluated on the *same* records (html vs. markdown, both n=54),
uses a *paired* bootstrap (resample the same record indices for both sides each
iteration) to report a CI directly on the difference -- the statistically appropriate
test for a matched/within-subject ablation design, and more informative than two
separate, unpaired CIs.

Usage:
    python scripts/bootstrap_ci.py
"""
import json
import random
from typing import Any, Dict, List, Tuple

N_BOOTSTRAP = 10000
SEED = 42


def load_records(path: str) -> List[Dict[str, Any]]:
    d = json.load(open(path, encoding="utf-8"))
    return [r for r in d["records"] if "error" not in r]


def micro_f1(records: List[Dict[str, Any]], strict: bool = False) -> float:
    tp_key = "n_strict_matched" if strict else "n_matched"
    tp = sum(r["extraction"][tp_key] for r in records)
    fp = sum(r["extraction"]["n_unmatched_extracted"] for r in records)
    fn = sum(r["extraction"]["n_unmatched_ground_truth"] for r in records)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def bootstrap_ci(records: List[Dict[str, Any]], strict: bool = False, n_boot: int = N_BOOTSTRAP) -> Tuple[float, float, float]:
    """Returns (point_estimate, ci_low, ci_high) for the 95% percentile bootstrap CI."""
    rng = random.Random(SEED)
    n = len(records)
    point = micro_f1(records, strict)
    samples = []
    for _ in range(n_boot):
        resample = [records[rng.randrange(n)] for _ in range(n)]
        samples.append(micro_f1(resample, strict))
    samples.sort()
    lo = samples[int(0.025 * n_boot)]
    hi = samples[int(0.975 * n_boot)]
    return point, lo, hi


def paired_bootstrap_diff(
    records_a: List[Dict[str, Any]], records_b: List[Dict[str, Any]], strict: bool = False, n_boot: int = N_BOOTSTRAP
) -> Tuple[float, float, float, float]:
    """
    Paired bootstrap over matched (country, year) records in both lists.
    Returns (point_diff, ci_low, ci_high, p_value_two_sided) for metric_a - metric_b.
    """
    key = lambda r: (r["country"], r["election_year"])
    idx_a = {key(r): r for r in records_a}
    idx_b = {key(r): r for r in records_b}
    common_keys = sorted(set(idx_a) & set(idx_b))
    assert len(common_keys) == len(records_a) == len(records_b), (
        f"record sets are not identical: {len(common_keys)} common vs "
        f"{len(records_a)}/{len(records_b)} -- paired bootstrap requires matched records"
    )
    paired_a = [idx_a[k] for k in common_keys]
    paired_b = [idx_b[k] for k in common_keys]

    point = micro_f1(paired_a, strict) - micro_f1(paired_b, strict)

    rng = random.Random(SEED)
    n = len(common_keys)
    diffs = []
    for _ in range(n_boot):
        sample_idx = [rng.randrange(n) for _ in range(n)]
        resample_a = [paired_a[i] for i in sample_idx]
        resample_b = [paired_b[i] for i in sample_idx]
        diffs.append(micro_f1(resample_a, strict) - micro_f1(resample_b, strict))
    diffs.sort()
    lo = diffs[int(0.025 * n_boot)]
    hi = diffs[int(0.975 * n_boot)]
    # two-sided bootstrap p-value: fraction of resampled diffs that cross 0 relative to
    # the observed sign, doubled
    if point >= 0:
        p = 2 * (sum(1 for d in diffs if d <= 0) / n_boot)
    else:
        p = 2 * (sum(1 for d in diffs if d >= 0) / n_boot)
    p = min(p, 1.0)
    return point, lo, hi, p


def main() -> None:
    html = load_records("results/extraction_evaluation_html.json")
    markdown = load_records("results/extraction_evaluation_markdown.json")
    raw = load_records("results/extraction_evaluation_raw_sample.json")

    print(f"N_BOOTSTRAP={N_BOOTSTRAP}, seed={SEED}\n")

    print("=== Per-mode 95% CI (percentile bootstrap over records) ===")
    for name, records in [("html (n=54)", html), ("markdown (n=54)", markdown), ("raw (n=10)", raw)]:
        for strict in (False, True):
            label = "strict F1" if strict else "existence F1"
            point, lo, hi = bootstrap_ci(records, strict)
            print(f"  {name:16s} {label:14s}: {point*100:.1f}%  [{lo*100:.1f}%, {hi*100:.1f}%]")
    print()

    print("=== Paired bootstrap: html vs. markdown difference (n=54, matched records) ===")
    for strict in (False, True):
        label = "strict F1" if strict else "existence F1"
        point, lo, hi, p = paired_bootstrap_diff(html, markdown, strict)
        sig = "SIGNIFICANT (95% CI excludes 0)" if lo > 0 or hi < 0 else "not significant (95% CI includes 0)"
        print(f"  {label:14s}: html - markdown = {point*100:+.1f}pp  [{lo*100:+.1f}pp, {hi*100:+.1f}pp]  p~{p:.3f}  -> {sig}")
    print()

    print("=== Sanity check: raw (n=10) vs. html/markdown restricted to the same 10 records ===")
    raw_keys = {(r["country"], r["election_year"]) for r in raw}
    html_10 = [r for r in html if (r["country"], r["election_year"]) in raw_keys]
    markdown_10 = [r for r in markdown if (r["country"], r["election_year"]) in raw_keys]
    for name, records in [("html (n=10)", html_10), ("markdown (n=10)", markdown_10), ("raw (n=10)", raw)]:
        for strict in (False, True):
            label = "strict F1" if strict else "existence F1"
            point, lo, hi = bootstrap_ci(records, strict)
            print(f"  {name:16s} {label:14s}: {point*100:.1f}%  [{lo*100:.1f}%, {hi*100:.1f}%]")
    print()

    print("=== Paired bootstrap: html vs. raw and markdown vs. raw (n=10, matched records) ===")
    for label_pair, a, b in [("html - raw", html_10, raw), ("markdown - raw", markdown_10, raw)]:
        for strict in (False, True):
            label = "strict F1" if strict else "existence F1"
            point, lo, hi, p = paired_bootstrap_diff(a, b, strict)
            sig = "SIGNIFICANT" if lo > 0 or hi < 0 else "not significant"
            print(f"  {label_pair:16s} {label:14s}: {point*100:+.1f}pp  [{lo*100:+.1f}pp, {hi*100:+.1f}pp]  p~{p:.3f}  -> {sig}")


if __name__ == "__main__":
    main()
