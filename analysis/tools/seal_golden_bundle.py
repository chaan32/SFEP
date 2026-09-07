#!/usr/bin/env python3
"""Seal the reviewed golden Bundle without importing its producer."""

from __future__ import annotations

import argparse
import csv
import ctypes
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Callable, Mapping, Sequence


_EXPECTATION_LITERALS = {
    "analysis_summary.template.json": (
        383700,
        "20acdfe66fc1167340c58f2ff91a00621725bace0b3b65832ab8629e51e04f1d",
    ),
    "criteria_projection.jsonl": (
        79246,
        "6842fabd1cc79ca801e34bb0708498f4bea16317d4449e6d773e28e40c94ff5c",
    ),
    "equipment_operating_ranges.template.json": (
        106,
        "77fa2a0665d6d5bf0cbd754b56a86c38b159cfa26abee6685fc910cf7e546581",
    ),
    "expected_alerts.json": (
        1244,
        "2c3bb6bc8e29fe077e71fd8f1b54efaa272ca31ae2676e87be8bcf549bc550ed",
    ),
    "quality_risk_intervals.template.json": (
        260272,
        "d47e7d8c230f31f56719056fc3a79505847863a51e686b1aac1aee9505143996",
    ),
    "replay_events.template.csv": (
        47904,
        "c516b9f8cebc2642b9c9b75a3bc12f1608df651522ccb2a65c2eb5c1424ce17c",
    ),
}
_SOURCE_LITERALS = {
    "sts_1sm_cc_1.csv": (
        1056,
        "c3bba5c7235166b6693ff657a25797933ea9d83c6392437af9b5141f4e56a68c",
    ),
    "sts_2fur_hr_2.csv": (
        1866,
        "973e864ed996ca48c44a00943efc0ea51a878b94b8aac7452c7254a572fe7e67",
    ),
    "sts_3ap_3.csv": (
        711,
        "efaef9f283a28e63dff54b26bc3dd42c6a72e20f0b75d2fa13a6ab47f41405c9",
    ),
}
_SCHEMA_LITERALS = {
    "analysis_config.schema.json": (
        5073,
        "fad28561dfe9d9fe3cd09b025bb18c2101be053cb094b08442ea45963b86f549",
    ),
    "analysis_summary.schema.json": (
        40096,
        "6cb6077cc7093b90e464cc794604ca6b7682002a138ebed051801aee29f9d374",
    ),
    "bundle_manifest.schema.json": (
        5512,
        "666e880d296c0d7e3df5af1aa80e6865922ebfb337fb9aca48f93eddfd89e8a5",
    ),
    "equipment_operating_ranges.schema.json": (
        2545,
        "bee7d8be181dae4844c51d4627c5a1f068583b60a60c854f17035a8291cd7d89",
    ),
    "producer_runtime.schema.json": (
        2916,
        "97131d80a993d09d17c2c040b0e1cb2bd0eed5948d7a11608f26331d18f557e6",
    ),
    "quality_risk_intervals.schema.json": (
        5319,
        "2c8775fec18671030cf58ea0e94a3c99f8dac075fa9d5fe8dce724c2462f42ad",
    ),
    "replay_event_row.schema.json": (
        7865,
        "309749f73cf2a5a522617f975128ac005298f2fd71412903d76263702c1b6bd6",
    ),
}
_CONFIG_LITERAL = (
    13830,
    "bb2610971dc1b3adcb4e93b9d26a50fdb4070292ffc4bf4a26ce7cdde0f5fb0b",
)
_RUNTIME_LITERAL = (
    5537,
    "3c1daabb3e868fc6cebc75023980fcb71d947996170de6d8e58cd06b18231407",
)
_SOURCE_ROLES = (
    ("sm_cc", "sts_1sm_cc_1.csv"),
    ("fur_hr", "sts_2fur_hr_2.csv"),
    ("ap", "sts_3ap_3.csv"),
)
_SCHEMA_ROLES = (
    ("analysis_config", "analysis_config.schema.json"),
    ("analysis_summary", "analysis_summary.schema.json"),
    ("bundle_manifest", "bundle_manifest.schema.json"),
    ("equipment_operating_ranges", "equipment_operating_ranges.schema.json"),
    ("producer_runtime", "producer_runtime.schema.json"),
    ("quality_risk_intervals", "quality_risk_intervals.schema.json"),
    ("replay_events", "replay_event_row.schema.json"),
)
_CRITERIA_SCHEMA_ROLES = (
    "analysis_config",
    "equipment_operating_ranges",
    "producer_runtime",
    "quality_risk_intervals",
)
_ARTIFACTS = (
    ("analysis_config", "analysis_config.json", "sfep-analysis-config/v1"),
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
    ("analysis_summary", "analysis_summary.json", "sfep-analysis-summary/v1"),
)
_OUTPUT_NAMES = tuple(name for _role, name, _version in _ARTIFACTS) + (
    "bundle_manifest.json",
    "expected_alerts.json",
)
_TEMPLATE_TOKENS = {
    "criteria_projection.jsonl": {},
    "equipment_operating_ranges.template.json": {"CRITERIA_ID": 1},
    "quality_risk_intervals.template.json": {"CRITERIA_ID": 1},
    "replay_events.template.csv": {"BUNDLE_ID": 95, "CRITERIA_ID": 95},
    "analysis_summary.template.json": {"BUNDLE_ID": 1, "CRITERIA_ID": 1},
}
_ALERT_TOKEN_NAMES = (
    "ARTIFACT_ANALYSIS_CONFIG_SHA256",
    "ARTIFACT_ANALYSIS_SUMMARY_SHA256",
    "ARTIFACT_EQUIPMENT_OPERATING_RANGES_SHA256",
    "ARTIFACT_PRODUCER_RUNTIME_SHA256",
    "ARTIFACT_QUALITY_RISK_INTERVALS_SHA256",
    "ARTIFACT_REPLAY_EVENTS_SHA256",
    "BUNDLE_ID",
    "CRITERIA_ID",
    "PRODUCER_RUNTIME_SHA256",
    "SCHEMA_ANALYSIS_CONFIG_SHA256",
    "SCHEMA_ANALYSIS_SUMMARY_SHA256",
    "SCHEMA_BUNDLE_MANIFEST_SHA256",
    "SCHEMA_EQUIPMENT_OPERATING_RANGES_SHA256",
    "SCHEMA_PRODUCER_RUNTIME_SHA256",
    "SCHEMA_QUALITY_RISK_INTERVALS_SHA256",
    "SCHEMA_REPLAY_EVENTS_SHA256",
    "SOURCE_AP_SHA256",
    "SOURCE_FUR_HR_SHA256",
    "SOURCE_SM_CC_SHA256",
)
_TOKEN_PATTERN = re.compile(rb"@([A-Z0-9_]+)@")
_REPLAY_HEADER = (
    "schema_version,bundle_id,criteria_id,event_id,replay_date,replay_hour,"
    "batch_kind,batch_id,equipment_batch_id,batch_step,time_precision,"
    "material_key,equipment_type,equipment_id,charge_id,slab_no,hr_coil_id,"
    "ap_prod_id,values_json"
)
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_SCRATCH_PREFIX = ".sfep-golden-bundle-"


