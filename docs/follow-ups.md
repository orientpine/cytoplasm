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

## 문서 검토 안내의 전송 경계 (2026-09-11)

- **doctype·proposal·procurement 검토 안내가 통지 파사드 밖에서 전송된다** → 전송 수단을
  `owner_notice`로 옮길지 별도 설계한다. 지금은 doctype·proposal의 `hermes send` argv·청킹·실패 처리와
  procurement의 직접 메시지 POST·첨부 경로를 그대로 두고, 본문만 소유자 메시지 봉투로 렌더한다.
  **영향: 세 검토 안내의 전송 정책 일원화 미완 · 심각도 낮음**.

새 안내는 검토 문서를 대상으로 식별하고, procurement가 이미 받은 Drive 링크를 위치로 싣는다.
링크가 없는 첨부·발행 실패 경로와 doctype·proposal의 문서 경로는
`Ref(scope="resource", url=None, search=("문서 검색", 파일명))`으로 안내한다.
doctype·proposal 검토 호출 시점에는 Drive URL이 전달되지 않으므로 추측하거나 재발행하지 않는다.
문서 경로는 대상 식별에 남고, 제안서의 검토 의견은 기존처럼 해당 문서에 저장된다.
봉투를 import할 수 없거나 렌더러가 필드를 거부하면 기존 본문을 바이트 그대로 전송한다.

## 소유자 메시지 계약 착지 후 남긴 것 (2026-09-12)

- **분할기가 두 벌이다** — `interop/chunker.chunk_message`는 URL 경계를 보존하지만 `release_spec.split_messages`는 줄·머리글 예산으로 나눈다.
  → 저장 레코드의 상세 재생·조각 수를 보존하는 공통 분할 계약을 먼저 정하고 통합을 검토한다. `release_notes.post_details`는 별도 분할기가 아니라 그 결과의 전송자다.
  **영향: 릴리스 상세와 일반 통지의 URL 절단·분할 정책이 달라질 수 있음 · 심각도 중**.
- **지침 링크·앵커에 상시 가드가 없다** — 보드 A4는 `done.md`의 경로만 보고 앵커를 버리며, todo 28 검사기는 일회성 증적이다.
  → 루트 지침·가이드·소개 문서의 상대 링크와 앵커를 검사하는 저장소 가드를 추가하고 없는 경로·제목 변이로 실패를 증명한다.
  **영향: 낡은 지침 링크가 테스트 통과 뒤에도 남을 수 있음 · 심각도 중**.
- **발신자 그래프가 iterator 반환값에서 발신자를 잃는다** — `dict(zip((True, 1.0), (client.log, client.send_owner_dm)))[1]`은 실제 발신하지만 CLEAN이다.
  → `owner_message_sender_ast.py`의 iterator 허용과 `owner_message_sender_bindings.py`의 Call 결과 사이에 발신자 전파를 보존하거나, 전파를 증명 못 하면 거부한다. 표기별 예외 추가는 피한다.
  **영향: 명시적 발신자가 있어도 봉투 미채택을 놓치는 수정 가능한 검사 공백 · 심각도 중**.
- **승인 lease가 원래 거부 예외를 TypeError로 바꾼다** — 생성기형 `FileKeyLease.hold`를 빠져나오는 frozen `SubmissionArtifactError`의 traceback 대입이 실패한다.
  → class 기반 context manager로 잠금·해제를 보존하고 실제 제출 거부 경로에서 원 예외·게시 0건·잠금 해제를 함께 검증한다.
  **영향: 과대 제출 카드 거부의 진단·오류 처리, 무승인 발송은 아님 · 심각도 중**.
