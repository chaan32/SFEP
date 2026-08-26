"""Independent hand calculations for the quality-risk statistics primitives."""

from __future__ import annotations

import math
import hashlib
from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from equipment_quality.statistics import (
    Stratum,
    benjamini_hochberg,
    charge_bootstrap_rr_ci,
    cmh_p_value,
    deterministic_sample_indices,
    mantel_haenszel_rr,
    standardized_rates,
    wilson_interval,
)
from factories.statistics_fixtures import (
    bootstrap_fixture_with_two_coils_per_charge,
    two_strata_fixture,
)


CRITERIA_ID = "sha256:" + "1" * 64
RULE_ID = "sha256:" + "2" * 64


def test_statistics_result_records_are_frozen():
    stratum = Stratum(a=1, b=2, c=3, d=4, key="A")
    rates = standardized_rates((stratum,), None)

    with pytest.raises(FrozenInstanceError):
        stratum.a = 2
    with pytest.raises(FrozenInstanceError):
        rates.candidate = 0.0


@pytest.mark.parametrize("field", ("a", "b", "c", "d"))
def test_stratum_rejects_negative_cells(field):
    values = {"a": 1, "b": 2, "c": 3, "d": 4}
    values[field] = -1
    with pytest.raises(ValueError, match="non-negative"):
        Stratum(**values)


@pytest.mark.parametrize("value", (True, False))
def test_stratum_rejects_boolean_cells(value):
    with pytest.raises(TypeError, match="boolean"):
        Stratum(a=value, b=2, c=3, d=4)


@pytest.mark.parametrize("value", (1.0, "1", None))
def test_stratum_rejects_non_integer_cells(value):
    with pytest.raises(TypeError, match="integer"):
        Stratum(a=value, b=2, c=3, d=4)


def test_stratum_rejects_counts_above_uint64():
    with pytest.raises(ValueError, match="uint64"):
        Stratum(a=2**64, b=0, c=0, d=0)


def test_stratum_requires_a_builtin_string_key():
    with pytest.raises(TypeError, match="key"):
        Stratum(a=1, b=2, c=3, d=4, key=1)


def test_wilson_interval_matches_hand_calculation():
    assert wilson_interval(5, 10, 1.959963984540054) == pytest.approx(
        (0.236593090512564, 0.7634069094874361)
    )


def test_wilson_interval_returns_none_for_a_zero_denominator():
    assert wilson_interval(0, 0, 1.959963984540054) is None


@pytest.mark.parametrize(
    ("defects", "total", "error", "message"),
    [
        (True, 10, TypeError, "boolean"),
        (1, False, TypeError, "boolean"),
        (1.0, 10, TypeError, "integer"),
        (1, 10.0, TypeError, "integer"),
        (-1, 10, ValueError, "non-negative"),
        (1, -10, ValueError, "non-negative"),
        (11, 10, ValueError, "exceed"),
        (2**64, 2**64, ValueError, "uint64"),
    ],
)
def test_wilson_interval_rejects_invalid_counts(defects, total, error, message):
    with pytest.raises(error, match=message):
        wilson_interval(defects, total, 1.959963984540054)


@pytest.mark.parametrize("z", (0.0, -1.0, math.nan, math.inf))
def test_wilson_interval_rejects_nonpositive_or_nonfinite_z(z):
    with pytest.raises(ValueError, match="positive finite"):
        wilson_interval(1, 10, z)


def test_wilson_interval_rejects_boolean_z():
    with pytest.raises(TypeError, match="boolean"):
        wilson_interval(1, 10, True)


def test_wilson_interval_returns_builtin_finite_bounded_values():
    result = wilson_interval(1, 3, 1.959963984540054)
    assert result is not None
    assert all(type(value) is float and math.isfinite(value) for value in result)
    assert 0.0 <= result[0] <= result[1] <= 1.0


def test_direct_standardization_uses_fixed_population_weights():
    rates = standardized_rates(two_strata_fixture(), {"A": 0.25, "B": 0.75})
    assert rates.candidate == pytest.approx(0.175)
    assert rates.comparator == pytest.approx(0.10)
    assert rates.risk_difference == pytest.approx(0.075)
    assert dict(rates.weights) == {"A": 0.25, "B": 0.75}


