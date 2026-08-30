# SFEP Java 실행 엔진·Swing 화면 최적화 설계

- 작성일: 2026-08-30
- 최종 수정일: 2026-08-31
- 상태: v1/v2 Bundle 호환·최적화 설계 대화 승인 완료, 구현 전 사용자 문서 검토 대기
- 대상 저장소: `/Users/haechan/Desktop/SFEP`
- 대상 모듈: `equipment-monitor`
- 원격 저장소 정책: 로컬 커밋만 수행하고 자동 Push하지 않음

## 1. 목적

현재 SFEP 장비 품질 모니터는 봉인된 과거 Bundle을 검증한 뒤 공정 이벤트를 순차 재생하고, 규칙 기반 품질위험과 역사적 조업범위 편차를 Swing 화면에 표시한다. 판정 기능은 정상 동작하지만 실제 Bundle에서 반복 규칙 탐색, 누적 결과 재계산, 전체 이력의 Swing 행 변환 때문에 실행 시간과 UI 응답성이 불필요하게 저하된다.

이번 변경의 목적은 다음과 같다.

1. 기존 v1 Bundle과 고정 통계 seed를 사용하는 새 v2 Bundle을 모두 실패 폐쇄 방식으로 검증한다.
2. 각 Bundle 안의 판정 의미, 규칙 ID, 근거와 집계 건수를 유지하면서 Java 처리량을 높인다.
3. 전체 이력을 보존하면서 화면에는 필요한 부분만 투영해 메모리와 EDT 작업량을 줄인다.
4. 모든 시간 기반 목록과 내보내기를 최신순으로 통일한다.
5. 로딩·검색·갱신 중에도 Swing이 응답하도록 만든다.
6. 최적화 전후 정확성과 성능을 재현 가능한 수치로 증명한다.

## 2. 변경 불가 계약

다음 항목은 최적화가 변경할 수 없다.

- 승인된 실제·golden v1 Bundle 및 `contracts/equipment-monitor/v1`의 파일 바이트, SHA-256, `bundleId`, `criteriaId`
- v1과 v2 각각의 manifest version, role별 artifact version과 embedded schema digest 조합
- 규칙의 적용 가능성, 규칙 우선순위, 판정 등급, 근거와 집계 건수
- 이벤트의 정규 순서와 전체 이력의 내용
- Bundle 경로·심볼릭 링크·스키마·ID·artifact digest 검증과 실패 폐쇄 원칙
- 전체 CSV 내보내기 가능성
- 과거 재생 모니터라는 제품 범위

새 v2 Bundle은 정직한 producer provenance 때문에 새 `criteriaId`와 `bundleId`를 가진다. 따라서 alert key와 화면 metadata의 bundle binding 값은 달라질 수 있지만, 이를 정규화한 rule grade·evidence·count·history·alert 의미는 v1과 같아야 한다.

사용자가 추가 승인한 표시 계약은 다음과 같다.

- 이력·경보·검색 결과·CSV 내보내기 등 시간 기반 결과는 최신 항목이 맨 위다.
- 같은 시간 버킷 안에서는 나중에 처리된 정규 replay ordinal이 먼저 온다.
- 사용자가 맨 위를 보고 있으면 새 항목을 즉시 표시한다.
- 사용자가 과거 위치를 보고 있으면 위치를 강제로 바꾸지 않고 `새 데이터 N건` 동작을 제공한다.
- 새 정렬은 표시 투영에만 적용하며 Bundle의 정규 이벤트 순서는 바꾸지 않는다.

## 3. 비대상

- v1 schema·golden Bundle 수정 또는 삭제
- 허용 목록 밖의 Bundle version이나 Bundle 내부 schema를 동적으로 신뢰하는 기능
- `sfep-criteria-id/v1`·`sfep-bundle-id/v1` 계산식 변경
- 검증·해시 계산 제거
- 판정 임계치, 통계식 또는 설비 의미 변경
- 전체 이력 삭제, 임의 상한 적용 또는 근거 축소
- 데이터베이스·웹 UI·Python 서비스 도입
- 실제 PLC·센서 연결 또는 온라인 예측 기능
- 원격 저장소 Push

