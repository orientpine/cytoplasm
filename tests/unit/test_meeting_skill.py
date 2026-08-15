"""W2-3 meeting ingest skill — deterministic tests (LLM mocked via recording)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
FIXTURES = SKILL / "fixtures"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_actions  # noqa: E402
import meeting_cli  # noqa: E402
import meeting_extract  # noqa: E402
import meeting_gate  # noqa: E402
import meeting_llm  # noqa: E402

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
RULES = meeting_gate.load_rules(REPO / "configs" / "sensitivity-rules.yaml")


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "meeting_gate_plugin", SKILL / "plugin" / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Source:
    def __init__(self, *, user_id="OWNER1", is_bot=False, chat_type="dm", chat_id="C1"):
        self.user_id = user_id
        self.is_bot = is_bot
        self.chat_type = chat_type
        self.chat_id = chat_id
        self.thread_id = None


_MISSING = object()


class _RawMessage:
    def __init__(self, content):
        self.content = content


class _Event:
    def __init__(
        self,
        text="",
        media_urls=(),
        media_types=(),
        metadata=None,
        raw_content=_MISSING,
        **source_kwargs,
    ):
        self.text = text
        self.media_urls = list(media_urls)
        self.media_types = list(media_types)
        self.metadata = dict(metadata or {})
        if raw_content is not _MISSING:
            self.raw_message = _RawMessage(raw_content)
        self.source = _Source(**source_kwargs)


# --- acceptance: fixed fixture -> milestones >= 2 AND todos >= 3 -------------


def test_recorded_extraction_meets_thresholds():
    raw = (FIXTURES / "recorded-clean.json").read_text(encoding="utf-8")
    extraction = meeting_llm.parse_extraction(raw)
    assert len(extraction.milestones) >= 2
    assert len(extraction.todos) >= 3
    for item in extraction.todos + extraction.milestones:
        assert item.title and item.basis
        assert item.deadline is None or len(item.deadline) == 10


def test_full_offline_pipeline_on_fixture(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_STATE_FILE", str(tmp_path / "state/milestones.yaml"))
    monkeypatch.setenv("MEETING_RULES_FILE", str(REPO / "configs/sensitivity-rules.yaml"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v3.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))
    rc = meeting_cli.main(
        [
            "ingest",
            "--file", str(FIXTURES / "meeting-clean.md"),
            "--recorded-response", str(FIXTURES / "recorded-clean.json"),
            "--offline",
            "--notify-channel", "TEST",
        ]
    )
    assert rc == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["todos"] >= 3 and result["milestones"] >= 2
    assert result["glm_called"] is False and result["provider"] == "recorded"
    cards = (tmp_path / "plan/kanban-plan.jsonl").read_text().splitlines()
    assert len(cards) >= 3
    state = (tmp_path / "state/milestones.yaml").read_text()
    assert state.count("  - title: ") >= 2
    assert (tmp_path / "plan/team-post.txt").read_text().startswith("```json")


# --- sensitivity gate ---------------------------------------------------------


def test_gate_clean_fixture_not_sensitive():
    text = (FIXTURES / "meeting-clean.md").read_text(encoding="utf-8")
    assert meeting_gate.evaluate(text, RULES).sensitive is False


def test_gate_patent_fixture_sensitive():
    text = (FIXTURES / "meeting-patent.md").read_text(encoding="utf-8")
    verdict = meeting_gate.evaluate(text, RULES)
    assert verdict.sensitive is True
    assert "patent-sensitive" in verdict.tags


def test_gate_english_patterns():
    verdict = meeting_gate.evaluate("Discussed the patent claims for PCT.", RULES)
    assert verdict.sensitive is True


def test_rules_and_prompt_copies_in_sync():
    for canonical, copy in [
        ("configs/sensitivity-rules.yaml", "skills/meeting/configs/sensitivity-rules.yaml"),
        ("prompts/meeting-extraction-v3.md", "skills/meeting/prompts/meeting-extraction-v3.md"),
    ]:
        assert (REPO / canonical).read_bytes() == (REPO / copy).read_bytes()

def test_prompt_template_excludes_doc_header():
    """Header prose mentions <<<PROMPT>>>; the loader must anchor to the marker LINE.

    Regression: substring split leaked header text ("변경 시 버전 파일명을
    올린다") into the LLM prompt and codex created v3/v4 template files.
    """
    template = meeting_llm.load_prompt_template(REPO / "prompts/meeting-extraction-v3.md")
    assert template.startswith("아래 회의록을 읽어라.")
    prompt = meeting_llm.build_prompt(template, meeting_text="본문", my_names="cha")
    assert "버전 파일명" not in prompt and "치환해" not in prompt


def test_fallback_parser_matches_yaml():
    raw = (REPO / "configs/sensitivity-rules.yaml").read_text(encoding="utf-8")
    fallback = meeting_gate._parse_rules_fallback(raw)
    spec = fallback["tags"]["patent-sensitive"]
    rule = RULES[0]
    assert tuple(spec["keywords"]) == rule.keywords
    assert len(spec["patterns"]) == len(rule.patterns)


# --- refusals -----------------------------------------------------------------


def test_size_reject_before_read(tmp_path):
    big = tmp_path / "big.md"
    with big.open("wb") as handle:
        handle.seek(26 * 1024 * 1024)
        handle.write(b"x")
    with pytest.raises(meeting_extract.ExtractionRefused) as exc:
        meeting_extract.extract_file(big)
    assert exc.value.exit_code == 3
    assert "크기 초과" in exc.value.notice


def test_scanned_pdf_manual_conversion(tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-fake")
    with pytest.raises(meeting_extract.ExtractionRefused) as exc:
        meeting_extract.extract_file(pdf, pdf_runner=lambda _: "\x0c\n ")
    assert exc.value.exit_code == 4
    assert "수동 변환 요청" in exc.value.notice


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler absent")
def test_generated_pdfs_roundtrip(tmp_path):
    for flag, expect_ok in [("--text", True), ("--scanned", False)]:
        out = tmp_path / f"f{flag.strip('-')}.pdf"
        subprocess.run(
            [sys.executable, str(SKILL / "scripts/make_fixture_pdf.py"), str(out), flag],
            check=True,
        )
        if expect_ok:
            extracted = meeting_extract.extract_file(out)
            assert extracted.kind == "pdf" and "2026-08-01" in extracted.text
        else:
            with pytest.raises(meeting_extract.ExtractionRefused):
                meeting_extract.extract_file(out)


def test_unsupported_extension(tmp_path):
    other = tmp_path / "x.docx"
    other.write_text("hello meeting")
    with pytest.raises(meeting_extract.ExtractionRefused) as exc:
        meeting_extract.extract_file(other)
    assert exc.value.exit_code == 5


# --- patent routing + sanitization ---------------------------------------------


def test_call_litellm_refuses_sensitive():
    with pytest.raises(meeting_llm.PatentRoutingError):
        meeting_llm.call_litellm(
            "x", sensitive=True, base_url="http://127.0.0.1:1", api_key="none"
        )


def test_sensitive_cards_and_milestones_sanitized(tmp_path):
    raw = (FIXTURES / "recorded-patent.json").read_text(encoding="utf-8")
    extraction = meeting_llm.parse_extraction(raw)
    cards = meeting_actions.plan_cards(
        extraction, sensitive=True, note_name="n.md", ref="deadbeef"
    )
    assert len(cards) == len(extraction.todos)
    state = tmp_path / "milestones.yaml"
    meeting_actions.update_milestones(
        state, extraction.milestones, sensitive=True, note_name="n.md",
        ref="deadbeef", now=NOW,
    )
    public = " ".join(card.title + card.body for card in cards) + state.read_text()
    for banned in ("특허", "출원", "청구항", "claim", "변리사", "선행기술", "기술이전"):
        assert banned not in public
    assert meeting_actions.format_team_post(
        extraction.others, agent_id="a", ref="deadbeef", now=NOW
    ) is not None  # caller suppresses it for sensitive docs (cli guard)

# --- item-level card recheck (sensitive docs) -----------------------------------


def _todo(title, deadline, basis):
    return meeting_llm.ActionItem(title=title, deadline=deadline, basis=basis)


def test_sensitive_clean_item_gets_informative_card():
    card = meeting_actions.sanitize_card(
        _todo("주간 보고서 정리", "2026-08-01", "회의에서 합의"),
        sensitive=True, seq=1, note_name="n.md", ref="deadbeef", rules=RULES,
    )
    assert card.title == "[민감회의] 주간 보고서 정리 (마감 2026-08-01)"
    assert card.body.startswith("근거: 회의에서 합의")
    assert "출처: ~/notes/meetings/n.md" in card.body
    assert card.idempotency_key == "meeting:deadbeef:todo:1"
    assert not meeting_gate.evaluate(card.title + "\n" + card.body, RULES).tags


def test_sensitive_item_hit_stays_masked_byte_identical():
    item = _todo("청구항 초안 작성", "2026-07-28", "청구항 초안은 차가 7/28까지")
    with_rules = meeting_actions.sanitize_card(
        item, sensitive=True, seq=2, note_name="n.md", ref="deadbeef", rules=RULES
    )
    legacy = meeting_actions.sanitize_card(
        item, sensitive=True, seq=2, note_name="n.md", ref="deadbeef"
    )
    assert with_rules == legacy
    assert with_rules.title == "[민감] 회의 액션아이템 2"
    assert "청구항" not in with_rules.title + with_rules.body


def test_sensitive_recheck_covers_deadline_and_basis():
    card = meeting_actions.sanitize_card(
        _todo("미팅 준비", "2026-08-05", "변리사 미팅은 8/5"),
        sensitive=True, seq=1, note_name="n.md", ref="deadbeef", rules=RULES,
    )
    assert card.title == "[민감] 회의 액션아이템 1"
    assert "변리사" not in card.title + card.body


def test_sensitive_rules_none_fail_closed():
    card = meeting_actions.sanitize_card(
        _todo("주간 보고서 정리", "2026-08-01", "회의에서 합의"),
        sensitive=True, seq=1, note_name="n.md", ref="deadbeef", rules=None,
    )
    assert card.title == "[민감] 회의 액션아이템 1"


def test_informative_title_prefix_inside_clip():
    card = meeting_actions.sanitize_card(
        _todo("가" * 120, None, "근거"),
        sensitive=True, seq=1, note_name="n.md", ref="deadbeef", rules=RULES,
    )
    assert card.title.startswith("[민감회의] ")
    assert len(card.title) <= 80


def test_plan_cards_mixed_items_and_passthrough():
    extraction = meeting_llm.Extraction(
        todos=(
            _todo("주간 보고서 정리", "2026-08-01", "회의에서 합의"),
            _todo("기술이전 검토 자료 정리", None, "기술이전 가능성 검토"),
        )
    )
    cards = meeting_actions.plan_cards(
        extraction, sensitive=True, note_name="n.md", ref="deadbeef", rules=RULES
    )
    assert cards[0].title.startswith("[민감회의] ")
    assert cards[1].title == "[민감] 회의 액션아이템 2"
    assert [card.idempotency_key for card in cards] == [
        "meeting:deadbeef:todo:1",
        "meeting:deadbeef:todo:2",
    ]


def test_note_keeps_original_detail(tmp_path):
    raw = (FIXTURES / "recorded-patent.json").read_text(encoding="utf-8")
    extraction = meeting_llm.parse_extraction(raw)
    original = (FIXTURES / "meeting-patent.md").read_text(encoding="utf-8")
    note = meeting_actions.write_note(
        tmp_path, label="민감 회의", kind="md", original_text=original,
        extraction=extraction, sensitive=True, ref="deadbeef", now=NOW,
    )
    content = note.read_text(encoding="utf-8")
    assert "청구항" in content and "patent-sensitive" in content
    assert note.stat().st_mode & 0o777 == 0o600


# --- parsing robustness ---------------------------------------------------------


def test_parse_extraction_with_fences_and_junk():
    raw = 'chatter\n```json\n{"todos": [{"title": "t", "deadline": "2026-01-02", "basis": "b"}], "milestones": [], "others": [], "decisions": ["d"]}\n```\ntrailing'
    extraction = meeting_llm.parse_extraction(raw)
    assert extraction.todos[0].deadline == "2026-01-02"
    assert extraction.decisions == ("d",)


def test_parse_extraction_rejects_garbage():
    with pytest.raises(meeting_llm.ExtractionParseError):
        meeting_llm.parse_extraction("no json here")


def test_milestones_dedupe(tmp_path):
    state = tmp_path / "m.yaml"
    items = meeting_llm.parse_extraction(
        (FIXTURES / "recorded-clean.json").read_text(encoding="utf-8")
    ).milestones
    first = meeting_actions.update_milestones(
        state, items, sensitive=False, note_name="n.md", ref="r1", now=NOW
    )
    second = meeting_actions.update_milestones(
        state, items, sensitive=False, note_name="n.md", ref="r1", now=NOW
    )
    assert first == len(items) and second == 0
    assert state.read_text().count("  - title: ") == len(items)


# --- plugin trigger logic --------------------------------------------------------


def _plugin_spies(monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_config", lambda: {"owner_id": "OWNER1"})
    launches: list = []
    posts: list = []
    monkeypatch.setattr(plugin, "_launch", lambda trigger, python_bin: launches.append(trigger))
    monkeypatch.setattr(plugin, "_post", lambda chat_id, content: posts.append((chat_id, content)))
    return plugin, launches, posts


def test_plugin_does_not_trigger_for_nonmeeting_message_txt(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    event = _Event(
        text="첨부된 메일 내용을 확인해줘",
        media_urls=["/cache/doc_ab12cd34ef56_message.txt"],
        media_types=["text/plain; charset=utf-8"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None) is None
    assert launches == []
    assert posts == []


@pytest.mark.parametrize(
    ("path", "mime"),
    [
        ("/cache/doc_ab12cd34ef56_notes.txt", ""),
        ("/cache/doc_ab12cd34ef56_blob", "text/plain"),
        ("/cache/doc_ab12cd34ef56_agenda.pdf", "application/pdf"),
        ("/cache/doc_ab12cd34ef56_weekly-meeting-minutes.md", "text/markdown"),
    ],
)
def test_plugin_does_not_infer_intent_from_extension_or_mime(monkeypatch, path, mime):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    event = _Event(text="자료 전달", media_urls=[path], media_types=[mime])
    assert plugin.pre_gateway_dispatch(event, None, None) is None
    assert launches == []
    assert posts == []


@pytest.mark.parametrize(
    "text",
    [
        "메일 예시에는 !meeting 명령이 포함되어 있습니다",
        "!meetingfoo 합성 회의 본문",
        "!meeting-archive 합성 회의 본문",
    ],
)
def test_plugin_requires_bounded_command_at_start(monkeypatch, text):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    assert plugin.pre_gateway_dispatch(_Event(text=text), None, None) is None
    assert launches == []
    assert posts == []


@pytest.mark.parametrize("value", [False, None, "true", "True", 1, [], {}])
def test_plugin_requires_boolean_trusted_metadata_signal(monkeypatch, value):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    event = _Event(
        media_urls=["/cache/doc_ab12cd34ef56_notes.md"],
        media_types=["text/markdown"],
        metadata={plugin.MEETING_INTENT_METADATA_KEY: value},
    )
    assert plugin.pre_gateway_dispatch(event, None, None) is None
    assert launches == []
    assert posts == []


def test_plugin_preserves_explicit_meeting_body_command(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    command = _Event(text="  !MeEtInG 오늘 회의: 차는 보고서 작성  ")
    assert plugin.pre_gateway_dispatch(command, None, None) == {
        "action": "skip",
        "reason": "meeting_ingest",
    }
    assert launches[0].doc_paths == ()
    assert launches[0].body == "오늘 회의: 차는 보고서 작성"
    assert posts == [("C1", plugin.ACK_MESSAGE)]


def test_plugin_preserves_explicit_upload_using_raw_caption(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    # Discord inlines small text attachments before the caption in event.text.
    event = _Event(
        text="[Content of notes.txt]\n합성 회의 본문\n!meeting",
        raw_content="!meeting",
        media_urls=["/cache/doc_ab12cd34ef56_notes.txt"],
        media_types=["text/plain"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"
    assert launches[0].doc_paths == ("/cache/doc_ab12cd34ef56_notes.txt",)
    assert launches[0].body is None
    assert posts == [("C1", plugin.ACK_MESSAGE)]


def test_plugin_does_not_promote_command_inside_attachment_when_raw_caption_empty(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    event = _Event(
        text="[Content of message.txt]\n!meeting quoted example from the attachment",
        raw_content="",
        media_urls=["/cache/doc_ab12cd34ef56_message.txt"],
        media_types=["text/plain"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None) is None
    assert launches == []
    assert posts == []


def test_plugin_preserves_trusted_metadata_intent(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    event = _Event(
        media_urls=["/cache/doc_ab12cd34ef56_notes.md"],
        media_types=["text/markdown"],
        metadata={plugin.MEETING_INTENT_METADATA_KEY: True},
    )
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"
    assert launches[0].doc_paths == ("/cache/doc_ab12cd34ef56_notes.md",)
    assert launches[0].body is None
    assert posts == [("C1", plugin.ACK_MESSAGE)]


def test_plugin_rejects_other_users_and_bots_even_with_explicit_signal(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    assert plugin.pre_gateway_dispatch(
        _Event(text="!meeting x", user_id="OTHER"), None, None
    ) is None
    assert plugin.pre_gateway_dispatch(
        _Event(metadata={plugin.MEETING_INTENT_METADATA_KEY: True}, is_bot=True), None, None
    ) is None
    assert launches == []
    assert posts == []


def test_nonmeeting_gate_has_zero_meeting_side_effects(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_config", lambda: {"owner_id": "OWNER1"})

    def forbidden_launch(trigger, python_bin):
        for name in (
            "kanban-card.json",
            "meeting-note.md",
            "milestones.yaml",
            "team-action.txt",
            "ingest-log.jsonl",
        ):
            (tmp_path / name).write_text("unexpected")

    def forbidden_post(chat_id, content):
        (tmp_path / "team-post.txt").write_text("unexpected")

    monkeypatch.setattr(plugin, "_launch", forbidden_launch)
    monkeypatch.setattr(plugin, "_post", forbidden_post)
    event = _Event(
        text="이 첨부를 검토해줘",
        media_urls=["/cache/doc_ab12cd34ef56_message.txt"],
        media_types=["text/plain"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None) is None
    assert list(tmp_path.iterdir()) == []


# --- t_f409f47b: artifact producers must be call-counted, not merely absent ----

# Every function that materializes a meeting artifact. `plan_cards` builds the
# Kanban cards, `write_note` the meeting note, `update_milestones` the
# milestones.yaml rows, `format_team_post` the #team action items.
_ARTIFACT_PRODUCERS = (
    "write_note",
    "plan_cards",
    "update_milestones",
    "format_team_post",
)


def _artifact_call_counts(monkeypatch) -> dict[str, int]:
    """Count calls on every artifact producer while keeping real behaviour."""
    counts = dict.fromkeys(_ARTIFACT_PRODUCERS, 0)

    def install(name: str) -> None:
        original = getattr(meeting_actions, name)

        def counted(*args, **kwargs):
            counts[name] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(meeting_actions, name, counted)

    for name in _ARTIFACT_PRODUCERS:
        install(name)
    return counts


def test_nonmeeting_message_txt_upload_calls_zero_artifact_producers(monkeypatch):
    """Given the incident input (a non-meeting `message.txt` upload with no
    `!meeting` signal), When the gate runs with the REAL ``_launch``, Then no CLI
    process is spawned and every artifact producer records zero calls.

    ``_spawn == []`` strictly dominates the live Kanban path: ``_run_kanban``
    only executes inside the spawned CLI process, so no spawn means no card.
    """
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_config", lambda: {"owner_id": "OWNER1"})
    spawns: list = []
    posts: list = []
    monkeypatch.setattr(plugin, "_spawn", lambda argv: spawns.append(argv))
    monkeypatch.setattr(plugin, "_post", lambda chat_id, content: posts.append(chat_id))
    counts = _artifact_call_counts(monkeypatch)

    event = _Event(
        text="첨부된 메일 내용을 확인해줘",
        media_urls=["/cache/doc_ab12cd34ef56_message.txt"],
        media_types=["text/plain; charset=utf-8"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None) is None

    assert spawns == []
    assert posts == []
    assert counts == dict.fromkeys(_ARTIFACT_PRODUCERS, 0)


def test_explicit_meeting_command_calls_every_artifact_producer(tmp_path, monkeypatch):
    """Given an explicit `!meeting` body, When the same spy harness runs the
    offline pipeline, Then every artifact producer records exactly one call.

    This is the discriminating half of the pair: without it the zero-call
    assertions above would hold even if the spies were never wired up.
    """
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_STATE_FILE", str(tmp_path / "state/milestones.yaml"))
    monkeypatch.setenv("MEETING_RULES_FILE", str(REPO / "configs/sensitivity-rules.yaml"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v3.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_config", lambda: {"owner_id": "OWNER1"})
    monkeypatch.setattr(plugin, "_post", lambda chat_id, content: None)
    counts = _artifact_call_counts(monkeypatch)

    def offline_launch(trigger, python_bin):
        body_file = tmp_path / "explicit-meeting.txt"
        body_file.write_text(trigger.body, encoding="utf-8")
        assert meeting_cli.main(
            [
                "ingest",
                "--body-file", str(body_file),
                "--label", "합성 명시 회의",
                "--recorded-response", str(FIXTURES / "recorded-clean.json"),
                "--offline",
                "--notify-channel", trigger.chat_id,
            ]
        ) == 0

    monkeypatch.setattr(plugin, "_launch", offline_launch)
    event = _Event(text="!meeting 합성 주간회의 기록입니다\n- 차: 다음 주까지 보고서 작성")
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"

    assert counts == dict.fromkeys(_ARTIFACT_PRODUCERS, 1)


def test_explicit_meeting_command_still_creates_offline_outputs(tmp_path, monkeypatch, capsys):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_config", lambda: {"owner_id": "OWNER1"})
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_STATE_FILE", str(tmp_path / "state/milestones.yaml"))
    monkeypatch.setenv("MEETING_RULES_FILE", str(REPO / "configs/sensitivity-rules.yaml"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v3.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))
    posts = []
    monkeypatch.setattr(plugin, "_post", lambda chat_id, content: posts.append((chat_id, content)))

    def offline_launch(trigger, python_bin):
        assert trigger.doc_paths == ()
        body_file = tmp_path / "explicit-meeting.txt"
        body_file.write_text(trigger.body, encoding="utf-8")
        assert meeting_cli.main(
            [
                "ingest",
                "--body-file", str(body_file),
                "--label", "합성 명시 회의",
                "--recorded-response", str(FIXTURES / "recorded-clean.json"),
                "--offline",
                "--notify-channel", trigger.chat_id,
            ]
        ) == 0

    monkeypatch.setattr(plugin, "_launch", offline_launch)
    event = _Event(
        text="!meeting 합성 주간회의 기록입니다\n- 차: 다음 주까지 보고서 작성\n- 결정: 초안을 검토한다"
    )
    assert plugin.pre_gateway_dispatch(event, None, None) == {
        "action": "skip",
        "reason": "meeting_ingest",
    }
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["todos"] >= 3 and result["milestones"] >= 2
    assert len((tmp_path / "plan/kanban-plan.jsonl").read_text().splitlines()) >= 3
    assert len(list((tmp_path / "notes").glob("*.md"))) == 1
    assert (tmp_path / "state/milestones.yaml").exists()
    assert (tmp_path / "plan/team-post.txt").exists()
    assert posts == [("C1", plugin.ACK_MESSAGE)]


def test_plugin_triggers_multiple_supported_docs_only(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    event = _Event(
        text="!meeting",
        media_urls=["/cache/a.md", "/cache/b.pdf", "/cache/photo.png"],
        media_types=["text/markdown", "application/pdf", "image/png"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"
    assert launches[0].doc_paths == ("/cache/a.md", "/cache/b.pdf")
    assert posts == [("C1", plugin.ACK_MESSAGE)]


def test_plugin_preserves_nontrigger_general_flow(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    downstream = []
    result = plugin.pre_gateway_dispatch(_Event(text="일반 요청"), None, None)
    if result is None:
        downstream.append("called")
    assert downstream == ["called"]
    assert launches == []
    assert posts == []


def test_plugin_fail_closed_after_trigger(monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_config", lambda: {"owner_id": "OWNER1"})

    def boom(trigger, python_bin):
        raise RuntimeError("launch broke")

    monkeypatch.setattr(plugin, "_launch", boom)
    monkeypatch.setattr(plugin, "_post", lambda chat_id, content: None)
    event = _Event(
        text="!meeting",
        media_urls=["/cache/doc_ab12cd34ef56_m.pdf"],
        media_types=["application/pdf"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"
