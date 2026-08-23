from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


IDENTITY_COLUMNS = ("charge_id", "slab_no", "hr_coil_id")
FEATURE_TIME_COLUMNS = ("hr_date", "feature_available_at")
FORBIDDEN_EXACT = {"judge", "ap_date", "label_finalized_at", "dataset_split"}
LABEL_COLUMNS = ("hr_coil_id", "judge", "label_finalized_at")
VALID_LABELS = {"양품", "불량"}
ENGINEERED_FEATURES = (
    "gas_total",
    "heating_interval_total",
    "pre_to_heat_temp_delta",
    "heat_to_sock_temp_delta",
    "width_reduction",
    "width_ratio",
)


def add_engineered_features(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    required = {
        "f_bfg",
        "f_cog",
        "f_ldg",
        "f_pre_interval",
        "f_heat_interval",
        "f_sock_interval",
        "f_pre_temp",
        "f_heat_temp",
        "f_sock_temp",
        "slab_width",
        "hr_width",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"engineered features require columns: {', '.join(missing)}")
    result = frame.copy()

    def numeric(column: str) -> pd.Series:
        return pd.to_numeric(result[column], errors="coerce")

    result["gas_total"] = numeric("f_bfg") + numeric("f_cog") + numeric("f_ldg")
    result["heating_interval_total"] = (
        numeric("f_pre_interval")
        + numeric("f_heat_interval")
        + numeric("f_sock_interval")
    )
    result["pre_to_heat_temp_delta"] = numeric("f_heat_temp") - numeric("f_pre_temp")
    result["heat_to_sock_temp_delta"] = numeric("f_sock_temp") - numeric("f_heat_temp")
    result["width_reduction"] = numeric("slab_width") - numeric("hr_width")
    slab_width = numeric("slab_width")
    result["width_ratio"] = numeric("hr_width").div(slab_width.mask(slab_width.eq(0.0)))
    return result, list(ENGINEERED_FEATURES)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def read_json_strict(path: Path) -> dict[str, object]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=_reject_json_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def _atomic_create(path: Path, writer: Callable[[object], None], *, binary: bool) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        mode = "wb" if binary else "w"
        kwargs = {} if binary else {"encoding": "utf-8", "newline": ""}
        with os.fdopen(file_descriptor, mode, **kwargs) as destination:
            writer(destination)
            destination.flush()
            os.fsync(destination.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            raise FileExistsError(path) from None
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_json(payload: dict[str, object], path: Path) -> None:
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    _atomic_create(Path(path), lambda destination: destination.write(encoded), binary=True)


def atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    encoded = frame.to_csv(index=False).encode("utf-8-sig")
    _atomic_create(Path(path), lambda destination: destination.write(encoded), binary=True)


def atomic_write_text(value: str, path: Path) -> None:
    encoded = value.encode("utf-8")
    _atomic_create(Path(path), lambda destination: destination.write(encoded), binary=True)


def atomic_copy_file(source: Path, path: Path) -> None:
    source = Path(source)

    def copy_into(destination: object) -> None:
        with source.open("rb") as origin:
            for chunk in iter(lambda: origin.read(1024 * 1024), b""):
                destination.write(chunk)

    _atomic_create(Path(path), copy_into, binary=True)


def _utc_timestamp(value: object, *, field: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} contains an invalid timestamp") from exc
    if timestamp.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return timestamp.tz_convert("UTC")


def make_batch_id(source_sha256: str, generated_at: pd.Timestamp) -> str:
    if len(source_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in source_sha256.lower()
    ):
        raise ValueError("source_sha256 must be a 64-character hexadecimal digest")
    utc = _utc_timestamp(generated_at, field="generated_at")
    return f"{utc.strftime('%Y%m%dT%H%M%S%fZ')}-{source_sha256[:12].lower()}"


def _registry_feature_contract(
    registry: dict[str, object],
) -> tuple[list[str], list[str], list[str]]:
    models = registry.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("registry must contain at least one model")
    included: list[str] = []
    numeric: list[str] = []
    categorical: list[str] = []
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("registry model entry must be an object")
        schema = model.get("feature_schema")
        if not isinstance(schema, dict):
            raise ValueError("registry model feature schema is missing")
        for contract_name, destination in (
            ("included", included),
            ("numeric", numeric),
            ("categorical", categorical),
        ):
            columns = schema.get(contract_name)
            if not isinstance(columns, list) or not all(
                isinstance(column, str) for column in columns
            ):
                raise ValueError(f"registry feature schema {contract_name} is invalid")
            for column in columns:
                if column not in destination:
                    destination.append(column)
    if set(included) != set(numeric) | set(categorical):
        raise ValueError("registry feature schema is internally inconsistent")
    if set(numeric) & set(categorical):
        raise ValueError("registry numeric and categorical features overlap")
    return included, numeric, categorical


def _normalized_identifiers(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in IDENTITY_COLUMNS:
        values = result[column].astype("string").str.strip()
        if values.isna().any() or values.eq("").any():
            raise ValueError(f"{column} contains missing identifiers")
        result[column] = values
    return result


def validate_feature_batch(
    frame: pd.DataFrame,
    registry: dict[str, object],
    existing_coils: set[str],
) -> pd.DataFrame:
    supplied_engineered = sorted(set(frame.columns) & set(ENGINEERED_FEATURES))
    if supplied_engineered:
        raise ValueError(
            "feature batch must not supply derived columns: "
            + ", ".join(supplied_engineered)
        )
    forbidden = sorted(
        column
        for column in frame.columns
        if column in FORBIDDEN_EXACT or column.startswith("ap_")
    )
    if forbidden:
        raise ValueError(f"feature batch contains forbidden columns: {', '.join(forbidden)}")
    required_contract = [*IDENTITY_COLUMNS, *FEATURE_TIME_COLUMNS]
    missing_contract = sorted(set(required_contract) - set(frame.columns))
    if missing_contract:
        raise ValueError(
            f"feature batch is missing contract columns: {', '.join(missing_contract)}"
        )
    included, numeric, categorical = _registry_feature_contract(registry)
    requested_engineered = set(included) & set(ENGINEERED_FEATURES)
    missing_features = sorted(
        set(included) - set(frame.columns) - requested_engineered
    )
    if missing_features:
        raise ValueError(
            f"feature batch is missing model features: {', '.join(missing_features)}"
        )
    prepared = _normalized_identifiers(frame)
    if requested_engineered:
        prepared, _ = add_engineered_features(prepared)
    if prepared["hr_coil_id"].duplicated().any():
        raise ValueError("feature batch contains duplicate hr_coil_id")
    reused = sorted(set(prepared["hr_coil_id"]) & {str(value) for value in existing_coils})
    if reused:
        raise ValueError(f"hr_coil_id was already scored: {reused[0]}")
    parsed_hr_date = pd.to_datetime(
        prepared["hr_date"], format="%Y-%m-%d", errors="coerce"
    )
    if parsed_hr_date.isna().any():
        raise ValueError("hr_date contains invalid dates")
    prepared["hr_date"] = parsed_hr_date.dt.strftime("%Y-%m-%d")
    prepared["feature_available_at"] = [
        _utc_timestamp(value, field="feature_available_at").isoformat()
        for value in prepared["feature_available_at"]
    ]
    coercion_count = np.zeros(len(prepared), dtype=int)
    for column in numeric:
        original = prepared[column]
        converted = pd.to_numeric(original, errors="coerce")
        coercion_count += (original.notna() & converted.isna()).to_numpy(dtype=int)
        prepared[column] = converted
    for column in categorical:
        prepared[column] = prepared[column].astype("object")
        prepared.loc[prepared[column].isna(), column] = np.nan
    ordered = [*IDENTITY_COLUMNS, *FEATURE_TIME_COLUMNS, *included]
    result = prepared[ordered].copy()
    result["shadow_numeric_coercion_count"] = coercion_count
    return result


def validate_label_batch(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(LABEL_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"label batch is missing columns: {', '.join(missing)}")
    result = frame[list(LABEL_COLUMNS)].copy()
    result["hr_coil_id"] = result["hr_coil_id"].astype("string").str.strip()
    if result["hr_coil_id"].isna().any() or result["hr_coil_id"].eq("").any():
        raise ValueError("hr_coil_id contains missing identifiers")
    if result["hr_coil_id"].duplicated().any():
        raise ValueError("label batch contains duplicate hr_coil_id")
    result["judge"] = result["judge"].astype("string").str.strip()
    invalid_labels = sorted(set(result["judge"].dropna()) - VALID_LABELS)
    if result["judge"].isna().any() or invalid_labels:
        shown = ["<MISSING>"] if result["judge"].isna().any() else []
        shown.extend(str(value) for value in invalid_labels)
        raise ValueError(f"label batch contains unsupported judge values: {', '.join(shown)}")
    result["label_finalized_at"] = [
        _utc_timestamp(value, field="label_finalized_at").isoformat()
        for value in result["label_finalized_at"]
    ]
    return result
