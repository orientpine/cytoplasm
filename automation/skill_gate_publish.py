"""Owner-only publish approval action for the managed-skill channel (MS-P2).

Single-gate reuse (SI-3): declares the kind ``SKILL_PUBLISH``, which SI-6 pins to
the same supply-chain surface the deploy gate uses, appends to the SAME
APPROVAL_LOG, and rides the deploy gate's Discord/owner machinery imported from
``automation.skill_gate``. This module defines NO watcher, render, or resolve of
its own — the shared directory and the existing gate surface own those. Peer
attestation is NOT required here: the deploy approval already attested the
artifact; publish binds the owner to the release metadata (manifest digest + tag).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from typing import Final
from urllib.error import HTTPError
from urllib.parse import quote

from automation import (
    skill_gate,
    skill_gate_approval,
    skill_gate_request,
    skill_gate_specs,
    skill_gate_surface,
)
from automation.interop.approval_surface import ApprovalKind
from automation.skill_gate_e2e import GateBindings, check_injected

_PUBLISH_BINDING: Final = re.compile(
    r"\A\[skill-publish\] 발행 승인 요청\n- skill: `(?P<skill>[a-z0-9][a-z0-9-]{1,40})`\n"
    r"- sha256: `(?P<digest>[0-9a-f]{64})`\n- manifest_sha256: `(?P<manifest>[0-9a-f]{64})`\n"
    r"- tag: `(?P<tag>[A-Za-z0-9][A-Za-z0-9._/-]{0,80})`\n- publish_nonce: `(?P<nonce>[0-9a-f]{32})`\n"
)


def _publish_approval_text(skill: str, digest: str, message_id: str) -> str:
    return f"PUBLISH skill:{skill} sha256:{digest} msg:{message_id}"


def _log_publish_approval(args: argparse.Namespace, method: str) -> None:
    payload = {
        "action": "skill.publish",
        "approval": {"channel": "approvals", "message_id": args.message_id, "method": method},
        "payload": {"manifest_sha256": args.manifest_hash, "skill_sha256": args.hash, "tag": args.tag},
        "target_id": f"skill:{args.skill}",
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    record = {
        "action": "skill.publish",
        "approval": payload["approval"],
        "hash": f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}",
        "payload": payload["payload"],
        "result": {"status": "approved"},
        "target_id": f"skill:{args.skill}",
        "timestamp": skill_gate._utc_now(),
    }
    skill_gate._append_jsonl(skill_gate.APPROVAL_LOG, record)


def _publish_bindings() -> skill_gate_surface.SupplyChainSurface:
    """Declare the publish kind; the shared directory alone answers where it lands."""
    return skill_gate_surface.surface_for(ApprovalKind.SKILL_PUBLISH, skill_gate._identity())


def _publish_gate(args: argparse.Namespace) -> skill_gate_approval.SkillApprovalGate:
    """This run's publish gate: a fresh nonce, plus one action hash over the release metadata."""
    spec = skill_gate_specs.PublishSpec(
        skill=args.skill,
        digest=args.hash,
        manifest_hash=args.manifest_hash,
        tag=args.tag,
        publish_nonce=secrets.token_hex(16),
        binding=_PUBLISH_BINDING,
    )
    surface = skill_gate_approval.GateSurface(
        skill_gate._api,
        skill_gate.GATE_DIR,
        skill_gate._owner_id,
        _publish_bindings,
    )
    return skill_gate_approval.SkillApprovalGate(surface, spec)


def cmd_publish_request(args: argparse.Namespace) -> int:
    """One live publish request per skill — the same lifecycle the deploy gate rides."""
    json_output = bool(getattr(args, "json", False))
    gate = _publish_gate(args)
    reused = skill_gate_request.reuse(gate)
    if reused is not None:
        return skill_gate_request.emit(reused, json_output=json_output)
    return skill_gate_request.emit(
        skill_gate_request.post_request(gate, fresh=False), json_output=json_output
    )


def _binding_matches(args: argparse.Namespace, channel_id: str) -> bool:
    message = skill_gate._api("GET", f"/channels/{channel_id}/messages/{args.message_id}")
    content = message.get("content") if isinstance(message, dict) else None
    matched = _PUBLISH_BINDING.match(content) if isinstance(content, str) else None
    if matched is None:
        return False
    bound = (
        matched.group("skill"),
        matched.group("digest"),
        matched.group("manifest"),
        matched.group("tag"),
        matched.group("nonce"),
    )
    return bound == (args.skill, args.hash, args.manifest_hash, args.tag, args.publish_nonce)


def _check_publish_reaction(args: argparse.Namespace, owner_id: str, channel_id: str) -> int:
    try:
        users = skill_gate._api(
            "GET",
            f"/channels/{channel_id}/messages/{args.message_id}/reactions/{quote(skill_gate.APPROVE_EMOJI)}?limit=100",
        )
    except HTTPError as error:
        if error.code != 404:
            raise
        users = []
    for user in users:
        user_id, is_bot = str(user.get("id", "")), bool(user.get("bot", False))
        if user_id == owner_id and not is_bot:
            _log_publish_approval(args, "manual_reaction")
            print(f"APPROVED method=manual_reaction owner={skill_gate._mask(owner_id)}")
            return 0
        print(f"IGNORED non-owner reaction: user={skill_gate._mask(user_id)} bot={is_bot}", file=sys.stderr)
    print(
        f"REJECTED: no owner {skill_gate.APPROVE_EMOJI} reaction on message {skill_gate._mask(args.message_id)}",
        file=sys.stderr,
    )
    return 1


def cmd_publish_check(args: argparse.Namespace) -> int:
    owner_id = skill_gate._owner_id()
    channel_id = _publish_gate(args).channel_id()
    if not _binding_matches(args, channel_id):
        print(
            "REJECTED: publish approval message missing or not bound to skill/sha256/manifest_sha256/tag/publish_nonce",
            file=sys.stderr,
        )
        return 1
    if args.injection_file:
        if os.environ.get("E2E_TEST_MODE") != "1":
            print("REJECTED: injected approval requires E2E_TEST_MODE=1", file=sys.stderr)
            return 1
        bindings = GateBindings(
            owner_id,
            channel_id,
            _publish_approval_text,
            lambda _skill, _digest, _message_id, method: _log_publish_approval(args, method),
            skill_gate._mask,
        )
        return check_injected(args, bindings)
    if os.environ.get("E2E_TEST_MODE"):
        print("FATAL: E2E_TEST_MODE set but no --injection-file; refusing ambiguous mode", file=sys.stderr)
        return 2
    return _check_publish_reaction(args, owner_id, channel_id)
