# Performance Experiment Plan

이 프로젝트는 처음부터 완성된 구조를 적용하지 않고, 병목을 측정한 뒤 단계별 개선 효과를 비교하는 방식으로 진행한다.

모든 성능 측정은 [Performance Check Template](./performance-check-template.md)을 기준으로 기록한다. 측정 목적, 환경, 시나리오, AS-IS/TO-BE 비교, 병목 분석, 다음 개선 결정을 같은 형식으로 남겨야 단계별 개선 효과를 포트폴리오에 일관되게 정리할 수 있다.

## Current Snapshot

현재 프로젝트는 Kafka 기반 비동기 수집/저장 구조와 알림 Consumer 분리까지 구현한 상태다.

가장 최근 실험에서는 100,000건 이벤트를 기준으로 Kafka topic partition 수와 Consumer concurrency 조합을 비교했다.

| case | partition | concurrency | 전체 완료 시간 | 저장 처리율 | 최대 Lag | 알림 p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| p6-c3 | 6 | 3 | 4,457ms | 34,435 events/sec | 88,729 | 1,527ms |
| p6-c6 | 6 | 6 | 4,732ms | 27,435 events/sec | 74,500 | 1,074ms |
| p12-c6 | 12 | 6 | 5,264ms | 25,927 events/sec | 81,564 | 1,427ms |
| p12-c12 | 12 | 12 | 6,330ms | 21,753 events/sec | 84,399 | 1,773ms |

측정 결과, partition과 concurrency를 무조건 늘린다고 성능이 좋아지지는 않았다. 저장 처리량은 `6 partitions / 3 concurrency`에서 가장 높았고, 알림 p95 지연은 `6 partitions / 6 concurrency`에서 가장 낮았다. 반면 `12 partitions / 12 concurrency`는 오히려 저장 처리량이 감소했다.

따라서 현재 병목은 Kafka 병렬성 부족 단독 문제가 아니라, Consumer가 PostgreSQL에 동시에 batch write를 수행하면서 발생하는 DB write 경합으로 정의한다.

다음 개선은 PostgreSQL index/partitioning과 이벤트 이력 조회 성능 측정으로 진행한다.

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

단발 저장 실험은 "한 번에 들어온 이벤트를 얼마나 빨리 저장하는지"를 확인하는 기준선이다. 하지만 실제 제조 설비 이벤트 환경에서는 설비가 멈추지 않고 지속적으로 이벤트를 생성하므로, 순간 처리량만으로는 운영 안정성을 설명하기 어렵다.

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

## Stage 0 병목 확인 결과

### 최초 구현의 문제

초기 Direct DB Write 구현은 이벤트 1건마다 다음 작업을 반복했다.

```text
SensorEvent 1건 저장
-> Equipment 단건 조회
-> Equipment 최신 상태 갱신
-> 위험 이벤트 알림 판단
```

이 구조는 구현이 단순하지만 이벤트 수가 증가할수록 DB round-trip이 이벤트 수에 비례해 증가한다. 특히 설비 최신 상태는 같은 설비에 대해 여러 이벤트가 들어와도 최종 상태 1건만 필요하지만, 초기 구현에서는 모든 이벤트마다 설비를 조회하고 갱신했다.

### 개선 전 측정 결과

| 이벤트 수 | 평균 저장 시간 | 평균 처리율 |
| ---: | ---: | ---: |
| 100 | 230ms | 456 events/sec |
| 500 | 555ms | 910 events/sec |
| 1,000 | 992ms | 1,015 events/sec |
| 3,000 | 3,453ms | 869 events/sec |
| 5,000 | 5,426ms | 923 events/sec |
| 10,000 | 11,416ms | 876 events/sec |
| 20,000 | 21,992ms | 910 events/sec |
| 50,000 | 53,611ms | 933 events/sec |

