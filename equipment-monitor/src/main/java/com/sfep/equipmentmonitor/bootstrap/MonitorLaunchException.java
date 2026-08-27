package com.sfep.equipmentmonitor.bootstrap;

public final class MonitorLaunchException extends IllegalArgumentException {
    public MonitorLaunchException(String code, String detail) {
        super(code + ": " + detail);
    }
}
