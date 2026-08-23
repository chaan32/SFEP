# SFEP Steel Shadow Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect Spring Boot SFEP to the frozen Python Steel Shadow workflow through a local, fail-closed HTTP sidecar that accepts future Coil Features, delayed labels, status requests, and Coil prediction lookups.

**Architecture:** A standard-library Python HTTP sidecar owns Shadow contracts, model scoring, append-only evidence, and evaluation. Spring Boot adds a disabled-by-default `steelshadow` adapter that validates and forwards requests but never scores, duplicates the ledger, deploys a model, or sends a production-control command.

**Tech Stack:** Python 3.14, pandas 3.0.1, scikit-learn 1.9.0, joblib 1.5.3, `ThreadingHTTPServer`, Java 21, Spring Boot 3.5, Jackson, Spring `RestClient`, Micrometer, `unittest`, Gradle/JUnit 5.

**Spec:** `docs/superpowers/specs/2026-08-21-sfep-steel-shadow-bridge-design.md`

## Global Constraints

- The HTTP score path always records `INDEPENDENT_FUTURE_SHADOW`; requests cannot override evidence status.
- Tests use temporary output directories and never write synthetic data to `training/steel/shadow_output`.
- `judge`, `ap_date`, `dataset_split`, every `ap_*`, and the six engineered Features are forbidden in score input.
- System code generates prediction and ingestion timestamps as timezone-aware UTC.
- The sidecar binds to `127.0.0.1`; the Spring feature is disabled by default.
- No endpoint may set `deployment_eligible=true`, replace the incumbent, or trigger production control.
- A Feature request contains 1–10,000 unique Coils and is at most 10 MiB.
- Existing lock, metadata-last, source snapshot, SHA-256, Batch ID, output-mode, temporal, lineage, and Gate checks remain active.
- No new Python dependency is added.
- Before a Java edit, `sfep-server/src/**`, Gradle files, wrapper files, and `application.yaml` must have no `dataless` flag and relevant files must be read completely.
- If downloaded Spring conventions conflict with the spec, stop before Java edits and revise the design and plan.
- Git metadata is non-responsive. Each commit step attempts scoped status first; if it does not return, record `COMMIT_SKIPPED_GIT_UNAVAILABLE` and do not stage unrelated files.

## Source Materialization Gate

Required before Task 4:

```bash
ls -lO \
  sfep-server/build.gradle \
  sfep-server/settings.gradle \
  sfep-server/gradlew \
  sfep-server/gradle/wrapper/gradle-wrapper.properties \
  sfep-server/src/main/resources/application.yaml \
  sfep-server/src/main/java/com/sfep/sfep_server/event/controller/SensorEventController.java \
  sfep-server/src/test/java/com/sfep/sfep_server/SfepServerApplicationTests.java
```

Expected: no `dataless`. Otherwise use Finder **Download Now**, a functioning iCloud materialization operation, or a fresh repository clone. Never reconstruct user Java source from `.class` names.

After materialization read `build.gradle`, `application.yaml`, representative Event/Kafka/Dashboard classes, and the Spring test bootstrap before editing.

---

### Task 1: Lock validating evaluation and expose prediction reads

**Files:**
- Modify: `training/steel/run_shadow.py:123-139,629-812,1596-1835`
- Modify: `training/steel/tests/test_run_shadow.py:623-1177`

**Interfaces:**
- Consumes: `_output_lock`, `_validated_prediction_ledger`, `load_and_validate_registry`
- Produces: `evaluate_shadow(registry_path: Path, output_dir: Path, *, now: Callable[[], pd.Timestamp] = _utc_now, bootstrap_iterations: int = 2000) -> dict[str, object]`
- Produces: `_evaluate_shadow_locked(registry_path: Path, output_dir: Path, *, now: Callable[[], pd.Timestamp], bootstrap_iterations: int) -> dict[str, object]`
- Produces: `read_shadow_predictions(registry_path: Path, output_dir: Path, *, batch_id: str | None = None, hr_coil_id: str | None = None, now: Callable[[], pd.Timestamp] = _utc_now) -> list[dict[str, object]]`

