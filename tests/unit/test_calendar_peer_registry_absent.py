"""분류 레지스트리 부재는 정상 설치 상태다 — 단독 일정을 막지 않는다.

`~/.hermes/interop/peers.yaml` 은 report-hub·calendar 가 표시·라우팅 분류에만 쓰는
**선택** 파일이고, 「분류가 필요한 설치에만」 생성된다
(docs/guide/discord-server-architecture.md §2.2, §5.1-6). 그러므로 파일이 없는 노드에서
`draft-create` 는 피어 분류 없이 단독 일정으로 진행해야 한다(fail-soft). 반대로 파일이
**있는데** 읽히지 않거나 깨졌으면 분류가 조용히 틀리는 것이므로 그대로 막는다(fail-closed).

`/etc/autophagy/peers.yaml` 은 스키마가 다른 attestation trust root 이므로 폴백 대상이
아니다 — 부재 시의 대체 경로는 존재하지 않는다.
"""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "calendar" / "scripts"))

calendar_routing = import_module("calendar_routing")

REGISTRY_PAYLOAD = "\n".join(
    (
        "version: 1",
        "peers:",
        "  agent-cha:",
        '    bot_user_id: "111111111111111111"',
        "    bot_name: Owner-Agent",
        "  peer-test:",
        '    bot_user_id: "222222222222222222"',
        "    bot_name: Test-Peer",
        "",
    )
)


def test_absent_registry_routes_solo_request_without_peers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the optional classification registry was never generated on this node.
    missing = tmp_path / "interop" / "peers.yaml"
    monkeypatch.setenv("CALENDAR_PEERS_CONFIG", str(missing))

    # When: a solo request (no peer named) is classified.
    peer_ids = calendar_routing.named_peer_ids("내일 오후 3시 미팅")

    # Then: no peer is detected and the skip is announced once on stderr.
    assert peer_ids == ()
    captured = capsys.readouterr()
    assert "PEER-REGISTRY-ABSENT" in captured.err
    assert str(missing) in captured.err


def test_present_but_malformed_registry_still_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the registry EXISTS but cannot be parsed — classification would be wrong.
    broken = tmp_path / "peers.yaml"
    _ = broken.write_text("not: [valid", encoding="utf-8")
    monkeypatch.setenv("CALENDAR_PEERS_CONFIG", str(broken))

    # When / Then: the request is refused instead of silently losing the peer.
    with pytest.raises(calendar_routing.PeerRegistryError):
        _ = calendar_routing.named_peer_ids("내일 오후 3시 미팅")


def test_named_peer_is_still_detected_with_a_real_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an installation that DID generate the classification registry.
    registry = tmp_path / "peers.yaml"
    _ = registry.write_text(REGISTRY_PAYLOAD, encoding="utf-8")
    monkeypatch.setenv("CALENDAR_PEERS_CONFIG", str(registry))

    # When: the free text names a registered peer.
    peer_ids = calendar_routing.named_peer_ids("peer-test랑 다음주 수요일 미팅")

    # Then: detection is unchanged and nothing is reported as absent.
    assert peer_ids == ("peer-test",)
    assert "PEER-REGISTRY-ABSENT" not in capsys.readouterr().err
