# AGENTS.md — autophagy-agents repo, orchestrator/agent instructions

**Generated:** 2026-07-20 · **Updated:** 2026-07-30 · **Commit:** 157dbd1 · **Branch:** main

## OVERVIEW
cha의 개인 Hermes 에이전트 시스템. Discord로 타 연구자 에이전트와 상호운용하며, 설치별 `<primary-node>` / `<rag-node>` 역할로 구동한다. Python 3.12 **stdlib 중심**. 주 대화 모델은 `openai-codex/gpt-5.6-sol`(구독, LiteLLM 예산 밖), `glm-main`(LiteLLM)은 폴백+배치 파이프라인 전용(2026-07-22 스왑). **모든 외부효과는 소유자(cha) 승인 게이트 경유** — 이것이 아키텍처의 중심 불변식이다.

## STRUCTURE
```
autophagy/
├── skills/       # 17개 스킬 디렉터리 (16 기능 + hello-autophagy 데모). 각 <name>/{SKILL.md, scripts/*_cli.py, scripts/*watch.py, deploy.sh}
├── automation/   # 오케스트레이션·게이트·워처·배치 코어 (interop, repair, rag_ingest, report_hub, skill_generation, twin_distill, twin_observe, managed_skills, managed_sync ...)
├── configs/      # routing-policy.md, peers.example.yaml, sensitivity-rules.yaml, external-effect-tools.yaml, rag/, litellm-staging/
├── prompts/      # 버전형 LLM 프롬프트 자산
├── docs/         # guide/(설계규약) patch/(인프라 변경) qa/(W#-# 웨이브 증적) troubleshooting/
├── tests/        # unit/ + e2e/(drivers, scenarios, fixtures)
├── .omo/         # 계획·오케스트레이션 상태 — plans/autophagy-agents.md = 설계 단일 진실
└── AGENTS.md · README.md
```

## WHERE TO LOOK
| 작업 | 위치 | 비고 |
|------|------|------|
| 새 스킬 제작·배포 | `skills/<name>/`, `automation/deploy-skill.sh` | 4단계 게이트, `docs/guide/스킬-제작.md` |
| 외부효과 승인 게이트 | `automation/interop/external_effect_gate.py` | fail-closed, 해시 바인딩 |
| 인터롭 규약(보고/질의/조율) | `docs/guide/interop-규약.md`, `automation/interop/` | 단일 진실 |
| 워처/cron 설계 규칙 | `docs/guide/watcher-cron-설계규약.md` | no-agent cron 필수 규약 |
| 기능 현황·웨이브 맵 | `docs/features.md` → `.omo/plans/autophagy-agents.md` | 계획 문서가 단일 진실 |
| 의사결정 트윈 스키마 | `docs/guide/decision-twin-스키마.md` | 트윈 키·판단 근거 규약 |
| 라우팅·모델·예산 정책 | `configs/routing-policy.md`, `configs/*.yaml` | 주=gpt-5.6-sol, glm-main=폴백·배치, `<monthly-soft-cap>`/`<monthly-hard-cap>`, patent-sensitive=GLM 403 |
| QA 증적 | `docs/qa/<wave-id>/` | 마스킹된 증적 (원시는 ops 전용) |
| Discord 2-서버·승인 채널 구조 | `docs/guide/discord-server-architecture.md`, `automation/interop/approval_surface.py` | **소유자 전용 승인=행위 봇의 오너 DM / 스킬 공급망 승인=개인 서버 `#approvals`**(배포·peer attest·발행·managed 활성화 — 2차 주체인 peer 봇이 같은 채널을 봐야 하므로). 893da68은 승인을 공유 Lab이 아닌 **개인 서버**로 옮긴 결정이며 공급망에 한정된다. 표면은 `approval_surface.py`가 단일 결정하고 conformance 테스트가 강제한다 — 산문이 아니라 코드가 진실. 이관 진행 중(AS): calendar·coordination·wiki·mail compose는 이미 DM, 나머지는 계획 `.omo/plans/approval-surface-ssot.md` 순서대로 전환 |
| 장애 대응·운영 제약 | `docs/guide/incident-response.md`, `docs/guide/operations.md` | 원인 확인 전 재시작/설정변경/키 재발급 금지 |
| 관리형 스킬 발행 | `automation/managed_skills/`, `docs/guide/managed-skill-channel.md` | 발행자(cha) 전용 |
| 개인→그룹 스킬 제출 | `automation/managed_skills/submission_*.py` | personal provenance 재사용, 그룹 `#approvals` 검토, 자동 import 없음 |
| 관리형 스킬 구독 | `automation/managed_sync/`, `~/.hermes/managed-sync/` | 구독자(연구원) 전용. 틱 배포는 `deploy.sh`(Hermes cron) 또는 설치기 **opt-in** `--with-component managed-sync`(systemd) — 둘 다 같은 래퍼를 돌린다. **배달은 자동, 마운트는 아니다**(D3) |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `evaluate_tool_call` | fn | `automation/interop/external_effect_gate.py` | 외부효과 denylist 게이트 — 읽기 허용, mutation은 소유자 승인 레코드 필요 |
| `DiscordTransport` | class | `automation/interop/discord_transport.py` | 순차 청킹 전송 + 429 Retry-After 백오프 (8 callers) |
| coordination | module | `automation/interop/coordination.py` | 에이전트간 일정 조율 순수 상태머신 (deadlock/재협상 규칙) |
| `skill_gate` | module | `automation/skill_gate.py` | 스킬 배포 승인 게이트 (✅ 리액션, `GATE_DIR`, `APPROVAL_LOG`) |
| `deploy-skill.sh` | script | `automation/deploy-skill.sh` | 배포 4단계: SANDBOX→REVIEW→owner ✅→MOUNT. 요청만 올리면(`--request-only`) **✅ 한 번으로 2분 내 자동 마운트** — `supply_chain_plan.SUPPORTED_KINDS`가 스킬 이름이 아니라 종류(`skill-deploy`) 기준이라 **신규 스킬도 등록 없이 포함**된다. 재개가 실패하면 그 요청은 유예되지만 영구가 아니다 — 실패 지문이 `릴리스 sha:사유`라 **원인을 고쳐 랜딩해 릴리스가 바뀌면 다음 tick에 자동 재시도**되고, 대기 중에도 매 tick `backoff (attempt N, retry in Ns)` 한 줄이 남아 사라진 것과 구분된다(`docs/guide/스킬-제작.md` §2) |
| `repair_capability` | module | `automation/repair/repair_capability.py` | repair 티켓·occurrence HMAC capability의 경쟁 안전 발급·게시·검증·소급 보정 |
| `repair_report_queue` | module | `automation/repair/repair_report_queue.py` | capability-bound enum-only 보고 요청 큐 + 엄격 파서 + 신원 대조 영수증 기반 compact |
| `repair_report_send` | module | `automation/repair/repair_report_send.py` | repair lifecycle 보고 직접 전송 + watermark 범위 정확 조회 |
| `repair_report_consumer` | module | `automation/repair/repair_report_consumer.py` | agent 소유 내부 큐 executor — 카드 readback 전이, 의미 예약, 보고 확정, 불변 영수증 |
| `repair_report_reconcile` | module | `automation/repair/repair_report_reconcile.py` | ops 종결 lifecycle에서 누락된 보고 요청을 멱등 복구하는 보정기 |
| `repair_report_consume_watch` | cron | `automation/repair/cron/repair_report_consume_watch.py` | Discord 수신 없이 agent 소유 런타임의 repair 보고 큐를 실행하는 no-agent 래퍼 |
| `rag_ingest` | pkg | `automation/rag_ingest/` | 개인 RAG 인제스트 (content-hash 멱등, patent-sensitive 태깅) |
| `recall` | module | `skills/recall/scripts/recall_cli.py` | RAG 검색 (v2: patent-sensitive는 주 모델 non-GLM 기계검증 시만 센티널 부착 포함, 그 외 제외 — GLM 폴백은 LiteLLM 센티널 403이 차단) |
| `obsidian` | module | `automation/rag_ingest/sources/obsidian.py` | Obsidian RAG 소스 (read-only git 미러, 민감 태깅) |
| `wiki_store` | module | `skills/wiki/scripts/wiki_store.py` | 위키 저장소 (5필수 + 트윈 키 스키마 v1) |
| `twin_consult` | module | `skills/wiki/scripts/twin_consult.py` | 트윈 판단 근거 랭킹/충돌 판정 (read-only) |
| `twin_distill` | pkg | `automation/twin_distill/` | LLM 기반 판단 근거 추출 (inferred) |
| `twin_observe` | pkg | `automation/twin_observe/` | 게이트 이력 기반 판단 근거 분석 (observed) |
| `ManagedManifest` | class | `automation/managed_skills/manifest.py` | 관리형 스킬 릴리스 매니페스트 (v1) |
| `resolve_signed_update` | fn | `automation/update_trust.py` | 공개 origin의 현재 head와 결합된 SSH-signed annotated tag만 업데이트 SHA로 승인 |
| `publish_cli` | module | `automation/managed_skills/publish_cli.py` | 관리형 스킬 발행 CLI (SSH 서명 태그) |
| `submission_cli` | module | `automation/managed_skills/submission_cli.py` | 개인 HEAD의 sha 고정 제출 + 기존 승인 표면 검토 요청(발행·마운트 없음) |
| `managed_sync` | pkg | `automation/managed_sync/` | 관리형 스킬 동기화 (fetch→verify→quarantine) |
| `managed_sync_watch` | cron | `automation/managed_sync/cron/managed_sync_watch.py` | 구독자 틱 **단일 구현** — Hermes cron(`deploy.sh`)과 systemd 타이머(`systemd/`, 설치기 opt-in)가 둘 다 이것을 돌린다. 겹친 틱=`FileKeyLease`로 무음 exit 0, 자식 env에 자격증명+두 runtime root 명시 전파, skill tag sync 성공 뒤 같은 mirror의 roster ref를 **별도 fetch**(부재/거부가 skill rc를 바꾸지 않음), staged 릴리스가 생긴 틱에만 기존 `owner_notice`로 best-effort 알림 1건(거부는 저널에만 — 매 틱 반복되므로 DM 홍수 방지) |
| `OPT_IN_COMPONENTS` | registry | `automation/install/components.py` | 설치기 opt-in 컴포넌트 단일 레지스트리. **이름을 대지 않으면 파일도 타이머도 생기지 않는다**(disabled가 아니라 absent). 멱등성은 기존 `build_plan`의 digest·활성타이머 대조에서 그대로 온다 |
| `install-managed` | verb | `automation/skill_store.py` | 관리형 스킬 전용 루트 설치 헬퍼 |
| `classify_save_request` | fn | `skills/doctype/scripts/doctype_routing.py` | 문서 저장 목적지 결정론 라우터 — 개인노트=Obsidian 단독 / 목적지 미지정=Drive 비공개 / 모호=clarify(fail-closed). `doctype_cli`가 mutation 직전에 강제(RTS-A) |
| `obsidian_write` | pkg | `automation/obsidian_write/` | Obsidian 승인형 쓰기 — RAG 미러와 **분리된 클론** + `-rw` 키, PARA 결정적 upsert → commit → push → 원격 read-back 해시 검증. 외부효과 게이트 바인딩(SI-5 개정) |
| `memory_routing` | pkg | `automation/memory_routing/` | ‘기억해’ 저장 분류·단일 흐름 — canonical=위키 노트(기존 wiki_gate 재사용), 짧고 안정적 사실만 MEMORY.md 병기, 임시 상태는 비영속, 모호하면 보수적 기본값 |
| `entity_preflight` | pkg | `automation/entity_preflight/` | 외부 쓰기 전 개인 고유명사 해석 — 관계 기반 질의 재작성 + 로컬 RAG·주소록 대조, 고신뢰 단일 후보만 자동 정규화, 모호하면 `ENTITY-CLARIFY`(승인 게이트 아님) |
| `todo` | skill | `skills/todo/scripts/todo_cli.py` | Google Tasks 쓰기의 리포측 소유자 — `gws_tasks_mutation` denylist 규칙로 게이트 경유, 등록 후 `tasks get` 재조회 검증 |

