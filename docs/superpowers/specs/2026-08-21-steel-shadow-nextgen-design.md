# Steel Shadow Validation and Next-Generation Model Design

## 1. 목적과 현재 상태

현재 `steel-quality-baseline-v0.3`과 `steel-quality-challenger-v0.2`는 과거 데이터로 개발·진단됐으며 Challenger는 의도적으로 `deployment_eligible=false`다. 작업 폴더에는 2025-09-29 이후에 생성된 새로운 SM/CC·HR·AP 생산 데이터가 없다. 따라서 기존 HOLDOUT을 다시 사용해 독립 Shadow 성능이라고 주장하지 않는다.

이번 설계의 목적은 두 가지다.

1. 미래 코일의 정답을 모르는 시점에 동결 모델을 동시에 실행하고, 정답이 확정된 뒤에만 비교하는 Shadow 검증 체계를 만든다.
2. 기존 HOLDOUT을 모델 선택에 사용하지 않고 더 안정적인 차세대 후보를 개발해 같은 Shadow 체계에 등록한다.

구현 완료 시점의 올바른 상태는 `AWAITING_FUTURE_DATA`다. 실제 미래 데이터가 Gate 요건을 충족하기 전에는 어떤 모델도 자동 배포하지 않는다.

## 2. 범위

### 포함

- 동결 모델 Registry와 SHA-256 계보 검증
- 미래 Feature Batch의 정답 없는 Shadow 추론
- 수정 불가능한 Batch 단위 예측 Ledger
- 지연 도착한 확정 Label의 별도 적재와 결합
- 모델별 성능·Drift·Calibration·운영 Gate 평가
- 기존 데이터의 개발 가능 구간만 사용하는 차세대 모델 탐색
- 미래 데이터가 없을 때의 명시적 Dry Run과 `AWAITING_FUTURE_DATA` 상태
- 단위·통합·누수 회귀 테스트와 재현 명령 문서화

### 제외

- PLC/Modbus/Kafka 실시간 연결
- 모델 자동 배포 또는 자동 정비 Ticket 발행
- SHAP 연관성을 설비 고장 원인으로 단정하는 기능
- 기존 HOLDOUT을 새로운 독립 Shadow 근거로 재사용하는 행위
- AP 운전조건을 열연 직후 예측 Feature로 사용하는 행위
- 새로운 외부 ML 패키지 설치

## 3. 핵심 원칙

1. **Predict first, label later:** 예측 Batch에는 `judge`, `ap_date`, `label_finalized_at`을 허용하지 않는다.
2. **Frozen contract:** 모델 파일, Feature Schema, Threshold, Software Version, 학습 Cohort Hash를 Registry에 기록한다.
3. **Append-only evidence:** 이미 기록된 예측 Batch를 덮어쓰지 않는다. 같은 `hr_coil_id`의 중복 예측은 명시적으로 거부한다.
4. **No HOLDOUT selection:** 차세대 후보의 Feature·Hyperparameter·Threshold 선택은 2025-09-01 이전 개발 Cohort만 사용한다.
5. **Human deployment gate:** 평가기는 추천 상태만 생성하며 `deployment_eligible`을 자동으로 `true`로 바꾸지 않는다.
6. **Causal restraint:** Feature 중요도와 SHAP은 점검 순서 후보일 뿐 공정 원인이나 설비 고장의 증거가 아니다.

## 4. 아키텍처

### 4.1 파일 구성

- `training/steel/shadow_contract.py`
  - Feature Batch, Label Batch, Registry, Ledger의 스키마와 무결성 검증
- `training/steel/run_shadow.py`
  - `register`, `score`, `ingest-labels`, `evaluate`, `status`, `dry-run` 명령 제공
- `training/steel/train_nextgen.py`
  - 개발 Cohort Cross-Validation, 후보 선택, 최종 재학습, Artifact 생성
- `training/steel/tests/test_shadow_contract.py`
  - 누수 열, 중복 Coil, 시간 순서, Hash, 불변 Ledger 테스트
- `training/steel/tests/test_run_shadow.py`
  - 정답 없는 추론부터 지연 Label 평가까지의 통합 테스트
- `training/steel/tests/test_train_nextgen.py`
  - HOLDOUT 불변성, Group 분리, 후보 선택, Gate 고정 테스트
