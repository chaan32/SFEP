# SFEP Java and Swing Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** v1/v2 Bundle 검증과 판정 의미를 유지하면서 실제 역사 재생을 10초 이하, 데스크톱 UI 구성을 15초 이하로 목표 최적화하고, 전체 이력을 보존한 최신순 paging·비동기 검색·새 데이터 알림·비차단 시작 화면을 제공한다.

**Architecture:** `ReplayCursor`가 canonical CSV row ordinal을 비-wire `SequencedReplayEvent`로 만들고, `ReplayUnit.fromSequenced(...)`만 이를 받아 monitor에 전달한다. monitor는 한 번 해석한 값 snapshot, 불변 rule/range index, 증분 material/equipment accumulator로 전체 정규 상태를 유지한다. Swing은 전체 상태를 직접 표로 만들지 않고 `HistoryProjection`의 최신순 page와 generation 기반 query 결과만 EDT에 적용한다.

**Tech Stack:** Java 21, Swing, Spring Boot 3.5.15 non-web runtime, Gradle 8.14.5 via `sfep-server/gradlew`, JUnit 5, AssertJ, Jackson, Apache Commons CSV.

**Spec:** `docs/superpowers/specs/2026-08-30-sfep-runtime-desktop-optimization-design.md`

## Global Constraints

- 선행 `docs/superpowers/plans/2026-08-31-sfep-bundle-v2-seed-migration-plan.md`을 완료한다. Tasks 1~9는 v1 actual Bundle로 실행 가능하고, Task 10의 v1↔v2 actual 비교는 Python 최적화 계획의 actual v2 봉인을 기다린다.
- Bundle path/schema/digest/ID/TOCTOU 검증을 성능 명목으로 제거하지 않는다.
- rule/range evaluation, evidence/count/grade, repetition, material history, alert key와 전체 alert 내용은 reference와 같아야 한다.
- range 후보 tie는 `contextLevel` 오름차순 후 `ruleId` 오름차순이다.
- `ReplayUnit.events()`는 의미상 순서를 주장하지 않는다. 최신순은 날짜 문자열이나 hash ID가 아니라 canonical CSV `replayOrdinal`로 정한다.
- raw `ReplayEvent` 목록에서 ordinal을 합성하지 않는다. cursor와 모든 test fixture는 명시적인 `SequencedReplayEvent`를 제공한다.
- 모든 시간 기반 화면·검색·CSV export는 `replayOrdinal` 내림차순이다. 같은 event의 여러 alert만 기존 stable alert key 오름차순으로 tie-break한다.
- 공개 `HistoricalAlert`와 CSV column 목록은 변경하지 않는다. ordinal은 내부 history record에만 둔다.
- 전체 history와 assessment를 보존한다. page size는 Swing row 객체 수만 제한한다.
- EDT mutation은 EDT에서만 실행하고, worker의 stale generation 결과는 폐기한다.
- 같은 저장소에 두 번째 Gradle wrapper를 만들지 않는다. 실행은 `./sfep-server/gradlew -p equipment-monitor ...`를 유지한다.
- 원격 저장소에 Push하지 않는다.

## Dependency Map

```text
Task 1 reference/benchmark
    ↓
Task 2 replay ordinal + latest export
    ↓
Task 3 value snapshot + material accumulator
    ↓
Task 4 rule/range indexes
    ↓
Task 5 unit equipment accumulator
    ↓
Task 6 pure HistoryProjection
    ↓
Task 7 async paging/typeahead/new-data/EDT slicing
    ↓
Task 8 incremental cards/overview/render/accessibility
    ↓
Task 9 nonblocking startup
    ↓
Task 10 actual v1/v2 regression, launch, performance evidence
```

### Task 1: Freeze reference semantics and Java performance lanes

**Files:**

- Create: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/performance/ReferenceRiskEvaluation.java`
- Create: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/performance/ReferenceHistoricalProjection.java`
- Create: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/performance/JavaOptimizationBenchmarkTest.java`
- Create: `scripts/benchmark-equipment-monitor.sh`
- Create: `docs/performance/2026-08-31-sfep-java-swing-baseline.md`
- Modify: `equipment-monitor/build.gradle`

- [ ] Write failing tests that require reference evaluators to expose complete ordered range/rule results and monitor prefix snapshots without importing optimized index/projection classes.

- [ ] Add a benchmark-tag test that records canonical JSON lines for `bundle-load`, `historical-replay`, and `desktop-projection`, including wall time, heap delta, row counts, rule/range/alert semantic digest, JDK, and bundle ID. Do not use hard millisecond assertions inside JUnit.

```java
record BenchmarkRecord(
        String caseName,
        long elapsedNanos,
        long heapDeltaBytes,
        long materials,
        long alerts,
        String semanticDigest) {}
