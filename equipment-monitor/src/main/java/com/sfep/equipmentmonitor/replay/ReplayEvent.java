package com.sfep.equipmentmonitor.replay;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.Objects;

public record ReplayEvent(
        String schemaVersion,
        String bundleId,
        String criteriaId,
        String eventId,
        String replayDate,
        Integer replayHour,
        String batchKind,
        String batchId,
        String equipmentBatchId,
        String batchStep,
        String timePrecision,
        String materialKey,
        String equipmentType,
        String equipmentId,
        String chargeId,
        String slabNo,
        String hrCoilId,
        String apProdId,
        JsonNode values) {
    public ReplayEvent {
        Objects.requireNonNull(schemaVersion, "schemaVersion");
        Objects.requireNonNull(bundleId, "bundleId");
        Objects.requireNonNull(criteriaId, "criteriaId");
        Objects.requireNonNull(eventId, "eventId");
        Objects.requireNonNull(replayDate, "replayDate");
        Objects.requireNonNull(batchKind, "batchKind");
        Objects.requireNonNull(batchId, "batchId");
        Objects.requireNonNull(batchStep, "batchStep");
        Objects.requireNonNull(timePrecision, "timePrecision");
        Objects.requireNonNull(materialKey, "materialKey");
        Objects.requireNonNull(equipmentType, "equipmentType");
        Objects.requireNonNull(equipmentId, "equipmentId");
        Objects.requireNonNull(chargeId, "chargeId");
        Objects.requireNonNull(slabNo, "slabNo");
        values = Objects.requireNonNull(values, "values").deepCopy();
    }

    @Override
    public JsonNode values() {
        return values.deepCopy();
    }
}
