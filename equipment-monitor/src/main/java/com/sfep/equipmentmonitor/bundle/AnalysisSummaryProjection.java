package com.sfep.equipmentmonitor.bundle;

public record AnalysisSummaryProjection(
        String asOf,
        String evaluationMode,
        String dateFrom,
        String dateTo,
        long holdoutTotal) {
}
