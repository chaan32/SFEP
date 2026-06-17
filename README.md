# Smart Factory Event Monitoring Platform

SFEP는 제조 설비에서 발생하는 센서 이벤트를 수집, 저장, 조회하고 단계별 성능 개선 실험을 하기 위한 포트폴리오용 백엔드 프로젝트입니다.

초기 버전은 일부러 가장 단순한 구조인 `Spring -> PostgreSQL 직접 저장`으로 구현합니다. 이후 Kafka, Consumer 병렬화, Batch Insert, Redis 최신 상태 캐시, DB Index/Partitioning, Retry/DLQ를 순서대로 추가하면서 병목 지점과 개선 수치를 비교합니다.

## Current Stage

`Stage 0 - Direct DB Write`

- 설비 센서 이벤트를 HTTP API로 수집
- 이벤트를 PostgreSQL에 직접 저장
- 설비별 최신 상태를 `equipment` 테이블에 갱신
- 로컬 실행 시 Swing 기반 데스크톱 모니터 실행
- 시뮬레이터 API로 테스트 이벤트 생성
- 대시보드 요약 API로 현재 상태 조회
- Actuator/Prometheus 기반 메트릭 노출

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
