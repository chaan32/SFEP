"""Strict source CSV and analysis-configuration validation."""

from __future__ import annotations

import csv
import hashlib
import io
import importlib.resources
import json
import math
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from urllib.parse import unquote_to_bytes

import pandas as pd
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing.exceptions import Unresolvable

from equipment_quality.deterministic import sha256_uri
from equipment_quality.models import AnalysisConfig, InputTables, SourceFile


_SOURCE_SPECS = (
    (
        "sm_cc",
        "sts_1sm_cc_1.csv",
        (
            "sm_plant", "charge_id", "steel_grade", "steel_usage", "delta_ferrite",
            "ingre_cr", "ingre_ni", "ingre_s", "cast_date", "cc_gubun",
            "tundish_temp", "mlac_ratio", "slab_no", "slab_gubun", "slab_grind",
        ),
    ),
    (
        "fur_hr",
        "sts_2fur_hr_2.csv",
        (
            "charge_id", "slab_no", "furnace_no", "f_jangip_gubun",
            "f_jangip_temp", "f_bfg", "f_cog", "f_ldg", "f_bfg_per",
            "f_cog_per", "f_ldg_per", "f_pre_temp", "f_heat_temp", "f_sock_temp",
            "f_pre_interval", "f_heat_interval", "f_sock_interval", "f_ext_date",
            "f_ext_time", "hr_coil_id", "hr_date", "hr_thick", "hr_width",
            "rm4_temp", "rm_pitch", "slab_width",
        ),
    ),
    (
        "ap",
        "sts_3ap_3.csv",
        (
            "judge", "hr_coil_id", "ap_plant", "ap_prod_id", "ap_date", "ap_shift",
            "ap_thick", "ap_width", "ap_line_speed",
        ),
    ),
)

_IDENTIFIER_COLUMNS = {
    "sm_cc": {"charge_id", "slab_no"},
    "fur_hr": {"charge_id", "slab_no", "hr_coil_id"},
    "ap": {"hr_coil_id", "ap_prod_id"},
}
_DATE_COLUMNS = {
    "sm_cc": {"cast_date"},
    "fur_hr": {"f_ext_date", "hr_date"},
    "ap": {"ap_date"},
}
_NUMERIC_COLUMNS = {
    "sm_cc": {
        "delta_ferrite", "ingre_cr", "ingre_ni", "ingre_s", "tundish_temp",
        "mlac_ratio",
    },
    "fur_hr": {
        "f_jangip_temp", "f_bfg", "f_cog", "f_ldg", "f_bfg_per", "f_cog_per",
        "f_ldg_per", "f_pre_temp", "f_heat_temp", "f_sock_temp", "f_pre_interval",
        "f_heat_interval", "f_sock_interval", "hr_thick", "hr_width", "rm4_temp",
        "rm_pitch", "slab_width",
    },
    "ap": {"ap_thick", "ap_width", "ap_line_speed"},
}
_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_EQUIPMENT_TYPES = ("SM_CC", "FURNACE", "RM4", "AP")
_NORMATIVE_SCHEMA_NAMES = frozenset(
    {
        "bundle_manifest.schema.json",
        "analysis_config.schema.json",
        "producer_runtime.schema.json",
        "equipment_operating_ranges.schema.json",
        "quality_risk_intervals.schema.json",
        "analysis_summary.schema.json",
        "replay_event_row.schema.json",
    }
)
_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
# These pins authenticate the packaged copies against the normative v1 resources.
# Any intentional root-contract byte change must update its packaged copy and this
# independent literal in the same broader contract change.
_NORMATIVE_SCHEMA_SHA256 = MappingProxyType(
    {
        "analysis_config.schema.json": "fad28561dfe9d9fe3cd09b025bb18c2101be053cb094b08442ea45963b86f549",
        "analysis_summary.schema.json": "38e61d5d81f15ffb30bd3164c6f34b469b20f06a90e9e393dee1f2a54818bf0f",
        "bundle_manifest.schema.json": "666e880d296c0d7e3df5af1aa80e6865922ebfb337fb9aca48f93eddfd89e8a5",
        "equipment_operating_ranges.schema.json": "bee7d8be181dae4844c51d4627c5a1f068583b60a60c854f17035a8291cd7d89",
        "producer_runtime.schema.json": "97131d80a993d09d17c2c040b0e1cb2bd0eed5948d7a11608f26331d18f557e6",
        "quality_risk_intervals.schema.json": "2c8775fec18671030cf58ea0e94a3c99f8dac075fa9d5fe8dce724c2462f42ad",
        "replay_event_row.schema.json": "309749f73cf2a5a522617f975128ac005298f2fd71412903d76263702c1b6bd6",
    }
)
_NORMATIVE_SCHEMA_BYTES_CACHE: dict[str, bytes] = {}
_NORMATIVE_VALIDATOR_CACHE: dict[str, Draft202012Validator] = {}
_NORMATIVE_SCHEMA_CACHE_LOCK = RLock()

