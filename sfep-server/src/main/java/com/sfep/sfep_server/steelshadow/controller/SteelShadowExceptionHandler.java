package com.sfep.sfep_server.steelshadow.controller;

import com.sfep.sfep_server.steelshadow.client.SteelShadowClientException;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowErrorResponse;
import com.sfep.sfep_server.steelshadow.service.SteelShadowRequestValidationException;
import jakarta.validation.ConstraintViolationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.method.annotation.HandlerMethodValidationException;

@RestControllerAdvice(assignableTypes = SteelShadowController.class)
@ConditionalOnProperty(prefix = "sfep.steel-shadow", name = "enabled", havingValue = "true")
public class SteelShadowExceptionHandler {
    private static final Logger log = LoggerFactory.getLogger(SteelShadowExceptionHandler.class);

    @ExceptionHandler(SteelShadowClientException.class)
    public ResponseEntity<SteelShadowErrorResponse> handleClient(
            SteelShadowClientException exception
    ) {
        return ResponseEntity.status(exception.status()).body(exception.error());
    }

    @ExceptionHandler(SteelShadowRequestValidationException.class)
    public ResponseEntity<SteelShadowErrorResponse> handleParsedRequest(
            SteelShadowRequestValidationException exception
    ) {
        log.info("steel_shadow request rejected requestId={}", exception.requestId());
        return ResponseEntity.status(exception.status()).body(new SteelShadowErrorResponse(
                "steel-shadow-error-v1",
                exception.requestId(),
                "REQUEST_SCHEMA_INVALID",
                "request body failed Steel Shadow validation",
                false
        ));
    }

    @ExceptionHandler({
            HttpMessageNotReadableException.class,
            ConstraintViolationException.class,
            HandlerMethodValidationException.class
    })
    public ResponseEntity<SteelShadowErrorResponse> handleInvalidRequest(Exception exception) {
        log.info("steel_shadow request rejected type={}", exception.getClass().getSimpleName());
        return ResponseEntity.badRequest().body(error(
                "REQUEST_SCHEMA_INVALID",
                "request body failed Steel Shadow validation",
                false
        ));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<SteelShadowErrorResponse> handleUnexpected(Exception exception) {
        log.error("steel_shadow unexpected adapter failure", exception);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(error(
                "SHADOW_INTERNAL_ERROR",
                "unexpected Steel Shadow adapter failure",
                false
        ));
    }

    private static SteelShadowErrorResponse error(
            String code,
            String message,
            boolean retryable
    ) {
        return new SteelShadowErrorResponse(
                "steel-shadow-error-v1",
                null,
                code,
                message,
                retryable
        );
    }
}
