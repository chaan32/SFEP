from __future__ import annotations

import ast
import copy
import csv
import hashlib
import io
import inspect
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import pytest
from jsonschema import ValidationError
from jsonschema.validators import validator_for


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_ROOT = REPOSITORY_ROOT / "analysis"
CONTRACT = REPOSITORY_ROOT / "contracts/equipment-monitor/v1"
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
RANGE_CONTEXT_KEYS = frozenset({
    "ap_plant", "ap_shift", "ap_thick_band", "ap_width_band",
    "f_jangip_gubun", "furnace_no", "hr_thick_band", "hr_width_band",
    "slab_width_band", "sm_plant", "steel_grade", "steel_usage",
})
RISK_CONTEXT_KEYS = frozenset({
    "ap_plant", "ap_shift", "ap_thick_band", "ap_width_band",
    "f_jangip_gubun", "furnace_no", "hr_thick_band", "hr_width_band",
    "slab_width_band", "sm_plant", "steel_grade", "steel_usage",
})
CONTEXT_KEY_PATTERN = (
    r"ap_plant|ap_shift|ap_thick_band|ap_width_band|f_jangip_gubun|"
    r"furnace_no|hr_thick_band|hr_width_band|slab_width_band|sm_plant|"
    r"steel_grade|steel_usage"
)
LINEAGE_OUTPUT_PATTERNS = {
    "bundle_manifest": re.compile(
        r"^(?:schemaVersion|bundleId|criteriaId|asOf|timezone|labelMaturityDays|"
        r"identity\.(?:analysis_config_sha256|criteria_id|producer_runtime_sha256|"
        r"schema\.(?:analysis_config|analysis_summary|bundle_manifest|equipment_operating_ranges|"
        r"producer_runtime|quality_risk_intervals|replay_events)\.sha256|"
        r"source\.(?:ap|fur_hr|sm_cc)\.(?:name|sha256|size_bytes))|"
        r"criteriaIdentity\.(?:analysis_config_sha256|as_of|criteria_projection_sha256|"
        r"producer_runtime_sha256|schema\.(?:analysis_config|equipment_operating_ranges|"
        r"producer_runtime|quality_risk_intervals)\.sha256)|"
        r"artifacts\[\]\.(?:role|sizeBytes|sha256|schemaVersion))$"
    ),
    "analysis_config": re.compile(
        r"^(?:schemaVersion|analysisConfigVersion|timezone|labelMaturityDays|wilsonZ|"
        r"splits\.(?:referenceFraction|discoveryFraction)|"
        r"operatingRanges\.(?:minimumSupport|extremeTailMinimumSupport|quantileMethod|"
        r"typicalLowerQuantile|typicalUpperQuantile|extremeLowerQuantile|extremeUpperQuantile)|"
        r"qualityRisk\.(?:minimumDiscoverySupport|minimumCautionDefects|minimumDangerDefects|"
        r"minimumConfirmationSupport|minimumConfirmationDefects|minimumInformativeStrata|"
        r"numericBins|interactionBins|zeroCellCorrection|bhQ\.(?:caution|danger)|"
        r"relativeRisk\.(?:caution|danger)|riskDifference\.(?:caution|danger)|"
        r"confirmationRelativeRisk\.(?:cautionExclusive|danger))|"
        r"bootstrap\.(?:replicates|minimumValidReplicates)|"
        r"fields\[\]\.(?:field|sourceRole|sourceColumn|dataType|featureRole|equipmentType|"
        r"firstAvailableStage|evidenceFamily|dependencies\[\])|"
        r"(?:rangeContextHierarchies|riskAdjustmentHierarchies)\[\]\.(?:equipmentType|levels\[\]\[\])|"
        r"fixedInteractions\[\]\[\]|fdrFamilies\[\]|evidenceFamilies\[\])$"
    ),
    "producer_runtime": re.compile(
        r"^(?:schemaVersion|pipVersion|platform\.(?:system|machine|macosProductVersion|sysconfigPlatform)|"
        r"python\.(?:implementation|version|build|cacheTag|soabi|executableSha256)|"
        r"locks\.(?:pyproject|bootstrap|buildRequirements|requirements|wheelhouse|producer)|"
        r"packages\[\]\.(?:name|version|direct|wheelFilename|wheelTag|wheelSha256|installedCodeTreeSha256)|"
        r"producer\.(?:name|version|wheelFilename|wheelSha256|installedCodeTreeSha256|sourceSha256)|"
        r"environmentPolicy\.(?:pythonHashSeed|timezone|localeIndependentParsing|floatPolicy))$"
    ),
    "equipment_operating_ranges": re.compile(
        r"^(?:schemaVersion|criteriaId|asOf|ranges\[\]\.(?:ruleId|field|fieldRole|"
        r"firstAvailableStage|equipmentType|equipmentId|contextLevel|context(?:\."
        + CONTEXT_KEY_PATTERN
        + r")?|support|median|p01|p05|p95|p99|lowerTailEnabled|upperTailEnabled))$"
    ),
    "quality_risk_intervals": re.compile(
        r"^(?:schemaVersion|criteriaId|asOf|rules\[\]\.(?:ruleId|analysisFamily|evidenceFamily|"
        r"firstAvailableStage|equipmentType|applicationScope|equipmentId|fieldNames\[\]|"
        r"predicate\.allOf\[\]\.(?:field|type|lower|lowerInclusive|upper|upperInclusive|values(?:\[\])?)|"
        r"applicationContext(?:\."
        + CONTEXT_KEY_PATTERN
        + r")?|adjustmentLevel|adjustmentFieldsDropped\[\]|"
        r"adjustmentKind|grade|earlyWarningEligible|(?:discovery|confirmation)\.(?:support|defects|"
        r"crudeRate|crudeRateCiLower|crudeRateCiUpper|adjustedRate|comparatorAdjustedRate|"
        r"riskDifference|relativeRisk|relativeRiskCiLower|relativeRiskCiUpper|pValue|qValue|reasonCode)|"
        r"displayMergeRuleIds\[\]))$"
    ),
    "replay_events": re.compile(
        r"^(?:schema_version|bundle_id|criteria_id|event_id|replay_date|replay_hour|batch_kind|batch_id|"
        r"equipment_batch_id|batch_step|time_precision|material_key|equipment_type|equipment_id|"
        r"charge_id|slab_no|hr_coil_id|ap_prod_id|values_json\.(?:sm_plant|steel_grade|steel_usage|"
        r"cc_gubun|slab_gubun|tundish_temp|mlac_ratio|delta_ferrite|ingre_cr|ingre_ni|ingre_s|"
        r"slab_grind|cast_date|furnace_no|f_jangip_gubun|f_jangip_temp|slab_width|f_pre_temp|"
        r"f_pre_interval|f_heat_temp|f_heat_interval|f_sock_temp|f_sock_interval|f_bfg|f_cog|"
        r"f_ldg|f_bfg_ratio|f_cog_ratio|f_ldg_ratio|f_ext_date|f_ext_time|hr_date|hr_thick|"
        r"hr_width|rm4_temp|rm_pitch|ap_plant|ap_date|ap_shift|ap_thick|ap_width|ap_line_speed|judge))$"
    ),
    "analysis_summary": re.compile(
        r"^(?:schemaVersion|bundleId|criteriaId|asOf|innerSplitDate|evaluationMode|"
        r"dateRange\.(?:from|to)|splitCounts\.(?:reference|discovery|confirmation|holdout)\."
        r"(?:total|defects|nonDefects|unknownOrCensored|dateFrom|dateTo)|"
        r"quarantineCounts\.(?:MISSING_SM_CC_KEY|MISSING_FUR_HR_KEY|MISSING_AP_HR_COIL_ID|"
        r"MISSING_FUR_HR_COIL_ID|MISSING_AP_PROD_ID|DUPLICATE_SM_CC_KEY|DUPLICATE_FUR_HR_KEY|"
        r"DUPLICATE_AP_KEY|DUPLICATE_FUR_HR_COIL_KEY|DUPLICATE_AP_PROD_ID|INVALID_FUR_HR_DATE|"
        r"IMPOSSIBLE_STAGE_DATE_ORDER|UNLINKED_SM_CC|UNLINKED_FUR_HR|UNLINKED_AP)|"
        r"labelCensoringCounts\.(?:AP_UNLINKED|LABEL_MISSING|LABEL_NOT_YET_AVAILABLE)|"
        r"chargePurgeCounts\.(?:outer|inner)\.(?:chargeCount|rowCount)|"
        r"sourceColumnProfiles\[\]\.(?:sourceRole|column|dataType|total|missing|unique|"
        r"numeric(?:\.(?:p05|median|p95))?|levels(?:\[\]\.(?:value|count))?)|"
        r"driftMetrics\[\]\.(?:field|dataType|(?:reference|holdout)\."
        r"(?:support|missingRate|p05|median|p95|levels(?:\[\]\.(?:value|count))?))|"
        r"holdoutMetrics\[\]\.(?:alertGrade|total|truePositive|falsePositive|trueNegative|falseNegative|"
        r"(?:alertRate|precision|recall|baseDefectRate|lift|falseAlertsPer100)\."
        r"(?:pointEstimate|lower|upper|validReplicates|reasonCode)))$"
    ),
}

QUARANTINE_REASONS = (
    "MISSING_SM_CC_KEY", "MISSING_FUR_HR_KEY", "MISSING_AP_HR_COIL_ID",
    "MISSING_FUR_HR_COIL_ID", "MISSING_AP_PROD_ID", "DUPLICATE_SM_CC_KEY",
    "DUPLICATE_FUR_HR_KEY", "DUPLICATE_AP_KEY", "DUPLICATE_FUR_HR_COIL_KEY",
    "DUPLICATE_AP_PROD_ID", "INVALID_FUR_HR_DATE", "IMPOSSIBLE_STAGE_DATE_ORDER",
    "UNLINKED_SM_CC", "UNLINKED_FUR_HR", "UNLINKED_AP",
)
CENSOR_REASONS = ("AP_UNLINKED", "LABEL_MISSING", "LABEL_NOT_YET_AVAILABLE")
POPULATION_ORDER = ("REFERENCE", "DISCOVERY", "CONFIRMATION", "HOLDOUT")
HOLDOUT_PROFILE_ORDER = ("DANGER", "CAUTION_OR_DANGER")
HOLDOUT_METRIC_NAMES = (
    "alertRate", "precision", "recall", "baseDefectRate", "lift", "falseAlertsPer100",
)
CONVERSIONS = {
    "COPY_SOURCE_SCALAR", "PARSE_FINITE_BINARY64", "PARSE_DATE", "PARSE_HOUR_BUCKET",
    "DERIVE_FUEL_RATIO", "COPY_CANONICAL_CONFIG", "COPY_VERIFIED_RUNTIME",
    "COMPUTE_IDENTITY", "COMPUTE_SHA256", "TYPE1_QUANTILE", "COUNT_PARTITION",
    "PROFILE_SOURCE_COLUMN", "COMPARE_DISTRIBUTIONS", "COMPUTE_HOLDOUT_METRIC",
    "LINEAGE_INDEX_V1", "COPY_IDENTITY_VALUE", "COPY_DIGEST_VALUE", "COPY_SCHEMA_VERSION",
    "COPY_BOUNDARY_DATE", "SELECT_TIME_BOUNDARY", "COPY_SOURCE_METADATA", "COPY_FIELD_METADATA",
    "PROJECT_ARTIFACT_METADATA", "COMPUTE_BYTE_SIZE", "SELECT_STAGE_VALUE",
    "CLASSIFY_REPLAY_SCHEDULE", "SELECT_STAGE_EQUIPMENT", "DERIVE_RULE_CANDIDATE",
    "COMPUTE_QUALITY_METRIC", "APPLY_GRADE_POLICY", "DERIVE_STAGE_ELIGIBILITY",
    "MERGE_DISPLAY_INTERVALS", "COPY_POLICY_VALUE", "COMPUTE_DATE_RANGE",
    "DERIVE_RANGE_GROUP", "COUNT_RANGE_SUPPORT", "APPLY_RANGE_TAIL_POLICY",
}
AGGREGATE_FILTERS = {
    "STAGE_AVAILABLE_AT_AS_OF", "FINITE_VALUE", "LABEL_AVAILABLE_AND_MATURE",
    "PREDICATE_MATCH", "INFORMATIVE_STRATA_ONLY", "FIXED_DISCOVERY_STRATA",
}
AGGREGATE_TRANSFORMATIONS = {
    "TYPE1_QUANTILE", "WILSON_SCORE_INTERVAL", "DIRECT_STANDARDIZATION",
    "MANTEL_HAENSZEL_RR", "CMH_NORMAL_APPROXIMATION", "BENJAMINI_HOCHBERG_FDR",
    "CHARGE_BLOCK_BOOTSTRAP_PERCENTILE_CI", "CONFIRMATION_WEIGHT_RENORMALIZATION",
    "GRADE_POLICY_V1", "DISPLAY_MERGE_V1",
}
LITERAL_SOURCE_DEPENDENCY_TERMINALS = frozenset({
    "sm_cc.sm_plant", "sm_cc.charge_id", "sm_cc.steel_grade",
    "sm_cc.steel_usage", "sm_cc.delta_ferrite", "sm_cc.ingre_cr",
    "sm_cc.ingre_ni", "sm_cc.ingre_s", "sm_cc.cast_date", "sm_cc.cc_gubun",
    "sm_cc.tundish_temp", "sm_cc.mlac_ratio", "sm_cc.slab_no",
    "sm_cc.slab_gubun", "sm_cc.slab_grind",
    "fur_hr.charge_id", "fur_hr.slab_no", "fur_hr.furnace_no",
    "fur_hr.f_jangip_gubun", "fur_hr.f_jangip_temp", "fur_hr.f_bfg",
    "fur_hr.f_cog", "fur_hr.f_ldg", "fur_hr.f_bfg_per", "fur_hr.f_cog_per",
    "fur_hr.f_ldg_per", "fur_hr.f_pre_temp", "fur_hr.f_heat_temp",
    "fur_hr.f_sock_temp", "fur_hr.f_pre_interval", "fur_hr.f_heat_interval",
    "fur_hr.f_sock_interval", "fur_hr.f_ext_date", "fur_hr.f_ext_time",
    "fur_hr.hr_coil_id", "fur_hr.hr_date", "fur_hr.hr_thick",
    "fur_hr.hr_width", "fur_hr.rm4_temp", "fur_hr.rm_pitch",
    "fur_hr.slab_width", "ap.judge", "ap.hr_coil_id", "ap.ap_plant",
    "ap.ap_prod_id", "ap.ap_date", "ap.ap_shift", "ap.ap_thick",
    "ap.ap_width", "ap.ap_line_speed",
})


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


def _positive_binary64_fraction(bits: int) -> tuple[Fraction, int]:
    exponent = (bits >> 52) & 0x7FF
    fraction_bits = bits & ((1 << 52) - 1)
    if exponent == 0:
        significand = fraction_bits
        power = -1074
    else:
        significand = (1 << 52) | fraction_bits
        power = exponent - 1023 - 52
    value = Fraction(significand)
    if power >= 0:
        value *= 1 << power
    else:
        value /= 1 << -power
    return value, significand


def _floor_log10_fraction(value: Fraction) -> int:
    assert value > 0
    candidate = len(str(value.numerator)) - len(str(value.denominator))

    def power10(exponent: int) -> Fraction:
        return (
            Fraction(10**exponent)
            if exponent >= 0
            else Fraction(1, 10 ** -exponent)
        )

    while value < power10(candidate):
        candidate -= 1
    while value >= power10(candidate + 1):
        candidate += 1
    return candidate


