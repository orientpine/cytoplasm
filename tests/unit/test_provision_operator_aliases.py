"""Node rebuild must not silently remove the owner's credential lookup.

`autophagy-cred` / `kanban-cred` / `reporthub-cred` are the owner's only surface
for reading the dashboard credentials, and they lived exclusively in
`~<operator>/.bash_aliases` — no provisioning script defined them. Rebuild the
node and that surface disappears without a single message; the credentials
themselves survive in their mode-600 files, so nothing fails, it just becomes
unreachable.

The aliases carry no secret: each is a `sudo -n -u <account> cat <path>` lookup
command, which is why the definitions can live in the repository at all. The
values stay in the per-account mode-600 files.

Provisioning restores them only when absent — an owner who edited their own
dotfile keeps their version, matching `ensure_file_if_absent`'s only-if-unset
discipline everywhere else in this script.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

_SCRIPT: Final = Path("automation/provision-agent.sh")
_ALIASES: Final = ("autophagy-cred", "kanban-cred", "reporthub-cred")
_MARKER: Final = "autophagy-cred-alias"


def _source() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    text = _source()
    start = text.index(f"{name}() {{")
    return text[start : text.index("\n}\n", start)]


def test_every_owner_lookup_alias_is_defined_by_provisioning() -> None:
    body = _function_body("ensure_operator_credential_aliases")

    for alias in _ALIASES:
        assert f"alias {alias}=" in body


def test_the_step_runs_as_part_of_provisioning() -> None:
    # Given — a helper nobody calls restores nothing
    text = _source()
    definition = text.index("ensure_operator_credential_aliases() {")

    assert re.search(r"^ensure_operator_credential_aliases$", text[definition:], re.MULTILINE)


def test_restoration_is_only_if_absent() -> None:
    # Given — the owner may have their own edited .bash_aliases
    body = _function_body("ensure_operator_credential_aliases")

    assert _MARKER in body
    assert "grep" in body


def test_paths_come_from_node_config_not_hardcoded_homes() -> None:
    # Given — ops_account/agent_account are configurable and third parties change them
    body = _function_body("ensure_operator_credential_aliases")

    assert "$NODE_AGENT_ACCOUNT" in body
    assert "$NODE_OPS_ACCOUNT" in body
    assert "/home/agent" not in body
    assert "/home/ops" not in body


def test_no_credential_value_is_embedded() -> None:
    # Given — the aliases are lookup commands; the values stay in mode-600 files
    body = _function_body("ensure_operator_credential_aliases")

    assert "sudo -n -u" in body
    assert "password" not in body.lower()
    assert body.count("cat ") >= 2


def test_the_script_stays_syntactically_valid() -> None:
    import subprocess

    completed = subprocess.run(
        ("bash", "-n", str(_SCRIPT)), check=False, capture_output=True, text=True
    )

    assert completed.returncode == 0, completed.stderr
