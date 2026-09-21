"""Stored approval bytes and new supply-chain card rendering."""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote

import pytest

from automation import skill_gate, skill_gate_publish, skill_gate_specs, skill_gate_surface
from automation.interop import owner_message
from automation.interop.approval_types import Probe
from automation.interop.approval_surface import ApprovalBinding, ApprovalKind, ApprovalSurface, ChannelFacts, RequestThread
from automation.skill_gate_approval import SkillApprovalGate

DEPLOY_V1 = (
    "[skill-deploy] calendar 배포 승인 요청\n"
    "- skill: `calendar`\n"
    "- sha256: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`\n"
    "- deploy_nonce: `11111111111111111111111111111111`\n"
    "- review: PASS\n"
    "- sandbox: PASS (peer 인스턴스, DUMMY 시크릿)\n"
    "- 승인 방법: 이 메시지에 cha가 ✅ 리액션 (소유자 전용 — 봇/타인 리액션은 거부됨)"
)
DEPLOY_OLD_HEADER = (
    "[skill-deploy] 승인 요청\n"
    "- skill: `calendar`\n"
    "- sha256: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`\n"
    "- deploy_nonce: `11111111111111111111111111111111`\n"
    "- review: PASS\n"
    "- sandbox: PASS (peer 인스턴스, DUMMY 시크릿)\n"
    "- 승인 방법: 이 메시지에 cha가 ✅ 리액션 (소유자 전용 — 봇/타인 리액션은 거부됨)"
)
PUBLISH_V1 = (
    "[skill-publish] 발행 승인 요청\n"
    "- skill: `managed-x`\n"
    "- sha256: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`\n"
    "- manifest_sha256: `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`\n"
    "- tag: `managed-x/v1`\n"
    "- publish_nonce: `11111111111111111111111111111111`\n"
    "- 승인 방법: 이 메시지에 cha가 ✅ 리액션 (소유자 전용 — 봇/타인 리액션은 거부됨)"
)


DEPLOY_V2 = (
    "[skill-deploy] calendar 배포 승인 요청\n"
    "- skill: `calendar`\n"
    "- sha256: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`\n"
    "- deploy_nonce: `11111111111111111111111111111111`\n"
    "대상: calendar 배포 승인 (skill-deploy:calendar)\n"
    "사실: - review: PASS - sandbox: PASS (peer 인스턴스, DUMMY 시크릿) (승인 요청; 만료: 기한 없음)\n"
    "위치: 이 메시지\n"
    "인계: 소유자: 위 위치 · 반응 ✅ 승인 또는 ⛔ 취소 (소유자 전용); 다음: 승인된 스킬만 배포\n"
    "되돌리기: 해당 없음; 취소 시: 스킬 배포 안 함"
)
PUBLISH_V2 = (
    "[skill-publish] 발행 승인 요청\n"
    "- skill: `managed-x`\n"
    "- sha256: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`\n"
    "- manifest_sha256: `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`\n"
    "- tag: `managed-x/v1`\n"
    "- publish_nonce: `11111111111111111111111111111111`\n"
    "대상: managed-x 발행 승인 (skill-publish:managed-x)\n"
    "사실: tag: `managed-x/v1` (승인 요청; 만료: 기한 없음)\n"
    "위치: 이 메시지\n"
    "인계: 소유자: 위 위치 · 반응 ✅ 승인 또는 ⛔ 취소 (소유자 전용); 다음: 승인된 스킬만 발행\n"
    "되돌리기: 해당 없음; 취소 시: 스킬 발행 안 함"
)
MANAGED_V2 = (
    "[skill-deploy] managed-x 배포 승인 요청\n"
    "- skill: `managed-x`\n"
    "- sha256: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`\n"
    "- deploy_nonce: `11111111111111111111111111111111`\n"
    "대상: managed-x 배포 승인 (skill-deploy:managed-x)\n"
    "사실: - review: PASS - sandbox: PASS (peer 인스턴스, DUMMY 시크릿) (승인 요청; 만료: 기한 없음)\n"
    "위치: 이 메시지\n"
    "인계: 소유자: 위 위치 · 반응 ✅ 승인 또는 ⛔ 취소 (소유자 전용); 다음: 승인된 스킬만 배포\n"
    "되돌리기: 해당 없음; 취소 시: 스킬 배포 안 함"
)
ENVELOPES = {
    "deploy": owner_message.OwnerMessage(
        "skill-deploy:calendar", "calendar 배포 승인",
        "- review: PASS - sandbox: PASS (peer 인스턴스, DUMMY 시크릿)",
        owner_message.Ref("self"),
        owner_message.Action("react", owner_message.Ref("self"), "✅ 승인 또는 ⛔ 취소 (소유자 전용)"),
        "승인된 스킬만 배포", "not_applicable", owner_message.Approval(None, "스킬 배포 안 함")),
    "publish": owner_message.OwnerMessage(
        "skill-publish:managed-x", "managed-x 발행 승인", "tag: `managed-x/v1`",
        owner_message.Ref("self"),
        owner_message.Action("react", owner_message.Ref("self"), "✅ 승인 또는 ⛔ 취소 (소유자 전용)"),
        "승인된 스킬만 발행", "not_applicable", owner_message.Approval(None, "스킬 발행 안 함")),
    "managed": owner_message.OwnerMessage(
        "skill-deploy:managed-x", "managed-x 배포 승인",
        "- review: PASS - sandbox: PASS (peer 인스턴스, DUMMY 시크릿)",
        owner_message.Ref("self"),
        owner_message.Action("react", owner_message.Ref("self"), "✅ 승인 또는 ⛔ 취소 (소유자 전용)"),
        "승인된 스킬만 배포", "not_applicable", owner_message.Approval(None, "스킬 배포 안 함")),
}


