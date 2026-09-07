package com.sfep.equipmentmonitor.ui;

import com.sfep.equipmentmonitor.bundle.BundleLoader;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class MonitorDashboardMetadataTest {
    @TempDir
    Path temporary;

    @Test
    void projectsVerifiedHashesAuditCountsAndDefinitionIntervalsFromGoldenBundle() throws Exception {
        MonitorDashboardMetadata metadata = MonitorDashboardMetadata.from(
                new BundleLoader().load(runtimeGoldenBundle()));

        assertThat(metadata.managementFacts()).anySatisfy(fact -> {
            assertThat(fact.category()).isEqualTo("원천 데이터 해시");
            assertThat(fact.name()).isEqualTo("제강·연주");
            assertThat(fact.value()).isEqualTo(
                    "sha256:c3bba5c7235166b6693ff657a25797933ea9d83c6392437af9b5141f4e56a68c");
        }).anySatisfy(fact -> {
            assertThat(fact.category()).isEqualTo("격리 건수");
            assertThat(fact.name()).isEqualTo("중복 AP 키");
            assertThat(fact.value()).isEqualTo("2");
        }).anySatisfy(fact -> {
            assertThat(fact.category()).isEqualTo("라벨 검열 건수");
            assertThat(fact.name()).isEqualTo("라벨 공개 시점 미도달");
            assertThat(fact.value()).isEqualTo("2");
        });
        assertThat(metadata.managementFacts().stream()
                .filter(fact -> fact.category().equals("산출물 해시"))).hasSize(6);
        assertThat(metadata.definitions()).hasSize(165);
        assertThat(metadata.definitions()).allSatisfy(definition ->
                assertThat(definition.kind()).isEqualTo("품질 위험구간"));
        assertThat(metadata.definitions()).anySatisfy(definition -> {
            assertThat(definition.ruleId()).startsWith("sha256:");
            assertThat(definition.interval()).contains("allOf");
        });
    }

    private Path runtimeGoldenBundle() throws Exception {
        Path source = Path.of(System.getProperty("sfep.repo-root"))
                .resolve("contracts/equipment-monitor/v1/golden-bundle");
        Path destination = Files.createDirectory(temporary.toRealPath().resolve("runtime-bundle"));
        for (String name : List.of(
                "bundle_manifest.json", "analysis_config.json", "producer_runtime.json",
                "equipment_operating_ranges.json", "quality_risk_intervals.json",
                "replay_events.csv", "analysis_summary.json")) {
            Files.copy(source.resolve(name), destination.resolve(name));
        }
        return destination;
    }
}
