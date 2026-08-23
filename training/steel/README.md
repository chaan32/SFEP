# SFEP-Steel 데이터·Baseline 파이프라인

이 디렉터리는 제강·연주(SM/CC) → 열연(HR) → 소둔·산세(AP) 데이터를 `1 hr_coil_id = 1행`으로 연결하고, Charge·시간 누수를 막은 품질 Risk Baseline을 재현한다.

원본 CSV는 수정하지 않는다. 모델의 Positive Class는 `judge=불량`이며, 예측 시점은 임시로 열연 완료일(`hr_date`) 직후·AP 투입 전으로 정의한다. AP 데이터에서는 정답 연결용 `judge`, `ap_date`만 Curated Dataset에 남기고 AP 작업조건은 모델 입력에서 제외한다.

검증 환경은 Python 3.14와 `training/steel/requirements.txt`에 고정했다. 로컬에 별도 환경을 만들 때는 해당 파일로 의존성을 설치한다.

## 1. 데이터 큐레이션

```bash
python3 training/steel/curate_genealogy.py \
  --sm-csv '/Users/haechan/Downloads/32기 A14(철강)/sts_1sm_cc_1.csv' \
  --hr-csv '/Users/haechan/Downloads/32기 A14(철강)/sts_2fur_hr_2.csv' \
  --ap-csv '/Users/haechan/Downloads/32기 A14(철강)/sts_3ap_3.csv' \
  --output-dir training/steel/output
```

엄격 연결 조건은 다음과 같다.

- SM과 HR의 `(charge_id, slab_no)`가 양쪽에서 각각 고유해야 한다.
- HR과 AP의 `hr_coil_id`가 양쪽에서 각각 고유하고 모두 존재해야 한다.
- 근거 없는 `drop_duplicates()`나 임의 첫 행 선택은 하지 않는다.
- 2025-08-01 또는 2025-09-01 경계를 넘는 `charge_id`는 전체를 `BOUNDARY_EXCLUDED`로 분리한다.

주요 산출물:

| 파일 | 용도 |
|---|---|
| `output/curated_coil_dataset.csv` | 경계 제외 전, 안전하게 연결된 1 Coil=1행 데이터 |
| `output/modeling_cohort.csv` | TRAIN/VALIDATION/HOLDOUT만 포함한 모델 입력 Cohort |
| `output/process_key_audit.csv` | SM–HR 자연키의 1:1/1:N/N:1/N:N 감사 |
| `output/coil_audit.csv` | HR·AP Coil 합집합과 모든 중첩 사유 감사 |
| `output/quarantine_coils.csv` | Curated에서 제외한 고유 Coil |
| `output/hr_row_audit.csv` | 모든 HR 원본 행의 strict 판정 |
| `output/hr_matching_quarantine.csv` | 우선순위가 적용된 상호배타 HR 격리 22행 |
| `output/split_manifest.csv` | Coil별 최종 Split 근거 |
| `output/curation_summary.json` | 원본 해시, Cohort, Label, DQ, 무결성 요약 |

현재 재현 결과:

| Cohort | 전체 | 양품 | 불량 |
|---|---:|---:|---:|
| Curated, 경계 제외 전 | 23,630 | 23,001 | 629 |
| Boundary-excluded | 1,682 | 1,615 | 67 |
| 최종 Modeling | 21,948 | 21,386 | 562 |

TRAIN 7,564행, VALIDATION 8,740행, HOLDOUT 5,644행이며 세 Split의 `charge_id` 교집합은 0이다.

## 2. Baseline 학습

```bash
python3 training/steel/train_baseline.py \
  --modeling-cohort training/steel/output/modeling_cohort.csv \
  --output-dir training/steel/baseline_output \
  --random-state 42 \
  --random-forest-estimators 300 \
  --n-jobs 1 \
  --validation-freeze-date 2025-09-01
```

평가 절차는 고정되어 있다.

1. TRAIN에서 Logistic Regression과 Random Forest를 각각 학습한다.
2. Holdout 시작일(2025-09-01) 전에 `ap_date`가 도착한 VALIDATION 7,399행만 선택 Cohort로 사용한다.
3. 이 As-of VALIDATION의 PR-AUC만으로 모델을 고른다.
4. 선택 모델의 F2 최대 임계값도 같은 As-of VALIDATION에서만 정한다.
5. 모델과 임계값을 고정한 뒤 HOLDOUT을 한 번 평가한다.

모델 Feature에서 다음을 강제로 제외한다.