```

- [ ] Run the new test and confirm the reference/benchmark classes are missing.

```bash
cd /Users/haechan/Desktop/SFEP
SFEP_ACTUAL_BUNDLE=/Users/haechan/Desktop/SFEP/var/equipment-quality/run-a/sha256:65d535350ef2d7c5abf6372fdfe7b364c62a6c0af60b01946a23c4659c1ee82a \
  ./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.performance.JavaOptimizationBenchmarkTest
```

- [ ] Implement test-only full scans/recalculations that mirror the pre-optimization code. Keep them under `src/test`; production code must not call them.

- [ ] Register a `benchmarkTest` Gradle task including only tag `benchmark`, and pass `SFEP_ACTUAL_BUNDLE` as a system property after explicit environment validation. Keep `fastTest`, `actualBundleTest`, `actualBundleV2Test`, and default `test` behavior from the contract plan unchanged.

- [ ] Make the shell harness run cold 1/warm 2 under `/usr/bin/time -l`, save stdout/stderr to an explicit result directory, and never modify source or fixtures.

- [ ] Record the already observed baselines as prior evidence, not a fresh run:

```text
actual Bundle load: 6.944s
actual historical replay: 15.553s
actual desktop UI: 24.242s
history rows: 54,515
material filter cardinality: 23,631
```

- [ ] Run reference tests and commit.

```bash
cd /Users/haechan/Desktop/SFEP
SFEP_ACTUAL_BUNDLE=/Users/haechan/Desktop/SFEP/var/equipment-quality/run-a/sha256:65d535350ef2d7c5abf6372fdfe7b364c62a6c0af60b01946a23c4659c1ee82a \
  ./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.risk.RuleCompilerEvaluatorTest \
  --tests com.sfep.equipmentmonitor.risk.OperatingRangeEvaluatorTest \
  --tests com.sfep.equipmentmonitor.state.HistoricalMonitorTest \
  --tests com.sfep.equipmentmonitor.performance.JavaOptimizationBenchmarkTest
git add equipment-monitor/src/test/java/com/sfep/equipmentmonitor/performance \
  equipment-monitor/build.gradle scripts/benchmark-equipment-monitor.sh \
  docs/performance/2026-08-31-sfep-java-swing-baseline.md
git commit -m "test: add Java monitor parity benchmarks"
```

### Task 2: Preserve canonical replay ordinal through history and export

**Files:**

- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/replay/SequencedReplayEvent.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/replay/ReplayCursor.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/replay/ReplayUnit.java`
- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/alert/HistoryRecord.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/alert/AlertHistory.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/state/HistoricalMonitor.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/state/MonitorUpdate.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/replay/ReplayUnitCursorTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/replay/ReplayControllerTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/state/HistoricalMonitorTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/MonitorDashboardPanelTest.java`
- Create: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/alert/AlertHistoryLatestFirstTest.java`

- [ ] Write failing tests proving canonical rows receive monotonically increasing ordinals starting at 0, while `ReplayUnit.events()` remains a compatibility view with no ordering contract.

```java
public record SequencedReplayEvent(long replayOrdinal, ReplayEvent event) {
    public SequencedReplayEvent {
        if (replayOrdinal < 0) throw new IllegalArgumentException("negative replay ordinal");
        Objects.requireNonNull(event, "event");
    }
}
```

- [ ] Add tests for two events in one unit, multiple alerts from one event, shuffled `SequencedReplayEvent` input that preserves each wrapper's ordinal, duplicate alert suppression, newest-first snapshot/export, and stable alert-key tie order.

- [ ] Add an export-failure test proving a partial destination is never presented as complete. Export must snapshot first, write a sibling temporary file, fsync/close, and atomically replace only on success.

- [ ] Run focused tests and confirm insertion order cannot satisfy them.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.replay.ReplayUnitCursorTest \
  --tests com.sfep.equipmentmonitor.state.HistoricalMonitorTest \
  --tests com.sfep.equipmentmonitor.alert.AlertHistoryLatestFirstTest
