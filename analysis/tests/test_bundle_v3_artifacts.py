from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError

from equipment_quality import artifacts
from equipment_quality.bundle_contract import contract_for_manifest_version
from equipment_quality.deterministic import canonical_json_bytes, sha256_uri
from equipment_quality.schema import contract_schema_bytes, validate_contract_instance
from factories.artifacts import bundle_request


ROOT = Path(__file__).parents[2]
GOLDEN_V2 = ROOT / "contracts/equipment-monitor/v2/golden-bundle"


def _metadata(blob):
    names = [f"feature_{index}" for index in range(28)]
    features = {
        "features": [{"name": name, "type": "numeric", "median": 1} for name in names],
        "maxMissingFeatureCount": 3,
    }
    period = {"from": "2025-01-01", "to": "2025-01-02"}
    metadata = {
        "schemaVersion": "sfep-quality-model-metadata/v1",
        "onnxSha256": sha256_uri(blob),
        "featureSha256": sha256_uri(canonical_json_bytes(features)),
        "model": "random_forest",
        "features": features,
        "threshold": 0.5,
        "qualification": "RESEARCH_ONLY",
        "qualificationReasons": ["BRIER_SKILL_NOT_POSITIVE"],
        "onnx": {
            "triggerStage": "FURNACE_EXTRACTED",
            "opsets": {"": 21, "ai.onnx.ml": 1},
            "input": {"name": "features", "dataType": "FLOAT", "shape": [None, 28]},
            "labelOutput": {"name": "label", "dataType": "INT64", "shape": [None]},
            "probabilityOutput": {"name": "probabilities", "dataType": "FLOAT", "shape": [None, 2]},
        },
        "trainingPeriod": period,
        "holdoutPeriod": {"from": "2025-01-03", "to": "2025-01-04"},
        "oofRanking": [
            {"model": model, "averagePrecision": 0.5, "threshold": 0.5}
            for model in ("random_forest", "logistic", "hgb")
        ],
        "selectionReason": "Highest OOF AP",
        "holdout": {
            "total": 4, "evaluable": 4, "defects": 2, "recall": 0.5,
            "precision": 0.5, "averagePrecision": 0.5, "brierSkill": 0, "prevalence": 0.5,
            "confusion": {"tp": 1, "fp": 1, "tn": 1, "fn": 1},
        },
        "importance": [{"feature": name, "apDecreaseMean": 0, "apDecreaseStd": 0} for name in names],
    }
    metadata["modelId"] = artifacts.compute_model_identity(metadata).value
    return canonical_json_bytes(metadata)


def _request(tmp_path, blob=b"\x08\x00\xff\x01"):
    old = bundle_request(tmp_path)
    profile = contract_for_manifest_version("sfep-equipment-bundle/v3")
    config = (ROOT / "analysis/analysis_config_v3.json").read_bytes()
    schemas = {role: sha256_uri(contract_schema_bytes(profile, role)) for role in profile.schema_resources}
    criteria = artifacts.compute_criteria_identity(
        old.as_of, b"test criteria", config, old.producer_runtime,
        {role: schemas[role] for role in (
            "analysis_config", "equipment_operating_ranges", "producer_runtime", "quality_risk_intervals",
        )},
    )
    metadata = _metadata(blob)
    bundle = artifacts.compute_bundle_identity(
        criteria.value, config, old.producer_runtime, old.sources, schemas,
        quality_model=blob, quality_model_metadata=metadata,
    )
    summary = json.loads((GOLDEN_V2 / "analysis_summary.json").read_bytes())
    return replace(
        old, contract=profile, manifest_version=profile.manifest_version,
        analysis_config=config, schema_digests=schemas, criteria_identity=criteria,
        bundle_identity=bundle, quality_model=blob, quality_model_metadata=metadata,
        analysis_summary={**summary, "bundleId": bundle.value, "criteriaId": criteria.value},
        equipment_operating_ranges={**old.equipment_operating_ranges, "criteriaId": criteria.value},
        quality_risk_intervals={**old.quality_risk_intervals, "criteriaId": criteria.value},
        replay_events=tuple(
            replace(event, bundle_id=bundle.value, criteria_id=criteria.value)
            for event in old.replay_events
        ),
    )


def test_model_id_uses_a_separate_namespace_and_has_no_self_reference():
    metadata = {"schemaVersion": "sfep-quality-model-metadata/v1", "onnxSha256": "sample"}
    digest = sha256_uri(canonical_json_bytes(metadata))
    expected = hashlib.sha256(f"sfep-quality-model-id/v1\nmetadata_sha256={digest}\n".encode())
    assert artifacts.compute_model_identity(metadata).value == "sha256:" + expected.hexdigest()
    with pytest.raises(ValueError, match="modelId"):
        artifacts.compute_model_identity({**metadata, "modelId": "forged"})


