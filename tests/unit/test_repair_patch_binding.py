"""Patch bytes, not patch names, are what an owner approval must bind to."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from automation.repair.repair_patch_binding import (
    PatchBindingError,
    PatchFileDelta,
    changes_from_json,
    changes_to_json,
    content_action_hash,
    load_patch_artifact,
    parse_patch_changes,
    plan_patch_path,
)

TICKET = "t_repair01"

ADDED = """diff --git a/automation/added.py b/automation/added.py
new file mode 100644
index 0000000..1c2d3e4
--- /dev/null
+++ b/automation/added.py
@@ -0,0 +1,2 @@
+alpha
+beta
"""

DELETED = """diff --git a/docs/removed.md b/docs/removed.md
deleted file mode 100644
index 1c2d3e4..0000000
--- a/docs/removed.md
+++ /dev/null
@@ -1 +0,0 @@
-gone
"""

RENAMED = """diff --git a/tests/old_name.py b/tests/new_name.py
similarity index 90%
rename from tests/old_name.py
rename to tests/new_name.py
--- a/tests/old_name.py
+++ b/tests/new_name.py
@@ -1,2 +1,2 @@
 context
-old line
+new line
"""


def _patch(tmp_path: Path, body: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "patch.diff"
    _ = target.write_text(body, encoding="utf-8")
    return target


def _one_file(inner: str) -> str:
    return (
        "diff --git a/automation/mod.py b/automation/mod.py\n"
        "--- a/automation/mod.py\n"
        "+++ b/automation/mod.py\n"
        "@@ -1,2 +1,2 @@\n"
        " context\n"
        "-old\n"
        f"+{inner}\n"
    )


def test_same_name_and_deltas_but_different_hunk_text_then_digest_and_hash_differ(tmp_path: Path) -> None:
    # Given: two patches that agree on filename, changed path, and +1/-1 counts.
    first = load_patch_artifact(_patch(tmp_path / "a", _one_file("first replacement")))
    second = load_patch_artifact(_patch(tmp_path / "b", _one_file("second replacement")))

    # When: only the hunk body differs between them.
    assert first.changes == second.changes
    assert first.path.name == second.path.name == "patch.diff"

    # Then: the bytes digest and therefore the approval binding both move.
    assert first.patch_sha256 != second.patch_sha256
    assert content_action_hash(TICKET, "patch.diff", first.patch_sha256, first.changes) != content_action_hash(
        TICKET, "patch.diff", second.patch_sha256, second.changes
    )


def test_added_deleted_and_renamed_files_when_parsed_then_headers_add_no_counts() -> None:
    # Given: one addition, one deletion, and one rename in a single patch.
    changes = parse_patch_changes((ADDED + DELETED + RENAMED).encode("utf-8"))

    # When / Then: every side is attributed and ---/+++ headers contribute nothing.
    assert changes == (
        PatchFileDelta(None, "automation/added.py", 2, 0),
        PatchFileDelta("docs/removed.md", None, 0, 1),
        PatchFileDelta("tests/old_name.py", "tests/new_name.py", 1, 1),
    )


def _git_quote(path: str) -> str:
    """Reproduce git's core.quotePath encoding for a path."""
    encoded = "".join(
        character
        if character.isascii() and character not in '"\\'
        else "".join(f"\\{byte:03o}" for byte in character.encode("utf-8"))
        for character in path
    )
    return f'"{encoded}"'


def test_git_quoted_non_ascii_paths_are_decoded_like_git_wrote_them() -> None:
    # Given: real `git diff` output. core.quotePath defaults to true, so every
    # non-ASCII path arrives C-quoted with octal escapes — and this repo is full
    # of Korean file names, so a repair touching one must not be refused.
    name = "docs/기능소개/파 일.md"
    body = (
        f"diff --git {_git_quote('a/' + name)} {_git_quote('b/' + name)}\n"
        "--- /dev/null\n"
        f"+++ {_git_quote('b/' + name)}\n"
        "@@ -0,0 +1 @@\n"
        "+내용\n"
    )

    # When: the parser reads it.
    changes = parse_patch_changes(body.encode("utf-8"))

    # Then: the owner sees the real file name, spaces and all.
    assert changes == (PatchFileDelta(None, name, 1, 0),)


def test_a_quoted_path_that_escapes_the_repository_is_still_refused() -> None:
    # Given: quoting must not become a way around the traversal check.
    quoted = _git_quote("a/../기능.md")
    body = f"diff --git {quoted} {quoted}\n--- {quoted}\n+++ {quoted}\n@@ -0,0 +1 @@\n+x\n"

    # When / Then: the decoded path is validated exactly like a plain one.
    with pytest.raises(PatchBindingError):
        _ = parse_patch_changes(body.encode("utf-8"))


