---
name: speechtotext
description: "Google Drive 폴더에 올려둔 음성 녹취를 전사본(.md)으로 만들고, 그 전사본을 meeting 스킬로 넘겨 회의록까지 잇는 스킬. 전사는 기본이 로컬(whisper.cpp)이고, 2시간이 넘는 단일 녹취도 누락 검증을 통과해야만 회의록으로 넘어간다."
version: 1.1.2
author: autophagy-agents
---

# speechtotext — 음성 → 전사본(.md) → 회의록

변경 명령은 `/srv/autophagy-skills/live/speechtotext/scripts/`에서만 실행하며, 낡은 사본은 `STALE-SKILL-COPY-BLOCK`으로 거부한다.

소유자가 **감시 폴더에 녹취 파일을 놓으면** 5분 틱 워처가 그것을 집어 전사본을 만들고
곧바로 `meeting` 스킬로 넘겨 회의록·칸반·마일스톤까지 잇는다. 폴더에 파일을 놓는 행위가
`!meeting` 토큰과 같은 **명시 지시**이며, 그래서 별도 승인 게이트를 두지 않는다.

## 동작 방식 (에이전트가 지켜야 할 규칙)

1. **전사본 원문을 내 컨텍스트/응답에 붙여넣지 마라.** 회의 내용은 CLI 안에서만 흐르고
   결과는 JSON 요약(전사본 경로·글자수·커버리지·meeting 종료코드)으로만 읽는다.
2. 소유자가 **로컬 음성 파일 경로**를 대며 회의록을 요청하면 아래를 실행한다:

   ```bash
   python3 /srv/autophagy-skills/live/speechtotext/scripts/speechtotext_cli.py ingest \
     --file <음성경로> --label "<회의 라벨>"
   ```

   채널 지시라면 `--notify-channel <채널ID> --notify-message-id <지시 메시지ID>`를 함께
   넘긴다(그대로 meeting 에 전달되어 결과가 원 채널 스레드로 돌아간다).
3. 전사본만 필요하면 `transcribe`, 회의록까지면 `ingest`, 이미 있는 전사본을 다시 다듬기만
   하려면 `polish` 다. `transcribe` 와 `polish` 는 meeting 을 호출하지 않는다.
4. **거부는 그대로 전달하라.** 종료코드가 곧 사유다 — 3=크기 초과, 4=로컬 전사 도구 부재,
   5=미지원 형식/빈 전사, 6=전사 API 실패, 7=회의록 체인 실패(전사본은 남아 있으므로
   재시도해도 전사를 다시 지불하지 않는다), 8=**누락 의심**. 내용을 추측해 채우지 마라.

## 전사 백엔드 — 로컬이 기본인 이유

회의록의 민감도 게이트는 **텍스트**만 본다. 즉 외부 전사 API를 쓰면 특허·민감 회의의
**원음이 게이트를 거치기 전에** 공유 프로바이더로 나간다. 그래서 로컬 whisper.cpp 가
해석되면 그것을 쓴다.

| `SPEECHTOTEXT_BACKEND` | 동작 |
|---|---|
| `auto`(기본) | 로컬 도구가 해석되면 로컬, 아니면 API |
| `local` | 로컬만. 도구가 없으면 **exit 4 로 중단하고 절대 네트워크로 폴백하지 않는다** |
| `api` | OpenAI 호환 `/v1/audio/transcriptions` (`SPEECHTOTEXT_BASE_URL` 로 대체 가능) |

## 2시간이 넘는 단일 녹취

- **로컬**: 분할하지 않고 한 번에 처리한다(whisper.cpp 의 장문 디코딩이 그렇게 설계돼 있고,
  외부 분할은 이음매에서 말을 자른다). 25MiB 상한은 API 업로드 제약일 뿐 로컬에는 없다.
