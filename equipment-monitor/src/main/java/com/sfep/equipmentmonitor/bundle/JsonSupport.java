package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.json.JsonMapper;

import java.io.IOException;

public final class JsonSupport {
    static final ObjectMapper MAPPER = JsonMapper.builder()
            .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
            .enable(DeserializationFeature.FAIL_ON_READING_DUP_TREE_KEY)
            .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .enable(DeserializationFeature.FAIL_ON_NULL_FOR_PRIMITIVES)
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
            .build();

    private JsonSupport() {
    }

    public static JsonNode parse(byte[] bytes, String code, String label) {
        try {
            JsonNode value = MAPPER.readTree(bytes);
            if (value == null || !value.isObject()) {
                throw new BundleLoadException(code, label + " must be one JSON object");
            }
            return value;
        } catch (BundleLoadException error) {
            throw error;
        } catch (IOException error) {
            throw new BundleLoadException(code, label, error);
        }
    }

    public static <T> T bind(JsonNode value, Class<T> type, String code) {
        try {
            return MAPPER.treeToValue(value, type);
        } catch (IOException error) {
            throw new BundleLoadException(code, type.getSimpleName(), error);
        }
    }
}
