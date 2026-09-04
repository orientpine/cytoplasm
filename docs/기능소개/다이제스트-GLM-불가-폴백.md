# 기능 소개 — 다이제스트 GLM 불가 폴백

**완료:** 2026-09-03 · **티켓:** repair `t_f0159b45` · **스킬:** `mail`

## 무엇을

다이제스트의 비민감 분류·요약에서 GLM 가용성 실패(HTTP 429·5xx·연결 실패)를 `LlmUnavailableError`로 구분한다. 기존 1회 재시도 뒤에도 실패하면 같은 프롬프트를 codex 티어로 처리하고, 런 단위 래치가 남은 항목의 GLM 재시도를 멈춘다. 각 강등 호출은 `llm-calls.jsonl`의 `fallback_from=glm-main` 표식으로 남고, 소유자 DM에는 `⚠️ glm-main 사용 불가 …` 줄이 정확히 한 번만 붙는다.

## 왜

2026-09-03 오전 provider가 반복 HTTP 429를 반환했을 때 다이제스트 항목 다수가 `(요약 실패)`와 `⚠️ 분류 실패`로 끝났고, 소유자는 원인을 바로 알 수 없었다. 같은 장애를 항목마다 재시도하면 지연과 호출만 늘어나므로, 가용성 장애만 구분해 안전한 비-GLM 경로로 한 번 강등해야 했다.

## 사용 시나리오

### happy path

1. 비민감 메일의 분류·요약은 평소처럼 glm-main에서 처리된다. 래치는 비활성이고 소유자 DM에 경고가 없다.
2. 한 호출이 `LlmUnavailableError`가 되면 기존 재시도 뒤 codex 티어가 같은 프롬프트를 처리한다.
3. 그 뒤 항목들은 GLM을 다시 시도하지 않고 codex 티어로 처리되며, 다이제스트 DM에는 마스킹된 경고 줄 하나와 정상 카드가 함께 도착한다.

### 실패/거부 경로

- 민감 메일은 처음부터 GLM에 보내지 않는 민감도 게이트를 그대로 따른다. 이 폴백은 민감 라우팅을 바꾸거나 GLM 호출을 허용하지 않는다.
- codex 티어도 실패하면 기존 항목 단위 fail-open 규칙이 해당 카드의 실패 상태를 보인다. 폴백이 전체 다이제스트나 승인 게이트를 우회하지 않는다.

## 관련

- 스킬: `skills/mail/SKILL.md`
- 티켓: repair `t_f0159b45`
- 파일: `skills/mail/scripts/triage_llm.py`, `skills/mail/scripts/triage_llm_routing.py`, `skills/mail/scripts/triage_digest.py`
- 게이트: 민감도 게이트가 민감 메일의 GLM 경로를 차단하고, 다이제스트는 읽기·통지 경계라 발송 승인 게이트를 열지 않는다
- 회귀: `tests/unit/test_mail_digest_glm_fallback.py`
