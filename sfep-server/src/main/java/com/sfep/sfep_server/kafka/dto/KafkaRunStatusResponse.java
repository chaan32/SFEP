package com.sfep.sfep_server.kafka.dto;

public record KafkaRunStatusResponse(
        String runId,
        int expectedEvents,
        int publishedEvents,
        int consumedEvents,
        int savedEvents,
        int failedEvents,
        int lagEvents,
        int maxLagEvents,
        long publishElapsedMs,
        long processingElapsedMs,
        long totalElapsedMs,
        double publishEventsPerSecond,
        double saveEventsPerSecond,
        boolean completed,
        String status
) {
}
