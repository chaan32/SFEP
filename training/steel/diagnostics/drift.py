from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp


def psi_from_proportions(
    reference: np.ndarray,
    current: np.ndarray,
    *,
    epsilon: float = 1e-6,
) -> float:
    reference_values = np.asarray(reference, dtype=float)
    current_values = np.asarray(current, dtype=float)
    if reference_values.shape != current_values.shape:
        raise ValueError("reference and current proportions must have the same shape")
    if np.any(reference_values < 0) or np.any(current_values < 0):
        raise ValueError("proportions cannot be negative")
    if reference_values.sum() <= 0 or current_values.sum() <= 0:
        raise ValueError("proportions must contain positive mass")
    reference_values = np.clip(reference_values, epsilon, None)
    current_values = np.clip(current_values, epsilon, None)
    reference_values = reference_values / reference_values.sum()
    current_values = current_values / current_values.sum()
    return float(
        np.sum(
            (current_values - reference_values)
            * np.log(current_values / reference_values)
        )
    )


def psi_from_counts(
    reference_counts: np.ndarray,
    current_counts: np.ndarray,
    *,
    pseudocount: float = 0.5,
) -> float:
    reference = np.asarray(reference_counts, dtype=float)
    current = np.asarray(current_counts, dtype=float)
    if reference.shape != current.shape or reference.ndim != 1:
        raise ValueError("reference and current counts must be same-width vectors")
    if np.any(reference < 0) or np.any(current < 0):
        raise ValueError("counts cannot be negative")
    categories = len(reference)
    reference_rates = (reference + pseudocount) / (
        reference.sum() + pseudocount * categories
    )
    current_rates = (current + pseudocount) / (
        current.sum() + pseudocount * categories
    )
    return psi_from_proportions(reference_rates, current_rates)


def _jensen_shannon_divergence(reference: np.ndarray, current: np.ndarray) -> float:
    reference_values = np.asarray(reference, dtype=float)
    current_values = np.asarray(current, dtype=float)
    reference_values = reference_values / reference_values.sum()
    current_values = current_values / current_values.sum()
    midpoint = 0.5 * (reference_values + current_values)
    left = np.zeros_like(reference_values)
    right = np.zeros_like(current_values)
    reference_positive = reference_values > 0
    current_positive = current_values > 0
    left[reference_positive] = reference_values[reference_positive] * np.log2(
        reference_values[reference_positive] / midpoint[reference_positive]
    )
    right[current_positive] = current_values[current_positive] * np.log2(
        current_values[current_positive] / midpoint[current_positive]
    )
    return float(0.5 * (left.sum() + right.sum()))


def _normalized_categories(series: pd.Series) -> pd.Series:
    return series.astype("string").fillna("<MISSING>")


def categorical_drift(
    feature: str,
    reference: pd.Series,
    current: pd.Series,
    *,
    comparison_split: str,
) -> tuple[dict[str, object], pd.DataFrame]:
    reference_values = _normalized_categories(reference)
    current_values = _normalized_categories(current)
    categories = sorted(set(reference_values) | set(current_values), key=str)
    reference_counts = reference_values.value_counts().reindex(categories, fill_value=0)
    current_counts = current_values.value_counts().reindex(categories, fill_value=0)
    reference_rates = reference_counts / len(reference_values)
    current_rates = current_counts / len(current_values)
    is_new = reference_counts.eq(0) & current_counts.gt(0)

    levels = pd.DataFrame(
        {
            "feature": feature,
            "comparison_split": comparison_split,
            "category": categories,
            "reference_count": reference_counts.to_numpy(dtype=int),
            "current_count": current_counts.to_numpy(dtype=int),
            "reference_rate": reference_rates.to_numpy(dtype=float),
            "current_rate": current_rates.to_numpy(dtype=float),
            "rate_delta": (current_rates - reference_rates).to_numpy(dtype=float),
            "is_new_category": is_new.to_numpy(dtype=bool),
        }
    )
    summary = {
        "feature": feature,
        "comparison_split": comparison_split,
        "reference_n": int(len(reference_values)),
        "current_n": int(len(current_values)),
        "psi": psi_from_counts(reference_counts.to_numpy(), current_counts.to_numpy()),
        "jensen_shannon_divergence": _jensen_shannon_divergence(
            reference_rates.to_numpy(), current_rates.to_numpy()
        ),
        "new_category_count": int(is_new.sum()),
        "new_category_row_rate": float(current_rates[is_new].sum()),
        "reference_missing_rate": float(reference_values.eq("<MISSING>").mean()),
        "current_missing_rate": float(current_values.eq("<MISSING>").mean()),
        "psi_smoothing": "jeffreys_0.5",
        "js_log_base": 2,
    }
    return summary, levels