def test_discovery_standardization_derives_population_weights():
    rates = standardized_rates(
        (
            Stratum(a=1, b=9, c=1, d=9, key="small"),
            Stratum(a=8, b=12, c=2, d=18, key="large"),
        ),
        None,
    )
    assert dict(rates.weights) == pytest.approx({"large": 2.0 / 3.0, "small": 1.0 / 3.0})
    assert rates.candidate == pytest.approx(0.3)
    assert rates.comparator == pytest.approx(0.1)


def test_confirmation_ignores_absent_discovery_strata_and_renormalizes_survivors():
    rates = standardized_rates(
        (Stratum(a=1, b=9, c=2, d=8, key="A"),),
        {"A": 0.25, "B": 0.75},
    )
    assert dict(rates.weights) == {"A": 1.0}
    assert rates.candidate == pytest.approx(0.1)
    assert rates.comparator == pytest.approx(0.2)


def test_standardization_drops_noninformative_strata_before_weight_validation():
    rates = standardized_rates(
        (
            Stratum(a=1, b=9, c=2, d=8, key="survives"),
            Stratum(a=0, b=0, c=1, d=9, key="candidate-absent"),
            Stratum(a=1, b=9, c=0, d=0, key="comparator-absent"),
        ),
        {"survives": 0.5, "discovery-only": 0.5},
    )
    assert dict(rates.weights) == {"survives": 1.0}
    assert rates.candidate == pytest.approx(0.1)
    assert rates.comparator == pytest.approx(0.2)


def test_standardization_empty_or_all_zero_denominators_is_explicitly_null():
    empty = standardized_rates((), None)
    zero = standardized_rates((Stratum(0, 0, 0, 0, key="empty"),), None)
    for result in (empty, zero):
        assert result.candidate is None
        assert result.comparator is None
        assert result.risk_difference is None
        assert dict(result.weights) == {}


def test_standardization_requires_explicit_weight_for_every_surviving_key():
    with pytest.raises(ValueError, match="missing.*B"):
        standardized_rates(two_strata_fixture(), {"A": 1.0})


def test_standardization_rejects_duplicate_surviving_keys():
    with pytest.raises(ValueError, match="duplicate.*A"):
        standardized_rates(
            (Stratum(1, 9, 1, 9, "A"), Stratum(2, 8, 1, 9, "A")),
            {"A": 1.0},
        )


@pytest.mark.parametrize(
    ("weights", "error", "message"),
    [
        ({"A": 0.0, "B": 1.0}, ValueError, "positive"),
        ({"A": -0.1, "B": 1.1}, ValueError, "positive"),
        ({"A": math.nan, "B": 1.0}, ValueError, "finite"),
        ({"A": math.inf, "B": 1.0}, ValueError, "finite"),
        ({"A": 0.2, "B": 0.2}, ValueError, "sum to 1"),
        ({"A": True, "B": 0.0}, TypeError, "boolean"),
        ({1: 0.25, "B": 0.75}, TypeError, "key"),
    ],
)
def test_standardization_rejects_invalid_explicit_weights(weights, error, message):
    with pytest.raises(error, match=message):
        standardized_rates(two_strata_fixture(), weights)


def test_standardization_is_independent_of_stratum_and_weight_input_order():
    forward = standardized_rates(two_strata_fixture(), {"A": 0.25, "B": 0.75})
    reverse = standardized_rates(tuple(reversed(two_strata_fixture())), {"B": 0.75, "A": 0.25})
    assert reverse == forward
    assert tuple(reverse.weights) == ("A", "B")


def test_mh_adds_half_to_all_four_cells_only_in_zero_stratum():
    rr = mantel_haenszel_rr((Stratum(a=0, b=9, c=1, d=9),))
    assert rr == pytest.approx((0.5 * 11.0 / 21.0) / (1.5 * 10.0 / 21.0))


