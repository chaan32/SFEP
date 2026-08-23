from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact

from training.steel.diagnostics.drift import reference_quantile_edges


OUTCOME_ORDER = ("TN", "FP", "FN", "TP")


def attach_outcomes(
    frame: pd.DataFrame,
    *,
    y_true: pd.Series | np.ndarray,
    y_pred: pd.Series | np.ndarray,
) -> pd.DataFrame:
    truth = np.asarray(y_true, dtype=int)
    predicted = np.asarray(y_pred, dtype=int)
    if len(frame) != len(truth) or len(truth) != len(predicted):
        raise ValueError("frame, y_true, and y_pred must have the same length")
    if not set(np.unique(truth)).issubset({0, 1}) or not set(
        np.unique(predicted)
    ).issubset({0, 1}):
        raise ValueError("y_true and y_pred must be binary")

    result = frame.reset_index(drop=True).copy()
    result["y_true"] = truth
    result["y_pred"] = predicted
    result["outcome"] = np.select(
        [
            (truth == 0) & (predicted == 0),
            (truth == 0) & (predicted == 1),
            (truth == 1) & (predicted == 0),
            (truth == 1) & (predicted == 1),
        ],
        OUTCOME_ORDER,
        default="INVALID",
    )
    return result


def _safe_rate(numerator: int, denominator: int) -> float:
    return float(numerator / denominator) if denominator else float("nan")


def _wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total == 0:
        return float("nan"), float("nan")
    proportion = successes / total
    denominator = 1 + z**2 / total
    center = (proportion + z**2 / (2 * total)) / denominator
    margin = (
        z
        * np.sqrt(proportion * (1 - proportion) / total + z**2 / (4 * total**2))
        / denominator
    )
    return float(center - margin), float(center + margin)


def _benjamini_hochberg(pvalues: np.ndarray) -> np.ndarray:
    values = np.asarray(pvalues, dtype=float)
    order = np.argsort(values, kind="stable")
    ranked = values[order]
    adjusted = np.empty(len(values), dtype=float)
    running = 1.0
    for reverse_index in range(len(values) - 1, -1, -1):
        rank = reverse_index + 1
        running = min(running, ranked[reverse_index] * len(values) / rank)
        adjusted[order[reverse_index]] = min(running, 1.0)
    return adjusted


def _slice_row(
    *,
    slice_feature: str,
    slice_value: str,
    group: pd.DataFrame,
) -> dict[str, object]:
    counts = group["outcome"].value_counts()
    tn, fp, fn, tp = (int(counts.get(name, 0)) for name in OUTCOME_ORDER)
    actual_positive = tp + fn
    actual_negative = tn + fp
    alerts = tp + fp
    return {
        "slice_feature": slice_feature,
        "slice_value": slice_value,
        "n": int(len(group)),
        "actual_positive": actual_positive,
        "actual_negative": actual_negative,
        "alerts": alerts,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "tp": tp,
        "positive_rate": _safe_rate(actual_positive, len(group)),
        "alert_rate": _safe_rate(alerts, len(group)),
        "precision": _safe_rate(tp, alerts),
        "recall": _safe_rate(tp, actual_positive),
        "false_alarm_rate": _safe_rate(fp, actual_negative),
        "error_rate": _safe_rate(fp + fn, len(group)),
        "mean_risk_score": float(group["risk_score"].mean()),
    }


def categorical_error_slices(
    frame: pd.DataFrame,
    features: list[str],
    *,
    min_support: int = 30,
) -> pd.DataFrame:
    required = {"outcome", "risk_score"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"error frame is missing columns: {', '.join(missing)}")
    rows: list[dict[str, object]] = []
    for feature in features:
        if feature not in frame:
            continue
        values = frame[feature].astype("string").fillna("<MISSING>")
        for value, index in values.groupby(values, sort=True).groups.items():
            group = frame.loc[index]
            if len(group) < min_support:
                continue
            rows.append(
                _slice_row(
                    slice_feature=feature,
                    slice_value=str(value),
                    group=group,
                )
            )
    columns = [
        "slice_feature",
        "slice_value",
        "n",
        "actual_positive",
        "actual_negative",
        "alerts",
        "tn",
        "fp",
        "fn",
        "tp",
        "positive_rate",
        "alert_rate",
        "precision",
        "recall",
        "false_alarm_rate",
        "error_rate",
        "mean_risk_score",
    ]
    return pd.DataFrame(rows, columns=columns)


