"""Label-free quality-risk candidates and globally adjusted evidence metrics."""

from __future__ import annotations

import math
import os
import pickle
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from numbers import Integral, Real
from types import MappingProxyType

import pandas as pd

from equipment_quality.deterministic import canonical_json_bytes, sha256_uri, type1_quantile
from equipment_quality.feature_roles import STAGE_RANK, FeatureDefinition, definitions as configured_definitions
from equipment_quality.models import AnalysisConfig, TimeSplitResult
from equipment_quality.statistics import (
    BootstrapCi,
    Stratum,
    benjamini_hochberg,
    charge_bootstrap_rr_ci,
    cmh_p_value,
    mantel_haenszel_rr,
    standardized_rates,
    wilson_interval,
)


_CANDIDATE_ROLES = {
    "DIRECT_OPERATION",
    "PRODUCT_STATE_REFERENCE",
    "CONTEXT",
    "EQUIPMENT_IDENTIFIER",
}
_EQUIPMENT_IDENTIFIER_ROLE = "EQUIPMENT_IDENTIFIER"
_MISSING = object()
_PARALLEL_BOOTSTRAP_MINIMUM = 16
_MAX_BOOTSTRAP_WORKERS = 10


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return False
    missing = pd.isna(value)
    return bool(missing) if isinstance(missing, (bool, type(pd.NA))) else False


def _finite_number(value: object) -> float | None:
    if _is_missing(value):
        return None
    if isinstance(value, bool) or getattr(getattr(value, "dtype", None), "kind", None) == "b":
        return None
    if not isinstance(value, Real):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _category(value: object) -> str | None:
    return value if type(value) is str else None


def _scalar(value: object) -> str | int | float | bool | object:
    if _is_missing(value):
        return _MISSING
    if type(value) is str:
        return value
    if type(value) is bool:
        return value
    if isinstance(value, Integral) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        return number if math.isfinite(number) else _MISSING
    return _MISSING


@dataclass(frozen=True)
class PredicateClause:
    field: str
    type: str
    lower: float | None
    lower_inclusive: bool | None
    upper: float | None
    upper_inclusive: bool | None
    values: tuple[str | int | float | bool, ...] | None

    def __post_init__(self) -> None:
        if type(self.field) is not str or not self.field:
            raise ValueError("predicate field must be a non-empty built-in string")
        if self.type == "NUMERIC_INTERVAL":
            if self.values is not None:
                raise ValueError("numeric predicate values must be null")
            if self.lower is None or self.upper is None:
                raise ValueError("numeric predicate bounds must be present")
            lower = _finite_number(self.lower)
            upper = _finite_number(self.upper)
            if lower is None or upper is None or lower > upper:
                raise ValueError("numeric predicate bounds must be finite and ordered")
            if type(self.lower_inclusive) is not bool or type(self.upper_inclusive) is not bool:
                raise TypeError("numeric predicate inclusion flags must be booleans")
            object.__setattr__(self, "lower", lower)
            object.__setattr__(self, "upper", upper)
            return
        if self.type != "CATEGORY_IN":
            raise ValueError(f"unknown predicate type: {self.type}")
        if any(value is not None for value in (self.lower, self.lower_inclusive, self.upper, self.upper_inclusive)):
            raise ValueError("category predicate bounds must be null")
        if not isinstance(self.values, tuple) or not self.values:
            raise ValueError("category predicate values must be a non-empty tuple")
        checked: list[str | int | float | bool] = []
        for value in self.values:
            scalar = _scalar(value)
            if scalar is _MISSING:
                raise ValueError("category predicate values must be finite non-null scalars")
            checked.append(scalar)
        if len(set(checked)) != len(checked):
            raise ValueError("category predicate values must be unique")
        object.__setattr__(self, "values", tuple(checked))

    def to_wire(self) -> dict[str, object]:
        return {
            "field": self.field,
            "type": self.type,
            "lower": self.lower,
            "lowerInclusive": self.lower_inclusive,
            "upper": self.upper,
            "upperInclusive": self.upper_inclusive,
            "values": None if self.values is None else list(self.values),
        }


@dataclass(frozen=True)
class Candidate:
    analysis_family: str
    evidence_family: str
    field_names: tuple[str, ...]
    predicate: tuple[PredicateClause, ...]
    first_available_stage: str
    equipment_type: str
    application_scope: str
    equipment_id: str
    application_context: Mapping[str, str | int | float | bool]
    adjustment_level: int
    adjustment_fields_dropped: tuple[str, ...]
    adjustment_kind: str
    early_warning_eligible: bool
    support: int
    comparator_support: int
    informative_strata: int
    rule_id: str
    adjustment_fields: tuple[str, ...]
    discovery_weights: Mapping[str, float]
    band_boundaries: Mapping[str, tuple[float, float, float] | None]

    def __post_init__(self) -> None:
        if not self.predicate or len(self.predicate) != len(self.field_names):
            raise ValueError("candidate predicate must match its non-empty field names")
        if self.support < 1 or self.comparator_support < 0 or self.informative_strata < 0:
            raise ValueError("candidate support metadata must be non-negative")
        object.__setattr__(self, "field_names", tuple(self.field_names))
        object.__setattr__(self, "predicate", tuple(self.predicate))
        object.__setattr__(self, "adjustment_fields_dropped", tuple(self.adjustment_fields_dropped))
        object.__setattr__(self, "adjustment_fields", tuple(self.adjustment_fields))
        object.__setattr__(
            self,
            "application_context",
            MappingProxyType(dict(self.application_context)),
        )
        object.__setattr__(
            self,
            "discovery_weights",
            MappingProxyType(dict(self.discovery_weights)),
        )
        object.__setattr__(
            self,
            "band_boundaries",
            MappingProxyType(dict(self.band_boundaries)),
        )

    @property
    def canonical_predicate(self) -> bytes:
        return canonical_json_bytes(
            {"allOf": [clause.to_wire() for clause in self.predicate]}
        )

    def identity(self) -> dict[str, object]:
        return {
            "analysisFamily": self.analysis_family,
            "fieldNames": list(self.field_names),
            "predicate": {"allOf": [clause.to_wire() for clause in self.predicate]},
            "firstAvailableStage": self.first_available_stage,
            "equipmentType": self.equipment_type,
            "applicationScope": self.application_scope,
            "equipmentId": self.equipment_id,
            "applicationContext": dict(self.application_context),
            "adjustmentLevel": self.adjustment_level,
            "adjustmentFieldsDropped": list(self.adjustment_fields_dropped),
            "adjustmentKind": self.adjustment_kind,
        }


