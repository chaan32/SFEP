package com.sfep.equipmentmonitor.prediction;

import com.fasterxml.jackson.databind.JsonNode;
import com.sfep.equipmentmonitor.bundle.BundleLoadException;
import com.sfep.equipmentmonitor.bundle.DigestIds;
import com.sfep.equipmentmonitor.bundle.JsonSupport;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

public record QualityModelMetadata(String modelId, String model, String onnxSha256, String qualification,
                                   double threshold, FeatureEncoder encoder) {
    public static QualityModelMetadata read(Path path) {
        JsonNode metadata;
        try {
            metadata = JsonSupport.parse(Files.readAllBytes(path), "INVALID_MODEL_METADATA", path.toString());
        } catch (IOException error) {
            throw new BundleLoadException("MODEL_METADATA_READ_FAILED", path.toString(), error);
        }
        if (metadata.size() != 7 || !"sfep-quality-model-dev/v1".equals(metadata.path("schemaVersion").textValue())) {
            throw new BundleLoadException("INVALID_MODEL_METADATA", path.toString());
        }
        return parse(metadata, DigestIds.canonicalSha256Uri(metadata));
    }

    public static QualityModelMetadata parse(JsonNode metadata, String modelId) {
        String model = metadata.path("model").textValue();
        String qualification = metadata.path("qualification").textValue();
        JsonNode threshold = metadata.path("threshold");
        String onnxSha256 = metadata.path("onnxSha256").textValue();
        String featureSha256 = metadata.path("featureSha256").textValue();
        JsonNode features = metadata.path("features");

        if (model == null || model.isBlank() || !validDigest(onnxSha256) || !validDigest(featureSha256)
                || !threshold.isNumber() || !validProbability(threshold.doubleValue()) || !features.isObject()
                || !("QUALIFIED".equals(qualification) || "RESEARCH_ONLY".equals(qualification))) {
            throw new BundleLoadException("INVALID_MODEL_METADATA", modelId);
        }
        FeatureEncoder encoder = FeatureEncoder.fromContract(features);
        if (!featureSha256.equals(DigestIds.canonicalSha256Uri(features))) {
            throw new BundleLoadException("FEATURE_DIGEST_MISMATCH", modelId);
        }
        return new QualityModelMetadata(modelId, model, onnxSha256, qualification, threshold.doubleValue(), encoder);
    }

    public int inputWidth() {
        return encoder.width();
    }

    private static boolean validDigest(String value) {
        return value != null && value.matches("sha256:[0-9a-f]{64}");
    }

    static boolean validProbability(double value) {
        return value >= 0.0 && value <= 1.0;
    }
}
