package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

record UntrustedManifestClaims(
        String schemaVersion,
        String bundleId,
        String criteriaId,
        String asOf,
        Map<String, String> identity,
        Map<String, String> criteriaIdentity,
        List<ArtifactDescriptor> artifacts) {
    private static final Set<String> IDENTITY_KEYS = Set.of(
            "analysis_config_sha256",
            "criteria_id",
            "producer_runtime_sha256",
            "schema.analysis_config.sha256",
            "schema.analysis_summary.sha256",
            "schema.bundle_manifest.sha256",
            "schema.equipment_operating_ranges.sha256",
            "schema.producer_runtime.sha256",
            "schema.quality_risk_intervals.sha256",
            "schema.replay_events.sha256",
            "source.ap.name",
            "source.ap.sha256",
            "source.ap.size_bytes",
            "source.fur_hr.name",
            "source.fur_hr.sha256",
            "source.fur_hr.size_bytes",
            "source.sm_cc.name",
            "source.sm_cc.sha256",
            "source.sm_cc.size_bytes");
    private static final Set<String> CRITERIA_IDENTITY_KEYS = Set.of(
            "analysis_config_sha256",
            "as_of",
            "criteria_projection_sha256",
            "producer_runtime_sha256",
            "schema.analysis_config.sha256",
            "schema.equipment_operating_ranges.sha256",
            "schema.producer_runtime.sha256",
            "schema.quality_risk_intervals.sha256");
    private static final Set<String> ARTIFACT_KEYS = Set.of(
            "role", "sizeBytes", "sha256", "schemaVersion");

    static UntrustedManifestClaims extract(JsonNode root) {
        if (!root.isObject()) {
            throw invalid("manifest must be an object");
        }
        return new UntrustedManifestClaims(
                requiredText(root, "schemaVersion"),
                requiredText(root, "bundleId"),
                requiredText(root, "criteriaId"),
                requiredText(root, "asOf"),
                stringMap(root.path("identity"), "identity", IDENTITY_KEYS),
                stringMap(
                        root.path("criteriaIdentity"),
                        "criteriaIdentity",
                        CRITERIA_IDENTITY_KEYS),
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
            requireExactKeys(artifact, "artifact " + index, ARTIFACT_KEYS);
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

    private static Map<String, String> stringMap(
            JsonNode value,
            String label,
            Set<String> expectedKeys) {
        if (!value.isObject()) {
            throw invalid(label + " is not an object");
        }
        requireExactKeys(value, label, expectedKeys);
        LinkedHashMap<String, String> result = new LinkedHashMap<>();
        value.properties().forEach(entry -> {
            if (!entry.getValue().isTextual()) {
                throw invalid(label + "." + entry.getKey() + " is not a string");
            }
            result.put(entry.getKey(), entry.getValue().textValue());
        });
        return Map.copyOf(result);
    }

    private static void requireExactKeys(JsonNode object, String label, Set<String> expectedKeys) {
        Set<String> actualKeys = new java.util.HashSet<>();
        object.fieldNames().forEachRemaining(actualKeys::add);
        if (!actualKeys.equals(expectedKeys)) {
            throw invalid(label + " has unexpected or missing claims");
        }
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
