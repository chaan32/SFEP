# Performance Check Template

성능 개선을 진행할 때는 매번 같은 기준으로 측정한다. 단순히 "빨라졌다"가 아니라, 어떤 병목을 가정했고 어떤 변경으로 어느 지표가 얼마나 개선됐는지 남기는 것이 목적이다.

## 1. 측정 목적

### 확인할 문제

- 예: Direct DB Write 구조에서 이벤트 수 증가 시 저장 시간이 선형 증가하는지 확인

### 개선 가설

- 예: 이벤트를 JDBC Batch Insert로 저장하면 DB round-trip이 줄어 처리량이 증가할 것이다.

### 성공 기준

- 예: 10,000 events 저장 시간이 1초 이하
- 예: 처리율 10,000 events/sec 이상
- 예: p95 latency 300ms 이하

## 2. 측정 환경

| 항목 | 값 |
| --- | --- |
| 측정 일시 | YYYY-MM-DD HH:mm |
| Git commit | commit hash |
| 실행 모드 | local / docker / EC2 |
| 서버 | Spring Boot, port |
| DB | PostgreSQL version |
| Kafka | enabled / disabled |
| Redis | enabled / disabled |
| JVM | Java version |
| 주요 설정 | batch size, connection pool, topic partition 등 |

## 3. 테스트 시나리오

| 케이스 | 설비 수 | 설비당 이벤트 수 | 총 이벤트 수 | 장애 비율 | 반복 횟수 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Case 1 | 100 | 1 | 100 | 2% | 3 |
| Case 2 | 100 | 10 | 1,000 | 2% | 3 |
| Case 3 | 1,000 | 10 | 10,000 | 2% | 3 |
| Case 4 | 1,000 | 50 | 50,000 | 2% | 3 |
| Case 5 | 1,000 | 100 | 100,000 | 2% | 3 |

## 4. 측정 명령

### 서버 실행

```bash
cd ~/Desktop/SFEP/sfep-server
SFEP_DESKTOP_ENABLED=false SERVER_PORT=18080 ./gradlew bootRun
```

### Direct DB Write 측정

```bash
curl -X POST http://127.0.0.1:18080/api/v1/simulator/direct/run \
  -H "Content-Type: application/json" \
  -d '{"equipmentCount":1000,"eventsPerEquipment":10,"failureRatePercent":2}'
```

### Kafka Publish + Consumer 측정

```bash
curl -X POST http://127.0.0.1:18080/api/v1/simulator/kafka/run \
  -H "Content-Type: application/json" \
  -d '{"equipmentCount":1000,"eventsPerEquipment":100,"failureRatePercent":2}'

curl http://127.0.0.1:18080/api/v1/simulator/kafka/runs/{runId}
```

### 지속 유입 측정

```text
Swing Monitor -> 지속 유입 모니터링 설정
- 설비 수
- 설비당 초당 이벤트 수
- 기간(분)
- CRITICAL 비율
```

## 5. 측정 결과

| 케이스 | 총 이벤트 수 | 발행 시간 | 저장 완료 시간 | 전체 완료 시간 | 처리율 | 최대 Lag | 최종 Lag | 특이사항 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Case 1 | 100 | - | - | - | - | - | - | - |
| Case 2 | 1,000 | - | - | - | - | - | - | - |
| Case 3 | 10,000 | - | - | - | - | - | - | - |
| Case 4 | 50,000 | - | - | - | - | - | - | - |
| Case 5 | 100,000 | - | - | - | - | - | - | - |

## 6. AS-IS / TO-BE 비교

| 항목 | AS-IS | TO-BE | 개선 효과 |
| --- | ---: | ---: | ---: |
| 10,000 events 저장 시간 | - | - | - |
| 10,000 events 처리율 | - | - | - |
| 100,000 events 전체 완료 시간 | - | - | - |
| 100,000 events 처리율 | - | - | - |
| 100,000 events 최대 Lag | - | - | - |

## 7. 병목 분석

### 확인된 병목

- 예: 이벤트마다 JPA save 반복
- 예: 설비 최신 상태 갱신을 이벤트 수만큼 반복
- 예: API 요청이 DB 저장 완료까지 대기

### 근거

- 예: 이벤트 수 증가에 따라 저장 시간이 선형 증가
- 예: DB insert count와 equipment update count가 이벤트 수에 비례

## 8. 개선 결정

### 적용할 개선

- 예: JDBC Batch Insert
- 예: Kafka 기반 수집/저장 분리
- 예: Consumer Group 병렬화
- 예: Redis 최신 상태 캐싱

### 적용하지 않는 개선과 이유

- 예: 지금 단계에서는 Kubernetes보다 단일 Docker Compose 환경에서 병목을 먼저 확인한다.

## 9. 포트폴리오 작성 문장

### AS-IS

초기 구조에서는 ...

### TO-BE

이를 해결하기 위해 ...

### 수치적 효과

그 결과 ...

### 배운 점

이 실험을 통해 ...