목표 시나리오인 `1,000대 x 10건 = 10,000 events` 기준으로 약 11.4초가 걸렸고, 처리율은 약 876 events/sec 수준이었다. 목표 처리량인 10,000 events/sec와 비교하면 약 10배 이상 부족했다.

## Stage 0.1 개선: JDBC Batch Insert + 최신 설비 상태 집계

### 개선 내용

이벤트 저장 경로를 다음과 같이 변경했다.

```text
요청 이벤트 전체를 SensorEvent 목록으로 변환
-> sensor_event JDBC batch insert
-> equipment_id 기준 최신 이벤트만 집계
-> equipment PostgreSQL upsert
-> 위험 이벤트 알림 발행
```

적용한 개선:

- `JpaRepository.save()` 반복 호출 제거
- `JdbcTemplate.batchUpdate()`로 `sensor_event` 일괄 insert
- PostgreSQL JDBC URL에 `reWriteBatchedInserts=true` 적용
- 동일 요청 내 설비 상태 갱신을 `이벤트 수` 기준이 아니라 `설비 수` 기준으로 축소
- `equipment` 테이블은 `ON CONFLICT (equipment_id) DO UPDATE`로 upsert 처리
- 중복 `event_id`는 `ON CONFLICT DO NOTHING`으로 idempotent하게 처리

### 개선 후 측정 결과

측정 환경:

- API: `POST /api/v1/simulator/direct/run`
- DB: Docker PostgreSQL 16
- 서버: local Spring Boot, `SERVER_PORT=18080`, desktop UI disabled
- 각 케이스 3회 평균

| 설비 수 | 설비당 이벤트 | 총 이벤트 | 개선 전 평균 저장 시간 | 개선 후 평균 저장 시간 | 시간 감소율 | 개선 후 평균 처리율 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 1 | 100 | 230ms | 25ms | 89.1% | 4,549 events/sec |
| 100 | 5 | 500 | 555ms | 44ms | 92.1% | 11,597 events/sec |
| 100 | 10 | 1,000 | 992ms | 65ms | 93.4% | 15,745 events/sec |
| 300 | 10 | 3,000 | 3,453ms | 144ms | 95.8% | 20,930 events/sec |
| 500 | 10 | 5,000 | 5,426ms | 197ms | 96.4% | 25,641 events/sec |
| 1,000 | 10 | 10,000 | 11,416ms | 380ms | 96.7% | 26,373 events/sec |
| 1,000 | 20 | 20,000 | 21,992ms | 751ms | 96.6% | 26,653 events/sec |
| 1,000 | 50 | 50,000 | 53,611ms | 1,849ms | 96.6% | 27,056 events/sec |
| 1,000 | 100 | 100,000 | 미측정 | 3,984ms | - | 25,159 events/sec |

### 해석

기존 구조는 이벤트 수가 증가할수록 저장 시간이 거의 선형으로 증가했고, 처리율은 약 900 events/sec 부근에서 정체됐다. 개선 후에는 10,000건 기준 저장 시간이 약 11.4초에서 약 0.38초로 줄었고, 처리율은 약 26,000 events/sec 수준까지 증가했다. 추가로 100,000건 요청도 측정했으며, 평균 저장 시간은 약 3.98초, 평균 처리율은 약 25,159 events/sec로 나타났다.

100,000건은 개선 전 구조에서 직접 측정하지 않았다. 50,000건까지의 AS-IS 결과를 보면 처리율이 약 900 events/sec 수준에서 정체되므로 100,000건은 100초 이상 소요될 가능성이 높지만, 포트폴리오에는 직접 측정한 값과 추정치를 구분해서 작성한다.

이번 개선으로 Direct DB Write 단계에서도 목표 처리량인 10,000 events/sec를 단발 batch 기준으로는 넘길 수 있게 됐다. 다만 이 결과는 API 요청 하나가 큰 batch를 한 번에 저장하는 상황의 성능이다. 실제 운영 환경처럼 이벤트가 지속 유입되는 상황에서는 요청 수 증가, DB connection 경쟁, consumer lag, 장애 재처리 문제가 별도로 발생할 수 있다.

