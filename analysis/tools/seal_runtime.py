#!/usr/bin/env python3
"""Authenticate one installed SFEP runtime and publish its canonical manifest."""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable, Mapping
import csv
from email import policy
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import stat
import subprocess
import sys
import tempfile
import tomllib
import unicodedata
import zipfile


_SOURCE_HEADER = b"sfep-source-lines/v1\n"
_SOURCE_DATE_EPOCH = "1735689600"
_PROVENANCE_PATH = "equipment_quality/sfep_producer_provenance.json"
_PRODUCER_FILENAME = "sfep_equipment_quality-1.1.0-py3-none-any.whl"
_PRODUCER_DISTRIBUTION = "sfep-equipment-quality"
_PRODUCER_VERSION = "1.1.0"
_PRODUCER_SHA256 = "abdfb2738c4eea6dd9e8c832951deef5faff4795ffbae39c6a9617cac293d514"
_PIP_VERSION = "25.1.1"
_PACKAGE_NAMES = (
    "attrs", "jsonschema", "jsonschema-specifications", "numpy", "pandas",
    "python-dateutil", "pytz", "referencing", "rpds-py", "six",
    "typing-extensions", "tzdata",
)
_DIRECT_PACKAGES = frozenset({"jsonschema", "numpy", "pandas"})
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
    _PRODUCER_DISTRIBUTION: {
        "sfep-equipment-quality": "equipment_quality.cli:main"
    },
}
_EXPECTED_DISTRIBUTIONS = frozenset((*_PACKAGE_NAMES, "pip", _PRODUCER_DISTRIBUTION))
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
_SPEC_BY_NAME = {item[0]: item for item in _THIRD_PARTY}
_RECORD_HASH = re.compile(r"^sha256=([A-Za-z0-9_-]{43})$")
_RECORD_SIZE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_PEP503 = re.compile(r"[-_.]+")
_DIST_INFO_EXCLUSIONS = frozenset({"RECORD", "INSTALLER", "direct_url.json", "REQUESTED"})
_PROVENANCE_FIELDS = frozenset({
    "schemaVersion", "producerName", "distributionName", "version",
    "sourceSha256", "sourceDateEpoch", "requirementsLockSha256",
})
_HASH_PROBES = (-4218979432691865272, 1379760580859628941)
_MAX_SMALL_FILE = 4 * 1024 * 1024
_MAX_WHEEL = 256 * 1024 * 1024
_MAX_RECORD = 16 * 1024 * 1024
_MAX_PROBE = 1024 * 1024


