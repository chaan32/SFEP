"""Fail-closed verification of the installed SFEP producer runtime."""

from __future__ import annotations

import base64
from collections.abc import Mapping
import csv
from dataclasses import dataclass
import hashlib
import io
import importlib.machinery
import importlib.util
import json
import os
import platform
import re
import stat
import sys
import sysconfig
from importlib import metadata as importlib_metadata
from importlib.metadata import Distribution
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from equipment_quality.deterministic import canonical_json_bytes, sha256_uri
from equipment_quality.schema import validate_normative_instance


_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_RECORD_BYTES = 16 * 1024 * 1024
_PROVENANCE_PATH = "equipment_quality/sfep_producer_provenance.json"
_PRODUCER_DISTRIBUTION = "sfep-equipment-quality"
_PACKAGE_NAMES = (
    "attrs",
    "jsonschema",
    "jsonschema-specifications",
    "numpy",
    "pandas",
    "python-dateutil",
    "pytz",
    "referencing",
    "rpds-py",
    "six",
    "typing-extensions",
    "tzdata",
)
_DIRECT_PACKAGE_NAMES = frozenset({"jsonschema", "numpy", "pandas"})
_EXPECTED_DISTRIBUTIONS = frozenset((*_PACKAGE_NAMES, "pip", _PRODUCER_DISTRIBUTION))
_PROVENANCE_FIELDS = frozenset(
    {
        "schemaVersion",
        "producerName",
        "distributionName",
        "version",
        "sourceSha256",
        "sourceDateEpoch",
        "requirementsLockSha256",
    }
)
_RECORD_HASH_PATTERN = re.compile(r"^sha256=([A-Za-z0-9_-]{43})$")
_RECORD_SIZE_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)$")
_PEP503_PATTERN = re.compile(r"[-_.]+")
_DIST_INFO_EXCLUSIONS = frozenset(
    {"RECORD", "INSTALLER", "direct_url.json", "REQUESTED"}
)
_HASH_PROBE_INPUTS = (
    "sfep-runtime-hash-seed/v1",
    "equipment-quality/3.12.10",
)
_HASH_SEED_PROBES = (-4218979432691865272, 1379760580859628941)
_DISTRIBUTION_IMPORT_ROOTS = MappingProxyType({
    "attrs": ("attr", "attrs"),
    "jsonschema": ("jsonschema",),
    "jsonschema-specifications": ("jsonschema_specifications",),
    "numpy": ("numpy",),
    "pandas": ("pandas",),
    "python-dateutil": ("dateutil",),
    "pytz": ("pytz",),
    "referencing": ("referencing",),
    "rpds-py": ("rpds",),
    "six": ("six",),
    "typing-extensions": ("typing_extensions",),
    "tzdata": ("tzdata",),
    _PRODUCER_DISTRIBUTION: ("equipment_quality",),
})
_IMPORT_ROOT_DISTRIBUTIONS = MappingProxyType({
    root: distribution
    for distribution, roots in _DISTRIBUTION_IMPORT_ROOTS.items()
    for root in roots
})


class RuntimeIdentityError(RuntimeError):
    """The installed process does not match its authenticated runtime manifest."""


@dataclass(frozen=True, slots=True)
class RuntimeImportOrigin:
    """A frozen import resolution or loaded-module origin."""

    name: str
    origin: Path | None
    module_file: Path | None
    package_locations: tuple[Path, ...]
    module_package_locations: tuple[Path, ...]
    loader_module: str | None


@dataclass(frozen=True, slots=True)
class RuntimeEnvironment:
    """A frozen snapshot adapter for process facts and installed distributions."""

    executable: Path
    platform_system: str
    platform_machine: str
    macos_product_version: str
    sysconfig_platform: str
    python_implementation: str
    python_version: str
    python_build: str
    python_cache_tag: str
    python_soabi: str
    python_hash_seed: str | None
    timezone: str | None
    distributions: tuple[Distribution, ...]
    python_hash_probes: tuple[int, int]
    resolved_imports: tuple[RuntimeImportOrigin, ...]
    loaded_imports: tuple[RuntimeImportOrigin, ...]


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """Deeply immutable result of a successful runtime verification."""

    manifest_bytes: bytes
    manifest_sha256: str
    manifest: Mapping[str, object]
    installed_code_trees: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class _VerifiedTree:
    digest: str
    captured_file: bytes | None
    record_files: Mapping[str, tuple[str, str]]
    record_directories: Mapping[str, tuple[str, str]]


def _identity_error(message: str, error: Exception | None = None) -> RuntimeIdentityError:
    failure = RuntimeIdentityError(message)
    if error is not None:
        failure.__cause__ = error
    return failure


