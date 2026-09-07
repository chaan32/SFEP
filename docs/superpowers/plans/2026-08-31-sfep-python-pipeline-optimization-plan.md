# SFEP Python Pipeline Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** v2 Bundle의 통계·wire 의미와 결정론을 v1 reference와 정확히 유지하면서 실제 Python 생성 파이프라인의 반복 계산과 최대 메모리를 줄이고, 정직한 최적화 producer runtime으로 actual v2 Bundle을 두 번 동일하게 봉인한다.

**Architecture:** 계약 이행은 선행 v2 migration 계획에 맡긴다. 이 계획은 한 실행 안에서만 사는 `ChargeConfusionStats`, `PredicateMaskCache`, `ColumnSummaryCache`와 열 지향 genealogy/event 경로를 순서대로 적용한다. 각 최적화는 reference 경로와 byte/trace parity를 통과한 뒤에만 유지하며, 보안 I/O와 canonical float 경로는 측정 기반 조건부 단계로 격리한다.

**Tech Stack:** Python 3.12.10, pandas 2.3.0, NumPy 2.2.6, jsonschema 4.24.0, pytest 8.4.1, standard-library profiling, `/usr/bin/time` on macOS.

**Spec:** `docs/superpowers/specs/2026-08-30-sfep-python-pipeline-optimization-design.md`

## Global Constraints

- 선행 계획 `docs/superpowers/plans/2026-08-31-sfep-bundle-v2-seed-migration-plan.md`의 authenticated local Python 복구, 호환성 커밋, 모든 테스트가 먼저 통과해야 한다.
- 통계식, candidate, split, label maturity, 2,000 bootstrap, RNG 호출 순서, rule grade, holdout 결과, ID preimage, canonical JSON/CSV를 바꾸지 않는다.
- cache는 실행-local이고 불변 snapshot만 보유한다. module global, disk cache, Bundle artifact에는 기록하지 않는다.
- 최적화 on/off 비교에서 identity-normalized 의미가 다르면 해당 변경은 폐기한다.
- source streaming과 canonical float cache는 각각 최대 RSS 또는 wall-time이 10% 이상 개선되고 안전성/정확성 테스트가 통과할 때만 커밋한다.
- 실제 데이터는 `SFEP_STEEL_DATA_DIR`에서만 읽고 새 데이터는 요구하지 않는다.
- 승인된 v1 Bundle과 v1 golden은 수정하지 않는다. actual v2 출력은 `var/equipment-quality/v2-*` 아래에 두고 커밋하지 않는다.
- 각 성능 수치는 같은 장비·runtime·selection에서 cold 1회와 warm 2회 또는 warm-up 후 3회 중앙값으로 기록한다.
- 원격 저장소에 Push하지 않는다.

## Dependency Map

```text
Task 1 reference harness
 ├─→ Task 2 holdout one-pass
 ├─→ Task 3 predicate masks
 ├─→ Task 4 column summaries
 └─→ Task 5 genealogy columns → Task 6 event columns
                                      ↓
                         Task 7 conditional source streaming
                                      ↓
                         Task 8 conditional float cache
                                      ↓
                         Task 9 full parity/performance gate
                                      ↓
                         Task 10 honest runtime + actual v2 seal
```

### Task 1: Freeze the benchmark selection and reference execution trace

**Files:**

- Create: `analysis/tools/benchmark_pipeline.py`
- Create: `analysis/tests/performance/__init__.py`
- Create: `analysis/tests/performance/reference_selection.txt`
- Create: `analysis/tests/test_pipeline_reference_trace.py`
- Modify: `analysis/tests/test_v2_migration.py`
- Modify: `analysis/tests/conftest.py`
- Modify: `analysis/pyproject.toml`
- Create: `docs/performance/2026-08-31-sfep-python-baseline.md`

- [ ] Write a failing test that requires the benchmark harness to emit one canonical JSON record with `case`, `wallSeconds`, `maxRssBytes`, `pythonVersion`, `sourceDigests`, `bundleContract`, and a semantic-trace digest.

```python
assert set(record) == {
    "bundleContract", "case", "maxRssBytes", "pythonVersion",
    "semanticTraceSha256", "sourceDigests", "wallSeconds",
}
```