- [ ] **Step 1: Write failing tests**

```python
def test_evaluate_shadow_takes_the_output_lock(self):
    with mock.patch.object(subject, "_output_lock", wraps=subject._output_lock) as lock:
        subject.evaluate_shadow(registry_path, output_dir, now=fixed_now)
    lock.assert_called_once_with(output_dir)

def test_reads_one_validated_batch_or_coil_without_internal_paths(self):
    scored = score_fixture_batch()
    rows = subject.read_shadow_predictions(
        registry_path, output_dir, batch_id=scored["batch_id"], now=fixed_now
    )
    self.assertEqual(len(rows), 2)
    self.assertNotIn("prediction_path", rows[0])

def test_prediction_reader_requires_exactly_one_filter(self):
    with self.assertRaisesRegex(ValueError, "exactly one"):
        subject.read_shadow_predictions(registry_path, output_dir)
```

- [ ] **Step 2: Run tests and verify RED**

```bash
LOKY_MAX_CPU_COUNT=1 python3 -m unittest \
  training.steel.tests.test_run_shadow.ShadowEvaluationTest.test_evaluate_shadow_takes_the_output_lock \
  training.steel.tests.test_run_shadow.ShadowEvaluationTest.test_reads_one_validated_batch_or_coil_without_internal_paths \
  training.steel.tests.test_run_shadow.ShadowEvaluationTest.test_prediction_reader_requires_exactly_one_filter -v
```

Expected: the lock wrapper and reader are missing.

- [ ] **Step 3: Implement the minimal wrapper and reader**

```python
def evaluate_shadow(
    registry_path: Path,
    output_dir: Path,
    *,
    now: Callable[[], pd.Timestamp] = _utc_now,
    bootstrap_iterations: int = 2000,
) -> dict[str, object]:
    with _output_lock(Path(output_dir)):
        return _evaluate_shadow_locked(
            registry_path, output_dir, now=now,
            bootstrap_iterations=bootstrap_iterations,
        )
```

Move the existing body to `_evaluate_shadow_locked`. The reader validates the Registry and full ledger, requires exactly one filter, and returns JSON-safe records sorted by `(hr_coil_id, model_role)`.

- [ ] **Step 4: Verify GREEN and regressions**

```bash
LOKY_MAX_CPU_COUNT=1 python3 -m unittest training.steel.tests.test_run_shadow -q
LOKY_MAX_CPU_COUNT=1 python3 -m unittest discover -s training/steel/tests -q
```

- [ ] **Step 5: Commit when available**

```bash
git status --short -- training/steel/run_shadow.py training/steel/tests/test_run_shadow.py
git add training/steel/run_shadow.py training/steel/tests/test_run_shadow.py
git commit -m "feat: expose locked steel shadow reads"
```

---

### Task 2: Define deterministic HTTP contracts

**Files:**
- Create: `training/steel/shadow_api_contract.py`
- Create: `training/steel/tests/test_shadow_api_contract.py`

**Interfaces:**
- Produces: `FEATURE_SCHEMA_VERSION = "steel-shadow-feature-batch-v1"`
- Produces: `LABEL_SCHEMA_VERSION = "steel-shadow-label-batch-v1"`
- Produces: `parse_feature_request(payload: object, registry: dict[str, object], *, max_coils: int) -> tuple[str, pd.DataFrame]`
- Produces: `parse_label_request(payload: object, *, max_coils: int) -> tuple[str, str | None, pd.DataFrame]`
- Produces: `canonical_csv_bytes(frame: pd.DataFrame) -> bytes`
- Produces: `ShadowApiContractError(code: str, message: str)`

- [ ] **Step 1: Write failing contract tests**

Create a fixture containing all 30 Registry base Features and add independent tests:

