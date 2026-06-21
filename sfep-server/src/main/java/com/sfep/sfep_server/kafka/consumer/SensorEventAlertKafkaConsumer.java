package com.sfep.sfep_server.kafka.consumer;

import com.sfep.sfep_server.alert.service.AlertEventPublisher;
import com.sfep.sfep_server.event.dto.SensorEventRequest;
import com.sfep.sfep_server.kafka.dto.KafkaSensorEventMessage;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
public class SensorEventAlertKafkaConsumer {

    private final AlertEventPublisher alertEventPublisher;

    public SensorEventAlertKafkaConsumer(AlertEventPublisher alertEventPublisher) {
        this.alertEventPublisher = alertEventPublisher;
    }

    /**
     * Consume the messages By Another GroupId to alert
     * @param messages Msgs in Kafka
     */
    @KafkaListener(
            topics = "${sfep.kafka.sensor-events-topic:sfep.sensor-events}",
            // alert-Consumer
            groupId = "${sfep.kafka.alert-consumer-group:sfep-alert-processor}",
            autoStartup = "${sfep.kafka.alert-consumer-enabled:true}"
    )
    public void consumeAlerts(List<KafkaSensorEventMessage> messages) {
        List<SensorEventRequest> events = messages.stream()
                .map(KafkaSensorEventMessage::event)
                .toList();
        alertEventPublisher.publishAlerts(events);
    }
}
