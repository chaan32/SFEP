# SFEP Equipment Quality Python Producer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 세 철강 CSV를 엄격히 검증·연결하고, 시점 누출 없는 역사적 조업범위·품질위험 규칙·재생 이벤트를 결정론적 로컬 Bundle로 생성하는 Python 오프라인 분석기를 만든다.

**Architecture:** `analysis/equipment_quality` 패키지가 CP949 입력 → 계보/격리 → 시간 분리 → 전형범위/품질규칙 → 재생 이벤트 → 원자적 Bundle 순으로 처리한다. `contracts/equipment-monitor/v1`의 hand-authored schema와 semantic golden이 producer보다 먼저 기준이 되고, build/runtime/golden seal 도구는 producer를 import하지 않은 채 provenance만 봉인한다.

**Tech Stack:** CPython 3.12.10, pandas 2.3.0, NumPy 2.2.6, jsonschema 4.24.0, pytest 8.4.1, Python 표준 `hashlib/json/csv/math/pathlib`, Darwin arm64.

**Spec:** `docs/superpowers/specs/2026-08-25-sfep-equipment-quality-early-warning-design.md`

## Global Constraints

- AI/머신러닝 모델, 예측 확률, Python HTTP API와 장기 실행 Sidecar를 만들지 않는다.
- 입력은 `sts_1sm_cc_1.csv`, `sts_2fur_hr_2.csv`, `sts_3ap_3.csv` 세 CP949 파일이며 새 데이터를 만들거나 요구하지 않는다.
- 모든 production/test 작업은 먼저 실패하는 실제 동작 테스트를 확인한 뒤 최소 구현으로 통과시킨다.
- Python producer 지원환경은 `Darwin/arm64`, CPython `3.12.10` 하나다.
- 직접 의존성은 `numpy==2.2.6`, `pandas==2.3.0`, `jsonschema==4.24.0`, `pytest==8.4.1`, pip `25.1.1`이다. SciPy는 사용하지 않는다.
- JSON은 UTF-8·BOM 없음, Unicode code-point key 정렬, 공백 없는 separators, 비ASCII 비이스케이프, 마지막 LF 한 개다. Exact Python `int`는 binary64와 분리해 canonical 10진 정수로 쓰고, finite binary64는 round-to-nearest-ties-to-even interval의 모든 JSON spelling 중 UTF-8 길이·lexical 순으로 전역 winner를 택한다. `e`는 소문자, exponent는 `+`/선행 0 없이 정규화하고 양·음 zero는 `0`으로 합친다.
- CSV는 UTF-8·BOM 없음, RFC 4180, LF, 고정 header와 고정 sort tuple을 사용한다.
- `LABEL_MATURITY_DAYS=38`, `T=처음으로 count(hr_date<=d)>=ceil(0.70*N)인 날짜`, 내부 `D`도 같은 70% 공식이며 날짜를 쪼개거나 재계산하지 않는다.
- 동일 Charge가 경계를 가르면 양쪽에서 전부 제거한다. `judge`가 없는/미성숙 소재는 양품이 아니라 `unknownOrCensored`다.
- 후보 생성은 label-free이고 numeric/categorical/interaction 세 전역 BH family를 사용한다. DANGER를 강제로 만들지 않는다.
- `material_key`, `batch_id`, `equipment_batch_id`, `event_id`는 spec 6.1의 exact digest preimage를 사용한다. RM4의 `equipment_batch_id`는 null이다.
- Bundle artifact basename 6개는 고정이고 자유경로를 Manifest에 넣지 않는다. Manifest는 마지막 완료표식이며 원자 이동 전까지 최종 디렉터리를 노출하지 않는다.
- 산출물은 `var/equipment-quality/` 또는 명시적 절대 `--output-dir`에만 두고 Git에 추가하지 않는다.
- 기존 `training/steel`과 Shadow 코드는 이 계획에서 참고·수정하지 않는다. 제거는 최종 통합 계획에서 정확한 추적 파일만 수행한다.
- 원격 저장소에 Push하지 않는다.

## Preflight Environment (non-production)

규범 contract를 production code보다 먼저 작성할 수 있도록, 계획 커밋에서 `.superpowers/`, `analysis/.venv/`, `analysis/.wheelhouse/`, `var/equipment-quality/`를 ignore한 뒤 다음 개발환경만 준비한다.

```bash
conda create -y -p /private/tmp/sfep-python-3.12.10 python=3.12.10
/private/tmp/sfep-python-3.12.10/bin/python -m venv analysis/.venv
analysis/.venv/bin/python -m pip install pip==25.1.1
analysis/.venv/bin/python -m pip install setuptools==80.9.0 wheel==0.45.1 build==1.2.2.post1
analysis/.venv/bin/python -m pip install numpy==2.2.6 pandas==2.3.0 jsonschema==4.24.0 pytest==8.4.1
analysis/.venv/bin/python --version
analysis/.venv/bin/python -m pip --version
git check-ignore -q analysis/.venv analysis/.wheelhouse var/equipment-quality .superpowers
```

Expected: CPython `3.12.10`, pip `25.1.1`, 마지막 명령 exit 0. 이 개발 venv는 테스트 bootstrap이며 Task 9의 runtime identity로 사용하지 않는다.

---

### Task 1: Python 패키지와 결정론적 byte/ID 기반

**Files:**
- Create: `analysis/.python-version`
- Create: `analysis/pyproject.toml`
- Create: `analysis/equipment_quality/__init__.py`
- Create: `analysis/equipment_quality/deterministic.py`
- Create: `analysis/tests/test_deterministic.py`

**Interfaces:**
- Produces: `canonical_json_bytes(value: object) -> bytes`
- Produces: `sha256_uri(data: bytes) -> str`
- Produces: `id_lines(version: str, fields: Mapping[str, str]) -> bytes`
- Produces: `digest_json_id(namespace: str, value: Mapping[str, object]) -> str`
- Produces: `type1_quantile(values: Sequence[float], q: float) -> float | None`

- [ ] **Step 1: package metadata를 추가**

`analysis/.python-version`은 `3.12.10` 한 줄이고, `analysis/pyproject.toml`은 다음 계약을 사용한다.

```toml
[build-system]
requires = ["setuptools==80.9.0", "wheel==0.45.1"]
build-backend = "setuptools.build_meta"

[project]
name = "sfep-equipment-quality"
version = "1.0.0"
requires-python = "==3.12.*"
dependencies = [
  "numpy==2.2.6",
  "pandas==2.3.0",
  "jsonschema==4.24.0",
]

[project.scripts]
sfep-equipment-quality = "equipment_quality.cli:main"

[tool.setuptools]
packages = ["equipment_quality"]
include-package-data = false

[tool.setuptools.package-data]
equipment_quality = ["sfep_producer_provenance.json"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
markers = [
  "real_data: requires SFEP_STEEL_DATA_DIR and validates the approved local snapshot",
]
```

- [ ] **Step 2: 결정론적 byte 계약의 실패 테스트 작성**

```python
import math

import pytest

from equipment_quality.deterministic import (
    canonical_json_bytes,
    digest_json_id,
    id_lines,
    sha256_uri,
    type1_quantile,
)


def test_canonical_json_is_compact_sorted_utf8_and_lf_terminated():
    assert canonical_json_bytes({"한글": "값", "a": 1.5}) == (
        '{"a":1.5,"한글":"값"}\n'.encode("utf-8")
    )


def test_canonical_json_rejects_non_finite_numbers():
    with pytest.raises(ValueError, match="finite"):
        canonical_json_bytes({"value": math.nan})


def test_material_key_matches_independent_sha256_vector():
    assert digest_json_id(
        "sfep-material-key/v1", {"chargeId": "CH1", "slabNo": "1"}
    ) == "sha256:b313bfca80b92882d529a186bb7fc082e65020c409956481283745421ec1ce5a"


def test_id_lines_rejects_line_breaks_and_equals():
    with pytest.raises(ValueError):
        id_lines("sfep-bundle-id/v1", {"bad=key": "value"})


def test_sha256_uri_matches_empty_message_vector():
    assert sha256_uri(b"") == "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_canonical_json_rejects_non_string_object_keys():
    with pytest.raises(TypeError, match="string keys"):
        canonical_json_bytes({1: "value"})


def test_type1_quantile_uses_inverse_empirical_boundary():
    assert type1_quantile([1.0, 2.0, 3.0, 4.0], 0.25) == 1.0
    assert type1_quantile([1.0, 2.0, 3.0, 4.0], 0.50) == 2.0
    assert type1_quantile([], 0.50) is None
```

- [ ] **Step 3: 실패를 확인**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_deterministic.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: equipment_quality` or missing functions.

- [ ] **Step 4: 최소 결정론 구현 추가**

```python
def canonical_json_bytes(value: object) -> bytes:
    _reject_non_finite(value)
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return text.encode("utf-8") + b"\n"


def digest_json_id(namespace: str, value: Mapping[str, object]) -> str:
    preimage = namespace.encode("utf-8") + b"\n" + canonical_json_bytes(value)
    return sha256_uri(preimage)
```

`canonical_json_bytes`는 모든 중첩 object의 string key와 finite number를 재귀 검증한다. `id_lines`는 version 첫 줄 다음에 key UTF-8 byte 정렬된 `key=value\n`을 붙이고 version/key/value의 `=`·CR·LF를 거부한다. `type1_quantile`은 finite 값과 `0<q<=1`만 허용하고 정렬값의 `ceil(q*n)-1`을 clamp하며 빈 입력은 null이다.

- [ ] **Step 5: 단위 테스트와 전체 기존 기준선 확인**

Run:

```bash
analysis/.venv/bin/python -m pytest analysis/tests/test_deterministic.py -q
./sfep-server/gradlew -p sfep-server test
```

Expected: 새 테스트 모두 PASS, 기존 Java `BUILD SUCCESSFUL`.

- [ ] **Step 6: 커밋**

```bash
git add analysis/.python-version analysis/pyproject.toml analysis/equipment_quality analysis/tests/test_deterministic.py
git commit -m "feat: add deterministic Python producer foundation"
```

### Task 2: Normative contract, 분석설정과 semantic golden

**Files:**
- Create: `analysis/analysis_config.json`
- Create: `contracts/equipment-monitor/v1/bundle_manifest.schema.json`
- Create: `contracts/equipment-monitor/v1/analysis_config.schema.json`
- Create: `contracts/equipment-monitor/v1/producer_runtime.schema.json`
- Create: `contracts/equipment-monitor/v1/equipment_operating_ranges.schema.json`
- Create: `contracts/equipment-monitor/v1/quality_risk_intervals.schema.json`
- Create: `contracts/equipment-monitor/v1/analysis_summary.schema.json`
- Create: `contracts/equipment-monitor/v1/replay_event_row.schema.json`
- Create: `contracts/equipment-monitor/v1/id-test-vectors.json`
- Create: `contracts/equipment-monitor/v1/canonical-number-test-vectors.json`
- Create: `contracts/equipment-monitor/v1/golden-source/sts_1sm_cc_1.csv`
- Create: `contracts/equipment-monitor/v1/golden-source/sts_2fur_hr_2.csv`
- Create: `contracts/equipment-monitor/v1/golden-source/sts_3ap_3.csv`
- Create: `contracts/equipment-monitor/v1/golden-expectation/criteria_projection.jsonl`
- Create: `contracts/equipment-monitor/v1/golden-expectation/equipment_operating_ranges.template.json`
- Create: `contracts/equipment-monitor/v1/golden-expectation/quality_risk_intervals.template.json`
- Create: `contracts/equipment-monitor/v1/golden-expectation/replay_events.template.csv`
- Create: `contracts/equipment-monitor/v1/golden-expectation/analysis_summary.template.json`
- Create: `contracts/equipment-monitor/v1/golden-expectation/expected_alerts.json`
- Create: `analysis/tests/test_contracts.py`

**Interfaces:**
- Does not import producer code; contract bytes and expectations are authored independently before Task 1 production code.
- Produces: seven Draft 2020-12 schemas with `additionalProperties:false`
- Produces: `quality-analysis-v1` constants and column/stage/context mappings
- Produces: hand-authored CP949 source and semantic oracle with provenance tokens only

- [ ] **Step 1: contract 파일 부재를 증명하는 실패 테스트 작성**

```python
from pathlib import Path
import json