def _read_manifest_once(path: Path) -> bytes:
    try:
        raw_path = os.fspath(path)
        before = os.lstat(raw_path)
    except (OSError, TypeError, ValueError) as error:
        raise _identity_error("runtime manifest is unavailable", error)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise _identity_error("runtime manifest must be a non-symlink regular file")
    if before.st_size > _MAX_MANIFEST_BYTES:
        raise _identity_error("runtime manifest exceeds the size limit")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(raw_path, flags)
    except OSError as error:
        raise _identity_error("runtime manifest is unreadable", error)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _identity_error("runtime manifest must be a regular file")
        if opened.st_size > _MAX_MANIFEST_BYTES:
            raise _identity_error("runtime manifest exceeds the size limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, _MAX_MANIFEST_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_MANIFEST_BYTES:
                raise _identity_error("runtime manifest exceeds the size limit")
        after = os.fstat(descriptor)
    except OSError as error:
        raise _identity_error("runtime manifest could not be read", error)
    finally:
        os.close(descriptor)

    identity_before = (before.st_dev, before.st_ino)
    identity_opened = (opened.st_dev, opened.st_ino)
    if identity_before != identity_opened:
        raise _identity_error("runtime manifest changed while opening")
    if (
        opened.st_size != after.st_size
        or opened.st_mtime_ns != after.st_mtime_ns
        or total != after.st_size
    ):
        raise _identity_error("runtime manifest changed while reading")
    return b"".join(chunks)


def _reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number: {value}")


def _parse_canonical_json(payload: bytes, label: str) -> object:
    if not payload or len(payload) > _MAX_MANIFEST_BYTES:
        raise _identity_error(f"{label} has an invalid size")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise _identity_error(f"{label} must not contain a UTF-8 BOM")
    if not payload.endswith(b"\n"):
        raise _identity_error(f"{label} must end with one LF")
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_json_constant,
        )
        rendered = canonical_json_bytes(value)
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError) as error:
        raise _identity_error(f"{label} is not strict canonical JSON", error)
    if rendered != payload:
        raise _identity_error(f"{label} bytes are not canonical JSON")
    return value


def _deep_freeze(value: object) -> object:
    if type(value) is dict:
        return MappingProxyType(
            {key: _deep_freeze(item) for key, item in value.items()}
        )
    if type(value) is list:
        return tuple(_deep_freeze(item) for item in value)
    return value


def _normalise_distribution_name(name: object) -> str:
    if type(name) is not str or not name:
        raise _identity_error("installed distribution has no valid name")
    normalised = _PEP503_PATTERN.sub("-", name).lower()
    if not normalised:
        raise _identity_error("installed distribution has no valid normalized name")
    return normalised


def _is_generated_bin_path(parts: tuple[str, ...]) -> bool:
    return (
        len(parts) == 5
        and parts[:4] == ("..", "..", "..", "bin")
        and parts[4] not in {"", ".", ".."}
    )


def _checked_record_path(raw_path: str) -> tuple[PurePosixPath, bool]:
    if (
        type(raw_path) is not str
        or not raw_path
        or any(ord(character) < 32 or ord(character) == 127 for character in raw_path)
    ):
        raise _identity_error("distribution RECORD contains an unsafe path")
    try:
        raw_path.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise _identity_error("distribution RECORD path is not UTF-8", error)
    if "\\" in raw_path or raw_path.startswith("/") or raw_path.endswith("/"):
        raise _identity_error("distribution RECORD contains an unsafe path")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or path.as_posix() != raw_path:
        raise _identity_error("distribution RECORD contains a non-canonical path")
    parts = path.parts
    generated_bin = _is_generated_bin_path(parts)
    if ".." in parts and not generated_bin:
        raise _identity_error("distribution RECORD path escapes its installation root")
    return path, generated_bin


def _lexical_path_key(path: Path) -> str:
    try:
        raw_path = os.fspath(path)
    except (TypeError, ValueError) as error:
        raise _identity_error("runtime path is invalid", error)
    if not os.path.isabs(raw_path):
        raise _identity_error("runtime path must be absolute")
    return os.path.normcase(os.path.abspath(raw_path))


def _physical_path_key(path: Path) -> str:
    _lexical_path_key(path)
    try:
        resolved = os.path.realpath(os.fspath(path), strict=True)
    except (OSError, TypeError, ValueError) as error:
        raise _identity_error("runtime path cannot be resolved", error)
    return os.path.normcase(resolved)


def _checked_located_target(
    distribution: Distribution,
    base: Path,
    path: PurePosixPath,
) -> Path:
    expected = Path(os.path.join(os.fspath(base), *path.parts))
    try:
        target = Path(distribution.locate_file(path))
    except Exception as error:
        raise _identity_error("distribution RECORD target cannot be located", error)
    if _lexical_path_key(target) != _lexical_path_key(expected):
        raise _identity_error("distribution RECORD target does not match its base path")
    if _physical_path_key(target) != _physical_path_key(expected):
        raise _identity_error("distribution RECORD target resolves outside its base path")
    return target


def _require_non_symlink_directory(path: Path) -> None:
    try:
        mode = os.lstat(path).st_mode
    except OSError as error:
        raise _identity_error("distribution RECORD directory is inaccessible", error)
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise _identity_error("distribution RECORD directory must be non-symlink")


