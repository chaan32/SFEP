# SFEP ONNX Quality Probability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 포트폴리오에 필요한 세로 한 줄을 먼저 완성해 실제 데이터의 품질 불량확률을 Python에서 학습·ONNX 변환하고 Java Swing에서 같은 확률을 표시한 뒤, 시간이 허용되면 Bundle v3와 sealed runtime까지 완성한다.

**Architecture:** 1단계는 기존 v2 분석 입력을 그대로 사용해 Python Feature→시간순 후보 평가→ONNX→Holdout 지표를 만든다. 2단계는 로컬 ONNX/metadata를 Java에 직접 주입해 parity·latency·Swing 데모를 먼저 통과시킨다. 3단계에서 같은 core를 sealed Python runtime과 Bundle v3 publication/loader에 연결하고 임시 로컬 주입 경로를 제거한다.

**Tech Stack:** Python 3.12.10, pandas 2.3.0, NumPy 2.2.6, scikit-learn 1.9.1, ONNX 1.23.2, skl2onnx 1.20.0, ONNX Runtime 1.31.1, threadpoolctl 3.8.0, Java 21, Swing, Gradle, JUnit 5.

**Spec:** `docs/superpowers/specs/2026-11-12-sfep-onnx-quality-probability-design.md`

## Global Constraints