from jsonschema.validators import validator_for

CONTRACT = Path("contracts/equipment-monitor/v1")


def test_all_normative_schemas_compile_and_reject_unknown_properties():
    names = [
        "bundle_manifest.schema.json", "analysis_config.schema.json",
        "producer_runtime.schema.json", "equipment_operating_ranges.schema.json",
        "quality_risk_intervals.schema.json", "analysis_summary.schema.json",
        "replay_event_row.schema.json",
    ]
    for name in names:
        schema = json.loads((CONTRACT / name).read_text(encoding="utf-8"))
        validator_for(schema).check_schema(schema)
        assert schema["additionalProperties"] is False


@pytest.mark.parametrize("schema_name,valid_instance,mutator", NESTED_SCHEMA_NEGATIVE_CASES)
def test_every_object_boundary_rejects_unknown_missing_null_enum_and_range(
    schema_name, valid_instance, mutator
):
    schema = json.loads((CONTRACT / schema_name).read_text(encoding="utf-8"))
    validator_for(schema)(schema).validate(valid_instance)
    with pytest.raises(ValidationError):
        validator_for(schema)(schema).validate(mutator(copy.deepcopy(valid_instance)))


def test_id_vectors_match_independent_expected_ids():
    vectors = json.loads((CONTRACT / "id-test-vectors.json").read_text())
    assert vectors["materialKey"]["expected"] == (
        "sha256:b313bfca80b92882d529a186bb7fc082e65020c409956481283745421ec1ce5a"
    )


def test_golden_sources_are_cp949_and_have_exact_headers():
    expected = {
        "sts_1sm_cc_1.csv": "sm_plant,charge_id,steel_grade",
        "sts_2fur_hr_2.csv": "charge_id,slab_no,furnace_no",
        "sts_3ap_3.csv": "judge,hr_coil_id,ap_plant",
    }
    for name, prefix in expected.items():
        text = (CONTRACT / "golden-source" / name).read_text(encoding="cp949")
        assert text.splitlines()[0].startswith(prefix)


def test_golden_expectations_validate_after_only_provenance_tokens_are_substituted():
    rendered = render_expectation_tokens_with_literal_sha256_values(CONTRACT / "golden-expectation")
    validate_all_six_artifacts_and_replay_rows(rendered, CONTRACT)
```

- [ ] **Step 2: 실패를 확인**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_contracts.py -q`

Expected: FAIL with missing `contracts/equipment-monitor/v1` files.

- [ ] **Step 3: 분석설정과 일곱 schema를 hand-author**

`analysis_config.json`에 다음 exact policy를 넣는다: `schemaVersion=sfep-analysis-config/v1`, `analysisConfigVersion=quality-analysis-v1`, `timezone=Asia/Seoul`, `labelMaturityDays=38`, reference/discovery fraction `0.70`, range support `400/2000`, discovery support/defects `200/5/10`, confirmation support/defects `100/5`, bootstrap `2000/1900`, Wilson z `1.959963984540054`, BH q `0.10/0.05`, effect thresholds `1.5/2.0`과 `0.005/0.01`, spec 8.5 field stages, spec 12.3의 6개 fixed interactions, spec 13의 10개 evidence family.

각 schema는 `$schema=https://json-schema.org/draft/2020-12/schema`, exact `required`, enum/nullability/numeric range와 모든 중첩 object의 `additionalProperties:false`를 둔다. 배열의 role/order/unique 조건은 Draft 2020-12 표현과 application-level validation 양쪽에 고정한다. Manifest artifact item에는 `role,sizeBytes,sha256,schemaVersion`만 허용하고 자유 `path`를 금지한다. `analysis_summary`에는 Task 3 exact quarantine/censor vocabulary, 별도 필수 `chargePurgeCounts.outer|inner.{chargeCount,rowCount}`, 정확히 `DANGER,CAUTION_OR_DANGER` 순서인 두 holdout profile과 필수 `lineage.fields[]`, `lineage.materials[]`, `lineage.populations[]`, `lineage.aggregates[]`를 둔다. 여섯 holdout metric은 각각 `pointEstimate,lower,upper,validReplicates,reasonCode`를 소유해 정상 CI, zero denominator, too-few-bootstrap 상태를 혼합하지 않는다. population은 정확히 `REFERENCE,DISCOVERY,CONFIRMATION,HOLDOUT` 순서이고 ref/split을 같은 값에 묶는다. Range aggregate는 `REFERENCE/REFERENCE/NOT_APPLICABLE`, quality aggregate는 matching `DISCOVERY|CONFIRMATION` population과 `FIXED_POPULATION_STRATA_MINUS_CANDIDATE`를 쓴다.

field entry는 `artifactRole,outputField,sourceRole,sourceColumn,conversion,dependencies,firstAvailableStage`, material entry는 `materialKey,chargeId,slabNo,hrCoilId,sourceRecords`를 가지며 source record는 `role,name,recordNumber`다. population entry는 `populationRef,split,materialKeys`, aggregate entry는 `artifactRole,ruleId,split,populationRef,inputMaterialKeys,comparatorDefinition,filters,transformations`를 가진다. 일곱 role의 output grammar, 50개 source column(`f_bfg_per,f_cog_per,f_ldg_per` 포함), config/runtime/schema/source/identity/population/policy root와 conversion/filter/transformation vocabulary를 폐쇄한다. Replay identifier raw mapping은 `values_json.*`가 아니라 top-level `charge_id,slab_no,hr_coil_id,ap_prod_id`를 사용한다. raw mapping은 exact source role/column, non-null stage와 빈 dependencies, derived mapping은 null source role/column과 non-empty dependency를 가지며 derived replay field만 stage를 가질 수 있다. `analysis_summary.lineage` 자체만 재귀 metadata tracking에서 제외한다. Range input keys는 실제 finite value 기여 소재, quality input keys는 candidate 소재이고 comparator는 고정 population/strata에서 candidate를 뺀 집합이라는 exact expression으로 기록한다. 모든 material/population/output-node ref의 존재, uniqueness, UTF-8 정렬, cycle, role별 terminal과 point/CI consistency를 application validator가 추가 확인한다. `NESTED_SCHEMA_NEGATIVE_CASES`와 token renderer는 test 안의 hand-authored literal instance/변조 함수이며 producer helper를 import하지 않는다.

- [ ] **Step 4: golden source/expectation과 ID vector를 hand-author**

Golden source는 한 Charge당 두 Slab, 가열로 1~4호기, AP 양품/불량, AP 미성숙/미연결/중복 격리를 모두 포함하는 최소 12소재로 만든다. UTF-8 원고를 `iconv -f UTF-8 -t CP949`로 변환해 최종 세 CSV만 추적한다. Expectation의 통계·등급·event order/count는 literal로 적고 다음 token만 허용한다.

```text
@PRODUCER_RUNTIME_SHA256@
@CRITERIA_ID@
@BUNDLE_ID@
@SCHEMA_<ROLE>_SHA256@
@SOURCE_<ROLE>_SHA256@
@ARTIFACT_<ROLE>_SHA256@
```

- [ ] **Step 5: contract 테스트를 통과시키고 변조 거부를 확인**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_contracts.py -q`

Expected: PASS. 이어 test 내부에 unknown Manifest `path`와 unknown enum이 `ValidationError`를 내는 두 assertion을 추가하고 다시 PASS.

- [ ] **Step 6: 커밋**

```bash
git add analysis/analysis_config.json analysis/tests/test_contracts.py contracts/equipment-monitor/v1
git commit -m "feat: define equipment monitor wire contracts"
```

### Task 3: CP949 입력, 엄격 계보와 시점 분리

**Files:**
- Create: `analysis/equipment_quality/schema.py`
- Create: `analysis/equipment_quality/genealogy.py`
- Create: `analysis/equipment_quality/time_split.py`
- Create: `analysis/equipment_quality/models.py`
- Create: `analysis/tests/factories/__init__.py`
- Create: `analysis/tests/factories/schema_time.py`
- Create: `analysis/tests/test_schema_genealogy.py`
- Create: `analysis/tests/test_time_split.py`

**Interfaces:**
- Produces: `read_inputs(data_dir: Path) -> InputTables`
- Produces: `build_genealogy(inputs: InputTables) -> GenealogyResult`
- Produces: `build_time_split(result: GenealogyResult, config: AnalysisConfig) -> TimeSplitResult`
- Produces: `load_analysis_config(path: Path) -> AnalysisConfig`
- `InputTables`: `sm_cc`, `fur_hr`, `ap`, `sources`
- `SourceFile` is frozen `role`, `name`, `size_bytes`, `sha256`; source names are basenames and never absolute paths.
- `SourceRecordRef` is frozen `role,name,record_number`; `record_number` is the one-based CSV data-record ordinal after the header, independent of physical multiline line numbers.
- `FieldLineage` is frozen `artifact_role,output_field,source_role,source_column,conversion,dependencies,first_available_stage`; `MaterialLineage` is frozen `material_key,charge_id,slab_no,hr_coil_id,source_records`.
- `AnalysisConfig` is frozen and contains every Task 2 policy field; mappings are immutable and no caller default is allowed.
- `GenealogyResult`: `boundary_rows`, `replay_rows`, `quality_rows`, `quarantine_rows`, `lineage_rows`, `audit`. `boundary_rows` is built only from raw FUR/HR rows with valid non-empty unique `(charge_id,slab_no)` and valid `hr_date`; it is independent of SM/AP linkage and `judge`.
- `TimeSplitResult`: `as_of`, `discovery_cutoff`, `reference_rows`, `discovery_rows`, `confirmation_rows`, `holdout_rows`, `counts`
- Test-only `analysis/tests/factories/schema_time.py`: `tables`, `tables_with_duplicate_ap`, `tables_with_unlinked_sm_and_ap`, `tables_with_different_sm_ap_linkage_same_fur`, `tables_with_ap_before_hr`, `tables_with_known_record_numbers`, `dated_rows`, `analysis_config`, `maturity_fixture`; `dated_rows`와 `maturity_fixture`는 `GenealogyResult`를 반환하고 각 함수는 최소 literal DataFrame/dataclass만 구성하며 production helper를 호출해 expected 값을 계산하지 않는다.

- [ ] **Step 1: 실제 깨짐을 잡는 schema/genealogy 실패 테스트 작성**

```python
def test_build_genealogy_never_joins_charge_without_slab():
    inputs = tables(
        sm=[{"charge_id":"C1","slab_no":"1"}, {"charge_id":"C1","slab_no":"2"}],
        fur=[{"charge_id":"C1","slab_no":"2","hr_coil_id":"H2"}],
        ap=[{"hr_coil_id":"H2","judge":"불량"}],
    )
    result = build_genealogy(inputs)
    assert result.replay_rows.iloc[0]["slab_no"] == "2"
    assert len(result.replay_rows) == 1


