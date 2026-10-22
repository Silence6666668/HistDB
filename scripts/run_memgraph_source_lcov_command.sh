#!/usr/bin/env bash
set -euo pipefail

# Run an arbitrary Memgraph-testing command against a source-instrumented
# Memgraph binary and collect cumulative lcov snapshots over time.
#
# The command is restarted once per epoch. This keeps coverage sampling simple:
# start Memgraph, run the tool for one epoch, stop the tool, gracefully stop
# Memgraph so .gcda files are flushed, collect lcov, then continue.

CONFIG_PATH="configs/server_ubuntu_layout.env"
RUN_NAME=""
TOOL_LABEL="custom"
TOOL_COMMAND=""
MEMGRAPH_BIN=""
SOURCE_ROOT=""
BUILD_DIR=""
OUTPUT_ROOT=""
BOLT_PORT=""
BUDGET_SECONDS=""
EPOCH_SECONDS=""
READY_TIMEOUT_SECONDS=120
STOP_TIMEOUT_SECONDS=30
QUERY_TIMEOUT_SEC=20
STORAGE_TIMEOUT_SEC=60
EXTRA_MEMGRAPH_ARGS=()
ZERO_COUNTERS=1
GENERATE_HTML=0
KEEP_MEMGRAPH_DATA=0
KEEP_RAW_INFO=0
SUMMARY_ONLY="${MEMGRAPH_COVERAGE_SUMMARY_ONLY:-0}"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/run_memgraph_source_lcov_command.sh --run-name NAME --command CMD [options]

Required:
  --run-name NAME          Run name under MEMGRAPH_COVERAGE_ROOT.
  --command CMD            Shell command to run for each epoch.

Options:
  --config PATH            Layout config. Default: configs/server_ubuntu_layout.env
  --tool-label LABEL       Tool/method label stored in manifest. Default: custom
  --memgraph-bin PATH      Instrumented memgraph binary. Default: MEMGRAPH_BIN or auto-detect.
  --source-root DIR        Memgraph source checkout. Default: MEMGRAPH_SOURCE_ROOT.
  --build-dir DIR          Memgraph coverage build directory. Default: MEMGRAPH_BUILD_ROOT.
  --output-root DIR        Memgraph coverage root. Default: MEMGRAPH_COVERAGE_ROOT.
  --bolt-port PORT         Memgraph Bolt port. Default: MEMGRAPH_BOLT_PORT or 7687.
  --budget-seconds SEC     Total budget. Default: EXPERIMENT_BUDGET_SECONDS or 86400.
  --epoch-seconds SEC      Epoch length. Default: MEMGRAPH_COVERAGE_EPOCH_SECONDS or 300.
  --ready-timeout SEC      Memgraph startup timeout. Default: 120.
  --stop-timeout SEC       Graceful stop timeout. Default: 30.
  --query-timeout-sec SEC  Memgraph query execution timeout. Default: 20.
  --storage-timeout-sec SEC Memgraph storage access timeout. Default: 60.
  --memgraph-arg ARG       Extra Memgraph argument. Can be repeated.
  --no-zero-counters       Do not zero lcov counters before the first epoch.
  --html                   Generate genhtml output for each epoch.
  --keep-memgraph-data     Keep per-epoch Memgraph data directories.
  --keep-raw-info          Keep raw/intermediate lcov info files.
  --summary-only           Keep lcov summaries but drop final per-epoch .info files.
  -h, --help               Show this help.

