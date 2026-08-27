"""Identity and atomic artifact publication for equipment-quality bundles."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
import errno
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from types import MappingProxyType

from equipment_quality.deterministic import canonical_json_bytes, id_lines, sha256_uri
from equipment_quality.event_builder import serialize_replay_events
from equipment_quality.models import BundleWriteRequest, Identity, SourceFile
from equipment_quality.schema import normative_schema_bytes, validate_normative_instance


_CRITERIA_SCHEMA_ROLES = (
    "analysis_config",
    "equipment_operating_ranges",
    "producer_runtime",
    "quality_risk_intervals",
)
_BUNDLE_SCHEMA_ROLES = (
    "analysis_config",
    "analysis_summary",
    "bundle_manifest",
    "equipment_operating_ranges",
    "producer_runtime",
    "quality_risk_intervals",
    "replay_events",
)
_SOURCE_NAMES = {
    "sm_cc": "sts_1sm_cc_1.csv",
    "fur_hr": "sts_2fur_hr_2.csv",
    "ap": "sts_3ap_3.csv",
}
_SOURCE_ORDER = tuple(_SOURCE_NAMES)

FIXED_ARTIFACT_ROLES = (
    "analysis_config",
    "producer_runtime",
    "equipment_operating_ranges",
    "quality_risk_intervals",
    "replay_events",
    "analysis_summary",
)
FIXED_ARTIFACT_FILENAMES = MappingProxyType(
    {
        "analysis_config": "analysis_config.json",
        "producer_runtime": "producer_runtime.json",
        "equipment_operating_ranges": "equipment_operating_ranges.json",
        "quality_risk_intervals": "quality_risk_intervals.json",
        "replay_events": "replay_events.csv",
        "analysis_summary": "analysis_summary.json",
    }
)
_ARTIFACT_SCHEMA_NAMES = {
    "analysis_config": "analysis_config.schema.json",
    "producer_runtime": "producer_runtime.schema.json",
    "equipment_operating_ranges": "equipment_operating_ranges.schema.json",
    "quality_risk_intervals": "quality_risk_intervals.schema.json",
    "replay_events": "replay_event_row.schema.json",
    "analysis_summary": "analysis_summary.schema.json",
    "bundle_manifest": "bundle_manifest.schema.json",
}
_ARTIFACT_SCHEMA_VERSIONS = {
    "analysis_config": "sfep-analysis-config/v1",
    "producer_runtime": "sfep-producer-runtime/v1",
    "equipment_operating_ranges": "sfep-operating-ranges/v1",
    "quality_risk_intervals": "sfep-quality-rules/v1",
    "replay_events": "sfep-replay-events/v1",
    "analysis_summary": "sfep-analysis-summary/v1",
}
_LOCK_NAME = ".sfep-equipment-quality.lock"
_TEMP_PREFIX = ".sfep-bundle-tmp-"
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600


class BundleLockError(RuntimeError):
    """Another process holds the output-root publication lock."""


class NonDeterministicBundleError(RuntimeError):
    """A complete path for the same input identity differs in bytes or shape."""


class PublishDurabilityError(RuntimeError):
    """The final directory is visible but parent-directory durability is unknown."""


class _BundleVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class _PreparedBundle:
    artifact_bytes: Mapping[str, bytes]
    manifest_bytes: bytes


def _exact_bytes(value: object, label: str) -> bytes:
    if type(value) is not bytes:
        raise TypeError(f"{label} must be built-in bytes")
    return value


def _sha256_value(value: object, label: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a built-in string")
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must use the lowercase sha256 URI form")
    return value


def _mapping_snapshot(value: object, label: str) -> tuple[tuple[str, object], ...]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    try:
        entries = tuple(value.items())
    except (AttributeError, TypeError, ValueError) as error:
        raise TypeError(f"{label} must expose one stable item sequence") from error
    seen: set[str] = set()
    checked: list[tuple[str, object]] = []
    for entry in entries:
        if type(entry) is not tuple or len(entry) != 2:
            raise TypeError(f"{label} items must be key-value pairs")
        key, item = entry
        if type(key) is not str:
            raise TypeError(f"{label} keys must be built-in strings")
        if key in seen:
            raise ValueError(f"{label} roles must be unique")
        seen.add(key)
        checked.append((key, item))
    return tuple(checked)


def _schema_snapshot(
    schema_digests: object,
    expected_roles: tuple[str, ...],
) -> dict[str, str]:
    entries = _mapping_snapshot(schema_digests, "schema digests")
    if {key for key, _ in entries} != set(expected_roles) or len(entries) != len(
        expected_roles
    ):
        raise ValueError(f"schema roles must be exactly {expected_roles!r}")
    return {
        key: _sha256_value(value, f"schema digest for {key}")
        for key, value in entries
    }


def _identity(version: str, fields: dict[str, str]) -> Identity:
    value = sha256_uri(id_lines(version, fields))
    return Identity(value=value, version=version, fields=fields)


def compute_criteria_identity(
    as_of: date,
    criteria_projection: bytes,
    analysis_config: bytes,
    producer_runtime: bytes,
    schema_digests: Mapping[str, str],
) -> Identity:
    """Compute the exact eight-field criteria identity."""
    if type(as_of) is not date:
        raise TypeError("as_of must be an exact date")
    projection = _exact_bytes(criteria_projection, "criteria_projection")
    config = _exact_bytes(analysis_config, "analysis_config")
    runtime = _exact_bytes(producer_runtime, "producer_runtime")
    schemas = _schema_snapshot(schema_digests, _CRITERIA_SCHEMA_ROLES)
    fields = {
        "as_of": as_of.isoformat(),
        "criteria_projection_sha256": sha256_uri(projection),
        "analysis_config_sha256": sha256_uri(config),
        "producer_runtime_sha256": sha256_uri(runtime),
        **{
            f"schema.{role}.sha256": schemas[role]
            for role in _CRITERIA_SCHEMA_ROLES
        },
    }
    if len(fields) != 8:
        raise RuntimeError("criteria identity must contain exactly eight fields")
    return _identity("sfep-criteria-id/v1", fields)


def compute_bundle_identity(
    criteria_id: str,
    analysis_config: bytes,
    producer_runtime: bytes,
    sources: Sequence[SourceFile],
    schema_digests: Mapping[str, str],
) -> Identity:
    """Compute the exact nineteen-field bundle identity."""
    criteria = _sha256_value(criteria_id, "criteria_id")
    config = _exact_bytes(analysis_config, "analysis_config")
    runtime = _exact_bytes(producer_runtime, "producer_runtime")
    schemas = _schema_snapshot(schema_digests, _BUNDLE_SCHEMA_ROLES)
    if type(sources) is str:
        raise TypeError("sources must be a sequence")
    try:
        source_snapshot = tuple(sources)
    except TypeError as error:
        raise TypeError("sources must be a sequence") from error
    if len(source_snapshot) != 3 or tuple(
        source.role if type(source) is SourceFile else None for source in source_snapshot
    ) != _SOURCE_ORDER:
        raise ValueError(f"source roles must be exactly {_SOURCE_ORDER!r} in order")

    source_fields: dict[str, str] = {}
    for source in source_snapshot:
        if type(source) is not SourceFile:
            raise TypeError("sources must contain exact SourceFile values")
        if type(source.role) is not str or type(source.name) is not str:
            raise TypeError("source role and name must be built-in strings")
        if source.name != _SOURCE_NAMES[source.role]:
            raise ValueError(f"source name for {source.role} must be fixed")
        if type(source.size_bytes) is not int or source.size_bytes < 0:
            raise TypeError("source size must be a non-negative built-in integer")
        digest = _sha256_value(source.sha256, f"source {source.role} sha256")
        prefix = f"source.{source.role}"
        source_fields[f"{prefix}.name"] = source.name
        source_fields[f"{prefix}.size_bytes"] = str(source.size_bytes)
        source_fields[f"{prefix}.sha256"] = digest

    fields = {
        "criteria_id": criteria,
        "analysis_config_sha256": sha256_uri(config),
        "producer_runtime_sha256": sha256_uri(runtime),
        **source_fields,
        **{
            f"schema.{role}.sha256": schemas[role]
            for role in _BUNDLE_SCHEMA_ROLES
        },
    }
    if len(fields) != 19:
        raise RuntimeError("bundle identity must contain exactly nineteen fields")
    return _identity("sfep-bundle-id/v1", fields)


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def _canonical_mapping_bytes(
    value: Mapping[str, object],
    schema_name: str,
) -> bytes:
    plain = _plain_json(value)
    if not isinstance(plain, dict):
        raise TypeError("JSON artifact payload must be an object")
    payload = canonical_json_bytes(plain)
    validate_normative_instance(schema_name, plain)
    return payload


def _validate_exact_json_bytes(payload: bytes, schema_name: str) -> dict[str, object]:
    try:
        decoded = payload.decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{schema_name} bytes must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{schema_name} top level must be an object")
    if canonical_json_bytes(value) != payload:
        raise ValueError(f"{schema_name} bytes must use canonical JSON")
    validate_normative_instance(schema_name, value)
    return value


def _validate_schema_descriptors(request: BundleWriteRequest) -> None:
    for role, schema_name in _ARTIFACT_SCHEMA_NAMES.items():
        expected = sha256_uri(normative_schema_bytes(schema_name))
        if request.schema_digests.get(role) != expected:
            raise ValueError(f"schema digest for {role} does not match normative bytes")


def _prepare_bundle(request: BundleWriteRequest) -> _PreparedBundle:
    if type(request) is not BundleWriteRequest:
        raise TypeError("request must be an exact BundleWriteRequest")
    _validate_schema_descriptors(request)
    _validate_exact_json_bytes(
        request.analysis_config, _ARTIFACT_SCHEMA_NAMES["analysis_config"]
    )
    _validate_exact_json_bytes(
        request.producer_runtime, _ARTIFACT_SCHEMA_NAMES["producer_runtime"]
    )
    ranges = _canonical_mapping_bytes(
        request.equipment_operating_ranges,
        _ARTIFACT_SCHEMA_NAMES["equipment_operating_ranges"],
    )
    rules = _canonical_mapping_bytes(
        request.quality_risk_intervals,
        _ARTIFACT_SCHEMA_NAMES["quality_risk_intervals"],
    )
    # Task 7 performs the one and only full replay-row validation in this call.
    replay = serialize_replay_events(request.replay_events)
    summary = _canonical_mapping_bytes(
        request.analysis_summary,
        _ARTIFACT_SCHEMA_NAMES["analysis_summary"],
    )
    artifact_bytes = {
        "analysis_config": request.analysis_config,
        "producer_runtime": request.producer_runtime,
        "equipment_operating_ranges": ranges,
        "quality_risk_intervals": rules,
        "replay_events": replay,
        "analysis_summary": summary,
    }
    metadata = [
        {
            "role": role,
            "schemaVersion": _ARTIFACT_SCHEMA_VERSIONS[role],
            "sha256": sha256_uri(artifact_bytes[role]),
            "sizeBytes": len(artifact_bytes[role]),
        }
        for role in FIXED_ARTIFACT_ROLES
    ]
    manifest = {
        "artifacts": metadata,
        "asOf": request.as_of.isoformat(),
        "bundleId": request.bundle_identity.value,
        "criteriaId": request.criteria_identity.value,
        "criteriaIdentity": dict(request.criteria_identity.fields),
        "identity": dict(request.bundle_identity.fields),
        "labelMaturityDays": request.label_maturity_days,
        "schemaVersion": "sfep-equipment-bundle/v1",
        "timezone": request.timezone,
    }
    manifest_bytes = canonical_json_bytes(manifest)
    validate_normative_instance(
        _ARTIFACT_SCHEMA_NAMES["bundle_manifest"], manifest
    )
    return _PreparedBundle(MappingProxyType(artifact_bytes), manifest_bytes)


def _validate_output_root(output_root: Path) -> None:
    if not output_root.is_absolute():
        raise ValueError("output root must be absolute")
    current = output_root
    while current != current.parent:
        try:
            component_stat = current.lstat()
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(component_stat.st_mode):
                raise ValueError("output root path must not contain a symlink")
        current = current.parent
    try:
        root_stat = output_root.lstat()
    except FileNotFoundError:
        output_root.mkdir(parents=True, mode=_DIRECTORY_MODE)
        root_stat = output_root.lstat()
    if stat.S_ISLNK(root_stat.st_mode):
        raise ValueError("output root must not be a symlink")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError("output root must be a directory")


@contextmanager
def output_lock(output_root: Path):
    """Hold the nonblocking output-root lock for one complete publication."""
    if not isinstance(output_root, Path):
        raise TypeError("output_root must be a pathlib Path")
    root = Path(str(output_root))
    _validate_output_root(root)
    lock_path = root / _LOCK_NAME
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, _FILE_MODE)
    try:
        lock_stat = os.fstat(descriptor)
        if not stat.S_ISREG(lock_stat.st_mode):
            raise ValueError("output lock must be a regular file")
        os.fchmod(descriptor, _FILE_MODE)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                raise BundleLockError("output root is locked") from error
            raise
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def fsync_file(descriptor: int, phase: str) -> None:
    """Durably flush one artifact; phase is exposed for deterministic fault tests."""
    if type(descriptor) is not int or type(phase) is not str:
        raise TypeError("fsync_file arguments have invalid types")
    os.fsync(descriptor)


def fsync_directory(path: Path) -> None:
    """Durably flush directory entries without following a symlink."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        directory_stat = os.fstat(descriptor)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise ValueError("fsync target must be a directory")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_rename(source: Path, target: Path) -> None:
    """Perform the same-filesystem visibility transition."""
    os.rename(source, target)


