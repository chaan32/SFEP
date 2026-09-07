package com.sfep.equipmentmonitor.bundle;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

public final class Digests {
    private Digests() {
    }

    public static String sha256Uri(byte[] bytes) {
        return "sha256:" + HexFormat.of().formatHex(sha256().digest(bytes));
    }

    public static String sha256Uri(Path path) {
        MessageDigest digest = sha256();
        try (InputStream input = Files.newInputStream(path, LinkOption.NOFOLLOW_LINKS)) {
            byte[] buffer = new byte[64 * 1024];
            int count;
            while ((count = input.read(buffer)) != -1) {
                digest.update(buffer, 0, count);
            }
        } catch (IOException error) {
            throw new BundleLoadException("ARTIFACT_READ_FAILED", path.toString(), error);
        }
        return "sha256:" + HexFormat.of().formatHex(digest.digest());
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 unavailable", impossible);
        }
    }
}
