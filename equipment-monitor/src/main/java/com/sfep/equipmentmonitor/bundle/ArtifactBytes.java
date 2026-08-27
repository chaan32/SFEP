package com.sfep.equipmentmonitor.bundle;

public final class ArtifactBytes {
    private ArtifactBytes() {
    }

    public static byte[] verify(
            ArtifactRole role,
            ArtifactDescriptor descriptor,
            byte[] consumed) {
        if (consumed.length != descriptor.sizeBytes()) {
            throw new BundleLoadException(
                    "ARTIFACT_CONSUMED_SIZE_MISMATCH",
                    role.manifestRole());
        }
        if (!Digests.sha256Uri(consumed).equals(descriptor.sha256())) {
            throw new BundleLoadException(
                    "ARTIFACT_CONSUMED_HASH_MISMATCH",
                    role.manifestRole());
        }
        return consumed;
    }
}
