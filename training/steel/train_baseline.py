from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from pandas.api.types import is_numeric_dtype
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    fbeta_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


BASELINE_VERSION = "steel-quality-baseline-v0.3"
DEFAULT_VALIDATION_FREEZE_DATE = "2025-09-01"
POSITIVE_LABEL = "불량"
NEGATIVE_LABEL = "양품"
SPLIT_NAMES = ("TRAIN", "VALIDATION", "HOLDOUT")
IDENTITY_COLUMNS = {"charge_id", "slab_no", "hr_coil_id"}
RAW_TIME_COLUMNS = {"cast_date", "f_ext_date", "hr_date"}
CONTROL_COLUMNS = {"judge", "dataset_split"}
REDUNDANT_GAS_RATIO_COLUMNS = {"f_bfg_per", "f_cog_per", "f_ldg_per"}


def select_feature_columns(
    frame: pd.DataFrame,
) -> tuple[list[str], list[str], list[str]]:
    excluded_set = (
        IDENTITY_COLUMNS
        | RAW_TIME_COLUMNS
        | CONTROL_COLUMNS
        | REDUNDANT_GAS_RATIO_COLUMNS
    )
    excluded = [
        column
        for column in frame.columns
        if column in excluded_set or column.startswith("ap_") or column.startswith("dq_")
    ]
    candidates = [column for column in frame.columns if column not in excluded]
    numeric = []
    for column in candidates:
        series = frame[column]
        non_missing = series.dropna()
        if is_numeric_dtype(series.dtype) or (
            not non_missing.empty
            and pd.to_numeric(non_missing, errors="coerce").notna().all()
        ):
            numeric.append(column)
    categorical = [column for column in candidates if column not in numeric]
    if not numeric and not categorical:
        raise ValueError("no eligible upstream feature columns remain after leakage exclusions")
    return numeric, categorical, excluded


def validation_as_of_mask(
    validation: pd.DataFrame,
    freeze_date: str | pd.Timestamp,
) -> pd.Series:
    if "ap_date" not in validation:
        raise ValueError("validation frame is missing label-availability proxy: ap_date")
    parsed = pd.to_datetime(validation["ap_date"], format="%Y-%m-%d", errors="coerce")
    if parsed.isna().any():
        raise ValueError(f"validation ap_date contains {int(parsed.isna().sum())} invalid dates")
    cutoff = pd.Timestamp(freeze_date)
    return parsed.lt(cutoff)