def test_ambiguous_duplicate_is_quarantined_not_first_wins():
    result = build_genealogy(tables_with_duplicate_ap("H2"))
    assert result.quality_rows.empty
    assert set(result.quarantine_rows["reason"]) == {"DUPLICATE_AP_KEY"}


def test_boundary_population_is_fur_only_and_ignores_sm_ap_linkage():
    baseline = build_genealogy(tables_with_unlinked_sm_and_ap())
    mutated = build_genealogy(tables_with_different_sm_ap_linkage_same_fur())
    assert baseline.boundary_rows.equals(mutated.boundary_rows)
    assert build_time_split(baseline, analysis_config()).as_of == (
        build_time_split(mutated, analysis_config()).as_of
    )


def test_impossible_stage_date_order_is_quarantined_not_replayed():
    result = build_genealogy(tables_with_ap_before_hr())
    assert result.replay_rows.empty
    assert set(result.quarantine_rows["reason"]) == {"IMPOSSIBLE_STAGE_DATE_ORDER"}


def test_join_preserves_exact_source_record_provenance():
    result = build_genealogy(tables_with_known_record_numbers())
    lineage = result.lineage_rows.iloc[0]
    assert lineage["sm_cc_record_number"] == 2
    assert lineage["fur_hr_record_number"] == 4
    assert lineage["ap_record_number"] == 3
```

- [ ] **Step 2: 실패 확인**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_schema_genealogy.py -q`

Expected: FAIL because modules/functions are missing.

- [ ] **Step 3: schema와 genealogy 최소 구현**

같은 test file에 CP949 한글 fixture, 누락/추가/순서변경 header, trim 뒤 빈 ID, 잘못된 날짜, invalid `judge`, NaN/Infinity numeric을 각각 거부하는 parametrized 실패 테스트를 먼저 둔다. `read_inputs`는 exact filename, CP949, exact ordered header, ID trim-only, date `YYYY-MM-DD`, `judge in {양품,불량}`, numeric finite coercion을 검증하고 모든 row에 내부 `_source_record_number`를 붙인다. `build_genealogy`는 `(charge_id,slab_no)`와 `hr_coil_id`의 결측·중복을 reason별로 격리하고 임의 first/drop-duplicates를 금지한다. FUR 기준 replay row는 유효 SM 연결까지 유지하고 AP 미연결은 censored로 남기며, `quality_rows`는 unique AP가 연결된 행만 둔다. `cast_date<=f_ext_date<=hr_date<=ap_date` 중 존재하는 인접단계 순서가 역전되면 `IMPOSSIBLE_STAGE_DATE_ORDER`로 격리한다. `lineage_rows`는 정상/검열 material의 세 source record ref를 보존하고 derived artifact에서는 `_source_record_number`를 감시 feature로 취급하지 않는다. `boundary_rows`는 SM/AP 조인 전에 raw FUR/HR의 유효·유일 ID와 `hr_date`만으로 만들고 원본 row identity를 보존한다.

`AnalysisConfig`는 Task 2 JSON의 상수·field stage·range context·quality stratification·interaction을 immutable tuple/map으로 로드하고 unknown/missing key를 schema validation으로 거부한다.

- [ ] **Step 4: 시간분리 실패 테스트 작성**

```python
def test_reference_cutoff_keeps_date_whole_and_does_not_recalculate_after_charge_purge():
    rows = dated_rows([("C1","2025-01-01"), ("C2","2025-01-02"),
                       ("C3","2025-01-03"), ("C4","2025-01-04"),
                       ("C3","2025-01-05")])
    split = build_time_split(rows, analysis_config(reference_fraction=0.70, maturity_days=38))
    assert split.as_of.isoformat() == "2025-01-04"
    assert "C3" not in set(split.reference_rows.charge_id)
    assert "C3" not in set(split.holdout_rows.charge_id)


def test_unmatured_label_is_censored_not_good():
    split = build_time_split(maturity_fixture(), analysis_config(reference_fraction=0.70, maturity_days=38))
    assert split.counts["reference"]["unknownOrCensored"] == 1
    assert split.counts["reference"]["nonDefects"] == 0
```

- [ ] **Step 5: 실패 확인 후 exact T/D/38일 구현**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_time_split.py -q`

Expected: FAIL before implementation. `build_time_split`은 `GenealogyResult.boundary_rows`의 FUR-only `N`, whole-date T, cross-boundary Charge purge, mature criteria `hr_date<=T-38 and ap_date<=T`, 같은 공식의 D와 second Charge purge를 구현한다. T는 replay/quality row 수나 SM/AP 연결 결과로 절대 재계산하지 않는다.

- [ ] **Step 6: 관련 테스트와 full Python subset 통과**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_schema_genealogy.py analysis/tests/test_time_split.py -q`

Expected: PASS, pandas warning 없음.

- [ ] **Step 7: 커밋**

```bash
git add analysis/equipment_quality analysis/tests/factories analysis/tests/test_schema_genealogy.py analysis/tests/test_time_split.py
git commit -m "feat: add strict steel genealogy and time split"
```

### Task 4: Column role과 역사적 전형 조업범위

**Files:**
- Create: `analysis/equipment_quality/feature_roles.py`
- Create: `analysis/equipment_quality/operating_ranges.py`
- Create: `analysis/tests/factories/ranges.py`
- Create: `analysis/tests/test_feature_roles.py`
- Create: `analysis/tests/test_operating_ranges.py`

**Interfaces:**
- Produces: `FeatureDefinition(name, field_role, first_stage, event_date_column, equipment_type, equipment_id_column, context_hierarchy)`
- Produces: `definitions(config: AnalysisConfig) -> Sequence[FeatureDefinition]`
- Produces: `build_operating_ranges(split: TimeSplitResult, definitions: Sequence[FeatureDefinition], config: AnalysisConfig) -> list[dict[str, object]]`
- Test-only factories added: `range_rows`, `split_with_reference`, `sparse_context_fixture`, `stage_date_fixture`, `future_context_fixture`, `collapsed_lower_tail_fixture`, `only`; `EXPECTED_MONITORED_AND_CONTEXT_COLUMNS` is a hand-authored literal `frozenset` in `test_feature_roles.py`.

- [ ] **Step 1: 미래 field와 제품상태 오판을 잡는 실패 테스트 작성**

```python
def test_every_non_identity_source_column_has_one_role_and_stage():
    mapped = {definition.name for definition in definitions(analysis_config())}
    assert mapped == EXPECTED_MONITORED_AND_CONTEXT_COLUMNS


def test_product_state_range_is_reference_only_not_direct_operation():
    slab_width = next(d for d in definitions(analysis_config()) if d.name == "slab_width")
    assert slab_width.field_role == "PRODUCT_STATE_REFERENCE"
    assert slab_width.first_stage == "FURNACE_CHARGED"
```

- [ ] **Step 2: 실패 확인 후 exact field map 구현**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_feature_roles.py -q`

Expected: FAIL then PASS after materializing all spec 8 columns exclusively from the validated `AnalysisConfig` maps. Identifier/time/result columns are explicit `IDENTITY|TIME|RESULT` exclusions and never receive thresholds. Production modules do not carry a second hard-coded field→stage/context/interaction map; only the fixed stage enum ordering is code.

- [ ] **Step 3: type-1/support/context 실패 테스트 작성**

Range context hierarchy는 다음 exact 순서다.

```text
SM_CC: sm_plant+steel_grade+steel_usage -> sm_plant+steel_grade -> sm_plant
FURNACE: furnace_no+steel_grade+steel_usage+f_jangip_gubun+slab_width_band
         -> furnace_no+steel_grade+f_jangip_gubun
         -> furnace_no+f_jangip_gubun -> furnace_no
RM4: steel_grade+steel_usage+hr_thick_band+hr_width_band
     -> steel_grade+steel_usage -> steel_grade -> empty
AP: ap_plant+steel_grade+steel_usage+ap_shift+ap_thick_band+ap_width_band
    -> ap_plant+steel_grade+steel_usage -> ap_plant+steel_grade -> ap_plant
```

```python
def test_range_uses_type1_quantiles_and_disables_extreme_tail_below_2000():
    rows = range_rows(values=list(range(1, 401)), furnace_no="1호기")
    config = analysis_config()
    record = only(build_operating_ranges(split_with_reference(rows), definitions(config), config))
    assert record["p05"] == 20.0
    assert record["p95"] == 380.0
    assert record["p01"] is None
    assert record["p99"] is None
    assert record["lowerTailEnabled"] is False
    assert record["upperTailEnabled"] is False


def test_sparse_specific_context_falls_back_and_records_level():
    config = analysis_config()
    record = only(build_operating_ranges(sparse_context_fixture(), definitions(config), config))
    assert record["contextLevel"] == 1
    assert record["context"] == {"furnace_no": "1호기", "f_jangip_gubun": "CCR"}


def test_range_excludes_value_whose_stage_event_arrives_after_as_of():
    config = analysis_config()
    record = only(build_operating_ranges(stage_date_fixture(value_dates=["2025-01-01", "2025-01-05"], as_of="2025-01-04"), definitions(config), config))
    assert record["support"] == 400


