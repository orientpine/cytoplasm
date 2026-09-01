# repair 승인 스레드 이관 — 정책 v8 (ON-4)

## 무엇을

수리(repair) 승인 요청의 게시 표면을 소유자 DM에서 `#agent-chat`의 `승인-repair`
스레드로 옮긴다. `approval_surface.POLICY_VERSION`을 8로 승격하고 REPAIR 전이에
`(8, AGENT_CHAT_THREAD)` 한 행을 더한 것이 전부다 — 해석·검증·스레드 생성은 기존
공유 코드(`approval_directory`)가 그대로 소유한다.

## 왜

v7이 소유자 전용 승인을 전부 스레드로 옮겼을 때 repair만 DM에 남았다 — 발신 주체인
Ops 봇이 개인 서버에 초대돼 있지 않았기 때문이다. 소유자 확정(2026-08-28 §10-7):
초대하고 v8로 이관해 **DM 잔존 승인 표면을 0**으로 만든다.

## 롤아웃 전제 (소유자 작업 — 순서가 중요하다)

1. **Ops 봇을 개인 서버에 초대**한다(1분 작업). 초대 전에 v8이 실린 릴리스가 나가면
   새 repair 승인은 게시 불가로 **fail-closed** 된다(어디에도 게시하지 않음) —
   그래서 v8 릴리스 컷은 초대 확인 뒤에만 한다.
2. repair 승인 유닛은 `ProtectHome`이라 `~/.hermes`를 못 본다 — `agent_chat_channel_id`
   해석은 `INTEROP_CONFIG` env(`/etc/autophagy/repair-approval.env`에 추가)로 유닛이
   읽을 수 있는 설정 경로를 가리킨다.
3. v7 이하로 이미 게시된 DM pending 레코드는 저장 바인딩(S2)대로 **DM에서 종결**된다
   — 위조·재게시 없음.

## 동작

- 새 repair 승인: Ops 봇이 자기 자격으로 `승인-repair` 스레드를 찾거나 만들어
  게시하고, 스레드 사실(type 11/12 + 부모=agent-chat)을 검증한 뒤에만 신뢰한다.
- 초대·설정이 안 된 상태: `ApprovalSurfaceError` → `REPAIR-...surface cannot be
  resolved`로 실패하고 아무 데도 게시하지 않는다.
- 이력 불변: v0-4=`#approvals`, v5-7=owner DM — 옛 레코드의 의미는 재해석되지 않는다.

## 관련

- `automation/interop/approval_surface.py`(`POLICY_VERSION=8`, REPAIR 전이)
- 회귀: `tests/unit/test_approval_surface.py`(v8 flip)·`tests/unit/test_repair_approval_binding.py`
- 계획: `.omo/plans/release-convergence-and-versioned-approval.md` §6 ON-4 · [오너 통지 단일 채널](오너-통지-단일-채널.md)
