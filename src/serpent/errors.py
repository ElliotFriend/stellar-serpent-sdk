"""The ContractError hierarchy and serpent's reserved runtime error codes.

Imports `serpent.val` for error Val encoding (`val.py` itself stays
stdlib-only and imports nothing from `serpent`).

## Reserved runtime error codes

Only 2 of the 256 codes in `[RESERVED_CODE_MIN, 0xFFFF_FFFF]` were allocated
before M1-C (A9); `serpent.compiler` owns the rest of the registry (dossier
E14). `serpent.compiler.codes` is the COMPILE-TIME `SPT####` diagnostic
registry -- source-located errors a contract never ships with. The codes
below are RUNTIME error codes: they are baked into the emitted WASM (by
sub-plan D, which does not exist yet as of Task 1) and can surface from a
deployed contract's execution trace, so they are documented and pinned here
instead.

| Code                        | Value        | Emitted by (once sub-plan D lands)                                             |
|------------------------------|--------------|----------------------------------------------------------------------------------|
| `CODE_BAD_ARGUMENT`          | `0xFFFF_FFFF` | pre-existing; see `BadArgument` below.                                          |
| `CODE_ARITHMETIC_OVERFLOW`   | `0xFFFF_FFFE` | pre-existing; see `ArithmeticOverflow` below.                                    |
| `CODE_MISSING_VALUE`         | `0xFFFF_FFFD` | the storage `get(key, T)` lowering (no `default`, dossier C.4) when the host reports the key absent -- `serpent.compiler.recognize`'s storage-bucket recognition table. |
| `CODE_UNREACHABLE_GUARD`     | `0xFFFF_FFFC` | a defensive `fail_with_error` the emitter inserts after an exhaustive dispatch or a definite-return-proven function tail, so a compiler bug can never fall through to a bare `unreachable` (S17/P6) -- structurally should never fire at runtime. |
| `CODE_ABI_CHECK_FAILED`      | `0xFFFF_FFFB` | the ABI prologue's incoming-argument tag/range check (spec Sec.4) -- ONE code for every argument position; which argument failed is a message/trap-context concern, not a code concern. |
| `CODE_UNSUPPORTED_AT_RUNTIME`| `0xFFFF_FFFA` | an explicit fail-safe the emitter uses for any construct the frontend proved compiles but the (still-under-construction) emitter does not yet lower -- fails loudly with a distinct code instead of silently miscompiling. |

## A runtime trap that is NOT one of the codes above: 128-bit division by zero

Every code in the table is a `ContractError` the emitted wasm raises through
`fail_with_error` -- a deliberate, in-band signal. `//`/`%` by zero is
different, and its two widths behave differently on purpose (a ruling
carried in M1-D's Task 6 report and ledgered for this docstring):

* **32/64-bit** `//0`/`%0` lowers straight to wasm's own `i64.div_s`/
  `i64.rem_s` (etc.), so a zero divisor is a native WASM TRAP -- the guest
  instance aborts, the same class the host reports for any other trapping
  instruction. No code from this module is involved because none is raised.
* **128-bit** `//0`/`%0` has no wasm division instruction to trap: A4 routes
  it through the `{i,u}256_div` HOST function, so a zero divisor comes back
  as the HOST's own `ScError` (a trap-class host error) rather than a wasm
  trap. Conformant under A10's trap-class mapping, and implemented with no
  guest-side zero check (`serpent.emitter.arith`'s wide-division helper
  documents the same divergence at its own definition). Tier 2b (sub-plan F)
  is where this gets re-confirmed against a real host rather than the mini
  host, which is not an oracle for it (ruling E1).

Concretely: `U32(1) // U32(0)` and `I128(1) // I128(0)` both abort the guest
call, but through two different mechanisms -- neither surfaces as one of the
`CODE_*` values above, and a trace-reader should not expect one.
"""

from typing import Any, ClassVar

from serpent import val

RESERVED_CODE_MIN = 0xFFFF_FF00
CODE_BAD_ARGUMENT = 0xFFFF_FFFF
CODE_ARITHMETIC_OVERFLOW = 0xFFFF_FFFE
CODE_MISSING_VALUE = 0xFFFF_FFFD
CODE_UNREACHABLE_GUARD = 0xFFFF_FFFC
CODE_ABI_CHECK_FAILED = 0xFFFF_FFFB
CODE_UNSUPPORTED_AT_RUNTIME = 0xFFFF_FFFA

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