- **승인 카드 3종이 봉투 뒤에 바인딩 footer 한 줄을 남긴다** — budget(`budget_core.py:250`)·calendar(`calendar_confirm.py:147`)·coordination(`coordination_lifecycle.py:137`)이 5필드 뒤에 `sha256`(budget 은 `draft id` 포함)을 덧붙여 6줄이 된다. 그 footer 는 이 계획보다 앞서 존재했고 승인 해시 바인딩이 의존한다.
  → 줄 수만을 이유로 카드 버전을 신설하지 않는다(이미 게시된 카드의 바이트를 바꾸면 소유자의 ✅ 가 소급 무효가 된다). 다른 이유로 카드 서식을 손댈 때 바인딩을 다섯 필드 안에 수용할 수 있는지 함께 검토한다.
  **영향: 표시상 군더더기뿐 — F3 실측으로 행동 필드 뒤에 오고 경쟁 지시를 만들지 않으며 승인 위치를 가리지 않는다(해시를 필드 안으로 옮기면 오히려 가독성 저하) · 심각도 낮음**.
- **publish 카드가 기계용 wire 접두부로 11줄이 된다** — `skill_gate_specs.py:319`가 파싱 대상 접두부 뒤에 봉투를 잇는다. `_PUBLISH_BINDING`(`skill_gate_publish.py:34-38`)이 개행 구분 6줄 접두부를 요구해, F4 실측상 현재 11줄은 바인딩 일치·5줄 봉투 단독은 불일치·접두부를 5줄에 접어도 불일치다.
  → 「승인 흐름·게이트·POLICY_VERSION 불변」을 지키는 한 줄이지 않으므로, 게이트 파서 변경이 **독립적으로** 정당화될 때만 다룬다. peer attestation 은 publish 에 요구되지 않으므로 peer 의존은 근거가 아니다 — 게이트 파서 단독으로 성립한다.
  **영향: 소유자 가시 해악 미입증(F4) · 심각도 낮음**.