- **API**: 25MiB를 넘으면 15분 창 + 10초 겹침으로 나눠 올리고, **겹친 구간에서 실제로
  반복되는 텍스트만** 지운 뒤 순서대로 잇는다(일치하지 않으면 양쪽을 모두 남긴다 —
  중복은 불편이지만 누락은 실패다).
- **누락 검증**: whisper.cpp full JSON 의 구간 타임스탬프 합집합을 `ffprobe` 실제 길이와
  대조한다. 침묵은 결함이 아니므로 커버리지 비율만으로 판정하지 않고, 설명되지 않는
  앞/뒤 구간이나 보통의 쉼보다 훨씬 긴 내부 공백이 있으면 **exit 8 로 거부**한다. 길이를
  알 수 없으면 완결을 주장하지 않는다(`COVERAGE-UNKNOWN`).
- **반복 붕괴 검사**: 커버리지는 "구간이 비었는가"만 본다. 디코더가 무너지면 타임스탬프는
  그대로 채워진 채 같은 문장만 되풀이되므로 커버리지는 통과한다. 그래서 최다 8어절의
  점유율을 따로 재고, 기본 8%를 넘으면 같은 exit 8로 거부한다(`SPEECHTOTEXT_MAX_REPEAT`).
  실측: 정상 구간 1.2% vs 붕괴한 94분 녹취 57.1%.
- **문맥 이월은 기본으로 끈다(`-mc 0`)**. 원래는 "문맥을 자르면 연속성이 준다"는 이유로 쓰지
  않았는데, 실제 94분 한국어 녹취가 그 가정을 뒤집었다 — 이월을 켜면 디코더가 자기 출력을
  되먹어 전사본의 28%가 한 문장 910회 반복이 됐고, 같은 구간을 `-mc 0` 으로 다시 디코딩하니
  정상 수준(1.2%)으로 돌아오며 사라졌던 28분치 발화가 복구됐다. 되돌리려면
  `SPEECHTOTEXT_WHISPER_CONTEXT=-1`.
- 그 외 완결성을 해치는 플래그는 여전히 쓰지 않는다: `-nf`(실패한 창을 구제하는 온도 폴백을
  끈다) · `--vad`(조용한 한국어 발화를 잘라낸다).

## 전사본 다듬기 (정리)

전사기는 모든 구간을 **한 줄**로 이어 붙인다 — 실제 94분 녹취가 38,216자 한 줄로 나왔다.
충실한 전사본이지만 읽을 수 없는 문서다. 그래서 `.md` 로 쓰기 직전에 한 번 다듬는다:

- 문장 경계로 나누고 블록으로 묶는다(아래 「문장 단위 출력」). 화자가 없으면 4문장·180자를 모두
  넘겨야 블록이 닫힌다.
- **연속으로 완전히 같은 문장**만 하나로 접는다. 나중에 다시 나오는 같은 말은 사람이 다시
  한 것이므로 남긴다 — 이 스킬의 반복 원칙 그대로, 중복은 불편이고 누락은 실패다.
- 용어집으로 고유명사를 바로잡는다.
- **요약하지 않는다.** 결정사항·액션아이템·마일스톤을 읽어내는 일은 meeting 의 몫이고
  이 경계를 넘지 않는다.

## 문장 단위 출력

전사본 본문은 **블록**의 나열이고, 블록은 빈 줄 하나로 갈린다. 블록 안에서는 **한 줄에 한 문장**만
쓴다. 블록의 첫 줄은 헤더이며 `[HH:MM:SS] 화자N · 이름` 형식이다. 시각만 알면 `[HH:MM:SS]`,
화자만 알면 `[--:--:--] 화자N`, 둘 다 모르면 헤더 없이 문장 줄만 남는다(레거시 문단을 다시 다듬으면
이 모양이 된다).

```
[00:03:12] 화자1 · 김민수
안녕하세요, 저는 김민수라고 합니다.
오늘은 해양 계측 일정부터 보겠습니다.

[00:03:41] 화자2 · 이영희
계측기 납품이 2주 밀렸습니다.
```

