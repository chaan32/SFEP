import importlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def load_subject():
    try:
        return importlib.import_module("training.steel.train_baseline")
    except ModuleNotFoundError:
        raise AssertionError("baseline module is not implemented") from None


class FeatureContractTest(unittest.TestCase):
    def test_excludes_identity_time_label_and_all_ap_operating_columns(self):
        subject = load_subject()
        frame = pd.DataFrame(
            {
                "charge_id": ["C1"],
                "slab_no": ["1"],
                "hr_coil_id": ["H1"],
                "cast_date": ["2025-07-01"],
                "f_ext_date": ["2025-07-02"],
                "hr_date": ["2025-07-02"],
                "ap_date": ["2025-07-03"],
                "ap_line_speed": [42.0],
                "judge": ["불량"],
                "dataset_split": ["TRAIN"],
                "f_heat_temp": [1250],
                "f_bfg_per": [40.0],
                "f_cog_per": [30.0],
                "f_ldg_per": [30.0],
                "furnace_no": ["1호기"],
                "dq_any_flag": [False],
            }
        )

        numeric, categorical, excluded = subject.select_feature_columns(frame)

        self.assertEqual(numeric, ["f_heat_temp"])
        self.assertEqual(categorical, ["furnace_no"])
        for column in (
            "charge_id",
            "slab_no",
            "hr_coil_id",
            "cast_date",
            "f_ext_date",
            "hr_date",
            "ap_date",
            "ap_line_speed",
            "judge",
            "dataset_split",
            "f_bfg_per",
            "f_cog_per",
            "f_ldg_per",
            "dq_any_flag",
        ):
            self.assertIn(column, excluded)


class LabelAvailabilityTest(unittest.TestCase):
    def test_uses_only_validation_labels_available_before_holdout_freeze(self):
        subject = load_subject()
        validation = pd.DataFrame(
            {
                "ap_date": ["2025-08-31", "2025-09-01", "2025-09-02"],
                "judge": ["양품", "불량", "불량"],
            }
        )

        mask = subject.validation_as_of_mask(validation, "2025-09-01")

        self.assertEqual(mask.tolist(), [True, False, False])


class FixedProtocolCutoffIntegrationTest(unittest.TestCase):
    def test_does_not_move_validation_cutoff_when_holdout_starts_late(self):
        subject = load_subject()
        rows = []
        split_specs = {
            "TRAIN": ("2025-07-15", 24),
            "VALIDATION": ("2025-08-15", 18),
            "HOLDOUT": ("2025-09-10", 18),
        }
        sequence = 0
        for split_name, (hr_date, count) in split_specs.items():
            for index in range(count):
                ap_date = hr_date
                if split_name == "VALIDATION" and index >= 14:
                    ap_date = "2025-09-05"
                is_bad = index % 4 == 0
                rows.append(
                    {
                        "charge_id": f"C{sequence}",
                        "slab_no": "1",
                        "hr_coil_id": f"H{sequence}",
                        "cast_date": hr_date,
                        "f_ext_date": hr_date,
                        "hr_date": hr_date,
                        "ap_date": ap_date,
                        "judge": "불량" if is_bad else "양품",
                        "dataset_split": split_name,
                        "signal": float(index + (3 if is_bad else 0)),
                    }
                )
                sequence += 1
        frame = pd.DataFrame(rows)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "modeling.csv"
            frame.to_csv(input_path, index=False, encoding="utf-8-sig")

            summary = subject.run_baseline(
                input_path,
                root / "baseline",
                random_state=7,
                random_forest_estimators=20,
                n_jobs=1,
            )

            self.assertEqual(summary["selection_protocol"]["freeze_date"], "2025-09-01")
            self.assertEqual(summary["selection_protocol"]["validation_selection_rows"], 14)

            with self.assertRaisesRegex(ValueError, "immutable.*2025-09-01"):
                subject.run_baseline(
                    input_path,
                    root / "unsafe-baseline",
                    random_state=7,
                    random_forest_estimators=20,
                    n_jobs=1,
                    validation_freeze_date="2025-09-10",
                )


