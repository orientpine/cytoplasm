"""Contract for the curator's best-effort near-cap alert DM.

The alert is a notification, not an approval, so it lives in effects and
fails closed: a dry-run prints, a real send creates the owner DM channel
once then posts, a missing token no-ops, and any transport error is
swallowed so a bad cron tick never crashes.  The Discord POST is injected
so this is host-testable without touching Discord.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from automation.memory_curator import effects
from automation.memory_curator.binding import PromotionReceipt
from automation.memory_curator.effects import (
    _twin_meta_and_body,
    _wiki_gate_promote,
    alert_owner,
    post_promotion,
)
from automation.memory_curator.promotion import PromotionProposal, build_proposal
from automation.memory_curator.reporting import preview
from skills.wiki.scripts import wiki_store


def test_dry_run_prints_and_does_not_post(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("MEMORY_CURATOR_DRY_RUN", "1")
    calls: list[tuple[str, str, dict[str, str]]] = []

    def post(token: str, path: str, payload: dict[str, str]) -> dict[str, object]:
        calls.append((token, path, payload))
        return {"id": "x"}

    sent = alert_owner("near cap!", post=post)
    assert calls == []
    assert sent is False
    assert "DRY-RUN alert: near cap!" in capsys.readouterr().out


def test_creates_owner_dm_channel_then_posts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    _ = config.write_text('{"owner_id": "999"}', encoding="utf-8")
    monkeypatch.setattr("automation.memory_curator.effects._INTEROP_CONFIG", config)
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    calls: list[tuple[str, str, dict[str, str]]] = []

    def post(token: str, path: str, payload: dict[str, str]) -> dict[str, object]:
        calls.append((token, path, payload))
        return {"id": "chan123"}

    sent = alert_owner("\u26a0\ufe0f near cap", post=post)
    assert calls[0] == ("tok", "/users/@me/channels", {"recipient_id": "999"})
    assert calls[1] == ("tok", "/channels/chan123/messages", {"content": "\u26a0\ufe0f near cap"})
    assert sent is True


def test_no_token_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    calls: list[tuple[str, str, dict[str, str]]] = []

    def post(token: str, path: str, payload: dict[str, str]) -> dict[str, object]:
        calls.append((token, path, payload))
        return {"id": "y"}

    sent = alert_owner("x", post=post)
    assert calls == []
    assert sent is False


def test_transport_error_is_swallowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    _ = config.write_text('{"owner_id": "999"}', encoding="utf-8")
    monkeypatch.setattr("automation.memory_curator.effects._INTEROP_CONFIG", config)
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")

    def boom(
        _token: str, _path: str, _payload: dict[str, str]
    ) -> dict[str, object]:
        raise RuntimeError("discord down")

    assert alert_owner("x", post=boom) is False



# --- promotion effect ------------------------------------------------------- #
def test_twin_meta_is_observed_advisory_capped() -> None:
    proposal = build_proposal("앞으로 배려를 원칙으로 한다", source_kind="user")
    meta, body = _twin_meta_and_body(proposal, "2026-01-01T00:00:00Z")
    assert meta["kind"] == "principle"
    assert meta["authority"] == "advisory"  # SI-3 proposer cap
    assert meta["provenance"] == "observed"  # SI-3 proposer cap
    assert meta["tags"] == ["twin", "principle"]
    assert body == proposal.body


def test_post_promotion_returns_runner_result() -> None:
    proposal = build_proposal("원칙으로 한다", source_kind="memory")
    receipt = PromotionReceipt("draft-abc", "message-123", proposal.slug, "note-hash")

    assert post_promotion(proposal, runner=lambda _p: receipt) == receipt
    assert post_promotion(proposal, runner=lambda _p: None) is None


def test_post_promotion_swallows_runner_error() -> None:
    proposal = build_proposal("원칙으로 한다", source_kind="memory")

    def boom(_proposal: PromotionProposal) -> PromotionReceipt | None:
        raise RuntimeError("wiki gate down")

    assert post_promotion(proposal, runner=boom) is None


def test_wiki_gate_promote_returns_confirmation_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)
    proposal = build_proposal("원칙으로 한다", source_kind="memory")
    note_texts: list[str] = []
    fake_gate = ModuleType("wiki_gate")

    def create_draft(
        action: str, slug: str, note_text: str, surface: str, *, summary: str
    ) -> dict[str, str]:
        note_texts.append(note_text)
        assert (action, slug, surface) == ("create", proposal.slug, "dm")
        assert summary  # the owner-DM summary is always supplied by the curator
        return {"id": "draft-abc", "sha256": "note-hash"}

    def post_confirm_message(_draft: dict[str, str]) -> dict[str, str]:
        return {"confirm_message_id": "message-123"}

    fake_gate.__dict__["create_draft"] = create_draft
    fake_gate.__dict__["post_confirm_message"] = post_confirm_message
    monkeypatch.setitem(sys.modules, "wiki_gate", fake_gate)
    monkeypatch.setitem(sys.modules, "wiki_store", wiki_store)

    receipt = _wiki_gate_promote(proposal)

    assert receipt == PromotionReceipt("draft-abc", "message-123", proposal.slug, "note-hash")
    _meta, body = wiki_store.parse_note(note_texts[0])
    assert body.rstrip("\n") == proposal.body

def test_wiki_gate_promote_passes_a_summary_describing_the_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ✅ the owner taps DELETES the entry from the agent's own memory — the
    message they react to must say so, in the same message (memory_relocate precedent).
    The wiki gate learns nothing about memory: it only renders this opaque string."""
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)
    proposal = build_proposal("원칙으로 한다", source_kind="memory")
    summaries: list[str] = []
    fake_gate = ModuleType("wiki_gate")

    def create_draft(
        action: str, slug: str, note_text: str, surface: str, *, summary: str
    ) -> dict[str, str]:
        # keyword-only: a positional summary raises TypeError right here.
        _ = (action, slug, note_text, surface)
        summaries.append(summary)
        return {"id": "draft-abc", "sha256": "note-hash"}

    def post_confirm_message(_draft: dict[str, str]) -> dict[str, str]:
        return {"confirm_message_id": "message-123"}

    fake_gate.__dict__["create_draft"] = create_draft
    fake_gate.__dict__["post_confirm_message"] = post_confirm_message
    monkeypatch.setitem(sys.modules, "wiki_gate", fake_gate)
    monkeypatch.setitem(sys.modules, "wiki_store", wiki_store)

    _ = _wiki_gate_promote(proposal)

    assert len(summaries) == 1
    summary = summaries[0]
    assert preview(proposal.entry_text) in summary  # (i) entry preview
    assert "✅" in summary  # (ii) the reaction that deletes
    assert "자체 메모리" in summary
    assert "삭제" in summary
    assert proposal.twin_kind in summary  # (iii) the twin kind



