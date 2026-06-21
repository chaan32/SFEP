# SFEP Final Summary

작성일: 2026-06-18

이 문서는 SFEP 프로젝트에서 지금까지 진행한 내용을 처음부터 현재 단계까지 한 번에 정리한 최종 요약 문서다.

프로젝트를 왜 시작했는지, 어떤 구조로 만들었는지, 어떤 코드를 추가했는지, 성능이 어떻게 바뀌었는지, 현재 병목이 무엇인지까지 포트폴리오 작성 기준으로 정리한다.

---

## 1. 프로젝트 한 줄 요약

SFEP는 제조 설비에서 발생하는 대규모 이벤트를 실시간으로 수집하고, 위험 이벤트를 감지하며, Kafka 기반 비동기 처리와 PostgreSQL 저장 최적화를 통해 저장 처리량과 알림 지연 시간을 단계적으로 개선한 대규모 이벤트 처리 시스템이다.

이 프로젝트의 핵심은 단순히 CRUD API를 만드는 것이 아니라, 실제 제조 현장에서 발생할 수 있는 대량 이벤트 유입 상황을 가정하고 다음 흐름을 직접 설계하고 검증하는 것이다.

```text
설비 이벤트 생성
-> API 수신
-> Kafka 발행
-> Storage Consumer 저장
-> Alert Consumer 위험 판단
-> SSE / Swing 알림
-> 성능 측정
-> 병목 정의
-> 다음 개선 진행
```

---

## 1.1 최종 핵심 성과

- 대규모 이벤트 처리 시스템을 설계하고, 100,000건 규모의 설비 이벤트를 기준으로 수집, 저장, 알림 흐름을 단계별로 개선했다.
- Direct DB Write 구조에서 발생하던 DB 저장 병목을 JDBC Batch Insert로 개선하여, 50,000건 기준 처리 시간을 **53,611ms -> 1,849ms**로 단축했다.
- 저장 처리율은 동일 조건에서 **933 events/sec -> 27,056 events/sec**로 향상되었다.
- Kafka 기반 비동기 처리와 Consumer 병렬화를 적용하여 API 요청 흐름과 저장 흐름을 분리했고, 약 **50,000 events/sec** 수준의 대량 이벤트 처리 구조를 검증했다.
- Storage Consumer와 Alert Consumer를 분리하여 DB 저장 병목이 알림 지연으로 전파되지 않도록 개선했고, 100,000건 기준 p95 알림 지연 시간을 **6,959ms -> 1,926ms**로 줄였다.
- Kafka partition/concurrency 조합 실험을 통해 단순 병렬화가 항상 성능 향상으로 이어지지 않으며, DB write 경합을 고려한 최적 조합이 필요하다는 점을 수치로 검증했다.

---

## 2. 프로젝트를 시작한 이유

제조 공장에서는 수백 대에서 수천 대의 설비가 동시에 동작한다.

각 설비는 온도, 진동, 전류, RPM, 전력 사용량 같은 데이터를 계속 생성한다. 이 데이터가 많아지면 단순히 DB에 저장하는 것만으로는 충분하지 않다.

운영자 입장에서 중요한 것은 다음과 같다.

- 어떤 설비가 위험 상태인지 빠르게 확인할 수 있는가
- 장애 이벤트가 유실되지 않고 저장되는가
- 이벤트가 많이 몰려도 API 서버가 멈추지 않는가
- DB 저장이 느려져도 위험 알림은 빠르게 전달되는가
- 성능 병목을 수치로 확인하고 개선할 수 있는가

그래서 SFEP는 처음부터 완성형 아키텍처를 만드는 대신, 가장 단순한 구조부터 시작해서 병목을 하나씩 발견하고 개선하는 방식으로 진행했다.

---

## 3. 목표 시나리오

기본 목표는 가상의 대규모 제조 설비 이벤트 환경을 구성하는 것이다.

```text
설비 수: 1,000대
설비당 이벤트 수: 초당 10건
목표 유입량: 10,000 events/sec
```

각 설비 이벤트는 다음과 같은 데이터를 가진다.

```json
{
  "equipmentId": "EQ-0001",
  "equipmentType": "CONVEYOR",
  "status": "RUNNING",
  "temperature": 84.5,
  "vibration": 2.4,
  "rpm": 2300,
  "power": 4.3,
  "currentValue": 35.0,
  "occurredAt": "2026-06-18T10:00:00"
}
```

위 데이터를 기반으로 서버는 위험도를 판단한다.

```text
NORMAL
WARNING
CRITICAL
```

---

## 4. 현재 전체 구조

현재 구조는 Kafka 기반 이벤트 처리 구조와 실시간 알림 구조까지 포함한다.

