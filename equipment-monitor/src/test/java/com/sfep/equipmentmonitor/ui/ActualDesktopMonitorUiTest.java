package com.sfep.equipmentmonitor.ui;

import com.sfep.equipmentmonitor.bundle.BundleLoader;
import com.sfep.equipmentmonitor.bundle.LoadedBundle;
import com.sfep.equipmentmonitor.replay.ReplaySpeed;
import com.sfep.equipmentmonitor.replay.ReplayStatus;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Tag;

import javax.swing.JTable;
import javax.swing.SwingUtilities;
import java.awt.Component;
import java.awt.Container;
import java.nio.file.Path;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

@Tag("actual-bundle-v1")
class ActualDesktopMonitorUiTest {
    @Test
    void coalescesTheCompleteActualReplayWithoutLosingOverviewEvidenceOrAlerts() throws Exception {
        String configured = System.getProperty("sfep.actual-bundle", System.getenv("SFEP_ACTUAL_BUNDLE"));
        assumeTrue(configured != null && !configured.isBlank(), "local-only actual bundle not configured");
        LoadedBundle bundle = new BundleLoader().load(Path.of(configured));
        AtomicReference<Runnable> pendingDrain = new AtomicReference<>();

        try (DesktopMonitorSession session = new DesktopMonitorSession(bundle)) {
            MonitorDashboardPanel panel = onEdt(() -> new MonitorDashboardPanel(
                    session.metadata(), session, operation -> {
                        if (!pendingDrain.compareAndSet(null, operation)) {
                            throw new AssertionError("more than one EDT drain was scheduled");
                        }
                    }));
            try (AutoCloseable ignored = session.addUpdateListener(panel::acceptUpdate)) {
                session.setSpeed(ReplaySpeed.MAX);
                session.start();
                await().atMost(Duration.ofSeconds(60)).untilAsserted(() ->
                        assertThat(session.state().status()).isEqualTo(ReplayStatus.COMPLETED));

                onEdt(() -> pendingDrain.getAndSet(null).run());

                assertThat(onEdt(() -> table(panel, "overview-table").getRowCount()))
                        .isEqualTo(23_631);
                assertThat(onEdt(() -> table(panel, "evidence-table").getRowCount()))
                        .isPositive()
                        .isLessThanOrEqualTo(MonitorDashboardPanel.MAX_RETAINED_EVIDENCE_ROWS);
                assertThat(onEdt(() -> table(panel, "history-table").getRowCount()))
                        .isEqualTo(54_515);
                onEdt(panel::removeNotify);
            }
        }
    }

    private static JTable table(Container root, String name) {
        if (root instanceof JTable table && name.equals(table.getName())) return table;
        for (Component child : root.getComponents()) {
            if (child instanceof JTable table && name.equals(table.getName())) return table;
            if (child instanceof Container container) {
                JTable found = tableOrNull(container, name);
                if (found != null) return found;
            }
        }
        throw new AssertionError("table not found: " + name);
    }

    private static JTable tableOrNull(Container root, String name) {
        for (Component child : root.getComponents()) {
            if (child instanceof JTable table && name.equals(table.getName())) return table;
            if (child instanceof Container container) {
                JTable found = tableOrNull(container, name);
                if (found != null) return found;
            }
        }
        return null;
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
