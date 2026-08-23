import importlib
import json
import tempfile
import unittest
from pathlib import Path

import joblib
import numpy as np
import pandas as pd


def load_subject():
    try:
        return importlib.import_module("training.steel.train_nextgen")
    except ModuleNotFoundError:
        raise AssertionError("nextgen training module is not implemented") from None


class ScoreColumnPredictor:
    def __init__(self, column: str):
        self.column = column
        self.feature_names_in_ = np.asarray([column], dtype=object)
        self.classes_ = np.asarray([0, 1])

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        positive = frame[self.column].to_numpy(dtype=float)
        return np.column_stack([1.0 - positive, positive])


def modeling_frame() -> pd.DataFrame:
    rows = []
    sequence = 0
    specs = [
        ("TRAIN", "2025-06-20", "2025-06-25", 30),
        ("TRAIN", "2025-07-20", "2025-07-25", 30),
        ("TRAIN", "2025-07-25", "2025-07-30", 30),
        ("VALIDATION", "2025-08-15", "2025-08-20", 30),
        ("HOLDOUT", "2025-09-15", "2025-09-20", 30),
    ]
    for split, hr_date, ap_date, count in specs:
        for index in range(count):
            is_bad = index % 5 == 0
            current_ap_date = ap_date
            if split == "VALIDATION" and index >= 26:
                current_ap_date = "2025-09-05"
            rows.append(
                {
                    "charge_id": f"C{sequence}",
                    "slab_no": "1",
                    "hr_coil_id": f"H{sequence}",
                    "cast_date": hr_date,
                    "f_ext_date": hr_date,
                    "hr_date": hr_date,
                    "ap_date": current_ap_date,
                    "judge": "불량" if is_bad else "양품",
                    "dataset_split": split,
                    "f_bfg": float(index % 3),
                    "f_cog": float(index % 4),
                    "f_ldg": float(index % 2),
                    "f_pre_interval": 10.0 + index,
                    "f_heat_interval": 20.0 + index,
                    "f_sock_interval": 30.0 + index,
                    "f_pre_temp": 900.0 + index,
                    "f_heat_temp": 1200.0 + index + (20 if is_bad else 0),
                    "f_sock_temp": 1250.0 + index,
                    "slab_width": 1300.0,
                    "hr_width": 1250.0 + index % 3,
                    "furnace_no": "1호기" if index % 2 else "2호기",
                }
            )
            sequence += 1
    return pd.DataFrame(rows)


class EngineeredFeatureTest(unittest.TestCase):
    def test_has_exact_formulas_and_safe_zero_division(self):
        subject = load_subject()
        source = pd.DataFrame(
            {
                "f_bfg": [1.0, 2.0],
                "f_cog": [3.0, 4.0],
                "f_ldg": [5.0, 6.0],
                "f_pre_interval": [10.0, 20.0],
                "f_heat_interval": [30.0, 40.0],
                "f_sock_interval": [50.0, 60.0],
                "f_pre_temp": [900.0, 910.0],
                "f_heat_temp": [1200.0, 1210.0],
                "f_sock_temp": [1250.0, 1260.0],
                "slab_width": [1300.0, 0.0],
                "hr_width": [1250.0, 1248.0],
            }
        )

        result, names = subject.add_engineered_features(source)

        self.assertEqual(
            names,
            [
                "gas_total",
                "heating_interval_total",
                "pre_to_heat_temp_delta",
                "heat_to_sock_temp_delta",
                "width_reduction",
                "width_ratio",
            ],
        )
        self.assertEqual(result["gas_total"].tolist(), [9.0, 12.0])
        self.assertEqual(result["heating_interval_total"].tolist(), [90.0, 120.0])
        self.assertEqual(result["pre_to_heat_temp_delta"].tolist(), [300.0, 300.0])
        self.assertEqual(result["heat_to_sock_temp_delta"].tolist(), [50.0, 50.0])
        self.assertEqual(result["width_reduction"].tolist(), [50.0, -1248.0])
        self.assertAlmostEqual(result.loc[0, "width_ratio"], 1250.0 / 1300.0)
        self.assertTrue(pd.isna(result.loc[1, "width_ratio"]))