```

- [ ] Convert `ReplayUnit` to an immutable final class with the single construction boundary `ReplayUnit.fromSequenced(..., List<SequencedReplayEvent>)`. Validate non-negative unique ordinals and unit metadata, retain `events()` only as a derived compatibility view, and remove direct raw-event construction. Update every current call site in `ReplayUnit`, `ReplayControllerTest`, `HistoricalMonitorTest`, and `MonitorDashboardPanelTest` to wrap fixtures with explicit ordinals. No synthetic-ordinal policy is allowed.

- [ ] Add `long nextReplayOrdinal` to `ReplayCursor`; assign it when a validated CSV record becomes a `ReplayEvent`, before unit grouping. Ordinal must not enter event wire data, IDs, equality, or risk evaluation.

- [ ] Store `HistoryRecord(alert, replayOrdinal, stableAlertKey)` in `AlertHistory`. Public snapshots return only alerts sorted by ordinal descending then stable key ascending.

- [ ] Add the exact immutable field `Map<String, Long> changedMaterialOrdinals` to `MonitorUpdate`, defensively copy it, and update `HistoricalMonitor` plus every direct constructor in `MonitorDashboardPanelTest`. Keys equal the changed-material snapshot keys and values are their maximum accepted replay ordinal. Keep existing semantic snapshot fields unchanged; Task 8 consumes this field directly.

- [ ] Run replay/state/export tests and commit.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.replay.ReplayUnitCursorTest \
  --tests com.sfep.equipmentmonitor.replay.ReplayControllerTest \
  --tests com.sfep.equipmentmonitor.state.HistoricalMonitorTest \
  --tests com.sfep.equipmentmonitor.alert.AlertHistoryLatestFirstTest
git add equipment-monitor/src/main/java/com/sfep/equipmentmonitor/replay \
  equipment-monitor/src/main/java/com/sfep/equipmentmonitor/alert \
  equipment-monitor/src/main/java/com/sfep/equipmentmonitor/state \
  equipment-monitor/src/test/java/com/sfep/equipmentmonitor/replay \
  equipment-monitor/src/test/java/com/sfep/equipmentmonitor/state \
  equipment-monitor/src/test/java/com/sfep/equipmentmonitor/alert
git commit -m "feat: retain latest-first replay history order"
```

### Task 3: Decode event values once and aggregate material risk incrementally

**Files:**

- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/replay/EventValueSnapshot.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/replay/ReplayEvent.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/state/HistoricalMonitor.java`
- Create: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/state/HistoricalMonitorIncrementalParityTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/state/HistoricalMonitorTest.java`

- [ ] Write a failing test instrumenting one event with null/text/boolean/integer/decimal values and proving JSON is decoded to immutable `RiskScalar` values once, shared by range/rule/material/equipment paths, and not mutable by callers.

```java
public record EventValueSnapshot(Map<String, RiskScalar> values) {
    public static EventValueSnapshot from(ReplayEvent event) { ... }
}
```

- [ ] Write prefix-parity tests comparing incremental monitor state with `ReferenceHistoricalProjection` after every event over grade escalation/de-escalation, AP historical evidence, missing data, repeated matched rule IDs, and 100 randomized deterministic sequences.

- [ ] Run tests and confirm `HistoricalMonitor.process()` calls `event.values()`/`scalarValues()` twice and `MaterialAccumulator.updateAssessment()` rescans all assessments.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.state.HistoricalMonitorIncrementalParityTest \
  --tests com.sfep.equipmentmonitor.state.HistoricalMonitorTest
```

- [ ] Decode one `EventValueSnapshot` at process entry and reuse its map. Keep `ReplayEvent.values()` deep-copy behavior for public callers.

- [ ] Maintain early/historical grade counts and actionable evidence-family counts incrementally when adding one `StageAssessment`. Preserve the full assessment and evaluation lists for detail views.

- [ ] Compute `matchedRuleCount` with the exact existing cumulative semantics; do not accidentally count repeated appearances of the same rule differently.

- [ ] Run parity and actual replay benchmark. Retain only with exact prefix equality and at least 10% focused improvement.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.state.HistoricalMonitorIncrementalParityTest \
  --tests com.sfep.equipmentmonitor.state.HistoricalMonitorTest
SFEP_ACTUAL_BUNDLE=/Users/haechan/Desktop/SFEP/var/equipment-quality/run-a/sha256:65d535350ef2d7c5abf6372fdfe7b364c62a6c0af60b01946a23c4659c1ee82a \
  ./sfep-server/gradlew -p equipment-monitor benchmarkTest --tests '*JavaOptimizationBenchmarkTest.historicalReplay*'
```

- [ ] Commit.

