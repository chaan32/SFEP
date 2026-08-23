from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


PROCESS_KEYS = ["charge_id", "slab_no"]
COIL_KEY = "hr_coil_id"
CURATION_VERSION = "steel-genealogy-v0.1"
ALLOWED_LABELS = {"양품", "불량"}
REQUIRED_COLUMNS = {
    "SM": {*PROCESS_KEYS},
    "HR": {*PROCESS_KEYS, COIL_KEY, "hr_date"},
    "AP": {COIL_KEY, "judge", "ap_date"},
}
SPLIT_NAMES = ["TRAIN", "VALIDATION", "HOLDOUT", "BOUNDARY_EXCLUDED"]


@dataclass(frozen=True)
class CurationResult:
    curated: pd.DataFrame
    process_audit: pd.DataFrame
    coil_audit: pd.DataFrame
    hr_row_audit: pd.DataFrame


def _validate_required_columns(name: str, frame: pd.DataFrame) -> None:
    missing = sorted(REQUIRED_COLUMNS[name] - set(frame.columns))
    if missing:
        raise ValueError(f"{name} is missing required columns: {', '.join(missing)}")


def _validate_normalized_inputs(sm: pd.DataFrame, hr: pd.DataFrame, ap: pd.DataFrame) -> None:
    for name, frame, columns in (
        ("SM", sm, PROCESS_KEYS),
        ("HR", hr, [*PROCESS_KEYS, COIL_KEY]),
        ("AP", ap, [COIL_KEY]),
    ):
        for column in columns:
            invalid_count = int((frame[column].isna() | frame[column].eq("")).sum())
            if invalid_count:
                raise ValueError(f"{name} {column} contains {invalid_count} blank key values")

    unsupported = sorted(
        {
            "<MISSING>" if pd.isna(value) or str(value).strip() == "" else str(value)
            for value in ap["judge"]
            if pd.isna(value) or str(value).strip() not in ALLOWED_LABELS
        }
    )
    if unsupported:
        raise ValueError(f"AP judge contains unsupported values: {', '.join(unsupported)}")

    for name, frame, column in (("HR", hr, "hr_date"), ("AP", ap, "ap_date")):
        parsed = pd.to_datetime(frame[column], format="%Y-%m-%d", errors="coerce")
        invalid_count = int(parsed.isna().sum())
        if invalid_count:
            raise ValueError(f"{name} {column} contains {invalid_count} invalid dates")


