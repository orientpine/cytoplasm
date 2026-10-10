"""소유자 대리 요청의 I/O 어댑터 — 1회 수락 원장과 원문 조회. 판정은 `owner_proxy` 가 한다."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LEDGER_RETENTION_SECONDS: Final = 2 * 24 * 60 * 60
_DISCORD_API: Final = "https://discord.com/api/v10"
_USER_AGENT: Final = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"


@dataclass(frozen=True, slots=True)
class FileLedger:
    """원문 id 별 1회 수락 원장 — `{id: 수락 시각}` JSON 을 flock 아래 원자 교체한다."""

    path: Path

    def claim(self, origin_message_id: str, now: float) -> bool:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = self.path.with_suffix(".lock")
        with lock.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            seen = self._read()
            if origin_message_id in seen:
                return False
            kept = {key: at for key, at in seen.items() if now - at < LEDGER_RETENTION_SECONDS}
            kept[origin_message_id] = now
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(kept, sort_keys=True), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            return True

    def _read(self) -> dict[str, float]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as error:
            raise OSError("owner proxy ledger is unreadable") from error
        if not isinstance(payload, dict):
            raise OSError("owner proxy ledger is not an object")
        return {str(key): float(value) for key, value in payload.items() if isinstance(value, int | float)}


@dataclass(frozen=True, slots=True)
class DiscordOriginReader:
    """원문 조회 — 읽기 전용 GET 두 번. 실패는 예외로 올려 `verify` 가 fail-closed 로 바꾼다."""

    token: str
    timeout: float = 5.0

    def channel(self, channel_id: str) -> Mapping[str, object]:
        return self._get(f"/channels/{channel_id}")

    def message(self, channel_id: str, message_id: str) -> Mapping[str, object]:
        return self._get(f"/channels/{channel_id}/messages/{message_id}")

    def _get(self, path: str) -> Mapping[str, object]:
        request = Request(
            f"{_DISCORD_API}{path}",
            headers={"Authorization": f"Bot {self.token}", "User-Agent": _USER_AGENT},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as error:
            raise OSError(f"discord origin lookup failed: {type(error).__name__}") from error
        if not isinstance(payload, dict):
            raise OSError("discord origin lookup returned a non-object")
        return payload
