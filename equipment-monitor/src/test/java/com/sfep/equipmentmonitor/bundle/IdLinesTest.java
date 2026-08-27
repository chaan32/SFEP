package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class IdLinesTest {
    private static final ObjectMapper JSON = new ObjectMapper();

    @Test
    void canonicalIdLinesSortsUtf8KeysAndRequiresFinalLf() {
        Map<String, String> fields = new LinkedHashMap<>();
        fields.put("z", "last");
        fields.put("a", "first");

        IdValue result = IdLines.compute("sfep-test/v1", fields);

        assertThat(result.preimage()).isEqualTo("sfep-test/v1\na=first\nz=last\n");
        assertThat(result.id()).isEqualTo(Digests.sha256Uri(result.preimage().getBytes(StandardCharsets.UTF_8)));
    }

    @Test
    void rejectsAmbiguousNamespaceKeysAndValues() {
        assertThatThrownBy(() -> IdLines.compute("bad\nnamespace", Map.of("a", "b")))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> IdLines.compute("ok", Map.of("a=b", "c")))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> IdLines.compute("ok", Map.of("a", "b\r")))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void reproducesEveryNormativeJsonIdVectorByteForByte() throws Exception {
        JsonNode vectors = JSON.readTree(Path.of(System.getProperty("sfep.repo-root"))
                .resolve("contracts/equipment-monitor/v1/id-test-vectors.json").toFile());

        vectors.properties().forEach(entry -> {
            JsonNode vector = entry.getValue();
            IdValue actual = DigestIds.compute(vector.path("namespace").textValue(), vector.path("object"));
            assertThat(actual.preimage()).as(entry.getKey()).isEqualTo(vector.path("preimage").textValue());
            assertThat(actual.id()).as(entry.getKey()).isEqualTo(vector.path("expected").textValue());
        });
    }

    @Test
    void preservesUtf8KoreanFurnaceIdsWithoutAsciiEscaping() {
        var value = JsonNodeFactory.instance.objectNode();
        value.put("batchId", "sha256:" + "a".repeat(64));
        value.put("equipmentId", "1호기");
        value.put("equipmentType", "FURNACE");

        IdValue actual = DigestIds.compute("sfep-equipment-batch-id/v1", value);

        assertThat(actual.preimage()).isEqualTo(
                "sfep-equipment-batch-id/v1\n"
                        + "{\"batchId\":\"sha256:" + "a".repeat(64)
                        + "\",\"equipmentId\":\"1호기\",\"equipmentType\":\"FURNACE\"}\n");
        assertThat(actual.id())
                .isEqualTo("sha256:8ebb4203c3ccd32560278c3c205e1c5bc3d71de64a91240219f4b7d0072c3531");
    }

    @Test
    void reproducesGoldenCriteriaAndBundleIdsFromTheManifestIdentityMaps() throws Exception {
        JsonNode manifest = JSON.readTree(GoldenBundleFixture.source().resolve("bundle_manifest.json").toFile());

        Map<String, String> criteria = JSON.convertValue(
                manifest.path("criteriaIdentity"),
                JSON.getTypeFactory().constructMapType(Map.class, String.class, String.class));
        Map<String, String> bundle = JSON.convertValue(
                manifest.path("identity"),
                JSON.getTypeFactory().constructMapType(Map.class, String.class, String.class));

        assertThat(IdLines.compute("sfep-criteria-id/v1", criteria).id())
                .isEqualTo(manifest.path("criteriaId").textValue());
        assertThat(IdLines.compute("sfep-bundle-id/v1", bundle).id())
                .isEqualTo(manifest.path("bundleId").textValue());
    }
}
