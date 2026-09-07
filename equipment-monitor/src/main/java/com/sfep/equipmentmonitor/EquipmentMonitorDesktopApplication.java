package com.sfep.equipmentmonitor;

import com.sfep.equipmentmonitor.bootstrap.MonitorLaunchPreflight;
import com.sfep.equipmentmonitor.bundle.BundleLoader;
import com.sfep.equipmentmonitor.bundle.LoadedBundle;
import com.sfep.equipmentmonitor.ui.DesktopMonitorSession;
import com.sfep.equipmentmonitor.ui.DesktopMonitorWindow;
import org.springframework.boot.ApplicationRunner;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.ConfigurableApplicationContext;

import java.nio.file.Path;

@SpringBootApplication
public class EquipmentMonitorDesktopApplication {
    public static void main(String[] args) {
        MonitorLaunchPreflight.validateSystem(args);
        application().run(args);
    }

    public static SpringApplication application() {
        return new SpringApplicationBuilder(EquipmentMonitorDesktopApplication.class)
                .headless(false)
                .web(WebApplicationType.NONE)
                .build();
    }

    @Bean
    BundleLoader bundleLoader() {
        return new BundleLoader();
    }

    @Bean
    LoadedBundle loadedBundle(
            BundleLoader loader,
            @Value("${sfep.equipment-monitor.bundle-dir}") String bundleDirectory) {
        Path path = Path.of(bundleDirectory);
        if (!path.isAbsolute()) {
            throw new IllegalArgumentException("BUNDLE_DIR_NOT_ABSOLUTE: bundle directory must be absolute");
        }
        return loader.load(path);
    }

    @Bean(destroyMethod = "close")
    DesktopMonitorSession desktopMonitorSession(LoadedBundle bundle) {
        return new DesktopMonitorSession(bundle);
    }

    @Bean(destroyMethod = "close")
    DesktopMonitorWindow desktopMonitorWindow(
            DesktopMonitorSession session,
            ConfigurableApplicationContext context) {
        return new DesktopMonitorWindow(
                session.metadata(),
                session,
                session::addUpdateListener,
                context::close);
    }

    @Bean
    ApplicationRunner showDesktopMonitor(DesktopMonitorWindow window) {
        return ignored -> window.show();
    }
}