@dataclass(frozen=True)
class QualityMetric:
    support: int
    defects: int
    crude_rate: float | None
    crude_rate_ci_lower: float | None
    crude_rate_ci_upper: float | None
    adjusted_rate: float | None
    comparator_adjusted_rate: float | None
    risk_difference: float | None
    relative_risk: float | None
    relative_risk_ci_lower: float | None
    relative_risk_ci_upper: float | None
    p_value: float | None
    q_value: float | None
    reason_code: str

    def to_wire(self) -> dict[str, object]:
        return {
            "support": self.support,
            "defects": self.defects,
            "crudeRate": self.crude_rate,
            "crudeRateCiLower": self.crude_rate_ci_lower,
            "crudeRateCiUpper": self.crude_rate_ci_upper,
            "adjustedRate": self.adjusted_rate,
            "comparatorAdjustedRate": self.comparator_adjusted_rate,
            "riskDifference": self.risk_difference,
            "relativeRisk": self.relative_risk,
            "relativeRiskCiLower": self.relative_risk_ci_lower,
            "relativeRiskCiUpper": self.relative_risk_ci_upper,
            "pValue": self.p_value,
            "qValue": self.q_value,
            "reasonCode": self.reason_code,
        }


def _validate_sha256_uri(value: object, label: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a built-in string")
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must use the lowercase sha256 URI form")
    return value


@dataclass(frozen=True)
class QualityRule:
    candidate: Candidate
    discovery: QualityMetric
    confirmation: QualityMetric
    grade: str
    display_merge_rule_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, Candidate):
            raise TypeError("candidate must be a Candidate")
        if not isinstance(self.discovery, QualityMetric):
            raise TypeError("discovery must be a QualityMetric")
        if not isinstance(self.confirmation, QualityMetric):
            raise TypeError("confirmation must be a QualityMetric")
        if type(self.grade) is not str:
            raise TypeError("grade must be a built-in string")
        if self.grade not in {
            "NORMAL",
            "UNCONFIRMED",
            "INSUFFICIENT_EVIDENCE",
            "CAUTION",
            "DANGER",
        }:
            raise ValueError(f"unknown quality rule grade: {self.grade}")
        if any(
            value is not None
            for value in (
                self.confirmation.relative_risk_ci_lower,
                self.confirmation.relative_risk_ci_upper,
                self.confirmation.p_value,
                self.confirmation.q_value,
            )
        ):
            raise ValueError("confirmation RR CI, pValue, and qValue must be null")
        if self.grade == "DANGER" and (
            self.candidate.adjustment_kind != "STRATIFIED"
            or self.discovery.relative_risk_ci_lower is None
            or self.discovery.relative_risk_ci_lower <= 1.0
        ):
            raise ValueError("DANGER requires STRATIFIED discovery with RR CI lower > 1")
        try:
            display_ids = tuple(self.display_merge_rule_ids)
        except TypeError as error:
            raise TypeError("display_merge_rule_ids must be an iterable of SHA IDs") from error
        checked = tuple(
            _validate_sha256_uri(value, "display_merge_rule_ids item")
            for value in display_ids
        )
        if len(checked) != len(set(checked)):
            raise ValueError("display_merge_rule_ids must be unique")
        object.__setattr__(
            self,
            "display_merge_rule_ids",
            tuple(sorted(checked, key=lambda value: value.encode("utf-8"))),
        )

    def to_wire(self) -> dict[str, object]:
        candidate = self.candidate
        return {
            "ruleId": candidate.rule_id,
            "analysisFamily": candidate.analysis_family,
            "evidenceFamily": candidate.evidence_family,
            "firstAvailableStage": candidate.first_available_stage,
            "equipmentType": candidate.equipment_type,
            "applicationScope": candidate.application_scope,
            "equipmentId": candidate.equipment_id,
            "fieldNames": list(candidate.field_names),
            "predicate": {"allOf": [clause.to_wire() for clause in candidate.predicate]},
            "applicationContext": dict(candidate.application_context),
            "adjustmentLevel": candidate.adjustment_level,
            "adjustmentFieldsDropped": list(candidate.adjustment_fields_dropped),
            "adjustmentKind": candidate.adjustment_kind,
            "grade": self.grade,
            "earlyWarningEligible": candidate.early_warning_eligible,
            "discovery": self.discovery.to_wire(),
            "confirmation": self.confirmation.to_wire(),
            "displayMergeRuleIds": list(self.display_merge_rule_ids),
        }


@dataclass(frozen=True)
class _Rows:
    columns: Mapping[str, tuple[object, ...]]
    count: int


def _snapshot_rows(
    rows: pd.DataFrame,
    included_columns: frozenset[str] | None = None,
) -> _Rows:
    if not isinstance(rows, pd.DataFrame):
        raise TypeError("quality rows must be a pandas DataFrame")
    if not rows.columns.is_unique:
        raise ValueError("quality rows must have unique columns")
    selected = [
        column
        for column in rows.columns
        if included_columns is None or str(column) in included_columns
    ]
    snapshot = rows.loc[:, selected].copy(deep=True)
    columns = {
        str(column): tuple(snapshot[column].tolist()) for column in snapshot.columns
    }
    return _Rows(MappingProxyType(columns), len(snapshot))


