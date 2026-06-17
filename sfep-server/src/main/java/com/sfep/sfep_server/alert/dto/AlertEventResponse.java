package com.sfep.sfep_server.alert.dto;

import com.sfep.sfep_server.equipment.domain.EquipmentType;
import com.sfep.sfep_server.event.domain.EventSeverity;
import com.sfep.sfep_server.event.domain.SensorEvent;

import java.time.Duration;
import java.time.Instant;

public record AlertEventResponse(
        String eventId,
        String equipmentId,
        EquipmentType equipmentType,
        EventSeverity severity,
        double temperature,
        double vibration,
        double currentValue,
        Instant occurredAt,
        Instant receivedAt,
        Instant alertPublishedAt,
        long alertLatencyMs,
        String message
) {

    public static AlertEventResponse from(SensorEvent event) {
        Instant alertPublishedAt = Instant.now();
        return new AlertEventResponse(
                event.getEventId(),
                event.getEquipmentId(),
                event.getEquipmentType(),
                event.getSeverity(),
                event.getTemperature(),
                event.getVibration(),
                event.getCurrentValue(),
                event.getOccurredAt(),
                event.getReceivedAt(),
                alertPublishedAt,
                Duration.between(event.getOccurredAt(), alertPublishedAt).toMillis(),
                "%s 설비에서 %s 위험 이벤트 발생".formatted(event.getEquipmentId(), event.getSeverity())
        );
    }
}
