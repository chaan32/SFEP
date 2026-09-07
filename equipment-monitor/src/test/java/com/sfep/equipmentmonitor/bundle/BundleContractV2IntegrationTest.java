package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BundleContractV2IntegrationTest {
    private static final String V1_CONFIG_SCHEMA_DIGEST =
            "sha256:fad28561dfe9d9fe3cd09b025bb18c2101be053cb094b08442ea45963b86f549";
    private static final String V2_CONFIG_SCHEMA_DIGEST =
            "sha256:7942b12abcfeee661290f5b40df922edfeec66d6f95dc1ce20e78cc0f58f5da1";

    @TempDir
    Path temporary;

    @Test
    void loadsTheTrackedV2GoldenBundleWithTheMixedVersionProfile() throws Exception {
        Path bundle = copyGolden("v2", "loads-v2");

        LoadedBundle loaded = new BundleLoader().load(bundle);

        assertThat(loaded.bundleId())
                .isEqualTo("sha256:1f3d3b5a8d58380edb3a277736d67c8a38f3b014be3f2a8f1f09375dc56ad9e1");
        assertThat(loaded.criteriaId())
                .isEqualTo("sha256:abeb3857e0d648cdc40ad456817bdb6c535caa42255cc28547dd0a3a65440363");
        assertThat(loaded.summary().asOf()).isEqualTo("2025-02-20");
        assertThat(loaded.summary().holdoutTotal()).isEqualTo(2);
        assertThat(loaded.replay().metadata().rowCount()).isEqualTo(95);
    }

    @Test
    void rejectsUnknownProfileBeforeReadingMalformedArtifacts() throws Exception {
        Path bundle = copyGolden("v2", "unknown-profile");
        ObjectNode manifest = readManifest(bundle);
        manifest.put("schemaVersion", "sfep-equipment-bundle/v3");
        writeManifest(bundle, manifest);
        Files.writeString(bundle.resolve("analysis_summary.json"), "{not-json");

        assertCode("BUNDLE_CONTRACT_UNSUPPORTED", () -> new BundleLoader().load(bundle));
    }

    @Test
    void doesNotFallbackFromTheClaimedProfileToAnotherProfile() throws Exception {
        Path bundle = copyGolden("v2", "no-fallback");
        ObjectNode manifest = readManifest(bundle);
        manifest.put("schemaVersion", "sfep-equipment-bundle/v1");
        writeManifest(bundle, manifest);

        assertCode("SCHEMA_DIGEST_MISMATCH", () -> new BundleLoader().load(bundle));
    }

    @Test
    void rejectsV1AndV2ArtifactVersionMixing() throws Exception {
        Path v1 = copyGolden("v1", "v1-artifact-version");
        setArtifactVersion(v1, "analysis_config", "sfep-analysis-config/v2");
        assertCode("MANIFEST_ARTIFACT_INVALID", () -> new BundleLoader().load(v1));

        Path v2 = copyGolden("v2", "v2-artifact-version");
        setArtifactVersion(v2, "analysis_config", "sfep-analysis-config/v1");
        assertCode("MANIFEST_ARTIFACT_INVALID", () -> new BundleLoader().load(v2));
    }

    @Test
    void rejectsV1AndV2SchemaDigestMixing() throws Exception {
        Path v1 = copyGolden("v1", "v1-schema-digest");
        setIdentity(v1, "schema.analysis_config.sha256", V2_CONFIG_SCHEMA_DIGEST);
        assertCode("SCHEMA_DIGEST_MISMATCH", () -> new BundleLoader().load(v1));

        Path v2 = copyGolden("v2", "v2-schema-digest");
        setIdentity(v2, "schema.analysis_config.sha256", V1_CONFIG_SCHEMA_DIGEST);
        assertCode("SCHEMA_DIGEST_MISMATCH", () -> new BundleLoader().load(v2));
    }

    private Path copyGolden(String profile, String targetName) throws Exception {
        Path source = Path.of(System.getProperty("sfep.repo-root"))
                .resolve("contracts/equipment-monitor")
                .resolve(profile)
                .resolve("golden-bundle");
        Path target = temporary.toRealPath().resolve(targetName);
        Files.createDirectory(target);
        for (String fileName : GoldenBundleFixture.BUNDLE_FILES) {
            Files.copy(source.resolve(fileName), target.resolve(fileName), StandardCopyOption.COPY_ATTRIBUTES);
        }
        return target;
    }

    private static void setArtifactVersion(Path bundle, String role, String version) throws Exception {
        ObjectNode manifest = readManifest(bundle);
        ArrayNode artifacts = (ArrayNode) manifest.path("artifacts");
        for (JsonNode candidate : artifacts) {
            if (role.equals(candidate.path("role").textValue())) {
                ((ObjectNode) candidate).put("schemaVersion", version);
                writeManifest(bundle, manifest);
                return;
            }
        }
        throw new AssertionError("missing role " + role);
    }

    private static void setIdentity(Path bundle, String key, String value) throws Exception {
        ObjectNode manifest = readManifest(bundle);
        ((ObjectNode) manifest.path("identity")).put(key, value);
        writeManifest(bundle, manifest);
    }

    private static ObjectNode readManifest(Path bundle) throws Exception {
        return (ObjectNode) BundleTestJson.mapper()
                .readTree(bundle.resolve("bundle_manifest.json").toFile());
    }

    private static void writeManifest(Path bundle, JsonNode manifest) throws Exception {
        BundleTestJson.mapper().writeValue(bundle.resolve("bundle_manifest.json").toFile(), manifest);
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
