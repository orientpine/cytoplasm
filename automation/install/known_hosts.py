"""Name the host-key prerequisite the clone needs — without deciding it.

`SystemMutator._repository` clones the deploy checkout with
``StrictHostKeyChecking=yes`` under the ops account's HOME. That is the correct
setting, and it is also the reason a fresh node cannot clone: the installer
creates the ops account and its deploy key, but nothing puts the origin's host
key into ``~ops/.ssh/known_hosts``. What the operator sees today is ssh failing
somewhere inside git, surfaced as a bare exception type.

**This module never seeds the file.** Writing a host key here would mean the
installer decides which key is genuine, and that is the one judgement
``docs/guide/install.md`` §6.2 explicitly asks a human to make: run
``ssh-keyscan`` and compare the fingerprint out of band. So the only thing
offered is a named, actionable prerequisite, checked *before* git runs. There is
no ``subprocess`` import in this file on purpose, and there never should be.

Hashed entries (``HashKnownHosts=yes``, lines starting ``|1|``) cannot be
matched by name. When any are present the answer is "not missing": a false
prerequisite that blocks a node whose host key is in fact installed is worse
than letting ssh give the verdict it is already qualified to give.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

KNOWN_HOSTS_PREREQUISITE: Final = "KNOWN-HOSTS-MISSING"
_DEFAULT_SSH_PORT: Final = "22"
_HASHED_MARKER: Final = "|1|"


def _origin_endpoint(origin_url: str) -> tuple[str, str] | None:
    """Return ``(host, port)`` for an ssh remote, or None when no host key applies."""
    if origin_url.startswith(("/", "file://", "https://", "http://")):
        return None
    remainder = origin_url.removeprefix("ssh://") if origin_url.startswith("ssh://") else None
    if remainder is None:
        # scp-like `user@host:path`; a colon here separates the path, not a port.
        user, separator, rest = origin_url.partition("@")
        if not separator:
            return None
        del user
        host, _, _ = rest.partition(":")
        return (host, _DEFAULT_SSH_PORT) if host else None
    authority = remainder.partition("/")[0].rpartition("@")[2]
    if authority.startswith("["):  # IPv6 literal, optionally with :port
        host, _, tail = authority.removeprefix("[").partition("]")
        return (host, tail.removeprefix(":") or _DEFAULT_SSH_PORT) if host else None
    host, separator, port = authority.partition(":")
    if not host:
        return None
    return host, (port if separator and port else _DEFAULT_SSH_PORT)


def _patterns(host: str, port: str) -> frozenset[str]:
    """The literal forms OpenSSH writes for this endpoint."""
    if port == _DEFAULT_SSH_PORT:
        return frozenset({host, f"[{host}]:{port}"})
    return frozenset({f"[{host}]:{port}"})


def _known_names(text: str) -> tuple[frozenset[str], bool]:
    names: set[str] = set()
    hashed = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        first = line.removeprefix("@cert-authority ").removeprefix("@revoked ").split(maxsplit=1)
        if not first:
            continue
        if first[0].startswith(_HASHED_MARKER):
            hashed = True
            continue
        names.update(first[0].split(","))
    return frozenset(names), hashed


def missing_known_host(origin_url: str, ops_home: Path) -> str | None:
    """Return a named prerequisite when the clone's host key is not installed.

    None means "do not block": either no host key applies (local or https
    remote), the entry is present, or the file is hashed and cannot be read by
    name.
    """
    endpoint = _origin_endpoint(origin_url)
    if endpoint is None:
        return None
    host, port = endpoint
    path = ops_home / ".ssh" / "known_hosts"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return _reason(host, path, exists=False)
    names, hashed = _known_names(text)
    if names & _patterns(host, port) or hashed:
        return None
    return _reason(host, path, exists=True)


def _reason(host: str, path: Path, *, exists: bool) -> str:
    state = f"{path}에 {host} 항목이 없다" if exists else f"{path}가 없다"
    return (
        f"{KNOWN_HOSTS_PREREQUISITE}: {state} — clone은 StrictHostKeyChecking=yes로 돌기 때문에 "
        f"호스트키가 먼저 있어야 한다. 설치기는 이것을 대신 정하지 않는다(어떤 호스트키가 "
        f"진짜인지는 소유자 판단이다). docs/guide/install.md §6.2대로 "
        f"`ssh-keyscan {host}`의 출력을 대역외 지문과 대조한 뒤 {path}에 추가하고 다시 실행한다."
    )
