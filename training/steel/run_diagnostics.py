from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from importlib.metadata import version as package_version
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from scipy import sparse
from sklearn.metrics import average_precision_score, roc_auc_score

from training.steel.diagnostics.calibration import (
    apply_frozen_calibrator,
    apply_frozen_score_calibrator,
    calibration_metrics,
    choose_calibration_method,
    cross_validated_frozen_calibration,
    fit_frozen_calibrator,
)
from training.steel.diagnostics.drift import categorical_drift, numeric_drift
from training.steel.diagnostics.error_analysis import (
    OUTCOME_ORDER,
    add_complement_statistics,
    attach_outcomes,
    categorical_error_slices,
    numeric_error_slices,
)
from training.steel.diagnostics.linear_shap import (
    aggregate_to_source_features,
    linear_logit_shap,
)
from training.steel.diagnostics.registry import INTERPRETATION_LIMIT, build_feature_registry
from training.steel.train_baseline import (
    POSITIVE_LABEL,
    _prepare_features,
    validation_as_of_mask,
)


ANALYSIS_VERSION = "steel-diagnostics-v0.1"
IDENTITY_COLUMNS = ["charge_id", "slab_no", "hr_coil_id"]
CALIBRATION_BRIER_TOLERANCE = 1e-4


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_frame(path: Path) -> pd.DataFrame:
    return pd.read_csv(
        path,
        encoding="utf-8-sig",
        dtype={column: "string" for column in IDENTITY_COLUMNS},
    )


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _align_predictions(source: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    for name, frame in (("source", source), ("predictions", predictions)):
        missing = sorted(set(IDENTITY_COLUMNS) - set(frame.columns))
        if missing:
            raise ValueError(f"{name} is missing identity columns: {', '.join(missing)}")
        if frame.duplicated(IDENTITY_COLUMNS).any():
            raise ValueError(f"{name} must be unique on the coil identity")
    prediction_columns = [
        *IDENTITY_COLUMNS,
        "y_true",
        "risk_score",
        "decision_threshold",
        "predicted_label",
    ]
    missing_prediction_columns = sorted(set(prediction_columns) - set(predictions.columns))
    if missing_prediction_columns:
        raise ValueError(
            "predictions are missing columns: " + ", ".join(missing_prediction_columns)
        )
    joined = source.merge(
        predictions[prediction_columns],
        on=IDENTITY_COLUMNS,
        how="left",
        validate="one_to_one",
        sort=False,
        indicator=True,
    )
    if len(joined) != len(source) or not joined["_merge"].eq("both").all():
        raise ValueError("saved predictions do not match the requested source cohort exactly")
    joined = joined.drop(columns="_merge")
    expected_truth = joined["judge"].eq(POSITIVE_LABEL).astype(int)
    if not np.array_equal(expected_truth.to_numpy(), joined["y_true"].to_numpy(dtype=int)):
        raise ValueError("saved prediction labels disagree with the modeling cohort")
    return joined


def _numeric_screening_status(row: dict[str, object]) -> str:
    if row["reference_missing_rate"] == 0 and row["current_missing_rate"] > 0:
        return "ALERT"
    if row["psi"] >= 0.25 or row["ks_statistic"] >= 0.20:
        return "ALERT"
    if row["psi"] >= 0.10 or row["ks_statistic"] >= 0.10:
        return "WATCH"
    return "LOW"


def _categorical_screening_status(row: dict[str, object]) -> str:
    if row["new_category_count"] > 0:
        return "ALERT"
    if row["psi"] >= 0.25 or row["jensen_shannon_divergence"] >= 0.05:
        return "ALERT"
    if row["psi"] >= 0.10 or row["jensen_shannon_divergence"] >= 0.01:
        return "WATCH"
    return "LOW"


def _outcome_counts(frame: pd.DataFrame) -> dict[str, int]:
    counts = frame["outcome"].value_counts()
    return {name: int(counts.get(name, 0)) for name in OUTCOME_ORDER}


def _calibration_outputs(
    *,
    estimator,
    validation_features: pd.DataFrame,
    validation_frame: pd.DataFrame,
    holdout_features: pd.DataFrame,
    holdout_frame: pd.DataFrame,
    holdout_probability: np.ndarray,
    decision_threshold: float,
    calibration_folds: int,
    random_state: int,
) -> tuple[
    str,
    object,
    np.ndarray,
    float,
    pd.DataFrame,
    pd.DataFrame,
    dict[str, object],
]:
    validation_truth = validation_frame["judge"].eq(POSITIVE_LABEL).astype(int).to_numpy()
    holdout_truth = holdout_frame["judge"].eq(POSITIVE_LABEL).astype(int).to_numpy()
    oof = cross_validated_frozen_calibration(
        estimator,
        validation_features,
        validation_truth,
        validation_frame["charge_id"].to_numpy(),
        n_splits=calibration_folds,
        random_state=random_state,
    )
    metric_rows: list[dict[str, object]] = []
    bin_frames: list[pd.DataFrame] = []
    for method in ("raw", "platt", "isotonic"):
        metrics, bins = calibration_metrics(
            validation_truth,
            oof.probabilities[method],
            split="VALIDATION_OOF",
            variant=method,
        )
        metric_rows.append(metrics)
        bin_frames.append(bins)
    oof_metrics = pd.DataFrame(metric_rows)
    selected_method = choose_calibration_method(
        oof_metrics,
        brier_tolerance=CALIBRATION_BRIER_TOLERANCE,
    )
    calibrator = fit_frozen_calibrator(
        estimator,
        selected_method,
        validation_features,
        validation_truth,
    )
    if selected_method == "raw":
        calibrated_holdout = holdout_probability.copy()
        calibrated_threshold = float(decision_threshold)
    else:
        calibrated_holdout = apply_frozen_calibrator(
            selected_method,
            calibrator,
            estimator,
            holdout_features,
        )
        raw_decision_threshold = float(
            np.log(decision_threshold / (1.0 - decision_threshold))
        )
        calibrated_threshold = float(
            apply_frozen_score_calibrator(
                selected_method,
                calibrator,
                np.asarray([raw_decision_threshold]),
            )[0]
        )

    for variant, probability in (
        ("raw", holdout_probability),
        (f"selected_{selected_method}", calibrated_holdout),
    ):
        metrics, bins = calibration_metrics(
            holdout_truth,
            probability,
            split="HOLDOUT",
            variant=variant,
        )
        metrics["roc_auc"] = float(roc_auc_score(holdout_truth, probability))
        metrics["pr_auc"] = float(average_precision_score(holdout_truth, probability))
        metric_rows.append(metrics)
        bin_frames.append(bins)

    metrics_frame = pd.DataFrame(metric_rows)
    bins_frame = pd.concat(bin_frames, ignore_index=True)
    raw_policy = holdout_probability >= decision_threshold
    mapped_policy = calibrated_holdout >= calibrated_threshold
    selection_rows = oof_metrics.set_index("variant")
    validation_prevalence = float(validation_truth.mean())
    constant_brier = float(np.mean((holdout_truth - validation_prevalence) ** 2))
    selected_brier = float(
        metrics_frame.loc[
            metrics_frame["split"].eq("HOLDOUT")
            & metrics_frame["variant"].eq(f"selected_{selected_method}"),
            "brier_score",
        ].iloc[0]
    )
    summary = {
        "selected_method": selected_method,
        "selection_split": "VALIDATION_OOF",
        "grouping_key": "charge_id",
        "folds": int(calibration_folds),
        "brier_tolerance": CALIBRATION_BRIER_TOLERANCE,
        "holdout_used_for_selection": False,
        "constant_validation_prevalence": validation_prevalence,
        "constant_validation_prevalence_holdout_brier": constant_brier,
        "selected_brier_relative_improvement_over_constant": float(
            (constant_brier - selected_brier) / constant_brier
        ),
        "validation_oof": {
            method: {
                "brier_score": float(selection_rows.loc[method, "brier_score"]),
                "log_loss": float(selection_rows.loc[method, "log_loss"]),
                "ece": float(selection_rows.loc[method, "ece"]),
                "fixed_width_ece": float(
                    selection_rows.loc[method, "fixed_width_ece"]
                ),
            }
            for method in ("raw", "platt", "isotonic")
        },
        "raw_decision_threshold": float(decision_threshold),
        "calibrated_display_threshold": calibrated_threshold,
        "mapped_threshold_decisions_equal": bool(np.array_equal(raw_policy, mapped_policy)),
    }
    return (
        selected_method,
        calibrator,
        calibrated_holdout,
        calibrated_threshold,
        metrics_frame,
        bins_frame,
        summary,
    )


def _shap_outputs(
    *,
    pipeline,
    train_frame: pd.DataFrame,
    holdout_frame: pd.DataFrame,
    holdout_errors: pd.DataFrame,
    calibrated_probability: np.ndarray,
    feature_schema: dict[str, object],
    top_shap_per_coil: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    numeric = list(feature_schema["numeric"])
    categorical = list(feature_schema["categorical"])
    included = list(feature_schema["included"])
    preprocessor = pipeline.named_steps["preprocessor"]
    classifier = pipeline.named_steps["classifier"]
    if not hasattr(classifier, "coef_") or np.asarray(classifier.coef_).shape[0] != 1:
        raise ValueError("analytical Linear SHAP requires the selected binary linear classifier")

    train_matrix = preprocessor.transform(_prepare_features(train_frame, numeric, categorical))
    holdout_matrix = preprocessor.transform(
        _prepare_features(holdout_frame, numeric, categorical)
    )
    background_mean = (
        np.asarray(train_matrix.mean(axis=0)).reshape(-1)
        if sparse.issparse(train_matrix)
        else np.asarray(train_matrix, dtype=float).mean(axis=0)
    )
    result = linear_logit_shap(
        holdout_matrix,
        coefficient=np.asarray(classifier.coef_[0], dtype=float),
        intercept=float(classifier.intercept_[0]),
        background_mean=background_mean,
    )
    model_logit = np.asarray(classifier.decision_function(holdout_matrix), dtype=float)
    reconstructed = result.base_value + result.values.sum(axis=1)
    max_additivity_error = float(np.max(np.abs(reconstructed - model_logit)))
    transformed_names = preprocessor.get_feature_names_out()
    source_names, source_values = aggregate_to_source_features(
        transformed_names,
        result.values,
        categorical_features=categorical,
    )
    if set(source_names) != set(included):
        missing = sorted(set(included) - set(source_names))
        extra = sorted(set(source_names) - set(included))
        raise ValueError(f"SHAP source mapping mismatch; missing={missing}, extra={extra}")

    truth = holdout_frame["judge"].eq(POSITIVE_LABEL).to_numpy()
    outcomes = holdout_errors["outcome"].to_numpy()
    global_rows: list[dict[str, object]] = []
    for index, feature in enumerate(source_names):
        values = source_values[:, index]
        absolute = np.abs(values)
        global_rows.append(
            {
                "source_feature": feature,
                "n": int(len(values)),
                "mean_abs_shap_logit": float(absolute.mean()),
                "median_abs_shap_logit": float(np.median(absolute)),
                "p95_abs_shap_logit": float(np.quantile(absolute, 0.95)),
                "mean_shap_logit": float(values.mean()),
                "mean_shap_actual_good": float(values[~truth].mean()),
                "mean_shap_actual_bad": float(values[truth].mean()),
                "mean_abs_shap_fp": float(absolute[outcomes == "FP"].mean())
                if np.any(outcomes == "FP")
                else float("nan"),
                "mean_abs_shap_fn": float(absolute[outcomes == "FN"].mean())
                if np.any(outcomes == "FN")
                else float("nan"),
            }
        )
    global_frame = pd.DataFrame(global_rows).sort_values(
        "mean_abs_shap_logit", ascending=False, kind="stable"
    )
    global_frame.insert(0, "rank", range(1, len(global_frame) + 1))
    global_frame = global_frame.reset_index(drop=True)

    local_rows: list[dict[str, object]] = []
    known_categories: dict[str, set[str]] = {}
    if categorical:
        encoder = preprocessor.named_transformers_["categorical"].named_steps["onehot"]
        known_categories = {
            feature: {str(value) for value in values if not pd.isna(value)}
            for feature, values in zip(categorical, encoder.categories_, strict=True)
        }
    top_count = min(max(int(top_shap_per_coil), 1), len(source_names))
    top_indices = np.argsort(-np.abs(source_values), axis=1, kind="stable")[:, :top_count]
    for row_index, selected_indices in enumerate(top_indices):
        source_row = holdout_frame.iloc[row_index]
        for rank, feature_index in enumerate(selected_indices, start=1):
            feature = source_names[int(feature_index)]
            raw_value = source_row[feature]
            imputed_flag = bool(pd.isna(raw_value))
            unknown_category_flag = bool(
                feature in known_categories
                and not imputed_flag
                and str(raw_value) not in known_categories[feature]
            )
            local_rows.append(
                {
                    "charge_id": source_row["charge_id"],
                    "slab_no": source_row["slab_no"],
                    "hr_coil_id": source_row["hr_coil_id"],
                    "hr_date": source_row.get("hr_date", ""),
                    "outcome": outcomes[row_index],
                    "y_true": int(truth[row_index]),
                    "risk_score": float(holdout_errors.iloc[row_index]["risk_score"]),
                    "calibrated_probability": float(calibrated_probability[row_index]),
                    "base_logit": result.base_value,
                    "model_logit": float(model_logit[row_index]),
                    "rank_within_coil": rank,
                    "source_feature": feature,
                    "raw_value": "<MISSING>" if pd.isna(raw_value) else str(raw_value),
                    "imputed_flag": imputed_flag,
                    "unknown_category_flag": unknown_category_flag,
                    "shap_logit": float(source_values[row_index, feature_index]),
                    "abs_shap_logit": float(abs(source_values[row_index, feature_index])),
                }
            )
    local_frame = pd.DataFrame(local_rows)

    registry = build_feature_registry(included)
    feature_candidates = global_frame.merge(
        registry,
        left_on="source_feature",
        right_on="feature",
        how="left",
        validate="one_to_one",
    )
    equipment_candidates = (
        feature_candidates.groupby(
            ["process_stage", "sub_process", "equipment_candidate"],
            dropna=False,
            as_index=False,
        )
        .agg(
            feature_count=("source_feature", "size"),
            contributing_features=("source_feature", lambda values: "|".join(values)),
            candidate_kinds=(
                "candidate_kind",
                lambda values: "|".join(dict.fromkeys(values)),
            ),
            recommended_review_routes=(
                "recommended_review_route",
                lambda values: "|".join(dict.fromkeys(values)),
            ),
            automated_maintenance_ticket_eligible=(
                "automated_maintenance_ticket_eligible",
                "all",
            ),
            sum_mean_abs_shap_logit=("mean_abs_shap_logit", "sum"),
            max_feature_mean_abs_shap_logit=("mean_abs_shap_logit", "max"),
            mean_signed_shap_logit=("mean_shap_logit", "sum"),
        )
        .sort_values("sum_mean_abs_shap_logit", ascending=False, kind="stable")
        .reset_index(drop=True)
    )
    equipment_candidates.insert(0, "candidate_rank", range(1, len(equipment_candidates) + 1))
    equipment_candidates["interpretation_limit"] = INTERPRETATION_LIMIT
    diagnostic_caveats: list[dict[str, object]] = []
    if "f_ldg" in train_frame and "f_ldg" in holdout_frame:
        train_ldg = pd.to_numeric(train_frame["f_ldg"], errors="coerce")
        holdout_ldg = pd.to_numeric(holdout_frame["f_ldg"], errors="coerce")
        diagnostic_caveats.append(
            {
                "features": ["f_ldg"],
                "kind": "distribution_extrapolation",
                "train_mean": float(train_ldg.mean()),
                "holdout_mean": float(holdout_ldg.mean()),
                "train_nonzero_rate": float(train_ldg.ne(0).mean()),
                "holdout_nonzero_rate": float(holdout_ldg.ne(0).mean()),
                "message": "TRAIN 대비 0 초과 유량 비중 변화가 커 SHAP 크기가 외삽의 영향을 받음",
            }
        )
    if {"hr_width", "slab_width"}.issubset(train_frame.columns):
        width_correlation = float(
            pd.to_numeric(train_frame["hr_width"], errors="coerce").corr(
                pd.to_numeric(train_frame["slab_width"], errors="coerce")
            )
        )
        diagnostic_caveats.append(
            {
                "features": ["hr_width", "slab_width"],
                "kind": "correlated_features",
                "train_correlation": width_correlation,
                "message": "상관된 폭 변수의 개별 SHAP 순위는 불안정할 수 있어 함께 확인해야 함",
            }
        )
    if {"f_jangip_temp", "f_jangip_gubun"}.issubset(train_frame.columns):
        diagnostic_caveats.append(
            {
                "features": ["f_jangip_temp", "f_jangip_gubun"],
                "kind": "related_operating_signals",
                "message": "장입 온도와 장입 방식은 연관 운전 신호이므로 독립 원인으로 분리하지 않음",
            }
        )
    summary = {
        "method": "analytical_linear_logit_shap",
        "explanation_space": "classifier_log_odds",
        "background_split": "TRAIN",
        "background_rows": int(len(train_frame)),
        "base_logit": float(result.base_value),
        "transformed_feature_count": int(len(transformed_names)),
        "source_feature_count": int(len(source_names)),
        "max_additivity_error": max_additivity_error,
        "diagnostic_caveats": diagnostic_caveats,
        "interpretation_limit": INTERPRETATION_LIMIT,
    }
    return registry, global_frame, local_frame, equipment_candidates, summary


def _markdown_table(frame: pd.DataFrame, columns: list[str], limit: int = 6) -> str:
    if frame.empty:
        return "해당 조건을 만족하는 행이 없습니다."
    shown = frame.loc[:, columns].head(limit).copy()
    for column in shown.select_dtypes(include="number"):
        shown[column] = shown[column].map(lambda value: f"{value:.4f}")
    headers = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    rows = [
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in shown.itertuples(index=False, name=None)
    ]
    return "\n".join([headers, divider, *rows])


def _top_per_comparison(
    frame: pd.DataFrame,
    *,
    metric: str,
    rows_per_comparison: int,
) -> pd.DataFrame:
    groups = [
        group.nlargest(rows_per_comparison, metric)
        for _, group in frame.groupby("comparison_split", sort=False)
    ]
    return pd.concat(groups, ignore_index=True) if groups else frame.head(0).copy()


def _build_report(
    *,
    summary: dict[str, object],
    categorical_slices: pd.DataFrame,
    numeric_slices: pd.DataFrame,
    numeric_drift_frame: pd.DataFrame,
    categorical_drift_frame: pd.DataFrame,
    label_drift_frame: pd.DataFrame,
    calibration_metrics_frame: pd.DataFrame,
    equipment_candidates: pd.DataFrame,
    holdout_errors: pd.DataFrame,
) -> str:
    outcomes = summary["holdout_outcomes"]
    actual_good = outcomes["TN"] + outcomes["FP"]
    actual_bad = outcomes["FN"] + outcomes["TP"]
    fpr = outcomes["FP"] / actual_good if actual_good else 0.0
    fnr = outcomes["FN"] / actual_bad if actual_bad else 0.0
    supported = pd.concat([categorical_slices, numeric_slices], ignore_index=True)
    fpr_slices = supported.loc[supported.get("fpr_support_pass", False).eq(True)].sort_values(
        "false_alarm_rate_delta", key=lambda values: values.abs(), ascending=False
    )
    fnr_slices = supported.loc[supported.get("fnr_support_pass", False).eq(True)].sort_values(
        "fnr_delta", key=lambda values: values.abs(), ascending=False
    )
    numeric_top = _top_per_comparison(
        numeric_drift_frame,
        metric="psi",
        rows_per_comparison=5,
    )
    categorical_top = _top_per_comparison(
        categorical_drift_frame,
        metric="jensen_shannon_divergence",
        rows_per_comparison=4,
    )
    selected_method = summary["calibration"]["selected_method"]
    holdout_calibration = calibration_metrics_frame.loc[
        calibration_metrics_frame["split"].eq("HOLDOUT"),
        [
            "variant",
            "brier_score",
            "log_loss",
            "ece",
            "fixed_width_ece",
            "mean_probability",
        ],
    ]
    overlap_note = ""
    audit_notes: list[str] = []
    required_overlap = {"f_jangip_gubun", "f_jangip_temp", "f_ldg"}
    if required_overlap.issubset(holdout_errors.columns):
        mask = (
            holdout_errors["f_jangip_gubun"].astype("string").eq("CCR")
            & pd.to_numeric(holdout_errors["f_jangip_temp"], errors="coerce").le(30)
            & pd.to_numeric(holdout_errors["f_ldg"], errors="coerce").eq(0)
        )
        group = holdout_errors.loc[mask]
        counts = _outcome_counts(group)
        negative = counts["TN"] + counts["FP"]
        overlap_note = (
            f"- `CCR ∩ 장입온도≤30 ∩ LDG=0` 중 FP는 {counts['FP']:,}건이며, "
            f"실제 양품 기준 오탐률은 {counts['FP'] / negative:.2%}입니다. "
            "이는 중첩된 운전 조건으로, 세 신호를 독립 원인처럼 해석하면 안 됩니다."
            if negative
            else ""
        )
    if "steel_usage" in holdout_errors:
        rj1 = holdout_errors.loc[holdout_errors["steel_usage"].astype("string").eq("RJ1")]
        counts = _outcome_counts(rj1)
        positives = counts["FN"] + counts["TP"]
        if positives:
            audit_notes.append(
                f"`steel_usage=RJ1`은 실제 불량 {positives:,}건 중 FN {counts['FN']:,}건"
                f"(미탐률 {counts['FN'] / positives:.2%})이나 TP가 {counts['TP']:,}건이라 "
                "의사결정용 최소 지원 기준은 통과하지 못했습니다."
            )
    if "f_jangip_gubun" in holdout_errors:
        hcr = holdout_errors.loc[
            holdout_errors["f_jangip_gubun"].astype("string").eq("HCR")
        ]
        counts = _outcome_counts(hcr)
        positives = counts["FN"] + counts["TP"]
        if positives:
            audit_notes.append(
                f"`HCR` 실제 불량은 {positives:,}건(FN {counts['FN']:,}, TP {counts['TP']:,})으로 "
                "표본이 작아 감사용 신호로만 남겼습니다."
            )
    if "hr_week" in holdout_errors:
        weekly = categorical_slices.loc[categorical_slices["slice_feature"].eq("hr_week")]
        if not weekly.empty:
            row = weekly.sort_values("false_alarm_rate", ascending=False).iloc[0]
            audit_notes.append(
                f"주간 최고 오탐률은 `{row['slice_value']}`의 {row['false_alarm_rate']:.2%}"
                f"(FP {int(row['fp']):,}건)입니다."
            )
    audit_note_text = "\n".join(f"- {note}" for note in audit_notes)
    shap_caveat_text = "\n".join(
        f"- `{', '.join(item['features'])}`: {item['message']}"
        for item in summary["shap"].get("diagnostic_caveats", [])
    )
    missing_rows = summary["data_quality"]["model_feature_missing_rows"]
    dq_any = summary["data_quality"]["dq_flags"].get("dq_any_flag", {})
    dq_text = ", ".join(
        f"{split_name} {values['rows']}건(불량 {values['positive_rows']}건)"
        for split_name, values in dq_any.items()
    )
    holdout_n = sum(outcomes.values())
    return f"""# SFEP Steel Holdout 진단 보고서

## 결론

- Holdout {holdout_n:,}개 Coil의 결과는 TN {outcomes['TN']:,}, FP {outcomes['FP']:,}, FN {outcomes['FN']:,}, TP {outcomes['TP']:,}입니다. 오탐률은 {fpr:.2%}, 미탐률은 {fnr:.2%}입니다.
- Calibration은 Holdout을 보지 않은 charge-group {summary['calibration']['folds']}-fold Validation OOF 규칙으로 `{selected_method}`를 선택했습니다. 확률 해석은 개선하지만 분류 순위와 기존 정책의 한계를 고치지는 않습니다.
- Drift와 SHAP은 확인 우선순위를 정하는 근거입니다. 아래 후보는 공정 원인이나 설비 고장의 판정이 아닙니다.

## 1. Holdout 오탐·미탐

지원도, Fisher 검정, BH 보정을 통과한 오탐 관련 상위 Slice입니다.

{_markdown_table(fpr_slices, ['slice_feature', 'slice_value', 'n', 'fp', 'false_alarm_rate', 'complement_false_alarm_rate', 'fpr_bh_qvalue'])}

지원도를 통과한 미탐 관련 상위 Slice입니다.

{_markdown_table(fnr_slices, ['slice_feature', 'slice_value', 'n', 'fn', 'tp', 'fnr', 'complement_fnr', 'fnr_bh_qvalue'])}

{overlap_note}

지원 기준 미달이지만 놓치면 안 되는 감사용 관찰입니다.

{audit_note_text}

숫자 구간은 TRAIN 분포로 만든 탐색 구간입니다. Holdout에서 발견한 경계값은 다음 기간에 재확인하기 전 운영 규칙으로 승격하지 않습니다.

## 2. 기간 Drift

먼저 불량률 자체가 이동했습니다.

{_markdown_table(label_drift_frame, ['comparison_split', 'reference_positive_rate', 'current_positive_rate', 'positive_rate_delta_pp', 'positive_rate_ratio', 'screening_status'])}

TRAIN 대비 수치형 상위 Drift입니다. PSI는 Jeffreys 0.5 보정, KS는 두 표본 경험분포를 사용했습니다.

{_markdown_table(numeric_top, ['comparison_split', 'feature', 'psi', 'ks_statistic', 'reference_mean', 'current_mean', 'screening_status'], limit=10)}

범주형은 JSD(base 2), PSI 및 신규 범주를 함께 확인했습니다.

{_markdown_table(categorical_top, ['comparison_split', 'feature', 'jensen_shannon_divergence', 'psi', 'new_category_count', 'screening_status'], limit=8)}

Drift 경보는 분포 변화 신호이며 품질 원인의 증거가 아닙니다. 특히 코드 치환, 센서 단위, 제품 Mix 변경을 먼저 확인해야 합니다.

30개 모델 Feature의 결측 포함 행은 TRAIN {missing_rows['TRAIN']:,}건, As-of VALIDATION {missing_rows['VALIDATION_AS_OF']:,}건, HOLDOUT {missing_rows['HOLDOUT']:,}건입니다. `dq_any_flag`는 {dq_text or '해당 Flag 없음'}이며, 소수 Flag의 불량 0건을 안전하다는 근거로 해석하지 않습니다.

## 3. Calibration

{_markdown_table(holdout_calibration, ['variant', 'brier_score', 'log_loss', 'ece', 'fixed_width_ece', 'mean_probability'])}

`ece`는 동일 확률 Tie를 나누지 않는 등빈도 ECE이고, `fixed_width_ece`는 고정 폭 ECE입니다. 희귀 불량에서는 두 값을 함께 봅니다.

선택 보정기의 Brier 개선은 Validation 불량률 상수 기준선 대비 {summary['calibration']['selected_brier_relative_improvement_over_constant']:.2%}입니다. 즉 Raw Risk Score보다 확률 해석은 크게 나아졌지만, 단순 상수 확률 대비 예측 정보 증가는 작고 Holdout 실제 불량률을 과소예측합니다.

기존 raw threshold `{summary['calibration']['raw_decision_threshold']:.6f}`는 표시용 보정 확률 threshold `{summary['calibration']['calibrated_display_threshold']:.6f}`에 대응합니다. 경보 정책은 raw 기준으로 고정했으며, 보정 확률에 0.5 또는 기존 raw threshold를 재사용하면 안 됩니다.

## 4. SHAP 기반 공정·설비 확인 후보

설명값은 고정 Logistic classifier의 log-odds 기여도이고, TRAIN 평균 변환값을 배경으로 사용했습니다.

{_markdown_table(equipment_candidates, ['candidate_rank', 'process_stage', 'equipment_candidate', 'candidate_kinds', 'contributing_features', 'sum_mean_abs_shap_logit'], limit=10)}

SHAP 해석 시 함께 묶어 볼 신호입니다.

{shap_caveat_text}

우선 확인 순서는 ① 데이터 정의·코드/단위 ② 동일 강종·용도 내 재현 ③ 계측기·운전 로그 대조 ④ 현장 점검입니다. Context/제품 사양 변수는 자동 정비 Ticket으로 전환하지 않습니다.

## 분석 통제

- 모델 입력 30개 외 AP 운영변수는 사용하지 않았습니다.
- Calibration 선택과 임계값 선택에 Holdout을 사용하지 않았습니다.
- SHAP additivity 최대 오차는 `{summary['shap']['max_additivity_error']:.3e}`입니다.
- 분석 버전: `{summary['analysis_version']}`
- 한계: {INTERPRETATION_LIMIT}
"""


def _create_charts(
    output_dir: Path,
    *,
    outcomes: dict[str, int],
    numeric_drift_frame: pd.DataFrame,
    calibration_bins_frame: pd.DataFrame,
    selected_method: str,
    equipment_candidates: pd.DataFrame,
) -> list[str]:
    cache_dir = Path("/private/tmp/sfep-mpl-cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    korean_font = Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf")
    if korean_font.exists():
        matplotlib.rcParams["font.family"] = font_manager.FontProperties(
            fname=korean_font
        ).get_name()
        matplotlib.rcParams["axes.unicode_minus"] = False

    chart_names: list[str] = []
    matrix = np.asarray(
        [[outcomes["TN"], outcomes["FP"]], [outcomes["FN"], outcomes["TP"]]],
        dtype=int,
    )
    figure, axis = plt.subplots(figsize=(5.2, 4.4))
    image = axis.imshow(matrix, cmap="Blues")
    for row in range(2):
        for column in range(2):
            axis.text(
                column,
                row,
                f"{matrix[row, column]:,}",
                ha="center",
                va="center",
                color="white" if matrix[row, column] > matrix.max() / 2 else "black",
            )
    axis.set_xticks([0, 1], ["Predicted good", "Predicted bad"])
    axis.set_yticks([0, 1], ["Actual good", "Actual bad"])
    axis.set_title("Holdout confusion matrix")
    figure.colorbar(image, ax=axis, shrink=0.8)
    figure.tight_layout()
    name = "holdout_confusion_matrix.png"
    figure.savefig(output_dir / name, dpi=180)
    plt.close(figure)
    chart_names.append(name)

    drift_plot = numeric_drift_frame.nlargest(12, "psi").sort_values("psi")
    figure, axis = plt.subplots(figsize=(8.4, 5.2))
    labels = drift_plot["comparison_split"] + " | " + drift_plot["feature"]
    axis.barh(labels, drift_plot["psi"], color="#d97706")
    axis.axvline(0.25, color="#991b1b", linestyle="--", linewidth=1)
    axis.set_xlabel("PSI (Jeffreys smoothing)")
    axis.set_title("Largest numeric distribution shifts")
    figure.tight_layout()
    name = "drift_top_features.png"
    figure.savefig(output_dir / name, dpi=180)
    plt.close(figure)
    chart_names.append(name)

    holdout_bins = calibration_bins_frame.loc[
        calibration_bins_frame["split"].eq("HOLDOUT")
    ]
    variants = ("raw", f"selected_{selected_method}")
    figure, axes = plt.subplots(1, 2, figsize=(9.8, 4.7))
    for axis, variant in zip(axes, variants, strict=True):
        group = holdout_bins.loc[holdout_bins["variant"].eq(variant)]
        axis.plot(
            group["mean_probability"],
            group["observed_rate"],
            marker="o",
            color="#2563eb" if variant == "raw" else "#ea580c",
        )
        limit = max(
            0.10,
            float(group["mean_probability"].max()) * 1.08,
            float(group["observed_rate"].max()) * 1.08,
        )
        axis.plot([0, limit], [0, limit], color="#4b5563", linestyle="--")
        axis.set_xlim(0, limit)
        axis.set_ylim(0, limit)
        axis.set_aspect("equal", adjustable="box")
        axis.set_xlabel("Mean predicted probability")
        axis.set_ylabel("Observed defect rate")
        axis.set_title(variant)
    figure.suptitle("Holdout reliability (equal-frequency bins)")
    figure.tight_layout()
    name = "calibration_curve.png"
    figure.savefig(output_dir / name, dpi=180)
    plt.close(figure)
    chart_names.append(name)

    candidates = equipment_candidates.head(10).sort_values("sum_mean_abs_shap_logit")
    figure, axis = plt.subplots(figsize=(8.4, 5.3))
    axis.barh(
        candidates["equipment_candidate"],
        candidates["sum_mean_abs_shap_logit"],
        color="#2563eb",
    )
    axis.set_xlabel("Sum mean |SHAP| (log-odds)")
    axis.set_title("Process/equipment review candidates")
    figure.tight_layout()
    name = "shap_equipment_candidates.png"
    figure.savefig(output_dir / name, dpi=180)
    plt.close(figure)
    chart_names.append(name)
    return chart_names


def run_diagnostics(
    modeling_cohort_csv: str | Path,
    baseline_dir: str | Path,
    output_dir: str | Path,
    *,
    calibration_folds: int = 5,
    min_slice_support: int = 30,
    top_shap_per_coil: int = 5,
    create_charts: bool = True,
    random_state: int = 42,
) -> dict[str, object]:
    modeling_path = Path(modeling_cohort_csv)
    baseline_path = Path(baseline_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    frame = _read_frame(modeling_path)
    baseline_metrics = _read_json(baseline_path / "baseline_metrics.json")
    feature_schema = _read_json(baseline_path / "feature_schema.json")
    model_artifact = joblib.load(baseline_path / "baseline_model.joblib")
    pipeline = model_artifact["pipeline"]
    if model_artifact["feature_schema"] != feature_schema:
        raise ValueError("model artifact and feature_schema.json disagree")
    source_sha256 = _sha256(modeling_path)
    if source_sha256 != baseline_metrics["source"]["sha256"]:
        raise ValueError("modeling cohort hash does not match the baseline source")
    decision_threshold = float(model_artifact["decision_threshold"])

    split_frames = {
        split_name: frame.loc[frame["dataset_split"].eq(split_name)].reset_index(drop=True)
        for split_name in ("TRAIN", "VALIDATION", "HOLDOUT")
    }
    freeze_date = str(baseline_metrics["selection_protocol"]["freeze_date"])
    as_of_mask = validation_as_of_mask(split_frames["VALIDATION"], freeze_date)
    validation_as_of = split_frames["VALIDATION"].loc[as_of_mask].reset_index(drop=True)
    train_frame = split_frames["TRAIN"]
    holdout_frame = split_frames["HOLDOUT"]

    validation_predictions = _read_frame(baseline_path / "validation_predictions.csv")
    holdout_predictions = _read_frame(baseline_path / "holdout_predictions.csv")
    validation_joined = _align_predictions(validation_as_of, validation_predictions)
    holdout_joined = _align_predictions(holdout_frame, holdout_predictions)
    numeric = list(feature_schema["numeric"])
    categorical = list(feature_schema["categorical"])
    validation_features = _prepare_features(validation_as_of, numeric, categorical)
    holdout_features = _prepare_features(holdout_frame, numeric, categorical)
    validation_probability = pipeline.predict_proba(validation_features)[:, 1]
    holdout_probability = pipeline.predict_proba(holdout_features)[:, 1]
    holdout_decision = np.asarray(pipeline.decision_function(holdout_features))
    max_score_reproduction_error = float(
        max(
            np.max(np.abs(validation_probability - validation_joined["risk_score"])),
            np.max(np.abs(holdout_probability - holdout_joined["risk_score"])),
        )
    )
    if max_score_reproduction_error > 1e-10:
        raise ValueError("saved prediction scores do not reproduce from the model artifact")

    y_true = holdout_joined["y_true"].to_numpy(dtype=int)
    y_pred = holdout_joined["risk_score"].ge(decision_threshold).astype(int).to_numpy()
    holdout_errors = attach_outcomes(holdout_joined, y_true=y_true, y_pred=y_pred)
    holdout_errors["confusion_cohort"] = holdout_errors["outcome"]
    holdout_errors["signed_margin"] = holdout_errors["risk_score"] - decision_threshold
    holdout_errors["absolute_margin"] = holdout_errors["signed_margin"].abs()
    holdout_errors["hr_week"] = (
        pd.to_datetime(holdout_errors["hr_date"], errors="coerce")
        .dt.to_period("W-SUN")
        .dt.start_time
        .dt.strftime("%Y-%m-%d")
    )
    _write_csv(holdout_errors, output_path / "holdout_error_cohorts.csv")

    slice_categories = [*categorical, "hr_week"]
    slice_categories.extend(
        column for column in frame.columns if column.startswith("dq_") and column.endswith("_flag")
    )
    slice_categories = list(dict.fromkeys(slice_categories))
    categorical_slices = categorical_error_slices(
        holdout_errors,
        slice_categories,
        min_support=min_slice_support,
    )
    categorical_slices = add_complement_statistics(categorical_slices, holdout_errors)
    numeric_slices = numeric_error_slices(
        train_frame,
        holdout_errors,
        numeric,
        min_support=min_slice_support,
    )
    numeric_slices = add_complement_statistics(numeric_slices, holdout_errors)
    _write_csv(categorical_slices, output_path / "categorical_error_slices.csv")
    _write_csv(numeric_slices, output_path / "numeric_error_deciles.csv")

    comparisons = {
        "VALIDATION_AS_OF": validation_as_of,
        "HOLDOUT": holdout_frame,
    }
    numeric_drift_rows: list[dict[str, object]] = []
    categorical_drift_rows: list[dict[str, object]] = []
    categorical_level_frames: list[pd.DataFrame] = []
    label_drift_rows: list[dict[str, object]] = []
    train_positive = int(train_frame["judge"].eq(POSITIVE_LABEL).sum())
    train_positive_rate = train_positive / len(train_frame)
    for split_name, comparison_frame in comparisons.items():
        for feature in numeric:
            row = numeric_drift(
                feature,
                train_frame[feature],
                comparison_frame[feature],
                comparison_split=split_name,
            )
            row["screening_status"] = _numeric_screening_status(row)
            numeric_drift_rows.append(row)
        for feature in categorical:
            row, levels = categorical_drift(
                feature,
                train_frame[feature],
                comparison_frame[feature],
                comparison_split=split_name,
            )
            row["screening_status"] = _categorical_screening_status(row)
            categorical_drift_rows.append(row)
            categorical_level_frames.append(levels)
        label_row, _ = categorical_drift(
            "judge",
            train_frame["judge"],
            comparison_frame["judge"],
            comparison_split=split_name,
        )
        positive = int(comparison_frame["judge"].eq(POSITIVE_LABEL).sum())
        positive_rate = positive / len(comparison_frame)
        rate_ratio = positive_rate / train_positive_rate if train_positive_rate else float("inf")
        label_row.update(
            {
                "reference_positive": train_positive,
                "current_positive": positive,
                "reference_positive_rate": train_positive_rate,
                "current_positive_rate": positive_rate,
                "positive_rate_delta_pp": (positive_rate - train_positive_rate) * 100,
                "positive_rate_ratio": rate_ratio,
                "screening_status": "WATCH"
                if abs(positive_rate - train_positive_rate) >= 0.01 or rate_ratio >= 1.5
                else "LOW",
            }
        )
        label_drift_rows.append(label_row)
    numeric_drift_frame = pd.DataFrame(numeric_drift_rows)
    categorical_drift_frame = pd.DataFrame(categorical_drift_rows)
    categorical_levels_frame = pd.concat(categorical_level_frames, ignore_index=True)
    label_drift_frame = pd.DataFrame(label_drift_rows)
    _write_csv(numeric_drift_frame, output_path / "numeric_drift.csv")
    _write_csv(categorical_drift_frame, output_path / "categorical_drift.csv")
    _write_csv(categorical_levels_frame, output_path / "categorical_drift_levels.csv")
    _write_csv(label_drift_frame, output_path / "label_drift.csv")

    (
        selected_method,
        calibrator,
        calibrated_holdout,
        calibrated_threshold,
        calibration_metrics_frame,
        calibration_bins_frame,
        calibration_summary,
    ) = _calibration_outputs(
        estimator=pipeline,
        validation_features=validation_features,
        validation_frame=validation_as_of,
        holdout_features=holdout_features,
        holdout_frame=holdout_frame,
        holdout_probability=holdout_probability,
        decision_threshold=decision_threshold,
        calibration_folds=calibration_folds,
        random_state=random_state,
    )
    _write_csv(calibration_metrics_frame, output_path / "calibration_metrics.csv")
    _write_csv(calibration_bins_frame, output_path / "calibration_bins.csv")
    calibrated_predictions = holdout_predictions.copy()
    calibrated_predictions["raw_decision"] = holdout_decision
    calibrated_predictions["calibration_method"] = selected_method
    calibrated_predictions["calibrated_probability"] = calibrated_holdout
    calibrated_predictions["raw_decision_threshold"] = decision_threshold
    calibrated_predictions["calibrated_display_threshold"] = calibrated_threshold
    calibrated_predictions["policy_note"] = "frozen_raw_threshold_policy"
    _write_csv(
        calibrated_predictions,
        output_path / "holdout_calibrated_predictions.csv",
    )
    joblib.dump(
        {
            "calibrator": calibrator,
            "method": selected_method,
            "baseline_model_sha256": _sha256(baseline_path / "baseline_model.joblib"),
            "feature_schema_sha256": _sha256(baseline_path / "feature_schema.json"),
            "modeling_cohort_sha256": source_sha256,
            "freeze_date": freeze_date,
            "fit_split": "VALIDATION_AS_OF",
            "fit_rows": int(len(validation_as_of)),
            "fit_positive_rows": int(validation_as_of["judge"].eq(POSITIVE_LABEL).sum()),
            "grouped_oof_folds": int(calibration_folds),
            "holdout_used_for_selection": False,
            "raw_decision_threshold": decision_threshold,
            "calibrated_display_threshold": calibrated_threshold,
        },
        output_path / "calibrator.joblib",
    )

    registry, shap_global, shap_local, equipment_candidates, shap_summary = _shap_outputs(
        pipeline=pipeline,
        train_frame=train_frame,
        holdout_frame=holdout_frame,
        holdout_errors=holdout_errors,
        calibrated_probability=calibrated_holdout,
        feature_schema=feature_schema,
        top_shap_per_coil=top_shap_per_coil,
    )
    _write_csv(registry, output_path / "feature_equipment_registry.csv")
    _write_csv(shap_global, output_path / "shap_global_features.csv")
    _write_csv(shap_local, output_path / "shap_top_per_coil.csv")
    _write_csv(equipment_candidates, output_path / "shap_equipment_candidates.csv")

    outcomes = _outcome_counts(holdout_errors)
    model_feature_missing_rows = {
        split_name: int(group[list(feature_schema["included"])].isna().any(axis=1).sum())
        for split_name, group in {
            "TRAIN": train_frame,
            "VALIDATION_AS_OF": validation_as_of,
            "HOLDOUT": holdout_frame,
        }.items()
    }
    dq_flag_columns = [
        column
        for column in frame.columns
        if column.startswith("dq_") and column.endswith("_flag")
    ]
    dq_flag_summary = {
        flag: {
            split_name: {
                "rows": int(group[flag].fillna(False).astype(bool).sum()),
                "positive_rows": int(
                    (
                        group[flag].fillna(False).astype(bool)
                        & group["judge"].eq(POSITIVE_LABEL)
                    ).sum()
                ),
            }
            for split_name, group in {
                "TRAIN": train_frame,
                "VALIDATION_AS_OF": validation_as_of,
                "HOLDOUT": holdout_frame,
            }.items()
        }
        for flag in dq_flag_columns
    }
    summary: dict[str, object] = {
        "analysis_version": ANALYSIS_VERSION,
        "source": {
            "modeling_cohort": str(modeling_path),
            "modeling_cohort_sha256": source_sha256,
            "baseline_model_sha256": _sha256(baseline_path / "baseline_model.joblib"),
            "feature_schema_sha256": _sha256(baseline_path / "feature_schema.json"),
            "max_score_reproduction_error": max_score_reproduction_error,
        },
        "software": {
            "python": platform.python_version(),
            "scikit_learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "scipy": package_version("scipy"),
            "matplotlib": package_version("matplotlib"),
            "joblib": joblib.__version__,
            "chart_font": (
                "/System/Library/Fonts/Supplemental/AppleGothic.ttf"
                if Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf").exists()
                else "matplotlib_default"
            ),
        },
        "cohorts": {
            "TRAIN": int(len(train_frame)),
            "VALIDATION_AS_OF": int(len(validation_as_of)),
            "HOLDOUT": int(len(holdout_frame)),
            "freeze_date": freeze_date,
        },
        "feature_schema": feature_schema,
        "holdout_outcomes": outcomes,
        "data_quality": {
            "model_feature_missing_rows": model_feature_missing_rows,
            "dq_flags": dq_flag_summary,
        },
        "error_analysis": {
            "categorical_slice_rows": int(len(categorical_slices)),
            "numeric_slice_rows": int(len(numeric_slices)),
            "fpr_supported_slices": int(categorical_slices["fpr_support_pass"].sum())
            + int(numeric_slices["fpr_support_pass"].sum()),
            "fnr_supported_slices": int(categorical_slices["fnr_support_pass"].sum())
            + int(numeric_slices["fnr_support_pass"].sum()),
            "holdout_cutpoints_are_exploratory": True,
        },
        "drift": {
            "reference_split": "TRAIN",
            "numeric_alerts": int(numeric_drift_frame["screening_status"].eq("ALERT").sum()),
            "categorical_alerts": int(
                categorical_drift_frame["screening_status"].eq("ALERT").sum()
            ),
            "psi_smoothing": "jeffreys_0.5",
            "jensen_shannon_log_base": 2,
        },
        "calibration": calibration_summary,
        "shap": shap_summary,
        "interpretation_limit": INTERPRETATION_LIMIT,
    }
    report = _build_report(
        summary=summary,
        categorical_slices=categorical_slices,
        numeric_slices=numeric_slices,
        numeric_drift_frame=numeric_drift_frame,
        categorical_drift_frame=categorical_drift_frame,
        label_drift_frame=label_drift_frame,
        calibration_metrics_frame=calibration_metrics_frame,
        equipment_candidates=equipment_candidates,
        holdout_errors=holdout_errors,
    )
    (output_path / "diagnostic_report.md").write_text(report, encoding="utf-8")
    chart_names: list[str] = []
    if create_charts:
        chart_names = _create_charts(
            output_path,
            outcomes=outcomes,
            numeric_drift_frame=numeric_drift_frame,
            calibration_bins_frame=calibration_bins_frame,
            selected_method=selected_method,
            equipment_candidates=equipment_candidates,
        )
    summary["charts"] = chart_names
    (output_path / "diagnostic_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate SFEP Steel holdout error, drift, calibration, and SHAP diagnostics."
    )
    parser.add_argument("--modeling-cohort", required=True, type=Path)
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--calibration-folds", type=int, default=5)
    parser.add_argument("--min-slice-support", type=int, default=30)
    parser.add_argument("--top-shap-per-coil", type=int, default=5)
    parser.add_argument("--no-charts", action="store_true")
    parser.add_argument("--random-state", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = run_diagnostics(
        args.modeling_cohort,
        args.baseline_dir,
        args.output_dir,
        calibration_folds=args.calibration_folds,
        min_slice_support=args.min_slice_support,
        top_shap_per_coil=args.top_shap_per_coil,
        create_charts=not args.no_charts,
        random_state=args.random_state,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
