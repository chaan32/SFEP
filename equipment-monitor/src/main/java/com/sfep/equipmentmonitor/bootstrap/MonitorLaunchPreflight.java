package com.sfep.equipmentmonitor.bootstrap;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

public final class MonitorLaunchPreflight {
    public static final String BUNDLE_OPTION = "--sfep.equipment-monitor.bundle-dir=";

    private MonitorLaunchPreflight() {
    }

    public static Path validate(String[] args, RuntimeProbe runtime) {
        if (runtime.javaFeature() != 21) {
            throw new MonitorLaunchException("JAVA_21_REQUIRED", "Java 21 is required");
        }
        if (!runtime.desktopModulePresent()) {
            throw new MonitorLaunchException("JAVA_DESKTOP_REQUIRED", "java.desktop is required");
        }
        if (runtime.headless()) {
            throw new MonitorLaunchException("HEADLESS_ENVIRONMENT", "a graphical desktop session is required");
        }

        List<String> values = new ArrayList<>();
        for (String arg : args) {
            if (arg != null && arg.startsWith(BUNDLE_OPTION)) {
                values.add(arg.substring(BUNDLE_OPTION.length()));
            }
        }
        if (values.isEmpty() || values.getFirst().isBlank()) {
            throw new MonitorLaunchException("BUNDLE_DIR_REQUIRED", BUNDLE_OPTION + " must be supplied");
        }
        if (values.size() != 1) {
            throw new MonitorLaunchException("BUNDLE_DIR_DUPLICATE", "bundle directory must be supplied once");
        }
        Path bundle = Path.of(values.getFirst());
        if (!bundle.isAbsolute()) {
            throw new MonitorLaunchException("BUNDLE_DIR_NOT_ABSOLUTE", "bundle directory must be absolute");
        }
        return bundle.normalize();
    }

    public static Path validateSystem(String[] args) {
        return validate(args, RuntimeProbe.system());
    }
}
