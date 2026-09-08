"""VA-1 release approval binding: one version, HEAD, and complete surface digest set."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from automation import release_approval, skill_gate, skill_gate_request
from automation.interop.approval_surface import (
    POLICY_VERSION,
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    required_surface,
)
from automation.release_approval import (
    DECISION_APPROVED,
    DECISION_DENIED,
    DECISION_PENDING,
    decision_exit,
    spec_from_plan,
    spec_from_record,
)
from automation.interop.approval_lifecycle import Probe
from automation.interop.discord_transport import SentMessage
from automation.release_spec import ReleaseSpec, ReleaseSpecError


def _spec() -> ReleaseSpec:
    return ReleaseSpec(
        version="v1.2.3",
        head_sha="a" * 40,
        release_nonce="b" * 32,
        surface_digests=(
            ("home:skills/mail", "c" * 64),
            ("skill:meeting", "d" * 64),
        ),
        patch_notes="- mail wrapper\n- meeting skill",
    )


_MESSAGE_ID = "1538547247514525816"
_REFUSAL = "REFUSED: approval request not posted outcome=deferred reason=binding-mismatch"


def _binding() -> ApprovalBinding:
    return ApprovalBinding(
        ApprovalKind.RELEASE, ApprovalSurface.SKILL_APPROVALS, "999", POLICY_VERSION
    )


def _pending(gate_dir: Path) -> dict[str, str]:
    record = _spec().new_record(_MESSAGE_ID, _binding())
    path = gate_dir / "pending" / "release.json"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    return record


def _plan_file(tmp_path: Path, patch_notes: str = "") -> str:
    spec = _spec()
    path = tmp_path / "plan.json"
    _ = path.write_text(
        json.dumps(
            {
                "version": spec.version,
                "head": spec.head_sha,
                "surface_digests": [list(row) for row in spec.surface_digests],
                "patch_notes": patch_notes or spec.patch_notes,
            }
        ),
        encoding="utf-8",
    )
    return str(path)


_LONG_NOTES = "\n".join(f"- 변경 {index} " + "가" * 60 for index in range(60))


class _RecordingTransport:
    """Discord 대신 순서와 본문만 기록한다 — 테스트는 절대 망을 열지 않는다."""

    def __init__(self, fail_after: int | None = None) -> None:
        self.sent: list[str] = []
        self._fail_after = fail_after

    def send(self, body: str) -> tuple[SentMessage, ...]:
        if self._fail_after is not None and len(self.sent) >= self._fail_after:
            raise OSError("discord is unreachable")
        self.sent.append(body)
        return (SentMessage(message_id=f"detail-{len(self.sent)}"),)


class _PostingGate:
    """카드가 이미 올라간 뒤의 게이트 — 채널과 레코드 경로만 답한다."""

    def __init__(self, gate_dir: Path) -> None:
        self._path = gate_dir / "pending" / "release.json"
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    def channel_id(self) -> str:
        return "1528936606856122421"

    def path(self) -> Path:
        return self._path


def _posted(
    monkeypatch: pytest.MonkeyPatch, gate: _PostingGate, transport: _RecordingTransport
) -> list[str]:
    """카드가 새로 게시된 경로를 세운다 — 반환값은 transport 가 받은 채널 목록이다."""
    record = _spec().new_record(_MESSAGE_ID, _binding())
    channels: list[str] = []
    monkeypatch.setattr(release_approval, "_gate", lambda spec: gate)
    monkeypatch.setattr(skill_gate_request, "reuse", lambda gate: None)
    monkeypatch.setattr(
        skill_gate_request,
        "post_request",
        lambda gate, *, fresh: skill_gate_request.Requested(record, 0, posted=True),
    )

    def transport_for(channel_id: str) -> _RecordingTransport:
        channels.append(channel_id)
        return transport

    monkeypatch.setattr(release_approval, "detail_transport", transport_for)
    return channels


class _StubGate:
    """저장된 레코드의 프로브만 흉내낸다 — Discord 도, 실제 게이트 상태도 건드리지 않는다."""

    def __init__(self, probe: Probe) -> None:
        self._probe = probe

    def outstanding(self, key: str) -> tuple[str, ...]:
        return ("live",)

    def probe(self, request: str) -> Probe:
        return self._probe


def _refused(monkeypatch: pytest.MonkeyPatch, message: str) -> None:
    monkeypatch.setattr(skill_gate_request, "reuse", lambda gate: None)
    monkeypatch.setattr(
        skill_gate_request,
        "post_request",
        lambda gate, *, fresh: skill_gate_request.Requested(None, 6, message),
    )


def test_the_abandon_subcommand_delegates_to_the_audited_release_abandon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """워크스테이션에는 게이트 상태가 없다 — 같은 producer 표면으로 감사된 abandon 에 닿는다."""
    record = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "logs" / "approvals.jsonl")
    monkeypatch.setenv("SUDO_USER", "cha")

    exit_code = release_approval.main(
        [
            "abandon",
            "--version",
            record["version"],
            "--head",
            record["head_sha"],
            "--message-id",
            record["message_id"],
            "--reason",
            "stale pending release superseded by origin/main advance",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "RELEASE-ABANDONED" in captured.out
    assert not (tmp_path / "pending" / "release.json").exists()
    audited = [
        json.loads(line)
        for line in (tmp_path / "logs" / "approval-abandons.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [line["event"] for line in audited] == ["release-abandon"]
    assert audited[0]["actor"] == "cha"
    assert audited[0]["head_sha"] == record["head_sha"]
    assert [path.name for path in (tmp_path / "release-abandoned").iterdir()]


def test_a_binding_mismatch_refusal_names_the_blocking_record_and_its_own_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """release.sh 의 자동 복구는 이 한 줄만 읽는다 — 거절 메시지와 종료 코드는 그대로다."""
    record = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _StubGate(Probe.BOUND_PENDING))
    _refused(monkeypatch, _REFUSAL)

    exit_code = release_approval.main(["request", "--plan-file", _plan_file(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 6
    lines = captured.err.splitlines()
    assert _REFUSAL in lines
    assert (
        f"RELEASE-REQUEST-STALE: version={record['version']} head={record['head_sha']}"
        f" message_id={record['message_id']} probe=bound_pending"
    ) in lines


@pytest.mark.parametrize(
    ("message", "stored"),
    (
        (_REFUSAL, False),  # 레코드를 읽을 수 없으면 아무 것도 덧붙이지 않는다
        ("REFUSED: approval request not posted outcome=deferred reason=owner-decided", True),
    ),
)
def test_only_a_readable_binding_mismatch_earns_the_extra_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    message: str,
    stored: bool,
) -> None:
    if stored:
        _ = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _StubGate(Probe.BOUND_PENDING))
    _refused(monkeypatch, message)

    exit_code = release_approval.main(["request", "--plan-file", _plan_file(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 6
    assert message in captured.err.splitlines()
    assert "RELEASE-REQUEST-STALE:" not in captured.err


def test_decision_names_the_version_bound_to_the_current_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A caller can reuse a live request's immutable version without changing its record."""
    record = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _StubGate(Probe.APPROVED))

    exit_code = release_approval.main(["decision", "--head", record["head_sha"]])

    captured = capsys.readouterr()
    assert exit_code == DECISION_APPROVED
    assert captured.err == f"RELEASE-DECISION: approved version={record['version']}\n"


