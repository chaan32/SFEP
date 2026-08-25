# SFEP 공정설비 조업조건 기반 품질위험 조기경보 시스템 설계

- 작성일: 2026-08-25
- 최종 수정일: 2026-08-26
- 상태: 대화 승인 완료, 구현 전 사용자 문서 검토 대기
- 대상 저장소: `server/SFEP`
- 원본 데이터 위치: 분석 명령의 필수 절대경로 인자 `--data-dir`

## 1. 배경과 문제 정의

기존 Steel Shadow 흐름은 여러 AI 모델이 불량 확률을 계산하고 Java Adapter가 그 결과를 전달하는 구조였다. 새 목표는 AI 모델의 성능 경쟁이나 설비의 기계 고장예측이 아니다.

새 시스템은 과거 생산기록을 공정 완료 이벤트처럼 순차 재생하면서, **그 시점까지 실제로 알 수 있었던 Slab 또는 Coil의 대표 조업값만** 감시한다. 값이 과거 `judge` 불량률 상승구간에 해당하면 작업자에게 설비, 소재, 공정단계, 위험항목과 통계 근거를 설명한다. 현재 데이터는 실시간 센서 스트림이 아니므로 이 기능은 실제 공장 온라인 감시가 아니라 **온라인 의사결정 방식을 검증하는 역사적 재생 모니터**다.

정확한 시스템 정의는 다음과 같다.

> **제강·연주, 가열로·조압연, 소둔·산세 공정의 역사적 대표 조업값을 공정단계별로 재생하고, 각 시점까지 관측된 값이 과거 품질불량 증가구간에 해당하면 설명 가능한 규칙으로 경보를 제공하는 Java Swing 시스템**

이 시스템은 다음 두 판단을 구분한다.

1. **역사적 조업범위 편차**: 재생 시점까지 관측된 직접 조업값이 해당 설비와 제품조건의 역사적 전형범위에서 벗어났는가.
2. **품질 위험 연관성**: 재생 시점까지 관측된 조업값 또는 제품상태값이 과거 `judge` 불량률이 신뢰성 있게 높았던 구간에 들어갔는가.

두 판단은 별도 축으로 유지한다. 제품 화학성분이나 치수는 품질위험의 근거가 될 수 있지만, 그 값만으로 설비가 편차 상태라고 말하지 않는다.

## 2. 목표

1. 현재 세 CSV를 모두 사용해 소재별 공정 계보를 구성한다.
2. 설비·공정·제품조건별 역사적 전형 조업범위와 `judge` 연계 품질위험 구간을 계산한다.
3. 과거 데이터를 원래 시간 해상도와 단계별 정보 공개시점을 보존한 공정 이벤트로 재생한다.
4. 재생 중인 소재 단위로 정상·주의·위험·근거 부족 상태를 판정한다.
5. 경보마다 어떤 설비의 어떤 항목이 어떤 근거로 위험한지 보여준다.
6. 별도 Java Spring Boot 비웹 프로세스와 단일 Swing 프레임으로 모니터를 실행한다.
7. Python은 관리 기준표와 재생 이벤트를 생성하는 오프라인 통계 분석기로만 사용한다.
8. 기존 Steel Shadow와 AI 모델 관련 코드는 제거하고 일반 SFEP 기반은 유지한다.

## 3. 비대상

다음 기능은 구현하지 않는다.

- AI/머신러닝 불량예측 모델
- 단일 블랙박스 불량 확률
- Python 예측 API 또는 장기 실행 Python Sidecar
- Java와 Python 사이의 HTTP 예측 통신
- 모터, 베어링, 밸브, 펌프의 고장진단
- 진동, 전류, 압력 등 존재하지 않는 센서값의 생성
- 실제 공장 PLC·센서와의 온라인 연결
- 초·분 단위 온도 변화, 위험 지속시간 또는 실제 설비의 현재 위치 추정
- 설비 자동제어 또는 조업값 자동 변경
- 관측된 연관성을 불량의 인과관계로 단정
- 원격 저장소 자동 Push

## 4. 원본 데이터와 확인된 제약

원본 CSV는 CP949 인코딩이다.

| 파일 | 실제 크기 | 역할 | 실제 시간 해상도 |
|---|---:|---|---|
| `sts_1sm_cc_1.csv` | 23,649행 × 15열 | 제강·연주 선행 공정조건 | `cast_date` 일 단위 |
| `sts_2fur_hr_2.csv` | 23,652행 × 26열 | 가열로·열연 조업조건 | 추출 일자 + 0~23시, 구간시간은 분 단위 |
| `sts_3ap_3.csv` | 23,641행 × 9열 | AP 조업조건과 최종 `judge` | `ap_date` 일 단위 |

확인된 데이터 제약은 다음과 같다.

- SM/CC와 FUR/HR는 `charge_id + slab_no`로 연결한다. `charge_id` 단독 조인은 금지한다.
- FUR/HR와 AP는 `hr_coil_id`로 연결한다.
- 중복 키와 미연결 소재가 있으므로 완전한 1:1 조인을 가정하지 않는다.
- `f_ext_time`은 분·초가 없는 시간 버킷이다.
- 동일 호기·동일 추출 시간대에 여러 소재가 있어 실제 Coil 순서를 복원할 수 없다.
- `f_pre_interval`, `f_heat_interval`, CSV의 `f_sock_interval`은 분 단위 구간시간이다.
- RM4의 정확한 발생시각은 없다.
- AP `judge`는 열연보다 뒤에 도착하며 실제 연결 데이터의 중앙 시차는 약 4일이다.
- AP 지연은 중앙값 약 4일, 99백분위 약 38일, 최대 약 68일이다. 이 지연을 무시하고 최근 소재를 양품으로 간주하지 않는다.
- AP 조업값과 `judge`는 같은 AP 행과 일자에 있어 두 값의 선후관계를 증명할 수 없다. 따라서 `AP_RECORDED_WITH_RESULT`라는 하나의 논리 이벤트로 공개한다.
- 각 행은 공정단계의 대표값이지 공정 중 연속 센서 시계열이 아니다.
- `judge`는 Coil Grinding 평가 기반 이진 결과다. 특정 결함 종류나 설비 고장을 직접 뜻한다고 해석하지 않는다.
- `f_bfg`, `f_cog`, `f_ldg`의 물리 단위가 정의서에 명확하지 않으므로 실제 열효율이나 에너지 소비량으로 단정하지 않는다. 확인 전에는 상대적 연료 프로파일로만 사용한다.
- 원본 행은 시간순으로 정렬되어 있지 않다.

## 5. 선택한 접근과 대안

### 선택: 전체 공정 모니터 + 가열로 상세화

- 제강·연주: Charge/Slab와 일 단위의 선행 공정조건을 감시한다.
- 가열로·조압연: 소재별 공정 이벤트와 위험경보를 가장 상세하게 표시한다.
- AP: 일·작업조 단위 조업조건을 감시하고 실제 `judge`를 후행 결과로 연결한다.

### 선택하지 않은 대안

1. **가열로만 구현**: 시간 재생은 가장 좋지만 전체 공정 계보와 AP 결과 연결이 약하다.
2. **공정별 독립 프로그램**: 구현은 단순하지만 동일 소재의 선행조건과 후행 결과가 끊어진다.
3. **Python 실시간 서버**: 동적 계산은 가능하지만 기존 Shadow와 같은 운영 복잡성이 다시 생긴다.
4. **전부 Java로 분석**: 실행파일은 단순하지만 통계분석과 산출물 검증이 복잡해진다.

## 6. 전체 아키텍처

```text
원본 CSV 3개
      │
      ▼
Python 오프라인 분석기
├─ 스키마·인코딩 검증
├─ 계보 연결과 격리
├─ 컬럼 역할 분류
├─ 설비별 역사적 전형 조업범위 계산
├─ judge 품질위험 구간 계산
├─ 공정 재생 이벤트 생성
└─ 분석 요약·해시 생성
      │
      ▼
로컬 관리 기준 디렉터리
├─ analysis_config.json
├─ producer_runtime.json
├─ equipment_operating_ranges.json
├─ quality_risk_intervals.json
├─ replay_events.csv
├─ analysis_summary.json
└─ bundle_manifest.json  ← 마지막에 원자적으로 기록되는 완료 표식
      │
      ▼
Java Spring Boot + Swing
├─ 관리 기준표 로더
├─ 공정 이벤트 재생기
├─ 소재·설비 상태 관리자
├─ 규칙 기반 위험 판정기
├─ 경보 이력 저장소
└─ Swing 현황판
```

Python 프로세스는 분석 명령이 종료되면 함께 종료된다. Swing 실행 중에는 Python 프로세스, 모델, API, Registry가 존재하지 않는다.

산출물은 `--output-dir` 아래 임시 형제 디렉터리에 모두 쓴 뒤 파일별 `fsync`와 SHA-256 검증을 수행하고, 완성된 디렉터리를 `<output-dir>/<bundleId>`로 원자 이동한다. 임시·최종 디렉터리는 같은 로컬 파일시스템에 있어야 한다. 같은 ID가 이미 있으면 해시를 검증해 재사용하고 덮어쓰지 않는다. `bundle_manifest.json`은 임시 디렉터리 안에서 항상 마지막에 기록하며, 최종 디렉터리는 Manifest까지 완성되기 전에는 보이지 않는다.

두 ID의 책임은 다음처럼 분리한다.

- `criteriaId`: `T` 시점 적합 투영, 분석설정·producer runtime 파일과 관련 contract schema의 정체성이다. `T` 뒤 자료는 포함하지 않는다.
- `bundleId`: 세 원본 파일 전체, 분석설정·producer runtime·모든 wire schema와 `criteriaId`를 포함한 Bundle **입력·계약**의 정체성이다. 여섯 artifact의 정확한 bytes는 Manifest의 개별 digest가 식별한다.

두 ID는 JSON 재직렬화가 아닌 `sfep-id-lines/v1` 바이트 규약으로 계산한다. 첫 줄은 각각 `sfep-criteria-id/v1` 또는 `sfep-bundle-id/v1`이고, 나머지는 아래 필수 key를 UTF-8 byte 순으로 정렬한 `key=value\n`이다. 파일 끝에도 LF 한 개를 둔 뒤 SHA-256을 계산한다. key와 value에는 LF와 `=`를 허용하지 않는다.

| ID | 필수 key |
|---|---|
| criteria | `as_of`, `criteria_projection_sha256`, `analysis_config_sha256`, `producer_runtime_sha256`, `schema.analysis_config.sha256`, `schema.producer_runtime.sha256`, `schema.equipment_operating_ranges.sha256`, `schema.quality_risk_intervals.sha256` |
| bundle | `criteria_id`, `analysis_config_sha256`, `producer_runtime_sha256`, 세 역할 각각의 `source.<role>.name`, `source.<role>.size_bytes`, `source.<role>.sha256`, 일곱 파일 각각의 `schema.<role>.sha256` |

