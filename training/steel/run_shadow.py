from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

import joblib
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    fbeta_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)

from training.steel.shadow_contract import (
    ENGINEERED_FEATURES,
    IDENTITY_COLUMNS,
    LABEL_COLUMNS,
    add_engineered_features,
    atomic_copy_file,
    atomic_write_csv,
    atomic_write_json,
    atomic_write_text,
    make_batch_id,
    read_json_strict,
    sha256_file,
    validate_feature_batch,
    validate_label_batch,
)
from training.steel import train_baseline as baseline
from training.steel.diagnostics.calibration import apply_calibrator
from training.steel.diagnostics.drift import categorical_drift, numeric_drift


REGISTRY_VERSION = "steel-shadow-registry-v0.1"
POLICY_FPR_CAP = 0.15
BASELINE_VERSION = "steel-quality-baseline-v0.3"
CHALLENGER_VERSION = "steel-quality-challenger-v0.2"
NEXTGEN_VERSION = "steel-quality-nextgen-v0.1"
INDEPENDENT_EVIDENCE_STATUS = "INDEPENDENT_FUTURE_SHADOW"
DRY_RUN_EVIDENCE_STATUS = "NON_INDEPENDENT_DRY_RUN"
ALLOWED_EVIDENCE_STATUSES = frozenset(
    {INDEPENDENT_EVIDENCE_STATUS, DRY_RUN_EVIDENCE_STATUS}
)
OUTPUT_MODE_VERSION = "steel-shadow-output-mode-v0.1"


class ShadowDomainError(ValueError):
    pass


class LedgerIntegrityError(ShadowDomainError):
    pass


class RegistryUnavailableError(ShadowDomainError):
    pass


class CoilAlreadyScoredError(ShadowDomainError):
    pass


class FeatureBatchError(ShadowDomainError):
    pass


class LabelBatchError(ShadowDomainError):
    pass


def _utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _validate_evidence_status(evidence_status: str, output_dir: Path) -> None:
    if evidence_status not in ALLOWED_EVIDENCE_STATUSES:
        raise ValueError("unsupported evidence_status")
    output_path = Path(output_dir)
    dry_run_directory = any(
        "dry_run" in name for name in {output_path.name, output_path.resolve().name}
    )
    if evidence_status == DRY_RUN_EVIDENCE_STATUS and not dry_run_directory:
        raise ValueError("Dry Run evidence_status requires a dry_run output directory")
    if evidence_status == INDEPENDENT_EVIDENCE_STATUS and dry_run_directory:
        raise ValueError(
            "independent evidence_status must not use a dry_run output directory"
        )


def _output_mode_path(output_dir: Path) -> Path:
    return Path(output_dir) / ".shadow-mode.json"


def _bind_output_mode(output_dir: Path, evidence_status: str) -> Path:
    _validate_evidence_status(evidence_status, output_dir)
    mode_path = _output_mode_path(output_dir)
    expected = {
        "mode_version": OUTPUT_MODE_VERSION,
        "evidence_status": evidence_status,
    }
    if mode_path.is_file():
        if read_json_strict(mode_path) != expected:
            raise ValueError("output-root mode manifest mismatch")
        return mode_path
    atomic_write_json(expected, mode_path)
    return mode_path


def _validate_output_mode(output_dir: Path, evidence_status: str) -> Path:
    _validate_evidence_status(evidence_status, output_dir)
    mode_path = _output_mode_path(output_dir)
    if not mode_path.is_file():
        raise ValueError("output-root mode manifest is missing")
    expected = {
        "mode_version": OUTPUT_MODE_VERSION,
        "evidence_status": evidence_status,
    }
    if read_json_strict(mode_path) != expected:
        raise ValueError("output-root mode manifest mismatch")
    return mode_path


def _json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@contextmanager
def _output_lock(output_dir: Path):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = output_dir / ".shadow.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _predictor_from_artifact(artifact: dict[str, object]):
    predictor = artifact.get("pipeline", artifact.get("predictor"))
    if predictor is None or not hasattr(predictor, "predict_proba"):
        raise ValueError("model artifact predictor is missing or invalid")
    return predictor


def _class_order(predictor) -> list[int]:
    classifier = (
        predictor.named_steps.get("classifier")
        if isinstance(predictor, Pipeline)
        else predictor
    )
    classes = getattr(classifier, "classes_", None)
    if classes is None:
        classes = getattr(predictor, "classes_", None)
    if classes is None:
        raise ValueError("model artifact class order is missing")
    return [int(value) for value in np.asarray(classes).tolist()]


def _feature_order(schema: dict[str, object]) -> list[str]:
    numeric = schema.get("numeric")
    categorical = schema.get("categorical")
    included = schema.get("included")
    if not all(isinstance(value, list) for value in (numeric, categorical, included)):
        raise ValueError("model artifact feature schema is invalid")
    order = [*numeric, *categorical]
    if set(included) != set(order) or len(included) != len(order):
        raise ValueError("model artifact feature schema is internally inconsistent")
    return [str(column) for column in order]


def _validate_predictor_contract(
    artifact: dict[str, object],
    schema: dict[str, object],
) -> tuple[object, list[int]]:
    predictor = _predictor_from_artifact(artifact)
    expected_order = np.asarray(_feature_order(schema), dtype=object)
    actual_order = getattr(predictor, "feature_names_in_", None)
    if not np.array_equal(actual_order, expected_order):
        raise ValueError("model artifact feature order does not match feature schema")
    classes = _class_order(predictor)
    if classes != [0, 1]:
        raise ValueError("model artifact class order must be [0, 1]")
    if artifact.get("ensemble_children") is not None:
        from training.steel.nextgen_model import validate_ensemble_integrity

        validate_ensemble_integrity(artifact)
    return predictor, classes


def _model_entry(
    artifact_path: Path,
    *,
    role: str,
    model_id: str,
    threshold: float,
    calibrator_path: Path | None = None,
) -> dict[str, object]:
    artifact = joblib.load(artifact_path)
    if not isinstance(artifact, dict):
        raise ValueError("model artifact root must be an object")
    schema = artifact.get("feature_schema")
    if not isinstance(schema, dict):
        raise ValueError("model artifact feature schema is missing")
    _, classes = _validate_predictor_contract(artifact, schema)
    if not math.isfinite(float(threshold)):
        raise ValueError("model policy threshold must be finite")
    if artifact.get("deployment_eligible") is True:
        raise ValueError("shadow registry cannot register a deployment-eligible artifact")
    positive_label = artifact.get("positive_label", schema.get("positive_label"))
    if positive_label != "불량":
        raise ValueError("model artifact positive label must be 불량")
    cohort_sha256 = artifact.get("modeling_cohort_sha256")
    if not isinstance(cohort_sha256, str) or len(cohort_sha256) != 64:
        raise ValueError("model artifact modeling cohort SHA-256 is missing")
    resolved_artifact = artifact_path.resolve()
    resolved_calibrator = calibrator_path.resolve() if calibrator_path else None
    artifact_sha256 = sha256_file(resolved_artifact)
    if role == "reference_baseline":
        if artifact.get("baseline_version") != BASELINE_VERSION:
            raise ValueError("baseline artifact version mismatch")
        expected_model_id = f"{BASELINE_VERSION}:logistic_regression"
    elif role == "shadow_incumbent":
        if artifact.get("challenger_version") != CHALLENGER_VERSION:
            raise ValueError("challenger artifact version mismatch")
        if artifact.get("holdout_used_for_selection") is not False:
            raise ValueError("challenger artifact used HOLDOUT for selection")
        expected_model_id = f"{CHALLENGER_VERSION}:{artifact.get('model_name')}"
    elif role == "next_challenger":
        if artifact.get("nextgen_version") != NEXTGEN_VERSION:
            raise ValueError("NextGen artifact version mismatch")
        if artifact.get("holdout_used_for_selection") is not False:
            raise ValueError("NextGen artifact used HOLDOUT for selection")
        if artifact.get("predictor_source_sha256") != sha256_file(
            Path(__file__).with_name("nextgen_model.py")
        ):
            raise ValueError("NextGen predictor source SHA-256 mismatch")
        if artifact.get("feature_schema_sha256") != _json_sha256(schema):
            raise ValueError("NextGen feature schema SHA-256 mismatch")
        for field in (
            "selection_oof_scores_sha256",
            "oof_fold_assignments_sha256",
            "development_identity_sha256",
        ):
            if not isinstance(artifact.get(field), str) or len(str(artifact[field])) != 64:
                raise ValueError(f"NextGen {field} is missing")
        expected_model_id = f"{NEXTGEN_VERSION}:{artifact.get('model_name')}"
    else:
        raise ValueError("unsupported shadow model role")
    if model_id != expected_model_id:
        raise ValueError("model ID does not match artifact role and version")
    if resolved_calibrator is not None:
        calibrator_artifact = joblib.load(resolved_calibrator)
        if not isinstance(calibrator_artifact, dict):
            raise ValueError("calibrator artifact root must be an object")
        if calibrator_artifact.get("model_sha256") != artifact_sha256:
            raise ValueError("calibrator model SHA-256 mismatch")
        if calibrator_artifact.get("modeling_cohort_sha256") != cohort_sha256:
            raise ValueError("calibrator cohort SHA-256 mismatch")
        if calibrator_artifact.get("deployment_eligible") is not False:
            raise ValueError("calibrator deployment_eligible must be false")
        if calibrator_artifact.get("holdout_used_for_selection") is not False:
            raise ValueError("calibrator used HOLDOUT for selection")
        if calibrator_artifact.get("method") != "platt":
            raise ValueError("calibrator method must be platt")
        calibrator = calibrator_artifact.get("calibrator")
        if not hasattr(calibrator, "predict_proba") or [
            int(value) for value in np.asarray(getattr(calibrator, "classes_", [])).tolist()
        ] != [0, 1]:
            raise ValueError("calibrator class contract is invalid")
        for field in (
            "selection_oof_scores_sha256",
            "oof_fold_assignments_sha256",
            "development_identity_sha256",
            "feature_schema_sha256",
            "software",
        ):
            if calibrator_artifact.get(field) != artifact.get(field):
                raise ValueError(f"calibrator {field} lineage mismatch")
    return {
        "model_id": model_id,
        "role": role,
        "artifact_path": str(resolved_artifact),
        "artifact_sha256": artifact_sha256,
        "modeling_cohort_sha256": cohort_sha256,
        "feature_schema": schema,
        "positive_label": "불량",
        "class_order": classes,
        "policy_fpr_cap": POLICY_FPR_CAP,
        "policy_threshold": float(threshold),
        "calibrator_path": None if resolved_calibrator is None else str(resolved_calibrator),
        "calibrator_sha256": (
            None if resolved_calibrator is None else sha256_file(resolved_calibrator)
        ),
        "deployment_eligible": False,
    }