def test_changing_model_bytes_keeps_criteria_but_changes_both_bundle_and_model_identity(tmp_path):
    original = _request(tmp_path)
    changed = _request(tmp_path, b"\x08\x00\xff\x02")
    assert original.criteria_identity.value == changed.criteria_identity.value
    assert original.bundle_identity.value != changed.bundle_identity.value
    assert original.bundle_identity.fields["model_id"] != changed.bundle_identity.fields["model_id"]
    assert original.bundle_identity.fields["quality_model_sha256"] == sha256_uri(original.quality_model)
    assert original.bundle_identity.fields["quality_model_metadata_sha256"] == sha256_uri(original.quality_model_metadata)
    assert original.bundle_identity.fields["schema.quality_model_metadata.sha256"] == original.schema_digests["quality_model_metadata"]
    with pytest.raises(ValueError, match="model"):
        replace(original, quality_model=b"forged binary")
    with pytest.raises(ValueError, match="model"):
        replace(original, quality_model_metadata=None)


def test_bundle_identity_rejects_metadata_with_a_stale_model_id(tmp_path):
    request = _request(tmp_path)
    metadata = json.loads(request.quality_model_metadata)
    metadata["threshold"] = 0.75
    with pytest.raises(ValueError, match="model ID"):
        artifacts.compute_bundle_identity(
            request.criteria_identity.value, request.analysis_config,
            request.producer_runtime, request.sources, request.schema_digests,
            quality_model=request.quality_model,
            quality_model_metadata=canonical_json_bytes(metadata),
        )


def test_v3_manifest_rejects_missing_extra_and_reordered_artifacts(tmp_path):
    request = _request(tmp_path)
    bundle = artifacts.write_bundle(request)
    manifest = json.loads((bundle / "bundle_manifest.json").read_bytes())
    descriptors = manifest["artifacts"]
    assert [entry["role"] for entry in descriptors] == [
        "analysis_config", "producer_runtime", "equipment_operating_ranges",
        "quality_risk_intervals", "replay_events", "analysis_summary", "quality_model",
        "quality_model_metadata",
    ]
    for altered in (descriptors[:-1], descriptors + descriptors[-1:], descriptors[:-2] + descriptors[-2:][::-1]):
        with pytest.raises(ValidationError):
            validate_contract_instance(request.contract, "bundle_manifest", {**manifest, "artifacts": altered})


def test_binary_publication_is_idempotent_and_never_overwrites_a_tampered_bundle(tmp_path):
    request = _request(tmp_path)
    bundle = artifacts.write_bundle(request)
    model_file = bundle / "quality_model.onnx"
    assert model_file.read_bytes() == request.quality_model
    assert artifacts.verify_complete_bundle(bundle, request)
    assert artifacts.write_bundle(request) == bundle
    model_file.write_bytes(b"\x08\x00\xff\x02")
    assert not artifacts.verify_complete_bundle(bundle, request)
    with pytest.raises(artifacts.NonDeterministicBundleError):
        artifacts.write_bundle(request)
    assert model_file.read_bytes() == b"\x08\x00\xff\x02"


def test_failure_during_binary_fsync_leaves_no_partial_bundle(tmp_path, monkeypatch):
    request = _request(tmp_path)
    fsync_file = artifacts.fsync_file

    def fail_on_model(fd, phase):
        if phase == "quality_model":
            raise OSError("injected binary write failure")
        fsync_file(fd, phase)

    monkeypatch.setattr(artifacts, "fsync_file", fail_on_model)
    with pytest.raises(OSError, match="binary write failure"):
        artifacts.write_bundle(request)
    assert {item.name for item in tmp_path.iterdir()} == {".sfep-equipment-quality.lock"}


def test_publication_does_not_replace_an_empty_directory_created_at_commit(tmp_path, monkeypatch):
    request = _request(tmp_path)
    target = tmp_path / request.bundle_identity.value
    rename = artifacts.atomic_rename

    def create_collision_then_rename(source, destination):
        destination.mkdir()
        rename(source, destination)

    monkeypatch.setattr(artifacts, "atomic_rename", create_collision_then_rename)
    with pytest.raises(artifacts.NonDeterministicBundleError):
        artifacts.write_bundle(request)
    assert list(target.iterdir()) == []
    assert not list(tmp_path.glob(".sfep-bundle-tmp-*"))
