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

## 2026-09-03 전수 처리 (후속 과제 스윕 4)

열린 29건(10묶음)을 mass-ulw DAG 22노드로 병렬 처리했다 — 20건 해소, OWNER 2·BLOCKED 2(repair 동결)·OBSERVE 5 이관.
기관메일 Gmail 회신 인용은 코드 없이 실측으로 닫혔다(`gws gmail +reply` 가 원문을 인용한다). 이관 사유와 해소 근거는
[follow-ups-deferred.md](follow-ups-deferred.md) 의 각 `## 원 헤딩` 아래 `↳ 처리(2026-09-03)` 줄에 있다.
착지 중 새로 발견한 것은 아래 묶음에 쌓는다.

## 후속 과제 스윕 4 착지 후 남긴 것 (2026-09-03)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 수리 티켓 t_bd0d3789 후속 (2026-09-03)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 2026-09-03 수리 스윕(메일 인용·다이제스트 GLM 폴백)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## LiteLLM GPT 전환과 헬스체크 정리 후 남긴 것 (2026-09-03)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래. 노드 래퍼 설치는 OWNER 항목으로 남았다.

## v1.1.0 편의 릴리스 착지 후 남긴 것 (2026-09-03)

- **`release.sh` 를 손으로 돌린 세션과 워크스테이션 완결 타이머(`autophagy-release-complete.timer`)가 같은 ✅ 를 보고 `deploy_all --apply` 를 동시에 돌렸다 — 스킬별 실행 lock 이 서로를 `EXECUTION-LOCK-BLOCK` 으로 막아 두 실행 모두 `incomplete` 로 끝났고, 합집합이 우연히 전부 마운트돼 영수증은 다음 `--verify` 에서야 나왔다(v1.1.2 실측) → 릴리스 단위 lock(`/srv/autophagy-private/deploy-all/`)을 deploy_all 이 잡거나, 완결 타이머가 활성이면 `release.sh` 가 deploy 를 완결기에 위임하고 폴링만 하도록 한다.** 영향 범위: 이중 실행 자체는 멱등(마운트 digest 대조)이라 손상 없음, 다만 한쪽이 `SKILL-STALE`·rc=10 으로 끝나 사람이 오독한다 — 심각도 낮음.

