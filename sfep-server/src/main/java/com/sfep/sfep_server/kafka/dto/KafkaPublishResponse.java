package com.sfep.sfep_server.kafka.dto;

public record KafkaPublishResponse(
        String runId,
        int equipmentCount,
        int eventsPerEquipment,
        int generatedEvents,
        int publishedEvents,
        long publishElapsedMs,
        double publishEventsPerSecond,
        String mode
) {

    public static KafkaPublishResponse of(
            String runId,
            int equipmentCount,
            int eventsPerEquipment,
            int generatedEvents,
            int publishedEvents,
            long publishElapsedMs
    ) {
        double seconds = Math.max(publishElapsedMs, 1) / 1000.0;
        return new KafkaPublishResponse(
                runId,
                equipmentCount,
                eventsPerEquipment,
                generatedEvents,
                publishedEvents,
                publishElapsedMs,
                publishedEvents / seconds,
                "KAFKA_PUBLISH"
        );
    }
}