_LINEAGE_ARTIFACT_ORDER = {
    role: index
    for index, role in enumerate(
        (
            "bundle_manifest",
            "analysis_config",
            "producer_runtime",
            "equipment_operating_ranges",
            "quality_risk_intervals",
            "replay_events",
            "analysis_summary",
        )
    )
}
_LINEAGE_SOURCE_TERMINALS = frozenset(
    f"{role}.{column}"
    for role, _name, columns in _SOURCE_SPECS
    for column in columns
)
_LINEAGE_CONVERSIONS = frozenset(
    {
        "COPY_SOURCE_SCALAR",
        "PARSE_FINITE_BINARY64",
        "PARSE_DATE",
        "PARSE_HOUR_BUCKET",
        "DERIVE_FUEL_RATIO",
        "COPY_CANONICAL_CONFIG",
        "COPY_VERIFIED_RUNTIME",
        "COMPUTE_IDENTITY",
        "COMPUTE_SHA256",
        "TYPE1_QUANTILE",
        "COUNT_PARTITION",
        "PROFILE_SOURCE_COLUMN",
        "COMPARE_DISTRIBUTIONS",
        "COMPUTE_HOLDOUT_METRIC",
        "LINEAGE_INDEX_V1",
    }
)
_LINEAGE_FILTERS = frozenset(
    {
        "STAGE_AVAILABLE_AT_AS_OF",
        "FINITE_VALUE",
        "LABEL_AVAILABLE_AND_MATURE",
        "PREDICATE_MATCH",
        "INFORMATIVE_STRATA_ONLY",
        "FIXED_DISCOVERY_STRATA",
    }
)
_LINEAGE_TRANSFORMATIONS = frozenset(
    {
        "TYPE1_QUANTILE",
        "WILSON_SCORE_INTERVAL",
        "DIRECT_STANDARDIZATION",
        "MANTEL_HAENSZEL_RR",
        "CMH_NORMAL_APPROXIMATION",
        "BENJAMINI_HOCHBERG_FDR",
        "CHARGE_BLOCK_BOOTSTRAP_PERCENTILE_CI",
        "CONFIRMATION_WEIGHT_RENORMALIZATION",
        "GRADE_POLICY_V1",
        "DISPLAY_MERGE_V1",
    }
)
_LINEAGE_ROOT_PREFIXES = {
    "bundle_manifest": ("identity.", "schema.", "source."),
    "analysis_config": ("config.",),
    "producer_runtime": ("runtime.",),
    "equipment_operating_ranges": ("config.", "population.REFERENCE"),
    "quality_risk_intervals": (
        "config.",
        "population.DISCOVERY",
        "population.CONFIRMATION",
    ),
    "replay_events": ("config.", "identity."),
    "analysis_summary": (
        "config.",
        "identity.",
        "policy.",
        "population.",
        "runtime.",
        "schema.",
        "source.",
    ),
}
_LINEAGE_SOURCE_TERMINAL_ROLES = frozenset(
    {
        "equipment_operating_ranges",
        "quality_risk_intervals",
        "replay_events",
        "analysis_summary",
    }
)
_LINEAGE_NODE_DEPENDENCY_ROLES = {
    "bundle_manifest": frozenset(
        {
            "analysis_config",
            "producer_runtime",
            "equipment_operating_ranges",
            "quality_risk_intervals",
            "replay_events",
            "analysis_summary",
        }
    ),
    "analysis_config": frozenset(),
    "producer_runtime": frozenset(),
    "equipment_operating_ranges": frozenset({"analysis_config"}),
    "quality_risk_intervals": frozenset(
        {"analysis_config", "equipment_operating_ranges"}
    ),
    "replay_events": frozenset(
        {"equipment_operating_ranges", "quality_risk_intervals"}
    ),
    "analysis_summary": frozenset(_LINEAGE_ARTIFACT_ORDER),
}
_HOLDOUT_METRICS = (
    "alertRate",
    "precision",
    "recall",
    "baseDefectRate",
    "lift",
    "falseAlertsPer100",
)