- 식별자: `charge_id`, `slab_no`, `hr_coil_id`
- 원시 날짜: `cast_date`, `f_ext_date`, `hr_date`
- 정답·Split: `judge`, `dataset_split`
- 모든 AP 변수: `ap_*`
- 감사 전용 품질 Flag: `dq_*`
- 원량과 중복되는 가스 비율: `f_bfg_per`, `f_cog_per`, `f_ldg_per`

주요 산출물:

| 파일 | 용도 |
|---|---|
| `baseline_output/baseline_metrics.json` | 선택 절차, Validation 비교, Holdout 지표와 무결성 |
| `baseline_output/baseline_model.joblib` | 선택 모델 Pipeline과 고정 임계값 |
| `baseline_output/feature_schema.json` | 포함·제외 Feature 계약 |
| `baseline_output/validation_model_comparison.csv` | 두 Baseline의 Validation 비교 |
| `baseline_output/validation_predictions.csv` | 임계값 선택 감사용 Validation 점수 |
| `baseline_output/holdout_predictions.csv` | 최종 Holdout Risk Score와 판정 |
| `baseline_output/top_feature_associations.csv` | 선택 모델의 상위 예측 연관 Feature |

현재 선택 모델은 Logistic Regression이다. Holdout PR-AUC는 0.1172로 Holdout 불량률 기준선 0.0395보다 높지만, Recall 0.2197, Precision 0.0884, Brier Score 0.1843이다. 따라서 이것은 파이프라인 기준선이지 배포 가능한 모델이 아니다. 출력값은 보정된 불량 확률이 아니라 `risk_score`로 취급한다. `ap_date`는 Label 확정시각의 임시 Proxy이므로 운영 전에는 `label_finalized_at`이 필요하다.

Feature 계수·중요도는 품질 Risk와의 예측 연관성일 뿐, 공정 원인·설비 고장·인과관계의 증거가 아니다.

## 3. Holdout 진단·Drift·Calibration·SHAP

```bash
python3 -m training.steel.run_diagnostics \
  --modeling-cohort training/steel/output/modeling_cohort.csv \
  --baseline-dir training/steel/baseline_output \
  --output-dir training/steel/diagnostics_output
```

이 실행은 학습된 Logistic Pipeline과 Threshold를 고정한 채 다음을 수행한다.

1. Holdout의 TN/FP/FN/TP와 범주·TRAIN 분위수별 오류 Slice를 계산한다.
2. TRAIN을 기준으로 As-of VALIDATION과 HOLDOUT의 Label·수치·범주 Drift를 계산한다.
3. `charge_id` Group 5-fold Validation OOF에서 Platt/Isotonic을 비교하고, 선택된 보정기만 전체 As-of VALIDATION에 적합한다. 분류기는 `FrozenEstimator`로 고정한다.
4. Logistic `decision_function`을 TRAIN 평균 변환값 배경의 정확한 Linear SHAP(log-odds)으로 분해하고, 원 Feature와 공정·설비 확인 후보 Registry로 연결한다.

주요 산출물:

| 파일 | 용도 |
|---|---|
| `diagnostics_output/diagnostic_report.md` | 핵심 오류·Drift·Calibration·SHAP 해석 보고서 |
| `diagnostics_output/holdout_error_cohorts.csv` | Coil별 TN/FP/FN/TP, Score Margin과 원 공정 조건 |
| `diagnostics_output/*_error_*.csv` | 범주 및 TRAIN 분위수 오류 Slice, Wilson 구간·Fisher·BH 검정 |
| `diagnostics_output/*_drift*.csv` | Label, 수치, 범주, 신규 범주의 기간 Drift |
| `diagnostics_output/calibration_*.csv` | Validation OOF 선택과 Holdout 보정 지표·Reliability Bin |
| `diagnostics_output/calibrator.joblib` | 고정 모델 위에 적합한 Platt 보정기와 Lineage |
| `diagnostics_output/shap_*.csv` | Global·Coil별 SHAP과 공정·설비 확인 후보 |
| `diagnostics_output/feature_equipment_registry.csv` | 30개 Feature의 공정·후보·검토 경로·자동 Ticket 금지 계약 |
| `diagnostics_output/*.png` | Confusion Matrix, Drift, Calibration, SHAP 후보 차트 |
| `diagnostics_output/diagnostic_summary.json` | 버전·해시·Cohort·검증 요약 |

현재 재현 결과의 핵심은 다음과 같다.

