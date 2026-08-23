from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Callable, Iterable

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline

from training.steel import train_baseline as baseline
from training.steel import train_challenger as challenger
from training.steel.diagnostics.calibration import fit_calibrator
from training.steel.nextgen_model import (
    FrozenRankAverageEnsemble,
    ensemble_integrity_metadata,
    frozen_percentile,
    numeric_array_sha256,
    validate_ensemble_integrity,
)
from training.steel.shadow_contract import (
    add_engineered_features as add_shared_engineered_features,
    atomic_write_csv,
    atomic_write_json,
    atomic_write_text,
    sha256_file,
)


NEXTGEN_VERSION = "steel-quality-nextgen-v0.1"
DEFAULT_FREEZE_DATE = "2025-09-01"
ENGINEERED_FEATURES = [
    "gas_total",
    "heating_interval_total",
    "pre_to_heat_temp_delta",
    "heat_to_sock_temp_delta",
    "width_reduction",
    "width_ratio",
]


def development_cohort(
    frame: pd.DataFrame,
    freeze_date: str = DEFAULT_FREEZE_DATE,
) -> pd.DataFrame:
    if freeze_date != DEFAULT_FREEZE_DATE:
        raise ValueError(f"freeze_date is immutable and must be {DEFAULT_FREEZE_DATE}")
    required = {"dataset_split", "ap_date", "judge", "charge_id", "hr_coil_id", "hr_date"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"modeling cohort is missing columns: {', '.join(missing)}")
    train = frame.loc[frame["dataset_split"].eq("TRAIN")].copy()
    validation = frame.loc[frame["dataset_split"].eq("VALIDATION")].copy()
    validation = validation.loc[
        baseline.validation_as_of_mask(validation, freeze_date)
    ].copy()
    development = pd.concat([train, validation], ignore_index=True)
    if development.empty or set(development["judge"].unique()) != {"양품", "불량"}:
        raise ValueError("development cohort must contain both labels")
    if development["hr_coil_id"].duplicated().any():
        raise ValueError("development cohort must contain one row per hr_coil_id")
    return development.reset_index(drop=True)


