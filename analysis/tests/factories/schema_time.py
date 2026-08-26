"""Literal fixtures for schema, genealogy, and time-split tests.

These factories intentionally do not call producer builders to calculate any
expected value.  They construct only literal DataFrames and frozen model
instances.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping

import pandas as pd

from equipment_quality.models import (
    AnalysisConfig,
    GenealogyResult,
    InputTables,
    SourceFile,
)


_SOURCE_FILES = (
    SourceFile(
        role="sm_cc",
        name="sts_1sm_cc_1.csv",
        size_bytes=101,
        sha256="sha256:" + "1" * 64,
    ),
    SourceFile(
        role="fur_hr",
        name="sts_2fur_hr_2.csv",
        size_bytes=202,
        sha256="sha256:" + "2" * 64,
    ),
    SourceFile(
        role="ap",
        name="sts_3ap_3.csv",
        size_bytes=303,
        sha256="sha256:" + "3" * 64,
    ),
)

_SM_DEFAULT = {
    "sm_plant": "1공장",
    "charge_id": "C1",
    "steel_grade": "S1",
    "steel_usage": "U1",
    "delta_ferrite": 50.0,
    "ingre_cr": 18.0,
    "ingre_ni": 8.0,
    "ingre_s": 0.01,
    "cast_date": date(2024, 11, 1),
    "cc_gubun": "1연주",
    "tundish_temp": 1500.0,
    "mlac_ratio": 95.0,
    "slab_no": "1",
    "slab_gubun": "C",
    "slab_grind": "MISS",
}

_FUR_DEFAULT = {
    "charge_id": "C1",
    "slab_no": "1",
    "furnace_no": "1호기",
    "f_jangip_gubun": "CCR",
    "f_jangip_temp": 30.0,
    "f_bfg": 25.0,
    "f_cog": 25.0,
    "f_ldg": 50.0,
    "f_bfg_per": 99.0,
    "f_cog_per": 0.5,
    "f_ldg_per": 0.5,
    "f_pre_temp": 1100.0,
    "f_heat_temp": 1200.0,
    "f_sock_temp": 1250.0,
    "f_pre_interval": 90.0,
    "f_heat_interval": 45.0,
    "f_sock_interval": 40.0,
    "f_ext_date": date(2024, 11, 2),
    "f_ext_time": 10,
    "hr_coil_id": "H1",
    "hr_date": date(2024, 11, 2),
    "hr_thick": 3.0,
    "hr_width": 1000.0,
    "rm4_temp": 1100.0,
    "rm_pitch": 90.0,
    "slab_width": 1000.0,
}

_AP_DEFAULT = {
    "judge": "양품",
    "hr_coil_id": "H1",
    "ap_plant": "1공장",
    "ap_prod_id": "A1",
    "ap_date": date(2024, 11, 3),
    "ap_shift": "A",
    "ap_thick": 3.0,
    "ap_width": 1000.0,
    "ap_line_speed": 30.0,
}


def _literal_frame(
    rows: Iterable[Mapping[str, object]],
    defaults: Mapping[str, object],
) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for ordinal, row in enumerate(rows, start=2):
        record = dict(defaults)
        record.update(row)
        for column in ("cast_date", "f_ext_date", "hr_date", "ap_date"):
            value = record.get(column)
            if isinstance(value, str):
                record[column] = date.fromisoformat(value)
        record.setdefault("_source_record_number", ordinal)
        records.append(record)
    return pd.DataFrame(records)


def tables(
    *,
    sm: Iterable[Mapping[str, object]] | None = None,
    fur: Iterable[Mapping[str, object]] | None = None,
    ap: Iterable[Mapping[str, object]] | None = None,
) -> InputTables:
    """Construct minimal validated-shaped input tables from literal rows."""
    sm_rows = [_SM_DEFAULT] if sm is None else list(sm)
    fur_rows = [_FUR_DEFAULT] if fur is None else list(fur)
    ap_rows = [_AP_DEFAULT] if ap is None else list(ap)
    return InputTables(
        sm_cc=_literal_frame(sm_rows, _SM_DEFAULT),
        fur_hr=_literal_frame(fur_rows, _FUR_DEFAULT),
        ap=_literal_frame(ap_rows, _AP_DEFAULT),
        sources=_SOURCE_FILES,
    )


def tables_with_duplicate_ap(hr_coil_id: str) -> InputTables:
    return tables(
        fur=[{"hr_coil_id": hr_coil_id}],
        ap=[
            {"hr_coil_id": hr_coil_id, "ap_prod_id": "A1"},
            {"hr_coil_id": hr_coil_id, "ap_prod_id": "A2"},
        ],
    )


def tables_with_unlinked_sm_and_ap() -> InputTables:
    return tables(
        sm=[{"charge_id": "UNLINKED_SM", "slab_no": "9"}],
        fur=[{"charge_id": "C1", "slab_no": "1", "hr_coil_id": "H1"}],
        ap=[{"hr_coil_id": "UNLINKED_AP", "ap_prod_id": "A9"}],
    )


def tables_with_different_sm_ap_linkage_same_fur() -> InputTables:
    return tables(
        sm=[{"charge_id": "C1", "slab_no": "1"}],
        fur=[{"charge_id": "C1", "slab_no": "1", "hr_coil_id": "H1"}],
        ap=[{"hr_coil_id": "H1", "ap_prod_id": "A1"}],
    )


def tables_with_ap_before_hr() -> InputTables:
    return tables(
        sm=[{"cast_date": "2025-01-01"}],
        fur=[{"f_ext_date": "2025-01-02", "hr_date": "2025-01-03"}],
        ap=[{"ap_date": "2025-01-02"}],
    )


def tables_with_known_record_numbers() -> InputTables:
    return tables(
        sm=[{"_source_record_number": 2}],
        fur=[{"_source_record_number": 4}],
        ap=[{"_source_record_number": 3}],
    )


def analysis_config(
    *,
    reference_fraction: float = 0.70,
    maturity_days: int = 38,
) -> AnalysisConfig:
    """Return every Task 2 policy surface as literal constructor values."""
    return AnalysisConfig(
        schema_version="sfep-analysis-config/v1",
        analysis_config_version="quality-analysis-v1",
        timezone="Asia/Seoul",
        label_maturity_days=maturity_days,
        reference_fraction=reference_fraction,
        discovery_fraction=0.70,
        operating_ranges={
            "minimumSupport": 400,
            "extremeTailMinimumSupport": 2000,
            "quantileMethod": "INVERTED_CDF_TYPE_1",
            "typicalLowerQuantile": 0.05,
            "typicalUpperQuantile": 0.95,
            "extremeLowerQuantile": 0.01,
            "extremeUpperQuantile": 0.99,
        },
        quality_risk={
            "minimumDiscoverySupport": 200,
            "minimumCautionDefects": 5,
            "minimumDangerDefects": 10,
            "minimumConfirmationSupport": 100,
            "minimumConfirmationDefects": 5,
            "minimumInformativeStrata": 2,
            "numericBins": 10,
            "interactionBins": 3,
            "zeroCellCorrection": 0.5,
            "bhQ": {"caution": 0.10, "danger": 0.05},
            "relativeRisk": {"caution": 1.5, "danger": 2.0},
            "riskDifference": {"caution": 0.005, "danger": 0.01},
            "confirmationRelativeRisk": {"cautionExclusive": 1.0, "danger": 1.5},
        },
        bootstrap={"replicates": 2000, "minimumValidReplicates": 1900},
        wilson_z=1.959963984540054,
        fields=(),
        range_context_hierarchies={
            "SM_CC": (("sm_plant", "steel_grade", "steel_usage"),),
            "FURNACE": (("furnace_no",),),
            "RM4": ((),),
            "AP": (("ap_plant",),),
        },
        risk_adjustment_hierarchies={
            "SM_CC": ((),),
            "FURNACE": ((),),
            "RM4": ((),),
            "AP": ((),),
        },
        fixed_interactions=(
            ("tundish_temp", "mlac_ratio"),
            ("f_pre_interval", "f_pre_temp"),
            ("f_heat_interval", "f_heat_temp"),
            ("f_sock_interval", "f_sock_temp"),
            ("rm_pitch", "rm4_temp"),
            ("ap_line_speed", "ap_thick"),
        ),
        fdr_families=("NUMERIC", "CATEGORICAL", "INTERACTION"),
        evidence_families=(
            "STEEL_CHEMISTRY",
            "CASTING_STABILITY",
            "CHARGE",
            "FUEL_PROFILE",
            "PREHEAT",
            "HEATING",
            "SOAKING",
            "RM4",
            "DIMENSIONS",
            "AP",
        ),
    )


def dated_rows(rows: Iterable[tuple[str, str]]) -> GenealogyResult:
    records: list[dict[str, object]] = []
    for index, (charge_id, day) in enumerate(rows, start=1):
        hr_date = date.fromisoformat(day)
        records.append(
            {
                "charge_id": charge_id,
                "slab_no": str(index),
                "hr_coil_id": f"H{index}",
                "hr_date": hr_date,
                "ap_date": hr_date,
                "judge": "양품",
                "sm_cc_record_number": index + 1,
                "fur_hr_record_number": index + 1,
                "ap_record_number": index + 1,
            }
        )
    frame = pd.DataFrame(records)
    empty = pd.DataFrame()
    return GenealogyResult(
        boundary_rows=frame[["charge_id", "slab_no", "hr_coil_id", "hr_date"]].copy(),
        replay_rows=frame.copy(),
        quality_rows=frame.copy(),
        quarantine_rows=empty.copy(),
        lineage_rows=empty.copy(),
        audit={},
    )


def maturity_fixture() -> GenealogyResult:
    rows = dated_rows(
        [
            ("C1", "2025-01-01"),
            ("C2", "2025-01-02"),
            ("C2", "2025-01-03"),
            ("C2", "2025-01-04"),
            ("C2", "2025-01-05"),
        ]
    )
    rows.replay_rows.loc[rows.replay_rows["charge_id"] == "C1", "ap_date"] = date(
        2025, 1, 4
    )
    rows.quality_rows.loc[rows.quality_rows["charge_id"] == "C1", "ap_date"] = date(
        2025, 1, 4
    )
    return rows


def maturity_boundary_fixture() -> GenealogyResult:
    """Return a literal snapshot whose outer cutoff is 2025-02-08."""
    result = dated_rows(
        [
            ("MISSING", "2024-12-29"),
            ("UNLINKED", "2024-12-30"),
            ("AP_AFTER", "2024-12-31"),
            ("M38", "2025-01-01"),
            ("M37", "2025-01-02"),
            ("RECENT1", "2025-02-07"),
            ("RECENT2", "2025-02-08"),
            ("HOLD1", "2025-02-09"),
            ("HOLD2", "2025-02-10"),
            ("HOLD3", "2025-02-11"),
        ]
    )
    replay = result.replay_rows.copy()
    replay.loc[replay["charge_id"] == "MISSING", "judge"] = None
    replay.loc[replay["charge_id"] == "UNLINKED", ["ap_date", "judge", "ap_record_number"]] = None
    replay.loc[replay["charge_id"] == "AP_AFTER", "ap_date"] = date(2025, 2, 9)
    replay.loc[replay["charge_id"] == "M38", "ap_date"] = date(2025, 2, 8)
    replay.loc[replay["charge_id"] == "M37", "ap_date"] = date(2025, 2, 8)
    quality = replay[replay["charge_id"] != "UNLINKED"].copy()
    return GenealogyResult(
        boundary_rows=result.boundary_rows,
        replay_rows=replay,
        quality_rows=quality,
        quarantine_rows=result.quarantine_rows,
        lineage_rows=result.lineage_rows,
        audit={},
    )
