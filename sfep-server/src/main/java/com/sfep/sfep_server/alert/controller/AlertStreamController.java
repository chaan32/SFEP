package com.sfep.sfep_server.alert.controller;

import com.sfep.sfep_server.alert.dto.AlertEventResponse;
import com.sfep.sfep_server.alert.dto.AlertMetricsResponse;
import com.sfep.sfep_server.alert.service.AlertEventBus;
import com.sfep.sfep_server.alert.service.AlertMetricsService;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

@RestController
@RequestMapping("/api/v1/alerts")
public class AlertStreamController {

    private final AlertEventBus alertEventBus;
    private final AlertMetricsService alertMetricsService;

    public AlertStreamController(AlertEventBus alertEventBus, AlertMetricsService alertMetricsService) {
        this.alertEventBus = alertEventBus;
        this.alertMetricsService = alertMetricsService;
    }

    @GetMapping("/metrics")
    public AlertMetricsResponse metrics() {
        return alertMetricsService.snapshot();
    }

    @PostMapping("/metrics/reset")
    public AlertMetricsResponse resetMetrics() {
        return alertMetricsService.reset();
    }

    @GetMapping(value = "/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter stream() {
        SseEmitter emitter = new SseEmitter(0L);
        AtomicReference<Runnable> unsubscribeRef = new AtomicReference<>();

        Consumer<AlertEventResponse> subscriber = alert -> {
            try {
                emitter.send(SseEmitter.event()
                        .name("alert")
                        .data(alert));
            } catch (IOException | IllegalStateException exception) {
                Runnable unsubscribe = unsubscribeRef.get();
                if (unsubscribe != null) {
                    unsubscribe.run();
                }
                emitter.completeWithError(exception);
            }
        };

        Runnable unsubscribe = alertEventBus.subscribe(subscriber);
        unsubscribeRef.set(unsubscribe);
        emitter.onCompletion(unsubscribe);
        emitter.onTimeout(unsubscribe);
        emitter.onError(error -> unsubscribe.run());

        try {
            emitter.send(SseEmitter.event()
                    .name("connected")
                    .data("SFEP alert stream connected"));
        } catch (IOException exception) {
            unsubscribe.run();
            emitter.completeWithError(exception);
        }

        return emitter;
    }
}