def _definition_surface(
    requested: Sequence[FeatureDefinition], config: AnalysisConfig
) -> tuple[FeatureDefinition, ...]:
    expected = configured_definitions(config)
    expected_by_name = {definition.name: definition for definition in expected}
    snapshot = tuple(requested)
    names = [definition.name for definition in snapshot]
    if len(names) != len(set(names)):
        raise ValueError("feature definitions must be unique")
    if set(names) != set(expected_by_name):
        raise ValueError("feature definitions must be complete")
    if any(expected_by_name[definition.name] != definition for definition in snapshot):
        raise ValueError("feature definition does not match immutable config")
    return expected


def _field_policy(config: AnalysisConfig) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for field in config.fields:
        name = str(field["field"])
        if name in result:
            raise ValueError("configured quality fields must be unique")
        result[name] = field
    return result


def _numeric_predicates(field: str, values: tuple[object, ...], bins: int) -> tuple[PredicateClause, ...]:
    finite = [number for value in values if (number := _finite_number(value)) is not None]
    if not finite:
        return ()
    boundaries = []
    for index in range(1, bins + 1):
        boundary = type1_quantile(finite, index / bins)
        if boundary is not None and (not boundaries or boundary != boundaries[-1]):
            boundaries.append(float(boundary))
    lower = min(finite)
    predicates: list[PredicateClause] = []
    for upper in boundaries:
        predicates.append(
            PredicateClause(
                field=field,
                type="NUMERIC_INTERVAL",
                lower=float(lower),
                lower_inclusive=not predicates,
                upper=float(upper),
                upper_inclusive=True,
                values=None,
            )
        )
        lower = upper
    return tuple(predicates)


def _category_predicates(field: str, values: tuple[object, ...]) -> tuple[PredicateClause, ...]:
    levels = sorted(
        {_category(value) for value in values if _category(value) is not None},
        key=lambda value: value.encode("utf-8"),
    )
    return tuple(
        PredicateClause(field, "CATEGORY_IN", None, None, None, None, (level,))
        for level in levels
    )


def _clause_mask(rows: _Rows, clause: PredicateClause) -> tuple[bool, ...]:
    values = rows.columns.get(clause.field, (None,) * rows.count)
    if clause.type == "CATEGORY_IN":
        wanted = set(clause.values or ())
        return tuple(_category(value) in wanted for value in values)
    lower = float(clause.lower)
    upper = float(clause.upper)
    result: list[bool] = []
    for raw in values:
        value = _finite_number(raw)
        result.append(
            value is not None
            and (value >= lower if clause.lower_inclusive else value > lower)
            and (value <= upper if clause.upper_inclusive else value < upper)
        )
    return tuple(result)


def _predicate_mask(rows: _Rows, predicate: tuple[PredicateClause, ...]) -> tuple[bool, ...]:
    masks = [_clause_mask(rows, clause) for clause in predicate]
    return tuple(all(mask[index] for mask in masks) for index in range(rows.count))


def _eligible_mask(rows: _Rows, predicate: tuple[PredicateClause, ...]) -> tuple[bool, ...]:
    result = [True] * rows.count
    for clause in predicate:
        values = rows.columns.get(clause.field, (None,) * rows.count)
        for index, raw in enumerate(values):
            valid = _finite_number(raw) is not None if clause.type == "NUMERIC_INTERVAL" else _category(raw) is not None
            result[index] = result[index] and valid
    return tuple(result)


def _band_boundaries(rows: _Rows, fields: set[str]) -> dict[str, tuple[float, float, float] | None]:
    result: dict[str, tuple[float, float, float] | None] = {}
    for band in sorted(fields, key=lambda value: value.encode("utf-8")):
        base = band.removesuffix("_band")
        values = [number for raw in rows.columns.get(base, ()) if (number := _finite_number(raw)) is not None]
        quartiles = tuple(type1_quantile(values, q) for q in (0.25, 0.5, 0.75))
        result[band] = None if any(value is None for value in quartiles) else tuple(float(value) for value in quartiles)
    return result


def _context_column(rows: _Rows, field: str, bands: Mapping[str, tuple[float, float, float] | None]) -> tuple[object, ...]:
    if field.endswith("_band"):
        boundaries = bands.get(field)
        base_values = rows.columns.get(field.removesuffix("_band"), (None,) * rows.count)
        if boundaries is None:
            return (_MISSING,) * rows.count
        lower, median, upper = boundaries
        result: list[object] = []
        for raw in base_values:
            value = _finite_number(raw)
            if value is None:
                result.append(_MISSING)
            elif value <= lower:
                result.append("Q1")
            elif value <= median:
                result.append("Q2")
            elif value <= upper:
                result.append("Q3")
            else:
                result.append("Q4")
        return tuple(result)
    return tuple(_scalar(value) for value in rows.columns.get(field, (None,) * rows.count))


class _StrataCache:
    """Cache label-free context columns and canonical keys for one row snapshot."""

    def __init__(
        self,
        rows: _Rows,
        bands: Mapping[str, tuple[float, float, float] | None],
    ) -> None:
        self.rows = rows
        self.bands = bands
        self._columns: dict[str, tuple[object, ...]] = {}
        self._keys: dict[tuple[str, ...], tuple[str | None, ...]] = {}

    def keys(self, fields: tuple[str, ...]) -> tuple[str | None, ...]:
        cached = self._keys.get(fields)
        if cached is not None:
            return cached
        if not fields:
            result: tuple[str | None, ...] = ("",) * self.rows.count
            self._keys[fields] = result
            return result
        columns = []
        for field in fields:
            column = self._columns.get(field)
            if column is None:
                column = _context_column(self.rows, field, self.bands)
                self._columns[field] = column
            columns.append(column)
        encoded: dict[tuple[object, ...], str] = {}
        result_values: list[str | None] = []
        for index in range(self.rows.count):
            values = tuple(column[index] for column in columns)
            if any(value is _MISSING for value in values):
                result_values.append(None)
                continue
            key = encoded.get(values)
            if key is None:
                key = canonical_json_bytes(
                    dict(zip(fields, values, strict=True))
                )[:-1].decode("utf-8")
                encoded[values] = key
            result_values.append(key)
        result = tuple(result_values)
        self._keys[fields] = result
        return result


