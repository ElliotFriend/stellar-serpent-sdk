import pytest

from serpent import val


def test_tag_constants_match_verified_table() -> None:
    assert (val.TAG_FALSE, val.TAG_TRUE, val.TAG_VOID, val.TAG_ERROR) == (0, 1, 2, 3)
    assert (val.TAG_U32, val.TAG_I32) == (4, 5)
    assert (val.TAG_U64_SMALL, val.TAG_I64_SMALL) == (6, 7)
    assert (val.TAG_TIMEPOINT_SMALL, val.TAG_DURATION_SMALL) == (8, 9)
    assert (val.TAG_U128_SMALL, val.TAG_I128_SMALL) == (10, 11)
    assert (val.TAG_U256_SMALL, val.TAG_I256_SMALL) == (12, 13)
    assert val.TAG_SYMBOL_SMALL == 14
    assert (val.TAG_U64_OBJECT, val.TAG_MAP_OBJECT, val.TAG_ADDRESS_OBJECT) == (64, 76, 77)
    assert (val.TAG_MUXED_ADDRESS_OBJECT, val.TAG_EXECUTABLE_TAG_OBJECT) == (78, 79)
    assert val.TAG_BAD == 0x7F


def test_layout_helpers() -> None:
    v = val.from_major_minor_tag(7, 0, val.TAG_U32)
    assert v == (7 << 32) | 4
    assert val.tag_of(v) == 4 and val.major_of(v) == 7 and val.minor_of(v) == 0
    assert val.body_of(v) == 7 << 24


def test_bool_and_void() -> None:
    assert val.pack_bool(True) == 1 and val.pack_bool(False) == 0
    assert val.unpack_bool(1) is True and val.unpack_bool(0) is False
    assert val.VOID_VAL == 2
    with pytest.raises(ValueError):
        val.unpack_bool(2)  # Void is not a bool


def test_u32val_golden() -> None:
    assert val.pack_u32val(0) == 4
    assert val.pack_u32val(3_000_000_000) == (3_000_000_000 << 32) | 4
    assert val.unpack_u32val((3_000_000_000 << 32) | 4) == 3_000_000_000
    with pytest.raises(ValueError):
        val.pack_u32val(2**32)
    with pytest.raises(ValueError):
        val.pack_u32val(-1)


def test_unpack_checks_tag() -> None:
    with pytest.raises(ValueError):
        val.unpack_u32val(val.pack_i32val(1))
    with pytest.raises(ValueError):
        val.unpack_small_i64(val.pack_small_u64(1, val.TAG_U64_SMALL), val.TAG_I64_SMALL)


def test_i32val_bit_pattern() -> None:
    assert val.pack_i32val(-1) == (0xFFFF_FFFF << 32) | 5
    assert val.unpack_i32val((0xFFFF_FFFF << 32) | 5) == -1


def test_small_i64_round_trip_bounds() -> None:
    for x in (0, 1, -1, val.MAX_SMALL_I64, val.MIN_SMALL_I64):
        packed = val.pack_small_i64(x, val.TAG_I64_SMALL)
        assert val.unpack_small_i64(packed, val.TAG_I64_SMALL) == x
    with pytest.raises(ValueError):
        val.pack_small_i64(val.MAX_SMALL_I64 + 1, val.TAG_I64_SMALL)


def test_signed_boundary_masking() -> None:
    # wasmtime hands back signed i64; as_u64/as_i64 must round-trip bit patterns
    assert val.as_u64(-1) == val.MASK64
    assert val.as_i64(val.MASK64) == -1
    assert val.as_u64(val.as_i64((3_000_000_000 << 32) | 4)) == (3_000_000_000 << 32) | 4


def test_is_object_range() -> None:
    assert not val.is_object(val.pack_u32val(9))
    assert val.is_object(val.from_major_minor_tag(1, 0, val.TAG_VEC_OBJECT))
    assert val.is_object(val.from_major_minor_tag(1, 0, val.TAG_EXECUTABLE_TAG_OBJECT))
    assert not val.is_object(val.TAG_BAD)  # 0x7F is Bad, not an object


def test_symbol_small_goldens_from_chain() -> None:
    assert val.symbol_small("COUNTER") == 253576579652878
    assert val.symbol_small("COUNT") == 61908344590


def test_symbol_char_codes() -> None:
    assert val.symbol_char_code("_") == 1
    assert val.symbol_char_code("0") == 2 and val.symbol_char_code("9") == 11
    assert val.symbol_char_code("A") == 12 and val.symbol_char_code("Z") == 37
    assert val.symbol_char_code("a") == 38 and val.symbol_char_code("z") == 63


def test_symbol_small_rejects() -> None:
    with pytest.raises(ValueError):
        val.symbol_small("ten_chars_")
    with pytest.raises(ValueError):
        val.symbol_small("has-dash")
    with pytest.raises(ValueError):
        val.symbol_small("")


def test_symbol_validation_boundaries() -> None:
    assert val.is_valid_symbol("a" * 32) and not val.is_valid_symbol("a" * 33)
    assert val.fits_symbol_small("nine_char") and not val.fits_symbol_small("ten_chars_")


def test_symbol_small_text_round_trip() -> None:
    for s in ("A", "COUNT", "z9_", "nine_char"):
        assert val.symbol_small_text(val.symbol_small(s)) == s