```text
Factory Simulator
-> Spring API
-> Kafka Topic
   |
   +-> Storage Consumer Group
   |   -> JDBC Batch Insert
   |   -> PostgreSQL sensor_event 저장
   |   -> equipment 최신 상태 upsert
   |
   +-> Alert Consumer Group
       -> 위험도 판단
       -> AlertEventBus
       -> SSE / Swing Alert Monitor

Spring Actuator
-> Prometheus
-> Grafana
```

현재 프로젝트에서 중요한 점은 저장 Consumer와 알림 Consumer를 분리했다는 것이다.

처음에는 DB 저장 후 알림을 발행하는 구조였지만, 이 방식은 DB 저장이 느려지면 알림도 같이 늦어진다. 그래서 Kafka의 같은 topic을 서로 다른 consumer group이 읽도록 분리했다.

```text
Storage Consumer Group: DB 저장 담당
Alert Consumer Group: 위험 이벤트 알림 담당
```

이렇게 하면 DB 저장 경로가 느려져도 알림 경로는 독립적으로 동작할 수 있다.

---

## 5. 기술 스택

| 영역 | 사용 기술 |
| --- | --- |
| Backend | Java 21, Spring Boot 3.5 |
| Persistence | PostgreSQL, Spring Data JPA, JdbcTemplate |
| Messaging | Kafka |
| Cache / Infra 준비 | Redis |
| Monitoring | Spring Actuator, Micrometer, Prometheus, Grafana |
| Desktop Monitor | Java Swing |
| Infra | Docker Compose |
| Benchmark | Node.js script, Shell script |

Redis는 인프라 구성에 포함되어 있지만, 현재 핵심 검증은 PostgreSQL 저장, Kafka Consumer 병렬화, 알림 분리에 집중했다. Redis 최신 상태 캐싱은 후속 개선 단계에서 검증할 수 있는 영역으로 남겨두었다.

---

## 6. 주요 기능

### 6.1 Factory Simulator

가상의 설비 데이터를 생성한다.

설정 가능한 값:

- 설비 수
- 설비당 이벤트 수
- 장애 비율
- 지속 유입 시간
- 설비당 초당 이벤트 수

이를 통해 다음 두 가지 실험이 가능하다.

```text
1. 단발 이벤트 처리 성능 측정
2. 일정 시간 동안 지속적으로 들어오는 이벤트 처리 안정성 측정
```

---

### 6.2 Direct DB Write Monitor

Spring Boot 실행 시 Swing UI를 띄워 직접 성능을 볼 수 있게 만들었다.

확인 가능한 값:

- 저장 이벤트 수
- 진행률
- 소요 시간
- 초당 처리율
- 전체 설비 수
- 최근 1분 이벤트 수
- WARNING / CRITICAL 이벤트 수

이 UI는 단순 테스트용이 아니라, 포트폴리오 설명에서 "내가 직접 실험 조건을 바꿔가며 병목을 측정했다"는 것을 보여주기 위한 장치다.

---

### 6.3 Real-time Alert Monitor

위험 이벤트가 발생하면 별도 Swing 창에서 실시간으로 알림을 표시한다.

표시 내용:

- 발생 시각
- 알림 수신 시각
- 설비 ID
- 설비 유형
- 심각도
- 온도 / 진동 / 전류
- 알림 지연 시간

이 기능을 통해 단순 저장 처리율뿐 아니라, 운영자가 위험 상황을 얼마나 빨리 인지할 수 있는지도 함께 측정할 수 있게 되었다.

---

### 6.4 SSE 알림 API

웹 대시보드나 외부 클라이언트가 실시간 알림을 받을 수 있도록 SSE endpoint를 제공한다.

```text
GET /api/v1/alerts/stream
```

현재는 SSE 기반이고, 이후 WebSocket이나 Slack 알림으로 확장할 수 있다.

---

### 6.5 알림 지연 지표 API

알림 지연 시간을 서버가 직접 수집하도록 만들었다.

```text
GET  /api/v1/alerts/metrics
POST /api/v1/alerts/metrics/reset
```

측정 지표:

- 전체 알림 수
- 평균 알림 지연
- p95 알림 지연
- p99 알림 지연

주의할 점은 현재 지표가 "서버에서 AlertEventBus가 알림을 발행한 시점"까지의 지연이라는 것이다.

즉 완전한 end-to-end UI 렌더링 지연까지 측정한 것은 아니다. 클라이언트 수신 ACK까지 포함하려면 후속 단계에서 ACK API를 추가하면 된다.

---

### 6.6 Benchmark Script

반복 측정을 위해 스크립트를 만들었다.

```text
scripts/benchmark-sfep.mjs
scripts/run-performance-matrix.sh
scripts/run-partition-concurrency-matrix.sh
```

이 스크립트들은 다음 값을 자동으로 측정한다.

