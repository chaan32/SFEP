# SFEP Bundle v2 Seed Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 승인된 v1 Bundle과 계약 바이트를 그대로 보존하면서, 고정 통계 seed와 정직한 producer provenance를 갖는 v2 Bundle을 Python에서 생성하고 Java에서 실패 폐쇄 방식으로 검증할 수 있게 한다.

**Architecture:** v1·v2를 허용 목록 기반 `BundleContract`로 분리한다. v2는 config·summary·manifest schema만 새 버전을 사용하고 runtime·ranges·rules·replay schema와 v1 ID 계산식은 그대로 재사용한다. 통계 seed는 v2 config의 `seedMaterial`로 명시하며, Python과 Java는 같은 profile의 일곱 schema role·artifact version 조합만 신뢰한다.

**Tech Stack:** Python 3.12, pandas 2.3.0, NumPy 2.2.6, jsonschema 4.24.0, pytest, Java 21, Gradle 8.14.5 wrapper(`sfep-server/gradlew`), JUnit 5, Jackson, networknt JSON Schema Validator.

**Spec:** `docs/superpowers/specs/2026-08-30-sfep-python-pipeline-optimization-design.md`, `docs/superpowers/specs/2026-08-30-sfep-runtime-desktop-optimization-design.md`

## Global Constraints

- `contracts/equipment-monitor/v1/**`, `contracts/equipment-monitor/v1/golden-bundle/**`, 승인된 actual v1 Bundle의 파일 바이트와 ID를 수정하지 않는다.
- 기존 `analysis/analysis_config.json`은 v1 전용으로 그대로 둔다. v2 actual config는 `analysis/analysis_config_v2.json`이다.
- ID namespace와 preimage는 `sfep-criteria-id/v1` 8필드, `sfep-bundle-id/v1` 19필드를 그대로 사용한다.
- actual v2 seed는 검증된 actual v1 criteria ID `sha256:c0a9d1f3f0d655c24d2eeddc58f1905672d2f72d7ffd0d14e87bdecb118a2a26`이다.
- golden v2 seed는 검증된 golden v1 criteria ID `sha256:8c88d3109bd6945b76317ee415c7821524f515c9bd79d36b8072c285c6434af7`이다.
- Bundle 내부 schema, 외부 경로, 알 수 없는 version을 신뢰하지 않는다. profile 간 fallback도 허용하지 않는다.
- Java 검증 순서는 `layout preflight → strict manifest parse → allowlist profile 선택 → profile claim 검증 → 전체 artifact size/hash attestation → schema validation/DTO binding`이다.
- 실제 데이터와 생성 Bundle은 `var/` 아래에 두고 커밋하지 않는다. 원격 저장소에는 Push하지 않는다.
- 이 계획의 Tasks 2~8은 Python·Java 공유 계약 변경 하나다. 중간 결과는 테스트하되 Task 8 전에는 커밋하지 않고, Task 8에서 하나의 호환성 커밋으로 묶는다.

## Dependency Map

```text
Task 0: 로컬 Python 3.12.10 복구
    ↓
Task 1: v1 oracle 고정
    ↓
Task 2: v2 schema/config
    ↓
Task 3: Python BundleContract
    ↓
Task 4: seed API/worker v2
    ↓
Task 5: v2 summary/artifact/CLI
    ↓
Task 6: v2 golden fixture/comparator
    ↓
Task 7: Java BundleContract loader
    ↓
Task 8: 교차 언어 lane + 단일 호환성 커밋
```

### Task 0: Restore pinned Python 3.12.10 from authenticated local packages

**Files:**

- Create: `analysis/python312-conda.lock`
- Create: `scripts/restore-sfep-python312.sh`
- Create: `analysis/tests/test_python312_restore.py`

- [ ] Freeze the already-present local Conda package set, not a network channel. `analysis/python312-conda.lock` contains exactly these SHA-256/name pairs, one pair per line and in this order:

```text
0a0544cf95f64394fe4959286f5c71f5444ad58feb0602e53becb27448d24da6  ca-certificates-2026.7.22-hbd8a1cb_0.conda
c432626b16768b8dab228bfb706f7060c2d462a21c516d240f68f2f902b5a044  packaging-26.3-pyhc364b38_0.conda
c205bae42eb11b364fe3663a817cbcbc20a8ef544c6b2ff6f391013487117259  pip-26.2.1-pyh8b19718_0.conda
9e200ee5f9ff19a4d94e4b51c4856d53dec849f91032f345cf0c6bc3d51a7183  setuptools-84.0.0-pyh332efcf_0.conda
b928c30ddcb0e3f544c6eade8352737e6e610e263276b90232db6a578ef899d8  tzdata-2026c-h151e31d_0.conda
ba64b29b6d418024cb565081a1799262cb2780d2534ff4c14f76aa858c4286cd  wheel-0.48.0-pyhd8ed1ab_0.conda
8ec22f0ba25cbfc2e64d70cf29459eccd7ffdf6436f6a6ff15bbfef799f7d4f6  bzip2-1.0.8-h4e30115_10.conda
6cdb5dee54c72e56ab189fb3ad33cb28533553d42590e7e831160248f4416a43  icu-78.3-py310h579977c_2.conda
5af74261101e3c777399c6294b2b5d290e508153268eb2e9ff99c4d69834612f  libexpat-2.8.1-hf6b4638_1.conda
c6a530924a9b14e193ea9adfe92843de2a806d1b7dbfd341546ece9653129e60  libffi-3.4.6-h1da3d7d_1.conda
23d0630046a3e8b164d8f80f2b74ed2605af2e7050ab9913018056402fae4311  liblzma-5.8.3-h8088a28_1.conda
839b31d4830e896b4d315b551d27f2bb08026bc946df04bcc360d8627c3ba2cd  libsqlite-3.53.4-hca69786_1.conda
a18fa5d5bac452401459f966cf0d872224e8080c4ff93c77e168d43ab42ef9d7  libzlib-1.3.2-h8088a28_3.conda
7024a48c8c0d0114ed4ab53c76bf9275d50e91ba7cea367a9aead638d3c29c68  ncurses-6.6-he64c551_1.conda
f23239eacd75c4c50705e68fae1aa3292da473e6a3a4abe2330f1e6afa680704  openssl-3.6.4-h55eecbc_0.conda
69aed911271e3f698182e9a911250b05bdf691148b670a23e0bea020031e298e  python-3.12.10-hc22306f_0_cpython.conda
d6782b430e6dce4fe8c7ac118cd988d5a193eb4a7f60741edfaede58016b6110  readline-8.3-h8b90a29_1.conda
857d89087ae7f2c328cf256728affcb7343031a148b4af847334d248a9cf564c  tk-8.6.13-hbeba79b_4.conda
```