## 4. 확인된 기준 성능과 병목

동일한 로컬 실제 Bundle에서 확인한 기준은 다음과 같다.

| 경로 | 기준 시간 |
|---|---:|
| 실제 Bundle 로딩 계약 테스트 | 약 6.944초 |
| 실제 역사 재생 테스트 | 약 15.553초 |
| 실제 데스크톱 UI 테스트 | 약 24.242초 |

실제 UI 투영 규모는 개요 23,631건, 이력 54,515건, 근거 최대 2,000건이다. 주요 병목은 다음과 같다.

1. 소재 이벤트가 들어올 때마다 이전 assessment와 rule을 다시 순회하는 누적 집계
2. 약 165개 품질규칙을 매 이벤트마다 전부 확인하는 적용 가능성 탐색
3. 424개 실제 운전범위를 매 이벤트마다 재분류하는 탐색
4. 같은 이벤트의 `values_json`을 여러 번 해석·복사하는 흐름
5. 각 설비 단위 snapshot 갱신 때 전체 map을 다시 복사·정렬하는 흐름
6. Swing EDT에서 54,515개 이력행 생성·삽입·필터링·정렬
7. 23,631개 소재를 일반 `JComboBox`에 모두 적재하는 필터
8. 화면 갱신 때 요약 카드가 전체 소재를 반복 순회하는 계산

## 5. 선택한 구조

```text
봉인 Bundle
    │
    ├─ 안전한 manifest version 추출
    ▼
불변 ContractProfile 선택
├─ v1: 기존 config·summary·manifest
└─ v2: seed-aware config·summary·manifest
    │
    ├─ profile별 schema digest·artifact version·ID 검증
    ▼
불변 Runtime Index
├─ QualityRuleIndex
└─ OperatingRangeIndex
    │
    ▼
ReplayCursor → EventValueSnapshot → 판정기
                                      │
                                      ▼
                         증분 Monitor Accumulator
                                      │
                        ┌─────────────┴─────────────┐
                        ▼                           ▼
                  전체 정규 상태              Desktop Projection
                                            ├─ 최신순 page
                                            ├─ 비동기 filter
                                            └─ 전체 최신순 export
```

Bundle DTO와 판정 결과 DTO는 기존 공개 계약을 유지한다. 새 `BundleContract`는 loader 내부에서만 version별 신뢰 기준을 제공하고, 나머지 새 구성요소는 반복 탐색과 Swing 투영을 담당한다.

## 6. Java 실행 엔진 설계

### 6.1 v1/v2 BundleContract loader

기존 `contracts/equipment-monitor/v1`과 그 embedded bytes는 수정하지 않는다. `contracts/equipment-monitor/v2`에는 seed-aware config·summary·manifest schema를 추가하고, producer runtime·ranges·rules·replay는 신뢰된 v1 schema를 재사용한다. v2 artifact version 조합은 다음과 같이 고정한다.

| artifact | v2 profile의 schema version |
|---|---|
| analysis config | `sfep-analysis-config/v2` |
| producer runtime | `sfep-producer-runtime/v1` |
| operating ranges | `sfep-operating-ranges/v1` |
| quality rules | `sfep-quality-rules/v1` |
| replay events | `sfep-replay-events/v1` |
| analysis summary | `sfep-analysis-summary/v2` |

`BundleLoader`는 안전한 layout preflight와 strict JSON parse 후 untrusted manifest의 `schemaVersion`을 정확한 allowlist로만 분기한다. 선택된 불변 `BundleContract`가 manifest schema bytes, role별 schema bytes·digest, artifact version과 identity namespace를 제공한다. 다음 순서를 지킨다.

