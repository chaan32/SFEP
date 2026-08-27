package com.sfep.equipmentmonitor.state;

import com.fasterxml.jackson.databind.JsonNode;
import com.sfep.equipmentmonitor.alert.AlertHistory;
import com.sfep.equipmentmonitor.alert.AlertKey;
import com.sfep.equipmentmonitor.alert.AlertKind;
import com.sfep.equipmentmonitor.alert.HistoricalAlert;
import com.sfep.equipmentmonitor.alert.RepetitionTracker;
import com.sfep.equipmentmonitor.replay.ReplayEvent;
import com.sfep.equipmentmonitor.replay.ReplayUnit;
import com.sfep.equipmentmonitor.risk.EquipmentType;
import com.sfep.equipmentmonitor.risk.OperatingRange;
import com.sfep.equipmentmonitor.risk.OperatingRangeEvaluator;
import com.sfep.equipmentmonitor.risk.ProcessStage;
import com.sfep.equipmentmonitor.risk.QualityRule;
import com.sfep.equipmentmonitor.risk.QualityRuleEvaluator;
import com.sfep.equipmentmonitor.risk.RangeEvaluation;
import com.sfep.equipmentmonitor.risk.RangeStatus;
import com.sfep.equipmentmonitor.risk.RiskEvent;
import com.sfep.equipmentmonitor.risk.RiskGrade;
import com.sfep.equipmentmonitor.risk.RiskScalar;
import com.sfep.equipmentmonitor.risk.RuleEvaluation;
import com.sfep.equipmentmonitor.risk.RuleEvaluationSummary;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Consumer;

/** Applies sealed historical definitions to values revealed by replay events. */
public final class HistoricalMonitor {
    private final Object lock = new Object();
    private final String bundleId;
    private final List<OperatingRange> ranges;
    private final List<QualityRule> rules;
    private final Map<String, QualityRule> rulesById;
    private final OperatingRangeEvaluator rangeEvaluator = new OperatingRangeEvaluator();
    private final QualityRuleEvaluator ruleEvaluator = new QualityRuleEvaluator();
    private final AlertHistory alertHistory = new AlertHistory();
    private final RepetitionTracker repetitionTracker = new RepetitionTracker();
    private final LinkedHashMap<String, MaterialAccumulator> materials = new LinkedHashMap<>();
    private final LinkedHashMap<EquipmentKey, EquipmentSnapshot> equipment = new LinkedHashMap<>();
    private final CopyOnWriteArrayList<Consumer<MonitorUpdate>> listeners = new CopyOnWriteArrayList<>();

    private long unitsProcessed;
    private long eventsProcessed;

    public HistoricalMonitor(String bundleId, List<OperatingRange> ranges, List<QualityRule> rules) {
        if (bundleId == null || bundleId.isBlank()) {
            throw new IllegalArgumentException("bundleId must not be blank");
        }
        this.bundleId = bundleId;
        this.ranges = List.copyOf(Objects.requireNonNull(ranges, "ranges"));
        this.rules = List.copyOf(Objects.requireNonNull(rules, "rules"));
        LinkedHashMap<String, QualityRule> index = new LinkedHashMap<>();
        for (QualityRule rule : this.rules) {
            if (index.putIfAbsent(rule.ruleId(), rule) != null) {
                throw new IllegalArgumentException("duplicate quality rule id " + rule.ruleId());
            }
        }
        this.rulesById = Map.copyOf(index);
    }