source 역할은 `sm_cc`, `fur_hr`, `ap`로 고정한다. schema 역할은 `bundle_manifest`, `analysis_config`, `producer_runtime`, `equipment_operating_ranges`, `quality_risk_intervals`, `analysis_summary`, `replay_events`다. `analysis_config_sha256`은 체크인 설정파일 bytes, 각 `schema.<role>.sha256` value는 대응 contract schema bytes의 `sha256:` digest다. `producer_runtime_sha256`은 아래 고정 Python 환경 manifest bytes의 digest다. Manifest의 `identity`와 `criteriaIdentity`에는 바로 이 key/value map을 넣으므로 Python과 Java가 동일 line preimage를 재구성할 수 있다. Java monitor는 이 일곱 normative schema 원본 bytes를 bootJar resource에 포함하고, Manifest가 주장하는 각 schema digest를 자신의 내장 resource digest와 exact-match해야 한다. Manifest 내부 map만 서로 일치하는 것은 호환성 검증으로 인정하지 않는다.

artifact는 내부에 `bundleId`를 포함하므로 artifact digest를 `bundleId` preimage에 다시 넣는 순환은 만들지 않는다. 같은 입력·계약에서 같은 `bundleId`인데 기존 디렉터리와 새 artifact digest가 하나라도 다르면 producer 비결정성 오류로 종료하고 기존 Bundle을 재사용하거나 덮어쓰지 않는다.

`criteria_projection_sha256`의 입력은 기준에 실제 사용된 행만 `hr_date,charge_id,slab_no,hr_coil_id`로 안정 정렬한 JSON Lines다. 각 줄은 (1) 전형범위 대상의 `firstAvailableStage`·field·context·value와 (2) 성숙 품질대상의 split·모든 후보 feature·context·`judge`를 구분한 object이며 6.1의 결정론적 JSON 규약으로 직렬화한다. `T` 뒤 행, 벽시계와 절대경로는 투영에서 제외한다.

따라서 `T` 뒤 AP 값이나 `judge`가 바뀌면 원본 SHA와 `bundleId`는 반드시 바뀌지만 `criteriaId`와 두 criteria JSON의 정규화 내용은 바뀌지 않아야 한다. 벽시계 생성시각, 절대경로, 실행시간은 로그에만 남기고 산출물에는 넣지 않는다.

분석기는 `--config`, `--runtime-manifest`, `--data-dir`, `--output-dir` 절대경로를 필수로 받고 해석된 경로를 출력한다. Java는 `bundle_manifest.json`, 내장 normative schema digest, 두 ID와 산출물 해시를 검증하며 실행 시 원본 CSV는 요구하지 않는다. 사용자가 원본 경로를 추가로 제공한 경우에만 원본 해시도 대조한다.

### 6.1 Bundle wire contract v1

모든 JSON은 UTF-8·BOM 없음, key Unicode code-point 오름차순, 공백 없는 `,`·`:` 구분자, 비ASCII 문자 비이스케이프, LF 한 개 종료로 기록한다. 숫자는 Python binary64의 shortest round-trip 표기인 유한 JSON number만 허용하고 NaN/Infinity는 금지한다. Java는 JSON을 다시 직렬화해 hash하지 않고 원본 바이트 digest를 검증한 뒤 파싱한다. 날짜는 `YYYY-MM-DD`, SHA-256은 `sha256:` 뒤 소문자 64자리 hex다. 모르는 필수 enum이나 schema major version은 Java가 거부한다.

normative contract는 `contracts/equipment-monitor/v1/` 아래 다음 JSON Schema 파일과 `golden-bundle/` fixture로 저장한다. 동일한 일곱 schema bytes를 Gradle `processResources`로 monitor bootJar의 고정 classpath에 포함하며, 빌드 중 원본과 복사본의 digest가 다르면 실패한다.

- `bundle_manifest.schema.json`
- `analysis_config.schema.json`
- `producer_runtime.schema.json`
- `equipment_operating_ranges.schema.json`
- `quality_risk_intervals.schema.json`
- `analysis_summary.schema.json`
- `replay_event_row.schema.json`
- `id-test-vectors.json` — hand-authored line preimage와 독립 SHA-256 기대값
- `golden-source/` — CP949 최소 원본 3개
- `golden-expectation/` — hand-authored criteria projection, 통계·이벤트·경보의 의미상 기대값과 provenance token template
- `golden-bundle/` — producer 구현 뒤 독립 봉인한 최소 완전 Bundle과 Java 예상 이벤트·경보

모든 object schema는 `additionalProperties:false`, 필수 필드, nullability, enum, 숫자범위와 배열 정렬조건을 명시한다. 구현 단계에서는 JSON Schema가 normative다. producer·consumer보다 먼저 schema, 분석설정, ID test vector, golden source와 `golden-expectation/`을 hand-author하고 별도 검토한다. expectation에는 정확한 criteria projection JSON Lines, as-of/split/count, range/rule 통계·등급, replay row의 의미상 값과 Java 예상 경보를 적는다. 아직 존재하지 않는 producer 정체성에 의존하는 자리만 `@PRODUCER_RUNTIME_SHA256@`, `@CRITERIA_ID@`, `@BUNDLE_ID@`와 명명된 digest token으로 둔다.

Python producer의 모든 의미 기능을 구현하고 producer source와 wheel/runtime을 고정한 뒤, producer package를 import하지 않는 독립 `golden-seal` 도구가 token 자리에만 frozen runtime bytes, schema·source digest, identity map, 두 ID와 artifact size/digest를 채워 `golden-bundle/`을 만든다. 이 도구는 통계, 분리, 계보, 이벤트 또는 경보 계산을 하지 못하도록 계약한다. range/rule 경계·통계·등급, 이벤트 값·순서, count/as-of/split과 criteria projection 내용은 봉인 단계에서 변경할 수 없다. 봉인 diff는 허용 token과 Manifest provenance 필드만 바뀌었는지 검사한다. 기대 digest와 ID preimage는 표준 SHA-256 도구, 독립 seal test와 Java `MessageDigest` test로 교차 확인한다. 이후 Python은 golden source에서 봉인된 golden bundle을 byte-for-byte 재현하고 Java는 그 bundle의 예상 이벤트·경보를 재현해야 한다. producer가 자기 출력을 oracle로 승인하지 않으며 의미상 golden 변경은 schema/분석설정 version 검토 없이 허용하지 않는다.

Java Jackson은 unknown property, primitive null과 enum 미등록값을 모두 실패 처리한다. Java의 검증 순서는 `내장 schema bytes digest 계산 → Manifest의 일곱 schema digest와 exact-match → 두 ID 재계산 → 여섯 artifact bytes digest 검증 → 해당 schema로 검증 → DTO 파싱`으로 고정한다. 앞 단계 하나라도 실패하면 뒤 단계의 자료를 신뢰하거나 Swing을 열지 않는다.

`bundle_manifest.json`의 필수 필드는 다음과 같다. Manifest 자신은 `artifacts` digest 목록에서 제외한다.

| 필드 | 타입 | 의미 |
|---|---|---|
| `schemaVersion` | string | `sfep-equipment-bundle/v1` |
| `bundleId` / `criteriaId` | string | 위에서 정의한 SHA-256 ID |
| `identity` | object | `sfep-bundle-id/v1` 필수 key/value map; `criteria_id` 포함 |
| `criteriaIdentity` | object | `sfep-criteria-id/v1` 필수 key/value map |
| `asOf` | date | 기준시점 `T` |
| `timezone` | string | `Asia/Seoul` |
| `labelMaturityDays` | integer | `38` |
| `artifacts` | array | 역할순 정렬된 `role,sizeBytes,sha256,schemaVersion` 6건; 자유경로 필드 없음 |

artifact 역할과 파일명은 다음 일대일 매핑으로 고정한다. 배열 순서도 이 표 순서이며 누락·중복·추가 역할을 허용하지 않는다.

| role | bundle root의 고정 basename |
|---|---|
| `analysis_config` | `analysis_config.json` |
| `producer_runtime` | `producer_runtime.json` |
| `equipment_operating_ranges` | `equipment_operating_ranges.json` |
| `quality_risk_intervals` | `quality_risk_intervals.json` |
| `replay_events` | `replay_events.csv` |
| `analysis_summary` | `analysis_summary.json` |

Java는 먼저 사용자가 준 절대 bundle root가 존재하는 non-symlink directory인지 확인하고 정규화한 real path를 신뢰경계로 고정한다. Manifest의 문자열로 파일을 찾지 않고 위 role enum을 고정 basename에 매핑한다. 각 대상은 root의 직접 자식이어야 하며 절대경로·경로구분자·`.`·`..`를 허용하지 않는다. `NOFOLLOW_LINKS`로 열기 전후에 non-symlink regular file과 parent real path를 확인하고 하나라도 root 밖이거나 바뀌면 실패한다. `bundle_manifest.json` 자체도 같은 규칙의 고정 직접 자식이다.

그 뒤 bootJar에 포함된 일곱 schema bytes의 SHA-256을 계산해 두 identity map의 해당 key와 exact-match한다. 이어 두 map으로 `sfep-id-lines/v1` preimage를 재구성해 ID를 재계산하고 고정 파일의 digest를 검증한다. 같은 major version이라도 내장 schema digest와 다르면 실행을 거부한다.

`analysis_config.json`은 `schemaVersion=sfep-analysis-config/v1`, `analysisConfigVersion=quality-analysis-v1`과 10~12절의 분리공식, 38일, 최소표본·등급 상수, 공정별 완화순서, feature role, `firstAvailableStage`, FDR family와 사전고정 상호작용을 모두 포함한다. 분석기는 임의 기본값을 쓰지 않고 이 체크인 파일을 필수 입력으로 읽어 Bundle에 byte-for-byte 복사한다.

`producer_runtime.json`은 `schemaVersion=sfep-producer-runtime/v1`과 7.1절의 OS·architecture·CPython build·pip·직접/전이 패키지·실제 설치 wheel 및 tree digest·producer source digest·lock digest·환경정책을 담고 분석기가 Bundle에 byte-for-byte 복사한다.

`equipment_operating_ranges.json`은 `schemaVersion=sfep-operating-ranges/v1`, `criteriaId`, `asOf`, `ranges[]`를 가진다. 각 range의 필수 필드는 `ruleId`, `field`, `fieldRole`, `firstAvailableStage`, `equipmentType`, `equipmentId`, `contextLevel`, `context` object, `support`, `median`, `p05`, `p95`, `lowerTailEnabled`, `upperTailEnabled`이다. range `ruleId`는 이 필드들과 실제 경계의 결정론적 JSON bytes SHA-256이다. `p01`과 `p99`는 2,000건 미만이면 JSON null이다. `fieldRole`은 `DIRECT_OPERATION|PRODUCT_STATE_REFERENCE`, 모든 경계는 finite number다.

`quality_risk_intervals.json`은 `schemaVersion=sfep-quality-rules/v1`, `criteriaId`, `asOf`, `rules[]`를 가진다. 각 rule의 필수 필드는 다음과 같다.