def binary_metrics(
    y_true: pd.Series | np.ndarray,
    risk_score: pd.Series | np.ndarray,
    *,
    threshold: float,
) -> dict[str, object]:
    truth = np.asarray(y_true, dtype=int)
    scores = np.asarray(risk_score, dtype=float)
    predictions = (scores >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(truth, predictions, labels=[0, 1]).ravel()
    negative_total = tn + fp
    return {
        "threshold": float(threshold),
        "roc_auc": float(roc_auc_score(truth, scores)),
        "pr_auc": float(average_precision_score(truth, scores)),
        "brier_score": float(brier_score_loss(truth, scores)),
        "precision": float(precision_score(truth, predictions, zero_division=0)),
        "recall": float(recall_score(truth, predictions, zero_division=0)),
        "f1": float(f1_score(truth, predictions, zero_division=0)),
        "f2": float(fbeta_score(truth, predictions, beta=2, zero_division=0)),
        "false_alarm_rate": 0.0 if negative_total == 0 else float(fp / negative_total),
        "alert_rate": float(predictions.mean()),
        "positive_rate": float(truth.mean()),
        "confusion_matrix": {
            "tn": int(tn),
            "fp": int(fp),
            "fn": int(fn),
            "tp": int(tp),
        },
    }


def select_f2_threshold(
    y_true: pd.Series | np.ndarray,
    risk_score: pd.Series | np.ndarray,
) -> tuple[float, dict[str, object]]:
    truth = np.asarray(y_true, dtype=int)
    scores = np.asarray(risk_score, dtype=float)
    if set(np.unique(truth)) != {0, 1}:
        raise ValueError("threshold selection requires both positive and negative validation labels")
    candidates = sorted(set(float(score) for score in scores))
    candidates.append(float(np.nextafter(max(candidates), np.inf)))
    evaluated = [(threshold, binary_metrics(truth, scores, threshold=threshold)) for threshold in candidates]
    threshold, metrics = max(
        evaluated,
        key=lambda item: (
            item[1]["f2"],
            item[1]["recall"],
            item[1]["precision"],
            item[0],
        ),
    )
    return float(threshold), metrics


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_modeling_frame(frame: pd.DataFrame) -> None:
    required = {*IDENTITY_COLUMNS, "hr_date", "judge", "dataset_split"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"modeling cohort is missing required columns: {', '.join(missing)}")
    invalid_labels = sorted(set(frame["judge"].dropna()) - {NEGATIVE_LABEL, POSITIVE_LABEL})
    if frame["judge"].isna().any() or invalid_labels:
        shown = ["<MISSING>"] if frame["judge"].isna().any() else []
        shown.extend(str(value) for value in invalid_labels)
        raise ValueError(f"judge contains unsupported values: {', '.join(shown)}")
    invalid_splits = sorted(set(frame["dataset_split"].dropna()) - set(SPLIT_NAMES))
    if frame["dataset_split"].isna().any() or invalid_splits:
        shown = ["<MISSING>"] if frame["dataset_split"].isna().any() else []
        shown.extend(str(value) for value in invalid_splits)
        raise ValueError(f"dataset_split contains unsupported values: {', '.join(shown)}")
    if frame["hr_coil_id"].duplicated().any():
        raise ValueError("modeling cohort must contain one row per hr_coil_id")
    for split_name in SPLIT_NAMES:
        group = frame.loc[frame["dataset_split"].eq(split_name)]
        if group.empty:
            raise ValueError(f"modeling cohort has no {split_name} rows")
        if set(group["judge"].unique()) != {NEGATIVE_LABEL, POSITIVE_LABEL}:
            raise ValueError(f"{split_name} must contain both 양품 and 불량 labels")


def _split_charge_overlap(frame: pd.DataFrame) -> int:
    charges = {
        split_name: set(frame.loc[frame["dataset_split"].eq(split_name), "charge_id"])
        for split_name in SPLIT_NAMES
    }
    overlap = (
        (charges["TRAIN"] & charges["VALIDATION"])
        | (charges["TRAIN"] & charges["HOLDOUT"])
        | (charges["VALIDATION"] & charges["HOLDOUT"])
    )
    return len(overlap)


def _prepare_features(
    frame: pd.DataFrame,
    numeric: list[str],
    categorical: list[str],
) -> pd.DataFrame:
    prepared = frame[[*numeric, *categorical]].copy()
    for column in numeric:
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    for column in categorical:
        prepared[column] = prepared[column].astype("object")
        prepared.loc[prepared[column].isna(), column] = np.nan
    return prepared


def _build_pipeline(
    model_name: str,
    numeric: list[str],
    categorical: list[str],
    *,
    random_state: int,
    random_forest_estimators: int,
    n_jobs: int,
) -> Pipeline:
    numeric_steps: list[tuple[str, object]] = [
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True))
    ]
    if model_name == "logistic_regression":
        numeric_steps.append(("scaler", StandardScaler()))
    transformers: list[tuple[str, object, list[str]]] = []
    if numeric:
        transformers.append(("numeric", Pipeline(numeric_steps), numeric))
    if categorical:
        categorical_pipeline = Pipeline(
            [
                (
                    "imputer",
                    SimpleImputer(
                        strategy="most_frequent",
                        keep_empty_features=True,
                    ),
                ),
                ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=True)),
            ]
        )
        transformers.append(("categorical", categorical_pipeline, categorical))
    preprocessor = ColumnTransformer(transformers, remainder="drop")

    if model_name == "logistic_regression":
        classifier = LogisticRegression(
            class_weight="balanced",
            max_iter=3000,
            random_state=random_state,
            solver="liblinear",
        )
    elif model_name == "random_forest":
        classifier = RandomForestClassifier(
            n_estimators=random_forest_estimators,
            class_weight="balanced_subsample",
            max_depth=8,
            min_samples_leaf=20,
            max_features="sqrt",
            random_state=random_state,
            n_jobs=n_jobs,
        )
    else:
        raise ValueError(f"unsupported model: {model_name}")
    return Pipeline([("preprocessor", preprocessor), ("classifier", classifier)])


