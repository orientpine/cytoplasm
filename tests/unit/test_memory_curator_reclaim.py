"""Reclaim accounting and ordering for the curator's per-tick promotion cap.

The curator promotes at most 3 entries per tick, so the tick must spend those
slots on the entries that free the most chars.  Every expected char figure here
is hand-computed from the ``§`` join accounting in ``model.MemoryFile`` — for a
file with N>=2 entries, removing one frees ``len(text) + 3`` (the entry plus one
``"\\n§\\n"`` separator); for a one-entry file it frees the whole char_count.
"""

from __future__ import annotations

import pytest

from automation.memory_curator.binding import entry_digest
from automation.memory_curator.model import MemoryEntry, MemoryFile, MemoryKind
from automation.memory_curator.reclaim import order_by_reclaim, reclaimable_chars


def _memory_file(kind: MemoryKind, *texts: str) -> MemoryFile:
    return MemoryFile(kind, tuple(MemoryEntry(text) for text in texts))


class TestReclaimableChars:
    def test_frees_whole_char_count_when_file_holds_a_single_entry(self) -> None:
        # Given: a one-entry file, so there is no separator to account for
        memory_file = _memory_file("memory", "only durable fact")
        assert memory_file.char_count == 17

        # When / Then: removing the only entry empties the file
        assert reclaimable_chars(memory_file, 0) == 17

    def test_frees_entry_plus_one_separator_when_entry_is_first(self) -> None:
        # Given: a four-entry file — 20 text chars + 3 separators of 3 chars
        memory_file = _memory_file("memory", "aaaa", "bbbbbbbb", "cc", "dddddd")
        assert memory_file.char_count == 29

        # When / Then: dropping the head frees its 4 chars and one separator
        assert reclaimable_chars(memory_file, 0) == 7

    def test_frees_entry_plus_one_separator_when_entry_is_in_the_middle(self) -> None:
        # Given: the same four-entry file
        memory_file = _memory_file("memory", "aaaa", "bbbbbbbb", "cc", "dddddd")

        # When / Then: an interior entry frees its own chars and one separator
        assert reclaimable_chars(memory_file, 1) == 11
        assert reclaimable_chars(memory_file, 2) == 5

    def test_frees_entry_plus_one_separator_when_entry_is_last(self) -> None:
        # Given: the same four-entry file
        memory_file = _memory_file("memory", "aaaa", "bbbbbbbb", "cc", "dddddd")

        # When / Then: the tail also frees exactly one separator, never two
        assert reclaimable_chars(memory_file, 3) == 9

    def test_accounting_matches_the_user_kind_cap_free_formula(self) -> None:
        # Given: the USER.md kind, whose accounting must be identical
        memory_file = _memory_file("user", "role: researcher", "prefers Korean")
        assert memory_file.char_count == 33

        # When / Then: kind changes the cap, never the reclaim arithmetic
        assert reclaimable_chars(memory_file, 0) == 19
        assert reclaimable_chars(memory_file, 1) == 17

    def test_raises_when_index_is_past_the_last_entry(self) -> None:
        # Given: a three-entry file
        memory_file = _memory_file("memory", "aaaa", "bbbb", "cccc")

        # When / Then: an index at or past the end is refused, not clamped
        with pytest.raises(IndexError):
            _ = reclaimable_chars(memory_file, 3)
        with pytest.raises(IndexError):
            _ = reclaimable_chars(memory_file, 99)

    def test_raises_when_index_is_negative(self) -> None:
        # Given: a three-entry file
        memory_file = _memory_file("memory", "aaaa", "bbbb", "cccc")

        # When / Then: a negative index must not silently wrap to the tail
        with pytest.raises(IndexError):
            _ = reclaimable_chars(memory_file, -1)

    def test_raises_when_file_is_empty(self) -> None:
        # Given: a file with no entries at all
        memory_file = _memory_file("memory")

        # When / Then: there is no entry 0 to reclaim
        with pytest.raises(IndexError):
            _ = reclaimable_chars(memory_file, 0)


