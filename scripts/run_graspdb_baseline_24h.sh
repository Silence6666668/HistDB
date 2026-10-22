#!/usr/bin/env bash
set -u

ROOT_DIR="${ROOT_DIR:-/root/graph-baselines}"
GRASPDB_ROOT="${GRASPDB_ROOT:-$ROOT_DIR/external_tools/GRASpDB}"
GRASPDB_JAR="${GRASPDB_JAR:-$GRASPDB_ROOT/out/artifacts/GraspDB_jar/GraspDB.jar}"
TARGET="${TARGET:-neo4j}"
DURATION_SECONDS="${DURATION_SECONDS:-86400}"
NUM_TRIES="${NUM_TRIES:-100000000}"
NUM_QUERIES="${NUM_QUERIES:-100}"
MONITOR_INTERVAL_SECONDS="${MONITOR_INTERVAL_SECONDS:-300}"
RUN_NAME="${RUN_NAME:-graspdb_${TARGET}_24h_$(date -u +%Y%m%d_%H%M%S)}"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/coverage_runs/$RUN_NAME}"

NEO4J_IMAGE="${NEO4J_IMAGE:-neo4j:2026.03.1}"
MEMGRAPH_IMAGE="${MEMGRAPH_IMAGE:-memgraph/memgraph:3.9.0}"
FALKORDB_IMAGE="${FALKORDB_IMAGE:-falkordb/falkordb:v4.16.8}"

NEO4J_PASSWORD="${NEO4J_PASSWORD:-GraspDB1}"
MEMGRAPH_PASSWORD="${MEMGRAPH_PASSWORD:-123abc...}"
LOG_EACH_SELECT="${LOG_EACH_SELECT:-true}"
TRUNCATE_CUR_LOGS="${TRUNCATE_CUR_LOGS:-true}"
CUR_LOG_MAX_SIZE="${CUR_LOG_MAX_SIZE:-100M}"

mkdir -p "$OUT_DIR"
SUMMARY_TSV="$OUT_DIR/summary.tsv"
SUMMARY_MD="$OUT_DIR/summary.md"
MONITOR_TSV="$OUT_DIR/monitor.tsv"
MANIFEST="$OUT_DIR/manifest.env"
printf "target\tstatus\texit_code\tduration_seconds\treport_count\tout_dir\n" > "$SUMMARY_TSV"
printf "timestamp_utc\telapsed_seconds\tlog_txt_bytes\treport_count\tstdout_gz_bytes\tstderr_gz_bytes\tcontainer_status\n" > "$MONITOR_TSV"

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

remove_container() {
  docker rm -f "$1" >/dev/null 2>&1 || true
}

wait_tcp() {
  local host="$1"
  local port="$2"
  local limit="${3:-120}"
  for _ in $(seq 1 "$limit"); do
    if timeout 1 bash -c "cat < /dev/null > /dev/tcp/$host/$port" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_neo4j() {
  local name="$1"
  local limit="${2:-240}"
  for _ in $(seq 1 "$limit"); do
    if docker exec "$name" cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "RETURN 1;" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

write_config() {
  local config="$1"
  case "$TARGET" in
    neo4j)
      cat > "$config" <<EOF
{
  "neo4j@first": {"port": 18787, "host": "127.0.0.1", "username": "neo4j", "password": "$NEO4J_PASSWORD"},
  "neo4j@second": {"port": 18788, "host": "127.0.0.1", "username": "neo4j", "password": "$NEO4J_PASSWORD"}
}
EOF
      ;;
    memgraph)
      cat > "$config" <<EOF
{
  "memgraph@first": {"port": 17689, "host": "127.0.0.1", "username": "memgraph", "password": "$MEMGRAPH_PASSWORD"},
  "memgraph@second": {"port": 17690, "host": "127.0.0.1", "username": "memgraph", "password": "$MEMGRAPH_PASSWORD"}
}
EOF
      ;;
    falkordb)
      cat > "$config" <<EOF
{
  "redisgraph@first": {"port": 16379, "host": "127.0.0.1", "username": "redis", "password": ""},
  "redisgraph@second": {"port": 16380, "host": "127.0.0.1", "username": "redis", "password": ""}
}
EOF
      ;;
    *)
      log "Unsupported target: $TARGET"
      exit 2
      ;;
  esac
}

target_provider() {
  if [ "$TARGET" = "falkordb" ]; then
    printf "redisgraph"
  else
    printf "%s" "$TARGET"
  fi
}

