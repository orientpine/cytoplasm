"""로컬 전사의 단어 배정과 문장 조립 순서를 잇는다. 문서 문법은 stt_blocks에 맡긴다."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from itertools import groupby

import stt_attribute
import stt_blocks
import stt_diarize
import stt_gap
import stt_split


def sentences(
    words: Sequence[stt_blocks.TimedWord], turns: Sequence[stt_diarize.Turn],
) -> tuple[stt_blocks.TimedSentence, ...]:
    """토큰 시각이 있는 입력은 배정부터 하고, 세그먼트 전용 입력만 옛 경로로 보낸다."""
    if not any(word.timing_source in {"token", "aligned"} for word in words):
        return _legacy(stt_blocks.sentences_from_words(words), turns)
    tags: dict[int, stt_attribute.SpeakerTag] = {}
    # 누락 표지는 발화가 아니므로 BPE 묶음과 근거 구간 양쪽에서 제외한다.
    for marker, indices in groupby(range(len(words)), key=lambda i: stt_gap.is_marker(words[i].text)):
        run = tuple(indices)
        if marker:
            continue
        attributed = stt_attribute.attribute_words(
            words[run[0]:run[-1] + 1], turns, policy=stt_attribute.AttributionPolicy(),
        )
        tags.update((item.source_index + run[0], item.tag) for item in attributed)
    made: list[stt_blocks.TimedSentence] = []
    available = tuple(turns)
    # 문장을 **먼저** 만들고 그 다음에 라벨을 정한다. 예전에는 낱말 판정으로 먼저 묶어서
    # (groupby(tag)) 문장이 만들어지기도 전에 잘렸다 — 화자1 런 한가운데의 근거 없는 낱말
    # 하나가 런을 셋으로 가르고 그 조각이 각각 블록이 됐다. 노드 실측(2026-09-07)에서 낱말의
    # 비-SPEAKER 비율 12.0%·20.5% 가 블록의 화자0 비율 47.6%·49.7% 로, 두 녹음 모두 정확히
    # 2.4배 증폭됐다. 배율이 같다는 것이 원인은 판정 임계값이 아니라 이 조립 순서라는 증거다.
    # 진짜 화자 교대는 split_on_turns 가 계속 가른다 — 이제 빈 turn 대신 실제 turn 을 준다.
    for marker, indices in groupby(range(len(words)), key=lambda i: stt_gap.is_marker(words[i].text)):
        run = tuple(indices)
        chunk = stt_blocks.sentences_from_words(words[run[0]:run[-1] + 1])
        for sentence in stt_split.split_on_turns(chunk, available):
            # 원본 색인을 키로 써서 BPE 판정·구두점·미상 시각을 그대로 운반한다.
            refs = tuple(replace(ref, source_index=ref.source_index + run[0])
                         for ref in sentence.words)
            if marker or stt_gap.is_marker(sentence.text):
                made.append(replace(sentence, words=refs))
                continue
            tag = _spoken_by(refs, tags)
            made.append(replace(sentence, speaker=tag.label, attribution=tag, words=refs))
    return tuple(made)


# 낱말 판정은 여기서 사라지지 않는다 — refs 가 원본 색인을 들고 있어 그대로 남는다. 여기서
# 정하는 것은 문서가 헤더 한 줄에 적을 수 있는 **하나의** 라벨이고, 그 단위는 문장이다.
# 근거는 문장 밖으로 나가지 않으므로 금지된 '직전 화자 상속' 이 아니다: 앞 문장이 무엇이든
# 이 문장의 낱말만 센다. 동률이면 화자를 지어내지 않고 미상으로 둔다.
def _spoken_by(
    refs: Sequence[stt_blocks.SentenceWord],
    tags: dict[int, stt_attribute.SpeakerTag],
) -> stt_attribute.SpeakerTag:
    """문장 하나의 화자 — 그 문장 안의 낱말 판정만으로 정한다."""
    spoken: Counter[str] = Counter()
    overlaps = 0
    participants: list[str] = []
    for ref in refs:
        tag = tags.get(ref.source_index)
        if tag is None:
            continue
        if tag.kind == "SPEAKER":
            spoken[tag.speakers[0]] += 1
        elif tag.kind == "OVERLAP":
            overlaps += 1
            participants.extend(name for name in tag.speakers if name not in participants)
    ranked = spoken.most_common()
    if ranked and ranked[0][1] > overlaps and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
        return stt_attribute.SpeakerTag("SPEAKER", (ranked[0][0],))
    if overlaps and participants:
        return stt_attribute.SpeakerTag("OVERLAP", tuple(participants))
    return stt_attribute.SpeakerTag("UNKNOWN")


def _legacy(
    source: Sequence[stt_blocks.TimedSentence], turns: Sequence[stt_diarize.Turn],
) -> tuple[stt_blocks.TimedSentence, ...]:
    """옛 배정기의 분할·라벨을 재사용하되 직전 화자는 근거로 인정하지 않는다."""
    available = tuple(turns)
    made: list[stt_blocks.TimedSentence] = []
    for sentence in stt_diarize.assign(source, available):
        if stt_gap.is_marker(sentence.text):
            made.append(sentence)
            continue
        # 단독 배정에는 상속할 직전 문장이 없다. 라벨 순서는 전체 배정 결과를 유지한다.
        supported = any(piece.speaker for piece in stt_diarize.assign((sentence,), available))
        tag = (stt_attribute.SpeakerTag("SPEAKER", (sentence.speaker,))
               if supported else stt_attribute.SpeakerTag("UNKNOWN"))
        made.append(replace(sentence, speaker=tag.label, attribution=tag))
    return tuple(made)
