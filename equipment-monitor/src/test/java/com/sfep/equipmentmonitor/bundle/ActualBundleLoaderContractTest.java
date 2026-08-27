package com.sfep.equipmentmonitor.bundle;

import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

class ActualBundleLoaderContractTest {
    @Test
    void projectsTheSealedActualBundleManagementEvidenceWhenConfigured() throws Exception {
        String configured = System.getenv("SFEP_ACTUAL_BUNDLE");
        assumeTrue(configured != null && !configured.isBlank(), "local-only actual bundle not configured");

        LoadedBundle loaded = new BundleLoader().load(Path.of(configured).toRealPath());
        LoadedBundle.ManagementProjection management = loaded.management();

        assertThat(loaded.bundleId()).isEqualTo(
                "sha256:65d535350ef2d7c5abf6372fdfe7b364c62a6c0af60b01946a23c4659c1ee82a");
        assertThat(management.sourceHashes()).containsExactlyInAnyOrderEntriesOf(Map.of(
                LoadedBundle.SourceRole.SM_CC,
                "sha256:0cd3e91428c005dae1785b9af01d30e3d08230e2058c68693c9aa7ffee043075",
                LoadedBundle.SourceRole.FUR_HR,
                "sha256:c2bb0b503ec30b0e01e00d2bd88fde536479de59aebf1eae84131380d58f3bcf",
                LoadedBundle.SourceRole.AP,
                "sha256:ff4572a1302459787ddca7458857864182d969e45b6a81edc4241bc47549801d"));
        assertThat(management.artifactHashes()).containsExactlyInAnyOrderEntriesOf(Map.of(
                ArtifactRole.ANALYSIS_CONFIG,
                "sha256:bb2610971dc1b3adcb4e93b9d26a50fdb4070292ffc4bf4a26ce7cdde0f5fb0b",
                ArtifactRole.PRODUCER_RUNTIME,
                "sha256:3c1daabb3e868fc6cebc75023980fcb71d947996170de6d8e58cd06b18231407",
                ArtifactRole.EQUIPMENT_OPERATING_RANGES,
                "sha256:8bd1e05d4b33a0d6e4fda59d135cfa0b6a5b8f00779a6c82c307d09d95751682",
                ArtifactRole.QUALITY_RISK_INTERVALS,
                "sha256:bd2e2f6fa8c0233e6d658d7fb84b2a717d17a7fbdc74bd47733024f412cb0954",
                ArtifactRole.REPLAY_EVENTS,
                "sha256:d2b5a8029b98f60181460c75bf5e55d8568c4dd46f32752e3dfd5e200ecbd599",
                ArtifactRole.ANALYSIS_SUMMARY,
                "sha256:fc3bd17615f1500e36da11394a48acdffab53e18935a600a0257814fd41caadc"));
        assertThat(management.quarantineCounts()).containsExactlyInAnyOrderEntriesOf(Map.of(
                LoadedBundle.QuarantineReason.DUPLICATE_AP_PROD_ID, 4L,
                LoadedBundle.QuarantineReason.DUPLICATE_FUR_HR_COIL_KEY, 4L,
                LoadedBundle.QuarantineReason.DUPLICATE_FUR_HR_KEY, 6L,
                LoadedBundle.QuarantineReason.DUPLICATE_SM_CC_KEY, 6L,
                LoadedBundle.QuarantineReason.MISSING_AP_PROD_ID, 2L,
                LoadedBundle.QuarantineReason.UNLINKED_AP, 6L,
                LoadedBundle.QuarantineReason.UNLINKED_FUR_HR, 11L,
                LoadedBundle.QuarantineReason.UNLINKED_SM_CC, 12L));
        assertThat(management.labelCensoringCounts()).containsExactlyInAnyOrderEntriesOf(Map.of(
                LoadedBundle.LabelCensoringReason.AP_UNLINKED, 5L,
                LoadedBundle.LabelCensoringReason.LABEL_NOT_YET_AVAILABLE, 10_594L));
        assertThat(management.chargePurgeCounts())
                .isEqualTo(new LoadedBundle.ChargePurgeCounts(
                        new LoadedBundle.PurgeCount(145, 641),
                        new LoadedBundle.PurgeCount(46, 203)));
    }
}
