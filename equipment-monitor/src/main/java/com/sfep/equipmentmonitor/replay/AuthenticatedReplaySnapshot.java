package com.sfep.equipmentmonitor.replay;

import com.sfep.equipmentmonitor.bundle.BundleLoadException;
import com.sfep.equipmentmonitor.bundle.SafeFile;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.OpenOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.FileAttribute;
import java.nio.file.attribute.PosixFileAttributeView;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.HexFormat;
import java.util.Set;

final class AuthenticatedReplaySnapshot implements AutoCloseable {
    private static final String FILE_PREFIX = "sfep-replay-snapshot-";
    private static final int CREATE_ATTEMPTS = 32;
    private static final int BUFFER_SIZE = 64 * 1024;
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final Set<OpenOption> OPEN_OPTIONS = Set.of(
            StandardOpenOption.READ,
            StandardOpenOption.WRITE,
            StandardOpenOption.CREATE_NEW,
            StandardOpenOption.DELETE_ON_CLOSE,
            LinkOption.NOFOLLOW_LINKS);
    private static final FileAttribute<Set<PosixFilePermission>> OWNER_ONLY =
            PosixFilePermissions.asFileAttribute(Set.of(
                    PosixFilePermission.OWNER_READ,
                    PosixFilePermission.OWNER_WRITE));

    private final Path backingPath;
    private final FileChannel channel;
    private final SafeFile original;
    private final FileChannel originalChannel;

    private AuthenticatedReplaySnapshot(
            Path backingPath,
            FileChannel channel,
            SafeFile original,
            FileChannel originalChannel) {
        this.backingPath = backingPath;
        this.channel = channel;
        this.original = original;
        this.originalChannel = originalChannel;
    }

    static AuthenticatedReplaySnapshot capture(
            SafeFile original,
            long expectedSize,
            String expectedSha256) {
        return capture(
                original,
                expectedSize,
                expectedSha256,
                "REPLAY_CONSUMED_SIZE_MISMATCH",
                "REPLAY_CONSUMED_HASH_MISMATCH");
    }

    static AuthenticatedReplaySnapshot capture(
            SafeFile original,
            long expectedSize,
            String expectedSha256,
            String sizeMismatchCode,
            String hashMismatchCode) {
        OpenSnapshot opened = openSnapshot();
        FileChannel originalChannel = null;
        try {
            original.assertUnchanged("REPLAY_FILE_CHANGED");
            originalChannel = openOriginal(original);
            original.assertUnchanged("REPLAY_FILE_CHANGED");
            MessageDigest digest = sha256();
            long consumed = copy(original, originalChannel, opened.channel(), digest, sizeMismatchCode);
            original.assertUnchanged("REPLAY_FILE_CHANGED");
            if (consumed != expectedSize) {
                throw new BundleLoadException(
                        sizeMismatchCode,
                        "expected " + expectedSize + " but consumed " + consumed);
            }
            String actualSha256 = digestUri(digest);
            if (!actualSha256.equals(expectedSha256)) {
                throw new BundleLoadException(
                        hashMismatchCode,
                        "expected " + expectedSha256 + " but consumed " + actualSha256);
            }
            opened.channel().position(0);
            originalChannel.position(0);
            return new AuthenticatedReplaySnapshot(
                    opened.path(), opened.channel(), original, originalChannel);
        } catch (IOException error) {
            BundleLoadException failure = new BundleLoadException(
                    "REPLAY_SNAPSHOT_FAILED", original.path().toString(), error);
            closeAfterFailure(opened.channel(), originalChannel, failure);
            throw failure;
        } catch (RuntimeException | Error error) {
            closeAfterFailure(opened.channel(), originalChannel, error);
            throw error;
        }
    }

    Path backingPath() {
        return backingPath;
    }

    FileChannel channel() {
        return channel;
    }

    void rewind() {
        try {
            channel.position(0);
        } catch (IOException error) {
            throw new BundleLoadException("REPLAY_SNAPSHOT_FAILED", backingPath.toString(), error);
        }
    }

    void verifyOriginal(long expectedSize, String expectedSha256) {
        try {
            originalChannel.position(0);
            MessageDigest digest = sha256();
            long consumed = digest(originalChannel, digest, "REPLAY_CONSUMED_SIZE_MISMATCH");
            if (consumed != expectedSize) {
                throw new BundleLoadException(
                        "REPLAY_CONSUMED_SIZE_MISMATCH",
                        "expected " + expectedSize + " but consumed " + consumed);
            }
            String actualSha256 = digestUri(digest);
            if (!actualSha256.equals(expectedSha256)) {
                throw new BundleLoadException(
                        "REPLAY_CONSUMED_HASH_MISMATCH",
                        "expected " + expectedSha256 + " but consumed " + actualSha256);
            }
        } catch (BundleLoadException error) {
            throw error;
        } catch (IOException error) {
            throw new BundleLoadException("REPLAY_CSV_INVALID", original.path().toString(), error);
        }
    }

    void assertOriginalUnchanged() {
        original.assertUnchanged("REPLAY_FILE_CHANGED");
    }

