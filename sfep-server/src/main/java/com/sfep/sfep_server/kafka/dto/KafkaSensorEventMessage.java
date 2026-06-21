package com.sfep.sfep_server.kafka.dto;

import com.sfep.sfep_server.event.dto.SensorEventRequest;

public record KafkaSensorEventMessage(
        String runId,
        SensorEventRequest event
) {
}
