package com.sfep.sfep_server.alert.service;

import com.sfep.sfep_server.alert.dto.AlertEventResponse;
import com.sfep.sfep_server.alert.dto.AlertMetricsResponse;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.atomic.LongAdder;

@Service
public class AlertMetricsService {

    private static final int MAX_SAMPLE_SIZE = 20_000;

    private final LongAdder totalAlerts = new LongAdder();
    private final LongAdder totalLatencyMs = new LongAdder();
    private final List<Long> recentLatenciesMs = new ArrayList<>();
    private final Object lock = new Object();

    public void record(AlertEventResponse alert) {
        long latencyMs = Math.max(0, alert.alertLatencyMs());
        totalAlerts.increment();
        totalLatencyMs.add(latencyMs);
        synchronized (lock) {
            if (recentLatenciesMs.size() >= MAX_SAMPLE_SIZE) {
                recentLatenciesMs.remove(0);
            }
            recentLatenciesMs.add(latencyMs);
        }
    }

    public AlertMetricsResponse snapshot() {
        List<Long> snapshot;
        synchronized (lock) {
            snapshot = new ArrayList<>(recentLatenciesMs);
        }

        long total = totalAlerts.sum();
        long average = total == 0 ? 0 : Math.round((double) totalLatencyMs.sum() / total);
        return new AlertMetricsResponse(
                total,
                average,
                percentile(snapshot, 95),
                percentile(snapshot, 99),
                snapshot.size()
        );
    }

    public AlertMetricsResponse reset() {
        totalAlerts.reset();
        totalLatencyMs.reset();
        synchronized (lock) {
            recentLatenciesMs.clear();
        }
        return snapshot();
    }

    private long percentile(List<Long> values, int percentile) {
        if (values.isEmpty()) {
            return 0;
        }
        Collections.sort(values);
        int index = (int) Math.ceil((percentile / 100.0) * values.size()) - 1;
        return values.get(Math.max(0, Math.min(index, values.size() - 1)));
    }
}