def _flat_comparison_row(model_name: str, metrics: dict[str, object]) -> dict[str, object]:
    confusion = metrics["confusion_matrix"]
    return {
        "model": model_name,
        "validation_threshold": metrics["threshold"],
        "validation_pr_auc": metrics["pr_auc"],
        "validation_roc_auc": metrics["roc_auc"],
        "validation_brier_score": metrics["brier_score"],
        "validation_precision": metrics["precision"],
        "validation_recall": metrics["recall"],
        "validation_f1": metrics["f1"],
        "validation_f2": metrics["f2"],
        "validation_false_alarm_rate": metrics["false_alarm_rate"],
        "validation_alert_rate": metrics["alert_rate"],
        "validation_tn": confusion["tn"],
        "validation_fp": confusion["fp"],
        "validation_fn": confusion["fn"],
        "validation_tp": confusion["tp"],
    }


def _source_feature(transformed_name: str, categorical: list[str]) -> str:
    if transformed_name.startswith("numeric__"):
        return transformed_name.removeprefix("numeric__")
    remainder = transformed_name.removeprefix("categorical__")
    for column in sorted(categorical, key=len, reverse=True):
        if remainder == column or remainder.startswith(f"{column}_"):
            return column
    return remainder


def _feature_associations(
    pipeline: Pipeline,
    model_name: str,
    categorical: list[str],
    *,
    limit: int = 30,
) -> pd.DataFrame:
    transformed_names = pipeline.named_steps["preprocessor"].get_feature_names_out()
    classifier = pipeline.named_steps["classifier"]
    if model_name == "logistic_regression":
        signed_values = np.asarray(classifier.coef_[0], dtype=float)
        importance = np.abs(signed_values)
        direction = np.where(signed_values >= 0, "positive", "negative")
    else:
        signed_values = np.full(len(transformed_names), np.nan)
        importance = np.asarray(classifier.feature_importances_, dtype=float)
        direction = np.full(len(transformed_names), "non_directional", dtype=object)
    associations = pd.DataFrame(
        {
            "transformed_feature": transformed_names,
            "source_feature": [
                _source_feature(str(name), categorical) for name in transformed_names
            ],
            "importance": importance,
            "signed_value": signed_values,
            "direction": direction,
        }
    )
    associations = associations.sort_values("importance", ascending=False, kind="stable").head(limit)
    associations.insert(0, "rank", range(1, len(associations) + 1))
    associations.insert(1, "model", model_name)
    return associations.reset_index(drop=True)


def _prediction_frame(
    source: pd.DataFrame,
    y_true: pd.Series,
    risk_score: np.ndarray,
    threshold: float,
) -> pd.DataFrame:
    identity = [
        column
        for column in ("charge_id", "slab_no", "hr_coil_id", "hr_date", "judge")
        if column in source.columns
    ]
    predictions = source[identity].reset_index(drop=True).copy()
    predictions["y_true"] = np.asarray(y_true, dtype=int)
    predictions["risk_score"] = np.asarray(risk_score, dtype=float)
    predictions["decision_threshold"] = float(threshold)
    predictions["predicted_label"] = np.where(
        predictions["risk_score"].ge(threshold), POSITIVE_LABEL, NEGATIVE_LABEL
    )
    return predictions


