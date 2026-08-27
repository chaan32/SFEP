"""Label-free Task 6 candidate and global-FDR contracts."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import select
import signal
import sys
import threading
import time
import warnings

import pandas as pd
import pytest
from jsonschema.validators import validator_for

from equipment_quality.deterministic import canonical_json_bytes
from equipment_quality.feature_roles import definitions
from equipment_quality import models as equipment_models, quality_intervals
from equipment_quality.quality_intervals import build_quality_rules, generate_candidates
from equipment_quality.statistics import BootstrapCi
from factories.quality import (
    analysis_config,
    adjacent_numeric_fixture,
    ap_candidate_fixture,
    caution_only_fixture,
    caution_unconfirmed_fixture,
    equipment_category_fixture,
    future_context_quality_fixture,
    grade_boundary_fixture,
    global_family_fixture,
    global_family_split,
    identified_split_and_catalog,
    null_fixture,
    performance_fixture,
    q_values_by_rule_id,
    rows_with_labels,
    six_interaction_cells_fixture,
    split_from_rows,
    strong_repeated_fixture,
    tied_numeric_fixture,
    too_few_bootstraps_fixture,
    zero_variance_fixture,
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
    ("split_factory", "expected_grade"),
    [
        (caution_only_fixture, "CAUTION"),
        (null_fixture, "NORMAL"),
        (caution_unconfirmed_fixture, "UNCONFIRMED"),
    ],
)
def test_supported_discovery_metrics_keep_charge_bootstrap_ci_independent_of_grade(
    split_factory, expected_grade
):
    config = analysis_config()
    rule = _risk_rule(
        build_quality_rules(split_factory(), definitions(config), config, CRITERIA_ID)
    )

    assert rule["grade"] == expected_grade
    assert rule["discovery"]["relativeRiskCiLower"] is not None
    assert rule["discovery"]["relativeRiskCiUpper"] is not None
    assert rule["discovery"]["reasonCode"] == "NONE"
    assert rule["confirmation"]["relativeRiskCiLower"] is None
    assert rule["confirmation"]["relativeRiskCiUpper"] is None
    assert rule["confirmation"]["pValue"] is None
    assert rule["confirmation"]["qValue"] is None
    if expected_grade == "CAUTION":
        assert rule["discovery"]["relativeRiskCiLower"] == pytest.approx(
            1.1925243227955165
        )
        assert rule["discovery"]["relativeRiskCiUpper"] == pytest.approx(
            2.2188319134993444
        )


def test_bootstrap_selection_uses_metric_applicability_once_per_rule_not_grade(
    monkeypatch,
):
    config = analysis_config()
    cases = (
        (caution_only_fixture(), "slab_grind", "RISK", "CAUTION", "STRATIFIED"),
        (null_fixture(), "slab_grind", "RISK", "NORMAL", "STRATIFIED"),
        (
            caution_unconfirmed_fixture(),
            "slab_grind",
            "RISK",
            "UNCONFIRMED",
            "STRATIFIED",
        ),
        (
            adjacent_numeric_fixture(),
            "f_pre_temp",
            0.0,
            "CAUTION",
            "UNADJUSTED_FALLBACK",
        ),
    )

    for split, field, predicate_value, grade, adjustment_kind in cases:
        calls: list[str] = []

        def successful_bootstrap(_rows, _criteria_id, rule_id, *, replicates):
            assert replicates == 2000
            calls.append(rule_id)
            return BootstrapCi(0.5, 2.5, 2000, "NONE")

        monkeypatch.setattr(
            quality_intervals, "charge_bootstrap_rr_ci", successful_bootstrap
        )
        rules = build_quality_rules(split, definitions(config), config, CRITERIA_ID)
        if field == "slab_grind":
            target = _risk_rule(rules)
        else:
            target = next(
                rule
                for rule in rules
                if rule["fieldNames"] == [field]
                and rule["predicate"]["allOf"][0]["upper"] == predicate_value
            )

        assert target["grade"] == grade
        assert target["adjustmentKind"] == adjustment_kind
        assert target["discovery"]["relativeRiskCiLower"] == 0.5
        assert Counter(calls)[target["ruleId"]] == 1
        populated_ids = {
            rule["ruleId"]
            for rule in rules
            if rule["discovery"]["relativeRiskCiLower"] is not None
        }
        assert set(calls) == populated_ids
        assert all(count == 1 for count in Counter(calls).values())
        for rule in rules:
            if rule["ruleId"] not in populated_ids:
                assert rule["discovery"]["reasonCode"] in {
                    "LOW_SUPPORT",
                    "LOW_DEFECT_COUNT",
                    "NO_INFORMATIVE_STRATA",
                    "ZERO_COMPARATOR_RISK",
                    "NON_FINITE_ESTIMATE",
                }


def test_successful_bootstrap_preserves_a_genuine_metric_reason():
    config = analysis_config()
    rule = _risk_rule(
        build_quality_rules(
            zero_variance_fixture(), definitions(config), config, CRITERIA_ID
        )
    )

    assert rule["grade"] == "NORMAL"
    assert rule["discovery"]["relativeRiskCiLower"] is not None
    assert rule["discovery"]["relativeRiskCiUpper"] is not None
    assert (
        rule["discovery"]["relativeRiskCiLower"]
        <= rule["discovery"]["relativeRiskCiUpper"]
    )
    assert rule["discovery"]["reasonCode"] == "ZERO_VARIANCE"


def _bootstrap_ci() -> BootstrapCi:
    return BootstrapCi(0.75, 1.25, 2000, "NONE")


def _bootstrap_rule_id(digit: str) -> str:
    return "sha256:" + digit * 64


def _bootstrap_response(index: int, rule_id: str, interval=None):
    return quality_intervals._BootstrapResponse(
        index,
        rule_id,
        _bootstrap_ci() if interval is None else interval,
    )


@pytest.mark.parametrize(
    ("responses", "message"),
    [
        (
            lambda first, second: (first, first),
            "duplicate",
        ),
        (
            lambda first, second: (first,),
            "cardinality",
        ),
        (
            lambda first, second: (
                first,
                _bootstrap_response(2, second.rule_id),
            ),
            "range",
        ),
        (
            lambda first, second: (
                first,
                _bootstrap_response(2, second.rule_id),
            ),
            "unexpected",
        ),
        (
            lambda first, second: (
                first,
                _bootstrap_response(1, _bootstrap_rule_id("3")),
            ),
            "ruleId",
        ),
        (
            lambda first, second: (
                first,
                _bootstrap_response(1, second.rule_id, object()),
            ),
            "BootstrapCi",
        ),
    ],
)
def test_bootstrap_responses_must_be_an_exact_bijection_before_application(
    responses, message
):
    expected = (
        (0, _bootstrap_rule_id("1"), 2000),
        (1, _bootstrap_rule_id("2"), 2000),
    )
    first = _bootstrap_response(expected[0][0], expected[0][1])
    second = _bootstrap_response(expected[1][0], expected[1][1])
    result_size = 3 if message == "unexpected" else 2

    with pytest.raises(RuntimeError, match=message):
        quality_intervals._validate_bootstrap_responses(
            expected,
            responses(first, second),
            result_size=result_size,
        )


def test_bootstrap_response_rejects_exact_type_with_mutated_shape():
    interval = _bootstrap_ci()
    object.__setattr__(interval, "unexpected", "field")
    expected = ((0, _bootstrap_rule_id("1"), 2000),)

    with pytest.raises(RuntimeError, match="shape"):
        quality_intervals._validate_bootstrap_responses(
            expected,
            (_bootstrap_response(expected[0][0], expected[0][1], interval),),
            result_size=1,
        )


def test_bootstrap_response_revalidates_finite_interval_semantics():
    interval = _bootstrap_ci()
    object.__setattr__(interval, "lower", math.nan)
    expected = ((0, _bootstrap_rule_id("1"), 2000),)

    with pytest.raises(RuntimeError, match="semantics"):
        quality_intervals._validate_bootstrap_responses(
            expected,
            (_bootstrap_response(expected[0][0], expected[0][1], interval),),
            result_size=1,
        )


@pytest.mark.parametrize("valid_replicates", [2001, 2**64 - 1, True, -1])
def test_bootstrap_response_valid_replicates_are_bounded_by_submitted_task(
    valid_replicates,
):
    interval = _bootstrap_ci()
    object.__setattr__(interval, "valid_replicates", valid_replicates)
    rule_id = _bootstrap_rule_id("1")
    expected = ((0, rule_id, 2000),)

    with pytest.raises(RuntimeError, match="validReplicates"):
        quality_intervals._validate_bootstrap_responses(
            expected,
            (_bootstrap_response(0, rule_id, interval),),
            result_size=1,
        )


def test_bootstrap_response_accepts_partial_valid_replicates_within_submission():
    interval = BootstrapCi(0.75, 1.25, 1900, "NONE")
    rule_id = _bootstrap_rule_id("1")

    validated = quality_intervals._validate_bootstrap_responses(
        ((0, rule_id, 2000),),
        (_bootstrap_response(0, rule_id, interval),),
        result_size=1,
    )

    assert validated[0].interval.valid_replicates == 1900


@pytest.mark.parametrize("replicates", [True, 0, -1, 2**64])
def test_submitted_replicates_fail_preflight_before_worker_start(
    monkeypatch, replicates
):
    started = False

    def forbidden_start(*_args):
        nonlocal started
        started = True
        raise AssertionError("invalid submitted replicates reached a worker")

    monkeypatch.setattr(quality_intervals, "_start_bootstrap_worker", forbidden_start)
    task = (*_tiny_bootstrap_task()[:-1], replicates)

    with pytest.raises(RuntimeError, match="replicates"):
        quality_intervals._parallel_bootstrap_tasks([task], 1)

    assert not started


def test_worker_input_rejects_replicates_outside_uint64_range():
    task = (*_tiny_bootstrap_task()[:-1], 2**64)
    payload = pickle.dumps(("sfep-quality-bootstrap/v1", 0, (task,)))

    with pytest.raises(RuntimeError, match="replicates"):
        quality_intervals._decode_bootstrap_worker_input(payload)


@pytest.mark.parametrize(
    "payload",
    [
        b"not-a-pickle",
        pickle.dumps(("wrong-protocol", ())),
        pickle.dumps(("sfep-quality-bootstrap/v1", ()))[:-1],
        pickle.dumps(("sfep-quality-bootstrap/v1", ((0, "short"),))),
    ],
)
def test_worker_response_rejects_malformed_short_or_wrong_protocol(payload):
    with pytest.raises(RuntimeError, match="bootstrap worker protocol"):
        quality_intervals._decode_bootstrap_worker_response(payload)


class _FakeWorkerProcess:
    def __init__(
        self,
        *,
        output=b"",
        error=b"",
        returncode=0,
        resist_terminate=False,
    ):
        self.pid = 4242
        self._output = output
        self._error = error
        self._configured_returncode = returncode
        self._resist_terminate = resist_terminate
        self._complete_on_poll = not resist_terminate
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self, _deadline):
        if self.returncode is None and self._complete_on_poll:
            self.returncode = self._configured_returncode
            self.waited = True
        return self.returncode

    def terminate(self, _deadline):
        self.terminated = True
        if not self._resist_terminate:
            self.returncode = -signal.SIGTERM

    def kill(self, _deadline):
        self.killed = True
        self.returncode = -signal.SIGKILL

    def wait(self, timeout=None):
        self.waited = True
        if self.returncode is None:
            raise TimeoutError("bootstrap-worker did not exit")
        return self.returncode

    def reap(self, _deadline):
        return self.wait()


def _fake_worker_start(process):
    def start(_input_path, output_path, error_path):
        process.output_path = output_path
        process.error_path = error_path
        output_path.write_bytes(process._output)
        error_path.write_bytes(process._error)
        return process

    return start


def _tiny_bootstrap_task(index=0, digit="1"):
    rows = pd.DataFrame(
        [
            ("C1", "S", True, "불량"),
            ("C2", "S", True, "양품"),
            ("C3", "S", False, "불량"),
            ("C4", "S", False, "양품"),
        ],
        columns=("charge_id", "stratum", "candidate", "judge"),
    )
    return index, rows, CRITERIA_ID, _bootstrap_rule_id(digit), 2000


def _weighted_bootstrap_task(index: int, digit: str, unique_charges: int):
    rows = pd.DataFrame(
        [
            (f"C{number}", "S", number % 2 == 0, "불량" if number % 3 == 0 else "양품")
            for number in range(unique_charges)
        ],
        columns=("charge_id", "stratum", "candidate", "judge"),
    )
    return index, rows, CRITERIA_ID, _bootstrap_rule_id(digit), 2000


def test_worker_partitions_use_deterministic_lpt_statistical_cost_balancing():
    tasks = tuple(
        _weighted_bootstrap_task(index, digit, unique_charges)
        for index, (digit, unique_charges) in enumerate(
            zip("123456", (5, 4, 3, 2, 1, 1), strict=True)
        )
    )

    partitions = quality_intervals._partition_bootstrap_tasks(tasks, 2)

    assert tuple(tuple(task[0] for task in partition) for partition in partitions) == (
        (0, 3, 4),
        (1, 2, 5),
    )


def test_parent_keyboard_interrupt_closes_terminates_and_reaps_worker(monkeypatch):
    process = _FakeWorkerProcess(
        resist_terminate=True,
    )
    monkeypatch.setattr(
        quality_intervals, "_start_bootstrap_worker", _fake_worker_start(process)
    )
    monkeypatch.setattr(
        quality_intervals,
        "_wait_for_bootstrap_workers",
        lambda _workers, _deadline: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(
        quality_intervals, "_BOOTSTRAP_TERMINATE_TIMEOUT_SECONDS", 0.0
    )

    with pytest.raises(KeyboardInterrupt):
        quality_intervals._parallel_bootstrap_tasks([_tiny_bootstrap_task()], 1)

    assert process.terminated and process.killed and process.waited


def test_start_failure_after_partial_workers_cleans_every_started_process(monkeypatch):
    first = _FakeWorkerProcess(resist_terminate=True)
    starts = iter((first, OSError("spawn failed")))

    def start(_input_path, output_path, error_path):
        result = next(starts)
        if isinstance(result, BaseException):
            raise result
        return _fake_worker_start(result)(_input_path, output_path, error_path)

    monkeypatch.setattr(quality_intervals, "_start_bootstrap_worker", start)
    monkeypatch.setattr(
        quality_intervals, "_BOOTSTRAP_TERMINATE_TIMEOUT_SECONDS", 0.0
    )

    with pytest.raises(OSError, match="spawn failed"):
        quality_intervals._parallel_bootstrap_tasks(
            [_tiny_bootstrap_task(0, "1"), _tiny_bootstrap_task(1, "2")],
            2,
        )

    assert first.terminated and first.killed and first.waited


@pytest.mark.parametrize("returncode", [7, -signal.SIGKILL])
def test_child_nonzero_or_signal_fails_closed_and_reaps(monkeypatch, returncode):
    process = _FakeWorkerProcess(returncode=returncode, error=b"worker failed")
    monkeypatch.setattr(
        quality_intervals, "_start_bootstrap_worker", _fake_worker_start(process)
    )

    with pytest.raises(RuntimeError, match="bootstrap worker"):
        quality_intervals._parallel_bootstrap_tasks([_tiny_bootstrap_task()], 1)

    assert process.waited


def test_partial_worker_payload_fails_closed_and_reaps(monkeypatch):
    payload = pickle.dumps(("sfep-quality-bootstrap/v1", ()))[:-1]
    process = _FakeWorkerProcess(output=payload)
    monkeypatch.setattr(
        quality_intervals, "_start_bootstrap_worker", _fake_worker_start(process)
    )

    with pytest.raises(RuntimeError, match="bootstrap worker protocol"):
        quality_intervals._parallel_bootstrap_tasks([_tiny_bootstrap_task()], 1)

    assert process.waited


def test_worker_ordinal_mismatch_fails_closed_before_results_are_returned(monkeypatch):
    payload = pickle.dumps(
        (
            "sfep-quality-bootstrap/v1",
            1,
            ((0, _bootstrap_rule_id("1"), _bootstrap_ci()),),
        )
    )
    process = _FakeWorkerProcess(output=payload)
    monkeypatch.setattr(
        quality_intervals, "_start_bootstrap_worker", _fake_worker_start(process)
    )

    with pytest.raises(RuntimeError, match="worker ordinal"):
        quality_intervals._parallel_bootstrap_tasks([_tiny_bootstrap_task()], 1)

    assert process.waited


def test_worker_serialization_and_write_failures_return_nonzero_not_outer_control(
    monkeypatch, tmp_path
):
    input_path = tmp_path / "worker-input.pickle"
    input_path.write_bytes(
        pickle.dumps(
            (
                "sfep-quality-bootstrap/v1",
                0,
                (_tiny_bootstrap_task(),),
            )
        )
    )
    original_dumps = pickle.dumps

    def serialization_failure(*_args, **_kwargs):
        raise OSError("cannot serialize")

    monkeypatch.setattr(quality_intervals.pickle, "dumps", serialization_failure)
    assert quality_intervals._bootstrap_worker_main(input_path, output_fd=99) != 0

    monkeypatch.setattr(quality_intervals.pickle, "dumps", original_dumps)
    monkeypatch.setattr(
        quality_intervals,
        "_write_all",
        lambda _fd, _payload: (_ for _ in ()).throw(BrokenPipeError()),
    )
    assert quality_intervals._bootstrap_worker_main(input_path, output_fd=99) != 0


def test_worker_calls_task5_exactly_once_per_submitted_identity(monkeypatch, tmp_path):
    tasks = tuple(
        _tiny_bootstrap_task(index, digit)
        for index, digit in enumerate("123")
    )
    input_path = tmp_path / "worker-input.pickle"
    input_path.write_bytes(
        pickle.dumps(("sfep-quality-bootstrap/v1", 7, tasks))
    )
    calls = []
    outputs = []

    def observed(rows, criteria_id, rule_id, *, replicates):
        calls.append((rows.copy(deep=True), criteria_id, rule_id, replicates))
        return _bootstrap_ci()

    monkeypatch.setattr(quality_intervals, "charge_bootstrap_rr_ci", observed)
    monkeypatch.setattr(
        quality_intervals,
        "_write_all",
        lambda output_fd, payload: outputs.append((output_fd, payload)),
    )

    assert quality_intervals._bootstrap_worker_main(input_path, output_fd=99) == 0
    ordinal, responses = quality_intervals._decode_bootstrap_worker_response(
        outputs[0][1]
    )

    assert ordinal == 7
    assert outputs[0][0] == 99
    assert [(response.index, response.rule_id) for response in responses] == [
        (task[0], task[3]) for task in tasks
    ]
    assert [(call[1], call[2], call[3]) for call in calls] == [
        (CRITERIA_ID, task[3], 2000) for task in tasks
    ]
    assert len(calls) == len({call[2] for call in calls}) == 3


def test_worker_rejects_malformed_input_without_entering_outer_caller(tmp_path):
    input_path = tmp_path / "malformed-worker-input.pickle"
    input_path.write_bytes(b"truncated-pickle")

    assert quality_intervals._bootstrap_worker_main(input_path, output_fd=99) != 0


def test_spawn_exec_bootstrap_is_warning_free_with_an_existing_background_thread():
    ready = threading.Event()
    release = threading.Event()

    def background():
        ready.set()
        while not release.wait(timeout=0.001):
            transient_read, transient_write = os.pipe()
            os.close(transient_read)
            os.close(transient_write)

    thread = threading.Thread(target=background)
    thread.start()
    assert ready.wait(timeout=5)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            responses = quality_intervals._parallel_bootstrap_tasks(
                [_tiny_bootstrap_task(0, "1"), _tiny_bootstrap_task(1, "2")],
                2,
            )
    finally:
        release.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert [(item.index, item.rule_id) for item in responses] == [
        (0, _bootstrap_rule_id("1")),
        (1, _bootstrap_rule_id("2")),
    ]


def test_background_thread_workers_use_direct_posix_spawn_not_fork(
    monkeypatch,
):
    original_spawn = os.posix_spawn
    spawn_calls = []

    def observed_spawn(*args, **kwargs):
        spawn_calls.append((args, kwargs))
        return original_spawn(*args, **kwargs)

    def forbidden_fork():
        raise AssertionError("bootstrap workers must never call fork")

    monkeypatch.setattr(os, "posix_spawn", observed_spawn)
    monkeypatch.setattr(os, "fork", forbidden_fork)
    responses = quality_intervals._parallel_bootstrap_tasks(
        [_tiny_bootstrap_task()],
        1,
    )

    assert len(responses) == 1
    assert len(spawn_calls) == 1


def test_direct_posix_spawn_maps_worker_standard_streams_with_file_actions(
    monkeypatch, tmp_path
):
    observed = {}
    read_fd, write_fd = os.pipe()
    os.set_inheritable(write_fd, True)

    def fake_posix_spawn(executable, arguments, environment, **kwargs):
        observed.update(
            executable=executable,
            arguments=arguments,
            environment=environment,
            kwargs=kwargs,
        )
        return 4242

    monkeypatch.setattr(os, "posix_spawn", fake_posix_spawn)
    try:
        worker = quality_intervals._start_bootstrap_worker(
            tmp_path / "input.pickle",
            tmp_path / "response.pickle",
            tmp_path / "stderr.txt",
        )
        assert os.get_inheritable(write_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert worker.pid == 4242
    assert observed["arguments"][1:4] == (
        "-m",
        "equipment_quality._bootstrap_worker",
        "--bootstrap-worker",
    )
    actions = observed["kwargs"]["file_actions"]
    action_kinds_and_fds = [(action[0], action[1]) for action in actions]
    assert action_kinds_and_fds == [
        (os.POSIX_SPAWN_OPEN, 0),
        (os.POSIX_SPAWN_OPEN, 1),
        (os.POSIX_SPAWN_OPEN, 2),
    ]


def _child_pythonpath(injection_directory: Path) -> str:
    existing = os.environ.get("PYTHONPATH")
    return str(injection_directory) + (
        "" if not existing else os.pathsep + existing
    )


def _wait_for_spawned_process(pid: int, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waited_pid, status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.005)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    raise AssertionError("bootstrap worker entry did not exit promptly")


def test_real_worker_entry_fails_before_heavy_import_when_fd_directories_unavailable(
    tmp_path,
):
    marker_path = tmp_path / "entry-marker.json"
    sitecustomize = tmp_path / "sitecustomize.py"
    sitecustomize.write_text(
        """
