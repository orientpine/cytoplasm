from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import cast

from ..agents.llm import MockLLMClient, load_mock_responses
from .orchestrator import DEFAULT_LLM_FIXTURE, run


def run_demo(out_path: str | None = None) -> str:
    corpus_dir = os.path.join(os.path.dirname(__file__), "../resource/gold")
    output_path = out_path or "/tmp/demo.hwpx"
    llm = MockLLMClient(load_mock_responses(str(DEFAULT_LLM_FIXTURE)))

    result = run(corpus_dir=corpus_dir, out_path=output_path, llm=llm)
    for node in result.node_log:
        print(json.dumps(node.model_dump(mode="json"), ensure_ascii=False, sort_keys=True), flush=True)
    return result.artifact_path


def assert_deterministic(n: int = 3) -> None:
    temp_dir = Path(tempfile.mkdtemp())
    hashes: list[str] = []
    for index in range(n):
        output_path = temp_dir / f"demo-{index}.hwpx"
        artifact_path = run_demo(str(output_path))
        hashes.append(_sha256(Path(artifact_path)))

    if len(set(hashes)) != 1:
        raise AssertionError(f"Demo output is not deterministic: {hashes}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_out(argv: list[str]) -> str:
    parser = argparse.ArgumentParser(description="Run the offline KIMM docbot demo harness.")
    _ = parser.add_argument("out_positional", nargs="?")
    _ = parser.add_argument("--out", dest="out_flag")
    args = parser.parse_args(argv[1:])
    out_flag = cast("str | None", args.out_flag)
    out_positional = cast("str | None", args.out_positional)
    return out_flag or out_positional or "/tmp/demo.hwpx"


if __name__ == "__main__":
    import sys

    _ = run_demo(_parse_out(sys.argv) if len(sys.argv) > 1 else "/tmp/demo.hwpx")


__all__ = ["assert_deterministic", "run_demo"]