class RuntimeSealError(RuntimeError):
    """The installed runtime cannot be authenticated or safely sealed."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--producer-wheel-dir", required=True)
    parser.add_argument("--producer-lock", required=True)
    parser.add_argument("--runtime-venv", required=True)
    parser.add_argument("--output", required=True)
    return parser


def _sha256_uri(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON value: {value}")


def _strict_json(payload: bytes, label: str, *, max_size: int = _MAX_SMALL_FILE) -> object:
    if not payload or len(payload) > max_size or not payload.endswith(b"\n"):
        raise RuntimeSealError(f"{label} has an invalid size or terminator")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise RuntimeSealError(f"{label} contains a UTF-8 BOM")
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_members, parse_constant=_reject_constant)
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise RuntimeSealError(f"{label} is not strict JSON") from error
    if _canonical_json_bytes(value) != payload:
        raise RuntimeSealError(f"{label} is not canonical JSON")
    return value


def _normalise_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeSealError("distribution name is invalid")
    normalised = _PEP503.sub("-", str(value)).lower()
    if not normalised:
        raise RuntimeSealError("distribution name is invalid")
    return normalised


def _path_alias_key(path: Path) -> str:
    return unicodedata.normalize("NFC", os.path.abspath(os.fspath(path))).casefold()


def _path_is_within(path: Path, root: Path) -> bool:
    candidate = _path_alias_key(path)
    boundary = _path_alias_key(root).rstrip(os.sep)
    return candidate == boundary or candidate.startswith(boundary + os.sep)


def _checked_absolute(path: Path, label: str) -> Path:
    try:
        raw = os.fspath(path)
    except (TypeError, ValueError) as error:
        raise RuntimeSealError(f"{label} path is invalid") from error
    if not raw or not os.path.isabs(raw) or ".." in Path(raw).parts or any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise RuntimeSealError(f"{label} path must be absolute and canonical")
    return Path(raw)


def _reject_intermediate_symlinks(
    path: Path,
    label: str,
    *,
    allow_missing_final: bool = False,
    allow_final_symlink: bool = False,
) -> None:
    """Reject lexical paths that acquire meaning through a symlinked component."""
    if not path.is_absolute():
        raise RuntimeSealError(f"{label} path must be absolute")
    current = Path(path.anchor)
    parts = path.parts[1:]
    for index, part in enumerate(parts):
        current /= part
        final = index == len(parts) - 1
        try:
            result = os.lstat(current)
        except FileNotFoundError:
            if allow_missing_final and final:
                return
            raise RuntimeSealError(f"{label} path component is unavailable") from None
        except OSError as error:
            raise RuntimeSealError(f"{label} path component is unavailable") from error
        if stat.S_ISLNK(result.st_mode) and not (allow_final_symlink and final):
            raise RuntimeSealError(f"{label} path contains an intermediate symlink")


def _fingerprint(result: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (result.st_dev, result.st_ino, stat.S_IFMT(result.st_mode), result.st_size, result.st_mtime_ns, result.st_ctime_ns)


def _require_directory(path: Path, label: str) -> tuple[int, int]:
    try:
        result = os.lstat(path)
    except OSError as error:
        raise RuntimeSealError(f"{label} is unavailable") from error
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISDIR(result.st_mode):
        raise RuntimeSealError(f"{label} must be a non-symlink directory")
    return result.st_dev, result.st_ino


def _read_regular(path: Path, label: str, maximum: int) -> bytes:
    try:
        before_name = os.lstat(path)
    except OSError as error:
        raise RuntimeSealError(f"{label} is unavailable") from error
    if stat.S_ISLNK(before_name.st_mode) or not stat.S_ISREG(before_name.st_mode) or before_name.st_size > maximum:
        raise RuntimeSealError(f"{label} must be a bounded non-symlink regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeSealError(f"{label} cannot be opened safely") from error
    chunks: list[bytes] = []
    total = 0
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > maximum:
            raise RuntimeSealError(f"{label} is not a bounded regular file")
        while True:
            chunk = os.read(descriptor, min(65536, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise RuntimeSealError(f"{label} exceeds its size limit")
        after = os.fstat(descriptor)
    except OSError as error:
        raise RuntimeSealError(f"{label} changed while being read") from error
    finally:
        os.close(descriptor)
    try:
        after_name = os.lstat(path)
    except OSError as error:
        raise RuntimeSealError(f"{label} changed while being read") from error
    if not (_fingerprint(before_name) == _fingerprint(opened) == _fingerprint(after) == _fingerprint(after_name)) or total != after.st_size:
        raise RuntimeSealError(f"{label} changed while being read")
    return b"".join(chunks)


def _hash_executable(path: Path) -> str:
    try:
        real = Path(os.path.realpath(path, strict=True))
    except (OSError, TypeError, ValueError) as error:
        raise RuntimeSealError("runtime interpreter cannot be resolved") from error
    return _sha256_uri(_read_regular(real, "runtime interpreter", _MAX_WHEEL))


def _source_snapshot(source_root: Path) -> dict[str, bytes]:
    _require_directory(source_root, "source root")
    snapshot = {"pyproject.toml": _read_regular(source_root / "pyproject.toml", "pyproject.toml", _MAX_SMALL_FILE)}
    package_root = source_root / "equipment_quality"
    _require_directory(package_root, "producer source package")
    for current, directories, files in os.walk(package_root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.sort(key=lambda item: item.encode("utf-8"))
        files.sort(key=lambda item: item.encode("utf-8"))
        retained: list[str] = []
        for name in directories:
            result = os.lstat(current_path / name)
            if stat.S_ISLNK(result.st_mode) or not stat.S_ISDIR(result.st_mode):
                raise RuntimeSealError("producer source contains an unsafe directory")
            if name != "__pycache__":
                retained.append(name)
        directories[:] = retained
        for name in files:
            path = current_path / name
            relative = path.relative_to(source_root).as_posix()
            if relative == _PROVENANCE_PATH:
                raise RuntimeSealError("generated provenance must not exist in source")
            if name.endswith((".pyc", ".pyo")):
                continue
            snapshot[relative] = _read_regular(path, f"source file {relative}", _MAX_SMALL_FILE)
    return snapshot


def _source_digest(snapshot: dict[str, bytes]) -> str:
    lines = [_SOURCE_HEADER]
    for relative, payload in sorted(snapshot.items(), key=lambda item: item[0].encode("utf-8")):
        lines.append(relative.encode("utf-8") + b"=sha256:" + hashlib.sha256(payload).hexdigest().encode("ascii") + b"\n")
    return _sha256_uri(b"".join(lines))


def _validate_pyproject(payload: bytes) -> None:
    try:
        value = tomllib.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        raise RuntimeSealError("pyproject.toml is malformed") from error
    project = value.get("project")
    build = value.get("build-system")
    if not isinstance(project, dict) or not isinstance(build, dict) or project.get("name") != _PRODUCER_DISTRIBUTION or project.get("version") != _PRODUCER_VERSION or project.get("requires-python") != "==3.12.*" or project.get("dependencies") != ["numpy==2.2.6", "pandas==2.3.0", "jsonschema==4.24.0"] or build.get("requires") != ["setuptools==80.9.0", "wheel==0.45.1"] or build.get("build-backend") != "setuptools.build_meta":
        raise RuntimeSealError("pyproject.toml does not match the producer contract")


def _expected_requirement_lock(group: str) -> bytes:
    selected = [item for item in _THIRD_PARTY if item[5] == group]
    return "".join(f"{name}=={version} --hash=sha256:{digest}\n" for name, version, _filename, _tag, digest, _group in sorted(selected, key=lambda item: item[0].encode("utf-8"))).encode("ascii")


def _expected_wheelhouse_lock() -> bytes:
    wheels = [{"filename": filename, "sha256": f"sha256:{digest}", "tag": tag} for _name, _version, filename, tag, digest, _group in _THIRD_PARTY]
    wheels.sort(key=lambda item: item["filename"].encode("utf-8"))
    return _canonical_json_bytes({"schemaVersion": "sfep-wheelhouse-lock/v1", "target": {"implementation": "CPython", "pythonVersion": "3.12.10", "system": "Darwin", "machine": "arm64"}, "wheels": wheels})


def _authenticate_locks(source_root: Path, producer_lock: Path) -> tuple[dict[str, bytes], bytes]:
    expected = {"bootstrap.lock": _expected_requirement_lock("bootstrap"), "build-requirements.lock": _expected_requirement_lock("build"), "requirements.lock": _expected_requirement_lock("runtime"), "wheelhouse.lock.json": _expected_wheelhouse_lock()}
    locks: dict[str, bytes] = {}
    for filename, expected_payload in expected.items():
        payload = _read_regular(source_root / filename, filename, _MAX_SMALL_FILE)
        if payload != expected_payload:
            raise RuntimeSealError(f"{filename} does not match its frozen contract")
        locks[filename] = payload
    producer_payload = _read_regular(producer_lock, "producer.lock", _MAX_SMALL_FILE)
    expected_producer = f"{_PRODUCER_DISTRIBUTION}=={_PRODUCER_VERSION} --hash=sha256:{_PRODUCER_SHA256}\n".encode("ascii")
    if producer_payload != expected_producer:
        raise RuntimeSealError("producer.lock does not match the frozen producer wheel")
    return locks, producer_payload


def _checked_archive_path(raw: str) -> PurePosixPath:
    if not raw or "\\" in raw or raw.startswith("/") or raw.endswith("/") or any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise RuntimeSealError("wheel contains an unsafe member path")
    path = PurePosixPath(raw)
    if path.is_absolute() or path.as_posix() != raw or ".." in path.parts:
        raise RuntimeSealError("wheel contains an unsafe member path")
    return path


def _parse_record(payload: bytes, label: str) -> list[list[str]]:
    if not payload or len(payload) > _MAX_RECORD:
        raise RuntimeSealError(f"{label} RECORD has an invalid size")
    try:
        rows = list(csv.reader(io.StringIO(payload.decode("utf-8", errors="strict"), newline=""), strict=True))
    except (UnicodeError, csv.Error) as error:
        raise RuntimeSealError(f"{label} RECORD is malformed") from error
    if not rows or any(len(row) != 3 for row in rows):
        raise RuntimeSealError(f"{label} RECORD rows are malformed")
    return rows


def _metadata_identity(payload: bytes, label: str) -> tuple[str, str]:
    try:
        message = BytesParser(policy=policy.strict).parsebytes(payload)
    except Exception as error:
        raise RuntimeSealError(f"{label} METADATA is malformed") from error
    names = message.get_all("Name", [])
    versions = message.get_all("Version", [])
    if message.defects or len(names) != 1 or len(versions) != 1 or not versions[0]:
        raise RuntimeSealError(f"{label} METADATA identity is ambiguous")
    return _normalise_name(names[0]), str(versions[0])


def _tree_excluded(path: PurePosixPath, generated_script: bool = False) -> bool:
    if generated_script or "__pycache__" in path.parts or path.name.endswith(".pyc"):
        return True
    return len(path.parts) >= 2 and path.parts[-2].endswith(".dist-info") and path.name in _DIST_INFO_EXCLUSIONS


def _wheel_identity(wheel_path: Path, expected_name: str, expected_version: str, expected_tag: str, expected_digest: str) -> dict[str, object]:
    payload = _read_regular(wheel_path, f"wheel {wheel_path.name}", _MAX_WHEEL)
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise RuntimeSealError(f"wheel {wheel_path.name} digest does not match its lock")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (OSError, zipfile.BadZipFile) as error:
        raise RuntimeSealError(f"wheel {wheel_path.name} is malformed") from error
    with archive:
        members: dict[str, bytes] = {}
        for info in archive.infolist():
            if info.is_dir():
                continue
            raw = info.orig_filename
            _checked_archive_path(raw)
            if raw in members:
                raise RuntimeSealError(f"wheel {wheel_path.name} contains a duplicate member")
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                raise RuntimeSealError(f"wheel {wheel_path.name} contains a non-regular member")
            members[raw] = archive.read(info)
    metadata_paths = [name for name in members if len(PurePosixPath(name).parts) == 2 and name.endswith(".dist-info/METADATA")]
    wheel_paths = [name for name in members if len(PurePosixPath(name).parts) == 2 and name.endswith(".dist-info/WHEEL")]
    record_paths = [name for name in members if len(PurePosixPath(name).parts) == 2 and name.endswith(".dist-info/RECORD")]
    if len(metadata_paths) != 1 or len(wheel_paths) != 1 or len(record_paths) != 1:
        raise RuntimeSealError(f"wheel {wheel_path.name} metadata inventory is not exact")
    name, version = _metadata_identity(members[metadata_paths[0]], wheel_path.name)
    if name != expected_name or version != expected_version:
        raise RuntimeSealError(f"wheel {wheel_path.name} identity does not match its filename")
    try:
        wheel_metadata = BytesParser(policy=policy.strict).parsebytes(members[wheel_paths[0]])
    except Exception as error:
        raise RuntimeSealError(f"wheel {wheel_path.name} WHEEL metadata is malformed") from error
    interpreter_tag, abi_tag, platform_tag = expected_tag.split("-", 2)
    expanded_tags = [
        f"{interpreter}-{abi_tag}-{platform_tag}"
        for interpreter in interpreter_tag.split(".")
    ]
    observed_tags = [str(item) for item in wheel_metadata.get_all("Tag", [])]
    if wheel_metadata.defects or observed_tags != expanded_tags:
        raise RuntimeSealError(f"wheel {wheel_path.name} tag is not exact")
    rows = _parse_record(members[record_paths[0]], wheel_path.name)
    seen: set[str] = set()
    self_count = 0
    lines: list[tuple[bytes, bytes]] = []
    for raw_path, hash_field, size_field in rows:
        if raw_path in seen:
            raise RuntimeSealError(f"wheel {wheel_path.name} RECORD has a duplicate path")
        seen.add(raw_path)
        path = _checked_archive_path(raw_path)
        if raw_path == record_paths[0]:
            self_count += 1
            if hash_field or size_field:
                raise RuntimeSealError(f"wheel {wheel_path.name} RECORD self-row is hashed")
            continue
        member = members.get(raw_path)
        match = _RECORD_HASH.fullmatch(hash_field)
        if member is None or match is None or _RECORD_SIZE.fullmatch(size_field) is None:
            raise RuntimeSealError(f"wheel {wheel_path.name} RECORD is incomplete")
        digest = base64.urlsafe_b64encode(hashlib.sha256(member).digest()).rstrip(b"=").decode("ascii")
        if digest != match.group(1) or len(member) != int(size_field):
            raise RuntimeSealError(f"wheel {wheel_path.name} RECORD does not authenticate its payload")
        if not _tree_excluded(path):
            encoded = raw_path.encode("utf-8")
            lines.append((encoded, encoded + b"=sha256:" + hashlib.sha256(member).hexdigest().encode("ascii") + b"\n"))
    if self_count != 1 or seen != set(members):
        raise RuntimeSealError(f"wheel {wheel_path.name} RECORD inventory is not exact")
    tree = _sha256_uri(b"".join(line for _path, line in sorted(lines, key=lambda item: item[0])))
    return {"name": name, "version": version, "filename": wheel_path.name, "tag": expected_tag, "sha256": f"sha256:{expected_digest}", "tree": tree, "provenance": members.get(_PROVENANCE_PATH)}


def _authenticate_wheelhouse(wheel_dir: Path) -> dict[str, dict[str, object]]:
    _require_directory(wheel_dir, "producer wheel directory")
    expected_names = {item[2] for item in _THIRD_PARTY} | {_PRODUCER_FILENAME}
    entries = list(os.scandir(wheel_dir))
    if {entry.name for entry in entries} != expected_names or len(entries) != len(expected_names):
        raise RuntimeSealError("producer wheel directory inventory is not exact")
    wheels: dict[str, dict[str, object]] = {}
    alias_names: set[str] = set()
    identities: set[tuple[int, int]] = set()
    for name, version, filename, tag, digest, _group in _THIRD_PARTY:
        alias = unicodedata.normalize("NFC", filename).casefold()
        if alias in alias_names:
            raise RuntimeSealError("wheel filenames are ambiguous")
        alias_names.add(alias)
        result = os.lstat(wheel_dir / filename)
        identity = (result.st_dev, result.st_ino)
        if identity in identities:
            raise RuntimeSealError("wheel files alias one physical file")
        identities.add(identity)
        wheels[name] = _wheel_identity(wheel_dir / filename, name, version, tag, digest)
    producer_result = os.lstat(wheel_dir / _PRODUCER_FILENAME)
    if (producer_result.st_dev, producer_result.st_ino) in identities:
        raise RuntimeSealError("producer wheel aliases a third-party wheel")
    wheels[_PRODUCER_DISTRIBUTION] = _wheel_identity(wheel_dir / _PRODUCER_FILENAME, _PRODUCER_DISTRIBUTION, _PRODUCER_VERSION, "py3-none-any", _PRODUCER_SHA256)
    return wheels


def _runtime_layout(runtime_venv: Path) -> tuple[Path, Path, Path]:
    _reject_intermediate_symlinks(runtime_venv, "runtime venv")
    _require_directory(runtime_venv, "runtime venv")
    config_path = runtime_venv / "pyvenv.cfg"
    _reject_intermediate_symlinks(config_path, "runtime pyvenv.cfg")
    payload = _read_regular(config_path, "runtime pyvenv.cfg", _MAX_SMALL_FILE)
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise RuntimeSealError("runtime pyvenv.cfg is not UTF-8") from error
    if not text.endswith("\n") or "\r" in text:
        raise RuntimeSealError("runtime pyvenv.cfg is not canonically terminated")
    values: dict[str, str] = {}
    for line in text.splitlines():
        if line.count(" = ") != 1:
            raise RuntimeSealError("runtime pyvenv.cfg is malformed")
        key, value = line.split(" = ", 1)
        if (
            not re.fullmatch(r"[a-z][a-z-]*", key)
            or not value
            or key in values
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise RuntimeSealError("runtime pyvenv.cfg is malformed")
        values[key] = value
    if frozenset(values) != frozenset(
        {"home", "include-system-site-packages", "version", "executable", "command"}
    ):
        raise RuntimeSealError("runtime pyvenv.cfg members are not exact")
    if values["include-system-site-packages"] != "false" or values["version"] != "3.12.10":
        raise RuntimeSealError("runtime pyvenv.cfg does not describe the pinned runtime")
    configured_home = _checked_absolute(Path(values["home"]), "runtime pyvenv home")
    configured_executable = _checked_absolute(
        Path(values["executable"]), "runtime pyvenv executable"
    )
    _reject_intermediate_symlinks(configured_home, "runtime pyvenv home")
    _require_directory(configured_home, "runtime pyvenv home")
    _reject_intermediate_symlinks(
        configured_executable,
        "runtime pyvenv executable",
        allow_final_symlink=True,
    )
    executable = runtime_venv / "bin/python"
    _reject_intermediate_symlinks(
        executable, "runtime interpreter", allow_final_symlink=True
    )
    try:
        executable_result = os.stat(executable, follow_symlinks=True)
        configured_result = os.stat(configured_executable, follow_symlinks=True)
    except OSError as error:
        raise RuntimeSealError("runtime interpreter is unavailable") from error
    if (
        not stat.S_ISREG(executable_result.st_mode)
        or not stat.S_ISREG(configured_result.st_mode)
        or (executable_result.st_dev, executable_result.st_ino)
        != (configured_result.st_dev, configured_result.st_ino)
        or _path_alias_key(configured_home) != _path_alias_key(configured_executable.parent)
    ):
        raise RuntimeSealError("runtime interpreter does not match pyvenv.cfg")
    purelib = runtime_venv / "lib/python3.12/site-packages"
    platlib = purelib
    for site_path in (purelib, platlib):
        _reject_intermediate_symlinks(site_path, "runtime site-packages")
        _require_directory(site_path, "runtime site-packages")
    return executable, purelib, platlib


_PROBE_CODE = r'''import json, os, platform, subprocess, sys, sysconfig
runtime_root, purelib, platlib = sys.argv[1:4]
hash_code = 'import json; print(json.dumps([hash("sfep-runtime-hash-seed/v1"),hash("equipment-quality/3.12.10")],separators=(",",":")))'
hash_env = {"PYTHONHASHSEED":"0","TZ":"Asia/Seoul","LC_ALL":"C","LANG":"C","PATH":"/usr/bin:/bin"}
hashed = subprocess.run([sys.executable,"-P","-S","-c",hash_code],cwd="/private/tmp",env=hash_env,check=True,capture_output=True,text=True,timeout=10)
build_number, build_date = platform.python_build()
value = {"schemaVersion":"sfep-runtime-probe/v1","prefix":runtime_root,"executable":sys.executable,"purelib":purelib,"platlib":platlib,"flags":{"isolated":sys.flags.isolated,"noSite":sys.flags.no_site,"ignoreEnvironment":sys.flags.ignore_environment,"safePath":sys.flags.safe_path},"platform":{"system":platform.system(),"machine":platform.machine(),"macosProductVersion":platform.mac_ver()[0],"sysconfigPlatform":sysconfig.get_platform()},"python":{"implementation":platform.python_implementation(),"version":platform.python_version(),"build":build_number+" "+build_date,"cacheTag":sys.implementation.cache_tag,"soabi":sysconfig.get_config_var("SOABI")},"environment":{"pythonHashSeed":os.environ.get("PYTHONHASHSEED"),"timezone":os.environ.get("TZ")},"hashProbes":json.loads(hashed.stdout)}
print(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")))'''


def _limit_probe_child() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (_MAX_PROBE, _MAX_PROBE))


def _probe_runtime(runtime_venv: Path, executable: Path) -> dict[str, object]:
    expected_executable, purelib, platlib = _runtime_layout(runtime_venv)
    if _path_alias_key(expected_executable) != _path_alias_key(executable):
        raise RuntimeSealError("runtime interpreter path is not canonical")
    try:
        before = _fingerprint(os.stat(executable, follow_symlinks=True))
    except OSError as error:
        raise RuntimeSealError("runtime interpreter is unavailable") from error
    environment = {"PYTHONHASHSEED": "0", "TZ": "Asia/Seoul", "LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"}
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
        try:
            result = subprocess.run(
                [
                    os.fspath(executable),
                    "-I",
                    "-S",
                    "-c",
                    _PROBE_CODE,
                    os.fspath(runtime_venv),
                    os.fspath(purelib),
                    os.fspath(platlib),
                ],
                cwd="/private/tmp",
                env=environment,
                check=False,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=20,
                preexec_fn=_limit_probe_child,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeSealError("runtime interpreter probe failed") from error
        stdout_size = os.fstat(stdout_file.fileno()).st_size
        stderr_size = os.fstat(stderr_file.fileno()).st_size
        if stdout_size > _MAX_PROBE or stderr_size > _MAX_PROBE:
            raise RuntimeSealError("runtime interpreter probe exceeded its output limit")
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(_MAX_PROBE + 1)
        stderr = stderr_file.read(_MAX_PROBE + 1)
    try:
        after = _fingerprint(os.stat(executable, follow_symlinks=True))
    except OSError as error:
        raise RuntimeSealError("runtime interpreter changed during its probe") from error
    if before != after:
        raise RuntimeSealError("runtime interpreter changed during its probe")
    if result.returncode != 0 or stderr or len(stdout) > _MAX_PROBE:
        raise RuntimeSealError("runtime interpreter probe did not return bounded clean output")
    payload = bytes(stdout)
    if not payload.endswith(b"\n"):
        raise RuntimeSealError("runtime interpreter probe output is not terminated")
    value = _strict_json(payload, "runtime probe", max_size=_MAX_PROBE)
    if type(value) is not dict:
        raise RuntimeSealError("runtime probe root must be an object")
    return value


def _validate_probe(probe: dict[str, object], runtime_venv: Path, executable: Path) -> tuple[Path, Path]:
    expected_members = frozenset({"schemaVersion", "prefix", "executable", "purelib", "platlib", "flags", "platform", "python", "environment", "hashProbes"})
    if frozenset(probe) != expected_members or probe.get("schemaVersion") != "sfep-runtime-probe/v1":
        raise RuntimeSealError("runtime probe members are not exact")
    platform_value = probe.get("platform")
    python_value = probe.get("python")
    environment = probe.get("environment")
    flags = probe.get("flags")
    if type(platform_value) is not dict or type(python_value) is not dict or type(environment) is not dict or type(flags) is not dict:
        raise RuntimeSealError("runtime probe objects are malformed")
    if frozenset(platform_value) != frozenset({"system", "machine", "macosProductVersion", "sysconfigPlatform"}) or frozenset(python_value) != frozenset({"implementation", "version", "build", "cacheTag", "soabi"}) or frozenset(environment) != frozenset({"pythonHashSeed", "timezone"}) or frozenset(flags) != frozenset({"isolated", "noSite", "ignoreEnvironment", "safePath"}):
        raise RuntimeSealError("runtime probe nested members are not exact")
    if platform_value.get("system") != "Darwin" or platform_value.get("machine") != "arm64" or type(platform_value.get("macosProductVersion")) is not str or not platform_value.get("macosProductVersion") or type(platform_value.get("sysconfigPlatform")) is not str or not platform_value.get("sysconfigPlatform") or python_value.get("implementation") != "CPython" or python_value.get("version") != "3.12.10" or type(python_value.get("build")) is not str or not python_value.get("build") or python_value.get("cacheTag") != "cpython-312" or type(python_value.get("soabi")) is not str or not python_value["soabi"].startswith("cpython-312") or environment != {"pythonHashSeed": "0", "timezone": "Asia/Seoul"} or flags != {"isolated": 1, "noSite": 1, "ignoreEnvironment": 1, "safePath": True} or any(type(flags[name]) is not expected_type for name, expected_type in (("isolated", int), ("noSite", int), ("ignoreEnvironment", int), ("safePath", bool))) or probe.get("hashProbes") != list(_HASH_PROBES):
        raise RuntimeSealError("runtime probe does not match the pinned platform")
    expected_executable, expected_purelib, expected_platlib = _runtime_layout(runtime_venv)
    if _path_alias_key(expected_executable) != _path_alias_key(executable):
        raise RuntimeSealError("runtime interpreter path is not canonical")
    paths: dict[str, Path] = {}
    for member in ("prefix", "executable", "purelib", "platlib"):
        raw = probe.get(member)
        if type(raw) is not str:
            raise RuntimeSealError(f"runtime probe {member} path is invalid")
        paths[member] = _checked_absolute(Path(raw), f"runtime probe {member}")
    if _path_alias_key(paths["prefix"]) != _path_alias_key(runtime_venv):
        raise RuntimeSealError("runtime probe prefix escaped the supplied venv")
    if _path_alias_key(paths["executable"]) != _path_alias_key(executable):
        raise RuntimeSealError("runtime probe executable is not the supplied interpreter")
    if _path_alias_key(paths["purelib"]) != _path_alias_key(expected_purelib) or _path_alias_key(paths["platlib"]) != _path_alias_key(expected_platlib):
        raise RuntimeSealError("runtime probe site paths do not match pyvenv.cfg")
    return paths["purelib"], paths["platlib"]


def _checked_record_path(raw: str) -> tuple[PurePosixPath, bool]:
    if not raw or "\\" in raw or raw.startswith("/") or raw.endswith("/") or any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise RuntimeSealError("installed RECORD contains an unsafe path")
    path = PurePosixPath(raw)
    generated = len(path.parts) == 5 and path.parts[:4] == ("..", "..", "..", "bin")
    if path.is_absolute() or path.as_posix() != raw or (".." in path.parts and not generated):
        raise RuntimeSealError("installed RECORD path escapes its installation root")
    return path, generated


def _reject_symlink_components(base: Path, path: PurePosixPath, generated: bool) -> Path:
    current = base
    for part in path.parts:
        if part == "..":
            current = current.parent
            continue
        current = current / part
        try:
            result = os.lstat(current)
        except OSError as error:
            raise RuntimeSealError("installed RECORD target is missing") from error
        if stat.S_ISLNK(result.st_mode):
            raise RuntimeSealError("installed RECORD target contains a symlink")
    if not generated:
        base_key = _path_alias_key(base).rstrip(os.sep) + os.sep
        if not _path_alias_key(current).startswith(base_key):
            raise RuntimeSealError("installed RECORD target escaped site-packages")
    return current


def _read_record_target(target: Path, hash_field: str, size_field: str, label: str) -> tuple[str, bytes]:
    match = _RECORD_HASH.fullmatch(hash_field)
    if match is None or _RECORD_SIZE.fullmatch(size_field) is None:
        raise RuntimeSealError(f"{label} RECORD requires canonical sha256 and size")
    payload = _read_regular(target, f"{label} RECORD target", _MAX_WHEEL)
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")
    if encoded != match.group(1) or len(payload) != int(size_field):
        raise RuntimeSealError(f"{label} RECORD does not authenticate installed bytes")
    return hashlib.sha256(payload).hexdigest(), payload


def _console_wrapper(executable: Path, target: str) -> bytes:
    module, separator, function = target.partition(":")
    if (
        separator != ":"
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function)
    ):
        raise RuntimeSealError("console script target is invalid")
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


def _verify_installed_distribution(
    purelib: Path,
    dist_info: Path,
    expected_name: str,
    expected_version: str,
    expected_tree: str,
    capture_path: str | None,
    inode_owners: dict[tuple[int, int], str],
    expected_scripts: Mapping[str, str] | None = None,
) -> dict[str, object]:
    _require_directory(dist_info, f"{expected_name} dist-info")
    if dist_info.parent != purelib:
        raise RuntimeSealError(f"{expected_name} dist-info is outside its site root")
    metadata_payload = _read_regular(dist_info / "METADATA", f"{expected_name} METADATA", _MAX_SMALL_FILE)
    name, version = _metadata_identity(metadata_payload, expected_name)
    if name != expected_name or version != expected_version:
        raise RuntimeSealError(f"installed {expected_name} identity is not exact")
    record_path = dist_info / "RECORD"
    record_payload = _read_regular(record_path, f"{expected_name} RECORD", _MAX_RECORD)
    rows = _parse_record(record_payload, expected_name)
    seen: set[str] = set()
    alias_paths: set[str] = set()
    record_self = 0
    lines: list[tuple[bytes, bytes]] = []
    claimed: set[str] = set()
    captured: bytes | None = None
    generated_scripts: list[str] = []
    for raw_path, hash_field, size_field in rows:
        alias = unicodedata.normalize("NFC", raw_path).casefold()
        if raw_path in seen or alias in alias_paths:
            raise RuntimeSealError(f"installed {expected_name} RECORD paths are ambiguous")
        seen.add(raw_path)
        alias_paths.add(alias)
        path, generated = _checked_record_path(raw_path)
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            raise RuntimeSealError(
                f"installed {expected_name} RECORD contains bytecode or a cache path"
            )
        target = _reject_symlink_components(purelib, path, generated)
        result = os.lstat(target)
        if not stat.S_ISREG(result.st_mode):
            raise RuntimeSealError(f"installed {expected_name} RECORD target is not regular")
        identity = (result.st_dev, result.st_ino)
        previous = inode_owners.setdefault(identity, raw_path)
        if previous != raw_path or result.st_nlink != 1:
            raise RuntimeSealError("installed RECORD paths hardlink or alias one file")
        claimed.add(_path_alias_key(target))
        if target == record_path:
            record_self += 1
            if hash_field or size_field:
                raise RuntimeSealError(f"installed {expected_name} RECORD self-row is hashed")
            continue
        file_hex, payload = _read_record_target(target, hash_field, size_field, expected_name)
        if raw_path == capture_path:
            captured = payload
        if generated:
            generated_scripts.append(path.name)
            required_target = ({} if expected_scripts is None else expected_scripts).get(
                path.name
            )
            runtime_root = purelib.parents[2]
            expected_executable = runtime_root / "bin/python"
            if (
                required_target is None
                or target != runtime_root / "bin" / path.name
                or stat.S_IMODE(result.st_mode) != 0o755
                or payload != _console_wrapper(expected_executable, required_target)
            ):
                raise RuntimeSealError(
                    f"installed {expected_name} console wrapper is not exact"
                )
        if not _tree_excluded(path, generated):
            encoded = raw_path.encode("utf-8")
            lines.append((encoded, encoded + b"=sha256:" + file_hex.encode("ascii") + b"\n"))
    if record_self != 1:
        raise RuntimeSealError(f"installed {expected_name} RECORD self-row is not unique")
    if _read_regular(record_path, f"{expected_name} RECORD", _MAX_RECORD) != record_payload:
        raise RuntimeSealError(f"installed {expected_name} RECORD changed during capture")
    digest = _sha256_uri(b"".join(line for _path, line in sorted(lines, key=lambda item: item[0])))
    if digest != expected_tree:
        raise RuntimeSealError(f"installed {expected_name} tree does not match its wheel")
    observed_scripts = frozenset(generated_scripts)
    required_scripts = frozenset() if expected_scripts is None else frozenset(expected_scripts)
    if len(generated_scripts) != len(observed_scripts) or observed_scripts != required_scripts:
        raise RuntimeSealError(f"installed {expected_name} console script relation is not exact")
    return {"tree": digest, "captured": captured, "claimed": claimed, "generatedScripts": tuple(sorted(generated_scripts, key=lambda item: item.encode("utf-8")))}


def _discover_distributions(purelib: Path, platlib: Path) -> dict[str, Path]:
    roots = [purelib]
    if _path_alias_key(platlib) != _path_alias_key(purelib):
        roots.append(platlib)
    observed: dict[str, Path] = {}
    aliases: set[str] = set()
    for root in roots:
        _require_directory(root, "runtime site-packages")
        for entry in os.scandir(root):
            if not entry.name.endswith(".dist-info"):
                continue
            result = os.stat(entry.path, follow_symlinks=False)
            if stat.S_ISLNK(result.st_mode) or not stat.S_ISDIR(result.st_mode):
                raise RuntimeSealError("installed dist-info must be a non-symlink directory")
            dist_info = Path(entry.path)
            metadata = _read_regular(dist_info / "METADATA", "installed METADATA", _MAX_SMALL_FILE)
            name, _version = _metadata_identity(metadata, entry.name)
            alias = unicodedata.normalize("NFC", name).casefold()
            if name in observed or alias in aliases:
                raise RuntimeSealError("installed distribution names are duplicated or ambiguous")
            aliases.add(alias)
            observed[name] = dist_info
    if frozenset(observed) != _EXPECTED_DISTRIBUTIONS:
        raise RuntimeSealError("installed distribution inventory is not exact")
    return observed


def _site_inventory(roots: tuple[Path, ...]) -> dict[str, tuple[int, int, int, int, int, int]]:
    inventory: dict[str, tuple[int, int, int, int, int, int]] = {}
    for root in roots:
        for current, directories, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            directories.sort(key=lambda item: item.encode("utf-8"))
            files.sort(key=lambda item: item.encode("utf-8"))
            for name in directories:
                result = os.lstat(current_path / name)
                if stat.S_ISLNK(result.st_mode) or not stat.S_ISDIR(result.st_mode):
                    raise RuntimeSealError("site-packages contains an unsafe directory")
                if name == "__pycache__":
                    raise RuntimeSealError(
                        "site-packages contains a bytecode cache directory"
                    )
            for name in files:
                path = current_path / name
                result = os.lstat(path)
                if stat.S_ISLNK(result.st_mode) or not stat.S_ISREG(result.st_mode):
                    raise RuntimeSealError("site-packages contains an unsafe file")
                if path.suffix in {".pyc", ".pyo"}:
                    raise RuntimeSealError("site-packages contains bytecode or pyc")
                key = _path_alias_key(path)
                if key in inventory:
                    raise RuntimeSealError("site-packages paths are ambiguous")
                inventory[key] = _fingerprint(result)
    return inventory


def _inspect_runtime(purelib: Path, platlib: Path, wheels: dict[str, dict[str, object]]) -> tuple[dict[str, str], bytes]:
    roots = (purelib,) if _path_alias_key(purelib) == _path_alias_key(platlib) else (purelib, platlib)
    before = _site_inventory(roots)
    distributions = _discover_distributions(purelib, platlib)
    inode_owners: dict[tuple[int, int], str] = {}
    claimed: set[str] = set()
    trees: dict[str, str] = {}
    provenance: bytes | None = None
    for name in (*_PACKAGE_NAMES, "pip", _PRODUCER_DISTRIBUTION):
        wheel = wheels[name]
        dist_info = distributions[name]
        result = _verify_installed_distribution(
            dist_info.parent,
            dist_info,
            name,
            str(wheel["version"]),
            str(wheel["tree"]),
            _PROVENANCE_PATH if name == _PRODUCER_DISTRIBUTION else None,
            inode_owners,
            _GENERATED_SCRIPTS.get(name, {}),
        )
        trees[name] = str(result["tree"])
        claimed.update(result["claimed"])
        if name == _PRODUCER_DISTRIBUTION:
            captured = result["captured"]
            provenance = captured if isinstance(captured, bytes) else None
    after = _site_inventory(roots)
    if before != after:
        raise RuntimeSealError("site-packages changed during runtime capture")
    for path_key in after:
        if path_key in claimed:
            continue
        raise RuntimeSealError("site-packages contains an unclaimed meaningful file")
    if provenance is None:
        raise RuntimeSealError("installed producer provenance is not RECORD-backed")
    return trees, provenance


def _validate_provenance(payload: bytes, expected: dict[str, object]) -> None:
    value = _strict_json(payload, "producer provenance")
    if type(value) is not dict or frozenset(value) != _PROVENANCE_FIELDS or value != expected:
        raise RuntimeSealError("producer provenance does not match source, locks, and wheel")


def _output_parent(path: Path) -> Path:
    parent = path.parent
    _require_directory(parent, "runtime manifest parent")
    return parent


def _identity(result: os.stat_result) -> tuple[int, int]:
    return result.st_dev, result.st_ino


def _read_retained_regular(
    descriptor: int, label: str, maximum: int
) -> tuple[bytes, os.stat_result]:
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size < 0
            or before.st_size > maximum
        ):
            raise RuntimeSealError(f"{label} is not a bounded regular file")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise RuntimeSealError(f"{label} exceeds its size limit")
        after = os.fstat(descriptor)
    except OSError as error:
        raise RuntimeSealError(f"{label} changed while being read") from error
    if _fingerprint(before) != _fingerprint(after) or total != after.st_size:
        raise RuntimeSealError(f"{label} changed while being read")
    return b"".join(chunks), after


def _parent_identity(parent: Path, parent_fd: int) -> tuple[int, int]:
    _reject_intermediate_symlinks(parent, "runtime manifest parent")
    try:
        named = os.lstat(parent)
        opened = os.fstat(parent_fd)
    except OSError as error:
        raise RuntimeSealError("runtime manifest parent changed") from error
    if (
        stat.S_ISLNK(named.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or _identity(named) != _identity(opened)
    ):
        raise RuntimeSealError("runtime manifest parent identity changed")
    return _identity(opened)


def _named_identity(
    parent_fd: int, name: str, owner: tuple[int, int], label: str
) -> os.stat_result:
    try:
        result = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise RuntimeSealError(f"{label} changed or disappeared") from error
    if (
        stat.S_ISLNK(result.st_mode)
        or not stat.S_ISREG(result.st_mode)
        or _identity(result) != owner
    ):
        raise RuntimeSealError(f"{label} identity was replaced")
    return result


def _invalidate_retained_owner(
    descriptor: int, owner: tuple[int, int]
) -> bool:
    """Invalidate only the inode retained by ``descriptor``; never unlink a name."""
    try:
        opened = os.fstat(descriptor)
    except OSError:
        return False
    if not stat.S_ISREG(opened.st_mode) or _identity(opened) != owner:
        return False
    for operation in (
        lambda: os.pwrite(descriptor, b"\0", 0),
        lambda: os.ftruncate(descriptor, 0),
        lambda: os.fsync(descriptor),
    ):
        try:
            current = os.fstat(descriptor)
            if not stat.S_ISREG(current.st_mode) or _identity(current) != owner:
                return False
            operation()
        except OSError:
            continue
    try:
        final = os.fstat(descriptor)
    except OSError:
        return False
    return (
        stat.S_ISREG(final.st_mode)
        and _identity(final) == owner
        and final.st_size == 0
    )


def _open_named(
    parent_fd: int, name: str, owner: tuple[int, int], label: str
) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as error:
        raise RuntimeSealError(f"{label} cannot be retained") from error
    try:
        opened = os.fstat(descriptor)
        named = _named_identity(parent_fd, name, owner, label)
        if _identity(opened) != _identity(named) or not stat.S_ISREG(opened.st_mode):
            raise RuntimeSealError(f"{label} identity changed while opening")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _publish_manifest(
    output: Path,
    payload: bytes,
    *,
    post_publish: Callable[[], None] | None = None,
) -> str:
    parent = _output_parent(output)
    _reject_intermediate_symlinks(parent, "runtime manifest parent")
    parent_before = os.lstat(parent)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(parent, flags)
    except OSError as error:
        raise RuntimeSealError("runtime manifest parent cannot be retained") from error
    output_fd: int | None = None
    output_owner: tuple[int, int] | None = None
    created = False
    try:
        opened_parent = os.fstat(parent_fd)
        if _identity(parent_before) != _identity(opened_parent):
            raise RuntimeSealError("runtime manifest parent changed while opening")
        try:
            existing = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
                raise RuntimeSealError("existing runtime manifest is not a regular file")
            if stat.S_IMODE(existing.st_mode) != 0o644 or existing.st_nlink != 1:
                raise RuntimeSealError("existing runtime manifest mode or link count is not canonical")
            output_owner = _identity(existing)
            output_fd = _open_named(
                parent_fd,
                output.name,
                output_owner,
                "existing runtime manifest",
            )
            observed, retained = _read_retained_regular(
                output_fd, "existing runtime manifest", _MAX_SMALL_FILE
            )
            if observed != payload:
                raise RuntimeSealError("existing runtime manifest differs; refusing overwrite")
            if stat.S_IMODE(retained.st_mode) != 0o644 or retained.st_nlink != 1:
                raise RuntimeSealError(
                    "existing runtime manifest mode or link count changed"
                )
            if post_publish is not None:
                post_publish()
            _parent_identity(parent, parent_fd)
            named = _named_identity(
                parent_fd,
                output.name,
                output_owner,
                "existing runtime manifest",
            )
            observed_after, retained_after = _read_retained_regular(
                output_fd, "existing runtime manifest", _MAX_SMALL_FILE
            )
            if (
                observed_after != payload
                or _fingerprint(retained) != _fingerprint(retained_after)
                or _identity(named) != _identity(retained_after)
                or stat.S_IMODE(retained_after.st_mode) != 0o644
                or retained_after.st_nlink != 1
            ):
                raise RuntimeSealError("existing runtime manifest changed during reuse")
            os.fsync(parent_fd)
            return "reused"
        create_flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            output_fd = os.open(
                output.name, create_flags, 0o600, dir_fd=parent_fd
            )
        except FileExistsError as error:
            raise RuntimeSealError(
                "runtime manifest appeared during no-clobber publication"
            ) from error
        except OSError as error:
            raise RuntimeSealError("runtime manifest cannot be created") from error
        created = True
        opened = os.fstat(output_fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise RuntimeSealError("created runtime manifest inode is not canonical")
        output_owner = _identity(opened)
        os.fchmod(output_fd, 0o644)
        offset = 0
        while offset < len(payload):
            written = os.write(output_fd, payload[offset:])
            if written <= 0:
                raise OSError("short manifest write")
            offset += written
        os.fsync(output_fd)
        if os.fstat(output_fd).st_size != len(payload):
            raise RuntimeSealError("runtime manifest size is inconsistent")
        published = _named_identity(
            parent_fd,
            output.name,
            output_owner,
            "published runtime manifest",
        )
        observed, retained = _read_retained_regular(
            output_fd, "published runtime manifest", _MAX_SMALL_FILE
        )
        if (
            observed != payload
            or stat.S_IMODE(published.st_mode) != 0o644
            or published.st_nlink != 1
            or stat.S_IMODE(retained.st_mode) != 0o644
            or retained.st_nlink != 1
        ):
            raise RuntimeSealError("published runtime manifest bytes are inconsistent")
        if post_publish is not None:
            post_publish()
        _parent_identity(parent, parent_fd)
        named_after = _named_identity(
            parent_fd,
            output.name,
            output_owner,
            "published runtime manifest",
        )
        observed_after, retained_after = _read_retained_regular(
            output_fd, "published runtime manifest", _MAX_SMALL_FILE
        )
        if (
            observed_after != payload
            or _fingerprint(retained) != _fingerprint(retained_after)
            or _identity(named_after) != _identity(retained_after)
            or stat.S_IMODE(retained_after.st_mode) != 0o644
            or retained_after.st_nlink != 1
        ):
            raise RuntimeSealError("published runtime manifest changed during validation")
        os.fsync(parent_fd)
        return "created"
    except BaseException as error:
        cleanup_failed = False
        if created and output_fd is not None and output_owner is not None:
            cleanup_failed = not _invalidate_retained_owner(
                output_fd, output_owner
            )
            try:
                os.fsync(parent_fd)
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            raise RuntimeSealError("runtime manifest cleanup failed") from error
        raise
    finally:
        if output_fd is not None:
            try:
                os.close(output_fd)
            except OSError:
                pass
        try:
            os.close(parent_fd)
        except OSError:
            pass


def _validate_topology(source_root: Path, wheel_dir: Path, producer_lock: Path, runtime_venv: Path, output: Path) -> None:
    for path, label in (
        (source_root, "source root"),
        (wheel_dir, "producer wheel directory"),
        (producer_lock, "producer lock"),
        (runtime_venv, "runtime venv"),
    ):
        _reject_intermediate_symlinks(path, label)
    _reject_intermediate_symlinks(
        output, "runtime manifest output", allow_missing_final=True
    )
    directory_identities = {_require_directory(source_root, "source root"), _require_directory(wheel_dir, "producer wheel directory"), _require_directory(runtime_venv, "runtime venv")}
    if len(directory_identities) != 3:
        raise RuntimeSealError("runtime seal directories alias one another")
    if _path_alias_key(output) in {_path_alias_key(producer_lock), _path_alias_key(runtime_venv / "bin/python")}:
        raise RuntimeSealError("runtime manifest output aliases an authenticated input")
    if _path_is_within(output, wheel_dir) or _path_is_within(output, runtime_venv) or _path_is_within(output, source_root / "equipment_quality"):
        raise RuntimeSealError("runtime manifest output is nested in an authenticated mutable input")
    physical_output = Path(os.path.realpath(output))
    physical_inputs = (
        Path(os.path.realpath(wheel_dir)),
        Path(os.path.realpath(runtime_venv)),
        Path(os.path.realpath(source_root / "equipment_quality")),
    )
    if any(_path_is_within(physical_output, item) for item in physical_inputs):
        raise RuntimeSealError(
            "runtime manifest output is physically nested in an authenticated input"
        )
    _output_parent(output)


def _capture_manifest(
    source_root: Path,
    producer_wheel_dir: Path,
    producer_lock: Path,
    runtime_venv: Path,
) -> bytes:
    source = _source_snapshot(source_root)
    _validate_pyproject(source["pyproject.toml"])
    source_sha = _source_digest(source)
    locks, producer_lock_bytes = _authenticate_locks(source_root, producer_lock)
    wheels = _authenticate_wheelhouse(producer_wheel_dir)
    producer_wheel = wheels[_PRODUCER_DISTRIBUTION]
    expected_provenance = {
        "schemaVersion": "sfep-producer-provenance/v1",
        "producerName": "equipment-quality",
        "distributionName": _PRODUCER_DISTRIBUTION,
        "version": _PRODUCER_VERSION,
        "sourceSha256": source_sha,
        "sourceDateEpoch": _SOURCE_DATE_EPOCH,
        "requirementsLockSha256": _sha256_uri(locks["requirements.lock"]),
    }
    wheel_provenance = producer_wheel.get("provenance")
    if not isinstance(wheel_provenance, bytes):
        raise RuntimeSealError("producer wheel provenance is missing")
    _validate_provenance(wheel_provenance, expected_provenance)
    executable = runtime_venv / "bin/python"
    probe = _probe_runtime(runtime_venv, executable)
    purelib, platlib = _validate_probe(probe, runtime_venv, executable)
    trees, installed_provenance = _inspect_runtime(purelib, platlib, wheels)
    _validate_provenance(installed_provenance, expected_provenance)
    platform_value = probe["platform"]
    python_value = probe["python"]
    assert isinstance(platform_value, dict) and isinstance(python_value, dict)
    packages = []
    for name in _PACKAGE_NAMES:
        spec = _SPEC_BY_NAME[name]
        wheel = wheels[name]
        packages.append({"name": name, "version": spec[1], "direct": name in _DIRECT_PACKAGES, "wheelFilename": spec[2], "wheelTag": spec[3], "wheelSha256": wheel["sha256"], "installedCodeTreeSha256": trees[name]})
    manifest = {
        "schemaVersion": "sfep-producer-runtime/v1",
        "platform": {"system": platform_value["system"], "machine": platform_value["machine"], "macosProductVersion": platform_value["macosProductVersion"], "sysconfigPlatform": platform_value["sysconfigPlatform"]},
        "python": {"implementation": python_value["implementation"], "version": python_value["version"], "build": python_value["build"], "cacheTag": python_value["cacheTag"], "soabi": python_value["soabi"], "executableSha256": _hash_executable(executable)},
        "pipVersion": _PIP_VERSION,
        "locks": {"pyproject": _sha256_uri(source["pyproject.toml"]), "bootstrap": _sha256_uri(locks["bootstrap.lock"]), "buildRequirements": _sha256_uri(locks["build-requirements.lock"]), "requirements": _sha256_uri(locks["requirements.lock"]), "wheelhouse": _sha256_uri(locks["wheelhouse.lock.json"]), "producer": _sha256_uri(producer_lock_bytes)},
        "packages": packages,
        "producer": {"name": "equipment-quality", "version": _PRODUCER_VERSION, "wheelFilename": _PRODUCER_FILENAME, "wheelSha256": producer_wheel["sha256"], "installedCodeTreeSha256": trees[_PRODUCER_DISTRIBUTION], "sourceSha256": source_sha},
        "environmentPolicy": {"pythonHashSeed": "0", "timezone": "Asia/Seoul", "localeIndependentParsing": True, "floatPolicy": "IEEE754_BINARY64_FINITE"},
    }
    payload = _canonical_json_bytes(manifest)
    if _source_snapshot(source_root) != source:
        raise RuntimeSealError("producer source changed during runtime capture")
    locks_after, producer_after = _authenticate_locks(source_root, producer_lock)
    if locks_after != locks or producer_after != producer_lock_bytes:
        raise RuntimeSealError("runtime lock inputs changed during capture")
    wheels_after = _authenticate_wheelhouse(producer_wheel_dir)
    if wheels_after != wheels:
        raise RuntimeSealError("runtime wheel inputs changed during capture")
    return payload


def _run(source_root: Path, producer_wheel_dir: Path, producer_lock: Path, runtime_venv: Path, output: Path) -> str:
    source_root = _checked_absolute(source_root, "source root")
    producer_wheel_dir = _checked_absolute(producer_wheel_dir, "producer wheel directory")
    producer_lock = _checked_absolute(producer_lock, "producer lock")
    runtime_venv = _checked_absolute(runtime_venv, "runtime venv")
    output = _checked_absolute(output, "runtime manifest output")

    def capture() -> bytes:
        _validate_topology(
            source_root,
            producer_wheel_dir,
            producer_lock,
            runtime_venv,
            output,
        )
        return _capture_manifest(
            source_root, producer_wheel_dir, producer_lock, runtime_venv
        )

    payload = capture()
    if capture() != payload:
        raise RuntimeSealError("runtime inputs changed between joint captures")

    def final_reauthentication() -> None:
        if capture() != payload:
            raise RuntimeSealError(
                "runtime inputs changed during final joint reauthentication"
            )

    status = _publish_manifest(
        output, payload, post_publish=final_reauthentication
    )
    return f"{status} {_sha256_uri(payload)}"


def main(arguments: list[str] | None = None) -> int:
    parser = _parser()
    try:
        namespace = parser.parse_args(arguments)
        result = _run(Path(namespace.source_root), Path(namespace.producer_wheel_dir), Path(namespace.producer_lock), Path(namespace.runtime_venv), Path(namespace.output))
    except RuntimeSealError as error:
        print(f"seal_runtime.py: error: {error}", file=sys.stderr)
        return 2
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
