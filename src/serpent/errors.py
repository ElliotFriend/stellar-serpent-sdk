"""The ContractError hierarchy and serpent's reserved runtime error codes.

Imports `serpent.val` for error Val encoding (`val.py` itself stays
stdlib-only and imports nothing from `serpent`).
"""

from typing import Any, ClassVar

from serpent import val

RESERVED_CODE_MIN = 0xFFFF_FF00
CODE_BAD_ARGUMENT = 0xFFFF_FFFF
CODE_ARITHMETIC_OVERFLOW = 0xFFFF_FFFE

_MAX_CODE = 0xFFFF_FFFF


class ContractError(Exception):
    """Abstract base for contract errors.

    `code` is declared as a `ClassVar[int]` annotation only -- no value is
    assigned here, so instantiating `ContractError` directly raises
    `TypeError`. Every concrete subclass must set a `code` in
    `[0, 2**32)`; `__init_subclass__` enforces this at class-definition
    time, not just at instantiation.
    """

    code: ClassVar[int]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        code = getattr(cls, "code", None)
        if not isinstance(code, int) or isinstance(code, bool) or not 0 <= code <= _MAX_CODE:
            raise TypeError(
                f"{cls.__name__} must define an in-range `code` (0 <= code <= {_MAX_CODE})"
            )

    def __init__(self, *args: object) -> None:
        if type(self) is ContractError:
            raise TypeError("ContractError is abstract; subclass it and set `code`")
        super().__init__(*args)

    def to_val(self) -> int:
        return val.error_val(self.code)


class ArithmeticOverflow(ContractError):
    code = CODE_ARITHMETIC_OVERFLOW


class BadArgument(ContractError):
    code = CODE_BAD_ARGUMENT
