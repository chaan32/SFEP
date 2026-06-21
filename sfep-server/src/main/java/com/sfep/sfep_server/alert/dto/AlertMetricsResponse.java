package com.sfep.sfep_server.alert.dto;

public record AlertMetricsResponse(
        long totalAlerts,
        long averageLatencyMs,
        long p95LatencyMs,
        long p99LatencyMs,
        int sampleSize
) {
}
