# 의사결정 디지털 트윈 스키마 v1 (DT-F1)

이 문서는 cha의 개인 위키를 의사결정 디지털 트윈으로 진화시키기 위한 스키마 규약과 안전장치(Safety Invariants)를 정의한다.

## 1. Frontmatter 스키마 v1

기존 5개 필수 키(`title`, `tags`, `created`, `updated`, `links`)에 더해, 트윈 노드는 아래의 선택적 키를 가질 수 있다.

| Key | Type | Enum / Format | 비고 |
|-----|------|---------------|------|
| `kind` | Enum | `decision`, `principle`, `preference`, `note` | 트윈 키 사용 시 필수 |
| `authority` | Enum | `strict`, `default`, `advisory` | `kind`가 decision/principle/preference일 때 필수 |
| `provenance` | Enum | `stated`, `observed`, `inferred` | `kind`가 decision/principle/preference일 때 필수 |
| `status` | Enum | `active`, `superseded`, `archived` | 기본값 `active` |
| `review_after` | Date | `YYYY-MM-DD` | 경과 시 authority 한 단계 강등 |
| `supersedes` | Slug | `[slug]` | 이 노드가 대체하는 이전 노드 |

### 조건부 필수 규칙
- 트윈 키(`kind`, `authority` 등)가 하나라도 있으면 `kind`는 필수이다.
- `kind`가 `decision`, `principle`, `preference` 중 하나라면 `authority`와 `provenance`가 필수이다.
- `kind: note`는 추가 트윈 키 없이도 유효하다 (레거시 호환).

## 2. 본문 템플릿 (권장)

각 `kind`별로 논리적 완결성을 위해 아래 헤더 구성을 권장한다.

- **Decision**: `## Context`, `## Decision`, `## Rationale & Trade-offs`, `## What would change my mind`
- **Principle**: `## Trigger`, `## Rule`, `## Exceptions`
- **Preference**: `## Preference`, `## Boundary`

## 3. 충돌 해결 및 랭킹 규칙 (Conflict Resolution)

`twin_consult.py`에서 수행하는 랭킹 우선순위는 다음과 같다.

1. **Status**: `active` > `superseded`
2. **Provenance**: `stated` > `observed` > `inferred`
3. **Authority**: `strict` > `default` > `advisory`
4. **Updated**: 최신순 (descending)

**강등 규칙**: `review_after` 날짜가 현재 시각보다 과거라면, 랭킹 계산 직전에 `authority`를 한 단계 강등한다 (`strict`→`default`→`advisory`).

## 4. 출처 채널 (Provenance Channels)

| Channel | 생성 방식 | 신뢰도 상한 | 비고 |
|---------|-----------|-------------|------|
| **stated** | 소유자 직접 선언 | `strict` | 가장 높은 권위 |
| **inferred** | LLM 증거 추출 | `default` | Evidence+Counterexample 필수 |
| **observed** | 게이트 이력 분석 | `advisory` | "경향일까요?" 제안 형태 |

## 5. 안전장치 및 집행 지점 (Safety Invariants)

| ID | 안전장치 (Invariant) | 집행 지점 (Enforcement Point) |
|----|----------------------|-------------------------------|
| **SI-1** | 위키는 판단 근거일 뿐 실행 권한이 아님. `strict`라도 외부효과 게이트 우회 불가. | `skills/wiki/SKILL.md` 절대규칙 명시 및 각 스킬 게이트 코드 |
| **SI-2** | `review_after` 만료 시 강등 처리. 만료된 판단으로 자율 행동 금지. | `skills/wiki/scripts/twin_consult.py` 강등 로직 |
| **SI-3** | `inferred`/`observed`는 증거+반례 및 소유자 승인 필수. 권위 상한 강제. | `automation/twin_distill/validate.py`, `automation/twin_observe/propose.py` |
| **SI-4** | Obsidian 민감 콘텐츠 외부 유출 방지. 특허 관련 내용은 GLM 전달 차단. | `automation/rag_ingest/sensitivity.py` 태깅 + `recall_cli.py` model-aware 게이트(v2: 주 모델 non-GLM 검증 시만 센티널 부착 포함) + LiteLLM 센티널/태그 403 (`custom_callbacks.py`) |
| **SI-5** *(2026-07-28 개정)* | **RAG 미러는 단방향(Pull) 전용** — 미러에 대한 쓰기 경로는 원천 차단을 유지한다. 쓰기가 필요한 경우 **미러와 분리된 별도 클론 + 별도 `-rw` deploy key**를 쓰며, commit/push는 **소유자 승인 게이트를 통과한 건에 한해** 허용한다. | (pull 전용) `automation/rag_ingest/sources/obsidian.py` push-disabled + 매 tick `reset --hard` · (쓰기) `automation/obsidian_write/` 분리 클론 + 외부효과 게이트 바인딩 |

> **SI-5 개정 배경 (2026-07-28, 소유자 확정)**: repair 티켓 `t_1b8aab9b`(개인노트를 PARA Markdown으로 저장하고 git commit·push·원격 검증)이 종전 SI-5의 "쓰기 원천 금지"와 충돌했다. 미러를 열어 주는 대신 **경로를 분리**했다 — RAG 미러는 그대로 read-only(10분마다 `reset --hard`라 어차피 쓰기가 소멸)로 두고, 쓰기는 별도 클론에서만 일어나며 승인 게이트를 반드시 거친다. 따라서 "에이전트가 승인 없이 볼트를 변경할 수 없다"는 원래 보호 목표는 유지된다.
| **SI-6** | 기존 게이트 재사용. 병렬 승인 워처 신설 금지. | `skills/wiki/scripts/wiki_gate.py` reaction retrofit |
