#!/usr/bin/env bash
set -euo pipefail

# Capture source-level line, function, and branch coverage for an instrumented
# Memgraph build.
# Call this after gracefully stopping the Memgraph process so .gcda files are
# flushed. For time-series plots, call it once per epoch without zeroing counters.

CONFIG_PATH="configs/server_ubuntu_layout.env"
RUN_NAME=""
EPOCH_LABEL="$(date -u +%Y%m%dT%H%M%SZ)"
SOURCE_ROOT=""
BUILD_DIR=""
OUTPUT_ROOT=""
GENERATE_HTML=1
ZERO_COUNTERS=0
DROP_FINAL_INFO=0
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
GCOV_TOOL="${MEMGRAPH_GCOV_TOOL:-}"
if [[ -z "$GCOV_TOOL" && -x /opt/toolchain-v7/bin/llvm-cov ]]; then
  GCOV_TOOL="$repo_root/scripts/llvm-cov-gcov-wrapper.sh"
fi
GCOV_ARGS=()
if [[ -n "$GCOV_TOOL" ]]; then
  GCOV_ARGS=(--gcov-tool "$GCOV_TOOL")
fi
LCOV_BRANCH_ARGS=(--rc lcov_branch_coverage=1)
LCOV_IGNORE_ARGS=()

lcov_supports_option() {
  command -v lcov >/dev/null 2>&1 && lcov --help 2>&1 | grep -q -- "$1"
}

lcov_supports_error_category() {
  command -v lcov >/dev/null 2>&1 && lcov --help 2>&1 | grep -q -- "$1"
}

if lcov_supports_option "--keep-going"; then
  LCOV_IGNORE_ARGS+=(--keep-going)
fi
if lcov_supports_option "--ignore-errors"; then
  LCOV_IGNORE_ARGS+=(--ignore-errors gcov,source,graph)
  if lcov_supports_error_category "inconsistent"; then
    LCOV_IGNORE_ARGS+=(--ignore-errors inconsistent,inconsistent,inconsistent,inconsistent,inconsistent,inconsistent,inconsistent,inconsistent,inconsistent,inconsistent)
  fi
  if lcov_supports_error_category "unused"; then
    LCOV_IGNORE_ARGS+=(--ignore-errors unused,unused)
  fi
fi

usage() {
  cat <<'EOF'
Usage: bash scripts/collect_memgraph_lcov_report.sh --run-name NAME [options]

Options:
  --config PATH       Layout config. Default: configs/server_ubuntu_layout.env
  --run-name NAME     Required. Coverage run name under MEMGRAPH_COVERAGE_ROOT.
  --epoch LABEL       Snapshot label. Default: UTC timestamp.
  --source-root DIR   Memgraph source checkout. Default: MEMGRAPH_SOURCE_ROOT.
  --build-dir DIR     Instrumented build directory. Default: MEMGRAPH_BUILD_ROOT.
  --output-root DIR   Memgraph coverage root. Default: MEMGRAPH_COVERAGE_ROOT.
  --no-html           Skip genhtml output.
  --zero-counters     Zero coverage counters in the build directory and exit.
  --drop-final-info   Keep only the summary/html outputs and remove final .info.
  -h, --help          Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG_PATH="${2:?missing config path}"; shift 2 ;;
    --run-name) RUN_NAME="${2:?missing run name}"; shift 2 ;;
    --epoch) EPOCH_LABEL="${2:?missing epoch label}"; shift 2 ;;
    --source-root) SOURCE_ROOT="${2:?missing source root}"; shift 2 ;;
    --build-dir) BUILD_DIR="${2:?missing build dir}"; shift 2 ;;
    --output-root) OUTPUT_ROOT="${2:?missing output root}"; shift 2 ;;
    --no-html) GENERATE_HTML=0; shift ;;
    --zero-counters) ZERO_COUNTERS=1; shift ;;
    --drop-final-info) DROP_FINAL_INFO=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -f "$CONFIG_PATH" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_PATH"
fi

SOURCE_ROOT="${SOURCE_ROOT:-${MEMGRAPH_SOURCE_ROOT:-}}"
BUILD_DIR="${BUILD_DIR:-${MEMGRAPH_BUILD_ROOT:-}}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${MEMGRAPH_COVERAGE_ROOT:-coverage_runs/memgraph}}"

if [[ -z "$RUN_NAME" && "$ZERO_COUNTERS" -eq 0 ]]; then
  echo "--run-name is required unless --zero-counters is used." >&2
  usage
  exit 2
fi

if [[ -z "$BUILD_DIR" ]]; then
  echo "Build directory is required. Pass --build-dir or set MEMGRAPH_BUILD_ROOT." >&2
  exit 2
fi

