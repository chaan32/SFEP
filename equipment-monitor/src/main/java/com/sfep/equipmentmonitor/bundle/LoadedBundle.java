package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;
import com.sfep.equipmentmonitor.replay.ReplaySource;

import java.util.List;

public record LoadedBundle(
        String bundleId,
        String criteriaId,
        AnalysisSummaryProjection summary,
        List<JsonNode> ranges,
        List<JsonNode> rules,
        ReplaySource replay) {
    public LoadedBundle {
        ranges = List.copyOf(ranges);
        rules = List.copyOf(rules);
    }
}