- [ ] Run the test and confirm the harness does not exist.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_pipeline_reference_trace.py
```

- [ ] Implement a read-only harness with these exact named cases:

```text
quality-rules-golden
holdout-golden
events-golden
actual-phase
actual-events
actual-full-v2
```

The harness must call production entry points, write measurement JSON only to an explicit output path, and never update expected values. It must derive the trace through `tests/oracles/bundle_semantic_comparator.py` rather than timing-only success. `actual-phase` and `actual-events` read `SFEP_STEEL_DATA_DIR` and invoke named internal production stages without artifact publication or runtime-manifest verification. `actual-full-v2` always spawns the absolute interpreter in `SFEP_BENCHMARK_RUNTIME_PYTHON`, authenticates it against `analysis/producer_runtime.json`, and invokes the public CLI: Task 1 uses the sealed compatibility producer `1.1.0` for the full baseline, Tasks 2~9 do not use this case because that installed wheel would not contain their source edits, and Task 10 reruns it with optimized producer `1.2.0`.

- [ ] Freeze the original 1,390-test selection in `reference_selection.txt` as one exact collected pytest node ID per line—no `-k` expressions, globs, file-only patterns, or comments—so later added tests do not distort the 553.18-second baseline. Add a harness test that validates every line against `pytest --collect-only`.

- [ ] Add an `actual_snapshot` marker and `actual_snapshot_dir` fixture that require only the absolute `SFEP_STEEL_DATA_DIR`. Use them for pre-seal differential tests of data-loading, genealogy, and event phases. Keep the existing `real_data` fixture/lane unchanged: it additionally requires both `SFEP_RUNTIME_PYTHON_A` and `SFEP_RUNTIME_PYTHON_B` and is reserved for Task 10.

- [ ] Record existing evidence without claiming a fresh run when the pinned runtime is unavailable:

```text
full reference selection: 553.18s, 1,390 passed, 9 skipped
actual event generation: 168.63s
quality paths: 20.61s to 28.11s
```

Clearly label these as pre-plan observed baselines. The implementation session must append fresh before/after measurements from the restored pinned runtime.

- [ ] Reuse the absolute `1.1.0` compatibility runtime handed off by the contract plan, or reconstruct a clean one from its sealed `producer.lock` with the same pinned-runtime install steps. Assert it authenticates `analysis/producer_runtime.json`, then freeze the full actual-v2 baseline without changing source or artifacts.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
SFEP_TEST_PYTHON=/Users/haechan/Desktop/SFEP/analysis/.venv/bin/python
test -n "$SFEP_BENCHMARK_RUNTIME_PYTHON"
test -x "$SFEP_BENCHMARK_RUNTIME_PYTHON"
test "$($SFEP_TEST_PYTHON --version)" = "Python 3.12.10"
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data \
SFEP_BENCHMARK_RUNTIME_PYTHON="$SFEP_BENCHMARK_RUNTIME_PYTHON" \
  "$SFEP_TEST_PYTHON" tools/benchmark_pipeline.py --case actual-full-v2 --cold 1 --warm 2 \
  --output /private/tmp/sfep-full-compat-baseline.json
```

- [ ] Run trace tests and commit the harness.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_pipeline_reference_trace.py tests/test_v2_migration.py
cd /Users/haechan/Desktop/SFEP
git add analysis/tools/benchmark_pipeline.py analysis/tests/performance \
  analysis/tests/test_pipeline_reference_trace.py analysis/tests/test_v2_migration.py \
  analysis/tests/conftest.py analysis/pyproject.toml \
  docs/performance/2026-08-31-sfep-python-baseline.md
git commit -m "test: add Python pipeline parity benchmarks"
```

### Task 2: Replace holdout row resampling with ChargeConfusionStats

**Files:**

- Create: `analysis/equipment_quality/holdout.py`
- Modify: `analysis/equipment_quality/summary.py`
- Modify: `analysis/tests/test_artifacts.py`
- Create: `analysis/tests/test_holdout_optimization.py`
- Modify: `analysis/tests/test_pipeline_reference_trace.py`

- [ ] Write failing prefix-parity tests that compare the reference row-based holdout path and the new integer aggregate path for empty, single-Charge, zero-denominator, 1,899-valid, 1,900-valid, and mixed-Charge fixtures.

```python
@dataclass(frozen=True)
class ChargeConfusionStats:
    charge_id: str
    alert_profile: str
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    alert_count: int
    defect_count: int
    row_count: int
```

The profile is an explicit closed value such as `DANGER` or `CAUTION_OR_DANGER`; the two thresholds must never share an aggregate table accidentally. The test oracle must compare every replicate's sampled Charge ordinal sequence, summed confusion tuple, six metric values/reasons, valid replicate count, and CI for each profile independently.

- [ ] Run focused tests and confirm `holdout_metrics()` still rebuilds sampled row lists.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_holdout_optimization.py tests/test_artifacts.py -k holdout
```

- [ ] Implement one stable Charge table from canonical holdout row order. Bootstrap must call the existing `deterministic_sample_indices()` once per replicate with exactly the same population size and consume ordinals in the same loop order.

- [ ] Keep point estimates on the same confusion-count functions. Do not vectorize RNG generation or combine metric formulas.

- [ ] Make `summary.holdout_metrics()` build the two alert profiles in one history/rule traversal and reuse their immutable per-Charge tables.

- [ ] Run parity and benchmark cases. Keep the change only if semantics are exact and the focused median improves by at least 10%.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_holdout_optimization.py
python -m pytest -q tests/test_artifacts.py -k 'holdout or lineage'
python -m pytest -q tests/test_v2_migration.py -k holdout
python tools/benchmark_pipeline.py --case holdout-golden --repeat 3 --output /private/tmp/sfep-holdout-after.json
```

- [ ] Commit the independently revertible optimization.

```bash
cd /Users/haechan/Desktop/SFEP
git add analysis/equipment_quality/holdout.py analysis/equipment_quality/summary.py \
  analysis/tests/test_artifacts.py analysis/tests/test_holdout_optimization.py \
  analysis/tests/test_pipeline_reference_trace.py
