import importlib
import hashlib
import inspect
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import joblib
import numpy as np
import pandas as pd


def load_subject():
    try:
        return importlib.import_module("training.steel.run_shadow")
    except ModuleNotFoundError:
        raise AssertionError("shadow runner module is not implemented") from None


def write_model_fixtures(root: Path) -> tuple[Path, Path, Path]:
    baseline = importlib.import_module("training.steel.train_baseline")
    features = pd.DataFrame(
        {
            "f_heat_temp": [1180.0, 1190.0, 1250.0, 1260.0, 1200.0, 1270.0],
            "furnace_no": ["1호기", "2호기", "1호기", "2호기", "1호기", "2호기"],
        }
    )
    target = pd.Series([0, 0, 1, 1, 0, 1])
    schema = {
        "included": ["f_heat_temp", "furnace_no"],
        "numeric": ["f_heat_temp"],
        "categorical": ["furnace_no"],
        "excluded": [],
        "target": "judge",
        "positive_label": "불량",
    }
    baseline_pipeline = baseline._build_pipeline(
        "logistic_regression",
        schema["numeric"],
        schema["categorical"],
        random_state=7,
        random_forest_estimators=20,
        n_jobs=1,
    ).fit(features, target)
    challenger_pipeline = baseline._build_pipeline(
        "logistic_regression",
        schema["numeric"],
        schema["categorical"],
        random_state=11,
        random_forest_estimators=20,
        n_jobs=1,
    ).fit(features, target)
    cohort_sha256 = "1" * 64
    baseline_path = root / "baseline_model.joblib"
    challenger_path = root / "challenger_model.joblib"
    capacity_path = root / "validation_alert_capacity.csv"
    joblib.dump(
        {
            "pipeline": baseline_pipeline,
            "feature_schema": schema,
            "baseline_version": "steel-quality-baseline-v0.3",
            "positive_label": "불량",
            "modeling_cohort_sha256": cohort_sha256,
            "deployment_eligible": False,
        },
        baseline_path,
    )
    baseline_sha256 = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    joblib.dump(
        {
            "pipeline": challenger_pipeline,
            "model_name": "logistic_l2_c0_1",
            "feature_schema": schema,
            "challenger_version": "steel-quality-challenger-v0.2",
            "positive_label": "불량",
            "policy_fpr_cap": 0.15,
            "policy_threshold": 0.72,
            "modeling_cohort_sha256": cohort_sha256,
            "baseline_model_sha256": baseline_sha256,
            "deployment_eligible": False,
            "holdout_used_for_selection": False,
        },
        challenger_path,
    )
    pd.DataFrame(
        [
            {
                "model": "incumbent_logistic",
                "false_alarm_cap": 0.15,
                "selected_threshold": 0.81,
            },
            {
                "model": "logistic_l2_c0_1",
                "false_alarm_cap": 0.15,
                "selected_threshold": 0.72,
            },
        ]
    ).to_csv(capacity_path, index=False, encoding="utf-8-sig")
    return baseline_path, challenger_path, capacity_path


def score_label_test_coils(
    subject,
    root: Path,
    output_dir: Path,
    coil_ids: list[str],
) -> Path:
    baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
    registry_path = root / "label-test-registry.json"
    subject.build_registry(
        baseline_path,
        challenger_path,
        capacity_path,
        registry_path,
        now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
    )
    feature_path = root / "label-test-features.csv"
    pd.DataFrame(
        {
            "charge_id": [f"C{index}" for index, _ in enumerate(coil_ids, start=1)],
            "slab_no": ["1"] * len(coil_ids),
            "hr_coil_id": coil_ids,
            "hr_date": ["2026-09-01"] * len(coil_ids),
            "feature_available_at": ["2026-09-01T01:00:00Z"] * len(coil_ids),
            "f_heat_temp": [1240.0] * len(coil_ids),
            "furnace_no": ["1호기"] * len(coil_ids),
        }
    ).to_csv(feature_path, index=False, encoding="utf-8-sig")
    subject.score_feature_batch(
        feature_path,
        registry_path,
        output_dir,
        now=lambda: pd.Timestamp("2026-09-01T02:00:00Z"),
    )
    return registry_path


