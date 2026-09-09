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

## plaud 파이프라인 실동작 검증 착지 후 남긴 것 (2026-09-05)

> ↳ 2026-09-05 후속 과제 스윕 5에서 4건 해소(PR #405·#412·#414) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## provenance 가드의 남은 이스케이프 경로 (2026-09-05)

> ↳ 2026-09-05 후속 과제 스윕 5에서 해소(PR #404) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 릴리스 승인 카드가 peer 시야에 있다 (2026-09-05)

> ↳ 2026-09-05 A+C 로 해소(소유자 결정: 인터롭은 필요하므로 peer 게이트웨이는 유지, B 는 채택하지 않음) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## peer 자가 스킬이 승인 심사 절차를 저작했다 (2026-09-05)

> ↳ 2026-09-05 후속 과제 스윕 5에서 2건 해소(PR #408·#409) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래. 프로브 판독 권한은 새 OWNER 항목으로 남겼다.

## 완결 타이머와 세션 release.sh 의 태그 경합 (2026-09-05)

> ↳ 2026-09-05 후속 과제 스윕 5에서 4건 해소(PR #406·#407) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 수리 티켓 t_bd0d3789 후속 (2026-09-03)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 2026-09-03 수리 스윕(메일 인용·다이제스트 GLM 폴백)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## LiteLLM GPT 전환과 헬스체크 정리 후 남긴 것 (2026-09-03)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래. 노드 래퍼 설치는 OWNER 항목으로 남았다.

## v1.1.0 편의 릴리스 착지 후 남긴 것 (2026-09-03)

> ↳ 2026-09-05 후속 과제 스윕 5에서 해소(PR #407) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 동결 해제·repair 재발 수리 착지 후 남긴 것 (2026-09-04)

> ↳ 2026-09-04 같은 세션에서 처리 — 수리 2건은 닫았고(상태 조회 전용 timeout · 새 카드가 이전 카드를 지목), 동결 4행에 걸린 1건은 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래 BLOCKED 로 옮겼다.

## 2026-09-04 plaud 구간 전사 수리 (t_4e3d6630) 잔여

> ↳ 2026-09-05 후속 과제 스윕 5에서 2건 해소(PR #410·#411) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래 해소 기록에 있다. 기존 빈 노트 upsert 승인은 그 문서의 OWNER 기록 그대로다.

## 용어 교정 문서 단계 이동 착지 후 남긴 것 (2026-09-05)

> ↳ 2026-09-05 후속 과제 스윕 5에서 OBSERVE 로 이관 — 새 본문을 쓰는 조건이 아직 성립하지 않았다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 2026-09-05 전수 처리 (후속 과제 스윕 5)

시작 시 열린 15건(7묶음)을 전부 처리했다 — PR #404~#412·#414로 14건 해소, 조건 미성립 1건은 OBSERVE 이관.
불릿은 지우지 않고 원 헤딩·본문 그대로 [follow-ups-deferred.md](follow-ups-deferred.md) 에 옮겨 `↳ 처리(2026-09-05)` 줄에 PR 근거를 붙였다.
PR #413은 동시 `--apply` 회귀의 release FIFO를 O_RDWR로 유지해 CI 경합을 닫았다(기존 후속 불릿 없음).
새 OWNER 1건·OBSERVE 1건은 보류 문서에, 저장소에서 손댈 수 있는 새 3건만 아래에 남긴다.

## 후속 과제 스윕 5 착지 후 남긴 것 (2026-09-05)

> ↳ 2026-09-06 후속 과제 스윕 6에서 3건 해소(PR #417·#418·#419·#420·#421) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래 해소 기록에 있다.

## 2026-09-05 수리 스윕-4 후 남긴 것

> ↳ 2026-09-06 후속 처리 세션에서 해소 — `release_complete.sh` 가 워크트리 동기화 직후 origin/main 의 자기 사본으로 exec 한다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 2026-09-06 전수 처리 (후속 과제 스윕 6)

시작 시 열린 3건(1묶음)을 전부 해소했다 — 공개 반출 원장 경로순 정렬(PR #417), 200–250 LOC 구간 파일 분할(PR #418·#419·#421), 형제 import 편집기 진단(PR #420).
불릿은 지우지 않고 원 헤딩·본문 그대로 [follow-ups-deferred.md](follow-ups-deferred.md) 에 옮겨 `↳ 처리(2026-09-06)` 줄에 PR 근거를 붙였다.
스윕 5가 남긴 OWNER(peer 읽기 권한)는 PR #425로 저장소 쪽 설치 자산이 갖춰졌지만 기존 노드 설치는 소유자의 sudo 한 번이 아직 남아 OWNER 그대로이고, OBSERVE(화자 임계값)는 PR #422가 두 화자 확인 녹음으로 1.35를 검증해 닫았다.
기존 불릿 없이 발견한 결함 1건은 PR #424로 고쳤다 — 로컬 전사 노트 본문이 오프셋 없는 `get_file` UTC 를 현지 시각처럼 찍던 것. 이미 vault 에 쓰인 노트 3건의 보정은 OWNER 로 남겼다.
배포 관측 2건(deploy.sh 종료 코드와 최종 프로브의 불일치, 미선언 홈 산출물)은 보류 문서의 스윕 6 헤딩 아래 OWNER·OBSERVE 로 기록했다. 저장소에서 지금 손댈 열린 건은 없다. 증적: [docs/qa/FU6/summary.md](qa/FU6/summary.md).

## 2026-09-08 전수 처리 (후속 과제 스윕 7)

시작 시 열린 13건(7묶음)을 mass-ulw DAG 11노드(병렬 lane 10 + 검증 1)로 처리했다 — 9건 해소, 1건 부분 해소(계측 완료·실측 대기),
OWNER 2·OBSERVE 2 이관. 불릿은 지우지 않고 원 `##` 헤딩·본문 그대로 [follow-ups-deferred.md](follow-ups-deferred.md) 에 옮겨
`↳ 처리(2026-09-08)` 근거 줄을 붙였다. 보류 문서의 OBSERVE·BLOCKED 69건도 전수 재판정해 66 STILL-DEFERRED · 1 NOW-ACTIONABLE ·
2 ALREADY-RESOLVED 로 판정했고, 그 1건(승인 단일성 E2E 재평가, OBSERVE#5)은 이 스윕이 바로 그 「다음 승인 생명주기 작업」이라
근거와 함께 재판정해 보류 문서에 기록했다. 착지 중 발견한 1건만 아래에 남긴다.

## 후속 과제 스윕 7 착지 후 남긴 것 (2026-09-08)

- **릴리스 승인 CLI 두 파일이 250 pure-LOC 경고 구간에 들어왔다** — 자가 회수 분기를 넣으며
  `automation/release_approval.py` 245 · `automation/release_abandon.py` 227 이 됐고, 전자는 회수 조립을
  후자로 옮겨 F2 등록부에서 내려온 참이다(등록부 항목 제거 완료). 조치: 다음 기능 추가 때 **먼저**
  CLI 명령 조립(argparse 배선)과 종결 표시(abandon 회신·감사 문구) 책임을 분리한 뒤 기능을 얹는다 —
  지금 나누면 이번 사이클의 검증된 이음새를 근거 없이 다시 흔든다. **영향: 다음 변경의 유지보수 비용,
  현재 동작·인가 경계 무영향 · 심각도 낮음**.

## 라이프로그 전사·화자 품질 교정 착지 후 남긴 것 (2026-09-06)

> ↳ 2026-09-08 후속 과제 스윕 7에서 2건 해소(임계값 전제 재판정 · `text_of` 조립 판정) — 남은 1건(화자 상한 8)은 실측 선행이라 OBSERVE 로 이관했다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 전사 정확도 문서 공개 검사 잔여 (2026-09-07)

> ↳ 2026-09-08 후속 과제 스윕 7에서 해소 — 두 기능 소개 문서의 계정 홈 절대 경로를 설치별 자리표시자로 바꿨다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## mailon 런타임 드리프트 프로브의 수렴 안내 (2026-09-07)

> ↳ 2026-09-08 후속 과제 스윕 7에서 해소 — 프로브가 드리프트 방향을 판정해 런타임이 앞선 창에서는 `automation/release.sh` 를 안내한다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 스킬 편집기 import 해석 복구로 드러난 타입 부채 (2026-09-07)

> ↳ 2026-09-08 후속 과제 스윕 7에서 해소 — 지목된 9건이 진단에서 사라졌다(억제·설정 되돌림 없음). 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## cytoplasm 신규 노드 설치 보고에서 드러난 공백 (2026-09-07)

> 외부 설치자가 신규 설치기(`automation/install/`)로 처음 완주하며 보고한 6건 + 문서 공백 1건.
> 판정·우회·반영의 전문은 [신규 노드 설치에서 막히는 6곳](troubleshooting/신규-노드-설치-공백.md).
> **7건 전부 2026-09-07 에 해소했다** — 열린 항목 없음. 처리 근거는
> [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 라이프로그 화자 분리 정정 착지 후 남긴 것 (2026-09-07)

> ↳ 2026-09-08 후속 과제 스윕 7에서 1건 해소(재처리 노트 경로 고정), 2건은 OWNER·OBSERVE 로 이관했다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 설치 마법사·프로필 착지 후 남긴 것 (2026-09-08)

> ↳ 2026-09-08 설치기 착지로 이관 — 원문·처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 제안서 엔진 내제화 착지 후 남긴 것 (2026-09-08)

> ↳ 2026-09-08 후속 과제 스윕 7에서 1건 해소(샌드박스 사이드카), 엔진 공개 여부는 OWNER 로 이관했다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## healthcheck 래퍼 설치 자산의 non-root 운영자 결함 (2026-09-08)

- **컨테이너 하네스가 `operator_account='root'` 로만 돌아, 운영자가 root 가 아닌 노드에서만 나타나는 결함을 v1.6.1 로 내보냈다** →
  `tests/e2e/install/systemd_container/run.sh` 에 non-root 운영자 경로(`--operator NAME`: 계정 생성·config 렌더·summary 필드)를 더한다.
  이번 사이클에서는 그 컨테이너 안에 손으로 계정을 만들어 증명했고(증적 12), 회귀는 argv 계약으로 고정했다.
  **영향: 같은 종류의 root 가정이 또 새어 나갈 수 있음 · 동작은 정상 · 심각도 중**.

증적: [docs/qa/INSTALL-TUI/12-probe-asset-nonroot-operator.txt](qa/INSTALL-TUI/12-probe-asset-nonroot-operator.txt).

## v1.6.0 릴리스 착지 후 남긴 것 (2026-09-08)

> ↳ 2026-09-08 후속 과제 스윕 7에서 2건 모두 해소 — 승인 뒤 tip 이 전진한 릴리스를 감사형 abandon 으로 자가 회수하고, 죽은 카드에 상태 회신을 남긴다([소개](기능소개/릴리스-승인-자가-회수.md)). 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 수리 스윕 5 착지 후 남긴 것 (2026-09-08)

> ↳ 2026-09-09 정산 — 3건 해소(PR #466 converge stderr · #468 원장 헤더 · #467 `_reference` 드롭 관측),
> 프롬프트 v5 요약 산문 준수 1건은 관측 조건 미성립으로 OBSERVE 이관. 원문과 처리 근거는
> [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 토큰 없이 설치 완주 착지 후 남긴 것 (2026-09-09)

> **2건 전부 2026-09-09 에 해소했다** — 열린 항목 없음. 원문과 처리 근거는
> [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.
> 이 반영이 만든 소유자 몫 1건(래퍼 재프로비저닝)은 같은 문서의 OWNER 항목에 있다.

## 수리 티켓 유실 사고 수정 중 발견한 인접 결함 (2026-09-09)

> ↳ 2026-09-09 정책 결정으로 해소 — 결론은 **켜지 않는다**이고 대체된 선행 구현을 삭제했다. 원문과 판단 근거는
> [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.