def _write_artifact_file(path: Path, payload: bytes, phase: str) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, _FILE_MODE)
    try:
        os.fchmod(descriptor, _FILE_MODE)
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("artifact write made no progress")
            offset += written
        fsync_file(descriptor, phase)
    finally:
        os.close(descriptor)


def _read_regular_file(path: Path) -> bytes:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise _BundleVerificationError("bundle artifact must be a non-symlink regular file")
    if stat.S_IMODE(before.st_mode) != _FILE_MODE:
        raise _BundleVerificationError("bundle artifact mode is invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (before.st_dev, before.st_ino):
            raise _BundleVerificationError("bundle artifact changed while opening")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    after = path.lstat()
    if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
        raise _BundleVerificationError("bundle artifact changed while reading")
    return b"".join(chunks)


def _verify_against_prepared(
    bundle_root: Path,
    request: BundleWriteRequest,
    prepared: _PreparedBundle,
) -> None:
    if bundle_root.parent != request.output_root:
        raise _BundleVerificationError("bundle root must be a direct output-root child")
    if bundle_root.parent.resolve(strict=True) != request.output_root.resolve(strict=True):
        raise _BundleVerificationError("bundle root parent escapes the output root")
    root_stat = bundle_root.lstat()
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise _BundleVerificationError("bundle root must be a non-symlink directory")
    if stat.S_IMODE(root_stat.st_mode) != _DIRECTORY_MODE:
        raise _BundleVerificationError("bundle root mode is invalid")
    if bundle_root.name != request.bundle_identity.value:
        raise _BundleVerificationError("bundle path does not match request bundle ID")
    expected_names = {
        *FIXED_ARTIFACT_FILENAMES.values(),
        "bundle_manifest.json",
    }
    actual_names = {entry.name for entry in os.scandir(bundle_root)}
    if actual_names != expected_names:
        raise _BundleVerificationError("bundle has missing or unexpected files")

    actual: dict[str, bytes] = {}
    for role in FIXED_ARTIFACT_ROLES:
        actual[role] = _read_regular_file(
            bundle_root / FIXED_ARTIFACT_FILENAMES[role]
        )
        if actual[role] != prepared.artifact_bytes[role]:
            raise _BundleVerificationError("bundle artifact bytes differ")
    manifest_bytes = _read_regular_file(bundle_root / "bundle_manifest.json")
    if manifest_bytes != prepared.manifest_bytes:
        raise _BundleVerificationError("bundle manifest bytes differ")

    _validate_exact_json_bytes(
        actual["analysis_config"], _ARTIFACT_SCHEMA_NAMES["analysis_config"]
    )
    _validate_exact_json_bytes(
        actual["producer_runtime"], _ARTIFACT_SCHEMA_NAMES["producer_runtime"]
    )
    _validate_exact_json_bytes(
        actual["equipment_operating_ranges"],
        _ARTIFACT_SCHEMA_NAMES["equipment_operating_ranges"],
    )
    _validate_exact_json_bytes(
        actual["quality_risk_intervals"],
        _ARTIFACT_SCHEMA_NAMES["quality_risk_intervals"],
    )
    _validate_exact_json_bytes(
        actual["analysis_summary"], _ARTIFACT_SCHEMA_NAMES["analysis_summary"]
    )
    manifest = _validate_exact_json_bytes(
        manifest_bytes, _ARTIFACT_SCHEMA_NAMES["bundle_manifest"]
    )
    if manifest["bundleId"] != request.bundle_identity.value:
        raise _BundleVerificationError("manifest bundle ID differs from request")
    for metadata, role in zip(manifest["artifacts"], FIXED_ARTIFACT_ROLES, strict=True):
        if (
            metadata["role"] != role
            or metadata["sizeBytes"] != len(actual[role])
            or metadata["sha256"] != sha256_uri(actual[role])
        ):
            raise _BundleVerificationError("manifest artifact metadata is inconsistent")


def verify_complete_bundle(bundle_root: Path, request: BundleWriteRequest) -> bool:
    """Read-only exact verifier for a proposed complete bundle directory."""
    prepared = _prepare_bundle(request)
    try:
        if not isinstance(bundle_root, Path) or not bundle_root.is_absolute():
            return False
        _verify_against_prepared(Path(str(bundle_root)), request, prepared)
    except (OSError, TypeError, ValueError, KeyError):
        return False
    return True


def _remove_temp_directory(path: Path | None) -> None:
    if path is None:
        return
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(current.st_mode):
        path.unlink()
    elif stat.S_ISDIR(current.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _durability_unknown(error: OSError) -> PublishDurabilityError:
    return PublishDurabilityError(
        "PUBLISH_DURABILITY_UNKNOWN: visible bundle is complete but parent fsync failed"
    )


def write_bundle(request: BundleWriteRequest) -> Path:
    """Validate, durably write, and atomically expose one immutable bundle."""
    if type(request) is not BundleWriteRequest:
        raise TypeError("request must be an exact BundleWriteRequest")
    with output_lock(request.output_root):
        prepared = _prepare_bundle(request)
        final = request.output_root / request.bundle_identity.value
        if final.exists() or final.is_symlink():
            try:
                _verify_against_prepared(final, request, prepared)
            except (OSError, TypeError, ValueError, KeyError) as error:
                raise NonDeterministicBundleError(
                    "NON_DETERMINISTIC_BUNDLE: existing bundle is not byte-identical"
                ) from error
            try:
                fsync_directory(request.output_root)
            except OSError as error:
                raise _durability_unknown(error) from error
            return final

        temp: Path | None = Path(
            tempfile.mkdtemp(prefix=_TEMP_PREFIX, dir=request.output_root)
        )
        if temp.parent != request.output_root:
            _remove_temp_directory(temp)
            raise ValueError("temporary bundle must be a direct output-root child")
        os.chmod(temp, _DIRECTORY_MODE)
        try:
            for role in FIXED_ARTIFACT_ROLES:
                artifact_path = temp / FIXED_ARTIFACT_FILENAMES[role]
                _write_artifact_file(
                    artifact_path,
                    prepared.artifact_bytes[role],
                    role,
                )
                if _read_regular_file(artifact_path) != prepared.artifact_bytes[role]:
                    raise ValueError(f"written artifact bytes differ for {role}")
            manifest_path = temp / "bundle_manifest.json"
            _write_artifact_file(
                manifest_path,
                prepared.manifest_bytes,
                "bundle_manifest",
            )
            if _read_regular_file(manifest_path) != prepared.manifest_bytes:
                raise ValueError("written artifact bytes differ for bundle_manifest")
            fsync_directory(temp)
            atomic_rename(temp, final)
            temp = None
        except BaseException:
            _remove_temp_directory(temp)
            raise

        try:
            fsync_directory(request.output_root)
        except OSError as error:
            try:
                _verify_against_prepared(final, request, prepared)
            except Exception as verification_error:
                raise _BundleVerificationError(
                    "visible final bundle failed post-rename verification"
                ) from verification_error
            raise _durability_unknown(error) from error
        return final