git commit -m "perf: aggregate holdout bootstrap by Charge"
```

### Task 3: Cache predicate and validity masks per population

**Files:**

- Modify: `analysis/equipment_quality/quality_intervals.py`
- Create: `analysis/tests/test_predicate_mask_cache.py`
- Modify: `analysis/tests/test_quality_intervals.py`
- Modify: `analysis/tests/test_pipeline_reference_trace.py`

- [ ] Write failing tests for cache key completeness, population separation, stable row ordinals, immutable results, collision rejection, and cache-enabled/reference wire equality.

```python
@dataclass(frozen=True)
class MaskKey:
    population_id: str
    analysis_family: str
    field_names: tuple[str, ...]
    predicate_bytes: bytes
    application_context_bytes: bytes
    equipment_scope: tuple[str, str]
    validity_policy: str
```

- [ ] Add a test that gives discovery and confirmation equal-length frames with different rows and proves no mask is shared. Add a test that mutates the caller frame after snapshot and proves cached results do not change.

- [ ] Run tests and confirm `_predicate_mask()` and `_eligible_mask()` recompute for candidate generation and `_metric()`.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_predicate_mask_cache.py
python -m pytest -q tests/test_quality_intervals.py -k 'mask or deterministic or rules'
```

- [ ] Implement an execution-local `PredicateMaskCache` over `_Rows`. Cache canonical tuple/NumPy boolean snapshots but expose no mutable array. Validate row count and a stable population token on every lookup.

- [ ] Thread one cache through candidate generation, discovery metrics, confirmation metrics, and interactions. Preserve the existing `_StrataCache` and do not merge their meanings.

- [ ] Provide a test-only `use_mask_cache=False` reference switch that cannot be set through production CLI and compare complete rule wire bytes on golden and synthetic fixtures.

- [ ] Run focused parity/benchmark tests and retain only on exact equality plus at least 10% focused median improvement.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_predicate_mask_cache.py tests/test_quality_intervals.py
python -m pytest -q tests/test_v2_migration.py -k 'rule or bootstrap or mask'
python tools/benchmark_pipeline.py --case quality-rules-golden --repeat 3 --output /private/tmp/sfep-rules-after.json
```

- [ ] Commit.

```bash
cd /Users/haechan/Desktop/SFEP
git add analysis/equipment_quality/quality_intervals.py \
  analysis/tests/test_predicate_mask_cache.py analysis/tests/test_quality_intervals.py \
  analysis/tests/test_pipeline_reference_trace.py
git commit -m "perf: reuse quality predicate masks"
```

### Task 4: Reuse immutable column summaries

**Files:**

- Modify: `analysis/equipment_quality/summary.py`
- Create: `analysis/tests/test_column_summary_cache.py`
- Modify: `analysis/tests/test_artifacts.py`
- Modify: `analysis/tests/test_contracts.py`

- [ ] Write failing tests that compare cache/reference results for numeric, categorical, date, identifier, all-missing, signed-zero, duplicate, and boundary-quantile columns.

```python
@dataclass(frozen=True)
class ColumnSummary:
    row_count: int
    missing_count: int
    unique_count: int
    finite_values: tuple[float, ...]
    categorical_counts: tuple[tuple[object, int], ...]
```

- [ ] Require cache keys to contain population identity, field, data type, and the exact stage-availability projection. A retrospective holdout column must never reuse a reference-at-T column.

- [ ] Run focused tests and confirm source profiles/drift repeatedly materialize records or scan columns.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_column_summary_cache.py
python -m pytest -q tests/test_artifacts.py -k 'profile or drift'
```

- [ ] Implement `ColumnSummaryCache` local to `build_summary(request)`. Reuse the existing type-1 quantile/median and canonical scalar ordering functions; cache inputs, not final JSON spelling shortcuts.

- [ ] Remove field-loop `to_dict(orient="records")` calls. Read only the requested Series and stable row ordinal arrays.

- [ ] Compare complete summary canonical bytes with cache disabled/enabled, including lineage order and every float token.

- [ ] Run tests and benchmark.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_column_summary_cache.py
python -m pytest -q tests/test_artifacts.py tests/test_contracts.py \
  -k 'summary or profile or drift or lineage'
python -m pytest -q tests/test_v2_migration.py -k 'summary or profile or drift or lineage'
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data \
  python tools/benchmark_pipeline.py --case actual-phase --phase summary --repeat 3 --output /private/tmp/sfep-summary-after.json
```

- [ ] Commit if the focused median improves at least 10% with exact bytes.

```bash
cd /Users/haechan/Desktop/SFEP
git add analysis/equipment_quality/summary.py analysis/tests/test_column_summary_cache.py \
  analysis/tests/test_artifacts.py analysis/tests/test_contracts.py
