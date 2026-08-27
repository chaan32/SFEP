package com.sfep.equipmentmonitor.bundle;

public enum SchemaRole {
    BUNDLE_MANIFEST("bundle_manifest", "bundle_manifest.schema.json"),
    ANALYSIS_CONFIG("analysis_config", "analysis_config.schema.json"),
    PRODUCER_RUNTIME("producer_runtime", "producer_runtime.schema.json"),
    EQUIPMENT_OPERATING_RANGES("equipment_operating_ranges", "equipment_operating_ranges.schema.json"),
    QUALITY_RISK_INTERVALS("quality_risk_intervals", "quality_risk_intervals.schema.json"),
    REPLAY_EVENTS("replay_events", "replay_event_row.schema.json"),
    ANALYSIS_SUMMARY("analysis_summary", "analysis_summary.schema.json");

    private final String identityRole;
    private final String fileName;

    SchemaRole(String identityRole, String fileName) {
        this.identityRole = identityRole;
        this.fileName = fileName;
    }

    public String identityRole() {
        return identityRole;
    }

    public String fileName() {
        return fileName;
    }

    public String manifestIdentityKey() {
        return "schema." + identityRole + ".sha256";
    }
}
