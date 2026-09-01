from __future__ import annotations

import email.message
import os
import sys
import types
from pathlib import Path
from urllib.error import HTTPError

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME = _ROOT / "automation" / "research_trends"
os.environ.setdefault("TOPICS_SCRIPTS", str(_ROOT / "skills" / "topics" / "scripts"))
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_RUNTIME))

from automation.research_trends import research_trends, research_trends_core as core  # noqa: E402
from automation.research_trends.research_trends import OwnerDmDeliveryError  # noqa: E402


ATOM = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<feed xmlns=\"http://www.w3.org/2005/Atom\">
  <entry>
    <id>http://arxiv.org/abs/2607.00001v1</id>
    <title> Autophagy regulation </title>
    <summary> A public abstract about regulation. </summary>
    <published>2026-07-15T00:00:00Z</published>
    <link rel=\"alternate\" href=\"https://arxiv.org/abs/2607.00001\" />
  </entry>
</feed>"""


def test_topics_modules_resolve_from_the_scripts_override() -> None:
    # Given: collection configured the topics scripts override before module import.
    expected = Path(os.environ["TOPICS_SCRIPTS"])

    # When/Then: the runtime records both the selected override and live default.
    assert research_trends.SCRIPTS_DIR == expected
    # 덮어쓰기가 없으면 공유 정의의 governed live 기본값으로 fail-closed 한다.
    assert research_trends.skill_scripts("topics", env_var="TOPICS_SCRIPTS", env={}) == Path(
        "/srv/autophagy-skills/live/topics/scripts"
    )


def test_parse_arxiv_atom_returns_public_paper() -> None:
    # Given
    response = ATOM

    # When
    papers = core.parse_arxiv_feed(response)

    # Then
    assert len(papers) == 1
    assert papers[0].title == "Autophagy regulation"
    assert papers[0].url == "https://arxiv.org/abs/2607.00001"


def test_parse_arxiv_empty_feed_returns_no_papers() -> None:
    # Given
    response = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'

    # When
    papers = core.parse_arxiv_feed(response)

    # Then
    assert papers == ()


def test_parse_arxiv_invalid_response_raises_typed_error() -> None:
    # Given
    response = "upstream is unavailable"

    # When / Then
    with pytest.raises(core.ArxivResponseError):
        core.parse_arxiv_feed(response)


def test_unreachable_topic_yields_partial_report_without_llm_call() -> None:
    # Given
    glm_topics: list[str] = []
    codex_topics: list[str] = []

    def fetch(topic: str) -> tuple[core.Paper, ...]:
        if topic == "unreachable":
            raise core.ArxivUnavailable("connection refused")
        return core.parse_arxiv_feed(ATOM)

    def glm(topic: str, papers: tuple[core.Paper, ...]) -> str:
        glm_topics.append(topic)
        return f"draft {len(papers)}"

    def codex(topic: str, papers: tuple[core.Paper, ...], draft: str) -> str:
        codex_topics.append(topic)
        return f"정리 {draft}"

    # When
    outcomes = core.run_topics(("autophagy", "unreachable"), fetch, glm, codex)
    report = core.assemble_report("2026-07-16", outcomes)

    # Then
    assert glm_topics == ["autophagy"]
    assert codex_topics == ["autophagy"]
    assert "## autophagy" in report
    assert "https://arxiv.org/abs/2607.00001" in report
    assert "## unreachable" in report
    assert "출처 조회 실패" in report


def test_glm_child_receives_key_from_secrets_when_cron_environment_lacks_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    binary = tmp_path / ".local" / "bin" / "hermes"
    binary.parent.mkdir(parents=True)
    _ = binary.write_text(
        '#!/bin/sh\n[ -n "$LITELLM_AGENT_KEY" ] || exit 9\nprintf "summary"\n',
        encoding="utf-8",
    )
    _ = binary.chmod(0o755)
    secrets_file = tmp_path / ".env.secrets"
    _ = secrets_file.write_text("LITELLM_AGENT_KEY=cron-fallback-key\n", encoding="utf-8")
    monkeypatch.delenv("LITELLM_AGENT_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RESEARCH_TRENDS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(research_trends, "SECRETS", secrets_file)

    # When
    result = research_trends._run_llm("glm", "custom:litellm", "glm-main", "topic", "prompt")

    # Then
    assert result == "summary"


def test_bot_token_raises_typed_owner_dm_error_when_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets_file = tmp_path / ".env.secrets"
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setattr(research_trends, "SECRETS", secrets_file)

    with pytest.raises(OwnerDmDeliveryError):
        research_trends._bot_token()

    assert issubclass(OwnerDmDeliveryError, RuntimeError)


def test_send_dm_raises_typed_owner_dm_error_when_not_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ON-2: destination resolution lives in the owner_notice facade; this module
    # only maps "not delivered" onto its typed error so main() exits masked.
    from automation import owner_notice

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture-token")
    monkeypatch.setattr(owner_notice, "notify_owner", lambda _report: False)

    with pytest.raises(OwnerDmDeliveryError):
        research_trends._send_dm("report")


def test_send_dm_delegates_the_report_to_the_notice_facade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automation import owner_notice

    sent: list[str] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture-token")
    monkeypatch.setattr(
        owner_notice, "notify_owner", lambda report: sent.append(report) or True
    )

    research_trends._send_dm("report")

    assert sent == ["report"]  # 마스킹·본문은 그대로, 목적지만 파사드가 정한다


def test_send_dm_exports_the_token_for_the_facade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The deployed cron carries the token in ~/.env.secrets, not the env; the
    # facade reads DISCORD_BOT_TOKEN from the env, so _send_dm must export it.
    from automation import owner_notice

    secrets = tmp_path / "env.secrets"
    _ = secrets.write_text("DISCORD_BOT_TOKEN=from-secrets\n", encoding="utf-8")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setattr(research_trends, "SECRETS", secrets)
    seen: list[str] = []
    monkeypatch.setattr(
        owner_notice,
        "notify_owner",
        lambda _report: seen.append(os.environ.get("DISCORD_BOT_TOKEN", "")) or True,
    )

    research_trends._send_dm("report")

    assert seen == ["from-secrets"]

class _FakeResponse:
    """Minimal urlopen context-manager stand-in for retry tests."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _http_error(url: str, code: int) -> HTTPError:
    return HTTPError(url, code, "arXiv throttled", email.message.Message(), None)