    public void accept(ReplayUnit unit) {
        Objects.requireNonNull(unit, "unit");
        MonitorUpdate update;
        synchronized (lock) {
            LinkedHashMap<String, MaterialSnapshot> changedMaterials = new LinkedHashMap<>();
            LinkedHashMap<EquipmentKey, EquipmentSnapshot> changedEquipment = new LinkedHashMap<>();
            List<PendingAlert> pendingAlerts = new ArrayList<>();
            List<RepetitionTracker.Observation> repetitionObservations = new ArrayList<>();
            List<HistoricalAlert> newAlerts = new ArrayList<>();
            for (ReplayEvent event : unit.events()) {
                process(event, changedMaterials, changedEquipment, pendingAlerts, repetitionObservations);
            }
            Map<RepetitionTracker.ObservationKey, Boolean> repeated =
                    repetitionTracker.observeUnit(repetitionObservations);
            pendingAlerts.sort(Comparator.comparing(PendingAlert::sortKey));
            for (PendingAlert pending : pendingAlerts) {
                boolean badge = repeated.getOrDefault(pending.observationKey(), false);
                HistoricalAlert alert = pending.range() == null
                        ? qualityAlert(pending.event(), pending.rule(), badge)
                        : rangeAlert(pending.event(), pending.range(), badge);
                if (alertHistory.add(alert)) {
                    newAlerts.add(alert);
                }
            }
            unitsProcessed++;
            update = new MonitorUpdate(unit, unitsProcessed, eventsProcessed,
                    changedMaterials, changedEquipment, newAlerts);
        }
        listeners.forEach(listener -> listener.accept(update));
    }

    public MonitorSnapshot snapshot() {
        synchronized (lock) {
            LinkedHashMap<String, MaterialSnapshot> stableMaterials = new LinkedHashMap<>();
            materials.forEach((key, value) -> stableMaterials.put(key, value.snapshot()));
            return new MonitorSnapshot(bundleId, unitsProcessed, eventsProcessed,
                    stableMaterials, new LinkedHashMap<>(equipment), alertHistory.snapshot());
        }
    }

    public AutoCloseable addListener(Consumer<MonitorUpdate> listener) {
        Objects.requireNonNull(listener, "listener");
        listeners.add(listener);
        return () -> listeners.remove(listener);
    }

    public void exportAlerts(Path destination) {
        alertHistory.exportCsv(destination);
    }

    public void reset() {
        synchronized (lock) {
            materials.clear();
            equipment.clear();
            alertHistory.reset();
            repetitionTracker.reset();
            unitsProcessed = 0;
            eventsProcessed = 0;
        }
    }

    private void process(
            ReplayEvent event,
            Map<String, MaterialSnapshot> changedMaterials,
            Map<EquipmentKey, EquipmentSnapshot> changedEquipment,
            List<PendingAlert> pendingAlerts,
            List<RepetitionTracker.Observation> repetitionObservations) {
        if (!bundleId.equals(event.bundleId())) {
            throw new IllegalArgumentException("replay event bundle does not match monitor bundle");
        }
        ProcessStage stage = parse(ProcessStage.class, event.batchStep(), "batchStep");
        EquipmentType equipmentType = parse(EquipmentType.class, event.equipmentType(), "equipmentType");
        MaterialAccumulator material = materials.computeIfAbsent(
                event.materialKey(), ignored -> new MaterialAccumulator(event));
        material.merge(event, scalarValues(event.values()));

        RiskEvent riskEvent = RiskEvent.of(stage, equipmentType, event.equipmentId(), material.values);
        List<RangeEvaluation> rangeEvaluations = rangeEvaluator.evaluate(riskEvent, ranges);
        List<RuleEvaluation> ruleEvaluations = ruleEvaluator.evaluate(riskEvent, rules);
        material.updateAssessment(event, rangeEvaluations, ruleEvaluations);

        for (RangeEvaluation evaluation : rangeEvaluations) {
            if (evaluation.selectedRuleId() == null) {
                continue;
            }
            RepetitionTracker.Observation observation = new RepetitionTracker.Observation(
                    event.equipmentType(), event.equipmentId(), evaluation.selectedRuleId(),
                    event.materialKey(), evaluation.alertEligible());
            repetitionObservations.add(observation);
            if (evaluation.alertEligible()) {
                pendingAlerts.add(new PendingAlert(event, evaluation, null, observation.key()));
            }
        }
        for (RuleEvaluation evaluation : ruleEvaluations) {
            boolean hit = evaluation.matched() && evaluation.alertEligible();
            RepetitionTracker.Observation observation = new RepetitionTracker.Observation(
                    event.equipmentType(), event.equipmentId(), evaluation.ruleId(),
                    event.materialKey(), hit);
            repetitionObservations.add(observation);
            if (hit) {
                pendingAlerts.add(new PendingAlert(event, null, evaluation, observation.key()));
            }
        }

        MaterialSnapshot materialSnapshot = material.snapshot();
        changedMaterials.put(event.materialKey(), materialSnapshot);
        EquipmentKey equipmentKey = new EquipmentKey(event.equipmentType(), event.equipmentId());
        Map<String, RiskScalar> published = scalarValues(event.values());
        EquipmentSnapshot inUnit = changedEquipment.get(equipmentKey);
        LinkedHashMap<String, Map<String, RiskScalar>> valuesByMaterial = new LinkedHashMap<>();
        List<String> materialKeys = new ArrayList<>();
        if (inUnit != null) {
            materialKeys.addAll(inUnit.materialKeys());
            valuesByMaterial.putAll(inUnit.valuesByMaterial());
        }
        if (!valuesByMaterial.containsKey(event.materialKey())) {
            materialKeys.add(event.materialKey());
        }
        valuesByMaterial.put(event.materialKey(), published);
        materialKeys.sort(String::compareTo);
        EquipmentSnapshot equipmentSnapshot = new EquipmentSnapshot(
                equipmentKey, event.batchStep(), event.replayDate(), event.replayHour(),
                event.timePrecision(), materialKeys, valuesByMaterial, false);
        equipment.put(equipmentKey, equipmentSnapshot);
        changedEquipment.put(equipmentKey, equipmentSnapshot);
        eventsProcessed++;
    }

