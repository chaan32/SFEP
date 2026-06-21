#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$ROOT_DIR/sfep-server"
BASE_URL="${BASE_URL:-http://localhost:18080}"
SERVER_PORT="${SERVER_PORT:-18080}"
EVENT_CASES="${EVENT_CASES:-100000}"
MODE="${MODE:-kafka}"
EQUIPMENT_COUNT="${EQUIPMENT_COUNT:-1000}"

JDBC_BATCH="${SFEP_JDBC_BATCH_SIZE:-2000}"
MAX_POLL="${SFEP_KAFKA_MAX_POLL_RECORDS:-1000}"
HIKARI_MAX="${SFEP_HIKARI_MAX_POOL_SIZE:-10}"

COMBINATIONS=(
  "6 3 p6-c3"
  "6 6 p6-c6"
  "12 6 p12-c6"
  "12 12 p12-c12"
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
  echo "server did not become healthy. recent log:" >&2
  tail -n 120 "$ROOT_DIR/.sfep-partition-matrix-server.log" >&2 || true
  return 1
}

run_case() {
  local partitions="$1"
  local concurrency="$2"
  local label="$3"
  local topic="sfep.sensor-events.${label}"

  echo
  echo "## ${label}"
  echo "partitions=${partitions} concurrency=${concurrency} jdbcBatch=${JDBC_BATCH} maxPoll=${MAX_POLL} hikariMax=${HIKARI_MAX} topic=${topic}"

  cleanup
  (
    cd "$SERVER_DIR"
    SFEP_DESKTOP_ENABLED=false \
    SERVER_PORT="$SERVER_PORT" \
    SFEP_JDBC_BATCH_SIZE="$JDBC_BATCH" \
    SFEP_HIBERNATE_JDBC_BATCH_SIZE="$JDBC_BATCH" \
    SFEP_KAFKA_MAX_POLL_RECORDS="$MAX_POLL" \
    SFEP_HIKARI_MAX_POOL_SIZE="$HIKARI_MAX" \
    SFEP_KAFKA_PARTITIONS="$partitions" \
    SFEP_KAFKA_CONCURRENCY="$concurrency" \
    SFEP_KAFKA_SENSOR_EVENTS_TOPIC="$topic" \
    SFEP_KAFKA_CONSUMER_GROUP="sfep-event-processor-${label}" \
    SFEP_KAFKA_ALERT_CONSUMER_GROUP="sfep-alert-processor-${label}" \
    SFEP_KAFKA_PRODUCER_BATCH_SIZE="${SFEP_KAFKA_PRODUCER_BATCH_SIZE:-65536}" \
    SFEP_KAFKA_PRODUCER_LINGER_MS="${SFEP_KAFKA_PRODUCER_LINGER_MS:-10}" \
    SFEP_KAFKA_PRODUCER_COMPRESSION_TYPE="${SFEP_KAFKA_PRODUCER_COMPRESSION_TYPE:-lz4}" \
    ./gradlew bootRun > "$ROOT_DIR/.sfep-partition-matrix-server.log" 2>&1
  ) &
  SERVER_PID=$!

  wait_health

  SFEP_JDBC_BATCH_SIZE="$JDBC_BATCH" \
  SFEP_HIBERNATE_JDBC_BATCH_SIZE="$JDBC_BATCH" \
  SFEP_KAFKA_MAX_POLL_RECORDS="$MAX_POLL" \
  SFEP_HIKARI_MAX_POOL_SIZE="$HIKARI_MAX" \
  SFEP_KAFKA_PARTITIONS="$partitions" \
  SFEP_KAFKA_CONCURRENCY="$concurrency" \
  SFEP_KAFKA_PRODUCER_BATCH_SIZE="${SFEP_KAFKA_PRODUCER_BATCH_SIZE:-65536}" \
  SFEP_KAFKA_PRODUCER_LINGER_MS="${SFEP_KAFKA_PRODUCER_LINGER_MS:-10}" \
  SFEP_KAFKA_PRODUCER_COMPRESSION_TYPE="${SFEP_KAFKA_PRODUCER_COMPRESSION_TYPE:-lz4}" \
  SFEP_BENCHMARK_LABEL="$label" \
  SFEP_KAFKA_SENSOR_EVENTS_TOPIC="$topic" \
  BASE_URL="$BASE_URL" \
  MODE="$MODE" \
  EVENT_CASES="$EVENT_CASES" \
  EQUIPMENT_COUNT="$EQUIPMENT_COUNT" \
  node "$ROOT_DIR/scripts/benchmark-sfep.mjs"
}

trap cleanup EXIT

for combination in "${COMBINATIONS[@]}"; do
  read -r partitions concurrency label <<< "$combination"
  run_case "$partitions" "$concurrency" "$label"
done
