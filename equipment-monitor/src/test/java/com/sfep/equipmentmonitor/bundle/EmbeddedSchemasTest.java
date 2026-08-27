package com.sfep.equipmentmonitor.bundle;

import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class EmbeddedSchemasTest {
    @Test
    void embedsExactlyTheSevenNormativeRootSchemaByteSequences() throws Exception {
        EmbeddedSchemas schemas = EmbeddedSchemas.load();
        Path root = Path.of(System.getProperty("sfep.repo-root"))
                .resolve("contracts/equipment-monitor/v1");

        assertThat(schemas.documents()).hasSize(7);
        assertThat(schemas.documents().keySet()).containsExactlyInAnyOrder(SchemaRole.values());
        for (SchemaRole role : SchemaRole.values()) {
            byte[] normative = Files.readAllBytes(root.resolve(role.fileName()));
            SchemaDocument embedded = schemas.document(role);
            assertThat(embedded.bytes()).as(role.fileName()).containsExactly(normative);
            assertThat(embedded.sha256()).isEqualTo(Digests.sha256Uri(normative));
        }
    }
}
