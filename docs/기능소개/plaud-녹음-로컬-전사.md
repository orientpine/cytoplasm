# Plaud 녹음 로컬 전사 (plaud_sync `transcribing` 스테이지)

## 무엇을

Plaud 녹음의 **오디오 파일을 내려받아 노드에서 직접 전사**한다 — speechtotext 스킬이 이미 가진
whisper.cpp + sherpa-onnx 화자 분리를 그대로 써서, 결과 전사본(.md: `- 화자:` 범례 + `---` +
`[HH:MM:SS] 화자N · 이름` 블록, 한 줄 한 문장)을 `~/.hermes/plaud-sync/transcripts/<노트 stem>.md` 에
남기고, lifelog 노트의 `## 전문` 을 그 전사로 채운다. Plaud 클라우드 전사는 로컬 전사가 끝내 실패할
때의 폴백일 뿐이다.

## 왜

- 소유자 지시(2026-09-04): "recording 된 audio 파일을 내가 직접 전사해서, 화자 구분 후 텍스트로
  저장 — 그 저장물을 life log 나 회의록 파이프라인(추후 기능 포함)이 쓰게".
- Plaud 클라우드 전사는 `speaker_1` 같은 라벨만 있고 우리 용어집·자기소개 이름 규칙·문장 단위 출력이
  없다. 그리고 **파일이 노드에 남아야** `meeting_cli.py ingest --file` 같은 다른 파이프라인이 읽는다.
- 음성은 노드 밖으로 나가지 않는다. 자식 env 에 `SPEECHTOTEXT_BACKEND=local` 을 고정하므로 도구가
  없으면 exit 4 로 멈추고 API 로 폴백하지 않으며, `DRIVE_PUBLISH_ENABLED=0` 으로 개인 녹음이 Drive
  `전사본/` 폴더에 올라가지 않는다 — 승인된 Obsidian 노트가 유일한 목적지다.

## 어떻게 (틱 한 번)

1. **discovery**(30분 게이트) — 새 녹음을 `planned` 대신 `transcribing` 으로 동결한다(초안 = Plaud 요약 +
   클라우드 전사, hash 는 finalize 때 다시 계산).
2. **resolve** — 카드 게시·✅ 판독·vault 저장(watch.lock 아래, 기존 그대로). `transcribing` 은 건드리지 않는다.
3. **watch.lock 을 푼다** → 전사 스텝(`transcribe_live.run_transcribe_step`, 틱당 1건): 
   `pipeline_lock`(speechtotext 워처와 공유 — whisper 는 같은 자원, 못 잡으면 busy 한 줄 양보) →
   `get_file` presigned URL(24h, JSON 뒤 산문이 붙어 `raw_decode`) → 스트리밍 다운로드(상한 1 GiB,
   `audio/<id>.mp3`, 캐시) → speechtotext CLI `transcribe --file … --label <stem>` → 전사본을
   `transcripts/<stem>.md` 에 저장 → 요약을 `get_note` 로 갱신하고 노트 본문을 재조립 →
   **commit: watch.lock 을 blocking 으로 다시 잡고 레코드가 여전히 `transcribing`·같은 action_hash 인지
   재검사한 뒤 한 번 저장** → 오디오 삭제 → `planned`.
4. 승격된 건이 있으면 같은 틱에서 resolve 를 한 번 더 돌려 카드를 바로 올린다(다음 틱 10분을 기다리지 않는다).

117분 녹음은 노드에서 약 45분 걸린다(0.4× 실시간). 그동안 watch.lock 이 비어 있으므로 다음 틱들은 ✅ 를
정상 소비하고, 전사 스텝만 pipeline_lock 에 막혀 양보한다.

## 실패 분류

| 종류 | 예 | 처리 |
|---|---|---|
| 환경(노드) | rc=3(governed 거부)·rc=4(whisper/sherpa 없음), CLI 미마운트, MCP 오류, 네트워크 | **카운트 안 함**, 사유를 `last_block_reason` 에 적고 매 틱 재시도 |
| 녹음 | rc≠0(빈 전사·잘림·미지원 형식), 시간 초과, 오디오 상한 초과 | `transcribe_attempts`+1; 상한(기본 2) 도달 시 **클라우드 전사로 폴백**해 `planned`, 노트 출처 줄에 `PLAUD 클라우드 전사(로컬 전사 N회 실패: …)` |
| stale | commit 시점에 레코드가 바뀌어 있음 | 아무것도 덮어쓰지 않는다 |