**왜 바꿨나**: 94분 실측 녹취가 문단 140줄로 나왔고 가장 긴 줄이 1,137자였다. 충실하지만 소유자가
그 안에서 아무것도 찾지 못했다. 같은 녹취를 문장 줄로 쓰면 735줄이 되고, 각 블록이 언제 시작했는지도
줄 위에 적힌다. 사람이 읽는 단위가 문장이므로 문서의 줄도 문장이다.

타임스탬프는 whisper.cpp 가 이미 토큰마다 보고하던 값이다. 예전 경로는 구간을 한 문자열로 이어붙이며
그 값을 버렸다. 문법은 `stt_blocks.render()` 가 쓰고 `stt_blocks.parse()` 가 되읽으므로, 디스크에 이미
있는 옛 전사본과 새로 다듬은 전사본이 같은 문서 형태로 수렴한다(`polish` 는 멱등).

화자가 하나도 없으면 블록은 예전 문단 규칙(4문장·180자를 모두 넘겨야 닫힘)으로 끊고, 화자가 있으면
**같은 화자가 이어지는 최대 구간**이 한 블록이다.

## 화자 구분

로컬 whisper.cpp 경로에서만 동작하며, 화자 분리는 **sherpa-onnx 바이너리를 노드에서 직접 실행**한다.
음성은 어떤 경우에도 노드 밖으로 나가지 않는다(전사 백엔드를 로컬로 두는 이유와 같다). 분리 결과는
`speaker_00` 같은 클러스터 번호이고, 이것을 전사본에 처음 나온 순서대로 `화자1`, `화자2` … 로 매긴다.
문장은 겹침이 가장 큰 turn 에 붙고, 겹치는 turn 이 없으면 중점이 2초 이내인 가장 가까운 turn 에,
그것도 없으면 직전 문장의 화자를 잇는다.
화자를 붙이기 전에 문장을 먼저 쪼갠다 — 한 문장을 두 화자 이상이 각각 1초 넘게 나눠 가졌으면 화자가
바뀌는 자리에서, whisper 가 구두점을 내지 않아 문장이 길어지면 15초마다, 가장 가까운 띄어쓰기에서
자른다(문장 하나에는 화자 하나만 붙으므로, 쪼개지 않으면 4명이 말한 57초가 통째로 화자1 이 된다).

- **fail-soft**: 도구·모델이 없거나 실행이 실패하면 stderr 에 `DIARIZE-FAIL <사유>` 를 찍고 **화자 없이
  전사를 계속한다**. 화자 분리 때문에 전사가 실패하는 일은 없다. 도구가 없는 노드의 출력은 화자 헤더만
  빠진 문장 줄 문서다.
- `--speaker-count N`: 화자 수를 알면 클러스터 수를 고정한다(`--clustering.num-clusters`).
- `--no-diarize`: 이번 실행만 화자 분리를 건너뛴다.
- `SPEECHTOTEXT_DIARIZE_THRESHOLD`(기본 `0.9`): 화자 수를 모를 때 쓰는 군집 임계값. **낮추면 화자를 더
  잘게 쪼개고(같은 사람이 둘로 갈릴 수 있다), 올리면 서로 다른 사람이 한 화자로 합쳐진다.**
- API 백엔드에는 구간 타임스탬프가 없어 화자 분리를 하지 않는다.

## 화자 이름

`화자N` 라벨에 실제 이름을 붙이는 경로는 셋이고, 결과는 헤더의 범례 한 줄로 남는다:

```
- 화자: 화자1=김민수 [자기소개 00:03:12] · 화자2=이영희 [LLM] · 화자3=미상
```

