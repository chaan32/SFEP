from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score, roc_curve
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from training.steel import train_baseline as baseline


CHALLENGER_VERSION = "steel-quality-challenger-v0.2"
DEFAULT_VALIDATION_FREEZE_DATE = "2025-09-01"
INCUMBENT_MODEL_NAME = "incumbent_logistic"
DEFAULT_CANDIDATE_NAMES = (
    "logistic_l2_c0_1",
    "logistic_l2_c1",
    "logistic_l2_c10",
    "logistic_l1_c0_1",
    "logistic_l1_c1",
    "random_forest_depth6_leaf10",
    "random_forest_depth10_leaf20",
    "hist_gradient_boosting",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_feature_schema(frame: pd.DataFrame) -> dict[str, object]:
    train_frame = frame.loc[frame["dataset_split"].eq("TRAIN")].reset_index(drop=True)
    numeric, categorical, excluded = baseline.select_feature_columns(train_frame)
    eligible = set(numeric) | set(categorical)
    return {
        "included": [column for column in frame.columns if column in eligible],
        "numeric": numeric,
        "categorical": categorical,
        "excluded": excluded,
        "target": "judge",
        "positive_label": baseline.POSITIVE_LABEL,
    }


def _validate_baseline_lineage(
    artifact: dict[str, object],
    metrics: dict[str, object],
    modeling_cohort_sha256: str,
    *,
    expected_feature_schema: dict[str, object],
    expected_freeze_date: str | None = None,
) -> None:
    artifact_cohort_sha256 = artifact.get("modeling_cohort_sha256")
    metrics_source = metrics.get("source")
    metrics_cohort_sha256 = (
        metrics_source.get("sha256") if isinstance(metrics_source, dict) else None
    )
    if (
        artifact_cohort_sha256 != modeling_cohort_sha256
        or metrics_cohort_sha256 != modeling_cohort_sha256
    ):
        raise ValueError("baseline cohort SHA-256 does not match the modeling cohort")
    if metrics.get("selected_model") != "logistic_regression":
        raise ValueError("baseline selected model must be logistic_regression")
    if (
        artifact.get("baseline_version") != baseline.BASELINE_VERSION
        or metrics.get("baseline_version") != baseline.BASELINE_VERSION
    ):
        raise ValueError(
            f"baseline version does not equal expected baseline version {baseline.BASELINE_VERSION}"
        )
    if artifact.get("baseline_version") != metrics.get("baseline_version"):
        raise ValueError("baseline version differs between model artifact and metrics")
    if (
        artifact.get("feature_schema") != expected_feature_schema
        or metrics.get("feature_schema") != expected_feature_schema
    ):
        raise ValueError("baseline feature schema does not match expected feature schema")
    if artifact.get("feature_schema") != metrics.get("feature_schema"):
        raise ValueError("baseline feature schema differs between model artifact and metrics")
    artifact_schema = artifact.get("feature_schema")
    schema_positive_label = (
        artifact_schema.get("positive_label") if isinstance(artifact_schema, dict) else None
    )
    if (
        artifact.get("positive_label") != baseline.POSITIVE_LABEL
        or schema_positive_label != baseline.POSITIVE_LABEL
    ):
        raise ValueError("baseline positive label must be 불량")
    pipeline = artifact.get("pipeline")
    if not isinstance(pipeline, Pipeline):
        raise ValueError("baseline artifact pipeline is missing or invalid")
    classifier = pipeline.named_steps.get("classifier")
    if not isinstance(classifier, LogisticRegression):
        raise ValueError("baseline selected model artifact must contain LogisticRegression")
    expected_pipeline_features = np.asarray(
        [
            *expected_feature_schema["numeric"],
            *expected_feature_schema["categorical"],
        ],
        dtype=object,
    )
    if not np.array_equal(
        getattr(pipeline, "feature_names_in_", None), expected_pipeline_features
    ):
        raise ValueError("baseline pipeline feature contract does not match expected feature schema")
    if not np.array_equal(getattr(classifier, "classes_", None), np.array([0, 1])):
        raise ValueError("baseline classifier class order must be [0, 1]")
    selection_protocol = metrics.get("selection_protocol")
    if not isinstance(selection_protocol, dict):
        raise ValueError("baseline selection protocol is missing")
    if selection_protocol.get("holdout_used_for_selection") is not False:
        raise ValueError("baseline selection protocol must exclude HOLDOUT")
    if (
        expected_freeze_date is not None
        and selection_protocol.get("freeze_date") != expected_freeze_date
    ):
        raise ValueError("baseline validation freeze date does not match challenger protocol")


def select_threshold_at_fpr_cap(
    y_true: pd.Series | np.ndarray,
    risk_score: pd.Series | np.ndarray,
    *,
    false_alarm_cap: float,
) -> tuple[float, dict[str, object]]:
    """Choose the highest-recall validation threshold that respects an FPR cap."""
    if not 0.0 <= false_alarm_cap <= 1.0:
        raise ValueError("false_alarm_cap must be between 0 and 1")
    truth = np.asarray(y_true, dtype=int)
    scores = np.asarray(risk_score, dtype=float)
    if len(truth) != len(scores) or len(truth) == 0:
        raise ValueError("y_true and risk_score must be non-empty and have equal length")
    if set(np.unique(truth)) != {0, 1}:
        raise ValueError("threshold selection requires both negative and positive labels")
    if not np.isfinite(scores).all():
        raise ValueError("risk_score must contain only finite values")

    false_positive_rates, true_positive_rates, thresholds = roc_curve(
        truth,
        scores,
        drop_intermediate=False,
    )
    eligible = np.flatnonzero(false_positive_rates <= false_alarm_cap + 1e-12)
    positive_count = int(truth.sum())
    negative_count = int(len(truth) - positive_count)

    def rank(index: int) -> tuple[float, float, float]:
        true_positive = int(round(true_positive_rates[index] * positive_count))
        false_positive = int(round(false_positive_rates[index] * negative_count))
        predicted_positive = true_positive + false_positive
        precision = 0.0 if predicted_positive == 0 else true_positive / predicted_positive
        threshold = float(thresholds[index])
        return float(true_positive_rates[index]), float(precision), threshold

    selected_index = max((int(index) for index in eligible), key=rank)
    selected_threshold = float(thresholds[selected_index])
    if not np.isfinite(selected_threshold):
        selected_threshold = float(np.nextafter(np.max(scores), np.inf))
        if not np.isfinite(selected_threshold):
            raise ValueError("unable to create a finite no-alert threshold")
    metrics = baseline.binary_metrics(truth, scores, threshold=selected_threshold)
    if float(metrics["false_alarm_rate"]) > false_alarm_cap + 1e-12:
        raise RuntimeError("selected threshold exceeds the requested false_alarm_cap")
    return selected_threshold, metrics


def select_recommended_model(
    validation_leaderboard: pd.DataFrame,
    *,
    incumbent_model: str = INCUMBENT_MODEL_NAME,
    min_pr_auc_improvement: float = 0.0,
) -> dict[str, object]:
    """Lock a recommendation using validation ranking only."""
    required = {"model", "validation_pr_auc", "validation_roc_auc"}
    missing = sorted(required - set(validation_leaderboard.columns))
    if missing:
        raise ValueError(f"validation leaderboard is missing columns: {', '.join(missing)}")
    if min_pr_auc_improvement < 0:
        raise ValueError("min_pr_auc_improvement must be non-negative")
    incumbent_rows = validation_leaderboard.loc[
        validation_leaderboard["model"].eq(incumbent_model)
    ]
    if len(incumbent_rows) != 1:
        raise ValueError("validation leaderboard must contain exactly one incumbent row")

    incumbent = incumbent_rows.iloc[0]
    challenger_rows = validation_leaderboard.loc[
        ~validation_leaderboard["model"].eq(incumbent_model)
    ]
    if challenger_rows.empty:
        best_challenger_model = None
        best_challenger_pr_auc = None
        improvement = 0.0
        adopt = False
    else:
        best = challenger_rows.sort_values(
            ["validation_pr_auc", "validation_roc_auc", "model"],
            ascending=[False, False, True],
            kind="stable",
        ).iloc[0]
        best_challenger_model = str(best["model"])
        best_challenger_pr_auc = float(best["validation_pr_auc"])
        improvement = best_challenger_pr_auc - float(incumbent["validation_pr_auc"])
        adopt = improvement > min_pr_auc_improvement

    return {
        "status": "ADOPT_CHALLENGER" if adopt else "RETAIN_INCUMBENT",
        "recommended_model": best_challenger_model if adopt else incumbent_model,
        "incumbent_model": incumbent_model,
        "incumbent_validation_pr_auc": float(incumbent["validation_pr_auc"]),
        "best_challenger_model": best_challenger_model,
        "best_challenger_validation_pr_auc": best_challenger_pr_auc,
        "validation_pr_auc_improvement": float(improvement),
        "min_pr_auc_improvement": float(min_pr_auc_improvement),
        "selection_inputs": ["VALIDATION_AS_OF PR-AUC", "VALIDATION_AS_OF ROC-AUC tie-break"],
    }


def _build_preprocessor(
    numeric: list[str],
    categorical: list[str],
    *,
    scale_numeric: bool,
    dense_output: bool,
) -> ColumnTransformer:
    numeric_steps: list[tuple[str, object]] = [
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True))
    ]
    if scale_numeric:
        numeric_steps.append(("scaler", StandardScaler()))
    transformers: list[tuple[str, object, list[str]]] = []
    if numeric:
        transformers.append(("numeric", Pipeline(numeric_steps), numeric))
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        (
                            "imputer",
                            SimpleImputer(strategy="most_frequent", keep_empty_features=True),
                        ),
                        (
                            "onehot",
                            OneHotEncoder(
                                handle_unknown="ignore",
                                sparse_output=not dense_output,
                            ),
                        ),
                    ]
                ),
                categorical,
            )
        )
    return ColumnTransformer(
        transformers,
        remainder="drop",
        sparse_threshold=0.0 if dense_output else 0.3,
    )