git commit -m "perf: cache immutable column summaries"
```

### Task 5: Make genealogy construction column-oriented

**Files:**

- Modify: `analysis/equipment_quality/genealogy.py`
- Modify: `analysis/tests/test_schema_genealogy.py`
- Modify: `analysis/tests/test_pipeline_reference_trace.py`

- [ ] Write failing differential tests that run old/reference and new paths over every quarantine reason, duplicate-key collision, missing identity, invalid date, impossible stage order, unlinked record, derived gas ratio, and source-record ordinal fixture.

- [ ] Freeze exact outputs: accepted row order, quarantine precedence/counts, lineage source refs, audit maps, pandas scalar types, and all material IDs.

- [ ] Run the genealogy suite and confirm `iterrows()`, repeated `.at`, and deep frame copies are present in `build_genealogy()`.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_schema_genealogy.py -k genealogy
```

- [ ] Snapshot required columns once with `Series.tolist()`/array views plus the original index and `_source_record_number`. Implement duplicate and quarantine selection over stable integer row ordinals.

- [ ] Build final DataFrames only at the existing public result boundary, with the same column order and dtype-visible scalar semantics. Do not weaken `_ensure_columns()` or the quarantine precedence table.

- [ ] Run differential/golden/actual invariants and benchmark.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_schema_genealogy.py tests/test_golden_production_parity.py
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data \
  python -m pytest -q tests/test_schema_genealogy.py -m actual_snapshot
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data \
  python tools/benchmark_pipeline.py --case actual-phase --phase genealogy --repeat 3 --output /private/tmp/sfep-genealogy-after.json
```

- [ ] Commit on exact parity and at least 10% focused median improvement.

```bash
cd /Users/haechan/Desktop/SFEP
git add analysis/equipment_quality/genealogy.py analysis/tests/test_schema_genealogy.py \
  analysis/tests/test_pipeline_reference_trace.py
git commit -m "perf: build genealogy from stable columns"
```

### Task 6: Make replay event construction column-oriented

**Files:**

- Modify: `analysis/equipment_quality/event_builder.py`
- Modify: `analysis/tests/test_event_builder.py`
- Modify: `analysis/tests/test_golden_production_parity.py`
- Modify: `analysis/tests/test_pipeline_reference_trace.py`

- [ ] Write failing differential tests for every stage, furnace number normalization, missing JSON key versus JSON null, date/hour scheduling, stable tie, event ID preimage, and duplicate-ID rejection.

- [ ] Require the optimized/reference event lists to match on full model equality and serialized CSV bytes. For v1↔v2 comparison normalize only `bundle_id` and `criteria_id`; `event_id` and `values_json` bytes remain exact.

- [ ] Run focused tests and confirm row objects/scalar lookups are rebuilt per stage.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_event_builder.py tests/test_golden_production_parity.py
```

- [ ] Snapshot each required column and source ordinal once. Preallocate stage candidate records, call the existing ID/canonical-value helpers, then apply the existing canonical sort key exactly once.

- [ ] Do not stream final CSV yet. `SummaryBuildRequest` and atomic publication still need the complete validated event tuple; streaming is a separate future contract change.

- [ ] Run actual event generation with cold/warm measurements and semantic trace.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_event_builder.py tests/test_golden_production_parity.py
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data \
  python -m pytest -q tests/test_event_builder.py -m actual_snapshot
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data \
  python tools/benchmark_pipeline.py --case actual-events --repeat 3 --output /private/tmp/sfep-events-after.json
```

- [ ] Commit only if bytes are exact and median improves at least 10%.

```bash
cd /Users/haechan/Desktop/SFEP
git add analysis/equipment_quality/event_builder.py analysis/tests/test_event_builder.py \
  analysis/tests/test_golden_production_parity.py analysis/tests/test_pipeline_reference_trace.py
git commit -m "perf: build replay events from stable columns"
```

### Task 7: Conditionally stream authenticated CP949 source reads

**Files:**

- Modify only if accepted: `analysis/equipment_quality/schema.py`
- Modify only if accepted: `analysis/tests/test_schema_genealogy.py`
- Modify only if accepted: `analysis/tests/test_cli.py`
- Create: `analysis/tests/test_source_streaming.py`
- Modify: `docs/performance/2026-08-31-sfep-python-baseline.md`

- [ ] First measure peak RSS after Tasks 2~6. If the 20% overall RSS target is already met, record `not implemented: target already met` and skip all remaining checkboxes in this task without source changes.

- [ ] Otherwise write failing tests for one authenticated file descriptor, strict CP949 chunk boundaries, multiline CSV records, digest/parser byte identity, before/after inode/size checks, symlink rejection, concurrent replacement, and unchanged error categories.

```python
@dataclass(frozen=True)
class AuthenticatedCsvRead:
    rows: tuple[dict[str, object], ...]
    size_bytes: int
    sha256: str
    file_identity: tuple[int, int]