def test_range_context_never_uses_field_revealed_after_feature_stage():
    config = analysis_config()
    record = only(build_operating_ranges(future_context_fixture(), definitions(config), config))
    assert "hr_thick_band" not in record["context"]


def test_tied_tail_boundary_disables_only_collapsed_tail():
    config = analysis_config()
    record = only(build_operating_ranges(collapsed_lower_tail_fixture(), definitions(config), config))
    assert record["p01"] == record["p05"]
    assert record["lowerTailEnabled"] is False
    assert record["upperTailEnabled"] is True
```

- [ ] **Step 4: 실패 확인 후 범위 builder 구현**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_operating_ranges.py -q`

Expected: FAIL. 구현은 feature별 `event_date_column<=T`인 실제 도착행만 사용하고, 해당 `first_stage`까지 공개된 context만 허용한다. support 400, extremes 2000, finite numeric only, 동률 보존 type-1, context hierarchy와 deterministic `ruleId`를 사용한다. `DIRECT_OPERATION`과 `PRODUCT_STATE_REFERENCE`를 모두 기록하되 Java가 후자에 편차 경보를 만들지 않도록 `fieldRole`을 보존한다. 한쪽 p01/p05 또는 p95/p99가 붕괴하면 그 꼬리만 disabled다.

- [ ] **Step 5: 테스트 통과와 커밋**

```bash
analysis/.venv/bin/python -m pytest analysis/tests/test_feature_roles.py analysis/tests/test_operating_ranges.py -q
git add analysis/equipment_quality/feature_roles.py analysis/equipment_quality/operating_ranges.py analysis/tests/factories/ranges.py analysis/tests/test_feature_roles.py analysis/tests/test_operating_ranges.py
git commit -m "feat: derive historical operating ranges"
```

### Task 5: 품질위험 통계 primitives

**Files:**
- Create: `analysis/equipment_quality/statistics.py`
- Create: `analysis/tests/factories/statistics_fixtures.py`
- Create: `analysis/tests/test_statistics.py`

**Interfaces:**
- Produces: `wilson_interval(defects: int, total: int, z: float) -> tuple[float, float] | None`
- Produces: `standardized_rates(strata: Sequence[Stratum], weights: Mapping[str, float] | None) -> StandardizedRates`
- Produces: `mantel_haenszel_rr(strata: Sequence[Stratum]) -> float | None`
- Produces: `cmh_p_value(strata: Sequence[Stratum]) -> tuple[float, str]`
- Produces: `benjamini_hochberg(p_values: Sequence[float]) -> list[float]`
- Produces: `deterministic_sample_indices(seed: bytes, replicate: int, population_size: int) -> tuple[int, ...]`
- Produces: `charge_bootstrap_rr_ci(rows, criteria_id, rule_id, replicates=2000) -> BootstrapCi`
- `Stratum` is frozen `a,b,c,d,key=""`; `StandardizedRates` is frozen `candidate,comparator,risk_difference,weights`; `BootstrapCi` is frozen `lower,upper,valid_replicates,reason_code`. Explicit weights are keyed by `Stratum.key`, missing surviving keys are fatal, and confirmation renormalizes the surviving discovery weights.
- `charge_bootstrap_rr_ci` rows have exact columns `charge_id,stratum,candidate,judge`; no row-level sampling path is exposed.
- Test-only factories added: `two_strata_fixture`, `bootstrap_fixture_with_two_coils_per_charge`.

- [ ] **Step 1: hand-calculated 실패 테스트 작성**

```python
def test_cmh_zero_variance_returns_one_with_reason():
    p_value, reason = cmh_p_value([Stratum(a=0, b=10, c=0, d=20)])
    assert p_value == 1.0
    assert reason == "ZERO_VARIANCE"


def test_wilson_interval_matches_hand_calculation():
    assert wilson_interval(5, 10, 1.959963984540054) == pytest.approx(
        (0.236593090512564, 0.7634069094874361)
    )


def test_direct_standardization_uses_fixed_population_weights():
    rates = standardized_rates(two_strata_fixture(), {"A": 0.25, "B": 0.75})
    assert rates.candidate == pytest.approx(0.175)
    assert rates.comparator == pytest.approx(0.10)


def test_mh_adds_half_to_all_four_cells_only_in_zero_stratum():
    rr = mantel_haenszel_rr([Stratum(a=0, b=9, c=1, d=9)])
    assert rr == pytest.approx((0.5 * 11.0 / 21.0) / (1.5 * 10.0 / 21.0))


def test_bh_includes_invalid_candidates_as_p_one_and_preserves_order():
    assert benjamini_hochberg([0.01, 1.0, 0.04]) == pytest.approx([0.03, 1.0, 0.06])
```

- [ ] **Step 2: 실패 확인**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_statistics.py -q`

Expected: FAIL because `statistics.py` is missing.

- [ ] **Step 3: Wilson/direct standardization/MH/CMH/BH 최소 구현**

CMH는 continuity correction 없이 spec 12.1.1 수식과 `math.erfc(abs(z)/sqrt(2))`를 사용한다. 0.5 보정은 RR의 zero-cell stratum 네 칸에만 적용하고 support/rate/p-value에는 적용하지 않는다. BH는 `(p,index)` 안정 정렬 후 뒤에서 cumulative minimum으로 원래 순서를 복원한다.

- [ ] **Step 4: deterministic Charge bootstrap 실패 테스트 추가**

```python
def test_charge_bootstrap_is_reproducible_and_keeps_charge_rows_together():
    rows = bootstrap_fixture_with_two_coils_per_charge()
    first = charge_bootstrap_rr_ci(rows, "sha256:" + "1" * 64, "sha256:" + "2" * 64)
    second = charge_bootstrap_rr_ci(rows, "sha256:" + "1" * 64, "sha256:" + "2" * 64)
    assert first == second
    assert first.valid_replicates >= 1900
    assert first.lower <= first.upper


def test_sha256_sampler_matches_independent_index_vector():
    seed = hashlib.sha256(
        (("sha256:" + "1" * 64) + "\0" + ("sha256:" + "2" * 64) + "\0rule-ci-v1").encode()
    ).digest()
    assert deterministic_sample_indices(seed, replicate=0, population_size=3) == (1, 1, 2)
```

- [ ] **Step 5: SHA256 index sampler와 type-1 CI 구현 후 통과**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_statistics.py -q`

Expected: PASS. Seed는 `SHA-256(criteriaId + NUL + ruleId + NUL + "rule-ci-v1")`, index는 `SHA-256(seed || uint64be(r) || uint64be(j))` 첫 8바이트 mod K다. Resample 단위는 정렬된 고유 Charge이고 선택된 Charge의 모든 Coil을 multiplicity 그대로 포함한다; test fixture는 row-level sampling이면 다른 CI가 되도록 hand-author한다.

- [ ] **Step 6: 커밋**

```bash
git add analysis/equipment_quality/statistics.py analysis/tests/factories/statistics_fixtures.py analysis/tests/test_statistics.py
git commit -m "feat: add deterministic quality risk statistics"
```

### Task 6: Label-free 후보, FDR와 확정 품질규칙

**Files:**
- Create: `analysis/equipment_quality/quality_intervals.py`
- Create: `analysis/tests/factories/quality.py`
- Create: `analysis/tests/test_quality_intervals.py`

**Interfaces:**
- Consumes: Task 3 `TimeSplitResult`, Task 5 statistics
- Produces: `generate_candidates(discovery_rows, definitions, config) -> Sequence[Candidate]`
- Produces: `build_quality_rules(split, definitions, config, criteria_id) -> list[dict[str, object]]`
- `PredicateClause` is frozen `field,type,lower,lower_inclusive,upper,upper_inclusive,values`; `Candidate.predicate` is a non-empty tuple serialized as `allOf` and exposes `canonical_predicate` bytes. `Candidate` is frozen and contains `analysis_family`, `evidence_family`, `field_names`, `predicate`, `first_available_stage`, `equipment_type`, `application_scope`, `equipment_id`, `application_context`, `adjustment_level`, `adjustment_fields_dropped`, `adjustment_kind`, `early_warning_eligible`, discovery support metadata and label-free `rule_id` inputs. It contains no `judge`, defect count, p/q-value, grade or holdout field.
- `QualityMetric` is frozen and contains all schema fields: `support`, `defects`, `crude_rate`, Wilson bounds, adjusted/comparator rates, risk difference, RR/RR bounds, p/q-value and `reason_code`.
- `QualityRule` is frozen and adds discovery/confirmation `QualityMetric`, `grade`, `display_merge_rule_ids` to the Candidate wire fields. Serialization uses the exact camelCase schema names and no omitted optional keys.
- Test-only factories added: `rows_with_labels`, `tied_numeric_fixture`, `equipment_category_fixture`, `ap_candidate_fixture`, `global_family_fixture`, `global_family_split`, `q_values_by_rule_id`, `strong_repeated_fixture`, `caution_only_fixture`, `null_fixture`, `grade_boundary_fixture`, `future_context_quality_fixture`, `too_few_bootstraps_fixture`; tests define literal 64-hex `CRITERIA_ID` and hand-calculated `LITERAL_GLOBAL_FAMILY_Q_VALUES`.

- [ ] **Step 1: label-free candidate와 family 경계를 잡는 실패 테스트 작성**

```python
def test_candidate_predicates_do_not_change_when_only_labels_change():
    config = analysis_config()
    original = generate_candidates(rows_with_labels([0, 1, 0, 1]), definitions(config), config)
    flipped = generate_candidates(rows_with_labels([1, 0, 1, 0]), definitions(config), config)
    assert [c.predicate for c in original] == [c.predicate for c in flipped]


def test_empty_and_duplicate_tied_predicates_are_collapsed_before_bh():
    config = analysis_config()
    candidates = generate_candidates(tied_numeric_fixture(), definitions(config), config)
    assert len({c.canonical_predicate for c in candidates}) == len(candidates)
    assert all(c.support > 0 for c in candidates)


def test_candidate_metadata_maps_to_wire_contract_without_labels():
    config = analysis_config()
    furnace = next(c for c in generate_candidates(equipment_category_fixture(), definitions(config), config)
                   if c.field_names == ("furnace_no",))
    assert furnace.application_scope == "EQUIPMENT_SPECIFIC"
    assert furnace.equipment_id == furnace.predicate[0].values[0]
    assert furnace.evidence_family == "CHARGE"


def test_ap_rules_are_retrospective_only():
    config = analysis_config()
    ap = next(c for c in generate_candidates(ap_candidate_fixture(), definitions(config), config)
              if c.first_available_stage == "AP_RECORDED_WITH_RESULT")
    assert ap.early_warning_eligible is False


def test_bh_runs_once_per_global_family_and_keeps_invalid_candidates_as_one():
    config = analysis_config()
    candidates = generate_candidates(global_family_fixture(), definitions(config), config)
    rules = build_quality_rules(global_family_split(candidates), definitions(config), config, CRITERIA_ID)
    assert q_values_by_rule_id(rules) == LITERAL_GLOBAL_FAMILY_Q_VALUES
```

