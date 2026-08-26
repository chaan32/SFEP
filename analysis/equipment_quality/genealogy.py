"""Strict composite-key steel genealogy with deterministic quarantine."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Iterable

import pandas as pd

from equipment_quality.deterministic import digest_json_id
from equipment_quality.models import (
    GenealogyResult,
    InputTables,
    MaterialLineage,
    SourceFile,
    SourceRecordRef,
)


_ROLE_RANK = {"sm_cc": 0, "fur_hr": 1, "ap": 2}
_QUARANTINE_COLUMNS = [
    "source_role",
    "source_name",
    "source_record_number",
    "reason",
    "charge_id",
    "slab_no",
    "hr_coil_id",
    "ap_prod_id",
]


def _missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return bool(pd.isna(value))


def _ensure_columns(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    result = frame.copy(deep=True)
    for column in columns:
        if column not in result.columns:
            result[column] = pd.Series([None] * len(result), dtype=object)
    return result


def _duplicate_indexes(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    eligible: pd.Series,
) -> set[int]:
    keys = [
        tuple(frame.at[index, column] for column in columns)
        for index in frame.index[eligible]
    ]
    counts = Counter(keys)
    duplicates = {key for key, count in counts.items() if count > 1}
    return {
        int(index)
        for index in frame.index[eligible]
        if tuple(frame.at[index, column] for column in columns) in duplicates
    }


def _record_number(row: pd.Series, role: str) -> int:
    value = row.get("_source_record_number")
    if _missing(value):
        value = row.get(f"{role}_record_number")
    if _missing(value):
        raise ValueError("validated source rows must carry _source_record_number")
    return int(value)


def _source_map(sources: tuple[SourceFile, ...]) -> dict[str, SourceFile]:
    result = {source.role: source for source in sources}
    if set(result) != {"sm_cc", "fur_hr", "ap"}:
        raise ValueError("input sources must contain exactly sm_cc, fur_hr, and ap")
    return result


def _monotonic_stage_dates(row: pd.Series) -> bool:
    values: list[date] = []
    for column in ("cast_date", "f_ext_date", "hr_date", "ap_date"):
        value = row.get(column)
        if _missing(value):
            continue
        if isinstance(value, pd.Timestamp):
            value = value.date()
        if not isinstance(value, date):
            return False
        values.append(value)
    return all(left <= right for left, right in zip(values, values[1:]))


def _derived_gas_ratios(row: pd.Series) -> tuple[float | None, float | None, float | None]:
    values = (row.get("f_bfg"), row.get("f_cog"), row.get("f_ldg"))
    if any(_missing(value) for value in values):
        return None, None, None
    numbers = tuple(float(value) for value in values)
    total = sum(numbers)
    if total == 0.0:
        return None, None, None
    return tuple(100.0 * value / total for value in numbers)  # type: ignore[return-value]


def build_genealogy(inputs: InputTables) -> GenealogyResult:
    """Build only unambiguous joins; quarantine every rejected source record."""
    sources = _source_map(inputs.sources)
    sm = _ensure_columns(
        inputs.sm_cc, ("charge_id", "slab_no", "cast_date", "_source_record_number")
    )
    fur = _ensure_columns(
        inputs.fur_hr,
        (
            "charge_id", "slab_no", "hr_coil_id", "f_ext_date", "hr_date",
            "_source_record_number",
        ),
    )
    ap = _ensure_columns(
        inputs.ap,
        ("hr_coil_id", "ap_prod_id", "ap_date", "judge", "_source_record_number"),
    )
    quarantine: list[dict[str, object]] = []

    def add_quarantine(role: str, row: pd.Series, reason: str) -> None:
        quarantine.append(
            {
                "source_role": role,
                "source_name": sources[role].name,
                "source_record_number": _record_number(row, role),
                "reason": reason,
                "charge_id": row.get("charge_id"),
                "slab_no": row.get("slab_no"),
                "hr_coil_id": row.get("hr_coil_id"),
                "ap_prod_id": row.get("ap_prod_id"),
            }
        )

    sm_key_valid = pd.Series(
        [not _missing(row["charge_id"]) and not _missing(row["slab_no"]) for _, row in sm.iterrows()],
        index=sm.index,
        dtype=bool,
    )
    fur_key_valid = pd.Series(
        [not _missing(row["charge_id"]) and not _missing(row["slab_no"]) for _, row in fur.iterrows()],
        index=fur.index,
        dtype=bool,
    )
    fur_coil_valid = pd.Series(
        [not _missing(value) for value in fur["hr_coil_id"]], index=fur.index, dtype=bool
    )
    ap_coil_valid = pd.Series(
        [not _missing(value) for value in ap["hr_coil_id"]], index=ap.index, dtype=bool
    )
    ap_prod_valid = pd.Series(
        [not _missing(value) for value in ap["ap_prod_id"]], index=ap.index, dtype=bool
    )

    for index in sm.index[~sm_key_valid]:
        add_quarantine("sm_cc", sm.loc[index], "MISSING_SM_CC_KEY")
    for index in fur.index[~fur_key_valid]:
        add_quarantine("fur_hr", fur.loc[index], "MISSING_FUR_HR_KEY")
    for index in fur.index[~fur_coil_valid]:
        add_quarantine("fur_hr", fur.loc[index], "MISSING_FUR_HR_COIL_ID")
    for index in ap.index[~ap_coil_valid]:
        add_quarantine("ap", ap.loc[index], "MISSING_AP_HR_COIL_ID")
    for index in ap.index[~ap_prod_valid]:
        add_quarantine("ap", ap.loc[index], "MISSING_AP_PROD_ID")

    sm_duplicate = _duplicate_indexes(sm, ("charge_id", "slab_no"), sm_key_valid)
    fur_duplicate = _duplicate_indexes(fur, ("charge_id", "slab_no"), fur_key_valid)
    fur_coil_duplicate = _duplicate_indexes(fur, ("hr_coil_id",), fur_coil_valid)
    ap_duplicate = _duplicate_indexes(ap, ("hr_coil_id",), ap_coil_valid)
    ap_prod_duplicate = _duplicate_indexes(ap, ("ap_prod_id",), ap_prod_valid)

    for index in sorted(sm_duplicate):
        add_quarantine("sm_cc", sm.loc[index], "DUPLICATE_SM_CC_KEY")
    for index in sorted(fur_duplicate):
        add_quarantine("fur_hr", fur.loc[index], "DUPLICATE_FUR_HR_KEY")
    for index in sorted(fur_coil_duplicate):
        add_quarantine("fur_hr", fur.loc[index], "DUPLICATE_FUR_HR_COIL_KEY")
    for index in sorted(ap_duplicate):
        add_quarantine("ap", ap.loc[index], "DUPLICATE_AP_KEY")
    for index in sorted(ap_prod_duplicate):
        add_quarantine("ap", ap.loc[index], "DUPLICATE_AP_PROD_ID")

    boundary_mask = fur_key_valid.copy()
    for index in fur_duplicate:
        boundary_mask.at[index] = False
    for index in fur.index[boundary_mask]:
        value = fur.at[index, "hr_date"]
        if isinstance(value, pd.Timestamp):
            value = value.date()
        if not isinstance(value, date):
            boundary_mask.at[index] = False
            add_quarantine("fur_hr", fur.loc[index], "INVALID_FUR_HR_DATE")
    boundary_rows = fur.loc[boundary_mask].copy()
    boundary_rows = boundary_rows.rename(
        columns={"_source_record_number": "fur_hr_record_number"}
    ).sort_values("fur_hr_record_number", kind="stable").reset_index(drop=True)

    sm_eligible = sm_key_valid.copy()
    fur_eligible = fur_key_valid & fur_coil_valid
    ap_eligible = ap_coil_valid & ap_prod_valid
    for index in sm_duplicate:
        sm_eligible.at[index] = False
    for index in fur_duplicate | fur_coil_duplicate:
        fur_eligible.at[index] = False
    for index in ap_duplicate | ap_prod_duplicate:
        ap_eligible.at[index] = False

    sm_keys = {
        (sm.at[index, "charge_id"], sm.at[index, "slab_no"])
        for index in sm.index[sm_eligible]
    }
    fur_keys = {
        (fur.at[index, "charge_id"], fur.at[index, "slab_no"])
        for index in fur.index[fur_eligible]
    }
    fur_coils = {fur.at[index, "hr_coil_id"] for index in fur.index[fur_eligible]}
    for index in sm.index[sm_eligible]:
        key = (sm.at[index, "charge_id"], sm.at[index, "slab_no"])
        if key not in fur_keys:
            add_quarantine("sm_cc", sm.loc[index], "UNLINKED_SM_CC")
    for index in fur.index[fur_eligible]:
        key = (fur.at[index, "charge_id"], fur.at[index, "slab_no"])
        if key not in sm_keys:
            add_quarantine("fur_hr", fur.loc[index], "UNLINKED_FUR_HR")
    for index in ap.index[ap_eligible]:
        if ap.at[index, "hr_coil_id"] not in fur_coils:
            add_quarantine("ap", ap.loc[index], "UNLINKED_AP")

    valid_sm = sm.loc[sm_eligible].rename(
        columns={"_source_record_number": "sm_cc_record_number"}
    )
    valid_fur = fur.loc[fur_eligible].rename(
        columns={"_source_record_number": "fur_hr_record_number"}
    )
    valid_ap = ap.loc[ap_eligible].rename(
        columns={"_source_record_number": "ap_record_number"}
    )
    chain = valid_fur.merge(
        valid_sm,
        on=["charge_id", "slab_no"],
        how="inner",
        validate="one_to_one",
        suffixes=("", "_sm"),
        sort=False,
    )
    chain = chain.merge(
        valid_ap,
        on="hr_coil_id",
        how="left",
        validate="one_to_one",
        suffixes=("", "_ap"),
        sort=False,
    )

    accepted_records: list[dict[str, object]] = []
    for _, joined in chain.iterrows():
        if not _monotonic_stage_dates(joined):
            add_quarantine("fur_hr", joined, "IMPOSSIBLE_STAGE_DATE_ORDER")
            continue
        record = joined.to_dict()
        bfg_ratio, cog_ratio, ldg_ratio = _derived_gas_ratios(joined)
        record["f_bfg_ratio"] = bfg_ratio
        record["f_cog_ratio"] = cog_ratio
        record["f_ldg_ratio"] = ldg_ratio
        if _missing(record.get("ap_record_number")):
            record["label_status"] = "AP_UNLINKED"
        elif _missing(record.get("judge")):
            record["label_status"] = "LABEL_MISSING"
        else:
            record["label_status"] = "AVAILABLE"
        accepted_records.append(record)

    replay_rows = pd.DataFrame(accepted_records)
    if not replay_rows.empty:
        replay_rows = replay_rows.sort_values(
            ["fur_hr_record_number", "charge_id", "slab_no"], kind="stable"
        ).reset_index(drop=True)
    quality_rows = (
        replay_rows.loc[replay_rows["ap_record_number"].notna()].copy()
        if not replay_rows.empty
        else replay_rows.copy()
    )

    lineage_records: list[dict[str, object]] = []
    for _, row in replay_rows.iterrows():
        refs = [
            SourceRecordRef(
                role="sm_cc",
                name=sources["sm_cc"].name,
                record_number=int(row["sm_cc_record_number"]),
            ),
            SourceRecordRef(
                role="fur_hr",
                name=sources["fur_hr"].name,
                record_number=int(row["fur_hr_record_number"]),
            ),
        ]
        ap_record_number: int | None = None
        if not _missing(row.get("ap_record_number")):
            ap_record_number = int(row["ap_record_number"])
            refs.append(
                SourceRecordRef(
                    role="ap",
                    name=sources["ap"].name,
                    record_number=ap_record_number,
                )
            )
        material_key = digest_json_id(
            "sfep-material-key/v1",
            {"chargeId": row["charge_id"], "slabNo": row["slab_no"]},
        )
        material = MaterialLineage(
            material_key=material_key,
            charge_id=row["charge_id"],
            slab_no=row["slab_no"],
            hr_coil_id=row.get("hr_coil_id"),
            source_records=tuple(refs),
        )
        lineage_records.append(
            {
                "material_key": material_key,
                "charge_id": row["charge_id"],
                "slab_no": row["slab_no"],
                "hr_coil_id": row.get("hr_coil_id"),
                "sm_cc_record_number": int(row["sm_cc_record_number"]),
                "fur_hr_record_number": int(row["fur_hr_record_number"]),
                "ap_record_number": ap_record_number,
                "source_records": tuple(refs),
                "material_lineage": material,
            }
        )
    lineage_rows = pd.DataFrame(lineage_records)

    quarantine.sort(
        key=lambda item: (
            _ROLE_RANK[str(item["source_role"])],
            int(item["source_record_number"]),
            str(item["reason"]),
        )
    )
    quarantine_rows = pd.DataFrame(quarantine, columns=_QUARANTINE_COLUMNS)
    reason_counts = dict(sorted(Counter(row["reason"] for row in quarantine).items()))
    label_counts = dict(
        sorted(
            Counter(
                row["label_status"]
                for row in accepted_records
                if row["label_status"] != "AVAILABLE"
            ).items()
        )
    )
    audit = {
        "source_rows": {"sm_cc": len(sm), "fur_hr": len(fur), "ap": len(ap)},
        "boundary_rows": len(boundary_rows),
        "replay_rows": len(replay_rows),
        "quality_rows": len(quality_rows),
        "quarantine": reason_counts,
        "label_censoring": label_counts,
        "derived_features": {
            "f_bfg_ratio": ("fur_hr.f_bfg", "fur_hr.f_cog", "fur_hr.f_ldg"),
            "f_cog_ratio": ("fur_hr.f_bfg", "fur_hr.f_cog", "fur_hr.f_ldg"),
            "f_ldg_ratio": ("fur_hr.f_bfg", "fur_hr.f_cog", "fur_hr.f_ldg"),
        },
    }
    return GenealogyResult(
        boundary_rows=boundary_rows,
        replay_rows=replay_rows,
        quality_rows=quality_rows,
        quarantine_rows=quarantine_rows,
        lineage_rows=lineage_rows,
        audit=audit,
    )
