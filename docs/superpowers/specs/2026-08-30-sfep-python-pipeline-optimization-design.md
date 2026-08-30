# SFEP Python Bundle 생성 파이프라인 최적화 설계

- 작성일: 2026-08-30
- 최종 수정일: 2026-08-31
- 상태: v1 보존·v2 고정 통계 seed 설계 사용자 승인 완료, 구현 계획 작성 완료
- 대상 저장소: `/Users/haechan/Desktop/SFEP`
- 대상 모듈: `analysis`, `contracts/equipment-monitor/v2`
- 원격 저장소 정책: 로컬 커밋만 수행하고 자동 Push하지 않음

## 1. 목적

Python 분석기는 세 CP949 원본을 검증하고 계보를 연결한 뒤 운전범위, 품질위험 규칙, 요약과 189,043개 replay event가 포함된 봉인 Bundle을 결정론적으로 만든다. 현재 결과는 검증됐지만 동일한 조건·칼럼·Charge 표본을 여러 분석 단계와 부트스트랩 반복에서 다시 계산하고, 큰 DataFrame과 row object를 반복 생성한다.

이번 변경은 다음을 달성한다.

1. 통계식·표본·난수열·직렬화 규약을 바꾸지 않고 반복 계산을 제거한다.
2. 실제 이벤트 생성과 품질구간 테스트의 실행 시간을 줄인다.
3. 중간 DataFrame·record·sample row 복제를 줄여 최대 메모리를 낮춘다.
4. 승인된 기존 v1 Bundle과 v1 계약 파일을 한 바이트도 변경하지 않고 계속 검증할 수 있게 보존한다.
5. 최적화된 producer는 실제 코드 출처가 기록된 새 v2 Bundle과 새 `criteriaId`·`bundleId`를 만든다.
6. 새 ID가 부트스트랩 표본을 바꾸지 않도록 통계 seed를 코드 출처 ID에서 분리하고, v1과 v2의 판정 의미를 동일하게 유지한다.
7. 빠른 개발 테스트와 장시간 실제 회귀 검증을 명확히 분리한다.

## 2. 변경 불가 계약과 허용된 차이

### 2.1 그대로 보존할 계약

- 세 원본의 strict CP949 해석, 크기와 SHA-256 검증
- 계보 연결·격리·label maturity·split·charge purge 의미
- 후보 bin, 최소 support, defect count, FDR family와 등급 경계
- discovery에서 고정한 조건을 confirmation에서 재학습하지 않는 원칙
- 2,000회 Charge bootstrap, seed와 각 replicate의 Charge 추출 순서
- 통계식, population ordering, RNG sequence, null·NaN 처리와 reason code
- 결정론적 JSON·CSV 직렬화, float canonicalization과 정렬 순서
- rule/event/material/batch ID와 그 preimage
- lock, `O_NOFOLLOW`, 임시 형제 디렉터리, `fsync`, digest 검증과 원자 이동
- 승인된 실제 v1 Bundle, v1 golden Bundle과 `contracts/equipment-monitor/v1`의 모든 파일 바이트·ID
- v1과 v2의 후보 조건, 운전범위, point estimate, 부트스트랩 표본·CI, rule grade, holdout metric과 최종 경보 의미

### 2.2 v2에서 반드시 달라지는 항목

- 새 producer code tree·source·wheel을 기록한 `producer_runtime.json`
- `bootstrap.seedProtocol`·`bootstrap.seedMaterial`이 추가된 v2 `analysis_config.json`
- 새 config·runtime·schema digest를 정직하게 포함한 `criteriaId`와 `bundleId`
- artifact 안의 `criteriaId`, `bundleId` binding field와 Manifest identity·digest·size
- 부트스트랩 계보가 `identity.criteria_id` 대신 `config.bootstrap.seedMaterial`을 가리키도록 수정된 v2 summary lineage

통계 알고리즘·표본·결과는 유지하지만, bootstrap seed provenance는 기존의 암묵적 `identity.criteria_id`에서 명시적인 config 두 필드로 의도적으로 이관한다. 이 차이는 숨기거나 기존 값으로 위장하지 않는다. 그 밖의 결과는 뒤에서 정의한 identity-normalized 비교를 통과해야 한다.