- [ ] Implement a strict shell CLI with required `--conda`, `--package-cache`, `--lock`, and `--output` arguments. It verifies every archive with `/usr/bin/shasum -a 256`, rejects extra/missing/duplicate/control-character lock entries, and invokes Conda with the explicit archive paths plus `--offline --copy --no-default-packages`. Set `CONDA_REGISTER_ENVS=false` and a task-local writable `CONDA_PKGS_DIRS`; do not resolve a package name or channel and do not access the network. Conda must create the exact final `--output` prefix directly because installed scripts may contain that prefix; do not build elsewhere and relocate it.

- [ ] If `--output` already passes the exact Python/stdlib probes, exit successfully without mutation. If it exists but is damaged, move it intact beneath a new `mktemp -d /private/tmp/sfep-python-damaged.XXXXXX` recovery directory, report that path, then let Conda create and verify the exact final prefix. If creation fails and leaves a partial prefix, move that partial prefix into a second reported recovery directory and fail; never delete either the damaged original or partial result.

- [ ] Recover the canonical prefix from the verified local cache and prove the existing analysis test venv works through its unchanged symlink.

```bash
cd /Users/haechan/Desktop/SFEP
scripts/restore-sfep-python312.sh \
  --conda /opt/homebrew/Caskroom/miniconda/base/bin/conda \
  --package-cache /opt/homebrew/Caskroom/miniconda/base/pkgs \
  --lock /Users/haechan/Desktop/SFEP/analysis/python312-conda.lock \
  --output /private/tmp/sfep-python-3.12.10
/private/tmp/sfep-python-3.12.10/bin/python3.12 -I -S -B -c \
  'import encodings, pathlib, platform, ssl, venv; assert platform.python_version() == "3.12.10"; assert pathlib.Path(encodings.__file__).is_file()'
/Users/haechan/Desktop/SFEP/analysis/.venv/bin/python -P -B -c \
  'import pytest, numpy, pandas, jsonschema; assert pytest.__version__ == "8.4.1"'
```

- [ ] Add post-bootstrap tests for exact lock parsing, tampered/missing/extra archives, no-clobber success, damaged-output recovery, `CONDA_REGISTER_ENVS=false`, explicit archive arguments, and version/`encodings` failure. Use a fake Conda executable for failure-path unit tests; keep one real local-cache acceptance run as the gate above.

- [ ] Run the focused test, then commit only the lock, script, and tests.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
/Users/haechan/Desktop/SFEP/analysis/.venv/bin/python -m pytest -q \
  tests/test_python312_restore.py
cd /Users/haechan/Desktop/SFEP
git add analysis/python312-conda.lock scripts/restore-sfep-python312.sh \
  analysis/tests/test_python312_restore.py
git commit -m "build: restore pinned local Python runtime"
```

### Task 1: Freeze the verified v1 migration oracle

**Files:**

- Create: `analysis/tests/test_v2_migration.py`
- Create: `analysis/tests/oracles/v1_execution_trace.py`
- Modify: `analysis/tests/oracles/__init__.py`
- Modify: `analysis/tests/conftest.py`
- Test: `analysis/tests/test_v2_migration.py`

- [ ] Write a failing test that authenticates the existing golden v1 manifest and the local actual v1 manifest before exposing their criteria IDs.

```python
def test_verified_v1_seed_anchors_are_frozen(actual_v1_bundle):
    golden = verify_v1_bundle(GOLDEN_V1_BUNDLE)
    actual = verify_v1_bundle(actual_v1_bundle)
    assert golden.criteria_id == GOLDEN_V1_SEED
    assert actual.criteria_id == ACTUAL_V1_SEED
```

The test helper must verify canonical JSON, fixed v1 schema digests, all six artifact sizes/hashes, 8/19-field identities, and artifact bindings. It must not accept a manifest merely because `criteriaId` has valid syntax.

`actual_v1_bundle` is an explicit fixture in `analysis/tests/conftest.py`: read `SFEP_ACTUAL_BUNDLE`, require an absolute existing directory when set, and skip with that variable name when absent. Never search `var/` or select a newest directory implicitly.

- [ ] Run the focused test and confirm it fails because the independent verifier/trace oracle does not exist.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
SFEP_ACTUAL_BUNDLE=/Users/haechan/Desktop/SFEP/var/equipment-quality/run-a/sha256:65d535350ef2d7c5abf6372fdfe7b364c62a6c0af60b01946a23c4659c1ee82a \
  python -m pytest -q tests/test_v2_migration.py
```

