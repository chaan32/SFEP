import importlib
import copy
import json
import math
import tempfile
import unittest
import warnings
from pathlib import Path

import pandas as pd
import joblib


def load_subject():
    try:
        return importlib.import_module("training.steel.train_challenger")
    except ModuleNotFoundError:
        raise AssertionError("challenger module is not implemented") from None


class AlertCapacityThresholdTest(unittest.TestCase):
    def test_selects_highest_recall_threshold_within_false_alarm_cap(self):
        subject = load_subject()

        threshold, metrics = subject.select_threshold_at_fpr_cap(
            pd.Series([0, 0, 0, 0, 1, 1]),
            pd.Series([0.10, 0.20, 0.60, 0.90, 0.65, 0.80]),
            false_alarm_cap=0.25,
        )

        self.assertAlmostEqual(threshold, 0.65)
        self.assertAlmostEqual(metrics["false_alarm_rate"], 0.25)
        self.assertAlmostEqual(metrics["recall"], 1.0)
        self.assertEqual(metrics["confusion_matrix"], {"tn": 3, "fp": 1, "fn": 0, "tp": 2})

    def test_rejects_invalid_false_alarm_cap(self):
        subject = load_subject()

        with self.assertRaisesRegex(ValueError, "false_alarm_cap"):
            subject.select_threshold_at_fpr_cap(
                pd.Series([0, 1]),
                pd.Series([0.10, 0.90]),
                false_alarm_cap=1.1,
            )

    def test_uses_finite_no_alert_threshold_for_constant_scores(self):
        subject = load_subject()

        threshold, metrics = subject.select_threshold_at_fpr_cap(
            pd.Series([0, 0, 1, 1]),
            pd.Series([0.50, 0.50, 0.50, 0.50]),
            false_alarm_cap=0.0,
        )

        self.assertTrue(math.isfinite(threshold))
        self.assertGreater(threshold, 0.50)
        self.assertEqual(metrics["confusion_matrix"], {"tn": 2, "fp": 0, "fn": 2, "tp": 0})


class ValidationOnlyRecommendationTest(unittest.TestCase):
    def test_adopts_challenger_only_when_validation_pr_auc_improves(self):
        subject = load_subject()
        validation_leaderboard = pd.DataFrame(
            [
                {"model": "incumbent_logistic", "validation_pr_auc": 0.10, "validation_roc_auc": 0.60},
                {"model": "challenger_a", "validation_pr_auc": 0.12, "validation_roc_auc": 0.58},
                {"model": "challenger_b", "validation_pr_auc": 0.11, "validation_roc_auc": 0.70},
            ]
        )

        recommendation = subject.select_recommended_model(
            validation_leaderboard,
            incumbent_model="incumbent_logistic",
            min_pr_auc_improvement=0.0,
        )

        self.assertEqual(recommendation["status"], "ADOPT_CHALLENGER")
        self.assertEqual(recommendation["recommended_model"], "challenger_a")
        self.assertAlmostEqual(recommendation["validation_pr_auc_improvement"], 0.02)

    def test_retains_incumbent_when_challenger_only_ties(self):
        subject = load_subject()
        validation_leaderboard = pd.DataFrame(
            [
                {"model": "incumbent_logistic", "validation_pr_auc": 0.10, "validation_roc_auc": 0.60},
                {"model": "challenger_a", "validation_pr_auc": 0.10, "validation_roc_auc": 0.70},
            ]
        )

        recommendation = subject.select_recommended_model(
            validation_leaderboard,
            incumbent_model="incumbent_logistic",
            min_pr_auc_improvement=0.0,
        )

        self.assertEqual(recommendation["status"], "RETAIN_INCUMBENT")
        self.assertEqual(recommendation["recommended_model"], "incumbent_logistic")


