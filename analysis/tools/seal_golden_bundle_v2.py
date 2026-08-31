#!/usr/bin/env python3
"""Seal the authenticated golden v1 semantics as one immutable v2 Bundle."""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import ctypes
from fractions import Fraction
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import unicodedata
from typing import Callable, Mapping, Sequence


_V1_CRITERIA_ID = "sha256:8c88d3109bd6945b76317ee415c7821524f515c9bd79d36b8072c285c6434af7"
_V1_BUNDLE_ID = "sha256:4f40427690116f0defcbe609d9e5e1ef4025bb4193cdf099386659a26dd9f8bc"
_V1_BUNDLE_LITERALS = {
    "analysis_config.json": (13830, "bb2610971dc1b3adcb4e93b9d26a50fdb4070292ffc4bf4a26ce7cdde0f5fb0b"),
    "analysis_summary.json": (383818, "aec581bca1e6b4eaed1ddae36c53cd3d5c70e2afb582589e1bc299dd2743876e"),
    "bundle_manifest.json": (3676, "d717b150fcd7cf80065e2a71744091da387d38243f30d816f4cc83e63d605a33"),
    "equipment_operating_ranges.json": (164, "bd8ab5dc79a1a7e97d64132dd3669befbe3fa28493a57eff5918818933526200"),
    "expected_alerts.json": (2032, "bb1834b04cc847d52f1a5e2d9fa9268140dbbab4af86e69d310d1c1c1278e0ae"),
    "producer_runtime.json": (5537, "3c1daabb3e868fc6cebc75023980fcb71d947996170de6d8e58cd06b18231407"),
    "quality_risk_intervals.json": (260330, "3b0a85925ab1ae7b42d70989c32fad3ea3b881cf9647a6a13a72db3d42b7eb30"),
    "replay_events.csv": (59114, "10b39aac41f317210074e42b44a0e1ab6f521b7c432647edf72be80a4e0fef8c"),
}
_V1_SOURCE_LITERALS = {
    "sts_1sm_cc_1.csv": (1056, "c3bba5c7235166b6693ff657a25797933ea9d83c6392437af9b5141f4e56a68c"),
    "sts_2fur_hr_2.csv": (1866, "973e864ed996ca48c44a00943efc0ea51a878b94b8aac7452c7254a572fe7e67"),
    "sts_3ap_3.csv": (711, "efaef9f283a28e63dff54b26bc3dd42c6a72e20f0b75d2fa13a6ab47f41405c9"),
}
_V2_CONFIG_LITERAL = (13963, "592e711b007c7d397c5c849c92a874b9ed6fca4e88cadaf66581ae1735046b63")
_V2_RUNTIME_LITERAL = (5537, "3bd2a358461949cff481ecf813b4d7bc4bc2e40b6f8da1877db2df5ea4d4702a")
_SCHEMA_LITERALS = {
    "analysis_config": ("analysis_config.schema.json", "7942b12abcfeee661290f5b40df922edfeec66d6f95dc1ce20e78cc0f58f5da1"),
    "analysis_summary": ("analysis_summary.schema.json", "0b38234ea0b7c5614dff0ac7514b6fb82e401a7846621e45bcbcf00ed82dc7c4"),
    "bundle_manifest": ("bundle_manifest.schema.json", "0d7c4fb5e939beb324c6d83005a83cb9702e480a5babdfede88e5ca05776c2c3"),
    "equipment_operating_ranges": ("equipment_operating_ranges.schema.json", "bee7d8be181dae4844c51d4627c5a1f068583b60a60c854f17035a8291cd7d89"),
    "producer_runtime": ("producer_runtime.schema.json", "97131d80a993d09d17c2c040b0e1cb2bd0eed5948d7a11608f26331d18f557e6"),
    "quality_risk_intervals": ("quality_risk_intervals.schema.json", "2c8775fec18671030cf58ea0e94a3c99f8dac075fa9d5fe8dce724c2462f42ad"),
    "replay_events": ("replay_event_row.schema.json", "309749f73cf2a5a522617f975128ac005298f2fd71412903d76263702c1b6bd6"),
}
_OUTPUT_NAMES = (
    "analysis_config.json",
    "producer_runtime.json",
    "equipment_operating_ranges.json",
    "quality_risk_intervals.json",
    "replay_events.csv",
    "analysis_summary.json",
    "bundle_manifest.json",
)
_ARTIFACTS = (
    ("analysis_config", "analysis_config.json", "sfep-analysis-config/v2"),
    ("producer_runtime", "producer_runtime.json", "sfep-producer-runtime/v1"),
    ("equipment_operating_ranges", "equipment_operating_ranges.json", "sfep-operating-ranges/v1"),
    ("quality_risk_intervals", "quality_risk_intervals.json", "sfep-quality-rules/v1"),
    ("replay_events", "replay_events.csv", "sfep-replay-events/v1"),
    ("analysis_summary", "analysis_summary.json", "sfep-analysis-summary/v2"),
)
_REPLAY_HEADER = (
    "schema_version", "bundle_id", "criteria_id", "event_id", "replay_date",
    "replay_hour", "batch_kind", "batch_id", "equipment_batch_id", "batch_step",
    "time_precision", "material_key", "equipment_type", "equipment_id", "charge_id",
    "slab_no", "hr_coil_id", "ap_prod_id", "values_json",
)
_RUNTIME_DIFFERENCES = frozenset({
    "locks.producer", "locks.pyproject", "producer.installedCodeTreeSha256",
    "producer.sourceSha256", "producer.version", "producer.wheelFilename",
    "producer.wheelSha256", "python.executableSha256",
})
_LINEAGE_CORRECTIONS = frozenset({
    ("analysis_summary", f"holdoutMetrics[].{metric}.{leaf}")
    for metric in ("precision", "lift")
    for leaf in ("lower", "upper", "validReplicates", "reasonCode")
})
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_SCRATCH_PREFIX = ".sfep-golden-v2-"
_PUBLICATION_LOCK = ".sfep-golden-v2.lock"
_POWER10 = tuple(10**exponent for exponent in range(400))
_RECORD_HASH = re.compile(r"sha256=([A-Za-z0-9_-]{43})\Z")
_RECORD_SIZE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_DIST_INFO_EXCLUSIONS = frozenset({"RECORD", "INSTALLER", "direct_url.json", "REQUESTED"})
_MANIFEST_KEYS = {
    "artifacts", "asOf", "bundleId", "criteriaId", "criteriaIdentity",
    "identity", "labelMaturityDays", "schemaVersion", "timezone",
}
_EXPECTED_PRODUCER = {
    "installedCodeTreeSha256": "sha256:210fc9b61b174cda72e85b2e6eb32b83dcc8642a52718ea93871c91356f59f8a",
    "name": "equipment-quality",
    "sourceSha256": "sha256:26eefd1d37546b1aeb1a56158bad06765cb40f21f975a1c2efe569410d5d9a05",
    "version": "1.1.0",
    "wheelFilename": "sfep_equipment_quality-1.1.0-py3-none-any.whl",
    "wheelSha256": "sha256:abdfb2738c4eea6dd9e8c832951deef5faff4795ffbae39c6a9617cac293d514",
}
_EXPECTED_EXECUTABLE_SHA256 = "sha256:68d03d8a962f8a756e3a2ba0fc95a8361719c0548f257f5f069ac090727d1d14"
_EXPECTED_PIP_TREE_SHA256 = "sha256:b7c42058a2b36be74282b0b1ac72379b47ad1c54eea6186273fc4e68c9e78801"
_GENERATED_SCRIPTS = {
    "jsonschema": {"jsonschema": "jsonschema.cli:main"},
    "numpy": {
        "f2py": "numpy.f2py.f2py2e:main",
        "numpy-config": "numpy._configtool:main",
    },
    "pip": {
        "pip": "pip._internal.cli.main:main",
        "pip3": "pip._internal.cli.main:main",
        "pip3.12": "pip._internal.cli.main:main",
    },
    "sfep-equipment-quality": {
        "sfep-equipment-quality": "equipment_quality.cli:main",
    },
}


class GoldenV2SealError(RuntimeError):
    """The golden v2 Bundle could not be authenticated or safely published."""


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GoldenV2SealError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise GoldenV2SealError(f"non-finite JSON number is forbidden: {value}")


def _scaled_floor(value: Fraction, exponent: int) -> int:
    if exponent >= 0:
        power = _POWER10[exponent] if exponent < len(_POWER10) else 10**exponent
        return value.numerator // (value.denominator * power)
    power = _POWER10[-exponent] if -exponent < len(_POWER10) else 10 ** (-exponent)
    return (value.numerator * power) // value.denominator


def _decimal_order(value: Fraction) -> int:
    order = len(str(value.numerator)) - len(str(value.denominator))
    if order >= 0:
        return order - 1 if value.numerator < value.denominator * _POWER10[order] else order
    return order - 1 if value.numerator * _POWER10[-order] < value.denominator else order


def _rounding_interval(number: float) -> tuple[Fraction, Fraction]:
    target = Fraction.from_float(number)
    previous = Fraction.from_float(math.nextafter(number, -math.inf))
    lower = (previous + target) / 2
    following = math.nextafter(number, math.inf)
    upper = target + (target - previous) / 2 if math.isinf(following) else (target + Fraction.from_float(following)) / 2
    return lower, upper


def _decimal_spellings(coefficient: int, exponent: int, limit: int) -> tuple[str, ...]:
    digits = str(coefficient)
    if exponent >= 0:
        fixed = digits + "0" * exponent
    else:
        point = len(digits) + exponent
        fixed = digits[:point] + "." + digits[point:] if point > 0 else "0." + "0" * (-point) + digits
    result = [fixed] if len(fixed) <= limit else []
    for point in range(1, len(digits) + 1):
        mantissa = digits if point == len(digits) else digits[:point] + "." + digits[point:]
        scientific = f"{mantissa}e{exponent + len(digits) - point}"
        if len(scientific) <= limit:
            result.append(scientific)
    return tuple(result)


def _initial_float_spelling(number: float) -> str:
    negative = number < 0.0
    magnitude = -number if negative else number
    coefficient, marker, exponent_text = repr(magnitude).lower().partition("e")
    exponent = int(exponent_text) if marker else 0
    integer, dot, fraction = coefficient.partition(".")
    digits = (integer + fraction).lstrip("0")
    decimal_exponent = exponent - (len(fraction) if dot else 0)
    while digits.endswith("0"):
        digits = digits[:-1]
        decimal_exponent += 1
    prefix = "-" if negative else ""
    candidates = [prefix + value for value in _decimal_spellings(int(digits), decimal_exponent, 1000)
                  if float(prefix + value) == number]
    return min(candidates, key=lambda value: (len(value), value))


def _minimum_spelling_length(digits: int, exponent: int, sign: int) -> int:
    fixed = digits + exponent if exponent >= 0 else digits + 1 if digits + exponent > 0 else 2 - (digits + exponent) + digits
    scientific = min(
        digits + (1 if point < digits else 0) + 1 + len(str(exponent + digits - point))
        for point in range(1, digits + 1)
    )
    return sign + min(fixed, scientific)