### 다음 개선 포인트

현재 다음 병목 후보:

- API 서버가 여전히 이벤트 생성과 저장을 같은 요청 흐름에서 처리한다.
- 대량 요청 시 HTTP 요청은 저장 완료까지 응답을 기다려야 한다.
- 실시간 지속 유입 상황에서는 DB write 순간 부하가 API 요청 처리와 경쟁할 수 있다.
- 장애 발생 시 재시도, 지연 처리, 유실 방지 구조가 아직 부족하다.

따라서 다음 단계에서는 Kafka를 도입해 `수집 요청`과 `DB 저장`을 분리하고, Consumer Group 병렬화와 Lag 측정으로 지속 유입 상황의 안정성을 검증한다.

## Stage 1 개선: Kafka 기반 비동기 수집/저장 분리

### 개선 의도

Stage 0.1의 JDBC Batch Insert 개선으로 단발 batch 저장 성능은 크게 개선됐다. 하지만 API 요청 흐름은 여전히 다음 구조를 가진다.

```text
Client -> Spring API -> PostgreSQL 저장 완료 대기 -> Response
```

이 구조에서는 DB 저장이 오래 걸릴 경우 사용자 요청이 끝까지 대기해야 하고, DB 장애나 순간 부하가 API 응답 흐름에 직접 전파된다. 따라서 Stage 1에서는 Kafka를 도입하여 요청 수신과 DB 저장을 분리했다.

변경 구조:

```text
Client
-> Spring API
-> Kafka publish
-> Response

Kafka Consumer
-> PostgreSQL batch 저장
-> Run 상태 갱신
```

### 구현 내용

- `/api/v1/simulator/kafka/run` API 추가
- 이벤트 생성 후 `sfep.sensor-events` Kafka Topic으로 발행
- 메시지 Key는 `equipmentId`로 설정하여 이후 Partition Key 최적화 실험의 기준 마련
- Kafka Consumer batch listener 추가
- Consumer는 같은 `runId` 기준으로 이벤트를 묶어 `saveDirectBatch()` 재사용
- `/api/v1/simulator/kafka/runs/{runId}` 상태 조회 API 추가
- 상태 지표:
  - `expectedEvents`
  - `publishedEvents`
  - `consumedEvents`
  - `savedEvents`
  - `lagEvents`
  - `maxLagEvents`
  - `publishElapsedMs`
  - `processingElapsedMs`
  - `totalElapsedMs`

### 운영 설정 이슈와 해결

초기 Kafka 설정에서는 단일 브로커 환경에서 Consumer Group coordinator 조회가 지연되는 문제가 있었다.

원인:

- Kafka는 Consumer Group offset 정보를 내부 토픽에 저장한다.
- 단일 브로커 개발 환경에서 내부 토픽 복제 계수가 기본값으로 남아 있으면 coordinator가 안정적으로 생성되지 않을 수 있다.
- 그 결과 Producer는 메시지를 발행하지만 Consumer Group이 정상적으로 partition assignment를 받지 못했다.

해결:

`docker-compose.yml`의 Kafka 설정에 단일 브로커용 내부 토픽 설정을 추가했다.

```yaml
KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS: 0
```

### 측정 결과

측정 환경:

- API: `POST /api/v1/simulator/kafka/run`
- 상태 조회: `GET /api/v1/simulator/kafka/runs/{runId}`
- Kafka: Docker `apache/kafka:3.7.2`, single broker
- DB: Docker PostgreSQL 16
- 서버: local Spring Boot, `SERVER_PORT=18080`, desktop UI disabled
- Consumer concurrency: 1
- Consumer batch size: `max.poll.records=1000`