1. manifest version이 `sfep-equipment-bundle/v1` 또는 `/v2`인지 확인한다.
2. 선택된 profile의 embedded manifest schema digest가 manifest identity claim과 같은지 확인한다.
3. 모든 role의 schema digest와 artifact version 조합을 profile과 비교한다.
4. 선택된 embedded schema로 Manifest와 각 artifact를 검증한다.
5. 기존 여덟 필드 criteria ID와 열아홉 필드 bundle ID를 재계산하고 identity binding을 확인한다.
6. 모든 artifact의 크기와 hash를 attestation한 뒤에만 Manifest·artifact schema validation과 DTO binding을 수행한다.

검증 순서는 `layout preflight → strict manifest parse → allowlist profile 선택 → profile schema digest·descriptor version·identity 확인 → 모든 artifact size/hash attestation → manifest/artifact schema validation·DTO binding`이다. Bundle이 제공하는 schema를 읽거나, 한 profile의 검증 실패를 다른 profile로 fallback하지 않는다. config의 bootstrap은 DTO에서 이미 `JsonNode`이므로 Swing과 domain DTO에는 seed field를 추가하지 않아도 된다.

각 `BundleContract`는 일곱 role 전체의 `role → embedded schema document` map을 직접 소유한다. v2에서 재사용하는 v1 schema도 profile map에 명시적으로 넣으며, 전역 `SchemaRole.fileName()`으로 암묵 추론하지 않는다.

### 6.2 불변 품질규칙 색인

`QualityRuleEvaluator`가 생성될 때 봉인된 규칙 순서를 보존한 불변 색인을 한 번 만든다. 1차 key는 `firstAvailableStage`, `equipmentType`, `applicationScope`이고 설비 전용 규칙은 `equipmentId`로 한 번 더 나눈다. 이벤트 판정 시에는 가능한 bucket만 결합한다.

결합된 후보의 최종 순서는 원래 봉인 배열 ordinal로 안정 정렬한다. 따라서 탐색량은 줄지만 최초 일치 규칙, 표시 병합 순서와 근거 순서는 변하지 않는다. 중복 rule ID, 알 수 없는 scope 또는 색인 불변식 위반은 로딩 단계에서 실패한다.

### 6.3 불변 운전범위 색인

`OperatingRangeEvaluator`는 field, `firstAvailableStage`, `equipmentType`, `equipmentId`, context level 기준의 후보 색인을 생성한다. 기존의 context 완화 순서를 metadata로 보존한다. 같은 context level 후보의 최종 선택은 기존 계약대로 `ruleId` 오름차순이며 source/range ordinal로 바꾸지 않는다. 실제 판정은 기존 비교 함수 하나만 사용하고, 색인은 후보 축소에만 관여한다.

### 6.4 이벤트 값 1회 snapshot

`HistoricalMonitor.process` 입구에서 이벤트 값을 불변 snapshot으로 한 번 얻는다. 품질규칙 판정과 운전범위 판정은 같은 snapshot을 읽는다. 호출자에게 수정 가능한 map을 노출하지 않으며 null, 숫자 타입과 문자열 비교 의미는 기존과 동일하다.

### 6.5 증분 소재·설비 집계

소재별 assessment 목록은 근거와 상세보기를 위해 모두 보존한다. 단, grade별 건수와 최종 severity는 새 assessment가 추가될 때 갱신한다. 화면 snapshot 생성 시 전체 assessment를 다시 계산하지 않는다.

설비 단위 상태도 재생 중에는 내부 mutable builder에 누적하고 외부 공개 시점에만 기존 불변 DTO로 고정한다. 내부 객체가 Swing이나 외부 코드로 누출되지 않도록 한다.

### 6.6 반복 검증은 프로파일 후 판단

Bundle 로더, replay validator와 cursor에 존재하는 파일 재읽기·재해시는 보안 및 TOCTOU 방어와 연결되어 있다. 프로파일 없이 제거하지 않는다. 하나의 인증된 file descriptor로 동일 검증을 합칠 수 있고 공격면이 늘지 않는다는 테스트가 있을 때만 별도 단계에서 변경한다. 이 조건을 충족하지 못하면 현재 검증을 유지한다.