| 출처 | 어떻게 | 표기 |
|---|---|---|
| 자기소개(규칙) | "저는 김민수라고 합니다" 류를 각 화자의 앞 12문장에서만 찾는다. 한 이름을 두 화자가 주장하면 둘 다 버린다 | `자기소개 HH:MM:SS` |
| meeting LLM | `ingest` 가 회의록 CLI 의 마지막 stdout JSON 의 `speakers` 배열을 받아 전사본에 되먹인다(호명·소개처럼 문맥이 필요한 근거는 여기서 나온다) | `LLM` |
| 소유자 | `polish --speakers "화자1=김민수,화자2=이영희"` | `소유자` |

우선순위는 **소유자 > 자기소개 > LLM**. 규칙이 이미 이름을 정한 라벨에 LLM 이 다른 이름을 제안하면
규칙 이름을 유지하고 이견을 범례에 남긴다: `[자기소개 00:03:12 · LLM 제안: 박철수]`. 근거 없는 라벨은
`미상`으로 적는다. 누락과 미상은 다른 사실이다.

**이름은 제안이지 판정이 아니다.** 자기소개 패턴도 LLM 도 틀릴 수 있으므로, 소유자가
`polish --speakers` 로 고치면 그 값이 이후 모든 경로를 이긴다.

이미 있는 전사본은 오디오·모델·비용 없이 다시 다듬을 수 있다(멱등):

```bash
python3 /srv/autophagy-skills/live/speechtotext/scripts/speechtotext_cli.py polish --file <전사본.md>
```

### 용어집 — 이름을 한 곳에만 적는다

용어집의 정본은 **Drive 이고, 산출물 트리와 같은 모양으로 중첩**된다. 각 층에 `용어집.csv` 를 두면
되고, 안쪽 층이 같은 표기를 다르게 적으면 **깊은 쪽이 이긴다**.

| 어디에 | 무엇을 | 우선순위 |
|---|---|---|
| Drive `autophagy/전사본/<과제>/용어집.csv` | 그 과제의 기관·업체·사람·장비 이름 | 가장 높음 |
| Drive `autophagy/전사본/용어집.csv` | 전사 전반의 일반 오인식(영무→업무) | 중간 |
| Drive `autophagy/용어집.csv` | 모든 산출물에 걸리는 이름 | 낮음 |
| 노드 `~/.hermes/speechtotext/glossary.txt` | 위 Drive 층들의 **캐시**(정본 아님) | Drive 불통 시에만 |

층은 없어도 된다 — 없는 폴더는 건너뛰고 있는 층만 합친다. 조회는 `find_folder_path` 라 **용어집을
찾는 것만으로 폴더가 생기지 않는다**.

**적는 법은 바른 용어 한 줄에 하나다.** 틀린 표기는 적지 않아도 된다 — 모델이 어떻게 잘못 들을지는
소유자가 미리 알 수 없고, 그것까지 적게 하는 순간 소유자가 모르는 오인식은 영영 고쳐지지 않는다.
전사 후 각 어절의 앞머리를 바른 용어와 **자모 단위로** 대조해, 가까우면 그 자리만 고친다
(`열기환기`·`열기완기` → `열교환기`, `항정기술하고` → `한전기술하고` — 조사·구두점은 그대로).

소리가 멀리 빗나간 오인식은 두 칸(`틀린표기,올바른표기`)으로 짚어 준다 — `영무,업무` 처럼. 두 형식은
같은 파일에 섞어 써도 되고, 두 칸 행의 **오른쪽도 바른 용어**로 취급돼 근접 교정의 목표가 된다. 그래서
이미 1:1 로 써 둔 용어집은 고쳐 적지 않아도 그대로 더 많이 잡는다(실측: 실제 회의 전사본에서 그 파일이
놓친 오인식 7건을 추가로 교정, 무관한 낱말 교체 0건 / 9,625 어절).

일부러 고치지 않는 것들 — 잘못 고치는 것이 안 고치는 것보다 나쁘다: 두 음절 미만 용어(닮은 낱말이 너무
많다), 음절 수가 다른 말(`고신뢰` 는 `고신뢰성` 이 아니다), 한 낱말이 두 용어에 동시에 가까운 경우,
그리고 낱말 **속** 조각(`선금` 을 적어도 `기성금` 은 안전하다 — 1:1 치환이 실제로 깨뜨렸던 그 사고다).

