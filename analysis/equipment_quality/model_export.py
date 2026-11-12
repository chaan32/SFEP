"""Deterministic ONNX export and probability comparison."""

import numpy as np
import onnx
import onnxruntime as ort
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType

from equipment_quality.model_runtime import single_threaded


def export_onnx(estimator, feature_count: int) -> bytes:
    with single_threaded():
        model = convert_sklearn(
            estimator,
            initial_types=[("features", FloatTensorType([None, feature_count]))],
            target_opset=21,
            options={id(estimator): {"zipmap": False}},
        )
    if (len(model.graph.input) != 1 or len(model.graph.output) != 2
            or model.graph.output[1].name != "probabilities"):
        raise ValueError("INVALID_ONNX_IO")
    model.graph.name = "sfep_quality_model"
    onnx.checker.check_model(model)
    return model.SerializeToString(deterministic=True)


def onnx_probabilities(model_bytes: bytes, matrix: np.ndarray) -> np.ndarray:
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    with single_threaded():
        session = ort.InferenceSession(model_bytes, sess_options=options, providers=["CPUExecutionProvider"])
        probability = session.run(["probabilities"], {"features": np.ascontiguousarray(matrix, dtype=np.float32)})[0]
    if probability.ndim != 2 or probability.shape[1] != 2 or not np.isfinite(probability).all():
        raise ValueError("INVALID_ONNX_PROBABILITY")
    return probability


def verify_probability_parity(native, exported, atol: float = 1e-5) -> float:
    left = np.asarray(native, dtype=float)
    right = np.asarray(exported, dtype=float)
    if left.shape != right.shape or not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError("INVALID_PROBABILITY_PARITY")
    delta = float(np.max(np.abs(left - right))) if left.size else 0.0
    if delta > atol:
        raise ValueError(f"ONNX_PARITY_FAILED: {delta}")
    return delta
