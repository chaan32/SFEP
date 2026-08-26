from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import re
from pathlib import Path

import pytest
from jsonschema import ValidationError
from jsonschema.validators import validator_for


CONTRACT = Path("contracts/equipment-monitor/v1")
SCHEMA_NAMES = [
    "bundle_manifest.schema.json",
    "analysis_config.schema.json",
    "producer_runtime.schema.json",
    "equipment_operating_ranges.schema.json",
    "quality_risk_intervals.schema.json",
    "analysis_summary.schema.json",
    "replay_event_row.schema.json",
]
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
TOKEN_RE = re.compile(
    rb"@(?:PRODUCER_RUNTIME_SHA256|CRITERIA_ID|BUNDLE_ID|"
    rb"SCHEMA_[A-Z0-9_]+_SHA256|SOURCE_[A-Z0-9_]+_SHA256|"
    rb"ARTIFACT_[A-Z0-9_]+_SHA256)@"
)


def _load_schema(name: str) -> dict:
    return json.loads((CONTRACT / name).read_text(encoding="utf-8"))


def _assert_all_object_schemas_closed(node: object, location: str = "$") -> None:
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, location
        for key, value in node.items():
            _assert_all_object_schemas_closed(value, f"{location}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _assert_all_object_schemas_closed(value, f"{location}[{index}]")


def _validator(schema_name: str):
    schema = _load_schema(schema_name)
    cls = validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def _digest_id(namespace: str, value: dict) -> tuple[str, str]:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    preimage = namespace + "\n" + canonical
    digest = hashlib.sha256(preimage.encode("utf-8")).hexdigest()
    return preimage, "sha256:" + digest


def _replace_tokens(data: bytes) -> bytes:
    return TOKEN_RE.sub(SHA_A.encode("ascii"), data)


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _validate_summary_application_contract(summary: dict) -> None:
    for count in summary["splitCounts"].values():
        assert count["total"] == count["defects"] + count["nonDefects"] + count["unknownOrCensored"]

    lineage = summary["lineage"]
    material_keys = [item["materialKey"] for item in lineage["materials"]]
    assert material_keys == sorted(material_keys, key=lambda item: item.encode("utf-8"))
    assert len(material_keys) == len(set(material_keys))
    known_materials = set(material_keys)
    populations = {item["populationRef"]: item for item in lineage["populations"]}
    assert len(populations) == len(lineage["populations"])
    for population in populations.values():
        keys = population["materialKeys"]
        assert keys == sorted(keys, key=lambda item: item.encode("utf-8"))
        assert set(keys) <= known_materials
    for aggregate in lineage["aggregates"]:
        keys = aggregate["inputMaterialKeys"]
        assert keys == sorted(keys, key=lambda item: item.encode("utf-8"))
        assert set(keys) <= set(populations[aggregate["populationRef"]]["materialKeys"])
        if aggregate["artifactRole"] == "equipment_operating_ranges":
            assert aggregate["comparatorDefinition"] == "NOT_APPLICABLE"
        else:
            assert aggregate["comparatorDefinition"] == "FIXED_POPULATION_STRATA_MINUS_CANDIDATE"
    for field in lineage["fields"]:
        raw = field["sourceRole"] is not None
        if raw:
            assert field["sourceColumn"] is not None
            assert field["firstAvailableStage"] is not None
        else:
            assert field["sourceColumn"] is None
            assert field["dependencies"]


def _manifest() -> dict:
    criteria_identity = {
        "analysis_config_sha256": SHA_A,
        "as_of": "2025-01-03",
        "criteria_projection_sha256": SHA_A,
        "producer_runtime_sha256": SHA_A,
        "schema.analysis_config.sha256": SHA_A,
        "schema.equipment_operating_ranges.sha256": SHA_A,
        "schema.producer_runtime.sha256": SHA_A,
        "schema.quality_risk_intervals.sha256": SHA_A,
    }
    identity = {
        "analysis_config_sha256": SHA_A,
        "criteria_id": SHA_B,
        "producer_runtime_sha256": SHA_A,
        "schema.analysis_config.sha256": SHA_A,
        "schema.analysis_summary.sha256": SHA_A,
        "schema.bundle_manifest.sha256": SHA_A,
        "schema.equipment_operating_ranges.sha256": SHA_A,
        "schema.producer_runtime.sha256": SHA_A,
        "schema.quality_risk_intervals.sha256": SHA_A,
        "schema.replay_events.sha256": SHA_A,
        "source.ap.name": "sts_3ap_3.csv",
        "source.ap.sha256": SHA_A,
        "source.ap.size_bytes": "100",
        "source.fur_hr.name": "sts_2fur_hr_2.csv",
        "source.fur_hr.sha256": SHA_A,
        "source.fur_hr.size_bytes": "100",
        "source.sm_cc.name": "sts_1sm_cc_1.csv",
        "source.sm_cc.sha256": SHA_A,
        "source.sm_cc.size_bytes": "100",
    }
    roles = [
        ("analysis_config", "sfep-analysis-config/v1"),
        ("producer_runtime", "sfep-producer-runtime/v1"),
        ("equipment_operating_ranges", "sfep-operating-ranges/v1"),
        ("quality_risk_intervals", "sfep-quality-rules/v1"),
        ("replay_events", "sfep-replay-events/v1"),
        ("analysis_summary", "sfep-analysis-summary/v1"),
    ]
    return {
        "schemaVersion": "sfep-equipment-bundle/v1",
        "bundleId": SHA_A,
        "criteriaId": SHA_B,
        "identity": identity,
        "criteriaIdentity": criteria_identity,
        "asOf": "2025-01-03",
        "timezone": "Asia/Seoul",
        "labelMaturityDays": 38,
        "artifacts": [
            {"role": role, "sizeBytes": 1, "sha256": SHA_A, "schemaVersion": version}
            for role, version in roles
        ],
    }


def _runtime() -> dict:
    package = {
        "name": "jsonschema",
        "version": "4.24.0",
        "direct": True,
        "wheelFilename": "jsonschema-4.24.0-py3-none-any.whl",
        "wheelTag": "py3-none-any",
        "wheelSha256": SHA_A,
        "installedCodeTreeSha256": SHA_A,
    }
    return {
        "schemaVersion": "sfep-producer-runtime/v1",
        "platform": {
            "system": "Darwin",
            "machine": "arm64",
            "macosProductVersion": "15.6",
            "sysconfigPlatform": "macosx-15.0-arm64",
        },
        "python": {
            "implementation": "CPython",
            "version": "3.12.10",
            "build": "main, Apr  8 2025, 12:00:00",
            "cacheTag": "cpython-312",
            "soabi": "cpython-312-darwin",
            "executableSha256": SHA_A,
        },
        "pipVersion": "25.1.1",
        "locks": {
            "pyproject": SHA_A,
            "bootstrap": SHA_A,
            "buildRequirements": SHA_A,
            "requirements": SHA_A,
            "wheelhouse": SHA_A,
            "producer": SHA_A,
        },
        "packages": [package],
        "producer": {
            "name": "equipment-quality",
            "version": "0.1.0",
            "wheelFilename": "equipment_quality-0.1.0-py3-none-any.whl",
            "wheelSha256": SHA_A,
            "installedCodeTreeSha256": SHA_A,
            "sourceSha256": SHA_A,
        },
        "environmentPolicy": {
            "pythonHashSeed": "0",
            "timezone": "Asia/Seoul",
            "localeIndependentParsing": True,
            "floatPolicy": "IEEE754_BINARY64_FINITE",
        },
    }


def _range_artifact() -> dict:
    return {
        "schemaVersion": "sfep-operating-ranges/v1",
        "criteriaId": SHA_A,
        "asOf": "2025-01-03",
        "ranges": [{
            "ruleId": SHA_B,
            "field": "f_pre_temp",
            "fieldRole": "DIRECT_OPERATION",
            "firstAvailableStage": "PREHEAT_COMPLETE",
            "equipmentType": "FURNACE",
            "equipmentId": "1",
            "contextLevel": 0,
            "context": {"furnace_no": "1"},
            "support": 400,
            "median": 1120.0,
            "p01": None,
            "p05": 1080.0,
            "p95": 1160.0,
            "p99": None,
            "lowerTailEnabled": False,
            "upperTailEnabled": False,
        }],
    }


def _metric(confirmation: bool = False) -> dict:
    return {
        "support": 200 if not confirmation else 100,
        "defects": 5,
        "crudeRate": 0.025,
        "crudeRateCiLower": 0.0107,
        "crudeRateCiUpper": 0.0572,
        "adjustedRate": 0.025,
        "comparatorAdjustedRate": 0.01,
        "riskDifference": 0.015,
        "relativeRisk": 2.5,
        "relativeRiskCiLower": None if confirmation else 1.1,
        "relativeRiskCiUpper": None if confirmation else 4.5,
        "pValue": None if confirmation else 0.01,
        "qValue": None if confirmation else 0.04,
        "reasonCode": "NONE",
    }


def _rules_artifact() -> dict:
    return {
        "schemaVersion": "sfep-quality-rules/v1",
        "criteriaId": SHA_A,
        "asOf": "2025-01-03",
        "rules": [{
            "ruleId": SHA_B,
            "analysisFamily": "NUMERIC",
            "evidenceFamily": "PREHEAT",
            "firstAvailableStage": "PREHEAT_COMPLETE",
            "equipmentType": "FURNACE",
            "applicationScope": "PROCESS_GLOBAL",
            "equipmentId": "ALL",
            "fieldNames": ["f_pre_temp"],
            "predicate": {"allOf": [{
                "field": "f_pre_temp",
                "type": "NUMERIC_INTERVAL",
                "lower": 1150.0,
                "lowerInclusive": True,
                "upper": None,
                "upperInclusive": False,
                "values": None,
            }]},
            "applicationContext": {},
            "adjustmentLevel": 0,
            "adjustmentFieldsDropped": ["f_pre_temp"],
            "adjustmentKind": "STRATIFIED",
            "grade": "CAUTION",
            "earlyWarningEligible": True,
            "discovery": _metric(),
            "confirmation": _metric(True),
            "displayMergeRuleIds": [],
        }],
    }


def _count(total: int, defects: int, non_defects: int, unknown: int) -> dict:
    return {
        "total": total,
        "defects": defects,
        "nonDefects": non_defects,
        "unknownOrCensored": unknown,
        "dateFrom": "2025-01-01",
        "dateTo": "2025-01-03",
    }


def _summary() -> dict:
    return {
        "schemaVersion": "sfep-analysis-summary/v1",
        "bundleId": SHA_A,
        "criteriaId": SHA_B,
        "asOf": "2025-01-03",
        "innerSplitDate": "2025-01-01",
        "evaluationMode": "LOCKED_RETROSPECTIVE_HOLDOUT",
        "dateRange": {"from": "2025-01-01", "to": "2025-02-01"},
        "splitCounts": {
            "reference": _count(8, 1, 5, 2),
            "discovery": _count(4, 1, 3, 0),
            "confirmation": _count(2, 0, 2, 0),
            "holdout": _count(4, 1, 2, 1),
        },
        "quarantineCounts": {"DUPLICATE_KEY": 1, "UNLINKED_AP": 1},
        "labelCensoringCounts": {"LABEL_NOT_YET_AVAILABLE": 2},
        "sourceColumnProfiles": [{
            "sourceRole": "fur_hr", "column": "f_pre_temp", "dataType": "NUMBER",
            "total": 12, "missing": 0, "unique": 12,
            "numeric": {"p05": 1080.0, "median": 1120.0, "p95": 1160.0},
            "levels": None,
        }],
        "driftMetrics": [{
            "field": "f_pre_temp", "dataType": "NUMBER",
            "reference": {"support": 8, "missingRate": 0.0, "p05": 1080.0, "median": 1120.0, "p95": 1160.0, "levels": None},
            "holdout": {"support": 4, "missingRate": 0.0, "p05": 1090.0, "median": 1130.0, "p95": 1170.0, "levels": None},
        }],
        "holdoutMetrics": [{
            "alertGrade": "DANGER", "total": 3, "truePositive": 1, "falsePositive": 0,
            "trueNegative": 2, "falseNegative": 0, "alertRate": 0.3333333333333333,
            "precision": 1.0, "recall": 1.0, "baseDefectRate": 0.3333333333333333,
            "lift": 3.0, "falseAlertsPer100": 0.0, "validBootstrapReplicates": 2000,
            "ci": {"alertRate": [0.0, 1.0], "precision": [1.0, 1.0], "recall": [1.0, 1.0], "baseDefectRate": [0.0, 1.0], "lift": [1.0, 3.0], "falseAlertsPer100": [0.0, 0.0]},
            "reasonCode": "NONE",
        }],
        "lineage": {
            "fields": [{
                "artifactRole": "replay_events", "outputField": "values_json.f_pre_temp",
                "sourceRole": "fur_hr", "sourceColumn": "f_pre_temp", "conversion": "PARSE_FINITE_BINARY64",
                "dependencies": [], "firstAvailableStage": "PREHEAT_COMPLETE",
            }, {
                "artifactRole": "analysis_summary", "outputField": "driftMetrics[].reference.median",
                "sourceRole": None, "sourceColumn": None, "conversion": "TYPE1_QUANTILE",
                "dependencies": ["fur_hr.f_pre_temp"], "firstAvailableStage": None,
            }],
            "materials": [{
                "materialKey": SHA_A, "chargeId": "CH1", "slabNo": "1", "hrCoilId": "H001",
                "sourceRecords": [{"role": "sm_cc", "name": "sts_1sm_cc_1.csv", "recordNumber": 2}, {"role": "fur_hr", "name": "sts_2fur_hr_2.csv", "recordNumber": 2}, {"role": "ap", "name": "sts_3ap_3.csv", "recordNumber": 2}],
            }],
            "populations": [{"populationRef": "DISCOVERY", "split": "DISCOVERY", "materialKeys": [SHA_A]}],
            "aggregates": [{
                "artifactRole": "equipment_operating_ranges", "ruleId": SHA_B,
                "split": "REFERENCE", "populationRef": "DISCOVERY", "inputMaterialKeys": [SHA_A],
                "comparatorDefinition": "NOT_APPLICABLE",
                "filters": ["isfinite(f_pre_temp)"], "transformations": ["TYPE1_QUANTILE"],
            }],
        },
    }


def _replay_row() -> dict:
    return {
        "schema_version": "sfep-replay-events/v1", "bundle_id": SHA_A,
        "criteria_id": SHA_B, "event_id": SHA_A, "replay_date": "2025-01-01",
        "replay_hour": None, "batch_kind": "CAST_DAY", "batch_id": SHA_A,
        "equipment_batch_id": None, "batch_step": "CAST_RECORDED", "time_precision": "DAY",
        "material_key": SHA_B, "equipment_type": "SM_CC", "equipment_id": "SM1",
        "charge_id": "CH1", "slab_no": "1", "hr_coil_id": None, "ap_prod_id": None,
        "values_json": {"steel_grade": "STS304", "tundish_temp": 1540.0},
    }


VALID_INSTANCES = {
    "bundle_manifest.schema.json": _manifest,
    "analysis_config.schema.json": lambda: json.loads(Path("analysis/analysis_config.json").read_text()),
    "producer_runtime.schema.json": _runtime,
    "equipment_operating_ranges.schema.json": _range_artifact,
    "quality_risk_intervals.schema.json": _rules_artifact,
    "analysis_summary.schema.json": _summary,
    "replay_event_row.schema.json": _replay_row,
}


def test_all_normative_schemas_compile_and_close_every_object_boundary():
    assert len(SCHEMA_NAMES) == 7
    for name in SCHEMA_NAMES:
        schema = _load_schema(name)
        validator_for(schema).check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["additionalProperties"] is False
        _assert_all_object_schemas_closed(schema)


def test_normative_json_and_json_lines_are_canonical_utf8_bytes():
    json_paths = [Path("analysis/analysis_config.json"), CONTRACT / "id-test-vectors.json"]
    json_paths.extend(CONTRACT / name for name in SCHEMA_NAMES)
    json_paths.extend((CONTRACT / "golden-expectation").glob("*.json"))
    for path in json_paths:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert raw == _canonical_json_bytes(json.loads(raw))
    projection = (CONTRACT / "golden-expectation" / "criteria_projection.jsonl").read_bytes()
    assert projection.endswith(b"\n")
    for line in projection.splitlines(keepends=True):
        assert line == _canonical_json_bytes(json.loads(line))


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_each_schema_accepts_a_hand_authored_literal_instance(schema_name):
    _validator(schema_name).validate(VALID_INSTANCES[schema_name]())


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_each_top_level_object_rejects_unknown_missing_and_null(schema_name):
    validator = _validator(schema_name)
    valid = VALID_INSTANCES[schema_name]()
    unknown = copy.deepcopy(valid)
    unknown["unexpected"] = True
    missing = copy.deepcopy(valid)
    missing.pop(next(iter(_load_schema(schema_name)["required"])))
    null = copy.deepcopy(valid)
    null[next(iter(_load_schema(schema_name)["required"]))] = None
    for bad in (unknown, missing, null):
        with pytest.raises(ValidationError):
            validator.validate(bad)


def test_nested_objects_reject_unknown_fields_and_invalid_enum_or_range():
    cases = []
    manifest = _manifest()
    bad_path = copy.deepcopy(manifest)
    bad_path["artifacts"][0]["path"] = "analysis_config.json"
    bad_role = copy.deepcopy(manifest)
    bad_role["artifacts"][0]["role"] = "arbitrary"
    bad_size = copy.deepcopy(manifest)
    bad_size["artifacts"][0]["sizeBytes"] = -1
    cases.extend(("bundle_manifest.schema.json", value) for value in (bad_path, bad_role, bad_size))

    runtime = _runtime()
    runtime["platform"]["unexpected"] = True
    cases.append(("producer_runtime.schema.json", runtime))
    range_artifact = _range_artifact()
    range_artifact["ranges"][0]["fieldRole"] = "IDENTIFIER"
    cases.append(("equipment_operating_ranges.schema.json", range_artifact))
    range_with_unsupported_extreme = _range_artifact()
    range_with_unsupported_extreme["ranges"][0]["p01"] = 1000.0
    cases.append(("equipment_operating_ranges.schema.json", range_with_unsupported_extreme))
    rules = _rules_artifact()
    rules["rules"][0]["discovery"]["qValue"] = 1.1
    cases.append(("quality_risk_intervals.schema.json", rules))
    ap_early_warning = _rules_artifact()
    ap_early_warning["rules"][0]["firstAvailableStage"] = "AP_RECORDED_WITH_RESULT"
    ap_early_warning["rules"][0]["equipmentType"] = "AP"
    cases.append(("quality_risk_intervals.schema.json", ap_early_warning))
    summary = _summary()
    summary["lineage"]["materials"][0]["sourceRecords"][0]["path"] = "forbidden"
    cases.append(("analysis_summary.schema.json", summary))
    derived_without_dependencies = _summary()
    derived_without_dependencies["lineage"]["fields"][1]["dependencies"] = []
    cases.append(("analysis_summary.schema.json", derived_without_dependencies))
    raw_without_stage = _summary()
    raw_without_stage["lineage"]["fields"][0]["firstAvailableStage"] = None
    cases.append(("analysis_summary.schema.json", raw_without_stage))
    replay = _replay_row()
    replay["replay_hour"] = 24
    cases.append(("replay_event_row.schema.json", replay))
    early_hr_identity = _replay_row()
    early_hr_identity["hr_coil_id"] = "H001"
    cases.append(("replay_event_row.schema.json", early_hr_identity))

    for schema_name, bad in cases:
        with pytest.raises(ValidationError):
            _validator(schema_name).validate(bad)


def test_lineage_application_validation_rejects_broken_counts_refs_and_sorting():
    bad_count = _summary()
    bad_count["splitCounts"]["reference"]["total"] += 1
    with pytest.raises(AssertionError):
        _validate_summary_application_contract(bad_count)

    missing_material = _summary()
    missing_material["lineage"]["populations"][0]["materialKeys"] = [SHA_B]
    with pytest.raises(AssertionError):
        _validate_summary_application_contract(missing_material)

    unsorted = _summary()
    second = copy.deepcopy(unsorted["lineage"]["materials"][0])
    second["materialKey"] = "sha256:" + "0" * 64
    unsorted["lineage"]["materials"].append(second)
    with pytest.raises(AssertionError):
        _validate_summary_application_contract(unsorted)


def test_manifest_roles_are_exactly_ordered_and_unique():
    validator = _validator("bundle_manifest.schema.json")
    reversed_roles = _manifest()
    reversed_roles["artifacts"].reverse()
    duplicate = _manifest()
    duplicate["artifacts"][1] = copy.deepcopy(duplicate["artifacts"][0])
    for bad in (reversed_roles, duplicate):
        with pytest.raises(ValidationError):
            validator.validate(bad)


def test_analysis_config_contains_the_exact_quality_analysis_v1_policy():
    config = json.loads(Path("analysis/analysis_config.json").read_text(encoding="utf-8"))
    assert config["schemaVersion"] == "sfep-analysis-config/v1"
    assert config["analysisConfigVersion"] == "quality-analysis-v1"
    assert config["timezone"] == "Asia/Seoul"
    assert config["labelMaturityDays"] == 38
    assert config["splits"] == {"referenceFraction": 0.7, "discoveryFraction": 0.7}
    assert config["operatingRanges"]["minimumSupport"] == 400
    assert config["operatingRanges"]["extremeTailMinimumSupport"] == 2000
    assert config["qualityRisk"]["minimumDiscoverySupport"] == 200
    assert config["qualityRisk"]["minimumCautionDefects"] == 5
    assert config["qualityRisk"]["minimumDangerDefects"] == 10
    assert config["qualityRisk"]["minimumConfirmationSupport"] == 100
    assert config["qualityRisk"]["minimumConfirmationDefects"] == 5
    assert config["bootstrap"] == {"replicates": 2000, "minimumValidReplicates": 1900}
    assert config["wilsonZ"] == 1.959963984540054
    assert config["qualityRisk"]["bhQ"] == {"caution": 0.1, "danger": 0.05}
    assert config["qualityRisk"]["relativeRisk"] == {"caution": 1.5, "danger": 2.0}
    assert config["qualityRisk"]["riskDifference"] == {"caution": 0.005, "danger": 0.01}
    assert config["fixedInteractions"] == [
        ["tundish_temp", "mlac_ratio"], ["f_pre_interval", "f_pre_temp"],
        ["f_heat_interval", "f_heat_temp"], ["f_sock_interval", "f_sock_temp"],
        ["rm_pitch", "rm4_temp"], ["ap_line_speed", "ap_thick"],
    ]
    assert config["evidenceFamilies"] == [
        "STEEL_CHEMISTRY", "CASTING_STABILITY", "CHARGE", "FUEL_PROFILE", "PREHEAT",
        "HEATING", "SOAKING", "RM4", "DIMENSIONS", "AP",
    ]
    assert set(config["fdrFamilies"]) == {"NUMERIC", "CATEGORICAL", "INTERACTION"}
    assert {entry["field"]: entry["firstAvailableStage"] for entry in config["fields"]}["judge"] == "AP_RECORDED_WITH_RESULT"


def test_id_vectors_match_literal_preimages_and_independent_sha256():
    vectors = json.loads((CONTRACT / "id-test-vectors.json").read_text(encoding="utf-8"))
    assert vectors["materialKey"]["expected"] == (
        "sha256:b313bfca80b92882d529a186bb7fc082e65020c409956481283745421ec1ce5a"
    )
    for vector in vectors.values():
        preimage, expected = _digest_id(vector["namespace"], vector["object"])
        assert vector["preimage"] == preimage
        assert vector["expected"] == expected


def test_golden_sources_are_cp949_have_exact_headers_and_cover_edge_populations():
    expected = {
        "sts_1sm_cc_1.csv": "sm_plant,charge_id,steel_grade",
        "sts_2fur_hr_2.csv": "charge_id,slab_no,furnace_no",
        "sts_3ap_3.csv": "judge,hr_coil_id,ap_plant",
    }
    decoded = {}
    for name, prefix in expected.items():
        raw = (CONTRACT / "golden-source" / name).read_bytes()
        text = raw.decode("cp949")
        assert text.splitlines()[0].startswith(prefix)
        assert "\ufffd" not in text
        decoded[name] = list(csv.DictReader(io.StringIO(text)))
    fur = decoded["sts_2fur_hr_2.csv"]
    assert len({(row["charge_id"], row["slab_no"]) for row in fur}) == 12
    assert {row["furnace_no"] for row in fur} == {"1", "2", "3", "4"}
    ap = decoded["sts_3ap_3.csv"]
    with pytest.raises(UnicodeDecodeError):
        (CONTRACT / "golden-source" / "sts_3ap_3.csv").read_text(encoding="utf-8")
    assert {row["judge"] for row in ap} >= {"양품", "불량", ""}
    hr_ids = [row["hr_coil_id"] for row in ap]
    assert len(hr_ids) != len(set(hr_ids))
    assert any(value.startswith("UNLINKED") for value in hr_ids)


def test_golden_expectations_use_only_allowed_provenance_tokens_and_validate():
    root = CONTRACT / "golden-expectation"
    expected_names = {
        "criteria_projection.jsonl", "equipment_operating_ranges.template.json",
        "quality_risk_intervals.template.json", "replay_events.template.csv",
        "analysis_summary.template.json", "expected_alerts.json",
    }
    assert {path.name for path in root.iterdir()} == expected_names
    all_tokens: set[bytes] = set()
    for path in root.iterdir():
        raw = path.read_bytes()
        all_tokens.update(re.findall(rb"@[A-Z0-9_]+@", raw))
        assert not (set(re.findall(rb"@[A-Z0-9_]+@", raw)) - set(TOKEN_RE.findall(raw)))
    assert {b"@PRODUCER_RUNTIME_SHA256@", b"@CRITERIA_ID@", b"@BUNDLE_ID@"} <= all_tokens

    ranges = json.loads(_replace_tokens((root / "equipment_operating_ranges.template.json").read_bytes()))
    rules = json.loads(_replace_tokens((root / "quality_risk_intervals.template.json").read_bytes()))
    summary = json.loads(_replace_tokens((root / "analysis_summary.template.json").read_bytes()))
    _validator("equipment_operating_ranges.schema.json").validate(ranges)
    _validator("quality_risk_intervals.schema.json").validate(rules)
    _validator("analysis_summary.schema.json").validate(summary)
    _validate_summary_application_contract(summary)

    projection = (root / "criteria_projection.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(projection) >= 2
    assert {json.loads(line)["kind"] for line in projection} == {"OPERATING_RANGE_INPUT", "MATURE_QUALITY_INPUT"}

    replay_text = _replace_tokens((root / "replay_events.template.csv").read_bytes()).decode("utf-8")
    assert replay_text.splitlines()[0] == (
        "schema_version,bundle_id,criteria_id,event_id,replay_date,replay_hour,batch_kind,"
        "batch_id,equipment_batch_id,batch_step,time_precision,material_key,equipment_type,"
        "equipment_id,charge_id,slab_no,hr_coil_id,ap_prod_id,values_json"
    )
    rows = list(csv.DictReader(io.StringIO(replay_text)))
    assert rows
    assert [row["batch_step"] for row in rows] == [
        "CAST_RECORDED", "FURNACE_CHARGED", "PREHEAT_COMPLETE", "HEAT_COMPLETE",
        "SOAK_COMPLETE", "FURNACE_EXTRACTED", "RM4_RECORDED", "AP_RECORDED_WITH_RESULT",
    ]
    assert len({row["event_id"] for row in rows}) == len(rows)
    for row in rows:
        instance = {key: (None if value == "" else value) for key, value in row.items()}
        instance["replay_hour"] = None if instance["replay_hour"] is None else int(instance["replay_hour"])
        instance["values_json"] = json.loads(instance["values_json"])
        _validator("replay_event_row.schema.json").validate(instance)
        if instance["batch_kind"] == "FURNACE_HOUR":
            batch_object = {"batchKind": "FURNACE_HOUR", "replayDate": instance["replay_date"], "replayHour": instance["replay_hour"]}
        else:
            batch_object = {"batchKind": instance["batch_kind"], "replayDate": instance["replay_date"]}
        assert instance["batch_id"] == _digest_id("sfep-batch-id/v1", batch_object)[1]
        if instance["equipment_type"] == "FURNACE":
            equipment_object = {"batchId": instance["batch_id"], "equipmentId": instance["equipment_id"], "equipmentType": "FURNACE"}
            assert instance["equipment_batch_id"] == _digest_id("sfep-equipment-batch-id/v1", equipment_object)[1]
        event_object = {
            "batchId": instance["batch_id"], "batchStep": instance["batch_step"],
            "equipmentBatchId": instance["equipment_batch_id"], "equipmentId": instance["equipment_id"],
            "equipmentType": instance["equipment_type"], "materialKey": instance["material_key"],
        }
        assert instance["event_id"] == _digest_id("sfep-event-id/v1", event_object)[1]
    alerts = json.loads(_replace_tokens((root / "expected_alerts.json").read_bytes()))
    assert alerts["schemaVersion"] == "sfep-expected-alerts/v1"
    assert alerts["expectedReplayEventCount"] == len(rows)
    assert alerts["alerts"] == []
