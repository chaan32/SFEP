from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.special import expit
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.model_selection import StratifiedGroupKFold


@dataclass(frozen=True)
class CrossValidatedCalibration:
    probabilities: dict[str, np.ndarray]
    fold_id: np.ndarray


def _row_subset(frame, index: np.ndarray):
    return frame.iloc[index] if hasattr(frame, "iloc") else np.asarray(frame)[index]


def fit_frozen_calibrator(estimator, method: str, features, y_true: np.ndarray):
    if method == "raw":
        return None
    sklearn_method = {"platt": "sigmoid", "isotonic": "isotonic"}.get(method)
    if sklearn_method is None:
        raise ValueError(f"unsupported calibration method: {method}")
    truth = np.asarray(y_true, dtype=int)
    class_counts = np.bincount(truth, minlength=2)
    inner_folds = int(min(5, class_counts.min()))
    if inner_folds < 2:
        raise ValueError("frozen calibration requires at least two rows per class")
    return CalibratedClassifierCV(
        FrozenEstimator(estimator),
        method=sklearn_method,
        cv=inner_folds,
    ).fit(features, truth)


def apply_frozen_calibrator(method: str, calibrator, estimator, features) -> np.ndarray:
    model = estimator if method == "raw" else calibrator
    classes = np.asarray(model.classes_)
    positive_positions = np.flatnonzero(classes == 1)
    if len(positive_positions) != 1:
        raise ValueError("calibration estimator must expose binary class 1")
    return np.asarray(model.predict_proba(features)[:, positive_positions[0]], dtype=float)


def apply_frozen_score_calibrator(
    method: str,
    calibrator,
    decision_score: np.ndarray | pd.Series,
) -> np.ndarray:
    scores = np.asarray(decision_score, dtype=float)
    if method == "raw":
        return expit(scores)
    calibrated_classifiers = getattr(calibrator, "calibrated_classifiers_", [])
    if len(calibrated_classifiers) != 1:
        raise ValueError("expected one frozen calibrated classifier")
    score_calibrators = calibrated_classifiers[0].calibrators
    if len(score_calibrators) != 1:
        raise ValueError("expected one binary score calibrator")
    return np.asarray(score_calibrators[0].predict(scores), dtype=float)


def cross_validated_frozen_calibration(
    estimator,
    features,
    y_true: np.ndarray | pd.Series,
    groups: np.ndarray | pd.Series,
    *,
    n_splits: int = 5,
    random_state: int = 42,
) -> CrossValidatedCalibration:
    truth = np.asarray(y_true, dtype=int)
    group_values = np.asarray(groups)
    if not (len(features) == len(truth) == len(group_values)):
        raise ValueError("features, y_true, and groups must have the same length")
    if set(np.unique(truth)) != {0, 1}:
        raise ValueError("calibration requires both binary labels")
    if len(np.unique(group_values)) < n_splits:
        raise ValueError("calibration requires at least n_splits unique groups")
    if min(np.bincount(truth)) < n_splits:
        raise ValueError("each label requires at least n_splits rows")

    probabilities = {
        "raw": apply_frozen_calibrator("raw", None, estimator, features),
        "platt": np.full(len(truth), np.nan),
        "isotonic": np.full(len(truth), np.nan),
    }
    fold_id = np.full(len(truth), -1, dtype=int)
    splitter = StratifiedGroupKFold(
        n_splits=n_splits,
        shuffle=True,
        random_state=random_state,
    )
    for fold, (fit_index, score_index) in enumerate(
        splitter.split(np.zeros((len(truth), 1)), truth, group_values)
    ):
        fold_id[score_index] = fold
        fit_features = _row_subset(features, fit_index)
        score_features = _row_subset(features, score_index)
        for method in ("platt", "isotonic"):
            calibrator = fit_frozen_calibrator(
                estimator,
                method,
                fit_features,
                truth[fit_index],
            )
            probabilities[method][score_index] = apply_frozen_calibrator(
                method,
                calibrator,
                estimator,
                score_features,
            )
    if np.any(fold_id < 0) or any(
        np.isnan(values).any() for values in probabilities.values()
    ):
        raise ValueError("every row must receive exactly one out-of-fold probability")
    return CrossValidatedCalibration(probabilities=probabilities, fold_id=fold_id)


