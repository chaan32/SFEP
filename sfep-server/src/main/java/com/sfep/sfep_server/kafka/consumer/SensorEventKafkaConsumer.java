package com.sfep.sfep_server.kafka.consumer;

import com.sfep.sfep_server.event.dto.DirectWriteResult;
import com.sfep.sfep_server.event.dto.SensorEventRequest;
import com.sfep.sfep_server.event.service.SensorEventService;
import com.sfep.sfep_server.kafka.dto.KafkaSensorEventMessage;
import com.sfep.sfep_server.kafka.service.KafkaRunTracker;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Component
public class SensorEventKafkaConsumer {

    private final SensorEventService sensorEventService;
    private final KafkaRunTracker kafkaRunTracker;

    public SensorEventKafkaConsumer(
            SensorEventService sensorEventService,
            KafkaRunTracker kafkaRunTracker
    ) {
        this.sensorEventService = sensorEventService;
        this.kafkaRunTracker = kafkaRunTracker;
    }

    @KafkaListener(
            topics = "${sfep.kafka.sensor-events-topic:sfep.sensor-events}",
            groupId = "${sfep.kafka.consumer-group:sfep-event-processor}"
    )
    public void consume(List<KafkaSensorEventMessage> messages) {
        Map<String, List<KafkaSensorEventMessage>> messagesByRunId = messages.stream()
                .collect(Collectors.groupingBy(KafkaSensorEventMessage::runId));

        for (Map.Entry<String, List<KafkaSensorEventMessage>> entry : messagesByRunId.entrySet()) {
            String runId = entry.getKey();
            List<SensorEventRequest> events = entry.getValue().stream()
                    .map(KafkaSensorEventMessage::event)
                    .toList();
            DirectWriteResult result = sensorEventService.saveDirectBatch(events, false);
            kafkaRunTracker.addSaved(runId, events.size(), result.savedEvents());
        }
    }
}
