"""A refinement host that never answered is a transport fault, not a rejected rewrite.

2026-09-28 node run: the agent account had no Codex CLI login, every chunk got 401, and
the report said ``invariant-failed`` — the diagnosis went looking at sentence checks.
These cases live in their own file so the existing refine suite stays byte-stable.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
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
    assert "codex login" in err


def test_live_login_preflight_skips_before_any_chunk_is_sent(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    version = _version(root, "# 연구 목표\n\n시스템을 검증한다.\n")
    monkeypatch.setenv("PROPOSAL_REFINE_TRANSPORT", "live")
    monkeypatch.setattr(proposal_refine.shutil, "which", lambda _name: "/usr/bin/codex")
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(argv))
        if tuple(argv) == ("codex", "login", "status"):
            return subprocess.CompletedProcess(argv, 1, "", "Not logged in\n")
        pytest.fail(f"a chunk reached the host despite a failed login check: {argv}")

    monkeypatch.setattr(proposal_refine.subprocess, "run", fake_run)

    rc = proposal_refine.main(["--slug", "demo", "--json"])

    assert rc == REFINEMENT_HOST_FAILED_EXIT
    assert calls == [("codex", "login", "status")]
    assert _report(version)["reason"] == "host-unauthenticated"
    assert "REFINEMENT-HOST-SKIPPED reason=host-unauthenticated" in capsys.readouterr().err


def test_live_transport_classifies_401_stderr_as_unauthenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        stderr = "ERROR codex_api: failed to connect to websocket: HTTP error: 401 Unauthorized\n"
        return subprocess.CompletedProcess(argv, 1, "", stderr)

    monkeypatch.setattr(proposal_refine.subprocess, "run", fake_run)

    with pytest.raises(RefinementHostUnauthenticated, match="codex login"):
        _ = proposal_refine._live_transport("본문", "codex-oauth", 5.0)  # pyright: ignore[reportPrivateUsage]


def test_live_transport_keeps_other_failures_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 2, "", "stream disconnected\n")

    monkeypatch.setattr(proposal_refine.subprocess, "run", fake_run)

    with pytest.raises(RefinementTransportError) as raised:
        _ = proposal_refine._live_transport("본문", "codex-oauth", 5.0)  # pyright: ignore[reportPrivateUsage]

    assert not isinstance(raised.value, RefinementHostUnauthenticated)