## 3. 비대상

- 모델 또는 불량 확률 예측 도입
- 임계치·학습법·split·bootstrap 횟수 변경으로 속도 확보
- v1 schema·golden Bundle의 수정 또는 삭제
- criteria/bundle ID의 여덟 필드·열아홉 필드 계산식과 `sfep-criteria-id/v1`·`sfep-bundle-id/v1` namespace 변경
- v2 seed를 새 `criteriaId`에서 다시 파생하거나 producer provenance에 숨기는 방식
- 압축 형식 또는 규칙·범위·replay artifact의 의미 구조 변경
- 보안 검증 생략, 검증되지 않은 persistent cache
- canonical float 알고리즘을 근사 formatter로 교체
- 새 데이터 수집
- 원격 저장소 Push

## 4. 확인된 기준과 병목

현재 Python 전체 테스트 결과는 `1,390 passed, 9 skipped in 553.18s`다. skip은 실제 데이터 환경변수와 두 개의 독립 sealed runtime이 없는 테스트다. 실제 데이터는 별도 명시 경로에 존재하며 최종 검증에서 기존 데이터만 사용한다.

주요 시간은 다음과 같다.

| 경로 | 기준 시간 |
|---|---:|
| 실제 snapshot event 생성 테스트 | 168.63초 |
| 복수 주의 후보 승격 방지 | 28.11초 |
| Charge bootstrap 확인 경로 | 약 20.61~27.95초 |
| rich quality wire 동일성 | 12.61초 |
| 전체 테스트 | 553.18초 |

확인된 병목은 다음과 같다.

1. holdout profile마다 전체 history와 rule을 다시 순회하는 평가
2. 2,000회 bootstrap마다 선택된 Charge의 원본 row list를 다시 만드는 작업
3. discovery·confirmation·interaction이 같은 predicate와 validity mask를 반복 계산하는 작업
4. field별 요약에서 전체 DataFrame을 record dict로 반복 변환하는 작업
5. genealogy와 event builder의 깊은 DataFrame 복사, `iterrows`와 반복 scalar lookup
6. raw bytes, decoded text, records, converted records와 DataFrame이 동시에 살아 있는 구간

## 5. 선택한 구조

```text
v2 analysis config
├─ seedProtocol
└─ seedMaterial ─────────────────────┐
                                     │
인증된 원본 snapshot                 │
        │                            │
        ▼                            │
고정 칼럼 배열·행 식별자             │
        │                            │
        ├─ PredicateMaskCache ── 규칙 탐색/확인/상호작용
        ├─ ColumnSummaryCache ── profile/drift/summary
        └─ ChargeConfusionStats ── holdout/bootstrap ◀── 고정 seed
        │
        ▼
기존 결과 model
        │
        ▼
versioned canonical serializer·보안 봉인
        │
        ▼
새 v2 Bundle + 새 정직한 provenance/ID
        │
        ▼
기존 v1 Bundle과 identity-normalized 의미 비교
```

캐시는 한 번의 분석 실행 안에서만 존재한다. artifact와 ID preimage에는 포함하지 않고 실행 종료 시 사라진다.

### 5.1 v1/v2 계약 프로필

`contracts/equipment-monitor/v1`은 immutable reference다. 새 `contracts/equipment-monitor/v2`에는 실제로 shape이나 lineage가 달라지는 다음 세 schema만 둔다.

- `analysis_config.schema.json`: `sfep-analysis-config/v2`, `quality-analysis-v2`
- `analysis_summary.schema.json`: `sfep-analysis-summary/v2`
- `bundle_manifest.schema.json`: `sfep-equipment-bundle/v2`

producer runtime, operating ranges, quality rules와 replay event는 기존 v1 schema를 그대로 재사용한다. v2 manifest의 artifact version 조합은 config·summary만 v2이고 나머지는 v1이다. Python은 manifest의 허용된 계약 버전에 맞는 불변 `BundleContract`를 선택하며, 계약 간 schema digest나 artifact version을 섞은 입력은 거부한다.

