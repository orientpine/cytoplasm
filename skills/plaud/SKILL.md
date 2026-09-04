---
name: plaud
description: "Plaud 라이프로그 동기화(plaud-sync 워처)의 상태를 읽기 전용으로 보고한다 — 로컬 전사 대기·승인 대기·저장 대기 건수와 로컬 전사본(.md) 경로. 트리거: 'plaud 상태', '라이프로그 동기화 상태', 'plaud 승인 대기 몇 건', 'plaud 마지막 폴', 'plaud 전사본 어디', 'plaud 녹음 회의록으로'. READ-only — Plaud·Discord·vault 어디에도 쓰지 않는다. 승인 카드(✅/⛔)는 이 스킬이 아니라 #agent-chat 요청별 스레드에 있다."
version: 1.1.0
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Plaud, Lifelog, Status, Read-Only]
prerequisites:
  commands: [python3]
---

# plaud — 라이프로그 동기화 상태 (읽기 전용)

## 무엇을

`plaud-sync` 워처(no-agent cron, 10분 틱)가 Plaud 녹음을 발견해 **오디오를 내려받아 노드에서
직접 전사**(whisper.cpp + 화자 분리)하고, 그 전사본으로 노트를 동결한 뒤 소유자 승인 뒤
Obsidian vault 에 밀어 넣는다. 이 스킬은 그 워처의 **상태 파일**을 읽어 한눈에 보고한다:

- 마지막 Plaud 폴 시각(UTC + KST)
- 레코드 수와 status 별 건수 — `transcribing`(발견·오디오 로컬 전사 대기) · `planned`(전사 완료·미게시) ·
  `posted`(카드 게시, ✅ 대기) · `approved`(✅ 받음, 다음 틱에 저장) · `written`(vault 저장 완료) ·
  `abandoned`(⛔ 또는 만료)
- 전사 대기 중인 녹음: 녹음 id · 시도 횟수 · 마지막 사유(예: `rc=4 로컬 전사 도구를 찾지 못했습니다`)
- 승인 대기 중인 녹음 목록: 녹음 id · 승인 스레드 id · 노트 파일명
- **로컬 전사본 목록** — `~/.hermes/plaud-sync/transcripts/<노트 stem>.md`. speechtotext 전사본과 같은
  형식(헤더 `- 화자: …` 범례 + `---` + `[HH:MM:SS] 화자N · 이름` 블록)이라 회의록 체인이 그대로 읽는다.

## 언제

소유자가 "plaud 상태", "라이프로그 동기화 상태 알려줘", "plaud 승인 대기 몇 건이야",
"plaud 마지막 폴 언제였어", "plaud 전사본 어디 있어" 처럼 물으면 **질문하지 말고** 바로 실행해
결과를 그대로 전한다.

## 명령

```bash
python3 /srv/autophagy-skills/live/plaud/scripts/plaud_cli.py status          # 사람용
python3 /srv/autophagy-skills/live/plaud/scripts/plaud_cli.py status --json   # 기계용 (transcripts[].path 포함)
```

출력 첫 줄은 항상 `PLAUD-STATUS state=<present|absent>` 다.

- `state=absent` — 워처가 아직 한 번도 성공적으로 돌지 않았다(상태 파일 없음). 그대로
  전하고, 필요하면 `hermes cron list --all | grep -A12 plaud-sync` 로 마지막 틱 결과를 본다.
- exit 2 + stderr `PLAUD-STATUS state=unreadable reason=…` — 상태 파일이 깨졌다. 추측해서
  요약하지 말고 그 사유를 소유자에게 전한다.
- `전사 대기(transcribing) N건` 의 사유가 `rc=4 …` 면 노드에 whisper/sherpa 도구가 없거나 경로 설정이
  빠진 것이다 — 녹음이 아니라 노드 문제이므로 시도 횟수는 늘지 않고 매 틱 다시 시도한다. 녹음 자체의
  실패(빈 전사·잘림·시간 초과)는 시도가 늘고 상한(기본 2)에 닿으면 Plaud 클라우드 전사로 노트를
  만들며 노트 출처 줄에 그 사실이 적힌다.

## 전사본을 회의록으로 보내기 (소유자 지시가 있을 때만)

lifelog 는 회의록 체인을 **자동 호출하지 않는다**. 소유자가 "이 plaud 녹음 회의록으로 만들어" 라고
지시하면 `status --json` 의 `transcripts[]` 에서 그 녹음의 `path` 를 찾아 기존 meeting CLI 에 넘긴다:

```bash
python3 /srv/autophagy-skills/live/meeting/scripts/meeting_cli.py ingest \
  --file ~/.hermes/plaud-sync/transcripts/<노트 stem>.md --label <회의 라벨> [--project <과제명>]
```

회의록 도메인(민감도 게이트·칸반·통지·Drive 발행·관리번호)은 meeting 스킬이 그대로 소유한다 — 여기서
재구현하지 않는다. 전사본은 노드 밖으로 나간 적 없는 로컬 전사라 `--file` 로 넘겨도 원음은 여전히 노드에만 있다.

## 하지 않는 것

- Plaud 를 폴하지 않고, 오디오를 내려받거나 전사하지 않으며(그건 워처의 일), Discord 에 아무것도
  올리지 않고, vault 에 쓰지 않는다.
- 노트 본문을 읽지 않는다(파일명만 보고한다). 본문은 승인 카드의 미리보기와 vault 에 있다.
- 승인 결정을 대신하지 않는다 — ✅/⛔ 는 `#agent-chat` 의 `obsidian-write · <녹음 id>`
  스레드에서 소유자만 누른다.

## 운영자 참고 — 승인 카드를 새 형식으로 다시 올릴 때

카드 렌더 형식이 바뀐 뒤 이미 게시된 카드를 새로 만들려면(소유자 요청이 있을 때만),
agent 계정에서 워처를 재게시 모드로 한 번 돌린다. 옛 카드를 지우고 **같은 스레드**에
새 카드를 올리며, 이미 ✅ 를 받은 건은 먼저 정상 틱으로 소비되므로 잃지 않는다:

```bash
python3 ~/.hermes/scripts/plaud_sync_watch.py --repost-posted
```

로컬 전사를 잠시 끄려면(옛 동작 = Plaud 클라우드 전사로 바로 `planned`) 노드 `~/.env.secrets` 에
`PLAUD_SYNC_TRANSCRIBE=0` 을 둔다. 이미 `transcribing` 인 레코드는 그대로 남으므로 되돌릴 때 다시 잡힌다.

## 관련

- 워처·저장 경로·승인 흐름: `docs/기능소개/plaud-lifelog-동기화.md`
- 로컬 전사 스테이지(오디오 다운로드·전사·잠금·폴백): `docs/기능소개/plaud-녹음-로컬-전사.md`
- 코드: `automation/plaud_sync/` (상태 스키마 `model.py`, 카드 `render.py`, 재게시 `repost.py`,
  전사 스텝 `transcribe.py`·`transcribe_live.py`, 오디오 `audio.py`)