| 필드 | 타입 |
|---|---|
| `ruleId` | string |
| `analysisFamily` | `NUMERIC|CATEGORICAL|INTERACTION` |
| `evidenceFamily` | 13절의 고정 enum |
| `firstAvailableStage`, `equipmentType`, `applicationScope`, `equipmentId` | string, string, `PROCESS_GLOBAL|EQUIPMENT_SPECIFIC`, string |
| `fieldNames` | 길이 1 또는 2의 string array |
| `predicate` | `allOf[]`; 각 항목은 `field,type,lower,lowerInclusive,upper,upperInclusive,values`이며 해당하지 않는 값은 null |
| `applicationContext`, `adjustmentLevel`, `adjustmentFieldsDropped`, `adjustmentKind` | scalar map, 0부터 시작하는 hierarchy index, string array, `STRATIFIED|UNADJUSTED_FALLBACK` |
| `grade` | `NORMAL|CAUTION|DANGER|UNCONFIRMED|INSUFFICIENT_EVIDENCE` |
| `earlyWarningEligible` | boolean; AP 규칙은 false |
| `discovery`, `confirmation` | 아래 metric object |
| `displayMergeRuleIds` | 표시용 병합이 없으면 빈 array |

`applicationContext` value는 null이 아닌 string·boolean·finite number 한 개이고 무제한 context는 빈 object다. 숫자·상호작용과 비설비 범주 규칙은 `PROCESS_GLOBAL`, `equipmentId=ALL`이고, 설비 ID 자체의 범주 predicate만 `EQUIPMENT_SPECIFIC`과 해당 ID를 쓴다. Java는 predicate와 `applicationContext`만 runtime 매칭에 쓰며 `adjustmentLevel`은 통계설명 전용이다. predicate `type`은 `NUMERIC_INTERVAL|CATEGORY_IN`, `values` 원소도 같은 scalar 타입이다. metric object는 `support`, `defects`, `crudeRate`, `crudeRateCiLower`, `crudeRateCiUpper`, `adjustedRate`, `comparatorAdjustedRate`, `riskDifference`, `relativeRisk`, `relativeRiskCiLower`, `relativeRiskCiUpper`, `pValue`, `qValue`, `reasonCode`를 가진다. 계산 불가능한 통계만 JSON null이며 `reasonCode`는 `NONE|LOW_SUPPORT|LOW_DEFECT_COUNT|ZERO_COMPARATOR_RISK|NON_FINITE_ESTIMATE|NO_INFORMATIVE_STRATA|NO_VARIATION|ZERO_VARIANCE|DIRECTION_NOT_REPEATED|TOO_FEW_VALID_BOOTSTRAPS|NOT_APPLICABLE` 중 하나다. 확인구간의 `relativeRiskCiLower`, `relativeRiskCiUpper`, `pValue`, `qValue`는 계약상 null이며 정상 계산이면 `reasonCode=NONE`이다.

`analysis_summary.json`은 `schemaVersion=sfep-analysis-summary/v1`, 두 ID, 날짜범위, `splitCounts`, `quarantineCounts`, `labelCensoringCounts`, `sourceColumnProfiles`, `driftMetrics`, `holdoutMetrics`를 가진다. 모든 count object는 `total,defects,nonDefects,unknownOrCensored` 정수와 적용 가능한 날짜범위를 포함하며 `total = defects + nonDefects + unknownOrCensored`를 항상 만족한다. Label이 정의되지 않은 격리·미연결·미성숙 행은 `unknownOrCensored`에만 센다.

`replay_events.csv`는 UTF-8, RFC 4180, LF, 점 소수점이며 첫 행의 열 순서를 다음으로 고정한다.

```text
schema_version,bundle_id,criteria_id,event_id,replay_date,replay_hour,
batch_kind,batch_id,equipment_batch_id,batch_step,time_precision,material_key,equipment_type,
equipment_id,charge_id,slab_no,hr_coil_id,ap_prod_id,values_json
```

위 줄바꿈은 문서 표시용이며 실제 헤더는 한 줄이다. `schema_version`은 `sfep-replay-events/v1`이다. `batch_id`는 전역 재생단위, `equipment_batch_id`는 그 안의 호기별 묶음이다. `replay_hour`는 인용하지 않은 10진 정수 `0..23` 또는 `DAY` 이벤트의 빈값이다. `event_id`, `batch_id`, `material_key`, `charge_id`, `slab_no`는 모든 정상 재생행에서 필수다. `equipment_batch_id`는 `batch_kind=FURNACE_HOUR AND equipment_type=FURNACE`일 때만 존재하고 `RM4_RECORDED`를 포함한 나머지는 빈값이다. `hr_coil_id`는 `RM4_RECORDED` 전까지 빈값, `ap_prod_id`는 `AP_RECORDED_WITH_RESULT` 전까지 빈값이며 이후 단계에는 이미 공개된 ID를 유지한다. 빈값은 null이다. `values_json`은 위 결정론적 JSON 규약의 object를 CSV 인용규칙으로 감싼다. 이 object에는 해당 단계에서 **새로 공개되는 필드만** 들어가고, 원본 결측 필드는 key와 JSON null을 함께 기록한다. `batch_kind`는 `CAST_DAY|FURNACE_HOUR|AP_DAY`, `batch_step`은 8.5절 이벤트 enum, `time_precision`은 `DAY|HOUR_BUCKET|SEQUENCE_ONLY`, `equipment_type`은 `SM_CC|FURNACE|RM4|AP`다. `equipment_id`는 원본 plant/호기 값 또는 고정값 `RM4_PROCESS`다. RM4 화면이 원래 가열로 호기를 보여줄 때는 같은 `material_key`의 이전 FURNACE 상태를 참조하며 RM4 행의 ID를 호기 ID로 위장하지 않는다. Java는 Apache Commons CSV와 Jackson으로 이 계약을 엄격 검증한다.

재생 ID는 `digestId(namespace, object) = "sha256:" + lowerHex(SHA-256(UTF8(namespace + "\n") || deterministicJson(object)))`로 계산한다. `deterministicJson`은 6.1 규약의 마지막 LF까지 포함하며 모든 object key와 null 여부는 아래 그대로다.

- `material_key`: namespace `sfep-material-key/v1`, object `{"chargeId":charge_id,"slabNo":slab_no}`. 두 값은 9절에서 정규화한 non-empty string이며 최초 CAST부터 AP까지 바뀌지 않는다.
- `batch_id`: namespace `sfep-batch-id/v1`. `CAST_DAY`는 `{"batchKind":"CAST_DAY","replayDate":replay_date}`, `FURNACE_HOUR`는 `{"batchKind":"FURNACE_HOUR","replayDate":replay_date,"replayHour":replay_hour}`, `AP_DAY`는 `{"batchKind":"AP_DAY","replayDate":replay_date}`다.
- `equipment_batch_id`: `batch_kind=FURNACE_HOUR AND equipment_type=FURNACE`일 때만 namespace `sfep-equipment-batch-id/v1`, object `{"batchId":batch_id,"equipmentId":equipment_id,"equipmentType":"FURNACE"}`로 계산한다. 같은 batch의 `RM4_RECORDED`와 다른 batch kind에서는 CSV 빈값이다.
- `event_id`: namespace `sfep-event-id/v1`, object `{"batchId":batch_id,"batchStep":batch_step,"equipmentBatchId":equipment_batch_id 또는 null,"equipmentId":equipment_id,"equipmentType":equipment_type,"materialKey":material_key}`다.

`material_key`는 하나의 `charge_id+slab_no` preimage에만, 각 나머지 ID도 하나의 정의된 preimage에만 대응해야 한다. 같은 ID에 다른 preimage가 나오거나 같은 `event_id`가 두 행에 나오면 분석을 실패한다. Java도 원시 식별 열과 행 필드로 네 ID를 재계산하고 유일성을 확인한다.

실제 CSV 행 순서는 다음 tuple의 오름차순으로 고정한다.

```text
replay_date,
batch_kind_rank(CAST_DAY=0,FURNACE_HOUR=1,AP_DAY=2),
replay_hour_sort(DAY=-1, FURNACE_HOUR=0..23),
batch_id UTF-8 bytes,
batch_step_rank(CAST_RECORDED=0,FURNACE_CHARGED=10,PREHEAT_COMPLETE=11,
                HEAT_COMPLETE=12,SOAK_COMPLETE=13,FURNACE_EXTRACTED=14,
                RM4_RECORDED=15,AP_RECORDED_WITH_RESULT=20),
equipment_sort(FURNACE는 0+호기 정수 1..4, 나머지는 1+equipment_id UTF-8 bytes),
material_key UTF-8 bytes,
event_id UTF-8 bytes
```

이 정렬은 같은 시간대의 모든 호기를 단계별 lockstep으로 만든다. 입력 행 순서는 tie-breaker로 쓰지 않는다. Java는 파일 전체가 이 순서로 단조 증가하는지 확인한 뒤에만 재생하며, 완전히 같은 sort tuple은 중복 오류다.

## 7. 코드와 책임 경계

### 7.1 Python 오프라인 분석기

새 디렉터리는 `analysis/equipment_quality/`로 한다.

재현환경은 이 로컬 시연기의 지원대상을 `Darwin/arm64` 하나로 제한하고 CPython `3.12.10`으로 고정한다. 다른 OS·architecture에서는 Bundle 생성을 거부하며 Java 소비만 허용한다. `analysis/.python-version`, `analysis/pyproject.toml`, `analysis/bootstrap.lock`, `analysis/build-requirements.lock`, `analysis/requirements.lock`, `analysis/wheelhouse.lock.json`, 구현 후 봉인하는 `analysis/producer.lock`과 `analysis/producer_runtime.json`을 버전 관리한다. 초기 직접 의존성은 `numpy==2.2.6`, `pandas==2.3.0`, `jsonschema==4.24.0`, 테스트용 `pytest==8.4.1`이며 SciPy 없이 표준 `math.erfc`와 12.1.1 수식을 사용한다. 모든 lock은 전이 의존성과 배포파일 SHA-256을 포함하고 pip도 `25.1.1`로 고정한다. `wheelhouse.lock.json`은 이 target에 실제 선택한 제3자 wheel filename·compatibility tag·archive SHA-256을 고정하며 제3자 sdist와 로컬 재컴파일을 금지한다.

`producer_runtime.json`은 다음 실행 정체성을 필수로 기록한다.

- `platformSystem=Darwin`, `platformMachine=arm64`, macOS product version, `sysconfig.get_platform()`
- Python 구현·정확한 버전·`python_build()`·cache tag·SOABI·interpreter executable SHA-256
- pip 버전, pyproject·bootstrap/build/runtime/producer lock·wheelhouse lock SHA-256
- 모든 직접/전이 배포판의 name·version·설치 wheel filename/tag/archive SHA-256과 `dist-info/RECORD` 검증 후 계산한 installed-code-tree SHA-256
- `equipment_quality` package version, producer wheel filename/archive SHA-256·installed-code-tree SHA-256과 `sfep-source-lines/v1`으로 정렬한 producer source 상대경로·파일 SHA-256 preimage의 digest
- `PYTHONHASHSEED=0`, `TZ=Asia/Seoul`, locale-independent parsing, binary64 정책