def _field_stage(field: str, policy: Mapping[str, Mapping[str, object]]) -> str:
    base = field.removesuffix("_band") if field.endswith("_band") else field
    if base not in policy:
        raise ValueError(f"adjustment field is not configured: {field}")
    return str(policy[base]["firstAvailableStage"])


def _dropped_fields(
    field_names: tuple[str, ...],
    first_stage: str,
    equipment_type: str,
    analysis_family: str,
    config: AnalysisConfig,
    policy: Mapping[str, Mapping[str, object]],
) -> tuple[str, ...]:
    dropped: set[str] = set(field_names)
    dropped.update(f"{field}_band" for field in field_names)
    changed = True
    while changed:
        changed = False
        for name, configured in policy.items():
            dependencies = {str(value) for value in configured["dependencies"]}
            if name in dropped or dependencies & dropped:
                before = len(dropped)
                dropped.add(name)
                dropped.update(dependencies)
                changed = changed or len(dropped) != before
    for name in field_names:
        configured = policy[name]
        stage = configured["firstAvailableStage"]
        dropped.update(
            other
            for other, other_policy in policy.items()
            if other_policy["featureRole"] == "DIRECT_OPERATION"
            and other_policy["dataType"] == "NUMBER"
            and other_policy["firstAvailableStage"] == stage
        )
    if equipment_type == "AP" and analysis_family != "INTERACTION":
        dropped.update(
            f"{name}_band"
            for name, configured in policy.items()
            if configured["equipmentType"] == equipment_type
            and configured["featureRole"] == "PRODUCT_STATE_REFERENCE"
            and configured["dataType"] == "NUMBER"
        )
    for level in config.risk_adjustment_hierarchies[equipment_type]:
        dropped.update(
            field
            for field in level
            if STAGE_RANK[_field_stage(field, policy)] > STAGE_RANK[first_stage]
        )
    return tuple(sorted(dropped, key=lambda value: value.encode("utf-8")))


def _select_adjustment(
    rows: _Rows,
    candidate_mask: tuple[bool, ...],
    eligible: tuple[bool, ...],
    equipment_type: str,
    first_stage: str,
    dropped: tuple[str, ...],
    strata_cache: _StrataCache,
    config: AnalysisConfig,
    policy: Mapping[str, Mapping[str, object]],
) -> tuple[int, tuple[str, ...], str, int, int, int, Mapping[str, float]]:
    minimum_support = int(config.quality_risk["minimumDiscoverySupport"])
    minimum_strata = int(config.quality_risk["minimumInformativeStrata"])
    hierarchy = config.risk_adjustment_hierarchies[equipment_type]
    dropped_set = set(dropped)
    for level_index, configured_level in enumerate(hierarchy):
        fields = tuple(
            field
            for field in configured_level
            if field not in dropped_set
            and STAGE_RANK[_field_stage(field, policy)] <= STAGE_RANK[first_stage]
        )
        keys = strata_cache.keys(fields)
        counts: dict[str, list[int]] = {}
        for index, key in enumerate(keys):
            if key is None or not eligible[index]:
                continue
            cell = counts.setdefault(key, [0, 0])
            cell[0 if candidate_mask[index] else 1] += 1
        informative = {
            key: value for key, value in counts.items() if value[0] > 0 and value[1] > 0
        }
        support = sum(value[0] for value in informative.values())
        comparator = sum(value[1] for value in informative.values())
        if len(informative) >= minimum_strata and support >= minimum_support and comparator >= minimum_support:
            total = sum(sum(value) for value in informative.values())
            weights = {
                key: (value[0] + value[1]) / total
                for key, value in sorted(informative.items(), key=lambda item: item[0].encode("utf-8"))
            }
            return level_index, fields, "STRATIFIED", support, comparator, len(informative), weights

    support = sum(1 for index in range(rows.count) if eligible[index] and candidate_mask[index])
    comparator = sum(1 for index in range(rows.count) if eligible[index] and not candidate_mask[index])
    weights = {"": 1.0} if support and comparator else {}
    return len(hierarchy) - 1, (), "UNADJUSTED_FALLBACK", support, comparator, int(bool(weights)), weights


def _make_candidate(
    *,
    rows: _Rows,
    analysis_family: str,
    evidence_family: str,
    field_names: tuple[str, ...],
    predicate: tuple[PredicateClause, ...],
    first_stage: str,
    equipment_type: str,
    config: AnalysisConfig,
    policy: Mapping[str, Mapping[str, object]],
    bands: Mapping[str, tuple[float, float, float] | None],
    strata_cache: _StrataCache,
) -> Candidate | None:
    candidate_mask = _predicate_mask(rows, predicate)
    support_all = sum(candidate_mask)
    if support_all == 0:
        return None
    eligible = _eligible_mask(rows, predicate)
    equipment_specific = (
        analysis_family == "CATEGORICAL"
        and policy[field_names[0]]["featureRole"] == _EQUIPMENT_IDENTIFIER_ROLE
    )
    equipment_id = str(predicate[0].values[0]) if equipment_specific else "ALL"
    dropped = _dropped_fields(
        field_names, first_stage, equipment_type, analysis_family, config, policy
    )
    level, fields, kind, support, comparator, informative, weights = _select_adjustment(
        rows,
        candidate_mask,
        eligible,
        equipment_type,
        first_stage,
        dropped,
        strata_cache,
        config,
        policy,
    )
    provisional = Candidate(
        analysis_family=analysis_family,
        evidence_family=evidence_family,
        field_names=field_names,
        predicate=predicate,
        first_available_stage=first_stage,
        equipment_type=equipment_type,
        application_scope="EQUIPMENT_SPECIFIC" if equipment_specific else "PROCESS_GLOBAL",
        equipment_id=equipment_id,
        application_context={},
        adjustment_level=level,
        adjustment_fields_dropped=dropped,
        adjustment_kind=kind,
        early_warning_eligible=first_stage != "AP_RECORDED_WITH_RESULT",
        support=support,
        comparator_support=comparator,
        informative_strata=informative,
        rule_id="sha256:" + "0" * 64,
        adjustment_fields=fields,
        discovery_weights=weights,
        band_boundaries=bands,
    )
    return replace(provisional, rule_id=sha256_uri(canonical_json_bytes(provisional.identity())))


