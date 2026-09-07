package com.sfep.equipmentmonitor.replay;

import java.util.List;
import java.util.Objects;

/** Identifies the historical replay unit whose processing failed. */
public record ReplayFailureContext(
        String batchId,
        String batchStep,
        String replayDate,
        Integer replayHour,
        List<String> materialKeys) {
    public ReplayFailureContext {
        Objects.requireNonNull(batchId, "batchId");
        Objects.requireNonNull(batchStep, "batchStep");
        Objects.requireNonNull(replayDate, "replayDate");
        materialKeys = List.copyOf(Objects.requireNonNull(materialKeys, "materialKeys"));
    }

    public static ReplayFailureContext from(ReplayUnit unit) {
        Objects.requireNonNull(unit, "unit");
        return new ReplayFailureContext(
                unit.batchId(),
                unit.batchStep(),
                unit.replayDate(),
                unit.replayHour(),
                unit.events().stream()
                        .map(ReplayEvent::materialKey)
                        .distinct()
                        .sorted()
                        .toList());
    }
}