```python
def test_parses_valid_camel_case_feature_request_in_contract_order(self):
    request_id, frame = subject.parse_feature_request(
        valid_feature_payload(), registry_fixture(), max_coils=10_000
    )
    self.assertEqual(request_id, "hr-20260822-a-001")
    self.assertEqual(frame.columns[:5].tolist(), [
        "charge_id", "slab_no", "hr_coil_id", "hr_date", "feature_available_at"
    ])
    self.assertNotIn("gas_total", frame)

def test_identical_payloads_produce_identical_canonical_bytes(self):
    _, first = subject.parse_feature_request(payload, registry, max_coils=10_000)
    _, second = subject.parse_feature_request(reordered_payload, registry, max_coils=10_000)
    self.assertEqual(subject.canonical_csv_bytes(first), subject.canonical_csv_bytes(second))

def test_rejects_forbidden_and_engineered_fields(self):
    for field in ("judge", "ap_date", "ap_line_speed", "gas_total"):
        with self.subTest(field=field):
            with self.assertRaisesRegex(subject.ShadowApiContractError, "forbidden"):
                subject.parse_feature_request(payload_with(field), registry, max_coils=10_000)
```

Also test wrong versions, unknown fields, empty/invalid request ID, duplicate Coils, 0 and 10,001 Coils, and invalid labels.

- [ ] **Step 2: Run tests and verify RED**

```bash
LOKY_MAX_CPU_COUNT=1 python3 -m unittest training.steel.tests.test_shadow_api_contract -v
```

Expected: `training.steel.shadow_api_contract` is missing.

- [ ] **Step 3: Implement strict parsing**

Use exact field sets from the spec. Derive base Feature order from the union of Registry `included` minus `ENGINEERED_FEATURES`. Require an exact Feature set per Coil, reject duplicates before sorting, sort canonical rows by `hr_coil_id`, and encode with `frame.to_csv(index=False).encode("utf-8-sig")`.

- [ ] **Step 4: Verify GREEN**

```bash
LOKY_MAX_CPU_COUNT=1 python3 -m unittest \
  training.steel.tests.test_shadow_api_contract \
  training.steel.tests.test_shadow_contract -v
```

- [ ] **Step 5: Commit when available**

```bash
git status --short -- training/steel/shadow_api_contract.py training/steel/tests/test_shadow_api_contract.py
git add training/steel/shadow_api_contract.py training/steel/tests/test_shadow_api_contract.py
git commit -m "feat: define steel shadow HTTP contracts"
```

---

### Task 3: Build the Python Shadow HTTP sidecar

**Files:**
- Create: `training/steel/shadow_api.py`
- Create: `training/steel/tests/test_shadow_api.py`
- Modify: `training/steel/README.md`

**Interfaces:**
- Consumes: Task 1 reader/locked evaluation and Task 2 contract parsers
- Produces: `ShadowApiConfig(host: str, port: int, registry_path: Path, output_dir: Path, max_request_bytes: int, max_coils: int)` as a frozen dataclass
- Produces: `ShadowApiApplication.handle_features(payload: object) -> dict[str, object]`
- Produces: `ShadowApiApplication.handle_labels(payload: object) -> dict[str, object]`
- Produces: `ShadowApiApplication.status() -> dict[str, object]`
- Produces: `ShadowApiApplication.predictions(hr_coil_id: str) -> dict[str, object]`
- Produces: `ShadowApiApplication.health() -> dict[str, object]`
- Produces: `build_server(config: ShadowApiConfig, *, now: Callable[[], pd.Timestamp] = _utc_now) -> ThreadingHTTPServer`
- Produces CLI: `python3 -m training.steel.shadow_api`

- [ ] **Step 1: Write failing service tests without sockets**

Use fixture artifacts and a temporary output directory:

```python
def test_feature_request_scores_every_registered_model_without_paths(self):
    response = app.handle_features(valid_feature_payload())
    self.assertEqual(response["schemaVersion"], "steel-shadow-score-response-v1")
    self.assertEqual(len(response["predictions"]), 2)
    self.assertFalse(response["deploymentEligible"])
    self.assertFalse(any("path" in key.lower() for key in flatten_keys(response)))

def test_identical_retry_returns_one_committed_batch(self):
    first = app.handle_features(valid_feature_payload())
    second = app.handle_features(valid_feature_payload())
    self.assertEqual(first["batchId"], second["batchId"])
    self.assertEqual(len(list(prediction_dir.glob("*.metadata.json"))), 1)

def test_label_request_evaluates_after_commit(self):
    app.handle_features(valid_feature_payload())
    response = app.handle_labels(valid_label_payload())
    self.assertEqual(response["shadowStatus"], "COLLECTING")
    self.assertFalse(response["deploymentEligible"])
```

