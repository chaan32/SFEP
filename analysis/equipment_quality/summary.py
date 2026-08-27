"""Build the deterministic analysis summary and retrospective holdout report."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
import hashlib
import json
import math
from numbers import Integral, Real
import struct

import pandas as pd

from equipment_quality.deterministic import canonical_json_bytes, type1_quantile
from equipment_quality.event_builder import material_key
from equipment_quality.feature_roles import FeatureDefinition, STAGE_RANK
from equipment_quality.models import (
    AggregateLineage,
    AnalysisConfig,
    MaterialLineage,
    PopulationLineage,
    QualityRulesResult,
    SummaryBuildRequest,
)


_QUALITY_ROLES = {
    "DIRECT_OPERATION",
    "PRODUCT_STATE_REFERENCE",
    "CONTEXT",
    "EQUIPMENT_IDENTIFIER",
}
_POPULATION_ORDER = ("REFERENCE", "DISCOVERY", "CONFIRMATION", "HOLDOUT")
_STAGE_DATE = {
    "CAST_RECORDED": "cast_date",
    "FURNACE_CHARGED": "f_ext_date",
    "PREHEAT_COMPLETE": "f_ext_date",
    "HEAT_COMPLETE": "f_ext_date",
    "SOAK_COMPLETE": "f_ext_date",
    "FURNACE_EXTRACTED": "f_ext_date",
    "RM4_RECORDED": "f_ext_date",
    "AP_RECORDED_WITH_RESULT": "ap_date",
}
_IDENTIFIER_COLUMNS = {"charge_id", "slab_no", "hr_coil_id", "ap_prod_id"}
_SOURCE_ORDER = ("sm_cc", "fur_hr", "ap")
_INSUFFICIENT_QUALITY_REASONS = {
    "LOW_SUPPORT",
    "LOW_DEFECT_COUNT",
    "NO_INFORMATIVE_STRATA",
    "NO_VARIATION",
    "NON_FINITE_ESTIMATE",
    "ZERO_COMPARATOR_RISK",
}
_QUARANTINE_DEPENDENCIES = {
    "MISSING_SM_CC_KEY": ("sm_cc.charge_id", "sm_cc.slab_no"),
    "MISSING_FUR_HR_KEY": ("fur_hr.charge_id", "fur_hr.slab_no"),
    "MISSING_AP_HR_COIL_ID": ("ap.hr_coil_id",),
    "MISSING_FUR_HR_COIL_ID": ("fur_hr.hr_coil_id",),
    "MISSING_AP_PROD_ID": ("ap.ap_prod_id",),
    "DUPLICATE_SM_CC_KEY": ("sm_cc.charge_id", "sm_cc.slab_no"),
    "DUPLICATE_FUR_HR_KEY": ("fur_hr.charge_id", "fur_hr.slab_no"),
    "DUPLICATE_AP_KEY": ("ap.hr_coil_id",),
    "DUPLICATE_FUR_HR_COIL_KEY": ("fur_hr.hr_coil_id",),
    "DUPLICATE_AP_PROD_ID": ("ap.ap_prod_id",),
    "INVALID_FUR_HR_DATE": ("fur_hr.hr_date",),
    "IMPOSSIBLE_STAGE_DATE_ORDER": (
        "ap.ap_date",
        "fur_hr.f_ext_date",
        "fur_hr.hr_date",
        "sm_cc.cast_date",
    ),
    "UNLINKED_SM_CC": (
        "fur_hr.charge_id",
        "fur_hr.slab_no",
        "sm_cc.charge_id",
        "sm_cc.slab_no",
    ),
    "UNLINKED_FUR_HR": (
        "fur_hr.charge_id",
        "fur_hr.slab_no",
        "sm_cc.charge_id",
        "sm_cc.slab_no",
    ),
    "UNLINKED_AP": ("ap.hr_coil_id", "fur_hr.hr_coil_id"),
}


def _missing(value: object) -> bool:
    if value is None:
        return True
    marker = pd.isna(value)
    return bool(marker) if isinstance(marker, bool) else False


def _wire_scalar(value: object) -> object:
    if _missing(value):
        return None
    if type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, Integral) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, Real) and not isinstance(value, bool):
        number = float(value)
        return number if math.isfinite(number) else None
    raise TypeError(f"summary scalar has unsupported type: {type(value).__name__}")


def _date_value(value: object, label: str) -> date | None:
    if _missing(value):
        return None
    if type(value) is date:
        return value
    if isinstance(value, datetime):
        return value.date()
    raise TypeError(f"{label} must be an exact date")


def _finite_number(value: object) -> float | None:
    if _missing(value) or isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _level_counts(values: Sequence[object]) -> list[dict[str, object]]:
    counts: dict[object, int] = {}
    for value in values:
        scalar = _wire_scalar(value)
        if scalar is None:
            continue
        counts[scalar] = counts.get(scalar, 0) + 1
    return [
        {"count": counts[value], "value": value}
        for value in sorted(counts, key=lambda item: canonical_json_bytes(item))
    ]


def _source_data_types(request: SummaryBuildRequest) -> dict[tuple[str, str], str]:
    frames = {
        "sm_cc": request.inputs.sm_cc,
        "fur_hr": request.inputs.fur_hr,
        "ap": request.inputs.ap,
    }
    result: dict[tuple[str, str], str] = {}
    for role, frame in frames.items():
        for column in frame.columns:
            if column == "_source_record_number":
                continue
            result[(role, str(column))] = (
                "IDENTIFIER" if column in _IDENTIFIER_COLUMNS else "CATEGORY"
            )
    for configured in request.analysis_config.fields:
        role = configured["sourceRole"]
        column = configured["sourceColumn"]
        if role is None or column is None or column in _IDENTIFIER_COLUMNS:
            continue
        data_type = configured["dataType"]
        result[(str(role), str(column))] = (
            "NUMBER"
            if data_type in {"NUMBER", "HOUR"}
            else "DATE"
            if data_type == "DATE"
            else "CATEGORY"
        )
    for column in ("f_bfg_per", "f_cog_per", "f_ldg_per"):
        result[("fur_hr", column)] = "NUMBER"
    return result


def _source_profiles(request: SummaryBuildRequest) -> list[dict[str, object]]:
    frames = {
        "sm_cc": request.inputs.sm_cc,
        "fur_hr": request.inputs.fur_hr,
        "ap": request.inputs.ap,
    }
    data_types = _source_data_types(request)
    profiles: list[dict[str, object]] = []
    for role in _SOURCE_ORDER:
        frame = frames[role]
        for raw_column in frame.columns:
            column = str(raw_column)
            if column == "_source_record_number":
                continue
            data_type = data_types[(role, column)]
            raw_values = frame[raw_column].tolist()
            present = [value for value in raw_values if not _missing(value)]
            numeric = None
            levels = None
            if data_type == "NUMBER":
                values = [value for value in (_finite_number(item) for item in present) if value is not None]
                numeric = {
                    "median": type1_quantile(values, 0.5),
                    "p05": type1_quantile(values, 0.05),
                    "p95": type1_quantile(values, 0.95),
                }
            else:
                levels = _level_counts(present)
            unique_values = {_wire_scalar(value) for value in present}
            profiles.append(
                {
                    "column": column,
                    "dataType": data_type,
                    "levels": levels,
                    "missing": len(raw_values) - len(present),
                    "numeric": numeric,
                    "sourceRole": role,
                    "total": len(raw_values),
                    "unique": len(unique_values),
                }
            )
    return profiles


def _distribution(
    rows: pd.DataFrame,
    definition: FeatureDefinition,
    as_of: date,
    *,
    reference: bool,
) -> dict[str, object]:
    raw: list[object | None] = []
    for row in rows.to_dict(orient="records"):
        arrived = _date_value(row.get(definition.event_date_column), definition.event_date_column)
        raw.append(
            None
            if reference and (arrived is None or arrived > as_of)
            else row.get(definition.name)
        )
    present = [value for value in raw if not _missing(value)]
    result: dict[str, object] = {
        "levels": None,
        "median": None,
        "missingRate": (len(raw) - len(present)) / len(raw) if raw else 0.0,
        "p05": None,
        "p95": None,
        "support": len(present),
    }
    if definition.data_type == "NUMBER":
        values = [value for value in (_finite_number(item) for item in present) if value is not None]
        if values:
            result.update(
                {
                    "median": type1_quantile(values, 0.5),
                    "p05": type1_quantile(values, 0.05),
                    "p95": type1_quantile(values, 0.95),
                }
            )
    else:
        result["levels"] = _level_counts(present)
    return result


def _drift_metrics(request: SummaryBuildRequest) -> list[dict[str, object]]:
    definitions = sorted(
        (
            item
            for item in request.definitions
            if item.field_role in _QUALITY_ROLES
        ),
        key=lambda item: (STAGE_RANK[item.first_stage], item.name.encode("utf-8")),
    )
    return [
        {
            "dataType": "NUMBER" if item.data_type == "NUMBER" else "CATEGORY",
            "field": item.name,
            "holdout": _distribution(
                request.split.holdout_rows,
                item,
                request.split.as_of,
                reference=False,
            ),
            "reference": _distribution(
                request.split.reference_rows,
                item,
                request.split.as_of,
                reference=True,
            ),
        }
        for item in definitions
    ]


def _predicate_term_matches(term: Mapping[str, object], value: object) -> bool:
    if _missing(value):
        return False
    term_type = term.get("type")
    if term_type == "CATEGORY_IN":
        values = term.get("values")
        return isinstance(values, (list, tuple)) and _wire_scalar(value) in tuple(values)
    if term_type != "NUMERIC_INTERVAL":
        raise ValueError("holdout rule predicate type is invalid")
    number = _finite_number(value)
    if number is None:
        return False
    lower = term.get("lower")
    upper = term.get("upper")
    if lower is not None:
        lower_number = _finite_number(lower)
        if lower_number is None:
            raise ValueError("holdout predicate lower must be finite or null")
        if number < lower_number or (
            number == lower_number and term.get("lowerInclusive") is not True
        ):
            return False
    if upper is not None:
        upper_number = _finite_number(upper)
        if upper_number is None:
            raise ValueError("holdout predicate upper must be finite or null")
        if number > upper_number or (
            number == upper_number and term.get("upperInclusive") is not True
        ):
            return False
    return True


def _rule_matches(row: Mapping[str, object], rule: Mapping[str, object], as_of: date) -> bool:
    stage = rule.get("firstAvailableStage")
    if type(stage) is not str or stage not in _STAGE_DATE:
        raise ValueError("holdout rule stage is invalid")
    if stage == "AP_RECORDED_WITH_RESULT" or rule.get("earlyWarningEligible") is not True:
        return False
    arrived = _date_value(row.get(_STAGE_DATE[stage]), _STAGE_DATE[stage])
    if arrived is None or arrived <= as_of:
        return False
    context = rule.get("applicationContext")
    if not isinstance(context, Mapping):
        raise ValueError("holdout applicationContext must be a mapping")
    for field, expected in context.items():
        if type(field) is not str or _wire_scalar(row.get(field)) != expected:
            return False
    predicate = rule.get("predicate")
    if not isinstance(predicate, Mapping):
        raise ValueError("holdout predicate must be a mapping")
    terms = predicate.get("allOf")
    if not isinstance(terms, (list, tuple)) or not terms:
        raise ValueError("holdout predicate allOf must be nonempty")
    for term in terms:
        if not isinstance(term, Mapping) or type(term.get("field")) is not str:
            raise ValueError("holdout predicate terms must be mappings")
        if not _predicate_term_matches(term, row.get(term["field"])):
            return False
    return True


def _rules_snapshot(rules: object) -> tuple[Mapping[str, object], ...]:
    if isinstance(rules, QualityRulesResult):
        raw = rules.to_wire()
    else:
        if type(rules) is str:
            raise TypeError("rules must be a sequence")
        try:
            raw = tuple(rules)  # type: ignore[arg-type]
        except TypeError as error:
            raise TypeError("rules must be a sequence") from error
    snapshot: list[Mapping[str, object]] = []
    for rule in raw:
        if hasattr(rule, "to_wire"):
            rule = rule.to_wire()
        if not isinstance(rule, Mapping):
            raise TypeError("rules must contain mappings or QualityRule values")
        frozen = json.loads(canonical_json_bytes(rule))
        if not isinstance(frozen, Mapping):
            raise TypeError("rules must serialize as JSON objects")
        snapshot.append(frozen)
    return tuple(snapshot)


def _holdout_points(counts: Mapping[str, int]) -> dict[str, float | None]:
    tp = counts["truePositive"]
    fp = counts["falsePositive"]
    fn = counts["falseNegative"]
    total = sum(counts.values())
    alerts = tp + fp
    defects = tp + fn
    precision = None if alerts == 0 else tp / alerts
    base = None if total == 0 else defects / total
    return {
        "alertRate": None if total == 0 else alerts / total,
        "precision": precision,
        "recall": None if defects == 0 else tp / defects,
        "baseDefectRate": base,
        "lift": None if precision is None or base in {None, 0.0} else precision / base,
        "falseAlertsPer100": None if total == 0 else 100.0 * fp / total,
    }


def _confusion(rows: Sequence[Mapping[str, object]], alerted: set[str]) -> dict[str, int]:
    counts = {
        "truePositive": 0,
        "falsePositive": 0,
        "trueNegative": 0,
        "falseNegative": 0,
    }
    for row in rows:
        alert = row["__material_key"] in alerted
        defect = row.get("judge") == "불량"
        key = (
            "truePositive"
            if alert and defect
            else "falsePositive"
            if alert
            else "falseNegative"
            if defect
            else "trueNegative"
        )
        counts[key] += 1
    return counts


def _sample_charge_indices(seed: bytes, replicate: int, size: int) -> tuple[int, ...]:
    return tuple(
        int.from_bytes(
            hashlib.sha256(seed + struct.pack(">Q", replicate) + struct.pack(">Q", draw)).digest()[:8],
            "big",
        )
        % size
        for draw in range(size)
    )


def _holdout_metric(
    point: float | None,
    values: list[float],
    minimum_valid: int,
) -> dict[str, object]:
    if point is None:
        return {
            "pointEstimate": None,
            "lower": None,
            "upper": None,
            "validReplicates": 0,
            "reasonCode": "ZERO_DENOMINATOR",
        }
    if len(values) < minimum_valid:
        return {
            "pointEstimate": point,
            "lower": None,
            "upper": None,
            "validReplicates": len(values),
            "reasonCode": "TOO_FEW_VALID_BOOTSTRAPS",
        }
    return {
        "pointEstimate": point,
        "lower": type1_quantile(values, 0.025),
        "upper": type1_quantile(values, 0.975),
        "validReplicates": len(values),
        "reasonCode": "NONE",
    }


def holdout_metrics(
    split,
    rules,
    criteria_id: str,
) -> list[dict[str, object]]:
    """Evaluate frozen rules on retrospective holdout with Charge blocks."""
    from equipment_quality.models import TimeSplitResult

    if type(split) is not TimeSplitResult:
        raise TypeError("split must be an exact TimeSplitResult")
    if (
        type(criteria_id) is not str
        or len(criteria_id) != 71
        or not criteria_id.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in criteria_id[7:])
    ):
        raise ValueError("criteria_id must be a lowercase SHA-256 URI")
    rule_snapshot = _rules_snapshot(rules)
    evaluated: list[dict[str, object]] = []
    for raw_row in split.holdout_rows.to_dict(orient="records"):
        if raw_row.get("label_status") != "AVAILABLE" or raw_row.get("judge") not in {
            "양품",
            "불량",
        }:
            continue
        charge = raw_row.get("charge_id")
        slab = raw_row.get("slab_no")
        coil = raw_row.get("hr_coil_id")
        if any(type(value) is not str or not value for value in (charge, slab, coil)):
            raise ValueError("evaluated holdout identities must be nonempty strings")
        row = dict(raw_row)
        row["__material_key"] = material_key(charge, slab)
        evaluated.append(row)
    charge_ids = sorted(
        {str(row["charge_id"]) for row in evaluated},
        key=lambda value: value.encode("utf-8"),
    )
    rows_by_charge = {
        charge: [row for row in evaluated if row["charge_id"] == charge]
        for charge in charge_ids
    }
    seed = hashlib.sha256(
        (criteria_id + "\0holdout-bootstrap-v1").encode("utf-8")
    ).digest()
    result: list[dict[str, object]] = []
    for profile_name, grades in (
        ("DANGER", {"DANGER"}),
        ("CAUTION_OR_DANGER", {"CAUTION", "DANGER"}),
    ):
        alerted = {
            str(row["__material_key"])
            for row in evaluated
            if any(
                rule.get("grade") in grades
                and _rule_matches(row, rule, split.as_of)
                for rule in rule_snapshot
            )
        }
        counts = _confusion(evaluated, alerted)
        points = _holdout_points(counts)
        bootstrap_values = {name: [] for name in points}
        if charge_ids:
            for replicate in range(2000):
                sampled: list[Mapping[str, object]] = []
                for index in _sample_charge_indices(seed, replicate, len(charge_ids)):
                    sampled.extend(rows_by_charge[charge_ids[index]])
                for name, value in _holdout_points(_confusion(sampled, alerted)).items():
                    if value is not None:
                        bootstrap_values[name].append(value)
        profile: dict[str, object] = {
            "alertGrade": profile_name,
            "total": len(evaluated),
            **counts,
        }
        for name, point in points.items():
            profile[name] = _holdout_metric(point, bootstrap_values[name], 1900)
        result.append(profile)
    return result


def _catalog_index(catalog: Sequence[MaterialLineage]) -> dict[tuple[str, str, str], str]:
    result: dict[tuple[str, str, str], str] = {}
    for material in catalog:
        if material.hr_coil_id is None:
            continue
        key = (material.charge_id, material.slab_no, material.hr_coil_id)
        if key in result:
            raise ValueError("material catalog identities must be unique")
        result[key] = material.material_key
    return result


def _population_keys(
    request: SummaryBuildRequest,
) -> tuple[PopulationLineage, ...]:
    index = _catalog_index(request.material_catalog)
    frames = {
        "REFERENCE": request.split.reference_rows,
        "DISCOVERY": request.split.discovery_rows,
        "CONFIRMATION": request.split.confirmation_rows,
        "HOLDOUT": request.split.holdout_rows,
    }
    populations: list[PopulationLineage] = []
    for name in _POPULATION_ORDER:
        keys: list[str] = []
        for row in frames[name].to_dict(orient="records"):
            identity = (row.get("charge_id"), row.get("slab_no"), row.get("hr_coil_id"))
            if identity not in index:
                raise ValueError(f"{name} row is missing from material catalog")
            keys.append(index[identity])
        populations.append(
            PopulationLineage(
                name,
                name,
                tuple(sorted(keys, key=lambda value: value.encode("utf-8"))),
            )
        )
    return tuple(populations)


def _aggregate_lineage(
    request: SummaryBuildRequest,
    populations: Sequence[PopulationLineage],
) -> list[dict[str, object]]:
    by_population = {item.population_ref: set(item.material_keys) for item in populations}
    result: list[AggregateLineage] = []
    for record, sidecar in zip(
        request.operating_ranges.records,
        request.operating_ranges.sidecars,
        strict=True,
    ):
        if not set(sidecar.contributor_material_keys).issubset(by_population["REFERENCE"]):
            raise ValueError("range contributors must belong to REFERENCE")
        result.append(
            AggregateLineage(
                "equipment_operating_ranges",
                str(record["ruleId"]),
                "REFERENCE",
                "REFERENCE",
                sidecar.contributor_material_keys,
                "NOT_APPLICABLE",
                ("STAGE_AVAILABLE_AT_AS_OF", "FINITE_VALUE"),
                ("TYPE1_QUANTILE",),
            )
        )
    for record, sidecar in zip(
        request.quality_rules.records,
        request.quality_rules.sidecars,
        strict=True,
    ):
        requires_finite = record["analysisFamily"] != "CATEGORICAL"
        for split_name, split_sidecar in (
            ("CONFIRMATION", sidecar.confirmation),
            ("DISCOVERY", sidecar.discovery),
        ):
            population = by_population[split_name]
            candidates = set(split_sidecar.candidate_material_keys)
            if not candidates.issubset(population):
                raise ValueError("quality candidates must belong to their population")
            comparator_count = sum(split_sidecar.comparator_counts_by_stratum.values())
            if comparator_count > len(population - candidates):
                raise ValueError("quality comparator counts exceed the fixed population")
            inputs = (
                split_sidecar.candidate_material_keys
                if 0 < len(candidates) < len(population)
                else ()
            )
            filters = ["LABEL_AVAILABLE_AND_MATURE"]
            if requires_finite:
                filters.append("FINITE_VALUE")
            filters.append("PREDICATE_MATCH")
            if split_name == "CONFIRMATION":
                filters.append("FIXED_DISCOVERY_STRATA")
            filters.append("INFORMATIVE_STRATA_ONLY")
            transformations = ["WILSON_SCORE_INTERVAL"]
            if split_name == "DISCOVERY":
                transformations.append("BENJAMINI_HOCHBERG_FDR")
            result.append(
                AggregateLineage(
                    "quality_risk_intervals",
                    str(record["ruleId"]),
                    split_name,
                    split_name,
                    tuple(inputs),
                    "FIXED_POPULATION_STRATA_MINUS_CANDIDATE",
                    tuple(filters),
                    tuple(transformations),
                )
            )
    result.sort(
        key=lambda item: (
            0 if item.artifact_role == "equipment_operating_ranges" else 1,
            item.rule_id.encode("utf-8"),
            item.split.encode("utf-8"),
        )
    )
    return [item.to_wire() for item in result]


def _material_lineage(catalog: Sequence[MaterialLineage]) -> list[dict[str, object]]:
    return [
        {
            "chargeId": material.charge_id,
            "hrCoilId": material.hr_coil_id,
            "materialKey": material.material_key,
            "slabNo": material.slab_no,
            "sourceRecords": [
                {
                    "name": record.name,
                    "recordNumber": record.record_number,
                    "role": record.role,
                }
                for record in sorted(
                    material.source_records,
                    key=lambda item: (
                        _SOURCE_ORDER.index(item.role),
                        item.record_number,
                    ),
                )
            ],
        }
        for material in sorted(catalog, key=lambda item: item.material_key.encode("utf-8"))
    ]


_ARTIFACT_ROLES = (
    "bundle_manifest",
    "analysis_config",
    "producer_runtime",
    "equipment_operating_ranges",
    "quality_risk_intervals",
    "replay_events",
    "analysis_summary",
)
_PUBLISHED_ARTIFACT_ROLES = _ARTIFACT_ROLES[1:]
_ARTIFACT_SCHEMA_VERSIONS = {
    "analysis_config": "sfep-analysis-config/v1",
    "producer_runtime": "sfep-producer-runtime/v1",
    "equipment_operating_ranges": "sfep-operating-ranges/v1",
    "quality_risk_intervals": "sfep-quality-rules/v1",
    "replay_events": "sfep-replay-events/v1",
    "analysis_summary": "sfep-analysis-summary/v1",
}


def _normalized_leaf_paths(value: object, prefix: str = "") -> set[str]:
    if isinstance(value, Mapping):
        if not value:
            return {prefix} if prefix else set()
        result: set[str] = set()
        for key, item in tuple(value.items()):
            if type(key) is not str or not key:
                raise ValueError("artifact leaf keys must be built-in non-empty strings")
            child = key if not prefix else prefix + "." + key
            result.update(_normalized_leaf_paths(item, child))
        return result
    if isinstance(value, (list, tuple)):
        result: set[str] = set()
        for item in value:
            result.update(_normalized_leaf_paths(item, prefix + "[]"))
        return result
    if not prefix:
        raise ValueError("artifact leaf must have a normalized output path")
    return {prefix}


def _replay_event_payload(event: object) -> dict[str, object]:
    return {
        "schema_version": event.schema_version,
        "bundle_id": event.bundle_id,
        "criteria_id": event.criteria_id,
        "event_id": event.event_id,
        "replay_date": event.replay_date.isoformat(),
        "replay_hour": event.replay_hour,
        "batch_kind": event.batch_kind,
        "batch_id": event.batch_id,
        "equipment_batch_id": event.equipment_batch_id,
        "batch_step": event.batch_step,
        "time_precision": event.time_precision,
        "material_key": event.material_key,
        "equipment_type": event.equipment_type,
        "equipment_id": event.equipment_id,
        "charge_id": event.charge_id,
        "slab_no": event.slab_no,
        "hr_coil_id": event.hr_coil_id,
        "ap_prod_id": event.ap_prod_id,
        "values_json": event.values_json,
    }


def _artifact_lineage_paths(
    request: SummaryBuildRequest,
    summary_payload: Mapping[str, object],
) -> dict[str, tuple[str, ...]]:
    manifest = {
        "artifacts": [
            {
                "role": role,
                "schemaVersion": _ARTIFACT_SCHEMA_VERSIONS[role],
                "sha256": "sha256:" + "0" * 64,
                "sizeBytes": 0,
            }
            for role in _PUBLISHED_ARTIFACT_ROLES
        ],
        "asOf": request.split.as_of.isoformat(),
        "bundleId": request.bundle_identity.value,
        "criteriaId": request.criteria_identity.value,
        "criteriaIdentity": request.criteria_identity.fields,
        "identity": request.bundle_identity.fields,
        "labelMaturityDays": request.analysis_config.label_maturity_days,
        "schemaVersion": "sfep-equipment-bundle/v1",
        "timezone": request.analysis_config.timezone,
    }
    payloads: dict[str, object] = {
        "bundle_manifest": manifest,
        "analysis_config": json.loads(request.analysis_config_bytes),
        "producer_runtime": json.loads(request.producer_runtime_bytes),
        "equipment_operating_ranges": {
            "asOf": request.split.as_of.isoformat(),
            "criteriaId": request.criteria_identity.value,
            "ranges": request.operating_ranges.to_wire(),
            "schemaVersion": "sfep-operating-ranges/v1",
        },
        "quality_risk_intervals": {
            "asOf": request.split.as_of.isoformat(),
            "criteriaId": request.criteria_identity.value,
            "rules": request.quality_rules.to_wire(),
            "schemaVersion": "sfep-quality-rules/v1",
        },
        "replay_events": [_replay_event_payload(event) for event in request.events],
        "analysis_summary": summary_payload,
    }
    paths_by_role: dict[str, set[str]] = {}
    for role in _ARTIFACT_ROLES:
        if role == "replay_events":
            paths: set[str] = set()
            for event in payloads[role]:
                paths.update(_normalized_leaf_paths(event))
        else:
            paths = _normalized_leaf_paths(payloads[role])
        if role == "quality_risk_intervals":
            nullable_values = "rules[].predicate.allOf[].values"
            if nullable_values in paths:
                paths.remove(nullable_values)
                paths.add(nullable_values + "[]")
        if role == "analysis_summary":
            for empty_dynamic_counts in (
                "labelCensoringCounts",
                "quarantineCounts",
            ):
                paths.discard(empty_dynamic_counts)
        paths_by_role[role] = paths
    return {
        role: tuple(sorted(paths_by_role[role], key=lambda value: value.encode("utf-8")))
        for role in _ARTIFACT_ROLES
    }


def _range_lineage(
    request: SummaryBuildRequest,
) -> dict[str, list[str]]:
    definitions = {item.name: item for item in request.definitions}
    configured = {str(item["field"]): item for item in request.analysis_config.fields}

    def source_nodes(field: str) -> set[str]:
        name = field.removesuffix("_band")
        metadata = configured.get(name)
        if metadata is None:
            raise ValueError("range input field is not configured")
        if metadata["sourceRole"] is not None:
            return {str(metadata["sourceRole"]) + "." + str(metadata["sourceColumn"])}
        result: set[str] = set()
        for dependency in metadata["dependencies"]:
            result.update(source_nodes(str(dependency)))
        if not result:
            raise ValueError("derived range input has no source dependencies")
        return result

    def available_source_nodes(field: str) -> set[str]:
        name = field.removesuffix("_band")
        definition = definitions.get(name)
        if definition is None:
            raise ValueError("range input field has no feature definition")
        result = source_nodes(name) | source_nodes(definition.event_date_column)
        if field.endswith("_band"):
            result.add("population.REFERENCE")
        return result

    concrete: list[dict[str, set[str]]] = []
    for record, sidecar in zip(
        request.operating_ranges.records,
        request.operating_ranges.sidecars,
        strict=True,
    ):
        if record["ruleId"] != sidecar.rule_id:
            raise ValueError("range record and sidecar identity drift")
        field = str(record["field"])
        definition = definitions.get(field)
        if definition is None:
            raise ValueError("range record field is not configured")
        field_nodes = available_source_nodes(field)
        equipment_id_nodes: set[str] = set()
        if definition.equipment_id_column is not None:
            equipment_id_nodes.update(
                available_source_nodes(definition.equipment_id_column)
            )
        context = record["context"]
        if not isinstance(context, Mapping):
            raise ValueError("range context must be a mapping")
        context_dependencies: dict[str, set[str]] = {}
        context_nodes: set[str] = set()
        for name in context:
            dependencies = available_source_nodes(str(name))
            context_dependencies[str(name)] = dependencies
            context_nodes.update(dependencies)
        group_dependencies = {
            "config.operatingRanges.minimumSupport",
            "config.rangeContextHierarchies[].equipmentType",
            "config.rangeContextHierarchies[].levels[][]",
            "population.REFERENCE",
            *field_nodes,
            *equipment_id_nodes,
            *context_nodes,
        }
        quantile_dependencies = set(group_dependencies)
        trace: dict[str, set[str]] = {
            "ranges[].field": {"config.fields[].field"},
            "ranges[].fieldRole": {
                "config.fields[].featureRole",
            },
            "ranges[].firstAvailableStage": {
                "config.fields[].field",
                "config.fields[].firstAvailableStage",
            },
            "ranges[].equipmentType": {
                "config.fields[].equipmentType",
                "config.fields[].field",
            },
            "ranges[].equipmentId": {
                "config.fields[].equipmentType",
                "config.fields[].featureRole",
                "config.fields[].field",
                *equipment_id_nodes,
            },
            "ranges[].contextLevel": group_dependencies,
            "ranges[].support": group_dependencies,
            "ranges[].median": quantile_dependencies,
            "ranges[].p05": {
                "config.operatingRanges.typicalLowerQuantile",
                *quantile_dependencies,
            },
            "ranges[].p95": {
                "config.operatingRanges.typicalUpperQuantile",
                *quantile_dependencies,
            },
            "ranges[].p01": {
                "config.operatingRanges.extremeLowerQuantile",
                "config.operatingRanges.extremeTailMinimumSupport",
                *quantile_dependencies,
            },
            "ranges[].p99": {
                "config.operatingRanges.extremeUpperQuantile",
                "config.operatingRanges.extremeTailMinimumSupport",
                *quantile_dependencies,
            },
            "ranges[].lowerTailEnabled": {
                "config.operatingRanges.extremeLowerQuantile",
                "config.operatingRanges.extremeTailMinimumSupport",
                "config.operatingRanges.typicalLowerQuantile",
                *quantile_dependencies,
            },
            "ranges[].upperTailEnabled": {
                "config.operatingRanges.extremeUpperQuantile",
                "config.operatingRanges.extremeTailMinimumSupport",
                "config.operatingRanges.typicalUpperQuantile",
                *quantile_dependencies,
            },
        }
        for name in context:
            path = "ranges[].context." + str(name)
            trace[path] = {
                "config.operatingRanges.minimumSupport",
                "config.rangeContextHierarchies[].equipmentType",
                "config.rangeContextHierarchies[].levels[][]",
                *context_dependencies[str(name)],
            }
        trace["ranges[].ruleId"] = {
            "config.fields[].equipmentType",
            "config.fields[].featureRole",
            "config.fields[].field",
            "config.fields[].firstAvailableStage",
            "config.operatingRanges.extremeLowerQuantile",
            "config.operatingRanges.extremeTailMinimumSupport",
            "config.operatingRanges.extremeUpperQuantile",
            "config.operatingRanges.minimumSupport",
            "config.operatingRanges.typicalLowerQuantile",
            "config.operatingRanges.typicalUpperQuantile",
            "config.rangeContextHierarchies[].equipmentType",
            "config.rangeContextHierarchies[].levels[][]",
            *field_nodes,
            *equipment_id_nodes,
            *context_nodes,
        }
        concrete.append(trace)
    normalized: dict[str, set[str]] = {}
    for trace in concrete:
        for path, dependencies in trace.items():
            normalized.setdefault(path, set()).update(dependencies)
    return {
        path: sorted(dependencies, key=lambda value: value.encode("utf-8"))
        for path, dependencies in normalized.items()
    }


def _quality_feature_node(field: str) -> str:
    return "replay_events.values_json." + field


def _concrete_quality_lineage(
    rules: QualityRulesResult,
    config: AnalysisConfig,
) -> dict[str, dict[str, list[str]]]:
    if type(rules) is not QualityRulesResult:
        raise TypeError("quality lineage requires a rich QualityRulesResult")
    definitions = {str(item["field"]): item for item in config.fields}
    predicate_paths = {
        "quality_risk_intervals.rules[].predicate.allOf[]." + suffix
        for suffix in (
            "field",
            "type",
            "lower",
            "lowerInclusive",
            "upper",
            "upperInclusive",
            "values[]",
        )
    }
    rule_identity_paths = {
        "quality_risk_intervals.rules[]." + suffix
        for suffix in (
            "adjustmentFieldsDropped[]",
            "adjustmentKind",
            "adjustmentLevel",
            "analysisFamily",
            "applicationContext",
            "applicationScope",
            "equipmentId",
            "equipmentType",
            "fieldNames[]",
            "firstAvailableStage",
            "predicate.allOf[].field",
            "predicate.allOf[].lower",
            "predicate.allOf[].lowerInclusive",
            "predicate.allOf[].type",
            "predicate.allOf[].upper",
            "predicate.allOf[].upperInclusive",
            "predicate.allOf[].values[]",
        )
    }
    display_dependencies = {
        "quality_risk_intervals.rules[]." + suffix
        for suffix in (
            "analysisFamily",
            "applicationContext",
            "applicationScope",
            "equipmentId",
            "equipmentType",
            "fieldNames[]",
            "firstAvailableStage",
            "adjustmentFieldsDropped[]",
            "adjustmentKind",
            "adjustmentLevel",
            "grade",
            "predicate.allOf[].lower",
            "predicate.allOf[].lowerInclusive",
            "predicate.allOf[].upper",
            "predicate.allOf[].upperInclusive",
            "ruleId",
        )
    }
    result: dict[str, dict[str, list[str]]] = {}
    for rule, sidecar in zip(rules.records, rules.sidecars, strict=True):
        family = str(rule["analysisFamily"])
        field_names = tuple(str(value) for value in rule["fieldNames"])
        axes = {_quality_feature_node(field) for field in field_names}
        trace: dict[str, set[str]] = {}

        def record(path: str, dependencies: set[str]) -> None:
            trace[path] = set(dependencies)

        candidate_roots = {
            "config.fields[].dataType",
            "config.fields[].featureRole",
            "config.fields[].field",
            "population.DISCOVERY",
            *axes,
        }
        if family == "NUMERIC":
            candidate_roots.add("config.qualityRisk.numericBins")
        elif family == "INTERACTION":
            candidate_roots.update(
                {"config.fixedInteractions[][]", "config.qualityRisk.interactionBins"}
            )
        record("rules[].predicate.allOf[].field", candidate_roots)
        record(
            "rules[].predicate.allOf[].type",
            {
                "quality_risk_intervals.rules[].predicate.allOf[].field",
                "config.fields[].dataType",
                "config.fields[].field",
            },
        )
        record(
            "rules[].fieldNames[]",
            {"quality_risk_intervals.rules[].predicate.allOf[].field"},
        )
        record(
            "rules[].analysisFamily",
            {
                "quality_risk_intervals.rules[].fieldNames[]",
                "quality_risk_intervals.rules[].predicate.allOf[].type",
            },
        )
        numeric_bound_dependencies = {
            "quality_risk_intervals.rules[].analysisFamily",
            "quality_risk_intervals.rules[].predicate.allOf[].field",
            "quality_risk_intervals.rules[].predicate.allOf[].type",
            "population.DISCOVERY",
            *axes,
            (
                "config.qualityRisk.interactionBins"
                if family == "INTERACTION"
                else "config.qualityRisk.numericBins"
            ),
        }
        null_bound_dependencies = {
            "quality_risk_intervals.rules[].predicate.allOf[].field",
            "quality_risk_intervals.rules[].predicate.allOf[].type",
        }
        for suffix in ("lower", "lowerInclusive", "upper"):
            record(
                "rules[].predicate.allOf[]." + suffix,
                numeric_bound_dependencies
                if family in {"NUMERIC", "INTERACTION"}
                else null_bound_dependencies,
            )
        record("rules[].predicate.allOf[].upperInclusive", null_bound_dependencies)
        if family == "CATEGORICAL":
            record(
                "rules[].predicate.allOf[].values[]",
                {
                    "quality_risk_intervals.rules[].predicate.allOf[].field",
                    "quality_risk_intervals.rules[].predicate.allOf[].type",
                    "population.DISCOVERY",
                    *axes,
                },
            )

        for path, metadata in (
            ("rules[].firstAvailableStage", "firstAvailableStage"),
            ("rules[].equipmentType", "equipmentType"),
            ("rules[].evidenceFamily", "evidenceFamily"),
        ):
            record(
                path,
                {
                    "quality_risk_intervals.rules[].fieldNames[]",
                    "config.fields[].field",
                    "config.fields[]." + metadata,
                },
            )
        record(
            "rules[].applicationScope",
            {
                "quality_risk_intervals.rules[].analysisFamily",
                "quality_risk_intervals.rules[].fieldNames[]",
                "config.fields[].field",
                "config.fields[].featureRole",
            },
        )
        equipment_dependencies = {
            "quality_risk_intervals.rules[].applicationScope"
        }
        if rule["applicationScope"] == "EQUIPMENT_SPECIFIC":
            equipment_dependencies.add(
                "quality_risk_intervals.rules[].predicate.allOf[].values[]"
            )
        record("rules[].equipmentId", equipment_dependencies)
        record(
            "rules[].applicationContext",
            {"quality_risk_intervals.rules[].fieldNames[]"},
        )

        record(
            "rules[].adjustmentFieldsDropped[]",
            {
                "quality_risk_intervals.rules[].analysisFamily",
                "quality_risk_intervals.rules[].equipmentType",
                "quality_risk_intervals.rules[].fieldNames[]",
                "quality_risk_intervals.rules[].firstAvailableStage",
                "config.fields[].dataType",
                "config.fields[].dependencies[]",
                "config.fields[].equipmentType",
                "config.fields[].featureRole",
                "config.fields[].field",
                "config.fields[].firstAvailableStage",
                "config.riskAdjustmentHierarchies[].equipmentType",
                "config.riskAdjustmentHierarchies[].levels[][]",
            },
        )
        equipment_type = str(rule["equipmentType"])
        if equipment_type not in config.risk_adjustment_hierarchies:
            raise ValueError("quality rule equipment type has no adjustment hierarchy")
        attempted = {
            field.removesuffix("_band")
            for field in sidecar.discovery.adjustment_fields
        }
        context_axes = {_quality_feature_node(field) for field in attempted}
        adjustment_dependencies = {
            "quality_risk_intervals.rules[].adjustmentFieldsDropped[]",
            "quality_risk_intervals.rules[].equipmentType",
            "quality_risk_intervals.rules[].firstAvailableStage",
            *predicate_paths,
            "config.fields[].field",
            "config.fields[].firstAvailableStage",
            "config.qualityRisk.minimumDiscoverySupport",
            "config.qualityRisk.minimumInformativeStrata",
            "config.riskAdjustmentHierarchies[].equipmentType",
            "config.riskAdjustmentHierarchies[].levels[][]",
            "population.DISCOVERY",
            *axes,
            *context_axes,
        }
        record("rules[].adjustmentLevel", adjustment_dependencies)
        record("rules[].adjustmentKind", adjustment_dependencies)
        record("rules[].ruleId", rule_identity_paths)
        record(
            "rules[].earlyWarningEligible",
            {"quality_risk_intervals.rules[].firstAvailableStage"},
        )
        discovery_metric = rule["discovery"]
        confirmation_metric = rule["confirmation"]
        if not isinstance(discovery_metric, Mapping) or not isinstance(
            confirmation_metric, Mapping
        ):
            raise ValueError("quality rule metrics must be mappings")
        discovery_executed = discovery_metric["reasonCode"] not in (
            _INSUFFICIENT_QUALITY_REASONS
        )
        confirmation_executed = confirmation_metric["reasonCode"] not in (
            _INSUFFICIENT_QUALITY_REASONS
        )
        grade_dependencies = {
            "quality_risk_intervals.rules[].discovery.reasonCode"
        }
        if discovery_executed:
            grade_dependencies.add(
                "quality_risk_intervals.rules[].confirmation.reasonCode"
            )
        if discovery_executed and confirmation_executed:
            grade_dependencies.update(
                {
                    "config.qualityRisk.bhQ.caution",
                    "config.qualityRisk.bhQ.danger",
                    "config.qualityRisk.confirmationRelativeRisk.cautionExclusive",
                    "config.qualityRisk.confirmationRelativeRisk.danger",
                    "config.qualityRisk.minimumDangerDefects",
                    "config.qualityRisk.relativeRisk.caution",
                    "config.qualityRisk.relativeRisk.danger",
                    "config.qualityRisk.riskDifference.caution",
                    "config.qualityRisk.riskDifference.danger",
                    "quality_risk_intervals.rules[].adjustmentKind",
                    "quality_risk_intervals.rules[].confirmation.reasonCode",
                    "quality_risk_intervals.rules[].confirmation.relativeRisk",
                    "quality_risk_intervals.rules[].confirmation.riskDifference",
                    "quality_risk_intervals.rules[].discovery.defects",
                    "quality_risk_intervals.rules[].discovery.qValue",
                    "quality_risk_intervals.rules[].discovery.relativeRisk",
                    "quality_risk_intervals.rules[].discovery.relativeRiskCiLower",
                    "quality_risk_intervals.rules[].discovery.riskDifference",
                }
            )
        record("rules[].grade", grade_dependencies)
        if rule["displayMergeRuleIds"]:
            record("rules[].displayMergeRuleIds[]", display_dependencies)

        for split_name in ("discovery", "confirmation"):
            split_sidecar = (
                sidecar.discovery
                if split_name == "discovery"
                else sidecar.confirmation
            )
            population_dependencies = {
                "population.DISCOVERY",
                *predicate_paths,
                *axes,
                "quality_risk_intervals.rules[].adjustmentKind",
            }
            if split_name == "confirmation":
                population_dependencies.add("population.CONFIRMATION")
            prefix = "quality_risk_intervals.rules[]." + split_name + "."
            metric = rule[split_name]
            if not isinstance(metric, Mapping):
                raise ValueError("quality metric must be a mapping")
            executed = metric["reasonCode"] not in _INSUFFICIENT_QUALITY_REASONS
            minimum_support = (
                "config.qualityRisk.minimumDiscoverySupport"
                if split_name == "discovery"
                else "config.qualityRisk.minimumConfirmationSupport"
            )
            minimum_defects = (
                "config.qualityRisk.minimumCautionDefects"
                if split_name == "discovery"
                else "config.qualityRisk.minimumConfirmationDefects"
            )
            support_dependencies = set(population_dependencies)
            if executed:
                if (
                    not split_sidecar.informative_stratum_keys
                    or not split_sidecar.discovery_weights
                ):
                    raise ValueError(
                        "executed quality metric lacks informative strata or weights"
                    )
                support_dependencies.update(
                    {
                        "config.qualityRisk.minimumInformativeStrata",
                        minimum_support,
                        *context_axes,
                    }
                )
            record("rules[]." + split_name + ".support", support_dependencies)
            defect_dependencies = support_dependencies | {
                "replay_events.values_json.judge"
            }
            record("rules[]." + split_name + ".defects", defect_dependencies)
            count_nodes = {prefix + "support", prefix + "defects"}
            record("rules[]." + split_name + ".crudeRate", count_nodes)
            record(
                "rules[]." + split_name + ".crudeRateCiLower",
                count_nodes | {"config.wilsonZ"},
            )
            record(
                "rules[]." + split_name + ".crudeRateCiUpper",
                count_nodes | {"config.wilsonZ"},
            )
            reason_dependencies = set(population_dependencies)
            if metric["reasonCode"] != "NO_INFORMATIVE_STRATA":
                reason_dependencies.update({prefix + "support", minimum_support})
            if executed:
                reason_dependencies.update(
                    {
                        prefix + "defects",
                        minimum_defects,
                        "config.qualityRisk.minimumInformativeStrata",
                        "replay_events.values_json.judge",
                        *context_axes,
                    }
                )
            record("rules[]." + split_name + ".reasonCode", reason_dependencies)
            if executed:
                statistical_inputs = {
                    prefix + "reasonCode",
                    *support_dependencies,
                    "replay_events.values_json.judge",
                    minimum_defects,
                }
                record("rules[]." + split_name + ".adjustedRate", statistical_inputs)
                record(
                    "rules[]." + split_name + ".comparatorAdjustedRate",
                    statistical_inputs,
                )
                record(
                    "rules[]." + split_name + ".riskDifference",
                    {
                        prefix + "adjustedRate",
                        prefix + "comparatorAdjustedRate",
                    },
                )
                record("rules[]." + split_name + ".relativeRisk", statistical_inputs)
                record(
                    "rules[]." + split_name + ".pValue",
                    statistical_inputs
                    if split_name == "discovery"
                    else {prefix + "reasonCode"},
                )
            else:
                for suffix in (
                    "adjustedRate",
                    "comparatorAdjustedRate",
                    "riskDifference",
                    "relativeRisk",
                    "pValue",
                ):
                    record(
                        "rules[]." + split_name + "." + suffix,
                        {prefix + "reasonCode"},
                    )
            if split_name == "discovery":
                record(
                    "rules[].discovery.qValue",
                    {
                        "config.fdrFamilies[]",
                        "quality_risk_intervals.rules[].analysisFamily",
                        "quality_risk_intervals.rules[].discovery.pValue",
                    },
                )
                rr_ci_dependencies = {
                    "config.qualityRisk.minimumDiscoverySupport",
                    "quality_risk_intervals.rules[].discovery.reasonCode",
                    "quality_risk_intervals.rules[].discovery.support",
                }
                if executed:
                    rr_ci_dependencies.update(
                        {
                            "config.bootstrap.minimumValidReplicates",
                            "config.bootstrap.replicates",
                            "config.qualityRisk.minimumCautionDefects",
                            "identity.criteria_id",
                            "replay_events.charge_id",
                            "quality_risk_intervals.rules[].discovery.relativeRisk",
                        }
                    )
            else:
                record(
                    "rules[].confirmation.qValue",
                    {"quality_risk_intervals.rules[].confirmation.reasonCode"},
                )
                rr_ci_dependencies = {
                    "quality_risk_intervals.rules[].confirmation.reasonCode"
                }
            record(
                "rules[]." + split_name + ".relativeRiskCiLower",
                rr_ci_dependencies,
            )
            record(
                "rules[]." + split_name + ".relativeRiskCiUpper",
                rr_ci_dependencies,
            )
        result[str(rule["ruleId"])] = {
            path: sorted(dependencies, key=lambda value: value.encode("utf-8"))
            for path, dependencies in trace.items()
        }
    return result


def _normalize_quality_lineage(
    concrete: Mapping[str, Mapping[str, Sequence[str]]],
) -> dict[str, list[str]]:
    normalized: dict[str, set[str]] = {}
    for trace in concrete.values():
        for path, dependencies in trace.items():
            normalized.setdefault(path, set()).update(dependencies)
    return {
        path: sorted(dependencies, key=lambda value: value.encode("utf-8"))
        for path, dependencies in normalized.items()
    }


def _derived_field(
    role: str,
    path: str,
    conversion: str,
    dependencies: Sequence[str] | set[str],
    stage: str | None = None,
) -> dict[str, object]:
    return {
        "artifactRole": role,
        "conversion": conversion,
        "dependencies": sorted(
            set(dependencies), key=lambda value: value.encode("utf-8")
        ),
        "firstAvailableStage": stage,
        "outputField": path,
        "sourceColumn": None,
        "sourceRole": None,
    }


def _field_lineage(
    request: SummaryBuildRequest,
    quality_lineage: Mapping[str, Sequence[str]],
    summary_payload: Mapping[str, object],
) -> list[dict[str, object]]:
    config = request.analysis_config
    role_paths = _artifact_lineage_paths(request, summary_payload)
    range_lineage = _range_lineage(request)
    definitions = {str(item["field"]): item for item in config.fields}
    identifier_paths = {
        "charge_id": "charge_id",
        "slab_no": "slab_no",
        "hr_coil_id": "hr_coil_id",
        "ap_prod_id": "ap_prod_id",
    }
    raw_paths = {
        identifier_paths.get(field, "values_json." + field): definition
        for field, definition in definitions.items()
        if definition["sourceRole"] is not None
    }
    source_terminals = {
        f'{definition["sourceRole"]}.{definition["sourceColumn"]}'
        for definition in definitions.values()
        if definition["sourceRole"] is not None
    } | {
        "sm_cc.slab_no",
        "fur_hr.charge_id",
        "ap.hr_coil_id",
        "fur_hr.f_bfg_per",
        "fur_hr.f_cog_per",
        "fur_hr.f_ldg_per",
    }
    numeric_source_terminals = {
        f'{definition["sourceRole"]}.{definition["sourceColumn"]}'
        for definition in definitions.values()
        if definition["sourceRole"] is not None
        and definition["dataType"] in {"NUMBER", "HOUR"}
    } | {"fur_hr.f_bfg_per", "fur_hr.f_cog_per", "fur_hr.f_ldg_per"}
    nonnumeric_source_terminals = source_terminals - numeric_source_terminals
    quality_feature_nodes = {
        "replay_events." + identifier_paths.get(field, "values_json." + field)
        for field, definition in definitions.items()
        if definition["featureRole"] in _QUALITY_ROLES
    }
    numeric_quality_nodes = {
        "replay_events." + identifier_paths.get(field, "values_json." + field)
        for field, definition in definitions.items()
        if definition["featureRole"] in _QUALITY_ROLES
        and definition["dataType"] == "NUMBER"
    }
    categorical_quality_nodes = quality_feature_nodes - numeric_quality_nodes
    early_warning_quality_nodes = {
        "replay_events." + identifier_paths.get(field, "values_json." + field)
        for field, definition in definitions.items()
        if definition["featureRole"] in _QUALITY_ROLES
        and definition["firstAvailableStage"] != "AP_RECORDED_WITH_RESULT"
    }
    non_manifest_nodes = {
        role + "." + path
        for role, paths in role_paths.items()
        if role != "bundle_manifest"
        for path in paths
    }
    artifact_version_nodes = {
        role + ".schemaVersion"
        for role in (
            "analysis_config",
            "producer_runtime",
            "equipment_operating_ranges",
            "quality_risk_intervals",
            "analysis_summary",
        )
    } | {"replay_events.schema_version"}
    holdout_alert_dependencies = early_warning_quality_nodes | {
        "analysis_summary.asOf",
        "analysis_summary.holdoutMetrics[].alertGrade",
        "population.HOLDOUT",
        "replay_events.material_key",
        "replay_events.batch_step",
        "replay_events.replay_date",
        "quality_risk_intervals.rules[].applicationContext",
        "quality_risk_intervals.rules[].earlyWarningEligible",
        "quality_risk_intervals.rules[].firstAvailableStage",
        "quality_risk_intervals.rules[].grade",
        "quality_risk_intervals.rules[].predicate.allOf[].field",
        "quality_risk_intervals.rules[].predicate.allOf[].lower",
        "quality_risk_intervals.rules[].predicate.allOf[].lowerInclusive",
        "quality_risk_intervals.rules[].predicate.allOf[].type",
        "quality_risk_intervals.rules[].predicate.allOf[].upper",
        "quality_risk_intervals.rules[].predicate.allOf[].upperInclusive",
        "quality_risk_intervals.rules[].predicate.allOf[].values[]",
    }
    holdout_confusion_dependencies = holdout_alert_dependencies | {
        "replay_events.values_json.judge"
    }
    result: list[dict[str, object]] = []
    for role, raw_role_paths in role_paths.items():
        paths = sorted(raw_role_paths, key=lambda value: value.encode("utf-8"))
        for path in paths:
            if role == "replay_events" and path in raw_paths:
                definition = raw_paths[path]
                data_type = str(definition["dataType"])
                conversion = (
                    "PARSE_FINITE_BINARY64"
                    if data_type == "NUMBER"
                    else "PARSE_DATE"
                    if data_type == "DATE"
                    else "PARSE_HOUR_BUCKET"
                    if data_type == "HOUR"
                    else "COPY_SOURCE_SCALAR"
                )
                result.append(
                    {
                        "artifactRole": role,
                        "conversion": conversion,
                        "dependencies": [],
                        "firstAvailableStage": definition["firstAvailableStage"],
                        "outputField": path,
                        "sourceColumn": definition["sourceColumn"],
                        "sourceRole": definition["sourceRole"],
                    }
                )
                continue
            if role == "replay_events" and path in {
                "values_json.f_bfg_ratio",
                "values_json.f_cog_ratio",
                "values_json.f_ldg_ratio",
            }:
                result.append(
                    _derived_field(
                        role,
                        path,
                        "DERIVE_FUEL_RATIO",
                        ["fur_hr.f_bfg", "fur_hr.f_cog", "fur_hr.f_ldg"],
                        "FURNACE_EXTRACTED",
                    )
                )
                continue
            if role == "analysis_config":
                result.append(
                    _derived_field(
                        role, path, "COPY_CANONICAL_CONFIG", ["config." + path]
                    )
                )
                continue
            if role == "producer_runtime":
                result.append(
                    _derived_field(
                        role, path, "COPY_VERIFIED_RUNTIME", ["runtime." + path]
                    )
                )
                continue
            if role == "bundle_manifest":
                if path.startswith("identity.source."):
                    parts = path.split(".")
                    conversion = "COPY_SOURCE_METADATA"
                    dependencies = [f"source.{parts[2]}.{parts[3]}"]
                elif ".schema." in path:
                    schema_role = path.split(".schema.", 1)[1].removesuffix(".sha256")
                    conversion = "COPY_DIGEST_VALUE"
                    dependencies = ["schema." + schema_role + ".sha256"]
                elif path.startswith("criteriaIdentity."):
                    name = path.removeprefix("criteriaIdentity.")
                    if name == "analysis_config_sha256":
                        conversion = "COPY_DIGEST_VALUE"
                        dependencies = ["identity.analysis_config_sha256"]
                    elif name == "criteria_projection_sha256":
                        conversion = "COPY_DIGEST_VALUE"
                        dependencies = ["identity.criteria_projection_sha256"]
                    elif name == "producer_runtime_sha256":
                        conversion = "COPY_DIGEST_VALUE"
                        dependencies = ["identity.producer_runtime_sha256"]
                    elif name == "as_of":
                        conversion = "COPY_BOUNDARY_DATE"
                        dependencies = ["analysis_summary.asOf"]
                    else:
                        raise AssertionError("unexpected criteria identity lineage path")
                elif path.startswith("identity."):
                    name = path.removeprefix("identity.")
                    if name == "analysis_config_sha256":
                        conversion = "COPY_DIGEST_VALUE"
                        dependencies = ["identity.analysis_config_sha256"]
                    elif name == "producer_runtime_sha256":
                        conversion = "COPY_DIGEST_VALUE"
                        dependencies = ["identity.producer_runtime_sha256"]
                    elif name == "criteria_id":
                        conversion = "COPY_IDENTITY_VALUE"
                        dependencies = ["identity.criteria_id"]
                    else:
                        raise AssertionError("unexpected manifest identity lineage path")
                elif path == "artifacts[].role":
                    conversion = "PROJECT_ARTIFACT_METADATA"
                    dependencies = [
                        "schema.analysis_config",
                        "schema.analysis_summary",
                        "schema.equipment_operating_ranges",
                        "schema.producer_runtime",
                        "schema.quality_risk_intervals",
                        "schema.replay_events",
                    ]
                elif path == "artifacts[].schemaVersion":
                    conversion = "COPY_SCHEMA_VERSION"
                    dependencies = sorted(artifact_version_nodes)
                elif path == "artifacts[].sizeBytes":
                    conversion = "COMPUTE_BYTE_SIZE"
                    dependencies = sorted(non_manifest_nodes)
                elif path == "artifacts[].sha256":
                    conversion = "COMPUTE_SHA256"
                    dependencies = sorted(non_manifest_nodes)
                elif path == "bundleId":
                    conversion = "COMPUTE_IDENTITY"
                    dependencies = sorted(
                        "bundle_manifest." + value
                        for value in role_paths["bundle_manifest"]
                        if value.startswith("identity.")
                    )
                elif path == "criteriaId":
                    conversion = "COMPUTE_IDENTITY"
                    dependencies = sorted(
                        "bundle_manifest." + value
                        for value in role_paths["bundle_manifest"]
                        if value.startswith("criteriaIdentity.")
                    )
                elif path == "schemaVersion":
                    conversion = "COPY_SCHEMA_VERSION"
                    dependencies = ["schema.bundle_manifest"]
                elif path == "asOf":
                    conversion = "COPY_BOUNDARY_DATE"
                    dependencies = ["analysis_summary.asOf"]
                elif path == "timezone":
                    conversion = "COPY_CANONICAL_CONFIG"
                    dependencies = ["analysis_config.timezone"]
                elif path == "labelMaturityDays":
                    conversion = "COPY_CANONICAL_CONFIG"
                    dependencies = ["analysis_config.labelMaturityDays"]
                else:
                    raise AssertionError("unexpected manifest lineage path")
                result.append(_derived_field(role, path, conversion, dependencies))
                continue
            if role == "replay_events":
                replay_dependencies = {
                    "schema_version": ["schema.replay_events"],
                    "bundle_id": ["identity.bundle_id"],
                    "criteria_id": ["identity.criteria_id"],
                    "material_key": ["replay_events.charge_id", "replay_events.slab_no"],
                    "replay_date": [
                        "ap.ap_date",
                        "fur_hr.f_ext_date",
                        "replay_events.batch_step",
                        "sm_cc.cast_date",
                    ],
                    "replay_hour": ["fur_hr.f_ext_time", "replay_events.batch_step"],
                    "batch_kind": ["replay_events.batch_step"],
                    "batch_id": [
                        "replay_events.batch_kind",
                        "replay_events.replay_date",
                        "replay_events.replay_hour",
                    ],
                    "batch_step": [
                        "ap.ap_prod_id",
                        "ap.hr_coil_id",
                        "fur_hr.hr_coil_id",
                        "replay_events.material_key",
                    ],
                    "time_precision": ["replay_events.batch_step"],
                    "equipment_type": ["replay_events.batch_step"],
                    "equipment_id": [
                        "sm_cc.sm_plant",
                        "fur_hr.furnace_no",
                        "ap.ap_plant",
                        "replay_events.equipment_type",
                    ],
                    "equipment_batch_id": [
                        "replay_events.batch_id",
                        "replay_events.equipment_id",
                        "replay_events.equipment_type",
                    ],
                    "event_id": [
                        "replay_events.batch_id",
                        "replay_events.batch_step",
                        "replay_events.equipment_batch_id",
                        "replay_events.equipment_id",
                        "replay_events.equipment_type",
                        "replay_events.material_key",
                    ],
                }
                stage = (
                    "CAST_RECORDED"
                    if path in {"schema_version", "bundle_id", "criteria_id", "material_key"}
                    else None
                )
                conversion = (
                    "COPY_SCHEMA_VERSION"
                    if path == "schema_version"
                    else "COPY_IDENTITY_VALUE"
                    if path in {"bundle_id", "criteria_id"}
                    else "COMPUTE_IDENTITY"
                    if path in {"material_key", "batch_id", "equipment_batch_id", "event_id"}
                    else "SELECT_STAGE_VALUE"
                    if path in {"replay_date", "replay_hour"}
                    else "CLASSIFY_REPLAY_SCHEDULE"
                    if path in {"batch_kind", "batch_step", "time_precision"}
                    else "SELECT_STAGE_EQUIPMENT"
                )
                result.append(
                    _derived_field(
                        role, path, conversion, replay_dependencies[path], stage
                    )
                )
                continue
            if role == "equipment_operating_ranges":
                if path == "asOf":
                    conversion = "SELECT_TIME_BOUNDARY"
                    dependencies = [
                        "config.splits.referenceFraction",
                        "fur_hr.charge_id",
                        "fur_hr.hr_date",
                        "fur_hr.slab_no",
                    ]
                elif path == "criteriaId":
                    conversion = "COPY_IDENTITY_VALUE"
                    dependencies = ["identity.criteria_id"]
                elif path == "schemaVersion":
                    conversion = "COPY_SCHEMA_VERSION"
                    dependencies = ["schema.equipment_operating_ranges"]
                else:
                    dependencies = range_lineage[path]
                    conversion = (
                        "COMPUTE_IDENTITY"
                        if path == "ranges[].ruleId"
                        else "COPY_FIELD_METADATA"
                        if path
                        in {
                            "ranges[].field",
                            "ranges[].fieldRole",
                            "ranges[].firstAvailableStage",
                            "ranges[].equipmentType",
                        }
                        else "COUNT_RANGE_SUPPORT"
                        if path == "ranges[].support"
                        else "TYPE1_QUANTILE"
                        if path
                        in {
                            "ranges[].median",
                            "ranges[].p01",
                            "ranges[].p05",
                            "ranges[].p95",
                            "ranges[].p99",
                        }
                        else "APPLY_RANGE_TAIL_POLICY"
                        if path
                        in {
                            "ranges[].lowerTailEnabled",
                            "ranges[].upperTailEnabled",
                        }
                        else "DERIVE_RANGE_GROUP"
                    )
                result.append(_derived_field(role, path, conversion, dependencies))
                continue
            if role == "quality_risk_intervals":
                if path == "criteriaId":
                    conversion = "COPY_IDENTITY_VALUE"
                    dependencies = ["identity.criteria_id"]
                elif path == "schemaVersion":
                    conversion = "COPY_SCHEMA_VERSION"
                    dependencies = ["schema.quality_risk_intervals"]
                elif path == "asOf":
                    conversion = "SELECT_TIME_BOUNDARY"
                    dependencies = [
                        "config.splits.referenceFraction",
                        "fur_hr.charge_id",
                        "fur_hr.hr_date",
                        "fur_hr.slab_no",
                    ]
                else:
                    dependencies = list(quality_lineage[path])
                    conversion = (
                        "COMPUTE_IDENTITY"
                        if path == "rules[].ruleId"
                        else "COMPUTE_QUALITY_METRIC"
                        if path.startswith(
                            ("rules[].discovery.", "rules[].confirmation.")
                        )
                        else "APPLY_GRADE_POLICY"
                        if path == "rules[].grade"
                        else "DERIVE_STAGE_ELIGIBILITY"
                        if path == "rules[].earlyWarningEligible"
                        else "MERGE_DISPLAY_INTERVALS"
                        if path == "rules[].displayMergeRuleIds[]"
                        else "DERIVE_RULE_CANDIDATE"
                    )
                result.append(_derived_field(role, path, conversion, dependencies))
                continue

            if path == "bundleId":
                conversion = "COPY_IDENTITY_VALUE"
                dependencies = ["identity.bundle_id"]
            elif path == "criteriaId":
                conversion = "COPY_IDENTITY_VALUE"
                dependencies = ["identity.criteria_id"]
            elif path == "schemaVersion":
                conversion = "COPY_SCHEMA_VERSION"
                dependencies = ["schema.analysis_summary"]
            elif path == "asOf":
                conversion = "SELECT_TIME_BOUNDARY"
                dependencies = [
                    "config.splits.referenceFraction",
                    "fur_hr.charge_id",
                    "fur_hr.hr_date",
                    "fur_hr.slab_no",
                ]
            elif path == "innerSplitDate":
                conversion = "SELECT_TIME_BOUNDARY"
                dependencies = [
                    "analysis_summary.asOf",
                    "config.labelMaturityDays",
                    "config.splits.discoveryFraction",
                    "population.REFERENCE",
                    "replay_events.values_json.ap_date",
                    "replay_events.values_json.hr_date",
                    "replay_events.values_json.judge",
                ]
            elif path == "evaluationMode":
                conversion = "COPY_POLICY_VALUE"
                dependencies = ["policy.LOCKED_RETROSPECTIVE_HOLDOUT"]
            elif path.startswith("dateRange"):
                conversion = "COMPUTE_DATE_RANGE"
                dependencies = ["replay_events.replay_date"]
            elif path.startswith("sourceColumnProfiles"):
                if path == "sourceColumnProfiles[].numeric":
                    dependencies = ["analysis_summary.sourceColumnProfiles[].dataType"]
                elif path.startswith("sourceColumnProfiles[].numeric."):
                    dependencies = sorted(numeric_source_terminals)
                elif path == "sourceColumnProfiles[].levels":
                    dependencies = ["analysis_summary.sourceColumnProfiles[].dataType"]
                elif path.startswith("sourceColumnProfiles[].levels[]."):
                    dependencies = sorted(nonnumeric_source_terminals)
                else:
                    dependencies = sorted(source_terminals)
                result.append(
                    _derived_field(role, path, "PROFILE_SOURCE_COLUMN", dependencies)
                )
                continue
            elif path.startswith("driftMetrics"):
                if path == "driftMetrics[].field":
                    dependencies = [
                        "config.fields[].featureRole",
                        "config.fields[].field",
                        "config.fields[].firstAvailableStage",
                    ]
                    conversion = "COPY_FIELD_METADATA"
                elif path == "driftMetrics[].dataType":
                    dependencies = [
                        "config.fields[].dataType",
                        "config.fields[].featureRole",
                    ]
                    conversion = "COPY_FIELD_METADATA"
                elif path.endswith(".levels"):
                    dependencies = ["analysis_summary.driftMetrics[].dataType"]
                    conversion = "COPY_FIELD_METADATA"
                else:
                    population = (
                        "population.REFERENCE"
                        if ".reference." in path
                        else "population.HOLDOUT"
                    )
                    stage_dependencies = (
                        {
                            "analysis_summary.asOf",
                            "replay_events.values_json.ap_date",
                        }
                        if population == "population.REFERENCE"
                        else set()
                    )
                    if any(
                        path.endswith("." + name)
                        for name in ("p05", "median", "p95")
                    ):
                        dependencies = sorted(
                            numeric_quality_nodes
                            | stage_dependencies
                            | {
                                population,
                                "analysis_summary.driftMetrics[].dataType",
                            }
                        )
                    elif ".levels[]." in path:
                        dependencies = sorted(
                            categorical_quality_nodes
                            | stage_dependencies
                            | {population}
                        )
                    else:
                        dependencies = sorted(
                            quality_feature_nodes | stage_dependencies | {population}
                        )
                    conversion = "COMPARE_DISTRIBUTIONS"
                result.append(_derived_field(role, path, conversion, dependencies))
                continue
            elif path.startswith("holdoutMetrics"):
                suffix = path.removeprefix("holdoutMetrics[].")
                if suffix == "alertGrade":
                    conversion = "COPY_POLICY_VALUE"
                    dependencies = [
                        "policy.HOLDOUT_ALERT_CAUTION_OR_DANGER",
                        "policy.HOLDOUT_ALERT_DANGER",
                    ]
                elif suffix == "total":
                    conversion = "COUNT_PARTITION"
                    dependencies = [
                        "population.HOLDOUT",
                        "replay_events.values_json.judge",
                    ]
                elif suffix in {
                    "truePositive",
                    "falsePositive",
                    "trueNegative",
                    "falseNegative",
                }:
                    conversion = "COUNT_PARTITION"
                    dependencies = sorted(holdout_confusion_dependencies)
                else:
                    metric_name, leaf = suffix.split(".", 1)
                    prefix = "analysis_summary.holdoutMetrics[]."
                    metric_inputs = {
                        "alertRate": {
                            prefix + "truePositive",
                            prefix + "falsePositive",
                            prefix + "total",
                        },
                        "precision": {
                            prefix + "truePositive",
                            prefix + "falsePositive",
                        },
                        "recall": {
                            prefix + "truePositive",
                            prefix + "falseNegative",
                        },
                        "lift": {
                            prefix + "precision.pointEstimate",
                            prefix + "baseDefectRate.pointEstimate",
                        },
                        "falseAlertsPer100": {
                            prefix + "falsePositive",
                            prefix + "total",
                        },
                    }
                    bootstrap_dependencies = {
                        "config.bootstrap.replicates",
                        "identity.criteria_id",
                        "replay_events.charge_id",
                    }
                    if leaf != "validReplicates":
                        bootstrap_dependencies.add(
                            "config.bootstrap.minimumValidReplicates"
                        )
                    if metric_name == "baseDefectRate":
                        dependencies = {
                            "population.HOLDOUT",
                            "replay_events.values_json.judge",
                        }
                        if leaf != "pointEstimate":
                            dependencies.update(bootstrap_dependencies)
                    else:
                        dependencies = set(metric_inputs[metric_name])
                        if leaf != "pointEstimate" and metric_name in {
                            "alertRate",
                            "recall",
                            "falseAlertsPer100",
                        }:
                            matching = (
                                holdout_alert_dependencies
                                if metric_name == "alertRate"
                                else holdout_confusion_dependencies
                            )
                            if metric_name == "alertRate":
                                matching = matching | {
                                    "replay_events.values_json.judge"
                                }
                            dependencies.update(matching | bootstrap_dependencies)
                    dependencies = sorted(dependencies)
                    conversion = "COMPUTE_HOLDOUT_METRIC"
                result.append(_derived_field(role, path, conversion, dependencies))
                continue
            elif path.startswith("splitCounts.reference"):
                if path.endswith(".total"):
                    dependencies = ["population.REFERENCE"]
                elif path.endswith((".dateFrom", ".dateTo")):
                    dependencies = [
                        "population.REFERENCE",
                        "replay_events.values_json.hr_date",
                    ]
                elif path.endswith(".unknownOrCensored"):
                    dependencies = [
                        "analysis_summary.splitCounts.reference.defects",
                        "analysis_summary.splitCounts.reference.nonDefects",
                        "analysis_summary.splitCounts.reference.total",
                    ]
                else:
                    dependencies = [
                        "analysis_summary.asOf",
                        "config.labelMaturityDays",
                        "population.REFERENCE",
                        "replay_events.values_json.ap_date",
                        "replay_events.values_json.hr_date",
                        "replay_events.values_json.judge",
                    ]
                result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                continue
            elif path.startswith("splitCounts.discovery"):
                if path.endswith(".total"):
                    dependencies = ["population.DISCOVERY"]
                elif path.endswith((".dateFrom", ".dateTo")):
                    dependencies = [
                        "population.DISCOVERY",
                        "replay_events.values_json.hr_date",
                    ]
                elif path.endswith(".unknownOrCensored"):
                    dependencies = [
                        "analysis_summary.splitCounts.discovery.defects",
                        "analysis_summary.splitCounts.discovery.nonDefects",
                        "analysis_summary.splitCounts.discovery.total",
                    ]
                else:
                    dependencies = [
                        "population.DISCOVERY",
                        "replay_events.values_json.judge",
                    ]
                result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                continue
            elif path.startswith("splitCounts.confirmation"):
                if path.endswith(".total"):
                    dependencies = ["population.CONFIRMATION"]
                elif path.endswith((".dateFrom", ".dateTo")):
                    dependencies = [
                        "population.CONFIRMATION",
                        "replay_events.values_json.hr_date",
                    ]
                elif path.endswith(".unknownOrCensored"):
                    dependencies = [
                        "analysis_summary.splitCounts.confirmation.defects",
                        "analysis_summary.splitCounts.confirmation.nonDefects",
                        "analysis_summary.splitCounts.confirmation.total",
                    ]
                else:
                    dependencies = [
                        "population.CONFIRMATION",
                        "replay_events.values_json.judge",
                    ]
                result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                continue
            elif path.startswith("splitCounts.holdout"):
                if path.endswith(".total"):
                    dependencies = ["population.HOLDOUT"]
                elif path.endswith((".dateFrom", ".dateTo")):
                    dependencies = [
                        "population.HOLDOUT",
                        "replay_events.values_json.hr_date",
                    ]
                elif path.endswith(".unknownOrCensored"):
                    dependencies = [
                        "analysis_summary.splitCounts.holdout.defects",
                        "analysis_summary.splitCounts.holdout.nonDefects",
                        "analysis_summary.splitCounts.holdout.total",
                    ]
                else:
                    dependencies = [
                        "population.HOLDOUT",
                        "replay_events.values_json.judge",
                    ]
                result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                continue
            elif path.startswith("quarantineCounts"):
                reason = path.removeprefix("quarantineCounts.")
                dependencies = list(_QUARANTINE_DEPENDENCIES[reason])
                result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                continue
            elif path.startswith("labelCensoringCounts"):
                reason = path.removeprefix("labelCensoringCounts.")
                if reason == "AP_UNLINKED":
                    dependencies = [
                        "ap.hr_coil_id",
                        "fur_hr.hr_coil_id",
                        "population.HOLDOUT",
                        "population.REFERENCE",
                    ]
                elif reason == "LABEL_MISSING":
                    dependencies = ["ap.judge", "population.REFERENCE"]
                else:
                    dependencies = [
                        "analysis_summary.asOf",
                        "ap.ap_date",
                        "config.labelMaturityDays",
                        "fur_hr.hr_date",
                        "population.REFERENCE",
                    ]
                result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                continue
            elif path.startswith("chargePurgeCounts"):
                if ".outer." in path:
                    dependencies = [
                        "analysis_summary.asOf",
                        "fur_hr.charge_id",
                        "fur_hr.hr_date",
                    ]
                else:
                    dependencies = [
                        "analysis_summary.asOf",
                        "analysis_summary.innerSplitDate",
                        "config.labelMaturityDays",
                        "population.REFERENCE",
                        "replay_events.charge_id",
                        "replay_events.values_json.ap_date",
                        "replay_events.values_json.hr_date",
                        "replay_events.values_json.judge",
                    ]
                result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                continue
            else:
                raise AssertionError("unexpected summary lineage path")
            result.append(_derived_field(role, path, conversion, dependencies))
    return result


def _observed_nonzero_counts(
    raw: object,
    label: str,
) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"{label} must be a mapping")
    snapshot = tuple(raw.items())
    result: dict[str, int] = {}
    for key, value in snapshot:
        if type(key) is not str or not key:
            raise ValueError(f"{label} keys must be built-in non-empty strings")
        if type(value) is not int or value < 0:
            raise ValueError(f"{label} values must be non-negative built-in integers")
        if value:
            result[key] = value
    return {
        key: result[key]
        for key in sorted(result, key=lambda value: value.encode("utf-8"))
    }


def _split_counts(request: SummaryBuildRequest) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name in ("confirmation", "discovery", "holdout", "reference"):
        raw = request.split.counts.get(name)
        if not isinstance(raw, Mapping):
            raise ValueError(f"split counts are missing {name}")
        snapshot = dict(tuple(raw.items()))
        expected = {
            "dateFrom",
            "dateTo",
            "defects",
            "nonDefects",
            "total",
            "unknownOrCensored",
        }
        if set(snapshot) != expected:
            raise ValueError(f"split count {name} keys are invalid")
        for key in ("defects", "nonDefects", "total", "unknownOrCensored"):
            value = snapshot[key]
            if type(value) is not int or value < 0:
                raise ValueError(f"split count {name}.{key} is invalid")
        if snapshot["total"] != (
            snapshot["defects"]
            + snapshot["nonDefects"]
            + snapshot["unknownOrCensored"]
        ):
            raise ValueError(f"split count {name} is not balanced")
        for key in ("dateFrom", "dateTo"):
            value = snapshot[key]
            if type(value) is not str:
                raise TypeError(f"split count {name}.{key} must be a string")
            date.fromisoformat(value)
        result[name] = snapshot
    return result


def _charge_purge_counts(request: SummaryBuildRequest) -> dict[str, object]:
    raw = request.split.counts.get("chargePurges")
    if not isinstance(raw, Mapping):
        raise ValueError("split counts are missing chargePurges")
    snapshot = dict(tuple(raw.items()))
    expected = {
        "outerChargeIds",
        "outerBoundaryRows",
        "innerChargeIds",
        "innerMatureRows",
    }
    if set(snapshot) != expected:
        raise ValueError("charge purge keys are invalid")
    result: dict[str, object] = {}
    for output_name, ids_name, rows_name in (
        ("outer", "outerChargeIds", "outerBoundaryRows"),
        ("inner", "innerChargeIds", "innerMatureRows"),
    ):
        ids = tuple(snapshot[ids_name])
        if any(type(value) is not str or not value for value in ids):
            raise ValueError("charge purge IDs must be non-empty strings")
        if len(ids) != len(set(ids)):
            raise ValueError("charge purge IDs must be unique")
        row_count = snapshot[rows_name]
        if type(row_count) is not int or row_count < 0:
            raise ValueError("charge purge row counts must be non-negative integers")
        result[output_name] = {"chargeCount": len(ids), "rowCount": row_count}
    return result


def build_summary(request: SummaryBuildRequest) -> dict[str, object]:
    """Build the schema-valid deterministic summary from frozen rich inputs."""
    if type(request) is not SummaryBuildRequest:
        raise TypeError("request must be an exact SummaryBuildRequest")
    rules = request.quality_rules.to_wire()
    quality_lineage = _normalize_quality_lineage(
        _concrete_quality_lineage(request.quality_rules, request.analysis_config)
    )
    populations = _population_keys(request)
    replay_dates = [event.replay_date.isoformat() for event in request.events]
    summary: dict[str, object] = {
        "asOf": request.split.as_of.isoformat(),
        "bundleId": request.bundle_identity.value,
        "chargePurgeCounts": _charge_purge_counts(request),
        "criteriaId": request.criteria_identity.value,
        "dateRange": {
            "from": min(replay_dates) if replay_dates else None,
            "to": max(replay_dates) if replay_dates else None,
        },
        "driftMetrics": _drift_metrics(request),
        "evaluationMode": "LOCKED_RETROSPECTIVE_HOLDOUT",
        "holdoutMetrics": holdout_metrics(
            request.split,
            rules,
            request.criteria_identity.value,
        ),
        "innerSplitDate": request.split.discovery_cutoff.isoformat(),
        "labelCensoringCounts": _observed_nonzero_counts(
            request.split.counts.get("labelCensoring"),
            "label censoring counts",
        ),
        "quarantineCounts": _observed_nonzero_counts(
            request.genealogy.audit.get("quarantine"),
            "quarantine counts",
        ),
        "schemaVersion": "sfep-analysis-summary/v1",
        "sourceColumnProfiles": _source_profiles(request),
        "splitCounts": _split_counts(request),
    }
    summary["lineage"] = {
        "aggregates": _aggregate_lineage(request, populations),
        "fields": _field_lineage(request, quality_lineage, summary),
        "materials": _material_lineage(request.material_catalog),
        "populations": [item.to_wire() for item in populations],
    }
    canonical_json_bytes(summary)
    from equipment_quality.schema import validate_normative_instance

    validate_normative_instance("analysis_summary.schema.json", summary)
    return summary
