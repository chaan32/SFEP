package com.sfep.equipmentmonitor.bundle;

public enum ArtifactRole {
    ANALYSIS_CONFIG("analysis_config", "analysis_config.json", SchemaRole.ANALYSIS_CONFIG),
    PRODUCER_RUNTIME("producer_runtime", "producer_runtime.json", SchemaRole.PRODUCER_RUNTIME),
    EQUIPMENT_OPERATING_RANGES("equipment_operating_ranges", "equipment_operating_ranges.json", SchemaRole.EQUIPMENT_OPERATING_RANGES),
    QUALITY_RISK_INTERVALS("quality_risk_intervals", "quality_risk_intervals.json", SchemaRole.QUALITY_RISK_INTERVALS),
    REPLAY_EVENTS("replay_events", "replay_events.csv", SchemaRole.REPLAY_EVENTS),
    ANALYSIS_SUMMARY("analysis_summary", "analysis_summary.json", SchemaRole.ANALYSIS_SUMMARY);

    private final String manifestRole;
    private final String fileName;
    private final SchemaRole schemaRole;

    ArtifactRole(String manifestRole, String fileName, SchemaRole schemaRole) {
        this.manifestRole = manifestRole;
        this.fileName = fileName;
        this.schemaRole = schemaRole;
    }

    public String manifestRole() {
        return manifestRole;
    }

    public String fileName() {
        return fileName;
    }

    public SchemaRole schemaRole() {
        return schemaRole;
    }
}