- [ ] **Step 2: Run service tests and verify RED**

```bash
LOKY_MAX_CPU_COUNT=1 python3 -m unittest \
  training.steel.tests.test_shadow_api.ShadowApiApplicationTest -v
```

Expected: `training.steel.shadow_api` is missing.

- [ ] **Step 3: Implement the application service**

`ShadowApiApplication` owns one `threading.RLock`. Every public operation acquires it. Feature and label handlers parse JSON, write canonical CSV to `TemporaryDirectory`, call the append-only runner, release mutation locking, run validating evaluation, and return no internal paths. `status()` calls `evaluate_shadow`; `health()` calls `load_and_validate_registry` every time.

- [ ] **Step 4: Write failing real HTTP tests**

Start the server on port `0` and use `urllib.request`:

```python
def test_routes_limits_and_safe_errors(self):
    self.assertEqual(post_json("/v1/features", valid_payload).status, 200)
    self.assertEqual(post_json("/v1/features", forbidden_payload).status, 400)
    self.assertEqual(post_bytes("/v1/features", oversized_body).status, 413)
    self.assertEqual(get_json("/v1/predictions/UNKNOWN").status, 404)
    self.assertFalse(get_json("/health").json()["deploymentEligible"])
    self.assertNotIn(str(temp_root), forbidden_error_body)
```

- [ ] **Step 5: Run HTTP tests and verify RED, then implement routes**

```bash
LOKY_MAX_CPU_COUNT=1 python3 -m unittest \
  training.steel.tests.test_shadow_api.ShadowApiHttpTest -v
```

Implement only `POST /v1/features`, `POST /v1/labels`, `GET /v1/status`, `GET /v1/predictions/{coil}`, and `GET /health`. POST requires `application/json` and valid `Content-Length`; responses use `application/json; charset=utf-8` and the exact safe error schema.

- [ ] **Step 6: Add CLI and README commands**

```bash
LOKY_MAX_CPU_COUNT=1 python3 -m training.steel.shadow_api \
  --host 127.0.0.1 \
  --port 18081 \
  --registry training/steel/shadow_registry/registry.json \
  --output-dir training/steel/shadow_output
```

Document health, feature, label, status, and prediction calls. State that the real output directory accepts only genuine future production data.

- [ ] **Step 7: Verify full Python GREEN**

```bash
LOKY_MAX_CPU_COUNT=1 python3 -m unittest discover -s training/steel/tests -q
python3 -m py_compile \
  training/steel/shadow_api_contract.py \
  training/steel/shadow_api.py \
  training/steel/run_shadow.py
```

- [ ] **Step 8: Commit when available**

```bash
git status --short -- training/steel/shadow_api.py training/steel/tests/test_shadow_api.py training/steel/README.md
git add training/steel/shadow_api.py training/steel/tests/test_shadow_api.py training/steel/README.md
git commit -m "feat: add steel shadow HTTP sidecar"
```

---

### Task 4: Add Spring configuration, DTOs, and sidecar client

**Precondition:** The Source Materialization Gate is green and actual Spring conventions match the spec.

**Files:**
- Create: `sfep-server/src/main/java/com/sfep/sfep_server/steelshadow/config/SteelShadowProperties.java`
- Create: `sfep-server/src/main/java/com/sfep/sfep_server/steelshadow/config/SteelShadowClientConfig.java`
- Create: `sfep-server/src/main/java/com/sfep/sfep_server/steelshadow/dto/SteelShadowFeatureBatchRequest.java`
- Create: `sfep-server/src/main/java/com/sfep/sfep_server/steelshadow/dto/SteelShadowLabelBatchRequest.java`
- Create: `sfep-server/src/main/java/com/sfep/sfep_server/steelshadow/dto/SteelShadowResponses.java`
- Create: `sfep-server/src/main/java/com/sfep/sfep_server/steelshadow/client/SteelShadowClient.java`
- Create: `sfep-server/src/main/java/com/sfep/sfep_server/steelshadow/client/SteelShadowClientException.java`
- Create: `sfep-server/src/test/java/com/sfep/sfep_server/steelshadow/client/SteelShadowClientTest.java`
- Modify: `sfep-server/src/main/resources/application.yaml`