발신자 검사의 교훈: todo 38 초반의 모양 열거 대신, 현재는 알려진 발신자가 지나가는 모든 subscript에 하나의 값 기반 인증 조건을 적용한다.
불리언·실수·슬라이스·음수 인덱스의 누락을 남은 결함으로 다시 적지 않는다. 위 iterator 반환값 공백과
발신자 단서 자체가 없는 동적 경계는 다르며, 후자는 [보류 원장](follow-ups-deferred.md#소유자-메시지-계약-착지-후-남긴-것-2026-09-12)에 있다.
근거: [해시 감사](qa/OMUX/hash-binding-audit.md), [계약·파사드·분할기·검사 코드 대조와 재현 증적](../.omo/evidence/owner-message-ux/task-30.txt).

## 승인 요청 안내 메시지·비공개 표면 허용경로 착지 후 남긴 것 (2026-09-09)

> 착지 기능: [승인 요청 안내 메시지](기능소개/승인-요청-안내-메시지.md) ·
> [비공개 표면의 민감도 게이트](기능소개/비공개-표면-허용경로-추출.md)

- **안내 메시지는 새 요청에만 붙는다.** 이미 열려 있는 요청 스레드(진행 중인 캘린더·plaud 카드)는 옛
  형식이라 채널에서 여전히 본문 없는 시스템 줄로 보인다. 조치: 필요하면 각 워처의 **기존** 재게시 경로
  (`plaud_sync_watch.py --repost-posted` 등)로 다시 올린다 — 새 재게시 수단을 만들지 않는다.
  영향 범위: 표시만, 승인·해시 바인딩과 무관. 심각도: 낮음.
- **긴 본문 승인은 여전히 게시할 수 없다.** 수리 티켓 t_82644d12 의 두 절반 중 **쐐기**만 닫았다 —
  Discord 2000자를 넘는 승인 메시지는 이제 posting journal 을 예약하기 **전에** 거부되므로 그 키가
  영구히 막히지 않지만(예전에는 HTTP 400 뒤 모든 재시도가 POSTING_JOURNAL_STALE), 소유자가 요청한
  「요약 메시지 + 동일 메시지 본문 첨부」 게시·검증은 아직 없다. 조치: multipart 첨부 게시와
  lifecycle·리액션 확인 두 경로의 첨부 검증, 그리고 형식 판별자를 설계해 별도 사이클에서 구현한다
  (기존 승인 해시 검사는 그대로 두어야 이미 게시된 승인이 무효가 되지 않는다).
  영향 범위: `skills/mail/scripts/triage_{core,approval,confirm,gate}.py`. 심각도: 중.
- **`VerifiedRoute` 채택은 두 곳뿐이다**(`lifelog_extract_live`·`stt_speaker_ask`). 같은 모양의 게이트가
  회의록 등 **공개·공유 산출물** 경로에도 있으나 소유자 지시 범위("내가 보는 private 채널")를 벗어난다.
  조치: 확장하려면 표면별로 따로 판단한다. 영향 범위: 없음(현행이 규칙과 일치). 심각도: 낮음.

## 캘린더 카드 누락 백스톱과 통지 라우팅 착지 후 남긴 것 (2026-09-10)

> ↳ 2026-09-11 두 건 모두 해소 — 단위 테스트 홈 격리(`tests/unit/conftest.py`)와 리마인더의 요청 스레드 배달로 닫혔다. 원문과 판단 근거는
> [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## Google Tasks 합성 과제 유입 수리 중 발견한 인접 결함 (2026-09-11)

- **meeting 단위 테스트 4건이 워크스테이션의 실제 `gws` 로 소유자 Drive 를 읽는다** → PATH 에 로그만 남기는 가짜 `gws` 를 두고
  전량 스위트를 돌린 실측(`PYTEST_CURRENT_TEST` 기록): `tests/unit/test_meeting_skill.py::test_meeting_drive_publish_uses_note_date_and_label`(10회)
  · `::test_sensitive_meeting_skips_drive_publish`(4) · `::test_drive_facade_import_failure_does_not_block_local_save`(2) ·
  `tests/unit/test_meeting_project_ingest.py::test_pending_transcript_minutes_publish_under_its_project`(4) 가 `drive files list/get`
  (KIMM·autophagy 루트 폴더 조회, 폴더 id 재검증)을 실행한다.
  ↳ **2026-09-12 외부 도달은 닫혔다** — `tests/unit/conftest.py` 의 PATH 거부 스텁이 이 네 테스트의 `drive files list` 를
  exit 97 로 거부한다(실측: 가드 아래 `-k meeting` 305 passed — 네 테스트는 거부를 받고도 통과한다). 제안됐던 *파일 단위*
  autouse 가드는 채택하지 않았다: 같은 날 형제 워크트리의 pre-patch 사본이 실제로 Tasks 에 썼고, 파일 단위 가드는 그것을
  구조적으로 덮을 수 없다. **남은 것은 밀폐성뿐** — 네 테스트가 아직 `run` 주입 없이 실 캐시 경로를 *시도*하므로
  `DriveClient`/`drive_outputs` 의 `run` 을 주입하거나 `DRIVE_PUBLISH_ENABLED` 을 명시적으로 끄면 워크스테이션 상태에
  좌우되지 않는다. **영향: 외부효과 0(쓰기 0·읽기도 이제 차단) · 심각도 낮음**.

## 반출 릴리스 노트 안내 착지 후 남긴 것 (2026-09-13)

- **단위 테스트가 형제 테스트 모듈을 import 하면 편집기가 매번 가짜 오류를 낸다** → `pyrightconfig.json` 의
  `executionEnvironments` 에 `tests/unit` 이 없어 `from test_public_export import …` 같은 교차 import 8곳이 전부
  `reportImplicitRelativeImport` 를 낸다. 편집기 전용 설정이라 런타임·pytest·CI 는 무영향이지만, 편집 직후 진단을
  읽는 경로(에이전트 포함)에서는 진짜 오류와 섞인다.
  ↳ **조치**: `tests/unit` executionEnvironment 를 더하고, 선언 목록을 고정하는 `tests/unit/test_pyright_config.py` 를
  같은 커밋에서 갱신한다(PR #420 이 `skills/*/scripts` 19개를 더한 것과 같은 형태). **영향: 동작 결함 아님 · 심각도 낮음**.

## 워킹트리 전량 커밋 착지 후 남긴 것 (2026-09-14)

> ↳ 2026-09-14 같은 날 해소(소유자 지시 「gitignore 에 넣으면 좋을 것들 점검」) — `.omo/senpi-task/` 를 무시하고 인덱스에서 내렸다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.
