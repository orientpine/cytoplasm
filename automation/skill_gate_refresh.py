#!/usr/bin/env python3
"""Authorize peer re-attestation only for an unchanged current owner decision."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import Final

from automation import skill_gate

PEER_ATTESTATION_REFRESH_EXIT: Final = 7


def refresh_required(args: argparse.Namespace) -> int:
    """Return the refresh exit only when owner approval is current and peer TTL alone failed."""
    if args.injection_file or os.environ.get("E2E_TEST_MODE"):
        print("REJECTED: peer refresh requires production owner approval", file=sys.stderr)
        return 1
    mode = skill_gate._peer_attest_mode(args)
    if mode is None:
        print("REJECTED: peer_attest_mode is unset or invalid", file=sys.stderr)
        return 1
    peer_status = ""
    if mode == "signed":
        found = skill_gate._signed_peer_status(args)
        if found is None:
            print("REJECTED: signed peer trust root unavailable", file=sys.stderr)
            return 1
        peer_status = found
    owner_id = skill_gate._owner_id()
    gate = skill_gate._deploy_gate(args, peer_status=peer_status, peer_mode=mode)
    execution = skill_gate._approval_execution(gate, args)
    channel_id = execution.request.channel_id
    if not skill_gate._owner_approval_present(args, owner_id, channel_id):
        return 1
    if not gate.valid_approval(execution, skill_gate.APPROVAL_LOG):
        print("REJECTED: owner approval binding invalid", file=sys.stderr)
        return 1
    if skill_gate._peer_attestation_present(args, channel_id):
        print("REJECTED: peer attestation refresh not required", file=sys.stderr)
        return 1
    print("PEER-ATTESTATION-REFRESH-REQUIRED", file=sys.stderr)
    return PEER_ATTESTATION_REFRESH_EXIT


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--skill", required=True)
    _ = parser.add_argument("--hash", required=True)
    _ = parser.add_argument("--message-id", required=True)
    _ = parser.add_argument("--deploy-nonce", required=True)
    _ = parser.add_argument("--provenance-file", default="")
    _ = parser.add_argument("--injection-file", default="")
    _ = parser.add_argument("--peer-attest-mode", choices=("discord", "signed"), default="")
    _ = parser.add_argument("--peer-attest-public-key", default="")
    _ = parser.add_argument("--peer-attestation-stdin", action="store_true")
    return refresh_required(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
