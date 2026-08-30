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

import oracles.v1_execution_trace as v1_oracle
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
ACTUAL_V1_BUNDLE_ID = "sha256:65d535350ef2d7c5abf6372fdfe7b364c62a6c0af60b01946a23c4659c1ee82a"
ACTUAL_MANIFEST_SIZE = 3695
ACTUAL_MANIFEST_SHA256 = "c5f2c9d167fa82e1e7ca3347d401b0591c40b6dbd74a215ef9e77960dec4bd3a"
ACTUAL_ARTIFACTS = (
    ("analysis_config", 13830, "sha256:bb2610971dc1b3adcb4e93b9d26a50fdb4070292ffc4bf4a26ce7cdde0f5fb0b"),
    ("producer_runtime", 5537, "sha256:3c1daabb3e868fc6cebc75023980fcb71d947996170de6d8e58cd06b18231407"),
    ("equipment_operating_ranges", 187344, "sha256:8bd1e05d4b33a0d6e4fda59d135cfa0b6a5b8f00779a6c82c307d09d95751682"),
    ("quality_risk_intervals", 622603, "sha256:bd2e2f6fa8c0233e6d658d7fb84b2a717d17a7fbdc74bd47733024f412cb0954"),
    ("replay_events", 119700523, "sha256:d2b5a8029b98f60181460c75bf5e55d8568c4dd46f32752e3dfd5e200ecbd599"),
    ("analysis_summary", 90097180, "sha256:fc3bd17615f1500e36da11394a48acdffab53e18935a600a0257814fd41caadc"),
)
ACTUAL_SOURCES = {
    "ap": ("sts_3ap_3.csv", "sha256:ff4572a1302459787ddca7458857864182d969e45b6a81edc4241bc47549801d", "1277032"),
    "fur_hr": ("sts_2fur_hr_2.csv", "sha256:c2bb0b503ec30b0e01e00d2bd88fde536479de59aebf1eae84131380d58f3bcf", "3578800"),
    "sm_cc": ("sts_1sm_cc_1.csv", "sha256:0cd3e91428c005dae1785b9af01d30e3d08230e2058c68693c9aa7ffee043075", "1883100"),
}

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


def _sequence_digest(values: tuple[str, ...]) -> str:
    return hashlib.sha256(("\n".join(values) + "\n").encode()).hexdigest()


def _identity(version: str, fields: dict[str, str]) -> str:
    preimage = "\n".join([version, *(f"{key}={fields[key]}" for key in sorted(fields))]) + "\n"
    return _sha256_uri(preimage.encode())


def _copy_bundle(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination)
    return destination


def _overlay_bundle(source: Path, destination: Path) -> Path:
    destination.mkdir()
    for path in source.iterdir():
        target = destination / path.name
        if path.name == "bundle_manifest.json":
            shutil.copy2(path, target)
        else:
            target.symlink_to(path)
    return destination


@pytest.fixture(scope="module")
def verified_actual_v1(actual_v1_bundle: Path):
    return verify_v1_bundle(actual_v1_bundle)


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


def test_verified_v1_seed_anchors_are_frozen(verified_actual_v1) -> None:
    golden = verify_v1_bundle(GOLDEN_V1_BUNDLE)
    assert golden.criteria_id == GOLDEN_V1_SEED
    assert verified_actual_v1.criteria_id == ACTUAL_V1_SEED
    assert verified_actual_v1.bundle_id == ACTUAL_V1_BUNDLE_ID


def test_actual_manifest_artifact_and_source_anchor_literals(actual_v1_bundle: Path) -> None:
    manifest_payload = (actual_v1_bundle / "bundle_manifest.json").read_bytes()
    manifest = json.loads(manifest_payload)
    assert len(manifest_payload) == ACTUAL_MANIFEST_SIZE
    assert hashlib.sha256(manifest_payload).hexdigest() == ACTUAL_MANIFEST_SHA256
    assert manifest["bundleId"] == ACTUAL_V1_BUNDLE_ID
    assert tuple(
        (item["role"], item["sizeBytes"], item["sha256"])
        for item in manifest["artifacts"]
    ) == ACTUAL_ARTIFACTS
    assert {
        role: tuple(manifest["identity"][f"source.{role}.{field}"] for field in ("name", "sha256", "size_bytes"))
        for role in ("ap", "fur_hr", "sm_cc")
    } == ACTUAL_SOURCES


