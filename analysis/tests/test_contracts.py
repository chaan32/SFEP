from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import re
from datetime import date, timedelta
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
EXACT_ALLOWED_TOKENS = {
    b"@PRODUCER_RUNTIME_SHA256@", b"@CRITERIA_ID@", b"@BUNDLE_ID@",
    b"@SCHEMA_BUNDLE_MANIFEST_SHA256@", b"@SCHEMA_ANALYSIS_CONFIG_SHA256@",
    b"@SCHEMA_PRODUCER_RUNTIME_SHA256@", b"@SCHEMA_EQUIPMENT_OPERATING_RANGES_SHA256@",
    b"@SCHEMA_QUALITY_RISK_INTERVALS_SHA256@", b"@SCHEMA_ANALYSIS_SUMMARY_SHA256@",
    b"@SCHEMA_REPLAY_EVENTS_SHA256@", b"@SOURCE_SM_CC_SHA256@",
    b"@SOURCE_FUR_HR_SHA256@", b"@SOURCE_AP_SHA256@",
    b"@ARTIFACT_ANALYSIS_CONFIG_SHA256@", b"@ARTIFACT_PRODUCER_RUNTIME_SHA256@",
    b"@ARTIFACT_EQUIPMENT_OPERATING_RANGES_SHA256@",
    b"@ARTIFACT_QUALITY_RISK_INTERVALS_SHA256@", b"@ARTIFACT_REPLAY_EVENTS_SHA256@",
    b"@ARTIFACT_ANALYSIS_SUMMARY_SHA256@",
}
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
MATERIAL_CH1_1 = "sha256:b313bfca80b92882d529a186bb7fc082e65020c409956481283745421ec1ce5a"
TOKEN_RE = re.compile(rb"(?:" + rb"|".join(re.escape(token) for token in sorted(EXACT_ALLOWED_TOKENS)) + rb")")
LINEAGE_OUTPUT_PATTERNS = {
    "equipment_operating_ranges": re.compile(
        r"^(?:schemaVersion|criteriaId|asOf|ranges\[\]\.(?:ruleId|field|fieldRole|"
        r"firstAvailableStage|equipmentType|equipmentId|contextLevel|context(?:\.[a-z][a-z0-9_]*)?|"
        r"support|median|p01|p05|p95|p99|lowerTailEnabled|upperTailEnabled))$"
    ),
    "quality_risk_intervals": re.compile(
        r"^(?:schemaVersion|criteriaId|asOf|rules\[\]\.(?:ruleId|analysisFamily|evidenceFamily|"
        r"firstAvailableStage|equipmentType|applicationScope|equipmentId|fieldNames\[\]|"
        r"predicate\.allOf\[\]\.(?:field|type|lower|lowerInclusive|upper|upperInclusive|values\[\])|"
        r"applicationContext(?:\.[a-z][a-z0-9_]*)?|adjustmentLevel|adjustmentFieldsDropped\[\]|"
        r"adjustmentKind|grade|earlyWarningEligible|(?:discovery|confirmation)\.(?:support|defects|"
        r"crudeRate|crudeRateCiLower|crudeRateCiUpper|adjustedRate|comparatorAdjustedRate|"
        r"riskDifference|relativeRisk|relativeRiskCiLower|relativeRiskCiUpper|pValue|qValue|reasonCode)|"
        r"displayMergeRuleIds\[\]))$"
    ),
    "replay_events": re.compile(
        r"^(?:schema_version|bundle_id|criteria_id|event_id|replay_date|replay_hour|batch_kind|batch_id|"
        r"equipment_batch_id|batch_step|time_precision|material_key|equipment_type|equipment_id|"
        r"charge_id|slab_no|hr_coil_id|ap_prod_id|values_json\.[a-z][a-z0-9_]*)$"
    ),
    "analysis_summary": re.compile(
        r"^(?:schemaVersion|bundleId|criteriaId|asOf|innerSplitDate|evaluationMode|"
        r"dateRange\.(?:from|to)|splitCounts\.(?:reference|discovery|confirmation|holdout)\."
        r"(?:total|defects|nonDefects|unknownOrCensored|dateFrom|dateTo)|"
        r"quarantineCounts\.[A-Z][A-Z0-9_]*|labelCensoringCounts\.[A-Z][A-Z0-9_]*|"
        r"sourceColumnProfiles\[\]\.(?:sourceRole|column|dataType|total|missing|unique|"
        r"numeric\.(?:p05|median|p95)|levels\[\]\.(?:value|count))|"
        r"driftMetrics\[\]\.(?:field|dataType|(?:reference|holdout)\."
        r"(?:support|missingRate|p05|median|p95|levels\[\]\.(?:value|count)))|"
        r"holdoutMetrics\[\]\.(?:alertGrade|total|truePositive|falsePositive|trueNegative|falseNegative|"
        r"alertRate|precision|recall|baseDefectRate|lift|falseAlertsPer100|validBootstrapReplicates|"
        r"ci\.(?:alertRate|precision|recall|baseDefectRate|lift|falseAlertsPer100)|reasonCode))$"
    ),
}


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
    preimage_bytes = namespace.encode("utf-8") + b"\n" + _canonical_json_bytes(value)
    digest = hashlib.sha256(preimage_bytes).hexdigest()
    return preimage_bytes.decode("utf-8"), "sha256:" + digest


