package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.Error;
import com.networknt.schema.Schema;
import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SpecificationVersion;

import java.io.IOException;
import java.io.InputStream;
import java.util.Collections;
import java.util.EnumMap;
import java.util.Map;

public final class EmbeddedSchemas {
    private static final ObjectMapper JSON = new ObjectMapper();
    private final Map<SchemaRole, SchemaDocument> documents;

    private EmbeddedSchemas(Map<SchemaRole, SchemaDocument> documents) {
        this.documents = Collections.unmodifiableMap(new EnumMap<>(documents));
    }

    public static EmbeddedSchemas load() {
        return from(BundleContracts.require("sfep-equipment-bundle/v1"));
    }

    static EmbeddedSchemas from(BundleContract contract) {
        return new EmbeddedSchemas(contract.schemas());
    }

    static SchemaDocument loadDocument(String resourceName, String expectedDigest) {
        SchemaRegistry registry = SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12);
        try (InputStream input = EmbeddedSchemas.class.getClassLoader().getResourceAsStream(resourceName)) {
            if (input == null) {
                throw new BundleLoadException("EMBEDDED_SCHEMA_MISSING", resourceName);
            }
            byte[] bytes = input.readAllBytes();
            String actualDigest = Digests.sha256Uri(bytes);
            if (!expectedDigest.equals(actualDigest)) {
                throw new BundleLoadException(
                        "EMBEDDED_SCHEMA_DIGEST_MISMATCH",
                        resourceName + " expected " + expectedDigest + " but found " + actualDigest);
            }
            JsonNode schemaNode = JSON.readTree(bytes);
            Schema schema = registry.getSchema(schemaNode);
            return new SchemaDocument(bytes, actualDigest, schema);
        } catch (IOException | RuntimeException error) {
            if (error instanceof BundleLoadException loadError) {
                throw loadError;
            }
            throw new BundleLoadException("EMBEDDED_SCHEMA_INVALID", resourceName, error);
        }
    }

    public Map<SchemaRole, SchemaDocument> documents() {
        return documents;
    }

    public SchemaDocument document(SchemaRole role) {
        SchemaDocument document = documents.get(role);
        if (document == null) {
            throw new BundleLoadException("EMBEDDED_SCHEMA_MISSING", role.name());
        }
        return document;
    }

    public void validate(SchemaRole role, JsonNode instance, String errorCode) {
        java.util.List<Error> errors = document(role).schema().validate(instance);
        if (!errors.isEmpty()) {
            throw new BundleLoadException(errorCode, role.identityRole() + " " + errors.getFirst());
        }
    }
}
