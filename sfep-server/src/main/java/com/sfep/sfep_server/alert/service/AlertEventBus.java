package com.sfep.sfep_server.alert.service;

import com.sfep.sfep_server.alert.dto.AlertEventResponse;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executor;
import java.util.function.Consumer;

@Service
public class AlertEventBus {

    private final List<Consumer<AlertEventResponse>> subscribers = new CopyOnWriteArrayList<>();
    private final Executor alertEventExecutor;
    private final AlertMetricsService alertMetricsService;

    public AlertEventBus(
            @Qualifier("alertEventExecutor") Executor alertEventExecutor,
            AlertMetricsService alertMetricsService
    ) {
        this.alertEventExecutor = alertEventExecutor;
        this.alertMetricsService = alertMetricsService;
    }

    public Runnable subscribe(Consumer<AlertEventResponse> subscriber) {
        subscribers.add(subscriber);
        return () -> subscribers.remove(subscriber);
    }

    public void publish(AlertEventResponse alert) {
        alertMetricsService.record(alert);
        if (subscribers.isEmpty()) {
            return;
        }
        alertEventExecutor.execute(() -> deliver(alert));
    }

    private void deliver(AlertEventResponse alert) {
        for (Consumer<AlertEventResponse> subscriber : subscribers) {
            try {
                subscriber.accept(alert);
            } catch (RuntimeException ignored) {
                subscribers.remove(subscriber);
            }
        }
    }
}
