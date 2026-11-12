package com.sfep.equipmentmonitor.bundle;

import java.io.IOException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

public final class BundleLayout {
    public static final List<String> REQUIRED_FILES = requiredFiles(ArtifactRole.LEGACY_ROLES);
    private static final List<String> V3_REQUIRED_FILES = requiredFiles(ArtifactRole.ALL_ROLES);

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
            List<String> required;
            if (actual.equals(Set.copyOf(REQUIRED_FILES))) {
                required = REQUIRED_FILES;
            } else if (actual.equals(Set.copyOf(V3_REQUIRED_FILES))) {
                required = V3_REQUIRED_FILES;
            } else {
                throw new BundleLoadException(
                        "BUNDLE_INVENTORY_MISMATCH",
                        "inventory does not match a supported profile: " + actual);
            }

            LinkedHashMap<String, SafeFile> files = new LinkedHashMap<>();
            for (String name : required) {
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

    static void requireContract(SafeBundleLayout layout, BundleContract contract) {
        if (!layout.files().keySet().equals(Set.copyOf(requiredFiles(contract.artifactRoles())))) {
            throw new BundleLoadException("BUNDLE_INVENTORY_MISMATCH", contract.manifestVersion());
        }
    }

    private static List<String> requiredFiles(List<ArtifactRole> roles) {
        List<String> names = new ArrayList<>();
        names.add("bundle_manifest.json");
        roles.forEach(role -> names.add(role.fileName()));
        return List.copyOf(names);
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