```

- [ ] Run tests and confirm `_read_source()` currently retains bytes, decoded text, CSV record list, converted row list, and DataFrame together.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_source_streaming.py
python -m pytest -q tests/test_schema_genealogy.py tests/test_cli.py \
  -k 'source or cp949 or symlink or digest'
```

- [ ] Implement incremental SHA-256 and strict incremental CP949 decoding over the same `O_NOFOLLOW` descriptor. Feed `csv.reader` without materializing the complete record list; retain only converted columns required for the final DataFrame.

- [ ] Recheck descriptor/path identity and consumed byte count before returning. Preserve record numbering with header=1 and first data record=2.

- [ ] Run complete source tests and compare actual input frames, source digests, error codes, wall time, and max RSS.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q \
  tests/test_source_streaming.py tests/test_schema_genealogy.py tests/test_cli.py tests/test_contracts.py
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data \
  python tools/benchmark_pipeline.py --case actual-phase --phase source --repeat 3 --output /private/tmp/sfep-source-after.json
```

- [ ] If RSS improves less than 10%, revert only this task's source/test changes and record the measured rejection. Otherwise commit.

```bash
cd /Users/haechan/Desktop/SFEP
git add analysis/equipment_quality/schema.py analysis/tests/test_source_streaming.py \
  analysis/tests/test_schema_genealogy.py analysis/tests/test_cli.py \
  docs/performance/2026-08-31-sfep-python-baseline.md
git commit -m "perf: stream authenticated source CSV reads"
```

### Task 8: Conditionally cache canonical binary64 spellings

**Files:**

- Modify only if accepted: `analysis/equipment_quality/deterministic.py`
- Modify only if accepted: `analysis/tests/test_deterministic.py`
- Modify only if accepted: `analysis/tests/test_pipeline_reference_trace.py`
- Modify: `docs/performance/2026-08-31-sfep-python-baseline.md`

- [ ] Profile actual v2 serialization after Tasks 2~7. If exact float spelling consumes less than 10% of event/summary serialization wall time, record `not implemented: canonical-number encoding is not a material hotspot` and make no source/test change in this task.

- [ ] Otherwise write failing tests over every checked-in canonical-number vector plus deterministic random finite binary64 bit patterns. Cached and uncached encoders must return the same shortest spelling byte-for-byte, including signed zero and exponent boundaries.

```python
def binary64_cache_key(value: float) -> bytes:
    return struct.pack(">d", value)
```

- [ ] Add tests proving the cache is bounded, serialization-local, thread-independent, and never stores NaN/infinity. Repeated calls may reuse only exact 8-byte IEEE-754 keys.

- [ ] Run the deterministic suite and confirm the uncached path is used.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q tests/test_deterministic.py -k 'canonical or float or serialization'
python -m pytest -q tests/test_pipeline_reference_trace.py
```

- [ ] Implement a small bounded cache owned by one canonical serialization call. Do not use a module-global LRU and do not replace the existing exact interval/oracle algorithm.

- [ ] Compare complete golden JSON/CSV and actual semantic-trace digests, then run three serialization measurements.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
python -m pytest -q \
  tests/test_deterministic.py tests/test_golden_production_parity.py \
  tests/test_artifacts.py tests/test_event_builder.py tests/test_pipeline_reference_trace.py
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data \
  python tools/benchmark_pipeline.py --case actual-events --phase serialization --repeat 3 --output /private/tmp/sfep-float-cache-after.json
```

- [ ] If the serialization median improves less than 10% or memory regresses materially, remove only this task's implementation/test additions and record the rejection. Otherwise commit.

```bash
cd /Users/haechan/Desktop/SFEP
git add analysis/equipment_quality/deterministic.py analysis/tests/test_deterministic.py \
  analysis/tests/test_pipeline_reference_trace.py \
  docs/performance/2026-08-31-sfep-python-baseline.md
git commit -m "perf: cache exact canonical float spellings"
```

### Task 9: Run the full semantic and performance gate

**Files:**

- Modify: `docs/performance/2026-08-31-sfep-python-baseline.md`
- Modify: `analysis/tests/test_v2_migration.py`
- Modify: `analysis/tests/test_pipeline_reference_trace.py`

- [ ] Rerun the prerequisite plan's `scripts/restore-sfep-python312.sh` with the checked-in Conda archive lock and local cache. It is idempotent and network-free. Before tests, assert exact interpreter and direct dependency versions.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
/Users/haechan/Desktop/SFEP/scripts/restore-sfep-python312.sh \
  --conda /opt/homebrew/Caskroom/miniconda/base/bin/conda \
  --package-cache /opt/homebrew/Caskroom/miniconda/base/pkgs \
  --lock /Users/haechan/Desktop/SFEP/analysis/python312-conda.lock \
  --output /private/tmp/sfep-python-3.12.10
SFEP_TEST_PYTHON=/Users/haechan/Desktop/SFEP/analysis/.venv/bin/python
"$SFEP_TEST_PYTHON" -c 'import platform, numpy, pandas, jsonschema; print(platform.python_version(), numpy.__version__, pandas.__version__, jsonschema.__version__)'
```

