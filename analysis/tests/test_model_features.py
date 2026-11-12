from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from equipment_quality.deterministic import canonical_json_bytes
from equipment_quality.model_features import (
    encode_model_rows,
    fit_feature_contract,
    project_model_rows,
)


RAW_ROW = {
    "sm_plant": "2공장", "steel_grade": "C13", "steel_usage": "Other",
    "cc_gubun": "2연주", "slab_gubun": "C", "tundish_temp": 1489.0,
    "mlac_ratio": 100.0, "delta_ferrite": 69.0, "ingre_cr": 18.29,
    "ingre_ni": 8.22, "ingre_s": 0.02, "slab_grind": "HSHS",
    "furnace_no": "1호기", "f_jangip_gubun": "CCR", "f_jangip_temp": 135.0,
    "slab_width": 1260.0, "f_pre_temp": 600.0, "f_pre_interval": 8.0,
    "f_heat_temp": 1150.0, "f_heat_interval": 38.0, "f_sock_temp": 1240.0,
    "f_sock_interval": 18.0, "f_bfg": 15.0, "f_cog": 6.0, "f_ldg": 2.0,
    "f_bfg_ratio": 0.6, "f_cog_ratio": 0.3, "f_ldg_ratio": 0.1,
}


def test_joined_row_projection_and_encoding_exclude_forbidden_fields():
    clean = pd.DataFrame([RAW_ROW], index=[42])
    joined = clean.assign(
        f_ext_time=13, cast_date="2026-01-01", f_ext_date="2026-01-02",
        hr_date="2026-01-02", ap_date="2026-01-03", created_at="12:00",
        rm4_temp=900, rm_pitch=9, hr_width=950, ap_line_speed=20, ap_plant="1AP",
        judge="불량", charge_id="CH", slab_no="SL", hr_coil_id="CO", ap_prod_id="AP",
        _source_record_number=3, fur_hr_record_number=9, row_order=2, split="holdout",
        label_status="AVAILABLE", bundle_id="B", criteria_id="C", model_id="M",
    )
    projected = project_model_rows(joined.iloc[:, ::-1])
    pd.testing.assert_frame_equal(projected, clean)
    contract = fit_feature_contract(joined)
    assert [feature["name"] for feature in contract.to_wire()["features"]] == list(RAW_ROW)
    assert canonical_json_bytes(contract.to_wire()) == canonical_json_bytes(
        fit_feature_contract(clean).to_wire()
    )
    first = encode_model_rows(clean, contract, require_labels=False)
    second = encode_model_rows(joined, contract, require_labels=False)
    assert first.matrix.shape == (1, 64)
    assert first.matrix[0, -6::2].tolist() == pytest.approx([0.6, 0.3, 0.1])
    assert first.matrix.tobytes() == second.matrix.tobytes()
    assert second.labels == (None,)


@pytest.mark.parametrize("field", [
    "f_ext_time", "hr_date", "rm4_temp", "ap_plant", "judge", "charge_id",
    "hr_coil_id", "ap_prod_id", "fur_hr_record_number", "row_order", "split",
    "label_status", "bundle_id", "criteria_id", "model_id",
])
def test_contract_rejects_non_allowlisted_feature_definitions(field):
    contract = fit_feature_contract(pd.DataFrame([RAW_ROW]))
    with pytest.raises(ValueError, match="allowlist"):
        replace(contract, numeric_medians=contract.numeric_medians + ((field, 1.0),))


def test_contract_rejects_missing_or_reordered_features():
    contract = fit_feature_contract(pd.DataFrame([RAW_ROW]))
    for medians in (contract.numeric_medians[:-1], contract.numeric_medians[::-1]):
        with pytest.raises(ValueError, match="allowlist"):
            replace(contract, numeric_medians=medians)


