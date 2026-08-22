# hermes_compat 무패치 구동 탐지

## 무엇을

벤더 Hermes 게이트웨이가 우리 호환 패치를 **실제로 들고 있는지**를 기계로 판정한다.
`python3 -m automation.hermes_compat.patch_state`가 매니페스트의 패치별로
`PATCHED / MISSING / UNKNOWN`을 내고, 하나라도 빠졌으면 non-zero로 끝난다.

## 왜

2026-08-16 `hermes update`가 벤더 소스를 갈아엎으면서 autostash를 복원하지 않아 패치 3종이
통째로 빠졌고, **프로덕션이 이틀을 그 상태로 돌았다**. 그중
`busy-path-pre-gateway-dispatch`는 busy 경로 메시지가 skill-generation 관측(W6-4)과
meeting-gate fail-closed veto(W2-3) 훅을 타게 만드는 패치다 — 게이트 인접 훅이 꺼져 있었다는
뜻이다.

그런데 그 사실을 말해주는 것이 아무것도 없었다. 유닛은 내내 `active/running`이었고 Discord도
연결돼 있어 `systemctl is-active`류 프로브는 원리적으로 통과시킨다. 더 나쁜 것은 매니페스트가
"healthcheck should surface a missing patch"라고 **적고 있었다**는 점이다 — 그 파일에는
hermes_compat라는 단어조차 없었다. 장애 대응 중에만 읽히는 잘못된 안심은 아무 안심보다 나쁘다.

## 사용 시나리오

**정상.** 패치가 다 있으면 exit 0과 `PATCHED` 세 줄. 조용하다.

**절반 복구.** 캐리어를 올리는 deploy 스크립트가 둘이고 목적지도 달라서, 한쪽만 돌리면 3종 중
1종만 올라간다(노드에서 실제로 그 상태였다). 이제 exit 1과 함께 빠진 패치 id와 그것을 싣는
applier 이름이 같이 나온다. "3종을 다 올리려면 두 스크립트를 모두 돌려야 한다"는 사실도
회귀 테스트로 고정했다 — 매니페스트에 있는데 어떤 deploy 스크립트도 싣지 않는 applier가
생기면 RED다.

**판정 불가.** 대상 파일이 없거나 매니페스트를 읽을 수 없으면 exit 2 `UNKNOWN`이다. "빠졌다"로
접지 않는다 — 읽을 수 없었다는 것과 빠졌다는 것은 다른 사건이고, 접으면 원인 규명이 엉뚱한
곳에서 시작된다. 부재는 PASS가 아니다.

## 하지 않는 것

**재적용도 재시동도 하지 않는다.** 게이트웨이 재시동은 「게이트웨이 재시동 규칙」상 agent·peer
전 세트를 함께 다뤄야 하는 외부효과이고, 노드 패치 적용도 마찬가지다. 이 기능은 탐지까지이며
실제 복구는 소유자 원장 항목으로 남는다. 프로브는 `hermes`도 ssh도 systemctl도 호출하지 않는다.

## 관련

- 프로브: `automation/hermes_compat/patch_state.py` · 검사 `tests/unit/test_hermes_compat_patch_state.py`
- 복구 절차: [docs/troubleshooting/hermes-compat-patch-state.md](../troubleshooting/hermes-compat-patch-state.md)
- 매니페스트: `automation/hermes_compat/manifest.json` (notes를 실제로 돌아가는 명령으로 교체)
- 미완: 이 프로브의 `automation/healthcheck.sh` 배선은 아직이며, 매니페스트 notes가 그 사실을
  명시한다(문서가 없는 검사를 있다고 말하지 않게 회귀로 고정).
