"""Leak-free historical operating ranges from the fixed reference population."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from numbers import Integral, Real

import pandas as pd

from equipment_quality.deterministic import (
    canonical_json_bytes,
    sha256_uri,
    type1_quantile,
)
from equipment_quality.feature_roles import (
    STAGE_RANK,
    FeatureDefinition,
    definitions as configured_definitions,
)
from equipment_quality.models import AnalysisConfig, TimeSplitResult


_RANGE_ROLES = {"DIRECT_OPERATION", "PRODUCT_STATE_REFERENCE"}
_EQUIPMENT_RANK = {"SM_CC": 0, "FURNACE": 1, "RM4": 2, "AP": 3}
_BAND_QUANTILES = (0.25, 0.50, 0.75)
_MISSING = object()


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    missing = pd.isna(value)
    return bool(missing) if isinstance(missing, (bool, type(pd.NA))) else False


def _finite_numeric(value: object) -> float | None:
    if _is_missing(value):
        return None
    if isinstance(value, bool) or getattr(getattr(value, "dtype", None), "kind", None) == "b":
        return None
    if not isinstance(value, Real):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _validated_date(value: object, label: str) -> date | None:
    if _is_missing(value):
        return None
    if isinstance(value, pd.Timestamp):
        value = value.date()
    elif isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        raise ValueError(f"{label} must be a validated date")
    return value


def _available_by(
    columns: Mapping[str, list[object]],
    index: int,
    date_column: str,
    as_of: date,
) -> bool:
    if date_column not in columns:
        return False
    event_date = _validated_date(columns[date_column][index], date_column)
    return event_date is not None and event_date <= as_of


def _field_map(config: AnalysisConfig) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for field in config.fields:
        name = str(field["field"])
        if name in result:
            raise ValueError(f"configured field names must be unique: {name}")
        result[name] = field
    return result


def _validated_policy(config: AnalysisConfig) -> tuple[int, int, tuple[float, ...]]:
    policy = config.operating_ranges
    if policy["quantileMethod"] != "INVERTED_CDF_TYPE_1":
        raise ValueError("operating range quantile method must be INVERTED_CDF_TYPE_1")
    minimum_support = policy["minimumSupport"]
    extreme_support = policy["extremeTailMinimumSupport"]
    if (
        isinstance(minimum_support, bool)
        or not isinstance(minimum_support, Integral)
        or int(minimum_support) < 1
    ):
        raise ValueError("operating range minimum support must be a positive integer")
    if (
        isinstance(extreme_support, bool)
        or not isinstance(extreme_support, Integral)
        or int(extreme_support) < int(minimum_support)
    ):
        raise ValueError(
            "operating range extreme support must be an integer at least minimum support"
        )
    quantiles = tuple(
        float(policy[name])
        for name in (
            "extremeLowerQuantile",
            "typicalLowerQuantile",
            "typicalUpperQuantile",
            "extremeUpperQuantile",
        )
    )
    if not (
        0 < quantiles[0] <= quantiles[1] <= quantiles[2] <= quantiles[3] <= 1
    ):
        raise ValueError("operating range quantiles must be ordered within (0, 1]")
    return int(minimum_support), int(extreme_support), quantiles


def _validated_definition_subset(
    requested: Sequence[FeatureDefinition], config: AnalysisConfig
) -> tuple[FeatureDefinition, ...]:
    expected = {item.name: item for item in configured_definitions(config)}
    result: list[FeatureDefinition] = []
    seen: set[str] = set()
    for definition in tuple(requested):
        if definition.name in seen:
            raise ValueError(f"feature definitions must be unique: {definition.name}")
        seen.add(definition.name)
        if expected.get(definition.name) != definition:
            raise ValueError(
                f"feature definition does not match immutable config: {definition.name}"
            )
        result.append(definition)
    return tuple(result)


def _band_base(band_field: str) -> str:
    if not band_field.endswith("_band"):
        raise ValueError(f"derived context is not a dimension band: {band_field}")
    return band_field.removesuffix("_band")


def _fixed_band_boundaries(
    columns: Mapping[str, list[object]],
    row_count: int,
    band_fields: set[str],
    all_definitions: Mapping[str, FeatureDefinition],
    configured_fields: Mapping[str, Mapping[str, object]],
    as_of: date,
) -> dict[str, tuple[float, float, float] | None]:
    result: dict[str, tuple[float, float, float] | None] = {}
    for band_field in sorted(band_fields, key=lambda value: value.encode("utf-8")):
        base = _band_base(band_field)
        base_definition = all_definitions.get(base)
        field = configured_fields.get(base)
        if base_definition is None or field is None or field["dataType"] != "NUMBER":
            raise ValueError(f"dimension band has no configured numeric base: {band_field}")
        values: list[float] = []
        if base in columns and base_definition.event_date_column in columns:
            for index in range(row_count):
                if not _available_by(
                    columns, index, base_definition.event_date_column, as_of
                ):
                    continue
                value = _finite_numeric(columns[base][index])
                if value is not None:
                    values.append(value)
        boundaries = tuple(type1_quantile(values, q) for q in _BAND_QUANTILES)
        result[band_field] = (
            None
            if any(boundary is None for boundary in boundaries)
            else (float(boundaries[0]), float(boundaries[1]), float(boundaries[2]))
        )
    return result


def _band(value: float, boundaries: tuple[float, float, float]) -> str:
    lower, median, upper = boundaries
    if value <= lower:
        return "Q1"
    if value <= median:
        return "Q2"
    if value <= upper:
        return "Q3"
    return "Q4"


def _scalar(value: object) -> str | int | float | bool | object:
    if _is_missing(value):
        return _MISSING
    if isinstance(value, str):
        return str(value)
    if isinstance(value, bool) or getattr(getattr(value, "dtype", None), "kind", None) == "b":
        return bool(value)
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        return number if math.isfinite(number) else _MISSING
    return _MISSING


def _context_value(
    columns: Mapping[str, list[object]],
    index: int,
    field: str,
    *,
    as_of: date,
    all_definitions: Mapping[str, FeatureDefinition],
    bands: Mapping[str, tuple[float, float, float] | None],
) -> str | int | float | bool | object:
    if field in bands:
        boundaries = bands[field]
        if boundaries is None:
            return _MISSING
        base = _band_base(field)
        definition = all_definitions[base]
        if base not in columns or not _available_by(
            columns, index, definition.event_date_column, as_of
        ):
            return _MISSING
        value = _finite_numeric(columns[base][index])
        return _MISSING if value is None else _band(value, boundaries)
    definition = all_definitions.get(field)
    if definition is None:
        raise ValueError(f"context field is not a config-derived definition: {field}")
    if field not in columns or not _available_by(
        columns, index, definition.event_date_column, as_of
    ):
        return _MISSING
    return _scalar(columns[field][index])


def _equipment_id(
    columns: Mapping[str, list[object]],
    index: int,
    definition: FeatureDefinition,
) -> str | None:
    if definition.equipment_id_column is None:
        return "RM4_PROCESS"
    column = definition.equipment_id_column
    if column not in columns:
        return None
    value = _scalar(columns[column][index])
    if value is _MISSING:
        return None
    equipment_id = str(value)
    return equipment_id if equipment_id else None


def _context_sort_bytes(context: Mapping[str, object]) -> bytes:
    return canonical_json_bytes(context)


def _range_record(
    *,
    definition: FeatureDefinition,
    equipment_id: str,
    context_level: int,
    context: dict[str, object],
    values: Sequence[float],
    extreme_support: int,
    quantiles: tuple[float, ...],
) -> dict[str, object]:
    extreme_lower, typical_lower, typical_upper, extreme_upper = quantiles
    support = len(values)
    median = type1_quantile(values, 0.50)
    p05 = type1_quantile(values, typical_lower)
    p95 = type1_quantile(values, typical_upper)
    if median is None or p05 is None or p95 is None:
        raise ValueError("supported operating range unexpectedly has no quantiles")
    if support >= extreme_support:
        p01 = type1_quantile(values, extreme_lower)
        p99 = type1_quantile(values, extreme_upper)
        if p01 is None or p99 is None:
            raise ValueError("extreme-supported range unexpectedly has no quantiles")
        lower_enabled = p01 != p05
        upper_enabled = p95 != p99
    else:
        p01 = None
        p99 = None
        lower_enabled = False
        upper_enabled = False
    identity = {
        "field": definition.name,
        "fieldRole": definition.field_role,
        "firstAvailableStage": definition.first_stage,
        "equipmentType": definition.equipment_type,
        "equipmentId": equipment_id,
        "contextLevel": context_level,
        "context": context,
        "support": support,
        "median": float(median),
        "p01": None if p01 is None else float(p01),
        "p05": float(p05),
        "p95": float(p95),
        "p99": None if p99 is None else float(p99),
        "lowerTailEnabled": lower_enabled,
        "upperTailEnabled": upper_enabled,
    }
    return {"ruleId": sha256_uri(canonical_json_bytes(identity)), **identity}


def _records_for_feature(
    columns: Mapping[str, list[object]],
    row_count: int,
    definition: FeatureDefinition,
    *,
    as_of: date,
    all_definitions: Mapping[str, FeatureDefinition],
    bands: Mapping[str, tuple[float, float, float] | None],
    minimum_support: int,
    extreme_support: int,
    quantiles: tuple[float, ...],
) -> list[dict[str, object]]:
    if definition.name not in columns or definition.event_date_column not in columns:
        return []
    context_fields = {
        field for level in definition.context_hierarchy for field in level
    }
    prepared: list[tuple[float, str, dict[str, object]]] = []
    for index in range(row_count):
        if not _available_by(columns, index, definition.event_date_column, as_of):
            continue
        value = _finite_numeric(columns[definition.name][index])
        equipment_id = _equipment_id(columns, index, definition)
        if value is None or equipment_id is None:
            continue
        context = {
            field: _context_value(
                columns,
                index,
                field,
                as_of=as_of,
                all_definitions=all_definitions,
                bands=bands,
            )
            for field in context_fields
        }
        prepared.append((value, equipment_id, context))
    if not prepared:
        return []

    groups_by_level: list[dict[tuple[str, tuple[object, ...]], list[float]]] = []
    for level in definition.context_hierarchy:
        groups: dict[tuple[str, tuple[object, ...]], list[float]] = {}
        for value, equipment_id, context in prepared:
            context_values = tuple(context[field] for field in level)
            if any(item is _MISSING for item in context_values):
                continue
            groups.setdefault((equipment_id, context_values), []).append(value)
        groups_by_level.append(groups)

    selected: set[tuple[int, str, tuple[object, ...]]] = set()
    for _, equipment_id, context in prepared:
        for level_index, level in enumerate(definition.context_hierarchy):
            context_values = tuple(context[field] for field in level)
            if any(item is _MISSING for item in context_values):
                continue
            values = groups_by_level[level_index].get((equipment_id, context_values), ())
            if len(values) >= minimum_support:
                selected.add((level_index, equipment_id, context_values))
                break

    records: list[dict[str, object]] = []
    for level_index, equipment_id, context_values in selected:
        level = definition.context_hierarchy[level_index]
        context = dict(zip(level, context_values, strict=True))
        values = groups_by_level[level_index][(equipment_id, context_values)]
        records.append(
            _range_record(
                definition=definition,
                equipment_id=equipment_id,
                context_level=level_index,
                context=context,
                values=values,
                extreme_support=extreme_support,
                quantiles=quantiles,
            )
        )
    return records


def build_operating_ranges(
    split: TimeSplitResult,
    definitions: Sequence[FeatureDefinition],
    config: AnalysisConfig,
) -> list[dict[str, object]]:
    """Build deterministic numeric ranges from reference rows available by ``as_of``."""
    minimum_support, extreme_support, quantiles = _validated_policy(config)
    requested = _validated_definition_subset(definitions, config)
    configured_fields = _field_map(config)
    all_definitions = {
        item.name: item for item in configured_definitions(config)
    }
    numeric = tuple(
        definition
        for definition in requested
        if definition.field_role in _RANGE_ROLES
        and configured_fields[definition.name]["dataType"] == "NUMBER"
    )
    if not numeric or split.reference_rows.empty:
        return []

    reference_columns = {
        str(column): split.reference_rows[column].tolist()
        for column in split.reference_rows.columns
    }
    reference_row_count = len(split.reference_rows)

    band_fields = {
        field
        for definition in numeric
        for level in definition.context_hierarchy
        for field in level
        if field.endswith("_band")
    }
    bands = _fixed_band_boundaries(
        reference_columns,
        reference_row_count,
        band_fields,
        all_definitions,
        configured_fields,
        split.as_of,
    )
    records: list[dict[str, object]] = []
    for definition in numeric:
        records.extend(
            _records_for_feature(
                reference_columns,
                reference_row_count,
                definition,
                as_of=split.as_of,
                all_definitions=all_definitions,
                bands=bands,
                minimum_support=minimum_support,
                extreme_support=extreme_support,
                quantiles=quantiles,
            )
        )
    records.sort(
        key=lambda record: (
            STAGE_RANK[str(record["firstAvailableStage"])],
            _EQUIPMENT_RANK[str(record["equipmentType"])],
            str(record["field"]).encode("utf-8"),
            str(record["equipmentId"]).encode("utf-8"),
            int(record["contextLevel"]),
            _context_sort_bytes(record["context"]),
            str(record["ruleId"]).encode("utf-8"),
        )
    )
    preimage_by_rule_id: dict[str, bytes] = {}
    for record in records:
        rule_id = str(record["ruleId"])
        preimage = canonical_json_bytes(
            {key: value for key, value in record.items() if key != "ruleId"}
        )
        previous = preimage_by_rule_id.setdefault(rule_id, preimage)
        if previous != preimage:
            raise ValueError("operating range rule ID collision")
    return records