- [ ] Implement the standard-library-only read-only v1 verifier and a trace projection containing rule bootstrap replicate ordinal, sampled Charge ordinal sequence, confusion tuple, valid replicate count, CI, holdout metrics, rule/range/event IDs, ordered event semantic projection, and normalized summary lineage keys.

- [ ] Add a byte freeze assertion over every file under `contracts/equipment-monitor/v1` using literals kept in the test module; do not add or modify a file inside the v1 contract directory.

- [ ] Run the v1 oracle and existing identity/statistics tests.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q \
  tests/test_statistics.py \
  tests/test_artifacts.py
python -m pytest -q tests/test_golden_seal.py \
  -k 'checked_in_golden_bundle or independent_oracle or identity'
SFEP_ACTUAL_BUNDLE=/Users/haechan/Desktop/SFEP/var/equipment-quality/run-a/sha256:65d535350ef2d7c5abf6372fdfe7b364c62a6c0af60b01946a23c4659c1ee82a \
  python -m pytest -q tests/test_v2_migration.py
```

- [ ] Commit the test-only oracle.

```bash
git add analysis/tests/test_v2_migration.py analysis/tests/oracles analysis/tests/conftest.py
git commit -m "test: freeze v1 bundle migration oracle"
```

### Task 2: Add the three v2 schemas and dedicated configs

**Files:**

- Create: `contracts/equipment-monitor/v2/analysis_config.schema.json`
- Create: `contracts/equipment-monitor/v2/analysis_summary.schema.json`
- Create: `contracts/equipment-monitor/v2/bundle_manifest.schema.json`
- Create: `analysis/equipment_quality/contracts/v2/analysis_config.schema.json`
- Create: `analysis/equipment_quality/contracts/v2/analysis_summary.schema.json`
- Create: `analysis/equipment_quality/contracts/v2/bundle_manifest.schema.json`
- Create: `analysis/analysis_config_v2.json`
- Create: `contracts/equipment-monitor/v2/golden-config/analysis_config.json`
- Modify: `analysis/pyproject.toml`
- Modify: `analysis/tests/test_contracts.py`
- Modify: `analysis/tests/test_schema_resources.py`

- [ ] Write failing schema tests for the exact v2 version constants and bootstrap shape.

```python
assert actual_v2_config["schemaVersion"] == "sfep-analysis-config/v2"
assert actual_v2_config["analysisConfigVersion"] == "quality-analysis-v2"
assert actual_v2_config["bootstrap"] == {
    "minimumValidReplicates": 1900,
    "replicates": 2000,
    "seedMaterial": ACTUAL_V1_SEED,
    "seedProtocol": "LEGACY_CRITERIA_ID_UTF8_V1",
}
```

Also assert that v2 summary dependencies accept `config.bootstrap.seedMaterial` and `config.bootstrap.seedProtocol`, while v1 schema bytes and their SHA-256 literals remain unchanged.

- [ ] Run the focused contract tests and confirm missing v2 resources fail.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_contracts.py tests/test_schema_resources.py -k 'v2 or bootstrap or byte_identical'
```

- [ ] Derive v2 schemas from v1 with only these semantic changes:

```text
analysis_config: schemaVersion=/v2, analysisConfigVersion=quality-analysis-v2,
                 bootstrap additionally requires seedMaterial and seedProtocol
analysis_summary: schemaVersion=/v2, config dependency grammar includes both seed fields
bundle_manifest: schemaVersion=/v2, config artifact=/v2, summary artifact=/v2
```

The v2 manifest must still require runtime/range/rule/replay artifact versions ending in `/v1`.

- [ ] Copy the three v2 root schema files byte-for-byte into the Python package and extend package data with `contracts/v2/*.schema.json`.

- [ ] Build actual and golden v2 configs by changing only version fields and adding the approved seed pair. Confirm the original `analysis/analysis_config.json` hash is unchanged.

- [ ] Run schema tests and leave all changes uncommitted for the atomic compatibility commit.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_contracts.py tests/test_schema_resources.py
```

### Task 3: Introduce the Python BundleContract catalog

**Files:**

- Create: `analysis/equipment_quality/bundle_contract.py`
- Modify: `analysis/equipment_quality/schema.py`
- Modify: `analysis/equipment_quality/models.py`
- Modify: `analysis/equipment_quality/artifacts.py`
- Create: `analysis/tests/test_bundle_contract_profiles.py`
- Modify: `analysis/tests/factories/artifacts.py`

- [ ] Write failing tests for two immutable profiles, seven explicit schema roles per profile, exact artifact versions, unknown-version rejection, and cross-profile mixing rejection.

```python
@dataclass(frozen=True)
class BundleContract:
    manifest_version: str
    config_version: str
    summary_version: str
    artifact_versions: Mapping[str, str]
    schema_resources: Mapping[str, SchemaResource]
    criteria_id_namespace: str = "sfep-criteria-id/v1"
    bundle_id_namespace: str = "sfep-bundle-id/v1"
```

For v2, `schema_resources` must explicitly map runtime/ranges/rules/replay to authenticated v1 package resources. It must not infer reuse from a filename or directory fallback.

- [ ] Run the new tests and confirm current module-level v1 constants cannot represent v2.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_bundle_contract_profiles.py tests/test_artifacts.py
```

- [ ] Implement `V1_CONTRACT`, `V2_CONTRACT`, `contract_for_manifest_version()`, and `contract_for_config_version()` as closed allowlist lookups.

- [ ] Keep existing `normative_schema_bytes(name)` and `validate_normative_instance(name, value)` as v1-compatible wrappers. Add explicit APIs instead of silently changing their meaning:

