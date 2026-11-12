"""Fixed binary quality metrics and OOF decision threshold."""

from dataclasses import dataclass

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, confusion_matrix


@dataclass(frozen=True)
class ClassificationMetrics:
    average_precision: float
    tp: int
    fp: int
    tn: int
    fn: int
    recall: float
    precision: float
    f2: float
    brier: float
    brier_skill: float


def classification_metrics(labels, probabilities, threshold: float, reference_prevalence: float) -> ClassificationMetrics:
    y = np.asarray(labels, dtype=np.int8)
    p = np.asarray(probabilities, dtype=float)
    if len(y) != len(p) or len(y) == 0 or set(y) != {0, 1}:
        raise ValueError("INSUFFICIENT_LABELS")
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("INVALID_PROBABILITY")
    if not 0 <= reference_prevalence <= 1:
        raise ValueError("INVALID_REFERENCE_PREVALENCE")
    tn, fp, fn, tp = map(int, confusion_matrix(y, p >= threshold, labels=[0, 1]).ravel())
    recall = tp / (tp + fn)
    precision = tp / (tp + fp) if tp + fp else 0.0
    f2 = 5 * precision * recall / (4 * precision + recall) if precision + recall else 0.0
    brier = float(brier_score_loss(y, p))
    baseline = float(brier_score_loss(y, np.full(len(y), reference_prevalence)))
    return ClassificationMetrics(float(average_precision_score(y, p)), tp, fp, tn, fn, recall, precision, f2, brier, 1 - brier / baseline if baseline else 0.0)


def select_f2_threshold(labels, probabilities) -> float:
    y = np.asarray(labels, dtype=np.int8)
    p = np.asarray(probabilities, dtype=float)
    if len(y) != len(p) or set(y) != {0, 1} or not np.isfinite(p).all():
        raise ValueError("INSUFFICIENT_LABELS")
    prevalence = float(y.mean())
    return max((float(t) for t in np.unique(p)), key=lambda t: (classification_metrics(y, p, t, prevalence).f2, t))
