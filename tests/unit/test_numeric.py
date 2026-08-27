import pytest

from serpent import val
from serpent.errors import ArithmeticOverflow
from serpent.types import I32, I128, U32, U64, Bool, Duration, Timepoint

# I64/U128 are not on the brief's verbatim import line above but are needed by
# the analogous blocks ("write all analogous blocks for every type"); imported
# from their defining module so the verbatim line stays untouched.
from serpent.types.numeric import I64, U128, _ChainInt

INT_TYPES: list[type[_ChainInt]] = [U32, I32, U64, I64, U128, I128, Timepoint, Duration]
SIGNED_TYPES: list[type[_ChainInt]] = [I32, I64, I128]
UNSIGNED_TYPES: list[type[_ChainInt]] = [U32, U64, U128, Timepoint, Duration]
SMALL_UNSIGNED_TYPES: list[type[_ChainInt]] = [U64, U128, Timepoint, Duration]
SMALL_SIGNED_TYPES: list[type[_ChainInt]] = [I64, I128]


def test_construction_bounds() -> None:
    assert U32(0).value == 0 and U32(2**32 - 1).value == 2**32 - 1
    with pytest.raises(ValueError):
        U32(2**32)
    with pytest.raises(ValueError):
        U32(-1)


def test_declared_bounds_every_type() -> None:
    assert (U32.MIN, U32.MAX) == (0, 2**32 - 1)
    assert (I32.MIN, I32.MAX) == (-(2**31), 2**31 - 1)
    assert (U64.MIN, U64.MAX) == (0, 2**64 - 1)
    assert (I64.MIN, I64.MAX) == (-(2**63), 2**63 - 1)
    assert (U128.MIN, U128.MAX) == (0, 2**128 - 1)
    assert (I128.MIN, I128.MAX) == (-(2**127), 2**127 - 1)
    assert (Timepoint.MIN, Timepoint.MAX) == (0, 2**64 - 1)
    assert (Duration.MIN, Duration.MAX) == (0, 2**64 - 1)


def test_construction_bounds_every_type() -> None:
    for cls in INT_TYPES:
        assert cls(cls.MIN).value == cls.MIN
        assert cls(cls.MAX).value == cls.MAX
        with pytest.raises(ValueError):
            cls(cls.MIN - 1)
        with pytest.raises(ValueError):
            cls(cls.MAX + 1)


def test_construction_rejects_non_int() -> None:
    with pytest.raises(TypeError):
        U32("5")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Bool(1)  # type: ignore[arg-type]


def test_instances_are_immutable() -> None:
    x = U32(1)
    with pytest.raises(AttributeError):
        x._value = 2
    with pytest.raises(AttributeError):
        del x._value
    assert x.value == 1


def test_checked_arithmetic_overflow_raises() -> None:
    with pytest.raises(ArithmeticOverflow):
        U32(2**32 - 1) + U32(1)
    with pytest.raises(ArithmeticOverflow):
        U32(0) - U32(1)
    with pytest.raises(ArithmeticOverflow):
        I32(-(2**31)) - 1
    with pytest.raises(ArithmeticOverflow):
        -U32(1)                      # unary minus out of range
    with pytest.raises(ArithmeticOverflow):
        -I32(-(2**31))
    assert -I32(5) == I32(-5)


def test_checked_arithmetic_overflow_every_type() -> None:
    for cls in INT_TYPES:
        with pytest.raises(ArithmeticOverflow):
            cls(cls.MAX) + 1
        with pytest.raises(ArithmeticOverflow):
            cls(cls.MIN) - 1
        with pytest.raises(ArithmeticOverflow):
            cls(cls.MAX) * 2
        assert cls(cls.MAX) - cls(cls.MAX) == cls(0)
        assert cls(cls.MAX) * 1 == cls(cls.MAX)
        assert cls(0) * cls(0) == cls(0)


def test_unary_minus_every_type() -> None:
    for cls in UNSIGNED_TYPES:
        assert -cls(0) == cls(0)
        with pytest.raises(ArithmeticOverflow):
            -cls(1)
    for cls in SIGNED_TYPES:
        assert -cls(5) == cls(-5) and -cls(-5) == cls(5)
        with pytest.raises(ArithmeticOverflow):
            -cls(cls.MIN)
        with pytest.raises(ArithmeticOverflow):
            cls(cls.MIN) * -1