    private HistoricalAlert rangeAlert(ReplayEvent event, RangeEvaluation evaluation, boolean repeated) {
        RiskGrade grade = evaluation.status() == RangeStatus.SEVERE ? RiskGrade.DANGER : RiskGrade.CAUTION;
        return new HistoricalAlert(
                key(event, evaluation.selectedRuleId()), AlertKind.OPERATING_RANGE, event.eventId(),
                event.replayDate(), event.replayHour(), event.equipmentType(), event.equipmentId(),
                evaluation.field(), evaluation.observedValue(), evaluation.status(), grade, repeated,
                null, null);
    }

    private HistoricalAlert qualityAlert(ReplayEvent event, RuleEvaluation evaluation, boolean repeated) {
        QualityRule rule = rulesById.get(evaluation.ruleId());
        String field = rule == null ? null : String.join(" × ", rule.fieldNames());
        return new HistoricalAlert(
                key(event, evaluation.ruleId()), AlertKind.QUALITY_RISK, event.eventId(),
                event.replayDate(), event.replayHour(), event.equipmentType(), event.equipmentId(),
                field, null, null, evaluation.grade(), repeated,
                evaluation.discovery(), evaluation.confirmation());
    }

    private AlertKey key(ReplayEvent event, String ruleId) {
        return new AlertKey(bundleId, event.materialKey(), event.batchStep(), ruleId);
    }

    private static Map<String, RiskScalar> scalarValues(JsonNode values) {
        if (!values.isObject()) {
            throw new IllegalArgumentException("replay values must be an object");
        }
        LinkedHashMap<String, RiskScalar> result = new LinkedHashMap<>();
        values.fields().forEachRemaining(entry -> {
            JsonNode value = entry.getValue();
            if (value.isNull()) {
                return;
            }
            if (value.isTextual()) {
                result.put(entry.getKey(), RiskScalar.of(value.textValue()));
            } else if (value.isBoolean()) {
                result.put(entry.getKey(), RiskScalar.of(value.booleanValue()));
            } else if (value.isNumber()) {
                result.put(entry.getKey(), RiskScalar.of(value.decimalValue()));
            } else {
                throw new IllegalArgumentException("replay value is not scalar: " + entry.getKey());
            }
        });
        return Map.copyOf(result);
    }

    private static <T extends Enum<T>> T parse(Class<T> type, String value, String field) {
        try {
            return Enum.valueOf(type, value);
        } catch (IllegalArgumentException error) {
            throw new IllegalArgumentException("unregistered " + field + ": " + value, error);
        }
    }

