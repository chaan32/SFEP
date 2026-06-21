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

// Consumer Kafka messages.
@Component
public class SensorEventKafkaConsumer {

    // To save the events that consumed from kafka
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
            // event-Consumer
            groupId = "${sfep.kafka.consumer-group:sfep-event-processor}"
    )
    public void consume(List<KafkaSensorEventMessage> messages) {
        // Grouping messages by RunId
        Map<String, List<KafkaSensorEventMessage>> messagesByRunId =
                messages.stream().collect(Collectors.groupingBy(KafkaSensorEventMessage::runId));

        for (Map.Entry<String, List<KafkaSensorEventMessage>> entry : messagesByRunId.entrySet()) {
            // KafkaSensorEventMessage Object
            String runId = entry.getKey();
            // Convert KafkaSensorEventMessage to SensorEventRequest to save that using that way
            List<SensorEventRequest> events = entry.getValue().stream()
                    .map(KafkaSensorEventMessage::event)
                    .toList();
            // Save to DB
            DirectWriteResult result = sensorEventService.saveDirectBatch(events, false);
            // Alert to tracker
            kafkaRunTracker.addSaved(runId, events.size(), result.savedEvents());
        }
    }
}
