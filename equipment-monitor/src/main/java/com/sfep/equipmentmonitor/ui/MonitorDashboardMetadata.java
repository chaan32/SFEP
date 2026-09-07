package com.sfep.equipmentmonitor.ui;

import com.fasterxml.jackson.databind.JsonNode;
import com.sfep.equipmentmonitor.bundle.LoadedBundle;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/** Sealed-bundle facts shown by the desktop monitor. */
public record MonitorDashboardMetadata(
        String bundleId,
        String criteriaId,
        String asOf,
        String evaluationMode,
        String dateFrom,
        String dateTo,
        long holdoutTotal,
        long replayRows,
        int rangeDefinitionCount,
        int qualityRuleDefinitionCount,
        List<ManagementFact> managementFacts,
        List<DefinitionView> definitions) {
    public MonitorDashboardMetadata {
        Objects.requireNonNull(bundleId, "bundleId");
        Objects.requireNonNull(criteriaId, "criteriaId");
        Objects.requireNonNull(asOf, "asOf");
        Objects.requireNonNull(evaluationMode, "evaluationMode");
        Objects.requireNonNull(dateFrom, "dateFrom");
        Objects.requireNonNull(dateTo, "dateTo");
        if (holdoutTotal < 0 || replayRows < 0
                || rangeDefinitionCount < 0 || qualityRuleDefinitionCount < 0) {
            throw new IllegalArgumentException("dashboard counts must be non-negative");
        }
        managementFacts = List.copyOf(Objects.requireNonNull(managementFacts, "managementFacts"));
        definitions = List.copyOf(Objects.requireNonNull(definitions, "definitions"));
    }

    public MonitorDashboardMetadata(
            String bundleId,
            String criteriaId,
            String asOf,
            String evaluationMode,
            String dateFrom,
            String dateTo,
            long holdoutTotal,
            long replayRows,
            int rangeDefinitionCount,
            int qualityRuleDefinitionCount) {
        this(bundleId, criteriaId, asOf, evaluationMode, dateFrom, dateTo,
                holdoutTotal, replayRows, rangeDefinitionCount, qualityRuleDefinitionCount,
                List.of(), List.of());
    }

    public static MonitorDashboardMetadata from(LoadedBundle bundle) {
        Objects.requireNonNull(bundle, "bundle");
        return new MonitorDashboardMetadata(
                bundle.bundleId(),
                bundle.criteriaId(),
                bundle.summary().asOf(),
                bundle.summary().evaluationMode(),
                bundle.summary().dateFrom(),
                bundle.summary().dateTo(),
                bundle.summary().holdoutTotal(),
                bundle.replay().metadata().rowCount(),
                bundle.ranges().size(),
                bundle.rules().size(),
                managementFacts(bundle.management()),
                definitions(bundle));
    }

    private static List<ManagementFact> managementFacts(LoadedBundle.ManagementProjection management) {
        List<ManagementFact> facts = new ArrayList<>();
        management.sourceHashes().forEach((role, hash) -> facts.add(
                new ManagementFact("원천 데이터 해시", sourceLabel(role), hash)));
        management.artifactHashes().forEach((role, hash) -> facts.add(
                new ManagementFact("산출물 해시", role.fileName(), hash)));
        management.quarantineCounts().forEach((reason, count) -> facts.add(
                new ManagementFact("격리 건수", quarantineLabel(reason), Long.toString(count))));
        management.labelCensoringCounts().forEach((reason, count) -> facts.add(
                new ManagementFact("라벨 검열 건수", censoringLabel(reason), Long.toString(count))));
        addPurgeFacts(facts, "외부", management.chargePurgeCounts().outer());
        addPurgeFacts(facts, "내부", management.chargePurgeCounts().inner());
        return List.copyOf(facts);
    }

    private static void addPurgeFacts(
            List<ManagementFact> facts,
            String scope,
            LoadedBundle.PurgeCount count) {
        facts.add(new ManagementFact("장입 제거 집계", scope + " 장입 수", Long.toString(count.chargeCount())));
        facts.add(new ManagementFact("장입 제거 집계", scope + " 행 수", Long.toString(count.rowCount())));
    }

    private static List<DefinitionView> definitions(LoadedBundle bundle) {
        List<DefinitionView> definitions = new ArrayList<>();
        bundle.ranges().forEach(node -> definitions.add(new DefinitionView(
                "설비 운전범위",
                text(node, "ruleId"),
                text(node, "firstAvailableStage"),
                text(node, "equipmentType") + " / " + text(node, "equipmentId"),
                text(node, "field"),
                "맥락 수준 " + node.path("contextLevel").asInt(),
                "p01=" + jsonValue(node.path("p01"))
                        + ", p05=" + jsonValue(node.path("p05"))
                        + ", p95=" + jsonValue(node.path("p95"))
                        + ", p99=" + jsonValue(node.path("p99")))));
        bundle.rules().forEach(node -> definitions.add(new DefinitionView(
                "품질 위험구간",
                text(node, "ruleId"),
                text(node, "firstAvailableStage"),
                text(node, "equipmentType") + " / " + text(node, "equipmentId"),
                node.path("fieldNames").toString(),
                text(node, "evidenceFamily") + " / " + text(node, "grade"),
                node.path("predicate").toString())));
        return List.copyOf(definitions);
    }

    private static String text(JsonNode node, String field) {
        return node.path(field).asText("-");
    }

    private static String jsonValue(JsonNode value) {
        return value == null || value.isMissingNode() || value.isNull() ? "-" : value.asText();
    }

    private static String sourceLabel(LoadedBundle.SourceRole role) {
        return switch (role) {
            case SM_CC -> "제강·연주";
            case FUR_HR -> "가열·열연";
            case AP -> "AP 후행 품질";
        };
    }

    private static String quarantineLabel(LoadedBundle.QuarantineReason reason) {
        return switch (reason) {
            case MISSING_SM_CC_KEY -> "제강·연주 키 누락";
            case MISSING_FUR_HR_KEY -> "가열·열연 키 누락";
            case MISSING_AP_HR_COIL_ID -> "AP 열연 코일 ID 누락";
            case MISSING_FUR_HR_COIL_ID -> "가열·열연 코일 ID 누락";
            case MISSING_AP_PROD_ID -> "AP 제품 ID 누락";
            case DUPLICATE_SM_CC_KEY -> "중복 제강·연주 키";
            case DUPLICATE_FUR_HR_KEY -> "중복 가열·열연 키";
            case DUPLICATE_AP_KEY -> "중복 AP 키";
            case DUPLICATE_FUR_HR_COIL_KEY -> "중복 가열·열연 코일 키";
            case DUPLICATE_AP_PROD_ID -> "중복 AP 제품 ID";
            case INVALID_FUR_HR_DATE -> "잘못된 가열·열연 날짜";
            case IMPOSSIBLE_STAGE_DATE_ORDER -> "불가능한 공정 날짜 순서";
            case UNLINKED_SM_CC -> "연결되지 않은 제강·연주";
            case UNLINKED_FUR_HR -> "연결되지 않은 가열·열연";
            case UNLINKED_AP -> "연결되지 않은 AP";
        };
    }

    private static String censoringLabel(LoadedBundle.LabelCensoringReason reason) {
        return switch (reason) {
            case AP_UNLINKED -> "AP 연결 안 됨";
            case LABEL_MISSING -> "품질 라벨 누락";
            case LABEL_NOT_YET_AVAILABLE -> "라벨 공개 시점 미도달";
        };
    }

    public record ManagementFact(String category, String name, String value) {
        public ManagementFact {
            Objects.requireNonNull(category, "category");
            Objects.requireNonNull(name, "name");
            Objects.requireNonNull(value, "value");
        }
    }

    public record DefinitionView(
            String kind,
            String ruleId,
            String stage,
            String equipment,
            String target,
            String level,
            String interval) {
        public DefinitionView {
            Objects.requireNonNull(kind, "kind");
            Objects.requireNonNull(ruleId, "ruleId");
            Objects.requireNonNull(stage, "stage");
            Objects.requireNonNull(equipment, "equipment");
            Objects.requireNonNull(target, "target");
            Objects.requireNonNull(level, "level");
            Objects.requireNonNull(interval, "interval");
        }
    }
}
