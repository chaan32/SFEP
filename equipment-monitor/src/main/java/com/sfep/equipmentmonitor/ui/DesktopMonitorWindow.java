package com.sfep.equipmentmonitor.ui;

import com.sfep.equipmentmonitor.state.MonitorUpdate;

import javax.swing.JFrame;
import javax.swing.SwingUtilities;
import javax.swing.WindowConstants;
import java.awt.Dimension;
import java.awt.event.WindowAdapter;
import java.awt.event.WindowEvent;
import java.lang.reflect.InvocationTargetException;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import java.util.function.Function;

/** Owns only the native window shell; dashboard behavior stays in the testable panel. */
public final class DesktopMonitorWindow implements AutoCloseable {
    interface Host {
        void show(MonitorDashboardPanel dashboard, Runnable closeRequest);

        void close();
    }

    private final MonitorDashboardMetadata metadata;
    private final ReplayControl replayControl;
    private final Function<Consumer<MonitorUpdate>, AutoCloseable> listenerRegistration;
    private final Runnable shutdownApplication;
    private final Host host;
    private final AtomicBoolean shutdownRequested = new AtomicBoolean();

    private AutoCloseable listenerSubscription;
    private boolean shown;
    private boolean closed;

    public DesktopMonitorWindow(
            MonitorDashboardMetadata metadata,
            ReplayControl replayControl,
            Function<Consumer<MonitorUpdate>, AutoCloseable> listenerRegistration,
            Runnable shutdownApplication) {
        this(metadata, replayControl, listenerRegistration, shutdownApplication, new SwingHost());
    }

    DesktopMonitorWindow(
            MonitorDashboardMetadata metadata,
            ReplayControl replayControl,
            Function<Consumer<MonitorUpdate>, AutoCloseable> listenerRegistration,
            Runnable shutdownApplication,
            Host host) {
        this.metadata = Objects.requireNonNull(metadata, "metadata");
        this.replayControl = Objects.requireNonNull(replayControl, "replayControl");
        this.listenerRegistration = Objects.requireNonNull(listenerRegistration, "listenerRegistration");
        this.shutdownApplication = Objects.requireNonNull(shutdownApplication, "shutdownApplication");
        this.host = Objects.requireNonNull(host, "host");
    }

    public synchronized void show() {
        if (closed) throw new IllegalStateException("desktop window is closed");
        if (shown) return;
        runOnEdtAndWait(() -> {
            MonitorDashboardPanel dashboard = new MonitorDashboardPanel(metadata, replayControl);
            listenerSubscription = listenerRegistration.apply(dashboard::acceptUpdate);
            host.show(dashboard, this::requestShutdown);
        });
        shown = true;
    }

    private void requestShutdown() {
        if (!shutdownRequested.compareAndSet(false, true)) return;
        Thread shutdown = new Thread(shutdownApplication, "sfep-monitor-shutdown");
        shutdown.setDaemon(false);
        shutdown.start();
    }

    @Override
    public synchronized void close() {
        if (closed) return;
        closed = true;
        if (listenerSubscription != null) {
            try {
                listenerSubscription.close();
            } catch (Exception error) {
                throw new IllegalStateException("failed to remove monitor update listener", error);
            } finally {
                listenerSubscription = null;
            }
        }
        runOnEdtAndWait(host::close);
    }

    private static void runOnEdtAndWait(Runnable action) {
        if (SwingUtilities.isEventDispatchThread()) {
            action.run();
            return;
        }
        try {
            SwingUtilities.invokeAndWait(action);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("interrupted while updating desktop window", error);
        } catch (InvocationTargetException error) {
            Throwable cause = error.getCause();
            if (cause instanceof RuntimeException runtime) throw runtime;
            if (cause instanceof Error fatal) throw fatal;
            throw new IllegalStateException("desktop window operation failed", cause);
        }
    }

    private static final class SwingHost implements Host {
        private JFrame frame;

        @Override
        public void show(MonitorDashboardPanel dashboard, Runnable closeRequest) {
            frame = new JFrame("SFEP 설비·품질 과거 데이터 모니터");
            frame.setDefaultCloseOperation(WindowConstants.DO_NOTHING_ON_CLOSE);
            frame.addWindowListener(new WindowAdapter() {
                @Override
                public void windowClosing(WindowEvent event) {
                    closeRequest.run();
                }
            });
            frame.setContentPane(dashboard);
            frame.setMinimumSize(new Dimension(1100, 700));
            frame.setSize(1440, 900);
            frame.setLocationRelativeTo(null);
            frame.setVisible(true);
        }

        @Override
        public void close() {
            if (frame != null) {
                frame.dispose();
                frame = null;
            }
        }
    }
}