class ThresholdAndMetricsTest(unittest.TestCase):
    def test_selects_validation_threshold_that_maximizes_f2(self):
        subject = load_subject()
        threshold, metrics = subject.select_f2_threshold(
            pd.Series([0, 0, 1, 1]),
            pd.Series([0.10, 0.40, 0.45, 0.90]),
        )

        self.assertAlmostEqual(threshold, 0.45)
        self.assertAlmostEqual(metrics["f2"], 1.0)
        self.assertEqual(metrics["confusion_matrix"], {"tn": 2, "fp": 0, "fn": 0, "tp": 2})

    def test_reports_false_alarm_and_alert_rates_from_fixed_threshold(self):
        subject = load_subject()
        metrics = subject.binary_metrics(
            pd.Series([0, 0, 1, 1]),
            pd.Series([0.10, 0.70, 0.40, 0.90]),
            threshold=0.50,
        )

        self.assertEqual(metrics["confusion_matrix"], {"tn": 1, "fp": 1, "fn": 1, "tp": 1})
        self.assertAlmostEqual(metrics["precision"], 0.5)
        self.assertAlmostEqual(metrics["recall"], 0.5)
        self.assertAlmostEqual(metrics["false_alarm_rate"], 0.5)
        self.assertAlmostEqual(metrics["alert_rate"], 0.5)


class ModelGuardrailTest(unittest.TestCase):
    def test_random_forest_is_bounded_for_the_small_positive_training_sample(self):
        subject = load_subject()
        pipeline = subject._build_pipeline(
            "random_forest",
            ["x"],
            [],
            random_state=42,
            random_forest_estimators=20,
            n_jobs=1,
        )
        classifier = pipeline.named_steps["classifier"]

        self.assertEqual(classifier.max_depth, 8)
        self.assertEqual(classifier.min_samples_leaf, 20)
        self.assertEqual(classifier.max_features, "sqrt")


class BaselineIntegrationTest(unittest.TestCase):
    def test_trains_on_train_selects_on_validation_and_writes_holdout_once_outputs(self):
        subject = load_subject()
        rows = []
        split_specs = {
            "TRAIN": ("2025-07-01", 24),
            "VALIDATION": ("2025-08-01", 18),
            "HOLDOUT": ("2025-09-01", 18),
        }
        sequence = 0
        for split_name, (date, count) in split_specs.items():
            for index in range(count):
                is_bad = index % 4 == 0
                rows.append(
                    {
                        "charge_id": f"C{sequence}",
                        "slab_no": "1",
                        "hr_coil_id": f"H{sequence}",
                        "cast_date": date,
                        "f_ext_date": date,
                        "hr_date": date,
                        "ap_date": date,
                        "judge": "불량" if is_bad else "양품",
                        "dataset_split": split_name,
                        "f_heat_temp": 1300 + (20 if is_bad else 0) + index % 3,
                        "furnace_no": "1호기" if index % 2 else "2호기",
                        "dq_any_flag": False,
                    }
                )
                sequence += 1
        frame = pd.DataFrame(rows)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "modeling.csv"
            output_dir = root / "baseline"
            frame.to_csv(input_path, index=False, encoding="utf-8-sig")

            summary = subject.run_baseline(
                input_path,
                output_dir,
                random_state=7,
                random_forest_estimators=20,
                n_jobs=1,
            )

            expected_files = {
                "baseline_metrics.json",
                "baseline_model.joblib",
                "feature_schema.json",
                "holdout_predictions.csv",
                "top_feature_associations.csv",
                "validation_model_comparison.csv",
                "validation_predictions.csv",
            }
            self.assertEqual({path.name for path in output_dir.iterdir()}, expected_files)
            self.assertIn(summary["selected_model"], {"logistic_regression", "random_forest"})
            self.assertEqual(summary["selection_protocol"]["model_selection_split"], "VALIDATION")
            self.assertEqual(summary["selection_protocol"]["threshold_selection_split"], "VALIDATION")
            self.assertEqual(summary["selection_protocol"]["final_evaluation_split"], "HOLDOUT")
            self.assertEqual(summary["selection_protocol"]["label_availability_proxy"], "ap_date")
            self.assertEqual(summary["selection_protocol"]["freeze_date"], "2025-09-01")
            self.assertEqual(summary["selection_protocol"]["validation_selection_rows"], 18)
            self.assertEqual(summary["row_counts"], {"TRAIN": 24, "VALIDATION": 18, "HOLDOUT": 18})
            self.assertEqual(summary["integrity"]["split_charge_overlap"], 0)
            self.assertNotIn("ap_date", summary["feature_schema"]["included"])
            self.assertNotIn("ap_line_speed", summary["feature_schema"]["included"])

            saved = json.loads((output_dir / "baseline_metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, summary)
            holdout = pd.read_csv(output_dir / "holdout_predictions.csv", encoding="utf-8-sig")
            validation = pd.read_csv(output_dir / "validation_predictions.csv", encoding="utf-8-sig")
            self.assertEqual(len(holdout), 18)
            self.assertEqual(len(validation), 18)
            self.assertTrue({"y_true", "risk_score", "predicted_label"}.issubset(holdout.columns))


if __name__ == "__main__":
    unittest.main()
