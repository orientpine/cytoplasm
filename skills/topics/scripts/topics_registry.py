from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .topics_sensitivity import TagRule, evaluate, load_rules as _load_rules

DEFAULT_STATE_PATH = Path.home() / ".hermes" / "state" / "research-topics.yaml"
GENERALIZATION_GUIDANCE = (
    "민감 연구어는 등록하지 않았고 외부 arXiv 조회로 전송되지 않습니다. "
    "구체적 권리·출원 맥락 대신 일반 연구 분야명으로 바꿔 보세요."
)
_TOPIC_LINE = re.compile(r"^  - (.+)$")


class RegistryError(RuntimeError):
    pass


class TopicInputError(RegistryError):
    pass


@dataclass(frozen=True, slots=True)
class TopicDecision:
    accepted: bool
    duplicate: bool = False
    guidance: str = ""
    topic: str = ""


def default_rules_path() -> Path:
    source_path = Path(__file__).resolve().parents[3] / "configs" / "sensitivity-rules.yaml"
    if source_path.exists():
        return source_path
    return Path.home() / ".hermes" / "sensitivity-rules.yaml"


def load_rules(path: Path | None = None) -> tuple[TagRule, ...]:
    return _load_rules(path or default_rules_path())


def normalize_topic(topic: str) -> str:
    normalized = " ".join(topic.split())
    if len(normalized) < 2:
        raise TopicInputError("topic must contain at least two characters")
    if len(normalized) > 160:
        raise TopicInputError("topic must be at most 160 characters")
    return normalized


def _read_topics(path: Path) -> tuple[str, ...]:
    if not path.exists():
        return ()
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "version: 1" or len(lines) < 2 or lines[1] != "topics:":
        raise RegistryError(f"invalid topic registry: {path}")
    topics: list[str] = []
    for line in lines[2:]:
        match = _TOPIC_LINE.fullmatch(line)
        if match is None:
            raise RegistryError(f"invalid topic registry entry: {path}")
        decoded = json.loads(match.group(1))
        if not isinstance(decoded, str):
            raise RegistryError(f"topic must be a string: {path}")
        topics.append(normalize_topic(decoded))
    return tuple(topics)


def list_topics(path: Path = DEFAULT_STATE_PATH) -> tuple[str, ...]:
    return _read_topics(path)


def _write_topics(path: Path, topics: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    body = "version: 1\ntopics:\n" + "".join(
        f"  - {json.dumps(topic, ensure_ascii=False)}\n" for topic in topics
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(body, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def validate_suggestion(topic: str, rules: tuple[TagRule, ...]) -> TopicDecision:
    normalized = normalize_topic(topic)
    if evaluate(normalized, rules).sensitive:
        return TopicDecision(accepted=False, guidance=GENERALIZATION_GUIDANCE)
    return TopicDecision(accepted=True, topic=normalized)


def add_topic(path: Path, topic: str, rules: tuple[TagRule, ...]) -> TopicDecision:
    decision = validate_suggestion(topic, rules)
    if not decision.accepted:
        return decision
    existing = _read_topics(path)
    if any(item.casefold() == decision.topic.casefold() for item in existing):
        return TopicDecision(accepted=True, duplicate=True, topic=decision.topic)
    _write_topics(path, (*existing, decision.topic))
    return decision


def remove_topic(path: Path, topic: str) -> bool:
    normalized = normalize_topic(topic)
    existing = _read_topics(path)
    kept = tuple(item for item in existing if item.casefold() != normalized.casefold())
    if len(kept) == len(existing):
        return False
    _write_topics(path, kept)
    return True