현재 `analysis/analysis_config.json`은 v1 재현 입력으로 그대로 둔다. 실제 v2 생성에는 별도 `analysis/analysis_config_v2.json`을 사용하고, golden v2 fixture도 golden v1 criteria ID를 seed material로 가진 전용 v2 config를 사용한다. 따라서 v1 회귀 입력을 덮어쓰지 않는다.

criteria와 bundle identity의 필드 집합과 hash namespace는 그대로 유지한다. config bytes, config schema digest와 producer runtime bytes가 달라지므로 새 ID는 기존 계산식만으로 자연스럽게 달라진다.

### 5.2 고정 통계 seed

v2 config의 bootstrap은 다음 값을 필수로 가진다.

```json
{
  "minimumValidReplicates": 1900,
  "replicates": 2000,
  "seedMaterial": "sha256:c0a9d1f3f0d655c24d2eeddc58f1905672d2f72d7ffd0d14e87bdecb118a2a26",
  "seedProtocol": "LEGACY_CRITERIA_ID_UTF8_V1"
}
```

위 `seedMaterial`은 검증된 실제 v1 reference Manifest의 criteria ID를 provenance 주장이 아닌 통계 seed 재료로 명시한 것이다. v2 golden fixture는 검증된 golden v1 Manifest의 criteria ID인 `sha256:8c88d3109bd6945b76317ee415c7821524f515c9bd79d36b8072c285c6434af7`을 사용한다. migration test는 먼저 각 v1 Bundle의 schema·identity·artifact attestation을 전부 검증한 뒤 `v2.bootstrap.seedMaterial == verifiedV1.criteriaId`를 확인한다. 문자열 형식만 맞는 임의 seed는 reference 동등성으로 인정하지 않는다.

v2 rule bootstrap은 기존 공식의 `criteriaId` 자리에 `seedMaterial`을 넣고 기존 rule ID와 `rule-ci-v1` domain separator를 유지한다. holdout bootstrap도 같은 방식으로 `seedMaterial`과 `holdout-bootstrap-v1`을 사용한다. 따라서 row·Charge·stratum 순서와 rule ID가 같으면 모든 표본 ordinal, CI와 valid replicate가 v1과 정확히 같다. v1 실행은 계속 자신의 criteria ID를 seed 재료로 사용한다.

## 6. 최적화 컴포넌트

### 6.1 BundleContract와 seed 경계

`schema.py`, `artifacts.py`와 `cli.py`의 흩어진 v1 상수를 하나의 불변 `BundleContract`로 모은다. 이 객체는 허용된 manifest/config/summary version, role별 schema bytes·digest, artifact version과 seed source를 제공한다. 외부 경로나 Bundle 내부 schema를 동적으로 신뢰하지 않는다.

`statistics.py`, `quality_intervals.py`와 `summary.py`에는 criteria identity 대신 검증된 seed material만 전달하는 내부 API를 둔다. bootstrap worker protocol은 세 번째 값의 의미가 바뀌므로 v2로 올리고, v1 compatibility wrapper와 섞어 해석하지 않는다. v2 summary lineage는 `config.bootstrap.seedProtocol`과 `config.bootstrap.seedMaterial`을 실제 dependency로 기록한다.

### 6.2 PredicateMaskCache

`quality_intervals.py`에서 반복되는 candidate predicate와 valid-row 조건을 불변 boolean mask로 만든다. cache key는 다음 의미 입력을 모두 포함한다.

- population/split identity
- field name과 analysis family
- predicate의 canonical boundary/value tuple
- application context와 equipment scope
- label·validity 정책의 고정 식별자

mask는 원래 index와 길이를 함께 검증한다. discovery와 confirmation처럼 population이 다른 mask는 공유하지 않는다. NumPy/Pandas의 암묵적 index alignment에 의존하지 않고 명시된 row ordinal 기준으로만 사용한다.

### 6.3 ChargeConfusionStats

