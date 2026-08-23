package com.sfep.sfep_server.steelshadow.client;

import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowErrorResponse;
import org.springframework.http.HttpStatusCode;

public final class SteelShadowClientException extends RuntimeException {
    private final HttpStatusCode status;
    private final SteelShadowErrorResponse error;

    public SteelShadowClientException(
            HttpStatusCode status,
            SteelShadowErrorResponse error,
            Throwable cause
    ) {
        super(error.message(), cause);
        this.status = status;
        this.error = error;
    }

    public HttpStatusCode status() {
        return status;
    }

    public SteelShadowErrorResponse error() {
        return error;
    }
}
