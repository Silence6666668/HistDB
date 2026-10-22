from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from src.common.config import load_yaml
from src.common.io_utils import dump_json, ensure_dir, read_text
from src.common.jsonl_utils import iter_jsonl, write_jsonl
from src.llm.model_client import create_model_client, resolve_model_config
from src.stage3.llm_cypher25_rewrite import (
    build_rewrite_prompt,
    build_verify_prompt,
    load_doc_snippets,
    make_pair_record,
    normalize_rewrite_payload,
    rewrite_metadata_errors,
    static_rewrite_errors,
    summarize_rejections,
    verifier_accepts,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate LLM-verified Cypher25 metamorphic rewrites from existing Stage3 cases"
    )
    parser.add_argument("--input", default="data/stage3/instantiated_cases.jsonl")
    parser.add_argument("--output-dir", default="data/stage3b_llm_cypher25_rewrites")
    parser.add_argument("--model-config", default="configs/model.yaml")
    parser.add_argument("--task-name", default="cypher25_rewrite")
    parser.add_argument("--generator-prompt", default="prompts/cypher25_llm_rewrite_system.txt")
    parser.add_argument("--verifier-prompt", default="prompts/cypher25_llm_rewrite_verify_system.txt")
    parser.add_argument("--doc-chunks", default="corpus/neo4j_official/chunks/merged_chunks.jsonl")
    parser.add_argument("--doc-snippets", type=int, default=8)
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--max-rewrites-per-case", type=int, default=1)
    parser.add_argument("--min-verifier-confidence", choices=["low", "medium", "high"], default="high")
    parser.add_argument("--allow-write-main", action="store_true")
    parser.add_argument("--skip-llm-verification", action="store_true")
    return parser.parse_args()