**무엇이 무엇으로 바뀌었는지는 로그에 남는다** — `~/.hermes/speechtotext/logs/corrections.jsonl` 에
바뀐 어절이 한 줄씩 적힌다(`kind` 로 소유자 치환/기계 교정을 가르고, `stage` 로 어느 경로가 고쳤는지).
문장·문맥은 적지 않고 파일은 0600 이다. 기계가 추측한 교정을 훑어보려면 그 파일에서 `kind=fuzzy` 만
보면 된다. 상세: [교정 로그](../../docs/기능소개/교정-로그.md).

`#` 로 시작하는 줄은 주석이고 **작성 예시는 [`configs/용어집.example.csv`](configs/용어집.example.csv) 에
각주로 달아 두었다**. `.csv` 인 이유는 Drive 가 표를 Sheets 로 열어 주기 때문이다(같은 이유로 회의
action item 원장도 `.csv` 다). 예전 `용어집.txt`(`틀린표기=올바른표기`)도 계속 읽으며, 한 폴더에 둘 다
있으면 `.csv` 가 이긴다.

기관명은 한 과제의 사실이지 모든 회의의 사실이 아니다 — 그래서 안쪽이 이긴다. 어느 층이든 소유자가
Drive 에서 직접 고치면 다음 전사부터 반영된다(노드에 손댈 일이 없다).

**과제는 파일 이름이 정한다**: `_` 로 나눈 토큰 중 **날짜가 아닌 첫 토큰**. `20260825_해양고신뢰성.m4a`
와 `해양고신뢰성_킥오프.m4a` 는 같은 과제이고, 이름이 날짜뿐이면 과제 없음(예전처럼 연도 폴더에
바로 놓인다). `--project` 로 언제든 덮어쓸 수 있다. 전사본은 `전사본/<과제>/<YYYY>/` 에, 회의록은
meeting 이 같은 과제 이름으로 `회의록/<과제>/<YYYY>/` 에 놓는다.

Drive 용어집 조회는 다른 Drive 접근과 같은 옵트인(`DRIVE_PUBLISH_ENABLED=1`)을 따른다 — 꺼져 있으면
노드 캐시만 쓴다. 그래서 캐시가 중요하다: plaud 라이프로그는 `DRIVE_PUBLISH_ENABLED=0` 으로 도는데,
마지막으로 받아 둔 Drive 용어집이 노드에 남아 있으면 그 경로도 같은 이름 교정을 받는다. Drive 가
답했는데 어느 층에도 용어집이 없으면 **비어 있는 것이 정답**이라 캐시도 비운다(`GLOSSARY-DRIVE-ABSENT`);
Drive 가 아예 답하지 않으면 캐시로 답하고 `GLOSSARY-FETCH-FAIL` 한 줄을 남긴다.

형식(두 계층 모두 같다) — 한 줄씩:

```
영무=업무
한정기술=한전기술
```

같은 파일이 **두 번** 쓰인다 — 전사 전에는 모델에게 `--prompt 고유명사: 업무, 한전기술` 힌트로,
전사 후에는 남은 오인식의 치환으로. 기본은 비어 있다: 추측한 이름을 프로덕션에 적으면 그
오인식을 오히려 굳힌다.

**치환은 단순 문자열 교체다** — 다른 단어의 일부가 되는 표기는 넣지 마라. 예: `성금=선금`은
이 회의의 핵심어 `기성금`을 `기선금`으로 깨뜨린다. 안전한 기준 하나: 올바른 표기가 같은
전사본에 이미 등장하면(모델이 그 단어를 알고 있다는 뜻) 그 쌍은 넣어도 좋다.

## Drive 감시 폴더