def deploy() -> skill_gate_specs.DeploySpec:
    return skill_gate_specs.DeploySpec(
        "calendar", "a" * 64, "1" * 32, "- review: PASS",
        skill_gate_specs.Provenance("", "", ""), skill_gate._REQUEST_BINDING,
    )


def publish() -> skill_gate_specs.PublishSpec:
    return skill_gate_specs.PublishSpec(
        "managed-x", "a" * 64, "b" * 64, "managed-x/v1", "1" * 32,
        skill_gate_publish._PUBLISH_BINDING,
    )


def binding(kind: ApprovalKind) -> ApprovalBinding:
    return ApprovalBinding(kind, ApprovalSurface.SKILL_APPROVALS, "222", 1)


@pytest.mark.parametrize("kind,expected", [("deploy", DEPLOY_V1), ("publish", PUBLISH_V1)], ids=("deploy", "publish"))
def test_legacy_literal_bytes(kind: str, expected: str) -> None:
    spec = deploy() if kind == "deploy" else publish()
    assert spec.render() == expected
    record = spec.new_record("333", binding(ApprovalKind.SKILL_DEPLOY if kind == "deploy" else ApprovalKind.SKILL_PUBLISH))
    assert spec.bound(expected, record)
    assert not spec.bound(expected + " ", record)


def test_legacy_header_literal_bytes() -> None:
    spec = deploy()
    record = spec.new_record("333", binding(ApprovalKind.SKILL_DEPLOY))
    assert spec.bound(DEPLOY_OLD_HEADER, record)


def test_hash_preimages_and_nonce_independence() -> None:
    assert deploy().action_hash() == "47eeb975f2eecfe66e969697fda729d3d7478d8d34219c7ae0c16b1c5add2322"
    assert publish().action_hash() == "b96c453cd8d958e13ad9056dd1efeed63c62cc2e896af537e3d44f5150bf1e02"
    assert replace(deploy(), deploy_nonce="2" * 32).action_hash() == "47eeb975f2eecfe66e969697fda729d3d7478d8d34219c7ae0c16b1c5add2322"
    assert replace(publish(), publish_nonce="2" * 32).action_hash() == "b96c453cd8d958e13ad9056dd1efeed63c62cc2e896af537e3d44f5150bf1e02"


class Directory:
    def agent_chat(self) -> str:
        raise AssertionError("supply-chain must not move to agent chat")

    def agent_chat_thread(self, kind: ApprovalKind) -> str:
        raise AssertionError(f"unexpected agent thread: {kind}")

    def agent_chat_request_thread(self, kind: ApprovalKind, request: RequestThread) -> str:
        raise AssertionError(f"unexpected request thread: {kind} {request}")

    def owner_dm(self) -> str:
        raise AssertionError("supply-chain must stay on shared approvals")

    def skill_approvals(self) -> str:
        return "222"

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == "222"
        return ChannelFacts(0, "approvals", ())


