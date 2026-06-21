package com.sfep.sfep_server.event.service;

import com.sfep.sfep_server.alert.service.AlertEventPublisher;
import com.sfep.sfep_server.equipment.domain.Equipment;
import com.sfep.sfep_server.equipment.domain.EquipmentStatus;
import com.sfep.sfep_server.equipment.repository.EquipmentRepository;
import com.sfep.sfep_server.event.domain.EventSeverity;
import com.sfep.sfep_server.event.domain.SensorEvent;
import com.sfep.sfep_server.event.dto.DirectWriteResult;
import com.sfep.sfep_server.event.dto.SensorEventRequest;
import com.sfep.sfep_server.event.dto.SensorEventResponse;
import com.sfep.sfep_server.event.repository.SensorEventRepository;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.function.Function;
import java.util.stream.Collectors;

@Service
public class SensorEventService {

    private final SensorEventRepository sensorEventRepository;
    private final EquipmentRepository equipmentRepository;
    private final AlertEventPublisher alertEventPublisher;
    private final EventRiskAnalyzer riskAnalyzer;
    private final int maxEventsPerRequest;
    private final JdbcTemplate jdbcTemplate;
    private final int jdbcBatchSize;

    public SensorEventService(
            SensorEventRepository sensorEventRepository,
            EquipmentRepository equipmentRepository,
            AlertEventPublisher alertEventPublisher,
            EventRiskAnalyzer riskAnalyzer,
            JdbcTemplate jdbcTemplate,
            @Value("${sfep.direct-write.max-events-per-request:50000}") int maxEventsPerRequest,
            @Value("${sfep.direct-write.jdbc-batch-size:1000}") int jdbcBatchSize
    ) {
        this.sensorEventRepository = sensorEventRepository;
        this.equipmentRepository = equipmentRepository;
        this.alertEventPublisher = alertEventPublisher;
        this.riskAnalyzer = riskAnalyzer;
        this.jdbcTemplate = jdbcTemplate;
        this.maxEventsPerRequest = maxEventsPerRequest;
        this.jdbcBatchSize = jdbcBatchSize;
    }

    /**
     * Saving Single Event
     * @param request Event Object that contains specific information (ex : value, power, rpm etc. )
     * @return
     */
    @Transactional
    public SensorEventResponse saveDirect(SensorEventRequest request) {
        SensorEvent saved = sensorEventRepository.save(toEvent(request));
        upsertEquipment(saved);
        alertEventPublisher.publishIfNeeded(saved);
        return SensorEventResponse.from(saved);
    }

    /**
     * Saving Many Events
     * @param requests
     * @return
     */
    @Transactional
    public DirectWriteResult saveDirectBatch(List<SensorEventRequest> requests) {
        return saveDirectBatch(requests, true);
    }

    @Transactional
    public DirectWriteResult saveDirectBatch(List<SensorEventRequest> requests, boolean publishAlerts) {
        if (requests.size() > maxEventsPerRequest) {
            throw new IllegalArgumentException("events size must be less than or equal to " + maxEventsPerRequest);
        }

        long start = System.nanoTime();

        // Convert SensorEventRequest Object to SensorEvent Objet by using stream
        List<SensorEvent> events = requests.stream()
                .map(this::toEvent)
                .toList();

        int savedEvents = batchInsertEvents(events);
        batchUpsertLatestEquipment(events);
        if (publishAlerts) {
            events.forEach(alertEventPublisher::publishIfNeeded);
        }

        long elapsedMs = (System.nanoTime() - start) / 1_000_000;
        return DirectWriteResult.of(requests.size(), savedEvents, elapsedMs);
    }

    /**
     * Saving Lots of Event to Repository by Using JDBC. Because of Size of Batch. When Using JPA, it will be slow. So Using JDBC and 'on conflict'.
     * @param events
     * @return
     */
    private int batchInsertEvents(List<SensorEvent> events) {
        String sql = """
                insert into sensor_event (
                    event_id,
                    equipment_id,
                    equipment_type,
                    status,
                    severity,
                    temperature,
                    vibration,
                    rpm,
                    power,
                    current_value,
                    anomaly,
                    occurred_at,
                    received_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict (event_id) do nothing
                """;

        int[][] results = jdbcTemplate.batchUpdate(sql, events, jdbcBatchSize, (ps, event) -> {
            ps.setString(1, event.getEventId());
            ps.setString(2, event.getEquipmentId());
            ps.setString(3, event.getEquipmentType().name());
            ps.setString(4, event.getStatus().name());
            ps.setString(5, event.getSeverity().name());
            ps.setDouble(6, event.getTemperature());
            ps.setDouble(7, event.getVibration());
            ps.setDouble(8, event.getRpm());
            ps.setDouble(9, event.getPower());
            ps.setDouble(10, event.getCurrentValue());
            ps.setBoolean(11, event.isAnomaly());
            ps.setTimestamp(12, Timestamp.from(event.getOccurredAt()));
            ps.setTimestamp(13, Timestamp.from(event.getReceivedAt()));
        });

        return countBatchUpdates(results);
    }

    private void batchUpsertLatestEquipment(List<SensorEvent> events) {
        if (events.isEmpty()) {
            return;
        }

        Instant now = Instant.now();
        List<SensorEvent> latestEvents = events.stream()
                .collect(Collectors.toMap(
                        SensorEvent::getEquipmentId,
                        Function.identity(),
                        (left, right) -> Comparator
                                .comparing(SensorEvent::getOccurredAt)
                                .compare(left, right) >= 0 ? left : right
                ))
                .values()
                .stream()
                .toList();

        String sql = """
                insert into equipment (
                    equipment_id,
                    type,
                    status,
                    last_event_at,
                    created_at,
                    updated_at
                )
                values (?, ?, ?, ?, ?, ?)
                on conflict (equipment_id) do update set
                    type = excluded.type,
                    status = excluded.status,
                    last_event_at = excluded.last_event_at,
                    updated_at = excluded.updated_at
                """;

        jdbcTemplate.batchUpdate(sql, latestEvents, jdbcBatchSize, (ps, event) -> {
            ps.setString(1, event.getEquipmentId());
            ps.setString(2, event.getEquipmentType().name());
            ps.setString(3, event.getStatus().name());
            ps.setTimestamp(4, Timestamp.from(event.getOccurredAt()));
            ps.setTimestamp(5, Timestamp.from(now));
            ps.setTimestamp(6, Timestamp.from(now));
        });
    }

    private int countBatchUpdates(int[][] results) {
        int count = 0;
        for (int[] batch : results) {
            for (int result : batch) {
                if (result > 0 || result == java.sql.Statement.SUCCESS_NO_INFO) {
                    count++;
                }
            }
        }
        return count;
    }

    /**
     * Convert to SensorEvent Object that is the way to save Repository
     * @param request
     * @return
     */
    private SensorEvent toEvent(SensorEventRequest request) {
        Instant now = Instant.now();
        Instant occurredAt = request.occurredAt() == null ? now : request.occurredAt();
        EventSeverity severity = riskAnalyzer.analyze(request);
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

    /**
     * Update the Equipment status. Because if calculated Evnet status was WARNING or FAIL, It is important to know which equipment is Fail or Warning.
     * The equipment is influenced from Event
     * And The reason why save status of the equipment is the way that makes easy to find equipment status without events.
     * @param event Event Object. It will be using at finding Equipment from Repository
     */
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

}