```python
def contract_schema_bytes(contract: BundleContract, role: str) -> bytes: ...
def validate_contract_instance(
    contract: BundleContract, role: str, instance: object
) -> None: ...
```

- [ ] Add the selected manifest version to `BundleWriteRequest` as a validated non-wire selector with v1 as the compatibility default. Route `_validate_schema_descriptors()` and `_prepare_bundle()` through the selected contract without changing either identity function.

- [ ] Prove the v1 request produces the same prepared artifact and manifest bytes as before, then run focused tests.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_bundle_contract_profiles.py tests/test_schema_resources.py
python -m pytest -q tests/test_artifacts.py -k 'identity or bundle or schema'
```

- [ ] Leave the changes uncommitted for Task 8.

### Task 4: Separate bootstrap seed material and bump the worker protocol

**Files:**

- Modify: `analysis/equipment_quality/models.py`
- Modify: `analysis/equipment_quality/statistics.py`
- Modify: `analysis/equipment_quality/quality_intervals.py`
- Modify: `analysis/equipment_quality/_bootstrap_worker.py`
- Modify: `analysis/equipment_quality/summary.py`
- Modify: `analysis/tests/test_statistics.py`
- Modify: `analysis/tests/test_quality_intervals.py`
- Modify: `analysis/tests/test_artifacts.py`
- Modify: `analysis/tests/test_v2_migration.py`

- [ ] Write failing tests that compare v1 criteria-ID seeding with v2 explicit seeding for every sampled Charge ordinal and every final CI.

```python
@dataclass(frozen=True)
class BootstrapSeed:
    protocol: str
    material: str
    lineage_dependencies: tuple[str, ...]

def resolve_bootstrap_seed(config: AnalysisConfig, criteria_id: str) -> BootstrapSeed:
    ...
```

Expected resolution:

```text
v1 → material=criteria_id, dependencies=(identity.criteria_id,)
v2 → material=config.bootstrap.seedMaterial,
     dependencies=(config.bootstrap.seedMaterial, config.bootstrap.seedProtocol)
```

- [ ] Run the seed-focused tests and confirm the current `criteria_id`-named APIs fail the v2 case.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_statistics.py -k charge_bootstrap
python -m pytest -q tests/test_quality_intervals.py -k bootstrap
python -m pytest -q tests/test_artifacts.py -k holdout
python -m pytest -q tests/test_v2_migration.py -k seed
```

- [ ] Add an internal seed-explicit function while preserving the v1 public wrapper.

```python
def charge_bootstrap_rr_ci_with_seed(
    rows: pd.DataFrame,
    seed_material: str,
    rule_id: str,
    *,
    replicates: int,
) -> BootstrapCi:
    seed = hashlib.sha256(
        (seed_material + "\0" + rule_id + "\0rule-ci-v1").encode("utf-8")
    ).digest()
    ...
```

`charge_bootstrap_rr_ci(..., criteria_id, ...)` must remain a v1 wrapper. The holdout formula is `seedMaterial + "\0holdout-bootstrap-v1"` with the same existing RNG loop.

- [ ] Change worker request framing to an explicit protocol discriminator such as `sfep-bootstrap-worker/v2` and rename the payload field to `seedMaterial`. Reject v1/v2 ambiguous tuples rather than guessing their meaning.

- [ ] Pass one resolved `BootstrapSeed` from `run_analysis()` through rule and holdout construction. Do not allow downstream code to recalculate it from the new v2 criteria ID.

- [ ] Run all bootstrap tests and the independent trace comparison.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_statistics.py tests/test_v2_migration.py
python -m pytest -q tests/test_quality_intervals.py -k 'bootstrap or worker'
python -m pytest -q tests/test_artifacts.py -k 'holdout or lineage'
```

- [ ] Leave the changes uncommitted for Task 8.

### Task 5: Emit v2 summary lineage, manifest, and CLI output

**Files:**

- Modify: `analysis/equipment_quality/cli.py`
- Modify: `analysis/equipment_quality/summary.py`
- Modify: `analysis/equipment_quality/artifacts.py`
- Modify: `analysis/equipment_quality/schema.py`
- Modify: `analysis/equipment_quality/models.py`
- Modify: `analysis/tests/test_cli.py`
- Modify: `analysis/tests/test_artifacts.py`
- Modify: `analysis/tests/test_v2_migration.py`

- [ ] Write failing tests for v2 config selection, v2 summary/manifest versions, v2 artifact version matrix, and the exact lineage allowlist.

The keyed lineage comparison may differ only as follows:

```text
+ analysis_config.bootstrap.seedMaterial field record
+ analysis_config.bootstrap.seedProtocol field record
rule-CI/holdout dependency: identity.criteria_id
                         → config.bootstrap.seedMaterial + config.bootstrap.seedProtocol
```

All other field records, conversion/filter/transformation tokens, dependencies, aggregates, materials, and populations must compare exactly.

- [ ] Run focused tests and confirm v1-only `_SCHEMA_ROLE_NAMES`, `_ARTIFACT_SCHEMA_VERSIONS`, and summary constants fail.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_cli.py tests/test_artifacts.py tests/test_v2_migration.py -k 'v2 or lineage or bundle'
```

- [ ] Select the contract immediately after strict config JSON version extraction, validate with that profile, then pass the same immutable contract through schema digest calculation, summary generation, and artifact publication.

- [ ] Preserve `run_analysis(config_path, runtime_path, data_dir, output_dir)` and CLI flags. Version selection comes from the authenticated config, not a new user-provided `--version` switch.