- Target은 AP `judge == "불량"`; 기계 고장 예측으로 표현하지 않는다.
- 최초 추론 시점은 `FURNACE_EXTRACTED`; RM4/AP 값, ID, 날짜·시각을 Feature로 쓰지 않는다.
- `f_ext_time`은 체류시간이 아니라 0~23시 출측 시각으로 확인됐으므로 제외한다. 허용 raw Feature는 SM/CC 12개+FURNACE 16개, 총 28개다.
- 후보는 Logistic Regression, Histogram Gradient Boosting, Random Forest 세 개뿐이며 hyperparameter와 seed를 고정한다.
- Logistic은 `class_weight="balanced", solver="liblinear", max_iter=1000`; HGB는 `class_weight="balanced", learning_rate=0.05, max_iter=100, max_depth=4, min_samples_leaf=20, l2_regularization=1.0, early_stopping=False`; RF는 `n_estimators=200, max_depth=6, min_samples_leaf=10, class_weight="balanced_subsample", n_jobs=1`이다. `random_state`는 v2 bootstrap seed SHA-256의 마지막 8 hex를 unsigned int로 변환한다.
- 모든 fit/predict/export/permutation 구간은 `threadpoolctl.threadpool_limits(limits=1)` 안에서 실행한다. 개발 명령에도 `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `VECLIB_MAXIMUM_THREADS=1`을 둔다.
- parity는 `rtol=0`, `atol=1e-5`; 통과 여부뿐 아니라 실제 최대 절대 차이를 기록한다.
- 후보 순위와 threshold는 reference OOF만 사용한다. Holdout은 모델·threshold·qualification을 고정한 뒤 한 번 평가한다.
- 각 OOF fold의 encoder 통계는 해당 fold train에서만 fit한다. 최종 Feature 계약만 전체 reference에서 fit하고 Holdout·Python ONNX·Java가 함께 사용한다.
- grouped permutation importance는 qualification까지 고정한 뒤 Holdout에서 raw Feature 단위로 10회 계산하며, 선택·threshold·qualification에 되먹이지 않는다.
- 실제 원천 CSV·실제 `.onnx`·실제 Bundle은 `var/equipment-quality/` 아래 로컬 전용이다. Git에는 실제 행·ID를 넣지 않는다.
- v1/v2 통계 모니터의 상태·경보·최근순 정렬은 모델 추가로 바뀌지 않는다.
- 2026-11-14 23:00 KST까지 Python ONNX↔Java parity가 통과하지 않으면 ONNX를 포트폴리오 완료 항목에서 제외하고 미완성 코드를 main에 병합하지 않는다.

## Execution Phases

1. **1단계 — 오늘:** Task 1~4. Python 개발환경→Feature→세 후보 OOF 평가→ONNX→Python parity→Holdout 지표/importance.
2. **2단계 — 다음 작업일:** Task 5~6. Java 직접 ONNX parity/latency→기존 Swing에 로컬 모델 주입→실행 데모.
3. **3단계 — 시간 허용 시:** Task 7~10. wheelhouse/runtime 봉인→Bundle v3 계약→golden fixture→secure loader→전체 회귀/문서.

## Required Metrics Ledger

Task 4와 5가 다음 값을 machine-readable report와 사용자 요약에 모두 남긴다.

- Holdout 전체 수, 평가 가능 수, 불량 수
- 세 후보의 OOF Average Precision 순위와 최종 선택 이유
- 고정 threshold, Holdout Recall·Precision·Average Precision·confusion count
- `QUALIFIED` 또는 `RESEARCH_ONLY`와 사유
- sklearn native↔Python ONNX, Python ONNX↔Java ONNX 최대 절대 확률 차이
- Java encoder+ONNX 1건 latency: warm-up 1,000회, 측정 10,000회의 mean·median·p95 ms

## Task Completion Interview Gate

각 Task의 테스트가 통과하면 새 글쓰기 에이전트에게 해당 Task의 diff와 테스트만 주고 다음 문장을 그대로 요청한다.

> 이 Task의 설계를 왜 이렇게 했는지 면접 답변처럼 설명해 줘. 문제, 선택, 대안, 트레이드오프, 검증 순서로 60초 분량을 작성하고 코드에 없는 주장이나 미확인 수치는 넣지 마.

반환 답변은 본 에이전트가 검증한 뒤 `docs/interview/SFEP_ONNX_DECISIONS.md`의 해당 Task 절에 기록한다. 설명할 수 없는 구현은 다음 Task로 넘기지 않는다.

## Review Focus

- 시간·RM4·AP·ID 누출이 하나라도 vector에 들어가면 학습을 중단한다. Task 2가 고정한다.
- HGB/OpenMP/BLAS가 실제 fit/predict 구간에서 1 thread인지 검사하고 반복 산출물 digest를 비교한다. Task 1/3/4가 고정한다.
- Holdout importance는 qualification 뒤에만 실행하며 결과가 선택값을 바꾸지 못한다. Task 4가 호출 순서 테스트로 고정한다.
- 2단계의 standalone model 주입은 두 경로가 모두 명시됐을 때만 허용하고 3단계 완료 시 삭제한다. Task 6/10이 고정한다.
- 모델 오류·연구용 상태에서도 기존 통계 재생은 계속되고 모델 경보만 억제한다. Task 6/10이 고정한다.

---

## 1단계 — Python 지표와 ONNX 산출물

### Task 1: 가벼운 Python 개발환경을 먼저 복구

**Files:**
- Create: `analysis/model-requirements.txt`
- Create: `analysis/equipment_quality/model_runtime.py`
- Create: `analysis/tests/test_model_environment.py`
- Create: `docs/interview/SFEP_ONNX_DECISIONS.md`

**Interfaces:**
- Produces: 동작하는 `analysis/.venv/bin/python`과 `single_threaded()` 검증 기반.

- [ ] **Step 1: 환경 실패 테스트를 쓴다.** Python `3.12.x`, 직접 의존성의 정확한 버전, `threadpool_limits(limits=1)` 내부 native pool의 `num_threads == 1`을 검사한다.
- [ ] **Step 2: 현재 깨진 `.venv`로 실패를 확인한다.** Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_model_environment.py -q`. Expected: interpreter 또는 ML package 부재로 FAIL.
- [ ] **Step 3: 개발용 requirements만 고정한다.** 기존 3개 runtime dependency와 `scikit-learn==1.9.1`, `onnx==1.23.2`, `skl2onnx==1.20.0`, `onnxruntime==1.31.1`, `threadpoolctl==3.8.0`, 테스트용 `pytest==8.4.1`을 기록한다. `model_runtime.single_threaded()`는 `threadpool_limits(limits=1)`을 반환한다. 로컬 Conda cache로 Python 3.12.10을 `var/equipment-quality/python312/`에 복구하고 `.venv`를 다시 만든 뒤 이 파일로 설치한다. wheelhouse/producer lock은 아직 바꾸지 않는다.
- [ ] **Step 4: 환경 테스트를 통과시킨다.** Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_model_environment.py -q`. Expected: PASS.
- [ ] **Step 5: Interview Gate를 실행하고 커밋한다.** Commit: `build: add local ONNX model environment`.

### Task 2: 누출 없는 Feature 계약과 encoder 구현

**Files:**
- Create: `analysis/equipment_quality/model_features.py`
- Create: `analysis/tests/test_model_features.py`

**Interfaces:**
- Produces: `project_model_rows(rows: pd.DataFrame) -> pd.DataFrame`, `fit_feature_contract(rows: pd.DataFrame) -> FeatureContract`, `encode_model_rows(rows: pd.DataFrame, contract: FeatureContract, *, require_labels: bool) -> EncodedDataset`.
- `EncodedDataset.matrix`는 C-contiguous `np.float32[:, N]`; metadata용 `FeatureContract.to_wire()`는 canonical 정렬을 유지한다.

- [ ] **Step 1: allowlist/denylist 테스트를 쓴다.** 허용 28개만 vector에 들어가고 `f_ext_time`, 모든 날짜, RM4/AP, judge, ID, split/row-order가 들어가면 fail-closed 하는지 검사한다.
- [ ] **Step 2: missing/UNKNOWN 테스트를 쓴다.** 숫자는 train median, 범주는 UTF-8 정렬 vocabulary+`UNKNOWN`, 모든 raw Feature는 missing indicator를 가진다. reference 결측 수 nearest-rank p99 초과 행은 `INPUT_INCOMPLETE`다.
- [ ] **Step 3: 테스트가 module 부재로 실패하는지 확인한다.** Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_model_features.py -q`. Expected: FAIL.
- [ ] **Step 4: 최소 projection/encoder를 구현하고 통과시킨다.** Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_model_features.py -q`. Expected: 반복 vector bytes와 contract bytes 동일.
- [ ] **Step 5: Interview Gate를 실행하고 커밋한다.** Commit: `feat: add stage-safe model features`.

### Task 3: 시간순 세 후보 OOF 평가와 고정 threshold 구현

**Files:**
- Create: `analysis/equipment_quality/model_metrics.py`
- Create: `analysis/equipment_quality/model_training.py`
- Create: `analysis/tests/test_model_metrics.py`
- Create: `analysis/tests/test_model_training.py`

**Interfaces:**
- Produces: `build_forward_folds(rows, fold_count=3) -> tuple[ForwardFold, ...]`, `classification_metrics(...) -> ClassificationMetrics`, `select_f2_threshold(...) -> float`, `evaluate_candidates(reference_rows, seed) -> CandidateEvaluationSet`.
- `CandidateEvaluationSet`은 후보별 OOF probabilities/metrics/threshold/fold 변동과 전체 reference fit 결과를 가지며 Holdout을 입력받지 않는다.

- [ ] **Step 1: metric golden test를 쓴다.** Average Precision, TP/FP/TN/FN, Recall, Precision, F2, Brier, reference prevalence 기준 Brier Skill과 F2 동률 시 높은 threshold를 고정한다.
- [ ] **Step 2: forward fold 테스트를 쓴다.** mature reference의 누적 row 25%/50%/75% 위치를 whole-date cutoff로 사용하고 경계 Charge를 train/validation 양쪽에서 제거한다. 한쪽 class가 없으면 `INSUFFICIENT_LABELS`다.
- [ ] **Step 3: 세 후보와 ranking을 구현한다.** 각 fold의 median/vocabulary/missing p99는 fold train에서만 fit해 validation을 transform한다. Logistic→HGB→RF의 고정 hyperparameter를 쓰고 모든 fit/predict를 `single_threaded()` 안에서 실행한다. ranking은 AP 내림차순, Recall 내림차순, Precision 내림차순, Brier 오름차순, fold AP 표준편차 오름차순, 마지막 고정 단순성 순서 Logistic→HGB→RF다.
- [ ] **Step 4: 반복 학습 결정론을 검증한다.** Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_model_metrics.py analysis/tests/test_model_training.py -q`. Expected: 두 실행의 후보 순위·OOF 확률·threshold digest 동일.
- [ ] **Step 5: Interview Gate를 실행하고 커밋한다.** Commit: `feat: rank quality models with ordered validation`.

