import importlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def load_subject():
    try:
        return importlib.import_module("training.steel.curate_genealogy")
    except ModuleNotFoundError:
        raise AssertionError("curation module is not implemented") from None


class CurateFramesTest(unittest.TestCase):
    def test_quarantines_ambiguous_relationships_and_keeps_one_row_per_coil(self):
        subject = load_subject()
        sm = pd.DataFrame(
            [
                {"charge_id": "C1", "slab_no": "1", "sm_value": 10},
                {"charge_id": "C2", "slab_no": "1", "sm_value": 20},
                {"charge_id": "C2", "slab_no": "1", "sm_value": 21},
                {"charge_id": "C3", "slab_no": "1", "sm_value": 30},
                {"charge_id": "C4", "slab_no": "1", "sm_value": 40},
                {"charge_id": "C5", "slab_no": "1", "sm_value": 50},
                {"charge_id": "C7", "slab_no": "1", "sm_value": 70},
                {"charge_id": "C8", "slab_no": "1", "sm_value": 80},
            ]
        )
        hr = pd.DataFrame(
            [
                {"charge_id": "C1", "slab_no": "1", "hr_coil_id": "H1", "hr_date": "2025-07-01", "hr_value": 100},
                {"charge_id": "C2", "slab_no": "1", "hr_coil_id": "H2", "hr_date": "2025-07-02", "hr_value": 200},
                {"charge_id": "C3", "slab_no": "1", "hr_coil_id": "H3", "hr_date": "2025-07-03", "hr_value": 300},
                {"charge_id": "C3", "slab_no": "1", "hr_coil_id": "H4", "hr_date": "2025-07-03", "hr_value": 301},
                {"charge_id": "C4", "slab_no": "1", "hr_coil_id": "H5", "hr_date": "2025-07-04", "hr_value": 400},
                {"charge_id": "C5", "slab_no": "1", "hr_coil_id": "H5", "hr_date": "2025-07-05", "hr_value": 500},
                {"charge_id": "C6", "slab_no": "1", "hr_coil_id": "H6", "hr_date": "2025-07-06", "hr_value": 600},
                {"charge_id": "C7", "slab_no": "1", "hr_coil_id": "H7", "hr_date": "2025-07-07", "hr_value": 700},
            ]
        )
        ap = pd.DataFrame(
            [
                {"hr_coil_id": "H1", "judge": "양품", "ap_date": "2025-07-10", "ap_line_speed": 40.0},
                {"hr_coil_id": "H2", "judge": "불량", "ap_date": "2025-07-11", "ap_line_speed": 41.0},
                {"hr_coil_id": "H3", "judge": "양품", "ap_date": "2025-07-12", "ap_line_speed": 42.0},
                {"hr_coil_id": "H4", "judge": "불량", "ap_date": "2025-07-12", "ap_line_speed": 43.0},
                {"hr_coil_id": "H5", "judge": "양품", "ap_date": "2025-07-13", "ap_line_speed": 44.0},
                {"hr_coil_id": "H6", "judge": "양품", "ap_date": "2025-07-14", "ap_line_speed": 45.0},
                {"hr_coil_id": "HX", "judge": "불량", "ap_date": "2025-07-15", "ap_line_speed": 46.0},
            ]
        )

        result = subject.curate_frames(sm, hr, ap)

        self.assertEqual(result.curated["hr_coil_id"].tolist(), ["H1"])
        self.assertEqual(result.curated["judge"].tolist(), ["양품"])
        self.assertEqual(result.curated["sm_value"].tolist(), [10])
        self.assertEqual(result.curated["hr_value"].tolist(), [100])
        self.assertNotIn("ap_line_speed", result.curated.columns)
        self.assertEqual(result.curated["hr_coil_id"].nunique(), len(result.curated))

        reasons = result.coil_audit.set_index("hr_coil_id")["reason_codes"].to_dict()
        self.assertEqual(reasons["H1"], "")
        self.assertEqual(reasons["H2"], "SM_PROCESS_KEY_DUPLICATE")
        self.assertEqual(reasons["H3"], "HR_PROCESS_KEY_DUPLICATE")
        self.assertEqual(reasons["H4"], "HR_PROCESS_KEY_DUPLICATE")
        self.assertEqual(reasons["H5"], "HR_COIL_ID_DUPLICATE")
        self.assertEqual(reasons["H6"], "SM_PROCESS_MISSING")
        self.assertEqual(reasons["H7"], "AP_LABEL_MISSING")
        self.assertEqual(reasons["HX"], "HR_MISSING")

        row_reasons = result.hr_row_audit.groupby("hr_coil_id")["exclusive_reason"].agg(list).to_dict()
        self.assertEqual(row_reasons["H1"], [""])
        self.assertEqual(row_reasons["H2"], ["SM_PROCESS_KEY_DUPLICATE"])
        self.assertEqual(row_reasons["H3"], ["HR_PROCESS_KEY_DUPLICATE"])
        self.assertEqual(row_reasons["H4"], ["HR_PROCESS_KEY_DUPLICATE"])
        self.assertEqual(row_reasons["H5"], ["HR_COIL_ID_DUPLICATE", "HR_COIL_ID_DUPLICATE"])
        self.assertEqual(row_reasons["H6"], ["SM_PROCESS_MISSING"])
        self.assertEqual(row_reasons["H7"], ["AP_LABEL_MISSING"])

        process_decisions = result.process_audit.set_index(["charge_id", "slab_no"])["join_type"].to_dict()
        self.assertEqual(process_decisions[("C1", "1")], "1:1")
        self.assertEqual(process_decisions[("C2", "1")], "N:1")
        self.assertEqual(process_decisions[("C3", "1")], "1:N")
        self.assertEqual(process_decisions[("C8", "1")], "SM_ONLY")

    def test_rejects_missing_schema_and_invalid_labels(self):
        subject = load_subject()
        sm = pd.DataFrame([{"charge_id": "C1", "slab_no": "1"}])
        hr = pd.DataFrame(
            [{"charge_id": "C1", "slab_no": "1", "hr_coil_id": "H1"}]
        )
        ap = pd.DataFrame(
            [{"hr_coil_id": "H1", "judge": "UNKNOWN", "ap_date": "2025-07-02"}]
        )

        with self.assertRaisesRegex(ValueError, "HR is missing required columns: hr_date"):
            subject.curate_frames(sm, hr, ap)

        hr["hr_date"] = "2025-07-01"
        with self.assertRaisesRegex(ValueError, "AP judge contains unsupported values: UNKNOWN"):
            subject.curate_frames(sm, hr, ap)

    def test_adds_explicit_upstream_data_quality_flags(self):
        subject = load_subject()
        sm = pd.DataFrame(
            [
                {
                    "charge_id": "C1",
                    "slab_no": "1",
                    "tundish_temp": 0,
                    "mlac_ratio": 120,
                    "slab_grind": "MPMP",
                    "extra_sm": None,
                },
                {
                    "charge_id": "C2",
                    "slab_no": "1",
                    "tundish_temp": 1500,
                    "mlac_ratio": 80,
                    "slab_grind": "HSHS",
                    "extra_sm": 1,
                },
            ]
        )
        hr = pd.DataFrame(
            [
                {
                    "charge_id": "C1",
                    "slab_no": "1",
                    "hr_coil_id": "H1",
                    "hr_date": "2025-07-01",
                    "f_sock_temp": 1500,
                    "f_bfg_per": 0,
                    "f_cog_per": 0,
                    "f_ldg_per": 0,
                },
                {
                    "charge_id": "C2",
                    "slab_no": "1",
                    "hr_coil_id": "H2",
                    "hr_date": "2025-07-02",
                    "f_sock_temp": 1250,
                    "f_bfg_per": 40,
                    "f_cog_per": 30,
                    "f_ldg_per": 30,
                },
            ]
        )
        ap = pd.DataFrame(
            [
                {"hr_coil_id": "H1", "judge": "불량", "ap_date": "2025-07-03"},
                {"hr_coil_id": "H2", "judge": "양품", "ap_date": "2025-07-04"},
            ]
        )

        curated = subject.curate_frames(sm, hr, ap).curated.set_index("hr_coil_id")

        self.assertEqual(curated.loc["H1", "dq_missing_upstream_count"], 1)
        self.assertTrue(bool(curated.loc["H1", "dq_tundish_temp_flag"]))
        self.assertTrue(bool(curated.loc["H1", "dq_mlac_ratio_flag"]))
        self.assertTrue(bool(curated.loc["H1", "dq_f_sock_temp_flag"]))
        self.assertTrue(bool(curated.loc["H1", "dq_gas_ratio_sum_flag"]))
        self.assertTrue(bool(curated.loc["H1", "dq_slab_grind_unmapped_flag"]))
        self.assertTrue(bool(curated.loc["H1", "dq_any_flag"]))
        self.assertFalse(bool(curated.loc["H2", "dq_any_flag"]))


