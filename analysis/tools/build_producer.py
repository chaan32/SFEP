#!/usr/bin/env python3
"""Build the producer wheel twice from one frozen source snapshot."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import tomllib


_SOURCE_DATE_EPOCH = 1735689600
_SOURCE_HEADER = b"sfep-source-lines/v1\n"
_PROVENANCE_PATH = "equipment_quality/sfep_producer_provenance.json"
_PRODUCER_FILENAME = "sfep_equipment_quality-1.0.0-py3-none-any.whl"
_RUNTIME_VERSIONS = {
    "attrs": "26.1.0",
    "jsonschema": "4.24.0",
    "jsonschema-specifications": "2025.9.1",
    "numpy": "2.2.6",
    "pandas": "2.3.0",
    "python-dateutil": "2.9.0.post0",
    "pytz": "2026.3.post1",
    "referencing": "0.37.0",
    "rpds-py": "2026.6.3",
    "six": "1.17.0",
    "typing-extensions": "4.16.0",
    "tzdata": "2026.3",
}
_LOCK_LINE = re.compile(
    rb"([a-z0-9]+(?:-[a-z0-9]+)*)==([^\x00-\x20]+) "
    rb"--hash=sha256:([0-9a-f]{64})\n"
)
_Fingerprint = tuple[int, int, int, int, int, int]
_DirectoryInventory = tuple[_Fingerprint, dict[str, _Fingerprint]]
_DirectoryPin = tuple[Path, Path, tuple[int, int]]
_OutputPin = tuple[int, tuple[int, int]]
_OutputPins = dict[str, _OutputPin]
_SourceAttestation = tuple[
    tuple[int, int, int],
    dict[str, _Fingerprint],
    dict[str, _Fingerprint],
]


class ProducerBuildError(RuntimeError):
    """The producer build request is unsafe or could not be completed."""


@dataclass
class _OwnedOutput:
    primary_descriptor: int | None
    cleanup_descriptor: int | None = None
    owner_identity: tuple[int, int] | None = None
    expected_fingerprint: _Fingerprint | None = None
    primary_close_attempted: bool = False
    cleanup_close_attempted: bool = False


def _descriptor_identity_status(
    descriptor: int,
    expected_identity: tuple[int, int],
) -> bool | None:
    """Return whether an FD still names the owned regular file, if provable."""
    for inspect in (os.fstat, os.stat):
        try:
            opened = inspect(descriptor)
        except OSError as error:
            if error.errno == errno.EBADF:
                return False
            continue
        except BaseException:
            continue
        return stat.S_ISREG(opened.st_mode) and _identity(opened) == expected_identity
    return None


def _close_best_effort(descriptors: Iterable[int | None]) -> None:
    """Attempt every distinct numeric FD once and never mask the real result."""
    seen: set[int] = set()
    for value in descriptors:
        if value is None:
            continue
        descriptor = int(value)
        if descriptor in seen:
            continue
        seen.add(descriptor)
        try:
            os.close(descriptor)
        except BaseException:
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--build-python", required=True)
    parser.add_argument("--work-root", required=True)
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
        raise ProducerBuildError(f"{label} path contains a control character")
    path = Path(raw)
    if not path.is_absolute():
        raise ProducerBuildError(f"{label} path must be absolute")
    if ".." in path.parts:
        raise ProducerBuildError(f"{label} path must not contain a parent segment")
    return path


def _require_directory(path: Path, label: str) -> os.stat_result:
    try:
        result = os.lstat(path)
    except OSError as error:
        raise ProducerBuildError(f"{label} is unavailable") from error
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISDIR(result.st_mode):
        raise ProducerBuildError(f"{label} must be a non-symlink directory")
    return result


def _require_build_python(path: Path) -> None:
    try:
        result = os.lstat(path)
    except OSError as error:
        raise ProducerBuildError("build Python is unavailable") from error
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISREG(result.st_mode):
        raise ProducerBuildError("build Python must be a non-symlink regular file")
    if not os.access(path, os.X_OK):
        raise ProducerBuildError("build Python must be executable")


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
            raise ProducerBuildError("input path ancestry cannot be inspected") from error
        if result is not None:
            identity = result.st_dev, result.st_ino
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


def _reject_symlink_ancestry(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            result = os.lstat(current)
        except OSError as error:
            raise ProducerBuildError(f"{label} is unavailable") from error
        if stat.S_ISLNK(result.st_mode) or not stat.S_ISDIR(result.st_mode):
            raise ProducerBuildError(
                f"{label} must have only non-symlink directory components"
            )


def _prepare_work_root(
    path: Path,
    source_root: Path,
) -> tuple[int, _DirectoryPin, _OutputPins]:
    source_physical = Path(os.path.realpath(source_root))
    work_physical = Path(os.path.realpath(path))
    if _paths_overlap(source_physical, work_physical):
        raise ProducerBuildError("source root and work root must not overlap")
    _reject_symlink_ancestry(path, "work root")
    source_identity, source_ancestry = _path_ancestry(source_physical)
    if source_identity is None:
        raise ProducerBuildError("source root identity is unavailable")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    opened_descriptors: list[int] = []
    try:
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise ProducerBuildError(
                "work root cannot be opened safely as a prepared directory"
            ) from error
        opened_descriptors.append(descriptor)
        try:
            opened = os.fstat(descriptor)
            path_result = os.lstat(path)
        except OSError as error:
            raise ProducerBuildError(
                "work root initial pin authentication failed"
            ) from error
        work_identity = _identity(opened)
        current_physical = Path(os.path.realpath(path))
        if (
            stat.S_ISLNK(path_result.st_mode)
            or not stat.S_ISDIR(path_result.st_mode)
            or not stat.S_ISDIR(opened.st_mode)
            or _identity(path_result) != work_identity
            or current_physical != work_physical
            or work_identity in source_ancestry
            or _paths_overlap(source_physical, current_physical)
        ):
            raise ProducerBuildError("work root changed while being pinned")
        pin = path, current_physical, work_identity

        outputs: _OutputPins = {}
        for name in ("wheel-a", "wheel-b"):
            try:
                output_descriptor = os.open(
                    name,
                    flags,
                    dir_fd=descriptor,
                )
            except OSError as error:
                raise ProducerBuildError(
                    f"prepared {name} output directory is unavailable"
                ) from error
            opened_descriptors.append(output_descriptor)
            try:
                output_opened = os.fstat(output_descriptor)
                output_entry = os.lstat(name, dir_fd=descriptor)
            except OSError as error:
                raise ProducerBuildError(
                    f"prepared {name} initial pin authentication failed"
                ) from error
            output_identity = _identity(output_opened)
            if (
                not stat.S_ISDIR(output_opened.st_mode)
                or stat.S_ISLNK(output_entry.st_mode)
                or not stat.S_ISDIR(output_entry.st_mode)
                or _identity(output_entry) != output_identity
            ):
                raise ProducerBuildError(
                    f"prepared {name} output directory changed while being pinned"
                )
            if _directory_inventory_fd(
                output_descriptor,
                f"prepared {name} output directory",
            )[1]:
                raise ProducerBuildError(
                    f"prepared {name} output directory must be empty"
                )
            outputs[name] = output_descriptor, output_identity

        _recheck_prepared_root(
            descriptor,
            pin,
            outputs,
            {"wheel-a": None, "wheel-b": None},
        )
        return descriptor, pin, outputs
    except BaseException:
        _close_best_effort(reversed(opened_descriptors))
        raise


def _recheck_work_root_identity(
    descriptor: int,
    pin: _DirectoryPin,
) -> None:
    path, expected_physical, expected_identity = pin
    try:
        path_result = os.lstat(path)
        opened = os.fstat(descriptor)
    except OSError as error:
        raise ProducerBuildError("work root changed after validation") from error
    if (
        stat.S_ISLNK(path_result.st_mode)
        or not stat.S_ISDIR(path_result.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or _identity(path_result) != expected_identity
        or _identity(opened) != expected_identity
        or Path(os.path.realpath(path)) != expected_physical
    ):
        raise ProducerBuildError("work root physical identity changed")


def _recheck_prepared_root(
    descriptor: int,
    pin: _DirectoryPin,
    outputs: _OutputPins,
    expected_files: dict[str, _Fingerprint | None],
) -> None:
    _recheck_work_root_identity(descriptor, pin)
    _work, work_entries = _directory_inventory_fd(descriptor, "work root")
    if set(work_entries) != {"wheel-a", "wheel-b"}:
        raise ProducerBuildError("prepared work root inventory changed")
    for name in ("wheel-a", "wheel-b"):
        try:
            output_descriptor, expected_identity = outputs[name]
            opened = os.fstat(output_descriptor)
            entry = os.lstat(name, dir_fd=descriptor)
        except (KeyError, OSError) as error:
            raise ProducerBuildError(
                f"prepared {name} output directory changed"
            ) from error
        work_entry = work_entries[name]
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_ISLNK(entry.st_mode)
            or not stat.S_ISDIR(entry.st_mode)
            or work_entry[2] != stat.S_IFDIR
            or _identity(opened) != expected_identity
            or _identity(entry) != expected_identity
            or work_entry[:2] != expected_identity
        ):
            raise ProducerBuildError(
                f"prepared {name} output directory identity changed"
            )
        _directory, entries = _directory_inventory_fd(
            output_descriptor,
            f"prepared {name} output directory",
        )
        expected = expected_files.get(name)
        expected_entries = {} if expected is None else {_PRODUCER_FILENAME: expected}
        if entries != expected_entries:
            raise ProducerBuildError(
                f"prepared {name} output inventory changed"
            )


def _checked_relative(relative: str) -> bytes:
    if (
        not relative
        or relative.startswith("/")
        or "\\" in relative
        or any(ord(character) < 32 or ord(character) == 127 for character in relative)
    ):
        raise ProducerBuildError("producer source contains an unsafe path")
    try:
        encoded = relative.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ProducerBuildError("producer source path is not UTF-8") from error
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or parsed.as_posix() != relative or ".." in parsed.parts:
        raise ProducerBuildError("producer source contains a non-canonical path")
    return encoded


def _fingerprint(result: os.stat_result) -> _Fingerprint:
    return (
        result.st_dev,
        result.st_ino,
        stat.S_IFMT(result.st_mode),
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _identity(result: os.stat_result) -> tuple[int, int]:
    return result.st_dev, result.st_ino


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
                raise ProducerBuildError(f"{label} contains a duplicate entry")
            result = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
            entries[entry.name] = _fingerprint(result)
        after = os.fstat(descriptor)
    except OSError as error:
        raise ProducerBuildError(f"{label} changed while being inventoried") from error
    if not stat.S_ISDIR(before.st_mode) or not stat.S_ISDIR(after.st_mode):
        raise ProducerBuildError(f"{label} type changed while being inventoried")
    if _fingerprint(before) != _fingerprint(after):
        raise ProducerBuildError(f"{label} changed while being inventoried")
    return _fingerprint(before), dict(
        sorted(entries.items(), key=lambda item: item[0].encode("utf-8"))
    )


def _directory_inventory(
    path: Path,
    label: str,
) -> _DirectoryInventory:
    try:
        before = os.lstat(path)
    except OSError as error:
        raise ProducerBuildError(f"{label} is unavailable") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise ProducerBuildError(f"{label} must be a non-symlink directory")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ProducerBuildError(f"{label} cannot be opened safely") from error
    try:
        opened, entries = _directory_inventory_fd(descriptor, label)
    finally:
        _close_best_effort((descriptor,))
    try:
        path_after = os.lstat(path)
    except OSError as error:
        raise ProducerBuildError(f"{label} changed while being inventoried") from error
    if not (
        _fingerprint(before)
        == opened
        == _fingerprint(path_after)
    ):
        raise ProducerBuildError(f"{label} changed while being inventoried")
    return opened, entries


def _read_regular_once(
    path: Path,
    label: str,
    expected: _Fingerprint | None = None,
) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as error:
        raise ProducerBuildError(f"{label} is unavailable") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ProducerBuildError(f"{label} must be a non-symlink regular file")
    if expected is not None and _fingerprint(before) != expected:
        raise ProducerBuildError(f"{label} identity changed before reading")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ProducerBuildError(f"{label} cannot be opened safely") from error
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
        raise ProducerBuildError(f"{label} changed while reading") from error
    finally:
        _close_best_effort((descriptor,))
    try:
        path_after = os.lstat(path)
    except OSError as error:
        raise ProducerBuildError(f"{label} changed while reading") from error
    if (
        _fingerprint(before) != _fingerprint(opened)
        or _fingerprint(opened) != _fingerprint(after)
        or _fingerprint(after) != _fingerprint(path_after)
        or total != after.st_size
    ):
        raise ProducerBuildError(f"{label} changed while reading")
    return b"".join(chunks)


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
            raise ProducerBuildError("producer source directory identity changed")
        relative_directory = directory.relative_to(source_root).as_posix()
        _checked_relative(relative_directory)
        inventory[relative_directory] = directory_fingerprint
        for name, entry_fingerprint in entries.items():
            path = directory / name
            relative = path.relative_to(source_root).as_posix()
            _checked_relative(relative)
            if relative in inventory:
                raise ProducerBuildError("producer source contains a duplicate path")
            inventory[relative] = entry_fingerprint
            entry_type = entry_fingerprint[2]
            if entry_type == stat.S_IFLNK:
                raise ProducerBuildError("producer source must not contain a symlink")
            if entry_type == stat.S_IFDIR:
                visit(path, entry_fingerprint)
            elif entry_type != stat.S_IFREG:
                raise ProducerBuildError("producer source must contain only regular files")

    visit(package_root, expected_package)
    return dict(sorted(inventory.items(), key=lambda item: item[0].encode("utf-8")))


def _source_attestation(source_root: Path) -> _SourceAttestation:
    root_before, entries_before = _directory_inventory(source_root, "source root")
    selected_before: dict[str, _Fingerprint] = {}
    for filename in ("pyproject.toml", "requirements.lock"):
        entry = entries_before.get(filename)
        if entry is None or entry[2] != stat.S_IFREG:
            raise ProducerBuildError(f"{filename} must be a regular file")
        selected_before[filename] = entry
    package_before = entries_before.get("equipment_quality")
    if package_before is None or package_before[2] != stat.S_IFDIR:
        raise ProducerBuildError("equipment_quality source directory is unavailable")
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
        raise ProducerBuildError("equipment_quality source directory changed")
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
        raise ProducerBuildError("producer source changed while being attested")
    return root_after[:3], selected_before, inventory_before


def _recheck_source_attestation(
    source_root: Path,
    expected: _SourceAttestation,
) -> None:
    if _source_attestation(source_root) != expected:
        raise ProducerBuildError("producer source changed after authentication")


def _source_snapshot(source_root: Path) -> dict[str, bytes]:
    _require_directory(source_root, "source root")
    package_root = source_root / "equipment_quality"
    root_before, root_entries_before = _directory_inventory(source_root, "source root")
    pyproject_fingerprint = root_entries_before.get("pyproject.toml")
    package_fingerprint = root_entries_before.get("equipment_quality")
    if pyproject_fingerprint is None or pyproject_fingerprint[2] != stat.S_IFREG:
        raise ProducerBuildError("pyproject.toml must be a regular file")
    if package_fingerprint is None or package_fingerprint[2] != stat.S_IFDIR:
        raise ProducerBuildError("equipment_quality source directory is unavailable")
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
            raise ProducerBuildError("generated provenance must not exist in source")
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
        raise ProducerBuildError("producer source inventory changed while reading")
    if len(snapshot) < 2:
        raise ProducerBuildError("producer source snapshot is empty")
    return snapshot


def _read_source_root_file(source_root: Path, filename: str, label: str) -> bytes:
    root_before, entries_before = _directory_inventory(source_root, "source root")
    expected = entries_before.get(filename)
    if expected is None:
        raise ProducerBuildError(f"{label} is unavailable")
    payload = _read_regular_once(source_root / filename, label, expected)
    root_after, entries_after = _directory_inventory(source_root, "source root")
    if root_before != root_after or entries_before != entries_after:
        raise ProducerBuildError("source root inventory changed while reading")
    return payload


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
        setuptools = value["tool"]["setuptools"]
        package_data = setuptools["package-data"]["equipment_quality"]
    except (UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        raise ProducerBuildError("pyproject.toml does not define the producer") from error
    if project.get("name") != "sfep-equipment-quality" or project.get("version") != "1.0.0":
        raise ProducerBuildError("pyproject.toml producer name/version is invalid")
    if "sfep_producer_provenance.json" not in package_data:
        raise ProducerBuildError("pyproject.toml does not package provenance")


def _validate_requirements_lock(payload: bytes) -> None:
    matches = list(_LOCK_LINE.finditer(payload))
    if not matches or b"".join(match.group(0) for match in matches) != payload:
        raise ProducerBuildError("requirements.lock is not canonical")
    names = [match.group(1).decode("ascii") for match in matches]
    versions = [match.group(2).decode("ascii") for match in matches]
    if names != sorted(names, key=lambda name: name.encode("utf-8")):
        raise ProducerBuildError("requirements.lock names are not sorted")
    if len(names) != len(set(names)):
        raise ProducerBuildError("requirements.lock contains a duplicate name")
    if dict(zip(names, versions, strict=True)) != _RUNTIME_VERSIONS:
        raise ProducerBuildError("requirements.lock package inventory is invalid")


def _provenance(snapshot: dict[str, bytes], requirements: bytes) -> bytes:
    return _canonical_json_bytes({
        "schemaVersion": "sfep-producer-provenance/v1",
        "producerName": "equipment-quality",
        "distributionName": "sfep-equipment-quality",
        "version": "1.0.0",
        "sourceSha256": _source_digest(snapshot),
        "sourceDateEpoch": str(_SOURCE_DATE_EPOCH),
        "requirementsLockSha256": _sha256_uri(requirements),
    })


def _write_frozen_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ProducerBuildError("staging file could not be written")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        _close_best_effort((descriptor,))
    os.chmod(path, 0o644, follow_symlinks=False)
    os.utime(path, (_SOURCE_DATE_EPOCH, _SOURCE_DATE_EPOCH), follow_symlinks=False)


def _materialize_stage(
    stage: Path,
    snapshot: dict[str, bytes],
    provenance: bytes,
) -> None:
    stage.mkdir(mode=0o755)
    for relative, payload in sorted(
        snapshot.items(), key=lambda item: item[0].encode("utf-8")
    ):
        _write_frozen_file(stage.joinpath(*PurePosixPath(relative).parts), payload)
    _write_frozen_file(
        stage.joinpath(*PurePosixPath(_PROVENANCE_PATH).parts), provenance
    )
    directories = [path for path in stage.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        os.chmod(directory, 0o755, follow_symlinks=False)
        os.utime(
            directory,
            (_SOURCE_DATE_EPOCH, _SOURCE_DATE_EPOCH),
            follow_symlinks=False,
        )
    os.chmod(stage, 0o755, follow_symlinks=False)
    os.utime(stage, (_SOURCE_DATE_EPOCH, _SOURCE_DATE_EPOCH), follow_symlinks=False)


def _build_environment(build_python: Path, safe_root: Path) -> dict[str, str]:
    safe_root.mkdir(mode=0o700)
    home = safe_root / "home"
    temporary = safe_root / "tmp"
    home.mkdir(mode=0o700)
    temporary.mkdir(mode=0o700)
    return {
        "HOME": os.fspath(home),
        "SOURCE_DATE_EPOCH": str(_SOURCE_DATE_EPOCH),
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
        "TMPDIR": os.fspath(temporary),
        "PATH": os.pathsep.join((os.fspath(build_python.parent), "/usr/bin", "/bin")),
    }


def _build_once(build_python: Path, stage: Path, output: Path) -> bytes:
    output.mkdir(mode=0o755)
    command = [
        os.fspath(build_python),
        "-P",
        "-s",
        "-m",
        "build",
        "--wheel",
        "--no-isolation",
        "--outdir",
        os.fspath(output),
        os.fspath(stage),
    ]
    previous_umask = os.umask(0o022)
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            cwd=stage,
            env=_build_environment(build_python, output.parent / f"env-{output.name}"),
        )
    finally:
        os.umask(previous_umask)
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise ProducerBuildError(f"wheel build failed{suffix}")
    output_before, entries_before = _directory_inventory(
        output,
        "wheel build output",
    )
    if set(entries_before) != {_PRODUCER_FILENAME}:
        raise ProducerBuildError("wheel build did not produce the expected archive")
    expected = entries_before[_PRODUCER_FILENAME]
    if expected[2] != stat.S_IFREG:
        raise ProducerBuildError("wheel build output is not a regular archive")
    payload = _read_regular_once(
        output / _PRODUCER_FILENAME,
        "producer wheel",
        expected,
    )
    output_after, entries_after = _directory_inventory(output, "wheel build output")
    if output_before != output_after or entries_before != entries_after:
        raise ProducerBuildError("wheel build output inventory changed while reading")
    return payload


def _write_frozen_open_file(
    descriptor: int,
    directory_descriptor: int,
    payload: bytes,
) -> _Fingerprint:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise ProducerBuildError("producer wheel output could not be written")
        view = view[written:]
    os.fsync(descriptor)
    os.fchmod(descriptor, 0o644)
    os.utime(descriptor, (_SOURCE_DATE_EPOCH, _SOURCE_DATE_EPOCH))
    os.fsync(descriptor)
    fingerprint = _authenticate_open_file(
        descriptor,
        payload,
        "producer wheel output",
    )
    path_result = os.stat(
        _PRODUCER_FILENAME,
        dir_fd=directory_descriptor,
        follow_symlinks=False,
    )
    if _fingerprint(path_result) != fingerprint:
        raise ProducerBuildError("producer wheel output identity changed")
    return fingerprint


def _authenticate_open_file(
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
    except OSError as error:
        raise ProducerBuildError(f"{label} cannot be authenticated") from error
    if (
        _fingerprint(before) != _fingerprint(after)
        or b"".join(chunks) != expected
        or after.st_size != len(expected)
    ):
        raise ProducerBuildError(f"{label} changed during publication")
    return _fingerprint(after)


def _cleanup_owned_outputs(
    owned: dict[str, _OwnedOutput],
) -> None:
    fallback_descriptors: list[int] = []
    for name in reversed(("wheel-a", "wheel-b")):
        output = owned.get(name)
        if output is None:
            continue
        owner_identity = output.owner_identity
        cleanup_descriptor = output.cleanup_descriptor
        mutation_descriptor: int | None = None
        if (
            cleanup_descriptor is not None
            and not output.cleanup_close_attempted
            and owner_identity is not None
            and _descriptor_identity_status(cleanup_descriptor, owner_identity) is True
        ):
            mutation_descriptor = cleanup_descriptor
        elif (
            output.primary_descriptor is not None
            and not output.primary_close_attempted
            and owner_identity is not None
        ):
            try:
                fallback_descriptor = os.dup(output.primary_descriptor)
            except BaseException:
                fallback_descriptor = None
            if fallback_descriptor is not None:
                fallback_descriptors.append(fallback_descriptor)
                if (
                    _descriptor_identity_status(fallback_descriptor, owner_identity)
                    is True
                ):
                    mutation_descriptor = fallback_descriptor

        if mutation_descriptor is not None and owner_identity is not None:
            operations = (
                lambda: os.pwrite(mutation_descriptor, b"\0", 0),
                lambda: os.ftruncate(mutation_descriptor, 0),
                lambda: os.fsync(mutation_descriptor),
            )
            for operation in operations:
                if _descriptor_identity_status(
                    mutation_descriptor,
                    owner_identity,
                ) is False:
                    break
                try:
                    operation()
                except BaseException:
                    pass

    descriptors: list[int] = []
    for output in owned.values():
        if output.primary_descriptor is not None and not output.primary_close_attempted:
            descriptor = output.primary_descriptor
            output.primary_descriptor = None
            output.primary_close_attempted = True
            descriptors.append(descriptor)
        if output.cleanup_descriptor is not None and not output.cleanup_close_attempted:
            descriptor = output.cleanup_descriptor
            output.cleanup_descriptor = None
            output.cleanup_close_attempted = True
            descriptors.append(descriptor)
    descriptors.extend(fallback_descriptors)
    _close_best_effort(descriptors)


def _close_owned_for_commit(output: _OwnedOutput, attribute: str) -> None:
    descriptor = getattr(output, attribute)
    attempted_attribute = attribute.replace("descriptor", "close_attempted")
    if descriptor is None or getattr(output, attempted_attribute):
        return
    setattr(output, attribute, None)
    setattr(output, attempted_attribute, True)
    try:
        os.close(descriptor)
    except BaseException:
        pass


def _commit_owned_outputs(owned: dict[str, _OwnedOutput]) -> None:
    for attribute in ("primary_descriptor", "cleanup_descriptor"):
        for name in ("wheel-a", "wheel-b"):
            output = owned.get(name)
            if output is not None:
                _close_owned_for_commit(output, attribute)


def _reauthenticate_owned_outputs_for_commit(
    owned: dict[str, _OwnedOutput],
    wheel: bytes,
) -> None:
    for name in ("wheel-a", "wheel-b"):
        try:
            output = owned[name]
        except KeyError as error:
            raise ProducerBuildError("producer wheel pair is incomplete") from error
        descriptor = output.primary_descriptor
        if descriptor is None or output.primary_close_attempted:
            descriptor = output.cleanup_descriptor
            if descriptor is None or output.cleanup_close_attempted:
                raise ProducerBuildError(
                    f"{name} producer wheel has no retained authentication handle"
                )
        expected = output.expected_fingerprint
        owner_identity = output.owner_identity
        if expected is None or owner_identity is None:
            raise ProducerBuildError(
                f"{name} producer wheel authentication state is incomplete"
            )
        authenticated = _authenticate_open_file(
            descriptor,
            wheel,
            f"{name} producer wheel",
        )
        if authenticated != expected or authenticated[:2] != owner_identity:
            raise ProducerBuildError(
                f"{name} producer wheel changed before commit"
            )


def _publish_pair(
    work_descriptor: int,
    work_pin: _DirectoryPin,
    outputs: _OutputPins,
    wheel: bytes,
    source_root: Path,
    source_attestation: _SourceAttestation,
) -> None:
    expected_files: dict[str, _Fingerprint | None] = {
        "wheel-a": None,
        "wheel-b": None,
    }
    _recheck_prepared_root(
        work_descriptor,
        work_pin,
        outputs,
        expected_files,
    )
    _recheck_source_attestation(source_root, source_attestation)
    owned: dict[str, _OwnedOutput] = {}
    committed = False
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        for directory_name in ("wheel-a", "wheel-b"):
            _recheck_prepared_root(
                work_descriptor,
                work_pin,
                outputs,
                expected_files,
            )
            _recheck_source_attestation(source_root, source_attestation)
            directory_descriptor = outputs[directory_name][0]
            try:
                file_descriptor = os.open(
                    _PRODUCER_FILENAME,
                    flags,
                    0o644,
                    dir_fd=directory_descriptor,
                )
            except OSError as error:
                raise ProducerBuildError(
                    f"{directory_name} producer wheel cannot be created exclusively"
                ) from error
            output = _OwnedOutput(primary_descriptor=file_descriptor)
            owned[directory_name] = output
            try:
                opened_file = os.fstat(file_descriptor)
            except OSError as error:
                raise ProducerBuildError(
                    f"{directory_name} producer wheel initial pin failed"
                ) from error
            if not stat.S_ISREG(opened_file.st_mode):
                raise ProducerBuildError("producer wheel output is not regular")
            owner_identity = _identity(opened_file)
            output.owner_identity = owner_identity
            try:
                cleanup_descriptor = os.dup(file_descriptor)
            except OSError as error:
                raise ProducerBuildError(
                    f"{directory_name} producer wheel cleanup pin failed"
                ) from error
            output.cleanup_descriptor = cleanup_descriptor
            if (
                _descriptor_identity_status(cleanup_descriptor, owner_identity)
                is not True
            ):
                raise ProducerBuildError(
                    f"{directory_name} producer wheel cleanup pin changed"
                )
            expected_file = _write_frozen_open_file(
                file_descriptor,
                directory_descriptor,
                wheel,
            )
            expected_files[directory_name] = expected_file
            output.expected_fingerprint = expected_file
            os.fsync(directory_descriptor)
            _recheck_prepared_root(
                work_descriptor,
                work_pin,
                outputs,
                expected_files,
            )
            authenticated = _authenticate_open_file(
                file_descriptor,
                wheel,
                f"{directory_name} producer wheel",
            )
            if authenticated != expected_file:
                raise ProducerBuildError(
                    "producer wheel output changed during publication"
                )
            _recheck_source_attestation(source_root, source_attestation)
        _recheck_prepared_root(
            work_descriptor,
            work_pin,
            outputs,
            expected_files,
        )
        os.fsync(work_descriptor)
        _recheck_prepared_root(
            work_descriptor,
            work_pin,
            outputs,
            expected_files,
        )
        _recheck_source_attestation(source_root, source_attestation)
        _reauthenticate_owned_outputs_for_commit(owned, wheel)
        _recheck_prepared_root(
            work_descriptor,
            work_pin,
            outputs,
            expected_files,
        )
        # At final source-check return, the prior path/byte observations were
        # valid. These exact retained-byte/fingerprint and prepared-inventory
        # checks prove a simultaneous valid interval at that instant; later
        # concurrent drift is independently seal-detectable.
        # COMMIT POINT: no fallible action precedes the state transition below.
        committed = True
        _commit_owned_outputs(owned)
    except BaseException:
        if committed:
            try:
                _commit_owned_outputs(owned)
            except BaseException:
                pass
            return
        _cleanup_owned_outputs(owned)
        raise


def _run(source_root: Path, build_python: Path, work_root: Path) -> tuple[str, str, int]:
    previous_umask = os.umask(0o022)
    work_descriptor: int | None = None
    outputs: _OutputPins = {}
    try:
        source_identity = _identity(_require_directory(source_root, "source root"))
        _require_build_python(build_python)
        work_descriptor, work_pin, outputs = _prepare_work_root(
            work_root,
            source_root,
        )
        empty_outputs: dict[str, _Fingerprint | None] = {
            "wheel-a": None,
            "wheel-b": None,
        }
        source_attestation = _source_attestation(source_root)
        snapshot = _source_snapshot(source_root)
        _recheck_source_attestation(source_root, source_attestation)
        _recheck_prepared_root(
            work_descriptor,
            work_pin,
            outputs,
            empty_outputs,
        )
        if _identity(_require_directory(source_root, "source root")) != source_identity:
            raise ProducerBuildError("source root identity changed during snapshot")
        _validate_pyproject(snapshot["pyproject.toml"])
        requirements = _read_source_root_file(
            source_root,
            "requirements.lock",
            "requirements.lock",
        )
        _recheck_source_attestation(source_root, source_attestation)
        _recheck_prepared_root(
            work_descriptor,
            work_pin,
            outputs,
            empty_outputs,
        )
        if _identity(_require_directory(source_root, "source root")) != source_identity:
            raise ProducerBuildError("source root identity changed during snapshot")
        _validate_requirements_lock(requirements)
        provenance = _provenance(snapshot, requirements)

        with tempfile.TemporaryDirectory(
            prefix=".sfep-build-",
            dir="/tmp",
        ) as temporary:
            temporary_root = Path(temporary)
            stage_a = temporary_root / "stage-a"
            stage_b = temporary_root / "stage-b"
            _materialize_stage(stage_a, snapshot, provenance)
            _materialize_stage(stage_b, snapshot, provenance)
            wheel_a = _build_once(build_python, stage_a, temporary_root / "out-a")
            wheel_b = _build_once(build_python, stage_b, temporary_root / "out-b")
            if wheel_a != wheel_b:
                raise ProducerBuildError("independent producer wheel bytes differ")
        _recheck_source_attestation(source_root, source_attestation)
        result = _source_digest(snapshot), _sha256_uri(wheel_a), len(wheel_a)
        _publish_pair(
            work_descriptor,
            work_pin,
            outputs,
            wheel_a,
            source_root,
            source_attestation,
        )
        return result
    finally:
        try:
            _close_best_effort(
                [
                    *(value[0] for value in reversed(tuple(outputs.values()))),
                    work_descriptor,
                ]
            )
        finally:
            os.umask(previous_umask)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        source_root = _checked_absolute(arguments.source_root, "source root")
        build_python = _checked_absolute(arguments.build_python, "build Python")
        work_root = _checked_absolute(arguments.work_root, "work root")
        source_digest, wheel_digest, wheel_size = _run(
            source_root, build_python, work_root
        )
    except (ProducerBuildError, OSError, ValueError) as error:
        print(f"producer build failed: {error}", file=sys.stderr)
        return 2
    print(f"sourceSha256={source_digest}")
    print(f"wheelSha256={wheel_digest}")
    print(f"wheelSize={wheel_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