def _replace_tokens(data: bytes) -> bytes:
    return TOKEN_RE.sub(SHA_A.encode("ascii"), data)


def _canonical_json_bytes(value: object) -> bytes:
    def encode_finite_binary64(number: float) -> str:
        if not math.isfinite(number):
            raise ValueError("JSON numbers must be finite binary64 values")
        if number == 0.0:
            return "0"

        negative = number < 0.0
        shortest = repr(abs(number)).lower()
        coefficient, separator, exponent_text = shortest.partition("e")
        exponent = int(exponent_text) if separator else 0
        integer, dot, fraction = coefficient.partition(".")
        digits = (integer + fraction).lstrip("0")
        decimal_exponent = exponent - (len(fraction) if dot else 0)
        while digits.endswith("0"):
            digits = digits[:-1]
            decimal_exponent += 1

        if decimal_exponent >= 0:
            fixed = digits + ("0" * decimal_exponent)
        else:
            point = len(digits) + decimal_exponent
            fixed = (
                digits[:point] + "." + digits[point:]
                if point > 0
                else "0." + ("0" * -point) + digits
            )

        unsigned_candidates = {fixed}
        for point in range(1, len(digits) + 1):
            mantissa = digits if point == len(digits) else digits[:point] + "." + digits[point:]
            scientific_exponent = decimal_exponent + len(digits) - point
            unsigned_candidates.add(f"{mantissa}e{scientific_exponent}")

        prefix = "-" if negative else ""
        candidates = {
            prefix + candidate
            for candidate in unsigned_candidates
            if math.isfinite(float(prefix + candidate)) and float(prefix + candidate) == number
        }
        return min(
            candidates,
            key=lambda candidate: (len(candidate.encode("utf-8")), candidate.encode("utf-8")),
        )

    def encode(node: object) -> str:
        if node is None:
            return "null"
        if node is True:
            return "true"
        if node is False:
            return "false"
        if isinstance(node, int):
            return str(node)
        if isinstance(node, float):
            return encode_finite_binary64(node)
        if isinstance(node, str):
            return json.dumps(node, ensure_ascii=False, separators=(",", ":"))
        if isinstance(node, list):
            return "[" + ",".join(encode(item) for item in node) + "]"
        if isinstance(node, dict):
            if not all(isinstance(key, str) for key in node):
                raise TypeError("canonical JSON object keys must be strings")
            return "{" + ",".join(
                encode(key) + ":" + encode(node[key]) for key in sorted(node)
            ) + "}"
        raise TypeError(f"unsupported canonical JSON value: {type(node).__name__}")

    return (encode(value) + "\n").encode("utf-8")


