"""What the watcher is allowed to run, pinned before it is allowed to run anything.

FA-3. When the owner has approved, the watcher re-invokes the EXISTING pipeline. The
whole safety argument for that rests on the pipeline re-verifying everything — owner
decision, stop-reaction precedence, peer attestation, review verdict, digest — so the
command must carry nothing that would skip any of it.

Every bypass this repo has is a flag or an environment variable away:

* ``--sandbox-only`` / ``--approve-only`` stop before mounting, so a watcher using them
  would report success having deployed nothing.
* ``--request-only`` would repost rather than resume.
* ``--fresh`` supersedes the live request, destroying the approval it is acting on.
* ``SKILL_SRC_DIR`` retargets the source, and ``DEPLOY_ALLOW_UNPUSHED`` disables the
  provenance guard — either would let the watcher mount something origin/main does not
  have.

None of them are things a resume could ever legitimately want, so the test asserts
their absence from the module itself rather than only from one constructed command.
An automation that can reach a bypass eventually does.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from automation.supply_chain_effects import resume_command
from automation.supply_chain_plan import PendingRequest

_SCRIPT = Path("/srv/autophagy-agent-current/automation/deploy-skill.sh")
_MODULE = Path(__file__).resolve().parents[2] / "automation" / "supply_chain_effects.py"

_DEPLOY = PendingRequest(
    key="skill-deploy:demo", kind="skill-deploy", name="demo", record_name="demo"
)
_PUBLISH = PendingRequest(
    key="skill-publish:demo", kind="skill-publish", name="demo", record_name="publish-demo"
)


def test_the_resume_is_a_plain_re_invocation() -> None:
    assert resume_command(_SCRIPT, _DEPLOY) == ("sudo", "-n", str(_SCRIPT), "--skill", "demo")


def test_the_command_names_the_skill_not_the_record_file() -> None:
    """They coincide for a deploy, but the pipeline takes a skill."""
    managed = PendingRequest(
        key="skill-deploy:managed-x",
        kind="skill-deploy",
        name="managed-x",
        record_name="managed-x",
    )
    assert resume_command(_SCRIPT, managed)[-1] == managed.name


def test_no_bypass_flag_can_reach_the_pipeline() -> None:
    """An automation that can reach a bypass eventually does."""
    text = _MODULE.read_text(encoding="utf-8")
    for bypass in (
        "--sandbox-only",
        "--approve-only",
        "--request-only",
        "--fresh",
        "--activate-managed",
        "SKILL_SRC_DIR",
        "DEPLOY_ALLOW_UNPUSHED",
    ):
        assert bypass not in text, bypass


def test_a_kind_we_cannot_resume_has_no_command() -> None:
    """Refusing here is the last chance before invented arguments reach a subprocess."""
    with pytest.raises(Exception):
        _ = resume_command(_SCRIPT, _PUBLISH)


def test_the_command_carries_no_extra_arguments() -> None:
    """Exactly three tokens: the interpreter, the script, the skill."""
    assert len(resume_command(_SCRIPT, _DEPLOY)) == 5