def add_engineered_features(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    return add_shared_engineered_features(frame)


def development_feature_schema(frame: pd.DataFrame) -> dict[str, object]:
    train = frame.loc[frame["dataset_split"].eq("TRAIN")].reset_index(drop=True)
    numeric, categorical, excluded = baseline.select_feature_columns(train)
    eligible = set(numeric) | set(categorical)
    included = [column for column in train.columns if column in eligible]
    return {
        "included": [*included, *ENGINEERED_FEATURES],
        "numeric": [*numeric, *ENGINEERED_FEATURES],
        "categorical": categorical,
        "excluded": excluded,
        "engineered": list(ENGINEERED_FEATURES),
        "target": "judge",
        "positive_label": "불량",
    }


def _validate_fold(
    frame: pd.DataFrame,
    fit_index: np.ndarray,
    score_index: np.ndarray,
    *,
    label: str,
) -> None:
    fit_charges = set(frame.iloc[fit_index]["charge_id"])
    score_charges = set(frame.iloc[score_index]["charge_id"])
    if fit_charges & score_charges:
        raise ValueError(f"{label} contains charge_id overlap")
    for name, index in (("fit", fit_index), ("score", score_index)):
        if set(frame.iloc[index]["judge"].unique()) != {"양품", "불량"}:
            raise ValueError(f"{label} {name} partition must contain both labels")


def group_oof_splits(
    frame: pd.DataFrame,
    n_splits: int = 5,
    random_state: int = 42,
) -> list[tuple[np.ndarray, np.ndarray]]:
    truth = frame["judge"].eq("불량").astype(int).to_numpy()
    groups = frame["charge_id"].astype(str).to_numpy()
    splitter = StratifiedGroupKFold(
        n_splits=n_splits,
        shuffle=True,
        random_state=random_state,
    )
    result: list[tuple[np.ndarray, np.ndarray]] = []
    scored = np.zeros(len(frame), dtype=int)
    for fold, (fit_index, score_index) in enumerate(
        splitter.split(np.zeros((len(frame), 1)), truth, groups)
    ):
        fit_index = np.asarray(fit_index, dtype=int)
        score_index = np.asarray(score_index, dtype=int)
        _validate_fold(frame, fit_index, score_index, label=f"group fold {fold}")
        scored[score_index] += 1
        result.append((fit_index, score_index))
    if not np.array_equal(scored, np.ones(len(frame), dtype=int)):
        raise ValueError("every development row must receive one OOF score")
    return result


def expanding_time_splits(
    frame: pd.DataFrame,
) -> list[tuple[np.ndarray, np.ndarray]]:
    dates = pd.to_datetime(frame["hr_date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any():
        raise ValueError("development hr_date contains invalid dates")
    windows = (
        (pd.Timestamp("2025-07-16"), pd.Timestamp("2025-08-01")),
        (pd.Timestamp("2025-08-01"), pd.Timestamp("2025-09-01")),
    )
    result: list[tuple[np.ndarray, np.ndarray]] = []
    for fold, (boundary, end) in enumerate(windows):
        fit_mask = dates.lt(boundary)
        score_mask = dates.ge(boundary) & dates.lt(end)
        fit_charges = set(frame.loc[fit_mask, "charge_id"])
        score_charges = set(frame.loc[score_mask, "charge_id"])
        crossing = fit_charges & score_charges
        if crossing:
            fit_mask &= ~frame["charge_id"].isin(crossing)
            score_mask &= ~frame["charge_id"].isin(crossing)
        fit_index = np.flatnonzero(fit_mask.to_numpy())
        score_index = np.flatnonzero(score_mask.to_numpy())
        if len(fit_index) == 0 or len(score_index) == 0:
            raise ValueError(f"time fold {fold} is empty")
        _validate_fold(frame, fit_index, score_index, label=f"time fold {fold}")
        result.append((fit_index, score_index))
    return result


def _pipeline_factory(
    classifier_factory: Callable[[], object],
    numeric: list[str],
    categorical: list[str],
    *,
    scale_numeric: bool,
    dense_output: bool,
) -> Callable[[], Pipeline]:
    def factory() -> Pipeline:
        return Pipeline(
            [
                (
                    "preprocessor",
                    challenger._build_preprocessor(
                        numeric,
                        categorical,
                        scale_numeric=scale_numeric,
                        dense_output=dense_output,
                    ),
                ),
                ("classifier", classifier_factory()),
            ]
        )

    return factory


def candidate_factories(
    base_numeric: list[str],
    engineered_numeric: list[str],
    categorical: list[str],
    *,
    random_state: int = 42,
    n_jobs: int = 1,
    tree_estimators: int = 300,
) -> dict[str, Callable[[], Pipeline]]:
    if tree_estimators < 1:
        raise ValueError("tree_estimators must be positive")
    factories: dict[str, Callable[[], Pipeline]] = {}

    def logistic_factory(
        numeric: list[str],
        *,
        regularization: float,
        l1_ratio: float,
        solver: str,
    ) -> Callable[[], Pipeline]:
        return _pipeline_factory(
            lambda: LogisticRegression(
                C=regularization,
                l1_ratio=l1_ratio,
                class_weight="balanced",
                max_iter=3000,
                random_state=random_state,
                solver=solver,
            ),
            numeric,
            categorical,
            scale_numeric=True,
            dense_output=False,
        )

    factories["logistic_l2_c0_1_base"] = logistic_factory(
        base_numeric, regularization=0.1, l1_ratio=0.0, solver="liblinear"
    )
    for label, regularization in (("0_03", 0.03), ("0_3", 0.3), ("1", 1.0)):
        factories[f"logistic_l2_c{label}_engineered"] = logistic_factory(
            engineered_numeric,
            regularization=regularization,
            l1_ratio=0.0,
            solver="liblinear",
        )
    for label, regularization, ratio in (
        ("c0_03_l1r0_25", 0.03, 0.25),
        ("c0_1_l1r0_25", 0.1, 0.25),
        ("c0_1_l1r0_5", 0.1, 0.5),
        ("c0_3_l1r0_5", 0.3, 0.5),
    ):
        factories[f"elastic_net_{label}"] = logistic_factory(
            engineered_numeric,
            regularization=regularization,
            l1_ratio=ratio,
            solver="saga",
        )

    for label, depth, leaf in (
        ("depth6_leaf10", 6, 10),
        ("depth10_leaf20", 10, 20),
        ("unbounded_leaf30", None, 30),
    ):
        factories[f"extra_trees_{label}"] = _pipeline_factory(
            lambda depth=depth, leaf=leaf: ExtraTreesClassifier(
                n_estimators=tree_estimators,
                class_weight="balanced",
                max_depth=depth,
                min_samples_leaf=leaf,
                max_features="sqrt",
                random_state=random_state,
                n_jobs=n_jobs,
            ),
            engineered_numeric,
            categorical,
            scale_numeric=False,
            dense_output=False,
        )
    for label, depth, leaf in (
        ("depth6_leaf10", 6, 10),
        ("depth10_leaf20", 10, 20),
    ):
        factories[f"random_forest_{label}"] = _pipeline_factory(
            lambda depth=depth, leaf=leaf: RandomForestClassifier(
                n_estimators=tree_estimators,
                class_weight="balanced_subsample",
                max_depth=depth,
                min_samples_leaf=leaf,
                max_features="sqrt",
                random_state=random_state,
                n_jobs=n_jobs,
            ),
            engineered_numeric,
            categorical,
            scale_numeric=False,
            dense_output=False,
        )
    for label, leaves, leaf_rows in (
        ("leaves7_leaf20", 7, 20),
        ("leaves15_leaf20", 15, 20),
        ("leaves15_leaf40", 15, 40),
    ):
        factories[f"hist_gb_{label}"] = _pipeline_factory(
            lambda leaves=leaves, leaf_rows=leaf_rows: HistGradientBoostingClassifier(
                learning_rate=0.05,
                max_iter=250,
                max_leaf_nodes=leaves,
                min_samples_leaf=leaf_rows,
                l2_regularization=1.0,
                class_weight="balanced",
                random_state=random_state,
            ),
            engineered_numeric,
            categorical,
            scale_numeric=False,
            dense_output=True,
        )
    if len(factories) > 20:
        raise RuntimeError("nextgen candidate grid exceeds the 20-model safety bound")
    return factories


def select_nextgen(
    leaderboard: pd.DataFrame,
    incumbent_name: str = "logistic_l2_c0_1_base",
    min_pr_auc_improvement: float = 0.005,
) -> dict[str, object]:
    required = {"model", "oof_pr_auc", "oof_roc_auc", "worst_time_pr_auc"}
    missing = sorted(required - set(leaderboard.columns))
    if missing:
        raise ValueError(f"nextgen leaderboard is missing columns: {', '.join(missing)}")
    if min_pr_auc_improvement < 0:
        raise ValueError("min_pr_auc_improvement must be non-negative")
    incumbent_rows = leaderboard.loc[leaderboard["model"].eq(incumbent_name)]
    if len(incumbent_rows) != 1:
        raise ValueError("nextgen leaderboard must contain exactly one incumbent algorithm")
    ranked = leaderboard.sort_values(
        ["oof_pr_auc", "oof_roc_auc", "worst_time_pr_auc", "model"],
        ascending=[False, False, False, True],
        kind="stable",
    )
    incumbent_pr_auc = float(incumbent_rows.iloc[0]["oof_pr_auc"])
    best = ranked.iloc[0]
    best_name = str(best["model"])
    improvement = float(best["oof_pr_auc"] - incumbent_pr_auc)
    adopt = best_name != incumbent_name and improvement >= min_pr_auc_improvement
    return {
        "status": "ADOPT_NEXT_CHALLENGER" if adopt else "RETAIN_SHADOW_INCUMBENT_ALGORITHM",
        "recommended_model": best_name if adopt else incumbent_name,
        "best_candidate_model": best_name,
        "incumbent_model": incumbent_name,
        "incumbent_oof_pr_auc": incumbent_pr_auc,
        "best_candidate_oof_pr_auc": float(best["oof_pr_auc"]),
        "oof_pr_auc_improvement": improvement,
        "min_pr_auc_improvement": float(min_pr_auc_improvement),
    }


def _candidate_variant(model_name: str) -> str:
    return "base" if model_name == "logistic_l2_c0_1_base" else "engineered"


def _fit_and_score(
    factory: Callable[[], Pipeline],
    features: pd.DataFrame,
    target: pd.Series,
    fit_index: np.ndarray,
    score_index: np.ndarray,
) -> np.ndarray:
    pipeline = factory()
    pipeline.fit(features.iloc[fit_index], target.iloc[fit_index])
    return np.asarray(pipeline.predict_proba(features.iloc[score_index]), dtype=float)[:, 1]


def _fit_score_and_reference(
    factory: Callable[[], Pipeline],
    features: pd.DataFrame,
    target: pd.Series,
    fit_index: np.ndarray,
    score_index: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    pipeline = factory()
    pipeline.fit(features.iloc[fit_index], target.iloc[fit_index])
    score = np.asarray(
        pipeline.predict_proba(features.iloc[score_index]), dtype=float
    )[:, 1]
    reference = np.asarray(
        pipeline.predict_proba(features.iloc[fit_index]), dtype=float
    )[:, 1]
    return score, reference


def _atomic_joblib_dump(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        joblib.dump(value, temporary_path)
        with temporary_path.open("rb") as source:
            os.fsync(source.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            raise FileExistsError(path) from None
    finally:
        temporary_path.unlink(missing_ok=True)


def _model_card(summary: dict[str, object]) -> str:
    recommendation = summary["recommendation"]
    return "\n".join(
        [
            "# Steel NextGen Model Card",
            "",
            "## 1. 고2 수준 비유",
            "",
            "기존 선수와 새 선수들을 답을 보지 않는 교차 모의고사로 비교했습니다.",
            f"현재 선택은 `{recommendation['recommended_model']}`이지만 미래 경기 전에는 출전 금지입니다.",
            "",
            "## 2. 전문 용어 매핑",
            "",
            "- 교차 모의고사: charge_id Stratified Group OOF",
            "- 시간대별 원정 경기: expanding-window stability folds",
            "- 출전 금지: deployment_eligible=false",
            "- 다음 공식 경기: FUTURE_SHADOW_VALIDATION",
            "",
        ]
    )


def _json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run_nextgen(
    modeling_cohort_csv: Path,
    output_dir: Path,
    *,
    random_state: int = 42,
    n_jobs: int = 1,
    tree_estimators: int = 300,
    n_splits: int = 5,
    candidate_names: Iterable[str] | None = None,
    include_rank_ensemble: bool = True,
    min_pr_auc_improvement: float = 0.005,
) -> dict[str, object]:
    input_path = Path(modeling_cohort_csv)
    frame = pd.read_csv(
        input_path,
        encoding="utf-8-sig",
        dtype={"charge_id": "string", "slab_no": "string", "hr_coil_id": "string"},
    )
    baseline._validate_modeling_frame(frame)
    schema = development_feature_schema(frame)
    development = development_cohort(frame)
    development, engineered_names = add_engineered_features(development)
    base_numeric = [
        feature for feature in schema["numeric"] if feature not in engineered_names
    ]
    engineered_numeric = list(schema["numeric"])
    categorical = list(schema["categorical"])
    base_features = baseline._prepare_features(
        development, base_numeric, categorical
    )
    engineered_features = baseline._prepare_features(
        development, engineered_numeric, categorical
    )
    feature_frames = {"base": base_features, "engineered": engineered_features}
    target = development["judge"].eq("불량").astype(int)
    group_splits = group_oof_splits(
        development, n_splits=n_splits, random_state=random_state
    )
    time_splits = expanding_time_splits(development)
    factories = candidate_factories(
        base_numeric,
        engineered_numeric,
        categorical,
        random_state=random_state,
        n_jobs=n_jobs,
        tree_estimators=tree_estimators,
    )
    selected_names = tuple(factories) if candidate_names is None else tuple(candidate_names)
    if not selected_names or len(set(selected_names)) != len(selected_names):
        raise ValueError("candidate_names must be non-empty and unique")
    unknown = sorted(set(selected_names) - set(factories))
    if unknown:
        raise ValueError(f"unknown NextGen candidates: {', '.join(unknown)}")
    if "logistic_l2_c0_1_base" not in selected_names:
        raise ValueError("candidate_names must include logistic_l2_c0_1_base")

    fold_ids = np.full(len(development), -1, dtype=int)
    for fold, (_, score_index) in enumerate(group_splits):
        fold_ids[score_index] = fold
    oof_scores: dict[str, np.ndarray] = {}
    leaderboard_rows: list[dict[str, object]] = []
    time_rows: list[dict[str, object]] = []
    time_scores: dict[tuple[str, int], np.ndarray] = {}
    time_references: dict[tuple[str, int], np.ndarray] = {}
    for model_name in selected_names:
        variant = _candidate_variant(model_name)
        features = feature_frames[variant]
        scores = np.full(len(development), np.nan)
        for fit_index, score_index in group_splits:
            scores[score_index] = _fit_and_score(
                factories[model_name], features, target, fit_index, score_index
            )
        if not np.isfinite(scores).all():
            raise ValueError(f"candidate {model_name} did not produce complete finite OOF scores")
        oof_scores[model_name] = scores
        fold_pr_auc: list[float] = []
        for fold, (fit_index, score_index) in enumerate(time_splits):
            current_scores, current_reference = _fit_score_and_reference(
                factories[model_name], features, target, fit_index, score_index
            )
            time_scores[(model_name, fold)] = current_scores
            time_references[(model_name, fold)] = current_reference
            truth = target.iloc[score_index]
            pr_auc = float(average_precision_score(truth, current_scores))
            fold_pr_auc.append(pr_auc)
            time_rows.append(
                {
                    "model": model_name,
                    "fold": fold,
                    "fit_rows": int(len(fit_index)),
                    "score_rows": int(len(score_index)),
                    "pr_auc": pr_auc,
                    "roc_auc": float(roc_auc_score(truth, current_scores)),
                }
            )
        leaderboard_rows.append(
            {
                "model": model_name,
                "feature_variant": variant,
                "oof_pr_auc": float(average_precision_score(target, scores)),
                "oof_roc_auc": float(roc_auc_score(target, scores)),
                "oof_brier_score": float(brier_score_loss(target, scores)),
                "worst_time_pr_auc": float(min(fold_pr_auc)),
            }
        )

    ensemble_children: tuple[str, str] | None = None
    if include_rank_ensemble and len(selected_names) >= 2:
        base_leaderboard = pd.DataFrame(leaderboard_rows).sort_values(
            ["oof_pr_auc", "oof_roc_auc", "model"],
            ascending=[False, False, True],
            kind="stable",
        )
        ensemble_children = (
            str(base_leaderboard.iloc[0]["model"]),
            str(base_leaderboard.iloc[1]["model"]),
        )
        first_oof = frozen_percentile(
            oof_scores[ensemble_children[0]], oof_scores[ensemble_children[0]]
        )
        second_oof = frozen_percentile(
            oof_scores[ensemble_children[1]], oof_scores[ensemble_children[1]]
        )
        ensemble_scores = 0.5 * (first_oof + second_oof)
        ensemble_name = "rank_average_top2"
        oof_scores[ensemble_name] = ensemble_scores
        fold_pr_auc = []
        for fold, (_, score_index) in enumerate(time_splits):
            first = frozen_percentile(
                time_scores[(ensemble_children[0], fold)],
                time_references[(ensemble_children[0], fold)],
            )
            second = frozen_percentile(
                time_scores[(ensemble_children[1], fold)],
                time_references[(ensemble_children[1], fold)],
            )
            current = 0.5 * (first + second)
            truth = target.iloc[score_index]
            pr_auc = float(average_precision_score(truth, current))
            fold_pr_auc.append(pr_auc)
            time_rows.append(
                {
                    "model": ensemble_name,
                    "fold": fold,
                    "fit_rows": int(len(time_splits[fold][0])),
                    "score_rows": int(len(score_index)),
                    "pr_auc": pr_auc,
                    "roc_auc": float(roc_auc_score(truth, current)),
                }
            )
        leaderboard_rows.append(
            {
                "model": ensemble_name,
                "feature_variant": "engineered_union",
                "oof_pr_auc": float(average_precision_score(target, ensemble_scores)),
                "oof_roc_auc": float(roc_auc_score(target, ensemble_scores)),
                "oof_brier_score": float(brier_score_loss(target, ensemble_scores)),
                "worst_time_pr_auc": float(min(fold_pr_auc)),
            }
        )

    leaderboard = pd.DataFrame(leaderboard_rows)
    recommendation = select_nextgen(
        leaderboard,
        min_pr_auc_improvement=min_pr_auc_improvement,
    )
    recommended_model = str(recommendation["recommended_model"])
    leaderboard["recommended"] = leaderboard["model"].eq(recommended_model)
    leaderboard = leaderboard.sort_values(
        ["oof_pr_auc", "oof_roc_auc", "worst_time_pr_auc", "model"],
        ascending=[False, False, False, True],
        kind="stable",
    ).reset_index(drop=True)
    selected_scores = oof_scores[recommended_model]
    thresholds: dict[str, float] = {}
    capacity_rows: list[dict[str, object]] = []
    for cap in (0.10, 0.15, 0.20):
        threshold, metrics = challenger.select_threshold_at_fpr_cap(
            target, selected_scores, false_alarm_cap=cap
        )
        thresholds[str(cap)] = float(threshold)
        capacity_rows.append(
            {
                "false_alarm_cap": cap,
                "selected_threshold": float(threshold),
                "actual_false_alarm_rate": float(metrics["false_alarm_rate"]),
                "recall": float(metrics["recall"]),
                "precision": float(metrics["precision"]),
                **metrics["confusion_matrix"],
            }
        )
    policy_threshold = thresholds["0.15"]

    if recommended_model == "rank_average_top2":
        if ensemble_children is None:
            raise RuntimeError("rank ensemble children are missing")
        fitted_children = []
        for child in ensemble_children:
            variant = _candidate_variant(child)
            pipeline = factories[child]()
            pipeline.fit(feature_frames[variant], target)
            fitted_children.append(pipeline)
        predictor = FrozenRankAverageEnsemble(
            fitted_children[0],
            fitted_children[1],
            oof_scores[ensemble_children[0]],
            oof_scores[ensemble_children[1]],
            [*engineered_numeric, *categorical],
            integrity_probe=engineered_features.iloc[
                np.linspace(
                    0,
                    len(engineered_features) - 1,
                    num=min(128, len(engineered_features)),
                    dtype=int,
                )
            ].reset_index(drop=True),
        )
        selected_schema = schema
    else:
        selected_variant = _candidate_variant(recommended_model)
        predictor = factories[recommended_model]()
        predictor.fit(feature_frames[selected_variant], target)
        if selected_variant == "base":
            selected_schema = {
                **schema,
                "included": [*base_numeric, *categorical],
                "numeric": base_numeric,
                "engineered": [],
            }
        else:
            selected_schema = {
                **schema,
                "included": [*engineered_numeric, *categorical],
            }

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    model_path = output_path / "nextgen_model.joblib"
    software = {
        "scikit_learn": sklearn.__version__,
        "pandas": pd.__version__,
        "numpy": np.__version__,
    }
    selected_oof_sha256 = numeric_array_sha256(selected_scores)
    fold_assignments_sha256 = numeric_array_sha256(fold_ids)
    development_identity_sha256 = hashlib.sha256(
        development[["charge_id", "hr_coil_id", "judge"]]
        .to_csv(index=False)
        .encode("utf-8")
    ).hexdigest()
    feature_schema_sha256 = _json_sha256(selected_schema)
    selected_ensemble_children = (
        ensemble_children if recommended_model == "rank_average_top2" else None
    )
    model_artifact: dict[str, object] = {
        "predictor": predictor,
        "model_name": recommended_model,
        "nextgen_version": NEXTGEN_VERSION,
        "feature_schema": selected_schema,
        "policy_fpr_cap": 0.15,
        "policy_threshold": policy_threshold,
        "selected_thresholds_by_fpr_cap": thresholds,
        "positive_label": "불량",
        "modeling_cohort_sha256": sha256_file(input_path),
        "holdout_used_for_selection": False,
        "deployment_eligible": False,
        "required_next_step": "FUTURE_SHADOW_VALIDATION",
        "ensemble_children": (
            None
            if selected_ensemble_children is None
            else list(selected_ensemble_children)
        ),
        "ensemble_integrity": (
            None
            if selected_ensemble_children is None
            else ensemble_integrity_metadata(predictor)
        ),
        "selection_oof_scores_sha256": selected_oof_sha256,
        "oof_fold_assignments_sha256": fold_assignments_sha256,
        "development_identity_sha256": development_identity_sha256,
        "feature_schema_sha256": feature_schema_sha256,
        "predictor_source_sha256": sha256_file(
            Path(__file__).with_name("nextgen_model.py")
        ),
        "software": software,
    }
    validate_ensemble_integrity(model_artifact)
    _atomic_joblib_dump(model_artifact, model_path)
    calibrator = fit_calibrator("platt", selected_scores, target.to_numpy(dtype=int))
    calibrator_path = output_path / "nextgen_calibrator.joblib"
    _atomic_joblib_dump(
        {
            "method": "platt",
            "calibrator": calibrator,
            "model_sha256": sha256_file(model_path),
            "modeling_cohort_sha256": sha256_file(input_path),
            "selection_oof_scores_sha256": selected_oof_sha256,
            "oof_fold_assignments_sha256": fold_assignments_sha256,
            "development_identity_sha256": development_identity_sha256,
            "feature_schema_sha256": feature_schema_sha256,
            "class_order": [0, 1],
            "software": software,
            "holdout_used_for_selection": False,
            "deployment_eligible": False,
        },
        calibrator_path,
    )
    oof_rows = []
    for model_name, scores in oof_scores.items():
        for index, score in enumerate(scores):
            oof_rows.append(
                {
                    "charge_id": development.loc[index, "charge_id"],
                    "hr_coil_id": development.loc[index, "hr_coil_id"],
                    "judge": development.loc[index, "judge"],
                    "y_true": int(target.iloc[index]),
                    "fold_id": int(fold_ids[index]),
                    "model": model_name,
                    "risk_score": float(score),
                }
            )
    oof_frame = pd.DataFrame(oof_rows)
    time_frame = pd.DataFrame(time_rows)
    capacity_frame = pd.DataFrame(capacity_rows)
    atomic_write_csv(oof_frame, output_path / "nextgen_oof_predictions.csv")
    atomic_write_csv(leaderboard, output_path / "nextgen_leaderboard.csv")
    atomic_write_csv(time_frame, output_path / "nextgen_time_stability.csv")
    atomic_write_csv(capacity_frame, output_path / "nextgen_alert_capacity.csv")
    atomic_write_json(selected_schema, output_path / "nextgen_feature_schema.json")
    summary: dict[str, object] = {
        "nextgen_version": NEXTGEN_VERSION,
        "source": {
            "modeling_cohort": str(input_path),
            "modeling_cohort_sha256": sha256_file(input_path),
        },
        "software": software,
        "development_rows": int(len(development)),
        "development_positive_labels": int(target.sum()),
        "group_oof_folds": int(n_splits),
        "time_folds": int(len(time_splits)),
        "candidate_models": list(oof_scores),
        "leaderboard": leaderboard.to_dict(orient="records"),
        "recommendation": recommendation,
        "selected_thresholds_by_fpr_cap": thresholds,
        "policy_threshold": policy_threshold,
        "model_sha256": sha256_file(model_path),
        "calibrator_sha256": sha256_file(calibrator_path),
        "holdout_used_for_selection": False,
        "deployment_eligible": False,
        "required_next_step": "FUTURE_SHADOW_VALIDATION",
    }
    atomic_write_json(summary, output_path / "nextgen_metrics.json")
    atomic_write_text(_model_card(summary), output_path / "nextgen_model_card.md")
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train HOLDOUT-independent next-generation steel quality models."
    )
    parser.add_argument("--modeling-cohort", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--n-jobs", type=int, default=1)
    parser.add_argument("--tree-estimators", type=int, default=300)
    parser.add_argument("--n-splits", type=int, default=5)
    parser.add_argument("--min-pr-auc-improvement", type=float, default=0.005)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = run_nextgen(
        Path(args.modeling_cohort),
        Path(args.output_dir),
        random_state=args.random_state,
        n_jobs=args.n_jobs,
        tree_estimators=args.tree_estimators,
        n_splits=args.n_splits,
        min_pr_auc_improvement=args.min_pr_auc_improvement,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
