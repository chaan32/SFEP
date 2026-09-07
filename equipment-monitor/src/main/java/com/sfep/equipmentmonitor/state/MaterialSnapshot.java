package com.sfep.equipmentmonitor.state;

import com.sfep.equipmentmonitor.risk.RangeEvaluation;
import com.sfep.equipmentmonitor.risk.RiskGrade;
import com.sfep.equipmentmonitor.risk.RiskScalar;
import com.sfep.equipmentmonitor.risk.RuleEvaluation;

import java.util.List;
import java.util.Map;
import java.util.Objects;

public record MaterialSnapshot(
        String materialKey,
        String chargeId,
        String slabNo,
        String hrCoilId,
        String apProdId,
        String eventStage,
        String equipmentType,
        String equipmentId,
        String replayDate,
        Integer replayHour,
        String timePrecision,
        Map<String, RiskScalar> values,
        List<RangeEvaluation> rangeEvaluations,
        List<RuleEvaluation> ruleEvaluations,
        List<StageAssessment> assessments,
        RiskGrade qualityRisk,
        RiskGrade historicalEvidenceRisk,
        long matchedRuleCount) {
    public MaterialSnapshot {
        Objects.requireNonNull(materialKey, "materialKey");
        Objects.requireNonNull(chargeId, "chargeId");
        Objects.requireNonNull(slabNo, "slabNo");
        Objects.requireNonNull(eventStage, "eventStage");
        Objects.requireNonNull(equipmentType, "equipmentType");
        Objects.requireNonNull(equipmentId, "equipmentId");
        Objects.requireNonNull(replayDate, "replayDate");
        Objects.requireNonNull(timePrecision, "timePrecision");
        Objects.requireNonNull(qualityRisk, "qualityRisk");
        Objects.requireNonNull(historicalEvidenceRisk, "historicalEvidenceRisk");
        values = Map.copyOf(values);
        rangeEvaluations = List.copyOf(rangeEvaluations);
        ruleEvaluations = List.copyOf(ruleEvaluations);
        assessments = List.copyOf(assessments);
        if (matchedRuleCount < 0) {
            throw new IllegalArgumentException("matchedRuleCount must be non-negative");
        }
    }
}
