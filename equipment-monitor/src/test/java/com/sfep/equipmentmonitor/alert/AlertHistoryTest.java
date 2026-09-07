package com.sfep.equipmentmonitor.alert;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFileAttributeView;
import java.nio.file.attribute.PosixFilePermission;
import java.util.List;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

class AlertHistoryTest {
    private static final Set<PosixFilePermission> OWNER_ONLY = Set.of(
            PosixFilePermission.OWNER_READ,
            PosixFilePermission.OWNER_WRITE);

    @Test
    void publishesCsvWithOwnerOnlyPermissions(@TempDir Path temporary) throws Exception {
        assumeTrue(Files.getFileAttributeView(
                temporary, PosixFileAttributeView.class) != null);
        Path destination = temporary.resolve("alerts.csv");

        new AlertHistory().exportCsv(destination);

        assertThat(Files.getPosixFilePermissions(destination)).isEqualTo(OWNER_ONLY);
    }

    @Test
    void failedExportPreservesExistingDestinationAndLeavesNoTemporarySibling(
            @TempDir Path temporary) throws Exception {
        assumeTrue(Files.getFileAttributeView(
                temporary, PosixFileAttributeView.class) != null);
        Path directory = temporary.resolve("export");
        Files.createDirectory(directory);
        Path destination = directory.resolve("alerts.csv");
        Files.writeString(destination, "existing-destination", StandardCharsets.UTF_8);
        Set<PosixFilePermission> originalPermissions = Files.getPosixFilePermissions(directory);
        Files.setPosixFilePermissions(directory, Set.of(
                PosixFilePermission.OWNER_READ,
                PosixFilePermission.OWNER_EXECUTE));

        try {
            assertThatThrownBy(() -> new AlertHistory().exportCsv(destination))
                    .isInstanceOf(AlertExportException.class)
                    .hasMessageStartingWith("ALERT_EXPORT_FAILED:");
        } finally {
            Files.setPosixFilePermissions(directory, originalPermissions);
        }

        assertThat(Files.readString(destination, StandardCharsets.UTF_8))
                .isEqualTo("existing-destination");
        assertThat(childNames(directory)).containsExactly("alerts.csv");

        Path blockedDestination = directory.resolve("blocked.csv");
        Files.createDirectory(blockedDestination);
        Files.writeString(blockedDestination.resolve("keep.txt"), "keep", StandardCharsets.UTF_8);

        assertThatThrownBy(() -> new AlertHistory().exportCsv(blockedDestination))
                .isInstanceOf(AlertExportException.class)
                .hasMessageStartingWith("ALERT_EXPORT_FAILED:");
        assertThat(Files.readString(
                blockedDestination.resolve("keep.txt"), StandardCharsets.UTF_8)).isEqualTo("keep");
        assertThat(childNames(directory)).containsExactlyInAnyOrder("alerts.csv", "blocked.csv");
    }

    private static List<String> childNames(Path directory) throws Exception {
        try (var children = Files.list(directory)) {
            return children.map(path -> path.getFileName().toString()).sorted().toList();
        }
    }
}
