#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="configs/baseline_tool_repos.env"
UPDATE=0
INCLUDE_OPTIONAL=0

usage() {
  cat <<'EOF'
Usage:
  bash scripts/fetch_baseline_tools.sh [options]

Options:
  --config PATH       Tool repository config. Default: configs/baseline_tool_repos.env
  --update            If a tool directory already exists, run git fetch first.
  --optional          Also fetch optional tool repos configured in the env file.
  -h, --help          Show this help.

This script fetches only source artifacts. Run scripts/bootstrap_ubuntu.sh
afterward to install dependencies and build Dinkel/GDsmith/GDBMeter.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG_PATH="${2:?missing config path}"; shift 2 ;;
    --update) UPDATE=1; shift ;;
    --optional) INCLUDE_OPTIONAL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ -f "$CONFIG_PATH" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_PATH"
elif [[ -f configs/baseline_tool_repos.env.example ]]; then
  # shellcheck disable=SC1091
  source configs/baseline_tool_repos.env.example
else
  echo "No tool repo config found." >&2
  exit 2
fi

mkdir -p external_tools

fetch_tool() {
  local name="$1"
  local dest="$2"
  local repo="$3"
  local ref="${4:-}"

  if [[ -z "$repo" ]]; then
    echo "[fetch] skip $name: repo URL is empty"
    return
  fi

  if [[ -d "$dest/.git" ]]; then
    echo "[fetch] $name already exists at $dest"
    if [[ "$UPDATE" -eq 1 ]]; then
      git -C "$dest" fetch --all --tags --prune
    fi
  elif [[ -e "$dest" ]]; then
    echo "[fetch] skip $name: $dest exists but is not a git checkout" >&2
    echo "        Move it away or package local tool sources instead." >&2
    return
  else
    echo "[fetch] cloning $name from $repo"
    git clone --recursive "$repo" "$dest"
  fi

  if [[ -n "$ref" ]]; then
    echo "[fetch] checking out $name ref $ref"
    git -C "$dest" checkout "$ref"
    git -C "$dest" submodule update --init --recursive
  fi
}

fetch_tool "Dinkel" "external_tools/dinkel" "${DINKEL_REPO:-}" "${DINKEL_REF:-}"
fetch_tool "GDsmith" "external_tools/GDsmith" "${GDSMITH_REPO:-}" "${GDSMITH_REF:-}"
fetch_tool "GDBMeter" "external_tools/gdbmeter" "${GDBMETER_REPO:-}" "${GDBMETER_REF:-}"

if [[ "$INCLUDE_OPTIONAL" -eq 1 ]]; then
  fetch_tool "Gamera" "external_tools/Gamera" "${GAMERA_REPO:-}" "${GAMERA_REF:-}"
  fetch_tool "GraphGenie" "external_tools/GraphGenie" "${GRAPHGENIE_REPO:-}" "${GRAPHGENIE_REF:-}"
  fetch_tool "GRev" "external_tools/GRev" "${GREV_REPO:-}" "${GREV_REF:-}"
  fetch_tool "GRASpDB" "external_tools/GRASpDB" "${GRASPDB_REPO:-}" "${GRASPDB_REF:-}"
fi

echo "[fetch] done"
