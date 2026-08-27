package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;
import com.sfep.equipmentmonitor.replay.ReplayCsvValidator;
import com.sfep.equipmentmonitor.replay.ReplayMetadata;
import com.sfep.equipmentmonitor.replay.ReplaySource;

import java.nio.file.Path;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;

public final class BundleLoader {
    private final EmbeddedSchemas schemas;

    public BundleLoader() {
        this(EmbeddedSchemas.load());
    }

    BundleLoader(EmbeddedSchemas schemas) {
        this.schemas = schemas;
    }

    public LoadedBundle load(Path bundleRoot) {
        SafeBundleLayout layout = BundleLayout.preflight(bundleRoot);

        JsonNode manifestNode = JsonSupport.parse(
                layout.file("bundle_manifest.json").readAllBytes("MANIFEST_READ_FAILED"),
                "MANIFEST_JSON_INVALID",
                "bundle_manifest.json");
        UntrustedManifestClaims claims = UntrustedManifestClaims.extract(manifestNode);
        verifySchemaDigests(claims);
        verifyIds(claims);
        EnumMap<ArtifactRole, ArtifactDescriptor> descriptors = descriptors(claims.artifacts());
        verifyIdentityBindings(claims, descriptors);
        verifyArtifactAttestations(layout, descriptors);

        schemas.validate(SchemaRole.BUNDLE_MANIFEST, manifestNode, "MANIFEST_SCHEMA_INVALID");
        BundleManifest manifest = JsonSupport.bind(
                manifestNode, BundleManifest.class, "MANIFEST_DTO_INVALID");

        AnalysisConfigDto config = readJson(
                layout,
                ArtifactRole.ANALYSIS_CONFIG,
                descriptors.get(ArtifactRole.ANALYSIS_CONFIG),
                AnalysisConfigDto.class);
        ProducerRuntimeDto runtime = readJson(
                layout,
                ArtifactRole.PRODUCER_RUNTIME,
                descriptors.get(ArtifactRole.PRODUCER_RUNTIME),
                ProducerRuntimeDto.class);
        OperatingRangesDto ranges = readJson(
                layout,
                ArtifactRole.EQUIPMENT_OPERATING_RANGES,
                descriptors.get(ArtifactRole.EQUIPMENT_OPERATING_RANGES),
                OperatingRangesDto.class);
        QualityRulesDto rules = readJson(
                layout,
                ArtifactRole.QUALITY_RISK_INTERVALS,
                descriptors.get(ArtifactRole.QUALITY_RISK_INTERVALS),
                QualityRulesDto.class);

        verifyArtifactMetadata(manifest, config, runtime, ranges, rules);

        SafeFile replayFile = layout.file(ArtifactRole.REPLAY_EVENTS.fileName());
        ArtifactDescriptor replayDescriptor = descriptors.get(ArtifactRole.REPLAY_EVENTS);
        ReplayMetadata replayMetadata = new ReplayCsvValidator(schemas)
                .validate(
                        replayFile,
                        manifest.bundleId(),
                        manifest.criteriaId(),
                        replayDescriptor.sizeBytes(),
                        replayDescriptor.sha256());

        AnalysisSummaryDto summary = readJson(
                layout,
                ArtifactRole.ANALYSIS_SUMMARY,
                descriptors.get(ArtifactRole.ANALYSIS_SUMMARY),
                AnalysisSummaryDto.class);
        if (!manifest.bundleId().equals(summary.bundleId())
                || !manifest.criteriaId().equals(summary.criteriaId())
                || !manifest.asOf().equals(summary.asOf())) {
            throw new BundleLoadException("ARTIFACT_ID_MISMATCH", "analysis_summary");
        }

        ReplaySource replay = new ReplaySource(
                replayFile.path(),
                replayDescriptor.sizeBytes(),
                replayDescriptor.sha256(),
                manifest.bundleId(),
                manifest.criteriaId(),
                replayMetadata);
        AnalysisSummaryProjection projection = new AnalysisSummaryProjection(
                summary.asOf(),
                summary.evaluationMode(),
                summary.dateRange().path("from").textValue(),
                summary.dateRange().path("to").textValue(),
                summary.splitCounts().path("holdout").path("total").longValue());
        return new LoadedBundle(
                manifest.bundleId(),
                manifest.criteriaId(),
                projection,
                ranges.ranges(),
                rules.rules(),
                replay);
    }

    private void verifySchemaDigests(UntrustedManifestClaims manifest) {
        for (SchemaRole role : SchemaRole.values()) {
            String claimed = manifest.identity().get(role.manifestIdentityKey());
            String embedded = schemas.document(role).sha256();
            if (!embedded.equals(claimed)) {
                throw new BundleLoadException(
                        "SCHEMA_DIGEST_MISMATCH",
                        role.identityRole() + " expected " + embedded + " but found " + claimed);
            }
        }
        for (SchemaRole role : List.of(
                SchemaRole.ANALYSIS_CONFIG,
                SchemaRole.PRODUCER_RUNTIME,
                SchemaRole.EQUIPMENT_OPERATING_RANGES,
                SchemaRole.QUALITY_RISK_INTERVALS)) {
            String criteriaClaim = manifest.criteriaIdentity().get(role.manifestIdentityKey());
            if (!schemas.document(role).sha256().equals(criteriaClaim)) {
                throw new BundleLoadException("SCHEMA_DIGEST_MISMATCH", "criteria " + role.identityRole());
            }
        }
    }