def test_truncating_division_semantics() -> None:
    # WASM div_s/rem_s truncate toward zero; Python floors. We match the chain.
    assert I32(-7) // I32(2) == I32(-3)      # Python would say -4
    assert I32(-7) % I32(2) == I32(-1)       # Python would say 1
    assert I32(7) // I32(-2) == I32(-3) and I32(7) % I32(-2) == I32(1)
    with pytest.raises(ArithmeticOverflow):
        I32(-(2**31)) // I32(-1)             # overflows i32
    assert I32(-(2**31)) % I32(-1) == I32(0)  # rem_s does NOT trap here
    with pytest.raises(ZeroDivisionError):
        U32(1) // U32(0)


def test_truncating_division_every_signed_type() -> None:
    for cls in SIGNED_TYPES:
        assert cls(-7) // cls(2) == cls(-3) and cls(-7) % cls(2) == cls(-1)
        assert cls(7) // cls(-2) == cls(-3) and cls(7) % cls(-2) == cls(1)
        assert cls(-7) // cls(-2) == cls(3) and cls(-7) % cls(-2) == cls(-1)
        assert cls(7) // cls(2) == cls(3) and cls(7) % cls(2) == cls(1)
        assert cls(-6) // cls(2) == cls(-3) and cls(-6) % cls(2) == cls(0)
        with pytest.raises(ArithmeticOverflow):
            cls(cls.MIN) // cls(-1)
        assert cls(cls.MIN) % cls(-1) == cls(0)
        with pytest.raises(ZeroDivisionError):
            cls(1) // cls(0)
        with pytest.raises(ZeroDivisionError):
            cls(1) % cls(0)


def test_division_every_unsigned_type() -> None:
    for cls in UNSIGNED_TYPES:
        assert cls(7) // cls(2) == cls(3) and cls(7) % cls(2) == cls(1)
        assert cls(cls.MAX) // cls(cls.MAX) == cls(1)
        with pytest.raises(ZeroDivisionError):
            cls(1) // cls(0)
        with pytest.raises(ZeroDivisionError):
            cls(1) % cls(0)


def test_int_coercion_and_reflected_ops() -> None:
    assert (U32(5) + 3) == U32(8)
    assert (3 + U32(5)) == U32(8)            # __radd__: sum() works
    assert sum([U32(1), U32(2)], start=U32(0)) == U32(3)
    with pytest.raises(ValueError):
        U32(5) + (2**32)


def test_reflected_ops_for_every_operator() -> None:
    assert 10 - U32(3) == U32(7)
    assert 3 * U32(5) == U32(15)
    assert 7 // U32(2) == U32(3)
    assert 7 % U32(2) == U32(1)
    assert -7 // I32(2) == I32(-3)           # truncating on the reflected path too
    assert -7 % I32(2) == I32(-1)
    with pytest.raises(ArithmeticOverflow):
        0 - U32(1)
    with pytest.raises(ZeroDivisionError):
        1 // U32(0)


def test_augmented_assignment_uses_the_binary_ops() -> None:
    x = U32(5)
    x += 3
    assert x == U32(8)
    x -= U32(8)
    assert x == U32(0)
    with pytest.raises(ArithmeticOverflow):
        x -= 1


def test_out_of_range_int_operand_raises_value_error_on_either_side() -> None:
    with pytest.raises(ValueError):
        U32(5) - (2**40)
    with pytest.raises(ValueError):
        (2**40) + U32(5)
    with pytest.raises(ValueError):
        U32(5) * -1
    with pytest.raises(ValueError):
        I32(1) + 2**31


def test_no_implicit_widening_in_arithmetic() -> None:
    with pytest.raises(TypeError):
        U32(1) + U64(1)           # type: ignore[operator]
    with pytest.raises(TypeError):
        Timepoint(1) + Duration(1)  # type: ignore[operator]
    with pytest.raises(TypeError):
        U32(2) ** U32(3)          # type: ignore[operator]


