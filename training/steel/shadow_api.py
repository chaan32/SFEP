from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import tempfile
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import unquote, urlsplit

import pandas as pd

from training.steel import run_shadow
from training.steel.shadow_api_contract import (
    ShadowApiContractError,
    canonical_csv_bytes,
    parse_feature_request,
    parse_label_request,
)


SERVICE_VERSION = "steel-shadow-http-sidecar-v0.1"
MAX_REQUEST_BYTES = 10 * 1024 * 1024
MAX_COILS = 10_000
MAX_JSON_NESTING_DEPTH = 64


@dataclass(frozen=True)
class ShadowApiConfig:
    host: str
    port: int
    registry_path: Path
    output_dir: Path
    max_request_bytes: int = MAX_REQUEST_BYTES
    max_coils: int = MAX_COILS
    request_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost"}:
            raise ValueError("Shadow sidecar host must be a loopback address")
        if not 0 <= self.port <= 65_535:
            raise ValueError("port must be between 0 and 65535")
        if self.max_request_bytes < 1:
            raise ValueError("max_request_bytes must be positive")
        if self.max_request_bytes > MAX_REQUEST_BYTES:
            raise ValueError("max_request_bytes must not exceed 10 MiB")
        if self.max_coils < 1:
            raise ValueError("max_coils must be positive")
        if self.max_coils > MAX_COILS:
            raise ValueError("max_coils must not exceed 10,000")
        if not 0.1 <= float(self.request_timeout_seconds) <= 120.0:
            raise ValueError("request_timeout_seconds must be between 0.1 and 120")


class ShadowApiError(RuntimeError):
    def __init__(
        self,
        http_status: int,
        code: str,
        message: str,
        *,
        retryable: bool,
    ):
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.retryable = retryable


def _status_name(summary: dict[str, object]) -> str:
    if "status" in summary:
        return str(summary["status"])
    gate = summary.get("gate")
    if isinstance(gate, dict) and "status" in gate:
        return str(gate["status"])
    raise ValueError("Shadow evaluation response is missing status")


def _prediction_view(row: dict[str, object]) -> dict[str, object]:
    return {
        "hrCoilId": row["hr_coil_id"],
        "modelRole": row["model_role"],
        "modelId": row["model_id"],
        "riskScore": row["risk_score"],
        "calibratedProbability": row["calibrated_probability"],
        "policyThreshold": row["policy_threshold"],
        "predictedLabel": row["predicted_label"],
    }