## 7. Swing 화면 설계

### 7.1 전체 상태와 화면 page 분리

전체 이력은 domain 상태가 보존한다. `HistoryProjection`은 필터 조건, 정렬 기준, page offset과 page size를 받아 화면에 필요한 불변 row만 만든다. 기본 page size는 200건이고 선택 가능한 범위는 50~500건으로 제한한다. page 제한은 화면 객체 수에만 적용되며 전체 검색·집계·내보내기에는 적용하지 않는다.

### 7.2 최신순 계약

`ReplayUnit.events()`는 의미상 순서를 주장하지 않으므로 event list index나 현재 alert insertion order를 최신순 근거로 사용하지 않는다. `ReplayCursor`가 검증된 canonical CSV row를 읽을 때 0부터 증가하는 내부 `replayOrdinal`을 붙이고, cursor→monitor→history projection 경계의 내부 sequenced record가 이를 보존한다. 이 값은 표시·내보내기 정렬 전용이며 동일 unit 안의 처리 순서나 판정 결과에는 영향을 주지 않는다.

모든 시간 기반 투영은 `replayOrdinal` 내림차순 comparator를 공통으로 사용한다. 한 event에서 여러 alert가 생기면 기존 안정 alert key 오름차순으로 tie를 끊는다. 날짜·시간 문자열이나 hash인 event ID만으로 순서를 추정하지 않는다. overview의 동률 항목은 마지막 변경 ordinal 내림차순, 기존 안정 식별자 오름차순으로 정렬한다. `AlertHistory`는 alert와 ordinal을 묶은 내부 history record를 저장하되 공개 `HistoricalAlert`와 CSV column 계약은 유지한다.

새 이력이 들어왔을 때 viewport가 첫 행 부근이면 최신 page를 즉시 반영한다. 과거 page 또는 아래쪽을 보고 있으면 현재 selection과 viewport를 유지하고 대기 건수를 증가시킨다. 사용자가 `새 데이터 N건`을 실행하면 filter를 유지한 채 첫 page와 첫 행으로 이동한다.

### 7.3 비동기 검색과 고카디널리티 입력

23,631개 소재 전체를 combo item으로 만들지 않는다. 소재 필터는 검색 가능한 text field와 최대 20개의 typeahead 후보로 교체한다. 일반 필터는 짧은 debounce 뒤 전용 worker에서 계산한다.

각 요청은 증가하는 generation을 가진다. worker가 끝날 때 generation이 현재 값과 다르면 결과를 버린다. Swing component 변경은 EDT에서만 수행한다. 창 종료 시 worker와 timer를 취소한다.

### 7.4 EDT 시간 예산과 렌더링

한 번의 UI drain은 최대 16ms 또는 정해진 작은 작업량 중 먼저 도달하는 경계에서 양보한다. 남은 작업은 다음 EDT tick으로 넘긴다. renderer가 매 cell마다 만드는 font, border와 color 객체는 불변값으로 재사용한다. table model은 문자열 배열 전체를 복제하지 않고 현재 page의 typed row를 참조한다.

### 7.5 시작 화면과 접근성

프레임은 1초 이내에 로딩 상태로 표시하고 Bundle 로딩·재생은 백그라운드에서 수행한다. 진행 단계는 `Bundle 검증`, `이벤트 재생`, `화면 준비`로만 표시해 거짓 백분율을 만들지 않는다. 성공 전에 정상 상태를 표시하지 않는다.

검색 입력, page controls, 새 데이터 버튼과 표에는 accessible name·description과 label association을 제공한다. 주요 동작에는 mnemonic 또는 keyboard action을 제공한다.

### 7.6 내보내기

