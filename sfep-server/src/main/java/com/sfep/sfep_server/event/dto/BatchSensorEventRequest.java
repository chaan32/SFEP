package com.sfep.sfep_server.event.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotEmpty;

import java.util.List;

public record BatchSensorEventRequest(
        @NotEmpty @Valid List<SensorEventRequest> events
) {
}