if ! command -v lcov >/dev/null 2>&1; then
  echo "lcov not found. Install it with: sudo apt-get install -y lcov" >&2
  exit 1
fi

if [[ "$ZERO_COUNTERS" -eq 1 ]]; then
  lcov --zerocounters --directory "$BUILD_DIR"
  echo "Zeroed coverage counters under $BUILD_DIR"
  exit 0
fi

RUN_DIR="$OUTPUT_ROOT/$RUN_NAME"
INFO_DIR="$RUN_DIR/info"
SUMMARY_DIR="$RUN_DIR/summary"
HTML_DIR="$RUN_DIR/html/$EPOCH_LABEL"
mkdir -p "$INFO_DIR" "$SUMMARY_DIR"

RAW_INFO="$INFO_DIR/${EPOCH_LABEL}.raw.info"
SOURCE_INFO_TMP="$INFO_DIR/${EPOCH_LABEL}.source.tmp.info"
FINAL_INFO="$INFO_DIR/${EPOCH_LABEL}.info"
SUMMARY_FILE="$SUMMARY_DIR/${EPOCH_LABEL}.summary.txt"
MANIFEST_FILE="$RUN_DIR/memgraph_lcov_manifest.jsonl"

lcov --capture \
  --directory "$BUILD_DIR" \
  --output-file "$RAW_INFO" \
  --rc geninfo_unexecuted_blocks=1 \
  "${LCOV_BRANCH_ARGS[@]}" \
  "${GCOV_ARGS[@]}" \
  "${LCOV_IGNORE_ARGS[@]}"

if [[ -n "$SOURCE_ROOT" && -d "$SOURCE_ROOT" ]]; then
  if lcov --extract "$RAW_INFO" "$SOURCE_ROOT/*" --output-file "$SOURCE_INFO_TMP" \
    "${LCOV_BRANCH_ARGS[@]}" "${LCOV_IGNORE_ARGS[@]}"; then
    :
  else
    echo "Warning: lcov extract found no records under $SOURCE_ROOT; using raw capture." >&2
    cp "$RAW_INFO" "$SOURCE_INFO_TMP"
  fi
else
  echo "Warning: source root not set or missing; using raw capture." >&2
  cp "$RAW_INFO" "$SOURCE_INFO_TMP"
fi

lcov --remove "$SOURCE_INFO_TMP" \
  "*/third_party/*" \
  "*/external/*" \
  "*/build/*" \
  "*/tests/*" \
  "/usr/*" \
  --output-file "$FINAL_INFO" \
  "${LCOV_BRANCH_ARGS[@]}" \
  "${LCOV_IGNORE_ARGS[@]}"

lcov --summary "$FINAL_INFO" \
  "${LCOV_BRANCH_ARGS[@]}" \
  "${LCOV_IGNORE_ARGS[@]}" | tee "$SUMMARY_FILE"

if [[ "$GENERATE_HTML" -eq 1 ]]; then
  if ! command -v genhtml >/dev/null 2>&1; then
    echo "genhtml not found; skipping HTML output." >&2
  else
    mkdir -p "$HTML_DIR"
    genhtml "$FINAL_INFO" --branch-coverage --output-directory "$HTML_DIR" >/dev/null
  fi
fi

FINAL_INFO_RETAINED=true
if [[ "$DROP_FINAL_INFO" -eq 1 ]]; then
  rm -f "$FINAL_INFO"
  FINAL_INFO_RETAINED=false
fi

if [[ "$FINAL_INFO_RETAINED" == true ]]; then
  cat >> "$MANIFEST_FILE" <<EOF
{"timestamp":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","runName":"$RUN_NAME","epoch":"$EPOCH_LABEL","sourceRoot":"$SOURCE_ROOT","buildDir":"$BUILD_DIR","branchCoverage":true,"rawInfo":"$RAW_INFO","finalInfo":"$FINAL_INFO","finalInfoRetained":true,"summary":"$SUMMARY_FILE","html":"$HTML_DIR"}
EOF
else
  cat >> "$MANIFEST_FILE" <<EOF
{"timestamp":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","runName":"$RUN_NAME","epoch":"$EPOCH_LABEL","sourceRoot":"$SOURCE_ROOT","buildDir":"$BUILD_DIR","branchCoverage":true,"rawInfo":"$RAW_INFO","finalInfo":"$FINAL_INFO","finalInfoRetained":false,"summary":"$SUMMARY_FILE","html":"$HTML_DIR"}
EOF
fi

if [[ "$FINAL_INFO_RETAINED" == true ]]; then
  echo "Memgraph coverage info: $FINAL_INFO"
else
  echo "Memgraph coverage info removed after summary: $FINAL_INFO"
fi
echo "Memgraph coverage summary: $SUMMARY_FILE"
