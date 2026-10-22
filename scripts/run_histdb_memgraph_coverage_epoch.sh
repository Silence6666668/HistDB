#!/usr/bin/env bash
set -euo pipefail

CASES="${FULL_METHOD_CASES:?FULL_METHOD_CASES is required}"
DB_CONFIG="${FULL_METHOD_DB_CONFIG:-configs/db_targets_memgraph_source_coverage_7787.yaml}"
TARGET="${FULL_METHOD_TARGET:-memgraph_source_coverage}"
CASES_PER_EPOCH="${FULL_METHOD_CASES_PER_EPOCH:-1000}"
INNER_MAX_SECONDS="${FULL_METHOD_INNER_MAX_SECONDS:-120}"
QUERY_TIMEOUT_SEC="${FULL_METHOD_QUERY_TIMEOUT_SEC:-8}"
CASE_HARD_TIMEOUT_SEC="${FULL_METHOD_CASE_HARD_TIMEOUT_SEC:-12}"
FETCH_LIMIT="${FULL_METHOD_FETCH_LIMIT:-0}"

RUN_DIR="${MEMGRAPH_RUN_DIR:?MEMGRAPH_RUN_DIR is required}"
EPOCH_LABEL="${MEMGRAPH_EPOCH_LABEL:?MEMGRAPH_EPOCH_LABEL is required}"

offset_file="$RUN_DIR/full_method_offset.txt"
total_cases="$(wc -l < "$CASES" | tr -d '[:space:]')"
if [[ "$total_cases" -le 0 ]]; then
  echo "No cases in $CASES" >&2
  exit 2
fi

offset=0
if [[ -s "$offset_file" ]]; then
  offset="$(cat "$offset_file")"
fi
if [[ "$offset" -ge "$total_cases" ]]; then
  offset=0
fi

output_dir="$RUN_DIR/full_method/$EPOCH_LABEL"
mkdir -p "$output_dir"

set +e
timeout --preserve-status --kill-after=10s "${INNER_MAX_SECONDS}s" \
  python3 scripts/run_stage4_coverage_only.py \
    --input "$CASES" \
    --db-config "$DB_CONFIG" \
    --targets "$TARGET" \
    --output-dir "$output_dir" \
    --skip-items "$offset" \
    --max-items "$CASES_PER_EPOCH" \
    --query-timeout-sec "$QUERY_TIMEOUT_SEC" \
    --case-hard-timeout-sec "$CASE_HARD_TIMEOUT_SEC" \
    --fetch-limit "$FETCH_LIMIT"
tool_exit=$?
set -e

next_offset=$((offset + CASES_PER_EPOCH))
if [[ "$next_offset" -ge "$total_cases" ]]; then
  next_offset=0
fi
printf '%s\n' "$next_offset" > "$offset_file"
printf '{"epoch":"%s","offset":%s,"nextOffset":%s,"totalCases":%s,"casesPerEpoch":%s}\n' \
  "$EPOCH_LABEL" "$offset" "$next_offset" "$total_cases" "$CASES_PER_EPOCH" \
  >> "$RUN_DIR/full_method_offsets.jsonl"

exit "$tool_exit"