class SplitPolicyTest(unittest.TestCase):
    def test_excludes_boundary_charges_and_keeps_splits_group_disjoint(self):
        subject = load_subject()
        curated = pd.DataFrame(
            [
                {"charge_id": "T", "hr_coil_id": "T1", "hr_date": "2025-07-01"},
                {"charge_id": "T", "hr_coil_id": "T2", "hr_date": "2025-07-15"},
                {"charge_id": "V", "hr_coil_id": "V1", "hr_date": "2025-08-05"},
                {"charge_id": "H", "hr_coil_id": "H1", "hr_date": "2025-09-05"},
                {"charge_id": "B1", "hr_coil_id": "B11", "hr_date": "2025-07-31"},
                {"charge_id": "B1", "hr_coil_id": "B12", "hr_date": "2025-08-01"},
                {"charge_id": "B2", "hr_coil_id": "B21", "hr_date": "2025-08-31"},
                {"charge_id": "B2", "hr_coil_id": "B22", "hr_date": "2025-09-01"},
            ]
        )

        split = subject.assign_group_time_splits(curated)
        by_charge = split.groupby("charge_id")["dataset_split"].unique().to_dict()

        self.assertEqual(by_charge["T"].tolist(), ["TRAIN"])
        self.assertEqual(by_charge["V"].tolist(), ["VALIDATION"])
        self.assertEqual(by_charge["H"].tolist(), ["HOLDOUT"])
        self.assertEqual(by_charge["B1"].tolist(), ["BOUNDARY_EXCLUDED"])
        self.assertEqual(by_charge["B2"].tolist(), ["BOUNDARY_EXCLUDED"])
        assigned = {
            name: set(group["charge_id"])
            for name, group in split[split["dataset_split"] != "BOUNDARY_EXCLUDED"].groupby("dataset_split")
        }
        self.assertFalse(assigned["TRAIN"] & assigned["VALIDATION"])
        self.assertFalse(assigned["TRAIN"] & assigned["HOLDOUT"])
        self.assertFalse(assigned["VALIDATION"] & assigned["HOLDOUT"])


