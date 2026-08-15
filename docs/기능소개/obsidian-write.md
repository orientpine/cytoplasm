# Obsidian 승인형 쓰기 어댑터

## 무엇을

`automation/obsidian_write/`는 push 직전에 외부효과 승인 게이트를 평가하는 Obsidian
노트 저장 어댑터다. 읽기 전용 RAG 미러와 분리된 clone과 전용 deploy key를 사용해 PARA
경로에 한 파일만 upsert하고, owner 승인 로그가 요청의 경로·제목·본문과 일치할 때만
push한다. push 뒤 원격 ref를 읽어 SHA-256까지 일치할 때만 성공 영수증을 반환한다.

## 왜

RAG 인제스트용 `~/.hermes/obsidian-mirror`는 주기적으로 원격에 hard reset 되는
pull-only 캐시라 쓰기에 사용하면 변경이 유실되거나 읽기 경로를 훼손할 수 있다. 이
어댑터는 쓰기 clone을 `~/.hermes/obsidian-write`로 분리하고, mirror 경로를 clone으로
지정하면 subprocess 실행 전 거부한다.

## 사용 시나리오

### 저장 성공

1. 승인 producer는 공유 `approval_lifecycle.request_owner_approval`와 owner DM binding으로
   요청을 게시하고, 유효한 소유자 승인 레코드를 만든다.
2. 호출자는 `plan_note()`에 제목·본문·개인/기관 구분·PARA 힌트를 전달한다. 개인은
   `000_PARA/{Project,Area,Resource,Archive}`, 기관 내용은 `001_KIMM_PARA/*`에 놓이며,
   분류 불가는 기존 `000_PARA/Area/000_정리되지않은생각들` inbox로 간다.
3. `write_note()`는 별도 clone을 fetch/reset하고 한 경로만 add/commit한 뒤, `git push`
   직전 `obsidian_write.note_push` tool call을 평가한다. 경로·제목·본문이 승인과 모두
   일치해야 push하고, 그 뒤 `origin/<branch>:<relpath>`를 다시 읽는다. 내용 hash가 같으면
   `WriteReceipt`를 돌려준다.

### 거부·실패

- 전용 key 또는 config가 없거나 읽을 수 없으면 외부 명령 없이 fail-closed 오류를 낸다.
- 승인 레코드가 없거나, 승인 후 제목·경로·본문이 바뀌면 push 전에 fail-closed 오류를 낸다.
- push 실패나 원격 read-back hash 불일치는 성공으로 보고하지 않고 retryable 오류를 낸다.
- mirror를 write clone으로 준 요청은 zero subprocess로 거부된다.

## 관련

- 수리 티켓: `t_1b8aab9b`
- 구현: `automation/obsidian_write/config.py`, `note.py`, `gate_binding.py`, `writer.py`
- 검증: `tests/unit/test_obsidian_write_gate.py`, `tests/unit/test_obsidian_write_writer.py`,
  `docs/qa/RTS-2/a3-obsidian.txt`, `docs/qa/RTS-2/a4-gate.txt`
- 승인 결합: `ApprovalKind.OBSIDIAN_WRITE`는 owner DM으로 라우팅되며, 실제 채널 해석은
  공용 `approval_directory.py`만 수행한다.
