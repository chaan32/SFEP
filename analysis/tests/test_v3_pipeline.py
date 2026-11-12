"""Exercise the v3 producer with synthetic data and real model training."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from equipment_quality import cli
from equipment_quality.artifacts import compute_model_identity
from equipment_quality.deterministic import sha256_uri


REPOSITORY = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY / "contracts/equipment-monitor/v3"
SOURCE = FIXTURES / "golden-source"
CONFIG = REPOSITORY / "analysis/analysis_config_v3.json"
RUNTIME = REPOSITORY / "analysis/producer_runtime.json"


def test_v3_produces_identical_model_bundles(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli, "verify_runtime", lambda path: SimpleNamespace(manifest_bytes=path.read_bytes())
    )
    first, second = (
        cli.run_analysis(CONFIG, RUNTIME, SOURCE, tmp_path.resolve() / name)
        for name in ("first", "second")
    )
    files = {path.name: path.read_bytes() for path in first.iterdir()}
    assert first.name == second.name
    assert files == {path.name: path.read_bytes() for path in second.iterdir()}
    manifest = json.loads(files["bundle_manifest.json"])
    metadata = json.loads(files["quality_model.json"])
    assert len(files) == 9
    assert [entry["role"] for entry in manifest["artifacts"]][-2:] == [
        "quality_model", "quality_model_metadata"
    ]
    assert metadata["onnxSha256"] == sha256_uri(files["quality_model.onnx"])
    assert metadata["holdout"]["defects"] == 4
    assert metadata["trainingPeriod"]["to"] < metadata["holdoutPeriod"]["from"]
    assert all(b'"' + name + b'"' not in files["quality_model.json"]
               for name in (b"charge_id", b"hr_coil_id", b"ap_prod_id", b"raw", b"matrix"))
    model_id = metadata.pop("modelId")
    assert model_id == manifest["identity"]["model_id"] == compute_model_identity(metadata).value


def test_sealed_golden_bundle_matches_synthetic_sources_and_onnx_vectors():
    from equipment_quality.model_export import onnx_probabilities, verify_probability_parity

    bundle = FIXTURES / "golden-bundle"
    manifest = json.loads((bundle / "bundle_manifest.json").read_bytes())
    for source in SOURCE.iterdir():
        assert sha256_uri(source.read_bytes()) in manifest["identity"].values()
    vectors = json.loads((FIXTURES / "golden-expectation/quality_model_test_vectors.json").read_bytes())
    matrix = np.asarray(vectors["matrix"], dtype=np.float32)
    assert len(vectors["raw"]) == len(vectors["probabilities"]) == len(matrix) > 0
    model = (bundle / "quality_model.onnx").read_bytes()
    assert manifest["identity"]["quality_model_sha256"] == sha256_uri(model)
    verify_probability_parity(vectors["probabilities"], onnx_probabilities(model, matrix)[:, 1])
