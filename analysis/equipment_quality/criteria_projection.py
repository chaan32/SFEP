"""Build the exact T-safe criteria identity projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
import math
from numbers import Integral, Real

import pandas as pd

from equipment_quality.deterministic import canonical_json_bytes, type1_quantile
from equipment_quality.feature_roles import FeatureDefinition, STAGE_RANK
from equipment_quality.models import TimeSplitResult


_RANGE_ROLES = {"DIRECT_OPERATION", "PRODUCT_STATE_REFERENCE"}
_QUALITY_ROLES = {
    "DIRECT_OPERATION",
    "PRODUCT_STATE_REFERENCE",
    "CONTEXT",
    "EQUIPMENT_IDENTIFIER",
}
_BAND_QUANTILES = (0.25, 0.5, 0.75)


def _missing(value: object) -> bool:
    marker = pd.isna(value)
    return bool(marker) if isinstance(marker, bool) else False


def _date_value(value: object, label: str) -> date | None:
    if _missing(value):
        return None
    if type(value) is date:
        return value
    if type(value) is datetime:
        return value.date()
    raise TypeError(f"{label} must be an exact date")


def _wire_scalar(value: object) -> object:
    if _missing(value):
        return None
    if type(value) in {str, bool, int, float}:
        if type(value) is float and not math.isfinite(value):
            return None
        return value
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, Integral) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, Real) and not isinstance(value, bool):
        number = float(value)
        return number if math.isfinite(number) else None
    raise TypeError(f"projection value has unsupported type: {type(value).__name__}")


def _finite_number(value: object) -> float | None:
    if _missing(value) or isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _definitions_snapshot(
    definitions: Sequence[FeatureDefinition],
) -> tuple[FeatureDefinition, ...]:
    if type(definitions) is str:
        raise TypeError("definitions must be a sequence")
    try:
        snapshot = tuple(definitions)
    except TypeError as error:
        raise TypeError("definitions must be a sequence") from error
    if any(type(item) is not FeatureDefinition for item in snapshot):
        raise TypeError("definitions must contain exact FeatureDefinition values")
    names = [item.name for item in snapshot]
    if len(names) != len(set(names)):
        raise ValueError("definition names must be unique")
    for item in snapshot:
        if item.first_stage not in STAGE_RANK:
            raise ValueError(f"unknown supplied stage: {item.first_stage}")
    return snapshot


def _columns(frame: pd.DataFrame) -> dict[str, list[object]]:
    if not isinstance(frame, pd.DataFrame) or not frame.columns.is_unique:
        raise TypeError("projection split rows must be a DataFrame with unique columns")
    return {str(column): frame[column].tolist() for column in frame.columns}


def _band_base(field: str) -> str:
    if not field.endswith("_band"):
        raise ValueError(f"context band must end in _band: {field}")
    return field.removesuffix("_band")


def _band(value: float, boundaries: tuple[float, float, float]) -> str:
    if value <= boundaries[0]:
        return "Q1"
    if value <= boundaries[1]:
        return "Q2"
    if value <= boundaries[2]:
        return "Q3"
    return "Q4"


def _band_boundaries(
    reference: pd.DataFrame,
    definitions: tuple[FeatureDefinition, ...],
    as_of: date,
) -> dict[str, tuple[float, float, float] | None]:
    columns = _columns(reference)
    by_name = {item.name: item for item in definitions}
    band_fields = {
        field
        for item in definitions
        for level in item.context_hierarchy
        for field in level
        if field.endswith("_band")
    }
    result: dict[str, tuple[float, float, float] | None] = {}
    for field in sorted(band_fields, key=lambda value: value.encode("utf-8")):
        base = _band_base(field)
        definition = by_name.get(base)
        if definition is None or definition.data_type != "NUMBER":
            raise ValueError(f"context band has no supplied numeric definition: {field}")
        values: list[float] = []
        for index in range(len(reference)):
            if base not in columns or definition.event_date_column not in columns:
                continue
            arrived = _date_value(
                columns[definition.event_date_column][index],
                definition.event_date_column,
            )
            number = _finite_number(columns[base][index])
            if arrived is not None and arrived <= as_of and number is not None:
                values.append(number)
        cuts = tuple(type1_quantile(values, q) for q in _BAND_QUANTILES)
        result[field] = (
            None
            if any(cut is None for cut in cuts)
            else (float(cuts[0]), float(cuts[1]), float(cuts[2]))
        )
    return result


def _context(
    columns: Mapping[str, list[object]],
    index: int,
    definition: FeatureDefinition,
    by_name: Mapping[str, FeatureDefinition],
    bands: Mapping[str, tuple[float, float, float] | None],
    as_of: date,
) -> dict[str, object]:
    if not definition.context_hierarchy:
        raise ValueError(f"definition has no supplied context hierarchy: {definition.name}")
    result: dict[str, object] = {}
    for field in definition.context_hierarchy[0]:
        if field.endswith("_band"):
            base = _band_base(field)
            base_definition = by_name.get(base)
            cuts = bands.get(field)
            if base_definition is None or cuts is None or base not in columns:
                result[field] = None
                continue
            arrived = _date_value(
                columns[base_definition.event_date_column][index],
                base_definition.event_date_column,
            )
            number = _finite_number(columns[base][index])
            result[field] = (
                _band(number, cuts)
                if arrived is not None and arrived <= as_of and number is not None
                else None
            )
            continue
        context_definition = by_name.get(field)
        if context_definition is None or field not in columns:
            raise ValueError(f"context is not a supplied definition: {field}")
        arrived = _date_value(
            columns[context_definition.event_date_column][index],
            context_definition.event_date_column,
        )
        result[field] = (
            _wire_scalar(columns[field][index])
            if arrived is not None and arrived <= as_of
            else None
        )
    return result


def _required_string(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be a built-in non-empty string")
    return value


def build_criteria_projection(
    split: TimeSplitResult,
    definitions: Sequence[FeatureDefinition],
) -> bytes:
    """Return canonical JSONL containing only T-safe criteria inputs."""
    if type(split) is not TimeSplitResult:
        raise TypeError("split must be an exact TimeSplitResult")
    items = _definitions_snapshot(definitions)
    by_name = {item.name: item for item in items}
    ranges = tuple(
        item
        for item in items
        if item.field_role in _RANGE_ROLES and item.data_type == "NUMBER"
    )
    quality = tuple(item for item in items if item.field_role in _QUALITY_ROLES)
    reference = split.reference_rows.copy(deep=True)
    reference_columns = _columns(reference)
    bands = _band_boundaries(reference, items, split.as_of)
    lines: list[tuple[tuple[object, ...], bytes]] = []

    for index in range(len(reference)):
        hr_date_value = _date_value(reference_columns["hr_date"][index], "hr_date")
        if hr_date_value is None:
            raise ValueError("reference hr_date cannot be missing")
        hr_date = hr_date_value.isoformat()
        charge = _required_string(reference_columns["charge_id"][index], "charge_id")
        slab = _required_string(reference_columns["slab_no"][index], "slab_no")
        coil = _required_string(reference_columns["hr_coil_id"][index], "hr_coil_id")
        for definition in ranges:
            if (
                definition.name not in reference_columns
                or definition.event_date_column not in reference_columns
            ):
                continue
            arrived = _date_value(
                reference_columns[definition.event_date_column][index],
                definition.event_date_column,
            )
            value = _finite_number(reference_columns[definition.name][index])
            if arrived is None or arrived > split.as_of or value is None:
                continue
            line = {
                "kind": "OPERATING_RANGE_INPUT",
                "hrDate": hr_date,
                "chargeId": charge,
                "slabNo": slab,
                "hrCoilId": coil,
                "firstAvailableStage": definition.first_stage,
                "field": definition.name,
                "context": _context(
                    reference_columns,
                    index,
                    definition,
                    by_name,
                    bands,
                    split.as_of,
                ),
                "value": value,
            }
            lines.append(
                (
                    (
                        hr_date,
                        charge,
                        slab,
                        coil,
                        0,
                        STAGE_RANK[definition.first_stage],
                        definition.name.encode("utf-8"),
                    ),
                    canonical_json_bytes(line),
                )
            )

    for split_name, frame in (
        ("DISCOVERY", split.discovery_rows),
        ("CONFIRMATION", split.confirmation_rows),
    ):
        rows = frame.copy(deep=True)
        columns = _columns(rows)
        for index in range(len(rows)):
            hr_date_value = _date_value(columns["hr_date"][index], "hr_date")
            if hr_date_value is None:
                raise ValueError("mature hr_date cannot be missing")
            hr_date = hr_date_value.isoformat()
            charge = _required_string(columns["charge_id"][index], "charge_id")
            slab = _required_string(columns["slab_no"][index], "slab_no")
            coil = _required_string(columns["hr_coil_id"][index], "hr_coil_id")
            features = {
                definition.name: _wire_scalar(columns[definition.name][index])
                for definition in quality
            }
            line = {
                "kind": "MATURE_QUALITY_INPUT",
                "hrDate": hr_date,
                "chargeId": charge,
                "slabNo": slab,
                "hrCoilId": coil,
                "split": split_name,
                "features": features,
                "judge": _wire_scalar(columns["judge"][index]),
            }
            lines.append(
                (
                    (hr_date, charge, slab, coil, 1, 0, b""),
                    canonical_json_bytes(line),
                )
            )
    return b"".join(payload for _, payload in sorted(lines, key=lambda item: item[0]))
