# SFEP Docs

이 폴더는 SFEP 프로젝트의 성능 실험과 포트폴리오 작성 자료를 관리한다.

문서는 두 종류로 나눈다.

1. 공개용 문서
2. 로컬 학습용 Step 문서

---

## 공개용 문서

| 파일 | 역할 | git 포함 여부 |
| --- | --- | --- |
| `SFEP_FINAL_SUMMARY.md` | 지금까지 진행한 전체 프로젝트 최종 요약 | 포함 |
| `SFEP_PORTFOLIO_NOTION.md` | Arcane Notion 템플릿과 같은 흐름으로 정리한 포트폴리오용 문서 | 포함 |
| `performance-experiment.md` | 전체 성능 개선 계획과 주요 실험 결과 정리 | 포함 |
| `performance-check-template.md` | 앞으로 성능을 측정할 때 사용할 공통 기록 템플릿 | 포함 |
| `README.md` | 이 docs 폴더의 문서 목차 | 포함 |

공개용 문서는 GitHub에 올라가도 되는 수준으로 작성한다.

너무 상세한 개인 메모보다는 다음 내용 위주로 남긴다.

- 실험 목적
- AS-IS / TO-BE
- 측정 환경
- 측정 결과
- 병목 정의
- 다음 개선 결정

---

## 로컬 학습용 Step 문서

| 파일 | 내용 | git 포함 여부 |
| --- | --- | --- |
| `SFEP_STEP01.md` | Direct DB Write와 초기 병목 설명 | 제외 |
| `SFEP_STEP02.md` | JDBC Batch Insert와 최신 상태 upsert 설명 | 제외 |
| `SFEP_STEP03.md` | Kafka 도입과 API/DB 저장 흐름 분리 설명 | 제외 |
| `SFEP_STEP04.md` | Consumer 병렬화와 batch/pool 조합 설명 | 제외 |
| `SFEP_STEP05.md` | 알림 지연 측정, 알림 Consumer 분리, retry/DLT 설명 | 제외 |
| `SFEP_STEP06.md` | Kafka partition/concurrency 조합 비교 설명 | 제외 |

이 파일들은 너한테 코드 흐름을 쉽게 설명하기 위한 문서다.

그래서 `.gitignore`에서 다음 규칙으로 제외한다.

```gitignore
/docs/SFEP_STEP*.md
```

앞으로도 단계별 상세 설명은 이 이름 규칙을 따른다.

```text
docs/SFEP_STEP07.md
docs/SFEP_STEP08.md
...
```

---

## 현재 진행 상태

| 단계 | 주제 | 핵심 결과 |
| --- | --- | --- |
| FINAL | 전체 요약 | 프로젝트 목적, 구조, 단계별 개선, 성능 결과, 현재 병목을 한 문서로 정리 |
| PORTFOLIO | Notion 포트폴리오 정리 | Arcane 템플릿과 같은 개요/기능/역할/아키텍처/개선 사항 형식으로 정리 |
| STEP01 | Direct DB Write 기준선 | 단순 구조의 저장 병목 확인 |
| STEP02 | JDBC Batch Insert | 10,000건 저장 시간을 초 단위에서 수백 ms 수준으로 개선 |
| STEP03 | Kafka 도입 | API 요청 흐름과 DB 저장 흐름 분리 |
| STEP04 | Batch / poll / pool 조합 | Consumer와 DB batch 저장 조합별 병목 확인 |
| STEP05 | 알림 지연 측정 / Consumer 분리 | 저장 경로와 알림 경로를 분리하고 p95/p99 알림 지연 측정 |
| STEP06 | Partition / concurrency 비교 | 무작정 병렬화하면 DB write 경합으로 성능이 떨어질 수 있음을 확인 |

---

## 다음 문서 작성 규칙

새로운 개선을 진행하면 다음 순서로 문서를 남긴다.

1. `performance-check-template.md` 형식으로 측정 기준을 잡는다.
2. 구현 후 수치를 측정한다.
3. `SFEP_STEP숫자.md`에 코드 흐름과 결과를 쉽게 설명한다.
4. 공개 가능한 핵심 결과만 `performance-experiment.md`에 요약한다.

문서에는 반드시 다음 질문에 대한 답이 있어야 한다.

- 왜 이 실험을 했는가?
- 어떤 코드를 바꿨는가?
- 어떤 지표가 좋아졌는가?
- 오히려 나빠진 지표는 무엇인가?
- 그래서 다음 병목은 무엇인가?