## CONVENTIONS (repo 고유)
- **stdlib 전용 지향.** `from __future__ import annotations` + `@dataclass(frozen=True, slots=True)` + 엄격 타입(`TypeAlias`/`Protocol`). 외부 의존은 함수 내부 lazy import + fail-closed 가드(참조 procurement `_import()`).
- **모든 외부효과(메일·캘린더·예산·배포·위키)는 소유자 승인 게이트 경유** — 직접 실행 금지. 승인 이모지 ✅/⛔ (아래 Owner-confirm 규칙).
- **"커밋됨 ≠ 배포됨".** 배포 판정은 `readlink /srv/autophagy-skills/live/<skill>` 해시.
- **상태 마킹은 성공 이후** — claim → 작업 → 성공 시에만 processed 기록, 실패 시 release.
- `fail-closed`가 반복 원칙 — 설정/권한/확인 불가 시 실행하지 않는다.
- `configs/rag/*` 하위 서비스는 `uv` + Ruff `ALL`(line-length 100) + basedpyright `all`. 메인 트리는 위 코드 스타일을 관찰로 강제(루트 매니페스트 없음).
- **vendored 하위 패키지도 격리 예외.** `skills/mail/vendor/mailon/`은 외부 리포(구 `orientpine/emailAutomation`)를 통합한 **무수정 vendoring** 소스로, 4개 서드파티(`pyotp`/`python-dotenv`/`beautifulsoup4`/`lxml`)를 `vendor/requirements.txt` 고정 버전 + 스코프 venv로 쓴다(메인 트리 stdlib-전용의 명시적 예외, RAG 하위서비스와 동일 취지). 소스는 byte-identical 유지 — 고칠 것이 있으면 upstream 성격에 맞게 vendor 소스를 직접 고치고 재배포하되, PROJECT_ROOT 상대 런타임 쓰기(data/logs) 규약은 건드리지 않는다. 런타임은 체크아웃 밖 `~/.hermes/mailon-runtime/`(위 「불변 시드」와 동일 이유). 상세: `docs/guide/기관메일-인터페이스.md`.
- **추적 config = 불변 시드.** 런타임 상태는 체크아웃 밖(~/.hermes/…, /srv/autophagy-private/…)에만 기록한다 — 추적 파일을 런타임에 mutate하면 ops 체크아웃이 dirty해져 git pull --ff-only / peer-attest sync가 막힌다. 선례: configs/mail-mode.default.json(시드) vs ~/.hermes/mail-triage/mail-mode.json(런타임). triage_mode 가드가 시드/체크아웃 경로 쓰기를 fail-closed 거부.
- **`managed-` 접두사 예약.** 관리형 스킬은 반드시 `managed-`로 시작하며, 일반 스킬 배포는 이 접두사를 사용할 수 없다.
- **충돌 시 우선순위 없음.** 일반 스킬과 관리형 스킬 이름 충돌 시 양방향 fail-closed 차단 — 소유자가 하나를 제거(`--remove`)해야 한다.

## ANTI-PATTERNS (금지)
- cron 워처가 Discord **메시지/첨부를 폴링** — 실시간 에이전트와 경쟁 소비자. 워처는 **리액션만** 폴링.
- 자식 subprocess에 자격증명 미전파(`env=` 누락) — no-agent cron은 os.environ에 시크릿을 안 넣으므로 명시 전파 필수.
- 채널·기능별 **병렬 confirm 워처/resolve 신설** — 기존 게이트에 `channel_id` 바인딩으로 재사용.
- 도메인 중첩 스킬에서 SKILL.md 라우팅에만 의존 — mutating 경로에 결정론적 코드 가드 필수(선례 calendar `94d625f`). **가드는 양방향이어야 한다**: 한쪽만 막으면 LLM이 두 경로를 동시 발동해 이중 쓰기(선례 사고 2026-07-20: `peer-test 오전 10시` 요청이 calendar 07-22 10:00 + coordination 07-29 09:00 이중 등록). calendar↔coordination는 공유 판정 `calendar_routing.classify_meeting_request`(calendar|coordination|clarify)로 정확-단일-시각=calendar / 피어+범위+조율의사=coordination / 모호=clarify(fail-closed)를 양쪽 CLI가 함께 강제한다.
- 토큰 모양 문자열(`Bot `, `sk-`, `ghp_`)을 **주석/문서에도** 사용 — secret-scan 오탐이 배포를 막는다.
- 프로덕션 게이트웨이에 `E2E_TEST_MODE=1` — production guard가 부팅 거부.