def _generate_candidates(rows: _Rows, wanted: tuple[FeatureDefinition, ...], config: AnalysisConfig) -> tuple[Candidate, ...]:
    policy = _field_policy(config)
    if not config.fdr_families or len(config.fdr_families) != len(set(config.fdr_families)):
        raise ValueError("quality FDR families must be non-empty and unique")
    eligible_definitions = tuple(
        definition for definition in wanted if definition.field_role in _CANDIDATE_ROLES
    )
    for definition in eligible_definitions:
        evidence = policy[definition.name]["evidenceFamily"]
        if evidence not in config.evidence_families:
            raise ValueError(f"quality field has no configured evidence family: {definition.name}")

    all_band_fields = {
        field
        for hierarchy in config.risk_adjustment_hierarchies.values()
        for level in hierarchy
        for field in level
        if field.endswith("_band")
    }
    bands = _band_boundaries(rows, all_band_fields)
    strata_cache = _StrataCache(rows, bands)
    candidates: list[Candidate] = []
    definition_by_name = {definition.name: definition for definition in wanted}
    definition_rank = {definition.name: index for index, definition in enumerate(wanted)}
    interaction_rank = {pair: index for index, pair in enumerate(config.fixed_interactions)}

    for definition in eligible_definitions:
        if definition.name not in rows.columns:
            continue
        if definition.data_type == "NUMBER":
            predicates = _numeric_predicates(
                definition.name,
                rows.columns[definition.name],
                int(config.quality_risk["numericBins"]),
            )
            family = "NUMERIC"
        elif definition.data_type == "STRING":
            predicates = _category_predicates(definition.name, rows.columns[definition.name])
            family = "CATEGORICAL"
        else:
            raise ValueError(f"candidate data type is not supported: {definition.data_type}")
        for clause in predicates:
            candidate = _make_candidate(
                rows=rows,
                analysis_family=family,
                evidence_family=str(policy[definition.name]["evidenceFamily"]),
                field_names=(definition.name,),
                predicate=(clause,),
                first_stage=definition.first_stage,
                equipment_type=definition.equipment_type,
                config=config,
                policy=policy,
                bands=bands,
                strata_cache=strata_cache,
            )
            if candidate is not None:
                candidates.append(candidate)

    for pair in config.fixed_interactions:
        first_name, second_name = pair
        first = definition_by_name.get(first_name)
        second = definition_by_name.get(second_name)
        if first is None or second is None:
            raise ValueError(f"configured interaction field is missing: {pair}")
        if first.data_type != "NUMBER" or second.data_type != "NUMBER":
            raise ValueError(f"configured interaction fields must be numeric: {pair}")
        if first.equipment_type != second.equipment_type:
            raise ValueError(f"configured interaction must remain within one equipment type: {pair}")
        if first_name not in rows.columns or second_name not in rows.columns:
            continue
        first_predicates = _numeric_predicates(first_name, rows.columns[first_name], int(config.quality_risk["interactionBins"]))
        second_predicates = _numeric_predicates(second_name, rows.columns[second_name], int(config.quality_risk["interactionBins"]))
        if len(first_predicates) < 2 or len(second_predicates) < 2:
            continue
        later = first if STAGE_RANK[first.first_stage] >= STAGE_RANK[second.first_stage] else second
        for first_clause in first_predicates:
            for second_clause in second_predicates:
                candidate = _make_candidate(
                    rows=rows,
                    analysis_family="INTERACTION",
                    evidence_family=str(policy[first_name]["evidenceFamily"]),
                    field_names=(first_name, second_name),
                    predicate=(first_clause, second_clause),
                    first_stage=later.first_stage,
                    equipment_type=later.equipment_type,
                    config=config,
                    policy=policy,
                    bands=bands,
                    strata_cache=strata_cache,
                )
                if candidate is not None:
                    candidates.append(candidate)

    family_rank = {name: index for index, name in enumerate(config.fdr_families)}
    missing_families = {
        candidate.analysis_family for candidate in candidates
    } - set(family_rank)
    if missing_families:
        raise ValueError(
            "candidate analysis families are missing from configured FDR families: "
            + ", ".join(sorted(missing_families))
        )

    def predicate_sort_key(candidate: Candidate) -> tuple[object, ...]:
        terms: list[object] = []
        for clause in candidate.predicate:
            if clause.type == "NUMERIC_INTERVAL":
                terms.append(
                    (0, float(clause.lower), not bool(clause.lower_inclusive), float(clause.upper))
                )
            else:
                terms.append(
                    (1, tuple(str(value).encode("utf-8") for value in clause.values or ()))
                )
        return tuple(terms)

    candidates.sort(
        key=lambda candidate: (
            family_rank[candidate.analysis_family],
            interaction_rank.get(candidate.field_names, definition_rank.get(candidate.field_names[0], 0)),
            predicate_sort_key(candidate),
            candidate.rule_id.encode("utf-8"),
        )
    )
    seen_predicates: set[tuple[str, bytes]] = set()
    preimages: dict[str, bytes] = {}
    unique: list[Candidate] = []
    for candidate in candidates:
        predicate_key = (candidate.analysis_family, candidate.canonical_predicate)
        if predicate_key in seen_predicates:
            continue
        seen_predicates.add(predicate_key)
        preimage = canonical_json_bytes(candidate.identity())
        previous = preimages.setdefault(candidate.rule_id, preimage)
        if previous != preimage:
            raise ValueError("quality rule ID collision")
        unique.append(candidate)
    return tuple(unique)


