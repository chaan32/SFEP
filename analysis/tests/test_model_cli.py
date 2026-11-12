import pytest
import pandas as pd

from equipment_quality.model_cli import grouped_holdout_importance, _test_vectors, score_holdout


def test_importance_rejects_invalid_holdout():
    with pytest.raises(ValueError, match="INSUFFICIENT_LABELS"):
        grouped_holdout_importance(None, None, [0], [0], None, 42)


def test_vectors_include_raw_values_for_java_encoder_without_identifiers():
    frame = pd.DataFrame([{"sm_plant": "A", "f_jangip_temp": 123.0, "charge_id": "PRIVATE"}])
    vectors = _test_vectors(frame, [[1.0, 2.0]], [0.2], ["sm_plant", "f_jangip_temp"])
    assert vectors == {"raw": [{"sm_plant": "A", "f_jangip_temp": 123.0}], "matrix": [[1.0, 2.0]], "probabilities": [0.2]}


def test_importance_cannot_change_frozen_holdout_decision(monkeypatch):
    observed = []
    def importance(*args):
        observed.append("after_metrics")
        return ({"feature": "x", "apDecreaseMean": -999.0},)
    monkeypatch.setattr("equipment_quality.model_cli.grouped_holdout_importance", importance)
    metrics, qualification, values = score_holdout(object(), [[0], [1], [2], [3]], [1, 0, 1, 0], [0.9, 0.1, 0.8, 0.2], 0.5, 0.5, None, 42)
    assert observed == ["after_metrics"]
    assert (metrics.tp, metrics.fp, qualification) == (2, 0, "QUALIFIED")
    assert values[0]["apDecreaseMean"] == -999.0
