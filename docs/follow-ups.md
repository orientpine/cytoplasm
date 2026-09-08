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

## 라이프로그 전사·화자 품질 교정 착지 후 남긴 것 (2026-09-06)

- **기본 임계값 1.35 는 회의에서 과병합 쪽으로 기운다** — PR #422 가 소유자 확인 64분 녹음(화자 2)으로
  고른 값이고 lifelog 에는 맞지만, 15분 실회의를 1.35 에서 화자 1명으로 묶는다(1.30=2, 1.10=9). 파편
  가드는 군집 수를 바꾸지 않으므로 그 보정 자체는 유효하다. 조치: 회의 경로에서는 `--speaker-count`
  를 쓰고(그것이 PR #422 문서와 이 문서가 같이 도달한 결론), 소유자가 화자 수를 아는 다화자 녹음이
  생기면 그 값으로 회의용 기본값을 별도로 잴지 판단한다. **정확도 · 심각도 중**
  (증적 `docs/qa/PLQ1/summary.md` §4, `docs/qa/FU6/diarize-threshold-validation.md`).
- **`stt_window.text_of` 만 아직 세그먼트를 `" ".join` 으로 잇는다** — 반복 붕괴 검사 전용이라 문서에
  도달하지 않지만, 세그먼트가 이미 앞 공백을 갖고 오므로 이 경로만 조립 규칙이 다르다. 조치: 반복
  검사 입력을 문서와 같은 조립으로 통일할지 별도 판단(임계값 0.08 의 의미가 함께 바뀐다).
  **문서 영향 없음 · 심각도 낮음**.
- **화자 상한 8 은 개인 라이프로그에 크다** — 2인 녹음이 상한 보수를 타면 최대 8명이 된다. 조치:
  lifelog 경로에만 낮은 상한(`SPEECHTOTEXT_DIARIZE_MAX_SPEAKERS`)을 줄지 실측 후 판단한다.
  **정확도 · 심각도 낮음**.

## 전사 정확도 문서 공개 검사 잔여 (2026-09-07)

- **기존 기능 소개 2곳에 계정 홈 절대 경로가 남아 전체 개인화 검사가 2건을 검출한다** →
  `docs/기능소개/대시보드-비밀번호-교체.md`의 자격증명 조회 예시와
  `docs/기능소개/제안서-노드-자율-구동.md`의 브라우저 예시를 설치별 자리표시자로 바꾼다.
  이번 lane은 신규 기능 소개만 쓰기 허용이라 기존 문서는 보존했다. 실제 비밀 값 검출은 아니며
  **영향: 기존 문서의 설치 종속 예시·전체 개인화 검사, 런타임 무영향 · 심각도 낮음**.

증적: `.omo/evidence/transcript-accuracy/task-21.md` (변경 전·후 동일 2건).

## mailon 런타임 드리프트 프로브의 수렴 안내 (2026-09-07)

- **`mailon_runtime_drift.sh` 가 런타임이 릴리스 트리보다 *앞선* 창에서도 같은 DRIFT 문구로 "deploy.sh 를 돌려 수렴하라"고 안내한다 — 그 방향에서는 deploy 를 몇 번 돌려도 수렴하지 않는다(릴리스 트리는 서명 태그로만 전진한다)** →
  판정에 방향을 넣어, 런타임이 앞선 창이면 `automation/release.sh` 를 안내한다. 그 창은 "머지 직후 자기 변경을 배포"라는 가장 흔한 순서에서 열린다(2026-09-07 실측: runtime=78ee65a2 vs 릴리스 트리=d575b6de).
  **영향: 온디맨드 프로브의 안내 문구뿐 — healthcheck 레지스트리에 배선돼 있지 않아 반복 경보가 없고 런타임 동작과도 무관 · 심각도 낮음.**

## 스킬 편집기 import 해석 복구로 드러난 타입 부채 (2026-09-07)

- **`automation.*` import 설정 공백을 메우며 Unknown에 가려졌던 기존 타입 오류 9건이 드러났다** →
  `pyrightconfig.json`의 19개 스킬 `extraPaths: ["."]` 확장은 유지하고, 아래 8개 파일은 스킬별 타입
  좁히기·정확한 시그니처로 별도 사이클에서 처리한다. 전사 감사 수리와 무관한 제품 파일은 이번 PR에서
  수정하지 않는다. `npx --yes basedpyright --outputjson skills`의 severity=error 총계는 **602→462**이며,
  새로 보인 진단은 `reportArgumentType` 7건 + `reportReturnType` 2건이다(줄 번호는 task-31 실측 기준).
  - `skills/budget/scripts/budget_approval.py:277` — `reportArgumentType` **1건**:
    `assert_never`에 전달하는 `Outcome`을 `Never`로 좁히지 못함 → 기존 분기 사실을 타입으로 표현한다.
  - `skills/budget/scripts/budget_confirm.py:200` — `reportReturnType` **1건**:
    `str` 반환 자리에 `object` → 기존 문자열 보장 지점에서 반환 타입을 좁힌다.
  - `skills/calendar/scripts/calendar_approval.py:285` — `reportArgumentType` **1건**:
    `Outcome` → `Never` 불일치 → 기존 분기 사실을 타입으로 표현한다.
  - `skills/calendar/scripts/calendar_preflight.py:214,216` — `reportArgumentType` **2건**:
    `list[str]` → `JsonValue` 대입과 `dict[str, JsonValue]` → `Mapping[str, str | list[str]]` 인수 불일치 →
    실제 payload의 필드 타입과 `draft_sha256` 호출부 시그니처를 맞춘다.
  - `skills/coordination/scripts/coordination_approval.py:274` — `reportArgumentType` **1건**:
    `Outcome` → `Never` 불일치 → 기존 분기 사실을 타입으로 표현한다.
  - `skills/mail/scripts/triage_approval.py:407` — `reportArgumentType` **1건**:
    `Outcome` → `Never` 불일치 → 기존 분기 사실을 타입으로 표현한다.
  - `skills/mail/scripts/triage_confirm.py:171` — `reportReturnType` **1건**:
    `str` 반환 자리에 `object` → 기존 문자열 보장 지점에서 반환 타입을 좁힌다.
  - `skills/wiki/scripts/wiki_approval.py:293` — `reportArgumentType` **1건**:
    `Outcome` → `Never` 불일치 → 기존 분기 사실을 타입으로 표현한다.
  **영향 범위: 위 스킬의 편집기 정적 타입 진단뿐 · 심각도 낮음.** 보안·런타임 동작 문제가 아니라
  가려진 타입 부채라는 판정이다. 근거: 루트 `AGENTS.md`의 `pyrightconfig` 항목이 이 설정을
  편집기 전용이며 런타임·테스트·CI 동작과 무관하다고 선언하고, 이번 경로 확장은 위 8개 파일의
  실행 코드를 바꾸지 않았다. 설정을 되돌리면 Unknown으로 다시 숨길 뿐이므로 오류 억제·경로 복원으로
  처리하지 않는다. 후속 수리도 새 검증·예외 경로 없이 이미 참인 사실을 타입으로 표현한다.

증적: `.omo/evidence/transcript-accuracy/task-31.md` (B 결정·파일별 원문 진단·전체 회귀 출력).

## cytoplasm 신규 노드 설치 보고에서 드러난 공백 (2026-09-07)

> 외부 설치자가 신규 설치기(`automation/install/`)로 처음 완주하며 보고한 6건 + 문서 공백 1건.
> 판정·우회·반영의 전문은 [신규 노드 설치에서 막히는 6곳](troubleshooting/신규-노드-설치-공백.md).
> **7건 전부 2026-09-07 에 해소했다** — 열린 항목 없음. 처리 근거는
> [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 라이프로그 화자 분리 정정 착지 후 남긴 것 (2026-09-07)

- **재처리가 노트 이름을 바꿔 옛 노트를 고아로 남길 수 있다** — 노트 경로는 매 쓰기마다
  제목에서 새로 계산된다(`note.corrected_lifelog_note` → `lifelog_relpath`). 제목이 없던
  녹음을 `--reprocess` 하면 생성 제목이 붙어 파일 이름이 바뀌고, vault 의 옛 파일은 지워지지
  않은 채 남는다. 조치: 레코드에 이미 있는 `note_relpath` 를 재처리 경로에서 재사용해 첫
  경로에 못 박는다(`transcribe_promote`/`commit` 이음새). 영향: 데이터 손실은 없고 중복
  노트 1건이 생길 뿐이지만, 같은 녹음이 노트 둘로 갈라지면 RAG 인제스트가 둘 다 먹는다.
  심각도 중.
- **화자 수 질의 패스가 sherpa 백엔드에서는 성과가 작다** — 실측에서 화자 수를 고정해도
  sherpa(eres2net)·titanet 은 세 번째 목소리를 0.5~0.8% 조각으로만 내놓고, pyannote 만
  발화 시간 11.4%·15.8% 로 찾아낸다(docs/qa/PLD1 §5). 즉 질의 패스의 값어치는 백엔드가
  pyannote 일 때 나온다. **그리고 그때도 절반이다**: 라벨을 걷어낸 초안으로 두 녹음을 끝까지
  돌리니 272.5초는 모델이 3(기준점 3)이라 답해 재분리가 일어났고, 549.4초는 8,624자 초안에서도
  2(기준점 4)라 답해 재분리가 없었다. 반대로 549.4초는 질의 패스 없이 기본값만으로 실질 4명이라
  기준점과 같다 — 두 경로가 서로 다른 녹음을 맞히므로 어느 하나를 자동 기본으로 삼을 근거가 없다. 조치: 라이프로그 경로만 `SPEECHTOTEXT_DIARIZE_BACKEND=pyannote`
  로 돌릴지 판단하려면 CPU-only 비용(0.57x 실시간 — 64분 녹음 한 패스 약 37분)을 소유자가
  받아들일지가 선행 조건이므로, 코드에서 기본값을 바꾸지 않고 남긴다. 영향: 현재 기본값
  으로도 발화 구분은 회복됐고 화자 수만 근사다. 심각도 중.
- **약한 화자는 낱말을 한 번도 이기지 못해 문서에 오르지 못한다** — 549.4초 녹음은 실질
  4군집(22.5 / 21.9 / 20.7 / 16.4%)이 나오는데 전사본에는 `화자1`·`화자2` 둘만 오른다.
  2026-09-07 오후에 **조각화 쪽은 해소했다**(문장 조립 순서와 화자 변경 경계 — 화자0 블록
  47.6%→33.3%, 49.7%→39.3%, `docs/qa/PLD2`), 그러나 약한 두 화자가 문서에 오르지 못하는 것은
  남았다: 그들은 낱말 단위에서 한 번도 이기지 못하므로 문장 단위 다수결로도 올라오지 않는다.
  낱말 판정 분포는 direct 76.1% · low_coverage 7.1% · no_support 6.8% 이고, 이 둘은 그
  low_coverage/no_support 쪽에 몰려 있을 것으로 보이나 **화자별로는 아직 세지 않았다**.
  조치: reason 을 화자별로 쪼개 어느 화자가 어느 규칙에서 지는지 먼저 보고, 그 다음에 정책을
  손댄다. 겹침→화자0 은 1ms 겹침 회귀가 고정한 fail-safe 이고 실측에서도 겹침 몫 중앙값이
  0.457 이라 대부분 진짜 동시 발화이므로 그것은 계속 건드리지 않는다. 영향: 화자 수가 실제보다
  적게 보인다. 낱말은 잃지 않는다(`화자0` 으로 남는다). 심각도 중.

## 설치 마법사·프로필 착지 후 남긴 것 (2026-09-08)

- **`rag`·`report-hub` 프로필은 헬스체크 선언만 정하고 RAG 스택·report-hub 유닛을 배치하지 않는다** →
  다음 단계로 `OPT_IN_COMPONENTS` 확장을 설계 판단한다(`automation/install/components.py:39–49`).
  **영향: 추가 서비스 배치는 별도 준비 · 동작은 정상 · 심각도 낮음**.
- **healthcheck SSH forced-command 래퍼는 여전히 owner-run 별도 절차다** →
  `automation/provision-healthcheck-probe.sh:2–6`의 절차를 설치 자산으로 편입할지 검토한다.
  **영향: 신규 노드의 `check healthcheck`가 `INFRA_FAILURE`로 FAIL할 수 있음 · 심각도 중**.
- **컨테이너 실제 설치 검증은 `hermes-gateway` 외부 전제에서 멈춘다** →
  실호스트 첫 완주 때 아래 QA 디렉터리에 증적을 추가한다(**OBSERVE**).
  **영향: Hermes·Discord 뒤 타이머·최종 healthcheck 완주 근거가 아직 없음 · 심각도 낮음**.

증적: [docs/qa/INSTALL-TUI/](qa/INSTALL-TUI/) · [systemd 하네스](../tests/e2e/install/systemd_container/README.md).

## 제안서 엔진 내제화 착지 후 남긴 것 (2026-09-08)

- **샌드박스 scenario 의 render 가 엔진 입력 계약에서 선다.** `scenario.sh` 의 가짜 draft 는 실제
  draft 가 쓰는 `drafts.json.planspec.json`·`.pms.json` 사이드카를 만들지 않아, render 가
  `refined drafts sidecar source is missing` 로 멈춘다. **회귀가 아니다** — 내제화 전에는 핀 검사가
  exit 4로 먼저 죽어 샌드박스가 render 를 한 번도 실행한 적이 없었고, 이제야 그 사실이 보인다.
  조치: 샌드박스 draft 단계가 사이드카를 함께 내도록 하면 scenario 가 렌더까지 완주한다. 영향 범위는
  샌드박스 스모크뿐이고 실제 렌더는 `.omo/evidence/docbot-internalization/` 의 실측으로 증명돼 있다.
  **영향: 샌드박스 E2E 의 render 단계 커버리지, 프로덕션 렌더 무영향 · 심각도 낮음**.
- **엔진 트리의 공개 여부는 소유자 판단으로 남았다.** 원본 `kimm-docbot` 이 비공개였으므로
  `configs/public-export-manifest.txt` 에 디렉터리 한 줄로 제외해 현상을 유지했다. 공개로 승격하려면
  FS3 사유 원장에 엔진 경로를 등록해야 한다(약 78행). 판단 전까지 공개 배포본의 proposal 스킬은
  렌더 엔진 없이 나간다 — 내제화 이전과 같은 상태다.
  **영향: 공개 배포본의 proposal 스킬 완결성, private 저장소·노드 런타임 무영향 · 심각도 중**.

## v1.6.0 릴리스 착지 후 남긴 것 (2026-09-08)

- **소유자 ✅ 를 받은 릴리스 요청이 태그 전에 origin/main 이 전진하면 영구히 실행 불가가 되는데, `release.sh` 의 자동 회수는 `bound_pending` 만 다루고 완결 타이머는 매 틱 `RELEASE-DECISION: live request is bound to a different HEAD` 로 조용히 끝난다(2026-09-07 17:46 KST v1.6.0@2a0a20267 실측 — 승인 뒤 main 이 75커밋 전진, 다음 날 `release.sh` 는 `RELEASE-RETIRE-BLOCK: pending release does not match the latest signed head` 로 exit 4, 사람이 `release_approval_remote.sh abandon --version --head --message-id --reason` 을 돌려야 풀렸고 그동안 완결기는 18시간 동안 2분마다 같은 줄만 남겼다) → ① `release.sh` 가 시작 시 approved 이면서 head ≠ origin/main tip 인 레코드를 같은 감사형 abandon 으로 자가 회수하고 새 요청을 정확히 한 번 게시한다(새 요청은 옛 승인 범위의 상위집합이므로 재승인이 fail-closed 로 맞다), ② 완결 타이머는 같은 조건을 만나면 침묵하지 말고 소유자 통지를 에피소드당 1건 남긴다(매 틱 반복 금지). 인가 경계는 넓히지 않는다 — 옛 승인으로 새 tip 을 태그하는 경로는 만들지 않는다.** **영향: 릴리스 파이프라인 가용성(승인이 tip 전진보다 늦게 오는 모든 릴리스), 프로덕션 코드·노드 무영향 · 심각도 중**.
