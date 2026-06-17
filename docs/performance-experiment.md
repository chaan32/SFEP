# Performance Experiment Plan

이 프로젝트는 처음부터 완성된 구조를 적용하지 않고, 병목을 측정한 뒤 단계별 개선 효과를 비교하는 방식으로 진행한다.

## Stage Plan

| 단계 | 구조 | 의도 | 측정 지표 |
| --- | --- | --- | --- |
| 0단계 | DB 직접 저장 | 가장 단순한 구조의 한계 측정 | TPS, 평균 응답 시간, DB Connection |
| 1단계 | Kafka 도입 | 요청 폭주 시 버퍼링/비동기 처리 효과 검증 | 요청 응답 시간, Consumer Lag |
| 2단계 | Consumer 병렬화 | 처리량 증가와 Consumer Group 효과 검증 | 처리량, Lag 감소 속도 |
| 3단계 | Batch Insert 적용 | DB write 병목 개선 | TPS, DB CPU, insert 횟수 |
| 4단계 | Redis 최신 상태 캐싱 | 실시간 조회 API 성능 개선 | Dashboard API p50/p95 |
| 5단계 | Partition Key 최적화 | 특정 설비 이벤트 쏠림 방지 | Partition별 lag, 처리량 분산 |
| 6단계 | DB Index/Partitioning | 이벤트 이력 조회 성능 개선 | p50/p95/p99, Query Plan |
| 7단계 | Backpressure/Retry/DLQ | 장애 상황 안정성 개선 | 유실 이벤트 수, 복구 시간 |

## Stage 0 Baseline

### AS-IS 구조

```text
Simulator -> Spring API -> PostgreSQL
```

### 구현 내용

- Spring Boot 실행 시 Swing 데스크톱 모니터 실행
- Swing 창에서 설비 수, 설비당 이벤트 수, 장애 비율 설정
- `/api/v1/simulator/direct/run` API로 이벤트 생성
- `/api/v1/events/direct/batch` API로 이벤트 직접 저장
- 이벤트 저장 시 설비 최신 상태도 같이 갱신
- `/api/v1/dashboard/summary` API로 저장 결과 확인

### 측정 방법

Swing 모니터에서 직접 실행:

```bash
cd ~/Desktop/SFEP
docker compose up -d

cd sfep-server
./gradlew bootRun
```

창에서 원하는 설비 수와 설비당 이벤트 수를 입력하고 `보내기`를 클릭한다.

### 지속 유입 안정성 측정

단발 저장 실험은 "한 번에 들어온 이벤트를 얼마나 빨리 저장하는지"를 확인하는 기준선이다. 하지만 실제 Smart Factory 환경에서는 설비가 멈추지 않고 지속적으로 이벤트를 생성하므로, 순간 처리량만으로는 운영 안정성을 설명하기 어렵다.

Swing 모니터의 `지속 유입 모니터링 설정`에서는 다음 값을 입력한다.

- `설비 수`: 이벤트를 생성하는 가상 설비 수
- `설비당 초당 이벤트`: 설비 1대가 1초에 생성하는 이벤트 수
- `기간(분)`: 목표 유입률을 유지할 시간
- `CRITICAL 비율(%)`: 장애 이벤트 발생 비율

예를 들어 `설비 수=100`, `설비당 초당 이벤트=50`, `기간=1`로 설정하면 목표 유입률은 `5,000 events/sec`이고, 1분 동안 총 `300,000건`의 이벤트를 처리할 수 있는지 확인한다.

이 실험에서 확인할 지표:

- `목표 유입률`: 초당 들어와야 하는 이벤트 수
- `실제 처리율`: 실제 DB 저장 처리량
- `목표 대비 처리율`: 목표 유입량 대비 처리 성공 비율
- `미처리 목표 이벤트`: 시스템이 따라가지 못해 누적된 이벤트 수

`미처리 목표 이벤트`가 계속 증가하면 현재 Direct DB Write 구조가 실제 유입량을 따라가지 못한다는 의미다. 이 결과를 기준으로 Kafka 버퍼링, Consumer 병렬화, Batch Insert, Backpressure 구조가 왜 필요한지 단계별로 설명할 수 있다.

### 실시간 위험 알림

센서 이벤트 저장 후 서버가 이벤트의 심각도를 분석하고, `WARNING` 또는 `CRITICAL`로 판단된 이벤트는 즉시 알림 이벤트로 발행한다. Spring Boot 실행 시 두 번째 Swing 창인 `SFEP Real-time Alert Monitor`가 함께 실행되며, 위험 이벤트가 발생하면 다음 정보를 실시간으로 표시한다.

- 발생 시각
- 설비 ID
- 설비 유형
- 심각도
- 온도, 진동, 전류
- 위험 메시지

또한 `/api/v1/alerts/stream` SSE endpoint를 제공하여 웹 대시보드에서도 `EventSource` 기반 실시간 알림을 연결할 수 있도록 했다.

이 기능의 목적은 단순히 이벤트를 저장하는 데서 끝나는 것이 아니라, 운영자가 위험 상황을 즉시 인지하고 대응할 수 있는 구조를 검증하는 것이다. 이후 Kafka Consumer 기반 알림 발행, WebSocket 대시보드 알림, Slack 알림으로 확장할 수 있다.

API로 실행:

```bash
curl -X POST http://localhost:8080/api/v1/simulator/direct/run \
  -H "Content-Type: application/json" \
  -d '{"equipmentCount":100,"eventsPerEquipment":10,"failureRatePercent":2}'
```

측정 항목:

- `generatedEvents`
- `savedEvents`
- `elapsedMs`
- `eventsPerSecond`
- Prometheus JVM/HTTP/DB 관련 메트릭

## Portfolio Writing Template

### AS-IS

초기 구조에서는 설비 이벤트 수집 요청이 들어오면 API 서버가 모든 이벤트를 PostgreSQL에 직접 저장했다. 이 방식은 구조가 단순하지만 이벤트 발생량이 증가할수록 DB Connection 사용량과 Insert 지연이 함께 증가할 가능성이 있었다.

### TO-BE

먼저 Direct DB Write 구조를 기준선으로 구현하고 TPS, 응답 시간, DB Connection 사용량을 측정할 수 있도록 만들었다. 이후 Kafka, Consumer 병렬화, Batch Insert, Redis 캐싱을 단계적으로 적용하여 각 개선이 실제로 어떤 병목을 해결했는지 수치로 비교할 수 있는 실험 구조를 마련했다.