    @Override
    public void close() {
        IOException failure = null;
        try {
            channel.close();
        } catch (IOException error) {
            failure = error;
        }
        try {
            originalChannel.close();
        } catch (IOException error) {
            if (failure == null) {
                failure = error;
            } else {
                failure.addSuppressed(error);
            }
        }
        if (failure != null) {
            throw new BundleLoadException(
                    "REPLAY_SNAPSHOT_CLEANUP_FAILED", backingPath.toString(), failure);
        }
    }

    private static long copy(
            SafeFile original,
            FileChannel source,
            FileChannel destination,
            MessageDigest digest,
            String sizeMismatchCode) {
        try {
            ByteBuffer buffer = ByteBuffer.allocate(BUFFER_SIZE);
            long consumed = 0;
            while (true) {
                int count = source.read(buffer);
                if (count < 0) break;
                if (count == 0) continue;
                consumed = addCount(consumed, count, sizeMismatchCode);
                buffer.flip();
                digest.update(buffer.asReadOnlyBuffer());
                while (buffer.hasRemaining()) destination.write(buffer);
                buffer.clear();
            }
            destination.force(false);
            return consumed;
        } catch (BundleLoadException error) {
            throw error;
        } catch (IOException error) {
            throw new BundleLoadException("REPLAY_READ_FAILED", original.path().toString(), error);
        }
    }

    private static long digest(
            FileChannel source,
            MessageDigest digest,
            String sizeMismatchCode) throws IOException {
        ByteBuffer buffer = ByteBuffer.allocate(BUFFER_SIZE);
        long consumed = 0;
        while (true) {
            int count = source.read(buffer);
            if (count < 0) return consumed;
            if (count == 0) continue;
            consumed = addCount(consumed, count, sizeMismatchCode);
            buffer.flip();
            digest.update(buffer);
            buffer.clear();
        }
    }

    private static long addCount(long consumed, int count, String sizeMismatchCode) {
        try {
            return Math.addExact(consumed, count);
        } catch (ArithmeticException error) {
            throw new BundleLoadException(sizeMismatchCode, "consumed size overflow", error);
        }
    }

    private static FileChannel openOriginal(SafeFile original) {
        try {
            return FileChannel.open(
                    original.path(),
                    Set.<OpenOption>of(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS));
        } catch (IOException error) {
            throw new BundleLoadException("REPLAY_READ_FAILED", original.path().toString(), error);
        }
    }

    private static OpenSnapshot openSnapshot() {
        Path directory;
        try {
            String configured = System.getProperty("java.io.tmpdir");
            if (configured == null || configured.isEmpty()) {
                throw new IOException("java.io.tmpdir is not configured");
            }
            directory = Path.of(configured).toAbsolutePath().normalize().toRealPath();
            if (!Files.isDirectory(directory, LinkOption.NOFOLLOW_LINKS)) {
                throw new IOException("temporary root is not a directory");
            }
        } catch (IOException | RuntimeException error) {
            throw new BundleLoadException("REPLAY_SNAPSHOT_FAILED", "temporary root", error);
        }

        for (int attempt = 0; attempt < CREATE_ATTEMPTS; attempt++) {
            Path candidate = directory.resolve(FILE_PREFIX + randomSuffix() + ".tmp");
            try {
                boolean posixPermissionsSupported = Files.getFileAttributeView(
                        directory,
                        PosixFileAttributeView.class,
                        LinkOption.NOFOLLOW_LINKS) != null;
                return new OpenSnapshot(
                        candidate,
                        openChannel(candidate, posixPermissionsSupported));
            } catch (FileAlreadyExistsException collision) {
                // An unpredictable CREATE_NEW collision is safe to retry with a fresh name.
            } catch (IOException | RuntimeException error) {
                throw new BundleLoadException(
                        "REPLAY_SNAPSHOT_FAILED", candidate.toString(), error);
            }
        }
        throw new BundleLoadException(
                "REPLAY_SNAPSHOT_FAILED", "could not create an unpredictable temporary snapshot");
    }

    private static FileChannel openChannel(
            Path candidate,
            boolean posixPermissionsSupported) throws IOException {
        if (posixPermissionsSupported) {
            return FileChannel.open(candidate, OPEN_OPTIONS, OWNER_ONLY);
        }
        return FileChannel.open(candidate, OPEN_OPTIONS);
    }

    private static String randomSuffix() {
        byte[] random = new byte[24];
        RANDOM.nextBytes(random);
        return HexFormat.of().formatHex(random);
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 unavailable", impossible);
        }
    }

    private static String digestUri(MessageDigest digest) {
        return "sha256:" + HexFormat.of().formatHex(digest.digest());
    }

    private static void closeAfterFailure(
            FileChannel channel,
            FileChannel originalChannel,
            Throwable failure) {
        try {
            channel.close();
        } catch (IOException cleanupError) {
            failure.addSuppressed(cleanupError);
        }
        if (originalChannel != null) {
            try {
                originalChannel.close();
            } catch (IOException cleanupError) {
                failure.addSuppressed(cleanupError);
            }
        }
    }

    private record OpenSnapshot(Path path, FileChannel channel) {
    }
}
