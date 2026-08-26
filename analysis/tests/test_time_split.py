from __future__ import annotations

from datetime import date

from equipment_quality.genealogy import build_genealogy
from equipment_quality.time_split import build_time_split
from factories.schema_time import (
    analysis_config,
    dated_rows,
    maturity_boundary_fixture,
    maturity_fixture,
    tables,
    tables_with_different_sm_ap_linkage_same_fur,
    tables_with_unlinked_sm_and_ap,
)


def test_reference_cutoff_keeps_date_whole_and_does_not_recalculate_after_charge_purge():
    rows = dated_rows(
        [
            ("C1", "2025-01-01"),
            ("C2", "2025-01-02"),
            ("C3", "2025-01-03"),
            ("C4", "2025-01-04"),
            ("C3", "2025-01-05"),
        ]
    )

    split = build_time_split(
        rows, analysis_config(reference_fraction=0.70, maturity_days=38)
    )

    assert split.as_of.isoformat() == "2025-01-04"
    assert "C3" not in set(split.reference_rows.charge_id)
    assert "C3" not in set(split.holdout_rows.charge_id)
    assert split.counts["chargePurges"]["outerChargeIds"] == ("C3",)


def test_reference_cutoff_includes_every_record_on_threshold_date():
    rows = dated_rows(
        [
            ("C1", "2025-01-01"),
            ("C2", "2025-01-02"),
            ("C3", "2025-01-02"),
            ("C4", "2025-01-03"),
        ]
    )

    split = build_time_split(rows, analysis_config())

    assert split.as_of == date(2025, 1, 2)
    assert set(split.reference_rows.charge_id) == {"C1", "C2", "C3"}
    assert set(split.holdout_rows.charge_id) == {"C4"}


def test_boundary_linkage_changes_do_not_change_fur_only_cutoff():
    baseline = build_genealogy(tables_with_unlinked_sm_and_ap())
    mutated = build_genealogy(tables_with_different_sm_ap_linkage_same_fur())

    baseline_split = build_time_split(baseline, analysis_config())
    mutated_split = build_time_split(mutated, analysis_config())

    assert baseline_split.as_of == date(2024, 11, 2)
    assert baseline_split.as_of == mutated_split.as_of


def test_unmatured_label_is_censored_not_good():
    split = build_time_split(
        maturity_fixture(),
        analysis_config(reference_fraction=0.70, maturity_days=38),
    )

    assert split.counts["reference"]["unknownOrCensored"] == 1
    assert split.counts["reference"]["nonDefects"] == 0
    assert split.reference_rows.iloc[0]["label_status"] == "LABEL_NOT_YET_AVAILABLE"


def test_maturity_is_inclusive_at_exact_38_days_and_ap_on_as_of():
    split = build_time_split(maturity_boundary_fixture(), analysis_config())
    reference = split.reference_rows.set_index("charge_id")

    assert split.as_of == date(2025, 2, 8)
    assert reference.loc["M38", "label_status"] == "AVAILABLE"
    assert reference.loc["M37", "label_status"] == "LABEL_NOT_YET_AVAILABLE"
    assert reference.loc["AP_AFTER", "label_status"] == "LABEL_NOT_YET_AVAILABLE"
    assert reference.loc["UNLINKED", "label_status"] == "AP_UNLINKED"
    assert reference.loc["MISSING", "label_status"] == "LABEL_MISSING"
    assert split.counts["reference"]["nonDefects"] == 1
    assert split.counts["reference"]["unknownOrCensored"] == 6


def test_ap_unlinked_row_remains_replayable_but_censored():
    genealogy = build_genealogy(tables(ap=[]))

    split = build_time_split(genealogy, analysis_config())

    assert len(split.reference_rows) == 1
    assert split.reference_rows.iloc[0]["label_status"] == "AP_UNLINKED"
    assert split.counts["reference"]["unknownOrCensored"] == 1
    assert split.counts["reference"]["nonDefects"] == 0


