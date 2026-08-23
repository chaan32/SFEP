from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import sparse


@dataclass(frozen=True)
class LinearShapResult:
    values: np.ndarray
    base_value: float


def linear_logit_shap(
    matrix,
    *,
    coefficient: np.ndarray,
    intercept: float,
    background_mean: np.ndarray,
) -> LinearShapResult:
    dense = matrix.toarray() if sparse.issparse(matrix) else np.asarray(matrix, dtype=float)
    coefficients = np.asarray(coefficient, dtype=float).reshape(-1)
    mean = np.asarray(background_mean, dtype=float).reshape(-1)
    if dense.ndim != 2 or dense.shape[1] != len(coefficients) or len(mean) != len(coefficients):
        raise ValueError("matrix, coefficient, and background_mean dimensions must agree")
    values = (dense - mean) * coefficients
    base_value = float(intercept + np.dot(mean, coefficients))
    return LinearShapResult(values=values, base_value=base_value)


def _source_feature(transformed_name: str, categorical_features: list[str]) -> str:
    if transformed_name.startswith("numeric__"):
        return transformed_name.removeprefix("numeric__")
    remainder = transformed_name.removeprefix("categorical__")
    for feature in sorted(categorical_features, key=len, reverse=True):
        if remainder == feature or remainder.startswith(f"{feature}_"):
            return feature
    return remainder


def aggregate_to_source_features(
    transformed_names: np.ndarray | list[str],
    shap_values: np.ndarray,
    *,
    categorical_features: list[str],
) -> tuple[list[str], np.ndarray]:
    names = [str(name) for name in transformed_names]
    values = np.asarray(shap_values, dtype=float)
    if values.ndim != 2 or values.shape[1] != len(names):
        raise ValueError("SHAP matrix width must match transformed feature names")
    source_by_transformed = [
        _source_feature(name, categorical_features) for name in names
    ]
    source_names = list(dict.fromkeys(source_by_transformed))
    aggregated = np.zeros((len(values), len(source_names)), dtype=float)
    source_index = {name: index for index, name in enumerate(source_names)}
    for transformed_index, source_name in enumerate(source_by_transformed):
        aggregated[:, source_index[source_name]] += values[:, transformed_index]
    return source_names, aggregated
