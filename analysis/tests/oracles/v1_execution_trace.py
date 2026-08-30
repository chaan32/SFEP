"""Read-only, standard-library-only authentication and trace oracle for v1 bundles."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import struct
from typing import Mapping


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_POWER10 = tuple(10**exponent for exponent in range(400))
_ARTIFACTS = (
    ("analysis_config", "analysis_config.json", "sfep-analysis-config/v1"),
    ("producer_runtime", "producer_runtime.json", "sfep-producer-runtime/v1"),
    ("equipment_operating_ranges", "equipment_operating_ranges.json", "sfep-operating-ranges/v1"),
    ("quality_risk_intervals", "quality_risk_intervals.json", "sfep-quality-rules/v1"),
    ("replay_events", "replay_events.csv", "sfep-replay-events/v1"),
    ("analysis_summary", "analysis_summary.json", "sfep-analysis-summary/v1"),
)
_SCHEMA_DIGESTS = {
    "analysis_config": "sha256:fad28561dfe9d9fe3cd09b025bb18c2101be053cb094b08442ea45963b86f549",
    "analysis_summary": "sha256:6cb6077cc7093b90e464cc794604ca6b7682002a138ebed051801aee29f9d374",
    "bundle_manifest": "sha256:666e880d296c0d7e3df5af1aa80e6865922ebfb337fb9aca48f93eddfd89e8a5",
    "equipment_operating_ranges": "sha256:bee7d8be181dae4844c51d4627c5a1f068583b60a60c854f17035a8291cd7d89",
    "producer_runtime": "sha256:97131d80a993d09d17c2c040b0e1cb2bd0eed5948d7a11608f26331d18f557e6",
    "quality_risk_intervals": "sha256:2c8775fec18671030cf58ea0e94a3c99f8dac075fa9d5fe8dce724c2462f42ad",
    "replay_events": "sha256:309749f73cf2a5a522617f975128ac005298f2fd71412903d76263702c1b6bd6",
}
_CRITERIA_KEYS = {
    "analysis_config_sha256", "as_of", "criteria_projection_sha256",
    "producer_runtime_sha256", "schema.analysis_config.sha256",
    "schema.equipment_operating_ranges.sha256", "schema.producer_runtime.sha256",
    "schema.quality_risk_intervals.sha256",
}
_BUNDLE_KEYS = {
    "analysis_config_sha256", "criteria_id", "producer_runtime_sha256",
    *(f"schema.{role}.sha256" for role in _SCHEMA_DIGESTS),
    *(f"source.{role}.{field}" for role in ("ap", "fur_hr", "sm_cc")
      for field in ("name", "sha256", "size_bytes")),
}
_SOURCE_NAMES = {
    "ap": "sts_3ap_3.csv", "fur_hr": "sts_2fur_hr_2.csv", "sm_cc": "sts_1sm_cc_1.csv",
}
_REPLAY_HEADER = (
    "schema_version", "bundle_id", "criteria_id", "event_id", "replay_date",
    "replay_hour", "batch_kind", "batch_id", "equipment_batch_id", "batch_step",
    "time_precision", "material_key", "equipment_type", "equipment_id", "charge_id",
    "slab_no", "hr_coil_id", "ap_prod_id", "values_json",
)
_STAGE_RANK = {
    "CAST_RECORDED": 0, "FURNACE_CHARGED": 10, "PREHEAT_COMPLETE": 11,
    "HEAT_COMPLETE": 12, "SOAK_COMPLETE": 13, "FURNACE_EXTRACTED": 14,
    "RM4_RECORDED": 15, "AP_RECORDED_WITH_RESULT": 20,
}


@dataclass(frozen=True)
class EventSemantic:
    event_id: str
    batch_id: str
    batch_step: str
    material_key: str
    equipment_type: str
    equipment_id: str
    replay_date: str
    replay_hour: int | None
    values_sha256: str


@dataclass(frozen=True)
class RuleBootstrapTrace:
    rule_id: str
    replicate_ordinal: int
    sampled_charge_ordinals: tuple[int, ...]
    confusion: tuple[int, int, int, int]
    valid_replicate_count: int
    ci: tuple[float | None, float | None]


@dataclass(frozen=True)
class HoldoutMetricTrace:
    alert_grade: str
    confusion: tuple[int, int, int, int]
    metrics: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class V1ExecutionTrace:
    rule_bootstraps: tuple[RuleBootstrapTrace, ...]
    holdout_metrics: tuple[HoldoutMetricTrace, ...]
    rule_ids: tuple[str, ...]
    range_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    event_semantics: tuple[EventSemantic, ...]
    summary_lineage_keys: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedV1Bundle:
    root: Path
    criteria_id: str
    bundle_id: str
    trace: V1ExecutionTrace


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _require_sha(value: object, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 URI")
    return value


def _scaled_floor(value: Fraction, exponent: int) -> int:
    if exponent >= 0:
        power = _POWER10[exponent] if exponent < len(_POWER10) else 10**exponent
        return value.numerator // (value.denominator * power)
    power = _POWER10[-exponent] if -exponent < len(_POWER10) else 10 ** (-exponent)
    return (value.numerator * power) // value.denominator


def _decimal_order(value: Fraction) -> int:
    order = len(str(value.numerator)) - len(str(value.denominator))
    if order >= 0:
        return order - 1 if value.numerator < value.denominator * _POWER10[order] else order
    return order - 1 if value.numerator * _POWER10[-order] < value.denominator else order


def _rounding_interval(number: float) -> tuple[Fraction, Fraction]:
    target = Fraction.from_float(number)
    previous = Fraction.from_float(math.nextafter(number, -math.inf))
    lower = (previous + target) / 2
    following = math.nextafter(number, math.inf)
    upper = target + (target - previous) / 2 if math.isinf(following) else (target + Fraction.from_float(following)) / 2
    return lower, upper


def _decimal_spellings(coefficient: int, exponent: int, limit: int) -> tuple[str, ...]:
    digits = str(coefficient)
    if exponent >= 0:
        fixed = digits + "0" * exponent
    else:
        point = len(digits) + exponent
        fixed = digits[:point] + "." + digits[point:] if point > 0 else "0." + "0" * (-point) + digits
    result = [fixed] if len(fixed) <= limit else []
    for point in range(1, len(digits) + 1):
        mantissa = digits if point == len(digits) else digits[:point] + "." + digits[point:]
        scientific = f"{mantissa}e{exponent + len(digits) - point}"
        if len(scientific) <= limit:
            result.append(scientific)
    return tuple(result)


def _initial_float_spelling(number: float) -> str:
    negative = number < 0.0
    magnitude = -number if negative else number
    coefficient, marker, exponent_text = repr(magnitude).lower().partition("e")
    exponent = int(exponent_text) if marker else 0
    integer, dot, fraction = coefficient.partition(".")
    digits = (integer + fraction).lstrip("0")
    decimal_exponent = exponent - (len(fraction) if dot else 0)
    while digits.endswith("0"):
        digits = digits[:-1]
        decimal_exponent += 1
    prefix = "-" if negative else ""
    candidates = [prefix + value for value in _decimal_spellings(int(digits), decimal_exponent, 1000)
                  if float(prefix + value) == number]
    return min(candidates, key=lambda value: (len(value), value))


def _minimum_spelling_length(digits: int, exponent: int, sign: int) -> int:
    fixed = digits + exponent if exponent >= 0 else digits + 1 if digits + exponent > 0 else 2 - (digits + exponent) + digits
    scientific = min(
        digits + (1 if point < digits else 0) + 1 + len(str(exponent + digits - point))
        for point in range(1, digits + 1)
    )
    return sign + min(fixed, scientific)


def _canonical_float(number: float) -> str:
    if not math.isfinite(number):
        raise ValueError("canonical JSON numbers must be finite")
    if number == 0.0:
        return "0"
    best = _initial_float_spelling(number)
    best_key = (len(best.encode()), best.encode())
    negative = number < 0.0
    magnitude = -number if negative else number
    lower, upper = _rounding_interval(magnitude)
    prefix = "-" if negative else ""
    for digits in range(1, best_key[0] - len(prefix) + 1):
        lower_order, upper_order = _decimal_order(lower), _decimal_order(upper)
        for exponent in range(lower_order - digits - 1, upper_order - digits + 2):
            if _minimum_spelling_length(digits, exponent, len(prefix)) > best_key[0]:
                continue
            least = 1 if digits == 1 else _POWER10[digits - 1]
            greatest = _POWER10[digits] - 1
            first = max(least, _scaled_floor(lower, exponent) - 1)
            last = min(greatest, _scaled_floor(upper, exponent) + 1)
            for coefficient in range(first, min(last, first + 3) + 1):
                if coefficient % 10 == 0:
                    continue
                for spelling in _decimal_spellings(coefficient, exponent, best_key[0] - len(prefix)):
                    candidate = prefix + spelling
                    key = (len(candidate.encode()), candidate.encode())
                    if key <= best_key and float(candidate) == number:
                        best, best_key = candidate, key
    return best


def _canonical_text(value: object) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        return _canonical_float(value)
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if type(value) is list:
        return "[" + ",".join(_canonical_text(item) for item in value) + "]"
    if type(value) is dict:
        return "{" + ",".join(
            _canonical_text(key) + ":" + _canonical_text(value[key]) for key in sorted(value)
        ) + "}"
    raise ValueError(f"unsupported JSON value {type(value).__name__}")


def _canonical_bytes(value: object) -> bytes:
    return (_canonical_text(value) + "\n").encode("utf-8")


def _read_json(path: Path) -> tuple[bytes, dict[str, object]]:
    payload = path.read_bytes()
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{path.name} must be UTF-8 JSON") from error
    if type(value) is not dict:
        raise ValueError(f"{path.name} must contain one JSON object")
    if _canonical_bytes(value) != payload:
        raise ValueError(f"{path.name} must use canonical JSON")
    return payload, value


def _identity(version: str, fields: Mapping[str, object], expected: set[str], label: str) -> str:
    if type(fields) is not dict or set(fields) != expected or len(fields) != len(expected):
        raise ValueError(f"{label} must contain exactly {len(expected)} fields")
    if any(type(key) is not str or type(value) is not str for key, value in fields.items()):
        raise ValueError(f"{label} fields must be strings")
    preimage = "\n".join([version, *(f"{key}={fields[key]}" for key in sorted(fields, key=str.encode))]) + "\n"
    return _sha(preimage.encode())


def _digest_json_id(namespace: str, value: dict[str, object]) -> str:
    return _sha(namespace.encode() + b"\n" + _canonical_bytes(value))


def _verify_schema_fields(criteria: dict[str, object], bundle: dict[str, object]) -> None:
    for role, expected in _SCHEMA_DIGESTS.items():
        key = f"schema.{role}.sha256"
        if key in criteria and criteria[key] != expected:
            raise ValueError(f"criteria identity {key} does not match fixed v1 schema")
        if bundle.get(key) != expected:
            raise ValueError(f"bundle identity {key} does not match fixed v1 schema")


def _verify_manifest(root: Path) -> tuple[dict[str, object], dict[str, bytes]]:
    _manifest_bytes, manifest = _read_json(root / "bundle_manifest.json")
    if manifest.get("schemaVersion") != "sfep-equipment-bundle/v1":
        raise ValueError("manifest schemaVersion is not v1")
    entries = manifest.get("artifacts")
    if type(entries) is not list or len(entries) != len(_ARTIFACTS):
        raise ValueError("manifest must describe exactly six artifacts")
    payloads: dict[str, bytes] = {}
    for entry, (role, filename, version) in zip(entries, _ARTIFACTS, strict=True):
        if type(entry) is not dict or entry.get("role") != role or entry.get("schemaVersion") != version:
            raise ValueError("manifest artifact roles/schema versions are not exact")
        path = root / filename
        if not path.is_file():
            raise ValueError(f"missing artifact {filename}")
        payload = path.read_bytes()
        if type(entry.get("sizeBytes")) is not int or entry["sizeBytes"] != len(payload):
            raise ValueError(f"artifact {role} size does not match manifest")
        if entry.get("sha256") != _sha(payload):
            raise ValueError(f"artifact {role} digest does not match manifest")
        payloads[role] = payload
    return manifest, payloads


def _verify_identities(manifest: dict[str, object], payloads: dict[str, bytes]) -> tuple[str, str]:
    criteria = manifest.get("criteriaIdentity")
    bundle = manifest.get("identity")
    if type(criteria) is not dict or type(bundle) is not dict:
        raise ValueError("manifest identities must be objects")
    _verify_schema_fields(criteria, bundle)
    if criteria.get("as_of") != manifest.get("asOf"):
        raise ValueError("criteria identity as_of binding does not match manifest")
    for role in ("analysis_config", "producer_runtime"):
        digest = _sha(payloads[role])
        if criteria.get(f"{role}_sha256") != digest or bundle.get(f"{role}_sha256") != digest:
            raise ValueError(f"{role} identity binding does not match artifact")
    _require_sha(criteria.get("criteria_projection_sha256"), "criteria projection digest")
    criteria_id = _identity("sfep-criteria-id/v1", criteria, _CRITERIA_KEYS, "criteria identity")
    if manifest.get("criteriaId") != criteria_id:
        raise ValueError("criteria identity does not recompute to criteriaId")
    if bundle.get("criteria_id") != criteria_id:
        raise ValueError("bundle identity criteria_id binding does not match")
    for role, name in _SOURCE_NAMES.items():
        prefix = f"source.{role}"
        if bundle.get(f"{prefix}.name") != name:
            raise ValueError(f"bundle identity source name for {role} is not fixed")
        _require_sha(bundle.get(f"{prefix}.sha256"), f"source {role} digest")
        size = bundle.get(f"{prefix}.size_bytes")
        if type(size) is not str or not size.isascii() or not size.isdigit() or (size != "0" and size.startswith("0")):
            raise ValueError(f"bundle identity source size for {role} is not canonical")
    bundle_id = _identity("sfep-bundle-id/v1", bundle, _BUNDLE_KEYS, "bundle identity")
    if manifest.get("bundleId") != bundle_id:
        raise ValueError("bundle identity does not recompute to bundleId")
    return criteria_id, bundle_id


def _json_from_payload(payload: bytes, filename: str) -> dict[str, object]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{filename} must be UTF-8 JSON") from error
    if type(value) is not dict or _canonical_bytes(value) != payload:
        raise ValueError(f"{filename} must use canonical JSON")
    return value


def _verify_json_bindings(
    manifest: dict[str, object], payloads: dict[str, bytes], criteria_id: str, bundle_id: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    config = _json_from_payload(payloads["analysis_config"], "analysis_config.json")
    runtime = _json_from_payload(payloads["producer_runtime"], "producer_runtime.json")
    ranges = _json_from_payload(payloads["equipment_operating_ranges"], "equipment_operating_ranges.json")
    rules = _json_from_payload(payloads["quality_risk_intervals"], "quality_risk_intervals.json")
    summary = _json_from_payload(payloads["analysis_summary"], "analysis_summary.json")
    expected_versions = (
        (config, "sfep-analysis-config/v1"), (runtime, "sfep-producer-runtime/v1"),
        (ranges, "sfep-operating-ranges/v1"), (rules, "sfep-quality-rules/v1"),
        (summary, "sfep-analysis-summary/v1"),
    )
    for value, version in expected_versions:
        if value.get("schemaVersion") != version:
            raise ValueError(f"artifact schemaVersion binding is not {version}")
    for value in (ranges, rules, summary):
        if value.get("criteriaId") != criteria_id or value.get("asOf") != manifest.get("asOf"):
            raise ValueError("artifact criteriaId/asOf binding does not match manifest")
    if summary.get("bundleId") != bundle_id:
        raise ValueError("summary bundleId binding does not match manifest")
    return config, ranges, rules, summary


def _verify_range_ids(ranges: dict[str, object]) -> tuple[str, ...]:
    records = ranges.get("ranges")
    if type(records) is not list:
        raise ValueError("ranges must be a list")
    result = []
    for record in records:
        if type(record) is not dict:
            raise ValueError("range record must be an object")
        claimed = _require_sha(record.get("ruleId"), "range ruleId")
        expected = _sha(_canonical_bytes({key: value for key, value in record.items() if key != "ruleId"}))
        if claimed != expected:
            raise ValueError("range ruleId does not authenticate range semantics")
        result.append(claimed)
    if len(result) != len(set(result)):
        raise ValueError("range ruleId values must be unique")
    return tuple(sorted(result, key=str.encode))


_RULE_IDENTITY_KEYS = (
    "analysisFamily", "fieldNames", "predicate", "firstAvailableStage", "equipmentType",
    "applicationScope", "equipmentId", "applicationContext", "adjustmentLevel",
    "adjustmentFieldsDropped", "adjustmentKind",
)


def _verify_rule_ids(rules: dict[str, object]) -> tuple[str, ...]:
    records = rules.get("rules")
    if type(records) is not list:
        raise ValueError("rules must be a list")
    result = []
    for record in records:
        if type(record) is not dict:
            raise ValueError("rule record must be an object")
        claimed = _require_sha(record.get("ruleId"), "quality ruleId")
        identity = {key: record.get(key) for key in _RULE_IDENTITY_KEYS}
        expected = _sha(_canonical_bytes(identity))
        if claimed != expected:
            raise ValueError("quality ruleId does not authenticate rule semantics")
        result.append(claimed)
    if len(result) != len(set(result)):
        raise ValueError("quality ruleId values must be unique")
    return tuple(sorted(result, key=str.encode))


def _read_replay(
    root: Path, criteria_id: str, bundle_id: str,
) -> tuple[tuple[EventSemantic, ...], dict[str, dict[str, object]]]:
    semantics: list[EventSemantic] = []
    material_values: dict[str, dict[str, object]] = {}
    seen_events: set[str] = set()
    with (root / "replay_events.csv").open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        try:
            header = tuple(next(reader))
        except StopIteration as error:
            raise ValueError("replay_events.csv must not be empty") from error
        if header != _REPLAY_HEADER:
            raise ValueError("replay_events.csv header is not exact")
        for ordinal, cells in enumerate(reader, start=2):
            if len(cells) != len(header):
                raise ValueError(f"replay row {ordinal} field count is not exact")
            row = dict(zip(header, cells, strict=True))
            if row["schema_version"] != "sfep-replay-events/v1":
                raise ValueError("replay schema_version binding is not v1")
            if row["criteria_id"] != criteria_id or row["bundle_id"] != bundle_id:
                raise ValueError("replay criteria_id/bundle_id binding does not match manifest")
            try:
                values = json.loads(row["values_json"])
            except json.JSONDecodeError as error:
                raise ValueError(f"replay row {ordinal} values_json is invalid") from error
            if type(values) is not dict or _canonical_bytes(values)[:-1].decode() != row["values_json"]:
                raise ValueError(f"replay row {ordinal} values_json is not canonical")
            material = _digest_json_id(
                "sfep-material-key/v1", {"chargeId": row["charge_id"], "slabNo": row["slab_no"]},
            )
            if row["material_key"] != material:
                raise ValueError(f"replay row {ordinal} material_key does not match semantics")
            batch_value: dict[str, object] = {"batchKind": row["batch_kind"], "replayDate": row["replay_date"]}
            replay_hour = None if row["replay_hour"] == "" else int(row["replay_hour"])
            if row["batch_kind"] == "FURNACE_HOUR":
                batch_value["replayHour"] = replay_hour
            elif replay_hour is not None:
                raise ValueError(f"replay row {ordinal} day batch has an hour")
            batch = _digest_json_id("sfep-batch-id/v1", batch_value)
            if row["batch_id"] != batch:
                raise ValueError(f"replay row {ordinal} batch_id does not match semantics")
            equipment_batch = None
            if row["equipment_type"] == "FURNACE":
                equipment_batch = _digest_json_id(
                    "sfep-equipment-batch-id/v1",
                    {"batchId": batch, "equipmentId": row["equipment_id"], "equipmentType": "FURNACE"},
                )
            if row["equipment_batch_id"] != (equipment_batch or ""):
                raise ValueError(f"replay row {ordinal} equipment_batch_id does not match semantics")
            event = _digest_json_id(
                "sfep-event-id/v1",
                {"batchId": batch, "batchStep": row["batch_step"], "equipmentBatchId": equipment_batch,
                 "equipmentId": row["equipment_id"], "equipmentType": row["equipment_type"],
                 "materialKey": material},
            )
            if row["event_id"] != event:
                raise ValueError(f"replay row {ordinal} event_id does not match semantics")
            if event in seen_events:
                raise ValueError("replay event_id values must be unique")
            seen_events.add(event)
            semantics.append(EventSemantic(
                event, batch, row["batch_step"], material, row["equipment_type"], row["equipment_id"],
                row["replay_date"], replay_hour, _sha(row["values_json"].encode()),
            ))
            current = material_values.setdefault(material, {"charge_id": row["charge_id"]})
            current.update(values)
    return tuple(semantics), material_values


def _lineage_paths(value: object, prefix: str, target: set[str]) -> None:
    if type(value) is dict:
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            target.add(path)
            _lineage_paths(item, path, target)
    elif type(value) is list:
        array_path = prefix + "[]"
        target.add(array_path)
        for item in value:
            _lineage_paths(item, array_path, target)


def _type1(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return float(ordered[max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))])


def _term_state(term: dict[str, object], raw: object) -> tuple[bool, bool]:
    if term.get("type") == "CATEGORY_IN":
        eligible = type(raw) is str and bool(raw)
        return eligible, eligible and raw in term.get("values", [])
    eligible = type(raw) in {int, float} and type(raw) is not bool and math.isfinite(float(raw))
    if not eligible:
        return False, False
    number = float(raw)
    lower, upper = float(term["lower"]), float(term["upper"])
    lower_ok = number >= lower if term.get("lowerInclusive") else number > lower
    upper_ok = number <= upper if term.get("upperInclusive") else number < upper
    return True, lower_ok and upper_ok


def _context_value(raw: object, boundaries: tuple[float, float, float] | None) -> object:
    if boundaries is None:
        return raw if type(raw) in {str, int, float, bool} and raw is not None else None
    if type(raw) not in {int, float} or type(raw) is bool or not math.isfinite(float(raw)):
        return None
    number = float(raw)
    return "Q1" if number <= boundaries[0] else "Q2" if number <= boundaries[1] else "Q3" if number <= boundaries[2] else "Q4"


def _sample_indices(seed: bytes, replicate: int, size: int) -> tuple[int, ...]:
    return tuple(
        int.from_bytes(hashlib.sha256(seed + struct.pack(">Q", replicate) + struct.pack(">Q", draw)).digest()[:8], "big") % size
        for draw in range(size)
    )


def _rule_bootstrap_trace(
    config: dict[str, object], rules: dict[str, object], summary: dict[str, object],
    criteria_id: str, material_values: dict[str, dict[str, object]],
) -> tuple[RuleBootstrapTrace, ...]:
    populations = {item["populationRef"]: item["materialKeys"] for item in summary["lineage"]["populations"]}
    discovery_ids = populations["DISCOVERY"]
    rows = [(key, material_values[key]) for key in discovery_ids if key in material_values]
    hierarchies = {item["equipmentType"]: item["levels"] for item in config["riskAdjustmentHierarchies"]}
    stages = {item["field"]: item["firstAvailableStage"] for item in config["fields"]}
    band_names = {field for levels in hierarchies.values() for level in levels for field in level if field.endswith("_band")}
    bands: dict[str, tuple[float, float, float] | None] = {}
    for band in band_names:
        base = band.removesuffix("_band")
        values = [float(row[base]) for _, row in rows if type(row.get(base)) in {int, float}
                  and type(row.get(base)) is not bool and math.isfinite(float(row[base]))]
        bands[band] = None if not values else (_type1(values, .25), _type1(values, .5), _type1(values, .75))
    replicates = int(config["bootstrap"]["replicates"])
    minimum_support = int(config["qualityRisk"]["minimumDiscoverySupport"])
    minimum_defects = int(config["qualityRisk"]["minimumCautionDefects"])
    traces = []
    for rule in rules["rules"]:
        metric = rule["discovery"]
        applicable = (
            metric["support"] >= minimum_support and metric["defects"] >= minimum_defects
            and metric["adjustedRate"] is not None and metric["comparatorAdjustedRate"] is not None
            and metric["relativeRisk"] is not None and math.isfinite(metric["relativeRisk"])
            and metric["relativeRisk"] > 0
        )
        if not applicable:
            continue
        fields = [] if rule["adjustmentKind"] == "UNADJUSTED_FALLBACK" else [
            field for field in hierarchies[rule["equipmentType"]][rule["adjustmentLevel"]]
            if field not in set(rule["adjustmentFieldsDropped"])
            and _STAGE_RANK[stages[field.removesuffix("_band")]] <= _STAGE_RANK[rule["firstAvailableStage"]]
        ]
        prepared = []
        raw_counts: dict[str, list[int]] = {}
        for _material, row in rows:
            states = [_term_state(term, row.get(term["field"])) for term in rule["predicate"]["allOf"]]
            if not all(state[0] for state in states) or row.get("judge") not in {"양품", "불량"}:
                continue
            candidate = all(state[1] for state in states)
            context: dict[str, object] = {}
            missing = False
            for field in fields:
                base = field.removesuffix("_band")
                value = _context_value(row.get(base), bands[field] if field.endswith("_band") else None)
                if value is None:
                    missing = True
                    break
                context[field] = value
            if missing:
                continue
            key = "" if not fields else _canonical_text(context)
            cell = raw_counts.setdefault(key, [0, 0])
            cell[0 if candidate else 1] += 1
            prepared.append((row["charge_id"], key, candidate, row["judge"]))
        informative = {key for key, counts in raw_counts.items() if counts[0] and counts[1]}
        prepared = [row for row in prepared if row[1] in informative]
        by_charge: dict[str, dict[str, list[int]]] = {}
        for charge, key, candidate, judge in prepared:
            cells = by_charge.setdefault(charge, {}).setdefault(key, [0, 0, 0, 0])
            index = 0 if candidate and judge == "불량" else 1 if candidate else 2 if judge == "불량" else 3
            cells[index] += 1
        charges = sorted(by_charge, key=str.encode)
        if not charges:
            continue
        seed = hashlib.sha256((criteria_id + "\0" + rule["ruleId"] + "\0rule-ci-v1").encode()).digest()
        first_sample = _sample_indices(seed, 0, len(charges))
        first_confusion_values = [0, 0, 0, 0]
        for charge_index in first_sample:
            for cells in by_charge[charges[charge_index]].values():
                for index, value in enumerate(cells):
                    first_confusion_values[index] += value
        first_confusion = tuple(first_confusion_values)
        ci = (metric["relativeRiskCiLower"], metric["relativeRiskCiUpper"])
        valid_replicates = replicates if ci != (None, None) else 0
        traces.append(RuleBootstrapTrace(
            rule["ruleId"], 0, first_sample, first_confusion, valid_replicates, ci,
        ))
    return tuple(sorted(traces, key=lambda item: item.rule_id.encode()))


def _holdout_trace(summary: dict[str, object]) -> tuple[HoldoutMetricTrace, ...]:
    result = []
    for item in summary["holdoutMetrics"]:
        confusion = (item["truePositive"], item["falsePositive"], item["trueNegative"], item["falseNegative"])
        metrics = tuple((name, item[name]) for name in (
            "alertRate", "precision", "recall", "baseDefectRate", "lift", "falseAlertsPer100",
        ))
        result.append(HoldoutMetricTrace(item["alertGrade"], confusion, metrics))
    return tuple(result)


def _build_trace(
    root: Path, config: dict[str, object], ranges: dict[str, object], rules: dict[str, object],
    summary: dict[str, object], criteria_id: str, bundle_id: str,
) -> V1ExecutionTrace:
    range_ids = _verify_range_ids(ranges)
    rule_ids = _verify_rule_ids(rules)
    event_semantics, material_values = _read_replay(root, criteria_id, bundle_id)
    lineage_keys: set[str] = set()
    _lineage_paths(summary.get("lineage"), "lineage", lineage_keys)
    return V1ExecutionTrace(
        _rule_bootstrap_trace(config, rules, summary, criteria_id, material_values),
        _holdout_trace(summary), rule_ids, range_ids,
        tuple(item.event_id for item in event_semantics), event_semantics,
        tuple(sorted(lineage_keys, key=str.encode)),
    )


def verify_v1_bundle(bundle_root: Path) -> VerifiedV1Bundle:
    """Authenticate one explicit v1 bundle before exposing its criteria ID."""
    root = Path(bundle_root)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("v1 bundle root must be an absolute existing directory")
    manifest, payloads = _verify_manifest(root)
    criteria_id, bundle_id = _verify_identities(manifest, payloads)
    config, ranges, rules, summary = _verify_json_bindings(
        manifest, payloads, criteria_id, bundle_id,
    )
    trace = _build_trace(root, config, ranges, rules, summary, criteria_id, bundle_id)
    return VerifiedV1Bundle(root, criteria_id, bundle_id, trace)


def project_v1_execution_trace(bundle: Path | VerifiedV1Bundle) -> V1ExecutionTrace:
    """Return the deterministic trace of an already verified or explicit v1 bundle."""
    if type(bundle) is VerifiedV1Bundle:
        return bundle.trace
    return verify_v1_bundle(Path(bundle)).trace
