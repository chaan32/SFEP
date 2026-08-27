package com.sfep.equipmentmonitor.alert;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/** Tracks rule hits by distinct material; this is not a duration calculation. */
public final class RepetitionTracker {
    private static final int WINDOW = 5;
    private static final int REQUIRED_HITS = 3;

    private final Map<EquipmentRule, Deque<ObservationGroup>> observations = new LinkedHashMap<>();

    /**
     * Applies one unordered replay unit atomically. A whole tied unit is retained at the
     * five-material boundary, so CSV storage order can never choose the repeated badge.
     */
    public synchronized Map<ObservationKey, Boolean> observeUnit(List<Observation> unit) {
        LinkedHashMap<EquipmentRule, LinkedHashMap<String, Boolean>> current = new LinkedHashMap<>();
        for (Observation value : List.copyOf(unit)) {
            EquipmentRule rule = new EquipmentRule(value.equipmentType(), value.equipmentId(), value.ruleId());
            current.computeIfAbsent(rule, ignored -> new LinkedHashMap<>())
                    .merge(value.materialKey(), value.hit(), Boolean::logicalOr);
        }
        LinkedHashMap<ObservationKey, Boolean> result = new LinkedHashMap<>();
        current.forEach((rule, values) -> {
            Deque<ObservationGroup> recent = observations.computeIfAbsent(rule, ignored -> new ArrayDeque<>());
            for (ObservationGroup group : recent) {
                values.keySet().forEach(group.values()::remove);
            }
            recent.removeIf(group -> group.values().isEmpty());
            recent.addLast(new ObservationGroup(new LinkedHashMap<>(values)));
            trimWholeTieGroups(recent);
            long hits = recent.stream()
                    .flatMap(group -> group.values().values().stream())
                    .filter(Boolean::booleanValue)
                    .count();
            boolean repeated = hits >= REQUIRED_HITS;
            values.forEach((material, hit) -> result.put(
                    new ObservationKey(rule.equipmentType(), rule.equipmentId(), rule.ruleId(), material),
                    hit && repeated));
        });
        return Map.copyOf(result);
    }

    public synchronized void reset() {
        observations.clear();
    }

    private static void trimWholeTieGroups(Deque<ObservationGroup> recent) {
        while (recent.size() > 1) {
            int total = recent.stream().mapToInt(group -> group.values().size()).sum();
            ObservationGroup oldest = recent.peekFirst();
            if (oldest == null || total - oldest.values().size() < WINDOW) {
                return;
            }
            recent.removeFirst();
        }
    }

    public record Observation(
            String equipmentType,
            String equipmentId,
            String ruleId,
            String materialKey,
            boolean hit) {
        public Observation {
            Objects.requireNonNull(equipmentType, "equipmentType");
            Objects.requireNonNull(equipmentId, "equipmentId");
            Objects.requireNonNull(ruleId, "ruleId");
            Objects.requireNonNull(materialKey, "materialKey");
        }

        public ObservationKey key() {
            return new ObservationKey(equipmentType, equipmentId, ruleId, materialKey);
        }
    }

    public record ObservationKey(
            String equipmentType,
            String equipmentId,
            String ruleId,
            String materialKey) {
    }

    private record ObservationGroup(LinkedHashMap<String, Boolean> values) {
    }

    private record EquipmentRule(String equipmentType, String equipmentId, String ruleId) {
    }
}