def _make_selfdiff_case(
    base_case: Dict[str, Any],
    pair_id: str,
    rewrite_type: str,
    variant: str,
    main_query: str,
    pair: Dict[str, Any],
) -> Dict[str, Any]:
    case = dict(base_case)
    case["case_id"] = f"{base_case.get('case_id')}::{variant}::{rewrite_type}"
    case["main_query"] = main_query
    case["selfdiff_pair_id"] = pair_id
    case["selfdiff_variant"] = variant
    case["rewrite_type"] = rewrite_type
    case["rewrite_profile"] = pair.get("rewrite_profile")
    case["original_case_id"] = base_case.get("case_id")
    case["original_query_id"] = base_case.get("query_id")
    case["llm_cypher25_rewrite"] = {
        "transform_rule_id": pair.get("transform_rule_id"),
        "additional_transform_rule_ids": pair.get("additional_transform_rule_ids") or [],
        "cypher25_features_used": pair.get("cypher25_features_used") or [],
        "equivalence_rationale": pair.get("equivalence_rationale") or "",
        "safety_conditions": pair.get("safety_conditions") or [],
        "verification_plan": pair.get("verification_plan") or "",
        "llm_verification": pair.get("llm_verification") or {},
    }
    return case


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Stage3 input not found: {input_path}")

    output_dir = ensure_dir(args.output_dir)
    model_cfg = load_yaml(args.model_config)
    client = create_model_client(model_cfg, task=args.task_name)
    resolved_cfg = resolve_model_config(model_cfg, task=args.task_name)
    model_name = str(resolved_cfg.get("model") or "")
    generator_prompt = read_text(args.generator_prompt)
    verifier_prompt = read_text(args.verifier_prompt)
    doc_snippets = load_doc_snippets(args.doc_chunks, max_snippets=args.doc_snippets)

    pairs: List[Dict[str, Any]] = []
    rewritten_cases: List[Dict[str, Any]] = []
    selfdiff_cases: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    rewrite_counts: Counter[str] = Counter()
    seen_pair_ids: set[str] = set()

    processed = 0
    proposed = 0
    verified = 0

    for case in iter_jsonl(input_path):
        if args.max_items is not None and processed >= args.max_items:
            break
        processed += 1
        case_id = str(case.get("case_id") or case.get("query_id") or f"row_{processed}")
        original_query = str(case.get("main_query") or "").strip()

        try:
            generation = client.chat_json(
                system_prompt=generator_prompt,
                user_prompt=build_rewrite_prompt(case, doc_snippets, max_rewrites=args.max_rewrites_per_case),
            )
        except Exception as exc:
            rejected.append(
                {
                    "case_id": case_id,
                    "reject_reason": "generator_error",
                    "error": str(exc),
                }
            )
            continue

        rewrites = normalize_rewrite_payload(generation)[: max(1, args.max_rewrites_per_case)]
        if not rewrites:
            rejected.append(
                {
                    "case_id": case_id,
                    "reject_reason": "not_applicable",
                    "generator_response": generation,
                }
            )
            continue

        for rewrite in rewrites:
            proposed += 1
            rewritten_query = str(rewrite.get("rewritten_main_query") or "").strip()
            static_errors = static_rewrite_errors(
                original_query=original_query,
                rewritten_query=rewritten_query,
                allow_write_main=args.allow_write_main,
            )
            if static_errors:
                rejected.append(
                    {
                        "case_id": case_id,
                        "reject_reason": "static_gate_failed",
                        "static_errors": static_errors,
                        "rewrite": rewrite,
                        "generator_response": generation,
                    }
                )
                continue

            metadata_errors = rewrite_metadata_errors(rewrite)
            if metadata_errors:
                rejected.append(
                    {
                        "case_id": case_id,
                        "reject_reason": "metadata_gate_failed",
                        "static_errors": metadata_errors,
                        "rewrite": rewrite,
                        "generator_response": generation,
                    }
                )
                continue

            verifier_payload: Dict[str, Any] | None = None
            if not args.skip_llm_verification:
                try:
                    verifier_payload = client.chat_json(
                        system_prompt=verifier_prompt,
                        user_prompt=build_verify_prompt(
                            case=case,
                            rewrite=rewrite,
                            doc_snippets=doc_snippets,
                        ),
                    )
                except Exception as exc:
                    rejected.append(
                        {
                            "case_id": case_id,
                            "reject_reason": "verifier_error",
                            "error": str(exc),
                            "rewrite": rewrite,
                        }
                    )
                    continue
                if not verifier_accepts(
                    verifier_payload,
                    min_confidence=args.min_verifier_confidence,
                ):
                    rejected.append(
                        {
                            "case_id": case_id,
                            "reject_reason": "llm_verification_failed",
                            "verifier_response": verifier_payload,
                            "rewrite": rewrite,
                        }
                    )
                    continue
                verified += 1

            pair = make_pair_record(
                case=case,
                rewrite=rewrite,
                verifier_payload=verifier_payload,
                rewrite_profile="llm_cypher25_verified",
            )
            if pair["pair_id"] in seen_pair_ids:
                rejected.append(
                    {
                        "case_id": case_id,
                        "reject_reason": "duplicate_pair_id",
                        "pair_id": pair["pair_id"],
                        "rewrite": rewrite,
                    }
                )
                continue
            seen_pair_ids.add(pair["pair_id"])
            pairs.append(pair)
            rewrite_counts[pair["rewrite_type"]] += 1

            original_case = _make_selfdiff_case(
                case,
                pair_id=pair["pair_id"],
                rewrite_type=pair["rewrite_type"],
                variant="original",
                main_query=pair["original_main_query"],
                pair=pair,
            )
            rewrite_case = _make_selfdiff_case(
                case,
                pair_id=pair["pair_id"],
                rewrite_type=pair["rewrite_type"],
                variant="rewrite",
                main_query=pair["rewritten_main_query"],
                pair=pair,
            )
            rewritten_cases.append(rewrite_case)
            selfdiff_cases.append(original_case)
            selfdiff_cases.append(rewrite_case)

    write_jsonl(pairs, output_dir / "rewrite_pairs.jsonl")
    write_jsonl(rewritten_cases, output_dir / "rewritten_cases.jsonl")
    write_jsonl(selfdiff_cases, output_dir / "selfdiff_cases.jsonl")
    write_jsonl(rejected, output_dir / "rejected_rewrites.jsonl")

    summary: Dict[str, Any] = {
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "model_config": args.model_config,
        "task_name": args.task_name,
        "model": model_name,
        "doc_chunks": args.doc_chunks,
        "doc_snippets_loaded": len(doc_snippets),
        "processed_input_cases": processed,
        "proposed_rewrites": proposed,
        "verified_rewrites": verified if not args.skip_llm_verification else None,
        "accepted_rewrite_pairs": len(pairs),
        "rewritten_cases": len(rewritten_cases),
        "selfdiff_cases": len(selfdiff_cases),
        "rewrite_counts": dict(sorted(rewrite_counts.items())),
        "rejected_rewrites": len(rejected),
        "rejection_counts": summarize_rejections(rejected),
        "skip_llm_verification": bool(args.skip_llm_verification),
        "min_verifier_confidence": args.min_verifier_confidence,
        "allow_write_main": bool(args.allow_write_main),
    }
    dump_json(summary, output_dir / "summary.json", pretty=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
