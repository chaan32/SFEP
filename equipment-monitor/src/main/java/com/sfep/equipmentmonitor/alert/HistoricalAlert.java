package com.sfep.equipmentmonitor.alert;

import com.sfep.equipmentmonitor.risk.MetricEvidence;
import com.sfep.equipmentmonitor.risk.RangeStatus;
import com.sfep.equipmentmonitor.risk.RiskGrade;
import com.sfep.equipmentmonitor.risk.RiskScalar;

import java.util.Objects;

/** An explainable alert from a historical replay event, not a live-plant alarm. */
public record HistoricalAlert(
        AlertKey key,
        AlertKind kind,
        String eventId,
        String replayDate,
        Integer replayHour,
        String equipmentType,
        String equipmentId,
        String field,
        RiskScalar observedValue,
        RangeStatus rangeStatus,
        RiskGrade grade,
        boolean repeated,
        MetricEvidence discovery,
        MetricEvidence confirmation) {
    public HistoricalAlert {
        Objects.requireNonNull(key, "key");
        Objects.requireNonNull(kind, "kind");
        Objects.requireNonNull(eventId, "eventId");
        Objects.requireNonNull(replayDate, "replayDate");
        Objects.requireNonNull(equipmentType, "equipmentType");
        Objects.requireNonNull(equipmentId, "equipmentId");
        if (kind == AlertKind.OPERATING_RANGE && rangeStatus == null) {
            throw new IllegalArgumentException("operating-range alert requires rangeStatus");
        }
        if (kind == AlertKind.QUALITY_RISK && (discovery == null || confirmation == null)) {
            throw new IllegalArgumentException("quality-risk alert requires statistical evidence");
        }
        if (kind == AlertKind.MODEL_QUALITY_RISK) {
            if (grade != null || observedValue == null || !observedValue.isNumber()
                    || observedValue.doubleValue() < 0 || observedValue.doubleValue() > 1) {
                throw new IllegalArgumentException("model-quality alert requires a probability and no statistical grade");
            }
        } else if (grade != RiskGrade.CAUTION && grade != RiskGrade.DANGER) {
            throw new IllegalArgumentException("alert grade must be CAUTION or DANGER");
        }
    }

    public String materialKey() {
        return key.materialKey();
    }

    public String eventStage() {
        return key.eventStage();
    }

    public String ruleId() {
        return key.ruleId();
    }
}
