"""Literal replay-event fixtures with no production-derived expectations."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Mapping

import pandas as pd

from equipment_quality.models import GenealogyResult
from equipment_quality.schema import load_analysis_config


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def analysis_config():
    return load_analysis_config(REPOSITORY_ROOT / "analysis/analysis_config.json")


def _one_row() -> dict[str, object]:
    return {
        "sm_plant": "SM1",
        "charge_id": "CH1",
        "steel_grade": "STS304",
        "steel_usage": "A",
        "delta_ferrite": 7.1,
        "ingre_cr": 18.2,
        "ingre_ni": 8.1,
        "ingre_s": 0.005,
        "cast_date": date(2025, 1, 1),
        "cc_gubun": "CC1",
        "tundish_temp": 1540.0,
        "mlac_ratio": 0.92,
        "slab_no": "1",
        "slab_gubun": "NORMAL",
        "slab_grind": "HSHS",
        "furnace_no": "1",
        "f_jangip_gubun": "COLD",
        "f_jangip_temp": 620.0,
        "f_bfg": 100.0,
        "f_cog": 30.0,
        "f_ldg": 20.0,
        "f_bfg_ratio": 2.0 / 3.0,
        "f_cog_ratio": 0.2,
        "f_ldg_ratio": 2.0 / 15.0,
        "f_pre_temp": 1080.0,
        "f_heat_temp": 1220.0,
        "f_sock_temp": 1240.0,
        "f_pre_interval": 40.0,
        "f_heat_interval": 55.0,
        "f_sock_interval": 30.0,
        "f_ext_date": date(2025, 1, 1),
        "f_ext_time": 7,
        "hr_coil_id": "H001",
        "hr_date": date(2025, 1, 1),
        "hr_thick": 3.0,
        "hr_width": 1210.0,
        "rm4_temp": 1040.0,
        "rm_pitch": 2.1,
        "slab_width": 1200.0,
        "judge": "양품",
        "ap_plant": "AP1",
        "ap_prod_id": "A001",
        "ap_date": date(2025, 2, 1),
        "ap_shift": "A",
        "ap_thick": 2.8,
        "ap_width": 1200.0,
        "ap_line_speed": 85.0,
        "ap_record_number": 2,
    }


def _genealogy(rows: list[dict[str, object]]) -> GenealogyResult:
    replay_rows = pd.DataFrame(rows)
    empty = pd.DataFrame()
    quality_rows = replay_rows.loc[replay_rows["ap_record_number"].notna()].copy()
    return GenealogyResult(
        boundary_rows=empty.copy(),
        replay_rows=replay_rows,
        quality_rows=quality_rows,
        quarantine_rows=empty.copy(),
        lineage_rows=empty.copy(),
        audit={},
    )


def one_material_chain(
    *,
    missing: str | None = None,
    overrides: Mapping[str, object] | None = None,
) -> GenealogyResult:
    row = _one_row()
    if overrides is not None:
        row.update(overrides)
    if missing is not None:
        if missing not in row:
            raise ValueError(f"unknown literal field: {missing}")
        row[missing] = None
    return _genealogy([row])


def two_furnaces_same_hour() -> GenealogyResult:
    first = _one_row()
    second = dict(first)
    second.update(
        {
            "sm_plant": "SM2",
            "charge_id": "CH2",
            "slab_no": "2",
            "furnace_no": "2호기",
            "hr_coil_id": "H002",
            "ap_prod_id": None,
            "ap_date": None,
            "ap_record_number": None,
            "judge": None,
        }
    )
    for row in (first, second):
        row["ap_prod_id"] = None
        row["ap_date"] = None
        row["ap_record_number"] = None
        row["judge"] = None
    return _genealogy([second, first])


def two_furnaces_same_hour_in_forward_input_order() -> GenealogyResult:
    result = two_furnaces_same_hour()
    rows = list(reversed(result.replay_rows.to_dict(orient="records")))
    return _genealogy(rows)


def duplicated_material_chain() -> GenealogyResult:
    row = _one_row()
    result = _genealogy([dict(row), dict(row)])
    return GenealogyResult(
        boundary_rows=result.boundary_rows,
        replay_rows=result.replay_rows,
        quality_rows=result.quality_rows.iloc[:1].copy(),
        quarantine_rows=result.quarantine_rows,
        lineage_rows=result.lineage_rows,
        audit={},
    )


def replay_row_with_ap_columns_but_no_quality_row() -> GenealogyResult:
    result = one_material_chain()
    empty_quality = result.replay_rows.iloc[0:0].copy()
    return GenealogyResult(
        boundary_rows=result.boundary_rows,
        replay_rows=result.replay_rows,
        quality_rows=empty_quality,
        quarantine_rows=result.quarantine_rows,
        lineage_rows=result.lineage_rows,
        audit={},
    )


def two_distinct_materials() -> GenealogyResult:
    first = _one_row()
    second = dict(first)
    second.update(
        {
            "sm_plant": "SM2",
            "charge_id": "CH2",
            "slab_no": "2",
            "furnace_no": "2",
            "hr_coil_id": "H002",
            "ap_prod_id": "A002",
        }
    )
    return _genealogy([first, second])
