from __future__ import annotations

import argparse
import json
import traceback
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from src.common.config import load_yaml
from src.common.io_utils import dump_json, ensure_dir
from src.common.jsonl_utils import append_jsonl, iter_jsonl, write_jsonl
from src.llm.model_client import create_model_client, resolve_model_config
from src.stage3.instantiate_cases import (
    FEATURE_PROMPT_FORMAT,
    heuristic_instantiate,
    llm_instantiate,
    load_instantiation_prompt,
)
from src.stage3.quality_gates import (
    attach_quality_gates,
    normalize_diversity_profile,
    normalize_generation_profile,
    query_fingerprint,
    summarize_quality,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Run Stage 3: instantiate concrete testcases from Stage 2 outputs')
    p.add_argument('--pipeline-config', default='configs/pipeline.yaml')
    p.add_argument('--model-config', default='configs/model.yaml')
    p.add_argument('--input', default='data/stage2/instantiation_inputs.jsonl')
    p.add_argument('--output-dir', default=None)
    p.add_argument('--mode', choices=['llm', 'heuristic'], default=None)
    p.add_argument('--max-items', type=int, default=None)
    p.add_argument('--variants-per-input', type=int, default=1)
    p.add_argument('--family-bias', choices=['none','bug_rich_memgraph','call_scope','union_projection','optional_agg','delete_return','projection_order'], default='none')
    p.add_argument('--complexity-profile', choices=['default','large_neo4j','planner_stress_neo4j','engine_stress_neo4j'], default='default')
    p.add_argument('--generation-profile', choices=['portable_crossdb','neo4j_semantic','neo4j_cypher25','neo4j_cypher25_metamorphic','neo4j_internal_tier','neo4j_engine_stress'], default='portable_crossdb')
    p.add_argument('--diversity-profile', choices=['balanced','semantic_edges','boundary_heavy','engine_stress'], default='balanced')
    p.add_argument('--require-doc-grounding', action='store_true')
    p.add_argument('--quality-retry-limit', type=int, default=1, help='Retry LLM generation when Stage3 quality gates reject a case')
    p.add_argument('--resume', action='store_true')
    p.add_argument(
        '--retry-errors',
        action='store_true',
        help='When resuming, keep successful/rejected variants but remove error variants from the completed set so they are retried.',
    )
    p.add_argument('--prompt', default='prompts/instantiate_case_system.txt')
    return p.parse_args()


def _load_existing_variants(path: Path) -> Set[Tuple[str, int]]:
    if not path.exists():
        return set()
    ids: Set[Tuple[str, int]] = set()
    for rec in iter_jsonl(path):
        query_id = rec.get('query_id')
        variant_index = rec.get('variant_index', 0)
        if query_id is not None:
            ids.add((str(query_id), int(variant_index or 0)))
    return ids


def _rewrite_records_for_completed_variants(path: Path, completed: Set[Tuple[str, int]]) -> None:
    if not path.exists():
        return
    kept: List[Dict[str, Any]] = []
    for rec in iter_jsonl(path):
        query_id = str(rec.get("query_id") or "")
        variant_index = int(rec.get("variant_index", 0) or 0)
        if (query_id, variant_index) in completed:
            kept.append(rec)
    write_jsonl(kept, path)


def main() -> int:
    args = parse_args()
    pipe_cfg = load_yaml(args.pipeline_config)
    model_cfg = load_yaml(args.model_config)
    output_dir = Path(args.output_dir or pipe_cfg.get('paths', {}).get('stage3_output', 'data/stage3'))
    ensure_dir(output_dir)
    stage3_cfg = pipe_cfg.get('stage3', {})
    mode = args.mode or stage3_cfg.get('mode', 'llm')
    max_items = args.max_items if args.max_items is not None else stage3_cfg.get('max_items')
    variants_per_input = max(1, int(args.variants_per_input or 1))
    family_bias = None if (args.family_bias or 'none') == 'none' else args.family_bias
    complexity_profile = str(args.complexity_profile or 'default')
    generation_profile = normalize_generation_profile(args.generation_profile)
    diversity_profile = normalize_diversity_profile(args.diversity_profile)
    require_doc_grounding = bool(args.require_doc_grounding)
    quality_retry_limit = max(0, int(args.quality_retry_limit or 0))
    resume = bool(args.resume or stage3_cfg.get('resume', False))
    prompt = load_instantiation_prompt(args.prompt)

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f'Instantiation input not found: {input_path}')

    client = create_model_client(model_cfg, task='instantiation') if mode == 'llm' else None
    model_name = resolve_model_config(model_cfg, task='instantiation').get('model') if mode == 'llm' else None

    out_cases = output_dir / 'instantiated_cases.jsonl'
    out_rejected = output_dir / 'rejected_cases.jsonl'
    out_errors = output_dir / 'errors.jsonl'
    out_progress = output_dir / 'completed_variants.jsonl'

    if not resume:
        for path in (out_cases, out_rejected, out_errors, out_progress, output_dir / "summary.json"):
            if path.exists():
                path.unlink()

    existing_ids = _load_existing_variants(out_progress) if resume else set()
    if resume:
        if args.retry_errors:
            error_ids = _load_existing_variants(out_errors)
            if error_ids:
                existing_ids -= error_ids
        _rewrite_records_for_completed_variants(out_cases, existing_ids)
        _rewrite_records_for_completed_variants(out_rejected, existing_ids)
        _rewrite_records_for_completed_variants(out_errors, existing_ids)
        _rewrite_records_for_completed_variants(out_progress, existing_ids)
    seen_fingerprints: Set[str] = set()
    if out_cases.exists():
        for existing_case in iter_jsonl(out_cases):
            seen_fingerprints.add(query_fingerprint(existing_case))

    processed = 0
    input_records = 0
    for rec in iter_jsonl(input_path):
        if max_items is not None and input_records >= max_items:
            break
        input_records += 1
        query_id = str(rec.get('query_id') or 'unknown')
        for variant_index in range(variants_per_input):
            if resume and (query_id, variant_index) in existing_ids:
                continue
            try:
                quality_attempts = 0
                while True:
                    rec_for_attempt = dict(rec)
                    if mode == 'llm' and quality_attempts > 0:
                        previous_errors = case.get('quality', {}).get('quality_gate_errors') or case.get('validation', {}).get('errors', [])
                        rec_for_attempt['quality_retry_limit'] = quality_retry_limit
                        rec_for_attempt['stage3_quality_retry_errors'] = [str(err) for err in previous_errors]
                        rec_for_attempt['stage3_quality_retry_attempt'] = quality_attempts

                    if mode == 'llm':
                        case = llm_instantiate(
                            rec_for_attempt,
                            client=client,  # type: ignore[arg-type]
                            system_prompt=prompt,
                            model_name=model_name,
                            variant_index=variant_index,
                            total_variants=variants_per_input,
                            family_bias=family_bias,
                            complexity_profile=complexity_profile,
                            generation_profile=generation_profile,
                            diversity_profile=diversity_profile,
                        )
                    else:
                        case = heuristic_instantiate(
                            rec_for_attempt,
                            variant_index=variant_index,
                            total_variants=variants_per_input,
                            family_bias=family_bias,
                            complexity_profile=complexity_profile,
                            generation_profile=generation_profile,
                            diversity_profile=diversity_profile,
                        )
                    case = attach_quality_gates(
                        case,
                        rec,
                        generation_profile=generation_profile,
                        diversity_profile=diversity_profile,
                        require_doc_grounding=require_doc_grounding,
                        seen_fingerprints=seen_fingerprints,
                    )
                    case['quality_retry_attempts'] = quality_attempts
                    quality_errors = case.get('quality', {}).get('quality_gate_errors') or []
                    if mode != 'llm' or case.get('validation', {}).get('is_valid') or not quality_errors or quality_attempts >= quality_retry_limit:
                        break
                    quality_attempts += 1

                if case.get('validation', {}).get('is_valid'):
                    append_jsonl(case, out_cases)
                    seen_fingerprints.add(str(case.get('quality', {}).get('fingerprint') or query_fingerprint(case)))
                else:
                    append_jsonl(case, out_rejected)
                append_jsonl({'query_id': query_id, 'variant_index': variant_index}, out_progress)
                existing_ids.add((query_id, variant_index))
                processed += 1
                print(
                    f"[Stage3] processed {len(existing_ids)}: {query_id} "
                    f"(variant {variant_index + 1}/{variants_per_input}) | "
                    f"valid={case.get('validation', {}).get('is_valid')}"
                )
            except Exception as e:
                append_jsonl({
                    'query_id': query_id,
                    'variant_index': variant_index,
                    'error': str(e),
                    'traceback': traceback.format_exc(),
                }, out_errors)
                append_jsonl({'query_id': query_id, 'variant_index': variant_index}, out_progress)
                existing_ids.add((query_id, variant_index))
                processed += 1
                print(
                    f"[Stage3] processed {len(existing_ids)}: {query_id} "
                    f"(variant {variant_index + 1}/{variants_per_input}) | error={e}"
                )

    all_cases = list(iter_jsonl(out_cases)) if out_cases.exists() else []
    all_rejected = list(iter_jsonl(out_rejected)) if out_rejected.exists() else []
    all_errors = list(iter_jsonl(out_errors)) if out_errors.exists() else []
    all_quality_records = all_cases + all_rejected
    quality_summary = summarize_quality(all_quality_records)
    validation_error_counts: Counter[str] = Counter()
    for rec in all_rejected:
        validation_error_counts.update(str(err) for err in rec.get('validation', {}).get('errors', []) or [])

    summary = {
        'input_path': str(input_path),
        'output_dir': str(output_dir),
        'mode': mode,
        'model': model_name,
        'input_records': input_records,
        'variants_per_input': variants_per_input,
        'family_bias': family_bias,
        'complexity_profile': complexity_profile,
        'generation_profile': generation_profile,
        'diversity_profile': diversity_profile,
        'feature_prompt_format': FEATURE_PROMPT_FORMAT,
        'require_doc_grounding': require_doc_grounding,
        'quality_retry_limit': quality_retry_limit,
        'processed': len(existing_ids),
        'processed_this_run': processed,
        'valid_cases': len(all_cases),
        'rejected_cases': len(all_rejected),
        'errors': len(all_errors),
        'quality_summary': quality_summary,
        'validation_error_counts': dict(sorted(validation_error_counts.items())),
    }
    dump_json(summary, output_dir / 'summary.json', pretty=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
