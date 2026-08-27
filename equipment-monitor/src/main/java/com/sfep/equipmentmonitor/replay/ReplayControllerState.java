package com.sfep.equipmentmonitor.replay;

import java.util.Objects;

public record ReplayControllerState(
        ReplayStatus status,
        boolean paused,
        ReplaySpeed speed,
        long unitsDelivered,
        String errorMessage) {
    public ReplayControllerState {
        Objects.requireNonNull(status, "status");
        Objects.requireNonNull(speed, "speed");
        if (unitsDelivered < 0) {
            throw new IllegalArgumentException("unitsDelivered must be non-negative");
        }
        if (status == ReplayStatus.ERROR && errorMessage == null) {
            throw new IllegalArgumentException("ERROR requires an error message");
        }
    }
}
