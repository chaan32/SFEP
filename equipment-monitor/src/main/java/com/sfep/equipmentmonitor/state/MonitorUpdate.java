package com.sfep.equipmentmonitor.state;

import com.sfep.equipmentmonitor.alert.HistoricalAlert;
import com.sfep.equipmentmonitor.replay.ReplayUnit;

import java.util.List;
import java.util.Map;

public record MonitorUpdate(
        ReplayUnit unit,
        long unitsProcessed,
        long eventsProcessed,
        Map<String, MaterialSnapshot> changedMaterials,
        Map<EquipmentKey, EquipmentSnapshot> changedEquipment,
        List<HistoricalAlert> newAlerts) {
    public MonitorUpdate {
        changedMaterials = Map.copyOf(changedMaterials);
        changedEquipment = Map.copyOf(changedEquipment);
        newAlerts = List.copyOf(newAlerts);
    }
}
