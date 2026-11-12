# SFEP ONNX 품질 불량확률 하이브리드 설계

## 1. 목적

SFEP Steel의 기존 통계 기반 설비·품질 모니터링을 유지하면서, 실제 철강 공정 데이터로 학습한 품질 불량확률 모델을 ONNX로 배포하고 Java Swing 모니터에서 추론한다.

이 기능의 예측 대상은 기계 고장이 아니라 AP 공정에서 최종 확인되는 `judge=불량`이다. 모델은 가열로 출측까지 공개된 선행 공정값만 사용하며, 기존 운전범위·품질 위험규칙을 대체하지 않는다.

사용자는 한 소재에 대해 다음 네 가지를 함께 확인한다.

1. 설비 운전범위 이탈
2. 불량 Label과 통계적으로 연관된 품질 위험규칙
3. ONNX 모델의 예상 불량확률
4. AP 도착 후 실제 `judge` 결과

## 2. 현재 상태와 근거

- Python producer는 소재 계보, 시간 분할, 운전범위, 품질 위험구간, 재생 이벤트를 결정론적 Bundle로 생성한다.
- Java `equipment-monitor`는 Bundle을 검증한 뒤 공정 이벤트를 시간순으로 재생한다.
- 현재 실제 Bundle 기준 성숙한 reference Label은 불량 59건, 양품 5,606건이다.
- 독립 holdout은 불량 282건, 양품 6,444건이며 reference와 불량률 차이가 크다.
- 현재 저장소에는 ONNX Runtime 의존성, ONNX 모델, Feature encoder, Java 추론 코드가 없다.
- 기존 v1/v2 Bundle은 정확히 6개 artifact를 요구하므로 모델 파일을 몰래 추가할 수 없다.

이 분포에서는 Accuracy를 성공 기준으로 사용하지 않는다. 모델이 모든 소재를 양품으로 예측해도 Accuracy가 높게 보일 수 있기 때문이다.

## 3. 목표

- 기존 데이터만 사용해 `judge=불량` 이진 분류 모델을 학습한다.
- 결과 누출 없이 가열로 출측 시점에 사용할 수 있는 Feature만 입력한다.
- 시간순 검증으로 후보 모델을 비교하고 선택 근거를 남긴다.
- 선택 모델을 ONNX로 변환하고 Python과 Java 추론값 일치를 검증한다.
- 모델·Feature 계약·평가결과·임계값·해시를 검증 가능한 Bundle에 포함한다.
- Java Swing에서 불량확률과 모델 상태를 기존 통계 근거 옆에 표시한다.
- AP 결과가 공개되면 예측과 실제 `judge`를 같은 소재 화면에서 비교한다.
- 모델 성능이 기준을 통과하지 못하면 확률은 연구용으로만 표시하고 모델 경보는 발생시키지 않는다.

## 4. 비목표

- 모터·베어링·밸브 등 기계 고장 예측
- 새로운 외부 데이터 수집 또는 합성 Label 생성
- Kafka 기반 실시간 서버나 별도 Python 추론 서버 추가
- ONNX 모델로 기존 통계 규칙 대체
- 상관관계를 고장 또는 불량의 인과 원인으로 표현
- 실제 원천 CSV, 실제 학습 Bundle 또는 실제 ONNX 모델을 Git에 커밋
- 높은 성능 수치를 미리 보장하거나 Holdout 결과를 숨김
- 첫 버전에서 공정 단계별 모델 여러 개 운영

## 5. 검토한 접근법

### 5.1 선택: 통계 규칙과 ONNX 모델 병행

기존 규칙은 판단 근거를 제공하고 ONNX 모델은 누적 공정값을 하나의 확률로 요약한다. 모델이 자격 기준을 통과하지 못해도 기존 모니터 기능은 그대로 동작한다.

장점:

- 설명 가능성과 예측 기능을 함께 유지한다.
- 모델 실패가 기존 모니터를 무력화하지 않는다.
- 포트폴리오에서 통계적 연관성과 예측을 구분해 설명할 수 있다.