`SPEECHTOTEXT_DRIVE_FOLDER` 에 지정한 폴더만 본다(운영값 `autophagy/회의녹음` — 산출물과 같은 루트 아래의 유일한 입력 폴더). **미설정이면 아무것도 하지 않는다**
(어떤 폴더인지 추측하지 않는다). 소유자 본인만 접근 가능한 파일만 처리하고(공유된 파일은
`SPEECHTOTEXT-SKIP reason=not-owner-only`), 회의록 생성이 성공한 뒤에만 처리 완료로 기록해
실패한 틱은 다음 틱에 다시 시도한다. 폴링 대상은 Drive이므로 실시간 에이전트의 Discord
메시지와 경쟁하지 않는다.

전사본은 `~/.hermes/speechtotext/transcripts/`(0700)에 정본으로 남고,
`DRIVE_PUBLISH_ENABLED=1` 이면(끄려면 **값 `0`** — CLI 가 `~/.env.secrets` 를 환경에 싣기
때문에 `unset` 으로는 막히지 않는다) 공용 파사드로 `autophagy/전사본/<YYYY>/` 에
best-effort 발행된다(실패해도 로컬 전사본과 회의록은 그대로 진행).

## 설정

| 키 | 뜻 | 기본 |
|---|---|---|
| `SPEECHTOTEXT_DRIVE_FOLDER` | 감시할 Drive 폴더 경로(`/` 구분, 운영값 `autophagy/회의녹음`) | **없음 = 무동작** |
| `SPEECHTOTEXT_BACKEND` | `auto` / `local` / `api` | `auto` |
| `SPEECHTOTEXT_WHISPER_BIN` · `SPEECHTOTEXT_WHISPER_MODEL` | whisper.cpp 바이너리·ggml 모델 | PATH 의 `whisper-cli` / 없음 |
| `SPEECHTOTEXT_FFMPEG_BIN` · `SPEECHTOTEXT_FFPROBE_BIN` | 변환·길이 측정 | PATH |
| `SPEECHTOTEXT_LANGUAGE` | 언어 힌트 | `ko` |
| `SPEECHTOTEXT_MODEL` · `SPEECHTOTEXT_BASE_URL` | API 모델·엔드포인트 | `gpt-4o-transcribe` / OpenAI |
| `SPEECHTOTEXT_WHISPER_THREADS` · `SPEECHTOTEXT_LOCAL_TIMEOUT` | 스레드·상한(초) | CPU 수(≤16) / 14400 |
| `SPEECHTOTEXT_ALLOW_INCOMPLETE=1` | 누락·반복 붕괴 의심 전사본도 통과(확인 후에만) | off |
| `SPEECHTOTEXT_MAX_REPEAT` | 반복 붕괴 판정 임계(최다 8어절 점유율) | `0.08` |
| `SPEECHTOTEXT_WHISPER_CONTEXT` | whisper `-mc` 값. `-1`이면 문맥 이월 복원 | `0`(이월 끔) |
| `SPEECHTOTEXT_PROMPT` | 고유명사 힌트(로컬·API 양쪽에 전달). 미설정 시 용어집에서 만든다 | 없음 |
| `SPEECHTOTEXT_GLOSSARY` | 용어집 파일을 **명시**하면 Drive 를 조회하지 않는다(샌드박스·오프라인) | 미설정 = Drive 정본 + 노드 캐시 |
| `SPEECHTOTEXT_CORRECTION_LOG` | 교정 로그 경로(무엇이 무엇으로 바뀌었는지) | `~/.hermes/speechtotext/logs/corrections.jsonl` |
| `SPEECHTOTEXT_TRANSCRIPT_DIR` · `SPEECHTOTEXT_STATE_FILE` | 전사본·처리 상태 | `~/.hermes/speechtotext/` |
| `SPEECHTOTEXT_DIARIZE_BIN` | sherpa-onnx 화자 분리 바이너리. **셋 중 하나라도 비면 화자 분리를 하지 않는다** | 없음 = 화자 없음 |
| `SPEECHTOTEXT_DIARIZE_SEGMENTATION` | pyannote segmentation onnx 모델 경로 | 없음 |
| `SPEECHTOTEXT_DIARIZE_EMBEDDING` | 화자 임베딩 onnx 모델 경로 | 없음 |
| `SPEECHTOTEXT_DIARIZE_THRESHOLD` | 군집 임계값(화자 수 미지정일 때만 사용) | `0.9` |
| `SPEECHTOTEXT_DIARIZE_THREADS` | segmentation·embedding 스레드 수 | CPU 수(≤8) |
| `SPEECHTOTEXT_DIARIZE_TIMEOUT` | 화자 분리 상한(초). 넘기면 `DIARIZE-FAIL` 후 계속 | `3600` |

