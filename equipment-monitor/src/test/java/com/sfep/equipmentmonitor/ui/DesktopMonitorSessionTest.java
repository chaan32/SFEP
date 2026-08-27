package com.sfep.equipmentmonitor.ui;

import com.sfep.equipmentmonitor.bundle.BundleLoader;
import com.sfep.equipmentmonitor.bundle.LoadedBundle;
import com.sfep.equipmentmonitor.replay.ReplayStatus;
import com.sfep.equipmentmonitor.state.MonitorUpdate;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

class DesktopMonitorSessionTest {
    @TempDir
    Path temporary;

    @Test
    void wiresSealedGoldenBundleThroughBackgroundReplayIntoHistoricalMonitor() throws Exception {
        LoadedBundle bundle = new BundleLoader().load(runtimeGoldenBundle());
        List<MonitorUpdate> updates = new CopyOnWriteArrayList<>();
        List<String> listenerThreads = new CopyOnWriteArrayList<>();

        try (DesktopMonitorSession session = new DesktopMonitorSession(bundle);
             AutoCloseable ignored = session.addUpdateListener(update -> {
                 updates.add(update);
                 listenerThreads.add(Thread.currentThread().getName());
             })) {
            assertThat(session.metadata()).satisfies(metadata -> {
                assertThat(metadata.bundleId()).isEqualTo(
                        "sha256:4f40427690116f0defcbe609d9e5e1ef4025bb4193cdf099386659a26dd9f8bc");
                assertThat(metadata.criteriaId()).isEqualTo(
                        "sha256:8c88d3109bd6945b76317ee415c7821524f515c9bd79d36b8072c285c6434af7");
                assertThat(metadata.dateFrom()).isEqualTo("2025-01-01");
                assertThat(metadata.dateTo()).isEqualTo("2025-04-02");
                assertThat(metadata.holdoutTotal()).isEqualTo(2);
                assertThat(metadata.replayRows()).isEqualTo(95);
                assertThat(metadata.rangeDefinitionCount()).isZero();
                assertThat(metadata.qualityRuleDefinitionCount()).isEqualTo(165);
            });

            session.step();
            await().atMost(Duration.ofSeconds(5)).untilAsserted(() ->
                    assertThat(session.state().status()).isIn(ReplayStatus.PAUSED, ReplayStatus.COMPLETED));

            assertThat(updates).hasSize(1);
            assertThat(updates.getFirst().unitsProcessed()).isEqualTo(1);
            assertThat(updates.getFirst().eventsProcessed()).isPositive();
            assertThat(listenerThreads).allMatch(name -> name.equals("sfep-replay-controller"));
            assertThat(session.snapshot().unitsProcessed()).isEqualTo(1);
        }
    }

    private Path runtimeGoldenBundle() throws Exception {
        Path source = Path.of(System.getProperty("sfep.repo-root"))
                .resolve("contracts/equipment-monitor/v1/golden-bundle");
        Path destination = Files.createDirectory(temporary.toRealPath().resolve("runtime-bundle"));
        for (String name : List.of(
                "bundle_manifest.json",
                "analysis_config.json",
                "producer_runtime.json",
                "equipment_operating_ranges.json",
                "quality_risk_intervals.json",
                "replay_events.csv",
                "analysis_summary.json")) {
            Files.copy(source.resolve(name), destination.resolve(name));
        }
        return destination;
    }
}
