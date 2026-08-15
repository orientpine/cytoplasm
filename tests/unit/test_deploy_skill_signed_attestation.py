from __future__ import annotations

from pathlib import Path


_DEPLOY = Path(__file__).resolve().parents[2] / "automation" / "deploy-skill.sh"


def test_stage3_when_mode_is_signed_then_couriers_peer_stdout_to_every_gate_check() -> None:
    # Given: the production deploy script owns peer attestation and both approval checks.
    script = _DEPLOY.read_text(encoding="utf-8")

    # When / Then: peer stdout is retained and signed mode feeds that exact record over stdin.
    assert 'PEER_ATTEST_BLOB="$(peer_attest ' in script
    assert "printf '%s\\n' \"$PEER_ATTEST_BLOB\"" in script
    assert "--peer-attestation-stdin" in script
    assert '--peer-attest-mode "$NODE_PEER_ATTEST_MODE"' in script


def test_peer_attestor_when_invoked_then_receives_node_config_mode_and_fixed_key_namespace() -> None:
    # Given: the node config names the install mode and peer account.
    script = _DEPLOY.read_text(encoding="utf-8")
    start = script.index("peer_attest() {")
    body = script[start : script.index("\n}", start)]

    # When / Then: the peer CLI receives the mode while the gate derives the root-owned public key path.
    assert '--mode \\"$NODE_PEER_ATTEST_MODE\\"' in body
    assert 'PEER_ATTEST_PUBLIC_KEY="/etc/autophagy/peer-attest-${NODE_PEER_ACCOUNT}.pub"' in script


def test_managed_activation_when_reaching_stage3_then_uses_same_signed_attestation_path() -> None:
    # Given: managed activation selects its quarantine source before the shared four-stage pipeline.
    script = _DEPLOY.read_text(encoding="utf-8")
    managed_source = script.index('if [[ "$ACTIVATE_MANAGED" == 1 ]]', script.index("SRC_DIR="))
    stage3 = script.index("stage 3/4 REQUEST + PEER-ATTEST")
    courier = script.index('PEER_ATTEST_BLOB="$(peer_attest ', stage3)

    # When / Then: no managed-only bypass can skip the signed peer gate.
    assert managed_source < stage3 < courier