import atexit
import json
import os
from pathlib import Path
import resource
import sys

marker_path = Path(os.environ['SFEP_FD_MARKER'])
attempted = []
high_fd_open_on_attempt = []
listdir_calls = []
real_listdir = os.listdir

class ImportMarker:
    def find_spec(self, fullname, _path=None, _target=None):
        if fullname in {'equipment_quality.quality_intervals', 'pandas'}:
            attempted.append(fullname)
            try:
                os.fstat(100)
                high_fd_open_on_attempt.append(True)
            except OSError:
                high_fd_open_on_attempt.append(False)
        return None

def unavailable(path):
    if path in {'/dev/fd', '/proc/self/fd'}:
        listdir_calls.append(path)
        raise OSError('injected descriptor-directory failure')
    return real_listdir(path)

def write_marker():
    marker_path.write_text(
        json.dumps(
            {
                'attempted': attempted,
                'highFdOpenOnAttempt': high_fd_open_on_attempt,
                'listdirCalls': listdir_calls,
                'loaded': [
                    name for name in ('equipment_quality.quality_intervals', 'pandas')
                    if name in sys.modules
                ],
                'softLimit': resource.getrlimit(resource.RLIMIT_NOFILE)[0],
                'stdio': [fd for fd in (0, 1, 2) if _fd_open(fd)],
            },
            sort_keys=True,
        ),
        encoding='utf-8',
    )