installed-code-tree digest는 wheel의 RECORD 대상 중 실제 `purelib`·`platlib` 아래에 설치된 의미 있는 파일만 사용한다. 디렉터리, `__pycache__`, `.pyc`, `*.dist-info/RECORD`, `INSTALLER`, `direct_url.json`, `REQUESTED`와 가상환경 `bin/`의 생성 console script는 제외하고, 나머지 package/data/native-library/metadata bytes를 site-packages 상대경로 UTF-8 byte 순으로 정렬해 `path=sha256:<digest>\n`로 연결한 bytes의 SHA-256이다. producer source digest는 `analysis/equipment_quality/**`와 producer 동작에 영향을 주는 packaging 파일을 같은 방식으로 계산하며 생성산출물·가상환경·cache·fixture·runtime manifest·seal 도구는 제외한다.

검증 책임은 build/seal과 분석기 시작으로 나눈다. producer build helper는 source digest를 먼저 계산해 package 안의 고정 resource `sfep_producer_provenance.json`에 package version, source digest, `SOURCE_DATE_EPOCH`와 제3자 lock digest를 넣고 같은 source를 두 번 wheel로 만든다. producer를 import하지 않는 stdlib-only seal 도구군은 명시적으로 받은 source root, 두 wheel directory, wheelhouse와 lock 경로를 검증한다. 두 directory에 wheel이 정확히 하나씩 있고 archive SHA-256이 같을 때만 build seal 도구가 wheel을 검증 wheelhouse에 게시하고 `producer.lock`을 쓴다. runtime venv 설치 뒤 runtime seal 도구가 원본 source digest, 게시 wheel archive digest, 실제 installed-code-tree, interpreter/platform과 모든 lock digest를 확인해 `producer_runtime.json`을 봉인한다. 이 build/seal 단계만 원본 source tree와 wheel archive를 검증한다.

분석기 시작 시에는 별도 source root나 wheel archive를 요구하지 않는다. 대신 runtime manifest schema와 bytes, OS/architecture, CPython build, realpath 대상 interpreter bytes, 환경정책, 설치 distribution version·installed-code-tree를 다시 계산하고, 설치 package의 `sfep_producer_provenance.json`을 runtime manifest의 source/build provenance와 exact-match한다. wheel archive SHA는 build/seal attestation으로 보존하되 실행 시 복원할 수 있다고 주장하지 않는다. dependency와 producer 모두 검증된 wheelhouse에서 wheel로만 설치하며 `pip install ./analysis`는 금지한다. 서로 다른 두 절대경로의 새 venv에 같은 wheelhouse를 설치했을 때 runtime 검증결과와 생성 Bundle bytes가 같아야 한다. runtime manifest와 embedded provenance에는 절대경로, 설치시각과 venv 이름을 넣지 않는다. producer 코드·Python build·OS·wheel 또는 설치 bytes가 바뀌면 기존 runtime manifest 검증 또는 build/seal이 실패하고, 검토된 새 runtime file의 SHA-256이 두 ID를 바꾼다.

재현 명령은 다음 순서로 고정한다.

```text
python3.12 -m venv /absolute/sfep-build-venv
/absolute/sfep-build-venv/bin/python -m pip install --no-index --only-binary=:all: --require-hashes --find-links /absolute/verified-wheelhouse -r analysis/bootstrap.lock
/absolute/sfep-build-venv/bin/python -m pip install --no-index --only-binary=:all: --require-hashes --find-links /absolute/verified-wheelhouse -r analysis/build-requirements.lock
SOURCE_DATE_EPOCH=1735689600 /absolute/sfep-build-venv/bin/python -m build --wheel --no-isolation --outdir /absolute/wheel-build-a /absolute/repo/analysis
SOURCE_DATE_EPOCH=1735689600 /absolute/sfep-build-venv/bin/python -m build --wheel --no-isolation --outdir /absolute/wheel-build-b /absolute/repo/analysis
/absolute/sfep-build-venv/bin/python /absolute/repo/analysis/tools/seal_producer_build.py --source-root /absolute/repo/analysis --wheel-dir-a /absolute/wheel-build-a --wheel-dir-b /absolute/wheel-build-b --wheelhouse /absolute/verified-wheelhouse --producer-lock /absolute/repo/analysis/producer.lock
python3.12 -m venv /absolute/sfep-runtime-venv
/absolute/sfep-runtime-venv/bin/python -m pip install --no-index --only-binary=:all: --require-hashes --find-links /absolute/verified-wheelhouse -r analysis/bootstrap.lock
/absolute/sfep-runtime-venv/bin/python -m pip install --no-index --only-binary=:all: --require-hashes --find-links /absolute/verified-wheelhouse -r analysis/requirements.lock
/absolute/sfep-runtime-venv/bin/python -m pip install --no-index --only-binary=:all: --require-hashes --find-links /absolute/verified-wheelhouse -r analysis/producer.lock
/absolute/sfep-build-venv/bin/python /absolute/repo/analysis/tools/seal_runtime.py --source-root /absolute/repo/analysis --producer-wheel-dir /absolute/verified-wheelhouse --producer-lock /absolute/repo/analysis/producer.lock --runtime-venv /absolute/sfep-runtime-venv --output /absolute/repo/analysis/producer_runtime.json
PYTHONHASHSEED=0 TZ=Asia/Seoul /absolute/sfep-runtime-venv/bin/python -m equipment_quality.cli --config /absolute/repo/analysis/analysis_config.json --runtime-manifest /absolute/repo/analysis/producer_runtime.json --data-dir /absolute/steel-data --output-dir /absolute/output
```

| 구성요소 | 책임 |
|---|---|
| `schema.py` | CP949 입력, 정확한 컬럼·타입·값 범위 검증 |
| `genealogy.py` | 엄격한 키 조인, 중복·미연결 행 격리 |
| `feature_roles.py` | 감시값·제품조건·설비구분·식별값·결과값 분류 |
| `operating_ranges.py` | 설비와 제품조건별 역사적 전형 조업범위 계산 |
| `quality_intervals.py` | `judge` 구간별 불량률·상대위험·신뢰구간 계산 |
| `event_builder.py` | 실제 시간 정밀도를 보존한 재생 이벤트 생성 |
| `artifacts.py` | 임시 디렉터리·잠금·Manifest를 이용한 결정론적 원자 산출물 생성 |
| `cli.py` | 단일 분석 명령과 오류코드 제공 |

분석 산출물 위치는 `--output-dir` 절대경로로만 지정하고 Git에 추가하지 않는다. 권장 로컬 부모경로는 저장소의 `var/equipment-quality/`이며 이 디렉터리 전체를 `.gitignore`에 넣는다. CLI가 이 경로를 암묵적으로 추정하지는 않는다. 저장소에는 작은 합성 테스트 fixture만 포함한다.

### 7.2 Java 모니터

새 모니터는 기존 `sfep-server` classpath와 분리한 독립 Gradle 프로젝트 `equipment-monitor/`와 패키지 `com.sfep.equipmentmonitor`에 둔다. 의존성은 core Spring Boot starter, Jackson, Apache Commons CSV, 고정 버전 JSON Schema validator와 테스트 라이브러리만 허용하며 Web, JPA/DataSource, Kafka, Redis 의존성을 넣지 않는다.

| 구성요소 | 책임 |
|---|---|
| `baseline` | 내장 schema digest, Manifest, JSON 스키마·버전·산출물 해시·`bundleId`·`criteriaId` 검증과 로딩 |
| `replay` | 시작·정지·일시정지·한 단계·속도조절 |
| `state` | 소재별 재생단계와 설비별 최근 재생상태 관리 |
| `risk` | 역사적 조업범위 편차와 품질위험 규칙 적용 |
| `alert` | 경보 생성, 중복억제, 이력 관리 |
| `desktop` | Swing 탭·패널·표·상세 설명 표시 |

Swing Event Dispatch Thread는 화면 갱신만 담당한다. CSV 읽기와 재생 대기는 백그라운드 실행기로 처리해 UI 정지를 방지한다.

monitor의 `bootJar.mainClass`와 `bootRun.mainClass`는 `EquipmentMonitorDesktopApplication`으로 명시한다. 이 main은 `SpringApplicationBuilder.headless(false).web(WebApplicationType.NONE)`로 monitor 패키지만 시작한다. 기존 `sfep-server`의 main·build·bootJar는 그대로 유지한다. 실행계약은 `./sfep-server/gradlew -p equipment-monitor bootRun --args='--sfep.equipment-monitor.bundle-dir=/absolute/bundle/path'`, 배포물은 monitor 전용 bootJar 한 개다.

`sfep.equipment-monitor.bundle-dir`은 필수 절대경로이고 기본 상대경로는 두지 않는다. Java 21과 `java.desktop`을 요구하며 headless 환경이면 해결방법이 포함된 명확한 오류로 종료한다. 창을 닫으면 재생 실행기와 Spring Context를 순서대로 닫고 JVM이 남지 않아야 한다. 경보 이력은 프로세스 메모리에만 보관하고 사용자가 선택한 경우 UTF-8 CSV로 내보낸다.

## 8. 컬럼 역할과 공개시점

### 8.1 직접 조업값

작업자가 조업조건으로 확인할 수 있는 값이며, 역사적 조업범위 편차와 품질위험 연관성을 모두 계산한다.

- 제강·연주: `tundish_temp`, `mlac_ratio`
- 가열로: `f_jangip_temp`, `f_bfg`, `f_cog`, `f_ldg`, 각 가스 비율, 예열·가열·균열 온도와 시간
- 조압연: `rm4_temp`, `rm_pitch`
- AP: `ap_line_speed`

### 8.2 제품·소재 상태값

`delta_ferrite`, `ingre_cr`, `ingre_ni`, `ingre_s`, `slab_grind`, `slab_width`, `hr_thick`, `hr_width`, `ap_thick`, `ap_width`는 품질위험 근거와 제품구성 설명에는 사용한다. 그러나 이 값만으로 “역사적 조업범위 편차”를 발생시키지 않는다.

### 8.3 비교조건과 설비 구분

- 제품조건: `steel_grade`, `steel_usage`, `cc_gubun`, `slab_gubun`, `f_jangip_gubun`, 단계별 치수 구간
- 설비·운영조건: `sm_plant`, `furnace_no`, `ap_plant`, `ap_shift`
- 시간 드리프트 확인: 생산 월과 생산일. 시간 자체에는 위험 임계치를 만들지 않는다.

비교조건은 서로 다른 제품구성 때문에 생기는 차이를 설비 이상으로 오인하지 않도록 층화할 때 사용한다. 어떤 값이 판정 대상인지 비교조건인지와 관계없이, 현재 공정단계에 아직 도착하지 않은 칼럼은 층화에도 사용할 수 없다.

### 8.4 식별·시간·결과

- 식별: `charge_id`, `slab_no`, `hr_coil_id`, `ap_prod_id`
- 시간: `cast_date`, `f_ext_date`, `f_ext_time`, `hr_date`, `ap_date`
- 결과: `judge`

식별·시간에는 정상·위험 임계치를 만들지 않는다. `judge`는 품질위험 구간 생성과 AP 결과 확인에만 사용한다.

### 8.5 단계별 최초 사용 가능 필드

다음 표는 경보 대상값과 층화조건 모두에 적용하는 보수적인 `firstAvailableStage` 계약이다.