Environment passed to CMD:
  MEMGRAPH_HOST, MEMGRAPH_PORT, MEMGRAPH_RUN_DIR, MEMGRAPH_EPOCH_INDEX,
  MEMGRAPH_EPOCH_LABEL, MEMGRAPH_EPOCH_SECONDS, MEMGRAPH_TOOL_LABEL
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG_PATH="${2:?missing config path}"; shift 2 ;;
    --run-name) RUN_NAME="${2:?missing run name}"; shift 2 ;;
    --tool-label) TOOL_LABEL="${2:?missing tool label}"; shift 2 ;;
    --command) TOOL_COMMAND="${2:?missing command}"; shift 2 ;;
    --memgraph-bin) MEMGRAPH_BIN="${2:?missing memgraph binary}"; shift 2 ;;
    --source-root) SOURCE_ROOT="${2:?missing source root}"; shift 2 ;;
    --build-dir) BUILD_DIR="${2:?missing build dir}"; shift 2 ;;
    --output-root) OUTPUT_ROOT="${2:?missing output root}"; shift 2 ;;
    --bolt-port) BOLT_PORT="${2:?missing bolt port}"; shift 2 ;;
    --budget-seconds) BUDGET_SECONDS="${2:?missing budget seconds}"; shift 2 ;;
    --epoch-seconds) EPOCH_SECONDS="${2:?missing epoch seconds}"; shift 2 ;;
    --ready-timeout) READY_TIMEOUT_SECONDS="${2:?missing ready timeout}"; shift 2 ;;
    --stop-timeout) STOP_TIMEOUT_SECONDS="${2:?missing stop timeout}"; shift 2 ;;
    --query-timeout-sec) QUERY_TIMEOUT_SEC="${2:?missing query timeout}"; shift 2 ;;
    --storage-timeout-sec) STORAGE_TIMEOUT_SEC="${2:?missing storage timeout}"; shift 2 ;;
    --memgraph-arg) EXTRA_MEMGRAPH_ARGS+=("${2:?missing memgraph arg}"); shift 2 ;;
    --no-zero-counters) ZERO_COUNTERS=0; shift ;;
    --html) GENERATE_HTML=1; shift ;;
    --keep-memgraph-data) KEEP_MEMGRAPH_DATA=1; shift ;;
    --keep-raw-info) KEEP_RAW_INFO=1; shift ;;
    --summary-only) SUMMARY_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$RUN_NAME" || -z "$TOOL_COMMAND" ]]; then
  usage >&2
  exit 2
fi

