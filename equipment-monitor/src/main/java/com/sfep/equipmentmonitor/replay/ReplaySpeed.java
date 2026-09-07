package com.sfep.equipmentmonitor.replay;

public enum ReplaySpeed {
    X1(1_000),
    X10(100),
    X100(10),
    MAX(0);

    private final long delayMillis;

    ReplaySpeed(long delayMillis) {
        this.delayMillis = delayMillis;
    }

    public long delayMillis() {
        return delayMillis;
    }
}