def _build_candidate_pipeline(
    model_name: str,
    numeric: list[str],
    categorical: list[str],
    *,
    random_state: int,
    random_forest_estimators: int,
    n_jobs: int,
) -> Pipeline:
    logistic_options = {
        "logistic_l2_c0_1": ("l2", 0.1),
        "logistic_l2_c1": ("l2", 1.0),
        "logistic_l2_c10": ("l2", 10.0),
        "logistic_l1_c0_1": ("l1", 0.1),
        "logistic_l1_c1": ("l1", 1.0),
    }
    if model_name in logistic_options:
        penalty, regularization = logistic_options[model_name]
        classifier = LogisticRegression(
            C=regularization,
            l1_ratio=1.0 if penalty == "l1" else 0.0,
            class_weight="balanced",
            max_iter=3000,
            random_state=random_state,
            solver="liblinear",
        )
        preprocessor = _build_preprocessor(
            numeric,
            categorical,
            scale_numeric=True,
            dense_output=False,
        )
    elif model_name == "random_forest_depth6_leaf10":
        classifier = RandomForestClassifier(
            n_estimators=random_forest_estimators,
            class_weight="balanced_subsample",
            max_depth=6,
            min_samples_leaf=10,
            max_features="sqrt",
            random_state=random_state,
            n_jobs=n_jobs,
        )
        preprocessor = _build_preprocessor(
            numeric,
            categorical,
            scale_numeric=False,
            dense_output=False,
        )
    elif model_name == "random_forest_depth10_leaf20":
        classifier = RandomForestClassifier(
            n_estimators=random_forest_estimators,
            class_weight="balanced_subsample",
            max_depth=10,
            min_samples_leaf=20,
            max_features="sqrt",
            random_state=random_state,
            n_jobs=n_jobs,
        )
        preprocessor = _build_preprocessor(
            numeric,
            categorical,
            scale_numeric=False,
            dense_output=False,
        )
    elif model_name == "hist_gradient_boosting":
        classifier = HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=250,
            max_leaf_nodes=15,
            min_samples_leaf=20,
            l2_regularization=1.0,
            class_weight="balanced",
            random_state=random_state,
        )
        preprocessor = _build_preprocessor(
            numeric,
            categorical,
            scale_numeric=False,
            dense_output=True,
        )
    else:
        raise ValueError(f"unsupported challenger model: {model_name}")
    return Pipeline([("preprocessor", preprocessor), ("classifier", classifier)])


