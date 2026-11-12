package com.sfep.equipmentmonitor.prediction;

import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

class JavaInferenceBenchmarkTest {
    @Test
    void recordsSingleRowEncodingAndInferenceLatency() throws Exception {
        ModelDevArtifacts artifacts = ModelDevArtifacts.load();
        ModelDevArtifacts.Row row = ModelDevArtifacts.Row.read(artifacts.vectors().path("raw").get(0));
        int warmup = 1_000;
        long[] samples = new long[10_000];
        try (OnnxQualityPredictor predictor = OnnxQualityPredictor.open(artifacts.model(), artifacts.metadata())) {
            for (int index = 0; index < warmup; index++) {
                assertEquals(PredictionStatus.AVAILABLE, predictor.predict(row.values(), row.seenFields()).status());
            }
            for (int index = 0; index < samples.length; index++) {
                long start = System.nanoTime();
                QualityPrediction prediction = predictor.predict(row.values(), row.seenFields());
                samples[index] = System.nanoTime() - start;
                assertEquals(PredictionStatus.AVAILABLE, prediction.status());
            }
        }
        Arrays.sort(samples);
        int mid = samples.length / 2;
        artifacts.writeReport("java_inference_benchmark.json", Map.of(
                "warmupIterations", warmup, "measuredIterations", samples.length,
                "meanMs", Arrays.stream(samples).average().orElseThrow() / 1e6,
                "medianMs", (samples[mid - 1] + samples[mid]) / 2e6,
                "p95Ms", samples[(int) Math.ceil(samples.length * 0.95) - 1] / 1e6,
                "jvm", System.getProperty("java.vm.name") + " " + Runtime.version(),
                "cpuArchitecture", System.getProperty("os.arch"),
                "availableProcessors", Runtime.getRuntime().availableProcessors()));
    }
}