def test_hunk_line_that_looks_like_a_header_when_parsed_then_counts_as_one_insertion() -> None:
    # Given: an inserted line whose own text begins with the file-header marker.
    body = (
        "diff --git a/automation/mod.py b/automation/mod.py\n"
        "--- a/automation/mod.py\n"
        "+++ b/automation/mod.py\n"
        "@@ -0,0 +1,1 @@\n"
        "++++ not a header\n"
    )

    # When: the parser walks it in hunk mode.
    changes = parse_patch_changes(body.encode("utf-8"))

    # Then: position, not appearance, decides — this is content.
    assert changes == (PatchFileDelta("automation/mod.py", "automation/mod.py", 1, 0),)


def test_content_action_hash_when_called_repeatedly_then_is_stable_and_prefixed() -> None:
    # Given: one fixed summary of an approved patch.
    changes = parse_patch_changes(ADDED.encode("utf-8"))
    digest = hashlib.sha256(ADDED.encode("utf-8")).hexdigest()

    # When: the binding is derived twice.
    first = content_action_hash(TICKET, "patch.diff", digest, changes)

    # Then: it is deterministic and syntactically distinct from a legacy bare-hex hash.
    assert first == content_action_hash(TICKET, "patch.diff", digest, changes)
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64


def test_content_action_hash_when_only_the_summary_changes_then_the_binding_moves() -> None:
    # Given: an identical byte digest presented with a tampered file summary.
    digest = hashlib.sha256(ADDED.encode("utf-8")).hexdigest()
    honest = (PatchFileDelta(None, "automation/added.py", 2, 0),)
    tampered = (PatchFileDelta(None, "automation/added.py", 1, 0),)

    # When / Then: the owner-visible representation is inside the preimage.
    assert content_action_hash(TICKET, "patch.diff", digest, honest) != content_action_hash(
        TICKET, "patch.diff", digest, tampered
    )


def test_load_patch_artifact_when_read_then_hashes_the_raw_bytes_once(tmp_path: Path) -> None:
    # Given: a patch on disk.
    path = _patch(tmp_path, ADDED)

    # When: the artifact is captured.
    artifact = load_patch_artifact(path)

    # Then: it carries the exact bytes and their digest for later re-verification.
    assert artifact.content == ADDED.encode("utf-8")
    assert artifact.patch_sha256 == hashlib.sha256(ADDED.encode("utf-8")).hexdigest()
    assert artifact.path == path


def test_changes_json_round_trip_preserves_every_field() -> None:
    # Given: a parsed summary containing an addition, a deletion, and a rename.
    changes = parse_patch_changes((ADDED + DELETED + RENAMED).encode("utf-8"))

    # When: it is persisted and read back through the store codec.
    restored = changes_from_json(changes_to_json(changes))

    # Then: nothing is lost or reordered.
    assert restored == changes


def test_plan_patch_path_resolves_the_ops_private_patch(tmp_path: Path) -> None:
    # Given / When: the conventional plan layout is asked for its patch.
    resolved = plan_patch_path(tmp_path, TICKET)

    # Then: it is the one file every planner writes and the gate later re-reads.
    assert resolved == tmp_path / TICKET / "patch.diff"


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("combined", "diff --cc automation/mod.py\n@@@ -1,1 -1,1 +1,1 @@@\n++merged\n"),
        ("binary", "diff --git a/docs/x.png b/docs/x.png\nGIT binary patch\nliteral 4\n"),
        ("traversal", "diff --git a/../escape.py b/../escape.py\n--- a/../escape.py\n+++ b/../escape.py\n@@ -0,0 +1 @@\n+x\n"),
        ("absolute", "diff --git a/etc/x b/etc/x\n--- /etc/shadow\n+++ /etc/shadow\n@@ -0,0 +1 @@\n+x\n"),
        ("hunk_without_file", "@@ -0,0 +1 @@\n+orphan\n"),
        ("empty", "\n"),
    ],
)
def test_unsafe_or_ambiguous_patches_are_refused_rather_than_summarised(name: str, body: str) -> None:
    # Given: a patch the summary cannot honestly describe.
    del name

    # When / Then: it fails closed instead of being silently under-reported.
    with pytest.raises(PatchBindingError):
        _ = parse_patch_changes(body.encode("utf-8"))


def test_non_utf8_patch_is_refused() -> None:
    # Given: a patch whose bytes are not decodable text.
    body = b"diff --git a/automation/mod.py b/automation/mod.py\n--- a/automation/mod.py\n+++ b/automation/mod.py\n@@ -0,0 +1 @@\n+\xff\xfe\n"

    # When / Then: the parser refuses rather than guessing an encoding.
    with pytest.raises(PatchBindingError):
        _ = parse_patch_changes(body)


def test_missing_patch_file_is_refused(tmp_path: Path) -> None:
    # Given / When / Then: an absent patch can never be summarised or approved.
    with pytest.raises(PatchBindingError):
        _ = load_patch_artifact(tmp_path / "nowhere" / "patch.diff")
