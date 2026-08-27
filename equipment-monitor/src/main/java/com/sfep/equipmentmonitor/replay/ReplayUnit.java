package com.sfep.equipmentmonitor.replay;

import java.util.List;
import java.util.Objects;

/** A synchronized replay sub-step. Events inside the unit have no claimed order. */
public record ReplayUnit(
        String batchId,
        String batchStep,
        String replayDate,
        Integer replayHour,
        String batchKind,
        String timePrecision,
        List<ReplayEvent> events,
        boolean orderedWithinUnit) {
    public ReplayUnit {
        Objects.requireNonNull(batchId, "batchId");
        Objects.requireNonNull(batchStep, "batchStep");
        Objects.requireNonNull(replayDate, "replayDate");
        Objects.requireNonNull(batchKind, "batchKind");
        Objects.requireNonNull(timePrecision, "timePrecision");
        events = List.copyOf(Objects.requireNonNull(events, "events"));
        if (events.isEmpty()) {
            throw new IllegalArgumentException("a replay unit must contain at least one event");
        }
        if (orderedWithinUnit) {
            throw new IllegalArgumentException("replay events are unordered within a unit");
        }
        for (ReplayEvent event : events) {
            if (!batchId.equals(event.batchId()) || !batchStep.equals(event.batchStep())) {
                throw new IllegalArgumentException("unit events must share batchId and batchStep");
            }
            if (!replayDate.equals(event.replayDate())
                    || !Objects.equals(replayHour, event.replayHour())
                    || !batchKind.equals(event.batchKind())
                    || !timePrecision.equals(event.timePrecision())) {
                throw new IllegalArgumentException("unit events must share replay metadata");
            }
        }
    }

    static ReplayUnit from(List<ReplayEvent> events) {
        ReplayEvent first = events.getFirst();
        return new ReplayUnit(
                first.batchId(),
                first.batchStep(),
                first.replayDate(),
                first.replayHour(),
                first.batchKind(),
                first.timePrecision(),
                events,
                false);
    }
}