### Task 4: ONNX export, Python parity, Holdout report 완성

**Files:**
- Create: `analysis/equipment_quality/model_export.py`
- Create: `analysis/equipment_quality/model_cli.py`
- Create: `analysis/tests/test_model_export.py`
- Create: `analysis/tests/test_model_cli.py`

**Interfaces:**
- Produces: `export_onnx(estimator, feature_count) -> bytes`, `onnx_probabilities(model_bytes, matrix) -> np.ndarray`, `verify_probability_parity(..., atol=1e-5) -> float`, `grouped_holdout_importance(...) -> tuple[FeatureImportance, ...]`.
- CLI 출력: `quality_model.onnx`, `quality_model.json`, `quality_model_report.json`, `quality_model_test_vectors.json` under `var/equipment-quality/model-dev/`.

- [ ] **Step 1: 세 후보 export/parity 테스트를 쓴다.** opset 21, `features: float32[?,N]`, `probabilities: float32[?,2]`, class index 1, `rtol=0/atol=1e-5`, 실제 max delta 기록, 반복 ONNX digest 동일을 검사한다. OOF 순위대로 export/parity를 시도해 첫 통과 후보를 선택하고 실패 후보와 사유를 report에 남긴다.
- [ ] **Step 2: Holdout 호출 순서 테스트를 쓴다.** exportable 후보 확정→threshold 고정→Holdout metrics→qualification→grouped raw-feature permutation importance 10회 순서를 고정하고 importance가 앞선 결정을 바꾸지 못하게 한다.
- [ ] **Step 3: 실제 데이터 CLI를 구현한다.** `python -m equipment_quality.model_cli --config ... --data-dir ... --output-dir ...` 계약으로 config `analysis/analysis_config_v2.json`, data `/Users/haechan/프로젝트/sfep-steel/steel-data`, fixed seed를 사용하며 모든 계산은 single-thread context 안에서 실행한다.
- [ ] **Step 4: 단위 테스트 후 실제 학습을 두 번 실행한다.** 아래 명령에서 output만 `model-dev-a`와 `model-dev-b`로 바꿔 두 번 실행하고 ONNX/report digest를 비교한다. Expected: 동일 digest와 Required Metrics Ledger의 Python 항목 완성.

  ```bash
  cd /Users/haechan/Desktop/SFEP/analysis
  OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
    .venv/bin/python -m equipment_quality.model_cli \
    --config /Users/haechan/Desktop/SFEP/analysis/analysis_config_v2.json \
    --data-dir /Users/haechan/프로젝트/sfep-steel/steel-data \
    --output-dir /Users/haechan/Desktop/SFEP/var/equipment-quality/model-dev-a
  ```
