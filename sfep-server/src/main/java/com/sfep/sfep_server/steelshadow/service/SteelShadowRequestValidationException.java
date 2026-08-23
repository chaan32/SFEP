package com.sfep.sfep_server.steelshadow.service;

import org.springframework.http.HttpStatus;

public final class SteelShadowRequestValidationException extends RuntimeException {
    private final HttpStatus status;
    private final String requestId;

    public SteelShadowRequestValidationException(String requestId, Throwable cause) {
        this(HttpStatus.BAD_REQUEST, requestId, cause);
    }

    public SteelShadowRequestValidationException(
            HttpStatus status,
            String requestId,
            Throwable cause
    ) {
        super("request body failed Steel Shadow validation", cause);
        this.status = status;
        this.requestId = requestId;
    }

    public HttpStatus status() {
        return status;
    }

    public String requestId() {
        return requestId;
    }
}
