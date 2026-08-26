"""Read-only, standard-library oracle for the equipment-quality semantic golden.

This module deliberately derives the fixture from the normative design inputs.  It
does not share code, helpers, factories, or expectation bytes with the producer.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import struct
from datetime import date, timedelta
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType


_SOURCE_NAMES = {
    "sm_cc": "sts_1sm_cc_1.csv",
    "fur_hr": "sts_2fur_hr_2.csv",
    "ap": "sts_3ap_3.csv",
}
_STAGE_RANK = {
    "CAST_RECORDED": 0,
    "FURNACE_CHARGED": 10,
    "PREHEAT_COMPLETE": 11,
    "HEAT_COMPLETE": 12,
    "SOAK_COMPLETE": 13,
    "FURNACE_EXTRACTED": 14,
    "RM4_RECORDED": 15,
    "AP_RECORDED_WITH_RESULT": 20,
}
_QUALITY_ROLES = {"DIRECT_OPERATION", "PRODUCT_STATE_REFERENCE", "CONTEXT", "EQUIPMENT_IDENTIFIER"}
_RANGE_ROLES = {"DIRECT_OPERATION", "PRODUCT_STATE_REFERENCE"}
_AS_OF = date(2025, 2, 20)
_INNER_SPLIT = date(2025, 1, 3)
_CRITERIA_TOKEN = "@CRITERIA_ID@"
_BUNDLE_TOKEN = "@BUNDLE_ID@"

_LINEAGE_PATHS = {
    "bundle_manifest": tuple("""
artifacts[].role
artifacts[].schemaVersion
artifacts[].sha256
artifacts[].sizeBytes
asOf
bundleId
criteriaId
criteriaIdentity.analysis_config_sha256
criteriaIdentity.as_of
criteriaIdentity.criteria_projection_sha256
criteriaIdentity.producer_runtime_sha256
criteriaIdentity.schema.analysis_config.sha256
criteriaIdentity.schema.equipment_operating_ranges.sha256
criteriaIdentity.schema.producer_runtime.sha256
criteriaIdentity.schema.quality_risk_intervals.sha256
identity.analysis_config_sha256
identity.criteria_id
identity.producer_runtime_sha256
identity.schema.analysis_config.sha256
identity.schema.analysis_summary.sha256
identity.schema.bundle_manifest.sha256
identity.schema.equipment_operating_ranges.sha256
identity.schema.producer_runtime.sha256
identity.schema.quality_risk_intervals.sha256
identity.schema.replay_events.sha256
identity.source.ap.name
identity.source.ap.sha256
identity.source.ap.size_bytes
identity.source.fur_hr.name
identity.source.fur_hr.sha256
identity.source.fur_hr.size_bytes
identity.source.sm_cc.name
identity.source.sm_cc.sha256
identity.source.sm_cc.size_bytes
labelMaturityDays
schemaVersion
timezone
""".strip().splitlines()),
    "analysis_config": tuple("""
analysisConfigVersion
bootstrap.minimumValidReplicates
bootstrap.replicates
evidenceFamilies[]
fdrFamilies[]
fields[].dataType
fields[].dependencies[]
fields[].equipmentType
fields[].evidenceFamily
fields[].featureRole
fields[].field
fields[].firstAvailableStage
fields[].sourceColumn
fields[].sourceRole
fixedInteractions[][]
labelMaturityDays
operatingRanges.extremeLowerQuantile
operatingRanges.extremeTailMinimumSupport
operatingRanges.extremeUpperQuantile
operatingRanges.minimumSupport
operatingRanges.quantileMethod
operatingRanges.typicalLowerQuantile
operatingRanges.typicalUpperQuantile
qualityRisk.bhQ.caution
qualityRisk.bhQ.danger
qualityRisk.confirmationRelativeRisk.cautionExclusive
qualityRisk.confirmationRelativeRisk.danger
qualityRisk.interactionBins
qualityRisk.minimumCautionDefects
qualityRisk.minimumConfirmationDefects
qualityRisk.minimumConfirmationSupport
qualityRisk.minimumDangerDefects
qualityRisk.minimumDiscoverySupport
qualityRisk.minimumInformativeStrata
qualityRisk.numericBins
qualityRisk.relativeRisk.caution
qualityRisk.relativeRisk.danger
qualityRisk.riskDifference.caution
qualityRisk.riskDifference.danger
qualityRisk.zeroCellCorrection
rangeContextHierarchies[].equipmentType
rangeContextHierarchies[].levels[][]
riskAdjustmentHierarchies[].equipmentType
riskAdjustmentHierarchies[].levels[][]
schemaVersion
splits.discoveryFraction
splits.referenceFraction
timezone
wilsonZ
""".strip().splitlines()),
    "producer_runtime": tuple("""
environmentPolicy.floatPolicy
environmentPolicy.localeIndependentParsing
environmentPolicy.pythonHashSeed
environmentPolicy.timezone
locks.bootstrap
locks.buildRequirements
locks.producer
locks.pyproject
locks.requirements
locks.wheelhouse
packages[].direct
packages[].installedCodeTreeSha256
packages[].name
packages[].version
packages[].wheelFilename
packages[].wheelSha256
packages[].wheelTag
pipVersion
platform.machine
platform.macosProductVersion
platform.sysconfigPlatform
platform.system
producer.installedCodeTreeSha256
producer.name
producer.sourceSha256
producer.version
producer.wheelFilename
producer.wheelSha256
python.build
python.cacheTag
python.executableSha256
python.implementation
python.soabi
python.version
schemaVersion
""".strip().splitlines()),
    "equipment_operating_ranges": ("asOf", "criteriaId", "schemaVersion"),
    "quality_risk_intervals": tuple("""
asOf
criteriaId
rules[].adjustmentFieldsDropped[]
rules[].adjustmentKind
rules[].adjustmentLevel
rules[].analysisFamily
rules[].applicationContext
rules[].applicationScope
rules[].confirmation.adjustedRate
rules[].confirmation.comparatorAdjustedRate
rules[].confirmation.crudeRate
rules[].confirmation.crudeRateCiLower
rules[].confirmation.crudeRateCiUpper
rules[].confirmation.defects
rules[].confirmation.pValue
rules[].confirmation.qValue
rules[].confirmation.reasonCode
rules[].confirmation.relativeRisk
rules[].confirmation.relativeRiskCiLower
rules[].confirmation.relativeRiskCiUpper
rules[].confirmation.riskDifference
rules[].confirmation.support
rules[].discovery.adjustedRate
rules[].discovery.comparatorAdjustedRate
rules[].discovery.crudeRate
rules[].discovery.crudeRateCiLower
rules[].discovery.crudeRateCiUpper
rules[].discovery.defects
rules[].discovery.pValue
rules[].discovery.qValue
rules[].discovery.reasonCode
rules[].discovery.relativeRisk
rules[].discovery.relativeRiskCiLower
rules[].discovery.relativeRiskCiUpper
rules[].discovery.riskDifference
rules[].discovery.support
rules[].displayMergeRuleIds[]
rules[].earlyWarningEligible
rules[].equipmentId
rules[].equipmentType
rules[].evidenceFamily
rules[].fieldNames[]
rules[].firstAvailableStage
rules[].grade
rules[].predicate.allOf[].field
rules[].predicate.allOf[].lower
rules[].predicate.allOf[].lowerInclusive
rules[].predicate.allOf[].type
rules[].predicate.allOf[].upper
rules[].predicate.allOf[].upperInclusive
rules[].predicate.allOf[].values[]
rules[].ruleId
schemaVersion
""".strip().splitlines()),
    "replay_events": tuple("""
ap_prod_id
batch_id
batch_kind
batch_step
bundle_id
charge_id
criteria_id
equipment_batch_id
equipment_id
equipment_type
event_id
hr_coil_id
material_key
replay_date
replay_hour
schema_version
slab_no
time_precision
values_json.ap_date
values_json.ap_line_speed
values_json.ap_plant
values_json.ap_shift
values_json.ap_thick
values_json.ap_width
values_json.cast_date
values_json.cc_gubun
values_json.delta_ferrite
values_json.f_bfg
values_json.f_bfg_ratio
values_json.f_cog
values_json.f_cog_ratio
values_json.f_ext_date
values_json.f_ext_time
values_json.f_heat_interval
values_json.f_heat_temp
values_json.f_jangip_gubun
values_json.f_jangip_temp
values_json.f_ldg
values_json.f_ldg_ratio
values_json.f_pre_interval
values_json.f_pre_temp
values_json.f_sock_interval
values_json.f_sock_temp
values_json.furnace_no
values_json.hr_date
values_json.hr_thick
values_json.hr_width
values_json.ingre_cr
values_json.ingre_ni
values_json.ingre_s
values_json.judge
values_json.mlac_ratio
values_json.rm4_temp
values_json.rm_pitch
values_json.slab_grind
values_json.slab_gubun
values_json.slab_width
values_json.sm_plant
values_json.steel_grade
values_json.steel_usage
values_json.tundish_temp
""".strip().splitlines()),
    "analysis_summary": tuple("""
