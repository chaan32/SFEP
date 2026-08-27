package com.sfep.equipmentmonitor.state;

import com.sfep.equipmentmonitor.risk.RiskScalar;

import java.util.Map;
import java.util.List;
import java.util.LinkedHashMap;
import java.util.Objects;

public record EquipmentSnapshot(
        EquipmentKey key,
        String eventStage,
        String replayDate,
        Integer replayHour,
        String timePrecision,
        List<String> materialKeys,
        Map<String, Map<String, RiskScalar>> valuesByMaterial,
        boolean orderedWithinUnit) {
    public EquipmentSnapshot {
        Objects.requireNonNull(key, "key");
        Objects.requireNonNull(eventStage, "eventStage");
        Objects.requireNonNull(replayDate, "replayDate");
        Objects.requireNonNull(timePrecision, "timePrecision");
        materialKeys = List.copyOf(materialKeys);
        LinkedHashMap<String, Map<String, RiskScalar>> stable = new LinkedHashMap<>();
        valuesByMaterial.forEach((material, values) -> stable.put(material, Map.copyOf(values)));
        valuesByMaterial = Map.copyOf(stable);
        if (orderedWithinUnit) {
            throw new IllegalArgumentException("equipment batch must not claim material order");
        }
    }
}
