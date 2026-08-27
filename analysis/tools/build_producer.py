#!/usr/bin/env python3
"""Build the producer wheel twice from one frozen source snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
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


class ProducerBuildError(RuntimeError):
    """The producer build request is unsafe or could not be completed."""


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


def _physical_missing_path(path: Path) -> Path:
    suffix: list[str] = []
    current = path
    while True:
        try:
            result = os.lstat(current)
        except FileNotFoundError:
            suffix.append(current.name)
            parent = current.parent
            if parent == current:
                raise ProducerBuildError("work root has no existing parent")
            current = parent
            continue
        except OSError as error:
            raise ProducerBuildError("work root parent is unavailable") from error
        if stat.S_ISLNK(result.st_mode) or not stat.S_ISDIR(result.st_mode):
            raise ProducerBuildError(
                "work root parent must be a non-symlink directory"
            )
        resolved = Path(os.path.realpath(current))
        for part in reversed(suffix):
            resolved /= part
        return resolved


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        common = Path(os.path.commonpath((os.fspath(first), os.fspath(second))))
    except ValueError:
        return False
    return common == first or common == second


def _prepare_work_root(path: Path, source_root: Path) -> None:
    source_physical = Path(os.path.realpath(source_root))
    if path.exists() or path.is_symlink():
        _require_directory(path, "work root")
        work_physical = Path(os.path.realpath(path))
        try:
            with os.scandir(path) as entries:
                if next(entries, None) is not None:
                    raise ProducerBuildError("work root must be empty")
        except OSError as error:
            raise ProducerBuildError("work root cannot be inspected") from error
    else:
        work_physical = _physical_missing_path(path)
    if _paths_overlap(source_physical, work_physical):
        raise ProducerBuildError("source root and work root must not overlap")


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


def _read_regular_once(path: Path, label: str) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as error:
        raise ProducerBuildError(f"{label} is unavailable") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ProducerBuildError(f"{label} must be a non-symlink regular file")
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
        os.close(descriptor)
    try:
        path_after = os.lstat(path)
    except OSError as error:
        raise ProducerBuildError(f"{label} changed while reading") from error
    identity = lambda item: (item.st_dev, item.st_ino)
    stable = lambda item: (
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
        stat.S_IFMT(item.st_mode),
    )
    if (
        identity(before) != identity(opened)
        or identity(opened) != identity(after)
        or identity(after) != identity(path_after)
        or stable(before) != stable(opened)
        or stable(opened) != stable(after)
        or stable(after) != stable(path_after)
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


def _walk_source_directory(
    source_root: Path,
    directory: Path,
    snapshot: dict[str, bytes],
) -> None:
    try:
        entries = list(os.scandir(directory))
    except OSError as error:
        raise ProducerBuildError("producer source directory cannot be read") from error
    keyed: list[tuple[bytes, os.DirEntry[str]]] = []
    for entry in entries:
        relative = (directory / entry.name).relative_to(source_root).as_posix()
        keyed.append((_checked_relative(relative), entry))
    for _encoded, entry in sorted(keyed, key=lambda item: item[0]):
        path = Path(entry.path)
        relative = path.relative_to(source_root).as_posix()
        try:
            result = entry.stat(follow_symlinks=False)
        except OSError as error:
            raise ProducerBuildError("producer source entry is unavailable") from error
        if stat.S_ISLNK(result.st_mode):
            raise ProducerBuildError("producer source must not contain a symlink")
        if stat.S_ISDIR(result.st_mode):
            _walk_source_directory(source_root, path, snapshot)
            continue
        if not stat.S_ISREG(result.st_mode):
            raise ProducerBuildError("producer source must contain only regular files")
        if relative == _PROVENANCE_PATH:
            raise ProducerBuildError("generated provenance must not exist in source")
        if _excluded_source(relative):
            continue
        if relative in snapshot:
            raise ProducerBuildError("producer source contains a duplicate path")
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
    _walk_source_directory(source_root, package_root, snapshot)
    if len(snapshot) < 2:
        raise ProducerBuildError("producer source snapshot is empty")
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
        os.close(descriptor)
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


def _build_environment(build_python: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH", "SOURCE_DATE_EPOCH", "PYTHONHASHSEED"}
    }
    environment.update({
        "SOURCE_DATE_EPOCH": str(_SOURCE_DATE_EPOCH),
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
        "PATH": os.pathsep.join((os.fspath(build_python.parent), "/usr/bin", "/bin")),
    })
    return environment


def _build_once(build_python: Path, stage: Path, output: Path) -> bytes:
    output.mkdir(mode=0o755)
    command = [
        os.fspath(build_python),
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
            env=_build_environment(build_python),
        )
    finally:
        os.umask(previous_umask)
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise ProducerBuildError(f"wheel build failed{suffix}")
    try:
        entries = list(os.scandir(output))
    except OSError as error:
        raise ProducerBuildError("wheel build output cannot be inspected") from error
    if len(entries) != 1 or entries[0].name != _PRODUCER_FILENAME:
        raise ProducerBuildError("wheel build did not produce the expected archive")
    result = entries[0].stat(follow_symlinks=False)
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISREG(result.st_mode):
        raise ProducerBuildError("wheel build output is not a regular archive")
    return _read_regular_once(Path(entries[0].path), "producer wheel")


def _publish_pair(work_root: Path, wheel: bytes) -> None:
    created: list[Path] = []
    try:
        for directory_name in ("wheel-a", "wheel-b"):
            directory = work_root / directory_name
            directory.mkdir(mode=0o755)
            created.append(directory)
            _write_frozen_file(directory / _PRODUCER_FILENAME, wheel)
    except Exception:
        for directory in reversed(created):
            shutil.rmtree(directory, ignore_errors=True)
        raise


def _run(source_root: Path, build_python: Path, work_root: Path) -> tuple[str, str, int]:
    _require_directory(source_root, "source root")
    _require_build_python(build_python)
    _prepare_work_root(work_root, source_root)
    snapshot = _source_snapshot(source_root)
    _validate_pyproject(snapshot["pyproject.toml"])
    requirements = _read_regular_once(
        source_root / "requirements.lock", "requirements.lock"
    )
    _validate_requirements_lock(requirements)
    provenance = _provenance(snapshot, requirements)

    work_root.mkdir(mode=0o755, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".sfep-build-", dir=work_root) as temporary:
        temporary_root = Path(temporary)
        stage_a = temporary_root / "stage-a"
        stage_b = temporary_root / "stage-b"
        _materialize_stage(stage_a, snapshot, provenance)
        _materialize_stage(stage_b, snapshot, provenance)
        wheel_a = _build_once(build_python, stage_a, temporary_root / "out-a")
        wheel_b = _build_once(build_python, stage_b, temporary_root / "out-b")
        if wheel_a != wheel_b:
            raise ProducerBuildError("independent producer wheel bytes differ")
    _publish_pair(work_root, wheel_a)
    return _source_digest(snapshot), _sha256_uri(wheel_a), len(wheel_a)


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
