"""Quality model training for bundle publication and local evaluation."""

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import onnx
import pandas as pd

from equipment_quality.artifacts import compute_model_identity
from equipment_quality.deterministic import canonical_json_bytes
from equipment_quality.genealogy import build_genealogy
from equipment_quality.model_export import export_onnx, onnx_probabilities, verify_probability_parity
from equipment_quality.model_features import encode_model_rows
from equipment_quality.model_metrics import classification_metrics
from equipment_quality.model_runtime import single_threaded
from equipment_quality.model_training import evaluate_candidates
from equipment_quality.models import TimeSplitResult
from equipment_quality.schema import load_analysis_config, read_inputs
from equipment_quality.time_split import build_time_split


def grouped_holdout_importance(estimator, matrix, labels, evaluable, contract, seed):
    y = np.asarray(labels, dtype=np.int8)
    if set(y) != {0, 1}:
        raise ValueError("INSUFFICIENT_LABELS")
    rng = np.random.default_rng(seed)
    with single_threaded():
        baseline = classification_metrics(y, estimator.predict_proba(matrix)[:, 1], 0.5, float(y.mean())).average_precision
        result = []
        offset = 0
        for feature in contract.to_wire()["features"]:
            width = (1 if feature["type"] == "numeric" else len(feature["vocabulary"])) + 1
            losses = []
            for _ in range(10):
                altered = matrix.copy()
                altered[:, offset:offset + width] = matrix[rng.permutation(len(y)), offset:offset + width]
                ap = classification_metrics(y, estimator.predict_proba(altered)[:, 1], 0.5, float(y.mean())).average_precision
                losses.append(baseline - ap)
            result.append({"feature": feature["name"], "apDecreaseMean": float(np.mean(losses)), "apDecreaseStd": float(np.std(losses))})
            offset += width
    return tuple(result)


def _digest(data):
    return "sha256:" + sha256(data).hexdigest()


def _test_vectors(rows, matrix, probabilities, feature_names):
    raw = []
    for _, row in rows.iloc[:10].iterrows():
        raw.append({name: None if pd.isna(row[name]) else row[name] for name in feature_names})
    return {"raw": raw, "matrix": matrix[:10], "probabilities": probabilities[:10]}


def select_exportable(ranked, reference_x):
    failures = []
    for candidate in ranked:
        try:
            blob = export_onnx(candidate.estimator, reference_x.shape[1])
            with single_threaded():
                native = candidate.estimator.predict_proba(reference_x)[:, 1]
            parity = verify_probability_parity(native, onnx_probabilities(blob, reference_x)[:, 1])
            return candidate, blob, parity, failures
        except (ValueError, RuntimeError, NotImplementedError) as error:
            failures.append({"model": candidate.name, "reason": str(error)})
    raise ValueError(f"NO_EXPORTABLE_CANDIDATE: {failures}")


def _qualification_reasons(metrics, prevalence):
    return [reason for failed, reason in (
        (metrics.average_precision <= prevalence, "AP_NOT_ABOVE_PREVALENCE"),
        (metrics.brier_skill <= 0, "NONPOSITIVE_BRIER_SKILL"),
        (metrics.tp + metrics.fp == 0, "NO_PREDICTED_DEFECTS"),
        (metrics.tp + metrics.fn == 0, "NO_HOLDOUT_DEFECTS"),
    ) if failed]


def score_holdout(estimator, matrix, labels, probabilities, threshold, reference_prevalence, contract, seed):
    metrics = classification_metrics(labels, probabilities, threshold, reference_prevalence)
    qualification = "RESEARCH_ONLY" if _qualification_reasons(metrics, float(np.mean(labels))) else "QUALIFIED"
    importance = grouped_holdout_importance(estimator, matrix, labels, None, contract, seed)
    return metrics, qualification, importance


@dataclass(frozen=True)
class _TrainedModel:
    blob: bytes
    metadata: dict
    report: dict
    width: int
    training_period: dict
    holdout_period: dict
    qualification_reasons: list
    vectors: dict | None


def _date_span(days: pd.Series) -> dict[str, str]:
    return {"from": days.min().isoformat(), "to": days.max().isoformat()}


