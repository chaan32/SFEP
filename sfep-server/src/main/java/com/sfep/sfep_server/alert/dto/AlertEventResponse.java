package com.sfep.sfep_server.alert.dto;

import com.sfep.sfep_server.equipment.domain.EquipmentType;
import com.sfep.sfep_server.event.domain.EventSeverity;
import com.sfep.sfep_server.event.domain.SensorEvent;
import com.sfep.sfep_server.event.dto.SensorEventRequest;

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
        // 서버에서 위험하다고 판단한 시간!
        Instant alertPublishedAt,
        // alertPublishedAt - occurredAt
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

    public static AlertEventResponse from(SensorEventRequest request, EventSeverity severity) {
        Instant alertPublishedAt = Instant.now();
        Instant occurredAt = request.occurredAt() == null ? alertPublishedAt : request.occurredAt();
        return new AlertEventResponse(
                request.eventId(),
                request.equipmentId(),
                request.equipmentType(),
                severity,
                request.temperature(),
                request.vibration(),
                request.currentValue(),
                occurredAt,
                alertPublishedAt,
                alertPublishedAt,
                Duration.between(occurredAt, alertPublishedAt).toMillis(),
                "%s 설비에서 %s 위험 이벤트 발생".formatted(request.equipmentId(), severity)
        );
    }
}