- 이벤트 수
- Kafka 발행 시간
- DB 저장 완료 시간
- 전체 완료 시간
- 저장 처리율
- 최대 Kafka Lag
- 알림 수
- 알림 평균 지연
- 알림 p95
- 알림 p99

성능 측정은 앞으로도 `docs/performance-check-template.md` 형식에 맞춰 기록하도록 정리했다.

---

## 7. 주요 코드 구조

### Simulator

| 파일 | 역할 |
| --- | --- |
| `FactorySimulatorController.java` | Direct/Kafka 시뮬레이션 API 제공 |
| `FactorySimulatorService.java` | 가상 설비 이벤트 생성, Direct/Kafka 실행 흐름 관리 |
| `SimulatorRunRequest.java` | 시뮬레이션 요청 DTO |
| `SimulatorRunResponse.java` | 시뮬레이션 결과 DTO |

---

### Event 저장

| 파일 | 역할 |
| --- | --- |
| `SensorEventService.java` | 이벤트 저장 핵심 서비스 |
| `SensorEvent.java` | 센서 이벤트 Entity |
| `SensorEventRepository.java` | 이벤트 조회 repository |
| `BatchSensorEventRequest.java` | batch 저장 요청 DTO |
| `DirectWriteResult.java` | direct 저장 결과 DTO |

`SensorEventService`는 처음에는 JPA 단건 저장 중심이었지만, 성능 개선 후 `JdbcTemplate.batchUpdate()` 기반 대량 insert 중심으로 바뀌었다.

---

### 위험도 판단 / 알림

| 파일 | 역할 |
| --- | --- |
| `EventRiskAnalyzer.java` | 온도, 진동, 전류, 상태값 기준으로 위험도 판단 |
| `AlertEventPublisher.java` | 위험 이벤트만 알림으로 발행 |
| `AlertEventBus.java` | SSE/Swing 구독자에게 알림 전달 |
| `AlertMetricsService.java` | 알림 지연 평균, p95, p99 집계 |
| `AlertStreamController.java` | SSE stream과 알림 지표 API 제공 |
| `SfepAlertFrame.java` | 실시간 알림 Swing UI |

---

### Kafka

| 파일 | 역할 |
| --- | --- |
| `KafkaTopicConfig.java` | Kafka topic partition/replication 설정 |
| `KafkaErrorHandlingConfig.java` | Retry / DLT 기반 에러 처리 설정 |
| `SensorEventKafkaProducer.java` | 이벤트를 Kafka로 발행 |
| `SensorEventKafkaConsumer.java` | 저장 Consumer |
| `SensorEventAlertKafkaConsumer.java` | 알림 Consumer |
| `KafkaRunTracker.java` | Kafka run 상태 추적 |
| `KafkaRunState.java` | run별 발행/소비/저장 상태 보관 |
| `KafkaRunStatusResponse.java` | run 상태 조회 응답 |

---

### Dashboard / Desktop

| 파일 | 역할 |
| --- | --- |
| `DashboardController.java` | 저장된 이벤트 요약 API |
| `DashboardService.java` | 설비 수, 최근 이벤트, 위험 이벤트 통계 계산 |
| `SfepDesktopFrame.java` | Direct DB Write 성능 모니터 |
| `SfepDesktopRunner.java` | Spring Boot 실행 시 Swing UI 실행 |

---

## 8. 단계별 진행 정리

## STEP 01. Direct DB Write 기준선 구현

처음에는 가장 단순한 구조로 시작했다.

```text
Factory Simulator
-> Spring API
-> PostgreSQL
```

초기 구현은 이벤트를 1건씩 저장하고, 이벤트마다 설비를 조회하고, 설비 최신 상태를 갱신했다.

```text
이벤트 1건 저장
-> equipment 단건 조회
-> equipment 최신 상태 갱신
-> 위험 이벤트 알림 판단
```

이 구조의 문제는 이벤트 수가 늘어날수록 DB round-trip이 그대로 증가한다는 점이다.

### 초기 측정 결과

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

10,000건 기준 약 11.4초가 걸렸고, 처리율은 약 876 events/sec였다.

목표인 10,000 events/sec와 비교하면 한참 부족했다.

### 병목 정의

```text
이벤트마다 DB 저장/조회/수정이 반복됨
-> DB round-trip 증가
-> 10,000건 이상에서 처리 시간이 초 단위로 증가
```

---

## STEP 02. JDBC Batch Insert + 최신 상태 Upsert

초기 병목을 해결하기 위해 저장 방식을 바꿨다.

기존:

```text
1 Event = 1 Insert
1 Event = 1 Equipment Update
```

개선:

```text
이벤트 목록 생성
-> sensor_event JDBC batch insert
-> equipmentId 기준 최신 이벤트만 집계
-> equipment upsert
```

적용한 내용:

