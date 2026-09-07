#!/usr/bin/env python3
"""Verify and publish a reproducible producer wheel pair."""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable
import csv
from email import policy
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import struct
import sys
import tempfile
import tomllib
import zipfile
import zlib


_SOURCE_DATE_EPOCH = "1735689600"
_ZIP_EPOCH = (2025, 1, 1, 0, 0, 0)
_SOURCE_HEADER = b"sfep-source-lines/v1\n"
_PROVENANCE_PATH = "equipment_quality/sfep_producer_provenance.json"
_PRODUCER_FILENAME = "sfep_equipment_quality-1.1.0-py3-none-any.whl"
_DIST_INFO = "sfep_equipment_quality-1.1.0.dist-info"
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
    b"Version: 1.1.0\n"
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
_LOCAL_HEADER = struct.Struct("<IHHHHHIIIHH")
_CENTRAL_HEADER = struct.Struct("<IHHHHHHIIIHHHHHII")
_END_RECORD = struct.Struct("<IHHHHIIH")
_LOCAL_SIGNATURE = 0x04034B50
_CENTRAL_SIGNATURE = 0x02014B50
_END_SIGNATURE = 0x06054B50
_DOS_EPOCH_TIME = 0
_DOS_EPOCH_DATE = ((2025 - 1980) << 9) | (1 << 5) | 1
_Fingerprint = tuple[int, int, int, int, int, int]
_DirectoryInventory = tuple[_Fingerprint, dict[str, _Fingerprint]]
_DirectoryPin = tuple[Path, Path, tuple[int, int]]
_SourceAttestation = tuple[
    tuple[int, int, int],
    dict[str, _Fingerprint],
    dict[str, _Fingerprint],
]


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