**Interfaces:**
- Produces configuration prefix `sfep.steel-shadow`
- Produces typed camelCase request/response records
- Produces `SteelShadowScoreResponse score(SteelShadowFeatureBatchRequest request)`
- Produces `SteelShadowLabelResponse ingestLabels(SteelShadowLabelBatchRequest request)`
- Produces `SteelShadowStatusResponse status()`
- Produces `SteelShadowPredictionResponse predictions(String hrCoilId)`
- Produces `SteelShadowHealthResponse health()`

- [ ] **Step 1: Write failing client tests against JDK `HttpServer`**

```java
@Test
void forwardsExactFeatureContractAndParsesResponse() {
    var response = client.score(featureRequest());
    assertThat(capturedPath).isEqualTo("/v1/features");
    assertThat(capturedJson.at("/schemaVersion").asText())
        .isEqualTo("steel-shadow-feature-batch-v1");
    assertThat(response.deploymentEligible()).isFalse();
}

@Test
void mapsUnavailableServiceWithoutRetryingMutation() {
    assertThatThrownBy(() -> client.score(featureRequest()))
        .isInstanceOf(SteelShadowClientException.class);
    assertThat(requestCount.get()).isEqualTo(1);
}
```

- [ ] **Step 2: Run client tests and verify RED**

```bash
cd sfep-server
./gradlew test --tests '*SteelShadowClientTest'
```

Expected: Steel Shadow Java classes are missing.

- [ ] **Step 3: Implement configuration, records, and client**

Required property shape:

```java
@ConfigurationProperties("sfep.steel-shadow")
public record SteelShadowProperties(
    boolean enabled,
    URI baseUrl,
    Duration connectTimeout,
    Duration readTimeout
) {}
```

Use the downloaded project's actual configuration-registration convention and a configured Spring `RestClient`. Preserve safe sidecar error code/status, never retry POST internally, and add disabled defaults:

```yaml
sfep:
  steel-shadow:
    enabled: false
    base-url: http://127.0.0.1:18081
    connect-timeout: 2s
    read-timeout: 120s
```

- [ ] **Step 4: Verify client and full Spring GREEN**

```bash
./gradlew test --tests '*SteelShadowClientTest'
./gradlew test
```

- [ ] **Step 5: Commit when available**

```bash
git status --short -- sfep-server/src/main/java/com/sfep/sfep_server/steelshadow sfep-server/src/test/java/com/sfep/sfep_server/steelshadow sfep-server/src/main/resources/application.yaml
git add sfep-server/src/main/java/com/sfep/sfep_server/steelshadow sfep-server/src/test/java/com/sfep/sfep_server/steelshadow sfep-server/src/main/resources/application.yaml
git commit -m "feat: add SFEP steel shadow client"
```

---

### Task 5: Expose SFEP controller, safe errors, and metrics

**Files:**
- Create: `sfep-server/src/main/java/com/sfep/sfep_server/steelshadow/service/SteelShadowService.java`
- Create: `sfep-server/src/main/java/com/sfep/sfep_server/steelshadow/controller/SteelShadowController.java`
- Create: `sfep-server/src/main/java/com/sfep/sfep_server/steelshadow/controller/SteelShadowExceptionHandler.java`
- Create: `sfep-server/src/test/java/com/sfep/sfep_server/steelshadow/controller/SteelShadowControllerTest.java`
- Create: `sfep-server/src/test/java/com/sfep/sfep_server/steelshadow/controller/SteelShadowDisabledControllerTest.java`

