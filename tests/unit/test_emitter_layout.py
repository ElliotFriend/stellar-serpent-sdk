"""Tests for ``serpent.emitter.layout`` -- the literal pool + scratch layout.

``layout.py`` is a by-copy port of ``spikes/spike1/emitter.py:317-353``
(``_Memory``; R5, P12), extended with ``seed`` for D's larger inventory (E7).
The tests below cover, per the Task 3 brief:

* interning: dedupe (equal bytes stored once) and alignment padding;
* scratch: 8-byte rounding and monotonicity (never reused);
* both guards (pool, scratch), RED-proven against a deliberately weakened
  ``check()`` before being confirmed against the real one;
* ``seed`` determinism: two seeds of equal (not identical-object) inventories
  produce byte-identical ``pool_bytes()``;
* the Phase 0 ``Settings`` descriptor layout -- a SELF-SNAPSHOT (goldens
  README's third provenance class): the expected bytes are built in this
  test, by hand, from the same ``<II`` rule ``seed`` uses, not read back off
  a stored artifact or an on-chain/Rust-SDK comparison;
* memoryless: an unseeded, un-interned ``Memory`` reports ``is_empty``.
"""

import struct

import pytest

from serpent.compiler.frontend import LiteralInventory
from serpent.emitter.frame import BuildLimitError
from serpent.emitter.layout import Memory

# ===========================================================================
# Interning: dedupe + alignment padding
# ===========================================================================


def test_intern_dedupes_equal_bytes() -> None:
    mem = Memory()
    first = mem.intern(b"hello")
    second = mem.intern(b"hello")
    assert first == second == 0
    # Stored once: the pool is exactly the one copy, not two.
    assert mem.pool_bytes() == b"hello"


def test_intern_distinct_blobs_get_distinct_offsets() -> None:
    mem = Memory()
    off_a = mem.intern(b"aaa")
    off_b = mem.intern(b"bb")
    assert off_a == 0
    assert off_b == 3
    assert mem.pool_bytes() == b"aaabb"


def test_intern_pads_to_alignment_before_a_new_blob() -> None:
    mem = Memory()
    mem.intern(b"abc")  # 3 bytes, offset 0
    off = mem.intern(b"xyz", align=8)
    # 3 bytes already in the pool; padded up to the next multiple of 8.
    assert off == 8
    assert mem.pool_bytes() == b"abc" + b"\x00" * 5 + b"xyz"


def test_intern_repeat_hit_never_pads() -> None:
    """A cache hit returns the existing offset; ``align`` is not re-applied."""
    mem = Memory()
    mem.intern(b"abc")
    first_len = len(mem.pool_bytes())
    off = mem.intern(b"abc", align=8)
    assert off == 0
    assert len(mem.pool_bytes()) == first_len


# ===========================================================================
# Scratch: 8-byte rounding + monotonicity
# ===========================================================================


def test_scratch_starts_at_scratch_base() -> None:
    mem = Memory()
    assert mem.scratch(1) == Memory.SCRATCH_BASE


def test_scratch_rounds_up_to_8_bytes() -> None:
    mem = Memory()
    first = mem.scratch(1)
    second = mem.scratch(1)
    # 1 byte rounds up to 8, so the second reservation starts 8 on.
    assert second == first + 8


def test_scratch_exact_multiple_of_8_does_not_overpad() -> None:
    mem = Memory()
    first = mem.scratch(8)
    second = mem.scratch(1)
    assert second == first + 8


def test_scratch_never_reuses_an_offset() -> None:
    """Monotonic bump: repeated requests, even of the same size, never overlap."""
    mem = Memory()
    offsets = [mem.scratch(4) for _ in range(5)]
    assert offsets == sorted(offsets)
    assert len(set(offsets)) == len(offsets)
    for i in range(1, len(offsets)):
        assert offsets[i] - offsets[i - 1] == 8  # each 4B request rounds up to 8B


# ===========================================================================
# The two guards -- RED-proven (see report for the break/restore evidence)
# ===========================================================================


def test_check_raises_pool_limit_once_pool_exceeds_scratch_base() -> None:
    mem = Memory()
    mem.intern(b"\x00" * (Memory.SCRATCH_BASE + 1))
    with pytest.raises(BuildLimitError, match="literal pool") as exc_info:
        mem.check()
    assert exc_info.value.limit == "pool"