def test_wiki_gate_promote_dry_run_does_not_create_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_CURATOR_DRY_RUN", "1")
    fake_gate = ModuleType("wiki_gate")

    def forbidden_create_draft(*_args: str) -> dict[str, str]:
        pytest.fail("dry-run must not create a wiki draft")

    fake_gate.__dict__["create_draft"] = forbidden_create_draft
    monkeypatch.setitem(sys.modules, "wiki_gate", fake_gate)
    proposal = build_proposal("원칙으로 한다", source_kind="memory")

    assert _wiki_gate_promote(proposal) is None


def test_read_twin_returns_regular_file_bytes(tmp_path: Path) -> None:
    note = tmp_path / "principle.md"
    _ = note.write_bytes(b"wiki-note\n")

    assert effects.read_twin("principle", wiki_root=tmp_path) == b"wiki-note\n"


def test_read_twin_returns_none_when_file_is_missing(tmp_path: Path) -> None:
    assert effects.read_twin("missing", wiki_root=tmp_path) is None


def test_read_twin_never_follows_symlink(tmp_path: Path) -> None:
    note = tmp_path / "principle.md"
    _ = note.write_bytes(b"wiki-note\n")
    (tmp_path / "linked.md").symlink_to(note)

    assert effects.read_twin("linked", wiki_root=tmp_path) is None


def test_the_promotion_summary_shows_the_whole_entry_not_a_28_char_preview() -> None:
    """소유자는 이 한 건만 보고 삭제를 인가한다 — 나열용 미리보기 길이로는 판단할 수 없다.

    `reporting.preview` 의 28자는 여러 건을 한 번에 나열하는 🧠 알림용 크기다. 승인
    메시지는 항목 하나짜리이고 1900자 예산이 있으므로, 재배치 렌더가 원문을 그대로
    싣는 것과 같은 기준이어야 한다. 2026-08-03 실측: 소유자가 `'cha는 redundant
    approval gate…'` 만 보고 "무엇을 승인해야 하는지 모르겠다"고 했다.

    토큰 모양 문자열은 그대로 두면 안 되므로 마스킹은 유지한다.
    """
    entry = (
        "cha는 redundant approval gate 싫어함: DM 명시 요청 메일을 다시 #approvals 에서 "
        "승인하는 건 과도하다고 본다. 토큰 abcdefghijklmnop1234 는 마스킹돼야 한다."
    )
    proposal = build_proposal(entry, source_kind="user")
    summary = effects._promotion_summary(proposal)

    assert "승인하는 건 과도하다고 본다" in summary, "원문 뒷부분이 잘렸다"
    assert "abcdefghijklmnop1234" not in summary
    assert "[REDACTED]" in summary
    assert "…" not in summary.split("───")[1], "원문 구간이 잘림 표시를 달고 있다"