- [ ] Run fast semantic tests, then the baseline 1,390-node selection, then the complete expanded suite. Actual-data skips are allowed only when their documented environment variable is absent.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
SFEP_TEST_PYTHON=/Users/haechan/Desktop/SFEP/analysis/.venv/bin/python
"$SFEP_TEST_PYTHON" -m pytest -q \
  tests/test_v2_migration.py tests/test_pipeline_reference_trace.py \
  tests/test_holdout_optimization.py tests/test_predicate_mask_cache.py \
  tests/test_column_summary_cache.py tests/test_schema_genealogy.py tests/test_event_builder.py
"$SFEP_TEST_PYTHON" -m pytest -q $(tr '\n' ' ' < tests/performance/reference_selection.txt)
"$SFEP_TEST_PYTHON" -m pytest -q
```

- [ ] Run the pre-seal actual-snapshot lane using only the existing data directory. This lane exercises internal production phases and intentionally does not invoke artifact publication or `real_data` runtime verification.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
SFEP_TEST_PYTHON=/Users/haechan/Desktop/SFEP/analysis/.venv/bin/python
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data \
  "$SFEP_TEST_PYTHON" -m pytest -q -m actual_snapshot
```

- [ ] Run cold 1/warm 2 benchmark cases and record wall/RSS plus exact semantic trace digests.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
SFEP_TEST_PYTHON=/Users/haechan/Desktop/SFEP/analysis/.venv/bin/python
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data \
  "$SFEP_TEST_PYTHON" tools/benchmark_pipeline.py --case actual-events --cold 1 --warm 2 --output /private/tmp/sfep-events-final.json
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data \
  "$SFEP_TEST_PYTHON" tools/benchmark_pipeline.py --case actual-phase --phase full-analysis --cold 1 --warm 2 --output /private/tmp/sfep-phases-final.json
```

The post-optimization public `actual-full-v2` rerun and the `real_data` marker remain deferred to Task 10, after two independent `1.2.0` sealed runtime installations exist. Compare that final record to Task 1's authenticated `1.1.0` full baseline; do not satisfy this gate by disabling runtime verification.

- [ ] Apply the hard gates: all semantics exact; actual event and focused quality medians target at least 30% improvement; overall max RSS targets at least 20%. Keep exact optimizations even if aggregate targets miss, but report misses without relabeling them as success.

- [ ] Commit the evidence document.

```bash
cd /Users/haechan/Desktop/SFEP
git add docs/performance/2026-08-31-sfep-python-baseline.md \
  analysis/tests/test_v2_migration.py analysis/tests/test_pipeline_reference_trace.py