def _validate_summary_application_contract(summary: dict) -> None:
    config = json.loads(Path("analysis/analysis_config.json").read_text(encoding="utf-8"))
    source_names = {
        "sm_cc": "sts_1sm_cc_1.csv", "fur_hr": "sts_2fur_hr_2.csv", "ap": "sts_3ap_3.csv",
    }
    source_rows: dict[str, list[dict[str, str]]] = {}
    source_headers: dict[str, set[str]] = {}
    for role, name in source_names.items():
        text = (CONTRACT / "golden-source" / name).read_text(encoding="cp949")
        reader = csv.DictReader(io.StringIO(text))
        source_headers[role] = set(reader.fieldnames or [])
        source_rows[role] = list(reader)
    config_fields = {
        (item["sourceRole"], item["sourceColumn"]): item
        for item in config["fields"] if item["sourceRole"] is not None
    }
    source_dependencies = {
        f"{role}.{column}" for role, columns in source_headers.items() for column in columns
    }

    for count in summary["splitCounts"].values():
        assert count["total"] == count["defects"] + count["nonDefects"] + count["unknownOrCensored"]

    lineage = summary["lineage"]
    material_keys = [item["materialKey"] for item in lineage["materials"]]
    assert material_keys == sorted(material_keys, key=lambda item: item.encode("utf-8"))
    assert len(material_keys) == len(set(material_keys))
    known_materials = set(material_keys)
    for material in lineage["materials"]:
        expected_key = _digest_id(
            "sfep-material-key/v1", {"chargeId": material["chargeId"], "slabNo": material["slabNo"]}
        )[1]
        assert material["materialKey"] == expected_key
        for source_record in material["sourceRecords"]:
            role = source_record["role"]
            assert source_record["name"] == source_names[role]
            record_index = source_record["recordNumber"] - 2
            assert 0 <= record_index < len(source_rows[role])
            row = source_rows[role][record_index]
            if role == "sm_cc":
                assert row["charge_id"].strip() == material["chargeId"]
                assert row["slab_no"].strip() == material["slabNo"]
            elif role == "fur_hr":
                assert row["charge_id"].strip() == material["chargeId"]
                assert row["slab_no"].strip() == material["slabNo"]
                assert row["hr_coil_id"].strip() == material["hrCoilId"]
            else:
                assert row["hr_coil_id"].strip() == material["hrCoilId"]
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
        assert aggregate["split"] == populations[aggregate["populationRef"]]["split"]
        if aggregate["artifactRole"] == "equipment_operating_ranges":
            assert aggregate["comparatorDefinition"] == "NOT_APPLICABLE"
        else:
            assert aggregate["comparatorDefinition"] == "FIXED_POPULATION_STRATA_MINUS_CANDIDATE"
    lineage_node_ids = {
        f'{field["artifactRole"]}.{field["outputField"]}' for field in lineage["fields"]
    }
    dependency_graph: dict[str, list[str]] = {}
    for field in lineage["fields"]:
        node_id = f'{field["artifactRole"]}.{field["outputField"]}'
        output_pattern = LINEAGE_OUTPUT_PATTERNS.get(field["artifactRole"])
        if output_pattern is not None:
            assert output_pattern.fullmatch(field["outputField"]), "invalid lineage output field"
        raw = field["sourceRole"] is not None
        if raw:
            assert field["sourceColumn"] is not None
            assert field["firstAvailableStage"] is not None
            assert field["sourceColumn"] in source_headers[field["sourceRole"]]
            definition = config_fields[(field["sourceRole"], field["sourceColumn"])]
            assert field["artifactRole"] == "replay_events"
            assert field["outputField"] == f'values_json.{definition["field"]}'
            assert field["firstAvailableStage"] == definition["firstAvailableStage"]
        else:
            assert field["sourceColumn"] is None
            assert field["dependencies"]
        for dependency in field["dependencies"]:
            assert dependency in source_dependencies or dependency in lineage_node_ids
        dependency_graph[node_id] = [
            dependency for dependency in field["dependencies"] if dependency in lineage_node_ids
        ]

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        assert node_id not in visiting, "lineage dependency cycle"
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in dependency_graph[node_id]:
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in dependency_graph:
        visit(node_id)


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
                "materialKey": MATERIAL_CH1_1, "chargeId": "CH1", "slabNo": "1", "hrCoilId": "H001",
                "sourceRecords": [{"role": "sm_cc", "name": "sts_1sm_cc_1.csv", "recordNumber": 2}, {"role": "fur_hr", "name": "sts_2fur_hr_2.csv", "recordNumber": 2}, {"role": "ap", "name": "sts_3ap_3.csv", "recordNumber": 2}],
            }],
            "populations": [{"populationRef": "DISCOVERY", "split": "DISCOVERY", "materialKeys": [MATERIAL_CH1_1]}],
            "aggregates": [{
                "artifactRole": "equipment_operating_ranges", "ruleId": SHA_B,
                "split": "DISCOVERY", "populationRef": "DISCOVERY", "inputMaterialKeys": [MATERIAL_CH1_1],
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
        "values_json": {
            "sm_plant": "SM1", "steel_grade": "STS304", "steel_usage": "A",
            "cc_gubun": "CC1", "slab_gubun": "NORMAL", "tundish_temp": 1540.0,
            "mlac_ratio": 0.92, "delta_ferrite": 7.1, "ingre_cr": 18.2,
            "ingre_ni": 8.1, "ingre_s": 0.005, "slab_grind": "HSHS",
            "cast_date": "2025-01-01",
        },
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
    json_paths = [
        Path("analysis/analysis_config.json"), CONTRACT / "id-test-vectors.json",
        CONTRACT / "canonical-number-test-vectors.json",
    ]
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


def test_sm_lineage_source_record_must_match_both_composite_key_parts():
    wrong_slab_record = _summary()
    wrong_slab_record["lineage"]["materials"][0]["sourceRecords"][0][
        "recordNumber"
    ] = 3
    with pytest.raises(AssertionError):
        _validate_summary_application_contract(wrong_slab_record)


def test_golden_quality_candidate_is_derived_from_source_and_matches_metrics_and_lineage():
    root = CONTRACT / "golden-expectation"
    rules = json.loads(_replace_tokens((root / "quality_risk_intervals.template.json").read_bytes()))
    summary = json.loads(_replace_tokens((root / "analysis_summary.template.json").read_bytes()))
    fur_rows = list(csv.DictReader(io.StringIO(
        (CONTRACT / "golden-source" / "sts_2fur_hr_2.csv").read_text(encoding="cp949")
    )))
    rule = rules["rules"][0]
    rule_identity = {
        key: rule[key] for key in (
            "analysisFamily", "fieldNames", "predicate", "firstAvailableStage", "equipmentType",
            "applicationScope", "equipmentId", "applicationContext", "adjustmentLevel",
            "adjustmentFieldsDropped", "adjustmentKind",
        )
    }
    assert rule["ruleId"] == "sha256:" + hashlib.sha256(_canonical_json_bytes(rule_identity)).hexdigest()
    term = rule["predicate"]["allOf"][0]
    population = next(item for item in summary["lineage"]["populations"] if item["populationRef"] == "DISCOVERY")
    by_key = {
        _digest_id("sfep-material-key/v1", {"chargeId": row["charge_id"], "slabNo": row["slab_no"]})[1]: row
        for row in fur_rows
    }

    def selected(value: float) -> bool:
        lower_ok = term["lower"] is None or value > term["lower"] or (term["lowerInclusive"] and value == term["lower"])
        upper_ok = term["upper"] is None or value < term["upper"] or (term["upperInclusive"] and value == term["upper"])
        return lower_ok and upper_ok

    candidate_keys = [key for key in population["materialKeys"] if selected(float(by_key[key][term["field"]]))]
    aggregate = next(item for item in summary["lineage"]["aggregates"] if item["ruleId"] == rule["ruleId"])
    assert candidate_keys
    assert aggregate["inputMaterialKeys"] == candidate_keys
    assert rule["discovery"]["support"] == len(candidate_keys)
    ap_rows = list(csv.DictReader(io.StringIO(
        (CONTRACT / "golden-source" / "sts_3ap_3.csv").read_text(encoding="cp949")
    )))
    judge_by_hr = {row["hr_coil_id"]: row["judge"] for row in ap_rows if row["hr_coil_id"] != "H006"}
    defects = sum(judge_by_hr[by_key[key]["hr_coil_id"]] == "불량" for key in candidate_keys)
    assert rule["discovery"]["defects"] == defects
    assert rule["discovery"]["crudeRate"] == defects / len(candidate_keys)
    z = json.loads(Path("analysis/analysis_config.json").read_text(encoding="utf-8"))["wilsonZ"]
    n = len(candidate_keys)
    rate = defects / n
    denominator = 1 + z * z / n
    center = (rate + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / denominator
    assert rule["discovery"]["crudeRateCiLower"] == pytest.approx(center - radius)
    assert rule["discovery"]["crudeRateCiUpper"] == pytest.approx(center + radius)
    assert rule["discovery"]["adjustedRate"] is None
    assert rule["discovery"]["reasonCode"] == "LOW_SUPPORT"


def test_canonical_number_edge_vectors_use_shortest_finite_binary64_spelling():
    vectors = json.loads((CONTRACT / "canonical-number-test-vectors.json").read_text(encoding="utf-8"))
    assert vectors == [
        {"name": "one", "hex": "0x1.0000000000000p+0", "expected": "1"},
        {"name": "hundred-fixed-scientific-tie", "hex": "0x1.9000000000000p+6", "expected": "100"},
        {"name": "thousand-prefers-scientific", "hex": "0x1.f400000000000p+9", "expected": "1e3"},
        {"name": "ten-billion", "hex": "0x1.2a05f20000000p+33", "expected": "1e10"},
        {"name": "hundredth-fixed-scientific-tie", "hex": "0x1.47ae147ae147bp-7", "expected": "0.01"},
        {"name": "thousandth-prefers-scientific", "hex": "0x1.0624dd2f1a9fcp-10", "expected": "1e-3"},
        {"name": "decimal-placement-fixed-tie", "hex": "0x1.2c00000000000p+10", "expected": "1200"},
        {"name": "decimal-placement-scientific", "hex": "0x1.7700000000000p+13", "expected": "12e3"},
        {"name": "integral-float", "hex": "0x1.1800000000000p+10", "expected": "1120"},
        {"name": "negative-zero", "hex": "-0x0.0p+0", "expected": "0"},
        {"name": "small-exponent", "hex": "0x1.ad7f29abcaf48p-24", "expected": "1e-7"},
        {"name": "positive-exponent", "hex": "0x1.5af1d78b58c40p+66", "expected": "1e20"},
        {"name": "maximum-finite", "hex": "0x1.fffffffffffffp+1023", "expected": "17976931348623157e292"},
    ]
    for vector in vectors:
        value = float.fromhex(vector["hex"])
        encoded = _canonical_json_bytes({"value": value}).decode("utf-8")
        assert encoded == '{"value":' + vector["expected"] + "}\n"
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="finite"):
            _canonical_json_bytes({"value": value})


def test_raw_lineage_output_field_is_bound_to_artifact_source_and_config_mapping():
    for wrong_output in ("values_json.not_f_pre_temp", "values_json.f_heat_temp"):
        forged = _summary()
        forged["lineage"]["fields"][0]["outputField"] = wrong_output
        with pytest.raises(ValidationError):
            _validator("analysis_summary.schema.json").validate(forged)
        with pytest.raises((AssertionError, KeyError)):
            _validate_summary_application_contract(forged)

    valid_artifact_shapes = [
        ("equipment_operating_ranges", "ranges[].median"),
        ("quality_risk_intervals", "rules[].grade"),
        ("analysis_summary", "driftMetrics[].reference.median"),
    ]
    for artifact_role, output_field in valid_artifact_shapes:
        valid = _summary()
        valid["lineage"]["fields"][1].update(
            artifactRole=artifact_role, outputField=output_field
        )
        _validator("analysis_summary.schema.json").validate(valid)
        _validate_summary_application_contract(valid)

    wrong_artifact_shapes = [
        ("equipment_operating_ranges", "values_json.f_pre_temp"),
        ("quality_risk_intervals", "ranges[].median"),
        ("analysis_summary", "rules[].grade"),
    ]
    for artifact_role, output_field in wrong_artifact_shapes:
        invalid = _summary()
        invalid["lineage"]["fields"][1].update(
            artifactRole=artifact_role, outputField=output_field
        )
        with pytest.raises(ValidationError):
            _validator("analysis_summary.schema.json").validate(invalid)
        with pytest.raises(AssertionError, match="invalid lineage output field"):
            _validate_summary_application_contract(invalid)


def test_derived_lineage_node_dependencies_are_schema_valid_resolved_and_acyclic():
    valid = _summary()
    valid["lineage"]["fields"].append({
        "artifactRole": "analysis_summary",
        "outputField": "driftMetrics[].holdout.median",
        "sourceRole": None,
        "sourceColumn": None,
        "conversion": "COPY_DERIVED_METRIC",
        "dependencies": ["analysis_summary.driftMetrics[].reference.median"],
        "firstAvailableStage": None,
    })
    _validator("analysis_summary.schema.json").validate(valid)
    _validate_summary_application_contract(valid)

    dangling = copy.deepcopy(valid)
    dangling["lineage"]["fields"][-1]["dependencies"] = [
        "analysis_summary.driftMetrics[].missing.median"
    ]
    _validator("analysis_summary.schema.json").validate(dangling)
    with pytest.raises(AssertionError):
        _validate_summary_application_contract(dangling)

    cyclic = copy.deepcopy(valid)
    cyclic["lineage"]["fields"][1]["dependencies"] = [
        "analysis_summary.driftMetrics[].holdout.median"
    ]
    _validator("analysis_summary.schema.json").validate(cyclic)
    with pytest.raises(AssertionError, match="cycle"):
        _validate_summary_application_contract(cyclic)


def test_replay_values_reject_wrong_types_missing_keys_and_future_stage_fields():
    validator = _validator("replay_event_row.schema.json")
    bad_values = []
    bad_number = _replay_row()
    bad_number["values_json"]["tundish_temp"] = "bad"
    bad_values.append(bad_number)
    bad_date = _replay_row()
    bad_date["values_json"]["cast_date"] = False
    bad_values.append(bad_date)
    missing_required = _replay_row()
    missing_required["values_json"].pop("steel_grade")
    bad_values.append(missing_required)
    future_value = _replay_row()
    future_value["values_json"]["f_pre_temp"] = 1080.0
    bad_values.append(future_value)
    for bad in bad_values:
        with pytest.raises(ValidationError):
            validator.validate(bad)


def test_every_replay_stage_has_exact_keys_typed_values_and_explicit_nulls():
    rendered = _replace_tokens(
        (CONTRACT / "golden-expectation" / "replay_events.template.csv").read_bytes()
    ).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(rendered)))
    assert {row["batch_step"] for row in rows} == {
        "CAST_RECORDED", "FURNACE_CHARGED", "PREHEAT_COMPLETE", "HEAT_COMPLETE",
        "SOAK_COMPLETE", "FURNACE_EXTRACTED", "RM4_RECORDED", "AP_RECORDED_WITH_RESULT",
    }
    invalid_field = {
        "CAST_RECORDED": "tundish_temp", "FURNACE_CHARGED": "f_jangip_temp",
        "PREHEAT_COMPLETE": "f_pre_temp", "HEAT_COMPLETE": "f_heat_temp",
        "SOAK_COMPLETE": "f_sock_temp", "FURNACE_EXTRACTED": "f_ext_date",
        "RM4_RECORDED": "hr_date", "AP_RECORDED_WITH_RESULT": "judge",
    }
    for row in rows:
        instance = {key: (None if value == "" else value) for key, value in row.items()}
        instance["replay_hour"] = None if instance["replay_hour"] is None else int(instance["replay_hour"])
        values_text = instance["values_json"]
        instance["values_json"] = json.loads(values_text)
        assert values_text.encode("utf-8") == _canonical_json_bytes(instance["values_json"])[0:-1]
        validator = _validator("replay_event_row.schema.json")
        validator.validate(instance)

        missing = copy.deepcopy(instance)
        missing["values_json"].pop(next(iter(missing["values_json"])))
        with pytest.raises(ValidationError):
            validator.validate(missing)

        future = copy.deepcopy(instance)
        injected = "f_pre_temp" if instance["batch_step"] == "CAST_RECORDED" else "sm_plant"
        future["values_json"][injected] = None
        with pytest.raises(ValidationError):
            validator.validate(future)

        wrong_type = copy.deepcopy(instance)
        wrong_type["values_json"][invalid_field[instance["batch_step"]]] = False
        with pytest.raises(ValidationError):
            validator.validate(wrong_type)

        explicit_nulls = copy.deepcopy(instance)
        explicit_nulls["values_json"] = {key: None for key in instance["values_json"]}
        validator.validate(explicit_nulls)


