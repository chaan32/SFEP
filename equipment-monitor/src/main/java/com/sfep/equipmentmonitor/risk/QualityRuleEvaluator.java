package com.sfep.equipmentmonitor.risk;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

public final class QualityRuleEvaluator {
    public List<RuleEvaluation> evaluate(RiskEvent event, List<QualityRule> definitions) {
        List<RuleEvaluation> result = new ArrayList<>();
        for (QualityRule rule : List.copyOf(definitions)) {
            if (!appliesToEvent(rule, event)) continue;
            if (!contextMatches(rule.applicationContext(), event.values())) continue;
            boolean missing = rule.allOf().stream().anyMatch(term -> !event.values().containsKey(term.field()));
            if (missing) {
                result.add(evaluation(rule, RuleMatchStatus.DATA_MISSING, false));
                continue;
            }
            boolean matched = rule.allOf().stream().allMatch(term -> matches(term, event.values().get(term.field())));
            result.add(evaluation(rule, matched ? RuleMatchStatus.MATCHED : RuleMatchStatus.NOT_MATCHED, matched));
        }
        return List.copyOf(result);
    }

    public RuleEvaluationSummary summarize(List<RuleEvaluation> evaluations) {
        List<RuleEvaluation> matched = evaluations.stream().filter(RuleEvaluation::matched).toList();
        boolean dataMissing = evaluations.stream()
                .anyMatch(value -> value.status() == RuleMatchStatus.DATA_MISSING);
        Map<EvidenceFamily, RiskGrade> actionableByFamily = new java.util.EnumMap<>(EvidenceFamily.class);
        matched.stream()
                .filter(value -> value.grade() == RiskGrade.CAUTION || value.grade() == RiskGrade.DANGER)
                .forEach(value -> actionableByFamily.merge(
                        value.evidenceFamily(), value.grade(), QualityRuleEvaluator::higher));
        RiskGrade highest = actionableByFamily.values().stream()
                .max(Comparator.comparingInt(QualityRuleEvaluator::rank))
                .orElseGet(() -> !matched.isEmpty()
                        && matched.stream().allMatch(value -> value.grade() == RiskGrade.INSUFFICIENT_EVIDENCE)
                        ? RiskGrade.INSUFFICIENT_EVIDENCE
                        : dataMissing ? RiskGrade.INSUFFICIENT_EVIDENCE : RiskGrade.NORMAL);
        return new RuleEvaluationSummary(highest, actionableByFamily.size());
    }

    private static boolean appliesToEvent(QualityRule rule, RiskEvent event) {
        if (rule.firstAvailableStage() != event.stage() || rule.equipmentType() != event.equipmentType()) return false;
        return rule.applicationScope() == ApplicationScope.PROCESS_GLOBAL || rule.equipmentId().equals(event.equipmentId());
    }

    private static boolean contextMatches(Map<String, RiskScalar> required, Map<String, RiskScalar> values) {
        return required.entrySet().stream().allMatch(entry -> {
            RiskScalar actual = values.get(entry.getKey());
            return actual != null && actual.sameValue(entry.getValue());
        });
    }

    private static boolean matches(PredicateTerm term, RiskScalar value) {
        if (term.type() == PredicateType.CATEGORY_IN) {
            return term.values().stream().anyMatch(value::sameValue);
        }
        if (!value.isNumber()) return false;
        double number = value.doubleValue();
        boolean lower = term.lowerInclusive() ? number >= term.lower() : number > term.lower();
        boolean upper = term.upperInclusive() ? number <= term.upper() : number < term.upper();
        return lower && upper;
    }

    private static RuleEvaluation evaluation(QualityRule rule, RuleMatchStatus status, boolean matched) {
        boolean alert = matched && rule.earlyWarningEligible()
                && (rule.grade() == RiskGrade.CAUTION || rule.grade() == RiskGrade.DANGER);
        return new RuleEvaluation(rule.ruleId(), rule.evidenceFamily(), status, matched, rule.grade(), alert,
                !rule.earlyWarningEligible(), rule.discovery(), rule.confirmation());
    }

    private static RiskGrade higher(RiskGrade left, RiskGrade right) {
        return rank(left) >= rank(right) ? left : right;
    }

    private static int rank(RiskGrade grade) {
        return switch (grade) {
            case DANGER -> 5;
            case CAUTION -> 4;
            case NORMAL -> 3;
            case UNCONFIRMED -> 2;
            case INSUFFICIENT_EVIDENCE -> 1;
        };
    }
}