- [ ] **Step 5: 1단계 결과를 사용자에게 보여 준 뒤 Interview Gate를 실행하고 커밋한다.** 포트폴리오 문구는 아직 확정하지 않는다. Commit: `feat: export and evaluate ONNX quality model`.

---

## 2단계 — Java parity와 Swing 데모

### Task 5: Java에서 로컬 ONNX 직접 parity와 latency 측정

**Files:**
- Modify: `equipment-monitor/build.gradle`
- Create: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/prediction/{QualityModelMetadata,FeatureEncoder,QualityPredictor,OnnxQualityPredictor,QualityPrediction,PredictionStatus}.java`
- Create: `equipment-monitor/src/test/java/com/sfep/equipmentmonitor/prediction/{FeatureEncoderParityTest,OnnxQualityPredictorParityTest,JavaInferenceBenchmarkTest}.java`

**Interfaces:**
- `OnnxQualityPredictor.open(Path model, Path metadata) -> OnnxQualityPredictor`는 model digest/Feature digest/input-output 계약 검증 후 session을 연다.
- `predict(Map<String,RiskScalar> values, Set<String> seenFields) -> QualityPrediction`; 날짜·replayHour는 받지 않는다.

- [ ] **Step 1: ONNX Runtime `1.31.1`을 추가하고 local artifact property 기반 테스트를 쓴다.** `sfep.model-dev-dir`에서 Task 4의 네 파일을 읽으며 경로가 없으면 명시적으로 skip하고 일부 파일만 있으면 FAIL한다. `build.gradle`은 Gradle JVM의 `-Dsfep.model-dev-dir` 값을 test JVM에 전달한다.
- [ ] **Step 2: Python test vector와 Java encoder bit parity, Python ONNX↔Java ONNX max delta `<=1e-5`를 구현한다.** tensor/result/session은 try-with-resources/idempotent close로 관리한다.
- [ ] **Step 3: latency probe를 구현한다.** session/model load는 제외하고 Feature encode+`session.run`을 warm-up 1,000회, 측정 10,000회 수행해 mean·median·p95 ms와 JVM/CPU를 JSON으로 기록한다. 속도 합격선은 두지 않는다.
- [ ] **Step 4: local actual model로 세 테스트를 실행한다.** Expected: parity PASS와 Required Metrics Ledger의 Java 항목 완성.

  ```bash
  cd /Users/haechan/Desktop/SFEP
  ./sfep-server/gradlew -p equipment-monitor test \
    --tests '*FeatureEncoderParityTest' \
    --tests '*OnnxQualityPredictorParityTest' \
    --tests '*JavaInferenceBenchmarkTest' \
    -Dsfep.model-dev-dir=/Users/haechan/Desktop/SFEP/var/equipment-quality/model-dev-a \
    --no-daemon
  ```
- [ ] **Step 5: Interview Gate를 실행하고 커밋한다.** Commit: `feat: run ONNX quality inference in Java`.

### Task 6: 기존 Swing에 개발용 로컬 모델을 주입해 데모

**Files:**
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/state/{HistoricalMonitor,MaterialSnapshot}.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/alert/{AlertKind,HistoricalAlert}.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/ui/{DesktopMonitorSession,MonitorDashboardMetadata,MonitorDashboardPanel}.java`
- Modify: `equipment-monitor/src/main/java/com/sfep/equipmentmonitor/EquipmentMonitorDesktopApplication.java`
- Modify: 대응하는 state/ui/application tests.