def _canonical_float(number: float) -> str:
    if not math.isfinite(number):
        raise GoldenV2SealError("canonical JSON numbers must be finite")
    if number == 0.0:
        return "0"
    best = _initial_float_spelling(number)
    best_key = (len(best.encode()), best.encode())
    negative = number < 0.0
    magnitude = -number if negative else number
    lower, upper = _rounding_interval(magnitude)
    prefix = "-" if negative else ""
    for digits in range(1, best_key[0] - len(prefix) + 1):
        lower_order, upper_order = _decimal_order(lower), _decimal_order(upper)
        for exponent in range(lower_order - digits - 1, upper_order - digits + 2):
            if _minimum_spelling_length(digits, exponent, len(prefix)) > best_key[0]:
                continue
            least = 1 if digits == 1 else _POWER10[digits - 1]
            greatest = _POWER10[digits] - 1
            first = max(least, _scaled_floor(lower, exponent) - 1)
            last = min(greatest, _scaled_floor(upper, exponent) + 1)
            for coefficient in range(first, min(last, first + 3) + 1):
                if coefficient % 10 == 0:
                    continue
                for spelling in _decimal_spellings(coefficient, exponent, best_key[0] - len(prefix)):
                    candidate = prefix + spelling
                    key = (len(candidate.encode()), candidate.encode())
                    if key <= best_key and float(candidate) == number:
                        best, best_key = candidate, key
    return best


def _canonical_text(value: object) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        return _canonical_float(value)
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if type(value) is list:
        return "[" + ",".join(_canonical_text(item) for item in value) + "]"
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise GoldenV2SealError("canonical JSON object keys must be strings")
        return "{" + ",".join(
            _canonical_text(key) + ":" + _canonical_text(value[key])
            for key in sorted(value, key=str.encode)
        ) + "}"
    raise GoldenV2SealError(f"unsupported canonical JSON value {type(value).__name__}")


def _canonical_bytes(value: object) -> bytes:
    return (_canonical_text(value) + "\n").encode("utf-8")


