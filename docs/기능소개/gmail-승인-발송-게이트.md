# 기능 소개 — Gmail 승인 발송 게이트

**완료:** 2026-07-29 · **티켓:** repair `t_72dba111` · **스킬:** `mail`

## 무엇을

Gmail 발송은 소유자 DM의 유효한 ✅ 승인과 정확히 일치하는 경우에만 실행한다. 승인 시점의 argv·action hash·본문·수신자·회신 대상·첨부 매니페스트를 보존하고, `gws` 직전에 다시 검증한다.

## 왜

승인 후 본문·수신자·첨부가 바뀌거나 파일이 사라져도 이전 승인이 사용되면 다른 메일이 발송될 수 있다. 이 게이트는 차이를 발견하면 발송하지 않고 새 승인을 요구한다.

## 사용 시나리오

1. **정상:** Gmail 초안 → 소유자 DM에 계정·작업·수신자·제목·본문·첨부 이름/크기/SHA-256 표시 → cha ✅ → 직전 재검증 → 정확히 승인된 `gws` argv 1회 실행.
2. **거부/변경:** cha가 ⛔를 누르거나, 승인 뒤 첨부를 교체·수정·삭제하거나 수신자/본문이 달라짐 → 발송 0건, 재승인 필요.
3. **재시도:** 이미 성공으로 기록된 동일 action hash는 다시 실행하지 않아 이중 발송을 막는다.

## 관련

- `skills/mail/scripts/gmail_approval_gate.py`
- 기존 owner-DM producer/lifecycle: `skills/mail/scripts/triage_approval.py`
- 기존 attachment verifier: `skills/mail/scripts/triage_core.py:verify_attachment_manifest()`
- 기존 watcher/resolve 경로: `skills/mail/scripts/triage_cli.py`, `triage_confirm.py`
- 증적: `docs/qa/RTS-2/c3-gmail-gate.txt`