def _ranking_row(
    model_name: str,
    role: str,
    y_true: pd.Series,
    scores: np.ndarray,
) -> dict[str, object]:
    return {
        "model": model_name,
        "role": role,
        "validation_pr_auc": float(average_precision_score(y_true, scores)),
        "validation_roc_auc": float(roc_auc_score(y_true, scores)),
        "validation_brier_score": float(brier_score_loss(y_true, scores)),
        "validation_mean_risk_score": float(np.mean(scores)),
        "validation_positive_rate": float(np.mean(y_true)),
    }


def _capacity_row(
    model_name: str,
    cap: float,
    threshold: float,
    metrics: dict[str, object],
) -> dict[str, object]:
    confusion = metrics["confusion_matrix"]
    return {
        "model": model_name,
        "false_alarm_cap": float(cap),
        "selected_threshold": float(threshold),
        "actual_false_alarm_rate": float(metrics["false_alarm_rate"]),
        "recall": float(metrics["recall"]),
        "precision": float(metrics["precision"]),
        "f2": float(metrics["f2"]),
        "alert_rate": float(metrics["alert_rate"]),
        "tn": int(confusion["tn"]),
        "fp": int(confusion["fp"]),
        "fn": int(confusion["fn"]),
        "tp": int(confusion["tp"]),
    }


