"""Regression for the child-subprocess PATH bug: the fallback provider's own CLI
(``claude``) lives next to ``hermes`` under ``{HOME}/.local/bin`` (see
``_RELATIVE_BINARY`` for how ``hermes`` itself is resolved there), but
``CodexClient._run`` hardcoded the child PATH to ``/usr/bin:/bin``. That starves
any Hermes fallback_providers chain that shells out to a binary installed only
under the account's own ``.local/bin`` -- the whole call then fails closed with
``CodexUnavailableError`` even though the fallback is configured and would work.

These tests exercise the *real* ``subprocess.run`` call (no FakeRun stub) against a
throwaway ``HOME`` so the PATH the child process actually sees is the thing under
test, mirroring how ``test_binary_resolution_prefers_override_then_home_then_path``
in ``test_codex_llm.py`` builds real files under a ``tmp_path`` HOME instead of
mocking.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from automation.codex_llm import CodexClient, CodexUnavailableError

_FAKE_HERMES = """#!/bin/sh
# Stands in for the real `hermes` binary: it shells out to `claude` the same way
# Hermes' own fallback_providers chain does, using whatever PATH its parent gave it.
BIN=$(command -v claude 2>/dev/null)
if [ -z "$BIN" ]; then
    echo "install it with npm install -g @anthropic-ai/claude-code" 1>&2
    exit 1
fi
echo "claude-found:$BIN"
"""

_FAKE_CLAUDE = """#!/bin/sh
echo "claude-ran"
"""


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def home_with_local_claude(tmp_path: Path) -> Path:
    """A throwaway HOME with `claude` installed only under its own .local/bin.

    This is exactly where Hermes' own install docs put it, and exactly where
    `_RELATIVE_BINARY` already looks for `hermes` itself -- `claude` is not on
    `/usr/bin` or `/bin`, so it is invisible unless `.local/bin` is on PATH.
    """
    home = tmp_path / "fakehome"
    _write_executable(home / ".local" / "bin" / "claude", _FAKE_CLAUDE)
    return home


@pytest.fixture
def fake_hermes_binary(tmp_path: Path) -> Path:
    """A `hermes`-shaped binary living *outside* HOME, like the real deployed one."""
    binary = tmp_path / "bindir" / "fake-hermes"
    _write_executable(binary, _FAKE_HERMES)
    return binary


def test_child_path_puts_home_local_bin_ahead_of_the_safe_default(
    home_with_local_claude: Path,
) -> None:
    """The fix under test: `home/.local/bin` must be searched, `/usr/bin:/bin` too."""
    from automation.codex_llm import _child_path

    path = _child_path(str(home_with_local_claude))
    assert path == f"{home_with_local_claude}/.local/bin:/usr/bin:/bin"
    entries = path.split(":")
    assert entries[0] == f"{home_with_local_claude}/.local/bin"
    assert "/usr/bin" in entries
    assert "/bin" in entries


def test_fallback_binary_under_home_local_bin_is_now_reachable(
    home_with_local_claude: Path, fake_hermes_binary: Path
) -> None:
    """GREEN after the fix: the real `_run` subprocess call can find `claude`.

    Before the fix this raised CodexUnavailableError with the same
    "install it with npm install -g @anthropic-ai/claude-code" message the user
    hit live, because `/usr/bin:/bin` alone can never contain `claude`.
    """
    client = CodexClient(binary=str(fake_hermes_binary), home=str(home_with_local_claude))
    answer = client._run([str(fake_hermes_binary)], timeout=10.0)
    assert answer.startswith("claude-found:")
    assert str(home_with_local_claude) in answer


def test_usr_bin_and_bin_are_still_present_and_nothing_else_is_added(
    home_with_local_claude: Path, fake_hermes_binary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No broadening beyond the one account-owned directory: not a PATH leak.

    Proves the fix is additive-and-narrow, not a switch to inheriting the full
    caller environment: a binary placed only on the *caller's* unrelated PATH
    entry must stay invisible to the child, while /usr/bin and /bin must still
    be searched exactly as before.
    """
    from automation.codex_llm import _CHILD_PATH, _child_path

    # A directory on the *parent* process's PATH that is not /usr/bin, /bin, or
    # {home}/.local/bin must NOT leak into the child -- only the narrow fixed set may.
    leaky_dir = home_with_local_claude.parent / "unrelated-caller-only-bin"
    _write_executable(leaky_dir / "claude", _FAKE_CLAUDE)
    monkeypatch.setenv("PATH", f"{leaky_dir}:/usr/bin:/bin")

    computed = _child_path(str(home_with_local_claude))
    assert str(leaky_dir) not in computed
    for entry in _CHILD_PATH.split(":"):
        assert entry in computed.split(":")

    client = CodexClient(binary=str(fake_hermes_binary), home=str(home_with_local_claude))
    answer = client._run([str(fake_hermes_binary)], timeout=10.0)
    # Resolved from {home}/.local/bin, never from the leaky caller-only directory.
    assert f"{home_with_local_claude}/.local/bin/claude" in answer
    assert str(leaky_dir) not in answer
