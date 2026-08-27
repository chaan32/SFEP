package com.sfep.equipmentmonitor.risk;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.StreamSupport;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

class ActualRiskDefinitionsTest {
    private static final ObjectMapper JSON = new ObjectMapper();

    @Test
    void compilesTheLocallySealedActualDefinitionsAndFindsTheCcrDangerRule() throws Exception {
        String configured = System.getenv("SFEP_ACTUAL_BUNDLE");
        assumeTrue(configured != null && !configured.isBlank(), "local-only actual bundle not configured");
        Path bundle = Path.of(configured);
        JsonNode rangesJson = JSON.readTree(Files.readAllBytes(bundle.resolve("equipment_operating_ranges.json")));
        JsonNode rulesJson = JSON.readTree(Files.readAllBytes(bundle.resolve("quality_risk_intervals.json")));
        RiskDefinitionCompiler compiler = new RiskDefinitionCompiler();

        List<OperatingRange> ranges = compiler.compileRanges(elements(rangesJson.path("ranges")));
        List<QualityRule> rules = compiler.compileRules(elements(rulesJson.path("rules")));

        assertThat(ranges).hasSize(424);
        assertThat(rules).hasSize(313);
        assertThat(rules).filteredOn(rule -> rule.grade() == RiskGrade.DANGER).hasSize(2);
        assertThat(rules).anySatisfy(rule -> {
            assertThat(rule.ruleId()).isEqualTo(
                    "sha256:a09f13dea430e6292665bd7c2bb5c4dfc5c411808f7adf015a2d33ab612c93bc");
            assertThat(rule.earlyWarningEligible()).isTrue();
            assertThat(rule.evidenceFamily()).isEqualTo(EvidenceFamily.CHARGE);
        });
    }

    private static List<JsonNode> elements(JsonNode array) {
        return StreamSupport.stream(array.spliterator(), false).toList();
    }
}
