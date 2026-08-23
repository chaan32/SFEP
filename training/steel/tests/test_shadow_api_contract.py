import copy
import importlib
import unittest


BASE_FEATURES = [
    "sm_plant",
    "steel_grade",
    "steel_usage",
    "delta_ferrite",
    "ingre_cr",
    "ingre_ni",
    "ingre_s",
    "cc_gubun",
    "tundish_temp",
    "mlac_ratio",
    "slab_gubun",
    "slab_grind",
    "furnace_no",
    "f_jangip_gubun",
    "f_jangip_temp",
    "f_bfg",
    "f_cog",
    "f_ldg",
    "f_pre_temp",
    "f_heat_temp",
    "f_sock_temp",
    "f_pre_interval",
    "f_heat_interval",
    "f_sock_interval",
    "f_ext_time",
    "hr_thick",
    "hr_width",
    "rm4_temp",
    "rm_pitch",
    "slab_width",
]

ENGINEERED_FEATURES = [
    "gas_total",
    "heating_interval_total",
    "pre_to_heat_temp_delta",
    "heat_to_sock_temp_delta",
    "width_reduction",
    "width_ratio",
]


def load_subject():
    try:
        return importlib.import_module("training.steel.shadow_api_contract")
    except ModuleNotFoundError:
        raise AssertionError("Shadow API contract module is not implemented") from None


def registry_fixture():
    return {
        "models": [
            {
                "feature_schema": {
                    "included": [*BASE_FEATURES, *ENGINEERED_FEATURES],
                }
            }
        ]
    }


def feature_values():
    categorical = {
        "sm_plant",
        "steel_grade",
        "steel_usage",
        "cc_gubun",
        "slab_gubun",
        "slab_grind",
        "furnace_no",
        "f_jangip_gubun",
    }
    return {
        feature: (f"{feature}-value" if feature in categorical else float(index + 1))
        for index, feature in enumerate(BASE_FEATURES)
    }


def feature_coil(hr_coil_id="H20260822001"):
    return {
        "chargeId": "C20260822001",
        "slabNo": "1",
        "hrCoilId": hr_coil_id,
        "hrDate": "2026-08-22",
        "featureAvailableAt": "2026-08-22T03:15:00+00:00",
        "features": feature_values(),
    }


def valid_feature_payload():
    return {
        "schemaVersion": "steel-shadow-feature-batch-v1",
        "requestId": "hr-20260822-a-001",
        "coils": [feature_coil()],
    }


def valid_label_payload():
    return {
        "schemaVersion": "steel-shadow-label-batch-v1",
        "requestId": "ap-20260825-final-001",
        "supersedesLabelBatchId": None,
        "labels": [
            {
                "hrCoilId": "H20260822001",
                "judge": "불량",
                "labelFinalizedAt": "2026-08-25T09:00:00+00:00",
            }
        ],
    }


