package com.sfep.equipmentmonitor.replay;

import com.sfep.equipmentmonitor.bundle.Digests;
import com.sfep.equipmentmonitor.bundle.EmbeddedSchemas;
import com.sfep.equipmentmonitor.bundle.BundleLoadException;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ReplayUnitCursorTest {
    private static final String BUNDLE_ID = "sha256:4f40427690116f0defcbe609d9e5e1ef4025bb4193cdf099386659a26dd9f8bc";
    private static final String CRITERIA_ID = "sha256:8c88d3109bd6945b76317ee415c7821524f515c9bd79d36b8072c285c6434af7";

    @TempDir
    Path temporary;

    @Test
    void streamsTheGoldenRowsAsExactBatchAndStepUnitsWithoutClaimingWithinUnitOrder() throws Exception {
        List<ReplayUnit> units = new ArrayList<>();

        try (ReplayCursor cursor = ReplayCursor.open(goldenSource())) {
            Optional<ReplayUnit> next;
            while ((next = cursor.nextUnit()).isPresent()) {
                units.add(next.orElseThrow());
            }
        }

        assertThat(units).hasSize(53);
        assertThat(units).allSatisfy(unit -> {
            assertThat(unit.events()).isNotEmpty();
            assertThat(unit.orderedWithinUnit()).isFalse();
            assertThat(unit.events()).allSatisfy(event -> {
                assertThat(event.batchId()).isEqualTo(unit.batchId());
                assertThat(event.batchStep()).isEqualTo(unit.batchStep());
            });
        });

        Map<String, Long> eventCountsByStep = new LinkedHashMap<>();
        units.forEach(unit -> eventCountsByStep.merge(
                unit.batchStep(), (long) unit.events().size(), Long::sum));
        assertThat(eventCountsByStep).containsExactly(
                Map.entry("CAST_RECORDED", 12L),
                Map.entry("FURNACE_CHARGED", 12L),
                Map.entry("PREHEAT_COMPLETE", 12L),
                Map.entry("HEAT_COMPLETE", 12L),
                Map.entry("SOAK_COMPLETE", 12L),
                Map.entry("FURNACE_EXTRACTED", 12L),
                Map.entry("RM4_RECORDED", 12L),
                Map.entry("AP_RECORDED_WITH_RESULT", 11L));

        assertThat(units.stream().flatMap(unit -> unit.events().stream()).toList()).hasSize(95);
        assertThat(units.stream()
                .filter(unit -> unit.batchStep().equals("AP_RECORDED_WITH_RESULT"))
                .flatMap(unit -> unit.events().stream()))
                .allSatisfy(event -> assertThat(event.values().has("judge")).isTrue());
    }

    @Test
    void verifiesTheDescriptorBeforeParsingAndTheConsumedBytesAtEof() throws Exception {
        ReplaySource valid = goldenSource();
        ReplaySource wrongSize = new ReplaySource(
                valid.path(), valid.sizeBytes() + 1, valid.sha256(), valid.bundleId(),
                valid.criteriaId(), valid.metadata());
        ReplaySource wrongHash = new ReplaySource(
                valid.path(), valid.sizeBytes(), "sha256:" + "0".repeat(64), valid.bundleId(),
                valid.criteriaId(), valid.metadata());

        assertCode("REPLAY_DESCRIPTOR_SIZE_MISMATCH", () -> ReplayCursor.open(wrongSize));
        assertCode("REPLAY_DESCRIPTOR_HASH_MISMATCH", () -> ReplayCursor.open(wrongHash));

        Path replay = copyOfGolden("changed-while-consumed.csv");
        ReplaySource source = source(replay);
        try (ReplayCursor cursor = ReplayCursor.open(source)) {
            String csv = Files.readString(replay, StandardCharsets.UTF_8);
            int lastDefect = csv.lastIndexOf("불량");
            assertThat(lastDefect).isGreaterThan(0);
            String changed = csv.substring(0, lastDefect) + "양품" + csv.substring(lastDefect + 2);
            Files.writeString(replay, changed, StandardCharsets.UTF_8);
            assertThat(Files.size(replay)).isEqualTo(source.sizeBytes());

            assertCode("REPLAY_CONSUMED_HASH_MISMATCH", () -> exhaust(cursor));
        }
    }

    @Test
    void detectsWhenThePathIsReplacedAfterOpeningTheRetainedDescriptor() throws Exception {
        Path replay = copyOfGolden("replace.csv");
        ReplaySource source = source(replay);

        try (ReplayCursor cursor = ReplayCursor.open(source)) {
            Files.move(replay, temporary.resolve("retained.csv"), StandardCopyOption.ATOMIC_MOVE);
            Files.copy(goldenReplay(), replay);

            assertCode("REPLAY_FILE_CHANGED", () -> exhaust(cursor));
        }
    }

    @Test
    void exposesImmutableUnitsAndEventValues() throws Exception {
        try (ReplayCursor cursor = ReplayCursor.open(goldenSource())) {
            ReplayUnit unit = cursor.nextUnit().orElseThrow();
            ReplayEvent event = unit.events().getFirst();
            ((ObjectNode) event.values()).put("injected", true);

            assertThat(event.values().has("injected")).isFalse();
            assertThatThrownBy(() -> unit.events().add(event))
                    .isInstanceOf(UnsupportedOperationException.class);
        }
    }

    private static ReplaySource goldenSource() throws Exception {
        return source(goldenReplay());
    }

    private static ReplaySource source(Path replay) throws Exception {
        ReplayMetadata metadata = new ReplayCsvValidator(EmbeddedSchemas.load())
                .validate(replay, BUNDLE_ID, CRITERIA_ID);
        return new ReplaySource(
                replay,
                Files.size(replay),
                Digests.sha256Uri(replay),
                BUNDLE_ID,
                CRITERIA_ID,
                metadata);
    }

    private Path copyOfGolden(String name) throws Exception {
        Path copy = temporary.toRealPath().resolve(name);
        Files.copy(goldenReplay(), copy);
        return copy;
    }

    private static void exhaust(ReplayCursor cursor) {
        while (cursor.nextUnit().isPresent()) {
            // Consume one unit at a time; the cursor attests the stream at EOF.
        }
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
