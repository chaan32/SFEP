"""Installed, immutable, fail-closed access to the seven normative schemas."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

import jsonschema
import pytest
from jsonschema import Draft202012Validator, ValidationError
from referencing.exceptions import Unresolvable

import equipment_quality.schema as schema_module


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_ROOT = REPOSITORY_ROOT / "analysis"
CONTRACT_ROOT = REPOSITORY_ROOT / "contracts/equipment-monitor/v1"
PACKAGED_CONTRACT_ROOT = ANALYSIS_ROOT / "equipment_quality/contracts/v1"
SCHEMA_NAMES = (
    "bundle_manifest.schema.json",
    "analysis_config.schema.json",
    "producer_runtime.schema.json",
    "equipment_operating_ranges.schema.json",
    "quality_risk_intervals.schema.json",
    "analysis_summary.schema.json",
    "replay_event_row.schema.json",
)
LITERAL_ROOT_SHA256 = {
    "analysis_config.schema.json": "fad28561dfe9d9fe3cd09b025bb18c2101be053cb094b08442ea45963b86f549",
    "analysis_summary.schema.json": "0ae8e07595e5c87b509f7601c294de835fb35ab5d7b6843acc0971489d5da075",
    "bundle_manifest.schema.json": "666e880d296c0d7e3df5af1aa80e6865922ebfb337fb9aca48f93eddfd89e8a5",
    "equipment_operating_ranges.schema.json": "bee7d8be181dae4844c51d4627c5a1f068583b60a60c854f17035a8291cd7d89",
    "producer_runtime.schema.json": "97131d80a993d09d17c2c040b0e1cb2bd0eed5948d7a11608f26331d18f557e6",
    "quality_risk_intervals.schema.json": "2c8775fec18671030cf58ea0e94a3c99f8dac075fa9d5fe8dce724c2462f42ad",
    "replay_event_row.schema.json": "309749f73cf2a5a522617f975128ac005298f2fd71412903d76263702c1b6bd6",
}
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64


def _valid_replay_row() -> dict[str, object]:
    return {
        "schema_version": "sfep-replay-events/v1",
        "bundle_id": SHA_A,
        "criteria_id": SHA_B,
        "event_id": SHA_A,
        "replay_date": "2025-01-01",
        "replay_hour": None,
        "batch_kind": "CAST_DAY",
        "batch_id": SHA_A,
        "equipment_batch_id": None,
        "batch_step": "CAST_RECORDED",
        "time_precision": "DAY",
        "material_key": SHA_B,
        "equipment_type": "SM_CC",
        "equipment_id": "SM1",
        "charge_id": "CH1",
        "slab_no": "1",
        "hr_coil_id": None,
        "ap_prod_id": None,
        "values_json": {
            "sm_plant": "SM1",
            "steel_grade": "STS304",
            "steel_usage": "A",
            "cc_gubun": "CC1",
            "slab_gubun": "NORMAL",
            "tundish_temp": 1540.0,
            "mlac_ratio": 0.92,
            "delta_ferrite": 7.1,
            "ingre_cr": 18.2,
            "ingre_ni": 8.1,
            "ingre_s": 0.005,
            "slab_grind": "HSHS",
            "cast_date": "2025-01-01",
        },
    }


def _transitional_summary() -> dict[str, object]:
    payload = (
        CONTRACT_ROOT / "golden-expectation/analysis_summary.template.json"
    ).read_bytes()
    payload = payload.replace(b"@BUNDLE_ID@", SHA_A.encode("ascii"))
    payload = payload.replace(b"@CRITERIA_ID@", SHA_B.encode("ascii"))
    return json.loads(payload)


def _summary_with_derived_lineage_field(
    artifact_role: str,
    output_field: str,
    dependency: str,
) -> dict[str, object]:
    summary = _transitional_summary()
    summary["lineage"]["fields"] = [{
        "artifactRole": artifact_role,
        "outputField": output_field,
        "sourceRole": None,
        "sourceColumn": None,
        "conversion": "COMPUTE_IDENTITY",
        "dependencies": [dependency],
        "firstAvailableStage": None,
    }]
    return summary


def test_source_package_has_exact_byte_identical_copies_of_all_seven_root_schemas():
    assert not (ANALYSIS_ROOT / "equipment_quality/analysis_config.schema.json").exists()
    assert sorted(path.name for path in PACKAGED_CONTRACT_ROOT.glob("*.schema.json")) == sorted(
        SCHEMA_NAMES
    )
    for name in SCHEMA_NAMES:
        normative = (CONTRACT_ROOT / name).read_bytes()
        packaged = (PACKAGED_CONTRACT_ROOT / name).read_bytes()
        assert hashlib.sha256(normative).hexdigest() == LITERAL_ROOT_SHA256[name]
        assert packaged == normative
        assert schema_module.normative_schema_bytes(name) == normative


def test_production_digest_pins_are_exact_complete_and_immutable():
    assert schema_module._NORMATIVE_SCHEMA_SHA256 == LITERAL_ROOT_SHA256
    with pytest.raises(TypeError):
        schema_module._NORMATIVE_SCHEMA_SHA256["replay_event_row.schema.json"] = "0" * 64


@pytest.mark.parametrize("value", [None, b"analysis_config.schema.json", Path("x"), True])
def test_normative_schema_name_requires_exact_builtin_string(value):
    with pytest.raises(TypeError) as error:
        schema_module.normative_schema_bytes(value)  # type: ignore[arg-type]
    assert str(error.value) == "normative schema name must be a built-in str"


def test_normative_schema_name_rejects_string_subclasses_before_resource_access():
    class SchemaName(str):
        pass

    with pytest.raises(TypeError) as error:
        schema_module.normative_schema_bytes(SchemaName("analysis_config.schema.json"))
    assert str(error.value) == "normative schema name must be a built-in str"


@pytest.mark.parametrize(
    "name",
    [
        "unknown.schema.json",
        "../analysis_config.schema.json",
        "contracts/v1/analysis_config.schema.json",
        "/analysis_config.schema.json",
        "analysis_config.schema.json/..",
        "analysis_config.schema.json\x00",
    ],
)
def test_unknown_and_path_shaped_schema_names_are_rejected_deterministically(name):
    with pytest.raises(ValueError) as error:
        schema_module.normative_schema_bytes(name)
    assert str(error.value) == f"unknown normative schema name: {name!r}"


def test_returned_bytes_are_immutable_and_cannot_corrupt_cached_validator_state():
    original = schema_module.normative_schema_bytes("replay_event_row.schema.json")
    assert type(original) is bytes
    with pytest.raises(TypeError):
        original[0] = 0  # type: ignore[index]

    caller_copy = bytearray(original)
    caller_copy[0] ^= 0x01
    schema_module.validate_normative_instance(
        "replay_event_row.schema.json", _valid_replay_row()
    )
    assert schema_module.normative_schema_bytes("replay_event_row.schema.json") == original


def test_validator_accepts_valid_instance_and_rejects_schema_and_date_violations():
    valid = _valid_replay_row()
    assert (
        schema_module.validate_normative_instance(
            "replay_event_row.schema.json", valid
        )
        is None
    )

    invalid_enum = copy.deepcopy(valid)
    invalid_enum["batch_kind"] = "UNKNOWN_DAY"
    with pytest.raises(ValidationError):
        schema_module.validate_normative_instance(
            "replay_event_row.schema.json", invalid_enum
        )

    invalid_date = copy.deepcopy(valid)
    invalid_date["replay_date"] = "2025-02-30"
    with pytest.raises(ValidationError):
        schema_module.validate_normative_instance(
            "replay_event_row.schema.json", invalid_date
        )


def test_summary_resource_runs_closed_application_validation_after_json_schema():
    summary = _transitional_summary()
    schema_module.validate_normative_instance("analysis_summary.schema.json", summary)

    invalid = copy.deepcopy(summary)
    invalid["splitCounts"]["reference"]["total"] += 1
    with pytest.raises(ValidationError, match="count"):
        schema_module.validate_normative_instance(
            "analysis_summary.schema.json", invalid
        )


def test_public_validation_rejects_forged_range_and_rule_context_output_keys():
    for artifact_role, output_field in (
        (
            "equipment_operating_ranges",
            "ranges[].context.not_a_contract_key",
        ),
        (
            "quality_risk_intervals",
            "rules[].applicationContext.not_a_contract_key",
        ),
    ):
        forged = _summary_with_derived_lineage_field(
            artifact_role,
            output_field,
            "fur_hr.f_pre_temp",
        )
        with pytest.raises(ValidationError):
            schema_module.validate_normative_instance(
                "analysis_summary.schema.json", forged
            )
        with pytest.raises(ValidationError, match="context output field"):
            schema_module._validate_analysis_summary_application_contract(
                forged
            )


@pytest.mark.parametrize(
    ("artifact_role", "output_field", "dependency"),
    [
        (
            "equipment_operating_ranges",
            "schemaVersion",
            "schema.equipment_operating_ranges",
        ),
        (
            "equipment_operating_ranges",
            "criteriaId",
            "identity.criteria_id",
        ),
        (
            "quality_risk_intervals",
            "schemaVersion",
            "schema.quality_risk_intervals",
        ),
        (
            "quality_risk_intervals",
            "criteriaId",
            "identity.criteria_id",
        ),
        ("replay_events", "schema_version", "schema.replay_events"),
        ("replay_events", "bundle_id", "identity.bundle_id"),
        ("replay_events", "criteria_id", "identity.criteria_id"),
    ],
)
def test_role_scoped_schema_and_identity_terminals_accept_truthful_leaf_mappings(
    artifact_role,
    output_field,
    dependency,
):
    schema_module.validate_normative_instance(
        "analysis_summary.schema.json",
        _summary_with_derived_lineage_field(
            artifact_role,
            output_field,
            dependency,
        ),
    )


@pytest.mark.parametrize(
    ("artifact_role", "output_field", "dependency"),
    [
        (
            "equipment_operating_ranges",
            "schemaVersion",
            "schema.quality_risk_intervals",
        ),
        (
            "equipment_operating_ranges",
            "criteriaId",
            "identity.bundle_id",
        ),
        (
            "quality_risk_intervals",
            "schemaVersion",
            "schema.equipment_operating_ranges",
        ),
        (
            "quality_risk_intervals",
            "criteriaId",
            "identity.criteria_projection_sha256",
        ),
        ("replay_events", "schema_version", "schema.analysis_summary"),
        (
            "replay_events",
            "bundle_id",
            "identity.analysis_config_sha256",
        ),
    ],
)
def test_role_scoped_schema_and_identity_terminals_reject_cross_role_forgery(
    artifact_role,
    output_field,
    dependency,
):
    with pytest.raises(ValidationError, match="terminal"):
        schema_module.validate_normative_instance(
            "analysis_summary.schema.json",
            _summary_with_derived_lineage_field(
                artifact_role,
                output_field,
                dependency,
            ),
        )


def test_summary_application_validation_closes_refs_order_cycles_roles_and_intervals():
    summary = _transitional_summary()
    root_validator = Draft202012Validator(
        json.loads((CONTRACT_ROOT / "analysis_summary.schema.json").read_bytes())
    )
    invalid_instances = []

    invalid_purge = copy.deepcopy(summary)
    invalid_purge["chargePurgeCounts"]["outer"] = {
        "chargeCount": 1,
        "rowCount": 0,
    }
    invalid_instances.append(invalid_purge)

    invalid_interval = copy.deepcopy(summary)
    invalid_interval["holdoutMetrics"][0]["baseDefectRate"]["lower"] = 0.75
    invalid_instances.append(invalid_interval)

    invalid_formula = copy.deepcopy(summary)
    invalid_formula["holdoutMetrics"][0]["alertRate"].update(
        pointEstimate=0.25,
        lower=0,
        upper=0.5,
    )
    invalid_instances.append(invalid_formula)

    invalid_zero_denominator = copy.deepcopy(summary)
    invalid_zero_denominator["holdoutMetrics"][0]["alertRate"] = {
        "pointEstimate": None,
        "lower": None,
        "upper": None,
        "validReplicates": 0,
        "reasonCode": "ZERO_DENOMINATOR",
    }
    invalid_instances.append(invalid_zero_denominator)

    duplicate_material = copy.deepcopy(summary)
    duplicate_material["lineage"]["materials"].append(
        copy.deepcopy(duplicate_material["lineage"]["materials"][0])
    )
    invalid_instances.append(duplicate_material)

    missing_aggregate_material = copy.deepcopy(summary)
    missing_aggregate_material["lineage"]["aggregates"][0][
        "inputMaterialKeys"
    ] = ["sha256:" + "c" * 64]
    invalid_instances.append(missing_aggregate_material)

    unsorted_fields = copy.deepcopy(summary)
    unsorted_fields["lineage"]["fields"].reverse()
    invalid_instances.append(unsorted_fields)

    unsorted_dependencies = copy.deepcopy(summary)
    unsorted_dependencies["lineage"]["fields"][1]["dependencies"] = [
        "policy.LOCKED_RETROSPECTIVE_HOLDOUT",
        "fur_hr.f_pre_temp",
    ]
    invalid_instances.append(unsorted_dependencies)

    cycle = copy.deepcopy(summary)
    reference = copy.deepcopy(cycle["lineage"]["fields"][1])
    reference["dependencies"] = [
        "analysis_summary.driftMetrics[].holdout.median"
    ]
    holdout = copy.deepcopy(reference)
    holdout["outputField"] = "driftMetrics[].holdout.median"
    holdout["dependencies"] = [
        "analysis_summary.driftMetrics[].reference.median"
    ]
    cycle["lineage"]["fields"] = [
        cycle["lineage"]["fields"][0],
        holdout,
        reference,
    ]
    invalid_instances.append(cycle)

    wrong_role_terminal = copy.deepcopy(summary)
    runtime_field = copy.deepcopy(wrong_role_terminal["lineage"]["fields"][1])
    runtime_field.update(
        artifactRole="producer_runtime",
        outputField="producer.sourceSha256",
        conversion="COPY_VERIFIED_RUNTIME",
        dependencies=["fur_hr.f_pre_temp"],
    )
    wrong_role_terminal["lineage"]["fields"] = [
        runtime_field,
        wrong_role_terminal["lineage"]["fields"][0],
    ]
    invalid_instances.append(wrong_role_terminal)

    for invalid in invalid_instances:
        root_validator.validate(invalid)
        with pytest.raises(ValidationError):
            schema_module.validate_normative_instance(
                "analysis_summary.schema.json", invalid
            )


def test_validation_error_cannot_expose_mutable_cached_schema_state():
    module = importlib.reload(schema_module)
    try:
        with pytest.raises(ValidationError) as caught:
            module.validate_normative_instance("replay_event_row.schema.json", {})

        with pytest.raises(TypeError):
            caught.value.schema["required"] = ()

        module.validate_normative_instance(
            "replay_event_row.schema.json", _valid_replay_row()
        )
    finally:
        importlib.reload(module)


def test_concurrent_first_use_compiles_once_and_keeps_cache_isolated(monkeypatch):
    module = importlib.reload(schema_module)
    original_checker = module.FormatChecker
    calls = 0
    calls_lock = threading.Lock()
    start = threading.Barrier(24)

    def counted_checker():
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.01)
        return original_checker()

    monkeypatch.setattr(module, "FormatChecker", counted_checker)

    def validate(_: int) -> bytes:
        start.wait()
        module.validate_normative_instance(
            "replay_event_row.schema.json", _valid_replay_row()
        )
        return module.normative_schema_bytes("replay_event_row.schema.json")

    with ThreadPoolExecutor(max_workers=24) as executor:
        results = list(executor.map(validate, range(24)))

    assert calls == 1
    assert all(type(result) is bytes for result in results)
    assert len(set(results)) == 1


def test_cold_byte_access_authenticates_without_compiling_a_validator(monkeypatch):
    module = importlib.reload(schema_module)
    calls = 0
    original_checker = module.FormatChecker

    def counted_original_checker():
        nonlocal calls
        calls += 1
        return original_checker()

    monkeypatch.setattr(module, "FormatChecker", counted_original_checker)
    module.normative_schema_bytes("replay_event_row.schema.json")
    assert calls == 0
    module.validate_normative_instance(
        "replay_event_row.schema.json", _valid_replay_row()
    )
    assert calls == 1


def _isolated_resource_failure(tmp_path: Path, payload: bytes | None) -> str:
    isolated = tmp_path / "isolated"
    package_root = isolated / "equipment_quality"
    shutil.copytree(
        ANALYSIS_ROOT / "equipment_quality",
        package_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    resource = package_root / "contracts/v1/replay_event_row.schema.json"
    if payload is None:
        resource.unlink()
    else:
        resource.write_bytes(payload)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(isolated)
    environment["PYTHONNOUSERSITE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from equipment_quality.schema import normative_schema_bytes\n"
                "try:\n"
                "    normative_schema_bytes('replay_event_row.schema.json')\n"
                "except Exception as error:\n"
                "    print(type(error).__name__ + ':' + str(error))\n"
                "else:\n"
                "    raise SystemExit('resource unexpectedly accepted')\n"
            ),
        ],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def _isolated_tamper_then_restore(tmp_path: Path, payload: bytes) -> str:
    isolated = tmp_path / "isolated"
    package_root = isolated / "equipment_quality"
    shutil.copytree(
        ANALYSIS_ROOT / "equipment_quality",
        package_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    resource = package_root / "contracts/v1/replay_event_row.schema.json"
    original = tmp_path / "original.schema.json"
    original.write_bytes(resource.read_bytes())
    resource.write_bytes(payload)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(isolated)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["SFEP_SCHEMA_RESOURCE"] = str(resource)
    environment["SFEP_SCHEMA_ORIGINAL"] = str(original)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os\n"
                "from pathlib import Path\n"
                "from equipment_quality.schema import normative_schema_bytes, validate_normative_instance\n"
                "try:\n"
                "    normative_schema_bytes('replay_event_row.schema.json')\n"
                "except RuntimeError as error:\n"
                "    assert str(error) == 'normative schema resource digest mismatch: replay_event_row.schema.json'\n"
                "else:\n"
                "    raise AssertionError('tampered resource accepted')\n"
                "Path(os.environ['SFEP_SCHEMA_RESOURCE']).write_bytes(Path(os.environ['SFEP_SCHEMA_ORIGINAL']).read_bytes())\n"
                "restored = normative_schema_bytes('replay_event_row.schema.json')\n"
                "assert restored == Path(os.environ['SFEP_SCHEMA_ORIGINAL']).read_bytes()\n"
                "print('tamper-rejected-restore-accepted')\n"
            ),
        ],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            None,
            "RuntimeError:normative schema resource unavailable: replay_event_row.schema.json",
        ),
        (
            b"\xff",
            "RuntimeError:normative schema resource digest mismatch: replay_event_row.schema.json",
        ),
        (
            b'{"$schema":"https://json-schema.org/draft/2020-12/schema",',
            "RuntimeError:normative schema resource digest mismatch: replay_event_row.schema.json",
        ),
        (
            b'{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","type":"array"}',
            "RuntimeError:normative schema resource digest mismatch: replay_event_row.schema.json",
        ),
        (
            b'{"$schema":"https://json-schema.org/draft/2020-12/schema","minimum":NaN}',
            "RuntimeError:normative schema resource digest mismatch: replay_event_row.schema.json",
        ),
        (
            b'{"$schema":"https://json-schema.org/draft/2020-12/schema","minimum":1e999}',
            "RuntimeError:normative schema resource digest mismatch: replay_event_row.schema.json",
        ),
        (
            b'{"$schema":"https://json-schema.org/draft/2019-09/schema","type":"object"}',
            "RuntimeError:normative schema resource digest mismatch: replay_event_row.schema.json",
        ),
    ],
    ids=[
        "missing",
        "invalid-utf8",
        "truncated-json",
        "duplicate-member",
        "nonfinite-constant",
        "nonfinite-overflow",
        "wrong-draft-schema",
    ],
)
def test_missing_malformed_and_invalid_schema_resources_fail_closed(
    tmp_path, payload, expected
):
    assert _isolated_resource_failure(tmp_path, payload) == expected


def test_valid_permissive_one_byte_and_swapped_schema_tampering_fail_without_poisoning(
    tmp_path,
):
    original = (CONTRACT_ROOT / "replay_event_row.schema.json").read_bytes()
    tampered_payloads = (
        b'{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object"}',
        original + b" ",
        (CONTRACT_ROOT / "analysis_config.schema.json").read_bytes(),
    )
    for index, payload in enumerate(tampered_payloads):
        case_root = tmp_path / str(index)
        case_root.mkdir()
        assert (
            _isolated_tamper_then_restore(case_root, payload)
            == "tamper-rejected-restore-accepted"
        )


def test_schema_compiler_distinguishes_wrong_draft_from_malformed_json():
    name = "replay_event_row.schema.json"
    with pytest.raises(RuntimeError) as wrong_draft:
        schema_module._compile_normative_validator(
            name,
            b'{"$schema":"https://json-schema.org/draft/2019-09/schema","type":"object"}',
        )
    assert str(wrong_draft.value) == f"normative schema is invalid: {name}"

    with pytest.raises(RuntimeError) as malformed:
        schema_module._compile_normative_validator(name, b'{"$schema":')
    assert str(malformed.value) == f"normative schema resource is malformed: {name}"


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        b'{"$schema":"https://json-schema.org/draft/2020-12/schema",',
        b'{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","type":"array"}',
        b'{"$schema":"https://json-schema.org/draft/2020-12/schema","minimum":NaN}',
        b'{"$schema":"https://json-schema.org/draft/2020-12/schema","minimum":1e999}',
    ],
    ids=[
        "invalid-utf8",
        "truncated-json",
        "duplicate-member",
        "nonfinite-constant",
        "nonfinite-overflow",
    ],
)
def test_schema_compiler_strictly_rejects_malformed_resource_json(payload):
    name = "replay_event_row.schema.json"
    with pytest.raises(RuntimeError) as malformed:
        schema_module._compile_normative_validator(name, payload)
    assert str(malformed.value) == f"normative schema resource is malformed: {name}"


def test_local_reference_preflight_resolves_escaped_members_and_list_indexes():
    schema_module._assert_local_schema_references(
        {
            "$defs": {
                "a/b": {
                    "items": [
                        {"properties": {"~name": {"type": "string"}}}
                    ]
                }
            },
            "$ref": "#/$defs/a~1b/items/0/properties/~0name",
        }
    )


def test_external_dynamic_reference_is_rejected_without_network_access(monkeypatch):
    module = importlib.reload(schema_module)
    network_calls = 0

    def forbidden_urlopen(*args, **kwargs):
        nonlocal network_calls
        network_calls += 1
        raise AssertionError("network access attempted")

    payload = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$dynamicRef": "https://example.invalid/schema.json",
        },
        separators=(",", ":"),
    ).encode()
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_urlopen)
    monkeypatch.setattr(module, "_normative_schema_bytes", lambda name: payload)
    try:
        with pytest.raises(RuntimeError) as error:
            module.validate_normative_instance(
                "replay_event_row.schema.json", {}
            )
        assert str(error.value) == (
            "normative schema has invalid local reference: replay_event_row.schema.json"
        )
        assert network_calls == 0
    finally:
        monkeypatch.undo()
        importlib.reload(module)


@pytest.mark.parametrize("reference_keyword", ["$dynamicRef", "$recursiveRef"])
def test_network_capable_reference_vocabularies_are_rejected_recursively(
    reference_keyword,
):
    payload = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {
                "unused": {
                    "properties": {
                        "nested": {
                            reference_keyword: "#/$defs/unused",
                        }
                    }
                }
            },
            "type": "object",
        },
        separators=(",", ":"),
    ).encode()
    with pytest.raises(RuntimeError) as error:
        schema_module._compile_normative_validator(
            "replay_event_row.schema.json", payload
        )
    assert str(error.value) == (
        "normative schema has invalid local reference: replay_event_row.schema.json"
    )


@pytest.mark.parametrize("reference", [17, True, None])
def test_local_ref_requires_an_exact_builtin_string(reference):
    with pytest.raises(schema_module._InvalidLocalSchemaReference):
        schema_module._assert_local_schema_references(
            {"$defs": {"value": {}}, "$ref": reference}
        )


def test_local_ref_rejects_string_subclasses():
    class Reference(str):
        pass

    with pytest.raises(schema_module._InvalidLocalSchemaReference):
        schema_module._assert_local_schema_references(
            {"$defs": {"value": {}}, "$ref": Reference("#/$defs/value")}
        )


@pytest.mark.parametrize("reference", ["#/$defs/value", 17, None])
def test_dynamic_ref_is_rejected_regardless_of_value_type(reference):
    with pytest.raises(schema_module._InvalidLocalSchemaReference):
        schema_module._assert_local_schema_references(
            {"$defs": {"value": {}}, "$dynamicRef": reference}
        )


def test_dynamic_ref_rejects_string_subclasses():
    class Reference(str):
        pass

    with pytest.raises(schema_module._InvalidLocalSchemaReference):
        schema_module._assert_local_schema_references(
            {"$dynamicRef": Reference("#")}
        )


@pytest.mark.parametrize("reference", ["child.json", "#/$defs/value"])
def test_nested_id_is_rejected_with_relative_or_local_reference(reference):
    payload = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://sfep.local/root.schema.json",
            "$defs": {"value": {"type": "string"}},
            "properties": {
                "nested": {
                    "$id": "nested/",
                    "$ref": reference,
                }
            },
        },
        separators=(",", ":"),
    ).encode()
    with pytest.raises(RuntimeError) as error:
        schema_module._compile_normative_validator(
            "replay_event_row.schema.json", payload
        )
    assert str(error.value) == (
        "normative schema has invalid local reference: replay_event_row.schema.json"
    )


def test_root_id_allows_only_an_exact_builtin_string():
    schema_module._assert_local_schema_references(
        {"$id": "https://sfep.local/root.schema.json", "$ref": "#"}
    )

    class Identifier(str):
        pass

    for invalid in (Identifier("https://sfep.local/root.schema.json"), 17):
        with pytest.raises(schema_module._InvalidLocalSchemaReference):
            schema_module._assert_local_schema_references({"$id": invalid})


@pytest.mark.parametrize(
    "reference",
    [
        pytest.param("#/title", id="string"),
        pytest.param("#/examples", id="list"),
        pytest.param("#/examples/0", id="number"),
    ],
)
def test_ref_target_must_be_a_boolean_or_mapping_schema(reference):
    payload = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "not a schema",
            "examples": [17],
            "$ref": reference,
        },
        separators=(",", ":"),
    ).encode()
    with pytest.raises(RuntimeError) as error:
        schema_module._compile_normative_validator(
            "replay_event_row.schema.json", payload
        )
    assert str(error.value) == (
        "normative schema has invalid local reference: replay_event_row.schema.json"
    )


def test_true_and_false_boolean_ref_targets_are_valid_schemas():
    payload = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {"allow": True, "deny": False},
            "allOf": [
                {"$ref": "#/$defs/allow"},
                {"$ref": "#/$defs/deny"},
            ],
        },
        separators=(",", ":"),
    ).encode()
    validator = schema_module._compile_normative_validator(
        "replay_event_row.schema.json", payload
    )
    with pytest.raises(ValidationError):
        validator.validate({})


def test_reference_cycles_and_duplicate_targets_finish_preflight():
    payload = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {
                "left": {"$ref": "#/$defs/right"},
                "right": {"$ref": "#/$defs/left"},
            },
            "anyOf": [
                {"$ref": "#/$defs/left"},
                {"$ref": "#/$defs/left"},
            ],
        },
        separators=(",", ":"),
    ).encode()
    schema_module._compile_normative_validator(
        "replay_event_row.schema.json", payload
    )


def test_reference_preflight_failure_does_not_poison_validator_cache(monkeypatch):
    module = importlib.reload(schema_module)
    invalid = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {"value": {"type": "string"}},
            "properties": {
                "nested": {
                    "$id": "nested/",
                    "$ref": "#/$defs/value",
                }
            },
        },
        separators=(",", ":"),
    ).encode()
    valid = (CONTRACT_ROOT / "replay_event_row.schema.json").read_bytes()
    payloads = iter((invalid, valid))
    monkeypatch.setattr(module, "_normative_schema_bytes", lambda name: next(payloads))
    try:
        with pytest.raises(RuntimeError) as error:
            module.validate_normative_instance(
                "replay_event_row.schema.json", _valid_replay_row()
            )
        assert str(error.value) == (
            "normative schema has invalid local reference: replay_event_row.schema.json"
        )
        module.validate_normative_instance(
            "replay_event_row.schema.json", _valid_replay_row()
        )
    finally:
        monkeypatch.undo()
        importlib.reload(module)


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "other.schema.json#/$defs/value",
        "https://example.invalid/schema.json",
        "#not-a-json-pointer",
        "#/$defs/missing",
        "#/$defs/items/01",
        "#/$defs/~2bad",
        "#/%ZZ",
    ],
)
def test_invalid_external_and_unresolved_references_fail_preflight_deterministically(
    reference,
):
    payload = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {"items": [{"type": "string"}]},
            "$ref": reference,
        },
        separators=(",", ":"),
    ).encode()
    with pytest.raises(RuntimeError) as error:
        schema_module._compile_normative_validator(
            "replay_event_row.schema.json", payload
        )
    assert str(error.value) == (
        "normative schema has invalid local reference: replay_event_row.schema.json"
    )


def test_all_current_root_schemas_pass_local_reference_preflight():
    for name in SCHEMA_NAMES:
        schema_module._compile_normative_validator(
            name, (CONTRACT_ROOT / name).read_bytes()
        )


def test_validation_time_reference_failure_is_normalized(monkeypatch):
    class BrokenValidator:
        def validate(self, instance):
            raise Unresolvable("#/$defs/missing")

    monkeypatch.setattr(
        schema_module, "_normative_validator", lambda name: BrokenValidator()
    )
    with pytest.raises(RuntimeError) as error:
        schema_module.validate_normative_instance(
            "replay_event_row.schema.json", _valid_replay_row()
        )
    assert str(error.value) == (
        "normative schema reference resolution failed: replay_event_row.schema.json"
    )


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("schema-wheel")
    source = root / "source"
    source.mkdir()
    shutil.copy2(ANALYSIS_ROOT / "pyproject.toml", source / "pyproject.toml")
    shutil.copytree(
        ANALYSIS_ROOT / "equipment_quality",
        source / "equipment_quality",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    wheel_dir = root / "wheel"
    environment = os.environ.copy()
    environment["PIP_NO_INDEX"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(wheel_dir),
            str(source),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def test_wheel_contains_exactly_one_nested_copy_of_each_schema_and_no_legacy_copy(
    built_wheel,
):
    expected = {
        f"equipment_quality/contracts/v1/{name}" for name in SCHEMA_NAMES
    }
    with zipfile.ZipFile(built_wheel) as archive:
        names = archive.namelist()
        schema_members = {name for name in names if name.endswith(".schema.json")}
        assert schema_members == expected
        for member in expected:
            assert names.count(member) == 1
            name = Path(member).name
            archived = archive.read(member)
            normative = (CONTRACT_ROOT / name).read_bytes()
            assert archived == normative
            assert hashlib.sha256(archived).hexdigest() == LITERAL_ROOT_SHA256[name]
        assert "equipment_quality/analysis_config.schema.json" not in names


def test_tampered_wheel_schema_member_is_rejected_by_digest_boundary(
    tmp_path, built_wheel
):
    tampered_wheel = tmp_path / built_wheel.name
    target = "equipment_quality/contracts/v1/replay_event_row.schema.json"
    with zipfile.ZipFile(built_wheel) as source, zipfile.ZipFile(
        tampered_wheel, "w"
    ) as destination:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == target:
                payload += b" "
            destination.writestr(info, payload)

    cwd = tmp_path / "tampered-wheel-cwd"
    cwd.mkdir()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tampered_wheel)
    environment["PYTHONNOUSERSITE"] = "1"
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from equipment_quality.schema import normative_schema_bytes\n"
                "try:\n"
                "    normative_schema_bytes('replay_event_row.schema.json')\n"
                "except RuntimeError as error:\n"
                "    assert str(error) == 'normative schema resource digest mismatch: replay_event_row.schema.json'\n"
                "else:\n"
                "    raise AssertionError('tampered wheel member accepted')\n"
                "print('tampered-wheel-member-rejected')\n"
            ),
        ],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert probe.stdout.strip() == "tampered-wheel-member-rejected"


def test_installed_wheel_validates_all_schemas_from_arbitrary_cwd_without_network_or_root_contracts(
    tmp_path, built_wheel
):
    virtual_environment = tmp_path / "venv"
    create_environment = subprocess.run(
        [
            sys.executable,
            "-m",
            "venv",
            "--system-site-packages",
            str(virtual_environment),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert create_environment.returncode == 0, (
        create_environment.stdout + create_environment.stderr
    )
    installed_python = virtual_environment / "bin/python"
    install_environment = os.environ.copy()
    install_environment["PIP_NO_INDEX"] = "1"
    install = subprocess.run(
        [
            str(installed_python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            str(built_wheel),
        ],
        cwd=tmp_path,
        env=install_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    cwd = tmp_path / "arbitrary-cwd"
    cwd.mkdir()
    (cwd / "analysis_config.json").write_bytes(
        (ANALYSIS_ROOT / "analysis_config.json").read_bytes()
    )
    assert not (tmp_path / "contracts").exists()
    assert not (cwd / "contracts").exists()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(
        Path(jsonschema.__file__).resolve().parent.parent
    )
    environment["PYTHONNOUSERSITE"] = "1"
    environment["SFEP_VENV"] = str(virtual_environment)
    environment["SFEP_REPLAY_INSTANCE"] = json.dumps(
        _valid_replay_row(), ensure_ascii=False, separators=(",", ":")
    )
    environment["SFEP_SCHEMA_DIGESTS"] = json.dumps(
        LITERAL_ROOT_SHA256, sort_keys=True, separators=(",", ":")
    )
    environment["SFEP_SUMMARY_INSTANCE"] = json.dumps(
        _transitional_summary(), ensure_ascii=False, separators=(",", ":")
    )
    environment["PIP_NO_INDEX"] = "1"
    probe = subprocess.run(
        [
            str(installed_python),
            "-c",
            (
                "import copy, hashlib, json, os\n"
                "from pathlib import Path\n"
                "from jsonschema import ValidationError\n"
                "import equipment_quality\n"
                "from equipment_quality.schema import load_analysis_config, normative_schema_bytes, validate_normative_instance\n"
                "venv = Path(os.environ['SFEP_VENV']).resolve()\n"
                "assert Path(equipment_quality.__file__).resolve().is_relative_to(venv)\n"
                "assert not (Path.cwd() / 'contracts').exists()\n"
                "expected_digests = json.loads(os.environ['SFEP_SCHEMA_DIGESTS'])\n"
                "for name, expected_digest in expected_digests.items():\n"
                "    schema_bytes = normative_schema_bytes(name)\n"
                "    assert type(schema_bytes) is bytes\n"
                "    assert hashlib.sha256(schema_bytes).hexdigest() == expected_digest\n"
                "assert load_analysis_config(Path('analysis_config.json')).analysis_config_version == 'quality-analysis-v1'\n"
                "row = json.loads(os.environ['SFEP_REPLAY_INSTANCE'])\n"
                "validate_normative_instance('replay_event_row.schema.json', row)\n"
                "summary = json.loads(os.environ['SFEP_SUMMARY_INSTANCE'])\n"
                "validate_normative_instance('analysis_summary.schema.json', summary)\n"
                "invalid_summary = copy.deepcopy(summary)\n"
                "invalid_summary['splitCounts']['reference']['total'] += 1\n"
                "try:\n"
                "    validate_normative_instance('analysis_summary.schema.json', invalid_summary)\n"
                "except ValidationError:\n"
                "    pass\n"
                "else:\n"
                "    raise AssertionError('application-invalid summary accepted')\n"
                "invalid = copy.deepcopy(row)\n"
                "invalid['replay_date'] = '2025-02-30'\n"
                "try:\n"
                "    validate_normative_instance('replay_event_row.schema.json', invalid)\n"
                "except ValidationError:\n"
                "    pass\n"
                "else:\n"
                "    raise AssertionError('invalid date accepted')\n"
                "for name in ('../analysis_config.schema.json', 'unknown.schema.json'):\n"
                "    try:\n"
                "        normative_schema_bytes(name)\n"
                "    except ValueError as error:\n"
                "        assert str(error) == f'unknown normative schema name: {name!r}'\n"
                "    else:\n"
                "        raise AssertionError('unknown schema accepted')\n"
                "print('installed-schema-boundary-ok')\n"
            ),
        ],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert probe.stdout.strip() == "installed-schema-boundary-ok"