def test_release_kind_is_permanently_routed_to_approvals() -> None:
    assert ApprovalKind.RELEASE.value == "release"
    assert required_surface(ApprovalKind.RELEASE) is ApprovalSurface.SKILL_APPROVALS


def test_action_hash_binds_version_head_and_sorted_surface_digests() -> None:
    spec = _spec()
    reordered = replace(spec, surface_digests=tuple(reversed(spec.surface_digests)))

    assert spec.action_hash() == reordered.action_hash()
    assert spec.action_hash() != replace(spec, version="v1.2.4").action_hash()
    assert spec.action_hash() != replace(spec, head_sha="e" * 40).action_hash()
    assert spec.action_hash() != replace(
        spec,
        surface_digests=(("skill:meeting", "f" * 64),),
    ).action_hash()


def test_action_hash_excludes_the_random_nonce() -> None:
    spec = _spec()

    assert spec.action_hash() == replace(spec, release_nonce="0" * 32).action_hash()


def test_record_persists_every_authorizing_field_and_surface_binding() -> None:
    spec = _spec()
    binding = ApprovalBinding(
        ApprovalKind.RELEASE,
        ApprovalSurface.SKILL_APPROVALS,
        "1528936606856122421",
        POLICY_VERSION,
    )

    record = spec.new_record("1538547247514525816", binding)

    assert record["version"] == spec.version
    assert record["head_sha"] == spec.head_sha
    assert record["release_nonce"] == spec.release_nonce
    assert record["surface_digests"]
    assert record["kind"] == "release"
    assert record["surface"] == "skill-approvals"
    assert record["channel_id"] == binding.channel_id
    assert record["policy_version"] == str(POLICY_VERSION)
    assert record["render_version"] == "3"
    assert spec.bound(spec.render(), record)


