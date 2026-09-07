"""Strict semantic parity oracle for the sealed golden v1 and v2 Bundles."""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Mapping

from equipment_quality.bundle_contract import V2_CONTRACT
from equipment_quality.schema import validate_contract_instance

from .v1_execution_trace import _canonical_bytes, verify_v1_bundle


_SHA256 = "sha256:"
_INVENTORY_V1 = frozenset({
    "analysis_config.json",
    "producer_runtime.json",
    "equipment_operating_ranges.json",
    "quality_risk_intervals.json",
    "replay_events.csv",
    "analysis_summary.json",
    "bundle_manifest.json",
    "expected_alerts.json",
})
_INVENTORY_V2 = _INVENTORY_V1 - {"expected_alerts.json"}
_ARTIFACTS_V2 = (
    ("analysis_config", "analysis_config.json", "sfep-analysis-config/v2"),
    ("producer_runtime", "producer_runtime.json", "sfep-producer-runtime/v1"),
    (
        "equipment_operating_ranges",
        "equipment_operating_ranges.json",
        "sfep-operating-ranges/v1",
    ),
    (
        "quality_risk_intervals",
        "quality_risk_intervals.json",
        "sfep-quality-rules/v1",
    ),
    ("replay_events", "replay_events.csv", "sfep-replay-events/v1"),
    ("analysis_summary", "analysis_summary.json", "sfep-analysis-summary/v2"),
)
_SCHEMA_DIGESTS_V2 = {
    "analysis_config": "sha256:7942b12abcfeee661290f5b40df922edfeec66d6f95dc1ce20e78cc0f58f5da1",
    "analysis_summary": "sha256:0b38234ea0b7c5614dff0ac7514b6fb82e401a7846621e45bcbcf00ed82dc7c4",
    "bundle_manifest": "sha256:0d7c4fb5e939beb324c6d83005a83cb9702e480a5babdfede88e5ca05776c2c3",
    "equipment_operating_ranges": "sha256:bee7d8be181dae4844c51d4627c5a1f068583b60a60c854f17035a8291cd7d89",
    "producer_runtime": "sha256:97131d80a993d09d17c2c040b0e1cb2bd0eed5948d7a11608f26331d18f557e6",
    "quality_risk_intervals": "sha256:2c8775fec18671030cf58ea0e94a3c99f8dac075fa9d5fe8dce724c2462f42ad",
    "replay_events": "sha256:309749f73cf2a5a522617f975128ac005298f2fd71412903d76263702c1b6bd6",
}
_MANIFEST_KEYS = {
    "artifacts", "asOf", "bundleId", "criteriaId", "criteriaIdentity",
    "identity", "labelMaturityDays", "schemaVersion", "timezone",
}
_REPLAY_HEADER = (
    "schema_version",
    "bundle_id",
    "criteria_id",
    "event_id",
    "replay_date",
    "replay_hour",
    "batch_kind",
    "batch_id",
    "equipment_batch_id",
    "batch_step",
    "time_precision",
    "material_key",
    "equipment_type",
    "equipment_id",
    "charge_id",
    "slab_no",
    "hr_coil_id",
    "ap_prod_id",
    "values_json",
)
_RUNTIME_PROVENANCE_DIFFERENCES = frozenset({
    "locks.producer",
    "locks.pyproject",
    "producer.installedCodeTreeSha256",
    "producer.sourceSha256",
    "producer.version",
    "producer.wheelFilename",
    "producer.wheelSha256",
    "python.executableSha256",
})
_EXPECTED_V2_RUNTIME_PROVENANCE = {
    "locks.producer": "sha256:d643362f633b72f4e92133638981383435aba124f48d132176c8aefcdf9accf1",
    "locks.pyproject": "sha256:80fc0ac36593155d947e7b065736bae63c525ed80060fce717daea92bdd981aa",
    "producer.installedCodeTreeSha256": "sha256:210fc9b61b174cda72e85b2e6eb32b83dcc8642a52718ea93871c91356f59f8a",
    "producer.sourceSha256": "sha256:26eefd1d37546b1aeb1a56158bad06765cb40f21f975a1c2efe569410d5d9a05",
    "producer.version": "1.1.0",
    "producer.wheelFilename": "sfep_equipment_quality-1.1.0-py3-none-any.whl",
    "producer.wheelSha256": "sha256:abdfb2738c4eea6dd9e8c832951deef5faff4795ffbae39c6a9617cac293d514",
    "python.executableSha256": "sha256:68d03d8a962f8a756e3a2ba0fc95a8361719c0548f257f5f069ac090727d1d14",
}
_CONFIG_DIFFERENCES = frozenset({
    "analysisConfigVersion",
    "bootstrap.seedMaterial",
    "bootstrap.seedProtocol",
    "schemaVersion",
})
LINEAGE_CORRECTIONS = frozenset({
    ("analysis_summary", f"holdoutMetrics[].{metric}.{leaf}")
    for metric in ("precision", "lift")
    for leaf in ("lower", "upper", "validReplicates", "reasonCode")
})
ALLOWED_DIFFERENCES = (
    "analysis_config: four exact version/seed fields",
    "producer_runtime: eight exact provenance fields",
    "bundle_manifest: authenticated version/provenance/identity bindings",
    "equipment_operating_ranges.criteriaId",
    "quality_risk_intervals.criteriaId",
    "replay_events[].bundle_id",
    "replay_events[].criteria_id",
    "analysis_summary.schemaVersion",
    "analysis_summary.bundleId",
    "analysis_summary.criteriaId",
    "analysis_summary.lineage.fields: two seed fields",
    "analysis_summary.lineage.fields: exact seed-source substitutions",
    "analysis_summary.lineage.fields: eight enumerated precision/lift corrections",
)


