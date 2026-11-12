"""Forward validation and deterministic fixed-candidate training."""

from dataclasses import dataclass
from datetime import date
import math

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression

from equipment_quality.model_features import FeatureContract, encode_model_rows, fit_feature_contract
from equipment_quality.model_metrics import ClassificationMetrics, classification_metrics, select_f2_threshold
from equipment_quality.model_runtime import single_threaded


@dataclass(frozen=True)
class ForwardFold:
    train: pd.DataFrame
    validation: pd.DataFrame


@dataclass(frozen=True)
class CandidateEvaluation:
    name: str
    estimator: object
    oof_probabilities: tuple[float, ...]
    oof_labels: tuple[int, ...]
    threshold: float
    metrics: ClassificationMetrics
    fold_ap_std: float
    fold_contracts: tuple[FeatureContract, ...]


@dataclass(frozen=True)
class CandidateEvaluationSet:
    ranked: tuple[CandidateEvaluation, ...]
    feature_contract: FeatureContract
    reference_prevalence: float


def _day(value) -> date:
    if isinstance(value, pd.Timestamp):
        value = value.date()
    if not isinstance(value, date):
        raise ValueError("INVALID_HR_DATE")
    return value


def build_forward_folds(rows: pd.DataFrame, fold_count: int = 3) -> tuple[ForwardFold, ...]:
    if rows.empty or fold_count != 3 or not {"hr_date", "charge_id"}.issubset(rows.columns):
        raise ValueError("INSUFFICIENT_LABELS")
    ordered = rows.assign(_model_day=[_day(v) for v in rows.hr_date]).sort_values("_model_day", kind="stable")
    dates = sorted(ordered._model_day)
    cutoffs = [dates[math.ceil(len(dates) * fraction / 4) - 1] for fraction in (1, 2, 3)]
    if len(set(cutoffs)) != 3:
        raise ValueError("INSUFFICIENT_LABELS")
    result = []
    for start, end in zip(cutoffs, (*cutoffs[1:], max(dates)), strict=True):
        train = ordered.loc[ordered._model_day <= start].copy()
        validation = ordered.loc[(ordered._model_day > start) & (ordered._model_day <= end)].copy()
        crossing = set(train.charge_id) & set(validation.charge_id)
        train = train.loc[~train.charge_id.isin(crossing)].drop(columns="_model_day")
        validation = validation.loc[~validation.charge_id.isin(crossing)].drop(columns="_model_day")
        if train.empty or validation.empty:
            raise ValueError("INSUFFICIENT_LABELS")
        result.append(ForwardFold(train, validation))
    return tuple(result)


def _candidates(seed: int):
    return (
        ("logistic", LogisticRegression(class_weight="balanced", solver="liblinear", max_iter=1000, random_state=seed)),
        ("hgb", HistGradientBoostingClassifier(class_weight="balanced", learning_rate=0.05, max_iter=100, max_depth=4, min_samples_leaf=20, l2_regularization=1.0, early_stopping=False, random_state=seed)),
        ("random_forest", RandomForestClassifier(n_estimators=200, max_depth=6, min_samples_leaf=10, class_weight="balanced_subsample", n_jobs=1, random_state=seed)),
    )


def _eligible(dataset):
    y = np.asarray(dataset.labels, dtype=object)[dataset.evaluable].astype(np.int8)
    if set(y) != {0, 1}:
        raise ValueError("INSUFFICIENT_LABELS")
    return dataset.matrix[dataset.evaluable], y


def evaluate_candidates(reference_rows: pd.DataFrame, seed: int) -> CandidateEvaluationSet:
    if "label_status" in reference_rows:
        reference_rows = reference_rows.loc[reference_rows.label_status == "AVAILABLE"]
    reference_rows = reference_rows.loc[reference_rows.judge.isin(("양품", "불량"))]
    folds = build_forward_folds(reference_rows)
    full_contract = fit_feature_contract(reference_rows)
    full_x, full_y = _eligible(encode_model_rows(reference_rows, full_contract, require_labels=True))
    prevalence = float(full_y.mean())
    evaluated = []
    for name, final_estimator in _candidates(seed):
        all_y, all_p, fold_ap, contracts = [], [], [], []
        for fold in folds:
            contract = fit_feature_contract(fold.train)
            train_x, train_y = _eligible(encode_model_rows(fold.train, contract, require_labels=True))
            valid_x, valid_y = _eligible(encode_model_rows(fold.validation, contract, require_labels=True))
            estimator = dict(_candidates(seed))[name]
            with single_threaded():
                estimator.fit(train_x, train_y)
                probability = estimator.predict_proba(valid_x)[:, 1]
            all_y.extend(int(v) for v in valid_y)
            all_p.extend(float(v) for v in probability)
            fold_ap.append(classification_metrics(valid_y, probability, 0.5, prevalence).average_precision)
            contracts.append(contract)
        threshold = select_f2_threshold(all_y, all_p)
        metrics = classification_metrics(all_y, all_p, threshold, prevalence)
        with single_threaded():
            final_estimator.fit(full_x, full_y)
        evaluated.append(CandidateEvaluation(name, final_estimator, tuple(all_p), tuple(all_y), threshold, metrics, float(np.std(fold_ap)), tuple(contracts)))
    evaluated.sort(key=lambda c: (-c.metrics.average_precision, -c.metrics.recall, -c.metrics.precision, c.metrics.brier, c.fold_ap_std, ("logistic", "hgb", "random_forest").index(c.name)))
    return CandidateEvaluationSet(tuple(evaluated), full_contract, prevalence)
