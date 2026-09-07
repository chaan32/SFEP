package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

public final class DigestIds {
    private static final ObjectMapper JSON = new ObjectMapper();

    private DigestIds() {
    }

    public static IdValue compute(String namespace, JsonNode object) {
        if (namespace == null || namespace.isBlank() || namespace.indexOf('\r') >= 0 || namespace.indexOf('\n') >= 0) {
            throw new IllegalArgumentException("invalid digest namespace");
        }
        if (object == null || !object.isObject()) {
            throw new IllegalArgumentException("digest preimage must be an object");
        }
        try {
            String preimage = namespace + "\n" + JSON.writeValueAsString(canonicalize(object)) + "\n";
            return new IdValue(Digests.sha256Uri(preimage.getBytes(StandardCharsets.UTF_8)), preimage);
        } catch (JsonProcessingException error) {
            throw new IllegalArgumentException("cannot serialize digest preimage", error);
        }
    }

    /** SHA-256 of canonical object JSON without an ID namespace wrapper. */
    public static String canonicalSha256Uri(JsonNode object) {
        return Digests.sha256Uri(canonicalJsonBytes(object));
    }

    public static byte[] canonicalJsonBytes(JsonNode object) {
        return CanonicalJson.encodeObject(object);
    }

    private static JsonNode canonicalize(JsonNode node) {
        if (node.isObject()) {
            ObjectNode result = JsonNodeFactory.instance.objectNode();
            List<String> names = new ArrayList<>();
            node.fieldNames().forEachRemaining(names::add);
            names.sort(Utf8Order.COMPARATOR);
            for (String name : names) {
                result.set(name, canonicalize(node.get(name)));
            }
            return result;
        }
        if (node.isArray()) {
            ArrayNode result = JsonNodeFactory.instance.arrayNode();
            node.forEach(value -> result.add(canonicalize(value)));
            return result;
        }
        return node.deepCopy();
    }
}