class Discord:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.content: str = ""

    def api(self, method: str, path: str, payload: dict[str, str] | None = None) -> object:
        self.calls.append((method, path))
        assert "/threads" not in path
        if method == "POST":
            assert path == "/channels/222/messages"
            assert payload is not None
            self.content = payload["content"]
            return {"id": "333"}
        if method == "PUT":
            assert "/reactions/" in path and path.endswith("/@me")
            assert payload is None
            return None
        assert method == "GET"
        if "/reactions/" in path:
            return [{"id": "111", "bot": False}] if "✅" in unquote(path) else []
        return {"id": "333", "content": self.content}


def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> tuple[
    Discord, argparse.Namespace, Callable[[argparse.Namespace], int], Callable[[argparse.Namespace], SkillApprovalGate]
]:
    fake = Discord()
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "gate")
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "approvals.jsonl")
    monkeypatch.setattr(skill_gate, "_api", fake.api)
    monkeypatch.setattr(skill_gate, "_owner_id", lambda: "111")
    def review(*_args: object) -> str:
        return "- review: PASS"

    def nonce(_size: int) -> str:
        return "1" * 32

    def surface(skill: str) -> skill_gate_surface.SupplyChainSurface:
        return skill_gate_surface.SupplyChainSurface(skill_gate_surface.deploy_kind(skill), "111", Directory())

    monkeypatch.setattr(skill_gate, "review_status_line", review)
    monkeypatch.setattr(secrets, "token_hex", nonce)
    monkeypatch.setattr(skill_gate, "_deploy_bindings", surface)
    monkeypatch.setattr(skill_gate_publish, "_publish_bindings", lambda: skill_gate_surface.SupplyChainSurface(
        ApprovalKind.SKILL_PUBLISH, "111", Directory()))
    if kind == "publish":
        args = argparse.Namespace(skill="managed-x", hash="a" * 64, manifest_hash="b" * 64,
                                  tag="managed-x/v1", json=False)
        return fake, args, skill_gate_publish.cmd_publish_request, skill_gate_publish._publish_gate
    args = argparse.Namespace(skill="managed-x" if kind == "managed" else "calendar",
                              hash="a" * 64, fresh=False, json=False, peer_attest_mode="discord")
    return fake, args, skill_gate.cmd_request, skill_gate._deploy_gate


@pytest.mark.parametrize("kind", ["deploy", "publish", "managed"])
def test_new_requests_store_version_and_probe_actual_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    fake, args, request, make_gate = install(tmp_path, monkeypatch, kind)
    messages: list[tuple[owner_message.OwnerMessage, owner_message.Ref]] = []
    render = owner_message.render

    def observe(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        messages.append((message, destination))
        return render(message, destination=destination)

    monkeypatch.setattr(owner_message, "render", observe)
    assert request(args) == 0
    expected = replace(
        ENVELOPES[kind], render_version="owner-ko-v2",
        fact=("발행 태그: `managed-x/v1`" if kind == "publish"
              else "- review: PASS\n- sandbox: PASS (peer 인스턴스, DUMMY 시크릿)"),
    )
    assert messages == [(expected, owner_message.Ref("self"))]
    gate = make_gate(args)
    assert replace(gate.spec, render_version=2).render() == {
        "deploy": DEPLOY_V2, "publish": PUBLISH_V2, "managed": MANAGED_V2,
    }[kind]
    record = gate.stored()
    assert record is not None
    assert record.get("render_version") == "3"
    assert len(fake.content) <= 1900
    assert gate.probe(gate.outstanding(gate.spec.key())[0]) is Probe.APPROVED


@pytest.mark.parametrize("kind", ["deploy", "publish", "managed"])
@pytest.mark.parametrize("version", [1, 2, 3], ids=["v1-fallback", "v2-fallback", "v3"])
def test_bound_request_never_renders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, version: int) -> None:
    fake, args, request, make_gate = install(tmp_path, monkeypatch, kind)
    with pytest.MonkeyPatch.context() as missing:
        if version == 1:
            missing.setitem(sys.modules, "automation.interop.owner_message", None)
        elif version == 2:
            missing.setattr(skill_gate_specs, "_render_v3", _v3_unavailable)
        assert request(args) == 0
    gate = make_gate(args)
    record = gate.stored()
    assert record is not None and record["render_version"] == str(version)
    before = gate.path().read_bytes()
    posted = fake.content
    calls: list[object] = []

    def observable(*args: object, **kwargs: object) -> str:
        calls.append((args, kwargs))
        raise AssertionError("bound cards must not render")

    monkeypatch.setattr(owner_message, "render", observable)
    monkeypatch.setattr(type(gate.spec), "render", observable)
    monkeypatch.setattr(type(gate.spec), "render_v1", observable)
    monkeypatch.setattr(skill_gate_specs, "_render_v2", observable)
    monkeypatch.setattr(skill_gate_specs, "_render_v3", observable)
    assert gate.probe(gate.outstanding(gate.spec.key())[0]) is Probe.APPROVED
    assert request(args) == (0 if kind == "publish" else 6)
    assert calls == []
    assert fake.content == posted
    assert gate.path().read_bytes() == before
    assert [method for method, _ in fake.calls[:3]] == ["POST", "PUT", "PUT"]
    assert all(method == "GET" for method, _ in fake.calls[3:])


