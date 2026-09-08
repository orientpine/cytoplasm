"""릴리스가 실제 운영환경에 적용되면 소유자 DM 으로 한 번 알린다 (2026-09-05 지시).

**승인 반응은 적용이 아니다.** ✅ 는 "잘라도 좋다"는 허락일 뿐이고, 태그가 잘린 뒤에도
전량 반영은 막힐 수 있다(샌드박스 블록·수렴 실패). 그래서 적용 판정의 근거를 셋으로
둔다 — 완결기의 성공 마커(`completed/<sha>`, `release.sh` + `deploy_all --apply` 성공
뒤에만 쓰인다) · 노드의 활성 릴리스 포인터 · 전량 재판정 영수증(RC-4). 셋이 모두 이
sha 를 가리킬 때만 "적용되었습니다"가 참이다.

**번호는 추정하지 않는다.** 그 sha 에 달린 릴리스 태그가 유일한 출처다. 없으면 다음
틱을 기다리고, 둘이면(실측: f00f334ca 의 v1.1.5+v1.2.0) 영구 보류로 남겨 사람이 고른다.

**지나간 릴리스는 알리지 않는다.** 완결 마커는 과거 sha 에도 남아 있다 — 활성 포인터의
조상이면 그 릴리스는 지금 돌고 있지 않으므로 SUPERSEDED 로 기록만 하고 보내지 않는다.

절반은 워크스테이션(`sweep`, 완결 타이머가 매 틱 호출)에서, 절반은 노드(`send`,
`release_approval_remote.sh` 가 agent 자격으로 실행)에서 돈다. DM 오픈은 언제나
`owner_notice` 파사드 안에서만 일어난다(ON-2/ON-3).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, assert_never

from automation import owner_notice

_RELEASE_TAG: Final = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
_DEFAULT_RELEASE_CURRENT: Final = "/srv/autophagy-agent-current"
_MODULE: Final = "automation.release_applied_notice"


@dataclass(frozen=True, slots=True)
class Applied:
    """적용이 확인됐다 — 이 버전으로 통지한다."""

    version: str


@dataclass(frozen=True, slots=True)
class Skip:
    """다시 볼 필요가 없다 — 마커를 남겨 매 틱 되풀이하지 않는다."""

    reason: str


@dataclass(frozen=True, slots=True)
class Retry:
    """아직 모른다 — 마커를 남기지 않고 다음 틱에 다시 본다."""

    reason: str


Decision = Applied | Skip | Retry


@dataclass(frozen=True, slots=True)
class ReleaseFacts:
    """판정에 필요한 사실 전부 — 여기 없는 것은 판정에 쓰이지 않는다."""

    sha: str
    version_tags: tuple[str, ...]
    pointer_sha: str | None
    receipt_sha: str | None
    superseded: bool


def decide(facts: ReleaseFacts) -> Decision:
    """순수 판정. 순서가 곧 정책이다 — 앞선 조건이 뒤를 가린다."""
    if facts.superseded:
        return Skip("SUPERSEDED")
    if not facts.version_tags:
        return Retry("NO-TAG")
    if len(facts.version_tags) > 1:
        return Skip("VERSION-AMBIGUOUS")
    if facts.pointer_sha is None:
        return Retry("POINTER-UNREADABLE")
    if facts.pointer_sha != facts.sha:
        return Retry("POINTER-BEHIND")
    if facts.receipt_sha is None:
        return Retry("RECEIPT-MISSING")
    if facts.receipt_sha != facts.sha:
        return Retry("RECEIPT-STALE")
    return Applied(facts.version_tags[0])


def parse_probe(output: str) -> tuple[str | None, str | None]:
    """`readlink` 한 줄 + 영수증 JSON → (포인터 sha, 영수증 sha). 판독 불가는 전부 None."""
    brace = output.find("{")
    head = (output if brace < 0 else output[:brace]).strip()
    pointer_line = head.splitlines()[-1].strip() if head else ""
    pointer = pointer_line.rsplit("/", 1)[-1] if "/" in pointer_line else None
    if brace < 0:
        return pointer, None
    try:
        document = json.loads(output[brace:])
    except ValueError:
        return pointer, None
    recorded = document.get("release_sha") if isinstance(document, dict) else None
    return pointer, recorded if isinstance(recorded, str) and recorded else None


def release_tags(repo: Path, sha: str) -> tuple[str, ...]:
    """그 커밋을 가리키는 릴리스 태그들 — prerelease 접미사는 번호가 아니다."""
    listed = subprocess.run(
        ("git", "-C", str(repo), "tag", "--points-at", sha),
        capture_output=True,
        text=True,
        check=False,
    )
    return tuple(
        name for line in listed.stdout.splitlines() if _RELEASE_TAG.match(name := line.strip())
    )


def _is_ancestor(repo: Path, sha: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ("git", "-C", str(repo), "merge-base", "--is-ancestor", sha, descendant),
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def _log(message: str) -> None:
    print(f"[release-complete] {message}")


def _probe_command() -> list[str]:
    configured = os.environ.get("RELEASE_APPLIED_PROBE_CMD", "").strip()
    if configured:
        return shlex.split(configured)
    from automation.node_config import load_node_config

    node = load_node_config()
    return [
        "ssh",
        node.deploy_ssh_host,
        f"readlink {node.release_current}; "
        f"sudo -n -u {node.ops_account} -H cat {node.private_root}/deploy-all/receipt.json",
    ]


def _probe() -> tuple[str | None, str | None]:
    """노드 사실을 한 번만 읽는다 — 포인터도 영수증도 노드 전역이라 sha 마다 찌를 이유가 없다."""
    result = subprocess.run(_probe_command(), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None, None
    return parse_probe(result.stdout)


def _send_command(repo: Path) -> list[str]:
    configured = os.environ.get("RELEASE_APPROVAL_CMD", "").strip()
    if configured:
        return shlex.split(configured)
    return [str(repo / "automation" / "release_approval_remote.sh")]


@dataclass(frozen=True, slots=True)
class _Sweep:
    """한 틱분의 사실 — 완결 마커 목록을 이 노드 상태에 비추어 판정한다."""

    state: Path
    repo: Path
    pointer_sha: str | None
    receipt_sha: str | None

    def handle(self, sha: str) -> None:
        facts = ReleaseFacts(
            sha=sha,
            version_tags=release_tags(self.repo, sha),
            pointer_sha=self.pointer_sha,
            receipt_sha=self.receipt_sha,
            superseded=self._superseded(sha),
        )
        decision = decide(facts)
        match decision:
            case Skip(reason):
                _mark(self.state, "notify-skipped", sha, reason)
                _log(f"RELEASE-APPLIED-SKIP {reason} {sha[:12]}")
            case Retry(reason):
                _log(f"RELEASE-APPLIED-RETRY {reason} {sha[:12]}")
            case Applied(version):
                self._notify(sha, version)
            case unreachable:
                assert_never(unreachable)

    def _superseded(self, sha: str) -> bool:
        if self.pointer_sha is None or self.pointer_sha == sha:
            return False
        return _is_ancestor(self.repo, sha, self.pointer_sha)

    def _notify(self, sha: str, version: str) -> None:
        completed = subprocess.run(
            (*_send_command(self.repo), "send", "--version", version, "--head", sha),
            env={**os.environ, "RELEASE_APPROVAL_MODULE": _MODULE},
            check=False,
        )
        if completed.returncode != 0:
            _log(f"RELEASE-APPLIED-SEND-FAIL rc={completed.returncode} {sha[:12]}")
            return
        stamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        _mark(self.state, "notified", sha, f"{version} {stamp}")
        _log(f"RELEASE-APPLIED-NOTIFIED {version} {sha[:12]}")


def _mark(state: Path, kind: str, sha: str, body: str) -> None:
    directory = state / kind
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / sha
    _ = marker.write_text(f"{body}\n", encoding="utf-8")
    marker.chmod(0o600)


def _unnotified(state: Path) -> tuple[str, ...]:
    completed = state / "completed"
    if not completed.is_dir():
        return ()
    return tuple(
        sorted(
            entry.name
            for entry in completed.iterdir()
            if entry.is_file()
            and not (state / "notified" / entry.name).exists()
            and not (state / "notify-skipped" / entry.name).exists()
        )
    )


def sweep(state: Path, repo: Path) -> None:
    """완결된 릴리스 중 아직 통지하지 않은 것만 판정한다. 대상이 없으면 노드도 찌르지 않는다."""
    candidates = _unnotified(state)
    if not candidates:
        return
    pointer_sha, receipt_sha = _probe()
    tick = _Sweep(state=state, repo=repo, pointer_sha=pointer_sha, receipt_sha=receipt_sha)
    for sha in candidates:
        tick.handle(sha)


def send(version: str, head: str) -> int:
    """노드에서 도는 절반 — 포인터를 다시 확인하고 파사드에 DM 을 맡긴다."""
    current = os.environ.get("NODE_RELEASE_CURRENT", "").strip() or _DEFAULT_RELEASE_CURRENT
    try:
        pointer = os.path.basename(os.readlink(current))
    except OSError:
        pointer = ""
    if pointer != head:
        print(
            f"[release-applied] APPLIED-POINTER-MISMATCH {pointer or 'unreadable'} "
            f"!= {head[:12]}",
            file=sys.stderr,
        )
        return 4
    delivered = owner_notice.notify_owner_dm(
        f"릴리스 {version} 가 적용되었습니다. (HEAD {head[:12]})"
    )
    return 0 if delivered else 3


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release-applied-notice")
    commands = parser.add_subparsers(dest="command", required=True)
    sweeper = commands.add_parser("sweep", help="완결된 릴리스의 적용 여부를 판정한다")
    _ = sweeper.add_argument("--state", required=True)
    _ = sweeper.add_argument("--repo", required=True)
    sender = commands.add_parser("send", help="소유자 DM 으로 적용 완료를 알린다")
    _ = sender.add_argument("--version", required=True)
    _ = sender.add_argument("--head", required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "send":
        return send(str(arguments.version), str(arguments.head))
    try:
        sweep(Path(str(arguments.state)), Path(str(arguments.repo)))
    except Exception as error:  # noqa: BLE001 - 통지 실패가 완결을 막으면 본말전도다
        _log(f"RELEASE-APPLIED-SWEEP-FAIL {type(error).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