def calibration_bin_table(
    y_true: np.ndarray | pd.Series,
    probability: np.ndarray | pd.Series,
    *,
    split: str,
    variant: str,
    bins: int = 10,
) -> pd.DataFrame:
    truth = np.asarray(y_true, dtype=int)
    scores = np.asarray(probability, dtype=float)
    if len(truth) != len(scores) or len(truth) == 0:
        raise ValueError("y_true and probability must have the same non-zero length")
    if bins < 1:
        raise ValueError("bins must be positive")
    if not np.isfinite(scores).all() or np.any((scores < 0.0) | (scores > 1.0)):
        raise ValueError("probability must contain finite values between zero and one")
    if len(np.unique(scores)) == 1:
        bin_id = np.zeros(len(scores), dtype=int)
    else:
        quantile_buckets = pd.qcut(
            pd.Series(scores),
            q=min(bins, len(scores)),
            labels=False,
            duplicates="drop",
        )
        bin_id = quantile_buckets.to_numpy(dtype=int)
    rows: list[dict[str, object]] = []
    for current_bin in sorted(np.unique(bin_id)):
        mask = bin_id == current_bin
        mean_probability = float(scores[mask].mean())
        observed_rate = float(truth[mask].mean())
        rows.append(
            {
                "split": split,
                "variant": variant,
                "bin": int(current_bin + 1),
                "count": int(mask.sum()),
                "min_probability": float(scores[mask].min()),
                "max_probability": float(scores[mask].max()),
                "mean_probability": mean_probability,
                "observed_rate": observed_rate,
                "calibration_gap": observed_rate - mean_probability,
                "absolute_gap": abs(observed_rate - mean_probability),
            }
        )
    return pd.DataFrame(rows)


def expected_calibration_error(bin_table: pd.DataFrame) -> float:
    if bin_table.empty:
        raise ValueError("calibration bin table cannot be empty")
    return float(
        (bin_table["count"] * bin_table["absolute_gap"]).sum()
        / bin_table["count"].sum()
    )


def fixed_width_calibration_bin_table(
    y_true: np.ndarray | pd.Series,
    probability: np.ndarray | pd.Series,
    *,
    split: str,
    variant: str,
    bins: int = 10,
) -> pd.DataFrame:
    truth = np.asarray(y_true, dtype=int)
    scores = np.asarray(probability, dtype=float)
    if len(truth) != len(scores) or len(truth) == 0:
        raise ValueError("y_true and probability must have the same non-zero length")
    if bins < 1:
        raise ValueError("bins must be positive")
    if not np.isfinite(scores).all() or np.any((scores < 0.0) | (scores > 1.0)):
        raise ValueError("probability must contain finite values between zero and one")
    bin_id = np.minimum(np.floor(scores * bins).astype(int), bins - 1)
    rows: list[dict[str, object]] = []
    for current_bin in sorted(np.unique(bin_id)):
        mask = bin_id == current_bin
        mean_probability = float(scores[mask].mean())
        observed_rate = float(truth[mask].mean())
        rows.append(
            {
                "split": split,
                "variant": variant,
                "bin": int(current_bin + 1),
                "count": int(mask.sum()),
                "min_probability": float(scores[mask].min()),
                "max_probability": float(scores[mask].max()),
                "mean_probability": mean_probability,
                "observed_rate": observed_rate,
                "calibration_gap": observed_rate - mean_probability,
                "absolute_gap": abs(observed_rate - mean_probability),
            }
        )
    return pd.DataFrame(rows)


def calibration_metrics(
    y_true: np.ndarray | pd.Series,
    probability: np.ndarray | pd.Series,
    *,
    split: str,
    variant: str,
    bins: int = 10,
) -> tuple[dict[str, object], pd.DataFrame]:
    truth = np.asarray(y_true, dtype=int)
    scores = np.asarray(probability, dtype=float)
    if not np.isfinite(scores).all() or np.any((scores < 0.0) | (scores > 1.0)):
        raise ValueError("probability must contain finite values between zero and one")
    table = calibration_bin_table(
        truth,
        scores,
        split=split,
        variant=variant,
        bins=bins,
    )
    fixed_width_table = fixed_width_calibration_bin_table(
        truth,
        scores,
        split=split,
        variant=variant,
        bins=bins,
    )
    metrics = {
        "split": split,
        "variant": variant,
        "n": int(len(truth)),
        "positive": int(truth.sum()),
        "positive_rate": float(truth.mean()),
        "mean_probability": float(scores.mean()),
        "brier_score": float(brier_score_loss(truth, scores)),
        "log_loss": float(log_loss(truth, scores, labels=[0, 1])),
        "ece": expected_calibration_error(table),
        "fixed_width_ece": expected_calibration_error(fixed_width_table),
        "maximum_calibration_error": float(table["absolute_gap"].max()),
    }
    return metrics, table