def test_any_record_or_message_change_breaks_the_binding() -> None:
    spec = _spec()
    binding = ApprovalBinding(
        ApprovalKind.RELEASE,
        ApprovalSurface.SKILL_APPROVALS,
        "1528936606856122421",
        POLICY_VERSION,
    )
    record = spec.new_record("1538547247514525816", binding)

    assert not spec.bound(spec.render() + "\nchanged", record)
    assert not spec.bound(spec.render(), {**record, "head_sha": "0" * 40})


def test_release_message_is_fail_closed_above_1900_characters() -> None:
    """카드 자체가 한도를 넘으면 게시하지 않는다 — 잘라내기는 어디에도 없다."""
    crowded = tuple((f"skill:demo-{index:03d}", f"{index:064x}") for index in range(110))

    with pytest.raises(ReleaseSpecError, match="1900"):
        _ = replace(_spec(), surface_digests=crowded).render()


def test_the_card_points_at_the_detail_messages_instead_of_carrying_them() -> None:
    """Given a v3 release spec / When the card renders / Then it names the bundles,
    the binding and how many detail messages follow — and no patch-note line."""
    spec = _spec()

    lines = spec.render().splitlines()

    assert lines[:5] == [
        "[release] v1.2.3 배포 승인 요청",
        f"- 배포 기준: `{spec.head_sha}`",
        "- 배포 번들 (2): `home:skills/mail`, `skill:meeting`",
        f"- 승인 바인딩: `{spec.action_hash()}`",
        f"- 변경 상세: 아래 1개 메시지 (같은 릴리스 v1.2.3 · 기준 {spec.head_sha[:12]})",
    ]
    assert lines[5].startswith("- 승인 방법:")
    assert len(lines) == 6
    assert spec.patch_notes not in spec.render()
    assert spec.release_nonce not in spec.render()
    assert all(digest not in spec.render() for _name, digest in spec.surface_digests)


def test_a_major_release_card_carries_the_operator_line_before_the_detail_pointer() -> None:
    """Given machine-contract signals / When the card renders /
    Then the operator line stands above the detail pointer."""
    spec = replace(_spec(), major_note="MAJOR: 운영자 조치 필요 — automation/x.py:SCHEMA_VERSION")

    lines = spec.render().splitlines()

    assert lines[4] == spec.major_note
    assert lines[5].startswith("- 변경 상세: ")


def test_the_card_counts_every_detail_message_it_points_at() -> None:
    """Given change notes longer than one message / When the card renders /
    Then its count equals the messages the same spec produces."""
    spec = replace(
        _spec(),
        patch_notes="\n".join(f"- 변경 {index} " + "가" * 60 for index in range(60)),
    )

    messages = spec.detail_messages()

    assert len(messages) > 1
    assert f"- 변경 상세: 아래 {len(messages)}개 메시지 " in spec.render()


def test_the_frozen_v1_and_v2_renders_stay_byte_identical() -> None:
    """이미 게시된 승인은 그 바이트로만 재검증된다 — 옛 버전 문구는 영구 동결이다."""
    spec = _spec()

    assert replace(spec, render_version=1).render() == (
        "[release] v1.2.3 배포 승인 요청\n"
        "- version: `v1.2.3`\n"
        f"- HEAD: `{'a' * 40}`\n"
        f"- release_nonce: `{'b' * 32}`\n"
        f"- surface `home:skills/mail`: `{'c' * 64}`\n"
        f"- surface `skill:meeting`: `{'d' * 64}`\n"
        "- 패치노트:\n"
        "- mail wrapper\n"
        "- meeting skill\n"
        "- 승인 방법: 이 메시지에 cha가 ✅ 리액션 (소유자 전용 — 봇/타인 리액션은 거부됨)"
    )
    assert replace(spec, render_version=2).render() == (
        "[release] v1.2.3 배포 승인 요청\n"
        f"- 배포 기준: `{'a' * 40}`\n"
        "- 배포 번들 (2): `home:skills/mail`, `skill:meeting`\n"
        f"- 승인 바인딩: `{spec.action_hash()}`\n"
        "- 변경 내용:\n"
        "- mail wrapper\n"
        "- meeting skill\n"
        "- 승인 방법: 이 메시지에 cha가 ✅ 리액션 (소유자 전용 — 봇/타인 리액션은 거부됨)"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("version", "latest"),
        ("head_sha", "not-a-sha"),
        ("release_nonce", "short"),
        ("surface_digests", (("skill:mail", "short"),)),
    ),
)
def test_malformed_authorizing_fields_are_refused(field: str, value: object) -> None:
    with pytest.raises(ReleaseSpecError):
        _ = replace(_spec(), **{field: value})