def build_registry(
    baseline_model: Path,
    challenger_model: Path,
    challenger_capacity: Path,
    output_path: Path,
    nextgen_model: Path | None = None,
    nextgen_calibrator: Path | None = None,
    *,
    reference_cohort: Path | None = None,
    now: Callable[[], pd.Timestamp] = _utc_now,
) -> dict[str, object]:
    baseline_model = Path(baseline_model)
    challenger_model = Path(challenger_model)
    if nextgen_model is not None and reference_cohort is None:
        raise ValueError("NextGen registration requires a reference cohort")
    capacity = pd.read_csv(challenger_capacity, encoding="utf-8-sig")
    required_capacity = {"model", "false_alarm_cap", "selected_threshold"}
    missing_capacity = sorted(required_capacity - set(capacity.columns))
    if missing_capacity:
        raise ValueError(
            f"challenger capacity is missing columns: {', '.join(missing_capacity)}"
        )

    def capacity_threshold(model_name: str) -> float:
        rows = capacity.loc[
            capacity["model"].eq(model_name)
            & np.isclose(capacity["false_alarm_cap"], POLICY_FPR_CAP)
        ]
        if len(rows) != 1:
            raise ValueError(
                f"challenger capacity must contain one FPR-15 row for {model_name}"
            )
        return float(rows.iloc[0]["selected_threshold"])

    baseline_artifact = joblib.load(baseline_model)
    challenger_artifact = joblib.load(challenger_model)
    if not isinstance(baseline_artifact, dict) or not isinstance(challenger_artifact, dict):
        raise ValueError("model artifact root must be an object")
    baseline_sha256 = sha256_file(baseline_model)
    if challenger_artifact.get("baseline_model_sha256") != baseline_sha256:
        raise ValueError("challenger baseline model SHA-256 mismatch")
    cohort_sha256 = baseline_artifact.get("modeling_cohort_sha256")
    if challenger_artifact.get("modeling_cohort_sha256") != cohort_sha256:
        raise ValueError("baseline and challenger cohort SHA-256 mismatch")
    baseline_id = (
        f"{baseline_artifact.get('baseline_version', 'unknown-baseline')}:"
        "logistic_regression"
    )
    challenger_name = str(challenger_artifact.get("model_name", "unknown-model"))
    challenger_id = (
        f"{challenger_artifact.get('challenger_version', 'unknown-challenger')}:"
        f"{challenger_name}"
    )
    if not math.isclose(
        float(challenger_artifact.get("policy_threshold", math.nan)),
        capacity_threshold(challenger_name),
        rel_tol=0.0,
        abs_tol=1e-15,
    ):
        raise ValueError("challenger artifact threshold does not match capacity")
    models = [
        _model_entry(
            baseline_model,
            role="reference_baseline",
            model_id=baseline_id,
            threshold=capacity_threshold("incumbent_logistic"),
        ),
        _model_entry(
            challenger_model,
            role="shadow_incumbent",
            model_id=challenger_id,
            threshold=capacity_threshold(challenger_name),
        ),
    ]
    if nextgen_model is not None:
        if nextgen_calibrator is None:
            raise ValueError("NextGen registration requires a calibrator")
        nextgen_path = Path(nextgen_model)
        nextgen_artifact = joblib.load(nextgen_path)
        if not isinstance(nextgen_artifact, dict):
            raise ValueError("NextGen artifact root must be an object")
        if nextgen_artifact.get("modeling_cohort_sha256") != cohort_sha256:
            raise ValueError("NextGen cohort SHA-256 mismatch")
        selected_thresholds = nextgen_artifact.get("selected_thresholds_by_fpr_cap")
        if (
            not isinstance(selected_thresholds, dict)
            or not math.isclose(
                float(selected_thresholds.get("0.15", math.nan)),
                float(nextgen_artifact.get("policy_threshold", math.nan)),
                rel_tol=0.0,
                abs_tol=1e-15,
            )
        ):
            raise ValueError("NextGen policy threshold lineage mismatch")
        nextgen_id = (
            f"{nextgen_artifact.get('nextgen_version', 'unknown-nextgen')}:"
            f"{nextgen_artifact.get('model_name', 'unknown-model')}"
        )
        models.append(
            _model_entry(
                nextgen_path,
                role="next_challenger",
                model_id=nextgen_id,
                threshold=float(nextgen_artifact.get("policy_threshold", math.nan)),
                calibrator_path=(
                    None if nextgen_calibrator is None else Path(nextgen_calibrator)
                ),
            )
        )
    generated_at = now()
    if generated_at.tzinfo is None:
        raise ValueError("registry timestamp must be timezone-aware")
    registry: dict[str, object] = {
        "registry_version": REGISTRY_VERSION,
        "created_at": generated_at.tz_convert("UTC").isoformat(),
        "shadow_start_at": generated_at.tz_convert("UTC").isoformat(),
        "policy_fpr_cap": POLICY_FPR_CAP,
        "deployment_eligible": False,
        "reference_cohort_path": (
            None if reference_cohort is None else str(Path(reference_cohort).resolve())
        ),
        "reference_cohort_sha256": (
            None if reference_cohort is None else sha256_file(Path(reference_cohort).resolve())
        ),
        "challenger_capacity_path": str(Path(challenger_capacity).resolve()),
        "challenger_capacity_sha256": sha256_file(Path(challenger_capacity).resolve()),
        "models": models,
    }
    if (
        registry["reference_cohort_sha256"] is not None
        and registry["reference_cohort_sha256"] != cohort_sha256
    ):
        raise ValueError("registered reference cohort SHA-256 mismatch")
    atomic_write_json(registry, Path(output_path))
    return registry


def _resolve_registry_member_path(registry_path: Path, value: object) -> Path:
    candidate = Path(str(value))
    if not candidate.is_absolute():
        candidate = registry_path.resolve().parent / candidate
    return candidate.resolve()


