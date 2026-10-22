#!/usr/bin/env bash
set -euo pipefail

exec /opt/toolchain-v7/bin/llvm-cov gcov "$@"
