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
V2_CONTRACT_ROOT = REPOSITORY_ROOT / "contracts/equipment-monitor/v2"
V2_PACKAGED_CONTRACT_ROOT = ANALYSIS_ROOT / "equipment_quality/contracts/v2"
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
    "analysis_summary.schema.json": "6cb6077cc7093b90e464cc794604ca6b7682002a138ebed051801aee29f9d374",
    "bundle_manifest.schema.json": "666e880d296c0d7e3df5af1aa80e6865922ebfb337fb9aca48f93eddfd89e8a5",
    "equipment_operating_ranges.schema.json": "bee7d8be181dae4844c51d4627c5a1f068583b60a60c854f17035a8291cd7d89",
    "producer_runtime.schema.json": "97131d80a993d09d17c2c040b0e1cb2bd0eed5948d7a11608f26331d18f557e6",
    "quality_risk_intervals.schema.json": "2c8775fec18671030cf58ea0e94a3c99f8dac075fa9d5fe8dce724c2462f42ad",
    "replay_event_row.schema.json": "309749f73cf2a5a522617f975128ac005298f2fd71412903d76263702c1b6bd6",
}
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
V2_SCHEMA_NAMES = (
    "bundle_manifest.schema.json",
    "analysis_config.schema.json",
    "analysis_summary.schema.json",
)


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
    conversion: str = "COMPUTE_IDENTITY",
) -> dict[str, object]:
    summary = _transitional_summary()
    summary["lineage"]["fields"] = [{
        "artifactRole": artifact_role,
        "outputField": output_field,
        "sourceRole": None,
        "sourceColumn": None,
        "conversion": conversion,
        "dependencies": [dependency],
        "firstAvailableStage": None,
    }]
    return summary


def _lineage_field(
    summary: dict[str, object],
    artifact_role: str,
    output_field: str,
) -> dict[str, object]:
    return next(
        field
        for field in summary["lineage"]["fields"]
        if field["artifactRole"] == artifact_role
        and field["outputField"] == output_field
    )