**Interfaces:**
- 개발용 Spring properties `sfep.quality-model`과 `sfep.quality-model-metadata`가 둘 다 있을 때만 기존 v1 Bundle session에 모델을 주입한다.
- `MaterialSnapshot`은 `QualityPrediction`; `MODEL_QUALITY_RISK` key는 `(bundleId, materialKey, FURNACE_EXTRACTED, modelId)`다.

- [ ] **Step 1: 상태 테스트를 쓴다.** `FURNACE_EXTRACTED`에서 한 번만 추론하고 이후 유지하며 `QUALIFIED`+threshold 이상만 model alert를 만든다. research/input/inference 오류는 기존 range/rule/replay를 중단하지 않는다.
- [ ] **Step 2: UI 테스트를 쓴다.** 예상 불량확률·판정·AP 실제 결과를 표시하고 최근 항목이 위에 오며 접근성 이름이 존재한다.
- [ ] **Step 3: standalone 주입을 최소 구현한다.** 두 property 중 하나만 있거나 digest가 다르면 시작 실패한다. 이 경로에는 `shortcut: development-only until Bundle v3; remove in Task 10` 주석을 둔다.
- [ ] **Step 4: 실제 v1 Bundle+Task 4 model로 Swing을 실행해 화면과 전체 replay를 확인한다.** 기존 통계 alert/state digest는 model alert를 제외하고 같아야 한다. Bundle 경로는 `var/equipment-quality/run-a/` 아래 인증된 `sha256:*` 디렉터리 하나를 사용하고 다음 계약으로 실행한다.

  ```bash
  cd /Users/haechan/Desktop/SFEP
  ./sfep-server/gradlew -p equipment-monitor bootRun --no-daemon \
    --args="--sfep.equipment-monitor.bundle-dir=/Users/haechan/Desktop/SFEP/var/equipment-quality/run-a/<sha256:bundle-id> --sfep.quality-model=/Users/haechan/Desktop/SFEP/var/equipment-quality/model-dev-a/quality_model.onnx --sfep.quality-model-metadata=/Users/haechan/Desktop/SFEP/var/equipment-quality/model-dev-a/quality_model.json"
  ```
