package com.sfep.sfep_server.event.dto;

import com.sfep.sfep_server.equipment.domain.EquipmentStatus;
import com.sfep.sfep_server.equipment.domain.EquipmentType;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

import java.time.Instant;

public record SensorEventRequest(
        @NotBlank String eventId,
        @NotBlank String equipmentId,
        @NotNull EquipmentType equipmentType,
        @NotNull EquipmentStatus status,
        @NotNull Double temperature,
        @NotNull Double vibration,
        @NotNull Double rpm,
        @NotNull Double power,
        @NotNull Double currentValue,
        Instant occurredAt
) {
}
