from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from serpent import val
from serpent.errors import ArithmeticOverflow
from serpent.types import I32, I64, I128, U32, U64, U128, Duration, Timepoint
from serpent.types.numeric import _ChainArith, _ChainInt

INT_TYPES: list[type[_ChainInt]] = [U32, I32, U64, I64, U128, I128, Timepoint, Duration]
TYPE_IDS: list[str] = [cls.__name__ for cls in INT_TYPES]
# Timepoint/Duration expose no arithmetic at all, so the arithmetic properties
# run over the types that do.
ARITH_TYPES: list[type[_ChainArith]] = [U32, I32, U64, I64, U128, I128]
ARITH_TYPE_IDS: list[str] = [cls.__name__ for cls in ARITH_TYPES]


# --- Reference model ---------------------------------------------------------
# `//` truncates toward zero and `%` takes the dividend's sign (WASM div_s/rem_s,
# Rust). Derived here from Python's FLOOR division and corrected toward zero, so
# the model is an independent derivation rather than a copy of the implementation
# (and stays exact for arbitrary-width ints -- no float round-trip).


def ref_div(a: int, b: int) -> int:
    q = a // b  # Python floor
    if q < 0 and q * b != a:
        q += 1  # ... corrected toward zero
    return q


def ref_rem(a: int, b: int) -> int:
    return a - b * ref_div(a, b)


def test_reference_model_matches_the_known_chain_answers() -> None:
    assert (ref_div(-7, 2), ref_rem(-7, 2)) == (-3, -1)
    assert (ref_div(7, -2), ref_rem(7, -2)) == (-3, 1)
    assert (ref_div(-7, -2), ref_rem(-7, -2)) == (3, -1)
    assert (ref_div(7, 2), ref_rem(7, 2)) == (3, 1)
    assert (ref_div(-(2**31), -1), ref_rem(-(2**31), -1)) == (2**31, 0)
    assert (ref_div(-6, 3), ref_rem(-6, 3)) == (-2, 0)


def values_for(cls: type[_ChainInt]) -> st.SearchStrategy[int]:
    """Uniform draws over the type's whole range, mixed with the edge values that
    a uniform draw over a 2**128-wide range would essentially never produce."""
    interesting = [
        v
        for v in (
            0,
            1,
            -1,
            2,
            -2,
            7,
            -7,
            cls.MIN,
            cls.MIN + 1,
            cls.MAX,
            cls.MAX - 1,
            val.MAX_SMALL_U64,
            val.MAX_SMALL_U64 + 1,
            val.MAX_SMALL_I64,
            val.MAX_SMALL_I64 + 1,
            val.MIN_SMALL_I64,
            val.MIN_SMALL_I64 - 1,
            2**31,
            2**32,
            2**63,
            2**64,
        )
        if cls.MIN <= v <= cls.MAX
    ]
    return st.one_of(
        st.integers(min_value=cls.MIN, max_value=cls.MAX),
        st.sampled_from(interesting),
    )


def check(cls: type[_ChainArith], op: Callable[[], _ChainArith], expected: int) -> None:
    """The op equals the reference model when the result fits, and raises
    ArithmeticOverflow exactly when it does not."""
    if cls.MIN <= expected <= cls.MAX:
        result = op()
        assert type(result) is cls
        assert result.value == expected
    else:
        with pytest.raises(ArithmeticOverflow):
            op()


@pytest.mark.parametrize("cls", ARITH_TYPES, ids=ARITH_TYPE_IDS)
@given(data=st.data())
def test_arithmetic_matches_the_truncating_reference_model(
    cls: type[_ChainArith], data: st.DataObject
) -> None:
    a: int = data.draw(values_for(cls))
    b: int = data.draw(values_for(cls))
    x, y = cls(a), cls(b)

    check(cls, lambda: x + y, a + b)
    check(cls, lambda: x - y, a - b)
    check(cls, lambda: x * y, a * b)
    check(cls, lambda: -x, -a)
    # Reflected forms compute the same thing with the same checks.
    check(cls, lambda: b + x, b + a)
    check(cls, lambda: b - x, b - a)
    check(cls, lambda: b * x, b * a)

    if b == 0:
        with pytest.raises(ZeroDivisionError):
            x // y
        with pytest.raises(ZeroDivisionError):
            x % y
    else:
        check(cls, lambda: x // y, ref_div(a, b))
        check(cls, lambda: x % y, ref_rem(a, b))
        check(cls, lambda: x // b, ref_div(a, b))
        check(cls, lambda: x % b, ref_rem(a, b))
    if a != 0:
        check(cls, lambda: b // x, ref_div(b, a))
        check(cls, lambda: b % x, ref_rem(b, a))


@pytest.mark.parametrize("cls", ARITH_TYPES, ids=ARITH_TYPE_IDS)
@given(data=st.data())
def test_remainder_never_overflows_and_agrees_with_division(
    cls: type[_ChainArith], data: st.DataObject
) -> None:
    a: int = data.draw(values_for(cls))
    b: int = data.draw(values_for(cls).filter(lambda v: v != 0))
    r = (cls(a) % cls(b)).value
    assert r == ref_rem(a, b)
    # a == b * trunc(a/b) + rem, with rem taking the dividend's sign.
    assert b * ref_div(a, b) + r == a
    assert r == 0 or (r < 0) == (a < 0)
    assert abs(r) < abs(b)


def fits_small(cls: type[_ChainInt], x: int) -> bool:
    if cls in (U32, I32):
        return True  # always inline
    return val.fits_small_i(x) if cls.MIN < 0 else val.fits_small_u(x)


@pytest.mark.parametrize("cls", INT_TYPES, ids=TYPE_IDS)
@given(data=st.data())
def test_val_round_trips_for_small_forms(cls: type[_ChainInt], data: st.DataObject) -> None:
    x: int = data.draw(values_for(cls))
    instance = cls(x)
    if fits_small(cls, x):
        v = instance.to_val()
        assert cls.from_val(v) == instance
        assert val.tag_of(v) < 64  # not an object handle
    else:
        with pytest.raises(NotImplementedError):
            instance.to_val()


@pytest.mark.parametrize("cls", INT_TYPES, ids=TYPE_IDS)
@given(data=st.data())
def test_comparisons_agree_with_int_comparisons(cls: type[_ChainInt], data: st.DataObject) -> None:
    a: int = data.draw(values_for(cls))
    b: int = data.draw(values_for(cls))
    x, y = cls(a), cls(b)
    assert (x < y) == (a < b)
    assert (x <= y) == (a <= b)
    assert (x > y) == (a > b)
    assert (x >= y) == (a >= b)
    assert (x == y) == (a == b)
    assert (x != y) == (a != b)
    # Ordering against a plain int is answered mathematically, in range or not.
    assert (x < b) == (a < b)
    assert (x >= b) == (a >= b)
    assert (x < cls.MAX + 1) is True
    assert (x > cls.MIN - 1) is True


@pytest.mark.parametrize("cls", INT_TYPES, ids=TYPE_IDS)
@given(data=st.data())
def test_eq_hash_invariant_against_equal_ints(cls: type[_ChainInt], data: st.DataObject) -> None:
    a: int = data.draw(values_for(cls))
    x = cls(a)
    assert (x == a) is True
    assert hash(x) == hash(a)
    assert (x == cls.MAX + 1) is False  # out-of-range int: False, never raises
    assert (x == cls.MIN - 1) is False
    assert (x == object()) is False
    assert len({x, cls(a)}) == 1