def test_no_implicit_narrowing_or_bool_mixing_in_arithmetic() -> None:
    with pytest.raises(TypeError):
        U64(1) + U32(1)           # type: ignore[operator]
    with pytest.raises(TypeError):
        I64(1) * I32(2)           # type: ignore[operator]
    with pytest.raises(TypeError):
        Duration(5) - Timepoint(1)  # type: ignore[operator]
    with pytest.raises(TypeError):
        U32(1) * Bool(True)       # type: ignore[operator]


def test_unsupported_operators_name_the_omission() -> None:
    with pytest.raises(TypeError, match=r"\*\*"):
        U32(2) ** U32(3)          # type: ignore[operator]
    with pytest.raises(TypeError, match=r"\*\*"):
        2 ** U32(3)               # type: ignore[operator]
    with pytest.raises(TypeError, match="divmod"):
        divmod(U32(7), U32(2))    # type: ignore[operator]
    with pytest.raises(TypeError, match="divmod"):
        divmod(7, U32(2))         # type: ignore[operator]
    with pytest.raises(TypeError, match="&"):
        U32(1) & U32(2)           # type: ignore[operator]
    with pytest.raises(TypeError, match=r"\|"):
        U32(1) | U32(2)           # type: ignore[operator]
    with pytest.raises(TypeError, match=r"\^"):
        U32(1) ^ U32(2)           # type: ignore[operator]
    with pytest.raises(TypeError, match="<<"):
        U32(1) << 2               # type: ignore[operator]
    with pytest.raises(TypeError, match=">>"):
        U32(1) >> 2               # type: ignore[operator]
    with pytest.raises(TypeError, match="~"):
        ~U32(1)
    with pytest.raises(TypeError, match="&"):
        1 & U32(2)                # type: ignore[operator]
    with pytest.raises(TypeError, match="<<"):
        1 << U32(2)               # type: ignore[operator]


def test_equality_never_raises_and_hash_contract() -> None:
    assert U32(1) == 1 and hash(U32(1)) == hash(1)
    assert (U32(1) == U64(1)) is False        # foreign chain type: False, not TypeError
    assert (U32(5) == 2**40) is False         # out-of-range int: False
    assert U32(5) < 2**40                     # ordering vs any int is mathematical
    # mypy cannot type an int lookup in a dict[U32, str]; the eq/hash invariant it
    # exercises is exactly the point of the assertion, so the ignore stays narrow.
    assert {U32(1): "a"}[1] == "a"            # type: ignore[index]  # eq/hash invariant holds


def test_equality_matrix() -> None:
    not_a_chain_value: object = None
    assert (U32(1) == U32(1)) is True
    assert (U32(1) == U32(2)) is False
    assert (U32(1) == I32(1)) is False
    assert (Timepoint(1) == Duration(1)) is False
    assert (Timepoint(1) == U64(1)) is False
    assert (U32(1) == Bool(True)) is False
    assert (Bool(True) == U32(1)) is False
    assert (U32(1) == "1") is False
    assert (U32(1) == not_a_chain_value) is False
    assert (U32(1) != U32(2)) is True
    assert (U32(1) != 1) is False
    for cls in INT_TYPES:
        assert cls(cls.MIN) == cls.MIN
        assert hash(cls(cls.MAX)) == hash(cls.MAX)
        assert (cls(0) == cls.MAX + 1) is False


def test_ordering() -> None:
    assert U32(1) < U32(2) and U32(2) > U32(1)
    assert U32(1) <= U32(1) and U32(1) >= U32(1)
    assert not (U32(1) < U32(1)) and not (U32(2) <= U32(1))
    assert U32(5) < 2**40 and U32(5) > -1 and U32(5) >= 5 and U32(5) <= 5
    assert I32(-1) < 0 and I128(-(2**127)) < -1
    assert sorted([U32(3), U32(1), U32(2)]) == [U32(1), U32(2), U32(3)]
    assert min(I64(-1), I64(1)) == I64(-1)


def test_ordering_against_foreign_chain_types_raises() -> None:
    with pytest.raises(TypeError):
        _ = U32(1) < U64(1)             # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = Timepoint(1) <= Duration(1)  # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = U32(1) > Bool(True)         # type: ignore[operator]