def test_mh_does_not_correct_a_stratum_without_zero_cells():
    rr = mantel_haenszel_rr((Stratum(a=2, b=8, c=1, d=9),))
    assert rr == pytest.approx((2.0 * 10.0 / 20.0) / (1.0 * 10.0 / 20.0))


def test_mh_empty_is_null_and_all_zero_stratum_is_neutral():
    assert mantel_haenszel_rr(()) is None
    assert mantel_haenszel_rr((Stratum(0, 0, 0, 0),)) == pytest.approx(1.0)


def test_mh_is_order_invariant_and_returns_a_builtin_finite_float():
    strata = (
        Stratum(0, 9, 1, 9, "B"),
        Stratum(4, 6, 2, 8, "A"),
    )
    forward = mantel_haenszel_rr(strata)
    reverse = mantel_haenszel_rr(tuple(reversed(strata)))
    assert forward == reverse
    assert type(forward) is float and math.isfinite(forward)


def test_cmh_zero_variance_returns_one_with_reason():
    p_value, reason = cmh_p_value((Stratum(a=0, b=10, c=0, d=20),))
    assert p_value == 1.0
    assert reason == "ZERO_VARIANCE"


def test_cmh_empty_and_all_zero_tables_have_zero_variance():
    assert cmh_p_value(()) == (1.0, "ZERO_VARIANCE")
    assert cmh_p_value((Stratum(0, 0, 0, 0),)) == (1.0, "ZERO_VARIANCE")


def test_cmh_matches_independent_uncorrected_hand_vector():
    p_value, reason = cmh_p_value(
        (
            Stratum(a=12, b=8, c=5, d=15, key="A"),
            Stratum(a=6, b=14, c=3, d=17, key="B"),
        )
    )
    assert p_value == pytest.approx(0.015836879771955324)
    assert reason == "NONE"


def test_cmh_zero_cell_uses_raw_counts_not_mh_half_correction():
    p_value, reason = cmh_p_value((Stratum(a=0, b=9, c=1, d=9),))
    assert p_value == pytest.approx(0.34278171114790873)
    assert reason == "NONE"


def test_cmh_is_order_invariant_and_returns_a_bounded_builtin_probability():
    strata = (
        Stratum(12, 8, 5, 15, "B"),
        Stratum(6, 14, 3, 17, "A"),
    )
    forward = cmh_p_value(strata)
    reverse = cmh_p_value(tuple(reversed(strata)))
    assert forward == reverse
    assert type(forward[0]) is float and math.isfinite(forward[0])
    assert 0.0 <= forward[0] <= 1.0


def test_bh_includes_upstream_invalid_candidates_as_literal_one_and_preserves_order():
    assert benjamini_hochberg((0.01, 1.0, 0.04)) == pytest.approx((0.03, 1.0, 0.06))


def test_bh_ties_extremes_and_reverse_cumulative_minimum():
    assert benjamini_hochberg((1.0, 0.01, 0.5, 0.01, 0.0)) == pytest.approx(
        (1.0, 1.0 / 60.0, 0.625, 1.0 / 60.0, 0.0)
    )


def test_bh_empty_returns_an_empty_builtin_list():
    result = benjamini_hochberg(())
    assert result == []
    assert type(result) is list


@pytest.mark.parametrize("p_value", (-0.01, 1.01, math.nan, math.inf, -math.inf))
def test_bh_rejects_invalid_probabilities(p_value):
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        benjamini_hochberg((p_value,))


@pytest.mark.parametrize("p_value", (True, False))
def test_bh_rejects_boolean_probabilities(p_value):
    with pytest.raises(TypeError, match="boolean"):
        benjamini_hochberg((p_value,))


def test_bh_outputs_are_builtin_finite_probabilities():
    result = benjamini_hochberg((0.2, 0.05, 1.0))
    assert all(type(value) is float and math.isfinite(value) for value in result)
    assert all(0.0 <= value <= 1.0 for value in result)


def test_sha256_sampler_matches_independent_index_vector():
    seed = hashlib.sha256(
        (CRITERIA_ID + "\0" + RULE_ID + "\0rule-ci-v1").encode("utf-8")
    ).digest()
    assert deterministic_sample_indices(seed, replicate=0, population_size=3) == (1, 1, 2)


