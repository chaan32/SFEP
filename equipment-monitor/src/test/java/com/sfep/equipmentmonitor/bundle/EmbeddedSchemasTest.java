package com.sfep.equipmentmonitor.bundle;

import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class EmbeddedSchemasTest {
    @Test
    void embedsExactlyTwoClosedSevenRoleProfilesFromTheNormativeRoots() throws Exception {
        Path contractRoot = Path.of(System.getProperty("sfep.repo-root"))
                .resolve("contracts/equipment-monitor");
        Map<SchemaRole, String> fileNames = Map.of(
                SchemaRole.BUNDLE_MANIFEST, "bundle_manifest.schema.json",
                SchemaRole.ANALYSIS_CONFIG, "analysis_config.schema.json",
                SchemaRole.PRODUCER_RUNTIME, "producer_runtime.schema.json",
                SchemaRole.EQUIPMENT_OPERATING_RANGES, "equipment_operating_ranges.schema.json",
                SchemaRole.QUALITY_RISK_INTERVALS, "quality_risk_intervals.schema.json",
                SchemaRole.REPLAY_EVENTS, "replay_event_row.schema.json",
                SchemaRole.ANALYSIS_SUMMARY, "analysis_summary.schema.json");

        assertThat(BundleContracts.all()).containsOnlyKeys(
                "sfep-equipment-bundle/v1", "sfep-equipment-bundle/v2");
        for (String manifestVersion : BundleContracts.all().keySet()) {
            BundleContract contract = BundleContracts.require(manifestVersion);
            String profile = manifestVersion.endsWith("/v1") ? "v1" : "v2";
            assertThat(contract.schemas()).hasSize(7);
            assertThat(contract.schemas().keySet()).containsExactlyInAnyOrder(SchemaRole.values());
            for (SchemaRole role : SchemaRole.values()) {
                String normativeProfile = profile.equals("v2") && switch (role) {
                    case PRODUCER_RUNTIME, EQUIPMENT_OPERATING_RANGES,
                            QUALITY_RISK_INTERVALS, REPLAY_EVENTS -> true;
                    default -> false;
                } ? "v1" : profile;
                byte[] normative = Files.readAllBytes(
                        contractRoot.resolve(normativeProfile).resolve(fileNames.get(role)));
                SchemaDocument embedded = contract.schema(role);
                assertThat(embedded.bytes())
                        .as(manifestVersion + " " + role)
                        .containsExactly(normative);
                assertThat(embedded.sha256()).isEqualTo(Digests.sha256Uri(normative));
            }
        }

        BundleContract v1 = BundleContracts.require("sfep-equipment-bundle/v1");
        BundleContract v2 = BundleContracts.require("sfep-equipment-bundle/v2");
        assertThat(v2.schema(SchemaRole.PRODUCER_RUNTIME))
                .isSameAs(v1.schema(SchemaRole.PRODUCER_RUNTIME));
        assertThat(v2.schema(SchemaRole.EQUIPMENT_OPERATING_RANGES))
                .isSameAs(v1.schema(SchemaRole.EQUIPMENT_OPERATING_RANGES));
        assertThat(v2.schema(SchemaRole.QUALITY_RISK_INTERVALS))
                .isSameAs(v1.schema(SchemaRole.QUALITY_RISK_INTERVALS));
        assertThat(v2.schema(SchemaRole.REPLAY_EVENTS))
                .isSameAs(v1.schema(SchemaRole.REPLAY_EVENTS));
        assertThatThrownBy(() -> v2.schemas().put(
                SchemaRole.ANALYSIS_CONFIG, v1.schema(SchemaRole.ANALYSIS_CONFIG)))
                .isInstanceOf(UnsupportedOperationException.class);
        assertThatThrownBy(() -> BundleContracts.all().clear())
                .isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    void contractProfilesOwnTheExactArtifactVersionMatrixAndRejectUnknownVersions() {
        BundleContract v1 = BundleContracts.require("sfep-equipment-bundle/v1");
        BundleContract v2 = BundleContracts.require("sfep-equipment-bundle/v2");

        assertThat(v1.artifactVersions()).containsExactlyInAnyOrderEntriesOf(Map.of(
                ArtifactRole.ANALYSIS_CONFIG, "sfep-analysis-config/v1",
                ArtifactRole.PRODUCER_RUNTIME, "sfep-producer-runtime/v1",
                ArtifactRole.EQUIPMENT_OPERATING_RANGES, "sfep-operating-ranges/v1",
                ArtifactRole.QUALITY_RISK_INTERVALS, "sfep-quality-rules/v1",
                ArtifactRole.REPLAY_EVENTS, "sfep-replay-events/v1",
                ArtifactRole.ANALYSIS_SUMMARY, "sfep-analysis-summary/v1"));
        assertThat(v2.artifactVersions()).containsExactlyInAnyOrderEntriesOf(Map.of(
                ArtifactRole.ANALYSIS_CONFIG, "sfep-analysis-config/v2",
                ArtifactRole.PRODUCER_RUNTIME, "sfep-producer-runtime/v1",
                ArtifactRole.EQUIPMENT_OPERATING_RANGES, "sfep-operating-ranges/v1",
                ArtifactRole.QUALITY_RISK_INTERVALS, "sfep-quality-rules/v1",
                ArtifactRole.REPLAY_EVENTS, "sfep-replay-events/v1",
                ArtifactRole.ANALYSIS_SUMMARY, "sfep-analysis-summary/v2"));
        assertThat(v1.criteriaIdNamespace()).isEqualTo("sfep-criteria-id/v1");
        assertThat(v2.criteriaIdNamespace()).isEqualTo("sfep-criteria-id/v1");
        assertThat(v1.bundleIdNamespace()).isEqualTo("sfep-bundle-id/v1");
        assertThat(v2.bundleIdNamespace()).isEqualTo("sfep-bundle-id/v1");
        assertThatThrownBy(() -> BundleContracts.require("sfep-equipment-bundle/v3"))
                .isInstanceOf(BundleLoadException.class)
                .hasMessageStartingWith("BUNDLE_CONTRACT_UNSUPPORTED:");
    }
}