def _reject_path_symlinks(base: Path, path: PurePosixPath, generated_bin: bool) -> None:
    _require_non_symlink_directory(base)
    if generated_bin:
        current = base
        for part in path.parts:
            if part == "..":
                _require_non_symlink_directory(current)
                current = current.parent
                continue
            current = current / part
            try:
                mode = os.lstat(current).st_mode
            except OSError as error:
                raise _identity_error("distribution RECORD target is inaccessible", error)
            if stat.S_ISLNK(mode):
                raise _identity_error("distribution RECORD target must not be a symlink")
        return

    current = base
    for part in path.parts:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except OSError as error:
            raise _identity_error("distribution RECORD target is missing", error)
        if stat.S_ISLNK(mode):
            raise _identity_error("distribution RECORD target must not be a symlink")


def _read_verified_record_file(
    target: Path,
    expected_hash: str,
    expected_size: int,
    *,
    capture: bool,
) -> tuple[str, bytes | None]:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
    except OSError as error:
        raise _identity_error("distribution RECORD target is unreadable", error)
    digest = hashlib.sha256()
    captured = bytearray() if capture else None
    total = 0
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _identity_error("distribution RECORD target is not a regular file")
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
            if captured is not None:
                captured.extend(chunk)
        after = os.fstat(descriptor)
    except OSError as error:
        raise _identity_error("distribution RECORD target could not be read", error)
    finally:
        os.close(descriptor)
    if (
        opened.st_size != after.st_size
        or opened.st_mtime_ns != after.st_mtime_ns
        or total != after.st_size
    ):
        raise _identity_error("distribution RECORD target changed while reading")
    if total != expected_size:
        raise _identity_error("distribution RECORD size does not match installed bytes")
    encoded = base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode("ascii")
    if encoded != expected_hash:
        raise _identity_error("distribution RECORD hash does not match installed bytes")
    return digest.hexdigest(), bytes(captured) if captured is not None else None


