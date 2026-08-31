"""CLI orchestration and fail-closed path-boundary tests."""

from __future__ import annotations

from collections.abc import Sequence
import csv
from datetime import date
import hashlib
import io
import importlib
import inspect
import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import get_type_hints

import pytest
from jsonschema.exceptions import ValidationError

from equipment_quality import cli
import equipment_quality.schema as schema_module
from equipment_quality.bundle_contract import V1_CONTRACT, V2_CONTRACT
from equipment_quality.deterministic import canonical_json_bytes, sha256_uri
from equipment_quality.runtime_verify import RuntimeIdentity
from equipment_quality.schema import validate_normative_instance
from factories.artifacts import (
    LITERAL_GOLDEN_ARTIFACT_SNAPSHOT,
    LITERAL_GOLDEN_BUNDLE_ID,
    runtime_payload,
)


SCHEMA_ROLE_NAMES = (
    ("bundle_manifest", "bundle_manifest.schema.json"),
    ("analysis_config", "analysis_config.schema.json"),
    ("producer_runtime", "producer_runtime.schema.json"),
    ("equipment_operating_ranges", "equipment_operating_ranges.schema.json"),
    ("quality_risk_intervals", "quality_risk_intervals.schema.json"),
    ("analysis_summary", "analysis_summary.schema.json"),
    ("replay_events", "replay_event_row.schema.json"),
)
SCHEMA_ROLES = tuple(sorted((role for role, _ in SCHEMA_ROLE_NAMES), key=str.encode))
CRITERIA_SCHEMA_ROLES = (
    "analysis_config",
    "equipment_operating_ranges",
    "producer_runtime",
    "quality_risk_intervals",
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_SOURCE = REPOSITORY_ROOT / "contracts/equipment-monitor/v1/golden-source"


def test_cli_public_api_is_importable() -> None:
    """Removing either required public callable must break the CLI contract."""
    module = importlib.import_module("equipment_quality.cli")

    assert callable(module.run_analysis)
    assert callable(module.main)


def test_cli_public_signatures_are_exact() -> None:
    """Changing either parameter surface breaks the documented producer API."""
    run_signature = inspect.signature(cli.run_analysis)
    main_signature = inspect.signature(cli.main)

    assert tuple(run_signature.parameters) == (
        "config_path",
        "runtime_path",
        "data_dir",
        "output_dir",
    )
    assert get_type_hints(cli.run_analysis) == {
        "config_path": Path,
        "runtime_path": Path,
        "data_dir": Path,
        "output_dir": Path,
        "return": Path,
    }
    assert tuple(main_signature.parameters) == ("argv",)
    assert main_signature.parameters["argv"].default is None
    assert get_type_hints(cli.main) == {
        "argv": Sequence[str] | None,
        "return": int,
    }


def test_v2_config_strictly_selects_one_closed_contract_and_profile() -> None:
    config_bytes = (REPOSITORY_ROOT / "analysis/analysis_config_v2.json").read_bytes()

    contract, config = schema_module.load_analysis_config_contract(config_bytes)

    assert contract is V2_CONTRACT
    assert config.schema_version == V2_CONTRACT.config_version

    mixed = json.loads(config_bytes)
    mixed["schemaVersion"] = V1_CONTRACT.config_version
    with pytest.raises(ValidationError, match="quality-analysis-v1"):
        schema_module.load_analysis_config_contract(canonical_json_bytes(mixed))


@pytest.mark.parametrize(
    "payload",
    (
        b'{"schemaVersion":"sfep-analysis-config/v3"}\n',
        b'{"schemaVersion":7}\n',
        b'{"schemaVersion":"sfep-analysis-config/v2","schemaVersion":"sfep-analysis-config/v1"}\n',
    ),
)
def test_config_contract_selection_rejects_unknown_wrong_type_and_duplicate_versions(
    payload: bytes,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        schema_module.load_analysis_config_contract(payload)


def _valid_path_arguments(tmp_path: Path) -> dict[str, Path]:
    config = tmp_path / "analysis_config.json"
    runtime = tmp_path / "producer_runtime.json"
    data = tmp_path / "source-data"
    output = tmp_path / "missing" / "bundle-output"
    config.write_bytes(b"{}\n")
    runtime.write_bytes(b"{}\n")
    data.mkdir()
    return {
        "config": config,
        "runtime": runtime,
        "data": data,
        "output": output,
    }


def _argv(paths: dict[str, Path | str]) -> list[str]:
    return [
        "--config",
        str(paths["config"]),
        "--runtime-manifest",
        str(paths["runtime"]),
        "--data-dir",
        str(paths["data"]),
        "--output-dir",
        str(paths["output"]),
    ]


def _assert_output_unpublished(path: Path) -> None:
    if path.is_symlink():
        target = path.readlink()
        target = target if target.is_absolute() else path.parent / target
        assert list(target.iterdir()) == []
        return
    assert not path.exists()


def test_cli_help_lists_only_the_four_public_path_flags(capsys) -> None:
    """Removing a required flag or exposing an unreviewed flag breaks the CLI."""
    option_strings = {
        option
        for action in cli._parser()._actions
        for option in action.option_strings
    }
    code = cli.main(["--help"])
    captured = capsys.readouterr()

    assert option_strings == {
        "-h",
        "--help",
        "--config",
        "--runtime-manifest",
        "--data-dir",
        "--output-dir",
    }
    assert code == 0
    assert captured.err == ""
    for flag in ("--config", "--runtime-manifest", "--data-dir", "--output-dir"):
        assert flag in captured.out
    assert "--version" not in captured.out


@pytest.mark.parametrize("arguments", [[], ["--unknown", "value"]])
def test_cli_argument_errors_return_two_without_traceback(arguments, capsys) -> None:
    """Missing/unknown arguments must remain deterministic usage errors."""
    code = cli.main(arguments)
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert captured.err.count("Traceback") == 0
    assert captured.err.strip()


@pytest.mark.parametrize(
    ("exact_flag", "abbreviation"),
    [
        ("--config", "--conf"),
        ("--runtime-manifest", "--runtime-m"),
        ("--data-dir", "--data"),
        ("--output-dir", "--output"),
    ],
)
def test_cli_rejects_every_undocumented_long_flag_prefix_before_pipeline(
    tmp_path,
    monkeypatch,
    capsys,
    exact_flag,
    abbreviation,
) -> None:
    """Argparse abbreviation must not silently expand the reviewed CLI surface."""
    paths = _valid_path_arguments(tmp_path)
    arguments = _argv(paths)
    arguments[arguments.index(exact_flag)] = abbreviation
    pipeline_calls: list[tuple[object, ...]] = []

    def forbidden_pipeline(*args):
        pipeline_calls.append(args)
        return paths["output"] / "unexpected-bundle"

    monkeypatch.setattr(cli, "run_analysis", forbidden_pipeline)

    code = cli.main(arguments)
    captured = capsys.readouterr()

    assert code == 2
    assert pipeline_calls == []
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert captured.err.strip()
    _assert_output_unpublished(paths["output"])


def test_cli_exact_long_flags_still_invoke_pipeline_once(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """Disabling abbreviations must preserve the four exact public long flags."""
    paths = _valid_path_arguments(tmp_path)
    final = paths["output"] / ("sha256:" + "a" * 64)
    pipeline_calls: list[tuple[object, ...]] = []

    def pipeline(*args):
        pipeline_calls.append(args)
        return final

    monkeypatch.setattr(cli, "run_analysis", pipeline)

    code = cli.main(_argv(paths))
    captured = capsys.readouterr()

    assert code == 0
    assert pipeline_calls == [
        (paths["config"], paths["runtime"], paths["data"], paths["output"])
    ]
    assert captured.out == f"{final}\n"
    assert captured.err == ""


def test_cli_rejects_all_relative_paths_before_side_effects(tmp_path, capsys) -> None:
    """Accepting a relative path would make bundle inputs depend on the cwd."""
    output = tmp_path / "out"
    code = cli.main(
        [
            "--config",
            "relative.json",
            "--runtime-manifest",
            "runtime.json",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "absolute" in captured.err.lower()
    assert captured.err.count("Traceback") == 0
    assert not output.exists()


def test_cli_treats_embedded_nul_as_a_path_shape_error(tmp_path, capsys) -> None:
    """An invalid absolute pathname must not be misclassified as analysis failure."""
    paths = _valid_path_arguments(tmp_path)
    paths["config"] = Path(f"{tmp_path}/\0invalid-config.json")

    code = cli.main(_argv(paths))
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert "config" in captured.err.lower()
    assert "Traceback" not in captured.err
    _assert_output_unpublished(paths["output"])


@pytest.mark.parametrize(
    "invalid_component",
    ["\0invalid", "\ud800invalid", ".."],
    ids=["embedded-nul", "unencodable-surrogate", "parent-segment"],
)
def test_cli_lexically_rejects_invalid_missing_output_suffix_before_content(
    tmp_path,
    monkeypatch,
    capsys,
    invalid_component,
) -> None:
    """A missing ancestor must not hide malformed components later in output path."""
    paths = _valid_path_arguments(tmp_path)
    missing_ancestor = tmp_path / "absent-output-ancestor"
    paths["output"] = missing_ancestor / invalid_component / "bundle-output"
    content_calls: list[Path] = []

    def forbidden_runtime(path):
        content_calls.append(path)
        raise AssertionError("runtime verification ran before output syntax validation")

    monkeypatch.setattr(cli, "verify_runtime", forbidden_runtime)

    code = cli.main(_argv(paths))
    captured = capsys.readouterr()

    assert code == 2
    assert content_calls == []
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert "output" in captured.err.lower()
    assert not missing_ancestor.exists()


def test_output_preflight_never_lstats_below_first_valid_missing_ancestor(
    tmp_path,
    monkeypatch,
) -> None:
    """Lexical suffix validation must not probe filesystem children that cannot exist."""
    paths = _valid_path_arguments(tmp_path)
    missing_ancestor = tmp_path / "valid-missing-ancestor"
    paths["output"] = missing_ancestor / "nested" / "bundle-output"
    _install_stubbed_pipeline(monkeypatch, paths["output"])
    original_lstat = cli.os.lstat
    inspected: list[Path] = []

    def recording_lstat(path):
        inspected.append(Path(path))
        return original_lstat(path)

    monkeypatch.setattr(cli.os, "lstat", recording_lstat)

    final = cli.run_analysis(
        paths["config"], paths["runtime"], paths["data"], paths["output"]
    )

    assert final == paths["output"] / "bundle-id"
    assert missing_ancestor in inspected
    assert missing_ancestor / "nested" not in inspected
    assert paths["output"] not in inspected


@pytest.mark.parametrize("relative_role", ["config", "runtime", "data", "output"])
def test_cli_rejects_each_relative_path_independently(
    tmp_path,
    capsys,
    relative_role,
) -> None:
    """No one path may bypass the all-absolute contract."""
    paths = _valid_path_arguments(tmp_path)
    paths[relative_role] = Path(f"relative-{relative_role}")

    code = cli.main(_argv(paths))
    captured = capsys.readouterr()

    assert code == 2
    assert relative_role in captured.err.lower()
    assert "absolute" in captured.err.lower()
    _assert_output_unpublished(tmp_path / "missing" / "bundle-output")


@pytest.mark.parametrize("missing_role", ["config", "runtime", "data"])
def test_cli_rejects_each_missing_required_input(tmp_path, capsys, missing_role) -> None:
    """A missing input must fail before the output-root ancestor is created."""
    paths = _valid_path_arguments(tmp_path)
    path = paths[missing_role]
    path.unlink() if path.is_file() else path.rmdir()

    code = cli.main(_argv(paths))
    captured = capsys.readouterr()

    assert code == 2
    assert missing_role in captured.err.lower()
    _assert_output_unpublished(paths["output"])


@pytest.mark.parametrize("missing_role", ["config", "runtime", "data"])
def test_cli_rejects_a_missing_intermediate_input_component(
    tmp_path,
    capsys,
    missing_role,
) -> None:
    """A missing parent cannot be treated as a creatable analysis input."""
    paths = _valid_path_arguments(tmp_path)
    paths[missing_role] = tmp_path / "absent-parent" / paths[missing_role].name

    code = cli.main(_argv(paths))
    captured = capsys.readouterr()

    assert code == 2
    assert missing_role in captured.err.lower()
    assert "exist" in captured.err.lower()
    _assert_output_unpublished(paths["output"])


@pytest.mark.parametrize("wrong_role", ["config", "runtime", "data", "output"])
def test_cli_rejects_each_wrong_path_kind(tmp_path, capsys, wrong_role) -> None:
    """Regular-file and directory roles may not be interchanged."""
    paths = _valid_path_arguments(tmp_path)
    path = paths[wrong_role]
    if wrong_role in {"config", "runtime"}:
        path.unlink()
        path.mkdir()
    elif wrong_role == "data":
        path.rmdir()
        path.write_bytes(b"not a directory")
    else:
        path.parent.mkdir()
        path.write_bytes(b"not a directory")

    code = cli.main(_argv(paths))
    captured = capsys.readouterr()

    assert code == 2
    assert wrong_role in captured.err.lower()
    assert "directory" in captured.err.lower() or "file" in captured.err.lower()
    if wrong_role != "output":
        _assert_output_unpublished(paths["output"])


@pytest.mark.parametrize("symlink_role", ["config", "runtime", "data", "output"])
def test_cli_rejects_a_symlink_at_each_final_path(tmp_path, capsys, symlink_role) -> None:
    """Following a final symlink could authenticate or publish the wrong object."""
    paths = _valid_path_arguments(tmp_path)
    path = paths[symlink_role]
    target = tmp_path / f"real-{symlink_role}"
    if symlink_role in {"config", "runtime"}:
        target.write_bytes(b"{}\n")
        path.unlink()
    elif symlink_role == "data":
        target.mkdir()
        path.rmdir()
    else:
        target.mkdir()
        path.parent.mkdir()
    path.symlink_to(target, target_is_directory=symlink_role in {"data", "output"})

    code = cli.main(_argv(paths))
    captured = capsys.readouterr()

    assert code == 2
    assert symlink_role in captured.err.lower()
    assert "symlink" in captured.err.lower()
    _assert_output_unpublished(paths["output"])


@pytest.mark.parametrize("symlink_role", ["config", "runtime", "data", "output"])
def test_cli_rejects_a_symlink_in_each_path_prefix(tmp_path, capsys, symlink_role) -> None:
    """A non-symlink leaf is unsafe when any traversed parent is a symlink."""
    paths = _valid_path_arguments(tmp_path)
    real_parent = tmp_path / f"real-parent-{symlink_role}"
    linked_parent = tmp_path / f"linked-parent-{symlink_role}"
    real_parent.mkdir()
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    if symlink_role in {"config", "runtime"}:
        leaf = real_parent / f"{symlink_role}.json"
        leaf.write_bytes(b"{}\n")
        paths[symlink_role] = linked_parent / leaf.name
    elif symlink_role == "data":
        leaf = real_parent / "data"
        leaf.mkdir()
        paths[symlink_role] = linked_parent / leaf.name
    else:
        paths[symlink_role] = linked_parent / "missing-output"

    code = cli.main(_argv(paths))
    captured = capsys.readouterr()

    assert code == 2
    assert symlink_role in captured.err.lower()
    assert "symlink" in captured.err.lower()
    _assert_output_unpublished(paths["output"])


def test_structural_failure_precedes_every_content_operation(tmp_path, monkeypatch) -> None:
    """Preflight must not authenticate, read bytes, analyze CSVs, or create output."""
    paths = _valid_path_arguments(tmp_path)
    paths["config"].unlink()
    calls: list[str] = []

    def forbidden(name):
        def fail(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} ran before structural preflight completed")

        return fail

    for name in (
        "verify_runtime",
        "_read_config_bytes",
        "load_analysis_config",
        "read_inputs",
        "write_bundle",
    ):
        monkeypatch.setattr(cli, name, forbidden(name), raising=False)

    with pytest.raises(ValueError, match="config.*exist"):
        cli.run_analysis(
            paths["config"], paths["runtime"], paths["data"], paths["output"]
        )

    assert calls == []
    _assert_output_unpublished(paths["output"])


def test_runtime_failure_prevents_config_csv_reads_and_output_creation(
    tmp_path,
    monkeypatch,
) -> None:
    """No unauthenticated process may consume analysis inputs or touch output."""
    paths = _valid_path_arguments(tmp_path)
    calls: list[str] = []

    def fail_runtime(path):
        calls.append("verify_runtime")
        raise RuntimeError("runtime rejected")

    def forbidden(name):
        def fail(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} ran after runtime rejection")

        return fail

    monkeypatch.setattr(cli, "verify_runtime", fail_runtime, raising=False)
    for name in ("_read_config_bytes", "load_analysis_config", "read_inputs", "write_bundle"):
        monkeypatch.setattr(cli, name, forbidden(name), raising=False)

    with pytest.raises(RuntimeError, match="runtime rejected"):
        cli.run_analysis(
            paths["config"], paths["runtime"], paths["data"], paths["output"]
        )

    assert calls == ["verify_runtime"]
    _assert_output_unpublished(paths["output"])


def _install_stubbed_pipeline(
    monkeypatch,
    output: Path,
    *,
    fail_at: str | None = None,
    contract=V1_CONTRACT,
):
    calls: list[str] = []
    captured: dict[str, object] = {}
    config_bytes = b"exact config bytes\n"
    runtime_bytes = b"verified runtime bytes\n"
    config = SimpleNamespace(timezone="Asia/Seoul", label_maturity_days=38)
    runtime = SimpleNamespace(manifest_bytes=runtime_bytes)
    sources = (object(), object(), object())
    inputs = SimpleNamespace(sources=sources)
    material_catalog = (object(), object())

    class _Column:
        def tolist(self):
            return list(material_catalog)

    genealogy = SimpleNamespace(lineage_rows={"material_lineage": _Column()})
    split = SimpleNamespace(as_of=date(2025, 2, 20))
    configured_definitions = (object(), object(), object())
    projection = b"criteria projection\n"
    criteria_identity = SimpleNamespace(value="criteria-id")
    bootstrap_seed = object()
    bundle_identity = SimpleNamespace(value="bundle-id")
    events = [object(), object()]
    summary_payload = {"summary": "wire"}
    range_wire = [{"range": "wire"}]
    rule_wire = [{"rule": "wire"}]

    def step(name, result):
        def invoke(*args, **kwargs):
            calls.append(name)
            if fail_at == name:
                raise RuntimeError(f"injected {name} failure")
            captured[name] = (args, kwargs)
            return result

        return invoke

    monkeypatch.setattr(cli, "verify_runtime", step("verify_runtime", runtime), raising=False)
    monkeypatch.setattr(
        cli, "_read_config_bytes", step("config_bytes", config_bytes), raising=False
    )
    monkeypatch.setattr(
        cli,
        "load_analysis_config_contract",
        step("config_validation", (contract, config)),
        raising=False,
    )
    monkeypatch.setattr(cli, "read_inputs", step("read_inputs", inputs), raising=False)
    monkeypatch.setattr(
        cli, "build_genealogy", step("genealogy", genealogy), raising=False
    )
    monkeypatch.setattr(cli, "build_time_split", step("split", split), raising=False)
    monkeypatch.setattr(
        cli,
        "definitions",
        step("definitions", configured_definitions),
        raising=False,
    )

    def schema_bytes(selected_contract, role):
        assert selected_contract is contract
        calls.append(f"schema:{role}")
        return f"authenticated:{role}".encode("ascii")

    def schema_digest(payload):
        role = payload.decode("ascii").removeprefix("authenticated:")
        calls.append(f"schema_digest:{role}")
        return f"digest:{role}"

    monkeypatch.setattr(cli, "contract_schema_bytes", schema_bytes, raising=False)
    monkeypatch.setattr(cli, "sha256_uri", schema_digest, raising=False)
    monkeypatch.setattr(
        cli,
        "build_criteria_projection",
        step("criteria_projection", projection),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "compute_criteria_identity",
        step("criteria_identity", criteria_identity),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "resolve_bootstrap_seed",
        step("bootstrap_seed", bootstrap_seed),
        raising=False,
    )

    class _Ranges:
        def to_wire(self):
            calls.append("ranges_to_wire")
            return range_wire

    class _Rules:
        def to_wire(self):
            calls.append("rules_to_wire")
            return rule_wire

    ranges = _Ranges()
    rules = _Rules()
    monkeypatch.setattr(
        cli,
        "build_operating_ranges_result",
        step("ranges", ranges),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "build_quality_rules_result_with_seed",
        step("rules", rules),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "compute_bundle_identity",
        step("bundle_identity", bundle_identity),
        raising=False,
    )
    monkeypatch.setattr(
        cli, "build_replay_events", step("events", events), raising=False
    )
    summary_request = object()
    bundle_request = object()
    monkeypatch.setattr(
        cli,
        "SummaryBuildRequest",
        step("summary_request", summary_request),
        raising=False,
    )
    monkeypatch.setattr(
        cli, "build_summary", step("summary", summary_payload), raising=False
    )
    monkeypatch.setattr(
        cli,
        "BundleWriteRequest",
        step("bundle_request", bundle_request),
        raising=False,
    )

    def publish(request):
        calls.append("write")
        captured["write"] = ((request,), {})
        assert not output.exists()
        if fail_at == "write":
            raise RuntimeError("injected write failure")
        output.mkdir(parents=True)
        final = output / "bundle-id"
        final.mkdir()
        return final

    monkeypatch.setattr(cli, "write_bundle", publish, raising=False)
    return SimpleNamespace(
        calls=calls,
        captured=captured,
        config_bytes=config_bytes,
        runtime_bytes=runtime_bytes,
        config=config,
        contract=contract,
        runtime=runtime,
        inputs=inputs,
        sources=sources,
        genealogy=genealogy,
        split=split,
        definitions=configured_definitions,
        projection=projection,
        criteria_identity=criteria_identity,
        bootstrap_seed=bootstrap_seed,
        material_catalog=material_catalog,
        ranges=ranges,
        rules=rules,
        bundle_identity=bundle_identity,
        events=events,
        summary_payload=summary_payload,
        range_wire=range_wire,
        rule_wire=rule_wire,
        summary_request=summary_request,
        bundle_request=bundle_request,
    )


def test_orchestration_uses_one_config_runtime_snapshot_and_exact_fixed_order(
    tmp_path,
    monkeypatch,
) -> None:
    """A reordered call or reconstructed config can leak unauthenticated data."""
    paths = _valid_path_arguments(tmp_path)
    pipeline = _install_stubbed_pipeline(monkeypatch, paths["output"])

    final = cli.run_analysis(
        paths["config"], paths["runtime"], paths["data"], paths["output"]
    )

    schema_calls = [
        item
        for role in SCHEMA_ROLES
        for item in (f"schema:{role}", f"schema_digest:{role}")
    ]
    assert pipeline.calls == [
        "verify_runtime",
        "config_bytes",
        "config_validation",
        "read_inputs",
        "genealogy",
        "split",
        "definitions",
        *schema_calls,
        "criteria_projection",
        "criteria_identity",
        "bootstrap_seed",
        "ranges",
        "rules",
        "bundle_identity",
        "events",
        "summary_request",
        "summary",
        "ranges_to_wire",
        "rules_to_wire",
        "bundle_request",
        "write",
    ]
    assert final == paths["output"] / "bundle-id"

    assert pipeline.captured["verify_runtime"] == (((paths["runtime"]),), {})
    assert pipeline.captured["config_bytes"] == (((paths["config"]),), {})
    assert pipeline.captured["config_validation"] == (((pipeline.config_bytes),), {})
    assert pipeline.captured["read_inputs"] == (((paths["data"]),), {})
    assert pipeline.captured["genealogy"] == (((pipeline.inputs),), {})
    assert pipeline.captured["split"] == (
        (pipeline.genealogy, pipeline.config),
        {},
    )
    assert pipeline.captured["definitions"] == (((pipeline.config),), {})
    assert pipeline.captured["criteria_projection"] == (
        (pipeline.split, pipeline.definitions),
        {},
    )

    criteria_args, criteria_kwargs = pipeline.captured["criteria_identity"]
    assert criteria_args == (
        pipeline.split.as_of,
        pipeline.projection,
        pipeline.config_bytes,
        pipeline.runtime_bytes,
    )
    assert tuple(criteria_kwargs["schema_digests"]) == CRITERIA_SCHEMA_ROLES
    assert criteria_kwargs["schema_digests"] == {
        role: f"digest:{role}" for role in CRITERIA_SCHEMA_ROLES
    }

    flowing_catalog = pipeline.captured["ranges"][1]["material_catalog"]
    assert flowing_catalog == pipeline.material_catalog
    for key in ("ranges", "rules"):
        args, kwargs = pipeline.captured[key]
        expected_prefix = (
            pipeline.split,
            pipeline.definitions,
            pipeline.config,
        )
        assert args[:3] == expected_prefix
        assert kwargs["material_catalog"] is flowing_catalog
    assert pipeline.captured["bootstrap_seed"] == (
        (pipeline.config, pipeline.criteria_identity.value),
        {},
    )
    assert pipeline.captured["rules"][0][3] is pipeline.bootstrap_seed

    bundle_args, bundle_kwargs = pipeline.captured["bundle_identity"]
    assert bundle_args == (
        pipeline.criteria_identity.value,
        pipeline.config_bytes,
        pipeline.runtime_bytes,
        pipeline.sources,
    )
    assert tuple(bundle_kwargs["schema_digests"]) == SCHEMA_ROLES
    assert pipeline.captured["events"] == (
        (
            pipeline.genealogy,
            pipeline.bundle_identity.value,
            pipeline.criteria_identity.value,
            pipeline.config,
        ),
        {},
    )

    _, summary_kwargs = pipeline.captured["summary_request"]
    assert summary_kwargs["contract"] is pipeline.contract
    assert summary_kwargs["analysis_config"] is pipeline.config
    assert summary_kwargs["bootstrap_seed"] is pipeline.bootstrap_seed
    assert summary_kwargs["analysis_config_bytes"] is pipeline.config_bytes
    assert summary_kwargs["producer_runtime_bytes"] is pipeline.runtime_bytes
    assert summary_kwargs["definitions"] is pipeline.definitions
    assert summary_kwargs["material_catalog"] is flowing_catalog
    assert summary_kwargs["events"] == tuple(pipeline.events)
    assert pipeline.captured["summary"] == (((pipeline.summary_request),), {})

    _, bundle_kwargs = pipeline.captured["bundle_request"]
    assert bundle_kwargs["contract"] is pipeline.contract
    assert bundle_kwargs["manifest_version"] == pipeline.contract.manifest_version
    assert bundle_kwargs["output_root"] == paths["output"]
    assert bundle_kwargs["producer_runtime"] is pipeline.runtime_bytes
    assert bundle_kwargs["equipment_operating_ranges"] == {
        "schemaVersion": "sfep-operating-ranges/v1",
        "asOf": "2025-02-20",
        "criteriaId": "criteria-id",
        "ranges": pipeline.range_wire,
    }
    assert bundle_kwargs["quality_risk_intervals"] == {
        "schemaVersion": "sfep-quality-rules/v1",
        "asOf": "2025-02-20",
        "criteriaId": "criteria-id",
        "rules": pipeline.rule_wire,
    }
    assert pipeline.captured["write"] == (((pipeline.bundle_request),), {})


def test_v2_cli_flow_uses_selected_contract_for_every_output_version(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _valid_path_arguments(tmp_path)
    pipeline = _install_stubbed_pipeline(
        monkeypatch,
        paths["output"],
        contract=V2_CONTRACT,
    )

    cli.run_analysis(
        paths["config"], paths["runtime"], paths["data"], paths["output"]
    )

    _, summary_kwargs = pipeline.captured["summary_request"]
    _, bundle_kwargs = pipeline.captured["bundle_request"]
    assert summary_kwargs["contract"] is V2_CONTRACT
    assert bundle_kwargs["contract"] is V2_CONTRACT
    assert bundle_kwargs["manifest_version"] == "sfep-equipment-bundle/v2"
    assert bundle_kwargs["equipment_operating_ranges"]["schemaVersion"] == (
        "sfep-operating-ranges/v1"
    )
    assert bundle_kwargs["quality_risk_intervals"]["schemaVersion"] == (
        "sfep-quality-rules/v1"
    )


@pytest.mark.parametrize(
    "failure_boundary",
    ["genealogy", "split", "ranges", "rules", "events", "summary", "write"],
)
def test_prepublication_failure_never_creates_or_exposes_output(
    tmp_path,
    monkeypatch,
    failure_boundary,
) -> None:
    """Every analysis/publication boundary must fail before a partial Bundle is visible."""
    paths = _valid_path_arguments(tmp_path)
    _install_stubbed_pipeline(
        monkeypatch,
        paths["output"],
        fail_at=failure_boundary,
    )

    with pytest.raises(RuntimeError, match=f"injected {failure_boundary} failure"):
        cli.run_analysis(
            paths["config"], paths["runtime"], paths["data"], paths["output"]
        )

    _assert_output_unpublished(paths["output"])


def test_main_analysis_failure_is_one_concise_message_without_traceback(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """Analysis/publication exceptions must not leak a traceback or usage status."""
    paths = _valid_path_arguments(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("analysis exploded\nacross lines")

    monkeypatch.setattr(cli, "run_analysis", fail)

    code = cli.main(_argv(paths))
    captured = capsys.readouterr()

    assert code == 1
    assert captured.out == ""
    assert captured.err.splitlines() == ["error: analysis exploded across lines"]
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "interrupt",
    [KeyboardInterrupt(), SystemExit(17), GeneratorExit()],
    ids=["keyboard-interrupt", "system-exit", "generator-exit"],
)
def test_main_does_not_swallow_base_exceptions(
    tmp_path,
    monkeypatch,
    interrupt,
) -> None:
    """Only ordinary analysis exceptions belong to the CLI status-1 boundary."""
    paths = _valid_path_arguments(tmp_path)

    def fail(*args, **kwargs):
        raise interrupt

    monkeypatch.setattr(cli, "run_analysis", fail)

    with pytest.raises(type(interrupt)) as captured:
        cli.main(_argv(paths))
    if isinstance(interrupt, SystemExit):
        assert captured.value.code == 17


def _runtime_identity(payload_bytes: bytes) -> RuntimeIdentity:
    parsed = json.loads(payload_bytes)
    return RuntimeIdentity(
        manifest_bytes=payload_bytes,
        manifest_sha256=sha256_uri(payload_bytes),
        manifest=MappingProxyType(parsed),
        installed_code_trees=MappingProxyType({}),
    )


def _artifact_snapshot(bundle_root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.name: (len(payload), hashlib.sha256(payload).hexdigest())
        for path in sorted(bundle_root.iterdir(), key=lambda item: item.name)
        if path.is_file()
        for payload in (path.read_bytes(),)
    }


def _validate_replay_payload(payload: bytes) -> list[dict[str, str]]:
    rows = list(
        csv.DictReader(io.StringIO(payload.decode("utf-8"), newline=""), strict=True)
    )
    for row in rows:
        instance: dict[str, object] = {
            key: (None if value == "" else value) for key, value in row.items()
        }
        instance["replay_hour"] = (
            None
            if instance["replay_hour"] is None
            else int(str(instance["replay_hour"]))
        )
        instance["values_json"] = json.loads(str(instance["values_json"]))
        validate_normative_instance("replay_event_row.schema.json", instance)
    return rows


def test_golden_cli_uses_verified_runtime_bytes_and_reuses_exact_complete_bundle(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """Re-reading a replaced runtime file would change the golden ID and copied bytes."""
    config_path = REPOSITORY_ROOT / "analysis/analysis_config.json"
    runtime_path = tmp_path / "producer_runtime.json"
    output_dir = tmp_path / "missing-parent" / "bundle-output"
    verified_bytes = canonical_json_bytes(runtime_payload())
    replacement = json.loads(verified_bytes)
    replacement["producer"]["sourceSha256"] = "sha256:" + "c" * 64
    replacement_bytes = canonical_json_bytes(replacement)
    runtime_path.write_bytes(verified_bytes)
    verified_identity = _runtime_identity(verified_bytes)
    verification_calls = 0
    runtime_reads = 0

    def injected_verify(path):
        nonlocal verification_calls, runtime_reads
        verification_calls += 1
        assert path == runtime_path
        if runtime_reads == 0:
            observed = path.read_bytes()
            runtime_reads += 1
            assert observed == verified_bytes
            path.write_bytes(replacement_bytes)
        return verified_identity

    monkeypatch.setattr(cli, "verify_runtime", injected_verify)

    first = cli.run_analysis(
        config_path,
        runtime_path,
        GOLDEN_SOURCE,
        output_dir,
    )
    first_snapshot = _artifact_snapshot(first)

    code = cli.main(
        _argv(
            {
                "config": config_path,
                "runtime": runtime_path,
                "data": GOLDEN_SOURCE,
                "output": output_dir,
            }
        )
    )
    captured = capsys.readouterr()
    second_snapshot = _artifact_snapshot(first)

    assert code == 0
    assert captured.out == f"{first}\n"
    assert captured.err == ""
    assert first.is_absolute()
    assert first == output_dir / LITERAL_GOLDEN_BUNDLE_ID
    assert verification_calls == 2
    assert runtime_reads == 1
    assert runtime_path.read_bytes() == replacement_bytes
    assert (first / "producer_runtime.json").read_bytes() == verified_bytes
    assert first_snapshot == LITERAL_GOLDEN_ARTIFACT_SNAPSHOT
    assert second_snapshot == first_snapshot

    expected_files = {
        "analysis_config.json",
        "producer_runtime.json",
        "equipment_operating_ranges.json",
        "quality_risk_intervals.json",
        "replay_events.csv",
        "analysis_summary.json",
        "bundle_manifest.json",
    }
    assert {path.name for path in first.iterdir()} == expected_files
    json_schemas = {
        "analysis_config.json": "analysis_config.schema.json",
        "producer_runtime.json": "producer_runtime.schema.json",
        "equipment_operating_ranges.json": "equipment_operating_ranges.schema.json",
        "quality_risk_intervals.json": "quality_risk_intervals.schema.json",
        "analysis_summary.json": "analysis_summary.schema.json",
        "bundle_manifest.json": "bundle_manifest.schema.json",
    }
    decoded: dict[str, dict[str, object]] = {}
    for filename, schema_name in json_schemas.items():
        instance = json.loads((first / filename).read_bytes())
        validate_normative_instance(schema_name, instance)
        decoded[filename] = instance

    manifest = decoded["bundle_manifest.json"]
    ranges = decoded["equipment_operating_ranges.json"]
    rules = decoded["quality_risk_intervals.json"]
    replay_rows = _validate_replay_payload((first / "replay_events.csv").read_bytes())
    assert len(manifest["artifacts"]) == 6
    assert [item["role"] for item in manifest["artifacts"]] == [
        "analysis_config",
        "producer_runtime",
        "equipment_operating_ranges",
        "quality_risk_intervals",
        "replay_events",
        "analysis_summary",
    ]
    assert manifest["bundleId"] == LITERAL_GOLDEN_BUNDLE_ID
    assert manifest["identity"]["criteria_id"] == manifest["criteriaId"]
    assert manifest["criteriaIdentity"]["as_of"] == manifest["asOf"]
    assert ranges["criteriaId"] == rules["criteriaId"] == manifest["criteriaId"]
    assert ranges["asOf"] == rules["asOf"] == manifest["asOf"]
    assert ranges["ranges"] == []
    assert len(rules["rules"]) == 165
    assert len(replay_rows) == 95
    for metadata in manifest["artifacts"]:
        role = metadata["role"]
        filename = {
            "analysis_config": "analysis_config.json",
            "producer_runtime": "producer_runtime.json",
            "equipment_operating_ranges": "equipment_operating_ranges.json",
            "quality_risk_intervals": "quality_risk_intervals.json",
            "replay_events": "replay_events.csv",
            "analysis_summary": "analysis_summary.json",
        }[role]
        payload = (first / filename).read_bytes()
        assert metadata["sizeBytes"] == len(payload)
        assert metadata["sha256"] == sha256_uri(payload)