## COMMANDS
```bash
pytest tests/unit                     # 단위 테스트 (루트에서 실행, .pytest_cache)
ruff check .                          # 린트
automation/deploy-skill.sh <skill>    # 스킬 배포 (4단계 게이트; --sandbox-only / --request-only / --remove)
automation/healthcheck.sh             # 양 노드 read-only 헬스체크
python3 -m automation.rag_ingest      # 개인 RAG 인제스트
```

## NOTES
- 설계·작업분해의 단일 진실: `.omo/plans/autophagy-agents.md` (Work Plan). `docs/features.md`는 요약 뷰 + 아이디어 수집함.
- 배포는 오케스트레이터에서 `DEPLOY_SSH_HOST`(`<primary-node>`)로 SSH + `sudo -n -u agent|peer`.
- `logs/`는 git 미추적(디렉토리만 유지). `configs/rag/*/.venv`·`__pycache__`·`*_cache`는 소스 아님.
- `.git/hooks/`는 미추적 — clone마다 gitleaks pre-commit 훅 재설치 필요. `gitleaks` 바이너리가 PATH에 없으면 커밋이 fail-closed 차단된다(`docs/troubleshooting/gitleaks-setup.md`).


## 커뮤니케이션 언어 (사용자 지시, 2026-07-14)

**사용자(cha)에게 말하는 모든 내용(채팅 응답, 상태 보고, 질문, 요약)은 한국어로 작성한다.**

- 적용 대상: Atlas(오케스트레이터)가 cha에게 보내는 모든 메시지.
- 적용 제외: 코드, 커밋 메시지, 파일 내용, 서브에이전트에게 보내는 delegation 프롬프트,
  `.omo/plans/`·`.omo/notepads/`·`docs/` 등 산출물 자체의 언어는 이 규칙과 무관 — 기존
  계획 문서가 이미 한국어/영어 혼용이므로 각 문서의 기존 스타일을 따른다.
- 이 지침은 세션이 바뀌어도 유지되어야 하므로 repo 루트에 기록한다(사용자 요청: "지침에 추가해").

## Owner-confirm 이모지 컨벤션 (cha 지시, 2026-07-17)

모든 소유자 확정/승인 흐름(mail·budget·calendar·coordination 및 향후 추가되는 모든 confirm)은 동일한 이모지 리액션으로 처리한다:

- **✅ (U+2705, `:white_check_mark:`) = 승인 / 확정 / 실행**
- **⛔ (U+26D4, `:no_entry:`) = 거부 / 취소**

공통 규칙 (모든 confirm 게이트·워처):
- **소유자(cha) 리액션만 유효** — 봇/타인 리액션은 무시(reactor id == owner_id AND bot == false).
- **⛔ 우선** — ✅와 ⛔가 함께 있으면 취소로 처리한다(외부효과 fail-safe).
- **hash 바인딩 + fail-closed** — 대상 메시지가 드래프트 sha256을 참조해야 하며, 확인 불가/모호하면 실행하지 않는다.
- 확정 메시지에는 봇이 ✅·⛔를 미리 부착한다(cha는 탭 한 번). 텍스트 `실행/취소 <id>`는 하위호환 fallback일 뿐, 에이전트는 "실행 <id> 답장"류 텍스트 지시나 별도 DM을 보내지 않는다.
- 신규 confirm 워처(Hermes no-agent cron)의 배포 파일명은 `~/.hermes/scripts/` 안에서 **스킬별로 고유**해야 한다(예: `calendar_confirm_reaction_watch.py`) — 동명 파일 충돌 금지.
- **워처(cron)는 Discord 리액션만 폴링한다 — 메시지 텍스트/첨부의 수신·처리는 실시간 에이전트 단독 소유(경쟁 소비자 금지)**
- no-agent cron은 `~/.env.secrets` self-load + repo sys.path 래퍼 필수 — 상세: `docs/guide/watcher-cron-설계규약.md` — 워처가 자식 프로세스를 spawn할 때 자격증명을 자식 env에 명시 전달(자식 폴백 의존 금지, 규약 (b-2)).
- 도메인 중첩 스킬(calendar↔coordination 등)은 SKILL.md 라우팅만으로 부족, 양보 스킬의 mutating 경로에 결정론적 가드(등록 레지스트리 매칭, 소유자 제외, fail-closed)로 거부+안내(선례 calendar `94d625f`), 상세: `docs/guide/watcher-cron-설계규약.md`.
- **단일 게이트 절차 재사용 — 병렬 confirm 구조 신설 금지 (2026-07-19 mail compose 사후 반영)**: 신규 confirm 표면(owner DM 등)이 필요하면 해당 스킬의 **기존** 게이트를 재사용한다 — draft/pending 레코드·render·resolve·watch cron은 하나만 존재하며, 승인 채널은 레코드에 바인딩된 `channel_id` 필드로만 달라진다. 채널별·기능별로 별도 render/별도 resolve/별도 워처를 만드는 것은 금지. 선례: mail 스킬의 개인 서버 #approvals 회신 초안과 owner-DM compose 초안은 같은 `watch` cron·같은 `resolve_reaction`·같은 render 함수를 공유한다.

## 문서 갱신 규칙 (cha 지시, 2026-07-20)

**코드 작업은 관련 문서 갱신까지 마쳐야 종결된 것으로 간주한다.**

- 기능을 추가/변경/제거하면 **같은 커밋·배포 사이클에서** 해당 리포의 AGENTS.md·docs/·SKILL.md 중 낡아지는 문구를 함께 갱신한다.
- 적용 대상: autophagy-agents와 에이전트가 참조하는 모든 산출물 리포(emailAutomation 등). 에이전트는 문서를 사실 근거로 답하므로, 낡은 문서는 곧 소유자에게 잘못된 안내가 된다.
- 배경(사후 반영): 2026-07-20 mailon 보낸메일함 동기화가 배포됐으나 emailAutomation AGENTS.md의 "받은편지함만 지원" 문구가 남아, 에이전트가 기능이 없다고 잘못 안내함.

## 다른 세션 작업 덮어쓰기 방지 (cha 지시, 2026-07-21)

**작업 시작 전, 대상 영역의 최신 상태를 반드시 확인한다 — 특히 설계·아키텍처 결정.** 여러 세션/에이전트가 같은 repo에서 동시·연속으로 작업하므로, 내 기억이나 이전 세션의 이해는 이미 낡았을 수 있다.

- **설계·규약 문서를 수정하기 전에**: 해당 문서의 `git log`를 확인해 **최근 커밋이 다른 세션의 확정 결정인지** 살핀다. 최근에 그 주제를 바꾼 커밋이 있으면 그 커밋 메시지·diff를 읽어 **왜 그렇게 결정됐는지(rationale)** 파악한 뒤에만 손댄다. 내 이해와 충돌하면 덮어쓰지 말고 사용자에게 확인한다.
- **"내가 아는 설계"와 repo의 현재 상태가 다르면, repo가 진실이다.** 옛 이해로 최신 결정을 되돌리지 않는다. 사용자가 "최근 세션에서 해결했다"고 하면 반드시 그 커밋/세션을 먼저 찾아 확인한다(`git log --oneline -S <키워드>`, session 도구, 커밋 diff).
- **다른 세션의 미커밋 작업(dirty 워킹트리)은 되돌리지 않는다.** stash/reset/checkout/discard 금지 — 소유자 명시 승인이 있을 때만, 복원 가능한 방식(stash 보관 후 pop)으로 처리하고 작업 종료 시 반드시 복원한다.
- **배경(사후 반영)**: 2026-07-21, 한 세션이 확정·커밋한 Discord 2-서버 설계(893da68 — "모든 승인=개인 서버, peer attestation의 peer는 자기 두 번째 봇")를 다른 세션이 옛 이해("배포 승인은 공유 Lab")로 문서·config를 덮어써, 존재하지 않는 공유 Lab 채널을 가리켜 배포가 404로 실패했다. 최신 커밋을 먼저 확인했다면 예방 가능했다.