class TestOrderByReclaim:
    def test_orders_biggest_reclaim_first_across_both_kinds(self) -> None:
        # Given: two files whose candidates are scrambled within and across kinds
        memory_file = _memory_file("memory", "m" * 5, "m" * 50, "m" * 20)
        user_file = _memory_file("user", "u" * 100, "u" * 30)
        small, big, mid = memory_file.entries
        largest, medium = user_file.entries
        candidates: dict[MemoryKind, tuple[MemoryEntry, ...]] = {
            "memory": (small, mid, big),
            "user": (medium, largest),
        }

        # When: the tick asks which candidates buy back the most space
        ordered = order_by_reclaim(
            candidates,
            {"memory": memory_file, "user": user_file},
        )

        # Then: strictly descending reclaim — 103, 53, 33, 23, 8
        assert ordered == (
            ("user", 0, largest),
            ("memory", 1, big),
            ("user", 1, medium),
            ("memory", 2, mid),
            ("memory", 0, small),
        )

    def test_caps_to_the_three_biggest_when_sliced_by_the_tick(self) -> None:
        # Given: five candidates competing for three promotion slots
        memory_file = _memory_file("memory", "m" * 5, "m" * 50, "m" * 20)
        user_file = _memory_file("user", "u" * 100, "u" * 30)
        small, big, mid = memory_file.entries
        largest, medium = user_file.entries

        # When: the caller takes the first three of the ordering
        ordered = order_by_reclaim(
            {"memory": (small, mid, big), "user": (medium, largest)},
            {"memory": memory_file, "user": user_file},
        )

        # Then: the tick spends its 3 slots on 103 + 53 + 33 chars
        assert ordered[:3] == (
            ("user", 0, largest),
            ("memory", 1, big),
            ("user", 1, medium),
        )

    def test_breaks_ties_by_kind_then_entry_digest_ascending(self) -> None:
        # Given: three candidates that each free exactly 24 chars
        alpha = MemoryEntry("alpha-fact-0000000000")
        bravo = MemoryEntry("bravo-fact-0000000000")
        charlie = MemoryEntry("c" * 24)
        memory_file = MemoryFile("memory", (alpha, bravo))
        user_file = MemoryFile("user", (charlie,))
        assert reclaimable_chars(memory_file, 0) == 24
        assert reclaimable_chars(memory_file, 1) == 24
        assert reclaimable_chars(user_file, 0) == 24

        alpha_digest = entry_digest("memory", alpha.text)
        bravo_digest = entry_digest("memory", bravo.text)
        assert alpha_digest != bravo_digest
        first, second = (alpha, bravo) if alpha_digest < bravo_digest else (bravo, alpha)
        first_index = 0 if first is alpha else 1

        # When: the pair is fed in the exact reverse of the digest ordering, so a
        # sort that ignored the digest would preserve the input order and fail
        ordered = order_by_reclaim(
            {"memory": (second, first), "user": (charlie,)},
            {"memory": memory_file, "user": user_file},
        )

        # Then: "memory" precedes "user", and the memory pair sorts by digest
        assert ordered == (
            ("memory", first_index, first),
            ("memory", 1 - first_index, second),
            ("user", 0, charlie),
        )

    def test_resolves_duplicate_text_to_the_first_unused_index(self) -> None:
        # Given: the same text stored twice, with a distinct entry between them
        duplicate = MemoryEntry("duplicated fact")
        other = MemoryEntry("other")
        memory_file = MemoryFile("memory", (duplicate, other, duplicate))

        # When: both duplicates plus the distinct entry are candidates
        ordered = order_by_reclaim(
            {"memory": (duplicate, other, duplicate)},
            {"memory": memory_file},
        )

        # Then: each duplicate claims its own index (0 then 2), never index 0 twice
        assert ordered == (
            ("memory", 0, duplicate),
            ("memory", 2, duplicate),
            ("memory", 1, other),
        )

    def test_returns_empty_when_no_candidates_were_flagged(self) -> None:
        # Given: files that hold entries but nothing flagged for promotion
        memory_file = _memory_file("memory", "aaaa", "bbbb")

        # When / Then: an empty candidate map yields an empty ordering
        assert order_by_reclaim({}, {"memory": memory_file}) == ()
        assert order_by_reclaim({"memory": ()}, {"memory": memory_file}) == ()

    def test_raises_when_a_candidate_is_absent_from_its_file(self) -> None:
        # Given: a candidate whose text no longer exists in the file
        memory_file = _memory_file("memory", "aaaa", "bbbb")
        stale = MemoryEntry("never written")

        # When / Then: fail closed rather than silently dropping the candidate
        with pytest.raises(ValueError, match="not present"):
            _ = order_by_reclaim({"memory": (stale,)}, {"memory": memory_file})

    def test_raises_when_a_duplicate_candidate_outnumbers_its_file_copies(self) -> None:
        # Given: one stored copy of a text but two candidates claiming it
        duplicate = MemoryEntry("duplicated fact")
        memory_file = MemoryFile("memory", (duplicate, MemoryEntry("other")))

        # When / Then: the second claim has no unused index left to bind to
        with pytest.raises(ValueError, match="not present"):
            _ = order_by_reclaim({"memory": (duplicate, duplicate)}, {"memory": memory_file})