| 설비 수 | 설비당 이벤트 | 총 이벤트 | Kafka 발행 시간 | 발행 처리율 | DB 저장 완료 시간 | 전체 완료 시간 | 저장 처리율 | Lag |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 10 | 1,000 | 212ms | 4,717 events/sec | 256ms | 468ms | 3,906 events/sec | 0 |
| 1,000 | 10 | 10,000 | 174ms | 57,471 events/sec | 820ms | 994ms | 12,195 events/sec | 0 |
| 1,000 | 100 | 100,000 | 1,220ms | 81,967 events/sec | 4,628ms | 5,849ms | 21,608 events/sec | 0 |

### 2026-06-18 재측정 결과

측정 조건:

- 서버: local Spring Boot, `SERVER_PORT=18080`, desktop UI disabled
- DB: Docker PostgreSQL 16
- Kafka: Docker `apache/kafka:3.7.2`, single broker
- Consumer concurrency: 1
- Consumer batch size: `max.poll.records=1000`
- 반복 횟수: 단일 실행 스냅샷
- 데이터 초기화 없이 누적 데이터가 존재하는 상태에서 측정

Direct DB Write:

| 설비 수 | 설비당 이벤트 | 총 이벤트 | 저장 시간 | 처리율 |
| ---: | ---: | ---: | ---: | ---: |
| 100 | 10 | 1,000 | 135ms | 7,407 events/sec |
| 1,000 | 10 | 10,000 | 483ms | 20,704 events/sec |
| 1,000 | 50 | 50,000 | 1,951ms | 25,628 events/sec |
| 1,000 | 100 | 100,000 | 4,513ms | 22,158 events/sec |

Kafka Publish + Consumer 저장:

| 설비 수 | 설비당 이벤트 | 총 이벤트 | Kafka 발행 시간 | 발행 처리율 | DB 저장 완료 시간 | 전체 완료 시간 | 저장 처리율 | 최대 관측 Lag | 최종 Lag |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 10 | 1,000 | 204ms | 4,902 events/sec | 180ms | 385ms | 5,556 events/sec | 0 | 0 |
| 1,000 | 10 | 10,000 | 152ms | 65,789 events/sec | 601ms | 754ms | 16,639 events/sec | 7,248 | 0 |
| 1,000 | 50 | 50,000 | 595ms | 84,034 events/sec | 2,078ms | 2,674ms | 24,062 events/sec | 37,394 | 0 |
| 1,000 | 100 | 100,000 | 1,117ms | 89,526 events/sec | 4,310ms | 5,427ms | 23,202 events/sec | 83,735 | 0 |

### 2026-06-18 병목 분석

Direct DB Write는 100,000건 기준 4.513초에 저장을 완료했고, 처리율은 약 22,158 events/sec로 측정됐다. Stage 0.1에서 적용한 JDBC Batch Insert와 설비 최신 상태 집계 덕분에 단발 대량 저장 성능은 목표 기준인 10,000 events/sec를 초과했다.

Kafka 경로는 100,000건 기준 API 발행 시간이 1.117초로 Direct DB Write의 전체 저장 시간보다 짧다. 즉 사용자 요청 흐름은 DB 저장 완료까지 기다리지 않고 빠르게 분리할 수 있다. 반면 전체 저장 완료 시간은 5.427초로 Direct DB Write보다 길게 측정됐다. 이는 현재 Consumer concurrency가 1이고 Topic partition도 단일 partition이라, Kafka 뒤쪽의 DB 저장 작업이 단일 Consumer 흐름에 묶여 있기 때문이다.

따라서 Stage 1의 개선 효과는 `단발 저장 완료 시간 단축`이 아니라 `요청 흐름과 DB write 부하 분리`, `폭주 이벤트 버퍼링`, `Consumer Lag 기반 처리 지연 관측`에 있다. 특히 100,000건 테스트에서 최대 Lag가 83,735건까지 쌓였다가 최종적으로 0으로 수렴했으므로, Kafka가 유입 폭주를 흡수하고 Consumer가 후속 처리할 수 있는 구조는 확인됐다.

