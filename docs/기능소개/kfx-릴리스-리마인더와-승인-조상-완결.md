# 기능 소개: 릴리스 리마인더, 승인된 조상 SHA 완결, 사전 부착 반응 버튼

**PR:** #505(리마인더), #508(조상 SHA 완결), #523(반응 버튼) · **영역:** 공급망 승인 게이트 · 워크스테이션 완결기

## 무엇을

1. **릴리스 리마인더.** 공급망 워처의 리마인더 틱이 `RELEASE` 종류 승인 카드도 다룬다. 이번 틱에 "아직 답이 없다"고 확인된 레코드에만, 정책표의 간격마다 한 번씩 포인터 메시지를 보낸다. 스킬 배포 리마인더의 바이트는 바뀌지 않는다.
2. **승인된 조상 SHA 완결.** 완결기(`release_complete.sh`)가 origin/main 팁이 아니라 **다른 HEAD**에 승인이 묶여 있는 경우를 처리한다. 결정 명령이 rc 3으로 후보(`<40자 sha> <버전>`)를 돌려주면, 워크스테이션의 Git 그래프로 그 sha가 현재 팁의 조상인지 판정한다. 조상이면 정확히 그 sha를 검증·서명 태그·전량 반영한다. 조상이 아니면 저널에 남는 소유자 통지 하나를 보내고 태그·배포 없이 끝나며, 다음 틱에는 통지를 반복하지 않는다.
3. **사전 부착 반응 버튼.** 공유 공급망 승인 게이트가 카드의 메시지 바인딩을 저장한 직후 ✅와 ⛔ 반응을 각각 독립적으로 붙인다. 소유자는 버튼을 누르기만 하면 된다. 봇이 붙인 반응은 결정으로 인식되지 않는다.

## 왜

릴리스 승인 카드는 리마인더 대상이 아니어서 오래 묻혔다. 승인 뒤 origin/main이 전진하면 완결기는 매 틱 HEAD 불일치로 멈췄고, 실제로 승인된 커밋은 영영 완결되지 않았다. 반응 버튼이 없는 카드는 소유자가 이모지를 직접 찾아 넣어야 했다(최신 main에서 뒤늦게 발견된 22번째 후속 항목).

## 사용 시나리오

### 정상 경로

1. 릴리스 승인 카드가 올라오면 ✅/⛔ 두 반응이 이미 붙어 있다. 정책표 간격이 지나도록 답이 없으면 원채널에 포인터 한 통이 가고, 같은 간격 안에는 다시 가지 않는다.
2. 소유자가 ✅를 누른 뒤 다른 PR이 main에 먼저 들어간다. 다음 틱의 완결기는 승인 sha가 팁의 조상임을 확인하고 `RELEASE-DECISION: different HEAD branch=ancestor …`를 남긴 뒤 **그 sha**의 로컬 CI 영수증 검증, 서명 태그, `deploy_all --apply --wait-converge`를 수행한다. 승인 레코드와 결정 바이트는 그대로다.
3. 팁과 승인 sha가 같으면 기존 경로 그대로이며 실행·결정 바이트가 보존된다.

### 실패·거부 경로

- 승인 sha가 팁의 조상이 아니면(예: 승인 뒤 force-push나 다른 계보) 통지 한 번, 태그·배포 없음, 다음 틱 무통지.
- 조상 판정 자체가 불가능하면 `ancestry-unavailable`로 기록하고 exit 1로 물러난다.
- 시도 상한은 sha별 3회이며 태그 전·후가 같은 예산을 쓴다. `RECONCILE-DEFER`(노드가 아직 그 릴리스가 아님)는 시도로 세지 않는다.
- 반응 부착이 전송 실패하면 `APPROVAL-REACTION-FAIL` 마커만 남기고 바인딩된 카드는 삭제·교체하지 않는다. 첫 버튼이 실패해도 두 번째를 시도한다. 같은 요청을 다시 받으면 기존 레코드를 재사용하고 새 게시·반응 쓰기를 하지 않는다.
- 완결기는 요청·회수·계획을 스스로 만들지 않는다. 결정 전용 명령만 호출한다.

## 승인 경계

소유자 ✅만이 릴리스를 인가한다. 완결기는 이미 인가된 릴리스를 마무리할 뿐이며, 승인 해시·nonce·카드 렌더러·결정 정책은 바뀌지 않았다. 실제 노드 리마인더와 라이브 카드 read-back은 배포 이후 항목이다. 이번 문서 시점의 운영 릴리스는 이전 그대로다.

## 관련

- 코드: `automation/supply_chain_remind.py`, `automation/release_complete.sh`, `automation/release_approval.py`, `automation/release_completion_target.py`, `automation/skill_gate.py`
- 회귀: `tests/unit/test_supply_chain_remind_release.py`, `tests/unit/test_release_complete_bound.py`, `tests/unit/test_release_recovery_shell.py`
- 검증 증거: `.omo/evidence/kfx/fa-release-reminder/`, `.omo/evidence/kfx/fb-completer-bound-head/`, `.omo/evidence/kfx/d7-release-reactions/` (루프백 HTTP로 POST→PUT→PUT 확인)
- 참고: [릴리스 승인 자동 완결](릴리스-승인-자동-완결.md) · [릴리스 완결 리컨실](릴리스-완결-리컨실.md) · [실행된 릴리스는 낡은 승인이 아니다](실행된-릴리스는-낡은-승인이-아니다.md)