def test_verifier_rejects_coherent_actual_source_replacement(
    actual_v1_bundle: Path, tmp_path: Path,
) -> None:
    bundle = _overlay_bundle(actual_v1_bundle, tmp_path / "coherent-source")

    def mutate(manifest: dict[str, object]) -> None:
        manifest["identity"]["source.ap.size_bytes"] = "1277033"
        manifest["identity"]["source.ap.sha256"] = "sha256:" + "0" * 64
        manifest["bundleId"] = _identity("sfep-bundle-id/v1", manifest["identity"])

    _rewrite_manifest(bundle, mutate)
    with pytest.raises(ValueError, match="approved actual v1 anchor"):
        verify_v1_bundle(bundle)


def test_verifier_rejects_coherent_approved_artifact_replacement(tmp_path: Path) -> None:
    bundle = _copy_bundle(GOLDEN_V1_BUNDLE, tmp_path / "coherent-artifact")
    payload = (bundle / "analysis_summary.json").read_bytes()
    assert payload.startswith(b'{"asOf"')
    _rebind_artifact(bundle, "analysis_summary", b'{"aaa":0,' + payload[1:])
    with pytest.raises(ValueError, match="approved v1 anchor"):
        verify_v1_bundle(bundle)


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


@pytest.mark.parametrize("tamper", ("population", "aggregate-rule", "material"))
def test_verifier_authenticates_summary_lineage_bindings(tmp_path: Path, tamper: str) -> None:
    bundle = _copy_bundle(GOLDEN_V1_BUNDLE, tmp_path / tamper)
    payload = (bundle / "analysis_summary.json").read_bytes()
    summary = json.loads(payload)
    if tamper == "population":
        old = summary["lineage"]["populations"][1]["materialKeys"][0].encode()
        payload = payload.replace(b'"materialKeys":["' + old, b'"materialKeys":["sha256:' + b"0" * 64, 1)
    elif tamper == "aggregate-rule":
        old = summary["lineage"]["aggregates"][0]["ruleId"].encode()
        payload = payload.replace(b'"ruleId":"' + old, b'"ruleId":"sha256:' + b"0" * 64, 1)
    else:
        old = summary["lineage"]["materials"][0]["chargeId"].encode()
        payload = payload.replace(b'"chargeId":"' + old + b'","hrCoilId"', b'"chargeId":"ZZ1","hrCoilId"', 1)
    _rebind_artifact(bundle, "analysis_summary", payload)
    with pytest.raises(ValueError, match="lineage"):
        verify_v1_bundle(bundle)


def test_independent_canonical_number_serializer_matches_frozen_vectors() -> None:
    vectors = json.loads((V1_CONTRACT_ROOT / "canonical-number-test-vectors.json").read_bytes())
    assert len(vectors) == 44
    assert [
        v1_oracle.canonical_v1_number(float.fromhex(vector["hex"]))
        for vector in vectors
    ] == [vector["expected"] for vector in vectors]


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


def test_golden_trace_literals_are_frozen() -> None:
    trace = verify_v1_bundle(GOLDEN_V1_BUNDLE).trace
    assert (len(trace.rule_ids), trace.rule_ids[0], trace.rule_ids[-1], _sequence_digest(trace.rule_ids)) == (
        165,
        "sha256:01a3aef9da604e51b31a8fb6ab95551873c3673ef9dbcc10dd65b2753d99a479",
        "sha256:fba8056beb67ad38e3cd8aaf7722fdf2fc87f657e1b1aba236c9047a1a1c78ed",
        "17e1c021836a026ec931e60c7ae643a07b156fe120f1e4b7f262cea184f62985",
    )
    assert trace.range_ids == ()
    assert (len(trace.event_ids), trace.event_ids[0], trace.event_ids[-1], _sequence_digest(trace.event_ids)) == (
        95,
        "sha256:b80d26659b6f6c3ab842b840ce7c8f11eb621ce5292eb14156732c5d91f3c324",
        "sha256:71a6ea2d6ce6e0f65f4b2188ce5e692df5e63408eb868b18a9f292419677a0a1",
        "efbc5963a584dd79b84e2455044645d7d51b1f4c5d6721b0b2b38f9ccf2142ab",
    )
    assert trace.rule_bootstraps == ()
    assert tuple((item.alert_grade, item.confusion) for item in trace.holdout_metrics) == (
        ("DANGER", (0, 0, 1, 1)),
        ("CAUTION_OR_DANGER", (0, 0, 1, 1)),
    )
    assert (len(trace.summary_lineage_keys), _sequence_digest(trace.summary_lineage_keys)) == (
        40, "a3a7792e4daca926056c3eec5a48e3c79969ce1b0ebf5815d26266cdd37483ce",
    )