```bash
cd /Users/haechan/Desktop/SFEP
git add equipment-monitor/src/main/java/com/sfep/equipmentmonitor/replay/EventValueSnapshot.java \
  equipment-monitor/src/main/java/com/sfep/equipmentmonitor/replay/ReplayEvent.java \
  equipment-monitor/src/main/java/com/sfep/equipmentmonitor/state/HistoricalMonitor.java \
  equipment-monitor/src/test/java/com/sfep/equipmentmonitor/state
git commit -m "perf: aggregate material risk incrementally"
```

### Task 4: Compile immutable quality-rule and operating-range indexes

**Files:**

- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/risk/QualityRuleIndex.java`
- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/risk/OperatingRangeIndex.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/risk/QualityRuleEvaluator.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/risk/OperatingRangeEvaluator.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/state/HistoricalMonitor.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/DesktopMonitorSession.java`
- Create: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/risk/RiskIndexParityTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/risk/RuleCompilerEvaluatorTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/risk/OperatingRangeEvaluatorTest.java`

- [ ] Write failing randomized/reference parity tests over stage, equipment type, scope, equipment ID, application context, missing fields, and candidate order.

```java
QualityRuleIndex quality = QualityRuleIndex.compile(rules);
OperatingRangeIndex ranges = OperatingRangeIndex.compile(definitions);
List<RuleEvaluation> ruleResult = evaluator.evaluate(event, quality);
List<RangeEvaluation> rangeResult = rangeEvaluator.evaluate(event, ranges);
```

- [ ] Add explicit range tie fixtures whose candidates share context level but have reversed source order and different rule IDs. Both scan and index paths must choose the lexicographically smaller `ruleId`.

- [ ] Add duplicate rule ID, invalid scope, inconsistent field role, and index invariant failure tests. Production must fail construction; it must not silently fall back to a full scan.

- [ ] Run tests and confirm current evaluators scan 165 rules/build candidates from 424 ranges per event.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.risk.RiskIndexParityTest \
  --tests com.sfep.equipmentmonitor.risk.RuleCompilerEvaluatorTest \
  --tests com.sfep.equipmentmonitor.risk.OperatingRangeEvaluatorTest
```

- [ ] `QualityRuleIndex` stores original sealed ordinal with each rule and returns only possible stage/type/scope/equipment buckets, finally ordered by original ordinal.

- [ ] `OperatingRangeIndex` stores field/stage/type/equipment/context buckets but delegates all matching/status logic to existing evaluator helpers. Final selection comparator remains `contextLevel`, then `ruleId`.

- [ ] Compile both indexes once in `DesktopMonitorSession`/`HistoricalMonitor` construction. Remove no DTO validation.

- [ ] Run full risk/state parity and benchmark; retain only on exact equality plus at least 10% replay median improvement.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.risk.RiskIndexParityTest \
  --tests com.sfep.equipmentmonitor.risk.RuleCompilerEvaluatorTest \
  --tests com.sfep.equipmentmonitor.risk.OperatingRangeEvaluatorTest \
  --tests com.sfep.equipmentmonitor.state.HistoricalMonitorIncrementalParityTest
```

- [ ] Commit.

```bash
cd /Users/haechan/Desktop/SFEP
git add equipment-monitor/src/main/java/com/sfep/equipmentmonitor/risk \
  equipment-monitor/src/main/java/com/sfep/equipmentmonitor/state/HistoricalMonitor.java \
  equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/DesktopMonitorSession.java \
  equipment-monitor/src/test/java/com/sfep/equipmentmonitor/risk
git commit -m "perf: index sealed risk definitions"
```

### Task 5: Finalize equipment snapshots once per replay unit

**Files:**

- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/state/UnitEquipmentAccumulator.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/state/HistoricalMonitor.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/state/EquipmentSnapshot.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/state/HistoricalMonitorTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/state/HistoricalMonitorIncrementalParityTest.java`

- [ ] Write failing tests for a wide unordered unit with repeated equipment/material keys. Compare final equipment snapshots, changed-equipment map, material key sort, values, metadata, and `orderedWithinUnit=false` to the reference after every unit.

- [ ] Run tests and confirm `HistoricalMonitor.process()` copies/sorts equipment maps once per event.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.state.HistoricalMonitorTest \
  --tests com.sfep.equipmentmonitor.state.HistoricalMonitorIncrementalParityTest
```

- [ ] Accumulate mutable maps only inside one `accept()` call, then freeze one `EquipmentSnapshot` per equipment key after all sequenced events are evaluated. Never expose the builder through `MonitorUpdate` or `MonitorSnapshot`.

- [ ] Preserve last semantic event metadata according to replay ordinal, not the iteration order of `ReplayUnit.events()`.

- [ ] Run state/replay tests and commit after an exact wide-unit parity check.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.state.HistoricalMonitorTest \
  --tests com.sfep.equipmentmonitor.state.HistoricalMonitorIncrementalParityTest \
  --tests com.sfep.equipmentmonitor.replay.ReplayUnitCursorTest
git add equipment-monitor/src/main/java/com/sfep/equipmentmonitor/state \
  equipment-monitor/src/test/java/com/sfep/equipmentmonitor/state
git commit -m "perf: finalize equipment state per replay unit"
```