def _fd_open(fd):
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False

os.listdir = unavailable
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (10, hard))
sys.meta_path.insert(0, ImportMarker())
atexit.register(write_marker)
""",
        encoding="utf-8",
    )
    read_fd, write_fd = os.pipe()
    saved_fd_100 = None
    saved_fd_100_inheritable = None
    try:
        try:
            saved_fd_100_inheritable = os.get_inheritable(100)
            saved_fd_100 = os.dup(100)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
        os.dup2(write_fd, 100, inheritable=True)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = _child_pythonpath(tmp_path)
        environment["SFEP_FD_MARKER"] = str(marker_path)
        output_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        started = time.monotonic()
        pid = os.posix_spawn(
            sys.executable,
            (
                sys.executable,
                "-m",
                "equipment_quality._bootstrap_worker",
                "--bootstrap-worker",
                str(tmp_path / "missing-input.pickle"),
            ),
            environment,
            file_actions=(
                (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
                (
                    os.POSIX_SPAWN_OPEN,
                    1,
                    str(tmp_path / "stdout"),
                    output_flags,
                    0o600,
                ),
                (
                    os.POSIX_SPAWN_OPEN,
                    2,
                    str(tmp_path / "stderr"),
                    output_flags,
                    0o600,
                ),
            ),
            setsigmask=(),
        )
    finally:
        if saved_fd_100 is None:
            try:
                os.close(100)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise
        else:
            os.dup2(
                saved_fd_100,
                100,
                inheritable=saved_fd_100_inheritable,
            )
            os.close(saved_fd_100)
        os.close(write_fd)

    try:
        returncode = _wait_for_spawned_process(pid, 2.0)
        readable, _, _ = select.select((read_fd,), (), (), 0.1)
        assert readable and os.read(read_fd, 1) == b""
    finally:
        os.close(read_fd)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))

    assert returncode != 0
    assert time.monotonic() - started < 2.0
    assert marker == {
        "attempted": [],
        "highFdOpenOnAttempt": [],
        "listdirCalls": ["/dev/fd", "/proc/self/fd"],
        "loaded": [],
        "softLimit": 10,
        "stdio": [0, 1, 2],
    }


def test_dev_fd_failure_uses_proc_snapshot_and_runs_real_worker_entry(
    monkeypatch,
    tmp_path,
):
    marker_path = tmp_path / "proc-marker.json"
    (tmp_path / "sitecustomize.py").write_text(
        """
