#!/usr/bin/env python3
"""Verify and publish a reproducible producer wheel pair."""

from __future__ import annotations

import argparse
import base64
import csv
from email import policy
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import tomllib
import zipfile


_SOURCE_DATE_EPOCH = "1735689600"
_ZIP_EPOCH = (2025, 1, 1, 0, 0, 0)
_SOURCE_HEADER = b"sfep-source-lines/v1\n"
_PROVENANCE_PATH = "equipment_quality/sfep_producer_provenance.json"
_PRODUCER_FILENAME = "sfep_equipment_quality-1.0.0-py3-none-any.whl"
_DIST_INFO = "sfep_equipment_quality-1.0.0.dist-info"
_METADATA_PATH = f"{_DIST_INFO}/METADATA"
_WHEEL_PATH = f"{_DIST_INFO}/WHEEL"
_RECORD_PATH = f"{_DIST_INFO}/RECORD"
_GENERATED_MEMBERS = frozenset({
    _METADATA_PATH,
    _WHEEL_PATH,
    f"{_DIST_INFO}/entry_points.txt",
    f"{_DIST_INFO}/top_level.txt",
    _RECORD_PATH,
})
_EXPECTED_METADATA = (
    b"Metadata-Version: 2.4\n"
    b"Name: sfep-equipment-quality\n"
    b"Version: 1.0.0\n"
    b"Requires-Python: ==3.12.*\n"
    b"Requires-Dist: numpy==2.2.6\n"
    b"Requires-Dist: pandas==2.3.0\n"
    b"Requires-Dist: jsonschema==4.24.0\n"
)
_EXPECTED_WHEEL = (
    b"Wheel-Version: 1.0\n"
    b"Generator: setuptools (80.9.0)\n"
    b"Root-Is-Purelib: true\n"
    b"Tag: py3-none-any\n"
    b"\n"
)
_EXPECTED_ENTRY_POINTS = (
    b"[console_scripts]\n"
    b"sfep-equipment-quality = equipment_quality.cli:main\n"
)
_EXPECTED_TOP_LEVEL = b"equipment_quality\n"
_THIRD_PARTY = (
    ("attrs", "26.1.0", "attrs-26.1.0-py3-none-any.whl", "py3-none-any", "c647aa4a12dfbad9333ca4e71fe62ddc36f4e63b2d260a37a8b83d2f043ac309", "runtime"),
    ("build", "1.2.2.post1", "build-1.2.2.post1-py3-none-any.whl", "py3-none-any", "1d61c0887fa860c01971625baae8bdd338e517b836a2f70dd1f7aa3a6b2fc5b5", "build"),
    ("jsonschema", "4.24.0", "jsonschema-4.24.0-py3-none-any.whl", "py3-none-any", "a462455f19f5faf404a7902952b6f0e3ce868f3ee09a359b05eca6673bd8412d", "runtime"),
    ("jsonschema-specifications", "2025.9.1", "jsonschema_specifications-2025.9.1-py3-none-any.whl", "py3-none-any", "98802fee3a11ee76ecaca44429fda8a41bff98b00a0f2838151b113f210cc6fe", "runtime"),
    ("numpy", "2.2.6", "numpy-2.2.6-cp312-cp312-macosx_14_0_arm64.whl", "cp312-cp312-macosx_14_0_arm64", "894b3a42502226a1cac872f840030665f33326fc3dac8e57c607905773cdcde3", "runtime"),
    ("packaging", "26.3", "packaging-26.3-py3-none-any.whl", "py3-none-any", "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c", "build"),
    ("pandas", "2.3.0", "pandas-2.3.0-cp312-cp312-macosx_11_0_arm64.whl", "cp312-cp312-macosx_11_0_arm64", "b9d8c3187be7479ea5c3d30c32a5d73d62a621166675063b2edd21bc47614027", "runtime"),
    ("pip", "25.1.1", "pip-25.1.1-py3-none-any.whl", "py3-none-any", "2913a38a2abf4ea6b64ab507bd9e967f3b53dc1ede74b01b0931e1ce548751af", "bootstrap"),
    ("pyproject-hooks", "1.2.0", "pyproject_hooks-1.2.0-py3-none-any.whl", "py3-none-any", "9e5c6bfa8dcc30091c74b0cf803c81fdd29d94f01992a7707bc97babb1141913", "build"),
    ("python-dateutil", "2.9.0.post0", "python_dateutil-2.9.0.post0-py2.py3-none-any.whl", "py2.py3-none-any", "a8b2bc7bffae282281c8140a97d3aa9c14da0b136dfe83f850eea9a5f7470427", "runtime"),
    ("pytz", "2026.3.post1", "pytz-2026.3.post1-py2.py3-none-any.whl", "py2.py3-none-any", "dd95840dd199baea12d9cc096a1d452caa6596a1c1e4b5f3dbd1541855d5e815", "runtime"),
    ("referencing", "0.37.0", "referencing-0.37.0-py3-none-any.whl", "py3-none-any", "381329a9f99628c9069361716891d34ad94af76e461dcb0335825aecc7692231", "runtime"),
    ("rpds-py", "2026.6.3", "rpds_py-2026.6.3-cp312-cp312-macosx_11_0_arm64.whl", "cp312-cp312-macosx_11_0_arm64", "538949e262e46caa31ac01bdb3c1e8f642622922cacbabbae6a8445d9dc33eaf", "runtime"),
    ("setuptools", "80.9.0", "setuptools-80.9.0-py3-none-any.whl", "py3-none-any", "062d34222ad13e0cc312a4c02d73f059e86a4acbfbdea8f8f76b28c99f306922", "build"),
    ("six", "1.17.0", "six-1.17.0-py2.py3-none-any.whl", "py2.py3-none-any", "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274", "runtime"),
    ("typing-extensions", "4.16.0", "typing_extensions-4.16.0-py3-none-any.whl", "py3-none-any", "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8", "runtime"),
    ("tzdata", "2026.3", "tzdata-2026.3-py2.py3-none-any.whl", "py2.py3-none-any", "dc096730c87af6cab1b171c9d532be840741ff5d459015e7f6947bd7d7e54931", "runtime"),
    ("wheel", "0.45.1", "wheel-0.45.1-py3-none-any.whl", "py3-none-any", "708e7481cc80179af0e556bbf0cc00b8444c7321e2700b8d8580231d13017248", "build"),
)
_LOCK_FILES = {
    "bootstrap.lock": "bootstrap",
    "build-requirements.lock": "build",
    "requirements.lock": "runtime",
}
_HASH_URI = re.compile(r"^sha256:[0-9a-f]{64}$")
_RECORD_HASH = re.compile(r"^sha256=([A-Za-z0-9_-]{43})$")
_RECORD_SIZE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_PEP503 = re.compile(r"[-_.]+")