### Task 6: Add a pure latest-first HistoryProjection with bounded pages

**Files:**

- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/HistoryQuery.java`
- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/HistoryPage.java`
- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/HistoryRow.java`
- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/HistoryProjection.java`
- Create: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/HistoryProjectionTest.java`

- [ ] Write failing pure-Java tests with 55,000 history records and 23,631 distinct materials for newest-first ordering, same-event ties, date/equipment/material-prefix/grade filters, total matches, page boundaries, and suggestion limit 20.

```java
public record HistoryQuery(
        String date,
        String equipment,
        String materialPrefix,
        String grade,
        int offset,
        int pageSize) {
    public HistoryQuery {
        if (offset < 0 || pageSize < 50 || pageSize > 500) throw ...;
    }
}
```

- [ ] Require concatenating all pages to reconstruct the full canonical history exactly once in reverse ordinal order. A filter must never alter relative order.

- [ ] Run the test and confirm no projection classes exist.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.ui.HistoryProjectionTest
```

- [ ] Implement a thread-safe append/snapshot boundary. `HistoryProjection.query()` works from one immutable snapshot version and produces typed rows for only the requested page; default page size is 200.

- [ ] Keep history `RowSorter` disabled. Projection order is the single source of truth.

- [ ] Ensure typeahead uses a compact material index and returns at most 20 UTF-8/stable-sorted suggestions without creating 23,631 Swing items.

- [ ] Run tests and commit this Swing-free unit.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.ui.HistoryProjectionTest
git add equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/HistoryQuery.java \
  equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/HistoryPage.java \
  equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/HistoryRow.java \
  equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/HistoryProjection.java \
  equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/HistoryProjectionTest.java
git commit -m "feat: add paged latest-first history projection"
```

### Task 7: Add asynchronous filters, typeahead, new-data behavior, and bounded EDT drains

**Files:**

- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/HistoryQueryController.java`
- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/HistoryQueryResult.java`
- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/UiUpdateQueue.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/MonitorDashboardPanel.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/DesktopMonitorWindow.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/MonitorUiTheme.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/MonitorDashboardPanelTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/DesktopMonitorWindowTest.java`
- Create: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/HistoryQueryControllerTest.java`
- Create: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/UiUpdateQueueTest.java`

- [ ] Write failing tests for page controls, 50/100/200/500 page sizes, material text search, 200ms debounce, maximum 20 suggestions, generation discard, worker failure, close cancellation, final-window disposal, and EDT-only component mutation.

- [ ] Add the approved live-update scenarios:

```text
top/offset 0 → new matching rows become first rows immediately
old page/scrolled down → page, viewport, selection stay; badge becomes 새 데이터 N건
badge click → filters remain; offset 0 and first row selected
```

- [ ] Add a coalesced update test proving one EDT slice processes at most its configured item budget, reschedules exactly once while work remains, and loses no final state/alert/order. Treat 16ms as a measured performance target in the benchmark, not a wall-clock JUnit assertion.

- [ ] Run tests and confirm `allAlerts`, four JComboBoxes, synchronous `rebuildHistory()`, and whole-batch `drainUpdates()` fail the bounded behavior.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.ui.MonitorDashboardPanelTest \
  --tests com.sfep.equipmentmonitor.ui.HistoryQueryControllerTest \
  --tests com.sfep.equipmentmonitor.ui.UiUpdateQueueTest
