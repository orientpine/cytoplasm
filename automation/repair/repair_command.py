"""Manual repair-command parsing without routing ordinary conversation."""

from __future__ import annotations

from dataclasses import dataclass

from automation.repair.repair_redaction import digest, redact


@dataclass(frozen=True, slots=True)
class ManualRepairCommand:
    message: str


def parse_repair_command(text: str) -> ManualRepairCommand | None:
    """Recognize the supported manual repair phrases and retain an opaque message."""
    normalized = text.strip()
    if normalized == "!repair":
        return ManualRepairCommand(message="manual !repair request")
    if normalized.startswith("!repair "):
        return ManualRepairCommand(message=normalized.removeprefix("!repair ").strip())
    if "수리해줘" in normalized or "이상해" in normalized:
        return ManualRepairCommand(message=normalized)
    return None


def manual_location(message: str) -> str:
    """Bind a manual request's dedup signature to the whole message, not its first word.

    The signature is digest(source, location, first token of the excerpt) and a manual
    repair carries constant source and location, so the first word alone decided which
    card a request joined. 2026-09-09 실측: "캘린더 …" 와 "메일 …" 로 시작한 두 요청이 7월·8월에
    종결된 무관한 카드로 각각 빨려 들어가 보드에서 사라졌다.

    같은 요청을 다시 말하면 여전히 한 카드로 모여야 하므로 공백만 접어 정규화하고, 마스킹한
    뒤에 요약하므로 토큰 모양 문자열은 서명에도 남지 않는다.
    """
    return f"gateway-command:{digest(' '.join(redact(message).split()))}"
