package com.sfep.equipmentmonitor.replay;

@FunctionalInterface
public interface ReplayUnitConsumer {
    void accept(ReplayUnit unit) throws Exception;
}
