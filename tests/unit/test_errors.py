import pytest

from serpent import val
from serpent.errors import (
    CODE_ABI_CHECK_FAILED,
    CODE_ARITHMETIC_OVERFLOW,
    CODE_BAD_ARGUMENT,
    CODE_MISSING_VALUE,
    RESERVED_CODE_MIN,
    AbiCheckFailed,
    ArithmeticOverflow,
    BadArgument,
    ContractError,
    MissingValue,
)


def test_error_val_golden() -> None:
    assert val.error_val(7) == 30064771075
    assert val.error_code_of(30064771075) == 7
    assert val.error_type_of(30064771075) == val.ERROR_TYPE_CONTRACT
    assert val.is_contract_error_val(30064771075)


def test_error_val_non_contract_type() -> None:
    v = val.error_val(6, error_type=2)  # Context/InvalidAction shape
    assert val.error_type_of(v) == 2 and not val.is_contract_error_val(v)


def test_error_val_validates_inputs() -> None:
    with pytest.raises(ValueError):
        val.error_val(0, error_type=0x1000000)  # error_type bleeds into the code field
    with pytest.raises(ValueError):
        val.error_val(-1)
    with pytest.raises(ValueError):
        val.error_val(2**32)


def test_contract_error_base_is_abstract() -> None:
    with pytest.raises(TypeError):
        ContractError("nope")


def test_contract_error_subclass_carries_code_and_val() -> None:
    class Custom(ContractError):
        code = 42

    err = Custom("boom")
    assert err.code == 42 and err.to_val() == val.error_val(42)
    with pytest.raises(Custom):
        raise Custom("boom")


def test_reserved_codes() -> None:
    assert ArithmeticOverflow().code == CODE_ARITHMETIC_OVERFLOW
    assert BadArgument().code == CODE_BAD_ARGUMENT
    assert CODE_ARITHMETIC_OVERFLOW >= RESERVED_CODE_MIN


def test_missing_value_carries_its_reserved_code() -> None:
    """Raised by Task 2's tier-1 `get` on a missing key (no `default`)."""
    err = MissingValue()
    assert isinstance(err, ContractError)
    assert err.code == CODE_MISSING_VALUE
    assert err.to_val() == val.error_val(CODE_MISSING_VALUE)


def test_abi_check_failed_carries_its_reserved_code() -> None:
    """Raised by Task 2's tier-1 `get` ty-check, and the emitter's prologue/
    narrow checks (which already emit CODE_ABI_CHECK_FAILED as a raw Val)."""
    err = AbiCheckFailed()
    assert isinstance(err, ContractError)
    assert err.code == CODE_ABI_CHECK_FAILED
    assert err.to_val() == val.error_val(CODE_ABI_CHECK_FAILED)
