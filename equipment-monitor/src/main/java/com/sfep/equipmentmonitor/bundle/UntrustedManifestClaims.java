package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

record UntrustedManifestClaims(
        String bundleId,
        String criteriaId,
        String asOf,
        Map<String, String> identity,
        Map<String, String> criteriaIdentity,
        List<ArtifactDescriptor> artifacts) {
    static UntrustedManifestClaims extract(JsonNode root) {
        return new UntrustedManifestClaims(
                requiredText(root, "bundleId"),
                requiredText(root, "criteriaId"),
                requiredText(root, "asOf"),
                stringMap(root.path("identity"), "identity"),
                stringMap(root.path("criteriaIdentity"), "criteriaIdentity"),
                artifacts(root.path("artifacts")));
    }

    private static List<ArtifactDescriptor> artifacts(JsonNode value) {
        if (!value.isArray() || value.size() != ArtifactRole.values().length) {
            throw invalid("artifacts must contain exactly six claims");
        }
        List<ArtifactDescriptor> result = new ArrayList<>();
        for (int index = 0; index < value.size(); index++) {
            JsonNode artifact = value.path(index);
            if (!artifact.isObject()) {
                throw invalid("artifact " + index + " is not an object");
            }
            JsonNode size = artifact.path("sizeBytes");
            if (!size.isIntegralNumber() || !size.canConvertToLong() || size.longValue() < 0) {
                throw invalid("artifact " + index + " has invalid sizeBytes");
            }
            result.add(new ArtifactDescriptor(
                    requiredText(artifact, "role"),
                    size.longValue(),
                    requiredText(artifact, "sha256"),
                    requiredText(artifact, "schemaVersion")));
        }
        return List.copyOf(result);
    }

    private static Map<String, String> stringMap(JsonNode value, String label) {
        if (!value.isObject()) {
            throw invalid(label + " is not an object");
        }
        LinkedHashMap<String, String> result = new LinkedHashMap<>();
        value.properties().forEach(entry -> {
            if (!entry.getValue().isTextual()) {
                throw invalid(label + "." + entry.getKey() + " is not a string");
            }
            result.put(entry.getKey(), entry.getValue().textValue());
        });
        return Map.copyOf(result);
    }

    private static String requiredText(JsonNode object, String name) {
        JsonNode value = object.path(name);
        if (!value.isTextual() || value.textValue().isEmpty()) {
            throw invalid(name + " is not a non-empty string");
        }
        return value.textValue();
    }

    private static BundleLoadException invalid(String detail) {
        return new BundleLoadException("MANIFEST_CLAIMS_INVALID", detail);
    }
}