다음 개선은 Stage 2로 진행한다. Topic partition 수를 늘리고 Consumer concurrency를 확장하여 Lag 감소 속도와 저장 처리율이 실제로 증가하는지 검증한다.

### 2026-06-18 Stage 1 병목 지점 체크

현재 측정과 설정 확인 결과 병목 후보는 다음 순서로 정리된다.

| 우선순위 | 병목 지점 | 근거 | 영향 | 개선 방향 |
| ---: | --- | --- | --- | --- |
| 1 | Kafka partition/Consumer 병렬성 부족 | `sfep.sensor-events` topic이 `PartitionCount=1`, Consumer concurrency도 1 | 100,000건에서 최대 Lag 83,735건 발생. Consumer Group을 늘려도 단일 partition이면 실질 병렬 처리 불가 | Topic partition 3~6개로 재생성, `SFEP_KAFKA_CONCURRENCY` 증가 |
| 2 | Consumer 저장 흐름이 단일 batch 처리 경로에 집중 | Consumer가 poll한 메시지를 `saveDirectBatch()`로 저장하고 ack 처리 | Kafka 발행은 빠르지만 DB 저장 완료 시간은 Consumer 처리량에 묶임 | Consumer 병렬화 후 partition별 처리량, lag 감소 속도 측정 |
| 3 | PostgreSQL 인덱스 갱신 비용 | `sensor_event` 누적 row 약 163만 건, `event_id` unique index와 복합 index 3개 존재 | insert 시 데이터 저장뿐 아니라 여러 index 갱신 비용 발생 | 조회 패턴 기준 index 재검토, stage 6에서 table partitioning 검증 |
| 4 | 알림 발행이 저장 트랜잭션 내부에서 동기 실행 | `saveDirectBatch()` 내부에서 `events.forEach(this::publishAlertIfNeeded)` 실행 | subscriber가 느려지면 저장 경로도 함께 지연될 수 있음 | 알림 발행을 별도 queue/Kafka topic/SSE worker로 분리 |
| 5 | Producer가 요청 전체를 메모리에 생성 후 event별 future 생성 | 100,000건 요청 시 `SensorEventRequest` list와 `CompletableFuture[]`를 한 번에 생성 | 100,000건까지는 허용 가능하지만 더 큰 부하에서 GC/메모리 압박 가능 | chunk 단위 발행 또는 지속 유입 generator로 전환 |

현재 가장 먼저 개선해야 할 지점은 `Kafka partition/Consumer 병렬성`이다. Direct DB Write와 Kafka Consumer 저장 처리율이 모두 약 22,000~24,000 events/sec 부근에 형성되어 있으므로, 단일 저장 경로 자체는 나쁘지 않다. 하지만 Kafka 유입은 약 89,000 events/sec까지 가능했기 때문에 발행 속도와 저장 속도 사이에 차이가 생기고, 이 차이가 Lag로 누적된다.

따라서 Stage 2의 검증 질문은 다음과 같다.

- partition 수를 늘리면 Consumer가 실제로 병렬 처리되는가?
- Consumer concurrency를 늘렸을 때 100,000건 전체 완료 시간이 5.427초보다 줄어드는가?
- 최대 Lag가 83,735건보다 낮아지거나, Lag가 0으로 수렴하는 시간이 줄어드는가?
- DB가 Consumer 병렬화를 받아줄 수 있는가, 아니면 PostgreSQL write/index 갱신이 다음 병목으로 드러나는가?

## Stage 2: Kafka Partition + Consumer Group 병렬화

### 개선 목적

Stage 1에서 Kafka를 도입해 API 요청 흐름과 DB 저장 흐름은 분리했다. 하지만 Topic partition과 Consumer concurrency가 1인 상태에서는 Kafka 뒤쪽의 저장 처리가 단일 Consumer에 묶였다. 그 결과 100,000건 기준 최대 Lag가 83,735건까지 쌓였고, 전체 저장 완료 시간은 5.427초로 측정됐다.

