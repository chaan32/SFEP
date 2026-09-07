package com.sfep.equipmentmonitor.state;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sfep.equipmentmonitor.alert.AlertKind;
import com.sfep.equipmentmonitor.replay.ReplayEvent;
import com.sfep.equipmentmonitor.replay.ReplayUnit;
import com.sfep.equipmentmonitor.risk.OperatingRange;
import com.sfep.equipmentmonitor.risk.QualityRule;
import com.sfep.equipmentmonitor.risk.RangeStatus;
import com.sfep.equipmentmonitor.risk.RiskDefinitionCompiler;
import com.sfep.equipmentmonitor.risk.RiskGrade;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class HistoricalMonitorTest {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final String BUNDLE = "bundle-a";
    private static final String MATERIAL = "material-a";
    private final RiskDefinitionCompiler compiler = new RiskDefinitionCompiler();

    @Test
    void accumulatesOnlyValuesPublishedByTheCurrentStageAndReturnsImmutableSnapshots() throws Exception {
        HistoricalMonitor monitor = new HistoricalMonitor(BUNDLE, List.of(), List.of());

        monitor.accept(unit(event(MATERIAL, "CAST_RECORDED", "SM_CC", "SM1",
                "{\"steel_grade\":\"C13\",\"tundish_temp\":1540}")));
        MonitorSnapshot cast = monitor.snapshot();

        assertThat(cast.materials().get(MATERIAL).values())
                .containsKeys("steel_grade", "tundish_temp")
                .doesNotContainKey("f_jangip_temp");
        assertThatThrownBy(() -> cast.materials().put("x", cast.materials().get(MATERIAL)))
                .isInstanceOf(UnsupportedOperationException.class);

        monitor.accept(unit(event(MATERIAL, "FURNACE_CHARGED", "FURNACE", "1호기",
                "{\"furnace_no\":\"1호기\",\"f_jangip_gubun\":\"CCR\",\"f_jangip_temp\":1180}")));
        MonitorSnapshot charged = monitor.snapshot();

        assertThat(charged.materials().get(MATERIAL).values())
                .containsKeys("steel_grade", "tundish_temp", "furnace_no", "f_jangip_temp");
        assertThat(cast.materials().get(MATERIAL).values()).doesNotContainKey("f_jangip_temp");
        assertThat(charged.equipment()).containsKey(new EquipmentKey("FURNACE", "1호기"));
        assertThat(charged.equipment().get(new EquipmentKey("FURNACE", "1호기")).orderedWithinUnit()).isFalse();
        assertThat(charged.unitsProcessed()).isEqualTo(2);
        assertThat(charged.eventsProcessed()).isEqualTo(2);
    }

    @Test
    void keepsRangeAndQualityAxesSeparateAndDeduplicatesAlerts() throws Exception {
        OperatingRange range = compiler.compileRanges(List.of(definition(rangeJson(), false))).getFirst();
        QualityRule first = compiler.compileRules(List.of(definition(ruleJson("a", "f_jangip_gubun", "CCR", "CAUTION", true), true))).getFirst();
        QualityRule second = compiler.compileRules(List.of(definition(ruleJson("b", "furnace_no", "1호기", "CAUTION", true), true))).getFirst();
        HistoricalMonitor monitor = new HistoricalMonitor(BUNDLE, List.of(range), List.of(first, second));
        ReplayUnit unit = unit(event(MATERIAL, "FURNACE_CHARGED", "FURNACE", "1호기",
                "{\"furnace_no\":\"1호기\",\"f_jangip_gubun\":\"CCR\",\"f_jangip_temp\":145}"));

        monitor.accept(unit);
        monitor.accept(unit);

        MaterialSnapshot material = monitor.snapshot().materials().get(MATERIAL);
        assertThat(material.qualityRisk()).isEqualTo(RiskGrade.CAUTION);
        assertThat(material.rangeEvaluations()).filteredOn(value -> value.field().equals("f_jangip_temp")).singleElement()
                .extracting(value -> value.status()).isEqualTo(RangeStatus.SEVERE);
        assertThat(monitor.snapshot().alerts())
                .hasSize(3)
                .filteredOn(alert -> alert.kind() == AlertKind.QUALITY_RISK)
                .hasSize(2);
    }

    @Test
    void marksOnlyAHitRepeatedWhenThreeOfTheLastFiveDistinctMaterialsHit() throws Exception {
        QualityRule rule = compiler.compileRules(List.of(definition(
                ruleJson("a", "f_jangip_gubun", "CCR", "CAUTION", true), true))).getFirst();
        HistoricalMonitor monitor = new HistoricalMonitor(BUNDLE, List.of(), List.of(rule));

        applyCharge(monitor, "m1", "CCR");
        applyCharge(monitor, "m2", "HCR");
        applyCharge(monitor, "m3", "CCR");
        applyCharge(monitor, "m4", "HCR");
        applyCharge(monitor, "m5", "CCR");

        assertThat(monitor.snapshot().alerts()).extracting(alert -> alert.repeated())
                .containsExactly(false, false, true);
        assertThat(monitor.snapshot().alerts()).allSatisfy(alert ->
                assertThat(alert.grade()).isEqualTo(RiskGrade.CAUTION));
    }

    @Test
    void preservesApResultAsHistoricalEvidenceWithoutCreatingAnEarlyAlert() throws Exception {
        QualityRule ap = compiler.compileRules(List.of(definition(apRuleJson(), true))).getFirst();
        HistoricalMonitor monitor = new HistoricalMonitor(BUNDLE, List.of(), List.of(ap));

        monitor.accept(unit(event(MATERIAL, "AP_RECORDED_WITH_RESULT", "AP", "AP1",
                "{\"judge\":\"불량\",\"ap_line_speed\":120}")));

        MaterialSnapshot material = monitor.snapshot().materials().get(MATERIAL);
        assertThat(material.qualityRisk()).isEqualTo(RiskGrade.NORMAL);
        assertThat(material.historicalEvidenceRisk()).isEqualTo(RiskGrade.DANGER);
        assertThat(material.ruleEvaluations()).singleElement().satisfies(evaluation -> {
            assertThat(evaluation.matched()).isTrue();
            assertThat(evaluation.historicalEvidenceOnly()).isTrue();
            assertThat(evaluation.alertEligible()).isFalse();
        });
        assertThat(monitor.snapshot().alerts()).isEmpty();
    }

    @Test
    void retainsEarlierStageDangerAndRawEvidenceAfterLaterNormalStages() throws Exception {
        QualityRule danger = compiler.compileRules(List.of(definition(
                ruleJson("a", "f_jangip_gubun", "CCR", "DANGER", true), true))).getFirst();
        HistoricalMonitor monitor = new HistoricalMonitor(BUNDLE, List.of(), List.of(danger));

        applyCharge(monitor, MATERIAL, "CCR");
        monitor.accept(unit(event(MATERIAL, "PREHEAT_COMPLETE", "FURNACE", "1호기",
                "{\"f_pre_temp\":900,\"f_pre_interval\":30}")));

        MaterialSnapshot material = monitor.snapshot().materials().get(MATERIAL);
        assertThat(material.qualityRisk()).isEqualTo(RiskGrade.DANGER);
        assertThat(material.assessments()).hasSize(2);
        assertThat(material.assessments().getFirst().ruleEvaluations())
                .anySatisfy(evaluation -> assertThat(evaluation.ruleId()).isEqualTo(danger.ruleId()));
        assertThat(material.ruleEvaluations()).isEmpty();
    }

    @Test
    void equipmentSnapshotKeepsConcurrentMaterialsAsAnUnorderedBatch() throws Exception {
        HistoricalMonitor monitor = new HistoricalMonitor(BUNDLE, List.of(), List.of());
        ReplayEvent first = event("m1", "FURNACE_CHARGED", "FURNACE", "1호기",
                "{\"furnace_no\":\"1호기\",\"f_jangip_temp\":1180}");
        ReplayEvent second = new ReplayEvent(
                first.schemaVersion(), first.bundleId(), first.criteriaId(), "event-m2",
                first.replayDate(), first.replayHour(), first.batchKind(), first.batchId(),
                first.equipmentBatchId(), first.batchStep(), first.timePrecision(), "m2",
                first.equipmentType(), first.equipmentId(), "charge2", "slab2", "coil2", null,
                JSON.readTree("{\"furnace_no\":\"1호기\",\"f_jangip_temp\":1190}"));

        monitor.accept(new ReplayUnit(
                first.batchId(), first.batchStep(), first.replayDate(), first.replayHour(),
                first.batchKind(), first.timePrecision(), List.of(first, second), false));

        var equipment = monitor.snapshot().equipment().get(new EquipmentKey("FURNACE", "1호기"));
        assertThat(equipment.materialKeys()).containsExactly("m1", "m2");
        assertThat(equipment.valuesByMaterial()).containsOnlyKeys("m1", "m2");
        assertThat(equipment.orderedWithinUnit()).isFalse();
    }

    @Test
    void repetitionBadgeIsIndependentOfCsvOrderInsideAnUnorderedUnit() throws Exception {
        QualityRule rule = compiler.compileRules(List.of(definition(
                ruleJson("a", "f_jangip_gubun", "CCR", "CAUTION", true), true))).getFirst();
        HistoricalMonitor forward = new HistoricalMonitor(BUNDLE, List.of(), List.of(rule));
        HistoricalMonitor reverse = new HistoricalMonitor(BUNDLE, List.of(), List.of(rule));
        List<String[]> observations = List.of(
                new String[]{"m1", "CCR"}, new String[]{"m2", "HCR"},
                new String[]{"m3", "CCR"}, new String[]{"m4", "HCR"},
                new String[]{"m5", "CCR"}, new String[]{"m6", "HCR"});

        forward.accept(concurrentChargeUnit(observations));
        reverse.accept(concurrentChargeUnit(observations.reversed()));

        assertThat(repeatedByMaterial(forward.snapshot())).isEqualTo(repeatedByMaterial(reverse.snapshot()));
        assertThat(repeatedByMaterial(forward.snapshot()))
                .containsExactlyInAnyOrderEntriesOf(java.util.Map.of("m1", true, "m3", true, "m5", true));
    }

    @Test
    void resetsMemoryHistoryAndExportsUtf8Csv(@TempDir Path temporary) throws Exception {
        QualityRule rule = compiler.compileRules(List.of(definition(
                ruleJson("a", "f_jangip_gubun", "CCR", "CAUTION", true), true))).getFirst();
        HistoricalMonitor monitor = new HistoricalMonitor(BUNDLE, List.of(), List.of(rule));
        applyCharge(monitor, "한글-소재", "CCR");

        Path output = temporary.resolve("alerts.csv");
        monitor.exportAlerts(output);
        String csv = Files.readString(output, StandardCharsets.UTF_8);

        assertThat(csv).startsWith("bundle_id,material_key,event_stage,rule_id");
        assertThat(csv).contains("한글-소재", "QUALITY_RISK", "CAUTION");

        monitor.reset();
        assertThat(monitor.snapshot().materials()).isEmpty();
        assertThat(monitor.snapshot().equipment()).isEmpty();
        assertThat(monitor.snapshot().alerts()).isEmpty();
    }

    private static void applyCharge(HistoricalMonitor monitor, String material, String method) throws Exception {
        monitor.accept(unit(event(material, "FURNACE_CHARGED", "FURNACE", "1호기",
                "{\"furnace_no\":\"1호기\",\"f_jangip_gubun\":\"" + method + "\",\"f_jangip_temp\":1180}")));
    }

    private static ReplayUnit unit(ReplayEvent event) {
        return new ReplayUnit(event.batchId(), event.batchStep(), event.replayDate(), event.replayHour(),
                event.batchKind(), event.timePrecision(), List.of(event), false);
    }

    private static ReplayUnit concurrentChargeUnit(List<String[]> observations) throws Exception {
        ReplayEvent template = event(observations.getFirst()[0], "FURNACE_CHARGED", "FURNACE", "1호기",
                "{\"furnace_no\":\"1호기\",\"f_jangip_gubun\":\"" + observations.getFirst()[1] + "\"}");
        java.util.ArrayList<ReplayEvent> events = new java.util.ArrayList<>();
        for (String[] observation : observations) {
            events.add(new ReplayEvent(
                    template.schemaVersion(), template.bundleId(), template.criteriaId(), "event-" + observation[0],
                    template.replayDate(), template.replayHour(), template.batchKind(), template.batchId(),
                    template.equipmentBatchId(), template.batchStep(), template.timePrecision(), observation[0],
                    template.equipmentType(), template.equipmentId(), "charge-" + observation[0],
                    "slab-" + observation[0], "coil-" + observation[0], null,
                    JSON.readTree("{\"furnace_no\":\"1호기\",\"f_jangip_gubun\":\"" + observation[1] + "\"}")));
        }
        return new ReplayUnit(template.batchId(), template.batchStep(), template.replayDate(), template.replayHour(),
                template.batchKind(), template.timePrecision(), events, false);
    }

    private static java.util.Map<String, Boolean> repeatedByMaterial(MonitorSnapshot snapshot) {
        return snapshot.alerts().stream().collect(java.util.stream.Collectors.toMap(
                alert -> alert.materialKey(), alert -> alert.repeated()));
    }

    private static ReplayEvent event(
            String material, String stage, String equipmentType, String equipmentId, String values) throws Exception {
        String batchId = "batch-" + material + "-" + stage;
        return new ReplayEvent(
                "sfep-replay-events/v1", BUNDLE, "criteria", "event-" + material + "-" + stage,
                "2025-01-01", equipmentType.equals("FURNACE") ? 1 : null,
                stage.equals("AP_RECORDED_WITH_RESULT") ? "AP_DAY" :
                        stage.equals("CAST_RECORDED") ? "CAST_DAY" : "FURNACE_HOUR",
                batchId, equipmentType.equals("FURNACE") ? "equipment-" + material : null,
                stage, equipmentType.equals("FURNACE") ? "HOUR_BUCKET" : "DAY",
                material, equipmentType, equipmentId, "charge", "slab", "coil", null,
                JSON.readTree(values));
    }

    private static String rangeJson() {
        return """
                {"ruleId":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
                 "field":"f_jangip_temp","fieldRole":"DIRECT_OPERATION","firstAvailableStage":"FURNACE_CHARGED",
                 "equipmentType":"FURNACE","equipmentId":"1호기","contextLevel":0,"context":{},"support":2500,
                 "median":120,"p01":100,"p05":110,"p95":130,"p99":140,"lowerTailEnabled":true,"upperTailEnabled":true}
                """;
    }

    private static String ruleJson(
            String suffix, String field, String value, String grade, boolean early) {
        return """
                {"ruleId":"sha256:%s","analysisFamily":"CATEGORICAL","evidenceFamily":"CHARGE",
                 "firstAvailableStage":"FURNACE_CHARGED","equipmentType":"FURNACE","applicationScope":"PROCESS_GLOBAL",
                 "equipmentId":"ALL","fieldNames":["%s"],"predicate":{"allOf":[{"field":"%s","type":"CATEGORY_IN",
                 "lower":null,"lowerInclusive":null,"upper":null,"upperInclusive":null,"values":["%s"]}]},
                 "applicationContext":{},"adjustmentLevel":0,"adjustmentFieldsDropped":[],"adjustmentKind":"STRATIFIED",
                 "grade":"%s","earlyWarningEligible":%s,"discovery":%s,"confirmation":%s,"displayMergeRuleIds":[]}
                """.formatted(suffix.repeat(64), field, field, value, grade, early, metric(), metric());
    }

    private static String apRuleJson() {
        return """
                {"ruleId":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
                 "analysisFamily":"CATEGORICAL","evidenceFamily":"AP","firstAvailableStage":"AP_RECORDED_WITH_RESULT",
                 "equipmentType":"AP","applicationScope":"PROCESS_GLOBAL","equipmentId":"ALL","fieldNames":["judge"],
                 "predicate":{"allOf":[{"field":"judge","type":"CATEGORY_IN","lower":null,"lowerInclusive":null,
                 "upper":null,"upperInclusive":null,"values":["불량"]}]},"applicationContext":{},"adjustmentLevel":0,
                 "adjustmentFieldsDropped":[],"adjustmentKind":"STRATIFIED","grade":"DANGER","earlyWarningEligible":false,
                 "discovery":%s,"confirmation":%s,"displayMergeRuleIds":[]}
                """.formatted(metric(), metric());
    }

    private static String metric() {
        return "{\"support\":400,\"defects\":10,\"crudeRate\":0.025,\"crudeRateCiLower\":0.01,\"crudeRateCiUpper\":0.05," +
                "\"adjustedRate\":0.025,\"comparatorAdjustedRate\":0.01,\"riskDifference\":0.015,\"relativeRisk\":2.5," +
                "\"relativeRiskCiLower\":1.2,\"relativeRiskCiUpper\":4.0,\"pValue\":0.01,\"qValue\":0.02,\"reasonCode\":\"NONE\"}";
    }

    private static JsonNode definition(String raw, boolean qualityRule) throws Exception {
        com.fasterxml.jackson.databind.node.ObjectNode node =
                (com.fasterxml.jackson.databind.node.ObjectNode) JSON.readTree(raw);
        node.put("ruleId", qualityRule
                ? com.sfep.equipmentmonitor.risk.RiskDefinitionIds.qualityRuleId(node)
                : com.sfep.equipmentmonitor.risk.RiskDefinitionIds.operatingRangeId(node));
        return node;
    }
}