def _v2_validator(name: str) -> Draft202012Validator:
    schema = json.loads((V2_CONTRACT_ROOT / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


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


def test_v2_schema_resources_are_byte_identical_and_leave_v1_digests_immutable():
    assert sorted(path.name for path in V2_PACKAGED_CONTRACT_ROOT.glob("*.schema.json")) == sorted(
        V2_SCHEMA_NAMES
    )
    for name in V2_SCHEMA_NAMES:
        assert (V2_PACKAGED_CONTRACT_ROOT / name).read_bytes() == (
            V2_CONTRACT_ROOT / name
        ).read_bytes()
    for name, expected_digest in LITERAL_ROOT_SHA256.items():
        assert hashlib.sha256((CONTRACT_ROOT / name).read_bytes()).hexdigest() == expected_digest


def test_v2_analysis_config_schema_requires_valid_seed_material_and_protocol():
    validator = _v2_validator("analysis_config.schema.json")
    actual = json.loads((ANALYSIS_ROOT / "analysis_config_v2.json").read_text(encoding="utf-8"))
    golden = json.loads(
        (V2_CONTRACT_ROOT / "golden-config/analysis_config.json").read_text(encoding="utf-8")
    )
    validator.validate(actual)
    validator.validate(golden)
    for key, value in (
        ("seedMaterial", None),
        ("seedMaterial", "sha256:not-a-digest"),
        ("seedProtocol", None),
        ("seedProtocol", "UNVERSIONED_RANDOM_SEED"),
    ):
        invalid = copy.deepcopy(actual)
        if value is None:
            del invalid["bootstrap"][key]
        else:
            invalid["bootstrap"][key] = value
        with pytest.raises(ValidationError):
            validator.validate(invalid)
    for key, forged_value in (
        ("schemaVersion", "sfep-analysis-config/v1"),
        ("analysisConfigVersion", "quality-analysis-v1"),
    ):
        invalid = copy.deepcopy(actual)
        invalid[key] = forged_value
        with pytest.raises(ValidationError):
            validator.validate(invalid)


def test_v2_summary_dependency_grammar_accepts_only_bootstrap_seed_terminals():
    validator = _v2_validator("analysis_summary.schema.json")
    for dependency in ("config.bootstrap.seedMaterial", "config.bootstrap.seedProtocol"):
        valid = _transitional_summary()
        valid["schemaVersion"] = "sfep-analysis-summary/v2"
        valid["lineage"]["fields"][0]["dependencies"] = [dependency]
        validator.validate(valid)
    invalid = _transitional_summary()
    invalid["schemaVersion"] = "sfep-analysis-summary/v2"
    invalid["lineage"]["fields"][0]["dependencies"] = ["config.bootstrap.seedGenerator"]
    with pytest.raises(ValidationError):
        validator.validate(invalid)


def test_v2_summary_analysis_config_output_fields_allow_only_seed_pair():
    validator = _v2_validator("analysis_summary.schema.json")
    for output_field, dependency in (
        ("bootstrap.seedMaterial", "config.bootstrap.seedMaterial"),
        ("bootstrap.seedProtocol", "config.bootstrap.seedProtocol"),
    ):
        valid = _transitional_summary()
        valid["schemaVersion"] = "sfep-analysis-summary/v2"
        field = next(
            field
            for field in valid["lineage"]["fields"]
            if field["artifactRole"] == "analysis_config"
            and field["outputField"] == "bootstrap.replicates"
        )
        field["outputField"] = output_field
        field["dependencies"] = [dependency]
        validator.validate(valid)
    for output_field in ("bootstrap.seed", "bootstrap.seedMaterialHash"):
        invalid = _transitional_summary()
        invalid["schemaVersion"] = "sfep-analysis-summary/v2"
        field = next(
            field
            for field in invalid["lineage"]["fields"]
            if field["artifactRole"] == "analysis_config"
            and field["outputField"] == "bootstrap.replicates"
        )
        field["outputField"] = output_field
        field["dependencies"] = ["config.bootstrap.seedMaterial"]
        with pytest.raises(ValidationError):
            validator.validate(invalid)


def test_v2_summary_schema_rejects_non_v2_schema_version():
    validator = _v2_validator("analysis_summary.schema.json")
    valid = _transitional_summary()
    valid["schemaVersion"] = "sfep-analysis-summary/v2"
    validator.validate(valid)
    valid["schemaVersion"] = "sfep-analysis-summary/v1"
    with pytest.raises(ValidationError):
        validator.validate(valid)


def test_v2_manifest_enforces_the_exact_artifact_version_matrix():
    validator = _v2_validator("bundle_manifest.schema.json")
    manifest = json.loads(
        (CONTRACT_ROOT / "golden-bundle/bundle_manifest.json").read_text(encoding="utf-8")
    )
    manifest["schemaVersion"] = "sfep-equipment-bundle/v2"
    manifest["artifacts"][0]["schemaVersion"] = "sfep-analysis-config/v2"
    manifest["artifacts"][5]["schemaVersion"] = "sfep-analysis-summary/v2"
    validator.validate(manifest)
    invalid_top_level = copy.deepcopy(manifest)
    invalid_top_level["schemaVersion"] = "sfep-equipment-bundle/v1"
    with pytest.raises(ValidationError):
        validator.validate(invalid_top_level)
    for index, forged_version in (
        (0, "sfep-analysis-config/v1"),
        (1, "sfep-producer-runtime/v2"),
        (2, "sfep-operating-ranges/v2"),
        (3, "sfep-quality-rules/v2"),
        (4, "sfep-replay-events/v2"),
        (5, "sfep-analysis-summary/v1"),
    ):
        invalid = copy.deepcopy(manifest)
        invalid["artifacts"][index]["schemaVersion"] = forged_version
        with pytest.raises(ValidationError):
            validator.validate(invalid)


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


def test_application_contract_binds_each_golden_leaf_to_one_exact_conversion():
    summary = _transitional_summary()
    fields = summary["lineage"]["fields"]
    assert len(fields) == 345
    schema_module.validate_normative_instance("analysis_summary.schema.json", summary)

    for index, field in enumerate(fields):
        forged = copy.deepcopy(summary)
        forged_field = forged["lineage"]["fields"][index]
        forged_field["conversion"] = (
            "TYPE1_QUANTILE"
            if field["conversion"] == "LINEAGE_INDEX_V1"
            else "LINEAGE_INDEX_V1"
        )
        with pytest.raises(ValidationError, match="conversion"):
            schema_module._validate_analysis_summary_application_contract(forged)


_RANGE_CONTEXT_KEYS = (
    "ap_plant",
    "ap_shift",
    "ap_thick_band",
    "ap_width_band",
    "f_jangip_gubun",
    "furnace_no",
    "hr_thick_band",
    "hr_width_band",
    "slab_width_band",
    "sm_plant",
    "steel_grade",
    "steel_usage",
)


@pytest.mark.parametrize(
    ("output_field", "conversion", "dependency"),
    [
        ("ranges[].ruleId", "COMPUTE_IDENTITY", "config.fields[].field"),
        ("ranges[].field", "COPY_FIELD_METADATA", "config.fields[].field"),
        ("ranges[].fieldRole", "COPY_FIELD_METADATA", "config.fields[].featureRole"),
        (
            "ranges[].firstAvailableStage",
            "COPY_FIELD_METADATA",
            "config.fields[].firstAvailableStage",
        ),
        (
            "ranges[].equipmentType",
            "COPY_FIELD_METADATA",
            "config.fields[].equipmentType",
        ),
        (
            "ranges[].equipmentId",
            "DERIVE_RANGE_GROUP",
            "population.REFERENCE",
        ),
        (
            "ranges[].contextLevel",
            "DERIVE_RANGE_GROUP",
            "config.rangeContextHierarchies[].levels[][]",
        ),
        (
            "ranges[].context",
            "DERIVE_RANGE_GROUP",
            "config.rangeContextHierarchies[].levels[][]",
        ),
        ("ranges[].support", "COUNT_RANGE_SUPPORT", "population.REFERENCE"),
        ("ranges[].median", "TYPE1_QUANTILE", "population.REFERENCE"),
        (
            "ranges[].lowerTailEnabled",
            "APPLY_RANGE_TAIL_POLICY",
            "config.operatingRanges.extremeTailMinimumSupport",
        ),
        (
            "ranges[].upperTailEnabled",
            "APPLY_RANGE_TAIL_POLICY",
            "config.operatingRanges.extremeTailMinimumSupport",
        ),
    ]
    + [
        (
            f"ranges[].context.{key}",
            "DERIVE_RANGE_GROUP",
            "config.rangeContextHierarchies[].levels[][]",
        )
        for key in _RANGE_CONTEXT_KEYS
    ]
    + [
        (f"ranges[].{quantile}", "TYPE1_QUANTILE", "population.REFERENCE")
        for quantile in ("p01", "p05", "p95", "p99")
    ],
)
def test_nonempty_operating_range_lineage_uses_exact_operation_families(
    output_field,
    conversion,
    dependency,
):
    summary = _summary_with_derived_lineage_field(
        "equipment_operating_ranges",
        output_field,
        dependency,
        conversion,
    )
    schema_module.validate_normative_instance("analysis_summary.schema.json", summary)

    forged = copy.deepcopy(summary)
    forged["lineage"]["fields"][0]["conversion"] = (
        "TYPE1_QUANTILE"
        if conversion != "TYPE1_QUANTILE"
        else "DERIVE_RANGE_GROUP"
    )
    with pytest.raises(ValidationError, match="conversion"):
        schema_module._validate_analysis_summary_application_contract(forged)


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
    ("artifact_role", "output_field", "dependency", "conversion"),
    [
        (
            "equipment_operating_ranges",
            "schemaVersion",
            "schema.equipment_operating_ranges",
            "COPY_SCHEMA_VERSION",
        ),
        (
            "equipment_operating_ranges",
            "criteriaId",
            "identity.criteria_id",
            "COPY_IDENTITY_VALUE",
        ),
        (
            "quality_risk_intervals",
            "schemaVersion",
            "schema.quality_risk_intervals",
            "COPY_SCHEMA_VERSION",
        ),
        (
            "quality_risk_intervals",
            "criteriaId",
            "identity.criteria_id",
            "COPY_IDENTITY_VALUE",
        ),
        (
            "replay_events",
            "schema_version",
            "schema.replay_events",
            "COPY_SCHEMA_VERSION",
        ),
        (
            "replay_events",
            "bundle_id",
            "identity.bundle_id",
            "COPY_IDENTITY_VALUE",
        ),
        (
            "replay_events",
            "criteria_id",
            "identity.criteria_id",
            "COPY_IDENTITY_VALUE",
        ),
    ],
)
def test_role_scoped_schema_and_identity_terminals_accept_truthful_leaf_mappings(
    artifact_role,
    output_field,
    dependency,
    conversion,
):
    schema_module.validate_normative_instance(
        "analysis_summary.schema.json",
        _summary_with_derived_lineage_field(
            artifact_role,
            output_field,
            dependency,
            conversion,
        ),
    )


@pytest.mark.parametrize(
    ("artifact_role", "output_field", "dependency", "conversion"),
    [
        (
            "equipment_operating_ranges",
            "schemaVersion",
            "schema.quality_risk_intervals",
            "COPY_SCHEMA_VERSION",
        ),
        (
            "equipment_operating_ranges",
            "criteriaId",
            "identity.bundle_id",
            "COPY_IDENTITY_VALUE",
        ),
        (
            "quality_risk_intervals",
            "schemaVersion",
            "schema.equipment_operating_ranges",
            "COPY_SCHEMA_VERSION",
        ),
        (
            "quality_risk_intervals",
            "criteriaId",
            "identity.criteria_projection_sha256",
            "COPY_IDENTITY_VALUE",
        ),
        (
            "replay_events",
            "schema_version",
            "schema.analysis_summary",
            "COPY_SCHEMA_VERSION",
        ),
        (
            "replay_events",
            "bundle_id",
            "identity.analysis_config_sha256",
            "COPY_IDENTITY_VALUE",
        ),
    ],
)
def test_role_scoped_schema_and_identity_terminals_reject_cross_role_forgery(
    artifact_role,
    output_field,
    dependency,
    conversion,
):
    with pytest.raises(ValidationError, match="terminal"):
        schema_module.validate_normative_instance(
            "analysis_summary.schema.json",
            _summary_with_derived_lineage_field(
                artifact_role,
                output_field,
                dependency,
                conversion,
            ),
        )


@pytest.mark.parametrize(
    ("artifact_role", "output_field", "plausible_but_wrong_conversion"),
    [
        ("analysis_config", "labelMaturityDays", "COMPUTE_IDENTITY"),
        ("bundle_manifest", "schemaVersion", "COPY_CANONICAL_CONFIG"),
        ("bundle_manifest", "bundleId", "COPY_IDENTITY_VALUE"),
        ("bundle_manifest", "artifacts[].sha256", "COMPUTE_BYTE_SIZE"),
        ("bundle_manifest", "artifacts[].sizeBytes", "COMPUTE_SHA256"),
        ("bundle_manifest", "asOf", "SELECT_TIME_BOUNDARY"),
        ("replay_events", "values_json.sm_plant", "PARSE_FINITE_BINARY64"),
        ("replay_events", "values_json.f_pre_temp", "COPY_SOURCE_SCALAR"),
        ("replay_events", "values_json.cast_date", "PARSE_HOUR_BUCKET"),
        ("replay_events", "values_json.f_ext_time", "PARSE_DATE"),
        ("replay_events", "replay_date", "CLASSIFY_REPLAY_SCHEDULE"),
        ("replay_events", "batch_step", "SELECT_STAGE_VALUE"),
        ("replay_events", "equipment_id", "CLASSIFY_REPLAY_SCHEDULE"),
        ("quality_risk_intervals", "rules[].analysisFamily", "COMPUTE_QUALITY_METRIC"),
        ("quality_risk_intervals", "rules[].discovery.support", "DERIVE_RULE_CANDIDATE"),
        ("quality_risk_intervals", "rules[].grade", "DERIVE_STAGE_ELIGIBILITY"),
        ("quality_risk_intervals", "rules[].earlyWarningEligible", "APPLY_GRADE_POLICY"),
        ("quality_risk_intervals", "rules[].displayMergeRuleIds[]", "DERIVE_RULE_CANDIDATE"),
        ("analysis_summary", "asOf", "COPY_BOUNDARY_DATE"),
        ("analysis_summary", "dateRange.from", "COPY_BOUNDARY_DATE"),
        ("analysis_summary", "sourceColumnProfiles[].total", "COUNT_PARTITION"),
        ("analysis_summary", "driftMetrics[].reference.median", "TYPE1_QUANTILE"),
        ("analysis_summary", "holdoutMetrics[].alertRate.pointEstimate", "COUNT_PARTITION"),
    ],
)
def test_application_conversion_binding_rejects_plausible_cross_family_swaps(
    artifact_role,
    output_field,
    plausible_but_wrong_conversion,
):
    summary = _transitional_summary()
    field = _lineage_field(summary, artifact_role, output_field)
    assert field["conversion"] != plausible_but_wrong_conversion
    field["conversion"] = plausible_but_wrong_conversion
    with pytest.raises(ValidationError, match="conversion"):
        schema_module._validate_analysis_summary_application_contract(summary)


def test_summary_application_validation_closes_refs_order_cycles_roles_and_intervals():
    summary = _transitional_summary()
    root_validator = Draft202012Validator(
        json.loads((CONTRACT_ROOT / "analysis_summary.schema.json").read_bytes())
    )
    invalid_instances = []

    def lineage_field(
        instance: dict[str, object], artifact_role: str, output_field: str
    ) -> dict[str, object]:
        return next(
            item
            for item in instance["lineage"]["fields"]
            if item["artifactRole"] == artifact_role
            and item["outputField"] == output_field
        )

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
    reference_median = lineage_field(
        unsorted_dependencies,
        "analysis_summary",
        "driftMetrics[].reference.median",
    )
    reference_median["dependencies"] = list(reversed(reference_median["dependencies"]))
    invalid_instances.append(unsorted_dependencies)

    cycle = copy.deepcopy(summary)
    reference = lineage_field(
        cycle, "analysis_summary", "driftMetrics[].reference.median"
    )
    reference["dependencies"] = [
        "analysis_summary.driftMetrics[].holdout.median"
    ]
    holdout = lineage_field(
        cycle, "analysis_summary", "driftMetrics[].holdout.median"
    )
    holdout["dependencies"] = [
        "analysis_summary.driftMetrics[].reference.median"
    ]
    invalid_instances.append(cycle)

    wrong_role_terminal = copy.deepcopy(summary)
    runtime_field = lineage_field(
        wrong_role_terminal,
        "producer_runtime",
        "producer.sourceSha256",
    )
    runtime_field["dependencies"] = ["fur_hr.f_pre_temp"]
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
        **{
            f"equipment_quality/contracts/v1/{name}": CONTRACT_ROOT / name
            for name in SCHEMA_NAMES
        },
        **{
            f"equipment_quality/contracts/v2/{name}": V2_CONTRACT_ROOT / name
            for name in V2_SCHEMA_NAMES
        },
    }
    with zipfile.ZipFile(built_wheel) as archive:
        names = archive.namelist()
        schema_members = {name for name in names if name.endswith(".schema.json")}
        assert schema_members == set(expected)
        for member, normative_path in expected.items():
            assert names.count(member) == 1
            archived = archive.read(member)
            normative = normative_path.read_bytes()
            assert archived == normative
            if normative_path.parent == CONTRACT_ROOT:
                assert hashlib.sha256(archived).hexdigest() == LITERAL_ROOT_SHA256[
                    normative_path.name
                ]
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
    summary_instance_path = cwd / "analysis_summary.json"
    summary_instance_path.write_bytes(
        (CONTRACT_ROOT / "golden-expectation/analysis_summary.template.json")
        .read_bytes()
        .replace(b"@BUNDLE_ID@", b"sha256:" + b"1" * 64)
        .replace(b"@CRITERIA_ID@", b"sha256:" + b"2" * 64)
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
    environment["SFEP_SUMMARY_INSTANCE_PATH"] = str(summary_instance_path)
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
                "summary = json.loads(Path(os.environ['SFEP_SUMMARY_INSTANCE_PATH']).read_bytes())\n"
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