기존 `HistoricalMonitor.exportAlerts`는 현재 page가 아니라 전체 alert history의 immutable snapshot을 사용한다. 별도의 필터 내보내기 기능은 이번 범위에 추가하지 않는다. 전체 alert를 replay ordinal 내림차순으로 쓰며, 내보내기 도중 새 이벤트가 들어와도 시작 시점 snapshot 하나만 사용한다.

## 8. 오류 처리

- Bundle 검증 실패 시 Swing 정상 화면을 열지 않고 한국어 오류 요약과 안전한 상세 원인을 제공한다.
- 알 수 없는 manifest version, profile과 다른 schema digest 또는 v1/v2 artifact 혼합은 fallback 없이 거부한다.
- 손상된 replay 중간까지의 결과를 완전한 결과처럼 표시하지 않는다.
- 색인 구성 불변식 위반은 조용히 전체 scan으로 우회하지 않고 테스트 또는 시작 단계에서 실패한다.
- 비동기 filter 실패는 현재 정상 page를 유지하고 오류 상태를 표시한다.
- 종료된 화면에는 늦게 끝난 worker가 결과를 반영하지 못한다.
- export 실패는 부분 파일을 완료 파일로 남기지 않는다.

## 9. 테스트 설계

### 9.1 동일성 테스트

- 기존 실제·golden v1 Bundle이 변경된 loader에서 같은 ID와 내용으로 통과한다.
- 별도 v2 fixture와 실제 v2 Bundle이 선택된 v2 profile로 통과한다.
- manifest version, config/summary artifact version, schema digest 또는 seed를 교차 조작한 Bundle은 모두 실패한다.
- v1과 v2 실제 Bundle을 재생했을 때 bundle/criteria binding만 정규화한 rule grade, evidence, count, material history와 alert가 같다.
- 색인 경로와 기존 reference scan이 모든 fixture에서 같은 적용 규칙 순서를 반환한다.
- 같은 context level에 여러 운전범위가 있으면 indexed path와 reference scan이 모두 `ruleId` 오름차순 후보를 선택한다.
- 증분 집계와 전체 재계산 oracle이 이벤트 prefix마다 같은 상태를 반환한다.
- 같은 이벤트 snapshot을 두 판정기가 공유해도 기존 판정과 동일하다.
- 실제 Bundle에서 규칙 ID, grade, evidence, count와 전체 history 내용이 동일하다.
- 최신순 page를 이어 붙이면 canonical 전체 이력을 정확히 한 번씩 역순으로 복원한다.
- `ReplayUnit.events()` 순서를 섞어도 보존된 replay ordinal 기반 history·export 순서는 같고 판정 의미도 같다.
- 같은 시간 버킷과 한 event의 다중 alert tie, filter·page 조합도 결정론적이다.
- CSV 내보내기는 전체 건수와 내용을 보존하고 최신순이다.

### 9.2 Swing 동작 테스트

- 최신 위치에서는 새 row가 첫 행에 나타난다.
- 과거 위치에서는 viewport와 selection을 유지하고 대기 건수만 증가한다.
- `새 데이터 N건` 실행 시 첫 page로 이동한다.
- 오래된 filter worker 결과가 최신 결과를 덮어쓰지 못한다.
- page model이 최대 page size보다 많은 row 객체를 보유하지 않는다.
- UI mutation은 EDT에서만 실행한다.
- 접근성 이름과 label association이 존재한다.

### 9.3 성능 측정

동일한 장비·JDK·actual v1 Bundle에서 Java 최적화 전후를 측정해 계약 migration 효과와 섞지 않는다. 짧은 경로는 warm-up 후 3회 중앙값을 쓰고, 긴 실제 경로는 1회 cold와 2회 warm 값을 모두 기록한다. 시간과 최대 RSS를 함께 남긴다. 별도로 actual v2 Bundle의 load·replay가 v1과 같은 의미 결과를 내는지 검증한다.