def _read_record_snapshot(target: Path) -> bytes:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
    except OSError as error:
        raise _identity_error("installed distribution RECORD is unreadable", error)
    chunks: list[bytes] = []
    total = 0
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _identity_error("installed distribution RECORD must be regular")
        if opened.st_size > _MAX_RECORD_BYTES:
            raise _identity_error("installed distribution RECORD exceeds the size limit")
        while True:
            chunk = os.read(descriptor, min(65536, _MAX_RECORD_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_RECORD_BYTES:
                raise _identity_error(
                    "installed distribution RECORD exceeds the size limit"
                )
        after = os.fstat(descriptor)
    except OSError as error:
        raise _identity_error("installed distribution RECORD could not be read", error)
    finally:
        os.close(descriptor)
    if (
        opened.st_size != after.st_size
        or opened.st_mtime_ns != after.st_mtime_ns
        or total != after.st_size
    ):
        raise _identity_error("installed distribution RECORD changed while reading")
    return b"".join(chunks)


def _normalise_record_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _tree_path_is_excluded(path: PurePosixPath, generated_bin: bool) -> bool:
    if generated_bin:
        return True
    parts = path.parts
    if "__pycache__" in parts or path.name.endswith(".pyc"):
        return True
    return (
        len(parts) >= 2
        and parts[-2].endswith(".dist-info")
        and parts[-1] in _DIST_INFO_EXCLUSIONS
    )


def _verified_distribution_tree(
    distribution: Distribution,
    *,
    capture_path: str | None = None,
) -> _VerifiedTree:
    try:
        record_text = distribution.read_text("RECORD")
    except Exception as error:
        raise _identity_error("installed distribution RECORD is unreadable", error)
    if type(record_text) is not str or not record_text:
        raise _identity_error("installed distribution has no RECORD")
    try:
        record_bytes = record_text.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise _identity_error("installed distribution RECORD is not UTF-8", error)
    if len(record_bytes) > _MAX_RECORD_BYTES:
        raise _identity_error("installed distribution RECORD exceeds the size limit")
    try:
        rows = tuple(csv.reader(io.StringIO(record_text, newline=""), strict=True))
    except (csv.Error, UnicodeError) as error:
        raise _identity_error("installed distribution RECORD is malformed", error)
    if not rows:
        raise _identity_error("installed distribution RECORD is empty")

    try:
        base = Path(distribution.locate_file(PurePosixPath(".")))
    except Exception as error:
        raise _identity_error("installed distribution root is unavailable", error)
    _lexical_path_key(base)
    _require_non_symlink_directory(base)

    seen_paths: set[str] = set()
    record_self_count = 0
    record_target: Path | None = None
    captured_file: bytes | None = None
    digest_lines: list[tuple[bytes, bytes]] = []
    record_files: dict[str, tuple[str, str]] = {}
    record_directories: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3:
            raise _identity_error("installed distribution RECORD row must have three fields")
        raw_path, hash_field, size_field = row
        if raw_path in seen_paths:
            raise _identity_error("installed distribution RECORD contains a duplicate path")
        seen_paths.add(raw_path)
        path, generated_bin = _checked_record_path(raw_path)
        target = _checked_located_target(distribution, base, path)
        _reject_path_symlinks(base, path, generated_bin)
        try:
            target_stat = os.lstat(target)
        except OSError as error:
            raise _identity_error("distribution RECORD target is missing", error)
        if stat.S_ISLNK(target_stat.st_mode):
            raise _identity_error("distribution RECORD target must not be a symlink")
        if stat.S_ISDIR(target_stat.st_mode):
            if generated_bin:
                raise _identity_error("generated RECORD script must be a regular file")
            continue
        if not stat.S_ISREG(target_stat.st_mode):
            raise _identity_error("distribution RECORD target must be regular")

        record_self = (
            len(path.parts) >= 2
            and path.parts[-2].endswith(".dist-info")
            and path.name == "RECORD"
        )
        if record_self:
            record_self_count += 1
            if hash_field or size_field:
                raise _identity_error("distribution RECORD self-row must be unhashed")
            record_target = target
            continue

        generated_bytecode = "__pycache__" in path.parts or path.name.endswith(".pyc")
        if generated_bytecode and not hash_field and not size_field:
            continue

        match = _RECORD_HASH_PATTERN.fullmatch(hash_field)
        if match is None:
            raise _identity_error("distribution RECORD requires a sha256 hash")
        if _RECORD_SIZE_PATTERN.fullmatch(size_field) is None:
            raise _identity_error("distribution RECORD requires a canonical size")
        expected_size = int(size_field)
        file_hex, file_bytes = _read_verified_record_file(
            target,
            match.group(1),
            expected_size,
            capture=raw_path == capture_path,
        )
        if raw_path == capture_path:
            captured_file = file_bytes
        if _tree_path_is_excluded(path, generated_bin):
            continue
        target_key = _lexical_path_key(target)
        file_entry = (raw_path, _physical_path_key(target))
        previous_path = record_files.setdefault(target_key, file_entry)
        if previous_path != file_entry:
            raise _identity_error("distribution RECORD paths alias one installed file")
        parent = path.parent
        while parent != PurePosixPath("."):
            directory_target = Path(os.path.join(os.fspath(base), *parent.parts))
            directory_key = _lexical_path_key(directory_target)
            raw_directory = parent.as_posix()
            directory_entry = (
                raw_directory,
                _physical_path_key(directory_target),
            )
            previous_directory = record_directories.setdefault(
                directory_key, directory_entry
            )
            if previous_directory != directory_entry:
                raise _identity_error("distribution RECORD directories are ambiguous")
            parent = parent.parent
        encoded_path = raw_path.encode("utf-8")
        digest_lines.append(
            (
                encoded_path,
                encoded_path + b"=sha256:" + file_hex.encode("ascii") + b"\n",
            )
        )
    if record_self_count != 1:
        raise _identity_error("installed distribution RECORD self-row is not unique")
    if record_target is None:
        raise _identity_error("installed distribution RECORD self-row is unavailable")
    record_snapshot = _read_record_snapshot(record_target)
    try:
        snapshot_text = record_snapshot.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise _identity_error("installed distribution RECORD is not UTF-8", error)
    if _normalise_record_newlines(snapshot_text) != _normalise_record_newlines(
        record_text
    ):
        raise _identity_error("installed distribution RECORD changed after reading")
    preimage = b"".join(
        line for _path, line in sorted(digest_lines, key=lambda item: item[0])
    )
    return _VerifiedTree(
        sha256_uri(preimage),
        captured_file,
        MappingProxyType(dict(record_files)),
        MappingProxyType(dict(record_directories)),
    )


def installed_code_tree(distribution: Distribution) -> str:
    """Return a RECORD-verified installed-code-tree digest for a distribution."""
    try:
        return _verified_distribution_tree(distribution).digest
    except RuntimeIdentityError:
        raise
    except Exception as error:
        raise _identity_error("installed distribution verification failed", error)


def _hash_interpreter(executable: Path) -> str:
    try:
        real_path = Path(os.path.realpath(os.fspath(executable)))
        descriptor = os.open(
            real_path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except (OSError, TypeError, ValueError) as error:
        raise _identity_error("interpreter executable is unavailable", error)
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise _identity_error("interpreter executable must be a regular file")
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError as error:
        raise _identity_error("interpreter executable could not be read", error)
    finally:
        os.close(descriptor)
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise _identity_error("interpreter executable changed while reading")
    return "sha256:" + digest.hexdigest()


def _capture_import_origin(
    name: str,
    spec: object,
    module: object | None,
) -> RuntimeImportOrigin:
    if getattr(spec, "name", None) != name:
        raise _identity_error("runtime import spec name is inconsistent")
    raw_origin = getattr(spec, "origin", None)
    if raw_origin is None:
        origin = None
    elif type(raw_origin) is str and raw_origin not in {"built-in", "frozen"}:
        origin = Path(raw_origin)
    else:
        raise _identity_error("runtime import origin is invalid")

    raw_module_file = None if module is None else getattr(module, "__file__", None)
    if raw_module_file is None:
        module_file = None
    elif type(raw_module_file) is str:
        module_file = Path(raw_module_file)
    else:
        raise _identity_error("loaded runtime module file is invalid")

    raw_locations = getattr(spec, "submodule_search_locations", None)
    if raw_locations is None:
        package_locations: tuple[Path, ...] = ()
    else:
        try:
            locations = tuple(raw_locations)
        except (TypeError, RuntimeError) as error:
            raise _identity_error("runtime package locations are unavailable", error)
        if any(type(location) is not str for location in locations):
            raise _identity_error("runtime package location is invalid")
        package_locations = tuple(Path(location) for location in locations)

    raw_module_locations = None if module is None else getattr(module, "__path__", None)
    if raw_module_locations is None:
        module_package_locations: tuple[Path, ...] = ()
    else:
        try:
            active_locations = tuple(raw_module_locations)
        except (TypeError, RuntimeError) as error:
            raise _identity_error("loaded runtime module path is unavailable", error)
        if any(type(location) is not str for location in active_locations):
            raise _identity_error("loaded runtime module path is invalid")
        module_package_locations = tuple(Path(location) for location in active_locations)

    loader = getattr(spec, "loader", None)
    if loader is None:
        loader_module = None
    else:
        loader_module = type(loader).__module__
        if type(loader_module) is not str or not loader_module:
            raise _identity_error("runtime import loader is invalid")
    return RuntimeImportOrigin(
        name=name,
        origin=origin,
        module_file=module_file,
        package_locations=package_locations,
        module_package_locations=module_package_locations,
        loader_module=loader_module,
    )


def _import_sort_key(item: RuntimeImportOrigin) -> bytes:
    try:
        return item.name.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise _identity_error("runtime import name is not UTF-8", error)


def _capture_import_state(
) -> tuple[tuple[RuntimeImportOrigin, ...], tuple[RuntimeImportOrigin, ...]]:
    resolved: list[RuntimeImportOrigin] = []
    for root in sorted(_IMPORT_ROOT_DISTRIBUTIONS, key=lambda value: value.encode("utf-8")):
        try:
            spec = importlib.util.find_spec(root)
        except (ImportError, AttributeError, ValueError) as error:
            raise _identity_error("runtime import resolution failed", error)
        if spec is None:
            raise _identity_error("runtime import root cannot be resolved")
        resolved.append(_capture_import_origin(root, spec, None))

    loaded: list[RuntimeImportOrigin] = []
    for name, module in tuple(sys.modules.items()):
        if type(name) is not str or module is None:
            continue
        root = name.partition(".")[0]
        if root not in _IMPORT_ROOT_DISTRIBUTIONS:
            continue
        spec = getattr(module, "__spec__", None)
        if spec is None:
            raise _identity_error("loaded runtime module has no import spec")
        loaded.append(_capture_import_origin(name, spec, module))
    return tuple(sorted(resolved, key=_import_sort_key)), tuple(
        sorted(loaded, key=_import_sort_key)
    )


def _capture_environment() -> RuntimeEnvironment:
    build_number, build_date = platform.python_build()
    cache_tag = sys.implementation.cache_tag
    soabi = sysconfig.get_config_var("SOABI")
    if type(cache_tag) is not str or type(soabi) is not str:
        raise _identity_error("CPython runtime metadata is unavailable")
    resolved_imports, loaded_imports = _capture_import_state()
    return RuntimeEnvironment(
        executable=Path(sys.executable),
        platform_system=platform.system(),
        platform_machine=platform.machine(),
        macos_product_version=platform.mac_ver()[0],
        sysconfig_platform=sysconfig.get_platform(),
        python_implementation=platform.python_implementation(),
        python_version=platform.python_version(),
        python_build=f"{build_number} {build_date}",
        python_cache_tag=cache_tag,
        python_soabi=soabi,
        python_hash_seed=os.environ.get("PYTHONHASHSEED"),
        timezone=os.environ.get("TZ"),
        distributions=tuple(importlib_metadata.distributions()),
        python_hash_probes=tuple(str.__hash__(value) for value in _HASH_PROBE_INPUTS),
        resolved_imports=resolved_imports,
        loaded_imports=loaded_imports,
    )


def _require_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise _identity_error(f"runtime {label} does not match the manifest")


def _require_manifest_object(value: object, label: str) -> Mapping[str, object]:
    if type(value) is not dict:
        raise _identity_error(f"runtime manifest {label} must be an object")
    return value


def _installed_distributions(
    environment: RuntimeEnvironment,
) -> dict[str, Distribution]:
    if type(environment.distributions) is not tuple:
        raise _identity_error("runtime distribution inventory is not frozen")
    observed: dict[str, Distribution] = {}
    for distribution in environment.distributions:
        try:
            name = distribution.metadata["Name"]
            version = distribution.version
        except Exception as error:
            raise _identity_error("installed distribution metadata is unreadable", error)
        normalised = _normalise_distribution_name(name)
        if type(version) is not str or not version:
            raise _identity_error("installed distribution has no valid version")
        if normalised in observed:
            raise _identity_error("installed distribution names are not unique")
        observed[normalised] = distribution
    if frozenset(observed) != _EXPECTED_DISTRIBUTIONS:
        raise _identity_error("installed distribution inventory is not exact")
    return observed


def _verify_import_snapshot_shape(
    value: object,
    label: str,
) -> tuple[RuntimeImportOrigin, ...]:
    if type(value) is not tuple:
        raise _identity_error(f"runtime {label} import snapshot is not frozen")
    imports: tuple[RuntimeImportOrigin, ...] = value
    names: list[str] = []
    for item in imports:
        if type(item) is not RuntimeImportOrigin:
            raise _identity_error(f"runtime {label} import snapshot entry is invalid")
        if (
            type(item.name) is not str
            or not item.name
            or any(
                re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) is None
                for part in item.name.split(".")
            )
        ):
            raise _identity_error(f"runtime {label} import name is invalid")
        if item.origin is not None and not isinstance(item.origin, Path):
            raise _identity_error(f"runtime {label} import origin is invalid")
        if item.module_file is not None and not isinstance(item.module_file, Path):
            raise _identity_error(f"runtime {label} module file is invalid")
        if (
            type(item.package_locations) is not tuple
            or any(not isinstance(location, Path) for location in item.package_locations)
        ):
            raise _identity_error(f"runtime {label} package locations are not frozen")
        if (
            type(item.module_package_locations) is not tuple
            or any(
                not isinstance(location, Path)
                for location in item.module_package_locations
            )
        ):
            raise _identity_error(f"runtime {label} module path is not frozen")
        if item.loader_module is not None and (
            type(item.loader_module) is not str or not item.loader_module
        ):
            raise _identity_error(f"runtime {label} import loader is invalid")
        names.append(item.name)
    if len(set(names)) != len(names):
        raise _identity_error(f"runtime {label} import names are not unique")
    expected_order = sorted(names, key=lambda name: name.encode("utf-8"))
    if names != expected_order:
        raise _identity_error(f"runtime {label} import snapshot is not ordered")
    return imports


def _record_path_matches_module(record_path: str, module_name: str) -> bool:
    module_path = module_name.replace(".", "/")
    if record_path in {f"{module_path}.py", f"{module_path}/__init__.py"}:
        return True
    return any(
        record_path in {
            module_path + suffix,
            module_path + "/__init__" + suffix,
        }
        for suffix in importlib.machinery.EXTENSION_SUFFIXES
    )


def _record_path_is_package(record_path: str, module_name: str) -> bool:
    module_path = module_name.replace(".", "/")
    if record_path == f"{module_path}/__init__.py":
        return True
    return any(
        record_path == module_path + "/__init__" + suffix
        for suffix in importlib.machinery.EXTENSION_SUFFIXES
    )


def _verify_record_backed_import(
    item: RuntimeImportOrigin,
    tree: _VerifiedTree,
    *,
    loaded: bool,
) -> None:
    if item.origin is None:
        raise _identity_error("runtime import has no RECORD-backed origin")
    origin_key = _lexical_path_key(item.origin)
    record_entry = tree.record_files.get(origin_key)
    if record_entry is None:
        raise _identity_error("runtime import origin is not in its distribution RECORD tree")
    record_path, record_physical_path = record_entry
    if _physical_path_key(item.origin) != record_physical_path:
        raise _identity_error("runtime import origin resolves outside its RECORD tree")
    if not _record_path_matches_module(record_path, item.name):
        raise _identity_error("runtime import origin does not match its module namespace")

    if loaded:
        if item.module_file is None:
            raise _identity_error("loaded runtime module has no file origin")
        if (
            _lexical_path_key(item.module_file) != origin_key
            or _physical_path_key(item.module_file) != record_physical_path
        ):
            raise _identity_error("loaded runtime module file disagrees with its import spec")
        spec_locations = tuple(
            (_lexical_path_key(location), _physical_path_key(location))
            for location in item.package_locations
        )
        module_locations = tuple(
            (_lexical_path_key(location), _physical_path_key(location))
            for location in item.module_package_locations
        )
        if module_locations != spec_locations:
            raise _identity_error("loaded runtime module path disagrees with its import spec")
    elif item.module_file is not None:
        raise _identity_error("resolved runtime import unexpectedly has a loaded file")
    elif item.module_package_locations:
        raise _identity_error("resolved runtime import unexpectedly has a module path")

    package_origin = _record_path_is_package(record_path, item.name)
    if package_origin:
        if len(item.package_locations) != 1:
            raise _identity_error("runtime package origin is not an installed package")
        location = item.package_locations[0]
        expected_directory = item.name.replace(".", "/")
        directory_entry = tree.record_directories.get(_lexical_path_key(location))
        if (
            directory_entry is None
            or directory_entry[0] != expected_directory
            or directory_entry[1] != _physical_path_key(location)
        ):
            raise _identity_error(
                "runtime package location is not in its distribution RECORD tree"
            )
    elif item.package_locations:
        raise _identity_error("runtime module has unexpected package locations")


def _verify_import_bindings(
    environment: RuntimeEnvironment,
    distribution_trees: Mapping[str, _VerifiedTree],
) -> None:
    resolved = _verify_import_snapshot_shape(
        environment.resolved_imports, "resolved"
    )
    loaded = _verify_import_snapshot_shape(environment.loaded_imports, "loaded")
    expected_resolved = tuple(
        sorted(_IMPORT_ROOT_DISTRIBUTIONS, key=lambda name: name.encode("utf-8"))
    )
    if tuple(item.name for item in resolved) != expected_resolved:
        raise _identity_error("runtime resolved import root map is not exact")

    verified_loaded: set[str] = set()
    synthetic: list[RuntimeImportOrigin] = []
    for item, is_loaded in (
        *((item, False) for item in resolved),
        *((item, True) for item in loaded),
    ):
        root = item.name.partition(".")[0]
        distribution_name = _IMPORT_ROOT_DISTRIBUTIONS.get(root)
        if distribution_name is None:
            raise _identity_error("runtime import root is not approved")
        tree = distribution_trees.get(distribution_name)
        if tree is None:
            raise _identity_error("runtime import distribution tree is unavailable")
        if item.origin is None:
            if (
                not is_loaded
                or item.name == root
                or not (
                    item.name == "six.moves"
                    or item.name.startswith("six.moves.")
                )
                or item.loader_module != "six"
            ):
                raise _identity_error("runtime import root has no RECORD-backed origin")
            synthetic.append(item)
            continue
        _verify_record_backed_import(item, tree, loaded=is_loaded)
        if is_loaded:
            verified_loaded.add(item.name)

    loaded_by_name = {item.name: item for item in loaded}
    for item in synthetic:
        root = item.name.partition(".")[0]
        if (
            item.module_file is not None
            or item.package_locations
            or item.module_package_locations
        ):
            raise _identity_error("synthetic runtime import has an unexplained location")
        loader_module = item.loader_module
        if (
            loader_module is None
            or loader_module.partition(".")[0] != root
            or loader_module not in loaded_by_name
            or loader_module not in verified_loaded
        ):
            raise _identity_error("synthetic runtime import loader is not RECORD-backed")


def _verify_manifest_inventory(manifest: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    raw_packages = manifest["packages"]
    if type(raw_packages) is not list:
        raise _identity_error("manifest package inventory must be an array")
    packages: list[Mapping[str, object]] = []
    names: list[str] = []
    normalised_names: set[str] = set()
    for item in raw_packages:
        if type(item) is not dict:
            raise _identity_error("manifest package entry must be an object")
        name = item["name"]
        normalised = _normalise_distribution_name(name)
        if normalised in normalised_names:
            raise _identity_error("manifest package names are not unique")
        normalised_names.add(normalised)
        if type(name) is not str:
            raise _identity_error("manifest package name must be a string")
        names.append(name)
        packages.append(item)
    if tuple(names) != _PACKAGE_NAMES:
        raise _identity_error("manifest package inventory or order is not exact")
    for package in packages:
        name = package["name"]
        expected_direct = name in _DIRECT_PACKAGE_NAMES
        if package["direct"] is not expected_direct:
            raise _identity_error("manifest direct dependency flags are not exact")
    return tuple(packages)


def _verify_process_facts(
    manifest: Mapping[str, object],
    environment: RuntimeEnvironment,
) -> None:
    platform_manifest = manifest["platform"]
    python_manifest = manifest["python"]
    policy_manifest = manifest["environmentPolicy"]
    platform_manifest = _require_manifest_object(platform_manifest, "platform")
    python_manifest = _require_manifest_object(python_manifest, "python")
    policy_manifest = _require_manifest_object(policy_manifest, "environmentPolicy")
    if environment.python_hash_seed != "0":
        raise _identity_error("runtime PYTHONHASHSEED text is not the pinned seed")
    if (
        type(environment.python_hash_probes) is not tuple
        or environment.python_hash_probes != _HASH_SEED_PROBES
    ):
        raise _identity_error("runtime active hash seed does not match the pinned seed")
    comparisons = (
        (environment.platform_system, platform_manifest["system"], "platform system"),
        (environment.platform_machine, platform_manifest["machine"], "platform machine"),
        (
            environment.macos_product_version,
            platform_manifest["macosProductVersion"],
            "macOS product version",
        ),
        (
            environment.sysconfig_platform,
            platform_manifest["sysconfigPlatform"],
            "sysconfig platform",
        ),
        (
            environment.python_implementation,
            python_manifest["implementation"],
            "Python implementation",
        ),
        (environment.python_version, python_manifest["version"], "Python version"),
        (environment.python_build, python_manifest["build"], "Python build"),
        (environment.python_cache_tag, python_manifest["cacheTag"], "Python cache tag"),
        (environment.python_soabi, python_manifest["soabi"], "Python SOABI"),
        (
            environment.python_hash_seed,
            policy_manifest["pythonHashSeed"],
            "PYTHONHASHSEED",
        ),
        (environment.timezone, policy_manifest["timezone"], "timezone"),
        (
            _hash_interpreter(environment.executable),
            python_manifest["executableSha256"],
            "interpreter",
        ),
    )
    for actual, expected, label in comparisons:
        _require_equal(actual, expected, label)


def _verify_provenance(
    payload: bytes | None,
    manifest: Mapping[str, object],
) -> None:
    if payload is None:
        raise _identity_error("producer provenance is not RECORD-backed")
    value = _parse_canonical_json(payload, "producer provenance")
    if type(value) is not dict or frozenset(value) != _PROVENANCE_FIELDS:
        raise _identity_error("producer provenance fields are not exact")
    producer = _require_manifest_object(manifest["producer"], "producer")
    locks = _require_manifest_object(manifest["locks"], "locks")
    expected = {
        "schemaVersion": "sfep-producer-provenance/v1",
        "producerName": "equipment-quality",
        "distributionName": _PRODUCER_DISTRIBUTION,
        "version": producer["version"],
        "sourceSha256": producer["sourceSha256"],
        "sourceDateEpoch": "1735689600",
        "requirementsLockSha256": locks["requirements"],
    }
    if value != expected:
        raise _identity_error("producer provenance does not match the runtime manifest")


def _verify_runtime(
    manifest_path: Path,
    environment: RuntimeEnvironment | None,
) -> RuntimeIdentity:
    manifest_bytes = _read_manifest_once(manifest_path)
    parsed = _parse_canonical_json(manifest_bytes, "runtime manifest")
    if type(parsed) is not dict:
        raise _identity_error("runtime manifest root must be an object")
    try:
        validate_normative_instance("producer_runtime.schema.json", parsed)
    except Exception as error:
        raise _identity_error("runtime manifest does not satisfy its schema", error)
    packages = _verify_manifest_inventory(parsed)
    snapshot = _capture_environment() if environment is None else environment
    if type(snapshot) is not RuntimeEnvironment:
        raise _identity_error("runtime environment adapter is invalid")
    _verify_process_facts(parsed, snapshot)
    installed = _installed_distributions(snapshot)

    pip_distribution = installed["pip"]
    _require_equal(pip_distribution.version, parsed["pipVersion"], "pip version")
    verified_trees: dict[str, str] = {}
    distribution_trees: dict[str, _VerifiedTree] = {}
    for package in packages:
        name = package["name"]
        if type(name) is not str:
            raise _identity_error("manifest package name must be a string")
        distribution = installed[name]
        _require_equal(distribution.version, package["version"], f"{name} version")
        tree = _verified_distribution_tree(distribution)
        _require_equal(
            tree.digest,
            package["installedCodeTreeSha256"],
            f"{name} installed code tree",
        )
        verified_trees[name] = tree.digest
        distribution_trees[name] = tree

    producer_manifest = _require_manifest_object(parsed["producer"], "producer")
    if producer_manifest["name"] != "equipment-quality":
        raise _identity_error("logical producer name is not exact")
    producer_distribution = installed[_PRODUCER_DISTRIBUTION]
    _require_equal(
        producer_distribution.version,
        producer_manifest["version"],
        "producer version",
    )
    producer_tree = _verified_distribution_tree(
        producer_distribution,
        capture_path=_PROVENANCE_PATH,
    )
    _require_equal(
        producer_tree.digest,
        producer_manifest["installedCodeTreeSha256"],
        "producer installed code tree",
    )
    _verify_provenance(producer_tree.captured_file, parsed)
    verified_trees[_PRODUCER_DISTRIBUTION] = producer_tree.digest
    distribution_trees[_PRODUCER_DISTRIBUTION] = producer_tree
    _verify_import_bindings(snapshot, MappingProxyType(distribution_trees))

    frozen_manifest = _deep_freeze(parsed)
    if not isinstance(frozen_manifest, Mapping):
        raise _identity_error("runtime manifest could not be frozen")
    return RuntimeIdentity(
        manifest_bytes=bytes(manifest_bytes),
        manifest_sha256=sha256_uri(manifest_bytes),
        manifest=frozen_manifest,
        installed_code_trees=MappingProxyType(dict(verified_trees)),
    )


def verify_runtime(
    manifest_path: Path,
    environment: RuntimeEnvironment | None = None,
) -> RuntimeIdentity:
    """Verify the installed process against one canonical runtime manifest."""
    try:
        return _verify_runtime(manifest_path, environment)
    except RuntimeIdentityError:
        raise
    except Exception as error:
        raise _identity_error("runtime identity verification failed closed", error)