## 게이트웨이 재시동 규칙 (cha 지시, 2026-07-22)

**게이트웨이 재시동은 항상 agent와 peer를 포함한 모든 Hermes gateway를 함께 재시동한다 — 한쪽만 재시동 금지.**

- 기존 불변식 유지: healthy 서비스는 재시동하지 않고, 원인 확인 후에만 재시동한다. 이 규칙은 **재시동이 결정된 뒤의 범위**(항상 agent+peer 전 계정 세트, 향후 게이트웨이 계정 추가 시 그 계정도 포함)만 규정한다.
- 적용 대상: gateway를 재시동하는 모든 경로 — 수동 운영, 장애 대응, 재부팅 복구, 배포 스크립트(`automation/skill_generation/deploy.sh` 등). 상세 절차: `docs/guide/operations.md` §2 게이트웨이 재시동 규칙(incident-response.md·reboot-recovery.md 동기 갱신됨).
- caveat: 재시동 창 동안 진행 중인 peer attestation/승인 응답은 fail-closed 타임아웃될 수 있다 → 재시동 완료 후 재요청(차단 아님).
- LiteLLM(ops)·dashboard 등 다른 unit은 이 규칙 대상이 아니다 — Hermes gateway(agent·peer) 세트에만 적용.

## 기능 소개 문서 규칙 (cha 지시, 2026-07-25)

**기능(작업)이 완료되면, 완료된 기능을 소개하는 문서를 함께 만든다.**

- 위치: `docs/기능소개/<기능-slug>.md` (기능당 1개). 작성 후 `docs/features.md`의 해당 항목에서 링크한다.
- 길이: 1페이지 내외로 **간결하게**. 내부 구현 덤프가 아니라 "무엇을/왜/어떻게 쓰는가" 중심.
- 필수 포함:
  - **무엇을**: 이 기능이 무엇을 하는지 한두 문장.
  - **왜**: 어떤 문제를 해결하는지(기존 한계/장애).
  - **사용 시나리오**: 소유자(cha) 관점의 구체적 예시(입력→동작→결과). 최소 1개 happy-path + 실패/거부 경로가 있으면 함께.
  - **관련**: 스킬/티켓/핵심 파일 경로. 승인·게이트가 관여하면 명시.
- 민감정보 규칙 동일: 토큰·원문 로그·실제 문서 본문은 넣지 않는다(마스킹).
- 「문서 갱신 규칙」과 병행한다: 코드 완료 = 낡은 문구 갱신 + 이 소개 문서 작성까지가 종결 조건.
- 배경: 2026-07-25 cha 지시 — 완료 기능을 소유자가 바로 이해·사용할 수 있도록, 사용 시나리오 중심의 짧은 소개 문서를 남긴다.

## 커밋 규칙 (cha 지시, 2026-07-25)

**작업할 때는 단위 작업(logical unit)마다 커밋한다 — 여러 변경을 하나의 큰 커밋으로 몰지 않는다.**

- 하나의 논리적 변경(기능 추가·버그 수정·문서 갱신·리팩터 등)이 끝날 때마다 그 단위로 커밋한다. 서로 무관한 변경을 한 커밋에 섞지 않는다.
- 적용 대상: autophagy-agents에서 작업하는 모든 에이전트·세션. 커밋 메시지는 repo 기존 스타일(Conventional Commits: `feat(scope):`, `fix(scope):`, `docs(scope):` 등)을 따른다.
- 이유: 리뷰·되돌리기(revert)·이분 탐색(bisect)·다른 세션과의 병합을 쉽게 하고, "커밋됨 ≠ 배포됨" 판정과 프로덕션 핫픽스 유실 방지에 도움이 된다.
- 「문서 갱신 규칙」과 병행한다: 코드 단위 작업이 문서를 낡게 만들면 같은 단위·커밋 사이클에서 문서도 함께 갱신한다.

## 배포 provenance 규칙 (cha 지시, 2026-07-25)

**prod에는 `origin/main`에 있는 코드만 배포한다 — 미커밋·미푸시·뒤처진 체크아웃에서의 배포는 차단된다.**

- 모든 deploy 스크립트는 로컬 체크아웃의 파일을 prod로 복사한다. 병렬 세션 환경에서는 (a) 미커밋 파일 배포, (b) 커밋했지만 push 안 한 파일 배포, (c) origin/main보다 **뒤처진 체크아웃**에서의 배포가 쉽게 일어난다. 그러면 prod가 git에 없는 코드를 돌리고, 다음에 누군가 깨끗한 체크아웃에서 같은 스크립트를 돌리면 **말없이 되돌려진다**.
- 강제 수단: `automation/deploy_provenance.sh`의 `deploy_provenance_check <repo_root> <경로>...`가 각 파일의 워킹트리 blob 해시와 배포 기준(`origin/main`)의 blob 해시를 대조해 불일치면 `DEPLOY-BLOCK`으로 중단한다. 신규 deploy 스크립트는 **반드시** 이 가드를 호출해야 한다.
- **디렉터리 인자는 tracked 대조만으로 부족하다 (2026-08-01 보강).** 호출자는 디렉터리를 통째로 `tar`/`rsync`로 보내는데 가드는 `git ls-files`(tracked)만 열거했다 — 그래서 디렉터리 안의 **untracked 새 파일**이 `OK`를 받고 prod로 실려, 커밋·`origin/main`·PR을 **전부 우회**했다(실측: `automation/hermes_compat`에 untracked `.py` 1개 → `OK: 17 file(s) match origin/main`, rc 0). 이제 디렉터리 인자는 `ls-files --others --exclude-standard`로 untracked 비-ignored 파일을 함께 검사해 있으면 경로를 적시하고 `DEPLOY-BLOCK`한다. `--exclude-standard`는 의도적이다 — `.gitignore`가 비소스로 선언한 잔여물(`__pycache__`·`.venv`)까지 막으면 모든 실배포가 깨진다. **디렉터리 인자 호출자에는 `deploy-skill.sh`(스킬 마운트 경로)도 포함**되므로 이 보강은 최고 권한 경로에도 적용된다.
- 예외(의도적): `deploy-skill.sh --sandbox-only` / `--activate-managed` / 내부 staged-source용 `SKILL_SRC_DIR` 지정, 그리고 워킹트리를 통째로 rsync하는 `regression_bank/deploy_lab_node.sh`(푸시 전 테스트가 목적). 개인 저작은 이 우회를 쓰지 않고 `--personal <name>`이 별도 `personal_provenance_check`(독립 repo의 clean·committed HEAD + untracked 비-ignored 부재 + 승인 SHA 바인딩)를 통과한다.
- 탈출구 `DEPLOY_ALLOW_UNPUSHED=1`은 **샌드박스/실험 전용**이다. 배포를 통과시키려고 상습적으로 쓰면 가드가 무의미해진다 — 올바른 순서는 상시 "커밋 → 푸시 → 배포"다.
- 배경(사후 반영): 2026-07-25 mail 승인 워처 수리 중, 수정된 `mail_digest_watch.py`가 미커밋 상태로 prod에 배포돼 다른 세션의 배포 한 번으로 유실될 수 있는 상태였다. 같은 계열의 선례: 2026-07-21 옛 체크아웃이 최신 Discord 2-서버 결정을 덮어써 배포가 404로 실패.

## 작업 종결 규칙 (cha 지시, 2026-07-25)

**작업이 끝나면 커밋 → 푸시 → 배포까지 마쳐야 종결이다 — "코드만 고쳐두고 보고"는 미완이다.**