- [ ] **Step 5: 2026-11-14 23:00 KST gate를 판정하고 Interview Gate를 실행한다.** Java parity가 실패하면 여기서 ONNX 작업을 중단하고 main에 병합하지 않는다. 성공 시 Commit: `feat: show local quality probability in Swing`.

---

## 3단계 — 봉인과 Bundle v3 완성도

### Task 7: Python wheelhouse와 producer runtime 봉인

**Files:**
- Modify: `analysis/{pyproject.toml,requirements.lock,wheelhouse.lock.json,producer.lock,producer_runtime.json}`
- Modify: `analysis/tools/{build_producer,seal_producer_build,seal_runtime}.py`
- Modify: `analysis/tests/test_python312_restore.py`
- Keep: `analysis/model-requirements.txt` as the lightweight developer/test bootstrap; production runtime dependency는 canonical lock에도 별도로 흡수한다.

- [ ] **Step 1:** package `1.2.0`과 Task 1 direct/transitive dependency hash inventory 실패 테스트를 추가한다.
- [ ] **Step 2:** wheelhouse, producer wheel/lock, runtime manifest를 두 번 재생성해 digest 동일을 확인한다.
- [ ] **Step 3:** sealed runtime으로 Task 1~4 테스트와 실제 모델 두 번 생성을 다시 실행한다.
- [ ] **Step 4:** full Python pre-v3 regression을 통과시킨다.
- [ ] **Step 5:** Interview Gate 후 Commit: `build: seal ONNX training runtime`.

### Task 8: Bundle v3 계약·identity·원자 publication 추가

**Files:**
- Create: `analysis/analysis_config_v3.json`
- Create: Python/package 및 `contracts/equipment-monitor/v3/`의 config/manifest/model-metadata schemas.
- Modify: Python `bundle_contract.py`, `models.py`, `schema.py`, `artifacts.py`, `cli.py`와 contract/artifact tests.
- Modify: Java bundle `ArtifactRole`, `SchemaRole`, `BundleContract`, `BundleContracts`, `BundleLayout`, `EmbeddedSchemas`와 contract tests.

**Interfaces:**
- v1/v2 정확히 6 artifact; v3 정확히 8 artifact(`quality_model.onnx`, `quality_model.json` 추가).
- `sfep-quality-model-id/v1`; v3 bundleId는 modelId·ONNX digest·metadata digest·metadata schema digest를 결속한다.

