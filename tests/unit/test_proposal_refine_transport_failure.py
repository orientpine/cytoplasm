"""A refinement host that never answered is a transport fault, not a rejected rewrite.

2026-09-28 node run: the agent account had no Codex CLI login, every chunk got 401, and
the report said ``invariant-failed`` — the diagnosis went looking at sentence checks.
These cases live in their own file so the existing refine suite stays byte-stable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from skills.proposal.scripts import proposal_refine
from skills.proposal.scripts.proposal_refine import (
    REFINEMENT_HOST_FAILED_EXIT,
    RefinementHostUnauthenticated,
    RefinementTransportError,
    RefinementTransportFailed,
    refine_version,
)
from skills.proposal.scripts.proposal_version import Staging, VersionStore


def _version(root: Path, body: str) -> Path:
    store = VersionStore(root)
    staging = store.begin("demo", hashlib.sha256(str(root).encode()).hexdigest())
    assert isinstance(staging, Staging)
    version = store.promote(
        "demo", staging, {"parent": None, "request": {"profile": "30-page"}, "schema_version": 1}
    )
    path = root / "demo" / "versions" / version
    section = {"body": body, "claims": [], "section_id": "0", "title": "연구 개요"}
    _ = (path / "out" / "drafts.json").write_text(
        json.dumps({"sections": [section]}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def _report(version: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads((version / "out" / "refine-report.json").read_text()))


def _raise(error: Exception):  # noqa: ANN202 - tiny test transport factory
    def transport(_text: str, _host: str, _timeout: float) -> str:
        raise error

    return transport


@pytest.fixture
def root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    return tmp_path


def test_unauthenticated_host_is_reported_as_such_not_as_invariant_failure(root: Path) -> None:
    version = _version(root, "# 연구 목표\n\n시스템을 검증한다.\n")

    with pytest.raises(RefinementTransportFailed) as raised:
        _ = refine_version(
            "demo", transport=_raise(RefinementHostUnauthenticated("401")), host="codex-oauth"
        )

    assert raised.value.reason == "host-unauthenticated"
    report = _report(version)
    assert report["reason"] == "host-unauthenticated"
    assert report["failure_reason"] == "host-unauthenticated"
    assert report["invariant_summary"] == "NOT_RUN"
    assert report["refined"] is False
    assert not (version / "out" / "drafts.refined.json").exists()
    manifest = cast(dict[str, object], json.loads((version / "manifest.json").read_text()))
    assert manifest["reason"] == "host-unauthenticated"


def test_generic_transport_failure_is_transport_failed(root: Path) -> None:
    version = _version(root, "# 연구 목표\n\n시스템을 검증한다.\n")

    with pytest.raises(RefinementTransportFailed) as raised:
        _ = refine_version(
            "demo", transport=_raise(RefinementTransportError("rc=1")), host="codex-oauth"
        )

    assert raised.value.reason == "transport-failed"
    assert _report(version)["reason"] == "transport-failed"


def test_cli_names_the_remedy_and_exits_host_failed(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = _version(root, "# 연구 목표\n\n시스템을 검증한다.\n")

    rc = proposal_refine.main(
        ["--slug", "demo", "--json"], transport=_raise(RefinementHostUnauthenticated("401"))
    )

    err = capsys.readouterr().err
    assert rc == REFINEMENT_HOST_FAILED_EXIT
    assert "REFINEMENT_TRANSPORT_FAILED reason=host-unauthenticated" in err
    assert "REFINEMENT_INVARIANT_FAILED" not in err
    assert "hermes auth" in err


class _SharedClient:
    """Stands in for ``automation.codex_llm``: records prompts, answers or raises."""

    class CodexError(RuntimeError):
        pass

    def __init__(self, answer: str | None = None, error: str | None = None) -> None:
        self.prompts: list[str] = []
        self._answer = answer
        self._error = error

    def complete(self, prompt: str, *, timeout: float) -> str:
        assert timeout > 0
        self.prompts.append(prompt)
        if self._error is not None:
            raise self.CodexError(self._error)
        return cast(str, self._answer)


def _rules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rules = tmp_path / "im-not-ai" / "skills" / "humanize-korean" / "references" / "quick-rules.md"
    rules.parent.mkdir(parents=True)
    _ = rules.write_text("QUICK-RULES-SENTINEL", encoding="utf-8")
    monkeypatch.setenv("PROPOSAL_REFINE_ROOT", str(tmp_path / "im-not-ai"))


def _client(monkeypatch: pytest.MonkeyPatch, client: _SharedClient) -> None:
    monkeypatch.setattr(proposal_refine.proposal_llm, "shared_client_module", lambda: client)


def test_live_refine_skips_before_any_chunk_when_the_rules_are_not_installed(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    version = _version(root, "# 연구 목표\n\n시스템을 검증한다.\n")
    monkeypatch.setenv("PROPOSAL_REFINE_TRANSPORT", "live")
    monkeypatch.setenv("PROPOSAL_REFINE_ROOT", str(root / "absent"))
    client = _SharedClient(answer='{"text": "x"}')
    _client(monkeypatch, client)

    rc = proposal_refine.main(["--slug", "demo", "--json"])

    assert rc == REFINEMENT_HOST_FAILED_EXIT
    assert client.prompts == []
    assert _report(version)["reason"] == "host-unavailable"
    assert "REFINEMENT-HOST-SKIPPED reason=host-unavailable" in capsys.readouterr().err


def test_live_transport_asks_the_shared_hermes_route_with_the_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _rules(monkeypatch, tmp_path)
    client = _SharedClient(answer='```json\n{"text": "다듬은 본문이다."}\n```')
    _client(monkeypatch, client)

    text = proposal_refine._live_transport("본문", "hermes-codex", 5.0)  # pyright: ignore[reportPrivateUsage]

    assert text == "다듬은 본문이다."
    assert "QUICK-RULES-SENTINEL" in client.prompts[0]
    assert "<DATA>\n본문\n</DATA>" in client.prompts[0]


def test_live_transport_classifies_missing_credentials_as_unauthenticated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _rules(monkeypatch, tmp_path)
    failure = "Codex call failed (rc=1): hermes -z: agent failed: No Codex credentials stored."
    _client(monkeypatch, _SharedClient(error=failure))

    with pytest.raises(RefinementHostUnauthenticated, match="hermes auth"):
        _ = proposal_refine._live_transport("본문", "hermes-codex", 5.0)  # pyright: ignore[reportPrivateUsage]


def test_live_transport_keeps_other_failures_generic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _rules(monkeypatch, tmp_path)
    _client(monkeypatch, _SharedClient(error="Codex call failed (rc=2): stream disconnected"))

    with pytest.raises(RefinementTransportError) as raised:
        _ = proposal_refine._live_transport("본문", "hermes-codex", 5.0)  # pyright: ignore[reportPrivateUsage]

    assert not isinstance(raised.value, RefinementHostUnauthenticated)