def generate_candidates(
    discovery_rows: pd.DataFrame,
    definitions: Sequence[FeatureDefinition],
    config: AnalysisConfig,
) -> tuple[Candidate, ...]:
    """Generate all unique non-empty predicates without reading labels."""
    wanted = _definition_surface(definitions, config)
    feature_columns = frozenset(definition.name for definition in wanted)
    return _generate_candidates(
        _snapshot_rows(discovery_rows, feature_columns), wanted, config
    )


def _judges(rows: _Rows) -> tuple[bool, ...]:
    raw = rows.columns.get("judge")
    if raw is None:
        raise ValueError("quality metric rows must contain judge")
    result: list[bool] = []
    for value in raw:
        if value not in {"양품", "불량"}:
            raise ValueError("quality metric judge must be 양품 or 불량")
        result.append(value == "불량")
    return tuple(result)


def _metric(
    rows: _Rows,
    candidate: Candidate,
    config: AnalysisConfig,
    *,
    confirmation: bool,
    strata_cache: _StrataCache | None = None,
) -> QualityMetric:
    mask = _predicate_mask(rows, candidate.predicate)
    eligible = _eligible_mask(rows, candidate.predicate)
    cache = strata_cache or _StrataCache(rows, candidate.band_boundaries)
    keys = cache.keys(candidate.adjustment_fields)
    allowed_keys = set(candidate.discovery_weights)
    judges = _judges(rows)
    counts: dict[str, list[int]] = {}
    for index, key in enumerate(keys):
        if key is None or not eligible[index] or key not in allowed_keys:
            continue
        cells = counts.setdefault(key, [0, 0, 0, 0])
        cell = 0 if mask[index] and judges[index] else 1 if mask[index] else 2 if judges[index] else 3
        cells[cell] += 1
    informative = {
        key: cells
        for key, cells in counts.items()
        if cells[0] + cells[1] > 0 and cells[2] + cells[3] > 0
    }
    strata = tuple(
        Stratum(*informative[key], key=key)
        for key in sorted(informative, key=lambda value: value.encode("utf-8"))
    )
    support = sum(stratum.a + stratum.b for stratum in strata)
    comparator_support = sum(stratum.c + stratum.d for stratum in strata)
    defects = sum(stratum.a for stratum in strata)
    crude = defects / support if support else None
    wilson = wilson_interval(defects, support, config.wilson_z)
    minimum_support = int(
        config.quality_risk[
            "minimumConfirmationSupport" if confirmation else "minimumDiscoverySupport"
        ]
    )
    minimum_defects = int(
        config.quality_risk[
            "minimumConfirmationDefects" if confirmation else "minimumCautionDefects"
        ]
    )
    required_strata = (
        int(config.quality_risk["minimumInformativeStrata"])
        if candidate.adjustment_kind == "STRATIFIED"
        else 1
    )
    low_structure = len(strata) < required_strata
    low_support = support < minimum_support or comparator_support < minimum_support
    if low_structure or low_support:
        reason = "NO_INFORMATIVE_STRATA" if low_structure else "LOW_SUPPORT"
        return QualityMetric(
            support,
            defects,
            crude,
            None if wilson is None else wilson[0],
            None if wilson is None else wilson[1],
            None,
            None,
            None,
            None,
            None,
            None,
            None if confirmation else 1.0,
            None,
            reason,
        )
    rates = standardized_rates(strata, candidate.discovery_weights)
    relative_risk = mantel_haenszel_rr(strata)
    p_value, p_reason = cmh_p_value(strata)
    reason = "LOW_DEFECT_COUNT" if defects < minimum_defects else p_reason
    return QualityMetric(
        support,
        defects,
        crude,
        None if wilson is None else wilson[0],
        None if wilson is None else wilson[1],
        rates.candidate,
        rates.comparator,
        rates.risk_difference,
        relative_risk,
        None,
        None,
        None if confirmation else p_value,
        None,
        reason,
    )


def _validate_criteria_id(value: object) -> str:
    return _validate_sha256_uri(value, "criteria_id")


def _metric_is_insufficient(metric: QualityMetric) -> bool:
    return metric.reason_code in {
        "LOW_SUPPORT",
        "LOW_DEFECT_COUNT",
        "NO_INFORMATIVE_STRATA",
        "NO_VARIATION",
        "NON_FINITE_ESTIMATE",
        "ZERO_COMPARATOR_RISK",
    }


def _discovery_caution_pass(metric: QualityMetric, config: AnalysisConfig) -> bool:
    policy = config.quality_risk
    return (
        not _metric_is_insufficient(metric)
        and metric.relative_risk is not None
        and metric.risk_difference is not None
        and metric.q_value is not None
        and metric.relative_risk >= float(policy["relativeRisk"]["caution"])
        and metric.risk_difference >= float(policy["riskDifference"]["caution"])
        and metric.q_value <= float(policy["bhQ"]["caution"])
    )