class DevelopmentCohortTest(unittest.TestCase):
    def test_uses_train_and_only_as_of_validation(self):
        subject = load_subject()
        frame = modeling_frame()

        development = subject.development_cohort(frame)

        self.assertEqual(len(development), 116)
        self.assertEqual(set(development["dataset_split"]), {"TRAIN", "VALIDATION"})
        self.assertTrue(pd.to_datetime(development["ap_date"]).lt("2025-09-01").all())
        self.assertFalse(set(development["hr_coil_id"]) & set(
            frame.loc[frame["dataset_split"].eq("HOLDOUT"), "hr_coil_id"]
        ))
        with self.assertRaisesRegex(ValueError, "immutable.*2025-09-01"):
            subject.development_cohort(frame, freeze_date="2025-09-10")

    def test_holdout_values_and_dtype_cannot_change_development_schema(self):
        subject = load_subject()
        original = modeling_frame()
        changed = original.copy()
        holdout = changed["dataset_split"].eq("HOLDOUT")
        changed["f_heat_temp"] = changed["f_heat_temp"].astype("object")
        changed.loc[holdout, "f_heat_temp"] = "HOLDOUT_TYPE_POLLUTION"
        changed.loc[holdout, "judge"] = changed.loc[holdout, "judge"].map(
            {"양품": "불량", "불량": "양품"}
        )
        changed.loc[holdout, "charge_id"] = "CHANGED"

        first = subject.development_feature_schema(original)
        second = subject.development_feature_schema(changed)

        self.assertEqual(first, second)


class DevelopmentFoldTest(unittest.TestCase):
    def test_group_oof_and_time_folds_have_no_charge_overlap(self):
        subject = load_subject()
        development = subject.development_cohort(modeling_frame())

        group_folds = subject.group_oof_splits(development, n_splits=5, random_state=42)
        time_folds = subject.expanding_time_splits(development)

        self.assertEqual(len(group_folds), 5)
        self.assertEqual(len(time_folds), 2)
        scored = np.zeros(len(development), dtype=int)
        for fit_index, score_index in group_folds:
            fit_charges = set(development.iloc[fit_index]["charge_id"])
            score_charges = set(development.iloc[score_index]["charge_id"])
            self.assertFalse(fit_charges & score_charges)
            scored[score_index] += 1
        self.assertTrue(np.array_equal(scored, np.ones(len(development), dtype=int)))
        for fit_index, score_index in time_folds:
            fit_charges = set(development.iloc[fit_index]["charge_id"])
            score_charges = set(development.iloc[score_index]["charge_id"])
            self.assertFalse(fit_charges & score_charges)
            self.assertEqual(set(development.iloc[fit_index]["judge"]), {"양품", "불량"})
            self.assertEqual(set(development.iloc[score_index]["judge"]), {"양품", "불량"})


