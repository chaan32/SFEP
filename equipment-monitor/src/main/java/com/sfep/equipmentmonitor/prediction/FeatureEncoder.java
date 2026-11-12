package com.sfep.equipmentmonitor.prediction;

import com.fasterxml.jackson.databind.JsonNode;
import com.sfep.equipmentmonitor.bundle.BundleLoadException;
import com.sfep.equipmentmonitor.bundle.Utf8Order;
import com.sfep.equipmentmonitor.risk.RiskScalar;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class FeatureEncoder {
    private static final List<String> ORDERED_FIELDS = List.of(
            "sm_plant", "steel_grade", "steel_usage", "cc_gubun", "slab_gubun",
            "tundish_temp", "mlac_ratio", "delta_ferrite", "ingre_cr", "ingre_ni", "ingre_s",
            "slab_grind", "furnace_no", "f_jangip_gubun", "f_jangip_temp", "slab_width",
            "f_pre_temp", "f_pre_interval", "f_heat_temp", "f_heat_interval",
            "f_sock_temp", "f_sock_interval", "f_bfg", "f_cog", "f_ldg",
            "f_bfg_ratio", "f_cog_ratio", "f_ldg_ratio");
    private static final Set<String> CATEGORICAL_FIELDS = Set.of(
            "sm_plant", "steel_grade", "steel_usage", "cc_gubun", "slab_gubun",
            "slab_grind", "furnace_no", "f_jangip_gubun");
    private static final String UNKNOWN = "UNKNOWN";

    private final List<Feature> features;
    private final int maxMissing;
    private final int width;

    private FeatureEncoder(List<Feature> features, int maxMissing) {
        this.features = List.copyOf(features);
        this.maxMissing = maxMissing;
        this.width = features.stream().mapToInt(feature -> feature.valueWidth() + 1).sum();
    }

    static FeatureEncoder fromContract(JsonNode contract) {
        JsonNode fields = contract.path("features");
        JsonNode maxMissing = contract.path("maxMissingFeatureCount");
        if (contract.size() != 2 || !fields.isArray() || fields.size() != ORDERED_FIELDS.size()
                || !maxMissing.isIntegralNumber() || !maxMissing.canConvertToInt()
                || maxMissing.intValue() < 0 || maxMissing.intValue() > fields.size()) {
            throw invalid("Invalid feature inventory or missing count limit");
        }

        List<Feature> features = new ArrayList<>(fields.size());
        for (int index = 0; index < fields.size(); index++) {
            JsonNode field = fields.get(index);
            String name = ORDERED_FIELDS.get(index);
            boolean categorical = CATEGORICAL_FIELDS.contains(name);
            if (!field.isObject() || field.size() != 3 || !name.equals(field.path("name").textValue())
                    || !(categorical ? "categorical" : "numeric").equals(field.path("type").textValue())) {
                throw invalid("Expected feature " + name + " at index " + index);
            }
            if (categorical) {
                features.add(new Feature(name, 0, vocabulary(field.path("vocabulary"), name)));
            } else {
                JsonNode median = field.path("median");
                if (!median.isNumber() || !float32Range(median.doubleValue())) {
                    throw invalid("Invalid median for " + name);
                }
                features.add(new Feature(name, (float) median.doubleValue(), List.of()));
            }
        }
        return new FeatureEncoder(features, maxMissing.intValue());
    }

    private static List<String> vocabulary(JsonNode node, String name) {
        if (!node.isArray() || node.isEmpty() || !UNKNOWN.equals(node.get(node.size() - 1).textValue())) {
            throw invalid("Missing UNKNOWN vocabulary entry for " + name);
        }
        List<String> values = new ArrayList<>(node.size());
        for (int index = 0; index < node.size() - 1; index++) {
            String category = node.get(index).textValue();
            if (category == null || UNKNOWN.equals(category)
                    || (!values.isEmpty() && Utf8Order.compare(values.getLast(), category) >= 0)) {
                throw invalid("Invalid vocabulary for " + name);
            }
            values.add(category);
        }
        values.add(UNKNOWN);
        return List.copyOf(values);
    }

    public Encoded encode(Map<String, RiskScalar> values, Set<String> seenFields) {
        float[] vector = new float[width];
        int offset = 0;
        int missingCount = 0;
        for (Feature feature : features) {
            String name = feature.name();
            if (!seenFields.contains(name)) {
                throw new IllegalArgumentException("Required model feature was not published: " + name);
            }
            RiskScalar value = values.get(name);
            boolean missing = value == null;
            if (feature.vocabulary().isEmpty()) {
                if (!missing && !value.isNumber()) {
                    throw new IllegalArgumentException("Expected a number for " + name);
                }
                double number = missing ? feature.median() : value.doubleValue();
                if (!float32Range(number)) {
                    throw new IllegalArgumentException("Expected a finite float32-range number for " + name);
                }
                vector[offset] = (float) number;
            } else {
                if (!missing && !(value.value() instanceof String)) {
                    throw new IllegalArgumentException("Expected a string for " + name);
                }
                int category = missing ? -1 : feature.vocabulary().indexOf(value.value());
                vector[offset + (category < 0 ? feature.valueWidth() - 1 : category)] = 1.0f;
            }
            offset += feature.valueWidth();
            if (missing) {
                missingCount++;
                vector[offset] = 1.0f;
            }
            offset++;
        }
        return new Encoded(vector, missingCount > maxMissing);
    }

    int width() {
        return width;
    }

    private static boolean float32Range(double value) {
        return -Float.MAX_VALUE <= value && value <= Float.MAX_VALUE;
    }

    private static BundleLoadException invalid(String detail) {
        return new BundleLoadException("INVALID_FEATURE_CONTRACT", detail);
    }

    private record Feature(String name, float median, List<String> vocabulary) {
        int valueWidth() {
            return vocabulary.isEmpty() ? 1 : vocabulary.size();
        }
    }

    public record Encoded(float[] vector, boolean inputIncomplete) {
    }
}
