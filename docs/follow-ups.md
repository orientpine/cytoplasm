# 후속 과제

> **이 저장소가 지금 손댈 수 있는 열린 작업만** 남긴다. 소유자·노드에서만 닫히는 것, 동결·벤더에 막힌 것,
> 조건이 충족되기 전에는 조치하지 않는 것, 이미 닫힌 것은 전부 [follow-ups-deferred.md](follow-ups-deferred.md) 로 옮겼다.
> 현황판은 [features.md](features.md), 완료 기능은 [done.md](done.md).

> 기능 단위 묶음 항목으로, 불릿마다 "문제 → 조치" + 영향 범위·심각도를 적는다. 상세 규칙: 루트 `AGENTS.md`「후속 과제 기록 규칙」.
> **2026-08-26 분리**: 열린 105건을 전수 재판정해 84건을 보류 문서로 옮기고, 저장소에서 고칠 수 있는 16건은 실제로 고쳤다.
> 문서가 줄지 않던 기계적 원인은 회계 가드였다 — `tests/unit/test_features_board_conformance.py` 의 A9 가 FS3 baseline
> (`4716602d`)의 불릿 삭제를 막는다. 그래서 **삭제가 아니라 이동**으로 처리하고, 가드가 두 문서를 합쳐 읽도록 고쳤다.
> 원 묶음 `##` 헤딩을 양쪽에서 그대로 유지하는 것이 그 가드의 대조 키다.

## 현재 열린 항목 없음 (2026-08-31 전수 처리)

열린 19건을 병렬 수리로 17건 해소, 2건 재판정(BLOCKED — 기술이전 render 진리표는 KD 확인 선행, G8 분할 잔여는 FS3 재생 핀·동결 소유)했다.
2026-08-31 오후에는 제안서 노드 자율 구동 3건과 릴리스 승인 자동 완결의 낡은 pending 회복 1건도 해소했다.
전 이력은 [follow-ups-deferred.md](follow-ups-deferred.md) 의 해소 기록·BLOCKED 절에 있다. 새 후속 과제는 루트 `AGENTS.md`「후속 과제 기록 규칙」대로 여기에 다시 쌓는다.

## 기관메일 회신 원문 인용 후속 (2026-09-01)

- **Gmail 계정 회신(`gws gmail +reply --message-id`)은 인용을 붙이지 않는다 → gws 가 원문을 자동 인용하는지 노드에서 실측한 뒤, 안 하면 `mail_gmail_send.ReplyMailRequest` 본문에 같은 `mail_quote.render_quote` 를 붙인다.** Gmail 은 message-id 스레딩으로 대화 보기에 원문이 이미 묶이므로 동작 정상 — 표시 일관성 문제일 뿐이고 발송 안전성과 무관(심각도: 낮음).
- **mailon 웹메일의 답장 버튼(In-Reply-To/References 헤더) 경유는 vendor 변경이 필요해 미도입 → 상대 클라이언트의 스레드 묶음이 어긋나는 사례가 보고되면 `send_trigger` 계열에 답장 모드를 실측 기반으로 추가한다.** 현재는 본문 인용으로 사람 눈에는 회신으로 보이며 발송 안전성과 무관(심각도: 낮음).

## cron 워처 수리 후속 (2026-09-01)

- **`typing.override` 직수입이 3.11 no-agent 런타임 밖 경로에 남아 있다 → 해당 경로를 cron 체인에 편입하거나 리팩터할 때 `automation/typing_compat.py`(또는 mail_runtime 의 인라인 폴백 패턴) 경유로 먼저 바꾼다.** `skills/doctype/scripts/doctype_save.py`(게이트웨이 대화 경로 전용 — doctype 은 cron 워처가 없다)·`automation/group_roster/editor.py`·`automation/managed_skills/submission_errors.py`(둘 다 워크스테이션 CLI 전용)가 `from typing import override` 를 직수입한다. Hermes cron 의 uv CPython 3.11 이 실행하는 체인에는 현재 도달하지 않아 동작 정상 — mail-triage-watch 를 매 틱 죽인 결함(2026-08-31)과 같은 계열이지만 지금은 잠복이다. 영향 범위: 현재 없음, **심각도 낮음**. cron 편입 순간 같은 ImportError 가 재발한다.