@pytest.mark.parametrize("kind", ["deploy", "publish"])
def test_budget_refusal_has_no_external_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    fake, args, request, make_gate = install(tmp_path, monkeypatch, kind)
    if kind == "deploy":
        def over_budget(*_args: object) -> str:
            return "x" * 1600
        monkeypatch.setattr(skill_gate, "review_status_line", over_budget)
    else:
        args.tag = "x" * 1400
    spec = make_gate(args).spec
    assert isinstance(spec, skill_gate_specs.DeploySpec | skill_gate_specs.PublishSpec)
    assert len(spec.render_v1()) <= 1900
    assert request(args) == 6
    assert fake.calls == []
    assert not make_gate(args).path().exists()
    assert not list((tmp_path / "gate" / "posting-journal").glob("*"))
    assert not (tmp_path / "gate" / "proposals.jsonl").exists()


@pytest.mark.parametrize("kind", ["deploy", "publish"])
@pytest.mark.parametrize("failure", ["import", "render", "capability"])
def test_fallback_records_and_probes_legacy_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, failure: str) -> None:
    fake, args, request, make_gate = install(tmp_path, monkeypatch, kind)
    if failure == "import":
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    elif failure == "capability":
        monkeypatch.delattr(owner_message, "render")
    else:
        def unavailable(*_args: object, **_kwargs: object) -> str:
            raise owner_message.OwnerMessageError(detail="test")
        monkeypatch.setattr(owner_message, "render", unavailable)
    assert request(args) == 0
    assert fake.content == (DEPLOY_V1 if kind == "deploy" else PUBLISH_V1)
    gate = make_gate(args)
    record = gate.stored()
    assert record is not None
    assert record["render_version"] == "1"
    assert gate.probe(gate.outstanding(gate.spec.key())[0]) is Probe.APPROVED


@pytest.mark.parametrize("kind,content", [("deploy", DEPLOY_V1), ("deploy", DEPLOY_OLD_HEADER), ("publish", PUBLISH_V1)],
                         ids=["deploy", "deploy-old-header", "publish"])
def test_unversioned_records_probe_legacy_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, content: str) -> None:
    fake, args, _, make_gate = install(tmp_path, monkeypatch, kind)
    gate = make_gate(args)
    spec = deploy() if kind == "deploy" else publish()
    record = spec.new_record("333", binding(ApprovalKind.SKILL_DEPLOY if kind == "deploy" else ApprovalKind.SKILL_PUBLISH))
    assert "render_version" not in record
    gate.path().parent.mkdir(parents=True)
    _ = gate.path().write_text(json.dumps(record), encoding="utf-8")
    fake.content = content
    assert gate.probe(gate.outstanding(gate.spec.key())[0]) is Probe.APPROVED