- 순서는 항상 **커밋 → 푸시 → 배포**다. 「배포 provenance 규칙」의 가드가 미푸시 코드의 배포를 차단하므로 순서를 바꾸면 배포가 막힌다. 이 순서 자체가 프로덕션 핫픽스 유실 방지 장치다.
- 배포 대상 판정: 스킬 = `automation/deploy-skill.sh`(4단계 게이트), 워처 = 각 `deploy.sh`, ops 체크아웃에서 실행되는 코드(`/srv/autophagy-agents/automation/**`) = ops 계정 `git pull --ff-only`. 배포할 것이 없는 작업(문서·테스트만)은 커밋·푸시로 종결한다.
- 배포 후 **검증까지가 종결 조건**이다: live 심링크 해시 / 배포본 파일 해시 / 실제 실행(cron tick·import·smoke) 중 해당하는 증적을 남긴다("커밋됨 ≠ 배포됨").
- 소유자 승인이 필요한 배포는 승인 요청 게시까지 진행하고, cha의 ✅ 이후 남은 단계(MOUNT·검증·kanban 종결)도 같은 세션에서 마무리한다.
- 다른 세션의 미커밋 작업·로컬 전용 커밋은 절대 함께 커밋하지 않는다. 로컬 브랜치가 갈라져 있으면 `origin/main` 기반 worktree에서 **내 변경만** 커밋·푸시한다(선례 2026-07-25).

## 승인 메시지 단일성 규칙 (cha 지시, 2026-07-25)

**승인 게이트는 동일한 논리적 요청에 대해 이미 게시된 메시지가 있다면 이를 조용히 덮어쓰거나 중복 게시하지 않는다.**

- **금지**: (1) 동일한 `action_hash`를 가진 메시지를 중복 게시하는 것, (2) 저장된 `message_id`를 새 메시지 ID로 덮어써서 이전 메시지를 고아(Orphan)로 만드는 것.
- **절차**: 모든 승인 요청은 `automation/interop/approval_lifecycle.py` 파사드를 경유한다. 파사드가 lease 점유 → 상태 probe(6종 타입) → supersede(delete 후 record drop) → journal → 게시 → 커밋까지 임계구역 전체를 소유한다. 소유자가 이미 ✅/⛔를 누른 요청은 건드리지 않고 워처에게 양보한다(DEFER).
- **강제**: 산문만으로는 부족하다(이 문서의 원칙: 「mutating 경로에 결정론적 코드 가드 필수」). `tests/unit/test_approval_lifecycle_conformance.py`가 승인 producer 인벤토리를 들고 파사드 경유 여부를 기계적으로 검증한다. 예외는 소스 주석이 아니라 테스트 내 `_EXEMPT` 맵에 사유와 함께 등록해야 한다.
- **참조**: 상세 규약은 `docs/guide/watcher-cron-설계규약.md §(j)`, 구현 불변식과 금지사항은 `automation/interop/AGENTS.md`에 있다.
- **배경(사후 반영)**: 2026-07-25 drive-archive digest 중복 게시 및 message_id 덮어쓰기로 인해 소유자의 승인 ✅가 실종된 결함을 수리하며 도입되었습니다.
## 후속 과제 기록 규칙 (cha 지시, 2026-07-26)

**작업 중 발견했으나 이번 범위에서 처리하지 않은 사항은, 요청 사항을 마무리한 뒤 `docs/features.md`에 후속 과제로 기록한다 — 기록까지 마쳐야 작업이 종결된다.**

- **왜**: 후속 과제는 작업 과정에서만 발견된다. 제때 기록하지 않으면 QA 증적이나 세션 로그 속에 묻혀 사라지며, 세션 종료 후에는 그 맥락을 아는 주체도 없어진다.
- **어디에**: **`docs/follow-ups.md`** (2026-08-03 분리 — features.md가 103KB까지 부풀어 무엇이 남았는지 안 보였다. 현황판에는 묶음별 잔량 요약표만 둔다)(2026-07-29 분리 — PLAN의 "신규 아이디어"는 이제 진짜 새 기능 아이디어 전용이다). 기능/작업 단위로 **묶음 항목 1개 + 하위 불릿**으로 작성하고, 상세 증적 경로(`docs/qa/<wave-id>/...`)는 묶음 끝에 한 번만 병기한다. 계획 문서에 이미 반영된 건은 wave ID를 병기하여 PLAN "개발 예정"으로 옮긴다(features.md 사용법 규칙 준수).
- **무엇을 적나**: 각 불릿은 **"무엇이 문제인지 → 어떻게 조치할지"** 형태로 작성하며, **영향 범위와 심각도**를 명시한다(예: "보안 문제 아님 — 라이브 readlink가 권위 소스", "검증 메시지 오류일 뿐 마운트 로직과 무관"). 판단 근거를 남기지 않으면 다음 작업자가 처음부터 다시 조사해야 하는 낭비가 발생한다.
- **즉시 수정 vs 후속 과제**: 요청 범위를 벗어나거나, 안전 불변식을 위반하거나, 프로덕션 동작을 저해하는 결함은 **후속으로 미루지 않고 즉시 수정한다**. 후속 과제로 남기는 것은 (a) 동작은 정상이나 개선 여지가 있는 사항, (b) 범위 밖의 인접 결함, (c) 설계 판단이 필요하여 별도 논의가 필요한 사항에 한한다.
- **없으면 적지 않는다**: 형식을 채우기 위해 빈 항목이나 억지스러운 항목을 만들지 않는다.
- 「작업 종결 규칙」·「문서 갱신 규칙」·「기능 소개 문서 규칙」과 병행한다: 커밋 → 푸시 → 배포 + 낡은 문구 갱신 + 기능 소개 문서 + **후속 과제 기록**까지가 한 사이클의 종결 조건이다.
- **배경(사후 반영)**: 2026-07-26 관리형 스킬 채널(MS) 라이브 롤아웃에서 실제 결함 7건이 발견되어, 프로덕션을 저해하는 3건은 즉시 수정하고 나머지 4건은 후속으로 남겼다. 그러나 해당 4건이 초기에는 QA 증적 내에만 머물러 있어, 소유자가 후속 과제 문서화 여부를 확인하지 않았다면 유실될 위험이 있었다.

## ops 체크아웃 단방향 규칙 (cha 지시, 2026-07-27)

**노드의 배포 체크아웃(`/srv/autophagy-agents`)은 `origin/main`의 단방향 거울이다 — 그 안에서 커밋을 만들지 않는다.**

