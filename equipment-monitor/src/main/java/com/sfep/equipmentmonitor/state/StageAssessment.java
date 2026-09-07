package com.sfep.equipmentmonitor.state;

import com.sfep.equipmentmonitor.risk.RangeEvaluation;
import com.sfep.equipmentmonitor.risk.RiskGrade;
import com.sfep.equipmentmonitor.risk.RuleEvaluation;

import java.util.List;
import java.util.Objects;

/** Immutable evidence retained for one material at one revealed replay stage. */
public record StageAssessment(
        String eventId,
        String eventStage,
        String equipmentType,
        String equipmentId,
        String replayDate,
        Integer replayHour,
        List<RangeEvaluation> rangeEvaluations,
        List<RuleEvaluation> ruleEvaluations,
        RiskGrade earlyWarningRisk,
        RiskGrade historicalEvidenceRisk) {
    public StageAssessment {
        Objects.requireNonNull(eventId, "eventId");
        Objects.requireNonNull(eventStage, "eventStage");
        Objects.requireNonNull(equipmentType, "equipmentType");
        Objects.requireNonNull(equipmentId, "equipmentId");
        Objects.requireNonNull(replayDate, "replayDate");
        rangeEvaluations = List.copyOf(rangeEvaluations);
        ruleEvaluations = List.copyOf(ruleEvaluations);
        Objects.requireNonNull(earlyWarningRisk, "earlyWarningRisk");
        Objects.requireNonNull(historicalEvidenceRisk, "historicalEvidenceRisk");
    }
}