class CandidateCompatibilityTest(unittest.TestCase):
    def test_logistic_candidates_do_not_use_deprecated_penalty_argument(self):
        subject = load_subject()
        features = pd.DataFrame({"signal": [0.0, 1.0, 2.0, 3.0]})
        target = pd.Series([0, 0, 1, 1])
        pipeline = subject._build_candidate_pipeline(
            "logistic_l1_c1",
            ["signal"],
            [],
            random_state=7,
            random_forest_estimators=20,
            n_jobs=1,
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            pipeline.fit(features, target)

        deprecated = [
            warning
            for warning in caught
            if issubclass(warning.category, FutureWarning)
            and "penalty" in str(warning.message)
        ]
        self.assertEqual(deprecated, [])


class BaselineLineageTest(unittest.TestCase):
    def test_rejects_mismatched_cohort_model_schema_and_positive_class(self):
        subject = load_subject()
        baseline = importlib.import_module("training.steel.train_baseline")
        frame = ChallengerIntegrationTest._modeling_frame()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "modeling.csv"
            baseline_dir = root / "baseline"
            frame.to_csv(input_path, index=False, encoding="utf-8-sig")
            baseline.run_baseline(
                input_path,
                baseline_dir,
                random_state=7,
                random_forest_estimators=20,
                n_jobs=1,
            )
            artifact = joblib.load(baseline_dir / "baseline_model.joblib")
            metrics = json.loads(
                (baseline_dir / "baseline_metrics.json").read_text(encoding="utf-8")
            )
            cohort_sha256 = subject._sha256(input_path)
            expected_feature_schema = subject._expected_feature_schema(frame)

            self.assertTrue(hasattr(subject, "_validate_baseline_lineage"))
            subject._validate_baseline_lineage(
                artifact,
                metrics,
                cohort_sha256,
                expected_feature_schema=expected_feature_schema,
            )

            corruptions = []
            wrong_cohort = copy.deepcopy(metrics)
            wrong_cohort["source"]["sha256"] = "0" * 64
            corruptions.append(("cohort SHA-256", artifact, wrong_cohort))
            wrong_model = copy.deepcopy(metrics)
            wrong_model["selected_model"] = "random_forest"
            corruptions.append(("selected model", artifact, wrong_model))
            wrong_schema_artifact = copy.deepcopy(artifact)
            wrong_schema_artifact["feature_schema"] = copy.deepcopy(artifact["feature_schema"])
            wrong_schema_artifact["feature_schema"]["included"] = []
            corruptions.append(("feature schema", wrong_schema_artifact, metrics))
            wrong_label_artifact = copy.deepcopy(artifact)
            wrong_label_artifact["positive_label"] = "양품"
            corruptions.append(("positive label", wrong_label_artifact, metrics))
            jointly_wrong_version_artifact = copy.deepcopy(artifact)
            jointly_wrong_version_metrics = copy.deepcopy(metrics)
            jointly_wrong_version_artifact["baseline_version"] = "forged-v999"
            jointly_wrong_version_metrics["baseline_version"] = "forged-v999"
            corruptions.append(
                (
                    "expected baseline version",
                    jointly_wrong_version_artifact,
                    jointly_wrong_version_metrics,
                )
            )
            jointly_wrong_schema_artifact = copy.deepcopy(artifact)
            jointly_wrong_schema_metrics = copy.deepcopy(metrics)
            jointly_wrong_schema_artifact["feature_schema"] = copy.deepcopy(
                artifact["feature_schema"]
            )
            jointly_wrong_schema_metrics["feature_schema"] = copy.deepcopy(
                metrics["feature_schema"]
            )
            jointly_wrong_schema_artifact["feature_schema"]["included"] = []
            jointly_wrong_schema_metrics["feature_schema"]["included"] = []
            corruptions.append(
                (
                    "expected feature schema",
                    jointly_wrong_schema_artifact,
                    jointly_wrong_schema_metrics,
                )
            )

            for message, candidate_artifact, candidate_metrics in corruptions:
                with self.subTest(message=message):
                    with self.assertRaisesRegex(ValueError, message):
                        subject._validate_baseline_lineage(
                            candidate_artifact,
                            candidate_metrics,
                            cohort_sha256,
                            expected_feature_schema=expected_feature_schema,
                        )


class ChallengerIntegrationTest(unittest.TestCase):
    @staticmethod
    def _modeling_frame() -> pd.DataFrame:
        rows = []
        split_specs = {
            "TRAIN": ("2025-07-15", "2025-07-20", 48),
            "VALIDATION": ("2025-08-15", "2025-08-20", 36),
            "HOLDOUT": ("2025-09-10", "2025-09-15", 36),
        }
        sequence = 0
        for split_name, (hr_date, ap_date, count) in split_specs.items():
            for index in range(count):
                is_bad = index % 4 == 0
                rows.append(
                    {
                        "charge_id": f"C{sequence}",
                        "slab_no": "1",
                        "hr_coil_id": f"H{sequence}",
                        "cast_date": hr_date,
                        "f_ext_date": hr_date,
                        "hr_date": hr_date,
                        "ap_date": (
                            "2025-09-05"
                            if split_name == "VALIDATION" and index >= 32
                            else ap_date
                        ),
                        "judge": "불량" if is_bad else "양품",
                        "dataset_split": split_name,
                        "signal": float(index % 5 + (4 if is_bad else 0)),
                        "furnace_no": "1호기" if index % 2 else "2호기",
                        "dq_any_flag": False,
                    }
                )
                sequence += 1
        return pd.DataFrame(rows)

    def test_writes_validation_selected_and_historical_holdout_outputs(self):
        subject = load_subject()
        baseline = importlib.import_module("training.steel.train_baseline")
        frame = self._modeling_frame()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "modeling.csv"
            baseline_dir = root / "baseline"
            output_dir = root / "challenger"
            frame.to_csv(input_path, index=False, encoding="utf-8-sig")
            baseline.run_baseline(
                input_path,
                baseline_dir,
                random_state=7,
                random_forest_estimators=20,
                n_jobs=1,
            )

            summary = subject.run_challenger(
                input_path,
                baseline_dir,
                output_dir,
                random_state=7,
                random_forest_estimators=20,
                n_jobs=1,
                candidate_names=("logistic_l2_c0_1", "hist_gradient_boosting"),
                alert_fpr_caps=(0.10, 0.20),
                policy_fpr_cap=0.20,
            )

            expected_files = {
                "challenger_metrics.json",
                "challenger_report.md",
                "historical_holdout_comparison.csv",
                "historical_holdout_predictions.csv",
                "recommended_model.joblib",
                "validation_alert_capacity.csv",
                "validation_model_leaderboard.csv",
                "validation_recommended_predictions.csv",
            }
            self.assertEqual({path.name for path in output_dir.iterdir()}, expected_files)
            self.assertEqual(summary["selection_protocol"]["training_split"], "TRAIN")
            self.assertEqual(summary["selection_protocol"]["selection_split"], "VALIDATION_AS_OF")
            self.assertEqual(summary["selection_protocol"]["freeze_date"], "2025-09-01")
            self.assertEqual(summary["row_counts"]["VALIDATION_AS_OF"], 32)
            self.assertFalse(summary["selection_protocol"]["holdout_used_for_selection"])
            self.assertTrue(
                summary["selection_protocol"]["recommendation_locked_before_holdout_evaluation"]
            )
            self.assertIn("deployment_gate", summary)
            self.assertEqual(
                summary["deployment_gate"]["status"],
                "FUTURE_SHADOW_VALIDATION_REQUIRED",
            )
            self.assertFalse(summary["deployment_gate"]["deployment_eligible"])
            self.assertEqual(summary["selection_protocol"]["alert_fpr_caps"], [0.10, 0.20])
            self.assertIn(
                summary["recommendation"]["recommended_model"],
                {"incumbent_logistic", "logistic_l2_c0_1", "hist_gradient_boosting"},
            )

            saved = json.loads(
                (output_dir / "challenger_metrics.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved, summary)
            artifact = joblib.load(output_dir / "recommended_model.joblib")
            self.assertFalse(artifact["deployment_eligible"])
            self.assertEqual(
                artifact["required_next_step"],
                "FUTURE_SHADOW_VALIDATION",
            )
            report = (output_dir / "challenger_report.md").read_text(encoding="utf-8")
            self.assertIn("배포 가능 모델이 아닙니다", report)
            self.assertIn("미래 Shadow 검증", report)
            validation_capacity = pd.read_csv(
                output_dir / "validation_alert_capacity.csv", encoding="utf-8-sig"
            )
            holdout_comparison = pd.read_csv(
                output_dir / "historical_holdout_comparison.csv", encoding="utf-8-sig"
            )
            self.assertEqual(set(validation_capacity["false_alarm_cap"]), {0.10, 0.20})
            self.assertEqual(set(holdout_comparison["threshold_source"]), {"VALIDATION_AS_OF"})
            self.assertTrue((validation_capacity["actual_false_alarm_rate"] <= validation_capacity["false_alarm_cap"] + 1e-12).all())

            with self.assertRaisesRegex(ValueError, "immutable.*2025-09-01"):
                subject.run_challenger(
                    input_path,
                    baseline_dir,
                    root / "unsafe-challenger",
                    random_state=7,
                    random_forest_estimators=20,
                    n_jobs=1,
                    candidate_names=("logistic_l2_c0_1",),
                    alert_fpr_caps=(0.20,),
                    policy_fpr_cap=0.20,
                    validation_freeze_date="2025-09-10",
                )


class HoldoutIndependenceIntegrationTest(unittest.TestCase):
    @staticmethod
    def _selection_result(frame: pd.DataFrame, root: Path) -> dict[str, object]:
        baseline = importlib.import_module("training.steel.train_baseline")
        subject = load_subject()
        input_path = root / "modeling.csv"
        baseline_dir = root / "baseline"
        output_dir = root / "challenger"
        frame.to_csv(input_path, index=False, encoding="utf-8-sig")
        baseline.run_baseline(
            input_path,
            baseline_dir,
            random_state=7,
            random_forest_estimators=20,
            n_jobs=1,
        )
        return subject.run_challenger(
            input_path,
            baseline_dir,
            output_dir,
            random_state=7,
            random_forest_estimators=20,
            n_jobs=1,
            candidate_names=("logistic_l2_c0_1",),
            alert_fpr_caps=(0.15,),
            policy_fpr_cap=0.15,
        )

    def test_model_and_threshold_selection_ignore_all_holdout_values(self):
        original = ChallengerIntegrationTest._modeling_frame()
        changed = original.copy()
        holdout = changed["dataset_split"].eq("HOLDOUT")
        changed.loc[holdout, "charge_id"] = [f"X{index}" for index in range(holdout.sum())]
        changed.loc[holdout, "hr_coil_id"] = [f"Y{index}" for index in range(holdout.sum())]
        changed.loc[holdout, "hr_date"] = "2025-10-10"
        changed.loc[holdout, "ap_date"] = "2025-10-20"
        changed["signal"] = changed["signal"].astype("object")
        changed.loc[holdout, "signal"] = "HOLDOUT_TYPE_POLLUTION"
        changed.loc[holdout, "judge"] = changed.loc[holdout, "judge"].map(
            {"양품": "불량", "불량": "양품"}
        )

        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = self._selection_result(original, Path(first_dir))
            second = self._selection_result(changed, Path(second_dir))

        self.assertEqual(first["recommendation"], second["recommendation"])
        self.assertEqual(
            first["validation_model_leaderboard"],
            second["validation_model_leaderboard"],
        )
        self.assertEqual(
            first["policy_operating_point"]["validation_as_of"],
            second["policy_operating_point"]["validation_as_of"],
        )


if __name__ == "__main__":
    unittest.main()
