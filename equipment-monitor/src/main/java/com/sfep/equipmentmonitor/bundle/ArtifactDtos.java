package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.List;

record AnalysisConfigDto(
        String schemaVersion,
        String analysisConfigVersion,
        String timezone,
        int labelMaturityDays,
        JsonNode splits,
        JsonNode operatingRanges,
        JsonNode qualityRisk,
        JsonNode bootstrap,
        double wilsonZ,
        JsonNode fields,
        JsonNode rangeContextHierarchies,
        JsonNode riskAdjustmentHierarchies,
        JsonNode fixedInteractions,
        JsonNode fdrFamilies,
        JsonNode evidenceFamilies) {
}

record ProducerRuntimeDto(
        String schemaVersion,
        JsonNode platform,
        JsonNode python,
        String pipVersion,
        JsonNode locks,
        JsonNode packages,
        JsonNode producer,
        JsonNode environmentPolicy) {
}

record OperatingRangesDto(String schemaVersion, String criteriaId, String asOf, List<JsonNode> ranges) {
}

record QualityRulesDto(String schemaVersion, String criteriaId, String asOf, List<JsonNode> rules) {
}

record AnalysisSummaryDto(
        String schemaVersion,
        String bundleId,
        String criteriaId,
        String asOf,
        String innerSplitDate,
        String evaluationMode,
        JsonNode dateRange,
        JsonNode splitCounts,
        JsonNode quarantineCounts,
        JsonNode labelCensoringCounts,
        JsonNode chargePurgeCounts,
        JsonNode sourceColumnProfiles,
        JsonNode driftMetrics,
        JsonNode holdoutMetrics,
        JsonNode lineage) {
}
