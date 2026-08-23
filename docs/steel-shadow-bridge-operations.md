# SFEP Steel Shadow Bridge 운영 가이드

## 1. 먼저 쉬운 설명

Python Shadow sidecar는 SFEP와 모델 사이의 **검수 창구**다. SFEP가 코일 공정표를 보내면 창구가 양식을 검사하고, 등록된 모든 모델의 예측을 봉인된 기록장에 남긴다. AP 품질 정답은 나중에 별도로 받아 앞서 봉인한 예측과 맞춘다.

이 창구가 고장 나거나 기록장이 변조되면 “대충 계속 진행”하지 않는다. 각각 `503 SHADOW_UNAVAILABLE` 또는 `409 LEDGER_INTEGRITY_REVIEW_REQUIRED`로 멈춘다. 예측 결과는 생산라인 제어 명령이 아니며, 어떤 응답도 자동 배포를 허용하지 않는다.

## 2. 전문 용어 매핑

| 쉬운 비유 | 전문 용어 |
|---|---|
| 검수 창구 | localhost Python HTTP sidecar |
| 공정표 | label-free Feature Batch |
| 봉인 번호 | source SHA-256 + UTC 기반 Batch ID |
| 봉인된 기록장 | append-only Feature·Prediction·Label ledger |
| 나중에 온 정답표 | delayed finalized label |
| 기록장 재검사 | full-ledger integrity and lineage validation |
| 사람의 최종 승인 | `HUMAN_REVIEW_CANDIDATE` 이후 별도 검토 |

## 3. 현재 연결 상태

- Python sidecar와 데이터 계약, 안전한 예측 조회, 실제 HTTP Smoke test가 준비되어 있다.
- Spring Boot 어댑터는 `/api/steel-shadow/**`에 구현되어 있으며 기본값은 비활성화다.
- 실제 `training/steel/shadow_output`에는 합성 Smoke 데이터를 넣지 않는다.
- Spring 기능은 구현 후에도 기본값을 `enabled: false`로 유지한다.
- sidecar는 `127.0.0.1`에만 바인딩한다. 외부 노출 전에는 인증과 TLS를 별도로 설계해야 한다.

## 4. Sidecar 시작

프로젝트 루트에서 실행한다.

```bash
LOKY_MAX_CPU_COUNT=1 python3 -m training.steel.shadow_api \
  --host 127.0.0.1 \
  --port 18081 \
  --registry training/steel/shadow_registry/registry.json \
  --output-dir training/steel/shadow_output \
  --max-request-bytes 10485760 \
  --max-coils 10000 \
  --request-timeout-seconds 10
```

시작 과정에서 Registry, 모델·보정기 파일, SHA-256, Feature Schema, Class 순서를 검증한다. 하나라도 맞지 않으면 서버가 정상 상태가 되지 않는다.

주의: 위 실제 출력 경로에는 Registry의 `shadow_start_at` 이후에 새로 생산되고 제공된 진짜 미래 데이터만 보낸다. 개발·시연 데이터에는 아래 Smoke script를 사용한다.

## 5. Health와 상태 확인

```bash
curl --fail --silent http://127.0.0.1:18081/health
curl --fail --silent http://127.0.0.1:18081/v1/status
```

정상 Health는 `status=UP`, 현재 Registry SHA-256, 등록 모델 목록, `deploymentEligible=false`를 반환한다. 초기 Shadow 상태는 보통 `AWAITING_FUTURE_DATA`다. 첫 Feature가 기록되면 Label을 기다리는 `COLLECTING`이 된다.

## 6. Feature 전송

요청 계약은 `docs/superpowers/specs/2026-08-21-sfep-steel-shadow-bridge-design.md`의 5.1절 예시를 사용한다.

```bash
curl --fail --silent \
  -H 'Content-Type: application/json' \
  --data-binary @future-feature-request.json \
  http://127.0.0.1:18081/v1/features
```

필수 규칙:

- 요청당 1~10,000개의 서로 다른 `hrCoilId`
- HTTP 본문은 최대 10 MiB, JSON 객체·배열 중첩은 최대 64단계
- `chargeId`, `slabNo`, `hrCoilId`, `hrDate`, timezone이 있는 `featureAvailableAt`
- Registry가 요구하는 원 공정 Feature 전체
- `judge`, `ap_date`, `dataset_split`, 모든 `ap_*` 입력 금지
- 6개 파생 Feature 입력 금지: `gas_total`, `heating_interval_total`, `pre_to_heat_temp_delta`, `heat_to_sock_temp_delta`, `width_reduction`, `width_ratio`
- 클라이언트가 `evidenceStatus` 또는 `predictionGeneratedAt`을 지정할 수 없음

동일한 JSON 의미의 요청은 정렬된 canonical CSV로 바뀌며 같은 원본 해시를 만든다. 재전송은 기존 Batch를 돌려주고 새 원장을 만들지 않는다. 같은 Coil을 다른 값으로 다시 보내면 `409 COIL_ALREADY_SCORED_DIFFERENTLY`다.

## 7. 확정 Label 전송

Label은 예측 후 AP에서 품질 판정이 실제로 확정된 뒤 전송한다.

```bash
curl --fail --silent \
  -H 'Content-Type: application/json' \
  --data-binary @final-label-request.json \
  http://127.0.0.1:18081/v1/labels
```

`judge`는 정확히 `양품` 또는 `불량`이어야 한다. `labelFinalizedAt`은 해당 Coil의 예측시각보다 뒤이고 Label 수집시각보다 미래가 아니어야 한다. 정정 Batch는 `supersedesLabelBatchId`로 기존 Batch 전체 Coil을 교체해야 한다.

