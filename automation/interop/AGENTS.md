# automation/interop/ — 외부효과 게이트 + 봇간 인터롭 코어

프로젝트에서 가장 안전-임계인 경계. 규약 단일 진실은 [docs/guide/interop-규약.md](../../docs/guide/interop-규약.md).
모든 mutating 툴콜은 여기를 통과해야 하며, 원칙은 **fail-closed + 해시 바인딩**.

## 파일 지도
| 파일 | 역할 |
|------|------|
| `external_effect_gate.py` | `evaluate_tool_call` — denylist(`configs/external-effect-tools.yaml`) 매칭. 미매칭=읽기 허용. 매칭(mutation)=owner 승인 레코드 필요. `action_hash=sha256(정규화 payload)`, method는 `manual_reaction` 또는 `signed_injection_e2e`만 |
| `approval_types.py` · `approval_reminder.py` · `approval_lease.ReminderJournal` | 기존 승인 레코드의 key·원문 message id·실제 게시 시각에 고정된 리마인더 계산. 기존 key lease 안에서 lifecycle 관찰→영속 claim→최소정보 원문 링크 전송하며 catch-up은 최신 due 구간 1건만 시도한다. 별도 승인 상태머신이 아니다 |
| `external_effect_gate_e2e.py` · `injection_adapter.py` | E2E 서명(HMAC) 승인 경로. `E2E_TEST_MODE=1`에서만 동작 |
| `hermes_hook.py` · `hermes_plugin/` · `hook_boundary_e2e.py` | Hermes 플러그인 경계 — 게이트가 실제로 강제되는 지점. `coord-` 질의 응답은 config의 `interop_channel_id` 채널(#autophagy-agents)로 라우팅되며, 미설정 시 소스 채널로 폴백. 승인 표면은 `approval_surface.py`가 결정하고 `approval_directory.py`가 해석한다 — 설정 키(`personal_approvals_channel_id` / `deploy_approvals_channel_id`)는 그 디렉터리 내부에서만 읽힌다 |
| `approval_reminder_config.py` | `config.yaml`의 `approval_reminders`를 검증된 enabled·initial/repeat 모델로 로드(기본 true·3h·1h, 간격 오류 fail-fast) |
| `discord_transport.py` · `chunker.py` | `DiscordTransport` 순차 청킹 전송 + 429 `Retry-After` 백오프. `chunk_message`=2000자 분할 |
| `coordination.py` | 에이전트간 일정 조율 **순수 상태머신**. 가용성 교집합→후보 ≤3→양측 승인→소유자 승인=캘린더 쓰기 게이트. `correlation_id`는 `coord-` 접두사 |
| `delegation.py` · `report.py` | 위임 봉투 / `#agents-log` 보고 포맷 |
| `killswitch.py` | `!pause-agents`/`!resume-agents` (소유자만, 영속) |
| `loop_guard.py` | 봇 연쇄 분당 5회 상한 + 본문 해시 dedup |
| `gate_driver.py` · `live_peer_driver.py` | 게이트/피어 드라이버 |
| `production_guard.sh` | 프로덕션 게이트웨이에서 `E2E_TEST_MODE` 거부(부팅 차단) |

## 불변식 (변경 전 반드시 확인)
- **mutation은 owner 승인 레코드 없이는 절대 실행 안 됨.** 승인 판정 로직(`_has_valid_approval`) 변경은 보안 회귀.
- **`coordination.py`는 부수효과 없는 순수 상태머신** — 캘린더 쓰기/전송은 드라이버(coordination 스킬)가 소유. deadlock 10분·재협상 정확히 1회 규칙은 회귀 테스트로 고정.
- `DiscordTransport`는 8곳에서 호출됨 — 시그니처 변경 시 hermes_hook / gate_driver / meeting_cli 등 동반 갱신.
- 게이트/커버리지 부족 파일이 많음 — 로직 수정 시 RED→GREEN 단위 테스트 선행(`tests/unit/`).
- **`w1-5-*` 위임 트래픽은 반드시 소스 채널(#team) 응답을 유지해야 한다** — W1-5 게이트가 #team에서 response_availability를 폴링하므로 rerouting 금지. coord- 트래픽만 interop 채널로 분리한다.
- **승인 표면 해석**: 표면은 `approval_surface.py`(정책, I/O 없음)가 정하고 `approval_directory.py`(유일한 해석기)가 실제 채널로 바꾼다. 승인 producer가 스스로 채널을 해석하면 `tests/unit/test_approval_lifecycle_conformance.py`가 빌드를 깨뜨린다 — 산문이 아니라 코드가 강제한다.
  - **표면은 레코드에 영속된다**(`kind`/`surface`/`channel_id`/`policy_version`). 이후의 모든 읽기·리액션·삭제는 그 저장값을 쓴다. 정책이 앞서 나가도 저장된 레코드는 자기가 게시된 채널로 배수된다(append-only 전환 원장).
  - **현재 라우팅 (AS-SSOT, v7 — 2026-08-24 소유자 지시)**: calendar·coordination·wiki·mail compose·mail reply·budget·patent-export·obsidian-write·todo = **개인 서버 `#agent-chat`의 kind별 스레드**(`승인-<kind>`, 디렉터리가 active→archived→create 순으로 find-or-create; record.channel_id=스레드 id라 기존 리액션 워처·리마인더는 무변경 동작). **repair만 오너 DM 잔류** — 발신 주체 Ops 봇이 길드 미참여. skill-deploy·peer-attest·skill-publish·personal-skill-submit·managed 활성화 = **개인 서버 `#approvals`**(2차 주체인 peer 봇 또는 그룹 관리자가 같은 채널을 봐야 함). v6 이하로 게시된 기존 DM pending 레코드는 저장 바인딩(S2)대로 DM에서 종결된다. **주의: `#approvals`에는 메일·과제비·캘린더·위키·수리 승인이 단 한 건도 게시된 적이 없다**(전 생애 117건 실측).
  - 흐름별 `*_APPROVALS_CHANNEL_ID` env override는 **AS-3.2에서 제거되었다** — 승인 표면은 `~/.hermes/interop/config.json`의 config 키(`personal_approvals_channel_id` → 캐시 → guild-scan / **`agent_chat_channel_id`는 config 키 단독, 미설정=fail-closed·스캔 없음**)로만 해석되며, 환경 변수로 표면을 옮길 수 있는 경로는 없다(`tests/unit/test_approval_lifecycle_conformance.py::test_no_flow_specific_approvals_env_var_is_read`가 재도입을 차단). `deploy_approvals_channel_id`(공급망 config 키)는 대상이 아니다.
- **`#agents-log` 보고 summary는 마스킹 필수** — 내부 파일 경로/심볼/본문 인용/PII·시크릿·정량 민감치 금지(`report.py`, 원문: docs/guide/interop-규약.md).
- **`approval_lifecycle.request_owner_approval`은 보안 경계임**: 아래 5대 불변식을 약화하는 것은 소유자의 ✅가 무시되는 권한 회귀(Authorization Regression)로 간주함.
    - (1) Lease 보유 상태에서만 변이, (2) `message_id` 절대 덮어쓰기 금지, (3) 결정된 요청(APPROVED/CANCELLED) 파괴 금지, (4) 불확실 시 생존 간주(`UNVERIFIABLE` → `DEFER`), (5) 비성공 시 명시적 Reason + non-zero exit.
- **주의**: `_probe`의 예외 처리를 임의로 좁히지 마십시오. Lease 파일을 `unlink` 하거나 delete/drop 순서를 바꾸지 마십시오.
- **리마인더는 기존 watcher tick에서만 실행합니다.** 새 confirm surface·리액션·resolver·병렬 watcher를 만들지 않습니다. 본문은 요청 유형·경과시간·원문 링크만 포함하고 원문 채널 밖으로 전송 범위를 넓히지 않습니다.
- **리마인더 claim은 전송 오류에도 해제하지 않습니다.** 원격 전달 후 오류인지 구분할 수 없어 해제하면 동일 구간 중복 전송이 가능해집니다. 실패한 구간은 at-most-once로 닫고 다음 wall-clock 구간부터 다시 시도합니다.
- **I/O 제약**: `approval_lifecycle` 및 `approval_lease`는 lease/journal 파일 외의 디스크, 네트워크, 환경 변수에 직접 접근하지 않습니다. 저장소와 Discord 클라이언트는 각 게이트가 Protocol을 통해 주입해야 하며, 여기에 직접 I/O를 추가하지 마십시오.
