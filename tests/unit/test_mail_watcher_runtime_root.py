"""mail_digest_watch 도 자식에게 런타임 루트를 명시 전파해야 한다 — 회귀 고정."""
from __future__ import annotations

import ast
from pathlib import Path

_WATCHERS = Path(__file__).resolve().parents[2] / "skills" / "mail" / "scripts"


def _spawn_calls_pass_repo_root(source: str) -> bool:
    """subprocess.run(...) 호출이 AUTOPHAGY_REPO_ROOT 를 담은 env= 를 넘기는가."""
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if ast.unparse(node.func) != "subprocess.run":
            continue
        env = next((kw for kw in node.keywords if kw.arg == "env"), None)
        if env is None or "AUTOPHAGY_REPO_ROOT" not in ast.unparse(env.value):
            return False
    return True


def test_both_mail_watchers_state_the_runtime_root_for_their_child() -> None:
    """규약 (b-2): 부모가 명시한다 — 자식이 추측하게 두지 않는다.

    2026-08-18 실측: triage 워처는 주입해서 rc=0, digest 워처는 주입하지 않아
    `GATE-REFUSED 승인 라이프사이클 모듈 불가 (AUTOPHAGY_REPO_ROOT=/srv/autophagy-skills/releases)`
    로 죽었다. 같은 CLI 를 같은 방식으로 부르는데 한쪽만 살아 있었다.
    """
    offenders = [
        name for name in ("mail_triage_watch.py", "mail_digest_watch.py")
        if not _spawn_calls_pass_repo_root((_WATCHERS / name).read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"자식에게 AUTOPHAGY_REPO_ROOT 를 넘기지 않는 워처: {offenders} — "
        "마운트된 스킬에서 승인 파사드 import 가 실패해 게이트가 자기 자신을 거부한다"
    )
