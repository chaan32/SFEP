package com.sfep.sfep_server.alert.service;

import com.sfep.sfep_server.alert.dto.AlertEventResponse;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Consumer;

@Service
public class AlertEventBus {

    private final List<Consumer<AlertEventResponse>> subscribers = new CopyOnWriteArrayList<>();

    public Runnable subscribe(Consumer<AlertEventResponse> subscriber) {
        subscribers.add(subscriber);
        return () -> subscribers.remove(subscriber);
    }

    public void publish(AlertEventResponse alert) {
        for (Consumer<AlertEventResponse> subscriber : subscribers) {
            try {
                subscriber.accept(alert);
            } catch (RuntimeException ignored) {
                subscribers.remove(subscriber);
            }
        }
    }
}
