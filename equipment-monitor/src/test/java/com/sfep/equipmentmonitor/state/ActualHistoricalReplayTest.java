package com.sfep.equipmentmonitor.state;

import com.sfep.equipmentmonitor.alert.AlertKind;
import com.sfep.equipmentmonitor.bundle.BundleLoader;
import com.sfep.equipmentmonitor.bundle.LoadedBundle;
import com.sfep.equipmentmonitor.replay.ReplayCursor;
import com.sfep.equipmentmonitor.risk.RiskDefinitionCompiler;
import com.sfep.equipmentmonitor.risk.RiskGrade;
import com.sfep.equipmentmonitor.risk.RangeSelectionReason;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

class ActualHistoricalReplayTest {
    private static final String CCR_DANGER_RULE =
            "sha256:a09f13dea430e6292665bd7c2bb5c4dfc5c411808f7adf015a2d33ab612c93bc";

    @Test
    void replaysTheEntireLocallySealedBundleAndProducesExplainableCcrAlerts() {
        String configured = System.getenv("SFEP_ACTUAL_BUNDLE");
        assumeTrue(configured != null && !configured.isBlank(), "local-only actual bundle not configured");
        LoadedBundle bundle = new BundleLoader().load(Path.of(configured));
        RiskDefinitionCompiler compiler = new RiskDefinitionCompiler();
        HistoricalMonitor monitor = new HistoricalMonitor(
                bundle.bundleId(),
                compiler.compileRanges(bundle.ranges()),
                compiler.compileRules(bundle.rules()));

        try (ReplayCursor cursor = ReplayCursor.open(bundle.replay())) {
            while (cursor.nextUnit().map(unit -> {
                monitor.accept(unit);
                return unit;
            }).isPresent()) {
                // Streaming cursor retains only the current replay unit.
            }
        }

        MonitorSnapshot snapshot = monitor.snapshot();
        assertThat(snapshot.unitsProcessed()).isEqualTo(6_468);
        assertThat(snapshot.eventsProcessed()).isEqualTo(189_043);
        assertThat(snapshot.materials()).hasSize(23_631);
        assertThat(snapshot.materials().values().stream()
                .flatMap(material -> material.assessments().stream())
                .flatMap(assessment -> assessment.rangeEvaluations().stream())
                .filter(range -> range.selectionReason()
                        == RangeSelectionReason.BAND_BOUNDARY_NOT_SEALED_FALLBACK)
                .count()).isPositive();
        assertThat(snapshot.alerts())
                .filteredOn(alert -> alert.ruleId().equals(CCR_DANGER_RULE))
                .hasSize(11_827)
                .allSatisfy(alert -> {
                    assertThat(alert.kind()).isEqualTo(AlertKind.QUALITY_RISK);
                    assertThat(alert.grade()).isEqualTo(RiskGrade.DANGER);
                    assertThat(alert.eventStage()).isEqualTo("FURNACE_CHARGED");
                });
        assertThat(snapshot.alerts())
                .filteredOn(alert -> alert.kind() == AlertKind.QUALITY_RISK)
                .noneSatisfy(alert -> assertThat(alert.eventStage()).isEqualTo("AP_RECORDED_WITH_RESULT"));
    }
}