@pytest.mark.parametrize("kind", ["deploy", "publish"])
@pytest.mark.parametrize("damage", ["version", "text", "wire", "digest", "nonce", "content-digest", "missing-content-digest"])
@pytest.mark.parametrize("version", [1, 2, 3], ids=["v1-fallback", "v2-fallback", "v3"])
def test_unknown_version_or_binding_damage_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, damage: str, version: int,
) -> None:
    fake, args, request, make_gate = install(tmp_path, monkeypatch, kind)
    with pytest.MonkeyPatch.context() as missing:
        if version == 1:
            missing.setitem(sys.modules, "automation.interop.owner_message", None)
        elif version == 2:
            missing.setattr(skill_gate_specs, "_render_v3", _v3_unavailable)
        assert request(args) == 0
    gate = make_gate(args)
    record = gate.stored()
    assert record is not None
    if damage == "version":
        record["render_version"] = "999"
    elif damage == "text":
        fake.content += " "
    elif damage == "wire":
        fake.content = fake.content.replace("a" * 64, "b" * 64, 1)
    elif damage == "digest":
        record["hash"] = "b" * 64
    elif damage == "content-digest":
        record["content_sha256"] = "0" * 64
    elif damage == "missing-content-digest":
        del record["content_sha256"]
    else:
        record["deploy_nonce" if kind == "deploy" else "publish_nonce"] = "2" * 32
    _ = gate.path().write_text(json.dumps(record), encoding="utf-8")
    assert gate.probe(gate.outstanding(gate.spec.key())[0]) is Probe.BINDING_MISMATCH
    before = gate.path().read_bytes()
    args.hash = "c" * 64
    assert request(args) == 6
    assert gate.path().read_bytes() == before
    assert [method for method, _ in fake.calls[:3]] == ["POST", "PUT", "PUT"]
    assert all(method == "GET" for method, _ in fake.calls[3:])


SIGNED_V2 = (
    "[skill-deploy] calendar 배포 승인 요청\n"
    "- skill: `calendar`\n"
    "- sha256: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`\n"
    "- deploy_nonce: `11111111111111111111111111111111`\n"
    "대상: calendar 배포 승인 (skill-deploy:calendar)\n"
    "사실: - review: PASS - sandbox: PASS (peer 인스턴스, DUMMY 시크릿) "
    "- peer verdict: PENDING (awaiting signed peer record) (승인 요청; 만료: 기한 없음)\n"
    "위치: 이 메시지\n"
    "인계: 소유자: 위 위치 · 반응 ✅ 승인 또는 ⛔ 취소 (소유자 전용); 다음: 승인된 스킬만 배포\n"
    "되돌리기: 해당 없음; 취소 시: 스킬 배포 안 함"
)


@pytest.mark.parametrize("version", [1, 2, 3], ids=["v1-fallback", "v2-fallback", "v3"])
def test_signed_check_never_renders_or_patches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int) -> None:
    fake, args, request, make_gate = install(tmp_path, monkeypatch, "deploy")
    args.peer_attest_mode = "signed"
    with pytest.MonkeyPatch.context() as missing:
        if version == 1:
            missing.setitem(sys.modules, "automation.interop.owner_message", None)
        elif version == 2:
            missing.setattr(skill_gate_specs, "_render_v3", _v3_unavailable)
        assert request(args) == 0
    gate = make_gate(args)
    record = gate.stored()
    assert record is not None and record["render_version"] == str(version)
    if version == 2:
        assert fake.content == SIGNED_V2
    posted = fake.content
    before = gate.path().read_bytes()
    args.message_id, args.deploy_nonce, args.injection_file = "333", "1" * 32, ""

    def verified(_args: argparse.Namespace, _channel: str, _mode: skill_gate.PeerAttestMode) -> skill_gate.PeerAttestationEvidence:
        # Signature verification itself is covered with real SSH signatures by test_skill_gate_signed_attestation.
        return skill_gate.PeerAttestationEvidence(fake.content, "synthetic-fingerprint")

    calls: list[object] = []

    def observable(*args: object, **kwargs: object) -> str:
        calls.append((args, kwargs))
        raise AssertionError("signed cards must bind the stored bytes")

    monkeypatch.setattr(skill_gate, "_peer_attestation_evidence", verified)
    monkeypatch.setattr(skill_gate_specs.DeploySpec, "render", observable)
    monkeypatch.setattr(skill_gate_specs.DeploySpec, "render_v1", observable)
    monkeypatch.setattr(skill_gate_specs, "_render_v2", observable)
    monkeypatch.setattr(skill_gate_specs, "_render_v3", observable)
    monkeypatch.setattr(owner_message, "render", observable)
    assert skill_gate.cmd_check(args) == 0
    assert calls == []
    assert fake.content == posted
    assert gate.path().read_bytes() == before
    assert [method for method, _ in fake.calls[:3]] == ["POST", "PUT", "PUT"]
    assert all(method == "GET" for method, _ in fake.calls[3:])
    assert (tmp_path / "approvals.jsonl").exists()


def _v3_unavailable(spec: skill_gate_specs.DeploySpec | skill_gate_specs.PublishSpec) -> str:
    raise ImportError("synthetic pre-v3 runtime")
