"""Fail-closed verification of the installed SFEP producer runtime."""

from __future__ import annotations

import base64
from collections.abc import Mapping
import csv
from dataclasses import dataclass
import hashlib
import io
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


class RuntimeIdentityError(RuntimeError):
    """The installed process does not match its authenticated runtime manifest."""


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
    leading = 0
    while leading < len(parts) and parts[leading] == "..":
        leading += 1
    return (
        leading > 0
        and len(parts) == leading + 2
        and parts[leading] == "bin"
        and parts[-1] not in {"", ".", ".."}
    )


def _checked_record_path(raw_path: str) -> tuple[PurePosixPath, bool]:
    if type(raw_path) is not str or not raw_path or "\x00" in raw_path:
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


def _reject_path_symlinks(base: Path, path: PurePosixPath, generated_bin: bool) -> None:
    if generated_bin:
        current = base
        for part in path.parts:
            if part == "..":
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

    seen_paths: set[str] = set()
    record_self_count = 0
    captured_file: bytes | None = None
    digest_lines: list[tuple[bytes, bytes]] = []
    for row in rows:
        if len(row) != 3:
            raise _identity_error("installed distribution RECORD row must have three fields")
        raw_path, hash_field, size_field = row
        if raw_path in seen_paths:
            raise _identity_error("installed distribution RECORD contains a duplicate path")
        seen_paths.add(raw_path)
        path, generated_bin = _checked_record_path(raw_path)
        target = Path(distribution.locate_file(path))
        _reject_path_symlinks(base, path, generated_bin)
        try:
            target_stat = os.lstat(target)
        except OSError as error:
            raise _identity_error("distribution RECORD target is missing", error)
        if stat.S_ISLNK(target_stat.st_mode):
            raise _identity_error("distribution RECORD target must not be a symlink")
        if stat.S_ISDIR(target_stat.st_mode):
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
        encoded_path = raw_path.encode("utf-8")
        digest_lines.append(
            (
                encoded_path,
                encoded_path + b"=sha256:" + file_hex.encode("ascii") + b"\n",
            )
        )
    if record_self_count != 1:
        raise _identity_error("installed distribution RECORD self-row is not unique")
    preimage = b"".join(
        line for _path, line in sorted(digest_lines, key=lambda item: item[0])
    )
    return _VerifiedTree(sha256_uri(preimage), captured_file)


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


def _capture_environment() -> RuntimeEnvironment:
    build_number, build_date = platform.python_build()
    cache_tag = sys.implementation.cache_tag
    soabi = sysconfig.get_config_var("SOABI")
    if type(cache_tag) is not str or type(soabi) is not str:
        raise _identity_error("CPython runtime metadata is unavailable")
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
