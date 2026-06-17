package com.sfep.sfep_server.desktop;

import com.sfep.sfep_server.alert.service.AlertEventBus;
import com.sfep.sfep_server.dashboard.service.DashboardService;
import com.sfep.sfep_server.event.service.SensorEventService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import javax.swing.SwingUtilities;
import java.awt.GraphicsEnvironment;
import java.util.Locale;

@Component
@ConditionalOnProperty(prefix = "sfep.desktop", name = "enabled", havingValue = "true", matchIfMissing = true)
public class SfepDesktopRunner implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(SfepDesktopRunner.class);

    private final SensorEventService sensorEventService;
    private final DashboardService dashboardService;
    private final AlertEventBus alertEventBus;
    private final int dashboardRefreshMs;

    public SfepDesktopRunner(
            SensorEventService sensorEventService,
            DashboardService dashboardService,
            AlertEventBus alertEventBus,
            @Value("${sfep.desktop.dashboard-refresh-ms:1000}") int dashboardRefreshMs
    ) {
        this.sensorEventService = sensorEventService;
        this.dashboardService = dashboardService;
        this.alertEventBus = alertEventBus;
        this.dashboardRefreshMs = dashboardRefreshMs;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (isLinuxWithoutDisplay()) {
            log.info("SFEP desktop monitor is skipped because no Linux display server was detected.");
            return;
        }
        if (GraphicsEnvironment.isHeadless()) {
            log.info("SFEP desktop monitor is skipped because the environment is headless.");
            return;
        }

        SwingUtilities.invokeLater(() -> {
            try {
                SfepDesktopFrame frame = new SfepDesktopFrame(
                        sensorEventService,
                        dashboardService,
                        dashboardRefreshMs
                );
                frame.setVisible(true);

                SfepAlertFrame alertFrame = new SfepAlertFrame(alertEventBus);
                alertFrame.setLocation(frame.getX() + 40, frame.getY() + 40);
                alertFrame.setVisible(true);
            } catch (RuntimeException ex) {
                log.warn("Failed to start SFEP desktop monitor.", ex);
            }
        });
    }

    private boolean isLinuxWithoutDisplay() {
        String osName = System.getProperty("os.name", "").toLowerCase(Locale.ROOT);
        return osName.contains("linux")
                && System.getenv("DISPLAY") == null
                && System.getenv("WAYLAND_DISPLAY") == null;
    }
}