def _stub_arxiv_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RESEARCH_TRENDS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("RESEARCH_TRENDS_FORCE_ARXIV_FAILURE", raising=False)
    monkeypatch.setattr(research_trends, "_last_arxiv_request_at", 0.0, raising=False)
    sleeps: list[float] = []
    fake_time = types.SimpleNamespace(
        sleep=lambda seconds: sleeps.append(seconds), monotonic=lambda: 0.0
    )
    monkeypatch.setattr(research_trends, "time", fake_time, raising=False)
    return sleeps


def test_fetch_arxiv_retries_on_429_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    sleeps = _stub_arxiv_env(tmp_path, monkeypatch)
    attempts = {"n": 0}

    def fake_urlopen(request: object, timeout: int = 0) -> _FakeResponse:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise _http_error(getattr(request, "full_url", "http://x"), 429)
        return _FakeResponse(ATOM.encode("utf-8"))

    monkeypatch.setattr(research_trends, "urlopen", fake_urlopen)

    # When
    body = research_trends._fetch_arxiv("autophagy")

    # Then
    assert attempts["n"] == 3
    assert "Autophagy regulation" in body
    assert len(sleeps) >= 2


def test_fetch_arxiv_raises_after_persistent_429(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    _ = _stub_arxiv_env(tmp_path, monkeypatch)
    attempts = {"n": 0}

    def fake_urlopen(request: object, timeout: int = 0) -> _FakeResponse:
        attempts["n"] += 1
        raise _http_error(getattr(request, "full_url", "http://x"), 429)

    monkeypatch.setattr(research_trends, "urlopen", fake_urlopen)

    # When / Then
    with pytest.raises(research_trends.core.ArxivUnavailable):
        research_trends._fetch_arxiv("autophagy")
    assert attempts["n"] == 4


def test_fetch_arxiv_does_not_retry_client_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    _ = _stub_arxiv_env(tmp_path, monkeypatch)
    attempts = {"n": 0}

    def fake_urlopen(request: object, timeout: int = 0) -> _FakeResponse:
        attempts["n"] += 1
        raise _http_error(getattr(request, "full_url", "http://x"), 400)

    monkeypatch.setattr(research_trends, "urlopen", fake_urlopen)

    # When / Then
    with pytest.raises(research_trends.core.ArxivUnavailable):
        research_trends._fetch_arxiv("autophagy")
    assert attempts["n"] == 1


def test_retry_after_header_is_parsed() -> None:
    # Given
    headers = email.message.Message()
    headers["Retry-After"] = "7"
    error = HTTPError("http://export.arxiv.org", 429, "throttled", headers, None)

    # When / Then
    assert research_trends._retry_after_seconds(error) == 7.0