def _ceil_fraction(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def _render_decimal_layouts(coefficient: int, exponent: int) -> set[str]:
    assert coefficient > 0 and coefficient % 10 != 0
    digits = str(coefficient)
    count = len(digits)
    if exponent >= 0:
        fixed = digits + ("0" * exponent)
    else:
        point = count + exponent
        fixed = (
            digits[:point] + "." + digits[point:]
            if point > 0
            else "0." + ("0" * -point) + digits
        )
    layouts = {fixed}
    for point in range(1, count + 1):
        mantissa = (
            digits
            if point == count
            else digits[:point] + "." + digits[point:]
        )
        scientific_exponent = exponent + count - point
        layouts.add(mantissa + "e" + str(scientific_exponent))
    return layouts


def _independent_binary64_json_number(number: float) -> str:
    bits = int.from_bytes(struct.pack(">d", number), "big")
    sign = "-" if bits >> 63 else ""
    magnitude_bits = bits & ((1 << 63) - 1)
    exponent = (magnitude_bits >> 52) & 0x7FF
    if exponent == 0x7FF:
        raise ValueError("JSON numbers must be finite binary64 values")
    if magnitude_bits == 0:
        return "0"

    target, significand = _positive_binary64_fraction(magnitude_bits)
    previous = (
        Fraction(0)
        if magnitude_bits == 1
        else _positive_binary64_fraction(magnitude_bits - 1)[0]
    )
    if magnitude_bits == 0x7FEFFFFFFFFFFFFF:
        following = Fraction(1 << 1024)
    else:
        following = _positive_binary64_fraction(magnitude_bits + 1)[0]
    lower = (previous + target) / 2
    upper = (target + following) / 2
    midpoint_closed = significand % 2 == 0

    sign_length = len(sign.encode("utf-8"))
    candidates: set[str] = set()
    for digit_count in range(1, 24 - sign_length + 1):
        q_min = _floor_log10_fraction(lower) - digit_count + 1
        q_max = _floor_log10_fraction(upper) - digit_count + 1
        for decimal_exponent in range(q_min, q_max + 1):
            scale = (
                Fraction(10**decimal_exponent)
                if decimal_exponent >= 0
                else Fraction(1, 10 ** -decimal_exponent)
            )
            scaled_lower = lower / scale
            scaled_upper = upper / scale
            coefficient_low = (
                _ceil_fraction(scaled_lower)
                if midpoint_closed
                else scaled_lower.numerator // scaled_lower.denominator + 1
            )
            coefficient_high = (
                scaled_upper.numerator // scaled_upper.denominator
                if midpoint_closed
                else _ceil_fraction(scaled_upper) - 1
            )
            digit_low = 1 if digit_count == 1 else 10 ** (digit_count - 1)
            digit_high = 10**digit_count - 1
            coefficient_low = max(coefficient_low, digit_low)
            coefficient_high = min(coefficient_high, digit_high)
            if coefficient_low > coefficient_high:
                continue
            coefficient = coefficient_low
            while coefficient <= coefficient_high and coefficient % 10 == 0:
                coefficient += 1
            if coefficient > coefficient_high:
                continue
            candidates.update(
                sign + layout
                for layout in _render_decimal_layouts(
                    coefficient, decimal_exponent
                )
            )
    if not candidates:
        raise AssertionError("exact binary64 interval produced no decimal candidate")
    return min(
        candidates,
        key=lambda candidate: (
            len(candidate.encode("utf-8")),
            candidate.encode("utf-8"),
        ),
    )


_DECIMAL_NUMBER = re.compile(
    r"^(?P<sign>-?)(?P<integer>0|[1-9][0-9]*)(?:\.(?P<fraction>[0-9]+))?"
    r"(?:[eE](?P<exponent>[+-]?[0-9]+))?$"
)


def _exact_decimal_fraction(token: str) -> Fraction:
    match = _DECIMAL_NUMBER.fullmatch(token)
    if match is None:
        raise AssertionError(f"invalid JSON numeric token: {token}")
    fraction_digits = match.group("fraction") or ""
    coefficient = int(match.group("integer") + fraction_digits)
    if match.group("sign") == "-":
        coefficient = -coefficient
    exponent = int(match.group("exponent") or "0") - len(fraction_digits)
    return (
        Fraction(coefficient * (10**exponent))
        if exponent >= 0
        else Fraction(coefficient, 10 ** -exponent)
    )


def _exact_decimal_to_binary64_bits(token: str) -> int:
    exact = _exact_decimal_fraction(token)
    sign_bit = 1 << 63 if exact < 0 else 0
    magnitude = abs(exact)
    if magnitude == 0:
        return sign_bit

    maximum_bits = 0x7FEFFFFFFFFFFFFF
    low = 0
    high = maximum_bits
    while low <= high:
        middle = (low + high) // 2
        middle_value = (
            Fraction(0)
            if middle == 0
            else _positive_binary64_fraction(middle)[0]
        )
        if middle_value <= magnitude:
            low = middle + 1
        else:
            high = middle - 1
    lower_bits = high
    lower_value = (
        Fraction(0)
        if lower_bits == 0
        else _positive_binary64_fraction(lower_bits)[0]
    )
    if lower_value == magnitude:
        chosen = lower_bits
    else:
        upper_bits = lower_bits + 1
        upper_value = (
            Fraction(1 << 1024)
            if upper_bits > maximum_bits
            else _positive_binary64_fraction(upper_bits)[0]
        )
        lower_distance = magnitude - lower_value
        upper_distance = upper_value - magnitude
        if lower_distance < upper_distance:
            chosen = lower_bits
        elif upper_distance < lower_distance:
            chosen = upper_bits
        else:
            chosen = lower_bits if lower_bits % 2 == 0 else upper_bits
    if chosen > maximum_bits:
        raise ValueError("JSON numeric token rounds to non-finite binary64")
    return sign_bit | chosen


def _assert_json_numeric_tokens_are_canonical(text: str) -> None:
    def parse_integer(token: str) -> int:
        assert token == str(int(token)), f"non-canonical exact integer: {token}"
        return int(token)

    def parse_binary64(token: str) -> float:
        bits = _exact_decimal_to_binary64_bits(token)
        number = struct.unpack(">d", bits.to_bytes(8, "big"))[0]
        expected = _independent_binary64_json_number(number)
        assert token == expected, f"non-canonical binary64: {token} != {expected}"
        return number

    json.loads(text, parse_int=parse_integer, parse_float=parse_binary64)


def _canonical_json_bytes(value: object) -> bytes:
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
            return _independent_binary64_json_number(node)
        if isinstance(node, str):
            return json.dumps(node, ensure_ascii=False, separators=(",", ":"))
        if isinstance(node, list):
            return "[" + ",".join(encode(item) for item in node) + "]"
        if isinstance(node, dict):
            if not all(isinstance(key, str) for key in node):
                raise TypeError("canonical JSON object keys must be strings")
            return "{" + ",".join(
                encode(key) + ":" + encode(node[key])
                for key in sorted(node, key=lambda item: item.encode("utf-8"))
            ) + "}"
        raise TypeError(f"unsupported canonical JSON value: {type(node).__name__}")

    return (encode(value) + "\n").encode("utf-8")


def _validate_summary_application_contract(summary: dict) -> None:
    config = json.loads((ANALYSIS_ROOT / "analysis_config.json").read_text(encoding="utf-8"))
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
    external_dependencies = source_dependencies | {
        f"config.{path}" for path in (
            "analysisConfigVersion", "bootstrap.minimumValidReplicates", "bootstrap.replicates",
            "evidenceFamilies[]", "fdrFamilies[]", "fields[].dataType", "fields[].dependencies[]",
            "fields[].equipmentType", "fields[].evidenceFamily", "fields[].featureRole",
            "fields[].field", "fields[].firstAvailableStage", "fields[].sourceColumn",
            "fields[].sourceRole", "fixedInteractions[][]", "labelMaturityDays",
            "operatingRanges.extremeLowerQuantile", "operatingRanges.extremeTailMinimumSupport",
            "operatingRanges.extremeUpperQuantile", "operatingRanges.minimumSupport",
            "operatingRanges.quantileMethod", "operatingRanges.typicalLowerQuantile",
            "operatingRanges.typicalUpperQuantile", "qualityRisk.bhQ.caution",
            "qualityRisk.bhQ.danger", "qualityRisk.confirmationRelativeRisk.cautionExclusive",
            "qualityRisk.confirmationRelativeRisk.danger", "qualityRisk.interactionBins",
            "qualityRisk.minimumCautionDefects", "qualityRisk.minimumConfirmationDefects",
            "qualityRisk.minimumConfirmationSupport", "qualityRisk.minimumDangerDefects",
            "qualityRisk.minimumDiscoverySupport", "qualityRisk.minimumInformativeStrata",
            "qualityRisk.numericBins", "qualityRisk.relativeRisk.caution",
            "qualityRisk.relativeRisk.danger", "qualityRisk.riskDifference.caution",
            "qualityRisk.riskDifference.danger", "qualityRisk.zeroCellCorrection",
            "rangeContextHierarchies[].equipmentType", "rangeContextHierarchies[].levels[][]",
            "riskAdjustmentHierarchies[].equipmentType", "riskAdjustmentHierarchies[].levels[][]",
            "schemaVersion", "splits.discoveryFraction", "splits.referenceFraction", "timezone",
            "wilsonZ",
        )
    } | {
        f"runtime.{path}" for path in (
            "environmentPolicy.floatPolicy", "environmentPolicy.localeIndependentParsing",
            "environmentPolicy.pythonHashSeed", "environmentPolicy.timezone", "locks.bootstrap",
            "locks.buildRequirements", "locks.producer", "locks.pyproject", "locks.requirements",
            "locks.wheelhouse", "packages[].direct", "packages[].installedCodeTreeSha256",
            "packages[].name", "packages[].version", "packages[].wheelFilename",
            "packages[].wheelSha256", "packages[].wheelTag", "pipVersion", "platform.machine",
            "platform.macosProductVersion", "platform.sysconfigPlatform", "platform.system",
            "producer.installedCodeTreeSha256", "producer.name", "producer.sourceSha256",
            "producer.version", "producer.wheelFilename", "producer.wheelSha256", "python.build",
            "python.cacheTag", "python.executableSha256", "python.implementation", "python.soabi",
            "python.version", "schemaVersion",
        )
    } | {
        f"schema.{role}{suffix}"
        for role in (
            "analysis_config", "analysis_summary", "bundle_manifest",
            "equipment_operating_ranges", "producer_runtime", "quality_risk_intervals",
            "replay_events",
        )
        for suffix in ("", ".sha256")
    } | {
        f"source.{role}.{field}"
        for role in ("ap", "fur_hr", "sm_cc")
        for field in ("name", "sha256", "size_bytes")
    } | {
        "identity.analysis_config_sha256", "identity.bundle_id", "identity.criteria_id",
        "identity.criteria_projection_sha256", "identity.producer_runtime_sha256",
        "population.REFERENCE", "population.DISCOVERY", "population.CONFIRMATION",
        "population.HOLDOUT", "policy.LOCKED_RETROSPECTIVE_HOLDOUT",
        "policy.HOLDOUT_ALERT_DANGER", "policy.HOLDOUT_ALERT_CAUTION_OR_DANGER",
    }

    for count in summary["splitCounts"].values():
        assert count["total"] == count["defects"] + count["nonDefects"] + count["unknownOrCensored"]
    assert tuple(summary["quarantineCounts"]) == tuple(
        reason for reason in QUARANTINE_REASONS if reason in summary["quarantineCounts"]
    )
    assert set(summary["labelCensoringCounts"]) <= set(CENSOR_REASONS)
    assert set(summary["chargePurgeCounts"]) == {"outer", "inner"}
    for purge in summary["chargePurgeCounts"].values():
        assert purge["rowCount"] >= purge["chargeCount"]
    assert tuple(profile["alertGrade"] for profile in summary["holdoutMetrics"]) == (
        HOLDOUT_PROFILE_ORDER
    )
    for profile in summary["holdoutMetrics"]:
        assert profile["total"] == (
            profile["truePositive"] + profile["falsePositive"]
            + profile["trueNegative"] + profile["falseNegative"]
        )
        for metric_name in HOLDOUT_METRIC_NAMES:
            metric = profile[metric_name]
            if metric["reasonCode"] == "NONE":
                assert metric["pointEstimate"] is not None
                assert metric["lower"] is not None and metric["upper"] is not None
                assert metric["validReplicates"] >= 1900
                assert metric["lower"] <= metric["upper"]
            elif metric["reasonCode"] == "ZERO_DENOMINATOR":
                assert metric == {
                    "pointEstimate": None, "lower": None, "upper": None,
                    "validReplicates": 0, "reasonCode": "ZERO_DENOMINATOR",
                }
            else:
                assert metric["reasonCode"] == "TOO_FEW_VALID_BOOTSTRAPS"
                assert metric["pointEstimate"] is not None
                assert metric["lower"] is None and metric["upper"] is None
                assert metric["validReplicates"] < 1900

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
    assert tuple(item["populationRef"] for item in lineage["populations"]) == POPULATION_ORDER
    assert tuple(item["split"] for item in lineage["populations"]) == POPULATION_ORDER
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
            assert aggregate["split"] == "REFERENCE"
            assert aggregate["populationRef"] == "REFERENCE"
            assert aggregate["comparatorDefinition"] == "NOT_APPLICABLE"
        else:
            assert aggregate["split"] in {"DISCOVERY", "CONFIRMATION"}
            assert aggregate["populationRef"] == aggregate["split"]
            assert aggregate["comparatorDefinition"] == "FIXED_POPULATION_STRATA_MINUS_CANDIDATE"
        assert set(aggregate["filters"]) <= AGGREGATE_FILTERS
        assert set(aggregate["transformations"]) <= AGGREGATE_TRANSFORMATIONS
    lineage_node_ids = {
        f'{field["artifactRole"]}.{field["outputField"]}' for field in lineage["fields"]
    }
    dependency_graph: dict[str, list[str]] = {}
    for field in lineage["fields"]:
        node_id = f'{field["artifactRole"]}.{field["outputField"]}'
        output_pattern = LINEAGE_OUTPUT_PATTERNS.get(field["artifactRole"])
        if output_pattern is not None:
            assert output_pattern.fullmatch(field["outputField"]), "invalid lineage output field"
        assert field["conversion"] in CONVERSIONS
        raw = field["sourceRole"] is not None
        if raw:
            assert field["sourceColumn"] is not None
            assert field["firstAvailableStage"] is not None
            assert field["sourceColumn"] in source_headers[field["sourceRole"]]
            definition = config_fields[(field["sourceRole"], field["sourceColumn"])]
            assert field["artifactRole"] == "replay_events"
            identifier_outputs = {
                "charge_id": "charge_id", "slab_no": "slab_no",
                "hr_coil_id": "hr_coil_id", "ap_prod_id": "ap_prod_id",
            }
            assert field["outputField"] == identifier_outputs.get(
                definition["field"], f'values_json.{definition["field"]}'
            )
            assert field["firstAvailableStage"] == definition["firstAvailableStage"]
            assert field["dependencies"] == []
        else:
            assert field["sourceColumn"] is None
            assert field["dependencies"]
            if field["artifactRole"] != "replay_events":
                assert field["firstAvailableStage"] is None
        for dependency in field["dependencies"]:
            assert dependency in external_dependencies or dependency in lineage_node_ids
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


def _holdout_metric(
    point: float | None,
    lower: float | None,
    upper: float | None,
    valid_replicates: int,
    reason_code: str,
) -> dict:
    return {
        "pointEstimate": point,
        "lower": lower,
        "upper": upper,
        "validReplicates": valid_replicates,
        "reasonCode": reason_code,
    }


def _holdout_profile(alert_grade: str) -> dict:
    return {
        "alertGrade": alert_grade,
        "total": 3,
        "truePositive": 1,
        "falsePositive": 0,
        "trueNegative": 2,
        "falseNegative": 0,
        "alertRate": _holdout_metric(1 / 3, 0.0, 1.0, 2000, "NONE"),
        "precision": _holdout_metric(1.0, 1.0, 1.0, 2000, "NONE"),
        "recall": _holdout_metric(1.0, 1.0, 1.0, 2000, "NONE"),
        "baseDefectRate": _holdout_metric(1 / 3, 0.0, 1.0, 2000, "NONE"),
        "lift": _holdout_metric(3.0, 1.0, 3.0, 2000, "NONE"),
        "falseAlertsPer100": _holdout_metric(0.0, 0.0, 0.0, 2000, "NONE"),
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
        "quarantineCounts": {"DUPLICATE_AP_KEY": 1, "UNLINKED_AP": 1},
        "labelCensoringCounts": {"LABEL_NOT_YET_AVAILABLE": 2},
        "chargePurgeCounts": {
            "outer": {"chargeCount": 0, "rowCount": 0},
            "inner": {"chargeCount": 0, "rowCount": 0},
        },
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
        "holdoutMetrics": [
            _holdout_profile("DANGER"),
            _holdout_profile("CAUTION_OR_DANGER"),
        ],
        "lineage": {
            "fields": [{
                "artifactRole": "replay_events", "outputField": "values_json.f_pre_temp",
                "sourceRole": "fur_hr", "sourceColumn": "f_pre_temp", "conversion": "PARSE_FINITE_BINARY64",
                "dependencies": [], "firstAvailableStage": "PREHEAT_COMPLETE",
            }, {
                "artifactRole": "analysis_summary", "outputField": "driftMetrics[].reference.median",
                "sourceRole": None, "sourceColumn": None, "conversion": "COMPARE_DISTRIBUTIONS",
                "dependencies": ["fur_hr.f_pre_temp"], "firstAvailableStage": None,
            }],
            "materials": [{
                "materialKey": MATERIAL_CH1_1, "chargeId": "CH1", "slabNo": "1", "hrCoilId": "H001",
                "sourceRecords": [{"role": "sm_cc", "name": "sts_1sm_cc_1.csv", "recordNumber": 2}, {"role": "fur_hr", "name": "sts_2fur_hr_2.csv", "recordNumber": 2}, {"role": "ap", "name": "sts_3ap_3.csv", "recordNumber": 2}],
            }],
            "populations": [
                {"populationRef": split, "split": split, "materialKeys": [MATERIAL_CH1_1]}
                for split in POPULATION_ORDER
            ],
            "aggregates": [{
                "artifactRole": "equipment_operating_ranges", "ruleId": SHA_B,
                "split": "REFERENCE", "populationRef": "REFERENCE", "inputMaterialKeys": [MATERIAL_CH1_1],
                "comparatorDefinition": "NOT_APPLICABLE",
                "filters": ["FINITE_VALUE"], "transformations": ["TYPE1_QUANTILE"],
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
    "analysis_config.schema.json": lambda: json.loads((ANALYSIS_ROOT / "analysis_config.json").read_text()),
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
        ANALYSIS_ROOT / "analysis_config.json", CONTRACT / "id-test-vectors.json",
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


def test_summary_closes_task3_counts_and_requires_separate_charge_purges():
    validator = _validator("analysis_summary.schema.json")
    validator.validate(_summary())

    for invalid_reason in ("DUPLICATE_KEY", "CROSS_BOUNDARY_CHARGE", "NEW_REASON"):
        invalid = _summary()
        invalid["quarantineCounts"] = {invalid_reason: 1}
        with pytest.raises(ValidationError):
            validator.validate(invalid)
    invalid_censor = _summary()
    invalid_censor["labelCensoringCounts"] = {"UNKNOWN_CENSOR": 1}
    with pytest.raises(ValidationError):
        validator.validate(invalid_censor)
    missing_purge = _summary()
    missing_purge.pop("chargePurgeCounts")
    with pytest.raises(ValidationError):
        validator.validate(missing_purge)
    merged_purge = _summary()
    merged_purge["quarantineCounts"]["CROSS_BOUNDARY_CHARGE"] = 1
    with pytest.raises(ValidationError):
        validator.validate(merged_purge)


def test_summary_holdout_profiles_are_ordered_and_each_metric_owns_status():
    validator = _validator("analysis_summary.schema.json")
    valid = _summary()
    validator.validate(valid)
    _validate_summary_application_contract(valid)

    reversed_profiles = _summary()
    reversed_profiles["holdoutMetrics"].reverse()
    missing_profile = _summary()
    missing_profile["holdoutMetrics"].pop()
    shared_status = _summary()
    shared_status["holdoutMetrics"][0]["reasonCode"] = "NONE"
    for invalid in (reversed_profiles, missing_profile, shared_status):
        with pytest.raises(ValidationError):
            validator.validate(invalid)

    zero_denominator = _summary()
    zero_denominator["holdoutMetrics"][0]["precision"] = _holdout_metric(
        None, None, None, 0, "ZERO_DENOMINATOR"
    )
    validator.validate(zero_denominator)
    _validate_summary_application_contract(zero_denominator)

    confused_zero = copy.deepcopy(zero_denominator)
    confused_zero["holdoutMetrics"][0]["precision"]["lower"] = 0.0
    with pytest.raises(ValidationError):
        validator.validate(confused_zero)
    too_few = _summary()
    too_few["holdoutMetrics"][0]["lift"] = _holdout_metric(
        3.0, None, None, 1899, "TOO_FEW_VALID_BOOTSTRAPS"
    )
    validator.validate(too_few)
    _validate_summary_application_contract(too_few)


def test_summary_populations_and_aggregate_population_contract_are_exact():
    validator = _validator("analysis_summary.schema.json")
    valid = _summary()
    validator.validate(valid)
    _validate_summary_application_contract(valid)

    for mutation in (
        lambda value: value["lineage"]["populations"].reverse(),
        lambda value: value["lineage"]["populations"].pop(),
        lambda value: value["lineage"]["aggregates"][0].update(split="DISCOVERY"),
        lambda value: value["lineage"]["aggregates"][0].update(populationRef="DISCOVERY"),
    ):
        invalid = _summary()
        mutation(invalid)
        with pytest.raises((ValidationError, AssertionError)):
            validator.validate(invalid)
            _validate_summary_application_contract(invalid)


def test_lineage_vocabularies_and_all_seven_output_grammars_are_closed():
    validator = _validator("analysis_summary.schema.json")
    field_schema = _load_schema("analysis_summary.schema.json")["$defs"]["fieldLineage"]
    assert set(field_schema["properties"]["conversion"]["enum"]) == CONVERSIONS
    valid_shapes = (
        ("bundle_manifest", "artifacts[].sha256", "COMPUTE_SHA256", "schema.analysis_summary"),
        ("analysis_config", "qualityRisk.bhQ.danger", "COPY_CANONICAL_CONFIG", "config.qualityRisk.bhQ.danger"),
        ("producer_runtime", "producer.sourceSha256", "COPY_VERIFIED_RUNTIME", "runtime.producer.sourceSha256"),
        ("equipment_operating_ranges", "ranges[].median", "TYPE1_QUANTILE", "population.REFERENCE"),
        ("quality_risk_intervals", "rules[].grade", "COMPUTE_HOLDOUT_METRIC", "population.DISCOVERY"),
        ("quality_risk_intervals", "rules[].predicate.allOf[].values", "COMPUTE_HOLDOUT_METRIC", "population.DISCOVERY"),
        ("replay_events", "values_json.f_bfg_ratio", "DERIVE_FUEL_RATIO", "fur_hr.f_bfg_per"),
        ("analysis_summary", "driftMetrics[].reference.median", "COMPARE_DISTRIBUTIONS", "policy.LOCKED_RETROSPECTIVE_HOLDOUT"),
        ("analysis_summary", "sourceColumnProfiles[].numeric", "PROFILE_SOURCE_COLUMN", "fur_hr.f_pre_temp"),
        ("analysis_summary", "sourceColumnProfiles[].levels", "PROFILE_SOURCE_COLUMN", "fur_hr.f_pre_temp"),
        ("analysis_summary", "driftMetrics[].reference.levels", "COMPARE_DISTRIBUTIONS", "policy.LOCKED_RETROSPECTIVE_HOLDOUT"),
        ("analysis_summary", "driftMetrics[].holdout.levels", "COMPARE_DISTRIBUTIONS", "policy.LOCKED_RETROSPECTIVE_HOLDOUT"),
    )
    for artifact_role, output_field, conversion, dependency in valid_shapes:
        valid = _summary()
        valid["lineage"]["fields"][1].update(
            artifactRole=artifact_role,
            outputField=output_field,
            conversion=conversion,
            dependencies=[dependency],
            firstAvailableStage=(
                "FURNACE_EXTRACTED" if artifact_role == "replay_events" else None
            ),
        )
        validator.validate(valid)

    for mutation in (
        lambda field: field.update(outputField="unknown.path"),
        lambda field: field.update(conversion="FREE_TEXT_CONVERSION"),
        lambda field: field.update(dependencies=["config.unknown"]),
        lambda field: field.update(dependencies=["runtime.unknown"]),
        lambda field: field.update(dependencies=["fur_hr.not_a_source_column"]),
    ):
        invalid = _summary()
        mutation(invalid["lineage"]["fields"][1])
        with pytest.raises(ValidationError):
            validator.validate(invalid)

    invalid_filter = _summary()
    invalid_filter["lineage"]["aggregates"][0]["filters"] = ["field > 0"]
    invalid_transformation = _summary()
    invalid_transformation["lineage"]["aggregates"][0]["transformations"] = ["CUSTOM"]
    for invalid in (invalid_filter, invalid_transformation):
        with pytest.raises(ValidationError):
            validator.validate(invalid)


def test_lineage_source_dependency_enum_is_the_exact_50_source_terminals():
    field_schema = _load_schema("analysis_summary.schema.json")["$defs"][
        "fieldLineage"
    ]
    enum = field_schema["properties"]["dependencies"]["items"]["oneOf"][0][
        "enum"
    ]
    actual = frozenset(enum)
    assert len(enum) == len(actual) == len(LITERAL_SOURCE_DEPENDENCY_TERMINALS) == 50
    assert actual == LITERAL_SOURCE_DEPENDENCY_TERMINALS
    assert "sm_cc.slab_no" in actual


def test_range_and_risk_context_output_paths_use_exact_configured_keys():
    config = json.loads(
        (ANALYSIS_ROOT / "analysis_config.json").read_text(encoding="utf-8")
    )
    configured_range_keys = {
        key
        for hierarchy in config["rangeContextHierarchies"]
        for level in hierarchy["levels"]
        for key in level
    }
    configured_risk_keys = {
        key
        for hierarchy in config["riskAdjustmentHierarchies"]
        for level in hierarchy["levels"]
        for key in level
    }
    assert configured_range_keys == RANGE_CONTEXT_KEYS
    assert configured_risk_keys == RISK_CONTEXT_KEYS

    validator = _validator("analysis_summary.schema.json")
    for artifact_role, container, keys in (
        ("equipment_operating_ranges", "ranges[].context", RANGE_CONTEXT_KEYS),
        ("quality_risk_intervals", "rules[].applicationContext", RISK_CONTEXT_KEYS),
    ):
        for output_field in (container, *(f"{container}.{key}" for key in keys)):
            valid = _summary()
            valid["lineage"]["fields"] = [{
                "artifactRole": artifact_role,
                "outputField": output_field,
                "sourceRole": None,
                "sourceColumn": None,
                "conversion": "COPY_CANONICAL_CONFIG",
                "dependencies": ["fur_hr.f_pre_temp"],
                "firstAvailableStage": None,
            }]
            validator.validate(valid)

        forged = _summary()
        forged["lineage"]["fields"] = [{
            "artifactRole": artifact_role,
            "outputField": f"{container}.not_a_contract_key",
            "sourceRole": None,
            "sourceColumn": None,
            "conversion": "COPY_CANONICAL_CONFIG",
            "dependencies": ["fur_hr.f_pre_temp"],
            "firstAvailableStage": None,
        }]
        with pytest.raises(ValidationError):
            validator.validate(forged)


def test_replay_identifier_raw_lineage_uses_top_level_paths_and_gas_ratio_sources_exist():
    validator = _validator("analysis_summary.schema.json")
    identifier_cases = (
        ("sm_cc", "charge_id", "CAST_RECORDED", "charge_id", "COPY_SOURCE_SCALAR"),
        ("fur_hr", "slab_no", "FURNACE_CHARGED", "slab_no", "COPY_SOURCE_SCALAR"),
        ("fur_hr", "hr_coil_id", "RM4_RECORDED", "hr_coil_id", "COPY_SOURCE_SCALAR"),
        ("ap", "ap_prod_id", "AP_RECORDED_WITH_RESULT", "ap_prod_id", "COPY_SOURCE_SCALAR"),
    )
    for source_role, source_column, stage, output_field, conversion in identifier_cases:
        valid = _summary()
        valid["lineage"]["fields"][0].update(
            sourceRole=source_role,
            sourceColumn=source_column,
            firstAvailableStage=stage,
            outputField=output_field,
            conversion=conversion,
        )
        validator.validate(valid)
        _validate_summary_application_contract(valid)

        nested = copy.deepcopy(valid)
        nested["lineage"]["fields"][0]["outputField"] = f"values_json.{source_column}"
        with pytest.raises((ValidationError, AssertionError)):
            validator.validate(nested)
            _validate_summary_application_contract(nested)

    field_schema = _load_schema("analysis_summary.schema.json")["$defs"]["fieldLineage"]
    source_columns = set(field_schema["properties"]["sourceColumn"]["enum"])
    dependency_source_terminals = set(
        field_schema["properties"]["dependencies"]["items"]["oneOf"][0]["enum"]
    )
    for column in ("f_bfg_per", "f_cog_per", "f_ldg_per"):
        assert column in source_columns
        assert f"fur_hr.{column}" in dependency_source_terminals


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
    rule = next(
        item
        for item in rules["rules"]
        if item["predicate"]["allOf"] == [{
            "field": "f_pre_interval",
            "lower": 38,
            "lowerInclusive": True,
            "type": "NUMERIC_INTERVAL",
            "upper": 38,
            "upperInclusive": True,
            "values": None,
        }]
    )
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
    aggregate = next(
        item for item in summary["lineage"]["aggregates"]
        if item["ruleId"] == rule["ruleId"] and item["split"] == "DISCOVERY"
    )
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
    z = json.loads((ANALYSIS_ROOT / "analysis_config.json").read_text(encoding="utf-8"))["wilsonZ"]
    n = len(candidate_keys)
    rate = defects / n
    denominator = 1 + z * z / n
    center = (rate + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / denominator
    assert rule["discovery"]["crudeRateCiLower"] == pytest.approx(center - radius)
    assert rule["discovery"]["crudeRateCiUpper"] == pytest.approx(center + radius)
    assert rule["discovery"]["adjustedRate"] is None
    assert rule["discovery"]["reasonCode"] == "LOW_SUPPORT"


def test_golden_categorical_rule_has_literal_predicate_identity_metrics_and_memberships():
    root = CONTRACT / "golden-expectation"
    rules = json.loads(
        _replace_tokens((root / "quality_risk_intervals.template.json").read_bytes())
    )
    summary = json.loads(
        _replace_tokens((root / "analysis_summary.template.json").read_bytes())
    )
    expected_rule_id = (
        "sha256:0c1b606da50df74f380fe23ee71deaaf"
        "2220e8681fb5ff15b2952a793b9f06f3"
    )
    predicate = {
        "allOf": [{
            "field": "f_jangip_gubun",
            "lower": None,
            "lowerInclusive": None,
            "type": "CATEGORY_IN",
            "upper": None,
            "upperInclusive": None,
            "values": ["COLD"],
        }],
    }
    rule = next(item for item in rules["rules"] if item["ruleId"] == expected_rule_id)
    assert rule["predicate"] == predicate
    assert {
        key: rule[key]
        for key in (
            "analysisFamily",
            "evidenceFamily",
            "firstAvailableStage",
            "equipmentType",
            "applicationScope",
            "equipmentId",
            "applicationContext",
            "fieldNames",
            "adjustmentLevel",
            "adjustmentFieldsDropped",
            "adjustmentKind",
            "grade",
            "earlyWarningEligible",
            "displayMergeRuleIds",
        )
    } == {
        "analysisFamily": "CATEGORICAL",
        "evidenceFamily": "CHARGE",
        "firstAvailableStage": "FURNACE_CHARGED",
        "equipmentType": "FURNACE",
        "applicationScope": "PROCESS_GLOBAL",
        "equipmentId": "ALL",
        "applicationContext": {},
        "fieldNames": ["f_jangip_gubun"],
        "adjustmentLevel": 5,
        "adjustmentFieldsDropped": [
            "f_jangip_gubun",
            "f_jangip_gubun_band",
            "f_jangip_temp",
        ],
        "adjustmentKind": "UNADJUSTED_FALLBACK",
        "grade": "INSUFFICIENT_EVIDENCE",
        "earlyWarningEligible": True,
        "displayMergeRuleIds": [],
    }
    identity = {
        key: rule[key]
        for key in (
            "analysisFamily",
            "fieldNames",
            "predicate",
            "firstAvailableStage",
            "equipmentType",
            "applicationScope",
            "equipmentId",
            "applicationContext",
            "adjustmentLevel",
            "adjustmentFieldsDropped",
            "adjustmentKind",
        )
    }
    assert "sha256:" + hashlib.sha256(_canonical_json_bytes(identity)).hexdigest() == (
        expected_rule_id
    )

    fur_rows = list(csv.DictReader(io.StringIO(
        (CONTRACT / "golden-source" / "sts_2fur_hr_2.csv").read_text(
            encoding="cp949"
        )
    )))
    by_key = {
        _digest_id(
            "sfep-material-key/v1",
            {"chargeId": row["charge_id"], "slabNo": row["slab_no"]},
        )[1]: row
        for row in fur_rows
    }
    populations = {
        item["populationRef"]: item["materialKeys"]
        for item in summary["lineage"]["populations"]
    }
    selected_by_split = {
        split: [
            material_key
            for material_key in populations[split]
            if by_key[material_key]["f_jangip_gubun"] == "COLD"
        ]
        for split in ("DISCOVERY", "CONFIRMATION")
    }
    assert selected_by_split == {
        "DISCOVERY": [
            "sha256:0be5c92fa23c49ff2a32d7948db6928a472d3d50419d227d9751048bab3d6c45",
            "sha256:b313bfca80b92882d529a186bb7fc082e65020c409956481283745421ec1ce5a",
            "sha256:c51ab0d3a1baea29ad03b1e617ebec46ca635b3ba6cf930a90d0ab649a8c45e2",
        ],
        "CONFIRMATION": [],
    }
    aggregates = {
        item["split"]: item
        for item in summary["lineage"]["aggregates"]
        if item["ruleId"] == expected_rule_id
    }
    assert aggregates["DISCOVERY"]["inputMaterialKeys"] == selected_by_split["DISCOVERY"]
    assert aggregates["CONFIRMATION"]["inputMaterialKeys"] == []
    assert aggregates["DISCOVERY"]["filters"] == [
        "LABEL_AVAILABLE_AND_MATURE",
        "PREDICATE_MATCH",
        "INFORMATIVE_STRATA_ONLY",
    ]
    assert aggregates["CONFIRMATION"]["filters"] == [
        "LABEL_AVAILABLE_AND_MATURE",
        "PREDICATE_MATCH",
        "FIXED_DISCOVERY_STRATA",
        "INFORMATIVE_STRATA_ONLY",
    ]

    ap_rows = list(csv.DictReader(io.StringIO(
        (CONTRACT / "golden-source" / "sts_3ap_3.csv").read_text(encoding="cp949")
    )))
    unambiguous_judge = {
        row["hr_coil_id"]: row["judge"]
        for row in ap_rows
        if row["hr_coil_id"] != "H006"
    }
    discovery_defects = sum(
        unambiguous_judge[by_key[material_key]["hr_coil_id"]] == "불량"
        for material_key in selected_by_split["DISCOVERY"]
    )
    assert discovery_defects == 0
    assert rule["discovery"] == {
        "support": 3,
        "defects": discovery_defects,
        "crudeRate": 0,
        "crudeRateCiLower": 5.551115123125783e-17,
        "crudeRateCiUpper": 0.5614970317550454,
        "adjustedRate": None,
        "comparatorAdjustedRate": None,
        "riskDifference": None,
        "relativeRisk": None,
        "relativeRiskCiLower": None,
        "relativeRiskCiUpper": None,
        "pValue": 1,
        "qValue": 1,
        "reasonCode": "LOW_SUPPORT",
    }
    assert rule["confirmation"] == {
        "support": 0,
        "defects": 0,
        "crudeRate": None,
        "crudeRateCiLower": None,
        "crudeRateCiUpper": None,
        "adjustedRate": None,
        "comparatorAdjustedRate": None,
        "riskDifference": None,
        "relativeRisk": None,
        "relativeRiskCiLower": None,
        "relativeRiskCiUpper": None,
        "pValue": None,
        "qValue": None,
        "reasonCode": "NO_INFORMATIVE_STRATA",
    }


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
        {"name": "minimum-subnormal-open-midpoints", "hex": "0x0.0000000000001p-1022", "expected": "3e-324"},
        {"name": "minimum-normal-even-midpoints", "hex": "0x1.0000000000000p-1022", "expected": "22250738585072012e-324"},
        {"name": "eighteen-digit-fixed-tie", "hex": "0x1.6345785d8a03fp+56", "expected": "100000000000001001"},
        {"name": "negative-eighteen-digit-fixed-tie", "hex": "-0x1.6345785d8a03fp+56", "expected": "-100000000000001001"},
        {"name": "two-to-53-even-midpoint", "hex": "0x1.0000000000000p+53", "expected": "9007199254740992"},
        {"name": "negative-two-to-53-even-midpoint", "hex": "-0x1.0000000000000p+53", "expected": "-9007199254740992"},
        {"name": "two-to-54-even-midpoint", "hex": "0x1.0000000000000p+54", "expected": "18014398509481983"},
        {"name": "negative-two-to-54-even-midpoint", "hex": "-0x1.0000000000000p+54", "expected": "-18014398509481983"},
        {"name": "odd-successor-two-to-54", "hex": "0x1.0000000000001p+54", "expected": "18014398509481987"},
        {"name": "negative-odd-successor-two-to-54", "hex": "-0x1.0000000000001p+54", "expected": "-18014398509481987"},
        {"name": "positive-zero", "hex": "0x0.0p+0", "expected": "0"},
        {"name": "negative-zero", "hex": "-0x0.0p+0", "expected": "0"},
        {"name": "small-exponent", "hex": "0x1.ad7f29abcaf48p-24", "expected": "1e-7"},
        {"name": "positive-exponent", "hex": "0x1.5af1d78b58c40p+66", "expected": "1e20"},
        {"name": "maximum-finite", "hex": "0x1.fffffffffffffp+1023", "expected": "17976931348623157e292"},
        {"name": "gas-ratio-h001-ldg", "hex": "0x1.1111111111111p-3", "expected": "0.13333333333333332"},
        {"name": "gas-ratio-h002-h010-cog", "hex": "0x1.9ef4499ef449ap-3", "expected": "0.20261437908496731"},
        {"name": "gas-ratio-h002-ldg", "hex": "0x1.0bb6610bb6611p-3", "expected": "0.13071895424836601"},
        {"name": "gas-ratio-h003-cog", "hex": "0x1.af286bca1af28p-3", "expected": "0.21052631578947366"},
        {"name": "gas-ratio-h003-ldg", "hex": "0x1.286bca1af286cp-3", "expected": "0.14473684210526315"},
        {"name": "gas-ratio-h004-cog", "hex": "0x1.b9b9b9b9b9b9cp-3", "expected": "0.21568627450980392"},
        {"name": "gas-ratio-h004-ldg", "hex": "0x1.1919191919192p-3", "expected": "0.13725490196078432"},
        {"name": "gas-ratio-h005-cog", "hex": "0x1.7bc2f785ef0bep-3", "expected": "0.18543046357615893"},
        {"name": "gas-ratio-h006-cog", "hex": "0x1.81a98ef606a64p-3", "expected": "0.18831168831168831"},
        {"name": "gas-ratio-h006-ldg", "hex": "0x1.f959c427e5671p-4", "expected": "0.12337662337662337"},
        {"name": "gas-ratio-h007-cog", "hex": "0x1.ce739ce739ce7p-3", "expected": "0.22580645161290321"},
        {"name": "gas-ratio-h007-ldg", "hex": "0x1.4a5294a5294a5p-3", "expected": "0.16129032258064515"},
        {"name": "gas-ratio-h008-cog", "hex": "0x1.d89d89d89d89ep-3", "expected": "0.23076923076923077"},
        {"name": "gas-ratio-h008-ldg", "hex": "0x1.5555555555555p-3", "expected": "0.16666666666666665"},
        {"name": "gas-ratio-h009-ldg", "hex": "0x1.0f421e843d088p-3", "expected": "0.13245033112582782"},
        {"name": "gas-ratio-h010-ldg", "hex": "0x1.fca751fca7520p-4", "expected": "0.12418300653594771"},
        {"name": "gas-ratio-h011-cog", "hex": "0x1.ec8e951033d92p-3", "expected": "0.24050632911392405"},
        {"name": "gas-ratio-h011-ldg", "hex": "0x1.6aefcc26e2d5ep-3", "expected": "0.17721518987341771"},
        {"name": "gas-ratio-h012-cog", "hex": "0x1.f656f1826a43ap-3", "expected": "0.24528301886792452"},
        {"name": "gas-ratio-h012-ldg", "hex": "0x1.7588daf7f31e9p-3", "expected": "0.18238993710691822"},
    ]
    for vector in vectors:
        value = float.fromhex(vector["hex"])
        encoded = _canonical_json_bytes({"value": value}).decode("utf-8")
        assert encoded == '{"value":' + vector["expected"] + "}\n"
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="finite"):
            _canonical_json_bytes({"value": value})


def test_exact_binary64_oracle_has_no_float_formatting_or_production_import():
    oracle_source = "\n".join(
        inspect.getsource(function)
        for function in (
            _positive_binary64_fraction,
            _floor_log10_fraction,
            _ceil_fraction,
            _render_decimal_layouts,
            _independent_binary64_json_number,
        )
    )
    tree = ast.parse(oracle_source)
    prohibited_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"repr", "format", "float"}
    }
    assert prohibited_calls == set()
    module_tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for node in ast.walk(module_tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_names.add(module)
            imported_names.update(
                f"{module}.{alias.name}" if module else alias.name
                for alias in node.names
            )
    assert not {
        name for name in imported_names
        if name == "equipment_quality"
        or name.startswith("equipment_quality.")
        or name == "analysis.equipment_quality"
        or name.startswith("analysis.equipment_quality.")
    }


def test_every_current_golden_json_numeric_token_uses_the_exact_global_winner():
    root = CONTRACT / "golden-expectation"
    for path in sorted(root.glob("*.json")):
        _assert_json_numeric_tokens_are_canonical(path.read_text(encoding="utf-8"))
    for line in (root / "criteria_projection.jsonl").read_text(
        encoding="utf-8"
    ).splitlines():
        _assert_json_numeric_tokens_are_canonical(line)
    replay_rows = csv.DictReader(io.StringIO(
        (root / "replay_events.template.csv").read_text(encoding="utf-8")
    ))
    for row in replay_rows:
        if row["replay_hour"]:
            assert row["replay_hour"] == str(int(row["replay_hour"]))
        _assert_json_numeric_tokens_are_canonical(row["values_json"])


def test_spelling_only_repairs_preserve_expectation_rows_objects_counts_and_ids():
    root = CONTRACT / "golden-expectation"
    repairs = {
        "quality_risk_intervals.template.json": (
            "0.7934506856227626", "0.7934506856227625",
        ),
        "criteria_projection.jsonl": (
            "0.13333333333333333", "0.13333333333333332",
        ),
        "replay_events.template.csv": (
            "0.13333333333333333", "0.13333333333333332",
        ),
    }
    assert set(path.name for path in root.iterdir()) == {
        "analysis_summary.template.json",
        "criteria_projection.jsonl",
        "equipment_operating_ranges.template.json",
        "expected_alerts.json",
        "quality_risk_intervals.template.json",
        "replay_events.template.csv",
    }

    def surface(value: object) -> tuple[int, tuple[tuple[str, object], ...]]:
        objects = 0
        identifiers_and_counts: list[tuple[str, object]] = []

        def visit(node: object) -> None:
            nonlocal objects
            if isinstance(node, dict):
                objects += 1
                for key, child in node.items():
                    lowered = key.lower()
                    if (
                        "id" in lowered
                        or "count" in lowered
                        or lowered in {
                            "total", "support", "defects", "nondefects",
                            "unknownorcensored", "truepositive", "falsepositive",
                            "truenegative", "falsenegative",
                        }
                    ):
                        identifiers_and_counts.append((key, child))
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)

        visit(value)
        return objects, tuple(identifiers_and_counts)

    for name, (legacy_token, canonical_token) in repairs.items():
        current = (root / name).read_text(encoding="utf-8")
        assert current.count(legacy_token) == 0
        assert current.count(canonical_token) > 0
        legacy = current.replace(canonical_token, legacy_token)
        if name.endswith(".json"):
            current_value = json.loads(current)
            legacy_value = json.loads(legacy)
            assert current_value == legacy_value
            assert surface(current_value) == surface(legacy_value)
        elif name.endswith(".jsonl"):
            current_rows = [json.loads(line) for line in current.splitlines()]
            legacy_rows = [json.loads(line) for line in legacy.splitlines()]
            assert len(current_rows) == len(legacy_rows)
            assert current_rows == legacy_rows
            assert surface(current_rows) == surface(legacy_rows)
        else:
            current_rows = list(csv.DictReader(io.StringIO(current), strict=True))
            legacy_rows = list(csv.DictReader(io.StringIO(legacy), strict=True))
            assert len(current_rows) == len(legacy_rows)
            for current_row, legacy_row in zip(
                current_rows, legacy_rows, strict=True
            ):
                assert {
                    key: value for key, value in current_row.items()
                    if key != "values_json"
                } == {
                    key: value for key, value in legacy_row.items()
                    if key != "values_json"
                }
                current_values = json.loads(current_row["values_json"])
                legacy_values = json.loads(legacy_row["values_json"])
                assert current_values == legacy_values
                assert surface(current_values) == surface(legacy_values)

    unchanged_names = {
        "analysis_summary.template.json",
        "equipment_operating_ranges.template.json",
        "expected_alerts.json",
    }
    all_repair_tokens = {
        token for pair in repairs.values() for token in pair
    }
    for name in unchanged_names:
        text = (root / name).read_text(encoding="utf-8")
        assert all(text.count(token) == 0 for token in all_repair_tokens)


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
        "conversion": "COMPARE_DISTRIBUTIONS",
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


def test_output_node_dependencies_allow_consecutive_normalized_array_segments_only():
    field_schema = _load_schema("analysis_summary.schema.json")["$defs"]["fieldLineage"]
    validator = validator_for(field_schema)(field_schema)
    valid = {
        "artifactRole": "bundle_manifest",
        "outputField": "artifacts[].sha256",
        "sourceRole": None,
        "sourceColumn": None,
        "conversion": "COMPUTE_SHA256",
        "dependencies": [
            "analysis_config.fixedInteractions[][]",
            "analysis_config.rangeContextHierarchies[].levels[][]",
            "analysis_config.riskAdjustmentHierarchies[].levels[][]",
        ],
        "firstAvailableStage": None,
    }
    validator.validate(valid)

    for policy_terminal in (
        "policy.HOLDOUT_ALERT_DANGER",
        "policy.HOLDOUT_ALERT_CAUTION_OR_DANGER",
    ):
        policy = copy.deepcopy(valid)
        policy["artifactRole"] = "analysis_summary"
        policy["outputField"] = "holdoutMetrics[].alertGrade"
        policy["conversion"] = "COPY_POLICY_VALUE"
        policy["dependencies"] = [policy_terminal]
        validator.validate(policy)

    for malformed in (
        "analysis_config.fixedInteractions[",
        "analysis_config.fixedInteractions[]]",
        "analysis_config.fixedInteractions[0]",
        "analysis_config.fixedInteractions[][][]x",
    ):
        invalid = copy.deepcopy(valid)
        invalid["dependencies"] = [malformed]
        with pytest.raises(ValidationError):
            validator.validate(invalid)

    arbitrary_policy = copy.deepcopy(valid)
    arbitrary_policy["dependencies"] = ["policy.HOLDOUT_ALERT_WARNING"]
    with pytest.raises(ValidationError):
        validator.validate(arbitrary_policy)


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
    config = json.loads((ANALYSIS_ROOT / "analysis_config.json").read_text(encoding="utf-8"))
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
    config = json.loads((ANALYSIS_ROOT / "analysis_config.json").read_text(encoding="utf-8"))
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
    source_material_key = _digest_id(
        "sfep-material-key/v1",
        {"chargeId": source_row["charge_id"], "slabNo": source_row["slab_no"]},
    )[1]
    cast_row = next(
        row for row in replay_rows
        if row["material_key"] == source_material_key
        and row["batch_step"] == "CAST_RECORDED"
    )
    cast_values = json.loads(cast_row["values_json"])
    projection = [
        json.loads(line)
        for line in (
            CONTRACT / "golden-expectation" / "criteria_projection.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    mature_features = next(
        item["features"]
        for item in projection
        if item["kind"] == "MATURE_QUALITY_INPUT"
        and item["chargeId"] == source_row["charge_id"]
        and item["slabNo"] == source_row["slab_no"]
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
    assert summary["quarantineCounts"] == {"DUPLICATE_AP_KEY": 2, "UNLINKED_AP": 1}
    assert summary["chargePurgeCounts"] == {
        "outer": {"chargeCount": 0, "rowCount": 0},
        "inner": {"chargeCount": 0, "rowCount": 0},
    }
    assert [item["alertGrade"] for item in summary["holdoutMetrics"]] == [
        "DANGER", "CAUTION_OR_DANGER",
    ]
    material_key_by_coil = {
        coil_id: _digest_id(
            "sfep-material-key/v1", {"chargeId": charge_id, "slabNo": slab_no}
        )[1]
        for charge_id, slab_no, coil_id, _ in fur_materials
    }
    assert {
        item["materialKey"] for item in summary["lineage"]["materials"]
    } == set(material_key_by_coil.values())
    assert len(summary["lineage"]["materials"]) == 12
    assert len(summary["lineage"]["aggregates"]) == 330
    assert {
        item["populationRef"]: item["materialKeys"]
        for item in summary["lineage"]["populations"]
    } == {
        "REFERENCE": sorted(
            material_key_by_coil[coil]
            for coil, (split, _) in expected_outcomes.items()
            if split != "HOLDOUT"
        ),
        "DISCOVERY": sorted(
            material_key_by_coil[coil]
            for coil, (split, _) in expected_outcomes.items()
            if split == "DISCOVERY"
        ),
        "CONFIRMATION": sorted(
            material_key_by_coil[coil]
            for coil, (split, _) in expected_outcomes.items()
            if split == "CONFIRMATION"
        ),
        "HOLDOUT": sorted(
            material_key_by_coil[coil]
            for coil, (split, _) in expected_outcomes.items()
            if split == "HOLDOUT"
        ),
    }
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
    assert expected_alerts["expectedReplayEventCount"] == 95
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
    core_steps = [
        "CAST_RECORDED", "FURNACE_CHARGED", "PREHEAT_COMPLETE", "HEAT_COMPLETE",
        "SOAK_COMPLETE", "FURNACE_EXTRACTED", "RM4_RECORDED",
    ]
    steps_by_material: dict[str, list[str]] = {}
    for row in rows:
        steps_by_material.setdefault(row["material_key"], []).append(row["batch_step"])
    assert len(steps_by_material) == 12
    assert sum(steps == core_steps for steps in steps_by_material.values()) == 1
    assert sum(
        steps == [*core_steps, "AP_RECORDED_WITH_RESULT"]
        for steps in steps_by_material.values()
    ) == 11
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


def _isolated_golden_oracle_root(tmp_path: Path) -> Path:
    root = tmp_path / "oracle-root"
    source_root = root / "golden-source"
    source_root.mkdir(parents=True)
    shutil.copyfile(ANALYSIS_ROOT / "analysis_config.json", root / "analysis_config.json")
    for name in ("sts_1sm_cc_1.csv", "sts_2fur_hr_2.csv", "sts_3ap_3.csv"):
        shutil.copyfile(CONTRACT / "golden-source" / name, source_root / name)
    assert sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    ) == [
        "analysis_config.json",
        "golden-source/sts_1sm_cc_1.csv",
        "golden-source/sts_2fur_hr_2.csv",
        "golden-source/sts_3ap_3.csv",
    ]
    return root


def _golden_oracle_module():
    try:
        from tests.oracles import golden_semantics
    except ModuleNotFoundError as error:
        if error.name not in {"tests", "tests.oracles"}:
            raise
        from oracles import golden_semantics
    return golden_semantics


def _oracle_material(
    day: str,
    *,
    charge: str,
    slab: str,
    judge: str | None = "양품",
    ap_date: str | None = None,
    ap_linked: bool = True,
    **values: object,
) -> dict[str, object]:
    merged_values = {
        "hr_date": day,
        "cast_date": day,
        "f_ext_date": day,
        "f_ext_time": 7,
        "ap_date": ap_date or day,
        "judge": judge,
        "sm_plant": "SM1",
        "furnace_no": "1",
        "ap_plant": "AP1",
        "ap_prod_id": "P-" + slab,
        **values,
    }
    return {
        "material_key": "sha256:" + hashlib.sha256(
            f"{charge}\0{slab}".encode("utf-8")
        ).hexdigest(),
        "charge_id": charge,
        "slab_no": slab,
        "hr_coil_id": "H-" + slab,
        "values": merged_values,
        "ap": {} if ap_linked else None,
        "sm": {},
        "fur": {},
    }


def test_oracle_derives_whole_date_boundaries_charge_purges_and_censor_precedence():
    oracle = _golden_oracle_module()

    boundary_rows = [
        _oracle_material("2025-01-01", charge="CROSS", slab="1"),
        _oracle_material("2025-01-02", charge="A", slab="1"),
        _oracle_material("2025-01-02", charge="A", slab="2"),
        _oracle_material("2025-01-03", charge="CROSS", slab="2"),
    ]
    cutoff = oracle._whole_date_boundary(
        boundary_rows, "hr_date", Fraction(1, 2)
    )
    assert cutoff == date(2025, 1, 2)
    earlier = [row for row in boundary_rows if row["values"]["hr_date"] <= cutoff.isoformat()]
    later = [row for row in boundary_rows if row["values"]["hr_date"] > cutoff.isoformat()]
    earlier, later, purge = oracle._purge_crossing_charges(earlier, later)
    assert [row["charge_id"] for row in earlier] == ["A", "A"]
    assert later == []
    assert purge == {"chargeCount": 1, "rowCount": 2}

    config = {
        "labelMaturityDays": 0,
        "splits": {"referenceFraction": 1.0, "discoveryFraction": 0.5},
    }
    materials = [
        _oracle_material("2025-01-01", charge="A", slab="1", ap_linked=False),
        _oracle_material("2025-01-02", charge="B", slab="1", ap_date="2025-01-05"),
        _oracle_material("2025-01-03", charge="C", slab="1", judge=None),
        _oracle_material("2025-01-03", charge="D", slab="1"),
    ]
    split = oracle._split_materials(materials, config, materials)
    assert split["AS_OF"] == date(2025, 1, 3)
    assert split["CENSOR_COUNTS"] == {
        "AP_UNLINKED": 1,
        "LABEL_MISSING": 1,
        "LABEL_NOT_YET_AVAILABLE": 1,
    }
    assert split["PURGE_COUNTS"]["outer"] == {"chargeCount": 0, "rowCount": 0}


def test_oracle_derives_stage_safe_operating_range_group_and_tail_policy():
    oracle = _golden_oracle_module()

    config = {
        "fields": [{
            "field": "temperature",
            "featureRole": "DIRECT_OPERATION",
            "dataType": "NUMBER",
            "firstAvailableStage": "CAST_RECORDED",
            "equipmentType": "SM_CC",
        }],
        "rangeContextHierarchies": [{
            "equipmentType": "SM_CC",
            "levels": [["group"], []],
        }],
        "operatingRanges": {
            "minimumSupport": 2,
            "extremeTailMinimumSupport": 4,
            "typicalLowerQuantile": 0.05,
            "typicalUpperQuantile": 0.95,
            "extremeLowerQuantile": 0.01,
            "extremeUpperQuantile": 0.99,
        },
    }
    rows = [
        _oracle_material("2025-01-01", charge="A", slab="1", temperature=10, group="G"),
        _oracle_material("2025-01-02", charge="B", slab="1", temperature=20, group="G"),
        _oracle_material("2025-01-03", charge="C", slab="1", temperature=99, group="G"),
    ]
    rows[-1]["values"]["cast_date"] = "2025-02-01"
    rules, memberships = oracle._operating_range_rules(
        config, rows, date(2025, 1, 31)
    )
    rule = next(item for item in rules if item["context"] == {"group": "G"})
    assert rule["contextLevel"] == 0
    assert rule["support"] == 2
    assert (rule["p05"], rule["median"], rule["p95"]) == (10, 10, 20)
    assert (rule["p01"], rule["p99"]) == (None, None)
    assert not rule["lowerTailEnabled"] and not rule["upperTailEnabled"]
    assert len(memberships[rule["ruleId"]]) == 2
    duplicate_cuts = oracle._quartile_cuts([1, 1, 1, 2])
    assert duplicate_cuts == (1, 1, 1)
    assert oracle._band(1, duplicate_cuts) == "Q1"
    assert oracle._band(2, duplicate_cuts) == "Q4"


def _oracle_quality_config() -> dict[str, object]:
    return {
        "fields": [
            {
                "field": "x", "sourceRole": "sm_cc", "sourceColumn": "x",
                "dataType": "NUMBER", "featureRole": "DIRECT_OPERATION",
                "equipmentType": "SM_CC", "firstAvailableStage": "CAST_RECORDED",
                "evidenceFamily": "TEST", "dependencies": [],
            },
            {
                "field": "group", "sourceRole": "sm_cc", "sourceColumn": "group",
                "dataType": "STRING", "featureRole": "CONTEXT",
                "equipmentType": "SM_CC", "firstAvailableStage": "CAST_RECORDED",
                "evidenceFamily": "TEST", "dependencies": [],
            },
        ],
        "fixedInteractions": [],
        "riskAdjustmentHierarchies": [{
            "equipmentType": "SM_CC", "levels": [["group"], []],
        }],
        "qualityRisk": {
            "numericBins": 2, "interactionBins": 2,
            "minimumDiscoverySupport": 2, "minimumInformativeStrata": 2,
            "minimumConfirmationSupport": 1, "minimumCautionDefects": 1,
            "minimumDangerDefects": 2, "minimumConfirmationDefects": 1,
            "zeroCellCorrection": 0.5,
            "bhQ": {"caution": 0.1, "danger": 0.05},
            "relativeRisk": {"caution": 1.5, "danger": 2.0},
            "riskDifference": {"caution": 0.005, "danger": 0.01},
            "confirmationRelativeRisk": {"cautionExclusive": 1.0, "danger": 1.5},
        },
        "fdrFamilies": ["NUMERIC", "CATEGORICAL", "INTERACTION"],
        "evidenceFamilies": ["TEST"],
        "bootstrap": {"replicates": 20, "minimumValidReplicates": 10},
        "wilsonZ": 1.959963984540054,
    }


def test_oracle_selects_configured_quality_strata_and_global_fallback_from_rows():
    oracle = _golden_oracle_module()

    config = _oracle_quality_config()
    discovery = [
        _oracle_material(
            "2025-01-0" + str(index + 1),
            charge="C" + str(index),
            slab="1",
            judge="불량" if index in {0, 4} else "양품",
            x=index % 2,
            group="A" if index < 4 else "B",
        )
        for index in range(8)
    ]
    rules, _memberships, _lineage = oracle._quality_rules(
        config,
        {"DISCOVERY": discovery, "CONFIRMATION": discovery},
    )
    first_x = next(
        rule for rule in rules
        if rule["analysisFamily"] == "NUMERIC"
        and rule["fieldNames"] == ["x"]
        and rule["predicate"]["allOf"][0]["upper"] == 0
    )
    assert first_x["adjustmentLevel"] == 0
    assert first_x["adjustmentKind"] == "STRATIFIED"
    assert first_x["discovery"]["support"] == 4

    fallback_rows = [
        _oracle_material("2025-01-01", charge="A", slab="1", x=0, group="A"),
        _oracle_material("2025-01-02", charge="B", slab="1", x=1, group="B"),
    ]
    fallback_config = copy.deepcopy(config)
    fallback_config["qualityRisk"]["minimumDiscoverySupport"] = 1
    fallback_rules, _, _ = oracle._quality_rules(
        fallback_config,
        {"DISCOVERY": fallback_rows, "CONFIRMATION": fallback_rows},
    )
    fallback_x = next(
        rule for rule in fallback_rules
        if rule["analysisFamily"] == "NUMERIC"
        and rule["fieldNames"] == ["x"]
        and rule["predicate"]["allOf"][0]["upper"] == 0
    )
    assert fallback_x["adjustmentLevel"] == 1
    assert fallback_x["adjustmentKind"] == "UNADJUSTED_FALLBACK"


def test_oracle_quality_metadata_drops_and_interaction_guards_are_config_driven():
    oracle = _golden_oracle_module()

    definitions = {
        "raw_x": {
            "field": "raw_x", "dataType": "STRING", "featureRole": "CONTEXT",
            "equipmentType": "SM_CC", "firstAvailableStage": "CAST_RECORDED",
            "dependencies": [],
        },
        "x": {
            "field": "x", "dataType": "NUMBER", "featureRole": "DIRECT_OPERATION",
            "equipmentType": "SM_CC", "firstAvailableStage": "CAST_RECORDED",
            "dependencies": ["raw_x"],
        },
        "derived_x": {
            "field": "derived_x", "dataType": "NUMBER",
            "featureRole": "PRODUCT_STATE_REFERENCE", "equipmentType": "SM_CC",
            "firstAvailableStage": "CAST_RECORDED", "dependencies": ["x"],
        },
        "same_stage_proxy": {
            "field": "same_stage_proxy", "dataType": "NUMBER",
            "featureRole": "DIRECT_OPERATION", "equipmentType": "SM_CC",
            "firstAvailableStage": "CAST_RECORDED", "dependencies": [],
        },
        "future": {
            "field": "future", "dataType": "NUMBER", "featureRole": "CONTEXT",
            "equipmentType": "SM_CC", "firstAvailableStage": "FURNACE_CHARGED",
            "dependencies": [],
        },
    }
    hierarchy = {
        "equipmentType": "SM_CC",
        "levels": [["future_band"], []],
    }
    assert oracle._dropped_adjustment_fields(
        ("x",), "SM_CC", "NUMERIC", definitions, hierarchy,
    ) == [
        "derived_x", "future_band", "raw_x", "same_stage_proxy", "x", "x_band",
    ]

    ap_definitions = {
        "speed": {
            "field": "speed", "dataType": "NUMBER", "featureRole": "DIRECT_OPERATION",
            "equipmentType": "AP", "firstAvailableStage": "AP_RECORDED_WITH_RESULT",
            "dependencies": [],
        },
        "custom_dimension": {
            "field": "custom_dimension", "dataType": "NUMBER",
            "featureRole": "PRODUCT_STATE_REFERENCE", "equipmentType": "AP",
            "firstAvailableStage": "AP_RECORDED_WITH_RESULT", "dependencies": [],
        },
    }
    assert "custom_dimension_band" in oracle._dropped_adjustment_fields(
        ("speed",), "AP", "NUMERIC", ap_definitions,
        {"equipmentType": "AP", "levels": [[]]},
    )

    config = _oracle_quality_config()
    config["fields"].append({
        "field": "line_id", "sourceRole": "sm_cc", "sourceColumn": "line_id",
        "dataType": "STRING", "featureRole": "EQUIPMENT_IDENTIFIER",
        "equipmentType": "SM_CC", "firstAvailableStage": "CAST_RECORDED",
        "evidenceFamily": "TEST", "dependencies": [],
    })
    rows = [
        _oracle_material("2025-01-01", charge="A", slab="1", x=0, group="A", line_id="L1"),
        _oracle_material("2025-01-02", charge="B", slab="1", x=1, group="A", line_id="L2"),
    ]
    rules, _, _ = oracle._quality_rules(
        config, {"DISCOVERY": rows, "CONFIRMATION": rows},
    )
    line_rule = next(
        rule for rule in rules
        if rule["analysisFamily"] == "CATEGORICAL"
        and rule["fieldNames"] == ["line_id"]
        and rule["predicate"]["allOf"][0]["values"] == ["L1"]
    )
    assert line_rule["applicationScope"] == "EQUIPMENT_SPECIFIC"
    assert line_rule["equipmentId"] == "L1"

    interaction_config = _oracle_quality_config()
    interaction_config["fields"].append({
        "field": "y", "sourceRole": "sm_cc", "sourceColumn": "y",
        "dataType": "NUMBER", "featureRole": "DIRECT_OPERATION",
        "equipmentType": "SM_CC", "firstAvailableStage": "CAST_RECORDED",
        "evidenceFamily": "TEST", "dependencies": [],
    })
    interaction_config["fixedInteractions"] = [["x", "y"]]
    constant_axis_rows = [
        _oracle_material(
            f"2025-01-0{index + 1}", charge=f"C{index}", slab="1",
            x=index % 2, y=1, group="A",
        )
        for index in range(4)
    ]
    interaction_rules, _, _ = oracle._quality_rules(
        interaction_config,
        {"DISCOVERY": constant_axis_rows, "CONFIRMATION": constant_axis_rows},
    )
    assert not any(rule["analysisFamily"] == "INTERACTION" for rule in interaction_rules)
    missing_axis_rows = [
        _oracle_material(
            f"2025-01-0{index + 1}", charge=f"M{index}", slab="1",
            x=index % 2, y=None, group="A",
        )
        for index in range(4)
    ]
    missing_axis_rules, _, _ = oracle._quality_rules(
        interaction_config,
        {"DISCOVERY": missing_axis_rows, "CONFIRMATION": missing_axis_rows},
    )
    assert not any(rule["analysisFamily"] == "INTERACTION" for rule in missing_axis_rules)


def test_oracle_low_defect_metric_retains_computed_adjusted_statistics():
    oracle = _golden_oracle_module()
    rows = [
        _oracle_material("2025-01-01", charge="A", slab="1", x=1, judge="불량"),
        _oracle_material("2025-01-02", charge="B", slab="1", x=1, judge="양품"),
        _oracle_material("2025-01-03", charge="C", slab="1", x=0, judge="양품"),
        _oracle_material("2025-01-04", charge="D", slab="1", x=0, judge="양품"),
    ]
    term = ({
        "field": "x", "type": "NUMERIC_INTERVAL", "lower": 1,
        "lowerInclusive": True, "upper": 1, "upperInclusive": True,
        "values": None,
    },)
    policy = copy.deepcopy(_oracle_quality_config()["qualityRisk"])
    policy["minimumDiscoverySupport"] = 1
    policy["minimumCautionDefects"] = 2
    metric, _ = oracle._metric(
        rows, term, (), {}, {(): Fraction(1, 1)}, 1.959963984540054,
        policy, discovery=True,
    )
    assert metric["reasonCode"] == "LOW_DEFECT_COUNT"
    assert metric["adjustedRate"] == 0.5
    assert metric["comparatorAdjustedRate"] == 0
    assert metric["riskDifference"] == 0.5
    assert metric["relativeRisk"] is not None
    assert metric["pValue"] is not None


def test_oracle_derives_grade_alert_matching_and_charge_bootstrap_metrics():
    oracle = _golden_oracle_module()

    policy = _oracle_quality_config()["qualityRisk"]
    discovery = {
        "support": 200, "defects": 10, "relativeRisk": 2.1,
        "relativeRiskCiLower": 1.1, "riskDifference": 0.02, "qValue": 0.04,
        "reasonCode": "NONE",
    }
    confirmation = {
        "support": 100, "defects": 5, "relativeRisk": 1.6,
        "riskDifference": 0.01, "reasonCode": "NONE",
    }
    assert oracle._grade_rule(discovery, confirmation, "STRATIFIED", policy) == "DANGER"
    assert oracle._grade_rule(discovery, confirmation, "UNADJUSTED_FALLBACK", policy) == "CAUTION"
    low = dict(discovery, reasonCode="LOW_SUPPORT")
    assert oracle._grade_rule(low, confirmation, "STRATIFIED", policy) == "INSUFFICIENT_EVIDENCE"
    not_repeated = dict(confirmation, relativeRisk=0.9, riskDifference=0.0)
    assert oracle._grade_rule(
        discovery, not_repeated, "STRATIFIED", policy,
    ) == "UNCONFIRMED"
    assert not_repeated["reasonCode"] == "DIRECTION_NOT_REPEATED"

    rule = {
        "grade": "DANGER", "earlyWarningEligible": True,
        "firstAvailableStage": "CAST_RECORDED",
        "predicate": {"allOf": [{
            "field": "x", "type": "NUMERIC_INTERVAL", "lower": 1,
            "lowerInclusive": True, "upper": 1, "upperInclusive": True,
            "values": None,
        }]},
    }
    holdout = [
        _oracle_material("2025-02-02", charge="A", slab="1", judge="불량", x=1),
        _oracle_material("2025-02-02", charge="A", slab="2", judge="양품", x=0),
        _oracle_material("2025-02-03", charge="B", slab="1", judge="양품", x=1),
    ]
    config = _oracle_quality_config()
    profiles = oracle._holdout_profiles(
        config, {"HOLDOUT": holdout}, [rule], "sha256:" + "1" * 64,
        date(2025, 2, 1),
    )
    danger = profiles[0]
    assert (danger["total"], danger["truePositive"], danger["falsePositive"]) == (3, 1, 1)
    assert danger["alertRate"]["pointEstimate"] == 2 / 3
    assert danger["precision"]["pointEstimate"] == 1 / 2
    assert danger["baseDefectRate"]["pointEstimate"] == 1 / 3
    assert 0 < danger["alertRate"]["validReplicates"] <= 20

    assert oracle._matching_alert_material_keys(
        holdout, [dict(rule, grade="NORMAL")], {"DANGER"}, date(2025, 2, 1)
    ) == set()
    seed = hashlib.sha256(
        ("sha256:" + "1" * 64).encode("ascii")
        + b"\0holdout-bootstrap-v1"
    ).digest()
    assert Counter(
        2 - sum(oracle._sample_charge_indices(seed, replicate, 2))
        for replicate in range(2_000)
    ) == {0: 499, 1: 1_009, 2: 492}
    assert not oracle._bootstrap_applicable(
        {"support": 3, "reasonCode": "LOW_SUPPORT"}, policy,
    )
    assert oracle._display_intervals_are_adjacent(
        {"upper": 1, "upperInclusive": True},
        {"lower": 1, "lowerInclusive": False},
    )
    assert not oracle._display_intervals_are_adjacent(
        {"upper": 1, "upperInclusive": False},
        {"lower": 1, "lowerInclusive": False},
    )
    assert not oracle._display_intervals_are_adjacent(
        {"upper": 1, "upperInclusive": True},
        {"lower": 1, "lowerInclusive": True},
    )


def test_oracle_derives_replay_count_and_date_span_from_emitted_events():
    oracle = _golden_oracle_module()

    material = _oracle_material(
        "2025-01-10",
        charge="A",
        slab="1",
        ap_date="2025-02-01",
    )
    material["values"]["cast_date"] = "2025-01-01"
    config = {"fields": []}
    records = oracle._replay_event_records(config, [material])
    assert len(records) == 8
    assert {record["batch_step"] for record in records} == {
        "CAST_RECORDED",
        "FURNACE_CHARGED",
        "PREHEAT_COMPLETE",
        "HEAT_COMPLETE",
        "SOAK_COMPLETE",
        "FURNACE_EXTRACTED",
        "RM4_RECORDED",
        "AP_RECORDED_WITH_RESULT",
    }
    assert min(record["replay_date"] for record in records) == "2025-01-01"
    assert max(record["replay_date"] for record in records) == "2025-02-01"

    material["ap"] = None
    assert len(oracle._replay_event_records(config, [material])) == 7


def test_oracle_quality_lineage_is_concrete_first_and_leaf_exact(tmp_path):
    oracle = _golden_oracle_module()

    root = _isolated_golden_oracle_root(tmp_path)
    config, tables = oracle._load_inputs(root)
    materials, _quarantine = oracle._derive_materials(config, tables)
    split = oracle._split_materials(materials, config, oracle._fur_boundary_rows(tables))
    rules, _memberships, normalized = oracle._quality_rules(config, split)
    concrete = oracle._concrete_quality_lineage(rules, config)
    manually_normalized: dict[str, set[str]] = {}
    for trace in concrete.values():
        for path, dependencies in trace.items():
            manually_normalized.setdefault(path, set()).update(dependencies)
    assert normalized == {
        path: sorted(dependencies, key=lambda value: value.encode("utf-8"))
        for path, dependencies in manually_normalized.items()
    }
    assert len(concrete) == len(rules) == 165
    assert oracle._quality_bootstrap_task_count(rules, config) == 0

    metric_paths = [
        path for path in normalized
        if path.startswith("rules[].discovery.")
        or path.startswith("rules[].confirmation.")
    ]
    candidate_paths = [
        path for path in normalized
        if path.startswith("rules[].")
        and not path.startswith(("rules[].discovery.", "rules[].confirmation."))
        and path not in {
            "rules[].ruleId", "rules[].grade", "rules[].earlyWarningEligible",
            "rules[].displayMergeRuleIds[]",
        }
    ]
    assert len({tuple(normalized[path]) for path in metric_paths}) > 8
    assert len({tuple(normalized[path]) for path in candidate_paths}) > 8

    for split_name in ("discovery", "confirmation"):
        support = set(normalized[f"rules[].{split_name}.support"])
        defects = set(normalized[f"rules[].{split_name}.defects"])
        crude = set(normalized[f"rules[].{split_name}.crudeRate"])
        lower = set(normalized[f"rules[].{split_name}.crudeRateCiLower"])
        reason = set(normalized[f"rules[].{split_name}.reasonCode"])
        assert "replay_events.values_json.judge" not in support
        assert not any("wilsonZ" in value or "fdrFamilies" in value for value in support)
        assert not any("minimum" in value for value in support)
        assert defects == support | {"replay_events.values_json.judge"}
        assert crude == {
            f"quality_risk_intervals.rules[].{split_name}.defects",
            f"quality_risk_intervals.rules[].{split_name}.support",
        }
        assert lower == crude | {"config.wilsonZ"}
        assert "config.wilsonZ" not in reason
        assert "config.fdrFamilies[]" not in reason
        assert not any("Defects" in value for value in reason)

    assert set(normalized["rules[].discovery.qValue"]) == {
        "config.fdrFamilies[]",
        "quality_risk_intervals.rules[].analysisFamily",
        "quality_risk_intervals.rules[].discovery.pValue",
    }
    assert normalized["rules[].confirmation.qValue"] == [
        "quality_risk_intervals.rules[].confirmation.reasonCode",
    ]
    assert normalized["rules[].grade"] == [
        "quality_risk_intervals.rules[].discovery.reasonCode",
    ]

    quality_nodes = {
        "replay_events.values_json." + definition["field"]
        for definition in config["fields"]
        if definition["featureRole"] in oracle._QUALITY_ROLES
    }
    numeric_nodes = {
        "replay_events.values_json." + definition["field"]
        for definition in config["fields"]
        if definition["featureRole"] in oracle._QUALITY_ROLES
        and definition["dataType"] == "NUMBER"
    }
    predicate_nodes = {
        "quality_risk_intervals.rules[].predicate.allOf[]." + suffix
        for suffix in (
            "field", "type", "lower", "lowerInclusive", "upper",
            "upperInclusive", "values[]",
        )
    }
    metric_base = {
        "population.DISCOVERY",
        "quality_risk_intervals.rules[].adjustmentKind",
        *predicate_nodes,
        *quality_nodes,
    }
    assert set(normalized["rules[].discovery.reasonCode"]) == metric_base | {
        "config.qualityRisk.minimumDiscoverySupport",
        "quality_risk_intervals.rules[].discovery.support",
    }
    assert set(normalized["rules[].confirmation.reasonCode"]) == metric_base | {
        "config.qualityRisk.minimumConfirmationSupport",
        "population.CONFIRMATION",
        "quality_risk_intervals.rules[].confirmation.support",
    }
    for path in metric_paths:
        if path != "rules[].discovery.qValue":
            assert "config.fdrFamilies[]" not in normalized[path], path

    assert set(normalized["rules[].predicate.allOf[].lower"]) & quality_nodes == numeric_nodes
    assert set(normalized["rules[].predicate.allOf[].values[]"]) & quality_nodes == quality_nodes - numeric_nodes

    for path, metadata in (
        ("rules[].firstAvailableStage", "firstAvailableStage"),
        ("rules[].equipmentType", "equipmentType"),
        ("rules[].evidenceFamily", "evidenceFamily"),
    ):
        assert set(normalized[path]) == {
            "config.fields[].field",
            "config.fields[]." + metadata,
            "quality_risk_intervals.rules[].fieldNames[]",
        }
        assert not any(
            dependency.startswith(("population.", "replay_events."))
            or "qualityRisk" in dependency
            for dependency in normalized[path]
        )
    dropped = set(normalized["rules[].adjustmentFieldsDropped[]"])
    assert not any(
        dependency.startswith(("population.", "replay_events."))
        or "qualityRisk" in dependency
        for dependency in dropped
    )

    display_dependencies = {
        "quality_risk_intervals.rules[]." + suffix
        for suffix in (
            "adjustmentFieldsDropped[]", "adjustmentKind", "adjustmentLevel",
            "analysisFamily", "applicationContext", "applicationScope",
            "equipmentId", "equipmentType", "fieldNames[]",
            "firstAvailableStage", "grade", "predicate.allOf[].lower",
            "predicate.allOf[].lowerInclusive", "predicate.allOf[].upper",
            "predicate.allOf[].upperInclusive", "ruleId",
        )
    }
    assert set(normalized["rules[].displayMergeRuleIds[]"]) == display_dependencies
    assert not any(
        dependency.startswith("config.")
        or ".discovery." in dependency
        or ".confirmation." in dependency
        for dependency in display_dependencies
    )

    representatives = {
        family: next(rule for rule in rules if rule["analysisFamily"] == family)
        for family in ("NUMERIC", "CATEGORICAL", "INTERACTION")
    }
    for family, rule in representatives.items():
        axes = {
            "replay_events.values_json." + term["field"]
            for term in rule["predicate"]["allOf"]
        }
        support = set(concrete[rule["ruleId"]]["rules[].discovery.support"])
        assert support & quality_nodes == axes, family

    fallback = next(
        rule for rule in rules
        if rule["analysisFamily"] == "NUMERIC"
        and rule["fieldNames"] == ["f_pre_temp"]
    )
    adjustment = set(
        concrete[fallback["ruleId"]]["rules[].adjustmentKind"]
    )
    assert adjustment & quality_nodes == {
        "replay_events.values_json.f_jangip_gubun",
        "replay_events.values_json.f_pre_temp",
        "replay_events.values_json.furnace_no",
        "replay_events.values_json.slab_width",
        "replay_events.values_json.steel_grade",
        "replay_events.values_json.steel_usage",
    }
    traced_display = {
        rule["ruleId"]
        for rule in rules
        if "rules[].displayMergeRuleIds[]" in concrete[rule["ruleId"]]
    }
    assert traced_display == {
        rule["ruleId"] for rule in rules if rule["displayMergeRuleIds"]
    }
    assert len(traced_display) == 113
    confirmation_no_information = next(
        rule for rule in rules
        if rule["confirmation"]["reasonCode"] == "NO_INFORMATIVE_STRATA"
    )
    no_information_dependencies = set(
        concrete[confirmation_no_information["ruleId"]][
            "rules[].confirmation.reasonCode"
        ]
    )
    assert "config.qualityRisk.minimumConfirmationSupport" not in (
        no_information_dependencies
    )
    assert "quality_risk_intervals.rules[].confirmation.support" not in (
        no_information_dependencies
    )
    confirmation_low_support = next(
        rule for rule in rules
        if rule["confirmation"]["reasonCode"] == "LOW_SUPPORT"
    )
    low_support_dependencies = set(
        concrete[confirmation_low_support["ruleId"]][
            "rules[].confirmation.reasonCode"
        ]
    )
    assert {
        "config.qualityRisk.minimumConfirmationSupport",
        "quality_risk_intervals.rules[].confirmation.support",
    } <= low_support_dependencies


_ORACLE_ALLOWED_IMPORT_ROOTS = frozenset({
    "__future__",
    "csv",
    "datetime",
    "fractions",
    "functools",
    "hashlib",
    "io",
    "json",
    "math",
    "pathlib",
    "struct",
    "types",
})
_ORACLE_FORBIDDEN_CALL_NAMES = frozenset({
    "__import__",
    "ArgumentParser",
    "compile",
    "copy",
    "copyfile",
    "copytree",
    "delattr",
    "eval",
    "exec",
    "format",
    "getattr",
    "globals",
    "hardlink_to",
    "hasattr",
    "import_module",
    "link",
    "locals",
    "makedirs",
    "mkdir",
    "mknod",
    "move",
    "open",
    "parse_args",
    "parse_known_args",
    "remove",
    "removedirs",
    "rename",
    "renames",
    "replace",
    "repr",
    "rmdir",
    "setattr",
    "symlink",
    "symlink_to",
    "touch",
    "truncate",
    "unlink",
    "vars",
    "write",
    "write_bytes",
    "write_text",
    "writelines",
})


def _assert_read_only_oracle_ast(source: str) -> None:
    tree = ast.parse(source)
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                assert root in _ORACLE_ALLOWED_IMPORT_ROOTS, alias.name
                aliases[alias.asname or root] = alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            module = node.module or ""
            root = module.split(".", 1)[0]
            assert root in _ORACLE_ALLOWED_IMPORT_ROOTS, module
            for alias in node.names:
                assert alias.name != "*"
                aliases[alias.asname or alias.name] = f"{module}.{alias.name}"

    def resolved_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            prefix = resolved_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        if isinstance(node, ast.Call):
            return resolved_name(node.func)
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = resolved_name(node.func)
        final_name = name.rsplit(".", 1)[-1] if name else ""
        assert final_name not in _ORACLE_FORBIDDEN_CALL_NAMES, name


_READ_ONLY_ORACLE_CHILD = r'''
import os
import sys

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_MUTATION_EVENTS = {
    "os.chmod", "os.chown", "os.link", "os.mkdir", "os.remove", "os.rename",
    "os.rmdir", "os.symlink", "os.truncate", "os.utime", "shutil.copyfile",
    "shutil.copytree", "shutil.move", "tempfile.mkstemp", "tempfile.mkdtemp",
}

def _deny_filesystem_mutation(event, args):
    if event == "open":
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        writes = isinstance(mode, str) and any(char in mode for char in "wax+")
        writes = writes or isinstance(flags, int) and bool(flags & _WRITE_FLAGS)
        if writes:
            raise PermissionError("oracle filesystem mutation forbidden")
    elif event in _MUTATION_EVENTS:
        raise PermissionError("oracle filesystem mutation forbidden")

sys.addaudithook(_deny_filesystem_mutation)

if len(sys.argv) == 3:
    with open(sys.argv[2], "w", encoding="utf-8") as stream:
        stream.write("forbidden")
else:
    from pathlib import Path
    from tests.oracles.golden_semantics import build_golden_semantics
    result = build_golden_semantics(Path(sys.argv[1]))
    assert len(result) == 6
    assert all(type(value) is bytes for value in result.values())
    print("oracle-read-only-audit-ok")
'''


def _run_read_only_oracle_child(
    root: Path,
    *,
    write_probe: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-B", "-c", _READ_ONLY_ORACLE_CHILD, str(root)]
    if write_probe is not None:
        command.append(str(write_probe))
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    return subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "source",
    [
        "import os as harmless\nharmless.remove('victim')\n",
        "import analysis.equipment_quality.schema as harmless\n",
        "from importlib import import_module as load\nload('equipment_quality')\n",
        "from os import truncate as observe\nobserve('victim', 0)\n",
        "from pathlib import Path as P\nP('victim').open('w')\n",
        "open('victim', 'w')\n",
        "getattr(object(), 'write_text')('payload')\n",
    ],
)
def test_oracle_ast_guard_rejects_alias_qualified_dynamic_and_write_bypasses(source):
    with pytest.raises(AssertionError):
        _assert_read_only_oracle_ast(source)


def test_oracle_child_process_audit_rejects_inside_and_outside_filesystem_writes(
    tmp_path,
):
    root = _isolated_golden_oracle_root(tmp_path)
    audited = _run_read_only_oracle_child(root)
    assert audited.returncode == 0, audited.stdout + audited.stderr
    assert audited.stdout.strip() == "oracle-read-only-audit-ok"

    for target in (root / "inside", tmp_path.parent / "outside-oracle-root"):
        probed = _run_read_only_oracle_child(root, write_probe=target)
        assert probed.returncode != 0
        assert "oracle filesystem mutation forbidden" in probed.stderr
        assert not target.exists()


def test_independent_golden_oracle_has_one_read_only_stdlib_surface():
    oracle_path = ANALYSIS_ROOT / "tests/oracles/golden_semantics.py"
    assert oracle_path.is_file()
    source_bytes = oracle_path.read_bytes()
    assert len(source_bytes) == 138_506
    assert hashlib.sha256(source_bytes).hexdigest() == (
        "9aae75fd4b27d5b236d3a306eefa64c17be5df473e81ba83868e764c41a0d655"
    )
    source = source_bytes.decode("utf-8")
    tree = ast.parse(source)
    _assert_read_only_oracle_ast(source)

    public_functions = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]
    assert public_functions == ["build_golden_semantics"]
    assert "golden-expectation" not in source


def test_independent_golden_oracle_derives_complete_immutable_bytes(tmp_path):
    build_golden_semantics = _golden_oracle_module().build_golden_semantics

    root = _isolated_golden_oracle_root(tmp_path)
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    result = build_golden_semantics(root)
    assert isinstance(result, Mapping)
    assert set(result) == {
        "analysis_summary.template.json",
        "criteria_projection.jsonl",
        "equipment_operating_ranges.template.json",
        "expected_alerts.json",
        "quality_risk_intervals.template.json",
        "replay_events.template.csv",
    }
    assert all(type(value) is bytes for value in result.values())
    expected_outputs = {
        "analysis_summary.template.json": (
            383_700, "20acdfe66fc1167340c58f2ff91a00621725bace0b3b65832ab8629e51e04f1d",
        ),
        "criteria_projection.jsonl": (
            79_246, "6842fabd1cc79ca801e34bb0708498f4bea16317d4449e6d773e28e40c94ff5c",
        ),
        "equipment_operating_ranges.template.json": (
            106, "77fa2a0665d6d5bf0cbd754b56a86c38b159cfa26abee6685fc910cf7e546581",
        ),
        "expected_alerts.json": (
            1_244, "2c3bb6bc8e29fe077e71fd8f1b54efaa272ca31ae2676e87be8bcf549bc550ed",
        ),
        "quality_risk_intervals.template.json": (
            260_272, "d47e7d8c230f31f56719056fc3a79505847863a51e686b1aac1aee9505143996",
        ),
        "replay_events.template.csv": (
            47_904, "c516b9f8cebc2642b9c9b75a3bc12f1608df651522ccb2a65c2eb5c1424ce17c",
        ),
    }
    for name, (size, digest) in expected_outputs.items():
        assert len(result[name]) == size
        assert hashlib.sha256(result[name]).hexdigest() == digest
        assert (CONTRACT / "golden-expectation" / name).read_bytes() == result[name]
    with pytest.raises(TypeError):
        result["criteria_projection.jsonl"] = b"forbidden"  # type: ignore[index]
    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before

    assert hashlib.sha256((root / "analysis_config.json").read_bytes()).hexdigest() == (
        "bb2610971dc1b3adcb4e93b9d26a50fdb4070292ffc4bf4a26ce7cdde0f5fb0b"
    )
    assert {
        name: hashlib.sha256((root / "golden-source" / name).read_bytes()).hexdigest()
        for name in ("sts_1sm_cc_1.csv", "sts_2fur_hr_2.csv", "sts_3ap_3.csv")
    } == {
        "sts_1sm_cc_1.csv": "c3bba5c7235166b6693ff657a25797933ea9d83c6392437af9b5141f4e56a68c",
        "sts_2fur_hr_2.csv": "973e864ed996ca48c44a00943efc0ea51a878b94b8aac7452c7254a572fe7e67",
        "sts_3ap_3.csv": "efaef9f283a28e63dff54b26bc3dd42c6a72e20f0b75d2fa13a6ab47f41405c9",
    }

    projection = result["criteria_projection.jsonl"]
    projection_rows = [json.loads(line) for line in projection.splitlines()]
    assert len(projection_rows) == 268
    assert len(projection) == 79_246
    assert sum(row["kind"] == "OPERATING_RANGE_INPUT" for row in projection_rows) == 261
    assert sum(row["kind"] == "MATURE_QUALITY_INPUT" for row in projection_rows) == 7
    assert hashlib.sha256(projection).hexdigest() == (
        "6842fabd1cc79ca801e34bb0708498f4bea16317d4449e6d773e28e40c94ff5c"
    )

    ranges = json.loads(result["equipment_operating_ranges.template.json"])
    assert ranges["asOf"] == "2025-02-20"
    assert ranges["ranges"] == []

    rules = json.loads(result["quality_risk_intervals.template.json"])
    assert len(result["quality_risk_intervals.template.json"]) == 260_272
    assert hashlib.sha256(result["quality_risk_intervals.template.json"]).hexdigest() == (
        "d47e7d8c230f31f56719056fc3a79505847863a51e686b1aac1aee9505143996"
    )
    assert len(rules["rules"]) == 165
    assert {rule["grade"] for rule in rules["rules"]} == {"INSUFFICIENT_EVIDENCE"}
    assert {
        family: sum(rule["analysisFamily"] == family for rule in rules["rules"])
        for family in ("NUMERIC", "CATEGORICAL", "INTERACTION")
    } == {"NUMERIC": 113, "CATEGORICAL": 25, "INTERACTION": 27}
    rule_ids = b"".join((rule["ruleId"] + "\n").encode("ascii") for rule in rules["rules"])
    assert hashlib.sha256(rule_ids).hexdigest() == (
        "32873af746cdaf4e300f3efd136074cf98f6ed601f0d2d6c7c856d98144deee2"
    )
    assert sum(bool(rule["displayMergeRuleIds"]) for rule in rules["rules"]) == 113
    assert Counter(len(rule["displayMergeRuleIds"]) for rule in rules["rules"]) == {
        0: 52, 3: 27, 4: 16, 5: 70,
    }
    assert Counter(rule["discovery"]["reasonCode"] for rule in rules["rules"]) == {
        "LOW_SUPPORT": 165,
    }
    assert Counter(rule["discovery"]["support"] for rule in rules["rules"]) == {
        1: 129, 2: 27, 3: 4, 4: 5,
    }
    assert Counter(rule["discovery"]["defects"] for rule in rules["rules"]) == {0: 122, 1: 43}
    assert Counter(rule["confirmation"]["reasonCode"] for rule in rules["rules"]) == {
        "NO_INFORMATIVE_STRATA": 155, "LOW_SUPPORT": 10,
    }
    assert Counter(rule["confirmation"]["support"] for rule in rules["rules"]) == {0: 155, 1: 10}
    assert {rule["confirmation"]["defects"] for rule in rules["rules"]} == {0}
    by_rule_id = {rule["ruleId"]: rule for rule in rules["rules"]}
    assert by_rule_id[
        "sha256:3c3447df2f9c16c0d2bbe54e88126eef9194e511306aac71b72fe19b8a909715"
    ]["adjustmentFieldsDropped"] == [
        "delta_ferrite", "delta_ferrite_band", "mlac_ratio", "tundish_temp",
    ]
    assert by_rule_id[
        "sha256:5130184417af466f31384f1a92a78c14e41807e73b040e99a853bfaaaa08db7b"
    ]["adjustmentFieldsDropped"] == [
        "f_heat_interval", "f_heat_interval_band", "f_heat_temp", "f_heat_temp_band",
    ]
    assert by_rule_id[
        "sha256:ebb6ab4f687f6a5152812f2080e7a8f2c5accfe8c4211c3b0c271312bdceca28"
    ]["adjustmentFieldsDropped"] == [
        "ap_line_speed", "ap_line_speed_band", "ap_thick_band", "ap_width_band",
    ]

    replay_text = result["replay_events.template.csv"].decode("utf-8")
    replay_rows = list(csv.DictReader(io.StringIO(replay_text)))
    assert len(replay_rows) == 95
    event_ids = b"".join((row["event_id"] + "\n").encode("ascii") for row in replay_rows)
    assert hashlib.sha256(event_ids).hexdigest() == (
        "efbc5963a584dd79b84e2455044645d7d51b1f4c5d6721b0b2b38f9ccf2142ab"
    )
    assert len(result["replay_events.template.csv"]) == 47_904
    assert hashlib.sha256(result["replay_events.template.csv"]).hexdigest() == (
        "c516b9f8cebc2642b9c9b75a3bc12f1608df651522ccb2a65c2eb5c1424ce17c"
    )
    h001 = [row for row in replay_rows if row["charge_id"] == "CH1" and row["slab_no"] == "1"]
    h006 = [row for row in replay_rows if row["charge_id"] == "CH3" and row["slab_no"] == "2"]
    assert len(h001) == 8
    assert len(h006) == 7
    assert all(row["batch_step"] != "AP_RECORDED_WITH_RESULT" for row in h006)
    extracted = next(row for row in h001 if row["batch_step"] == "FURNACE_EXTRACTED")
    assert json.loads(extracted["values_json"])["f_ext_time"] == 7

    summary = json.loads(result["analysis_summary.template.json"])
    assert len(result["analysis_summary.template.json"]) == 383_700
    assert hashlib.sha256(result["analysis_summary.template.json"]).hexdigest() == (
        "20acdfe66fc1167340c58f2ff91a00621725bace0b3b65832ab8629e51e04f1d"
    )
    resolved_summary = json.loads(
        result["analysis_summary.template.json"]
        .replace(b"@BUNDLE_ID@", b"sha256:" + b"1" * 64)
        .replace(b"@CRITERIA_ID@", b"sha256:" + b"2" * 64)
    )
    _validator("analysis_summary.schema.json").validate(resolved_summary)
    _validate_summary_application_contract(resolved_summary)
    assert summary["asOf"] == "2025-02-20"
    assert summary["innerSplitDate"] == "2025-01-03"
    assert summary["quarantineCounts"] == {"DUPLICATE_AP_KEY": 2, "UNLINKED_AP": 1}
    assert summary["labelCensoringCounts"] == {
        "AP_UNLINKED": 1, "LABEL_NOT_YET_AVAILABLE": 2,
    }
    assert [
        summary["splitCounts"][name]["total"]
        for name in ("reference", "discovery", "confirmation", "holdout")
    ] == [10, 5, 2, 2]
    assert len(summary["sourceColumnProfiles"]) == 50
    assert len(summary["driftMetrics"]) == 37
    assert len(summary["holdoutMetrics"]) == 2
    assert len(summary["lineage"]["materials"]) == 12
    assert len(summary["lineage"]["populations"]) == 4
    assert len(summary["lineage"]["aggregates"]) == 330
    assert summary["dateRange"] == {"from": "2025-01-01", "to": "2025-04-02"}
    assert summary["splitCounts"] == {
        "confirmation": {"dateFrom": "2025-01-04", "dateTo": "2025-01-04", "defects": 0, "nonDefects": 2, "total": 2, "unknownOrCensored": 0},
        "discovery": {"dateFrom": "2025-01-01", "dateTo": "2025-01-03", "defects": 1, "nonDefects": 4, "total": 5, "unknownOrCensored": 0},
        "holdout": {"dateFrom": "2025-02-21", "dateTo": "2025-02-21", "defects": 1, "nonDefects": 1, "total": 2, "unknownOrCensored": 0},
        "reference": {"dateFrom": "2025-01-01", "dateTo": "2025-02-20", "defects": 1, "nonDefects": 6, "total": 10, "unknownOrCensored": 3},
    }
    component_expectations = {
        "sourceColumnProfiles": (10_514, "09f60fe477b885f64f4eac1b3bacdda8f828de70d54ba5a2515a1e44cff3f910"),
        "driftMetrics": (9_486, "454f30e7be94d7b6da1bee37391b53d625e0c6c9327977fb351cbdecd1bd15f9"),
        "holdoutMetrics": (1_455, "8a472f5c4966bb68494c6d39b5ea14b18456c0ed66e38113af15eeb75fcd3ead"),
    }
    for name, (size, digest) in component_expectations.items():
        payload = _canonical_json_bytes(summary[name])
        assert len(payload) == size
        assert hashlib.sha256(payload).hexdigest() == digest
    lineage_component_expectations = {
        "materials": (3_944, "0c4a97f9df86f07e46742baeace60e5839ee7de1fcaa9106e90c0d7d83cb7d90"),
        "populations": (1_678, "bcc35e7c3e0128d534271676d7b86f90087a864c290ae7fc347525e77eb59102"),
        "aggregates": (158_617, "cd0bd97dcf1d5f0ca3e4ab2a9092c7d2a51a21100f4c9051684cabf631f37dfb"),
    }
    for name, (size, digest) in lineage_component_expectations.items():
        payload = _canonical_json_bytes(summary["lineage"][name])
        assert len(payload) == size
        assert hashlib.sha256(payload).hexdigest() == digest
    summary_core = _canonical_json_bytes({key: value for key, value in summary.items() if key != "lineage"})
    assert len(summary_core) == 22_480
    assert hashlib.sha256(summary_core).hexdigest() == (
        "d01ad5261a7cda263df560413ca2fd047454df0f824181e9416111ca487cb655"
    )
    expected_profile_order = []
    for source_role, source_name in (
        ("sm_cc", "sts_1sm_cc_1.csv"),
        ("fur_hr", "sts_2fur_hr_2.csv"),
        ("ap", "sts_3ap_3.csv"),
    ):
        header = next(csv.reader(io.StringIO(
            (root / "golden-source" / source_name).read_text(encoding="cp949")
        )))
        expected_profile_order.extend((source_role, column) for column in header)
    assert [(item["sourceRole"], item["column"]) for item in summary["sourceColumnProfiles"]] == expected_profile_order
    config = json.loads((root / "analysis_config.json").read_bytes())
    stage_rank = {
        "CAST_RECORDED": 0, "FURNACE_CHARGED": 10, "PREHEAT_COMPLETE": 11,
        "HEAT_COMPLETE": 12, "SOAK_COMPLETE": 13, "FURNACE_EXTRACTED": 14,
        "RM4_RECORDED": 15, "AP_RECORDED_WITH_RESULT": 20,
    }
    quality_fields = {
        item["field"]: item for item in config["fields"]
        if item["featureRole"] in {"DIRECT_OPERATION", "PRODUCT_STATE_REFERENCE", "CONTEXT", "EQUIPMENT_IDENTIFIER"}
    }
    assert [item["field"] for item in summary["driftMetrics"]] == sorted(
        quality_fields,
        key=lambda field: (stage_rank[quality_fields[field]["firstAvailableStage"]], field.encode("utf-8")),
    )
    for field in ("ap_line_speed", "ap_plant", "ap_shift", "ap_thick", "ap_width"):
        drift = next(item for item in summary["driftMetrics"] if item["field"] == field)
        assert drift["reference"]["support"] == 7
        assert drift["reference"]["missingRate"] == 0.3
        assert drift["holdout"]["support"] == 2
        assert drift["holdout"]["missingRate"] == 0.0
    materials_by_coil = {item["hrCoilId"]: item for item in summary["lineage"]["materials"]}
    assert [(item["role"], item["recordNumber"]) for item in materials_by_coil["H001"]["sourceRecords"]] == [("sm_cc", 2), ("fur_hr", 2), ("ap", 2)]
    assert [(item["role"], item["recordNumber"]) for item in materials_by_coil["H006"]["sourceRecords"]] == [("sm_cc", 7), ("fur_hr", 7)]
    assert [(item["role"], item["recordNumber"]) for item in materials_by_coil["H009"]["sourceRecords"]] == [("sm_cc", 10), ("fur_hr", 10), ("ap", 10)]
    assert [(item["role"], item["recordNumber"]) for item in materials_by_coil["H011"]["sourceRecords"]] == [("sm_cc", 12), ("fur_hr", 12), ("ap", 12)]
    aggregates = summary["lineage"]["aggregates"]
    assert [(item["ruleId"], item["split"]) for item in aggregates] == sorted(
        ((item["ruleId"], item["split"]) for item in aggregates),
        key=lambda item: (item[0].encode("utf-8"), 0 if item[1] == "CONFIRMATION" else 1),
    )
    reduced_aggregates = _canonical_json_bytes([{
        "inputMaterialKeys": item["inputMaterialKeys"], "ruleId": item["ruleId"], "split": item["split"],
    } for item in aggregates])
    assert len(reduced_aggregates) == 59_212
    assert hashlib.sha256(reduced_aggregates).hexdigest() == (
        "c6e2f6404b5943859676ccd29fe20e62382dcae7f21ed6a21dd6d337f54c7056"
    )
    assert sum(not item["inputMaterialKeys"] for item in aggregates if item["split"] == "CONFIRMATION") == 155
    assert all(item["inputMaterialKeys"] for item in aggregates if item["split"] == "DISCOVERY")
    role_order = (
        "bundle_manifest", "analysis_config", "producer_runtime", "equipment_operating_ranges",
        "quality_risk_intervals", "replay_events", "analysis_summary",
    )
    field_counts = Counter(item["artifactRole"] for item in summary["lineage"]["fields"])
    assert field_counts == {
        "bundle_manifest": 37, "analysis_config": 49, "producer_runtime": 35,
        "equipment_operating_ranges": 3, "quality_risk_intervals": 53,
        "replay_events": 61, "analysis_summary": 107,
    }
    normalized_paths = b"".join(
        (role + "\t" + item["outputField"] + "\n").encode("utf-8")
        for role in role_order
        for item in sorted(
            (field for field in summary["lineage"]["fields"] if field["artifactRole"] == role),
            key=lambda field: field["outputField"].encode("utf-8"),
        )
    )
    assert hashlib.sha256(normalized_paths).hexdigest() == (
        "30e7754507d5fda136ce7bf8dff8517182b7b0276988ac2903acc47050e47200"
    )
    assert not any(item["outputField"] == "values_json" for item in summary["lineage"]["fields"])
    field_lineage = summary["lineage"]["fields"]
    field_lineage_bytes = _canonical_json_bytes(field_lineage)
    assert len(field_lineage_bytes) == 196_921
    assert hashlib.sha256(field_lineage_bytes).hexdigest() == (
        "8cb915529575d5df9342117b02539ef76321f1146da6537dba1d2f1d448d25ef"
    )
    assert sum(len(item["dependencies"]) for item in field_lineage) == 3_340
    field_by_node = {
        (item["artifactRole"], item["outputField"]): item for item in field_lineage
    }
    assert len({
        tuple(item["dependencies"])
        for item in field_lineage
        if item["artifactRole"] == "quality_risk_intervals"
    }) == 35
    assert Counter(item["conversion"] for item in field_lineage) == {
        "APPLY_GRADE_POLICY": 1,
        "CLASSIFY_REPLAY_SCHEDULE": 3,
        "COMPARE_DISTRIBUTIONS": 14,
        "COMPUTE_BYTE_SIZE": 1,
        "COMPUTE_DATE_RANGE": 2,
        "COMPUTE_HOLDOUT_METRIC": 30,
        "COMPUTE_IDENTITY": 7,
        "COMPUTE_QUALITY_METRIC": 28,
        "COMPUTE_SHA256": 1,
        "COPY_BOUNDARY_DATE": 2,
        "COPY_CANONICAL_CONFIG": 51,
        "COPY_DIGEST_VALUE": 16,
        "COPY_FIELD_METADATA": 4,
        "COPY_IDENTITY_VALUE": 7,
        "COPY_POLICY_VALUE": 2,
        "COPY_SCHEMA_VERSION": 6,
        "COPY_SOURCE_METADATA": 9,
        "COPY_SOURCE_SCALAR": 15,
        "COPY_VERIFIED_RUNTIME": 35,
        "COUNT_PARTITION": 37,
        "DERIVE_FUEL_RATIO": 3,
        "DERIVE_RULE_CANDIDATE": 18,
        "DERIVE_STAGE_ELIGIBILITY": 1,
        "MERGE_DISPLAY_INTERVALS": 1,
        "PARSE_DATE": 4,
        "PARSE_FINITE_BINARY64": 24,
        "PARSE_HOUR_BUCKET": 1,
        "PROFILE_SOURCE_COLUMN": 13,
        "PROJECT_ARTIFACT_METADATA": 1,
        "SELECT_STAGE_EQUIPMENT": 2,
        "SELECT_STAGE_VALUE": 2,
        "SELECT_TIME_BOUNDARY": 4,
    }
    assert max(len(item["dependencies"]) for item in field_lineage) == 308
    assert all(
        f'{item["artifactRole"]}.{item["outputField"]}' not in item["dependencies"]
        and all(".lineage" not in dependency for dependency in item["dependencies"])
        for item in field_lineage
    )
    non_manifest_nodes = sorted(
        f'{item["artifactRole"]}.{item["outputField"]}'
        for item in field_lineage
        if item["artifactRole"] != "bundle_manifest"
    )
    for output_field, conversion in (
        ("artifacts[].sha256", "COMPUTE_SHA256"),
        ("artifacts[].sizeBytes", "COMPUTE_BYTE_SIZE"),
    ):
        attestation = field_by_node[("bundle_manifest", output_field)]
        assert attestation["conversion"] == conversion
        assert attestation["dependencies"] == non_manifest_nodes
    assert field_by_node[("bundle_manifest", "identity.source.sm_cc.sha256")][
        "dependencies"
    ] == ["source.sm_cc.sha256"]
    assert field_by_node[("bundle_manifest", "identity.schema.analysis_config.sha256")] == {
        "artifactRole": "bundle_manifest",
        "conversion": "COPY_DIGEST_VALUE",
        "dependencies": ["schema.analysis_config.sha256"],
        "firstAvailableStage": None,
        "outputField": "identity.schema.analysis_config.sha256",
        "sourceColumn": None,
        "sourceRole": None,
    }
    assert field_by_node[("analysis_config", "fixedInteractions[][]")]["dependencies"] == [
        "config.fixedInteractions[][]",
    ]
    assert field_by_node[("producer_runtime", "packages[].wheelSha256")]["dependencies"] == [
        "runtime.packages[].wheelSha256",
    ]
    assert field_by_node[("replay_events", "charge_id")]["dependencies"] == []
    assert field_by_node[("replay_events", "charge_id")]["sourceRole"] == "sm_cc"
    assert field_by_node[("replay_events", "slab_no")]["sourceRole"] == "fur_hr"
    assert field_by_node[("replay_events", "values_json.f_bfg_ratio")]["dependencies"] == [
        "fur_hr.f_bfg", "fur_hr.f_cog", "fur_hr.f_ldg",
    ]
    assert field_by_node[("replay_events", "batch_step")]["dependencies"] == [
        "ap.ap_prod_id", "ap.hr_coil_id", "fur_hr.hr_coil_id",
        "replay_events.material_key",
    ]
    assert field_by_node[("replay_events", "replay_date")]["dependencies"] == [
        "ap.ap_date", "fur_hr.f_ext_date", "replay_events.batch_step", "sm_cc.cast_date",
    ]
    assert field_by_node[("replay_events", "event_id")]["dependencies"] == [
        "replay_events.batch_id", "replay_events.batch_step",
        "replay_events.equipment_batch_id", "replay_events.equipment_id",
        "replay_events.equipment_type", "replay_events.material_key",
    ]
    assert len(field_by_node[(
        "analysis_summary", "sourceColumnProfiles[].numeric.p05",
    )]["dependencies"]) == 28
    assert len(field_by_node[(
        "analysis_summary", "driftMetrics[].reference.median",
    )]["dependencies"]) == 31
    for leaf in (
        "support", "missingRate", "p05", "median", "p95", "levels[].value", "levels[].count",
    ):
        assert "analysis_summary.asOf" in field_by_node[(
            "analysis_summary", f"driftMetrics[].reference.{leaf}",
        )]["dependencies"]
    assert "analysis_summary.asOf" not in field_by_node[(
        "analysis_summary", "driftMetrics[].reference.levels",
    )]["dependencies"]
    assert all(
        "analysis_summary.asOf" not in field_by_node[(
            "analysis_summary", f"driftMetrics[].holdout.{leaf}",
        )]["dependencies"]
        for leaf in (
            "support", "missingRate", "p05", "median", "p95",
            "levels", "levels[].value", "levels[].count",
        )
    )
    precision_dependencies = [
        "analysis_summary.holdoutMetrics[].falsePositive",
        "analysis_summary.holdoutMetrics[].truePositive",
    ]
    for leaf in ("pointEstimate", "lower", "upper", "validReplicates", "reasonCode"):
        assert field_by_node[(
            "analysis_summary", f"holdoutMetrics[].precision.{leaf}",
        )]["dependencies"] == precision_dependencies
    assert field_by_node[(
        "analysis_summary", "holdoutMetrics[].baseDefectRate.pointEstimate",
    )]["dependencies"] == [
        "population.HOLDOUT", "replay_events.values_json.judge",
    ]
    for leaf in ("lower", "upper", "reasonCode"):
        assert field_by_node[(
            "analysis_summary", f"holdoutMetrics[].baseDefectRate.{leaf}",
        )]["dependencies"] == [
            "config.bootstrap.minimumValidReplicates", "config.bootstrap.replicates",
            "identity.criteria_id", "population.HOLDOUT", "replay_events.charge_id",
            "replay_events.values_json.judge",
        ]
    assert field_by_node[(
        "analysis_summary", "holdoutMetrics[].baseDefectRate.validReplicates",
    )]["dependencies"] == [
        "config.bootstrap.replicates", "identity.criteria_id", "population.HOLDOUT",
        "replay_events.charge_id", "replay_events.values_json.judge",
    ]
    for metric_name in ("alertRate", "recall", "falseAlertsPer100"):
        valid_dependencies = field_by_node[(
            "analysis_summary", f"holdoutMetrics[].{metric_name}.validReplicates",
        )]["dependencies"]
        assert "config.bootstrap.replicates" in valid_dependencies
        assert "config.bootstrap.minimumValidReplicates" not in valid_dependencies
        for leaf in ("lower", "upper", "reasonCode"):
            assert "config.bootstrap.minimumValidReplicates" in field_by_node[(
                "analysis_summary", f"holdoutMetrics[].{metric_name}.{leaf}",
            )]["dependencies"]
        for leaf in ("lower", "upper", "reasonCode", "validReplicates"):
            assert "analysis_summary.holdoutMetrics[].alertGrade" in field_by_node[(
                "analysis_summary", f"holdoutMetrics[].{metric_name}.{leaf}",
            )]["dependencies"]
            assert "replay_events.values_json.judge" in field_by_node[(
                "analysis_summary", f"holdoutMetrics[].{metric_name}.{leaf}",
            )]["dependencies"]
    for leaf in ("truePositive", "falsePositive", "trueNegative", "falseNegative"):
        assert "analysis_summary.holdoutMetrics[].alertGrade" in field_by_node[(
            "analysis_summary", f"holdoutMetrics[].{leaf}",
        )]["dependencies"]
    assert all(
        "analysis_summary.holdoutMetrics[].alertGrade" not in field_by_node[(
            "analysis_summary", f"holdoutMetrics[].baseDefectRate.{leaf}",
        )]["dependencies"]
        for leaf in ("pointEstimate", "lower", "upper", "reasonCode", "validReplicates")
    )
    alert_grade_consumers = {
        output_field
        for (artifact_role, output_field), item in field_by_node.items()
        if artifact_role == "analysis_summary"
        and "analysis_summary.holdoutMetrics[].alertGrade" in item["dependencies"]
    }
    assert alert_grade_consumers == {
        *(f"holdoutMetrics[].{leaf}" for leaf in (
            "truePositive", "falsePositive", "trueNegative", "falseNegative",
        )),
        *(f"holdoutMetrics[].{metric}.{leaf}"
          for metric in ("alertRate", "recall", "falseAlertsPer100")
          for leaf in ("lower", "upper", "reasonCode", "validReplicates")),
    }
    ap_stage_feature_nodes = {
        "replay_events.values_json.ap_line_speed",
        "replay_events.values_json.ap_plant",
        "replay_events.values_json.ap_shift",
        "replay_events.values_json.ap_thick",
        "replay_events.values_json.ap_width",
    }
    for output_field in alert_grade_consumers:
        dependencies = field_by_node[("analysis_summary", output_field)]["dependencies"]
        assert ap_stage_feature_nodes.isdisjoint(dependencies)
        assert "analysis_summary.asOf" in dependencies
        assert "replay_events.replay_date" in dependencies
    for metric_name in ("alertRate", "precision", "recall", "baseDefectRate", "lift", "falseAlertsPer100"):
        assert "analysis_summary.asOf" not in field_by_node[(
            "analysis_summary", f"holdoutMetrics[].{metric_name}.pointEstimate",
        )]["dependencies"]
        assert "replay_events.replay_date" not in field_by_node[(
            "analysis_summary", f"holdoutMetrics[].{metric_name}.pointEstimate",
        )]["dependencies"]
    assert field_by_node[("analysis_summary", "holdoutMetrics[].total")]["dependencies"] == [
        "population.HOLDOUT", "replay_events.values_json.judge",
    ]
    assert field_by_node[(
        "analysis_summary", "splitCounts.reference.defects",
    )]["dependencies"] == [
        "analysis_summary.asOf", "config.labelMaturityDays", "population.REFERENCE",
        "replay_events.values_json.ap_date", "replay_events.values_json.hr_date",
        "replay_events.values_json.judge",
    ]

    alerts = json.loads(result["expected_alerts.json"])
    assert alerts["alerts"] == []
    assert alerts["bundleId"] == "@BUNDLE_ID@"
    assert alerts["criteriaId"] == "@CRITERIA_ID@"
    assert alerts["expectedReplayEventCount"] == 95
    assert set(re.findall(r"@[A-Z0-9_]+@", json.dumps(alerts))) == {
        token.decode("ascii") for token in EXACT_ALLOWED_TOKENS
    }