git commit -m "docs: record Python optimization evidence"
```

### Task 10: Reseal the honest producer runtime and actual v2 Bundle twice

**Files:**

- Modify: `analysis/pyproject.toml`
- Modify: `analysis/producer.lock`
- Modify: `analysis/producer_runtime.json`
- Modify: `analysis/tools/build_producer.py`
- Modify if required by exact invariants: `analysis/tools/seal_producer_build.py`
- Modify if required by exact invariants: `analysis/tools/seal_runtime.py`
- Modify: `analysis/tests/test_golden_seal.py`
- Modify: `analysis/tests/test_runtime_identity.py`
- Modify: `analysis/tests/test_real_data_invariants.py`
- Runtime output only: `var/equipment-quality/v2-run-a/**`
- Runtime output only: `var/equipment-quality/v2-run-b/**`

- [ ] Write failing tests that reject the compatibility producer source/tree/wheel digests and require optimized producer version `1.2.0` plus exact optimized code-tree provenance. Do not alter any v1 Bundle fixture or the checked-in v2 golden fixture produced by compatibility runtime `1.1.0`. Assert v1 golden reseals read `contracts/equipment-monitor/v1/golden-bundle/producer_runtime.json`, v2 golden reseals read `contracts/equipment-monitor/v2/golden-bundle/producer_runtime.json`, and only current-runtime/actual tests read the new root `analysis/producer_runtime.json`.

- [ ] Change the package/build/sealing literals from compatibility version `1.1.0` to optimized version `1.2.0`, then rebuild the producer wheel twice from the same source epoch, compare archive bytes, seal `producer.lock`, install the verified wheel into two independent clean pinned runtime venvs, and seal `analysis/producer_runtime.json` against runtime A with the actual source/wheel/tree hashes. Use the exact CLIs already enforced by `test_golden_seal.py`; do not add an update/bless mode. Runtime B must independently verify against the same sealed manifest before it is allowed to generate the second Bundle.

Rerun the prerequisite authenticated restore CLI before the commands below. It verifies the exact checked-in SHA-256 list of local Conda archives, repairs the canonical prefix without deleting the damaged copy, and performs the version/`encodings` probes. Do not continue with system Python 3.14 or any unlisted package source.

```bash
cd /Users/haechan/Desktop/SFEP
/Users/haechan/Desktop/SFEP/scripts/restore-sfep-python312.sh \
  --conda /opt/homebrew/Caskroom/miniconda/base/bin/conda \
  --package-cache /opt/homebrew/Caskroom/miniconda/base/pkgs \
  --lock /Users/haechan/Desktop/SFEP/analysis/python312-conda.lock \
  --output /private/tmp/sfep-python-3.12.10
SFEP_OPT_ROOT="$(mktemp -d /private/tmp/sfep-producer-v2.XXXXXX)"
SFEP_BUILD_VENV="$SFEP_OPT_ROOT/build-venv"
SFEP_RUNTIME_VENV_A="$SFEP_OPT_ROOT/runtime-venv-a"
SFEP_RUNTIME_VENV_B="$SFEP_OPT_ROOT/runtime-venv-b"
SFEP_WHEEL_WORK="$SFEP_OPT_ROOT/wheel-work"
SFEP_VERIFIED_WHEELHOUSE="$SFEP_OPT_ROOT/verified-wheelhouse"
SFEP_PINNED_PYTHON=/private/tmp/sfep-python-3.12.10/bin/python3.12
SFEP_TEST_PYTHON=/Users/haechan/Desktop/SFEP/analysis/.venv/bin/python
test "$($SFEP_PINNED_PYTHON --version)" = "Python 3.12.10"
"$SFEP_PINNED_PYTHON" -c 'import encodings, pathlib; assert pathlib.Path(encodings.__file__).is_file()'
test "$($SFEP_TEST_PYTHON --version)" = "Python 3.12.10"
"$SFEP_TEST_PYTHON" -c 'import pytest, numpy, pandas, jsonschema; assert pytest.__version__ == "8.4.1"'
mkdir "$SFEP_VERIFIED_WHEELHOUSE"
find /Users/haechan/Desktop/SFEP/analysis/.wheelhouse -maxdepth 1 -type f -name '*.whl' \
  ! -name 'sfep_equipment_quality-*.whl' -exec cp {} "$SFEP_VERIFIED_WHEELHOUSE" \;

"$SFEP_PINNED_PYTHON" -m venv "$SFEP_BUILD_VENV"
"$SFEP_BUILD_VENV/bin/python" -m pip install --no-index --only-binary=:all: \
  --require-hashes --find-links "$SFEP_VERIFIED_WHEELHOUSE" \
  -r /Users/haechan/Desktop/SFEP/analysis/bootstrap.lock
"$SFEP_BUILD_VENV/bin/python" -m pip install --no-index --only-binary=:all: \
  --require-hashes --find-links "$SFEP_VERIFIED_WHEELHOUSE" \
  -r /Users/haechan/Desktop/SFEP/analysis/build-requirements.lock
SOURCE_DATE_EPOCH=1735689600 "$SFEP_BUILD_VENV/bin/python" \
  /Users/haechan/Desktop/SFEP/analysis/tools/build_producer.py \
  --source-root /Users/haechan/Desktop/SFEP/analysis \
  --build-python "$SFEP_BUILD_VENV/bin/python" \
  --work-root "$SFEP_WHEEL_WORK"
"$SFEP_BUILD_VENV/bin/python" \
  /Users/haechan/Desktop/SFEP/analysis/tools/seal_producer_build.py \
  --source-root /Users/haechan/Desktop/SFEP/analysis \
  --wheel-dir-a "$SFEP_WHEEL_WORK/wheel-a" \
  --wheel-dir-b "$SFEP_WHEEL_WORK/wheel-b" \
  --wheelhouse "$SFEP_VERIFIED_WHEELHOUSE" \
  --producer-lock /Users/haechan/Desktop/SFEP/analysis/producer.lock

for SFEP_RUNTIME_TARGET in "$SFEP_RUNTIME_VENV_A" "$SFEP_RUNTIME_VENV_B"; do
  "$SFEP_PINNED_PYTHON" -m venv "$SFEP_RUNTIME_TARGET"
  "$SFEP_RUNTIME_TARGET/bin/python" -m pip install --no-index --only-binary=:all: \
    --require-hashes --find-links "$SFEP_VERIFIED_WHEELHOUSE" \
    -r /Users/haechan/Desktop/SFEP/analysis/bootstrap.lock
  "$SFEP_RUNTIME_TARGET/bin/python" -m pip install --no-index --only-binary=:all: \
    --require-hashes --find-links "$SFEP_VERIFIED_WHEELHOUSE" \
    -r /Users/haechan/Desktop/SFEP/analysis/requirements.lock
  "$SFEP_RUNTIME_TARGET/bin/python" -m pip install --no-index --only-binary=:all: \
    --require-hashes --find-links "$SFEP_VERIFIED_WHEELHOUSE" \
    -r /Users/haechan/Desktop/SFEP/analysis/producer.lock
done
PYTHONHASHSEED=0 TZ=Asia/Seoul "$SFEP_BUILD_VENV/bin/python" \
  /Users/haechan/Desktop/SFEP/analysis/tools/seal_runtime.py \
  --source-root /Users/haechan/Desktop/SFEP/analysis \
  --producer-wheel-dir "$SFEP_VERIFIED_WHEELHOUSE" \
  --producer-lock /Users/haechan/Desktop/SFEP/analysis/producer.lock \
  --runtime-venv "$SFEP_RUNTIME_VENV_A" \
  --output /Users/haechan/Desktop/SFEP/analysis/producer_runtime.json
```

Keep `SFEP_OPT_ROOT`, `SFEP_BUILD_VENV`, `SFEP_RUNTIME_VENV_A`, `SFEP_RUNTIME_VENV_B`, and `SFEP_TEST_PYTHON` in the same execution shell through Bundle generation and verification; they are task-specific variables and must not replace system environment variables.

- [ ] Run seal/runtime tests before generating data.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
"$SFEP_TEST_PYTHON" -m pytest -q \
  tests/test_golden_seal.py tests/test_runtime_identity.py
```

Add a focused runtime-manifest probe that runs once through each installed interpreter with `-P -s -S -B`, the sealed site-packages path, and a minimal environment. Both probes must authenticate the same `analysis/producer_runtime.json`, package version `1.2.0`, wheel hash, and installed-tree hash without writing bytecode.

- [ ] Generate actual v2 twice with the verified installed producer, fixed config, same runtime manifest, and same existing data.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
PYTHONHASHSEED=0 TZ=Asia/Seoul "$SFEP_RUNTIME_VENV_A/bin/python" -m equipment_quality.cli \
  --config /Users/haechan/Desktop/SFEP/analysis/analysis_config_v2.json \
  --runtime-manifest /Users/haechan/Desktop/SFEP/analysis/producer_runtime.json \
  --data-dir /Users/haechan/프로젝트/sfep-steel/steel-data \
  --output-dir /Users/haechan/Desktop/SFEP/var/equipment-quality/v2-run-a
PYTHONHASHSEED=0 TZ=Asia/Seoul "$SFEP_RUNTIME_VENV_B/bin/python" -m equipment_quality.cli \
  --config /Users/haechan/Desktop/SFEP/analysis/analysis_config_v2.json \
  --runtime-manifest /Users/haechan/Desktop/SFEP/analysis/producer_runtime.json \
  --data-dir /Users/haechan/프로젝트/sfep-steel/steel-data \
  --output-dir /Users/haechan/Desktop/SFEP/var/equipment-quality/v2-run-b
```

- [ ] Compare both v2 directories byte-for-byte, verify each complete Bundle, then compare actual v1 and v2 with the strict identity-normalized semantic comparator.

```bash
cd /Users/haechan/Desktop/SFEP/analysis
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data \
SFEP_BENCHMARK_RUNTIME_PYTHON="$SFEP_RUNTIME_VENV_A/bin/python" \
SFEP_RUNTIME_PYTHON_A="$SFEP_RUNTIME_VENV_A/bin/python" \
SFEP_RUNTIME_PYTHON_B="$SFEP_RUNTIME_VENV_B/bin/python" \
  "$SFEP_TEST_PYTHON" -m pytest -q \
  tests/test_real_data_invariants.py tests/test_v2_migration.py -m real_data
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data \
SFEP_BENCHMARK_RUNTIME_PYTHON="$SFEP_RUNTIME_VENV_A/bin/python" \
SFEP_RUNTIME_PYTHON_A="$SFEP_RUNTIME_VENV_A/bin/python" \
SFEP_RUNTIME_PYTHON_B="$SFEP_RUNTIME_VENV_B/bin/python" \
  "$SFEP_TEST_PYTHON" tools/benchmark_pipeline.py \
  --case actual-full-v2 --cold 1 --warm 2 --output /private/tmp/sfep-full-final.json
```

- [ ] Confirm `var/` is ignored and not staged. Commit only truthful producer metadata, exact seal invariants, and tests.

```bash
cd /Users/haechan/Desktop/SFEP
git status --short
git check-ignore var/equipment-quality/v2-run-a var/equipment-quality/v2-run-b
git add analysis/pyproject.toml analysis/producer.lock analysis/producer_runtime.json \
  analysis/tools/build_producer.py analysis/tools/seal_producer_build.py \
  analysis/tools/seal_runtime.py \
  analysis/tests/test_golden_seal.py analysis/tests/test_runtime_identity.py \
  analysis/tests/test_real_data_invariants.py
git commit -m "build: reseal optimized Python producer"
```

- [ ] Do not Push. Hand the absolute v2 Bundle path and IDs to the Java/Swing plan.

## Completion Gate

- Full Python suite and actual-data lane pass with no unexplained skips.
- Optimized/reference traces and v1↔v2 semantic projections are exact outside the approved allowlist.
- Same actual v2 input/runtime produces byte-identical Bundle directories twice.
- Producer runtime records the actual optimized source, wheel, and installed tree; no old provenance is spoofed.
- Accepted optimizations have focused median improvement of at least 10%; rejected conditions and unmet aggregate targets are documented.
- Actual v2 Bundle remains local under `var/`, and all source changes are local commits with no Push.