import atexit
import json
import os
from pathlib import Path
import sys

marker_path = Path(os.environ['SFEP_FD_MARKER'])
calls = []
real_listdir = os.listdir
snapshot = real_listdir('/dev/fd')

def injected_listdir(path):
    if path == '/dev/fd':
        calls.append(path)
        raise OSError('injected /dev/fd failure')
    if path == '/proc/self/fd':
        calls.append(path)
        return snapshot
    return real_listdir(path)

def write_marker():
    marker_path.write_text(
        json.dumps(
            {
                'calls': calls,
                'heavyLoaded': all(
                    name in sys.modules
                    for name in ('equipment_quality.quality_intervals', 'pandas')
                ),
                'stdio': [fd for fd in (0, 1, 2) if _fd_open(fd)],
            },
            sort_keys=True,
        ),
        encoding='utf-8',
    )

def _fd_open(fd):
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False

os.listdir = injected_listdir
atexit.register(write_marker)
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", _child_pythonpath(tmp_path))
    monkeypatch.setenv("SFEP_FD_MARKER", str(marker_path))

    responses = quality_intervals._parallel_bootstrap_tasks(
        [_tiny_bootstrap_task()],
        1,
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))

    assert len(responses) == 1
    assert responses[0].interval.valid_replicates == 2000
    assert marker == {
        "calls": ["/dev/fd", "/proc/self/fd"],
        "heavyLoaded": True,
        "stdio": [0, 1, 2],
    }


def test_real_worker_does_not_retain_unrelated_inheritable_fd(
    tmp_path,
):
    ready = threading.Event()
    release = threading.Event()

    def background():
        ready.set()
        release.wait(timeout=30)

    thread = threading.Thread(target=background)
    thread.start()
    assert ready.wait(timeout=5)
    read_fd, write_fd = os.pipe()
    assert not os.get_inheritable(write_fd)
    os.set_inheritable(write_fd, True)
    long_task = (*_tiny_bootstrap_task()[:-1], 500_000)
    input_path = quality_intervals._write_bootstrap_worker_inputs(
        tmp_path,
        ((long_task,),),
    )[0]
    worker = None
    try:
        worker = quality_intervals._start_bootstrap_worker(
            input_path,
            tmp_path / "response.pickle",
            tmp_path / "stderr.txt",
        )
        assert os.get_inheritable(write_fd)
        os.close(write_fd)
        write_fd = -1

        readable, _, _ = select.select((read_fd,), (), (), 2.0)
        assert readable, "worker retained the unrelated inheritable pipe writer"
        assert os.read(read_fd, 1) == b""
        os.kill(worker.pid, 0)
    finally:
        if worker is not None:
            quality_intervals._cleanup_bootstrap_workers((worker,))
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)
        release.set()
        thread.join(timeout=5)

    assert not thread.is_alive()


def test_waitpid_repeated_eintr_obeys_absolute_deadline_without_spin(
    monkeypatch,
):
    release = threading.Event()
    calls = 0
    errors = []

    def interrupted_waitpid(pid, _options):
        nonlocal calls
        calls += 1
        if not release.is_set():
            raise InterruptedError(errno.EINTR, "interrupted")
        return pid, 0

    monkeypatch.setattr(os, "waitpid", interrupted_waitpid)
    worker = quality_intervals._SpawnedBootstrapWorker(
        4242,
        Path("response"),
        Path("stderr"),
    )

    def wait():
        try:
            quality_intervals._wait_for_bootstrap_workers(
                (worker,), time.monotonic() + 0.01
            )
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=wait)
    thread.start()
    thread.join(timeout=0.1)
    try:
        assert not thread.is_alive(), "EINTR retry ignored the absolute deadline"
    finally:
        release.set()
        thread.join(timeout=1)

    assert len(errors) == 1
    assert isinstance(errors[0], TimeoutError)
    assert calls < 100