- `JpaRepository.save()` 반복 제거
- `JdbcTemplate.batchUpdate()` 기반 batch insert 적용
- PostgreSQL JDBC URL에 `reWriteBatchedInserts=true` 적용
- `equipment`는 `ON CONFLICT DO UPDATE`로 upsert
- 같은 설비의 최신 상태는 요청 내 마지막 이벤트 기준으로 한 번만 갱신
- `event_id`는 `ON CONFLICT DO NOTHING`으로 중복 저장 방지

### 개선 후 측정 결과

| 총 이벤트 | 개선 전 평균 저장 시간 | 개선 후 평균 저장 시간 | 감소율 | 개선 후 처리율 |
| ---: | ---: | ---: | ---: | ---: |
| 100 | 230ms | 25ms | 89.1% | 4,549 events/sec |
| 500 | 555ms | 44ms | 92.1% | 11,597 events/sec |
| 1,000 | 992ms | 65ms | 93.4% | 15,745 events/sec |
| 3,000 | 3,453ms | 144ms | 95.8% | 20,930 events/sec |
| 5,000 | 5,426ms | 197ms | 96.4% | 25,641 events/sec |
| 10,000 | 11,416ms | 380ms | 96.7% | 26,373 events/sec |
| 20,000 | 21,992ms | 751ms | 96.6% | 26,653 events/sec |
| 50,000 | 53,611ms | 1,849ms | 96.6% | 27,056 events/sec |
| 100,000 | 미측정 | 3,984ms | - | 25,159 events/sec |

10,000건 기준 저장 시간이 11.4초에서 0.38초로 줄었다.

이 단계에서 가장 큰 개선은 DB round-trip을 이벤트 수만큼 반복하지 않도록 만든 것이다.

### 병목 변화

```text
기존 병목: 단건 insert / 단건 update 반복
해결 방법: JDBC batch insert + equipment 최신 상태 집계
다음 병목: API 요청이 여전히 DB 저장 완료까지 기다림
```

---

## STEP 03. Kafka 도입

Batch Insert로 저장 성능은 좋아졌지만, API 요청은 여전히 DB 저장이 끝날 때까지 기다렸다.

그래서 Kafka를 도입해서 요청 수신과 DB 저장을 분리했다.

기존:

```text
Client
-> Spring API
-> PostgreSQL 저장 완료 대기
-> Response
```

개선:

```text
Client
-> Spring API
-> Kafka publish
-> Response

Kafka Consumer
-> PostgreSQL batch 저장
```

### 구현 내용

- `/api/v1/simulator/kafka/run` 추가
- `SensorEventKafkaProducer` 추가
- `SensorEventKafkaConsumer` 추가
- `KafkaRunTracker`로 run별 상태 추적
- `maxLagEvents`, `publishedEvents`, `savedEvents` 측정
- Kafka 단일 브로커 환경 설정 보정

단일 브로커 환경에서 consumer group coordinator 문제가 생기지 않도록 다음 설정을 추가했다.

```yaml
KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS: 0
```

### Stage 1 재측정 결과

| 총 이벤트 | Kafka 발행 시간 | DB 저장 완료 시간 | 전체 완료 시간 | 저장 처리율 | 최대 Lag | 최종 Lag |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 204ms | 180ms | 385ms | 5,556 events/sec | 0 | 0 |
| 10,000 | 152ms | 601ms | 754ms | 16,639 events/sec | 7,248 | 0 |
| 50,000 | 595ms | 2,078ms | 2,674ms | 24,062 events/sec | 37,394 | 0 |
| 100,000 | 1,117ms | 4,310ms | 5,427ms | 23,202 events/sec | 83,735 | 0 |

Kafka 도입 후 API는 100,000건을 약 1.1초 만에 Kafka로 발행할 수 있었다.

하지만 Consumer concurrency가 1이고 partition도 1개였기 때문에, 뒤쪽 저장 경로는 여전히 단일 Consumer에 묶였다.

### 병목 정의

```text
Kafka 발행은 빠름
-> Consumer 저장은 단일 흐름
-> 100,000건에서 최대 Lag 83,735건 발생
```

다음 단계는 partition과 consumer concurrency를 늘려 실제 병렬 처리가 되는지 확인하는 것이었다.

---

## STEP 04. Kafka Partition + Consumer 병렬화

Kafka에서 병렬 처리의 기본 단위는 partition이다.

Consumer concurrency를 늘리더라도 topic partition이 1개라면 실제로는 하나의 Consumer만 일한다.

그래서 topic partition을 6개로 늘리고, Consumer concurrency를 3으로 설정했다.

```text
Topic partition: 6
Consumer concurrency: 3
```

### 구현 내용