@pytest.mark.parametrize("mutation", [
    lambda value: value["lineage"]["materials"][0]["sourceRecords"][0].update(recordNumber=999),
    lambda value: value["lineage"]["materials"][0]["sourceRecords"][0].update(name="sts_2fur_hr_2.csv"),
    lambda value: value["lineage"]["fields"][0].update(sourceColumn="not_a_source_column"),
    lambda value: value["lineage"]["fields"][0].update(firstAvailableStage="HEAT_COMPLETE"),
    lambda value: value["lineage"]["fields"][1].update(dependencies=["fur_hr.not_a_source_column"]),
    lambda value: value["lineage"]["fields"][1].update(dependencies=["analysis_summary.driftMetrics[].reference.median"]),
    lambda value: value["lineage"]["fields"][0].update(sourceRole="sm_cc"),
    lambda value: value["lineage"]["aggregates"][0].update(split="CONFIRMATION"),
    lambda value: (
        value["lineage"]["materials"][0].update(materialKey=SHA_B),
        value["lineage"]["populations"][0].update(materialKeys=[SHA_B]),
        value["lineage"]["aggregates"][0].update(inputMaterialKeys=[SHA_B]),
    ),
])
def test_lineage_resolution_rejects_false_or_dangling_provenance(mutation):
    summary = _summary()
    mutation(summary)
    with pytest.raises((AssertionError, ValidationError, KeyError)):
        _validate_summary_application_contract(summary)


