# Plaud lifelog 노트 이름 양식 — `YYYY-MM-DD_HHMM_제목` (파일명 = title = H1, 공백 없음)

## 무엇을

`plaud_sync` 가 vault `000_PARA/Area/Lifelog/<연도>/` 에 쓰는 라이프로그 노트의 파일명이
`2026-09-10_1041_출산_당일_병원_이동과_입실_준비.md` 처럼 **날짜·시각·제목**을 `_` 로 이어 서고(공백 없음 — 소유자 지시 2026-09-15 저녁), frontmatter
`title` 과 `# H1` 이 그 파일명 stem 과 같은 문자열이다. 이름은 제목이 확정되는 시점(로컬 전사 뒤
finalize)에 정해지고, 한 번 vault 에 쓰인 뒤에는 고정된다. 이미 있던 노트 15건도 같은 양식으로
옮겼다(2026-09-15, `git-obsidian` `73dfa7e99`).

## 왜

2026-09-14 까지의 이름은 `2026-09-04-180427--7df8fc0f016b.md` 였다 — 문제는 셋이었다.

1. **이름을 정하는 시점이 틀렸다.** 파일명은 발견 때 계산돼 레코드에 박혔고 재처리가 그 경로를
   재사용했다. 로컬 전사 녹음은 발견 때 Plaud 제목이 없으므로 나중에 LLM 이 제목을 만들어도
   파일명은 영원히 시각(`180427`)이었다 — vault 13건 중 6건 실측.
2. **해시 12자**는 유일성만을 위한 것인데 Obsidian 사이드바·링크에 그대로 보였다.
3. 날짜가 파일명·제목 괄호·`녹음::` 줄에 **세 번** 나오면서 정작 **시각은 없어** 같은 날 녹음이
   시간순으로 정렬되지 않았고, 3건은 제목이 파일 stem, 3건은 H1 이 title 과 달랐다.

소유자 결정(2026-09-15): A안 `YYYY-MM-DD HHMM 제목.md`, 제목/H1 = stem, 기존 노트는 vault 에서
`git mv` + 커밋·푸시. 같은 날 저녁 추가 지시: **이름에 공백을 두지 않고 `_` 로 잇는다** — 링크·셸·URL 에서
따옴표 없이 다룰 수 있고, Obsidian 이 파일명을 제목으로 보여 주므로 `_` 가 그대로 보인다.

## 어떻게

- **stem** = `{날짜}_{HHMM}_{제목}` 이고 제목의 낱말 사이도 `_` 다(`WORD_SEPARATOR`). 제목은 Plaud 이름
  앞머리의 날짜·시각 토큰을 걷어낸 낱말이고, 글자가 하나도 없으면 추출 제목, 그것도 없으면 `녹음`.
  Obsidian 이 파일명에 허용하지 않는 글자(`* " \ / < > : | ?`)와 링크에서 뜻을 갖는 글자(`# ^ [ ]`)는
  `_` 로, 60자 상한, 해시 접미 없음.
- **유일성**: 같은 분에 다른 녹음이 이미 앉았을 때만 `_(2)`, `_(3)` 접미(발견이 기존 레코드 경로를
  `taken` 으로 넘긴다). 단일 장치라 실제로는 거의 일어나지 않는다.
- **시점**: `binding.finalize` 는 레코드에 `remote_ref` 가 없으면(아직 vault 에 없음) 추출 제목으로
  이름을 다시 짓고, 있으면 경로를 고정해 재처리(`--reprocess`)가 **같은 파일을 덮어쓴다**. 그때
  제목은 그 경로의 stem 을 따른다 — 옛 노트가 고아로 남는 경로는 없다.
- **용어집 교정**은 stem 을 처음 정할 때 이름에 한 번 걸린다. 경로가 고정된 뒤에는 용어집을 고쳐도
  노트가 둘로 갈라지지 않는다.
- **옛 노트 이관**: `python3 -m automation.plaud_sync.rename_legacy --vault <vault>` 가 dry-run 으로
  계획을 찍고, `--apply` 가 `git mv` + title/H1 재작성 + old→new 매핑 JSON 을 남긴다. 새 stem 은
  노트 자신의 frontmatter(`created` 의 시각, `title` 의 낱말)에서 오고, 제목이 옛 stem 이었던 노트는
  슬러그에서, 시각뿐인 노트는 `SKIP … pass --stem` 으로 보고돼 소유자가
  `--stem 'OLD.md=YYYY-MM-DD_HHMM_제목'` 으로 이름을 준다. 2026-09-15 낮의 공백 양식(`YYYY-MM-DD HHMM 제목`)도
  legacy 로 인식해 `_` 양식으로 옮긴다(title 의 날짜·시각 앞머리를 걷어내 두 번 적지 않는다). `--state <state.json> --mapping <json>
  --apply` 는 노드의 **written 레코드만** 새 경로로 옮기고(action_hash 불변), 미종결 레코드는
  승인 카드가 옛 경로에 묶여 있으므로 건드리지 않고 `STATE-SKIP` 으로 보고한다.

## 사용 시나리오

- **새 녹음(happy path)**: 발견 → `2026-09-16_0930_녹음.md` 자리표시로 동결(transcribing) → 로컬 전사 →
  추출 제목 "주간 실험 계획 점검" → finalize 가 `2026-09-16_0930_주간_실험_계획_점검.md` 로 이름을
  짓고 카드에 그 경로를 싣는다 → ✅ → vault 에 그 이름으로 저장.
- **재처리**: 위 노트가 이미 vault 에 있고 소유자가 `--reprocess` 를 돌리면 새 추출이 다른 제목을
  내도 파일명·title·H1 은 `2026-09-16_0930_주간_실험_계획_점검` 그대로다.
- **이관 거부 경로**: `rename_legacy --vault …` 가 `SKIP 2026-09-04-180427--….md: no title — pass --stem`
  을 찍으면 아무것도 옮기지 않는다. `--stem` 의 값이 `YYYY-MM-DD_HHMM_제목` 꼴이 아니면
  `BAD-STEM` 으로 exit 2, 이미 있는 이름과 겹치면 그 한 건만 `collides with …` 로 건너뛴다.

## 관련

- `automation/plaud_sync/note_paths.py`(`note_destination`·`stem_title`·`strip_recording_stamp`·`STEM_RE`),
  `note.py`(`corrected_lifelog_note(taken=, relpath=)`), `binding.py`(`finalize` 의 remote_ref 규칙),
  `sync.py`(발견 시 `taken`), `rename_legacy.py`(이관 CLI).
- 회귀: `tests/unit/test_plaud_sync_note.py`, `test_plaud_sync_transcribe.py`,
  `test_plaud_sync_reprocess_note_path.py`, `test_plaud_sync_rename_legacy.py`.
- 승인 게이트는 그대로다 — 카드의 `note_relpath` 가 새 양식일 뿐 해시 바인딩·✅ 흐름은 불변.
- 이전 양식 문서: [plaud-lifelog-노트-v2-양식](plaud-lifelog-노트-v2-양식.md)(본문 구조는 그대로).
