package com.sfep.sfep_server.simulator.service;

import com.sfep.sfep_server.equipment.domain.EquipmentStatus;
import com.sfep.sfep_server.equipment.domain.EquipmentType;
import com.sfep.sfep_server.event.dto.DirectWriteResult;
import com.sfep.sfep_server.event.dto.SensorEventRequest;
import com.sfep.sfep_server.event.service.SensorEventService;
import com.sfep.sfep_server.simulator.dto.SimulatorRunRequest;
import com.sfep.sfep_server.simulator.dto.SimulatorRunResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

@Service
public class FactorySimulatorService {

    private final SensorEventService sensorEventService;
    private final int defaultEquipmentCount;
    private final int defaultEventsPerEquipment;

    public FactorySimulatorService(
            SensorEventService sensorEventService,
            @Value("${sfep.direct-write.default-equipment-count:100}") int defaultEquipmentCount,
            @Value("${sfep.direct-write.default-events-per-equipment:10}") int defaultEventsPerEquipment
    ) {
        this.sensorEventService = sensorEventService;
        this.defaultEquipmentCount = defaultEquipmentCount;
        this.defaultEventsPerEquipment = defaultEventsPerEquipment;
    }

    public SimulatorRunResponse runDirectWrite(SimulatorRunRequest request) {
        int equipmentCount = request.equipmentCount() == null ? defaultEquipmentCount : request.equipmentCount();
        int eventsPerEquipment = request.eventsPerEquipment() == null
                ? defaultEventsPerEquipment
                : request.eventsPerEquipment();
        int failureRatePercent = request.failureRatePercent() == null ? 2 : request.failureRatePercent();

        List<SensorEventRequest> events = generateEvents(equipmentCount, eventsPerEquipment, failureRatePercent);
        DirectWriteResult result = sensorEventService.saveDirectBatch(events);
        return SimulatorRunResponse.of(equipmentCount, eventsPerEquipment, result, "DIRECT_DB_WRITE");
    }

    private List<SensorEventRequest> generateEvents(
            int equipmentCount,
            int eventsPerEquipment,
            int failureRatePercent
    ) {
        List<SensorEventRequest> events = new ArrayList<>(equipmentCount * eventsPerEquipment);
        EquipmentType[] types = EquipmentType.values();
        for (int equipmentIndex = 1; equipmentIndex <= equipmentCount; equipmentIndex++) {
            String equipmentId = "EQ-%04d".formatted(equipmentIndex);
            EquipmentType type = types[(equipmentIndex - 1) % types.length];
            for (int eventIndex = 0; eventIndex < eventsPerEquipment; eventIndex++) {
                events.add(generateEvent(equipmentId, type, failureRatePercent));
            }
        }
        return events;
    }

    private SensorEventRequest generateEvent(String equipmentId, EquipmentType type, int failureRatePercent) {
        ThreadLocalRandom random = ThreadLocalRandom.current();
        EquipmentStatus status = randomStatus(random, failureRatePercent);
        boolean failure = status == EquipmentStatus.FAILURE;
        boolean warning = status == EquipmentStatus.WARNING;

        double temperature = random.nextDouble(45.0, 72.0);
        double vibration = random.nextDouble(0.4, 4.2);
        double currentValue = random.nextDouble(20.0, 52.0);

        if (warning) {
            temperature = random.nextDouble(75.0, 88.0);
            vibration = random.nextDouble(5.0, 7.5);
            currentValue = random.nextDouble(60.0, 78.0);
        }
        if (failure) {
            temperature = random.nextDouble(90.0, 115.0);
            vibration = random.nextDouble(8.0, 13.0);
            currentValue = random.nextDouble(80.0, 110.0);
        }

        return new SensorEventRequest(
                UUID.randomUUID().toString(),
                equipmentId,
                type,
                status,
                round(temperature),
                round(vibration),
                round(random.nextDouble(650.0, 2400.0)),
                round(random.nextDouble(2.0, 9.5)),
                round(currentValue),
                Instant.now()
        );
    }

    private EquipmentStatus randomStatus(ThreadLocalRandom random, int failureRatePercent) {
        int roll = random.nextInt(100);
        if (roll < failureRatePercent) {
            return EquipmentStatus.FAILURE;
        }
        if (roll < failureRatePercent + 8) {
            return EquipmentStatus.WARNING;
        }
        if (roll < failureRatePercent + 18) {
            return EquipmentStatus.IDLE;
        }
        return EquipmentStatus.RUNNING;
    }

    private double round(double value) {
        return Math.round(value * 100.0) / 100.0;
    }
}
