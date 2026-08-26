"""Leak-free deterministic historical operating range contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import math

import pandas as pd
import pytest
from jsonschema.validators import validator_for

from equipment_quality.feature_roles import definitions
from equipment_quality import operating_ranges
from equipment_quality.operating_ranges import build_operating_ranges
from equipment_quality.schema import load_analysis_config
from factories.ranges import (
    collapsed_lower_tail_fixture,
    future_context_fixture,
    only,
    range_rows,
    sparse_context_fixture,
    split_with_reference,
    stage_date_fixture,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LITERAL_SIMPLE_RANGE_RULE_ID = (
    "sha256:cce7219c5b31e1379e9710b21ab7f1f06a88ab7c2f1d079ab5f8e8aecbeeb88e"
)


def analysis_config():
    return load_analysis_config(REPOSITORY_ROOT / "analysis/analysis_config.json")


def _definitions_for(config, *names: str):
    wanted = set(names)
    return tuple(item for item in definitions(config) if item.name in wanted)


def test_range_uses_type1_quantiles_and_disables_extremes_below_2000():
    config = analysis_config()
    rows = range_rows(values=range(1, 401))

    record = only(
        build_operating_ranges(
            split_with_reference(rows), _definitions_for(config, "f_pre_temp"), config
        )
    )

    assert record == {
        "ruleId": LITERAL_SIMPLE_RANGE_RULE_ID,
        "field": "f_pre_temp",
        "fieldRole": "DIRECT_OPERATION",
        "firstAvailableStage": "PREHEAT_COMPLETE",
        "equipmentType": "FURNACE",
        "equipmentId": "1호기",
        "contextLevel": 1,
        "context": {
            "furnace_no": "1호기",
            "steel_grade": "S1",
            "f_jangip_gubun": "CCR",
        },
        "support": 400,
        "median": 200.0,
        "p01": None,
        "p05": 20.0,
        "p95": 380.0,
        "p99": None,
        "lowerTailEnabled": False,
        "upperTailEnabled": False,
    }


@pytest.mark.parametrize(("support", "expected_count"), [(399, 0), (400, 1)])
def test_minimum_support_boundary_is_exact(support: int, expected_count: int):
    config = analysis_config()
    rows = range_rows(values=range(1, support + 1))

    records = build_operating_ranges(
        split_with_reference(rows), _definitions_for(config, "f_pre_temp"), config
    )

    assert len(records) == expected_count


def test_support_thresholds_are_read_from_config_without_fallback_defaults():
    config = analysis_config()
    policy = dict(config.operating_ranges)
    policy["minimumSupport"] = 3
    policy["extremeTailMinimumSupport"] = 5
    changed = replace(config, operating_ranges=policy)

    record = only(
        build_operating_ranges(
            split_with_reference(range_rows(values=[1.0, 2.0, 3.0, 4.0])),
            _definitions_for(changed, "f_pre_temp"),
            changed,
        )
    )

    assert record["support"] == 4
    assert record["p01"] is None
    assert record["p99"] is None


@pytest.mark.parametrize(
    ("support", "expected_p01", "expected_p99", "tails_enabled"),
    [
        (1999, None, None, (False, False)),
        (2000, 20.0, 1980.0, (True, True)),
    ],
)
def test_extreme_support_boundary_is_exact(
    support: int,
    expected_p01: float | None,
    expected_p99: float | None,
    tails_enabled: tuple[bool, bool],
):
    config = analysis_config()
    record = only(
        build_operating_ranges(
            split_with_reference(range_rows(values=range(1, support + 1))),
            _definitions_for(config, "f_pre_temp"),
            config,
        )
    )

    assert record["p01"] == expected_p01
    assert record["p99"] == expected_p99
    assert (record["lowerTailEnabled"], record["upperTailEnabled"]) == tails_enabled


def test_missing_and_nonfinite_numeric_values_never_contribute_support():
    config = analysis_config()
    values = [None, math.nan, math.inf, -math.inf, *range(1, 401)]

    record = only(
        build_operating_ranges(
            split_with_reference(range_rows(values=values)),
            _definitions_for(config, "f_pre_temp"),
            config,
        )
    )

    assert record["support"] == 400
    assert (record["p05"], record["median"], record["p95"]) == (
        20.0,
        200.0,
        380.0,
    )


def test_same_date_value_is_included_and_future_stage_event_is_excluded():
    config = analysis_config()
    split = stage_date_fixture(
        value_dates=["2025-01-04", "2025-01-05"], as_of="2025-01-04"
    )

    record = only(
        build_operating_ranges(split, _definitions_for(config, "f_pre_temp"), config)
    )

    assert record["support"] == 400
    assert record["median"] == 200.0


def test_sparse_specific_context_uses_exact_config_fallback_level_and_context():
    config = analysis_config()

    record = only(
        build_operating_ranges(
            sparse_context_fixture(), _definitions_for(config, "f_pre_temp"), config
        )
    )

    assert record["contextLevel"] == 2
    assert record["context"] == {
        "furnace_no": "1호기",
        "f_jangip_gubun": "CCR",
    }
    assert record["support"] == 400


def test_future_stage_context_is_removed_before_grouping_and_cannot_change_range():
    config = analysis_config()
    hierarchies = dict(config.range_context_hierarchies)
    hierarchies["FURNACE"] = (
        ("furnace_no", "hr_thick_band"),
        ("furnace_no",),
    )
    changed = replace(config, range_context_hierarchies=hierarchies)
    original = future_context_fixture()
    mutated_rows = original.reference_rows.copy(deep=True)
    mutated_rows["hr_thick"] = list(reversed(mutated_rows["hr_thick"].tolist()))
    mutated = split_with_reference(mutated_rows)

    before = build_operating_ranges(
        original, _definitions_for(changed, "f_pre_temp"), changed
    )
    after = build_operating_ranges(
        mutated, _definitions_for(changed, "f_pre_temp"), changed
    )

    assert before == after
    record = only(before)
    assert record["contextLevel"] == 0
    assert record["context"] == {"furnace_no": "1호기"}
    assert "hr_thick_band" not in record["context"]


def test_dimension_context_bands_are_fixed_type1_quartiles_on_reference_only():
    config = analysis_config()
    rows = range_rows(
        values=range(1, 1601),
        slab_width=[float(value) for value in range(1, 1601)],
    )
    holdout = range_rows(
        values=range(5001, 5401),
        slab_width=[999999.0] * 400,
    )

    records = build_operating_ranges(
        split_with_reference(rows, holdout_rows=holdout),
        _definitions_for(config, "f_pre_temp"),
        config,
    )

    assert [record["context"]["slab_width_band"] for record in records] == [
        "Q1",
        "Q2",
        "Q3",
        "Q4",
    ]
    assert [record["support"] for record in records] == [400, 400, 400, 400]
    assert all(record["contextLevel"] == 0 for record in records)


def test_tied_lower_tail_disables_only_lower_tail():
    config = analysis_config()
    record = only(
        build_operating_ranges(
            collapsed_lower_tail_fixture(),
            _definitions_for(config, "f_pre_temp"),
            config,
        )
    )

    assert record["p01"] == record["p05"] == 0.0
    assert record["p95"] != record["p99"]
    assert record["lowerTailEnabled"] is False
    assert record["upperTailEnabled"] is True


def test_tied_upper_tail_disables_only_upper_tail():
    config = analysis_config()
    values = [*range(1, 1900), *([2000.0] * 101)]
    record = only(
        build_operating_ranges(
            split_with_reference(range_rows(values=values)),
            _definitions_for(config, "f_pre_temp"),
            config,
        )
    )

    assert record["p01"] != record["p05"]
    assert record["p95"] == record["p99"] == 2000.0
    assert record["lowerTailEnabled"] is True
    assert record["upperTailEnabled"] is False


def test_empty_and_insufficient_groups_produce_no_ranges():
    config = analysis_config()

    assert build_operating_ranges(
        split_with_reference(pd.DataFrame()),
        _definitions_for(config, "f_pre_temp"),
        config,
    ) == []
    assert build_operating_ranges(
        split_with_reference(range_rows(values=range(1, 400))),
        _definitions_for(config, "f_pre_temp"),
        config,
    ) == []


def test_holdout_feature_mutation_cannot_change_ranges_or_ids():
    config = analysis_config()
    reference = range_rows(values=range(1, 401))
    holdout = range_rows(values=range(1001, 1401))
    mutated_holdout = holdout.copy(deep=True)
    mutated_holdout["f_pre_temp"] = [-999999.0] * 400
    wanted = _definitions_for(config, "f_pre_temp")

    before = build_operating_ranges(
        split_with_reference(reference, holdout_rows=holdout), wanted, config
    )
    after = build_operating_ranges(
        split_with_reference(reference, holdout_rows=mutated_holdout), wanted, config
    )

    assert before == after
    assert only(before)["ruleId"] == LITERAL_SIMPLE_RANGE_RULE_ID


def test_product_state_numeric_ranges_preserve_role_but_categorical_state_is_excluded():
    config = analysis_config()
    numeric = build_operating_ranges(
        split_with_reference(range_rows(values=range(1, 401), field="slab_width")),
        _definitions_for(config, "slab_width"),
        config,
    )
    categorical = build_operating_ranges(
        split_with_reference(range_rows(values=["HSHS"] * 400, field="slab_grind")),
        _definitions_for(config, "slab_grind"),
        config,
    )

    assert only(numeric)["fieldRole"] == "PRODUCT_STATE_REFERENCE"
    assert categorical == []


def test_input_and_definition_order_do_not_change_output_order_or_rule_ids():
    config = analysis_config()
    rows = range_rows(values=range(1, 401))
    rows["f_heat_temp"] = [float(value) for value in range(1001, 1401)]
    wanted = _definitions_for(config, "f_pre_temp", "f_heat_temp")

    first = build_operating_ranges(split_with_reference(rows), wanted, config)
    second = build_operating_ranges(
        split_with_reference(rows.iloc[::-1].reset_index(drop=True)),
        tuple(reversed(wanted)),
        config,
    )

    assert first == second
    assert [record["field"] for record in first] == ["f_pre_temp", "f_heat_temp"]
    assert first[0]["ruleId"] == LITERAL_SIMPLE_RANGE_RULE_ID


def test_rule_id_collision_between_distinct_ranges_is_fatal(monkeypatch):
    config = analysis_config()
    rows = range_rows(
        values=range(1, 801),
        furnace_no=["1호기"] * 400 + ["2호기"] * 400,
    )
    monkeypatch.setattr(
        operating_ranges, "sha256_uri", lambda _: "sha256:" + "0" * 64
    )

    with pytest.raises(ValueError, match="rule ID collision"):
        build_operating_ranges(
            split_with_reference(rows),
            _definitions_for(config, "f_pre_temp"),
            config,
        )


def test_range_records_validate_against_normative_contract():
    config = analysis_config()
    records = build_operating_ranges(
        split_with_reference(range_rows(values=range(1, 2001))),
        _definitions_for(config, "f_pre_temp"),
        config,
    )
    schema = json.loads(
        (
            REPOSITORY_ROOT
            / "contracts/equipment-monitor/v1/equipment_operating_ranges.schema.json"
        ).read_text(encoding="utf-8")
    )
    validator = validator_for(schema)
    validator.check_schema(schema)

    validator(schema).validate(
        {
            "schemaVersion": "sfep-operating-ranges/v1",
            "criteriaId": "sha256:" + "1" * 64,
            "asOf": "2025-01-04",
            "ranges": records,
        }
    )
