"""원장은 공용 판독기로 읽고, 성공한 편집의 지문·범례만 비공개 정답 옆에 남긴다."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from automation.plaud_sync.audio_manifest import outside_checkout, rows
from automation.stt_eval.snapshot import atomic_publish

from automation.stt_eval.cron.capture_text import Parsed, Surface


@dataclass(frozen=True, slots=True)
class Binding:
    digest: str
    duration_ms: int


def bindings(path: Path) -> dict[tuple[Surface, str], Binding]:
    result: dict[tuple[Surface, str], Binding] = {}
    for row in rows(path):
        domain, stem = row.get("domain"), row.get("transcript_stem")
        digest, duration = row.get("audio_sha256"), row.get("duration_ms")
        if domain not in ("lifelog", "meeting") or not isinstance(stem, str) or not stem:
            raise ValueError("capture manifest fields")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("capture manifest digest")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
            raise ValueError("capture manifest duration")
        surface: Surface = "lifelog" if domain == "lifelog" else "meeting"
        key = surface, unicodedata.normalize("NFC", stem)
        binding = Binding(digest, duration)
        if key in result and result[key] != binding:
            raise ValueError("capture ambiguous manifest stem")
        result[key] = binding
    return result


def fingerprint(parsed: Parsed) -> str:
    return hashlib.sha256(json.dumps(asdict(parsed), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def state_path(root: Path, digest: str) -> Path:
    return outside_checkout(root / "reference" / ".capture" / f"{digest}.json")


def previous(root: Path, digest: str) -> str | None:
    path = state_path(root, digest)
    if not path.exists():
        return None
    raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(raw, dict):
        raise ValueError("capture state shape")
    data = cast(dict[str, object], raw)
    if set(data) != {"fingerprint", "names"}:
        raise ValueError("capture state shape")
    value = data["fingerprint"]
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("capture state fingerprint")
    return value


def mark(root: Path, digest: str, parsed: Parsed) -> None:
    """호출자는 정답 저장 성공 뒤에만 호출한다. EvalRecord 직렬화 사본은 아니다."""
    payload = json.dumps({"fingerprint": fingerprint(parsed), "names": parsed.names}, ensure_ascii=False, sort_keys=True) + "\n"
    _ = atomic_publish(root, state_path(root, digest), lambda path: path.write_text(payload, encoding="utf-8"))