| 이벤트 | 이때부터 사용할 수 있는 필드 |
|---|---|
| `CAST_RECORDED` | SM/CC 파일의 모든 값. 단, `judge`와 후속 공정값은 제외 |
| `FURNACE_CHARGED` | `furnace_no`, `f_jangip_gubun`, `f_jangip_temp`, `slab_width` |
| `PREHEAT_COMPLETE` | `f_pre_temp`, `f_pre_interval` |
| `HEAT_COMPLETE` | `f_heat_temp`, `f_heat_interval` |
| `SOAK_COMPLETE` | `f_sock_temp`, `f_sock_interval` |
| `FURNACE_EXTRACTED` | `f_bfg`, `f_cog`, `f_ldg`, 세 가스 비율, `f_ext_date`, `f_ext_time` |
| `RM4_RECORDED` | `hr_coil_id`, `hr_date`, `hr_thick`, `hr_width`, `rm4_temp`, `rm_pitch` |
| `AP_RECORDED_WITH_RESULT` | `ap_plant`, `ap_prod_id`, `ap_date`, `ap_shift`, `ap_thick`, `ap_width`, `ap_line_speed`, `judge` |

가스값은 공정 중 언제 측정되었는지 알 수 없으므로 추출 완료 전에는 공개하지 않는다. AP 값과 `judge`도 일 단위 동일 행이므로 AP 값으로 AP 결과를 “조기” 경보하지 않는다. AP 단계 분석은 후행 설명과 이력 보고에만 쓴다.

## 9. 데이터 정제와 계보

1. 세 파일을 바이트 스냅샷으로 읽고 SHA-256을 기록한다.
2. CP949로 파싱한 뒤 정확한 필수 컬럼과 허용 타입을 검증한다.
3. 원본 문자열 ID의 공백만 정규화하고 값을 임의 변환하지 않는다.
4. SM/CC와 FUR/HR는 `charge_id + slab_no`로 조인한다.
5. FUR/HR와 AP는 `hr_coil_id`로 조인한다.
6. 중복 키, 연결 불능, 필수 ID 결측은 격리 목록에 기록한다.
7. 모호한 중복 중 하나를 임의로 선택하지 않는다.
8. 감시값 결측은 평균으로 채우지 않고 이벤트에 `DATA_MISSING`으로 기록한다.
9. 조인 전후 행수, 격리 사유와 건수를 `analysis_summary.json`에 남긴다.
10. 모든 산출 필드에 원본 파일·행, 변환 규칙과 `firstAvailableStage`를 추적할 수 있는 계보를 남긴다.

## 10. 시점 기준과 시간 분리

기본 모니터링 모드는 과거에 확정된 정보만으로 이후 데이터를 감시하는 상황을 재현한다. 다만 현재 전체 자료를 이미 탐색해 38일 정책과 평가분포를 확인했으므로 뒤 구간은 독립 외부검증이 아니라 **`LOCKED_RETROSPECTIVE_HOLDOUT`**으로 부른다.

1. AP 연결이나 `judge`를 보지 않고 FUR/HR 원본에서 필수 ID·`hr_date`가 유효하고 `charge_id+slab_no`가 유일한 행만 경계 모집단으로 삼는다. 그 행수를 `N`이라 한다.
2. `T`는 날짜 `d`에 대해 `count(hr_date <= d) >= ceil(0.70 × N)`을 처음 만족하는 날짜다. 날짜는 쪼개지 않고 `T`를 재계산하지 않는다.
3. 완성 계보 중 `hr_date <= T`는 기준 후보, `hr_date > T`는 시간 홀드아웃이다.
4. 동일 `charge_id`가 양쪽에 걸치면 경계 확정 뒤 그 Charge의 모든 행을 양쪽에서 제거하고 건수를 보고한다.
5. 역사적 전형 조업범위는 기준 후보 중 해당 값의 실제 공정 이벤트가 `T`까지 도착한 행만 사용한다.
6. 품질위험 기준은 `hr_date <= T - LABEL_MATURITY_DAYS`이면서 `ap_date <= T`인 소재만 사용한다.
7. `LABEL_MATURITY_DAYS=38`은 분석기가 입력에서 재추정하지 않는 `quality-analysis-v1` 정책상수다. 전체 회고 snapshot의 지연을 보고 선택했으므로 현재 홀드아웃은 전향 증거가 아니며, 새 미래자료 평가 전에 이 값을 동결해야 독립 검증으로 인정한다.
8. 성숙조건을 만족하지 않은 기준 후보는 양품으로 간주하지 않고 `LABEL_NOT_YET_AVAILABLE`로 검열·집계한다.
9. 성숙한 품질기준 행수 `N_inner`에 대해 같은 공식으로 내부 경계 `D`를 정한다. `hr_date <= D`는 탐색, `hr_date > D`는 확인구간이며 교차 Charge는 양쪽에서 제거하고 `D`는 재계산하지 않는다.
10. 위험후보, 분위대, 층화·완화 선택과 모든 수치기준은 탐색구간에서만 만들고 확인구간은 고정 후보의 재현성 확인에만 사용한다.
11. 시간 홀드아웃은 규칙, 임계치, 층화, 최소표본 또는 등급을 조정하는 데 사용하지 않는다.
12. 재생 중 AP 값과 `judge`는 실제 `ap_date`의 `AP_RECORDED_WITH_RESULT` 이벤트에서만 함께 공개한다.

산출물은 `asOf=T`, `D`, `LABEL_MATURITY_DAYS`, 탐색·확인·홀드아웃별 전체/불량/검열/Charge 제거 건수와 날짜범위를 기록한다. Charge 경계 제거까지 적용한 예비 점검에서는 품질기준이 약 5,462건·불량 52건, 내부 탐색이 약 4,111건·불량 41건, 확인이 약 1,351건·불량 11건이므로 최종 산출물에서 정확한 수를 다시 검증한다.

누출 불변성 fixture는 경계·키를 고정한 채 연결된 `hr_date>T` 소재의 criteria 관련 SM/CC·FUR/HR·AP 비식별 feature와 `judge`를 모두 변조한다. 이때 두 criteria JSON과 `criteriaId`는 byte-for-byte 불변이고 원본 SHA·`bundleId`·재생·요약은 변해야 한다. 반대로 `hr_date<=T` 적합대상의 사용 feature 또는 성숙 `judge` 하나를 변조하면 `criteria_projection_sha256`과 `criteriaId`가 변해야 한다. `hr_date`, `ap_date`와 ID는 이 fixture에서 변조하지 않는다.

홀드아웃 평가 기본단위는 최종 Label이 도착한 `hr_coil_id` 한 건이다. `T` 뒤 의사결정 이벤트에서 AP 결과 공개 전 `earlyWarningEligible=true`인 규칙의 `DANGER`가 한 번이라도 발생하면 alert-positive, 최종 `judge`가 불량이면 label-positive다. `CAUTION`까지 포함한 결과는 보조지표로 따로 보고한다.

```text
N                  = TP + FP + TN + FN
alertRate          = (TP + FP) / N
precision          = TP / (TP + FP)
recall             = TP / (TP + FN)
baseDefectRate     = (TP + FN) / N
lift               = precision / baseDefectRate
falseAlertsPer100  = 100 * FP / N
```

95% CI는 정렬된 고유 `charge_id` K개에서 매 replicate마다 K개를 복원추출하고 선택된 Charge의 모든 Coil을 multiplicity 그대로 넣는 block bootstrap 2,000회다. seed bytes는 UTF-8 `criteriaId + NUL + "holdout-bootstrap-v1"`의 SHA-256이고 replicate/draw index 생성은 12.1.1과 같은 `SHA-256(seedBytes || uint64be(r) || uint64be(j)) mod K` 규약을 쓴다. 각 지표의 유효 replicate에 type-1 2.5·97.5백분위를 적용한다. 분모가 0인 점추정과 replicate는 0이 아니라 null이며 `reasonCode=ZERO_DENOMINATOR`와 유효 replicate 수를 기록한다. 유효 replicate가 1,900개 미만이면 해당 CI는 null이고 `reasonCode=TOO_FEW_VALID_BOOTSTRAPS`다. 정상은 `reasonCode=NONE`이다.

예비 점검에서 기준구간 불량률은 약 1.96%, 홀드아웃은 약 4.32%로 차이가 있었다. 설비·단계별 표본, 불량률, 신뢰구간과 분포·불량률 드리프트를 명시하되, 그 결과로 기준을 다시 맞추지 않는다.

`전체 이력 재생 모드`에서는 모든 행을 화면에 재생할 수 있다. 이 모드는 공정 시연과 이력 조회용이며 독립 성능검증으로 보고하지 않는다.

## 11. 역사적 전형 조업범위

이 범위는 건강한 설비의 인증 정상범위가 아니라 과거 관측분포의 **역사적 전형범위**이며, `judge` 품질위험과 독립적으로 계산한다. 직접 조업값만 설비 편차 경보를 만들고 제품·소재 상태값은 분포 참고자료로만 보관한다.

1. 직접 조업값을 설비와 현재 단계까지 사용 가능한 비교조건별로 묶는다.
2. 그룹 표본이 400건 이상일 때만 `median`, `p05`, `p95`를 계산한다. 이는 각 꼬리에 최소 20건을 확보하기 위한 기준이다.
3. 그룹 표본이 2,000건 이상일 때만 `p01`, `p99` 심한 편차 경계를 추가한다.
4. 경험적 분위수는 Hyndman-Fan type 1과 같은 `inverted_cdf` 방법으로 계산하고 같은 값은 절대 나누지 않는다.
5. `p05 <= x <= p95`는 전형범위, `p01 <= x < p05` 또는 `p95 < x <= p99`는 주의 편차, `x < p01` 또는 `x > p99`는 심한 편차다. 1/99 경계가 없는 그룹은 심한 편차를 판정하지 않는다.
6. 동률 질량 때문에 한쪽의 1%와 5% 경계 또는 95%와 99% 경계가 같은 값으로 붕괴하면 그 꼬리에서는 편차 경보를 만들지 않는다.
7. 표본이 부족하면 현재 단계에서 이미 공개된 조건만 사용해 아래 행의 왼쪽부터 오른쪽 순서로 완화하고, 400건을 처음 만족한 수준에서 멈춘다.

| 공정 | 고정 완화 순서 |
|---|---|
| SM/CC | `sm_plant+steel_grade+steel_usage` → `sm_plant+steel_grade` → `sm_plant` |
| 가열로 | `furnace_no+steel_grade+steel_usage+f_jangip_gubun+slab_width_band` → `furnace_no+steel_grade+f_jangip_gubun` → `furnace_no+f_jangip_gubun` → `furnace_no` |
| RM4 | `steel_grade+steel_usage+hr_thick_band+hr_width_band` → `steel_grade+steel_usage` → `steel_grade` → RM4 전체 |
| AP | `ap_plant+steel_grade+steel_usage+ap_shift+ap_thick_band+ap_width_band` → `ap_plant+steel_grade+steel_usage` → `ap_plant+steel_grade` → `ap_plant` |

치수 band는 기준후보에서 동률을 보존한 type-1 사분위로 한 번 정해 고정한다. RM4 식별번호가 없으므로 RM4 범위는 특정 기계가 아닌 공정 수준 기준이라고 표시한다. 마지막 수준에서도 400건이 안 되면 범위를 억지로 만들지 않고 `INSUFFICIENT_EVIDENCE`로 둔다. 사용한 기준 수준, 표본 수, 분위수 방식과 대체 여부를 모든 판정에 포함한다.

