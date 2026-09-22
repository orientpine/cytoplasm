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

## 화자 식별(voice catalog ③) 착지 후 남긴 것 (2026-09-18)

> ↳ 2026-09-19 kanbanfix 통합 정산: 3건 해소(PR #505·#508·#523), 3건 OBSERVE(노드 카탈로그가 등록 1명 그대로), 1건 OWNER(`--reprocess` 재전사) 이관. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

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

> ↳ 2026-09-19 kanbanfix 통합 정산에서 해소: PR #508 이 완결 기능을 얹기 전에 SPLIT 커밋으로 CLI 책임을 나눴다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

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

> ↳ 2026-09-19 kanbanfix 통합 정산에서 해소(PR #507): 하네스가 `--operator NAME` 을 받는다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

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

## 문서 검토 안내의 전송 경계 (2026-09-11)

> ↳ 2026-09-19 kanbanfix 통합 정산에서 해소(PR #513): 검토 안내 3종이 통지 파사드로 나간다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 소유자 메시지 계약 착지 후 남긴 것 (2026-09-12)

> ↳ 2026-09-19 kanbanfix 통합 정산: 3건 해소(PR #500·#504·#514), footer·publish 접두부 2건은 OBSERVE 로, 분할기 단일화 1건은 동결 파일(`automation/deploy-skill.sh`) 때문에 BLOCKED(PR #502 열림, 소유자 해제 대기)로 이관. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 승인 요청 안내 메시지·비공개 표면 허용경로 착지 후 남긴 것 (2026-09-09)

> ↳ 2026-09-19 kanbanfix 통합 정산: 장문 승인 첨부 1건 해소(PR #511, 실게시 C2 는 배포 뒤), 옛 카드 재게시는 노드 확인 선행 OWNER, `VerifiedRoute` 확장은 범위 결정 OWNER 로 이관. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 캘린더 카드 누락 백스톱과 통지 라우팅 착지 후 남긴 것 (2026-09-10)

> ↳ 2026-09-11 두 건 모두 해소 — 단위 테스트 홈 격리(`tests/unit/conftest.py`)와 리마인더의 요청 스레드 배달로 닫혔다. 원문과 판단 근거는
> [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## Google Tasks 합성 과제 유입 수리 중 발견한 인접 결함 (2026-09-11)

> ↳ 2026-09-19 kanbanfix 통합 정산에서 해소(PR #498): 네 테스트의 Drive 실행 경계 스텁, 변이 증명 포함. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 반출 릴리스 노트 안내 착지 후 남긴 것 (2026-09-13)

> ↳ 2026-09-19 kanbanfix 통합 정산에서 해소(PR #498): `tests/unit` executionEnvironment 와 선언 가드. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 워킹트리 전량 커밋 착지 후 남긴 것 (2026-09-14)

> ↳ 2026-09-14 같은 날 해소(소유자 지시 「gitignore 에 넣으면 좋을 것들 점검」) — `.omo/senpi-task/` 를 무시하고 인덱스에서 내렸다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## plaud ogg 형식 사고 착지 후 남긴 것 (2026-09-16)

> ↳ 2026-09-19 kanbanfix 통합 정산에서 해소(PR #501): 재확인 오류는 `last_recheck_error` 로 분리된다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 2026-09-19 전수 처리 (kanbanfix 통합 정산)

시작 시 열린 22건(9묶음)을 전부 판정했다: 13건 해소(PR #498·#500·#501·#504·#505·#507·#508·#511·#513·#514·#523), OBSERVE 5·OWNER 3·BLOCKED 1 이관.
불릿은 지우지 않고 원 `##` 헤딩·본문 그대로 [follow-ups-deferred.md](follow-ups-deferred.md) 에 옮겨 `↳ 처리(2026-09-19)`·`↳ 이관(2026-09-19)` 줄에 근거를 붙였다.
해소 13건은 **머지까지**다: 프로덕션은 `b8333c5c6`(v1.9.1) 그대로이고, 장문 승인 실게시(C2)·릴리스 카드 버튼(C6) 같은 실표면 확인은 다음 릴리스 뒤 몫이다.
분할기 단일화(PR #502)는 코드·CI 가 끝났으나 수정 동결된 `automation/deploy-skill.sh` 한 줄 때문에 소유자 해제를 기다린다.
회계 전문은 `.omo/evidence/kfx/active-closeout.md`. 저장소에서 지금 손댈 열린 건은 없다.

## 2026-09-20 라이브 마감 (v1.9.2 → v1.9.4)

v1.9.2 배포 영수증이 proposal 마운트 후 스모크 실패를 덮은 것을 실측으로 잡아 세 건을 고쳐 머지했다(PR #526 실패 전파, #527 CDN User-Agent, #528 마운트 상대 import). v1.9.3 에서 남은 시나리오 fixture import(PR #531)와 소유자 지시 두 건 — 승인 스레드의 일정 상세 표시(PR #530), 승인 카드 Discord 가독성 재설계(PR #532) — 를 v1.9.4 로 배포했고 `deploy_all.sh --verify` exit 0, 배포된 v4 일정 카드·장문 메일 첨부·proposal CLI 를 노드에서 read-back 했다. 수리 티켓 7건은 전부 done. 실측 증적은 `docs/qa/KFX-LIVE/summary.md`, 세션 원장은 `.omo/evidence/kfx/live-recovery-status.md`.

## 승인 카드 가독성 재설계 착지 후 남긴 것 (2026-09-21)

- **릴리스 카드 v5 의 참조 꼬리표가 비어 보인다** — `-# 참조: \`release:\`` 로 렌더된다(다른 생산자는 draft id 앞 8자). `automation/release_spec_message.py` 의 subject_key 가 빈 접미로 끝나서다 → 버전(`v1.9.4`) 이나 배포 기준 앞 12자를 참조로 싣는다. 저장된 v1~v4 는 불변, 새 v5 만. **영향: 표시 결함 · 승인 해시·바인딩 무관 · 심각도 낮음**.
  - ↳ [해소 2026-09-21] v1~v5를 byte-frozen으로 두고 v6 카드가 실제 첫 변경 상세 Discord 링크·버전·HEAD 앞 12자를 참조로 싣는다. guild 미상은 검색 안내로 저하한다.
- **release v5·skill deploy/publish v3 에 바이트 골든이 없다** — 일관성은 `bound()`/봉투 입력 핀으로만 잡혀 있어 문구가 조용히 바뀌어도 잡히지 않는다 → `tests/unit/mail_approval_card_golden.py` 와 같은 방식으로 세 카드의 새 버전 바이트를 고정한다. **영향: 회귀 탐지 공백 · 심각도 낮음**.
- **`automation/release_card.py:25` 가 이름 없는 `3` 을 쓴다** — 위 두 줄은 이름 상수를 쓰는데 폴백 버전만 리터럴이다 → 기존 상수 이름으로 맞춘다. **영향: 가독성 · 심각도 낮음**.
  - ↳ [해소 2026-09-21] v6→v5→v4→v3 폴백 순서를 모두 이름 상수로 고정했다.

## Grok 폴백 착지 후 남긴 것 (2026-09-22)

- **라우팅 로그가 실제로 답한 모델을 적지 않는다** — mail `triage_llm._log_call`·doctype `_log_call` 등 마스킹 라우팅 로그는 `provider=openai-codex` 를 그대로 적는다. Hermes 가 `fallback_providers`(`xai-oauth/grok-4.7`)로 넘긴 호출도 Codex 로 기록되므로 특허 민감 본문이 실제로 어느 제공자에게 갔는지 로그만으로는 감사할 수 없다 → 공용 클라이언트(`automation/codex_llm.py`)가 `hermes -z … --usage-file <tmp>` 의 `provider`·`model` 을 읽어 돌려주고 각 로그가 그 값을 싣는다. **영향: 감사 정확도만 · 라우팅·민감도 게이트·실행 결과 불변 · 심각도 낮음**.
  - ↳ [해소 2026-09-22] 공용 클라이언트에 `complete_served()` 를 더해 Hermes `--usage-file` 보고서의 provider·model 을 돌려주고, 메일·doctype·특허 초안·제안서·보고서·주간 연구 동향 로그 6곳이 `served_provider`·`served_model` 을 싣는다(기존 `provider`·`model` 은 요청한 주 경로 그대로). 보고서가 없으면 `unknown`. 회귀 `tests/unit/test_served_route_logging.py`.

## ASR 후보 평가 후 남긴 것 (2026-09-22)

- **용어집 힌트가 whisper 에 닿지 않는다** — 로컬 전사는 `-mc 0`(`SPEECHTOTEXT_WHISPER_CONTEXT` 기본)으로 돌고 whisper.cpp 는 `n_max_text_ctx > 0` 일 때만 prompt 를 붙여 `--prompt` 가 통째로 버려진다(힌트 유무 출력 바이트 동일, [STS2](qa/STS2/summary.md)). `--carry-initial-prompt`+`-mc 64` 로 강제하면 대리 기준 CER 14.2→19.2%(삭제 급증) → 소유자 교정 정답 3건 이상으로 다시 재서 힌트 경로를 걷어 낼지(문서·`asr_fingerprint` 정리) carry 로 살릴지 정한다. **영향: 없는 기능을 있다고 적은 문서 · 전사 결과 불변 · 심각도 중**.