def normalized_event(row: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    copy = dict(row)
    copy["bundle_id"] = "<bundle>"
    copy["criteria_id"] = "<criteria>"
    return tuple(copy.items())


def _sha(payload: bytes) -> str:
    return _SHA256 + hashlib.sha256(payload).hexdigest()


def _inventory(root: Path, expected: frozenset[str]) -> None:
    assert root.is_dir(), f"missing Bundle directory: {root}"
    entries = tuple(root.iterdir())
    assert all(
        path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1
        for path in entries
    )
    assert {path.name for path in entries} == expected


def _json(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    value = json.loads(payload)
    assert type(value) is dict
    assert _canonical_bytes(value) == payload, f"{path.name} is not canonical JSON"
    return value


def _identity(namespace: str, fields: object, expected_keys: set[str]) -> str:
    assert type(fields) is dict
    assert set(fields) == expected_keys
    assert all(type(key) is str and type(value) is str for key, value in fields.items())
    preimage = "\n".join(
        [namespace, *(f"{key}={fields[key]}" for key in sorted(fields, key=str.encode))]
    ) + "\n"
    return _sha(preimage.encode("utf-8"))


def _v2_bundle(
    root: Path,
    v1_manifest: dict[str, object],
) -> tuple[dict[str, object], dict[str, bytes], str, str]:
    _inventory(root, _INVENTORY_V2)
    manifest = _json(root / "bundle_manifest.json")
    assert set(manifest) == _MANIFEST_KEYS
    assert manifest.get("schemaVersion") == "sfep-equipment-bundle/v2"
    entries = manifest.get("artifacts")
    assert type(entries) is list and len(entries) == len(_ARTIFACTS_V2)
    payloads: dict[str, bytes] = {}
    for entry, (role, filename, version) in zip(entries, _ARTIFACTS_V2, strict=True):
        assert type(entry) is dict
        payload = (root / filename).read_bytes()
        assert entry == {
            "role": role,
            "schemaVersion": version,
            "sha256": _sha(payload),
            "sizeBytes": len(payload),
        }
        payloads[role] = payload

    criteria = manifest.get("criteriaIdentity")
    identity = manifest.get("identity")
    criteria_keys = {
        "analysis_config_sha256",
        "as_of",
        "criteria_projection_sha256",
        "producer_runtime_sha256",
        "schema.analysis_config.sha256",
        "schema.equipment_operating_ranges.sha256",
        "schema.producer_runtime.sha256",
        "schema.quality_risk_intervals.sha256",
    }
    identity_keys = {
        "analysis_config_sha256",
        "criteria_id",
        "producer_runtime_sha256",
        *(f"schema.{role}.sha256" for role in (
            "analysis_config",
            "analysis_summary",
            "bundle_manifest",
            "equipment_operating_ranges",
            "producer_runtime",
            "quality_risk_intervals",
            "replay_events",
        )),
        *(f"source.{role}.{field}" for role in ("ap", "fur_hr", "sm_cc")
          for field in ("name", "sha256", "size_bytes")),
    }
    assert type(criteria) is dict and type(identity) is dict
    v1_criteria = v1_manifest["criteriaIdentity"]
    v1_identity = v1_manifest["identity"]
    assert type(v1_criteria) is dict and type(v1_identity) is dict
    expected_criteria = dict(v1_criteria)
    expected_criteria.update({
        "analysis_config_sha256": _sha(payloads["analysis_config"]),
        "producer_runtime_sha256": _sha(payloads["producer_runtime"]),
        "schema.analysis_config.sha256": _SCHEMA_DIGESTS_V2["analysis_config"],
    })
    assert criteria == expected_criteria
    assert criteria["as_of"] == manifest["asOf"] == v1_manifest["asOf"]
    expected_identity = dict(v1_identity)
    criteria_id = _identity("sfep-criteria-id/v1", criteria, criteria_keys)
    expected_identity.update({
        "analysis_config_sha256": _sha(payloads["analysis_config"]),
        "criteria_id": criteria_id,
        "producer_runtime_sha256": _sha(payloads["producer_runtime"]),
        "schema.analysis_config.sha256": _SCHEMA_DIGESTS_V2["analysis_config"],
        "schema.analysis_summary.sha256": _SCHEMA_DIGESTS_V2["analysis_summary"],
        "schema.bundle_manifest.sha256": _SCHEMA_DIGESTS_V2["bundle_manifest"],
    })
    assert identity == expected_identity
    for role, digest in _SCHEMA_DIGESTS_V2.items():
        assert identity[f"schema.{role}.sha256"] == digest
        key = f"schema.{role}.sha256"
        if key in criteria:
            assert criteria[key] == digest
    bundle_id = _identity("sfep-bundle-id/v1", identity, identity_keys)
    assert manifest.get("criteriaId") == criteria_id
    assert manifest.get("bundleId") == bundle_id
    return manifest, payloads, criteria_id, bundle_id


def _diff_paths(left: object, right: object, prefix: str = "") -> set[str]:
    if type(left) is dict and type(right) is dict:
        keys = set(left) | set(right)
        return set().union(*(
            _diff_paths(
                left.get(key, object()),
                right.get(key, object()),
                f"{prefix}.{key}" if prefix else key,
            )
            for key in keys
        )) if keys else set()
    if type(left) is list and type(right) is list:
        if len(left) != len(right):
            return {prefix + ".length"}
        return set().union(*(
            _diff_paths(first, second, f"{prefix}[{index}]")
            for index, (first, second) in enumerate(zip(left, right, strict=True))
        )) if left else set()
    return set() if left == right else {prefix}


def _runtime_parity(v1: dict[str, object], v2: dict[str, object]) -> None:
    assert _diff_paths(v1, v2) == _RUNTIME_PROVENANCE_DIFFERENCES, (
        "runtime provenance difference allowlist is not exact"
    )
    for path, expected in _EXPECTED_V2_RUNTIME_PROVENANCE.items():
        value: object = v2
        for part in path.split("."):
            assert type(value) is dict, (
                f"runtime provenance field {path} is not authenticated"
            )
            value = value.get(part)
        assert value == expected, (
            f"runtime provenance field {path} is not authenticated"
        )


def _validate_v2_schemas(
    objects: Mapping[str, object],
    events: tuple[dict[str, str], ...],
) -> None:
    try:
        assert {
            role: "sha256:" + resource.sha256
            for role, resource in V2_CONTRACT.schema_resources.items()
        } == _SCHEMA_DIGESTS_V2
        for role, value in objects.items():
            validate_contract_instance(V2_CONTRACT, role, value)
        for source in events:
            row: dict[str, object] = dict(source)
            row["replay_hour"] = (
                None if row["replay_hour"] == "" else int(str(row["replay_hour"]))
            )
            for name in ("equipment_batch_id", "hr_coil_id", "ap_prod_id"):
                row[name] = row[name] or None
            row["values_json"] = json.loads(str(row["values_json"]))
            validate_contract_instance(V2_CONTRACT, "replay_events", row)
    except Exception as error:
        raise AssertionError("v2 schema validation failed") from error


def _config_parity(
    v1: dict[str, object], v2: dict[str, object], v1_criteria_id: str
) -> None:
    assert _diff_paths(v1, v2) == _CONFIG_DIFFERENCES
    assert v2["analysisConfigVersion"] == "quality-analysis-v2"
    assert v2["schemaVersion"] == "sfep-analysis-config/v2"
    bootstrap = v2["bootstrap"]
    assert type(bootstrap) is dict
    assert bootstrap["seedMaterial"] == v1_criteria_id
    assert bootstrap["seedProtocol"] == "LEGACY_CRITERIA_ID_UTF8_V1"


def _manifest_parity(
    v1: dict[str, object],
    v2: dict[str, object],
    v1_payloads: dict[str, bytes],
    v2_payloads: dict[str, bytes],
) -> None:
    for key in ("asOf", "labelMaturityDays", "timezone"):
        assert v2[key] == v1[key]
    assert v2["schemaVersion"] == "sfep-equipment-bundle/v2"

    v1_criteria = v1["criteriaIdentity"]
    v2_criteria = v2["criteriaIdentity"]
    assert type(v1_criteria) is dict and type(v2_criteria) is dict
    criteria_changes = {
        "analysis_config_sha256",
        "producer_runtime_sha256",
        "schema.analysis_config.sha256",
    }
    assert set(v1_criteria) == set(v2_criteria)
    assert {
        key: value for key, value in v2_criteria.items() if key not in criteria_changes
    } == {
        key: value for key, value in v1_criteria.items() if key not in criteria_changes
    }

    v1_identity = v1["identity"]
    v2_identity = v2["identity"]
    assert type(v1_identity) is dict and type(v2_identity) is dict
    identity_changes = {
        "analysis_config_sha256",
        "criteria_id",
        "producer_runtime_sha256",
        "schema.analysis_config.sha256",
        "schema.analysis_summary.sha256",
        "schema.bundle_manifest.sha256",
    }
    assert set(v1_identity) == set(v2_identity)
    assert {
        key: value for key, value in v2_identity.items() if key not in identity_changes
    } == {
        key: value for key, value in v1_identity.items() if key not in identity_changes
    }

    assert tuple(item["role"] for item in v2["artifacts"]) == tuple(
        item["role"] for item in v1["artifacts"]
    )
    for entry in v2["artifacts"]:
        role = entry["role"]
        assert entry["sha256"] == _sha(v2_payloads[role])
        assert entry["sizeBytes"] == len(v2_payloads[role])
    for entry in v1["artifacts"]:
        role = entry["role"]
        assert entry["sha256"] == _sha(v1_payloads[role])
        assert entry["sizeBytes"] == len(v1_payloads[role])


def _read_events(
    root: Path,
    *,
    criteria_id: str | None = None,
    bundle_id: str | None = None,
) -> tuple[dict[str, str], ...]:
    payload = (root / "replay_events.csv").read_bytes()
    text = payload.decode("utf-8")
    reader = csv.DictReader(text.splitlines(keepends=True))
    try:
        assert tuple(reader.fieldnames or ()) == _REPLAY_HEADER
        rows = tuple(dict(row) for row in reader)
    except csv.Error as error:
        raise AssertionError("replay CSV is malformed") from error
    rendered = io.StringIO(newline="")
    writer = csv.writer(rendered, lineterminator="\n")
    writer.writerow(_REPLAY_HEADER)
    writer.writerows(tuple(row[column] for column in _REPLAY_HEADER) for row in rows)
    assert rendered.getvalue().encode("utf-8") == payload, "replay CSV is not canonical"
    for ordinal, row in enumerate(rows, start=2):
        assert tuple(row) == _REPLAY_HEADER
        assert row["schema_version"] == "sfep-replay-events/v1"
        if criteria_id is not None:
            assert row["criteria_id"] == criteria_id
        if bundle_id is not None:
            assert row["bundle_id"] == bundle_id
        values = json.loads(row["values_json"])
        assert type(values) is dict
        assert _canonical_bytes(values)[:-1].decode("utf-8") == row["values_json"], (
            f"row {ordinal} values_json is not canonical"
        )
    return rows


def _lineage_expected(v1_fields: list[object]) -> list[dict[str, object]]:
    assert all(type(item) is dict for item in v1_fields)
    fields = copy.deepcopy(v1_fields)
    keyed = {
        (item["artifactRole"], item["outputField"]): item
        for item in fields
    }
    assert len(keyed) == len(fields)
    seed_pair = {
        "config.bootstrap.seedMaterial",
        "config.bootstrap.seedProtocol",
    }
    confusion = set(
        keyed[("analysis_summary", "holdoutMetrics[].truePositive")]["dependencies"]
    )
    for key, item in keyed.items():
        dependencies = set(item["dependencies"])
        if (
            item["conversion"]
            in {"COMPUTE_QUALITY_METRIC", "COMPUTE_HOLDOUT_METRIC"}
            and "identity.criteria_id" in dependencies
        ):
            dependencies.remove("identity.criteria_id")
            dependencies.update(seed_pair)
            item["dependencies"] = sorted(dependencies, key=str.encode)
        if key in LINEAGE_CORRECTIONS:
            metric, leaf = item["outputField"].removeprefix(
                "holdoutMetrics[]."
            ).split(".", 1)
            point = set(
                keyed[
                    ("analysis_summary", f"holdoutMetrics[].{metric}.pointEstimate")
                ]["dependencies"]
            )
            dependencies = point | confusion | seed_pair | {
                "config.bootstrap.replicates",
                "replay_events.charge_id",
            }
            if leaf != "validReplicates":
                dependencies.add("config.bootstrap.minimumValidReplicates")
            item["dependencies"] = sorted(dependencies, key=str.encode)

    for output_field in ("bootstrap.seedMaterial", "bootstrap.seedProtocol"):
        fields.append({
            "artifactRole": "analysis_config",
            "conversion": "COPY_CANONICAL_CONFIG",
            "dependencies": ["config." + output_field],
            "firstAvailableStage": None,
            "outputField": output_field,
            "sourceColumn": None,
            "sourceRole": None,
        })
    role_rank = {
        role: index
        for index, role in enumerate((
            "bundle_manifest",
            "analysis_config",
            "producer_runtime",
            "equipment_operating_ranges",
            "quality_risk_intervals",
            "replay_events",
            "analysis_summary",
        ))
    }
    return sorted(
        fields,
        key=lambda item: (
            role_rank[item["artifactRole"]],
            item["outputField"].encode("utf-8"),
        ),
    )


def _summary_parity(
    v1: dict[str, object],
    v2: dict[str, object],
    *,
    criteria_id: str,
    bundle_id: str,
) -> None:
    assert v2["criteriaId"] == criteria_id
    assert v2["bundleId"] == bundle_id
    expected = copy.deepcopy(v1)
    expected["schemaVersion"] = "sfep-analysis-summary/v2"
    expected["bundleId"] = v2["bundleId"]
    expected["criteriaId"] = v2["criteriaId"]
    lineage = expected["lineage"]
    assert type(lineage) is dict
    lineage["fields"] = _lineage_expected(lineage["fields"])
    assert v2 == expected


def assert_semantic_parity(v1_bundle: Path, v2_bundle: Path) -> None:
    """Assert the complete, narrowly allowlisted golden v1-to-v2 migration."""
    _inventory(v1_bundle, _INVENTORY_V1)
    verified_v1 = verify_v1_bundle(v1_bundle)
    v1_manifest = _json(v1_bundle / "bundle_manifest.json")
    v2_manifest, v2_payloads, v2_criteria_id, v2_bundle_id = _v2_bundle(
        v2_bundle, v1_manifest
    )
    v1_payloads = {
        role: (v1_bundle / filename).read_bytes()
        for role, filename, _version in (
            ("analysis_config", "analysis_config.json", ""),
            ("producer_runtime", "producer_runtime.json", ""),
            ("equipment_operating_ranges", "equipment_operating_ranges.json", ""),
            ("quality_risk_intervals", "quality_risk_intervals.json", ""),
            ("replay_events", "replay_events.csv", ""),
            ("analysis_summary", "analysis_summary.json", ""),
        )
    }

    v1_config = _json(v1_bundle / "analysis_config.json")
    v2_config = _json(v2_bundle / "analysis_config.json")
    v2_schema_objects: dict[str, object] = {
        "bundle_manifest": v2_manifest,
        "analysis_config": v2_config,
    }
    _config_parity(v1_config, v2_config, verified_v1.criteria_id)
    v1_runtime = _json(v1_bundle / "producer_runtime.json")
    v2_runtime = _json(v2_bundle / "producer_runtime.json")
    v2_schema_objects["producer_runtime"] = v2_runtime
    _runtime_parity(v1_runtime, v2_runtime)
    _manifest_parity(v1_manifest, v2_manifest, v1_payloads, v2_payloads)

    for filename, role, binding in (
        (
            "equipment_operating_ranges.json",
            "equipment_operating_ranges",
            "criteriaId",
        ),
        (
            "quality_risk_intervals.json",
            "quality_risk_intervals",
            "criteriaId",
        ),
    ):
        before = _json(v1_bundle / filename)
        after = _json(v2_bundle / filename)
        assert after["criteriaId"] == v2_criteria_id
        assert after["asOf"] == v2_manifest["asOf"]
        expected = copy.deepcopy(before)
        expected[binding] = v2_criteria_id
        assert after == expected
        v2_schema_objects[role] = after

    v1_events = _read_events(v1_bundle)
    v2_events = _read_events(
        v2_bundle, criteria_id=v2_criteria_id, bundle_id=v2_bundle_id
    )
    assert len(v2_events) == len(v1_events)
    assert tuple(map(normalized_event, v2_events)) == tuple(
        map(normalized_event, v1_events)
    )

    v1_summary = _json(v1_bundle / "analysis_summary.json")
    v2_summary = _json(v2_bundle / "analysis_summary.json")
    v2_schema_objects["analysis_summary"] = v2_summary
    _validate_v2_schemas(v2_schema_objects, v2_events)
    assert v2_summary["asOf"] == v2_manifest["asOf"]
    _summary_parity(
        v1_summary,
        v2_summary,
        criteria_id=v2_criteria_id,
        bundle_id=v2_bundle_id,
    )
    alerts = _json(v1_bundle / "expected_alerts.json")
    assert alerts["alerts"] == []
    assert alerts["expectedReplayEventCount"] == len(v2_events)


__all__ = [
    "ALLOWED_DIFFERENCES",
    "LINEAGE_CORRECTIONS",
    "assert_semantic_parity",
    "normalized_event",
]
