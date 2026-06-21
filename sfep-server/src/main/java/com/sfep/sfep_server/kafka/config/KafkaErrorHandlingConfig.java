package com.sfep.sfep_server.kafka.config;

import com.sfep.sfep_server.kafka.dto.KafkaSensorEventMessage;
import org.apache.kafka.common.TopicPartition;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.util.backoff.FixedBackOff;

@Configuration
public class KafkaErrorHandlingConfig {

    @Bean
    public DefaultErrorHandler kafkaDefaultErrorHandler(
            KafkaTemplate<String, KafkaSensorEventMessage> kafkaTemplate,
            @Value("${sfep.kafka.retry-interval-ms:1000}") long retryIntervalMs,
            @Value("${sfep.kafka.retry-max-attempts:3}") long retryMaxAttempts
    ) {
        DeadLetterPublishingRecoverer recoverer = new DeadLetterPublishingRecoverer(
                kafkaTemplate,
                (record, exception) -> new TopicPartition(record.topic() + ".DLT", record.partition())
        );
        return new DefaultErrorHandler(recoverer, new FixedBackOff(retryIntervalMs, retryMaxAttempts));
    }
}