- Holdout: TN 4,916, FP 505, FN 174, TP 49; 오탐률 9.32%, 미탐률 78.03%.
- 오탐 집중: `CCR ∩ f_jangip_temp<=30 ∩ f_ldg=0`에서 FP 361건, 실제 양품 기준 오탐률 35.19%. 이 세 조건은 중첩되므로 독립 원인으로 해석하지 않는다.
- 미탐 감사 신호: `steel_usage=RJ1`은 실제 불량 97건 중 FN 94건, HCR 실제 불량 23건은 모두 FN이다. 둘 다 작은 TP/표본 때문에 운영 규칙이 아닌 재확인 후보로만 사용한다.
- 불량률 Drift: TRAIN 1.1502% → As-of VALIDATION 2.8653% → HOLDOUT 3.9511%.
- 주요 Drift: `hr_width`, `f_pre_temp`, `f_sock_temp`, `slab_width`; HOLDOUT의 `f_ldg`는 0 초과 비율이 TRAIN 3.33%에서 36.18%로 증가했다.
- Validation OOF에서 Isotonic의 Brier 우위는 `4.46e-6`뿐이고 Log Loss가 더 나빠, 사전 규칙(Brier `1e-4` 동률 범위 후 Log Loss·안정성)으로 Platt를 선택했다.
- Holdout Platt: Brier 0.0380, Log Loss 0.1683, 등빈도 ECE 0.0222. 기존 Raw는 각각 0.1843, 0.7087, 0.2727이다. 다만 Validation 불량률 상수 Brier 0.03807 대비 개선은 약 0.11%뿐이다. 기존 Raw Threshold 0.79733은 표시용 보정 Threshold 0.03123에 대응하며 판정은 동일하다.
- SHAP 상위 확인 후보는 `f_ldg`, `slab_width`, `hr_width`, `ingre_ni`, `steel_grade`, `f_jangip_temp`다. `f_ldg`는 Drift 외삽, 두 폭 변수는 TRAIN 상관계수 0.99936의 영향을 받으므로 개별 원인 순위로 보지 않는다.

Calibration은 Score를 확률처럼 읽기 위한 보정일 뿐 Recall·Precision·순위 성능을 개선하지 않는다. SHAP과 Registry는 데이터·공정·계측 확인 순서를 위한 예측 연관 후보이며, 설비 고장 판정이나 자동 정비 Ticket의 근거가 아니다.

## 4. Challenger 모델 실험

```bash
python3 -m training.steel.train_challenger \
  --modeling-cohort training/steel/output/modeling_cohort.csv \
  --baseline-dir training/steel/baseline_output \
  --output-dir training/steel/challenger_output \
  --random-state 42 \
  --random-forest-estimators 300 \
  --n-jobs 1 \
  --alert-fpr-caps 0.10,0.15,0.20 \
  --policy-fpr-cap 0.15 \
  --validation-freeze-date 2025-09-01
```

기존 모델과 Logistic 규제 후보 5개, Random Forest 후보 2개, HistGradientBoosting 후보 1개를 비교한다. 학습은 TRAIN만 사용하고 모델 순위와 임계값은 As-of VALIDATION만 사용한다. 각 모델은 양품 오경보율(FPR)을 10%, 15%, 20% 이하로 제한하면서 Recall이 가장 높은 임계값을 선택한다. 추천 모델을 잠근 다음에만 과거 HOLDOUT을 진단 목적으로 계산한다.

Feature 이름·Numeric/Categorical 타입도 TRAIN 값만으로 결정한다. 따라서 HOLDOUT 값이 CSV dtype 추론을 오염시키더라도 모델 구조나 Validation 임계값을 바꿀 수 없다. 계약상 Numeric Feature는 Split별로 명시적 숫자 변환 후 전처리한다.

`validation-freeze-date`는 실험 프로토콜상 `2025-09-01`로 봉인되어 있으며, 다른 값을 입력하면 실행을 거부한다. 기준일을 바꾸려면 기존 결과를 덮어쓰지 말고 새 실험 버전과 별도 출력 경로로 설계·검토해야 한다.

주요 산출물:

| 파일 | 용도 |
|---|---|
| `challenger_output/challenger_metrics.json` | 후보, 선택 절차, 추천, 배포 금지 Gate, 정책점 요약 |
| `challenger_output/validation_model_leaderboard.csv` | Validation PR-AUC 기준 모델 순위 |
| `challenger_output/validation_alert_capacity.csv` | FPR 10%·15%·20% 상한별 임계값·Recall·Precision |
| `challenger_output/historical_holdout_comparison.csv` | 선택 완료 후 동일 임계값의 과거 Holdout 비교 |
| `challenger_output/*_predictions.csv` | 추천 모델의 Validation·과거 Holdout Coil별 판정 |
| `challenger_output/recommended_model.joblib` | 임시 추천 Pipeline과 Validation 임계값 계약 |
| `challenger_output/challenger_report.md` | 쉬운 비유와 전문 용어를 연결한 결과 보고서 |

