package com.sfep.equipmentmonitor.risk;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class RuleCompilerEvaluatorTest {
    private static final ObjectMapper JSON = new ObjectMapper();
    private final RiskDefinitionCompiler compiler = new RiskDefinitionCompiler();
    private final QualityRuleEvaluator evaluator = new QualityRuleEvaluator();

    @Test
    void compilesEveryTrackedGoldenDefinition() throws Exception {
        Path root = Path.of(System.getProperty("sfep.repo-root"));
        JsonNode rangeArtifact = JSON.readTree(Files.readAllBytes(root.resolve(
                "contracts/equipment-monitor/v1/golden-bundle/equipment_operating_ranges.json")));
        JsonNode ruleArtifact = JSON.readTree(Files.readAllBytes(root.resolve(
                "contracts/equipment-monitor/v1/golden-bundle/quality_risk_intervals.json")));

        List<OperatingRange> ranges = compiler.compileRanges(elements(rangeArtifact.path("ranges")));
        List<QualityRule> rules = compiler.compileRules(elements(ruleArtifact.path("rules")));

        assertThat(ranges).isEmpty();
        assertThat(rules).hasSize(165);
        assertThatThrownBy(() -> rules.clear()).isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    void compilesAndMatchesTheSealedCcrDangerRuleWithoutLosingEvidence() throws Exception {
        JsonNode sealedRule = JSON.readTree("""
                {
                  "ruleId":"sha256:a09f13dea430e6292665bd7c2bb5c4dfc5c411808f7adf015a2d33ab612c93bc",
                  "analysisFamily":"CATEGORICAL","evidenceFamily":"CHARGE",
                  "firstAvailableStage":"FURNACE_CHARGED","equipmentType":"FURNACE",
                  "applicationScope":"PROCESS_GLOBAL","equipmentId":"ALL",
                  "fieldNames":["f_jangip_gubun"],
                  "predicate":{"allOf":[{"field":"f_jangip_gubun","type":"CATEGORY_IN","lower":null,"lowerInclusive":null,"upper":null,"upperInclusive":null,"values":["CCR"]}]},
                  "applicationContext":{},"adjustmentLevel":0,
                  "adjustmentFieldsDropped":["f_jangip_gubun","f_jangip_gubun_band","f_jangip_temp"],
                  "adjustmentKind":"STRATIFIED","grade":"DANGER","earlyWarningEligible":true,
                  "discovery":{"support":1412,"defects":28,"crudeRate":0.019830028328611898,"crudeRateCiLower":0.013754940958849115,"crudeRateCiUpper":0.028510708523218072,"adjustedRate":0.019077459878869926,"comparatorAdjustedRate":0.005434744985512071,"riskDifference":0.013642714893357855,"relativeRisk":2.198252291688047,"relativeRiskCiLower":1.5889550066757179,"relativeRiskCiUpper":3.073915017304279,"pValue":0.00007575116722941069,"qValue":0.0012877698428999816,"reasonCode":"NONE"},
                  "confirmation":{"support":335,"defects":5,"crudeRate":0.014925373134328358,"crudeRateCiLower":0.006391635257906626,"crudeRateCiUpper":0.034457730899552445,"adjustedRate":0.00852360910694244,"comparatorAdjustedRate":0.002697417079137509,"riskDifference":0.005826192027804931,"relativeRisk":2.093261482825261,"relativeRiskCiLower":null,"relativeRiskCiUpper":null,"pValue":null,"qValue":null,"reasonCode":"NONE"},
                  "displayMergeRuleIds":[]
                }
                """);

        QualityRule rule = compiler.compileRules(List.of(sealedRule)).getFirst();
        RiskEvent event = RiskEvent.of(ProcessStage.FURNACE_CHARGED, EquipmentType.FURNACE, "1호기",
                Map.of("f_jangip_gubun", RiskScalar.of("CCR"), "f_jangip_temp", RiskScalar.of(1180)));

        RuleEvaluation evaluation = evaluator.evaluate(event, List.of(rule)).getFirst();

        assertThat(evaluation.matched()).isTrue();
        assertThat(evaluation.grade()).isEqualTo(RiskGrade.DANGER);
        assertThat(evaluation.alertEligible()).isTrue();
        assertThat(evaluation.discovery()).isEqualTo(rule.discovery());
        assertThat(evaluation.confirmation()).isEqualTo(rule.confirmation());
        assertThat(evaluation.discovery().support()).isEqualTo(1412);
        assertThat(evaluation.discovery().relativeRisk()).isEqualTo(2.198252291688047);
    }

    @Test
    void respectsInclusiveNumericBoundsCategoryAndTwoTermInteraction() throws Exception {
        QualityRule interaction = compiler.compileRules(List.of(rule(
                "INTERACTION", "CAUTION", "FURNACE_CHARGED", "FURNACE", "EQUIPMENT_SPECIFIC", "1호기", true,
                "[\n" +
                        "{\"field\":\"f_jangip_temp\",\"type\":\"NUMERIC_INTERVAL\",\"lower\":1100,\"lowerInclusive\":true,\"upper\":1200,\"upperInclusive\":false,\"values\":null},\n" +
                        "{\"field\":\"f_jangip_gubun\",\"type\":\"CATEGORY_IN\",\"lower\":null,\"lowerInclusive\":null,\"upper\":null,\"upperInclusive\":null,\"values\":[\"CCR\",\"HCR\"]}\n]",
                "{\"furnace_no\":\"1호기\"}"))).getFirst();

        RiskEvent lower = event("1호기", Map.of("f_jangip_temp", 1100, "f_jangip_gubun", "CCR", "furnace_no", "1호기"));
        RiskEvent upper = event("1호기", Map.of("f_jangip_temp", 1200, "f_jangip_gubun", "CCR", "furnace_no", "1호기"));

        assertThat(evaluator.evaluate(lower, List.of(interaction)).getFirst().matched()).isTrue();
        assertThat(evaluator.evaluate(upper, List.of(interaction)).getFirst().matched()).isFalse();
        assertThat(evaluator.evaluate(event("2호기", lower.rawValues()), List.of(interaction))).isEmpty();
    }

    @Test
    void reportsMissingPredicateDataAndNeverPromotesMultipleCautionsToDanger() throws Exception {
        QualityRule first = compiler.compileRules(List.of(rule(
                "NUMERIC", "CAUTION", "FURNACE_CHARGED", "FURNACE", "PROCESS_GLOBAL", "ALL", true,
                "[{\"field\":\"f_jangip_temp\",\"type\":\"NUMERIC_INTERVAL\",\"lower\":1100,\"lowerInclusive\":true,\"upper\":1300,\"upperInclusive\":true,\"values\":null}]", "{}"))).getFirst();
        QualityRule second = compiler.compileRules(List.of(rule(
                "CATEGORICAL", "CAUTION", "FURNACE_CHARGED", "FURNACE", "PROCESS_GLOBAL", "ALL", true,
                "[{\"field\":\"f_jangip_gubun\",\"type\":\"CATEGORY_IN\",\"lower\":null,\"lowerInclusive\":null,\"upper\":null,\"upperInclusive\":null,\"values\":[\"CCR\"]}]", "{}"))).getFirst();

        RuleEvaluation missing = evaluator.evaluate(event("1호기", Map.of("f_jangip_gubun", "CCR")), List.of(first)).getFirst();
        RuleEvaluationSummary twoCautions = evaluator.summarize(
                evaluator.evaluate(event("1호기", Map.of("f_jangip_temp", 1150, "f_jangip_gubun", "CCR")), List.of(first, second)));

        assertThat(missing.status()).isEqualTo(RuleMatchStatus.DATA_MISSING);
        assertThat(missing.alertEligible()).isFalse();
        assertThat(twoCautions.highestMatchedGrade()).isEqualTo(RiskGrade.CAUTION);
        assertThat(twoCautions.matchedCount()).isEqualTo(1);
    }

    @Test
    void ignoresUnconfirmedForReplayAndCountsCorrelatedEvidenceOnlyOnce() throws Exception {
        QualityRule unconfirmed = compiler.compileRules(List.of(rule(
                "CATEGORICAL", "UNCONFIRMED", "FURNACE_CHARGED", "FURNACE", "PROCESS_GLOBAL", "ALL", true,
                "[{\"field\":\"f_jangip_gubun\",\"type\":\"CATEGORY_IN\",\"lower\":null,\"lowerInclusive\":null,\"upper\":null,\"upperInclusive\":null,\"values\":[\"CCR\"]}]", "{}"))).getFirst();
        QualityRule first = compiler.compileRules(List.of(rule(
                "CATEGORICAL", "CAUTION", "FURNACE_CHARGED", "FURNACE", "PROCESS_GLOBAL", "ALL", true,
                "[{\"field\":\"f_jangip_gubun\",\"type\":\"CATEGORY_IN\",\"lower\":null,\"lowerInclusive\":null,\"upper\":null,\"upperInclusive\":null,\"values\":[\"CCR\"]}]", "{}"))).getFirst();
        QualityRule second = compiler.compileRules(List.of(rule(
                "CATEGORICAL", "CAUTION", "FURNACE_CHARGED", "FURNACE", "PROCESS_GLOBAL", "ALL", true,
                "[{\"field\":\"furnace_no\",\"type\":\"CATEGORY_IN\",\"lower\":null,\"lowerInclusive\":null,\"upper\":null,\"upperInclusive\":null,\"values\":[\"1호기\"]}]", "{}"))).getFirst();
        RiskEvent event = event("1호기", Map.of("f_jangip_gubun", "CCR", "furnace_no", "1호기"));

        assertThat(evaluator.summarize(evaluator.evaluate(event, List.of(unconfirmed))).highestMatchedGrade())
                .isEqualTo(RiskGrade.NORMAL);
        assertThat(evaluator.summarize(evaluator.evaluate(event, List.of(first, second))).matchedCount())
                .isEqualTo(1);
    }

    @Test
    void keepsApResultAsHistoryEvidenceButNeverAsAnEarlyAlert() throws Exception {
        QualityRule ap = compiler.compileRules(List.of(rule(
                "CATEGORICAL", "DANGER", "AP_RECORDED_WITH_RESULT", "AP", "PROCESS_GLOBAL", "ALL", false,
                "[{\"field\":\"judge\",\"type\":\"CATEGORY_IN\",\"lower\":null,\"lowerInclusive\":null,\"upper\":null,\"upperInclusive\":null,\"values\":[\"불량\"]}]", "{}"))).getFirst();
        RiskEvent event = RiskEvent.of(ProcessStage.AP_RECORDED_WITH_RESULT, EquipmentType.AP, "AP1",
                Map.of("judge", RiskScalar.of("불량")));

        RuleEvaluation evaluation = evaluator.evaluate(event, List.of(ap)).getFirst();

        assertThat(evaluation.matched()).isTrue();
        assertThat(evaluation.grade()).isEqualTo(RiskGrade.DANGER);
        assertThat(evaluation.alertEligible()).isFalse();
        assertThat(evaluation.historicalEvidenceOnly()).isTrue();
    }

    @Test
    void compilerFailsClosedOnUnknownFieldsInvalidPredicatesAndMutableInputs() throws Exception {
        JsonNode unknown = rule("CATEGORICAL", "DANGER", "FURNACE_CHARGED", "FURNACE", "PROCESS_GLOBAL", "ALL", true,
                "[{\"field\":\"f_jangip_gubun\",\"type\":\"CATEGORY_IN\",\"lower\":null,\"lowerInclusive\":null,\"upper\":null,\"upperInclusive\":null,\"values\":[\"CCR\"]}]", "{}");
        ((com.fasterxml.jackson.databind.node.ObjectNode) unknown).put("unexpected", true);
        JsonNode threeTerms = rule("INTERACTION", "DANGER", "FURNACE_CHARGED", "FURNACE", "PROCESS_GLOBAL", "ALL", true,
                "[{\"field\":\"a\",\"type\":\"CATEGORY_IN\",\"lower\":null,\"lowerInclusive\":null,\"upper\":null,\"upperInclusive\":null,\"values\":[1]},{\"field\":\"b\",\"type\":\"CATEGORY_IN\",\"lower\":null,\"lowerInclusive\":null,\"upper\":null,\"upperInclusive\":null,\"values\":[1]},{\"field\":\"c\",\"type\":\"CATEGORY_IN\",\"lower\":null,\"lowerInclusive\":null,\"upper\":null,\"upperInclusive\":null,\"values\":[1]}]", "{}");

        assertThatThrownBy(() -> compiler.compileRules(List.of(unknown)))
                .isInstanceOf(RiskDefinitionException.class).hasMessageContaining("unexpected");
        assertThatThrownBy(() -> compiler.compileRules(List.of(threeTerms)))
                .isInstanceOf(RiskDefinitionException.class).hasMessageContaining("allOf");

        java.util.HashMap<String, RiskScalar> values = new java.util.HashMap<>();
        values.put("f_jangip_gubun", RiskScalar.of("CCR"));
        RiskEvent immutable = RiskEvent.of(ProcessStage.FURNACE_CHARGED, EquipmentType.FURNACE, "1호기", values);
        values.clear();
        assertThat(immutable.values()).containsKey("f_jangip_gubun");
        assertThatThrownBy(() -> immutable.values().put("x", RiskScalar.of(1)))
                .isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    void compilerRejectsIdentityDriftAndDuplicateRuleIds() throws Exception {
        com.fasterxml.jackson.databind.node.ObjectNode original = (com.fasterxml.jackson.databind.node.ObjectNode) rule(
                "CATEGORICAL", "DANGER", "FURNACE_CHARGED", "FURNACE", "PROCESS_GLOBAL", "ALL", true,
                "[{\"field\":\"f_jangip_gubun\",\"type\":\"CATEGORY_IN\",\"lower\":null,\"lowerInclusive\":null,\"upper\":null,\"upperInclusive\":null,\"values\":[\"CCR\"]}]", "{}");
        com.fasterxml.jackson.databind.node.ObjectNode drifted = original.deepCopy();
        ((com.fasterxml.jackson.databind.node.ArrayNode) drifted.path("predicate").path("allOf").get(0).path("values"))
                .set(0, JSON.getNodeFactory().textNode("HCR"));

        assertThatThrownBy(() -> compiler.compileRules(List.of(drifted)))
                .isInstanceOf(RiskDefinitionException.class).hasMessageContaining("identity mismatch");
        assertThatThrownBy(() -> compiler.compileRules(List.of(original, original.deepCopy())))
                .isInstanceOf(RiskDefinitionException.class).hasMessageContaining("duplicate quality ruleId");
    }

    private static RiskEvent event(String equipmentId, Map<String, ?> raw) {
        return RiskEvent.of(ProcessStage.FURNACE_CHARGED, EquipmentType.FURNACE, equipmentId,
                raw.entrySet().stream().collect(java.util.stream.Collectors.toUnmodifiableMap(
                        Map.Entry::getKey, entry -> RiskScalar.of(entry.getValue()))));
    }

    private static JsonNode rule(
            String family, String grade, String stage, String equipmentType, String scope,
            String equipmentId, boolean early, String terms, String context) throws Exception {
        com.fasterxml.jackson.databind.node.ObjectNode node = (com.fasterxml.jackson.databind.node.ObjectNode) JSON.readTree("""
                {"ruleId":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                 "analysisFamily":"%s","evidenceFamily":"CHARGE","firstAvailableStage":"%s",
                 "equipmentType":"%s","applicationScope":"%s","equipmentId":"%s",
                 "fieldNames":[%s],"predicate":{"allOf":%s},"applicationContext":%s,
                 "adjustmentLevel":0,"adjustmentFieldsDropped":[],"adjustmentKind":"UNADJUSTED_FALLBACK",
                 "grade":"%s","earlyWarningEligible":%s,
                 "discovery":%s,"confirmation":%s,"displayMergeRuleIds":[]}
                """.formatted(family, stage, equipmentType, scope, equipmentId,
                family.equals("INTERACTION") ? "\"f_jangip_temp\",\"f_jangip_gubun\"" :
                        (terms.contains("f_jangip_temp") ? "\"f_jangip_temp\"" :
                                terms.contains("furnace_no") ? "\"furnace_no\"" :
                                        terms.contains("judge") ? "\"judge\"" : "\"f_jangip_gubun\""),
                terms, context, grade, early, metric(), metric()));
        node.put("ruleId", RiskDefinitionIds.qualityRuleId(node));
        return node;
    }

    private static String metric() {
        return "{\"support\":400,\"defects\":4,\"crudeRate\":0.01,\"crudeRateCiLower\":0.003,\"crudeRateCiUpper\":0.025,\"adjustedRate\":0.01,\"comparatorAdjustedRate\":0.005,\"riskDifference\":0.005,\"relativeRisk\":2.0,\"relativeRiskCiLower\":1.1,\"relativeRiskCiUpper\":3.0,\"pValue\":0.01,\"qValue\":0.02,\"reasonCode\":\"NONE\"}";
    }

    private static List<JsonNode> elements(JsonNode array) {
        return java.util.stream.StreamSupport.stream(array.spliterator(), false).toList();
    }
}
