"""Config-driven feature role and stage contracts for Task 4."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from equipment_quality.feature_roles import definitions
from equipment_quality.schema import load_analysis_config


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# This is intentionally a hand-authored oracle. It neither loads config values
# nor calls production helpers to construct expected semantics.
SM_CONTEXT = (
    ("sm_plant", "steel_grade", "steel_usage"),
    ("sm_plant", "steel_grade"),
    ("sm_plant",),
)
FURNACE_CONTEXT = (
    (
        "furnace_no",
        "steel_grade",
        "steel_usage",
        "f_jangip_gubun",
        "slab_width_band",
    ),
    ("furnace_no", "steel_grade", "f_jangip_gubun"),
    ("furnace_no", "f_jangip_gubun"),
    ("furnace_no",),
)
RM4_CONTEXT = (
    ("steel_grade", "steel_usage", "hr_thick_band", "hr_width_band"),
    ("steel_grade", "steel_usage"),
    ("steel_grade",),
    (),
)
AP_CONTEXT = (
    (
        "ap_plant",
        "steel_grade",
        "steel_usage",
        "ap_shift",
        "ap_thick_band",
        "ap_width_band",
    ),
    ("ap_plant", "steel_grade", "steel_usage"),
    ("ap_plant", "steel_grade"),
    ("ap_plant",),
)

# Values are: role, data type, first stage, event date, equipment type,
# source equipment-ID column, process equipment-ID value, context hierarchy.
EXPECTED_FEATURE_DEFINITIONS = {
    "sm_plant": ("EQUIPMENT_IDENTIFIER", "STRING", "CAST_RECORDED", "cast_date", "SM_CC", "sm_plant", None, SM_CONTEXT),
    "steel_grade": ("CONTEXT", "STRING", "CAST_RECORDED", "cast_date", "SM_CC", "sm_plant", None, SM_CONTEXT),
    "steel_usage": ("CONTEXT", "STRING", "CAST_RECORDED", "cast_date", "SM_CC", "sm_plant", None, SM_CONTEXT),
    "cc_gubun": ("CONTEXT", "STRING", "CAST_RECORDED", "cast_date", "SM_CC", "sm_plant", None, SM_CONTEXT),
    "slab_gubun": ("CONTEXT", "STRING", "CAST_RECORDED", "cast_date", "SM_CC", "sm_plant", None, SM_CONTEXT),
    "tundish_temp": ("DIRECT_OPERATION", "NUMBER", "CAST_RECORDED", "cast_date", "SM_CC", "sm_plant", None, SM_CONTEXT),
    "mlac_ratio": ("DIRECT_OPERATION", "NUMBER", "CAST_RECORDED", "cast_date", "SM_CC", "sm_plant", None, SM_CONTEXT),
    "delta_ferrite": ("PRODUCT_STATE_REFERENCE", "NUMBER", "CAST_RECORDED", "cast_date", "SM_CC", "sm_plant", None, SM_CONTEXT),
    "ingre_cr": ("PRODUCT_STATE_REFERENCE", "NUMBER", "CAST_RECORDED", "cast_date", "SM_CC", "sm_plant", None, SM_CONTEXT),
    "ingre_ni": ("PRODUCT_STATE_REFERENCE", "NUMBER", "CAST_RECORDED", "cast_date", "SM_CC", "sm_plant", None, SM_CONTEXT),
    "ingre_s": ("PRODUCT_STATE_REFERENCE", "NUMBER", "CAST_RECORDED", "cast_date", "SM_CC", "sm_plant", None, SM_CONTEXT),
    "slab_grind": ("PRODUCT_STATE_REFERENCE", "STRING", "CAST_RECORDED", "cast_date", "SM_CC", "sm_plant", None, SM_CONTEXT),
    "furnace_no": ("EQUIPMENT_IDENTIFIER", "STRING", "FURNACE_CHARGED", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_jangip_gubun": ("CONTEXT", "STRING", "FURNACE_CHARGED", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_jangip_temp": ("DIRECT_OPERATION", "NUMBER", "FURNACE_CHARGED", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "slab_width": ("PRODUCT_STATE_REFERENCE", "NUMBER", "FURNACE_CHARGED", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_pre_temp": ("DIRECT_OPERATION", "NUMBER", "PREHEAT_COMPLETE", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_pre_interval": ("DIRECT_OPERATION", "NUMBER", "PREHEAT_COMPLETE", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_heat_temp": ("DIRECT_OPERATION", "NUMBER", "HEAT_COMPLETE", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_heat_interval": ("DIRECT_OPERATION", "NUMBER", "HEAT_COMPLETE", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_sock_temp": ("DIRECT_OPERATION", "NUMBER", "SOAK_COMPLETE", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_sock_interval": ("DIRECT_OPERATION", "NUMBER", "SOAK_COMPLETE", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_bfg": ("DIRECT_OPERATION", "NUMBER", "FURNACE_EXTRACTED", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_cog": ("DIRECT_OPERATION", "NUMBER", "FURNACE_EXTRACTED", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_ldg": ("DIRECT_OPERATION", "NUMBER", "FURNACE_EXTRACTED", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_bfg_ratio": ("DIRECT_OPERATION", "NUMBER", "FURNACE_EXTRACTED", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_cog_ratio": ("DIRECT_OPERATION", "NUMBER", "FURNACE_EXTRACTED", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "f_ldg_ratio": ("DIRECT_OPERATION", "NUMBER", "FURNACE_EXTRACTED", "f_ext_date", "FURNACE", "furnace_no", None, FURNACE_CONTEXT),
    "hr_thick": ("PRODUCT_STATE_REFERENCE", "NUMBER", "RM4_RECORDED", "f_ext_date", "RM4", None, "RM4_PROCESS", RM4_CONTEXT),
    "hr_width": ("PRODUCT_STATE_REFERENCE", "NUMBER", "RM4_RECORDED", "f_ext_date", "RM4", None, "RM4_PROCESS", RM4_CONTEXT),
    "rm4_temp": ("DIRECT_OPERATION", "NUMBER", "RM4_RECORDED", "f_ext_date", "RM4", None, "RM4_PROCESS", RM4_CONTEXT),
    "rm_pitch": ("DIRECT_OPERATION", "NUMBER", "RM4_RECORDED", "f_ext_date", "RM4", None, "RM4_PROCESS", RM4_CONTEXT),
    "ap_plant": ("EQUIPMENT_IDENTIFIER", "STRING", "AP_RECORDED_WITH_RESULT", "ap_date", "AP", "ap_plant", None, AP_CONTEXT),
    "ap_shift": ("CONTEXT", "STRING", "AP_RECORDED_WITH_RESULT", "ap_date", "AP", "ap_plant", None, AP_CONTEXT),
    "ap_thick": ("PRODUCT_STATE_REFERENCE", "NUMBER", "AP_RECORDED_WITH_RESULT", "ap_date", "AP", "ap_plant", None, AP_CONTEXT),
    "ap_width": ("PRODUCT_STATE_REFERENCE", "NUMBER", "AP_RECORDED_WITH_RESULT", "ap_date", "AP", "ap_plant", None, AP_CONTEXT),
    "ap_line_speed": ("DIRECT_OPERATION", "NUMBER", "AP_RECORDED_WITH_RESULT", "ap_date", "AP", "ap_plant", None, AP_CONTEXT),
}

EXCLUDED_IDENTITY_TIME_RESULT_COLUMNS = frozenset(
    {
        "charge_id",
        "slab_no",
        "hr_coil_id",
        "ap_prod_id",
        "cast_date",
        "f_ext_date",
        "f_ext_time",
        "hr_date",
        "ap_date",
        "judge",
    }
)


def analysis_config():
    return load_analysis_config(REPOSITORY_ROOT / "analysis/analysis_config.json")


def _replace_field(config, field_name: str, **changes: object):
    fields = []
    for field in config.fields:
        item = dict(field)
        if item["field"] == field_name:
            item.update(changes)
        fields.append(item)
    return replace(config, fields=tuple(fields))


def test_every_feature_definition_matches_the_hand_authored_semantic_oracle():
    produced = definitions(analysis_config())
    observed = {
        definition.name: (
            definition.field_role,
            definition.data_type,
            definition.first_stage,
            definition.event_date_column,
            definition.equipment_type,
            definition.equipment_id_column,
            definition.equipment_id_value,
            definition.context_hierarchy,
        )
        for definition in produced
    }

    assert observed == EXPECTED_FEATURE_DEFINITIONS
    assert len(observed) == 37
    assert EXCLUDED_IDENTITY_TIME_RESULT_COLUMNS.isdisjoint(observed)


def test_product_state_roles_and_structural_event_and_equipment_mappings_are_exact():
    by_name = {item.name: item for item in definitions(analysis_config())}

    assert by_name["slab_width"].field_role == "PRODUCT_STATE_REFERENCE"
    assert by_name["slab_width"].first_stage == "FURNACE_CHARGED"
    assert by_name["slab_width"].event_date_column == "f_ext_date"
    assert by_name["slab_width"].equipment_id_column == "furnace_no"
    assert by_name["rm4_temp"].event_date_column == "f_ext_date"
    assert by_name["rm4_temp"].equipment_id_column is None
    assert by_name["ap_line_speed"].event_date_column == "ap_date"
    assert by_name["ap_line_speed"].equipment_id_column == "ap_plant"


def test_slab_grind_stays_categorical_product_state_evidence():
    config = analysis_config()
    configured = next(field for field in config.fields if field["field"] == "slab_grind")
    produced = next(item for item in definitions(config) if item.name == "slab_grind")

    assert configured["dataType"] == "STRING"
    assert produced.field_role == "PRODUCT_STATE_REFERENCE"
    assert produced.first_stage == "CAST_RECORDED"


def test_definition_role_is_driven_by_config_without_a_second_field_table():
    config = _replace_field(
        analysis_config(), "f_pre_temp", featureRole="PRODUCT_STATE_REFERENCE"
    )

    produced = next(item for item in definitions(config) if item.name == "f_pre_temp")

    assert produced.field_role == "PRODUCT_STATE_REFERENCE"


def test_source_equipment_identifier_column_is_derived_from_config_role():
    config = _replace_field(
        _replace_field(analysis_config(), "furnace_no", featureRole="CONTEXT"),
        "f_jangip_gubun",
        featureRole="EQUIPMENT_IDENTIFIER",
    )

    produced = definitions(config)

    assert {
        item.equipment_id_column
        for item in produced
        if item.equipment_type == "FURNACE"
    } == {"f_jangip_gubun"}


def test_zero_or_multiple_source_equipment_identifiers_fail_closed():
    zero = _replace_field(
        analysis_config(), "furnace_no", featureRole="CONTEXT"
    )
    multiple = _replace_field(
        analysis_config(), "f_jangip_gubun", featureRole="EQUIPMENT_IDENTIFIER"
    )

    with pytest.raises(ValueError, match="exactly one.*FURNACE"):
        definitions(zero)
    with pytest.raises(ValueError, match="exactly one.*FURNACE"):
        definitions(multiple)


def test_context_hierarchy_is_config_owned_and_future_stage_fields_are_removed():
    config = analysis_config()
    changed_hierarchies = dict(config.range_context_hierarchies)
    changed_hierarchies["FURNACE"] = (
        ("furnace_no", "hr_thick_band"),
        ("furnace_no",),
    )
    changed = replace(config, range_context_hierarchies=changed_hierarchies)

    produced = next(item for item in definitions(changed) if item.name == "f_pre_temp")

    assert produced.context_hierarchy == (("furnace_no",), ("furnace_no",))


def test_unknown_context_field_has_no_implicit_stage_default():
    config = analysis_config()
    changed_hierarchies = dict(config.range_context_hierarchies)
    changed_hierarchies["FURNACE"] = (("furnace_no", "invented_band"),)

    with pytest.raises(ValueError, match="context field"):
        definitions(replace(config, range_context_hierarchies=changed_hierarchies))