## 12. `judge` 품질위험 구간

8.1의 모든 직접 조업값과 8.2의 모든 제품·소재 상태값을 빠짐없이 분석한다. 8.3의 제품·설비 범주도 후보 자신을 층화에서 제외하는 조건으로 범주위험을 계산한다. 식별자·시간·`judge` 자체만 후보에서 제외한다. 각 대상은 위험구간이 없더라도 `NORMAL`, `UNCONFIRMED` 또는 `INSUFFICIENT_EVIDENCE` 결과를 남겨 “분석하지 않음”과 “근거 없음”을 구분한다.

v1의 숫자·상호작용 품질구간은 소수의 불량 Label을 호기별로 다시 쪼개지 않고 제품·설비구성을 보정한 `PROCESS_GLOBAL` 규칙으로 만든다. 호기별 직접 조업 편차는 11절의 equipment-specific 전형범위가 담당한다. `furnace_no`, `sm_plant`, `ap_plant` 자체의 범주위험만 해당 ID의 `EQUIPMENT_SPECIFIC` 품질규칙이 된다.

### 12.1 공통 통계 계약

분석 설정 `quality-analysis-v1`은 다음 값을 버전 관리하며 시간 홀드아웃 결과로 바꾸지 않는다.

| 상수 | 값 |
|---|---:|
| `MIN_DISCOVERY_SUPPORT` | 200 |
| `MIN_CAUTION_DEFECTS` | 5 |
| `MIN_DANGER_DEFECTS` | 10 |
| `MIN_CONFIRM_SUPPORT` | 100 |
| `MIN_CONFIRM_DEFECTS` | 5 |

숫자형 변수는 탐색구간에서 동률을 보존한 경험적 10분위 후보구간을 만든다. 각 후보의 비교군은 동일한 사전정의 층 안의 나머지 구간이다. 범주형 변수의 비교군도 동일 층 안의 나머지 범주다. 단계별 층화는 아래 행에서 왼쪽부터 오른쪽으로만 완화한다.

| 단계 | 고정 층화 완화 순서 |
|---|---|
| SM/CC | `[sm_plant,steel_grade,steel_usage]` → `[steel_grade,steel_usage]` → `[steel_grade]` → `[]` |
| 가열로 | `[furnace_no,f_jangip_gubun,steel_grade,steel_usage,slab_width_band]` → `[furnace_no,f_jangip_gubun,steel_grade,steel_usage]` → `[furnace_no,f_jangip_gubun,steel_grade]` → `[furnace_no,f_jangip_gubun]` → `[furnace_no]` → `[]` |
| RM4 | `[steel_grade,steel_usage,hr_thick_band,hr_width_band]` → `[steel_grade,steel_usage]` → `[steel_grade]` → `[]` |
| AP 이력 보고 | `[ap_plant,ap_shift,steel_grade,steel_usage,ap_thick_band,ap_width_band]` → `[ap_plant,steel_grade,steel_usage]` → `[ap_plant,steel_grade]` → `[ap_plant]` → `[]` |

후보 `x`를 검정할 때는 `x`, `bin(x)`, 결정적 파생값과 후보 상호작용의 두 축을 모든 층화수준에서 먼저 제거한다. 후보와 같은 이벤트에서 처음 공개되는 다른 **연속 조업 측정값**도 사후 proxy가 될 수 있어 층화에서 제거한다. 후보가 아닌 설비 ID, 장입방식과 제품·소재 조건은 그 이벤트까지 공개됐다면 남길 수 있다. AP 치수는 사전고정 AP 상호작용 외의 단일변수 보정에는 쓰지 않는다. 따라서 `furnace_no` 자체를 비교하면서 `furnace_no`로 층화하지 않는다. 탐색구간에서 후보와 비교군이 모두 존재하는 층을 informative stratum으로 정의한다. 총 최소표본 조건과 informative strata 2개를 처음 만족하는 수준을 고정하며, 어디서도 만족하지 못하면 최종 `[]` 한 층의 `UNADJUSTED_FALLBACK`을 사용한다. 이 fallback은 제품구성 보정이 없음을 표시하고 최대 `CAUTION`까지만 허용한다.

확인구간은 탐색에서 고정된 경계·층·가중치를 그대로 사용한다. 고정 층 중 후보와 비교군이 모두 있는 층만 남기고 탐색 가중치를 재정규화한다. `STRATIFIED`는 informative strata가 2개 미만이면, `UNADJUSTED_FALLBACK`은 전역 후보·비교군 중 하나라도 없으면 `INSUFFICIENT_EVIDENCE`다. 두 방식 모두 확인 최소표본·불량을 충족해야 한다. 후보와 비교군의 제품구성 차이는 탐색구간 층 가중치를 고정한 직접 표준화 불량률로 표시하고, Mantel-Haenszel 공통 상대위험과 95% 신뢰구간으로 검정한다. 구간 자체 불량률의 표시용 95% 구간은 Wilson 방법을 쓴다.

#### 12.1.1 단일 계산식과 0-cell 계약

informative stratum `s`에서 `a_s=후보 불량`, `b_s=후보 양품`, `c_s=비교군 불량`, `d_s=비교군 양품`으로 둔다. support와 최소 불량 판정에는 보정하지 않은 실제 정수만 쓴다. `MIN_DISCOVERY_SUPPORT`와 `MIN_CONFIRM_SUPPORT`는 후보 support와 비교군 support가 **각각** 만족해야 하고 최소 불량수는 후보 불량수에 적용한다.

탐색구간의 직접표준화 가중치와 표시 통계는 다음으로 고정한다. 확인구간은 남아 있는 고정 층에 대해 탐색 가중치만 합이 1이 되게 재정규화한다.

```text
n1_s = a_s + b_s
n0_s = c_s + d_s
w_s  = (n1_s + n0_s) / Σ_s(n1_s + n0_s)
adjustedRate            = Σ_s w_s * (a_s / n1_s)
comparatorAdjustedRate  = Σ_s w_s * (c_s / n0_s)
riskDifference          = adjustedRate - comparatorAdjustedRate
```

Mantel-Haenszel RR 계산에서 한 stratum의 네 cell 중 하나라도 0이면 **그 stratum의 네 cell 모두에만 0.5**를 더한다. support·불량수·표준화율·CMH p-value에는 이 보정을 쓰지 않는다. 보정 뒤 `n_s=n1_s+n0_s`일 때 점추정은 다음이다.

```text
relativeRisk = [Σ_s (a_s * n0_s / n_s)] / [Σ_s (c_s * n1_s / n_s)]
```

양측 p-value는 보정 전 정수 cell의 continuity correction 없는 CMH 정규근사로 고정한다.

```text
E_s = n1_s * (a_s + c_s) / n_s
V_s = n1_s*n0_s*(a_s+c_s)*(b_s+d_s) / [n_s^2*(n_s-1)]
Z   = Σ_s(a_s-E_s) / sqrt(Σ_s V_s)
p   = erfc(abs(Z) / sqrt(2))
```

`ΣV_s=0`이면 `p=1`, `reasonCode=ZERO_VARIANCE`다. 표시용 개별 불량률 95% Wilson 구간은 `z=1.959963984540054`와 보정 전 `a_s,b_s` 합계로 계산한다.

RR 95% CI는 탐색 Charge block bootstrap 2,000회 percentile 구간이다. 정렬된 고유 Charge `K`개에서 매 replicate마다 `K`개를 복원추출하고 선택된 Charge의 모든 Coil을 multiplicity 그대로 넣어 위 RR을 다시 계산한다. seed bytes는 `SHA-256(criteriaId + NUL + ruleId + NUL + "rule-ci-v1")`이다. replicate `r=0..1999`, draw `j=0..K-1`의 index는 `SHA-256(seedBytes || uint64be(r) || uint64be(j))` 첫 8바이트를 unsigned big-endian 정수로 읽어 `mod K`한 값이다. 유한한 양수 RR replicate의 type-1 2.5·97.5백분위를 쓰며 유효 replicate가 1,900개 미만이면 CI는 null, `reasonCode=TOO_FEW_VALID_BOOTSTRAPS`이고 `DANGER`가 될 수 없다.

후보 family는 Label을 보지 않고 만들어진 **고유하고 비어 있지 않은 predicate 전부**다. `ruleId`는 Label·통계·등급을 제외한 `analysisFamily,fieldNames,predicate,firstAvailableStage,equipmentType,applicationScope,equipmentId,applicationContext,adjustmentLevel,adjustmentFieldsDropped,adjustmentKind`의 결정론적 JSON bytes SHA-256으로 등급 판정 전에 만든다. 따라서 다른 호기·제품 context·적용범위는 같은 ID를 공유하지 않는다. 동률로 같은 숫자 경계가 생기면 중복 predicate를 하나로 만들고, 상호작용 축은 실제 고유 band만 사용한다. 한 축이 2개 미만이면 해당 상호작용은 `NO_VARIATION`으로 분석하지 않으며, 빈 joint cell은 후보로 만들지 않는다. 생성된 후보가 support·결측·계산조건을 만족하지 못해도 `p=1`로 해당 BH family에 포함한다.

탐색구간의 BH-FDR family는 정확히 세 개다. (1) 모든 숫자형 변수의 모든 10분위 후보, (2) 모든 범주형 변수의 모든 범주 후보, (3) 사전고정 상호작용의 모든 3×3 cell이다. 각 family 전체 p-value에 한 번 Benjamini-Hochberg를 적용한다. 확인구간과 시간 홀드아웃에서는 후보를 새로 찾거나 FDR 기준을 다시 맞추지 않는다.

탐색 판정은 다음과 같다.

- `discoveryCautionPass`: 표본 ≥200, 불량 ≥5, 보정 상대위험 ≥1.5, 보정 위험차 ≥0.005, BH q≤0.10.
- `discoveryDangerPass`: 위 조건 전부 + 불량 ≥10, 보정 상대위험 ≥2.0, 보정 위험차 ≥0.01, 상대위험 95% CI 하한 >1, BH q≤0.05.
- `confirmationCautionPass`: 표본 ≥100, 불량 ≥5, 상대위험 >1.0, 위험차 >0.
- `confirmationDangerPass`: 표본 ≥100, 불량 ≥5, 상대위험 ≥1.5, 위험차 >0.

최종등급 우선순위는 다음으로 고정한다.

1. 탐색 또는 확인 최소표본·불량 미달: `INSUFFICIENT_EVIDENCE`.
2. `discoveryDangerPass AND confirmationDangerPass`이며 `STRATIFIED`: `DANGER`.
3. `(discoveryCautionPass OR discoveryDangerPass) AND confirmationCautionPass`: `CAUTION`.
4. 탐색은 CAUTION 이상이지만 표본이 충분한 확인구간에서 상승방향이 반복되지 않음: `UNCONFIRMED`.
5. 나머지: `NORMAL`.