## 로컬 전사 도구 설치 (노드 1회)

```bash
git clone https://github.com/ggml-org/whisper.cpp && cd whisper.cpp
cmake -B build && cmake --build build -j --config Release      # build/bin/whisper-cli
sh ./models/download-ggml-model.sh large-v3-turbo-q5_0          # 약 547MiB
```

`~/.env.secrets` 또는 cron 환경에 `SPEECHTOTEXT_WHISPER_BIN`·`SPEECHTOTEXT_WHISPER_MODEL`
을 지정하면 그 다음 틱부터 로컬 경로로 전사한다. `ffmpeg`/`ffprobe` 는 두 경로 모두에서
필요하다(16kHz mono 변환·길이 측정). 품질을 더 원하면 `large-v3-q5_0`(약 1.1GiB), 처리량이
급하면 `medium`. 처리 시간은 CPU 에서 실시간의 1~5배가 걸릴 수 있으므로 cron 틱이 겹치지
않도록 워처가 flock 을 건다.

### GPU 빌드 (NVIDIA GB10 노드, 2026-09-01)

CPU 빌드는 `build/` 에 폴백으로 남기고, `SPEECHTOTEXT_WHISPER_BIN` 은 CUDA 빌드를 가리킨다:

```bash
cmake -B build-cuda -DGGML_CUDA=1 -DCMAKE_CUDA_ARCHITECTURES=121   # CUDA 13.0
cmake --build build-cuda -j --config Release                        # build-cuda/bin/whisper-cli
```

### 화자 분리 도구 설치 (노드 1회)

sherpa-onnx v1.13.6 CPU 빌드(`linux-aarch64-shared-cpu` 자산)를 agent 계정 홈 `~/sherpa-onnx/` 아래에 둔다:

```
sherpa-onnx/
  bin/    화자 분리 실행 파일 (SPEECHTOTEXT_DIARIZE_BIN)
  lib/    공유 라이브러리 (CLI 가 LD_LIBRARY_PATH 에 자동으로 얹는다)
  models/ sherpa-onnx-pyannote-segmentation-3-0/model.onnx
          3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx
  samples/
```

두 모델 경로를 `SPEECHTOTEXT_DIARIZE_SEGMENTATION`·`SPEECHTOTEXT_DIARIZE_EMBEDDING` 에
적으면 다음 틱부터 화자 블록이 붙는다. 화자 분리는 CPU 에서 돌고 전사는 GPU 에서 도므로
둘이 자원을 놓고 다투지 않는다.

## 관련

- 회의록 생성 본체: [`meeting`](../meeting/SKILL.md) — 민감도 게이트·칸반·통지·Drive 발행 소유
- 발행 규약: [`drive-publish`](../../docs/guide/drive-publish.md)
- 워처 규약: [`watcher-cron-설계규약`](../../docs/guide/watcher-cron-설계규약.md)
- 소개: [`음성-녹취-회의록-자동화`](../../docs/기능소개/음성-녹취-회의록-자동화.md) ·
  [`전사본-화자-구분과-문장-단위-출력`](../../docs/기능소개/전사본-화자-구분과-문장-단위-출력.md)