| 지표 | 기준 | 완료 목표 |
|---|---:|---:|
| 실제 역사 재생 | 15.553초 | 10초 이하 |
| 실제 데스크톱 UI 구성 | 24.242초 | 15초 이하 |
| 첫 로딩 프레임 | 로딩 완료 후 | 1초 이내 표시 |
| 화면 page 객체 | 54,515개 전체 변환 | 설정 page size 이하 |
| 주요 Java 경로 최대 RSS | 구현 전 측정값 | 20% 절감 목표 |

정확성은 hard gate다. 성능 목표는 작업 우선순위와 복잡성 판단 기준이다. 개별 최적화가 동일성은 통과하지만 중앙값 10% 미만의 개선만 만들고 복잡성을 유의미하게 늘리면 그 변경은 유지하지 않는다.

### 9.4 Gradle 검증 lane

로컬 actual Bundle을 사용하는 테스트는 v1 단독 회귀와 v1↔v2 비교에 서로 다른 JUnit tag를 부여한다. 기본 `test` 계약은 유지하되 다음 보조 task를 제공한다.

- `fastTest`: actual Bundle tag를 제외한 빠른 개발 검증
- `actualBundleTest`: actual Bundle tag만 실행하며 `SFEP_ACTUAL_BUNDLE`이 없으면 skip이 아니라 명확히 실패
- `actualBundleV2Test`: `SFEP_ACTUAL_BUNDLE`과 `SFEP_ACTUAL_BUNDLE_V2`를 모두 요구해 v2 load·v1 대비 의미 동일성을 검증하며, 어느 하나라도 없으면 변수 이름을 포함해 명확히 실패
- `test`: 현재처럼 전체 test class를 대상으로 하며 실제 Bundle 환경변수가 없으면 기존 assumption에 따라 actual test만 skip

`actualBundleTest`는 v1 tag만, `actualBundleV2Test`는 v1↔v2 비교 tag만 선택해 서로를 잘못 수집하지 않는다. 실행 명령은 기존 계약인 `./sfep-server/gradlew -p equipment-monitor ...`를 유지한다. 같은 저장소에 두 번째 Gradle wrapper를 복제하지 않는다. 최종 완료 전에는 `fastTest`, 기본 `test`, actual Bundle이 설정된 `actualBundleTest`와 `actualBundleV2Test`를 모두 실행한다.

## 10. 구현 순서와 커밋 경계

1. v1 고정 회귀와 v2 profile·cross-version tamper test
2. versioned embedded schema registry와 `BundleContract` loader
3. benchmark·reference parity test 추가
4. 이벤트 값 snapshot과 증분 집계
5. 품질규칙·운전범위 불변 색인
6. cursor replay ordinal과 내부 history record
7. page 기반 최신순 history projection
8. 비동기 filter·typeahead·새 데이터 동작
9. 시작 화면·접근성·renderer 정리
10. actual v1/v2 Bundle 회귀·성능 측정과 한국어 결과 문서

공유 v2 계약과 Python·Java 양쪽 contract support는 어느 한쪽만 남아 깨지지 않도록 하나의 별도 호환성 커밋 경계로 관리한다. 이후 Java/Swing 최적화는 Python 계산 최적화와 섞지 않고 테스트가 통과하는 작은 로컬 커밋으로 남긴다.

## 11. 완료 정의

- 전체 Java 테스트와 새 동일성·UI 테스트가 통과한다.
- 기존 실제·golden v1 Bundle이 그대로 로드되고 고정 ID 검증을 통과한다.
- 새 actual v2 Bundle의 새 ID·schema digest·artifact attestation이 검증된다.
- actual v1/v2 Bundle의 판정 내용과 건수가 identity-normalized 비교에서 같다.
- 모든 시간 기반 화면과 CSV가 최신순이다.
- 전체 이력을 유지하면서 UI model 보유 row가 page 상한을 넘지 않는다.
- 실제 Swing 프로그램을 실행해 로딩, 재생, 검색, page 이동과 새 데이터 동작을 확인한다.
- 전후 시간·메모리와 미달 목표를 숨김없이 문서화한다.
- GitHub에 Push하지 않고 로컬 브랜치에만 커밋한다.
