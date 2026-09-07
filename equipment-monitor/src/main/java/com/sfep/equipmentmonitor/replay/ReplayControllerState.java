package com.sfep.equipmentmonitor.replay;

import java.util.List;
import java.util.Objects;

public record ReplayControllerState(
        ReplayStatus status,
        boolean paused,
        ReplaySpeed speed,
        long unitsDelivered,
        String currentReplayDate,
        ReplayFailureContext failureContext,
        List<ReplayStatus> statusHistory,
        String errorMessage) {
    public ReplayControllerState {
        Objects.requireNonNull(status, "status");
        Objects.requireNonNull(speed, "speed");
        statusHistory = List.copyOf(Objects.requireNonNull(statusHistory, "statusHistory"));
        if (statusHistory.isEmpty() || statusHistory.getLast() != status) {
            throw new IllegalArgumentException("statusHistory must end with the current status");
        }
        if (unitsDelivered < 0) {
            throw new IllegalArgumentException("unitsDelivered must be non-negative");
        }
        if (status == ReplayStatus.ERROR && errorMessage == null) {
            throw new IllegalArgumentException("ERROR requires an error message");
        }
    }

    public ReplayControllerState(
            ReplayStatus status,
            boolean paused,
            ReplaySpeed speed,
            long unitsDelivered,
            String currentReplayDate,
            String errorMessage) {
        this(status, paused, speed, unitsDelivered, currentReplayDate,
                null, List.of(status), errorMessage);
    }
}
