# 롤백 방지 floor 를 ops 손에서 뺀다

## 무엇을
릴리스 다운그레이드를 막는 **anti-rollback floor** 를 ops 소유 경로에서 **root 소유 경로**(`/var/lib/autophagy/update-trust/release-floor.json`)로 옮기고, 역할을 둘로 갈랐다.
- **사전 게이트(ops)** — 읽고 비교만 한다. 절대 쓰지 않는다.
- **특권 경로(root)** — 서명 재검증 뒤에만 **단조 증가**시킨다. 낮추는 일은 없다.

## 왜
floor 가 ops 소유일 때는, 악의의 ops 가 그 파일을 지우고 update channel 이나 미러 origin 을 **과거의 진짜 서명 릴리스**로 되돌려 취약한 구버전으로 유도할 수 있었다. 서명 검증만으로는 막히지 않는다 — 옛 릴리스도 서명은 진짜이기 때문이다. 막는 것은 "여기보다 뒤로는 못 간다"는 floor 뿐인데, 그 앵커를 공격자가 지울 수 있으면 방어가 성립하지 않는다.

## 사용 시나리오
**정상 수렴(happy path)** — 리컨실러 tick 에서 ops 사전 게이트가 후보를 검증하고, root 헬퍼가 같은 릴리스를 다시 검증한 뒤 floor 를 전진시킨다. 두 검증의 대상이 어긋나면 `SYNC-BLOCK` 으로 멈춘다.

**다운그레이드 시도(실패 경로)** — 과거 태그로 되돌리려 하면 `RELEASE-ROLLBACK` 으로 거부된다. floor 는 앞으로만 간다. 나쁜 릴리스의 취소는 문제를 revert 해 **더 높은 버전**을 컷하는 것이다(「공개 릴리스 규칙」 D8).

**첫 설치** — floor 가 아직 없는 설치는 root 경로가 첫 릴리스로 앵커를 세운다. 그 부모 디렉터리가 root 소유 0755 라 ops 는 만들 수도 지울 수도 없다 — 그래서 "없음"은 "지워졌다"가 아니라 "아직 심지 않았다"로 읽어도 안전하다.

**기존 노드 이관** — 프로비저너가 기존 ops 소유 floor 를 **삭제 없이 정확히 복사**한다. 이미 authoritative floor 가 있으면 덮지 않는다(재프로비저닝이 값을 낮추지 못하게).

## 관련
- `automation/update_trust_state.py`(`advance_release_floor` 읽기 전용 / `privileged_advance_release_floor` 특권 전진) · `automation/converge_origin_main.sh` · `automation/provision-deploy-converge.sh`
- 회귀 `tests/unit/test_release_floor_privilege.py` · `test_update_trust.py` · `test_release_rollback.py`
- 규약 `AGENTS.md` 「공개 릴리스 규칙」·「릴리스 태그 규칙」
