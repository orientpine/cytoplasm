# configs/ — 추적 시드 + 배포 번들

**모든 추적 config는 불변 시드다** (루트 "추적 config = 불변 시드" 규칙). 런타임 상태는
체크아웃 밖(~/.hermes/…, /srv/autophagy-private/…)에만 기록 — 여기 파일을 런타임에 mutate하면
ops 체크아웃이 dirty해져 pull/peer-attest가 막힌다.

## 파일 → 소비자 맵
| 파일 | 소비자 | 비고 |
|------|--------|------|
| `peers.example.yaml` | report-hub·calendar 분류 레지스트리의 공개 템플릿 | 권한 판정 금지. 실제값은 각 런타임의 비추적 `peers.yaml` |
| `sensitivity-rules.yaml` | meeting·mail·topics·prompt·proposal·report·doctype·recall·rag_ingest | 결정적 pre-LLM 민감도 분류 |
| `external-effect-tools.yaml` | `automation/interop/external_effect_gate.py` | denylist. 불량/빈/파싱불가 설정=mutation 전면 차단(fail-closed) |
| `entity-preflight.json` | `automation/entity_preflight/policy.py` | 개인 고유명사 자동선택·충돌 임계값과 출처 weight의 불변 시드. 누락/불량=fail-closed |
| `~/.hermes/config.yaml`의 `approval_reminders` | `automation/interop/approval_reminder_config.py` → 공용 승인 watcher | 비밀이 아닌 런타임 정책. 파서 생략 기본은 enabled=true·3h·1h, 신규 provision 시드는 소유자 정책 1h·1h, 불량 간격=시작 실패 |
| `mail-mode.default.json` | mail triage_mode | **시드 전용** — 런타임은 `~/.hermes/mail-triage/mail-mode.json`. 시드 경로 쓰기는 코드 가드가 거부 |
| `routing-policy.md` | (코드 미파싱) | LiteLLM 라우팅·예산·태그 정책의 문서 source of truth — 런북/배포 절차가 참조 |
| `budget-sheet.md` · `inventory.md` · `templates/` | budget 스킬 / 문서 | — |

Peer attestation의 신뢰 근원은 추적 시드와 분리된 `/etc/autophagy/peers.yaml`이다.
`automation/skill_gate.py`만 공용 `load_bot_ids()`로 읽으며 root 또는 ops 소유, 정규 파일,
group/other 쓰기 불가인 파일·부모 디렉터리만 인정한다. `~/.hermes/**`와
`configs/peers.example.yaml`은 agent가 바꿀 수 있으므로 attestation 권한 판정에 절대 쓰지 않는다.

## 배포 번들 (단순 config 아님)
- `rag/` — 개인 RAG 스택 한 세트: `compose.yaml` + `personal-rag.service`(systemd) + `mcp/`(인증 MCP API)
  + `embedding/`(로컬 임베딩 서비스). **자체 툴체인**: `uv` + Ruff `ALL`(line-length 100) +
  basedpyright `all` + 각자 `pyproject.toml`의 pytest(메인 트리 pytest와 별개).
- `litellm-staging/` — staged LiteLLM 게이트웨이 번들: `docker-compose.yml`이 `config.yaml`(라우팅·예산)
  + `custom_callbacks.py`(`PatentSensitiveGlmBlocker` — patent-sensitive 태그 및
  `[[PATENT-SENSITIVE-RECALL]]` 센티널 포함 glm-main 요청을 HTTP 403 거부)
  + `alert_dispatcher.py`(예산 알림)를 묶음. 배포 절차는 `DEPLOY.md` 런북.

## ANTI-PATTERNS
- 삭제된 공유 Lab 채널 ID를 config에 기입 (404 — 승인 채널은 이름 검색/env로 해석, interop AGENTS 참조).
- `litellm-staging/config.yaml` 수정 시 `model_list[glm-main]` 외 항목(태그 가드·키 예산·alias) 변경 —
  `routing-policy.md`의 검증 절차(인증 `/health`, 태그 403 마커, hard-cap 검증) 없이 배포 금지.
- `rag/*/.venv`·`__pycache__`·`*_cache`를 소스로 취급 — 생성물이다.
