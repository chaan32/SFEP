"""Build deterministic, stage-safe historical replay events."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
import csv
from dataclasses import dataclass
from datetime import date
import io
import math
import re
from types import MappingProxyType

import pandas as pd

from equipment_quality.deterministic import canonical_json_bytes, digest_json_id
from equipment_quality.models import AnalysisConfig, GenealogyResult
from equipment_quality.schema import validate_normative_instance


_SCHEMA_VERSION = "sfep-replay-events/v1"
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_BATCH_KINDS = ("CAST_DAY", "FURNACE_HOUR", "AP_DAY")
_BATCH_KIND_RANK = {kind: rank for rank, kind in enumerate(_BATCH_KINDS)}
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
_FURNACE_STAGES = (
    "FURNACE_CHARGED",
    "PREHEAT_COMPLETE",
    "HEAT_COMPLETE",
    "SOAK_COMPLETE",
    "FURNACE_EXTRACTED",
    "RM4_RECORDED",
)
_FURNACE_NUMBER = re.compile(r"^([1-4])(?:호기)?$")
_CSV_FIELDS = (
    "schema_version",
    "bundle_id",
    "criteria_id",
    "event_id",
    "replay_date",
    "replay_hour",
    "batch_kind",
    "batch_id",
    "equipment_batch_id",
    "batch_step",
    "time_precision",
    "material_key",
    "equipment_type",
    "equipment_id",
    "charge_id",
    "slab_no",
    "hr_coil_id",
    "ap_prod_id",
    "values_json",
)


def _immutable_values(values: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(values, Mapping):
        raise TypeError("values_json must be a mapping")
    try:
        entries = tuple(values.items())
    except (TypeError, ValueError) as error:
        raise TypeError("values_json mapping items must be key-value pairs") from error
    result: dict[str, object] = {}
    for entry in entries:
        if type(entry) is not tuple or len(entry) != 2:
            raise TypeError("values_json mapping items must be key-value pairs")
        key, value = entry
        if type(key) is not str:
            raise TypeError("values_json key must be a built-in string")
        if key in result:
            raise ValueError("values_json keys must be unique")
        if value is None or type(value) in {str, int}:
            pass
        elif type(value) is bool:
            raise TypeError("values_json scalar must not be boolean")
        elif type(value) is float:
            if not math.isfinite(value):
                raise ValueError("values_json numbers must be finite")
        else:
            raise TypeError("values_json values must be built-in scalars")
        result[key] = value
    return MappingProxyType(result)


@dataclass(frozen=True)
class ReplayEvent:
    schema_version: str
    bundle_id: str
    criteria_id: str
    event_id: str
    replay_date: date
    replay_hour: int | None
    batch_kind: str
    batch_id: str
    equipment_batch_id: str | None
    batch_step: str
    time_precision: str
    material_key: str
    equipment_type: str
    equipment_id: str
    charge_id: str
    slab_no: str
    hr_coil_id: str | None
    ap_prod_id: str | None
    values_json: Mapping[str, object]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or self.schema_version != _SCHEMA_VERSION:
            raise ValueError(f"invalid schema_version: {self.schema_version!r}")
        _sha256(self.bundle_id, "bundle_id")
        _sha256(self.criteria_id, "criteria_id")
        _sha256(self.event_id, "event_id")
        _exact_date(self.replay_date, "replay_date")
        if self.replay_hour is not None:
            _hour(self.replay_hour, "replay_hour")
        _enum(self.batch_kind, _BATCH_KIND_RANK, "batch_kind")
        _sha256(self.batch_id, "batch_id")
        if self.equipment_batch_id is not None:
            _sha256(self.equipment_batch_id, "equipment_batch_id")
        _enum(self.batch_step, _STAGE_RANK, "batch_step")
        _enum(
            self.time_precision,
            {"DAY", "HOUR_BUCKET", "SEQUENCE_ONLY"},
            "time_precision",
        )
        _sha256(self.material_key, "material_key")
        _enum(self.equipment_type, {"SM_CC", "FURNACE", "RM4", "AP"}, "equipment_type")
        _non_empty_string(self.equipment_id, "equipment_id")
        _non_empty_string(self.charge_id, "charge_id")
        _non_empty_string(self.slab_no, "slab_no")
        if self.hr_coil_id is not None:
            _non_empty_string(self.hr_coil_id, "hr_coil_id")
        if self.ap_prod_id is not None:
            _non_empty_string(self.ap_prod_id, "ap_prod_id")
        _validate_stage_matrix(self)
        if material_key(self.charge_id, self.slab_no) != self.material_key:
            raise ValueError("material_key does not match charge_id and slab_no")
        if batch_id(self.batch_kind, self.replay_date, self.replay_hour) != self.batch_id:
            raise ValueError("batch_id does not match replay batch semantics")
        expected_equipment_batch = equipment_batch_id(
            self.batch_id, self.equipment_type, self.equipment_id
        )
        if expected_equipment_batch != self.equipment_batch_id:
            raise ValueError("equipment_batch_id does not match equipment semantics")
        if _event_id(self, None) != self.event_id:
            raise ValueError("event_id does not match event semantics")
        object.__setattr__(self, "values_json", _immutable_values(self.values_json))

    def __hash__(self) -> int:
        return hash(
            (
                self.schema_version,
                self.bundle_id,
                self.criteria_id,
                self.event_id,
                self.replay_date,
                self.replay_hour,
                self.batch_kind,
                self.batch_id,
                self.equipment_batch_id,
                self.batch_step,
                self.time_precision,
                self.material_key,
                self.equipment_type,
                self.equipment_id,
                self.charge_id,
                self.slab_no,
                self.hr_coil_id,
                self.ap_prod_id,
                canonical_json_bytes(self.values_json),
            )
        )


def _non_empty_string(value: object, label: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError(f"{label} must be a normalized non-empty built-in string")
    return value


def _sha256(value: object, label: str) -> str:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 URI")
    return value


def _enum(value: object, accepted: Collection[str], label: str) -> str:
    if type(value) is not str or value not in accepted:
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def _validate_stage_matrix(event: ReplayEvent) -> None:
    stage = event.batch_step
    if stage == "CAST_RECORDED":
        if (
            event.batch_kind != "CAST_DAY"
            or event.replay_hour is not None
            or event.time_precision != "DAY"
            or event.equipment_type != "SM_CC"
            or event.equipment_batch_id is not None
            or event.hr_coil_id is not None
            or event.ap_prod_id is not None
        ):
            raise ValueError("CAST_RECORDED stage fields are inconsistent")
        return
    if stage in _FURNACE_STAGES[:-1]:
        if (
            event.batch_kind != "FURNACE_HOUR"
            or event.replay_hour is None
            or event.time_precision != "HOUR_BUCKET"
            or event.equipment_type != "FURNACE"
            or event.equipment_batch_id is None
            or event.hr_coil_id is not None
            or event.ap_prod_id is not None
        ):
            raise ValueError(f"{stage} stage fields are inconsistent")
        return
    if stage == "RM4_RECORDED":
        if (
            event.batch_kind != "FURNACE_HOUR"
            or event.replay_hour is None
            or event.time_precision != "SEQUENCE_ONLY"
            or event.equipment_type != "RM4"
            or event.equipment_id != "RM4_PROCESS"
            or event.equipment_batch_id is not None
            or event.hr_coil_id is None
            or event.ap_prod_id is not None
        ):
            raise ValueError("RM4_RECORDED stage fields are inconsistent")
        return
    if (
        event.batch_kind != "AP_DAY"
        or event.replay_hour is not None
        or event.time_precision != "DAY"
        or event.equipment_type != "AP"
        or event.equipment_batch_id is not None
        or event.hr_coil_id is None
        or event.ap_prod_id is None
    ):
        raise ValueError("AP_RECORDED_WITH_RESULT stage fields are inconsistent")


def _exact_date(value: object, label: str) -> date:
    if type(value) is not date:
        raise TypeError(f"{label} must be a built-in date")
    return value


def _hour(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 23:
        raise ValueError(f"{label} must be a built-in hour from 0 to 23")
    return value


def material_key(charge_id: str, slab_no: str) -> str:
    """Return the stable material identity for one normalized Charge/Slab pair."""
    return _material_key(charge_id, slab_no, None)


@dataclass
class _DigestRegistry:
    preimages: dict[str, bytes]

    def digest(
        self,
        namespace: str,
        value: Mapping[str, object],
        *,
        event: bool = False,
    ) -> str:
        digest = _validated_digest_json_id(namespace, value)
        preimage = namespace.encode("utf-8") + b"\n" + canonical_json_bytes(value)
        prior = self.preimages.get(digest)
        if prior is not None:
            if prior != preimage:
                raise ValueError("replay digest preimage collision")
            if event:
                raise ValueError("duplicate event ID")
            return digest
        self.preimages[digest] = preimage
        return digest


def _validated_digest_json_id(
    namespace: str, value: Mapping[str, object]
) -> str:
    return _sha256(
        digest_json_id(namespace, value),
        "digest_json_id result",
    )


def _digest(
    namespace: str,
    value: Mapping[str, object],
    registry: _DigestRegistry | None,
    *,
    event: bool = False,
) -> str:
    if registry is None:
        return _validated_digest_json_id(namespace, value)
    return registry.digest(namespace, value, event=event)


def _material_key(
    charge_id: object,
    slab_no: object,
    registry: _DigestRegistry | None,
) -> str:
    charge = _non_empty_string(charge_id, "charge_id")
    slab = _non_empty_string(slab_no, "slab_no")
    return _digest(
        "sfep-material-key/v1",
        {"chargeId": charge, "slabNo": slab},
        registry,
    )


def batch_id(kind: str, date: date, hour: int | None) -> str:
    """Return the stable replay-batch identity without fabricating day hours."""
    return _batch_id(kind, date, hour, None)


def _batch_id(
    kind: object,
    replay_date_value: object,
    hour: object,
    registry: _DigestRegistry | None,
) -> str:
    if type(kind) is not str or kind not in _BATCH_KIND_RANK:
        raise ValueError(f"unknown batch kind: {kind!r}")
    replay_date = _exact_date(replay_date_value, "replay date")
    value: dict[str, object] = {
        "batchKind": kind,
        "replayDate": replay_date.isoformat(),
    }
    if kind == "FURNACE_HOUR":
        value["replayHour"] = _hour(hour, "replay hour")
    elif hour is not None:
        raise ValueError("day batches must not contain a replay hour")
    return _digest("sfep-batch-id/v1", value, registry)


def equipment_batch_id(
    batch_id: str, equipment_type: str, equipment_id: str
) -> str | None:
    """Return a furnace sub-batch identity; other equipment has no sub-batch."""
    return _equipment_batch_id(batch_id, equipment_type, equipment_id, None)


def _equipment_batch_id(
    batch_id_value: object,
    equipment_type: object,
    equipment_id_value: object,
    registry: _DigestRegistry | None,
) -> str | None:
    batch = _sha256(batch_id_value, "batch_id")
    equipment = _non_empty_string(equipment_id_value, "equipment_id")
    if type(equipment_type) is not str or equipment_type not in {
        "SM_CC", "FURNACE", "RM4", "AP"
    }:
        raise ValueError(f"unknown equipment type: {equipment_type!r}")
    if equipment_type != "FURNACE":
        return None
    return _digest(
        "sfep-equipment-batch-id/v1",
        {
            "batchId": batch,
            "equipmentId": equipment,
            "equipmentType": "FURNACE",
        },
        registry,
    )


def event_id(event: ReplayEvent) -> str:
    """Return an event identity from only its normative semantic fields."""
    if type(event) is not ReplayEvent:
        raise TypeError("event must be an exact ReplayEvent")
    return _event_id(event, None)


def _event_id(event: ReplayEvent, registry: _DigestRegistry | None) -> str:
    return _digest(
        "sfep-event-id/v1",
        {
            "batchId": _sha256(event.batch_id, "batch_id"),
            "batchStep": event.batch_step,
            "equipmentBatchId": event.equipment_batch_id,
            "equipmentId": event.equipment_id,
            "equipmentType": event.equipment_type,
            "materialKey": _sha256(event.material_key, "material_key"),
        },
        registry,
        event=registry is not None,
    )


def _missing(value: object) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return type(result) is bool and result


def _wire_scalar(value: object, data_type: str) -> object:
    if _missing(value):
        return None
    if data_type == "DATE":
        return _exact_date(value, "stage date").isoformat()
    if data_type == "HOUR":
        return _hour(value, "stage hour")
    if data_type == "STRING":
        if type(value) is not str:
            raise TypeError("stage string values must be built-in strings")
        return value
    if data_type == "NUMBER":
        if type(value) is bool:
            raise TypeError("stage numeric values must not be boolean")
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise TypeError("stage numeric values must be real scalars") from error
        if not math.isfinite(number):
            raise ValueError("stage numeric values must be finite")
        return number
    raise ValueError(f"unknown configured data type: {data_type}")


def _stage_fields(config: AnalysisConfig) -> dict[str, tuple[tuple[str, str], ...]]:
    by_stage = {stage: [] for stage in _STAGE_RANK}
    seen: set[str] = set()
    judge_policy_seen = False
    for configured in config.fields:
        name = configured["field"]
        stage = configured["firstAvailableStage"]
        role = configured["featureRole"]
        data_type = configured["dataType"]
        if type(name) is not str or name in seen:
            raise ValueError("configured replay fields must have unique built-in names")
        seen.add(name)
        if stage not in by_stage:
            raise ValueError(f"unknown configured replay stage: {stage}")
        if name == "judge":
            judge_policy_seen = True
            if role != "RESULT" or stage != "AP_RECORDED_WITH_RESULT":
                raise ValueError(
                    "judge must remain RESULT at AP_RECORDED_WITH_RESULT"
                )
        if role != "IDENTIFIER":
            by_stage[stage].append((name, data_type))
    if not judge_policy_seen:
        raise ValueError("judge must be configured at AP_RECORDED_WITH_RESULT")
    return {
        stage: tuple(fields)
        for stage, fields in by_stage.items()
    }


def _event_sort_key(event: ReplayEvent) -> tuple[object, ...]:
    if event.equipment_type == "FURNACE":
        match = _FURNACE_NUMBER.fullmatch(event.equipment_id)
        if match is None:
            raise ValueError(
                "furnace equipment_id must be 1..4 or 1호기..4호기 for sorting"
            )
        equipment_sort: tuple[object, ...] = (0, int(match.group(1)), b"")
    else:
        equipment_sort = (1, 0, event.equipment_id.encode("utf-8"))
    return (
        event.replay_date,
        _BATCH_KIND_RANK[event.batch_kind],
        -1 if event.replay_hour is None else event.replay_hour,
        event.batch_id.encode("utf-8"),
        _STAGE_RANK[event.batch_step],
        equipment_sort,
        event.material_key.encode("utf-8"),
        event.event_id.encode("utf-8"),
    )


@dataclass(frozen=True)
class _ReplayRowSnapshot:
    row: Mapping[str, object]
    material_identity: tuple[str, str]
    cast_date: date
    sm_plant: str
    furnace_date: date
    furnace_hour: int
    furnace_no: str
    hr_coil_id: str


@dataclass(frozen=True)
class _QualityRowSnapshot:
    row: Mapping[str, object]
    material_identity: tuple[str, str]
    ap_date: date
    ap_plant: str
    ap_prod_id: str


def _required_date(row: Mapping[str, object], field: str) -> date:
    value = row.get(field)
    if _missing(value):
        raise ValueError(f"{field} is required for replay scheduling")
    return _exact_date(value, field)


def _required_hour(row: Mapping[str, object], field: str) -> int:
    value = row.get(field)
    if _missing(value):
        raise ValueError(f"{field} is required for replay scheduling")
    return _hour(value, field)


def _snapshot_replay_row(row: Mapping[str, object]) -> _ReplayRowSnapshot:
    copied = MappingProxyType(dict(row))
    charge = _non_empty_string(copied.get("charge_id"), "charge_id")
    slab = _non_empty_string(copied.get("slab_no"), "slab_no")
    furnace = _non_empty_string(copied.get("furnace_no"), "furnace_no")
    if _FURNACE_NUMBER.fullmatch(furnace) is None:
        raise ValueError("furnace_no must be 1..4 or 1호기..4호기")
    return _ReplayRowSnapshot(
        row=copied,
        material_identity=(charge, slab),
        cast_date=_required_date(copied, "cast_date"),
        sm_plant=_non_empty_string(copied.get("sm_plant"), "sm_plant"),
        furnace_date=_required_date(copied, "f_ext_date"),
        furnace_hour=_required_hour(copied, "f_ext_time"),
        furnace_no=furnace,
        hr_coil_id=_non_empty_string(copied.get("hr_coil_id"), "hr_coil_id"),
    )


def _snapshot_quality_row(row: Mapping[str, object]) -> _QualityRowSnapshot:
    copied = MappingProxyType(dict(row))
    charge = _non_empty_string(copied.get("charge_id"), "quality charge_id")
    slab = _non_empty_string(copied.get("slab_no"), "quality slab_no")
    return _QualityRowSnapshot(
        row=copied,
        material_identity=(charge, slab),
        ap_date=_required_date(copied, "ap_date"),
        ap_plant=_non_empty_string(copied.get("ap_plant"), "ap_plant"),
        ap_prod_id=_non_empty_string(copied.get("ap_prod_id"), "ap_prod_id"),
    )


def _make_event(
    *,
    row: Mapping[str, object],
    bundle_id_value: str,
    criteria_id_value: str,
    replay_date: date,
    replay_hour: int | None,
    batch_kind: str,
    batch_value: str,
    batch_step: str,
    time_precision: str,
    equipment_type: str,
    equipment_id_value: str,
    equipment_batch_value: str | None,
    material_value: str,
    stage_fields: tuple[tuple[str, str], ...],
    registry: _DigestRegistry,
) -> ReplayEvent:
    values = {
        name: _wire_scalar(row.get(name), data_type)
        for name, data_type in stage_fields
    }
    hr_coil_id = (
        _non_empty_string(row.get("hr_coil_id"), "hr_coil_id")
        if batch_step in {"RM4_RECORDED", "AP_RECORDED_WITH_RESULT"}
        else None
    )
    ap_prod_id = (
        _non_empty_string(row.get("ap_prod_id"), "ap_prod_id")
        if batch_step == "AP_RECORDED_WITH_RESULT"
        else None
    )
    event_value = {
        "batchId": batch_value,
        "batchStep": batch_step,
        "equipmentBatchId": equipment_batch_value,
        "equipmentId": equipment_id_value,
        "equipmentType": equipment_type,
        "materialKey": material_value,
    }
    event_digest = _digest(
        "sfep-event-id/v1", event_value, registry, event=True
    )
    return ReplayEvent(
        schema_version=_SCHEMA_VERSION,
        bundle_id=bundle_id_value,
        criteria_id=criteria_id_value,
        event_id=event_digest,
        replay_date=replay_date,
        replay_hour=replay_hour,
        batch_kind=batch_kind,
        batch_id=batch_value,
        equipment_batch_id=equipment_batch_value,
        batch_step=batch_step,
        time_precision=time_precision,
        material_key=material_value,
        equipment_type=equipment_type,
        equipment_id=equipment_id_value,
        charge_id=_non_empty_string(row.get("charge_id"), "charge_id"),
        slab_no=_non_empty_string(row.get("slab_no"), "slab_no"),
        hr_coil_id=hr_coil_id,
        ap_prod_id=ap_prod_id,
        values_json=values,
    )


def build_replay_events(
    genealogy: GenealogyResult,
    bundle_id: str,
    criteria_id: str,
    config: AnalysisConfig,
) -> list[ReplayEvent]:
    """Build all dated stage events and sort each hour in furnace lockstep."""
    bundle = _sha256(bundle_id, "bundle_id")
    criteria = _sha256(criteria_id, "criteria_id")
    fields_by_stage = _stage_fields(config)
    rows = tuple(
        _snapshot_replay_row(row)
        for row in genealogy.replay_rows.to_dict(orient="records")
    )
    quality_rows = tuple(
        _snapshot_quality_row(row)
        for row in genealogy.quality_rows.to_dict(orient="records")
    )
    replay_keys = {row.material_identity for row in rows}
    quality_keys_list = [row.material_identity for row in quality_rows]
    if len(set(quality_keys_list)) != len(quality_keys_list):
        raise ValueError("quality rows must contain unique material identities")
    quality_by_key = {
        row.material_identity: row
        for row in quality_rows
    }
    quality_keys = set(quality_by_key)
    if not quality_keys.issubset(replay_keys):
        raise ValueError("quality rows must be a subset of replay rows")
    registry = _DigestRegistry(preimages={})
    events: list[ReplayEvent] = []
    for snapshot in rows:
        row = snapshot.row
        row_key = snapshot.material_identity
        material = _material_key(*row_key, registry)

        cast_batch = _batch_id("CAST_DAY", snapshot.cast_date, None, registry)
        events.append(
            _make_event(
                row=row, bundle_id_value=bundle, criteria_id_value=criteria,
                replay_date=snapshot.cast_date, replay_hour=None, batch_kind="CAST_DAY",
                batch_value=cast_batch, batch_step="CAST_RECORDED",
                time_precision="DAY", equipment_type="SM_CC",
                equipment_id_value=snapshot.sm_plant,
                equipment_batch_value=None, material_value=material,
                stage_fields=fields_by_stage["CAST_RECORDED"],
                registry=registry,
            )
        )

        furnace_batch = _batch_id(
            "FURNACE_HOUR", snapshot.furnace_date, snapshot.furnace_hour, registry
        )
        furnace_equipment_batch = _equipment_batch_id(
            furnace_batch, "FURNACE", snapshot.furnace_no, registry
        )
        for stage in _FURNACE_STAGES:
            is_rm4 = stage == "RM4_RECORDED"
            events.append(
                _make_event(
                    row=row, bundle_id_value=bundle, criteria_id_value=criteria,
                    replay_date=snapshot.furnace_date,
                    replay_hour=snapshot.furnace_hour,
                    batch_kind="FURNACE_HOUR", batch_value=furnace_batch,
                    batch_step=stage,
                    time_precision="SEQUENCE_ONLY" if is_rm4 else "HOUR_BUCKET",
                    equipment_type="RM4" if is_rm4 else "FURNACE",
                    equipment_id_value=(
                        "RM4_PROCESS" if is_rm4 else snapshot.furnace_no
                    ),
                    equipment_batch_value=(
                        None if is_rm4 else furnace_equipment_batch
                    ),
                    material_value=material, stage_fields=fields_by_stage[stage],
                    registry=registry,
                )
            )

        if row_key in quality_keys:
            quality = quality_by_key[row_key]
            ap_row = dict(row)
            for name, _ in fields_by_stage["AP_RECORDED_WITH_RESULT"]:
                ap_row[name] = quality.row.get(name)
            ap_row["ap_date"] = quality.ap_date
            ap_row["ap_plant"] = quality.ap_plant
            ap_row["ap_prod_id"] = quality.ap_prod_id
            ap_batch = _batch_id("AP_DAY", quality.ap_date, None, registry)
            events.append(
                _make_event(
                    row=ap_row, bundle_id_value=bundle, criteria_id_value=criteria,
                    replay_date=quality.ap_date, replay_hour=None, batch_kind="AP_DAY",
                    batch_value=ap_batch, batch_step="AP_RECORDED_WITH_RESULT",
                    time_precision="DAY", equipment_type="AP",
                    equipment_id_value=quality.ap_plant,
                    equipment_batch_value=None, material_value=material,
                    stage_fields=fields_by_stage["AP_RECORDED_WITH_RESULT"],
                    registry=registry,
                )
            )
    expected_count = 7 * len(rows) + len(quality_rows)
    if len(events) != expected_count:
        raise RuntimeError(
            f"replay event cardinality mismatch: expected {expected_count}, got {len(events)}"
        )
    return sorted(events, key=_event_sort_key)


def serialize_replay_events(events: Sequence[ReplayEvent]) -> bytes:
    """Serialize an already strictly sorted event stream as normative CSV bytes."""
    snapshot = tuple(events)
    registry = _DigestRegistry(preimages={})
    prior_sort_key: tuple[object, ...] | None = None
    envelope_identity: tuple[str, str] | None = None
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(_CSV_FIELDS)
    for event in snapshot:
        if type(event) is not ReplayEvent:
            raise TypeError("replay CSV input must contain exact ReplayEvent instances")
        values = _immutable_values(event.values_json)
        replay_date = _exact_date(event.replay_date, "replay_date")
        row = {
            "schema_version": event.schema_version,
            "bundle_id": event.bundle_id,
            "criteria_id": event.criteria_id,
            "event_id": event.event_id,
            "replay_date": replay_date.isoformat(),
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
            "values_json": dict(values),
        }
        validate_normative_instance("replay_event_row.schema.json", row)
        current_identity = (event.bundle_id, event.criteria_id)
        if envelope_identity is None:
            envelope_identity = current_identity
        else:
            if event.bundle_id != envelope_identity[0]:
                raise ValueError("replay CSV rows must have one bundle_id")
            if event.criteria_id != envelope_identity[1]:
                raise ValueError("replay CSV rows must have one criteria_id")
        _verify_id_claims(event, registry)
        sort_key = _event_sort_key(event)
        if prior_sort_key is not None and sort_key <= prior_sort_key:
            raise ValueError("replay events must be strictly monotonic by the exact sort tuple")
        prior_sort_key = sort_key
        values_bytes = canonical_json_bytes(values)
        if not values_bytes.endswith(b"\n"):
            raise ValueError("canonical values_json must end with LF")
        values_text = values_bytes[:-1].decode("utf-8")
        writer.writerow(
            (
                row["schema_version"],
                row["bundle_id"],
                row["criteria_id"],
                row["event_id"],
                row["replay_date"],
                row["replay_hour"],
                row["batch_kind"],
                row["batch_id"],
                row["equipment_batch_id"],
                row["batch_step"],
                row["time_precision"],
                row["material_key"],
                row["equipment_type"],
                row["equipment_id"],
                row["charge_id"],
                row["slab_no"],
                row["hr_coil_id"],
                row["ap_prod_id"],
                values_text,
            )
        )
    return output.getvalue().encode("utf-8")


def _verify_id_claims(event: ReplayEvent, registry: _DigestRegistry) -> None:
    material = _digest(
        "sfep-material-key/v1",
        {"chargeId": event.charge_id, "slabNo": event.slab_no},
        registry,
    )
    if material != event.material_key:
        raise ValueError("material_key does not match its semantic preimage")

    batch_value: dict[str, object] = {
        "batchKind": event.batch_kind,
        "replayDate": event.replay_date.isoformat(),
    }
    if event.batch_kind == "FURNACE_HOUR":
        batch_value["replayHour"] = event.replay_hour
    batch = _digest("sfep-batch-id/v1", batch_value, registry)
    if batch != event.batch_id:
        raise ValueError("batch_id does not match its semantic preimage")

    if event.equipment_batch_id is not None:
        equipment_batch = _digest(
            "sfep-equipment-batch-id/v1",
            {
                "batchId": event.batch_id,
                "equipmentId": event.equipment_id,
                "equipmentType": event.equipment_type,
            },
            registry,
        )
        if equipment_batch != event.equipment_batch_id:
            raise ValueError(
                "equipment_batch_id does not match its semantic preimage"
            )

    event_digest = _digest(
        "sfep-event-id/v1",
        {
            "batchId": event.batch_id,
            "batchStep": event.batch_step,
            "equipmentBatchId": event.equipment_batch_id,
            "equipmentId": event.equipment_id,
            "equipmentType": event.equipment_type,
            "materialKey": event.material_key,
        },
        registry,
        event=True,
    )
    if event_digest != event.event_id:
        raise ValueError("event_id does not match its semantic preimage")
