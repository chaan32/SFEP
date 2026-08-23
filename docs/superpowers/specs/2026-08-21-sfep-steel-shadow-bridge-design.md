# SFEP Steel Shadow Bridge Design

**Date:** 2026-08-21  
**Status:** Proposed for implementation  
**Scope:** Phase 1 HTTP bridge between the existing Spring Boot SFEP server and the Python steel Shadow workflow

## 1. Purpose

Connect newly produced Coil process features and later-finalized quality labels to the existing append-only Steel Shadow workflow without changing the current AI4I `SensorEvent` contract and without allowing the NextGen model to control production decisions.

The bridge must let SFEP:

1. accept a label-free Coil Feature Batch;
2. obtain Baseline, Challenger, and NextGen predictions from the Python artifacts;
3. accept delayed final labels;
4. expose Shadow collection and Gate status;
5. retrieve the recorded predictions for one Coil;
6. fail safely when the Shadow service is unavailable.

`READY` in this design means the connection software is ready. It never means that NextGen is deployment eligible.

## 2. Architectural decision

Use a long-running local Python HTTP sidecar and a thin Spring Boot adapter.

```text
SFEP client / future collector
        |
        v
Spring Boot /api/steel-shadow/**
        |
        v
Python Steel Shadow HTTP sidecar (127.0.0.1:18081)
        |
        +--> registry + three frozen model artifacts
        +--> append-only feature/prediction/label ledger
        +--> Drift, Calibration, period metrics, and Gate evaluation
```

This is preferred over launching one Python process per request because the interpreter and HTTP service stay alive and request latency and failure handling are explicit. The existing runner continues to validate and load the registered artifacts for each new committed Batch; Phase 1 does not introduce a model cache that could bypass hash validation. It is preferred over a Kafka-first implementation for Phase 1 because the current objective is a testable Coil-to-prediction connection, not a new distributed event platform. Kafka topics may be added after this HTTP contract is stable.

The Python sidecar will use the standard-library `ThreadingHTTPServer`; no new Python runtime dependency is introduced. The Spring adapter will use Spring's existing HTTP client and Jackson support. The Java implementation must follow the versions and patterns in the downloaded SFEP source rather than assuming undeclared dependencies.

## 3. Boundaries

### Included

- Python HTTP service wrapping `training.steel.run_shadow`
- Spring Boot request DTOs, response DTOs, controller, service, client, configuration, and metrics
- Feature scoring, delayed-label ingestion, status, health, and Coil prediction lookup
- Request limits, schema-version validation, idempotency, and safe error mapping
- Python, Spring, and end-to-end contract tests
- Operating instructions and example requests

### Excluded

- automatic model deployment or model replacement
- PLC commands, line stop commands, or production-control decisions
- changing the existing AI4I `SensorEventRequest`
- Kafka topics, PostgreSQL Shadow tables, Grafana panels, authentication, TLS termination, or public network exposure
- tuning models using the historical HOLDOUT
- treating synthetic or replayed requests as independent future evidence

The sidecar binds to `127.0.0.1` by default. Network authentication and TLS are required before any non-local deployment.

## 4. Components

### 4.1 Python contract adapter

Create `training/steel/shadow_api_contract.py`.

Responsibilities:

- validate top-level schema versions and non-empty request IDs;
- convert camelCase HTTP JSON into deterministic canonical CSV frames;
- require identity, time, and all 30 base process Features;
- reject `judge`, `ap_date`, every `ap_*`, `dataset_split`, and caller-supplied engineered Features in scoring requests;
- reject unknown top-level and Coil fields;
- keep deterministic column and row order so an identical request produces identical source bytes;
- impose 10,000 Coil, 10 MiB request, and 64-level JSON nesting limits;
- map domain failures to stable API error codes.

The six NextGen engineered Features are never accepted from the caller. They remain calculated by `shadow_contract.add_engineered_features`:

- `gas_total`
- `heating_interval_total`
- `pre_to_heat_temp_delta`
- `heat_to_sock_temp_delta`
- `width_reduction`
- `width_ratio`

### 4.2 Python HTTP sidecar

Create `training/steel/shadow_api.py`.

Startup arguments:

```text
--host 127.0.0.1
--port 18081
--registry training/steel/shadow_registry/registry.json
--output-dir training/steel/shadow_output
--max-request-bytes 10485760
--max-coils 10000
```

Startup must call `load_and_validate_registry`. A registry, model, calibrator, cohort, or hash error prevents the server from becoming healthy.