```

- [ ] Replace the material `JComboBox` with a named `JTextField` plus bounded suggestion popup. Keep date/equipment/grade options compact; their page query still runs off EDT.

- [ ] `HistoryQueryController` receives an `ExecutorService` factory and owns the resulting worker plus a monotonically increasing generation. A new query cancels any queued prior future and stale running completions are discarded by generation. Publish one atomic immutable `HistoryQueryResult(HistoryPage page, long snapshotVersion, int matchingNewCount)` through an injected EDT scheduler. `close()` is idempotent, cancels queued/running work, calls `shutdownNow()`, returns without waiting, and prevents every late callback. A separate bounded `awaitTermination(Duration)` rejects EDT calls. The production factory uses a named daemon thread as a final JVM-exit safeguard; tests close and await, then assert zero live worker threads.

- [ ] Make `historyModel` reference typed current-page rows and fire one table-data event per accepted page. It must never hold more rows than selected page size.

- [ ] Give `UiUpdateQueue` the deterministic seam `UiUpdateQueue(int maxItemsPerSlice, LongSupplier nanoClock, Consumer<Runnable> edtScheduler)` and a production default of 128 items. One item is the history/index delta belonging to one queued `MonitorUpdate`; coalesced overview/current-state deltas are applied first and do not consume that history budget. Each drain handles at most 128 items, records elapsed nanos for diagnostics, preserves FIFO/ordinal order, and schedules one continuation when work remains. Preserve existing failure reporting to `ReplayController`.

- [ ] Make panel ownership explicit in `MonitorDashboardPanel.removeNotify()`: on EDT stop the debounce `Timer`, set the panel/queue closed flag, call nonblocking `HistoryQueryController.close()`, then close `UiUpdateQueue`. Any already queued EDT continuation observes the closed flag and becomes a no-op. Keep the dashboard reference in `DesktopMonitorWindow`; after EDT host disposal, its existing off-EDT shutdown path calls the panel's bounded background-termination wait. Tests dispose the final window and assert no timer, query worker, or queue continuation survives; Task 9 repeats the assertion for close during startup handoff.

- [ ] Run UI tests and actual v1 desktop projection benchmark; commit on exact behavior and bounded model size.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.ui.HistoryProjectionTest \
  --tests com.sfep.equipmentmonitor.ui.HistoryQueryControllerTest \
  --tests com.sfep.equipmentmonitor.ui.UiUpdateQueueTest \
  --tests com.sfep.equipmentmonitor.ui.MonitorDashboardPanelTest
git add equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui \
  equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui
git commit -m "feat: optimize paged Swing history queries"
```

### Task 8: Make cards and overview incremental, then finish rendering and accessibility

**Files:**

- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/DashboardCounts.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/MonitorDashboardPanel.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/MonitorUiTheme.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/MonitorDashboardPanelTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/MonitorUiThemeTest.java`

- [ ] Write failing differential tests comparing incremental severe/caution/quality-danger/material counts with a full scan after every material update.

- [ ] Add overview ordering tests: primary priority ascending, then last-change replay ordinal descending, then material ID ascending. Changing one material must preserve selection and avoid a 23,631-row sorter rebuild.

- [ ] Add latest-first evidence tests. Any retained time-based evidence list must use ordinal descending; management/definition tables remain semantic, not time sorted.

- [ ] Add accessibility assertions for `labelFor`, accessible name/description, focusability, mnemonic/action bindings, typeahead list, page controls, `새 데이터 N건`, status/progress, and all tables. Risk state must remain understandable from text/symbols without colour.

- [ ] Run tests and confirm summary cards rescan all materials and filter labels lack associations.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.ui.MonitorDashboardPanelTest \
  --tests com.sfep.equipmentmonitor.ui.MonitorUiThemeTest
```

- [ ] Implement `DashboardCounts.replace(previous, current)` with exact category deltas. Keep a reference full-scan method under tests only.

- [ ] Consume `MonitorUpdate.changedMaterialOrdinals()` directly, store each overview row with that exact last-change ordinal, and use one explicit comparator. Batch row mutations and resort once per accepted UI slice.

- [ ] Cache immutable fonts, borders, and colours in `MonitorUiTheme`; renderers must not allocate style objects per cell. Preserve focus rings and textual symbols.

- [ ] Associate every visible label with its input and set concise Korean accessible descriptions/tooltips. Do not depend on a vendor screen-reader API.

- [ ] Run UI/theme tests and commit.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.ui.MonitorDashboardPanelTest \
  --tests com.sfep.equipmentmonitor.ui.MonitorUiThemeTest
git add equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui \
  equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui
git commit -m "perf: update Swing summaries incrementally"
```

### Task 9: Show a verified nonblocking startup shell before Bundle load

**Files:**

- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/DesktopMonitorStartupCoordinator.java`
- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/DesktopMonitorStartupWindow.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/EquipmentMonitorDesktopApplication.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/DesktopMonitorWindow.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/DesktopMonitorSession.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/EquipmentMonitorDesktopApplicationTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/DesktopMonitorWindowTest.java`
- Create: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/DesktopMonitorStartupCoordinatorTest.java`

- [ ] Write failing tests with a controllable loader executor for: loading shell visible before load completion, all Swing mutation on EDT, exact lifecycle labels, verified success creates one session/window/listener, failure shows Korean safe summary, close during load ignores late success, and cleanup is idempotent.

