package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;
import com.sfep.equipmentmonitor.replay.ReplaySource;

import java.util.Collections;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

public record LoadedBundle(
        String bundleId,
        String criteriaId,
        AnalysisSummaryProjection summary,
        ManagementProjection management,
        List<JsonNode> ranges,
        List<JsonNode> rules,
        ReplaySource replay) {
    public LoadedBundle {
        ranges = List.copyOf(ranges);
        rules = List.copyOf(rules);
    }

    public record ManagementProjection(
            Map<SourceRole, String> sourceHashes,
            Map<ArtifactRole, String> artifactHashes,
            Map<QuarantineReason, Long> quarantineCounts,
            Map<LabelCensoringReason, Long> labelCensoringCounts,
            ChargePurgeCounts chargePurgeCounts) {
        public ManagementProjection {
            sourceHashes = immutableEnumMap(SourceRole.class, sourceHashes);
            artifactHashes = immutableEnumMap(ArtifactRole.class, artifactHashes);
            quarantineCounts = immutableEnumMap(QuarantineReason.class, quarantineCounts);
            labelCensoringCounts = immutableEnumMap(LabelCensoringReason.class, labelCensoringCounts);
            chargePurgeCounts = Objects.requireNonNull(chargePurgeCounts, "chargePurgeCounts");
        }
    }

    public record ChargePurgeCounts(PurgeCount outer, PurgeCount inner) {
        public ChargePurgeCounts {
            Objects.requireNonNull(outer, "outer");
            Objects.requireNonNull(inner, "inner");
        }
    }

    public record PurgeCount(long chargeCount, long rowCount) {
        public PurgeCount {
            if (chargeCount < 0 || rowCount < 0) {
                throw new IllegalArgumentException("purge counts must be non-negative");
            }
        }
    }

    public enum SourceRole {
        SM_CC("sm_cc"),
        FUR_HR("fur_hr"),
        AP("ap");

        private final String identityName;

        SourceRole(String identityName) {
            this.identityName = identityName;
        }

        String identityKey() {
            return "source." + identityName + ".sha256";
        }
    }

    public enum QuarantineReason {
        MISSING_SM_CC_KEY,
        MISSING_FUR_HR_KEY,
        MISSING_AP_HR_COIL_ID,
        MISSING_FUR_HR_COIL_ID,
        MISSING_AP_PROD_ID,
        DUPLICATE_SM_CC_KEY,
        DUPLICATE_FUR_HR_KEY,
        DUPLICATE_AP_KEY,
        DUPLICATE_FUR_HR_COIL_KEY,
        DUPLICATE_AP_PROD_ID,
        INVALID_FUR_HR_DATE,
        IMPOSSIBLE_STAGE_DATE_ORDER,
        UNLINKED_SM_CC,
        UNLINKED_FUR_HR,
        UNLINKED_AP
    }

    public enum LabelCensoringReason {
        AP_UNLINKED,
        LABEL_MISSING,
        LABEL_NOT_YET_AVAILABLE
    }

    private static <K extends Enum<K>, V> Map<K, V> immutableEnumMap(
            Class<K> keyType,
            Map<K, V> values) {
        Objects.requireNonNull(values, "values");
        EnumMap<K, V> copy = new EnumMap<>(keyType);
        copy.putAll(values);
        return Collections.unmodifiableMap(copy);
    }
}
