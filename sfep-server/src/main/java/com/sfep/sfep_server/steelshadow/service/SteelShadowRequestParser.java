package com.sfep.sfep_server.steelshadow.service;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowFeatureBatchRequest;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowLabelBatchRequest;
import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validator;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import java.util.Objects;
import java.util.Set;

@Component
@ConditionalOnProperty(prefix = "sfep.steel-shadow", name = "enabled", havingValue = "true")
public final class SteelShadowRequestParser {
    static final int MAX_REQUEST_BYTES = 10 * 1024 * 1024;

    private final ObjectMapper objectMapper;
    private final Validator validator;

    public SteelShadowRequestParser(ObjectMapper objectMapper, Validator validator) {
        this.objectMapper = Objects.requireNonNull(objectMapper).copy()
                .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                .enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION);
        this.validator = Objects.requireNonNull(validator);
    }

    public SteelShadowFeatureBatchRequest parseFeatures(byte[] body) {
        return parse(body, SteelShadowFeatureBatchRequest.class);
    }

    public SteelShadowLabelBatchRequest parseLabels(byte[] body) {
        return parse(body, SteelShadowLabelBatchRequest.class);
    }

    private <T> T parse(byte[] body, Class<T> type) {
        if (body == null || body.length == 0) {
            throw new SteelShadowRequestValidationException(null, null);
        }
        if (body.length > MAX_REQUEST_BYTES) {
            throw new SteelShadowRequestValidationException(
                    HttpStatus.PAYLOAD_TOO_LARGE, null, null
            );
        }
        JsonNode root = null;
        try {
            root = objectMapper.readTree(body);
            if (root == null || !root.isObject()) {
                throw new IllegalArgumentException("request JSON root must be an object");
            }
            T request = objectMapper.treeToValue(root, type);
            Set<ConstraintViolation<T>> violations = validator.validate(request);
            if (!violations.isEmpty()) {
                throw new IllegalArgumentException("request violates the Steel Shadow schema");
            }
            return request;
        } catch (SteelShadowRequestValidationException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new SteelShadowRequestValidationException(
                    safeRequestId(root), exception
            );
        }
    }

    private static String safeRequestId(JsonNode root) {
        if (root == null || !root.isObject()) {
            return null;
        }
        JsonNode value = root.get("requestId");
        if (value == null || !value.isTextual()) {
            return null;
        }
        String requestId = value.textValue();
        if (requestId.length() < 1 || requestId.length() > 128) {
            return null;
        }
        for (int index = 0; index < requestId.length(); index++) {
            char character = requestId.charAt(index);
            if (character < 32 || character > 126) {
                return null;
            }
        }
        return requestId;
    }
}