현재 As-of VALIDATION에서는 `logistic_l2_c0_1`이 PR-AUC 0.0546으로 기존 Logistic 0.0509보다 높아 임시 Challenger로 추천됐다. FPR 15% 정책점의 Validation Recall은 30.19%(TP 64, FN 148), 실제 FPR은 14.37%다. 같은 Validation 임계값을 과거 Holdout에 적용하면 Recall 17.94%(TP 40, FN 183), FPR 6.03%(FP 327)다. 동일 FPR 15% 정책의 기존 모델은 Holdout Recall 16.59%(TP 37, FN 186), FPR 5.76%(FP 312)다.

개선 폭은 작고 이 실험 설계 전에 기존 Holdout 결과를 이미 확인했으므로, 산출물에는 `deployment_eligible=false`와 `FUTURE_SHADOW_VALIDATION_REQUIRED`를 강제로 기록한다. 완전히 새로운 미래 Cohort로 Shadow 검증하기 전에는 배포 모델로 취급하지 않는다.

## 5. NextGen 후보 탐색

```bash
LOKY_MAX_CPU_COUNT=1 python3 -m training.steel.train_nextgen \
  --modeling-cohort training/steel/output/modeling_cohort.csv \
  --output-dir training/steel/nextgen_output \
  --random-state 42 \
  --n-jobs 1 \
  --tree-estimators 300 \
  --n-splits 5 \
  --min-pr-auc-improvement 0.005
```

쉽게 말하면 기존 선수와 새 선수 16명, 상위 2명의 순위 평균 팀을 과거 정답을 미리 보지 않는 모의고사로 비교한다. 전문적으로는 `TRAIN + ap_date < 2025-09-01`인 VALIDATION만 개발 Cohort로 사용하고, `charge_id` 기준 5-fold OOF와 두 개의 expanding-window 시간 검증을 수행한다. HOLDOUT은 Feature Schema, 후보 순위, 임계값, 보정기에 영향을 줄 수 없다.

6개 파생 Feature는 가스 총량, 가열시간 총합, 예열→가열·가열→균열 온도차, 폭 감소량, 폭 비율이다. 동일 공식은 Shadow 입력 검증기가 직접 계산하므로 현장 입력 파일이 파생값을 임의로 공급할 수 없다.

현재 14,963개 개발 Coil의 재현 결과에서 기존 Logistic OOF PR-AUC는 0.06264, 추천된 `rank_average_top2`는 0.12241이다. 추천 앙상블의 자식은 `random_forest_depth10_leaf20`과 `hist_gb_leaves15_leaf20`이다. 다만 최저 시간 Fold PR-AUC는 0.02182이므로 `deployment_eligible=false`이며 미래 Shadow 검증 전에는 교체 모델이 아니다. Elastic Net 후보 일부는 `max_iter` 수렴 경고가 있었고 최종 앙상블에는 포함되지 않았다.

## 6. 미래 Shadow 검증

모델 등록부는 세 모델의 파일 해시, Feature 순서, Class 순서, FPR 15% 정책 임계값, Cohort·보정기 계보를 고정한다.

```bash
python3 -m training.steel.run_shadow register \
  --baseline-model training/steel/baseline_output/baseline_model.joblib \
  --challenger-model training/steel/challenger_output/recommended_model.joblib \
  --challenger-capacity training/steel/challenger_output/validation_alert_capacity.csv \
  --nextgen-model training/steel/nextgen_output/nextgen_model.joblib \
  --nextgen-calibrator training/steel/nextgen_output/nextgen_calibrator.joblib \
  --reference-cohort training/steel/output/modeling_cohort.csv \
  --registry training/steel/shadow_registry/registry.json
```

미래 Feature Batch에는 `charge_id`, `slab_no`, `hr_coil_id`, `hr_date`, timezone이 있는 `feature_available_at`, 그리고 등록 모델의 원 공정 Feature가 필요하다. `judge`, `ap_date`, `label_finalized_at`, `dataset_split`, 모든 `ap_*`와 직접 계산한 파생 Feature는 입력할 수 없다.

```bash
python3 -m training.steel.run_shadow score \
  --feature-batch /path/to/future_features.csv \
  --registry training/steel/shadow_registry/registry.json \
  --output-dir training/steel/shadow_output

python3 -m training.steel.run_shadow ingest-labels \
  --label-batch /path/to/finalized_labels.csv \
  --output-dir training/steel/shadow_output

python3 -m training.steel.run_shadow evaluate \
  --registry training/steel/shadow_registry/registry.json \
  --output-dir training/steel/shadow_output

python3 -m training.steel.run_shadow status \
  --output-dir training/steel/shadow_output
```