def reference_quantile_edges(reference: np.ndarray, bins: int) -> np.ndarray:
    quantiles = np.linspace(0.0, 1.0, bins + 1)
    edges = np.unique(np.quantile(reference, quantiles))
    if len(edges) < 3:
        zero_rate = float(np.mean(np.asarray(reference) == 0.0))
        if zero_rate >= 0.5:
            return np.array([-np.inf, 0.0, np.inf])
        unique_values = np.unique(reference)
        if len(unique_values) == 1:
            value = float(unique_values[0])
            return np.array([-np.inf, value, np.inf])
        midpoint = float(unique_values[0] + (unique_values[-1] - unique_values[0]) / 2)
        return np.array([-np.inf, midpoint, np.inf])
    edges = edges.astype(float)
    edges[0] = -np.inf
    edges[-1] = np.inf
    return edges


def numeric_drift(
    feature: str,
    reference: pd.Series,
    current: pd.Series,
    *,
    comparison_split: str,
    bins: int = 10,
) -> dict[str, object]:
    reference_numeric = pd.to_numeric(reference, errors="coerce")
    current_numeric = pd.to_numeric(current, errors="coerce")
    reference_clean = reference_numeric.dropna().to_numpy(dtype=float)
    current_clean = current_numeric.dropna().to_numpy(dtype=float)
    if len(reference_clean) == 0 or len(current_clean) == 0:
        raise ValueError(f"{feature} requires non-missing numeric values in both cohorts")

    edges = reference_quantile_edges(reference_clean, bins)
    structural_zero_fallback = bool(
        len(edges) == 3
        and edges[0] == -np.inf
        and edges[1] == 0.0
        and edges[2] == np.inf
        and np.mean(reference_clean == 0.0) >= 0.5
    )
    reference_hist = (
        pd.cut(
            pd.Series(reference_clean),
            bins=edges,
            include_lowest=True,
            right=True,
        )
        .value_counts(sort=False)
        .to_numpy(dtype=int)
    )
    current_hist = (
        pd.cut(
            pd.Series(current_clean),
            bins=edges,
            include_lowest=True,
            right=True,
        )
        .value_counts(sort=False)
        .to_numpy(dtype=int)
    )
    reference_rates = reference_hist / reference_hist.sum()
    current_rates = current_hist / current_hist.sum()
    ks = ks_2samp(reference_clean, current_clean, alternative="two-sided", method="auto")
    return {
        "feature": feature,
        "comparison_split": comparison_split,
        "reference_n": int(len(reference)),
        "current_n": int(len(current)),
        "reference_non_missing": int(len(reference_clean)),
        "current_non_missing": int(len(current_clean)),
        "reference_mean": float(np.mean(reference_clean)),
        "current_mean": float(np.mean(current_clean)),
        "reference_median": float(np.median(reference_clean)),
        "current_median": float(np.median(current_clean)),
        "standardized_mean_difference": float(
            (np.mean(current_clean) - np.mean(reference_clean))
            / max(np.std(reference_clean, ddof=0), 1e-12)
        ),
        "ks_statistic": float(ks.statistic),
        "ks_pvalue": float(ks.pvalue),
        "psi": psi_from_counts(reference_hist, current_hist),
        "reference_missing_rate": float(reference_numeric.isna().mean()),
        "current_missing_rate": float(current_numeric.isna().mean()),
        "missing_rate_delta": float(
            current_numeric.isna().mean() - reference_numeric.isna().mean()
        ),
        "reference_bin_count": int(len(reference_hist)),
        "bin_strategy": "train_quantile_with_structural_value_fallback",
        "structural_zero_fallback": structural_zero_fallback,
        "psi_smoothing": "jeffreys_0.5",
    }
