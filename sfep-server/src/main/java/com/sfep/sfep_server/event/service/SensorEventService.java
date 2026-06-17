package com.sfep.sfep_server.event.service;

import com.sfep.sfep_server.alert.dto.AlertEventResponse;
import com.sfep.sfep_server.alert.service.AlertEventBus;
import com.sfep.sfep_server.equipment.domain.Equipment;
import com.sfep.sfep_server.equipment.domain.EquipmentStatus;
import com.sfep.sfep_server.equipment.repository.EquipmentRepository;
import com.sfep.sfep_server.event.domain.EventSeverity;
import com.sfep.sfep_server.event.domain.SensorEvent;
import com.sfep.sfep_server.event.dto.DirectWriteResult;
import com.sfep.sfep_server.event.dto.SensorEventRequest;
import com.sfep.sfep_server.event.dto.SensorEventResponse;
import com.sfep.sfep_server.event.repository.SensorEventRepository;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;

@Service
public class SensorEventService {

    private final SensorEventRepository sensorEventRepository;
    private final EquipmentRepository equipmentRepository;
    private final AlertEventBus alertEventBus;
    private final int maxEventsPerRequest;

    public SensorEventService(
            SensorEventRepository sensorEventRepository,
            EquipmentRepository equipmentRepository,
            AlertEventBus alertEventBus,
            @Value("${sfep.direct-write.max-events-per-request:50000}") int maxEventsPerRequest
    ) {
        this.sensorEventRepository = sensorEventRepository;
        this.equipmentRepository = equipmentRepository;
        this.alertEventBus = alertEventBus;
        this.maxEventsPerRequest = maxEventsPerRequest;
    }

    @Transactional
    public SensorEventResponse saveDirect(SensorEventRequest request) {
        SensorEvent saved = sensorEventRepository.save(toEvent(request));
        upsertEquipment(saved);
        publishAlertIfNeeded(saved);
        return SensorEventResponse.from(saved);
    }

    @Transactional
    public DirectWriteResult saveDirectBatch(List<SensorEventRequest> requests) {
        if (requests.size() > maxEventsPerRequest) {
            throw new IllegalArgumentException("events size must be less than or equal to " + maxEventsPerRequest);
        }

        long start = System.nanoTime();
        int savedEvents = 0;
        for (SensorEventRequest request : requests) {
            SensorEvent saved = sensorEventRepository.save(toEvent(request));
            upsertEquipment(saved);
            publishAlertIfNeeded(saved);
            savedEvents++;
        }
        long elapsedMs = (System.nanoTime() - start) / 1_000_000;
        return DirectWriteResult.of(requests.size(), savedEvents, elapsedMs);
    }

    private SensorEvent toEvent(SensorEventRequest request) {
        Instant now = Instant.now();
        Instant occurredAt = request.occurredAt() == null ? now : request.occurredAt();
        EventSeverity severity = analyzeSeverity(request);
        return new SensorEvent(
                request.eventId(),
                request.equipmentId(),
                request.equipmentType(),
                request.status(),
                severity,
                request.temperature(),
                request.vibration(),
                request.rpm(),
                request.power(),
                request.currentValue(),
                severity != EventSeverity.NORMAL,
                occurredAt,
                now
        );
    }

    private EventSeverity analyzeSeverity(SensorEventRequest request) {
        if (request.status() == EquipmentStatus.FAILURE
                || request.temperature() >= 90.0
                || request.vibration() >= 8.0
                || request.currentValue() >= 80.0) {
            return EventSeverity.CRITICAL;
        }
        if (request.status() == EquipmentStatus.WARNING
                || request.temperature() >= 75.0
                || request.vibration() >= 5.0
                || request.currentValue() >= 60.0) {
            return EventSeverity.WARNING;
        }
        return EventSeverity.NORMAL;
    }

    private void upsertEquipment(SensorEvent event) {
        Instant now = Instant.now();
        Equipment equipment = equipmentRepository.findById(event.getEquipmentId())
                .orElseGet(() -> new Equipment(
                        event.getEquipmentId(),
                        event.getEquipmentType(),
                        event.getStatus(),
                        event.getOccurredAt()
                ));
        equipment.updateLatestStatus(event.getEquipmentType(), event.getStatus(), event.getOccurredAt(), now);
        equipmentRepository.save(equipment);
    }

    private void publishAlertIfNeeded(SensorEvent event) {
        if (event.getSeverity() == EventSeverity.NORMAL) {
            return;
        }
        alertEventBus.publish(AlertEventResponse.from(event));
    }
}
