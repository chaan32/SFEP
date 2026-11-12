"""Feature projection and encoding at the furnace-extraction stage."""

from dataclasses import dataclass
from numbers import Real
from statistics import median

import numpy as np
import pandas as pd

from equipment_quality.deterministic import type1_quantile


_FEATURE_TYPES = (
    ("sm_plant", "categorical"),
    ("steel_grade", "categorical"),
    ("steel_usage", "categorical"),
    ("cc_gubun", "categorical"),
    ("slab_gubun", "categorical"),
    ("tundish_temp", "numeric"),
    ("mlac_ratio", "numeric"),
    ("delta_ferrite", "numeric"),
    ("ingre_cr", "numeric"),
    ("ingre_ni", "numeric"),
    ("ingre_s", "numeric"),
    ("slab_grind", "categorical"),
    ("furnace_no", "categorical"),
    ("f_jangip_gubun", "categorical"),
    ("f_jangip_temp", "numeric"),
    ("slab_width", "numeric"),
    ("f_pre_temp", "numeric"),
    ("f_pre_interval", "numeric"),
    ("f_heat_temp", "numeric"),
    ("f_heat_interval", "numeric"),
    ("f_sock_temp", "numeric"),
    ("f_sock_interval", "numeric"),
    ("f_bfg", "numeric"),
    ("f_cog", "numeric"),
    ("f_ldg", "numeric"),
    ("f_bfg_ratio", "numeric"),
    ("f_cog_ratio", "numeric"),
    ("f_ldg_ratio", "numeric"),
)
_UNKNOWN = "UNKNOWN"
_FLOAT32_MAX = float(np.finfo(np.float32).max)
_TARGETS = {"양품": 0, "불량": 1}


@dataclass(frozen=True)
class FeatureContract:
    numeric_medians: tuple[tuple[str, float], ...]
    categorical_vocabularies: tuple[tuple[str, tuple[str, ...]], ...]
    max_missing_feature_count: int

    def __post_init__(self) -> None:
        if (
            tuple(name for name, _ in self.numeric_medians)
            != tuple(name for name, kind in _FEATURE_TYPES if kind == "numeric")
            or tuple(name for name, _ in self.categorical_vocabularies)
            != tuple(name for name, kind in _FEATURE_TYPES if kind == "categorical")
        ):
            raise ValueError("Feature contract must match the ordered model allowlist")

    def to_wire(self) -> dict[str, object]:
        medians = dict(self.numeric_medians)
        vocabularies = dict(self.categorical_vocabularies)
        features = []
        for name, kind in _FEATURE_TYPES:
            if kind == "numeric":
                features.append({"median": medians[name], "name": name, "type": kind})
            else:
                features.append({"name": name, "type": kind, "vocabulary": list(vocabularies[name])})
        return {"features": features, "maxMissingFeatureCount": self.max_missing_feature_count}


@dataclass(frozen=True)
class EncodedDataset:
    """Rows in input order, with missing or unavailable labels represented by None."""

    matrix: np.ndarray
    labels: tuple[int | None, ...]
    input_incomplete: np.ndarray
    evaluable: np.ndarray


def project_model_rows(rows: pd.DataFrame) -> pd.DataFrame:
    names = [name for name, _ in _FEATURE_TYPES]
    missing = [name for name in names if name not in rows.columns]
    if missing:
        raise ValueError(f"Missing model feature columns: {', '.join(missing)}")
    projected = rows.loc[:, names]
    if not projected.columns.is_unique:
        raise ValueError("Duplicate model feature columns")
    return projected


def _observed(column: pd.Series, kind: str) -> tuple[np.ndarray, list]:
    missing = column.isna().to_numpy()
    values = column[~missing].tolist()
    if kind == "numeric":
        if any(
            not isinstance(value, Real)
            or isinstance(value, (bool, np.bool_))
            or not -_FLOAT32_MAX <= value <= _FLOAT32_MAX
            for value in values
        ):
            raise ValueError(f"{column.name} must contain finite float32-range numbers or nulls")
        values = [float(value) for value in values]
    elif any(not isinstance(value, str) for value in values):
        raise ValueError(f"{column.name} must contain strings or nulls")
    return missing, values


def fit_feature_contract(rows: pd.DataFrame) -> FeatureContract:
    """Fit from the supplied training rows only."""
    projected = project_model_rows(rows)
    if projected.empty:
        raise ValueError("Cannot fit a feature contract without training rows")
    medians = []
    vocabularies = []
    missing_counts = np.zeros(len(projected), dtype=int)
    for name, kind in _FEATURE_TYPES:
        missing, values = _observed(projected[name], kind)
        missing_counts += missing
        if kind == "numeric":
            if not values:
                raise ValueError(f"Cannot fit median for {name} without observed values")
            medians.append((name, median(values) or 0.0))
        else:
            known = sorted(set(values) - {_UNKNOWN}, key=str.encode)
            vocabularies.append((name, (*known, _UNKNOWN)))
    p99 = type1_quantile(missing_counts.tolist(), 0.99)
    return FeatureContract(tuple(medians), tuple(vocabularies), int(p99))


def encode_model_rows(
    rows: pd.DataFrame, contract: FeatureContract, *, require_labels: bool
) -> EncodedDataset:
    """Append one missing indicator after the value columns of each raw feature."""
    projected = project_model_rows(rows)
    medians = dict(contract.numeric_medians)
    vocabularies = dict(contract.categorical_vocabularies)
    width = len(_FEATURE_TYPES) + len(medians) + sum(map(len, vocabularies.values()))
    matrix = np.zeros((len(projected), width), dtype=np.float32)
    missing_counts = np.zeros(len(projected), dtype=int)
    start = 0
    for name, kind in _FEATURE_TYPES:
        missing, values = _observed(projected[name], kind)
        missing_counts += missing
        if kind == "numeric":
            matrix[missing, start] = medians[name]
            matrix[~missing, start] = values
            start += 1
        else:
            vocabulary = vocabularies[name]
            category_indices = {value: i for i, value in enumerate(vocabulary, start)}
            unknown_index = category_indices[_UNKNOWN]
            matrix[missing, unknown_index] = 1
            matrix[np.flatnonzero(~missing), [
                category_indices.get(value, unknown_index) for value in values
            ]] = 1
            start += len(vocabulary)
        matrix[:, start] = missing
        start += 1

    labels = (None,) * len(projected)
    if require_labels and "judge" in rows.columns:
        available = np.ones(len(projected), dtype=bool)
        if "label_status" in rows.columns:
            available = rows["label_status"].isin(["AVAILABLE"]).to_numpy()
        labels = tuple(
            _TARGETS.get(judge) if allowed and isinstance(judge, str) else None
            for judge, allowed in zip(rows["judge"], available, strict=True)
        )
    input_incomplete = missing_counts > contract.max_missing_feature_count
    evaluable = ~input_incomplete & np.array([label is not None for label in labels], dtype=bool)
    input_incomplete.setflags(write=False)
    evaluable.setflags(write=False)
    return EncodedDataset(matrix, labels, input_incomplete, evaluable)