각 holdout profile에서 Charge마다 `true positive`, `false positive`, `false negative`, `true negative`, alert count, defect count와 row count의 정수 tuple을 한 번 계산한다. bootstrap은 기존 RNG 호출로 똑같은 Charge ordinal을 같은 순서로 뽑고, 선택된 tuple을 정수 합산한 뒤 기존 metric 함수를 호출한다.

원본 row list를 만들지 않지만 다음은 동일해야 한다.

- replicate별 선택 Charge sequence
- replicate별 confusion totals
- point estimate, valid replicate 수와 CI 배열
- reason code와 최종 wire value

RNG를 벡터화해 호출 횟수나 난수 소비 순서를 바꾸는 최적화는 금지한다.

### 6.4 ColumnSummaryCache

`summary.py`는 field마다 필요한 numeric values, missing count, 정렬된 finite values와 categorical counts를 한 번 snapshot한다. source profile, drift와 summary는 이 snapshot을 읽는다. 분위수와 중앙값 함수 자체는 기존 함수를 사용하므로 경계값과 float spelling은 변하지 않는다.

전체 DataFrame의 `to_dict(orient="records")`를 field마다 만들지 않는다. 요청한 column과 stable row ordinal만 읽는다.

### 6.5 계보·이벤트 열 지향 처리

`genealogy.py`와 `event_builder.py`의 행 단위 경로는 필요한 column array와 stable ordinal을 먼저 얻고 그 배열을 순회한다. 반복 `.at`, `iterrows`와 중간 DataFrame deep copy를 줄인다. 다음 의미는 명시적으로 보존한다.

- Pandas NA와 원본 빈 문자열의 기존 구분
- quarantine reason과 count
- stable sort key와 tie-breaker
- ID preimage의 exact Python scalar type
- `values_json`의 key 존재와 JSON null

event object의 최종 순서는 기존 canonical sort key로 한 번 확정한다. 출력 스트리밍은 summary와 Manifest가 요구하는 count·digest를 정확히 계산하고 기존 byte stream을 재현할 수 있는 단계에서만 적용한다.

### 6.6 원본 읽기 메모리 단계

현재 raw bytes, decoded string, record list와 DataFrame이 겹치는 구간은 최대 RSS 측정 후 별도 단계에서 줄인다. 변경할 경우 하나의 `O_NOFOLLOW` 인증 file descriptor에서 incremental SHA-256과 strict CP949 decoder를 함께 수행하고, 다음을 증명해야 한다.

- digest 대상 bytes가 parser가 읽은 bytes와 동일하다.
- 파일 교체·변경 탐지가 약해지지 않는다.
- decode error 위치와 schema error 의미가 유지된다.
- 행·칼럼 값과 lineage source token이 동일하다.

이 보안 증명이 없거나 최대 RSS가 10% 미만만 개선되면 읽기 경로는 변경하지 않는다.

### 6.7 유지할 기존 안전 구현

다음은 계산량이 있어도 현재 설계에서 유지한다.

- schema/application validation
- source와 artifact digest 검증
- canonical number exact interval oracle
- lock, fsync, 원자 rename과 기존 Bundle 재사용 검증
- label leakage 차단과 fixed strata 정책

canonical float encoding cache는 IEEE-754 bit key, bounded size와 exact oracle equivalence를 모두 증명하기 전에는 도입하지 않는다. 우선순위가 낮으므로 앞 단계가 목표를 달성하면 구현하지 않는다.

## 7. 오류와 cache 수명

- 모든 cache는 분석 실행 단위 local object이며 module global이나 디스크에 저장하지 않는다.
- cache key 충돌, mask 길이·index 불일치 또는 immutable input 변조는 즉시 실패한다.
- v2 config의 seed protocol·material이 없거나 잘못됐으면 분석 전에 실패한다.
- v1/v2 manifest·artifact version·schema digest가 섞였으면 fallback하지 않고 실패한다.
- 최적화 결과가 reference의 identity-normalized 의미와 다르면 artifact publication 전에 실패한다.
- 실패한 실행의 임시 Bundle은 완료 Bundle로 이동하지 않는다.
- 동일 `bundleId`의 기존 Bundle과 artifact digest가 다르면 비결정성 오류를 유지한다.
- 기존 v1 Bundle 경로는 v2 publication target으로 사용하지 않는다.
- 메모리 절감을 위해 입력을 일찍 해제하더라도 lineage와 오류 메시지에 필요한 최소 provenance는 보존한다.

