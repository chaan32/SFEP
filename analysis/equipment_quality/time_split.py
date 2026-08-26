"""Whole-date retrospective splits with Charge purging and label maturity."""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, timedelta

import pandas as pd

from equipment_quality.models import AnalysisConfig, GenealogyResult, TimeSplitResult


def _missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return bool(pd.isna(value))


def _as_date(value: object, label: str) -> date:
    if isinstance(value, pd.Timestamp):
        value = value.date()
    if not isinstance(value, date):
        raise ValueError(f"{label} must be a validated date")
    return value


def _whole_date_cutoff(rows: pd.DataFrame, fraction: float, label: str) -> date:
    if rows.empty:
        raise ValueError(f"cannot calculate {label} from an empty population")
    dates = sorted(_as_date(value, f"{label} hr_date") for value in rows["hr_date"])
    required = math.ceil(fraction * len(dates))
    if required < 1 or required > len(dates):
        raise ValueError(f"{label} fraction must satisfy 0 < fraction <= 1")
    return dates[required - 1]


def _charge_ids_on_both_sides(
    rows: pd.DataFrame, cutoff: date
) -> tuple[str, ...]:
    left = {
        str(row["charge_id"])
        for _, row in rows.iterrows()
        if _as_date(row["hr_date"], "split hr_date") <= cutoff
    }
    right = {
        str(row["charge_id"])
        for _, row in rows.iterrows()
        if _as_date(row["hr_date"], "split hr_date") > cutoff
    }
    return tuple(sorted(left & right, key=lambda value: value.encode("utf-8")))