def test_reap_repeated_eintr_obeys_absolute_deadline_without_spin(monkeypatch):
    calls = 0

    def interrupted_waitpid(_pid, _options):
        nonlocal calls
        calls += 1
        raise InterruptedError(errno.EINTR, "interrupted")

    monkeypatch.setattr(os, "waitpid", interrupted_waitpid)
    worker = quality_intervals._SpawnedBootstrapWorker(
        4242,
        Path("response"),
        Path("stderr"),
    )
    started = time.monotonic()

    with pytest.raises(TimeoutError, match="reap"):
        worker.reap(started + 0.01)

    assert time.monotonic() - started < 0.1
    assert calls < 100


def test_signal_repeated_eintr_obeys_absolute_deadline_without_spin(monkeypatch):
    calls = 0

    def interrupted_kill(_pid, _signal):
        nonlocal calls
        calls += 1
        raise InterruptedError(errno.EINTR, "interrupted")

    monkeypatch.setattr(os, "kill", interrupted_kill)
    worker = quality_intervals._SpawnedBootstrapWorker(
        4242,
        Path("response"),
        Path("stderr"),
    )
    started = time.monotonic()

    with pytest.raises(TimeoutError, match="TERM"):
        worker.terminate(started + 0.01)

    assert time.monotonic() - started < 0.1
    assert calls < 100