    private record PendingAlert(
            ReplayEvent event,
            RangeEvaluation range,
            RuleEvaluation rule,
            RepetitionTracker.ObservationKey observationKey) {
        private String sortKey() {
            return event.equipmentType() + "\u0000" + event.equipmentId() + "\u0000"
                    + observationKey.ruleId() + "\u0000" + event.materialKey();
        }
    }

    private static final class MaterialAccumulator {
        private static final QualityRuleEvaluator SUMMARY_EVALUATOR = new QualityRuleEvaluator();
        private final String materialKey;
        private final String chargeId;
        private final String slabNo;
        private String hrCoilId;
        private String apProdId;
        private final LinkedHashMap<String, RiskScalar> values = new LinkedHashMap<>();
        private ReplayEvent latest;
        private List<RangeEvaluation> rangeEvaluations = List.of();
        private List<RuleEvaluation> ruleEvaluations = List.of();
        private final List<StageAssessment> assessments = new ArrayList<>();
        private RiskGrade qualityRisk = RiskGrade.NORMAL;
        private RiskGrade historicalEvidenceRisk = RiskGrade.NORMAL;
        private long matchedRuleCount;

        private MaterialAccumulator(ReplayEvent event) {
            materialKey = event.materialKey();
            chargeId = event.chargeId();
            slabNo = event.slabNo();
            latest = event;
        }

        private void merge(ReplayEvent event, Map<String, RiskScalar> published) {
            if (!chargeId.equals(event.chargeId()) || !slabNo.equals(event.slabNo())) {
                throw new IllegalArgumentException("material identity changed during replay: " + materialKey);
            }
            if (event.hrCoilId() != null) {
                hrCoilId = event.hrCoilId();
            }
            if (event.apProdId() != null) {
                apProdId = event.apProdId();
            }
            values.putAll(published);
            latest = event;
        }

        private void updateAssessment(
                ReplayEvent event,
                List<RangeEvaluation> ranges,
                List<RuleEvaluation> rules) {
            latest = event;
            rangeEvaluations = List.copyOf(ranges);
            ruleEvaluations = rules.stream()
                    .filter(value -> value.matched()
                            || value.status() == com.sfep.equipmentmonitor.risk.RuleMatchStatus.DATA_MISSING)
                    .toList();
            RuleEvaluationSummary early = SUMMARY_EVALUATOR.summarize(
                    ruleEvaluations.stream().filter(value -> !value.historicalEvidenceOnly()).toList());
            RuleEvaluationSummary historical = SUMMARY_EVALUATOR.summarize(
                    ruleEvaluations.stream().filter(RuleEvaluation::historicalEvidenceOnly).toList());
            assessments.add(new StageAssessment(
                    event.eventId(), event.batchStep(), event.equipmentType(), event.equipmentId(),
                    event.replayDate(), event.replayHour(), rangeEvaluations, ruleEvaluations,
                    early.highestMatchedGrade(), historical.highestMatchedGrade()));
            List<RuleEvaluation> accumulated = assessments.stream()
                    .flatMap(value -> value.ruleEvaluations().stream()).toList();
            RuleEvaluationSummary cumulativeEarly = SUMMARY_EVALUATOR.summarize(
                    accumulated.stream().filter(value -> !value.historicalEvidenceOnly()).toList());
            RuleEvaluationSummary cumulativeHistorical = SUMMARY_EVALUATOR.summarize(
                    accumulated.stream().filter(RuleEvaluation::historicalEvidenceOnly).toList());
            qualityRisk = cumulativeEarly.highestMatchedGrade();
            historicalEvidenceRisk = cumulativeHistorical.highestMatchedGrade();
            matchedRuleCount = cumulativeEarly.matchedCount();
        }

        private MaterialSnapshot snapshot() {
            return new MaterialSnapshot(
                    materialKey, chargeId, slabNo, hrCoilId, apProdId, latest.batchStep(),
                    latest.equipmentType(), latest.equipmentId(), latest.replayDate(), latest.replayHour(),
                    latest.timePrecision(), values, rangeEvaluations, ruleEvaluations,
                    assessments, qualityRisk, historicalEvidenceRisk, matchedRuleCount);
        }
    }
}
