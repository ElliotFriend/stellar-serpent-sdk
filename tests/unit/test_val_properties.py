from hypothesis import given, strategies as st

from serpent import val

u32s = st.integers(min_value=0, max_value=2**32 - 1)
i32s = st.integers(min_value=-(2**31), max_value=2**31 - 1)
small_us = st.integers(min_value=0, max_value=val.MAX_SMALL_U64)
small_is = st.integers(min_value=val.MIN_SMALL_I64, max_value=val.MAX_SMALL_I64)
u64_bits = st.integers(min_value=0, max_value=2**64 - 1)


@given(u32s)
def test_u32val_round_trips(x: int) -> None:
    assert val.unpack_u32val(val.pack_u32val(x)) == x


@given(i32s)
def test_i32val_round_trips(x: int) -> None:
    assert val.unpack_i32val(val.pack_i32val(x)) == x


@given(small_us)
def test_small_u64_round_trips(x: int) -> None:
    assert val.unpack_small_u64(val.pack_small_u64(x, val.TAG_U64_SMALL), val.TAG_U64_SMALL) == x


@given(small_is)
def test_small_i64_round_trips(x: int) -> None:
    assert val.unpack_small_i64(val.pack_small_i64(x, val.TAG_I64_SMALL), val.TAG_I64_SMALL) == x


@given(u64_bits)
def test_signed_masking_is_a_bijection(bits: int) -> None:
    assert val.as_u64(val.as_i64(bits)) == bits


symbol_alphabet = st.sampled_from("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
small_symbols = st.text(alphabet=symbol_alphabet, min_size=1, max_size=9)


@given(small_symbols)
def test_symbol_small_round_trips(s: str) -> None:
    assert val.symbol_small_text(val.symbol_small(s)) == s


@given(small_symbols)
def test_symbol_small_tag(s: str) -> None:
    assert val.tag_of(val.symbol_small(s)) == val.TAG_SYMBOL_SMALL
