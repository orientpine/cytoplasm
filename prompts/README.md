# prompts/ — 프롬프트 라이브러리 (W5-1)

cha의 연구/작업 프롬프트 자산. `skills/prompt/`(`!prompt search/get/add`)가
이 디렉터리를 읽기 전용 canonical 계층으로 소비한다.

## 구조

```
prompts/
  README.md                      # 이 문서 (스키마 계약)
  library/<id>/v<N>.md           # 라이브러리 형식 엔트리 (버전별 파일, 불변)
  meeting-extraction-v{1,2,3}.md # 레거시 형식 (W2-3 소비 — 절대 수정 금지)
```

## 라이브러리 엔트리 형식 (library format)

파일 = `library/<id>/v<N>.md`. frontmatter 키 **정확히 10개** (그 외 금지):

```
---
id: report-outline
version: 1
category: task                # task(작업별) | research-background(연구 배경지식)
purpose: "용도 한 줄"
model: openai-codex           # 권장 모델: openai-codex | any
tags: [report, weekly]
created: 2026-07-16T00:00:00Z
updated: 2026-07-16T00:00:00Z
sensitivity: none             # none | patent-sensitive
body_ref: inline              # inline | private:<불투명 32-hex ID>
---
프롬프트 본문 (body_ref: inline 일 때만)
```

## 민감도 분리 계약 (constraint 7/8)

- add/update 시 `configs/sensitivity-rules.yaml`의 결정적 키워드/정규식
  게이트가 **본문+메타 전체**를 검사한다 (LLM 무참여).
- **적중 시**: 본문은 `~agent/prompts-private/`(700, git 밖)에만 저장되고,
  repo/오버레이 엔트리는 `sensitivity: patent-sensitive` +
  `body_ref: private:<불투명 ID>` 메타 스텁만 갖는다 (본문 0바이트).
- 민감 프롬프트 **사용 시** 호출은 `patent-sensitive` 태그가 강제되어
  Codex OAuth 경로(`openai-codex`)로만 라우팅된다. 경로가 없거나 Codex OAuth로
  검증되지 않으면 provider call 전에 fail-closed로 거부한다
  (`configs/routing-policy.md`).

## 버전 규칙

- 같은 `id`로 add → **덮어쓰기 금지**, `v<max+1>` 새 파일 생성.
- `get`은 기본 최신 버전, `--version N`으로 과거 버전 조회.

## 런타임 계층 (agent 인스턴스)

- repo 계층(읽기 전용): `/srv/autophagy-agents/prompts/`
- 오버레이 계층(agent가 add한 엔트리): `~/.hermes/prompt-library/entries/`
  — 비민감 엔트리는 검토 후 사람이 repo `library/`로 승격(커밋)할 수 있다.
- 민감 본문: `~agent/prompts-private/` (700; repo에는 절대 없음)

## 레거시 형식 (meeting-extraction-v*.md)

`<<<PROMPT>>>` 라인 앵커 아래가 본문인 W2-3 형식. `skills/meeting/`이 파일
경로로 직접 소비하므로 **내용/형식 변경 금지**. prompt 스킬은 이 파일들을
읽기 전용 어댑터로 색인한다 (id=`meeting-extraction`, 파일명의 v<N>이 버전).