def numeric_error_slices(
    reference: pd.DataFrame,
    error_frame: pd.DataFrame,
    features: list[str],
    *,
    bins: int = 10,
    min_support: int = 30,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for feature in features:
        if feature not in reference or feature not in error_frame:
            continue
        reference_values = pd.to_numeric(reference[feature], errors="coerce").dropna()
        current_values = pd.to_numeric(error_frame[feature], errors="coerce")
        if reference_values.empty:
            continue
        edges = reference_quantile_edges(reference_values.to_numpy(dtype=float), bins)
        buckets = pd.cut(current_values, bins=edges, include_lowest=True, duplicates="drop")
        for bin_order, interval in enumerate(buckets.cat.categories, start=1):
            mask = buckets.eq(interval)
            group = error_frame.loc[mask]
            if len(group) < min_support:
                continue
            row = _slice_row(
                slice_feature=feature,
                slice_value=str(interval),
                group=group,
            )
            row.update(
                {
                    "slice_family": "TRAIN_QUANTILE_BIN",
                    "bin_order": bin_order,
                    "lower_bound": float(interval.left),
                    "upper_bound": float(interval.right),
                }
            )
            rows.append(row)
    columns = [
        "slice_family",
        "slice_feature",
        "slice_value",
        "bin_order",
        "lower_bound",
        "upper_bound",
        "n",
        "actual_positive",
        "actual_negative",
        "alerts",
        "tn",
        "fp",
        "fn",
        "tp",
        "positive_rate",
        "alert_rate",
        "precision",
        "recall",
        "false_alarm_rate",
        "error_rate",
        "mean_risk_score",
    ]
    return pd.DataFrame(rows, columns=columns)


def add_complement_statistics(
    slices: pd.DataFrame,
    full_error_frame: pd.DataFrame,
    *,
    fpr_min_negative: int = 200,
    fpr_min_fp: int = 20,
    fnr_min_positive: int = 40,
    fnr_min_fn: int = 10,
    fnr_min_tp: int = 10,
    fpr_material_delta: float = 0.03,
    fnr_material_delta: float = 0.15,
) -> pd.DataFrame:
    if slices.empty:
        return slices.copy()
    total_counts = full_error_frame["outcome"].value_counts()
    total_tn, total_fp, total_fn, total_tp = (
        int(total_counts.get(name, 0)) for name in OUTCOME_ORDER
    )
    enriched = slices.copy()
    fpr_pvalues: list[float] = []
    fnr_pvalues: list[float] = []
    complement_fpr: list[float] = []
    complement_fnr: list[float] = []
    fpr_low: list[float] = []
    fpr_high: list[float] = []
    fnr_low: list[float] = []
    fnr_high: list[float] = []
    for row in enriched.itertuples(index=False):
        other_fp, other_tn = total_fp - row.fp, total_tn - row.tn
        other_fn, other_tp = total_fn - row.fn, total_tp - row.tp
        complement_fpr.append(_safe_rate(other_fp, other_fp + other_tn))
        complement_fnr.append(_safe_rate(other_fn, other_fn + other_tp))
        fpr_pvalues.append(
            float(fisher_exact([[row.fp, row.tn], [other_fp, other_tn]]).pvalue)
        )
        fnr_pvalues.append(
            float(fisher_exact([[row.fn, row.tp], [other_fn, other_tp]]).pvalue)
        )
        low, high = _wilson_interval(row.fp, row.actual_negative)
        fpr_low.append(low)
        fpr_high.append(high)
        low, high = _wilson_interval(row.fn, row.actual_positive)
        fnr_low.append(low)
        fnr_high.append(high)

    enriched["fpr_ci_low"] = fpr_low
    enriched["fpr_ci_high"] = fpr_high
    enriched["fnr"] = enriched["fn"] / enriched["actual_positive"].replace(0, np.nan)
    enriched["fnr_ci_low"] = fnr_low
    enriched["fnr_ci_high"] = fnr_high
    enriched["complement_false_alarm_rate"] = complement_fpr
    enriched["complement_fnr"] = complement_fnr
    enriched["false_alarm_rate_delta"] = (
        enriched["false_alarm_rate"] - enriched["complement_false_alarm_rate"]
    )
    enriched["fnr_delta"] = enriched["fnr"] - enriched["complement_fnr"]
    enriched["fpr_fisher_pvalue"] = fpr_pvalues
    enriched["fnr_fisher_pvalue"] = fnr_pvalues
    enriched["fpr_bh_qvalue"] = _benjamini_hochberg(np.asarray(fpr_pvalues))
    enriched["fnr_bh_qvalue"] = _benjamini_hochberg(np.asarray(fnr_pvalues))
    enriched["fpr_support_pass"] = (
        enriched["actual_negative"].ge(fpr_min_negative)
        & enriched["fp"].ge(fpr_min_fp)
        & enriched["fpr_bh_qvalue"].lt(0.05)
        & enriched["false_alarm_rate_delta"].abs().ge(fpr_material_delta)
    )
    enriched["fnr_support_pass"] = (
        enriched["actual_positive"].ge(fnr_min_positive)
        & enriched["fn"].ge(fnr_min_fn)
        & enriched["tp"].ge(fnr_min_tp)
        & enriched["fnr_bh_qvalue"].lt(0.05)
        & enriched["fnr_delta"].abs().ge(fnr_material_delta)
    )
    return enriched
