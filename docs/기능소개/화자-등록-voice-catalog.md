# 화자 등록 (voice catalog ②) — 등록된 목소리 저장소

## 무엇을

소유자가 「이 녹음의 화자2 는 김민수」라고 지정하면, 도구가 그 화자의 발화 구간을 원음에서
잘라 등록본(16 kHz mono wav, 최대 60초)으로 노드에 남긴다. `propose` 로 전사본의 화자N 별
블록 수·발화 시간·등록 계획을 먼저 보고, `enroll` 로 등록하고, `list`·`remove` 로 관리한다.
저장소는 `~/.hermes/speechtotext/voice-catalog/`(`catalog.json` + `samples/*.wav`)다.

## 왜

전사본의 `화자N` 은 익명이다. 이름은 자기소개 규칙·LLM 제안·소유자 override 로만 붙는데
2026-09-17 노드 실측에서 라이프로그 전사본 28건 중 이름이 확정된 화자는 **0건**이었다 —
회의에서 사람들은 자기소개를 하지 않는다. 소유자 아이디어(특허 초안 「voice catalog」)는
등록된 목소리를 녹음 앞에 결합해 diarization 만으로 식별하는 것이고, 이 단계는 그 결합에
쓸 **등록본을 만드는 저장소**다. 단계 전체는 `.omo/plans/voice-catalog.md` — ① 임베딩 상향
(완료, `docs/qa/PLE1`) → **② 이 문서** → ③ 선두 결합 + `catalog` 신뢰 단계 → ④ stt_eval 비교.

## 사용 시나리오

1. **제안 보기(읽기 전용, 에이전트가 스스로 돌려도 된다).**
   `stt_catalog_cli.py propose <전사본.md>` →
   ```
   화자1: 블록 85 · 발화 1182.0초 · 등록 계획 60.0초 (2구간)
   화자2: 블록 11 · 발화 82.0초 · 등록 계획 60.0초 (4구간)
   ```
   마지막 줄은 같은 내용의 JSON 이라 에이전트가 그대로 소유자에게 보여 줄 수 있다.
2. **등록(소유자가 이름을 명시해 지시했을 때만).** "그 녹음 화자2 는 김민수야, 등록해" →
   `enroll --transcript <md> --speaker 화자2 --name 김민수`. 원음은 `--audio` 가 없으면 Drive
   아카이브 manifest 에서 받아 0700 임시 디렉터리에서 자르고 지운다. 결과 JSON 한 줄
   (`name`·`seconds`·`wav`·`recording_id`)로 끝난다. 같은 녹음·같은 화자를 다시 등록하면
   그 등록본만 교체된다(멱등).
3. **되돌리기.** `remove --name 김민수` 가 `catalog.json` 항목과 wav 파일을 함께 지운다.
4. **Obsidian 에 적기만 해도 된다(②b, 2026-09-17 소유자 지시).** 라이프로그 노트의 `## 한눈에`
   아래에 `- 화자:: 화자1=차백동 · 화자3=김민수` 한 줄을 적으면, 10분 틱 no-agent 워처
   (`automation/voice_catalog/cron/voice_catalog_enroll_watch.py`)가 RAG 미러에서 그 줄을 읽어
   2 의 `enroll` 을 대신 돌리고 `#notifications` 에 「목소리 등록 · 김민수」(등록본 초·녹음
   id·되돌리기 명령) 한 건을 올린다. 같은 이름은 무동작, 이름을 고치면 재등록, 실패는 통지
   1건 뒤 노트가 바뀔 때까지 재시도하지 않는다(원장 `obsidian-enroll.json`). 소유자가 자기
   vault 에 이름을 적은 것이 곧 명령=동의라 별도 ✅ 는 없다.

### 거부 경로

- 그 화자의 1.5초 이상 블록이 없으면 `CATALOG-NO-SEGMENTS 화자N`(exit 2, 아무것도 안 씀).
- 원음도 manifest 행도 없으면 `CATALOG-AUDIO-MISSING <stem>`(exit 2).
- 저장 루트가 git 체크아웃 안이면 `CATALOG-ROOT-REFUSED`(exit 2) — 등록된 목소리가 저장소에
  섞이지 않게.
- `enroll`·`remove` 를 배포 사본이 아닌 곳에서 돌리면 `STALE-SKILL-COPY-BLOCK`(exit 3).
- **옛 전사본 주의(2026-09-17 실측)**: ① 이전에 만든 라이프로그 3건은 여러 사람이 `화자1`
  하나로 뭉쳐 있었다(272초 3인·449초 4인 녹음이 모두 `화자1` 15·31 블록). 거기서 자르면
  섞인 목소리가 등록되므로 `plaud_sync_watch.py --reprocess <id>` 로 다시 전사한 뒤 등록한다.
  도구는 이것을 판별하지 못한다 — 범례에 화자가 하나뿐이면 의심하라.

## 동의

타인의 목소리를 노드에 남기는 일이라 **소유자의 명시 지시가 곧 동의**다: 에이전트는 이름을
추측해 등록하지 않고, `propose` 로 제안만 한다. 저장은 노드 로컬이고 음성은 노드 밖으로
나가지 않으므로 외부효과 승인 게이트(Discord ✅) 대상은 아니다 — 소유자가 ✅ 게이트를
원하면 `ApprovalKind` 신설과 함께 별도 PR 로 올린다(계획 문서 「동의 정책」 B).

## 관련

- 코드: `skills/speechtotext/scripts/stt_catalog.py`(순수 — 구간 계획·스키마 v1·ffmpeg 필터),
  `stt_catalog_cli.py`(I/O — propose/enroll/list/remove)
- 테스트: `tests/unit/test_stt_catalog.py`, `tests/unit/test_stt_catalog_cli.py`
- 증적: `docs/qa/VC2/summary.md` · 계획: `.omo/plans/voice-catalog.md`
- 이름 병합 규칙: [speechtotext SKILL.md 「화자 이름」](../../skills/speechtotext/SKILL.md)
