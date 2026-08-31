package com.sfep.equipmentmonitor.bundle;

import com.sfep.equipmentmonitor.alert.HistoricalAlert;
import com.sfep.equipmentmonitor.replay.ReplayCursor;
import com.sfep.equipmentmonitor.replay.ReplayEvent;
import com.sfep.equipmentmonitor.replay.ReplayUnit;
import com.sfep.equipmentmonitor.risk.RiskDefinitionCompiler;
import com.sfep.equipmentmonitor.state.HistoricalMonitor;
import com.sfep.equipmentmonitor.state.MonitorSnapshot;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

@Tag("actual-bundle-v2-parity")
class ActualBundleV2ParityTest {
    @Test
    void preservesTheCompleteActualV1SemanticsWithOnlyBundleAndCriteriaBindingsNormalized() {
        String v1Configured = System.getProperty("sfep.actual-bundle", System.getenv("SFEP_ACTUAL_BUNDLE"));
        String v2Configured = System.getProperty("sfep.actual-bundle-v2", System.getenv("SFEP_ACTUAL_BUNDLE_V2"));
        assumeTrue(v1Configured != null && !v1Configured.isBlank(), "local-only actual v1 bundle not configured");
        assumeTrue(v2Configured != null && !v2Configured.isBlank(), "local-only actual v2 bundle not configured");

        LoadedBundle v1 = new BundleLoader().load(Path.of(v1Configured));
        LoadedBundle v2 = new BundleLoader().load(Path.of(v2Configured));
        RiskDefinitionCompiler compiler = new RiskDefinitionCompiler();

        assertThat(v2.ranges()).containsExactlyElementsOf(v1.ranges());
        assertThat(v2.rules()).containsExactlyElementsOf(v1.rules());

        HistoricalMonitor v1Monitor = new HistoricalMonitor(
                v1.bundleId(), compiler.compileRanges(v1.ranges()), compiler.compileRules(v1.rules()));
        HistoricalMonitor v2Monitor = new HistoricalMonitor(
                v2.bundleId(), compiler.compileRanges(v2.ranges()), compiler.compileRules(v2.rules()));
        replayInLockstep(v1, v2, v1Monitor, v2Monitor);

        MonitorSnapshot v1Snapshot = v1Monitor.snapshot();
        MonitorSnapshot v2Snapshot = v2Monitor.snapshot();
        assertThat(v2Snapshot.unitsProcessed()).isEqualTo(v1Snapshot.unitsProcessed());
        assertThat(v2Snapshot.eventsProcessed()).isEqualTo(v1Snapshot.eventsProcessed());
        assertThat(v2Snapshot.materials()).isEqualTo(v1Snapshot.materials());
        assertThat(v2Snapshot.alerts().stream().map(ActualBundleV2ParityTest::normalizeAlert).toList())
                .containsExactlyElementsOf(v1Snapshot.alerts().stream()
                        .map(ActualBundleV2ParityTest::normalizeAlert).toList());
    }

    private static void replayInLockstep(
            LoadedBundle v1,
            LoadedBundle v2,
            HistoricalMonitor v1Monitor,
            HistoricalMonitor v2Monitor) {
        try (ReplayCursor v1Cursor = ReplayCursor.open(v1.replay());
             ReplayCursor v2Cursor = ReplayCursor.open(v2.replay())) {
            while (true) {
                Optional<ReplayUnit> v1Unit = v1Cursor.nextUnit();
                Optional<ReplayUnit> v2Unit = v2Cursor.nextUnit();
                assertThat(v2Unit.isPresent()).isEqualTo(v1Unit.isPresent());
                if (v1Unit.isEmpty()) break;
                assertSameUnit(v1Unit.orElseThrow(), v2Unit.orElseThrow());
                v1Monitor.accept(v1Unit.orElseThrow());
                v2Monitor.accept(v2Unit.orElseThrow());
            }
        }
    }

    private static void assertSameUnit(ReplayUnit v1, ReplayUnit v2) {
        assertThat(v2.batchId()).isEqualTo(v1.batchId());
        assertThat(v2.batchStep()).isEqualTo(v1.batchStep());
        assertThat(v2.replayDate()).isEqualTo(v1.replayDate());
        assertThat(v2.replayHour()).isEqualTo(v1.replayHour());
        assertThat(v2.batchKind()).isEqualTo(v1.batchKind());
        assertThat(v2.timePrecision()).isEqualTo(v1.timePrecision());
        assertThat(v2.orderedWithinUnit()).isEqualTo(v1.orderedWithinUnit());
        assertThat(v2.events().stream().map(ActualBundleV2ParityTest::normalizeEvent).toList())
                .containsExactlyElementsOf(v1.events().stream()
                        .map(ActualBundleV2ParityTest::normalizeEvent).toList());
    }

    private static EventSemantics normalizeEvent(ReplayEvent event) {
        return new EventSemantics(
                event.schemaVersion(), event.eventId(), event.replayDate(), event.replayHour(),
                event.batchKind(), event.batchId(), event.equipmentBatchId(), event.batchStep(),
                event.timePrecision(), event.materialKey(), event.equipmentType(), event.equipmentId(),
                event.chargeId(), event.slabNo(), event.hrCoilId(), event.apProdId(), event.values());
    }

    private static AlertSemantics normalizeAlert(HistoricalAlert alert) {
        return new AlertSemantics(
                alert.materialKey(), alert.eventStage(), alert.ruleId(), alert.kind(), alert.eventId(),
                alert.replayDate(), alert.replayHour(), alert.equipmentType(), alert.equipmentId(),
                alert.field(), alert.observedValue(), alert.rangeStatus(), alert.grade(), alert.repeated(),
                alert.discovery(), alert.confirmation());
    }

    private record EventSemantics(
            String schemaVersion, String eventId, String replayDate, Integer replayHour,
            String batchKind, String batchId, String equipmentBatchId, String batchStep,
            String timePrecision, String materialKey, String equipmentType, String equipmentId,
            String chargeId, String slabNo, String hrCoilId, String apProdId,
            com.fasterxml.jackson.databind.JsonNode values) {
    }

    private record AlertSemantics(
            String materialKey, String eventStage, String ruleId, Object kind, String eventId,
            String replayDate, Integer replayHour, String equipmentType, String equipmentId,
            String field, Object observedValue, Object rangeStatus, Object grade, boolean repeated,
            Object discovery, Object confirmation) {
    }
}
