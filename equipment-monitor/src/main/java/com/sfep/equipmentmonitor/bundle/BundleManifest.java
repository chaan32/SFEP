package com.sfep.equipmentmonitor.bundle;

import java.util.List;
import java.util.Map;

public record BundleManifest(
        String schemaVersion,
        String bundleId,
        String criteriaId,
        Map<String, String> identity,
        Map<String, String> criteriaIdentity,
        String asOf,
        String timezone,
        int labelMaturityDays,
        List<ArtifactDescriptor> artifacts) {
}