def test_fold_train_fixes_median_vocabulary_and_nearest_rank_p99():
    train = pd.DataFrame([RAW_ROW] * 100)
    train.loc[:48, "tundish_temp"] = 1000.0
    train.loc[49:97, "tundish_temp"] = 1200.0
    train.loc[98:, "tundish_temp"] = np.nan
    train.loc[99, "steel_grade"] = None
    train.loc[0, "steel_grade"] = "한글"
    train.loc[1, "steel_grade"] = "é"
    train.loc[2, "steel_grade"] = "UNKNOWN"
    contract = fit_feature_contract(train)
    wire = contract.to_wire()
    assert wire["features"][1]["vocabulary"] == ["C13", "é", "한글", "UNKNOWN"]
    assert wire["features"][5]["median"] == 1100.0
    assert wire["maxMissingFeatureCount"] == 1

    validation = pd.DataFrame([RAW_ROW] * 4)
    validation["steel_grade"] = ["unseen", None, None, "한글"]
    validation["tundish_temp"] = [1600.0, 1600.0, np.nan, 1600.0]
    validation["judge"] = ["불량", "양품", "불량", "양품"]
    first = encode_model_rows(validation, contract, require_labels=True)
    second_contract = fit_feature_contract(train.iloc[::-1, ::-1])
    second = encode_model_rows(validation, second_contract, require_labels=True)
    assert canonical_json_bytes(wire) == canonical_json_bytes(second_contract.to_wire())
    assert first.matrix.dtype == np.float32
    assert first.matrix.flags.c_contiguous
    assert first.matrix.tobytes() == second.matrix.tobytes()
    assert first.matrix[:, 3:8].tolist() == [
        [0, 0, 0, 1, 0], [0, 0, 0, 1, 1], [0, 0, 0, 1, 1], [0, 0, 1, 0, 0],
    ]
    assert first.matrix[:, 17:19].tolist() == [[1600, 0], [1600, 0], [1100, 1], [1600, 0]]
    assert first.input_incomplete.tolist() == [False, False, True, False]
    assert first.evaluable.tolist() == [True, True, False, True]


def test_each_raw_feature_has_its_own_missing_indicator():
    contract = fit_feature_contract(pd.DataFrame([RAW_ROW]))
    rows = pd.DataFrame([RAW_ROW | {field: None} for field in RAW_ROW])
    encoded = encode_model_rows(rows, contract, require_labels=False)
    indicator_indices = [
        2, 5, 8, 11, 14, 16, 18, 20, 22, 24, 26, 29, 32, 35, 37, 39,
        41, 43, 45, 47, 49, 51, 53, 55, 57, 59, 61, 63,
    ]
    np.testing.assert_array_equal(encoded.matrix[:, indicator_indices], np.eye(28))
    assert encoded.input_incomplete.all()


def test_imputed_zero_bytes_are_independent_of_training_row_order():
    train = pd.DataFrame([RAW_ROW] * 3).assign(f_bfg=[0.0, 0.0, -0.0])
    validation = pd.DataFrame([RAW_ROW | {"f_bfg": None}])
    first = encode_model_rows(validation, fit_feature_contract(train), require_labels=False)
    second = encode_model_rows(
        validation, fit_feature_contract(train.iloc[[0, 2, 1]]), require_labels=False
    )
    assert first.matrix.tobytes() == second.matrix.tobytes()


def test_targets_respect_label_availability_and_do_not_enter_matrix():
    rows = pd.DataFrame([RAW_ROW] * 6).assign(
        judge=["불량", "양품", "알 수 없음", pd.NA, "불량", "양품"],
        label_status=[
            "AVAILABLE", "AVAILABLE", "AVAILABLE", "LABEL_MISSING",
            "LABEL_NOT_YET_AVAILABLE", "AP_UNLINKED",
        ],
    )
    contract = fit_feature_contract(rows)
    training = encode_model_rows(rows, contract, require_labels=True)
    inference = encode_model_rows(rows, contract, require_labels=False)
    assert training.labels == (1, 0, None, None, None, None)
    assert training.evaluable.tolist() == [True, True, False, False, False, False]
    assert inference.labels == (None,) * 6
    assert not inference.evaluable.any()
    assert training.matrix.tobytes() == inference.matrix.tobytes()


@pytest.mark.parametrize("field, value", [
    ("tundish_temp", "1489"), ("tundish_temp", np.inf), ("steel_grade", 123),
])
def test_encoder_rejects_raw_type_violations(field, value):
    contract = fit_feature_contract(pd.DataFrame([RAW_ROW]))
    with pytest.raises(ValueError, match=field):
        encode_model_rows(pd.DataFrame([RAW_ROW | {field: value}]), contract, require_labels=False)


def test_missing_column_is_a_contract_error():
    with pytest.raises(ValueError, match="f_bfg_ratio"):
        project_model_rows(pd.DataFrame([RAW_ROW]).drop(columns="f_bfg_ratio"))


def test_fit_rejects_numeric_feature_without_any_observed_values():
    with pytest.raises(ValueError, match="tundish_temp"):
        fit_feature_contract(pd.DataFrame([RAW_ROW | {"tundish_temp": None}]))