def _discovery_danger_prebootstrap(metric: QualityMetric, config: AnalysisConfig) -> bool:
    policy = config.quality_risk
    return (
        _discovery_caution_pass(metric, config)
        and metric.defects >= int(policy["minimumDangerDefects"])
        and metric.relative_risk is not None
        and metric.risk_difference is not None
        and metric.q_value is not None
        and metric.relative_risk >= float(policy["relativeRisk"]["danger"])
        and metric.risk_difference >= float(policy["riskDifference"]["danger"])
        and metric.q_value <= float(policy["bhQ"]["danger"])
    )


def _bootstrap_applicable(metric: QualityMetric, config: AnalysisConfig) -> bool:
    return (
        metric.support >= int(config.quality_risk["minimumDiscoverySupport"])
        and metric.defects >= int(config.quality_risk["minimumCautionDefects"])
        and metric.adjusted_rate is not None
        and metric.comparator_adjusted_rate is not None
        and metric.relative_risk is not None
        and math.isfinite(metric.relative_risk)
        and metric.relative_risk > 0.0
    )


def _confirmation_caution_pass(metric: QualityMetric, config: AnalysisConfig) -> bool:
    policy = config.quality_risk
    return (
        not _metric_is_insufficient(metric)
        and metric.relative_risk is not None
        and metric.risk_difference is not None
        and metric.relative_risk
        > float(policy["confirmationRelativeRisk"]["cautionExclusive"])
        and metric.risk_difference > 0.0
    )


def _confirmation_danger_pass(metric: QualityMetric, config: AnalysisConfig) -> bool:
    return (
        _confirmation_caution_pass(metric, config)
        and metric.relative_risk is not None
        and metric.relative_risk
        >= float(config.quality_risk["confirmationRelativeRisk"]["danger"])
    )


def _bootstrap_rows(
    rows: _Rows,
    candidate: Candidate,
    strata_cache: _StrataCache | None = None,
) -> pd.DataFrame:
    charge_ids = rows.columns.get("charge_id")
    if charge_ids is None:
        raise ValueError("quality bootstrap rows must contain charge_id")
    mask = _predicate_mask(rows, candidate.predicate)
    eligible = _eligible_mask(rows, candidate.predicate)
    cache = strata_cache or _StrataCache(rows, candidate.band_boundaries)
    keys = cache.keys(candidate.adjustment_fields)
    allowed = set(candidate.discovery_weights)
    judges = rows.columns.get("judge")
    if judges is None:
        raise ValueError("quality bootstrap rows must contain judge")
    records: list[dict[str, object]] = []
    for index, key in enumerate(keys):
        if key is None or key not in allowed or not eligible[index]:
            continue
        charge_id = charge_ids[index]
        if type(charge_id) is not str or not charge_id:
            raise ValueError("quality bootstrap charge_id must be a non-empty string")
        judge = judges[index]
        if judge not in {"양품", "불량"}:
            raise ValueError("quality bootstrap judge must be 양품 or 불량")
        records.append(
            {
                "charge_id": charge_id,
                "stratum": key,
                "candidate": bool(mask[index]),
                "judge": judge,
            }
        )
    return pd.DataFrame(
        records,
        columns=("charge_id", "stratum", "candidate", "judge"),
    )


def _bootstrap_task(
    task: tuple[int, pd.DataFrame, str, str, int],
) -> tuple[int, BootstrapCi]:
    index, rows, criteria_id, rule_id, replicates = task
    return index, charge_bootstrap_rr_ci(
        rows,
        criteria_id,
        rule_id,
        replicates=replicates,
    )