비용:

- Bundle 계약, Python 학습, Java Runtime, UI를 모두 연결해야 한다.
- 규칙 위험등급과 모델 확률이 다를 때 둘을 별도로 설명해야 한다.

### 5.2 대안: 가중 로지스틱 모델 하나만 사용

가장 작고 설명하기 쉽지만 비선형 관계를 놓칠 수 있다. 단독 고정 모델로 결정하지 않고 후보군의 기준선으로 포함한다.

### 5.3 제외: ONNX 모델로 규칙 대체

현재의 통계적 근거와 품질 위험구간을 잃고 작은 불량 표본에 전체 판정을 의존하므로 제외한다.

### 5.4 제외: 별도 Python 모델 서버

네트워크, 서버 생명주기, 배포 단위가 추가되고 현재 오프라인 Swing 재생 구조와 맞지 않으므로 제외한다.

## 6. 사용자에게 보이는 동작

### 6.1 최초 예측 시점

첫 버전은 `FURNACE_EXTRACTED` 이벤트가 공개된 직후 한 번 추론한다.

이 시점에는 제강·연주와 가열로 공정값이 모두 공개되어 있고, RM4와 AP 결과는 아직 공개되지 않았다. 따라서 가열로 중심 모니터링이라는 목표를 유지하면서 결과 누출을 막을 수 있다.

예측은 이후 RM4 단계에서도 유지하며 AP 결과가 도착하면 실제 `judge`와 비교한다. `FURNACE_EXTRACTED` 이전에는 `예측 대기 · 가열로 출측 후 계산`으로 표시한다.

### 6.2 화면 표시

기존 개요 및 소재 상세에 다음 정보를 추가한다.

- 예상 불량확률: 0~100%
- 모델 판정: 기준 미만 / 기준 이상 / 연구용 / 예측 대기 / 입력 부족 / 추론 실패
- 예측 시점: 가열로 출측 완료
- 모델 ID와 Holdout 기준 요약
- 기존 품질 위험규칙과 운전범위 이탈 근거
- AP 실제 결과: 대기 / 양품 / 불량
- 예측과 결과 비교: 적중 여부가 아니라 확률과 실제 Label을 병렬 표시

모델이 `RESEARCH_ONLY`이면 확률 옆에 `독립 평가 기준 미달 · 경보 미발행`을 명시한다.

### 6.3 경보

- `QUALIFIED` 모델만 확률 임계값을 넘을 때 `MODEL_QUALITY_RISK` 경보를 생성한다.
- 모델 판정 임계값은 하나만 사용한다. 임계값 미만은 `기준 미만`, 이상은 `기준 이상`으로 표시한다. 기존 통계 규칙의 `CAUTION`/`DANGER` 등급과 모델 확률 등급을 억지로 합치지 않는다.
- 기존 `QUALITY_RISK`와 `OPERATING_RANGE` 경보는 변경하지 않는다.
- 모델 경보는 `(bundleId, materialKey, FURNACE_EXTRACTED, modelId)`로 중복 억제한다.
- 연구용 모델, 입력 부족, 추론 오류는 모델 경보를 만들지 않는다.
- 추론 오류는 해당 소재의 모델 상태만 실패시키며 재생 전체를 중단하지 않는다. 단, 모델 파일·명세·해시가 잘못된 Bundle은 로딩 단계에서 실패한다.

## 7. 학습 데이터와 누출 방지

### 7.1 Target

- `judge == "불량"` → 1
- `judge == "양품"` → 0
- 미성숙, 미연결, 알 수 없는 Label → 학습·평가 제외

### 7.2 허용 Feature

`FURNACE_EXTRACTED`까지 실제로 공개되는 다음 계열만 허용한다.

