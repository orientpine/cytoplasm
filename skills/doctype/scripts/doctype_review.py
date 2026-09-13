"""Controlled owner-DM review transport; no submission or other outbound path."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


class DeliveryError(RuntimeError):
    """The controlled owner review request could not be delivered."""


def resolve_target(explicit: str = "") -> str:
    """Resolve the owner-only Hermes DM target without committing an account id."""
    if explicit:
        return explicit
    configured = os.environ.get("DOCTYPE_DM_TARGET", "")
    if configured:
        return configured
    path = Path("~/.hermes/doctype/config.json").expanduser()
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise DeliveryError("doctype DM configuration is invalid") from error
        target = payload.get("dm_target") if isinstance(payload, dict) else None
        if isinstance(target, str) and target:
            return target
    if os.environ.get("DOCTYPE_DM_DISABLED") == "1":
        return ""
    raise DeliveryError("DOCTYPE_DM_TARGET is required for an owner review DM")


def _chunks(message: str) -> tuple[str, ...]:
    chunks: list[str] = []
    remaining = message
    while len(remaining) > 1800:
        boundary = remaining.rfind("\n", 0, 1800)
        split_at = boundary if boundary > 0 else 1800
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return (*chunks, remaining)


def send_review(target: str, message: str, file: Path | None = None) -> None:
    """문서 좌표를 렌더하되 옛 런타임과 좌표 없는 호출은 원문을 보낸다."""
    if not target:
        return
    content = message
    if file is not None:
        try:
            from automation.interop.owner_message import Action, OwnerMessage, OwnerMessageError, Ref, Result, render
        except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
            content = message
        else:
            location = Ref(scope="resource", search=("문서 검색", file.name))
            try:
                content = render(OwnerMessage(
                    subject_key=str(file), subject="서류 초안", fact="검토 요청",
                    location=location, owner=Action("open", target=location, argument="검토·제출은 직접"),
                    agent_next=None, recovery="not_applicable", detail=Result("executed"),
                ), destination=Ref(scope="none"))
            except OwnerMessageError:
                content = message
    binary = os.environ.get("DOCTYPE_DM_HERMES_BIN", "hermes")
    environment = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"}
    for chunk in _chunks(content):
        try:
            completed = subprocess.run(
                (binary, "send", "--to", target, chunk),
                cwd=Path.home(),
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DeliveryError(error.__class__.__name__) from error
        if completed.returncode != 0:
            raise DeliveryError(f"owner DM rc={completed.returncode}")
