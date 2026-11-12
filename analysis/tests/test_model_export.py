import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier

from equipment_quality.model_export import export_onnx, onnx_probabilities, verify_probability_parity
from equipment_quality.model_cli import select_exportable


def test_native_and_onnx_probabilities_match():
    x = np.array([[0, 0], [1, 1], [2, 2], [3, 3]], dtype=np.float32)
    model = LogisticRegression(solver="liblinear").fit(x, [0, 0, 1, 1])
    blob = export_onnx(model, 2)
    actual = onnx_probabilities(blob, x)
    assert actual.shape == (4, 2)
    assert verify_probability_parity(model.predict_proba(x)[:, 1], actual[:, 1]) <= 1e-5
    assert blob == export_onnx(model, 2)


def test_all_three_candidate_families_have_explicit_export_outcomes():
    x = np.arange(120, dtype=np.float32).reshape(60, 2)
    y = np.array([0, 1] * 30)
    models = [
        LogisticRegression(solver="liblinear", random_state=42),
        HistGradientBoostingClassifier(max_iter=10, min_samples_leaf=2, early_stopping=False, random_state=42),
        RandomForestClassifier(n_estimators=10, n_jobs=1, random_state=42),
    ]
    for model in models:
        model.fit(x, y)
        if isinstance(model, HistGradientBoostingClassifier):
            # Pinned converter currently rejects this ONNX tree representation.
            with pytest.raises(ValueError):
                export_onnx(model, 2)
            continue
        blob = export_onnx(model, 2)
        assert blob == export_onnx(model, 2)
        assert verify_probability_parity(model.predict_proba(x)[:, 1], onnx_probabilities(blob, x)[:, 1]) <= 1e-5


def test_export_fallback_records_first_failure():
    x = np.array([[0, 0], [1, 1], [2, 2], [3, 3]], dtype=np.float32)
    good = LogisticRegression(solver="liblinear").fit(x, [0, 0, 1, 1])
    class Candidate:
        def __init__(self, name, estimator):
            self.name, self.estimator = name, estimator
    selected, blob, delta, failures = select_exportable((Candidate("bad", object()), Candidate("good", good)), x)
    assert selected.name == "good"
    assert blob
    assert delta <= 1e-5
    assert failures[0]["model"] == "bad"