def _prediction_frame(
    source: pd.DataFrame,
    y_true: pd.Series,
    scores: np.ndarray,
    *,
    model_name: str,
    threshold: float,
    false_alarm_cap: float,
    evaluation_role: str,
) -> pd.DataFrame:
    identity_columns = [
        column
        for column in ("charge_id", "slab_no", "hr_coil_id", "hr_date", "judge")
        if column in source.columns
    ]
    result = source[identity_columns].reset_index(drop=True).copy()
    truth = np.asarray(y_true, dtype=int)
    predictions = (np.asarray(scores, dtype=float) >= threshold).astype(int)
    result["y_true"] = truth
    result["risk_score"] = np.asarray(scores, dtype=float)
    result["model"] = model_name
    result["validation_selected_threshold"] = float(threshold)
    result["false_alarm_cap"] = float(false_alarm_cap)
    result["predicted_label"] = np.where(
        predictions == 1,
        baseline.POSITIVE_LABEL,
        baseline.NEGATIVE_LABEL,
    )
    result["outcome"] = np.select(
        [
            (truth == 0) & (predictions == 0),
            (truth == 0) & (predictions == 1),
            (truth == 1) & (predictions == 0),
            (truth == 1) & (predictions == 1),
        ],
        ["TN", "FP", "FN", "TP"],
        default="INVALID",
    )
    result["evaluation_role"] = evaluation_role
    return result


def _report_markdown(summary: dict[str, object]) -> str:
    recommendation = summary["recommendation"]
    validation = summary["policy_operating_point"]["validation_as_of"]
    holdout = summary["policy_operating_point"]["historical_holdout"]
    incumbent_pr = recommendation["incumbent_validation_pr_auc"]
    best_pr = recommendation["best_challenger_validation_pr_auc"]
    easy_decision = (
        "새 학생을 후보로 채택했습니다."
        if recommendation["status"] == "ADOPT_CHALLENGER"
        else "새 학생이 확실히 더 낫지 않아 기존 학생을 유지했습니다."
    )
    professional_decision = (
        f"Validation PR-AUC {incumbent_pr:.4f}에서 {best_pr:.4f}로 개선되어 Challenger를 추천했습니다."
        if recommendation["status"] == "ADOPT_CHALLENGER" and best_pr is not None
        else f"Challenger의 Validation PR-AUC가 incumbent {incumbent_pr:.4f}를 넘지 못해 기존 모델을 유지했습니다."
    )
    return "\n".join(
        [
            "# Steel 품질 Challenger 실험 보고서",
            "",
            "## 1. 고2 수준 비유",
            "",
            "기존 학생과 여러 새 학생에게 같은 모의고사를 풀렸습니다. 모의고사에서는 양품을 불량이라고 잘못 경고하는 비율을 정해진 한도 안으로 묶고, 그 안에서 실제 불량을 가장 많이 찾는 학생을 비교했습니다.",
            "",
            f"- 결과: {easy_decision}",
            f"- 운영 연습 기준: 양품 오경보를 최대 {summary['policy_operating_point']['false_alarm_cap']:.0%}까지 허용",
            f"- 모의고사에서 잡은 불량: {validation['tp']}개, 놓친 불량: {validation['fn']}개",
            f"- 이미 공개된 과거 실전시험에서는 잡은 불량: {holdout['tp']}개, 놓친 불량: {holdout['fn']}개",
            "",
            "과거 실전시험 결과는 학생을 고르는 데 사용하지 않았습니다. 다음 학기의 새로운 시험이 진짜 최종 평가입니다.",
            "따라서 현재 추천 결과는 배포 가능 모델이 아닙니다. 새로운 미래 데이터로 Shadow 검증을 통과해야 합니다.",
            "",
            "## 2. 비유를 전문 용어에 연결",
            "",
            "| 쉬운 비유 | 전문 용어 | 실제 역할 |",
            "|---|---|---|",
            "| 문제집 | TRAIN | 모델 학습에만 사용 |",
            "| 모의고사 | VALIDATION_AS_OF | 모델·임계값 선택에 사용 |",
            "| 이미 본 과거 실전시험 | HOLDOUT | 선택 완료 후 역사적 비교만 수행 |",
            "| 학생 | 모델 Pipeline | Logistic, Random Forest, Gradient Boosting |",
            "| 오경보 허용치 | False Positive Rate cap | 양품 중 FP 비율 상한 |",
            "| 불량을 잡은 비율 | Recall | 실제 불량 중 TP 비율 |",
            "",
            "## 3. 전문 결과",
            "",
            f"- 추천 상태: `{recommendation['status']}`",
            f"- 추천 모델: `{recommendation['recommended_model']}`",
            f"- {professional_decision}",
            f"- Validation 정책점 Recall: {validation['recall']:.4f}, Precision: {validation['precision']:.4f}, FPR: {validation['false_alarm_rate']:.4f}",
            f"- Historical Holdout Recall: {holdout['recall']:.4f}, Precision: {holdout['precision']:.4f}, FPR: {holdout['false_alarm_rate']:.4f}",
            "",
            "## 4. 해석 제한",
            "",
            "이 실험 설계는 이미 기존 Holdout 분석을 본 뒤 작성됐습니다. 코드상 Holdout은 선택에 사용하지 않았지만, 완전히 새로운 미래 데이터가 최종 독립 시험 역할을 해야 합니다. 미래 Shadow 검증 전에는 배포 가능 모델이 아닙니다. 또한 이 점수는 불량 원인이나 설비 고장을 증명하지 않습니다.",
            "",
        ]
    )


