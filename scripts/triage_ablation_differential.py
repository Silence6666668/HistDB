#!/usr/bin/env python3
"""Cross-engine triage for the ablation runs using the validation comparator.

Stage 45 compares results by a literal fingerprint (column keys plus row text),
which reports two classes of false divergence:
  * numeric type differences, e.g. 0 vs 0.0
  * a target that returns zero rows with an empty column list while other
    targets return zero rows with named columns

The reproduction/validation path already contains the comparator the paper
relies on: normalize_rows() plus _values_equal() (math.isclose) and
_multiset_matches(). This script applies that comparator to the ablation
execution results, so the counts it produces are the ones comparable to the
findings pipeline.

Output categories per case:
  agree          two or more targets passed and their row multisets match
  diverge        two or more targets passed and their row multisets differ
  crash          at least one target reported a crash / internal error
  mixed          one or more targets failed for other reasons
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.stage4.compare import _normalize_preview  # noqa: E402
from src.reproduction.normalize import normalize_rows  # noqa: E402
from src.reproduction.oracles import (  # noqa: E402
    _multiset_matches,
    _values_equal,
    extract_main_phase,
)

LANES = ["skeleton_feature", "skeleton_only", "feature_only", "random_feature"]

CRASH_RE = re.compile(
    r"segmentation|segfault|signal|SIGSEGV|exit code|exit 13[0-9]|core dump|"
    r"panicked|abort|UnknownError|ExecutionFailed|50N00|internal error|"
    r"ClassCastException|NullPointerException|AssertionError",
    re.IGNORECASE,
)
SENTINEL_KEY_RE = re.compile(r"^__.*__$")


def strip_executor_sentinels(value):
    """Remove executor-specific transport keys such as __labels__ / __node_id__.

    Those keys are added by individual Stage 4 executors (for example the
    FalkorDB executor attaches node labels), so they are transport artifacts
    rather than part of a query's result and must not create a divergence.
    """
    if isinstance(value, dict):
        return {
            k: strip_executor_sentinels(v)
            for k, v in value.items()
            if not SENTINEL_KEY_RE.match(str(k))
        }
    if isinstance(value, list):
        return [strip_executor_sentinels(v) for v in value]
    return value


def normalized_rows(record: dict) -> list | None:
    """AGE-aware normalization (stage4) followed by canonical normalization.

    The AGE executor returns values as strings ("0", '"Alice"', "true"), so the
    stage4 preview normalization is applied first; the reproduction normalizer
    then makes the values deterministic JSON-compatible objects.
    """
    main = extract_main_phase(record)
    if not isinstance(main, dict) or main.get("records") is None:
        return None
    preview = _normalize_preview(record)
    scrubbed = strip_executor_sentinels(preview.get("records") or [])
    return normalize_rows(scrubbed, ordered=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exec-root", required=True)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--output-csv", default=None)
    args = ap.parse_args()

    root = Path(args.exec_root)
    lanes = [l for l in LANES if not args.only or l in args.only]
    out_rows: list[dict] = []

    print(f"{'lane':20}{'cases':>8}{'agree':>8}{'divStrict':>10}{'divPart':>9}"
          f"{'crash':>7}{'mixed':>7}{'other':>7}")
    for lane in lanes:
        src = root / lane / "execution_results.jsonl"
        if not src.exists():
            print(f"{lane:20}  (missing)")
            continue
        groups: dict[str, list[dict]] = collections.defaultdict(list)
        for line in src.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                groups[f"{rec.get('query_id')}::{rec.get('case_id')}"].append(rec)

        cat = collections.Counter()
        for key, recs in groups.items():
            passed = [r for r in recs if str(r.get("status")) == "passed"]
            failed = [r for r in recs if str(r.get("status")) not in ("passed",)]
            crashes = [r for r in failed if CRASH_RE.search(str(r.get("error") or ""))]
            if crashes:
                category = "crash"
            elif len(passed) >= 2:
                normalized = [normalized_rows(r) or [] for r in passed]
                base = normalized[0]
                same = all(
                    _multiset_matches(other, base, lambda a, b: _values_equal(a, b, 0.0))
                    for other in normalized[1:]
                )
                if same:
                    category = "agree"
                else:
                    # Strict: every target executed successfully, so the
                    # disagreement cannot be explained by an unsupported
                    # construct on one engine.
                    category = "diverge_strict" if len(passed) == len(recs) else "diverge_partial"
            elif len(passed) == 1:
                category = "mixed"
            else:
                category = "other"
            cat[category] += 1
            if category in ("diverge_strict", "diverge_partial", "crash"):
                first = recs[0]
                rows_by_target = {}
                if category == "diverge":
                    for r in passed:
                        rows = normalized_rows(r) or []
                        rows_by_target[str(r.get("target"))] = rows[:4]
                out_rows.append({
                    "lane": lane,
                    "category": category,
                    "query_id": first.get("query_id"),
                    "case_id": first.get("case_id"),
                    "targets_passed": ",".join(str(r.get("target")) for r in passed),
                    "row_counts": json.dumps(
                        {str(r.get("target")): len(normalized_rows(r) or []) for r in recs},
                        ensure_ascii=False,
                    ),
                    "rows_preview": json.dumps(rows_by_target, ensure_ascii=False)[:2000],
                    "main_query": first.get("main_query"),
                })
        print(f"{lane:20}{len(groups):>8}{cat['agree']:>8}"
              f"{cat['diverge_strict']:>10}{cat['diverge_partial']:>9}"
              f"{cat['crash']:>7}{cat['mixed']:>7}{cat['other']:>7}")

    if args.output_csv and out_rows:
        with open(args.output_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)
        print(f"\nwrote {args.output_csv} ({len(out_rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