class ShadowApiApplication:
    def __init__(
        self,
        *,
        registry_path: Path,
        output_dir: Path,
        max_coils: int,
        now: Callable[[], pd.Timestamp] = run_shadow._utc_now,
    ):
        if max_coils < 1:
            raise ValueError("max_coils must be positive")
        self.registry_path = Path(registry_path)
        self.output_dir = Path(output_dir)
        self.max_coils = int(max_coils)
        self.now = now
        self._lock = threading.RLock()
        self._validated_registry()

    def _validated_registry(self) -> dict[str, object]:
        try:
            return run_shadow._load_runtime_registry(self.registry_path)
        except run_shadow.RegistryUnavailableError as exc:
            raise ShadowApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "SHADOW_UNAVAILABLE",
                "Shadow registry or model service is unavailable",
                retryable=True,
            ) from exc

    def _temporary_csv(self, frame: pd.DataFrame):
        temporary = tempfile.TemporaryDirectory(prefix="steel-shadow-http-")
        path = Path(temporary.name) / "canonical.csv"
        path.write_bytes(canonical_csv_bytes(frame))
        return temporary, path

    def handle_features(self, payload: object) -> dict[str, object]:
        with self._lock:
            registry = self._validated_registry()
            request_id, frame = parse_feature_request(
                payload,
                registry,
                max_coils=self.max_coils,
            )
            temporary, source_path = self._temporary_csv(frame)
            try:
                committed = run_shadow.score_feature_batch(
                    source_path,
                    self.registry_path,
                    self.output_dir,
                    now=self.now,
                )
            finally:
                temporary.cleanup()
            predictions = run_shadow.read_shadow_predictions(
                self.registry_path,
                self.output_dir,
                batch_id=str(committed["batch_id"]),
                now=self.now,
            )
            summary = run_shadow.evaluate_shadow(
                self.registry_path,
                self.output_dir,
                now=self.now,
            )
            return {
                "schemaVersion": "steel-shadow-score-response-v1",
                "requestId": request_id,
                "batchId": committed["batch_id"],
                "evidenceStatus": committed["evidence_status"],
                "coilCount": int(committed["coil_rows"]),
                "predictions": [_prediction_view(row) for row in predictions],
                "shadowStatus": _status_name(summary),
                "deploymentEligible": False,
            }

    def handle_labels(self, payload: object) -> dict[str, object]:
        with self._lock:
            self._validated_registry()
            request_id, supersedes, frame = parse_label_request(
                payload,
                max_coils=self.max_coils,
            )
            temporary, source_path = self._temporary_csv(frame)
            try:
                committed = run_shadow.ingest_label_batch(
                    source_path,
                    self.output_dir,
                    supersedes_label_batch_id=supersedes,
                    registry_path=self.registry_path,
                    now=self.now,
                )
            finally:
                temporary.cleanup()
            summary = run_shadow.evaluate_shadow(
                self.registry_path,
                self.output_dir,
                now=self.now,
            )
            return {
                "schemaVersion": "steel-shadow-label-response-v1",
                "requestId": request_id,
                "batchId": committed["batch_id"],
                "labelCount": int(committed["label_rows"]),
                "positiveLabels": int(committed["positive_labels"]),
                "shadowStatus": _status_name(summary),
                "deploymentEligible": False,
            }

    def status(self) -> dict[str, object]:
        with self._lock:
            self._validated_registry()
            summary = run_shadow.evaluate_shadow(
                self.registry_path,
                self.output_dir,
                now=self.now,
            )
            observation = summary.get("observation", {})
            gate = summary.get("gate", {})
            if not isinstance(observation, dict) or not isinstance(gate, dict):
                raise ValueError("Shadow evaluation response has invalid status fields")
            return {
                "schemaVersion": "steel-shadow-status-response-v1",
                "status": _status_name(summary),
                "evidenceStatus": summary.get(
                    "evidence_status", run_shadow.INDEPENDENT_EVIDENCE_STATUS
                ),
                "predictionBatches": int(
                    summary.get(
                        "prediction_batches",
                        len(
                            list(
                                (self.output_dir / "prediction_batches").glob(
                                    "*.metadata.json"
                                )
                            )
                        ),
                    )
                ),
                "labelBatches": int(
                    summary.get(
                        "label_batches",
                        len(
                            list(
                                (self.output_dir / "label_batches").glob(
                                    "*.metadata.json"
                                )
                            )
                        ),
                    )
                ),
                "days": int(observation.get("days", 0)),
                "labeledCoils": int(observation.get("labeled_coils", 0)),
                "positiveLabels": int(observation.get("positive_labels", 0)),
                "failedChecks": list(gate.get("failed_checks", [])),
                "deploymentEligible": False,
            }

    def predictions(self, hr_coil_id: str) -> dict[str, object]:
        with self._lock:
            self._validated_registry()
            if not isinstance(hr_coil_id, str) or not hr_coil_id.strip():
                raise ShadowApiError(
                    404,
                    "COIL_NOT_FOUND",
                    "no committed prediction exists for this Coil",
                    retryable=False,
                )
            rows = run_shadow.read_shadow_predictions(
                self.registry_path,
                self.output_dir,
                hr_coil_id=hr_coil_id.strip(),
                now=self.now,
            )
            if not rows:
                raise ShadowApiError(
                    404,
                    "COIL_NOT_FOUND",
                    "no committed prediction exists for this Coil",
                    retryable=False,
                )
            batch_ids = {str(row["batch_id"]) for row in rows}
            if len(batch_ids) != 1:
                raise ValueError("validated Coil predictions span multiple batches")
            return {
                "schemaVersion": "steel-shadow-prediction-response-v1",
                "hrCoilId": hr_coil_id.strip(),
                "batchId": next(iter(batch_ids)),
                "predictions": [_prediction_view(row) for row in rows],
                "deploymentEligible": False,
            }

    def health(self) -> dict[str, object]:
        with self._lock:
            registry = self._validated_registry()
            return {
                "schemaVersion": "steel-shadow-health-response-v1",
                "status": "UP",
                "serviceVersion": SERVICE_VERSION,
                "registrySha256": run_shadow.sha256_file(self.registry_path),
                "models": [
                    {
                        "modelId": model["model_id"],
                        "modelRole": model["role"],
                    }
                    for model in registry["models"]
                ],
                "deploymentEligible": False,
            }


def _safe_request_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("requestId")
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        return None
    if any(ord(character) < 32 or ord(character) > 126 for character in value):
        return None
    return value


