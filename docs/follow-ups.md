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

## provenance 가드의 남은 이스케이프 경로 (2026-09-05)

- **`deploy_provenance.sh` 의 저장소 전체 검사(144·149행)는 아직 `git status --porcelain` / `git ls-files` 의 기본 따옴표 이스케이프를 그대로 출력한다 → 비ASCII 파일이 dirty·untracked 로 걸리면 차단 메시지가 `"\354\232\251…"` 로 나와 어느 파일인지 사람이 알 수 없다.** 디렉터리 인자 경로(193·202·213)는 `-c core.quotepath=false` 로 고쳤고 그것이 실제 배포를 막던 지점이었다. 남은 두 곳은 **판정에 영향이 없다** — 존재 여부(빈 문자열인가)만 보고 경로 문자열을 파일로 열지 않기 때문이다. 즉 오작동이 아니라 가독성 결손이며, `status --porcelain -z` 로 옮기려면 읽기 루프 구조를 함께 바꿔야 해서 이번 수정 범위 밖에 두었다(심각도: 낮음).

## 릴리스 승인 카드가 peer 시야에 있다 (2026-09-05)

> ↳ 2026-09-05 A+C 로 해소(소유자 결정: 인터롭은 필요하므로 peer 게이트웨이는 유지, B 는 채택하지 않음) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## peer 자가 스킬이 승인 심사 절차를 저작했다 (2026-09-05)

- **`selfskill_audit` 는 자가 스킬의 이름 그림자(`SHADOWS-GOVERNED`)와 기능 겹침(`OVERLAPS-GOVERNED`)만 보고, SKILL.md 가 **승인 절차를 주장**하는지는 보지 않는다 → 본문이 승인 kind 접두어(`[release]`·`[skill-deploy]`·`[skill-publish]`)·`#approvals`·게이트 판정 어휘(`DO-NOT-APPROVE`·`승인 보류`)를 참조하면 `CLAIMS-APPROVAL-ROLE:<skill>` advisory 로 아침 보고에 싣는다.** peer 의 `autophagy-interop` 이 2026-09-01 에 `[release]` 심사 절차(라우팅 행·Quick Reference 50줄·참고 문서·교훈 12건)를 스스로 저작했고 v1.1.2~v1.2.2 의 모든 릴리스 카드에 거짓 ⛔ 를 붙였는데, 감사 원장에는 `edited` 델타 한 줄뿐이라 **무엇을** 저작했는지 아무도 보지 못했다(`docs/patch/2026-09-05-peer-gateway-ignores-approvals.md`). 토큰 매칭 규칙이라 오탐 상한은 governed 18개 상호 대조 0건으로 회귀 고정한다. 심각도 중 — 게이트·노드 동작은 무영향이지만 소유자 판단을 매 릴리스 오염시켰다.
- **peer 게이트웨이의 `discord.ignored_channels`(`#approvals`) 는 노드 config 에만 있고 저장소·프로브는 그 존재를 모른다 → healthcheck 의 read-only 프로브가 peer `config.yaml` 의 `discord.ignored_channels` 에 `#approvals` 채널 id(`channel_directory.json` 대조)가 들어 있는지 읽어 없으면 FAIL 로 낸다.** 다음 config 재생성·온보딩 재실행이 조용히 되돌릴 수 있고(2026-08-15 pin 부수 효과가 E7 을 되돌린 것과 같은 모양), 되돌아온 증상은 "카드에 peer 논평이 다시 붙는다" 뿐이라 발견이 다음 릴리스까지 늦다. 심각도 낮음(되돌아가도 게이트 판정은 불변).

## 완결 타이머와 세션 release.sh 의 태그 경합 (2026-09-05)

- **두 실행이 같은 ✅ 를 보고 동시에 태그를 자르면 한 커밋에 서명 태그가 둘 생긴다 → 완결기가 승인 레코드에 적힌 요청 버전을 재사용하게 한다.** 실측: 세션이 `--bump minor` 로 `v1.2.0` 을 13:11:22 에, 완결 타이머(`~/.hermes/release-completer/`)가 **기본 patch bump** 로 `v1.1.5` 를 13:11:23 에 잘랐다 — 둘 다 같은 커밋을 peel 하고 둘 다 update-trust 서명이 유효하다. `release_version_for` 의 "HEAD 에 이미 붙은 태그를 재사용" 은 태그가 **먼저 존재해야** 작동하므로 1초 차 경합을 막지 못한다.
- **갈라지는 조건은 좁다** — 세션이 `--bump patch`(완결기 기본값과 같음)로 돌면 양쪽이 같은 이름을 계산하고 `ensure_signed_tag` 가 동일 이름 기존 태그를 찾아 멱등 성공한다(v1.2.1·v1.2.2 실측: 중복 없음). 즉 문제는 **세션이 minor·major 를 쓸 때만** 나타난다.
- 프로덕션 영향은 없다(같은 커밋·같은 키라 어느 쪽으로 수렴해도 같은 코드). 다만 `public_export.sh` 는 `--version` 생략 시 source 커밋의 vX.Y.Z 태그가 **정확히 하나**여야 해서 `source commit has multiple semantic release tags` 로 막히므로, 그 릴리스만 `--version` 을 명시해야 한다. 서명 태그는 지우지 않는다(롤백 방지 floor 는 앞으로만 간다) — 심각도 낮음.