**Interfaces:**
- Produces `POST /api/steel-shadow/features`
- Produces `POST /api/steel-shadow/labels`
- Produces `GET /api/steel-shadow/status`
- Produces `GET /api/steel-shadow/predictions/{hrCoilId}`
- Produces stable `steel-shadow-error-v1` and Micrometer metrics

- [ ] **Step 1: Write failing controller tests using project conventions**

```java
@Test
void featureEndpointNeverReportsDeploymentEligible() throws Exception {
    mvc.perform(post("/api/steel-shadow/features")
            .contentType(APPLICATION_JSON)
            .content(validFeatureJson()))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.deploymentEligible").value(false));
}

@Test
void unreachableSidecarReturnsSafe503() throws Exception {
    mvc.perform(get("/api/steel-shadow/status"))
        .andExpect(status().isServiceUnavailable())
        .andExpect(jsonPath("$.code").value("SHADOW_UNAVAILABLE"))
        .andExpect(jsonPath("$.retryable").value(true));
}
```

Put the disabled-default assertion in its own property-scoped test context:

```java
@SpringBootTest(properties = "sfep.steel-shadow.enabled=false")
@AutoConfigureMockMvc
class SteelShadowDisabledControllerTest {
    @Autowired MockMvc mvc;

    @Test
    void disabledAdapterDoesNotExposeEndpoints() throws Exception {
        mvc.perform(get("/api/steel-shadow/status"))
            .andExpect(status().isNotFound());
    }
}
```

- [ ] **Step 2: Run controller tests and verify RED**

```bash
cd sfep-server
./gradlew test --tests '*SteelShadowControllerTest'
```

- [ ] **Step 3: Implement conditional controller/service/advice**

Use:

```java
@ConditionalOnProperty(
    prefix = "sfep.steel-shadow",
    name = "enabled",
    havingValue = "true"
)
```

The service delegates only to the client. Record `sfep_steel_shadow_requests_total{operation,outcome}`, `sfep_steel_shadow_request_duration_seconds{operation}`, and `sfep_steel_shadow_unavailable_total`. Never log Feature payloads or filesystem paths.

- [ ] **Step 4: Verify controller and full Spring GREEN**

```bash
./gradlew test --tests '*SteelShadowControllerTest'
./gradlew test
```

- [ ] **Step 5: Commit when available**

```bash
git status --short -- sfep-server/src/main/java/com/sfep/sfep_server/steelshadow sfep-server/src/test/java/com/sfep/sfep_server/steelshadow
git add sfep-server/src/main/java/com/sfep/sfep_server/steelshadow sfep-server/src/test/java/com/sfep/sfep_server/steelshadow
git commit -m "feat: expose SFEP steel shadow API"
```

---

### Task 6: Add cross-process smoke test and operations guide

**Files:**
- Create: `scripts/smoke-steel-shadow-bridge.py`
- Create: `docs/steel-shadow-bridge-operations.md`
- Modify: `training/steel/README.md`

**Interfaces:**
- Produces a temporary-output direct-sidecar smoke test
- Optionally verifies Spring when `--sfep-base-url` is supplied
- Produces exact startup, health, score, label, status, prediction, failure, and shutdown instructions
- Produces `main() -> int` and a strict JSON result on stdout

- [ ] **Step 1: Write the smoke test assertions first**

Write `main()` around this exact verification sequence; `fixture_environment`, `start_sidecar`, and `post_json` are test-support functions in the same script and must use `TemporaryDirectory`, `subprocess.Popen`, and `urllib.request` respectively:

```python
def main() -> int:
    with fixture_environment() as fixture:
        process, base_url = start_sidecar(fixture)
        try:
            first_score = post_json(base_url + "/v1/features", fixture.features)
            second_score = post_json(base_url + "/v1/features", fixture.features)
            if first_score["batchId"] != second_score["batchId"]:
                raise AssertionError("feature retry changed batch ID")
            if first_score["deploymentEligible"] is not False:
                raise AssertionError("Shadow response became deployment eligible")
            if len(first_score["predictions"]) != fixture.model_count:
                raise AssertionError("not every registered model produced a prediction")

            first_label = post_json(base_url + "/v1/labels", fixture.labels)
            second_label = post_json(base_url + "/v1/labels", fixture.labels)
            if first_label["batchId"] != second_label["batchId"]:
                raise AssertionError("label retry changed batch ID")
            if first_label["shadowStatus"] != "COLLECTING":
                raise AssertionError("finalized label did not update collection state")

            assert_committed_file_counts(fixture.output_dir, predictions=1, labels=1)
            print(json.dumps({"ok": True, "scoreBatchId": first_score["batchId"]},
                             allow_nan=False, sort_keys=True))
            return 0
        finally:
            process.terminate()
            process.wait(timeout=10)
```