def _normalize_keys(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    normalized = frame.copy()
    for column in columns:
        normalized[column] = normalized[column].astype("string").str.strip()
    return normalized


def _missing_mask(frame: pd.DataFrame) -> pd.DataFrame:
    missing = frame.isna()
    text_columns = frame.select_dtypes(include=["object", "string"]).columns
    for column in text_columns:
        blank = frame[column].astype("string").str.strip().eq("").fillna(False)
        missing[column] = missing[column].astype(bool) | blank.astype(bool)
    return missing


def _numeric_range_flag(
    frame: pd.DataFrame,
    column: str,
    lower: float,
    upper: float,
    *,
    lower_inclusive: bool = True,
) -> pd.Series:
    if column not in frame:
        return pd.Series(False, index=frame.index, dtype=bool)
    values = pd.to_numeric(frame[column], errors="coerce")
    below = values.lt(lower) if lower_inclusive else values.le(lower)
    return (values.isna() | below | values.gt(upper)).astype(bool)


def _add_data_quality_flags(curated: pd.DataFrame) -> pd.DataFrame:
    flagged = curated.copy()
    metadata_columns = {*PROCESS_KEYS, COIL_KEY, "judge", "ap_date"}
    upstream_columns = [column for column in flagged.columns if column not in metadata_columns]
    if upstream_columns:
        flagged["dq_missing_upstream_count"] = (
            _missing_mask(flagged[upstream_columns]).sum(axis=1).astype(int)
        )
    else:
        flagged["dq_missing_upstream_count"] = 0

    flagged["dq_tundish_temp_flag"] = _numeric_range_flag(
        flagged, "tundish_temp", 1400, 1700
    )
    flagged["dq_mlac_ratio_flag"] = _numeric_range_flag(
        flagged, "mlac_ratio", 0, 100, lower_inclusive=False
    )
    flagged["dq_f_sock_temp_flag"] = _numeric_range_flag(
        flagged, "f_sock_temp", 1200, 1300
    )

    gas_ratio_columns = ["f_bfg_per", "f_cog_per", "f_ldg_per"]
    if set(gas_ratio_columns).issubset(flagged.columns):
        gas_ratios = flagged[gas_ratio_columns].apply(pd.to_numeric, errors="coerce")
        flagged["dq_gas_ratio_sum_flag"] = (
            gas_ratios.isna().any(axis=1) | gas_ratios.sum(axis=1).sub(100).abs().gt(0.5)
        )
    else:
        flagged["dq_gas_ratio_sum_flag"] = False

    if "slab_grind" in flagged:
        slab_grind = flagged["slab_grind"].astype("string").str.strip().str.upper()
        flagged["dq_slab_grind_unmapped_flag"] = (
            slab_grind.notna() & ~slab_grind.isin({"HSHS", "MISS"})
        )
    else:
        flagged["dq_slab_grind_unmapped_flag"] = False

    flag_columns = [
        "dq_tundish_temp_flag",
        "dq_mlac_ratio_flag",
        "dq_f_sock_temp_flag",
        "dq_gas_ratio_sum_flag",
        "dq_slab_grind_unmapped_flag",
    ]
    flagged["dq_any_flag"] = (
        flagged["dq_missing_upstream_count"].gt(0) | flagged[flag_columns].any(axis=1)
    )
    return flagged


def _join_type(sm_count: int, hr_count: int) -> str:
    if sm_count == 0:
        return "HR_ONLY"
    if hr_count == 0:
        return "SM_ONLY"
    left = "1" if sm_count == 1 else "N"
    right = "1" if hr_count == 1 else "N"
    return f"{left}:{right}"


def _build_process_audit(sm: pd.DataFrame, hr: pd.DataFrame) -> pd.DataFrame:
    sm_counts = sm.groupby(PROCESS_KEYS, dropna=False).size().rename("sm_count")
    hr_counts = hr.groupby(PROCESS_KEYS, dropna=False).size().rename("hr_count")
    audit = pd.concat([sm_counts, hr_counts], axis=1).fillna(0).astype(int).reset_index()
    audit["join_type"] = [
        _join_type(sm_count, hr_count)
        for sm_count, hr_count in zip(audit["sm_count"], audit["hr_count"], strict=True)
    ]
    audit["generated_rows"] = audit["sm_count"] * audit["hr_count"]
    coil_ids = (
        hr.groupby(PROCESS_KEYS, dropna=False)[COIL_KEY]
        .agg(lambda values: "|".join(sorted(set(values.dropna().astype(str)))))
        .rename("hr_coil_ids")
        .reset_index()
    )
    audit = audit.merge(coil_ids, on=PROCESS_KEYS, how="left")
    audit["hr_coil_ids"] = audit["hr_coil_ids"].fillna("")
    audit["decision"] = audit["join_type"].eq("1:1").map({True: "ELIGIBLE", False: "QUARANTINE"})
    return audit.sort_values(PROCESS_KEYS, kind="stable").reset_index(drop=True)


def _build_hr_row_audit(
    sm: pd.DataFrame,
    hr: pd.DataFrame,
    ap: pd.DataFrame,
) -> pd.DataFrame:
    sm_counts = sm.groupby(PROCESS_KEYS, dropna=False).size().to_dict()
    hr_process_counts = hr.groupby(PROCESS_KEYS, dropna=False).size().to_dict()
    hr_coil_counts = hr.groupby(COIL_KEY, dropna=False).size().to_dict()
    ap_counts = ap.groupby(COIL_KEY, dropna=False).size().to_dict()

    rows: list[dict[str, object]] = []
    for source_offset, (_, hr_row) in enumerate(hr.iterrows(), start=2):
        process_key = tuple(hr_row[PROCESS_KEYS])
        coil_id = hr_row[COIL_KEY]
        sm_count = int(sm_counts.get(process_key, 0))
        hr_process_count = int(hr_process_counts.get(process_key, 0))
        hr_coil_count = int(hr_coil_counts.get(coil_id, 0))
        ap_count = int(ap_counts.get(coil_id, 0))

        if sm_count == 0:
            reason = "SM_PROCESS_MISSING"
        elif sm_count > 1:
            reason = "SM_PROCESS_KEY_DUPLICATE"
        elif hr_process_count > 1:
            reason = "HR_PROCESS_KEY_DUPLICATE"
        elif hr_coil_count > 1:
            reason = "HR_COIL_ID_DUPLICATE"
        elif ap_count == 0:
            reason = "AP_LABEL_MISSING"
        elif ap_count > 1:
            reason = "AP_COIL_ID_DUPLICATE"
        else:
            reason = ""

        rows.append(
            {
                "hr_source_row_number": source_offset,
                "charge_id": hr_row["charge_id"],
                "slab_no": hr_row["slab_no"],
                COIL_KEY: coil_id,
                "sm_count": sm_count,
                "hr_process_count": hr_process_count,
                "hr_coil_count": hr_coil_count,
                "ap_count": ap_count,
                "exclusive_reason": reason,
                "decision": "CURATED" if reason == "" else "QUARANTINE",
            }
        )
    return pd.DataFrame(rows)


def _build_coil_audit(
    sm: pd.DataFrame,
    hr: pd.DataFrame,
    ap: pd.DataFrame,
) -> pd.DataFrame:
    sm_counts = sm.groupby(PROCESS_KEYS, dropna=False).size().to_dict()
    hr_process_counts = hr.groupby(PROCESS_KEYS, dropna=False).size().to_dict()
    hr_groups = {key: group for key, group in hr.groupby(COIL_KEY, dropna=False, sort=False)}
    ap_groups = {key: group for key, group in ap.groupby(COIL_KEY, dropna=False, sort=False)}
    coil_ids = sorted(set(hr_groups) | set(ap_groups), key=str)

    rows: list[dict[str, object]] = []
    for coil_id in coil_ids:
        hr_group = hr_groups.get(coil_id)
        ap_group = ap_groups.get(coil_id)
        hr_count = 0 if hr_group is None else len(hr_group)
        ap_count = 0 if ap_group is None else len(ap_group)
        reasons: list[str] = []

        if hr_count == 0:
            reasons.append("HR_MISSING")
        elif hr_count > 1:
            reasons.append("HR_COIL_ID_DUPLICATE")
        else:
            process_key = tuple(hr_group.iloc[0][PROCESS_KEYS])
            if hr_process_counts.get(process_key, 0) > 1:
                reasons.append("HR_PROCESS_KEY_DUPLICATE")
            sm_count = sm_counts.get(process_key, 0)
            if sm_count == 0:
                reasons.append("SM_PROCESS_MISSING")
            elif sm_count > 1:
                reasons.append("SM_PROCESS_KEY_DUPLICATE")

        if ap_count == 0:
            reasons.append("AP_LABEL_MISSING")
        elif ap_count > 1:
            reasons.append("AP_COIL_ID_DUPLICATE")

        process_keys = ""
        if hr_group is not None:
            process_keys = "|".join(
                sorted(
                    {
                        f"{row.charge_id}/{row.slab_no}"
                        for row in hr_group[PROCESS_KEYS].itertuples(index=False)
                    }
                )
            )
        labels = ""
        if ap_group is not None:
            labels = "|".join(sorted(set(ap_group["judge"].dropna().astype(str))))

        rows.append(
            {
                COIL_KEY: str(coil_id),
                "hr_count": hr_count,
                "ap_count": ap_count,
                "process_keys": process_keys,
                "labels": labels,
                "reason_codes": "|".join(reasons),
                "decision": "CURATED" if not reasons else "QUARANTINE",
            }
        )

    return pd.DataFrame(rows).sort_values(COIL_KEY, kind="stable").reset_index(drop=True)


def curate_frames(sm: pd.DataFrame, hr: pd.DataFrame, ap: pd.DataFrame) -> CurationResult:
    _validate_required_columns("SM", sm)
    _validate_required_columns("HR", hr)
    _validate_required_columns("AP", ap)

    sm_normalized = _normalize_keys(sm, PROCESS_KEYS)
    hr_normalized = _normalize_keys(hr, [*PROCESS_KEYS, COIL_KEY, "hr_date"])
    ap_normalized = _normalize_keys(ap, [COIL_KEY, "judge", "ap_date"])
    _validate_normalized_inputs(sm_normalized, hr_normalized, ap_normalized)

    process_audit = _build_process_audit(sm_normalized, hr_normalized)
    coil_audit = _build_coil_audit(sm_normalized, hr_normalized, ap_normalized)
    hr_row_audit = _build_hr_row_audit(sm_normalized, hr_normalized, ap_normalized)
    eligible_coils = coil_audit.loc[coil_audit["decision"].eq("CURATED"), COIL_KEY]

    hr_eligible = hr_normalized[hr_normalized[COIL_KEY].isin(eligible_coils)]
    eligible_process_keys = hr_eligible[PROCESS_KEYS].drop_duplicates()
    eligible_index = pd.MultiIndex.from_frame(eligible_process_keys)
    sm_index = pd.MultiIndex.from_frame(sm_normalized[PROCESS_KEYS])
    sm_eligible = sm_normalized.loc[sm_index.isin(eligible_index)]
    curated = sm_eligible.merge(hr_eligible, on=PROCESS_KEYS, how="inner", validate="one_to_one")
    label_columns = [COIL_KEY, "judge", "ap_date"]
    curated = curated.merge(
        ap_normalized[label_columns],
        on=COIL_KEY,
        how="inner",
        validate="one_to_one",
    )
    curated = _add_data_quality_flags(curated)
    curated = curated.sort_values(["hr_date", COIL_KEY], kind="stable").reset_index(drop=True)
    if curated[COIL_KEY].duplicated().any():
        raise ValueError("curated dataset must contain exactly one row per hr_coil_id")
    if int(hr_row_audit["decision"].eq("CURATED").sum()) != len(curated):
        raise ValueError("HR row audit and curated dataset row counts do not reconcile")

    return CurationResult(
        curated=curated,
        process_audit=process_audit,
        coil_audit=coil_audit,
        hr_row_audit=hr_row_audit,
    )


def assign_group_time_splits(
    curated: pd.DataFrame,
    validation_start: str = "2025-08-01",
    holdout_start: str = "2025-09-01",
) -> pd.DataFrame:
    required = {"charge_id", COIL_KEY, "hr_date"}
    missing = sorted(required - set(curated.columns))
    if missing:
        raise ValueError(f"curated frame is missing split columns: {', '.join(missing)}")

    validation_cutoff = pd.Timestamp(validation_start)
    holdout_cutoff = pd.Timestamp(holdout_start)
    if validation_cutoff >= holdout_cutoff:
        raise ValueError("validation_start must be earlier than holdout_start")

    split = curated.copy()
    split_dates = pd.to_datetime(split["hr_date"], format="%Y-%m-%d", errors="coerce")
    if split_dates.isna().any():
        raise ValueError(f"hr_date contains {int(split_dates.isna().sum())} invalid dates")
    bounds = (
        pd.DataFrame({"charge_id": split["charge_id"], "hr_date": split_dates})
        .groupby("charge_id", sort=False)["hr_date"]
        .agg(["min", "max"])
    )

    def classify(row: pd.Series) -> str:
        crosses_validation = row["min"] < validation_cutoff <= row["max"]
        crosses_holdout = row["min"] < holdout_cutoff <= row["max"]
        if crosses_validation or crosses_holdout:
            return "BOUNDARY_EXCLUDED"
        if row["max"] < validation_cutoff:
            return "TRAIN"
        if row["min"] >= validation_cutoff and row["max"] < holdout_cutoff:
            return "VALIDATION"
        if row["min"] >= holdout_cutoff:
            return "HOLDOUT"
        return "BOUNDARY_EXCLUDED"

    charge_splits = bounds.apply(classify, axis=1)
    split["dataset_split"] = split["charge_id"].map(charge_splits)
    if split["dataset_split"].isna().any():
        raise ValueError("every curated row must receive a dataset_split")
    return split


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _label_counts(frame: pd.DataFrame) -> dict[str, int]:
    counts = frame["judge"].value_counts()
    return {label: int(counts.get(label, 0)) for label in sorted(ALLOWED_LABELS)}


def _split_counts(frame: pd.DataFrame) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for split_name in SPLIT_NAMES:
        group = frame.loc[frame["dataset_split"].eq(split_name)]
        dates = pd.to_datetime(group["hr_date"], format="%Y-%m-%d", errors="coerce")
        result[split_name] = {
            "rows": int(len(group)),
            "labels": _label_counts(group),
            "charges": int(group["charge_id"].nunique()),
            "date_min": None if group.empty else dates.min().strftime("%Y-%m-%d"),
            "date_max": None if group.empty else dates.max().strftime("%Y-%m-%d"),
        }
    return result


def _reason_counts(quarantine: pd.DataFrame) -> dict[str, int]:
    if quarantine.empty:
        return {}
    reasons = quarantine["reason_codes"].str.split("|").explode()
    reasons = reasons[reasons.ne("")]
    return {str(reason): int(count) for reason, count in reasons.value_counts().sort_index().items()}


def _split_charge_overlap(frame: pd.DataFrame) -> int:
    charge_sets = {
        name: set(frame.loc[frame["dataset_split"].eq(name), "charge_id"])
        for name in ("TRAIN", "VALIDATION", "HOLDOUT")
    }
    overlap = (
        (charge_sets["TRAIN"] & charge_sets["VALIDATION"])
        | (charge_sets["TRAIN"] & charge_sets["HOLDOUT"])
        | (charge_sets["VALIDATION"] & charge_sets["HOLDOUT"])
    )
    return len(overlap)


def run_curation(
    sm_csv: str | Path,
    hr_csv: str | Path,
    ap_csv: str | Path,
    output_dir: str | Path,
    *,
    input_encoding: str = "cp949",
    validation_start: str = "2025-08-01",
    holdout_start: str = "2025-09-01",
) -> dict[str, object]:
    paths = {"sm": Path(sm_csv), "hr": Path(hr_csv), "ap": Path(ap_csv)}
    frames = {
        "sm": pd.read_csv(
            paths["sm"], encoding=input_encoding, dtype={"charge_id": "string", "slab_no": "string"}
        ),
        "hr": pd.read_csv(
            paths["hr"],
            encoding=input_encoding,
            dtype={"charge_id": "string", "slab_no": "string", COIL_KEY: "string"},
        ),
        "ap": pd.read_csv(
            paths["ap"], encoding=input_encoding, dtype={COIL_KEY: "string", "judge": "string"}
        ),
    }
    result = curate_frames(frames["sm"], frames["hr"], frames["ap"])
    curated = assign_group_time_splits(
        result.curated,
        validation_start=validation_start,
        holdout_start=holdout_start,
    )
    modeling = curated.loc[curated["dataset_split"].ne("BOUNDARY_EXCLUDED")].copy()
    quarantine = result.coil_audit.loc[result.coil_audit["decision"].eq("QUARANTINE")].copy()
    hr_matching_quarantine = result.hr_row_audit.loc[
        result.hr_row_audit["decision"].eq("QUARANTINE")
    ].copy()
    split_manifest_columns = [*PROCESS_KEYS, COIL_KEY, "hr_date", "ap_date", "judge", "dataset_split"]
    split_manifest = curated[split_manifest_columns].copy()

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    output_frames = {
        "curated_coil_dataset.csv": curated,
        "modeling_cohort.csv": modeling,
        "process_key_audit.csv": result.process_audit,
        "coil_audit.csv": result.coil_audit,
        "quarantine_coils.csv": quarantine,
        "hr_row_audit.csv": result.hr_row_audit,
        "hr_matching_quarantine.csv": hr_matching_quarantine,
        "split_manifest.csv": split_manifest,
    }
    for name, frame in output_frames.items():
        frame.to_csv(output_path / name, index=False, encoding="utf-8-sig")

    dq_flag_columns = [column for column in curated if column.startswith("dq_") and column.endswith("_flag")]
    source_files = {
        name: {
            "file_name": path.name,
            "sha256": _sha256(path),
            "rows": int(len(frames[name])),
            "columns": int(len(frames[name].columns)),
        }
        for name, path in paths.items()
    }
    summary: dict[str, object] = {
        "curation_version": CURATION_VERSION,
        "source_files": source_files,
        "policy": {
            "grain": "one row per unique hr_coil_id",
            "process_join": "SM and HR process keys must both be unique",
            "coil_join": "HR and AP hr_coil_id must both be unique and present",
            "ap_inputs": ["judge", "ap_date"],
            "validation_start": validation_start,
            "holdout_start": holdout_start,
            "boundary_charge_policy": "exclude an entire charge_id if its hr_date range crosses a split boundary",
        },
        "counts": {
            "curated_rows_before_boundary": int(len(curated)),
            "curated_unique_coils": int(curated[COIL_KEY].nunique()),
            "boundary_excluded_rows": int(curated["dataset_split"].eq("BOUNDARY_EXCLUDED").sum()),
            "modeling_rows": int(len(modeling)),
            "modeling_unique_coils": int(modeling[COIL_KEY].nunique()),
            "quarantine_unique_coils": int(len(quarantine)),
            "hr_anchor_quarantine_rows": int(len(hr_matching_quarantine)),
            "ap_only_coils": int(quarantine["reason_codes"].str.contains("HR_MISSING", regex=False).sum()),
        },
        "label_counts": {
            "curated_before_boundary": _label_counts(curated),
            "modeling": _label_counts(modeling),
        },
        "split_counts": _split_counts(curated),
        "quarantine_reason_counts": _reason_counts(quarantine),
        "hr_anchor_exclusive_reason_counts": {
            str(reason): int(count)
            for reason, count in hr_matching_quarantine["exclusive_reason"]
            .value_counts()
            .sort_index()
            .items()
        },
        "data_quality": {
            "rows_with_any_flag": int(curated["dq_any_flag"].sum()),
            "missing_upstream_rows": int(curated["dq_missing_upstream_count"].gt(0).sum()),
            "flag_counts": {column: int(curated[column].sum()) for column in dq_flag_columns},
        },
        "integrity": {
            "curated_one_row_per_coil": bool(not curated[COIL_KEY].duplicated().any()),
            "modeling_one_row_per_coil": bool(not modeling[COIL_KEY].duplicated().any()),
            "split_charge_overlap": int(_split_charge_overlap(curated)),
        },
    }
    (output_path / "curation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a leakage-safe, one-coil-per-row SFEP-Steel genealogy dataset."
    )
    parser.add_argument("--sm-csv", required=True, type=Path)
    parser.add_argument("--hr-csv", required=True, type=Path)
    parser.add_argument("--ap-csv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--input-encoding", default="cp949")
    parser.add_argument("--validation-start", default="2025-08-01")
    parser.add_argument("--holdout-start", default="2025-09-01")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = run_curation(
        args.sm_csv,
        args.hr_csv,
        args.ap_csv,
        args.output_dir,
        input_encoding=args.input_encoding,
        validation_start=args.validation_start,
        holdout_start=args.holdout_start,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