```text
startup shell: Bundle 검증 → 화면 준비
verified dashboard opens paused
user presses 시작: dashboard status becomes 이벤트 재생
```

Do not display a percentage because loader/replay work units are not calibrated. Do not replay automatically during startup: the existing start/pause/next-date control semantics remain user-driven.

- [ ] Add a test whose loader blocks for over one second while the shell becomes visible within one second of `ApplicationRunner` invocation.

- [ ] Run tests and confirm eager Spring beans load the Bundle before the window exists.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.EquipmentMonitorDesktopApplicationTest \
  --tests com.sfep.equipmentmonitor.ui.DesktopMonitorWindowTest \
  --tests com.sfep.equipmentmonitor.ui.DesktopMonitorStartupCoordinatorTest
```

- [ ] Replace eager `LoadedBundle → DesktopMonitorSession → DesktopMonitorWindow` bean creation with a coordinator started by `ApplicationRunner`. `BundleLoader.load()` and session creation run off EDT; the shell itself is created/shown on EDT immediately. After verified load and screen preparation, create the dashboard in paused state; only `DesktopMonitorSession.start()` invoked by the user's `시작` action begins replay and changes the dashboard status to `이벤트 재생`.

- [ ] Do not show the normal dashboard until Bundle validation succeeds. On failure keep only the error shell with safe detail and close action.

- [ ] Make ownership explicit: the startup shell and final window receive the existing `shutdownApplication` callback, and any user close path invokes it exactly once. Coordinator `close()` is idempotent, cancels loader work, unregisters listeners, closes the visible window, then session, then shell; a late loader completion observes the closed generation and disposes any provisional resource without publishing. Add close-count tests for close during load, close after success, repeated close, and Spring context shutdown; each must close the application context exactly once and leave no worker/listener alive.

- [ ] Run lifecycle/UI tests and commit.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor test \
  --tests com.sfep.equipmentmonitor.EquipmentMonitorDesktopApplicationTest \
  --tests com.sfep.equipmentmonitor.ui.DesktopMonitorSessionTest \
  --tests com.sfep.equipmentmonitor.ui.DesktopMonitorWindowTest \
  --tests com.sfep.equipmentmonitor.ui.DesktopMonitorStartupCoordinatorTest
git add equipment-monitor/src/main/java/com/sfep/equipmentmonitor/EquipmentMonitorDesktopApplication.java \
  equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui \
  equipment-monitor/src/test/java/com/sfep/equipmentmonitor/EquipmentMonitorDesktopApplicationTest.java \
  equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui
git commit -m "feat: show nonblocking verified startup UI"
```

### Task 10: Run actual v1/v2 parity, performance, and Swing launch verification

**Files:**

- Modify (created by the prerequisite contract plan): `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/bundle/ActualBundleV2ParityTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/state/ActualHistoricalReplayTest.java`
- Modify: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/ActualDesktopMonitorUiTest.java`
- Modify: `docs/performance/2026-08-31-sfep-java-swing-baseline.md`
- Create: `docs/verification/2026-08-31-sfep-actual-v2-desktop-check.md`

- [ ] Fail fast if the prerequisite compatibility slice is absent: the v2 parity test source and both custom Gradle tasks must already exist before any verification command runs.

```bash
cd /Users/haechan/Desktop/SFEP
test -f equipment-monitor/src/test/java/com/sfep/equipmentmonitor/bundle/ActualBundleV2ParityTest.java
./sfep-server/gradlew -p equipment-monitor help --task fastTest
./sfep-server/gradlew -p equipment-monitor help --task actualBundleTest
./sfep-server/gradlew -p equipment-monitor help --task actualBundleV2Test
```

- [ ] Run fast and default suites first.

```bash
cd /Users/haechan/Desktop/SFEP
./sfep-server/gradlew -p equipment-monitor fastTest
./sfep-server/gradlew -p equipment-monitor test
```

- [ ] Run actual v1 contract/replay/UI lane against the unchanged approved Bundle.

```bash
cd /Users/haechan/Desktop/SFEP
SFEP_ACTUAL_BUNDLE=/Users/haechan/Desktop/SFEP/var/equipment-quality/run-a/sha256:65d535350ef2d7c5abf6372fdfe7b364c62a6c0af60b01946a23c4659c1ee82a \
  ./sfep-server/gradlew -p equipment-monitor actualBundleTest