def _train_model(split: TimeSplitResult, seed: int, *, collect_vectors: bool = False) -> _TrainedModel:
    candidates = evaluate_candidates(split.reference_rows, seed)
    reference = split.reference_rows.loc[(split.reference_rows.label_status == "AVAILABLE") & split.reference_rows.judge.isin(("양품", "불량"))]
    encoded_reference = encode_model_rows(reference, candidates.feature_contract, require_labels=True)
    reference_x = encoded_reference.matrix[encoded_reference.evaluable]
    holdout = encode_model_rows(split.holdout_rows, candidates.feature_contract, require_labels=True)
    holdout_x = holdout.matrix[holdout.evaluable]
    holdout_y = np.asarray(holdout.labels, dtype=object)[holdout.evaluable].astype(np.int8)
    if set(holdout_y) != {0, 1}:
        raise ValueError("INSUFFICIENT_LABELS")
    chosen, blob, parity, failures = select_exportable(candidates.ranked, reference_x)
    probability = onnx_probabilities(blob, holdout_x)[:, 1]
    metrics, qualification, importance = score_holdout(chosen.estimator, holdout_x, holdout_y, probability, chosen.threshold, candidates.reference_prevalence, candidates.feature_contract, seed)
    prevalence = float(holdout_y.mean())
    feature_wire = candidates.feature_contract.to_wire()
    metadata = {"schemaVersion": "sfep-quality-model-dev/v1", "model": chosen.name, "features": feature_wire, "featureSha256": _digest(canonical_json_bytes(feature_wire)), "threshold": chosen.threshold, "qualification": qualification, "onnxSha256": _digest(blob)}
    report = {"holdoutTotal": len(split.holdout_rows), "holdoutEvaluable": len(holdout_y), "holdoutDefects": int(holdout_y.sum()), "oofRanking": [{"model": c.name, "averagePrecision": c.metrics.average_precision, "threshold": c.threshold} for c in candidates.ranked], "selectedModel": chosen.name, "exportFailures": failures, "threshold": chosen.threshold, "holdoutRecall": metrics.recall, "holdoutPrecision": metrics.precision, "holdoutAveragePrecision": metrics.average_precision, "holdoutBrierSkill": metrics.brier_skill, "holdoutPrevalence": prevalence, "confusion": {"tp": metrics.tp, "fp": metrics.fp, "tn": metrics.tn, "fn": metrics.fn}, "qualification": metadata["qualification"], "pythonOnnxMaxDifference": parity, "importance": list(importance)}
    eligible_rows = split.holdout_rows.iloc[np.flatnonzero(holdout.evaluable)]
    vectors = None
    if collect_vectors:
        vectors = _test_vectors(eligible_rows, holdout_x[:10].tolist(), probability[:10].tolist(), [f["name"] for f in feature_wire["features"]])
    return _TrainedModel(
        blob, metadata, report, reference_x.shape[1],
        _date_span(reference.loc[encoded_reference.evaluable, "hr_date"]),
        _date_span(eligible_rows.hr_date),
        _qualification_reasons(metrics, prevalence), vectors,
    )


def bundle_model_artifacts(split: TimeSplitResult, seed: int) -> tuple[bytes, bytes]:
    """Train once on the verified producer split and return its v3 artifacts."""
    trained = _train_model(split, seed)
    report = trained.report
    model = onnx.load_model_from_string(trained.blob)
    metadata = {
        **trained.metadata,
        "schemaVersion": "sfep-quality-model-metadata/v1",
        "trainingPeriod": trained.training_period,
        "holdoutPeriod": trained.holdout_period,
        "qualificationReasons": trained.qualification_reasons or ["HOLDOUT_GATES_PASSED"],
        "onnx": {
            "triggerStage": "FURNACE_EXTRACTED",
            "opsets": {item.domain: item.version for item in model.opset_import},
            "input": {"name": "features", "dataType": "FLOAT", "shape": [None, trained.width]},
            "labelOutput": {"name": "label", "dataType": "INT64", "shape": [None]},
            "probabilityOutput": {"name": "probabilities", "dataType": "FLOAT", "shape": [None, 2]},
        },
        "oofRanking": report["oofRanking"],
        "selectionReason": "HIGHEST_EXPORTABLE_OOF_RANK",
        "holdout": {
            "total": report["holdoutTotal"],
            "evaluable": report["holdoutEvaluable"],
            "defects": report["holdoutDefects"],
            "recall": report["holdoutRecall"],
            "precision": report["holdoutPrecision"],
            "averagePrecision": report["holdoutAveragePrecision"],
            "brierSkill": report["holdoutBrierSkill"],
            "prevalence": report["holdoutPrevalence"],
            "confusion": report["confusion"],
        },
        "importance": report["importance"],
    }
    metadata["modelId"] = compute_model_identity(metadata).value
    return trained.blob, canonical_json_bytes(metadata)


def run_model(config_path: Path, data_dir: Path, output_dir: Path) -> dict:
    config_bytes = config_path.read_bytes()
    config = load_analysis_config(config_path)
    seed_material = json.loads(config_bytes)["bootstrap"]["seedMaterial"]
    seed = int(seed_material[-8:], 16)
    split = build_time_split(build_genealogy(read_inputs(data_dir)), config)
    trained = _train_model(split, seed, collect_vectors=True)
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "quality_model.onnx").write_bytes(trained.blob)
    for name, value in (("quality_model.json", trained.metadata), ("quality_model_report.json", trained.report), ("quality_model_test_vectors.json", trained.vectors)):
        (output_dir / name).write_bytes(canonical_json_bytes(value))
    return trained.report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_model(args.config, args.data_dir, args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
