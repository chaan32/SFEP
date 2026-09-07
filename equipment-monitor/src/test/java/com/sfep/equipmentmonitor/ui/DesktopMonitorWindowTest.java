package com.sfep.equipmentmonitor.ui;

import com.sfep.equipmentmonitor.replay.ReplayControllerState;
import com.sfep.equipmentmonitor.replay.ReplaySpeed;
import com.sfep.equipmentmonitor.replay.ReplayStatus;
import com.sfep.equipmentmonitor.replay.ReplayUnit;
import com.sfep.equipmentmonitor.state.MonitorUpdate;
import org.junit.jupiter.api.Test;

import javax.swing.SwingUtilities;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

class DesktopMonitorWindowTest {
    @Test
    void createsDashboardAndHostOnEdtThenClosesSubscriptionAndRequestsApplicationShutdown() {
        RecordingHost host = new RecordingHost();
        AtomicReference<Consumer<MonitorUpdate>> listener = new AtomicReference<>();
        AtomicBoolean subscriptionClosed = new AtomicBoolean();
        AtomicInteger shutdowns = new AtomicInteger();
        AtomicReference<String> shutdownThread = new AtomicReference<>();
        ReplayControl control = idleControl();
        DesktopMonitorWindow window = new DesktopMonitorWindow(
                metadata(),
                control,
                updateListener -> {
                    listener.set(updateListener);
                    return () -> subscriptionClosed.set(true);
                },
                () -> {
                    shutdownThread.set(Thread.currentThread().getName());
                    shutdowns.incrementAndGet();
                },
                host);

        window.show();

        assertThat(host.panel.get()).isInstanceOf(MonitorDashboardPanel.class);
        assertThat(listener.get()).isNotNull();
        assertThat(host.showOnEdt.get()).isTrue();

        host.requestClose.get().run();
        await().atMost(Duration.ofSeconds(2)).untilAsserted(() ->
                assertThat(shutdowns).hasValue(1));
        assertThat(shutdownThread.get()).isEqualTo("sfep-monitor-shutdown");

        window.close();
        assertThat(subscriptionClosed).isTrue();
        assertThat(host.closeOnEdt.get()).isTrue();
    }

    private static MonitorDashboardMetadata metadata() {
        return new MonitorDashboardMetadata(
                "sha256:" + "a".repeat(64), "sha256:" + "b".repeat(64),
                "2025-04-01", "HISTORICAL_REPLAY", "2025-01-01", "2025-03-31",
                10, 100, 20, 30);
    }

    private static ReplayControl idleControl() {
        return new ReplayControl() {
            @Override public void start() { }
            @Override public void pause() { }
            @Override public void resume() { }
            @Override public void step() { }
            @Override public void stop() { }
            @Override public void advanceToNextDate() { }
            @Override public void setSpeed(ReplaySpeed speed) { }
            @Override public void reportUiFailure(ReplayUnit unit, Throwable error) { }
            @Override public ReplayControllerState state() {
                return new ReplayControllerState(
                        ReplayStatus.STOPPED, true, ReplaySpeed.X1, 0, null, null);
            }
        };
    }

    private static final class RecordingHost implements DesktopMonitorWindow.Host {
        private final AtomicReference<MonitorDashboardPanel> panel = new AtomicReference<>();
        private final AtomicReference<Runnable> requestClose = new AtomicReference<>();
        private final AtomicBoolean showOnEdt = new AtomicBoolean();
        private final AtomicBoolean closeOnEdt = new AtomicBoolean();

        @Override
        public void show(MonitorDashboardPanel dashboard, Runnable closeRequest) {
            panel.set(dashboard);
            requestClose.set(closeRequest);
            showOnEdt.set(SwingUtilities.isEventDispatchThread());
        }

        @Override
        public void close() {
            closeOnEdt.set(SwingUtilities.isEventDispatchThread());
        }
    }
}