class ShadowFeatureApiContractTest(unittest.TestCase):
    def test_parses_valid_camel_case_request_in_contract_order(self):
        subject = load_subject()

        request_id, frame = subject.parse_feature_request(
            valid_feature_payload(), registry_fixture(), max_coils=10_000
        )

        self.assertEqual(request_id, "hr-20260822-a-001")
        self.assertEqual(
            frame.columns[:5].tolist(),
            [
                "charge_id",
                "slab_no",
                "hr_coil_id",
                "hr_date",
                "feature_available_at",
            ],
        )
        self.assertEqual(frame.columns[5:].tolist(), BASE_FEATURES)
        self.assertFalse(set(ENGINEERED_FEATURES) & set(frame.columns))

    def test_identical_payloads_produce_identical_canonical_bytes(self):
        subject = load_subject()
        payload = valid_feature_payload()
        payload["coils"].append(feature_coil("H20260822000"))
        reordered = copy.deepcopy(payload)
        reordered["coils"].reverse()
        for coil in reordered["coils"]:
            coil["features"] = dict(reversed(list(coil["features"].items())))

        _, first = subject.parse_feature_request(
            payload, registry_fixture(), max_coils=10_000
        )
        _, second = subject.parse_feature_request(
            reordered, registry_fixture(), max_coils=10_000
        )

        first_bytes = subject.canonical_csv_bytes(first)
        self.assertEqual(first_bytes, subject.canonical_csv_bytes(second))
        self.assertTrue(first_bytes.startswith(b"\xef\xbb\xbf"))

    def test_rejects_forbidden_and_engineered_fields(self):
        subject = load_subject()
        for field in ("judge", "ap_date", "ap_line_speed", "gas_total"):
            with self.subTest(field=field):
                payload = valid_feature_payload()
                payload["coils"][0]["features"][field] = "forbidden"
                with self.assertRaisesRegex(
                    subject.ShadowApiContractError, "forbidden"
                ) as caught:
                    subject.parse_feature_request(
                        payload, registry_fixture(), max_coils=10_000
                    )
                self.assertEqual(caught.exception.code, "FEATURE_CONTRACT_INVALID")

    def test_rejects_wrong_schema_unknown_fields_and_bad_request_ids(self):
        subject = load_subject()
        cases = []
        wrong_version = valid_feature_payload()
        wrong_version["schemaVersion"] = "v0"
        cases.append(wrong_version)
        unknown_top = valid_feature_payload()
        unknown_top["extra"] = True
        cases.append(unknown_top)
        unknown_coil = valid_feature_payload()
        unknown_coil["coils"][0]["predictionGeneratedAt"] = "caller-controlled"
        cases.append(unknown_coil)
        empty_request_id = valid_feature_payload()
        empty_request_id["requestId"] = ""
        cases.append(empty_request_id)
        non_printable_request_id = valid_feature_payload()
        non_printable_request_id["requestId"] = "bad\nrequest"
        cases.append(non_printable_request_id)

        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(subject.ShadowApiContractError) as caught:
                    subject.parse_feature_request(
                        payload, registry_fixture(), max_coils=10_000
                    )
                self.assertEqual(caught.exception.code, "REQUEST_SCHEMA_INVALID")

    def test_rejects_missing_unknown_and_duplicate_coils(self):
        subject = load_subject()
        missing = valid_feature_payload()
        missing["coils"][0]["features"].pop(BASE_FEATURES[-1])
        unknown = valid_feature_payload()
        unknown["coils"][0]["features"]["mystery"] = 1
        duplicate = valid_feature_payload()
        duplicate["coils"].append(copy.deepcopy(duplicate["coils"][0]))

        for payload in (missing, unknown, duplicate):
            with self.subTest(payload=payload):
                with self.assertRaises(subject.ShadowApiContractError) as caught:
                    subject.parse_feature_request(
                        payload, registry_fixture(), max_coils=10_000
                    )
                self.assertEqual(caught.exception.code, "FEATURE_CONTRACT_INVALID")

    def test_enforces_batch_size_boundaries(self):
        subject = load_subject()
        empty = valid_feature_payload()
        empty["coils"] = []
        too_many = valid_feature_payload()
        too_many["coils"] = [feature_coil()] * 10_001

        for payload in (empty, too_many):
            with self.subTest(size=len(payload["coils"])):
                with self.assertRaises(subject.ShadowApiContractError) as caught:
                    subject.parse_feature_request(
                        payload, registry_fixture(), max_coils=10_000
                    )
                self.assertEqual(caught.exception.code, "REQUEST_SCHEMA_INVALID")


class ShadowLabelApiContractTest(unittest.TestCase):
    def test_parses_and_orders_valid_labels(self):
        subject = load_subject()
        payload = valid_label_payload()
        payload["labels"].append(
            {
                "hrCoilId": "H20260822000",
                "judge": "양품",
                "labelFinalizedAt": "2026-08-25T09:00:00Z",
            }
        )

        request_id, supersedes, frame = subject.parse_label_request(
            payload, max_coils=10_000
        )

        self.assertEqual(request_id, "ap-20260825-final-001")
        self.assertIsNone(supersedes)
        self.assertEqual(frame["hr_coil_id"].tolist(), ["H20260822000", "H20260822001"])
        self.assertEqual(
            frame.columns.tolist(),
            ["hr_coil_id", "judge", "label_finalized_at"],
        )

    def test_rejects_invalid_labels_unknown_fields_and_duplicate_coils(self):
        subject = load_subject()
        invalid_judge = valid_label_payload()
        invalid_judge["labels"][0]["judge"] = "보류"
        unknown = valid_label_payload()
        unknown["labels"][0]["apDate"] = "2026-08-25"
        duplicate = valid_label_payload()
        duplicate["labels"].append(copy.deepcopy(duplicate["labels"][0]))

        for payload in (invalid_judge, unknown, duplicate):
            with self.subTest(payload=payload):
                with self.assertRaises(subject.ShadowApiContractError) as caught:
                    subject.parse_label_request(payload, max_coils=10_000)
                self.assertEqual(caught.exception.code, "LABEL_CONTRACT_INVALID")


if __name__ == "__main__":
    unittest.main()
