package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

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
