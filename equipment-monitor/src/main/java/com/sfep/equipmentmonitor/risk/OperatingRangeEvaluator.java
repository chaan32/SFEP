package com.sfep.equipmentmonitor.risk;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class OperatingRangeEvaluator {
    public List<RangeEvaluation> evaluate(RiskEvent event, List<OperatingRange> definitions) {
        Map<String, List<OperatingRange>> byField = new LinkedHashMap<>();
        for (OperatingRange definition : List.copyOf(definitions)) {
            if (definition.firstAvailableStage() == event.stage()
                    && definition.equipmentType() == event.equipmentType()) {
                byField.computeIfAbsent(definition.field(), ignored -> new ArrayList<>()).add(definition);
            }
        }
        LinkedHashMap<String, FieldRole> expected = new LinkedHashMap<>();
        MonitoredFieldCatalog.fields(event.stage(), event.equipmentType())
                .forEach(field -> expected.put(field.name(), field.role()));
        byField.forEach((field, candidates) -> expected.putIfAbsent(field, candidates.getFirst().fieldRole()));
        List<RangeEvaluation> result = new ArrayList<>();
        expected.forEach((field, role) -> {
            List<OperatingRange> stageCandidates = byField.getOrDefault(field, List.of());
            List<OperatingRange> equipmentCandidates = stageCandidates.stream()
                    .filter(candidate -> candidate.equipmentId().equals(event.equipmentId()))
                    .toList();
            if (equipmentCandidates.isEmpty()) {
                RangeStatus status = stageCandidates.isEmpty()
                        ? RangeStatus.INSUFFICIENT_EVIDENCE
                        : RangeStatus.UNREGISTERED_CONDITION;
                RangeSelectionReason reason = stageCandidates.isEmpty()
                        ? RangeSelectionReason.NO_BASELINE
                        : RangeSelectionReason.UNREGISTERED_EQUIPMENT;
                result.add(evaluation(field, role, status, event.values().get(field), null, reason, false));
            } else {
                result.add(evaluateField(event, field, equipmentCandidates));
            }
        });
        return List.copyOf(result);
    }

    private RangeEvaluation evaluateField(RiskEvent event, String field, List<OperatingRange> candidates) {
        RiskScalar observed = event.values().get(field);
        FieldRole role = candidates.getFirst().fieldRole();
        if (observed == null) return evaluation(field, role, RangeStatus.DATA_MISSING, null, null,
                RangeSelectionReason.DATA_MISSING, false);

        OperatingRange selected = candidates.stream()
                .filter(candidate -> contextMatches(candidate.context(), event.values()))
                .min(Comparator.comparingInt(OperatingRange::contextLevel).thenComparing(OperatingRange::ruleId))
                .orElse(null);
        if (selected == null) {
            boolean unavailableDerivedContext = candidates.stream().allMatch(candidate ->
                    candidate.context().keySet().stream().anyMatch(key ->
                            key.endsWith("_band") && !event.values().containsKey(key)));
            return evaluation(field, role,
                    unavailableDerivedContext
                            ? RangeStatus.INSUFFICIENT_EVIDENCE
                            : RangeStatus.UNREGISTERED_CONDITION,
                    observed, null,
                    unavailableDerivedContext
                            ? RangeSelectionReason.BAND_BOUNDARY_NOT_SEALED
                            : RangeSelectionReason.UNREGISTERED_CONTEXT,
                    false);
        }
        RangeSelectionReason selectionReason = hasUnavailableMoreSpecificBand(candidates, selected, event.values())
                ? RangeSelectionReason.BAND_BOUNDARY_NOT_SEALED_FALLBACK
                : RangeSelectionReason.EXACT_CONTEXT;
        if (selected.fieldRole() == FieldRole.PRODUCT_STATE_REFERENCE) {
            return evaluation(field, role, RangeStatus.REFERENCE_ONLY, observed, selected, selectionReason, false);
        }
        if (!observed.isNumber()) {
            return evaluation(field, role, RangeStatus.DATA_MISSING, observed, selected,
                    RangeSelectionReason.DATA_MISSING, false);
        }
        RangeStatus status = status(observed.doubleValue(), selected);
        return evaluation(field, role, status, observed, selected, selectionReason,
                status == RangeStatus.CAUTION || status == RangeStatus.SEVERE);
    }

    private static boolean hasUnavailableMoreSpecificBand(
            List<OperatingRange> candidates,
            OperatingRange selected,
            Map<String, RiskScalar> values) {
        return candidates.stream().anyMatch(candidate ->
                candidate.contextLevel() < selected.contextLevel()
                        && candidate.context().entrySet().stream()
                        .filter(entry -> !entry.getKey().endsWith("_band"))
                        .allMatch(entry -> {
                            RiskScalar actual = values.get(entry.getKey());
                            return actual != null && actual.sameValue(entry.getValue());
                        })
                        && candidate.context().keySet().stream().anyMatch(key ->
                        key.endsWith("_band") && !values.containsKey(key)));
    }

    private static RangeStatus status(double value, OperatingRange range) {
        if (value < range.p05()) {
            if (!range.lowerTailEnabled() && range.p01() != null) return RangeStatus.TYPICAL;
            if (range.lowerTailEnabled() && range.p01() != null && value < range.p01()) return RangeStatus.SEVERE;
            return RangeStatus.CAUTION;
        }
        if (value > range.p95()) {
            if (!range.upperTailEnabled() && range.p99() != null) return RangeStatus.TYPICAL;
            if (range.upperTailEnabled() && range.p99() != null && value > range.p99()) return RangeStatus.SEVERE;
            return RangeStatus.CAUTION;
        }
        return RangeStatus.TYPICAL;
    }

    private static boolean contextMatches(Map<String, RiskScalar> required, Map<String, RiskScalar> values) {
        return required.entrySet().stream().allMatch(entry -> {
            RiskScalar actual = values.get(entry.getKey());
            return actual != null && actual.sameValue(entry.getValue());
        });
    }

    private static RangeEvaluation evaluation(
            String field, FieldRole role, RangeStatus status, RiskScalar observed,
            OperatingRange selected, RangeSelectionReason reason, boolean alert) {
        return new RangeEvaluation(field, role, status, observed,
                selected == null ? null : selected.ruleId(),
                selected == null ? null : selected.contextLevel(), reason, alert);
    }
}
