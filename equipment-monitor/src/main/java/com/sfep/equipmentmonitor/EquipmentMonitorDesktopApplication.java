package com.sfep.equipmentmonitor;

import com.sfep.equipmentmonitor.bootstrap.MonitorLaunchPreflight;
import com.sfep.equipmentmonitor.bundle.BundleLoader;
import com.sfep.equipmentmonitor.bundle.LoadedBundle;
import com.sfep.equipmentmonitor.prediction.OnnxQualityPredictor;
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
    DesktopMonitorSession desktopMonitorSession(
            LoadedBundle bundle,
            @Value("${sfep.quality-model:#{null}}") String modelPath,
            @Value("${sfep.quality-model-metadata:#{null}}") String metadataPath) {
        // shortcut: development-only until Bundle v3; remove in Task 10
        if (modelPath == null && metadataPath == null) {
            return new DesktopMonitorSession(bundle);
        }
        if (modelPath == null || modelPath.isBlank() || metadataPath == null || metadataPath.isBlank()) {
            throw new IllegalArgumentException(
                    "QUALITY_MODEL_PATHS_REQUIRED: configure both sfep.quality-model and sfep.quality-model-metadata");
        }
        OnnxQualityPredictor predictor = OnnxQualityPredictor.open(Path.of(modelPath), Path.of(metadataPath));
        try {
            return new DesktopMonitorSession(bundle, predictor);
        } catch (RuntimeException | Error failure) {
            try {
                predictor.close();
            } catch (RuntimeException closeFailure) {
                failure.addSuppressed(closeFailure);
            }
            throw failure;
        }
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