asOf
bundleId
chargePurgeCounts.inner.chargeCount
chargePurgeCounts.inner.rowCount
chargePurgeCounts.outer.chargeCount
chargePurgeCounts.outer.rowCount
criteriaId
dateRange.from
dateRange.to
driftMetrics[].dataType
driftMetrics[].field
driftMetrics[].holdout.levels
driftMetrics[].holdout.levels[].count
driftMetrics[].holdout.levels[].value
driftMetrics[].holdout.median
driftMetrics[].holdout.missingRate
driftMetrics[].holdout.p05
driftMetrics[].holdout.p95
driftMetrics[].holdout.support
driftMetrics[].reference.levels
driftMetrics[].reference.levels[].count
driftMetrics[].reference.levels[].value
driftMetrics[].reference.median
driftMetrics[].reference.missingRate
driftMetrics[].reference.p05
driftMetrics[].reference.p95
driftMetrics[].reference.support
evaluationMode
holdoutMetrics[].alertGrade
holdoutMetrics[].alertRate.lower
holdoutMetrics[].alertRate.pointEstimate
holdoutMetrics[].alertRate.reasonCode
holdoutMetrics[].alertRate.upper
holdoutMetrics[].alertRate.validReplicates
holdoutMetrics[].baseDefectRate.lower
holdoutMetrics[].baseDefectRate.pointEstimate
holdoutMetrics[].baseDefectRate.reasonCode
holdoutMetrics[].baseDefectRate.upper
holdoutMetrics[].baseDefectRate.validReplicates
holdoutMetrics[].falseAlertsPer100.lower
holdoutMetrics[].falseAlertsPer100.pointEstimate
holdoutMetrics[].falseAlertsPer100.reasonCode
holdoutMetrics[].falseAlertsPer100.upper
holdoutMetrics[].falseAlertsPer100.validReplicates
holdoutMetrics[].falseNegative
holdoutMetrics[].falsePositive
holdoutMetrics[].lift.lower
holdoutMetrics[].lift.pointEstimate
holdoutMetrics[].lift.reasonCode
holdoutMetrics[].lift.upper
holdoutMetrics[].lift.validReplicates
holdoutMetrics[].precision.lower
holdoutMetrics[].precision.pointEstimate
holdoutMetrics[].precision.reasonCode
holdoutMetrics[].precision.upper
holdoutMetrics[].precision.validReplicates
holdoutMetrics[].recall.lower
holdoutMetrics[].recall.pointEstimate
holdoutMetrics[].recall.reasonCode
holdoutMetrics[].recall.upper
holdoutMetrics[].recall.validReplicates
holdoutMetrics[].total
holdoutMetrics[].trueNegative
holdoutMetrics[].truePositive
innerSplitDate
labelCensoringCounts.AP_UNLINKED
labelCensoringCounts.LABEL_NOT_YET_AVAILABLE
quarantineCounts.DUPLICATE_AP_KEY
quarantineCounts.UNLINKED_AP
schemaVersion
sourceColumnProfiles[].column
sourceColumnProfiles[].dataType
sourceColumnProfiles[].levels
sourceColumnProfiles[].levels[].count
sourceColumnProfiles[].levels[].value
sourceColumnProfiles[].missing
sourceColumnProfiles[].numeric
sourceColumnProfiles[].numeric.median
sourceColumnProfiles[].numeric.p05
sourceColumnProfiles[].numeric.p95
sourceColumnProfiles[].sourceRole
sourceColumnProfiles[].total
sourceColumnProfiles[].unique
splitCounts.confirmation.dateFrom
splitCounts.confirmation.dateTo
splitCounts.confirmation.defects
splitCounts.confirmation.nonDefects
splitCounts.confirmation.total
splitCounts.confirmation.unknownOrCensored
splitCounts.discovery.dateFrom
splitCounts.discovery.dateTo
splitCounts.discovery.defects
splitCounts.discovery.nonDefects
splitCounts.discovery.total
splitCounts.discovery.unknownOrCensored
splitCounts.holdout.dateFrom
splitCounts.holdout.dateTo
splitCounts.holdout.defects
splitCounts.holdout.nonDefects
splitCounts.holdout.total
splitCounts.holdout.unknownOrCensored
splitCounts.reference.dateFrom
splitCounts.reference.dateTo
splitCounts.reference.defects
splitCounts.reference.nonDefects
splitCounts.reference.total
splitCounts.reference.unknownOrCensored
""".strip().splitlines()),
}


def _sha256_uri(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _fraction_for_positive_bits(bits: int) -> Fraction:
    exponent_bits = (bits >> 52) & 0x7FF
    fraction_bits = bits & ((1 << 52) - 1)
    if exponent_bits == 0:
        significand = fraction_bits
        exponent = -1074
    else:
        significand = (1 << 52) | fraction_bits
        exponent = exponent_bits - 1023 - 52
    if exponent >= 0:
        return Fraction(significand << exponent, 1)
    return Fraction(significand, 1 << -exponent)


def _integer_ceiling(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def _decimal_spellings(coefficient: int, exponent: int) -> tuple[str, ...]:
    digits = str(coefficient)
    decimal_position = len(digits) + exponent
    if decimal_position <= 0:
        fixed = "0." + "0" * -decimal_position + digits
    elif decimal_position >= len(digits):
        fixed = digits + "0" * (decimal_position - len(digits))
    else:
        fixed = digits[:decimal_position] + "." + digits[decimal_position:]
    scientific_exponent = exponent + len(digits) - 1
    mantissa = digits if len(digits) == 1 else digits[0] + "." + digits[1:]
    scientific = mantissa + "e" + str(scientific_exponent)
    coefficient_exponent = digits + "e" + str(exponent)
    return fixed, scientific, coefficient_exponent


@lru_cache(maxsize=None)
def _positive_binary64_spelling(bits: int) -> str:
    value = _fraction_for_positive_bits(bits)
    previous = Fraction(0, 1) if bits == 1 else _fraction_for_positive_bits(bits - 1)
    following = Fraction(1 << 1024, 1) if bits == 0x7FEFFFFFFFFFFFFF else _fraction_for_positive_bits(bits + 1)
    lower = (previous + value) / 2
    upper = (value + following) / 2
    even = (_fraction_significand(bits) & 1) == 0
    candidates: set[str] = set()
    for digits_count in range(1, 19):
        coefficient_floor = 1 if digits_count == 1 else 10 ** (digits_count - 1)
        coefficient_ceiling = 10**digits_count - 1
        for decimal_exponent in range(-400, 401):
            scale = (
                Fraction(10**decimal_exponent, 1)
                if decimal_exponent >= 0
                else Fraction(1, 10 ** -decimal_exponent)
            )
            scaled_lower = lower / scale
            scaled_upper = upper / scale
            first = _integer_ceiling(scaled_lower)
            if not even and Fraction(first, 1) == scaled_lower:
                first += 1
            last = scaled_upper.numerator // scaled_upper.denominator
            if not even and Fraction(last, 1) == scaled_upper:
                last -= 1
            first = max(first, coefficient_floor)
            last = min(last, coefficient_ceiling)
            if first > last:
                continue
            for coefficient in range(first, last + 1):
                if coefficient % 10 == 0:
                    continue
                candidates.update(_decimal_spellings(coefficient, decimal_exponent))
        if candidates:
            shortest = min(len(candidate.encode("utf-8")) for candidate in candidates)
            return min(
                candidate
                for candidate in candidates
                if len(candidate.encode("utf-8")) == shortest
            )
    raise AssertionError("no finite binary64 decimal spelling")


def _fraction_significand(bits: int) -> int:
    exponent_bits = (bits >> 52) & 0x7FF
    fraction_bits = bits & ((1 << 52) - 1)
    return fraction_bits if exponent_bits == 0 else (1 << 52) | fraction_bits


def _float_spelling(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("golden numeric values must be finite")
    bits = struct.unpack(">Q", struct.pack(">d", value))[0]
    absolute_bits = bits & ((1 << 63) - 1)
    if absolute_bits == 0:
        return "0"
    prefix = "-" if bits >> 63 else ""
    return prefix + _positive_binary64_spelling(absolute_bits)


def _json_text(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        return _float_spelling(value)
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if type(value) in (list, tuple):
        return "[" + ",".join(_json_text(item) for item in value) + "]"
    if type(value) is dict:
        if not all(type(key) is str for key in value):
            raise TypeError("canonical object keys must be strings")
        return "{" + ",".join(
            _json_text(key) + ":" + _json_text(value[key])
            for key in sorted(value)
        ) + "}"
    raise TypeError("unsupported canonical JSON value")


def _json_bytes(value: object) -> bytes:
    return (_json_text(value) + "\n").encode("utf-8")


def _parse_number(text: str) -> int | float | None:
    stripped = text.strip()
    if stripped == "":
        return None
    if any(marker in stripped.lower() for marker in (".", "e")):
        value = float(stripped)
        if not math.isfinite(value):
            raise ValueError("source numeric value must be finite")
        return value
    return int(stripped)


def _read_table(root: Path, role: str) -> list[dict[str, str]]:
    payload = (root / "golden-source" / _SOURCE_NAMES[role]).read_bytes()
    text = payload.decode("cp949")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    rows: list[dict[str, str]] = []
    for record_number, row in enumerate(reader, start=2):
        normalized = {str(key): str(value).strip() for key, value in row.items()}
        normalized["_record_number"] = str(record_number)
        rows.append(normalized)
    return rows


def _digest_id(namespace: str, value: dict[str, object]) -> str:
    return _sha256_uri(namespace.encode("utf-8") + b"\n" + _json_bytes(value))


def _type1(values: list[int | float], probability: Fraction) -> int | float:
    ordered = sorted(values)
    rank = _integer_ceiling(Fraction(len(ordered), 1) * probability)
    return ordered[max(1, rank) - 1]


def _quartile_cuts(values: list[int | float]) -> tuple[int | float, ...]:
    proposed = (
        _type1(values, Fraction(1, 4)),
        _type1(values, Fraction(1, 2)),
        _type1(values, Fraction(3, 4)),
        max(values),
    )
    return tuple(dict.fromkeys(proposed))


def _band(value: int | float, cuts: tuple[int | float, ...]) -> str:
    for index, boundary in enumerate(cuts, start=1):
        if value <= boundary:
            return "Q" + str(index)
    raise AssertionError("band cut does not include maximum")


def _load_inputs(root: Path) -> tuple[dict[str, object], dict[str, list[dict[str, str]]]]:
    config_bytes = (root / "analysis_config.json").read_bytes()
    if hashlib.sha256(config_bytes).hexdigest() != "bb2610971dc1b3adcb4e93b9d26a50fdb4070292ffc4bf4a26ce7cdde0f5fb0b":
        raise ValueError("analysis config bytes do not match the frozen golden contract")
    config = json.loads(config_bytes)
    if (
        config.get("analysisConfigVersion") != "quality-analysis-v1"
        or config.get("labelMaturityDays") != 38
        or config.get("splits") != {"discoveryFraction": 0.7, "referenceFraction": 0.7}
        or config.get("qualityRisk", {}).get("numericBins") != 10
        or config.get("qualityRisk", {}).get("interactionBins") != 3
    ):
        raise ValueError("analysis config policy disagrees with the binding design")
    tables = {role: _read_table(root, role) for role in _SOURCE_NAMES}
    expected_digests = {
        "sm_cc": "c3bba5c7235166b6693ff657a25797933ea9d83c6392437af9b5141f4e56a68c",
        "fur_hr": "973e864ed996ca48c44a00943efc0ea51a878b94b8aac7452c7254a572fe7e67",
        "ap": "efaef9f283a28e63dff54b26bc3dd42c6a72e20f0b75d2fa13a6ab47f41405c9",
    }
    for role, expected in expected_digests.items():
        actual = hashlib.sha256((root / "golden-source" / _SOURCE_NAMES[role]).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("golden source bytes do not match the frozen input")
    return config, tables


def _derive_materials(
    config: dict[str, object], tables: dict[str, list[dict[str, str]]]
) -> tuple[list[dict[str, object]], dict[str, int]]:
    sm_by_key = {(row["charge_id"], row["slab_no"]): row for row in tables["sm_cc"]}
    fur_by_key = {(row["charge_id"], row["slab_no"]): row for row in tables["fur_hr"]}
    ap_groups: dict[str, list[dict[str, str]]] = {}
    for row in tables["ap"]:
        ap_groups.setdefault(row["hr_coil_id"], []).append(row)
    fur_coils = {row["hr_coil_id"] for row in tables["fur_hr"]}
    quarantine = {
        "DUPLICATE_AP_KEY": sum(len(rows) for rows in ap_groups.values() if len(rows) > 1),
        "UNLINKED_AP": sum(len(rows) for key, rows in ap_groups.items() if key not in fur_coils),
    }
    fields = {str(item["field"]): item for item in config["fields"]}  # type: ignore[index]
    materials: list[dict[str, object]] = []
    for key in sorted(fur_by_key, key=lambda item: (fur_by_key[item]["hr_date"], item[0], item[1])):
        sm = sm_by_key[key]
        fur = fur_by_key[key]
        ap_rows = ap_groups.get(fur["hr_coil_id"], [])
        ap = ap_rows[0] if len(ap_rows) == 1 else None
        values: dict[str, object] = {}
        for field_name, definition in fields.items():
            source_role = definition["sourceRole"]
            source_column = definition["sourceColumn"]
            if source_role is None:
                continue
            source = sm if source_role == "sm_cc" else fur if source_role == "fur_hr" else ap
            if source is None:
                values[field_name] = None
                continue
            raw = source[str(source_column)]
            data_type = definition["dataType"]
            values[field_name] = (
                _parse_number(raw) if data_type in {"NUMBER", "HOUR"} else (raw or None)
            )
        fuel = [values["f_bfg"], values["f_cog"], values["f_ldg"]]
        if all(type(item) in (int, float) for item in fuel) and sum(fuel) != 0:
            total = float(sum(fuel))
            values["f_bfg_ratio"] = float(values["f_bfg"]) / total
            values["f_cog_ratio"] = float(values["f_cog"]) / total
            values["f_ldg_ratio"] = float(values["f_ldg"]) / total
        else:
            values["f_bfg_ratio"] = values["f_cog_ratio"] = values["f_ldg_ratio"] = None
        material_key = _digest_id(
            "sfep-material-key/v1", {"chargeId": key[0], "slabNo": key[1]}
        )
        materials.append({
            "material_key": material_key,
            "charge_id": key[0],
            "slab_no": key[1],
            "hr_coil_id": fur["hr_coil_id"],
            "values": values,
            "sm": sm,
            "fur": fur,
            "ap": ap,
        })
    return materials, quarantine


def _split_materials(materials: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    reference = [item for item in materials if date.fromisoformat(str(item["values"]["hr_date"])) <= _AS_OF]  # type: ignore[index]
    holdout = [item for item in materials if date.fromisoformat(str(item["values"]["hr_date"])) > _AS_OF]  # type: ignore[index]
    maturity_cutoff = _AS_OF - timedelta(days=38)
    mature: list[dict[str, object]] = []
    for item in reference:
        values = item["values"]  # type: ignore[assignment]
        ap = item["ap"]
        if (
            ap is not None
            and date.fromisoformat(str(values["hr_date"])) <= maturity_cutoff
            and date.fromisoformat(str(values["ap_date"])) <= _AS_OF
            and values["judge"] in {"양품", "불량"}
        ):
            mature.append(item)
    discovery = [item for item in mature if date.fromisoformat(str(item["values"]["hr_date"])) <= _INNER_SPLIT]  # type: ignore[index]
    confirmation = [item for item in mature if date.fromisoformat(str(item["values"]["hr_date"])) > _INNER_SPLIT]  # type: ignore[index]
    return {
        "REFERENCE": reference,
        "DISCOVERY": discovery,
        "CONFIRMATION": confirmation,
        "HOLDOUT": holdout,
        "MATURE": mature,
    }


def _band_cuts(split: dict[str, list[dict[str, object]]]) -> dict[str, tuple[int | float, ...]]:
    reference = split["REFERENCE"]
    ap_reference = [item for item in reference if item["ap"] is not None and date.fromisoformat(str(item["values"]["ap_date"])) <= _AS_OF]  # type: ignore[index]
    sources = {
        "slab_width_band": (reference, "slab_width"),
        "hr_thick_band": (reference, "hr_thick"),
        "hr_width_band": (reference, "hr_width"),
        "ap_thick_band": (ap_reference, "ap_thick"),
        "ap_width_band": (ap_reference, "ap_width"),
    }
    result: dict[str, tuple[int | float, ...]] = {}
    for band_name, (rows, field) in sources.items():
        values = [item["values"][field] for item in rows]  # type: ignore[index]
        result[band_name] = _quartile_cuts([value for value in values if type(value) in (int, float)])
    return result


def _context_for(
    item: dict[str, object], equipment_type: str, config: dict[str, object], cuts: dict[str, tuple[int | float, ...]]
) -> dict[str, object]:
    hierarchy = next(
        entry for entry in config["rangeContextHierarchies"]  # type: ignore[index]
        if entry["equipmentType"] == equipment_type
    )
    values = item["values"]  # type: ignore[assignment]
    context: dict[str, object] = {}
    for field in hierarchy["levels"][0]:
        if field.endswith("_band"):
            base = field[:-5]
            value = values[base]
            context[field] = _band(value, cuts[field])  # type: ignore[arg-type]
        else:
            context[field] = values[field]
    return context


def _criteria_projection(
    config: dict[str, object], split: dict[str, list[dict[str, object]]]
) -> bytes:
    definitions = [
        item for item in config["fields"]  # type: ignore[index]
        if item["featureRole"] in _RANGE_ROLES and item["dataType"] == "NUMBER"
    ]
    quality_definitions = [
        item for item in config["fields"]  # type: ignore[index]
        if item["featureRole"] in _QUALITY_ROLES
    ]
    cuts = _band_cuts(split)
    lines: list[tuple[tuple[object, ...], bytes]] = []
    for item in split["REFERENCE"]:
        values = item["values"]  # type: ignore[assignment]
        hr_date = str(values["hr_date"])
        for definition in definitions:
            field = str(definition["field"])
            event_date_field = (
                "cast_date" if definition["equipmentType"] == "SM_CC"
                else "ap_date" if definition["equipmentType"] == "AP"
                else "f_ext_date"
            )
            event_date = values.get(event_date_field)
            value = values.get(field)
            if event_date is None or date.fromisoformat(str(event_date)) > _AS_OF or type(value) not in (int, float):
                continue
            line = {
                "kind": "OPERATING_RANGE_INPUT",
                "hrDate": hr_date,
                "chargeId": item["charge_id"],
                "slabNo": item["slab_no"],
                "hrCoilId": item["hr_coil_id"],
                "firstAvailableStage": definition["firstAvailableStage"],
                "field": field,
                "context": _context_for(item, str(definition["equipmentType"]), config, cuts),
                "value": value,
            }
            sort_key = (
                hr_date, item["charge_id"], item["slab_no"], item["hr_coil_id"],
                0, _STAGE_RANK[str(definition["firstAvailableStage"])], field.encode("utf-8"),
            )
            lines.append((sort_key, _json_bytes(line)))
    for item in split["MATURE"]:
        values = item["values"]  # type: ignore[assignment]
        features = {
            str(definition["field"]): values[str(definition["field"])]
            for definition in quality_definitions
        }
        split_name = "DISCOVERY" if item in split["DISCOVERY"] else "CONFIRMATION"
        line = {
            "kind": "MATURE_QUALITY_INPUT",
            "hrDate": values["hr_date"],
            "chargeId": item["charge_id"],
            "slabNo": item["slab_no"],
            "hrCoilId": item["hr_coil_id"],
            "split": split_name,
            "features": features,
            "judge": values["judge"],
        }
        sort_key = (
            str(values["hr_date"]), item["charge_id"], item["slab_no"], item["hr_coil_id"],
            1, 0, b"",
        )
        lines.append((sort_key, _json_bytes(line)))
    return b"".join(payload for _, payload in sorted(lines, key=lambda pair: pair[0]))


def _numeric_terms(field: str, values: list[int | float], bins: int) -> list[dict[str, object]]:
    boundaries = tuple(dict.fromkeys(
        _type1(values, Fraction(index, bins)) for index in range(1, bins + 1)
    ))
    terms: list[dict[str, object]] = []
    previous: int | float | None = None
    minimum = min(values)
    for boundary in boundaries:
        terms.append({
            "field": field,
            "type": "NUMERIC_INTERVAL",
            "lower": minimum if previous is None else previous,
            "lowerInclusive": previous is None,
            "upper": boundary,
            "upperInclusive": True,
            "values": None,
        })
        previous = boundary
    return terms


def _term_matches(term: dict[str, object], value: object) -> bool:
    if term["type"] == "CATEGORY_IN":
        return value in term["values"]  # type: ignore[operator]
    if type(value) not in (int, float):
        return False
    lower = term["lower"]
    upper = term["upper"]
    lower_ok = value > lower or (term["lowerInclusive"] and value == lower)  # type: ignore[operator]
    upper_ok = value < upper or (term["upperInclusive"] and value == upper)  # type: ignore[operator]
    return bool(lower_ok and upper_ok)


def _wilson(defects: int, support: int, z: float) -> tuple[float | None, float | None]:
    if support == 0:
        return None, None
    rate = defects / support
    denominator = 1.0 + z * z / support
    center = (rate + z * z / (2.0 * support)) / denominator
    margin = z * math.sqrt(
        rate * (1.0 - rate) / support + z * z / (4.0 * support * support)
    ) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _metric(
    rows: list[dict[str, object]], terms: tuple[dict[str, object], ...], z: float, *, discovery: bool
) -> dict[str, object]:
    selected = [
        item for item in rows
        if all(_term_matches(term, item["values"].get(str(term["field"]))) for term in terms)  # type: ignore[union-attr]
    ]
    informative = 0 < len(selected) < len(rows)
    if not informative:
        selected = []
    support = len(selected)
    defects = sum(item["values"].get("judge") == "불량" for item in selected)  # type: ignore[union-attr]
    crude = defects / support if support else None
    lower, upper = _wilson(defects, support, z)
    return {
        "support": support,
        "defects": defects,
        "crudeRate": crude,
        "crudeRateCiLower": lower,
        "crudeRateCiUpper": upper,
        "adjustedRate": None,
        "comparatorAdjustedRate": None,
        "riskDifference": None,
        "relativeRisk": None,
        "relativeRiskCiLower": None,
        "relativeRiskCiUpper": None,
        "pValue": 1.0 if discovery else None,
        "qValue": 1.0 if discovery else None,
        "reasonCode": "LOW_SUPPORT" if informative else "NO_INFORMATIVE_STRATA",
    }


def _dropped_adjustment_fields(
    field_names: tuple[str, ...], equipment_type: str, analysis_family: str,
    definitions: dict[str, dict[str, object]],
) -> list[str]:
    dropped = {name for field in field_names for name in (field, field + "_band")}
    candidate_stage = str(definitions[field_names[0]]["firstAvailableStage"])
    for field, definition in definitions.items():
        if (
            definition["dataType"] == "NUMBER"
            and definition["featureRole"] == "DIRECT_OPERATION"
            and definition["firstAvailableStage"] == candidate_stage
        ):
            dropped.add(field)
    changed = True
    while changed:
        changed = False
        for field, definition in definitions.items():
            dependencies = {str(value) for value in definition["dependencies"]}
            if dependencies.intersection(dropped) and field not in dropped:
                dropped.add(field)
                changed = True
    if equipment_type == "AP" and analysis_family != "INTERACTION":
        dropped.update(("ap_thick_band", "ap_width_band"))
    return sorted(dropped, key=lambda item: item.encode("utf-8"))


def _candidate(
    config: dict[str, object], definitions: dict[str, dict[str, object]],
    analysis_family: str, field_names: tuple[str, ...], terms: tuple[dict[str, object], ...],
) -> dict[str, object]:
    first = definitions[field_names[0]]
    equipment_type = str(first["equipmentType"])
    hierarchy = next(
        item for item in config["riskAdjustmentHierarchies"]  # type: ignore[index]
        if item["equipmentType"] == equipment_type
    )
    equipment_specific = (
        analysis_family == "CATEGORICAL"
        and field_names[0] in {"sm_plant", "furnace_no", "ap_plant"}
    )
    equipment_id = str(terms[0]["values"][0]) if equipment_specific else "ALL"  # type: ignore[index]
    identity = {
        "analysisFamily": analysis_family,
        "fieldNames": list(field_names),
        "predicate": {"allOf": list(terms)},
        "firstAvailableStage": first["firstAvailableStage"],
        "equipmentType": equipment_type,
        "applicationScope": "EQUIPMENT_SPECIFIC" if equipment_specific else "PROCESS_GLOBAL",
        "equipmentId": equipment_id,
        "applicationContext": {},
        "adjustmentLevel": len(hierarchy["levels"]) - 1,
        "adjustmentFieldsDropped": _dropped_adjustment_fields(
            field_names, equipment_type, analysis_family, definitions
        ),
        "adjustmentKind": "UNADJUSTED_FALLBACK",
    }
    return {"identity": identity, "definitions": first}


def _quality_rules(
    config: dict[str, object], split: dict[str, list[dict[str, object]]]
) -> tuple[list[dict[str, object]], dict[str, dict[str, list[str]]]]:
    definitions = {
        str(item["field"]): item for item in config["fields"]  # type: ignore[index]
        if item["featureRole"] in _QUALITY_ROLES
    }
    discovery = split["DISCOVERY"]
    candidates: list[dict[str, object]] = []
    numeric_bins = int(config["qualityRisk"]["numericBins"])  # type: ignore[index]
    interaction_bins = int(config["qualityRisk"]["interactionBins"])  # type: ignore[index]
    for field, definition in definitions.items():
        values = [item["values"].get(field) for item in discovery]  # type: ignore[union-attr]
        if definition["dataType"] == "NUMBER":
            finite = [value for value in values if type(value) in (int, float) and math.isfinite(float(value))]
            for term in _numeric_terms(field, finite, numeric_bins):
                candidates.append(_candidate(config, definitions, "NUMERIC", (field,), (term,)))
        elif definition["dataType"] == "STRING":
            categories = sorted(
                {value for value in values if type(value) is str and value != ""},
                key=lambda item: item.encode("utf-8"),
            )
            for category in categories:
                term = {
                    "field": field,
                    "type": "CATEGORY_IN",
                    "lower": None,
                    "lowerInclusive": None,
                    "upper": None,
                    "upperInclusive": None,
                    "values": [category],
                }
                candidates.append(_candidate(config, definitions, "CATEGORICAL", (field,), (term,)))
    for configured_pair in config["fixedInteractions"]:  # type: ignore[index]
        field_names = tuple(str(field) for field in configured_pair)
        terms_by_field: list[list[dict[str, object]]] = []
        for field in field_names:
            values = [item["values"].get(field) for item in discovery]  # type: ignore[union-attr]
            finite = [value for value in values if type(value) in (int, float) and math.isfinite(float(value))]
            terms_by_field.append(_numeric_terms(field, finite, interaction_bins))
        for first_term in terms_by_field[0]:
            for second_term in terms_by_field[1]:
                terms = (first_term, second_term)
                if any(
                    all(_term_matches(term, item["values"].get(str(term["field"]))) for term in terms)  # type: ignore[union-attr]
                    for item in discovery
                ):
                    candidates.append(_candidate(config, definitions, "INTERACTION", field_names, terms))
    rules: list[dict[str, object]] = []
    memberships: dict[str, dict[str, list[str]]] = {}
    z = float(config["wilsonZ"])
    for candidate in candidates:
        identity = candidate["identity"]
        rule_id = _sha256_uri(_json_bytes(identity))
        terms = tuple(identity["predicate"]["allOf"])  # type: ignore[index]
        discovery_metric = _metric(split["DISCOVERY"], terms, z, discovery=True)
        confirmation_metric = _metric(split["CONFIRMATION"], terms, z, discovery=False)
        definition = candidate["definitions"]
        rule = {
            **identity,
            "ruleId": rule_id,
            "evidenceFamily": definition["evidenceFamily"],
            "grade": "INSUFFICIENT_EVIDENCE",
            "earlyWarningEligible": definition["firstAvailableStage"] != "AP_RECORDED_WITH_RESULT",
            "discovery": discovery_metric,
            "confirmation": confirmation_metric,
            "displayMergeRuleIds": [],
        }
        rules.append(rule)
        memberships[rule_id] = {
            split_name: sorted(
                [
                    str(item["material_key"])
                    for item in split[split_name]
                    if all(
                        _term_matches(term, item["values"].get(str(term["field"])))  # type: ignore[union-attr]
                        for term in terms
                    )
                ],
                key=lambda item: item.encode("utf-8"),
            )
            for split_name in ("DISCOVERY", "CONFIRMATION")
        }
    family_rank = {"NUMERIC": 0, "CATEGORICAL": 1, "INTERACTION": 2}
    interaction_rank = {
        tuple(str(field) for field in pair): index
        for index, pair in enumerate(config["fixedInteractions"])  # type: ignore[index]
    }
    candidate_order = {
        str(rule["ruleId"]): index for index, rule in enumerate(rules)
    }
    def rule_sort_key(rule: dict[str, object]) -> tuple[object, ...]:
        family = str(rule["analysisFamily"])
        fields = tuple(str(field) for field in rule["fieldNames"])
        if family == "INTERACTION":
            definition_key: tuple[object, ...] = (interaction_rank[fields],)
        else:
            definition = definitions[fields[0]]
            definition_key = (
                _STAGE_RANK[str(definition["firstAvailableStage"])],
                fields[0].encode("utf-8"),
            )
        return (
            family_rank[family], definition_key, candidate_order[str(rule["ruleId"])],
            str(rule["ruleId"]).encode("utf-8"),
        )
    rules.sort(key=rule_sort_key)
    numeric_runs: dict[tuple[str, ...], list[dict[str, object]]] = {}
    for rule in rules:
        if rule["analysisFamily"] == "NUMERIC":
            numeric_runs.setdefault(tuple(str(value) for value in rule["fieldNames"]), []).append(rule)
    for run in numeric_runs.values():
        constituent_ids = sorted(
            (str(rule["ruleId"]) for rule in run), key=lambda item: item.encode("utf-8")
        )
        for rule in run:
            rule["displayMergeRuleIds"] = constituent_ids
    return rules, memberships


def _event_values(
    item: dict[str, object], definitions: list[dict[str, object]], stage: str
) -> dict[str, object]:
    values = item["values"]  # type: ignore[assignment]
    excluded_identifiers = {"charge_id", "slab_no", "hr_coil_id", "ap_prod_id"}
    return {
        str(definition["field"]): values[str(definition["field"])]
        for definition in definitions
        if definition["firstAvailableStage"] == stage
        and definition["field"] not in excluded_identifiers
    }


def _replay_events(
    config: dict[str, object], materials: list[dict[str, object]]
) -> bytes:
    definitions = list(config["fields"])  # type: ignore[arg-type]
    events: list[dict[str, object]] = []
    for item in materials:
        values = item["values"]  # type: ignore[assignment]
        material_key = str(item["material_key"])
        stage_descriptors = (
            ("CAST_RECORDED", "CAST_DAY", str(values["cast_date"]), None, "DAY", "SM_CC", str(values["sm_plant"])),
            ("FURNACE_CHARGED", "FURNACE_HOUR", str(values["f_ext_date"]), int(values["f_ext_time"]), "HOUR_BUCKET", "FURNACE", str(values["furnace_no"])),
            ("PREHEAT_COMPLETE", "FURNACE_HOUR", str(values["f_ext_date"]), int(values["f_ext_time"]), "HOUR_BUCKET", "FURNACE", str(values["furnace_no"])),
            ("HEAT_COMPLETE", "FURNACE_HOUR", str(values["f_ext_date"]), int(values["f_ext_time"]), "HOUR_BUCKET", "FURNACE", str(values["furnace_no"])),
            ("SOAK_COMPLETE", "FURNACE_HOUR", str(values["f_ext_date"]), int(values["f_ext_time"]), "HOUR_BUCKET", "FURNACE", str(values["furnace_no"])),
            ("FURNACE_EXTRACTED", "FURNACE_HOUR", str(values["f_ext_date"]), int(values["f_ext_time"]), "HOUR_BUCKET", "FURNACE", str(values["furnace_no"])),
            ("RM4_RECORDED", "FURNACE_HOUR", str(values["f_ext_date"]), int(values["f_ext_time"]), "SEQUENCE_ONLY", "RM4", "RM4_PROCESS"),
        )
        descriptors = list(stage_descriptors)
        if item["ap"] is not None:
            descriptors.append((
                "AP_RECORDED_WITH_RESULT", "AP_DAY", str(values["ap_date"]), None,
                "DAY", "AP", str(values["ap_plant"]),
            ))
        for stage, batch_kind, replay_date, replay_hour, precision, equipment_type, equipment_id in descriptors:
            batch_object: dict[str, object] = {
                "batchKind": batch_kind,
                "replayDate": replay_date,
            }
            if batch_kind == "FURNACE_HOUR":
                batch_object["replayHour"] = replay_hour
            batch_id = _digest_id("sfep-batch-id/v1", batch_object)
            equipment_batch_id = None
            if equipment_type == "FURNACE":
                equipment_batch_id = _digest_id(
                    "sfep-equipment-batch-id/v1",
                    {"batchId": batch_id, "equipmentId": equipment_id, "equipmentType": "FURNACE"},
                )
            event_id = _digest_id(
                "sfep-event-id/v1",
                {
                    "batchId": batch_id,
                    "batchStep": stage,
                    "equipmentBatchId": equipment_batch_id,
                    "equipmentId": equipment_id,
                    "equipmentType": equipment_type,
                    "materialKey": material_key,
                },
            )
            events.append({
                "schema_version": "sfep-replay-events/v1",
                "bundle_id": _BUNDLE_TOKEN,
                "criteria_id": _CRITERIA_TOKEN,
                "event_id": event_id,
                "replay_date": replay_date,
                "replay_hour": replay_hour,
                "batch_kind": batch_kind,
                "batch_id": batch_id,
                "equipment_batch_id": equipment_batch_id,
                "batch_step": stage,
                "time_precision": precision,
                "material_key": material_key,
                "equipment_type": equipment_type,
                "equipment_id": equipment_id,
                "charge_id": item["charge_id"],
                "slab_no": item["slab_no"],
                "hr_coil_id": item["hr_coil_id"] if _STAGE_RANK[stage] >= _STAGE_RANK["RM4_RECORDED"] else None,
                "ap_prod_id": values["ap_prod_id"] if stage == "AP_RECORDED_WITH_RESULT" else None,
                "values_json": _json_text(_event_values(item, definitions, stage)),
            })

    batch_rank = {"CAST_DAY": 0, "FURNACE_HOUR": 1, "AP_DAY": 2}
    def event_sort_key(event: dict[str, object]) -> tuple[object, ...]:
        if event["equipment_type"] == "FURNACE":
            equipment_sort: tuple[object, ...] = (0, int(str(event["equipment_id"])))
        else:
            equipment_sort = (1, str(event["equipment_id"]).encode("utf-8"))
        return (
            event["replay_date"],
            batch_rank[str(event["batch_kind"])],
            -1 if event["replay_hour"] is None else event["replay_hour"],
            str(event["batch_id"]).encode("utf-8"),
            _STAGE_RANK[str(event["batch_step"])],
            equipment_sort,
            str(event["material_key"]).encode("utf-8"),
            str(event["event_id"]).encode("utf-8"),
        )

    events.sort(key=event_sort_key)
    header = (
        "schema_version", "bundle_id", "criteria_id", "event_id", "replay_date",
        "replay_hour", "batch_kind", "batch_id", "equipment_batch_id", "batch_step",
        "time_precision", "material_key", "equipment_type", "equipment_id", "charge_id",
        "slab_no", "hr_coil_id", "ap_prod_id", "values_json",
    )
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    for event in events:
        writer.writerow(["" if event[name] is None else event[name] for name in header])
    return stream.getvalue().encode("utf-8")


def _source_data_types(
    config: dict[str, object], tables: dict[str, list[dict[str, str]]]
) -> dict[tuple[str, str], str]:
    identifiers = {"charge_id", "slab_no", "hr_coil_id", "ap_prod_id"}
    result: dict[tuple[str, str], str] = {}
    for role, rows in tables.items():
        for column in rows[0]:
            if column == "_record_number":
                continue
            result[(role, column)] = "IDENTIFIER" if column in identifiers else "CATEGORY"
    for definition in config["fields"]:  # type: ignore[index]
        role = definition["sourceRole"]
        column = definition["sourceColumn"]
        if role is None or column is None or column in identifiers:
            continue
        data_type = str(definition["dataType"])
        result[(str(role), str(column))] = (
            "NUMBER" if data_type in {"NUMBER", "HOUR"}
            else "DATE" if data_type == "DATE"
            else "CATEGORY"
        )
    for column in ("f_bfg_per", "f_cog_per", "f_ldg_per"):
        result[("fur_hr", column)] = "NUMBER"
    return result


def _level_counts(values: list[object]) -> list[dict[str, object]]:
    counts: dict[object, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return [
        {"count": counts[value], "value": value}
        for value in sorted(counts, key=lambda item: _json_text(item).encode("utf-8"))
    ]


def _source_profiles(
    config: dict[str, object], tables: dict[str, list[dict[str, str]]]
) -> list[dict[str, object]]:
    data_types = _source_data_types(config, tables)
    profiles: list[dict[str, object]] = []
    for role in ("sm_cc", "fur_hr", "ap"):
        rows = tables[role]
        for column in (key for key in rows[0] if key != "_record_number"):
            data_type = data_types[(role, column)]
            raw_values = [row[column] for row in rows]
            present = [value for value in raw_values if value != ""]
            numeric = None
            levels = None
            if data_type == "NUMBER":
                values = [_parse_number(value) for value in present]
                finite = [value for value in values if type(value) in (int, float)]
                numeric = {
                    "median": _type1(finite, Fraction(1, 2)),
                    "p05": _type1(finite, Fraction(5, 100)),
                    "p95": _type1(finite, Fraction(95, 100)),
                }
            else:
                levels = _level_counts(present)
            profiles.append({
                "column": column,
                "dataType": data_type,
                "levels": levels,
                "missing": len(raw_values) - len(present),
                "numeric": numeric,
                "sourceRole": role,
                "total": len(raw_values),
                "unique": len(set(present)),
            })
    return profiles


def _distribution(
    rows: list[dict[str, object]], field: str, data_type: str, *, reference_ap: bool = False,
) -> dict[str, object]:
    raw = [
        None
        if reference_ap and (
            item["values"].get("ap_date") is None  # type: ignore[union-attr]
            or date.fromisoformat(str(item["values"]["ap_date"])) > _AS_OF  # type: ignore[index]
        )
        else item["values"].get(field)  # type: ignore[union-attr]
        for item in rows
    ]
    present = [value for value in raw if value is not None]
    result: dict[str, object] = {
        "levels": None,
        "median": None,
        "missingRate": (len(raw) - len(present)) / len(raw) if raw else 0.0,
        "p05": None,
        "p95": None,
        "support": len(present),
    }
    if data_type == "NUMBER":
        finite = [value for value in present if type(value) in (int, float)]
        if finite:
            result.update({
                "median": _type1(finite, Fraction(1, 2)),
                "p05": _type1(finite, Fraction(5, 100)),
                "p95": _type1(finite, Fraction(95, 100)),
            })
    else:
        result["levels"] = _level_counts(present)
    return result


def _drift_metrics(
    config: dict[str, object], split: dict[str, list[dict[str, object]]]
) -> list[dict[str, object]]:
    definitions = [
        definition for definition in config["fields"]  # type: ignore[index]
        if definition["featureRole"] in _QUALITY_ROLES
    ]
    definitions.sort(key=lambda item: (
        _STAGE_RANK[str(item["firstAvailableStage"])],
        str(item["field"]).encode("utf-8"),
    ))
    result: list[dict[str, object]] = []
    for definition in definitions:
        field = str(definition["field"])
        data_type = "NUMBER" if definition["dataType"] == "NUMBER" else "CATEGORY"
        result.append({
            "dataType": data_type,
            "field": field,
            "holdout": _distribution(split["HOLDOUT"], field, data_type),
            "reference": _distribution(
                split["REFERENCE"], field, data_type,
                reference_ap=definition["firstAvailableStage"] == "AP_RECORDED_WITH_RESULT",
            ),
        })
    return result


def _split_count(
    rows: list[dict[str, object]], label_eligible: set[str] | None = None,
) -> dict[str, object]:
    dates = [str(item["values"]["hr_date"]) for item in rows]  # type: ignore[index]
    eligible_rows = rows if label_eligible is None else [
        item for item in rows if str(item["material_key"]) in label_eligible
    ]
    defects = sum(item["values"].get("judge") == "불량" for item in eligible_rows)  # type: ignore[union-attr]
    non_defects = sum(item["values"].get("judge") == "양품" for item in eligible_rows)  # type: ignore[union-attr]
    return {
        "dateFrom": min(dates),
        "dateTo": max(dates),
        "defects": defects,
        "nonDefects": non_defects,
        "total": len(rows),
        "unknownOrCensored": len(rows) - defects - non_defects,
    }


def _holdout_rate_metric(point: float | None, reason: str) -> dict[str, object]:
    if reason == "ZERO_DENOMINATOR":
        return {
            "lower": None, "pointEstimate": None, "reasonCode": reason,
            "upper": None, "validReplicates": 0,
        }
    return {
        "lower": point, "pointEstimate": point, "reasonCode": "NONE",
        "upper": point, "validReplicates": 2000,
    }


def _holdout_profiles() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for grade in ("DANGER", "CAUTION_OR_DANGER"):
        result.append({
            "alertGrade": grade,
            "alertRate": _holdout_rate_metric(0.0, "NONE"),
            "baseDefectRate": _holdout_rate_metric(0.5, "NONE"),
            "falseAlertsPer100": _holdout_rate_metric(0.0, "NONE"),
            "falseNegative": 1,
            "falsePositive": 0,
            "lift": _holdout_rate_metric(None, "ZERO_DENOMINATOR"),
            "precision": _holdout_rate_metric(None, "ZERO_DENOMINATOR"),
            "recall": _holdout_rate_metric(0.0, "NONE"),
            "total": 2,
            "trueNegative": 1,
            "truePositive": 0,
        })
    return result


def _material_lineage(materials: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in materials:
        records = [
            {"name": _SOURCE_NAMES[role], "recordNumber": int(source["_record_number"]), "role": role}
            for role, source in (("sm_cc", item["sm"]), ("fur_hr", item["fur"]), ("ap", item["ap"]))
            if source is not None
        ]
        result.append({
            "chargeId": item["charge_id"],
            "hrCoilId": item["hr_coil_id"],
            "materialKey": item["material_key"],
            "slabNo": item["slab_no"],
            "sourceRecords": records,
        })
    return sorted(result, key=lambda item: str(item["materialKey"]).encode("utf-8"))


def _population_lineage(
    split: dict[str, list[dict[str, object]]]
) -> list[dict[str, object]]:
    return [{
        "materialKeys": sorted(
            (str(item["material_key"]) for item in split[name]),
            key=lambda item: item.encode("utf-8"),
        ),
        "populationRef": name,
        "split": name,
    } for name in ("REFERENCE", "DISCOVERY", "CONFIRMATION", "HOLDOUT")]


def _aggregate_lineage(
    rules: list[dict[str, object]], memberships: dict[str, dict[str, list[str]]],
    split: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    population_sizes = {name: len(split[name]) for name in ("DISCOVERY", "CONFIRMATION")}
    result: list[dict[str, object]] = []
    for rule in sorted(rules, key=lambda item: str(item["ruleId"]).encode("utf-8")):
        rule_id = str(rule["ruleId"])
        requires_finite = rule["analysisFamily"] != "CATEGORICAL"
        for split_name in ("CONFIRMATION", "DISCOVERY"):
            candidate = memberships[rule_id][split_name]
            inputs = candidate if 0 < len(candidate) < population_sizes[split_name] else []
            if split_name == "DISCOVERY":
                filters = ["LABEL_AVAILABLE_AND_MATURE"]
                if requires_finite:
                    filters.append("FINITE_VALUE")
                filters.extend(["PREDICATE_MATCH", "INFORMATIVE_STRATA_ONLY"])
                transformations = ["WILSON_SCORE_INTERVAL", "BENJAMINI_HOCHBERG_FDR"]
            else:
                filters = ["LABEL_AVAILABLE_AND_MATURE"]
                if requires_finite:
                    filters.append("FINITE_VALUE")
                filters.extend([
                    "PREDICATE_MATCH", "FIXED_DISCOVERY_STRATA", "INFORMATIVE_STRATA_ONLY",
                ])
                transformations = ["WILSON_SCORE_INTERVAL"]
            result.append({
                "artifactRole": "quality_risk_intervals",
                "comparatorDefinition": "FIXED_POPULATION_STRATA_MINUS_CANDIDATE",
                "filters": filters,
                "inputMaterialKeys": inputs,
                "populationRef": split_name,
                "ruleId": rule_id,
                "split": split_name,
                "transformations": transformations,
            })
    return result


def _derived_field(
    role: str, path: str, conversion: str, dependencies: list[str],
    stage: str | None = None,
) -> dict[str, object]:
    return {
        "artifactRole": role,
        "conversion": conversion,
        "dependencies": sorted(set(dependencies), key=lambda item: item.encode("utf-8")),
        "firstAvailableStage": stage,
        "outputField": path,
        "sourceColumn": None,
        "sourceRole": None,
    }


def _field_lineage(config: dict[str, object]) -> list[dict[str, object]]:
    definitions = {str(item["field"]): item for item in config["fields"]}  # type: ignore[index]
    identifier_paths = {
        "charge_id": "charge_id", "slab_no": "slab_no",
        "hr_coil_id": "hr_coil_id", "ap_prod_id": "ap_prod_id",
    }
    raw_paths = {
        identifier_paths.get(field, "values_json." + field): definition
        for field, definition in definitions.items() if definition["sourceRole"] is not None
    }
    source_terminals = {
        f'{definition["sourceRole"]}.{definition["sourceColumn"]}'
        for definition in definitions.values() if definition["sourceRole"] is not None
    } | {
        "sm_cc.slab_no", "fur_hr.charge_id", "ap.hr_coil_id",
        "fur_hr.f_bfg_per", "fur_hr.f_cog_per", "fur_hr.f_ldg_per",
    }
    numeric_source_terminals = {
        f'{definition["sourceRole"]}.{definition["sourceColumn"]}'
        for definition in definitions.values()
        if definition["sourceRole"] is not None and definition["dataType"] in {"NUMBER", "HOUR"}
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
        if definition["featureRole"] in _QUALITY_ROLES and definition["dataType"] == "NUMBER"
    }
    categorical_quality_nodes = quality_feature_nodes - numeric_quality_nodes
    early_warning_quality_nodes = {
        "replay_events." + identifier_paths.get(field, "values_json." + field)
        for field, definition in definitions.items()
        if definition["featureRole"] in _QUALITY_ROLES
        and definition["firstAvailableStage"] != "AP_RECORDED_WITH_RESULT"
    }
    rule_identity_nodes = {
        "quality_risk_intervals.rules[]." + path for path in (
            "adjustmentFieldsDropped[]", "adjustmentKind", "adjustmentLevel", "analysisFamily",
            "applicationContext", "applicationScope", "equipmentId", "equipmentType",
            "fieldNames[]", "firstAvailableStage", "predicate.allOf[].field",
            "predicate.allOf[].lower", "predicate.allOf[].lowerInclusive",
            "predicate.allOf[].type", "predicate.allOf[].upper",
            "predicate.allOf[].upperInclusive", "predicate.allOf[].values[]",
        )
    }
    rule_config_terminals = {
        "config.fields[].dataType", "config.fields[].dependencies[]", "config.fields[].equipmentType",
        "config.fields[].evidenceFamily", "config.fields[].featureRole", "config.fields[].field",
        "config.fields[].firstAvailableStage", "config.fixedInteractions[][]",
        "config.qualityRisk.interactionBins", "config.qualityRisk.numericBins",
        "config.riskAdjustmentHierarchies[].equipmentType",
        "config.riskAdjustmentHierarchies[].levels[][]",
    }
    rule_config_terminals.update({
        "config.evidenceFamilies[]", "config.fdrFamilies[]",
        "config.qualityRisk.minimumDiscoverySupport",
        "config.qualityRisk.minimumInformativeStrata",
    })
    rule_candidate_evaluation_nodes = {
        "quality_risk_intervals.rules[]." + path for path in (
            "adjustmentFieldsDropped[]", "adjustmentKind", "adjustmentLevel",
            "predicate.allOf[].field", "predicate.allOf[].lower",
            "predicate.allOf[].lowerInclusive", "predicate.allOf[].type",
            "predicate.allOf[].upper", "predicate.allOf[].upperInclusive",
            "predicate.allOf[].values[]",
        )
    }
    non_manifest_nodes = {
        role + "." + path
        for role, paths in _LINEAGE_PATHS.items() if role != "bundle_manifest"
        for path in paths
    }
    artifact_version_nodes = {
        role + ".schemaVersion" for role in (
            "analysis_config", "producer_runtime", "equipment_operating_ranges",
            "quality_risk_intervals", "analysis_summary",
        )
    } | {"replay_events.schema_version"}
    holdout_alert_dependencies = (
        early_warning_quality_nodes
        | {
            "analysis_summary.asOf",
            "analysis_summary.holdoutMetrics[].alertGrade",
            "population.HOLDOUT", "replay_events.material_key", "replay_events.batch_step",
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
    )
    holdout_confusion_dependencies = holdout_alert_dependencies | {
        "replay_events.values_json.judge",
    }
    result: list[dict[str, object]] = []
    for role, paths in _LINEAGE_PATHS.items():
        for path in paths:
            if role == "replay_events" and path in raw_paths:
                definition = raw_paths[path]
                data_type = str(definition["dataType"])
                conversion = (
                    "PARSE_FINITE_BINARY64" if data_type == "NUMBER"
                    else "PARSE_DATE" if data_type == "DATE"
                    else "PARSE_HOUR_BUCKET" if data_type == "HOUR"
                    else "COPY_SOURCE_SCALAR"
                )
                result.append({
                    "artifactRole": role,
                    "conversion": conversion,
                    "dependencies": [],
                    "firstAvailableStage": definition["firstAvailableStage"],
                    "outputField": path,
                    "sourceColumn": definition["sourceColumn"],
                    "sourceRole": definition["sourceRole"],
                })
                continue
            if role == "replay_events" and path in {
                "values_json.f_bfg_ratio", "values_json.f_cog_ratio", "values_json.f_ldg_ratio",
            }:
                result.append(_derived_field(
                    role, path, "DERIVE_FUEL_RATIO",
                    ["fur_hr.f_bfg", "fur_hr.f_cog", "fur_hr.f_ldg"],
                    "FURNACE_EXTRACTED",
                ))
                continue
            if role == "analysis_config":
                result.append(_derived_field(role, path, "COPY_CANONICAL_CONFIG", ["config." + path]))
            elif role == "producer_runtime":
                result.append(_derived_field(role, path, "COPY_VERIFIED_RUNTIME", ["runtime." + path]))
            elif role == "bundle_manifest":
                if path.startswith("identity.source."):
                    source_parts = path.split(".")
                    source_role, property_name = source_parts[2], source_parts[3]
                    conversion = "COPY_SOURCE_METADATA"
                    dependencies = [f"source.{source_role}.{property_name}"]
                elif ".schema." in path:
                    schema_role = path.split(".schema.", 1)[1].removesuffix(".sha256")
                    conversion = "COPY_DIGEST_VALUE"
                    dependencies = ["schema." + schema_role + ".sha256"]
                elif path.startswith("criteriaIdentity."):
                    criteria_name = path.removeprefix("criteriaIdentity.")
                    if criteria_name == "analysis_config_sha256":
                        conversion, dependencies = "COPY_DIGEST_VALUE", ["identity.analysis_config_sha256"]
                    elif criteria_name == "criteria_projection_sha256":
                        conversion = "COPY_DIGEST_VALUE"
                        dependencies = ["identity.criteria_projection_sha256"]
                    elif criteria_name == "producer_runtime_sha256":
                        conversion, dependencies = "COPY_DIGEST_VALUE", ["identity.producer_runtime_sha256"]
                    elif criteria_name == "as_of":
                        conversion, dependencies = "COPY_BOUNDARY_DATE", ["analysis_summary.asOf"]
                    else:
                        raise AssertionError("unexpected criteria identity lineage path")
                elif path.startswith("identity."):
                    identity_name = path.removeprefix("identity.")
                    if identity_name == "analysis_config_sha256":
                        conversion, dependencies = "COPY_DIGEST_VALUE", ["identity.analysis_config_sha256"]
                    elif identity_name == "producer_runtime_sha256":
                        conversion, dependencies = "COPY_DIGEST_VALUE", ["identity.producer_runtime_sha256"]
                    elif identity_name == "criteria_id":
                        conversion, dependencies = "COPY_IDENTITY_VALUE", ["identity.criteria_id"]
                    else:
                        raise AssertionError("unexpected manifest identity lineage path")
                elif path == "artifacts[].role":
                    conversion = "PROJECT_ARTIFACT_METADATA"
                    dependencies = [
                        "schema.analysis_config", "schema.analysis_summary",
                        "schema.equipment_operating_ranges", "schema.producer_runtime",
                        "schema.quality_risk_intervals", "schema.replay_events",
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
                        "bundle_manifest." + value for value in _LINEAGE_PATHS["bundle_manifest"]
                        if value.startswith("identity.")
                    )
                elif path == "criteriaId":
                    conversion = "COMPUTE_IDENTITY"
                    dependencies = sorted(
                        "bundle_manifest." + value for value in _LINEAGE_PATHS["bundle_manifest"]
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
            elif role == "replay_events":
                replay_dependencies = {
                    "schema_version": ["schema.replay_events"],
                    "bundle_id": ["identity.bundle_id"],
                    "criteria_id": ["identity.criteria_id"],
                    "material_key": ["replay_events.charge_id", "replay_events.slab_no"],
                    "replay_date": [
                        "ap.ap_date", "fur_hr.f_ext_date", "replay_events.batch_step",
                        "sm_cc.cast_date",
                    ],
                    "replay_hour": ["fur_hr.f_ext_time", "replay_events.batch_step"],
                    "batch_kind": ["replay_events.batch_step"],
                    "batch_id": ["replay_events.batch_kind", "replay_events.replay_date", "replay_events.replay_hour"],
                    "batch_step": [
                        "ap.ap_prod_id", "ap.hr_coil_id", "fur_hr.hr_coil_id",
                        "replay_events.material_key",
                    ],
                    "time_precision": ["replay_events.batch_step"],
                    "equipment_type": ["replay_events.batch_step"],
                    "equipment_id": ["sm_cc.sm_plant", "fur_hr.furnace_no", "ap.ap_plant", "replay_events.equipment_type"],
                    "equipment_batch_id": ["replay_events.batch_id", "replay_events.equipment_id", "replay_events.equipment_type"],
                    "event_id": [
                        "replay_events.batch_id", "replay_events.batch_step",
                        "replay_events.equipment_batch_id", "replay_events.equipment_id",
                        "replay_events.equipment_type", "replay_events.material_key",
                    ],
                }
                stage = "CAST_RECORDED" if path in {"schema_version", "bundle_id", "criteria_id", "material_key"} else None
                conversion = (
                    "COPY_SCHEMA_VERSION" if path == "schema_version"
                    else "COPY_IDENTITY_VALUE" if path in {"bundle_id", "criteria_id"}
                    else "COMPUTE_IDENTITY" if path in {
                        "material_key", "batch_id", "equipment_batch_id", "event_id",
                    }
                    else "SELECT_STAGE_VALUE" if path in {"replay_date", "replay_hour"}
                    else "CLASSIFY_REPLAY_SCHEDULE" if path in {
                        "batch_kind", "batch_step", "time_precision",
                    }
                    else "SELECT_STAGE_EQUIPMENT"
                )
                result.append(_derived_field(
                    role, path, conversion, replay_dependencies[path], stage,
                ))
            elif role == "equipment_operating_ranges":
                if path == "asOf":
                    conversion = "SELECT_TIME_BOUNDARY"
                    dependencies = [
                        "config.splits.referenceFraction", "fur_hr.charge_id",
                        "fur_hr.hr_date", "fur_hr.slab_no",
                    ]
                elif path == "criteriaId":
                    conversion, dependencies = "COPY_IDENTITY_VALUE", ["identity.criteria_id"]
                else:
                    conversion, dependencies = "COPY_SCHEMA_VERSION", ["schema.equipment_operating_ranges"]
                result.append(_derived_field(role, path, conversion, dependencies))
            elif role == "quality_risk_intervals":
                if path == "criteriaId":
                    dependencies, conversion = ["identity.criteria_id"], "COPY_IDENTITY_VALUE"
                elif path == "schemaVersion":
                    dependencies, conversion = ["schema.quality_risk_intervals"], "COPY_SCHEMA_VERSION"
                elif path == "asOf":
                    dependencies, conversion = [
                        "config.splits.referenceFraction", "fur_hr.charge_id",
                        "fur_hr.hr_date", "fur_hr.slab_no",
                    ], "SELECT_TIME_BOUNDARY"
                elif path == "rules[].ruleId":
                    dependencies, conversion = sorted(rule_identity_nodes), "COMPUTE_IDENTITY"
                elif path.startswith("rules[].discovery."):
                    dependencies, conversion = sorted(
                        quality_feature_nodes
                        | rule_candidate_evaluation_nodes
                        | {
                            "population.DISCOVERY", "replay_events.values_json.judge",
                            "config.fdrFamilies[]", "config.qualityRisk.minimumCautionDefects",
                            "config.qualityRisk.minimumDiscoverySupport", "config.wilsonZ",
                        }
                    ), "COMPUTE_QUALITY_METRIC"
                elif path.startswith("rules[].confirmation."):
                    dependencies, conversion = sorted(
                        quality_feature_nodes
                        | rule_candidate_evaluation_nodes
                        | {
                            "population.CONFIRMATION", "population.DISCOVERY",
                            "replay_events.values_json.judge",
                            "config.qualityRisk.minimumConfirmationDefects",
                            "config.qualityRisk.minimumConfirmationSupport", "config.wilsonZ",
                        }
                    ), "COMPUTE_QUALITY_METRIC"
                elif path == "rules[].grade":
                    dependencies, conversion = [
                        "quality_risk_intervals.rules[].discovery.reasonCode",
                    ], "APPLY_GRADE_POLICY"
                elif path == "rules[].earlyWarningEligible":
                    dependencies, conversion = [
                        "quality_risk_intervals.rules[].firstAvailableStage",
                    ], "DERIVE_STAGE_ELIGIBILITY"
                elif path == "rules[].displayMergeRuleIds[]":
                    dependencies, conversion = sorted({
                        "quality_risk_intervals.rules[].analysisFamily",
                        "quality_risk_intervals.rules[].applicationContext",
                        "quality_risk_intervals.rules[].applicationScope",
                        "quality_risk_intervals.rules[].equipmentId",
                        "quality_risk_intervals.rules[].equipmentType",
                        "quality_risk_intervals.rules[].fieldNames[]",
                        "quality_risk_intervals.rules[].firstAvailableStage",
                        "quality_risk_intervals.rules[].grade",
                        "quality_risk_intervals.rules[].adjustmentFieldsDropped[]",
                        "quality_risk_intervals.rules[].adjustmentKind",
                        "quality_risk_intervals.rules[].adjustmentLevel",
                        "quality_risk_intervals.rules[].predicate.allOf[].lower",
                        "quality_risk_intervals.rules[].predicate.allOf[].lowerInclusive",
                        "quality_risk_intervals.rules[].predicate.allOf[].upper",
                        "quality_risk_intervals.rules[].predicate.allOf[].upperInclusive",
                        "quality_risk_intervals.rules[].ruleId",
                    }), "MERGE_DISPLAY_INTERVALS"
                else:
                    dependencies, conversion = sorted(
                        rule_config_terminals | quality_feature_nodes | {"population.DISCOVERY"}
                    ), "DERIVE_RULE_CANDIDATE"
                result.append(_derived_field(role, path, conversion, dependencies))
            else:
                if path == "bundleId":
                    dependencies, conversion = ["identity.bundle_id"], "COPY_IDENTITY_VALUE"
                elif path == "criteriaId":
                    dependencies, conversion = ["identity.criteria_id"], "COPY_IDENTITY_VALUE"
                elif path == "schemaVersion":
                    dependencies, conversion = ["schema.analysis_summary"], "COPY_SCHEMA_VERSION"
                elif path == "asOf":
                    dependencies, conversion = [
                        "config.splits.referenceFraction", "fur_hr.charge_id",
                        "fur_hr.hr_date", "fur_hr.slab_no",
                    ], "SELECT_TIME_BOUNDARY"
                elif path == "innerSplitDate":
                    dependencies, conversion = [
                        "analysis_summary.asOf", "config.labelMaturityDays",
                        "config.splits.discoveryFraction", "population.REFERENCE",
                        "replay_events.values_json.ap_date", "replay_events.values_json.hr_date",
                        "replay_events.values_json.judge",
                    ], "SELECT_TIME_BOUNDARY"
                elif path == "evaluationMode":
                    dependencies, conversion = [
                        "policy.LOCKED_RETROSPECTIVE_HOLDOUT",
                    ], "COPY_POLICY_VALUE"
                elif path.startswith("dateRange"):
                    dependencies, conversion = ["replay_events.replay_date"], "COMPUTE_DATE_RANGE"
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
                    result.append(_derived_field(
                        role, path, "PROFILE_SOURCE_COLUMN", dependencies,
                    ))
                    continue
                elif path.startswith("driftMetrics"):
                    if path == "driftMetrics[].field":
                        dependencies = [
                            "config.fields[].featureRole", "config.fields[].field",
                            "config.fields[].firstAvailableStage",
                        ]
                        conversion = "COPY_FIELD_METADATA"
                    elif path == "driftMetrics[].dataType":
                        dependencies = ["config.fields[].dataType", "config.fields[].featureRole"]
                        conversion = "COPY_FIELD_METADATA"
                    elif path.endswith(".levels"):
                        dependencies = ["analysis_summary.driftMetrics[].dataType"]
                        conversion = "COPY_FIELD_METADATA"
                    else:
                        population = "population.REFERENCE" if ".reference." in path else "population.HOLDOUT"
                        stage_dependencies = {
                            "analysis_summary.asOf", "replay_events.values_json.ap_date",
                        } if population == "population.REFERENCE" else set()
                        if any(path.endswith("." + name) for name in ("p05", "median", "p95")):
                            dependencies = sorted(
                                numeric_quality_nodes | stage_dependencies | {population, "analysis_summary.driftMetrics[].dataType"}
                            )
                        elif ".levels[]." in path:
                            dependencies = sorted(categorical_quality_nodes | stage_dependencies | {population})
                        else:
                            dependencies = sorted(quality_feature_nodes | stage_dependencies | {population})
                        conversion = "COMPARE_DISTRIBUTIONS"
                    result.append(_derived_field(
                        role, path, conversion, dependencies,
                    ))
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
                        dependencies = ["population.HOLDOUT", "replay_events.values_json.judge"]
                    elif suffix in {
                        "truePositive", "falsePositive", "trueNegative", "falseNegative",
                    }:
                        conversion = "COUNT_PARTITION"
                        dependencies = sorted(holdout_confusion_dependencies)
                    else:
                        metric_name, leaf = suffix.split(".", 1)
                        prefix = "analysis_summary.holdoutMetrics[]."
                        metric_inputs = {
                            "alertRate": {prefix + "truePositive", prefix + "falsePositive", prefix + "total"},
                            "precision": {prefix + "truePositive", prefix + "falsePositive"},
                            "recall": {prefix + "truePositive", prefix + "falseNegative"},
                            "lift": {prefix + "precision.pointEstimate", prefix + "baseDefectRate.pointEstimate"},
                            "falseAlertsPer100": {prefix + "falsePositive", prefix + "total"},
                        }
                        bootstrap_dependencies = {
                            "config.bootstrap.replicates", "identity.criteria_id",
                            "replay_events.charge_id",
                        }
                        if leaf != "validReplicates":
                            bootstrap_dependencies.add("config.bootstrap.minimumValidReplicates")
                        if metric_name == "baseDefectRate":
                            dependencies = {
                                "population.HOLDOUT", "replay_events.values_json.judge",
                            }
                            if leaf != "pointEstimate":
                                dependencies.update(bootstrap_dependencies)
                        else:
                            dependencies = set(metric_inputs[metric_name])
                            if leaf != "pointEstimate" and metric_name in {
                                "alertRate", "recall", "falseAlertsPer100",
                            }:
                                matching_dependencies = (
                                    holdout_alert_dependencies
                                    if metric_name == "alertRate" else holdout_confusion_dependencies
                                )
                                if metric_name == "alertRate":
                                    matching_dependencies = matching_dependencies | {
                                        "replay_events.values_json.judge",
                                    }
                                dependencies.update(matching_dependencies | bootstrap_dependencies)
                        dependencies = sorted(dependencies)
                        conversion = "COMPUTE_HOLDOUT_METRIC"
                    result.append(_derived_field(
                        role, path, conversion, dependencies,
                    ))
                    continue
                elif path.startswith("splitCounts.reference"):
                    if path.endswith(".total"):
                        dependencies = ["population.REFERENCE"]
                    elif path.endswith((".dateFrom", ".dateTo")):
                        dependencies = ["population.REFERENCE", "replay_events.values_json.hr_date"]
                    elif path.endswith(".unknownOrCensored"):
                        dependencies = [
                            "analysis_summary.splitCounts.reference.defects",
                            "analysis_summary.splitCounts.reference.nonDefects",
                            "analysis_summary.splitCounts.reference.total",
                        ]
                    else:
                        dependencies = [
                            "analysis_summary.asOf", "config.labelMaturityDays", "population.REFERENCE",
                            "replay_events.values_json.ap_date", "replay_events.values_json.hr_date",
                            "replay_events.values_json.judge",
                        ]
                    result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                    continue
                elif path.startswith("splitCounts.discovery"):
                    if path.endswith(".total"):
                        dependencies = ["population.DISCOVERY"]
                    elif path.endswith((".dateFrom", ".dateTo")):
                        dependencies = ["population.DISCOVERY", "replay_events.values_json.hr_date"]
                    elif path.endswith(".unknownOrCensored"):
                        dependencies = [
                            "analysis_summary.splitCounts.discovery.defects",
                            "analysis_summary.splitCounts.discovery.nonDefects",
                            "analysis_summary.splitCounts.discovery.total",
                        ]
                    else:
                        dependencies = ["population.DISCOVERY", "replay_events.values_json.judge"]
                    result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                    continue
                elif path.startswith("splitCounts.confirmation"):
                    if path.endswith(".total"):
                        dependencies = ["population.CONFIRMATION"]
                    elif path.endswith((".dateFrom", ".dateTo")):
                        dependencies = ["population.CONFIRMATION", "replay_events.values_json.hr_date"]
                    elif path.endswith(".unknownOrCensored"):
                        dependencies = [
                            "analysis_summary.splitCounts.confirmation.defects",
                            "analysis_summary.splitCounts.confirmation.nonDefects",
                            "analysis_summary.splitCounts.confirmation.total",
                        ]
                    else:
                        dependencies = ["population.CONFIRMATION", "replay_events.values_json.judge"]
                    result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                    continue
                elif path.startswith("splitCounts.holdout"):
                    if path.endswith(".total"):
                        dependencies = ["population.HOLDOUT"]
                    elif path.endswith((".dateFrom", ".dateTo")):
                        dependencies = ["population.HOLDOUT", "replay_events.values_json.hr_date"]
                    elif path.endswith(".unknownOrCensored"):
                        dependencies = [
                            "analysis_summary.splitCounts.holdout.defects",
                            "analysis_summary.splitCounts.holdout.nonDefects",
                            "analysis_summary.splitCounts.holdout.total",
                        ]
                    else:
                        dependencies = ["population.HOLDOUT", "replay_events.values_json.judge"]
                    result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                    continue
                elif path.startswith("quarantineCounts"):
                    dependencies = ["ap.hr_coil_id"]
                    if path.endswith("UNLINKED_AP"):
                        dependencies.append("fur_hr.hr_coil_id")
                    result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                    continue
                elif path.startswith("labelCensoringCounts"):
                    if path.endswith("AP_UNLINKED"):
                        dependencies = [
                            "ap.hr_coil_id", "fur_hr.hr_coil_id",
                            "population.HOLDOUT", "population.REFERENCE",
                        ]
                    else:
                        dependencies = [
                            "analysis_summary.asOf", "ap.ap_date", "config.labelMaturityDays",
                            "fur_hr.hr_date", "population.REFERENCE",
                        ]
                    result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                    continue
                elif path.startswith("chargePurgeCounts"):
                    if ".outer." in path:
                        dependencies = [
                            "analysis_summary.asOf", "fur_hr.charge_id", "fur_hr.hr_date",
                        ]
                    else:
                        dependencies = [
                            "analysis_summary.asOf", "analysis_summary.innerSplitDate",
                            "config.labelMaturityDays", "population.REFERENCE",
                            "replay_events.charge_id", "replay_events.values_json.ap_date",
                            "replay_events.values_json.hr_date", "replay_events.values_json.judge",
                        ]
                    result.append(_derived_field(role, path, "COUNT_PARTITION", dependencies))
                    continue
                else:
                    raise AssertionError("unexpected summary lineage path")
                result.append(_derived_field(role, path, conversion, dependencies))
    return result


def _summary_artifact(
    config: dict[str, object], tables: dict[str, list[dict[str, str]]],
    materials: list[dict[str, object]], quarantine: dict[str, int],
    split: dict[str, list[dict[str, object]]], rules: list[dict[str, object]],
    memberships: dict[str, dict[str, list[str]]],
) -> dict[str, object]:
    mature_keys = {str(item["material_key"]) for item in split["MATURE"]}
    return {
        "asOf": _AS_OF.isoformat(),
        "bundleId": _BUNDLE_TOKEN,
        "chargePurgeCounts": {
            "inner": {"chargeCount": 0, "rowCount": 0},
            "outer": {"chargeCount": 0, "rowCount": 0},
        },
        "criteriaId": _CRITERIA_TOKEN,
        "dateRange": {"from": "2025-01-01", "to": "2025-04-02"},
        "driftMetrics": _drift_metrics(config, split),
        "evaluationMode": "LOCKED_RETROSPECTIVE_HOLDOUT",
        "holdoutMetrics": _holdout_profiles(),
        "innerSplitDate": _INNER_SPLIT.isoformat(),
        "labelCensoringCounts": {"AP_UNLINKED": 1, "LABEL_NOT_YET_AVAILABLE": 2},
        "lineage": {
            "aggregates": _aggregate_lineage(rules, memberships, split),
            "fields": _field_lineage(config),
            "materials": _material_lineage(materials),
            "populations": _population_lineage(split),
        },
        "quarantineCounts": quarantine,
        "schemaVersion": "sfep-analysis-summary/v1",
        "sourceColumnProfiles": _source_profiles(config, tables),
        "splitCounts": {
            "confirmation": _split_count(split["CONFIRMATION"]),
            "discovery": _split_count(split["DISCOVERY"]),
            "holdout": _split_count(split["HOLDOUT"]),
            "reference": _split_count(split["REFERENCE"], mature_keys),
        },
    }


@lru_cache(maxsize=None)
def _cached_semantics(root_text: str) -> MappingProxyType:
    root = Path(root_text)
    config, tables = _load_inputs(root)
    materials, quarantine = _derive_materials(config, tables)
    split = _split_materials(materials)
    projection = _criteria_projection(config, split)
    quality_rules, memberships = _quality_rules(config, split)
    replay = _replay_events(config, materials)
    ranges = _json_bytes({
        "asOf": _AS_OF.isoformat(),
        "criteriaId": _CRITERIA_TOKEN,
        "ranges": [],
        "schemaVersion": "sfep-operating-ranges/v1",
    })
    rules = _json_bytes({
        "asOf": _AS_OF.isoformat(),
        "criteriaId": _CRITERIA_TOKEN,
        "rules": quality_rules,
        "schemaVersion": "sfep-quality-rules/v1",
    })
    summary = _json_bytes(_summary_artifact(
        config, tables, materials, quarantine, split, quality_rules, memberships,
    ))
    alerts = _json_bytes({
        "alerts": [],
        "bundleId": _BUNDLE_TOKEN,
        "criteriaId": _CRITERIA_TOKEN,
        "expectedReplayEventCount": 95,
        "provenance": {
            "analysisConfigArtifactSha256": "@ARTIFACT_ANALYSIS_CONFIG_SHA256@",
            "analysisConfigSchemaSha256": "@SCHEMA_ANALYSIS_CONFIG_SHA256@",
            "analysisSummaryArtifactSha256": "@ARTIFACT_ANALYSIS_SUMMARY_SHA256@",
            "analysisSummarySchemaSha256": "@SCHEMA_ANALYSIS_SUMMARY_SHA256@",
            "apSourceSha256": "@SOURCE_AP_SHA256@",
            "bundleManifestSchemaSha256": "@SCHEMA_BUNDLE_MANIFEST_SHA256@",
            "equipmentOperatingRangesArtifactSha256": "@ARTIFACT_EQUIPMENT_OPERATING_RANGES_SHA256@",
            "equipmentOperatingRangesSchemaSha256": "@SCHEMA_EQUIPMENT_OPERATING_RANGES_SHA256@",
            "furHrSourceSha256": "@SOURCE_FUR_HR_SHA256@",
            "producerRuntimeArtifactSha256": "@ARTIFACT_PRODUCER_RUNTIME_SHA256@",
            "producerRuntimeSchemaSha256": "@SCHEMA_PRODUCER_RUNTIME_SHA256@",
            "producerRuntimeSha256": "@PRODUCER_RUNTIME_SHA256@",
            "qualityRiskIntervalsArtifactSha256": "@ARTIFACT_QUALITY_RISK_INTERVALS_SHA256@",
            "qualityRiskIntervalsSchemaSha256": "@SCHEMA_QUALITY_RISK_INTERVALS_SHA256@",
            "replayEventsArtifactSha256": "@ARTIFACT_REPLAY_EVENTS_SHA256@",
            "replayEventsSchemaSha256": "@SCHEMA_REPLAY_EVENTS_SHA256@",
            "smCcSourceSha256": "@SOURCE_SM_CC_SHA256@",
        },
        "schemaVersion": "sfep-expected-alerts/v1",
    })
    return MappingProxyType({
        "analysis_summary.template.json": summary,
        "criteria_projection.jsonl": projection,
        "equipment_operating_ranges.template.json": ranges,
        "expected_alerts.json": alerts,
        "quality_risk_intervals.template.json": rules,
        "replay_events.template.csv": replay,
    })


def build_golden_semantics(root: Path) -> MappingProxyType:
    """Return immutable semantic expectation bytes derived from four read-only inputs."""

    if not isinstance(root, Path) or not root.is_absolute():
        raise TypeError("golden oracle root must be an absolute pathlib.Path")
    return _cached_semantics(str(root.resolve()))