Stage 2에서는 Topic partition을 6개로 확장하고 Consumer concurrency를 3으로 늘려, Kafka Consumer Group이 실제로 병렬 처리되는지 검증했다.

### 구현 내용

- `KafkaTopicConfig` 추가
  - `sfep.sensor-events` Topic을 애플리케이션 설정으로 관리
  - 기본 partition 수: 6
  - 단일 브로커 개발 환경 replication factor: 1
- `spring.kafka.listener.concurrency` 기본값을 3으로 변경
- 기존 Topic partition을 6개로 확장
- Consumer Group assignment 확인
  - Consumer 1: partition 0, 1
  - Consumer 2: partition 2, 3
  - Consumer 3: partition 4, 5
- 성능 측정 응답에 `maxLagEvents` 추가
  - 최종 Lag는 0으로 수렴하더라도, 처리 중 최고 적체량을 기록하기 위함

### 측정 결과

측정 조건:

- 서버: local Spring Boot, `SERVER_PORT=18080`, desktop UI disabled
- DB: Docker PostgreSQL 16
- Kafka: Docker `apache/kafka:3.7.2`, single broker
- Topic partition: 6
- Consumer concurrency: 3
- Consumer batch size: `max.poll.records=1000`
- 데이터 초기화 없이 누적 데이터가 존재하는 상태에서 측정

| 설비 수 | 설비당 이벤트 | 총 이벤트 | Kafka 발행 시간 | 발행 처리율 | DB 저장 완료 시간 | 전체 완료 시간 | 저장 처리율 | 최대 관측 Lag | 최종 Lag |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 10 | 10,000 | 201ms | 49,751 events/sec | 405ms | 607ms | 24,691 events/sec | - | 0 |
| 1,000 | 50 | 50,000 | 523ms | 95,602 events/sec | 760ms | 1,283ms | 65,789 events/sec | - | 0 |
| 1,000 | 100 | 100,000 | 964ms | 103,734 events/sec | 1,147ms | 2,112ms | 87,184 events/sec | - | 0 |
| 1,000 | 100 | 100,000 | 1,272ms | 78,616 events/sec | 2,064ms | 3,337ms | 48,450 events/sec | 75,129 | 0 |

마지막 100,000건 측정은 `maxLagEvents`를 잡기 위해 50ms 간격으로 상태 조회를 수행한 결과다. 상태 조회 polling 부하가 포함되어 전체 시간은 앞선 100,000건 측정보다 길지만, 최대 Lag가 75,129건까지 발생했다가 최종 0으로 수렴하는 것을 확인했다.

### Stage 1 대비 개선 효과

| 항목 | Stage 1 | Stage 2 | 개선 효과 |
| --- | ---: | ---: | ---: |
| Consumer concurrency | 1 | 3 | 3개 Consumer 병렬 처리 |
| Topic partition | 1 | 6 | partition 기반 분산 처리 가능 |
| 100,000건 전체 완료 시간 | 5,427ms | 2,112ms | 약 61.1% 감소 |
| 100,000건 저장 처리율 | 23,202 events/sec | 87,184 events/sec | 약 3.76배 증가 |
| 100,000건 최대 관측 Lag | 83,735 | 75,129 | 약 10.3% 감소 |
| 최종 Lag | 0 | 0 | 안정적으로 수렴 |

### Stage 2 병목 분석

Stage 2에서 Consumer 병렬화를 적용한 결과, 전체 완료 시간과 저장 처리율은 크게 개선됐다. 따라서 Stage 1에서 확인한 `partition/consumer 병렬성 부족`은 실제 병목이 맞았고, 개선 효과도 확인됐다.

다만 100,000건을 순간 발행하면 여전히 최대 Lag가 75,129건까지 쌓인다. 이는 Kafka 발행 처리율이 순간적으로 78,000~103,000 events/sec 수준까지 올라가는 반면, Consumer가 DB batch insert와 설비 최신 상태 upsert를 수행하는 속도는 그보다 낮기 때문이다.

