# skills/ — 에이전트 스킬 (autophagy 고유 저작 규약)

스킬 하나 = `skills/<name>/` 디렉터리 하나. Hermes는 `~/.hermes/skills/<name>/`에
드롭되면 자동 발견(install 불필요). 배포는 루트 `automation/deploy-skill.sh` 4단계 게이트.

## 디렉터리 형식
```
skills/<name>/
├── SKILL.md              # 필수. frontmatter + 사용법 본문
├── scripts/
│   ├── <name>_cli.py     # 실행 진입점 (거의 모든 스킬)
│   ├── *watch.py / confirm_reaction_watch.py  # no-agent cron 워처 (있으면)
│   └── scenario.sh       # 필수(배포용). 샌드박스 검증 시나리오
├── prompts/  configs/    # 선택 (LLM 프롬프트, 스킬별 sensitivity 등)
└── deploy.sh             # 선택 (스킬 자체 배포 훅)
```

## SKILL.md frontmatter (파이프라인 lint가 강제)
- `name`: 디렉터리명과 정확 일치 — `^[a-z0-9][a-z0-9-]{1,40}$`
- `description`: 10자 이상. 라우팅 근거이므로 트리거·게이트·READ/mutate 구분 명시
- `version` / `license` / `metadata.hermes.tags`: 권장

## scenario.sh 계약 (샌드박스 통과 조건)
- `env -i HOME=… AUTOPHAGY_DEMO_SECRET=DUMMY-…` 격리 환경에서 실행 — **실시크릿 미주입**.
- 성공 시 stdout에 `SCENARIO-PASS` + exit 0. 그 외 전부 실패.
- 참조 구현: [hello-autophagy/scripts/scenario.sh](hello-autophagy/scripts/scenario.sh).

## 스킬 목록 (16 기능 + hello-autophagy 데모 = 17 디렉터리)
calendar · coordination · mail · budget · repair · patent-prep · proposal · report ·
topics · prompt · meeting · recall · wiki · todo · doctype · procurement · (hello-autophagy=데모).

## 예외 매트릭스 (표준 계약 밖)
- 승인 워처 보유: calendar · coordination · mail(triage+digest 2개) · budget · wiki · patent-prep(export confirm). `deploy.sh` 보유: calendar · coordination · mail.
- CLI 명명 예외: mail=`triage_cli.py`, coordination=`coordinate_cli.py`, procurement=`procure_cli.py`+`procure_registry_cli.py`(2개).
- meeting: 워처 대신 게이트웨이 **플러그인 훅**(`meeting/plugin/plugin.yaml`).
- repair: repo-local CLI 아님 — `~/.hermes/repair/automation/repair/repair_cli.py` 외부 런타임 호출.
- wiki: 본문·제목은 owner DM 밖 유출 금지. 트윈 판단은 `review_after` 만료 시 자율 행동에 사용 금지(`docs/guide/decision-twin-스키마.md`).
- automation 공유 코드: calendar/coordination/mail/budget/wiki/meeting/todo는 `automation.interop.*`(external_effect_gate · injection_adapter · discord_transport · coordination)에 의존 — interop 시그니처 변경 시 동반 갱신.

## 이 디렉터리의 필수 규칙
- **mutating 경로는 반드시 승인 게이트 경유** (draft/pending → render → resolve_reaction
  → watch cron). 게이트 재사용: 새 승인 표면은 레코드의 `channel_id`로만 분기 — 별도 워처 신설 금지.
- **도메인 중첩 스킬은 결정론적 코드 가드 필수.** SKILL.md 라우팅 설명만으로 부족(LLM 확률적).
  가드는 **양방향**이어야 한다 — 한쪽만 막으면 두 경로가 동시 발동해 이중 쓰기(선례 사고
  2026-07-20: `peer-test 오전 10시`가 calendar 07-22 + coordination 07-29 이중 등록).
  calendar↔coordination은 공유 판정 `calendar_routing.classify_meeting_request`
  (calendar|coordination|clarify)를 두 CLI가 함께 강제: 정확-단일-시각=calendar,
  피어+범위+조율의사=coordination, 모호=clarify(fail-closed). calendar는 비-calendar를
  exit 4 `ROUTING-REJECT`/`ROUTING-CLARIFY`로, coordination은 정확-단일-시각을 피어 질의
  전 exit 2 `ROUTING-REJECT`로 거부. READ는 가드 대상 아님.
- **cron 워처 파일명은 `~/.hermes/scripts/` 안에서 스킬별 고유** (예: `calendar_confirm_reaction_watch.py`).
- **추적 config는 시드 전용** — 스킬 런타임 상태(mode 재판정 등)는 ~/.hermes 등 체크아웃 밖에 기록한다. 런타임 경로가 시드/체크아웃 경로를 가리키면 코드 가드로 fail-closed 거부(선례: mail triage_mode._runtime_path_shadows_seed).
- 상세: [docs/guide/스킬-제작.md](../docs/guide/스킬-제작.md), [docs/guide/watcher-cron-설계규약.md](../docs/guide/watcher-cron-설계규약.md).
- **승인 메시지를 게시하고 `message_id`를 저장하는 모든 스킬은 `automation.interop.approval_lifecycle.request_owner_approval`을 경유해야 함**: 이는 `dm_message_id`, `confirm_message_id` 등을 포함한 모든 소유자 승인 표면에 적용됩니다. 각 스킬은 `<skill>_approval.py` 어댑터를 통해 파사드를 호출하며, 저장소 참조는 lazy import + `AUTOPHAGY_REPO_ROOT` `sys.path` 삽입을 사용합니다. `ImportError` 발생 시 무가드 게시로 폴백하지 않고 요청을 거부(fail-closed)해야 합니다. 강제 수단으로 `tests/unit/test_approval_lifecycle_conformance.py` 준수 테스트가 실행됩니다.