- `KafkaTopicConfig` 추가
- `sfep.sensor-events` topic partition 설정 관리
- `spring.kafka.listener.concurrency` 환경변수화
- Consumer Group assignment 확인
- `maxLagEvents` 측정 추가

### 측정 결과

| 총 이벤트 | Stage 1 전체 완료 시간 | Stage 2 전체 완료 시간 | 개선 효과 |
| ---: | ---: | ---: | ---: |
| 100,000 | 5,427ms | 2,112ms | 약 61.1% 감소 |

| 항목 | Stage 1 | Stage 2 |
| --- | ---: | ---: |
| Topic partition | 1 | 6 |
| Consumer concurrency | 1 | 3 |
| 100,000건 저장 처리율 | 23,202 events/sec | 87,184 events/sec |
| 100,000건 최대 Lag | 83,735 | 75,129 |
| 최종 Lag | 0 | 0 |

Consumer 병렬화는 효과가 있었다.

하지만 순간 발행량이 워낙 커서 100,000건에서는 여전히 큰 Lag가 발생했다.

### 병목 변화

```text
기존 병목: partition/consumer 병렬성 부족
개선 결과: 저장 처리율 크게 증가
다음 병목: DB batch write와 Consumer 설정 조합
```

---

## STEP 05. 저장 Consumer와 알림 Consumer 분리

이 단계에서는 저장 성능뿐 아니라 알림 지연을 보기 시작했다.

기존 구조:

```text
Kafka Consumer
-> PostgreSQL 저장
-> 위험도 판단
-> 알림 발행
```

문제는 DB 저장이 느려지면 알림도 늦어진다는 것이다.

개선 구조:

```text
sfep.sensor-events
|
+-> Storage Consumer Group
|   -> PostgreSQL 저장
|
+-> Alert Consumer Group
    -> 위험도 판단
    -> AlertEventBus
    -> SSE / Swing 알림
```

### 구현 내용

- `EventRiskAnalyzer` 추가
- `AlertEventPublisher` 추가
- `SensorEventAlertKafkaConsumer` 추가
- 저장 Consumer에서 알림 발행 제거
- `AlertMetricsService`로 평균/p95/p99 알림 지연 측정
- `AlertStreamController`에 알림 지표 API 추가
- `AlertAsyncConfig`로 알림 전달 비동기 executor 구성

### 알림 지연 측정 결과

| 방식 | 이벤트 수 | 전체 완료 시간 | 저장 처리율 | 최대 Lag | 알림 수 | 알림 평균 | 알림 p95 | 알림 p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DIRECT | 10,000 | 1,578ms | 6,798 | 0 | 992 | 1,484ms | 1,486ms | 1,486ms |
| KAFKA | 10,000 | 1,032ms | 12,225 | 8,777 | 966 | 150ms | 221ms | 224ms |
| DIRECT | 50,000 | 5,502ms | 9,206 | 0 | 4,952 | 5,459ms | 5,479ms | 5,480ms |
| KAFKA | 50,000 | 3,043ms | 21,796 | 43,650 | 4,953 | 476ms | 760ms | 770ms |
| DIRECT | 100,000 | 7,000ms | 14,514 | 0 | 10,148 | 6,927ms | 6,959ms | 6,962ms |
| KAFKA | 100,000 | 6,728ms | 21,222 | 85,700 | 10,073 | 1,246ms | 1,926ms | 1,998ms |

100,000건 기준 알림 p95는 다음과 같이 개선됐다.

```text
DIRECT alertP95 = 6,959ms
KAFKA  alertP95 = 1,926ms
개선율 = 약 72.3%
```

즉 Kafka 구조의 가치는 단순히 저장 완료 시간을 줄이는 것만이 아니라, 저장 병목과 알림 경로를 분리해 위험 이벤트를 더 빨리 전달하는 데 있다는 것을 확인했다.

### 병목 정의

```text
저장 경로와 알림 경로 분리는 효과 있음
하지만 Alert Consumer도 raw event 전체를 읽어야 함
100,000건에서는 알림 p95가 1.9초까지 증가
```

후속 개선으로는 위험 이벤트 전용 topic 분리 또는 alert-events topic을 둘 수 있다.

---

## STEP 06. Producer 튜닝 + Partition / Concurrency 조합 비교

이 단계에서는 Kafka 병렬성을 더 늘리면 성능이 좋아지는지 검증했다.

공통 조건:

```text
총 이벤트 수: 100,000
설비 수: 1,000
설비당 이벤트 수: 100
JDBC batch size: 2,000
Kafka max.poll.records: 1,000
Hikari maximum pool size: 10
Producer batch.size: 65,536
Producer linger.ms: 10
Producer compression.type: lz4
```

비교 조합:

