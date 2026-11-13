package com.sfep.equipmentmonitor.ui;

import com.sfep.equipmentmonitor.EquipmentMonitorDesktopApplication;
import com.sfep.equipmentmonitor.alert.AlertKind;
import com.sfep.equipmentmonitor.bundle.BundleLoader;
import com.sfep.equipmentmonitor.bundle.LoadedBundle;
import com.sfep.equipmentmonitor.prediction.PredictionStatus;
import com.sfep.equipmentmonitor.replay.ReplaySpeed;
import com.sfep.equipmentmonitor.replay.ReplayStatus;
import com.sfep.equipmentmonitor.state.MonitorUpdate;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Tag;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

import javax.swing.JLabel;
import javax.swing.JTable;
import javax.swing.SwingUtilities;
import java.awt.Component;
import java.awt.Container;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Duration;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

class ActualDesktopMonitorUiTest {
    @Test
    @Tag("actual-bundle-v1")
    void coalescesTheCompleteActualReplayWithoutLosingOverviewEvidenceOrAlerts() throws Exception {
        String configured = System.getProperty("sfep.actual-bundle", System.getenv("SFEP_ACTUAL_BUNDLE"));
        assumeTrue(configured != null && !configured.isBlank(), "local-only actual bundle not configured");
        LoadedBundle bundle = new BundleLoader().load(Path.of(configured));
        try (DesktopMonitorSession session = new DesktopMonitorSession(bundle)) {
            assertActualReplayUi(session, update -> {}, 54_515, false);
        }
    }