- 「배포 provenance 규칙」이 **밖으로 나가는** 코드를 통제한다면(=origin/main에 없는 코드는 배포 금지), 이 규칙은 **반대 방향**을 막는다: **배포 체크아웃이 코드의 출처가 되어서는 안 된다.** 그 안에서 허용되는 쓰기는 `git fetch` / `git pull --ff-only`뿐이며, 편집·`git add`·`git commit`·`git stash`는 금지다. 고칠 것이 있으면 개발 체크아웃에서 커밋 → 푸시 → 배포한다.
- **에이전트가 런타임에 스스로 학습한 내용도 예외가 아니다.** 소유자와의 실제 작업에서 나온 진짜 지식이라도 배포 체크아웃에 직접 커밋하면 위험이 이중이다 — (a) git에 없는 코드가 되어 다음 정렬 때 **유실**되고, (b) 그때까지 **모든 세션의 `git pull --ff-only`가 막혀** 배포 파이프라인이 선다. 「추적 config = 불변 시드」와 같은 이유로, 런타임에 생기는 것은 체크아웃 밖(`~/.hermes/…`)에 쓰거나 개발 경로로 되돌려 보낸다.
- **커밋은 반영이 아니다 — 에이전트에게도 똑같이 적용된다.** 스킬 문서를 고쳐 커밋해도 마운트본은 그대로이므로, 배포(소유자 ✅) 전까지 그 변경은 **조용히 무효**다. ff-pull 차단은 시끄럽게 실패하지만 이쪽은 아무 신호도 없어 발견이 늦는다 — 에이전트는 배웠다고 여기지만 실제로는 구버전이 돈다. 판정은 언제나 `readlink /srv/autophagy-skills/live/<skill>`("커밋됨 ≠ 배포됨").
- **규칙은 산문이 아니라 훅으로 강제된다.** `automation/bootstrap-accounts.sh`의 `install_commit_refusal_hook`이 배포 체크아웃의 `.git/hooks/pre-commit`에 **모든 커밋을 무조건 거부**하는 훅을 설치한다(멱등 — `install`이 단일 경로를 교체하므로 재실행해도 훅은 정확히 1개). 스캔하지도 판단하지도 않는다 — 조건부 거부는 "예외를 아는 사람"에게만 통하는데, 사고를 낸 주체는 언제나 예외를 모르는 쪽이었다. 거부 메시지는 개발 체크아웃에서 커밋하는 방법과 비파괴 복구 절차(format-patch→am)를 함께 안내한다. `git fetch`/`git pull --ff-only`는 커밋을 만들지 않으므로 영향이 없다(회귀 고정: `tests/unit/test_deploy_checkout_commit_refusal.py`). 이것은 거부(구조)와 탐지(아래 프로브)의 2중 방어다 — `--no-verify`로 훅을 뚫어도 프로브가 잡는다.
- **탐지는 운에 맡기지 않는다.** `automation/healthcheck.sh`의 `checkout_mirrors_origin` 프로브가 배포 체크아웃이 origin/main과 어기나는지를 확인해 FAIL·수리 티켓을 낸다. **검증은 체크아웃이 로컬인 cron 호스트(`<primary-node>`)에서 로컬로 돌리므로 ssh도 sudo도 쓰지 않는다** — 원건 이전엔 SSH allowlist와 중첩 sudo가 둘 다 rc=126으로 프로브를 죽였다(2026-07-29 실측). 검증은 세 가지다: 추적 파일 미커밋 변경(dirty), HEAD가 로컬 origin/main의 조상이 아님(ahead=로컬 커밋), 그리고 **origin보다 뒤처짐(behind)** — 뒤처짐은 `git ls-remote`(네트워크 읽기지만 로컬 ref를 쓰지 않음)로 진짜 origin과 대조해 잡는다. **뒤처짐의 심각도는 프로덕션이 무엇을 돌리는가로 채점한다**(DG-6 정합): 릴리스가 `origin/main`에 있으면 관측소만 지연된 것이므로 PASS + `MIRROR-BEHIND-WARN`, 릴리스가 낡았으면 `release-stale`로 FAIL, 릴리스가 없으면(롤백 형태) 미러가 곧 프로덕션이므로 FAIL이다 — 단 **아직 수렴 중인 드리프트는 유예한다**(`RELEASE-CONVERGING-WARN`). 그 판단은 우리가 새로 내리지 않고 리컨실러의 상태 파일을 읽어 위임한다 — 그쪽이 이미 `DRIFT_NOTICE_SECONDS`(600초)·`FAILURE_NOTICE_THRESHOLD`(3회)로 "이게 사건인가"를 소유자에게 알릴지 결정하고 있으므로, 같은 질문을 두 곳에서 따로 판정하면 방금 고친 그 어긋남이 릴리스 쪽으로 옮겨갈 뿐이다. 유예는 fail-closed다: 상태 파일이 없거나·깨졌거나·리컨실러가 자기 임계값만큼 침묵하면 그대로 FAIL이다(침묵이 조용함을 사지 못하게 한다 — 2026-08-02 실측) — 조용해지려고 탐지를 내주지 않는다(`release == origin/main`을 보는 전용 프로브가 없어 낡은 릴리스는 오직 여기서만 보인다). dirty/ahead는 모드와 무관하게 FAIL이다. 그래도 **프로브는 `git fetch`를 안 한다** — read-only 불변식은 유지된다(origin 불통은 `BEHIND-UNKNOWN`으로 degrade). `logs/` 같은 미추적 파일은 오탐이 아니다. 티켓은 체크 이름만 실지 않고 비파괴 복구 절차를 함께 실는다(`checkout_mirror_guidance`: ahead=`format-patch`, behind=`land.sh`). 프로브·채점·가이드는 `automation/checkout_mirror_probe.sh`에 분리되어 land.sh와 공유된다 — healthcheck는 **탐지**, land.sh와 리컨실러는 **수렴**이며, land.sh는 그 함수를 노드에 값으로 실어 보낸다(`declare -f`; 노드 사본을 source하면 방금 경고로 낮춘 그 미러의 셸을 ops로 실행하게 된다 — 그래서 실리는 3개 함수는 자기완결적이어야 하고, 채점 헬퍼를 거기서 호출하면 노드에서 "command not found"가 된다). **push+노드 동기화는 `automation/land.sh` 하나로 수행해 수동 2단계의 망각을 없앤다**. 상세: [소개](docs/기능소개/배포-체크아웃-지연-감지.md)·[소개](docs/기능소개/단일-랜딩-명령.md), 증적 `docs/qa/DG-1/summary.txt`.
- **수렴도 사람에게 맡기지 않는다.** 릴리스 수렴은 detached 스냅샷 워크트리에서 이뤄져 미러의 HEAD를 설계상 건들지 않는다. 그래서 예전엔 사람이 `land.sh`를 돌릴 때만 미러가 따라왔는데, **브랜치 작업은 PR 머지로 main에 도달해 `land.sh`를 거치지 않는다** — 즉 아무도 되돌리지 않았고, 헬스체크는 매 5분 같은 실패를 반복했다(실측 2026-07-29~08-03 `mirror-behind` 447회). 이제 2분 리컨실러가 같은 틱에서 미러를 `git pull --ff-only`로 따라오게 한다(`sync_mirror`) — 단, **판정이 정확히 `mirror-behind`일 때**와 **프로덕션이 이미 `origin/main`에 도달한 뒤**에만. dirty/ahead는 절대 건들지 않고(그 작업은 다른 어디에도 없다), 릴리스가 낡은 동안에는 미러의 지연이 그 사실의 유일한 증거이므로 일부러 남긴다. 이것은 이 규칙의 예외가 아니다 — `pull --ff-only`는 위에서 허용한 바로 그 쓰기다. 상세: [소개](docs/기능소개/관측-미러-자동-수렴.md).
- **DG-5 이후 이 체크아웃은 런타임이 아니다 — 관측소다.** cron 워처·마운트 스킬·`peer_attest`·`deploy-skill.sh`는 공용 리졸버로 불변 릴리스 `/srv/autophagy-agent-current`를 먼저 찾는다([소개](docs/기능소개/불변-런타임-릴리스.md)). 그래서 **`land.sh`의 계약은 노드 상태에 따라 갈린다**: 릴리스가 살아 있으면 미러의 dirty/ahead는 `LAND-MIRROR-WARN`으로 낮추고(그 상태가 프로덕션을 좌우하지 않으므로 — 무관한 랜딩을 거부하던 것이 원래 사고였다) 하드 사후조건은 **릴리스가 방금 push한 sha에 도달했는가**로 옚긴다. `current`를 지워 롤백한 상태(폴백 모드)에서는 미러가 다시 프로덕션이므로 **옛 하드 계약이 그대로 되살아난다**. 끊어진 심링크처럼 **깨진 `current`는 부재가 아니라 손상**이라 랜딩이 거부된다 — `[[ -e ]]`가 거짓이라는 이유로 릴리스 노드를 낡은 미러로 조용히 강등하면 안 된다. 이 규칙(커밋 금지·ff-pull만 허용)은 모드와 무관하게 유지된다.
  - **예외 1건(2026-08-10 시점)**: `autophagy-repair-approval-watch.service`는 system 스코프 유닛이라 DG-5의 `--user` 이관 절차가 닿지 않았고, 라이브 설치본이 여전히 미러(`/srv/autophagy-agents`)를 WorkingDirectory·PYTHONPATH·ExecStart로 쓰고 있었다 — 즉 이 한 유닛에 한해서는 미러가 아직 런타임이었다. 2026-08-10의 별도 system-scope 수렴 작업으로 해소되며, 그 전에는 위 문단을 이 유닛에 적용하지 않는다(근거: `.omo/notepads/repair-report-rollout/decisions.md`).
