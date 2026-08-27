"""Command-line entry point for deterministic equipment-quality analysis."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import os
from pathlib import Path
import stat
import sys

from equipment_quality.artifacts import (
    compute_bundle_identity,
    compute_criteria_identity,
    write_bundle,
)
from equipment_quality.criteria_projection import build_criteria_projection
from equipment_quality.deterministic import sha256_uri
from equipment_quality.event_builder import build_replay_events
from equipment_quality.feature_roles import definitions
from equipment_quality.genealogy import build_genealogy
from equipment_quality.models import BundleWriteRequest, SummaryBuildRequest
from equipment_quality.operating_ranges import build_operating_ranges_result
from equipment_quality.quality_intervals import build_quality_rules_result
from equipment_quality.runtime_verify import verify_runtime
from equipment_quality.schema import (
    load_analysis_config,
    normative_schema_bytes,
    read_inputs,
)
from equipment_quality.summary import build_summary
from equipment_quality.time_split import build_time_split


_SCHEMA_ROLE_NAMES = (
    ("bundle_manifest", "bundle_manifest.schema.json"),
    ("analysis_config", "analysis_config.schema.json"),
    ("producer_runtime", "producer_runtime.schema.json"),
    ("equipment_operating_ranges", "equipment_operating_ranges.schema.json"),
    ("quality_risk_intervals", "quality_risk_intervals.schema.json"),
    ("analysis_summary", "analysis_summary.schema.json"),
    ("replay_events", "replay_event_row.schema.json"),
)
_CRITERIA_SCHEMA_ROLES = (
    "analysis_config",
    "equipment_operating_ranges",
    "producer_runtime",
    "quality_risk_intervals",
)


class _PathShapeError(ValueError):
    """An absolute input/output path fails the structural CLI contract."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self._print_message(f"error: {message}\n", sys.stderr)
        raise SystemExit(2)


def _path(path: Path, label: str) -> Path:
    try:
        candidate = Path(path)
    except (TypeError, ValueError) as error:
        raise _PathShapeError(f"{label} path is invalid") from error
    if not candidate.is_absolute():
        raise _PathShapeError(f"{label} path must be absolute")
    raw_path = os.fspath(candidate)
    if "\0" in raw_path:
        raise _PathShapeError(f"{label} path is invalid")
    try:
        os.fsencode(raw_path)
    except UnicodeError as error:
        raise _PathShapeError(f"{label} path is invalid") from error
    if ".." in candidate.parts:
        raise _PathShapeError(f"{label} path must not contain '..'")
    return candidate


def _lstat_path(
    path: Path,
    label: str,
    *,
    allow_missing_suffix: bool,
) -> os.stat_result | None:
    current = Path(path.anchor)
    components = (current,)
    for part in path.parts[1:]:
        current = current / part
        components += (current,)

    for index, component in enumerate(components):
        final = index == len(components) - 1
        try:
            metadata = os.lstat(component)
        except FileNotFoundError as error:
            if allow_missing_suffix:
                return None
            raise _PathShapeError(f"{label} path must exist") from error
        except (OSError, ValueError) as error:
            raise _PathShapeError(f"{label} path cannot be inspected") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise _PathShapeError(f"{label} path must not contain a symlink")
        if not final and not stat.S_ISDIR(metadata.st_mode):
            raise _PathShapeError(
                f"{label} path components must be directories"
            )
    return metadata


def _require_regular_file(path: Path, label: str) -> None:
    metadata = _lstat_path(path, label, allow_missing_suffix=False)
    if metadata is None or not stat.S_ISREG(metadata.st_mode):
        raise _PathShapeError(f"{label} path must be a regular file")


def _require_directory(path: Path, label: str, *, may_be_missing: bool) -> None:
    metadata = _lstat_path(path, label, allow_missing_suffix=may_be_missing)
    if metadata is not None and not stat.S_ISDIR(metadata.st_mode):
        raise _PathShapeError(f"{label} path must be a directory")


def _preflight_paths(
    config_path: Path,
    runtime_path: Path,
    data_dir: Path,
    output_dir: Path,
) -> tuple[Path, Path, Path, Path]:
    config = _path(config_path, "config")
    runtime = _path(runtime_path, "runtime")
    data = _path(data_dir, "data")
    output = _path(output_dir, "output")
    _require_regular_file(config, "config")
    _require_regular_file(runtime, "runtime")
    _require_directory(data, "data", may_be_missing=False)
    _require_directory(output, "output", may_be_missing=True)
    return config, runtime, data, output


