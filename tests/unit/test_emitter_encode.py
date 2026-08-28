"""Golden-vector and property tests for ``serpent.emitter.encode``.

encode.py is a by-copy port of ``spikes/spike1/emitter.py:101-139`` (R5): the
six framing primitives that produced the on-chain-verified 877-byte artifact
at ``spikes/spike1/spike.wasm``. The `section`/`vec`/`wasm_name`/
`custom_section` vectors below are hand-extracted from that artifact's bytes
(a one-off offline script walked the module's section table); they are
embedded as literals rather than read from the file at test time because
``spike.wasm`` may be absent from a fresh checkout (task instructions).
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from serpent import val
from serpent.emitter import encode

# --- uleb/sleb golden vectors (canonical LEB128 examples) ------------------


def test_uleb_rejects_a_negative_input() -> None:
    # A negative n never clears its sign bit under `n >>= 7`, so the encoder
    # would spin forever rather than raise -- a CI hang, worse than a crash.
    # `ValueError` (not `frame.EmitError`): `frame` imports `encode`, so the
    # reverse import would be a cycle.
    with pytest.raises(ValueError, match="uleb requires n >= 0, got -1"):
        encode.uleb(-1)


def test_uleb_golden_vectors() -> None:
    assert encode.uleb(0) == bytes([0x00])
    assert encode.uleb(1) == bytes([0x01])
    assert encode.uleb(127) == bytes([0x7F])
    assert encode.uleb(128) == bytes([0x80, 0x01])
    # The canonical DWARF/WebAssembly spec example.
    assert encode.uleb(624485) == bytes([0xE5, 0x8E, 0x26])


def test_sleb_golden_vectors() -> None:
    assert encode.sleb(-1) == bytes([0x7F])
    assert encode.sleb(-64) == bytes([0x40])
    assert encode.sleb(-65) == bytes([0xBF, 0x7F])
    assert encode.sleb(63) == bytes([0x3F])
    assert encode.sleb(64) == bytes([0xC0, 0x00])
    # A wasmtime-shaped signed i64 fed through val.as_i64, per the brief.
    assert val.as_i64(0xFFFF_FFFF_FFFF_FF07) == -249
    assert encode.sleb(val.as_i64(0xFFFF_FFFF_FFFF_FF07)) == bytes([0x87, 0x7E])


# --- Hypothesis: round-trip + minimal-length (E7) ---------------------------
#
# encode.py has no decoders (none are in its interface), so decode_uleb/
# decode_sleb below are independent, test-only LEB128 decoders written from
# the spec rather than derived from encode.uleb/encode.sleb -- round-tripping
# through them is a real check, not a tautology. The minimal-length bound is
# likewise computed independently, from bit_length() arithmetic, not by
# re-running the encoder.


def decode_uleb(b: bytes) -> tuple[int, int]:
    n = 0
    shift = 0
    i = 0
    while True:
        byte = b[i]
        i += 1
        n |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return n, i
        shift += 7


def decode_sleb(b: bytes) -> tuple[int, int]:
    n = 0
    shift = 0
    i = 0
    while True:
        byte = b[i]
        i += 1
        n |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            if byte & 0x40:
                n |= -(1 << shift)
            return n, i


def uleb_min_len(n: int) -> int:
    """Minimal ULEB128 byte count for n>=0, from bit_length() alone."""
    bits = n.bit_length()
    return max(1, -(-bits // 7))


def sleb_min_len(n: int) -> int:
    """Minimal SLEB128 byte count, from bit_length() alone.

    A signed n needs enough bits to hold its two's-complement magnitude plus
    one sign bit; take the "spread" via ~n for negatives (~n == -n - 1) so the
    formula is one-sided.
    """
    magnitude = n if n >= 0 else ~n
    bits = magnitude.bit_length() + 1
    return max(1, -(-bits // 7))


u64_ints = st.integers(min_value=0, max_value=2**64 - 1)
i64_ints = st.integers(min_value=-(2**63), max_value=2**63 - 1)


@given(u64_ints)
def test_uleb_round_trips_and_is_minimal(n: int) -> None:
    encoded = encode.uleb(n)
    decoded, used = decode_uleb(encoded)
    assert decoded == n
    assert used == len(encoded)
    assert len(encoded) == uleb_min_len(n)


@given(i64_ints)
def test_sleb_round_trips_and_is_minimal(n: int) -> None:
    encoded = encode.sleb(n)
    decoded, used = decode_sleb(encoded)
    assert decoded == n
    assert used == len(encoded)
    assert len(encoded) == sleb_min_len(n)


# --- framing vectors, hand-extracted from spikes/spike1/spike.wasm (R5) ----
#
# spike.wasm's type section (section id 1) holds 4 function types, all
# (i64...)->i64: extracted via `section id, len-bytes, payload` walk over the
# module's section table.

_FUNCTYPE_0 = bytes.fromhex("60017e017e")  # (i64) -> i64
_FUNCTYPE_1 = bytes.fromhex("60027e7e017e")  # (i64, i64) -> i64
_FUNCTYPE_2 = bytes.fromhex("60037e7e7e017e")  # (i64, i64, i64) -> i64
_FUNCTYPE_3 = bytes.fromhex("6000017e")  # () -> i64
_TYPE_SECTION_PAYLOAD = bytes.fromhex("0460017e017e60027e7e017e60037e7e7e017e6000017e")
_TYPE_SECTION_FULL = bytes.fromhex("01170460017e017e60027e7e017e60037e7e7e017e6000017e")

# The export section (section id 7): three exports, "setup"/"bump"/"memory".
_EXPORT_SECTION_PAYLOAD = bytes.fromhex("0305736574757000080462756d700009066d656d6f72790200")
_EXPORT_SECTION_FULL = bytes.fromhex("07190305736574757000080462756d700009066d656d6f72790200")

# The first custom section: name "contractenvmetav0" (17 chars) + payload.
_CONTRACTENVMETAV0_NAME_FRAMED = bytes.fromhex("11636f6e7472616374656e766d6574617630")
_CONTRACTENVMETAV0_PAYLOAD = bytes.fromhex("000000000000001b00000000")
_CONTRACTENVMETAV0_SECTION_FULL = bytes.fromhex(
    "001e11636f6e7472616374656e766d6574617630000000000000001b00000000"
)


def test_vec_matches_type_section_payload() -> None:
    assert encode.vec([_FUNCTYPE_0, _FUNCTYPE_1, _FUNCTYPE_2, _FUNCTYPE_3]) == _TYPE_SECTION_PAYLOAD


def test_section_matches_type_and_export_sections() -> None:
    assert encode.section(1, _TYPE_SECTION_PAYLOAD) == _TYPE_SECTION_FULL
    assert encode.section(7, _EXPORT_SECTION_PAYLOAD) == _EXPORT_SECTION_FULL


def test_wasm_name_matches_export_names() -> None:
    assert encode.wasm_name("setup") == bytes.fromhex("057365747570")
    assert encode.wasm_name("memory") == bytes.fromhex("066d656d6f7279")
    assert encode.wasm_name("contractenvmetav0") == _CONTRACTENVMETAV0_NAME_FRAMED


def test_custom_section_matches_contractenvmetav0() -> None:
    assert (
        encode.custom_section("contractenvmetav0", _CONTRACTENVMETAV0_PAYLOAD)
        == _CONTRACTENVMETAV0_SECTION_FULL
    )
