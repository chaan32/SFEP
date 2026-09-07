package com.sfep.equipmentmonitor.risk;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class RiskDefinitionCompiler {
    private static final Set<String> RANGE_KEYS = Set.of(
            "ruleId", "field", "fieldRole", "firstAvailableStage", "equipmentType", "equipmentId",
            "contextLevel", "context", "support", "median", "p01", "p05", "p95", "p99",
            "lowerTailEnabled", "upperTailEnabled");
    private static final Set<String> RULE_KEYS = Set.of(
            "ruleId", "analysisFamily", "evidenceFamily", "firstAvailableStage", "equipmentType",
            "applicationScope", "equipmentId", "fieldNames", "predicate", "applicationContext",
            "adjustmentLevel", "adjustmentFieldsDropped", "adjustmentKind", "grade",
            "earlyWarningEligible", "discovery", "confirmation", "displayMergeRuleIds");
    private static final Set<String> TERM_KEYS = Set.of(
            "field", "type", "lower", "lowerInclusive", "upper", "upperInclusive", "values");
    private static final Set<String> METRIC_KEYS = Set.of(
            "support", "defects", "crudeRate", "crudeRateCiLower", "crudeRateCiUpper", "adjustedRate",
            "comparatorAdjustedRate", "riskDifference", "relativeRisk", "relativeRiskCiLower",
            "relativeRiskCiUpper", "pValue", "qValue", "reasonCode");
    private static final Set<String> RANGE_CONTEXT_KEYS = Set.of(
            "ap_plant", "ap_shift", "ap_thick_band", "ap_width_band", "f_jangip_gubun", "furnace_no",
            "hr_thick_band", "hr_width_band", "slab_width_band", "sm_plant", "steel_grade", "steel_usage");
    private static final Set<String> RULE_CONTEXT_KEYS = Set.of(
            "ap_plant", "ap_shift", "cc_gubun", "f_jangip_gubun", "furnace_no", "hr_thick_band",
            "hr_width_band", "slab_gubun", "slab_width_band", "sm_plant", "steel_grade", "steel_usage");

    public List<OperatingRange> compileRanges(List<JsonNode> nodes) {
        if (nodes == null) throw failure("ranges must not be null");
        List<OperatingRange> result = new ArrayList<>(nodes.size());
        Set<String> ids = new HashSet<>();
        for (int index = 0; index < nodes.size(); index++) {
            JsonNode node = object(nodes.get(index), "ranges[" + index + "]");
            OperatingRange range = compileRange(node, index);
            verifyIdentity(range.ruleId(), RiskDefinitionIds.operatingRangeId(node), "ranges[" + index + "].ruleId");
            if (!ids.add(range.ruleId())) throw failure("duplicate range ruleId " + range.ruleId());
            result.add(range);
        }
        return List.copyOf(result);
    }

    public List<QualityRule> compileRules(List<JsonNode> nodes) {
        if (nodes == null) throw failure("rules must not be null");
        List<QualityRule> result = new ArrayList<>(nodes.size());
        Set<String> ids = new HashSet<>();
        for (int index = 0; index < nodes.size(); index++) {
            JsonNode node = object(nodes.get(index), "rules[" + index + "]");
            QualityRule rule = compileRule(node, index);
            verifyIdentity(rule.ruleId(), RiskDefinitionIds.qualityRuleId(node), "rules[" + index + "].ruleId");
            if (!ids.add(rule.ruleId())) throw failure("duplicate quality ruleId " + rule.ruleId());
            result.add(rule);
        }
        return List.copyOf(result);
    }

    private OperatingRange compileRange(JsonNode node, int index) {
        String path = "ranges[" + index + "]";
        exactKeys(node, RANGE_KEYS, path);
        String ruleId = id(node, "ruleId", path);
        String field = text(node, "field", path);
        FieldRole role = enumeration(FieldRole.class, text(node, "fieldRole", path), path + ".fieldRole");
        ProcessStage stage = enumeration(ProcessStage.class, text(node, "firstAvailableStage", path), path + ".firstAvailableStage");
        EquipmentType equipment = enumeration(EquipmentType.class, text(node, "equipmentType", path), path + ".equipmentType");
        String equipmentId = text(node, "equipmentId", path);
        int level = integer(node, "contextLevel", path, 0);
        Map<String, RiskScalar> context = scalarMap(node.get("context"), path + ".context", RANGE_CONTEXT_KEYS);
        long support = integerLong(node, "support", path, 0);
        double median = number(node, "median", path);
        Double p01 = nullableNumber(node, "p01", path);
        double p05 = number(node, "p05", path);
        double p95 = number(node, "p95", path);
        Double p99 = nullableNumber(node, "p99", path);
        boolean lower = bool(node, "lowerTailEnabled", path);
        boolean upper = bool(node, "upperTailEnabled", path);
        if (lower && p01 == null) throw failure(path + ".p01 required when lower tail is enabled");
        if (upper && p99 == null) throw failure(path + ".p99 required when upper tail is enabled");
        if (p05 > median || median > p95 || (p01 != null && p01 > p05) || (p99 != null && p95 > p99)) {
            throw failure(path + " quantiles are not ordered");
        }
        return new OperatingRange(ruleId, field, role, stage, equipment, equipmentId, level, context,
                support, median, p01, p05, p95, p99, lower, upper);
    }

    private QualityRule compileRule(JsonNode node, int index) {
        String path = "rules[" + index + "]";
        exactKeys(node, RULE_KEYS, path);
        String ruleId = id(node, "ruleId", path);
        AnalysisFamily family = enumeration(AnalysisFamily.class, text(node, "analysisFamily", path), path + ".analysisFamily");
        EvidenceFamily evidence = enumeration(EvidenceFamily.class, text(node, "evidenceFamily", path), path + ".evidenceFamily");
        ProcessStage stage = enumeration(ProcessStage.class, text(node, "firstAvailableStage", path), path + ".firstAvailableStage");
        EquipmentType equipment = enumeration(EquipmentType.class, text(node, "equipmentType", path), path + ".equipmentType");
        ApplicationScope scope = enumeration(ApplicationScope.class, text(node, "applicationScope", path), path + ".applicationScope");
        String equipmentId = text(node, "equipmentId", path);
        if ((scope == ApplicationScope.PROCESS_GLOBAL) != equipmentId.equals("ALL")) {
            throw failure(path + " scope/equipmentId mismatch");
        }
        List<String> fields = stringList(node.get("fieldNames"), path + ".fieldNames", 1, 2, true);
        JsonNode predicate = object(node.get("predicate"), path + ".predicate");
        exactKeys(predicate, Set.of("allOf"), path + ".predicate");
        JsonNode termsNode = array(predicate.get("allOf"), path + ".predicate.allOf", 1, 2);
        List<PredicateTerm> terms = new ArrayList<>();
        for (int termIndex = 0; termIndex < termsNode.size(); termIndex++) {
            terms.add(compileTerm(object(termsNode.get(termIndex), path + ".predicate.allOf[" + termIndex + "]"),
                    path + ".predicate.allOf[" + termIndex + "]"));
        }
        if ((family == AnalysisFamily.INTERACTION) != (fields.size() == 2 && terms.size() == 2)) {
            throw failure(path + ".predicate.allOf does not match analysisFamily");
        }
        Set<String> termFields = new HashSet<>();
        terms.forEach(term -> termFields.add(term.field()));
        if (!termFields.equals(new HashSet<>(fields))) throw failure(path + " fieldNames/predicate mismatch");
        Map<String, RiskScalar> context = scalarMap(node.get("applicationContext"), path + ".applicationContext", RULE_CONTEXT_KEYS);
        int adjustmentLevel = integer(node, "adjustmentLevel", path, 0);
        List<String> dropped = stringList(node.get("adjustmentFieldsDropped"), path + ".adjustmentFieldsDropped", 0, Integer.MAX_VALUE, true);
        AdjustmentKind adjustment = enumeration(AdjustmentKind.class, text(node, "adjustmentKind", path), path + ".adjustmentKind");
        RiskGrade grade = enumeration(RiskGrade.class, text(node, "grade", path), path + ".grade");
        boolean early = bool(node, "earlyWarningEligible", path);
        if (stage == ProcessStage.AP_RECORDED_WITH_RESULT && early) {
            throw failure(path + " AP result cannot be early-warning eligible");
        }
        MetricEvidence discovery = metric(object(node.get("discovery"), path + ".discovery"), path + ".discovery");
        MetricEvidence confirmation = metric(object(node.get("confirmation"), path + ".confirmation"), path + ".confirmation");
        List<String> mergeIds = stringList(node.get("displayMergeRuleIds"), path + ".displayMergeRuleIds", 0, Integer.MAX_VALUE, true);
        mergeIds.forEach(value -> validateId(value, path + ".displayMergeRuleIds"));
        return new QualityRule(ruleId, family, evidence, stage, equipment, scope, equipmentId, fields,
                terms, context, adjustmentLevel, dropped, adjustment, grade, early, discovery, confirmation, mergeIds);
    }

    private PredicateTerm compileTerm(JsonNode node, String path) {
        exactKeys(node, TERM_KEYS, path);
        String field = text(node, "field", path);
        PredicateType type = enumeration(PredicateType.class, text(node, "type", path), path + ".type");
        Double lower = nullableNumber(node, "lower", path);
        Boolean lowerInclusive = nullableBoolean(node, "lowerInclusive", path);
        Double upper = nullableNumber(node, "upper", path);
        Boolean upperInclusive = nullableBoolean(node, "upperInclusive", path);
        List<RiskScalar> values = nullableScalarList(node.get("values"), path + ".values");
        if (type == PredicateType.NUMERIC_INTERVAL) {
            if (values != null || lower == null || upper == null || lowerInclusive == null || upperInclusive == null || lower > upper) {
                throw failure(path + " invalid NUMERIC_INTERVAL");
            }
        } else if (lower != null || upper != null || lowerInclusive != null || upperInclusive != null || values == null || values.isEmpty()) {
            throw failure(path + " invalid CATEGORY_IN");
        }
        return new PredicateTerm(field, type, lower, lowerInclusive, upper, upperInclusive, values);
    }

    private MetricEvidence metric(JsonNode node, String path) {
        exactKeys(node, METRIC_KEYS, path);
        long support = integerLong(node, "support", path, 0);
        long defects = integerLong(node, "defects", path, 0);
        if (defects > support) throw failure(path + " defects exceed support");
        ReasonCode reason = enumeration(ReasonCode.class, text(node, "reasonCode", path), path + ".reasonCode");
        return new MetricEvidence(support, defects,
                nullableNumber(node, "crudeRate", path), nullableNumber(node, "crudeRateCiLower", path),
                nullableNumber(node, "crudeRateCiUpper", path), nullableNumber(node, "adjustedRate", path),
                nullableNumber(node, "comparatorAdjustedRate", path), nullableNumber(node, "riskDifference", path),
                nullableNumber(node, "relativeRisk", path), nullableNumber(node, "relativeRiskCiLower", path),
                nullableNumber(node, "relativeRiskCiUpper", path), nullableNumber(node, "pValue", path),
                nullableNumber(node, "qValue", path), reason);
    }

    private static Map<String, RiskScalar> scalarMap(JsonNode node, String path, Set<String> allowedKeys) {
        object(node, path);
        LinkedHashMap<String, RiskScalar> values = new LinkedHashMap<>();
        node.fields().forEachRemaining(entry -> {
            if (!allowedKeys.contains(entry.getKey())) throw failure(path + " unexpected context field " + entry.getKey());
            values.put(entry.getKey(), scalar(entry.getValue(), path + "." + entry.getKey()));
        });
        return Map.copyOf(values);
    }

    private static List<RiskScalar> nullableScalarList(JsonNode node, String path) {
        if (node == null) throw failure(path + " missing");
        if (node.isNull()) return null;
        JsonNode array = array(node, path, 1, Integer.MAX_VALUE);
        List<RiskScalar> result = new ArrayList<>();
        for (int index = 0; index < array.size(); index++) result.add(scalar(array.get(index), path + "[" + index + "]"));
        if (new HashSet<>(result).size() != result.size()) throw failure(path + " contains duplicate scalar");
        return List.copyOf(result);
    }

    private static RiskScalar scalar(JsonNode node, String path) {
        if (node == null || !(node.isTextual() || node.isNumber() || node.isBoolean())) {
            throw failure(path + " must be a scalar");
        }
        if (node.isTextual()) return RiskScalar.of(node.textValue());
        if (node.isBoolean()) return RiskScalar.of(node.booleanValue());
        return RiskScalar.of(node.decimalValue());
    }

    private static void exactKeys(JsonNode node, Set<String> expected, String path) {
        Set<String> actual = new HashSet<>();
        node.fieldNames().forEachRemaining(actual::add);
        Set<String> missing = new HashSet<>(expected);
        missing.removeAll(actual);
        Set<String> extra = new HashSet<>(actual);
        extra.removeAll(expected);
        if (!missing.isEmpty() || !extra.isEmpty()) {
            throw failure(path + " keys invalid; missing=" + missing + ", unexpected=" + extra);
        }
    }

    private static JsonNode object(JsonNode node, String path) {
        if (node == null || !node.isObject()) throw failure(path + " must be an object");
        return node;
    }

    private static JsonNode array(JsonNode node, String path, int min, int max) {
        if (node == null || !node.isArray() || node.size() < min || node.size() > max) {
            throw failure(path + " must be an array with " + min + ".." + max + " entries");
        }
        return node;
    }

    private static String text(JsonNode node, String key, String path) {
        JsonNode value = node.get(key);
        if (value == null || !value.isTextual() || value.textValue().isEmpty()) throw failure(path + "." + key + " must be text");
        return value.textValue();
    }

    private static String id(JsonNode node, String key, String path) {
        String result = text(node, key, path);
        validateId(result, path + "." + key);
        return result;
    }

    private static void validateId(String value, String path) {
        if (!value.matches("sha256:[0-9a-f]{64}")) throw failure(path + " must be a sha256 id");
    }

    private static int integer(JsonNode node, String key, String path, int min) {
        long value = integerLong(node, key, path, min);
        if (value > Integer.MAX_VALUE) throw failure(path + "." + key + " too large");
        return (int) value;
    }

    private static long integerLong(JsonNode node, String key, String path, long min) {
        JsonNode value = node.get(key);
        if (value == null || !value.isIntegralNumber() || !value.canConvertToLong() || value.longValue() < min) {
            throw failure(path + "." + key + " must be an integer >= " + min);
        }
        return value.longValue();
    }

    private static double number(JsonNode node, String key, String path) {
        JsonNode value = node.get(key);
        if (value == null || !value.isNumber() || !Double.isFinite(value.doubleValue())) throw failure(path + "." + key + " must be finite number");
        return value.doubleValue();
    }

    private static Double nullableNumber(JsonNode node, String key, String path) {
        JsonNode value = node.get(key);
        if (value == null) throw failure(path + "." + key + " missing");
        if (value.isNull()) return null;
        return number(node, key, path);
    }

    private static boolean bool(JsonNode node, String key, String path) {
        JsonNode value = node.get(key);
        if (value == null || !value.isBoolean()) throw failure(path + "." + key + " must be boolean");
        return value.booleanValue();
    }

    private static Boolean nullableBoolean(JsonNode node, String key, String path) {
        JsonNode value = node.get(key);
        if (value == null) throw failure(path + "." + key + " missing");
        if (value.isNull()) return null;
        return bool(node, key, path);
    }

    private static List<String> stringList(JsonNode node, String path, int min, int max, boolean unique) {
        JsonNode array = array(node, path, min, max);
        List<String> result = new ArrayList<>();
        for (int index = 0; index < array.size(); index++) {
            JsonNode item = array.get(index);
            if (!item.isTextual() || item.textValue().isEmpty()) throw failure(path + " must contain non-empty text");
            result.add(item.textValue());
        }
        if (unique && new HashSet<>(result).size() != result.size()) throw failure(path + " must be unique");
        return List.copyOf(result);
    }

    private static <T extends Enum<T>> T enumeration(Class<T> type, String value, String path) {
        try {
            return Enum.valueOf(type, value);
        } catch (IllegalArgumentException exception) {
            throw failure(path + " invalid value " + value);
        }
    }

    private static String member(String value, Set<String> allowed, String path) {
        if (!allowed.contains(value)) throw failure(path + " invalid value " + value);
        return value;
    }

    private static RiskDefinitionException failure(String message) {
        return new RiskDefinitionException(message);
    }

    private static void verifyIdentity(String claimed, String computed, String path) {
        if (!claimed.equals(computed)) {
            throw failure(path + " identity mismatch; expected " + computed + " but found " + claimed);
        }
    }
}
