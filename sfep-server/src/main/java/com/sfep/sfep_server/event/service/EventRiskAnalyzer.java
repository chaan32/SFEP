package com.sfep.sfep_server.event.service;

import com.sfep.sfep_server.equipment.domain.EquipmentStatus;
import com.sfep.sfep_server.event.domain.EventSeverity;
import com.sfep.sfep_server.event.dto.SensorEventRequest;
import org.springframework.stereotype.Component;

@Component
public class EventRiskAnalyzer {

    public EventSeverity analyze(SensorEventRequest request) {
        if (request.status() == EquipmentStatus.FAILURE
                || request.temperature() >= 90.0
                || request.vibration() >= 8.0
                || request.currentValue() >= 80.0) {
            return EventSeverity.CRITICAL;
        }
        if (request.status() == EquipmentStatus.WARNING
                || request.temperature() >= 75.0
                || request.vibration() >= 5.0
                || request.currentValue() >= 60.0) {
            return EventSeverity.WARNING;
        }
        return EventSeverity.NORMAL;
    }
}
/**
 * CRITICAL:
 * - 상태가 FAILURE
 * - 온도 >= 90
 * - 진동 >= 8
 * - 전류 >= 80
 *
 * WARNING:
 * - 상태가 WARNING
 * - 온도 >= 75
 * - 진동 >= 5
 * - 전류 >= 60
 *
 * 그 외:
 * - NORMAL
 */