Each accepted HTTP request is converted to a canonical UTF-8-SIG CSV in a temporary directory. The existing runner copies that source byte-for-byte into its append-only ledger before the temporary file is removed. Temporary conversion files are not evidence; the committed `*.source.csv` ledger file is evidence.

The server must never accept an evidence-status parameter. Feature scoring always calls the public `score_feature_batch`, which is fixed to `INDEPENDENT_FUTURE_SHADOW`.

All score, label, status, and prediction-ledger operations are serialized inside the sidecar. The runner's existing filesystem lock remains the cross-process mutation lock, and evaluation is updated to take the same output lock through a non-recursive public-wrapper/private-worker structure. This prevents a CLI label ingestion or score commit from racing an HTTP evaluation.

### 4.3 Spring Boot adapter

Add a separate package without modifying the AI4I event domain:

```text
sfep-server/src/main/java/com/sfep/sfep_server/steelshadow/
├── config/
├── controller/
├── dto/
├── client/
└── service/
```

Responsibilities:

- expose `/api/steel-shadow/**`;
- validate request shape before forwarding;
- preserve `requestId` in logs and responses;
- call only the configured local sidecar URL;
- map sidecar contract failures without turning them into SFEP server crashes;
- record request count, failure count, and latency through Micrometer when available;
- perform no scoring and store no duplicate Shadow truth of record in Java.

Configuration keys:

```yaml
sfep:
  steel-shadow:
    enabled: false
    base-url: http://127.0.0.1:18081
    connect-timeout: 2s
    read-timeout: 120s
```

The feature is disabled by default. Enabling it does not change existing AI4I endpoints, Kafka consumers, simulator behavior, alert behavior, or dashboard behavior.

## 5. HTTP contracts

### 5.1 Score a Feature Batch

SFEP endpoint: `POST /api/steel-shadow/features`  
Sidecar endpoint: `POST /v1/features`

Request:

```json
{
  "schemaVersion": "steel-shadow-feature-batch-v1",
  "requestId": "hr-20260822-shift-a-001",
  "coils": [
    {
      "chargeId": "C20260822001",
      "slabNo": "1",
      "hrCoilId": "H20260822001",
      "hrDate": "2026-08-22",
      "featureAvailableAt": "2026-08-22T03:15:00+00:00",
      "features": {
        "sm_plant": "1제강",
        "steel_grade": "GRADE-A",
        "steel_usage": "USE-A",
        "delta_ferrite": 0.0,
        "ingre_cr": 0.0,
        "ingre_ni": 0.0,
        "ingre_s": 0.0,
        "cc_gubun": "CC-A",
        "tundish_temp": 1500.0,
        "mlac_ratio": 50.0,
        "slab_gubun": "SLAB-A",
        "slab_grind": "HSHS",
        "furnace_no": "1호기",
        "f_jangip_gubun": "A",
        "f_jangip_temp": 700.0,
        "f_bfg": 10.0,
        "f_cog": 5.0,
        "f_ldg": 0.0,
        "f_pre_temp": 1100.0,
        "f_heat_temp": 1250.0,
        "f_sock_temp": 1250.0,
        "f_pre_interval": 10.0,
        "f_heat_interval": 20.0,
        "f_sock_interval": 10.0,
        "f_ext_time": 1.0,
        "hr_thick": 2.0,
        "hr_width": 1248.0,
        "rm4_temp": 950.0,
        "rm_pitch": 1.0,
        "slab_width": 1250.0
      }
    }
  ]
}
```

Response:

```json
{
  "schemaVersion": "steel-shadow-score-response-v1",
  "requestId": "hr-20260822-shift-a-001",
  "batchId": "20260822T031600000000Z-0123456789ab",
  "evidenceStatus": "INDEPENDENT_FUTURE_SHADOW",
  "coilCount": 1,
  "predictions": [
    {
      "hrCoilId": "H20260822001",
      "modelRole": "shadow_incumbent",
      "modelId": "steel-quality-challenger-v0.2:logistic_l2_c0_1",
      "riskScore": 0.18,
      "calibratedProbability": null,
      "policyThreshold": 0.8518789071419577,
      "predictedLabel": "양품"
    }
  ],
  "shadowStatus": "COLLECTING",
  "deploymentEligible": false
}
```

The actual response contains one prediction for each registered model and Coil. Internal filesystem paths are never returned through HTTP.

### 5.2 Ingest finalized labels

SFEP endpoint: `POST /api/steel-shadow/labels`  
Sidecar endpoint: `POST /v1/labels`

