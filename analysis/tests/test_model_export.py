import numpy as np
from sklearn.linear_model import LogisticRegression

from equipment_quality.model_export import export_onnx, onnx_probabilities, verify_probability_parity


def test_native_and_onnx_probabilities_match():
    x = np.array([[0, 0], [1, 1], [2, 2], [3, 3]], dtype=np.float32)
    model = LogisticRegression(solver="liblinear").fit(x, [0, 0, 1, 1])
    blob = export_onnx(model, 2)
    actual = onnx_probabilities(blob, x)
    assert actual.shape == (4, 2)
    assert verify_probability_parity(model.predict_proba(x)[:, 1], actual[:, 1]) <= 1e-5
    assert blob == export_onnx(model, 2)
