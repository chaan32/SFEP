import importlib
import importlib.util
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import error, parse, request

import pandas as pd

from training.steel.tests.test_run_shadow import write_model_fixtures


def load_subject():
    try:
        return importlib.import_module("training.steel.shadow_api")
    except ModuleNotFoundError:
        raise AssertionError("Shadow HTTP API module is not implemented") from None


def load_smoke_script():
    path = Path(__file__).resolve().parents[3] / "scripts" / "smoke-steel-shadow-bridge.py"
    specification = importlib.util.spec_from_file_location("steel_shadow_smoke", path)
    if specification is None or specification.loader is None:
        raise AssertionError("unable to load Steel Shadow smoke script")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def feature_payload():
    return {
        "schemaVersion": "steel-shadow-feature-batch-v1",
        "requestId": "hr-20260901-a-001",
        "coils": [
            {
                "chargeId": "C1",
                "slabNo": "1",
                "hrCoilId": "H1",
                "hrDate": "2026-09-01",
                "featureAvailableAt": "2026-09-01T01:00:00Z",
                "features": {
                    "f_heat_temp": 1240.0,
                    "furnace_no": "1호기",
                },
            }
        ],
    }


def label_payload():
    return {
        "schemaVersion": "steel-shadow-label-batch-v1",
        "requestId": "ap-20260901-final-001",
        "supersedesLabelBatchId": None,
        "labels": [
            {
                "hrCoilId": "H1",
                "judge": "불량",
                "labelFinalizedAt": "2026-09-01T02:30:00Z",
            }
        ],
    }


class MutableClock:
    def __init__(self, value: str):
        self.current = pd.Timestamp(value)

    def __call__(self):
        return self.current


def flatten_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from flatten_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from flatten_keys(child)


class ShadowApiApplicationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        baseline_path, challenger_path, capacity_path = write_model_fixtures(self.root)
        self.registry_path = self.root / "registry.json"
        self.output_dir = self.root / "shadow_output"
        runner = importlib.import_module("training.steel.run_shadow")
        runner.build_registry(
            baseline_path,
            challenger_path,
            capacity_path,
            self.registry_path,
            now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
        )
        self.clock = MutableClock("2026-09-01T02:00:00Z")
        subject = load_subject()
        self.app = subject.ShadowApiApplication(
            registry_path=self.registry_path,
            output_dir=self.output_dir,
            max_coils=10_000,
            now=self.clock,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_feature_request_scores_every_registered_model_without_paths(self):
        response = self.app.handle_features(feature_payload())

        self.assertEqual(response["schemaVersion"], "steel-shadow-score-response-v1")
        self.assertEqual(response["requestId"], "hr-20260901-a-001")
        self.assertEqual(response["coilCount"], 1)
        self.assertEqual(len(response["predictions"]), 2)
        self.assertEqual(
            {item["modelRole"] for item in response["predictions"]},
            {"reference_baseline", "shadow_incumbent"},
        )
        self.assertEqual(response["shadowStatus"], "COLLECTING")
        self.assertFalse(response["deploymentEligible"])
        self.assertFalse(any("path" in key.lower() for key in flatten_keys(response)))
        json.dumps(response, ensure_ascii=False, allow_nan=False)

    def test_identical_retry_returns_one_committed_batch(self):
        first = self.app.handle_features(feature_payload())
        self.clock.current = pd.Timestamp("2026-09-01T02:10:00Z")
        second = self.app.handle_features(feature_payload())

        self.assertEqual(first["batchId"], second["batchId"])
        self.assertEqual(
            len(list((self.output_dir / "prediction_batches").glob("*.metadata.json"))),
            1,
        )

    def test_label_request_evaluates_after_commit(self):
        self.app.handle_features(feature_payload())
        self.clock.current = pd.Timestamp("2026-09-01T03:00:00Z")

        response = self.app.handle_labels(label_payload())

        self.assertEqual(response["schemaVersion"], "steel-shadow-label-response-v1")
        self.assertEqual(response["requestId"], "ap-20260901-final-001")
        self.assertEqual(response["labelCount"], 1)
        self.assertEqual(response["positiveLabels"], 1)
        self.assertEqual(response["shadowStatus"], "COLLECTING")
        self.assertFalse(response["deploymentEligible"])
        self.assertFalse(any("path" in key.lower() for key in flatten_keys(response)))

    def test_status_prediction_and_health_are_safe_views(self):
        score = self.app.handle_features(feature_payload())

        status = self.app.status()
        predictions = self.app.predictions("H1")
        health = self.app.health()

        self.assertEqual(status["status"], "COLLECTING")
        self.assertEqual(predictions["hrCoilId"], "H1")
        self.assertEqual(len(predictions["predictions"]), 2)
        self.assertEqual(predictions["batchId"], score["batchId"])
        self.assertEqual(health["status"], "UP")
        self.assertEqual(len(health["models"]), 2)
        for response in (status, predictions, health):
            self.assertFalse(response["deploymentEligible"])
            self.assertFalse(any("path" in key.lower() for key in flatten_keys(response)))

    def test_unknown_coil_raises_stable_not_found_error(self):
        subject = load_subject()
        with self.assertRaises(subject.ShadowApiError) as caught:
            self.app.predictions("UNKNOWN")

        self.assertEqual(caught.exception.http_status, 404)
        self.assertEqual(caught.exception.code, "COIL_NOT_FOUND")
        self.assertFalse(caught.exception.retryable)


class ShadowApiHttpTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        baseline_path, challenger_path, capacity_path = write_model_fixtures(self.root)
        self.registry_path = self.root / "registry.json"
        self.output_dir = self.root / "shadow_output"
        runner = importlib.import_module("training.steel.run_shadow")
        runner.build_registry(
            baseline_path,
            challenger_path,
            capacity_path,
            self.registry_path,
            now=lambda: pd.Timestamp("2026-09-01T00:00:00Z"),
        )
        self.clock = MutableClock("2026-09-01T02:00:00Z")
        subject = load_subject()
        config = subject.ShadowApiConfig(
            host="127.0.0.1",
            port=0,
            registry_path=self.registry_path,
            output_dir=self.output_dir,
            max_request_bytes=1024,
            max_coils=10_000,
            request_timeout_seconds=1.0,
        )
        self.server = subject.build_server(config, now=self.clock)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def exchange(self, method, path, *, payload=None, body=None, content_type=None):
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        headers = {}
        if content_type is not None:
            headers["Content-Type"] = content_type
        req = request.Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            finally:
                exc.close()

    def raw_exchange(self, request_bytes, *, shutdown_write=False):
        with socket.create_connection(self.server.server_address, timeout=5) as client:
            client.settimeout(5)
            client.sendall(request_bytes)
            if shutdown_write:
                client.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                try:
                    chunk = client.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)

    def test_routes_score_status_prediction_label_and_health(self):
        status, score = self.exchange("POST", "/v1/features", payload=feature_payload())
        self.assertEqual(status, 200)
        self.assertEqual(len(score["predictions"]), 2)

        status, health = self.exchange("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "UP")

        status, shadow_status = self.exchange("GET", "/v1/status")
        self.assertEqual(status, 200)
        self.assertEqual(shadow_status["status"], "COLLECTING")

        encoded_coil = parse.quote("H1", safe="")
        status, prediction = self.exchange(
            "GET", f"/v1/predictions/{encoded_coil}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(prediction["hrCoilId"], "H1")

        self.clock.current = pd.Timestamp("2026-09-01T03:00:00Z")
        status, labels = self.exchange("POST", "/v1/labels", payload=label_payload())
        self.assertEqual(status, 200)
        self.assertEqual(labels["shadowStatus"], "COLLECTING")

        for response in (score, health, shadow_status, prediction, labels):
            self.assertFalse(response["deploymentEligible"])
            self.assertFalse(any("path" in key.lower() for key in flatten_keys(response)))

    def test_routes_enforce_contract_size_and_safe_errors(self):
        forbidden = feature_payload()
        forbidden["coils"][0]["features"]["judge"] = "불량"
        status, contract_error = self.exchange(
            "POST", "/v1/features", payload=forbidden
        )
        self.assertEqual(status, 400)
        self.assertEqual(contract_error["code"], "FEATURE_CONTRACT_INVALID")
        self.assertEqual(contract_error["requestId"], "hr-20260901-a-001")

        status, size_error = self.exchange(
            "POST",
            "/v1/features",
            body=b"{" + b"x" * 1024,
            content_type="application/json",
        )
        self.assertEqual(status, 413)
        self.assertEqual(size_error["code"], "REQUEST_SCHEMA_INVALID")

        status, type_error = self.exchange(
            "POST", "/v1/features", body=b"{}", content_type="text/plain"
        )
        self.assertEqual(status, 400)
        self.assertEqual(type_error["code"], "REQUEST_SCHEMA_INVALID")

        status, missing = self.exchange("GET", "/v1/predictions/UNKNOWN")
        self.assertEqual(status, 404)
        self.assertEqual(missing["code"], "COIL_NOT_FOUND")

        for response in (contract_error, size_error, type_error, missing):
            self.assertEqual(response["schemaVersion"], "steel-shadow-error-v1")
            self.assertNotIn(str(self.root), response["message"])

    def test_ledger_corruption_fails_closed_without_exposing_paths(self):
        status, _ = self.exchange("POST", "/v1/features", payload=feature_payload())
        self.assertEqual(status, 200)
        prediction_path = next(
            (self.output_dir / "prediction_batches").glob("*.predictions.csv")
        )
        prediction_path.write_bytes(prediction_path.read_bytes() + b"\n")

        status, response = self.exchange("GET", "/v1/predictions/H1")

        self.assertEqual(status, 409)
        self.assertEqual(response["code"], "LEDGER_INTEGRITY_REVIEW_REQUIRED")
        self.assertFalse(response["retryable"])
        self.assertNotIn(str(self.root), response["message"])

    def test_health_revalidates_registry_and_returns_unavailable(self):
        self.registry_path.write_text("{}", encoding="utf-8")

        status, response = self.exchange("GET", "/health")

        self.assertEqual(status, 503)
        self.assertEqual(response["code"], "SHADOW_UNAVAILABLE")
        self.assertTrue(response["retryable"])
        self.assertNotIn(str(self.root), response["message"])

    def test_stalled_and_prebody_rejections_close_the_connection(self):
        stalled = self.raw_exchange(
            b"POST /v1/features HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 100\r\n\r\n{" 
        )
        self.assertIn(b" 408 ", stalled.split(b"\r\n", 1)[0])
        self.assertIn(b"Connection: close", stalled)

        pipelined = self.raw_exchange(
            b"POST /v1/features HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 2\r\n\r\n{}"
            b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
        )
        self.assertEqual(pipelined.count(b"HTTP/1.1"), 1)
        self.assertIn(b"Connection: close", pipelined)

    def test_all_operations_fail_unavailable_when_registry_becomes_invalid(self):
        self.registry_path.write_text("{}", encoding="utf-8")

        requests = [
            ("GET", "/v1/status", None),
            ("GET", "/v1/predictions/H1", None),
            ("POST", "/v1/features", feature_payload()),
            ("POST", "/v1/labels", label_payload()),
        ]
        for method, path, payload in requests:
            with self.subTest(method=method, path=path):
                status, response = self.exchange(method, path, payload=payload)
                self.assertEqual(status, 503)
                self.assertEqual(response["code"], "SHADOW_UNAVAILABLE")
                self.assertTrue(response["retryable"])

        self.assertFalse((self.output_dir / "label_batches").exists())

    def test_all_operations_fail_unavailable_when_registry_artifact_schema_breaks(self):
        runner = importlib.import_module("training.steel.run_shadow")
        registry = json.loads(self.registry_path.read_text(encoding="utf-8-sig"))
        capacity_path = Path(registry["challenger_capacity_path"])
        capacity_path.write_text("wrong_column\nvalue\n", encoding="utf-8-sig")
        registry["challenger_capacity_sha256"] = runner.sha256_file(capacity_path)
        self.registry_path.write_text(json.dumps(registry), encoding="utf-8")

        requests = [
            ("GET", "/health", None),
            ("GET", "/v1/status", None),
            ("GET", "/v1/predictions/H1", None),
            ("POST", "/v1/features", feature_payload()),
            ("POST", "/v1/labels", label_payload()),
        ]
        for method, path, payload in requests:
            with self.subTest(method=method, path=path):
                status, response = self.exchange(method, path, payload=payload)
                self.assertEqual(status, 503)
                self.assertEqual(response["code"], "SHADOW_UNAVAILABLE")
                self.assertTrue(response["retryable"])

        self.assertFalse((self.output_dir / "prediction_batches").exists())
        self.assertFalse((self.output_dir / "label_batches").exists())

    def test_partially_missing_ledger_returns_manual_review_conflict(self):
        status, _ = self.exchange("POST", "/v1/features", payload=feature_payload())
        self.assertEqual(status, 200)
        prediction_path = next(
            (self.output_dir / "prediction_batches").glob("*.predictions.csv")
        )
        prediction_path.unlink()

        status, response = self.exchange("GET", "/v1/predictions/H1")

        self.assertEqual(status, 409)
        self.assertEqual(response["code"], "LEDGER_INTEGRITY_REVIEW_REQUIRED")
        self.assertFalse(response["retryable"])

    def test_malformed_metadata_type_returns_manual_review_conflict(self):
        status, _ = self.exchange("POST", "/v1/features", payload=feature_payload())
        self.assertEqual(status, 200)
        metadata_path = next(
            (self.output_dir / "prediction_batches").glob("*.metadata.json")
        )
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["coil_rows"] = None
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        status, response = self.exchange("GET", "/v1/predictions/H1")

        self.assertEqual(status, 409)
        self.assertEqual(response["code"], "LEDGER_INTEGRITY_REVIEW_REQUIRED")

    def test_invalid_label_supersession_is_a_contract_error(self):
        status, _ = self.exchange("POST", "/v1/features", payload=feature_payload())
        self.assertEqual(status, 200)
        self.clock.current = pd.Timestamp("2026-09-01T03:00:00Z")
        status, first = self.exchange("POST", "/v1/labels", payload=label_payload())
        self.assertEqual(status, 200)
        correction = label_payload()
        correction["requestId"] = "ap-20260901-correction-001"
        correction["supersedesLabelBatchId"] = "NOT-A-BATCH"
        correction["labels"][0]["judge"] = "양품"
        correction["labels"][0]["labelFinalizedAt"] = "2026-09-01T03:30:00Z"
        self.clock.current = pd.Timestamp("2026-09-01T04:00:00Z")

        status, response = self.exchange("POST", "/v1/labels", payload=correction)

        self.assertEqual(status, 400)
        self.assertEqual(response["code"], "LABEL_CONTRACT_INVALID")
        self.assertEqual(
            len(list((self.output_dir / "label_batches").glob("*.metadata.json"))),
            1,
        )
        self.assertNotEqual(response.get("batchId"), first["batchId"])

    def test_unsupported_method_uses_safe_json_error(self):
        status, response = self.exchange("PUT", "/v1/status", body=b"")

        self.assertEqual(status, 405)
        self.assertEqual(response["schemaVersion"], "steel-shadow-error-v1")
        self.assertEqual(response["code"], "REQUEST_SCHEMA_INVALID")

    def test_malformed_request_line_returns_safe_json_error(self):
        response = self.raw_exchange(b"GARBAGE\r\n\r\n", shutdown_write=True)

        self.assertIn(b" 400 ", response.split(b"\r\n", 1)[0])
        self.assertIn(b"Content-Type: application/json", response)
        payload = json.loads(response.split(b"\r\n\r\n", 1)[1].decode("utf-8"))
        self.assertEqual(payload["code"], "REQUEST_SCHEMA_INVALID")

    def test_excessively_nested_json_is_a_contract_error(self):
        body = b"[" * 65 + b"0" + b"]" * 65

        status, response = self.exchange(
            "POST",
            "/v1/features",
            body=body,
            content_type="application/json",
        )

        self.assertEqual(status, 400)
        self.assertEqual(response["code"], "REQUEST_SCHEMA_INVALID")

    def test_unknown_and_early_labels_are_rejected_before_commit(self):
        unknown_payload = label_payload()
        unknown_payload["labels"][0]["labelFinalizedAt"] = "2026-09-01T01:30:00Z"
        status, unknown = self.exchange(
            "POST", "/v1/labels", payload=unknown_payload
        )
        self.assertEqual(status, 400)
        self.assertEqual(unknown["code"], "LABEL_CONTRACT_INVALID")
        self.assertFalse((self.output_dir / "label_batches").exists())

        status, _ = self.exchange("POST", "/v1/features", payload=feature_payload())
        self.assertEqual(status, 200)
        early = label_payload()
        early["labels"][0]["labelFinalizedAt"] = "2026-09-01T01:30:00Z"
        status, response = self.exchange("POST", "/v1/labels", payload=early)

        self.assertEqual(status, 400)
        self.assertEqual(response["code"], "LABEL_CONTRACT_INVALID")
        self.assertFalse((self.output_dir / "label_batches").exists())

        self.clock.current = pd.Timestamp("2026-09-01T03:00:00Z")
        mixed = label_payload()
        mixed["labels"].append(
            {
                "hrCoilId": "H2",
                "judge": "양품",
                "labelFinalizedAt": "2026-09-01T02:30:00Z",
            }
        )
        status, response = self.exchange("POST", "/v1/labels", payload=mixed)
        self.assertEqual(status, 400)
        self.assertEqual(response["code"], "LABEL_CONTRACT_INVALID")
        self.assertFalse((self.output_dir / "label_batches").exists())

    def test_corrupt_ledger_blocks_new_feature_before_commit(self):
        status, _ = self.exchange("POST", "/v1/features", payload=feature_payload())
        self.assertEqual(status, 200)
        prediction_path = next(
            (self.output_dir / "prediction_batches").glob("*.predictions.csv")
        )
        prediction_path.write_bytes(prediction_path.read_bytes() + b"\n")
        second = feature_payload()
        second["requestId"] = "hr-20260901-a-002"
        second["coils"][0]["chargeId"] = "C2"
        second["coils"][0]["hrCoilId"] = "H2"

        status, response = self.exchange("POST", "/v1/features", payload=second)

        self.assertEqual(status, 409)
        self.assertEqual(response["code"], "LEDGER_INTEGRITY_REVIEW_REQUIRED")
        self.assertEqual(
            len(list((self.output_dir / "prediction_batches").glob("*.metadata.json"))),
            1,
        )

    def test_corrupt_label_history_blocks_new_feature_before_commit(self):
        status, _ = self.exchange("POST", "/v1/features", payload=feature_payload())
        self.assertEqual(status, 200)
        self.clock.current = pd.Timestamp("2026-09-01T03:00:00Z")
        status, _ = self.exchange("POST", "/v1/labels", payload=label_payload())
        self.assertEqual(status, 200)
        labels_path = next((self.output_dir / "label_batches").glob("*.labels.csv"))
        labels_path.write_bytes(labels_path.read_bytes() + b"\n")
        second = feature_payload()
        second["requestId"] = "hr-20260901-a-002"
        second["coils"][0]["chargeId"] = "C2"
        second["coils"][0]["hrCoilId"] = "H2"

        status, response = self.exchange("POST", "/v1/features", payload=second)

        self.assertEqual(status, 409)
        self.assertEqual(response["code"], "LEDGER_INTEGRITY_REVIEW_REQUIRED")
        self.assertEqual(
            len(list((self.output_dir / "prediction_batches").glob("*.metadata.json"))),
            1,
        )


class ShadowSmokeRoutingTest(unittest.TestCase):
    def test_uses_distinct_sidecar_and_sfep_route_shapes(self):
        subject = load_smoke_script()

        self.assertEqual(
            subject.route_urls("http://127.0.0.1:18081", sfep_adapter=False),
            (
                "http://127.0.0.1:18081/v1/features",
                "http://127.0.0.1:18081/v1/labels",
            ),
        )
        self.assertEqual(
            subject.route_urls(
                "http://127.0.0.1:18080/api/steel-shadow/", sfep_adapter=True
            ),
            (
                "http://127.0.0.1:18080/api/steel-shadow/features",
                "http://127.0.0.1:18080/api/steel-shadow/labels",
            ),
        )

    def test_fixture_matches_the_production_three_role_contract(self):
        subject = load_smoke_script()
        runner = importlib.import_module("training.steel.run_shadow")

        with subject.fixture_environment() as fixture:
            registry = runner.load_and_validate_registry(fixture.registry_path)
            roles = [model["role"] for model in registry["models"]]
            supplied = set(fixture.features["coils"][0]["features"])

        self.assertEqual(
            roles,
            ["reference_baseline", "shadow_incumbent", "next_challenger"],
        )
        self.assertEqual(fixture.model_count, 3)
        self.assertEqual(len(supplied), 30)
        self.assertFalse(supplied & set(runner.ENGINEERED_FEATURES))

    def test_first_label_with_three_models_remains_collecting(self):
        subject = load_smoke_script()
        api = load_subject()

        with subject.fixture_environment() as fixture:
            application = api.ShadowApiApplication(
                registry_path=fixture.registry_path,
                output_dir=fixture.output_dir,
                max_coils=10_000,
            )
            score = application.handle_features(fixture.features)
            labels = application.handle_labels(fixture.labels())

            self.assertEqual(len(score["predictions"]), 3)
            self.assertEqual(labels["shadowStatus"], "COLLECTING")
            self.assertEqual(
                len(list((fixture.output_dir / "label_batches").glob("*.metadata.json"))),
                1,
            )


class ShadowApiConfigurationTest(unittest.TestCase):
    def test_ipv6_literal_is_rejected_until_server_supports_ipv6(self):
        subject = load_subject()

        with self.assertRaisesRegex(ValueError, "loopback"):
            subject.ShadowApiConfig(
                host="::1",
                port=0,
                registry_path=Path("registry.json"),
                output_dir=Path("shadow"),
            )

    def test_hard_limits_and_duplicate_json_keys_are_rejected(self):
        subject = load_subject()
        with self.assertRaisesRegex(ValueError, "10 MiB"):
            subject.ShadowApiConfig(
                host="127.0.0.1",
                port=0,
                registry_path=Path("registry.json"),
                output_dir=Path("shadow"),
                max_request_bytes=10 * 1024 * 1024 + 1,
            )
        with self.assertRaisesRegex(ValueError, "10,000"):
            subject.ShadowApiConfig(
                host="127.0.0.1",
                port=0,
                registry_path=Path("registry.json"),
                output_dir=Path("shadow"),
                max_coils=10_001,
            )
        with self.assertRaisesRegex(ValueError, "duplicate JSON field"):
            subject._strict_json_loads(b'{"requestId":"one","requestId":"one"}')
        with self.assertRaisesRegex(ValueError, "nesting depth"):
            subject._strict_json_loads(b"[" * 65 + b"0" + b"]" * 65)


if __name__ == "__main__":
    unittest.main()
