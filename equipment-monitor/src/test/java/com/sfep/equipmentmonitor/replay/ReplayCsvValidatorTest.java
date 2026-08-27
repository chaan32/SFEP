package com.sfep.equipmentmonitor.replay;

import com.sfep.equipmentmonitor.bundle.BundleLoadException;
import com.sfep.equipmentmonitor.bundle.EmbeddedSchemas;
import com.sfep.equipmentmonitor.bundle.Digests;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.assertThatCode;

class ReplayCsvValidatorTest {
    private static final String BUNDLE_ID = "sha256:4f40427690116f0defcbe609d9e5e1ef4025bb4193cdf099386659a26dd9f8bc";
    private static final String CRITERIA_ID = "sha256:8c88d3109bd6945b76317ee415c7821524f515c9bd79d36b8072c285c6434af7";

    @TempDir
    Path temporary;

    @Test
    void parsesTheGoldenRfc4180Utf8FileIncrementallyAndValidatesOrderingAndIds() throws Exception {
        ReplayCsvValidator validator = new ReplayCsvValidator(EmbeddedSchemas.load());

        ReplayMetadata metadata = validator.validate(goldenReplay(), BUNDLE_ID, CRITERIA_ID);

        assertThat(metadata.rowCount()).isEqualTo(95);
        assertThat(metadata.firstEventId())
                .isEqualTo("sha256:b80d26659b6f6c3ab842b840ce7c8f11eb621ce5292eb14156732c5d91f3c324");
        assertThat(metadata.lastEventId())
                .isEqualTo("sha256:71a6ea2d6ce6e0f65f4b2188ce5e692df5e63408eb868b18a9f292419677a0a1");
    }

    @Test
    void rejectsAnyHeaderDriftOrMalformedUtf8() throws Exception {
        String csv = Files.readString(goldenReplay());
        Path physical = temporary.toRealPath();
        Path wrongHeader = physical.resolve("wrong-header.csv");
        Files.writeString(wrongHeader, csv.replaceFirst("schema_version,bundle_id", "bundle_id,schema_version"));
        assertCode("REPLAY_HEADER_INVALID", () -> validate(wrongHeader));

        Path invalidUtf8 = physical.resolve("invalid-utf8.csv");
        byte[] bytes = Files.readAllBytes(goldenReplay());
        bytes[bytes.length - 2] = (byte) 0xFF;
        Files.write(invalidUtf8, bytes);
        assertCode("REPLAY_UTF8_INVALID", () -> validate(invalidUtf8));

        Path malformed = physical.resolve("malformed.csv");
        Files.writeString(malformed, csv + "\"unterminated", StandardCharsets.UTF_8);
        assertCode("REPLAY_CSV_INVALID", () -> validate(malformed));
    }

    @Test
    void rejectsOutOfOrderDuplicateAndNonDerivedIds() throws Exception {
        var lines = new ArrayList<>(Files.readAllLines(goldenReplay(), StandardCharsets.UTF_8));

        Path physical = temporary.toRealPath();
        Path outOfOrder = physical.resolve("out-of-order.csv");
        String first = lines.get(1);
        lines.set(1, lines.get(2));
        lines.set(2, first);
        Files.write(outOfOrder, lines, StandardCharsets.UTF_8);
        assertCode("REPLAY_SORT_INVALID", () -> validate(outOfOrder));

        lines = new ArrayList<>(Files.readAllLines(goldenReplay(), StandardCharsets.UTF_8));
        lines.set(2, lines.get(1));
        Path duplicate = physical.resolve("duplicate.csv");
        Files.write(duplicate, lines, StandardCharsets.UTF_8);
        assertCode("REPLAY_EVENT_ID_DUPLICATE", () -> validate(duplicate));

        String csv = Files.readString(goldenReplay());
        Path wrongId = physical.resolve("wrong-id.csv");
        Files.writeString(wrongId, csv.replaceFirst(
                "sha256:b80d26659b6f6c3ab842b840ce7c8f11eb621ce5292eb14156732c5d91f3c324",
                "sha256:" + "0".repeat(64)));
        assertCode("REPLAY_EVENT_ID_MISMATCH", () -> validate(wrongId));
    }

    @Test
    void rejectsRowsBoundToAnotherBundleOrCriteria() throws Exception {
        String csv = Files.readString(goldenReplay());
        Path physical = temporary.toRealPath();
        Path wrongBundle = physical.resolve("wrong-bundle.csv");
        Files.writeString(wrongBundle, csv.replaceFirst(BUNDLE_ID, "sha256:" + "0".repeat(64)));
        assertCode("REPLAY_BUNDLE_ID_MISMATCH", () -> validate(wrongBundle));

        Path wrongCriteria = physical.resolve("wrong-criteria.csv");
        Files.writeString(wrongCriteria, csv.replaceFirst(CRITERIA_ID, "sha256:" + "0".repeat(64)));
        assertCode("REPLAY_CRITERIA_ID_MISMATCH", () -> validate(wrongCriteria));
    }

    @Test
    void acceptsBothNormativeFurnaceNumberSpellingsForEquipmentSorting() {
        assertThatCode(() -> ReplaySortKey.from(furnaceEvent("1"))).doesNotThrowAnyException();
        assertThatCode(() -> ReplaySortKey.from(furnaceEvent("1호기"))).doesNotThrowAnyException();
    }

    @Test
    void attestsTheExactReplayBytesConsumedByTheStreamingParser() throws Exception {
        Path replay = goldenReplay();
        ReplayCsvValidator validator = new ReplayCsvValidator(EmbeddedSchemas.load());
        long size = Files.size(replay);
        String sha256 = Digests.sha256Uri(replay);

        assertCode("REPLAY_CONSUMED_SIZE_MISMATCH", () ->
                validator.validate(replay, BUNDLE_ID, CRITERIA_ID, size + 1, sha256));
        assertCode("REPLAY_CONSUMED_HASH_MISMATCH", () ->
                validator.validate(
                        replay,
                        BUNDLE_ID,
                        CRITERIA_ID,
                        size,
                        "sha256:" + "0".repeat(64)));
    }

    private static ReplayEvent furnaceEvent(String equipmentId) {
        return new ReplayEvent(
                "sfep-replay-events/v1", BUNDLE_ID, CRITERIA_ID,
                "sha256:" + "f".repeat(64), "2025-01-01", 0,
                "FURNACE_HOUR", "sha256:" + "a".repeat(64),
                "sha256:" + "b".repeat(64), "FURNACE_CHARGED", "HOUR_BUCKET",
                "sha256:" + "c".repeat(64), "FURNACE", equipmentId,
                "CH1", "1", null, null,
                com.fasterxml.jackson.databind.node.JsonNodeFactory.instance.objectNode());
    }

    private ReplayMetadata validate(Path path) {
        return new ReplayCsvValidator(EmbeddedSchemas.load()).validate(path, BUNDLE_ID, CRITERIA_ID);
    }

    private static Path goldenReplay() {
        return Path.of(System.getProperty("sfep.repo-root"))
                .resolve("contracts/equipment-monitor/v1/golden-bundle/replay_events.csv");
    }

    private static void assertCode(String code, Runnable action) {
        assertThatThrownBy(action::run)
                .isInstanceOf(BundleLoadException.class)
                .hasMessageStartingWith(code + ":");
    }
}
