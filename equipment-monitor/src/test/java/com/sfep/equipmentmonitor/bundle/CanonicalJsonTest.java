package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class CanonicalJsonTest {
    private static final ObjectMapper JSON = new ObjectMapper();

    @Test
    void formatsEveryNormativeBinary64VectorExactly() throws Exception {
        Path vectors = Path.of(System.getProperty("sfep.repo-root"))
                .resolve("contracts/equipment-monitor/v1/canonical-number-test-vectors.json");
        JsonNode values = JSON.readTree(Files.readAllBytes(vectors));

        for (JsonNode vector : values) {
            double number = Double.valueOf(vector.path("hex").textValue());
            var object = JsonNodeFactory.instance.objectNode();
            object.put("value", number);
            String encoded = new String(DigestIds.canonicalJsonBytes(object), StandardCharsets.UTF_8);
            assertThat(encoded)
                    .as(vector.path("name").textValue())
                    .isEqualTo("{\"value\":" + vector.path("expected").textValue() + "}\n");
        }
    }
}