class _MalformedSchemaResource(ValueError):
    pass


class _InvalidLocalSchemaReference(ValueError):
    pass


def _require_normative_schema_name(name: str) -> None:
    if type(name) is not str:
        raise TypeError("normative schema name must be a built-in str")
    if name not in _NORMATIVE_SCHEMA_NAMES:
        raise ValueError(f"unknown normative schema name: {name!r}")


def _reject_schema_json_constant(value: str) -> None:
    raise _MalformedSchemaResource(value)


def _reject_duplicate_schema_members(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise _MalformedSchemaResource(name)
        result[name] = value
    return result


def _reject_non_finite_schema_numbers(value: object) -> None:
    if type(value) is float:
        if not math.isfinite(value):
            raise _MalformedSchemaResource("non-finite number")
        return
    if type(value) is list:
        for item in value:
            _reject_non_finite_schema_numbers(item)
        return
    if type(value) is dict:
        for item in value.values():
            _reject_non_finite_schema_numbers(item)


def _freeze_schema(value: object) -> object:
    if type(value) is dict:
        return MappingProxyType(
            {name: _freeze_schema(item) for name, item in value.items()}
        )
    if type(value) is list:
        return tuple(_freeze_schema(item) for item in value)
    return value


def _decode_json_pointer_token(token: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        character = token[index]
        if character != "~":
            decoded.append(character)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise _InvalidLocalSchemaReference("invalid JSON Pointer escape")
        decoded.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(decoded)


def _decode_local_json_pointer(reference: object) -> tuple[str, ...]:
    if type(reference) is not str or reference == "" or not reference.startswith("#"):
        raise _InvalidLocalSchemaReference("reference is not a local fragment")
    fragment = reference[1:]
    for index, character in enumerate(fragment):
        if character == "%" and (
            index + 2 >= len(fragment)
            or any(item not in "0123456789abcdefABCDEF" for item in fragment[index + 1:index + 3])
        ):
            raise _InvalidLocalSchemaReference("invalid URI escape")
    try:
        pointer = unquote_to_bytes(fragment).decode("utf-8")
    except UnicodeError as error:
        raise _InvalidLocalSchemaReference("invalid URI encoding") from error
    if pointer == "":
        return ()
    if not pointer.startswith("/"):
        raise _InvalidLocalSchemaReference("fragment is not a JSON Pointer")
    return tuple(
        _decode_json_pointer_token(token) for token in pointer[1:].split("/")
    )


def _resolve_local_json_pointer(
    schema: Mapping[str, object], reference: object
) -> object:
    target: object = schema
    for token in _decode_local_json_pointer(reference):
        if isinstance(target, Mapping):
            if token not in target:
                raise _InvalidLocalSchemaReference("object member does not exist")
            target = target[token]
        elif type(target) is list:
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                raise _InvalidLocalSchemaReference("invalid array index")
            index = int(token)
            if index >= len(target):
                raise _InvalidLocalSchemaReference("array index does not exist")
            target = target[index]
        else:
            raise _InvalidLocalSchemaReference("pointer traverses a scalar")
    return target


def _assert_local_schema_references(schema: Mapping[str, object]) -> None:
    pending: list[tuple[object, bool]] = [(schema, True)]
    visited: set[int] = set()
    while pending:
        value, is_document_root = pending.pop()
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in visited:
                continue
            visited.add(identity)
            if "$id" in value and (
                not is_document_root or type(value["$id"]) is not str
            ):
                raise _InvalidLocalSchemaReference(
                    "$id is allowed only as a built-in string at document root"
                )
            if "$dynamicRef" in value or "$recursiveRef" in value:
                raise _InvalidLocalSchemaReference(
                    "dynamic and recursive references are not allowed"
                )
            if "$ref" in value:
                reference = value["$ref"]
                if type(reference) is not str:
                    raise _InvalidLocalSchemaReference(
                        "$ref must be a built-in string"
                    )
                target = _resolve_local_json_pointer(schema, reference)
                if type(target) is not bool and not isinstance(target, Mapping):
                    raise _InvalidLocalSchemaReference(
                        "$ref target is not a schema"
                    )
                if isinstance(target, Mapping):
                    pending.append((target, target is schema))
            pending.extend((item, False) for item in value.values())
        elif type(value) is list:
            identity = id(value)
            if identity in visited:
                continue
            visited.add(identity)
            pending.extend((item, False) for item in value)


def _read_authenticated_normative_schema(name: str) -> bytes:
    try:
        resource = importlib.resources.files("equipment_quality").joinpath(
            "contracts", "v1", name
        )
        schema_bytes = bytes(resource.read_bytes())
    except OSError as error:
        raise RuntimeError(
            f"normative schema resource unavailable: {name}"
        ) from error
    if hashlib.sha256(schema_bytes).hexdigest() != _NORMATIVE_SCHEMA_SHA256[name]:
        raise RuntimeError(f"normative schema resource digest mismatch: {name}")
    return schema_bytes


def _parse_normative_schema(name: str, schema_bytes: bytes) -> dict[str, object]:
    try:
        schema = json.loads(
            schema_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_schema_members,
            parse_constant=_reject_schema_json_constant,
        )
        if type(schema) is not dict:
            raise _MalformedSchemaResource("top-level schema must be an object")
        _reject_non_finite_schema_numbers(schema)
    except (UnicodeError, ValueError, TypeError, OverflowError, RecursionError) as error:
        raise RuntimeError(
            f"normative schema resource is malformed: {name}"
        ) from error
    return schema


def _compile_normative_validator(
    name: str, schema_bytes: bytes
) -> Draft202012Validator:
    schema = _parse_normative_schema(name, schema_bytes)
    try:
        if schema.get("$schema") != _DRAFT_2020_12:
            raise SchemaError("schema must declare Draft 2020-12")
        _assert_local_schema_references(schema)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(
            _freeze_schema(schema),
            format_checker=FormatChecker(),
        )
    except _InvalidLocalSchemaReference as error:
        raise RuntimeError(
            f"normative schema has invalid local reference: {name}"
        ) from error
    except SchemaError as error:
        raise RuntimeError(f"normative schema is invalid: {name}") from error
    return validator


def _normative_schema_bytes(name: str) -> bytes:
    _require_normative_schema_name(name)
    with _NORMATIVE_SCHEMA_CACHE_LOCK:
        cached = _NORMATIVE_SCHEMA_BYTES_CACHE.get(name)
        if cached is None:
            cached = _read_authenticated_normative_schema(name)
            _NORMATIVE_SCHEMA_BYTES_CACHE[name] = cached
        return cached


def _normative_validator(name: str) -> Draft202012Validator:
    _require_normative_schema_name(name)
    with _NORMATIVE_SCHEMA_CACHE_LOCK:
        cached = _NORMATIVE_VALIDATOR_CACHE.get(name)
        if cached is None:
            cached = _compile_normative_validator(
                name, _normative_schema_bytes(name)
            )
            _NORMATIVE_VALIDATOR_CACHE[name] = cached
        return cached


def normative_schema_bytes(name: str) -> bytes:
    """Return digest-authenticated immutable bytes for one normative schema."""
    return _normative_schema_bytes(name)


def _summary_validation_error(message: str) -> None:
    raise ValidationError(f"analysis summary application contract: {message}")


def _utf8_sorted(values: list[str]) -> bool:
    return values == sorted(values, key=lambda value: value.encode("utf-8"))


def _validate_analysis_summary_application_contract(
    summary: Mapping[str, object],
) -> None:
    split_counts = summary["splitCounts"]
    assert isinstance(split_counts, Mapping)
    for split, value in split_counts.items():
        assert isinstance(value, Mapping)
        if value["total"] != (
            value["defects"]
            + value["nonDefects"]
            + value["unknownOrCensored"]
        ):
            _summary_validation_error(f"{split} count balance is invalid")

    purge_counts = summary["chargePurgeCounts"]
    assert isinstance(purge_counts, Mapping)
    for boundary in ("outer", "inner"):
        value = purge_counts[boundary]
        assert isinstance(value, Mapping)
        if value["rowCount"] < value["chargeCount"]:
            _summary_validation_error(
                f"{boundary} purge row count is smaller than charge count"
            )

    holdout_profiles = summary["holdoutMetrics"]
    assert isinstance(holdout_profiles, list)
    if [item["alertGrade"] for item in holdout_profiles] != [
        "DANGER",
        "CAUTION_OR_DANGER",
    ]:
        _summary_validation_error("holdout profile order is invalid")
    for profile in holdout_profiles:
        assert isinstance(profile, Mapping)
        if profile["total"] != sum(
            profile[name]
            for name in (
                "truePositive",
                "falsePositive",
                "trueNegative",
                "falseNegative",
            )
        ):
            _summary_validation_error("holdout confusion count balance is invalid")
        total = profile["total"]
        true_positive = profile["truePositive"]
        false_positive = profile["falsePositive"]
        false_negative = profile["falseNegative"]
        alert_count = true_positive + false_positive
        defect_count = true_positive + false_negative
        precision = (
            None if alert_count == 0 else true_positive / alert_count
        )
        base_defect_rate = None if total == 0 else defect_count / total
        expected_points = {
            "alertRate": None if total == 0 else alert_count / total,
            "precision": precision,
            "recall": (
                None if defect_count == 0 else true_positive / defect_count
            ),
            "baseDefectRate": base_defect_rate,
            "lift": (
                None
                if precision is None or base_defect_rate in {None, 0}
                else precision / base_defect_rate
            ),
            "falseAlertsPer100": (
                None if total == 0 else 100 * false_positive / total
            ),
        }
        for name in _HOLDOUT_METRICS:
            metric = profile[name]
            assert isinstance(metric, Mapping)
            reason = metric["reasonCode"]
            point = metric["pointEstimate"]
            lower = metric["lower"]
            upper = metric["upper"]
            valid = metric["validReplicates"]
            expected_point = expected_points[name]
            if expected_point is None:
                if (
                    reason != "ZERO_DENOMINATOR"
                    or (point, lower, upper, valid) != (None, None, None, 0)
                ):
                    _summary_validation_error(
                        f"holdout {name} zero-denominator state is inconsistent"
                    )
                continue
            if point != expected_point or reason == "ZERO_DENOMINATOR":
                _summary_validation_error(
                    f"holdout {name} point estimate is inconsistent with counts"
                )
            if reason == "NONE":
                if (
                    lower is None
                    or upper is None
                    or valid < 1900
                    or not lower <= point <= upper
                ):
                    _summary_validation_error(
                        f"holdout {name} valid interval is inconsistent"
                    )
            elif (
                lower is not None
                or upper is not None
                or valid >= 1900
            ):
                _summary_validation_error(
                    f"holdout {name} bootstrap state is inconsistent"
                )

    lineage = summary["lineage"]
    assert isinstance(lineage, Mapping)
    materials = lineage["materials"]
    assert isinstance(materials, list)
    material_keys = [item["materialKey"] for item in materials]
    if not _utf8_sorted(material_keys) or len(material_keys) != len(set(material_keys)):
        _summary_validation_error("material keys must be unique and UTF-8 sorted")
    known_materials = set(material_keys)
    source_order = {role: index for index, (role, _name, _columns) in enumerate(_SOURCE_SPECS)}
    for material in materials:
        records = material["sourceRecords"]
        record_keys = [
            (source_order[record["role"]], record["recordNumber"])
            for record in records
        ]
        if record_keys != sorted(record_keys) or len(record_keys) != len(set(record_keys)):
            _summary_validation_error(
                "material source records must be unique and role/record sorted"
            )

    populations = lineage["populations"]
    assert isinstance(populations, list)
    expected_populations = ["REFERENCE", "DISCOVERY", "CONFIRMATION", "HOLDOUT"]
    if [item["populationRef"] for item in populations] != expected_populations or [
        item["split"] for item in populations
    ] != expected_populations:
        _summary_validation_error("population order or split binding is invalid")
    population_keys: dict[str, set[str]] = {}
    for population in populations:
        keys = population["materialKeys"]
        if not _utf8_sorted(keys) or not set(keys) <= known_materials:
            _summary_validation_error(
                "population material keys must be sorted references"
            )
        population_keys[population["populationRef"]] = set(keys)

    aggregates = lineage["aggregates"]
    assert isinstance(aggregates, list)
    aggregate_sort_keys = [
        (
            _LINEAGE_ARTIFACT_ORDER[item["artifactRole"]],
            item["ruleId"].encode("utf-8"),
            item["split"].encode("utf-8"),
        )
        for item in aggregates
    ]
    if aggregate_sort_keys != sorted(aggregate_sort_keys) or len(
        aggregate_sort_keys
    ) != len(set(aggregate_sort_keys)):
        _summary_validation_error("aggregates must be unique and sorted")
    for aggregate in aggregates:
        keys = aggregate["inputMaterialKeys"]
        if not _utf8_sorted(keys) or not set(keys) <= population_keys[
            aggregate["populationRef"]
        ]:
            _summary_validation_error(
                "aggregate material keys must be sorted population references"
            )
        if aggregate["artifactRole"] == "equipment_operating_ranges":
            expected_binding = ("REFERENCE", "REFERENCE", "NOT_APPLICABLE")
        else:
            expected_binding = (
                aggregate["split"],
                aggregate["split"],
                "FIXED_POPULATION_STRATA_MINUS_CANDIDATE",
            )
        actual_binding = (
            aggregate["split"],
            aggregate["populationRef"],
            aggregate["comparatorDefinition"],
        )
        if actual_binding != expected_binding:
            _summary_validation_error("aggregate population binding is invalid")
        if not set(aggregate["filters"]) <= _LINEAGE_FILTERS:
            _summary_validation_error("aggregate filter vocabulary is invalid")
        if not set(aggregate["transformations"]) <= _LINEAGE_TRANSFORMATIONS:
            _summary_validation_error("aggregate transformation vocabulary is invalid")

    fields = lineage["fields"]
    assert isinstance(fields, list)
    node_ids = [f'{field["artifactRole"]}.{field["outputField"]}' for field in fields]
    field_sort_keys = [
        (
            _LINEAGE_ARTIFACT_ORDER[field["artifactRole"]],
            field["outputField"].encode("utf-8"),
        )
        for field in fields
    ]
    if field_sort_keys != sorted(field_sort_keys) or len(node_ids) != len(set(node_ids)):
        _summary_validation_error("lineage fields must be unique and sorted")
    known_nodes = set(node_ids)
    dependency_graph: dict[str, list[str]] = {}
    source_columns = {
        role: set(columns) for role, _name, columns in _SOURCE_SPECS
    }
    for field, node_id in zip(fields, node_ids, strict=True):
        if field["conversion"] not in _LINEAGE_CONVERSIONS:
            _summary_validation_error("lineage conversion vocabulary is invalid")
        dependencies = field["dependencies"]
        if not _utf8_sorted(dependencies):
            _summary_validation_error(
                "lineage dependencies must be unique and UTF-8 sorted"
            )
        source_role = field["sourceRole"]
        if source_role is not None:
            source_column = field["sourceColumn"]
            if (
                field["artifactRole"] != "replay_events"
                or source_column not in source_columns[source_role]
                or field["firstAvailableStage"] is None
                or dependencies
            ):
                _summary_validation_error("raw lineage mapping is invalid")
            identifier_fields = {
                "charge_id",
                "slab_no",
                "hr_coil_id",
                "ap_prod_id",
            }
            expected_output = (
                source_column
                if source_column in identifier_fields
                else f"values_json.{source_column}"
            )
            if field["outputField"] != expected_output:
                _summary_validation_error("raw replay output path is invalid")
        elif (
            field["sourceColumn"] is not None
            or not dependencies
            or (
                field["artifactRole"] != "replay_events"
                and field["firstAvailableStage"] is not None
            )
        ):
            _summary_validation_error("derived lineage mapping is invalid")

        graph_dependencies: list[str] = []
        for dependency in dependencies:
            if dependency in known_nodes:
                dependency_role = dependency.split(".", 1)[0]
                if dependency_role not in _LINEAGE_NODE_DEPENDENCY_ROLES[
                    field["artifactRole"]
                ]:
                    _summary_validation_error(
                        "lineage output-node dependency is invalid for "
                        f"{field['artifactRole']}"
                    )
                graph_dependencies.append(dependency)
                continue
            if (
                dependency in _LINEAGE_SOURCE_TERMINALS
                and field["artifactRole"] in _LINEAGE_SOURCE_TERMINAL_ROLES
            ) or any(
                dependency.startswith(prefix)
                for prefix in _LINEAGE_ROOT_PREFIXES[field["artifactRole"]]
            ):
                continue
            _summary_validation_error(
                f"lineage dependency terminal is invalid for {field['artifactRole']}"
            )
        dependency_graph[node_id] = graph_dependencies

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            _summary_validation_error("lineage dependency cycle")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in dependency_graph[node_id]:
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in dependency_graph:
        visit(node_id)


def validate_normative_instance(name: str, instance: object) -> None:
    """Validate an instance with the cached checked Draft 2020-12 schema."""
    _require_normative_schema_name(name)
    try:
        _normative_validator(name).validate(instance)
        if name == "analysis_summary.schema.json":
            assert isinstance(instance, Mapping)
            _validate_analysis_summary_application_contract(instance)
    except Unresolvable as error:
        raise RuntimeError(
            f"normative schema reference resolution failed: {name}"
        ) from error


def _parse_date(raw: str, *, role: str, column: str, record_number: int) -> date:
    value = raw.strip()
    if not _DATE_PATTERN.fullmatch(value):
        raise ValueError(
            f"{role} record {record_number} column {column} must be YYYY-MM-DD"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(
            f"{role} record {record_number} column {column} must be YYYY-MM-DD"
        ) from error


def _parse_number(
    raw: str, *, role: str, column: str, record_number: int
) -> float | None:
    value = raw.strip()
    if value == "":
        return None
    try:
        number = float(value)
    except ValueError as error:
        raise ValueError(
            f"{role} record {record_number} column {column} must be finite numeric"
        ) from error
    if not math.isfinite(number):
        raise ValueError(
            f"{role} record {record_number} column {column} must be finite numeric"
        )
    return number


def _convert_record(
    role: str,
    header: tuple[str, ...],
    values: list[str],
    record_number: int,
) -> dict[str, object]:
    converted: dict[str, object] = {}
    for column, raw in zip(header, values, strict=True):
        if column in _IDENTIFIER_COLUMNS[role]:
            if raw == "":
                converted[column] = None
                continue
            identifier = raw.strip()
            if identifier == "":
                raise ValueError(
                    f"{role} record {record_number} column {column} must be a non-empty identifier"
                )
            converted[column] = identifier
        elif column in _DATE_COLUMNS[role]:
            converted[column] = _parse_date(
                raw, role=role, column=column, record_number=record_number
            )
        elif column in _NUMERIC_COLUMNS[role]:
            converted[column] = _parse_number(
                raw, role=role, column=column, record_number=record_number
            )
        elif role == "fur_hr" and column == "f_ext_time":
            number = _parse_number(
                raw, role=role, column=column, record_number=record_number
            )
            if number is None or not number.is_integer() or not 0 <= number <= 23:
                raise ValueError(
                    f"{role} record {record_number} column {column} must be an hour from 0 to 23"
                )
            converted[column] = int(number)
        elif role == "ap" and column == "judge":
            if raw == "":
                converted[column] = None
            elif raw not in {"양품", "불량"}:
                raise ValueError(
                    f"ap record {record_number} judge must be one of 양품 or 불량"
                )
            else:
                converted[column] = raw
        else:
            converted[column] = raw if raw != "" else None
    converted["_source_record_number"] = record_number
    return converted


def _read_source(
    data_dir: Path,
    role: str,
    name: str,
    header: tuple[str, ...],
) -> tuple[pd.DataFrame, SourceFile]:
    path = data_dir / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"source must be a non-symlink regular file: {name}")
    payload = path.read_bytes()
    try:
        text = payload.decode("cp949", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"source {name} is not valid CP949") from error
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError(f"source {name} is not valid CSV") from error
    if not records or tuple(records[0]) != header:
        actual = tuple(records[0]) if records else ()
        raise ValueError(
            f"source {name} must have the exact ordered header; got {actual!r}"
        )
    converted_rows: list[dict[str, object]] = []
    for record_number, values in enumerate(records[1:], start=2):
        if len(values) != len(header):
            raise ValueError(
                f"source {name} record {record_number} has {len(values)} fields; expected {len(header)}"
            )
        converted_rows.append(_convert_record(role, header, values, record_number))
    frame = pd.DataFrame(converted_rows, columns=[*header, "_source_record_number"])
    source = SourceFile(
        role=role,
        name=name,
        size_bytes=len(payload),
        sha256=sha256_uri(payload),
    )
    return frame, source


def read_inputs(data_dir: Path) -> InputTables:
    """Read the exact three CP949 inputs into strictly typed tables."""
    data_dir = Path(data_dir)
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise ValueError(f"data directory must be a non-symlink directory: {data_dir}")
    expected = {name for _, name, _ in _SOURCE_SPECS}
    actual = {
        entry.name
        for entry in data_dir.iterdir()
        if entry.is_file() and entry.suffix.lower() == ".csv"
    }
    if actual != expected:
        raise ValueError(
            f"data directory CSV filenames must be exactly {sorted(expected)}; got {sorted(actual)}"
        )
    frames: dict[str, pd.DataFrame] = {}
    sources: list[SourceFile] = []
    for role, name, header in _SOURCE_SPECS:
        frame, source = _read_source(data_dir, role, name, header)
        frames[role] = frame
        sources.append(source)
    return InputTables(
        sm_cc=frames["sm_cc"],
        fur_hr=frames["fur_hr"],
        ap=frames["ap"],
        sources=tuple(sources),
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(
        f"analysis config JSON contains non-standard numeric token: {value}"
    )


def _reject_duplicate_object_members(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(
                "analysis config JSON contains duplicate object member name: "
                f"{name}"
            )
        result[name] = value
    return result


def _load_strict_json(text: str) -> object:
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_object_members,
        parse_constant=_reject_json_constant,
    )


def _hierarchies(
    items: list[dict[str, object]], *, label: str
) -> dict[str, tuple[tuple[str, ...], ...]]:
    equipment_types = [str(item["equipmentType"]) for item in items]
    if any(equipment_types.count(value) != 1 for value in _EQUIPMENT_TYPES) or any(
        value not in _EQUIPMENT_TYPES for value in equipment_types
    ):
        raise ValueError(
            f"{label} must contain exactly one policy for each equipment type"
        )
    return {
        str(item["equipmentType"]): tuple(
            tuple(str(field) for field in level) for level in item["levels"]
        )
        for item in items
    }


def load_analysis_config(path: Path) -> AnalysisConfig:
    """Load the complete normative config without unknowns, defaults, or mutation."""
    path = Path(path)
    try:
        payload = _load_strict_json(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise ValueError("analysis config must be UTF-8") from error
    validate_normative_instance("analysis_config.schema.json", payload)

    field_names = [field["field"] for field in payload["fields"]]
    if len(set(field_names)) != len(field_names):
        raise ValueError("analysis config field names must be unique")
    for field in payload["fields"]:
        source_role = field["sourceRole"]
        source_column = field["sourceColumn"]
        dependencies = field["dependencies"]
        if (source_role is None) != (source_column is None):
            raise ValueError("field source role and column must both be present or both null")
        if source_role is None and not dependencies:
            raise ValueError("derived fields must declare dependencies")
        if source_role is not None and dependencies:
            raise ValueError("raw source fields must not declare derived dependencies")

    return AnalysisConfig(
        schema_version=payload["schemaVersion"],
        analysis_config_version=payload["analysisConfigVersion"],
        timezone=payload["timezone"],
        label_maturity_days=payload["labelMaturityDays"],
        reference_fraction=payload["splits"]["referenceFraction"],
        discovery_fraction=payload["splits"]["discoveryFraction"],
        operating_ranges=payload["operatingRanges"],
        quality_risk=payload["qualityRisk"],
        bootstrap=payload["bootstrap"],
        wilson_z=payload["wilsonZ"],
        fields=tuple(payload["fields"]),
        range_context_hierarchies=_hierarchies(
            payload["rangeContextHierarchies"], label="rangeContextHierarchies"
        ),
        risk_adjustment_hierarchies=_hierarchies(
            payload["riskAdjustmentHierarchies"],
            label="riskAdjustmentHierarchies",
        ),
        fixed_interactions=tuple(
            tuple(interaction) for interaction in payload["fixedInteractions"]
        ),
        fdr_families=tuple(payload["fdrFamilies"]),
        evidence_families=tuple(payload["evidenceFamilies"]),
    )
