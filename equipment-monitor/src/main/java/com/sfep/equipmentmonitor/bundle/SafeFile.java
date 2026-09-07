package com.sfep.equipmentmonitor.bundle;

import java.io.IOException;
import java.io.InputStream;
import java.nio.channels.Channels;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.OpenOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.nio.file.attribute.FileTime;
import java.util.Objects;
import java.util.Set;

public record SafeFile(
        Path path,
        Object fileKey,
        long size,
        FileTime modifiedTime,
        Path parentPath,
        Object parentFileKey) {
    static SafeFile capture(Path path, Path expectedParent, Object expectedParentKey, String code) {
        try {
            assertParent(expectedParent, expectedParentKey, code);
            BasicFileAttributes attributes = Files.readAttributes(
                    path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
            if (attributes.isSymbolicLink() || !attributes.isRegularFile() || attributes.fileKey() == null) {
                throw new BundleLoadException(code, path + " is not an identified regular file");
            }
            Path real = path.toRealPath(LinkOption.NOFOLLOW_LINKS);
            if (!real.equals(path.toAbsolutePath().normalize())) {
                throw new BundleLoadException(code, path + " does not resolve to itself");
            }
            if (!real.getParent().equals(expectedParent)) {
                throw new BundleLoadException(code, path + " is not a direct child of the retained parent");
            }
            assertParent(expectedParent, expectedParentKey, code);
            return new SafeFile(
                    real,
                    attributes.fileKey(),
                    attributes.size(),
                    attributes.lastModifiedTime(),
                    expectedParent,
                    expectedParentKey);
        } catch (IOException error) {
            throw new BundleLoadException(code, path.toString(), error);
        }
    }

    public static SafeFile captureStandalone(Path path) {
        Path absolute = path.toAbsolutePath().normalize();
        BundleLayout.rejectSymlinkComponents(absolute, "BUNDLE_CHILD_UNSAFE");
        Path parent = absolute.getParent();
        if (parent == null) {
            throw new BundleLoadException("BUNDLE_CHILD_UNSAFE", "file has no parent");
        }
        try {
            BasicFileAttributes parentAttributes = Files.readAttributes(
                    parent, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
            if (!parentAttributes.isDirectory() || parentAttributes.fileKey() == null) {
                throw new BundleLoadException("BUNDLE_CHILD_UNSAFE", parent + " is not an identified directory");
            }
            return capture(absolute, parent, parentAttributes.fileKey(), "BUNDLE_CHILD_UNSAFE");
        } catch (IOException error) {
            throw new BundleLoadException("BUNDLE_CHILD_UNSAFE", parent.toString(), error);
        }
    }

    public byte[] readAllBytes(String code) {
        assertUnchanged(code);
        try (FileChannel channel = FileChannel.open(
                path, Set.<OpenOption>of(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS));
             InputStream input = Channels.newInputStream(channel)) {
            byte[] bytes = input.readAllBytes();
            assertUnchanged(code);
            return bytes;
        } catch (IOException error) {
            throw new BundleLoadException(code, path.toString(), error);
        }
    }

    public InputStream openInputStream(String code) {
        assertUnchanged(code);
        try {
            return Files.newInputStream(path, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS);
        } catch (IOException error) {
            throw new BundleLoadException(code, path.toString(), error);
        }
    }

    public void assertUnchanged(String code) {
        try {
            assertParent(parentPath, parentFileKey, code);
            BasicFileAttributes current = Files.readAttributes(
                    path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
            if (current.isSymbolicLink()
                    || !current.isRegularFile()
                    || !Objects.equals(fileKey, current.fileKey())
                    || size != current.size()
                    || !modifiedTime.equals(current.lastModifiedTime())) {
                throw new BundleLoadException(code, path + " changed after path preflight");
            }
            assertParent(parentPath, parentFileKey, code);
        } catch (IOException error) {
            throw new BundleLoadException(code, path.toString(), error);
        }
    }

    private static void assertParent(Path parent, Object expectedKey, String code) throws IOException {
        BasicFileAttributes current = Files.readAttributes(
                parent, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
        if (current.isSymbolicLink()
                || !current.isDirectory()
                || !Objects.equals(expectedKey, current.fileKey())
                || !parent.toRealPath(LinkOption.NOFOLLOW_LINKS).equals(parent)) {
            throw new BundleLoadException(code, parent + " changed after path preflight");
        }
    }
}