- [ ] **Step 1:** v1/v2 회귀와 v3 exact inventory/unknown version/missing-extra 거부 테스트를 쓴다.
- [ ] **Step 2:** profile별 artifact/schema 목록과 v3 schema를 구현한다.
- [ ] **Step 3:** model identity와 순환 참조 없는 metadata→bundle identity 계산을 구현한다.
- [ ] **Step 4:** binary ONNX를 포함한 no-clobber/atomic publication 변조 테스트를 통과시킨다.
- [ ] **Step 5:** Interview Gate 후 Commit: `feat: define sealed Bundle v3 model contract`.

### Task 9: v3 pipeline·합성 golden·secure Java loader 연결

**Files:**
- Modify: Python `model_training.py`, `model_cli.py`, `cli.py`, `artifacts.py`, `summary.py`.
- Create: `analysis/tools/seal_golden_bundle_v3.py`, v3 합성 golden source/bundle/expectation.
- Modify: Java `BundleLoader`, `LoadedBundle`, `ReplayCsvValidator`.
- Create: Java `QualityModelArtifact`, v3 loader/inference parity tests.

- [ ] **Step 1:** Task 4 core를 v3 `run_analysis`에 연결해 8 artifact를 두 번 동일하게 발행한다.
- [ ] **Step 2:** 합성 golden vector와 ONNX를 Git에 봉인하고 Python/Java parity를 `1e-5`로 검사한다.
- [ ] **Step 3:** Java loader가 schema/digest/modelId/opset/input-output/feature contract를 검증한 뒤에만 session을 열게 한다.
- [ ] **Step 4:** model/metadata 교체·경로 탈출·key 누락·trigger 누락·resource close 테스트를 통과시킨다.
- [ ] **Step 5:** Interview Gate 후 Commit: `feat: load verified Bundle v3 models`.

### Task 10: 임시 경로 제거·실제 전체 회귀·문서 완료

**Files:**
- Remove: Task 6의 standalone model system-property 주입과 shortcut comment.
- Modify: application/session이 `LoadedBundle.qualityModel()`만 사용하도록 변경.
- Create: actual v3 loader/replay/UI tests.
- Create: `docs/SFEP_ONNX_QUALITY_MODEL.md`.

- [ ] **Step 1:** 실제 데이터로 v3를 두 번 생성해 8 artifact와 Bundle ID digest 동일을 확인한다.
- [ ] **Step 2:** 전체 189,043 replay event와 model alert 제외 기존 semantic digest, 최근순 정렬, v1/v2 회귀를 확인한다.
- [ ] **Step 3:** Python 전체 suite와 Java `check`+actual v1/v2/v3 tasks를 통과시키고 실제 데이터/ONNX가 `git status`에 없는지 확인한다.
- [ ] **Step 4:** Required Metrics Ledger의 숫자와 qualification을 문서·Swing에서 교차 확인한 뒤 사용자에게 보여 준다. 포트폴리오 문구는 사용자 확인 후 별도 수정한다.
- [ ] **Step 5:** 최종 Interview Gate 후 Commit: `docs: verify ONNX quality model end to end`.

## Self-Review Result

- 실행 순서를 지표→Java parity→Swing 데모→봉인으로 바꿔 1단계만 끝나도 모델 가치와 실패 여부를 확인할 수 있다.
- `f_ext_time`은 실제 0~23시 출측 시각임을 코드와 원천 값으로 확인해 Feature 수를 28개로 고쳤다.
- fold별 전처리를 fold train에서만 fit하도록 명시해 validation 분포 누출을 막았다.
- HGB native thread, parity `1e-5`, Holdout importance, Java latency와 hard stop을 각 소유 Task의 테스트/산출물에 연결했다.
- 2단계 standalone 주입은 임시 경로로 명시하고 Task 10에서 제거해 v1 Bundle 계약을 영구적으로 우회하지 않는다.
- 다단계 모델, calibration, Python 서버, Kafka 실시간 연결은 이번 마감과 무관해 제외했다.
