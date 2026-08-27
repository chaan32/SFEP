package com.sfep.equipmentmonitor.bootstrap;

import java.awt.GraphicsEnvironment;

public record RuntimeProbe(int javaFeature, boolean desktopModulePresent, boolean headless) {
    public static RuntimeProbe system() {
        return new RuntimeProbe(
                Runtime.version().feature(),
                ModuleLayer.boot().findModule("java.desktop").isPresent(),
                GraphicsEnvironment.isHeadless());
    }
}