def test_sampler_is_deterministic_bounded_and_returns_builtin_indices():
    seed = bytes(range(32))
    first = deterministic_sample_indices(seed, replicate=17, population_size=11)
    second = deterministic_sample_indices(seed, replicate=17, population_size=11)
    assert first == second
    assert type(first) is tuple and len(first) == 11
    assert all(type(index) is int and 0 <= index < 11 for index in first)


def test_sampler_accepts_the_largest_uint64_replicate_without_overflow():
    assert deterministic_sample_indices(b"x" * 32, 2**64 - 1, 1) == (0,)


@pytest.mark.parametrize("seed", (b"x" * 31, b"x" * 33, bytearray(b"x" * 32), "x" * 32))
def test_sampler_requires_exactly_32_builtin_seed_bytes(seed):
    error = ValueError if type(seed) is bytes else TypeError
    with pytest.raises(error, match="seed"):
        deterministic_sample_indices(seed, 0, 1)


@pytest.mark.parametrize("replicate", (True, -1, 1.0, 2**64))
def test_sampler_rejects_invalid_uint64_replicates(replicate):
    error = TypeError if replicate is True or isinstance(replicate, float) else ValueError
    with pytest.raises(error, match="replicate"):
        deterministic_sample_indices(b"x" * 32, replicate, 1)


@pytest.mark.parametrize("population_size", (True, 0, -1, 1.0, 2**64))
def test_sampler_rejects_invalid_uint64_population_sizes(population_size):
    error = TypeError if population_size is True or isinstance(population_size, float) else ValueError
    with pytest.raises(error, match="population_size"):
        deterministic_sample_indices(b"x" * 32, 0, population_size)


def test_charge_bootstrap_is_reproducible_and_matches_block_golden_ci():
    rows = bootstrap_fixture_with_two_coils_per_charge()
    first = charge_bootstrap_rr_ci(rows, CRITERIA_ID, RULE_ID)
    second = charge_bootstrap_rr_ci(rows, CRITERIA_ID, RULE_ID)
    assert first == second
    assert first.lower == pytest.approx(0.4)
    assert first.upper == pytest.approx(13.0 / 3.0)
    assert first.valid_replicates == 2000
    assert first.reason_code == "NONE"
    assert type(first.lower) is float and math.isfinite(first.lower)
    assert type(first.upper) is float and math.isfinite(first.upper)


def test_charge_bootstrap_result_record_is_frozen():
    result = charge_bootstrap_rr_ci(
        bootstrap_fixture_with_two_coils_per_charge(), CRITERIA_ID, RULE_ID
    )
    with pytest.raises(FrozenInstanceError):
        result.lower = 0.0


def test_charge_bootstrap_preserves_one_charge_with_all_of_its_coils():
    rows = pd.DataFrame(
        [
            {"charge_id": "ONLY", "stratum": "", "candidate": True, "judge": "불량"},
            {"charge_id": "ONLY", "stratum": "", "candidate": False, "judge": "양품"},
        ],
        columns=("charge_id", "stratum", "candidate", "judge"),
    )
    result = charge_bootstrap_rr_ci(rows, CRITERIA_ID, RULE_ID, replicates=1900)
    assert result.lower == pytest.approx(3.0)
    assert result.upper == pytest.approx(3.0)
    assert result.valid_replicates == 1900
    assert result.reason_code == "NONE"


def test_charge_bootstrap_is_independent_of_input_row_order_and_does_not_mutate_rows():
    rows = bootstrap_fixture_with_two_coils_per_charge()
    before = rows.copy(deep=True)
    shuffled = rows.sample(frac=1.0, random_state=41).reset_index(drop=True)
    assert charge_bootstrap_rr_ci(rows, CRITERIA_ID, RULE_ID) == charge_bootstrap_rr_ci(
        shuffled, CRITERIA_ID, RULE_ID
    )
    pd.testing.assert_frame_equal(rows, before)


