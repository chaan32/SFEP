package com.sfep.equipmentmonitor.bundle;

import java.nio.file.Path;
import java.util.Map;

public record SafeBundleLayout(Path root, Map<String, SafeFile> files) {
    public SafeBundleLayout {
        files = Map.copyOf(files);
    }

    public SafeFile file(String name) {
        SafeFile file = files.get(name);
        if (file == null) {
            throw new BundleLoadException("BUNDLE_INVENTORY_MISMATCH", "missing " + name);
        }
        return file;
    }
}