class RunCurationTest(unittest.TestCase):
    def test_writes_reproducible_audits_and_modeling_cohort(self):
        subject = load_subject()
        sm = pd.DataFrame(
            [
                {"charge_id": "C1", "slab_no": "1", "tundish_temp": 1500},
                {"charge_id": "C2", "slab_no": "1", "tundish_temp": 1501},
                {"charge_id": "C3", "slab_no": "1", "tundish_temp": 1502},
                {"charge_id": "CB", "slab_no": "1", "tundish_temp": 1503},
                {"charge_id": "CB", "slab_no": "2", "tundish_temp": 1504},
            ]
        )
        hr = pd.DataFrame(
            [
                {"charge_id": "C1", "slab_no": "1", "hr_coil_id": "H1", "hr_date": "2025-07-01"},
                {"charge_id": "C2", "slab_no": "1", "hr_coil_id": "H2", "hr_date": "2025-08-02"},
                {"charge_id": "C3", "slab_no": "1", "hr_coil_id": "H3", "hr_date": "2025-09-02"},
                {"charge_id": "CB", "slab_no": "1", "hr_coil_id": "HB1", "hr_date": "2025-07-31"},
                {"charge_id": "CB", "slab_no": "2", "hr_coil_id": "HB2", "hr_date": "2025-08-01"},
            ]
        )
        ap = pd.DataFrame(
            [
                {"hr_coil_id": "H1", "judge": "양품", "ap_date": "2025-07-02"},
                {"hr_coil_id": "H2", "judge": "불량", "ap_date": "2025-08-03"},
                {"hr_coil_id": "H3", "judge": "양품", "ap_date": "2025-09-03"},
                {"hr_coil_id": "HB1", "judge": "양품", "ap_date": "2025-08-01"},
                {"hr_coil_id": "HB2", "judge": "불량", "ap_date": "2025-08-02"},
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            paths = {
                "sm": input_dir / "sm.csv",
                "hr": input_dir / "hr.csv",
                "ap": input_dir / "ap.csv",
            }
            sm.to_csv(paths["sm"], index=False, encoding="cp949")
            hr.to_csv(paths["hr"], index=False, encoding="cp949")
            ap.to_csv(paths["ap"], index=False, encoding="cp949")

            summary = subject.run_curation(paths["sm"], paths["hr"], paths["ap"], output_dir)

            expected_files = {
                "curated_coil_dataset.csv",
                "modeling_cohort.csv",
                "process_key_audit.csv",
                "coil_audit.csv",
                "quarantine_coils.csv",
                "hr_row_audit.csv",
                "hr_matching_quarantine.csv",
                "split_manifest.csv",
                "curation_summary.json",
            }
            self.assertEqual({path.name for path in output_dir.iterdir()}, expected_files)
            self.assertEqual(summary["curation_version"], "steel-genealogy-v0.1")
            self.assertEqual(summary["counts"]["curated_rows_before_boundary"], 5)
            self.assertEqual(summary["counts"]["boundary_excluded_rows"], 2)
            self.assertEqual(summary["counts"]["modeling_rows"], 3)
            self.assertEqual(summary["split_counts"]["TRAIN"]["rows"], 1)
            self.assertEqual(summary["split_counts"]["VALIDATION"]["rows"], 1)
            self.assertEqual(summary["split_counts"]["HOLDOUT"]["rows"], 1)
            self.assertEqual(set(summary["source_files"]), {"sm", "hr", "ap"})
            self.assertTrue(all("sha256" in item for item in summary["source_files"].values()))

            saved_summary = json.loads((output_dir / "curation_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_summary, summary)
            curated_saved = pd.read_csv(output_dir / "curated_coil_dataset.csv", encoding="utf-8-sig")
            modeling_saved = pd.read_csv(output_dir / "modeling_cohort.csv", encoding="utf-8-sig")
            self.assertEqual(len(curated_saved), 5)
            self.assertEqual(len(modeling_saved), 3)
            self.assertNotIn("ap_line_speed", modeling_saved.columns)


if __name__ == "__main__":
    unittest.main()
