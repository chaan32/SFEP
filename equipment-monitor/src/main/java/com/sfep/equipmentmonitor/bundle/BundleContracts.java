package com.sfep.equipmentmonitor.bundle;

import java.util.Collections;
import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.Map;

public final class BundleContracts {
    private static final String V1 = "sfep-equipment-bundle/v1";
    private static final String V2 = "sfep-equipment-bundle/v2";
    private static final String CRITERIA_NAMESPACE = "sfep-criteria-id/v1";
    private static final String BUNDLE_NAMESPACE = "sfep-bundle-id/v1";
    private static final Map<String, BundleContract> CONTRACTS = loadContracts();

    private BundleContracts() {
    }

    public static Map<String, BundleContract> all() {
        return CONTRACTS;
    }

    public static BundleContract require(String manifestVersion) {
        BundleContract contract = manifestVersion == null ? null : CONTRACTS.get(manifestVersion);
        if (contract == null) {
            throw new BundleLoadException(
                    "BUNDLE_CONTRACT_UNSUPPORTED",
                    String.valueOf(manifestVersion));
        }
        return contract;
    }

    private static Map<String, BundleContract> loadContracts() {
        EnumMap<SchemaRole, SchemaDocument> v1Schemas = new EnumMap<>(SchemaRole.class);
        v1Schemas.put(SchemaRole.BUNDLE_MANIFEST, document(
                "v1/bundle_manifest.schema.json",
                "sha256:666e880d296c0d7e3df5af1aa80e6865922ebfb337fb9aca48f93eddfd89e8a5"));
        v1Schemas.put(SchemaRole.ANALYSIS_CONFIG, document(
                "v1/analysis_config.schema.json",
                "sha256:fad28561dfe9d9fe3cd09b025bb18c2101be053cb094b08442ea45963b86f549"));
        v1Schemas.put(SchemaRole.PRODUCER_RUNTIME, document(
                "v1/producer_runtime.schema.json",
                "sha256:97131d80a993d09d17c2c040b0e1cb2bd0eed5948d7a11608f26331d18f557e6"));
        v1Schemas.put(SchemaRole.EQUIPMENT_OPERATING_RANGES, document(
                "v1/equipment_operating_ranges.schema.json",
                "sha256:bee7d8be181dae4844c51d4627c5a1f068583b60a60c854f17035a8291cd7d89"));
        v1Schemas.put(SchemaRole.QUALITY_RISK_INTERVALS, document(
                "v1/quality_risk_intervals.schema.json",
                "sha256:2c8775fec18671030cf58ea0e94a3c99f8dac075fa9d5fe8dce724c2462f42ad"));
        v1Schemas.put(SchemaRole.REPLAY_EVENTS, document(
                "v1/replay_event_row.schema.json",
                "sha256:309749f73cf2a5a522617f975128ac005298f2fd71412903d76263702c1b6bd6"));
        v1Schemas.put(SchemaRole.ANALYSIS_SUMMARY, document(
                "v1/analysis_summary.schema.json",
                "sha256:6cb6077cc7093b90e464cc794604ca6b7682002a138ebed051801aee29f9d374"));

        BundleContract v1 = new BundleContract(
                V1,
                CRITERIA_NAMESPACE,
                BUNDLE_NAMESPACE,
                versions("sfep-analysis-config/v1", "sfep-analysis-summary/v1"),
                v1Schemas);

        EnumMap<SchemaRole, SchemaDocument> v2Schemas = new EnumMap<>(SchemaRole.class);
        v2Schemas.put(SchemaRole.BUNDLE_MANIFEST, document(
                "v2/bundle_manifest.schema.json",
                "sha256:0d7c4fb5e939beb324c6d83005a83cb9702e480a5babdfede88e5ca05776c2c3"));
        v2Schemas.put(SchemaRole.ANALYSIS_CONFIG, document(
                "v2/analysis_config.schema.json",
                "sha256:7942b12abcfeee661290f5b40df922edfeec66d6f95dc1ce20e78cc0f58f5da1"));
        v2Schemas.put(SchemaRole.ANALYSIS_SUMMARY, document(
                "v2/analysis_summary.schema.json",
                "sha256:0b38234ea0b7c5614dff0ac7514b6fb82e401a7846621e45bcbcf00ed82dc7c4"));
        v2Schemas.put(SchemaRole.PRODUCER_RUNTIME, v1Schemas.get(SchemaRole.PRODUCER_RUNTIME));
        v2Schemas.put(
                SchemaRole.EQUIPMENT_OPERATING_RANGES,
                v1Schemas.get(SchemaRole.EQUIPMENT_OPERATING_RANGES));
        v2Schemas.put(
                SchemaRole.QUALITY_RISK_INTERVALS,
                v1Schemas.get(SchemaRole.QUALITY_RISK_INTERVALS));
        v2Schemas.put(SchemaRole.REPLAY_EVENTS, v1Schemas.get(SchemaRole.REPLAY_EVENTS));

        BundleContract v2 = new BundleContract(
                V2,
                CRITERIA_NAMESPACE,
                BUNDLE_NAMESPACE,
                versions("sfep-analysis-config/v2", "sfep-analysis-summary/v2"),
                v2Schemas);

        LinkedHashMap<String, BundleContract> profiles = new LinkedHashMap<>();
        profiles.put(v1.manifestVersion(), v1);
        profiles.put(v2.manifestVersion(), v2);
        return Collections.unmodifiableMap(profiles);
    }

    private static EnumMap<ArtifactRole, String> versions(String config, String summary) {
        EnumMap<ArtifactRole, String> versions = new EnumMap<>(ArtifactRole.class);
        versions.put(ArtifactRole.ANALYSIS_CONFIG, config);
        versions.put(ArtifactRole.PRODUCER_RUNTIME, "sfep-producer-runtime/v1");
        versions.put(ArtifactRole.EQUIPMENT_OPERATING_RANGES, "sfep-operating-ranges/v1");
        versions.put(ArtifactRole.QUALITY_RISK_INTERVALS, "sfep-quality-rules/v1");
        versions.put(ArtifactRole.REPLAY_EVENTS, "sfep-replay-events/v1");
        versions.put(ArtifactRole.ANALYSIS_SUMMARY, summary);
        return versions;
    }

    private static SchemaDocument document(String relativePath, String expectedDigest) {
        return EmbeddedSchemas.loadDocument(
                "contracts/equipment-monitor/" + relativePath,
                expectedDigest);
    }
}