| case | partition | concurrency | 목적 |
| --- | ---: | ---: | --- |
| p6-c3 | 6 | 3 | 기존 기준점 |
| p6-c6 | 6 | 6 | partition 수만큼 consumer 사용 |
| p12-c6 | 12 | 6 | partition만 늘렸을 때 |
| p12-c12 | 12 | 12 | partition과 consumer 모두 확장 |

각 case는 topic과 consumer group을 분리했다.

```text
sfep.sensor-events.p6-c3
sfep.sensor-events.p6-c6
sfep.sensor-events.p12-c6
sfep.sensor-events.p12-c12
```

기존 topic offset이나 partition 상태가 섞이면 측정이 왜곡될 수 있기 때문에 case마다 독립 topic을 사용했다.

### 측정 결과

| case | partition | concurrency | publishMs | processMs | totalMs | saveEPS | maxLag | alerts | alertAvg | alertP95 | alertP99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| p6-c3 | 6 | 3 | 1,553 | 2,904 | 4,457 | 34,435 | 88,729 | 9,819 | 1,090 | 1,527 | 1,567 |
| p6-c6 | 6 | 6 | 1,087 | 3,645 | 4,732 | 27,435 | 74,500 | 9,939 | 599 | 1,074 | 1,097 |
| p12-c6 | 12 | 6 | 1,407 | 3,857 | 5,264 | 25,927 | 81,564 | 10,091 | 852 | 1,427 | 1,536 |
| p12-c12 | 12 | 12 | 1,732 | 4,597 | 6,330 | 21,753 | 84,399 | 10,018 | 1,090 | 1,773 | 1,795 |

### 결과 해석

저장 처리량은 `p6-c3`이 가장 좋았다.

```text
p6-c3 saveEPS = 34,435 events/sec
```

알림 지연은 `p6-c6`이 가장 좋았다.

```text
p6-c6 alertP95 = 1,074ms
```

반면 `p12-c12`는 가장 좋지 않았다.

```text
p12-c12 saveEPS = 21,753 events/sec
p12-c12 alertP95 = 1,773ms
```

이 결과는 매우 중요하다.

Kafka partition과 Consumer concurrency를 무조건 늘린다고 성능이 좋아지지 않는다.

Consumer 수가 늘어나면 Kafka 메시지는 더 많이 가져올 수 있지만, 최종 저장소인 PostgreSQL에 동시에 batch write가 몰린다. DB write 경합이 커지면 전체 처리량은 오히려 떨어질 수 있다.

### 현재 병목 정의

```text
Kafka 병렬성 부족은 일부 해결됨
하지만 concurrency를 더 늘리면 PostgreSQL batch write 경합이 커짐
현재 핵심 병목은 Consumer 병렬성 자체가 아니라 DB write 처리량과 동시성 조합
```

---

## 9. 성능 개선 흐름 요약

가장 중요한 성능 변화만 정리하면 다음과 같다.

| 구분 | 기준 | 결과 |
| --- | --- | --- |
| 초기 Direct DB Write | 10,000건 | 11,416ms, 876 events/sec |
| JDBC Batch 적용 후 | 10,000건 | 380ms, 26,373 events/sec |
| JDBC Batch 적용 후 | 100,000건 | 3,984ms, 25,159 events/sec |
| Kafka Stage 1 | 100,000건 | total 5,427ms, saveEPS 23,202, maxLag 83,735 |
| Kafka Stage 2 | 100,000건 | total 2,112ms, saveEPS 87,184 |
| Alert 분리 후 Kafka | 100,000건 | alertP95 1,926ms |
| Partition/Concurrency 최종 비교 | 100,000건 p6-c3 | total 4,457ms, saveEPS 34,435 |
| Partition/Concurrency 최종 비교 | 100,000건 p6-c6 | alertP95 1,074ms |

측정값은 단계별 코드와 조건이 달라진 상태에서 측정한 결과다.

따라서 숫자만 단순 비교하기보다, 각 단계에서 어떤 병목을 해결했는지 중심으로 해석해야 한다.

핵심 흐름은 다음과 같다.

```text
단건 DB 저장 병목
-> JDBC Batch Insert로 해결

API 요청과 DB 저장 결합
-> Kafka로 분리

Kafka 단일 Consumer 병목
-> partition/concurrency 확장으로 일부 해결

DB 저장 후 알림 지연
-> 저장 Consumer / 알림 Consumer 분리로 개선

무작정 병렬화 시 성능 저하
-> PostgreSQL write 경합을 다음 병목으로 정의
```

---

## 10. 현재까지 확인한 병목

### 해결한 병목

