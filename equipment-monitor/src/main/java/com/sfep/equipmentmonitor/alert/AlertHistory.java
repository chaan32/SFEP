package com.sfep.equipmentmonitor.alert;

import org.apache.commons.csv.CSVFormat;
import org.apache.commons.csv.CSVPrinter;

import java.io.IOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.FileAttribute;
import java.nio.file.attribute.PosixFileAttributeView;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Objects;
import java.util.Set;

/** In-memory, insertion-ordered history with contract-key duplicate suppression. */
public final class AlertHistory {
    private static final String[] CSV_HEADER = {
            "bundle_id", "material_key", "event_stage", "rule_id", "kind", "grade",
            "repeated", "replay_date", "replay_hour", "equipment_type", "equipment_id",
            "field", "observed_value", "range_status", "discovery_support",
            "discovery_defects", "discovery_relative_risk", "discovery_rr_ci_lower",
            "discovery_rr_ci_upper", "discovery_q_value", "confirmation_support",
            "confirmation_defects"
    };
    private static final FileAttribute<Set<PosixFilePermission>> OWNER_ONLY =
            PosixFilePermissions.asFileAttribute(Set.of(
                    PosixFilePermission.OWNER_READ,
                    PosixFilePermission.OWNER_WRITE));

    private final LinkedHashMap<AlertKey, HistoricalAlert> alerts = new LinkedHashMap<>();

    public synchronized boolean add(HistoricalAlert alert) {
        Objects.requireNonNull(alert, "alert");
        if (alerts.containsKey(alert.key())) {
            return false;
        }
        alerts.put(alert.key(), alert);
        return true;
    }

    public synchronized List<HistoricalAlert> snapshot() {
        return List.copyOf(alerts.values());
    }

    public synchronized void reset() {
        alerts.clear();
    }

    public void exportCsv(Path destination) {
        Objects.requireNonNull(destination, "destination");
        List<HistoricalAlert> stable = snapshot();
        Path temporary = null;
        try {
            Path absoluteDestination = destination.toAbsolutePath().normalize();
            temporary = createTemporarySibling(absoluteDestination);
            writeCsv(temporary, stable);
            Files.move(
                    temporary,
                    absoluteDestination,
                    StandardCopyOption.ATOMIC_MOVE,
                    StandardCopyOption.REPLACE_EXISTING);
            temporary = null;
        } catch (IOException error) {
            deleteAfterFailure(temporary, error);
            throw new AlertExportException("ALERT_EXPORT_FAILED: " + destination, error);
        } catch (RuntimeException | Error error) {
            deleteAfterFailure(temporary, error);
            throw error;
        }
    }

    private static void writeCsv(Path temporary, List<HistoricalAlert> stable) throws IOException {
        try (Writer writer = Files.newBufferedWriter(
                temporary,
                StandardCharsets.UTF_8,
                StandardOpenOption.TRUNCATE_EXISTING,
                StandardOpenOption.WRITE);
             CSVPrinter printer = CSVFormat.RFC4180.builder().setHeader(CSV_HEADER).get().print(writer)) {
            for (HistoricalAlert alert : stable) {
                List<Object> row = new ArrayList<>();
                row.add(alert.key().bundleId());
                row.add(alert.materialKey());
                row.add(alert.eventStage());
                row.add(alert.ruleId());
                row.add(alert.kind());
                row.add(alert.grade());
                row.add(alert.repeated());
                row.add(alert.replayDate());
                row.add(nullable(alert.replayHour()));
                row.add(alert.equipmentType());
                row.add(alert.equipmentId());
                row.add(nullable(alert.field()));
                row.add(alert.observedValue() == null ? "" : alert.observedValue().toString());
                row.add(nullable(alert.rangeStatus()));
                appendMetric(row, alert.discovery());
                appendConfirmation(row, alert.confirmation());
                printer.printRecord(row);
            }
        }
    }

    private static Path createTemporarySibling(Path destination) throws IOException {
        Path parent = destination.getParent();
        if (parent == null) {
            throw new IOException("destination has no parent");
        }
        boolean posixPermissionsSupported = Files.getFileAttributeView(
                parent,
                PosixFileAttributeView.class,
                LinkOption.NOFOLLOW_LINKS) != null;
        if (posixPermissionsSupported) {
            return Files.createTempFile(parent, ".sfep-alert-export-", ".tmp", OWNER_ONLY);
        }
        return Files.createTempFile(parent, ".sfep-alert-export-", ".tmp");
    }

    private static void deleteAfterFailure(Path temporary, Throwable failure) {
        if (temporary == null) return;
        try {
            Files.deleteIfExists(temporary);
        } catch (IOException cleanupError) {
            failure.addSuppressed(cleanupError);
        }
    }

    private static void appendMetric(List<Object> row, com.sfep.equipmentmonitor.risk.MetricEvidence metric) {
        row.add(metric == null ? "" : metric.support());
        row.add(metric == null ? "" : metric.defects());
        row.add(metric == null ? "" : nullable(metric.relativeRisk()));
        row.add(metric == null ? "" : nullable(metric.relativeRiskCiLower()));
        row.add(metric == null ? "" : nullable(metric.relativeRiskCiUpper()));
        row.add(metric == null ? "" : nullable(metric.qValue()));
    }

    private static void appendConfirmation(List<Object> row, com.sfep.equipmentmonitor.risk.MetricEvidence metric) {
        row.add(metric == null ? "" : metric.support());
        row.add(metric == null ? "" : metric.defects());
    }

    private static Object nullable(Object value) {
        return value == null ? "" : value;
    }
}