- `training/steel/shadow_registry/registry.json`
  - 동결 모델과 정책 Threshold 계약
- `training/steel/nextgen_output/`
  - 개발 결과, OOF 예측, Leaderboard, 선택 Artifact, Model Card
- `training/steel/shadow_output/`
  - 미래 실행 시 생성되는 불변 예측·Label·평가 Batch
- `training/steel/shadow_dry_run_output/`
  - 기능 확인 전용 결과. 실제 Shadow 증거와 물리적으로 분리

### 4.2 데이터 흐름

1. `register`가 Baseline·Challenger·Next Challenger Artifact와 정책 Threshold를 읽고 Hash·Schema·Class Order를 검증한다.
2. `score`가 Label 없는 미래 Feature Batch를 검증하고 모든 Registry 모델의 Risk Score와 고정 Threshold 판정을 생성한다.
3. 예측 결과는 Batch ID와 원본 Hash를 가진 새 파일로만 저장한다.
4. `ingest-labels`는 별도 Label Batch에서 `hr_coil_id`, `judge`, `label_finalized_at`을 검증한다.
5. `evaluate`는 `hr_coil_id`로 예측과 확정 Label을 1:1 결합하고 예측시각이 Label 확정시각보다 빠른 행만 평가한다.
6. `status`는 표본 충족 여부, 성능, Drift, Gate와 다음 행동을 보고한다.

## 5. 데이터 계약

### 5.1 Shadow Feature Batch

필수 식별·시간 열:

- `charge_id`
- `slab_no`
- `hr_coil_id`
- `hr_date`
- `feature_available_at`

필수 모델 입력 열은 동결 Registry Feature Schema의 합집합이다. Numeric/Categorical 타입은 Artifact 계약을 따르며 입력 파일의 전체 dtype 추론으로 재결정하지 않는다.

금지 열:

- `judge`
- `ap_date`
- `label_finalized_at`
- `dataset_split`
- 그 밖의 `ap_*` 운전조건

무결성 조건:

- Batch 내부와 기존 Ledger 전체에서 `hr_coil_id`가 고유해야 한다.
- 필수 Feature가 누락되면 전체 Batch를 거부한다.
- Numeric 변환 실패 수와 미지 범주 수는 기록하되, 모델 Pipeline이 계약상 처리 가능한 결측·미지 범주만 허용한다.
- `feature_available_at`은 timezone을 포함한 ISO-8601이어야 한다.
- `prediction_generated_at`은 입력값을 신뢰하지 않고 `score` 실행 시 시스템 UTC 시각으로 생성한다.

### 5.2 Label Batch

필수 열:

- `hr_coil_id`
- `judge`: `양품` 또는 `불량`
- `label_finalized_at`: timezone을 포함한 ISO-8601

각 Coil에는 하나의 최종 Label만 허용한다. 수정 Label은 기존 파일 덮어쓰기가 아니라 `supersedes_label_batch_id`를 포함하는 별도 정정 Batch로 저장하며 평가 보고서에 정정 이력을 남긴다.

### 5.3 Batch ID와 저장

Batch ID는 `UTC timestamp + source SHA-256 앞 12자리`로 생성한다. 예측 CSV와 Metadata JSON을 쌍으로 저장하며 JSON에는 입력 Hash, 모델 Hash, 행 수, 생성 시각, Schema Version을 기록한다. 동일 Batch ID 또는 동일 Source Hash의 재등록은 멱등 성공으로 처리하되 기존 파일 내용이 다르면 거부한다.

## 6. Shadow 평가와 Gate

### 6.1 평가 지표

각 모델에 대해 다음을 계산한다. Registry는 기존 Baseline을 참고 모델, 현재 Challenger를 `shadow_incumbent`, 차세대 후보를 `next_challenger`로 구분한다.

- PR-AUC, ROC-AUC
- 고정 정책 Threshold의 Recall, Precision, F1, F2, FPR, Alert Rate
- TN, FP, FN, TP
- 불량률과 월·주차별 성능
- Numeric PSI/KS, Categorical JSD/신규 범주율
- 보정기가 Registry에 등록된 경우 Brier Score, Log Loss, ECE
- `shadow_incumbent` 대비 PR-AUC·Recall·FPR 차이의 `charge_id` 단위 Paired Bootstrap 95% 신뢰구간

