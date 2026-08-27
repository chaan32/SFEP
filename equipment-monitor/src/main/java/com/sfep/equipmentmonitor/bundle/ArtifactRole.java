package com.sfep.equipmentmonitor.bundle;

public enum ArtifactRole {
    ANALYSIS_CONFIG("analysis_config", "analysis_config.json", "sfep-analysis-config/v1", SchemaRole.ANALYSIS_CONFIG),
    PRODUCER_RUNTIME("producer_runtime", "producer_runtime.json", "sfep-producer-runtime/v1", SchemaRole.PRODUCER_RUNTIME),
    EQUIPMENT_OPERATING_RANGES("equipment_operating_ranges", "equipment_operating_ranges.json", "sfep-operating-ranges/v1", SchemaRole.EQUIPMENT_OPERATING_RANGES),
    QUALITY_RISK_INTERVALS("quality_risk_intervals", "quality_risk_intervals.json", "sfep-quality-rules/v1", SchemaRole.QUALITY_RISK_INTERVALS),
    REPLAY_EVENTS("replay_events", "replay_events.csv", "sfep-replay-events/v1", SchemaRole.REPLAY_EVENTS),
    ANALYSIS_SUMMARY("analysis_summary", "analysis_summary.json", "sfep-analysis-summary/v1", SchemaRole.ANALYSIS_SUMMARY);

    private final String manifestRole;
    private final String fileName;
    private final String schemaVersion;
    private final SchemaRole schemaRole;

    ArtifactRole(String manifestRole, String fileName, String schemaVersion, SchemaRole schemaRole) {
        this.manifestRole = manifestRole;
        this.fileName = fileName;
        this.schemaVersion = schemaVersion;
        this.schemaRole = schemaRole;
    }

    public String manifestRole() {
        return manifestRole;
    }

    public String fileName() {
        return fileName;
    }

    public String schemaVersion() {
        return schemaVersion;
    }

    public SchemaRole schemaRole() {
        return schemaRole;
    }
}
