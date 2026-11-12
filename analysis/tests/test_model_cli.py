import pytest
import pandas as pd

from equipment_quality.model_cli import grouped_holdout_importance, _test_vectors


def test_importance_rejects_invalid_holdout():
    with pytest.raises(ValueError, match="INSUFFICIENT_LABELS"):
        grouped_holdout_importance(None, None, [0], [0], None, 42)


def test_vectors_include_raw_values_for_java_encoder_without_identifiers():
    frame = pd.DataFrame([{"sm_plant": "A", "f_jangip_temp": 123.0, "charge_id": "PRIVATE"}])
    vectors = _test_vectors(frame, [[1.0, 2.0]], [0.2], ["sm_plant", "f_jangip_temp"])
    assert vectors == {"raw": [{"sm_plant": "A", "f_jangip_temp": 123.0}], "matrix": [[1.0, 2.0]], "probabilities": [0.2]}
