package com.sfep.equipmentmonitor.bundle;

public enum SchemaRole {
    BUNDLE_MANIFEST("bundle_manifest"),
    ANALYSIS_CONFIG("analysis_config"),
    PRODUCER_RUNTIME("producer_runtime"),
    EQUIPMENT_OPERATING_RANGES("equipment_operating_ranges"),
    QUALITY_RISK_INTERVALS("quality_risk_intervals"),
    REPLAY_EVENTS("replay_events"),
    ANALYSIS_SUMMARY("analysis_summary");

    private final String identityRole;

    SchemaRole(String identityRole) {
        this.identityRole = identityRole;
    }

    public String identityRole() {
        return identityRole;
    }

    public String manifestIdentityKey() {
        return "schema." + identityRole + ".sha256";
    }
}