- **복구는 파괴적으로 하지 않는다.** 앞서 있는 커밋은 `git format-patch` → 개발 체크아웃에서 `git am`으로 **작성자·타임스탬프를 보존한 채** main 위에 올리고, blob 바이트 동일성을 확인한 뒤 체크아웃을 정렬한다. 되돌림 대비 ref를 노드에 남긴다(선례 `785eb34`/`67fd9e9`, 유실 0건). `reset --hard`로 먼저 맞추는 것은 학습분을 지우는 행위다.
- **배경(사후 반영)**: 2026-07-27, 노드의 agent 계정이 `skills/mail/SKILL.md`(세미나 출장 신청 안내 표준, v1.5.3→1.5.5)를 배포 체크아웃에 직접 커밋해 prod가 git에 없는 코드를 들고 있었고, ops가 origin보다 2커밋 앞서 모든 ff-pull이 막혔다. 동시에 그 학습분은 배포되지 않아 마운트본은 구버전이었다 — 두 실패가 한 원인에서 나왔다.

## 수리 반영 경로 규칙 (cha 지시, 2026-07-29)

**수리 에이전트는 배포 체크아웃에서 커밋하지 않는다. 전용 작업 클론에서 작업해 `repair/t_<ticket>` 브랜치로 push하고, 그 브랜치→main PR까지 에이전트가 생성한다. main 머지는 cha가 GitHub에서 한다.**

- **작업 위치**: 수리의 apply·commit·패치문서·회귀시나리오 등록은 전용 작업 클론에서만 수행한다. 배포 체크아웃(`/srv/autophagy-agents`)은 「ops 체크아웃 단방향 규칙」그대로 `git fetch`/`git pull --ff-only`만 허용된다. 샌드박스 단계는 이미 `git clone --shared`로 격리돼 있으므로(`repair_ops_adapters.py`) 그대로 둔다.
- **자격증명**: 저장소 **한정** write deploy key를 사용한다(fine-grained PAT 아님 — 범위가 더 좁기 때문). 배포 체크아웃의 ops 키는 **계속 read-only**로 둠 — 두 키를 섞지 않는다. 키는 `/srv/autophagy-private/repair_push_key`(ops:600)에 둔다 — **홈에 두면 안 된다**: 두 수리 유닛이 `ProtectHome=yes`라 `/home`이 서비스에게 빈 디렉터리로 보이고, 파일이 디스크에 멀쩡히 있는데도 런타임에만 "키 없음"으로 실패한다(회귀 고정: `tests/unit/test_repair_push_key_sandbox.py` — 유닛 파일에서 제약을 역산하므로 `ProtectHome`이 바뀌면 테스트도 따라 바뀐다). 호스트 키도 같은 이유로 `/srv/autophagy-private/repair_known_hosts`에 **고정**한다 — ssh는 `~/.ssh/known_hosts`를 passwd 엔트리로 해석하므로 `ProtectHome`이 가리고, 노드에 `/etc/ssh/ssh_known_hosts`도 ssh_config 전역 설정도 없어 유닛 안에서는 검증할 DB가 아예 없다(실증: 호스트 키 DB를 끊으면 `ls-remote`가 실패). `accept-new`로 우회하지 않는다 — write 자격증명이 오가는 유일한 경로에서 아무 키나 신뢰하게 되기 때문이며, 파일이 없으면 역시 exit 4다. 경로는 `REPAIR_PUSH_KEY`로 덮어쓸 수 있고, 키가 없으면 **push를 시도하지 않고 exit 4로 실패**한다 — 조용히 ops read-only 키로 폴백하면 진짜 원인에서 먼 곳에서 실패하기 때문.
- **push 대상은 브랜치뿐**: `repair/t_<ticket>` 패턴으로만 push한다. **`main` 직접 push 금지**, 자동 ff-머지도 금지.
- **PR 생성까지가 에이전트 종착점 (cha 지시, 2026-07-31)**: 브랜치를 push했으면 에이전트가 곧바로 `repair/t_<ticket>`→`main` PR을 `gh pr create --base main --head repair/t_<ticket>`로 생성한다(제목·본문은 커밋 스타일; 본문에 티켓 id·수정 요약·검증 증적, 실수신자/본문 등 민감정보 마스킹). **push만 하고 멈추지 않는다** — cha에게는 GitHub에서 브랜치 diff를 눈으로 확인해 Merge 버튼을 누르는 트리거가 필요하고, PR이 없으면 그 트리거 자체가 없다(2026-07-31 선례: 브랜치만 push되고 PR이 없어 cha가 머지할 방법이 없었다). 이미 열린 PR이 있으면 새로 만들지 않고 그 PR을 재사용한다(push만으로 헤드가 갱신됨). `gh` 미설치·미인증이면 설치·인증 후 진행하고, 그래도 불가하면 PR을 만들 수 없음을 명시해 cha에게 알린다.
- **main 머지 주체 = cha**: cha가 GitHub PR에서 diff를 확인하고 Merge 버튼으로 머지한다. 이것이 자동화의 의도된 종착점이다 — PR까지 자동이면 급한 불은 이미 꺼지고, main 반영만 사람 눈을 거친다. **에이전트는 main에 직접 머지·push하지 않는다.** 머지 후 노드는 평소대로 `pull --ff-only`로 받고, 에이전트는 반영을 확인한 뒤 kanban 티켓 완료 처리까지 이어간다.
- **승인은 패치 내용에 바인딩된다 (완료, 2026-07-29)**: `action_hash`는 `sha256:<canonical(ticket_id, patch_name, 패치 바이트 sha256, 변경파일·라인증감 요약)>`이고, 승인 DM은 변경 파일 목록·라인 증감·`patch_sha256`을 실는다. 패치 본문은 여전히 Discord에 노출하지 않고 `/srv/autophagy-private/`의 경로만 안내한다. 적용 게이트는 적용 직전 디스크의 패치로 해시를 재계산해 소유자가 실제로 승인한 해시와 대조하므로, **승인 뒤 `patch.diff`가 바뀌면 적용되지 않는다**. 불일치는 `False`가 아니라 **예외**로 표면화한다 — `_run`은 `AWAITING_APPROVAL`에도 exit 0을 돌려주고(`repair_ops_cli.py:174`) 워처는 자식이 0이면 레코드를 회수한 뒤 approvals 로그에 `approved`를 남기기 때문에, `False`로 거부하면 아무것도 적용하지 않은 채 감사 기록만 생긴다. 구스키마(이름만 해싱) 레코드는 읽힐 수는 있으나 **인가하지 못한다**; 소유자가 반응하지 않은 것은 새 요청이 교체하고, 이미 누른 것은 파괴하지 않고 24h TTL로 정리된다. 승인 메시지 렌더러 **v1은 동결**이다 — 이미 게시된 메시지는 저장 레코드의 재렌더와 정확히 일치해야 하며, 문구를 바꾸려면 v3을 만든다(2026-07-29 `5ef869d` 선례). 구현 `automation/repair/repair_patch_{diff,binding}.py`·`repair_approval_render.py`, 상세 [소개](docs/기능소개/수리-승인-내용-바인딩.md), 증적 `docs/qa/RTS-4/r2-content-binding.txt`.
- **롤아웃 순서(완료, 2026-07-29)**: ① apply 대상을 작업 클론으로 이관(`08a221d`) → ② systemd `ReadWritePaths`에서 `/srv/autophagy-agents` 제거(`7ea6a8c`) → ③ 브랜치 push 추가(`b86e1df`, 키 경로 수정 `d30b4a1`) → ④ 배포 체크아웃 커밋 거부 훅 롤아웃(노드 설치 완료). 순서를 바꾸면 훅이 수리 apply 경로를 깨뜨리므로, 재구축 시에도 이 순서를 지킨다. ④ 증적: 배포 체크아웃에서 `git commit --allow-empty`가 exit 1로 거부되고 HEAD 불변, `git fetch`·`git pull --ff-only`는 정상 동작.
- **선행 충돌 주의**: `.omo/plans/repair-report-{core,rollout}.md`(타 세션, 미추적 상태)가 `repair_ops_core.py`·`repair_core.py`·`automation/interop/report.py`·**`automation/repair/systemd/*`(예외 없음)**·일부 승인 흐름의 변경을 금지하고 있다. 위 롤아웃 ②는 systemd 유닛을 건드리므로 **해당 계획과 순서를 조율한 뒤** 진행한다. 먼저 손대면 다른 세션의 확정 설계를 덮어쓰게 된다(2026-07-21 선례).
- **배경**: 2026-07-28 드리프트 복구 중, 배포 체크아웃의 미병합 커밋을 생산한 주체가 사람이 아니라 **수리 자동화**임이 드러났다(`repair_ops_cli.py:59` 기본값 `REPAIR_CHECKOUT=/srv/autophagy-agents`, `repair_ops_adapters.py:233-244,272`의 `git commit`/`--amend`/`revert`, 유닛의 `ReadWritePaths`). ops deploy key가 read-only라 그 커밋들은 origin에 도달할 수 없어 — 프로덕션이 git에 없는 코드를 돌고, 다음 정렬 때 유실되며, 그전까지 모든 세션의 ff-pull이 막힌다. 즉 즉시 반영이라는 장점을 유실·파이프라인 정지로 지불하는 구조였다.

