package com.sfep.sfep_server.steelshadow.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowFeatureBatchRequest;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowLabelBatchRequest;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowErrorResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowHealthResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowLabelResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowPredictionResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowScoreResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowStatusResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.ShadowOnlyResponse;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.io.IOException;
import java.util.Objects;

public final class SteelShadowClient {
    private static final String ERROR_SCHEMA_VERSION = "steel-shadow-error-v1";
    private static final String SCORE_SCHEMA_VERSION = "steel-shadow-score-response-v1";
    private static final String LABEL_SCHEMA_VERSION = "steel-shadow-label-response-v1";
    private static final String STATUS_SCHEMA_VERSION = "steel-shadow-status-response-v1";
    private static final String PREDICTION_SCHEMA_VERSION = "steel-shadow-prediction-response-v1";
    private static final String HEALTH_SCHEMA_VERSION = "steel-shadow-health-response-v1";

    private final RestClient restClient;
    private final ObjectMapper objectMapper;

    public SteelShadowClient(RestClient restClient, ObjectMapper objectMapper) {
        this.restClient = Objects.requireNonNull(restClient);
        this.objectMapper = Objects.requireNonNull(objectMapper);
    }

    public SteelShadowScoreResponse score(SteelShadowFeatureBatchRequest request) {
        SteelShadowScoreResponse response = exchange(
                restClient.post()
                        .uri("/v1/features")
                        .contentType(MediaType.APPLICATION_JSON)
                        .body(jsonBody(request, request.requestId())),
                SteelShadowScoreResponse.class,
                request.requestId(),
                SCORE_SCHEMA_VERSION
        );
        validateRequestId(request.requestId(), response.requestId());
        return response;
    }

    public SteelShadowLabelResponse ingestLabels(SteelShadowLabelBatchRequest request) {
        SteelShadowLabelResponse response = exchange(
                restClient.post()
                        .uri("/v1/labels")
                        .contentType(MediaType.APPLICATION_JSON)
                        .body(jsonBody(request, request.requestId())),
                SteelShadowLabelResponse.class,
                request.requestId(),
                LABEL_SCHEMA_VERSION
        );
        validateRequestId(request.requestId(), response.requestId());
        return response;
    }

    public SteelShadowStatusResponse status() {
        return exchange(
                restClient.get().uri("/v1/status"),
                SteelShadowStatusResponse.class,
                null,
                STATUS_SCHEMA_VERSION
        );
    }

    public SteelShadowPredictionResponse predictions(String hrCoilId) {
        return exchange(
                restClient.get().uri("/v1/predictions/{hrCoilId}", hrCoilId),
                SteelShadowPredictionResponse.class,
                null,
                PREDICTION_SCHEMA_VERSION
        );
    }

    public SteelShadowHealthResponse health() {
        return exchange(
                restClient.get().uri("/health"),
                SteelShadowHealthResponse.class,
                null,
                HEALTH_SCHEMA_VERSION
        );
    }

    private <T> T exchange(
            RestClient.RequestHeadersSpec<?> request,
            Class<T> responseType,
            String requestId,
            String expectedSchemaVersion
    ) {
        try {
            T response = request.retrieve()
                    .onStatus(status -> status.isError(), (httpRequest, httpResponse) -> {
                        SteelShadowErrorResponse error;
                        try {
                            error = objectMapper.readValue(
                                    httpResponse.getBody(), SteelShadowErrorResponse.class
                            );
                        } catch (IOException parseFailure) {
                            throw internalFailure(
                                    requestId,
                                    "Shadow sidecar returned an invalid error response",
                                    parseFailure
                            );
                        }
                        if (!isValidError(error, requestId)) {
                            throw internalFailure(
                                    requestId,
                                    "Shadow sidecar returned an invalid error response",
                                    null
                            );
                        }
                        throw new SteelShadowClientException(
                                httpResponse.getStatusCode(), error, null
                        );
                    })
                    .body(responseType);
            if (response == null) {
                throw new RestClientException("Shadow sidecar returned an empty response");
            }
            if (!(response instanceof ShadowOnlyResponse shadowOnly)
                    || !expectedSchemaVersion.equals(shadowOnly.schemaVersion())
                    || shadowOnly.deploymentEligible()) {
                throw internalFailure(
                        requestId,
                        "Shadow sidecar violated the response safety contract",
                        null
                );
            }
            return response;
        } catch (SteelShadowClientException exception) {
            throw exception;
        } catch (ResourceAccessException exception) {
            throw unavailable(requestId, exception);
        } catch (RestClientException exception) {
            throw internalFailure(
                    requestId,
                    "Shadow sidecar returned an invalid success response",
                    exception
            );
        }
    }

    private static void validateRequestId(String expected, String actual) {
        if (!Objects.equals(expected, actual)) {
            throw internalFailure(
                    expected,
                    "Shadow sidecar returned a mismatched request identifier",
                    null
            );
        }
    }

    private static boolean isValidError(SteelShadowErrorResponse error, String requestId) {
        return error != null
                && ERROR_SCHEMA_VERSION.equals(error.schemaVersion())
                && Objects.equals(requestId, error.requestId())
                && error.code() != null
                && !error.code().isBlank()
                && error.message() != null
                && !error.message().isBlank();
    }

    private byte[] jsonBody(Object value, String requestId) {
        try {
            return objectMapper.writeValueAsBytes(value);
        } catch (JsonProcessingException exception) {
            throw new SteelShadowClientException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    safeError(
                            requestId,
                            "SHADOW_INTERNAL_ERROR",
                            "Steel Shadow request serialization failed",
                            false
                    ),
                    exception
            );
        }
    }

    private static SteelShadowClientException unavailable(String requestId, Throwable cause) {
        return new SteelShadowClientException(
                HttpStatus.SERVICE_UNAVAILABLE,
                safeError(
                        requestId,
                        "SHADOW_UNAVAILABLE",
                        "Shadow sidecar is unavailable",
                        true
                ),
                cause
        );
    }

    private static SteelShadowClientException internalFailure(
            String requestId,
            String message,
            Throwable cause
    ) {
        return new SteelShadowClientException(
                HttpStatus.INTERNAL_SERVER_ERROR,
                safeError(
                        requestId,
                        "SHADOW_INTERNAL_ERROR",
                        message,
                        false
                ),
                cause
        );
    }

    private static SteelShadowErrorResponse safeError(
            String requestId,
            String code,
            String message,
            boolean retryable
    ) {
        return new SteelShadowErrorResponse(
                ERROR_SCHEMA_VERSION,
                requestId,
                code,
                message,
                retryable
        );
    }
}