def test_scval_ranks_follow_scval_type_order() -> None:
    assert Bool._SCVAL_RANK == 0
    ordered: list[type[_ChainInt]] = [U32, I32, U64, I64, Timepoint, Duration, U128, I128]
    assert [cls._SCVAL_RANK for cls in ordered] == [3, 4, 5, 6, 7, 8, 9, 10]


def test_cmp_payload_is_the_numeric_value() -> None:
    assert U32(7)._cmp_payload() == 7
    assert I128(-1)._cmp_payload() == -1
    assert Bool(True)._cmp_payload() is True
    assert Bool(False)._cmp_payload() is False


def test_repr() -> None:
    assert repr(U32(7)) == "U32(7)"
    assert repr(I128(-1)) == "I128(-1)"
    assert repr(Timepoint(5)) == "Timepoint(5)"
    assert repr(Duration(0)) == "Duration(0)"
    assert repr(Bool(False)) == "Bool(False)"


def test_bool_type() -> None:
    assert Bool(True).to_val() == val.TRUE_VAL and Bool(False).to_val() == val.FALSE_VAL
    assert bool(Bool(True)) and not bool(Bool(False))
    assert Bool(False) < Bool(True)


def test_bool_details() -> None:
    truth = True
    assert Bool(True).value is True and Bool(False).value is False
    assert Bool(True) == Bool(True) and (Bool(True) == Bool(False)) is False
    assert Bool(True) == truth
    assert hash(Bool(True)) == hash(True) and hash(Bool(False)) == hash(False)
    assert Bool(True) > Bool(False) and Bool(False) <= Bool(False)
    assert Bool.from_val(val.TRUE_VAL) == Bool(True)
    assert Bool.from_val(val.FALSE_VAL) == Bool(False)
    with pytest.raises(ValueError):
        Bool.from_val(val.VOID_VAL)
    with pytest.raises(AttributeError):
        Bool(True)._value = False


def test_bool_has_no_arithmetic() -> None:
    with pytest.raises(TypeError):
        Bool(True) + Bool(False)  # type: ignore[operator]
    with pytest.raises(TypeError):
        Bool(True) + 1            # type: ignore[operator]
    with pytest.raises(TypeError):
        -Bool(True)               # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = Bool(True) < 1        # type: ignore[operator]


def test_i128_limbs_golden() -> None:
    assert I128(-1).hi64 == -1 and I128(-1).lo64 == 2**64 - 1
    assert I128(-(2**64)).hi64 == -1 and I128(-(2**64)).lo64 == 0


def test_i128_limbs_more() -> None:
    assert I128(0).hi64 == 0 and I128(0).lo64 == 0
    assert I128(1).hi64 == 0 and I128(1).lo64 == 1
    assert I128(2**64).hi64 == 1 and I128(2**64).lo64 == 0
    assert I128(-(2**127)).hi64 == -(2**63) and I128(-(2**127)).lo64 == 0
    assert I128(2**127 - 1).hi64 == 2**63 - 1 and I128(2**127 - 1).lo64 == 2**64 - 1


def test_u128_limbs_golden() -> None:
    assert U128(2**64).hi64 == 1 and U128(2**64).lo64 == 0
    assert U128(5).hi64 == 0 and U128(5).lo64 == 5
    assert U128(2**128 - 1).hi64 == 2**64 - 1 and U128(2**128 - 1).lo64 == 2**64 - 1


def test_timepoint_u64_bridge() -> None:
    assert Timepoint.from_u64(U64(1000)).to_u64() == U64(1000)


def test_duration_u64_bridge() -> None:
    assert Duration.from_u64(U64(0)).to_u64() == U64(0)
    assert Duration.from_u64(U64(2**64 - 1)).value == 2**64 - 1
    assert Timepoint.from_u64(U64(7)).value == 7
    assert isinstance(Timepoint.from_u64(U64(7)), Timepoint)
    assert isinstance(Duration(7).to_u64(), U64)