def load_and_validate_registry(path: Path) -> dict[str, object]:
    registry_path = Path(path)
    registry = read_json_strict(registry_path)
    if registry.get("registry_version") != REGISTRY_VERSION:
        raise ValueError("unsupported shadow registry version")
    if registry.get("deployment_eligible") is not False:
        raise ValueError("shadow registry deployment_eligible must be false")
    shadow_start_at = pd.Timestamp(registry.get("shadow_start_at"))
    if shadow_start_at.tzinfo is None:
        raise ValueError("shadow registry shadow_start_at must be timezone-aware")
    reference_path = registry.get("reference_cohort_path")
    reference_sha256 = registry.get("reference_cohort_sha256")
    if reference_path is None:
        if reference_sha256 is not None:
            raise ValueError("reference cohort SHA-256 has no reference path")
    else:
        resolved_reference = _resolve_registry_member_path(
            registry_path, reference_path
        )
        if not resolved_reference.is_file():
            raise ValueError("registered reference cohort is missing")
        if sha256_file(resolved_reference) != reference_sha256:
            raise ValueError("registered reference cohort SHA-256 mismatch")
        registry["reference_cohort_path"] = str(resolved_reference)
    capacity_path = _resolve_registry_member_path(
        registry_path, registry.get("challenger_capacity_path", "")
    )
    if not capacity_path.is_file():
        raise ValueError("registered challenger capacity is missing")
    if sha256_file(capacity_path) != registry.get("challenger_capacity_sha256"):
        raise ValueError("registered challenger capacity SHA-256 mismatch")
    registry["challenger_capacity_path"] = str(capacity_path)
    models = registry.get("models")
    if not isinstance(models, list) or len(models) < 2:
        raise ValueError("shadow registry must contain at least two models")
    roles = [model.get("role") if isinstance(model, dict) else None for model in models]
    if len(set(roles)) != len(roles):
        raise ValueError("shadow registry model roles must be unique")
    if "next_challenger" in roles and reference_path is None:
        raise ValueError("NextGen shadow registry requires a reference cohort")
    artifacts_by_role: dict[str, tuple[dict[str, object], dict[str, object]]] = {}
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("shadow registry model entry must be an object")
        artifact_path = _resolve_registry_member_path(
            registry_path, model.get("artifact_path", "")
        )
        if not artifact_path.is_file():
            raise ValueError("registered model artifact is missing")
        if sha256_file(artifact_path) != model.get("artifact_sha256"):
            raise ValueError("registered model artifact SHA-256 mismatch")
        model["artifact_path"] = str(artifact_path)
        artifact = joblib.load(artifact_path)
        if not isinstance(artifact, dict):
            raise ValueError("registered model artifact root must be an object")
        schema = artifact.get("feature_schema")
        if model.get("feature_schema") != schema:
            raise ValueError("registered model feature schema mismatch")
        if not isinstance(schema, dict):
            raise ValueError("registered model feature schema is missing")
        _, actual_classes = _validate_predictor_contract(artifact, schema)
        if model.get("class_order") != [0, 1] or actual_classes != [0, 1]:
            raise ValueError("registered model class order must be [0, 1]")
        if model.get("positive_label") != "불량":
            raise ValueError("registered model positive label must be 불량")
        if artifact.get("modeling_cohort_sha256") != model.get(
            "modeling_cohort_sha256"
        ):
            raise ValueError("registered model cohort SHA-256 mismatch")
        threshold = model.get("policy_threshold")
        if not isinstance(threshold, (int, float)) or not math.isfinite(float(threshold)):
            raise ValueError("registered model policy threshold must be finite")
        if model.get("deployment_eligible") is not False:
            raise ValueError("registered model deployment_eligible must be false")
        expected_ids = {
            "reference_baseline": f"{BASELINE_VERSION}:logistic_regression",
            "shadow_incumbent": (
                f"{CHALLENGER_VERSION}:{artifact.get('model_name')}"
            ),
            "next_challenger": f"{NEXTGEN_VERSION}:{artifact.get('model_name')}",
        }
        role = str(model.get("role"))
        if role not in expected_ids or model.get("model_id") != expected_ids[role]:
            raise ValueError("registered model ID does not match role and version")
        artifacts_by_role[role] = (artifact, model)
        calibrator_path = model.get("calibrator_path")
        calibrator_sha256 = model.get("calibrator_sha256")
        if calibrator_path is None:
            if calibrator_sha256 is not None:
                raise ValueError("registered calibrator SHA-256 has no calibrator path")
        else:
            resolved_calibrator = _resolve_registry_member_path(
                registry_path, calibrator_path
            )
            if not resolved_calibrator.is_file():
                raise ValueError("registered calibrator is missing")
            if sha256_file(resolved_calibrator) != calibrator_sha256:
                raise ValueError("registered calibrator SHA-256 mismatch")
            model["calibrator_path"] = str(resolved_calibrator)
            calibrator_artifact = joblib.load(resolved_calibrator)
            if not isinstance(calibrator_artifact, dict):
                raise ValueError("registered calibrator artifact root must be an object")
            if calibrator_artifact.get("model_sha256") != model.get("artifact_sha256"):
                raise ValueError("registered calibrator model SHA-256 mismatch")
            if calibrator_artifact.get("modeling_cohort_sha256") != model.get(
                "modeling_cohort_sha256"
            ):
                raise ValueError("registered calibrator cohort SHA-256 mismatch")
            if calibrator_artifact.get("deployment_eligible") is not False:
                raise ValueError("registered calibrator deployment_eligible must be false")
            if calibrator_artifact.get("holdout_used_for_selection") is not False:
                raise ValueError("registered calibrator used HOLDOUT for selection")
            if calibrator_artifact.get("method") != "platt":
                raise ValueError("registered calibrator method must be platt")
            calibrator = calibrator_artifact.get("calibrator")
            if not hasattr(calibrator, "predict_proba") or [
                int(value)
                for value in np.asarray(getattr(calibrator, "classes_", [])).tolist()
            ] != [0, 1]:
                raise ValueError("registered calibrator class contract is invalid")
            for field in (
                "selection_oof_scores_sha256",
                "oof_fold_assignments_sha256",
                "development_identity_sha256",
                "feature_schema_sha256",
                "software",
            ):
                if calibrator_artifact.get(field) != artifact.get(field):
                    raise ValueError(f"registered calibrator {field} lineage mismatch")
    baseline_pair = artifacts_by_role.get("reference_baseline")
    challenger_pair = artifacts_by_role.get("shadow_incumbent")
    if baseline_pair is None or challenger_pair is None:
        raise ValueError("registry is missing baseline or shadow incumbent")
    baseline_artifact, baseline_model_entry = baseline_pair
    challenger_artifact, challenger_model_entry = challenger_pair
    if baseline_artifact.get("baseline_version") != BASELINE_VERSION:
        raise ValueError("registered baseline version mismatch")
    if challenger_artifact.get("challenger_version") != CHALLENGER_VERSION:
        raise ValueError("registered challenger version mismatch")
    if challenger_artifact.get("holdout_used_for_selection") is not False:
        raise ValueError("registered challenger used HOLDOUT for selection")
    if challenger_artifact.get("baseline_model_sha256") != baseline_model_entry.get(
        "artifact_sha256"
    ):
        raise ValueError("registered challenger baseline SHA-256 mismatch")
    cohort_hashes = {
        str(entry.get("modeling_cohort_sha256")) for _, entry in artifacts_by_role.values()
    }
    if len(cohort_hashes) != 1:
        raise ValueError("registered model cohort SHA-256 values differ")
    if reference_sha256 is not None and cohort_hashes != {str(reference_sha256)}:
        raise ValueError("registered models do not match reference cohort SHA-256")
    nextgen_pair = artifacts_by_role.get("next_challenger")
    if nextgen_pair is not None:
        nextgen_artifact, nextgen_entry = nextgen_pair
        if nextgen_artifact.get("nextgen_version") != NEXTGEN_VERSION:
            raise ValueError("registered NextGen version mismatch")
        if nextgen_artifact.get("holdout_used_for_selection") is not False:
            raise ValueError("registered NextGen used HOLDOUT for selection")
        if nextgen_artifact.get("predictor_source_sha256") != sha256_file(
            Path(__file__).with_name("nextgen_model.py")
        ):
            raise ValueError("registered NextGen predictor source SHA-256 mismatch")
        if nextgen_artifact.get("feature_schema_sha256") != _json_sha256(
            nextgen_artifact.get("feature_schema")
        ):
            raise ValueError("registered NextGen feature schema SHA-256 mismatch")
        thresholds = nextgen_artifact.get("selected_thresholds_by_fpr_cap")
        if not isinstance(thresholds, dict) or not math.isclose(
            float(thresholds.get("0.15", math.nan)),
            float(nextgen_entry["policy_threshold"]),
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError("registered NextGen policy threshold lineage mismatch")
    capacity = pd.read_csv(capacity_path, encoding="utf-8-sig")
    for role, capacity_model in (
        ("reference_baseline", "incumbent_logistic"),
        ("shadow_incumbent", str(challenger_artifact.get("model_name"))),
    ):
        rows = capacity.loc[
            capacity["model"].eq(capacity_model)
            & np.isclose(capacity["false_alarm_cap"], POLICY_FPR_CAP)
        ]
        if len(rows) != 1 or not math.isclose(
            float(rows.iloc[0]["selected_threshold"]),
            float(artifacts_by_role[role][1]["policy_threshold"]),
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError("registered policy threshold does not match capacity")
    return registry


def _load_runtime_registry(path: Path) -> dict[str, object]:
    try:
        return load_and_validate_registry(path)
    except RegistryUnavailableError:
        raise
    except Exception as exc:
        raise RegistryUnavailableError("Shadow Registry validation failed") from exc


def _existing_prediction_coils(output_dir: Path) -> set[str]:
    coils: set[str] = set()
    batch_dir = output_dir / "prediction_batches"
    if not batch_dir.is_dir():
        return coils
    for path in sorted(batch_dir.glob("*.predictions.csv")):
        frame = pd.read_csv(
            path,
            encoding="utf-8-sig",
            dtype={"hr_coil_id": "string"},
            usecols=["hr_coil_id"],
        )
        coils.update(str(value) for value in frame["hr_coil_id"].dropna())
    return coils


def _validated_prediction_ledger(
    output_dir: Path,
    registry_path: Path,
    registry: dict[str, object],
    *,
    evaluation_time: pd.Timestamp,
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    prediction_dir = Path(output_dir) / "prediction_batches"
    metadata_paths = sorted(prediction_dir.glob("*.metadata.json"))
    raw_prediction_paths = sorted(prediction_dir.glob("*.predictions.csv"))
    raw_feature_paths = sorted(prediction_dir.glob("*.features.csv"))
    raw_source_paths = sorted(prediction_dir.glob("*.source.csv"))
    if not metadata_paths:
        if raw_prediction_paths or raw_feature_paths or raw_source_paths:
            raise ValueError("prediction ledger contains orphan files without metadata")
        return [], [], [], []
    registry_sha256 = sha256_file(registry_path)
    models = {str(model["model_id"]): model for model in registry["models"]}
    expected_prediction_paths: list[Path] = []
    expected_feature_paths: list[Path] = []
    expected_source_paths: list[Path] = []
    seen_coils: set[str] = set()
    for metadata_path in metadata_paths:
        metadata = read_json_strict(metadata_path)
        batch_id = str(metadata.get("batch_id", ""))
        if metadata_path.name != f"{batch_id}.metadata.json":
            raise ValueError("prediction metadata filename does not match batch_id")
        prediction_path = prediction_dir / f"{batch_id}.predictions.csv"
        feature_path = prediction_dir / f"{batch_id}.features.csv"
        source_path = prediction_dir / f"{batch_id}.source.csv"
        if Path(str(metadata.get("metadata_path", ""))).resolve() != metadata_path.resolve():
            raise ValueError("prediction metadata path escapes its committed batch")
        if Path(str(metadata.get("prediction_path", ""))).resolve() != prediction_path.resolve():
            raise ValueError("prediction metadata path escapes its committed batch")
        if Path(str(metadata.get("feature_snapshot_path", ""))).resolve() != feature_path.resolve():
            raise ValueError("feature metadata path escapes its committed batch")
        if Path(str(metadata.get("source_snapshot_path", ""))).resolve() != source_path.resolve():
            raise ValueError("source snapshot path escapes its committed batch")
        if Path(str(metadata.get("registry_path", ""))).resolve() != Path(
            registry_path
        ).resolve():
            raise ValueError("prediction metadata Registry path mismatch")
        evidence_status = str(metadata.get("evidence_status", ""))
        if evidence_status not in ALLOWED_EVIDENCE_STATUSES:
            raise ValueError("unsupported evidence_status")
        if metadata.get("registry_sha256") != registry_sha256:
            raise ValueError("prediction registry SHA-256 mismatch")
        _validate_existing_prediction_batch(
            metadata_path,
            prediction_path,
            batch_id=batch_id,
            source_sha256=str(metadata.get("source_sha256")),
            registry_sha256=registry_sha256,
        )
        predictions = pd.read_csv(
            prediction_path,
            encoding="utf-8-sig",
            dtype={"hr_coil_id": "string"},
        )
        features = pd.read_csv(
            feature_path,
            encoding="utf-8-sig",
            dtype={"hr_coil_id": "string"},
        )
        if len(features) != int(metadata.get("coil_rows", -1)):
            raise ValueError("feature snapshot row count does not match metadata")
        if len(predictions) != int(metadata.get("prediction_rows", -1)):
            raise ValueError("prediction row count does not match metadata")
        if int(metadata.get("model_count", -1)) != len(models):
            raise ValueError("prediction model count does not match registry")
        missing_feature_times = sorted(
            {"hr_date", "feature_available_at"} - set(features.columns)
        )
        if missing_feature_times:
            raise ValueError(
                "feature snapshot is missing temporal columns: "
                + ", ".join(missing_feature_times)
            )
        if features["hr_coil_id"].duplicated().any():
            raise ValueError("feature snapshot contains duplicate hr_coil_id")
        feature_coils = set(features["hr_coil_id"].astype(str))
        overlap = sorted(feature_coils & seen_coils)
        if overlap:
            raise ValueError(f"prediction ledger repeats hr_coil_id: {overlap[0]}")
        seen_coils.update(feature_coils)
        required_prediction_columns = {
            "batch_id",
            "hr_coil_id",
            "model_id",
            "model_role",
            "risk_score",
            "calibrated_probability",
            "policy_threshold",
            "predicted_label",
            "prediction_generated_at",
            "evidence_status",
        }
        missing = sorted(required_prediction_columns - set(predictions.columns))
        if missing:
            raise ValueError(f"prediction ledger is missing columns: {', '.join(missing)}")
        if predictions.duplicated(["hr_coil_id", "model_id"]).any():
            raise ValueError("prediction ledger contains duplicate coil-model rows")
        if set(predictions["hr_coil_id"].astype(str)) != feature_coils:
            raise ValueError("prediction and feature snapshot coil sets differ")
        if set(predictions["model_id"].astype(str)) != set(models):
            raise ValueError("prediction model IDs do not match registry")
        if not predictions["batch_id"].astype(str).eq(batch_id).all():
            raise ValueError("prediction batch_id does not match metadata")
        if not predictions["evidence_status"].astype(str).eq(
            str(metadata.get("evidence_status"))
        ).all():
            raise ValueError("prediction evidence_status does not match metadata")
        prediction_times = pd.to_datetime(
            predictions["prediction_generated_at"], utc=True, errors="raise"
        )
        batch_prediction_time = pd.Timestamp(metadata["prediction_generated_at"])
        if batch_prediction_time.tzinfo is None:
            raise ValueError("prediction timestamp must be timezone-aware")
        batch_prediction_time = batch_prediction_time.tz_convert("UTC")
        if not prediction_times.eq(batch_prediction_time).all():
            raise ValueError("prediction timestamp does not match metadata")
        if prediction_times.gt(evaluation_time).any():
            raise ValueError("prediction timestamp is after evaluation time")
        available_at = pd.to_datetime(
            features["feature_available_at"], utc=True, errors="raise"
        )
        if available_at.gt(batch_prediction_time).any():
            raise ValueError(
                "prediction_generated_at must not precede feature_available_at"
            )
        if evidence_status == INDEPENDENT_EVIDENCE_STATUS:
            shadow_start_at = pd.Timestamp(registry["shadow_start_at"]).tz_convert("UTC")
            hr_dates = pd.to_datetime(
                features["hr_date"], format="%Y-%m-%d", utc=True, errors="raise"
            )
            if (
                batch_prediction_time < shadow_start_at
                or available_at.lt(shadow_start_at).any()
                or hr_dates.lt(shadow_start_at.normalize()).any()
            ):
                raise ValueError(
                    "independent Shadow temporal provenance must not precede shadow_start_at"
                )
        _validate_output_mode(output_dir, evidence_status)
        for model_id, model in models.items():
            subset = predictions.loc[predictions["model_id"].astype(str).eq(model_id)]
            if len(subset) != len(features):
                raise ValueError("prediction model does not cover every feature row")
            if not subset["model_role"].astype(str).eq(str(model["role"])).all():
                raise ValueError("prediction model role does not match registry")
            threshold = pd.to_numeric(subset["policy_threshold"], errors="coerce")
            if not np.isfinite(threshold).all() or not np.allclose(
                threshold, float(model["policy_threshold"]), rtol=0.0, atol=1e-15
            ):
                raise ValueError("prediction policy threshold does not match registry")
            risk = pd.to_numeric(subset["risk_score"], errors="coerce")
            if not np.isfinite(risk).all() or not risk.between(0.0, 1.0).all():
                raise ValueError("prediction risk_score must be finite in [0, 1]")
            expected_label = np.where(risk.ge(threshold), "불량", "양품")
            if not np.array_equal(subset["predicted_label"].astype(str), expected_label):
                raise ValueError("prediction label does not match score and threshold")
            calibrated = pd.to_numeric(
                subset["calibrated_probability"], errors="coerce"
            )
            if model.get("calibrator_path") is not None:
                if calibrated.isna().any() or not np.isfinite(calibrated).all():
                    raise ValueError("registered calibrator produced missing probabilities")
                if not calibrated.between(0.0, 1.0).all():
                    raise ValueError("calibrated probabilities must be in [0, 1]")
            elif calibrated.notna().any():
                raise ValueError("unregistered calibrator probabilities are present")
        expected_prediction_paths.append(prediction_path)
        expected_feature_paths.append(feature_path)
        expected_source_paths.append(source_path)
    if {path.resolve() for path in raw_prediction_paths} != {
        path.resolve() for path in expected_prediction_paths
    }:
        raise ValueError("prediction ledger contains orphan or missing prediction files")
    if {path.resolve() for path in raw_feature_paths} != {
        path.resolve() for path in expected_feature_paths
    }:
        raise ValueError("prediction ledger contains orphan or missing feature files")
    if {path.resolve() for path in raw_source_paths} != {
        path.resolve() for path in expected_source_paths
    }:
        raise ValueError("prediction ledger contains orphan or missing source snapshots")
    return (
        expected_prediction_paths,
        expected_feature_paths,
        expected_source_paths,
        metadata_paths,
    )


def _checked_prediction_ledger(
    output_dir: Path,
    registry_path: Path,
    registry: dict[str, object],
    *,
    evaluation_time: pd.Timestamp,
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    try:
        return _validated_prediction_ledger(
            output_dir,
            registry_path,
            registry,
            evaluation_time=evaluation_time,
        )
    except LedgerIntegrityError:
        raise
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, OverflowError) as exc:
        raise LedgerIntegrityError(f"prediction ledger integrity failure: {exc}") from exc


def _checked_label_batches(output_dir: Path) -> list[tuple[dict[str, object], Path]]:
    try:
        return _validated_label_batches(output_dir)
    except LedgerIntegrityError:
        raise
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, OverflowError) as exc:
        raise LedgerIntegrityError(f"label ledger integrity failure: {exc}") from exc


def _validate_committed_label_provenance(
    prediction_files: list[Path],
    label_batches: list[tuple[dict[str, object], Path]],
) -> None:
    if not label_batches:
        return
    if not prediction_files:
        raise LedgerIntegrityError("committed labels have no prediction evidence")
    predictions = pd.concat(
        [
            pd.read_csv(
                path,
                encoding="utf-8-sig",
                dtype={"hr_coil_id": "string"},
                usecols=["hr_coil_id", "prediction_generated_at"],
            )
            for path in prediction_files
        ],
        ignore_index=True,
    )
    predictions["prediction_generated_at"] = pd.to_datetime(
        predictions["prediction_generated_at"], utc=True, errors="raise"
    )
    counts = predictions.groupby("hr_coil_id", observed=True)[
        "prediction_generated_at"
    ].nunique()
    if not counts.eq(1).all():
        raise LedgerIntegrityError(
            "one Coil has inconsistent prediction timestamps across registered models"
        )
    prediction_time = predictions.groupby("hr_coil_id", observed=True)[
        "prediction_generated_at"
    ].first()
    predicted_coils = set(prediction_time.index.astype(str))
    for metadata, path in label_batches:
        labels = pd.read_csv(
            path, encoding="utf-8-sig", dtype={"hr_coil_id": "string"}
        )
        label_coils = set(labels["hr_coil_id"].astype(str))
        unknown = sorted(label_coils - predicted_coils)
        if unknown:
            raise LedgerIntegrityError(
                f"committed label has no prediction evidence: {unknown[0]}"
            )
        finalized = pd.to_datetime(
            labels["label_finalized_at"], utc=True, errors="raise"
        )
        ingested_at = pd.Timestamp(metadata["ingested_at"]).tz_convert("UTC")
        if finalized.gt(ingested_at).any():
            raise LedgerIntegrityError(
                "committed label was finalized after its ingestion timestamp"
            )
        for coil, label_time in zip(labels["hr_coil_id"].astype(str), finalized):
            if label_time <= prediction_time.loc[coil]:
                raise LedgerIntegrityError(
                    "committed label was finalized before its prediction timestamp"
                )


def _checked_committed_label_provenance(
    prediction_files: list[Path],
    label_batches: list[tuple[dict[str, object], Path]],
) -> None:
    try:
        _validate_committed_label_provenance(prediction_files, label_batches)
    except LedgerIntegrityError:
        raise
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, OverflowError) as exc:
        raise LedgerIntegrityError(
            f"label provenance integrity failure: {exc}"
        ) from exc


def _validate_existing_prediction_batch(
    metadata_path: Path,
    prediction_path: Path,
    *,
    batch_id: str,
    source_sha256: str,
    registry_sha256: str,
) -> dict[str, object]:
    if not metadata_path.is_file() or not prediction_path.is_file():
        raise RuntimeError("prediction batch is partially written and requires manual review")
    metadata = read_json_strict(metadata_path)
    expected = {
        "batch_id": batch_id,
        "source_sha256": source_sha256,
        "registry_sha256": registry_sha256,
    }
    for field, value in expected.items():
        if metadata.get(field) != value:
            raise ValueError(f"existing prediction batch {field} mismatch")
    try:
        expected_batch_id = make_batch_id(
            source_sha256,
            pd.Timestamp(metadata.get("prediction_generated_at")),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("prediction batch_id provenance is invalid") from exc
    if batch_id != expected_batch_id:
        raise ValueError("prediction batch_id does not match source and timestamp")
    if metadata.get("prediction_sha256") != sha256_file(prediction_path):
        raise ValueError("existing prediction batch SHA-256 mismatch")
    source_snapshot_path = Path(str(metadata.get("source_snapshot_path", "")))
    expected_source_snapshot_path = metadata_path.parent / f"{batch_id}.source.csv"
    if source_snapshot_path.resolve() != expected_source_snapshot_path.resolve():
        raise ValueError("existing prediction source snapshot path mismatch")
    if not source_snapshot_path.is_file():
        raise ValueError("existing prediction source snapshot is missing")
    if metadata.get("source_sha256") != sha256_file(source_snapshot_path):
        raise ValueError("existing prediction source snapshot SHA-256 mismatch")
    feature_snapshot_path = Path(str(metadata.get("feature_snapshot_path", "")))
    if not feature_snapshot_path.is_file():
        raise ValueError("existing prediction feature snapshot is missing")
    if metadata.get("feature_snapshot_sha256") != sha256_file(feature_snapshot_path):
        raise ValueError("existing prediction feature snapshot SHA-256 mismatch")
    return metadata


def score_feature_batch(
    feature_csv: Path,
    registry_path: Path,
    output_dir: Path,
    *,
    now: Callable[[], pd.Timestamp] = _utc_now,
) -> dict[str, object]:
    with _output_lock(Path(output_dir)):
        return _score_feature_batch_locked(
            feature_csv,
            registry_path,
            output_dir,
            now=now,
            evidence_status=INDEPENDENT_EVIDENCE_STATUS,
        )


def _score_feature_batch_locked(
    feature_csv: Path,
    registry_path: Path,
    output_dir: Path,
    *,
    now: Callable[[], pd.Timestamp] = _utc_now,
    evidence_status: str = INDEPENDENT_EVIDENCE_STATUS,
) -> dict[str, object]:
    feature_csv = Path(feature_csv)
    registry_path = Path(registry_path)
    output_dir = Path(output_dir)
    registry = _load_runtime_registry(registry_path)
    generated_at = now()
    if generated_at.tzinfo is None:
        raise FeatureBatchError("prediction_generated_at must be timezone-aware")
    generated_at = generated_at.tz_convert("UTC")
    mode_path = _output_mode_path(output_dir)
    has_committed_files = any(
        path.is_file()
        for directory in ("prediction_batches", "label_batches")
        for path in (output_dir / directory).glob("*")
    )
    if mode_path.is_file():
        try:
            _validate_output_mode(output_dir, evidence_status)
        except ValueError as exc:
            raise LedgerIntegrityError(f"output mode integrity failure: {exc}") from exc
    elif has_committed_files:
        raise LedgerIntegrityError("output mode manifest is missing for committed evidence")
    else:
        _bind_output_mode(output_dir, evidence_status)
    (
        validated_prediction_files,
        validated_feature_files,
        _,
        validated_metadata_files,
    ) = _checked_prediction_ledger(
        output_dir,
        registry_path,
        registry,
        evaluation_time=generated_at,
    )
    validated_label_batches = _checked_label_batches(output_dir)
    _checked_committed_label_provenance(
        validated_prediction_files, validated_label_batches
    )
    source_sha256 = sha256_file(feature_csv)
    registry_sha256 = sha256_file(registry_path)
    prediction_dir = output_dir / "prediction_batches"
    committed_matches: list[dict[str, object]] = []
    for committed_metadata_path in validated_metadata_files:
        committed = read_json_strict(committed_metadata_path)
        if (
            committed.get("source_sha256") == source_sha256
            and committed.get("registry_sha256") == registry_sha256
        ):
            committed_matches.append(committed)
    if len(committed_matches) > 1:
        raise ValueError("multiple committed prediction batches share one source hash")
    if committed_matches:
        committed = committed_matches[0]
        committed_batch_id = str(committed.get("batch_id"))
        return _validate_existing_prediction_batch(
            prediction_dir / f"{committed_batch_id}.metadata.json",
            prediction_dir / f"{committed_batch_id}.predictions.csv",
            batch_id=committed_batch_id,
            source_sha256=source_sha256,
            registry_sha256=registry_sha256,
        )
    batch_id = make_batch_id(source_sha256, generated_at)
    prediction_path = prediction_dir / f"{batch_id}.predictions.csv"
    feature_snapshot_path = prediction_dir / f"{batch_id}.features.csv"
    source_snapshot_path = prediction_dir / f"{batch_id}.source.csv"
    metadata_path = prediction_dir / f"{batch_id}.metadata.json"
    if (
        prediction_path.exists()
        or feature_snapshot_path.exists()
        or source_snapshot_path.exists()
        or metadata_path.exists()
    ):
        return _validate_existing_prediction_batch(
            metadata_path,
            prediction_path,
            batch_id=batch_id,
            source_sha256=source_sha256,
            registry_sha256=registry_sha256,
        )

    raw = pd.read_csv(
        feature_csv,
        encoding="utf-8-sig",
        dtype={"charge_id": "string", "slab_no": "string", "hr_coil_id": "string"},
    )
    try:
        prepared = validate_feature_batch(raw, registry, set())
    except ValueError as exc:
        raise FeatureBatchError(str(exc)) from exc
    existing_coils: set[str] = set()
    for path in validated_feature_files:
        existing = pd.read_csv(
            path,
            encoding="utf-8-sig",
            dtype={"hr_coil_id": "string"},
            usecols=["hr_coil_id"],
        )
        existing_coils.update(str(value) for value in existing["hr_coil_id"])
    reused = sorted(set(prepared["hr_coil_id"].astype(str)) & existing_coils)
    if reused:
        raise CoilAlreadyScoredError(f"hr_coil_id was already scored: {reused[0]}")
    available_at = pd.to_datetime(
        prepared["feature_available_at"], utc=True, errors="raise"
    )
    if available_at.gt(generated_at).any():
        raise FeatureBatchError(
            "prediction_generated_at must not precede feature_available_at"
        )
    if evidence_status == INDEPENDENT_EVIDENCE_STATUS:
        shadow_start_at = pd.Timestamp(registry["shadow_start_at"]).tz_convert("UTC")
        hr_dates = pd.to_datetime(
            prepared["hr_date"], format="%Y-%m-%d", utc=True, errors="raise"
        )
        if (
            generated_at < shadow_start_at
            or available_at.lt(shadow_start_at).any()
            or hr_dates.lt(shadow_start_at.normalize()).any()
        ):
            raise FeatureBatchError(
                "independent Shadow hr_date and feature_available_at must not precede shadow_start_at"
            )

    rows: list[dict[str, object]] = []
    for model in registry["models"]:
        artifact = joblib.load(Path(model["artifact_path"]))
        predictor = _predictor_from_artifact(artifact)
        schema = model["feature_schema"]
        numeric = list(schema["numeric"])
        categorical = list(schema["categorical"])
        features = baseline._prepare_features(prepared, numeric, categorical)
        raw_probabilities = np.asarray(predictor.predict_proba(features), dtype=float)[:, 1]
        if not np.isfinite(raw_probabilities).all():
            raise ValueError(f"model {model['model_id']} produced non-finite risk scores")
        calibrated: np.ndarray | None = None
        if model["calibrator_path"] is not None:
            calibrator_artifact = joblib.load(Path(model["calibrator_path"]))
            if not isinstance(calibrator_artifact, dict):
                raise ValueError("calibrator artifact root must be an object")
            calibrated = apply_calibrator(
                str(calibrator_artifact.get("method")),
                calibrator_artifact.get("calibrator"),
                raw_probabilities,
            )
        threshold = float(model["policy_threshold"])
        for index, source in prepared.iterrows():
            risk_score = float(raw_probabilities[index])
            rows.append(
                {
                    "batch_id": batch_id,
                    "charge_id": source["charge_id"],
                    "slab_no": source["slab_no"],
                    "hr_coil_id": source["hr_coil_id"],
                    "hr_date": source["hr_date"],
                    "feature_available_at": source["feature_available_at"],
                    "prediction_generated_at": generated_at.isoformat(),
                    "model_id": model["model_id"],
                    "model_role": model["role"],
                    "risk_score": risk_score,
                    "calibrated_probability": (
                        np.nan if calibrated is None else float(calibrated[index])
                    ),
                    "policy_threshold": threshold,
                    "predicted_label": "불량" if risk_score >= threshold else "양품",
                    "evidence_status": evidence_status,
                }
            )
    predictions = pd.DataFrame(rows)
    atomic_copy_file(feature_csv, source_snapshot_path)
    atomic_write_csv(prepared, feature_snapshot_path)
    atomic_write_csv(predictions, prediction_path)
    metadata: dict[str, object] = {
        "batch_id": batch_id,
        "evidence_status": evidence_status,
        "source_path": str(feature_csv.resolve()),
        "source_sha256": source_sha256,
        "source_snapshot_path": str(source_snapshot_path.resolve()),
        "registry_path": str(registry_path.resolve()),
        "registry_sha256": registry_sha256,
        "prediction_generated_at": generated_at.isoformat(),
        "coil_rows": int(len(prepared)),
        "model_count": int(len(registry["models"])),
        "prediction_rows": int(len(predictions)),
        "prediction_path": str(prediction_path.resolve()),
        "prediction_sha256": sha256_file(prediction_path),
        "feature_snapshot_path": str(feature_snapshot_path.resolve()),
        "feature_snapshot_sha256": sha256_file(feature_snapshot_path),
        "metadata_path": str(metadata_path.resolve()),
    }
    atomic_write_json(metadata, metadata_path)
    return metadata


def _validated_label_batches(output_dir: Path) -> list[tuple[dict[str, object], Path]]:
    label_dir = output_dir / "label_batches"
    if not label_dir.is_dir():
        return []
    metadata_paths = sorted(label_dir.glob("*.metadata.json"))
    raw_label_paths = sorted(label_dir.glob("*.labels.csv"))
    raw_source_paths = sorted(label_dir.glob("*.source.csv"))
    metadata_items: list[tuple[dict[str, object], Path]] = []
    expected_label_paths: list[Path] = []
    expected_source_paths: list[Path] = []
    for metadata_path in metadata_paths:
        item = read_json_strict(metadata_path)
        batch_id = str(item.get("batch_id", ""))
        expected_path = label_dir / f"{batch_id}.labels.csv"
        expected_source_path = label_dir / f"{batch_id}.source.csv"
        if metadata_path.name != f"{batch_id}.metadata.json":
            raise ValueError("label metadata filename does not match batch_id")
        if Path(str(item.get("metadata_path", ""))).resolve() != metadata_path.resolve():
            raise ValueError("label metadata path escapes its committed batch")
        if Path(str(item.get("labels_path", ""))).resolve() != expected_path.resolve():
            raise ValueError("label metadata path escapes its committed batch")
        if Path(str(item.get("source_snapshot_path", ""))).resolve() != expected_source_path.resolve():
            raise ValueError("label source snapshot path escapes its committed batch")
        if not expected_source_path.is_file():
            raise ValueError("label source snapshot is missing")
        if item.get("source_sha256") != sha256_file(expected_source_path):
            raise ValueError("label source snapshot SHA-256 mismatch")
        if not expected_path.is_file():
            raise ValueError("registered label batch file is missing")
        if item.get("labels_sha256") != sha256_file(expected_path):
            raise ValueError("registered label batch SHA-256 mismatch")
        ingested_at = pd.Timestamp(item.get("ingested_at"))
        if ingested_at.tzinfo is None:
            raise ValueError("label ingestion timestamp must be timezone-aware")
        try:
            expected_batch_id = make_batch_id(
                str(item.get("source_sha256")),
                ingested_at,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("label batch_id provenance is invalid") from exc
        if batch_id != expected_batch_id:
            raise ValueError("label batch_id does not match source and timestamp")
        labels = pd.read_csv(
            expected_path,
            encoding="utf-8-sig",
            dtype={"hr_coil_id": "string"},
        )
        if set(labels.columns) != set(LABEL_COLUMNS):
            raise ValueError("registered label batch columns do not match contract")
        normalized = validate_label_batch(labels)
        if len(normalized) != int(item.get("label_rows", -1)):
            raise ValueError("label batch row count does not match metadata")
        if int(normalized["judge"].eq("불량").sum()) != int(
            item.get("positive_labels", -1)
        ):
            raise ValueError("label positive count does not match metadata")
        metadata_items.append((item, expected_path))
        expected_label_paths.append(expected_path)
        expected_source_paths.append(expected_source_path)
    if {path.resolve() for path in raw_label_paths} != {
        path.resolve() for path in expected_label_paths
    }:
        raise ValueError("label ledger contains orphan or missing label files")
    if {path.resolve() for path in raw_source_paths} != {
        path.resolve() for path in expected_source_paths
    }:
        raise ValueError("label ledger contains orphan or missing source snapshots")
    items_by_id = {str(item["batch_id"]): (item, path) for item, path in metadata_items}
    superseded_by: dict[str, str] = {}
    for item, path in metadata_items:
        replacement_id = str(item["batch_id"])
        superseded_value = item.get("supersedes_label_batch_id")
        if superseded_value is None:
            continue
        superseded_id = str(superseded_value)
        if superseded_id == replacement_id or superseded_id not in items_by_id:
            raise ValueError("label supersession graph references an invalid batch")
        if superseded_id in superseded_by:
            raise ValueError("one label batch cannot have multiple replacements")
        original, original_path = items_by_id[superseded_id]
        replacement_time = pd.Timestamp(item["ingested_at"]).tz_convert("UTC")
        original_time = pd.Timestamp(original["ingested_at"]).tz_convert("UTC")
        if replacement_time <= original_time:
            raise ValueError("label replacement must be ingested after its original")
        replacement_labels = pd.read_csv(
            path, encoding="utf-8-sig", dtype={"hr_coil_id": "string"}
        )
        original_labels = pd.read_csv(
            original_path, encoding="utf-8-sig", dtype={"hr_coil_id": "string"}
        )
        if set(replacement_labels["hr_coil_id"].astype(str)) != set(
            original_labels["hr_coil_id"].astype(str)
        ):
            raise ValueError("a label correction must replace every coil")
        superseded_by[superseded_id] = replacement_id
    for batch_id in items_by_id:
        visited: set[str] = set()
        current = batch_id
        while current in superseded_by:
            if current in visited:
                raise ValueError("label supersession graph contains a cycle")
            visited.add(current)
            current = superseded_by[current]
    return metadata_items


def _select_effective_label_batches(
    metadata_items: list[tuple[dict[str, object], Path]],
) -> list[tuple[dict[str, object], Path]]:
    superseded = {
        str(item["supersedes_label_batch_id"])
        for item, _ in metadata_items
        if item.get("supersedes_label_batch_id") is not None
    }
    effective: list[tuple[dict[str, object], Path]] = []
    for item, labels_path in metadata_items:
        if item.get("batch_id") in superseded:
            continue
        effective.append((item, labels_path))
    return effective


def _effective_label_batches(output_dir: Path) -> list[tuple[dict[str, object], Path]]:
    return _select_effective_label_batches(_validated_label_batches(output_dir))


def ingest_label_batch(
    label_csv: Path,
    output_dir: Path,
    supersedes_label_batch_id: str | None = None,
    *,
    registry_path: Path | None = None,
    now: Callable[[], pd.Timestamp] = _utc_now,
) -> dict[str, object]:
    with _output_lock(Path(output_dir)):
        return _ingest_label_batch_locked(
            label_csv,
            output_dir,
            supersedes_label_batch_id=supersedes_label_batch_id,
            registry_path=registry_path,
            now=now,
        )


def _ingest_label_batch_locked(
    label_csv: Path,
    output_dir: Path,
    supersedes_label_batch_id: str | None = None,
    *,
    registry_path: Path | None = None,
    now: Callable[[], pd.Timestamp] = _utc_now,
) -> dict[str, object]:
    label_csv = Path(label_csv)
    output_dir = Path(output_dir)
    generated_at = now()
    if generated_at.tzinfo is None:
        raise LabelBatchError("label ingestion timestamp must be timezone-aware")
    generated_at = generated_at.tz_convert("UTC")
    try:
        raw = pd.read_csv(
            label_csv, encoding="utf-8-sig", dtype={"hr_coil_id": "string"}
        )
        labels = validate_label_batch(raw)
    except (OSError, ValueError) as exc:
        raise LabelBatchError(str(exc)) from exc
    finalized_at = pd.to_datetime(labels["label_finalized_at"], utc=True, errors="raise")
    if finalized_at.gt(generated_at).any():
        raise LabelBatchError("label_finalized_at must not be after ingestion time")
    if registry_path is None:
        registry_paths: set[Path] = set()
        for metadata_path in sorted(
            (output_dir / "prediction_batches").glob("*.metadata.json")
        ):
            metadata = read_json_strict(metadata_path)
            stored_path = Path(str(metadata.get("registry_path", "")))
            if not stored_path.is_file():
                raise LedgerIntegrityError("prediction Registry path is missing")
            registry_paths.add(stored_path.resolve())
        if len(registry_paths) != 1:
            raise LabelBatchError(
                "label batch requires predictions from exactly one registered Shadow"
            )
        resolved_registry_path = next(iter(registry_paths))
    else:
        resolved_registry_path = Path(registry_path).resolve()
    registry = _load_runtime_registry(resolved_registry_path)
    prediction_files, _, _, _ = _checked_prediction_ledger(
        output_dir,
        resolved_registry_path,
        registry,
        evaluation_time=generated_at,
    )
    committed_label_batches = _checked_label_batches(output_dir)
    _checked_committed_label_provenance(
        prediction_files, committed_label_batches
    )
    if not prediction_files:
        raise LabelBatchError("label batch contains Coils without committed predictions")
    predictions = pd.concat(
        [
            pd.read_csv(
                path,
                encoding="utf-8-sig",
                dtype={"hr_coil_id": "string"},
                usecols=["hr_coil_id", "prediction_generated_at"],
            )
            for path in prediction_files
        ],
        ignore_index=True,
    )
    requested_coils = set(labels["hr_coil_id"].astype(str))
    predicted_coils = set(predictions["hr_coil_id"].astype(str))
    unknown_coils = sorted(requested_coils - predicted_coils)
    if unknown_coils:
        raise LabelBatchError(
            f"label hr_coil_id has no committed prediction: {unknown_coils[0]}"
        )
    prediction_times = predictions.loc[
        predictions["hr_coil_id"].astype(str).isin(requested_coils)
    ].copy()
    prediction_times["prediction_generated_at"] = pd.to_datetime(
        prediction_times["prediction_generated_at"], utc=True, errors="raise"
    )
    unique_times = prediction_times.groupby("hr_coil_id", observed=True)[
        "prediction_generated_at"
    ].nunique()
    if not unique_times.eq(1).all():
        raise LedgerIntegrityError(
            "one Coil has inconsistent prediction timestamps across registered models"
        )
    coil_prediction_time = prediction_times.groupby("hr_coil_id", observed=True)[
        "prediction_generated_at"
    ].first()
    label_time_by_coil = labels.set_index("hr_coil_id")["label_finalized_at"].map(
        lambda value: pd.Timestamp(value).tz_convert("UTC")
    )
    if any(
        label_time_by_coil.loc[coil] <= coil_prediction_time.loc[coil]
        for coil in requested_coils
    ):
        raise LabelBatchError("label_finalized_at must be after prediction_generated_at")
    source_sha256 = sha256_file(label_csv)
    label_dir = output_dir / "label_batches"
    committed_matches = []
    for committed_metadata_path in sorted(label_dir.glob("*.metadata.json")):
        committed = read_json_strict(committed_metadata_path)
        if committed.get("source_sha256") == source_sha256:
            committed_matches.append(committed)
    if len(committed_matches) > 1:
        raise LedgerIntegrityError("multiple committed label batches share one source hash")
    if committed_matches:
        committed = committed_matches[0]
        if committed.get("supersedes_label_batch_id") != supersedes_label_batch_id:
            raise LabelBatchError("label retry supersedes contract mismatch")
        committed_path = Path(str(committed.get("labels_path", "")))
        committed_source_path = Path(str(committed.get("source_snapshot_path", "")))
        if not committed_path.is_file() or committed.get("labels_sha256") != sha256_file(
            committed_path
        ):
            raise LedgerIntegrityError("existing label batch SHA-256 mismatch")
        if (
            not committed_source_path.is_file()
            or committed.get("source_sha256") != sha256_file(committed_source_path)
        ):
            raise LedgerIntegrityError("existing label source snapshot SHA-256 mismatch")
        return committed
    batch_id = make_batch_id(source_sha256, generated_at)
    labels_path = label_dir / f"{batch_id}.labels.csv"
    source_snapshot_path = label_dir / f"{batch_id}.source.csv"
    metadata_path = label_dir / f"{batch_id}.metadata.json"
    if labels_path.exists() or source_snapshot_path.exists() or metadata_path.exists():
        if (
            not labels_path.is_file()
            or not source_snapshot_path.is_file()
            or not metadata_path.is_file()
        ):
            raise LedgerIntegrityError(
                "label batch is partially written and requires manual review"
            )
        metadata = read_json_strict(metadata_path)
        if metadata.get("source_sha256") != source_sha256:
            raise LedgerIntegrityError("existing label batch source SHA-256 mismatch")
        if metadata.get("labels_sha256") != sha256_file(labels_path):
            raise LedgerIntegrityError("existing label batch SHA-256 mismatch")
        if metadata.get("source_sha256") != sha256_file(source_snapshot_path):
            raise LedgerIntegrityError("existing label source snapshot SHA-256 mismatch")
        return metadata
    effective_batches = _select_effective_label_batches(committed_label_batches)
    existing_coils: set[str] = set()
    existing_batch_ids = {str(item["batch_id"]) for item, _ in effective_batches}
    for item, path in effective_batches:
        if supersedes_label_batch_id is not None and item.get("batch_id") == supersedes_label_batch_id:
            continue
        existing = pd.read_csv(path, encoding="utf-8-sig", dtype={"hr_coil_id": "string"})
        existing_coils.update(str(value) for value in existing["hr_coil_id"])
    if supersedes_label_batch_id is not None and supersedes_label_batch_id not in existing_batch_ids:
        raise LabelBatchError(
            "supersedes_label_batch_id does not name an effective label batch"
        )
    if supersedes_label_batch_id is not None:
        superseded_path = next(
            path
            for item, path in effective_batches
            if item.get("batch_id") == supersedes_label_batch_id
        )
        superseded = pd.read_csv(
            superseded_path,
            encoding="utf-8-sig",
            dtype={"hr_coil_id": "string"},
        )
        if set(labels["hr_coil_id"].astype(str)) != set(
            superseded["hr_coil_id"].astype(str)
        ):
            raise LabelBatchError(
                "a label correction must replace every coil in the superseded batch"
            )
    overlap = sorted(set(labels["hr_coil_id"].astype(str)) & existing_coils)
    if overlap:
        raise LabelBatchError(
            f"label batch duplicates an effective hr_coil_id: {overlap[0]}"
        )
    atomic_copy_file(label_csv, source_snapshot_path)
    atomic_write_csv(labels, labels_path)
    metadata: dict[str, object] = {
        "batch_id": batch_id,
        "source_path": str(label_csv.resolve()),
        "source_sha256": source_sha256,
        "source_snapshot_path": str(source_snapshot_path.resolve()),
        "ingested_at": generated_at.isoformat(),
        "label_rows": int(len(labels)),
        "positive_labels": int(labels["judge"].eq("불량").sum()),
        "supersedes_label_batch_id": supersedes_label_batch_id,
        "labels_path": str(labels_path.resolve()),
        "labels_sha256": sha256_file(labels_path),
        "metadata_path": str(metadata_path.resolve()),
    }
    atomic_write_json(metadata, metadata_path)
    return metadata


def settle_predictions(
    predictions: pd.DataFrame,
    labels: pd.DataFrame,
) -> pd.DataFrame:
    required_predictions = {
        "charge_id",
        "hr_coil_id",
        "prediction_generated_at",
        "model_role",
        "risk_score",
        "policy_threshold",
    }
    missing_predictions = sorted(required_predictions - set(predictions.columns))
    if missing_predictions:
        raise ValueError(
            f"predictions are missing columns: {', '.join(missing_predictions)}"
        )
    normalized_labels = validate_label_batch(labels)
    joined = predictions.merge(
        normalized_labels,
        on="hr_coil_id",
        how="inner",
        validate="many_to_one",
    )
    prediction_time = pd.to_datetime(
        joined["prediction_generated_at"], utc=True, errors="raise"
    )
    label_time = pd.to_datetime(joined["label_finalized_at"], utc=True, errors="raise")
    if label_time.le(prediction_time).any():
        raise ValueError("label_finalized_at must be after prediction_generated_at")
    joined["y_true"] = joined["judge"].eq("불량").astype(int)
    joined["predicted_positive"] = joined["risk_score"].ge(
        joined["policy_threshold"]
    )
    return joined


def _paired_metric_delta(
    frame: pd.DataFrame,
    *,
    candidate_role: str,
    incumbent_role: str,
) -> tuple[float, float, float]:
    candidate = frame.loc[frame["model_role"].eq(candidate_role)]
    incumbent = frame.loc[frame["model_role"].eq(incumbent_role)]
    if set(candidate["hr_coil_id"]) != set(incumbent["hr_coil_id"]):
        raise ValueError("paired models must score the same hr_coil_id set")
    candidate = candidate.sort_values("hr_coil_id", kind="stable")
    incumbent = incumbent.sort_values("hr_coil_id", kind="stable")
    truth = candidate["y_true"].to_numpy(dtype=int)
    if not np.array_equal(truth, incumbent["y_true"].to_numpy(dtype=int)):
        raise ValueError("paired model labels do not match")
    if set(np.unique(truth)) != {0, 1}:
        raise ValueError("paired metrics require both labels")
    candidate_predictions = candidate["risk_score"].to_numpy(float) >= candidate[
        "policy_threshold"
    ].to_numpy(float)
    incumbent_predictions = incumbent["risk_score"].to_numpy(float) >= incumbent[
        "policy_threshold"
    ].to_numpy(float)
    candidate_recall = recall_score(truth, candidate_predictions, zero_division=0)
    incumbent_recall = recall_score(truth, incumbent_predictions, zero_division=0)
    candidate_pr_auc = average_precision_score(truth, candidate["risk_score"])
    incumbent_pr_auc = average_precision_score(truth, incumbent["risk_score"])
    negative = truth == 0
    candidate_fpr = float(candidate_predictions[negative].mean())
    incumbent_fpr = float(incumbent_predictions[negative].mean())
    return (
        float(candidate_recall - incumbent_recall),
        float(candidate_pr_auc - incumbent_pr_auc),
        float(candidate_fpr - incumbent_fpr),
    )


def paired_group_bootstrap(
    joined: pd.DataFrame,
    *,
    candidate_role: str,
    incumbent_role: str,
    iterations: int = 2000,
    random_state: int = 42,
) -> dict[str, object]:
    if iterations < 1:
        raise ValueError("bootstrap iterations must be positive")
    relevant = joined.loc[
        joined["model_role"].isin([candidate_role, incumbent_role])
    ].copy()
    point_recall, point_pr_auc, point_fpr = _paired_metric_delta(
        relevant,
        candidate_role=candidate_role,
        incumbent_role=incumbent_role,
    )
    charges = np.asarray(sorted(relevant["charge_id"].astype(str).unique()))
    if len(charges) < 2:
        raise ValueError("paired Bootstrap requires at least two charge_id groups")
    candidate = relevant.loc[relevant["model_role"].eq(candidate_role)].sort_values(
        "hr_coil_id", kind="stable"
    )
    incumbent = relevant.loc[relevant["model_role"].eq(incumbent_role)].sort_values(
        "hr_coil_id", kind="stable"
    )
    truth = candidate["y_true"].to_numpy(dtype=int)
    candidate_score = candidate["risk_score"].to_numpy(dtype=float)
    incumbent_score = incumbent["risk_score"].to_numpy(dtype=float)
    candidate_predicted = candidate_score >= candidate["policy_threshold"].to_numpy(
        dtype=float
    )
    incumbent_predicted = incumbent_score >= incumbent["policy_threshold"].to_numpy(
        dtype=float
    )
    charge_index = {charge: index for index, charge in enumerate(charges)}
    row_charge_index = np.asarray(
        [charge_index[value] for value in candidate["charge_id"].astype(str)],
        dtype=int,
    )
    generator = np.random.default_rng(random_state)
    recall_deltas: list[float] = []
    pr_auc_deltas: list[float] = []
    fpr_deltas: list[float] = []
    for _ in range(iterations):
        charge_weights = generator.multinomial(
            len(charges), np.full(len(charges), 1.0 / len(charges))
        )
        weights = charge_weights[row_charge_index].astype(float)
        positive_weight = float(weights[truth == 1].sum())
        negative_weight = float(weights[truth == 0].sum())
        if positive_weight == 0.0 or negative_weight == 0.0:
            continue
        candidate_recall = float(
            weights[(truth == 1) & candidate_predicted].sum() / positive_weight
        )
        incumbent_recall = float(
            weights[(truth == 1) & incumbent_predicted].sum() / positive_weight
        )
        candidate_fpr = float(
            weights[(truth == 0) & candidate_predicted].sum() / negative_weight
        )
        incumbent_fpr = float(
            weights[(truth == 0) & incumbent_predicted].sum() / negative_weight
        )
        recall_deltas.append(candidate_recall - incumbent_recall)
        pr_auc_deltas.append(
            float(
                average_precision_score(truth, candidate_score, sample_weight=weights)
                - average_precision_score(
                    truth, incumbent_score, sample_weight=weights
                )
            )
        )
        fpr_deltas.append(candidate_fpr - incumbent_fpr)
    if not recall_deltas:
        raise ValueError("paired Bootstrap produced no valid two-class iterations")
    if len(recall_deltas) < max(1, math.ceil(iterations * 0.90)):
        raise ValueError("paired Bootstrap produced too few valid iterations")
    return {
        "candidate_role": candidate_role,
        "incumbent_role": incumbent_role,
        "requested_iterations": int(iterations),
        "valid_iterations": int(len(recall_deltas)),
        "random_state": int(random_state),
        "recall_delta": point_recall,
        "recall_ci_low": float(np.quantile(recall_deltas, 0.025)),
        "recall_ci_high": float(np.quantile(recall_deltas, 0.975)),
        "pr_auc_delta": point_pr_auc,
        "pr_auc_ci_low": float(np.quantile(pr_auc_deltas, 0.025)),
        "pr_auc_ci_high": float(np.quantile(pr_auc_deltas, 0.975)),
        "fpr_delta": point_fpr,
        "fpr_ci_low": float(np.quantile(fpr_deltas, 0.025)),
        "fpr_ci_high": float(np.quantile(fpr_deltas, 0.975)),
    }


def gate_status(
    metrics: dict[str, object],
    observation: dict[str, object],
) -> dict[str, object]:
    deployment_eligible = False
    integrity_errors = int(observation.get("integrity_errors", 0))
    if integrity_errors > 0:
        return {
            "status": "INTEGRITY_REVIEW_REQUIRED",
            "deployment_eligible": deployment_eligible,
            "failed_checks": ["integrity_errors"],
        }
    minimums = {
        "days": 28,
        "labeled_coils": 5000,
        "positive_labels": 150,
    }
    missing = [
        field
        for field, minimum in minimums.items()
        if float(observation.get(field, 0)) < minimum
    ]
    if missing:
        return {
            "status": "COLLECTING",
            "deployment_eligible": deployment_eligible,
            "failed_checks": missing,
            "minimums": minimums,
        }
    if metrics.get("candidate_present") is not True:
        return {
            "status": "AWAITING_NEXT_CHALLENGER",
            "deployment_eligible": deployment_eligible,
            "failed_checks": ["candidate_present"],
        }
    checks = {
        "candidate_fpr": float(metrics.get("candidate_fpr", math.inf)) <= 0.15,
        "recall_delta": float(metrics.get("recall_delta", -math.inf)) >= 0.05,
        "recall_ci_low": float(metrics.get("recall_ci_low", -math.inf)) > 0.0,
        "pr_auc_delta": float(metrics.get("pr_auc_delta", -math.inf)) >= 0.005,
        "pr_auc_ci_low": float(metrics.get("pr_auc_ci_low", -math.inf)) >= 0.0,
        "candidate_ece": (
            metrics.get("candidate_ece") is not None
            and math.isfinite(float(metrics["candidate_ece"]))
            and float(metrics["candidate_ece"]) <= 0.05
        ),
    }
    failed = [field for field, passed in checks.items() if not passed]
    status = "HUMAN_REVIEW_CANDIDATE" if not failed else "RETAIN_SHADOW_INCUMBENT"
    return {
        "status": status,
        "deployment_eligible": deployment_eligible,
        "failed_checks": failed,
        "process_review_required": bool(metrics.get("process_review_required", False)),
    }


def _model_metrics(group: pd.DataFrame) -> dict[str, object]:
    truth = group["y_true"].to_numpy(dtype=int)
    score = group["risk_score"].to_numpy(dtype=float)
    threshold_values = group["policy_threshold"].drop_duplicates().to_numpy(float)
    if len(threshold_values) != 1:
        raise ValueError("one model role must use exactly one policy threshold")
    threshold = float(threshold_values[0])
    predicted = score >= threshold
    tn, fp, fn, tp = confusion_matrix(truth, predicted, labels=[0, 1]).ravel()
    both_classes = set(np.unique(truth)) == {0, 1}
    negative = tn + fp
    metrics: dict[str, object] = {
        "model_id": str(group["model_id"].iloc[0]),
        "model_role": str(group["model_role"].iloc[0]),
        "rows": int(len(group)),
        "positive_labels": int(truth.sum()),
        "policy_threshold": threshold,
        "pr_auc": float(average_precision_score(truth, score)) if both_classes else None,
        "roc_auc": float(roc_auc_score(truth, score)) if both_classes else None,
        "precision": float(precision_score(truth, predicted, zero_division=0)),
        "recall": float(recall_score(truth, predicted, zero_division=0)),
        "f1": float(f1_score(truth, predicted, zero_division=0)),
        "f2": float(fbeta_score(truth, predicted, beta=2.0, zero_division=0)),
        "alert_rate": float(predicted.mean()),
        "prevalence": float(truth.mean()),
        "false_alarm_rate": 0.0 if negative == 0 else float(fp / negative),
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
        "tp": int(tp),
        "brier_score": None,
        "log_loss": None,
        "ece": None,
    }
    calibrated = pd.to_numeric(group["calibrated_probability"], errors="coerce")
    if both_classes and calibrated.notna().all():
        probability = calibrated.to_numpy(float)
        metrics["brier_score"] = float(brier_score_loss(truth, probability))
        metrics["log_loss"] = float(log_loss(truth, probability, labels=[0, 1]))
        bins = pd.qcut(
            pd.Series(probability), q=min(10, len(probability)), labels=False, duplicates="drop"
        )
        calibration = pd.DataFrame(
            {"truth": truth, "probability": probability, "bin": bins}
        ).groupby("bin", observed=True).agg(
            rows=("truth", "size"),
            observed=("truth", "mean"),
            predicted=("probability", "mean"),
        )
        metrics["ece"] = float(
            (
                calibration["rows"]
                * (calibration["observed"] - calibration["predicted"]).abs()
            ).sum()
            / len(probability)
        )
    return metrics


def _period_metrics(joined: pd.DataFrame) -> pd.DataFrame:
    timestamps = pd.to_datetime(
        joined["prediction_generated_at"], utc=True, errors="raise"
    ).dt.tz_localize(None)
    rows: list[dict[str, object]] = []
    for period_type, values in (
        ("week", timestamps.dt.to_period("W-SUN").astype(str)),
        ("month", timestamps.dt.to_period("M").astype(str)),
    ):
        current = joined.assign(_period=values)
        for (period, _), group in current.groupby(
            ["_period", "model_role"], sort=True, observed=True
        ):
            rows.append(
                {
                    "period_type": period_type,
                    "period": str(period),
                    **_model_metrics(group),
                }
            )
    return pd.DataFrame(rows)


def _evaluation_digest(
    registry_path: Path,
    files: list[Path],
    *,
    output_dir: Path,
) -> str:
    digest = hashlib.sha256()
    digest.update(sha256_file(registry_path).encode("ascii"))
    resolved_root = Path(output_dir).resolve()
    logical_files: list[tuple[str, Path]] = []
    for path in files:
        resolved_path = Path(path).resolve()
        try:
            logical_path = resolved_path.relative_to(resolved_root).as_posix()
        except ValueError as exc:
            raise ValueError("evaluation evidence path escapes output root") from exc
        logical_files.append((logical_path, resolved_path))
    for logical_path, resolved_path in sorted(logical_files, key=lambda item: item[0]):
        digest.update(logical_path.encode("utf-8"))
        digest.update(sha256_file(resolved_path).encode("ascii"))
    return digest.hexdigest()


def _shadow_report(summary: dict[str, object]) -> str:
    gate = summary["gate"]
    observation = summary["observation"]
    evidence_status = str(summary.get("evidence_status", "UNKNOWN"))
    return "\n".join(
        [
            "# Steel Shadow 검증 보고서",
            "",
            "## 1. 고2 수준 비유",
            "",
            "경비원들이 정답을 보지 않고 먼저 기록한 경보와 나중에 공개된 정답을 맞춰 보는 중입니다.",
            f"현재 정답이 확정된 코일은 {observation['labeled_coils']}개이며 상태는 `{gate['status']}`입니다.",
            f"이 보고서의 증거 등급은 `{evidence_status}`입니다.",
            (
                "과거 답을 사용한 배관 리허설이므로 미래 Shadow 성능 증거로 사용할 수 없습니다."
                if evidence_status == "NON_INDEPENDENT_DRY_RUN"
                else "미래 데이터 증거는 등록 시각 이후의 독립 배치만 인정합니다."
            ),
            "합격선에 도달해도 자동 배치하지 않고 사람이 최종 검토합니다.",
            "",
            "## 2. 전문 용어 매핑",
            "",
            "- 먼저 기록한 경보: append-only Shadow prediction ledger",
            "- 나중에 공개된 정답: delayed finalized label",
            "- 관찰 경기 수: 28일·5,000 Coil·불량 150건 Gate",
            "- 사람의 최종 검토: HUMAN_REVIEW_CANDIDATE",
            f"- 증거 등급: {evidence_status}",
            "",
        ]
    )


def read_shadow_predictions(
    registry_path: Path,
    output_dir: Path,
    *,
    batch_id: str | None = None,
    hr_coil_id: str | None = None,
    now: Callable[[], pd.Timestamp] = _utc_now,
) -> list[dict[str, object]]:
    if (batch_id is None) == (hr_coil_id is None):
        raise ValueError("exactly one of batch_id or hr_coil_id is required")
    registry_path = Path(registry_path)
    output_dir = Path(output_dir)
    with _output_lock(output_dir):
        registry = _load_runtime_registry(registry_path)
        evaluation_time = now()
        if evaluation_time.tzinfo is None:
            raise ValueError("prediction read timestamp must be timezone-aware")
        evaluation_time = evaluation_time.tz_convert("UTC")
        prediction_files, _, _, _ = _checked_prediction_ledger(
            output_dir,
            registry_path,
            registry,
            evaluation_time=evaluation_time,
        )
        if not prediction_files:
            return []
        predictions = pd.concat(
            [
                pd.read_csv(
                    path,
                    encoding="utf-8-sig",
                    dtype={"hr_coil_id": "string"},
                )
                for path in prediction_files
            ],
            ignore_index=True,
        )
        if batch_id is not None:
            predictions = predictions.loc[
                predictions["batch_id"].astype(str).eq(str(batch_id))
            ]
        else:
            predictions = predictions.loc[
                predictions["hr_coil_id"].astype(str).eq(str(hr_coil_id))
            ]
        if predictions.empty:
            return []
        public_columns = [
            "batch_id",
            "charge_id",
            "slab_no",
            "hr_coil_id",
            "hr_date",
            "feature_available_at",
            "prediction_generated_at",
            "model_id",
            "model_role",
            "risk_score",
            "calibrated_probability",
            "policy_threshold",
            "predicted_label",
            "evidence_status",
        ]
        missing = sorted(set(public_columns) - set(predictions.columns))
        if missing:
            raise ValueError(
                "prediction ledger is missing public columns: " + ", ".join(missing)
            )
        predictions = predictions[public_columns].sort_values(
            ["hr_coil_id", "model_role"], kind="stable"
        )
        records: list[dict[str, object]] = []
        for row in predictions.to_dict(orient="records"):
            safe_row: dict[str, object] = {}
            for key, value in row.items():
                if pd.isna(value):
                    safe_row[str(key)] = None
                elif isinstance(value, np.generic):
                    safe_row[str(key)] = value.item()
                elif isinstance(value, pd.Timestamp):
                    safe_row[str(key)] = value.isoformat()
                else:
                    safe_row[str(key)] = value
            records.append(safe_row)
        return records


def evaluate_shadow(
    registry_path: Path,
    output_dir: Path,
    *,
    now: Callable[[], pd.Timestamp] = _utc_now,
    bootstrap_iterations: int = 2000,
) -> dict[str, object]:
    output_dir = Path(output_dir)
    with _output_lock(output_dir):
        return _evaluate_shadow_locked(
            registry_path,
            output_dir,
            now=now,
            bootstrap_iterations=bootstrap_iterations,
        )


def _evaluate_shadow_locked(
    registry_path: Path,
    output_dir: Path,
    *,
    now: Callable[[], pd.Timestamp],
    bootstrap_iterations: int,
) -> dict[str, object]:
    registry_path = Path(registry_path)
    output_dir = Path(output_dir)
    registry = _load_runtime_registry(registry_path)
    generated_at = now()
    if generated_at.tzinfo is None:
        raise ValueError("evaluation timestamp must be timezone-aware")
    generated_at = generated_at.tz_convert("UTC")
    (
        prediction_files,
        feature_files,
        prediction_source_files,
        prediction_metadata_files,
    ) = (
        _checked_prediction_ledger(
            output_dir,
            registry_path,
            registry,
            evaluation_time=generated_at,
        )
    )
    all_label_batches = _checked_label_batches(output_dir)
    _checked_committed_label_provenance(prediction_files, all_label_batches)
    effective_labels = _select_effective_label_batches(all_label_batches)
    output_mode_files = (
        [_output_mode_path(output_dir)] if _output_mode_path(output_dir).is_file() else []
    )
    label_files = [path for _, path in effective_labels]
    all_label_files = [path for _, path in all_label_batches]
    label_metadata_files = [
        Path(str(metadata["metadata_path"])) for metadata, _ in all_label_batches
    ]
    label_source_files = [
        Path(str(metadata["source_snapshot_path"])) for metadata, _ in all_label_batches
    ]
    for metadata, _ in all_label_batches:
        ingested_at = pd.Timestamp(metadata["ingested_at"])
        if ingested_at.tzinfo is None:
            raise ValueError("label ingestion timestamp must be timezone-aware")
        if ingested_at.tz_convert("UTC") > generated_at:
            raise ValueError("label ingestion timestamp is after evaluation time")
    if not prediction_files or not label_files:
        current_status = (
            "AWAITING_FUTURE_DATA" if not prediction_files else "COLLECTING"
        )
        status_digest = _evaluation_digest(
            registry_path,
            [
                *prediction_metadata_files,
                *prediction_files,
                *feature_files,
                *prediction_source_files,
                *output_mode_files,
                *label_metadata_files,
                *all_label_files,
                *label_source_files,
            ],
            output_dir=output_dir,
        )
        status_dir = output_dir / "status"
        status_path = status_dir / f"status-{status_digest[:16]}.json"
        report_path = status_dir / f"status-{status_digest[:16]}.md"
        if status_path.is_file():
            return read_json_strict(status_path)
        evidence_statuses = {
            str(read_json_strict(path).get("evidence_status"))
            for path in prediction_metadata_files
        }
        evidence_status = (
            next(iter(evidence_statuses))
            if len(evidence_statuses) == 1
            else INDEPENDENT_EVIDENCE_STATUS
        )
        status_payload: dict[str, object] = {
            "status": current_status,
            "deployment_eligible": False,
            "prediction_batches": len(prediction_files),
            "label_batches": len(all_label_batches),
            "generated_at": generated_at.isoformat(),
            "registry_sha256": sha256_file(registry_path),
            "evidence_status": evidence_status,
            "observation": {
                "days": 0,
                "labeled_coils": 0,
                "positive_labels": 0,
                "integrity_errors": 0,
            },
            "gate": {
                "status": current_status,
                "deployment_eligible": False,
                "failed_checks": [
                    "future_prediction_batch" if not prediction_files else "finalized_labels"
                ],
            },
            "status_path": str(status_path.resolve()),
            "report_path": str(report_path.resolve()),
        }
        atomic_write_text(_shadow_report(status_payload), report_path)
        atomic_write_json(status_payload, status_path)
        return status_payload
    evaluation_digest = _evaluation_digest(
        registry_path,
        [
            *prediction_metadata_files,
            *prediction_files,
            *feature_files,
            *prediction_source_files,
            *output_mode_files,
            *label_metadata_files,
            *all_label_files,
            *label_source_files,
        ],
        output_dir=output_dir,
    )
    evaluation_id = f"eval-{evaluation_digest[:16]}"
    evaluation_dir = output_dir / "evaluations" / evaluation_id
    summary_path = evaluation_dir / "shadow_evaluation.json"
    if summary_path.is_file():
        return read_json_strict(summary_path)
    predictions = pd.concat(
        [pd.read_csv(path, encoding="utf-8-sig") for path in prediction_files],
        ignore_index=True,
    )
    if "evidence_status" not in predictions.columns:
        raise ValueError("predictions are missing evidence_status")
    evidence_statuses = set(predictions["evidence_status"].astype(str))
    if len(evidence_statuses) != 1:
        raise ValueError("one evaluation cannot mix Shadow evidence statuses")
    evidence_status = next(iter(evidence_statuses))
    _validate_evidence_status(evidence_status, output_dir)
    dry_run = evidence_status == DRY_RUN_EVIDENCE_STATUS
    labels = pd.concat(
        [pd.read_csv(path, encoding="utf-8-sig") for path in label_files],
        ignore_index=True,
    )
    if labels["hr_coil_id"].duplicated().any():
        raise ValueError("effective label batches contain duplicate hr_coil_id")
    if pd.to_datetime(labels["label_finalized_at"], utc=True, errors="raise").gt(
        generated_at
    ).any():
        raise ValueError("label_finalized_at is after evaluation time")
    joined = settle_predictions(predictions, labels)
    metric_rows = [
        _model_metrics(group)
        for _, group in joined.groupby("model_role", sort=True, observed=True)
    ]
    metrics_frame = pd.DataFrame(metric_rows)
    period_metrics_frame = _period_metrics(joined)
    metric_by_role = {
        str(row["model_role"]): row for row in metrics_frame.to_dict(orient="records")
    }
    prediction_times = pd.to_datetime(joined["prediction_generated_at"], utc=True)
    label_times = pd.to_datetime(joined["label_finalized_at"], utc=True)
    observation = {
        "days": int((label_times.max() - prediction_times.min()).total_seconds() // 86400),
        "labeled_coils": int(joined["hr_coil_id"].nunique()),
        "positive_labels": int(
            joined[["hr_coil_id", "y_true"]].drop_duplicates()["y_true"].sum()
        ),
        "integrity_errors": 0,
    }
    comparison: dict[str, object] = {"candidate_present": False}
    bootstrap_rows = pd.DataFrame(
        columns=[
            "candidate_role",
            "incumbent_role",
            "requested_iterations",
            "valid_iterations",
            "random_state",
            "recall_delta",
            "recall_ci_low",
            "recall_ci_high",
            "pr_auc_delta",
            "pr_auc_ci_low",
            "pr_auc_ci_high",
            "fpr_delta",
            "fpr_ci_low",
            "fpr_ci_high",
        ]
    )
    if "next_challenger" in metric_by_role and "shadow_incumbent" in metric_by_role:
        candidate = metric_by_role["next_challenger"]
        comparison = {
            "candidate_present": True,
            "candidate_fpr": candidate["false_alarm_rate"],
            "candidate_ece": candidate["ece"],
            "process_review_required": False,
        }
        unique_truth = set(joined["y_true"].astype(int).unique())
        observation_minimums_met = (
            observation["days"] >= 28
            and observation["labeled_coils"] >= 5000
            and observation["positive_labels"] >= 150
        )
        bootstrap_ready = (
            (dry_run or observation_minimums_met)
            and unique_truth == {0, 1}
            and joined["charge_id"].astype(str).nunique() >= 2
        )
        if bootstrap_ready:
            try:
                bootstrap = paired_group_bootstrap(
                    joined,
                    candidate_role="next_challenger",
                    incumbent_role="shadow_incumbent",
                    iterations=bootstrap_iterations,
                    random_state=42,
                )
            except ValueError:
                comparison["bootstrap_status"] = "INSUFFICIENT_VALID_RESAMPLES"
            else:
                comparison.update(bootstrap)
                comparison["bootstrap_status"] = "COMPLETE"
                bootstrap_rows = pd.DataFrame([bootstrap])
        else:
            comparison["bootstrap_status"] = "PENDING_MINIMUM_OBSERVATION"
    numeric_drift_rows: list[dict[str, object]] = []
    categorical_drift_rows: list[dict[str, object]] = []
    reference_path_value = registry.get("reference_cohort_path")
    if reference_path_value is not None and feature_files:
        reference = pd.read_csv(Path(str(reference_path_value)), encoding="utf-8-sig")
        if "dataset_split" in reference.columns:
            reference = reference.loc[reference["dataset_split"].eq("TRAIN")].reset_index(
                drop=True
            )
        requested_engineered = {
            feature
            for model in registry["models"]
            for feature in model["feature_schema"].get("engineered", [])
        }
        if requested_engineered:
            reference, _ = add_engineered_features(reference)
        current = pd.concat(
            [pd.read_csv(path, encoding="utf-8-sig") for path in feature_files],
            ignore_index=True,
        )
        if current["hr_coil_id"].duplicated().any():
            raise ValueError("feature snapshots contain duplicate hr_coil_id")
        numeric_features: list[str] = []
        categorical_features: list[str] = []
        for model in registry["models"]:
            for feature in model["feature_schema"]["numeric"]:
                if feature not in numeric_features:
                    numeric_features.append(feature)
            for feature in model["feature_schema"]["categorical"]:
                if feature not in categorical_features:
                    categorical_features.append(feature)
        for feature in numeric_features:
            if feature not in reference or feature not in current:
                continue
            row = numeric_drift(
                feature,
                reference[feature],
                current[feature],
                comparison_split="SHADOW_ALL_SCORED",
            )
            row["drift_alert"] = bool(
                float(row["psi"]) >= 0.25 or float(row["ks_statistic"]) >= 0.20
            )
            numeric_drift_rows.append(row)
        for feature in categorical_features:
            if feature not in reference or feature not in current:
                continue
            row, _ = categorical_drift(
                feature,
                reference[feature],
                current[feature],
                comparison_split="SHADOW_ALL_SCORED",
            )
            reference_values = set(
                reference[feature].astype("string").fillna("__MISSING__")
            )
            current_values = current[feature].astype("string").fillna("__MISSING__")
            row["unknown_category_row_count"] = int(
                (~current_values.isin(reference_values)).sum()
            )
            row["drift_alert"] = bool(
                float(row["psi"]) >= 0.25
                or float(row["jensen_shannon_divergence"]) >= 0.10
                or float(row["new_category_row_rate"]) >= 0.05
            )
            categorical_drift_rows.append(row)
    numeric_drift_frame = pd.DataFrame(numeric_drift_rows)
    categorical_drift_frame = pd.DataFrame(categorical_drift_rows)
    if numeric_drift_frame.empty:
        numeric_drift_frame = pd.DataFrame(
            columns=["feature", "comparison_split", "psi", "ks_statistic", "drift_alert"]
        )
    if categorical_drift_frame.empty:
        categorical_drift_frame = pd.DataFrame(
            columns=[
                "feature",
                "comparison_split",
                "psi",
                "jensen_shannon_divergence",
                "new_category_row_rate",
                "unknown_category_row_count",
                "drift_alert",
            ]
        )
    process_review_required = bool(
        (
            not numeric_drift_frame.empty
            and numeric_drift_frame["drift_alert"].astype(bool).any()
        )
        or (
            not categorical_drift_frame.empty
            and categorical_drift_frame["drift_alert"].astype(bool).any()
        )
    )
    comparison["process_review_required"] = process_review_required
    if dry_run:
        gate = {
            "status": "AWAITING_FUTURE_DATA",
            "deployment_eligible": False,
            "failed_checks": ["independent_future_shadow_evidence"],
        }
    else:
        gate = gate_status(comparison, observation)
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    joined_path = evaluation_dir / "shadow_joined_predictions.csv"
    metrics_path = evaluation_dir / "shadow_model_metrics.csv"
    period_metrics_path = evaluation_dir / "shadow_period_metrics.csv"
    bootstrap_path = evaluation_dir / "shadow_bootstrap_intervals.csv"
    numeric_drift_path = evaluation_dir / "shadow_numeric_drift.csv"
    categorical_drift_path = evaluation_dir / "shadow_categorical_drift.csv"
    report_path = evaluation_dir / "shadow_report.md"
    atomic_write_csv(joined, joined_path)
    atomic_write_csv(metrics_frame, metrics_path)
    atomic_write_csv(period_metrics_frame, period_metrics_path)
    atomic_write_csv(bootstrap_rows, bootstrap_path)
    atomic_write_csv(numeric_drift_frame, numeric_drift_path)
    atomic_write_csv(categorical_drift_frame, categorical_drift_path)
    summary: dict[str, object] = {
        "evaluation_id": evaluation_id,
        "evaluation_sha256": evaluation_digest,
        "generated_at": generated_at.isoformat(),
        "registry_sha256": sha256_file(registry_path),
        "prediction_batches": len(prediction_files),
        "label_batches": len(all_label_batches),
        "observation": observation,
        "model_metrics": metric_rows,
        "comparison": comparison,
        "gate": gate,
        "evidence_status": evidence_status,
        "joined_predictions_path": str(joined_path.resolve()),
        "model_metrics_path": str(metrics_path.resolve()),
        "period_metrics_path": str(period_metrics_path.resolve()),
        "bootstrap_intervals_path": str(bootstrap_path.resolve()),
        "numeric_drift_path": str(numeric_drift_path.resolve()),
        "categorical_drift_path": str(categorical_drift_path.resolve()),
        "process_review_required": process_review_required,
        "report_path": str(report_path.resolve()),
        "deployment_eligible": False,
    }
    atomic_write_text(_shadow_report(summary), report_path)
    atomic_write_json(summary, summary_path)
    return summary


def run_historical_dry_run(
    modeling_cohort: Path,
    registry_path: Path,
    output_dir: Path,
    *,
    bootstrap_iterations: int = 2000,
) -> dict[str, object]:
    modeling_cohort = Path(modeling_cohort)
    registry_path = Path(registry_path)
    output_dir = Path(output_dir)
    if "dry_run" not in output_dir.name:
        raise ValueError("historical Dry Run output directory name must contain dry_run")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("historical Dry Run output directory must be empty")
    registry = load_and_validate_registry(registry_path)
    frame = pd.read_csv(
        modeling_cohort,
        encoding="utf-8-sig",
        dtype={"charge_id": "string", "slab_no": "string", "hr_coil_id": "string"},
    )
    required = {"dataset_split", "judge", "ap_date", "hr_date", *IDENTITY_COLUMNS}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"modeling cohort is missing Dry Run columns: {', '.join(missing)}")
    holdout = frame.loc[frame["dataset_split"].eq("HOLDOUT")].copy()
    if holdout.empty:
        raise ValueError("historical Dry Run requires HOLDOUT rows")
    included: list[str] = []
    for model in registry["models"]:
        for feature in model["feature_schema"]["included"]:
            if feature not in included:
                included.append(str(feature))
    base_features = [
        feature for feature in included if feature not in set(ENGINEERED_FEATURES)
    ]
    missing_features = sorted(set(base_features) - set(holdout.columns))
    if missing_features:
        raise ValueError(
            f"modeling cohort is missing Dry Run model features: {', '.join(missing_features)}"
        )
    hr_dates = pd.to_datetime(holdout["hr_date"], format="%Y-%m-%d", errors="raise")
    available_at = hr_dates.dt.tz_localize("UTC") + pd.Timedelta(hours=23)
    feature_batch = holdout[[*IDENTITY_COLUMNS, "hr_date", *base_features]].copy()
    feature_batch.insert(4, "feature_available_at", available_at.map(pd.Timestamp.isoformat))
    prediction_time = available_at.max() + pd.Timedelta(minutes=1)
    ap_dates = pd.to_datetime(holdout["ap_date"], format="%Y-%m-%d", errors="raise")
    finalized_at = ap_dates.dt.tz_localize("UTC") + pd.Timedelta(days=1)
    finalized_at = finalized_at.where(
        finalized_at.gt(prediction_time), prediction_time + pd.Timedelta(minutes=1)
    )
    labels = holdout[["hr_coil_id", "judge"]].copy()
    labels["label_finalized_at"] = finalized_at.map(pd.Timestamp.isoformat)
    input_dir = output_dir / "dry_run_inputs"
    feature_path = input_dir / "historical_holdout_features.csv"
    label_path = input_dir / "historical_holdout_labels.csv"
    atomic_write_csv(feature_batch, feature_path)
    atomic_write_csv(labels, label_path)
    with _output_lock(output_dir):
        _score_feature_batch_locked(
            feature_path,
            registry_path,
            output_dir,
            now=lambda: prediction_time,
            evidence_status=DRY_RUN_EVIDENCE_STATUS,
        )
    ingestion_time = finalized_at.max() + pd.Timedelta(minutes=1)
    ingest_label_batch(
        label_path,
        output_dir,
        registry_path=registry_path,
        now=lambda: ingestion_time,
    )
    summary = evaluate_shadow(
        registry_path,
        output_dir,
        now=lambda: ingestion_time + pd.Timedelta(minutes=1),
        bootstrap_iterations=bootstrap_iterations,
    )
    if summary.get("evidence_status") != DRY_RUN_EVIDENCE_STATUS:
        raise RuntimeError("historical Dry Run evidence status was not preserved")
    if summary.get("gate", {}).get("status") != "AWAITING_FUTURE_DATA":
        raise RuntimeError("historical Dry Run escaped its immutable safety state")
    return summary


def shadow_status(output_dir: Path) -> dict[str, object]:
    output_dir = Path(output_dir)
    summaries = []
    for path in (output_dir / "evaluations").glob("*/shadow_evaluation.json"):
        payload = read_json_strict(path)
        summaries.append((str(payload.get("generated_at", "")), path, payload))
    if summaries:
        _, path, latest = max(summaries, key=lambda item: item[0])
        return {
            "status": latest["gate"]["status"],
            "deployment_eligible": False,
            "latest_evaluation_path": str(path.resolve()),
            "observation": latest["observation"],
            "gate": latest["gate"],
        }
    status_items = []
    for path in (output_dir / "status").glob("status-*.json"):
        payload = read_json_strict(path)
        status_items.append((str(payload.get("generated_at", "")), path, payload))
    if status_items:
        _, path, latest = max(status_items, key=lambda item: item[0])
        return {
            **latest,
            "status_path": str(path.resolve()),
            "deployment_eligible": False,
        }
    prediction_batches = len(
        list((output_dir / "prediction_batches").glob("*.metadata.json"))
    )
    label_batches = len(list((output_dir / "label_batches").glob("*.metadata.json")))
    return {
        "status": (
            "AWAITING_FUTURE_DATA" if prediction_batches == 0 else "COLLECTING"
        ),
        "deployment_eligible": False,
        "prediction_batches": prediction_batches,
        "label_batches": label_batches,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run steel quality shadow validation.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    register.add_argument("--baseline-model", required=True)
    register.add_argument("--challenger-model", required=True)
    register.add_argument("--challenger-capacity", required=True)
    register.add_argument("--nextgen-model")
    register.add_argument("--nextgen-calibrator")
    register.add_argument("--registry", required=True)
    register.add_argument("--reference-cohort")
    score = subparsers.add_parser("score")
    score.add_argument("--feature-batch", required=True)
    score.add_argument("--registry", required=True)
    score.add_argument("--output-dir", required=True)
    ingest = subparsers.add_parser("ingest-labels")
    ingest.add_argument("--label-batch", required=True)
    ingest.add_argument("--output-dir", required=True)
    ingest.add_argument("--supersedes-label-batch-id")
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--registry", required=True)
    evaluate.add_argument("--output-dir", required=True)
    evaluate.add_argument("--bootstrap-iterations", type=int, default=2000)
    status = subparsers.add_parser("status")
    status.add_argument("--output-dir", required=True)
    dry_run = subparsers.add_parser("dry-run")
    dry_run.add_argument("--modeling-cohort", required=True)
    dry_run.add_argument("--registry", required=True)
    dry_run.add_argument("--output-dir", required=True)
    dry_run.add_argument("--bootstrap-iterations", type=int, default=2000)
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    if args.command == "register":
        summary = build_registry(
            Path(args.baseline_model),
            Path(args.challenger_model),
            Path(args.challenger_capacity),
            Path(args.registry),
            None if args.nextgen_model is None else Path(args.nextgen_model),
            None if args.nextgen_calibrator is None else Path(args.nextgen_calibrator),
            reference_cohort=(
                None if args.reference_cohort is None else Path(args.reference_cohort)
            ),
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "score":
        summary = score_feature_batch(
            Path(args.feature_batch),
            Path(args.registry),
            Path(args.output_dir),
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "ingest-labels":
        summary = ingest_label_batch(
            Path(args.label_batch),
            Path(args.output_dir),
            supersedes_label_batch_id=args.supersedes_label_batch_id,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "evaluate":
        summary = evaluate_shadow(
            Path(args.registry),
            Path(args.output_dir),
            bootstrap_iterations=args.bootstrap_iterations,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "status":
        summary = shadow_status(Path(args.output_dir))
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "dry-run":
        summary = run_historical_dry_run(
            Path(args.modeling_cohort),
            Path(args.registry),
            Path(args.output_dir),
            bootstrap_iterations=args.bootstrap_iterations,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
