import importlib
import unittest

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression


def load_calibration_subject():
    try:
        return importlib.import_module("training.steel.diagnostics.calibration")
    except ModuleNotFoundError:
        raise AssertionError("calibration module is not implemented") from None


def load_shap_subject():
    try:
        return importlib.import_module("training.steel.diagnostics.linear_shap")
    except ModuleNotFoundError:
        raise AssertionError("linear SHAP module is not implemented") from None


def load_registry_subject():
    try:
        return importlib.import_module("training.steel.diagnostics.registry")
    except ModuleNotFoundError:
        raise AssertionError("feature registry module is not implemented") from None


class CalibrationMetricTest(unittest.TestCase):
    def test_adaptive_ece_matches_hand_calculated_two_bins(self):
        subject = load_calibration_subject()
        y_true = np.array([0, 0, 1, 1])
        probability = np.array([0.1, 0.2, 0.8, 0.9])

        bins = subject.calibration_bin_table(
            y_true,
            probability,
            split="VALIDATION_OOF",
            variant="platt",
            bins=2,
        )
        ece = subject.expected_calibration_error(bins)

        self.assertEqual(bins["count"].tolist(), [2, 2])
        self.assertAlmostEqual(ece, 0.15)

    def test_adaptive_bins_do_not_split_equal_probability_ties(self):
        subject = load_calibration_subject()
        y_true = np.array([0, 1, 0, 1, 1, 0])
        probability = np.array([0.1, 0.1, 0.1, 0.8, 0.8, 0.8])

        bins = subject.calibration_bin_table(
            y_true,
            probability,
            split="VALIDATION_OOF",
            variant="isotonic",
            bins=4,
        )

        self.assertEqual(bins["count"].tolist(), [3, 3])
        self.assertEqual(bins["min_probability"].tolist(), [0.1, 0.8])
        self.assertEqual(bins["max_probability"].tolist(), [0.1, 0.8])

    def test_calibration_metrics_include_fixed_and_tie_preserving_ece(self):
        subject = load_calibration_subject()

        metrics, _ = subject.calibration_metrics(
            np.array([0, 0, 1, 1]),
            np.array([0.1, 0.2, 0.8, 0.9]),
            split="HOLDOUT",
            variant="platt",
            bins=2,
        )

        self.assertAlmostEqual(metrics["ece"], 0.15)
        self.assertAlmostEqual(metrics["fixed_width_ece"], 0.15)

    def test_method_selection_uses_brier_then_log_loss_and_prefers_platt_on_tie(self):
        subject = load_calibration_subject()
        metrics = pd.DataFrame(
            [
                {"variant": "raw", "brier_score": 0.20, "log_loss": 0.50, "ece": 0.20},
                {"variant": "isotonic", "brier_score": 0.10, "log_loss": 0.40, "ece": 0.05},
                {"variant": "platt", "brier_score": 0.10, "log_loss": 0.30, "ece": 0.08},
            ]
        )

        selected = subject.choose_calibration_method(metrics)

        self.assertEqual(selected, "platt")

    def test_method_selection_treats_tiny_brier_difference_as_a_stability_tie(self):
        subject = load_calibration_subject()
        metrics = pd.DataFrame(
            [
                {"variant": "isotonic", "brier_score": 0.10000, "log_loss": 0.40, "ece": 0.04},
                {"variant": "platt", "brier_score": 0.10004, "log_loss": 0.30, "ece": 0.05},
            ]
        )

        selected = subject.choose_calibration_method(metrics, brier_tolerance=1e-4)

        self.assertEqual(selected, "platt")

    def test_grouped_cross_validation_returns_out_of_fold_probabilities(self):
        subject = load_calibration_subject()
        raw_score = np.linspace(0.05, 0.95, 40)
        y_true = np.array([0, 1] * 20)
        groups = np.array([f"C{index // 2}" for index in range(40)])

        result = subject.cross_validated_calibration(
            raw_score,
            y_true,
            groups,
            n_splits=4,
            random_state=7,
        )

        self.assertEqual(set(result.probabilities), {"raw", "platt", "isotonic"})
        self.assertEqual(len(result.fold_id), 40)
        self.assertFalse(np.any(result.fold_id < 0))
        for values in result.probabilities.values():
            self.assertEqual(len(values), 40)
            self.assertTrue(np.all((values >= 0.0) & (values <= 1.0)))
        group_fold_counts = pd.DataFrame(
            {"group": groups, "fold": result.fold_id}
        ).groupby("group")["fold"].nunique()
        self.assertTrue(group_fold_counts.eq(1).all())

    def test_grouped_calibration_keeps_raw_probability_separate_from_decision_score(self):
        subject = load_calibration_subject()
        decision = np.linspace(-4.0, 4.0, 40)
        raw_probability = 1.0 / (1.0 + np.exp(-decision))
        y_true = np.array([0, 1] * 20)
        groups = np.array([f"C{index // 2}" for index in range(40)])

        result = subject.cross_validated_calibration(
            decision,
            y_true,
            groups,
            raw_probability=raw_probability,
            n_splits=4,
            random_state=7,
        )

        np.testing.assert_allclose(result.probabilities["raw"], raw_probability)

    def test_frozen_estimator_calibration_does_not_refit_the_classifier(self):
        subject = load_calibration_subject()
        matrix = np.column_stack(
            [np.linspace(-2.0, 2.0, 40), np.tile([0.0, 1.0], 20)]
        )
        y_true = np.array([0, 1] * 20)
        groups = np.array([f"C{index // 2}" for index in range(40)])
        classifier = LogisticRegression().fit(matrix, y_true)
        coefficient_before = classifier.coef_.copy()

        result = subject.cross_validated_frozen_calibration(
            classifier,
            matrix,
            y_true,
            groups,
            n_splits=4,
            random_state=7,
        )

        self.assertEqual(set(result.probabilities), {"raw", "platt", "isotonic"})
        np.testing.assert_allclose(classifier.coef_, coefficient_before)
        np.testing.assert_allclose(
            result.probabilities["raw"], classifier.predict_proba(matrix)[:, 1]
        )


