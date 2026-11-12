package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;
import com.networknt.schema.Error;

import java.util.Collections;
import java.util.EnumMap;
import java.util.EnumSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

public record BundleContract(
        String manifestVersion,
        String criteriaIdNamespace,
        String bundleIdNamespace,
        Map<ArtifactRole, String> artifactVersions,
        Map<SchemaRole, SchemaDocument> schemas) {
    public BundleContract {
        requireText(manifestVersion, "manifestVersion");
        requireText(criteriaIdNamespace, "criteriaIdNamespace");
        requireText(bundleIdNamespace, "bundleIdNamespace");
        if (!"sfep-criteria-id/v1".equals(criteriaIdNamespace)) {
            throw new IllegalArgumentException("criteriaIdNamespace must remain sfep-criteria-id/v1");
        }
        if (!"sfep-bundle-id/v1".equals(bundleIdNamespace)) {
            throw new IllegalArgumentException("bundleIdNamespace must remain sfep-bundle-id/v1");
        }
        boolean hasModel = BundleContracts.V3.equals(manifestVersion);
        artifactVersions = immutableCompleteMap(
                ArtifactRole.class, artifactVersions,
                EnumSet.copyOf(hasModel ? ArtifactRole.ALL_ROLES : ArtifactRole.LEGACY_ROLES));
        EnumSet<SchemaRole> schemaRoles = EnumSet.allOf(SchemaRole.class);
        if (!hasModel) {
            schemaRoles.remove(SchemaRole.QUALITY_MODEL_METADATA);
        }
        schemas = immutableCompleteMap(SchemaRole.class, schemas, schemaRoles);
        artifactVersions.values().forEach(version -> requireText(version, "artifactVersion"));
        schemas.values().forEach(schema -> Objects.requireNonNull(schema, "schema"));
    }

    public List<ArtifactRole> artifactRoles() {
        return List.copyOf(artifactVersions.keySet());
    }

    public String artifactVersion(ArtifactRole role) {
        String version = artifactVersions.get(Objects.requireNonNull(role, "role"));
        if (version == null) {
            throw new BundleLoadException("BUNDLE_CONTRACT_INVALID", role.name());
        }
        return version;
    }

    public SchemaDocument schema(SchemaRole role) {
        SchemaDocument document = schemas.get(Objects.requireNonNull(role, "role"));
        if (document == null) {
            throw new BundleLoadException("BUNDLE_CONTRACT_INVALID", role.name());
        }
        return document;
    }

    public void validate(SchemaRole role, JsonNode instance, String errorCode) {
        List<Error> errors = schema(role).schema().validate(instance);
        if (!errors.isEmpty()) {
            throw new BundleLoadException(errorCode, role.identityRole() + " " + errors.getFirst());
        }
    }

    private static void requireText(String value, String label) {
        if (value == null || value.isEmpty()) {
            throw new IllegalArgumentException(label + " must be non-empty");
        }
    }

    private static <E extends Enum<E>, V> Map<E, V> immutableCompleteMap(
            Class<E> keyType,
            Map<E, V> source,
            Set<E> expected) {
        Objects.requireNonNull(source);
        EnumMap<E, V> copy = new EnumMap<>(keyType);
        copy.putAll(source);
        if (!copy.keySet().equals(expected)) {
            throw new IllegalArgumentException(keyType.getSimpleName() + " inventory must match the profile");
        }
        return Collections.unmodifiableMap(copy);
    }
}