def _fingerprint(result: os.stat_result) -> _Fingerprint:
    return (
        result.st_dev,
        result.st_ino,
        stat.S_IFMT(result.st_mode),
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _directory_inventory_fd(
    descriptor: int,
    label: str,
) -> _DirectoryInventory:
    try:
        before = os.fstat(descriptor)
        scanner = os.scandir(descriptor)
        try:
            scanned = list(scanner)
        finally:
            close = getattr(scanner, "close", None)
            if close is not None:
                close()
        entries: dict[str, _Fingerprint] = {}
        for entry in scanned:
            if entry.name in entries:
                raise ProducerSealError(f"{label} contains a duplicate entry")
            result = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
            entries[entry.name] = _fingerprint(result)
        after = os.fstat(descriptor)
    except OSError as error:
        raise ProducerSealError(f"{label} changed while being inventoried") from error
    if not stat.S_ISDIR(before.st_mode) or not stat.S_ISDIR(after.st_mode):
        raise ProducerSealError(f"{label} type changed while being inventoried")
    if _fingerprint(before) != _fingerprint(after):
        raise ProducerSealError(f"{label} changed while being inventoried")
    return _fingerprint(before), dict(
        sorted(entries.items(), key=lambda item: item[0].encode("utf-8"))
    )


def _require_directory(path: Path, label: str) -> tuple[int, int]:
    try:
        result = os.lstat(path)
    except OSError as error:
        raise ProducerSealError(f"{label} is unavailable") from error
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISDIR(result.st_mode):
        raise ProducerSealError(f"{label} must be a non-symlink directory")
    return _identity(result)


def _directory_inventory(
    path: Path,
    label: str,
) -> _DirectoryInventory:
    try:
        before = os.lstat(path)
    except OSError as error:
        raise ProducerSealError(f"{label} is unavailable") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise ProducerSealError(f"{label} must be a non-symlink directory")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ProducerSealError(f"{label} cannot be opened safely") from error
    try:
        opened, entries = _directory_inventory_fd(descriptor, label)
    finally:
        os.close(descriptor)
    try:
        path_after = os.lstat(path)
    except OSError as error:
        raise ProducerSealError(f"{label} changed while being inventoried") from error
    if not (
        _fingerprint(before)
        == opened
        == _fingerprint(path_after)
    ):
        raise ProducerSealError(f"{label} changed while being inventoried")
    return opened, entries


def _physical_directory(
    path: Path,
    label: str,
) -> tuple[Path, tuple[int, int]]:
    identity = _require_directory(path, label)
    physical = Path(os.path.realpath(path))
    if _require_directory(physical, label) != identity:
        raise ProducerSealError(f"{label} changed while resolving its physical path")
    return physical, identity


def _pin_directory(path: Path, label: str) -> _DirectoryPin:
    physical, identity = _physical_directory(path, label)
    return path, physical, identity


def _recheck_directory_pin(pin: _DirectoryPin, label: str) -> None:
    path, expected_physical, expected_identity = pin
    physical, identity = _physical_directory(path, label)
    if physical != expected_physical or identity != expected_identity:
        raise ProducerSealError(f"{label} physical identity changed")


def _path_ancestry(path: Path) -> tuple[tuple[int, int] | None, set[tuple[int, int]]]:
    endpoint: tuple[int, int] | None = None
    identities: set[tuple[int, int]] = set()
    current = path
    first = True
    while True:
        try:
            result = os.lstat(current)
        except FileNotFoundError:
            result = None
        except OSError as error:
            raise ProducerSealError("input path ancestry cannot be inspected") from error
        if result is not None:
            identity = _identity(result)
            identities.add(identity)
            if first:
                endpoint = identity
        first = False
        parent = current.parent
        if parent == current:
            break
        current = parent
    return endpoint, identities


def _paths_overlap(first: Path, second: Path) -> bool:
    first_endpoint, first_ancestry = _path_ancestry(first)
    second_endpoint, second_ancestry = _path_ancestry(second)
    if (
        first_endpoint is not None
        and first_endpoint in second_ancestry
        or second_endpoint is not None
        and second_endpoint in first_ancestry
    ):
        return True
    try:
        common = Path(os.path.commonpath((os.fspath(first), os.fspath(second))))
    except ValueError:
        return False
    return common == first or common == second


def _validate_path_topology(
    source_root: Path,
    wheel_dir_a: Path,
    wheel_dir_b: Path,
    wheelhouse: Path,
    producer_lock: Path,
) -> tuple[_DirectoryPin, _DirectoryPin, _DirectoryPin, _DirectoryPin]:
    input_pins = (
        _pin_directory(wheel_dir_a, "wheel directory A"),
        _pin_directory(wheel_dir_b, "wheel directory B"),
        _pin_directory(wheelhouse, "wheelhouse"),
    )
    for index, (_first, first_path, first_identity) in enumerate(input_pins):
        for _second, second_path, second_identity in input_pins[index + 1:]:
            if first_identity == second_identity or _paths_overlap(first_path, second_path):
                raise ProducerSealError(
                    "wheel directories and wheelhouse must be physically disjoint and independent"
                )
    lock_parent_pin = _pin_directory(
        producer_lock.parent,
        "producer lock parent",
    )
    _lock_parent, lock_parent_physical, _lock_parent_identity = lock_parent_pin
    lock_physical = lock_parent_physical / producer_lock.name
    for _input, input_path, _identity_value in input_pins:
        if lock_physical == input_path or _paths_overlap(input_path, lock_physical):
            raise ProducerSealError(
                "producer lock and wheel input directories must be physically disjoint"
            )
    package_physical, _package_identity = _physical_directory(
        source_root / "equipment_quality",
        "equipment_quality source directory",
    )
    if lock_physical == package_physical or _paths_overlap(
        package_physical,
        lock_physical,
    ):
        raise ProducerSealError(
            "producer lock and authenticated source package must be physically disjoint"
        )
    return input_pins[0], input_pins[1], input_pins[2], lock_parent_pin


def _recheck_path_topology(
    topology: tuple[_DirectoryPin, _DirectoryPin, _DirectoryPin, _DirectoryPin],
    expected_inventories: tuple[
        _DirectoryInventory,
        _DirectoryInventory,
        _DirectoryInventory,
        _DirectoryInventory,
    ],
) -> tuple[int, int, int, int]:
    labels = (
        "wheel directory A",
        "wheel directory B",
        "wheelhouse",
        "producer lock parent",
    )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    try:
        for pin, expected_inventory, label in zip(
            topology,
            expected_inventories,
            labels,
            strict=True,
        ):
            _recheck_directory_pin(pin, label)
            try:
                descriptor = os.open(pin[0], flags)
            except OSError as error:
                raise ProducerSealError(
                    f"{label} cannot be opened for publication"
                ) from error
            descriptors.append(descriptor)
            opened = os.fstat(descriptor)
            if not stat.S_ISDIR(opened.st_mode) or _identity(opened) != pin[2]:
                raise ProducerSealError(f"{label} identity changed before publication")
            if _directory_inventory_fd(descriptor, label) != expected_inventory:
                raise ProducerSealError(
                    f"{label} inventory changed before publication"
                )
            _recheck_directory_pin(pin, label)
        input_pins = topology[:3]
        for index, (_first, first_path, first_identity) in enumerate(input_pins):
            for _second, second_path, second_identity in input_pins[index + 1:]:
                if first_identity == second_identity or _paths_overlap(
                    first_path,
                    second_path,
                ):
                    raise ProducerSealError(
                        "wheel directories and wheelhouse are no longer physically disjoint"
                    )
        return tuple(descriptors)  # type: ignore[return-value]
    except Exception:
        for descriptor in descriptors:
            os.close(descriptor)
        raise


def _read_regular_once(
    path: Path,
    label: str,
    expected: _Fingerprint | None = None,
) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as error:
        raise ProducerSealError(f"{label} is unavailable") from error
    if stat.S_ISLNK(before.st_mode):
        raise ProducerSealError(f"{label} must not be a symlink")
    if not stat.S_ISREG(before.st_mode):
        raise ProducerSealError(f"{label} must be a regular file")
    if expected is not None and _fingerprint(before) != expected:
        raise ProducerSealError(f"{label} identity changed before reading")
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
    if (
        _fingerprint(before) != _fingerprint(opened)
        or _fingerprint(opened) != _fingerprint(after)
        or _fingerprint(after) != _fingerprint(path_after)
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


def _source_inventory(
    source_root: Path,
    package_root: Path,
    expected_package: _Fingerprint,
) -> dict[str, _Fingerprint]:
    inventory: dict[str, _Fingerprint] = {}

    def visit(directory: Path, expected_directory: _Fingerprint) -> None:
        directory_fingerprint, entries = _directory_inventory(
            directory,
            "producer source directory",
        )
        if directory_fingerprint != expected_directory:
            raise ProducerSealError("producer source directory identity changed")
        relative_directory = directory.relative_to(source_root).as_posix()
        _safe_relative(relative_directory, "producer source")
        inventory[relative_directory] = directory_fingerprint
        for name, entry_fingerprint in entries.items():
            path = directory / name
            relative = path.relative_to(source_root).as_posix()
            _safe_relative(relative, "producer source")
            if relative in inventory:
                raise ProducerSealError("producer source contains a duplicate path")
            inventory[relative] = entry_fingerprint
            entry_type = entry_fingerprint[2]
            if entry_type == stat.S_IFLNK:
                raise ProducerSealError("producer source must not contain a symlink")
            if entry_type == stat.S_IFDIR:
                visit(path, entry_fingerprint)
            elif entry_type != stat.S_IFREG:
                raise ProducerSealError("producer source must contain only regular files")

    visit(package_root, expected_package)
    return dict(sorted(inventory.items(), key=lambda item: item[0].encode("utf-8")))


def _source_attestation(source_root: Path) -> _SourceAttestation:
    root_before, entries_before = _directory_inventory(source_root, "source root")
    selected_before: dict[str, _Fingerprint] = {}
    for filename in (
        "pyproject.toml",
        "bootstrap.lock",
        "build-requirements.lock",
        "requirements.lock",
        "wheelhouse.lock.json",
    ):
        entry = entries_before.get(filename)
        if entry is None or entry[2] != stat.S_IFREG:
            raise ProducerSealError(f"{filename} must be a regular file")
        selected_before[filename] = entry
    package_before = entries_before.get("equipment_quality")
    if package_before is None or package_before[2] != stat.S_IFDIR:
        raise ProducerSealError("equipment_quality source directory is unavailable")
    inventory_before = _source_inventory(
        source_root,
        source_root / "equipment_quality",
        package_before,
    )
    root_after, entries_after = _directory_inventory(source_root, "source root")
    package_after = entries_after.get("equipment_quality")
    selected_after = {
        filename: entries_after.get(filename)
        for filename in selected_before
    }
    if package_after is None or package_after[2] != stat.S_IFDIR:
        raise ProducerSealError("equipment_quality source directory changed")
    inventory_after = _source_inventory(
        source_root,
        source_root / "equipment_quality",
        package_after,
    )
    if (
        root_before != root_after
        or entries_before != entries_after
        or selected_before != selected_after
        or package_before != package_after
        or inventory_before != inventory_after
    ):
        raise ProducerSealError("producer source changed while being attested")
    return root_after[:3], selected_before, inventory_before


def _recheck_source_attestation(
    source_root: Path,
    expected: _SourceAttestation,
) -> None:
    if _source_attestation(source_root) != expected:
        raise ProducerSealError("producer source changed after authentication")


def _source_snapshot(source_root: Path) -> dict[str, bytes]:
    _require_directory(source_root, "source root")
    package_root = source_root / "equipment_quality"
    root_before, root_entries_before = _directory_inventory(source_root, "source root")
    pyproject_fingerprint = root_entries_before.get("pyproject.toml")
    package_fingerprint = root_entries_before.get("equipment_quality")
    if pyproject_fingerprint is None or pyproject_fingerprint[2] != stat.S_IFREG:
        raise ProducerSealError("pyproject.toml must be a regular file")
    if package_fingerprint is None or package_fingerprint[2] != stat.S_IFDIR:
        raise ProducerSealError("equipment_quality source directory is unavailable")
    inventory_before = _source_inventory(
        source_root,
        package_root,
        package_fingerprint,
    )
    snapshot = {
        "pyproject.toml": _read_regular_once(
            source_root / "pyproject.toml",
            "pyproject.toml",
            pyproject_fingerprint,
        )
    }
    for relative, entry_fingerprint in inventory_before.items():
        if entry_fingerprint[2] == stat.S_IFDIR:
            continue
        if relative == _PROVENANCE_PATH:
            raise ProducerSealError("generated provenance must not exist in source")
        if _excluded_source(relative):
            continue
        snapshot[relative] = _read_regular_once(
            source_root.joinpath(*PurePosixPath(relative).parts),
            "producer source file",
            entry_fingerprint,
        )
    root_after, root_entries_after = _directory_inventory(source_root, "source root")
    inventory_after = _source_inventory(
        source_root,
        package_root,
        package_fingerprint,
    )
    if (
        root_before != root_after
        or root_entries_before != root_entries_after
        or inventory_before != inventory_after
    ):
        raise ProducerSealError("producer source inventory changed while reading")
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
    if project.get("name") != "sfep-equipment-quality" or project.get("version") != "1.1.0":
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
    root_before, entries_before = _directory_inventory(source_root, "source root")
    authenticated: dict[str, bytes] = {}
    for filename, group in _LOCK_FILES.items():
        expected = entries_before.get(filename)
        if expected is None:
            raise ProducerSealError(f"{filename} is unavailable")
        payload = _read_regular_once(source_root / filename, filename, expected)
        if payload != _expected_lock_bytes(group):
            raise ProducerSealError(f"{filename} bytes do not match the frozen lock")
        authenticated[filename] = payload
    root_after, entries_after = _directory_inventory(source_root, "source root")
    if root_before != root_after or entries_before != entries_after:
        raise ProducerSealError("source root lock inventory changed while reading")
    return authenticated


def _read_source_root_file(source_root: Path, filename: str, label: str) -> bytes:
    root_before, entries_before = _directory_inventory(source_root, "source root")
    expected = entries_before.get(filename)
    if expected is None:
        raise ProducerSealError(f"{label} is unavailable")
    payload = _read_regular_once(source_root / filename, label, expected)
    root_after, entries_after = _directory_inventory(source_root, "source root")
    if root_before != root_after or entries_before != entries_after:
        raise ProducerSealError("source root inventory changed while reading")
    return payload


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


def _authenticate_wheelhouse(
    source_root: Path,
    wheelhouse: Path,
    expected_directory_identity: tuple[int, int],
) -> _DirectoryInventory:
    lock_payload = _read_source_root_file(
        source_root,
        "wheelhouse.lock.json",
        "wheelhouse lock",
    )
    lock = _strict_canonical_json(lock_payload, "wheelhouse lock")
    if lock != _expected_wheelhouse_lock():
        raise ProducerSealError("wheelhouse lock does not match the frozen inventory")

    wheelhouse_before, entries = _directory_inventory(wheelhouse, "wheelhouse")
    if wheelhouse_before[:2] != expected_directory_identity:
        raise ProducerSealError("wheelhouse identity changed before authentication")
    expected_names = {item[2] for item in _THIRD_PARTY}
    actual_names = set(entries)
    if actual_names - {_PRODUCER_FILENAME} != expected_names:
        raise ProducerSealError("wheelhouse inventory is missing or has extra files")
    if actual_names > expected_names | {_PRODUCER_FILENAME}:
        raise ProducerSealError("wheelhouse inventory is missing or has extra files")
    expected_by_filename = {item[2]: item for item in _THIRD_PARTY}
    seen_normalized: set[str] = set()
    for filename in sorted(expected_names, key=lambda item: item.encode("utf-8")):
        expected_fingerprint = entries[filename]
        if expected_fingerprint[2] == stat.S_IFLNK:
            raise ProducerSealError("wheelhouse wheel must not be a symlink")
        if expected_fingerprint[2] != stat.S_IFREG:
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
        payload = _read_regular_once(
            wheelhouse / filename,
            "wheelhouse wheel",
            expected_fingerprint,
        )
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ProducerSealError("wheelhouse wheel digest does not match the lock")
    wheelhouse_after, entries_after = _directory_inventory(wheelhouse, "wheelhouse")
    if wheelhouse_before != wheelhouse_after or entries != entries_after:
        raise ProducerSealError("wheelhouse inventory changed while reading")
    return wheelhouse_before, entries


def _read_wheel_directory(
    directory: Path,
    label: str,
    expected_directory_identity: tuple[int, int],
) -> tuple[bytes, tuple[int, int], _DirectoryInventory]:
    directory_before, entries = _directory_inventory(directory, label)
    if directory_before[:2] != expected_directory_identity:
        raise ProducerSealError(f"{label} changed while reading")
    if len(entries) != 1:
        raise ProducerSealError(f"{label} must contain exactly one wheel")
    if set(entries) != {_PRODUCER_FILENAME}:
        raise ProducerSealError(f"{label} does not contain the expected producer wheel")
    wheel_fingerprint = entries[_PRODUCER_FILENAME]
    if wheel_fingerprint[2] != stat.S_IFREG:
        raise ProducerSealError(f"{label} wheel must be a non-symlink regular file")
    payload = _read_regular_once(
        directory / _PRODUCER_FILENAME,
        f"{label} wheel",
        wheel_fingerprint,
    )
    directory_after, entries_after = _directory_inventory(directory, label)
    if directory_before != directory_after or entries != entries_after:
        raise ProducerSealError(f"{label} changed while reading")
    return payload, wheel_fingerprint[:2], (directory_before, entries)


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
    _validate_email_field(members[_METADATA_PATH], "METADATA", "Version", "1.1.0")
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


def _raw_zip_layout(payload: bytes) -> list[dict[str, int | bytes]]:
    if len(payload) < _END_RECORD.size:
        raise ProducerSealError("producer ZIP end record is missing")
    end_offset = len(payload) - _END_RECORD.size
    end_signature = struct.pack("<I", _END_SIGNATURE)
    if payload[end_offset:end_offset + 4] != end_signature:
        candidate = payload.rfind(
            end_signature,
            max(0, len(payload) - (_END_RECORD.size + 0xFFFF)),
        )
        if candidate >= 0 and candidate + _END_RECORD.size <= len(payload):
            comment_size = struct.unpack_from("<H", payload, candidate + 20)[0]
            if comment_size and candidate + _END_RECORD.size + comment_size == len(payload):
                raise ProducerSealError("producer ZIP archive comment is forbidden")
        raise ProducerSealError("producer ZIP has trailing data or a noncanonical end record")
    try:
        end = _END_RECORD.unpack_from(payload, end_offset)
    except struct.error as error:
        raise ProducerSealError("producer ZIP end record is malformed") from error
    (
        signature,
        disk_number,
        central_disk,
        disk_entries,
        total_entries,
        central_size,
        central_offset,
        comment_size,
    ) = end
    if signature != _END_SIGNATURE:
        raise ProducerSealError("producer ZIP has trailing data or a noncanonical end record")
    if comment_size:
        raise ProducerSealError("producer ZIP archive comment is forbidden")
    if disk_number or central_disk or disk_entries != total_entries:
        raise ProducerSealError("producer ZIP multi-disk layout is forbidden")
    central_end = central_offset + central_size
    if central_end != end_offset or central_offset > end_offset:
        raise ProducerSealError("producer ZIP central directory has unclaimed bytes")

    records: list[dict[str, int | bytes]] = []
    cursor = central_offset
    for _index in range(total_entries):
        if cursor + _CENTRAL_HEADER.size > central_end:
            raise ProducerSealError("producer ZIP central directory is truncated")
        try:
            central = _CENTRAL_HEADER.unpack_from(payload, cursor)
        except struct.error as error:
            raise ProducerSealError("producer ZIP central entry is malformed") from error
        if central[0] != _CENTRAL_SIGNATURE:
            raise ProducerSealError("producer ZIP central entry signature is invalid")
        (
            _signature,
            create_version,
            extract_version,
            flags,
            method,
            dos_time,
            dos_date,
            crc,
            compressed_size,
            uncompressed_size,
            filename_size,
            extra_size,
            member_comment_size,
            disk_start,
            internal_attr,
            external_attr,
            local_offset,
        ) = central
        variable_start = cursor + _CENTRAL_HEADER.size
        variable_end = variable_start + filename_size + extra_size + member_comment_size
        if variable_end > central_end:
            raise ProducerSealError("producer ZIP central entry exceeds its directory")
        filename = payload[variable_start:variable_start + filename_size]
        extra_start = variable_start + filename_size
        extra = payload[extra_start:extra_start + extra_size]
        member_comment = payload[extra_start + extra_size:variable_end]
        if not filename:
            raise ProducerSealError("producer ZIP central filename is empty")
        try:
            decoded_filename = filename.decode("ascii", errors="strict")
        except UnicodeError as error:
            raise ProducerSealError("producer ZIP central filename is not ASCII") from error
        if flags != 0:
            raise ProducerSealError(
                "producer ZIP encryption, data descriptors, and flags are forbidden"
            )
        if method not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ProducerSealError("producer ZIP central compression method is invalid")
        if dos_time != _DOS_EPOCH_TIME or dos_date != _DOS_EPOCH_DATE:
            raise ProducerSealError("producer ZIP member timestamp is invalid")
        if extra or member_comment:
            raise ProducerSealError("producer ZIP central extra/comment data is forbidden")
        if disk_start:
            raise ProducerSealError("producer ZIP central disk reference is invalid")
        if create_version != 0x0314 or extract_version != 20:
            raise ProducerSealError("producer ZIP central version fields are invalid")
        if decoded_filename.endswith("/"):
            raise ProducerSealError("producer ZIP directory members are forbidden")
        if internal_attr != 0 or external_attr & 0xFFFF:
            raise ProducerSealError("producer ZIP central attributes are invalid")
        expected_mode = 0o664 if decoded_filename == _RECORD_PATH else 0o644
        if external_attr >> 16 != stat.S_IFREG | expected_mode:
            raise ProducerSealError("producer ZIP member mode/attributes are invalid")
        records.append({
            "filename": filename,
            "flags": flags,
            "method": method,
            "dos_time": dos_time,
            "dos_date": dos_date,
            "crc": crc,
            "compressed_size": compressed_size,
            "uncompressed_size": uncompressed_size,
            "extra": extra,
            "local_offset": local_offset,
            "create_version": create_version,
            "extract_version": extract_version,
            "internal_attr": internal_attr,
            "external_attr": external_attr,
        })
        cursor = variable_end
    if cursor != central_end:
        raise ProducerSealError("producer ZIP central directory has unclaimed bytes")

    expected_local_offset = 0
    for record in records:
        local_offset = int(record["local_offset"])
        if local_offset != expected_local_offset:
            raise ProducerSealError("producer ZIP local data has unclaimed bytes")
        if local_offset + _LOCAL_HEADER.size > central_offset:
            raise ProducerSealError("producer ZIP local header is truncated")
        try:
            local = _LOCAL_HEADER.unpack_from(payload, local_offset)
        except struct.error as error:
            raise ProducerSealError("producer ZIP local header is malformed") from error
        if local[0] != _LOCAL_SIGNATURE:
            raise ProducerSealError("producer ZIP local header signature is invalid")
        (
            _signature,
            extract_version,
            flags,
            method,
            dos_time,
            dos_date,
            crc,
            compressed_size,
            uncompressed_size,
            filename_size,
            extra_size,
        ) = local
        variable_start = local_offset + _LOCAL_HEADER.size
        data_start = variable_start + filename_size + extra_size
        data_end = data_start + compressed_size
        if data_end > central_offset:
            raise ProducerSealError("producer ZIP local data exceeds the central directory")
        filename = payload[variable_start:variable_start + filename_size]
        extra = payload[variable_start + filename_size:data_start]
        compared = (
            filename,
            flags,
            method,
            dos_time,
            dos_date,
            crc,
            compressed_size,
            uncompressed_size,
            extra,
            extract_version,
        )
        expected = (
            record["filename"],
            record["flags"],
            record["method"],
            record["dos_time"],
            record["dos_date"],
            record["crc"],
            record["compressed_size"],
            record["uncompressed_size"],
            record["extra"],
            record["extract_version"],
        )
        if compared != expected:
            raise ProducerSealError("producer ZIP local header does not match central entry")
        compressed = payload[data_start:data_end]
        if method == zipfile.ZIP_STORED:
            member = compressed
        elif method == zipfile.ZIP_DEFLATED:
            decoder = zlib.decompressobj(-zlib.MAX_WBITS)
            try:
                member = decoder.decompress(compressed) + decoder.flush()
            except zlib.error as error:
                raise ProducerSealError(
                    "producer ZIP DEFLATE stream is malformed"
                ) from error
            if not decoder.eof:
                raise ProducerSealError("producer ZIP DEFLATE stream is incomplete")
            if decoder.unused_data or decoder.unconsumed_tail:
                raise ProducerSealError(
                    "producer ZIP DEFLATE stream has unconsumed compressed bytes"
                )
        else:  # pragma: no cover - the central-directory gate is fail-closed.
            raise ProducerSealError("producer ZIP compression method is unsupported")
        if len(member) != uncompressed_size:
            raise ProducerSealError("producer ZIP raw member size is invalid")
        if zlib.crc32(member) & 0xFFFFFFFF != crc:
            raise ProducerSealError("producer ZIP raw member CRC is invalid")
        expected_local_offset = data_end
    if expected_local_offset != central_offset:
        raise ProducerSealError("producer ZIP local data has unclaimed bytes")
    return records


def _verify_producer_wheel(
    payload: bytes,
    snapshot: dict[str, bytes],
    expected_provenance: bytes,
    forbidden_roots: tuple[Path, ...],
) -> None:
    raw_records = _raw_zip_layout(payload)
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if archive.comment:
                raise ProducerSealError("producer ZIP archive comment is forbidden")
            infos = archive.infolist()
            if not infos:
                raise ProducerSealError("producer ZIP archive is empty")
            if len(infos) != len(raw_records):
                raise ProducerSealError("producer ZIP central entry count is inconsistent")
            names: set[str] = set()
            members: dict[str, bytes] = {}
            for info, raw in zip(infos, raw_records, strict=True):
                name = info.filename
                if (
                    raw["filename"] != name.encode("ascii", errors="strict")
                    or raw["local_offset"] != info.header_offset
                    or raw["crc"] != info.CRC
                    or raw["compressed_size"] != info.compress_size
                    or raw["uncompressed_size"] != info.file_size
                    or raw["external_attr"] != info.external_attr
                    or raw["internal_attr"] != info.internal_attr
                    or int(raw["create_version"]) & 0xFF != info.create_version
                    or int(raw["create_version"]) >> 8 != info.create_system
                    or raw["extract_version"] != info.extract_version
                ):
                    raise ProducerSealError("producer ZIP raw central entry is inconsistent")
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
        "sfep-equipment-quality==1.1.0 "
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


def _publish_no_clobber_paths(
    target_wheel: Path,
    producer_lock: Path,
    wheel: bytes,
    lock: bytes,
) -> None:
    wheel_temp: Path | None = None
    lock_temp: Path | None = None
    wheel_created = False
    lock_created = False
    try:
        wheel_temp = _durable_temp(target_wheel.parent, wheel)
        lock_temp = _durable_temp(producer_lock.parent, lock)
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
        if wheel_temp is not None:
            wheel_temp.unlink(missing_ok=True)
        if lock_temp is not None:
            lock_temp.unlink(missing_ok=True)


def _durable_temp_at(
    parent_descriptor: int,
    payload: bytes,
) -> tuple[str, int, os.stat_result]:
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    name = ""
    for _attempt in range(128):
        name = ".sfep-producer-" + secrets.token_hex(8)
        try:
            descriptor = os.open(
                name,
                flags,
                0o600,
                dir_fd=parent_descriptor,
            )
        except FileExistsError:
            continue
        break
    if descriptor is None:
        raise ProducerSealError("producer temporary output name could not be reserved")
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ProducerSealError("producer output could not be written")
            view = view[written:]
        os.fsync(descriptor)
        result = os.fstat(descriptor)
    except Exception:
        try:
            current = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            opened = os.fstat(descriptor)
            if _identity(current) == _identity(opened):
                os.unlink(name, dir_fd=parent_descriptor)
        except OSError:
            pass
        os.close(descriptor)
        raise
    return name, descriptor, result


def _authenticate_open_temp(
    parent_descriptor: int,
    temp_name: str,
    target_name: str,
    descriptor: int,
    initial: os.stat_result,
    expected: bytes,
    label: str,
) -> _Fingerprint:
    try:
        before = os.fstat(descriptor)
        if (
            _identity(before) != _identity(initial)
            or stat.S_IFMT(before.st_mode) != stat.S_IFREG
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != initial.st_size
            or before.st_mtime_ns != initial.st_mtime_ns
            or before.st_nlink != initial.st_nlink + 1
        ):
            raise ProducerSealError(f"{label} temporary inode changed after linking")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_read = os.fstat(descriptor)
        if (
            _fingerprint(before) != _fingerprint(after_read)
            or b"".join(chunks) != expected
            or after_read.st_size != len(expected)
        ):
            raise ProducerSealError(f"{label} temporary payload changed after linking")
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
        before_final_read = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        verified = bytearray()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            verified.extend(chunk)
        final = os.fstat(descriptor)
        if (
            _fingerprint(before_final_read) != _fingerprint(final)
            or _identity(final) != _identity(initial)
            or stat.S_IFMT(final.st_mode) != stat.S_IFREG
            or stat.S_IMODE(final.st_mode) != 0o644
            or final.st_size != len(expected)
            or final.st_mtime_ns != initial.st_mtime_ns
            or final.st_nlink != initial.st_nlink + 1
            or bytes(verified) != expected
        ):
            raise ProducerSealError(f"{label} temporary payload is not authenticated")
        fingerprint = _fingerprint(final)
        for name in (temp_name, target_name):
            current = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if _fingerprint(current) != fingerprint:
                raise ProducerSealError(f"{label} published link identity changed")
        return fingerprint
    except ProducerSealError:
        raise
    except OSError as error:
        raise ProducerSealError(f"{label} temporary output cannot be authenticated") from error


def _unlink_owned_name(
    parent_descriptor: int,
    name: str,
    owner_descriptor: int,
) -> bool:
    try:
        current = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        owner = os.fstat(owner_descriptor)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    if _identity(current) != _identity(owner):
        return False
    try:
        os.unlink(name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except OSError:
        return False
    return True


def _cleanup_open_temp(
    parent_descriptor: int,
    name: str | None,
    descriptor: int | None,
) -> bool:
    if name is None or descriptor is None:
        return True
    cleaned = _unlink_owned_name(parent_descriptor, name, descriptor)
    os.close(descriptor)
    return cleaned


def _authenticate_final_target(
    parent_descriptor: int,
    target_name: str,
    descriptor: int,
    expected: bytes,
    label: str,
) -> _Fingerprint:
    try:
        before = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        current = os.stat(
            target_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        raise ProducerSealError(f"{label} final output cannot be authenticated") from error
    if (
        _fingerprint(before) != _fingerprint(after)
        or _fingerprint(current) != _fingerprint(after)
        or stat.S_IFMT(after.st_mode) != stat.S_IFREG
        or stat.S_IMODE(after.st_mode) != 0o644
        or after.st_nlink != 1
        or after.st_size != len(expected)
        or b"".join(chunks) != expected
    ):
        raise ProducerSealError(f"{label} final payload or identity changed")
    return _fingerprint(after)


def _publish_no_clobber_at(
    target_wheel_name: str,
    producer_lock_name: str,
    wheel: bytes,
    lock: bytes,
    wheelhouse_descriptor: int,
    lock_parent_descriptor: int,
    expected_wheelhouse_entries: dict[str, _Fingerprint],
    expected_lock_parent_entries: dict[str, _Fingerprint],
    final_check: Callable[[str, _Fingerprint, str, _Fingerprint], None],
    post_cleanup_check: Callable[[_Fingerprint, _Fingerprint], None],
) -> None:
    wheel_temp: str | None = None
    lock_temp: str | None = None
    wheel_temp_descriptor: int | None = None
    lock_temp_descriptor: int | None = None
    wheel_created = False
    lock_created = False
    try:
        wheel_temp, wheel_temp_descriptor, wheel_initial = _durable_temp_at(
            wheelhouse_descriptor,
            wheel,
        )
        lock_temp, lock_temp_descriptor, lock_initial = _durable_temp_at(
            lock_parent_descriptor,
            lock,
        )
        try:
            os.link(
                wheel_temp,
                target_wheel_name,
                src_dir_fd=wheelhouse_descriptor,
                dst_dir_fd=wheelhouse_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise ProducerSealError(
                "existing producer wheel appeared during publication"
            ) from error
        wheel_created = True
        os.fsync(wheelhouse_descriptor)
        try:
            os.link(
                lock_temp,
                producer_lock_name,
                src_dir_fd=lock_parent_descriptor,
                dst_dir_fd=lock_parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise ProducerSealError(
                "existing producer lock appeared during publication"
            ) from error
        lock_created = True
        os.fsync(lock_parent_descriptor)
        wheel_fingerprint = _authenticate_open_temp(
            wheelhouse_descriptor,
            wheel_temp,
            target_wheel_name,
            wheel_temp_descriptor,
            wheel_initial,
            wheel,
            "producer wheel",
        )
        lock_fingerprint = _authenticate_open_temp(
            lock_parent_descriptor,
            lock_temp,
            producer_lock_name,
            lock_temp_descriptor,
            lock_initial,
            lock,
            "producer lock",
        )
        final_check(wheel_temp, wheel_fingerprint, lock_temp, lock_fingerprint)
    except Exception as error:
        collision = False
        if lock_created:
            if lock_temp_descriptor is None or not _unlink_owned_name(
                lock_parent_descriptor,
                producer_lock_name,
                lock_temp_descriptor,
            ):
                collision = True
        if wheel_created:
            if wheel_temp_descriptor is None or not _unlink_owned_name(
                wheelhouse_descriptor,
                target_wheel_name,
                wheel_temp_descriptor,
            ):
                collision = True
        if not _cleanup_open_temp(
            wheelhouse_descriptor,
            wheel_temp,
            wheel_temp_descriptor,
        ):
            collision = True
        if not _cleanup_open_temp(
            lock_parent_descriptor,
            lock_temp,
            lock_temp_descriptor,
        ):
            collision = True
        if collision:
            raise ProducerSealError(
                "producer publication changed during rollback collision"
            ) from error
        raise
    try:
        if (
            wheel_temp is None
            or lock_temp is None
            or wheel_temp_descriptor is None
            or lock_temp_descriptor is None
        ):
            raise ProducerSealError("producer temporary publication state is incomplete")
        wheel_cleaned = _unlink_owned_name(
            wheelhouse_descriptor,
            wheel_temp,
            wheel_temp_descriptor,
        )
        lock_cleaned = _unlink_owned_name(
            lock_parent_descriptor,
            lock_temp,
            lock_temp_descriptor,
        )
        if not wheel_cleaned or not lock_cleaned:
            raise ProducerSealError("producer temporary cleanup changed")
        final_wheel_fingerprint = _authenticate_final_target(
            wheelhouse_descriptor,
            target_wheel_name,
            wheel_temp_descriptor,
            wheel,
            "producer wheel",
        )
        final_lock_fingerprint = _authenticate_final_target(
            lock_parent_descriptor,
            producer_lock_name,
            lock_temp_descriptor,
            lock,
            "producer lock",
        )
        post_cleanup_check(final_wheel_fingerprint, final_lock_fingerprint)
    except Exception as error:
        rollback_collision = False
        if (
            lock_temp_descriptor is None
            or not _unlink_owned_name(
                lock_parent_descriptor,
                producer_lock_name,
                lock_temp_descriptor,
            )
        ):
            rollback_collision = True
        if (
            wheel_temp_descriptor is None
            or not _unlink_owned_name(
                wheelhouse_descriptor,
                target_wheel_name,
                wheel_temp_descriptor,
            )
        ):
            rollback_collision = True
        if wheel_temp_descriptor is not None:
            os.close(wheel_temp_descriptor)
        if lock_temp_descriptor is not None:
            os.close(lock_temp_descriptor)
        if rollback_collision:
            raise ProducerSealError(
                "producer final publication changed with rollback collision"
            ) from error
        raise
    os.close(wheel_temp_descriptor)
    os.close(lock_temp_descriptor)


def _publish_no_clobber(
    target_wheel: Path,
    producer_lock: Path,
    wheel: bytes,
    lock: bytes,
    *,
    wheelhouse_descriptor: int | None = None,
    lock_parent_descriptor: int | None = None,
    expected_wheelhouse_entries: dict[str, _Fingerprint] | None = None,
    expected_lock_parent_entries: dict[str, _Fingerprint] | None = None,
    final_check: Callable[[str, _Fingerprint, str, _Fingerprint], None] | None = None,
    post_cleanup_check: Callable[[_Fingerprint, _Fingerprint], None] | None = None,
) -> None:
    if wheelhouse_descriptor is None and lock_parent_descriptor is None:
        _publish_no_clobber_paths(target_wheel, producer_lock, wheel, lock)
        return
    if (
        wheelhouse_descriptor is None
        or lock_parent_descriptor is None
        or expected_wheelhouse_entries is None
        or expected_lock_parent_entries is None
        or final_check is None
        or post_cleanup_check is None
    ):
        raise ProducerSealError("pinned publication arguments are incomplete")
    _publish_no_clobber_at(
        target_wheel.name,
        producer_lock.name,
        wheel,
        lock,
        wheelhouse_descriptor,
        lock_parent_descriptor,
        expected_wheelhouse_entries,
        expected_lock_parent_entries,
        final_check,
        post_cleanup_check,
    )


def _expected_provenance(snapshot: dict[str, bytes], requirements: bytes) -> bytes:
    return _canonical_json_bytes({
        "schemaVersion": "sfep-producer-provenance/v1",
        "producerName": "equipment-quality",
        "distributionName": "sfep-equipment-quality",
        "version": "1.1.0",
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
    topology = _validate_path_topology(
        source_root,
        wheel_dir_a,
        wheel_dir_b,
        wheelhouse,
        producer_lock,
    )
    wheel_dir_a_identity = topology[0][2]
    wheel_dir_b_identity = topology[1][2]
    wheelhouse_identity = topology[2][2]
    source_attestation = _source_attestation(source_root)
    snapshot = _source_snapshot(source_root)
    _recheck_source_attestation(source_root, source_attestation)
    _validate_pyproject(snapshot["pyproject.toml"])
    locks = _authenticate_locks(source_root)
    _recheck_source_attestation(source_root, source_attestation)
    wheelhouse_inventory = _authenticate_wheelhouse(
        source_root,
        wheelhouse,
        wheelhouse_identity,
    )
    wheel_a, wheel_a_identity, wheel_dir_a_inventory = _read_wheel_directory(
        wheel_dir_a,
        "wheel directory A",
        wheel_dir_a_identity,
    )
    wheel_b, wheel_b_identity, wheel_dir_b_inventory = _read_wheel_directory(
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
    _recheck_source_attestation(source_root, source_attestation)
    lock = _producer_lock_bytes(wheel_a)
    target_wheel = wheelhouse / _PRODUCER_FILENAME
    lock_parent_inventory = _directory_inventory(
        producer_lock.parent,
        "producer lock parent",
    )
    if lock_parent_inventory[0][:2] != topology[3][2]:
        raise ProducerSealError("producer lock parent identity changed")
    reused = _preflight_outputs(target_wheel, producer_lock, wheel_a, lock)
    inventories = (
        wheel_dir_a_inventory,
        wheel_dir_b_inventory,
        wheelhouse_inventory,
        lock_parent_inventory,
    )
    publication_descriptors = _recheck_path_topology(topology, inventories)
    try:
        _recheck_source_attestation(source_root, source_attestation)
        if not reused:
            labels = (
                "wheel directory A",
                "wheel directory B",
                "wheelhouse",
                "producer lock parent",
            )

            def check_publication_inventories(
                wheelhouse_additions: dict[str, _Fingerprint],
                lock_parent_additions: dict[str, _Fingerprint],
            ) -> None:
                _recheck_source_attestation(source_root, source_attestation)
                for pin, label in zip(topology, labels, strict=True):
                    _recheck_directory_pin(pin, label)
                for descriptor, expected_inventory, label in zip(
                    publication_descriptors[:2],
                    inventories[:2],
                    labels[:2],
                    strict=True,
                ):
                    if _directory_inventory_fd(descriptor, label) != expected_inventory:
                        raise ProducerSealError(
                            f"{label} changed during publication"
                        )
                _wheelhouse, wheelhouse_entries = _directory_inventory_fd(
                    publication_descriptors[2],
                    "wheelhouse",
                )
                expected_wheelhouse = dict(wheelhouse_inventory[1])
                expected_wheelhouse.update(wheelhouse_additions)
                if wheelhouse_entries != expected_wheelhouse:
                    raise ProducerSealError("wheelhouse changed during publication")
                _lock_parent, lock_parent_entries = _directory_inventory_fd(
                    publication_descriptors[3],
                    "producer lock parent",
                )
                expected_lock_parent = dict(lock_parent_inventory[1])
                wheelhouse_directory_fingerprint = _fingerprint(
                    os.fstat(publication_descriptors[2])
                )
                for name, expected_entry in tuple(expected_lock_parent.items()):
                    if expected_entry[:2] == wheelhouse_directory_fingerprint[:2]:
                        if expected_entry[2] != wheelhouse_directory_fingerprint[2]:
                            raise ProducerSealError(
                                "wheelhouse type changed during publication"
                            )
                        expected_lock_parent[name] = wheelhouse_directory_fingerprint
                expected_lock_parent.update(lock_parent_additions)
                if lock_parent_entries != expected_lock_parent:
                    raise ProducerSealError(
                        "producer lock parent changed during publication"
                    )
                for pin, label in zip(topology, labels, strict=True):
                    _recheck_directory_pin(pin, label)
                _recheck_source_attestation(source_root, source_attestation)

            def final_check(
                wheel_temp: str,
                wheel_fingerprint: _Fingerprint,
                lock_temp: str,
                lock_fingerprint: _Fingerprint,
            ) -> None:
                check_publication_inventories(
                    {
                        wheel_temp: wheel_fingerprint,
                        target_wheel.name: wheel_fingerprint,
                    },
                    {
                        lock_temp: lock_fingerprint,
                        producer_lock.name: lock_fingerprint,
                    },
                )

            def post_cleanup_check(
                wheel_fingerprint: _Fingerprint,
                lock_fingerprint: _Fingerprint,
            ) -> None:
                check_publication_inventories(
                    {target_wheel.name: wheel_fingerprint},
                    {producer_lock.name: lock_fingerprint},
                )

            _publish_no_clobber(
                target_wheel,
                producer_lock,
                wheel_a,
                lock,
                wheelhouse_descriptor=publication_descriptors[2],
                lock_parent_descriptor=publication_descriptors[3],
                expected_wheelhouse_entries=wheelhouse_inventory[1],
                expected_lock_parent_entries=lock_parent_inventory[1],
                final_check=final_check,
                post_cleanup_check=post_cleanup_check,
            )
        else:
            _recheck_source_attestation(source_root, source_attestation)
    finally:
        for descriptor in publication_descriptors:
            os.close(descriptor)
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
