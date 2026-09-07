package com.sfep.equipmentmonitor.state;

import com.sfep.equipmentmonitor.alert.HistoricalAlert;

import java.util.List;
import java.util.Map;
import java.util.Objects;

public record MonitorSnapshot(
        String bundleId,
        long unitsProcessed,
        long eventsProcessed,
        Map<String, MaterialSnapshot> materials,
        Map<EquipmentKey, EquipmentSnapshot> equipment,
        List<HistoricalAlert> alerts) {
    public MonitorSnapshot {
        Objects.requireNonNull(bundleId, "bundleId");
        materials = Map.copyOf(materials);
        equipment = Map.copyOf(equipment);
        alerts = List.copyOf(alerts);
    }
}