def test_charge_bootstrap_threshold_is_exactly_1899_versus_1900():
    rows = bootstrap_fixture_with_two_coils_per_charge()
    insufficient = charge_bootstrap_rr_ci(rows, CRITERIA_ID, RULE_ID, replicates=1899)
    exact = charge_bootstrap_rr_ci(rows, CRITERIA_ID, RULE_ID, replicates=1900)
    assert insufficient.lower is None and insufficient.upper is None
    assert insufficient.valid_replicates == 1899
    assert insufficient.reason_code == "TOO_FEW_VALID_BOOTSTRAPS"
    assert exact.lower is not None and exact.upper is not None
    assert exact.valid_replicates == 1900
    assert exact.reason_code == "NONE"


def test_charge_bootstrap_empty_population_has_no_valid_replicates():
    rows = pd.DataFrame(columns=("charge_id", "stratum", "candidate", "judge"))
    result = charge_bootstrap_rr_ci(rows, CRITERIA_ID, RULE_ID)
    assert result.lower is None and result.upper is None
    assert result.valid_replicates == 0
    assert result.reason_code == "TOO_FEW_VALID_BOOTSTRAPS"


@pytest.mark.parametrize(
    "columns",
    [
        ("charge_id", "stratum", "candidate"),
        ("charge_id", "stratum", "candidate", "judge", "extra"),
        ("judge", "candidate", "stratum", "charge_id"),
    ],
)
def test_charge_bootstrap_requires_exact_ordered_row_schema(columns):
    rows = pd.DataFrame(columns=columns)
    with pytest.raises(ValueError, match="exact columns"):
        charge_bootstrap_rr_ci(rows, CRITERIA_ID, RULE_ID)


def test_charge_bootstrap_requires_a_dataframe():
    with pytest.raises(TypeError, match="DataFrame"):
        charge_bootstrap_rr_ci([], CRITERIA_ID, RULE_ID)


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("charge_id", "", "charge_id"),
        ("charge_id", "   ", "charge_id"),
        ("charge_id", "bad\0id", "charge_id"),
        ("charge_id", 1, "charge_id"),
        ("stratum", "bad\nkey", "stratum"),
        ("stratum", 1, "stratum"),
        ("candidate", 1, "candidate"),
        ("candidate", None, "candidate"),
        ("judge", "defect", "judge"),
        ("judge", None, "judge"),
        ("judge", 1, "judge"),
    ],
)
def test_charge_bootstrap_rejects_malformed_rows(column, value, message):
    row = {"charge_id": "C01", "stratum": "A", "candidate": True, "judge": "불량"}
    row[column] = value
    rows = pd.DataFrame([row], columns=("charge_id", "stratum", "candidate", "judge"))
    with pytest.raises((TypeError, ValueError), match=message):
        charge_bootstrap_rr_ci(rows, CRITERIA_ID, RULE_ID)


@pytest.mark.parametrize(
    "invalid_id",
    (
        None,
        "sha256:" + "a" * 63,
        "sha256:" + "A" * 64,
        "sha256:" + "g" * 64,
        "1" * 64,
    ),
)
def test_charge_bootstrap_rejects_invalid_criteria_and_rule_ids(invalid_id):
    rows = bootstrap_fixture_with_two_coils_per_charge()
    with pytest.raises((TypeError, ValueError), match="criteria_id"):
        charge_bootstrap_rr_ci(rows, invalid_id, RULE_ID)
    with pytest.raises((TypeError, ValueError), match="rule_id"):
        charge_bootstrap_rr_ci(rows, CRITERIA_ID, invalid_id)


@pytest.mark.parametrize("replicates", (True, 0, -1, 1.0, 2**64))
def test_charge_bootstrap_rejects_invalid_replicate_counts(replicates):
    error = TypeError if replicates is True or isinstance(replicates, float) else ValueError
    with pytest.raises(error, match="replicates"):
        charge_bootstrap_rr_ci(
            bootstrap_fixture_with_two_coils_per_charge(),
            CRITERIA_ID,
            RULE_ID,
            replicates=replicates,
        )
