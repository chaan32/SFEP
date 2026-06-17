package com.sfep.sfep_server.event.dto;

import com.sfep.sfep_server.equipment.domain.EquipmentStatus;
import com.sfep.sfep_server.equipment.domain.EquipmentType;
import com.sfep.sfep_server.event.domain.EventSeverity;
import com.sfep.sfep_server.event.domain.SensorEvent;

import java.time.Instant;

public record SensorEventResponse(
        Long id,
        String eventId,
        String equipmentId,
        EquipmentType equipmentType,
        EquipmentStatus status,
        EventSeverity severity,
        double temperature,
        double vibration,
        double rpm,
        double power,
        double currentValue,
        boolean anomaly,
        Instant occurredAt,
        Instant receivedAt
) {

    public static SensorEventResponse from(SensorEvent event) {
        return new SensorEventResponse(
                event.getId(),
                event.getEventId(),
                event.getEquipmentId(),
                event.getEquipmentType(),
                event.getStatus(),
                event.getSeverity(),
                event.getTemperature(),
                event.getVibration(),
                event.getRpm(),
                event.getPower(),
                event.getCurrentValue(),
                event.isAnomaly(),
                event.getOccurredAt(),
                event.getReceivedAt()
        );
    }
}
