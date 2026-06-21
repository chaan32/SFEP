#!/usr/bin/env node

const baseUrl = process.env.BASE_URL ?? "http://localhost:8080";
const mode = (process.env.MODE ?? "both").toLowerCase();
const equipmentCount = Number(process.env.EQUIPMENT_COUNT ?? 1000);
const failureRatePercent = Number(process.env.FAILURE_RATE_PERCENT ?? 2);
const eventCases = (process.env.EVENT_CASES ?? "10000,50000,100000")
  .split(",")
  .map((value) => Number(value.trim()))
  .filter((value) => Number.isFinite(value) && value > 0);
const pollIntervalMs = Number(process.env.POLL_INTERVAL_MS ?? 100);
const pollTimeoutMs = Number(process.env.POLL_TIMEOUT_MS ?? 120000);
const alertSettleMs = Number(process.env.ALERT_SETTLE_MS ?? 1000);

const configLabel = {
  benchmarkLabel: process.env.SFEP_BENCHMARK_LABEL ?? "-",
  kafkaTopic: process.env.SFEP_KAFKA_SENSOR_EVENTS_TOPIC ?? "-",
  jdbcBatchSize: process.env.SFEP_JDBC_BATCH_SIZE ?? "-",
  hibernateBatchSize: process.env.SFEP_HIBERNATE_JDBC_BATCH_SIZE ?? "-",
  maxPollRecords: process.env.SFEP_KAFKA_MAX_POLL_RECORDS ?? "-",
  hikariMaxPoolSize: process.env.SFEP_HIKARI_MAX_POOL_SIZE ?? "-",
  kafkaPartitions: process.env.SFEP_KAFKA_PARTITIONS ?? "-",
  consumerConcurrency: process.env.SFEP_KAFKA_CONCURRENCY ?? "-",
  producerBatchSize: process.env.SFEP_KAFKA_PRODUCER_BATCH_SIZE ?? "-",
  producerLingerMs: process.env.SFEP_KAFKA_PRODUCER_LINGER_MS ?? "-",
  producerCompressionType: process.env.SFEP_KAFKA_PRODUCER_COMPRESSION_TYPE ?? "-",
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function postJson(path, body) {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(`${path} failed: ${response.status} ${message}`);
  }
  return response.json();
}

async function postEmpty(path) {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(`${path} failed: ${response.status} ${message}`);
  }
  return response.json();
}

async function getJson(path) {
  const response = await fetch(`${baseUrl}${path}`);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(`${path} failed: ${response.status} ${message}`);
  }
  return response.json();
}

async function resetAlertMetrics() {
  return postEmpty("/api/v1/alerts/metrics/reset");
}

async function alertMetrics() {
  await sleep(alertSettleMs);
  return getJson("/api/v1/alerts/metrics");
}

function requestFor(totalEvents) {
  if (totalEvents % equipmentCount !== 0) {
    throw new Error(`totalEvents=${totalEvents} must be divisible by equipmentCount=${equipmentCount}`);
  }
  return {
    equipmentCount,
    eventsPerEquipment: totalEvents / equipmentCount,
    failureRatePercent,
  };
}

async function runDirect(totalEvents) {
  await resetAlertMetrics();
  const startedAt = Date.now();
  const result = await postJson("/api/v1/simulator/direct/run", requestFor(totalEvents));
  const requestElapsedMs = Date.now() - startedAt;
  const alerts = await alertMetrics();
  return {
    transport: "DIRECT",
    totalEvents,
    elapsedMs: result.elapsedMs,
    totalElapsedMs: requestElapsedMs,
    saveEventsPerSecond: result.eventsPerSecond,
    publishElapsedMs: 0,
    publishEventsPerSecond: 0,
    maxLagEvents: 0,
    finalLagEvents: 0,
    status: "COMPLETED",
    alertCount: alerts.totalAlerts,
    alertAverageLatencyMs: alerts.averageLatencyMs,
    alertP95LatencyMs: alerts.p95LatencyMs,
    alertP99LatencyMs: alerts.p99LatencyMs,
    ...configLabel,
  };
}

async function runKafka(totalEvents) {
  await resetAlertMetrics();
  const published = await postJson("/api/v1/simulator/kafka/run", requestFor(totalEvents));
  const deadline = Date.now() + pollTimeoutMs;
  let lastStatus;

  while (Date.now() < deadline) {
    lastStatus = await getJson(`/api/v1/simulator/kafka/runs/${published.runId}`);
    if (lastStatus.completed) {
      break;
    }
    await sleep(pollIntervalMs);
  }

  if (!lastStatus?.completed) {
    throw new Error(`Kafka run did not finish within ${pollTimeoutMs}ms: runId=${published.runId}`);
  }

  const alerts = await alertMetrics();
  return {
    transport: "KAFKA",
    totalEvents,
    elapsedMs: lastStatus.processingElapsedMs,
    totalElapsedMs: lastStatus.totalElapsedMs,
    saveEventsPerSecond: lastStatus.saveEventsPerSecond,
    publishElapsedMs: lastStatus.publishElapsedMs,
    publishEventsPerSecond: lastStatus.publishEventsPerSecond,
    maxLagEvents: lastStatus.maxLagEvents,
    finalLagEvents: lastStatus.lagEvents,
    status: lastStatus.status,
    alertCount: alerts.totalAlerts,
    alertAverageLatencyMs: alerts.averageLatencyMs,
    alertP95LatencyMs: alerts.p95LatencyMs,
    alertP99LatencyMs: alerts.p99LatencyMs,
    ...configLabel,
  };
}

function markdownTable(rows) {
  const headers = [
    "transport",
    "events",
    "case",
    "topic",
    "jdbcBatch",
    "maxPoll",
    "hikari",
    "partitions",
    "concurrency",
    "producerBatch",
    "linger",
    "compression",
    "publishMs",
    "processMs",
    "totalMs",
    "saveEPS",
    "maxLag",
    "alerts",
    "alertAvg",
    "alertP95",
    "alertP99",
    "status",
  ];
  const lines = [
    `| ${headers.join(" | ")} |`,
    `| ${headers.map(() => "---").join(" | ")} |`,
  ];
  for (const row of rows) {
    lines.push(
      [
        row.transport,
        row.totalEvents,
        row.benchmarkLabel,
        row.kafkaTopic,
        row.jdbcBatchSize,
        row.maxPollRecords,
        row.hikariMaxPoolSize,
        row.kafkaPartitions,
        row.consumerConcurrency,
        row.producerBatchSize,
        row.producerLingerMs,
        row.producerCompressionType,
        Math.round(row.publishElapsedMs),
        Math.round(row.elapsedMs),
        Math.round(row.totalElapsedMs),
        Math.round(row.saveEventsPerSecond),
        row.maxLagEvents,
        row.alertCount,
        row.alertAverageLatencyMs,
        row.alertP95LatencyMs,
        row.alertP99LatencyMs,
        row.status,
      ].join(" | ").replace(/^/, "| ").concat(" |")
    );
  }
  return lines.join("\n");
}

const rows = [];
for (const totalEvents of eventCases) {
  if (mode === "direct" || mode === "both") {
    rows.push(await runDirect(totalEvents));
  }
  if (mode === "kafka" || mode === "both") {
    rows.push(await runKafka(totalEvents));
  }
}

console.log(markdownTable(rows));
console.log("\n```json");
console.log(JSON.stringify(rows, null, 2));
console.log("```");