def _parser() -> _ArgumentParser:
    parser = _ArgumentParser(
        prog="sfep-equipment-quality",
        description="Publish one authenticated equipment-quality Bundle.",
        allow_abbrev=False,
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--runtime-manifest", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def _concise_error(error: Exception) -> str:
    text = " ".join(str(error).split())
    return text or type(error).__name__


def _read_config_bytes(path: Path) -> bytes:
    return path.read_bytes()


def run_analysis(
    config_path: Path,
    runtime_path: Path,
    data_dir: Path,
    output_dir: Path,
) -> Path:
    """Run one authenticated analysis and publish its immutable Bundle."""
    config, runtime, data, output = _preflight_paths(
        config_path,
        runtime_path,
        data_dir,
        output_dir,
    )

    runtime_identity = verify_runtime(runtime)
    producer_runtime_bytes = runtime_identity.manifest_bytes
    analysis_config_bytes = _read_config_bytes(config)
    analysis_config = load_analysis_config(config)
    inputs = read_inputs(data)
    genealogy = build_genealogy(inputs)
    split = build_time_split(genealogy, analysis_config)
    configured_definitions = definitions(analysis_config)

    schema_digests = {
        role: sha256_uri(normative_schema_bytes(schema_name))
        for role, schema_name in _SCHEMA_ROLE_NAMES
    }
    projection = build_criteria_projection(split, configured_definitions)
    criteria_identity = compute_criteria_identity(
        split.as_of,
        projection,
        analysis_config_bytes,
        producer_runtime_bytes,
        schema_digests={
            role: schema_digests[role] for role in _CRITERIA_SCHEMA_ROLES
        },
    )

    material_catalog = tuple(
        genealogy.lineage_rows["material_lineage"].tolist()
    )
    operating_ranges = build_operating_ranges_result(
        split,
        configured_definitions,
        analysis_config,
        material_catalog=material_catalog,
    )
    quality_rules = build_quality_rules_result(
        split,
        configured_definitions,
        analysis_config,
        criteria_identity.value,
        material_catalog=material_catalog,
    )
    bundle_identity = compute_bundle_identity(
        criteria_identity.value,
        analysis_config_bytes,
        producer_runtime_bytes,
        inputs.sources,
        schema_digests=schema_digests,
    )
    events = tuple(
        build_replay_events(
            genealogy,
            bundle_identity.value,
            criteria_identity.value,
            analysis_config,
        )
    )
    summary_request = SummaryBuildRequest(
        analysis_config=analysis_config,
        analysis_config_bytes=analysis_config_bytes,
        producer_runtime_bytes=producer_runtime_bytes,
        schema_digests=schema_digests,
        inputs=inputs,
        genealogy=genealogy,
        split=split,
        material_catalog=material_catalog,
        definitions=configured_definitions,
        criteria_identity=criteria_identity,
        bundle_identity=bundle_identity,
        operating_ranges=operating_ranges,
        quality_rules=quality_rules,
        events=events,
    )
    analysis_summary = build_summary(summary_request)
    range_envelope = {
        "schemaVersion": "sfep-operating-ranges/v1",
        "asOf": split.as_of.isoformat(),
        "criteriaId": criteria_identity.value,
        "ranges": operating_ranges.to_wire(),
    }
    rule_envelope = {
        "schemaVersion": "sfep-quality-rules/v1",
        "asOf": split.as_of.isoformat(),
        "criteriaId": criteria_identity.value,
        "rules": quality_rules.to_wire(),
    }
    bundle_request = BundleWriteRequest(
        output_root=output,
        analysis_config=analysis_config_bytes,
        producer_runtime=producer_runtime_bytes,
        equipment_operating_ranges=range_envelope,
        quality_risk_intervals=rule_envelope,
        replay_events=events,
        analysis_summary=analysis_summary,
        criteria_identity=criteria_identity,
        bundle_identity=bundle_identity,
        sources=inputs.sources,
        schema_digests=schema_digests,
        as_of=split.as_of,
        timezone=analysis_config.timezone,
        label_maturity_days=analysis_config.label_maturity_days,
    )
    return write_bundle(bundle_request)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the public CLI, report one result, and return a process status."""
    try:
        arguments = _parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code) if isinstance(error.code, int) else 2

    try:
        final = run_analysis(
            arguments.config,
            arguments.runtime_manifest,
            arguments.data_dir,
            arguments.output_dir,
        )
    except _PathShapeError as error:
        print(f"error: {_concise_error(error)}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"error: {_concise_error(error)}", file=sys.stderr)
        return 1
    print(final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
