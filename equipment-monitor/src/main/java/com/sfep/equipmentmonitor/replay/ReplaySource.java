package com.sfep.equipmentmonitor.replay;

import com.sfep.equipmentmonitor.bundle.EmbeddedSchemas;

import java.nio.file.Path;

public record ReplaySource(
        Path path,
        long sizeBytes,
        String sha256,
        String bundleId,
        String criteriaId,
        ReplayMetadata metadata) {
    public ReplayMetadata revalidate() {
        return new ReplayCsvValidator(EmbeddedSchemas.load())
                .validate(path, bundleId, criteriaId, sizeBytes, sha256);
    }
}
