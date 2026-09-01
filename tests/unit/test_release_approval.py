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
from automation.release_spec import ReleaseSpec, ReleaseSpecError, fit_patch_notes


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


def _plan_file(tmp_path: Path) -> str:
    spec = _spec()
    path = tmp_path / "plan.json"
    _ = path.write_text(
        json.dumps(
            {
                "version": spec.version,
                "head": spec.head_sha,
                "surface_digests": [list(row) for row in spec.surface_digests],
                "patch_notes": spec.patch_notes,
            }
        ),
        encoding="utf-8",
    )
    return str(path)


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
    assert record["render_version"] == "2"
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
    with pytest.raises(ReleaseSpecError, match="1900"):
        _ = replace(_spec(), patch_notes="x" * 1900).render()


def test_release_message_prioritizes_human_changes_over_raw_gate_fields() -> None:
    spec = _spec()

    rendered = spec.render()

    assert spec.patch_notes in rendered
    assert spec.action_hash() in rendered
    assert all(name in rendered for name, _digest in spec.surface_digests)
    assert spec.release_nonce not in rendered
    assert all(digest not in rendered for _name, digest in spec.surface_digests)


def test_generated_patch_notes_fit_without_weakening_raw_fail_closed() -> None:
    surfaces = tuple((f"surface-{index}", f"{index:064x}") for index in range(14))
    notes = "\n".join(f"- change {index} " + "x" * 80 for index in range(20))

    fitted = fit_patch_notes(
        version="v1.2.3",
        head_sha="a" * 40,
        surface_digests=surfaces,
        patch_notes=notes,
    )
    rendered = ReleaseSpec(
        version="v1.2.3",
        head_sha="a" * 40,
        release_nonce="b" * 32,
        surface_digests=surfaces,
        patch_notes=fitted,
    ).render()

    assert len(rendered) <= 1900
    assert fitted != notes


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