def test_inner_cutoff_is_not_recomputed_after_second_charge_purge():
    rows = dated_rows(
        [
            ("C1", "2025-01-01"),
            ("C2", "2025-01-02"),
            ("C3", "2025-01-03"),
            ("C4", "2025-01-04"),
            ("C3", "2025-01-05"),
            ("C6", "2025-02-20"),
            ("C7", "2025-02-21"),
            ("H1", "2025-02-22"),
            ("H2", "2025-02-23"),
            ("H3", "2025-02-24"),
        ]
    )

    split = build_time_split(rows, analysis_config())

    assert split.as_of == date(2025, 2, 21)
    assert split.discovery_cutoff == date(2025, 1, 4)
    assert "C3" not in set(split.discovery_rows.charge_id)
    assert "C3" not in set(split.confirmation_rows.charge_id)
    assert set(split.discovery_rows.charge_id) == {"C1", "C2", "C4"}
    assert split.counts["chargePurges"]["innerChargeIds"] == ("C3",)


def test_discovery_cutoff_keeps_all_mature_rows_on_boundary_date():
    rows = dated_rows(
        [
            ("C1", "2025-01-01"),
            ("C2", "2025-01-02"),
            ("C3", "2025-01-02"),
            ("C4", "2025-01-03"),
            ("C5", "2025-01-04"),
            ("C6", "2025-02-20"),
            ("C7", "2025-02-21"),
            ("H1", "2025-02-22"),
            ("H2", "2025-02-23"),
            ("H3", "2025-02-24"),
        ]
    )

    split = build_time_split(rows, analysis_config())

    assert split.discovery_cutoff == date(2025, 1, 3)
    assert set(split.discovery_rows.charge_id) == {"C1", "C2", "C3", "C4"}
    assert set(split.confirmation_rows.charge_id) == {"C5"}


def test_no_mature_quality_rows_yields_no_inner_cutoff_or_false_goods():
    genealogy = build_genealogy(tables(ap=[]))

    split = build_time_split(genealogy, analysis_config())

    assert split.discovery_cutoff is None
    assert split.discovery_rows.empty
    assert split.confirmation_rows.empty
    assert split.counts["discovery"]["total"] == 0
    assert split.counts["confirmation"]["total"] == 0


def test_holdout_counts_only_observed_final_labels_as_good_or_defect():
    rows = dated_rows(
        [
            ("C1", "2025-01-01"),
            ("C2", "2025-01-02"),
            ("C3", "2025-01-03"),
            ("C4", "2025-01-04"),
            ("C5", "2025-01-05"),
        ]
    )
    rows.replay_rows.loc[rows.replay_rows["charge_id"] == "C5", "judge"] = "불량"
    rows.quality_rows.loc[rows.quality_rows["charge_id"] == "C5", "judge"] = "불량"

    split = build_time_split(rows, analysis_config())

    assert split.counts["holdout"]["total"] == 1
    assert split.counts["holdout"]["defects"] == 1
    assert split.counts["holdout"]["nonDefects"] == 0
    assert split.counts["holdout"]["unknownOrCensored"] == 0


def test_every_split_count_balances_and_uses_hr_date_range():
    split = build_time_split(maturity_boundary_fixture(), analysis_config())

    for name in ("reference", "discovery", "confirmation", "holdout"):
        count = split.counts[name]
        assert count["total"] == (
            count["defects"] + count["nonDefects"] + count["unknownOrCensored"]
        )
    assert split.counts["reference"]["dateFrom"] == "2024-12-29"
    assert split.counts["reference"]["dateTo"] == "2025-02-08"
    assert split.counts["holdout"]["dateFrom"] == "2025-02-09"
    assert split.counts["holdout"]["dateTo"] == "2025-02-11"


def test_label_censoring_reasons_are_counted_separately():
    split = build_time_split(maturity_boundary_fixture(), analysis_config())

    assert split.counts["labelCensoring"] == {
        "AP_UNLINKED": 1,
        "LABEL_MISSING": 1,
        "LABEL_NOT_YET_AVAILABLE": 4,
    }
