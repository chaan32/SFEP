import importlib
import math
import unittest
import warnings

import numpy as np
import pandas as pd


def load_error_subject():
    try:
        return importlib.import_module("training.steel.diagnostics.error_analysis")
    except ModuleNotFoundError:
        raise AssertionError("error analysis module is not implemented") from None


def load_drift_subject():
    try:
        return importlib.import_module("training.steel.diagnostics.drift")
    except ModuleNotFoundError:
        raise AssertionError("drift module is not implemented") from None


class ErrorAnalysisTest(unittest.TestCase):
    def test_attaches_the_four_confusion_outcomes(self):
        subject = load_error_subject()
        frame = pd.DataFrame({"coil": ["A", "B", "C", "D"]})

        result = subject.attach_outcomes(
            frame,
            y_true=pd.Series([0, 0, 1, 1]),
            y_pred=pd.Series([0, 1, 0, 1]),
        )

        self.assertEqual(result["outcome"].tolist(), ["TN", "FP", "FN", "TP"])

    def test_categorical_slices_report_denominators_and_error_rates(self):
        subject = load_error_subject()
        frame = pd.DataFrame(
            {
                "line": ["A", "A", "A", "B", "B", "B"],
                "outcome": ["TN", "FP", "FN", "TP", "FP", "FN"],
                "y_true": [0, 0, 1, 1, 0, 1],
                "y_pred": [0, 1, 0, 1, 1, 0],
                "risk_score": [0.1, 0.8, 0.2, 0.9, 0.7, 0.3],
            }
        )

        result = subject.categorical_error_slices(frame, ["line"], min_support=1)
        rows = result.set_index("slice_value").to_dict(orient="index")

        self.assertEqual(rows["A"]["n"], 3)
        self.assertEqual(
            {key: rows["A"][key] for key in ("tn", "fp", "fn", "tp")},
            {"tn": 1, "fp": 1, "fn": 1, "tp": 0},
        )
        self.assertAlmostEqual(rows["A"]["precision"], 0.0)
        self.assertAlmostEqual(rows["A"]["recall"], 0.0)
        self.assertAlmostEqual(rows["A"]["false_alarm_rate"], 0.5)
        self.assertEqual(
            {key: rows["B"][key] for key in ("tn", "fp", "fn", "tp")},
            {"tn": 0, "fp": 1, "fn": 1, "tp": 1},
        )
        self.assertAlmostEqual(rows["B"]["precision"], 0.5)
        self.assertAlmostEqual(rows["B"]["recall"], 0.5)

        screened = subject.add_complement_statistics(
            result,
            frame,
            fpr_min_negative=1,
            fpr_min_fp=1,
            fnr_min_positive=1,
            fnr_min_fn=1,
            fnr_min_tp=0,
            fpr_material_delta=0.0,
            fnr_material_delta=0.0,
        ).set_index("slice_value")
        self.assertAlmostEqual(screened.loc["A", "complement_false_alarm_rate"], 1.0)
        self.assertAlmostEqual(screened.loc["A", "false_alarm_rate_delta"], -0.5)
        self.assertTrue(screened["fpr_bh_qvalue"].between(0, 1).all())

    def test_numeric_slices_use_train_edges_not_holdout_quantiles(self):
        subject = load_error_subject()
        reference = pd.DataFrame({"temperature": [0.0, 1.0, 2.0, 3.0]})
        holdout = pd.DataFrame(
            {
                "temperature": [0.0, 1.0, 2.0, 3.0],
                "outcome": ["TN", "FP", "FN", "TP"],
                "risk_score": [0.1, 0.8, 0.2, 0.9],
            }
        )

        result = subject.numeric_error_slices(
            reference,
            holdout,
            ["temperature"],
            bins=2,
            min_support=1,
        )

        self.assertEqual(result["n"].tolist(), [2, 2])
        self.assertEqual(result["fp"].tolist(), [1, 0])
        self.assertEqual(result["fn"].tolist(), [0, 1])


class DriftMetricTest(unittest.TestCase):
    def test_population_stability_index_matches_hand_calculation(self):
        subject = load_drift_subject()
        reference = np.array([0.5, 0.5])
        current = np.array([0.25, 0.75])
        expected = (-0.25 * math.log(0.5)) + (0.25 * math.log(1.5))

        actual = subject.psi_from_proportions(reference, current)

        self.assertAlmostEqual(actual, expected, places=12)

    def test_categorical_drift_reports_new_category_rate(self):
        subject = load_drift_subject()
        reference = pd.Series(["A", "A", "B", "B"])
        current = pd.Series(["A", "C", "C", "C"])

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            summary, levels = subject.categorical_drift(
                "line",
                reference,
                current,
                comparison_split="HOLDOUT",
            )

        self.assertEqual(summary["new_category_count"], 1)
        self.assertAlmostEqual(summary["new_category_row_rate"], 0.75)
        self.assertGreater(summary["jensen_shannon_divergence"], 0.0)
        level_c = levels.set_index("category").loc["C"]
        self.assertTrue(bool(level_c["is_new_category"]))
        self.assertAlmostEqual(level_c["current_rate"], 0.75)

    def test_numeric_drift_uses_reference_bins_and_detects_shift(self):
        subject = load_drift_subject()
        reference = pd.Series(range(100), dtype=float)
        current = pd.Series(range(100, 200), dtype=float)

        result = subject.numeric_drift(
            "temperature",
            reference,
            current,
            comparison_split="HOLDOUT",
            bins=10,
        )

        self.assertAlmostEqual(result["ks_statistic"], 1.0)
        self.assertGreater(result["psi"], 1.0)
        self.assertEqual(result["reference_missing_rate"], 0.0)
        self.assertEqual(result["current_missing_rate"], 0.0)

    def test_numeric_drift_does_not_collapse_a_structural_zero_reference(self):
        subject = load_drift_subject()
        reference = pd.Series([0.0] * 96 + [1.0, 10.0, 20.0, 30.0])
        current = pd.Series([0.0] * 50 + [1.0] * 50)

        result = subject.numeric_drift(
            "fuel",
            reference,
            current,
            comparison_split="HOLDOUT",
            bins=10,
        )

        expected = subject.psi_from_counts(
            np.array([96, 4]),
            np.array([50, 50]),
        )

        self.assertEqual(result["reference_bin_count"], 2)
        self.assertAlmostEqual(result["psi"], expected)

    def test_numeric_drift_assigns_quantile_boundaries_to_right_closed_bins(self):
        subject = load_drift_subject()

        result = subject.numeric_drift(
            "discrete_signal",
            pd.Series([0.0, 1.0, 1.0, 2.0]),
            pd.Series([0.0, 0.0, 1.0, 2.0]),
            comparison_split="HOLDOUT",
            bins=2,
        )

        self.assertAlmostEqual(result["psi"], 0.0)

    def test_structural_zero_psi_uses_zero_versus_nonzero_bins(self):
        subject = load_drift_subject()

        result = subject.numeric_drift(
            "gas_flow",
            pd.Series([0.0, 0.0, 0.0, 0.0]),
            pd.Series([0.0, 0.0, 2.0, 3.0]),
            comparison_split="HOLDOUT",
            bins=10,
        )
        expected = subject.psi_from_counts(
            np.array([4, 0]),
            np.array([2, 2]),
        )

        self.assertEqual(result["reference_bin_count"], 2)
        self.assertAlmostEqual(result["psi"], expected)


if __name__ == "__main__":
    unittest.main()