Bootstrap은 고정 Seed 42와 2,000회 재표집을 사용한다. 양쪽 Class가 없는 재표집은 해당 지표 계산에서 제외하고 유효 반복 수를 기록한다.

### 6.2 최소 관찰량

다음 조건을 모두 만족하기 전에는 상태를 `COLLECTING`으로 유지한다.

- 첫 예측부터 마지막 확정 Label까지 28일 이상
- 확정 Label이 결합된 Coil 5,000개 이상
- 확정 불량 150개 이상
- 예측 전 Label 확정, 중복 Coil, Schema/Hash 불일치가 각각 0건

### 6.3 성능 Gate

최소 관찰량 충족 후에도 자동 배포하지 않는다. Next Challenger가 다음 조건을 모두 만족할 때만 `HUMAN_REVIEW_CANDIDATE`를 출력한다.

- 실제 FPR `<= 0.15`
- `shadow_incumbent` 대비 Recall 절대 개선 `>= 0.05`
- Recall 차이의 Paired Bootstrap 95% 신뢰구간 하한 `> 0`
- `shadow_incumbent` 대비 PR-AUC 절대 개선 `>= 0.005`
- PR-AUC 차이의 Paired Bootstrap 95% 신뢰구간 하한 `>= 0`
- Registry 보정 확률을 제공하는 경우 ECE `<= 0.05`
- 중대한 Schema 위반이 0건

Drift 경보가 기준을 넘으면 성능 Gate와 별개로 `PROCESS_REVIEW_REQUIRED`를 함께 출력한다. 어떤 경우에도 평가기가 Artifact의 `deployment_eligible`을 자동 변경하지 않는다.

## 7. 차세대 모델 개발

### 7.1 개발 Cohort

- 원본은 `training/steel/output/modeling_cohort.csv`다.
- `TRAIN` 전체와 `ap_date < 2025-09-01`인 `VALIDATION`만 개발에 사용한다.
- `HOLDOUT` 행은 Feature Schema 추론, 후보 학습, Hyperparameter 선택, Threshold 선택, Calibration에 사용하지 않는다.
- 최종 후보가 선택된 뒤에도 기존 HOLDOUT은 차세대 모델의 합격 근거로 재평가하지 않는다. 실제 합격 시험은 미래 Shadow다.

### 7.2 Feature

기존 30개 상류 Feature를 기본으로 사용하고 다음의 제한된 공정 파생 Feature를 별도 후보군으로 비교한다.

- `gas_total = f_bfg + f_cog + f_ldg`
- `heating_interval_total = f_pre_interval + f_heat_interval + f_sock_interval`
- `pre_to_heat_temp_delta = f_heat_temp - f_pre_temp`
- `heat_to_sock_temp_delta = f_sock_temp - f_heat_temp`
- `width_reduction = slab_width - hr_width`
- `width_ratio = hr_width / slab_width` (`slab_width=0`은 결측 처리)

파생 Feature는 단위·부호·분포를 보고하며, AP 변수와 ID·원시 날짜·정답·DQ 감사 Flag는 계속 제외한다.

### 7.3 후보 모델

현재 scikit-learn 의존성만 사용한다.

- Class-weighted Logistic Regression: L2 및 Elastic-Net의 제한된 Grid
- Extra Trees: 깊이·Leaf 최소 표본을 제한한 Grid
- Random Forest: 기존 후보와 보수적 변형
- HistGradientBoosting: Learning Rate·Leaf 제한 Grid
- 상위 두 모델의 단순 Rank-Average Ensemble

Grid 크기는 총 20개 Pipeline 이하로 제한한다. Positive Class가 적으므로 무제한 Tree, 과도한 Search, SMOTE는 사용하지 않는다.

### 7.4 선택 절차

1. 개발 Cohort에서 `charge_id`를 절대 분리하지 않는 5-fold Stratified Group OOF 점수를 생성한다.
2. 별도로 날짜순 전반부→후반부 두 개의 expanding-window 검증을 생성하며 경계를 넘는 Charge는 해당 Fold에서 제외한다.
3. 1차 순위는 Group OOF PR-AUC, 2차는 OOF ROC-AUC, 3차는 시간 Fold 최저 PR-AUC로 정한다.
4. OOF 점수에서 FPR 10%, 15%, 20%별 최대 Recall Threshold를 고정한다.
5. 기존 Challenger 대비 PR-AUC 개선이 `0.005` 미만이면 기존 Challenger를 유지한다.
6. 선택 후 개발 Cohort 전체로만 재학습하고 Platt Calibration은 `charge_id` Group OOF 점수로 적합한다.
7. 최종 Artifact는 `deployment_eligible=false`, `required_next_step=FUTURE_SHADOW_VALIDATION`으로 저장한다.