- [ ] **Step 2: Run before completion and verify RED**

```bash
LOKY_MAX_CPU_COUNT=1 python3 scripts/smoke-steel-shadow-bridge.py
```

Expected: the unavailable sidecar module or endpoint is identified.

- [ ] **Step 3: Complete the script and guide**

The guide distinguishes temporary synthetic smoke evidence, historical Dry Run evidence, and genuine future Shadow evidence. It documents all endpoints, disabled default, localhost restriction, 503 behavior, ledger backup, and the fact that `HUMAN_REVIEW_CANDIDATE` still needs human approval.

- [ ] **Step 4: Run both smoke paths**

```bash
LOKY_MAX_CPU_COUNT=1 python3 scripts/smoke-steel-shadow-bridge.py
LOKY_MAX_CPU_COUNT=1 python3 scripts/smoke-steel-shadow-bridge.py \
  --sfep-base-url http://127.0.0.1:18080/api/steel-shadow
```

Expected: both print strict JSON with `ok=true`; the real Shadow ledger remains empty.

- [ ] **Step 5: Commit when available**

```bash
git status --short -- scripts/smoke-steel-shadow-bridge.py docs/steel-shadow-bridge-operations.md training/steel/README.md
git add scripts/smoke-steel-shadow-bridge.py docs/steel-shadow-bridge-operations.md training/steel/README.md
git commit -m "docs: add steel shadow bridge operations"
```

---

### Task 7: Final regression and safety verification

**Files:**
- Verify only; fix a failure only after adding a focused failing regression test

**Interfaces:**
- Produces final evidence that connection software is operational while NextGen remains Shadow-only

- [ ] **Step 1: Run Python verification**

```bash
LOKY_MAX_CPU_COUNT=1 python3 -m unittest discover -s training/steel/tests -q
python3 -m py_compile \
  training/steel/shadow_contract.py \
  training/steel/run_shadow.py \
  training/steel/shadow_api_contract.py \
  training/steel/shadow_api.py
```

- [ ] **Step 2: Run Spring verification**

```bash
cd sfep-server
./gradlew clean test
./gradlew bootJar
```

- [ ] **Step 3: Run both Task 6 smoke commands**

Preserve their strict JSON summaries in the final work report.

- [ ] **Step 4: Prove real Shadow evidence was not polluted**

```bash
cd /Users/haechan/Desktop/SFEP
python3 -m training.steel.run_shadow status \
  --output-dir training/steel/shadow_output
```

Expected: prediction batches `0`, label batches `0`, `AWAITING_FUTURE_DATA`, and deployment `false`.

- [ ] **Step 5: Scan for forbidden coupling**

```bash
rg -n "SensorEventRequest|PLC|stopLine|deployment_eligible.*true|evidence.?status" \
  sfep-server/src/main/java/com/sfep/sfep_server/steelshadow \
  training/steel/shadow_api.py \
  training/steel/shadow_api_contract.py
```

Expected: no AI4I request coupling, PLC/line-stop call, true deployment assignment, or evidence-status input.

- [ ] **Step 6: Request independent review**

Review Feature/Label leakage, temporal provenance, output mode, idempotency, concurrency, safe HTTP errors, Registry/ledger lineage, disabled default, existing-domain regression, and isolation from real `shadow_output`. Resolve every Critical and Important finding through a new failing test.

- [ ] **Step 7: Record final state**

Report connection software status, incumbent model, NextGen eligibility, real future counts, exact tests, source-materialization state, and Git limitations as separate facts.
