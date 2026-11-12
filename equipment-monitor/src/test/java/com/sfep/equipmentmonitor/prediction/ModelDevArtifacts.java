package com.sfep.equipmentmonitor.prediction;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sfep.equipmentmonitor.bundle.JsonSupport;
import com.sfep.equipmentmonitor.risk.RiskScalar;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

record ModelDevArtifacts(Path directory, Path model, Path metadata, JsonNode vectors, JsonNode report) {
    static ModelDevArtifacts load() throws IOException {
        String configured = System.getProperty("sfep.model-dev-dir");
        assumeTrue(configured != null && !configured.isBlank(), "No sfep.model-dev-dir configured");
        return load(Path.of(configured));
    }

    static ModelDevArtifacts load(Path directory) throws IOException {
        Path model = directory.resolve("quality_model.onnx");
        Path metadata = directory.resolve("quality_model.json");
        Path vectors = directory.resolve("quality_model_test_vectors.json");
        Path report = directory.resolve("quality_model_report.json");
        List<Path> files = List.of(model, metadata, vectors, report);
        assumeTrue(files.stream().anyMatch(path -> !Files.notExists(path)), "No model artifacts in " + directory);
        for (Path path : files) {
            assertTrue(Files.isRegularFile(path) && Files.size(path) > 0, "Missing or empty model artifact " + path);
        }
        return new ModelDevArtifacts(directory, model, metadata, read(vectors), read(report));
    }

    static JsonNode read(Path path) throws IOException {
        return JsonSupport.parse(Files.readAllBytes(path), "INVALID_TEST_JSON", path.toString());
    }

    void writeReport(String name, Map<String, ?> metrics) throws IOException {
        new ObjectMapper().writerWithDefaultPrettyPrinter().writeValue(directory.resolve(name).toFile(), metrics);
    }

    record Row(Map<String, RiskScalar> values, Set<String> seenFields) {
        static Row read(JsonNode row) {
            assertTrue(row.isObject() && !row.isEmpty(), "Expected a nonempty raw vector");
            Map<String, RiskScalar> values = new LinkedHashMap<>();
            Set<String> seenFields = new HashSet<>();
            row.properties().forEach(entry -> {
                seenFields.add(entry.getKey());
                JsonNode value = entry.getValue();
                if (value.isTextual()) {
                    values.put(entry.getKey(), RiskScalar.of(value.textValue()));
                } else if (value.isNumber()) {
                    values.put(entry.getKey(), RiskScalar.of(value.decimalValue()));
                } else {
                    assertTrue(value.isNull(), "Expected a number, string or null for " + entry.getKey());
                }
            });
            return new Row(Map.copyOf(values), Set.copyOf(seenFields));
        }
    }
}
