package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sfep.equipmentmonitor.prediction.PredictionStatus;
import com.sfep.equipmentmonitor.risk.RiskScalar;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.*;

class BundleV3ModelIntegrationTest {
    private static final Path V3 = Path.of(System.getProperty("sfep.repo-root"),
            "contracts/equipment-monitor/v3");

    @Test
    void verifiedSyntheticModelMatchesPythonAndCloses(@TempDir Path temp) throws Exception {
        Path bundle = copyBundle(temp);
        var artifact = new BundleLoader().load(bundle).qualityModel().orElseThrow();
        JsonNode vectors = BundleTestJson.mapper().readTree(
                V3.resolve("golden-expectation/quality_model_test_vectors.json").toFile());
        assertFalse(vectors.path("raw").isEmpty());
        Files.delete(bundle.resolve("quality_model.onnx"));
        var predictor = artifact.openPredictor();
        try (predictor) {
            assertEquals(vectors.path("raw").size(), vectors.path("probabilities").size());
            for (int index = 0; index < vectors.path("raw").size(); index++) {
                Map<String, RiskScalar> values = new HashMap<>();
                JsonNode raw = vectors.path("raw").get(index);
                raw.properties().forEach(field -> {
                    if (!field.getValue().isNull()) {
                        values.put(field.getKey(), field.getValue().isNumber()
                                ? RiskScalar.of(field.getValue().doubleValue())
                                : RiskScalar.of(field.getValue().textValue()));
                    }
                });
                Set<String> seen = raw.properties().stream()
                        .map(Map.Entry::getKey).collect(Collectors.toSet());
                JsonNode matrixRow = vectors.path("matrix").get(index);
                float[] expected = new float[matrixRow.size()];
                for (int column = 0; column < expected.length; column++) {
                    expected[column] = matrixRow.get(column).floatValue();
                }
                assertArrayEquals(expected, artifact.metadata().encoder().encode(values, seen).vector());
                var prediction = predictor.predict(values, seen);
                assertEquals(PredictionStatus.AVAILABLE, prediction.status());
                assertEquals(vectors.path("probabilities").get(index).doubleValue(),
                        prediction.probability(), 1e-5);
            }
        }
        assertThrows(IllegalStateException.class, () -> predictor.predict(Map.of(), Set.of()));
    }

    @Test
    void rejectsChangedModelBytes(@TempDir Path temp) throws Exception {
        Path bundle = copyBundle(temp);
        Path path = bundle.resolve("quality_model.onnx");
        byte[] model = Files.readAllBytes(path);
        model[model.length / 2] ^= 1;
        Files.write(path, model);
        assertRejected("ARTIFACT_HASH_MISMATCH", bundle);
    }

    @Test
    void rejectsModelPathThatEscapesBundle(@TempDir Path temp) throws Exception {
        Path bundle = copyBundle(Files.createDirectory(temp.resolve("bundle")));
        Path model = bundle.resolve("quality_model.onnx");
        Path external = Files.move(model, temp.resolve("outside.onnx"));
        Files.createSymbolicLink(model, external);
        assertRejected("BUNDLE_CHILD_UNSAFE", bundle);
    }

    @Test
    void rejectsMetadataWithStaleModelIdEvenIfManifestIsReattested(@TempDir Path temp) throws Exception {
        Path bundle = copyBundle(temp);
        ObjectNode metadata = readObject(bundle.resolve("quality_model.json"));
        metadata.put("threshold", metadata.path("threshold").doubleValue() == 0.99 ? 0.98 : 0.99);
        replaceMetadata(bundle, metadata, false);
        assertRejected("MODEL_ID_MISMATCH", bundle);
    }

    @Test
    void rejectsMetadataThatMisstatesOnnxOpsetImports(@TempDir Path temp) throws Exception {
        Path bundle = copyBundle(temp);
        ObjectNode metadata = readObject(bundle.resolve("quality_model.json"));
        ((ObjectNode) metadata.path("onnx").path("opsets")).remove("ai.onnx.ml");
        replaceMetadata(bundle, metadata, true);
        assertRejected("INVALID_ONNX_CONTRACT", bundle);
    }

    @Test
    void rejectsReattestedModelWithUnexpectedGraphOutput(@TempDir Path temp) throws Exception {
        Path bundle = copyBundle(temp);
        Path path = bundle.resolve("quality_model.onnx");
        byte[] model = Files.readAllBytes(path);
        int output = new String(model, StandardCharsets.ISO_8859_1).lastIndexOf("probabilities");
        assertTrue(output >= 0);
        model[output] = 'P';
        Files.write(path, model);
        ObjectNode metadata = readObject(bundle.resolve("quality_model.json"));
        metadata.put("onnxSha256", Digests.sha256Uri(model));
        replaceMetadata(bundle, metadata, true);
        assertRejected("INVALID_ONNX_CONTRACT", bundle);
    }

