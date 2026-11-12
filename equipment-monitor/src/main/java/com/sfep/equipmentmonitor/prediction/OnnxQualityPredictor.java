package com.sfep.equipmentmonitor.prediction;

import ai.onnxruntime.NodeInfo;
import ai.onnxruntime.OnnxJavaType;
import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtException;
import ai.onnxruntime.OrtSession;
import ai.onnxruntime.TensorInfo;
import com.sfep.equipmentmonitor.bundle.BundleLoadException;
import com.sfep.equipmentmonitor.bundle.Digests;
import com.sfep.equipmentmonitor.risk.RiskScalar;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.FloatBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.Set;

public final class OnnxQualityPredictor implements QualityPredictor, AutoCloseable {
    private static final Logger LOGGER = LoggerFactory.getLogger(OnnxQualityPredictor.class);
    private static final String INPUT = "features";
    private static final String PROBABILITIES = "probabilities";
    private static final String LABEL = "label";
    private static final Set<String> REQUESTED_OUTPUT = Set.of(PROBABILITIES);

    private final QualityModelMetadata metadata;
    private final OrtEnvironment environment;
    private final OrtSession session;
    private boolean closed;

    private OnnxQualityPredictor(QualityModelMetadata metadata, OrtEnvironment environment, OrtSession session) {
        this.metadata = metadata;
        this.environment = environment;
        this.session = session;
    }

    public static OnnxQualityPredictor open(Path modelPath, Path metadataPath) {
        QualityModelMetadata metadata = QualityModelMetadata.read(metadataPath);
        byte[] model;
        try {
            model = Files.readAllBytes(modelPath);
        } catch (IOException error) {
            throw new BundleLoadException("ONNX_READ_FAILED", modelPath.toString(), error);
        }
        if (!metadata.onnxSha256().equals(Digests.sha256Uri(model))) {
            throw new BundleLoadException("ONNX_DIGEST_MISMATCH", modelPath.toString());
        }

        // ONNX Runtime owns its process-wide environment and closes it at JVM shutdown.
        OrtEnvironment environment = OrtEnvironment.getEnvironment();
        try (OrtSession.SessionOptions options = new OrtSession.SessionOptions()) {
            options.setIntraOpNumThreads(1);
            options.setInterOpNumThreads(1);
            OrtSession session = environment.createSession(model, options);
            try {
                validateIo(session, metadata.encoder().width());
                return new OnnxQualityPredictor(metadata, environment, session);
            } catch (OrtException | RuntimeException error) {
                try {
                    session.close();
                } catch (OrtException closeError) {
                    error.addSuppressed(closeError);
                }
                throw error;
            }
        } catch (OrtException error) {
            throw new BundleLoadException("ONNX_OPEN_FAILED", modelPath.toString(), error);
        }
    }

    private static void validateIo(OrtSession session, int width) throws OrtException {
        Map<String, NodeInfo> inputs = session.getInputInfo();
        Map<String, NodeInfo> outputs = session.getOutputInfo();
        if (!inputs.keySet().equals(Set.of(INPUT)) || !outputs.keySet().equals(Set.of(LABEL, PROBABILITIES))
                || !tensorMatches(inputs.get(INPUT), OnnxJavaType.FLOAT, width)
                || !tensorMatches(outputs.get(LABEL), OnnxJavaType.INT64)
                || !tensorMatches(outputs.get(PROBABILITIES), OnnxJavaType.FLOAT, 2)) {
            throw new BundleLoadException("INVALID_ONNX_IO", "Unexpected input or output tensor contract");
        }
    }

    private static boolean tensorMatches(NodeInfo node, OnnxJavaType type, long... dimensions) {
        if (!(node.getInfo() instanceof TensorInfo tensor) || tensor.type != type) {
            return false;
        }
        long[] shape = tensor.getShape();
        if (shape.length != dimensions.length + 1 || (shape[0] != -1 && shape[0] != 1)) {
            return false;
        }
        for (int index = 0; index < dimensions.length; index++) {
            if (shape[index + 1] != dimensions[index]) {
                return false;
            }
        }
        return true;
    }

    public QualityModelMetadata metadata() {
        return metadata;
    }

    @Override
    public synchronized QualityPrediction predict(Map<String, RiskScalar> values, Set<String> seenFields) {
        if (closed) {
            throw new IllegalStateException("ONNX quality predictor is closed");
        }
        FeatureEncoder.Encoded input = metadata.encoder().encode(values, seenFields);
        if (input.inputIncomplete()) {
            return prediction(PredictionStatus.INPUT_INCOMPLETE, null);
        }

        try (OnnxTensor tensor = OnnxTensor.createTensor(environment, FloatBuffer.wrap(input.vector()),
                new long[]{1, input.vector().length});
             OrtSession.Result result = session.run(Map.of(INPUT, tensor), REQUESTED_OUTPUT)) {
            if (result.get(0).getValue() instanceof float[][] probabilities
                    && probabilities.length == 1 && probabilities[0].length == 2
                    && QualityModelMetadata.validProbability(probabilities[0][0])
                    && QualityModelMetadata.validProbability(probabilities[0][1])) {
                return prediction(PredictionStatus.AVAILABLE, (double) probabilities[0][1]);
            }
            LOGGER.warn("Invalid ONNX probability tensor for model {}", metadata.modelId());
        } catch (OrtException error) {
            LOGGER.warn("ONNX quality inference failed for model {}", metadata.modelId(), error);
        }
        return prediction(PredictionStatus.INFERENCE_FAILED, null);
    }

    private QualityPrediction prediction(PredictionStatus status, Double probability) {
        return new QualityPrediction(status, probability, metadata.modelId(), metadata.qualification(),
                metadata.threshold());
    }

    @Override
    public synchronized void close() {
        if (closed) {
            return;
        }
        closed = true;
        try {
            session.close();
        } catch (OrtException error) {
            throw new BundleLoadException("ONNX_CLOSE_FAILED", metadata.modelId(), error);
        }
    }
}
