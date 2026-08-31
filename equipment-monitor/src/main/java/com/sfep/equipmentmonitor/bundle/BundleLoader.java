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
    public BundleLoader() {
    }

    public LoadedBundle load(Path bundleRoot) {
        SafeBundleLayout layout = BundleLayout.preflight(bundleRoot);

        JsonNode manifestNode = JsonSupport.parse(
                layout.file("bundle_manifest.json").readAllBytes("MANIFEST_READ_FAILED"),
                "MANIFEST_JSON_INVALID",
                "bundle_manifest.json");
        UntrustedManifestClaims claims = UntrustedManifestClaims.extract(manifestNode);
        BundleContract contract = BundleContracts.require(claims.schemaVersion());
        verifySchemaDigests(contract, claims);
        EnumMap<ArtifactRole, ArtifactDescriptor> descriptors = descriptors(contract, claims.artifacts());
        verifyIds(contract, claims);
        verifyIdentityBindings(claims, descriptors);
        verifyArtifactAttestations(layout, descriptors);

        contract.validate(SchemaRole.BUNDLE_MANIFEST, manifestNode, "MANIFEST_SCHEMA_INVALID");
        EnumMap<ArtifactRole, JsonNode> jsonArtifacts = readAndValidateJsonArtifacts(
                contract, layout, descriptors);

        SafeFile replayFile = layout.file(ArtifactRole.REPLAY_EVENTS.fileName());
        ArtifactDescriptor replayDescriptor = descriptors.get(ArtifactRole.REPLAY_EVENTS);
        ReplayMetadata replayMetadata = new ReplayCsvValidator(EmbeddedSchemas.from(contract))
                .validate(
                        replayFile,
                        claims.bundleId(),
                        claims.criteriaId(),
                        replayDescriptor.sizeBytes(),
                        replayDescriptor.sha256());

        BundleManifest manifest = JsonSupport.bind(
                manifestNode, BundleManifest.class, "MANIFEST_DTO_INVALID");
        AnalysisConfigDto config = bind(
                jsonArtifacts, ArtifactRole.ANALYSIS_CONFIG, AnalysisConfigDto.class);
        ProducerRuntimeDto runtime = bind(
                jsonArtifacts, ArtifactRole.PRODUCER_RUNTIME, ProducerRuntimeDto.class);
        OperatingRangesDto ranges = bind(
                jsonArtifacts, ArtifactRole.EQUIPMENT_OPERATING_RANGES, OperatingRangesDto.class);
        QualityRulesDto rules = bind(
                jsonArtifacts, ArtifactRole.QUALITY_RISK_INTERVALS, QualityRulesDto.class);
        AnalysisSummaryDto summary = bind(
                jsonArtifacts, ArtifactRole.ANALYSIS_SUMMARY, AnalysisSummaryDto.class);

        verifyArtifactMetadata(contract, manifest, config, runtime, ranges, rules, summary);
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
        LoadedBundle.ManagementProjection management = managementProjection(
                manifest,
                descriptors,
                summary);
        return new LoadedBundle(
                manifest.bundleId(),
                manifest.criteriaId(),
                projection,
                management,
                ranges.ranges(),
                rules.rules(),
                replay);
    }

    private static LoadedBundle.ManagementProjection managementProjection(
            BundleManifest manifest,
            Map<ArtifactRole, ArtifactDescriptor> descriptors,
            AnalysisSummaryDto summary) {
        EnumMap<LoadedBundle.SourceRole, String> sourceHashes =
                new EnumMap<>(LoadedBundle.SourceRole.class);
        for (LoadedBundle.SourceRole role : LoadedBundle.SourceRole.values()) {
            String hash = manifest.identity().get(role.identityKey());
            if (hash == null) {
                throw new BundleLoadException("MANAGEMENT_PROJECTION_INVALID", role.identityKey());
            }
            sourceHashes.put(role, hash);
        }

        EnumMap<ArtifactRole, String> artifactHashes = new EnumMap<>(ArtifactRole.class);
        for (ArtifactRole role : ArtifactRole.values()) {
            ArtifactDescriptor descriptor = descriptors.get(role);
            if (descriptor == null) {
                throw new BundleLoadException("MANAGEMENT_PROJECTION_INVALID", role.manifestRole());
            }
            artifactHashes.put(role, descriptor.sha256());
        }

        EnumMap<LoadedBundle.QuarantineReason, Long> quarantineCounts = enumCounts(
                summary.quarantineCounts(),
                LoadedBundle.QuarantineReason.class,
                "quarantineCounts");
        EnumMap<LoadedBundle.LabelCensoringReason, Long> labelCensoringCounts = enumCounts(
                summary.labelCensoringCounts(),
                LoadedBundle.LabelCensoringReason.class,
                "labelCensoringCounts");
        LoadedBundle.ChargePurgeCounts chargePurgeCounts = new LoadedBundle.ChargePurgeCounts(
                purgeCount(summary.chargePurgeCounts(), "outer"),
                purgeCount(summary.chargePurgeCounts(), "inner"));
        return new LoadedBundle.ManagementProjection(
                sourceHashes,
                artifactHashes,
                quarantineCounts,
                labelCensoringCounts,
                chargePurgeCounts);
    }

    private static <E extends Enum<E>> EnumMap<E, Long> enumCounts(
            JsonNode counts,
            Class<E> reasonType,
            String field) {
        if (counts == null || !counts.isObject()) {
            throw new BundleLoadException("MANAGEMENT_PROJECTION_INVALID", field);
        }
        EnumMap<E, Long> result = new EnumMap<>(reasonType);
        for (E reason : reasonType.getEnumConstants()) {
            JsonNode count = counts.get(reason.name());
            if (count != null) {
                result.put(reason, exactCount(count, field + "." + reason.name()));
            }
        }
        return result;
    }

    private static LoadedBundle.PurgeCount purgeCount(JsonNode counts, String split) {
        if (counts == null || !counts.isObject()) {
            throw new BundleLoadException("MANAGEMENT_PROJECTION_INVALID", "chargePurgeCounts");
        }
        JsonNode splitCounts = counts.get(split);
        if (splitCounts == null || !splitCounts.isObject()) {
            throw new BundleLoadException("MANAGEMENT_PROJECTION_INVALID", "chargePurgeCounts." + split);
        }
        return new LoadedBundle.PurgeCount(
                exactCount(splitCounts.get("chargeCount"), "chargePurgeCounts." + split + ".chargeCount"),
                exactCount(splitCounts.get("rowCount"), "chargePurgeCounts." + split + ".rowCount"));
    }

    private static long exactCount(JsonNode count, String field) {
        if (count == null || !count.isIntegralNumber() || !count.canConvertToLong()) {
            throw new BundleLoadException("MANAGEMENT_PROJECTION_INVALID", field);
        }
        long value = count.longValue();
        if (value < 0) {
            throw new BundleLoadException("MANAGEMENT_PROJECTION_INVALID", field);
        }
        return value;
    }

    private static void verifySchemaDigests(
            BundleContract contract,
            UntrustedManifestClaims manifest) {
        for (SchemaRole role : SchemaRole.values()) {
            String claimed = manifest.identity().get(role.manifestIdentityKey());
            String embedded = contract.schema(role).sha256();
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
            if (!contract.schema(role).sha256().equals(criteriaClaim)) {
                throw new BundleLoadException("SCHEMA_DIGEST_MISMATCH", "criteria " + role.identityRole());
            }
        }
    }

    private static void verifyIds(BundleContract contract, UntrustedManifestClaims manifest) {
        String criteria;
        String bundle;
        try {
            criteria = IdLines.compute(
                    contract.criteriaIdNamespace(), manifest.criteriaIdentity()).id();
            bundle = IdLines.compute(contract.bundleIdNamespace(), manifest.identity()).id();
        } catch (IllegalArgumentException error) {
            throw new BundleLoadException("MANIFEST_CLAIMS_INVALID", "identity preimage", error);
        }
        if (!criteria.equals(manifest.criteriaId())) {
            throw new BundleLoadException("CRITERIA_ID_MISMATCH", manifest.criteriaId());
        }
        if (!criteria.equals(manifest.identity().get("criteria_id"))) {
            throw new BundleLoadException("CRITERIA_ID_MISMATCH", "identity.criteria_id");
        }
        if (!bundle.equals(manifest.bundleId())) {
            throw new BundleLoadException("BUNDLE_ID_MISMATCH", manifest.bundleId());
        }
    }

    private static EnumMap<ArtifactRole, ArtifactDescriptor> descriptors(
            BundleContract contract,
            List<ArtifactDescriptor> artifacts) {
        EnumMap<ArtifactRole, ArtifactDescriptor> result = new EnumMap<>(ArtifactRole.class);
        for (int index = 0; index < ArtifactRole.values().length; index++) {
            ArtifactRole role = ArtifactRole.values()[index];
            ArtifactDescriptor descriptor = artifacts.get(index);
            if (!role.manifestRole().equals(descriptor.role())
                    || !contract.artifactVersion(role).equals(descriptor.schemaVersion())
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

    private static EnumMap<ArtifactRole, JsonNode> readAndValidateJsonArtifacts(
            BundleContract contract,
            SafeBundleLayout layout,
            Map<ArtifactRole, ArtifactDescriptor> descriptors) {
        EnumMap<ArtifactRole, JsonNode> result = new EnumMap<>(ArtifactRole.class);
        for (ArtifactRole role : ArtifactRole.values()) {
            if (role == ArtifactRole.REPLAY_EVENTS) {
                continue;
            }
            ArtifactDescriptor descriptor = descriptors.get(role);
            byte[] consumed = layout.file(role.fileName()).readAllBytes("ARTIFACT_READ_FAILED");
            ArtifactBytes.verify(role, descriptor, consumed);
            JsonNode node = JsonSupport.parse(
                    consumed,
                    "ARTIFACT_JSON_INVALID",
                    role.fileName());
            contract.validate(role.schemaRole(), node, "JSON_SCHEMA_INVALID");
            result.put(role, node);
        }
        return result;
    }

    private static <T> T bind(
            Map<ArtifactRole, JsonNode> artifacts,
            ArtifactRole role,
            Class<T> type) {
        return JsonSupport.bind(artifacts.get(role), type, "ARTIFACT_DTO_INVALID");
    }

    private static void verifyArtifactMetadata(
            BundleContract contract,
            BundleManifest manifest,
            AnalysisConfigDto config,
            ProducerRuntimeDto runtime,
            OperatingRangesDto ranges,
            QualityRulesDto rules,
            AnalysisSummaryDto summary) {
        if (!contract.manifestVersion().equals(manifest.schemaVersion())
                || !contract.artifactVersion(ArtifactRole.ANALYSIS_CONFIG)
                        .equals(config.schemaVersion())
                || !contract.artifactVersion(ArtifactRole.PRODUCER_RUNTIME)
                        .equals(runtime.schemaVersion())
                || !contract.artifactVersion(ArtifactRole.EQUIPMENT_OPERATING_RANGES)
                        .equals(ranges.schemaVersion())
                || !contract.artifactVersion(ArtifactRole.QUALITY_RISK_INTERVALS)
                        .equals(rules.schemaVersion())
                || !contract.artifactVersion(ArtifactRole.ANALYSIS_SUMMARY)
                        .equals(summary.schemaVersion())) {
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
