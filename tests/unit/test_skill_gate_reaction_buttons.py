"""Real lifecycle requests seed buttons without granting the bot approval."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

import pytest

from automation import skill_gate, skill_gate_request
from automation.interop.approval_lifecycle import Probe
from automation.interop.approval_surface import ApprovalKind
from automation.release_spec import ReleaseSpec
from automation.skill_gate_approval import GateSurface, SkillApprovalGate
from automation.skill_gate_specs import APPROVE_EMOJI, CANCEL_EMOJI, DeploySpec, Provenance
from tests.unit.test_skill_gate_single_live_request import _surface

_OWNER = "111111111111111111"


class Discord:
    """Mutable fake HTTP endpoint retaining actual posted content and reactors."""

    def __init__(self, pending: Path, fail: str = "") -> None:
        self.pending = pending
        self.fail = fail
        self.content = ""
        self.posts = 0
        self.buttons: list[str] = []
        self.reactors: dict[str, list[dict[str, str | bool]]] = {}

    def api(
        self, method: str, path: str, payload: dict[str, str] | None = None,
    ) -> dict[str, str] | list[dict[str, str | bool]] | None:
        if method == "POST":
            assert payload is not None
            self.content = payload["content"]
            self.posts += 1
            return {"id": "message-1"}
        if method == "PUT":
            assert self.pending.is_file(), "bind the card before decorating it"
            emoji = unquote(path.rsplit("/", 2)[1])
            self.buttons.append(emoji)
            if emoji == self.fail:
                raise OSError("fixture reaction unavailable")
            self.reactors[emoji] = [{"id": _OWNER, "bot": True}]
            return None
        assert method == "GET"
        if "/reactions/" in path:
            emoji = unquote(path.split("/reactions/", 1)[1].split("?", 1)[0])
            return self.reactors.get(emoji, [])
        return {"id": "message-1", "content": self.content}


def _gate(tmp_path: Path, kind: ApprovalKind, fail: str = "") -> tuple[SkillApprovalGate, Discord]:
    spec = (
        ReleaseSpec("v1.2.3", "a" * 40, "b" * 32, (), "fixture change")
        if kind is ApprovalKind.RELEASE
        else DeploySpec("demo", "a" * 64, "b" * 32, "PASS",
                        Provenance("", "", ""), skill_gate._REQUEST_BINDING)
    )
    api = Discord(tmp_path / "pending" / f"{spec.record_name()}.json", fail)
    surface = GateSurface(api.api, tmp_path, lambda: _OWNER, lambda: _surface(kind))
    return SkillApprovalGate(surface, spec), api


@pytest.mark.parametrize("kind", [ApprovalKind.RELEASE, ApprovalKind.SKILL_DEPLOY])
def test_new_card_has_both_buttons_but_bot_reactions_never_authorize(
    tmp_path: Path, kind: ApprovalKind,
) -> None:
    gate, api = _gate(tmp_path, kind)

    requested = skill_gate_request.post_request(gate, fresh=False)

    assert requested.exit_code == 0
    assert api.buttons == [APPROVE_EMOJI, CANCEL_EMOJI]
    request, = gate.outstanding(gate.spec.key())
    assert gate.probe(request) is Probe.BOUND_PENDING
    api.reactors[APPROVE_EMOJI].append({"id": _OWNER, "bot": False})
    assert gate.probe(request) is Probe.APPROVED
    api.reactors[CANCEL_EMOJI].append({"id": _OWNER, "bot": False})
    assert gate.probe(request) is Probe.CANCELLED


def test_reused_pending_card_is_not_posted_or_decorated_again(tmp_path: Path) -> None:
    gate, api = _gate(tmp_path, ApprovalKind.RELEASE)
    first = skill_gate_request.post_request(gate, fresh=False)

    second = skill_gate_request.post_request(gate, fresh=False)

    assert first.record == second.record
    assert api.posts == 1
    assert api.buttons == [APPROVE_EMOJI, CANCEL_EMOJI]


def test_button_failure_preserves_binding_and_attempts_the_other_button(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    gate, api = _gate(tmp_path, ApprovalKind.RELEASE, APPROVE_EMOJI)

    requested = skill_gate_request.post_request(gate, fresh=False)

    assert requested.exit_code == 0
    assert gate.stored() == requested.record
    assert api.buttons == [APPROVE_EMOJI, CANCEL_EMOJI]
    assert "APPROVAL-REACTION-FAIL" in capsys.readouterr().err
    request, = gate.outstanding(gate.spec.key())
    assert gate.probe(request) is Probe.BOUND_PENDING