```

- [ ] Resolve the final actual v2 directory produced by the Python plan, then run the v1↔v2 parity lane. Both environment variables are mandatory; missing variables must fail with their names.

```bash
cd /Users/haechan/Desktop/SFEP
SFEP_V2_BUNDLE_DIR="$(find /Users/haechan/Desktop/SFEP/var/equipment-quality/v2-run-a \
  -mindepth 1 -maxdepth 1 -type d -name 'sha256:*' -print)"
test "$(printf '%s\n' "$SFEP_V2_BUNDLE_DIR" | sed '/^$/d' | wc -l | tr -d ' ')" = "1"
SFEP_ACTUAL_BUNDLE=/Users/haechan/Desktop/SFEP/var/equipment-quality/run-a/sha256:65d535350ef2d7c5abf6372fdfe7b364c62a6c0af60b01946a23c4659c1ee82a \
SFEP_ACTUAL_BUNDLE_V2="$SFEP_V2_BUNDLE_DIR" \
  ./sfep-server/gradlew -p equipment-monitor actualBundleV2Test
```

The command requires exactly one SHA-256-named final directory in `v2-run-a`; the test then authenticates that directory name against its Manifest. Do not hard-code the generated ID in source.

- [ ] Benchmark actual v1 before/after using the same Bundle, cold/warm policy, and semantic digest. Separately benchmark v2 for compatibility evidence, not as the optimization baseline.

```bash
cd /Users/haechan/Desktop/SFEP
scripts/benchmark-equipment-monitor.sh \
  /Users/haechan/Desktop/SFEP/var/equipment-quality/run-a/sha256:65d535350ef2d7c5abf6372fdfe7b364c62a6c0af60b01946a23c4659c1ee82a \
  /private/tmp/sfep-java-final
```

- [ ] Enforce correctness as a hard gate. Targets are replay ≤10s, desktop projection ≤15s, loading shell ≤1s, visible history rows ≤ selected page size, and max RSS 20% lower. Report any missed performance target honestly.

- [ ] Launch the actual Swing application locally with v2 Bundle and verify loading shell, replay start/pause/resume/next-date, latest-first history, paging, filters/typeahead, old-page `새 데이터 N건`, jump-to-top, full latest-first CSV export, and clean close.

```bash
cd /Users/haechan/Desktop/SFEP
SFEP_V2_BUNDLE_DIR="$(find /Users/haechan/Desktop/SFEP/var/equipment-quality/v2-run-a \
  -mindepth 1 -maxdepth 1 -type d -name 'sha256:*' -print)"
test "$(printf '%s\n' "$SFEP_V2_BUNDLE_DIR" | sed '/^$/d' | wc -l | tr -d ' ')" = "1"
./sfep-server/gradlew -p equipment-monitor bootRun \
  --args="--sfep.equipment-monitor.bundle-dir=$SFEP_V2_BUNDLE_DIR"
```

- [ ] Capture the authenticated v1/v2 IDs, test commands/results, timings/RSS, observed row limits, and Swing checklist in the Korean verification document. Do not include raw private data rows.

- [ ] Run `git diff --check`, confirm `var/` and exported CSV/screenshot artifacts are unstaged, and commit only tests/docs.

```bash
cd /Users/haechan/Desktop/SFEP
git diff --check
git status --short
git add equipment-monitor/src/test/java/com/sfep/equipmentmonitor/bundle/ActualBundleV2ParityTest.java \
  equipment-monitor/src/test/java/com/sfep/equipmentmonitor/state/ActualHistoricalReplayTest.java \
  equipment-monitor/src/test/java/com/sfep/equipmentmonitor/ui/ActualDesktopMonitorUiTest.java \
  docs/performance/2026-08-31-sfep-java-swing-baseline.md \
  docs/verification/2026-08-31-sfep-actual-v2-desktop-check.md
git commit -m "docs: verify optimized actual Swing monitor"
```

- [ ] Do not Push. Hand off the local commit list and launch command.

## Completion Gate

- `fastTest`, default `test`, actual v1, and actual v1↔v2 lanes pass.
- Indexed/incremental results equal the full-scan/recalculation oracle at every tested prefix.
- All time-based projections and full CSV export are replay-ordinal latest-first with stable same-event ties.
- Full history remains available while the Swing table holds at most the selected page size.
- Old-page users keep viewport/selection and receive `새 데이터 N건`; top users see new rows immediately.
- Search workers cannot publish stale/late results, and EDT work is sliced/cancelled safely.
- Loading shell is visible within one second and normal UI appears only after verified success.
- Actual Swing launch and controls are verified locally; results and any missed performance target are documented.
- All implementation and evidence commits remain local with no remote Push.
