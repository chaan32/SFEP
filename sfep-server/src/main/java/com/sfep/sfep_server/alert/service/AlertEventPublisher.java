package com.sfep.sfep_server.alert.service;

import com.sfep.sfep_server.alert.dto.AlertEventResponse;
import com.sfep.sfep_server.event.domain.EventSeverity;
import com.sfep.sfep_server.event.domain.SensorEvent;
import com.sfep.sfep_server.event.dto.SensorEventRequest;
import com.sfep.sfep_server.event.service.EventRiskAnalyzer;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class AlertEventPublisher {

    private final EventRiskAnalyzer riskAnalyzer;
    private final AlertEventBus alertEventBus;

    public AlertEventPublisher(EventRiskAnalyzer riskAnalyzer, AlertEventBus alertEventBus) {
        this.riskAnalyzer = riskAnalyzer;
        this.alertEventBus = alertEventBus;
    }

    /**
     * publish Alerts
     * @param requests
     * @return
     */
    public int publishAlerts(List<SensorEventRequest> requests) {
        int published = 0;
        for (SensorEventRequest request : requests) {
            if (publishIfNeeded(request)) {
                published++;
            }
        }
        return published;
    }

    public boolean publishIfNeeded(SensorEventRequest request) {
        // 1) Analyze the status equipment by Messages that contains information like power, rpm ..
        EventSeverity severity = riskAnalyzer.analyze(request);
        // 2) Pass if Status is Normal
        if (severity == EventSeverity.NORMAL) {
            return false;
        }
        // 3) Alert Message will be published with necessary information, If Status is Warning or Critical
        alertEventBus.publish(AlertEventResponse.from(request, severity));
        return true;
    }

    public boolean publishIfNeeded(SensorEvent event) {
        if (event.getSeverity() == EventSeverity.NORMAL) {
            return false;
        }
        alertEventBus.publish(AlertEventResponse.from(event));
        return true;
    }
}