Request:

```json
{
  "schemaVersion": "steel-shadow-label-batch-v1",
  "requestId": "ap-20260825-final-001",
  "supersedesLabelBatchId": null,
  "labels": [
    {
      "hrCoilId": "H20260822001",
      "judge": "불량",
      "labelFinalizedAt": "2026-08-25T09:00:00+00:00"
    }
  ]
}
```

Response includes `batchId`, `labelCount`, `positiveLabels`, the current Gate status, and `deploymentEligible=false`.

### 5.3 Status

SFEP endpoint: `GET /api/steel-shadow/status`  
Sidecar endpoint: `GET /v1/status`

Response includes:

- `status`
- `evidenceStatus`
- `predictionBatches`
- `labelBatches`
- `days`
- `labeledCoils`
- `positiveLabels`
- `failedChecks`
- `deploymentEligible`

The initial expected state is `AWAITING_FUTURE_DATA`, zero prediction batches, zero label batches, and `deploymentEligible=false`.

`GET /v1/status` calls the validating evaluation path, not the convenience function that merely reads the latest saved status. Scoring and label ingestion also run evaluation after their commits, so the response reflects the ledger that was just written.

### 5.4 Coil predictions

SFEP endpoint: `GET /api/steel-shadow/predictions/{hrCoilId}`  
Sidecar endpoint: `GET /v1/predictions/{hrCoilId}`

The sidecar validates the complete committed prediction ledger before returning the requested Coil. A missing Coil returns `404 COIL_NOT_FOUND`; a ledger-integrity failure returns `409 LEDGER_INTEGRITY_REVIEW_REQUIRED` and no predictions.

### 5.5 Health

Sidecar endpoint: `GET /health`

Healthy response requires a currently valid Registry and all registered artifacts. It returns model IDs, registry hash, service version, and `deploymentEligible=false`. It does not run a prediction.

## 6. Error contract

All errors use:

```json
{
  "schemaVersion": "steel-shadow-error-v1",
  "requestId": "hr-20260822-shift-a-001",
  "code": "FEATURE_CONTRACT_INVALID",
  "message": "feature batch contains forbidden columns",
  "retryable": false
}
```

Stable mappings:

| HTTP | Code | Meaning |
|---:|---|---|
| 400 | `REQUEST_SCHEMA_INVALID` | malformed JSON, wrong schema version, unknown fields, or count violation |
| 405 | `REQUEST_SCHEMA_INVALID` | unsupported HTTP method |
| 408 | `REQUEST_SCHEMA_INVALID` | request body was not received before the local read timeout |
| 413 | `REQUEST_SCHEMA_INVALID` | request body exceeded the hard 10 MiB limit |
| 400 | `FEATURE_CONTRACT_INVALID` | missing Feature, forbidden field, invalid identifier, invalid timestamp |
| 400 | `LABEL_CONTRACT_INVALID` | invalid or non-final label contract |
| 404 | `COIL_NOT_FOUND` | no committed prediction for the requested Coil |
| 409 | `COIL_ALREADY_SCORED_DIFFERENTLY` | Coil exists in another committed source batch |
| 409 | `LEDGER_INTEGRITY_REVIEW_REQUIRED` | partial, changed, mismatched, or orphan ledger evidence |
| 503 | `SHADOW_UNAVAILABLE` | registry/model unavailable or sidecar unreachable |
| 500 | `SHADOW_INTERNAL_ERROR` | unexpected failure with no internal path or stack trace exposed |

Spring returns the sidecar status and safe error body. If the sidecar is unreachable or times out, Spring returns `503 SHADOW_UNAVAILABLE`. No failure invokes a production-control fallback.

## 7. Data, time, and idempotency rules

- One request contains 1 to 10,000 unique `hrCoilId` values.
- `requestId` is 1 to 128 printable ASCII characters and is used for correlation, not evidence identity.
- Evidence identity remains the deterministic canonical CSV SHA-256 plus system-generated UTC time.
- An identical source request retry returns the already committed Batch.
- The same Coil with changed Features is rejected.
- `predictionGeneratedAt` is generated only by the sidecar.
- Independent `hrDate` and `featureAvailableAt` must not precede the Registry `shadow_start_at` contract.
- `predictionGeneratedAt` must not precede `featureAvailableAt`.
- `labelFinalizedAt` must be after prediction and not after label ingestion time.
- Label corrections must replace every Coil in the superseded label Batch.
- The `judge` domain is exactly `양품` or `불량`.
- AP operating columns and labels never enter Feature scoring.

