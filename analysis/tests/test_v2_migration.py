"""Authenticated v1 anchors for the bundle-v2 seed migration."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from oracles.v1_execution_trace import (
    project_v1_execution_trace,
    verify_v1_bundle,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
V1_CONTRACT_ROOT = REPOSITORY_ROOT / "contracts" / "equipment-monitor" / "v1"
GOLDEN_V1_BUNDLE = V1_CONTRACT_ROOT / "golden-bundle"
GOLDEN_V1_SEED = "sha256:8c88d3109bd6945b76317ee415c7821524f515c9bd79d36b8072c285c6434af7"
ACTUAL_V1_SEED = "sha256:c0a9d1f3f0d655c24d2eeddc58f1905672d2f72d7ffd0d14e87bdecb118a2a26"
GOLDEN_V1_BUNDLE_ID = "sha256:4f40427690116f0defcbe609d9e5e1ef4025bb4193cdf099386659a26dd9f8bc"

# Hand-frozen from the verified v1 tree.  The oracle never derives this table.
V1_BYTE_FREEZE = {
    "analysis_config.schema.json": "fad28561dfe9d9fe3cd09b025bb18c2101be053cb094b08442ea45963b86f549",
    "analysis_summary.schema.json": "6cb6077cc7093b90e464cc794604ca6b7682002a138ebed051801aee29f9d374",
    "bundle_manifest.schema.json": "666e880d296c0d7e3df5af1aa80e6865922ebfb337fb9aca48f93eddfd89e8a5",
    "canonical-number-test-vectors.json": "38357d4c86b4e054e77f32b62eccaa8c787e058bf87a079a46979439bf45e5ac",
    "equipment_operating_ranges.schema.json": "bee7d8be181dae4844c51d4627c5a1f068583b60a60c854f17035a8291cd7d89",
    "golden-bundle/analysis_config.json": "bb2610971dc1b3adcb4e93b9d26a50fdb4070292ffc4bf4a26ce7cdde0f5fb0b",
    "golden-bundle/analysis_summary.json": "aec581bca1e6b4eaed1ddae36c53cd3d5c70e2afb582589e1bc299dd2743876e",
    "golden-bundle/bundle_manifest.json": "d717b150fcd7cf80065e2a71744091da387d38243f30d816f4cc83e63d605a33",
    "golden-bundle/equipment_operating_ranges.json": "bd8ab5dc79a1a7e97d64132dd3669befbe3fa28493a57eff5918818933526200",
    "golden-bundle/expected_alerts.json": "bb1834b04cc847d52f1a5e2d9fa9268140dbbab4af86e69d310d1c1c1278e0ae",
    "golden-bundle/producer_runtime.json": "3c1daabb3e868fc6cebc75023980fcb71d947996170de6d8e58cd06b18231407",
    "golden-bundle/quality_risk_intervals.json": "3b0a85925ab1ae7b42d70989c32fad3ea3b881cf9647a6a13a72db3d42b7eb30",
    "golden-bundle/replay_events.csv": "10b39aac41f317210074e42b44a0e1ab6f521b7c432647edf72be80a4e0fef8c",
    "golden-expectation/analysis_summary.template.json": "20acdfe66fc1167340c58f2ff91a00621725bace0b3b65832ab8629e51e04f1d",
    "golden-expectation/criteria_projection.jsonl": "6842fabd1cc79ca801e34bb0708498f4bea16317d4449e6d773e28e40c94ff5c",
    "golden-expectation/equipment_operating_ranges.template.json": "77fa2a0665d6d5bf0cbd754b56a86c38b159cfa26abee6685fc910cf7e546581",
    "golden-expectation/expected_alerts.json": "2c3bb6bc8e29fe077e71fd8f1b54efaa272ca31ae2676e87be8bcf549bc550ed",
    "golden-expectation/quality_risk_intervals.template.json": "d47e7d8c230f31f56719056fc3a79505847863a51e686b1aac1aee9505143996",
    "golden-expectation/replay_events.template.csv": "c516b9f8cebc2642b9c9b75a3bc12f1608df651522ccb2a65c2eb5c1424ce17c",
    "golden-source/sts_1sm_cc_1.csv": "c3bba5c7235166b6693ff657a25797933ea9d83c6392437af9b5141f4e56a68c",
    "golden-source/sts_2fur_hr_2.csv": "973e864ed996ca48c44a00943efc0ea51a878b94b8aac7452c7254a572fe7e67",
    "golden-source/sts_3ap_3.csv": "efaef9f283a28e63dff54b26bc3dd42c6a72e20f0b75d2fa13a6ab47f41405c9",
    "id-test-vectors.json": "4e3e20a658230b93e4c264dc16d8304d03cb834c85d3038a018bcd1dea01363f",
    "producer_runtime.schema.json": "97131d80a993d09d17c2c040b0e1cb2bd0eed5948d7a11608f26331d18f557e6",
    "quality_risk_intervals.schema.json": "2c8775fec18671030cf58ea0e94a3c99f8dac075fa9d5fe8dce724c2462f42ad",
    "replay_event_row.schema.json": "309749f73cf2a5a522617f975128ac005298f2fd71412903d76263702c1b6bd6",
}


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256_uri(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _identity(version: str, fields: dict[str, str]) -> str:
    preimage = "\n".join([version, *(f"{key}={fields[key]}" for key in sorted(fields))]) + "\n"
    return _sha256_uri(preimage.encode())


def _copy_bundle(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination)
    return destination


def _rewrite_manifest(bundle: Path, mutate) -> None:
    path = bundle / "bundle_manifest.json"
    manifest = json.loads(path.read_bytes())
    mutate(manifest)
    path.write_bytes(_canonical_json(manifest))


def _rebind_artifact(bundle: Path, role: str, payload: bytes) -> None:
    filenames = {
        "equipment_operating_ranges": "equipment_operating_ranges.json",
        "quality_risk_intervals": "quality_risk_intervals.json",
        "replay_events": "replay_events.csv",
        "analysis_summary": "analysis_summary.json",
    }
    (bundle / filenames[role]).write_bytes(payload)

    def mutate(manifest: dict[str, object]) -> None:
        artifact = next(item for item in manifest["artifacts"] if item["role"] == role)
        artifact["sizeBytes"] = len(payload)
        artifact["sha256"] = _sha256_uri(payload)

    _rewrite_manifest(bundle, mutate)


def test_verified_v1_seed_anchors_are_frozen(actual_v1_bundle: Path) -> None:
    golden = verify_v1_bundle(GOLDEN_V1_BUNDLE)
    actual = verify_v1_bundle(actual_v1_bundle)
    assert golden.criteria_id == GOLDEN_V1_SEED
    assert actual.criteria_id == ACTUAL_V1_SEED


def test_every_v1_contract_byte_is_frozen() -> None:
    paths = sorted(
        (path for path in V1_CONTRACT_ROOT.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(V1_CONTRACT_ROOT).as_posix().encode(),
    )
    actual = {
        path.relative_to(V1_CONTRACT_ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    assert actual == V1_BYTE_FREEZE


def test_verifier_rejects_noncanonical_manifest_and_artifact_tamper(tmp_path: Path) -> None:
    noncanonical = _copy_bundle(GOLDEN_V1_BUNDLE, tmp_path / "noncanonical")
    manifest_path = noncanonical / "bundle_manifest.json"
    manifest_path.write_text(json.dumps(json.loads(manifest_path.read_bytes()), indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="canonical JSON"):
        verify_v1_bundle(noncanonical)

    for filename in (
        "analysis_config.json",
        "producer_runtime.json",
        "equipment_operating_ranges.json",
        "quality_risk_intervals.json",
        "replay_events.csv",
        "analysis_summary.json",
    ):
        artifact_tamper = _copy_bundle(
            GOLDEN_V1_BUNDLE, tmp_path / f"artifact-{filename}"
        )
        with (artifact_tamper / filename).open("ab") as stream:
            stream.write(b" ")
        with pytest.raises(ValueError, match="size|digest"):
            verify_v1_bundle(artifact_tamper)


def test_verifier_recomputes_exact_criteria_and_bundle_identities(tmp_path: Path) -> None:
    criteria_tamper = _copy_bundle(GOLDEN_V1_BUNDLE, tmp_path / "criteria")

    def change_criteria(manifest: dict[str, object]) -> None:
        manifest["criteriaId"] = "sha256:" + "0" * 64
        manifest["identity"]["criteria_id"] = manifest["criteriaId"]

    _rewrite_manifest(criteria_tamper, change_criteria)
    with pytest.raises(ValueError, match="criteria identity"):
        verify_v1_bundle(criteria_tamper)

    bundle_tamper = _copy_bundle(GOLDEN_V1_BUNDLE, tmp_path / "bundle")
    _rewrite_manifest(
        bundle_tamper,
        lambda manifest: manifest["identity"].__setitem__("source.ap.size_bytes", "712"),
    )
    with pytest.raises(ValueError, match="bundle identity"):
        verify_v1_bundle(bundle_tamper)

    extra_criteria = _copy_bundle(GOLDEN_V1_BUNDLE, tmp_path / "extra-criteria")
    _rewrite_manifest(
        extra_criteria,
        lambda manifest: manifest["criteriaIdentity"].__setitem__("unexpected", "value"),
    )
    with pytest.raises(ValueError, match="exactly 8 fields"):
        verify_v1_bundle(extra_criteria)

    extra_bundle = _copy_bundle(GOLDEN_V1_BUNDLE, tmp_path / "extra-bundle")
    _rewrite_manifest(
        extra_bundle,
        lambda manifest: manifest["identity"].__setitem__("unexpected", "value"),
    )
    with pytest.raises(ValueError, match="exactly 19 fields"):
        verify_v1_bundle(extra_bundle)


def test_verifier_rejects_recomputed_identity_over_non_v1_schema(tmp_path: Path) -> None:
    bundle = _copy_bundle(GOLDEN_V1_BUNDLE, tmp_path / "schema")

    def mutate(manifest: dict[str, object]) -> None:
        criteria_fields = manifest["criteriaIdentity"]
        criteria_fields["schema.analysis_config.sha256"] = "sha256:" + "0" * 64
        criteria_id = _identity("sfep-criteria-id/v1", criteria_fields)
        manifest["criteriaId"] = criteria_id
        manifest["identity"]["criteria_id"] = criteria_id
        manifest["bundleId"] = _identity("sfep-bundle-id/v1", manifest["identity"])

    _rewrite_manifest(bundle, mutate)
    with pytest.raises(ValueError, match="fixed v1 schema"):
        verify_v1_bundle(bundle)


def test_verifier_authenticates_internal_rule_range_and_event_bindings(tmp_path: Path) -> None:
    range_bundle = _copy_bundle(GOLDEN_V1_BUNDLE, tmp_path / "range")
    ranges = json.loads((range_bundle / "equipment_operating_ranges.json").read_bytes())
    ranges["criteriaId"] = "sha256:" + "0" * 64
    _rebind_artifact(range_bundle, "equipment_operating_ranges", _canonical_json(ranges))
    with pytest.raises(ValueError, match="criteriaId"):
        verify_v1_bundle(range_bundle)

    rules_binding_bundle = _copy_bundle(GOLDEN_V1_BUNDLE, tmp_path / "rules-binding")
    rules_binding = (rules_binding_bundle / "quality_risk_intervals.json").read_bytes()
    criteria_binding = f'"criteriaId":"{GOLDEN_V1_SEED}"'.encode()
    assert rules_binding.count(criteria_binding) == 1
    rules_binding = rules_binding.replace(
        criteria_binding, b'"criteriaId":"sha256:' + b"0" * 64 + b'"'
    )
    _rebind_artifact(rules_binding_bundle, "quality_risk_intervals", rules_binding)
    with pytest.raises(ValueError, match="criteriaId"):
        verify_v1_bundle(rules_binding_bundle)

    summary_bundle = _copy_bundle(GOLDEN_V1_BUNDLE, tmp_path / "summary-binding")
    summary = (summary_bundle / "analysis_summary.json").read_bytes()
    bundle_binding = f'"bundleId":"{GOLDEN_V1_BUNDLE_ID}"'.encode()
    assert summary.count(bundle_binding) == 1
    summary = summary.replace(bundle_binding, b'"bundleId":"sha256:' + b"0" * 64 + b'"')
    _rebind_artifact(summary_bundle, "analysis_summary", summary)
    with pytest.raises(ValueError, match="bundleId"):
        verify_v1_bundle(summary_bundle)

    rule_bundle = _copy_bundle(GOLDEN_V1_BUNDLE, tmp_path / "rule")
    rule_payload = (rule_bundle / "quality_risk_intervals.json").read_bytes()
    assert b'"lower":5.2' in rule_payload
    rule_payload = rule_payload.replace(b'"lower":5.2', b'"lower":-123', 1)
    _rebind_artifact(rule_bundle, "quality_risk_intervals", rule_payload)
    with pytest.raises(ValueError, match="ruleId"):
        verify_v1_bundle(rule_bundle)

    event_bundle = _copy_bundle(GOLDEN_V1_BUNDLE, tmp_path / "event")
    replay_path = event_bundle / "replay_events.csv"
    rows = list(csv.reader(io.StringIO(replay_path.read_text(encoding="utf-8"), newline="")))
    rows[1][rows[0].index("event_id")] = "sha256:" + "0" * 64
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    _rebind_artifact(event_bundle, "replay_events", output.getvalue().encode())
    with pytest.raises(ValueError, match="event_id"):
        verify_v1_bundle(event_bundle)


def test_v1_oracle_imports_with_only_the_standard_library() -> None:
    command = (
        "import sys; "
        f"sys.path.insert(0, {str(Path(__file__).parent)!r}); "
        "import oracles.v1_execution_trace"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", command],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_trace_projects_all_migration_semantics_deterministically(
    actual_v1_bundle: Path,
) -> None:
    before = {
        path.name: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in actual_v1_bundle.iterdir()
        if path.is_file()
    }
    verified = verify_v1_bundle(actual_v1_bundle)
    trace = verified.trace
    assert trace == project_v1_execution_trace(verified)
    assert trace.rule_bootstraps
    assert all(item.replicate_ordinal == 0 for item in trace.rule_bootstraps)
    assert all(item.sampled_charge_ordinals for item in trace.rule_bootstraps)
    assert all(len(item.confusion) == 4 for item in trace.rule_bootstraps)
    assert all(0 <= item.valid_replicate_count <= 2000 for item in trace.rule_bootstraps)
    assert all(len(item.ci) == 2 for item in trace.rule_bootstraps)
    assert tuple(item.alert_grade for item in trace.holdout_metrics) == (
        "DANGER", "CAUTION_OR_DANGER",
    )
    assert trace.rule_ids == tuple(sorted(trace.rule_ids, key=str.encode))
    assert trace.range_ids == tuple(sorted(trace.range_ids, key=str.encode))
    assert trace.event_ids == tuple(item.event_id for item in trace.event_semantics)
    assert "lineage.aggregates[].ruleId" in trace.summary_lineage_keys
    assert "lineage.materials[].materialKey" in trace.summary_lineage_keys
    assert "lineage.populations[].materialKeys[]" in trace.summary_lineage_keys
    after = {
        path.name: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in actual_v1_bundle.iterdir()
        if path.is_file()
    }
    assert after == before
