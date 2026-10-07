"""The proposal engine must let the account's Hermes config pick the model.

2026-09-29: the engine asked Hermes for a model the subscription no longer served, so
every engine call fell through to the fallback and a writer call ran past the 600 s
limit. The owner then decided that one place — ``~/.hermes/config.yaml`` — picks every
model. The engine therefore passes neither ``-m`` nor ``--provider``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

from skills.proposal.engine.agents import hermes_client


def test_engine_calls_name_no_model_or_provider() -> None:
    seen: list[Sequence[str]] = []

    def runner(argv: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="answer", stderr="")

    client = hermes_client.hermes_client_from_env()
    client = hermes_client.HermesLLMClient(client.provider, client.model, runner=runner)

    assert client.complete("writer_sec0", "prompt") == "answer"
    assert not {"-m", "--model", "--provider"} & set(seen[0])
