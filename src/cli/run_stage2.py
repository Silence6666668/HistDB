from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set

from src.stage2.build_fragment_corpus import build_fragment_corpus
from src.stage2.common import dump_json, iter_jsonl, write_jsonl
from src.common.jsonl_utils import append_jsonl
from src.stage2.build_fragment_corpus import classify_group_from_text
from src.stage2.feature_selection import select_feature_records, selected_feature_metadata
from src.stage2.fragment_policy import documentation_fragment_text, filter_legacy_neo4j_index_fragments
from src.stage2.group_fragments import group_fragments
from src.stage2.official_filter import filter_official_chunks, normalize_profile
from src.stage2.retrieval_query_builder import build_retrieval_query
from src.stage2.retriever import LexicalRetriever

RAW_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Stage 2: fragment corpus + retrieval + grouping")
    parser.add_argument("--skeletons", default="data/stage1/skeletons.jsonl")
    parser.add_argument("--features", default="data/stage1/features.jsonl")
    parser.add_argument("--output-dir", default="data/stage2")
    parser.add_argument("--extra-fragments", nargs="*", default=None, help="Optional extra fragment corpora JSONL files")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--per-group-k", type=int, default=3)
    parser.add_argument("--feature-selection", choices=["paired", "random", "random_constrained"], default="paired")
    parser.add_argument(
        "--randomize-feature-selection",
        action="store_true",
        help="Mix a run-level salt into feature sampling so the same query_id can receive different selected features across different Stage 2 runs.",
    )
    parser.add_argument(
        "--feature-run-salt",
        default=None,
        help="Optional explicit run salt mixed into feature sampling. Useful for making a randomized Stage 2 run reproducible.",
    )
    parser.add_argument(
        "--generation-strategy",
        choices=["skeleton", "feature-driven", "feature-driven-soft", "feature-fragment-only"],
        default="skeleton",
        help="Choose whether Stage 3 should treat the skeleton as strict, absent, or soft guidance.",
    )
    parser.add_argument("--feature-sample-size", type=int, default=3)
    parser.add_argument("--feature-seed", type=int, default=42)
    parser.add_argument(
        "--feature-draws-per-skeleton", type=int, default=1,
        help="Prepare this many new compositions per skeleton. Random modes reject previously selected unordered feature-ID sets within this output run.",
    )
    parser.add_argument(
        "--allow-repeated-feature-combinations", action="store_true",
        help="Opt out of combination deduplication and retain historical stateless sampling for reproduction of older runs.",
    )
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--allow-same-query-fragments", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--official-retrieval-results",
        default=None,
        help="Optional official retrieval results JSONL. If provided, Stage 2 will consume those results instead of lexical retrieval when query_id matches.",
    )
    parser.add_argument(
        "--official-filter-profile",
        choices=["none", "neo4j_current", "portable_cypher"],
        default="none",
        help="Filter official RAG chunks before Stage 3. Use portable_cypher for cross-DB runs and neo4j_current for Neo4j-only runs.",
    )
    parser.add_argument(
        "--official-filter-allowed-source",
        nargs="*",
        default=None,
        help="Optional allow-list of official chunk sources, for example cypher_manual.",
    )
    parser.add_argument(
        "--official-backfill-k",
        type=int,
        default=2,
        help="When extra official fragments are available, append up to this many official chunks if normal retrieval found none.",
    )
    args = parser.parse_args()
    if args.feature_draws_per_skeleton < 1:
        parser.error("--feature-draws-per-skeleton must be positive")
    if args.feature_draws_per_skeleton > 1 and args.feature_selection == "paired":
        parser.error("paired selection is fixed; multiple feature draws require random or random_constrained selection")
    return args


def load_extra_fragments(paths: List[str] | None) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in paths or []:
        records.extend(iter_jsonl(path))
    return records


def load_official_retrieval_map(path: str | None) -> Dict[str, Dict[str, Any]]:
    if not path:
        return {}
    records = list(iter_jsonl(path))
    out: Dict[str, Dict[str, Any]] = {}
    for record in records:
        query_id = str(record.get("query_id") or "").strip()
        if query_id:
            out[query_id] = record
    return out


