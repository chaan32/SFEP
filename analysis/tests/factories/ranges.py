"""Literal Task 4 range fixtures with no production calculation helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
import hashlib

import pandas as pd

from equipment_quality.models import MaterialLineage, SourceRecordRef, TimeSplitResult


def _literal_date(value: date | str) -> date:
    return date.fromisoformat(value) if isinstance(value, str) else value


def _value_at(value: object, index: int, size: int) -> object:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) != size:
            raise ValueError("literal column sequence must match values")
        return value[index]
    return value


def range_rows(
    *,
    values: Iterable[object],
    field: str = "f_pre_temp",
    furnace_no: object = "1호기",
    steel_grade: object = "S1",
    steel_usage: object = "U1",
    f_jangip_gubun: object = "CCR",
    slab_width: object = None,
    f_ext_date: object = date(2025, 1, 2),
    cast_date: object = date(2025, 1, 1),
    hr_date: object = date(2025, 1, 2),
    ap_date: object = date(2025, 1, 3),
) -> pd.DataFrame:
    """Build validated-shaped rows while leaving unrelated features absent."""
    literal_values = list(values)
    size = len(literal_values)
    records: list[dict[str, object]] = []
    for index, value in enumerate(literal_values):
        record = {
            "charge_id": f"C{index // 2:05d}",
            "slab_no": str(index % 2 + 1),
            "hr_coil_id": f"H{index:06d}",
            "sm_plant": "SM1",
            "steel_grade": _value_at(steel_grade, index, size),
            "steel_usage": _value_at(steel_usage, index, size),
            "furnace_no": _value_at(furnace_no, index, size),
            "f_jangip_gubun": _value_at(f_jangip_gubun, index, size),
            "slab_width": _value_at(slab_width, index, size),
            "cast_date": _literal_date(_value_at(cast_date, index, size)),
            "f_ext_date": _literal_date(_value_at(f_ext_date, index, size)),
            "hr_date": _literal_date(_value_at(hr_date, index, size)),
            "ap_date": _literal_date(_value_at(ap_date, index, size)),
            "ap_plant": "AP1",
            "ap_shift": "A",
            field: value,
        }
        records.append(record)
    return pd.DataFrame(records)


def split_with_reference(
    rows: pd.DataFrame,
    *,
    as_of: date | str = date(2025, 1, 4),
    holdout_rows: pd.DataFrame | None = None,
) -> TimeSplitResult:
    cutoff = _literal_date(as_of)
    empty = rows.iloc[0:0].copy()
    holdout = empty if holdout_rows is None else holdout_rows.copy(deep=True)
    return TimeSplitResult(
        as_of=cutoff,
        discovery_cutoff=cutoff,
        reference_rows=rows.copy(deep=True),
        discovery_rows=empty.copy(deep=True),
        confirmation_rows=empty.copy(deep=True),
        holdout_rows=holdout,
        counts={},
    )


def sparse_context_fixture() -> TimeSplitResult:
    rows = range_rows(
        values=range(1, 401),
        steel_grade=["S1"] * 200 + ["S2"] * 200,
        steel_usage=["U1"] * 200 + ["U2"] * 200,
    )
    return split_with_reference(rows)


def stage_date_fixture(
    *, value_dates: Iterable[date | str], as_of: date | str
) -> TimeSplitResult:
    rows = []
    next_value = 1
    for day in value_dates:
        block = range_rows(
            values=range(next_value, next_value + 400),
            f_ext_date=_literal_date(day),
        )
        rows.append(block)
        next_value += 400
    return split_with_reference(pd.concat(rows, ignore_index=True), as_of=as_of)


def future_context_fixture() -> TimeSplitResult:
    rows = range_rows(values=range(1, 401))
    rows["hr_thick"] = [float(index) for index in range(1, 401)]
    return split_with_reference(rows)


def collapsed_lower_tail_fixture() -> TimeSplitResult:
    values = [0.0] * 100 + [float(value) for value in range(1, 1901)]
    return split_with_reference(range_rows(values=values))


def only(items: Sequence[object]):
    assert len(items) == 1
    return items[0]


def material_catalog_for_rows(rows: pd.DataFrame) -> tuple[MaterialLineage, ...]:
    """Build a deterministic test-only catalog without production ID helpers."""
    catalog: list[MaterialLineage] = []
    seen: set[tuple[str, str]] = set()
    for position, row in enumerate(rows.itertuples(index=False), start=2):
        charge_id = str(row.charge_id)
        slab_no = str(row.slab_no)
        pair = (charge_id, slab_no)
        if pair in seen:
            continue
        seen.add(pair)
        preimage = f"range-fixture-v1\0{charge_id}\0{slab_no}".encode("utf-8")
        catalog.append(
            MaterialLineage(
                material_key="sha256:" + hashlib.sha256(preimage).hexdigest(),
                charge_id=charge_id,
                slab_no=slab_no,
                hr_coil_id=str(row.hr_coil_id),
                source_records=(
                    SourceRecordRef("fur_hr", "range-fixture.csv", position),
                ),
            )
        )
    return tuple(catalog)