## 수리 티켓 t_bd0d3789 후속 (2026-09-03)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 2026-09-03 수리 스윕(메일 인용·다이제스트 GLM 폴백)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## LiteLLM GPT 전환과 헬스체크 정리 후 남긴 것 (2026-09-03)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래. 노드 래퍼 설치는 OWNER 항목으로 남았다.

## v1.1.0 편의 릴리스 착지 후 남긴 것 (2026-09-03)

- **`release.sh` 를 손으로 돌린 세션과 워크스테이션 완결 타이머(`autophagy-release-complete.timer`)가 같은 ✅ 를 보고 `deploy_all --apply` 를 동시에 돌렸다 — 스킬별 실행 lock 이 서로를 `EXECUTION-LOCK-BLOCK` 으로 막아 두 실행 모두 `incomplete` 로 끝났고, 합집합이 우연히 전부 마운트돼 영수증은 다음 `--verify` 에서야 나왔다(v1.1.2 실측) → 릴리스 단위 lock(`/srv/autophagy-private/deploy-all/`)을 deploy_all 이 잡거나, 완결 타이머가 활성이면 `release.sh` 가 deploy 를 완결기에 위임하고 폴링만 하도록 한다.** 영향 범위: 이중 실행 자체는 멱등(마운트 digest 대조)이라 손상 없음, 다만 한쪽이 `SKILL-STALE`·rc=10 으로 끝나 사람이 오독한다 — 심각도 낮음.

## 동결 해제·repair 재발 수리 착지 후 남긴 것 (2026-09-04)

> ↳ 2026-09-04 같은 세션에서 처리 — 수리 2건은 닫았고(상태 조회 전용 timeout · 새 카드가 이전 카드를 지목), 동결 4행에 걸린 1건은 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래 BLOCKED 로 옮겼다.

## 2026-09-04 plaud 구간 전사 수리 (t_4e3d6630) 잔여

- **사고로 게시된 빈 노트는 아직 vault 에 그대로다** — `000_PARA/Area/Lifelog/2026/2026-09-04-180427--7df8fc0f016b.md`
  는 요약·전문이 없는 649 B 노트다(정상 노트는 62 KB). 코드 결함은 이 수리로 닫혔지만 **이미 쓰인 노트는 스스로 고쳐지지
  않는다** → 수정본이 릴리스·마운트된 뒤 그 레코드를 `transcribing` 으로 되돌리면 파이프라인이 다시 전사해 승인 카드를
  새로 올린다. 상태 되돌림은 프로덕션 상태 쓰기라 소유자/노드 작업이며 이 브랜치에서는 하지 않았다. 영향: 라이프로그 1건,
  심각도 낮음.
- **반복 붕괴 구간은 회의록 경로에서 본문째 빠진다** — 임계 0.08 을 넘긴 구간은 표식만 남는다(라이프로그는
  `SPEECHTOTEXT_ALLOW_INCOMPLETE=1` 이라 보존). 실측: 사고 원본 61분의 구간 1 이 0.18 이라 회의록 정책에서는 15분이
  빠졌다. 전량 소실이던 예전보다는 순개선이라 이번 범위에서는 그대로 두고, 반복 구간을 **버리는 대신 접어서** 남기는
  방안은 별도 판단으로 남긴다. 심각도 낮음(표식으로 보이며 조용히 사라지지 않는다).
- **`automation/plaud_sync/mcp_client.py` 가 정확히 250 pure LOC** — 이번 변경 밖이지만 한 줄만 더해도 F2 파일 크기
  등록부에 예외를 쌓는다. 조치: 그 파일을 다음에 만질 때 분할한다. **심각도 낮음**.

## 용어 교정 문서 단계 이동 착지 후 남긴 것 (2026-09-05)

- **문서 종류 넷(report·proposal·doctype·procurement)은 아직 문서 단계 교정을 붙이지 않았다 → `tests/unit/test_term_correction_conformance.py` 의 `_EXEMPT` 에 사유와 함께 등록돼 있다.** 지금은 넷 다 소유자가 준 원문을 옮기거나 인용만 하므로 고칠 "새로 쓴 문장"이 없다 — 주간동향이 수집한 기사를 요약해 **새 본문을 쓰게 되거나** 제안서 자동 생성 본문이 과제 용어를 스스로 쓰게 되면 그때 `term_glossary.glossary_for("<kind>", project)` + `term_correction.apply` 를 렌더 직전에 붙이고 `_EXEMPT` 에서 `_ADOPTED` 로 옮긴다. 영향 범위는 그 두 스킬의 산출물뿐이고 **심각도 낮음** — 채택 전에도 회의록·라이프로그 교정은 정상 동작하며, 등록부가 비어 있는 채로 새 문서 종류가 규칙을 지나치는 일은 conformance 가 RED 로 막는다.
