package com.sfep.equipmentmonitor.risk;

import java.util.Map;
import java.util.Objects;

public record RiskEvent(
        ProcessStage stage,
        EquipmentType equipmentType,
        String equipmentId,
        Map<String, RiskScalar> values) {

    public RiskEvent {
        Objects.requireNonNull(stage, "stage");
        Objects.requireNonNull(equipmentType, "equipmentType");
        if (equipmentId == null || equipmentId.isBlank()) throw new IllegalArgumentException("equipmentId");
        values = Map.copyOf(values);
    }

    public static RiskEvent of(
            ProcessStage stage,
            EquipmentType equipmentType,
            String equipmentId,
            Map<String, RiskScalar> values) {
        return new RiskEvent(stage, equipmentType, equipmentId, values);
    }

    public Map<String, Object> rawValues() {
        return values.entrySet().stream().collect(java.util.stream.Collectors.toUnmodifiableMap(
                Map.Entry::getKey, entry -> entry.getValue().value()));
    }
}