def run_challenger(
    modeling_cohort_csv: str | Path,
    baseline_dir: str | Path,
    output_dir: str | Path,
    *,
    random_state: int = 42,
    random_forest_estimators: int = 300,
    n_jobs: int = -1,
    candidate_names: Iterable[str] = DEFAULT_CANDIDATE_NAMES,
    alert_fpr_caps: Iterable[float] = (0.10, 0.15, 0.20),
    policy_fpr_cap: float = 0.15,
    min_pr_auc_improvement: float = 0.0,
    validation_freeze_date: str = DEFAULT_VALIDATION_FREEZE_DATE,
) -> dict[str, object]:
    if validation_freeze_date != DEFAULT_VALIDATION_FREEZE_DATE:
        raise ValueError(
            "validation_freeze_date is immutable and must be "
            f"{DEFAULT_VALIDATION_FREEZE_DATE}"
        )
    input_path = Path(modeling_cohort_csv)
    baseline_path = Path(baseline_dir)
    model_path = baseline_path / "baseline_model.joblib"
    metrics_path = baseline_path / "baseline_metrics.json"
    if not model_path.is_file() or not metrics_path.is_file():
        raise FileNotFoundError("baseline_dir must contain baseline_model.joblib and baseline_metrics.json")

    candidate_names = tuple(candidate_names)
    if not candidate_names or len(set(candidate_names)) != len(candidate_names):
        raise ValueError("candidate_names must be non-empty and unique")
    unknown_candidates = sorted(set(candidate_names) - set(DEFAULT_CANDIDATE_NAMES))
    if unknown_candidates:
        raise ValueError(f"unsupported challenger models: {', '.join(unknown_candidates)}")
    caps = tuple(float(cap) for cap in alert_fpr_caps)
    if not caps or len(set(caps)) != len(caps):
        raise ValueError("alert_fpr_caps must be non-empty and unique")
    if any(not 0.0 <= cap <= 1.0 for cap in caps):
        raise ValueError("every alert FPR cap must be between 0 and 1")
    if policy_fpr_cap not in caps:
        raise ValueError("policy_fpr_cap must be included in alert_fpr_caps")

    frame = pd.read_csv(
        input_path,
        encoding="utf-8-sig",
        dtype={"charge_id": "string", "slab_no": "string", "hr_coil_id": "string"},
    )
    baseline._validate_modeling_frame(frame)
    overlap = baseline._split_charge_overlap(frame)
    if overlap:
        raise ValueError(f"charge_id leakage detected across splits: {overlap} overlapping charges")

    freeze_timestamp = pd.to_datetime(
        validation_freeze_date,
        format="%Y-%m-%d",
        errors="coerce",
    )
    if pd.isna(freeze_timestamp):
        raise ValueError("validation_freeze_date must use YYYY-MM-DD format")
    freeze_date = freeze_timestamp.strftime("%Y-%m-%d")
    modeling_cohort_sha256 = _sha256(input_path)
    incumbent_artifact = joblib.load(model_path)
    baseline_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    expected_feature_schema = _expected_feature_schema(frame)
    _validate_baseline_lineage(
        incumbent_artifact,
        baseline_metrics,
        modeling_cohort_sha256,
        expected_feature_schema=expected_feature_schema,
        expected_freeze_date=freeze_date,
    )
    feature_schema = expected_feature_schema
    numeric = list(feature_schema["numeric"])
    categorical = list(feature_schema["categorical"])
    included = list(feature_schema["included"])
    if set(included) != set(numeric) | set(categorical):
        raise ValueError("baseline feature schema is internally inconsistent")
    missing_features = sorted(set(included) - set(frame.columns))
    if missing_features:
        raise ValueError(f"modeling cohort is missing baseline features: {', '.join(missing_features)}")

    train_frame = frame.loc[frame["dataset_split"].eq("TRAIN")].reset_index(drop=True)
    validation_total_frame = frame.loc[
        frame["dataset_split"].eq("VALIDATION")
    ].reset_index(drop=True)
    train_features = baseline._prepare_features(train_frame, numeric, categorical)
    validation_total_features = baseline._prepare_features(
        validation_total_frame,
        numeric,
        categorical,
    )
    train_target = train_frame["judge"].eq(baseline.POSITIVE_LABEL).astype(int)
    validation_total_target = validation_total_frame["judge"].eq(
        baseline.POSITIVE_LABEL
    ).astype(int)
    validation_mask = baseline.validation_as_of_mask(validation_total_frame, freeze_date)
    validation_frame = validation_total_frame.loc[validation_mask].reset_index(drop=True)
    validation_features = validation_total_features.loc[validation_mask].reset_index(drop=True)
    validation_target = validation_total_target.loc[validation_mask].reset_index(drop=True)
    if set(validation_target.unique()) != {0, 1}:
        raise ValueError("VALIDATION_AS_OF must contain both labels")

    pipelines: dict[str, Pipeline] = {INCUMBENT_MODEL_NAME: incumbent_artifact["pipeline"]}
    validation_scores: dict[str, np.ndarray] = {
        INCUMBENT_MODEL_NAME: pipelines[INCUMBENT_MODEL_NAME].predict_proba(
            validation_features
        )[:, 1]
    }
    leaderboard_rows = [
        _ranking_row(
            INCUMBENT_MODEL_NAME,
            "INCUMBENT",
            validation_target,
            validation_scores[INCUMBENT_MODEL_NAME],
        )
    ]
    for model_name in candidate_names:
        pipeline = _build_candidate_pipeline(
            model_name,
            numeric,
            categorical,
            random_state=random_state,
            random_forest_estimators=random_forest_estimators,
            n_jobs=n_jobs,
        )
        pipeline.fit(train_features, train_target)
        scores = pipeline.predict_proba(validation_features)[:, 1]
        pipelines[model_name] = pipeline
        validation_scores[model_name] = scores
        leaderboard_rows.append(
            _ranking_row(model_name, "CHALLENGER", validation_target, scores)
        )

    leaderboard = pd.DataFrame(leaderboard_rows)
    recommendation = select_recommended_model(
        leaderboard,
        incumbent_model=INCUMBENT_MODEL_NAME,
        min_pr_auc_improvement=min_pr_auc_improvement,
    )
    recommended_model = str(recommendation["recommended_model"])
    leaderboard["recommended_on_validation_only"] = leaderboard["model"].eq(recommended_model)
    leaderboard = leaderboard.sort_values(
        ["validation_pr_auc", "validation_roc_auc", "model"],
        ascending=[False, False, True],
        kind="stable",
    ).reset_index(drop=True)

    capacity_rows: list[dict[str, object]] = []
    for model_name, scores in validation_scores.items():
        for cap in caps:
            threshold, metrics = select_threshold_at_fpr_cap(
                validation_target,
                scores,
                false_alarm_cap=cap,
            )
            capacity_rows.append(_capacity_row(model_name, cap, threshold, metrics))
    validation_capacity = pd.DataFrame(capacity_rows)
    recommended_capacity = validation_capacity.loc[
        validation_capacity["model"].eq(recommended_model)
    ].copy()
    validation_thresholds = {
        str(row.false_alarm_cap): float(row.selected_threshold)
        for row in recommended_capacity.itertuples(index=False)
    }
    policy_validation_row = recommended_capacity.loc[
        recommended_capacity["false_alarm_cap"].eq(policy_fpr_cap)
    ].iloc[0]

    # The recommendation is fully locked above. HOLDOUT features, target, and scores are
    # materialized only from here onward.
    holdout_frame = frame.loc[frame["dataset_split"].eq("HOLDOUT")].reset_index(drop=True)
    holdout_features = baseline._prepare_features(holdout_frame, numeric, categorical)
    holdout_target = holdout_frame["judge"].eq(baseline.POSITIVE_LABEL).astype(int)
    historical_rows: list[dict[str, object]] = []
    holdout_scores: dict[str, np.ndarray] = {}
    historical_models = list(dict.fromkeys([INCUMBENT_MODEL_NAME, recommended_model]))
    for model_name in historical_models:
        scores = pipelines[model_name].predict_proba(holdout_features)[:, 1]
        holdout_scores[model_name] = scores
        model_capacity = validation_capacity.loc[validation_capacity["model"].eq(model_name)]
        for capacity in model_capacity.itertuples(index=False):
            metrics = baseline.binary_metrics(
                holdout_target,
                scores,
                threshold=float(capacity.selected_threshold),
            )
            row = _capacity_row(
                model_name,
                float(capacity.false_alarm_cap),
                float(capacity.selected_threshold),
                metrics,
            )
            row["threshold_source"] = "VALIDATION_AS_OF"
            row["evaluation_role"] = "HISTORICAL_HOLDOUT_NOT_FOR_SELECTION"
            historical_rows.append(row)
    historical_holdout = pd.DataFrame(historical_rows)
    policy_holdout_row = historical_holdout.loc[
        historical_holdout["model"].eq(recommended_model)
        & historical_holdout["false_alarm_cap"].eq(policy_fpr_cap)
    ].iloc[0]
    policy_threshold = float(policy_validation_row["selected_threshold"])

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    leaderboard.to_csv(
        output_path / "validation_model_leaderboard.csv", index=False, encoding="utf-8-sig"
    )
    validation_capacity.to_csv(
        output_path / "validation_alert_capacity.csv", index=False, encoding="utf-8-sig"
    )
    historical_holdout.to_csv(
        output_path / "historical_holdout_comparison.csv", index=False, encoding="utf-8-sig"
    )
    _prediction_frame(
        validation_frame,
        validation_target,
        validation_scores[recommended_model],
        model_name=recommended_model,
        threshold=policy_threshold,
        false_alarm_cap=policy_fpr_cap,
        evaluation_role="VALIDATION_AS_OF_SELECTION",
    ).to_csv(
        output_path / "validation_recommended_predictions.csv",
        index=False,
        encoding="utf-8-sig",
    )
    _prediction_frame(
        holdout_frame,
        holdout_target,
        holdout_scores[recommended_model],
        model_name=recommended_model,
        threshold=policy_threshold,
        false_alarm_cap=policy_fpr_cap,
        evaluation_role="HISTORICAL_HOLDOUT_NOT_FOR_SELECTION",
    ).to_csv(
        output_path / "historical_holdout_predictions.csv",
        index=False,
        encoding="utf-8-sig",
    )
    joblib.dump(
        {
            "pipeline": pipelines[recommended_model],
            "model_name": recommended_model,
            "recommendation_status": recommendation["status"],
            "validation_selected_thresholds_by_fpr_cap": validation_thresholds,
            "policy_fpr_cap": float(policy_fpr_cap),
            "policy_threshold": policy_threshold,
            "feature_schema": feature_schema,
            "challenger_version": CHALLENGER_VERSION,
            "holdout_used_for_selection": False,
            "deployment_eligible": False,
            "required_next_step": "FUTURE_SHADOW_VALIDATION",
            "modeling_cohort_sha256": _sha256(input_path),
            "baseline_model_sha256": _sha256(model_path),
        },
        output_path / "recommended_model.joblib",
    )

    def policy_metrics(row: pd.Series) -> dict[str, object]:
        return {
            "threshold": float(row["selected_threshold"]),
            "false_alarm_rate": float(row["actual_false_alarm_rate"]),
            "recall": float(row["recall"]),
            "precision": float(row["precision"]),
            "f2": float(row["f2"]),
            "alert_rate": float(row["alert_rate"]),
            "tn": int(row["tn"]),
            "fp": int(row["fp"]),
            "fn": int(row["fn"]),
            "tp": int(row["tp"]),
        }

    summary: dict[str, object] = {
        "challenger_version": CHALLENGER_VERSION,
        "source": {
            "modeling_cohort": str(input_path),
            "modeling_cohort_sha256": _sha256(input_path),
            "baseline_model_sha256": _sha256(model_path),
            "baseline_metrics_sha256": _sha256(metrics_path),
        },
        "software": {
            "scikit_learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
        "random_state": int(random_state),
        "row_counts": {
            "TRAIN": int(len(train_frame)),
            "VALIDATION_TOTAL": int(len(validation_total_frame)),
            "VALIDATION_AS_OF": int(len(validation_frame)),
            "HOLDOUT": int(len(holdout_frame)),
        },
        "candidate_models": list(candidate_names),
        "selection_protocol": {
            "training_split": "TRAIN",
            "selection_split": "VALIDATION_AS_OF",
            "ranking_metric": "PR-AUC",
            "ranking_tie_break": "ROC-AUC",
            "threshold_selection_split": "VALIDATION_AS_OF",
            "threshold_policy": "maximize recall subject to false-positive-rate cap",
            "alert_fpr_caps": list(caps),
            "policy_fpr_cap": float(policy_fpr_cap),
            "freeze_date": freeze_date,
            "label_availability_proxy": "ap_date",
            "holdout_used_for_selection": False,
            "recommendation_locked_before_holdout_evaluation": True,
            "historical_holdout_role": "diagnostic comparison only; future data required for independent confirmation",
        },
        "validation_model_leaderboard": leaderboard.to_dict(orient="records"),
        "recommendation": recommendation,
        "deployment_gate": {
            "status": "FUTURE_SHADOW_VALIDATION_REQUIRED",
            "deployment_eligible": False,
            "reason": (
                "The historical HOLDOUT was already inspected before this challenger design; "
                "a new future cohort is required for independent confirmation."
            ),
        },
        "policy_operating_point": {
            "false_alarm_cap": float(policy_fpr_cap),
            "validation_as_of": policy_metrics(policy_validation_row),
            "historical_holdout": policy_metrics(policy_holdout_row),
        },
        "integrity": {
            "one_row_per_coil": bool(not frame["hr_coil_id"].duplicated().any()),
            "split_charge_overlap": int(overlap),
            "ap_operating_feature_count": int(
                sum(column.startswith("ap_") for column in included)
            ),
            "holdout_used_for_selection": False,
        },
        "interpretation_limit": (
            "Challenger ranking is validation evidence, not causal proof or an unbiased future deployment estimate."
        ),
    }
    (output_path / "challenger_metrics.json").write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_path / "challenger_report.md").write_text(
        _report_markdown(summary),
        encoding="utf-8",
    )
    return summary


