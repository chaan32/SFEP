package com.sfep.equipmentmonitor.bundle;

public final class BundleLoadException extends RuntimeException {
    private final String code;

    public BundleLoadException(String code, String detail) {
        super(code + ": " + detail);
        this.code = code;
    }

    public BundleLoadException(String code, String detail, Throwable cause) {
        super(code + ": " + detail, cause);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