## 8. Coil 예측 조회

```bash
curl --fail --silent \
  http://127.0.0.1:18081/v1/predictions/H20260822001
```

조회 전 전체 예측 원장의 파일명, metadata, source snapshot, SHA-256, Registry 계보, 시간 순서를 다시 검증한다. 내부 파일 경로는 응답하지 않는다.

## 9. 오류와 대응

| HTTP | Code | 운영 대응 |
|---:|---|---|
| 400 | `REQUEST_SCHEMA_INVALID` | JSON 버전, 필드명, Content-Type, 요청 크기를 수정한다. |
| 400 | `FEATURE_CONTRACT_INVALID` | 누락·금지 Feature, 식별자, 시간을 수정한다. |
| 400 | `LABEL_CONTRACT_INVALID` | Label 값과 확정시각을 확인한다. |
| 405 | `REQUEST_SCHEMA_INVALID` | 지원하는 `GET`·`POST` 경로와 HTTP 방식을 확인한다. |
| 408 | `REQUEST_SCHEMA_INVALID` | 본문 전송이 10초 안에 끝나지 않았다. 연결 상태와 요청 크기를 확인한다. |
| 413 | `REQUEST_SCHEMA_INVALID` | 본문이 10 MiB 제한을 넘었다. Batch를 나눈다. |
| 404 | `COIL_NOT_FOUND` | Coil ID 또는 Feature 선행 전송 여부를 확인한다. |
| 409 | `COIL_ALREADY_SCORED_DIFFERENTLY` | 재전송 원본을 비교하고 새 값으로 덮어쓰지 않는다. |
| 409 | `LEDGER_INTEGRITY_REVIEW_REQUIRED` | 쓰기를 중단하고 백업본·해시·부분 파일을 사람이 조사한다. |
| 503 | `SHADOW_UNAVAILABLE` | Registry·모델·보정기·용량표 파일과 sidecar 상태를 복구한 뒤 재시도한다. |

오류 응답에는 서버 파일 경로나 stack trace가 포함되지 않는다. 장애가 발생해도 생산 제어 명령이나 대체 모델 자동 배포는 실행하지 않는다.

## 10. 개발용 직접 Smoke test

```bash
LOKY_MAX_CPU_COUNT=1 python3 scripts/smoke-steel-shadow-bridge.py
```

이 명령은 현재 운영 Registry에 봉인된 Baseline·Challenger·NextGen 세 artifact와 전체 30개 원 Feature 계약을 사용하되, Registry 시각·출력 원장·localhost 포트는 임시 공간에 만든다. Feature·Label을 각각 두 번 보내 3개 모델 예측, 첫 Label의 `COLLECTING` 상태, Batch 멱등성을 검사하고 종료 시 임시 증거를 삭제한다. 성공하면 stdout에 `{"ok": true, ...}` 형태의 strict JSON 한 줄을 출력한다.

Spring 어댑터를 활성화한 뒤에는 다음 경로도 같은 임시 Fixture로 검사한다.

```bash
LOKY_MAX_CPU_COUNT=1 python3 scripts/smoke-steel-shadow-bridge.py \
  --sfep-base-url http://127.0.0.1:18080/api/steel-shadow
```

이 경우 Smoke sidecar는 Spring 기본 설정과 맞추기 위해 18081 포트를 사용한다. Spring이 다른 포트로 설정됐다면 `--sidecar-port <포트>`도 함께 지정한다. 같은 임시 Feature·Label을 sidecar 직통 경로와 SFEP 경로로 재전송해 원장이 각각 1개 Batch로 유지되는지 확인한다.

Spring은 sidecar를 먼저 시작한 뒤 다음처럼 활성화한다. 별도 터미널에서 실행하며 PostgreSQL 등 기존 SFEP 의존 서비스도 준비되어 있어야 한다.

```bash
cd sfep-server
SFEP_STEEL_SHADOW_ENABLED=true \
SFEP_STEEL_SHADOW_BASE_URL=http://127.0.0.1:18081 \
./gradlew bootRun
```

Java Adapter의 단위·웹·실제 sidecar end-to-end 회귀는 `cd sfep-server && ./gradlew test`로 실행한다. 테스트 원장은 운영체제 임시 폴더에만 생성된다.

## 11. 증거 등급 구분

| 구분 | 위치 | 모델 판단에 사용 |
|---|---|---|
| 합성 통신 Smoke | 운영체제 임시 폴더 | 사용 금지 |
| 과거 HOLDOUT Dry Run | `training/steel/shadow_dry_run_output` | 배관 검증만, 미래 성능 증거 아님 |
| 진짜 미래 Shadow | `training/steel/shadow_output` | Gate 관찰 증거 |

진짜 미래 Shadow도 최소 28일·5,000 Coil·불량 150건과 성능·Drift·Calibration·무결성 Gate를 모두 통과해야 한다. `HUMAN_REVIEW_CANDIDATE`는 자동 배포 승인이 아니라 사람이 검토할 자격을 얻었다는 뜻이다.

## 12. 백업과 종료

Sidecar를 정상 종료한 뒤 `shadow_output` 전체와 그 시점의 Registry·모델 artifact를 함께 읽기 전용 백업한다. 원장 일부만 복사하거나 metadata 없이 CSV만 복원하지 않는다.

포그라운드 실행은 `Ctrl+C`로 종료한다. 서비스 관리자를 사용할 경우 먼저 새 요청 유입을 막고 프로세스가 종료된 뒤 원장을 백업한다. 실행 중인 원장을 수동 편집·이름 변경·부분 삭제하지 않는다.
