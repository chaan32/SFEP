package com.sfep.sfep_server.kafka.service;

import com.sfep.sfep_server.event.dto.SensorEventRequest;
import com.sfep.sfep_server.kafka.dto.KafkaPublishResponse;
import com.sfep.sfep_server.kafka.dto.KafkaSensorEventMessage;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;

// 시뮬레이터가 만든 센서 이벤트들을 Kafka Topic으로 발행하는 서비스
// 특히 Kafka에 Produce하기만 함
@Service
public class SensorEventKafkaProducer {
    // key  : equipmentValue
    // value : KafkaSensorEventMessage that contains runId. So it will be easy to find the message.
    // the reason using eqValues with key is to know the order of Events.
    private final KafkaTemplate<String, KafkaSensorEventMessage> kafkaTemplate;
    // the Tracker of Produced Messages with RunId.
    private final KafkaRunTracker kafkaRunTracker;
    private final String topic;
    private final int maxEventsPerRequest;

    public SensorEventKafkaProducer(
            KafkaTemplate<String, KafkaSensorEventMessage> kafkaTemplate,
            KafkaRunTracker kafkaRunTracker,
            @Value("${sfep.kafka.sensor-events-topic:sfep.sensor-events}") String topic,
            @Value("${sfep.kafka.max-events-per-request:100000}") int maxEventsPerRequest
    ) {
        this.kafkaTemplate = kafkaTemplate;
        this.kafkaRunTracker = kafkaRunTracker;
        this.topic = topic;
        this.maxEventsPerRequest = maxEventsPerRequest;
    }

    /**
     * Main Method.
     *
     * @param equipmentCount Using this simulation
     * @param eventsPerEquipment
     * @param events List of Event to produce
     * @return
     */
    public KafkaPublishResponse publish(int equipmentCount, int eventsPerEquipment, List<SensorEventRequest> events) {
        if (events.size() > maxEventsPerRequest) {
            throw new IllegalArgumentException("events size must be less than or equal to " + maxEventsPerRequest);
        }

        String runId = UUID.randomUUID().toString();
        kafkaRunTracker.start(runId, events.size());

        long start = System.nanoTime();
        /**
         *     [example Message]
         *          Topic: sfep.sensor-events
         *          Key: EQ-0001
         *          Value:
         *              {
         *                  runId: "abc-123",
         *                  event: {
         *                          eventId : "123132131fasdsavv.."
         *                          equipmentId: "EQ-0001",
         *                          temperature: 91.2,
         *                          vibration: 0.8,
         *                              ...
         *                          }
         *              }
         */
        CompletableFuture<?>[] futures = events.stream()
                .map(event -> kafkaTemplate.send(
                        topic,
                        event.equipmentId(),
                        new KafkaSensorEventMessage(runId, event)
                ))
                .toArray(CompletableFuture[]::new);

        // Waiting until all the messages were published
        CompletableFuture.allOf(futures).join();
        long elapsedMs = Math.max(1, (System.nanoTime() - start) / 1_000_000);

        // record it published
        kafkaRunTracker.markPublished(runId, events.size(), elapsedMs);
        return KafkaPublishResponse.of(
                runId,
                equipmentCount,
                eventsPerEquipment,
                events.size(),
                events.size(),
                elapsedMs
        );
    }
}
