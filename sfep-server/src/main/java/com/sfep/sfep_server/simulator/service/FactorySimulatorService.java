package com.sfep.sfep_server.simulator.service;

import com.sfep.sfep_server.equipment.domain.EquipmentStatus;
import com.sfep.sfep_server.equipment.domain.EquipmentType;
import com.sfep.sfep_server.event.dto.DirectWriteResult;
import com.sfep.sfep_server.event.dto.SensorEventRequest;
import com.sfep.sfep_server.event.service.SensorEventService;
import com.sfep.sfep_server.kafka.dto.KafkaPublishResponse;
import com.sfep.sfep_server.kafka.dto.KafkaRunStatusResponse;
import com.sfep.sfep_server.kafka.service.KafkaRunTracker;
import com.sfep.sfep_server.kafka.service.SensorEventKafkaProducer;
import com.sfep.sfep_server.simulator.dto.SimulatorRunRequest;
import com.sfep.sfep_server.simulator.dto.SimulatorRunResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

// 가상의 설비 데이터 만드는 서비스 클래스
@Service
public class FactorySimulatorService {

    /**
     * equipmentCount = 설비 수
     * eventsPerEquipment = 설비당 이벤트 수
     * failureRatePercent = 장애 비율
     */

    // Direct DB Write 방식 사용
    private final SensorEventService sensorEventService;
    // 카프카 이벤트 발생 방식 사용
    private final SensorEventKafkaProducer sensorEventKafkaProducer;
    // 카프카의 runId 기준으로 publish, consume, save 상태 추적
    private final KafkaRunTracker kafkaRunTracker;
    private final int defaultEquipmentCount;
    private final int defaultEventsPerEquipment;

    public FactorySimulatorService(
            SensorEventService sensorEventService,
            SensorEventKafkaProducer sensorEventKafkaProducer,
            KafkaRunTracker kafkaRunTracker,
            @Value("${sfep.direct-write.default-equipment-count:100}") int defaultEquipmentCount,
            @Value("${sfep.direct-write.default-events-per-equipment:10}") int defaultEventsPerEquipment
    ) {
        this.sensorEventService = sensorEventService;
        this.sensorEventKafkaProducer = sensorEventKafkaProducer;
        this.kafkaRunTracker = kafkaRunTracker;
        this.defaultEquipmentCount = defaultEquipmentCount;
        this.defaultEventsPerEquipment = defaultEventsPerEquipment;
    }

    /**
     * 카프카 없이 바로 DB 저장
     * @param request
     * @return
     */
    public SimulatorRunResponse runDirectWrite(SimulatorRunRequest request) {
        // 요청이 있으면 Request의 요청 개수대로, 없으면 default로
        int equipmentCount = request.equipmentCount() == null ? defaultEquipmentCount : request.equipmentCount();
        int eventsPerEquipment = request.eventsPerEquipment() == null
                ? defaultEventsPerEquipment
                : request.eventsPerEquipment();
        int failureRatePercent = request.failureRatePercent() == null ? 2 : request.failureRatePercent();


        List<SensorEventRequest> events = generateEvents(equipmentCount, eventsPerEquipment, failureRatePercent);
        DirectWriteResult result = sensorEventService.saveDirectBatch(events);
        return SimulatorRunResponse.of(equipmentCount, eventsPerEquipment, result, "DIRECT_DB_WRITE");
    }

    /**
     * 카프카로 발행하기 ; 이벤트를 kafka에 넣고 비동기 처리 실험을 시작하는 것
     * @param request
     * @return
     */
    public KafkaPublishResponse runKafkaWrite(SimulatorRunRequest request) {
        int equipmentCount = request.equipmentCount() == null ? defaultEquipmentCount : request.equipmentCount();
        int eventsPerEquipment = request.eventsPerEquipment() == null
                ? defaultEventsPerEquipment
                : request.eventsPerEquipment();
        int failureRatePercent = request.failureRatePercent() == null ? 2 : request.failureRatePercent();

        List<SensorEventRequest> events = generateEvents(equipmentCount, eventsPerEquipment, failureRatePercent);
        // 카프카로 발행하기!
        return sensorEventKafkaProducer.publish(equipmentCount, eventsPerEquipment, events);
    }

    /**
     * 카프카의 고유 runId로 상태를 조회하는 메소드
     * @param runId
     * @return
     */
    public KafkaRunStatusResponse getKafkaRunStatus(String runId) {
        return kafkaRunTracker.get(runId);
    }

    /**
     * EQ-0001, EQ-0002 같은 설비 이벤트들이 만듦 (리스트)
     * @param equipmentCount
     * @param eventsPerEquipment
     * @param failureRatePercent
     * @return
     */
    private List<SensorEventRequest> generateEvents(
            int equipmentCount,
            int eventsPerEquipment,
            int failureRatePercent
    ) {
        List<SensorEventRequest> events = new ArrayList<>(equipmentCount * eventsPerEquipment);
        EquipmentType[] types = EquipmentType.values();
        for (int equipmentIndex = 1; equipmentIndex <= equipmentCount; equipmentIndex++) {
            /*
                1   -> EQ-0001
                2   -> EQ-0002
                15  -> EQ-0015
                100 -> EQ-0100
                이런 식으로 장비 ID 생성
             */
            String equipmentId = "EQ-%04d".formatted(equipmentIndex);
            // 타입은 그냥 순차적으로
            EquipmentType type = types[(equipmentIndex - 1) % types.length];
            for (int eventIndex = 0; eventIndex < eventsPerEquipment; eventIndex++) {
                events.add(generateEvent(equipmentId, type, failureRatePercent));
            }
        }
        return events;
    }

    /**
     * 이벤트 생성기 1개
     * @param equipmentId 장비 id
     * @param type 장비 type
     * @param failureRatePercent 장애 비율 ; 장애 비율에 따라서 장애 발생
     * @return
     */
    private SensorEventRequest generateEvent(String equipmentId, EquipmentType type, int failureRatePercent) {
        /**
         * FAILURE
         * WARNING
         * IDLE
         * RUNNING
         * 에 따라서 값이 좀 달라짐
         */

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
                UUID.randomUUID().toString(), // eventId
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