def test_the_promotion_summary_names_the_source_file() -> None:
    """어느 파일에서 빠지는지가 승인 판단의 일부다 — USER.md 와 MEMORY.md 는 성격이 다르다.

    `USER.md` 는 신원·스타일이라 상시 메모리에서 빠지면 자기소개·서명 때마다 검색이
    끼고, `MEMORY.md` 는 운영 사실이라 성격이 다르다. 소유자가 그 구분 없이 ✅ 를
    누르게 두면 안 된다(2026-08-03 소유자 요청).
    """
    user_summary = effects._promotion_summary(build_proposal("사용자 선호 항목", source_kind="user"))
    memory_summary = effects._promotion_summary(build_proposal("운영 사실 항목", source_kind="memory"))
    assert "USER.md" in user_summary and "MEMORY.md" not in user_summary
    assert "MEMORY.md" in memory_summary and "USER.md" not in memory_summary


def _posted_state(tmp_path: Path, *, summary: str, with_link: bool = True) -> object:
    """대기 중인 승격 하나 + 그에 대응하는 위키 초안."""
    from automation.memory_curator.state import CuratorState
    from automation.memory_curator.state_models import AlertState, PromotionRecord

    record = PromotionRecord(
        source_kind="user",
        entry_sha256="a" * 64,
        slug="memory-promoted-user-aaaa",
        created_at="2026-08-03T00:00:00Z",
        note_sha256="",
        draft_id="ab12cd",
        confirm_message_id="222",
        status="posted",
        posted_at="2026-08-03T00:00:00Z",
        reconciled_at=None,
        backup_path=None,
        last_block_reason=None,
    )
    drafts = tmp_path / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"summary": summary, "confirm_message_id": "222"}
    if with_link:
        payload["channel_id"] = "111"
    _ = (drafts / "ab12cd.json").write_text(json.dumps(payload), encoding="utf-8")
    return CuratorState(3, {"user:" + "a" * 64: record}, AlertState(None, None, None, None), {})


def test_the_reminder_goes_out_once_and_then_holds_for_three_hours(tmp_path: Path) -> None:
    """소유자가 못 본 승인을 다시 가리키되, 매 tick(30분) 두드리지는 않는다."""
    state = _posted_state(tmp_path, summary="머리말\n───\n어떤 판단 근거\n───\n꼬리말")
    marker = tmp_path / "marker"
    sent: list[str] = []
    at_ten = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)  # KST 10:00

    assert effects.send_pending_reminder(
        state, gate_dir=tmp_path, marker_path=marker, now=at_ten, alert=lambda m: bool(sent.append(m)) or True
    )
    assert "USER.md" in sent[0] and "어떤 판단 근거" in sent[0]
    assert "https://discord.com/channels/@me/111/222" in sent[0]

    assert not effects.send_pending_reminder(
        state, gate_dir=tmp_path, marker_path=marker,
        now=at_ten + timedelta(hours=2), alert=lambda _m: True
    )
    assert effects.send_pending_reminder(
        state, gate_dir=tmp_path, marker_path=marker,
        now=at_ten + timedelta(hours=3), alert=lambda _m: True
    )


def test_the_quiet_window_defers_without_consuming_the_turn(tmp_path: Path) -> None:
    """조용한 시간에 걸린 건 취소가 아니다 — 표식이 전진하지 않아 창이 열리면 바로 나간다."""
    state = _posted_state(tmp_path, summary="x\n───\n판단 근거\n───\ny")
    marker = tmp_path / "marker"
    at_three_am = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)  # KST 03:00
    assert not effects.send_pending_reminder(
        state, gate_dir=tmp_path, marker_path=marker, now=at_three_am, alert=lambda _m: True
    )
    assert not marker.exists(), "보내지도 않고 표식만 전진시켰다"
    assert effects.send_pending_reminder(
        state, gate_dir=tmp_path, marker_path=marker,
        now=datetime(2026, 8, 3, 0, 30, tzinfo=UTC), alert=lambda _m: True  # KST 09:30
    )


def test_a_promotion_whose_draft_is_gone_is_not_reminded(tmp_path: Path) -> None:
    """초안이 폐기됐다면 소유자가 이미 거절한 것이다 — 다시 물으면 안 된다."""
    state = _posted_state(tmp_path, summary="x", with_link=True)
    (tmp_path / "drafts" / "ab12cd.json").unlink()
    assert not effects.send_pending_reminder(
        state, gate_dir=tmp_path, marker_path=tmp_path / "marker",
        now=datetime(2026, 8, 3, 1, 0, tzinfo=UTC), alert=lambda _m: True
    )