def _parse_caps(value: str) -> tuple[float, ...]:
    try:
        return tuple(float(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("FPR caps must be comma-separated numbers") from exc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train validation-only steel quality challengers without using HOLDOUT for selection."
    )
    parser.add_argument("--modeling-cohort", required=True)
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--random-forest-estimators", type=int, default=300)
    parser.add_argument("--n-jobs", type=int, default=-1)
    parser.add_argument("--alert-fpr-caps", type=_parse_caps, default=(0.10, 0.15, 0.20))
    parser.add_argument("--policy-fpr-cap", type=float, default=0.15)
    parser.add_argument("--min-pr-auc-improvement", type=float, default=0.0)
    parser.add_argument(
        "--validation-freeze-date",
        default=DEFAULT_VALIDATION_FREEZE_DATE,
        help="Immutable label-availability cutoff in YYYY-MM-DD format.",
    )
    args = parser.parse_args()
    summary = run_challenger(
        args.modeling_cohort,
        args.baseline_dir,
        args.output_dir,
        random_state=args.random_state,
        random_forest_estimators=args.random_forest_estimators,
        n_jobs=args.n_jobs,
        alert_fpr_caps=args.alert_fpr_caps,
        policy_fpr_cap=args.policy_fpr_cap,
        min_pr_auc_improvement=args.min_pr_auc_improvement,
        validation_freeze_date=args.validation_freeze_date,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