class NextGenCandidateTest(unittest.TestCase):
    def test_rank_ensemble_class_has_a_stable_importable_module(self):
        subject = load_subject()

        self.assertEqual(
            subject.FrozenRankAverageEnsemble.__module__,
            "training.steel.nextgen_model",
        )

    def test_grid_is_bounded_and_contains_required_families(self):
        subject = load_subject()
        self.assertTrue(hasattr(subject, "candidate_factories"))

        factories = subject.candidate_factories(
            ["f_heat_temp"],
            ["f_heat_temp", "gas_total"],
            ["furnace_no"],
            random_state=42,
            n_jobs=1,
            tree_estimators=20,
        )

        self.assertLessEqual(len(factories), 20)
        self.assertIn("logistic_l2_c0_1_base", factories)
        self.assertTrue(any(name.startswith("elastic_net") for name in factories))
        self.assertTrue(any(name.startswith("extra_trees") for name in factories))
        self.assertTrue(any(name.startswith("random_forest") for name in factories))
        self.assertTrue(any(name.startswith("hist_gb") for name in factories))

    def test_retains_incumbent_below_material_pr_auc_improvement(self):
        subject = load_subject()
        self.assertTrue(hasattr(subject, "select_nextgen"))
        leaderboard = pd.DataFrame(
            [
                {
                    "model": "logistic_l2_c0_1_base",
                    "oof_pr_auc": 0.0500,
                    "oof_roc_auc": 0.60,
                    "worst_time_pr_auc": 0.04,
                },
                {
                    "model": "extra_trees_a",
                    "oof_pr_auc": 0.0549,
                    "oof_roc_auc": 0.70,
                    "worst_time_pr_auc": 0.05,
                },
            ]
        )

        decision = subject.select_nextgen(leaderboard)

        self.assertEqual(decision["recommended_model"], "logistic_l2_c0_1_base")
        self.assertEqual(decision["status"], "RETAIN_SHADOW_INCUMBENT_ALGORITHM")

    def test_frozen_rank_ensemble_score_does_not_depend_on_batch_companions(self):
        subject = load_subject()
        self.assertTrue(hasattr(subject, "FrozenRankAverageEnsemble"))
        first = ScoreColumnPredictor("a")
        second = ScoreColumnPredictor("b")
        ensemble = subject.FrozenRankAverageEnsemble(
            first,
            second,
            np.array([0.1, 0.2, 0.3, 0.4]),
            np.array([0.9, 0.8, 0.7, 0.6]),
            ["a", "b"],
        )
        one = pd.DataFrame({"a": [0.25], "b": [0.75]})
        many = pd.DataFrame({"a": [0.25, 100.0], "b": [0.75, -100.0]})

        alone = ensemble.predict_proba(one)[0, 1]
        accompanied = ensemble.predict_proba(many)[0, 1]

        self.assertEqual(alone, accompanied)

    def test_frozen_percentile_matches_average_rank_for_ties(self):
        subject = load_subject()
        reference = np.array([0.1, 0.2, 0.2, 0.4])

        transformed = subject.frozen_percentile(
            np.array([0.2, 0.3]), reference
        )

        self.assertAlmostEqual(transformed[0], 0.625)
        self.assertAlmostEqual(transformed[1], 0.75)

    def test_rank_ensemble_integrity_detects_reference_score_tampering(self):
        subject = load_subject()
        first = ScoreColumnPredictor("a")
        second = ScoreColumnPredictor("b")
        ensemble = subject.FrozenRankAverageEnsemble(
            first,
            second,
            np.array([0.1, 0.3, 0.9]),
            np.array([0.2, 0.4, 0.8]),
            ["a", "b"],
            integrity_probe=pd.DataFrame({"a": [0.2, 0.8], "b": [0.3, 0.7]}),
        )
        artifact = {
            "predictor": ensemble,
            "ensemble_children": ["first", "second"],
            "ensemble_integrity": subject.ensemble_integrity_metadata(ensemble),
        }

        subject.validate_ensemble_integrity(artifact)
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_path = Path(temp_dir) / "ensemble.joblib"
            joblib.dump(artifact, artifact_path)
            subject.validate_ensemble_integrity(joblib.load(artifact_path))
        ensemble.first_reference_scores[0] = 0.0
        with self.assertRaisesRegex(ValueError, "ensemble integrity"):
            subject.validate_ensemble_integrity(artifact)

    def test_fitted_rank_ensemble_integrity_survives_joblib_round_trip(self):
        subject = load_subject()
        baseline = importlib.import_module("training.steel.train_baseline")
        features = pd.DataFrame(
            {"x": [1.0, 2.0, 3.0, 4.0], "category": ["a", "b", "a", "b"]}
        )
        target = pd.Series([0, 0, 1, 1])
        children = [
            baseline._build_pipeline(
                "logistic_regression",
                ["x"],
                ["category"],
                random_state=seed,
                random_forest_estimators=10,
                n_jobs=1,
            ).fit(features, target)
            for seed in (1, 2)
        ]
        ensemble = subject.FrozenRankAverageEnsemble(
            children[0],
            children[1],
            np.array([0.1, 0.2, 0.3, 0.4]),
            np.array([0.2, 0.3, 0.4, 0.5]),
            ["x", "category"],
            integrity_probe=features,
        )
        artifact = {
            "predictor": ensemble,
            "ensemble_children": ["first", "second"],
            "ensemble_integrity": subject.ensemble_integrity_metadata(ensemble),
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_path = Path(temp_dir) / "ensemble.joblib"
            joblib.dump(artifact, artifact_path)
            reloaded = joblib.load(artifact_path)

        subject.validate_ensemble_integrity(reloaded)


class NextGenIntegrationTest(unittest.TestCase):
    @staticmethod
    def _selection_result(frame: pd.DataFrame, root: Path) -> dict[str, object]:
        subject = load_subject()
        if not hasattr(subject, "run_nextgen"):
            raise AssertionError("run_nextgen is not implemented")
        input_path = root / "modeling.csv"
        output_dir = root / "nextgen"
        frame.to_csv(input_path, index=False, encoding="utf-8-sig")
        return subject.run_nextgen(
            input_path,
            output_dir,
            random_state=7,
            n_jobs=1,
            tree_estimators=20,
            n_splits=3,
            candidate_names=(
                "logistic_l2_c0_1_base",
                "logistic_l2_c0_03_engineered",
            ),
            include_rank_ensemble=False,
        )

    def test_writes_locked_artifacts_and_ignores_all_holdout_values(self):
        original = modeling_frame()
        changed = original.copy()
        holdout = changed["dataset_split"].eq("HOLDOUT")
        changed["f_heat_temp"] = changed["f_heat_temp"].astype("object")
        changed.loc[holdout, "f_heat_temp"] = "HOLDOUT_TYPE_POLLUTION"
        changed.loc[holdout, "judge"] = changed.loc[holdout, "judge"].map(
            {"양품": "불량", "불량": "양품"}
        )
        changed.loc[holdout, "charge_id"] = [f"X{i}" for i in range(holdout.sum())]
        changed.loc[holdout, "hr_coil_id"] = [f"Y{i}" for i in range(holdout.sum())]

        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first_root = Path(first_dir)
            second_root = Path(second_dir)
            first = self._selection_result(original, first_root)
            second = self._selection_result(changed, second_root)
            artifact = joblib.load(first_root / "nextgen" / "nextgen_model.joblib")
            calibrator = joblib.load(
                first_root / "nextgen" / "nextgen_calibrator.joblib"
            )
            metrics = json.loads(
                (first_root / "nextgen" / "nextgen_metrics.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(first["recommendation"], second["recommendation"])
        self.assertEqual(first["leaderboard"], second["leaderboard"])
        self.assertEqual(first["selected_thresholds_by_fpr_cap"], second["selected_thresholds_by_fpr_cap"])
        self.assertFalse(artifact["deployment_eligible"])
        self.assertFalse(artifact["holdout_used_for_selection"])
        self.assertEqual(artifact["required_next_step"], "FUTURE_SHADOW_VALIDATION")
        for field in (
            "selection_oof_scores_sha256",
            "oof_fold_assignments_sha256",
            "development_identity_sha256",
            "feature_schema_sha256",
            "software",
        ):
            self.assertEqual(calibrator[field], artifact[field])
        self.assertEqual(calibrator["class_order"], [0, 1])
        self.assertEqual(calibrator["method"], "platt")
        self.assertFalse(metrics["deployment_eligible"])
        self.assertEqual(metrics["development_rows"], 116)

    def test_non_ensemble_winner_does_not_receive_ensemble_integrity(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "modeling.csv"
            output_dir = root / "nextgen"
            modeling_frame().to_csv(input_path, index=False, encoding="utf-8-sig")

            summary = subject.run_nextgen(
                input_path,
                output_dir,
                random_state=7,
                n_jobs=1,
                tree_estimators=20,
                n_splits=3,
                candidate_names=(
                    "logistic_l2_c0_1_base",
                    "logistic_l2_c0_03_engineered",
                ),
                include_rank_ensemble=True,
                min_pr_auc_improvement=1.0,
            )
            artifact = joblib.load(output_dir / "nextgen_model.joblib")

        self.assertEqual(
            summary["recommendation"]["recommended_model"],
            "logistic_l2_c0_1_base",
        )
        self.assertIsNone(artifact["ensemble_children"])
        self.assertIsNone(artifact["ensemble_integrity"])


if __name__ == "__main__":
    unittest.main()
