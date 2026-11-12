import numpy as np
import pytest

from equipment_quality.model_metrics import classification_metrics, select_f2_threshold


def test_metrics_use_average_precision_and_reference_brier_baseline():
    result = classification_metrics([1, 0, 1, 0], [0.9, 0.8, 0.4, 0.1], 0.5, 0.5)
    assert result.average_precision == pytest.approx(5 / 6)
    assert (result.tp, result.fp, result.tn, result.fn) == (1, 1, 1, 1)
    assert (result.recall, result.precision, result.f2) == (0.5, 0.5, 0.5)
    assert result.brier == pytest.approx(0.255)
    assert result.brier_skill == pytest.approx(-0.02)


def test_threshold_tie_prefers_higher_value():
    assert select_f2_threshold([1, 0], [0.8, 0.2]) == 0.8


def test_metrics_reject_single_class():
    with pytest.raises(ValueError, match="INSUFFICIENT_LABELS"):
        classification_metrics([0, 0], [0.1, 0.2], 0.5, 0.5)
