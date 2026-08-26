"""Strict source CSV and analysis-configuration validation."""

from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import date
from pathlib import Path

import pandas as pd
from jsonschema import FormatChecker
from jsonschema.validators import validator_for

from equipment_quality.deterministic import sha256_uri
from equipment_quality.models import AnalysisConfig, InputTables, SourceFile


_SOURCE_SPECS = (
    (
        "sm_cc",
        "sts_1sm_cc_1.csv",
        (
            "sm_plant", "charge_id", "steel_grade", "steel_usage", "delta_ferrite",
            "ingre_cr", "ingre_ni", "ingre_s", "cast_date", "cc_gubun",
            "tundish_temp", "mlac_ratio", "slab_no", "slab_gubun", "slab_grind",
        ),
    ),
    (
        "fur_hr",
        "sts_2fur_hr_2.csv",
        (
            "charge_id", "slab_no", "furnace_no", "f_jangip_gubun",
            "f_jangip_temp", "f_bfg", "f_cog", "f_ldg", "f_bfg_per",
            "f_cog_per", "f_ldg_per", "f_pre_temp", "f_heat_temp", "f_sock_temp",
            "f_pre_interval", "f_heat_interval", "f_sock_interval", "f_ext_date",
            "f_ext_time", "hr_coil_id", "hr_date", "hr_thick", "hr_width",
            "rm4_temp", "rm_pitch", "slab_width",
        ),
    ),
    (
        "ap",
        "sts_3ap_3.csv",
        (
            "judge", "hr_coil_id", "ap_plant", "ap_prod_id", "ap_date", "ap_shift",
            "ap_thick", "ap_width", "ap_line_speed",
        ),
    ),
)

_IDENTIFIER_COLUMNS = {
    "sm_cc": {"charge_id", "slab_no"},
    "fur_hr": {"charge_id", "slab_no", "hr_coil_id"},
    "ap": {"hr_coil_id", "ap_prod_id"},
}
_DATE_COLUMNS = {
    "sm_cc": {"cast_date"},
    "fur_hr": {"f_ext_date", "hr_date"},
    "ap": {"ap_date"},
}
_NUMERIC_COLUMNS = {
    "sm_cc": {
        "delta_ferrite", "ingre_cr", "ingre_ni", "ingre_s", "tundish_temp",
        "mlac_ratio",
    },
    "fur_hr": {
        "f_jangip_temp", "f_bfg", "f_cog", "f_ldg", "f_bfg_per", "f_cog_per",
        "f_ldg_per", "f_pre_temp", "f_heat_temp", "f_sock_temp", "f_pre_interval",
        "f_heat_interval", "f_sock_interval", "hr_thick", "hr_width", "rm4_temp",
        "rm_pitch", "slab_width",
    },
    "ap": {"ap_thick", "ap_width", "ap_line_speed"},
}
_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _parse_date(raw: str, *, role: str, column: str, record_number: int) -> date:
    value = raw.strip()
    if not _DATE_PATTERN.fullmatch(value):
        raise ValueError(
            f"{role} record {record_number} column {column} must be YYYY-MM-DD"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(
            f"{role} record {record_number} column {column} must be YYYY-MM-DD"
        ) from error


def _parse_number(
    raw: str, *, role: str, column: str, record_number: int
) -> float | None:
    value = raw.strip()
    if value == "":
        return None
    try:
        number = float(value)
    except ValueError as error:
        raise ValueError(
            f"{role} record {record_number} column {column} must be finite numeric"
        ) from error
    if not math.isfinite(number):
        raise ValueError(
            f"{role} record {record_number} column {column} must be finite numeric"
        )
    return number


def _convert_record(
    role: str,
    header: tuple[str, ...],
    values: list[str],
    record_number: int,
) -> dict[str, object]:
    converted: dict[str, object] = {}
    for column, raw in zip(header, values, strict=True):
        if column in _IDENTIFIER_COLUMNS[role]:
            if raw == "":
                converted[column] = None
                continue
            identifier = raw.strip()
            if identifier == "":
                raise ValueError(
                    f"{role} record {record_number} column {column} must be a non-empty identifier"
                )
            converted[column] = identifier
        elif column in _DATE_COLUMNS[role]:
            converted[column] = _parse_date(
                raw, role=role, column=column, record_number=record_number
            )
        elif column in _NUMERIC_COLUMNS[role]:
            converted[column] = _parse_number(
                raw, role=role, column=column, record_number=record_number
            )
        elif role == "fur_hr" and column == "f_ext_time":
            number = _parse_number(
                raw, role=role, column=column, record_number=record_number
            )
            if number is None or not number.is_integer() or not 0 <= number <= 23:
                raise ValueError(
                    f"{role} record {record_number} column {column} must be an hour from 0 to 23"
                )
            converted[column] = int(number)
        elif role == "ap" and column == "judge":
            if raw == "":
                converted[column] = None
            elif raw not in {"양품", "불량"}:
                raise ValueError(
                    f"ap record {record_number} judge must be one of 양품 or 불량"
                )
            else:
                converted[column] = raw
        else:
            converted[column] = raw if raw != "" else None
    converted["_source_record_number"] = record_number
    return converted


def _read_source(
    data_dir: Path,
    role: str,
    name: str,
    header: tuple[str, ...],
) -> tuple[pd.DataFrame, SourceFile]:
    path = data_dir / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"source must be a non-symlink regular file: {name}")
    payload = path.read_bytes()
    try:
        text = payload.decode("cp949", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"source {name} is not valid CP949") from error
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError(f"source {name} is not valid CSV") from error
    if not records or tuple(records[0]) != header:
        actual = tuple(records[0]) if records else ()
        raise ValueError(
            f"source {name} must have the exact ordered header; got {actual!r}"
        )
    converted_rows: list[dict[str, object]] = []
    for record_number, values in enumerate(records[1:], start=2):
        if len(values) != len(header):
            raise ValueError(
                f"source {name} record {record_number} has {len(values)} fields; expected {len(header)}"
            )
        converted_rows.append(_convert_record(role, header, values, record_number))
    frame = pd.DataFrame(converted_rows, columns=[*header, "_source_record_number"])
    source = SourceFile(
        role=role,
        name=name,
        size_bytes=len(payload),
        sha256=sha256_uri(payload),
    )
    return frame, source