def test_check_allows_pool_exactly_at_scratch_base() -> None:
    """The boundary itself is not a violation: the pool may fill up to (not past) it."""
    mem = Memory()
    mem.intern(b"\x00" * Memory.SCRATCH_BASE)
    mem.check()  # must not raise


def test_check_raises_scratch_limit_once_scratch_exceeds_one_page() -> None:
    mem = Memory()
    mem.scratch(Memory.PAGE - Memory.SCRATCH_BASE + 1)
    with pytest.raises(BuildLimitError, match="scratch") as exc_info:
        mem.check()
    assert exc_info.value.limit == "scratch"


def test_check_allows_scratch_exactly_filling_one_page() -> None:
    mem = Memory()
    mem.scratch(Memory.PAGE - Memory.SCRATCH_BASE)
    mem.check()  # must not raise


def test_check_passes_on_a_fresh_memory() -> None:
    Memory().check()  # must not raise


# ===========================================================================
# seed(): determinism (E7)
# ===========================================================================


def _sample_inventory() -> LiteralInventory:
    return LiteralInventory(
        symbols_over_9=("counter_value", "some_symbol"),
        strings=("hello", "world"),
        bytes_literals=(b"\x01\x02\x03", b"\xff"),
        struct_key_descriptor_sets=(
            ("counter_limit", "display_name"),
            ("a", "bb", "ccc"),
        ),
    )


def test_seed_is_deterministic_across_equal_but_distinct_inventories() -> None:
    inv_a = _sample_inventory()
    inv_b = _sample_inventory()
    assert inv_a is not inv_b
    assert inv_a == inv_b  # equal by value (frozen dataclass), not identity

    mem_a = Memory()
    mem_a.seed(inv_a)
    mem_b = Memory()
    mem_b.seed(inv_b)

    assert mem_a.pool_bytes() == mem_b.pool_bytes()


def test_seed_dedupes_a_name_shared_between_a_string_and_a_descriptor_key() -> None:
    """A field name equal to an already-interned literal is stored once."""
    inv = LiteralInventory(
        symbols_over_9=(),
        strings=("shared",),
        bytes_literals=(),
        struct_key_descriptor_sets=(("shared",),),
    )
    mem = Memory()
    mem.seed(inv)
    # "shared" appears in the pool exactly once, from the strings pass; the
    # descriptor pass's intern(b"shared") is a cache hit at the same offset.
    assert mem.pool_bytes().count(b"shared") == 1


def test_seed_never_re_sorts_an_adversarially_ordered_key_set() -> None:
    """A reverse-alphabetical key set is interned in the GIVEN order, never re-sorted.

    C9/P7 already sorted each key set into the byte-string order
    ``map_new_from_linear_memory`` needs at compile time; ``seed`` must trust
    that order verbatim (§C.1) -- re-sorting here would validate and then
    panic on chain (F.1.13). Every other fixture in this file happens to use
    an alphabetically-ascending set, so a stray ``sorted(key_set)`` inside
    ``seed`` would sail through the rest of the suite; this one uses
    ``("zebra", "apple")`` -- reverse order -- to catch exactly that mistake.

    Both names are the same length (5), so a re-sorted implementation would
    still produce a *numerically* identical packed ``<II>`` descriptor
    (offsets 0/5, lengths 5/5 either way) -- the assertion has to be on the
    raw pool BYTES, not just the descriptor's integers, or it would not
    actually distinguish the two orderings.
    """
    inv = LiteralInventory(
        symbols_over_9=(),
        strings=(),
        bytes_literals=(),
        struct_key_descriptor_sets=(("zebra", "apple"),),
    )
    mem = Memory()
    mem.seed(inv)

    pool = mem.pool_bytes()
    # "zebra" was given first, so its bytes must land before "apple"'s.
    assert pool.index(b"zebra") < pool.index(b"apple")

    expected_descriptor = struct.pack("<II", 0, 5) + struct.pack("<II", 5, 5)
    expected_pool = b"zebra" + b"apple" + b"\x00" * 6 + expected_descriptor
    assert pool == expected_pool


