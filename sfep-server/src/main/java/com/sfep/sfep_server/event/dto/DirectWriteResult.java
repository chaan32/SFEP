package com.sfep.sfep_server.event.dto;

public record DirectWriteResult(
        int requestedEvents,
        int savedEvents,
        long elapsedMs,
        double eventsPerSecond
) {

    public static DirectWriteResult of(int requestedEvents, int savedEvents, long elapsedMs) {
        double seconds = Math.max(elapsedMs, 1) / 1000.0;
        return new DirectWriteResult(requestedEvents, savedEvents, elapsedMs, savedEvents / seconds);
    }
}
