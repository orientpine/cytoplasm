---
name: speechtotext
description: "Google Drive 폴더에 올려둔 음성 녹취를 전사본(.md)으로 만들고, 그 전사본을 meeting 스킬로 넘겨 회의록까지 잇는 스킬. 전사는 기본이 로컬(whisper.cpp)이고, 2시간이 넘는 단일 녹취도 누락 검증을 통과해야만 회의록으로 넘어간다."
version: 1.0.0
author: autophagy-agents
---

# speechtotext — 음성 → 전사본(.md) → 회의록

소유자가 **감시 폴더에 녹취 파일을 놓으면** 5분 틱 워처가 그것을 집어 전사본을 만들고
곧바로 `meeting` 스킬로 넘겨 회의록·칸반·마일스톤까지 잇는다. 폴더에 파일을 놓는 행위가
`!meeting` 토큰과 같은 **명시 지시**이며, 그래서 별도 승인 게이트를 두지 않는다.

## 동작 방식 (에이전트가 지켜야 할 규칙)

1. **전사본 원문을 내 컨텍스트/응답에 붙여넣지 마라.** 회의 내용은 CLI 안에서만 흐르고
   결과는 JSON 요약(전사본 경로·글자수·커버리지·meeting 종료코드)으로만 읽는다.
2. 소유자가 **로컬 음성 파일 경로**를 대며 회의록을 요청하면 아래를 실행한다:

   ```bash
   python3 ~/.hermes/skills/speechtotext/scripts/speechtotext_cli.py ingest \
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
| `api` | OpenAI 호환 `/v1/audio/transcriptions` (LiteLLM 게이트웨이도 같은 경로) |

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

- 문장 경계로 나누고 문단으로 묶는다(문단은 4문장·180자를 모두 넘겨야 닫힌다).
- **연속으로 완전히 같은 문장**만 하나로 접는다. 나중에 다시 나오는 같은 말은 사람이 다시
  한 것이므로 남긴다 — 이 스킬의 반복 원칙 그대로, 중복은 불편이고 누락은 실패다.
- 용어집으로 고유명사를 바로잡는다.
- **요약하지 않는다.** 결정사항·액션아이템·마일스톤을 읽어내는 일은 meeting 의 몫이고
  이 경계를 넘지 않는다.

이미 있는 전사본은 오디오·모델·비용 없이 다시 다듬을 수 있다(멱등):

```bash
python3 ~/.hermes/skills/speechtotext/scripts/speechtotext_cli.py polish --file <전사본.md>
```

### 용어집 — 이름을 한 곳에만 적는다

용어집은 **두 계층**이다.

| 어디에 | 무엇을 | 우선순위 |
|---|---|---|
| Drive `autophagy/전사본/<과제>/용어집.txt` | 그 과제의 기관·업체·사람·장비 이름 | 높음 |
| 노드 `~/.hermes/speechtotext/glossary.txt`(`SPEECHTOTEXT_GLOSSARY`) | 과제와 무관한 일반 오인식(영무→업무) | 낮음 |

과제 용어집이 같은 표기를 다르게 적으면 **과제 쪽이 이긴다** — 기관명은 한 과제의 사실이지 모든
회의의 사실이 아니다. 과제 용어집은 소유자가 Drive 에서 직접 고치면 다음 전사부터 반영된다.

**과제는 파일 이름이 정한다**: `_` 로 나눈 토큰 중 **날짜가 아닌 첫 토큰**. `20260825_해양고신뢰성.m4a`
와 `해양고신뢰성_킥오프.m4a` 는 같은 과제이고, 이름이 날짜뿐이면 과제 없음(예전처럼 연도 폴더에
바로 놓인다). `--project` 로 언제든 덮어쓸 수 있다. 전사본은 `전사본/<과제>/<YYYY>/` 에, 회의록은
meeting 이 같은 과제 이름으로 `회의록/<과제>/<YYYY>/` 에 놓는다.

Drive 용어집 조회는 다른 Drive 접근과 같은 옵트인(`DRIVE_PUBLISH_ENABLED=1`)을 따른다 — 꺼져 있으면
과제 용어집 없이 전역 용어집만 쓴다.

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
| `SPEECHTOTEXT_GLOSSARY` | 용어집 파일(`틀린표기=올바른표기`) | `~/.hermes/speechtotext/glossary.txt` |
| `SPEECHTOTEXT_TRANSCRIPT_DIR` · `SPEECHTOTEXT_STATE_FILE` | 전사본·처리 상태 | `~/.hermes/speechtotext/` |

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

## 관련

- 회의록 생성 본체: [`meeting`](../meeting/SKILL.md) — 민감도 게이트·칸반·통지·Drive 발행 소유
- 발행 규약: [`drive-publish`](../../docs/guide/drive-publish.md)
- 워처 규약: [`watcher-cron-설계규약`](../../docs/guide/watcher-cron-설계규약.md)
- 소개: [`음성-녹취-회의록-자동화`](../../docs/기능소개/음성-녹취-회의록-자동화.md)