def test_actual_trace_literals_and_complete_events_are_frozen(verified_actual_v1) -> None:
    trace = verified_actual_v1.trace
    bootstrap = trace.rule_bootstraps
    assert len(bootstrap) == 1
    bootstrap = bootstrap[0]
    assert bootstrap.rule_id == "sha256:047972476e1a1aff01af8df44ff633c0111b150390823a99ca4859e935d83d49"
    assert bootstrap.replicate_ordinal == 0
    assert len(bootstrap.sampled_charge_ordinals) == 985
    assert bootstrap.sampled_charge_ordinals[:16] == (
        758, 264, 389, 113, 279, 843, 77, 366, 610, 702, 478, 662, 226, 503, 194, 967,
    )
    packed_sample = b"".join(value.to_bytes(8, "big") for value in bootstrap.sampled_charge_ordinals)
    assert hashlib.sha256(packed_sample).hexdigest() == "305f257e26d483d2a0a260d3f19e052e76938a0c391d14131dc497358ebfdea5"
    assert bootstrap.confusion == (10, 678, 31, 3266)
    assert bootstrap.valid_replicate_count == 2000
    assert bootstrap.ci == (1.9839196524486693, 2.9819562722877984)
    assert (len(trace.rule_ids), trace.rule_ids[0], trace.rule_ids[-1], _sequence_digest(trace.rule_ids)) == (
        313,
        "sha256:00b094d51275d226ee77936db9d79bd08a7c5b6f9bfb26e8fd78bd27a4963eac",
        "sha256:ff6b53f8745a6b1a5eea61fcc3d0a82395460e5b0ba6dd879eb44d6d02916a0b",
        "25843478a31226785093412ea05e2202ec07de154cc5334c23ea93bb4c379e69",
    )
    assert (len(trace.range_ids), trace.range_ids[0], trace.range_ids[-1], _sequence_digest(trace.range_ids)) == (
        424,
        "sha256:00360f2bdc1c772cc9a279c1409ffdf44a8fb6b6d4763141c40ae36bb6686ec8",
        "sha256:ff47bb2c4fbe2014c0067da50c3df555298176cef09bed48c6bf7c71f7cea156",
        "ba196558267e4170101c183cd55ee98a6451a353691c8d6bf51ddaf5f6c48708",
    )
    assert (len(trace.event_ids), trace.event_ids[0], trace.event_ids[-1], _sequence_digest(trace.event_ids)) == (
        189043,
        "sha256:bc50829b44127de3ad8cec1f1cc94c061eaf28f23c1a4202cdd3df498371a485",
        "sha256:e4f27e1e1a7bc8ba4076b6893128802f8ef642a84247d83b9112aaac53a6b81e",
        "7025ea9f3d12bb50ef64bae881a67b30615c9e5a0842914303ae8ca0cbe0937d",
    )
    assert vars(trace.event_semantics[0]) == {
        "schema_version": "sfep-replay-events/v1",
        "bundle_id": ACTUAL_V1_BUNDLE_ID,
        "criteria_id": ACTUAL_V1_SEED,
        "event_id": "sha256:bc50829b44127de3ad8cec1f1cc94c061eaf28f23c1a4202cdd3df498371a485",
        "replay_date": "2025-02-16", "replay_hour": None, "batch_kind": "CAST_DAY",
        "batch_id": "sha256:5ccc84ca04c9da2c032dc8f77af54d79e8aacf72ca04cccd5176ec7927074134",
        "equipment_batch_id": None, "batch_step": "CAST_RECORDED", "time_precision": "DAY",
        "material_key": "sha256:abe8d7d6cffc869d6b8facdc1b7bf51ecf6a6ce3822aba54e08cd2a424e8498a",
        "equipment_type": "SM_CC", "equipment_id": "2공장", "charge_id": "CH46344",
        "slab_no": "611", "hr_coil_id": None, "ap_prod_id": None,
        "values_json": '{"cast_date":"2025-02-16","cc_gubun":"2연주","delta_ferrite":69,"ingre_cr":18.29,"ingre_ni":8.22,"ingre_s":0.02,"mlac_ratio":100,"slab_grind":"HSHS","slab_gubun":"C","sm_plant":"2공장","steel_grade":"C13","steel_usage":"Other","tundish_temp":1489}'.encode(),
    }
    assert vars(trace.event_semantics[-1]) == {
        "schema_version": "sfep-replay-events/v1",
        "bundle_id": ACTUAL_V1_BUNDLE_ID,
        "criteria_id": ACTUAL_V1_SEED,
        "event_id": "sha256:e4f27e1e1a7bc8ba4076b6893128802f8ef642a84247d83b9112aaac53a6b81e",
        "replay_date": "2025-09-30", "replay_hour": None, "batch_kind": "AP_DAY",
        "batch_id": "sha256:05e06006cbdda1081cc15d9eae0eb9e00da295cd72a917c1a4c04ed83cf42a70",
        "equipment_batch_id": None, "batch_step": "AP_RECORDED_WITH_RESULT", "time_precision": "DAY",
        "material_key": "sha256:fca46f72022da0cc3de909a3c6a44990609bff3eef7b574c64acec3cb587d794",
        "equipment_type": "AP", "equipment_id": "2공장", "charge_id": "CH84642",
        "slab_no": "211", "hr_coil_id": "A741274", "ap_prod_id": "X7792",
        "values_json": '{"ap_date":"2025-09-30","ap_line_speed":50,"ap_plant":"2공장","ap_shift":"D","ap_thick":2.98,"ap_width":1022,"judge":"양품"}'.encode(),
    }
    assert tuple((item.alert_grade, item.confusion) for item in trace.holdout_metrics) == (
        ("DANGER", (253, 3285, 3159, 29)),
        ("CAUTION_OR_DANGER", (253, 3285, 3159, 29)),
    )
    expected_metrics = (
        ("alertRate", {"lower": 0.5047942174362, "pointEstimate": 0.5260184359203093, "reasonCode": "NONE", "upper": 0.5481918595854138, "validReplicates": 2000}),
        ("precision", {"lower": 0.060681629260182876, "pointEstimate": 0.07150932730356134, "reasonCode": "NONE", "upper": 0.08307343351290049, "validReplicates": 2000}),
        ("recall", {"lower": 0.8557692307692307, "pointEstimate": 0.8971631205673759, "reasonCode": "NONE", "upper": 0.9335664335664335, "validReplicates": 2000}),
        ("baseDefectRate", {"lower": 0.03606019967218, "pointEstimate": 0.04192685102586976, "reasonCode": "NONE", "upper": 0.04841127098321343, "validReplicates": 2000}),
        ("lift", {"lower": 1.611859396828654, "pointEstimate": 1.7055735299423886, "reasonCode": "NONE", "upper": 1.8012808931432032, "validReplicates": 2000}),
        ("falseAlertsPer100", {"lower": 46.74318080190788, "pointEstimate": 48.84032114183764, "reasonCode": "NONE", "upper": 51.002393776181925, "validReplicates": 2000}),
    )
    assert trace.holdout_metrics[0].metrics == expected_metrics
    assert trace.holdout_metrics[1].metrics == expected_metrics
    assert (len(trace.summary_lineage_keys), _sequence_digest(trace.summary_lineage_keys)) == (
        40, "a3a7792e4daca926056c3eec5a48e3c79969ce1b0ebf5815d26266cdd37483ce",
    )


def test_trace_projects_all_migration_semantics_deterministically(
    actual_v1_bundle: Path, verified_actual_v1,
) -> None:
    before = {
        path.name: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in actual_v1_bundle.iterdir()
        if path.is_file()
    }
    verified = verified_actual_v1
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