- 제강·연주: 공장, 강종, 용도, 연주 구분, Slab 구분, Tundish 온도, MLAC 비율, Delta Ferrite, 성분값, Slab Grind
- 가열로: 호기, 장입 구분, 장입 온도, Slab 폭, 예열·가열·균열 온도와 시간, BFG/COG/LDG 사용량과 비율
- 시간 Feature는 사용하지 않는다. 원천 `f_ext_time`은 가열로 체류시간이 아니라 0~23시의 출측 시각이므로 날짜·교대의 대리변수가 될 수 있어 제외한다.

### 7.3 금지 Feature

- `judge`
- AP 공장, 날짜, 교대, 두께, 폭, Line Speed 등 AP 단계 값
- RM4 온도, Pitch, 열연 두께·폭·일자 등 출측 이후 값
- 소재·Charge·Slab·Coil·제품 식별자
- 원천 행 순서, Bundle ID, Split 이름, Label 공개 여부
- `f_ext_date`, `f_ext_time`, `cast_date`, `hr_date`, `ap_date` 등 날짜·시각 Feature
- 미래 공정에서만 알 수 있는 파생값

Python 테스트는 Feature allowlist와 denylist를 모두 검사한다. 금지 Feature가 하나라도 포함되면 학습을 실패시킨다.

## 8. 시간순 학습과 후보 선택

### 8.1 분할

- Bundle의 전역 `asOf`와 기존 38일 성숙도 규칙으로 `AVAILABLE`이 된 reference Label만 모델 개발에 사용한다. 이 작업은 `asOf` 시점에 한 번 모델을 만드는 회고적 개발이며 과거 매일 재학습한 것처럼 주장하지 않는다.
- 날짜를 섞지 않는 forward-chaining 검증 fold를 만든다. 각 fold는 전역 `asOf`에서 이미 성숙한 행을 `hr_date`로 나누고, 학습에는 검증 시작일보다 앞선 행만 사용한다.
- 같은 Charge가 fold의 학습·검증 양쪽 날짜에 걸치면 기존 split과 같은 방식으로 해당 Charge 전체를 양쪽에서 제거한다.
- 검증 Label은 점수 계산에만 사용하며 Feature·전처리·후보 학습·임계값 학습에는 들어가지 않는다. 각 fold의 median, vocabulary, missing p99는 그 fold의 train 구간에서만 fit하고 validation에는 transform만 한다. 최종 후보를 전체 reference로 다시 학습할 때만 최종 Feature 계약을 전체 reference에서 fit해 Holdout과 Java에 동일하게 적용한다. fold별 과거 배포를 재현하지 않으므로 fold마다 별도의 과거 `asOf`를 만들지는 않는다.
- 임계값과 후보 선택은 reference 내부의 out-of-fold 예측만 사용한다.
- 최종 후보 선택과 임계값 고정 후 전체 성숙 reference로 다시 학습한다.
- 기존 holdout은 마지막 독립 평가에 한 번만 사용한다.
- Holdout 결과로 Feature, 후보 또는 임계값을 다시 조정하지 않는다.

각 fold에 불량 Label이 너무 적어 지표가 정의되지 않으면 fold 수를 자동으로 늘리거나 무작위 분할로 바꾸지 않고 `INSUFFICIENT_LABELS`로 실패한다.

### 8.2 후보 모델

고정 버전의 scikit-learn 계열에서 다음 세 후보만 비교한다.

1. class weight를 적용한 Logistic Regression
2. 깊이와 leaf 크기를 제한한 Random Forest
3. 깊이와 반복 수를 제한한 Histogram Gradient Boosting

고정값은 Logistic `class_weight="balanced", solver="liblinear", max_iter=1000`; Random Forest `n_estimators=200, max_depth=6, min_samples_leaf=10, class_weight="balanced_subsample", n_jobs=1`; HGB `class_weight="balanced", learning_rate=0.05, max_iter=100, max_depth=4, min_samples_leaf=20, l2_regularization=1.0, early_stopping=False`다. `random_state`는 인증된 v2 bootstrap seed SHA-256의 마지막 8 hex를 unsigned int로 변환해 세 후보가 공유한다.

