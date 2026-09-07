package com.sfep.equipmentmonitor.alert;

public record AlertKey(String bundleId, String materialKey, String eventStage, String ruleId) {
    public AlertKey {
        requireText(bundleId, "bundleId");
        requireText(materialKey, "materialKey");
        requireText(eventStage, "eventStage");
        requireText(ruleId, "ruleId");
    }

    private static void requireText(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(name + " must not be blank");
        }
    }
}
