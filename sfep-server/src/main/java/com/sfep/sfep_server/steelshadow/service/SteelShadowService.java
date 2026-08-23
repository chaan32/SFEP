package com.sfep.sfep_server.steelshadow.service;

import com.sfep.sfep_server.steelshadow.client.SteelShadowClient;
import com.sfep.sfep_server.steelshadow.client.SteelShadowClientException;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowFeatureBatchRequest;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowLabelBatchRequest;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowLabelResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowPredictionResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowScoreResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowStatusResponse;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Service;

import java.util.Objects;
import java.util.function.Supplier;

@Service
@ConditionalOnProperty(prefix = "sfep.steel-shadow", name = "enabled", havingValue = "true")
public class SteelShadowService {
    private static final Logger log = LoggerFactory.getLogger(SteelShadowService.class);

    private final SteelShadowClient client;
    private final MeterRegistry meterRegistry;

    public SteelShadowService(SteelShadowClient client, MeterRegistry meterRegistry) {
        this.client = Objects.requireNonNull(client);
        this.meterRegistry = Objects.requireNonNull(meterRegistry);
    }

    public SteelShadowScoreResponse score(SteelShadowFeatureBatchRequest request) {
        SteelShadowScoreResponse response = execute(
                "features", request.requestId(), () -> client.score(request)
        );
        log.info(
                "steel_shadow operation=features requestId={} batchId={} coilCount={} outcome=SUCCESS",
                request.requestId(), response.batchId(), response.coilCount()
        );
        return response;
    }

    public SteelShadowLabelResponse ingestLabels(SteelShadowLabelBatchRequest request) {
        SteelShadowLabelResponse response = execute(
                "labels", request.requestId(), () -> client.ingestLabels(request)
        );
        log.info(
                "steel_shadow operation=labels requestId={} batchId={} coilCount={} outcome=SUCCESS",
                request.requestId(), response.batchId(), response.labelCount()
        );
        return response;
    }

    public SteelShadowStatusResponse status() {
        return execute("status", null, client::status);
    }

    public SteelShadowPredictionResponse predictions(String hrCoilId) {
        return execute("predictions", null, () -> client.predictions(hrCoilId));
    }

    private <T> T execute(String operation, String requestId, Supplier<T> action) {
        Timer.Sample sample = Timer.start(meterRegistry);
        try {
            T response = action.get();
            requestCounter(operation, "SUCCESS").increment();
            return response;
        } catch (SteelShadowClientException exception) {
            String outcome = exception.error().code();
            requestCounter(operation, outcome).increment();
            if ("SHADOW_UNAVAILABLE".equals(outcome)) {
                Counter.builder("sfep_steel_shadow_unavailable_total")
                        .register(meterRegistry)
                        .increment();
            }
            log.warn(
                    "steel_shadow operation={} requestId={} outcome={} status={}",
                    operation, requestId, outcome, exception.status().value()
            );
            throw exception;
        } finally {
            sample.stop(Timer.builder("sfep_steel_shadow_request_duration_seconds")
                    .tag("operation", operation)
                    .register(meterRegistry));
        }
    }

    private Counter requestCounter(String operation, String outcome) {
        return Counter.builder("sfep_steel_shadow_requests_total")
                .tag("operation", operation)
                .tag("outcome", outcome)
                .register(meterRegistry);
    }
}
