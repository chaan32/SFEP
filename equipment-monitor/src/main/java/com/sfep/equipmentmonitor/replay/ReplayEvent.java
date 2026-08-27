package com.sfep.equipmentmonitor.replay;

import com.fasterxml.jackson.databind.JsonNode;

record ReplayEvent(
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
}