def _strict_json_loads(raw: bytes) -> object:
    depth = 0
    in_string = False
    escaped = False
    for value in raw:
        if in_string:
            if escaped:
                escaped = False
            elif value == ord("\\"):
                escaped = True
            elif value == ord('"'):
                in_string = False
            continue
        if value == ord('"'):
            in_string = True
        elif value in (ord("["), ord("{")):
            depth += 1
            if depth > MAX_JSON_NESTING_DEPTH:
                raise ValueError(
                    f"JSON nesting depth must not exceed {MAX_JSON_NESTING_DEPTH}"
                )
        elif value in (ord("]"), ord("}")):
            depth -= 1

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    return json.loads(
        raw.decode("utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=unique_object,
    )


class _ShadowRequestHandler(BaseHTTPRequestHandler):
    application: ShadowApiApplication
    config: ShadowApiConfig
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(float(self.config.request_timeout_seconds))

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        del message, explain
        self._operation = "unsupported"
        self._request_started_at = time.perf_counter()
        self.close_connection = True
        if getattr(self, "request_version", "HTTP/0.9") == "HTTP/0.9":
            self.request_version = self.protocol_version
        status = (
            HTTPStatus.METHOD_NOT_ALLOWED
            if int(code) == HTTPStatus.NOT_IMPLEMENTED
            else int(code)
        )
        self._write_error(
            status,
            "REQUEST_SCHEMA_INVALID",
            "HTTP method or request is not supported",
            retryable=False,
        )

    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        self._response_payload = payload
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def _write_error(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool,
        request_id: str | None = None,
    ) -> None:
        self._write_json(
            status,
            {
                "schemaVersion": "steel-shadow-error-v1",
                "requestId": request_id,
                "code": code,
                "message": message,
                "retryable": retryable,
            },
        )

    def _read_payload(self) -> object:
        if self.headers.get("Transfer-Encoding") is not None:
            self.close_connection = True
            raise ShadowApiContractError(
                "REQUEST_SCHEMA_INVALID", "Transfer-Encoding is not supported"
            )
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if media_type.lower() != "application/json":
            self.close_connection = True
            raise ShadowApiContractError(
                "REQUEST_SCHEMA_INVALID", "Content-Type must be application/json"
            )
        length_values = self.headers.get_all("Content-Length", [])
        if len(length_values) != 1:
            self.close_connection = True
            raise ShadowApiContractError(
                "REQUEST_SCHEMA_INVALID", "one Content-Length header is required"
            )
        length_value = length_values[0]
        try:
            length = int(length_value)
        except ValueError as exc:
            self.close_connection = True
            raise ShadowApiContractError(
                "REQUEST_SCHEMA_INVALID", "Content-Length must be a non-negative integer"
            ) from exc
        if length < 0:
            self.close_connection = True
            raise ShadowApiContractError(
                "REQUEST_SCHEMA_INVALID", "Content-Length must be a non-negative integer"
            )
        if length > self.config.max_request_bytes:
            self.close_connection = True
            raise ShadowApiError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "REQUEST_SCHEMA_INVALID",
                "request body exceeds the configured byte limit",
                retryable=False,
            )
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ShadowApiContractError(
                "REQUEST_SCHEMA_INVALID", "request body ended before Content-Length"
            )
        try:
            return _strict_json_loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise ShadowApiContractError(
                "REQUEST_SCHEMA_INVALID", "request body must be strict UTF-8 JSON"
            ) from exc

    def _dispatch(self, operation: str, action: Callable[[], dict[str, object]]) -> None:
        self._operation = operation
        self._request_started_at = time.perf_counter()
        request_id: str | None = None
        try:
            if self.command == "POST":
                payload = self._read_payload()
                request_id = _safe_request_id(payload)
                response = action(payload)  # type: ignore[misc]
            else:
                response = action()
            self._write_json(HTTPStatus.OK, response)
        except ShadowApiContractError as exc:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                exc.code,
                str(exc),
                retryable=False,
                request_id=request_id,
            )
        except ShadowApiError as exc:
            self._write_error(
                exc.http_status,
                exc.code,
                str(exc),
                retryable=exc.retryable,
                request_id=request_id,
            )
        except socket.timeout:
            self.close_connection = True
            self._write_error(
                HTTPStatus.REQUEST_TIMEOUT,
                "REQUEST_SCHEMA_INVALID",
                "request body was not received before the timeout",
                retryable=False,
                request_id=request_id,
            )
        except run_shadow.RegistryUnavailableError:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "SHADOW_UNAVAILABLE",
                "Shadow registry or model service is unavailable",
                retryable=True,
                request_id=request_id,
            )
        except run_shadow.LedgerIntegrityError:
            self._write_error(
                HTTPStatus.CONFLICT,
                "LEDGER_INTEGRITY_REVIEW_REQUIRED",
                "Shadow ledger integrity requires manual review",
                retryable=False,
                request_id=request_id,
            )
        except run_shadow.CoilAlreadyScoredError:
            self._write_error(
                HTTPStatus.CONFLICT,
                "COIL_ALREADY_SCORED_DIFFERENTLY",
                "one or more Coils were already scored differently",
                retryable=False,
                request_id=request_id,
            )
        except run_shadow.FeatureBatchError:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "FEATURE_CONTRACT_INVALID",
                "feature batch failed Shadow validation",
                retryable=False,
                request_id=request_id,
            )
        except run_shadow.LabelBatchError:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "LABEL_CONTRACT_INVALID",
                "label batch failed Shadow validation",
                retryable=False,
                request_id=request_id,
            )
        except (FileNotFoundError, OSError):
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "SHADOW_UNAVAILABLE",
                "Shadow registry or model service is unavailable",
                retryable=True,
                request_id=request_id,
            )
        except ValueError:
            if operation == "health":
                self._write_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "SHADOW_UNAVAILABLE",
                    "Shadow registry or model service is unavailable",
                    retryable=True,
                    request_id=request_id,
                )
            else:
                self._write_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "SHADOW_INTERNAL_ERROR",
                    "unexpected Shadow service failure",
                    retryable=False,
                    request_id=request_id,
                )
        except RuntimeError as exc:
            message = str(exc).lower()
            if "ledger" in message or "partially written" in message:
                self._write_error(
                    HTTPStatus.CONFLICT,
                    "LEDGER_INTEGRITY_REVIEW_REQUIRED",
                    "Shadow ledger integrity requires manual review",
                    retryable=False,
                    request_id=request_id,
                )
            else:
                self._write_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "SHADOW_INTERNAL_ERROR",
                    "unexpected Shadow service failure",
                    retryable=False,
                    request_id=request_id,
                )
        except Exception:
            self._write_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "SHADOW_INTERNAL_ERROR",
                "unexpected Shadow service failure",
                retryable=False,
                request_id=request_id,
            )

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/v1/features":
            self._dispatch("features", self.application.handle_features)
        elif path == "/v1/labels":
            self._dispatch("labels", self.application.handle_labels)
        else:
            self._operation = "unknown"
            self._request_started_at = time.perf_counter()
            self.close_connection = True
            self._write_error(
                HTTPStatus.NOT_FOUND,
                "ROUTE_NOT_FOUND",
                "route not found",
                retryable=False,
            )

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            self._dispatch("health", self.application.health)
        elif path == "/v1/status":
            self._dispatch("status", self.application.status)
        elif path.startswith("/v1/predictions/"):
            encoded = path.removeprefix("/v1/predictions/")
            coil_id = unquote(encoded)
            self._dispatch(
                "predictions", lambda: self.application.predictions(coil_id)
            )
        else:
            self._operation = "unknown"
            self._request_started_at = time.perf_counter()
            self._write_error(
                HTTPStatus.NOT_FOUND,
                "ROUTE_NOT_FOUND",
                "route not found",
                retryable=False,
            )

    def log_message(self, format: str, *args: object) -> None:
        status = args[1] if len(args) > 1 else None
        payload = getattr(self, "_response_payload", {})
        if not isinstance(payload, dict):
            payload = {}
        started_at = getattr(self, "_request_started_at", None)
        latency_ms = (
            None
            if started_at is None
            else round((time.perf_counter() - float(started_at)) * 1000.0, 3)
        )
        event = {
            "event": "steel_shadow_http_access",
            "operation": getattr(self, "_operation", "unknown"),
            "method": getattr(self, "command", None),
            "path": urlsplit(str(getattr(self, "path", ""))).path,
            "status": status,
            "outcome": payload.get("code", "SUCCESS"),
            "requestId": payload.get("requestId"),
            "batchId": payload.get("batchId"),
            "coilCount": payload.get("coilCount", payload.get("labelCount")),
            "latencyMs": latency_ms,
        }
        sys.stderr.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def build_server(
    config: ShadowApiConfig,
    *,
    now: Callable[[], pd.Timestamp] = run_shadow._utc_now,
) -> ThreadingHTTPServer:
    application = ShadowApiApplication(
        registry_path=config.registry_path,
        output_dir=config.output_dir,
        max_coils=config.max_coils,
        now=now,
    )

    class Handler(_ShadowRequestHandler):
        pass

    Handler.application = application
    Handler.config = config
    server = ThreadingHTTPServer((config.host, config.port), Handler)
    server.daemon_threads = True
    return server


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local SFEP Steel Shadow HTTP sidecar")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-request-bytes", type=int, default=10 * 1024 * 1024)
    parser.add_argument("--max-coils", type=int, default=10_000)
    parser.add_argument("--request-timeout-seconds", type=float, default=10.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    config = ShadowApiConfig(
        host=args.host,
        port=args.port,
        registry_path=args.registry,
        output_dir=args.output_dir,
        max_request_bytes=args.max_request_bytes,
        max_coils=args.max_coils,
        request_timeout_seconds=args.request_timeout_seconds,
    )
    server = build_server(config)
    started = {
        "event": "steel_shadow_sidecar_started",
        "host": config.host,
        "port": server.server_address[1],
        "serviceVersion": SERVICE_VERSION,
        "deploymentEligible": False,
    }
    sys.stderr.write(json.dumps(started, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