    @Test
    void rejectsReattestedModelWithStaleInternalDigest(@TempDir Path temp) throws Exception {
        Path bundle = copyBundle(temp);
        Path path = bundle.resolve("quality_model.onnx");
        Files.write(path, new byte[]{1});
        replaceMetadata(bundle, readObject(bundle.resolve("quality_model.json")), false);
        assertRejected("ONNX_DIGEST_MISMATCH", bundle);
    }

    @Test
    void rejectsMetadataWithoutTriggerStage(@TempDir Path temp) throws Exception {
        Path bundle = copyBundle(temp);
        ObjectNode metadata = readObject(bundle.resolve("quality_model.json"));
        ((ObjectNode) metadata.path("onnx")).remove("triggerStage");
        replaceMetadata(bundle, metadata, true);
        assertRejected("JSON_SCHEMA_INVALID", bundle);
    }

    @Test
    void rejectsReplayWithoutOneMaterialsTrigger(@TempDir Path temp) throws Exception {
        Path bundle = copyBundle(temp);
        Path replay = bundle.resolve("replay_events.csv");
        var rows = Files.readAllLines(replay);
        String trigger = rows.stream().filter(row -> row.contains(",FURNACE_EXTRACTED,")).findFirst().orElseThrow();
        rows.remove(trigger);
        Files.write(replay, rows);
        reattestReplay(bundle);
        assertRejected("REPLAY_MODEL_TRIGGER_MISSING", bundle);
    }

    @Test
    void rejectsReplayWithoutRequiredFeatureKey(@TempDir Path temp) throws Exception {
        Path bundle = copyBundle(temp);
        Path replay = bundle.resolve("replay_events.csv");
        String csv = Files.readString(replay);
        assertTrue(csv.contains("\"\"f_bfg\"\""));
        Files.writeString(replay, csv.replaceFirst("\"\"f_bfg\"\"", "\"\"unknown_feature\"\""));
        reattestReplay(bundle);
        assertRejected("REPLAY_SCHEMA_INVALID", bundle);
    }

    private static void replaceMetadata(Path bundle, ObjectNode metadata, boolean recomputeId) throws Exception {
        Path manifestPath = bundle.resolve("bundle_manifest.json");
        ObjectNode manifest = readObject(manifestPath);
        ObjectNode identity = (ObjectNode) manifest.path("identity");
        if (recomputeId) {
            metadata.remove("modelId");
            String id = IdLines.compute("sfep-quality-model-id/v1", Map.of(
                    "metadata_sha256", DigestIds.canonicalSha256Uri(metadata))).id();
            metadata.put("modelId", id);
            identity.put("model_id", id);
        }
        Path metadataPath = bundle.resolve("quality_model.json");
        Files.write(metadataPath, DigestIds.canonicalJsonBytes(metadata));
        identity.put("quality_model_metadata_sha256",
                reattest(manifest, "quality_model_metadata", metadataPath));
        identity.put("quality_model_sha256",
                reattest(manifest, "quality_model", bundle.resolve("quality_model.onnx")));
        Map<String, String> claims = new HashMap<>();
        identity.properties().forEach(entry -> claims.put(entry.getKey(), entry.getValue().textValue()));
        manifest.put("bundleId", IdLines.compute("sfep-bundle-id/v1", claims).id());
        Files.write(manifestPath, DigestIds.canonicalJsonBytes(manifest));
    }

    private static void reattestReplay(Path bundle) throws Exception {
        Path path = bundle.resolve("bundle_manifest.json");
        ObjectNode manifest = readObject(path);
        reattest(manifest, "replay_events", bundle.resolve("replay_events.csv"));
        Files.write(path, DigestIds.canonicalJsonBytes(manifest));
    }

    private static String reattest(ObjectNode manifest, String role, Path artifact) throws Exception {
        String hash = Digests.sha256Uri(artifact);
        for (JsonNode descriptor : manifest.path("artifacts")) {
            if (role.equals(descriptor.path("role").textValue())) {
                ((ObjectNode) descriptor).put("sha256", hash).put("sizeBytes", Files.size(artifact));
                return hash;
            }
        }
        throw new AssertionError("missing descriptor " + role);
    }

    private static ObjectNode readObject(Path path) throws Exception {
        return (ObjectNode) BundleTestJson.mapper().readTree(path.toFile());
    }

    private static void assertRejected(String code, Path bundle) {
        assertEquals(code, assertThrows(BundleLoadException.class, () -> new BundleLoader().load(bundle)).code());
    }

    private static Path copyBundle(Path destination) throws Exception {
        try (var files = Files.list(V3.resolve("golden-bundle"))) {
            for (Path source : files.toList()) {
                Files.copy(source, destination.resolve(source.getFileName()));
            }
        }
        return destination.toRealPath();
    }
}
