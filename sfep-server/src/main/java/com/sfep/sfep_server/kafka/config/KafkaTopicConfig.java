package com.sfep.sfep_server.kafka.config;

import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;

@Configuration
public class KafkaTopicConfig {

    @Bean
    public NewTopic sensorEventsTopic(
            @Value("${sfep.kafka.sensor-events-topic:sfep.sensor-events}") String topic,
            @Value("${sfep.kafka.partitions:6}") int partitions,
            @Value("${sfep.kafka.replication-factor:1}") short replicationFactor
    ) {
        return TopicBuilder.name(topic)
                .partitions(partitions)
                .replicas(replicationFactor)
                .build();
    }

    @Bean
    public NewTopic sensorEventsDeadLetterTopic(
            @Value("${sfep.kafka.sensor-events-topic:sfep.sensor-events}") String topic,
            @Value("${sfep.kafka.partitions:6}") int partitions,
            @Value("${sfep.kafka.replication-factor:1}") short replicationFactor
    ) {
        return TopicBuilder.name(topic + ".DLT")
                .partitions(partitions)
                .replicas(replicationFactor)
                .build();
    }
}