`UNCONFIRMED`는 관리기준 화면에만 보이고 재생 경보에는 사용하지 않는다. 위험구간은 강제로 만들지 않으며 `DANGER`가 0개인 결과도 정상적인 분석 결과로 보고한다. 실제 숫자 경계는 원본 데이터 분석 결과로만 생성한다.

인접 숫자구간은 각각 독립적으로 같은 등급을 얻은 뒤에만 화면 표시용으로 합친다. 합친 구간의 통계를 다시 계산해 후보선택이나 등급상향에 사용하지 않는다.

### 12.2 범주형 변수

설비, 공장, 장입방식, 작업조 같은 범주는 범주별 전체 건수, 불량 건수, 직접 표준화 불량률, Mantel-Haenszel 상대위험과 신뢰구간을 계산한다. 숫자형과 동일한 최소표본, FDR, 확인구간 계약을 적용한다. 설비 식별값의 연관성은 “해당 설비군에서 관측된 품질위험”으로 표현하며 고장 원인으로 단정하지 않는다.

### 12.3 상호작용

조합 폭발을 막기 위해 다음 2개 변수 조합만 사전에 고정한다. 각 숫자축은 탐색구간에서 동률을 보존한 type-1 3분위로 나누고, 3×3 각 joint cell을 동일 층의 나머지 cell 전체와 비교한다.

- `tundish_temp × mlac_ratio`
- `f_pre_interval × f_pre_temp`
- `f_heat_interval × f_heat_temp`
- `f_sock_interval × f_sock_temp`
- `rm_pitch × rm4_temp`
- `ap_line_speed × ap_thick` — AP 이력 보고 전용

`f_jangip_temp`는 `f_jangip_gubun`별로 층화한다. `furnace_no × f_jangip_gubun`은 상호작용이 아니라 비교조건이다. 연료 물리 단위가 확인되기 전에는 가스량을 에너지 상호작용으로 해석하지 않는다. 모든 상호작용에 단일 변수와 같은 최소표본, FDR, 확인구간 계약을 적용한다. 3개 이상 자동 조합탐색과 블랙박스 특성 중요도는 사용하지 않는다.

### 12.4 표현 원칙

경보는 “이 조건이 불량을 발생시켰다”고 표현하지 않는다. 다음 형식만 허용한다.

> 현재까지 관측된 대표 조업값은 과거 동일 조건에서 `judge` 불량률이 통계적으로 높았던 구간에 해당합니다. 해당 조업조건과 소재 상태를 확인하십시오.

“현재 온도가 오르고 있다”, “이 상태가 유지되면 불량이 된다”, “이 설비가 고장났다”와 같은 데이터로 입증할 수 없는 문구는 금지한다.

## 13. 재생 시점 판정과 경보 집계

소재가 역사적 공정을 재생하면서 이미 도착한 값만 누적한다.

```text
제강·연주 기록
→ 장입
→ 예열 완료
→ 가열 완료
→ 균열 완료
→ 가열로 추출
→ RM4 관측
→ AP 조업값과 judge 결과 동시 기록
```

- 품질위험은 개별 규칙이 확정 `DANGER` 구간에 해당하면 그 항목을 `DANGER`, 확정 `CAUTION` 구간에 해당하면 `CAUTION`으로 표시한다.
- 역사적 조업범위 편차는 `전형범위·주의 편차·심한 편차·근거 부족`으로 별도 표시하고 품질위험 등급과 합산하지 않는다.
- 소재 품질위험 요약은 사용 가능한 근거 중 하나라도 `DANGER`이면 `DANGER`, 그렇지 않고 하나라도 `CAUTION`이면 `CAUTION`, 표본이 모두 부족하면 `INSUFFICIENT_EVIDENCE`, 나머지는 `NORMAL`이다.
- 상관된 값의 중복 계산을 막기 위해 근거를 `STEEL_CHEMISTRY`, `CASTING_STABILITY`, `CHARGE`, `FUEL_PROFILE`, `PREHEAT`, `HEATING`, `SOAKING`, `RM4`, `DIMENSIONS`, `AP` family로 묶는다. 가스량과 가스비율은 함께 떠도 `FUEL_PROFILE` 한 family로 센다.
- 여러 `CAUTION` family가 동시에 발생해도 통계등급을 `DANGER`로 올리지 않고 목록 정렬 우선순위만 높인다. 개별 확정 `DANGER`는 그대로 유지한다.
- 같은 설비·같은 규칙이 최근 소재 5건 중 3건 이상에서 반복되면 `반복 확인 필요` 배지를 붙이되 위험등급은 올리지 않는다. 이는 지속시간이 아니라 소재 건수 기반 규칙이다.
- 경보는 `bundleId + materialKey + eventStage + ruleId`당 한 번만 생성하고 모든 원시 근거를 보존한다.
- 최종 요약만 보여주지 않고 개별 구간, 표본 수, 불량 수, 보정률, Wilson 불량률 CI, 상대위험, RR CI 또는 계산불가 reason, q-value와 확인구간 결과를 함께 보존한다.
- AI 확률, 학습된 가중합 또는 임의 점수를 사용하지 않는다.

## 14. 역사적 재생 이벤트와 시간 의미

재생기는 역산시각으로 서로 다른 소재의 순서를 만들지 않고 **날짜·시간대 Batch와 동기화된 단계 substep**을 사용한다.

1. 재생 날짜는 `cast_date`, `f_ext_date`, `ap_date`의 합집합을 오름차순으로 진행한다.
2. 한 날짜 안에서는 `CAST_DAY → FURNACE_HOUR 0..23 → AP_DAY` 순서를 UI 정책으로 사용한다. 이 순서는 일 단위 자료의 실제 시각을 주장하지 않는다.
3. `CAST_DAY`의 모든 소재는 `CAST_RECORDED` 한 Batch로 함께 공개하며 소재 간 순서를 두지 않는다.
4. 같은 `f_ext_date+f_ext_time`의 1~4호기 모든 소재를 하나의 `FURNACE_HOUR` 재생단위로 묶고 호기별 `equipmentBatchId`를 함께 기록한다.
5. 한 `FURNACE_HOUR` 안에서는 모든 소재를 lockstep으로 `FURNACE_CHARGED → PREHEAT_COMPLETE → HEAT_COMPLETE → SOAK_COMPLETE → FURNACE_EXTRACTED → RM4_RECORDED` 순서로 공개한다. 내부 저장을 위한 `material_key` 정렬은 화면에서 처리순서로 표현하지 않는다.
6. 예열·가열·균열 시간은 소재별 막대 길이와 근거값으로만 표시하고 substep 대기시간이나 소재 간 전역 정렬에 사용하지 않는다.
7. RM4는 같은 소재의 추출 뒤 `SEQUENCE_ONLY` 단계다. 가짜 분·초 시각을 만들지 않는다.
8. `AP_DAY`의 모든 AP 행은 날짜의 마지막 Batch에서 `AP_RECORDED_WITH_RESULT`로 함께 공개하고, AP 조업값과 `judge` 사이의 순서를 만들지 않는다.

시간 정밀도 enum은 `DAY`, `HOUR_BUCKET`, `SEQUENCE_ONLY` 세 개다. 이 정책은 정확한 공장 흐름 재현이 아니라 각 단계에서 어떤 판단이 가능했는지 보여주는 결정론적 시연이다. 모든 화면에는 `HISTORICAL_REPLAY` 배지를 상시 표시하고 “현재 공장상태”, “실시간 센서”, “온도 상승 중”, “위험 지속시간”을 표시하지 않는다.

## 15. Swing 화면

별도 데스크톱 진입점이 여는 단일 메인 프레임 안에 다음 탭을 둔다.

1. **전체 현황**: 제강·연주 → 가열로 1~4호기 → RM4 → AP의 역사적 재생 흐름과 설비별 최근 재생상태
2. **설비 상세**: 재생 소재, 진행단계, 공개된 조업값, 역사적 조업범위 편차, 품질위험 연관성
3. **위험 근거**: 표본 수, 불량 수, 보정 불량률, Wilson CI, 비교기준, 상대위험, RR CI 또는 계산불가 reason, q-value, 확인구간 결과와 사용 기준 수준
4. **이력 조회**: 날짜·설비·소재·등급 필터와 경보 이력
5. **관리 기준**: `bundleId`, `asOf`, 원본·산출물 해시, 분석·검열·제외 행수, 위험구간 조회

상단에는 `HISTORICAL_REPLAY` 고정 배지, 시작, 일시정지, 재개, 한 단계 진행, 속도조절, 날짜 이동을 둔다.

상태는 색상에만 의존하지 않는다.

- `● 정상`
- `▲ 주의`
- `■ 위험`
- `? 근거 부족`
- `! 데이터 오류`

동일 시간대 순서가 불명확하면 “시간대 처리 소재 N건, 정확한 처리순서 없음”을 표시한다.

## 16. 오류 처리

- 필수 입력 파일 또는 필수 컬럼이 없으면 분석을 시작하지 않는다.
- 분석 중 동일 출력경로에 다른 프로세스 잠금이 있으면 덮어쓰지 않고 종료한다.
- Manifest가 없거나 산출물이 부분 생성 상태면 Java 실행을 거부한다.
- bundle root·Manifest·고정 artifact 중 symlink, root 밖 canonical path, non-regular file, 누락·중복 역할이 있으면 어떤 artifact bytes도 열지 않고 Java 실행을 거부한다.
- bootJar 내장 schema bytes의 SHA-256이 Manifest의 일곱 schema digest 중 하나라도 다르거나, 산출물 크기·SHA-256·두 ID·분석설정 SHA·JSON 스키마 버전이 맞지 않으면 Java 실행을 거부한다.
- 선택적으로 원본 CSV 경로를 준 경우에만 Manifest의 원본 해시와 대조한다.
- `bundle-dir`이 없거나 상대경로이면 Java 실행을 거부하고 해석된 경로를 오류에 표시한다.
- headless 환경 또는 Java 21/`java.desktop` 미충족이면 실행조건을 설명하고 종료한다.
- 필수 ID 결측, 모호한 중복, 불가능한 날짜 순서는 격리하고 정상 이벤트로 만들지 않는다.
- 재생 ID preimage 충돌·중복 event ID·CSV 정렬 역행은 Bundle 생성 또는 Java 로딩을 실패시킨다.
- 감시값 하나가 결측이면 평균으로 보충하지 않고 그 항목만 `DATA_MISSING`으로 표시한다.
- 과거에 없던 범주가 들어오면 `UNREGISTERED_CONDITION`으로 표시한다.
- 조건별 표본이 부족하면 넓은 기준으로 대체하되 대체 수준을 표시한다.
- 백그라운드 재생 오류는 재생을 일시정지하고 오류 패널에 소재 ID와 원인을 표시한다.
- 화면 오류 때문에 재생 상태가 조용히 유실되지 않도록 상태전이를 기록한다.
- 창 닫기나 치명 오류 시 백그라운드 실행기, Spring Context, JVM을 순서대로 종료한다.

## 17. 기존 코드 정리

### 유지

- Spring Boot 기본 구조
- 일반 Kafka 이벤트 처리
- 일반 설비·센서 이벤트 도메인
- 기존 Dashboard와 Swing의 재사용 가능한 화면 패턴
- Docker의 PostgreSQL·Redis·Kafka 구성
- `build.gradle`의 H2 의존성, 공통 테스트 YAML, `.gitignore`의 일반 문서·Python 무시 규칙