- [ ] **Step 2: 실패 확인 후 numeric/category/interaction 후보 구현**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_quality_intervals.py -q`

Expected: FAIL. Numeric은 discovery type-1 deciles, category는 고유 level, interactions는 spec의 six 3x3 tertile cells만 만든다. 같은 predicate는 하나, empty cell은 생략, invalid candidate p=1이다.

- [ ] **Step 3: 등급/범위 적용 실패 테스트 추가**

```python
def test_danger_requires_stratified_discovery_and_confirmation():
    config = analysis_config()
    rule = only(build_quality_rules(strong_repeated_fixture(), definitions(config), config, CRITERIA_ID))
    assert rule["grade"] == "DANGER"
    assert rule["adjustmentKind"] == "STRATIFIED"
    assert rule["discovery"]["relativeRiskCiLower"] > 1.0


def test_multiple_caution_candidates_are_never_promoted_to_danger():
    config = analysis_config()
    rules = build_quality_rules(caution_only_fixture(), definitions(config), config, CRITERIA_ID)
    assert all(rule["grade"] != "DANGER" for rule in rules)


def test_no_qualified_interval_returns_zero_danger_rules():
    config = analysis_config()
    rules = build_quality_rules(null_fixture(), definitions(config), config, CRITERIA_ID)
    assert sum(r["grade"] == "DANGER" for r in rules) == 0


@pytest.mark.parametrize(
    ("fixture_name", "expected_grade"),
    [("below_support", "INSUFFICIENT_EVIDENCE"),
     ("discovery_only", "UNCONFIRMED"),
     ("confirmed_caution", "CAUTION"),
     ("confirmed_stratified_danger", "DANGER")],
)
def test_grade_boundaries_are_exact(fixture_name, expected_grade):
    config = analysis_config()
    rule = only(build_quality_rules(grade_boundary_fixture(fixture_name), definitions(config), config, CRITERIA_ID))
    assert rule["grade"] == expected_grade


def test_future_stage_context_is_never_used_for_adjustment():
    config = analysis_config()
    rule = only(build_quality_rules(future_context_quality_fixture(), definitions(config), config, CRITERIA_ID))
    assert "hr_thick_band" not in rule["applicationContext"]
    assert "hr_thick_band" in rule["adjustmentFieldsDropped"]


def test_rr_ci_failure_is_explicit_and_cannot_be_danger():
    config = analysis_config()
    rule = only(build_quality_rules(too_few_bootstraps_fixture(), definitions(config), config, CRITERIA_ID))
    assert rule["discovery"]["relativeRiskCiLower"] is None
    assert rule["discovery"]["reasonCode"] == "TOO_FEW_VALID_BOOTSTRAPS"
    assert rule["grade"] != "DANGER"
```

- [ ] **Step 4: exact adjustment hierarchy/FDR/grade 구현**

각 후보는 현재 stage까지 공개된 context만 사용하고 sparse하면 다음 exact hierarchy로 단계별 완화한다.

```text
SM_CC: [sm_plant,steel_grade,steel_usage] -> [steel_grade,steel_usage] -> [steel_grade] -> []
FURNACE: [furnace_no,f_jangip_gubun,steel_grade,steel_usage,slab_width_band]
         -> [furnace_no,f_jangip_gubun,steel_grade,steel_usage]
         -> [furnace_no,f_jangip_gubun,steel_grade]
         -> [furnace_no,f_jangip_gubun] -> [furnace_no] -> []
RM4: [steel_grade,steel_usage,hr_thick_band,hr_width_band]
     -> [steel_grade,steel_usage] -> [steel_grade] -> []
AP: [ap_plant,ap_shift,steel_grade,steel_usage,ap_thick_band,ap_width_band]
    -> [ap_plant,steel_grade,steel_usage] -> [ap_plant,steel_grade]
    -> [ap_plant] -> []
```

후보 자신·bin·결정적 파생값·interaction 두 축·같은 event의 다른 연속 측정값은 층화에서 제거하며, 후보 `firstAvailableStage` 뒤에 공개되는 field도 제거한다. Candidate/comparator가 모두 존재하는 informative stratum 2개와 양쪽 minimum support를 처음 만족한 수준을 고정한다. 어디서도 안 되면 `UNADJUSTED_FALLBACK`이며 최대 CAUTION이다. Numeric/category/interaction 세 전역 BH family를 각각 한 번 보정한다. Grade 우선순위는 `INSUFFICIENT_EVIDENCE → DANGER → CAUTION → UNCONFIRMED → NORMAL`이고 threshold는 Global Constraints exact 값이다. `ruleId`에는 label/stat/grade를 넣지 않는다.

Wire 조립은 `FeatureDefinition`과 Task 2 config의 10개 evidence-family map을 단일 출처로 사용한다. 숫자/상호작용과 비설비 범주는 `PROCESS_GLOBAL/equipmentId=ALL`, `sm_plant|furnace_no|ap_plant` 범주만 `EQUIPMENT_SPECIFIC`이다. AP stage rule은 항상 `earlyWarningEligible=false`; 나머지는 true다. 모든 고유 non-empty 후보는 `NORMAL|UNCONFIRMED|INSUFFICIENT_EVIDENCE`까지 포함해 출력하며 통계 필드를 생략하지 않는다. 독립적으로 같은 grade를 얻은 인접 numeric rule은 통계를 재계산하지 않고 각 원자 rule의 `displayMergeRuleIds`에 정렬된 구성 rule ID만 기록한다.

- [ ] **Step 5: 테스트 통과와 criteria 누출 불변성 확인**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_quality_intervals.py -q`

Expected: PASS. 같은 test file에서 경계·ID·날짜를 고정하고 holdout의 모든 비식별 SM/FUR/AP feature와 `judge`를 변조한 뒤 `canonical_json_bytes(ranges/rules)`가 불변임을, mature reference feature 또는 성숙 `judge`를 바꾸면 달라짐을 검증한다. 여러 CAUTION family가 있어도 DANGER가 되지 않고, 인접 표시 병합 전후 원자 통계·등급이 불변인 test도 PASS한다.

- [ ] **Step 6: 커밋**

```bash
git add analysis/equipment_quality/quality_intervals.py analysis/tests/factories/quality.py analysis/tests/test_quality_intervals.py
git commit -m "feat: derive confirmed quality risk intervals"
```

### Task 7: Replay digest ID와 lockstep event stream

**Files:**
- Create: `analysis/equipment_quality/event_builder.py`
- Create: `analysis/tests/factories/events.py`
- Create: `analysis/tests/test_event_builder.py`

**Interfaces:**
- Produces: `material_key(charge_id: str, slab_no: str) -> str`
- Produces: `batch_id(kind: str, date: date, hour: int | None) -> str`
- Produces: `equipment_batch_id(batch_id: str, equipment_type: str, equipment_id: str) -> str | None`
- Produces: `event_id(event: ReplayEvent) -> str`
- Produces: `build_replay_events(genealogy: GenealogyResult, bundle_id: str, criteria_id: str, config: AnalysisConfig) -> list[ReplayEvent]`
- Produces: `serialize_replay_events(events: Sequence[ReplayEvent]) -> bytes`
- `ReplayEvent` is frozen and has the exact 19 CSV fields in schema order: `schema_version,bundle_id,criteria_id,event_id,replay_date,replay_hour,batch_kind,batch_id,equipment_batch_id,batch_step,time_precision,material_key,equipment_type,equipment_id,charge_id,slab_no,hr_coil_id,ap_prod_id,values_json`. Dates are ISO strings at serialization, nullable scalars become empty CSV cells, and `values_json` is an immutable mapping serialized with Task 1 canonical JSON minus its terminal LF.
- Test-only factories added: `one_material_chain`, `two_furnaces_same_hour`, `event_at`, `duplicated_material_chain`, `two_distinct_materials`; tests define literal `BUNDLE_ID`, `CRITERIA_ID` and the complete `EXACT_19_COLUMN_HEADER` without importing a production header constant.

- [ ] **Step 1: ID preimage와 RM4 null 실패 테스트 작성**

```python
def test_material_key_matches_contract_vector():
    assert material_key("CH1", "1") == (
        "sha256:b313bfca80b92882d529a186bb7fc082e65020c409956481283745421ec1ce5a"
    )


def test_rm4_has_no_equipment_batch_and_keeps_source_furnace_only_in_state():
    rm4 = next(e for e in build_replay_events(one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config())
               if e.batch_step == "RM4_RECORDED")
    assert rm4.equipment_type == "RM4"
    assert rm4.equipment_id == "RM4_PROCESS"
    assert rm4.equipment_batch_id is None
```

- [ ] **Step 2: 실패 확인 후 네 digest ID 구현**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_event_builder.py -q`

Expected: FAIL. 구현은 spec 6.1의 namespace/object를 exact 사용하고 다른 preimage의 hash collision 또는 duplicate event ID를 fatal error로 만든다.

- [ ] **Step 3: lockstep/공개시점 실패 테스트 추가**

```python
def test_same_hour_all_furnaces_advance_in_lockstep():
    events = build_replay_events(two_furnaces_same_hour(), BUNDLE_ID, CRITERIA_ID, analysis_config())
    steps = [event.batch_step for event in events]
    assert steps == [
        "FURNACE_CHARGED", "FURNACE_CHARGED",
        "PREHEAT_COMPLETE", "PREHEAT_COMPLETE",
        "HEAT_COMPLETE", "HEAT_COMPLETE",
        "SOAK_COMPLETE", "SOAK_COMPLETE",
        "FURNACE_EXTRACTED", "FURNACE_EXTRACTED",
        "RM4_RECORDED", "RM4_RECORDED",
    ]


def test_future_identifiers_and_values_are_not_exposed_early():
    charged = event_at(one_material_chain(), "FURNACE_CHARGED")
    assert charged.hr_coil_id is None
    assert charged.ap_prod_id is None
    assert set(charged.values_json) == {"furnace_no", "f_jangip_gubun", "f_jangip_temp", "slab_width"}


def test_missing_newly_revealed_value_keeps_key_with_json_null():
    event = event_at(one_material_chain(missing="f_pre_temp"), "PREHEAT_COMPLETE")
    assert "f_pre_temp" in event.values_json
    assert event.values_json["f_pre_temp"] is None


def test_csv_bytes_use_fixed_header_lf_empty_nulls_and_canonical_values_json():
    payload = serialize_replay_events([event_at(one_material_chain(), "RM4_RECORDED")])
    assert payload.startswith(EXACT_19_COLUMN_HEADER.encode("ascii") + b"\n")
    assert b"\r" not in payload
    assert payload.endswith(b"\n")
    assert b",,RM4_RECORDED,SEQUENCE_ONLY," in payload