모든 난수 seed는 설정에 고정하고 병렬 학습은 결정론을 위해 제한한다. Logistic/Random Forest의 `n_jobs`뿐 아니라 HGB가 사용하는 OpenMP·BLAS도 `threadpoolctl.threadpool_limits(limits=1)` 안에서 학습·예측·변환한다. 개발 실행 명령도 `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `VECLIB_MAXIMUM_THREADS=1`을 명시한다. ONNX 변환과 Java parity를 통과하지 못하는 후보는 성능과 관계없이 탈락한다.

sklearn native↔Python ONNX 및 Python ONNX↔Java ONNX 확률 parity는 `rtol=0`, `atol=1e-5`로 판정한다. 트리 모델의 float32 누적 차이를 허용하되 실제 최대 절대 차이는 별도 지표로 기록한다.

### 8.3 전처리

- 숫자형: reference median 대치, 고정된 순서 유지
- 범주형: reference에서 확정한 vocabulary one-hot encoding, 미등록 값은 `UNKNOWN`
- 결측 여부가 의미 있는 Feature는 명시적 missing indicator 추가
- 모델 입력은 `float32[1, N]` 단일 tensor
- 각 원시 Feature의 null은 위 정책으로 대치하며, 대치 전 결측 Feature 수를 함께 계산한다.
- `maxMissingFeatureCount`는 reference 결측 수 분포의 p99로 고정해 metadata에 기록한다. 이를 초과하는 행은 학습·평가·Java 추론에서 모두 `INPUT_INCOMPLETE`로 제외한다.
- 키 자체 누락, 타입 위반, trigger stage 누락은 대치하지 않는다. Bundle 또는 재생 계약 오류로 처리한다.

전처리는 ONNX 그래프 밖의 명시적 Feature 계약으로 둔다. Python과 Java가 같은 `quality_model.json`을 읽고 동일한 벡터를 생성하며 golden vector로 byte/값 parity를 검증한다.

### 8.4 선택 지표

Accuracy와 ROC-AUC만으로 선택하지 않는다.

우선순위:

1. PR-AUC
2. F2 기준 임계값에서 불량 Recall
3. Precision
4. Brier Score와 Brier Skill Score
5. 시간 fold 사이 성능 변동

모든 수치가 동률이면 사전 고정한 단순성 순서 Logistic Regression, Histogram Gradient Boosting, Random Forest를 사용한다. ONNX 파일 크기를 보고 후보 순서를 뒤집지는 않는다.

OOF 순위는 ONNX 변환 전에 확정한다. 이후 순위대로 변환과 Python parity를 시도해 첫 통과 후보를 최종 후보로 삼으며, 실패 후보와 실패 사유를 report에 남긴다. 따라서 변환 가능성은 Holdout을 보지 않고 적용되는 배포 적합성 gate다.

`PR-AUC`는 사다리꼴 보간값이 아니라 Average Precision으로 계산한다. 운영 임계값은 reference out-of-fold 예측에서 F2가 최대가 되는 값 하나로 고정하며, 동률이면 더 높은 임계값을 선택한다. 이 임계값을 고정한 뒤에만 Holdout을 평가한다.

Brier Skill Score의 기준 예측은 Holdout 불량률이 아니라 전체 성숙 reference 불량률을 모든 행에 적용한 상수 예측이다. 따라서 미래 Holdout 분포를 이용해 기준 확률을 유리하게 바꾸지 않는다.

### 8.5 자격 상태

최종 Holdout 평가 후 모델 상태를 결정한다.

- `QUALIFIED`: ONNX parity 통과, Average Precision이 Holdout prevalence 기준선보다 높고, reference prevalence 기준 Brier Skill Score가 0보다 크며, 미리 고정한 단일 F2 임계값에서 예측 양성과 실제 양성이 모두 존재
- `RESEARCH_ONLY`: 모델과 추론은 정상이나 위 자격 기준 중 하나라도 미달
- `INVALID`: 계약·ONNX·Feature parity 또는 추론 유한값 검증 실패

Holdout은 모델 선택에 사용하지 않지만, 운영 경보 허용 여부를 결정하는 최종 안전 게이트로만 사용한다. 모든 지표와 confusion count를 그대로 Bundle에 기록한다.

Feature 연관 기여도는 모델·threshold·qualification을 모두 고정한 뒤 Holdout에서만 계산한다. 한 raw Feature에 대응하는 one-hot·missing indicator 열을 함께 섞는 grouped permutation importance를 고정 seed로 10회 반복하고 Average Precision 감소량의 평균과 표준편차를 기록한다. 이 결과는 설명용이며 후보 선택·임계값·qualification을 다시 바꾸지 않고 인과관계로 표현하지 않는다.

## 9. Bundle v3 계약

### 9.1 호환성

- v1/v2 Bundle 여섯 artifact와 의미를 변경하지 않는다.
- 새 `sfep-equipment-bundle/v3`만 모델 artifact를 요구한다.
- Java loader는 v1/v2를 기존과 동일하게 로드하고 모델 상태를 `NOT_AVAILABLE`로 표시한다.
- Python은 명시적으로 v3 출력을 요청했을 때만 모델을 학습한다.

### 9.2 신규 artifact

v3는 기존 여섯 artifact 뒤에 다음 두 개를 고정 순서로 추가한다.

1. `quality_model.onnx`
   - role: `quality_model`
   - schemaVersion: `sfep-quality-model-onnx/v1`
   - SHA-256과 size 검증
2. `quality_model.json`
   - role: `quality_model_metadata`
   - schemaVersion: `sfep-quality-model-metadata/v1`
   - Feature 순서·타입·대치값·범주 vocabulary
   - input/output 이름, shape, ONNX opset
   - 후보별 validation 지표와 선택 이유
   - 고정 임계값과 Holdout 지표
   - qualification 상태와 사유
   - 학습 기간, 평가 기간, Label count
   - modelId, ONNX digest, Feature contract digest
   - Holdout grouped permutation importance는 `연관 기여도`로만 기록하고 인과로 표현하지 않음

### 9.3 식별자

- 기존 `criteriaId`는 통계 규칙의 의미를 유지한다.
- 별도 `modelId`를 신규 namespace로 계산한다.
- v3 `bundleId` 계산에는 모델과 metadata digest, 신규 schema digest, modelId를 포함한다.
- 모델 변경은 `bundleId`와 `modelId`를 바꾸지만 같은 통계 기준이면 `criteriaId`는 유지된다.

### 9.4 실제 데이터 보호

- 실제 v3 Bundle과 ONNX 파일은 `var/equipment-quality/` 아래에 생성하고 Git에 추가하지 않는다.
- 저장소에는 작은 합성 데이터로 만든 golden v3 Bundle만 포함한다.
- 실제 데이터의 개별 행이나 식별자는 metadata와 모델 report에 기록하지 않는다.

## 10. Python producer 변경

신규 책임은 기존 분석 단계 뒤, artifact publication 전에 실행한다.

```text
검증·계보·시간분할
→ 기존 운전범위·품질규칙
→ 모델용 누출 방지 projection
→ 시간순 후보 학습·선택
→ 전체 reference 재학습
→ Holdout 독립 평가·qualification
→ ONNX 변환·Python parity
→ 모델 metadata 생성
→ v3 Bundle 원자 publication
```

필요 모듈:

- `model_features.py`: allowlist, projection, encoder 계약
- `model_training.py`: fold, 후보 학습, 선택, 임계값
- `model_metrics.py`: PR-AUC, confusion count, Brier 계열
- `model_export.py`: ONNX 변환, digest, parity
- 기존 `artifacts.py`, `schema.py`, `pipeline.py`: v3 artifact와 원자 publication 연결

학습·변환 의존성은 정확한 버전으로 lock하고 sealed producer runtime의 검증 대상에 포함한다.

## 11. Java monitor 변경

### 11.1 로딩

- `BundleContracts`에 v3 profile 추가
- artifact 역할과 schema 역할을 버전별 집합으로 다룰 수 있게 변경
- v1/v2는 여섯 역할, v3는 여덟 역할을 정확히 검증
- model metadata schema, ONNX size/digest, metadata 내부 digest 상호참조 검증
- 검증이 끝나기 전에는 ONNX session을 열지 않음

### 11.2 추론

- Microsoft ONNX Runtime Java를 고정 버전으로 추가
- 애플리케이션당 environment 하나, Bundle당 session 하나 생성
- `FURNACE_EXTRACTED`에서 누적 소재값을 Feature encoder로 변환
- input/output 이름, dtype, shape, 유한 확률, 0~1 범위를 검사
- session과 tensor는 명시적으로 close하고 Spring Context 종료 시 environment를 정리

### 11.3 상태와 UI

- 소재 snapshot에 prediction probability, grade, modelId, qualification, predictedAtStage 저장
- 통계 품질위험과 모델 품질위험은 별도 필드와 별도 색상/문구 사용
- 개요 표는 기존 열을 보존하면서 `예상 불량확률` 열을 추가
- 상세 패널에 모델 지표, 현재 예측, 기존 규칙 근거, AP 실제 결과를 묶어 표시
- 최근 항목이 위에 오는 기존 정렬 규칙을 유지
- v1/v2 Bundle은 UI에서 `ONNX 모델 없음`으로 정상 동작

## 12. 오류 처리

- 계약·해시·schema·model digest 불일치: Bundle 로드 실패
- 지원하지 않는 opset 또는 input/output 계약: Bundle 로드 실패
- 소재 Feature 누락: 해당 소재 `INPUT_INCOMPLETE`, 경보 없음
- 추론 NaN/Infinity/범위 이탈: 해당 소재 `INFERENCE_FAILED`, 경보 없음, 오류 이력 기록
- 연구용 모델: 확률 표시 가능, 경보 없음
- AP 결과 불일치나 예측 실패는 기존 통계 모니터 상태를 덮어쓰지 않음

## 13. 검증 전략

### 13.1 Python

- 금지 Feature 누출 테스트
- 날짜 기준 forward fold 테스트
- 불량이 없는 fold fail-closed 테스트
- seed 고정 및 반복 실행 model/metadata digest 결정론 테스트
- 후보 선택 tie-break 테스트
- threshold가 holdout 전에 고정되는지 테스트
- PR-AUC, Brier, confusion count golden test
- Python native probability와 ONNX probability parity
- HGB/OpenMP/BLAS가 학습·예측 구간에서 1 thread로 제한되는지 검증
- 실제 데이터 v3 두 번 생성 시 동일 Bundle 검증

### 13.2 Java

- v1/v2 회귀 로딩 테스트
- v3 golden Bundle 로딩 테스트
- artifact 변조·경로 탈출·digest 불일치 거부 테스트
- Python golden vector와 Java Feature encoder parity
- Python ONNX 결과와 Java ONNX 결과의 최대 절대 차이 및 `atol=1e-5` 통과 여부
- Java session을 미리 연 뒤 encoder+추론 1건의 warm-up 1,000회, 측정 10,000회 latency 평균·중앙값·p95
- 입력 부족·연구용·추론 실패 시 경보 억제 테스트
- session/resource 종료 테스트
- Swing 예측 대기/확률/연구용/AP 결과 상태 테스트

### 13.3 실제 데이터 승인 조건

- 실제 원천 데이터가 Git status에 나타나지 않음
- v3 Bundle 생성 성공 및 반복 digest 동일
- Java loader와 실제 Bundle 테스트 통과
- 전체 189,043개 재생 이벤트 의미와 기존 경보 결과가 모델 추가 전과 동일
- 모델 경보를 제외한 기존 상태·규칙·정렬 semantic digest 동일
- Holdout 결과가 metadata와 화면에서 동일
- QUALIFIED가 아니면 UI와 문서가 성능 미달을 숨기지 않음

## 14. 포트폴리오 반영 조건

다음 조건을 모두 충족한 뒤에만 ONNX 구현 완료를 포트폴리오에 작성한다.

- 실제 v3 Bundle로 Python→ONNX→Java 추론 성공
- Python/Java parity 테스트 통과
- 누출 방지 테스트 통과
- Holdout 지표와 Label count 공개
- 모델 qualification 상태 공개

표현은 `설비 고장 예측`이 아니라 `선행 공정조건 기반 최종 품질 불량확률 추론`으로 제한한다.

## 15. 작업 크기, 실행 순서와 마감선

이 작업은 먼저 포트폴리오 가치가 있는 세로 한 줄을 통과시키고, 그 뒤 배포 완성도를 올린다.

### 15.1 1단계 — Python 지표와 ONNX 산출물

1. 개발용 requirements만 고정하고 Python 3.12 환경을 복구한다.
2. Feature projection·누출 방지를 구현한다.
3. 시간순 세 후보를 평가하고 OOF 순위를 확정한다.
4. OOF 순위대로 ONNX 변환과 Python native parity를 검사해 첫 통과 후보를 확정한다.
5. Holdout 지표·qualification·Holdout permutation importance와 로컬 `.onnx`를 출력한다.

이 단계가 끝나면 Holdout 전체/불량 수, 선택 모델과 OOF AP 순위, threshold 기준 Recall·Precision·AP, qualification, Python parity 최대 차이를 공개한다.

### 15.2 2단계 — Java parity와 Swing 데모

1. v3 Bundle보다 먼저 로컬 ONNX+metadata를 Java에서 직접 검증·로딩한다.
2. Python golden vector와 Java 확률을 비교한다.
3. 기존 v1 Bundle 재생에 개발용 로컬 모델을 주입해 Swing에 확률과 상태를 표시한다.
4. Java encoder+ONNX 1건 latency를 warm-up 1,000회, 측정 10,000회로 기록한다.

### 15.3 3단계 — 봉인과 배포 완성도

1. wheelhouse/runtime을 봉인한다.
2. Bundle v3 계약·identity·원자 publication을 추가한다.
3. 합성 golden fixture와 secure Java v3 loader를 완성한다.
4. 개발용 로컬 모델 주입 경로를 제거하고 실제 v3 전체 회귀와 문서를 마친다.

### 15.4 중단 기준

2026-11-14 23:00 KST까지 Python ONNX↔Java 확률 parity가 통과하지 않으면 ONNX를 포트폴리오 완료 항목에서 제외한다. 미완성 로컬 모델 주입 코드는 main에 병합하지 않고, 2026-11-16 상담과 2026-11-17 SEMES 일정에 영향을 주는 추가 구현을 중단한다.

각 Task 종료 시 별도 에이전트가 실제 diff와 테스트만 읽고 `문제 → 선택 → 대안 → 트레이드오프 → 검증` 순서의 60초 면접 답변을 작성한다. 미확인 수치나 코드에 없는 설명은 기록하지 않는다.

## 16. 설계상 결정

- 사용자가 선택한 접근: 통계 규칙 + ONNX 하이브리드
- 첫 예측 시점: `FURNACE_EXTRACTED`
- 첫 버전 모델 수: 선택된 모델 하나
- 배포 방식: Bundle 내부 ONNX, 별도 서버 없음
- 호환성 방식: 기존 v1/v2 유지, 신규 v3 추가
- 모델 실패 정책: 기존 모니터 유지, 모델 경보 fail-closed
- 실제 데이터 정책: 로컬 전용, Git에는 합성 fixture만 포함

