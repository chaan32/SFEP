"""Label-free Task 6 candidate and global-FDR contracts."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import time

import pandas as pd
import pytest
from jsonschema.validators import validator_for

from equipment_quality.deterministic import canonical_json_bytes
from equipment_quality.feature_roles import definitions
from equipment_quality import quality_intervals
from equipment_quality.quality_intervals import build_quality_rules, generate_candidates
from factories.quality import (
    analysis_config,
    adjacent_numeric_fixture,
    ap_candidate_fixture,
    caution_only_fixture,
    equipment_category_fixture,
    future_context_quality_fixture,
    grade_boundary_fixture,
    global_family_fixture,
    global_family_split,
    null_fixture,
    performance_fixture,
    q_values_by_rule_id,
    rows_with_labels,
    six_interaction_cells_fixture,
    split_from_rows,
    strong_repeated_fixture,
    tied_numeric_fixture,
    too_few_bootstraps_fixture,
)


CRITERIA_ID = "sha256:" + "1" * 64
LITERAL_GLOBAL_FAMILY_Q_VALUES = (
    0.023995181246224552,
    1.0,
    0.016898377393510317,
    1.0,
)


def _field_candidates(candidates, name: str):
    return [candidate for candidate in candidates if candidate.field_names == (name,)]


def test_candidate_predicates_do_not_change_when_only_labels_change():
    config = analysis_config()
    original = generate_candidates(rows_with_labels([0, 1, 0, 1]), definitions(config), config)
    flipped = generate_candidates(rows_with_labels([1, 0, 1, 0]), definitions(config), config)

    assert [candidate.predicate for candidate in original] == [
        candidate.predicate for candidate in flipped
    ]
    assert [candidate.rule_id for candidate in original] == [
        candidate.rule_id for candidate in flipped
    ]
    assert all("judge" not in vars(candidate) for candidate in original)


def test_empty_and_duplicate_tied_predicates_are_collapsed_before_bh():
    config = analysis_config()
    candidates = _field_candidates(
        generate_candidates(tied_numeric_fixture(), definitions(config), config),
        "f_pre_temp",
    )

    assert len(candidates) == 3
    assert len({candidate.canonical_predicate for candidate in candidates}) == 3
    assert [candidate.support for candidate in candidates] == [10, 10, 10]
    assert all(candidate.support > 0 for candidate in candidates)
    assert [candidate.predicate[0].to_wire() for candidate in candidates] == [
        {
            "field": "f_pre_temp", "type": "NUMERIC_INTERVAL",
            "lower": 1000.0, "lowerInclusive": True,
            "upper": 1000.0, "upperInclusive": True, "values": None,
        },
        {
            "field": "f_pre_temp", "type": "NUMERIC_INTERVAL",
            "lower": 1000.0, "lowerInclusive": False,
            "upper": 1100.0, "upperInclusive": True, "values": None,
        },
        {
            "field": "f_pre_temp", "type": "NUMERIC_INTERVAL",
            "lower": 1100.0, "lowerInclusive": False,
            "upper": 1200.0, "upperInclusive": True, "values": None,
        },
    ]


def test_category_levels_use_exact_unicode_values_and_deterministic_order():
    config = analysis_config()
    rows = pd.DataFrame(
        {
            "charge_id": ["1", "2", "3", "4"],
            "slab_grind": ["한글", "A", "é", "A"],
            "judge": ["양품"] * 4,
        }
    )

    candidates = _field_candidates(generate_candidates(rows, definitions(config), config), "slab_grind")

    assert [candidate.predicate[0].values for candidate in candidates] == [
        ("A",), ("é",), ("한글",),
    ]
    assert all(
        candidate.application_scope == "PROCESS_GLOBAL"
        and candidate.equipment_id == "ALL"
        for candidate in candidates
    )


def test_candidate_metadata_maps_to_wire_contract_without_labels():
    config = analysis_config()
    furnace = next(
        candidate
        for candidate in generate_candidates(
            equipment_category_fixture(), definitions(config), config
        )
        if candidate.field_names == ("furnace_no",)
        and candidate.predicate[0].values == ("1호기",)
    )

    assert furnace.application_scope == "EQUIPMENT_SPECIFIC"
    assert furnace.equipment_id == "1호기"
    assert furnace.evidence_family == "CHARGE"
    assert dict(furnace.application_context) == {}
    assert furnace.adjustment_kind == "UNADJUSTED_FALLBACK"
    assert "furnace_no" in furnace.adjustment_fields_dropped
    expected_preimage = {
        "analysisFamily": furnace.analysis_family,
        "fieldNames": list(furnace.field_names),
        "predicate": {
            "allOf": [clause.to_wire() for clause in furnace.predicate]
        },
        "firstAvailableStage": furnace.first_available_stage,
        "equipmentType": furnace.equipment_type,
        "applicationScope": furnace.application_scope,
        "equipmentId": furnace.equipment_id,
        "applicationContext": {},
        "adjustmentLevel": furnace.adjustment_level,
        "adjustmentFieldsDropped": list(furnace.adjustment_fields_dropped),
        "adjustmentKind": furnace.adjustment_kind,
    }
    assert furnace.rule_id == "sha256:" + hashlib.sha256(
        canonical_json_bytes(expected_preimage)
    ).hexdigest()
    with pytest.raises(TypeError):
        furnace.discovery_weights["new"] = 1.0


def test_only_source_equipment_identifier_categories_are_equipment_specific():
    config = analysis_config()
    rows = pd.DataFrame(
        {
            "sm_plant": ["SM1", "SM2"],
            "furnace_no": ["F1", "F2"],
            "ap_plant": ["AP1", "AP2"],
            "slab_grind": ["YES", "NO"],
            "judge": ["양품", "불량"],
        }
    )
    candidates = generate_candidates(rows, definitions(config), config)

    for candidate in candidates:
        if candidate.field_names[0] in {"sm_plant", "furnace_no", "ap_plant"}:
            assert candidate.application_scope == "EQUIPMENT_SPECIFIC"
            assert candidate.equipment_id == candidate.predicate[0].values[0]
        else:
            assert candidate.application_scope == "PROCESS_GLOBAL"
            assert candidate.equipment_id == "ALL"


def test_ap_rules_are_retrospective_only():
    config = analysis_config()
    ap = next(
        candidate
        for candidate in generate_candidates(
            ap_candidate_fixture(), definitions(config), config
        )
        if candidate.first_available_stage == "AP_RECORDED_WITH_RESULT"
    )
    assert ap.early_warning_eligible is False


def test_event_dates_do_not_change_candidate_bytes_and_stage_comes_from_definition():
    config = analysis_config()
    rows = rows_with_labels([0, 1, 0, 1])
    rows["f_ext_date"] = ["1900-01-01", "2999-12-31", None, "invalid"]
    changed = rows.copy(deep=True)
    changed["f_ext_date"] = list(reversed(rows["f_ext_date"].tolist()))

    before = _field_candidates(
        generate_candidates(rows, definitions(config), config), "f_pre_temp"
    )
    after = _field_candidates(
        generate_candidates(changed, definitions(config), config), "f_pre_temp"
    )

    assert [candidate.rule_id for candidate in before] == [
        candidate.rule_id for candidate in after
    ]
    assert {candidate.first_available_stage for candidate in before} == {
        "PREHEAT_COMPLETE"
    }


def test_all_and_only_configured_six_interactions_produce_nine_nonempty_cells():
    config = analysis_config()
    candidates = generate_candidates(
        six_interaction_cells_fixture(), definitions(config), config
    )
    interactions = [candidate for candidate in candidates if candidate.analysis_family == "INTERACTION"]

    observed = {}
    for candidate in interactions:
        observed.setdefault(candidate.field_names, []).append(candidate)
    assert tuple(observed) == config.fixed_interactions
    assert [len(observed[pair]) for pair in config.fixed_interactions] == [9] * 6
    assert all(candidate.support == 1 for candidate in interactions)
    assert all(
        candidate.application_scope == "PROCESS_GLOBAL"
        and candidate.equipment_id == "ALL"
        for candidate in interactions
    )


def test_empty_interaction_cells_are_omitted_before_global_bh():
    config = analysis_config()
    complete = six_interaction_cells_fixture()
    diagonal = complete.loc[
        complete["tundish_temp"] == complete["mlac_ratio"]
    ].reset_index(drop=True)
    rows = pd.concat([diagonal] * 10, ignore_index=True)
    rows["charge_id"] = [f"DIAG-{index:03d}" for index in range(len(rows))]

    interactions = [
        candidate
        for candidate in generate_candidates(rows, definitions(config), config)
        if candidate.analysis_family == "INTERACTION"
    ]

    observed = Counter(candidate.field_names for candidate in interactions)
    assert observed == Counter({pair: 3 for pair in config.fixed_interactions})
    assert all(candidate.support == 10 for candidate in interactions)


def test_complete_semantic_definition_surface_is_required_before_empty_result():
    config = analysis_config()
    complete = definitions(config)

    with pytest.raises(ValueError, match="complete"):
        generate_candidates(pd.DataFrame(), complete[:-1], config)
    with pytest.raises(ValueError, match="immutable config"):
        generate_candidates(
            pd.DataFrame(), (replace(complete[0], data_type="NUMBER"), *complete[1:]), config
        )


def test_numeric_nonfinite_and_boolean_values_do_not_enter_candidate_support():
    config = analysis_config()
    rows = pd.DataFrame(
        {
            "charge_id": [str(index) for index in range(6)],
            "f_pre_temp": [1.0, 2.0, None, math.nan, math.inf, True],
            "judge": ["양품"] * 6,
        }
    )

    candidates = _field_candidates(generate_candidates(rows, definitions(config), config), "f_pre_temp")

    assert sum(candidate.support for candidate in candidates) == 2
    assert all(
        math.isfinite(bound)
        for candidate in candidates
        for bound in (candidate.predicate[0].lower, candidate.predicate[0].upper)
    )


def test_bh_runs_over_the_complete_global_category_family_with_invalid_p_one():
    config = analysis_config()
    candidates = generate_candidates(global_family_fixture(), definitions(config), config)
    rules = build_quality_rules(global_family_split(candidates), definitions(config), config, CRITERIA_ID)
    selected = [rule for rule in rules if rule["fieldNames"] == ["slab_grind"]]

    assert [rule["predicate"]["allOf"][0]["values"] for rule in selected] == [
        ["A"], ["B"], ["C"], ["한글"],
    ]
    assert [rule["discovery"]["qValue"] for rule in selected] == pytest.approx(
        LITERAL_GLOBAL_FAMILY_Q_VALUES
    )
    assert selected[-1]["discovery"]["pValue"] == 1.0
    assert len(q_values_by_rule_id(rules)) == 4
    assert selected[0]["discovery"]["crudeRate"] == 0.05
    assert selected[0]["discovery"]["crudeRateCiLower"] == pytest.approx(
        0.02738264560076393
    )
    assert selected[0]["discovery"]["crudeRateCiUpper"] == pytest.approx(
        0.08957814813877599
    )


def test_bh_runs_exactly_once_per_configured_global_family_with_ties(monkeypatch):
    config = analysis_config()
    rows = six_interaction_cells_fixture()
    rows["slab_grind"] = ["A", "B", "C"] * 3
    candidates = generate_candidates(rows, definitions(config), config)
    expected_sizes = Counter(candidate.analysis_family for candidate in candidates)
    calls: list[tuple[float, ...]] = []
    original = quality_intervals.benjamini_hochberg

    def recording_bh(p_values):
        calls.append(tuple(p_values))
        return original(p_values)

    monkeypatch.setattr(quality_intervals, "benjamini_hochberg", recording_bh)
    build_quality_rules(split_from_rows(rows), definitions(config), config, CRITERIA_ID)

    assert len(calls) == len(config.fdr_families) == 3
    assert [len(call) for call in calls] == [
        expected_sizes[family] for family in config.fdr_families
    ]
    assert all(all(p_value == 1.0 for p_value in call) for call in calls)


def test_row_order_is_irrelevant_and_inputs_are_not_mutated():
    config = analysis_config()
    rows = tied_numeric_fixture()
    before = rows.copy(deep=True)

    first = generate_candidates(rows, definitions(config), config)
    second = generate_candidates(
        rows.iloc[::-1].reset_index(drop=True), tuple(reversed(definitions(config))), config
    )

    pd.testing.assert_frame_equal(rows, before)
    assert [(item.rule_id, item.canonical_predicate) for item in first] == [
        (item.rule_id, item.canonical_predicate) for item in second
    ]


def test_rule_id_collision_between_distinct_label_free_preimages_is_fatal(monkeypatch):
    config = analysis_config()
    monkeypatch.setattr(quality_intervals, "sha256_uri", lambda _payload: "sha256:" + "0" * 64)

    with pytest.raises(ValueError, match="rule ID collision"):
        generate_candidates(tied_numeric_fixture(), definitions(config), config)


def _risk_rule(rules, field: str = "slab_grind"):
    return next(
        rule
        for rule in rules
        if rule["fieldNames"] == [field]
        and rule["predicate"]["allOf"][0].get("values") == ["RISK"]
    )


def test_danger_requires_stratified_discovery_and_confirmation():
    config = analysis_config()
    rule = _risk_rule(
        build_quality_rules(strong_repeated_fixture(), definitions(config), config, CRITERIA_ID)
    )

    assert rule["grade"] == "DANGER"
    assert rule["adjustmentKind"] == "STRATIFIED"
    assert rule["discovery"]["relativeRiskCiLower"] > 1.0
    assert rule["confirmation"]["relativeRisk"] >= 1.5


def test_multiple_caution_candidates_are_never_promoted_to_danger():
    config = analysis_config()
    rules = build_quality_rules(caution_only_fixture(), definitions(config), config, CRITERIA_ID)
    caution = [rule for rule in rules if rule["grade"] == "CAUTION"]

    assert {tuple(rule["fieldNames"]) for rule in caution} >= {
        ("slab_grind",), ("cc_gubun",),
    }
    assert all(rule["grade"] != "DANGER" for rule in caution)


def test_no_qualified_interval_returns_zero_danger_rules():
    config = analysis_config()
    rules = build_quality_rules(null_fixture(), definitions(config), config, CRITERIA_ID)
    assert sum(rule["grade"] == "DANGER" for rule in rules) == 0


@pytest.mark.parametrize(
    ("fixture_name", "expected_grade"),
    [
        ("below_support", "INSUFFICIENT_EVIDENCE"),
        ("discovery_only", "UNCONFIRMED"),
        ("confirmed_caution", "CAUTION"),
        ("confirmed_stratified_danger", "DANGER"),
    ],
)
def test_grade_boundaries_are_exact(fixture_name, expected_grade):
    config = analysis_config()
    rule = _risk_rule(
        build_quality_rules(
            grade_boundary_fixture(fixture_name), definitions(config), config, CRITERIA_ID
        )
    )
    assert rule["grade"] == expected_grade


def _literal_metric(
    *,
    defects=10,
    relative_risk=2.0,
    risk_difference=0.01,
    q_value=0.05,
    reason_code="NONE",
):
    return quality_intervals.QualityMetric(
        support=200,
        defects=defects,
        crude_rate=0.05,
        crude_rate_ci_lower=0.02738264560076393,
        crude_rate_ci_upper=0.08957814813877599,
        adjusted_rate=0.05,
        comparator_adjusted_rate=0.04,
        risk_difference=risk_difference,
        relative_risk=relative_risk,
        relative_risk_ci_lower=None,
        relative_risk_ci_upper=None,
        p_value=0.01,
        q_value=q_value,
        reason_code=reason_code,
    )


def test_discovery_and_confirmation_threshold_inclusion_is_exact_on_each_side():
    config = analysis_config()
    caution = _literal_metric(
        defects=5, relative_risk=1.5, risk_difference=0.005, q_value=0.1
    )
    assert quality_intervals._discovery_caution_pass(caution, config)
    assert not quality_intervals._discovery_caution_pass(
        replace(caution, relative_risk=math.nextafter(1.5, -math.inf)), config
    )
    assert not quality_intervals._discovery_caution_pass(
        replace(caution, risk_difference=math.nextafter(0.005, -math.inf)), config
    )
    assert not quality_intervals._discovery_caution_pass(
        replace(caution, q_value=math.nextafter(0.1, math.inf)), config
    )
    assert not quality_intervals._discovery_caution_pass(
        replace(caution, defects=4, reason_code="LOW_DEFECT_COUNT"), config
    )

    danger = _literal_metric()
    assert quality_intervals._discovery_danger_prebootstrap(danger, config)
    assert not quality_intervals._discovery_danger_prebootstrap(
        replace(danger, defects=9), config
    )
    assert not quality_intervals._discovery_danger_prebootstrap(
        replace(danger, relative_risk=math.nextafter(2.0, -math.inf)), config
    )
    assert not quality_intervals._discovery_danger_prebootstrap(
        replace(danger, risk_difference=math.nextafter(0.01, -math.inf)), config
    )
    assert not quality_intervals._discovery_danger_prebootstrap(
        replace(danger, q_value=math.nextafter(0.05, math.inf)), config
    )

    confirmation = replace(danger, relative_risk=math.nextafter(1.0, math.inf))
    assert quality_intervals._confirmation_caution_pass(confirmation, config)
    assert not quality_intervals._confirmation_caution_pass(
        replace(confirmation, relative_risk=1.0), config
    )
    assert not quality_intervals._confirmation_caution_pass(
        replace(confirmation, risk_difference=0.0), config
    )
    assert quality_intervals._confirmation_danger_pass(
        replace(confirmation, relative_risk=1.5), config
    )
    assert not quality_intervals._confirmation_danger_pass(
        replace(confirmation, relative_risk=math.nextafter(1.5, -math.inf)), config
    )


def test_future_stage_context_and_same_event_continuous_proxy_are_dropped():
    config = analysis_config()
    hierarchies = dict(config.risk_adjustment_hierarchies)
    hierarchies["FURNACE"] = (
        ("furnace_no", "steel_grade", "hr_thick_band", "f_pre_interval"),
        ("steel_grade",),
        (),
    )
    changed = replace(config, risk_adjustment_hierarchies=hierarchies)
    rule = next(
        rule
        for rule in build_quality_rules(
            future_context_quality_fixture(), definitions(changed), changed, CRITERIA_ID
        )
        if rule["fieldNames"] == ["f_pre_temp"]
        and rule["predicate"]["allOf"][0]["lower"] == 1000.0
    )

    assert "hr_thick_band" not in rule["applicationContext"]
    assert "hr_thick_band" in rule["adjustmentFieldsDropped"]
    assert "f_pre_interval" in rule["adjustmentFieldsDropped"]
    assert "f_pre_interval" not in rule["applicationContext"]


def test_product_state_candidate_also_drops_same_event_continuous_operation_proxy():
    config = analysis_config()
    hierarchies = dict(config.risk_adjustment_hierarchies)
    hierarchies["FURNACE"] = (("f_jangip_temp", "steel_grade"), ())
    changed = replace(config, risk_adjustment_hierarchies=hierarchies)
    rows = future_context_quality_fixture().discovery_rows.copy(deep=True)
    rows["slab_width"] = [1000.0 if value == "RISK" else 1200.0 for value in rows["slab_grind"]]
    rows["f_jangip_temp"] = [500.0 + index for index in range(len(rows))]

    candidate = next(
        item
        for item in generate_candidates(rows, definitions(changed), changed)
        if item.field_names == ("slab_width",)
        and item.predicate[0].lower == 1000.0
    )

    assert "f_jangip_temp" in candidate.adjustment_fields_dropped
    assert "f_jangip_temp" not in candidate.adjustment_fields


def test_categorical_candidate_also_drops_same_event_continuous_operation_proxy():
    config = analysis_config()
    hierarchies = dict(config.risk_adjustment_hierarchies)
    hierarchies["FURNACE"] = (("f_jangip_temp",), ())
    changed = replace(config, risk_adjustment_hierarchies=hierarchies)
    rows = equipment_category_fixture()
    rows["f_jangip_temp"] = [600.0, 610.0, 620.0]

    candidate = next(
        item
        for item in generate_candidates(rows, definitions(changed), changed)
        if item.field_names == ("furnace_no",)
        and item.predicate[0].values == ("1호기",)
    )

    assert "f_jangip_temp" in candidate.adjustment_fields_dropped
    assert "f_jangip_temp" not in candidate.adjustment_fields


def test_rr_ci_failure_is_explicit_and_cannot_be_danger():
    config = analysis_config()
    changed = replace(config, bootstrap={"replicates": 10, "minimumValidReplicates": 1900})
    rule = _risk_rule(
        build_quality_rules(
            too_few_bootstraps_fixture(), definitions(changed), changed, CRITERIA_ID
        )
    )

    assert rule["discovery"]["relativeRiskCiLower"] is None
    assert rule["discovery"]["relativeRiskCiUpper"] is None
    assert rule["discovery"]["reasonCode"] == "TOO_FEW_VALID_BOOTSTRAPS"
    assert rule["grade"] != "DANGER"


def test_unadjusted_fallback_can_never_be_danger_even_with_large_effect():
    config = analysis_config()
    split = adjacent_numeric_fixture()
    rule = next(
        rule
        for rule in build_quality_rules(split, definitions(config), config, CRITERIA_ID)
        if rule["fieldNames"] == ["f_pre_temp"]
        and rule["predicate"]["allOf"][0]["upper"] == 0.0
    )

    assert rule["adjustmentKind"] == "UNADJUSTED_FALLBACK"
    assert rule["discovery"]["relativeRisk"] >= 2.0
    assert rule["grade"] == "CAUTION"


def test_adjacent_same_grade_numeric_rules_only_receive_display_annotations():
    config = analysis_config()
    rules = [
        rule
        for rule in build_quality_rules(
            adjacent_numeric_fixture(), definitions(config), config, CRITERIA_ID
        )
        if rule["fieldNames"] == ["f_pre_temp"]
    ]
    cautions = [rule for rule in rules if rule["grade"] == "CAUTION"]

    assert len(cautions) == 2
    expected_ids = sorted(rule["ruleId"] for rule in cautions)
    assert all(rule["displayMergeRuleIds"] == expected_ids for rule in cautions)
    assert [rule["discovery"]["support"] for rule in cautions] == [200, 200]
    assert [rule["discovery"]["defects"] for rule in cautions] == [20, 20]


def test_confirmation_reuses_discovery_numeric_boundaries_and_rule_ids():
    config = analysis_config()
    split = adjacent_numeric_fixture()
    mutated_confirmation = split.confirmation_rows.copy(deep=True)
    mutated_confirmation["f_pre_temp"] = mutated_confirmation["f_pre_temp"] * 1000 + 17
    mutated = replace(split, confirmation_rows=mutated_confirmation)

    before = build_quality_rules(split, definitions(config), config, CRITERIA_ID)
    after = build_quality_rules(mutated, definitions(config), config, CRITERIA_ID)

    assert [(rule["ruleId"], rule["predicate"]) for rule in before] == [
        (rule["ruleId"], rule["predicate"]) for rule in after
    ]


def test_confirmation_reuses_discovery_strata_and_weights_without_retuning():
    config = analysis_config()
    split = strong_repeated_fixture()
    discovery_candidate = next(
        candidate
        for candidate in generate_candidates(
            split.discovery_rows, definitions(config), config
        )
        if candidate.field_names == ("slab_grind",)
        and candidate.predicate[0].values == ("RISK",)
    )
    changed_confirmation = split.confirmation_rows.copy(deep=True)
    changed_confirmation["sm_plant"] = "NEW_CONFIRMATION_ONLY_PLANT"
    changed = replace(split, confirmation_rows=changed_confirmation)

    rule = _risk_rule(
        build_quality_rules(changed, definitions(config), config, CRITERIA_ID)
    )

    assert rule["ruleId"] == discovery_candidate.rule_id
    assert "sm_plant" in discovery_candidate.adjustment_fields
    assert rule["confirmation"]["support"] == 0
    assert rule["confirmation"]["reasonCode"] == "NO_INFORMATIVE_STRATA"


def test_holdout_features_labels_and_future_stage_values_cannot_change_rule_bytes():
    config = analysis_config()
    split = strong_repeated_fixture()
    holdout = split.discovery_rows.iloc[:5].copy(deep=True)
    holdout["judge"] = ["양품", "불량", "양품", "불량", "양품"]
    holdout["ap_line_speed"] = [-999999.0] * 5
    with_holdout = replace(split, holdout_rows=holdout)
    mutated_holdout = holdout.copy(deep=True)
    mutated_holdout["judge"] = list(reversed(mutated_holdout["judge"].tolist()))
    mutated_holdout["ap_line_speed"] = [999999.0] * 5
    mutated = replace(split, holdout_rows=mutated_holdout)

    first = build_quality_rules(with_holdout, definitions(config), config, CRITERIA_ID)
    second = build_quality_rules(mutated, definitions(config), config, CRITERIA_ID)

    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_mature_discovery_feature_or_label_change_changes_appropriate_rule_bytes():
    config = analysis_config()
    split = strong_repeated_fixture()
    changed_rows = split.discovery_rows.copy(deep=True)
    changed_rows.loc[0, "slab_grind"] = "BASE"
    changed_rows.loc[1, "judge"] = "양품"
    changed = replace(split, discovery_rows=changed_rows)

    original = build_quality_rules(split, definitions(config), config, CRITERIA_ID)
    mutated = build_quality_rules(changed, definitions(config), config, CRITERIA_ID)

    assert canonical_json_bytes(original) != canonical_json_bytes(mutated)


def test_rule_id_and_predicate_are_label_free_while_statistics_can_change():
    config = analysis_config()
    split = strong_repeated_fixture()
    flipped = split.discovery_rows.copy(deep=True)
    flipped["judge"] = ["불량" if value == "양품" else "양품" for value in flipped["judge"]]
    changed = replace(split, discovery_rows=flipped)

    before = build_quality_rules(split, definitions(config), config, CRITERIA_ID)
    after = build_quality_rules(changed, definitions(config), config, CRITERIA_ID)

    assert [(rule["ruleId"], rule["predicate"]) for rule in before] == [
        (rule["ruleId"], rule["predicate"]) for rule in after
    ]
    assert [rule["discovery"] for rule in before] != [rule["discovery"] for rule in after]


def test_rules_are_deterministic_under_row_order_and_do_not_mutate_split_inputs():
    config = analysis_config()
    split = strong_repeated_fixture()
    discovery_before = split.discovery_rows.copy(deep=True)
    confirmation_before = split.confirmation_rows.copy(deep=True)
    reversed_split = replace(
        split,
        discovery_rows=split.discovery_rows.iloc[::-1].reset_index(drop=True),
        confirmation_rows=split.confirmation_rows.iloc[::-1].reset_index(drop=True),
    )

    first = build_quality_rules(split, definitions(config), config, CRITERIA_ID)
    second = build_quality_rules(reversed_split, tuple(reversed(definitions(config))), config, CRITERIA_ID)

    pd.testing.assert_frame_equal(split.discovery_rows, discovery_before)
    pd.testing.assert_frame_equal(split.confirmation_rows, confirmation_before)
    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_every_atomic_rule_has_all_fields_and_validates_against_normative_schema():
    config = analysis_config()
    rules = build_quality_rules(null_fixture(), definitions(config), config, CRITERIA_ID)
    schema_path = Path(__file__).resolve().parents[2] / "contracts/equipment-monitor/v1/quality_risk_intervals.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = validator_for(schema)
    validator.check_schema(schema)

    validator(schema).validate(
        {
            "schemaVersion": "sfep-quality-rules/v1",
            "criteriaId": CRITERIA_ID,
            "asOf": "2025-02-20",
            "rules": rules,
        }
    )
    assert all(set(rule["discovery"]) == {
        "support", "defects", "crudeRate", "crudeRateCiLower", "crudeRateCiUpper",
        "adjustedRate", "comparatorAdjustedRate", "riskDifference", "relativeRisk",
        "relativeRiskCiLower", "relativeRiskCiUpper", "pValue", "qValue", "reasonCode",
    } for rule in rules)


def test_actual_scale_candidate_surface_completes_within_bounded_cost():
    config = analysis_config()
    rows = performance_fixture()

    started = time.perf_counter()
    candidates = generate_candidates(rows, definitions(config), config)
    elapsed = time.perf_counter() - started

    assert len(candidates) >= 250
    assert elapsed < 8.0
