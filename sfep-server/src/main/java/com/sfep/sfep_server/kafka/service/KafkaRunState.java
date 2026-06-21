package com.sfep.sfep_server.kafka.service;

import com.sfep.sfep_server.kafka.dto.KafkaRunStatusResponse;

import java.time.Instant;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

final class KafkaRunState {

    private final String runId;
    private final int expectedEvents;
    private final Instant startedAt;
    private final AtomicInteger publishedEvents = new AtomicInteger();
    private final AtomicInteger consumedEvents = new AtomicInteger();
    private final AtomicInteger savedEvents = new AtomicInteger();
    private final AtomicInteger failedEvents = new AtomicInteger();
    private final AtomicInteger maxLagEvents = new AtomicInteger();
    private final AtomicLong publishElapsedMs = new AtomicLong();
    private final AtomicLong processingElapsedMs = new AtomicLong();
    private volatile Instant processingStartedAt;
    private volatile Instant completedAt;
    private volatile String status = "STARTED";

    KafkaRunState(String runId, int expectedEvents) {
        this.runId = runId;
        this.expectedEvents = expectedEvents;
        this.startedAt = Instant.now();
    }

    void markPublished(int count, long elapsedMs) {
        publishedEvents.set(count);
        publishElapsedMs.set(elapsedMs);
        processingStartedAt = Instant.now();
        status = "PUBLISHED";
    }

    void addSaved(int consumedCount, int savedCount) {
        consumedEvents.addAndGet(consumedCount);
        savedEvents.addAndGet(savedCount);
        refreshCompletion();
    }

    void addFailed(int failedCount) {
        failedEvents.addAndGet(failedCount);
        refreshCompletion();
    }

    private void refreshCompletion() {
        int finished = savedEvents.get() + failedEvents.get();
        if (finished >= expectedEvents) {
            completedAt = Instant.now();
            status = failedEvents.get() == 0 ? "COMPLETED" : "COMPLETED_WITH_FAILURE";
            Instant processingStart = processingStartedAt == null ? startedAt : processingStartedAt;
            processingElapsedMs.set(Math.max(1, completedAt.toEpochMilli() - processingStart.toEpochMilli()));
            return;
        }
        status = "PROCESSING";
        Instant processingStart = processingStartedAt == null ? startedAt : processingStartedAt;
        processingElapsedMs.set(Math.max(1, Instant.now().toEpochMilli() - processingStart.toEpochMilli()));
    }

    KafkaRunStatusResponse toResponse() {
        Instant end = completedAt == null ? Instant.now() : completedAt;
        long totalElapsedMs = Math.max(1, end.toEpochMilli() - startedAt.toEpochMilli());
        long currentProcessingElapsedMs = processingElapsedMs.get();
        if (completedAt == null && processingStartedAt != null) {
            currentProcessingElapsedMs = Math.max(1, Instant.now().toEpochMilli() - processingStartedAt.toEpochMilli());
        }

        int published = publishedEvents.get();
        int saved = savedEvents.get();
        int failed = failedEvents.get();
        int lag = Math.max(0, expectedEvents - saved - failed);
        maxLagEvents.accumulateAndGet(lag, Math::max);
        return new KafkaRunStatusResponse(
                runId,
                expectedEvents,
                published,
                consumedEvents.get(),
                saved,
                failed,
                lag,
                maxLagEvents.get(),
                publishElapsedMs.get(),
                currentProcessingElapsedMs,
                totalElapsedMs,
                published / (Math.max(publishElapsedMs.get(), 1) / 1000.0),
                saved / (Math.max(currentProcessingElapsedMs, 1) / 1000.0),
                lag == 0,
                status
        );
    }
}
