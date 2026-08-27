package com.sfep.equipmentmonitor.replay;

import com.sfep.equipmentmonitor.bundle.BundleLoadException;
import com.sfep.equipmentmonitor.bundle.Utf8Order;

import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

record ReplaySortKey(
        String date,
        int batchKind,
        int hour,
        String batchId,
        int batchStep,
        EquipmentOrder equipment,
        String materialKey,
        String eventId) implements Comparable<ReplaySortKey> {
    private static final Map<String, Integer> BATCH_RANK = Map.of(
            "CAST_DAY", 0,
            "FURNACE_HOUR", 1,
            "AP_DAY", 2);
    private static final Map<String, Integer> STEP_RANK = Map.of(
            "CAST_RECORDED", 0,
            "FURNACE_CHARGED", 10,
            "PREHEAT_COMPLETE", 11,
            "HEAT_COMPLETE", 12,
            "SOAK_COMPLETE", 13,
            "FURNACE_EXTRACTED", 14,
            "RM4_RECORDED", 15,
            "AP_RECORDED_WITH_RESULT", 20);
    private static final Pattern FURNACE_ID = Pattern.compile("^([1-4])(?:호기)?$");

    static ReplaySortKey from(ReplayEvent event) {
        Integer batchRank = BATCH_RANK.get(event.batchKind());
        Integer stepRank = STEP_RANK.get(event.batchStep());
        if (batchRank == null || stepRank == null) {
            throw new BundleLoadException("REPLAY_SORT_INVALID", "unknown rank vocabulary");
        }
        int hour = event.replayHour() == null ? -1 : event.replayHour();
        EquipmentOrder equipment;
        if (event.equipmentType().equals("FURNACE")) {
            Matcher furnaceId = FURNACE_ID.matcher(event.equipmentId());
            if (!furnaceId.matches()) {
                throw new BundleLoadException("REPLAY_EQUIPMENT_ID_INVALID", event.equipmentId());
            }
            int furnace = Integer.parseInt(furnaceId.group(1));
            equipment = new EquipmentOrder(0, furnace, "");
        } else {
            equipment = new EquipmentOrder(1, 0, event.equipmentId());
        }
        return new ReplaySortKey(
                event.replayDate(), batchRank, hour, event.batchId(), stepRank,
                equipment, event.materialKey(), event.eventId());
    }

    @Override
    public int compareTo(ReplaySortKey other) {
        int result = date.compareTo(other.date);
        if (result == 0) result = Integer.compare(batchKind, other.batchKind);
        if (result == 0) result = Integer.compare(hour, other.hour);
        if (result == 0) result = Utf8Order.compare(batchId, other.batchId);
        if (result == 0) result = Integer.compare(batchStep, other.batchStep);
        if (result == 0) result = equipment.compareTo(other.equipment);
        if (result == 0) result = Utf8Order.compare(materialKey, other.materialKey);
        if (result == 0) result = Utf8Order.compare(eventId, other.eventId);
        return result;
    }

    private record EquipmentOrder(int kind, int furnace, String text) implements Comparable<EquipmentOrder> {
        @Override
        public int compareTo(EquipmentOrder other) {
            int result = Integer.compare(kind, other.kind);
            if (result == 0 && kind == 0) result = Integer.compare(furnace, other.furnace);
            if (result == 0 && kind == 1) result = Utf8Order.compare(text, other.text);
            return result;
        }
    }
}
