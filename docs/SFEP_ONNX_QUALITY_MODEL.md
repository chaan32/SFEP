# SFEP 설비 모니터의 ONNX 불량확률

Swing 모니터는 가열로 출측 이벤트에서 그 시점까지 공개된 공정값을 사용해 소재의 불량확률을 계산한다. 소재 상세 화면에는 이 확률과 AP 공정에서 나중에 기록된 실제 결과를 함께 표시한다.

Python 분석기가 v3 번들에 ONNX 모델과 평가 메타데이터를 추가한다. Java는 모델과 메타데이터를 번들 식별자 및 해시에 대조하고, 입력 특성·출측 이벤트·ONNX 입출력 계약을 확인한다. Swing 세션은 검증된 모델 바이트를 열어 재생 중에 사용하고 창을 닫을 때 세션을 정리한다. v1·v2 번들은 기존 통계 기준으로 재생하며 불량확률 칸에는 `ONNX 모델 없음`을 표시한다.

Swing이 번들 안의 모델만 열도록 한 이유는 화면이 검증되지 않은 별도 로컬 파일을 실행하지 않게 하기 위해서다. `criteriaId`는 통계 기준, `modelId`는 모델 내용을 식별하고, 번들 전체의 `bundleId`가 둘을 함께 묶는다.

## 실제 데이터 평가

시간순 검증의 Average Precision(AP)은 Random Forest 0.04343, HGB 0.02698, 로지스틱 회귀 0.01728이었다. 검증 순위가 가장 높고 ONNX로 변환할 수 있는 Random Forest를 선택했다. Holdout 결과를 보기 전에 임계값을 0.314185로 고정했다.

| 지표 | 결과 |
| --- | ---: |
| Holdout 전체 / 평가 가능 / 실제 불량 | 6,730 / 6,726 / 282 |
| Recall / Precision | 52.48% / 9.47% |
| Holdout AP / 실제 불량 비율 | 0.09072 / 4.19% |
| TP / FP / TN / FN | 148 / 1,415 / 5,029 / 134 |
| Brier Skill | −0.95056 |
| sklearn ↔ Python ONNX 최대 절대 확률 차이 | 3.421 × 10⁻⁷ |
| Python ONNX ↔ Java ONNX 최대 절대 확률 차이 | 0.0 (실제 모델 검증 벡터 10개) |
| Java 1건 평균 / 중앙값 / p95 | 0.01649 / 0.01467 / 0.01917 ms |

Brier Skill이 0보다 낮아 이 모델은 `RESEARCH_ONLY`다. 독립 평가에서 불량 순위를 구분하는 신호는 있었지만 확률 점수가 기준 확률보다 부정확했다. Swing에서 `연구용`으로 표시하며 모델 경보는 발행하지 않는다. 설비 운전범위와 과거 품질 위험구간 경보는 각각 기존 기준을 적용한다.

Java 지연 시간은 Apple M1 Pro, JDK 21, ONNX Runtime 1.31.1에서 측정했다. 예열 1,000회 뒤 10,000회에 걸쳐 입력 벡터 생성부터 추론 결과 처리까지 잰 수치다. 모델 로딩 및 전체 화면 재생 시간은 포함하지 않는다.

실제 데이터로 봉인된 Python 런타임 두 개에서 v3 번들을 각각 생성했다. 두 번들의 ID는 `sha256:ab9d16a8504ec8e00c4195c7e08dd86507a3fa04cec01a20164acac426e7c8c9`로 같고, manifest를 포함한 파일 9개가 모두 바이트 단위로 일치했다. 실제 v2와 v3를 같은 JVM에서 전체 재생했을 때 통계 상태와 모델 외 경보의 정규화 digest도 일치했다. v3는 189,043개 이벤트, 23,631개 소재의 확률을 처리했고 모델 경보는 0건이었다.

최종 Java `check`는 136개 통과, 환경 입력이 없는 14개 건너뜀, 실패 0개였다. 실제 번들 전용 테스트는 v1 4개, v1↔v2 1개, v2↔v3 1개가 각각 통과했고 `bootJar`도 생성됐다. 대용량 실제 번들을 연속 로드하는 v2/v3 전용 테스트에는 2GB 힙을 사용한다.
Python 전체 테스트는 1,630개 통과, 환경 입력이 없는 31개 건너뜀, 실패 0개였다. 실제 v3 생성 두 번과 Java 실제 번들 전용 테스트는 별도로 실행했다.

## 실행과 검증

봉인된 Python 런타임과 실제 CSV가 있는 환경에서는 v3 설정으로 번들을 생성한다. 명령이 출력하는 번들 디렉터리를 Java 실행에 전달한다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 TZ=Asia/Seoul /absolute/path/to/sealed-venv/bin/python -m equipment_quality.cli \
  --config "$PWD/analysis/analysis_config_v3.json" \
  --runtime-manifest "$PWD/analysis/producer_runtime.json" \
  --data-dir /absolute/path/to/steel-data \
  --output-dir "$PWD/var/equipment-quality/v3"
```

Java 21과 GUI 환경에서 저장소 루트에서 실행한다. 번들 경로는 절대 경로다.

```bash
./sfep-server/gradlew -p equipment-monitor bootRun \
  --args="--sfep.equipment-monitor.bundle-dir=/absolute/path/to/v3-bundle"
```

합성 v3 번들은 `contracts/equipment-monitor/v3/golden-bundle`에 있다. 기본 Java 테스트는 이 번들의 Python·Java 인코딩 및 추론 일치와 파일 변조 거부를 검사한다. 실제 번들의 통계 재생 비교와 화면 검증은 다음 명령으로 실행한다.

```bash
SFEP_ACTUAL_BUNDLE_V2=/absolute/path/to/v2-bundle \
SFEP_ACTUAL_BUNDLE_V3=/absolute/path/to/v3-bundle \
  ./sfep-server/gradlew -p equipment-monitor actualBundleV3Test
```

실제 CSV는 로컬 `steel-data`에, 학습 모델과 실제 번들은 저장소의 무시 경로인 `var/equipment-quality/`에 둔다. 합성 golden만 Git에 포함한다.