## 8. 테스트 설계

### 8.1 reference 동일성과 허용 차이

최적화 전에 작은 fixture와 승인된 실제 v1 Bundle에서 기존 계산 경로를 test-only oracle로 고정한다. 다음은 v1과 v2에서 직접 같은지 비교한다.

- predicate·validity mask의 true ordinal
- discovery/confirmation/interaction rule 전체 model
- bootstrap replicate별 선택 Charge와 confusion tuple
- replicate별 metric, valid count, CI와 reason code
- source profile·drift·holdout metric
- quarantine, split, censoring과 purge count
- rule/range/material/batch/event ID
- replay event의 전체 순서와 identity-normalized event projection
- Java monitor의 rule grade·evidence·count·alert를 bundle ID만 정규화한 결과

event projection은 `bundle_id`와 `criteria_id` 두 binding column만 각각의 공통 placeholder로 치환한다. `schema_version`, `event_id`, replay 시각·batch·stage·material·equipment field와 canonical `values_json` bytes는 모두 정확히 같아야 한다. `event_id` preimage에는 두 binding ID가 없으므로 v1과 v2에서 같은 값이 hard gate다.

다음만 명시적인 allowlist로 정규화한다.

- config·summary·manifest의 v1/v2 schema version
- v2 config의 seed protocol·material
- producer runtime 전체 provenance
- criteria/bundle ID와 그것을 복사한 binding field
- Manifest identity, artifact digest·size
- v2 config에 `bootstrap.seedProtocol`·`bootstrap.seedMaterial` 두 field-lineage record가 추가되는 차이
- rule-CI와 holdout-bootstrap output dependency가 `identity.criteria_id`에서 `config.bootstrap.seedMaterial`로 바뀌고, seed 공식을 선택하는 `config.bootstrap.seedProtocol` dependency가 추가되는 차이

summary lineage는 위 output key에 해당하는 record만 keyed projection으로 정규화한다. 다른 lineage record의 추가·삭제·dependency·conversion·filter·transformation 차이는 실패다. 전체 allowlist 밖의 단 하나의 값·행·순서 차이도 실패다. v2를 동일 runtime·config·source에서 두 번 만들 때는 정규화하지 않고 전체 Bundle bytes가 같아야 한다.

### 8.2 결정론·보안 회귀

- v2 고정 seed를 사용해 인증 이후 canonical intermediate의 row 순서를 바꿔도 identity-normalized 의미 결과가 같다. 원본 CSV bytes를 바꾸는 검사는 source digest와 ID가 달라지는 별도 provenance test로 취급한다.
- 동일한 v2 입력·runtime으로 두 번 실행하면 전체 Bundle bytes가 같다.
- 검증을 마친 v1 reference Manifest의 criteria ID와 대응하는 v2 config의 seed material이 정확히 같다.
- 기존 v1 actual·golden Bundle이 업그레이드된 verifier에서 그대로 통과한다.
- v1 manifest에 v2 artifact를 넣거나 v2 manifest에 v1 config를 넣으면 실패한다.
- CP949 오류, 파일 교체, symlink와 digest mismatch를 계속 거부한다.
- 캐시 활성/비활성 test path가 같은 wire bytes를 만든다.
- v1 criteria ID와 v2 seed material을 각각 사용했을 때 모든 bootstrap replicate 배열이 같다.

### 8.3 테스트 lane

```text
빠른 단위 테스트
    ↓
통계·wire 동일성 테스트
    ↓
v1/v2 계약·seed 표본 호환성 테스트
    ↓
기존 실제 데이터 v1↔v2 의미 동일성 테스트
    ↓
성능·최대 RSS 측정
```