## 사용 시나리오

1. **happy**: Plaud 로 녹음 → 30분 내 발견(`transcribing`) → 다음 틱에 전사 → 카드 미리보기가 로컬 전사 상위
   5줄 → ✅ → vault `000_PARA/Area/Lifelog/<연도>/` 에 `## 요약` + `## 전문`(화자 범례·시각 블록) + 출처 줄
   `… · 전사: 로컬 전사 local:ggml-large-v3-turbo-q5_0 · 화자 분리`. 진행은 "plaud 상태" 한마디로 본다.
2. **회의록으로**: 소유자가 "이 plaud 녹음 회의록으로 만들어" → 에이전트가 `plaud_cli.py status --json` 의
   `transcripts[].path` 를 찾아 `meeting_cli.py ingest --file <path> --label … [--project …]`. lifelog 가 회의록
   체인을 **자동 호출하는 일은 없다**(기존 경계 유지).
3. **노드에 도구가 없다**: `전사 대기(transcribing) 1건 · 시도 0 · 사유 rc=4 로컬 전사 도구를 찾지 못했습니다`
   가 매 틱 그대로다. 노드 `~/.env.secrets` 의 `SPEECHTOTEXT_WHISPER_BIN/MODEL`·`SPEECHTOTEXT_DIARIZE_*` 를 고치면
   다음 틱에 진행한다. 당장 옛 동작(클라우드 전사로 바로 카드)이 필요하면 `PLAUD_SYNC_TRANSCRIBE=0`.

## 실측 (2026-09-04)

- `get_file` 은 `{id,name,created_at,start_at,duration,presigned_url,source_list,note_list}` + 산문 꼬리. presigned
  URL 은 GET 만 서명돼 HEAD 가 403 → 크기 상한은 `Content-Length` 와 스트림 누계로 건다.
- 워크스테이션 드라이버(실 MCP·실 S3·CLI 는 `SPEECHTOTEXT_CLI` 가짜): 2초 녹음 11,888 B(ID3 헤더) 다운로드 →
  `transcribing 1 → planned 1`, `transcripts/<stem>.md` 생성, `audio/` 비움, body sha 일치. whisper 실행 자체는
  노드 릴리스 뒤 첫 `transcribing` 레코드에서 확인한다(워크스테이션에 whisper 가 없다).

## 관련

- 코드: `automation/plaud_sync/audio.py`(URL·다운로드) · `transcribe.py`(순수 스텝·실패 분류·폴백) ·
  `transcribe_live.py`(MCP·S3·subprocess·잠금·commit) · `cron/plaud_sync_watch.py`(lock 해제 순서) ·
  `model.py`(`transcribing`, `transcribe_attempts`) · `skills/plaud`(status 1.1.0)
- 테스트: `tests/unit/test_plaud_sync_{audio,transcribe,transcribe_live,transcribing_status}.py`
- env(`configs/env.example`): `PLAUD_SYNC_TRANSCRIBE`(1) · `PLAUD_SYNC_TRANSCRIBE_PER_TICK`(1) ·
  `PLAUD_SYNC_TRANSCRIBE_ATTEMPTS`(2) · `PLAUD_SYNC_TRANSCRIBE_TIMEOUT`(21600) · `PLAUD_SYNC_MAX_AUDIO_BYTES`(1 GiB);
  CLI 경로 주입은 `SPEECHTOTEXT_CLI` / `SPEECHTOTEXT_SCRIPTS`
- 규약: `automation/pipeline_lock.py`(규약 (n) — plaud 도 같은 lock), [plaud lifelog 동기화](plaud-lifelog-동기화.md),
  [음성 녹취 → 전사본 → 회의록](음성-녹취-회의록-자동화.md)
