"""Shared safety validation for git remote URLs before they reach a git argv.

F3 (security audit, 2026-08-15). Four sites ran ``git clone <url> <dest>`` with
no ``--`` separator and no scheme allowlist, while the URL came from config the
agent can write (``node.toml`` ``origin_url``; ``~/.hermes/**`` ``remote_url`` /
``repo_url``). Two distinct attacks follow from that:

* **Option injection** — a value beginning with ``-`` is read by git as an
  option rather than a URL (``--upload-pack=/bin/sh`` runs a command).
* **Transport helpers** — ``ext::sh -c ...`` is a documented git transport whose
  entire purpose is to execute a command.

``--`` alone stops the first but not the second, and an allowlist alone does not
stop a URL-shaped value from being consumed as an option in some other position,
so callers do both: validate here, then pass ``--`` immediately before the URL.

This mirrors the bash-side guard landed for ``automation/public_export.sh``
(control characters, leading dash, ``ext::``); the allowlist is the additional
positive check the Python call sites can afford because their URLs are
configuration, not operator arguments.

Local absolute paths and ``file://`` are allowed deliberately: a local
repository is a legitimate git remote and is what the test and e2e fixtures
use. Neither can name a transport helper.
"""

from __future__ import annotations

import re
from typing import Final


class GitRemoteUrlError(ValueError):
    """The configured git remote URL is unsafe to hand to git."""


# ``<transport>::<address>`` selects a git remote helper — ext:: executes a
# command. An IPv6 literal such as ``ssh://[::1]/repo`` does not match, because
# everything before its ``::`` contains characters this pattern excludes.
_TRANSPORT_HELPER: Final = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9+.-]*::")
# SCP-like syntax: ``user@host:path`` (``git@github.com:owner/repo.git``).
_SCP_LIKE: Final = re.compile(r"\A[A-Za-z0-9._+-]+@[A-Za-z0-9._-]+:")
_ALLOWED_SCHEMES: Final = ("ssh://", "https://", "file://")


def validate_remote_url(url: str, *, label: str = "git remote URL") -> str:
    """Return ``url`` unchanged, or raise if git must not be handed it.

    Callers must still place ``--`` immediately before the returned value.
    """
    if not url:
        raise GitRemoteUrlError(f"{label} must not be empty")
    if not url.isprintable():
        raise GitRemoteUrlError(f"{label} must not contain control characters")
    if url.startswith("-"):
        raise GitRemoteUrlError(f"{label} must not begin with a dash")
    if _TRANSPORT_HELPER.match(url) is not None:
        raise GitRemoteUrlError(f"{label} must not use a git transport helper")
    if url.startswith(_ALLOWED_SCHEMES) or url.startswith("/"):
        return url
    if _SCP_LIKE.match(url) is not None:
        return url
    raise GitRemoteUrlError(
        f"{label} must be ssh://, https://, file://, user@host:path, or an absolute path"
    )