## 세션 워크트리 규칙 (cha 지시, 2026-08-03)

**세션마다 워크트리를 하나 만들어 그 안에서 작업한다 — 여러 세션이 한 워킹트리를 공유하지 않는다.**

- **왜**: 워킹트리를 공유하면 같은 파일을 동시에 만진다. 2026-08-03 실측 — `docs/features.md`를 두 세션이 함께 편집해 한쪽이 남의 묶음 헤더를 덮어썼고, 커밋 스테이징에도 남의 미완성 편집(문장이 끊긴 채로)이 섞였다. 「다른 세션 작업 덮어쓰기 방지」·「커밋 전 diff 확인 규칙」이 사람의 주의로 막던 것을 구조로 막는다.
- **비용은 작다**: 추적 파일 14MB, 생성 0.1초(실측). gitignore된 대용량 잔여물(`configs/rag/*/.venv` 5.5GB)은 따라오지 않으므로, **그것이 필요한 작업은 메인 체크아웃에서** 한다.
- **훅·가드는 그대로 작동한다**: 워크트리는 `.git/hooks`를 공유하므로 gitleaks pre-commit이 그대로 돈다(고엔트로피 토큰 실제 차단 확인). provenance 가드와 전체 테스트도 워크트리에서 통과한다(3012 passed). `.git`을 디렉터리로 가정하는 6곳은 전부 노드측 경로(미러·배포 체크아웃·전용 클론)라 개발 워크트리는 도달하지 않는다.

```bash
automation/worktree.sh start <이름> [--paths <손댈 경로>...]   # 세션 시작
automation/worktree.sh finish <이름>                          # 세션 종료
```

**시작 전에 기반이 최신인지 확인한다 (cha 지시)** — 낡음에는 세 종류가 있고 각각 다른 곳에서 잡힌다:

- **① 로컬 `origin/main` ref가 낡음** → `start`가 fetch를 **강제**하고, 실패하면 시작하지 않는다. 캐시된 ref를 조용히 쓰는 것이 정확히 2026-07-21 사고(옛 체크아웃이 최신 결정을 덮어써 배포 404)의 모양이기 때문에 경고가 아니라 거부다. 따라잡은 커밋 목록을 출력해 **무엇을 놓치고 있었는지** 보여준다.
- **② 작업 중 origin/main이 전진** → 시작 시점에는 못 잡는다(2026-08-03 하루에 3회 전진). PR 시점의 mergeable 판정과 `finish`의 fetch가 잡는다.
- **③ 손댈 파일을 다른 세션이 방금 바꿈** → ref가 최신이어도 모른다. `--paths`를 주면 그 경로의 최근 커밋과 **메인 체크아웃의 미커밋 변경**을 함께 보여준다. 「다른 세션 작업 덮어쓰기 방지」가 요구하는 확인을 기억이 아니라 출력으로 만든다.

**종료 시 착지 여부를 확인한다** — `finish`는 미커밋 변경이나 `origin/main`에 착지하지 않은 커밋이 있으면 **제거를 거부**한다. 2026-08-03 실측: 이미 머지된 PR의 브랜치로 증적을 push해 브랜치에는 올라갔으나 착지하지 못했고(push가 성공하므로 아무 신호도 없다), 정리 직전 이 확인이 QA 증적 65줄을 살렸다.

**머지된 브랜치에는 다시 push하지 않는다** — 새 작업은 새 브랜치. 2026-08-03에 두 번 어겼고 한 번은 유실 직전까지 갔다. `start`가 이름·브랜치 중복을 거부하는 것이 이것을 돕는다.

**원격 브랜치는 자동으로 지우지 않는다** — 공유 영향이라 사람이 판단한다(회귀로 고정: `tests/unit/test_session_worktree.py`).

## 커밋 전 diff 확인 규칙 (cha 지시, 2026-07-29)

**`git add` 전에 반드시 `git diff --stat`(필요시 `git diff`)로 의도한 파일만, 의도한 방향으로 바뀌었는지 확인한다. 설명되지 않는 삭제나 내가 만들지 않은 변경이 섞여 있으면 그대로 커밋하지 않는다.**

- **왜 별도 규칙인가**: 「다른 세션 작업 덮어쓰기 방지」는 *내가 남의 것을 되돌리는* 방향을 막는다. 이 규칙은 **반대 방향**을 막는다 — *남이 되돌린 워킹트리를 내가 모른 채 커밋해 그 삭제를 굳혀버리는* 경우다. 병렬 세션·노드 에이전트가 같은 체크아웃을 공유하므로, 내가 고친 파일만 `git add` 해도 **그 파일 안에 남의 되돌림이 이미 들어 있을 수 있다**.
- **무엇을 보나**: ① 변경 파일 목록이 내가 손대길 의도한 집합과 일치하는가. ② **삽입/삭제 줄 수의 방향**이 맞는가 — 문서를 추가했는데 순삭제(예: `+9/-20`)면 즉시 멈춘다. ③ 문서·규칙 파일이면 삭제된 줄을 직접 읽는다. ④ `version:` 문자열이 **낮아지는** 변경은 거의 항상 되돌림이다(배포본과 대조할 것).
- **발견 시 조치**: 되돌림으로 판정되면 커밋하지 말고 `git show origin/main:<path>`로 정본을 대조해 복원한다. **이미 커밋된 정본을 되찾는 복원**(`git checkout -- <path>`)은 「미커밋 작업 보존」 원칙과 충돌하지 않는다 — 반대로 남의 **생산물**을 지우는 방향이면 지우지 말고 소유자에게 묻는다.
- **push 전에 한 번 더**: `git diff --stat <직전 정상 커밋>..HEAD`로 삭제가 0이거나 의도한 것뿐인지 확인한다. push 전에 잡으면 복구가 쉽고, 놓치면 다른 세션이 그 상태를 기준으로 작업해 유실이 번진다.
- **배경(사후 반영)**: 2026-07-29 한 세션에서 두 번 발생했다. (1) `docs/features.md` 편집 시 워킹트리에 이미 되돌림이 섞여 있었고 확인 없이 커밋해 **후속 과제 4건이 유실**됐다(`373a0fa` — 문서를 더하는 커밋이 `+9/-20`이었는데 그 신호를 놓쳤다). `7ea6a8c` 정본 기준 재구성으로 복구했다(`46c82de`, 삭제 0 확인). (2) 직후 검증에서 `AGENTS.md`의 「수리 반영 경로 규칙」 18줄·CODE MAP 5행 삭제, 기능소개 문서 삭제, `doctype/SKILL.md`의 **v1.3.1→v1.2.2 후퇴**(배포본과 불일치), 코드 8건 후퇴로 테스트 10건 실패가 관측됐다 — 모두 미커밋 상태여서 origin 정본으로 복원했다.
