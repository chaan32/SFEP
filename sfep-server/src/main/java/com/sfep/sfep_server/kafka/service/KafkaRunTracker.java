package com.sfep.sfep_server.kafka.service;

import com.sfep.sfep_server.kafka.dto.KafkaRunStatusResponse;
import org.springframework.stereotype.Component;

import java.util.NoSuchElementException;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

// Tracker of Kafka Messages
@Component
public class KafkaRunTracker {

    private final ConcurrentMap<String, KafkaRunState> states = new ConcurrentHashMap<>();

    /**
     * Note the Running
     * @param runId Id of Kafka Executing
     * @param expectedEvents size of Events
     */
    public void start(String runId, int expectedEvents) {
        states.put(runId, new KafkaRunState(runId, expectedEvents));
    }

    public void markPublished(String runId, int publishedEvents, long elapsedMs) {
        state(runId).markPublished(publishedEvents, elapsedMs);
    }

    public void addSaved(String runId, int consumedEvents, int savedEvents) {
        KafkaRunState state = states.get(runId);
        if (state == null) {
            return;
        }
        state.addSaved(consumedEvents, savedEvents);
    }

    public void addFailed(String runId, int failedEvents) {
        KafkaRunState state = states.get(runId);
        if (state == null) {
            return;
        }
        state.addFailed(failedEvents);
    }

    public KafkaRunStatusResponse get(String runId) {
        return state(runId).toResponse();
    }

    private KafkaRunState state(String runId) {
        KafkaRunState state = states.get(runId);
        if (state == null) {
            throw new NoSuchElementException("Kafka run was not found: " + runId);
        }
        return state;
    }
}