def choose_calibration_method(
    metrics: pd.DataFrame,
    *,
    brier_tolerance: float = 1e-4,
) -> str:
    required = {"variant", "brier_score", "log_loss", "ece"}
    missing = sorted(required - set(metrics.columns))
    if missing:
        raise ValueError(f"calibration metrics are missing columns: {', '.join(missing)}")
    if brier_tolerance < 0:
        raise ValueError("brier_tolerance must be non-negative")
    preference = {"platt": 0, "raw": 1, "isotonic": 2}
    ranked = metrics.copy()
    best_brier = float(ranked["brier_score"].min())
    ranked = ranked.loc[
        ranked["brier_score"] <= best_brier + brier_tolerance
    ].copy()
    ranked["preference"] = ranked["variant"].map(preference).fillna(99)
    ranked = ranked.sort_values(
        ["log_loss", "ece", "preference", "brier_score"], kind="stable"
    )
    return str(ranked.iloc[0]["variant"])


def fit_calibrator(method: str, raw_score: np.ndarray, y_true: np.ndarray):
    scores = np.asarray(raw_score, dtype=float)
    truth = np.asarray(y_true, dtype=int)
    if method == "raw":
        return None
    if method == "platt":
        model = LogisticRegression(solver="lbfgs", max_iter=2000)
        return model.fit(scores.reshape(-1, 1), truth)
    if method == "isotonic":
        model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        return model.fit(scores, truth)
    raise ValueError(f"unsupported calibration method: {method}")


def apply_calibrator(method: str, calibrator, raw_score: np.ndarray) -> np.ndarray:
    scores = np.asarray(raw_score, dtype=float)
    if method == "raw":
        return np.clip(scores, 0.0, 1.0)
    if method == "platt":
        return calibrator.predict_proba(scores.reshape(-1, 1))[:, 1]
    if method == "isotonic":
        return np.asarray(calibrator.predict(scores), dtype=float)
    raise ValueError(f"unsupported calibration method: {method}")


def cross_validated_calibration(
    raw_score: np.ndarray | pd.Series,
    y_true: np.ndarray | pd.Series,
    groups: np.ndarray | pd.Series,
    *,
    raw_probability: np.ndarray | pd.Series | None = None,
    n_splits: int = 5,
    random_state: int = 42,
) -> CrossValidatedCalibration:
    scores = np.asarray(raw_score, dtype=float)
    truth = np.asarray(y_true, dtype=int)
    group_values = np.asarray(groups)
    probability = (
        np.clip(scores.copy(), 0.0, 1.0)
        if raw_probability is None
        else np.asarray(raw_probability, dtype=float)
    )
    if not (len(scores) == len(truth) == len(group_values) == len(probability)):
        raise ValueError(
            "raw_score, raw_probability, y_true, and groups must have the same length"
        )
    if not np.isfinite(probability).all() or np.any(
        (probability < 0.0) | (probability > 1.0)
    ):
        raise ValueError("raw_probability must be finite and between zero and one")
    if set(np.unique(truth)) != {0, 1}:
        raise ValueError("calibration requires both binary labels")
    if len(np.unique(group_values)) < n_splits:
        raise ValueError("calibration requires at least n_splits unique groups")
    if min(np.bincount(truth)) < n_splits:
        raise ValueError("each label requires at least n_splits rows")

    probabilities = {
        "raw": probability.copy(),
        "platt": np.full(len(scores), np.nan),
        "isotonic": np.full(len(scores), np.nan),
    }
    fold_id = np.full(len(scores), -1, dtype=int)
    splitter = StratifiedGroupKFold(
        n_splits=n_splits,
        shuffle=True,
        random_state=random_state,
    )
    for fold, (fit_index, score_index) in enumerate(
        splitter.split(scores.reshape(-1, 1), truth, group_values)
    ):
        fold_id[score_index] = fold
        for method in ("platt", "isotonic"):
            calibrator = fit_calibrator(method, scores[fit_index], truth[fit_index])
            probabilities[method][score_index] = apply_calibrator(
                method, calibrator, scores[score_index]
            )
    if np.any(fold_id < 0) or any(np.isnan(values).any() for values in probabilities.values()):
        raise ValueError("every row must receive exactly one out-of-fold calibrated probability")
    return CrossValidatedCalibration(probabilities=probabilities, fold_id=fold_id)
