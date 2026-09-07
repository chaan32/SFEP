package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BundleLoaderIntegrationTest {
    @TempDir
    Path temporary;

    @Test
    void loadsTheTrackedGoldenBundleWithoutRetainingReplayRows() throws Exception {
        Path bundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("golden"));

        LoadedBundle loaded = new BundleLoader().load(bundle);

        assertThat(loaded.bundleId()).isEqualTo(GoldenBundleFixture.BUNDLE_ID);
        assertThat(loaded.criteriaId()).isEqualTo(GoldenBundleFixture.CRITERIA_ID);
        assertThat(loaded.summary().asOf()).isEqualTo("2025-02-20");
        assertThat(loaded.summary().evaluationMode()).isEqualTo("LOCKED_RETROSPECTIVE_HOLDOUT");
        assertThat(loaded.summary().holdoutTotal()).isEqualTo(2);
        assertThat(loaded.replay().path()).isEqualTo(bundle.resolve("replay_events.csv").toRealPath());
        assertThat(loaded.replay().metadata().rowCount()).isEqualTo(95);
        assertThat(loaded.replay().metadata().firstEventId())
                .isEqualTo("sha256:b80d26659b6f6c3ab842b840ce7c8f11eb621ce5292eb14156732c5d91f3c324");
        assertThat(loaded.replay().metadata().lastEventId())
                .isEqualTo("sha256:71a6ea2d6ce6e0f65f4b2188ce5e692df5e63408eb868b18a9f292419677a0a1");
        assertThat(loaded.ranges().size()).isZero();
        assertThat(loaded.rules()).isNotEmpty();
    }

    @Test
    void projectsVerifiedGoldenManagementEvidenceAsImmutableTypedValues() throws Exception {
        Path bundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("management"));

        LoadedBundle.ManagementProjection management = new BundleLoader().load(bundle).management();

        assertThat(management.sourceHashes()).containsExactlyInAnyOrderEntriesOf(Map.of(
                LoadedBundle.SourceRole.SM_CC,
                "sha256:c3bba5c7235166b6693ff657a25797933ea9d83c6392437af9b5141f4e56a68c",
                LoadedBundle.SourceRole.FUR_HR,
                "sha256:973e864ed996ca48c44a00943efc0ea51a878b94b8aac7452c7254a572fe7e67",
                LoadedBundle.SourceRole.AP,
                "sha256:efaef9f283a28e63dff54b26bc3dd42c6a72e20f0b75d2fa13a6ab47f41405c9"));
        assertThat(management.artifactHashes()).containsExactlyInAnyOrderEntriesOf(Map.of(
                ArtifactRole.ANALYSIS_CONFIG,
                "sha256:bb2610971dc1b3adcb4e93b9d26a50fdb4070292ffc4bf4a26ce7cdde0f5fb0b",
                ArtifactRole.PRODUCER_RUNTIME,
                "sha256:3c1daabb3e868fc6cebc75023980fcb71d947996170de6d8e58cd06b18231407",
                ArtifactRole.EQUIPMENT_OPERATING_RANGES,
                "sha256:bd8ab5dc79a1a7e97d64132dd3669befbe3fa28493a57eff5918818933526200",
                ArtifactRole.QUALITY_RISK_INTERVALS,
                "sha256:3b0a85925ab1ae7b42d70989c32fad3ea3b881cf9647a6a13a72db3d42b7eb30",
                ArtifactRole.REPLAY_EVENTS,
                "sha256:10b39aac41f317210074e42b44a0e1ab6f521b7c432647edf72be80a4e0fef8c",
                ArtifactRole.ANALYSIS_SUMMARY,
                "sha256:aec581bca1e6b4eaed1ddae36c53cd3d5c70e2afb582589e1bc299dd2743876e"));
        assertThat(management.quarantineCounts())
                .containsExactlyInAnyOrderEntriesOf(Map.of(
                        LoadedBundle.QuarantineReason.DUPLICATE_AP_KEY, 2L,
                        LoadedBundle.QuarantineReason.UNLINKED_AP, 1L));
        assertThat(management.labelCensoringCounts())
                .containsExactlyInAnyOrderEntriesOf(Map.of(
                        LoadedBundle.LabelCensoringReason.AP_UNLINKED, 1L,
                        LoadedBundle.LabelCensoringReason.LABEL_NOT_YET_AVAILABLE, 2L));
        assertThat(management.chargePurgeCounts())
                .isEqualTo(new LoadedBundle.ChargePurgeCounts(
                        new LoadedBundle.PurgeCount(0, 0),
                        new LoadedBundle.PurgeCount(0, 0)));

        assertThatThrownBy(() -> management.sourceHashes().put(
                LoadedBundle.SourceRole.SM_CC, "sha256:" + "0".repeat(64)))
                .isInstanceOf(UnsupportedOperationException.class);
        assertThatThrownBy(() -> management.artifactHashes().put(
                ArtifactRole.ANALYSIS_CONFIG, "sha256:" + "0".repeat(64)))
                .isInstanceOf(UnsupportedOperationException.class);
        assertThatThrownBy(() -> management.quarantineCounts().put(
                LoadedBundle.QuarantineReason.UNLINKED_SM_CC, 1L))
                .isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    void rejectsSummaryCountsThatCannotBeProjectedWithoutNumericTruncation() throws Exception {
        Path bundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("oversized-count"));
        ObjectNode summary = (ObjectNode) BundleTestJson.mapper()
                .readTree(bundle.resolve("analysis_summary.json").toFile());
        ((ObjectNode) summary.path("quarantineCounts"))
                .put("UNLINKED_AP", BigInteger.valueOf(Long.MAX_VALUE).add(BigInteger.ONE));
        BundleTestJson.mapper().writeValue(bundle.resolve("analysis_summary.json").toFile(), summary);
        updateArtifactAttestation(bundle, "analysis_summary", "analysis_summary.json");

        assertCode("MANAGEMENT_PROJECTION_INVALID", () -> new BundleLoader().load(bundle));
    }

    @Test
    void schemaDigestMismatchWinsBeforeIdentityValidation() throws Exception {
        Path bundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("schema-digest"));
        ObjectNode manifest = (ObjectNode) GoldenBundleFixture.readManifest(bundle);
        ((ObjectNode) manifest.path("identity")).put(
                "schema.analysis_summary.sha256",
                "sha256:" + "0".repeat(64));
        GoldenBundleFixture.writeManifest(bundle, manifest);

        assertCode("SCHEMA_DIGEST_MISMATCH", () -> new BundleLoader().load(bundle));
    }

    @Test
    void embeddedSchemaTrustAndArtifactAttestationsPrecedeManifestSchemaValidation() throws Exception {
        Path digestBundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("trust-first"));
        ObjectNode digestManifest = (ObjectNode) GoldenBundleFixture.readManifest(digestBundle);
        digestManifest.put("unexpectedRootProperty", true);
        ((ObjectNode) digestManifest.path("identity")).put(
                "schema.analysis_summary.sha256",
                "sha256:" + "0".repeat(64));
        GoldenBundleFixture.writeManifest(digestBundle, digestManifest);
        assertCode("SCHEMA_DIGEST_MISMATCH", () -> new BundleLoader().load(digestBundle));

        Path artifactBundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("attestation-first"));
        ObjectNode artifactManifest = (ObjectNode) GoldenBundleFixture.readManifest(artifactBundle);
        artifactManifest.put("unexpectedRootProperty", true);
        GoldenBundleFixture.writeManifest(artifactBundle, artifactManifest);
        Files.writeString(
                artifactBundle.resolve("analysis_summary.json"),
                " ",
                java.nio.file.StandardOpenOption.APPEND);
        assertCode("ARTIFACT_SIZE_MISMATCH", () -> new BundleLoader().load(artifactBundle));

        Path schemaBundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("manifest-schema-last"));
        ObjectNode schemaManifest = (ObjectNode) GoldenBundleFixture.readManifest(schemaBundle);
        schemaManifest.put("unexpectedRootProperty", true);
        GoldenBundleFixture.writeManifest(schemaBundle, schemaManifest);
        assertCode("MANIFEST_SCHEMA_INVALID", () -> new BundleLoader().load(schemaBundle));
    }

    @Test
    void rejectsRecomputedCriteriaAndBundleIdMismatchesInOrder() throws Exception {
        Path criteriaBundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("criteria-id"));
        ObjectNode criteriaManifest = (ObjectNode) GoldenBundleFixture.readManifest(criteriaBundle);
        criteriaManifest.put("criteriaId", "sha256:" + "0".repeat(64));
        GoldenBundleFixture.writeManifest(criteriaBundle, criteriaManifest);
        assertCode("CRITERIA_ID_MISMATCH", () -> new BundleLoader().load(criteriaBundle));

        Path idBundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("bundle-id"));
        ObjectNode idManifest = (ObjectNode) GoldenBundleFixture.readManifest(idBundle);
        idManifest.put("bundleId", "sha256:" + "0".repeat(64));
        GoldenBundleFixture.writeManifest(idBundle, idManifest);
        assertCode("BUNDLE_ID_MISMATCH", () -> new BundleLoader().load(idBundle));
    }

    @Test
    void rejectsArtifactSizeAndHashTamperingBeforeJsonSchemaParsing() throws Exception {
        Path sizeBundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("size"));
        Files.writeString(sizeBundle.resolve("analysis_summary.json"), " ", java.nio.file.StandardOpenOption.APPEND);
        assertCode("ARTIFACT_SIZE_MISMATCH", () -> new BundleLoader().load(sizeBundle));

        Path hashBundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("hash"));
        byte[] summary = Files.readAllBytes(hashBundle.resolve("analysis_summary.json"));
        summary[summary.length - 2] ^= 1;
        Files.write(hashBundle.resolve("analysis_summary.json"), summary);
        assertCode("ARTIFACT_HASH_MISMATCH", () -> new BundleLoader().load(hashBundle));
    }

    @Test
    void artifactHashMismatchWinsBeforeMalformedArtifactJson() throws Exception {
        Path bundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("hash-before-json"));
        Path summaryPath = bundle.resolve("analysis_summary.json");
        byte[] malformedSameSize = Files.readAllBytes(summaryPath);
        java.util.Arrays.fill(malformedSameSize, (byte) ' ');
        malformedSameSize[0] = '{';
        Files.write(summaryPath, malformedSameSize);

        assertCode("ARTIFACT_HASH_MISMATCH", () -> new BundleLoader().load(bundle));
    }

    @Test
    void rejectsSchemaInvalidJsonAfterAValidUpdatedArtifactAttestation() throws Exception {
        Path bundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("json-schema"));
        ObjectNode summary = (ObjectNode) BundleTestJson.mapper()
                .readTree(bundle.resolve("analysis_summary.json").toFile());
        summary.put("evaluationMode", "NOT_THE_LOCKED_MODE");
        BundleTestJson.mapper().writeValue(bundle.resolve("analysis_summary.json").toFile(), summary);
        updateArtifactAttestation(bundle, "analysis_summary", "analysis_summary.json");

        assertCode("JSON_SCHEMA_INVALID", () -> new BundleLoader().load(bundle));
    }

    @Test
    void rejectsCrossBundleArtifactIdsEvenWhenSchemaAndAttestationAreValid() throws Exception {
        Path bundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("cross-id"));
        ObjectNode summary = (ObjectNode) BundleTestJson.mapper()
                .readTree(bundle.resolve("analysis_summary.json").toFile());
        summary.put("bundleId", "sha256:" + "0".repeat(64));
        BundleTestJson.mapper().writeValue(bundle.resolve("analysis_summary.json").toFile(), summary);
        updateArtifactAttestation(bundle, "analysis_summary", "analysis_summary.json");

        assertCode("ARTIFACT_ID_MISMATCH", () -> new BundleLoader().load(bundle));
    }

    @Test
    void rejectsSchemaInvalidReplayRowsAfterAValidUpdatedArtifactAttestation() throws Exception {
        Path bundle = GoldenBundleFixture.copyTo(temporary.toRealPath().resolve("row-schema"));
        Path replay = bundle.resolve("replay_events.csv");
        String csv = Files.readString(replay);
        Files.writeString(replay, csv.replaceFirst("sfep-replay-events/v1", "bad-schema-version"));
        updateArtifactAttestation(bundle, "replay_events", "replay_events.csv");

        assertCode("REPLAY_SCHEMA_INVALID", () -> new BundleLoader().load(bundle));
    }

    @Test
    void rejectsJsonBytesThatDifferFromTheAttestationAtTheConsumptionBoundary() throws Exception {
        byte[] original = Files.readAllBytes(
                GoldenBundleFixture.source().resolve("analysis_summary.json"));
        ArtifactDescriptor descriptor = new ArtifactDescriptor(
                "analysis_summary",
                original.length,
                Digests.sha256Uri(original),
                "sfep-analysis-summary/v1");
        byte[] replacedSameSize = original.clone();
        replacedSameSize[replacedSameSize.length - 2] ^= 1;

        assertCode("ARTIFACT_CONSUMED_HASH_MISMATCH", () ->
                ArtifactBytes.verify(ArtifactRole.ANALYSIS_SUMMARY, descriptor, replacedSameSize));
        assertCode("ARTIFACT_CONSUMED_SIZE_MISMATCH", () ->
                ArtifactBytes.verify(
                        ArtifactRole.ANALYSIS_SUMMARY,
                        descriptor,
                        java.util.Arrays.copyOf(original, original.length - 1)));
    }

    private static void updateArtifactAttestation(Path bundle, String role, String fileName) throws Exception {
        Path artifact = bundle.resolve(fileName);
        ObjectNode manifest = (ObjectNode) GoldenBundleFixture.readManifest(bundle);
        ArrayNode artifacts = (ArrayNode) manifest.path("artifacts");
        for (JsonNode candidate : artifacts) {
            if (role.equals(candidate.path("role").textValue())) {
                ObjectNode descriptor = (ObjectNode) candidate;
                descriptor.put("sizeBytes", Files.size(artifact));
                descriptor.put("sha256", Digests.sha256Uri(artifact));
                GoldenBundleFixture.writeManifest(bundle, manifest);
                return;
            }
        }
        throw new AssertionError("missing role " + role);
    }

    private static void assertCode(String code, ThrowingAction action) {
        assertThatThrownBy(action::run)
                .isInstanceOf(BundleLoadException.class)
                .hasMessageStartingWith(code + ":");
    }

    @FunctionalInterface
    private interface ThrowingAction {
        void run() throws Exception;
    }
}