| 병목 | 해결 방법 |
| --- | --- |
| 이벤트 1건마다 DB insert 반복 | JDBC Batch Insert |
| 이벤트마다 equipment update 반복 | equipmentId 기준 최신 상태 집계 후 upsert |
| API 요청이 DB 저장 완료까지 대기 | Kafka publish 후 Consumer 저장 구조 |
| 단일 Consumer 저장 병목 | Kafka partition / Consumer concurrency 확장 |
| DB 저장 지연이 알림까지 지연 | Storage Consumer와 Alert Consumer 분리 |
| 알림 지연을 감으로만 판단 | alertAvg / alertP95 / alertP99 지표 추가 |

### 현재 남은 병목

| 우선순위 | 병목 | 근거 | 다음 개선 |
| ---: | --- | --- | --- |
| 1 | PostgreSQL batch write 경합 | p12-c12에서 오히려 saveEPS 감소 | Hikari pool, batch size, index 비용 검토 |
| 2 | Alert Consumer가 raw event 전체를 읽음 | 위험 이벤트는 일부지만 전체 이벤트를 모두 판단 | alert-events 전용 topic 검토 |
| 3 | 이벤트 이력 조회 최적화 미완료 | 저장 위주 측정은 완료, 조회 p95/p99 미측정 | PostgreSQL index / partitioning |
| 4 | 완전한 E2E 알림 지연 미측정 | 현재는 서버 발행 시각까지만 측정 | client ACK 기반 end-to-end latency |
| 5 | 장애 복구 실측 미완료 | Retry/DLT 구조는 준비, 장애 시나리오 실측 필요 | DB 장애 주입, DLT 재처리 |

---

## 11. 지금까지 만든 문서

| 파일 | 내용 | git 포함 여부 |
| --- | --- | --- |
| `README.md` | 프로젝트 소개, 실행 방법, 현재 단계 요약 | 포함 |
| `docs/README.md` | 문서 목차 | 포함 |
| `docs/performance-experiment.md` | 전체 성능 개선 계획과 주요 결과 | 포함 |
| `docs/performance-check-template.md` | 앞으로 성능 측정 시 사용할 템플릿 | 포함 |
| `docs/SFEP_STEP01.md` | Direct DB Write 기준선 설명 | 제외 |
| `docs/SFEP_STEP02.md` | JDBC Batch Insert 설명 | 제외 |
| `docs/SFEP_STEP03.md` | Kafka 도입 설명 | 제외 |
| `docs/SFEP_STEP04.md` | 알림 Consumer 분리, Retry/DLT 설명 | 제외 |
| `docs/SFEP_STEP05.md` | 알림 지연 지표와 Producer 튜닝 설명 | 제외 |
| `docs/SFEP_STEP06.md` | partition/concurrency 비교 설명 | 제외 |
| `docs/SFEP_FINAL_SUMMARY.md` | 지금까지 전체 최종 요약 | 포함 |

`docs/SFEP_STEP*.md`는 로컬 학습용 문서이므로 `.gitignore`에서 제외했다.

```gitignore
/docs/SFEP_STEP*.md
```

---

## 12. 실행 방법

인프라 실행:

```bash
cd ~/Desktop/SFEP
docker compose up -d
```

서버 실행:

```bash
cd ~/Desktop/SFEP/sfep-server
./gradlew bootRun
```

GUI 없이 실행:

```bash
SFEP_DESKTOP_ENABLED=false ./gradlew bootRun
```

기본 벤치마크:

```bash
node scripts/benchmark-sfep.mjs
```

설정 조합 벤치마크:

```bash
scripts/run-performance-matrix.sh
```

Kafka partition/concurrency 벤치마크:

```bash
scripts/run-partition-concurrency-matrix.sh
```

---

## 13. 포트폴리오에 쓸 수 있는 핵심 문장

### 프로젝트 설명

제조 설비에서 발생하는 대규모 이벤트를 실시간으로 수집하고, 위험 이벤트를 감지하여 운영자에게 알림을 제공하는 대규모 이벤트 처리 시스템을 개발했습니다. 단순 CRUD 구현에 그치지 않고 Direct DB Write 구조부터 Kafka 기반 비동기 처리, Consumer 병렬화, JDBC Batch Insert, 실시간 알림 분리까지 단계적으로 적용하며 병목을 수치로 검증했습니다.

### 성능 개선 설명

초기 Direct DB Write 구조에서는 10,000건 이벤트 저장에 평균 11,416ms가 소요되어 처리율이 약 876 events/sec에 그쳤습니다. 이를 개선하기 위해 JPA 단건 저장을 제거하고 JdbcTemplate 기반 Batch Insert와 설비 최신 상태 upsert를 적용하여 10,000건 저장 시간을 380ms로 단축했고, 처리율을 약 26,373 events/sec까지 높였습니다.

### Kafka 도입 설명

