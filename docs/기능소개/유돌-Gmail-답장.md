# 유돌이 Gmail 메일에 Gmail 로 답장 카드를 올린다

## 무엇을

유돌 게이트 `mail-compose` 에 `--reply-gmail <Gmail 메시지 id>` 를 더했다. 유돌이 답장 본문만 주면 메일 스킬이
원메일을 읽어 받는 사람과 `Re:` 제목을 카드에 보여 주고, 형이 ✅ 하면 Gmail 계정에서 같은 대화로 답장이 나간다.

## 왜

형이 Gmail 로 온 알림 메일에 "지메일로 써"라고 했지만, 게이트 `mail-compose` 는 `--reply-gmail` 같은 Gmail 답장
인자를 모르는 플래그로 거절했고 `--to` 가 꼭 필요했다. 메일 스킬에는 Gmail `+reply` 발송과 그 승인 검사가 이미
있었지만, 그 길로 카드를 만드는 명령이 없었다(2026-11-02 유돌 수리 요청).

## 사용 시나리오

- **Gmail 답장**: 형 "지메일로 써" → 유돌 `node-read mail-compose --reply-gmail 1a19c5… --body-b64 <본문>` + 형 글 좌표
  → 형 스레드에 `📨 Gmail 답장 발송 승인 요청` 카드(받는 사람·제목·본문, 원메일이 답장 아래 붙는다는 줄) → 형 ✅
  → 다음 메일 watch 틱이 `gws gmail +reply` 를 한 번 실행하고 같은 스레드에 발송 완료를 알린다.
- **고쳐 쓰기**: 같은 스레드에서 유돌이 고친 답장 카드를 다시 올리면 옛 카드는 「새 판으로 대체됨」으로 닫혀 ✅ 해도 나가지 않는다.
- **거부**: 원메일을 읽지 못하거나 보낸 사람이 없으면 `REPLY-REFUSED`(초안 없음). `--to`·`--subject`·숨은참조·첨부·
  Drive 공유·전달을 함께 주면 게이트와 스킬이 둘 다 거절한다(받는 사람·제목은 원메일이 정한다).
- **⛔ 또는 무응답**: 아무것도 나가지 않는다.

## 관련

- 게이트 `automation/yoodori_gate/yoodori_read_gate.py`(`--reply-gmail` 은 16진수 id, `--body`·`--cc`·형 글 좌표만).
- 스킬 `skills/mail/scripts/triage_cli.py compose --reply-gmail` → `mail_forward.gmail_reply_target`(원메일 머리글
  읽기만) → 기존 `mail_gmail_send.ReplyMailRequest` → `gmail_approval_gate` 스냅숏 → `triage_gate.create_gmail_draft`
  → 같은 승인 카드·watch·Gmail 최종 게이트. 새 카드 종류·워처 없음.
- 받는 사람·제목은 gws 가 발송 때 원메일에서 같은 규칙(Reply-To 우선, `Re:` 한 번)으로 정하므로 카드에만 보이고,
  그 값은 초안 해시에 묶여 바뀌면 발송이 멈춘다.
- 승인: 바깥으로 나가는 메일이라 형 ✅ 카드 뒤에만 나간다. 시험 `tests/unit/test_mail_gmail_reply.py`·
  `tests/unit/test_yoodori_gate_gmail_reply.py`.