    private static void verifyIds(UntrustedManifestClaims manifest) {
        String criteria = IdLines.compute("sfep-criteria-id/v1", manifest.criteriaIdentity()).id();
        if (!criteria.equals(manifest.criteriaId())) {
            throw new BundleLoadException("CRITERIA_ID_MISMATCH", manifest.criteriaId());
        }
        if (!criteria.equals(manifest.identity().get("criteria_id"))) {
            throw new BundleLoadException("CRITERIA_ID_MISMATCH", "identity.criteria_id");
        }
        String bundle = IdLines.compute("sfep-bundle-id/v1", manifest.identity()).id();
        if (!bundle.equals(manifest.bundleId())) {
            throw new BundleLoadException("BUNDLE_ID_MISMATCH", manifest.bundleId());
        }
    }

    private static EnumMap<ArtifactRole, ArtifactDescriptor> descriptors(List<ArtifactDescriptor> artifacts) {
        EnumMap<ArtifactRole, ArtifactDescriptor> result = new EnumMap<>(ArtifactRole.class);
        for (int index = 0; index < ArtifactRole.values().length; index++) {
            ArtifactRole role = ArtifactRole.values()[index];
            ArtifactDescriptor descriptor = artifacts.get(index);
            if (!role.manifestRole().equals(descriptor.role())
                    || !role.schemaVersion().equals(descriptor.schemaVersion())
                    || result.put(role, descriptor) != null) {
                throw new BundleLoadException("MANIFEST_ARTIFACT_INVALID", descriptor.role());
            }
        }
        return result;
    }

    private static void verifyIdentityBindings(
            UntrustedManifestClaims manifest,
            Map<ArtifactRole, ArtifactDescriptor> descriptors) {
        String config = descriptors.get(ArtifactRole.ANALYSIS_CONFIG).sha256();
        String runtime = descriptors.get(ArtifactRole.PRODUCER_RUNTIME).sha256();
        if (!config.equals(manifest.identity().get("analysis_config_sha256"))
                || !config.equals(manifest.criteriaIdentity().get("analysis_config_sha256"))
                || !runtime.equals(manifest.identity().get("producer_runtime_sha256"))
                || !runtime.equals(manifest.criteriaIdentity().get("producer_runtime_sha256"))
                || !manifest.asOf().equals(manifest.criteriaIdentity().get("as_of"))) {
            throw new BundleLoadException("IDENTITY_BINDING_MISMATCH", "manifest identity maps");
        }
    }

    private static void verifyArtifactAttestations(
            SafeBundleLayout layout,
            Map<ArtifactRole, ArtifactDescriptor> descriptors) {
        for (ArtifactRole role : ArtifactRole.values()) {
            SafeFile file = layout.file(role.fileName());
            ArtifactDescriptor descriptor = descriptors.get(role);
            if (file.size() != descriptor.sizeBytes()) {
                throw new BundleLoadException("ARTIFACT_SIZE_MISMATCH", role.manifestRole());
            }
        }
        for (ArtifactRole role : ArtifactRole.values()) {
            SafeFile file = layout.file(role.fileName());
            ArtifactDescriptor descriptor = descriptors.get(role);
            file.assertUnchanged("ARTIFACT_FILE_CHANGED");
            String actual = Digests.sha256Uri(file.path());
            file.assertUnchanged("ARTIFACT_FILE_CHANGED");
            if (!actual.equals(descriptor.sha256())) {
                throw new BundleLoadException("ARTIFACT_HASH_MISMATCH", role.manifestRole());
            }
        }
    }

    private <T> T readJson(
            SafeBundleLayout layout,
            ArtifactRole role,
            ArtifactDescriptor descriptor,
            Class<T> type) {
        byte[] consumed = layout.file(role.fileName()).readAllBytes("ARTIFACT_READ_FAILED");
        ArtifactBytes.verify(role, descriptor, consumed);
        JsonNode node = JsonSupport.parse(
                consumed,
                "ARTIFACT_JSON_INVALID",
                role.fileName());
        schemas.validate(role.schemaRole(), node, "JSON_SCHEMA_INVALID");
        return JsonSupport.bind(node, type, "ARTIFACT_DTO_INVALID");
    }

    private static void verifyArtifactMetadata(
            BundleManifest manifest,
            AnalysisConfigDto config,
            ProducerRuntimeDto runtime,
            OperatingRangesDto ranges,
            QualityRulesDto rules) {
        if (!ArtifactRole.ANALYSIS_CONFIG.schemaVersion().equals(config.schemaVersion())
                || !ArtifactRole.PRODUCER_RUNTIME.schemaVersion().equals(runtime.schemaVersion())) {
            throw new BundleLoadException("ARTIFACT_ID_MISMATCH", "schemaVersion");
        }
        if (!manifest.criteriaId().equals(ranges.criteriaId())
                || !manifest.asOf().equals(ranges.asOf())
                || !manifest.criteriaId().equals(rules.criteriaId())
                || !manifest.asOf().equals(rules.asOf())) {
            throw new BundleLoadException("ARTIFACT_ID_MISMATCH", "criteria/asOf");
        }
    }
}
