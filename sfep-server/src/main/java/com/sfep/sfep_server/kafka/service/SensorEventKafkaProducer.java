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

@Service
public class SensorEventKafkaProducer {

    private final KafkaTemplate<String, KafkaSensorEventMessage> kafkaTemplate;
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

    public KafkaPublishResponse publish(int equipmentCount, int eventsPerEquipment, List<SensorEventRequest> events) {
        if (events.size() > maxEventsPerRequest) {
            throw new IllegalArgumentException("events size must be less than or equal to " + maxEventsPerRequest);
        }

        String runId = UUID.randomUUID().toString();
        kafkaRunTracker.start(runId, events.size());

        long start = System.nanoTime();
        CompletableFuture<?>[] futures = events.stream()
                .map(event -> kafkaTemplate.send(
                        topic,
                        event.equipmentId(),
                        new KafkaSensorEventMessage(runId, event)
                ))
                .toArray(CompletableFuture[]::new);

        CompletableFuture.allOf(futures).join();
        long elapsedMs = Math.max(1, (System.nanoTime() - start) / 1_000_000);

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