def test_only_exact_fixed_role_provenance_tokens_are_accepted():
    assert set(TOKEN_RE.findall(b" ".join(EXACT_ALLOWED_TOKENS))) == EXACT_ALLOWED_TOKENS
    for forbidden in (b"@SOURCE_UNKNOWN_SHA256@", b"@ARTIFACT_FAKE_SHA256@", b"@SCHEMA_OTHER_SHA256@"):
        assert TOKEN_RE.findall(forbidden) == []


def test_ranges_below_extreme_support_cannot_enable_tail_flags():
    artifact = _range_artifact()
    artifact["ranges"][0]["lowerTailEnabled"] = True
    with pytest.raises(ValidationError):
        _validator("equipment_operating_ranges.schema.json").validate(artifact)


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


def test_slab_grind_config_is_categorical_product_state_evidence():
    config = json.loads(Path("analysis/analysis_config.json").read_text(encoding="utf-8"))
    slab_grind = next(field for field in config["fields"] if field["field"] == "slab_grind")
    assert slab_grind == {
        "field": "slab_grind",
        "sourceRole": "sm_cc",
        "sourceColumn": "slab_grind",
        "dataType": "STRING",
        "featureRole": "PRODUCT_STATE_REFERENCE",
        "equipmentType": "SM_CC",
        "firstAvailableStage": "CAST_RECORDED",
        "evidenceFamily": "DIMENSIONS",
        "dependencies": [],
    }