start_target() {
  case "$TARGET" in
    neo4j)
      remove_container graspdb-24h-neo4j-1
      remove_container graspdb-24h-neo4j-2
      docker run -d --restart unless-stopped --name graspdb-24h-neo4j-1 \
        -p 127.0.0.1:18787:7687 \
        -e NEO4J_AUTH="neo4j/$NEO4J_PASSWORD" \
        -e NEO4J_server_memory_heap_initial__size=768m \
        -e NEO4J_server_memory_heap_max__size=768m \
        -e NEO4J_server_memory_pagecache_size=384m \
        "$NEO4J_IMAGE" >/dev/null
      docker run -d --restart unless-stopped --name graspdb-24h-neo4j-2 \
        -p 127.0.0.1:18788:7687 \
        -e NEO4J_AUTH="neo4j/$NEO4J_PASSWORD" \
        -e NEO4J_server_memory_heap_initial__size=768m \
        -e NEO4J_server_memory_heap_max__size=768m \
        -e NEO4J_server_memory_pagecache_size=384m \
        "$NEO4J_IMAGE" >/dev/null
      wait_neo4j graspdb-24h-neo4j-1 240 && wait_neo4j graspdb-24h-neo4j-2 240
      ;;
    memgraph)
      remove_container graspdb-24h-memgraph-1
      remove_container graspdb-24h-memgraph-2
      docker run -d --restart unless-stopped --name graspdb-24h-memgraph-1 -p 127.0.0.1:17689:7687 "$MEMGRAPH_IMAGE" >/dev/null
      docker run -d --restart unless-stopped --name graspdb-24h-memgraph-2 -p 127.0.0.1:17690:7687 "$MEMGRAPH_IMAGE" >/dev/null
      wait_tcp 127.0.0.1 17689 120 && wait_tcp 127.0.0.1 17690 120
      ;;
    falkordb)
      remove_container graspdb-24h-falkordb-1
      remove_container graspdb-24h-falkordb-2
      docker run -d --restart unless-stopped --name graspdb-24h-falkordb-1 -p 127.0.0.1:16379:6379 "$FALKORDB_IMAGE" >/dev/null
      docker run -d --restart unless-stopped --name graspdb-24h-falkordb-2 -p 127.0.0.1:16380:6379 "$FALKORDB_IMAGE" >/dev/null
      wait_tcp 127.0.0.1 16379 120 && wait_tcp 127.0.0.1 16380 120
      ;;
  esac
}

stop_target() {
  case "$TARGET" in
    neo4j)
      remove_container graspdb-24h-neo4j-1
      remove_container graspdb-24h-neo4j-2
      ;;
    memgraph)
      remove_container graspdb-24h-memgraph-1
      remove_container graspdb-24h-memgraph-2
      ;;
    falkordb)
      remove_container graspdb-24h-falkordb-1
      remove_container graspdb-24h-falkordb-2
      ;;
  esac
}

container_status() {
  docker ps -a --format '{{.Names}}={{.Status}}' | grep "graspdb-24h-$TARGET" | tr '\n' ';' || true
}

report_count() {
  local log_txt="$1"
  if [ -s "$log_txt" ]; then
    grep -c '^[0-9][0-9]*、' "$log_txt" || true
  else
    printf "0"
  fi
}

snapshot_monitor() {
  local start_ts="$1"
  local work_dir="$2"
  local now
  local elapsed
  local log_size
  local reports
  local stdout_size
  local stderr_size
  now="$(date +%s)"
  elapsed=$((now - start_ts))
  log_size="$(stat -c '%s' "$work_dir/log.txt" 2>/dev/null || printf 0)"
  reports="$(report_count "$work_dir/log.txt")"
  stdout_size="$(stat -c '%s' "$OUT_DIR/stdout.log.gz" 2>/dev/null || printf 0)"
  stderr_size="$(stat -c '%s' "$OUT_DIR/stderr.log.gz" 2>/dev/null || printf 0)"
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$elapsed" "$log_size" "$reports" "$stdout_size" "$stderr_size" "$(container_status)" >> "$MONITOR_TSV"
  if [ "$TRUNCATE_CUR_LOGS" = "true" ]; then
    find "$work_dir/logs" -type f -name '*-cur.log' -size +"$CUR_LOG_MAX_SIZE" -exec truncate -s 0 {} \; 2>/dev/null || true
  fi
}

collect_target_logs() {
  case "$TARGET" in
    neo4j)
      docker logs graspdb-24h-neo4j-1 > "$OUT_DIR/docker_neo4j_1.log" 2>&1 || true
      docker logs graspdb-24h-neo4j-2 > "$OUT_DIR/docker_neo4j_2.log" 2>&1 || true
      ;;
    memgraph)
      docker logs graspdb-24h-memgraph-1 > "$OUT_DIR/docker_memgraph_1.log" 2>&1 || true
      docker logs graspdb-24h-memgraph-2 > "$OUT_DIR/docker_memgraph_2.log" 2>&1 || true
      ;;
    falkordb)
      docker logs graspdb-24h-falkordb-1 > "$OUT_DIR/docker_falkordb_1.log" 2>&1 || true
      docker logs graspdb-24h-falkordb-2 > "$OUT_DIR/docker_falkordb_2.log" 2>&1 || true
      ;;
  esac
}