긴 실제 데이터·snapshot 테스트는 명시적인 marker와 별도 명령으로 분리하되 삭제하거나 기본 전체 검증에서 숨기지 않는다. 빠른 개발 lane은 장시간 marker를 제외하고, 기본 전체 명령은 지금처럼 모든 test를 수집한다. 실제 데이터 환경변수가 없을 때만 해당 환경 의존 test가 기존 이유와 함께 skip된다. 최종 완료 전에는 빠른 lane, 기본 전체 lane과 기존 데이터를 지정한 실제 데이터 lane을 모두 실행한다.

## 9. 성능 측정과 목표

동일한 장비, Python runtime, lockfile과 원본 데이터에서 전후를 측정한다. 짧은 경로는 warm-up 후 3회 중앙값, 긴 실제 경로는 1회 cold와 2회 warm 값을 모두 기록한다. `/usr/bin/time` 계열로 wall time과 최대 RSS를 기록한다.

| 지표 | 기준 | 완료 목표 |
|---|---:|---:|
| 기준 1,390개 Python 테스트 selection | 553.18초 | 같은 selection에서 30% 이상 단축 목표 |
| 실제 snapshot event 생성 test | 168.63초 | 30% 이상 단축 목표 |
| 약 20.61~28.11초 품질구간 test군 | 각 기준값 | 중앙값 30% 이상 단축 목표 |
| 주요 Python 경로 최대 RSS | 구현 전 측정값 | 20% 이상 절감 목표 |

identity-normalized 의미 동일성과 같은 v2 입력의 byte 결정론이 hard gate다. 새 회귀 테스트 때문에 전체 test count가 늘어나므로 성능 비교에는 최적화 전 1,390개 baseline selection을 고정해 사용하고, 새 테스트 통과 여부는 별도로 보고한다. 전체 시간은 머신 상태의 영향을 받으므로 전후 측정 조건과 원시 결과를 함께 기록한다. 개별 최적화가 중앙값 10% 미만의 이득만 만들고 복잡성을 유의미하게 늘리면 그 변경을 유지하지 않는다.

## 10. 구현 순서와 커밋 경계

1. test-only v1 reference oracle과 benchmark 명령
2. v2 config·summary·manifest schema, `BundleContract`와 고정 seed 경계
3. v1/v2 bootstrap 표본 및 identity-normalized 실제 Bundle 동일성 검증
4. holdout one-pass 평가와 Charge confusion bootstrap
5. predicate·validity mask cache
6. column summary cache
7. genealogy·event builder 열 지향 처리
8. 필요할 때만 인증 source streaming과 memory lifetime 축소
9. v2 재봉인·Java 실제 재생·성능 검증과 한국어 결과 문서

공유 v2 계약과 양쪽 loader 지원은 어느 한쪽만 남아 깨지지 않도록 하나의 별도 호환성 커밋 경계로 관리한다. 이후 Python 계산 최적화와 Java/Swing 최적화는 서로 다른 작은 로컬 커밋으로 남긴다.

## 11. 완료 정의

- 전체 Python 테스트와 새 reference·결정론·보안 테스트가 통과한다.
- 기존 실제·golden v1 Bundle과 v1 schema bytes가 그대로 보존되고 계속 검증된다.
- 기존 실제 데이터만 사용한 새 v2 Bundle이 새 producer provenance와 새 criteria/bundle ID를 가진다.
- v1↔v2의 모든 bootstrap 표본·통계·rule grade·rule/range/material/batch/event ID·event 값·경보가 identity-normalized 비교에서 같다.
- 동일 v2 입력을 두 번 봉인하면 전체 Bundle bytes가 같다.
- 측정한 시간과 최대 RSS를 전후 비교해 기록한다.
- 성능 목표 미달 항목과 유지하지 않은 후보도 숨김없이 문서화한다.
- 새 데이터, 통계 parameter 변경 또는 보안 검증 제거를 사용하지 않는다.
- GitHub에 Push하지 않고 로컬 브랜치에만 커밋한다.
