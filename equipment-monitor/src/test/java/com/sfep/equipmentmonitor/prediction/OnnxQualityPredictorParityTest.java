package com.sfep.equipmentmonitor.prediction;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sfep.equipmentmonitor.bundle.BundleLoadException;
import com.sfep.equipmentmonitor.bundle.DigestIds;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class OnnxQualityPredictorParityTest {
    @Test
    void matchesPythonOnnxDefectProbabilities() throws Exception {
        ModelDevArtifacts artifacts = ModelDevArtifacts.load();
        JsonNode raw = artifacts.vectors().path("raw");
        JsonNode probabilities = artifacts.vectors().path("probabilities");
        assertTrue(raw.isArray() && !raw.isEmpty() && probabilities.isArray());
        assertEquals(raw.size(), probabilities.size());
        double maxDelta = 0.0;
        boolean aboveThreshold = false;
        try (OnnxQualityPredictor predictor = OnnxQualityPredictor.open(artifacts.model(), artifacts.metadata())) {
            assertEquals(artifacts.report().path("qualification").textValue(), predictor.metadata().qualification());
            for (int index = 0; index < raw.size(); index++) {
                ModelDevArtifacts.Row row = ModelDevArtifacts.Row.read(raw.get(index));
                QualityPrediction prediction = predictor.predict(row.values(), row.seenFields());
                assertEquals(PredictionStatus.AVAILABLE, prediction.status());
                JsonNode expected = probabilities.get(index);
                assertTrue(expected.isNumber() && Double.isFinite(expected.doubleValue()));
                maxDelta = Math.max(maxDelta, Math.abs(expected.doubleValue() - prediction.probability()));
                if (prediction.probability() >= prediction.threshold()) {
                    aboveThreshold = true;
                    if ("RESEARCH_ONLY".equals(prediction.qualification())) {
                        assertFalse(prediction.shouldAlert());
                    }
                }
            }
        }
        assertTrue(aboveThreshold, "Test vectors must exercise threshold crossing");
        assertTrue(maxDelta <= 1e-5, "Max Python ONNX to Java ONNX probability delta " + maxDelta);
        artifacts.writeReport("java_onnx_parity.json", Map.of(
                "pythonJavaOnnxMaxDifference", maxDelta, "absoluteTolerance", 1e-5, "vectors", raw.size()));
    }

    @Test
    void rejectsOnnxAndFeatureDigestChanges(@TempDir Path directory) throws Exception {
        ModelDevArtifacts artifacts = ModelDevArtifacts.load();
        Path invalidModel = Files.writeString(directory.resolve("quality_model.onnx"), "tampered");
        assertEquals("ONNX_DIGEST_MISMATCH", assertThrows(BundleLoadException.class,
                () -> OnnxQualityPredictor.open(invalidModel, artifacts.metadata())).code());

        ObjectNode altered = (ObjectNode) ModelDevArtifacts.read(artifacts.metadata());
        ObjectNode contract = (ObjectNode) altered.path("features");
        contract.put("maxMissingFeatureCount", contract.path("maxMissingFeatureCount").intValue() == 0 ? 1 : 0);
        Path invalidMetadata = Files.writeString(directory.resolve("quality_model.json"), altered.toString());
        assertEquals("FEATURE_DIGEST_MISMATCH", assertThrows(BundleLoadException.class,
                () -> OnnxQualityPredictor.open(artifacts.model(), invalidMetadata)).code());
    }

    @Test
    void rejectsModelInputWidthMismatchAfterDigestVerification(@TempDir Path directory) throws Exception {
        ModelDevArtifacts artifacts = ModelDevArtifacts.load();
        ObjectNode altered = (ObjectNode) ModelDevArtifacts.read(artifacts.metadata());
        ObjectNode contract = (ObjectNode) altered.path("features");
        ArrayNode vocabulary = (ArrayNode) contract.path("features").get(0).path("vocabulary");
        int unknown = vocabulary.size() - 1;
        String extraCategory = unknown == 0 ? "new" : vocabulary.get(unknown - 1).textValue() + "~";
        vocabulary.insert(unknown, extraCategory);
        altered.put("featureSha256", DigestIds.canonicalSha256Uri(contract));
        Path invalidMetadata = Files.writeString(directory.resolve("quality_model.json"), altered.toString());
        assertEquals("INVALID_ONNX_IO", assertThrows(BundleLoadException.class,
                () -> OnnxQualityPredictor.open(artifacts.model(), invalidMetadata)).code());
    }

    @Test
    void skipsInferenceWhenPublishedNullsExceedTheLimit(@TempDir Path directory) throws Exception {
        ModelDevArtifacts artifacts = ModelDevArtifacts.load();
        ObjectNode altered = (ObjectNode) ModelDevArtifacts.read(artifacts.metadata());
        ObjectNode contract = (ObjectNode) altered.path("features");
        contract.put("maxMissingFeatureCount", 0);
        altered.put("featureSha256", DigestIds.canonicalSha256Uri(contract));
        Path strictMetadata = Files.writeString(directory.resolve("quality_model.json"), altered.toString());
        ModelDevArtifacts.Row row = ModelDevArtifacts.Row.read(artifacts.vectors().path("raw").get(0));
        try (OnnxQualityPredictor predictor = OnnxQualityPredictor.open(artifacts.model(), strictMetadata)) {
            QualityPrediction prediction = predictor.predict(Map.of(), row.seenFields());
            assertEquals(PredictionStatus.INPUT_INCOMPLETE, prediction.status());
            assertNull(prediction.probability());
            assertFalse(prediction.shouldAlert());
        }
    }

    @Test
    void closesIdempotentlyAndRejectsFurtherPredictions() throws Exception {
        ModelDevArtifacts artifacts = ModelDevArtifacts.load();
        ModelDevArtifacts.Row row = ModelDevArtifacts.Row.read(artifacts.vectors().path("raw").get(0));
        try (OnnxQualityPredictor predictor = OnnxQualityPredictor.open(artifacts.model(), artifacts.metadata())) {
            predictor.close();
            predictor.close();
            assertThrows(IllegalStateException.class, () -> predictor.predict(row.values(), row.seenFields()));
        }
    }
}