def test_cleanup_failure_does_not_replace_parent_keyboard_interrupt(monkeypatch):
    process = _FakeWorkerProcess(resist_terminate=True)

    def failed_reap(_deadline):
        raise TimeoutError("unreaped child")

    process.reap = failed_reap
    monkeypatch.setattr(
        quality_intervals, "_start_bootstrap_worker", _fake_worker_start(process)
    )
    monkeypatch.setattr(
        quality_intervals,
        "_wait_for_bootstrap_workers",
        lambda _workers, _deadline: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(
        quality_intervals, "_BOOTSTRAP_TERMINATE_TIMEOUT_SECONDS", 0.0
    )
    monkeypatch.setattr(
        quality_intervals, "_BOOTSTRAP_KILL_REAP_TIMEOUT_SECONDS", 0.0
    )

    with pytest.raises(KeyboardInterrupt) as captured:
        quality_intervals._parallel_bootstrap_tasks([_tiny_bootstrap_task()], 1)

    assert any("cleanup also failed" in note for note in captured.value.__notes__)


def test_cleanup_reaps_other_children_when_one_pid_keeps_timing_out(monkeypatch):
    first = _FakeWorkerProcess(resist_terminate=True)
    second = _FakeWorkerProcess(resist_terminate=True)
    first.pid = 4241
    second.pid = 4242
    second_reaped = False

    def first_reap(deadline):
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        raise TimeoutError("injected EINTR deadline")

    def second_reap(_deadline):
        nonlocal second_reaped
        second_reaped = True
        return -signal.SIGKILL

    first.reap = first_reap
    second.reap = second_reap
    monkeypatch.setattr(
        quality_intervals, "_BOOTSTRAP_TERMINATE_TIMEOUT_SECONDS", 0.0
    )
    monkeypatch.setattr(
        quality_intervals, "_BOOTSTRAP_KILL_REAP_TIMEOUT_SECONDS", 0.01
    )

    with pytest.raises(RuntimeError, match="4241.*reap"):
        quality_intervals._cleanup_bootstrap_workers((first, second))

    assert first.killed and second.killed and second_reaped


def test_cleanup_treats_esrch_then_echild_as_an_already_reaped_race(monkeypatch):
    calls = 0

    def raced_waitpid(_pid, _options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return 0, 0
        raise ChildProcessError(errno.ECHILD, "no child")

    monkeypatch.setattr(os, "waitpid", raced_waitpid)
    monkeypatch.setattr(
        os,
        "kill",
        lambda _pid, _signal: (_ for _ in ()).throw(
            ProcessLookupError(errno.ESRCH, "gone")
        ),
    )
    worker = quality_intervals._SpawnedBootstrapWorker(
        4242,
        Path("response"),
        Path("stderr"),
    )

    quality_intervals._cleanup_bootstrap_workers((worker,))

    assert worker.returncode == 255


def test_real_term_resistant_child_is_killed_and_reaped(monkeypatch, tmp_path):
    ready_path = tmp_path / "ready"
    program = (
        "import pathlib,signal,sys,time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "pathlib.Path(sys.argv[1]).write_text('ready');"
        "time.sleep(30)"
    )
    pid = os.posix_spawn(
        sys.executable,
        (sys.executable, "-c", program, str(ready_path)),
        dict(os.environ),
    )
    worker = quality_intervals._SpawnedBootstrapWorker(
        pid,
        tmp_path / "response",
        tmp_path / "stderr",
    )
    deadline = time.monotonic() + 5
    while not ready_path.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert ready_path.exists()
    monkeypatch.setattr(
        quality_intervals, "_BOOTSTRAP_TERMINATE_TIMEOUT_SECONDS", 0.01
    )

    quality_intervals._cleanup_bootstrap_workers((worker,))

    assert worker.returncode == -signal.SIGKILL
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


def test_actual_process_response_cannot_exceed_submitted_replicates(
    monkeypatch,
):
    interval = _bootstrap_ci()
    object.__setattr__(interval, "valid_replicates", 2001)
    response_payload = pickle.dumps(
        (
            "sfep-quality-bootstrap/v1",
            0,
            ((0, _bootstrap_rule_id("1"), interval),),
        )
    )

    def malformed_process(_input_path, output_path, error_path):
        program = (
            "import pathlib,sys;"
            "pathlib.Path(sys.argv[1]).write_bytes(bytes.fromhex(sys.argv[3]));"
            "pathlib.Path(sys.argv[2]).write_bytes(b'')"
        )
        pid = os.posix_spawn(
            sys.executable,
            (
                sys.executable,
                "-c",
                program,
                str(output_path),
                str(error_path),
                response_payload.hex(),
            ),
            dict(os.environ),
        )
        return quality_intervals._SpawnedBootstrapWorker(
            pid,
            output_path,
            error_path,
        )

    monkeypatch.setattr(
        quality_intervals, "_start_bootstrap_worker", malformed_process
    )

    with pytest.raises(RuntimeError, match="validReplicates"):
        quality_intervals._parallel_bootstrap_tasks([_tiny_bootstrap_task()], 1)


def test_concurrent_parent_calls_are_deterministic_and_do_not_leak_fds():
    before = len(os.listdir("/dev/fd"))

    def run(_ordinal):
        return quality_intervals._parallel_bootstrap_tasks(
            [_tiny_bootstrap_task()],
            1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(run, range(2)))

    assert results[0] == results[1]
    assert len(os.listdir("/dev/fd")) == before


def test_expected_identity_duplicates_fail_before_any_worker_starts(monkeypatch):
    started = False

    def forbidden_start(*_args):
        nonlocal started
        started = True
        raise AssertionError("invalid expected identities reached a worker")

    monkeypatch.setattr(quality_intervals, "_start_bootstrap_worker", forbidden_start)
    tasks = [_tiny_bootstrap_task(0, "1"), _tiny_bootstrap_task(0, "2")]

    with pytest.raises(RuntimeError, match="duplicate index"):
        quality_intervals._parallel_bootstrap_tasks(tasks, 2, result_size=2)

    assert not started


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


def _direct_quality_rule(display_merge_rule_ids=(), *, grade="NORMAL", confirmation=None):
    config = analysis_config()
    split = null_fixture()
    candidate = next(
        item
        for item in generate_candidates(
            split.discovery_rows, definitions(config), config
        )
        if item.field_names == ("slab_grind",)
        and item.predicate[0].values == ("RISK",)
    )
    discovery = _literal_metric(
        relative_risk=1.0, risk_difference=0.0, q_value=1.0
    )
    confirmation_metric = confirmation or replace(
        discovery,
        p_value=None,
        q_value=None,
    )
    return quality_intervals.QualityRule(
        candidate,
        discovery,
        confirmation_metric,
        grade,
        display_merge_rule_ids,
    )


def test_quality_rule_snapshots_sorts_and_detaches_display_merge_id_alias():
    first = "sha256:" + "a" * 64
    second = "sha256:" + "b" * 64
    external = [second, first]

    rule = _direct_quality_rule(external)
    before = canonical_json_bytes(rule.to_wire())
    external.append("sha256:" + "c" * 64)

    assert rule.display_merge_rule_ids == (first, second)
    assert canonical_json_bytes(rule.to_wire()) == before
    replaced = replace(
        rule,
        display_merge_rule_ids=["sha256:" + "d" * 64, first],
    )
    assert replaced.display_merge_rule_ids == (first, "sha256:" + "d" * 64)


@pytest.mark.parametrize(
    "invalid_id",
    [
        None,
        "sha256:" + "a" * 63,
        "sha256:" + "A" * 64,
        "sha256:" + "g" * 64,
        "a" * 64,
    ],
)
def test_quality_rule_rejects_noncanonical_display_merge_ids(invalid_id):
    with pytest.raises((TypeError, ValueError), match="display_merge_rule_ids"):
        _direct_quality_rule([invalid_id])


def test_quality_rule_rejects_duplicate_display_merge_ids():
    duplicate = "sha256:" + "a" * 64
    with pytest.raises(ValueError, match="unique"):
        _direct_quality_rule([duplicate, duplicate])


def test_quality_rule_validates_grade_and_confirmation_only_fields():
    with pytest.raises(ValueError, match="grade"):
        _direct_quality_rule(grade="SEVERE")
    invalid_confirmation = replace(
        _literal_metric(),
        relative_risk_ci_lower=0.8,
        relative_risk_ci_upper=2.2,
        p_value=0.01,
        q_value=0.05,
    )
    with pytest.raises(ValueError, match="confirmation"):
        _direct_quality_rule(confirmation=invalid_confirmation)


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


def test_real_bootstrap_workload_is_deterministic_for_1_8_and_10_workers():
    digits = "123456789abcdef0"
    tasks = [
        _tiny_bootstrap_task(index, digit)
        for index, digit in enumerate(digits)
    ]
    started = time.perf_counter()
    sequential = quality_intervals._parallel_bootstrap_tasks(tasks, 1)
    eight_workers = quality_intervals._parallel_bootstrap_tasks(tasks, 8)
    ten_workers = quality_intervals._parallel_bootstrap_tasks(tasks, 10)
    elapsed = time.perf_counter() - started

    assert sequential == eight_workers == ten_workers
    assert [(response.index, response.rule_id) for response in sequential] == [
        (index, _bootstrap_rule_id(digit))
        for index, digit in enumerate(digits)
    ]
    assert all(response.interval.valid_replicates == 2000 for response in sequential)
    assert elapsed < 30.0


def test_parallel_bootstrap_handles_zero_one_and_multiple_tasks():
    assert quality_intervals._parallel_bootstrap_tasks([], 10) == ()
    one = quality_intervals._parallel_bootstrap_tasks([_tiny_bootstrap_task()], 10)
    many = quality_intervals._parallel_bootstrap_tasks(
        [_tiny_bootstrap_task(0, "1"), _tiny_bootstrap_task(1, "2")],
        2,
    )

    assert [(response.index, response.rule_id) for response in one] == [
        (0, _bootstrap_rule_id("1"))
    ]
    assert [(response.index, response.rule_id) for response in many] == [
        (0, _bootstrap_rule_id("1")),
        (1, _bootstrap_rule_id("2")),
    ]


def _risk_record_and_sidecar(result):
    for record, sidecar in zip(result.records, result.sidecars, strict=True):
        if (
            record["fieldNames"] == ("slab_grind",)
            and record["predicate"]["allOf"][0]["values"] == ("RISK",)
        ):
            return record, sidecar
    raise AssertionError("missing RISK slab_grind rule")


def test_legacy_quality_payload_is_byte_pinned_before_rich_result_refactor():
    config = analysis_config()
    records = build_quality_rules(
        strong_repeated_fixture(), definitions(config), config, CRITERIA_ID
    )
    payload = canonical_json_bytes(records)

    assert len(payload) == 9_590
    assert hashlib.sha256(payload).hexdigest() == (
        "6f6463806267a357c215847db192e340e94bf08be0a1d54113e08c3f14f315e5"
    )


def test_rich_quality_sidecars_capture_exact_fixed_discovery_and_confirmation_inputs():
    config = analysis_config()
    split, catalog = identified_split_and_catalog(strong_repeated_fixture())
    result = quality_intervals.build_quality_rules_result(
        split,
        definitions(config),
        config,
        CRITERIA_ID,
        material_catalog=catalog,
    )
    record, sidecar = _risk_record_and_sidecar(result)
    key_by_charge = {material.charge_id: material.material_key for material in catalog}
    discovery_expected = tuple(
        sorted(
            (
                key_by_charge[charge_id]
                for charge_id in split.discovery_rows.loc[
                    split.discovery_rows["slab_grind"] == "RISK", "charge_id"
                ]
            ),
            key=lambda value: value.encode("utf-8"),
        )
    )
    confirmation_expected = tuple(
        sorted(
            (
                key_by_charge[charge_id]
                for charge_id in split.confirmation_rows.loc[
                    split.confirmation_rows["slab_grind"] == "RISK", "charge_id"
                ]
            ),
            key=lambda value: value.encode("utf-8"),
        )
    )
    strata = (
        '{"sm_plant":"P1","steel_grade":"G1","steel_usage":"U"}',
        '{"sm_plant":"P2","steel_grade":"G2","steel_usage":"U"}',
    )

    assert sidecar.rule_id == record["ruleId"]
    assert sidecar.discovery.adjustment_fields == (
        "sm_plant",
        "steel_grade",
        "steel_usage",
    )
    assert dict(sidecar.discovery.discovery_band_boundaries) == {}
    assert sidecar.discovery.informative_stratum_keys == strata
    assert dict(sidecar.discovery.discovery_weights) == {
        strata[0]: 0.5,
        strata[1]: 0.5,
    }
    assert sidecar.discovery.candidate_material_keys == discovery_expected
    assert dict(sidecar.discovery.comparator_counts_by_stratum) == {
        strata[0]: 100,
        strata[1]: 100,
    }
    assert sidecar.confirmation.adjustment_fields == sidecar.discovery.adjustment_fields
    assert sidecar.confirmation.discovery_band_boundaries == sidecar.discovery.discovery_band_boundaries
    assert sidecar.confirmation.discovery_weights == sidecar.discovery.discovery_weights
    assert sidecar.confirmation.informative_stratum_keys == strata
    assert sidecar.confirmation.candidate_material_keys == confirmation_expected
    assert dict(sidecar.confirmation.comparator_counts_by_stratum) == {
        strata[0]: 50,
        strata[1]: 50,
    }
    assert record["discovery"]["support"] == len(discovery_expected) == 200
    assert record["confirmation"]["support"] == len(confirmation_expected) == 100


def test_confirmation_only_new_strata_are_excluded_from_fixed_sidecar_inputs():
    config = analysis_config()
    base = strong_repeated_fixture()
    extra = pd.DataFrame(
        [
            {
                "charge_id": f"NEW-{level}-{index:03d}",
                "sm_plant": "P3",
                "steel_grade": "G3",
                "steel_usage": "U",
                "slab_grind": level,
                "judge": "불량" if index < 5 else "양품",
            }
            for level in ("RISK", "BASE")
            for index in range(50)
        ]
    )
    expanded = replace(
        base,
        confirmation_rows=pd.concat(
            [base.confirmation_rows, extra], ignore_index=True
        ),
    )
    split, catalog = identified_split_and_catalog(expanded)
    result = quality_intervals.build_quality_rules_result(
        split,
        definitions(config),
        config,
        CRITERIA_ID,
        material_catalog=catalog,
    )
    _, sidecar = _risk_record_and_sidecar(result)

    assert all('"P3"' not in key for key in sidecar.confirmation.informative_stratum_keys)
    assert all(
        not material.charge_id.startswith("NEW-")
        for material in catalog
        if material.material_key in sidecar.confirmation.candidate_material_keys
    )
    assert sidecar.confirmation.adjustment_fields == sidecar.discovery.adjustment_fields
    assert sidecar.confirmation.discovery_band_boundaries == sidecar.discovery.discovery_band_boundaries
    assert sidecar.confirmation.discovery_weights == sidecar.discovery.discovery_weights


def test_no_informative_quality_rule_has_no_candidate_membership_despite_raw_matches():
    config = analysis_config()
    discovery = pd.DataFrame(
        {
            "charge_id": [f"NI-D-{index:03d}" for index in range(200)],
            "slab_grind": ["RISK"] * 200,
            "judge": ["양품"] * 200,
        }
    )
    confirmation = pd.DataFrame(
        {
            "charge_id": [f"NI-C-{index:03d}" for index in range(100)],
            "slab_grind": ["RISK"] * 100,
            "judge": ["양품"] * 100,
        }
    )
    split, catalog = identified_split_and_catalog(
        split_from_rows(discovery, confirmation)
    )
    result = quality_intervals.build_quality_rules_result(
        split,
        definitions(config),
        config,
        CRITERIA_ID,
        material_catalog=catalog,
    )
    record, sidecar = _risk_record_and_sidecar(result)

    assert record["discovery"]["support"] == 0
    assert record["confirmation"]["support"] == 0
    assert sidecar.discovery.candidate_material_keys == ()
    assert sidecar.confirmation.candidate_material_keys == ()
    assert sidecar.discovery.informative_stratum_keys == ()
    assert sidecar.confirmation.informative_stratum_keys == ()


def test_rich_quality_wire_is_legacy_exact_fresh_immutable_and_order_independent():
    config = analysis_config()
    split, catalog = identified_split_and_catalog(strong_repeated_fixture())
    legacy = build_quality_rules(split, definitions(config), config, CRITERIA_ID)
    result = quality_intervals.build_quality_rules_result(
        split,
        definitions(config),
        config,
        CRITERIA_ID,
        material_catalog=catalog,
    )
    first = result.to_wire()
    first[0]["predicate"]["allOf"][0]["values"].append("CALLER")
    second = result.to_wire()
    reversed_result = quality_intervals.build_quality_rules_result(
        replace(
            split,
            discovery_rows=split.discovery_rows.iloc[::-1].reset_index(drop=True),
            confirmation_rows=split.confirmation_rows.iloc[::-1].reset_index(drop=True),
        ),
        tuple(reversed(definitions(config))),
        config,
        CRITERIA_ID,
        material_catalog=tuple(reversed(catalog)),
    )

    assert canonical_json_bytes(second) == canonical_json_bytes(legacy)
    assert canonical_json_bytes(second) == canonical_json_bytes(reversed_result.to_wire())
    assert result.sidecars == reversed_result.sidecars
    assert type(second) is list and type(second[0]) is dict
    assert "CALLER" not in second[0]["predicate"]["allOf"][0]["values"]
    with pytest.raises(TypeError):
        result.records[0]["discovery"]["support"] = 0
    assert "material_key" not in split.discovery_rows.columns
    assert "material_key" not in split.confirmation_rows.columns


def _valid_split_sidecar(*, confirmation: bool = False):
    model = equipment_models.QualitySplitSidecar
    informative = ("A",) if confirmation else ("A", "B")
    candidates = (
        ("sha256:" + "3" * 64,)
        if confirmation
        else ("sha256:" + "1" * 64, "sha256:" + "2" * 64)
    )
    return model(
        ("steel_grade", "slab_width_band"),
        {"slab_width_band": (1.0, 2.0, 3.0)},
        informative,
        {"A": 0.4, "B": 0.6},
        candidates,
        {key: 5 for key in informative},
    )


def test_quality_sidecar_models_reject_every_statistical_shape_violation():
    model = equipment_models.QualitySplitSidecar
    key = "sha256:" + "1" * 64

    class DuplicateWeightItems(dict):
        def items(self):
            return [("A", 0.5), ("A", 0.5)]

    invalid_arguments = [
        (("steel_grade", "steel_grade"), {}, ("A",), {"A": 1.0}, (key,), {"A": 1}),
        (("steel_grade",), {"slab_width_band": (1.0, 2.0, 3.0)}, ("A",), {"A": 1.0}, (key,), {"A": 1}),
        (("slab_width_band",), {"slab_width_band": (1.0, math.nan, 3.0)}, ("A",), {"A": 1.0}, (key,), {"A": 1}),
        (("slab_width_band",), {"slab_width_band": (2.0, 1.0, 3.0)}, ("A",), {"A": 1.0}, (key,), {"A": 1}),
        ((), {}, ("A", "A"), {"A": 1.0}, (key,), {"A": 1}),
        ((), {}, ("B", "A"), {"A": 0.5, "B": 0.5}, (key,), {"A": 1, "B": 1}),
        ((), {}, ("A",), {"A": 1.0}, (key,), {"A": 0}),
        ((), {}, ("A",), {"A": 1.0}, (key,), {"A": True}),
        ((), {}, ("A",), {"A": math.inf}, (key,), {"A": 1}),
        ((), {}, ("A",), {"A": 0.0}, (key,), {"A": 1}),
        ((), {}, ("A", "B"), {"A": 0.4, "B": 0.5}, (key,), {"A": 1, "B": 1}),
        ((), {}, ("A",), DuplicateWeightItems(), (key,), {"A": 1}),
        ((), {}, ("A",), {"A": 1.0}, (key, key), {"A": 1}),
    ]
    for arguments in invalid_arguments:
        with pytest.raises((TypeError, ValueError)):
            model(*arguments)


def test_quality_rule_sidecar_rejects_discovery_confirmation_drift():
    rule_model = equipment_models.QualityRuleSidecar
    discovery = _valid_split_sidecar()
    confirmation = _valid_split_sidecar(confirmation=True)
    rule_id = "sha256:" + "a" * 64
    rule_model(rule_id, discovery, confirmation)

    invalid_confirmations = [
        replace(
            confirmation,
            adjustment_fields=("steel_grade",),
            discovery_band_boundaries={},
        ),
        replace(
            confirmation,
            discovery_band_boundaries={"slab_width_band": (1.0, 2.0, 4.0)},
        ),
        replace(confirmation, discovery_weights={"A": 0.5, "B": 0.5}),
        replace(
            confirmation,
            informative_stratum_keys=("C",),
            comparator_counts_by_stratum={"C": 1},
        ),
    ]
    for invalid in invalid_confirmations:
        with pytest.raises(ValueError):
            rule_model(rule_id, discovery, invalid)
    mismatched_discovery = replace(
        discovery,
        informative_stratum_keys=("A",),
        comparator_counts_by_stratum={"A": 1},
    )
    with pytest.raises(ValueError, match="weight"):
        rule_model(rule_id, mismatched_discovery, confirmation)


def test_quality_result_rejects_misaligned_rule_id_and_support_cardinality():
    config = analysis_config()
    split, catalog = identified_split_and_catalog(strong_repeated_fixture())
    result = quality_intervals.build_quality_rules_result(
        split,
        definitions(config),
        config,
        CRITERIA_ID,
        material_catalog=catalog,
    )
    result_type = equipment_models.QualityRulesResult
    first = result.sidecars[0]

    with pytest.raises(ValueError, match="same order"):
        result_type(
            result.records,
            (replace(first, rule_id="sha256:" + "f" * 64), *result.sidecars[1:]),
        )
    with pytest.raises(ValueError, match="support"):
        result_type(
            result.records,
            (
                replace(
                    first,
                    discovery=replace(
                        first.discovery,
                        candidate_material_keys=(
                            *first.discovery.candidate_material_keys,
                            "sha256:" + "f" * 64,
                        ),
                    ),
                ),
                *result.sidecars[1:],
            ),
        )


def test_quality_result_models_reject_malformed_and_duplicate_rule_ids():
    config = analysis_config()
    split, catalog = identified_split_and_catalog(strong_repeated_fixture())
    result = quality_intervals.build_quality_rules_result(
        split,
        definitions(config),
        config,
        CRITERIA_ID,
        material_catalog=catalog,
    )
    result_type = equipment_models.QualityRulesResult

    with pytest.raises(ValueError, match="sha256"):
        equipment_models.QualityRuleSidecar(
            "invalid", result.sidecars[0].discovery, result.sidecars[0].confirmation
        )
    malformed_record = result.to_wire()[0]
    malformed_record["ruleId"] = "invalid"
    with pytest.raises(ValueError, match="sha256"):
        result_type((malformed_record,), (result.sidecars[0],))
    with pytest.raises(ValueError, match="unique"):
        result_type(
            (result.records[0], result.records[0]),
            (result.sidecars[0], result.sidecars[0]),
        )


def test_material_lineage_snapshots_records_and_rejects_malformed_identities():
    record = equipment_models.SourceRecordRef("ap", "fixture.csv", 2)
    external = [record]
    lineage = equipment_models.MaterialLineage(
        "sha256:" + "1" * 64,
        "C1",
        "1",
        "H1",
        external,
    )
    external.append(equipment_models.SourceRecordRef("ap", "fixture.csv", 3))
    assert lineage.source_records == (record,)

    invalid = [
        ("not-a-sha", "C1", "1", "H1"),
        ("sha256:" + "1" * 64, "", "1", "H1"),
        ("sha256:" + "1" * 64, "C1", "", "H1"),
        ("sha256:" + "1" * 64, "C1", "1", ""),
    ]
    for material_key, charge_id, slab_no, hr_coil_id in invalid:
        with pytest.raises((TypeError, ValueError)):
            equipment_models.MaterialLineage(
                material_key,
                charge_id,
                slab_no,
                hr_coil_id,
                (record,),
            )


def test_quality_rich_builder_rejects_catalog_disagreement_duplicates_and_overlap():
    config = analysis_config()
    split, catalog = identified_split_and_catalog(strong_repeated_fixture())

    disagreement = split.discovery_rows.copy(deep=True)
    disagreement.loc[0, "hr_coil_id"] = split.discovery_rows.loc[1, "hr_coil_id"]
    with pytest.raises(ValueError, match="disagreement"):
        quality_intervals.build_quality_rules_result(
            replace(split, discovery_rows=disagreement),
            definitions(config),
            config,
            CRITERIA_ID,
            material_catalog=catalog,
        )

    duplicate = pd.concat(
        [split.discovery_rows, split.discovery_rows.iloc[[0]]], ignore_index=True
    )
    with pytest.raises(ValueError, match="duplicate"):
        quality_intervals.build_quality_rules_result(
            replace(split, discovery_rows=duplicate),
            definitions(config),
            config,
            CRITERIA_ID,
            material_catalog=catalog,
        )

    overlap = split.confirmation_rows.copy(deep=True)
    for column in ("charge_id", "slab_no", "hr_coil_id"):
        overlap.loc[0, column] = split.discovery_rows.loc[0, column]
    with pytest.raises(ValueError, match="overlap"):
        quality_intervals.build_quality_rules_result(
            replace(split, confirmation_rows=overlap),
            definitions(config),
            config,
            CRITERIA_ID,
            material_catalog=catalog,
        )

    with pytest.raises(ValueError, match="identity columns"):
        quality_intervals.build_quality_rules_result(
            replace(split, discovery_rows=split.discovery_rows.drop(columns="slab_no")),
            definitions(config),
            config,
            CRITERIA_ID,
            material_catalog=catalog,
        )


def test_transient_metric_computation_rejects_candidate_comparator_overlap():
    computation_type = quality_intervals._MetricComputation

    with pytest.raises(ValueError, match="overlap"):
        computation_type(
            _literal_metric(),
            ("A",),
            (0,),
            (0,),
            {0: "A"},
            {"A": 1},
        )


def test_public_quality_sidecar_retains_no_comparator_material_identity_field():
    names = {field.name for field in fields(equipment_models.QualitySplitSidecar)}

    assert names == {
        "adjustment_fields",
        "discovery_band_boundaries",
        "informative_stratum_keys",
        "discovery_weights",
        "candidate_material_keys",
        "comparator_counts_by_stratum",
    }
    assert not any("comparator_material" in name for name in names)


def test_quality_legacy_and_rich_calls_use_core_once_and_worker_payload_stays_minimal(monkeypatch):
    config = analysis_config()
    split, catalog = identified_split_and_catalog(strong_repeated_fixture())
    original_core = quality_intervals._build_quality_rules_core
    original_task = quality_intervals._bootstrap_task
    core_calls = 0
    observed_tasks = []

    def counted_core(*args, **kwargs):
        nonlocal core_calls
        core_calls += 1
        return original_core(*args, **kwargs)

    def observed_task(task):
        observed_tasks.append(task)
        return original_task(task)

    monkeypatch.setattr(quality_intervals, "_build_quality_rules_core", counted_core)
    monkeypatch.setattr(quality_intervals, "_bootstrap_task", observed_task)
    build_quality_rules(split, definitions(config), config, CRITERIA_ID)
    assert core_calls == 1
    core_calls = 0
    quality_intervals.build_quality_rules_result(
        split,
        definitions(config),
        config,
        CRITERIA_ID,
        material_catalog=catalog,
    )

    assert core_calls == 1
    assert observed_tasks
    for _, worker_rows, worker_criteria_id, worker_rule_id, replicates in observed_tasks:
        assert tuple(worker_rows.columns) == (
            "charge_id",
            "stratum",
            "candidate",
            "judge",
        )
        assert "material_key" not in worker_rows.columns
        assert all(type(value) in {str, bool} for value in worker_rows.to_numpy().ravel())
        assert type(worker_criteria_id) is str
        assert type(worker_rule_id) is str
        assert type(replicates) is int
