package com.sfep.equipmentmonitor.risk;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Fixed v1 numeric monitoring surface from the normative analysis configuration. */
public final class MonitoredFieldCatalog {
    private static final Map<Key, List<Field>> FIELDS = definitions();

    private MonitoredFieldCatalog() {
    }

    public static List<Field> fields(ProcessStage stage, EquipmentType equipmentType) {
        return FIELDS.getOrDefault(new Key(stage, equipmentType), List.of());
    }

    private static Map<Key, List<Field>> definitions() {
        LinkedHashMap<Key, List<Field>> values = new LinkedHashMap<>();
        values.put(new Key(ProcessStage.CAST_RECORDED, EquipmentType.SM_CC), List.of(
                direct("tundish_temp"), direct("mlac_ratio"),
                reference("delta_ferrite"), reference("ingre_cr"), reference("ingre_ni"), reference("ingre_s")));
        values.put(new Key(ProcessStage.FURNACE_CHARGED, EquipmentType.FURNACE), List.of(
                direct("f_jangip_temp"), reference("slab_width")));
        values.put(new Key(ProcessStage.PREHEAT_COMPLETE, EquipmentType.FURNACE), List.of(
                direct("f_pre_temp"), direct("f_pre_interval")));
        values.put(new Key(ProcessStage.HEAT_COMPLETE, EquipmentType.FURNACE), List.of(
                direct("f_heat_temp"), direct("f_heat_interval")));
        values.put(new Key(ProcessStage.SOAK_COMPLETE, EquipmentType.FURNACE), List.of(
                direct("f_sock_temp"), direct("f_sock_interval")));
        values.put(new Key(ProcessStage.FURNACE_EXTRACTED, EquipmentType.FURNACE), List.of(
                direct("f_bfg"), direct("f_cog"), direct("f_ldg"),
                direct("f_bfg_ratio"), direct("f_cog_ratio"), direct("f_ldg_ratio")));
        values.put(new Key(ProcessStage.RM4_RECORDED, EquipmentType.RM4), List.of(
                direct("rm4_temp"), direct("rm_pitch"), reference("hr_thick"), reference("hr_width")));
        values.put(new Key(ProcessStage.AP_RECORDED_WITH_RESULT, EquipmentType.AP), List.of(
                direct("ap_line_speed"), reference("ap_thick"), reference("ap_width")));
        return Map.copyOf(values);
    }

    private static Field direct(String name) {
        return new Field(name, FieldRole.DIRECT_OPERATION);
    }

    private static Field reference(String name) {
        return new Field(name, FieldRole.PRODUCT_STATE_REFERENCE);
    }

    public record Field(String name, FieldRole role) {
    }

    private record Key(ProcessStage stage, EquipmentType equipmentType) {
    }
}
