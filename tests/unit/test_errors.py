import pytest

from serpent import val
from serpent.errors import (
    CODE_ARITHMETIC_OVERFLOW,
    CODE_BAD_ARGUMENT,
    RESERVED_CODE_MIN,
    ArithmeticOverflow,
    BadArgument,
    ContractError,
)


def test_error_val_golden() -> None:
    assert val.error_val(7) == 30064771075
    assert val.error_code_of(30064771075) == 7
    assert val.error_type_of(30064771075) == val.ERROR_TYPE_CONTRACT
    assert val.is_contract_error_val(30064771075)


def test_error_val_non_contract_type() -> None:
    v = val.error_val(6, error_type=2)  # Context/InvalidAction shape
    assert val.error_type_of(v) == 2 and not val.is_contract_error_val(v)


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