def convert_official_chunks(
    record: Dict[str, Any],
    top_k: int,
    *,
    filter_profile: str = "none",
    allowed_sources: List[str] | None = None,
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    raw_chunks = list(record.get("retrieved_chunks") or [])
    raw_chunks, mandatory_filter_reasons = filter_legacy_neo4j_index_fragments(raw_chunks)
    chunks, filter_reasons = filter_official_chunks(
        raw_chunks,
        profile=filter_profile,
        allowed_sources=allowed_sources,
        limit=top_k,
    )
    for reason, count in mandatory_filter_reasons.items():
        filter_reasons[reason] = filter_reasons.get(reason, 0) + count
    chunks = chunks[:top_k]
    converted: List[Dict[str, Any]] = []
    for chunk in chunks:
        text = documentation_fragment_text(chunk)
        tags = [str(tag) for tag in (chunk.get("tags") or [])]
        converted.append({
            "fragment_id": str(chunk.get("chunk_id") or ""),
            "query_id": str(record.get("query_id") or "official"),
            "source_id": str(chunk.get("source") or "cypher_manual"),
            "source_type": f"official_{str(chunk.get('chunk_type') or 'chunk')}",
            "group_hint": classify_group_from_text(text, tags),
            "text": text,
            "tags": tags,
            "metadata": {
                "source": chunk.get("source"),
                "url": chunk.get("url"),
                "page_title": chunk.get("page_title"),
                "section_title": chunk.get("section_title"),
                "chunk_type": chunk.get("chunk_type"),
                "rank": chunk.get("rank"),
                "official_score": chunk.get("score"),
            },
            "score": round(float(chunk.get("score") or 0.0), 6),
            "overlap_tokens": [],
        })
    return converted, filter_reasons


def fragment_is_official(fragment: Dict[str, Any], allowed_sources: List[str] | None = None) -> bool:
    source_type = str(fragment.get("source_type") or "").lower()
    source_id = str(fragment.get("source_id") or "").lower()
    metadata = fragment.get("metadata") if isinstance(fragment.get("metadata"), dict) else {}
    metadata_source = str(metadata.get("source") or "").lower()
    url = str(metadata.get("url") or fragment.get("url") or "").lower()
    allowed = {str(item).strip().lower() for item in (allowed_sources or []) if str(item).strip()}
    if "official" in source_type or "neo4j.com/docs" in url:
        return True
    if allowed and (source_id in allowed or metadata_source in allowed):
        return True
    return source_id in {"cypher_manual", "neo4j_manual", "neo4j_docs"}


def backfill_official_fragments(
    topk: List[Dict[str, Any]],
    official_retriever: LexicalRetriever | None,
    retrieval_query: Dict[str, Any],
    *,
    backfill_k: int,
    allowed_sources: List[str] | None = None,
) -> List[Dict[str, Any]]:
    if backfill_k <= 0 or official_retriever is None:
        return topk
    if any(fragment_is_official(fragment, allowed_sources) for fragment in topk):
        return topk

    seen = {str(fragment.get("fragment_id") or "") for fragment in topk}
    backfill = [
        fragment
        for fragment in official_retriever.retrieve(retrieval_query, top_k=backfill_k, exclude_query_id=None)
        if str(fragment.get("fragment_id") or "") not in seen
    ]
    if not backfill:
        raw_tokens = [
            match.group(0).lower()
            for match in RAW_TOKEN_RE.finditer(str(retrieval_query.get("retrieval_text") or ""))
        ]
        backfill = [
            fragment
            for fragment in official_retriever.retrieve(
                {**retrieval_query, "retrieval_tokens": raw_tokens},
                top_k=backfill_k,
                exclude_query_id=None,
            )
            if str(fragment.get("fragment_id") or "") not in seen
        ]
    if not backfill:
        backfill = [
            fragment
            for fragment in official_retriever.fragments[:backfill_k]
            if str(fragment.get("fragment_id") or "") not in seen
        ]
    return topk + backfill[:backfill_k]


def load_completed_queries(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    out: Set[str] = set()
    for rec in iter_jsonl(path):
        query_id = str(rec.get("query_id") or "").strip()
        if query_id:
            out.add(query_id)
    return out


def rewrite_records_for_completed_queries(path: Path, completed: Set[str]) -> None:
    if not path.exists():
        return
    kept: List[Dict[str, Any]] = []
    for rec in iter_jsonl(path):
        query_id = str(rec.get("query_id") or "").strip()
        if query_id in completed:
            kept.append(rec)
    write_jsonl(kept, path)


def iter_skeleton_draws(skeletons: List[Dict[str, Any]], draws: int) -> Iterator[Dict[str, Any]]:
    """Give each composition a task ID while retaining its historical seed ID."""
    task_ids: Set[str] = set()
    for idx, skeleton in enumerate(skeletons, start=1):
        seed_query_id = str(skeleton.get("query_id") or f"q_{idx:06d}")
        skeleton_id = str(skeleton.get("skeleton_id") or "").strip()
        skeleton_text = " ".join(str(skeleton.get("skeleton_text") or "").split())
        if skeleton_id:
            skeleton_key = f"id:{skeleton_id}"
        elif skeleton_text:
            skeleton_key = "text:" + hashlib.sha256(skeleton_text.encode("utf-8")).hexdigest()
        else:
            skeleton_key = f"query:{seed_query_id}"
        for draw_index in range(draws):
            task_id = seed_query_id if draw_index == 0 else f"{seed_query_id}__draw_{draw_index + 1:06d}"
            if task_id in task_ids:
                raise ValueError(f"Duplicate Stage 2 task ID: {task_id}")
            task_ids.add(task_id)
            yield {
                **skeleton,
                "query_id": task_id,
                "seed_query_id": seed_query_id,
                "feature_selection_skeleton_key": skeleton_key,
                "feature_selection_draw_index": draw_index,
            }


def main() -> int:
    args = parse_args()
    skeletons = list(iter_jsonl(args.skeletons))
    features = list(iter_jsonl(args.features))
    if not skeletons:
        raise FileNotFoundError(f"No skeleton records found: {args.skeletons}")
    if not features:
        raise FileNotFoundError(f"No feature records found: {args.features}")

    feature_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in features:
        feature_map[str(record.get("query_id") or "unknown")].append(record)

    extra_fragments = load_extra_fragments(args.extra_fragments)
    fragments = build_fragment_corpus(skeletons, features, extra_fragments=extra_fragments)
    retriever = LexicalRetriever(fragments)
    official_retrieval_map = load_official_retrieval_map(args.official_retrieval_results)
    official_filter_profile = normalize_profile(args.official_filter_profile)
    official_filter_allowed_sources = args.official_filter_allowed_source or []
    official_fragments = [
        fragment
        for fragment in fragments
        if fragment_is_official(fragment, official_filter_allowed_sources)
    ]
    official_retriever = LexicalRetriever(official_fragments) if official_fragments else None

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    retrieval_queries_path = output_dir / "retrieval_queries.jsonl"
    retrieval_results_path = output_dir / "retrieval_results.jsonl"
    instantiation_inputs_path = output_dir / "instantiation_inputs.jsonl"
    completed_queries_path = output_dir / "completed_queries.jsonl"
    summary_path = output_dir / "summary.json"

    if not args.resume:
        for path in (
            retrieval_queries_path,
            retrieval_results_path,
            instantiation_inputs_path,
            completed_queries_path,
            summary_path,
        ):
            if path.exists():
                path.unlink()

    completed_queries = load_completed_queries(completed_queries_path) if args.resume else set()
    existing_summary: Dict[str, Any] = {}
    if args.resume and summary_path.exists():
        existing_summary = json.loads(summary_path.read_text(encoding="utf-8"))

    unique_combinations = args.feature_selection != "paired" and not args.allow_repeated_feature_combinations
    selection_protocol = "unique_unordered_v1" if unique_combinations else "legacy_stateless_v1"
    input_hashes = {
        "skeletons": hashlib.sha256(Path(args.skeletons).read_bytes()).hexdigest(),
        "features": hashlib.sha256(Path(args.features).read_bytes()).hexdigest(),
    }
    selection_config = {
        "feature_selection": args.feature_selection,
        "feature_seed": args.feature_seed,
        "feature_sample_size": args.feature_sample_size,
        "feature_selection_protocol": selection_protocol,
        "feature_selection_input_hashes": input_hashes,
    }
    if args.resume and completed_queries and not existing_summary and not unique_combinations:
        # Historical Stage 2 wrote its summary only at the end of a run.
        # Recover recorded seed/mode/salt, but do not invent historical hashes.
        legacy_rows = [
            row for row in iter_jsonl(instantiation_inputs_path)
            if str(row.get("query_id") or "") in completed_queries
        ]
        if {str(row["query_id"]) for row in legacy_rows} != completed_queries:
            raise ValueError("Cannot resume without saved feature selections for all completed queries")
        if any(row.get("feature_selection_protocol") not in (None, "legacy_stateless_v1") for row in legacy_rows):
            raise ValueError("Cannot change the saved feature selection protocol while recovering a missing summary")
        saved_settings = {
            (row.get("feature_selection"), row.get("feature_selection_seed"), str(row.get("feature_run_salt") or ""))
            for row in legacy_rows
        }
        if len(saved_settings) != 1:
            raise ValueError("Cannot resume legacy inputs with inconsistent feature selection settings")
        saved_mode, saved_seed, saved_salt = saved_settings.pop()
        existing_summary = {
            "feature_selection": saved_mode,
            "feature_seed": saved_seed,
            "feature_run_salt": saved_salt or None,
            "feature_sample_size": args.feature_sample_size,
            "feature_selection_protocol": "legacy_stateless_v1",
            "resume_metadata_recovered_from_rows": True,
            "original_input_hashes_unavailable": True,
            "original_feature_sample_size_unavailable": True,
        }
    if args.resume and completed_queries:
        if not existing_summary and unique_combinations:
            raise ValueError("Cannot resume unique feature selection without its saved summary.json")
        for key, value in selection_config.items():
            saved = existing_summary.get(key)
            if key == "feature_selection_protocol" and saved is None:
                saved = "legacy_stateless_v1"
            if key == "feature_selection_input_hashes" and saved is None and not unique_combinations:
                continue  # Old manifests did not record these hashes.
            if saved != value:
                raise ValueError(f"Cannot resume with changed {key}; use a new output directory")
        if args.feature_run_salt is not None and str(args.feature_run_salt) != str(existing_summary.get("feature_run_salt") or ""):
            raise ValueError("Cannot resume with changed feature_run_salt; use a new output directory")
        if unique_combinations and not instantiation_inputs_path.exists():
            raise ValueError("Cannot resume without saved feature selections in instantiation_inputs.jsonl")

    if args.resume:
        rewrite_records_for_completed_queries(retrieval_queries_path, completed_queries)
        rewrite_records_for_completed_queries(retrieval_results_path, completed_queries)
        rewrite_records_for_completed_queries(instantiation_inputs_path, completed_queries)

    used_combinations: Dict[str, set[frozenset[str]]] = defaultdict(set)
    if unique_combinations and args.resume and instantiation_inputs_path.exists():
        restored_queries: Set[str] = set()
        for row in iter_jsonl(instantiation_inputs_path):
            key = str(row.get("feature_selection_skeleton_key") or "")
            ids = [str(value).strip() for value in row.get("selected_feature_ids", [])]
            if not key or len(ids) != args.feature_sample_size or len(set(ids)) != len(ids) or not all(ids):
                raise ValueError(f"Invalid saved feature selection for {row.get('query_id')}")
            combination = frozenset(ids)
            if combination in used_combinations[key]:
                raise ValueError(f"Duplicate saved feature combination for skeleton {key}")
            used_combinations[key].add(combination)
            restored_queries.add(str(row["query_id"]))
        if restored_queries != completed_queries:
            raise ValueError("Completed queries do not match saved feature selections; cannot safely resume")

    feature_run_salt = (
        args.feature_run_salt
        if args.feature_run_salt is not None
        else existing_summary.get("feature_run_salt")
    )
    if args.randomize_feature_selection and not feature_run_salt and not (args.resume and completed_queries):
        feature_run_salt = secrets.token_hex(8)
    feature_run_salt = str(feature_run_salt or "")

    # Persist the salt/config before the first task, including interrupted runs.
    dump_json({
        **existing_summary,
        **selection_config,
        "feature_run_salt": feature_run_salt or None,
        "feature_draws_per_skeleton": args.feature_draws_per_skeleton,
        "status": "running",
    }, summary_path)

    for sk in iter_skeleton_draws(skeletons, args.feature_draws_per_skeleton):
        if args.max_items is not None and len(completed_queries) >= args.max_items:
            break
        query_id = str(sk["query_id"])
        seed_query_id = str(sk["seed_query_id"])
        skeleton_key = str(sk["feature_selection_skeleton_key"])
        draw_index = int(sk["feature_selection_draw_index"])
        if args.resume and query_id in completed_queries:
            continue
        query_features = feature_map.get(seed_query_id, [])
        selected_features = select_feature_records(
            {**sk, "query_id": seed_query_id},
            query_features=query_features,
            all_features=features,
            mode=args.feature_selection,
            sample_size=args.feature_sample_size,
            seed=args.feature_seed,
            rng_salt=feature_run_salt,
            draw_index=draw_index if unique_combinations else 0,
            used_combinations=used_combinations[skeleton_key] if unique_combinations else None,
        )
        selection_meta = {
            **selected_feature_metadata(selected_features),
            "seed_query_id": seed_query_id,
            "feature_selection_skeleton_key": skeleton_key,
            "feature_selection_draw_index": draw_index,
            "feature_selection_protocol": selection_protocol,
        }
        rq = build_retrieval_query(sk, selected_features)
        official_record = official_retrieval_map.get(seed_query_id)
        official_filter_reasons: Dict[str, int] = {}
        if official_record is not None:
            topk, official_filter_reasons = convert_official_chunks(
                official_record,
                top_k=args.top_k,
                filter_profile=official_filter_profile,
                allowed_sources=official_filter_allowed_sources,
            )
            retrieval_backend = "official"
            retrieval_query_payload = {
                **rq,
                "official_query_text": official_record.get("query_text_for_retrieval"),
                "official_filter_profile": official_filter_profile,
                "official_filter_allowed_sources": official_filter_allowed_sources,
                "official_filter_removed_reasons": official_filter_reasons,
            }
        else:
            topk = retriever.retrieve(
                rq,
                top_k=args.top_k,
                exclude_query_id=None if args.allow_same_query_fragments else seed_query_id,
            )
            topk = backfill_official_fragments(
                topk,
                official_retriever,
                rq,
                backfill_k=args.official_backfill_k,
                allowed_sources=official_filter_allowed_sources,
            )
            retrieval_backend = "lexical"
            retrieval_query_payload = rq
        grouped = group_fragments(topk, per_group_k=args.per_group_k)
        append_jsonl(rq, retrieval_queries_path)
        append_jsonl({
            "query_id": query_id,
            "skeleton_id": sk.get("skeleton_id"),
            "prepared_source_name": sk.get("prepared_source_name"),
            "prepared_source_path": sk.get("prepared_source_path"),
            "feature_selection": args.feature_selection,
            "feature_selection_seed": args.feature_seed,
            "feature_run_salt": feature_run_salt or None,
            "randomize_feature_selection": bool(feature_run_salt),
            **selection_meta,
            "retrieval_backend": retrieval_backend,
            "retrieval_query": retrieval_query_payload,
            "topk": topk,
            "official_filter_profile": official_filter_profile,
            "official_filter_allowed_sources": official_filter_allowed_sources,
            "official_filter_removed_reasons": official_filter_reasons,
        }, retrieval_results_path)
        feature_items = []
        for ft in selected_features:
            feature_items.append({
                "feature_id": ft.get("feature_id"),
                "text": ft.get("feature_text") or ft.get("text") or ft.get("feature_name") or ft.get("feature_description"),
                "description": ft.get("feature_description"),
                "evidence": ft.get("evidence"),
                "tags": ft.get("tags", []),
            })
        generation_strategy = args.generation_strategy
        skeleton_text = sk.get("skeleton_text")
        guidance_skeleton_text = sk.get("skeleton_text")
        if generation_strategy in {"feature-driven", "feature-fragment-only"}:
            skeleton_text = ""
        if generation_strategy == "feature-fragment-only":
            guidance_skeleton_text = ""
        append_jsonl({
            "query_id": query_id,
            "source_id": sk.get("source_id"),
            "repo": sk.get("repo"),
            "issue_number": sk.get("issue_number"),
            "url": sk.get("url"),
            "prepared_source_name": sk.get("prepared_source_name"),
            "prepared_source_path": sk.get("prepared_source_path"),
            "skeleton_id": sk.get("skeleton_id"),
            "skeleton_text": skeleton_text,
            "guidance_skeleton_text": guidance_skeleton_text,
            "holes": sk.get("holes", []),
            "generation_strategy": generation_strategy,
            "feature_selection": args.feature_selection,
            "feature_selection_seed": args.feature_seed,
            "feature_run_salt": feature_run_salt or None,
            "randomize_feature_selection": bool(feature_run_salt),
            **selection_meta,
            "feature_items": feature_items,
            "retrieval_backend": retrieval_backend,
            "retrieval_query": retrieval_query_payload,
            "official_filter_profile": official_filter_profile,
            "official_filter_allowed_sources": official_filter_allowed_sources,
            "official_filter_removed_reasons": official_filter_reasons,
            "fragment_groups": grouped,
        }, instantiation_inputs_path)
        append_jsonl({"query_id": query_id}, completed_queries_path)
        completed_queries.add(query_id)
        print(
            f"[Stage2] processed {len(completed_queries)}: {query_id} | "
            f"backend={retrieval_backend} | retrieved={len(topk)} | groups={len(grouped)}"
        )

    write_jsonl(fragments, output_dir / "fragments.jsonl")
    retrieval_queries = list(iter_jsonl(retrieval_queries_path)) if retrieval_queries_path.exists() else []
    retrieval_results = list(iter_jsonl(retrieval_results_path)) if retrieval_results_path.exists() else []
    instantiation_inputs = list(iter_jsonl(instantiation_inputs_path)) if instantiation_inputs_path.exists() else []

    summary = {
        **{
            key: existing_summary[key]
            for key in (
                "resume_metadata_recovered_from_rows",
                "original_input_hashes_unavailable",
                "original_feature_sample_size_unavailable",
            )
            if key in existing_summary
        },
        **selection_config,
        "status": "complete",
        "skeletons_path": str(args.skeletons),
        "features_path": str(args.features),
        "output_dir": str(output_dir),
        "fragment_count": len(fragments),
        "retrieval_query_count": len(retrieval_queries),
        "instantiation_input_count": len(instantiation_inputs),
        "top_k": args.top_k,
        "per_group_k": args.per_group_k,
        "feature_selection": args.feature_selection,
        "generation_strategy": args.generation_strategy,
        "feature_sample_size": args.feature_sample_size,
        "feature_seed": args.feature_seed,
        "feature_draws_per_skeleton": args.feature_draws_per_skeleton,
        "randomize_feature_selection": bool(feature_run_salt),
        "feature_run_salt": feature_run_salt or None,
        "extra_fragment_files": args.extra_fragments or [],
        "allow_same_query_fragments": bool(args.allow_same_query_fragments),
        "official_retrieval_results": args.official_retrieval_results,
        "official_query_count": len(official_retrieval_map),
        "official_filter_profile": official_filter_profile,
        "official_filter_allowed_sources": official_filter_allowed_sources,
        "official_backfill_k": args.official_backfill_k,
        "official_fragment_count": len(official_fragments),
    }
    dump_json(summary, summary_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
