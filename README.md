# SFEP - Large-scale Event Processing System

SFEP는 제조 설비에서 발생하는 대규모 이벤트를 수집, 저장, 알림 처리하고 단계별 성능 개선 실험을 하기 위한 포트폴리오용 백엔드 프로젝트입니다.

처음부터 완성형 구조를 만드는 것이 아니라, `Direct DB Write -> Kafka -> Consumer 병렬화 -> Batch Insert -> 알림 분리 -> partition/concurrency 비교` 순서로 병목을 확인하고 개선 수치를 남기는 방식으로 진행합니다.

## Current Stage

`Stage 6 - Kafka Partition / Consumer Concurrency 비교 완료`

- Direct DB Write 기준선 측정
- JDBC Batch Insert와 최신 설비 상태 upsert 적용
- Kafka 기반 수집/저장 분리
- Storage Consumer와 Alert Consumer 분리
- SSE 기반 실시간 알림 endpoint 제공
- Swing 기반 이벤트 처리/알림 모니터 제공
- 100,000건 기준 Kafka partition/concurrency 조합 비교

현재까지의 핵심 결론:

- 50,000건 기준 Direct DB Write 처리 시간이 `53,611ms -> 1,849ms`로 단축됨
- 저장 처리율이 `933 events/sec -> 27,056 events/sec`로 향상됨
- Kafka Consumer 병렬화 후 약 `50,000 events/sec` 수준의 처리량을 검증함
- 100,000건 기준 Alert Consumer 분리 후 p95 알림 지연이 `6,959ms -> 1,926ms`로 감소함
- `12 partitions / 12 concurrency`는 오히려 저장 처리량이 감소하여 PostgreSQL write 경합 가능성을 확인함
- 다음 단계는 PostgreSQL index/partitioning과 조회 성능 최적화

## Tech Stack

- Java 21
- Spring Boot 3.5
- Spring Data JPA
- PostgreSQL
- Redis
- Kafka
- Docker Compose
- Prometheus
- Grafana

## Architecture

```text
Factory Simulator
-> Spring API
-> Kafka
-> Storage Consumer -> PostgreSQL
-> Alert Consumer   -> SSE / Swing Alert Monitor

PostgreSQL -> Dashboard Summary API
Actuator   -> Prometheus -> Grafana
```

## Run

```bash
cd ~/Desktop/SFEP
docker compose up -d

cd sfep-server
./gradlew bootRun
```

로컬 GUI 환경에서는 서버 실행 후 `SFEP Direct DB Write Monitor` Swing 창이 함께 열린다.

Swing 창에서 다음 값을 입력할 수 있다.

- 설비 수
- 설비당 이벤트 수
- 장애 비율

`보내기` 버튼을 누르면 이벤트가 PostgreSQL에 직접 저장되고, 창에서 다음 값을 실시간으로 확인할 수 있다.

- 저장 이벤트 수
- 진행률
- 소요 시간
- 초당 처리율
- 전체 설비 수
- 최근 1분 이벤트 수
- WARNING / CRITICAL 이벤트 수

GUI 없는 서버나 Docker 환경에서 실행할 때는 다음 옵션으로 데스크톱 창을 끈다.

```bash
SFEP_DESKTOP_ENABLED=false ./gradlew bootRun
```

## Benchmark

기본 성능 측정:

```bash
node scripts/benchmark-sfep.mjs
```

DB batch, Kafka poll, Hikari pool 조합 측정:

```bash
scripts/run-performance-matrix.sh
```

Kafka partition/concurrency 조합 측정:

```bash
scripts/run-partition-concurrency-matrix.sh
```

## API

### Run Direct Write Simulator

```bash
curl -X POST http://localhost:8080/api/v1/simulator/direct/run \
  -H "Content-Type: application/json" \
  -d '{
    "equipmentCount": 100,
    "eventsPerEquipment": 10,
    "failureRatePercent": 2
  }'
```

### Dashboard Summary

```bash
curl http://localhost:8080/api/v1/dashboard/summary
```

### Single Event Ingest

```bash
curl -X POST http://localhost:8080/api/v1/events/direct \
  -H "Content-Type: application/json" \
  -d '{
    "eventId": "manual-001",
    "equipmentId": "EQ-0001",
    "equipmentType": "CONVEYOR",
    "status": "RUNNING",
    "temperature": 62.5,
    "vibration": 2.1,
    "rpm": 1200,
    "power": 4.3,
    "currentValue": 35.0
  }'
```

## Monitoring

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3001
- Spring Actuator: http://localhost:8080/actuator
- Prometheus Metrics: http://localhost:8080/actuator/prometheus

## Docs

- [Final Project Summary](docs/SFEP_FINAL_SUMMARY.md)
- [Portfolio Notion Template](docs/SFEP_PORTFOLIO_NOTION.md)
- [Performance Experiment Plan](docs/performance-experiment.md)
- [Performance Check Template](docs/performance-check-template.md)
- [Docs Index](docs/README.md)

`docs/SFEP_STEP*.md` 파일은 단계별 학습 노트이며 `.gitignore`에 포함되어 있습니다. 포트폴리오 작성용으로 로컬에만 유지합니다.
