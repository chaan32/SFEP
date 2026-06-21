#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$ROOT_DIR/sfep-server"
BASE_URL="${BASE_URL:-http://localhost:18080}"
SERVER_PORT="${SERVER_PORT:-18080}"
EVENT_CASES="${EVENT_CASES:-100000}"
MODE="${MODE:-kafka}"
EQUIPMENT_COUNT="${EQUIPMENT_COUNT:-1000}"

COMBINATIONS=(
  "1000 1000 10 3 baseline"
  "2000 1000 10 3 larger-jdbc-batch"
  "2000 3000 20 3 larger-poll-and-pool"
  "2000 3000 20 6 higher-consumer-concurrency"
)

SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  SERVER_PID=""
}

wait_health() {
  for _ in {1..120}; do
    if curl -fsS "$BASE_URL/actuator/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

trap cleanup EXIT

for combination in "${COMBINATIONS[@]}"; do
  read -r JDBC_BATCH MAX_POLL HIKARI_MAX CONCURRENCY LABEL <<< "$combination"
  echo
  echo "## $LABEL"
  echo "jdbcBatch=$JDBC_BATCH maxPoll=$MAX_POLL hikariMax=$HIKARI_MAX concurrency=$CONCURRENCY"

  cleanup
  (
    cd "$SERVER_DIR"
    SFEP_DESKTOP_ENABLED=false \
    SERVER_PORT="$SERVER_PORT" \
    SFEP_JDBC_BATCH_SIZE="$JDBC_BATCH" \
    SFEP_HIBERNATE_JDBC_BATCH_SIZE="$JDBC_BATCH" \
    SFEP_KAFKA_MAX_POLL_RECORDS="$MAX_POLL" \
    SFEP_HIKARI_MAX_POOL_SIZE="$HIKARI_MAX" \
    SFEP_KAFKA_CONCURRENCY="$CONCURRENCY" \
    ./gradlew bootRun > "$ROOT_DIR/.sfep-benchmark-server.log" 2>&1
  ) &
  SERVER_PID=$!

  wait_health

  SFEP_JDBC_BATCH_SIZE="$JDBC_BATCH" \
  SFEP_HIBERNATE_JDBC_BATCH_SIZE="$JDBC_BATCH" \
  SFEP_KAFKA_MAX_POLL_RECORDS="$MAX_POLL" \
  SFEP_HIKARI_MAX_POOL_SIZE="$HIKARI_MAX" \
  SFEP_KAFKA_CONCURRENCY="$CONCURRENCY" \
  BASE_URL="$BASE_URL" \
  MODE="$MODE" \
  EVENT_CASES="$EVENT_CASES" \
  EQUIPMENT_COUNT="$EQUIPMENT_COUNT" \
  node "$ROOT_DIR/scripts/benchmark-sfep.mjs"
done
