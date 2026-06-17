package com.sfep.sfep_server.simulator.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;

public record SimulatorRunRequest(
        @Min(1) @Max(1000) Integer equipmentCount,
        @Min(1) @Max(1000) Integer eventsPerEquipment,
        @Min(0) @Max(100) Integer failureRatePercent
) {
}
