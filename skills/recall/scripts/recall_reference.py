"""소유자가 Drive 에 모아 둔 참고자료에서 근거 구절을 찾는 recall 보조 CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

OK: Final = "ok"


def _search(query: str, limit: int) -> Any:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import recall_runtime  # noqa: PLC0415

    sys.path.insert(0, str(recall_runtime.runtime_root()))
    from automation import drive_reference  # noqa: PLC0415

    return drive_reference.search(query, limit=limit)


def _hit_lines(hit: Any) -> list[str]:
    return [
        f"- {hit.name} · {hit.path}",
        f"  {hit.link}",
        f"  {hit.snippet if hit.status == OK else hit.status}",
    ]


def render(result: Any) -> str:
    if result.status != OK:
        return f"참고자료를 쓰지 못했습니다({result.status}) — {' / '.join(result.notes)}"
    lines = [f"참고자료 `{result.root}` · 문서 {result.scanned}건 훑음"]
    if not result.hits:
        lines.append("근거가 될 만한 문서를 찾지 못했습니다.")
    for hit in result.hits:
        lines += _hit_lines(hit)
    lines += [f"({note})" for note in result.notes]
    return "\n".join(lines)


def payload(result: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "root": result.root,
        "scanned": result.scanned,
        "notes": list(result.notes),
        "hits": [
            {
                "name": hit.name,
                "path": hit.path,
                "link": hit.link,
                "snippet": hit.snippet,
                "score": hit.score,
                "status": hit.status,
            }
            for hit in result.hits
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recall_reference", description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=3, help="본문까지 읽어 볼 문서 수")
    parser.add_argument("--json", action="store_true", help="reference-v1 JSON 출력")
    args = parser.parse_args(argv)
    if not args.query.strip():
        parser.error("query must not be empty")
    result = _search(args.query, args.limit)
    print(json.dumps(payload(result), ensure_ascii=False, indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