def read_inputs(data_dir: Path) -> InputTables:
    """Read the exact three CP949 inputs into strictly typed tables."""
    data_dir = Path(data_dir)
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise ValueError(f"data directory must be a non-symlink directory: {data_dir}")
    expected = {name for _, name, _ in _SOURCE_SPECS}
    actual = {
        entry.name
        for entry in data_dir.iterdir()
        if entry.is_file() and entry.suffix.lower() == ".csv"
    }
    if actual != expected:
        raise ValueError(
            f"data directory CSV filenames must be exactly {sorted(expected)}; got {sorted(actual)}"
        )
    frames: dict[str, pd.DataFrame] = {}
    sources: list[SourceFile] = []
    for role, name, header in _SOURCE_SPECS:
        frame, source = _read_source(data_dir, role, name, header)
        frames[role] = frame
        sources.append(source)
    return InputTables(
        sm_cc=frames["sm_cc"],
        fur_hr=frames["fur_hr"],
        ap=frames["ap"],
        sources=tuple(sources),
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"analysis config numbers must be finite; found {value}")


def _hierarchies(items: list[dict[str, object]]) -> dict[str, tuple[tuple[str, ...], ...]]:
    return {
        str(item["equipmentType"]): tuple(
            tuple(str(field) for field in level) for level in item["levels"]
        )
        for item in items
    }


def load_analysis_config(path: Path) -> AnalysisConfig:
    """Load the complete normative config without unknowns, defaults, or mutation."""
    path = Path(path)
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant
        )
    except UnicodeDecodeError as error:
        raise ValueError("analysis config must be UTF-8") from error
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "contracts/equipment-monitor/v1/analysis_config.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator_class = validator_for(schema)
    validator_class.check_schema(schema)
    validator_class(schema, format_checker=FormatChecker()).validate(payload)

    field_names = [field["field"] for field in payload["fields"]]
    if len(set(field_names)) != len(field_names):
        raise ValueError("analysis config field names must be unique")
    for field in payload["fields"]:
        source_role = field["sourceRole"]
        source_column = field["sourceColumn"]
        dependencies = field["dependencies"]
        if (source_role is None) != (source_column is None):
            raise ValueError("field source role and column must both be present or both null")
        if source_role is None and not dependencies:
            raise ValueError("derived fields must declare dependencies")
        if source_role is not None and dependencies:
            raise ValueError("raw source fields must not declare derived dependencies")

    return AnalysisConfig(
        schema_version=payload["schemaVersion"],
        analysis_config_version=payload["analysisConfigVersion"],
        timezone=payload["timezone"],
        label_maturity_days=payload["labelMaturityDays"],
        reference_fraction=payload["splits"]["referenceFraction"],
        discovery_fraction=payload["splits"]["discoveryFraction"],
        operating_ranges=payload["operatingRanges"],
        quality_risk=payload["qualityRisk"],
        bootstrap=payload["bootstrap"],
        wilson_z=payload["wilsonZ"],
        fields=tuple(payload["fields"]),
        range_context_hierarchies=_hierarchies(payload["rangeContextHierarchies"]),
        risk_adjustment_hierarchies=_hierarchies(
            payload["riskAdjustmentHierarchies"]
        ),
        fixed_interactions=tuple(
            tuple(interaction) for interaction in payload["fixedInteractions"]
        ),
        fdr_families=tuple(payload["fdrFamilies"]),
        evidence_families=tuple(payload["evidenceFamilies"]),
    )