다음 병목 후보는 다음 순서로 정리된다.

| 우선순위 | 병목 지점 | 근거 | 다음 개선 방향 |
| ---: | --- | --- | --- |
| 1 | Consumer + DB batch 저장 처리량 | Consumer 병렬화 후에도 100,000건 최대 Lag가 75,129건 발생 | batch size, DB connection pool, Consumer concurrency 조합별 측정 |
| 2 | PostgreSQL index 갱신 비용 | 누적 row가 증가한 상태에서도 insert 시 unique index와 복합 index를 함께 갱신 | 조회 패턴 기준 index 최소화, table partitioning 검증 |
| 3 | 알림 발행이 저장 트랜잭션 내부에서 실행 | `saveDirectBatch()` 내부에서 위험 이벤트 알림을 즉시 publish | 알림 발행을 별도 queue/Kafka topic으로 분리 |
| 4 | Producer가 요청 전체를 한 번에 발행 | 100,000건 순간 발행 시 Consumer 처리량보다 유입량이 큼 | chunk 발행, 지속 유입 모드, backpressure 적용 |

따라서 다음 단계는 무작정 Consumer 수를 더 늘리는 것이 아니라, `Consumer concurrency`, `max.poll.records`, `jdbcBatchSize`, `Hikari connection pool`, `DB index` 조합을 고정된 템플릿으로 비교하는 것이다. Consumer 수만 늘리면 DB write 경합이 커져 오히려 성능이 떨어질 수 있으므로, Stage 3에서는 Batch Insert와 DB write 설정을 중심으로 측정한다.

### 해석

Stage 1의 핵심은 DB 저장 성능 자체를 더 빠르게 만드는 것이 아니라, API 요청 흐름과 DB 저장 흐름을 분리하는 것이다. 100,000건 기준으로 보면 API는 Kafka 발행 완료 시점인 약 1.22초에 응답할 수 있고, 실제 DB 저장은 Consumer가 약 5.85초 안에 완료했다.

Direct DB Write 개선 후 100,000건은 평균 약 3.98초에 저장됐다. Kafka 경로의 전체 저장 완료 시간은 5.85초로 더 길지만, 사용자 요청이 DB 저장 전체를 기다리지 않아도 된다는 차이가 있다. 즉 Stage 1은 단발 저장 시간 개선보다는 다음 문제를 해결한다.

- API 요청 흐름과 DB write 부하 분리
- 요청 폭주 시 Kafka를 통한 buffering
- Consumer Lag 기반 처리 지연 관측
- 이후 Consumer Group 병렬화로 수평 확장 가능한 구조 확보

### 다음 개선 포인트

현재 Stage 1의 병목 후보:

- Consumer concurrency가 1이라 DB 저장은 단일 Consumer가 처리한다.
- Topic partition이 1개면 Consumer Group을 늘려도 실제 병렬 처리가 되지 않는다.
- 메시지 발행은 빠르지만 저장 완료는 Consumer 처리량과 DB batch 성능에 의존한다.

따라서 다음 단계에서는 Topic partition 수와 Consumer concurrency를 늘려 Consumer Group 병렬화 효과를 측정한다.

## Portfolio Writing Template

### AS-IS

초기 구조에서는 설비 이벤트 수집 요청이 들어오면 API 서버가 모든 이벤트를 PostgreSQL에 직접 저장했다. 이 방식은 구조가 단순하지만 이벤트 발생량이 증가할수록 DB Connection 사용량과 Insert 지연이 함께 증가할 가능성이 있었다.

### TO-BE

먼저 Direct DB Write 구조를 기준선으로 구현하고 TPS, 응답 시간, DB Connection 사용량을 측정할 수 있도록 만들었다. 이후 Kafka, Consumer 병렬화, Batch Insert, Redis 캐싱을 단계적으로 적용하여 각 개선이 실제로 어떤 병목을 해결했는지 수치로 비교할 수 있는 실험 구조를 마련했다.