def test_slab_grind_replay_and_semantic_golden_use_a_categorical_code():
    validator = _validator("replay_event_row.schema.json")
    categorical = _replay_row()
    categorical["values_json"]["slab_grind"] = "HSHS"
    validator.validate(categorical)

    numeric = _replay_row()
    numeric["values_json"]["slab_grind"] = 0.0
    with pytest.raises(ValidationError):
        validator.validate(numeric)

    source_row = next(csv.DictReader(io.StringIO(
        (CONTRACT / "golden-source" / "sts_1sm_cc_1.csv").read_text(encoding="cp949")
    )))
    replay_rows = list(csv.DictReader(io.StringIO(
        (CONTRACT / "golden-expectation" / "replay_events.template.csv").read_text(
            encoding="utf-8"
        )
    )))
    cast_values = json.loads(replay_rows[0]["values_json"])
    projection = [
        json.loads(line)
        for line in (
            CONTRACT / "golden-expectation" / "criteria_projection.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    mature_features = next(
        item["features"] for item in projection if item["kind"] == "MATURE_QUALITY_INPUT"
    )
    assert source_row["slab_grind"] == "HSHS"
    assert cast_values["slab_grind"] == "HSHS"
    assert mature_features["slab_grind"] == "HSHS"


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
        "sts_1sm_cc_1.csv": (
            "sm_plant", "charge_id", "steel_grade", "steel_usage", "delta_ferrite",
            "ingre_cr", "ingre_ni", "ingre_s", "cast_date", "cc_gubun",
            "tundish_temp", "mlac_ratio", "slab_no", "slab_gubun", "slab_grind",
        ),
        "sts_2fur_hr_2.csv": (
            "charge_id", "slab_no", "furnace_no", "f_jangip_gubun",
            "f_jangip_temp", "f_bfg", "f_cog", "f_ldg", "f_bfg_per",
            "f_cog_per", "f_ldg_per", "f_pre_temp", "f_heat_temp", "f_sock_temp",
            "f_pre_interval", "f_heat_interval", "f_sock_interval", "f_ext_date",
            "f_ext_time", "hr_coil_id", "hr_date", "hr_thick", "hr_width",
            "rm4_temp", "rm_pitch", "slab_width",
        ),
        "sts_3ap_3.csv": (
            "judge", "hr_coil_id", "ap_plant", "ap_prod_id", "ap_date", "ap_shift",
            "ap_thick", "ap_width", "ap_line_speed",
        ),
    }
    decoded = {}
    for name, exact_header in expected.items():
        raw = (CONTRACT / "golden-source" / name).read_bytes()
        text = raw.decode("cp949")
        records = list(csv.reader(io.StringIO(text)))
        assert tuple(records[0]) == exact_header
        assert "\ufffd" not in text
        decoded[name] = list(csv.DictReader(io.StringIO(text)))
    sm = decoded["sts_1sm_cc_1.csv"]
    fur = decoded["sts_2fur_hr_2.csv"]
    assert {(row["charge_id"], row["slab_no"]) for row in sm} == {
        ("CH1", "1"), ("CH1", "2"), ("CH2", "1"), ("CH2", "2"),
        ("CH3", "1"), ("CH3", "2"), ("CH4", "1"), ("CH4", "2"),
        ("CH5", "1"), ("CH5", "2"), ("CH6", "1"), ("CH6", "2"),
    }
    assert {(row["charge_id"], row["slab_no"]) for row in sm} == {
        (row["charge_id"], row["slab_no"]) for row in fur
    }
    assert len({(row["charge_id"], row["slab_no"]) for row in fur}) == 12
    assert {row["furnace_no"] for row in fur} == {"1", "2", "3", "4"}
    ap = decoded["sts_3ap_3.csv"]
    with pytest.raises(UnicodeDecodeError):
        (CONTRACT / "golden-source" / "sts_3ap_3.csv").read_text(encoding="utf-8")
    assert {row["judge"] for row in ap} >= {"양품", "불량", ""}
    hr_ids = [row["hr_coil_id"] for row in ap]
    assert len(hr_ids) != len(set(hr_ids))
    assert any(value.startswith("UNLINKED") for value in hr_ids)


