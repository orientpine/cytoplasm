# 다이제스트 분류 실패 fail-open — 한 메일이 전체 다이제스트를 무너뜨리지 않게

**완료:** 2026-07-31 · **티켓:** repair `t_digest_glm_failopen` · **스킬:** `mail`

## 무엇을

매일 08:00 KST 기관메일 다이제스트(`mail-daily-digest` cron)가 **메일 한 건의 분류(classify) LLM 실패로 전체가 중단되지 않도록** 고쳤다. 이제 분류가 실패하면 그 항목만 보수적으로 `🔴 중요` + `⚠️ 분류 실패` 배지로 표면화하고, 나머지 메일은 정상 요약·전달·기록된다. 요약(summarize)이 이미 `(요약 실패)` fallback으로 항목을 유지하던 것과 동일한 취지를 분류에도 적용했다.

## 왜

`glm-main`은 추론 모델 `zai/glm-5.2`에 별칭돼 있어 요약 1건당 추론 토큰을 650~714개 태우고(실측) 호출당 4~10초가 걸린다. 이 때문에 두 가지 간헐 실패가 관측됐다:

- **2026-07-31 08:10**: `LLM-FAIL glm-main 호출 실패: timed out` — 느린 아침에 180초 타임아웃.
- **2026-07-31 23:31**: `DIGEST-FAIL stage=build code=llm_call_failed detail=no JSON object in LLM response` — 응답에 `{`가 없어 JSON 계약 위반.

문제의 핵심은 **요약은 fail-open인데 분류는 아니었다**는 점이다. `build_item`에서 분류 호출이 실패하면 그 예외가 빌드 단계 `GateError`(exit 4)로 승격돼 **그 tick의 모든 메일**을 미전달로 남겼다 — 한 메일의 확률적 LLM 실패가 소유자의 하루치 다이제스트 전체를 삼켰다.

## 어떻게 (구조)

- **`build_item()`**: 분류 호출을 `LlmCallError`/`LlmParseError`에 한해서만 감싸는 fail-open 가드. 실패 시 보수적 fallback 판정(`category="important"`, 모든 플래그 False, `reason="classification_unavailable"`)으로 대체하고 `classification_failed` 플래그를 붙인다. `important AND schedule_needed AND schedule_text`일 때만 캘린더 초안을 위임하므로, 모든 플래그가 False인 fallback은 **조작된 판정으로 캘린더 초안을 만들지 않는다**.
- **배지 노출**: `⚠️ 분류 실패` 배지를 카드에 표시해 실패를 성공한 "중요" 판정처럼 위장하지 않는다.
- **`parse_classification()`**: bool 필드를 `_json_bool`로 엄격 파싱. glm-5.2가 bool을 문자열로 주면 `bool("false")`가 `True`가 돼 파싱은 되나 잘못된 응답이 캘린더 초안을 위임할 수 있었다 — 실제 JSON `true`/문자열 `"true"`(대소문자 무시)만 True.
- **가드 범위**: `except Exception`으로 전부 삼키지 않는다 — 알려진 LLM 호출·계약 오류만 fallback 대상이라 `PatentRoutingError`(민감도 라우팅 불변식)나 프로그래밍 오류는 그대로 표면화된다.

## 사용 시나리오

- **소유자 관점(정상)**: 변화 없음. 08:00에 다이제스트 DM을 받는다.
- **소유자 관점(부분 실패)**: 신규 5건 중 1건의 분류가 실패해도, 이제 다이제스트는 5건 모두를 담아 도착한다. 실패한 1건은 `### N. 제목` 아래 `🔴 중요 · ⚠️ 분류 실패`로 표시돼 소유자가 직접 확인하도록 유도한다 — 이전처럼 하루치 전체가 사라지지 않는다.
- **실패/거부 경로**: 분류 실패는 fallback으로 흡수되므로 워처 재시도가 불필요하다. 빌드 단계에서 실제로 `DIGEST-FAIL`이 나는 경로(요약·전달 실패)는 종전대로 구조화 마커로 소유자에게 알린다([소개](다이제스트-실패-소유자-알림.md)).

## 관련

- 코드: `skills/mail/scripts/triage_digest.py`(`build_item`, `_CLASSIFY_FALLBACK`, `_CLASSIFY_FAILED_FLAG`), `skills/mail/scripts/triage_core.py`(`parse_classification`, `_json_bool`)
- 테스트: `tests/unit/test_mail_digest.py`(분류 fail-open·배지·여러 건 중 1건 실패 완주), `tests/unit/test_mail_triage.py`(bool 엄격 파싱)
- 승인·게이트: 분류 fallback은 소유자 데이터 mutation이 아니라 다이제스트 조립 경계의 fail-open이므로 별도 승인 게이트를 신설하지 않는다. fallback이 캘린더를 위임하지 않는 것이 안전 불변식이며 테스트로 고정된다.
