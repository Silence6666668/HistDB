#!/usr/bin/env python3
"""Review and de-duplicate ablation candidates.

Two normalization steps beyond the raw comparison:

  1. Graph-entity reduction. The five executors serialize nodes and
     relationships differently (ArcadeDB adds @cat/@rid/@type, FalkorDB adds
     dst/src, Neo4j renders records as "({}, 'KNOWS', {})"). Those are
     transport representations of the same value, so any dict that looks like a
     serialized node or relationship is reduced to its properties.

  2. Family de-duplication. Remaining divergences are grouped by a signature
     that combines which engines disagreed, the kind of difference (row count
     vs value), and the mechanism shape of the query, so that many surface
     variants of one underlying issue collapse into a single family.
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
from scripts.triage_ablation_differential import (  # noqa: E402
    CRASH_RE,
    strip_executor_sentinels,
)

LANES = ["skeleton_feature", "skeleton_only", "feature_only", "random_feature"]

ENTITY_META_KEYS = {"@cat", "@rid", "@type", "@in", "@out", "src", "dst", "start", "end"}
CLAUSE_RE = re.compile(
    r"\b(OPTIONAL MATCH|MATCH|UNWIND|WITH|WHERE|CALL|CREATE|MERGE|SET|DELETE|REMOVE"
    r"|RETURN|UNION|FOREACH|FILTER|LET|NEXT|WHEN)\b",
    re.IGNORECASE,
)


def reduce_graph_entities(value):
    """Collapse engine-specific node/relationship serializations to properties."""
    if isinstance(value, list):
        return [reduce_graph_entities(v) for v in value]
    if not isinstance(value, dict):
        return value
    inner = {k: reduce_graph_entities(v) for k, v in value.items()}
    looks_like_entity = (
        ("properties" in inner and ("type" in inner or "@type" in inner or "labels" in inner))
        or ("@rid" in inner)
        or ("@cat" in inner)
        or ({"src", "dst"} <= set(inner))
    )
    if looks_like_entity:
        props = inner.get("properties")
        if not isinstance(props, dict):
            props = {k: v for k, v in inner.items() if k not in ENTITY_META_KEYS and not k.startswith("@")}
        return {"$entity": props}
    return inner


def normalized_rows(record: dict) -> list | None:
    main = extract_main_phase(record)
    if not isinstance(main, dict) or main.get("records") is None:
        return None
    preview = _normalize_preview(record)
    scrubbed = strip_executor_sentinels(preview.get("records") or [])
    reduced = reduce_graph_entities(scrubbed)
    return normalize_rows(reduced, ordered=False)


def mechanisms(query: str) -> str:
    kinds = sorted({m.group(1).upper().replace("  ", " ") for m in CLAUSE_RE.finditer(query or "")})
    return "+".join(kinds)


def diff_kind(rows_by_target: dict[str, list]) -> str:
    counts = {t: len(v) for t, v in rows_by_target.items()}
    if len(set(counts.values())) > 1:
        return "row_count"
    return "value"


def has_non_scalar(rows_by_target: dict[str, list]) -> bool:
    """True when any returned value is a node/relationship/path-like object.

    Graph entities are serialized differently by each executor (Neo4j renders a
    relationship as the string "({}, 'KNOWS', {})", ArcadeDB adds @rid/@type,
    FalkorDB adds src/dst), so a difference that involves one is a transport
    representation difference rather than a comparable semantic result.
    """
    for rows in rows_by_target.values():
        for row in rows:
            values = row.values() if isinstance(row, dict) else [row]
            for value in values:
                if isinstance(value, (dict, list)):
                    return True
                if isinstance(value, str) and re.match(r"^\(\{.*\}, *'[^']*', *\{.*\}\)$", value.strip()):
                    return True
    return False


def difference_shape(rows_by_target: dict[str, list], outlier: str) -> str:
    """Coarse description of how the outlier differs from the majority."""
    others = [t for t in rows_by_target if t != outlier]
    if not others:
        return "unknown"
    ref = rows_by_target[others[0]]
    out = rows_by_target[outlier]
    if len(out) != len(ref):
        return f"rows {len(ref)}->{len(out)}"
    shapes = []
    for a, b in zip(ref, out):
        if not isinstance(a, dict) or not isinstance(b, dict):
            continue
        for k in sorted(set(a) | set(b)):
            va, vb = a.get(k), b.get(k)
            if _values_equal(va, vb, 0.0):
                continue
            ta = "null" if va is None else type(va).__name__
            tb = "null" if vb is None else type(vb).__name__
            shapes.append(f"{k}:{ta}->{tb}")
    return ",".join(sorted(set(shapes))[:3]) or "value"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exec-root", required=True)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--output-csv", default=None)
    args = ap.parse_args()

    root = Path(args.exec_root)
    lanes = [l for l in LANES if not args.only or l in args.only]

    for lane in lanes:
        src = root / lane / "execution_results.jsonl"
        if not src.exists():
            print(f"[{lane}] (missing merged results)")
            continue
        groups: dict[str, list[dict]] = collections.defaultdict(list)
        for line in src.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                groups[f"{rec.get('query_id')}::{rec.get('case_id')}"].append(rec)

        cat = collections.Counter()
        families: dict[tuple, list] = collections.defaultdict(list)
        details: list[dict] = []
        for key, recs in groups.items():
            passed = [r for r in recs if r.get("status") == "passed"]
            failed = [r for r in recs if r.get("status") != "passed"]
            if any(CRASH_RE.search(str(r.get("error") or "")) for r in failed):
                cat["crash"] += 1
                families[(lane, "crash", "any")].append((key, recs))
                continue
            if len(passed) < 2:
                continue
            rows_by_target = {r["target"]: (normalized_rows(r) or []) for r in passed}
            base_t = passed[0]["target"]
            base = rows_by_target[base_t]
            same = all(
                _multiset_matches(rows_by_target[r["target"]], base, lambda a, b: _values_equal(a, b, 0.0))
                for r in passed[1:]
            )
            if same:
                cat["agree"] += 1
                continue
            if len(passed) != len(recs):
                cat["diverge_partial"] += 1
                continue
            cat["diverge_strict"] += 1
            kind = diff_kind(rows_by_target)
            if has_non_scalar(rows_by_target):
                cat["non_scalar"] += 1
                continue
            disagreeing = tuple(sorted(t for t, v in rows_by_target.items() if v != base))
            mech = mechanisms(str(recs[0].get("main_query") or ""))
            shape = difference_shape(rows_by_target, disagreeing[0]) if len(disagreeing) == 1 else "multi"
            # Family key: which engines disagree, and how. Engines that agree
            # with each other are the majority; the minority is the outlier.
            families[(lane, kind, "+".join(disagreeing), shape)].append((key, recs))
            details.append({
                "lane": lane, "category": "diverge_strict", "kind": kind,
                "query_id": recs[0].get("query_id"), "case_id": recs[0].get("case_id"),
                "disagreeing_targets": ",".join(disagreeing),
                "mechanisms": mech,
                "shape": shape,
                "row_counts": json.dumps({t: len(v) for t, v in rows_by_target.items()}),
                "rows_preview": json.dumps(
                    {t: v[:3] for t, v in rows_by_target.items()}, ensure_ascii=False
                )[:1500],
                "main_query": recs[0].get("main_query"),
            })

        print(f"--- {lane}: agree={cat['agree']} diverge_strict={cat['diverge_strict']} "
              f"(non_scalar={cat['non_scalar']}) diverge_partial={cat['diverge_partial']} "
              f"crash={cat['crash']}")
        top = sorted(((k, v) for k, v in families.items() if k[1] != "crash"),
                     key=lambda kv: -len(kv[1]))[:8]
        print("    top families (kind, engines, shape) -> size, example:")
        for (l, kind, disagree, shape), members in top:
            ex = str(members[0][1][0].get("main_query") or "")[:90]
            print(f"      {kind:10} {len(members):4d}  [{disagree}]")
            print(f"                    shape: {shape}")
            print(f"                    e.g. {ex}")
        if args.output_csv and details:
            with open(args.output_csv, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(details[0].keys()))
                w.writeheader()
                w.writerows(details)
            print(f"    wrote {args.output_csv} ({len(details)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
