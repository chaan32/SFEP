package com.sfep.equipmentmonitor.risk;

public record RuleEvaluationSummary(RiskGrade highestMatchedGrade, long matchedCount) {
}