DB 저장 부하가 API 요청 흐름에 직접 영향을 주는 문제를 해결하기 위해 Kafka를 도입했습니다. API 서버는 이벤트를 Kafka에 발행한 뒤 빠르게 응답하고, Consumer가 PostgreSQL 저장을 비동기로 처리하도록 구조를 분리했습니다. 이를 통해 대량 이벤트 유입 시 Kafka Lag를 관측하며 처리 지연을 수치화할 수 있는 기반을 만들었습니다.

### 알림 분리 설명

위험 이벤트 알림이 DB 저장 지연의 영향을 받는 문제를 해결하기 위해 Storage Consumer와 Alert Consumer를 서로 다른 Consumer Group으로 분리했습니다. 그 결과 100,000건 이벤트 처리 실험에서 Direct 방식 대비 Kafka 방식의 알림 p95 지연시간을 6,959ms에서 1,926ms로 약 72% 줄일 수 있었습니다.

### 병목 분석 설명

Kafka partition과 Consumer concurrency를 6/3, 6/6, 12/6, 12/12 조합으로 비교한 결과, 병렬성을 무조건 높이면 성능이 좋아지는 것이 아니라는 점을 확인했습니다. 12 partitions / 12 concurrency 조합에서는 저장 처리량이 오히려 감소했으며, 이를 통해 현재 병목이 Kafka 병렬성 부족이 아니라 PostgreSQL batch write 경합으로 이동했음을 정의했습니다.

---

## 14. 다음 단계

현재까지는 저장 처리량과 알림 지연을 중심으로 개선했다.

다음 단계에서는 조회 성능과 장애 대응을 검증하는 것이 좋다.

### 14.1 PostgreSQL Index / Partitioning

이벤트 데이터가 계속 누적되면 저장 성능뿐 아니라 이력 조회 성능이 중요해진다.

검증할 것:

- `equipment_id + occurred_at` 복합 index
- `severity + occurred_at` 복합 index
- 시간 기준 partitioning
- `EXPLAIN ANALYZE` 기반 query plan 비교
- 조회 API p50/p95/p99 측정

---

### 14.2 Alert Events Topic 분리

현재 Alert Consumer는 raw event 전체를 읽고 위험 이벤트만 골라낸다.

개선 후보:

```text
raw-events
-> Risk Consumer
-> alert-events
-> Alert Delivery Consumer
```

이렇게 하면 실제 알림 대상 이벤트만 알림 전달 경로로 흐르게 만들 수 있다.

---

### 14.3 End-to-End 알림 지연 측정

현재 알림 지연은 서버의 `AlertEventBus.publish()` 시점까지 측정한다.

더 정확한 운영 지표를 만들려면 다음 구조가 필요하다.

```text
서버 알림 발행
-> SSE/WebSocket 전송
-> UI 수신
-> 클라이언트 ACK API 호출
-> 서버가 최종 도착 시간 기록
```

---

### 14.4 장애 상황 검증

Retry / DLT 설정은 준비했지만, 실제 장애 시나리오 실험은 별도로 해야 한다.

검증할 것:

- PostgreSQL 중단
- Consumer retry 동작
- DLT 이동 여부
- DB 복구 후 재처리
- 유실 이벤트 수
- 복구 시간

---

## 15. 최종 결론

SFEP 프로젝트는 단순히 실시간 데이터를 저장하는 프로젝트가 아니다.

처음에는 가장 단순한 DB 직접 저장 구조로 시작했고, 실제 성능을 측정하면서 병목을 하나씩 확인했다.

이후 JDBC Batch Insert, Kafka 비동기 처리, Consumer 병렬화, 알림 Consumer 분리, 알림 지연 측정, partition/concurrency 조합 비교를 단계적으로 적용했다.

그 과정에서 다음을 확인했다.

- 단건 DB 저장은 대량 이벤트 환경에서 가장 먼저 병목이 된다.
- Batch Insert와 upsert만으로도 저장 성능은 크게 개선된다.
- Kafka는 단순히 전체 처리 시간을 줄이는 도구가 아니라, API 요청 흐름과 DB write 부하를 분리하는 구조적 개선이다.
- 저장 Consumer와 알림 Consumer를 분리하면 위험 알림 지연을 크게 줄일 수 있다.
- Kafka 병렬성을 무작정 높이면 PostgreSQL write 경합으로 오히려 성능이 떨어질 수 있다.
- 성능 개선은 "무엇을 도입했다"보다 "어떤 병목을 수치로 확인하고 어떤 구조로 해결했는가"가 중요하다.

현재 SFEP는 다음 문장으로 정리할 수 있다.

> 대규모 제조 설비 이벤트 환경을 가정하여 Direct DB Write 구조의 한계를 측정하고, Kafka 기반 비동기 처리, JDBC Batch Insert, Consumer 분리, 알림 지연 측정, 병렬성 조합 비교를 통해 저장 성능과 운영 알림 안정성을 단계적으로 개선한 대규모 이벤트 처리 시스템입니다.
