"""Deterministic, dependency-light quality-risk statistics."""

from __future__ import annotations

import hashlib
import math
import operator
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Integral, Real
from types import MappingProxyType

import pandas as pd

from equipment_quality.deterministic import type1_quantile


_MAX_UINT64 = (1 << 64) - 1
_MINIMUM_VALID_BOOTSTRAPS = 1900
_BOOTSTRAP_COLUMNS = ("charge_id", "stratum", "candidate", "judge")


def _is_boolean_scalar(value: object) -> bool:
    if isinstance(value, bool):
        return True
    return getattr(getattr(value, "dtype", None), "kind", None) == "b"


def _uint64(value: object, label: str) -> int:
    if _is_boolean_scalar(value):
        raise TypeError(f"{label} must not be boolean")
    if not isinstance(value, Integral):
        raise TypeError(f"{label} must be an integer")
    number = int(operator.index(value))
    if number < 0:
        raise ValueError(f"{label} must be non-negative")
    if number > _MAX_UINT64:
        raise ValueError(f"{label} must fit uint64")
    return number


def _finite_real(value: object, label: str) -> float:
    if _is_boolean_scalar(value):
        raise TypeError(f"{label} must not be boolean")
    if not isinstance(value, Real):
        raise TypeError(f"{label} must be a real number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


@dataclass(frozen=True)
class Stratum:
    """One candidate-versus-comparator 2x2 table."""

    a: int
    b: int
    c: int
    d: int
    key: str = ""

    def __post_init__(self) -> None:
        for field_name in ("a", "b", "c", "d"):
            object.__setattr__(
                self, field_name, _uint64(getattr(self, field_name), f"Stratum.{field_name}")
            )
        if type(self.key) is not str:
            raise TypeError("Stratum.key must be a built-in string")


@dataclass(frozen=True)
class StandardizedRates:
    """Directly standardized candidate and comparator defect rates."""

    candidate: float | None
    comparator: float | None
    risk_difference: float | None
    weights: Mapping[str, float]

    def __post_init__(self) -> None:
        for field_name in ("candidate", "comparator", "risk_difference"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    float(_finite_real(value, f"StandardizedRates.{field_name}")),
                )
        if not isinstance(self.weights, Mapping):
            raise TypeError("StandardizedRates.weights must be a mapping")
        try:
            entries = tuple(self.weights.items())
        except (TypeError, ValueError) as error:
            raise TypeError(
                "StandardizedRates.weights must contain key-value pairs"
            ) from error
        checked: dict[str, float] = {}
        for key, raw_weight in entries:
            if type(key) is not str:
                raise TypeError("StandardizedRates weight key must be a built-in string")
            if key in checked:
                raise ValueError(f"duplicate StandardizedRates weight key: {key}")
            weight = _finite_real(raw_weight, f"StandardizedRates weight for {key!r}")
            if weight < 0.0:
                raise ValueError("StandardizedRates weights must be non-negative")
            checked[key] = float(weight)
        object.__setattr__(
            self,
            "weights",
            MappingProxyType(
                dict(sorted(checked.items(), key=lambda entry: entry[0].encode("utf-8")))
            ),
        )


@dataclass(frozen=True)
class BootstrapCi:
    """Percentile RR interval and validity status for Charge resampling."""

    lower: float | None
    upper: float | None
    valid_replicates: int
    reason_code: str

    def __post_init__(self) -> None:
        valid_replicates = _uint64(self.valid_replicates, "valid_replicates")
        object.__setattr__(self, "valid_replicates", valid_replicates)
        if self.reason_code not in {"NONE", "TOO_FEW_VALID_BOOTSTRAPS"}:
            raise ValueError("unknown bootstrap reason_code")
        if (self.lower is None) != (self.upper is None):
            raise ValueError("bootstrap bounds must both be present or both be null")
        if self.lower is None:
            if self.reason_code != "TOO_FEW_VALID_BOOTSTRAPS":
                raise ValueError("null bootstrap bounds require TOO_FEW_VALID_BOOTSTRAPS")
            if valid_replicates >= _MINIMUM_VALID_BOOTSTRAPS:
                raise ValueError("null bootstrap bounds require fewer than 1900 valid replicates")
            return
        lower = _finite_real(self.lower, "bootstrap lower bound")
        upper = _finite_real(self.upper, "bootstrap upper bound")
        if lower <= 0.0 or upper <= 0.0 or lower > upper:
            raise ValueError("bootstrap bounds must be positive and ordered")
        if self.reason_code != "NONE" or valid_replicates < _MINIMUM_VALID_BOOTSTRAPS:
            raise ValueError("finite bootstrap bounds require 1900 valid replicates and reason NONE")
        object.__setattr__(self, "lower", float(lower))
        object.__setattr__(self, "upper", float(upper))


def _strata_snapshot(strata: Sequence[Stratum]) -> tuple[Stratum, ...]:
    try:
        snapshot = tuple(strata)
    except TypeError as error:
        raise TypeError("strata must be a sequence of Stratum values") from error
    if any(not isinstance(stratum, Stratum) for stratum in snapshot):
        raise TypeError("strata must contain only Stratum values")
    return tuple(
        sorted(snapshot, key=lambda item: (item.key.encode("utf-8"), item.a, item.b, item.c, item.d))
    )


def wilson_interval(
    defects: int, total: int, z: float
) -> tuple[float, float] | None:
    """Return a two-sided Wilson score interval, or ``None`` for total zero."""
    defects_number = _uint64(defects, "defects")
    total_number = _uint64(total, "total")
    if defects_number > total_number:
        raise ValueError("defects must not exceed total")
    try:
        z_number = _finite_real(z, "z")
    except ValueError as error:
        raise ValueError("z must be positive finite") from error
    if z_number <= 0.0:
        raise ValueError("z must be positive finite")
    if total_number == 0:
        return None

    proportion = defects_number / total_number
    z_squared = z_number * z_number
    denominator = 1.0 + z_squared / total_number
    centre = (proportion + z_squared / (2.0 * total_number)) / denominator
    half_width = (
        z_number
        * math.sqrt(
            proportion * (1.0 - proportion) / total_number
            + z_squared / (4.0 * total_number * total_number)
        )
        / denominator
    )
    lower = float(max(0.0, centre - half_width))
    upper = float(min(1.0, centre + half_width))
    if not (math.isfinite(lower) and math.isfinite(upper)):
        raise ArithmeticError("Wilson interval produced a non-finite result")
    return lower, upper


def _weights_snapshot(weights: Mapping[str, float]) -> dict[str, float]:
    if not isinstance(weights, Mapping):
        raise TypeError("weights must be a mapping")
    try:
        entries = tuple(weights.items())
    except (TypeError, ValueError) as error:
        raise TypeError("weights must contain key-value pairs") from error
    checked: dict[str, float] = {}
    for key, raw_weight in entries:
        if type(key) is not str:
            raise TypeError("weight key must be a built-in string")
        if key in checked:
            raise ValueError(f"duplicate weight key: {key}")
        weight = _finite_real(raw_weight, f"weight for {key!r}")
        if not 0.0 < weight <= 1.0:
            raise ValueError("weights must be positive probabilities no greater than 1")
        checked[key] = weight
    if not checked or not math.isclose(math.fsum(checked.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("weights must sum to 1")
    return checked


def standardized_rates(
    strata: Sequence[Stratum], weights: Mapping[str, float] | None
) -> StandardizedRates:
    """Directly standardize raw cell rates using discovery population weights.

    A stratum survives only when candidate and comparator denominators are both
    positive. Explicit discovery weights for absent strata are retained only
    for validation, then ignored while surviving weights are renormalized.
    """
    snapshot = _strata_snapshot(strata)
    all_keys: set[str] = set()
    for stratum in snapshot:
        if stratum.key in all_keys:
            raise ValueError(f"duplicate stratum key: {stratum.key}")
        all_keys.add(stratum.key)
    explicit = None if weights is None else _weights_snapshot(weights)
    surviving = tuple(
        stratum
        for stratum in snapshot
        if stratum.a + stratum.b > 0 and stratum.c + stratum.d > 0
    )
    if not surviving:
        return StandardizedRates(None, None, None, MappingProxyType({}))

    surviving_keys = {stratum.key for stratum in surviving}

    if explicit is None:
        totals = {stratum.key: stratum.a + stratum.b + stratum.c + stratum.d for stratum in surviving}
        population_total = sum(totals.values())
        normalized = {
            key: float(total / population_total)
            for key, total in sorted(totals.items(), key=lambda entry: entry[0].encode("utf-8"))
        }
    else:
        missing = sorted(
            (key for key in surviving_keys if key not in explicit),
            key=lambda key: key.encode("utf-8"),
        )
        if missing:
            raise ValueError(f"explicit weights are missing surviving stratum keys: {', '.join(missing)}")
        surviving_total = math.fsum(explicit[stratum.key] for stratum in surviving)
        normalized = {
            key: float(explicit[key] / surviving_total)
            for key in sorted(surviving_keys, key=lambda item: item.encode("utf-8"))
        }

    by_key = {stratum.key: stratum for stratum in surviving}
    candidate = math.fsum(
        normalized[key] * by_key[key].a / (by_key[key].a + by_key[key].b)
        for key in normalized
    )
    comparator = math.fsum(
        normalized[key] * by_key[key].c / (by_key[key].c + by_key[key].d)
        for key in normalized
    )
    risk_difference = candidate - comparator
    if not all(math.isfinite(value) for value in (candidate, comparator, risk_difference)):
        raise ArithmeticError("standardized rates produced a non-finite result")
    return StandardizedRates(
        float(candidate),
        float(comparator),
        float(risk_difference),
        MappingProxyType(normalized),
    )


def mantel_haenszel_rr(strata: Sequence[Stratum]) -> float | None:
    """Return the common Mantel-Haenszel risk ratio with local zero correction."""
    snapshot = _strata_snapshot(strata)
    if not snapshot:
        return None
    numerator_terms: list[float] = []
    denominator_terms: list[float] = []
    for stratum in snapshot:
        correction = 0.5 if 0 in (stratum.a, stratum.b, stratum.c, stratum.d) else 0.0
        a = stratum.a + correction
        b = stratum.b + correction
        c = stratum.c + correction
        d = stratum.d + correction
        candidate_total = a + b
        comparator_total = c + d
        total = candidate_total + comparator_total
        numerator_terms.append(a * comparator_total / total)
        denominator_terms.append(c * candidate_total / total)
    numerator = math.fsum(numerator_terms)
    denominator = math.fsum(denominator_terms)
    if denominator <= 0.0:
        return None
    result = numerator / denominator
    if not math.isfinite(result):
        return None
    return float(result)


def cmh_p_value(strata: Sequence[Stratum]) -> tuple[float, str]:
    """Return the uncorrected two-sided CMH normal-approximation p-value."""
    observed_minus_expected: list[float] = []
    variance_terms: list[float] = []
    for stratum in _strata_snapshot(strata):
        candidate_total = stratum.a + stratum.b
        comparator_total = stratum.c + stratum.d
        total = candidate_total + comparator_total
        if total == 0:
            continue
        defects = stratum.a + stratum.c
        non_defects = stratum.b + stratum.d
        expected = candidate_total * defects / total
        observed_minus_expected.append(stratum.a - expected)
        if total <= 1:
            variance_terms.append(0.0)
        else:
            variance_terms.append(
                candidate_total
                * comparator_total
                * defects
                * non_defects
                / (total * total * (total - 1))
            )
    variance = math.fsum(variance_terms)
    if variance <= 0.0:
        return 1.0, "ZERO_VARIANCE"
    z_score = math.fsum(observed_minus_expected) / math.sqrt(variance)
    p_value = math.erfc(abs(z_score) / math.sqrt(2.0))
    if not math.isfinite(p_value):
        raise ArithmeticError("CMH produced a non-finite p-value")
    return float(min(1.0, max(0.0, p_value))), "NONE"


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """Adjust one complete p-value family and restore its original order."""
    try:
        snapshot = tuple(p_values)
    except TypeError as error:
        raise TypeError("p_values must be a sequence") from error
    checked: list[float] = []
    for index, raw_p_value in enumerate(snapshot):
        try:
            p_value = _finite_real(raw_p_value, f"p-value at index {index}")
        except ValueError as error:
            raise ValueError("p-values must be in [0, 1]") from error
        if not 0.0 <= p_value <= 1.0:
            raise ValueError("p-values must be in [0, 1]")
        checked.append(p_value)
    count = len(checked)
    if count == 0:
        return []

    ordered = sorted(enumerate(checked), key=lambda item: (item[1], item[0]))
    adjusted_by_index = [0.0] * count
    cumulative_minimum = 1.0
    for rank_index in range(count - 1, -1, -1):
        original_index, p_value = ordered[rank_index]
        rank = rank_index + 1
        cumulative_minimum = min(cumulative_minimum, p_value * count / rank, 1.0)
        adjusted_by_index[original_index] = float(cumulative_minimum)
    return adjusted_by_index


def deterministic_sample_indices(
    seed: bytes, replicate: int, population_size: int
) -> tuple[int, ...]:
    """Draw one deterministic bootstrap index for every population member."""
    if type(seed) is not bytes:
        raise TypeError("seed must be built-in bytes")
    if len(seed) != 32:
        raise ValueError("seed must contain exactly 32 bytes")
    replicate_number = _uint64(replicate, "replicate")
    population_number = _uint64(population_size, "population_size")
    if population_number == 0:
        raise ValueError("population_size must be positive")
    replicate_bytes = replicate_number.to_bytes(8, "big", signed=False)
    return tuple(
        int.from_bytes(
            hashlib.sha256(
                seed + replicate_bytes + draw.to_bytes(8, "big", signed=False)
            ).digest()[:8],
            "big",
            signed=False,
        )
        % population_number
        for draw in range(population_number)
    )


def _sha256_identifier(value: object, label: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a built-in string")
    if not (
        len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must use the lowercase sha256 URI form")
    return value


def _row_identifier(value: object, label: str, *, allow_empty: bool) -> str:
    if type(value) is not str:
        raise TypeError(f"row {label} must be a built-in string")
    if value != value.strip() or (not allow_empty and not value):
        raise ValueError(f"row {label} must be normalized and non-empty")
    if any(character in value for character in ("\0", "\r", "\n")):
        raise ValueError(f"row {label} must not contain control delimiters")
    return value


def _charge_blocks(
    rows: pd.DataFrame,
) -> tuple[tuple[tuple[str, int, int, int, int], ...], ...]:
    if not isinstance(rows, pd.DataFrame):
        raise TypeError("rows must be a pandas DataFrame")
    if tuple(rows.columns) != _BOOTSTRAP_COLUMNS:
        raise ValueError(f"rows must have exact columns {_BOOTSTRAP_COLUMNS!r}")

    by_charge: dict[str, dict[str, list[int]]] = {}
    for row_number, row in enumerate(rows.itertuples(index=False, name=None)):
        charge_id = _row_identifier(row[0], f"charge_id at index {row_number}", allow_empty=False)
        stratum = _row_identifier(row[1], f"stratum at index {row_number}", allow_empty=True)
        candidate = row[2]
        if type(candidate) is not bool:
            raise TypeError(f"row candidate at index {row_number} must be boolean")
        judge = row[3]
        if type(judge) is not str or judge not in {"양품", "불량"}:
            raise ValueError(f"row judge at index {row_number} must be one of 양품 or 불량")
        cells = by_charge.setdefault(charge_id, {}).setdefault(stratum, [0, 0, 0, 0])
        cell_index = 0 if candidate and judge == "불량" else 1 if candidate else 2 if judge == "불량" else 3
        cells[cell_index] += 1

    return tuple(
        tuple(
            (stratum, *by_charge[charge_id][stratum])
            for stratum in sorted(by_charge[charge_id], key=lambda item: item.encode("utf-8"))
        )
        for charge_id in sorted(by_charge, key=lambda item: item.encode("utf-8"))
    )


def charge_bootstrap_rr_ci(
    rows: pd.DataFrame,
    criteria_id: str,
    rule_id: str,
    replicates: int = 2000,
) -> BootstrapCi:
    """Return the deterministic Charge-block percentile CI for the MH RR."""
    blocks = _charge_blocks(rows)
    criteria = _sha256_identifier(criteria_id, "criteria_id")
    rule = _sha256_identifier(rule_id, "rule_id")
    replicate_count = _uint64(replicates, "replicates")
    if replicate_count == 0:
        raise ValueError("replicates must be positive")
    return _charge_bootstrap_rr_ci(
        blocks,
        criteria,
        rule,
        replicate_count,
    )


def charge_bootstrap_rr_ci_with_seed(
    rows: pd.DataFrame,
    seed_material: str,
    rule_id: str,
    *,
    replicates: int,
) -> BootstrapCi:
    """Return the Charge-block CI from explicit authenticated seed material."""
    blocks = _charge_blocks(rows)
    material = _sha256_identifier(seed_material, "seed_material")
    rule = _sha256_identifier(rule_id, "rule_id")
    replicate_count = _uint64(replicates, "replicates")
    if replicate_count == 0:
        raise ValueError("replicates must be positive")
    return _charge_bootstrap_rr_ci(blocks, material, rule, replicate_count)


def _charge_bootstrap_rr_ci(
    blocks: tuple[tuple[tuple[str, int, int, int, int], ...], ...],
    seed_material: str,
    rule_id: str,
    replicate_count: int,
) -> BootstrapCi:
    seed = hashlib.sha256(
        (
            seed_material.encode("utf-8")
            + b"\0"
            + rule_id.encode("utf-8")
            + b"\0rule-ci-v1"
        )
    ).digest()
    population_size = len(blocks)
    if population_size == 0:
        return BootstrapCi(None, None, 0, "TOO_FEW_VALID_BOOTSTRAPS")

    valid: list[float] = []
    for replicate in range(replicate_count):
        cells_by_stratum: dict[str, list[int]] = {}
        for sampled_index in deterministic_sample_indices(seed, replicate, population_size):
            for stratum, a, b, c, d in blocks[sampled_index]:
                cells = cells_by_stratum.setdefault(stratum, [0, 0, 0, 0])
                cells[0] += a
                cells[1] += b
                cells[2] += c
                cells[3] += d
        sampled_strata = tuple(
            Stratum(*cells_by_stratum[key], key=key)
            for key in sorted(cells_by_stratum, key=lambda item: item.encode("utf-8"))
        )
        relative_risk = mantel_haenszel_rr(sampled_strata)
        if relative_risk is not None and math.isfinite(relative_risk) and relative_risk > 0.0:
            valid.append(float(relative_risk))

    valid_replicates = len(valid)
    if valid_replicates < _MINIMUM_VALID_BOOTSTRAPS:
        return BootstrapCi(
            None, None, valid_replicates, "TOO_FEW_VALID_BOOTSTRAPS"
        )
    lower = type1_quantile(valid, 0.025)
    upper = type1_quantile(valid, 0.975)
    if lower is None or upper is None:
        raise ArithmeticError("valid bootstrap replicates unexpectedly produced null quantiles")
    return BootstrapCi(float(lower), float(upper), valid_replicates, "NONE")
