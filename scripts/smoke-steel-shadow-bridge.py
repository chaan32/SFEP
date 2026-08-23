#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from urllib import error, request

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.steel import run_shadow


@dataclass(frozen=True)
class SmokeFixture:
    root: Path
    registry_path: Path
    output_dir: Path
    features: dict[str, object]
    model_count: int

    def labels(self) -> dict[str, object]:
        finalized_at = datetime.now(timezone.utc)
        return {
            "schemaVersion": "steel-shadow-label-batch-v1",
            "requestId": "smoke-label-001",
            "supersedesLabelBatchId": None,
            "labels": [
                {
                    "hrCoilId": "SMOKE-H1",
                    "judge": "불량",
                    "labelFinalizedAt": finalized_at.isoformat(),
                }
            ],
        }


@contextmanager
def fixture_environment() -> Iterator[SmokeFixture]:
    with tempfile.TemporaryDirectory(prefix="steel-shadow-smoke-") as temp_dir:
        root = Path(temp_dir)
        template_path = PROJECT_ROOT / "training/steel/shadow_registry/registry.json"
        registry = run_shadow.load_and_validate_registry(template_path)
        registry_path = root / "registry.json"
        output_dir = root / "shadow_output"
        registry_time = pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=5)
        registry = json.loads(json.dumps(registry, ensure_ascii=False))
        registry["created_at"] = registry_time.isoformat()
        registry["shadow_start_at"] = registry_time.isoformat()
        run_shadow.atomic_write_json(registry, registry_path)
        registry = run_shadow.load_and_validate_registry(registry_path)
        base_features: list[str] = []
        for model in registry["models"]:
            for feature in model["feature_schema"]["included"]:
                if (
                    feature not in run_shadow.ENGINEERED_FEATURES
                    and feature not in base_features
                ):
                    base_features.append(str(feature))
        reference = pd.read_csv(
            Path(str(registry["reference_cohort_path"])), encoding="utf-8-sig"
        )
        complete = reference.dropna(subset=base_features)
        if complete.empty:
            raise RuntimeError("reference cohort has no complete production-shaped row")
        source = complete.iloc[0]

        def json_value(value: object) -> object:
            if pd.isna(value):
                return None
            if hasattr(value, "item"):
                return value.item()
            return value

        available_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        features = {
            "schemaVersion": "steel-shadow-feature-batch-v1",
            "requestId": "smoke-feature-001",
            "coils": [
                {
                    "chargeId": "SMOKE-C1",
                    "slabNo": "1",
                    "hrCoilId": "SMOKE-H1",
                    "hrDate": available_at.date().isoformat(),
                    "featureAvailableAt": available_at.isoformat(),
                    "features": {
                        feature: json_value(source[feature])
                        for feature in base_features
                    },
                }
            ],
        }
        yield SmokeFixture(
            root=root,
            registry_path=registry_path,
            output_dir=output_dir,
            features=features,
            model_count=len(registry["models"]),
        )


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def get_json(url: str) -> dict[str, object]:
    with request.urlopen(url, timeout=10) as response:
        if response.status != 200:
            raise AssertionError(f"GET returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    http_request = request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=120) as response:
        if response.status != 200:
            raise AssertionError(f"POST returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def start_sidecar(
    fixture: SmokeFixture, *, port: int | None = None
) -> tuple[subprocess.Popen[str], str]:
    port = _available_port() if port is None else port
    command = [
        sys.executable,
        "-m",
        "training.steel.shadow_api",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--registry",
        str(fixture.registry_path),
        "--output-dir",
        str(fixture.output_dir),
    ]
    environment = os.environ.copy()
    environment["LOKY_MAX_CPU_COUNT"] = "1"
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise RuntimeError(f"sidecar exited during startup: {stderr[-500:]}")
        try:
            health = get_json(base_url + "/health")
            if health.get("status") == "UP":
                return process, base_url
        except (error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.05)
    process.terminate()
    process.wait(timeout=10)
    raise TimeoutError("sidecar did not become healthy within 20 seconds")


def assert_committed_file_counts(
    output_dir: Path, *, predictions: int, labels: int
) -> None:
    prediction_count = len(
        list((output_dir / "prediction_batches").glob("*.metadata.json"))
    )
    label_count = len(list((output_dir / "label_batches").glob("*.metadata.json")))
    if prediction_count != predictions or label_count != labels:
        raise AssertionError(
            f"unexpected committed Batch counts: predictions={prediction_count}, "
            f"labels={label_count}"
        )


def route_urls(base_url: str, *, sfep_adapter: bool) -> tuple[str, str]:
    root = base_url.rstrip("/")
    prefix = "" if sfep_adapter else "/v1"
    return root + prefix + "/features", root + prefix + "/labels"


def _verify_route(
    base_url: str,
    fixture: SmokeFixture,
    *,
    sfep_adapter: bool,
    labels: dict[str, object] | None = None,
) -> tuple[dict[str, str], dict[str, object]]:
    feature_url, label_url = route_urls(base_url, sfep_adapter=sfep_adapter)
    first_score = post_json(feature_url, fixture.features)
    second_score = post_json(feature_url, fixture.features)
    if first_score["batchId"] != second_score["batchId"]:
        raise AssertionError("feature retry changed batch ID")
    if first_score["deploymentEligible"] is not False:
        raise AssertionError("Shadow response became deployment eligible")
    if len(first_score["predictions"]) != fixture.model_count:
        raise AssertionError("not every registered model produced a prediction")

    label_payload = fixture.labels() if labels is None else labels
    first_label = post_json(label_url, label_payload)
    second_label = post_json(label_url, label_payload)
    if first_label["batchId"] != second_label["batchId"]:
        raise AssertionError("label retry changed batch ID")
    if first_label["shadowStatus"] != "COLLECTING":
        raise AssertionError("finalized label did not update collection state")
    return (
        {
            "scoreBatchId": str(first_score["batchId"]),
            "labelBatchId": str(first_label["batchId"]),
        },
        label_payload,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test the Steel Shadow HTTP bridge")
    parser.add_argument(
        "--sfep-base-url",
        help="Optional enabled SFEP adapter base, such as http://127.0.0.1:18080/api/steel-shadow",
    )
    parser.add_argument(
        "--sidecar-port",
        type=int,
        help="Sidecar port; defaults to an ephemeral port, or 18081 with --sfep-base-url",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    with fixture_environment() as fixture:
        sidecar_port = args.sidecar_port
        if sidecar_port is None and args.sfep_base_url:
            sidecar_port = 18081
        process, base_url = start_sidecar(fixture, port=sidecar_port)
        try:
            direct, labels = _verify_route(
                base_url, fixture, sfep_adapter=False
            )
            assert_committed_file_counts(fixture.output_dir, predictions=1, labels=1)
            sfep = None
            if args.sfep_base_url:
                sfep, _ = _verify_route(
                    args.sfep_base_url,
                    fixture,
                    sfep_adapter=True,
                    labels=labels,
                )
                assert_committed_file_counts(fixture.output_dir, predictions=1, labels=1)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "directSidecar": direct,
                        "sfepAdapter": sfep,
                        "temporaryEvidenceOnly": True,
                        "deploymentEligible": False,
                    },
                    allow_nan=False,
                    sort_keys=True,
                )
            )
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
