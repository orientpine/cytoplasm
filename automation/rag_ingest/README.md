# rag_ingest — W2-4 개인 RAG 인제스트 파이프라인

`personal_cha` 벡터 컬렉션(W2-1 MCP memory server 경유)에 개인+팀 지식을
적재하는 파이프라인. **임베딩은 설정된 RAG 노드 내부 로컬 bge-m3 서버에서만**
일어난다 — 이 패키지는 어떤 임베딩 API도 직접 호출하지 않고, 콘텐츠를 MCP
`load_memory` 툴로 전달할 뿐이다.

## 소스

| source_type | 원천 | source_key |
|---|---|---|
| `wiki` | `~agent/wiki/*.md` (W2-2 볼트) | `wiki:<relpath>` |
| `note` | `~agent/notes/*.md` | `note:<relpath>` |
| `meeting` | `~agent/notes/meetings/*.md` (W2-3 원문 저장 위치) | `meeting:<relpath>` |
| `conversation` | Hermes `state.db` (read-only) — 세션×KST일 단위 LLM-free 다이제스트 | `conversation:<session>:<day>` |
| `peer-report` | #agents-log의 active roster principal이 보낸 W1-6 v0 보고(정확 7키 JSON 블록) | `agents-log:<message_id>` |
| `team-chat` | #team 신규 메시지 배치 트랜스크립트 | `team:<first_id>-<last_id>` |
| `obsidian` | `~agent/.hermes/obsidian-mirror` (git-obsidian read-only 미러) | `obsidian:<relpath>` |
| | (민감도 태깅: `patent-sensitive` 자동 분류 포함) | |

모든 벡터에 **내 관점 메타데이터**(`agent_id/owner/role/project/interest_tags`
+ `source_type` + 출처 필드)가 붙는다. 팀 지식의 인별 중복 적재는 의도된
설계(사용자 결정 2026-07-13). W2-5 recall은 `source`/`metadata`(위키 경로,
`task_id`, `message_id`, `session_id`)로 출처를 표기할 수 있다.

`peer-report`는 Discord 실제 작성자 ID를 roster의 admin publisher principal 또는 active
member node label로 해석한 값과 본문 `agent_id`가 정확히 일치할 때만 적재한다. 미등록·
removed 작성자와 불일치 보고는 본문 없는 warning만 남기고 문서 생성 전에 거부하므로
`queue.jsonl`과 MCP `load_memory`에 들어가지 않는다. roster가 없거나 깨졌으면 그 tick의
Discord source 전체를 fail-closed로 건너뛴다.

## 멱등성 (2중)

1. **클라이언트 fingerprint** — `state.json`에 문서별 청크 (source,content)
   해시를 기록. 무변경 문서는 네트워크 호출 0으로 스킵.
2. **서버 uuid5 upsert** — MCP 서버가 `uuid5(source\ncontent)`를 포인트 id로
   사용하므로 강제 재적재(`--force`)도 동일 포인트를 덮어써 중복 0.

문서 변경 시 이전 포인트 중 새 청크에 없는 id는 `delete_memory`로 정리된다.

## 장애 경로 (큐잉/재시도, 유실 0)

변경분은 먼저 `queue.jsonl`에 내구 잡으로 기록된 뒤 전달된다. RAG 노드가
다운이면 잡이 큐에 남고 다음 cron tick에 재시도된다. state/커서는 전달 성공
후에만 전진한다(at-least-once + 멱등 upsert = 유실 0, 중복 0).

obsidian 미러 sync 실패는 파이프라인을 중단하지 않는다(Codified decision 2):
WARN 로그 후 last-good 미러가 있으면 그대로 스캔(신선도 희생 수용), 없으면
그 tick의 obsidian 소스만 스킵한다. 스킵된 tick은 deletion sync를 건드리지
않으므로 기존 벡터가 지워지지 않는다.

## 배포 (설정된 primary agent)

- 패키지: `~/.hermes/rag_ingest_runtime/rag_ingest/` (이 디렉터리 복사)
- 설정: `~/.hermes/rag-ingest/config.json` (600; `config.example.json` 참조 —
  guild id 등은 repo에 두지 않는다)
- 시크릿: `~/.env.secrets`의 `RAG_MCP_API_KEY`(ops 핸드오프), `DISCORD_BOT_TOKEN`
- 워처: `cron/rag_ingest_watch.py` → `~/.hermes/scripts/` 복사 후
  `hermes cron create "every 10m" --name rag-ingest-watch --no-agent --script rag_ingest_watch.py --deliver local`
  — flock 단일 인스턴스 가드 내장(`~/.hermes/rag-ingest/watch.lock`): 첫 obsidian
  부트스트랩(~2240 파일)처럼 10분을 넘기는 실행 중 겹친 tick은 무음 exit 0.

## 실행

```bash
python3 -m rag_ingest run [--config PATH] [--sources wiki,notes,...] [--force] [--verbose]
```

exit 0 = 정상(큐잉 포함), exit 1 = 설정/인증 오류. 성공 tick은 무음(stdout 없음),
백로그가 남으면 한 줄 공지. 상세 로그는 `~/.hermes/rag-ingest/logs/`(600) —
민감 콘텐츠가 제목에 포함될 수 있어 repo/공개 채널로 복사 금지(제약 8).

## 테스트

```bash
cd /path/to/repo && python3 -m pytest tests/unit/test_rag_ingest_*.py -v
```