def test_seed_multi_category_inventory_exact_byte_layout() -> None:
    """The exact expected bytes across all four categories, in seed() order.

    Stronger than ``test_seed_is_deterministic_across_equal_but_distinct_
    inventories`` (which only proves two runs agree with EACH OTHER): this
    pins the actual expected layout, computed by hand, category by category
    -- symbols_over_9, then strings, then bytes_literals, then the key set's
    ``<II>`` descriptor blob -- so a category emitted out of order, or with
    the wrong encoding, fails here even if it happened to still be
    self-consistent run to run.

    Hand-computed offsets, from an empty pool:
      * ``b"abcdefghij"`` (10 bytes, the one symbol) -> offset 0.
      * ``b"hi"`` (2 bytes, the one string) -> offset 10.
      * ``b"\\x01\\x02"`` (2 bytes, the one bytes literal) -> offset 12.
      * ``b"x"`` (1 byte, the key set's first field name) -> offset 14.
      * ``b"yy"`` (2 bytes, its second field name) -> offset 15; pool is now
        17 bytes.
      * the descriptor blob ``pack("<II", 14, 1) + pack("<II", 15, 2)``
        (16 bytes), interned with ``align=8``: padded by
        ``(-17) % 8 == 7`` zero bytes up to offset 24.
    """
    inv = LiteralInventory(
        symbols_over_9=("abcdefghij",),
        strings=("hi",),
        bytes_literals=(b"\x01\x02",),
        struct_key_descriptor_sets=(("x", "yy"),),
    )
    mem = Memory()
    mem.seed(inv)

    expected_descriptor = struct.pack("<II", 14, 1) + struct.pack("<II", 15, 2)
    expected_pool = (
        b"abcdefghij"
        + b"hi"
        + b"\x01\x02"
        + b"x"
        + b"yy"
        + b"\x00" * 7  # pad from pool length 17 up to the align=8 boundary at 24
        + expected_descriptor
    )
    assert mem.pool_bytes() == expected_pool


# ===========================================================================
# The Phase 0 Settings descriptor layout -- SELF-SNAPSHOT (goldens README)
# ===========================================================================


def test_settings_descriptor_layout_self_snapshot() -> None:
    """The packed ``<II`` pairs for the Phase 0 ``Settings`` field-name set.

    SELF-SNAPSHOT (tests/goldens/README.md's third provenance class, flagged
    unlabelled in plan review m5): this is *not* compared against an on-chain
    artifact or a Rust-SDK build -- the deployed artifact's data section is
    not being read here -- it is the expected byte layout worked out by hand,
    in this test, from the same rule ``Memory.seed`` applies
    (``struct.pack("<II", intern(name), len(name))`` per field name, in the
    set's given order), for one field-name set seeded into an otherwise-empty
    pool: ``("counter_limit", "display_name")``.

    Hand-computed offsets, from an empty pool:
      * ``b"counter_limit"`` (13 bytes) interned first -> offset 0.
      * ``b"display_name"`` (12 bytes) interned second, align=1, no padding
        needed after 13 bytes -> offset 13.
      * the descriptor blob is then
        ``pack("<II", 0, 13) + pack("<II", 13, 12)`` (16 bytes), interned
        with ``align=8``: 13 + 12 = 25 bytes already in the pool, padded by
        ``(-25) % 8 == 7`` zero bytes up to offset 32.
    """
    inv = LiteralInventory(
        symbols_over_9=(),
        strings=(),
        bytes_literals=(),
        struct_key_descriptor_sets=(("counter_limit", "display_name"),),
    )
    mem = Memory()
    mem.seed(inv)

    expected_key_pairs = struct.pack("<II", 0, 13) + struct.pack("<II", 13, 12)
    expected_pool = (
        b"counter_limit"  # offset 0, 13 bytes
        + b"display_name"  # offset 13, 12 bytes
        + b"\x00" * 7  # padding up to the align=8 boundary at offset 32
        + expected_key_pairs  # offset 32, 16 bytes
    )

    assert mem.pool_bytes() == expected_pool
    # The specific requirement (byte-compare the packed <II pairs): the
    # descriptor blob itself, independent of where it landed in the pool.
    assert mem.pool_bytes()[32:] == expected_key_pairs


# ===========================================================================
# Memoryless: is_empty
# ===========================================================================


def test_fresh_memory_is_empty() -> None:
    assert Memory().is_empty is True


def test_memory_is_not_empty_after_intern() -> None:
    mem = Memory()
    mem.intern(b"x")
    assert mem.is_empty is False


def test_memory_is_not_empty_after_scratch() -> None:
    mem = Memory()
    mem.scratch(1)
    assert mem.is_empty is False
