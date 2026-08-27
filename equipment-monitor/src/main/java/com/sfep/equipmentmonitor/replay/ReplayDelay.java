package com.sfep.equipmentmonitor.replay;

@FunctionalInterface
public interface ReplayDelay {
    void await(ReplaySpeed speed) throws InterruptedException;

    static ReplayDelay realTime() {
        return speed -> {
            if (speed.delayMillis() > 0) {
                Thread.sleep(speed.delayMillis());
            }
        };
    }
}