def _json(payload: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GoldenV2SealError(f"{label} must be valid UTF-8 JSON") from error
    if type(value) is not dict:
        raise GoldenV2SealError(f"{label} must contain one JSON object")
    if _canonical_bytes(value) != payload:
        raise GoldenV2SealError(f"{label} must use canonical JSON")
    return value


def _checked_path(raw: str | Path, label: str) -> Path:
    value = os.fspath(raw)
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise GoldenV2SealError(f"{label} contains a control character")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or os.path.normpath(value) != value:
        raise GoldenV2SealError(f"{label} must be a normalized absolute path")
    return path


def _assert_components(path: Path, *, missing_final: bool = False) -> None:
    current = Path(path.parts[0])
    for index, part in enumerate(path.parts[1:], start=1):
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if missing_final and index == len(path.parts) - 1:
                return
            raise GoldenV2SealError(f"missing path component: {current}") from None
        if stat.S_ISLNK(metadata.st_mode):
            raise GoldenV2SealError(f"symlink path component is forbidden: {current}")
        if index < len(path.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise GoldenV2SealError(f"non-directory path component: {current}")


def _read_file(path: Path, label: str) -> tuple[bytes, tuple[int, ...]]:
    _assert_components(path)
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise GoldenV2SealError(f"{label} must be a regular non-hardlinked file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        initial_identity = (initial.st_dev, initial.st_ino, initial.st_mode)
        if (
            (before.st_dev, before.st_ino, before.st_mode) != initial_identity
            or before.st_nlink != 1
            or not stat.S_ISREG(before.st_mode)
        ):
            raise GoldenV2SealError(f"{label} identity changed before read")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fingerprint = (
        before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
        before.st_size, before.st_mtime_ns, before.st_ctime_ns,
    )
    if fingerprint != (
        after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
        after.st_size, after.st_mtime_ns, after.st_ctime_ns,
    ):
        raise GoldenV2SealError(f"{label} changed while being read")
    current = path.lstat()
    if (
        (current.st_dev, current.st_ino, current.st_mode) != initial_identity
        or current.st_nlink != 1
    ):
        raise GoldenV2SealError(f"{label} path was replaced while being read")
    payload = b"".join(chunks)
    if len(payload) != before.st_size:
        raise GoldenV2SealError(f"{label} size changed while being read")
    return payload, fingerprint


def _inventory(path: Path, expected: set[str], label: str) -> None:
    _assert_components(path)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise GoldenV2SealError(f"{label} must be a directory")
    entries = tuple(os.scandir(path))
    if {entry.name for entry in entries} != expected or len(entries) != len(expected):
        raise GoldenV2SealError(f"{label} inventory is not exact")
    for entry in entries:
        metadata = entry.stat(follow_symlinks=False)
        if (
            entry.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise GoldenV2SealError(f"{label} contains a non-regular or hardlinked entry")


def _literal(payload: bytes, expected: tuple[int, str], label: str) -> None:
    if (len(payload), hashlib.sha256(payload).hexdigest()) != expected:
        raise GoldenV2SealError(f"authenticated {label} size/sha256 mismatch")


def _capture_inputs(
    contract_root: Path,
    analysis_config: Path,
    runtime_manifest: Path,
) -> tuple[dict[str, bytes], dict[str, tuple[int, ...]]]:
    bundle = contract_root / "golden-bundle"
    source = contract_root / "golden-source"
    _inventory(bundle, set(_V1_BUNDLE_LITERALS), "golden v1 Bundle")
    _inventory(source, set(_V1_SOURCE_LITERALS), "golden v1 source")
    files: dict[str, bytes] = {}
    fingerprints: dict[str, tuple[int, ...]] = {}
    for prefix, root, literals in (
        ("v1", bundle, _V1_BUNDLE_LITERALS),
        ("source", source, _V1_SOURCE_LITERALS),
    ):
        for name, literal in literals.items():
            payload, fingerprint = _read_file(root / name, f"{prefix}/{name}")
            _literal(payload, literal, f"{prefix}/{name}")
            files[f"{prefix}/{name}"] = payload
            fingerprints[f"{prefix}/{name}"] = fingerprint
    config, config_fingerprint = _read_file(analysis_config, "v2 analysis config")
    _literal(config, _V2_CONFIG_LITERAL, "v2 analysis config")
    runtime, runtime_fingerprint = _read_file(runtime_manifest, "v2 runtime manifest")
    _literal(runtime, _V2_RUNTIME_LITERAL, "v2 runtime manifest")
    files["v2/config"] = config
    files["v2/runtime"] = runtime
    fingerprints["v2/config"] = config_fingerprint
    fingerprints["v2/runtime"] = runtime_fingerprint

    v2_root = analysis_config.parent.parent
    for role, (name, digest) in _SCHEMA_LITERALS.items():
        root = v2_root if role in {"analysis_config", "analysis_summary", "bundle_manifest"} else contract_root
        payload, fingerprint = _read_file(root / name, f"schema/{role}")
        if hashlib.sha256(payload).hexdigest() != digest:
            raise GoldenV2SealError(f"authenticated schema/{role} sha256 mismatch")
        files[f"schema/{role}"] = payload
        fingerprints[f"schema/{role}"] = fingerprint
    _authenticate_v1(files)
    _authenticate_v2_config_runtime(files)
    return files, fingerprints


def _authenticate_v1(files: Mapping[str, bytes]) -> None:
    manifest = _json(files["v1/bundle_manifest.json"], "golden v1 manifest")
    if manifest.get("criteriaId") != _V1_CRITERIA_ID or manifest.get("bundleId") != _V1_BUNDLE_ID:
        raise GoldenV2SealError("golden v1 IDs are not the approved anchors")
    entries = manifest.get("artifacts")
    if type(entries) is not list or len(entries) != 6:
        raise GoldenV2SealError("golden v1 artifact manifest is not exact")
    filenames = dict((role, name) for role, name, _version in _ARTIFACTS)
    for entry in entries:
        if type(entry) is not dict or entry.get("role") not in filenames:
            raise GoldenV2SealError("golden v1 artifact role is invalid")
        payload = files["v1/" + filenames[entry["role"]]]
        if entry.get("sizeBytes") != len(payload) or entry.get("sha256") != _sha(payload):
            raise GoldenV2SealError("golden v1 artifact binding is invalid")
    identity = manifest.get("identity")
    if type(identity) is not dict:
        raise GoldenV2SealError("golden v1 identity is absent")
    source_names = {"sm_cc": "sts_1sm_cc_1.csv", "fur_hr": "sts_2fur_hr_2.csv", "ap": "sts_3ap_3.csv"}
    for role, name in source_names.items():
        payload = files["source/" + name]
        if (
            identity.get(f"source.{role}.name") != name
            or identity.get(f"source.{role}.sha256") != _sha(payload)
            or identity.get(f"source.{role}.size_bytes") != str(len(payload))
        ):
            raise GoldenV2SealError("golden v1 source identity is invalid")
    for filename in (
        "analysis_config.json", "producer_runtime.json", "equipment_operating_ranges.json",
        "quality_risk_intervals.json", "analysis_summary.json", "expected_alerts.json",
    ):
        _json(files["v1/" + filename], "golden v1/" + filename)


def _authenticate_v2_config_runtime(files: Mapping[str, bytes]) -> None:
    config = _json(files["v2/config"], "v2 analysis config")
    bootstrap = config.get("bootstrap")
    if (
        config.get("schemaVersion") != "sfep-analysis-config/v2"
        or config.get("analysisConfigVersion") != "quality-analysis-v2"
        or type(bootstrap) is not dict
        or bootstrap.get("seedMaterial") != _V1_CRITERIA_ID
        or bootstrap.get("seedProtocol") != "LEGACY_CRITERIA_ID_UTF8_V1"
    ):
        raise GoldenV2SealError("golden v2 seed/version is not the authenticated v1 migration")
    runtime = _json(files["v2/runtime"], "v2 runtime manifest")
    producer = runtime.get("producer")
    policy = runtime.get("environmentPolicy")
    if (
        runtime.get("schemaVersion") != "sfep-producer-runtime/v1"
        or type(producer) is not dict
        or producer != _EXPECTED_PRODUCER
        or runtime.get("python", {}).get("executableSha256") != _EXPECTED_EXECUTABLE_SHA256
        or type(policy) is not dict
        or policy.get("pythonHashSeed") != "0"
        or policy.get("timezone") != "Asia/Seoul"
        or files["v2/runtime"] == files["v1/producer_runtime.json"]
    ):
        raise GoldenV2SealError("runtime manifest is not the sealed compatibility producer 1.1.0")


def _diff_paths(left: object, right: object, prefix: str = "") -> set[str]:
    if type(left) is dict and type(right) is dict:
        result: set[str] = set()
        for key in set(left) | set(right):
            result.update(_diff_paths(left.get(key, object()), right.get(key, object()), f"{prefix}.{key}" if prefix else key))
        return result
    if type(left) is list and type(right) is list:
        if len(left) != len(right):
            return {prefix + ".length"}
        result = set()
        for index, (first, second) in enumerate(zip(left, right, strict=True)):
            result.update(_diff_paths(first, second, f"{prefix}[{index}]"))
        return result
    return set() if left == right else {prefix}


def _identity(namespace: str, fields: object, expected: set[str]) -> str:
    if type(fields) is not dict or set(fields) != expected or any(type(value) is not str for value in fields.values()):
        raise GoldenV2SealError(f"{namespace} fields are not exact strings")
    preimage = "\n".join([namespace, *(f"{key}={fields[key]}" for key in sorted(fields, key=str.encode))]) + "\n"
    return _sha(preimage.encode("utf-8"))


def _verify_generated(
    root: Path,
    inputs: Mapping[str, bytes],
) -> tuple[str, dict[str, bytes]]:
    _inventory(root, set(_OUTPUT_NAMES), "generated golden v2 Bundle")
    outputs = {name: _read_file(root / name, "generated/" + name)[0] for name in _OUTPUT_NAMES}
    manifest = _json(outputs["bundle_manifest.json"], "generated v2 manifest")
    entries = manifest.get("artifacts")
    if (
        set(manifest) != _MANIFEST_KEYS
        or manifest.get("schemaVersion") != "sfep-equipment-bundle/v2"
        or type(entries) is not list
        or len(entries) != 6
    ):
        raise GoldenV2SealError("generated v2 manifest version/inventory is invalid")
    for entry, (role, filename, version) in zip(entries, _ARTIFACTS, strict=True):
        payload = outputs[filename]
        if entry != {"role": role, "schemaVersion": version, "sha256": _sha(payload), "sizeBytes": len(payload)}:
            raise GoldenV2SealError("generated v2 artifact metadata is invalid")
    criteria_keys = {
        "analysis_config_sha256", "as_of", "criteria_projection_sha256", "producer_runtime_sha256",
        "schema.analysis_config.sha256", "schema.equipment_operating_ranges.sha256",
        "schema.producer_runtime.sha256", "schema.quality_risk_intervals.sha256",
    }
    identity_keys = {
        "analysis_config_sha256", "criteria_id", "producer_runtime_sha256",
        *(f"schema.{role}.sha256" for role in _SCHEMA_LITERALS),
        *(f"source.{role}.{field}" for role in ("ap", "fur_hr", "sm_cc") for field in ("name", "sha256", "size_bytes")),
    }
    v1_manifest = _json(inputs["v1/bundle_manifest.json"], "authenticated v1 manifest")
    v1_criteria = v1_manifest["criteriaIdentity"]
    v1_identity = v1_manifest["identity"]
    if type(v1_criteria) is not dict or type(v1_identity) is not dict:
        raise GoldenV2SealError("authenticated v1 identities are invalid")
    expected_criteria = dict(v1_criteria)
    expected_criteria.update({
        "analysis_config_sha256": _sha(outputs["analysis_config.json"]),
        "producer_runtime_sha256": _sha(outputs["producer_runtime.json"]),
        "schema.analysis_config.sha256": "sha256:" + _SCHEMA_LITERALS["analysis_config"][1],
    })
    criteria = manifest.get("criteriaIdentity")
    if criteria != expected_criteria:
        raise GoldenV2SealError("generated v2 criteria identity is not derived from authenticated inputs")
    criteria_id = _identity("sfep-criteria-id/v1", criteria, criteria_keys)
    expected_identity = dict(v1_identity)
    expected_identity.update({
        "analysis_config_sha256": _sha(outputs["analysis_config.json"]),
        "criteria_id": criteria_id,
        "producer_runtime_sha256": _sha(outputs["producer_runtime.json"]),
        "schema.analysis_config.sha256": "sha256:" + _SCHEMA_LITERALS["analysis_config"][1],
        "schema.analysis_summary.sha256": "sha256:" + _SCHEMA_LITERALS["analysis_summary"][1],
        "schema.bundle_manifest.sha256": "sha256:" + _SCHEMA_LITERALS["bundle_manifest"][1],
    })
    identity = manifest.get("identity")
    if identity != expected_identity:
        raise GoldenV2SealError("generated v2 bundle identity is not derived from authenticated inputs")
    bundle_id = _identity("sfep-bundle-id/v1", identity, identity_keys)
    if manifest.get("criteriaId") != criteria_id or manifest.get("bundleId") != bundle_id:
        raise GoldenV2SealError("generated v2 identities do not recompute")
    if (
        manifest.get("asOf") != v1_manifest.get("asOf")
        or manifest.get("labelMaturityDays") != v1_manifest.get("labelMaturityDays")
        or manifest.get("timezone") != v1_manifest.get("timezone")
        or criteria["as_of"] != manifest["asOf"]
        or criteria["criteria_projection_sha256"] != v1_criteria["criteria_projection_sha256"]
        or criteria["analysis_config_sha256"] != _sha(outputs["analysis_config.json"])
        or criteria["producer_runtime_sha256"] != _sha(outputs["producer_runtime.json"])
        or identity["analysis_config_sha256"] != _sha(outputs["analysis_config.json"])
        or identity["producer_runtime_sha256"] != _sha(outputs["producer_runtime.json"])
        or identity["criteria_id"] != criteria_id
    ):
        raise GoldenV2SealError("generated v2 identity artifact bindings are invalid")
    for role, (_name, digest) in _SCHEMA_LITERALS.items():
        expected = "sha256:" + digest
        if identity.get(f"schema.{role}.sha256") != expected:
            raise GoldenV2SealError(f"generated v2 schema identity differs for {role}")
        if f"schema.{role}.sha256" in criteria and criteria[f"schema.{role}.sha256"] != expected:
            raise GoldenV2SealError(f"generated v2 criteria schema differs for {role}")
    if outputs["analysis_config.json"] != inputs["v2/config"] or outputs["producer_runtime.json"] != inputs["v2/runtime"]:
        raise GoldenV2SealError("generated v2 immutable config/runtime bytes differ from inputs")
    _assert_semantic_parity(inputs, outputs)
    _validate_output_schemas(outputs)
    if root.name != bundle_id:
        raise GoldenV2SealError("generated directory name does not equal bundleId")
    return bundle_id, outputs


def _events(payload: bytes) -> tuple[dict[str, str], ...]:
    try:
        text = payload.decode("utf-8", errors="strict")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = tuple(next(reader))
        cells = tuple(tuple(row) for row in reader)
    except (UnicodeError, csv.Error, StopIteration) as error:
        raise GoldenV2SealError("replay CSV is malformed") from error
    if header != _REPLAY_HEADER or any(len(row) != len(header) for row in cells):
        raise GoldenV2SealError("replay header/row shape is not exact")
    rendered = io.StringIO(newline="")
    writer = csv.writer(rendered, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(cells)
    if rendered.getvalue().encode("utf-8") != payload:
        raise GoldenV2SealError("replay CSV must use canonical quoting and newlines")
    rows = tuple(dict(zip(header, row, strict=True)) for row in cells)
    for ordinal, row in enumerate(rows, start=2):
        try:
            values = json.loads(
                row["values_json"],
                object_pairs_hook=_pairs,
                parse_constant=_reject_constant,
            )
        except (UnicodeError, json.JSONDecodeError) as error:
            raise GoldenV2SealError("replay values_json is not valid JSON") from error
        if (
            type(values) is not dict
            or _canonical_text(values) != row["values_json"]
        ):
            if type(values) is not dict:
                raise GoldenV2SealError("replay values_json is not an object")
            raise GoldenV2SealError(
                f"replay row {ordinal} values_json is not canonical"
            )
        if type(values) is not dict:
            raise GoldenV2SealError("replay values_json is not an object")
    return rows


def _normalized_event(row: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    copy = dict(row)
    copy["bundle_id"] = "<bundle>"
    copy["criteria_id"] = "<criteria>"
    return tuple(copy.items())


def _expected_lineage(v1_fields: list[object]) -> list[dict[str, object]]:
    fields = copy.deepcopy(v1_fields)
    if not all(type(item) is dict for item in fields):
        raise GoldenV2SealError("v1 lineage fields are invalid")
    keyed = {(item["artifactRole"], item["outputField"]): item for item in fields}
    if len(keyed) != len(fields):
        raise GoldenV2SealError("v1 lineage fields are duplicated")
    seed_pair = {"config.bootstrap.seedMaterial", "config.bootstrap.seedProtocol"}
    confusion = set(keyed[("analysis_summary", "holdoutMetrics[].truePositive")]["dependencies"])
    for key, item in keyed.items():
        dependencies = set(item["dependencies"])
        if item["conversion"] in {"COMPUTE_QUALITY_METRIC", "COMPUTE_HOLDOUT_METRIC"} and "identity.criteria_id" in dependencies:
            dependencies.remove("identity.criteria_id")
            dependencies.update(seed_pair)
            item["dependencies"] = sorted(dependencies, key=str.encode)
        if key in _LINEAGE_CORRECTIONS:
            metric, leaf = item["outputField"].removeprefix("holdoutMetrics[].").split(".", 1)
            point = set(keyed[("analysis_summary", f"holdoutMetrics[].{metric}.pointEstimate")]["dependencies"])
            corrected = point | confusion | seed_pair | {"config.bootstrap.replicates", "replay_events.charge_id"}
            if leaf != "validReplicates":
                corrected.add("config.bootstrap.minimumValidReplicates")
            item["dependencies"] = sorted(corrected, key=str.encode)
    for output_field in ("bootstrap.seedMaterial", "bootstrap.seedProtocol"):
        fields.append({
            "artifactRole": "analysis_config", "conversion": "COPY_CANONICAL_CONFIG",
            "dependencies": ["config." + output_field], "firstAvailableStage": None,
            "outputField": output_field, "sourceColumn": None, "sourceRole": None,
        })
    role_rank = {
        role: index
        for index, role in enumerate((
            "bundle_manifest", "analysis_config", "producer_runtime",
            "equipment_operating_ranges", "quality_risk_intervals",
            "replay_events", "analysis_summary",
        ))
    }
    return sorted(
        fields,
        key=lambda item: (
            role_rank[item["artifactRole"]], item["outputField"].encode()
        ),
    )


def _assert_semantic_parity(inputs: Mapping[str, bytes], outputs: Mapping[str, bytes]) -> None:
    manifest = _json(outputs["bundle_manifest.json"], "v2 manifest")
    criteria_id = manifest["criteriaId"]
    bundle_id = manifest["bundleId"]
    as_of = manifest["asOf"]
    v1_config = _json(inputs["v1/analysis_config.json"], "v1 config")
    v2_config = _json(outputs["analysis_config.json"], "v2 config")
    if _diff_paths(v1_config, v2_config) != {"analysisConfigVersion", "bootstrap.seedMaterial", "bootstrap.seedProtocol", "schemaVersion"}:
        raise GoldenV2SealError("v2 config differs beyond the versioned seed allowlist")
    v1_runtime = _json(inputs["v1/producer_runtime.json"], "v1 runtime")
    v2_runtime = _json(outputs["producer_runtime.json"], "v2 runtime")
    if _diff_paths(v1_runtime, v2_runtime) != _RUNTIME_DIFFERENCES:
        raise GoldenV2SealError("v2 runtime differs beyond exact provenance fields")
    for filename in ("equipment_operating_ranges.json", "quality_risk_intervals.json"):
        before = _json(inputs["v1/" + filename], "v1/" + filename)
        after = _json(outputs[filename], "v2/" + filename)
        if after.get("criteriaId") != criteria_id or after.get("asOf") != as_of:
            raise GoldenV2SealError(f"{filename} manifest binding failed")
        before["criteriaId"] = criteria_id
        if before != after:
            raise GoldenV2SealError(f"{filename} semantic parity failed")
    v1_events = _events(inputs["v1/replay_events.csv"])
    v2_events = _events(outputs["replay_events.csv"])
    if any(
        row.get("schema_version") != "sfep-replay-events/v1"
        or row.get("bundle_id") != bundle_id
        or row.get("criteria_id") != criteria_id
        for row in v2_events
    ):
        raise GoldenV2SealError("replay manifest bindings failed")
    if len(v1_events) != len(v2_events) or tuple(map(_normalized_event, v1_events)) != tuple(map(_normalized_event, v2_events)):
        raise GoldenV2SealError("replay semantic parity failed")
    v1_summary = _json(inputs["v1/analysis_summary.json"], "v1 summary")
    v2_summary = _json(outputs["analysis_summary.json"], "v2 summary")
    if (
        v2_summary.get("bundleId") != bundle_id
        or v2_summary.get("criteriaId") != criteria_id
        or v2_summary.get("asOf") != as_of
    ):
        raise GoldenV2SealError("analysis summary manifest bindings failed")
    expected = copy.deepcopy(v1_summary)
    expected["schemaVersion"] = "sfep-analysis-summary/v2"
    expected["bundleId"] = bundle_id
    expected["criteriaId"] = criteria_id
    expected["lineage"]["fields"] = _expected_lineage(expected["lineage"]["fields"])
    if expected != v2_summary:
        raise GoldenV2SealError("analysis summary differs beyond exact binding/lineage allowlist")
    alerts = _json(inputs["v1/expected_alerts.json"], "v1 expected alerts")
    if alerts.get("alerts") != [] or alerts.get("expectedReplayEventCount") != len(v2_events):
        raise GoldenV2SealError("v1 expected alert semantics are not the empty golden boundary")


def _checked_record_path(raw: str) -> tuple[PurePosixPath, bool]:
    if (
        not raw
        or "\\" in raw
        or raw.startswith("/")
        or raw.endswith("/")
        or any(ord(character) < 32 or ord(character) == 127 for character in raw)
    ):
        raise GoldenV2SealError("installed producer RECORD contains an unsafe path")
    path = PurePosixPath(raw)
    generated = len(path.parts) == 5 and path.parts[:4] == ("..", "..", "..", "bin")
    if path.is_absolute() or path.as_posix() != raw or (".." in path.parts and not generated):
        raise GoldenV2SealError("installed producer RECORD escapes site-packages")
    return path, generated


def _console_wrapper(executable: Path, target: str) -> bytes:
    module, separator, function = target.partition(":")
    if (
        separator != ":"
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function)
    ):
        raise GoldenV2SealError("console script target is invalid")
    return (
        f"#!{executable}\n"
        "# -*- coding: utf-8 -*-\n"
        "import re\n"
        "import sys\n"
        f"from {module} import {function}\n"
        "if __name__ == '__main__':\n"
        "    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
        f"    sys.exit({function}())\n"
    ).encode("utf-8")


def _normalise_distribution_name(value: object) -> str:
    if type(value) is not str or not value:
        raise GoldenV2SealError("installed distribution name is invalid")
    result = re.sub(r"[-_.]+", "-", value).lower()
    if not result:
        raise GoldenV2SealError("installed distribution name is invalid")
    return result


def _metadata_identity(payload: bytes, label: str) -> tuple[str, str]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise GoldenV2SealError(f"installed {label} METADATA is not UTF-8") from error
    names = [line[6:] for line in text.splitlines() if line.startswith("Name: ")]
    versions = [line[9:] for line in text.splitlines() if line.startswith("Version: ")]
    if len(names) != 1 or len(versions) != 1 or not versions[0]:
        raise GoldenV2SealError(f"installed {label} METADATA identity is ambiguous")
    return _normalise_distribution_name(names[0]), versions[0]


def _runtime_site_inventory(purelib: Path) -> dict[Path, tuple[int, ...]]:
    inventory: dict[Path, tuple[int, ...]] = {}
    aliases: set[str] = set()
    for current, directories, filenames in os.walk(
        purelib, topdown=True, followlinks=False
    ):
        current_path = Path(current)
        directories.sort(key=lambda item: item.encode("utf-8"))
        filenames.sort(key=lambda item: item.encode("utf-8"))
        for directory in directories:
            metadata = (current_path / directory).lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise GoldenV2SealError(
                    "runtime site-packages contains an unsafe directory"
                )
            if directory == "__pycache__":
                raise GoldenV2SealError("runtime site-packages contains bytecode")
        for filename in filenames:
            path = current_path / filename
            metadata = path.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
            ):
                raise GoldenV2SealError(
                    "runtime site-packages contains an unsafe or hardlinked file"
                )
            if path.suffix in {".pyc", ".pyo"}:
                raise GoldenV2SealError("runtime site-packages contains bytecode")
            alias = unicodedata.normalize("NFC", os.path.abspath(path)).casefold()
            if alias in aliases:
                raise GoldenV2SealError("runtime site-packages paths are ambiguous")
            aliases.add(alias)
            inventory[path] = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
    return inventory


def _discover_runtime_distributions(
    purelib: Path,
    expected_names: set[str],
) -> dict[str, Path]:
    observed: dict[str, Path] = {}
    aliases: set[str] = set()
    for entry in os.scandir(purelib):
        if not entry.name.endswith(".dist-info"):
            continue
        metadata = entry.stat(follow_symlinks=False)
        if entry.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise GoldenV2SealError("installed dist-info must be a directory")
        dist_info = Path(entry.path)
        metadata_payload, _fingerprint = _read_file(
            dist_info / "METADATA", "installed distribution METADATA"
        )
        name, _version = _metadata_identity(metadata_payload, entry.name)
        alias = unicodedata.normalize("NFC", name).casefold()
        if name in observed or alias in aliases:
            raise GoldenV2SealError("installed distribution names are ambiguous")
        aliases.add(alias)
        observed[name] = dist_info
    if set(observed) != expected_names:
        raise GoldenV2SealError("installed distribution inventory is not exact")
    return observed


def _verify_installed_distribution(
    *,
    purelib: Path,
    prefix: Path,
    executable: Path,
    dist_info: Path,
    name: str,
    version: str,
    expected_tree: str,
    inode_owners: set[tuple[int, int]],
) -> tuple[set[Path], bytes | None]:
    metadata_payload, _fingerprint = _read_file(
        dist_info / "METADATA", f"installed {name} METADATA"
    )
    if _metadata_identity(metadata_payload, name) != (name, version):
        raise GoldenV2SealError(f"installed {name} identity is not exact")
    record_path = dist_info / "RECORD"
    record_payload, _fingerprint = _read_file(
        record_path, f"installed {name} RECORD"
    )
    try:
        rows = tuple(
            csv.reader(
                io.StringIO(
                    record_payload.decode("utf-8", errors="strict"), newline=""
                ),
                strict=True,
            )
        )
    except (UnicodeError, csv.Error) as error:
        raise GoldenV2SealError(f"installed {name} RECORD is malformed") from error
    if not rows or any(len(row) != 3 for row in rows):
        raise GoldenV2SealError(f"installed {name} RECORD rows are malformed")
    seen: set[str] = set()
    aliases: set[str] = set()
    claimed: set[Path] = set()
    tree_lines: list[tuple[bytes, bytes]] = []
    record_self = 0
    provenance: bytes | None = None
    scripts: list[str] = []
    expected_scripts = _GENERATED_SCRIPTS.get(name, {})
    for raw, hash_field, size_field in rows:
        alias = unicodedata.normalize("NFC", raw).casefold()
        if raw in seen or alias in aliases:
            raise GoldenV2SealError(f"installed {name} RECORD paths are ambiguous")
        seen.add(raw)
        aliases.add(alias)
        path, generated = _checked_record_path(raw)
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            raise GoldenV2SealError(f"installed {name} RECORD contains bytecode")
        target = prefix / "bin" / path.name if generated else purelib.joinpath(*path.parts)
        payload, _target_fingerprint = _read_file(
            target, f"installed {name} RECORD target"
        )
        metadata = target.lstat()
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in inode_owners:
            raise GoldenV2SealError("installed RECORD paths alias one inode")
        inode_owners.add(identity)
        claimed.add(target)
        if target == record_path:
            record_self += 1
            if hash_field or size_field:
                raise GoldenV2SealError(f"installed {name} RECORD self-row is hashed")
            continue
        match = _RECORD_HASH.fullmatch(hash_field)
        if match is None or _RECORD_SIZE.fullmatch(size_field) is None:
            raise GoldenV2SealError(
                f"installed {name} RECORD digest/size is not canonical"
            )
        encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
        encoded = encoded.rstrip(b"=").decode("ascii")
        if encoded != match.group(1) or len(payload) != int(size_field):
            raise GoldenV2SealError(
                f"installed {name} RECORD does not authenticate installed bytes"
            )
        if name == "sfep-equipment-quality" and raw == (
            "equipment_quality/sfep_producer_provenance.json"
        ):
            provenance = payload
        if generated:
            scripts.append(path.name)
            target_name = expected_scripts.get(path.name)
            if (
                target_name is None
                or stat.S_IMODE(metadata.st_mode) != 0o755
                or payload != _console_wrapper(executable, target_name)
            ):
                raise GoldenV2SealError(
                    f"installed {name} console wrapper is not exact"
                )
        excluded = generated or (
            len(path.parts) >= 2
            and path.parts[-2].endswith(".dist-info")
            and path.name in _DIST_INFO_EXCLUSIONS
        )
        if not excluded:
            encoded_path = raw.encode("utf-8")
            file_digest = hashlib.sha256(payload).hexdigest().encode("ascii")
            tree_lines.append(
                (encoded_path, encoded_path + b"=sha256:" + file_digest + b"\n")
            )
    if record_self != 1 or set(scripts) != set(expected_scripts) or (
        len(scripts) != len(expected_scripts)
    ):
        raise GoldenV2SealError(f"installed {name} RECORD inventory is incomplete")
    tree_digest = _sha(
        b"".join(line for _path, line in sorted(tree_lines, key=lambda item: item[0]))
    )
    if tree_digest != expected_tree:
        raise GoldenV2SealError(
            f"installed {name} tree digest does not match authenticated wheel material"
        )
    return claimed, provenance


def _verify_runtime_install(runtime_payload: bytes) -> Path:
    _literal(runtime_payload, _V2_RUNTIME_LITERAL, "v2 runtime manifest")
    runtime = _json(runtime_payload, "v2 runtime manifest")
    producer = runtime.get("producer")
    python = runtime.get("python")
    locks = runtime.get("locks")
    packages = runtime.get("packages")
    if (
        producer != _EXPECTED_PRODUCER
        or type(python) is not dict
        or python.get("executableSha256") != _EXPECTED_EXECUTABLE_SHA256
        or type(locks) is not dict
        or type(packages) is not list
        or runtime.get("pipVersion") != "25.1.1"
    ):
        raise GoldenV2SealError("runtime producer/wheel/tree identity is not exact")

    executable = Path(sys.executable)
    if (
        sys.flags.no_site != 1
        or sys.version_info[:3] != (3, 12, 10)
        or sys.implementation.name != "cpython"
        or executable.name != "python"
        or executable.parent.name != "bin"
    ):
        raise GoldenV2SealError("compatibility child interpreter is not the retained runtime Python")
    prefix = executable.parent.parent
    _assert_components(prefix)
    configuration, _configuration_fingerprint = _read_file(
        prefix / "pyvenv.cfg", "compatibility runtime pyvenv.cfg"
    )
    try:
        configuration_lines = configuration.decode("utf-8", errors="strict").splitlines()
        configuration_pairs = [line.split(" = ", 1) for line in configuration_lines]
        configuration_values = {key: value for key, value in configuration_pairs}
    except (UnicodeError, ValueError) as error:
        raise GoldenV2SealError("compatibility runtime pyvenv.cfg is malformed") from error
    if (
        len(configuration_pairs) != len(configuration_values)
        or configuration_values.get("include-system-site-packages") != "false"
        or configuration_values.get("version") != "3.12.10"
    ):
        raise GoldenV2SealError("compatibility runtime pyvenv.cfg is not exact")
    resolved_executable = executable.resolve(strict=True)
    executable_bytes, _fingerprint = _read_file(
        resolved_executable, "compatibility Python executable"
    )
    if _sha(executable_bytes) != _EXPECTED_EXECUTABLE_SHA256:
        raise GoldenV2SealError("compatibility Python executable digest is not sealed")
    purelib = prefix / "lib" / "python3.12" / "site-packages"
    _assert_components(purelib)

    specifications: dict[str, tuple[str, str]] = {}
    expected_package_keys = {
        "direct", "installedCodeTreeSha256", "name", "version",
        "wheelFilename", "wheelSha256", "wheelTag",
    }
    for package in packages:
        if (
            type(package) is not dict
            or set(package) != expected_package_keys
            or type(package.get("name")) is not str
            or type(package.get("version")) is not str
            or type(package.get("installedCodeTreeSha256")) is not str
        ):
            raise GoldenV2SealError("runtime package manifest is not exact")
        name = _normalise_distribution_name(package["name"])
        if name in specifications:
            raise GoldenV2SealError("runtime package names are duplicated")
        specifications[name] = (
            package["version"], package["installedCodeTreeSha256"]
        )
    specifications["pip"] = ("25.1.1", _EXPECTED_PIP_TREE_SHA256)
    specifications["sfep-equipment-quality"] = (
        "1.1.0", _EXPECTED_PRODUCER["installedCodeTreeSha256"]
    )
    before = _runtime_site_inventory(purelib)
    distributions = _discover_runtime_distributions(
        purelib, set(specifications)
    )
    inode_owners: set[tuple[int, int]] = set()
    claimed: set[Path] = set()
    provenance: bytes | None = None
    for name in sorted(specifications, key=str.encode):
        version, expected_tree = specifications[name]
        owned, captured = _verify_installed_distribution(
            purelib=purelib,
            prefix=prefix,
            executable=executable,
            dist_info=distributions[name],
            name=name,
            version=version,
            expected_tree=expected_tree,
            inode_owners=inode_owners,
        )
        claimed.update(owned)
        if name == "sfep-equipment-quality":
            provenance = captured
    after = _runtime_site_inventory(purelib)
    if before != after:
        raise GoldenV2SealError("runtime site-packages changed during authentication")
    if set(after) != {path for path in claimed if purelib in path.parents}:
        raise GoldenV2SealError(
            "runtime site-packages contains an unclaimed or shadow module"
        )
    expected_provenance = {
        "distributionName": "sfep-equipment-quality",
        "producerName": "equipment-quality",
        "requirementsLockSha256": locks.get("requirements"),
        "schemaVersion": "sfep-producer-provenance/v1",
        "sourceDateEpoch": "1735689600",
        "sourceSha256": _EXPECTED_PRODUCER["sourceSha256"],
        "version": "1.1.0",
    }
    if (
        provenance is None
        or _json(provenance, "installed producer provenance")
        != expected_provenance
    ):
        raise GoldenV2SealError(
            "installed producer provenance is not bound to source/wheel identity"
        )
    return purelib


_CHILD_VERIFIER_NAMES = (
    "_assert_components", "_canonical_bytes", "_canonical_float",
    "_canonical_text", "_checked_record_path", "_console_wrapper",
    "_decimal_order", "_decimal_spellings", "_discover_runtime_distributions",
    "_initial_float_spelling", "_json", "_literal", "_metadata_identity",
    "_minimum_spelling_length", "_normalise_distribution_name", "_pairs",
    "_read_file", "_reject_constant", "_rounding_interval",
    "_runtime_site_inventory", "_scaled_floor", "_sha",
    "_verify_installed_distribution", "_verify_runtime_install",
)


# Trust boundary: this literal and the verifier code objects are already
# resident in the executing parent. No child payload is read from the sealer
# path. The retained executable starts the child under -S, and only after this
# bootstrap authenticates the full installed closure does it add purelib.
# macOS cannot fd-pin the final native-module import; eliminating that final
# hash-to-import interval requires an OS sandbox/native loader.
_CHILD_BOOTSTRAP = r'''
import base64, csv, hashlib, io, json, marshal, math, os, re, runpy, stat, sys, types, unicodedata
from fractions import Fraction
from pathlib import Path, PurePosixPath

if sys.flags.no_site != 1 or sys.flags.safe_path != 1 or sys.flags.no_user_site != 1:
    raise SystemExit("compatibility child requires -S -P -s")
envelope = json.loads(sys.stdin.buffer.read())
expected_codes = {
    "_assert_components": ("sha256:8620995391bb29cdbf2f7e83febdb3207c15a46019dee1255ba83b076571295b", (1, 0, 1, 16777219)),
    "_canonical_bytes": ("sha256:0ef542f0930f4ebae7c1717eed6c8ebc9c19711c530adee09abea94c847871f3", (1, 0, 0, 16777219)),
    "_canonical_float": ("sha256:51b4bedaefe6dac499b0c5e801079e9de7818b5c81d616ba1767e23f734e5d1c", (1, 0, 0, 16777219)),
    "_canonical_text": ("sha256:74c069445ca8185a75c584f69117201644a7bfdfa84fc9a377813c7c27754938", (1, 0, 0, 16777219)),
    "_checked_record_path": ("sha256:86ccb6d7e299c3fffb2bc18ede3da7c93f0ba7c67c2f626d3d6ca39cee40efbe", (1, 0, 0, 16777219)),
    "_console_wrapper": ("sha256:7598f7d518ffe966c0406e65fa2ed6b9a105804ef1aa259812dd46b8c98eef90", (2, 0, 0, 16777219)),
    "_decimal_order": ("sha256:b7b92aec01bba7072dcafebb6047b83209d879a1b92a8ea6958c6c1370fee623", (1, 0, 0, 16777219)),
    "_decimal_spellings": ("sha256:cad941936267745e38fd96e27844fb2b8848eb76abbe352db7669040d1d42101", (3, 0, 0, 16777219)),
    "_discover_runtime_distributions": ("sha256:6086e20108a41f7294e3d73667ca738c681518580835235f3310f096fd1c576a", (2, 0, 0, 16777219)),
    "_initial_float_spelling": ("sha256:e69aa736446c648c748a72ab9791c2b58ca016d88c8b7f3e4de2af1012b20920", (1, 0, 0, 16777219)),
    "_json": ("sha256:19fffba38d093564e3fed6d3f9c7c17b39f94bf7f29356eb447500caf5e9a604", (2, 0, 0, 16777219)),
    "_literal": ("sha256:5a1b4c171e0e53490374d303ea9f04220f5fed73e467307dae756497141023b8", (3, 0, 0, 16777219)),
    "_metadata_identity": ("sha256:2e7be708ba2b87932c96b2e4e811f3a89f567f8bfbe67b915e6de89d234843a2", (2, 0, 0, 16777219)),
    "_minimum_spelling_length": ("sha256:964b4cd68bf8372d15efc69424f456429666c6bf80df96896e866893089359c0", (3, 0, 0, 16777219)),
    "_normalise_distribution_name": ("sha256:a6243153171b01c31c8c32852526ece5e1a50124674604c5e11dc2db66dbdae8", (1, 0, 0, 16777219)),
    "_pairs": ("sha256:329dbb6659542e503a92fb0d26ddeba32c3a478b53aae9f69165beb2ad4c44ee", (1, 0, 0, 16777219)),
    "_read_file": ("sha256:fc9904031721b74ae5764928db8035f4ac0ac520b7994f2e534258b213d58b77", (2, 0, 0, 16777219)),
    "_reject_constant": ("sha256:561c279dfef0e29677dea7dab18e59cc5a46ad8c375db63e098e0a4e94fed8d9", (1, 0, 0, 16777219)),
    "_rounding_interval": ("sha256:181a55672fd36aba86583d4403324b32d469e0024f634cf4466191b74617cc54", (1, 0, 0, 16777219)),
    "_runtime_site_inventory": ("sha256:391ae0c3509b7e05f7cb1c6718ffdffea5969d0e6b84ea8c0bbaec63c7970b57", (1, 0, 0, 16777219)),
    "_scaled_floor": ("sha256:7f787946c543dbc8923f927972c2148f5fbcbf4a40dd1780f0c928027827f9e5", (2, 0, 0, 16777219)),
    "_sha": ("sha256:63ef36af6aed78bc91c7153ff3f1d77da137ad60f4459439baddc137e61c9cf7", (1, 0, 0, 16777219)),
    "_verify_installed_distribution": ("sha256:816280e82611297e87988a123715eff3c1b12dc0107a20fe93105b9a3929c1b6", (0, 0, 8, 16777219)),
    "_verify_runtime_install": ("sha256:7ca13e72d811dfaf54373380021c37d61051aa0f951ebb3e56349c1ee390e330", (1, 0, 0, 16777219)),
}
encoded_codes = envelope.pop("verifierCode")
if type(encoded_codes) is not dict or set(encoded_codes) != set(expected_codes) or len(encoded_codes) != 24:
    raise SystemExit("resident verifier code inventory is not exact")

class GoldenV2SealError(RuntimeError):
    pass

namespace = {
    "__builtins__": __builtins__,
    "Fraction": Fraction,
    "GoldenV2SealError": GoldenV2SealError,
    "Path": Path,
    "PurePosixPath": PurePosixPath,
    "_DIST_INFO_EXCLUSIONS": frozenset({"RECORD", "INSTALLER", "direct_url.json", "REQUESTED"}),
    "_EXPECTED_EXECUTABLE_SHA256": "sha256:68d03d8a962f8a756e3a2ba0fc95a8361719c0548f257f5f069ac090727d1d14",
    "_EXPECTED_PIP_TREE_SHA256": "sha256:b7c42058a2b36be74282b0b1ac72379b47ad1c54eea6186273fc4e68c9e78801",
    "_EXPECTED_PRODUCER": {
        "installedCodeTreeSha256": "sha256:210fc9b61b174cda72e85b2e6eb32b83dcc8642a52718ea93871c91356f59f8a",
        "name": "equipment-quality",
        "sourceSha256": "sha256:26eefd1d37546b1aeb1a56158bad06765cb40f21f975a1c2efe569410d5d9a05",
        "version": "1.1.0",
        "wheelFilename": "sfep_equipment_quality-1.1.0-py3-none-any.whl",
        "wheelSha256": "sha256:abdfb2738c4eea6dd9e8c832951deef5faff4795ffbae39c6a9617cac293d514",
    },
    "_GENERATED_SCRIPTS": {
        "jsonschema": {"jsonschema": "jsonschema.cli:main"},
        "numpy": {"f2py": "numpy.f2py.f2py2e:main", "numpy-config": "numpy._configtool:main"},
        "pip": {"pip": "pip._internal.cli.main:main", "pip3": "pip._internal.cli.main:main", "pip3.12": "pip._internal.cli.main:main"},
        "sfep-equipment-quality": {"sfep-equipment-quality": "equipment_quality.cli:main"},
    },
    "_POWER10": tuple(10**exponent for exponent in range(400)),
    "_RECORD_HASH": re.compile(r"sha256=([A-Za-z0-9_-]{43})\Z"),
    "_RECORD_SIZE": re.compile(r"(?:0|[1-9][0-9]*)\Z"),
    "_V2_RUNTIME_LITERAL": (5537, "3bd2a358461949cff481ecf813b4d7bc4bc2e40b6f8da1877db2df5ea4d4702a"),
    "base64": base64, "csv": csv, "hashlib": hashlib, "io": io,
    "json": json, "math": math, "os": os, "re": re, "stat": stat,
    "sys": sys, "unicodedata": unicodedata,
}
functions = {}

def code_value(value):
    if type(value) is types.CodeType:
        return ["code", code_payload(value)]
    if type(value) is tuple:
        return ["tuple", [code_value(item) for item in value]]
    if type(value) is frozenset:
        converted = [code_value(item) for item in value]
        return ["frozenset", sorted(
            converted,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )]
    if type(value) is bytes:
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if value is None:
        return ["none"]
    if type(value) is bool:
        return ["bool", value]
    if type(value) is int:
        return ["int", str(value)]
    if type(value) is float:
        return ["float", value.hex()]
    if type(value) is complex:
        return ["complex", value.real.hex(), value.imag.hex()]
    if type(value) is str:
        return ["str", value]
    raise SystemExit("resident verifier code constant is unsupported")

def code_payload(code):
    return {
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "nlocals": code.co_nlocals,
        "stacksize": code.co_stacksize,
        "flags": code.co_flags,
        "code": base64.b64encode(code.co_code).decode("ascii"),
        "consts": [code_value(value) for value in code.co_consts],
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars),
        "cellvars": list(code.co_cellvars),
        "exceptiontable": base64.b64encode(code.co_exceptiontable).decode("ascii"),
    }

for name in sorted(expected_codes, key=str.encode):
    raw = base64.b64decode(encoded_codes[name], validate=True)
    digest, shape = expected_codes[name]
    code = marshal.loads(raw)
    if type(code) is not types.CodeType:
        raise SystemExit("resident verifier payload is not code for " + name)
    actual_shape = (code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount, code.co_flags)
    canonical_code = json.dumps(
        code_payload(code), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    if "sha256:" + hashlib.sha256(canonical_code).hexdigest() != digest:
        raise SystemExit("resident verifier code digest differs for " + name)
    if code.co_name != name or code.co_freevars or actual_shape != shape:
        raise SystemExit("resident verifier code shape differs for " + name)
    functions[name] = types.FunctionType(code, namespace, name)
functions["_assert_components"].__kwdefaults__ = {"missing_final": False}
namespace.update(functions)

expected_executable = envelope.get("expectedExecutable")
if type(expected_executable) is not str or expected_executable != sys.executable:
    raise SystemExit("compatibility child executable identity is not exact")
encoded_runtime = envelope.get("runtime")
if type(encoded_runtime) is not str:
    raise SystemExit("compatibility child runtime envelope is invalid")
runtime_payload = base64.b64decode(encoded_runtime, validate=True)
purelib = functions["_verify_runtime_install"](runtime_payload)
sys.path.insert(0, os.fspath(purelib))

mode = envelope.get("mode")
common = {"expectedExecutable", "mode", "runtime"}
output_names = (
    "analysis_config.json", "producer_runtime.json",
    "equipment_operating_ranges.json", "quality_risk_intervals.json",
    "replay_events.csv", "analysis_summary.json", "bundle_manifest.json",
)
if mode == "schema":
    if set(envelope) != common | {"outputs"}:
        raise SystemExit("schema child envelope keys are not exact")
    encoded_outputs = envelope.get("outputs")
    if type(encoded_outputs) is not dict or set(encoded_outputs) != set(output_names):
        raise SystemExit("schema child output envelope is invalid")
    payloads = {
        name: base64.b64decode(encoded_outputs[name], validate=True)
        for name in output_names
    }
    contract_module = __import__("equipment_quality.bundle_contract", fromlist=("V2_CONTRACT",))
    schema_module = __import__("equipment_quality.schema", fromlist=("validate_contract_instance",))
    contract = contract_module.V2_CONTRACT
    validate = schema_module.validate_contract_instance
    objects = {
        role: json.loads(payloads[filename])
        for role, filename in (
            ("analysis_config", "analysis_config.json"),
            ("producer_runtime", "producer_runtime.json"),
            ("equipment_operating_ranges", "equipment_operating_ranges.json"),
            ("quality_risk_intervals", "quality_risk_intervals.json"),
            ("analysis_summary", "analysis_summary.json"),
            ("bundle_manifest", "bundle_manifest.json"),
        )
    }
    for role, value in objects.items():
        validate(contract, role, value)
    reader = csv.DictReader(io.StringIO(payloads["replay_events.csv"].decode("utf-8"), newline=""))
    for row in reader:
        row["replay_hour"] = None if row["replay_hour"] == "" else int(row["replay_hour"])
        for name in ("equipment_batch_id", "hr_coil_id", "ap_prod_id"):
            row[name] = row[name] or None
        row["values_json"] = json.loads(row["values_json"])
        validate(contract, "replay_events", row)
    sys.stdout.write("SCHEMA_OK\n")
elif mode == "producer":
    required = common | {"analysisConfig", "contractRoot", "outputRoot", "runtimePath"}
    if set(envelope) != required or any(type(envelope.get(name)) is not str for name in required - common):
        raise SystemExit("producer child envelope is invalid")
    sys.argv = [
        "sfep-equipment-quality",
        "--config", envelope["analysisConfig"],
        "--runtime-manifest", envelope["runtimePath"],
        "--data-dir", os.fspath(Path(envelope["contractRoot"]) / "golden-source"),
        "--output-dir", envelope["outputRoot"],
    ]
    runpy.run_module("equipment_quality.cli", run_name="__main__", alter_sys=False)
else:
    raise SystemExit("compatibility child mode is invalid")
'''


def _sanitized_environment() -> dict[str, str]:
    return {
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TZ": "Asia/Seoul",
    }


def _child_envelope(values: Mapping[str, object]) -> bytes:
    marshal_module = __import__("marshal")
    envelope = dict(values)
    envelope.update({
        "expectedExecutable": os.fspath(Path(sys.executable)),
        "verifierCode": {
            name: base64.b64encode(
                marshal_module.dumps(globals()[name].__code__)
            ).decode("ascii")
            for name in _CHILD_VERIFIER_NAMES
        },
    })
    return _canonical_bytes(envelope)


def _validate_output_schemas(outputs: Mapping[str, bytes]) -> None:
    envelope = _child_envelope({
        "mode": "schema",
        "runtime": base64.b64encode(outputs["producer_runtime.json"]).decode("ascii"),
        "outputs": {
            name: base64.b64encode(outputs[name]).decode("ascii")
            for name in sorted(outputs, key=str.encode)
        },
    })
    result = subprocess.run(
        [sys.executable, "-S", "-P", "-s", "-B", "-c", _CHILD_BOOTSTRAP],
        input=envelope,
        capture_output=True,
        check=False,
        env=_sanitized_environment(),
    )
    if result.returncode != 0 or result.stdout != b"SCHEMA_OK\n" or result.stderr:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GoldenV2SealError(f"generated v2 schema validation failed: {detail}")


def _run_producer(
    contract_root: Path,
    analysis_config: Path,
    runtime_manifest: Path,
    temporary_root: Path,
) -> Path:
    runtime_payload = _read_file(runtime_manifest, "v2 runtime manifest child input")[0]
    envelope = _child_envelope({
        "analysisConfig": os.fspath(analysis_config),
        "contractRoot": os.fspath(contract_root),
        "mode": "producer",
        "outputRoot": os.fspath(temporary_root),
        "runtime": base64.b64encode(runtime_payload).decode("ascii"),
        "runtimePath": os.fspath(runtime_manifest),
    })
    command = [
        sys.executable, "-S", "-P", "-s", "-B", "-c", _CHILD_BOOTSTRAP,
    ]
    result = subprocess.run(
        command,
        input=envelope,
        check=False,
        capture_output=True,
        env=_sanitized_environment(),
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GoldenV2SealError(f"compatibility producer failed: {detail}")
    entries = tuple(temporary_root.iterdir())
    bundles = tuple(
        entry for entry in entries if entry.name != ".sfep-equipment-quality.lock"
    )
    lock = temporary_root / ".sfep-equipment-quality.lock"
    try:
        lock_stat = os.lstat(lock)
    except OSError as error:
        raise GoldenV2SealError("compatibility producer publication lock is unavailable") from error
    if (
        len(entries) != 2
        or len(bundles) != 1
        or not stat.S_ISREG(lock_stat.st_mode)
        or lock_stat.st_size != 0
        or not bundles[0].is_dir()
        or bundles[0].is_symlink()
    ):
        raise GoldenV2SealError("compatibility producer did not create exactly one Bundle")
    return bundles[0]


def _write_file(path: Path, payload: bytes, directory_fd: int | None = None) -> None:
    target: str | Path = path if directory_fd is None else path.name
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        _FILE_MODE,
        dir_fd=directory_fd,
    )
    try:
        os.fchmod(descriptor, _FILE_MODE)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise GoldenV2SealError("short artifact write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        _close_preserving_primary(descriptor, "artifact close diagnostic")


def _close_preserving_primary(descriptor: int, label: str) -> None:
    primary_error = sys.exception()
    try:
        os.close(descriptor)
    except Exception as error:
        if primary_error is None:
            raise
        primary_error.add_note(f"{label}: {error}")


def _atomic_install_exclusive(parent_fd: int, source: str, target: str) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    if hasattr(library, "renameatx_np"):
        function = library.renameatx_np
        flag = 0x00000004
    elif hasattr(library, "renameat2"):
        function = library.renameat2
        flag = 0x00000001
    else:
        raise GoldenV2SealError("platform lacks atomic directory no-replace")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(parent_fd, os.fsencode(source), parent_fd, os.fsencode(target), flag) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), target)


def _parent_identity(path: Path, descriptor: int) -> tuple[int, int, int]:
    opened = os.fstat(descriptor)
    lexical = path.lstat()
    opened_identity = (opened.st_dev, opened.st_ino, opened.st_mode)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(lexical.st_mode)
        or (lexical.st_dev, lexical.st_ino, lexical.st_mode) != opened_identity
    ):
        raise GoldenV2SealError("publication parent identity changed")
    return opened_identity


def _open_directory(path: Path, label: str) -> int:
    if not path.is_absolute():
        raise GoldenV2SealError(f"{label} must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise GoldenV2SealError(f"{label} is not a directory")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _require_parent_identity(path: Path, descriptor: int, expected: tuple[int, int, int]) -> None:
    if _parent_identity(path, descriptor) != expected:
        raise GoldenV2SealError("publication parent identity changed")


def _require_lock_identity(
    parent_fd: int,
    lock_fd: int,
    expected: tuple[int, int, int],
) -> None:
    opened = os.fstat(lock_fd)
    try:
        entry = os.stat(_PUBLICATION_LOCK, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError as error:
        raise GoldenV2SealError("golden v2 publication lock was removed") from error
    if (
        (opened.st_dev, opened.st_ino, opened.st_mode) != expected
        or (entry.st_dev, entry.st_ino, entry.st_mode) != expected
        or opened.st_nlink != 1
        or entry.st_nlink != 1
        or not stat.S_ISREG(opened.st_mode)
    ):
        raise GoldenV2SealError("golden v2 publication lock identity changed")


def _unlink_owned_lock(
    parent_fd: int,
    lock_fd: int,
    expected: tuple[int, int, int] | None,
) -> None:
    opened = os.fstat(lock_fd)
    opened_identity = (opened.st_dev, opened.st_ino, opened.st_mode)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or (expected is not None and opened_identity != expected)
    ):
        raise GoldenV2SealError("refusing to remove an unowned publication lock")
    try:
        entry = os.stat(_PUBLICATION_LOCK, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError as error:
        raise GoldenV2SealError("owned publication lock disappeared") from error
    if (
        (entry.st_dev, entry.st_ino, entry.st_mode) != opened_identity
        or entry.st_nlink != 1
        or not stat.S_ISREG(entry.st_mode)
    ):
        raise GoldenV2SealError("refusing to remove an unowned publication lock")
    os.unlink(_PUBLICATION_LOCK, dir_fd=parent_fd)


def _read_file_at(directory_fd: int, name: str, label: str) -> bytes:
    initial = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    initial_identity = (initial.st_dev, initial.st_ino, initial.st_mode)
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise GoldenV2SealError(f"{label} must be a regular non-hardlinked file")
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        before = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino, before.st_mode) != initial_identity
            or before.st_nlink != 1
        ):
            raise GoldenV2SealError(f"{label} identity changed before read")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        _close_preserving_primary(descriptor, f"{label} close diagnostic")
    fingerprint = (
        before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
        before.st_size, before.st_mtime_ns, before.st_ctime_ns,
    )
    if fingerprint != (
        after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
        after.st_size, after.st_mtime_ns, after.st_ctime_ns,
    ):
        raise GoldenV2SealError(f"{label} changed while being read")
    final = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if (
        (final.st_dev, final.st_ino, final.st_mode) != initial_identity
        or final.st_nlink != 1
    ):
        raise GoldenV2SealError(f"{label} path was replaced while being read")
    payload = b"".join(chunks)
    if len(payload) != before.st_size:
        raise GoldenV2SealError(f"{label} size changed while being read")
    return payload


def _cleanup_owned_scratch(
    parent_fd: int,
    scratch_name: str,
    scratch_fd: int,
    scratch_identity: tuple[int, int, int],
) -> None:
    opened = os.fstat(scratch_fd)
    if (
        (opened.st_dev, opened.st_ino, opened.st_mode) != scratch_identity
        or not stat.S_ISDIR(opened.st_mode)
    ):
        raise GoldenV2SealError("refusing to remove an unowned publication scratch")
    owned_names = []
    for name in os.listdir(parent_fd):
        try:
            entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if (entry.st_dev, entry.st_ino, entry.st_mode) == scratch_identity:
            owned_names.append(name)
    if len(owned_names) != 1:
        raise GoldenV2SealError("owned publication scratch entry is missing or ambiguous")
    owned_name = owned_names[0]
    for name in os.listdir(scratch_fd):
        if name in {"", ".", ".."} or "/" in name:
            raise GoldenV2SealError("publication scratch contains an unsafe entry")
        metadata = os.stat(name, dir_fd=scratch_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            raise GoldenV2SealError("refusing recursive deletion in publication scratch")
        os.unlink(name, dir_fd=scratch_fd)
    os.fsync(scratch_fd)
    os.rmdir(owned_name, dir_fd=parent_fd)
    os.fsync(parent_fd)


def _cleanup_early_owned_scratch(
    parent_fd: int,
    scratch_identity: tuple[int, int, int],
) -> None:
    owned_names = []
    for name in os.listdir(parent_fd):
        try:
            entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if (entry.st_dev, entry.st_ino, entry.st_mode) == scratch_identity:
            owned_names.append(name)
    if len(owned_names) != 1:
        raise GoldenV2SealError("early publication scratch entry is missing or ambiguous")
    owned_name = owned_names[0]
    descriptor = os.open(
        owned_name,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    try:
        opened = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino, opened.st_mode) != scratch_identity
            or not stat.S_ISDIR(opened.st_mode)
            or os.listdir(descriptor)
        ):
            raise GoldenV2SealError("refusing to remove an unowned or nonempty early scratch")
        final = os.stat(owned_name, dir_fd=parent_fd, follow_symlinks=False)
        if (final.st_dev, final.st_ino, final.st_mode) != scratch_identity:
            raise GoldenV2SealError("early publication scratch identity changed")
    finally:
        os.close(descriptor)
    os.rmdir(owned_name, dir_fd=parent_fd)
    os.fsync(parent_fd)


def _publication_inventory(
    parent_fd: int,
) -> dict[str, tuple[int, int, int, int, int, int, int]]:
    names = tuple(sorted(os.listdir(parent_fd), key=str.encode))
    inventory: dict[str, tuple[int, int, int, int, int, int, int]] = {}
    for name in names:
        try:
            entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as error:
            raise GoldenV2SealError("publication parent inventory changed") from error
        inventory[name] = (
            entry.st_dev,
            entry.st_ino,
            entry.st_mode,
            entry.st_nlink,
            entry.st_size,
            entry.st_mtime_ns,
            entry.st_ctime_ns,
        )
    if tuple(sorted(os.listdir(parent_fd), key=str.encode)) != names:
        raise GoldenV2SealError("publication parent inventory changed")
    return inventory


def _parent_mutation_epoch(
    parent_fd: int,
) -> tuple[int, int, int, int, int, int, int, int, int, int, int]:
    metadata = os.fstat(parent_fd)
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        getattr(metadata, "st_flags", 0),
        getattr(metadata, "st_birthtime_ns", 0),
    )


def _publish_bundle(
    output: Path,
    outputs: Mapping[str, bytes],
    reauthenticate: Callable[[], None],
    pinned_parent_fd: int | None = None,
    pinned_parent_identity: tuple[int, int, int] | None = None,
) -> str:
    parent_fd = (
        _open_directory(output.parent, "publication parent")
        if pinned_parent_fd is None
        else os.dup(pinned_parent_fd)
    )
    try:
        parent_identity = _parent_identity(output.parent, parent_fd)
        if (
            pinned_parent_identity is not None
            and parent_identity != pinned_parent_identity
        ):
            raise GoldenV2SealError("publication parent differs from its pinned identity")
    except Exception:
        os.close(parent_fd)
        raise
    lock_fd: int | None = None
    lock_identity: tuple[int, int, int] | None = None
    lock_owned = False
    lock_present = False
    scratch_name = ""
    scratch_fd: int | None = None
    scratch_identity: tuple[int, int, int] | None = None
    scratch_present = False
    scratch_fd_cleanup_safe = False
    scratch_ambiguous = False
    committed = False
    success = False
    try:
        try:
            lock_fd = os.open(
                _PUBLICATION_LOCK,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                _FILE_MODE,
                dir_fd=parent_fd,
            )
            lock_owned = True
            lock_present = True
        except FileExistsError as error:
            raise GoldenV2SealError("golden v2 publication lock is already held") from error
        os.fchmod(lock_fd, _FILE_MODE)
        lock_stat = os.fstat(lock_fd)
        lock_identity = (lock_stat.st_dev, lock_stat.st_ino, lock_stat.st_mode)
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
            raise GoldenV2SealError("golden v2 publication lock identity is invalid")
        _require_lock_identity(parent_fd, lock_fd, lock_identity)
        _require_parent_identity(output.parent, parent_fd, parent_identity)
        try:
            os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise GoldenV2SealError("output already exists; golden v2 publication is no-clobber")
        inventory_before_scratch = _publication_inventory(parent_fd)
        for _attempt in range(128):
            candidate = _SCRATCH_PREFIX + secrets.token_hex(12)
            try:
                os.mkdir(candidate, _DIRECTORY_MODE, dir_fd=parent_fd)
            except FileExistsError:
                continue
            scratch_name = candidate
            scratch_present = True
            break
        if not scratch_name:
            raise GoldenV2SealError("cannot allocate publication scratch")
        # POSIX mkdirat() returns no descriptor. Pin the first observable
        # post-mkdir parent mutation epoch, then open the directory before any
        # path stat. A parent mutation after that epoch makes ownership
        # ambiguous; cleanup preserves every candidate instead of deleting by
        # name. The mkdir-return-to-first-fstat interval cannot be eliminated
        # without an atomic create-directory-and-return-FD primitive.
        post_mkdir_epoch = None
        for epoch_attempt in range(2):
            try:
                post_mkdir_epoch = _parent_mutation_epoch(parent_fd)
                break
            except OSError:
                if epoch_attempt != 0:
                    raise
        if post_mkdir_epoch is None:
            raise GoldenV2SealError("publication parent epoch is unavailable")
        scratch = output.parent / scratch_name
        scratch_open_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for open_attempt in range(2):
            try:
                scratch_fd = os.open(
                    scratch_name,
                    scratch_open_flags,
                    dir_fd=parent_fd,
                )
                break
            except OSError:
                if _parent_mutation_epoch(parent_fd) != post_mkdir_epoch:
                    scratch_ambiguous = True
                    raise GoldenV2SealError(
                        "publication scratch ownership is ambiguous; parent epoch changed"
                    )
                if open_attempt != 0:
                    raise
        if scratch_fd is None:
            raise GoldenV2SealError("publication scratch descriptor is unavailable")
        if _parent_mutation_epoch(parent_fd) != post_mkdir_epoch:
            scratch_ambiguous = True
            raise GoldenV2SealError(
                "publication scratch ownership is ambiguous; parent epoch changed"
            )
        scratch_fd_cleanup_safe = True
        scratch_stat = os.fstat(scratch_fd)
        candidate_identity = (
            scratch_stat.st_dev, scratch_stat.st_ino, scratch_stat.st_mode
        )
        if not stat.S_ISDIR(scratch_stat.st_mode):
            raise GoldenV2SealError("new publication scratch is not a directory")
        scratch_entry = None
        for identity_attempt in range(2):
            try:
                scratch_entry = os.stat(
                    scratch_name, dir_fd=parent_fd, follow_symlinks=False
                )
                break
            except OSError:
                if _parent_mutation_epoch(parent_fd) != post_mkdir_epoch:
                    scratch_ambiguous = True
                    raise GoldenV2SealError(
                        "publication scratch ownership is ambiguous; parent epoch changed"
                    )
                if identity_attempt != 0:
                    raise
        if scratch_entry is None:
            raise GoldenV2SealError("publication scratch identity is unavailable")
        if _parent_mutation_epoch(parent_fd) != post_mkdir_epoch:
            scratch_ambiguous = True
            raise GoldenV2SealError(
                "publication scratch ownership is ambiguous; parent epoch changed"
            )
        if (
            (scratch_entry.st_dev, scratch_entry.st_ino, scratch_entry.st_mode)
            != candidate_identity
        ):
            scratch_ambiguous = True
            raise GoldenV2SealError(
                "publication scratch ownership is ambiguous; opened identity changed"
            )
        expected_inventory = dict(inventory_before_scratch)
        expected_inventory[scratch_name] = (
            scratch_entry.st_dev,
            scratch_entry.st_ino,
            scratch_entry.st_mode,
            scratch_entry.st_nlink,
            scratch_entry.st_size,
            scratch_entry.st_mtime_ns,
            scratch_entry.st_ctime_ns,
        )
        if _publication_inventory(parent_fd) != expected_inventory:
            scratch_ambiguous = True
            raise GoldenV2SealError(
                "publication scratch ownership is ambiguous; parent inventory changed"
            )
        if _parent_mutation_epoch(parent_fd) != post_mkdir_epoch:
            scratch_ambiguous = True
            raise GoldenV2SealError(
                "publication scratch ownership is ambiguous; parent epoch changed"
            )
        scratch_identity = candidate_identity
        os.fchmod(scratch_fd, _DIRECTORY_MODE)
        scratch_stat = os.fstat(scratch_fd)
        if (
            (scratch_stat.st_dev, scratch_stat.st_ino, scratch_stat.st_mode)
            != scratch_identity
        ):
            raise GoldenV2SealError("publication scratch changed during initialization")
        for name in _OUTPUT_NAMES:
            _write_file(scratch / name, outputs[name], scratch_fd)
        os.fsync(scratch_fd)
        _require_lock_identity(parent_fd, lock_fd, lock_identity)
        reauthenticate()
        _require_parent_identity(output.parent, parent_fd, parent_identity)
        _require_lock_identity(parent_fd, lock_fd, lock_identity)
        if set(os.listdir(scratch_fd)) != set(_OUTPUT_NAMES):
            raise GoldenV2SealError("publication scratch inventory changed")
        for name in _OUTPUT_NAMES:
            if _read_file_at(scratch_fd, name, "scratch/" + name) != outputs[name]:
                raise GoldenV2SealError("publication scratch bytes changed")
        entry = os.stat(scratch_name, dir_fd=parent_fd, follow_symlinks=False)
        if (entry.st_dev, entry.st_ino, entry.st_mode) != scratch_identity:
            raise GoldenV2SealError("publication scratch identity changed")
        try:
            os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise GoldenV2SealError("output appeared at commit; publication is no-clobber")
        _require_parent_identity(output.parent, parent_fd, parent_identity)
        _require_lock_identity(parent_fd, lock_fd, lock_identity)
        _atomic_install_exclusive(parent_fd, scratch_name, output.name)
        scratch_present = False
        committed = True
        published_stat = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        if (published_stat.st_dev, published_stat.st_ino, published_stat.st_mode) != scratch_identity:
            raise GoldenV2SealError("published output is not the retained scratch")
        _require_parent_identity(output.parent, parent_fd, parent_identity)
        _require_lock_identity(parent_fd, lock_fd, lock_identity)
        if set(os.listdir(scratch_fd)) != set(_OUTPUT_NAMES):
            raise GoldenV2SealError("published output inventory changed")
        for name in _OUTPUT_NAMES:
            if _read_file_at(scratch_fd, name, "published/" + name) != outputs[name]:
                raise GoldenV2SealError("published output bytes changed")
        os.fsync(parent_fd)
        _unlink_owned_lock(parent_fd, lock_fd, lock_identity)
        lock_present = False
        os.fsync(parent_fd)
        success = True
        return "created"
    finally:
        primary_error = sys.exception()
        cleanup_errors: list[Exception] = []
        if committed and not success and scratch_identity is not None:
            try:
                current = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if (current.st_dev, current.st_ino, current.st_mode) == scratch_identity:
                    rollback_names = [
                        scratch_name,
                        *(
                            _SCRATCH_PREFIX + secrets.token_hex(12)
                            for _attempt in range(128)
                        ),
                    ]
                    for rollback_name in rollback_names:
                        try:
                            _atomic_install_exclusive(parent_fd, output.name, rollback_name)
                        except OSError:
                            continue
                        scratch_present = True
                        committed = False
                        break
                else:
                    cleanup_errors.append(GoldenV2SealError(
                        "refusing to roll back an unowned published target"
                    ))
            if committed and not cleanup_errors:
                cleanup_errors.append(GoldenV2SealError(
                    "published target could not be safely rolled back"
                ))
        if scratch_ambiguous:
            pass
        elif scratch_present and scratch_fd is not None and scratch_identity is not None:
            try:
                _cleanup_owned_scratch(parent_fd, scratch_name, scratch_fd, scratch_identity)
            except Exception as error:
                cleanup_errors.append(error)
        elif scratch_present and scratch_fd is not None and scratch_fd_cleanup_safe:
            try:
                opened = os.fstat(scratch_fd)
                cleanup_identity = (opened.st_dev, opened.st_ino, opened.st_mode)
                _cleanup_owned_scratch(
                    parent_fd, scratch_name, scratch_fd, cleanup_identity
                )
            except Exception as error:
                cleanup_errors.append(error)
        elif scratch_present and scratch_identity is not None:
            try:
                _cleanup_early_owned_scratch(parent_fd, scratch_identity)
            except Exception as error:
                cleanup_errors.append(error)
        if lock_owned and lock_present and lock_fd is not None:
            try:
                _unlink_owned_lock(parent_fd, lock_fd, lock_identity)
                lock_present = False
                os.fsync(parent_fd)
            except Exception as error:
                cleanup_errors.append(error)
        for descriptor in (scratch_fd, lock_fd, parent_fd):
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except Exception as error:
                cleanup_errors.append(error)
        if cleanup_errors and not success:
            if primary_error is not None:
                for error in cleanup_errors:
                    primary_error.add_note(f"cleanup diagnostic: {error}")
            else:
                first, *remaining = cleanup_errors
                for error in remaining:
                    first.add_note(f"cleanup diagnostic: {error}")
                raise first


def _validate_topology(contract_root: Path, config: Path, runtime: Path, output: Path) -> None:
    for path in (contract_root, config, runtime):
        _assert_components(path)
    _assert_components(output.parent)
    _assert_components(output, missing_final=True)
    resolved_output = output.parent.resolve(strict=True) / output.name
    protected = (contract_root.resolve(strict=True), config.resolve(strict=True), runtime.resolve(strict=True))
    if any(resolved_output == path or path in resolved_output.parents or resolved_output in path.parents for path in protected):
        raise GoldenV2SealError("output overlaps an authenticated input")
    if output.exists() or output.is_symlink():
        raise GoldenV2SealError("output already exists; golden v2 publication is no-clobber")


def _run(contract_root: Path, config: Path, runtime: Path, output: Path) -> tuple[str, str]:
    contract_root = _checked_path(contract_root, "contract root")
    config = _checked_path(config, "analysis config")
    runtime = _checked_path(runtime, "runtime manifest")
    output = _checked_path(output, "output directory")
    parent_fd = _open_directory(output.parent, "publication parent")
    completed = False
    try:
        _validate_topology(contract_root, config, runtime, output)
        parent_identity = _parent_identity(output.parent, parent_fd)
        inputs, fingerprints = _capture_inputs(contract_root, config, runtime)
        _verify_runtime_install(inputs["v2/runtime"])
        with tempfile.TemporaryDirectory(
            prefix="sfep-golden-v2-producer-", dir="/private/tmp"
        ) as temporary:
            generated = _run_producer(contract_root, config, runtime, Path(temporary))
            _verify_runtime_install(inputs["v2/runtime"])
            bundle_id, outputs = _verify_generated(generated, inputs)
            _verify_runtime_install(inputs["v2/runtime"])

        def reauthenticate() -> None:
            current, current_fingerprints = _capture_inputs(contract_root, config, runtime)
            if current != inputs or current_fingerprints != fingerprints:
                raise GoldenV2SealError("authenticated inputs changed before publication")
            _verify_runtime_install(current["v2/runtime"])

        status = _publish_bundle(
            output,
            outputs,
            reauthenticate,
            parent_fd,
            parent_identity,
        )
        completed = True
        return status, bundle_id
    finally:
        primary_error = sys.exception()
        try:
            os.close(parent_fd)
        except Exception as error:
            if completed:
                pass
            elif primary_error is not None:
                primary_error.add_note(f"cleanup diagnostic: {error}")
            else:
                raise


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--contract-root", required=True)
    parser.add_argument("--analysis-config", required=True)
    parser.add_argument("--runtime-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if sys.flags.no_site != 1:
        print("error: golden v2 sealing requires Python -S", file=sys.stderr)
        return 2
    arguments = _parser().parse_args(argv)
    try:
        status, bundle_id = _run(
            Path(arguments.contract_root),
            Path(arguments.analysis_config),
            Path(arguments.runtime_manifest),
            Path(arguments.output_dir),
        )
    except (GoldenV2SealError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"{status} {bundle_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
