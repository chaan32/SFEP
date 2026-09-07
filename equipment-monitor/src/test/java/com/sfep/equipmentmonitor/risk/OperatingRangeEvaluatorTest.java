package com.sfep.equipmentmonitor.risk;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class OperatingRangeEvaluatorTest {
    private static final ObjectMapper JSON = new ObjectMapper();
    private final RiskDefinitionCompiler compiler = new RiskDefinitionCompiler();
    private final OperatingRangeEvaluator evaluator = new OperatingRangeEvaluator();

    @Test
    void directOperationAlertsButProductStateIsReferenceOnly() throws Exception {
        List<OperatingRange> ranges = compiler.compileRanges(List.of(
                range("a", "f_jangip_temp", "DIRECT_OPERATION", 0, "{}", 2500, 1200, 1100.0, 1150, 1250, 1300.0, true, true),
                range("b", "slab_width", "PRODUCT_STATE_REFERENCE", 0, "{}", 2500, 1200, 1000.0, 1100, 1300, 1400.0, true, true)));
        RiskEvent event = event(Map.of("f_jangip_temp", 1000, "slab_width", 900));

        List<RangeEvaluation> results = evaluator.evaluate(event, ranges);

        assertThat(result(results, "f_jangip_temp").status()).isEqualTo(RangeStatus.SEVERE);
        assertThat(result(results, "f_jangip_temp").alertEligible()).isTrue();
        assertThat(result(results, "slab_width").status()).isEqualTo(RangeStatus.REFERENCE_ONLY);
        assertThat(result(results, "slab_width").alertEligible()).isFalse();
    }

    @Test
    void choosesLowestCompleteContextAndSkipsUnavailableDerivedBands() throws Exception {
        List<OperatingRange> ranges = compiler.compileRanges(List.of(
                range("a", "f_jangip_temp", "DIRECT_OPERATION", 0, "{\"furnace_no\":\"1호기\",\"slab_width_band\":\"Q2\"}", 900, 1200, 1100.0, 1150, 1250, 1300.0, true, true),
                range("b", "f_jangip_temp", "DIRECT_OPERATION", 1, "{\"furnace_no\":\"1호기\",\"steel_grade\":\"C13\"}", 800, 1200, 1100.0, 1150, 1250, 1300.0, true, true),
                range("c", "f_jangip_temp", "DIRECT_OPERATION", 2, "{\"furnace_no\":\"1호기\"}", 3000, 1200, 1080.0, 1140, 1260, 1320.0, true, true)));
        RiskEvent event = event(Map.of("f_jangip_temp", 1210, "furnace_no", "1호기", "steel_grade", "C13"));

        RangeEvaluation result = evaluator.evaluate(event, ranges).getFirst();

        assertThat(result.selectedRuleId()).isEqualTo(ranges.get(1).ruleId());
        assertThat(result.contextLevel()).isEqualTo(1);
        assertThat(result.selectionReason()).isEqualTo(RangeSelectionReason.BAND_BOUNDARY_NOT_SEALED_FALLBACK);
    }

    @Test
    void usesTheMostSpecificBandContextWhenTheBandWasPublished() throws Exception {
        List<OperatingRange> ranges = compiler.compileRanges(List.of(
                range("a", "f_jangip_temp", "DIRECT_OPERATION", 0,
                        "{\"furnace_no\":\"1호기\",\"slab_width_band\":\"Q1\"}",
                        900, 1200, 1100.0, 1150, 1250, 1300.0, true, true),
                range("b", "f_jangip_temp", "DIRECT_OPERATION", 1,
                        "{\"furnace_no\":\"1호기\"}",
                        3000, 1200, 1080.0, 1140, 1260, 1320.0, true, true)));
        RiskEvent event = event(Map.of(
                "f_jangip_temp", 1130,
                "furnace_no", "1호기",
                "slab_width_band", "Q1"));

        RangeEvaluation result = evaluator.evaluate(event, ranges).getFirst();

        assertThat(result.selectedRuleId()).isEqualTo(ranges.getFirst().ruleId());
        assertThat(result.contextLevel()).isZero();
        assertThat(result.status()).isEqualTo(RangeStatus.CAUTION);
    }

    @Test
    void makesMissingBaselinesAndUnregisteredEquipmentExplicit() throws Exception {
        List<RangeEvaluation> withoutBaseline = evaluator.evaluate(
                RiskEvent.of(ProcessStage.PREHEAT_COMPLETE, EquipmentType.FURNACE, "1호기",
                        Map.of("f_pre_temp", RiskScalar.of(900))),
                List.of());
        OperatingRange knownEquipment = compiler.compileRanges(List.of(
                range("a", "f_jangip_temp", "DIRECT_OPERATION", 0, "{}",
                        2500, 1200, 1100.0, 1150, 1250, 1300.0, true, true))).getFirst();
        List<RangeEvaluation> unknownEquipment = evaluator.evaluate(
                RiskEvent.of(ProcessStage.FURNACE_CHARGED, EquipmentType.FURNACE, "5호기",
                        Map.of("f_jangip_temp", RiskScalar.of(1200))),
                List.of(knownEquipment));

        assertThat(result(withoutBaseline, "f_pre_temp").status())
                .isEqualTo(RangeStatus.INSUFFICIENT_EVIDENCE);
        assertThat(result(unknownEquipment, "f_jangip_temp").status())
                .isEqualTo(RangeStatus.UNREGISTERED_CONDITION);
    }

    @Test
    void appliesTailBoundariesWithoutInventingOrUsingCollapsedTails() throws Exception {
        OperatingRange full = compiler.compileRanges(List.of(
                range("a", "f_jangip_temp", "DIRECT_OPERATION", 0, "{}", 2500, 50, 10.0, 20, 80, 90.0, true, true))).getFirst();
        OperatingRange noExtreme = compiler.compileRanges(List.of(
                range("b", "f_jangip_temp", "DIRECT_OPERATION", 0, "{}", 500, 50, null, 20, 80, null, false, false))).getFirst();
        OperatingRange collapsed = compiler.compileRanges(List.of(
                range("c", "f_jangip_temp", "DIRECT_OPERATION", 0, "{}", 2500, 50, 20.0, 20, 80, 90.0, false, true))).getFirst();

        assertThat(status(full, 9)).isEqualTo(RangeStatus.SEVERE);
        assertThat(status(full, 10)).isEqualTo(RangeStatus.CAUTION);
        assertThat(status(full, 20)).isEqualTo(RangeStatus.TYPICAL);
        assertThat(status(full, 90)).isEqualTo(RangeStatus.CAUTION);
        assertThat(status(full, 91)).isEqualTo(RangeStatus.SEVERE);
        assertThat(status(noExtreme, 1)).isEqualTo(RangeStatus.CAUTION);
        assertThat(status(collapsed, 1)).isEqualTo(RangeStatus.TYPICAL);
    }

    @Test
    void distinguishesDataMissingUnregisteredAndDerivedOnlyInsufficientEvidence() throws Exception {
        OperatingRange known = compiler.compileRanges(List.of(
                range("a", "f_jangip_temp", "DIRECT_OPERATION", 0, "{\"f_jangip_gubun\":\"CCR\"}", 500, 1200, null, 1100, 1300, null, false, false))).getFirst();
        OperatingRange derived = compiler.compileRanges(List.of(
                range("b", "f_jangip_temp", "DIRECT_OPERATION", 0, "{\"slab_width_band\":\"Q1\"}", 500, 1200, null, 1100, 1300, null, false, false))).getFirst();

        assertThat(evaluator.evaluate(event(Map.of("f_jangip_gubun", "CCR")), List.of(known)).getFirst().status())
                .isEqualTo(RangeStatus.DATA_MISSING);
        assertThat(evaluator.evaluate(event(Map.of("f_jangip_temp", 1200, "f_jangip_gubun", "NEW")), List.of(known)).getFirst().status())
                .isEqualTo(RangeStatus.UNREGISTERED_CONDITION);
        assertThat(evaluator.evaluate(event(Map.of("f_jangip_temp", 1200)), List.of(derived)).getFirst().status())
                .isEqualTo(RangeStatus.INSUFFICIENT_EVIDENCE);
        assertThat(evaluator.evaluate(event(Map.of("f_jangip_temp", 1200)), List.of(derived)).getFirst().selectionReason())
                .isEqualTo(RangeSelectionReason.BAND_BOUNDARY_NOT_SEALED);
    }

    @Test
    void compilerRejectsUnknownPropertiesInvalidScalarsAndImpossibleTailShape() throws Exception {
        JsonNode unknown = range("a", "f_jangip_temp", "DIRECT_OPERATION", 0, "{}", 2500, 50, 10.0, 20, 80, 90.0, true, true);
        ((com.fasterxml.jackson.databind.node.ObjectNode) unknown).put("extra", 1);
        JsonNode objectScalar = range("b", "f_jangip_temp", "DIRECT_OPERATION", 0, "{\"furnace_no\":{\"bad\":1}}", 2500, 50, 10.0, 20, 80, 90.0, true, true);
        JsonNode enabledWithoutP01 = range("c", "f_jangip_temp", "DIRECT_OPERATION", 0, "{}", 2500, 50, null, 20, 80, 90.0, true, true);

        assertThatThrownBy(() -> compiler.compileRanges(List.of(unknown)))
                .isInstanceOf(RiskDefinitionException.class).hasMessageContaining("extra");
        assertThatThrownBy(() -> compiler.compileRanges(List.of(objectScalar)))
                .isInstanceOf(RiskDefinitionException.class).hasMessageContaining("scalar");
        assertThatThrownBy(() -> compiler.compileRanges(List.of(enabledWithoutP01)))
                .isInstanceOf(RiskDefinitionException.class).hasMessageContaining("p01");
    }

    private RangeStatus status(OperatingRange range, double value) {
        return evaluator.evaluate(event(Map.of("f_jangip_temp", value)), List.of(range)).getFirst().status();
    }

    private static RangeEvaluation result(List<RangeEvaluation> values, String field) {
        return values.stream().filter(value -> value.field().equals(field)).findFirst().orElseThrow();
    }

    private static RiskEvent event(Map<String, ?> raw) {
        return RiskEvent.of(ProcessStage.FURNACE_CHARGED, EquipmentType.FURNACE, "1호기",
                raw.entrySet().stream().collect(java.util.stream.Collectors.toUnmodifiableMap(
                        Map.Entry::getKey, entry -> RiskScalar.of(entry.getValue()))));
    }

    private static JsonNode range(
            String suffix, String field, String role, int level, String context, long support,
            double median, Double p01, double p05, double p95, Double p99,
            boolean lowerEnabled, boolean upperEnabled) throws Exception {
        String digit = switch (suffix) { case "a" -> "a"; case "b" -> "b"; default -> "c"; };
        com.fasterxml.jackson.databind.node.ObjectNode node = (com.fasterxml.jackson.databind.node.ObjectNode) JSON.readTree("""
                {"ruleId":"sha256:%s","field":"%s","fieldRole":"%s",
                 "firstAvailableStage":"FURNACE_CHARGED","equipmentType":"FURNACE","equipmentId":"1호기",
                 "contextLevel":%d,"context":%s,"support":%d,"median":%s,
                 "p01":%s,"p05":%s,"p95":%s,"p99":%s,
                 "lowerTailEnabled":%s,"upperTailEnabled":%s}
                """.formatted(digit.repeat(64), field, role, level, context, support, median,
                p01 == null ? "null" : p01, p05, p95, p99 == null ? "null" : p99,
                lowerEnabled, upperEnabled));
        node.put("ruleId", RiskDefinitionIds.operatingRangeId(node));
        return node;
    }
}