    @Test
    @Tag("actual-bundle-v3")
    void showsActualV3PredictionsWhilePreservingV2StatisticalReplay() throws Exception {
        String v2 = System.getProperty("sfep.actual-bundle-v2");
        String v3 = System.getProperty("sfep.actual-bundle-v3");
        assumeTrue(v2 != null && v3 != null, "run actualBundleV3Test with local actual v2 and v3");
        LoadedBundle baseline = new BundleLoader().load(Path.of(v2));
        ReplayDigest v2Digest = new ReplayDigest(baseline.bundleId());
        int statisticalAlerts;
        try (DesktopMonitorSession session = new DesktopMonitorSession(baseline);
             AutoCloseable ignored = session.addUpdateListener(v2Digest::accept)) {
            assertThat(session.metadata().qualityModel()).isNull();
            replayToEnd(session);
            statisticalAlerts = session.snapshot().alerts().size();
        }

        new ApplicationContextRunner()
                .withUserConfiguration(EquipmentMonitorDesktopApplication.class)
                .withPropertyValues("sfep.equipment-monitor.bundle-dir=" + v3)
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    var session = context.getBean(DesktopMonitorSession.class);
                    var model = session.metadata().qualityModel();
                    assertThat(model).isNotNull();
                    assertThat(model.qualification()).isEqualTo("RESEARCH_ONLY");
                    ReplayDigest v3Digest = new ReplayDigest(session.metadata().bundleId());
                    assertActualReplayUi(session, v3Digest::accept, statisticalAlerts, true);
                    var snapshot = session.snapshot();
                    assertThat(snapshot.eventsProcessed()).isEqualTo(189_043);
                    assertThat(snapshot.alerts().stream()
                            .filter(alert -> alert.kind() == AlertKind.MODEL_QUALITY_RISK).count()).isZero();
                    assertThat(snapshot.materials().values().stream().filter(material ->
                            material.qualityPrediction() != null
                                    && material.qualityPrediction().status() == PredictionStatus.AVAILABLE
                                    && material.qualityPrediction().modelId().equals(model.modelId())).count())
                            .isEqualTo(23_631);
                    assertThat(v3Digest.stateHash()).isEqualTo(v2Digest.stateHash());
                    assertThat(v3Digest.alertHash()).isEqualTo(v2Digest.alertHash());
                });
    }

    private static void assertActualReplayUi(
            DesktopMonitorSession session, Consumer<MonitorUpdate> observe, int alertCount, boolean hasModel)
            throws Exception {
        AtomicReference<Runnable> pendingDrain = new AtomicReference<>();
        MonitorDashboardPanel panel = onEdt(() -> new MonitorDashboardPanel(
                session.metadata(), session, operation -> {
                    if (!pendingDrain.compareAndSet(null, operation)) {
                        throw new AssertionError("more than one EDT drain was scheduled");
                    }
                }));
        try (AutoCloseable ignored = session.addUpdateListener(update -> {
            observe.accept(update);
            panel.acceptUpdate(update);
        })) {
            replayToEnd(session);
            onEdt(() -> {
                pendingDrain.getAndSet(null).run();
                JTable overview = component(panel, "overview-table", JTable.class);
                JTable history = component(panel, "history-table", JTable.class);
                assertThat(overview.getRowCount()).isEqualTo(23_631);
                assertThat(component(panel, "evidence-table", JTable.class).getRowCount())
                        .isPositive()
                        .isLessThanOrEqualTo(MonitorDashboardPanel.MAX_RETAINED_EVIDENCE_ROWS);
                assertThat(history.getRowCount()).isEqualTo(alertCount);
                int timeColumn = history.getColumnModel().getColumnIndex("재생 시각");
                assertThat(IntStream.range(0, history.getRowCount())
                        .mapToObj(row -> (String) history.getValueAt(row, timeColumn)).toList())
                        .isSortedAccordingTo(Comparator.reverseOrder());
                if (hasModel) {
                    assertResearchPrediction(panel, overview, session);
                }
            });
        } finally {
            onEdt(panel::removeNotify);
        }
    }

    private static void replayToEnd(DesktopMonitorSession session) {
        session.setSpeed(ReplaySpeed.MAX);
        session.start();
        await().atMost(Duration.ofSeconds(90)).untilAsserted(() ->
                assertThat(session.state().status())
                        .withFailMessage("Replay state: %s", session.state())
                        .isEqualTo(ReplayStatus.COMPLETED));
    }

    private static void assertResearchPrediction(
            MonitorDashboardPanel panel, JTable overview, DesktopMonitorSession session) {
        var materials = session.snapshot().materials();
        int materialColumn = overview.getColumnModel().getColumnIndex("소재");
        int row = IntStream.range(0, overview.getRowCount())
                .filter(index -> materials.get(overview.getValueAt(index, materialColumn)).values().containsKey("judge"))
                .findFirst().orElseThrow();
        var material = materials.get(overview.getValueAt(row, materialColumn));
        String probability = String.format(Locale.ROOT, "%.2f%%", material.qualityPrediction().probability() * 100);
        assertThat(overview.getValueAt(row, overview.getColumnModel().getColumnIndex("예상 불량확률")))
                .isEqualTo(probability + " · 연구용");
        overview.setRowSelectionInterval(row, row);
        assertThat(component(panel, "overview-selection-detail", JLabel.class).getText())
                .contains(probability, "독립 평가 기준 미달 · 경보 미발행", "AP 실제 결과",
                        material.values().get("judge").toString());
    }

    private static <T extends Component> T component(Container root, String name, Class<T> type) {
        T result = findComponent(root, name, type);
        if (result == null) {
            throw new AssertionError("component not found: " + name);
        }
        return result;
    }

    private static <T extends Component> T findComponent(Container root, String name, Class<T> type) {
        for (Component child : root.getComponents()) {
            if (type.isInstance(child) && name.equals(child.getName())) return type.cast(child);
            if (child instanceof Container container) {
                T found = findComponent(container, name, type);
                if (found != null) return found;
            }
        }
        return null;
    }

    // Compare in the same JVM, where the iteration order of immutable maps is shared.
    private static final class ReplayDigest {
        private final MessageDigest state;
        private final MessageDigest alerts;
        private final String bundleId;

        ReplayDigest(String bundleId) throws Exception {
            this.bundleId = bundleId;
            this.state = MessageDigest.getInstance("SHA-256");
            this.alerts = MessageDigest.getInstance("SHA-256");
        }

        void accept(MonitorUpdate update) {
            add(state, update.unit().batchId(), update.unitsProcessed(), update.eventsProcessed());
            update.changedMaterials().entrySet().stream().sorted(Map.Entry.comparingByKey()).forEach(entry -> {
                String material = entry.getValue().toString();
                add(state, material.substring(0, material.lastIndexOf(", qualityPrediction=")));
            });
            add(state, update.changedEquipment());
            update.newAlerts().stream().filter(alert -> alert.kind() != AlertKind.MODEL_QUALITY_RISK)
                    .forEach(alert -> add(alerts, alert.toString().replace(bundleId, "<bundle>")));
        }

        String stateHash() {
            return HexFormat.of().formatHex(state.digest());
        }

        String alertHash() {
            return HexFormat.of().formatHex(alerts.digest());
        }

        private static void add(MessageDigest digest, Object... values) {
            for (Object value : values) {
                digest.update(value.toString().getBytes(StandardCharsets.UTF_8));
                digest.update((byte) 0);
            }
        }
    }

    private static void onEdt(ThrowingRunnable action) throws Exception {
        onEdt(() -> {
            action.run();
            return null;
        });
    }

    private static <T> T onEdt(ThrowingSupplier<T> action) throws Exception {
        if (SwingUtilities.isEventDispatchThread()) return action.get();
        AtomicReference<T> value = new AtomicReference<>();
        AtomicReference<Throwable> failure = new AtomicReference<>();
        SwingUtilities.invokeAndWait(() -> {
            try {
                value.set(action.get());
            } catch (Throwable error) {
                failure.set(error);
            }
        });
        if (failure.get() instanceof Exception exception) throw exception;
        if (failure.get() != null) throw new AssertionError(failure.get());
        return value.get();
    }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }

    @FunctionalInterface
    private interface ThrowingSupplier<T> {
        T get() throws Exception;
    }
}
