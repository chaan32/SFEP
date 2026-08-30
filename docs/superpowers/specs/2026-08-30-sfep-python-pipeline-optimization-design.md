# SFEP Python Bundle 생성 파이프라인 최적화 설계

- 작성일: 2026-08-30
- 상태: 대화 승인 완료, 구현 전 사용자 문서 검토 대기
- 대상 저장소: `/Users/haechan/Desktop/SFEP`
- 대상 모듈: `analysis`
- 원격 저장소 정책: 로컬 커밋만 수행하고 자동 Push하지 않음

## 1. 목적

Python 분석기는 세 CP949 원본을 검증하고 계보를 연결한 뒤 운전범위, 품질위험 규칙, 요약과 189,043개 replay event가 포함된 봉인 Bundle을 결정론적으로 만든다. 현재 결과는 검증됐지만 동일한 조건·칼럼·Charge 표본을 여러 분석 단계와 부트스트랩 반복에서 다시 계산하고, 큰 DataFrame과 row object를 반복 생성한다.

이번 변경은 다음을 달성한다.

1. 통계식·표본·난수열·직렬화 규약을 바꾸지 않고 반복 계산을 제거한다.
2. 실제 이벤트 생성과 품질구간 테스트의 실행 시간을 줄인다.
3. 중간 DataFrame·record·sample row 복제를 줄여 최대 메모리를 낮춘다.
4. 생성된 여섯 artifact와 Manifest를 기존 Bundle과 byte-for-byte 동일하게 유지한다.
5. 빠른 개발 테스트와 장시간 실제 회귀 검증을 명확히 분리한다.

## 2. 변경 불가 계약

- 세 원본의 strict CP949 해석, 크기와 SHA-256 검증
- 계보 연결·격리·label maturity·split·charge purge 의미
- 후보 bin, 최소 support, defect count, FDR family와 등급 경계
- discovery에서 고정한 조건을 confirmation에서 재학습하지 않는 원칙
- 2,000회 Charge bootstrap, seed와 각 replicate의 Charge 추출 순서
- 통계식, null·NaN 처리, reason code와 provenance
- 결정론적 JSON·CSV 직렬화, float canonicalization과 정렬 순서
- `criteriaId`, `bundleId`, rule/event/material/batch ID
- lock, `O_NOFOLLOW`, 임시 형제 디렉터리, `fsync`, digest 검증과 원자 이동
- 기존 Bundle의 모든 파일 바이트

## 3. 비대상

- 모델 또는 불량 확률 예측 도입
- 임계치·학습법·split·bootstrap 횟수 변경으로 속도 확보
- schema v2, 압축 형식 또는 artifact 구조 변경
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
인증된 원본 snapshot
        │
        ▼
고정 칼럼 배열·행 식별자
        │
        ├─ PredicateMaskCache ── 규칙 탐색/확인/상호작용
        ├─ ColumnSummaryCache ── profile/drift/summary
        └─ ChargeConfusionStats ─ holdout/bootstrap
        │
        ▼
기존 결과 model
        │
        ▼
기존 canonical serializer·보안 봉인
        │
        ▼
