"""An interrupted ``compose`` must resume instead of paying for every model call again.

2026-09-29: a release convergence restarted the agent gateway eight minutes into a
live compose. The gateway killed the tool subprocess, the engine had kept nothing,
and the only way forward was to rerun the whole tens-of-minutes draft. These cases
pin the resume cache that compose now records every completed call into. They live
apart from ``test_proposal_pipeline_integrity.py`` because they drive the engine's
real caching client rather than replacing ``_build_llm``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import threading
from pathlib import Path

import pytest

from skills.proposal.engine.agents import hermes_client, llm, writer_guidance
from skills.proposal.engine.pipeline import orchestrator
from skills.proposal.scripts import proposal_compose
from skills.proposal.scripts.proposal_version import Staging, VersionStore


class _Killed(BaseException):
    """Stands in for SIGKILL: nothing in compose may catch it and carry on."""


class _Backend:
    def __init__(self, *, kill_at: int | None = None) -> None:
        self.calls: int = 0
        self._kill_at: int | None = kill_at
        self._lock: threading.Lock = threading.Lock()
        self._answers: llm.MockLLMClient = llm.MockLLMClient(
            llm.load_mock_responses(orchestrator.DEFAULT_LLM_FIXTURE)
        )

    def complete(self, role: str, prompt: str) -> str:
        with self._lock:
            self.calls += 1
            if self.calls == self._kill_at:
                raise _Killed
        try:
            return self._answers.complete(role, prompt)
        except KeyError:
            return "{}"


def _version(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    store = VersionStore(root)
    staging = store.begin("demo", hashlib.sha256(str(root).encode()).hexdigest())
    assert isinstance(staging, Staging)
    version = store.promote("demo", staging, {"parent": None, "schema_version": 1})
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    path = root / "demo" / "versions" / version
    gold = Path(orchestrator.PROJECT_ROOT) / "resource" / "gold"
    for source in gold.iterdir():
        if source.is_file():
            _ = shutil.copy(source, path / "corpus" / source.name)
    return path


def _use(backend: _Backend, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hermes_client, "hermes_client_from_env", lambda: backend)
    monkeypatch.setattr(writer_guidance, "SectionGuidedLLM", lambda inner: inner)


def test_a_killed_compose_resumes_without_repeating_finished_model_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    whole = _Backend()
    _use(whole, monkeypatch)
    _ = _version(tmp_path / "clean", monkeypatch)
    _ = proposal_compose.compose("demo", profile="10-page")
    assert whole.calls > 4

    version = _version(tmp_path / "killed", monkeypatch)
    _use(_Backend(kill_at=whole.calls // 2), monkeypatch)
    with pytest.raises(_Killed):
        _ = proposal_compose.compose("demo", profile="10-page")
    cache = version / "out" / proposal_compose.RESUME_CACHE
    finished = len(llm.load_mock_responses(cache))
    assert finished > 0
    assert not (version / "out" / "drafts.json").exists()

    rerun = _Backend()
    _use(rerun, monkeypatch)
    payload = proposal_compose.compose("demo", profile="10-page")

    assert rerun.calls == whole.calls - finished
    assert payload["resumed_llm_calls"] == finished
    assert (version / "out" / "drafts.json").is_file()
    assert not cache.exists()
    assert not list((version / "out").glob(".compose-*"))


def test_an_unreadable_resume_cache_and_stale_scratch_are_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    out = version / "out"
    _ = (out / proposal_compose.RESUME_CACHE).write_text('{"planner:ab', encoding="utf-8")
    (out / ".compose-leftover").mkdir()
    backend = _Backend()
    _use(backend, monkeypatch)

    payload = proposal_compose.compose("demo", profile="10-page")

    assert payload["resumed_llm_calls"] == 0
    assert backend.calls > 0
    assert not (out / proposal_compose.RESUME_CACHE).exists()
    assert not list(out.glob(".compose-*"))


def test_a_second_compose_on_the_same_version_is_refused_while_one_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    _use(_Backend(), monkeypatch)
    holder = os.open(version / "out" / ".compose.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        with pytest.raises(proposal_compose.ComposeError, match="still running"):
            _ = proposal_compose.compose("demo", profile="10-page")
    finally:
        os.close(holder)


def test_concurrent_recording_keeps_an_owner_only_complete_cache(tmp_path: Path) -> None:
    class _Echo:
        def complete(self, role: str, prompt: str) -> str:
            return f"{role}:{prompt}"

    path = tmp_path / "cache.json"
    client = llm.CachingLLMClient(_Echo(), {}, mode="record", cache_path=path)
    errors: list[BaseException] = []

    def record(worker: int) -> None:
        try:
            for index in range(40):
                _ = client.complete("reviewer", f"{worker}-{index}")
        except BaseException as error:  # noqa: BLE001 - the assertion reports it
            errors.append(error)

    threads = [threading.Thread(target=record, args=(worker,)) for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 320
    assert path.stat().st_mode & 0o777 == 0o600
    assert [entry.name for entry in tmp_path.iterdir()] == ["cache.json"]
