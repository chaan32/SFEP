from datetime import date, timedelta
from hashlib import sha256

import pandas as pd
import pytest

from equipment_quality.model_features import _FEATURE_TYPES
from equipment_quality.model_training import build_forward_folds, evaluate_candidates


def rows():
    records = []
    for day in range(16):
        for item in range(10):
            row = {name: (float(day + item + 1) if kind == "numeric" else "A") for name, kind in _FEATURE_TYPES}
            row.update(hr_date=date(2024, 1, 1) + timedelta(days=day), charge_id=f"C{day}-{item}", judge="불량" if item < 2 else "양품", label_status="AVAILABLE")
            records.append(row)
    return pd.DataFrame(records)


def test_forward_folds_keep_dates_whole_and_purge_crossing_charges():
    data = rows()
    data.loc[39, "charge_id"] = "SHARED"
    data.loc[40, "charge_id"] = "SHARED"
    folds = build_forward_folds(data)
    assert len(folds) == 3
    assert len(folds[0].train) == 39
    assert len(folds[0].validation) == 39
    for fold in folds:
        assert max(fold.train.hr_date) < min(fold.validation.hr_date)
        assert set(fold.train.charge_id).isdisjoint(fold.validation.charge_id)


def test_candidate_training_is_repeatable_and_fits_fold_only():
    data = rows()
    data.loc[data.hr_date >= date(2024, 1, 5), "sm_plant"] = "FUTURE"
    first = evaluate_candidates(data, 42)
    second = evaluate_candidates(data, 42)
    assert len(first.ranked) == 3
    assert {candidate.name for candidate in first.ranked} == {"logistic", "hgb", "random_forest"}
    assert sha256(repr([(c.name, c.oof_probabilities, c.threshold) for c in first.ranked]).encode()).digest() == sha256(repr([(c.name, c.oof_probabilities, c.threshold) for c in second.ranked]).encode()).digest()
    assert "FUTURE" not in first.ranked[0].fold_contracts[0].to_wire()["features"][0]["vocabulary"]


def test_single_class_fold_fails_closed():
    data = rows()
    data.loc[data.hr_date <= date(2024, 1, 4), "judge"] = "양품"
    with pytest.raises(ValueError, match="INSUFFICIENT_LABELS"):
        evaluate_candidates(data, 42)


def test_unavailable_rows_never_fit_final_contract():
    data = rows()
    data.loc[len(data)] = data.iloc[0]
    data.loc[len(data) - 1, "label_status"] = "LABEL_NOT_YET_AVAILABLE"
    data.loc[len(data) - 1, "sm_plant"] = "FUTURE_ONLY"
    result = evaluate_candidates(data, 42)
    assert "FUTURE_ONLY" not in result.feature_contract.to_wire()["features"][0]["vocabulary"]