def test_duplicate_event_and_digest_preimage_collision_are_fatal(monkeypatch):
    with pytest.raises(ValueError, match="duplicate event"):
        build_replay_events(duplicated_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config())
    monkeypatch.setattr(event_builder, "digest_json_id", lambda *_: "sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="collision"):
        build_replay_events(two_distinct_materials(), BUNDLE_ID, CRITERIA_ID, analysis_config())
```

- [ ] **Step 4: 고정 tuple sort와 stage values 구현**

정렬은 `date,kind rank,hour,batch_id,step rank,equipment sort,material_key,event_id` exact tuple이다. CAST/AP day batch의 실제 시각을 만들지 않고 RM4는 `SEQUENCE_ONLY`, AP 값과 judge는 같은 `AP_RECORDED_WITH_RESULT` event에만 공개한다. 새로 공개할 field 집합은 전달된 `AnalysisConfig.fieldStages`만 사용한다. serializer는 고정 19열, UTF-8/BOM 없음, RFC 4180 quoting, LF, null→빈 cell, canonical compact `values_json`을 사용하고 입력 event가 exact sort tuple 단조증가인지 재검증한다.

- [ ] **Step 5: 테스트 통과와 커밋**

```bash
analysis/.venv/bin/python -m pytest analysis/tests/test_event_builder.py -q
git add analysis/equipment_quality/event_builder.py analysis/tests/factories/events.py analysis/tests/test_event_builder.py
git commit -m "feat: build deterministic historical replay events"
```

### Task 8: Atomic Bundle, Manifest, summary와 CLI

**Files:**
- Create: `analysis/equipment_quality/artifacts.py`
- Create: `analysis/equipment_quality/criteria_projection.py`
- Create: `analysis/equipment_quality/summary.py`
- Create: `analysis/equipment_quality/cli.py`
- Modify: `analysis/equipment_quality/models.py`
- Create: `analysis/tests/test_artifacts.py`
- Create: `analysis/tests/test_cli.py`
- Create: `analysis/tests/factories/artifacts.py`

**Interfaces:**
- Produces: `compute_criteria_identity(as_of: date, criteria_projection: bytes, analysis_config: bytes, producer_runtime: bytes, schema_digests: Mapping[str, str]) -> Identity`
- Produces: `compute_bundle_identity(criteria_id: str, analysis_config: bytes, producer_runtime: bytes, sources: Sequence[SourceFile], schema_digests: Mapping[str, str]) -> Identity`
- Produces: `build_criteria_projection(split: TimeSplitResult, definitions: Sequence[FeatureDefinition]) -> bytes`
- Produces: `build_summary(request: SummaryBuildRequest) -> dict[str, object]`; the frozen request contains inputs, genealogy, split, ranges, rules, events, two IDs and config-derived definitions.
- Produces: `holdout_metrics(split: TimeSplitResult, rules: Sequence[QualityRule], criteria_id: str) -> list[dict[str, object]]`; 정확히 `DANGER`, `CAUTION_OR_DANGER` 순서다.
- Produces: `write_bundle(request: BundleWriteRequest) -> Path`
- Produces: `output_lock(output_root: Path) -> ContextManager[None]`
- Produces: `run_analysis(config_path: Path, runtime_path: Path, data_dir: Path, output_dir: Path) -> Path`
- Produces: CLI `main(argv: Sequence[str] | None = None) -> int`
- `Identity` is frozen `value: str, version: str, fields: Mapping[str, str]`; `BundleWriteRequest` contains six in-memory artifact payloads, two identities, source descriptors, schema descriptors and output root.
- `PopulationLineage` is frozen `population_ref,split,material_keys`; `AggregateLineage` is frozen `artifact_role,rule_id,split,population_ref,input_material_keys,comparator_definition,filters,transformations`. Summary construction validates every referenced material/population/rule and deterministic ordering.
- Criteria identity accepts exactly 8 spec keys and bundle identity exactly 19 spec keys (criteria/config/runtime, 9 source fields, 7 schema fields); missing, extra or wrong role names are fatal before hashing. `schema_digests` must contain exactly the four criteria roles or seven bundle roles respectively.
- Test-only factories added: `bundle_request`, `criteria_projection_fixture`, `mutate_holdout`, `mutate_mature_reference`, `configs_with_one_stage_change`, `stage_surface_snapshot`, `literal_criteria_identity_inputs`, `criteria_inputs_with_extra_schema_role`, `summary_fixture`, `holdout_fixture`, `walk_count_objects`, `assert_unbroken_lineage`, `trace_output_value`, `resolve_aggregate_sources`, `fsync_fault`; `stage_surface_snapshot` runs config-derived definitions, events, ranges, rules and projection on one literal row. `ALL_LITERAL_ARTIFACT_FIELD_PATHS`, two literal rule IDs and expected range/quality source-record sets are hand-authored test constants. `FIXED_ARTIFACT_ROLES` is imported from production and compared with a literal role list in the test. `LITERAL_EIGHT_CRITERIA_FIELDS`와 `LITERAL_CRITERIA_ID`는 standard `shasum`으로 교차확인한 test literal이다.

- [ ] **Step 1: partial publish와 자유경로를 잡는 실패 테스트 작성**

```python
def test_failed_write_never_exposes_final_bundle(tmp_path):
    request = bundle_request(tmp_path, quality_metric=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        write_bundle(request)
    assert list(tmp_path.glob("sha256:*")) == []
    assert not any(p.name == "bundle_manifest.json" for p in tmp_path.rglob("*"))


def test_manifest_has_six_fixed_roles_and_no_path_field(tmp_path):
    root = write_bundle(bundle_request(tmp_path))
    manifest = json.loads((root / "bundle_manifest.json").read_text())
    assert [a["role"] for a in manifest["artifacts"]] == FIXED_ARTIFACT_ROLES
    assert all("path" not in a for a in manifest["artifacts"])


def test_criteria_projection_excludes_holdout_but_includes_mature_reference():
    split = criteria_projection_fixture()
    config = analysis_config()
    original = build_criteria_projection(split, definitions(config))
    assert build_criteria_projection(mutate_holdout(split), definitions(config)) == original
    assert build_criteria_projection(mutate_mature_reference(split), definitions(config)) != original


def test_config_stage_change_drives_definitions_events_ranges_rules_and_projection_together():
    earlier, later = configs_with_one_stage_change()
    assert stage_surface_snapshot(earlier) != stage_surface_snapshot(later)
    assert stage_surface_snapshot(earlier) == stage_surface_snapshot(earlier)


def test_identity_maps_match_literal_id_lines_and_reject_role_drift():
    identity = compute_criteria_identity(**literal_criteria_identity_inputs())
    assert identity.fields == LITERAL_EIGHT_CRITERIA_FIELDS
    assert identity.value == LITERAL_CRITERIA_ID
    with pytest.raises(ValueError, match="schema roles"):
        compute_criteria_identity(**criteria_inputs_with_extra_schema_role())


def test_bundle_validates_every_artifact_against_normative_schema(tmp_path):
    request = bundle_request(tmp_path, quality_rules={"schemaVersion": "unknown/v9"})
    with pytest.raises(ValidationError):
        write_bundle(request)
    assert not any(p.name == "bundle_manifest.json" for p in tmp_path.rglob("*"))


def test_output_lock_rejects_concurrent_writer_without_overwrite(tmp_path):
    with output_lock(tmp_path):
        with pytest.raises(BundleLockError, match="locked"):
            write_bundle(bundle_request(tmp_path))
    assert not any(p.name == "bundle_manifest.json" for p in tmp_path.rglob("*"))


def test_pre_rename_fsync_failure_leaves_no_final_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "fsync_directory", fsync_fault("TEMP_COMPLETE"))
    with pytest.raises(OSError):
        write_bundle(bundle_request(tmp_path))
    assert list(tmp_path.glob("sha256:*")) == []


def test_post_rename_parent_fsync_failure_is_reported_and_rerun_recovers(tmp_path, monkeypatch):
    request = bundle_request(tmp_path)
    monkeypatch.setattr(artifacts, "fsync_directory", fsync_fault("OUTPUT_PARENT_AFTER_RENAME", once=True))
    with pytest.raises(PublishDurabilityError, match="PUBLISH_DURABILITY_UNKNOWN"):
        write_bundle(request)
    visible = list(tmp_path.glob("sha256:*"))
    assert len(visible) == 1
    assert verify_complete_bundle(visible[0], request)
    assert write_bundle(request) == visible[0]
    assert not any(p.name.startswith(".sfep-bundle-tmp-") for p in tmp_path.iterdir())
```

같은 test file에 criteria projection 양방향 누출 test를 먼저 추가한다. `build_criteria_projection`은 적합행만 `hr_date,charge_id,slab_no,hr_coil_id`로 정렬한다. 각 전형범위 line은 exact keys `kind=OPERATING_RANGE_INPUT,hrDate,chargeId,slabNo,hrCoilId,firstAvailableStage,field,context,value`, 각 성숙품질 line은 `kind=MATURE_QUALITY_INPUT,hrDate,chargeId,slabNo,hrCoilId,split,features,judge`를 갖는다. `context`는 그 field의 stage까지 허용된 실제 range context scalar map, `features`는 후보 및 그 stage-safe context 전체를 key 정렬해 넣는다. 같은 소재 안에서는 kind rank, stage rank, field UTF-8 bytes로 정렬해 Task 1 canonical JSON 한 object당 한 line으로 쓴다. Holdout 값/label 변조에는 byte-identical이고 mature reference feature/label 변조에는 달라야 한다.

- [ ] **Step 2: 실패 확인 후 deterministic writers/atomic publish 구현**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_artifacts.py -q`

Expected: FAIL. `<output-root>/.sfep-equipment-quality.lock`을 열고 Darwin `fcntl.flock(LOCK_EX|LOCK_NB)`를 전체 publish 수명 동안 유지하며 contention은 `BundleLockError`로 즉시 실패한다. JSON/CSV writer는 finite/canonical exact bytes를 확인하고 config/runtime/ranges/rules/summary와 replay 각 row를 Task 2 schema로 validate한 뒤 artifact별 fsync/hash, Manifest last, temp-directory fsync와 same-filesystem atomic rename을 사용한다. 임시 directory는 `<output-root>`의 직접 자식이다. 기존 same bundle ID가 byte-identical이면 재사용하고 하나라도 다르면 `NON_DETERMINISTIC_BUNDLE`로 실패한다. validation/file-fsync/temp-directory-fsync/rename 실패는 최종 bundle과 stale temp를 남기지 않는다. rename 성공 뒤 output-parent fsync만 실패하면 이미 보이는 final을 삭제하지 않고 완전한 bytes를 재검증한 뒤 `PublishDurabilityError(PUBLISH_DURABILITY_UNKNOWN)`로 실패한다; 다음 동일 요청은 lock 아래 final을 재검증하고 parent fsync에 성공하면 byte-identical bundle을 재사용한다. 어떤 경로도 불완전 final을 성공으로 반환하지 않는다.

- [ ] **Step 3: summary/holdout metric 실패 테스트 추가**

```python
def test_count_objects_balance_and_censored_is_never_nondefect():
    summary = build_summary(summary_fixture())
    for count in walk_count_objects(summary):
        assert count["total"] == count["defects"] + count["nonDefects"] + count["unknownOrCensored"]
    assert summary["labelCensoringCounts"]["reference"]["unknownOrCensored"] == 1


def test_every_output_field_and_material_resolves_to_source_or_declared_derivation():
    summary = build_summary(summary_fixture())
    assert_unbroken_lineage(summary, ALL_LITERAL_ARTIFACT_FIELD_PATHS)
    resolved = trace_output_value(summary, artifact_role="replay_events",
                                  output_field="values_json.f_pre_temp",
                                  material_key=LITERAL_MATERIAL_KEY)
    assert resolved == {"role": "fur_hr", "name": "sts_2fur_hr_2.csv", "recordNumber": 4,
                        "sourceColumn": "f_pre_temp", "conversion": "PARSE_FINITE_BINARY64"}


def test_range_and_quality_aggregate_lineage_resolve_all_stat_inputs_to_source_records():
    summary = build_summary(summary_fixture())
    range_records = resolve_aggregate_sources(summary, artifact_role="equipment_operating_ranges",
                                              rule_id=LITERAL_RANGE_RULE_ID, split="REFERENCE")
    quality_records = resolve_aggregate_sources(summary, artifact_role="quality_risk_intervals",
                                                rule_id=LITERAL_QUALITY_RULE_ID, split="DISCOVERY")
    assert range_records == LITERAL_RANGE_SOURCE_RECORDS
    assert quality_records == LITERAL_DISCOVERY_CANDIDATE_AND_COMPARATOR_RECORDS
    assert all(ref["recordNumber"] >= 1 for ref in range_records + quality_records)


def test_holdout_alert_positive_requires_pre_ap_danger():
    split, rules = holdout_fixture(caution_then_ap_defect=True)
    danger = holdout_metrics(split, rules, CRITERIA_ID)[0]
    assert danger["alertGrade"] == "DANGER"
    assert danger["alertRate"]["pointEstimate"] == 0.0
    assert danger["baseDefectRate"]["pointEstimate"] == 1.0


def test_holdout_danger_before_ap_is_alert_positive_and_charge_bootstrapped():
    split, rules = holdout_fixture(pre_ap_danger_then_ap_defect=True)
    danger = holdout_metrics(split, rules, CRITERIA_ID)[0]
    assert danger["alertGrade"] == "DANGER"
    assert danger["truePositive"] == 1
    assert danger["precision"]["pointEstimate"] == 1.0
    assert danger["precision"]["validReplicates"] >= 1900
```

Holdout point metrics는 spec 10의 TP/FP/TN/FN 식을 exact 사용한다. AP 결과 공개 전 도착했고 `earlyWarningEligible=true`인 DANGER rule 하나 이상과 매칭된 `hr_coil_id`만 DANGER profile의 alert-positive다; CAUTION 포함 값은 별도 `CAUTION_OR_DANGER` profile이며 등급을 올리지 않는다. 각 profile의 여섯 metric은 독립 `pointEstimate,lower,upper,validReplicates,reasonCode` object다. Holdout CI는 `criteriaId + NUL + "holdout-bootstrap-v1"`의 UTF-8 SHA-256 seed, Charge block 2,000회, Task 5와 같은 uint64be index sampler, type-1 2.5/97.5 percentile, 최소 유효 1,900회를 사용한다. 분모 0은 null point/CI, 0 replicate, `ZERO_DENOMINATOR`; 유효회수 부족은 non-null point, null CI, 1,900 미만 replicate, `TOO_FEW_VALID_BOOTSTRAPS`다.

- [ ] **Step 4: CLI absolute-path/fail-closed 실패 테스트 작성**

```python
def test_cli_requires_four_absolute_paths(tmp_path, capsys):
    code = main(["--config", "relative.json", "--runtime-manifest", "runtime.json",
                 "--data-dir", "data", "--output-dir", "out"])
    assert code == 2
    assert "absolute" in capsys.readouterr().err
```

- [ ] **Step 5: summary와 orchestration 구현 후 tests 통과**

`run_analysis`는 네 path의 absolute/non-symlink/expected kind를 확인하고 config/schema/runtime identity를 fail-closed 검증한 뒤 한 `AnalysisConfig` instance에서 definitions를 만든다. 순서는 `read/validate → genealogy → split → criteria projection/criteriaId → ranges/rules → bundleId → events/summary → atomic write`로 고정한다. `analysis_summary.json`에는 balanced split counts, exact quarantine/censor counts, 분리된 outer/inner Charge·row purge counts, source column profile, reference-vs-holdout drift, 두 ordered holdout profile, normalized lineage와 `LOCKED_RETROSPECTIVE_HOLDOUT` mode를 포함한다. `lineage.fields`는 여섯 artifact와 Manifest의 모든 leaf field path를 raw source column 또는 명명된 deterministic conversion/dependency에 연결하고, `lineage.materials`는 각 material의 source role/name/record ordinal을 연결한다. `lineage.populations`는 정확히 REFERENCE/DISCOVERY/CONFIRMATION/HOLDOUT 순서로 소재집합을 한 번씩 고정하고, `lineage.aggregates`는 range를 REFERENCE population, quality rule을 matching DISCOVERY/CONFIRMATION population에 묶어 실제 input material, closed filter/transformation/comparator 구성을 연결한다. 따라서 range/rule의 support·불량수·분위수·표준화·RR/CI도 population과 material ref를 따라 원본 record까지 역추적되어야 한다. Replay는 `materialKey|batchStep|values_json key`를 field/material 표에 대입해 추적하되 네 identifier는 top-level CSV path로 추적한다. `analysis_summary.lineage` subtree 자체는 다른 lineage entry를 재귀 요구하지 않는 유일한 metadata 예외이며, `LINEAGE_INDEX_V1` 변환으로 genealogy+definitions+split+ranges+rules/events에서 생성되고 모든 참조 무결성·정렬·cycle·role별 terminal·point/CI consistency를 schema/application tests로 검증한다. Source profile은 각 원본 column의 role/dtype/total/missing/unique와 numeric type-1 p05/median/p95 또는 category/date frequency를 정렬해 기록한다. Drift는 각 monitored/context field의 reference/holdout support·missing rate와 numeric p05/median/p95 또는 categorical level frequency를 양쪽에 나란히 기록할 뿐 임의 score나 threshold를 만들지 않는다. Holdout을 기준 재학습에 전달하는 함수 경로는 만들지 않는다.

Run:

```bash
analysis/.venv/bin/python -m pytest analysis/tests/test_artifacts.py analysis/tests/test_cli.py -q
analysis/.venv/bin/python -m pytest analysis/tests -q
```

Expected: 모두 PASS, warning 없음.

- [ ] **Step 6: 커밋**

```bash
git add analysis/equipment_quality analysis/tests/factories/artifacts.py analysis/tests/test_artifacts.py analysis/tests/test_cli.py
git commit -m "feat: publish atomic equipment quality bundles"
```

### Task 9: Reproducible wheel/runtime identity와 독립 golden seal

**Files:**
- Create: `analysis/bootstrap.lock`
- Create: `analysis/build-requirements.lock`
- Create: `analysis/requirements.lock`
- Create: `analysis/wheelhouse.lock.json`
- Create: `analysis/equipment_quality/runtime_verify.py`
- Create: `analysis/tools/build_producer.py`
- Create: `analysis/tools/seal_producer_build.py`
- Create: `analysis/tools/seal_runtime.py`
- Create: `analysis/tools/seal_golden_bundle.py`
- Create: `analysis/tests/test_runtime_identity.py`
- Create: `analysis/tests/test_golden_seal.py`
- Create: `analysis/tests/factories/runtime.py`
- Create after build: `analysis/producer.lock`
- Create after runtime seal: `analysis/producer_runtime.json`
- Create after independent seal: `contracts/equipment-monitor/v1/golden-bundle/*`

**Interfaces:**
- Produces: `installed_code_tree(distribution: Distribution) -> str`
- Produces: `verify_runtime(manifest_path: Path, environment: RuntimeEnvironment | None = None) -> RuntimeIdentity`; production uses the real process when `environment` is null and tests inject a frozen adapter.
- Produces: stdlib-only build/runtime/golden seal CLIs that never import `equipment_quality`
- `RuntimeIdentity` is frozen and contains schema version, producer/source/build provenance, platform/interpreter fields, immutable distribution records and environment policy; `RuntimeIdentityError` is the single fail-closed validation exception.
- Test-only factories added: `fake_distribution`, `runtime_manifest_fixture`, `write_manifest`, `mutate_runtime_case`, `two_wheels`, `run_build_seal`, `run_producer_build`, `source_tree_bytes`, `wheel_resource`, `independently_expected_provenance`, `golden_expectation_copy`, `frozen_runtime`, `semantic_projection`, `hand_authored_semantic_projection`, `no_tokens_remain`; fixed semantic expectations and ID hashes are literals, while the evolving source-tree digest is recomputed only by a test-side implementation independent of production.

- [ ] **Step 1: runtime path-independence와 provenance 실패 테스트 작성**

```python
def test_installed_code_tree_ignores_path_dependent_installer_files(tmp_path):
    first = fake_distribution(tmp_path / "one", direct_url="/one/checkout")
    second = fake_distribution(tmp_path / "two", direct_url="/two/checkout")
    assert installed_code_tree(first) == installed_code_tree(second)


def test_runtime_rejects_embedded_source_digest_mismatch(runtime_manifest_fixture):
    runtime_manifest_fixture["producer"]["sourceSha256"] = "sha256:" + "0" * 64
    with pytest.raises(RuntimeIdentityError, match="source provenance"):
        verify_runtime(write_manifest(runtime_manifest_fixture))


@pytest.mark.parametrize(
    "mutation",
    ["platform", "architecture", "cpython_version", "python_build", "interpreter_bytes",
     "pythonhashseed", "timezone", "lock_digest", "wheel_digest", "installed_code_tree",
     "distribution_version", "missing_embedded_provenance"],
)
def test_runtime_identity_mutations_fail_closed(runtime_manifest_fixture, mutation):
    manifest, environment = mutate_runtime_case(runtime_manifest_fixture, mutation)
    with pytest.raises(RuntimeIdentityError):
        verify_runtime(write_manifest(manifest), environment=environment)
```

- [ ] **Step 2: 실패 확인 후 runtime verifier 구현**

Run: `analysis/.venv/bin/python -m pytest analysis/tests/test_runtime_identity.py -q`

Expected: FAIL then PASS. Runtime은 source/wheel archive를 재구성하지 않고 embedded `sfep_producer_provenance.json`, platform/CPython/interpreter bytes, installed distribution/version/code-tree와 env policy만 검증한다.

- [ ] **Step 3: hash lock과 verified wheelhouse 생성**

Darwin arm64 CPython 3.12용 wheel만 다운로드하고 각 archive를 `shasum -a 256`으로 기록한다. `bootstrap.lock`은 pip 25.1.1, `build-requirements.lock`은 build/setuptools/wheel, `requirements.lock`은 runtime 직접/전이 의존성을 모두 `--hash=sha256:<hex>`로 고정한다. `wheelhouse.lock.json`은 filename/tag/archive digest를 정렬한다.

Run:

```bash
analysis/.venv/bin/python -m pip install --dry-run --no-index --only-binary=:all: --require-hashes --find-links analysis/.wheelhouse -r analysis/requirements.lock
```

Expected: exit 0, network access 없음.

- [ ] **Step 4: seal 도구 실패 테스트 작성**

```python
def test_build_seal_requires_two_byte_identical_wheels(tmp_path):
    wheel_a, wheel_b = two_wheels(tmp_path, second_payload=b"different")
    result = run_build_seal(wheel_a.parent, wheel_b.parent)
    assert result.returncode != 0
    assert not producer_lock_path(tmp_path).exists()


def test_golden_seal_changes_only_allowed_tokens_and_provenance(tmp_path):
    sealed = seal_golden(golden_expectation_copy(tmp_path), frozen_runtime(tmp_path))
    assert semantic_projection(sealed) == hand_authored_semantic_projection()
    assert no_tokens_remain(sealed)


def test_build_stages_embedded_provenance_without_mutating_source(tmp_path):
    before = source_tree_bytes(ANALYSIS_ROOT)
    wheel_a, wheel_b = run_producer_build(ANALYSIS_ROOT, tmp_path)
    assert source_tree_bytes(ANALYSIS_ROOT) == before
    assert wheel_a.read_bytes() == wheel_b.read_bytes()
    assert wheel_resource(wheel_a, "equipment_quality/sfep_producer_provenance.json") == (
        independently_expected_provenance(ANALYSIS_ROOT)
    )
```

- [ ] **Step 5: stdlib-only seal 도구와 reproducible build 구현**

Seal scripts는 Python 표준라이브러리만 사용하고 `equipment_quality` import를 AST 검사로 금지한다. `build_producer.py`도 package를 import하지 않으며 원본 `analysis/`를 두 임시 staging root에 byte-for-byte 복사하고, source digest에서 제외되는 generated `equipment_quality/sfep_producer_provenance.json`을 각 staging tree에 canonical bytes로 쓴다. 이 resource에는 package version, source digest, `SOURCE_DATE_EPOCH=1735689600`, 제3자 lock digest만 들어가며 절대경로/시각은 없다. Task 1 package-data 설정으로 wheel 안에 정확히 한 번 포함되는지 zip test로 검증한다. 원본 source tree에는 resource를 생성하거나 수정하지 않는다. 같은 환경변수로 staging root 각각에서 wheel을 만든 뒤 두 archive digest가 같을 때만 seal 도구가 producer wheel을 wheelhouse에 게시하고 hash-locked `producer.lock`을 쓴다.

- [ ] **Step 6: 두 절대 venv에서 runtime/Bundle 동일성 검증**

Run design spec 7.1의 build A/B → producer lock → runtime seal 순서를 `/private/tmp/sfep-runtime-a`와 `/private/tmp/sfep-runtime-b`에서 반복한다.

Expected: producer wheel SHA, installed-code-tree, `producer_runtime.json` bytes, golden Bundle bytes가 모두 동일하다.

- [ ] **Step 7: golden byte oracle와 전체 tests 통과**

Run:

```bash
analysis/.venv/bin/python -m pytest analysis/tests/test_runtime_identity.py analysis/tests/test_golden_seal.py -q
analysis/.venv/bin/python -m pytest analysis/tests -q
```

Expected: PASS. Python producer가 `golden-source`에서 sealed `golden-bundle`을 byte-for-byte 재현한다.

- [ ] **Step 8: 커밋**

```bash
git add analysis contracts/equipment-monitor/v1/golden-bundle
git commit -m "feat: seal reproducible producer runtime and golden bundle"
```

### Task 10: 실제 CSV Bundle 생성과 누출·결정론 검증

**Files:**
- Create: `analysis/tests/conftest.py`
- Create: `analysis/tests/test_real_data_invariants.py`
- Modify: one explicitly named `analysis/equipment_quality/<module>.py` only when a small synthetic regression test first reproduces a real-data defect
- Do not track: `var/equipment-quality/**`

**Interfaces:**
- Consumes: all prior producer interfaces
- Produces: local immutable `<output-dir>/<bundleId>` for the actual three CSV files
- `conftest.py` exposes `real_data_dir`: it resolves `SFEP_STEEL_DATA_DIR` as an absolute existing directory and otherwise calls `pytest.skip`; it never guesses a repository-relative path. `run_actual_bundle` invokes `/private/tmp/sfep-runtime-a/bin/python -m equipment_quality.cli` in a subprocess with `PYTHONHASHSEED=0,TZ=Asia/Seoul`; it never imports or executes the producer from the development venv.

- [ ] **Step 1: 실제 데이터 invariants 실패 테스트 작성**

```python
@pytest.mark.real_data
def test_actual_snapshot_matches_known_shape_and_genealogy(real_data_dir):
    inputs = read_inputs(real_data_dir)
    assert inputs.sm_cc.shape == (23649, 15)
    assert inputs.fur_hr.shape == (23652, 26)
    assert inputs.ap.shape == (23641, 9)
    assert set(inputs.fur_hr["furnace_no"].dropna().unique()) == {"1호기", "2호기", "3호기", "4호기"}


@pytest.mark.real_data
def test_actual_judge_counts_are_631_defect_and_23010_good(real_data_dir):
    inputs = read_inputs(real_data_dir)
    assert inputs.ap["judge"].value_counts().to_dict() == {"양품": 23010, "불량": 631}


@pytest.mark.real_data
def test_two_actual_bundle_runs_have_identical_relative_files_sizes_and_sha256(real_data_dir, tmp_path):
    first = run_actual_bundle(real_data_dir, tmp_path / "a")
    second = run_actual_bundle(real_data_dir, tmp_path / "b")
    assert bundle_file_snapshot(first) == bundle_file_snapshot(second)
```

- [ ] **Step 2: 실제 데이터 snapshot 검증 실행**

Run: `SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data analysis/.venv/bin/python -m pytest analysis/tests/test_real_data_invariants.py -q`

Expected: 세 test PASS. `bundle_file_snapshot`은 bundle root 아래의 모든 상대 basename, size와 SHA-256을 UTF-8 byte 순으로 모은 test-only helper다. 실패하면 원인을 작은 synthetic failing regression test로 먼저 재현하고 그 실패를 확인한 뒤 최소 production fix를 적용한다.

- [ ] **Step 3: 실제 Bundle 두 번 생성**

Run:

```bash
PYTHONHASHSEED=0 TZ=Asia/Seoul /private/tmp/sfep-runtime-a/bin/python -m equipment_quality.cli --config /Users/haechan/Desktop/SFEP/analysis/analysis_config.json --runtime-manifest /Users/haechan/Desktop/SFEP/analysis/producer_runtime.json --data-dir /Users/haechan/프로젝트/sfep-steel/steel-data --output-dir /Users/haechan/Desktop/SFEP/var/equipment-quality/run-a
PYTHONHASHSEED=0 TZ=Asia/Seoul /private/tmp/sfep-runtime-a/bin/python -m equipment_quality.cli --config /Users/haechan/Desktop/SFEP/analysis/analysis_config.json --runtime-manifest /Users/haechan/Desktop/SFEP/analysis/producer_runtime.json --data-dir /Users/haechan/프로젝트/sfep-steel/steel-data --output-dir /Users/haechan/Desktop/SFEP/var/equipment-quality/run-b
```

Expected: 두 directory의 `bundleId`, 파일명, size, SHA-256가 모두 같다. `bundle_manifest.json`은 각각 마지막 완료표식이다.

- [ ] **Step 4: holdout 누출 양방향 mutation 검증**

실제 전체 파일을 수정하지 않고 temp copy에서 연결된 `hr_date>T` 한 소재의 non-ID feature와 judge를 바꾼다. Expected: `criteriaId`와 두 criteria JSON bytes는 동일하고 `bundleId`, replay, summary는 변한다. 반대로 mature reference judge 하나를 바꾸면 `criteria_projection_sha256`과 `criteriaId`가 변한다.

- [ ] **Step 5: 전체 Python test/contract 검증**

Run:

```bash
SFEP_STEEL_DATA_DIR=/Users/haechan/프로젝트/sfep-steel/steel-data analysis/.venv/bin/python -m pytest analysis/tests -q
git status --short
git check-ignore -q var/equipment-quality/run-a
```

Expected: all PASS, Git에는 test/code 변경만 있고 real Bundle은 ignored.

- [ ] **Step 6: 커밋**

```bash
git add analysis/tests/conftest.py analysis/tests/test_real_data_invariants.py analysis/equipment_quality
git commit -m "test: verify producer against actual steel snapshot"
```

## Plan Self-Review Checklist

- [x] Spec 4, 6, 6.1, 7.1, 8~14, 16, 18 Python, 19의 producer 완료조건이 Task 1~10에 연결된다.
- [x] 모든 새 production 동작은 먼저 실패하는 실제 behavior test가 있다.
- [x] 미완성 표시문구, 임의 예시 통계값, 모델/예측/API 구현이 없다.
- [x] Task 간 함수명과 dataclass 이름이 Interfaces 블록에서 일치한다.
- [x] 실제 Bundle과 venv/wheelhouse는 Git에 추가되지 않는다.
- [x] Java/Swing과 Shadow 제거는 각각 별도 후속 계획으로 남겨 이 계획의 review surface를 Python producer로 제한한다.

## Execution Selection

사용자가 실제 구현 시작을 요청했으므로 별도 대기 없이 추천 방식인 `superpowers:subagent-driven-development`로 실행한다. 승인된 spec의 contract-first gate를 지키기 위해 실행순서는 `Preflight → Task 2 normative contract → Task 1 deterministic foundation → Task 3~10`으로 고정한다. 각 Task는 fresh implementer, task-scoped spec/quality review, 최대 5회 fix loop를 거치며 원격 Push는 하지 않는다.