- [ ] Ensure v2 output has config/summary/manifest `/v2`, the other artifact versions `/v1`, and the unchanged 8/19-field ID algorithms.

- [ ] Add a publication preflight that compares the optimized/reference semantic projection before final rename when a reference is explicitly supplied by the migration test; production CLI must not guess a reference path.

- [ ] Run CLI, artifact, and v1 golden regression tests.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q \
  tests/test_cli.py \
  tests/test_artifacts.py \
  tests/test_golden_production_parity.py \
  tests/test_v2_migration.py
```

- [ ] Leave the changes uncommitted for Task 8.

### Task 6: Add a sealed golden v2 fixture and strict semantic comparator

**Files:**

- Create: `analysis/tools/seal_golden_bundle_v2.py`
- Modify: `analysis/pyproject.toml`
- Modify: `analysis/tools/build_producer.py`
- Modify: `analysis/tools/seal_producer_build.py`
- Modify: `analysis/tools/seal_runtime.py`
- Modify: `analysis/producer.lock`
- Modify: `analysis/producer_runtime.json`
- Create: `contracts/equipment-monitor/v2/golden-bundle/analysis_config.json`
- Create: `contracts/equipment-monitor/v2/golden-bundle/producer_runtime.json`
- Create: `contracts/equipment-monitor/v2/golden-bundle/equipment_operating_ranges.json`
- Create: `contracts/equipment-monitor/v2/golden-bundle/quality_risk_intervals.json`
- Create: `contracts/equipment-monitor/v2/golden-bundle/replay_events.csv`
- Create: `contracts/equipment-monitor/v2/golden-bundle/analysis_summary.json`
- Create: `contracts/equipment-monitor/v2/golden-bundle/bundle_manifest.json`
- Create: `analysis/tests/oracles/bundle_semantic_comparator.py`
- Modify: `analysis/tests/test_golden_seal.py`
- Modify: `analysis/tests/test_runtime_identity.py`
- Modify: `analysis/tests/test_v2_migration.py`

- [ ] Write failing tests that require the v2 seal tool to be standard-library-only, read-only with respect to v1, deterministic, no-clobber, and byte-identical on two runs.

- [ ] Decouple immutable golden inputs from the mutable current producer manifest. Refactor golden test helpers to use these exact authenticated files:

```text
v1 golden seal input:
  contracts/equipment-monitor/v1/golden-bundle/producer_runtime.json
v2 golden seal input after initial 1.1 publication:
  contracts/equipment-monitor/v2/golden-bundle/producer_runtime.json
current producer identity/actual generation only:
  analysis/producer_runtime.json
