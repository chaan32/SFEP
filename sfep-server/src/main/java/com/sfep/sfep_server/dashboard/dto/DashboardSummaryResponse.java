package com.sfep.sfep_server.dashboard.dto;

import com.sfep.sfep_server.event.dto.SensorEventResponse;

import java.util.List;

public record DashboardSummaryResponse(
        long totalEquipment,
        long runningEquipment,
        long idleEquipment,
        long warningEquipment,
        long failureEquipment,
        long totalEvents,
        long warningEvents,
        long criticalEvents,
        long eventsLastMinute,
        List<SensorEventResponse> recentEvents
) {
}
