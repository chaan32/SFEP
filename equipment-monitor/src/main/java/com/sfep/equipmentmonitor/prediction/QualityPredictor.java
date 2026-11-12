package com.sfep.equipmentmonitor.prediction;

import com.sfep.equipmentmonitor.risk.RiskScalar;

import java.util.Map;
import java.util.Set;

@FunctionalInterface
public interface QualityPredictor {
    QualityPrediction predict(Map<String, RiskScalar> values, Set<String> seenFields);
}