```

Add a failing test that replaces the root current manifest with valid-but-different bytes and proves both golden oracles still select their embedded immutable runtime inputs. Keep current-runtime validation in `test_runtime_identity.py`; never weaken a golden expected value merely to accept current producer `1.1.0` or later `1.2.0`.

- [ ] Write the strict comparator before the fixture. Replay normalization is exactly:

```python
def normalized_event(row: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    copy = dict(row)
    copy["bundle_id"] = "<bundle>"
    copy["criteria_id"] = "<criteria>"
    return tuple(copy.items())
```

It must still require identical row count/order, `schema_version`, `event_id`, all semantic columns, and canonical `values_json` bytes. It must compare rule/range/material/batch/equipment-batch IDs, bootstrap samples, metrics, CIs, grades, holdout results, and alerts exactly.

- [ ] Run tests and confirm the missing fixture/tool fails.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_golden_seal.py tests/test_v2_migration.py -k 'v2 or semantic'
```

- [ ] Before producing v2, rerun Task 0's idempotent authenticated restore CLI and require its version/stdlib probes, then bump the compatibility producer from `1.0.0` to `1.1.0` in package/build/seal literals. Never seal with a damaged, unverified, or different interpreter.

- [ ] Build the `1.1.0` wheel twice with `analysis/tools/build_producer.py`, compare archives in `seal_producer_build.py`, publish only the verified wheel to a temporary wheelhouse, update `analysis/producer.lock`, install it with checked-in hashes into a clean runtime venv, and seal `analysis/producer_runtime.json` with `seal_runtime.py`. Tests must reject a manifest copied from the v1 fixture or a source/wheel/tree digest that does not match the installed compatibility code.

```bash
cd /Users/haechan/Desktop/SFEP
SFEP_COMPAT_ROOT="$(mktemp -d /private/tmp/sfep-compat-v2.XXXXXX)"
SFEP_COMPAT_BUILD="$SFEP_COMPAT_ROOT/build-venv"
SFEP_COMPAT_RUNTIME="$SFEP_COMPAT_ROOT/runtime-venv"
SFEP_COMPAT_WORK="$SFEP_COMPAT_ROOT/wheel-work"
SFEP_COMPAT_WHEELHOUSE="$SFEP_COMPAT_ROOT/verified-wheelhouse"
SFEP_PINNED_PYTHON=/private/tmp/sfep-python-3.12.10/bin/python3.12
SFEP_TEST_PYTHON=/Users/haechan/Desktop/SFEP/analysis/.venv/bin/python
test "$($SFEP_PINNED_PYTHON --version)" = "Python 3.12.10"
"$SFEP_PINNED_PYTHON" -c 'import encodings, pathlib; assert pathlib.Path(encodings.__file__).is_file()'
test "$($SFEP_TEST_PYTHON --version)" = "Python 3.12.10"
mkdir "$SFEP_COMPAT_WHEELHOUSE"
find /Users/haechan/Desktop/SFEP/analysis/.wheelhouse -maxdepth 1 -type f -name '*.whl' \
  ! -name 'sfep_equipment_quality-*.whl' -exec cp {} "$SFEP_COMPAT_WHEELHOUSE" \;
"$SFEP_PINNED_PYTHON" -m venv "$SFEP_COMPAT_BUILD"
"$SFEP_COMPAT_BUILD/bin/python" -m pip install --no-index --only-binary=:all: \
  --require-hashes --find-links "$SFEP_COMPAT_WHEELHOUSE" \
  -r /Users/haechan/Desktop/SFEP/analysis/bootstrap.lock
"$SFEP_COMPAT_BUILD/bin/python" -m pip install --no-index --only-binary=:all: \
  --require-hashes --find-links "$SFEP_COMPAT_WHEELHOUSE" \
  -r /Users/haechan/Desktop/SFEP/analysis/build-requirements.lock
SOURCE_DATE_EPOCH=1735689600 "$SFEP_COMPAT_BUILD/bin/python" \
  /Users/haechan/Desktop/SFEP/analysis/tools/build_producer.py \
  --source-root /Users/haechan/Desktop/SFEP/analysis \
  --build-python "$SFEP_COMPAT_BUILD/bin/python" \
  --work-root "$SFEP_COMPAT_WORK"
"$SFEP_COMPAT_BUILD/bin/python" \
  /Users/haechan/Desktop/SFEP/analysis/tools/seal_producer_build.py \
  --source-root /Users/haechan/Desktop/SFEP/analysis \
  --wheel-dir-a "$SFEP_COMPAT_WORK/wheel-a" \
  --wheel-dir-b "$SFEP_COMPAT_WORK/wheel-b" \
  --wheelhouse "$SFEP_COMPAT_WHEELHOUSE" \
  --producer-lock /Users/haechan/Desktop/SFEP/analysis/producer.lock
"$SFEP_PINNED_PYTHON" -m venv "$SFEP_COMPAT_RUNTIME"
"$SFEP_COMPAT_RUNTIME/bin/python" -m pip install --no-index --only-binary=:all: \
  --require-hashes --find-links "$SFEP_COMPAT_WHEELHOUSE" \
  -r /Users/haechan/Desktop/SFEP/analysis/bootstrap.lock
"$SFEP_COMPAT_RUNTIME/bin/python" -m pip install --no-index --only-binary=:all: \
  --require-hashes --find-links "$SFEP_COMPAT_WHEELHOUSE" \
  -r /Users/haechan/Desktop/SFEP/analysis/requirements.lock
"$SFEP_COMPAT_RUNTIME/bin/python" -m pip install --no-index --only-binary=:all: \
  --require-hashes --find-links "$SFEP_COMPAT_WHEELHOUSE" \
  -r /Users/haechan/Desktop/SFEP/analysis/producer.lock
PYTHONHASHSEED=0 TZ=Asia/Seoul "$SFEP_COMPAT_BUILD/bin/python" \
  /Users/haechan/Desktop/SFEP/analysis/tools/seal_runtime.py \
  --source-root /Users/haechan/Desktop/SFEP/analysis \
  --producer-wheel-dir "$SFEP_COMPAT_WHEELHOUSE" \
  --producer-lock /Users/haechan/Desktop/SFEP/analysis/producer.lock \
  --runtime-venv "$SFEP_COMPAT_RUNTIME" \
  --output /Users/haechan/Desktop/SFEP/analysis/producer_runtime.json
```

Keep all `SFEP_COMPAT_*` values and `SFEP_TEST_PYTHON` in the same shell until both fixture generations and their tests finish.

- [ ] Implement the v2 seal as a separate exact CLI executed by `"$SFEP_COMPAT_RUNTIME/bin/python"`. It must first independently verify the checked-in golden v1 Bundle, assert the v2 golden seed equals the verified v1 criteria ID, build only allowlisted version/seed/lineage differences, use the newly sealed `1.1.0` runtime artifact, recompute honest v2 IDs/digests, and publish atomically.

- [ ] Seal twice into distinct temporary directories, compare every output byte, then make a third no-clobber publication to the new checked-in v2 directory and compare it to the temporary oracle. Do not overwrite or regenerate `contracts/equipment-monitor/v1/golden-bundle`.

```bash
SFEP_GOLDEN_V2_A="$SFEP_COMPAT_ROOT/golden-v2-a"
SFEP_GOLDEN_V2_B="$SFEP_COMPAT_ROOT/golden-v2-b"
for SFEP_GOLDEN_V2_TARGET in "$SFEP_GOLDEN_V2_A" "$SFEP_GOLDEN_V2_B"; do
  PYTHONHASHSEED=0 TZ=Asia/Seoul "$SFEP_COMPAT_RUNTIME/bin/python" \
    /Users/haechan/Desktop/SFEP/analysis/tools/seal_golden_bundle_v2.py \
    --contract-root /Users/haechan/Desktop/SFEP/contracts/equipment-monitor/v1 \
    --analysis-config /Users/haechan/Desktop/SFEP/contracts/equipment-monitor/v2/golden-config/analysis_config.json \
    --runtime-manifest /Users/haechan/Desktop/SFEP/analysis/producer_runtime.json \
    --output-dir "$SFEP_GOLDEN_V2_TARGET"
done
diff -qr "$SFEP_GOLDEN_V2_A" "$SFEP_GOLDEN_V2_B"
test ! -e /Users/haechan/Desktop/SFEP/contracts/equipment-monitor/v2/golden-bundle
PYTHONHASHSEED=0 TZ=Asia/Seoul "$SFEP_COMPAT_RUNTIME/bin/python" \
  /Users/haechan/Desktop/SFEP/analysis/tools/seal_golden_bundle_v2.py \
  --contract-root /Users/haechan/Desktop/SFEP/contracts/equipment-monitor/v1 \
  --analysis-config /Users/haechan/Desktop/SFEP/contracts/equipment-monitor/v2/golden-config/analysis_config.json \
  --runtime-manifest /Users/haechan/Desktop/SFEP/analysis/producer_runtime.json \
  --output-dir /Users/haechan/Desktop/SFEP/contracts/equipment-monitor/v2/golden-bundle
diff -qr "$SFEP_GOLDEN_V2_A" \
  /Users/haechan/Desktop/SFEP/contracts/equipment-monitor/v2/golden-bundle
SFEP_GOLDEN_V2_EMBEDDED="$SFEP_COMPAT_ROOT/golden-v2-embedded-input"
PYTHONHASHSEED=0 TZ=Asia/Seoul "$SFEP_COMPAT_RUNTIME/bin/python" \
  /Users/haechan/Desktop/SFEP/analysis/tools/seal_golden_bundle_v2.py \
  --contract-root /Users/haechan/Desktop/SFEP/contracts/equipment-monitor/v1 \
  --analysis-config /Users/haechan/Desktop/SFEP/contracts/equipment-monitor/v2/golden-config/analysis_config.json \
  --runtime-manifest /Users/haechan/Desktop/SFEP/contracts/equipment-monitor/v2/golden-bundle/producer_runtime.json \
  --output-dir "$SFEP_GOLDEN_V2_EMBEDDED"
diff -qr "$SFEP_GOLDEN_V2_A" "$SFEP_GOLDEN_V2_EMBEDDED"
```

The root `analysis/producer_runtime.json` is permitted only for the first bootstrap publication above. From this embedded-input equality onward, every v1/v2 golden reseal test uses its own checked-in Bundle runtime artifact. Actual-v2 production continues to use the mutable current root manifest.

- [ ] Run v1/v2 golden parity and tamper tests.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
"$SFEP_TEST_PYTHON" -m pytest -q \
  tests/test_v2_migration.py \
  tests/test_golden_production_parity.py \
  tests/test_runtime_identity.py
"$SFEP_TEST_PYTHON" -m pytest -q tests/test_golden_seal.py -k 'golden_bundle or v2'
```

- [ ] Leave the changes uncommitted for Task 8.

### Task 7: Implement the Java allowlisted BundleContract loader

**Files:**

- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/bundle/BundleContract.java`
- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/bundle/BundleContracts.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/bundle/ArtifactRole.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/bundle/SchemaRole.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/bundle/EmbeddedSchemas.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/bundle/UntrustedManifestClaims.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/bundle/BundleLoader.java`
- Modify: `equipment-monitor/build.gradle`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/bundle/EmbeddedSchemasTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/bundle/BundleLoaderIntegrationTest.java`
- Create: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/bundle/BundleContractV2IntegrationTest.java`

- [ ] Write failing tests for both profiles, seven explicit schema documents, unknown manifest rejection, no fallback, and v1/v2 version/digest mixing.

```java
public record BundleContract(
        String manifestVersion,
        String criteriaIdNamespace,
        String bundleIdNamespace,
        Map<ArtifactRole, String> artifactVersions,
        Map<SchemaRole, SchemaDocument> schemas) {
}
```

- [ ] Add precedence tests proving an artifact hash mismatch is reported before malformed artifact JSON/schema errors, and a bad profile claim is reported before artifact reads.

- [ ] Run the focused loader tests and confirm the v1-global `ArtifactRole.schemaVersion()` and one-root `EmbeddedSchemas` design fail the v2 cases.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.bundle.EmbeddedSchemasTest \
  --tests com.sfep.equipmentmonitor.bundle.BundleLoaderIntegrationTest \
  --tests com.sfep.equipmentmonitor.bundle.BundleContractV2IntegrationTest
```

- [ ] Make `SchemaRole` a logical role only. Make each profile own its complete seven-role `SchemaDocument` map; v2 explicitly points four reused roles to authenticated v1 bytes.

- [ ] Extend `processResources` and `verifyNormativeSchemas` to copy/compare v1 seven files and v2 three files at their exact resource paths. Do not add a second Gradle wrapper.

- [ ] Refactor `BundleLoader.load()` to this exact order:

```text
BundleLayout.preflight
→ parse manifest strictly
→ UntrustedManifestClaims extracts only schemaVersion and closed claims
→ BundleContracts.require(schemaVersion)
→ verify profile schema digests/artifact versions/ID namespaces/bindings
→ verify every artifact size and SHA-256
→ validate manifest and artifacts with selected embedded schemas
→ bind DTOs and construct LoadedBundle
```

- [ ] Replace global role version lookups with `contract.artifactVersion(role)`. Keep config bootstrap as existing `JsonNode`; do not leak seed fields into Swing/domain DTOs.

- [ ] Run loader, path-safety, ID, and fixture tests.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor verifyNormativeSchemas test \
  --tests com.sfep.equipmentmonitor.bundle.EmbeddedSchemasTest \
  --tests com.sfep.equipmentmonitor.bundle.BundleLoaderIntegrationTest \
  --tests com.sfep.equipmentmonitor.bundle.BundleContractV2IntegrationTest \
  --tests com.sfep.equipmentmonitor.bundle.BundlePathSafetyTest \
  --tests com.sfep.equipmentmonitor.bundle.IdLinesTest
```

- [ ] Leave the changes uncommitted for Task 8.

### Task 8: Add explicit verification lanes and commit the atomic compatibility change

**Files:**

- Modify: `equipment-monitor/build.gradle`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/bundle/ActualBundleLoaderContractTest.java`
- Create: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/bundle/ActualBundleV2ParityTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/risk/ActualRiskDefinitionsTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/state/ActualHistoricalReplayTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/ActualDesktopMonitorUiTest.java`
- Modify: `analysis/pyproject.toml`
- Test: all files changed in Tasks 2~8

- [ ] Add distinct JUnit tags `actual-bundle-v1` and `actual-bundle-v2-parity`.

- [ ] Add Gradle tasks with fail-fast environment validation:

```groovy
tasks.register('actualBundleV2Test', Test) {
    testClassesDirs = sourceSets.test.output.classesDirs
    classpath = sourceSets.test.runtimeClasspath
    dependsOn verifyNormativeSchemas, verifyRuntimeShape
    systemProperty 'sfep.repo-root', repositoryRoot.asFile.absolutePath
    useJUnitPlatform { includeTags 'actual-bundle-v2-parity' }
    doFirst {
        ['SFEP_ACTUAL_BUNDLE', 'SFEP_ACTUAL_BUNDLE_V2'].each { name ->
            if (!System.getenv(name)) throw new GradleException("Missing ${name}")
        }
        systemProperty 'sfep.actual-bundle', System.getenv('SFEP_ACTUAL_BUNDLE')
        systemProperty 'sfep.actual-bundle-v2', System.getenv('SFEP_ACTUAL_BUNDLE_V2')
    }
}
```

Apply the same `testClassesDirs`, `classpath`, verification dependencies, repository-root property, and named environment validation to every custom `Test` task. `actualBundleTest` includes only the v1 tag and requires only `SFEP_ACTUAL_BUNDLE`. Default `test` keeps existing assumption-based skip behavior. `fastTest` excludes both actual tags. Add a configuration test or Gradle TestKit assertion so a custom task cannot silently have an empty classpath.

- [ ] Write actual parity assertions using the same strict normalization as Python. The test must compare complete rule/range models, replay order and event IDs, material histories, evidence/counts/grades, and alerts while normalizing only bundle/criteria binding values.

- [ ] Run Python contract/migration tests.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q \
  tests/test_bundle_contract_profiles.py \
  tests/test_contracts.py \
  tests/test_schema_resources.py \
  tests/test_statistics.py \
  tests/test_artifacts.py \
  tests/test_cli.py \
  tests/test_v2_migration.py
python -m pytest -q tests/test_quality_intervals.py -k 'bootstrap or worker'
python -m pytest -q tests/test_golden_seal.py -k 'golden_bundle or v2'
```

- [ ] Run Java fast/default tests and verify existing golden/actual v1 IDs are unchanged.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor fastTest
./sfep-server/gradlew -p equipment-monitor test
SFEP_ACTUAL_BUNDLE=/Users/haechan/Desktop/SFEP/var/equipment-quality/run-a/sha256:65d535350ef2d7c5abf6372fdfe7b364c62a6c0af60b01946a23c4659c1ee82a \
  ./sfep-server/gradlew -p equipment-monitor actualBundleTest
```

The actual-v1 lane is executable in this plan and must pass. `actualBundleV2Test` is registered and its fail-fast behavior is tested here, but its real-data parity execution is deliberately deferred until Python optimization Task 10 produces the honest `1.2.0` actual v2 Bundle; Java/Swing optimization Task 10 performs that final lane. Do not point the v2 lane at the golden fixture or fabricate an actual-v2 path.

- [ ] Scan the entire staged diff. Confirm no file below `contracts/equipment-monitor/v1` changed, no `var/` artifact is staged, and all Tasks 2~8 are present together.

```bash
cd /Users/haechan/Desktop/SFEP
git diff --check
git diff --name-only --cached
git status --short
```

- [ ] Commit the complete Python+Java compatibility slice as one local commit.

```bash
git add \
  analysis/analysis_config_v2.json \
  analysis/equipment_quality \
  analysis/pyproject.toml analysis/producer.lock analysis/producer_runtime.json \
  analysis/tests \
  analysis/tools/build_producer.py analysis/tools/seal_producer_build.py \
  analysis/tools/seal_runtime.py analysis/tools/seal_golden_bundle_v2.py \
  contracts/equipment-monitor/v2 \
  equipment-monitor
git commit -m "feat: add seed-aware Bundle v2 compatibility"
```

- [ ] Do not push. Record the commit hash and the absolute authenticated compatibility interpreter as `SFEP_BENCHMARK_RUNTIME_PYTHON` in the execution handoff. If the temporary runtime will not survive the next task, include the exact `producer.lock` reconstruction command instead.

## Completion Gate

- The pinned Python 3.12.10 prefix is reproducibly restored from the exact local 18-archive SHA-256 lock with Conda offline mode; the damaged copy is retained and no network source is used.
- Existing checked-in and actual v1 Bundles verify with exactly the old IDs and bytes.
- A v2 golden fixture produced by the honest compatibility runtime `1.1.0` loads in Python and Java with the approved mixed artifact-version matrix.
- v1 and v2 golden reseal oracles use their embedded immutable runtime manifests; mutable root producer `1.1.0`/`1.2.0` metadata is used only for current runtime and actual generation.
- Every v1 rule/holdout bootstrap sample equals its v2 explicit-seed counterpart.
- v1↔v2 semantic comparison permits only the documented version/provenance/binding/lineage differences.
- Unknown or mixed contracts fail without fallback and before unauthenticated artifact parsing.
- The unchanged actual v1 lane passes; the actual v2 lane is registered/fail-fast tested and explicitly handed to the final Java/Swing gate after optimized producer `1.2.0` sealing.
- Contract support is one local compatibility commit; no remote Push is performed.
