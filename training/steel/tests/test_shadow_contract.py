import importlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def load_subject():
    try:
        return importlib.import_module("training.steel.shadow_contract")
    except ModuleNotFoundError:
        raise AssertionError("shadow contract module is not implemented") from None


def registry_fixture() -> dict[str, object]:
    schema = {
        "included": ["f_heat_temp", "furnace_no"],
        "numeric": ["f_heat_temp"],
        "categorical": ["furnace_no"],
        "engineered": [],
    }
    return {
        "registry_version": "steel-shadow-registry-v0.1",
        "models": [
            {
                "model_id": "fixture-model",
                "role": "shadow_incumbent",
                "feature_schema": schema,
            }
        ],
    }


def valid_feature_batch() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "charge_id": ["C1"],
            "slab_no": ["1"],
            "hr_coil_id": ["H1"],
            "hr_date": ["2026-09-01"],
            "feature_available_at": ["2026-09-01T01:00:00+00:00"],
            "f_heat_temp": [1250.0],
            "furnace_no": ["1호기"],
        }
    )


class FeatureBatchContractTest(unittest.TestCase):
    def test_accepts_contract_and_orders_columns(self):
        subject = load_subject()

        result = subject.validate_feature_batch(
            valid_feature_batch(), registry_fixture(), set()
        )

        self.assertEqual(
            result.columns.tolist(),
            [
                "charge_id",
                "slab_no",
                "hr_coil_id",
                "hr_date",
                "feature_available_at",
                "f_heat_temp",
                "furnace_no",
                "shadow_numeric_coercion_count",
            ],
        )
        self.assertEqual(result.loc[0, "shadow_numeric_coercion_count"], 0)

    def test_rejects_labels_and_ap_columns(self):
        subject = load_subject()
        for column, value in (
            ("judge", "양품"),
            ("ap_date", "2026-09-02"),
            ("label_finalized_at", "2026-09-02T01:00:00Z"),
            ("dataset_split", "SHADOW"),
            ("ap_line_speed", 10.0),
        ):
            with self.subTest(column=column):
                with self.assertRaisesRegex(ValueError, "forbidden"):
                    subject.validate_feature_batch(
                        valid_feature_batch().assign(**{column: value}),
                        registry_fixture(),
                        set(),
                    )

    def test_rejects_missing_features_and_duplicate_coils(self):
        subject = load_subject()
        with self.assertRaisesRegex(ValueError, "missing model features"):
            subject.validate_feature_batch(
                valid_feature_batch().drop(columns="f_heat_temp"),
                registry_fixture(),
                set(),
            )
        with self.assertRaisesRegex(ValueError, "already scored"):
            subject.validate_feature_batch(
                valid_feature_batch(), registry_fixture(), {"H1"}
            )
        duplicated = pd.concat(
            [valid_feature_batch(), valid_feature_batch()], ignore_index=True
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            subject.validate_feature_batch(duplicated, registry_fixture(), set())

    def test_numeric_contract_coerces_invalid_values_and_counts_them(self):
        subject = load_subject()
        frame = valid_feature_batch().assign(f_heat_temp="not-a-number")

        result = subject.validate_feature_batch(frame, registry_fixture(), set())

        self.assertTrue(pd.isna(result.loc[0, "f_heat_temp"]))
        self.assertEqual(result.loc[0, "shadow_numeric_coercion_count"], 1)

    def test_requires_timezone_aware_feature_available_at(self):
        subject = load_subject()
        with self.assertRaisesRegex(ValueError, "timezone"):
            subject.validate_feature_batch(
                valid_feature_batch().assign(
                    feature_available_at="2026-09-01 01:00:00"
                ),
                registry_fixture(),
                set(),
            )

    def test_derives_registered_engineered_features_from_base_inputs(self):
        subject = load_subject()
        registry = registry_fixture()
        registry["models"][0]["feature_schema"] = {
            "included": [
                "f_bfg",
                "f_cog",
                "f_ldg",
                "f_pre_interval",
                "f_heat_interval",
                "f_sock_interval",
                "f_pre_temp",
                "f_heat_temp",
                "f_sock_temp",
                "slab_width",
                "hr_width",
                "gas_total",
                "heating_interval_total",
                "pre_to_heat_temp_delta",
                "heat_to_sock_temp_delta",
                "width_reduction",
                "width_ratio",
            ],
            "numeric": [
                "f_bfg",
                "f_cog",
                "f_ldg",
                "f_pre_interval",
                "f_heat_interval",
                "f_sock_interval",
                "f_pre_temp",
                "f_heat_temp",
                "f_sock_temp",
                "slab_width",
                "hr_width",
                "gas_total",
                "heating_interval_total",
                "pre_to_heat_temp_delta",
                "heat_to_sock_temp_delta",
                "width_reduction",
                "width_ratio",
            ],
            "categorical": [],
            "engineered": list(subject.ENGINEERED_FEATURES),
        }
        frame = valid_feature_batch().drop(columns="furnace_no").assign(
            f_bfg=10.0,
            f_cog=20.0,
            f_ldg=30.0,
            f_pre_interval=1.0,
            f_heat_interval=2.0,
            f_sock_interval=3.0,
            f_pre_temp=1100.0,
            f_sock_temp=1270.0,
            slab_width=1000.0,
            hr_width=800.0,
        )

        result = subject.validate_feature_batch(frame, registry, set())

        self.assertEqual(result.loc[0, "gas_total"], 60.0)
        self.assertEqual(result.loc[0, "heating_interval_total"], 6.0)
        self.assertEqual(result.loc[0, "pre_to_heat_temp_delta"], 150.0)
        self.assertEqual(result.loc[0, "heat_to_sock_temp_delta"], 20.0)
        self.assertEqual(result.loc[0, "width_reduction"], 200.0)
        self.assertEqual(result.loc[0, "width_ratio"], 0.8)
        with self.assertRaisesRegex(ValueError, "derived"):
            subject.validate_feature_batch(frame.assign(gas_total=999.0), registry, set())


class LabelBatchContractTest(unittest.TestCase):
    def test_requires_one_final_timezone_aware_label_per_coil(self):
        subject = load_subject()
        valid = pd.DataFrame(
            {
                "hr_coil_id": ["H1"],
                "judge": ["불량"],
                "label_finalized_at": ["2026-09-05T03:00:00+00:00"],
            }
        )

        result = subject.validate_label_batch(valid)

        self.assertEqual(result.loc[0, "judge"], "불량")
        self.assertEqual(result.loc[0, "label_finalized_at"], "2026-09-05T03:00:00+00:00")
        with self.assertRaisesRegex(ValueError, "timezone"):
            subject.validate_label_batch(
                valid.assign(label_finalized_at="2026-09-05 03:00:00")
            )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            subject.validate_label_batch(pd.concat([valid, valid], ignore_index=True))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            subject.validate_label_batch(valid.assign(judge="재검"))


class SerializationContractTest(unittest.TestCase):
    def test_writes_strict_json_and_bom_csv_without_overwrite(self):
        subject = load_subject()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            json_path = root / "payload.json"
            csv_path = root / "payload.csv"
            text_path = root / "payload.md"

            subject.atomic_write_json({"value": 1.5}, json_path)
            subject.atomic_write_csv(pd.DataFrame({"값": [1]}), csv_path)
            subject.atomic_write_text("안전한 보고서\n", text_path)

            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8")), {"value": 1.5})
            self.assertTrue(csv_path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertEqual(text_path.read_text(encoding="utf-8"), "안전한 보고서\n")
            with self.assertRaises(FileExistsError):
                subject.atomic_write_json({"value": 2.0}, json_path)
            with self.assertRaises(ValueError):
                subject.atomic_write_json({"value": float("nan")}, root / "nan.json")

    def test_batch_id_is_utc_and_source_bound(self):
        subject = load_subject()

        batch_id = subject.make_batch_id(
            "a" * 64, pd.Timestamp("2026-09-01T11:22:33.123456+09:00")
        )

        self.assertEqual(batch_id, "20260901T022233123456Z-aaaaaaaaaaaa")


if __name__ == "__main__":
    unittest.main()
