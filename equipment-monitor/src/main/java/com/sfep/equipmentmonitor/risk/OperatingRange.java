package com.sfep.equipmentmonitor.risk;

import java.util.Map;

public record OperatingRange(
        String ruleId,
        String field,
        FieldRole fieldRole,
        ProcessStage firstAvailableStage,
        EquipmentType equipmentType,
        String equipmentId,
        int contextLevel,
        Map<String, RiskScalar> context,
        long support,
        double median,
        Double p01,
        double p05,
        double p95,
        Double p99,
        boolean lowerTailEnabled,
        boolean upperTailEnabled) {

    public OperatingRange {
        context = Map.copyOf(context);
    }
}