### 제거

- `training/steel/**`의 AI 모델 학습·평가·API·Registry·모델 산출물
- `sfep-server/src/main/java/com/sfep/sfep_server/steelshadow/**`
- `sfep-server/src/test/java/com/sfep/sfep_server/steelshadow/**`
- `scripts/smoke-steel-shadow-bridge.py`
- `docs/steel-shadow-bridge-operations.md`
- `docs/superpowers/plans/2026-08-21-sfep-steel-shadow-bridge-plan.md`
- `docs/superpowers/plans/2026-08-21-steel-shadow-nextgen-plan.md`
- `docs/superpowers/specs/2026-08-21-sfep-steel-shadow-bridge-design.md`
- `docs/superpowers/specs/2026-08-21-steel-shadow-nextgen-design.md`
- `application.yaml`과 테스트 YAML의 `steel-shadow` 블록만 제거
- `.gitignore`의 Shadow lock과 `training/steel` 전용 예외만 제거

기존 커밋 전체를 revert하지 않는다. 일반 H2·Kafka·Dashboard·문서·Python 설정은 보존하며 삭제 대상은 구현계획에서 다시 정확히 확인한 Git 추적 파일만 명시적으로 제거한다. 기존 일반 `EquipmentType`과 `SensorEvent`는 철강 공정 의미와 맞지 않으므로 UI 패턴만 재사용하고 monitor 전용 Domain/ViewModel을 별도로 만든다. 저장소 루트나 넓은 디렉터리를 재귀 삭제하지 않는다.

## 18. 테스트 전략

### Python

- CP949 입력과 스키마 오류
- 엄격 조인, 중복·미연결·결측 격리
- 기준·홀드아웃 및 내부 시간 분리, Charge 경계 제거와 38일 Label 성숙조건
- 홀드아웃의 모든 비식별 feature·`judge` 변조 시 Bundle만 변하고 criteria는 불변이며, 적합 feature·성숙 `judge` 변조 시 criteria도 변하는 양방향 누출 방지
- 단계별 `firstAvailableStage`보다 이른 값과 층화조건 사용 거부
- type-1 분위수, 동률 보존, 400/2,000건 기준과 꼬리 경계 붕괴
- 숫자·범주·사전고정 상호작용의 Wilson 구간, Mantel-Haenszel 상대위험과 BH-FDR
- CAUTION/DANGER 최소표본·불량·위험차·재현성 경계값 테스트
- 직접표준화·0.5 cell 보정·CMH p-value·BH p=1 포함·동률축 축소와 Charge bootstrap golden 수치
- Wilson CI 필드와 RR CI null/reason 계약
- 기준을 충족하는 구간이 없을 때 DANGER 0개를 그대로 출력
- 상관 family 중복방지와 표시용 인접구간 병합 불변성
- 표본 부족, 단계 안전 계층 완화와 미등록 범주
- 동일 입력의 JSON/CSV/Manifest 바이트 결정론성
- 네 replay digest ID의 hand-authored preimage vector, RM4 equipment-batch null, 충돌·중복 거부와 고정 CSV tuple 정렬
- producer wheel 2회 빌드 archive SHA 일치, 검증 wheelhouse 외 네트워크·sdist·로컬 source 설치 거부
- 동일 wheelhouse를 서로 다른 두 절대경로 venv에 설치한 installed-code-tree digest와 Bundle bytes 일치
- producer source·wheel archive 변조는 build/seal이, CPython build·OS/architecture·embedded provenance·설치 code tree 변조는 runtime 검증이 실패하고, 검토된 runtime manifest를 갱신하면 두 ID가 바뀌는지 검증
- hand-authored semantic golden을 독립 seal 도구가 허용 token/provenance만 봉인하는 diff 검사와 `sfep-id-lines/v1`·JSON Schema·완전 golden Bundle 검증
- 임시 디렉터리, 잠금, 중간 실패 후 부분 Bundle 비공개와 원자 교체
- JSON/CSV의 NaN·Infinity·임의 예시값 부재

### Java

- 필수 절대 `bundle-dir`, Manifest 완료표식, `sfep-id-lines/v1`, 스키마·버전·크기·SHA·두 ID 검증
- 고정 role→basename만 사용하고 root/file symlink·절대/상대 경로 주입·`..`·중복/누락 역할·non-regular file을 열기 전 거부
- bootJar에 포함된 일곱 normative schema bytes와 Manifest의 schema digest exact-match 및 검증 순서 실패-폐쇄 테스트
- unknown/null/enum/범위 오류를 거부하는 DTO와 Python 생성 golden Bundle 호환성
- RFC 4180 UTF-8 CSV의 인용부호·쉼표·빈값·LF 파싱
- material/batch/equipment-batch/event ID 재계산·유일성과 고정 sort tuple 단조성 검증
- 전형범위 편차와 품질위험의 독립 판정
- 정상·주의·위험·근거 부족 판정과 여러 CAUTION의 DANGER 승격 금지
- 단계별 사용 가능 필드 제한
- 동일 시간대 Batch와 순서 미상 처리
- AP 조업값·`judge` 단일 이벤트 처리
- 시작·일시정지·재개·한 단계·속도 변경
- 경보키 중복억제, 최근 소재 반복 배지, 메모리 이력과 UTF-8 CSV 내보내기
- 데이터 누락·미등록 조건 표시
- Swing ViewModel과 Event Dispatch Thread 경계
- 웹·DB·Kafka·Redis 없이 monitor 전용 Context 기동
- headless 오류와 창 닫기 시 실행기·Context·JVM 종료

### 통합

1. 작은 합성 fixture로 Python 산출물을 만든다.
2. Java가 이를 읽고 모든 공정 이벤트를 재생한다.
3. 의도된 위험구간에서 설명 가능한 경보가 발생하는지 확인한다.
4. 각 단계 전 미래 필드가 노출되지 않고 AP 값과 `judge`가 함께 도착하는지 확인한다.
5. 동일 시간대 여러 소재가 전역 순서 없이 Batch로 표시되는지 확인한다.
6. 실제 세 CSV로 완전한 Bundle을 두 번 생성해 해시가 같은지 확인한다.
7. 실제 23,000여 건을 Swing에서 끝까지 재생한다.
8. 시작·일시정지·재개·한 단계·속도조절과 창 종료를 직접 확인한다.
9. `LOCKED_RETROSPECTIVE_HOLDOUT` 지표와 드리프트 보고서가 기준 재학습 없이 생성되는지 확인한다.

## 19. 완료 조건

다음 조건을 모두 만족해야 완료로 보고한다.

- 기존 Steel Shadow와 AI 예측 경로가 제거됨
- 오프라인 분석 명령이 실제 CSV로 성공함
- 모든 CAUTION/DANGER에 Wilson CI가 있고, DANGER에는 유효 RR CI, CAUTION에는 RR CI 또는 계산불가 reason과 FDR 근거가 있음
- 통계기준을 만족하지 않으면 DANGER 0개도 유효한 결과로 처리됨
- 완전한 Bundle과 Manifest가 원자적·결정론적으로 생성됨
- replay ID와 CSV 행 순서가 규범 preimage·sort tuple대로 재현되고 Python·Java가 모두 검증함
- versioned JSON Schema와 Python→Java golden Bundle이 동일 wire contract를 검증함
- Manifest에 자유경로가 없고 Java가 non-symlink bundle root의 고정 직접 자식 6개만 읽음
- Java가 bootJar 내장 일곱 schema digest와 Manifest를 exact-match한 뒤에만 Bundle을 파싱함
- build/seal이 producer source·wheel archive를 검증하고 Python 시작 검증이 Darwin/arm64·CPython build·embedded provenance·설치 code tree를 검증하며 그 변경이 두 ID에 반영됨
- 같은 producer wheelhouse를 다른 두 절대경로에 설치해도 runtime identity와 Bundle bytes가 같음
- Java가 Python 프로세스, HTTP API, 웹·DB·Kafka·Redis 없이 단독 실행됨
- Swing에서 전체 현황과 가열로 1~4호기 상세상태를 볼 수 있음
- 모든 화면이 `HISTORICAL_REPLAY`임을 명확히 표시함
- 재생 소재별 조업편차와 품질위험 항목·이유가 분리 표시됨
- 미래 공정정보·미성숙 Label·AP 결과가 앞 단계 경보와 층화에 사용되지 않음
- 동일 시간대의 가짜 Coil 순서가 생성되지 않음
- 제품 상태값이 역사적 조업범위 편차로 오인되지 않음
- 여러 CAUTION 또는 상관된 가스 근거가 DANGER로 중복 승격되지 않음
- 데이터 오류가 정상 또는 위험으로 오인되지 않음
- Python·Java·통합 테스트가 통과함
- 실제 CSV 재생을 로컬에서 시연함
- 원격 저장소에 자동 Push하지 않음

## 20. 구현 순서

설계 승인 이후 별도 구현계획에서 다음 순서를 세분화한다.

1. 분석설정·JSON Schema·ID test vector·golden source와 hand-authored semantic expectation, Java 내장 schema resource와 제3자 Python wheel 환경 확정
2. 기존 Shadow 코드의 정확한 제거 목록과 보존 설정 고정
3. Python 스키마·계보·단계 공개시점·시간분리 구현
4. 역사적 전형 조업범위, `judge` 통계계약, 공정 이벤트와 원자적 Bundle 구현
5. producer wheel 재현빌드 후 source/wheel/runtime identity 확정
6. 독립 seal 도구로 semantic golden의 허용 provenance·두 ID만 봉인하고 Python byte-for-byte 재현 검증
7. Java 전용 비웹 진입점, 내장 schema trust anchor, Bundle 로더와 규칙 엔진 구현
8. Batch 안전 공정 재생기, 상태·경보 관리 구현
9. `HISTORICAL_REPLAY` Swing 단일 프레임 구현
10. golden fixture와 실제 데이터 분석·평가·재생 검증

## 21. 설계 결정 요약

- 시스템 중심은 AI 모델이 아니라 설비 조업값과 설명 가능한 품질위험 규칙이다.
- 경보 단위는 역사적 재생 중 특정 공정단계에 도착한 Slab 또는 Coil 한 건이다.
- 세 CSV를 모두 사용하되 실제 시간 해상도, 동시 Batch와 단계별 정보 공개시점을 보존한다.
- 가열로는 가장 상세하게 표시하고 제강·연주는 선행 근거, AP는 결과 동시도착의 후행 설명으로 연결한다.
- `judge`는 38일 성숙·시점 기준을 통과한 위험구간 생성과 후행 결과 확인에만 사용한다.
- 직접 조업값의 역사적 전형범위 편차와 `judge` 품질위험 연관성을 별도 근거로 표시한다.
- DANGER는 고정된 최소표본, 효과크기, 신뢰구간, FDR와 시간 확인조건을 모두 만족할 때만 부여한다.
- Python은 결정론적 오프라인 관리기준 생성기이며 전용 비웹 Java Swing이 실행 본체다.
- 기계 고장진단, 자동제어, 인과 단정은 하지 않는다.