def _write_all(file_descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        offset += os.write(file_descriptor, payload[offset:])


def _parallel_bootstrap_tasks(
    tasks: list[tuple[int, pd.DataFrame, str, str, int]],
    worker_count: int,
) -> tuple[tuple[int, BootstrapCi], ...]:
    partitions = [tasks[index::worker_count] for index in range(worker_count)]
    children: list[tuple[int, int]] = []
    for partition in partitions:
        read_descriptor, write_descriptor = os.pipe()
        process_id = os.fork()
        if process_id == 0:
            os.close(read_descriptor)
            for _, inherited_read_descriptor in children:
                os.close(inherited_read_descriptor)
            try:
                payload = pickle.dumps(
                    ("OK", tuple(_bootstrap_task(task) for task in partition)),
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            except BaseException as error:
                payload = pickle.dumps(
                    ("ERROR", type(error).__name__, str(error)),
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            try:
                _write_all(write_descriptor, payload)
            finally:
                os.close(write_descriptor)
            os._exit(0)
        os.close(write_descriptor)
        children.append((process_id, read_descriptor))

    results: list[tuple[int, BootstrapCi]] = []
    errors: list[str] = []
    for process_id, read_descriptor in children:
        chunks: list[bytes] = []
        while chunk := os.read(read_descriptor, 65536):
            chunks.append(chunk)
        os.close(read_descriptor)
        _, status = os.waitpid(process_id, 0)
        if status != 0 or not chunks:
            errors.append(f"bootstrap worker {process_id} exited with status {status}")
            continue
        response = pickle.loads(b"".join(chunks))
        if response[0] == "ERROR":
            errors.append(f"bootstrap worker {response[1]}: {response[2]}")
        else:
            results.extend(response[1])
    if errors:
        raise RuntimeError("; ".join(errors))
    return tuple(sorted(results, key=lambda item: item[0]))


def _bootstrap_discovery_metrics(
    rows: _Rows,
    candidates: tuple[Candidate, ...],
    discovery: list[QualityMetric],
    strata_cache: _StrataCache,
    criteria_id: str,
    config: AnalysisConfig,
) -> list[QualityMetric]:
    replicates = int(config.bootstrap["replicates"])
    tasks = [
        (
            index,
            _bootstrap_rows(rows, candidate, strata_cache),
            criteria_id,
            candidate.rule_id,
            replicates,
        )
        for index, candidate in enumerate(candidates)
        if _bootstrap_applicable(discovery[index], config)
    ]
    worker_count = min(
        len(tasks),
        _MAX_BOOTSTRAP_WORKERS,
        os.cpu_count() or 1,
    )
    if len(tasks) >= _PARALLEL_BOOTSTRAP_MINIMUM and worker_count > 1:
        intervals = _parallel_bootstrap_tasks(tasks, worker_count)
    else:
        intervals = tuple(_bootstrap_task(task) for task in tasks)

    result = list(discovery)
    for index, interval in intervals:
        metric = result[index]
        result[index] = replace(
            metric,
            relative_risk_ci_lower=interval.lower,
            relative_risk_ci_upper=interval.upper,
            reason_code=(
                interval.reason_code
                if metric.reason_code == "NONE"
                else metric.reason_code
            ),
        )
    return result


def _grade(
    candidate: Candidate,
    discovery: QualityMetric,
    confirmation: QualityMetric,
    config: AnalysisConfig,
) -> tuple[str, QualityMetric]:
    if _metric_is_insufficient(discovery) or _metric_is_insufficient(confirmation):
        return "INSUFFICIENT_EVIDENCE", confirmation
    caution = _discovery_caution_pass(discovery, config)
    danger = (
        _discovery_danger_prebootstrap(discovery, config)
        and discovery.relative_risk_ci_lower is not None
        and discovery.relative_risk_ci_lower > 1.0
    )
    confirmation_caution = _confirmation_caution_pass(confirmation, config)
    confirmation_danger = _confirmation_danger_pass(confirmation, config)
    if (
        danger
        and confirmation_danger
        and candidate.adjustment_kind == "STRATIFIED"
    ):
        return "DANGER", confirmation
    if (caution or danger) and confirmation_caution:
        return "CAUTION", confirmation
    if caution or danger:
        return "UNCONFIRMED", replace(
            confirmation, reason_code="DIRECTION_NOT_REPEATED"
        )
    return "NORMAL", confirmation


def _annotate_display_merges(rules: list[QualityRule]) -> list[QualityRule]:
    grouped: dict[tuple[object, ...], list[int]] = {}
    for index, rule in enumerate(rules):
        candidate = rule.candidate
        if candidate.analysis_family != "NUMERIC":
            continue
        grouped.setdefault(
            (
                candidate.field_names,
                candidate.first_available_stage,
                candidate.equipment_type,
                candidate.application_scope,
                candidate.equipment_id,
                canonical_json_bytes(dict(candidate.application_context)),
                candidate.adjustment_level,
                candidate.adjustment_fields_dropped,
                candidate.adjustment_kind,
                rule.grade,
            ),
            [],
        ).append(index)
    result = list(rules)
    for indexes in grouped.values():
        indexes.sort(
            key=lambda index: (
                float(result[index].candidate.predicate[0].lower),
                float(result[index].candidate.predicate[0].upper),
            )
        )
        run: list[int] = []
        for index in indexes:
            if not run:
                run = [index]
                continue
            previous = result[run[-1]].candidate.predicate[0]
            current = result[index].candidate.predicate[0]
            if (
                previous.upper == current.lower
                and previous.upper_inclusive is True
                and current.lower_inclusive is False
            ):
                run.append(index)
                continue
            if len(run) >= 2:
                merge_ids = tuple(
                    sorted(
                        (result[item].candidate.rule_id for item in run),
                        key=lambda value: value.encode("utf-8"),
                    )
                )
                for item in run:
                    result[item] = replace(
                        result[item], display_merge_rule_ids=merge_ids
                    )
            run = [index]
        if len(run) >= 2:
            merge_ids = tuple(
                sorted(
                    (result[item].candidate.rule_id for item in run),
                    key=lambda value: value.encode("utf-8"),
                )
            )
            for item in run:
                result[item] = replace(result[item], display_merge_rule_ids=merge_ids)
    return result


def build_quality_rules(
    split: TimeSplitResult,
    definitions: Sequence[FeatureDefinition],
    config: AnalysisConfig,
    criteria_id: str,
) -> list[dict[str, object]]:
    """Build deterministic atomic discovery/confirmation quality-risk rules."""
    _validate_criteria_id(criteria_id)
    wanted = _definition_surface(definitions, config)
    discovery_rows = _snapshot_rows(split.discovery_rows)
    confirmation_rows = _snapshot_rows(split.confirmation_rows)
    candidates = _generate_candidates(discovery_rows, wanted, config)
    common_bands = candidates[0].band_boundaries if candidates else {}
    discovery_cache = _StrataCache(discovery_rows, common_bands)
    confirmation_cache = _StrataCache(confirmation_rows, common_bands)
    discovery = [
        _metric(
            discovery_rows,
            candidate,
            config,
            confirmation=False,
            strata_cache=discovery_cache,
        )
        for candidate in candidates
    ]
    for family in config.fdr_families:
        indexes = [index for index, candidate in enumerate(candidates) if candidate.analysis_family == family]
        adjusted = benjamini_hochberg(
            [1.0 if discovery[index].p_value is None else discovery[index].p_value for index in indexes]
        )
        for index, q_value in zip(indexes, adjusted, strict=True):
            discovery[index] = replace(discovery[index], q_value=q_value)
    discovery = _bootstrap_discovery_metrics(
        discovery_rows,
        candidates,
        discovery,
        discovery_cache,
        criteria_id,
        config,
    )

    rules: list[QualityRule] = []
    for index, candidate in enumerate(candidates):
        confirmation = _metric(
            confirmation_rows,
            candidate,
            config,
            confirmation=True,
            strata_cache=confirmation_cache,
        )
        grade, confirmation = _grade(
            candidate, discovery[index], confirmation, config
        )
        rules.append(
            QualityRule(candidate, discovery[index], confirmation, grade)
        )
    rules = _annotate_display_merges(rules)
    return [rule.to_wire() for rule in rules]