class GoldenSealError(RuntimeError):
    """A fail-closed golden seal error."""


@dataclass(frozen=True)
class _FileCapture:
    payload: bytes
    identity: tuple[int, int, int, int, int]


@dataclass(frozen=True)
class _Capture:
    files: Mapping[str, _FileCapture]
    directory_identities: Mapping[str, tuple[int, int]]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_uri(payload: bytes) -> str:
    return "sha256:" + _sha256(payload)


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _reject_constant(value: str) -> object:
    raise GoldenSealError(f"non-finite JSON number is forbidden: {value}")


def _pairs_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GoldenSealError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_json_object(payload: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GoldenSealError(f"{label} must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise GoldenSealError(f"{label} JSON top level must be an object")
    if (
        not payload.endswith(b"\n")
        or b"\r" in payload
        or b"\n" in payload[:-1]
        or not payload.startswith(b"{")
        or not payload.endswith(b"}\n")
    ):
        raise GoldenSealError(f"{label} must use canonical one-LF JSON shape")
    return value


def _path_from_argument(value: str, label: str) -> Path:
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise GoldenSealError(f"{label} contains a control character")
    path = Path(value)
    if not path.is_absolute():
        raise GoldenSealError(f"{label} must be absolute")
    if ".." in path.parts or os.path.normpath(value) != value:
        raise GoldenSealError(f"{label} must be a normalized absolute path")
    return path


def _assert_components(path: Path, *, final_may_be_missing: bool = False) -> None:
    parts = path.parts
    current = Path(parts[0])
    for index, part in enumerate(parts[1:], start=1):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if final_may_be_missing and index == len(parts) - 1:
                return
            raise GoldenSealError(f"missing path component: {current}") from None
        if stat.S_ISLNK(metadata.st_mode):
            raise GoldenSealError(f"symlink path component is forbidden: {current}")
        if index < len(parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise GoldenSealError(f"non-directory path component: {current}")


def _directory_identity(path: Path, label: str) -> tuple[int, int]:
    _assert_components(path)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise GoldenSealError(f"{label} must be a directory")
    return metadata.st_dev, metadata.st_ino


def _read_regular(path: Path, label: str) -> _FileCapture:
    _assert_components(path)
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode):
        raise GoldenSealError(f"authenticated {label} must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise GoldenSealError(f"cannot open authenticated {label}: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise GoldenSealError(f"authenticated {label} must be a regular file")
        if (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino):
            raise GoldenSealError(f"authenticated {label} path changed before open")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if identity != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise GoldenSealError(f"authenticated {label} changed during capture")
    current = path.lstat()
    if (current.st_dev, current.st_ino) != identity[:2]:
        raise GoldenSealError(f"authenticated {label} path was replaced during capture")
    payload = b"".join(chunks)
    if len(payload) != before.st_size:
        raise GoldenSealError(f"authenticated {label} size changed during capture")
    return _FileCapture(payload, identity)


def _check_literal(capture: _FileCapture, literal: tuple[int, str], label: str) -> None:
    size, digest = literal
    actual_digest = _sha256(capture.payload)
    if len(capture.payload) != size:
        raise GoldenSealError(
            f"pinned {label} size mismatch: {len(capture.payload)} != {size}"
        )
    if actual_digest != digest:
        raise GoldenSealError(f"pinned {label} sha256 mismatch")


def _exact_inventory(path: Path, expected: set[str], label: str) -> None:
    _directory_identity(path, label)
    try:
        entries = tuple(os.scandir(path))
    except OSError as error:
        raise GoldenSealError(f"cannot scan {label} inventory: {error}") from error
    actual = {entry.name for entry in entries}
    if actual != expected or len(entries) != len(expected):
        raise GoldenSealError(f"{label} inventory must be exactly {sorted(expected)!r}")
    for entry in entries:
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            raise GoldenSealError(f"{label} inventory contains a non-regular entry")


def _validate_topology(
    contract_root: Path,
    analysis_config: Path,
    runtime_manifest: Path,
    output_dir: Path,
) -> None:
    for path, label in (
        (contract_root, "contract root"),
        (analysis_config, "analysis config"),
        (runtime_manifest, "runtime manifest"),
    ):
        _assert_components(path)
        if path.is_symlink():
            raise GoldenSealError(f"{label} must not be a symlink")
    _assert_components(output_dir.parent)
    _assert_components(output_dir, final_may_be_missing=True)
    resolved_contract = contract_root.resolve(strict=True)
    resolved_config = analysis_config.resolve(strict=True)
    resolved_runtime = runtime_manifest.resolve(strict=True)
    resolved_output = output_dir.parent.resolve(strict=True) / output_dir.name
    if len({resolved_contract, resolved_config, resolved_runtime, resolved_output}) != 4:
        raise GoldenSealError("input and output paths must not alias")
    protected = (
        resolved_contract / "golden-expectation",
        resolved_contract / "golden-source",
        resolved_config,
        resolved_runtime,
        *(resolved_contract / name for name in _SCHEMA_LITERALS),
    )
    for path in protected:
        if resolved_output == path or path in resolved_output.parents:
            raise GoldenSealError("output overlaps authenticated semantic input")
        if resolved_output in path.parents:
            raise GoldenSealError("output contains an authenticated semantic input")


def _capture_once(
    contract_root: Path,
    analysis_config: Path,
    runtime_manifest: Path,
) -> _Capture:
    expectation_root = contract_root / "golden-expectation"
    source_root = contract_root / "golden-source"
    _exact_inventory(
        expectation_root, set(_EXPECTATION_LITERALS), "golden expectation"
    )
    _exact_inventory(source_root, set(_SOURCE_LITERALS), "golden source")
    _directory_identity(contract_root, "contract root")
    try:
        schema_names = {
            entry.name
            for entry in os.scandir(contract_root)
            if entry.name.endswith(".schema.json")
        }
    except OSError as error:
        raise GoldenSealError(f"cannot scan schema inventory: {error}") from error
    if schema_names != set(_SCHEMA_LITERALS):
        raise GoldenSealError("schema inventory does not match the seven pinned schemas")

    files: dict[str, _FileCapture] = {}
    for name, literal in _EXPECTATION_LITERALS.items():
        key = f"expectation/{name}"
        capture = _read_regular(expectation_root / name, key)
        _check_literal(capture, literal, key)
        files[key] = capture
    for name, literal in _SOURCE_LITERALS.items():
        key = f"source/{name}"
        capture = _read_regular(source_root / name, key)
        _check_literal(capture, literal, key)
        files[key] = capture
    for name, literal in _SCHEMA_LITERALS.items():
        key = f"schema/{name}"
        capture = _read_regular(contract_root / name, key)
        _check_literal(capture, literal, key)
        files[key] = capture
    config = _read_regular(analysis_config, "analysis config")
    runtime = _read_regular(runtime_manifest, "runtime manifest")
    _check_literal(config, _CONFIG_LITERAL, "analysis config")
    _check_literal(runtime, _RUNTIME_LITERAL, "runtime manifest")
    files["analysis_config"] = config
    files["producer_runtime"] = runtime

    _canonical_json_object(config.payload, "analysis config")
    _canonical_json_object(runtime.payload, "runtime manifest")
    for name in _SCHEMA_LITERALS:
        _canonical_json_object(files[f"schema/{name}"].payload, name)
    _exact_inventory(
        expectation_root, set(_EXPECTATION_LITERALS), "golden expectation"
    )
    _exact_inventory(source_root, set(_SOURCE_LITERALS), "golden source")
    return _Capture(
        files=files,
        directory_identities={
            "contract": _directory_identity(contract_root, "contract root"),
            "expectation": _directory_identity(
                expectation_root, "golden expectation"
            ),
            "source": _directory_identity(source_root, "golden source"),
        },
    )


def _capture_inputs(
    contract_root: Path,
    analysis_config: Path,
    runtime_manifest: Path,
) -> _Capture:
    first = _capture_once(contract_root, analysis_config, runtime_manifest)
    second = _capture_once(contract_root, analysis_config, runtime_manifest)
    if not _same_capture(first, second):
        raise GoldenSealError("joint authenticated input capture changed")
    return second


def _validate_tokens(payload: bytes, expected: Mapping[str, int], label: str) -> None:
    names = [match.group(1).decode("ascii") for match in _TOKEN_PATTERN.finditer(payload)]
    counts = {name: names.count(name) for name in set(names)}
    if counts != dict(expected):
        raise GoldenSealError(f"{label} token counts do not match the approved boundary")


def _substitute(
    payload: bytes,
    substitutions: Mapping[str, str],
    counts: Mapping[str, int],
    label: str,
) -> bytes:
    _validate_tokens(payload, counts, label)
    rendered_parts: list[bytes] = []
    recovered_parts: list[bytes] = []
    cursor = 0
    for match in _TOKEN_PATTERN.finditer(payload):
        name = match.group(1).decode("ascii")
        if name not in substitutions:
            raise GoldenSealError(f"{label} contains an unapproved token")
        unchanged = payload[cursor : match.start()]
        rendered_parts.extend((unchanged, substitutions[name].encode("ascii")))
        recovered_parts.extend((unchanged, match.group(0)))
        cursor = match.end()
    rendered_parts.append(payload[cursor:])
    recovered_parts.append(payload[cursor:])
    rendered = b"".join(rendered_parts)
    if _TOKEN_PATTERN.search(rendered):
        raise GoldenSealError(f"{label} contains an unresolved token")
    if b"".join(recovered_parts) != payload:
        raise GoldenSealError(f"{label} substitution is not byte reversible")
    return rendered


def _identity(version: str, fields: Mapping[str, str]) -> str:
    if len(fields) != len(set(fields)):
        raise GoldenSealError("identity fields must be unique")
    lines = version + "\n" + "".join(
        f"{key}={fields[key]}\n"
        for key in sorted(fields, key=lambda value: value.encode("utf-8"))
    )
    return _sha256_uri(lines.encode("utf-8"))


def _payload(capture: _Capture, key: str) -> bytes:
    return capture.files[key].payload


def _prepare_outputs(capture: _Capture) -> tuple[str, dict[str, bytes]]:
    config = _payload(capture, "analysis_config")
    runtime = _payload(capture, "producer_runtime")
    schema_digests = {
        role: _sha256_uri(_payload(capture, f"schema/{name}"))
        for role, name in _SCHEMA_ROLES
    }
    criteria_fields = {
        "analysis_config_sha256": _sha256_uri(config),
        "as_of": "2025-02-20",
        "criteria_projection_sha256": _sha256_uri(
            _payload(capture, "expectation/criteria_projection.jsonl")
        ),
        "producer_runtime_sha256": _sha256_uri(runtime),
        **{
            f"schema.{role}.sha256": schema_digests[role]
            for role in _CRITERIA_SCHEMA_ROLES
        },
    }
    if len(criteria_fields) != 8:
        raise GoldenSealError("criteria identity must contain exactly eight fields")
    criteria_id = _identity("sfep-criteria-id/v1", criteria_fields)

    bundle_fields = {
        "analysis_config_sha256": _sha256_uri(config),
        "criteria_id": criteria_id,
        "producer_runtime_sha256": _sha256_uri(runtime),
    }
    for role, name in _SOURCE_ROLES:
        source_payload = _payload(capture, f"source/{name}")
        bundle_fields[f"source.{role}.name"] = name
        bundle_fields[f"source.{role}.sha256"] = _sha256_uri(source_payload)
        bundle_fields[f"source.{role}.size_bytes"] = str(len(source_payload))
    bundle_fields.update(
        {f"schema.{role}.sha256": schema_digests[role] for role, _ in _SCHEMA_ROLES}
    )
    if len(bundle_fields) != 19:
        raise GoldenSealError("bundle identity must contain exactly nineteen fields")
    bundle_id = _identity("sfep-bundle-id/v1", bundle_fields)
    id_values = {"BUNDLE_ID": bundle_id, "CRITERIA_ID": criteria_id}

    for template_name, counts in _TEMPLATE_TOKENS.items():
        _validate_tokens(
            _payload(capture, f"expectation/{template_name}"),
            counts,
            template_name,
        )
    outputs = {
        "analysis_config.json": config,
        "producer_runtime.json": runtime,
        "equipment_operating_ranges.json": _substitute(
            _payload(
                capture, "expectation/equipment_operating_ranges.template.json"
            ),
            id_values,
            _TEMPLATE_TOKENS["equipment_operating_ranges.template.json"],
            "equipment operating ranges",
        ),
        "quality_risk_intervals.json": _substitute(
            _payload(capture, "expectation/quality_risk_intervals.template.json"),
            id_values,
            _TEMPLATE_TOKENS["quality_risk_intervals.template.json"],
            "quality risk intervals",
        ),
        "replay_events.csv": _substitute(
            _payload(capture, "expectation/replay_events.template.csv"),
            id_values,
            _TEMPLATE_TOKENS["replay_events.template.csv"],
            "replay events",
        ),
        "analysis_summary.json": _substitute(
            _payload(capture, "expectation/analysis_summary.template.json"),
            id_values,
            _TEMPLATE_TOKENS["analysis_summary.template.json"],
            "analysis summary",
        ),
    }
    for name in (
        "analysis_config.json",
        "producer_runtime.json",
        "equipment_operating_ranges.json",
        "quality_risk_intervals.json",
        "analysis_summary.json",
    ):
        _canonical_json_object(outputs[name], name)
    _validate_replay(outputs["replay_events.csv"], bundle_id, criteria_id)

    artifact_metadata = [
        {
            "role": role,
            "schemaVersion": schema_version,
            "sha256": _sha256_uri(outputs[name]),
            "sizeBytes": len(outputs[name]),
        }
        for role, name, schema_version in _ARTIFACTS
    ]
    manifest = {
        "artifacts": artifact_metadata,
        "asOf": "2025-02-20",
        "bundleId": bundle_id,
        "criteriaId": criteria_id,
        "criteriaIdentity": criteria_fields,
        "identity": bundle_fields,
        "labelMaturityDays": 38,
        "schemaVersion": "sfep-equipment-bundle/v1",
        "timezone": "Asia/Seoul",
    }
    outputs["bundle_manifest.json"] = _canonical_json_bytes(manifest)

    source_values = {
        role: _sha256_uri(_payload(capture, f"source/{name}"))
        for role, name in _SOURCE_ROLES
    }
    alert_values = {
        "ARTIFACT_ANALYSIS_CONFIG_SHA256": _sha256_uri(
            outputs["analysis_config.json"]
        ),
        "ARTIFACT_ANALYSIS_SUMMARY_SHA256": _sha256_uri(
            outputs["analysis_summary.json"]
        ),
        "ARTIFACT_EQUIPMENT_OPERATING_RANGES_SHA256": _sha256_uri(
            outputs["equipment_operating_ranges.json"]
        ),
        "ARTIFACT_PRODUCER_RUNTIME_SHA256": _sha256_uri(
            outputs["producer_runtime.json"]
        ),
        "ARTIFACT_QUALITY_RISK_INTERVALS_SHA256": _sha256_uri(
            outputs["quality_risk_intervals.json"]
        ),
        "ARTIFACT_REPLAY_EVENTS_SHA256": _sha256_uri(outputs["replay_events.csv"]),
        "BUNDLE_ID": bundle_id,
        "CRITERIA_ID": criteria_id,
        "PRODUCER_RUNTIME_SHA256": _sha256_uri(runtime),
        "SCHEMA_ANALYSIS_CONFIG_SHA256": schema_digests["analysis_config"],
        "SCHEMA_ANALYSIS_SUMMARY_SHA256": schema_digests["analysis_summary"],
        "SCHEMA_BUNDLE_MANIFEST_SHA256": schema_digests["bundle_manifest"],
        "SCHEMA_EQUIPMENT_OPERATING_RANGES_SHA256": schema_digests[
            "equipment_operating_ranges"
        ],
        "SCHEMA_PRODUCER_RUNTIME_SHA256": schema_digests["producer_runtime"],
        "SCHEMA_QUALITY_RISK_INTERVALS_SHA256": schema_digests[
            "quality_risk_intervals"
        ],
        "SCHEMA_REPLAY_EVENTS_SHA256": schema_digests["replay_events"],
        "SOURCE_AP_SHA256": source_values["ap"],
        "SOURCE_FUR_HR_SHA256": source_values["fur_hr"],
        "SOURCE_SM_CC_SHA256": source_values["sm_cc"],
    }
    if tuple(sorted(alert_values)) != tuple(sorted(_ALERT_TOKEN_NAMES)):
        raise GoldenSealError("alert provenance token map is incomplete")
    alerts_template = _payload(capture, "expectation/expected_alerts.json")
    outputs["expected_alerts.json"] = _substitute(
        alerts_template,
        alert_values,
        {name: 1 for name in _ALERT_TOKEN_NAMES},
        "expected alerts",
    )
    alerts = _canonical_json_object(outputs["expected_alerts.json"], "expected alerts")
    if alerts.get("alerts") != [] or alerts.get("expectedReplayEventCount") != 95:
        raise GoldenSealError("expected alerts semantic boundary is not empty/95")
    _canonical_json_object(outputs["bundle_manifest.json"], "bundle manifest")
    if tuple(outputs) != _OUTPUT_NAMES:
        raise GoldenSealError("output inventory/order is not exact")
    return bundle_id, outputs


def _validate_replay(payload: bytes, bundle_id: str, criteria_id: str) -> None:
    if not payload.endswith(b"\n") or b"\r" in payload:
        raise GoldenSealError("replay CSV must use one-LF rows")
    try:
        text = payload.decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(text, newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error) as error:
        raise GoldenSealError("replay CSV is malformed") from error
    if text.split("\n", 1)[0] != _REPLAY_HEADER:
        raise GoldenSealError("replay CSV header is not exact")
    expected_fields = _REPLAY_HEADER.split(",")
    if len(rows) != 95:
        raise GoldenSealError("replay CSV must contain exactly 95 data rows")
    for row in rows:
        if list(row) != expected_fields or None in row:
            raise GoldenSealError("replay CSV row shape is not exact")
        if row["bundle_id"] != bundle_id or row["criteria_id"] != criteria_id:
            raise GoldenSealError("replay CSV identity columns are not exact")


def _same_capture(first: _Capture, second: _Capture) -> bool:
    return (
        first.directory_identities == second.directory_identities
        and first.files == second.files
    )


def _write_file(
    path: Path,
    payload: bytes,
    directory_fd: int | None = None,
) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    target: str | Path = path if directory_fd is None else path.name
    descriptor = os.open(target, flags, _FILE_MODE, dir_fd=directory_fd)
    try:
        os.fchmod(descriptor, _FILE_MODE)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise GoldenSealError("short write while publishing an artifact")
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != len(payload):
            raise GoldenSealError("published scratch artifact has the wrong identity")
    finally:
        os.close(descriptor)


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise GoldenSealError("opened publication parent must be a directory")
    return descriptor


def _directory_fd_identity(descriptor: int) -> tuple[int, int]:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise GoldenSealError("retained publication descriptor is not a directory")
    return metadata.st_dev, metadata.st_ino


def _assert_path_matches_directory_fd(
    path: Path,
    descriptor: int,
    expected: tuple[int, int],
    label: str,
) -> None:
    current_fd = _directory_fd_identity(descriptor)
    try:
        current_path = path.lstat()
    except FileNotFoundError:
        raise GoldenSealError(f"retained {label} path disappeared") from None
    if not stat.S_ISDIR(current_path.st_mode) or stat.S_ISLNK(current_path.st_mode):
        raise GoldenSealError(f"retained {label} path was replaced")
    if current_fd != expected or (current_path.st_dev, current_path.st_ino) != expected:
        raise GoldenSealError(f"retained {label} identity changed")


def _entry_metadata(directory_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _read_regular_at(directory_fd: int, name: str, label: str) -> _FileCapture:
    initial = _entry_metadata(directory_fd, name)
    if initial is None or not stat.S_ISREG(initial.st_mode):
        raise GoldenSealError(f"{label} must be a retained regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        before = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino):
            raise GoldenSealError(f"{label} changed before retained open")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if identity != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise GoldenSealError(f"{label} changed during retained read")
    final = _entry_metadata(directory_fd, name)
    if final is None or (final.st_dev, final.st_ino) != identity[:2]:
        raise GoldenSealError(f"{label} name was replaced during retained read")
    payload = b"".join(chunks)
    if len(payload) != before.st_size:
        raise GoldenSealError(f"{label} size changed during retained read")
    return _FileCapture(payload, identity)


def _verify_directory_fd(
    descriptor: int,
    expected_identity: tuple[int, int],
    outputs: Mapping[str, bytes],
) -> None:
    if _directory_fd_identity(descriptor) != expected_identity:
        raise GoldenSealError("retained scratch directory identity changed")
    names_before = set(os.listdir(descriptor))
    if names_before != set(_OUTPUT_NAMES):
        raise GoldenSealError("retained scratch inventory is not exact")
    for name in _OUTPUT_NAMES:
        capture = _read_regular_at(descriptor, name, f"retained scratch/{name}")
        if capture.payload != outputs[name]:
            raise GoldenSealError(f"retained scratch bytes differ at {name}")
        metadata = _entry_metadata(descriptor, name)
        if metadata is None or stat.S_IMODE(metadata.st_mode) != _FILE_MODE:
            raise GoldenSealError(f"retained scratch mode differs at {name}")
    if set(os.listdir(descriptor)) != names_before:
        raise GoldenSealError("retained scratch inventory changed during verification")
    if _directory_fd_identity(descriptor) != expected_identity:
        raise GoldenSealError("retained scratch directory was replaced")


def _create_retained_scratch(
    parent_fd: int,
    parent_path: Path,
) -> tuple[Path, int, tuple[int, int]]:
    scratch_name = ""
    for _attempt in range(128):
        candidate = _SCRATCH_PREFIX + secrets.token_hex(12)
        previous_umask = os.umask(0)
        try:
            try:
                os.mkdir(candidate, _DIRECTORY_MODE, dir_fd=parent_fd)
            finally:
                os.umask(previous_umask)
        except FileExistsError:
            continue
        scratch_name = candidate
        break
    if not scratch_name:
        raise GoldenSealError("could not allocate a unique retained scratch name")
    scratch_path = parent_path / scratch_name
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        scratch_fd = os.open(scratch_name, flags, dir_fd=parent_fd)
    except BaseException as error:
        raise GoldenSealError(
            f"could not retain scratch descriptor; unclaimed path is {scratch_path}"
        ) from error
    os.fchmod(scratch_fd, _DIRECTORY_MODE)
    identity = _directory_fd_identity(scratch_fd)
    entry = _entry_metadata(parent_fd, scratch_name)
    if entry is None or not stat.S_ISDIR(entry.st_mode):
        os.close(scratch_fd)
        raise GoldenSealError(f"scratch entry changed; unclaimed path is {scratch_path}")
    if (entry.st_dev, entry.st_ino) != identity:
        os.close(scratch_fd)
        raise GoldenSealError(f"scratch identity changed; unclaimed path is {scratch_path}")
    return scratch_path, scratch_fd, identity


def _atomic_install_exclusive(
    source_dir_fd: int,
    source_name: str,
    target_dir_fd: int,
    target_name: str,
) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source_name)
    target_bytes = os.fsencode(target_name)
    if hasattr(library, "renameatx_np"):
        function = library.renameatx_np
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(
            source_dir_fd,
            source_bytes,
            target_dir_fd,
            target_bytes,
            0x00000004,
        )
    elif hasattr(library, "renameat2"):
        function = library.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(
            source_dir_fd,
            source_bytes,
            target_dir_fd,
            target_bytes,
            0x00000001,
        )
    else:
        raise GoldenSealError("platform lacks an atomic directory no-replace primitive")
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), target_name)


def _verify_output(path: Path, outputs: Mapping[str, bytes]) -> None:
    _directory_identity(path, "golden bundle")
    _exact_inventory(path, set(_OUTPUT_NAMES), "golden bundle")
    for name in _OUTPUT_NAMES:
        capture = _read_regular(path / name, f"golden bundle/{name}")
        if capture.payload != outputs[name]:
            raise GoldenSealError(f"existing golden bundle differs at {name}")


def _publish_bundle(
    output_dir: Path,
    outputs: Mapping[str, bytes],
    reauthenticate: Callable[[], None],
) -> str:
    parent_fd = _open_directory(output_dir.parent)
    parent_identity = _directory_fd_identity(parent_fd)
    scratch_fd: int | None = None
    try:
        _assert_path_matches_directory_fd(
            output_dir.parent, parent_fd, parent_identity, "publication parent"
        )
        existing = _entry_metadata(parent_fd, output_dir.name)
        if existing is not None:
            existing_identity = existing.st_dev, existing.st_ino
            reauthenticate()
            _assert_path_matches_directory_fd(
                output_dir.parent, parent_fd, parent_identity, "publication parent"
            )
            current = _entry_metadata(parent_fd, output_dir.name)
            if current is None or (current.st_dev, current.st_ino) != existing_identity:
                raise GoldenSealError("existing output identity changed before reuse")
            _verify_output(output_dir, outputs)
            final = _entry_metadata(parent_fd, output_dir.name)
            if final is None or (final.st_dev, final.st_ino) != existing_identity:
                raise GoldenSealError("existing output identity changed during reuse")
            return "reused"
        scratch, scratch_fd, scratch_identity = _create_retained_scratch(
            parent_fd, output_dir.parent
        )
        for name in _OUTPUT_NAMES:
            _write_file(scratch / name, outputs[name], scratch_fd)
        os.fsync(scratch_fd)

        # The commit boundary is deliberately ordered: all inputs, retained
        # scratch bytes, then parent/source/target names, immediately before
        # the exclusive rename.
        reauthenticate()
        _verify_directory_fd(scratch_fd, scratch_identity, outputs)
        _assert_path_matches_directory_fd(
            output_dir.parent, parent_fd, parent_identity, "publication parent"
        )
        scratch_entry = _entry_metadata(parent_fd, scratch.name)
        if scratch_entry is None or (
            scratch_entry.st_dev,
            scratch_entry.st_ino,
        ) != scratch_identity:
            raise GoldenSealError(
                f"scratch path was replaced; retained inode remains unclaimed at {scratch}"
            )
        if _entry_metadata(parent_fd, output_dir.name) is not None:
            raise GoldenSealError(
                f"output appeared at commit; retained scratch remains unclaimed at {scratch}"
            )
        try:
            _atomic_install_exclusive(
                parent_fd, scratch.name, parent_fd, output_dir.name
            )
        except OSError as error:
            raise GoldenSealError(
                f"atomic publication failed; retained scratch remains unclaimed at {scratch}: {error}"
            ) from error
        published = _entry_metadata(parent_fd, output_dir.name)
        if published is None or (published.st_dev, published.st_ino) != scratch_identity:
            raise GoldenSealError("published output is not the retained scratch inode")
        os.fsync(parent_fd)
        _verify_directory_fd(scratch_fd, scratch_identity, outputs)
        _verify_output(output_dir, outputs)
        final = _entry_metadata(parent_fd, output_dir.name)
        if final is None or (final.st_dev, final.st_ino) != scratch_identity:
            raise GoldenSealError("published output identity changed during final verification")
        _assert_path_matches_directory_fd(
            output_dir.parent, parent_fd, parent_identity, "publication parent"
        )
        return "created"
    finally:
        if scratch_fd is not None:
            os.close(scratch_fd)
        os.close(parent_fd)


def _run(
    contract_root: Path,
    analysis_config: Path,
    runtime_manifest: Path,
    output_dir: Path,
) -> tuple[str, str]:
    _validate_topology(contract_root, analysis_config, runtime_manifest, output_dir)
    capture = _capture_inputs(contract_root, analysis_config, runtime_manifest)
    bundle_id, outputs = _prepare_outputs(capture)
    forbidden = tuple(
        os.fsencode(path)
        for path in (contract_root, analysis_config, runtime_manifest, output_dir)
    )
    if any(value and any(value in payload for payload in outputs.values()) for value in forbidden):
        raise GoldenSealError("output contains a local path leak")

    def reauthenticate() -> None:
        current = _capture_inputs(contract_root, analysis_config, runtime_manifest)
        if not _same_capture(capture, current):
            raise GoldenSealError("authenticated inputs changed before publication")

    status = _publish_bundle(output_dir, outputs, reauthenticate)
    return status, bundle_id


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(allow_abbrev=False)
    parser.add_argument("--contract-root", required=True)
    parser.add_argument("--analysis-config", required=True)
    parser.add_argument("--runtime-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        contract_root = _path_from_argument(arguments.contract_root, "contract root")
        analysis_config = _path_from_argument(arguments.analysis_config, "analysis config")
        runtime_manifest = _path_from_argument(arguments.runtime_manifest, "runtime manifest")
        output_dir = _path_from_argument(arguments.output_dir, "output directory")
        status, bundle_id = _run(
            contract_root, analysis_config, runtime_manifest, output_dir
        )
    except (GoldenSealError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"{status} {bundle_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