def test_a_stored_record_replays_into_the_same_action_hash() -> None:
    """세션이 죽은 뒤의 decision 폴링은 레코드만으로 같은 스펙을 복원해야 한다."""
    spec = _spec()
    binding = ApprovalBinding(
        ApprovalKind.RELEASE,
        ApprovalSurface.SKILL_APPROVALS,
        "1528936606856122421",
        POLICY_VERSION,
    )
    record = spec.new_record("1538547247514525816", binding)

    replay = spec_from_record(record)

    assert replay.action_hash() == spec.action_hash()
    assert replay.render() == spec.render()


def test_legacy_record_without_version_replays_the_frozen_v1_message() -> None:
    spec = _spec()
    binding = ApprovalBinding(
        ApprovalKind.RELEASE,
        ApprovalSurface.SKILL_APPROVALS,
        "1528936606856122421",
        POLICY_VERSION,
    )
    legacy = spec.new_record("1538547247514525816", binding)
    legacy.pop("render_version", None)

    replay = spec_from_record(legacy)
    rendered = replay.render()

    assert replay.render_version == 1
    assert replay.release_nonce in rendered
    assert all(digest in rendered for _name, digest in replay.surface_digests)
    assert replay.action_hash() not in rendered


def test_a_plan_payload_builds_the_spec_the_request_posts() -> None:
    spec = _spec()
    payload = {
        "version": spec.version,
        "head": spec.head_sha,
        "surface_digests": [list(row) for row in spec.surface_digests],
        "patch_notes": spec.patch_notes,
    }

    built = spec_from_plan(payload, spec.release_nonce)

    assert built.action_hash() == spec.action_hash()


def test_decision_exit_maps_owner_probes_and_keeps_uncertainty_pending() -> None:
    assert decision_exit(Probe.APPROVED) == DECISION_APPROVED
    assert decision_exit(Probe.CANCELLED) == DECISION_DENIED
    assert decision_exit(Probe.BOUND_PENDING) == DECISION_PENDING
    assert decision_exit(Probe.UNVERIFIABLE) == DECISION_PENDING
    assert decision_exit(Probe.MISSING) == DECISION_PENDING


def test_the_detail_messages_follow_the_freshly_posted_card_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a card that was just posted / When the request command finishes /
    Then every detail message reaches the same channel, in order, and is recorded."""
    transport = _RecordingTransport()
    gate = _PostingGate(tmp_path)
    channels = _posted(monkeypatch, gate, transport)
    plan_file = _plan_file(tmp_path, _LONG_NOTES)
    expected = spec_from_plan(json.loads(Path(plan_file).read_text("utf-8")), "0" * 32)

    exit_code = release_approval.main(["request", "--plan-file", plan_file])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert len(transport.sent) > 1
    assert tuple(transport.sent) == expected.detail_messages()
    assert channels == ["1528936606856122421"]
    emitted = json.loads(captured.out)
    posted_ids = [f"detail-{index}" for index in range(1, len(transport.sent) + 1)]
    assert json.loads(emitted["detail_message_ids"]) == posted_ids
    stored = json.loads(gate.path().read_text(encoding="utf-8"))
    assert json.loads(stored["detail_message_ids"]) == posted_ids
    assert stored["action_hash"] == emitted["action_hash"]


def test_a_reused_request_posts_no_detail_messages_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a live request the owner has not answered / When the command re-runs /
    Then nothing is posted a second time."""
    transport = _RecordingTransport()
    record = _spec().new_record(_MESSAGE_ID, _binding())
    monkeypatch.setattr(
        skill_gate_request, "reuse", lambda gate: skill_gate_request.Requested(record, 0)
    )
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _PostingGate(tmp_path))
    monkeypatch.setattr(
        release_approval, "detail_transport", lambda channel_id: transport
    )

    exit_code = release_approval.main(
        ["request", "--plan-file", _plan_file(tmp_path, _LONG_NOTES)]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert transport.sent == []
    assert "detail_message_ids" not in json.loads(captured.out)


def test_a_detail_post_failure_is_loud_and_leaves_the_card_binding_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given Discord fails midway through the detail messages / When the command ends /
    Then the failure names how many landed and the exit code stays the card's."""
    transport = _RecordingTransport(fail_after=1)
    gate = _PostingGate(tmp_path)
    _ = _posted(monkeypatch, gate, transport)
    plan_file = _plan_file(tmp_path, _LONG_NOTES)
    expected = spec_from_plan(json.loads(Path(plan_file).read_text("utf-8")), "0" * 32)

    exit_code = release_approval.main(["request", "--plan-file", plan_file])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert (
        f"RELEASE-DETAIL-POST-FAIL OSError posted=1/{len(expected.detail_messages())}"
        in captured.err.splitlines()
    )
    stored = json.loads(gate.path().read_text(encoding="utf-8"))
    assert json.loads(stored["detail_message_ids"]) == ["detail-1"]