def _ordered(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows.reset_index(drop=True)
    columns = [
        column
        for column in (
            "hr_date",
            "charge_id",
            "slab_no",
            "hr_coil_id",
            "fur_hr_record_number",
        )
        if column in rows.columns
    ]
    return rows.sort_values(columns, kind="stable").reset_index(drop=True)


def _classify_reference_label(
    row: pd.Series,
    as_of: date,
    maturity_days: int,
) -> str:
    if row.get("label_status") == "AP_UNLINKED" or _missing(
        row.get("ap_record_number")
    ):
        return "AP_UNLINKED"
    hr_date = _as_date(row.get("hr_date"), "reference hr_date")
    ap_date = row.get("ap_date")
    if _missing(ap_date):
        return "LABEL_NOT_YET_AVAILABLE"
    ap_day = _as_date(ap_date, "reference ap_date")
    if hr_date > as_of - timedelta(days=maturity_days) or ap_day > as_of:
        return "LABEL_NOT_YET_AVAILABLE"
    if _missing(row.get("judge")):
        return "LABEL_MISSING"
    return "AVAILABLE"


def _classify_holdout_label(row: pd.Series) -> str:
    if row.get("label_status") == "AP_UNLINKED" or _missing(
        row.get("ap_record_number")
    ):
        return "AP_UNLINKED"
    if _missing(row.get("judge")):
        return "LABEL_MISSING"
    return "AVAILABLE"


def _with_label_status(
    rows: pd.DataFrame,
    *,
    as_of: date,
    maturity_days: int,
    reference: bool,
) -> pd.DataFrame:
    result = rows.copy()
    if result.empty:
        if "label_status" not in result.columns:
            result["label_status"] = pd.Series(dtype=object)
        return result
    if reference:
        result["label_status"] = [
            _classify_reference_label(row, as_of, maturity_days)
            for _, row in result.iterrows()
        ]
    else:
        result["label_status"] = [
            _classify_holdout_label(row) for _, row in result.iterrows()
        ]
    return result


def _date_string(value: object) -> str:
    return _as_date(value, "count hr_date").isoformat()


def _count(rows: pd.DataFrame) -> dict[str, object]:
    if rows.empty:
        return {
            "total": 0,
            "defects": 0,
            "nonDefects": 0,
            "unknownOrCensored": 0,
            "dateFrom": None,
            "dateTo": None,
        }
    statuses = (
        rows["label_status"]
        if "label_status" in rows.columns
        else pd.Series(["AVAILABLE"] * len(rows), index=rows.index)
    )
    available = statuses == "AVAILABLE"
    defects = int((available & (rows["judge"] == "불량")).sum())
    non_defects = int((available & (rows["judge"] == "양품")).sum())
    unknown = len(rows) - defects - non_defects
    dates = sorted(_date_string(value) for value in rows["hr_date"])
    return {
        "total": len(rows),
        "defects": defects,
        "nonDefects": non_defects,
        "unknownOrCensored": unknown,
        "dateFrom": dates[0],
        "dateTo": dates[-1],
    }


def build_time_split(
    result: GenealogyResult,
    config: AnalysisConfig,
) -> TimeSplitResult:
    """Build T/D splits exactly once, purging cross-boundary Charges afterward."""
    if not 0 < config.reference_fraction <= 1:
        raise ValueError("reference fraction must satisfy 0 < fraction <= 1")
    if not 0 < config.discovery_fraction <= 1:
        raise ValueError("discovery fraction must satisfy 0 < fraction <= 1")
    if config.label_maturity_days < 0:
        raise ValueError("label maturity days must be non-negative")

    boundary = result.boundary_rows.copy()
    required_boundary_columns = {"charge_id", "hr_date"}
    if not required_boundary_columns.issubset(boundary.columns):
        raise ValueError("boundary population must contain charge_id and hr_date")
    if boundary.empty:
        raise ValueError("FUR-only boundary population is empty")
    if any(_missing(value) for value in boundary["charge_id"]):
        raise ValueError("boundary population charge_id must be non-empty")
    for value in boundary["hr_date"]:
        _as_date(value, "boundary hr_date")

    as_of = _whole_date_cutoff(boundary, config.reference_fraction, "reference cutoff")
    outer_charge_ids = _charge_ids_on_both_sides(boundary, as_of)
    outer_charge_set = set(outer_charge_ids)

    replay = result.replay_rows.copy()
    if replay.empty or "hr_date" not in replay.columns:
        reference_rows = replay.copy()
        holdout_rows = replay.copy()
    else:
        for value in replay["hr_date"]:
            _as_date(value, "replay hr_date")
        not_purged = ~replay["charge_id"].astype(str).isin(outer_charge_set)
        reference_rows = replay.loc[
            not_purged
            & replay["hr_date"].map(lambda value: _as_date(value, "replay hr_date") <= as_of)
        ].copy()
        holdout_rows = replay.loc[
            not_purged
            & replay["hr_date"].map(lambda value: _as_date(value, "replay hr_date") > as_of)
        ].copy()

    reference_rows = _with_label_status(
        reference_rows,
        as_of=as_of,
        maturity_days=config.label_maturity_days,
        reference=True,
    )
    holdout_rows = _with_label_status(
        holdout_rows,
        as_of=as_of,
        maturity_days=config.label_maturity_days,
        reference=False,
    )
    reference_rows = _ordered(reference_rows)
    holdout_rows = _ordered(holdout_rows)

    mature_rows = reference_rows.loc[
        reference_rows["label_status"] == "AVAILABLE"
    ].copy()
    discovery_cutoff: date | None = None
    inner_charge_ids: tuple[str, ...] = ()
    if mature_rows.empty:
        discovery_rows = mature_rows.copy()
        confirmation_rows = mature_rows.copy()
    else:
        discovery_cutoff = _whole_date_cutoff(
            mature_rows, config.discovery_fraction, "discovery cutoff"
        )
        inner_charge_ids = _charge_ids_on_both_sides(mature_rows, discovery_cutoff)
        inner_charge_set = set(inner_charge_ids)
        not_inner_purged = ~mature_rows["charge_id"].astype(str).isin(inner_charge_set)
        discovery_rows = mature_rows.loc[
            not_inner_purged
            & mature_rows["hr_date"].map(
                lambda value: _as_date(value, "mature hr_date") <= discovery_cutoff
            )
        ].copy()
        confirmation_rows = mature_rows.loc[
            not_inner_purged
            & mature_rows["hr_date"].map(
                lambda value: _as_date(value, "mature hr_date") > discovery_cutoff
            )
        ].copy()
    discovery_rows = _ordered(discovery_rows)
    confirmation_rows = _ordered(confirmation_rows)

    censoring = Counter(
        status
        for rows in (reference_rows, holdout_rows)
        for status in rows.get("label_status", pd.Series(dtype=object)).tolist()
        if status != "AVAILABLE"
    )
    counts = {
        "reference": _count(reference_rows),
        "discovery": _count(discovery_rows),
        "confirmation": _count(confirmation_rows),
        "holdout": _count(holdout_rows),
        "labelCensoring": dict(sorted(censoring.items())),
        "chargePurges": {
            "outerChargeIds": outer_charge_ids,
            "outerBoundaryRows": int(
                boundary["charge_id"].astype(str).isin(outer_charge_set).sum()
            ),
            "innerChargeIds": inner_charge_ids,
            "innerMatureRows": int(
                mature_rows["charge_id"].astype(str).isin(set(inner_charge_ids)).sum()
            )
            if not mature_rows.empty
            else 0,
        },
    }
    return TimeSplitResult(
        as_of=as_of,
        discovery_cutoff=discovery_cutoff,
        reference_rows=reference_rows,
        discovery_rows=discovery_rows,
        confirmation_rows=confirmation_rows,
        holdout_rows=holdout_rows,
        counts=counts,
    )
