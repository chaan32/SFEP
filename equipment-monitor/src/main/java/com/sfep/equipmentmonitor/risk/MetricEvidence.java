package com.sfep.equipmentmonitor.risk;

public record MetricEvidence(
        long support,
        long defects,
        Double crudeRate,
        Double crudeRateCiLower,
        Double crudeRateCiUpper,
        Double adjustedRate,
        Double comparatorAdjustedRate,
        Double riskDifference,
        Double relativeRisk,
        Double relativeRiskCiLower,
        Double relativeRiskCiUpper,
        Double pValue,
        Double qValue,
        ReasonCode reasonCode) {
}