class RegistryTest(unittest.TestCase):
    def test_registers_reference_and_shadow_incumbent(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"

            registry = subject.build_registry(
                baseline_model=baseline_path,
                challenger_model=challenger_path,
                challenger_capacity=capacity_path,
                output_path=registry_path,
                now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
            )

            self.assertEqual(registry["registry_version"], "steel-shadow-registry-v0.1")
            self.assertEqual(
                [model["role"] for model in registry["models"]],
                ["reference_baseline", "shadow_incumbent"],
            )
            self.assertEqual(
                [model["policy_threshold"] for model in registry["models"]],
                [0.81, 0.72],
            )
            self.assertTrue(
                all(len(model["artifact_sha256"]) == 64 for model in registry["models"])
            )
            self.assertTrue(
                all(math.isfinite(model["policy_threshold"]) for model in registry["models"])
            )
            self.assertFalse(registry["deployment_eligible"])
            self.assertEqual(
                subject.load_and_validate_registry(registry_path), registry
            )

    def test_loads_relative_artifact_paths_from_registry_directory(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts = root / "artifacts"
            registry_dir = root / "config"
            artifacts.mkdir()
            registry_dir.mkdir()
            baseline_path, challenger_path, capacity_path = write_model_fixtures(
                artifacts
            )
            registry_path = registry_dir / "registry.json"
            registry = subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
                now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
            )
            for model in registry["models"]:
                model["artifact_path"] = os.path.relpath(
                    Path(model["artifact_path"]).resolve(), registry_dir.resolve()
                )
            registry["challenger_capacity_path"] = os.path.relpath(
                Path(registry["challenger_capacity_path"]).resolve(),
                registry_dir.resolve(),
            )
            registry_path.write_text(
                json.dumps(registry, ensure_ascii=False), encoding="utf-8"
            )

            loaded = subject.load_and_validate_registry(registry_path)

            self.assertEqual(
                Path(loaded["models"][0]["artifact_path"]),
                baseline_path.resolve(),
            )
            self.assertEqual(
                Path(loaded["challenger_capacity_path"]),
                capacity_path.resolve(),
            )

    def test_rejects_hash_schema_and_class_order_corruption(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            registry = subject.build_registry(
                baseline_model=baseline_path,
                challenger_model=challenger_path,
                challenger_capacity=capacity_path,
                output_path=registry_path,
            )

            corruptions = []
            wrong_hash = json.loads(json.dumps(registry))
            wrong_hash["models"][0]["artifact_sha256"] = "0" * 64
            corruptions.append(("artifact SHA-256", wrong_hash))
            wrong_schema = json.loads(json.dumps(registry))
            wrong_schema["models"][0]["feature_schema"]["included"] = []
            corruptions.append(("feature schema", wrong_schema))
            wrong_classes = json.loads(json.dumps(registry))
            wrong_classes["models"][0]["class_order"] = [1, 0]
            corruptions.append(("class order", wrong_classes))

            for index, (message, payload) in enumerate(corruptions):
                with self.subTest(message=message):
                    path = root / f"corrupt-{index}.json"
                    path.write_text(
                        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                    )
                    with self.assertRaisesRegex(ValueError, message):
                        subject.load_and_validate_registry(path)

    def test_rejects_calibrator_that_belongs_to_another_model(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            reference_path = root / "reference.csv"
            pd.DataFrame(
                {
                    "f_heat_temp": [1180.0, 1250.0],
                    "furnace_no": ["1호기", "2호기"],
                }
            ).to_csv(reference_path, index=False, encoding="utf-8-sig")
            cohort_sha256 = hashlib.sha256(reference_path.read_bytes()).hexdigest()
            baseline_fixture = joblib.load(baseline_path)
            baseline_fixture["modeling_cohort_sha256"] = cohort_sha256
            joblib.dump(baseline_fixture, baseline_path)
            challenger_fixture = joblib.load(challenger_path)
            challenger_fixture["modeling_cohort_sha256"] = cohort_sha256
            challenger_fixture["baseline_model_sha256"] = hashlib.sha256(
                baseline_path.read_bytes()
            ).hexdigest()
            joblib.dump(challenger_fixture, challenger_path)
            nextgen_path = root / "nextgen.joblib"
            nextgen = joblib.load(challenger_path)
            nextgen.update(
                {
                    "nextgen_version": "steel-quality-nextgen-v0.1",
                    "model_name": "fixture-nextgen",
                    "policy_threshold": 0.7,
                    "selected_thresholds_by_fpr_cap": {"0.15": 0.7},
                    "feature_schema_sha256": subject._json_sha256(
                        nextgen["feature_schema"]
                    ),
                    "predictor_source_sha256": subject.sha256_file(
                        Path(subject.__file__).with_name("nextgen_model.py")
                    ),
                    "selection_oof_scores_sha256": "2" * 64,
                    "oof_fold_assignments_sha256": "3" * 64,
                    "development_identity_sha256": "4" * 64,
                    "software": {"fixture": "1"},
                }
            )
            joblib.dump(nextgen, nextgen_path)
            calibrator_path = root / "calibrator.joblib"
            joblib.dump(
                {
                    "method": "platt",
                    "calibrator": object(),
                    "model_sha256": "0" * 64,
                    "modeling_cohort_sha256": cohort_sha256,
                    "deployment_eligible": False,
                },
                calibrator_path,
            )

            with self.assertRaisesRegex(ValueError, "calibrator model SHA-256"):
                subject.build_registry(
                    baseline_path,
                    challenger_path,
                    capacity_path,
                    root / "registry.json",
                    nextgen_path,
                    calibrator_path,
                    reference_cohort=reference_path,
                )

    def test_nextgen_registration_requires_reference_cohort(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)

            with self.assertRaisesRegex(ValueError, "reference cohort"):
                subject.build_registry(
                    baseline_path,
                    challenger_path,
                    capacity_path,
                    root / "registry.json",
                    nextgen_model=challenger_path,
                    nextgen_calibrator=challenger_path,
                )


class ShadowScoringTest(unittest.TestCase):
    def test_scores_without_labels_and_never_overwrites_a_batch(self):
        subject = load_subject()
        self.assertTrue(hasattr(subject, "score_feature_batch"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            output_dir = root / "shadow"
            subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
                now=lambda: pd.Timestamp("2026-08-31T23:00:00Z"),
            )
            feature_path = root / "future_features.csv"
            pd.DataFrame(
                {
                    "charge_id": ["C10"],
                    "slab_no": ["1"],
                    "hr_coil_id": ["H10"],
                    "hr_date": ["2026-09-01"],
                    "feature_available_at": ["2026-09-01T01:00:00Z"],
                    "f_heat_temp": [1240.0],
                    "furnace_no": ["1호기"],
                }
            ).to_csv(feature_path, index=False, encoding="utf-8-sig")

            summary = subject.score_feature_batch(
                feature_path,
                registry_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-01T02:00:00Z"),
            )

            predictions = pd.read_csv(
                summary["prediction_path"], encoding="utf-8-sig"
            )
            self.assertNotIn("judge", predictions)
            self.assertEqual(
                set(predictions["model_role"]),
                {"reference_baseline", "shadow_incumbent"},
            )
            self.assertTrue(
                predictions["prediction_generated_at"].eq(
                    "2026-09-01T02:00:00+00:00"
                ).all()
            )
            self.assertTrue(np.isfinite(predictions["risk_score"]).all())
            self.assertTrue(Path(summary["feature_snapshot_path"]).is_file())
            self.assertTrue(Path(summary["source_snapshot_path"]).is_file())
            self.assertEqual(
                Path(summary["source_snapshot_path"]).read_bytes(),
                feature_path.read_bytes(),
            )
            before = Path(summary["prediction_path"]).read_bytes()
            repeated = subject.score_feature_batch(
                feature_path,
                registry_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-01T03:00:00Z"),
            )
            self.assertEqual(summary["batch_id"], repeated["batch_id"])
            self.assertEqual(before, Path(summary["prediction_path"]).read_bytes())

    def test_rejects_independent_features_from_before_shadow_start(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
                now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
            )
            feature_path = root / "historical.csv"
            pd.DataFrame(
                {
                    "charge_id": ["C1"],
                    "slab_no": ["1"],
                    "hr_coil_id": ["H1"],
                    "hr_date": ["2025-09-01"],
                    "feature_available_at": ["2026-09-01T01:00:00Z"],
                    "f_heat_temp": [1240.0],
                    "furnace_no": ["1호기"],
                }
            ).to_csv(feature_path, index=False, encoding="utf-8-sig")

            with self.assertRaisesRegex(ValueError, "shadow_start_at"):
                subject.score_feature_batch(
                    feature_path,
                    registry_path,
                    root / "shadow",
                    now=lambda: pd.Timestamp("2026-09-01T02:00:00Z"),
                )

    def test_rejects_reused_coils_and_features_from_the_future(self):
        subject = load_subject()
        self.assertTrue(hasattr(subject, "score_feature_batch"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            output_dir = root / "shadow"
            subject.build_registry(
                baseline_path, challenger_path, capacity_path, registry_path
            )

            def write_features(path: Path, temperature: float, available: str) -> None:
                pd.DataFrame(
                    {
                        "charge_id": ["C10"],
                        "slab_no": ["1"],
                        "hr_coil_id": ["H10"],
                        "hr_date": ["2026-09-01"],
                        "feature_available_at": [available],
                        "f_heat_temp": [temperature],
                        "furnace_no": ["1호기"],
                    }
                ).to_csv(path, index=False, encoding="utf-8-sig")

            first = root / "first.csv"
            write_features(first, 1240.0, "2026-09-01T01:00:00Z")
            subject.score_feature_batch(
                first,
                registry_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-01T02:00:00Z"),
            )
            changed = root / "changed.csv"
            write_features(changed, 1300.0, "2026-09-01T01:00:00Z")
            with self.assertRaisesRegex(ValueError, "already scored"):
                subject.score_feature_batch(
                    changed,
                    registry_path,
                    output_dir,
                    now=lambda: pd.Timestamp("2026-09-01T03:00:00Z"),
                )
            future = root / "future.csv"
            pd.DataFrame(
                {
                    "charge_id": ["C11"],
                    "slab_no": ["1"],
                    "hr_coil_id": ["H11"],
                    "hr_date": ["2026-09-02"],
                    "feature_available_at": ["2026-09-02T05:00:00Z"],
                    "f_heat_temp": [1240.0],
                    "furnace_no": ["1호기"],
                }
            ).to_csv(future, index=False, encoding="utf-8-sig")
            with self.assertRaisesRegex(ValueError, "feature_available_at"):
                subject.score_feature_batch(
                    future,
                    registry_path,
                    output_dir,
                    now=lambda: pd.Timestamp("2026-09-02T04:00:00Z"),
                )

    def test_rejects_unknown_evidence_status_and_public_override(self):
        subject = load_subject()
        self.assertNotIn(
            "evidence_status", inspect.signature(subject.score_feature_batch).parameters
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
                now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
            )
            feature_path = root / "historical.csv"
            pd.DataFrame(
                {
                    "charge_id": ["C1"],
                    "slab_no": ["1"],
                    "hr_coil_id": ["H1"],
                    "hr_date": ["2025-09-01"],
                    "feature_available_at": ["2026-09-01T01:00:00Z"],
                    "f_heat_temp": [1240.0],
                    "furnace_no": ["1호기"],
                }
            ).to_csv(feature_path, index=False, encoding="utf-8-sig")

            with self.assertRaisesRegex(ValueError, "evidence_status"):
                subject._score_feature_batch_locked(
                    feature_path,
                    registry_path,
                    root / "shadow",
                    now=lambda: pd.Timestamp("2026-09-01T02:00:00Z"),
                    evidence_status="HISTORICAL_REPLAY",
                )

    def test_evaluation_rejects_coordinated_unknown_evidence_status(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            output_dir = root / "shadow"
            subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
                now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
            )
            feature_path = root / "features.csv"
            pd.DataFrame(
                {
                    "charge_id": ["C1"],
                    "slab_no": ["1"],
                    "hr_coil_id": ["H1"],
                    "hr_date": ["2026-09-01"],
                    "feature_available_at": ["2026-09-01T01:00:00Z"],
                    "f_heat_temp": [1240.0],
                    "furnace_no": ["1호기"],
                }
            ).to_csv(feature_path, index=False, encoding="utf-8-sig")
            scored = subject.score_feature_batch(
                feature_path,
                registry_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-01T02:00:00Z"),
            )
            predictions = pd.read_csv(scored["prediction_path"], encoding="utf-8-sig")
            predictions["evidence_status"] = "HISTORICAL_REPLAY"
            predictions.to_csv(
                scored["prediction_path"], index=False, encoding="utf-8-sig"
            )
            metadata_path = Path(scored["metadata_path"])
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["evidence_status"] = "HISTORICAL_REPLAY"
            metadata["prediction_sha256"] = subject.sha256_file(
                Path(scored["prediction_path"])
            )
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "evidence_status"):
                subject.evaluate_shadow(
                    registry_path,
                    output_dir,
                    now=lambda: pd.Timestamp("2026-09-01T03:00:00Z"),
                )

    def test_evaluation_rechecks_temporal_provenance_after_mode_changes(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            dry_output = root / "shadow_dry_run_output"
            subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
                now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
            )
            feature_path = root / "historical.csv"
            pd.DataFrame(
                {
                    "charge_id": ["C1"],
                    "slab_no": ["1"],
                    "hr_coil_id": ["H1"],
                    "hr_date": ["2025-09-01"],
                    "feature_available_at": ["2025-09-01T01:00:00Z"],
                    "f_heat_temp": [1240.0],
                    "furnace_no": ["1호기"],
                }
            ).to_csv(feature_path, index=False, encoding="utf-8-sig")
            with subject._output_lock(dry_output):
                scored = subject._score_feature_batch_locked(
                    feature_path,
                    registry_path,
                    dry_output,
                    now=lambda: pd.Timestamp("2025-09-01T02:00:00Z"),
                    evidence_status=subject.DRY_RUN_EVIDENCE_STATUS,
                )
            predictions = pd.read_csv(scored["prediction_path"], encoding="utf-8-sig")
            predictions["evidence_status"] = subject.INDEPENDENT_EVIDENCE_STATUS
            predictions.to_csv(
                scored["prediction_path"], index=False, encoding="utf-8-sig"
            )
            metadata_path = Path(scored["metadata_path"])
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["evidence_status"] = subject.INDEPENDENT_EVIDENCE_STATUS
            metadata["prediction_sha256"] = subject.sha256_file(
                Path(scored["prediction_path"])
            )
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )
            mode_path = dry_output / ".shadow-mode.json"
            mode = json.loads(mode_path.read_text(encoding="utf-8"))
            mode["evidence_status"] = subject.INDEPENDENT_EVIDENCE_STATUS
            mode_path.write_text(json.dumps(mode), encoding="utf-8")
            alias = root / "shadow_alias"
            alias.symlink_to(dry_output, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "shadow_start_at"):
                subject.evaluate_shadow(
                    registry_path,
                    alias,
                    now=lambda: pd.Timestamp("2026-09-30T00:00:00Z"),
                )

    def test_evaluation_recomputes_source_bound_prediction_batch_id(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            output_dir = root / "shadow"
            subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
                now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
            )
            feature_path = root / "features.csv"
            pd.DataFrame(
                {
                    "charge_id": ["C1"],
                    "slab_no": ["1"],
                    "hr_coil_id": ["H1"],
                    "hr_date": ["2026-09-01"],
                    "feature_available_at": ["2026-09-01T01:00:00Z"],
                    "f_heat_temp": [1240.0],
                    "furnace_no": ["1호기"],
                }
            ).to_csv(feature_path, index=False, encoding="utf-8-sig")
            scored = subject.score_feature_batch(
                feature_path,
                registry_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-01T02:00:00Z"),
            )
            source_path = Path(scored["source_snapshot_path"])
            source_path.write_bytes(b"coordinated-source-change")
            metadata_path = Path(scored["metadata_path"])
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["source_sha256"] = subject.sha256_file(source_path)
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "batch_id"):
                subject.evaluate_shadow(
                    registry_path,
                    output_dir,
                    now=lambda: pd.Timestamp("2026-09-01T03:00:00Z"),
                )


class ShadowEvaluationTest(unittest.TestCase):
    def test_evaluate_shadow_takes_the_output_lock(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            output_dir = root / "shadow_output"
            subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
                now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
            )

            with mock.patch.object(
                subject, "_output_lock", wraps=subject._output_lock
            ) as output_lock:
                subject.evaluate_shadow(
                    registry_path,
                    output_dir,
                    now=lambda: pd.Timestamp("2026-09-01T01:00:00Z"),
                )

            output_lock.assert_called_once_with(output_dir)

    def test_reads_one_validated_prediction_batch_or_coil(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            output_dir = root / "shadow_output"
            subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
                now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
            )
            feature_path = root / "features.csv"
            pd.DataFrame(
                {
                    "charge_id": ["C1"],
                    "slab_no": ["1"],
                    "hr_coil_id": ["H1"],
                    "hr_date": ["2026-09-01"],
                    "feature_available_at": ["2026-09-01T01:00:00Z"],
                    "f_heat_temp": [1240.0],
                    "furnace_no": ["1호기"],
                }
            ).to_csv(feature_path, index=False, encoding="utf-8-sig")
            scored = subject.score_feature_batch(
                feature_path,
                registry_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-01T02:00:00Z"),
            )

            by_batch = subject.read_shadow_predictions(
                registry_path,
                output_dir,
                batch_id=scored["batch_id"],
                now=lambda: pd.Timestamp("2026-09-01T03:00:00Z"),
            )
            by_coil = subject.read_shadow_predictions(
                registry_path,
                output_dir,
                hr_coil_id="H1",
                now=lambda: pd.Timestamp("2026-09-01T03:00:00Z"),
            )

            self.assertEqual(len(by_batch), 2)
            self.assertEqual(by_batch, by_coil)
            self.assertEqual(
                [row["model_role"] for row in by_coil],
                ["reference_baseline", "shadow_incumbent"],
            )
            self.assertFalse(
                any("path" in key.lower() for row in by_coil for key in row)
            )
            json.dumps(by_coil, ensure_ascii=False, allow_nan=False)

    def test_prediction_reader_requires_exactly_one_filter(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "exactly one"):
                subject.read_shadow_predictions(root / "registry.json", root / "shadow")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                subject.read_shadow_predictions(
                    root / "registry.json",
                    root / "shadow",
                    batch_id="batch-1",
                    hr_coil_id="H1",
                )

    def test_evaluation_digest_is_path_representation_independent(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry_path = root / "registry.json"
            registry_path.write_text('{"registry": 1}', encoding="utf-8")
            output_dir = root / "shadow"
            first = output_dir / "prediction_batches" / "batch.metadata.json"
            second = output_dir / "label_batches" / "batch.metadata.json"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_text("prediction", encoding="utf-8")
            second.write_text("label", encoding="utf-8")
            relative_output = Path(os.path.relpath(output_dir, Path.cwd()))
            relative_files = [
                Path(os.path.relpath(first, Path.cwd())),
                Path(os.path.relpath(second, Path.cwd())),
            ]
            alias = root / "shadow_alias"
            alias.symlink_to(output_dir, target_is_directory=True)
            alias_files = [
                alias / "prediction_batches" / first.name,
                alias / "label_batches" / second.name,
            ]

            absolute_digest = subject._evaluation_digest(
                registry_path, [first, second], output_dir=output_dir
            )
            relative_digest = subject._evaluation_digest(
                registry_path, relative_files, output_dir=relative_output
            )
            alias_digest = subject._evaluation_digest(
                registry_path, alias_files, output_dir=alias
            )

            self.assertEqual(absolute_digest, relative_digest)
            self.assertEqual(absolute_digest, alias_digest)

    def test_rejects_labels_finalized_before_prediction(self):
        subject = load_subject()
        self.assertTrue(hasattr(subject, "settle_predictions"))
        predictions = pd.DataFrame(
            {
                "charge_id": ["C1"],
                "hr_coil_id": ["H1"],
                "prediction_generated_at": ["2026-09-02T00:00:00Z"],
                "model_role": ["shadow_incumbent"],
                "risk_score": [0.5],
                "policy_threshold": [0.7],
            }
        )
        labels = pd.DataFrame(
            {
                "hr_coil_id": ["H1"],
                "judge": ["불량"],
                "label_finalized_at": ["2026-09-01T00:00:00Z"],
            }
        )

        with self.assertRaisesRegex(ValueError, "after prediction"):
            subject.settle_predictions(predictions, labels)

    def test_label_correction_must_replace_the_complete_superseded_batch(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "shadow"
            score_label_test_coils(subject, root, output_dir, ["H1", "H2"])
            original_path = root / "original.csv"
            pd.DataFrame(
                {
                    "hr_coil_id": ["H1", "H2"],
                    "judge": ["양품", "불량"],
                    "label_finalized_at": ["2026-09-03T00:00:00Z"] * 2,
                }
            ).to_csv(original_path, index=False, encoding="utf-8-sig")
            original = subject.ingest_label_batch(
                original_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-03T01:00:00Z"),
            )
            self.assertTrue(Path(original["source_snapshot_path"]).is_file())
            self.assertEqual(
                Path(original["source_snapshot_path"]).read_bytes(),
                original_path.read_bytes(),
            )
            correction_path = root / "correction.csv"
            pd.DataFrame(
                {
                    "hr_coil_id": ["H1"],
                    "judge": ["불량"],
                    "label_finalized_at": ["2026-09-04T00:00:00Z"],
                }
            ).to_csv(correction_path, index=False, encoding="utf-8-sig")

            with self.assertRaisesRegex(ValueError, "replace every coil"):
                subject.ingest_label_batch(
                    correction_path,
                    output_dir,
                    supersedes_label_batch_id=original["batch_id"],
                    now=lambda: pd.Timestamp("2026-09-04T01:00:00Z"),
                )

    def test_label_ledger_recomputes_source_bound_batch_id(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "shadow"
            score_label_test_coils(subject, root, output_dir, ["H1"])
            label_path = root / "labels.csv"
            pd.DataFrame(
                {
                    "hr_coil_id": ["H1"],
                    "judge": ["불량"],
                    "label_finalized_at": ["2026-09-03T00:00:00Z"],
                }
            ).to_csv(label_path, index=False, encoding="utf-8-sig")
            ingested = subject.ingest_label_batch(
                label_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-03T01:00:00Z"),
            )
            source_path = Path(ingested["source_snapshot_path"])
            source_path.write_bytes(b"coordinated-label-source-change")
            metadata_path = Path(ingested["metadata_path"])
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["source_sha256"] = subject.sha256_file(source_path)
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "batch_id"):
                subject._effective_label_batches(output_dir)

    def test_evaluation_validates_superseded_label_files(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "shadow"
            registry_path = score_label_test_coils(
                subject, root, output_dir, ["H1", "H2"]
            )
            original_path = root / "original-labels.csv"
            pd.DataFrame(
                {
                    "hr_coil_id": ["H1", "H2"],
                    "judge": ["양품", "불량"],
                    "label_finalized_at": ["2026-09-03T00:00:00Z"] * 2,
                }
            ).to_csv(original_path, index=False, encoding="utf-8-sig")
            original = subject.ingest_label_batch(
                original_path,
                output_dir,
                registry_path=registry_path,
                now=lambda: pd.Timestamp("2026-09-03T01:00:00Z"),
            )
            correction_path = root / "corrected-labels.csv"
            pd.DataFrame(
                {
                    "hr_coil_id": ["H1", "H2"],
                    "judge": ["불량", "불량"],
                    "label_finalized_at": ["2026-09-04T00:00:00Z"] * 2,
                }
            ).to_csv(correction_path, index=False, encoding="utf-8-sig")
            subject.ingest_label_batch(
                correction_path,
                output_dir,
                supersedes_label_batch_id=original["batch_id"],
                registry_path=registry_path,
                now=lambda: pd.Timestamp("2026-09-04T01:00:00Z"),
            )
            original_labels = Path(original["labels_path"])
            original_labels.write_bytes(original_labels.read_bytes() + b"\n")

            with self.assertRaisesRegex(
                subject.LedgerIntegrityError, "label ledger.*SHA-256"
            ):
                subject.evaluate_shadow(
                    registry_path,
                    output_dir,
                    now=lambda: pd.Timestamp("2026-09-04T02:00:00Z"),
                    bootstrap_iterations=10,
                )

    def test_rejects_labels_finalized_after_ingestion_time(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            label_path = root / "future_label.csv"
            pd.DataFrame(
                {
                    "hr_coil_id": ["H1"],
                    "judge": ["불량"],
                    "label_finalized_at": ["2099-01-01T00:00:00Z"],
                }
            ).to_csv(label_path, index=False, encoding="utf-8-sig")

            with self.assertRaisesRegex(ValueError, "after ingestion"):
                subject.ingest_label_batch(
                    label_path,
                    root / "shadow",
                    now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
                )

    def test_evaluation_rejects_prediction_ledger_tampering(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            output_dir = root / "shadow"
            subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
                now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
            )
            feature_path = root / "features.csv"
            pd.DataFrame(
                {
                    "charge_id": ["C1", "C2"],
                    "slab_no": ["1", "1"],
                    "hr_coil_id": ["H1", "H2"],
                    "hr_date": ["2026-09-01"] * 2,
                    "feature_available_at": ["2026-09-01T01:00:00Z"] * 2,
                    "f_heat_temp": [1185.0, 1255.0],
                    "furnace_no": ["1호기", "2호기"],
                }
            ).to_csv(feature_path, index=False, encoding="utf-8-sig")
            scored = subject.score_feature_batch(
                feature_path,
                registry_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-01T02:00:00Z"),
            )
            label_path = root / "labels.csv"
            pd.DataFrame(
                {
                    "hr_coil_id": ["H1", "H2"],
                    "judge": ["양품", "불량"],
                    "label_finalized_at": ["2026-09-02T00:00:00Z"] * 2,
                }
            ).to_csv(label_path, index=False, encoding="utf-8-sig")
            subject.ingest_label_batch(
                label_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-02T01:00:00Z"),
            )
            predictions = pd.read_csv(scored["prediction_path"], encoding="utf-8-sig")
            predictions["risk_score"] = [0.0, 1.0, 0.0, 1.0]
            predictions.to_csv(
                scored["prediction_path"], index=False, encoding="utf-8-sig"
            )

            with self.assertRaisesRegex(ValueError, "prediction.*SHA-256"):
                subject.evaluate_shadow(
                    registry_path,
                    output_dir,
                    now=lambda: pd.Timestamp("2026-09-02T02:00:00Z"),
                    bootstrap_iterations=10,
                )

    def test_evaluation_rejects_immutable_source_snapshot_tampering(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            output_dir = root / "shadow"
            subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
                now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
            )
            feature_path = root / "features.csv"
            pd.DataFrame(
                {
                    "charge_id": ["C1"],
                    "slab_no": ["1"],
                    "hr_coil_id": ["H1"],
                    "hr_date": ["2026-09-01"],
                    "feature_available_at": ["2026-09-01T01:00:00Z"],
                    "f_heat_temp": [1240.0],
                    "furnace_no": ["1호기"],
                }
            ).to_csv(feature_path, index=False, encoding="utf-8-sig")
            scored = subject.score_feature_batch(
                feature_path,
                registry_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-01T02:00:00Z"),
            )
            Path(scored["source_snapshot_path"]).write_bytes(b"tampered")

            with self.assertRaisesRegex(ValueError, "source snapshot SHA-256"):
                subject.evaluate_shadow(
                    registry_path,
                    output_dir,
                    now=lambda: pd.Timestamp("2026-09-01T03:00:00Z"),
                )

    def test_collects_until_all_minimum_observation_rules_are_met(self):
        subject = load_subject()
        self.assertTrue(hasattr(subject, "gate_status"))
        gate = subject.gate_status(
            {
                "candidate_present": True,
                "candidate_fpr": 0.14,
                "recall_delta": 0.06,
                "recall_ci_low": 0.01,
                "pr_auc_delta": 0.006,
                "pr_auc_ci_low": 0.0,
                "candidate_ece": 0.04,
            },
            {
                "days": 27,
                "labeled_coils": 4999,
                "positive_labels": 149,
                "integrity_errors": 0,
            },
        )

        self.assertEqual(gate["status"], "COLLECTING")
        self.assertFalse(gate["deployment_eligible"])

    def test_only_material_significant_improvement_reaches_human_review(self):
        subject = load_subject()
        self.assertTrue(hasattr(subject, "gate_status"))
        gate = subject.gate_status(
            {
                "candidate_present": True,
                "candidate_fpr": 0.14,
                "recall_delta": 0.06,
                "recall_ci_low": 0.01,
                "pr_auc_delta": 0.006,
                "pr_auc_ci_low": 0.0,
                "candidate_ece": 0.04,
            },
            {
                "days": 28,
                "labeled_coils": 5000,
                "positive_labels": 150,
                "integrity_errors": 0,
            },
        )

        self.assertEqual(gate["status"], "HUMAN_REVIEW_CANDIDATE")
        self.assertFalse(gate["deployment_eligible"])

    def test_missing_candidate_calibration_fails_closed(self):
        subject = load_subject()
        gate = subject.gate_status(
            {
                "candidate_present": True,
                "candidate_fpr": 0.14,
                "recall_delta": 0.06,
                "recall_ci_low": 0.01,
                "pr_auc_delta": 0.006,
                "pr_auc_ci_low": 0.0,
                "candidate_ece": None,
            },
            {
                "days": 28,
                "labeled_coils": 5000,
                "positive_labels": 150,
                "integrity_errors": 0,
            },
        )

        self.assertEqual(gate["status"], "RETAIN_SHADOW_INCUMBENT")
        self.assertIn("candidate_ece", gate["failed_checks"])

    def test_paired_group_bootstrap_is_reproducible(self):
        subject = load_subject()
        self.assertTrue(hasattr(subject, "paired_group_bootstrap"))
        rows = []
        for index in range(40):
            y_true = int(index % 2 == 0)
            rows.extend(
                [
                    {
                        "charge_id": f"C{index}",
                        "hr_coil_id": f"H{index}",
                        "model_role": "shadow_incumbent",
                        "y_true": y_true,
                        "risk_score": 0.4,
                        "policy_threshold": 0.5,
                    },
                    {
                        "charge_id": f"C{index}",
                        "hr_coil_id": f"H{index}",
                        "model_role": "next_challenger",
                        "y_true": y_true,
                        "risk_score": 0.9 if y_true else 0.1,
                        "policy_threshold": 0.5,
                    },
                ]
            )
        joined = pd.DataFrame(rows)

        first = subject.paired_group_bootstrap(
            joined,
            candidate_role="next_challenger",
            incumbent_role="shadow_incumbent",
            iterations=200,
            random_state=42,
        )
        second = subject.paired_group_bootstrap(
            joined,
            candidate_role="next_challenger",
            incumbent_role="shadow_incumbent",
            iterations=200,
            random_state=42,
        )

        self.assertEqual(first, second)
        self.assertEqual(first["requested_iterations"], 200)
        self.assertGreater(first["recall_delta"], 0.0)
        self.assertGreater(first["pr_auc_delta"], 0.0)
        self.assertIn("fpr_delta", first)
        self.assertIn("fpr_ci_low", first)

    def test_ingests_delayed_labels_and_evaluates_as_collecting(self):
        subject = load_subject()
        self.assertTrue(hasattr(subject, "ingest_label_batch"))
        self.assertTrue(hasattr(subject, "evaluate_shadow"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            output_dir = root / "shadow"
            reference_path = root / "reference.csv"
            pd.DataFrame(
                {
                    "f_heat_temp": [1180.0, 1190.0, 1250.0, 1260.0],
                    "furnace_no": ["1호기", "2호기", "1호기", "2호기"],
                }
            ).to_csv(reference_path, index=False, encoding="utf-8-sig")
            reference_sha256 = hashlib.sha256(reference_path.read_bytes()).hexdigest()
            baseline_fixture = joblib.load(baseline_path)
            baseline_fixture["modeling_cohort_sha256"] = reference_sha256
            joblib.dump(baseline_fixture, baseline_path)
            challenger_fixture = joblib.load(challenger_path)
            challenger_fixture["modeling_cohort_sha256"] = reference_sha256
            challenger_fixture["baseline_model_sha256"] = hashlib.sha256(
                baseline_path.read_bytes()
            ).hexdigest()
            joblib.dump(challenger_fixture, challenger_path)
            subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
                reference_cohort=reference_path,
            )
            feature_path = root / "future_features.csv"
            pd.DataFrame(
                {
                    "charge_id": ["C10", "C11", "C12", "C13"],
                    "slab_no": ["1", "1", "1", "1"],
                    "hr_coil_id": ["H10", "H11", "H12", "H13"],
                    "hr_date": ["2026-09-01"] * 4,
                    "feature_available_at": ["2026-09-01T01:00:00Z"] * 4,
                    "f_heat_temp": [1185.0, 1255.0, 1195.0, 1265.0],
                    "furnace_no": ["1호기", "1호기", "2호기", "2호기"],
                }
            ).to_csv(feature_path, index=False, encoding="utf-8-sig")
            subject.score_feature_batch(
                feature_path,
                registry_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-01T02:00:00Z"),
            )
            unlabeled_feature_path = root / "unlabeled_future_features.csv"
            pd.DataFrame(
                {
                    "charge_id": ["C14"],
                    "slab_no": ["1"],
                    "hr_coil_id": ["H14"],
                    "hr_date": ["2026-09-01"],
                    "feature_available_at": ["2026-09-01T01:00:00Z"],
                    "f_heat_temp": [1400.0],
                    "furnace_no": ["신규호기"],
                }
            ).to_csv(
                unlabeled_feature_path, index=False, encoding="utf-8-sig"
            )
            subject.score_feature_batch(
                unlabeled_feature_path,
                registry_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-01T02:30:00Z"),
            )
            label_path = root / "labels.csv"
            pd.DataFrame(
                {
                    "hr_coil_id": ["H10", "H11", "H12", "H13"],
                    "judge": ["양품", "불량", "양품", "불량"],
                    "label_finalized_at": ["2026-09-03T02:00:00Z"] * 4,
                }
            ).to_csv(label_path, index=False, encoding="utf-8-sig")
            subject.ingest_label_batch(
                label_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-03T03:00:00Z"),
            )

            summary = subject.evaluate_shadow(
                registry_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-03T04:00:00Z"),
                bootstrap_iterations=50,
            )

            self.assertEqual(summary["gate"]["status"], "COLLECTING")
            self.assertEqual(summary["observation"]["labeled_coils"], 4)
            metrics = pd.read_csv(summary["model_metrics_path"], encoding="utf-8-sig")
            self.assertEqual(
                set(metrics["model_role"]),
                {"reference_baseline", "shadow_incumbent"},
            )
            self.assertTrue(Path(summary["joined_predictions_path"]).is_file())
            period_metrics = pd.read_csv(
                summary["period_metrics_path"], encoding="utf-8-sig"
            )
            self.assertEqual(set(period_metrics["period_type"]), {"week", "month"})
            for metric in ("f1", "f2", "alert_rate", "prevalence"):
                self.assertIn(metric, period_metrics.columns)
            numeric_drift = pd.read_csv(
                summary["numeric_drift_path"], encoding="utf-8-sig"
            )
            categorical_drift = pd.read_csv(
                summary["categorical_drift_path"], encoding="utf-8-sig"
            )
            self.assertEqual(set(numeric_drift["feature"]), {"f_heat_temp"})
            self.assertTrue(numeric_drift["current_n"].eq(5).all())
            self.assertEqual(set(categorical_drift["feature"]), {"furnace_no"})
            self.assertEqual(
                int(categorical_drift.loc[0, "unknown_category_row_count"]), 1
            )
            self.assertEqual(
                categorical_drift.loc[0, "comparison_split"], "SHADOW_ALL_SCORED"
            )

    def test_historical_dry_run_never_calls_the_deployment_gate(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            output_dir = root / "shadow_dry_run_output"
            subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
            )
            modeling_path = root / "modeling.csv"
            pd.DataFrame(
                {
                    "charge_id": ["C10", "C11", "C12", "C13"],
                    "slab_no": ["1", "1", "1", "1"],
                    "hr_coil_id": ["H10", "H11", "H12", "H13"],
                    "hr_date": ["2025-09-01"] * 4,
                    "ap_date": ["2025-09-03"] * 4,
                    "judge": ["양품", "불량", "양품", "불량"],
                    "dataset_split": ["HOLDOUT"] * 4,
                    "f_heat_temp": [1185.0, 1255.0, 1195.0, 1265.0],
                    "furnace_no": ["1호기", "1호기", "2호기", "2호기"],
                }
            ).to_csv(modeling_path, index=False, encoding="utf-8-sig")

            with mock.patch.object(
                subject,
                "gate_status",
                side_effect=AssertionError("dry run must not call the deployment gate"),
            ):
                summary = subject.run_historical_dry_run(
                    modeling_path,
                    registry_path,
                    output_dir,
                    bootstrap_iterations=20,
                )

            self.assertEqual(summary["gate"]["status"], "AWAITING_FUTURE_DATA")
            self.assertEqual(summary["evidence_status"], "NON_INDEPENDENT_DRY_RUN")
            self.assertFalse(summary["deployment_eligible"])
            self.assertIn(
                "NON_INDEPENDENT_DRY_RUN",
                Path(summary["report_path"]).read_text(encoding="utf-8"),
            )
            predictions = pd.concat(
                [
                    pd.read_csv(path, encoding="utf-8-sig")
                    for path in (output_dir / "prediction_batches").glob(
                        "*.predictions.csv"
                    )
                ],
                ignore_index=True,
            )
            self.assertEqual(
                set(predictions["evidence_status"]), {"NON_INDEPENDENT_DRY_RUN"}
            )
            self.assertFalse((root / "shadow_output").exists())


class CommandSurfaceTest(unittest.TestCase):
    def test_empty_real_shadow_waits_for_future_data(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            status = subject.shadow_status(Path(temp_dir) / "shadow_output")

        self.assertEqual(status["status"], "AWAITING_FUTURE_DATA")
        self.assertFalse(status["deployment_eligible"])

    def test_empty_evaluation_writes_status_json_and_report(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline_path, challenger_path, capacity_path = write_model_fixtures(root)
            registry_path = root / "registry.json"
            output_dir = root / "shadow_output"
            subject.build_registry(
                baseline_path,
                challenger_path,
                capacity_path,
                registry_path,
                now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
            )

            status = subject.evaluate_shadow(
                registry_path,
                output_dir,
                now=lambda: pd.Timestamp("2026-09-01T01:00:00Z"),
            )
            self.assertEqual(status["status"], "AWAITING_FUTURE_DATA")
            self.assertTrue(Path(status["status_path"]).is_file())
            self.assertTrue(Path(status["report_path"]).is_file())

    def test_parses_label_evaluate_and_status_commands(self):
        subject = load_subject()
        self.assertIn("argv", inspect.signature(subject._parse_args).parameters)

        ingest = subject._parse_args(
            [
                "ingest-labels",
                "--label-batch",
                "labels.csv",
                "--output-dir",
                "shadow",
            ]
        )
        evaluate = subject._parse_args(
            [
                "evaluate",
                "--registry",
                "registry.json",
                "--output-dir",
                "shadow",
            ]
        )
        status = subject._parse_args(["status", "--output-dir", "shadow"])
        dry_run = subject._parse_args(
            [
                "dry-run",
                "--modeling-cohort",
                "modeling.csv",
                "--registry",
                "registry.json",
                "--output-dir",
                "shadow_dry_run_output",
            ]
        )

        self.assertEqual(ingest.command, "ingest-labels")
        self.assertEqual(evaluate.command, "evaluate")
        self.assertEqual(status.command, "status")
        self.assertEqual(dry_run.command, "dry-run")


if __name__ == "__main__":
    unittest.main()
