import importlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from training.steel.train_baseline import run_baseline


def load_subject():
    try:
        return importlib.import_module("training.steel.run_diagnostics")
    except ModuleNotFoundError:
        raise AssertionError("diagnostics runner is not implemented") from None


class DiagnosticsIntegrationTest(unittest.TestCase):
    def test_generates_error_drift_calibration_and_shap_artifacts(self):
        subject = load_subject()
        rows = []
        sequence = 0
        for split_name, date, count in (
            ("TRAIN", "2025-07-15", 32),
            ("VALIDATION", "2025-08-15", 32),
            ("HOLDOUT", "2025-09-15", 32),
        ):
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
                        "hr_width": 1200 + index % 4,
                        "furnace_no": "1호기" if index % 2 else "2호기",
                        "dq_any_flag": False,
                    }
                )
                sequence += 1
        frame = pd.DataFrame(rows)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            modeling_path = root / "modeling.csv"
            baseline_dir = root / "baseline"
            output_dir = root / "diagnostics"
            frame.to_csv(modeling_path, index=False, encoding="utf-8-sig")
            run_baseline(
                modeling_path,
                baseline_dir,
                random_state=7,
                random_forest_estimators=10,
                n_jobs=1,
            )

            summary = subject.run_diagnostics(
                modeling_path,
                baseline_dir,
                output_dir,
                calibration_folds=2,
                min_slice_support=1,
                top_shap_per_coil=2,
                create_charts=False,
            )

            expected_files = {
                "diagnostic_summary.json",
                "diagnostic_report.md",
                "holdout_error_cohorts.csv",
                "categorical_error_slices.csv",
                "numeric_error_deciles.csv",
                "numeric_drift.csv",
                "categorical_drift.csv",
                "categorical_drift_levels.csv",
                "label_drift.csv",
                "calibration_metrics.csv",
                "calibration_bins.csv",
                "calibrator.joblib",
                "holdout_calibrated_predictions.csv",
                "feature_equipment_registry.csv",
                "shap_global_features.csv",
                "shap_top_per_coil.csv",
                "shap_equipment_candidates.csv",
            }
            self.assertEqual({path.name for path in output_dir.iterdir()}, expected_files)
            self.assertEqual(summary["analysis_version"], "steel-diagnostics-v0.1")
            self.assertTrue(
                {"python", "scipy", "matplotlib", "joblib", "chart_font"}.issubset(
                    summary["software"]
                )
            )
            self.assertEqual(sum(summary["holdout_outcomes"].values()), 32)
            self.assertEqual(summary["cohorts"]["VALIDATION_AS_OF"], 32)
            self.assertEqual(summary["cohorts"]["HOLDOUT"], 32)
            self.assertEqual(
                summary["data_quality"]["model_feature_missing_rows"]["HOLDOUT"],
                0,
            )
            self.assertIn(summary["calibration"]["selected_method"], {"raw", "platt", "isotonic"})
            self.assertIn(
                "constant_validation_prevalence_holdout_brier",
                summary["calibration"],
            )
            self.assertLess(summary["shap"]["max_additivity_error"], 1e-10)
            self.assertEqual(summary["shap"]["method"], "analytical_linear_logit_shap")

            saved = json.loads((output_dir / "diagnostic_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, summary)
            errors = pd.read_csv(output_dir / "holdout_error_cohorts.csv", encoding="utf-8-sig")
            calibrated = pd.read_csv(
                output_dir / "holdout_calibrated_predictions.csv", encoding="utf-8-sig"
            )
            registry = pd.read_csv(
                output_dir / "feature_equipment_registry.csv", encoding="utf-8-sig"
            )
            shap_global = pd.read_csv(
                output_dir / "shap_global_features.csv", encoding="utf-8-sig"
            )
            shap_local = pd.read_csv(
                output_dir / "shap_top_per_coil.csv", encoding="utf-8-sig"
            )
            report = (output_dir / "diagnostic_report.md").read_text(encoding="utf-8")
            self.assertEqual(len(errors), 32)
            self.assertEqual(len(calibrated), 32)
            self.assertTrue(calibrated["calibrated_probability"].between(0, 1).all())
            self.assertEqual(set(registry["feature"]), set(summary["feature_schema"]["included"]))
            self.assertEqual(set(shap_global["source_feature"]), set(registry["feature"]))
            self.assertTrue(
                {"imputed_flag", "unknown_category_flag"}.issubset(shap_local.columns)
            )
            self.assertIn("VALIDATION_AS_OF", report)
            self.assertIn("positive_rate_delta_pp", report)


if __name__ == "__main__":
    unittest.main()
