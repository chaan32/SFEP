"""Literal Task 6 quality-risk fixtures.

Factories in this module construct inputs only.  They deliberately do not call
quality candidate, statistics, grading, or identifier production helpers.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from equipment_quality.models import TimeSplitResult
from equipment_quality.schema import load_analysis_config


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def analysis_config():
    return load_analysis_config(REPOSITORY_ROOT / "analysis/analysis_config.json")


def _split(discovery: pd.DataFrame, confirmation: pd.DataFrame | None = None) -> TimeSplitResult:
    confirm = discovery.copy(deep=True) if confirmation is None else confirmation.copy(deep=True)
    empty = discovery.iloc[0:0].copy(deep=True)
    counts = {
        "reference": {},
        "discovery": {},
        "confirmation": {},
        "holdout": {},
    }
    return TimeSplitResult(
        as_of=date(2025, 2, 20),
        discovery_cutoff=date(2025, 1, 31),
        reference_rows=pd.concat([discovery, confirm], ignore_index=True),
        discovery_rows=discovery.copy(deep=True),
        confirmation_rows=confirm,
        holdout_rows=empty,
        counts=counts,
    )


def rows_with_labels(labels: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "charge_id": [f"C{index:03d}" for index in range(len(labels))],
            "f_pre_temp": [1000.0, 1010.0, 1020.0, 1030.0][: len(labels)],
            "judge": ["불량" if value else "양품" for value in labels],
        }
    )


def tied_numeric_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "charge_id": [f"T{index:03d}" for index in range(30)],
            "f_pre_temp": [1000.0] * 10 + [1100.0] * 10 + [1200.0] * 10,
            "judge": ["양품"] * 30,
        }
    )


def equipment_category_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "charge_id": ["E1", "E2", "E3"],
            "furnace_no": ["2호기", "1호기", "2호기"],
            "judge": ["양품", "불량", "양품"],
        }
    )


def ap_candidate_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "charge_id": ["A1", "A2", "A3"],
            "ap_line_speed": [80.0, 90.0, 100.0],
            "judge": ["양품", "불량", "양품"],
        }
    )


def six_interaction_cells_fixture() -> pd.DataFrame:
    pairs = (
        ("tundish_temp", "mlac_ratio"),
        ("f_pre_interval", "f_pre_temp"),
        ("f_heat_interval", "f_heat_temp"),
        ("f_sock_interval", "f_sock_temp"),
        ("rm_pitch", "rm4_temp"),
        ("ap_line_speed", "ap_thick"),
    )
    rows: list[dict[str, object]] = []
    for left in (0.0, 1.0, 2.0):
        for right in (0.0, 1.0, 2.0):
            row: dict[str, object] = {
                "charge_id": f"I{len(rows):02d}",
                "judge": "양품",
            }
            for first, second in pairs:
                row[first] = left
                row[second] = right
            rows.append(row)
    return pd.DataFrame(rows)


def global_family_fixture() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for level, support, defects in (
        ("A", 200, 10),
        ("B", 200, 5),
        ("C", 200, 0),
        ("한글", 1, 1),
    ):
        for index in range(support):
            rows.append(
                {
                    "charge_id": f"{level}-{index:04d}",
                    "slab_grind": level,
                    "judge": "불량" if index < defects else "양품",
                }
            )
    return pd.DataFrame(rows)


def global_family_split(_candidates=None) -> TimeSplitResult:
    return _split(global_family_fixture())


def q_values_by_rule_id(rules: list[dict[str, object]]) -> dict[str, float]:
    return {
        str(rule["ruleId"]): float(rule["discovery"]["qValue"])
        for rule in rules
        if rule["fieldNames"] == ["slab_grind"]
    }


def split_from_rows(discovery: pd.DataFrame, confirmation: pd.DataFrame | None = None) -> TimeSplitResult:
    return _split(discovery, confirmation)


def only(values):
    snapshot = list(values)
    assert len(snapshot) == 1
    return snapshot[0]


def _group_rows(
    *,
    per_cell: int,
    risk_defects: int,
    comparator_defects: int,
    prefix: str,
    second_candidate: bool = False,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for stratum in range(2):
        for level, defects in (("RISK", risk_defects), ("BASE", comparator_defects)):
            for index in range(per_cell):
                row = {
                    "charge_id": f"{prefix}-{stratum}-{level}-{index:05d}",
                    "sm_plant": f"P{stratum + 1}",
                    "steel_grade": f"G{stratum + 1}",
                    "steel_usage": "U",
                    "slab_grind": level,
                    "judge": "불량" if index < defects else "양품",
                }
                if second_candidate:
                    row["cc_gubun"] = level
                rows.append(row)
    return pd.DataFrame(rows)


def strong_repeated_fixture() -> TimeSplitResult:
    discovery = _group_rows(
        per_cell=100, risk_defects=20, comparator_defects=5, prefix="D"
    )
    confirmation = _group_rows(
        per_cell=50, risk_defects=10, comparator_defects=5, prefix="C"
    )
    return _split(discovery, confirmation)


def caution_only_fixture() -> TimeSplitResult:
    discovery = _group_rows(
        per_cell=500,
        risk_defects=50,
        comparator_defects=31,
        prefix="CD",
        second_candidate=True,
    )
    confirmation = _group_rows(
        per_cell=250,
        risk_defects=25,
        comparator_defects=16,
        prefix="CC",
        second_candidate=True,
    )
    return _split(discovery, confirmation)


def caution_unconfirmed_fixture() -> TimeSplitResult:
    discovery = _group_rows(
        per_cell=500, risk_defects=50, comparator_defects=31, prefix="CUD"
    )
    confirmation = _group_rows(
        per_cell=250, risk_defects=16, comparator_defects=25, prefix="CUC"
    )
    return _split(discovery, confirmation)


def null_fixture() -> TimeSplitResult:
    discovery = _group_rows(
        per_cell=100, risk_defects=5, comparator_defects=5, prefix="ND"
    )
    confirmation = _group_rows(
        per_cell=50, risk_defects=5, comparator_defects=5, prefix="NC"
    )
    return _split(discovery, confirmation)


def zero_variance_fixture() -> TimeSplitResult:
    discovery = _group_rows(
        per_cell=100, risk_defects=100, comparator_defects=100, prefix="ZVD"
    )
    confirmation = _group_rows(
        per_cell=50, risk_defects=50, comparator_defects=50, prefix="ZVC"
    )
    return _split(discovery, confirmation)


def grade_boundary_fixture(fixture_name: str) -> TimeSplitResult:
    if fixture_name == "below_support":
        discovery = _group_rows(
            per_cell=100, risk_defects=20, comparator_defects=5, prefix="BSD"
        )
        first_risk_index = discovery.index[discovery["slab_grind"] == "RISK"][0]
        discovery = discovery.drop(first_risk_index).reset_index(drop=True)
        confirmation = _group_rows(
            per_cell=50, risk_defects=10, comparator_defects=5, prefix="BSC"
        )
        return _split(discovery, confirmation)
    if fixture_name == "discovery_only":
        discovery = _group_rows(
            per_cell=100, risk_defects=20, comparator_defects=5, prefix="DOD"
        )
        confirmation = _group_rows(
            per_cell=50, risk_defects=5, comparator_defects=10, prefix="DOC"
        )
        return _split(discovery, confirmation)
    if fixture_name == "confirmed_caution":
        return caution_only_fixture()
    if fixture_name == "confirmed_stratified_danger":
        return strong_repeated_fixture()
    raise ValueError(f"unknown grade fixture: {fixture_name}")


def future_context_quality_fixture() -> TimeSplitResult:
    discovery = _group_rows(
        per_cell=100, risk_defects=20, comparator_defects=5, prefix="FD"
    )
    confirmation = _group_rows(
        per_cell=50, risk_defects=10, comparator_defects=5, prefix="FC"
    )
    discovery["f_pre_temp"] = [1000.0 if value == "RISK" else 1200.0 for value in discovery["slab_grind"]]
    discovery["f_pre_interval"] = list(reversed(range(len(discovery))))
    discovery["hr_thick"] = [float(index % 4) for index in range(len(discovery))]
    confirmation["f_pre_temp"] = [1000.0 if value == "RISK" else 1200.0 for value in confirmation["slab_grind"]]
    confirmation["f_pre_interval"] = list(reversed(range(len(confirmation))))
    confirmation["hr_thick"] = [float(index % 4) for index in range(len(confirmation))]
    return _split(discovery, confirmation)


def too_few_bootstraps_fixture() -> TimeSplitResult:
    return strong_repeated_fixture()


def adjacent_numeric_fixture() -> TimeSplitResult:
    discovery: list[dict[str, object]] = []
    confirmation: list[dict[str, object]] = []
    for value, defects in ((0.0, 20), (1.0, 20), (2.0, 0)):
        for index in range(200):
            discovery.append(
                {
                    "charge_id": f"MD-{int(value)}-{index:04d}",
                    "f_pre_temp": value,
                    "judge": "불량" if index < defects else "양품",
                }
            )
        for index in range(100):
            confirmation.append(
                {
                    "charge_id": f"MC-{int(value)}-{index:04d}",
                    "f_pre_temp": value,
                    "judge": "불량" if index < defects // 2 else "양품",
                }
            )
    return _split(pd.DataFrame(discovery), pd.DataFrame(confirmation))


def performance_fixture(row_count: int = 4000) -> pd.DataFrame:
    """A deterministic full candidate surface with actual-scale row count."""
    config = analysis_config()
    columns: dict[str, list[object]] = {
        "charge_id": [f"P{index:05d}" for index in range(row_count)],
        "judge": ["불량" if index % 97 == 0 else "양품" for index in range(row_count)],
    }
    numeric_rank = 0
    category_rank = 0
    for field in config.fields:
        name = str(field["field"])
        if field["featureRole"] not in {
            "DIRECT_OPERATION", "PRODUCT_STATE_REFERENCE", "CONTEXT", "EQUIPMENT_IDENTIFIER"
        }:
            continue
        if field["dataType"] == "NUMBER":
            numeric_rank += 1
            columns[name] = [
                float((index * (numeric_rank * 2 + 1)) % 101)
                for index in range(row_count)
            ]
        elif field["dataType"] == "STRING":
            category_rank += 1
            columns[name] = [
                f"L{(index + category_rank) % 4}" for index in range(row_count)
            ]
    return pd.DataFrame(columns)