def run_baseline(
    modeling_cohort_csv: str | Path,
    output_dir: str | Path,
    *,
    random_state: int = 42,
    random_forest_estimators: int = 300,
    n_jobs: int = -1,
    validation_freeze_date: str = DEFAULT_VALIDATION_FREEZE_DATE,
) -> dict[str, object]:
    if validation_freeze_date != DEFAULT_VALIDATION_FREEZE_DATE:
        raise ValueError(
            "validation_freeze_date is immutable and must be "
            f"{DEFAULT_VALIDATION_FREEZE_DATE}"
        )
    input_path = Path(modeling_cohort_csv)
    frame = pd.read_csv(
        input_path,
        encoding="utf-8-sig",
        dtype={"charge_id": "string", "slab_no": "string", "hr_coil_id": "string"},
    )
    _validate_modeling_frame(frame)
    overlap = _split_charge_overlap(frame)
    if overlap:
        raise ValueError(f"charge_id leakage detected across splits: {overlap} overlapping charges")

    split_frames = {
        split_name: frame.loc[frame["dataset_split"].eq(split_name)].reset_index(drop=True)
        for split_name in SPLIT_NAMES
    }
    numeric, categorical, excluded = select_feature_columns(split_frames["TRAIN"])
    included = [column for column in frame.columns if column in set(numeric) | set(categorical)]
    feature_schema = {
        "included": included,
        "numeric": numeric,
        "categorical": categorical,
        "excluded": excluded,
        "target": "judge",
        "positive_label": POSITIVE_LABEL,
    }

    features = {
        split_name: _prepare_features(group, numeric, categorical)
        for split_name, group in split_frames.items()
    }
    targets = {
        split_name: group["judge"].eq(POSITIVE_LABEL).astype(int)
        for split_name, group in split_frames.items()
    }
    freeze_timestamp = pd.to_datetime(
        validation_freeze_date,
        format="%Y-%m-%d",
        errors="coerce",
    )
    if pd.isna(freeze_timestamp):
        raise ValueError("validation_freeze_date must use YYYY-MM-DD format")
    freeze_date = freeze_timestamp.strftime("%Y-%m-%d")
    validation_selection_mask = validation_as_of_mask(
        split_frames["VALIDATION"], freeze_date
    )
    validation_selection_frame = split_frames["VALIDATION"].loc[
        validation_selection_mask
    ].reset_index(drop=True)
    validation_selection_features = features["VALIDATION"].loc[
        validation_selection_mask
    ].reset_index(drop=True)
    validation_selection_target = targets["VALIDATION"].loc[
        validation_selection_mask
    ].reset_index(drop=True)
    if set(validation_selection_target.unique()) != {0, 1}:
        raise ValueError("as-of Validation selection cohort must contain both labels")

    pipelines: dict[str, Pipeline] = {}
    validation_scores: dict[str, np.ndarray] = {}
    comparison_rows: list[dict[str, object]] = []
    for model_name in ("logistic_regression", "random_forest"):
        pipeline = _build_pipeline(
            model_name,
            numeric,
            categorical,
            random_state=random_state,
            random_forest_estimators=random_forest_estimators,
            n_jobs=n_jobs,
        )
        pipeline.fit(features["TRAIN"], targets["TRAIN"])
        scores = pipeline.predict_proba(validation_selection_features)[:, 1]
        default_metrics = binary_metrics(validation_selection_target, scores, threshold=0.5)
        pipelines[model_name] = pipeline
        validation_scores[model_name] = scores
        comparison_rows.append(_flat_comparison_row(model_name, default_metrics))

    comparison = pd.DataFrame(comparison_rows)
    selected_model = max(
        comparison_rows,
        key=lambda row: (row["validation_pr_auc"], row["validation_roc_auc"]),
    )["model"]
    comparison["selected_on_validation_pr_auc"] = comparison["model"].eq(selected_model)
    selected_pipeline = pipelines[selected_model]
    selected_threshold, validation_tuned_metrics = select_f2_threshold(
        validation_selection_target, validation_scores[selected_model]
    )

    holdout_score = selected_pipeline.predict_proba(features["HOLDOUT"])[:, 1]
    holdout_metrics = binary_metrics(
        targets["HOLDOUT"], holdout_score, threshold=selected_threshold
    )
    all_good_scores = np.zeros(len(targets["HOLDOUT"]), dtype=float)
    all_good_metrics = binary_metrics(
        targets["HOLDOUT"], all_good_scores, threshold=0.5
    )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(
        output_path / "validation_model_comparison.csv", index=False, encoding="utf-8-sig"
    )
    _prediction_frame(
        validation_selection_frame,
        validation_selection_target,
        validation_scores[selected_model],
        selected_threshold,
    ).to_csv(output_path / "validation_predictions.csv", index=False, encoding="utf-8-sig")
    _prediction_frame(
        split_frames["HOLDOUT"], targets["HOLDOUT"], holdout_score, selected_threshold
    ).to_csv(output_path / "holdout_predictions.csv", index=False, encoding="utf-8-sig")
    _feature_associations(selected_pipeline, selected_model, categorical).to_csv(
        output_path / "top_feature_associations.csv", index=False, encoding="utf-8-sig"
    )

    row_counts = {split_name: int(len(split_frames[split_name])) for split_name in SPLIT_NAMES}
    label_counts = {
        split_name: {
            NEGATIVE_LABEL: int(split_frames[split_name]["judge"].eq(NEGATIVE_LABEL).sum()),
            POSITIVE_LABEL: int(split_frames[split_name]["judge"].eq(POSITIVE_LABEL).sum()),
        }
        for split_name in SPLIT_NAMES
    }
    summary: dict[str, object] = {
        "baseline_version": BASELINE_VERSION,
        "source": {
            "file_name": input_path.name,
            "sha256": _sha256(input_path),
            "rows": int(len(frame)),
            "columns": int(len(frame.columns)),
        },
        "software": {
            "scikit_learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
        "random_state": int(random_state),
        "row_counts": row_counts,
        "label_counts": label_counts,
        "feature_schema": feature_schema,
        "selection_protocol": {
            "training_split": "TRAIN",
            "model_selection_split": "VALIDATION",
            "model_selection_metric": "PR-AUC",
            "threshold_selection_split": "VALIDATION",
            "threshold_selection_metric": "F2",
            "final_evaluation_split": "HOLDOUT",
            "holdout_used_for_selection": False,
            "refit_after_validation": False,
            "label_availability_proxy": "ap_date",
            "freeze_date": freeze_date,
            "validation_total_rows": int(len(split_frames["VALIDATION"])),
            "validation_selection_rows": int(len(validation_selection_frame)),
            "validation_late_label_rows_excluded": int(
                len(split_frames["VALIDATION"]) - len(validation_selection_frame)
            ),
            "threshold_status": "provisional_no_alert_capacity",
        },
        "validation_model_comparison": comparison.to_dict(orient="records"),
        "selected_model": selected_model,
        "selected_threshold": float(selected_threshold),
        "validation_selected_threshold_metrics": validation_tuned_metrics,
        "holdout_metrics": holdout_metrics,
        "all_good_holdout_baseline": all_good_metrics,
        "integrity": {
            "one_row_per_coil": bool(not frame["hr_coil_id"].duplicated().any()),
            "split_charge_overlap": int(overlap),
            "ap_operating_feature_count": int(
                sum(column.startswith("ap_") for column in feature_schema["included"])
            ),
            "holdout_used_for_selection": False,
        },
        "interpretation_limit": (
            "Feature associations are predictive associations, not causal root causes or proof of equipment failure."
        ),
    }
    (output_path / "feature_schema.json").write_text(
        json.dumps(feature_schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    joblib.dump(
        {
            "pipeline": selected_pipeline,
            "decision_threshold": float(selected_threshold),
            "feature_schema": feature_schema,
            "baseline_version": BASELINE_VERSION,
            "positive_label": POSITIVE_LABEL,
            "modeling_cohort_sha256": _sha256(input_path),
        },
        output_path / "baseline_model.joblib",
    )
    (output_path / "baseline_metrics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train leakage-gated SFEP-Steel Logistic and Random Forest baselines."
    )
    parser.add_argument("--modeling-cohort", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--random-forest-estimators", type=int, default=300)
    parser.add_argument("--n-jobs", type=int, default=-1)
    parser.add_argument(
        "--validation-freeze-date",
        default=DEFAULT_VALIDATION_FREEZE_DATE,
        help="Immutable label-availability cutoff in YYYY-MM-DD format.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = run_baseline(
        args.modeling_cohort,
        args.output_dir,
        random_state=args.random_state,
        random_forest_estimators=args.random_forest_estimators,
        n_jobs=args.n_jobs,
        validation_freeze_date=args.validation_freeze_date,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