시간 Fold 중 한 Class가 없거나 Group 중첩이 발생하면 해당 후보를 채점하지 않고 실행을 실패시킨다.

## 8. Dry Run

미래 데이터가 없으므로 기존 HOLDOUT 복사본으로 인터페이스와 파일 흐름만 검증할 수 있다. Dry Run은 다음 규칙을 따른다.

- 출력 경로를 `shadow_dry_run_output/`으로 고정한다.
- 모든 보고서에 `evidence_status=NON_INDEPENDENT_DRY_RUN`을 기록한다.
- Deployment Gate 계산을 금지하고 항상 `AWAITING_FUTURE_DATA`를 출력한다.
- 성능 수치를 미래 일반화 성능으로 인용하지 않는다.

## 9. 오류 처리와 안전장치

- Hash·Schema·Class Order·Feature Order가 Registry와 다르면 추론 전에 실패한다.
- 금지 Label/AP 열이 Feature Batch에 있으면 실패한다.
- Ledger의 기존 파일은 덮어쓰지 않는다.
- 부분 저장을 막기 위해 임시 파일에 기록한 뒤 같은 파일시스템에서 원자적으로 교체한다.
- JSON은 `allow_nan=False`, CSV는 UTF-8-SIG로 저장한다.
- 시간은 timezone-aware UTC로 정규화하고 예측시각 이후 확정된 Label만 평가한다.
- CLI 실패는 비정상 종료코드와 사람이 이해할 수 있는 오류를 반환한다.

## 10. 테스트 전략

TDD로 다음 실패 테스트를 먼저 작성한다.

- Feature Batch에 `judge` 또는 `ap_*`가 있으면 거부
- Registry Hash나 Feature Order가 다르면 거부
- 미래 Feature/Label을 바꿔도 이미 저장된 예측 파일이 변하지 않음
- 중복 Coil과 예측보다 이른 Label 확정을 거부
- Label이 없을 때 `COLLECTING`, 최소량 미달 때 `COLLECTING`
- 최소량과 성능 기준을 모두 만족한 합성 데이터만 `HUMAN_REVIEW_CANDIDATE`
- HOLDOUT 전체 값·dtype·Label을 바꿔도 차세대 후보와 OOF Threshold가 동일
- 모든 OOF Fold에서 `charge_id` 교집합이 0
- 파생 Feature 수식과 0 나눗셈 처리
- Dry Run은 항상 `NON_INDEPENDENT_DRY_RUN` 및 `AWAITING_FUTURE_DATA`
- 출력 JSON 유한값, CSV BOM, Artifact/Source Hash 일치

전체 기존 `training/steel/tests`도 함께 실행해 회귀를 확인한다.

## 11. 산출물과 완료 조건

구현 단계의 완료 조건은 다음과 같다.

1. Shadow Registry·Score·Label·Evaluate·Status CLI가 테스트 데이터로 재현된다.
2. 실제 미래 데이터 부재를 `AWAITING_FUTURE_DATA`로 정확히 보고한다.
3. Dry Run이 실제 Shadow 출력과 분리되고 배포 근거로 사용되지 않는다.
4. Next Challenger 연구가 HOLDOUT 불변성 테스트를 통과한다.
5. 모델·Threshold·Calibration·Schema·Source Hash가 Artifact와 보고서에서 일치한다.
6. 기존 테스트와 새 테스트가 모두 통과한다.
7. README에 미래 Batch 입력법, Label 확정법, 평가법, Gate 해석을 고2 비유와 전문 용어 매핑으로 기록한다.

실제 Shadow 검증의 완료 조건은 구현 완료와 별개다. 28일·5,000 Coil·불량 150건의 새로운 데이터가 쌓이고 Gate 평가가 끝나야 실제 Shadow 결과를 보고할 수 있다.
