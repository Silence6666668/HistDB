# HistDB method

Use Python 3.12 and install `requirements.txt`. Set `DEEPSEEK_API_KEY` for live
generation. The model service and task settings are in `configs/model.yaml`.
The included code entry points and their dependencies are listed in `CODE_SCOPE.json`.

## 1. Historical representation

Prepare a JSONL file of historical query records containing `query_id`,
`source_id`, and `query`. Additional source metadata can be retained.

```bash
python -m src.cli.run_stage1 --input prepared_queries.jsonl --output-dir data/stage1 --skeleton-mode llm --feature-mode llm
```

The command writes the skeleton and feature libraries. The paper's frozen
libraries are included as `.jsonl.gz` files in `artifact_data/historical_knowledge/`.
Decompress the chosen library files before supplying them to Stage 2.

## 2. Skeleton-conditioned feature selection and retrieval

```bash
python -m src.cli.run_stage2 --skeletons data/stage1/skeletons.jsonl --features data/stage1/features.jsonl --feature-selection random_constrained --feature-sample-size 3 --feature-seed 42 --output-dir data/stage2
```

`src/stage2/feature_selection.py` implements structural support and weighted
sampling. Retrieval construction, grouping, and system filtering are in the
other `src/stage2/` modules. Supply official-documentation chunks or other
retrieval material with `--extra-fragments`.

## 3. Joint setup-query construction and bounded retry

```bash
python -m src.cli.run_stage3 --input data/stage2/instantiation_inputs.jsonl --output-dir data/stage3 --mode llm --quality-retry-limit 2
```

The system prompt is `prompts/instantiate_case_system.txt`. Record checks,
target-specific quality filters, and rejection-guided retries are implemented
in `src/stage3/` and `src/cli/run_stage3.py`.

W/S generation uses `feature-driven-soft`: both receive the guidance skeleton
and may adjust its structure to realize the selected features. Their difference
is feature sourcing. The shared system prompt contains only general construction
requirements; each user message supplies the applicable structural guidance.

## 4. Execution and comparison

Configure isolated DBMS endpoints in `configs/db_targets.yaml`, and set the
corresponding password environment variables before execution.

```bash
python -m src.cli.run_stage4 --input data/stage3/instantiated_cases.jsonl --db-config configs/db_targets.yaml --output-dir data/stage4
python -m src.cli.run_stage45 --input data/stage4/execution_results.jsonl --output-dir data/stage45
```

`src/stage4/` supplies the target adapters and execution records.
`src/reproduction/normalize.py` and `oracles.py` supply the validation comparator.
The reviewed case runner uses these through `python -m src.cli.reproduce`;
see `docs/CATALOG.md`.

## 5. Equivalent-query oracle and reduction

```bash
python -m src.cli.run_stage3b_llm_cypher25_rewrite --input data/stage3/instantiated_cases.jsonl --output-dir data/rewrites --doc-chunks official_chunks.jsonl
python -m src.cli.run_stage4b_selfdiff --input data/rewrites/selfdiff_cases.jsonl --db-config configs/db_targets.yaml --targets repro_neo4j --output-dir data/selfdiff
python -m src.cli.run_stage45b_selfdiff --input data/selfdiff/execution_results.jsonl --output-dir data/selfdiff_comparison
```

The transformation prompts and checks are in `prompts/cypher25_llm_rewrite*`
and `src/stage3/llm_cypher25_rewrite.py`. `scripts/minimize_candidates.py`
contains the failure-preserving reduction routines and imports its comparison
helpers from the accompanying review modules. Use its `--help` for candidate
and target options. Final validation and issue reporting require manual review.

## Evaluation

```bash
python scripts/reproduce_archived_results.py all
```

This recomputes the archived RQ2 and RQ3 statistics and checks all 148 cases.
The retained RQ2 paired-generation runner is
`scripts/run_rq2_ws_paired_completion.py`; the feature-count runner is
`scripts/run_rq2_feature_count_sensitivity.py`. Their shared input-selection,
sharding, and job helpers are included as dependencies. Source-run defaults
remain in those archived experiment runners; the packaged corpora are under
`artifact_data/` for offline reproduction.