def test_golden_summary_censoring_and_counts_match_literal_source_audit():
    fur_materials = [
        ("CH1", "1", "H001", "2025-01-01"),
        ("CH1", "2", "H002", "2025-01-01"),
        ("CH2", "1", "H003", "2025-01-02"),
        ("CH2", "2", "H004", "2025-01-02"),
        ("CH3", "1", "H005", "2025-01-03"),
        ("CH3", "2", "H006", "2025-01-03"),
        ("CH4", "1", "H007", "2025-01-04"),
        ("CH4", "2", "H008", "2025-01-04"),
        ("CH5", "1", "H009", "2025-02-20"),
        ("CH5", "2", "H010", "2025-02-20"),
        ("CH6", "1", "H011", "2025-02-21"),
        ("CH6", "2", "H012", "2025-02-21"),
    ]
    ap_records = [
        ("H001", "A001", "2025-02-01", "양품"),
        ("H002", "A002", "2025-02-02", "양품"),
        ("H003", "A003", "2025-02-03", "불량"),
        ("H004", "A004", "2025-02-04", "양품"),
        ("H005", "A005", "2025-02-05", "양품"),
        ("H006", "A006", "2025-02-06", "불량"),
        ("H007", "A007", "2025-02-07", "양품"),
        ("H008", "A008", "2025-02-08", "양품"),
        ("H009", "A009", "2025-03-25", ""),
        ("H010", "A010", "2025-03-26", "양품"),
        ("H011", "A011", "2025-04-01", "불량"),
        ("H012", "A012", "2025-04-02", "양품"),
        ("H006", "A006-DUP", "2025-02-06", "양품"),
        ("UNLINKED-H999", "A999", "2025-02-10", "양품"),
    ]
    expected_outcomes = {
        "H001": ("DISCOVERY", "NON_DEFECT"),
        "H002": ("DISCOVERY", "NON_DEFECT"),
        "H003": ("DISCOVERY", "DEFECT"),
        "H004": ("DISCOVERY", "NON_DEFECT"),
        "H005": ("DISCOVERY", "NON_DEFECT"),
        "H006": ("REFERENCE", "AP_UNLINKED"),
        "H007": ("CONFIRMATION", "NON_DEFECT"),
        "H008": ("CONFIRMATION", "NON_DEFECT"),
        "H009": ("REFERENCE", "LABEL_NOT_YET_AVAILABLE"),
        "H010": ("REFERENCE", "LABEL_NOT_YET_AVAILABLE"),
        "H011": ("HOLDOUT", "DEFECT"),
        "H012": ("HOLDOUT", "NON_DEFECT"),
    }

    source_root = CONTRACT / "golden-source"
    actual_fur = [
        (row["charge_id"], row["slab_no"], row["hr_coil_id"], row["hr_date"])
        for row in csv.DictReader(io.StringIO(
            (source_root / "sts_2fur_hr_2.csv").read_text(encoding="cp949")
        ))
    ]
    actual_ap = [
        (row["hr_coil_id"], row["ap_prod_id"], row["ap_date"], row["judge"])
        for row in csv.DictReader(io.StringIO(
            (source_root / "sts_3ap_3.csv").read_text(encoding="cp949")
        ))
    ]
    assert actual_fur == fur_materials
    assert actual_ap == ap_records

    ap_by_coil: dict[str, list[tuple[str, str, str]]] = {}
    for coil_id, product_id, ap_date, judge in ap_records:
        ap_by_coil.setdefault(coil_id, []).append((product_id, ap_date, judge))
    fur_coils = {coil_id for _, _, coil_id, _ in fur_materials}
    assert {coil_id for coil_id, rows in ap_by_coil.items() if len(rows) > 1} == {"H006"}
    assert set(ap_by_coil) - fur_coils == {"UNLINKED-H999"}

    as_of = date(2025, 2, 20)
    maturity_cutoff = as_of - timedelta(days=38)
    inner_split = date(2025, 1, 3)
    assert maturity_cutoff == date(2025, 1, 13)
    audited_outcomes: dict[str, tuple[str, str]] = {}
    for _, _, coil_id, hr_date_text in fur_materials:
        hr_date = date.fromisoformat(hr_date_text)
        ap_rows = ap_by_coil.get(coil_id, [])
        if hr_date > as_of:
            assert len(ap_rows) == 1
            outcome = "DEFECT" if ap_rows[0][2] == "불량" else "NON_DEFECT"
            audited_outcomes[coil_id] = ("HOLDOUT", outcome)
        elif len(ap_rows) != 1:
            audited_outcomes[coil_id] = ("REFERENCE", "AP_UNLINKED")
        else:
            _, ap_date_text, judge = ap_rows[0]
            if hr_date > maturity_cutoff or date.fromisoformat(ap_date_text) > as_of:
                audited_outcomes[coil_id] = ("REFERENCE", "LABEL_NOT_YET_AVAILABLE")
            elif judge == "":
                audited_outcomes[coil_id] = ("REFERENCE", "LABEL_MISSING")
            else:
                split = "DISCOVERY" if hr_date <= inner_split else "CONFIRMATION"
                outcome = "DEFECT" if judge == "불량" else "NON_DEFECT"
                audited_outcomes[coil_id] = (split, outcome)
    assert audited_outcomes == expected_outcomes

    expected_censoring = {"AP_UNLINKED": 1, "LABEL_NOT_YET_AVAILABLE": 2}
    assert {
        reason: sum(outcome == reason for _, outcome in audited_outcomes.values())
        for reason in expected_censoring
    } == expected_censoring

    expectation_root = CONTRACT / "golden-expectation"
    summary = json.loads((expectation_root / "analysis_summary.template.json").read_bytes())
    assert summary["asOf"] == "2025-02-20"
    assert summary["innerSplitDate"] == "2025-01-03"
    assert summary["labelCensoringCounts"] == expected_censoring
    assert summary["quarantineCounts"] == {"DUPLICATE_KEY": 1, "UNLINKED_AP": 1}
    assert summary["splitCounts"] == {
        "confirmation": {
            "dateFrom": "2025-01-04", "dateTo": "2025-01-04", "defects": 0,
            "nonDefects": 2, "total": 2, "unknownOrCensored": 0,
        },
        "discovery": {
            "dateFrom": "2025-01-01", "dateTo": "2025-01-03", "defects": 1,
            "nonDefects": 4, "total": 5, "unknownOrCensored": 0,
        },
        "holdout": {
            "dateFrom": "2025-02-21", "dateTo": "2025-02-21", "defects": 1,
            "nonDefects": 1, "total": 2, "unknownOrCensored": 0,
        },
        "reference": {
            "dateFrom": "2025-01-01", "dateTo": "2025-02-20", "defects": 1,
            "nonDefects": 6, "total": 10, "unknownOrCensored": 3,
        },
    }
    expected_alerts = json.loads((expectation_root / "expected_alerts.json").read_bytes())
    assert expected_alerts["expectedReplayEventCount"] == 8
    assert expected_alerts["alerts"] == []


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