write_summary() {
  {
    printf '# GRASpDB 24h baseline\n\n'
    printf 'Run name: `%s`\n\n' "$RUN_NAME"
    printf 'Output directory: `%s`\n\n' "$OUT_DIR"
    printf '| Target | Status | Exit code | Duration seconds | Raw report entries |\n'
    printf '|---|---|---:|---:|---:|\n'
    tail -n +2 "$SUMMARY_TSV" | while IFS="$(printf '\t')" read -r target status rc duration reports out_dir; do
      printf '| %s | %s | %s | %s | %s |\n' "$target" "$status" "$rc" "$duration" "$reports"
    done
  } > "$SUMMARY_MD"
}

if [ ! -s "$GRASPDB_JAR" ]; then
  log "GRASpDB jar not found: $GRASPDB_JAR"
  exit 1
fi

WORK_DIR="$OUT_DIR/work"
mkdir -p "$WORK_DIR/logs"
write_config "$WORK_DIR/config.json"

cat > "$MANIFEST" <<EOF
RUN_NAME=$RUN_NAME
TARGET=$TARGET
PROVIDER=$(target_provider)
DURATION_SECONDS=$DURATION_SECONDS
NUM_TRIES=$NUM_TRIES
NUM_QUERIES=$NUM_QUERIES
LOG_EACH_SELECT=$LOG_EACH_SELECT
TRUNCATE_CUR_LOGS=$TRUNCATE_CUR_LOGS
CUR_LOG_MAX_SIZE=$CUR_LOG_MAX_SIZE
NEO4J_IMAGE=$NEO4J_IMAGE
MEMGRAPH_IMAGE=$MEMGRAPH_IMAGE
FALKORDB_IMAGE=$FALKORDB_IMAGE
GRASPDB_JAR=$GRASPDB_JAR
EOF

log "Starting GRASpDB 24h baseline target=$TARGET provider=$(target_provider)"
if ! start_target; then
  log "Target did not become ready"
  collect_target_logs
  stop_target
  printf "%s\tfailed-readiness\t-1\t0\t0\t%s\n" "$TARGET" "$OUT_DIR" >> "$SUMMARY_TSV"
  write_summary
  exit 1
fi

START_TS="$(date +%s)"
set +e
(
  cd "$WORK_DIR" || exit 2
  timeout "$DURATION_SECONDS" java -jar "$GRASPDB_JAR" \
    --num-tries "$NUM_TRIES" \
    --num-queries "$NUM_QUERIES" \
    --log-each-select "$LOG_EACH_SELECT" \
    --print-progress-information true \
    --database-instance "$(target_provider)" \
    composite --config "$WORK_DIR/config.json"
) > >(gzip -c > "$OUT_DIR/stdout.log.gz") 2> >(gzip -c > "$OUT_DIR/stderr.log.gz") &
RUN_PID=$!

while kill -0 "$RUN_PID" >/dev/null 2>&1; do
  snapshot_monitor "$START_TS" "$WORK_DIR"
  sleep "$MONITOR_INTERVAL_SECONDS"
done
wait "$RUN_PID"
RC=$?
set -e
END_TS="$(date +%s)"
DURATION=$((END_TS - START_TS))
snapshot_monitor "$START_TS" "$WORK_DIR"

collect_target_logs

STATUS="failed"
if [ "$RC" -eq 124 ]; then
  STATUS="budget-complete"
elif [ "$RC" -eq 0 ]; then
  STATUS="completed"
elif [ "$RC" -eq 255 ]; then
  if gzip -cd "$OUT_DIR/stderr.log.gz" 2>/dev/null | grep -Fq "total number reached"; then
    STATUS="limit-complete"
  else
    STATUS="warning"
  fi
fi

REPORTS="$(report_count "$WORK_DIR/log.txt")"
printf "%s\t%s\t%s\t%s\t%s\t%s\n" "$TARGET" "$STATUS" "$RC" "$DURATION" "$REPORTS" "$OUT_DIR" >> "$SUMMARY_TSV"
write_summary
tar -C "$WORK_DIR" -czf "$OUT_DIR/work_logs_and_reports.tar.gz" logs log.txt config.json coverage_log 2>/dev/null || true
stop_target
log "Done status=$STATUS exit=$RC duration=${DURATION}s reports=$REPORTS summary=$SUMMARY_MD"