if [[ -f "$CONFIG_PATH" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_PATH"
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_ROOT="${SOURCE_ROOT:-${MEMGRAPH_SOURCE_ROOT:-}}"
BUILD_DIR="${BUILD_DIR:-${MEMGRAPH_BUILD_ROOT:-}}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${MEMGRAPH_COVERAGE_ROOT:-$repo_root/coverage_runs/memgraph}}"
BOLT_PORT="${BOLT_PORT:-${MEMGRAPH_BOLT_PORT:-7687}}"
BUDGET_SECONDS="${BUDGET_SECONDS:-${EXPERIMENT_BUDGET_SECONDS:-86400}}"
EPOCH_SECONDS="${EPOCH_SECONDS:-${MEMGRAPH_COVERAGE_EPOCH_SECONDS:-300}}"

if [[ -z "$MEMGRAPH_BIN" ]]; then
  for candidate in \
    "${BUILD_DIR:-}/memgraph" \
    "${BUILD_DIR:-}/src/memgraph" \
    "${BUILD_DIR:-}/src/memgraph/memgraph" \
    "${BUILD_DIR:-}/bin/memgraph" \
    "${SOURCE_ROOT:-}/build/memgraph" \
    "${SOURCE_ROOT:-}/build/src/memgraph" \
    "${SOURCE_ROOT:-}/build/bin/memgraph"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      MEMGRAPH_BIN="$candidate"
      break
    fi
  done
fi

if [[ -z "$MEMGRAPH_BIN" || ! -x "$MEMGRAPH_BIN" ]]; then
  echo "Instrumented Memgraph binary not found or not executable. Pass --memgraph-bin." >&2
  exit 2
fi
if [[ -z "$BUILD_DIR" || ! -d "$BUILD_DIR" ]]; then
  echo "Memgraph build directory not found. Pass --build-dir or set MEMGRAPH_BUILD_ROOT." >&2
  exit 2
fi

run_dir="$OUTPUT_ROOT/$RUN_NAME"
log_dir="$run_dir/logs"
memgraph_data_root="$run_dir/memgraph_data"
mkdir -p "$log_dir" "$memgraph_data_root"

manifest="$run_dir/memgraph_source_lcov_run_manifest.jsonl"
status_path="$run_dir/status.json"
summary_path="$run_dir/summary.json"

write_json() {
  local path="$1"
  local payload="$2"
  printf '%s\n' "$payload" > "$path"
}

append_manifest() {
  local payload="$1"
  printf '%s\n' "$payload" >> "$manifest"
}

wait_for_port() {
  local port="$1"
  local timeout="$2"
  local deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if command -v nc >/dev/null 2>&1 && nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
      return 0
    fi
    if (echo >"/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_bolt_ready() {
  local port="$1"
  local timeout="$2"
  local deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if python3 - "$port" >/dev/null 2>&1 <<'PY'
import sys
from neo4j import GraphDatabase

port = sys.argv[1]
driver = GraphDatabase.driver(
    f"bolt://127.0.0.1:{port}",
    auth=("", ""),
    connection_timeout=2,
    connection_acquisition_timeout=2,
)
try:
    with driver.session() as session:
        session.run("RETURN 1").consume()
finally:
    driver.close()
PY
    then
      return 0
    fi
    sleep 1
  done
  return 1
}

stop_pid_gracefully() {
  local pid="$1"
  local timeout="$2"
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi
  kill -TERM "$pid" >/dev/null 2>&1 || true
  local deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      wait "$pid" >/dev/null 2>&1 || true
      return 0
    fi
    sleep 1
  done
  kill -KILL "$pid" >/dev/null 2>&1 || true
  wait "$pid" >/dev/null 2>&1 || true
}

collect_coverage() {
  local epoch_label="$1"
  local html_args=("--no-html")
  local retention_args=()
  if [[ "$GENERATE_HTML" -eq 1 ]]; then
    html_args=()
  fi
  case "${SUMMARY_ONLY,,}" in
    1|true|yes|on) retention_args=(--drop-final-info) ;;
  esac
  bash "$repo_root/scripts/collect_memgraph_lcov_report.sh" \
    --config "$CONFIG_PATH" \
    --run-name "$RUN_NAME" \
    --epoch "$epoch_label" \
    --source-root "$SOURCE_ROOT" \
    --build-dir "$BUILD_DIR" \
    --output-root "$OUTPUT_ROOT" \
    "${html_args[@]}" \
    "${retention_args[@]}" \
    > "$log_dir/${epoch_label}_lcov.stdout.log" \
    2> "$log_dir/${epoch_label}_lcov.stderr.log"
}

cleanup_epoch_artifacts() {
  local epoch_label="$1"
  local epoch_data_dir="$2"

  if [[ "$KEEP_RAW_INFO" -eq 0 ]]; then
    rm -f \
      "$run_dir/info/${epoch_label}.raw.info" \
      "$run_dir/info/${epoch_label}.source.tmp.info"
  fi

  if [[ "$KEEP_MEMGRAPH_DATA" -eq 0 ]]; then
    case "$epoch_data_dir" in
      "$memgraph_data_root"/*)
        rm -rf -- "$epoch_data_dir"
        ;;
      *)
        echo "Refusing to remove unexpected Memgraph data path: $epoch_data_dir" >&2
        ;;
    esac
  fi
}

if [[ "$ZERO_COUNTERS" -eq 1 ]]; then
  bash "$repo_root/scripts/collect_memgraph_lcov_report.sh" \
    --config "$CONFIG_PATH" \
    --build-dir "$BUILD_DIR" \
    --zero-counters \
    > "$log_dir/zero_counters.stdout.log" \
    2> "$log_dir/zero_counters.stderr.log"
fi

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
append_manifest "{\"event\":\"run_started\",\"timestamp\":\"$started_at\",\"runName\":\"$RUN_NAME\",\"toolLabel\":\"$TOOL_LABEL\",\"memgraphBin\":\"$MEMGRAPH_BIN\",\"buildDir\":\"$BUILD_DIR\",\"sourceRoot\":\"$SOURCE_ROOT\",\"boltPort\":$BOLT_PORT,\"budgetSeconds\":$BUDGET_SECONDS,\"epochSeconds\":$EPOCH_SECONDS,\"keepMemgraphData\":$KEEP_MEMGRAPH_DATA,\"keepRawInfo\":$KEEP_RAW_INFO,\"summaryOnly\":\"$SUMMARY_ONLY\"}"

deadline=$((SECONDS + BUDGET_SECONDS))
epoch_index=0
completed_epochs=0
failed_epochs=0
last_tool_exit=""

while (( SECONDS < deadline )); do
  epoch_index=$((epoch_index + 1))
  epoch_label="$(printf 'epoch_%04d' "$epoch_index")"
  epoch_started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  epoch_data_dir="$memgraph_data_root/$epoch_label"
  mkdir -p "$epoch_data_dir"

  remaining=$((deadline - SECONDS))
  if (( remaining <= 0 )); then
    break
  fi
  epoch_budget="$EPOCH_SECONDS"
  if (( remaining < epoch_budget )); then
    epoch_budget="$remaining"
  fi

  memgraph_stdout="$log_dir/${epoch_label}_memgraph.stdout.log"
  memgraph_stderr="$log_dir/${epoch_label}_memgraph.stderr.log"
  tool_stdout="$log_dir/${epoch_label}_${TOOL_LABEL}.stdout.log"
  tool_stderr="$log_dir/${epoch_label}_${TOOL_LABEL}.stderr.log"

  "$MEMGRAPH_BIN" \
    --bolt-address=0.0.0.0 \
    --bolt-port="$BOLT_PORT" \
    --data-directory="$epoch_data_dir" \
    --storage-properties-on-edges=true \
    --query-plan-cache-max-size=0 \
    --storage-enable-schema-metadata=true \
    --query-execution-timeout-sec="$QUERY_TIMEOUT_SEC" \
    --storage-access-timeout-sec="$STORAGE_TIMEOUT_SEC" \
    "${EXTRA_MEMGRAPH_ARGS[@]}" \
    > "$memgraph_stdout" \
    2> "$memgraph_stderr" &
  memgraph_pid=$!

  if ! wait_for_port "$BOLT_PORT" "$READY_TIMEOUT_SECONDS"; then
    failed_epochs=$((failed_epochs + 1))
    append_manifest "{\"event\":\"epoch_failed\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"epoch\":\"$epoch_label\",\"reason\":\"memgraph_not_ready\",\"memgraphPid\":$memgraph_pid}"
    stop_pid_gracefully "$memgraph_pid" "$STOP_TIMEOUT_SECONDS"
    continue
  fi
  if ! wait_for_bolt_ready "$BOLT_PORT" "$READY_TIMEOUT_SECONDS"; then
    failed_epochs=$((failed_epochs + 1))
    append_manifest "{\"event\":\"epoch_failed\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"epoch\":\"$epoch_label\",\"reason\":\"memgraph_bolt_not_ready\",\"memgraphPid\":$memgraph_pid}"
    stop_pid_gracefully "$memgraph_pid" "$STOP_TIMEOUT_SECONDS"
    continue
  fi

  export MEMGRAPH_HOST="localhost"
  export MEMGRAPH_PORT="$BOLT_PORT"
  export MEMGRAPH_RUN_DIR="$run_dir"
  export MEMGRAPH_EPOCH_INDEX="$epoch_index"
  export MEMGRAPH_EPOCH_LABEL="$epoch_label"
  export MEMGRAPH_EPOCH_SECONDS="$epoch_budget"
  export MEMGRAPH_TOOL_LABEL="$TOOL_LABEL"

  set +e
  timeout --preserve-status --kill-after=10s "${epoch_budget}s" \
    bash -lc "$TOOL_COMMAND" \
    > "$tool_stdout" \
    2> "$tool_stderr"
  tool_exit=$?
  set -e
  last_tool_exit="$tool_exit"

  stop_pid_gracefully "$memgraph_pid" "$STOP_TIMEOUT_SECONDS"
  collect_coverage "$epoch_label"
  cleanup_epoch_artifacts "$epoch_label" "$epoch_data_dir"

  completed_epochs=$((completed_epochs + 1))
  epoch_ended="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  append_manifest "{\"event\":\"epoch_completed\",\"timestamp\":\"$epoch_ended\",\"epoch\":\"$epoch_label\",\"epochIndex\":$epoch_index,\"epochStarted\":\"$epoch_started\",\"toolExitCode\":$tool_exit,\"epochBudgetSeconds\":$epoch_budget,\"memgraphDataDir\":\"$epoch_data_dir\",\"toolStdout\":\"$tool_stdout\",\"toolStderr\":\"$tool_stderr\"}"

  write_json "$status_path" "{\"status\":\"running\",\"runName\":\"$RUN_NAME\",\"toolLabel\":\"$TOOL_LABEL\",\"completedEpochs\":$completed_epochs,\"failedEpochs\":$failed_epochs,\"lastEpoch\":\"$epoch_label\",\"lastToolExitCode\":\"$last_tool_exit\",\"updatedAt\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
done

ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
append_manifest "{\"event\":\"run_finished\",\"timestamp\":\"$ended_at\",\"runName\":\"$RUN_NAME\",\"toolLabel\":\"$TOOL_LABEL\",\"completedEpochs\":$completed_epochs,\"failedEpochs\":$failed_epochs,\"lastToolExitCode\":\"$last_tool_exit\"}"

write_json "$summary_path" "{\"status\":\"completed\",\"runName\":\"$RUN_NAME\",\"toolLabel\":\"$TOOL_LABEL\",\"startedAt\":\"$started_at\",\"endedAt\":\"$ended_at\",\"completedEpochs\":$completed_epochs,\"failedEpochs\":$failed_epochs,\"lastToolExitCode\":\"$last_tool_exit\",\"runDir\":\"$run_dir\",\"manifest\":\"$manifest\"}"
write_json "$status_path" "{\"status\":\"completed\",\"runName\":\"$RUN_NAME\",\"toolLabel\":\"$TOOL_LABEL\",\"completedEpochs\":$completed_epochs,\"failedEpochs\":$failed_epochs,\"summary\":\"$summary_path\",\"updatedAt\":\"$ended_at\"}"

echo "Memgraph source coverage run completed: $run_dir"
