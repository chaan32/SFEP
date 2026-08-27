package com.sfep.equipmentmonitor.risk;

public record RangeEvaluation(
        String field,
        FieldRole fieldRole,
        RangeStatus status,
        RiskScalar observedValue,
        String selectedRuleId,
        Integer contextLevel,
        RangeSelectionReason selectionReason,
        boolean alertEligible) {
}