기존 Bundle과 byte-for-byte 비교
```

캐시는 한 번의 분석 실행 안에서만 존재한다. artifact와 ID preimage에는 포함하지 않고 실행 종료 시 사라진다.

## 6. 최적화 컴포넌트

### 6.1 PredicateMaskCache

`quality_intervals.py`에서 반복되는 candidate predicate와 valid-row 조건을 불변 boolean mask로 만든다. cache key는 다음 의미 입력을 모두 포함한다.

- population/split identity
- field name과 analysis family
- predicate의 canonical boundary/value tuple
- application context와 equipment scope
- label·validity 정책의 고정 식별자

mask는 원래 index와 길이를 함께 검증한다. discovery와 confirmation처럼 population이 다른 mask는 공유하지 않는다. NumPy/Pandas의 암묵적 index alignment에 의존하지 않고 명시된 row ordinal 기준으로만 사용한다.

### 6.2 ChargeConfusionStats

각 holdout profile에서 Charge마다 `true positive`, `false positive`, `false negative`, `true negative`, alert count, defect count와 row count의 정수 tuple을 한 번 계산한다. bootstrap은 기존 RNG 호출로 똑같은 Charge ordinal을 같은 순서로 뽑고, 선택된 tuple을 정수 합산한 뒤 기존 metric 함수를 호출한다.

원본 row list를 만들지 않지만 다음은 동일해야 한다.

- replicate별 선택 Charge sequence
- replicate별 confusion totals
- point estimate, valid replicate 수와 CI 배열
- reason code와 최종 wire value

RNG를 벡터화해 호출 횟수나 난수 소비 순서를 바꾸는 최적화는 금지한다.

### 6.3 ColumnSummaryCache

`summary.py`는 field마다 필요한 numeric values, missing count, 정렬된 finite values와 categorical counts를 한 번 snapshot한다. source profile, drift와 summary는 이 snapshot을 읽는다. 분위수와 중앙값 함수 자체는 기존 함수를 사용하므로 경계값과 float spelling은 변하지 않는다.

전체 DataFrame의 `to_dict(orient="records")`를 field마다 만들지 않는다. 요청한 column과 stable row ordinal만 읽는다.

### 6.4 계보·이벤트 열 지향 처리

`genealogy.py`와 `event_builder.py`의 행 단위 경로는 필요한 column array와 stable ordinal을 먼저 얻고 그 배열을 순회한다. 반복 `.at`, `iterrows`와 중간 DataFrame deep copy를 줄인다. 다음 의미는 명시적으로 보존한다.

- Pandas NA와 원본 빈 문자열의 기존 구분
- quarantine reason과 count
- stable sort key와 tie-breaker
- ID preimage의 exact Python scalar type
- `values_json`의 key 존재와 JSON null

event object의 최종 순서는 기존 canonical sort key로 한 번 확정한다. 출력 스트리밍은 summary와 Manifest가 요구하는 count·digest를 정확히 계산하고 기존 byte stream을 재현할 수 있는 단계에서만 적용한다.

### 6.5 원본 읽기 메모리 단계

현재 raw bytes, decoded string, record list와 DataFrame이 겹치는 구간은 최대 RSS 측정 후 별도 단계에서 줄인다. 변경할 경우 하나의 `O_NOFOLLOW` 인증 file descriptor에서 incremental SHA-256과 strict CP949 decoder를 함께 수행하고, 다음을 증명해야 한다.

- digest 대상 bytes가 parser가 읽은 bytes와 동일하다.
- 파일 교체·변경 탐지가 약해지지 않는다.
- decode error 위치와 schema error 의미가 유지된다.
- 행·칼럼 값과 lineage source token이 동일하다.

이 보안 증명이 없거나 최대 RSS가 10% 미만만 개선되면 읽기 경로는 변경하지 않는다.

### 6.6 유지할 기존 안전 구현

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
- 최적화 결과가 reference 결과와 다르면 artifact publication 전에 실패한다.
- 실패한 실행의 임시 Bundle은 완료 Bundle로 이동하지 않는다.
- 동일 `bundleId`의 기존 Bundle과 artifact digest가 다르면 비결정성 오류를 유지한다.
- 메모리 절감을 위해 입력을 일찍 해제하더라도 lineage와 오류 메시지에 필요한 최소 provenance는 보존한다.

## 8. 테스트 설계

### 8.1 reference 동일성

최적화 전에 작은 fixture에서 기존 계산 경로를 test-only oracle로 고정한다. 다음을 직접 비교한다.

- predicate·validity mask의 true ordinal
- discovery/confirmation/interaction rule 전체 model
- bootstrap replicate별 선택 Charge와 confusion tuple
- replicate별 metric, valid count, CI와 reason code
- source profile·drift·holdout·lineage subtree
- quarantine, split, censoring과 purge count
- event model의 전체 순서와 각 `values_json`
- 최종 여섯 artifact bytes, Manifest bytes와 모든 ID

### 8.2 결정론·보안 회귀

- 입력 row order를 바꿔도 기존 계약이 요구하는 결과가 같다.
- 같은 입력을 두 번 실행하면 전체 Bundle bytes가 같다.
- CP949 오류, 파일 교체, symlink와 digest mismatch를 계속 거부한다.
- 캐시 활성/비활성 test path가 같은 wire bytes를 만든다.
- seed와 bootstrap replicate 배열이 기존 fixture와 같다.

### 8.3 테스트 lane

```text
빠른 단위 테스트
    ↓
통계·wire 동일성 테스트
    ↓
golden Bundle byte 테스트
    ↓
기존 실제 데이터 전체 테스트
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

정확한 Bundle byte 동일성이 hard gate다. 새 회귀 테스트 때문에 전체 test count가 늘어나므로 성능 비교에는 최적화 전 1,390개 baseline selection을 고정해 사용하고, 새 테스트 통과 여부는 별도로 보고한다. 전체 시간은 머신 상태의 영향을 받으므로 전후 측정 조건과 원시 결과를 함께 기록한다. 개별 최적화가 중앙값 10% 미만의 이득만 만들고 복잡성을 유의미하게 늘리면 그 변경을 유지하지 않는다.

## 10. 구현 순서와 커밋 경계

1. test-only reference oracle과 benchmark 명령
2. holdout one-pass 평가와 Charge confusion bootstrap
3. predicate·validity mask cache
4. column summary cache
5. genealogy·event builder 열 지향 처리
6. 필요할 때만 인증 source streaming과 memory lifetime 축소
7. 전체 byte 동일성·실데이터·성능 검증과 한국어 결과 문서

각 단계는 독립적으로 비교·되돌릴 수 있는 로컬 커밋으로 남긴다. Java/Swing 변경과 같은 커밋에 섞지 않는다.

## 11. 완료 정의

- 전체 Python 테스트와 새 reference·결정론·보안 테스트가 통과한다.
- 기존 실제 데이터만 사용해 생성한 Bundle이 승인된 기존 Bundle과 byte-for-byte 같다.
- 모든 rule/event/material/batch ID와 통계·lineage가 같다.
- 측정한 시간과 최대 RSS를 전후 비교해 기록한다.
- 성능 목표 미달 항목과 유지하지 않은 후보도 숨김없이 문서화한다.
- 새 데이터, 통계 parameter 변경 또는 보안 검증 제거를 사용하지 않는다.
- GitHub에 Push하지 않고 로컬 브랜치에만 커밋한다.
