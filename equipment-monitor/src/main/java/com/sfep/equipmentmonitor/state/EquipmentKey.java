package com.sfep.equipmentmonitor.state;

import java.util.Objects;

public record EquipmentKey(String equipmentType, String equipmentId) {
    public EquipmentKey {
        Objects.requireNonNull(equipmentType, "equipmentType");
        Objects.requireNonNull(equipmentId, "equipmentId");
    }
}
