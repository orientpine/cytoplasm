"""소유자 편집 수집의 단일 사이클.

평가 루트에서는 reference/만 쓰고 manifest.jsonl은 읽기만 한다. 러너의 hyp/와
plaud/drive_watch의 원장 쓰기에 겹치는 파일이 없어 별도 자원 lock은 필요 없다.
래퍼의 단일 인스턴스 lease만으로 겹친 수집 틱을 배제한다. Discord 접근은 없다.
"""
from __future__ import annotations

from collections.abc import Mapping
from itertools import chain
from pathlib import Path

from automation.plaud_sync.audio_manifest import manifest_path, outside_checkout
from automation.stt_eval import snapshot

from automation.stt_eval.cron import capture_state as state
from automation.stt_eval.cron.capture_sources import ReadDrive, default_drive, lifelog_documents, meeting_documents
from automation.stt_eval.cron.capture_text import build_record, parse


def run_once(env: Mapping[str, str], *, drive: ReadDrive | None = None, verbose: bool = False) -> int:
    path = manifest_path(env)
    root = path.parent
    home = Path(env.get("HOME") or Path.home())
    manifest = state.bindings(path)
    if drive is None and env.get("DRIVE_PUBLISH_ENABLED") == "1":
        drive = default_drive()
    meetings = meeting_documents(home, env, drive) if drive is not None else ()
    new = updated = skipped = 0
    for document in chain(lifelog_documents(home), meetings):
        binding = manifest.get((document.surface, document.stem))
        if document.frozen is None or binding is None:
            skipped += 1
            continue
        current = parse(document.current, document.surface)
        frozen = parse(document.frozen, document.surface)
        target = outside_checkout(root / "reference" / f"{binding.digest}.json")
        previous = state.previous(root, binding.digest)
        if (previous == state.fingerprint(current) and target.exists()) or (previous is None and current == frozen):
            continue
        existed = target.exists()
        record = build_record(current, digest=binding.digest, duration_ms=binding.duration_ms,
                              provenance=f"owner-edit:{document.surface}:{document.stamp}")
        if snapshot.write_reference(record, env) is None:
            raise RuntimeError("capture reference write failed")
        state.mark(root, binding.digest, current)
        updated += int(existed)
        new += int(not existed)
    if new or updated or verbose:
        print(f"STT-EVAL-CAPTURE new={new} updated={updated} skipped={skipped}")
    return 0
