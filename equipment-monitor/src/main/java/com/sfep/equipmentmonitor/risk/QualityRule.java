package com.sfep.equipmentmonitor.risk;

import java.util.List;
import java.util.Map;

public record QualityRule(
        String ruleId,
        AnalysisFamily analysisFamily,
        EvidenceFamily evidenceFamily,
        ProcessStage firstAvailableStage,
        EquipmentType equipmentType,
        ApplicationScope applicationScope,
        String equipmentId,
        List<String> fieldNames,
        List<PredicateTerm> allOf,
        Map<String, RiskScalar> applicationContext,
        int adjustmentLevel,
        List<String> adjustmentFieldsDropped,
        AdjustmentKind adjustmentKind,
        RiskGrade grade,
        boolean earlyWarningEligible,
        MetricEvidence discovery,
        MetricEvidence confirmation,
        List<String> displayMergeRuleIds) {

    public QualityRule {
        fieldNames = List.copyOf(fieldNames);
        allOf = List.copyOf(allOf);
        applicationContext = Map.copyOf(applicationContext);
        adjustmentFieldsDropped = List.copyOf(adjustmentFieldsDropped);
        displayMergeRuleIds = List.copyOf(displayMergeRuleIds);
    }
}