## 8. Storage and evidence

The Python append-only filesystem ledger remains the Phase 1 source of truth:

```text
training/steel/shadow_output/
├── .shadow-mode.json
├── prediction_batches/
├── label_batches/
├── evaluations/
└── status/
```

SFEP does not duplicate predictions into PostgreSQL in Phase 1. This avoids two competing sources of truth while the contract is being validated. PostgreSQL projection and Kafka publication are follow-up work after the HTTP bridge passes its end-to-end tests.

All existing protections remain active: output lock, metadata-last commit, raw source snapshot, SHA-256 verification, Batch ID recomputation, output-root mode binding, temporal revalidation, artifact lineage, and `deployment_eligible=false`.

## 9. Observability

Spring metrics, when Micrometer is present:

- `sfep_steel_shadow_requests_total{operation,outcome}`
- `sfep_steel_shadow_request_duration_seconds{operation}`
- `sfep_steel_shadow_unavailable_total`

Logs include `requestId`, operation, batch ID, Coil count, outcome, and latency. Logs never include the full Feature payload or internal filesystem paths.

The sidecar writes structured one-line JSON logs with the same correlation values. It never logs model artifact contents or full request bodies.

## 10. Testing strategy

### Python unit tests

- exact schema versions and unknown-field rejection
- deterministic JSON-to-CSV conversion
- required 30 base Features
- forbidden label/AP/engineered fields
- 10,000 Coil, 10 MiB, and 64-level JSON nesting limits
- safe error mapping
- Coil prediction ledger lookup and full-ledger validation

### Python HTTP integration tests

- ephemeral server port and temporary Registry/output directory
- score request produces one prediction per registered model and Coil
- repeated identical request returns the same Batch
- delayed label changes state from prediction-only collection to evaluated collection
- historical Feature, future Feature timestamp, and early/future label rejection
- health failure on invalid Registry
- no response exposes an internal path

### Spring tests

- controller request validation
- exact forwarding contract
- response and error mapping
- sidecar timeout/unreachable mapping to 503
- feature disabled behavior
- existing SensorEvent and dashboard test regression

### End-to-end acceptance

Tests use a temporary output directory and never write synthetic data to `training/steel/shadow_output`.

1. Start the sidecar on an ephemeral port with fixture artifacts.
2. Start the Spring test context with the adapter enabled.
3. POST one valid future Feature Batch through Spring.
4. Verify three predictions and `deploymentEligible=false`.
5. POST its finalized label through Spring.
6. Verify joined evaluation artifacts and `COLLECTING`.
7. Repeat both requests and verify no duplicate evidence.
8. Verify all pre-existing Python Steel tests and Spring tests remain green.

## 11. Operational flow

```text
1. Start PostgreSQL/Redis/Kafka only as required by the existing SFEP profile.
2. Start the Python Shadow sidecar locally.
3. Confirm /health and Registry hash.
4. Start SFEP with sfep.steel-shadow.enabled=true.
5. Submit only genuinely future production Features.
6. Submit labels only after AP finalization.
7. Monitor /api/steel-shadow/status.
8. Keep Challenger as incumbent until HUMAN_REVIEW_CANDIDATE is reached and reviewed.
```

The current Gate remains at least 28 days, 5,000 labeled Coils, and 150 positive labels, plus the existing FPR, Recall improvement, PR-AUC improvement, confidence-interval, Calibration, Drift, and integrity checks.

## 12. Prerequisite and implementation constraint

The current Spring Java source, `build.gradle`, `application.yaml`, and wrapper files are present as macOS/iCloud `dataless` placeholders. Before implementation, they must be downloaded locally and read in full. The implementation must then reconcile this design with the actual controller conventions, dependency declarations, configuration binding, exception handling, and test framework. If the downloaded code conflicts with an interface in this document, implementation pauses and the design is revised rather than guessing.

## 13. Success criteria

The bridge is complete only when all of the following hold:

- SFEP accepts a valid future Feature Batch and returns three model predictions.
- SFEP accepts delayed finalized labels and exposes updated collection status.
- repeat requests are idempotent and changed Coil content is rejected.
- historical/replayed data cannot enter independent future evidence.
- service, registry, model, ledger, or time failures fail closed.
- no endpoint can set `deployment_eligible=true` or trigger production control.
- synthetic tests leave the real `shadow_output` at zero real batches.
- all Python Steel tests, new bridge tests, and existing Spring tests pass.
- operating instructions reproduce startup, health, scoring, labeling, status, and shutdown.