Shadow는 시험 답이 공개되기 전에 세 모델의 예측을 append-only로 기록하고, 나중에 확정된 Label만 결합한다. 등록부의 `shadow_start_at` 이후에 생산·제공된 Feature만 독립 미래 증거로 인정하며, 입력 원본의 불변 원장 복사본·예측·Label·등록부의 SHA-256과 시간 순서를 평가 때 다시 검증한다. 증거 등급은 `INDEPENDENT_FUTURE_SHADOW`와 내부 Dry Run 전용 `NON_INDEPENDENT_DRY_RUN` 두 값만 허용하며 일반 `score` 명령에서는 변경할 수 없다. 출력 루트의 `.shadow-mode.json`도 등급에 고정하고, 원본 해시와 생성 시각으로 Batch ID를 재계산하므로 폴더 별칭이나 서로 맞지 않는 원본·Manifest를 거부한다. Drift는 Label 공개 여부와 무관하게 검증된 전체 예측 Batch에서 계산한다. 최소 28일·5,000 Coil·불량 150건 이후에도 NextGen의 FPR≤15%, Recall 개선 5%p 이상과 신뢰구간 하한>0, PR-AUC 개선 0.005 이상과 신뢰구간 하한≥0을 만족해야 사람의 검토 후보가 된다. 어떤 명령도 자동으로 배포 자격을 부여하지 않는다.

과거 HOLDOUT 리허설은 `training/steel/shadow_dry_run_output`에 물리적으로 분리했고 `charge_id` 단위 Bootstrap을 2,000회 수행했다. NextGen은 기존 Challenger보다 Recall이 17.94%→43.50%로 높았지만 FPR이 6.03%→15.97%로 증가했고 PR-AUC는 0.12920→0.11685로 낮아졌다. PR-AUC 차이의 95% 구간은 -0.05143~0.02741이고 FPR 차이의 95% 구간은 +0.08518~+0.11385다. 이 결과는 구조 점검용 `NON_INDEPENDENT_DRY_RUN`이며 상태는 영구적으로 `AWAITING_FUTURE_DATA`다. 이 결과로 모델을 다시 튜닝하지 않는다.

실제 `training/steel/shadow_output`은 현재 예측 Batch 0개, 확정 Label 0개인 `AWAITING_FUTURE_DATA` 상태다. 따라서 현 시점 주전은 기존 Challenger이며 NextGen은 배포 불가 Shadow 후보로만 유지한다.

## 7. 테스트

```bash
python3 -m unittest discover -s training/steel/tests -v
```

테스트는 중복·미연결 격리, 1 Coil=1행, AP 변수 누수 차단, DQ Flag, Charge 경계 분리, Feature 계약, Validation 전용 모델·임계값 선택, FPR 상한 Threshold, Challenger 배포 금지 Gate, 우폐구간 Drift Bin, 구조적 0 Drift, Group OOF Calibration, Frozen classifier, SHAP additivity, Registry 안전장치, NextGen HOLDOUT 불변성, 앙상블 저장·무결성, Shadow 지연 Label과 Dry Run 격리를 확인한다.

## 8. SFEP 연결용 Python Shadow sidecar

쉽게 말하면 sidecar는 SFEP가 보낸 공정표를 검사하고 모델별 예측을 봉인된 원장에 적는 접수창구다. 전문적으로는 엄격한 JSON 계약을 canonical UTF-8-SIG CSV로 변환한 뒤 기존 append-only Shadow runner를 호출하는 localhost HTTP adapter다.

```bash
LOKY_MAX_CPU_COUNT=1 python3 -m training.steel.shadow_api \
  --host 127.0.0.1 \
  --port 18081 \
  --registry training/steel/shadow_registry/registry.json \
  --output-dir training/steel/shadow_output
```

제공 경로는 `POST /v1/features`, `POST /v1/labels`, `GET /v1/status`, `GET /v1/predictions/{hrCoilId}`, `GET /health`다. 요청자는 증거 등급이나 예측시각을 지정할 수 없고, 모든 응답은 `deploymentEligible=false`다. 합성 연결 점검은 실제 원장을 사용하지 않는 다음 명령으로 수행한다.

```bash
LOKY_MAX_CPU_COUNT=1 python3 scripts/smoke-steel-shadow-bridge.py
```

상세 계약, 장애 대응, Label 시간 규칙, Spring 연결 점검 방법은 `docs/steel-shadow-bridge-operations.md`를 따른다. Spring Boot 어댑터는 `/api/steel-shadow/**`에 구현되어 있고 기본값은 비활성화다.
