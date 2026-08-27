package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.List;

final class GoldenBundleFixture {
    static final List<String> BUNDLE_FILES = List.of(
            "bundle_manifest.json",
            "analysis_config.json",
            "producer_runtime.json",
            "equipment_operating_ranges.json",
            "quality_risk_intervals.json",
            "replay_events.csv",
            "analysis_summary.json");
    static final String BUNDLE_ID = "sha256:4f40427690116f0defcbe609d9e5e1ef4025bb4193cdf099386659a26dd9f8bc";
    static final String CRITERIA_ID = "sha256:8c88d3109bd6945b76317ee415c7821524f515c9bd79d36b8072c285c6434af7";
    private static final ObjectMapper JSON = new ObjectMapper();

    private GoldenBundleFixture() {
    }

    static Path source() {
        return Path.of(System.getProperty("sfep.repo-root"))
                .resolve("contracts/equipment-monitor/v1/golden-bundle");
    }

    static Path copyTo(Path target) throws IOException {
        Files.createDirectory(target);
        for (String name : BUNDLE_FILES) {
            Files.copy(source().resolve(name), target.resolve(name), StandardCopyOption.COPY_ATTRIBUTES);
        }
        return target;
    }

    static JsonNode readManifest(Path bundle) throws IOException {
        return JSON.readTree(bundle.resolve("bundle_manifest.json").toFile());
    }

    static void writeManifest(Path bundle, JsonNode manifest) throws IOException {
        JSON.writeValue(bundle.resolve("bundle_manifest.json").toFile(), manifest);
    }
}
