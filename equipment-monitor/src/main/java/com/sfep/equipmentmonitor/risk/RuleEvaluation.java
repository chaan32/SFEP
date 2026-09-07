package com.sfep.equipmentmonitor.risk;

public record RuleEvaluation(
        String ruleId,
        EvidenceFamily evidenceFamily,
        RuleMatchStatus status,
        boolean matched,
        RiskGrade grade,
        boolean alertEligible,
        boolean historicalEvidenceOnly,
        MetricEvidence discovery,
        MetricEvidence confirmation) {
}
