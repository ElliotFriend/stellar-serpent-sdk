"""The single Val codec for serpent (spec §10: one implementation, everywhere).

A Val is a 64-bit host value: the low 8 bits are a tag, the remaining 56 bits
are the body (`val = (body << 8) | tag`). For small-value and object-handle
forms the body further splits 32/24 into major/minor
(`body = (major << 24) | minor`). This module is the only place that encodes
or decodes that layout; everything else imports it.
"""

MASK64 = (1 << 64) - 1
MASK56 = (1 << 56) - 1
MAX_SMALL_U64 = MASK56
MAX_SMALL_I64 = (1 << 55) - 1
MIN_SMALL_I64 = -(1 << 55)

# --- Tags: non-object, small-value forms ------------------------------------
TAG_FALSE = 0
TAG_TRUE = 1
TAG_VOID = 2
TAG_ERROR = 3
TAG_U32 = 4
TAG_I32 = 5
TAG_U64_SMALL = 6
TAG_I64_SMALL = 7
TAG_TIMEPOINT_SMALL = 8
TAG_DURATION_SMALL = 9
TAG_U128_SMALL = 10
TAG_I128_SMALL = 11
TAG_U256_SMALL = 12
TAG_I256_SMALL = 13
TAG_SYMBOL_SMALL = 14

# --- Tags: object handles (64-79) -------------------------------------------
# Values verified against soroban-env-common/src/val.rs @ v28.0.2.
# MuxedAddressObject=78 was protocol-gated in at protocol 23; ExecutableTagObject=79
# at protocol 28. The frozen spike harness (spikes/spike1/harness.py) predates both
# and treats 79 as an exclusive upper bound (i.e. matching protocol 23) -- that
# bound is stale and must not be copied into this module.
TAG_U64_OBJECT = 64
TAG_I64_OBJECT = 65
TAG_TIMEPOINT_OBJECT = 66
TAG_DURATION_OBJECT = 67
TAG_U128_OBJECT = 68
TAG_I128_OBJECT = 69
TAG_U256_OBJECT = 70
TAG_I256_OBJECT = 71
TAG_BYTES_OBJECT = 72
TAG_STRING_OBJECT = 73
TAG_SYMBOL_OBJECT = 74
TAG_VEC_OBJECT = 75
TAG_MAP_OBJECT = 76
TAG_ADDRESS_OBJECT = 77
TAG_MUXED_ADDRESS_OBJECT = 78
TAG_EXECUTABLE_TAG_OBJECT = 79

TAG_BAD = 0x7F

VOID_VAL = 2
TRUE_VAL = 1
FALSE_VAL = 0


def as_u64(v: int) -> int:
    """Mask an arbitrary Python int down to its unsigned 64-bit bit pattern."""
    return v & MASK64


def as_i64(v: int) -> int:
    """Reinterpret a 64-bit bit pattern as a signed i64 (wasmtime's return type)."""
    v &= MASK64
    return v - (1 << 64) if v >= 1 << 63 else v


def tag_of(v: int) -> int:
    return v & 0xFF


def body_of(v: int) -> int:
    return (v >> 8) & MASK56


def major_of(v: int) -> int:
    return body_of(v) >> 24


def minor_of(v: int) -> int:
    return body_of(v) & 0xFFFFFF


def is_object(v: int) -> bool:
    """True iff v's tag falls in the object-handle range (64-79 inclusive)."""
    tag = tag_of(v)
    return 63 < tag < 80


def from_body_tag(body: int, tag: int) -> int:
    return (body << 8) | tag


def from_major_minor_tag(major: int, minor: int, tag: int) -> int:
    return from_body_tag((major << 24) | minor, tag)


def _require_tag(v: int, expected: int) -> None:
    if tag_of(v) != expected:
        raise ValueError(f"expected tag {expected}, found tag {tag_of(v)} in {v:#x}")


def pack_bool(b: bool) -> int:
    return TRUE_VAL if b else FALSE_VAL


def unpack_bool(v: int) -> bool:
    """Decode a bool Val.

    Raises ValueError naming the found value if v is neither TRUE_VAL nor
    FALSE_VAL (e.g. VOID_VAL, which is a distinct tag from both).
    """
    if v == TRUE_VAL:
        return True
    if v == FALSE_VAL:
        return False
    raise ValueError(f"expected tag {TAG_TRUE} or {TAG_FALSE}, found tag {tag_of(v)} in {v:#x}")


def pack_u32val(x: int) -> int:
    if not 0 <= x <= 0xFFFF_FFFF:
        raise ValueError(f"does not fit u32: {x}")
    return from_major_minor_tag(x, 0, TAG_U32)


def unpack_u32val(v: int) -> int:
    """Validates the tag first, raising ValueError naming the found and expected tags."""
    _require_tag(v, TAG_U32)
    return major_of(v)


def pack_i32val(x: int) -> int:
    if not -(1 << 31) <= x <= (1 << 31) - 1:
        raise ValueError(f"does not fit i32: {x}")
    return from_major_minor_tag(x & 0xFFFF_FFFF, 0, TAG_I32)


def unpack_i32val(v: int) -> int:
    """Validates the tag first, raising ValueError naming the found and expected tags."""
    _require_tag(v, TAG_I32)
    major = major_of(v)
    return major - (1 << 32) if major >= 1 << 31 else major


def fits_small_u(x: int) -> bool:
    return 0 <= x <= MAX_SMALL_U64


def fits_small_i(x: int) -> bool:
    return MIN_SMALL_I64 <= x <= MAX_SMALL_I64


def pack_small_u64(x: int, tag: int) -> int:
    if not fits_small_u(x):
        raise ValueError(f"does not fit 56-bit unsigned small form: {x}")
    return from_body_tag(x, tag)


def unpack_small_u64(v: int, expected_tag: int) -> int:
    """Validates the tag first, raising ValueError naming the found and expected tags."""
    _require_tag(v, expected_tag)
    return body_of(v)


def pack_small_i64(x: int, tag: int) -> int:
    if not MIN_SMALL_I64 <= x <= MAX_SMALL_I64:
        raise ValueError(f"does not fit 56-bit signed small form: {x}")
    return from_body_tag(x & MASK56, tag)


def unpack_small_i64(v: int, expected_tag: int) -> int:
    """Validates the tag first, raising ValueError naming the found and expected tags."""
    _require_tag(v, expected_tag)
    body = body_of(v)
    return body - (1 << 56) if body >= 1 << 55 else body
