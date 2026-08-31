package com.sfep.equipmentmonitor.replay;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sfep.equipmentmonitor.bundle.BundleLoadException;
import com.sfep.equipmentmonitor.bundle.DigestIds;
import com.sfep.equipmentmonitor.bundle.EmbeddedSchemas;
import com.sfep.equipmentmonitor.bundle.IdValue;
import com.sfep.equipmentmonitor.bundle.JsonSupport;
import com.sfep.equipmentmonitor.bundle.SafeFile;
import com.sfep.equipmentmonitor.bundle.SchemaRole;
import org.apache.commons.csv.CSVFormat;
import org.apache.commons.csv.CSVParser;
import org.apache.commons.csv.CSVRecord;

import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.Reader;
import java.io.UncheckedIOException;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.channels.Channels;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class ReplayCsvValidator {
    public static final List<String> HEADER = List.of(
            "schema_version", "bundle_id", "criteria_id", "event_id", "replay_date",
            "replay_hour", "batch_kind", "batch_id", "equipment_batch_id", "batch_step",
            "time_precision", "material_key", "equipment_type", "equipment_id", "charge_id",
            "slab_no", "hr_coil_id", "ap_prod_id", "values_json");

    private final EmbeddedSchemas schemas;

    public ReplayCsvValidator(EmbeddedSchemas schemas) {
        this.schemas = schemas;
    }

    public ReplayMetadata validate(Path path, String expectedBundleId, String expectedCriteriaId) {
        SafeFile file = SafeFile.captureStandalone(path);
        return validate(
                file,
                expectedBundleId,
                expectedCriteriaId,
                file.size(),
                com.sfep.equipmentmonitor.bundle.Digests.sha256Uri(path));
    }

    public ReplayMetadata validate(
            Path path,
            String expectedBundleId,
            String expectedCriteriaId,
            long expectedSize,
            String expectedSha256) {
        return validate(
                SafeFile.captureStandalone(path),
                expectedBundleId,
                expectedCriteriaId,
                expectedSize,
                expectedSha256);
    }

    public ReplayMetadata validate(
            SafeFile file,
            String expectedBundleId,
            String expectedCriteriaId,
            long expectedSize,
            String expectedSha256) {
        var decoder = StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT);
        CSVFormat format = CSVFormat.RFC4180.builder()
                .setHeader()
                .setSkipHeaderRecord(true)
                .get();

        long count = 0;
        String firstEvent = null;
        String lastEvent = null;
        ReplaySortKey previous = null;
        Set<String> eventIds = new HashSet<>();
        Map<String, String> idPreimages = new HashMap<>();

        try (AuthenticatedReplaySnapshot snapshot = AuthenticatedReplaySnapshot.capture(
                     file, expectedSize, expectedSha256);
             InputStream input = Channels.newInputStream(snapshot.channel());
             Reader reader = new InputStreamReader(input, decoder);
             CSVParser parser = format.parse(reader)) {
            if (!parser.getHeaderNames().equals(HEADER)) {
                throw new BundleLoadException(
                        "REPLAY_HEADER_INVALID",
                        "expected " + HEADER + " but found " + parser.getHeaderNames());
            }
            for (CSVRecord record : parser) {
                if (record.size() != HEADER.size()) {
                    throw new BundleLoadException(
                            "REPLAY_CSV_INVALID",
                            "record " + record.getRecordNumber() + " has " + record.size() + " columns");
                }
                JsonNode row = rowNode(record);
                schemas.validate(SchemaRole.REPLAY_EVENTS, row, "REPLAY_SCHEMA_INVALID");
                ReplayEvent event = bind(row);
                if (!event.bundleId().equals(expectedBundleId)) {
                    throw new BundleLoadException("REPLAY_BUNDLE_ID_MISMATCH", event.eventId());
                }
                if (!event.criteriaId().equals(expectedCriteriaId)) {
                    throw new BundleLoadException("REPLAY_CRITERIA_ID_MISMATCH", event.eventId());
                }
                if (!eventIds.add(event.eventId())) {
                    throw new BundleLoadException("REPLAY_EVENT_ID_DUPLICATE", event.eventId());
                }
                verifyDerivedIds(event, idPreimages);

                ReplaySortKey current = ReplaySortKey.from(event);
                if (previous != null && previous.compareTo(current) >= 0) {
                    throw new BundleLoadException("REPLAY_SORT_INVALID", event.eventId());
                }
                previous = current;
                if (firstEvent == null) {
                    firstEvent = event.eventId();
                }
                lastEvent = event.eventId();
                count++;
            }
            file.assertUnchanged("REPLAY_FILE_CHANGED");
        } catch (BundleLoadException error) {
            throw error;
        } catch (UncheckedIOException | IOException error) {
            if (hasCharacterCodingCause(error)) {
                throw new BundleLoadException("REPLAY_UTF8_INVALID", file.path().toString(), error);
            }
            throw new BundleLoadException("REPLAY_CSV_INVALID", file.path().toString(), error);
        } catch (RuntimeException error) {
            if (hasCharacterCodingCause(error)) {
                throw new BundleLoadException("REPLAY_UTF8_INVALID", file.path().toString(), error);
            }
            throw new BundleLoadException("REPLAY_CSV_INVALID", file.path().toString(), error);
        }

        if (count == 0) {
            throw new BundleLoadException("REPLAY_EMPTY", file.path().toString());
        }
        return new ReplayMetadata(count, firstEvent, lastEvent);
    }

    static ObjectNode rowNode(CSVRecord record) {
        ObjectNode row = JsonNodeFactory.instance.objectNode();
        row.put("schema_version", record.get(0));
        row.put("bundle_id", record.get(1));
        row.put("criteria_id", record.get(2));
        row.put("event_id", record.get(3));
        row.put("replay_date", record.get(4));
        String hour = record.get(5);
        if (hour.isEmpty()) {
            row.putNull("replay_hour");
        } else {
            try {
                row.put("replay_hour", Integer.parseInt(hour));
            } catch (NumberFormatException error) {
                row.put("replay_hour", hour);
            }
        }
        row.put("batch_kind", record.get(6));
        row.put("batch_id", record.get(7));
        putNullable(row, "equipment_batch_id", record.get(8));
        row.put("batch_step", record.get(9));
        row.put("time_precision", record.get(10));
        row.put("material_key", record.get(11));
        row.put("equipment_type", record.get(12));
        row.put("equipment_id", record.get(13));
        row.put("charge_id", record.get(14));
        row.put("slab_no", record.get(15));
        putNullable(row, "hr_coil_id", record.get(16));
        putNullable(row, "ap_prod_id", record.get(17));
        JsonNode values = JsonSupport.parse(
                record.get(18).getBytes(StandardCharsets.UTF_8),
                "REPLAY_SCHEMA_INVALID",
                "values_json at record " + record.getRecordNumber());
        row.set("values_json", values);
        return row;
    }

    private static void putNullable(ObjectNode row, String field, String value) {
        if (value.isEmpty()) {
            row.putNull(field);
        } else {
            row.put(field, value);
        }
    }

    static ReplayEvent bind(JsonNode row) {
        return new ReplayEvent(
                text(row, "schema_version"),
                text(row, "bundle_id"),
                text(row, "criteria_id"),
                text(row, "event_id"),
                text(row, "replay_date"),
                row.path("replay_hour").isNull() ? null : row.path("replay_hour").intValue(),
                text(row, "batch_kind"),
                text(row, "batch_id"),
                nullableText(row, "equipment_batch_id"),
                text(row, "batch_step"),
                text(row, "time_precision"),
                text(row, "material_key"),
                text(row, "equipment_type"),
                text(row, "equipment_id"),
                text(row, "charge_id"),
                text(row, "slab_no"),
                nullableText(row, "hr_coil_id"),
                nullableText(row, "ap_prod_id"),
                row.path("values_json"));
    }

    private static String text(JsonNode row, String name) {
        return row.path(name).textValue();
    }

    private static String nullableText(JsonNode row, String name) {
        JsonNode value = row.path(name);
        return value.isNull() ? null : value.textValue();
    }

    private static void verifyDerivedIds(ReplayEvent event, Map<String, String> registry) {
        ObjectNode material = JsonNodeFactory.instance.objectNode();
        material.put("chargeId", event.chargeId());
        material.put("slabNo", event.slabNo());
        assertId("REPLAY_MATERIAL_KEY_MISMATCH", event.materialKey(),
                DigestIds.compute("sfep-material-key/v1", material), registry);

        ObjectNode batch = JsonNodeFactory.instance.objectNode();
        batch.put("batchKind", event.batchKind());
        batch.put("replayDate", event.replayDate());
        if (event.batchKind().equals("FURNACE_HOUR")) {
            batch.put("replayHour", event.replayHour());
        }
        assertId("REPLAY_BATCH_ID_MISMATCH", event.batchId(),
                DigestIds.compute("sfep-batch-id/v1", batch), registry);

        if (event.equipmentBatchId() != null) {
            ObjectNode equipment = JsonNodeFactory.instance.objectNode();
            equipment.put("batchId", event.batchId());
            equipment.put("equipmentId", event.equipmentId());
            equipment.put("equipmentType", event.equipmentType());
            assertId("REPLAY_EQUIPMENT_BATCH_ID_MISMATCH", event.equipmentBatchId(),
                    DigestIds.compute("sfep-equipment-batch-id/v1", equipment), registry);
        }

        ObjectNode eventObject = JsonNodeFactory.instance.objectNode();
        eventObject.put("batchId", event.batchId());
        eventObject.put("batchStep", event.batchStep());
        if (event.equipmentBatchId() == null) {
            eventObject.putNull("equipmentBatchId");
        } else {
            eventObject.put("equipmentBatchId", event.equipmentBatchId());
        }
        eventObject.put("equipmentId", event.equipmentId());
        eventObject.put("equipmentType", event.equipmentType());
        eventObject.put("materialKey", event.materialKey());
        assertId("REPLAY_EVENT_ID_MISMATCH", event.eventId(),
                DigestIds.compute("sfep-event-id/v1", eventObject), registry);
    }

    private static void assertId(
            String mismatchCode,
            String claimed,
            IdValue computed,
            Map<String, String> registry) {
        if (!computed.id().equals(claimed)) {
            throw new BundleLoadException(mismatchCode, claimed);
        }
        String previous = registry.putIfAbsent(claimed, computed.preimage());
        if (previous != null && !previous.equals(computed.preimage())) {
            throw new BundleLoadException("REPLAY_ID_COLLISION", claimed);
        }
    }

    private static boolean hasCharacterCodingCause(Throwable error) {
        for (Throwable current = error; current != null; current = current.getCause()) {
            if (current instanceof CharacterCodingException) {
                return true;
            }
        }
        return false;
    }

}