class LinearShapTest(unittest.TestCase):
    def test_linear_logit_shap_is_exactly_additive(self):
        subject = load_shap_subject()
        matrix = np.array([[1.0, 2.0], [3.0, 0.0]])
        coefficient = np.array([0.5, -2.0])
        background_mean = np.array([2.0, 1.0])

        result = subject.linear_logit_shap(
            matrix,
            coefficient=coefficient,
            intercept=0.25,
            background_mean=background_mean,
        )

        np.testing.assert_allclose(result.values, [[-0.5, -2.0], [0.5, 2.0]])
        self.assertAlmostEqual(result.base_value, -0.75)
        np.testing.assert_allclose(
            result.base_value + result.values.sum(axis=1),
            [0.25 + 0.5 - 4.0, 0.25 + 1.5],
        )

    def test_one_hot_contributions_are_aggregated_to_the_source_feature(self):
        subject = load_shap_subject()
        transformed_names = np.array(
            ["numeric__temp", "categorical__line_A", "categorical__line_B"]
        )
        values = np.array([[1.0, 2.0, 3.0], [-1.0, -2.0, -3.0]])

        source_names, aggregated = subject.aggregate_to_source_features(
            transformed_names,
            values,
            categorical_features=["line"],
        )

        self.assertEqual(source_names, ["temp", "line"])
        np.testing.assert_allclose(aggregated, [[1.0, 5.0], [-1.0, -5.0]])


class FeatureRegistryTest(unittest.TestCase):
    def test_registry_maps_known_features_and_marks_unknowns_for_review(self):
        subject = load_registry_subject()

        registry = subject.build_feature_registry(
            ["tundish_temp", "f_heat_temp", "hr_width", "unknown_signal"]
        ).set_index("feature")

        self.assertEqual(registry.loc["tundish_temp", "process_stage"], "연주")
        self.assertEqual(registry.loc["f_heat_temp", "equipment_candidate"], "가열로 가열대")
        self.assertEqual(registry.loc["hr_width", "equipment_candidate"], "열연 폭 계측 계통")
        self.assertEqual(registry.loc["unknown_signal", "mapping_status"], "REVIEW_REQUIRED")
        self.assertFalse(registry["automated_maintenance_ticket_eligible"].any())
        self.assertTrue(
            registry["interpretation_limit"].str.contains("원인|고장", regex=True).all()
        )


if __name__ == "__main__":
    unittest.main()