def test_val_round_trip_and_object_form_boundary() -> None:
    assert U32(9).to_val() == val.pack_u32val(9)
    assert U32.from_val(val.pack_u32val(9)) == U32(9)
    assert U64(val.MAX_SMALL_U64).to_val() == val.pack_small_u64(val.MAX_SMALL_U64, val.TAG_U64_SMALL)
    with pytest.raises(NotImplementedError):
        U64(val.MAX_SMALL_U64 + 1).to_val()


def test_to_val_uses_the_right_tag_for_every_type() -> None:
    assert val.tag_of(U32(1).to_val()) == val.TAG_U32
    assert val.tag_of(I32(-1).to_val()) == val.TAG_I32
    assert val.tag_of(U64(1).to_val()) == val.TAG_U64_SMALL
    assert val.tag_of(I64(-1).to_val()) == val.TAG_I64_SMALL
    assert val.tag_of(Timepoint(1).to_val()) == val.TAG_TIMEPOINT_SMALL
    assert val.tag_of(Duration(1).to_val()) == val.TAG_DURATION_SMALL
    assert val.tag_of(U128(1).to_val()) == val.TAG_U128_SMALL
    assert val.tag_of(I128(-1).to_val()) == val.TAG_I128_SMALL


def test_i32_and_u32_always_have_an_inline_form() -> None:
    for x in (0, 1, 2**32 - 1):
        assert U32.from_val(U32(x).to_val()) == U32(x)
    for y in (-(2**31), -1, 0, 2**31 - 1):
        assert I32.from_val(I32(y).to_val()) == I32(y)
        assert I32(y).to_val() == val.pack_i32val(y)


def test_small_form_boundaries_unsigned() -> None:
    for cls in SMALL_UNSIGNED_TYPES:
        top = cls(val.MAX_SMALL_U64)
        assert top.to_val() == val.pack_small_u64(val.MAX_SMALL_U64, cls._SMALL_TAG)
        assert cls.from_val(top.to_val()) == top
        with pytest.raises(NotImplementedError, match="sub-plan B"):
            cls(val.MAX_SMALL_U64 + 1).to_val()


def test_small_form_boundaries_signed() -> None:
    for cls in SMALL_SIGNED_TYPES:
        for x in (val.MIN_SMALL_I64, -1, 0, val.MAX_SMALL_I64):
            assert cls(x).to_val() == val.pack_small_i64(x, cls._SMALL_TAG)
            assert cls.from_val(cls(x).to_val()) == cls(x)
        with pytest.raises(NotImplementedError, match="sub-plan B"):
            cls(val.MAX_SMALL_I64 + 1).to_val()
        with pytest.raises(NotImplementedError, match="sub-plan B"):
            cls(val.MIN_SMALL_I64 - 1).to_val()


def test_from_val_is_tag_checked() -> None:
    with pytest.raises(ValueError):
        U32.from_val(val.pack_i32val(1))
    with pytest.raises(ValueError):
        I32.from_val(val.pack_u32val(1))
    with pytest.raises(ValueError):
        U64.from_val(val.pack_small_u64(1, val.TAG_TIMEPOINT_SMALL))
    with pytest.raises(ValueError):
        Timepoint.from_val(val.pack_small_u64(1, val.TAG_DURATION_SMALL))
    with pytest.raises(ValueError):
        I64.from_val(val.pack_small_i64(1, val.TAG_I128_SMALL))
    with pytest.raises(ValueError):
        Bool.from_val(val.pack_u32val(1))


def test_from_val_rejects_the_object_form_explicitly() -> None:
    for cls, object_tag in (
        (U64, val.TAG_U64_OBJECT),
        (I64, val.TAG_I64_OBJECT),
        (Timepoint, val.TAG_TIMEPOINT_OBJECT),
        (Duration, val.TAG_DURATION_OBJECT),
        (U128, val.TAG_U128_OBJECT),
        (I128, val.TAG_I128_OBJECT),
    ):
        with pytest.raises(NotImplementedError, match="sub-plan B"):
            cls.from_val(val.from_major_minor_tag(0, 0, object_tag))


def test_truthiness_is_zero_test() -> None:
    assert bool(U32(0)) is False and bool(U32(1)) is True
    assert bool(I32(-1)) is True
    assert not U32(0)
    assert bool(Timepoint(0)) is False and bool(Duration(1)) is True
