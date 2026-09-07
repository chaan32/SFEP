"""Materialize monitoring definitions from the validated analysis config."""

from __future__ import annotations

from dataclasses import dataclass

from equipment_quality.models import AnalysisConfig


STAGE_ORDER = (
    "CAST_RECORDED",
    "FURNACE_CHARGED",
    "PREHEAT_COMPLETE",
    "HEAT_COMPLETE",
    "SOAK_COMPLETE",
    "FURNACE_EXTRACTED",
    "RM4_RECORDED",
    "AP_RECORDED_WITH_RESULT",
)
STAGE_RANK = {stage: rank for rank, stage in enumerate(STAGE_ORDER)}

# These are structural replay facts that cannot be inferred from a field policy.
_EVENT_DATE_BY_STAGE = {
    "CAST_RECORDED": "cast_date",
    "FURNACE_CHARGED": "f_ext_date",
    "PREHEAT_COMPLETE": "f_ext_date",
    "HEAT_COMPLETE": "f_ext_date",
    "SOAK_COMPLETE": "f_ext_date",
    "FURNACE_EXTRACTED": "f_ext_date",
    "RM4_RECORDED": "f_ext_date",
    "AP_RECORDED_WITH_RESULT": "ap_date",
}
# RM4 has no source equipment identifier in the binding source model. This is
# the sole equipment-type identity policy that cannot be inferred from fields.
_PROCESS_EQUIPMENT_IDS = {"RM4": "RM4_PROCESS"}
_EXCLUDED_ROLES = {"IDENTIFIER", "TIME", "RESULT"}


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    field_role: str
    data_type: str
    first_stage: str
    event_date_column: str
    equipment_type: str
    equipment_id_column: str | None
    equipment_id_value: str | None
    context_hierarchy: tuple[tuple[str, ...], ...]


def _configured_fields(config: AnalysisConfig) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for configured in config.fields:
        field = dict(configured)
        name = field["field"]
        if type(name) is not str or not name:
            raise ValueError("configured field names must be non-empty strings")
        if name in result:
            raise ValueError(f"configured field names must be unique: {name}")
        result[name] = field
    return result


def _context_stage(
    context_field: str,
    fields: dict[str, dict[str, object]],
) -> str:
    configured = fields.get(context_field)
    if configured is None and context_field.endswith("_band"):
        configured = fields.get(context_field.removesuffix("_band"))
        if configured is not None and configured["dataType"] != "NUMBER":
            raise ValueError(
                f"context field {context_field} must derive from a numeric field"
            )
    if configured is None:
        raise ValueError(f"context field is not configured: {context_field}")
    stage = configured["firstAvailableStage"]
    if stage not in STAGE_RANK:
        raise ValueError(f"unknown first available stage: {stage}")
    return str(stage)


def _equipment_identities(
    fields: dict[str, dict[str, object]],
) -> dict[str, tuple[str | None, str | None]]:
    equipment_types = {
        str(configured["equipmentType"]) for configured in fields.values()
    }
    result: dict[str, tuple[str | None, str | None]] = {}
    for equipment_type in sorted(
        equipment_types, key=lambda value: value.encode("utf-8")
    ):
        identifiers = tuple(
            str(configured["field"])
            for configured in fields.values()
            if configured["equipmentType"] == equipment_type
            and configured["featureRole"] == "EQUIPMENT_IDENTIFIER"
        )
        process_id = _PROCESS_EQUIPMENT_IDS.get(equipment_type)
        if process_id is not None:
            if identifiers:
                raise ValueError(
                    f"process equipment type {equipment_type} must have no source "
                    "equipment identifier"
                )
            result[equipment_type] = (None, process_id)
            continue
        if len(identifiers) != 1:
            raise ValueError(
                f"exactly one equipment identifier is required for {equipment_type}"
            )
        result[equipment_type] = (identifiers[0], None)
    return result


def definitions(config: AnalysisConfig) -> tuple[FeatureDefinition, ...]:
    """Return each config-eligible field exactly once with stage-safe contexts."""
    fields = _configured_fields(config)
    equipment_identities = _equipment_identities(fields)
    produced: list[FeatureDefinition] = []
    for field in fields.values():
        role = field["featureRole"]
        if role in _EXCLUDED_ROLES:
            continue
        name = str(field["field"])
        stage = str(field["firstAvailableStage"])
        equipment_type = str(field["equipmentType"])
        if stage not in STAGE_RANK:
            raise ValueError(f"unknown first available stage: {stage}")
        try:
            configured_hierarchy = config.range_context_hierarchies[equipment_type]
        except KeyError as error:
            raise ValueError(
                f"missing range context hierarchy for {equipment_type}"
            ) from error
        stage_rank = STAGE_RANK[stage]
        hierarchy = tuple(
            tuple(
                context_field
                for context_field in level
                if STAGE_RANK[_context_stage(context_field, fields)] <= stage_rank
            )
            for level in configured_hierarchy
        )
        produced.append(
            FeatureDefinition(
                name=name,
                field_role=str(role),
                data_type=str(field["dataType"]),
                first_stage=stage,
                event_date_column=_EVENT_DATE_BY_STAGE[stage],
                equipment_type=equipment_type,
                equipment_id_column=equipment_identities[equipment_type][0],
                equipment_id_value=equipment_identities[equipment_type][1],
                context_hierarchy=hierarchy,
            )
        )
    produced.sort(
        key=lambda item: (STAGE_RANK[item.first_stage], item.name.encode("utf-8"))
    )
    return tuple(produced)