class ProducerSealError(RuntimeError):
    """The producer build cannot be authenticated or safely published."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--wheel-dir-a", required=True)
    parser.add_argument("--wheel-dir-b", required=True)
    parser.add_argument("--wheelhouse", required=True)
    parser.add_argument("--producer-lock", required=True)
    return parser


def _sha256_uri(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _checked_absolute(raw: str, label: str) -> Path:
    if not raw or any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise ProducerSealError(f"{label} path contains a control character")
    path = Path(raw)
    if not path.is_absolute():
        raise ProducerSealError(f"{label} path must be absolute")
    if ".." in path.parts:
        raise ProducerSealError(f"{label} path must not contain a parent segment")
    return path


def _identity(result: os.stat_result) -> tuple[int, int]:
    return result.st_dev, result.st_ino


def _require_directory(path: Path, label: str) -> tuple[int, int]:
    try:
        result = os.lstat(path)
    except OSError as error:
        raise ProducerSealError(f"{label} is unavailable") from error
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISDIR(result.st_mode):
        raise ProducerSealError(f"{label} must be a non-symlink directory")
    return _identity(result)


def _read_regular_once(path: Path, label: str) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as error:
        raise ProducerSealError(f"{label} is unavailable") from error
    if stat.S_ISLNK(before.st_mode):
        raise ProducerSealError(f"{label} must not be a symlink")
    if not stat.S_ISREG(before.st_mode):
        raise ProducerSealError(f"{label} must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ProducerSealError(f"{label} cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
    except OSError as error:
        raise ProducerSealError(f"{label} changed while reading") from error
    finally:
        os.close(descriptor)
    try:
        path_after = os.lstat(path)
    except OSError as error:
        raise ProducerSealError(f"{label} changed while reading") from error
    stable = lambda item: (
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
        stat.S_IFMT(item.st_mode),
    )
    if (
        _identity(before) != _identity(opened)
        or _identity(opened) != _identity(after)
        or _identity(after) != _identity(path_after)
        or stable(before) != stable(opened)
        or stable(opened) != stable(after)
        or stable(after) != stable(path_after)
        or total != after.st_size
    ):
        raise ProducerSealError(f"{label} changed while reading")
    return b"".join(chunks)


def _safe_relative(relative: str, label: str) -> bytes:
    if (
        not relative
        or relative.startswith("/")
        or "\\" in relative
        or any(ord(character) < 32 or ord(character) == 127 for character in relative)
    ):
        raise ProducerSealError(f"{label} contains an unsafe path")
    try:
        encoded = relative.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ProducerSealError(f"{label} path is not UTF-8") from error
    parsed = PurePosixPath(relative)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != relative
        or ".." in parsed.parts
        or "." in parsed.parts
    ):
        raise ProducerSealError(f"{label} contains an unsafe path")
    return encoded


def _excluded_source(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return (
        "__pycache__" in parts
        or relative.endswith(".pyc")
        or relative.endswith(".pyo")
    )


def _walk_source(
    source_root: Path,
    directory: Path,
    snapshot: dict[str, bytes],
) -> None:
    try:
        entries = list(os.scandir(directory))
    except OSError as error:
        raise ProducerSealError("producer source directory cannot be read") from error
    keyed: list[tuple[bytes, os.DirEntry[str]]] = []
    for entry in entries:
        relative = (directory / entry.name).relative_to(source_root).as_posix()
        keyed.append((_safe_relative(relative, "producer source"), entry))
    for _encoded, entry in sorted(keyed, key=lambda item: item[0]):
        path = Path(entry.path)
        relative = path.relative_to(source_root).as_posix()
        try:
            result = entry.stat(follow_symlinks=False)
        except OSError as error:
            raise ProducerSealError("producer source entry is unavailable") from error
        if stat.S_ISLNK(result.st_mode):
            raise ProducerSealError("producer source must not contain a symlink")
        if stat.S_ISDIR(result.st_mode):
            _walk_source(source_root, path, snapshot)
            continue
        if not stat.S_ISREG(result.st_mode):
            raise ProducerSealError("producer source must contain only regular files")
        if relative == _PROVENANCE_PATH:
            raise ProducerSealError("generated provenance must not exist in source")
        if _excluded_source(relative):
            continue
        if relative in snapshot:
            raise ProducerSealError("producer source contains a duplicate path")
        snapshot[relative] = _read_regular_once(path, "producer source file")


def _source_snapshot(source_root: Path) -> dict[str, bytes]:
    _require_directory(source_root, "source root")
    package_root = source_root / "equipment_quality"
    _require_directory(package_root, "equipment_quality source directory")
    snapshot = {
        "pyproject.toml": _read_regular_once(
            source_root / "pyproject.toml", "pyproject.toml"
        )
    }
    _walk_source(source_root, package_root, snapshot)
    return snapshot


def _source_digest(snapshot: dict[str, bytes]) -> str:
    lines = [_SOURCE_HEADER]
    for relative, payload in sorted(
        snapshot.items(), key=lambda item: item[0].encode("utf-8")
    ):
        lines.append(
            relative.encode("utf-8")
            + b"=sha256:"
            + hashlib.sha256(payload).hexdigest().encode("ascii")
            + b"\n"
        )
    return _sha256_uri(b"".join(lines))


def _validate_pyproject(payload: bytes) -> None:
    try:
        value = tomllib.loads(payload.decode("utf-8", errors="strict"))
        project = value["project"]
    except (UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        raise ProducerSealError("pyproject.toml is invalid") from error
    if project.get("name") != "sfep-equipment-quality" or project.get("version") != "1.0.0":
        raise ProducerSealError("pyproject.toml producer name/version is invalid")


def _expected_lock_bytes(group: str) -> bytes:
    selected = [item for item in _THIRD_PARTY if item[5] == group]
    return "".join(
        f"{name}=={version} --hash=sha256:{digest}\n"
        for name, version, _filename, _tag, digest, _group in sorted(
            selected, key=lambda item: item[0].encode("utf-8")
        )
    ).encode("ascii")


def _authenticate_locks(source_root: Path) -> dict[str, bytes]:
    authenticated: dict[str, bytes] = {}
    for filename, group in _LOCK_FILES.items():
        payload = _read_regular_once(source_root / filename, filename)
        if payload != _expected_lock_bytes(group):
            raise ProducerSealError(f"{filename} bytes do not match the frozen lock")
        authenticated[filename] = payload
    return authenticated


def _reject_duplicate_json(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number: {value}")


def _strict_canonical_json(payload: bytes, label: str) -> object:
    if payload.startswith(b"\xef\xbb\xbf") or not payload.endswith(b"\n"):
        raise ProducerSealError(f"{label} is not canonical JSON")
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_json,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise ProducerSealError(f"{label} is not canonical JSON") from error
    if _canonical_json_bytes(value) != payload:
        raise ProducerSealError(f"{label} is not canonical JSON")
    return value


def _expected_wheelhouse_lock() -> dict[str, object]:
    wheels = [
        {"filename": filename, "sha256": f"sha256:{digest}", "tag": tag}
        for _name, _version, filename, tag, digest, _group in _THIRD_PARTY
    ]
    wheels.sort(key=lambda item: item["filename"].encode("utf-8"))
    return {
        "schemaVersion": "sfep-wheelhouse-lock/v1",
        "target": {
            "implementation": "CPython",
            "pythonVersion": "3.12.10",
            "system": "Darwin",
            "machine": "arm64",
        },
        "wheels": wheels,
    }


def _parse_wheel_filename(filename: str) -> tuple[str, str, str]:
    if not filename.endswith(".whl"):
        raise ProducerSealError("wheelhouse inventory contains a non-wheel or sdist")
    try:
        prefix, python_tag, abi_tag, platform_tag = filename[:-4].rsplit("-", 3)
        distribution, version = prefix.split("-", 1)
    except ValueError as error:
        raise ProducerSealError("wheelhouse wheel filename is malformed") from error
    normalized = _PEP503.sub("-", distribution).lower()
    if not normalized or not version:
        raise ProducerSealError("wheelhouse wheel name/version is malformed")
    return normalized, version, f"{python_tag}-{abi_tag}-{platform_tag}"


def _authenticate_wheelhouse(source_root: Path, wheelhouse: Path) -> None:
    lock_payload = _read_regular_once(
        source_root / "wheelhouse.lock.json", "wheelhouse lock"
    )
    lock = _strict_canonical_json(lock_payload, "wheelhouse lock")
    if lock != _expected_wheelhouse_lock():
        raise ProducerSealError("wheelhouse lock does not match the frozen inventory")

    _require_directory(wheelhouse, "wheelhouse")
    try:
        entries = {entry.name: entry for entry in os.scandir(wheelhouse)}
    except OSError as error:
        raise ProducerSealError("wheelhouse inventory cannot be read") from error
    expected_names = {item[2] for item in _THIRD_PARTY}
    actual_names = set(entries)
    if actual_names - {_PRODUCER_FILENAME} != expected_names:
        raise ProducerSealError("wheelhouse inventory is missing or has extra files")
    if actual_names > expected_names | {_PRODUCER_FILENAME}:
        raise ProducerSealError("wheelhouse inventory is missing or has extra files")
    expected_by_filename = {item[2]: item for item in _THIRD_PARTY}
    seen_normalized: set[str] = set()
    for filename in sorted(expected_names, key=lambda item: item.encode("utf-8")):
        entry = entries[filename]
        try:
            result = entry.stat(follow_symlinks=False)
        except OSError as error:
            raise ProducerSealError("wheelhouse wheel is unavailable") from error
        if stat.S_ISLNK(result.st_mode):
            raise ProducerSealError("wheelhouse wheel must not be a symlink")
        if not stat.S_ISREG(result.st_mode):
            raise ProducerSealError("wheelhouse wheel must be a regular file")
        name, version, tag = _parse_wheel_filename(filename)
        expected_name, expected_version, _filename, expected_tag, digest, _group = (
            expected_by_filename[filename]
        )
        if name != expected_name or version != expected_version or tag != expected_tag:
            raise ProducerSealError("wheelhouse wheel name/version/tag is incompatible")
        if name in seen_normalized:
            raise ProducerSealError("wheelhouse has duplicate normalized names")
        seen_normalized.add(name)
        payload = _read_regular_once(Path(entry.path), "wheelhouse wheel")
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ProducerSealError("wheelhouse wheel digest does not match the lock")


def _read_wheel_directory(
    directory: Path,
    label: str,
    expected_directory_identity: tuple[int, int],
) -> tuple[bytes, tuple[int, int]]:
    if _require_directory(directory, label) != expected_directory_identity:
        raise ProducerSealError(f"{label} changed while reading")
    try:
        entries = list(os.scandir(directory))
    except OSError as error:
        raise ProducerSealError(f"{label} cannot be inspected") from error
    if len(entries) != 1:
        raise ProducerSealError(f"{label} must contain exactly one wheel")
    entry = entries[0]
    if entry.name != _PRODUCER_FILENAME:
        raise ProducerSealError(f"{label} does not contain the expected producer wheel")
    try:
        result = entry.stat(follow_symlinks=False)
    except OSError as error:
        raise ProducerSealError(f"{label} wheel is unavailable") from error
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISREG(result.st_mode):
        raise ProducerSealError(f"{label} wheel must be a non-symlink regular file")
    wheel_identity = _identity(result)
    payload = _read_regular_once(Path(entry.path), f"{label} wheel")
    try:
        wheel_after = os.lstat(entry.path)
    except OSError as error:
        raise ProducerSealError(f"{label} wheel changed while reading") from error
    if (
        _identity(wheel_after) != wheel_identity
        or _require_directory(directory, label) != expected_directory_identity
    ):
        raise ProducerSealError(f"{label} changed while reading")
    return payload, wheel_identity


def _validate_email_field(
    payload: bytes,
    label: str,
    field: str,
    expected: str,
) -> None:
    try:
        message = BytesParser(policy=policy.compat32).parsebytes(payload)
    except Exception as error:
        raise ProducerSealError(f"producer {label} is malformed") from error
    if message.defects or message.get_all(field, []) != [expected]:
        raise ProducerSealError(f"producer {label} {field} is invalid")


def _validate_metadata(members: dict[str, bytes]) -> None:
    _validate_email_field(
        members[_METADATA_PATH], "METADATA", "Name", "sfep-equipment-quality"
    )
    _validate_email_field(members[_METADATA_PATH], "METADATA", "Version", "1.0.0")
    _validate_email_field(members[_WHEEL_PATH], "wheel metadata", "Wheel-Version", "1.0")
    _validate_email_field(members[_WHEEL_PATH], "wheel metadata", "Root-Is-Purelib", "true")
    _validate_email_field(members[_WHEEL_PATH], "wheel metadata", "Tag", "py3-none-any")
    if members[_METADATA_PATH] != _EXPECTED_METADATA:
        raise ProducerSealError("producer METADATA bytes are unexpected")
    if members[_WHEEL_PATH] != _EXPECTED_WHEEL:
        raise ProducerSealError("producer wheel metadata bytes are unexpected")
    if members[f"{_DIST_INFO}/entry_points.txt"] != _EXPECTED_ENTRY_POINTS:
        raise ProducerSealError("producer entry-point metadata bytes are unexpected")
    if members[f"{_DIST_INFO}/top_level.txt"] != _EXPECTED_TOP_LEVEL:
        raise ProducerSealError("producer top-level metadata bytes are unexpected")


def _validate_provenance(payload: bytes, expected: bytes) -> None:
    try:
        value = _strict_canonical_json(payload, "producer provenance")
    except ProducerSealError as error:
        raise ProducerSealError("producer provenance is not exact canonical JSON") from error
    if payload != expected or not isinstance(value, dict) or len(value) != 7:
        raise ProducerSealError("producer provenance does not match source and lock")


def _validate_record(members: dict[str, bytes]) -> None:
    payload = members[_RECORD_PATH]
    if payload.startswith(b"\xef\xbb\xbf") or not payload.endswith(b"\n"):
        raise ProducerSealError("producer RECORD is not canonical UTF-8 CSV")
    try:
        text = payload.decode("utf-8", errors="strict")
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (UnicodeError, csv.Error) as error:
        raise ProducerSealError("producer RECORD is malformed") from error
    records: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3:
            raise ProducerSealError("producer RECORD row must have three columns")
        path, digest, size = row
        _safe_relative(path, "producer RECORD")
        if path in records:
            raise ProducerSealError("producer RECORD contains a duplicate path")
        records[path] = (digest, size)
    rendered = io.StringIO(newline="")
    csv.writer(rendered, lineterminator="\n").writerows(rows)
    if rendered.getvalue().encode("utf-8") != payload:
        raise ProducerSealError("producer RECORD bytes are not canonical CSV")
    if [row[0] for row in rows] != list(members):
        raise ProducerSealError("producer RECORD row order does not match the archive")
    if set(records) != set(members):
        raise ProducerSealError("producer RECORD inventory does not match the archive")
    for path, member in members.items():
        digest, size = records[path]
        if path == _RECORD_PATH:
            if digest or size:
                raise ProducerSealError("producer RECORD self-row must be empty")
            continue
        match = _RECORD_HASH.fullmatch(digest)
        if match is None:
            raise ProducerSealError("producer RECORD hash is not canonical sha256")
        expected_hash = base64.urlsafe_b64encode(hashlib.sha256(member).digest()).rstrip(b"=")
        if match.group(1).encode("ascii") != expected_hash:
            raise ProducerSealError("producer RECORD hash mismatch")
        if _RECORD_SIZE.fullmatch(size) is None or int(size) != len(member):
            raise ProducerSealError("producer RECORD size mismatch")


def _validate_zip_end(payload: bytes) -> None:
    if len(payload) < 22 or payload[-22:-18] != b"PK\x05\x06" or payload[-2:] != b"\0\0":
        raise ProducerSealError("producer ZIP has trailing data or a noncanonical end record")


def _verify_producer_wheel(
    payload: bytes,
    snapshot: dict[str, bytes],
    expected_provenance: bytes,
    forbidden_roots: tuple[Path, ...],
) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if archive.comment:
                raise ProducerSealError("producer ZIP archive comment is forbidden")
            infos = archive.infolist()
            if not infos:
                raise ProducerSealError("producer ZIP archive is empty")
            if infos[0].header_offset != 0:
                raise ProducerSealError("producer ZIP has prepended data")
            names: set[str] = set()
            members: dict[str, bytes] = {}
            for info in infos:
                name = info.filename
                if "\x00" in info.orig_filename or info.orig_filename != name:
                    raise ProducerSealError("producer ZIP contains an unsafe path")
                if info.is_dir() or name.endswith("/"):
                    raise ProducerSealError("producer ZIP directory members are forbidden")
                _safe_relative(name, "producer ZIP")
                if name in names:
                    raise ProducerSealError("producer ZIP contains a duplicate member")
                names.add(name)
                mode = info.external_attr >> 16
                expected_mode = 0o664 if name == _RECORD_PATH else 0o644
                if (
                    info.create_system != 3
                    or stat.S_IFMT(mode) != stat.S_IFREG
                    or stat.S_IMODE(mode) != expected_mode
                ):
                    raise ProducerSealError("producer ZIP member mode is invalid")
                if info.date_time != _ZIP_EPOCH:
                    raise ProducerSealError("producer ZIP member timestamp is invalid")
                if (
                    info.compress_type != zipfile.ZIP_DEFLATED
                    or info.flag_bits != 0
                    or info.extra
                    or info.comment
                ):
                    raise ProducerSealError("producer ZIP member metadata is invalid")
                try:
                    member = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                    raise ProducerSealError("producer ZIP member cannot be authenticated") from error
                if len(member) != info.file_size:
                    raise ProducerSealError("producer ZIP member size is invalid")
                members[name] = member
    except ProducerSealError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ProducerSealError("producer ZIP archive is malformed") from error
    _validate_zip_end(payload)

    if _PROVENANCE_PATH not in members:
        raise ProducerSealError("producer provenance is missing from the wheel")
    expected_package = {
        relative for relative in snapshot if relative.startswith("equipment_quality/")
    }
    expected_members = expected_package | {_PROVENANCE_PATH} | _GENERATED_MEMBERS
    if set(members) != expected_members:
        raise ProducerSealError("producer wheel source inventory is unexpected")
    root_package = sorted(
        (
            member
            for member in expected_package | {_PROVENANCE_PATH}
            if len(PurePosixPath(member).parts) == 2
        ),
        key=lambda member: member.encode("utf-8"),
    )
    package_data = sorted(
        (
            member
            for member in expected_package
            if len(PurePosixPath(member).parts) > 2
        ),
        key=lambda member: member.encode("utf-8"),
    )
    expected_order = [
        *root_package,
        *package_data,
        _METADATA_PATH,
        _WHEEL_PATH,
        f"{_DIST_INFO}/entry_points.txt",
        f"{_DIST_INFO}/top_level.txt",
        _RECORD_PATH,
    ]
    if list(members) != expected_order:
        raise ProducerSealError("producer wheel member order is unexpected")
    for relative in expected_package:
        if members[relative] != snapshot[relative]:
            raise ProducerSealError("producer wheel source file bytes do not match source")
    _validate_metadata(members)
    _validate_provenance(members[_PROVENANCE_PATH], expected_provenance)
    forbidden = {
        os.fspath(root).encode("utf-8")
        for root in forbidden_roots
        if os.fspath(root)
    }
    for member in members.values():
        if any(token in member for token in forbidden):
            raise ProducerSealError("producer wheel contains an absolute build path")
    _validate_record(members)


def _producer_lock_bytes(wheel: bytes) -> bytes:
    return (
        "sfep-equipment-quality==1.0.0 "
        f"--hash=sha256:{hashlib.sha256(wheel).hexdigest()}\n"
    ).encode("ascii")


def _path_exists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ProducerSealError("existing producer output cannot be inspected") from error
    return True


def _preflight_outputs(
    target_wheel: Path,
    producer_lock: Path,
    wheel: bytes,
    lock: bytes,
) -> bool:
    wheel_exists = _path_exists(target_wheel)
    lock_exists = _path_exists(producer_lock)
    if not wheel_exists and not lock_exists:
        return False
    if wheel_exists and lock_exists:
        existing_wheel = _read_regular_once(target_wheel, "existing producer wheel")
        existing_lock = _read_regular_once(producer_lock, "existing producer lock")
        if existing_wheel == wheel and existing_lock == lock:
            return True
    raise ProducerSealError("existing producer wheel/lock is incomplete or different")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_temp(parent: Path, payload: bytes) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=".sfep-producer-", dir=parent)
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o644)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ProducerSealError("producer output could not be written")
            view = view[written:]
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    return path


def _publish_no_clobber(
    target_wheel: Path,
    producer_lock: Path,
    wheel: bytes,
    lock: bytes,
) -> None:
    wheel_temp = _durable_temp(target_wheel.parent, wheel)
    lock_temp = _durable_temp(producer_lock.parent, lock)
    wheel_created = False
    lock_created = False
    try:
        try:
            os.link(wheel_temp, target_wheel, follow_symlinks=False)
        except FileExistsError as error:
            raise ProducerSealError("existing producer wheel appeared during publication") from error
        wheel_created = True
        _fsync_directory(target_wheel.parent)
        try:
            os.link(lock_temp, producer_lock, follow_symlinks=False)
        except FileExistsError as error:
            raise ProducerSealError("existing producer lock appeared during publication") from error
        lock_created = True
        _fsync_directory(producer_lock.parent)
    except Exception:
        if lock_created:
            try:
                producer_lock.unlink()
                _fsync_directory(producer_lock.parent)
            except OSError:
                pass
        if wheel_created:
            try:
                target_wheel.unlink()
                _fsync_directory(target_wheel.parent)
            except OSError:
                pass
        raise
    finally:
        wheel_temp.unlink(missing_ok=True)
        lock_temp.unlink(missing_ok=True)


def _expected_provenance(snapshot: dict[str, bytes], requirements: bytes) -> bytes:
    return _canonical_json_bytes({
        "schemaVersion": "sfep-producer-provenance/v1",
        "producerName": "equipment-quality",
        "distributionName": "sfep-equipment-quality",
        "version": "1.0.0",
        "sourceSha256": _source_digest(snapshot),
        "sourceDateEpoch": _SOURCE_DATE_EPOCH,
        "requirementsLockSha256": _sha256_uri(requirements),
    })


def _run(
    source_root: Path,
    wheel_dir_a: Path,
    wheel_dir_b: Path,
    wheelhouse: Path,
    producer_lock: Path,
) -> tuple[str, int, bool]:
    _require_directory(source_root, "source root")
    wheel_dir_a_identity = _require_directory(wheel_dir_a, "wheel directory A")
    wheel_dir_b_identity = _require_directory(wheel_dir_b, "wheel directory B")
    if wheel_dir_a_identity == wheel_dir_b_identity:
        raise ProducerSealError("producer wheel directories must be physically independent")
    _require_directory(wheelhouse, "wheelhouse")
    _require_directory(producer_lock.parent, "producer lock parent")
    snapshot = _source_snapshot(source_root)
    _validate_pyproject(snapshot["pyproject.toml"])
    locks = _authenticate_locks(source_root)
    _authenticate_wheelhouse(source_root, wheelhouse)
    wheel_a, wheel_a_identity = _read_wheel_directory(
        wheel_dir_a,
        "wheel directory A",
        wheel_dir_a_identity,
    )
    wheel_b, wheel_b_identity = _read_wheel_directory(
        wheel_dir_b,
        "wheel directory B",
        wheel_dir_b_identity,
    )
    if wheel_a_identity == wheel_b_identity:
        raise ProducerSealError("producer wheel files must be physically independent")
    if wheel_a != wheel_b:
        raise ProducerSealError("independent producer wheel archive bytes differ")
    provenance = _expected_provenance(snapshot, locks["requirements.lock"])
    _verify_producer_wheel(
        wheel_a,
        snapshot,
        provenance,
        (source_root, wheel_dir_a, wheel_dir_b, wheelhouse, producer_lock.parent),
    )
    lock = _producer_lock_bytes(wheel_a)
    target_wheel = wheelhouse / _PRODUCER_FILENAME
    reused = _preflight_outputs(target_wheel, producer_lock, wheel_a, lock)
    if not reused:
        _publish_no_clobber(target_wheel, producer_lock, wheel_a, lock)
    return _sha256_uri(wheel_a), len(wheel_a), reused


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        source_root = _checked_absolute(arguments.source_root, "source root")
        wheel_dir_a = _checked_absolute(arguments.wheel_dir_a, "wheel directory A")
        wheel_dir_b = _checked_absolute(arguments.wheel_dir_b, "wheel directory B")
        wheelhouse = _checked_absolute(arguments.wheelhouse, "wheelhouse")
        producer_lock = _checked_absolute(arguments.producer_lock, "producer lock")
        wheel_digest, wheel_size, reused = _run(
            source_root,
            wheel_dir_a,
            wheel_dir_b,
            wheelhouse,
            producer_lock,
        )
    except (ProducerSealError, OSError, ValueError) as error:
        print(f"producer seal failed: {error}", file=sys.stderr)
        return 2
    print(f"wheelSha256={wheel_digest}")
    print(f"wheelSize={wheel_size}")
    print(f"publication={'reused' if reused else 'created'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
