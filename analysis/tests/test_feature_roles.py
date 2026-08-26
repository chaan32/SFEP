"""Config-driven feature role and stage contracts for Task 4."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from equipment_quality.feature_roles import definitions
from equipment_quality.schema import load_analysis_config


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# Hand-authored from the approved three source headers plus the three approved
# derived gas fractions.  Identifier, time, result, and ignored source-percent
# columns are deliberately absent; no production helper derives this oracle.
EXPECTED_MONITORED_AND_CONTEXT_COLUMNS = frozenset(
    {
        "sm_plant",
        "steel_grade",
        "steel_usage",
        "delta_ferrite",
        "ingre_cr",
        "ingre_ni",
        "ingre_s",
        "cc_gubun",
        "tundish_temp",
        "mlac_ratio",
        "slab_gubun",
        "slab_grind",
        "furnace_no",
        "f_jangip_gubun",
        "f_jangip_temp",
        "f_bfg",
        "f_cog",
        "f_ldg",
        "f_bfg_ratio",
        "f_cog_ratio",
        "f_ldg_ratio",
        "f_pre_temp",
        "f_heat_temp",
        "f_sock_temp",
        "f_pre_interval",
        "f_heat_interval",
        "f_sock_interval",
        "hr_thick",
        "hr_width",
        "rm4_temp",
        "rm_pitch",
        "slab_width",
        "ap_plant",
        "ap_shift",
        "ap_thick",
        "ap_width",
        "ap_line_speed",
    }
)

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


def test_every_non_identity_source_or_derived_column_has_one_role_and_stage():
    produced = definitions(analysis_config())
    names = [definition.name for definition in produced]

    assert frozenset(names) == EXPECTED_MONITORED_AND_CONTEXT_COLUMNS
    assert len(names) == len(set(names)) == 37
    assert EXCLUDED_IDENTITY_TIME_RESULT_COLUMNS.isdisjoint(names)
    assert all(definition.field_role for definition in produced)
    assert all(definition.first_stage for definition in produced)


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
