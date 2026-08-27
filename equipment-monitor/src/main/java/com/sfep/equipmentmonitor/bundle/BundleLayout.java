package com.sfep.equipmentmonitor.bundle;

import java.io.IOException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

public final class BundleLayout {
    public static final List<String> REQUIRED_FILES = List.of(
            "bundle_manifest.json",
            "analysis_config.json",
            "producer_runtime.json",
            "equipment_operating_ranges.json",
            "quality_risk_intervals.json",
            "replay_events.csv",
            "analysis_summary.json");

    private BundleLayout() {
    }

    public static SafeBundleLayout preflight(Path suppliedRoot) {
        if (suppliedRoot == null || !suppliedRoot.isAbsolute()) {
            throw new BundleLoadException("BUNDLE_ROOT_UNSAFE", "bundle root must be absolute");
        }
        for (Path component : suppliedRoot) {
            if (component.toString().equals("..")) {
                throw new BundleLoadException("BUNDLE_ROOT_UNSAFE", "bundle root contains traversal");
            }
        }
        Path root = suppliedRoot.normalize();
        rejectSymlinkComponents(root, "BUNDLE_ROOT_UNSAFE");
        try {
            BasicFileAttributes attributes = Files.readAttributes(
                    root, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
            if (attributes.isSymbolicLink() || !attributes.isDirectory() || attributes.fileKey() == null) {
                throw new BundleLoadException("BUNDLE_ROOT_UNSAFE", root + " is not an identified directory");
            }
            Path realRoot = root.toRealPath(LinkOption.NOFOLLOW_LINKS);
            if (!realRoot.equals(root)) {
                throw new BundleLoadException("BUNDLE_ROOT_UNSAFE", root + " does not resolve to itself");
            }

            Set<String> actual = new LinkedHashSet<>();
            try (DirectoryStream<Path> children = Files.newDirectoryStream(root)) {
                for (Path child : children) {
                    actual.add(child.getFileName().toString());
                }
            }
            Set<String> required = new LinkedHashSet<>(REQUIRED_FILES);
            if (!actual.equals(required)) {
                throw new BundleLoadException(
                        "BUNDLE_INVENTORY_MISMATCH",
                        "expected " + required + " but found " + actual);
            }

            LinkedHashMap<String, SafeFile> files = new LinkedHashMap<>();
            for (String name : REQUIRED_FILES) {
                Path child = root.resolve(name).normalize();
                if (!child.getParent().equals(root)) {
                    throw new BundleLoadException("BUNDLE_CHILD_UNSAFE", name + " escapes bundle root");
                }
                files.put(name, SafeFile.capture(
                        child,
                        realRoot,
                        attributes.fileKey(),
                        "BUNDLE_CHILD_UNSAFE"));
            }

            BasicFileAttributes finalRoot = Files.readAttributes(
                    root, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
            if (!attributes.fileKey().equals(finalRoot.fileKey()) || !finalRoot.isDirectory()) {
                throw new BundleLoadException("BUNDLE_ROOT_UNSAFE", "bundle root changed during preflight");
            }
            return new SafeBundleLayout(realRoot, files);
        } catch (BundleLoadException error) {
            throw error;
        } catch (IOException error) {
            throw new BundleLoadException("BUNDLE_ROOT_UNSAFE", root.toString(), error);
        }
    }

    static void rejectSymlinkComponents(Path absolute, String code) {
        Path current = absolute.getRoot();
        if (current == null) {
            throw new BundleLoadException(code, "path is not absolute");
        }
        for (Path component : absolute) {
            current = current.resolve(component);
            if (Files.isSymbolicLink(current)) {
                throw new BundleLoadException(code, current + " is a symbolic link");
            }
        }
    }
}
