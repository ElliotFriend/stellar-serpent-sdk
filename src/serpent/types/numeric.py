"""The numeric chain types: `Bool U32 I32 U64 I64 U128 I128 Timepoint Duration`.

These classes are serpent's **behavioral oracle** for on-chain integer
semantics: a contract executed as plain Python against them must observe
exactly what the compiled WASM observes on the host. Every choice below is
therefore chain-driven, not Python-driven:

* **Checked arithmetic.** `+ - * // %` and unary `-` raise
  `ArithmeticOverflow` (a `ContractError`, code `0xFFFF_FFFE`) whenever the
  mathematical result leaves the type's range -- including `-U32(1)`,
  `-I32(-2**31)` and `I32(MIN) // I32(-1)`. Nothing ever wraps and nothing
  ever silently widens: `U32(1) + U64(1)` is a `TypeError`.
* **Truncating division.** `//` truncates toward zero and `%` takes the
  *dividend's* sign, matching WASM `div_s`/`rem_s` and Rust -- NOT Python's
  floor/floormod. `I32(-7) // I32(2) == I32(-3)` and `I32(-7) % I32(2) ==
  I32(-1)`. `MIN % -1` is `0` (the host does not trap there), while
  `MIN // -1` overflows. Division by zero traps on the host, so it raises
  `ZeroDivisionError` here.
* **Exception mapping rule.** Host traps map to builtin exceptions
  (`ZeroDivisionError`); contract errors map to `ContractError` subclasses
  (`ArithmeticOverflow`); authoring-time misuse maps to `ValueError` /
  `TypeError`. **Equality is exempt: `__eq__` never raises.**
* **Val forms.** `to_val()` returns the inline/small form when the value fits
  in 56 bits and raises `NotImplementedError("host object form; sub-plan B")`
  otherwise; `from_val` is tag-checked. `serpent.val` owns all bit math.

`**`, `divmod` and the bitwise operators are deliberately omitted (they raise
`TypeError` naming the omission) until a real contract needs them.
"""

from typing import ClassVar, Never, NoReturn, Self

from serpent import val
from serpent.errors import ArithmeticOverflow

_OMITTED = (
    "serpent chain integers deliberately omit **, divmod() and the bitwise "
    "operators; revisit when a contract needs them"
)


def _omitted(op: str, owner: object) -> NoReturn:
    raise TypeError(f"{op} is not supported on {type(owner).__name__}: {_OMITTED}")


def _trunc_div(a: int, b: int) -> int:
    """WASM `div_s`/`div_u`: quotient truncated toward zero (never floored)."""
    if b == 0:
        raise ZeroDivisionError("integer division by zero")
    quotient = abs(a) // abs(b)
    return -quotient if (a < 0) != (b < 0) else quotient


def _trunc_rem(a: int, b: int) -> int:
    """WASM `rem_s`/`rem_u`: remainder takes the dividend's sign."""
    if b == 0:
        raise ZeroDivisionError("integer modulo by zero")
    remainder = abs(a) % abs(b)
    return -remainder if a < 0 else remainder


class _ChainScalar:
    """Shared, immutable carrier for the scalar chain types in this module.

    Holds the ordering hooks that `serpent.types._ordering.val_cmp` reads off
    instances: `_SCVAL_RANK` (the host's `ScValType` order, used across types)
    and `_cmp_payload()` (used within a type).
    """

    __slots__ = ("_value",)

    _SCVAL_RANK: ClassVar[int]
    _value: int

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def _cmp_payload(self) -> object:
        return self._value


class _ChainInt(_ChainScalar):
    """Base for every fixed-width chain integer.

    Subclasses declare `MIN`/`MAX`, `_SCVAL_RANK` and their Val tags; all
    semantics live here so that there is exactly one definition of the
    checked-arithmetic and comparison contracts.
    """

    __slots__ = ()

    MIN: ClassVar[int]
    MAX: ClassVar[int]
    #: Tag of the 56-bit small form and of the host object form. Unused by the
    #: types whose Val form is always inline (`U32`/`I32` override to_val/from_val).
    _SMALL_TAG: ClassVar[int]
    _OBJECT_TAG: ClassVar[int]

    def __init__(self, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError(
                f"{type(self).__name__}() takes an int, not {type(value).__name__}"
            )
        v = int(value)  # normalise bool -> int; `value` is always a true int
        if not self.MIN <= v <= self.MAX:
            raise ValueError(
                f"{v} is out of range for {type(self).__name__} "
                f"[{self.MIN}, {self.MAX}]"
            )
        object.__setattr__(self, "_value", v)

    @property
    def value(self) -> int:
        return self._value

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._value})"

    # --- operand handling ----------------------------------------------------

    def _operand(self, other: object) -> int | None:
        """Coerce an arithmetic operand, or return None to defer (NotImplemented).

        Same chain type -> its value. Foreign chain type -> `TypeError` (no
        implicit widening or narrowing). In-range `int` -> itself. Out-of-range
        `int` -> `ValueError` (an authoring bug: that literal cannot exist as a
        value of this type on-chain).
        """
        if isinstance(other, _ChainInt):
            if type(other) is not type(self):
                raise TypeError(
                    f"no implicit conversion between chain types: "
                    f"{type(self).__name__} and {type(other).__name__}; convert explicitly"
                )
            return other._value
        if isinstance(other, _ChainScalar):
            raise TypeError(
                f"no implicit conversion between chain types: "
                f"{type(self).__name__} and {type(other).__name__}; convert explicitly"
            )
        if isinstance(other, int):
            v = int(other)
            if not self.MIN <= v <= self.MAX:
                raise ValueError(
                    f"{v} is out of range for {type(self).__name__} "
                    f"[{self.MIN}, {self.MAX}]"
                )
            return v
        return None

    def _cmp_operand(self, other: object) -> int | None:
        """Coerce an ordering operand, or return None to defer (NotImplemented).

        Unlike `_operand`, out-of-range ints are fine: ordering is answered
        mathematically (`U32(5) < 2**40` is `True`).
        """
        if isinstance(other, _ChainInt):
            if type(other) is not type(self):
                raise TypeError(
                    f"cannot order {type(self).__name__} against {type(other).__name__}"
                )
            return other._value
        if isinstance(other, _ChainScalar):
            raise TypeError(
                f"cannot order {type(self).__name__} against {type(other).__name__}"
            )
        if isinstance(other, int):
            return int(other)
        return None

    def _wrap(self, result: int) -> Self:
        if not self.MIN <= result <= self.MAX:
            raise ArithmeticOverflow(
                f"{type(self).__name__} arithmetic overflowed: {result} is outside "
                f"[{self.MIN}, {self.MAX}]"
            )
        return type(self)(result)

    # --- checked arithmetic --------------------------------------------------

    def __add__(self, other: Self | int) -> Self:
        o = self._operand(other)
        if o is None:
            return NotImplemented
        return self._wrap(self._value + o)

    def __radd__(self, other: int) -> Self:
        o = self._operand(other)
        if o is None:
            return NotImplemented
        return self._wrap(o + self._value)

    def __sub__(self, other: Self | int) -> Self:
        o = self._operand(other)
        if o is None:
            return NotImplemented
        return self._wrap(self._value - o)

    def __rsub__(self, other: int) -> Self:
        o = self._operand(other)
        if o is None:
            return NotImplemented
        return self._wrap(o - self._value)

    def __mul__(self, other: Self | int) -> Self:
        o = self._operand(other)
        if o is None:
            return NotImplemented
        return self._wrap(self._value * o)

    def __rmul__(self, other: int) -> Self:
        o = self._operand(other)
        if o is None:
            return NotImplemented
        return self._wrap(o * self._value)

    def __floordiv__(self, other: Self | int) -> Self:
        o = self._operand(other)
        if o is None:
            return NotImplemented
        return self._wrap(_trunc_div(self._value, o))

    def __rfloordiv__(self, other: int) -> Self:
        o = self._operand(other)
        if o is None:
            return NotImplemented
        return self._wrap(_trunc_div(o, self._value))

    def __mod__(self, other: Self | int) -> Self:
        o = self._operand(other)
        if o is None:
            return NotImplemented
        return self._wrap(_trunc_rem(self._value, o))

    def __rmod__(self, other: int) -> Self:
        o = self._operand(other)
        if o is None:
            return NotImplemented
        return self._wrap(_trunc_rem(o, self._value))

    def __neg__(self) -> Self:
        return self._wrap(-self._value)

    # --- deliberately omitted operators --------------------------------------

    def __pow__(self, other: Never) -> Never:
        _omitted("**", self)

    def __rpow__(self, other: Never) -> Never:
        _omitted("**", self)

    def __divmod__(self, other: Never) -> Never:
        _omitted("divmod()", self)

    def __rdivmod__(self, other: Never) -> Never:
        _omitted("divmod()", self)

    def __and__(self, other: Never) -> Never:
        _omitted("&", self)

    def __rand__(self, other: Never) -> Never:
        _omitted("&", self)

    def __or__(self, other: Never) -> Never:
        _omitted("|", self)

    def __ror__(self, other: Never) -> Never:
        _omitted("|", self)

    def __xor__(self, other: Never) -> Never:
        _omitted("^", self)

    def __rxor__(self, other: Never) -> Never:
        _omitted("^", self)

    def __lshift__(self, other: Never) -> Never:
        _omitted("<<", self)

    def __rlshift__(self, other: Never) -> Never:
        _omitted("<<", self)

    def __rshift__(self, other: Never) -> Never:
        _omitted(">>", self)

    def __rrshift__(self, other: Never) -> Never:
        _omitted(">>", self)

    def __invert__(self) -> Never:
        _omitted("~", self)

    # --- comparison ----------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        """Never raises: foreign chain types, non-chain objects and out-of-range
        ints are simply unequal."""
        if isinstance(other, _ChainInt):
            return type(other) is type(self) and self._value == other._value
        if isinstance(other, int):
            return self.MIN <= other <= self.MAX and self._value == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    def __lt__(self, other: Self | int) -> bool:
        o = self._cmp_operand(other)
        if o is None:
            return NotImplemented
        return self._value < o

    def __le__(self, other: Self | int) -> bool:
        o = self._cmp_operand(other)
        if o is None:
            return NotImplemented
        return self._value <= o

    def __gt__(self, other: Self | int) -> bool:
        o = self._cmp_operand(other)
        if o is None:
            return NotImplemented
        return self._value > o

    def __ge__(self, other: Self | int) -> bool:
        o = self._cmp_operand(other)
        if o is None:
            return NotImplemented
        return self._value >= o

    # --- Val forms -----------------------------------------------------------

    def to_val(self) -> int:
        """The 56-bit small form, or `NotImplementedError` for wider values.

        Signedness follows the type: signed types use the signed small bound
        (`MIN_SMALL_I64 <= v <= MAX_SMALL_I64`), unsigned types the unsigned one
        (`0 <= v <= MAX_SMALL_U64`) -- including the 128-bit types.
        """
        v = self._value
        if self.MIN < 0:
            if val.fits_small_i(v):
                return val.pack_small_i64(v, self._SMALL_TAG)
        elif val.fits_small_u(v):
            return val.pack_small_u64(v, self._SMALL_TAG)
        raise NotImplementedError("host object form; sub-plan B")

    @classmethod
    def from_val(cls, v: int) -> Self:
        """Decode a small-form Val of exactly this type's tag.

        Wrong tag -> `ValueError` (raised by the codec, naming both tags); this
        type's *object* tag -> `NotImplementedError`, mirroring `to_val`.
        """
        if val.tag_of(v) == cls._OBJECT_TAG:
            raise NotImplementedError("host object form; sub-plan B")
        if cls.MIN < 0:
            return cls(val.unpack_small_i64(v, cls._SMALL_TAG))
        return cls(val.unpack_small_u64(v, cls._SMALL_TAG))


class Bool(_ChainScalar):
    """The chain `bool`. Not a `_ChainInt`: the host offers no arithmetic on it."""

    __slots__ = ()

    _SCVAL_RANK: ClassVar[int] = 0

    def __init__(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise TypeError(f"Bool() takes a bool, not {type(value).__name__}")
        object.__setattr__(self, "_value", value)

    @property
    def value(self) -> bool:
        return bool(self._value)

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return f"Bool({bool(self._value)})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Bool):
            return self._value == other._value
        if isinstance(other, bool):
            return self._value == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    def __lt__(self, other: "Bool") -> bool:
        if not isinstance(other, Bool):
            return NotImplemented
        return self._value < other._value

    def __le__(self, other: "Bool") -> bool:
        if not isinstance(other, Bool):
            return NotImplemented
        return self._value <= other._value

    def __gt__(self, other: "Bool") -> bool:
        if not isinstance(other, Bool):
            return NotImplemented
        return self._value > other._value

    def __ge__(self, other: "Bool") -> bool:
        if not isinstance(other, Bool):
            return NotImplemented
        return self._value >= other._value

    def _cmp_payload(self) -> object:
        return bool(self._value)

    def to_val(self) -> int:
        return val.TRUE_VAL if self._value else val.FALSE_VAL

    @classmethod
    def from_val(cls, v: int) -> Self:
        return cls(val.unpack_bool(v))


class U32(_ChainInt):
    """32-bit unsigned. Always has an inline Val form (`U32Val`)."""

    __slots__ = ()

    MIN: ClassVar[int] = 0
    MAX: ClassVar[int] = 2**32 - 1
    _SCVAL_RANK: ClassVar[int] = 3

    def to_val(self) -> int:
        return val.pack_u32val(self._value)

    @classmethod
    def from_val(cls, v: int) -> Self:
        return cls(val.unpack_u32val(v))


class I32(_ChainInt):
    """32-bit signed. Always has an inline Val form (`I32Val`)."""

    __slots__ = ()

    MIN: ClassVar[int] = -(2**31)
    MAX: ClassVar[int] = 2**31 - 1
    _SCVAL_RANK: ClassVar[int] = 4

    def to_val(self) -> int:
        return val.pack_i32val(self._value)

    @classmethod
    def from_val(cls, v: int) -> Self:
        return cls(val.unpack_i32val(v))


class U64(_ChainInt):
    """64-bit unsigned."""

    __slots__ = ()

    MIN: ClassVar[int] = 0
    MAX: ClassVar[int] = 2**64 - 1
    _SCVAL_RANK: ClassVar[int] = 5
    _SMALL_TAG: ClassVar[int] = val.TAG_U64_SMALL
    _OBJECT_TAG: ClassVar[int] = val.TAG_U64_OBJECT


class I64(_ChainInt):
    """64-bit signed."""

    __slots__ = ()

    MIN: ClassVar[int] = -(2**63)
    MAX: ClassVar[int] = 2**63 - 1
    _SCVAL_RANK: ClassVar[int] = 6
    _SMALL_TAG: ClassVar[int] = val.TAG_I64_SMALL
    _OBJECT_TAG: ClassVar[int] = val.TAG_I64_OBJECT


class _TimeValue(_ChainInt):
    """Shared base for `Timepoint` and `Duration` (both u64-ranged).

    **Deferred:** cross-type time arithmetic (`Timepoint - Timepoint ->
    Duration`, `Timepoint + Duration -> Timepoint`) is NOT provided. The host
    models both as bare u64 and performs no unit algebra, so serpent will not
    invent one that the compiler tier cannot enforce; mixing them raises
    `TypeError`. Bridge explicitly through `to_u64()` / `from_u64()` (sub-plan E
    needs this for `env.ledger().timestamp()`).
    """

    __slots__ = ()

    MIN: ClassVar[int] = 0
    MAX: ClassVar[int] = 2**64 - 1

    @classmethod
    def from_u64(cls, u: U64) -> Self:
        return cls(u.value)

    def to_u64(self) -> U64:
        return U64(self._value)


class Timepoint(_TimeValue):
    """A u64 point in time (seconds since the Unix epoch, per the host)."""

    __slots__ = ()

    _SCVAL_RANK: ClassVar[int] = 7
    _SMALL_TAG: ClassVar[int] = val.TAG_TIMEPOINT_SMALL
    _OBJECT_TAG: ClassVar[int] = val.TAG_TIMEPOINT_OBJECT


class Duration(_TimeValue):
    """A u64 span of time (seconds, per the host)."""

    __slots__ = ()

    _SCVAL_RANK: ClassVar[int] = 8
    _SMALL_TAG: ClassVar[int] = val.TAG_DURATION_SMALL
    _OBJECT_TAG: ClassVar[int] = val.TAG_DURATION_OBJECT


class U128(_ChainInt):
    """128-bit unsigned, with the host's limb convention."""

    __slots__ = ()

    MIN: ClassVar[int] = 0
    MAX: ClassVar[int] = 2**128 - 1
    _SCVAL_RANK: ClassVar[int] = 9
    _SMALL_TAG: ClassVar[int] = val.TAG_U128_SMALL
    _OBJECT_TAG: ClassVar[int] = val.TAG_U128_OBJECT

    @property
    def hi64(self) -> int:
        """High limb, unsigned -- the `hi` argument of `obj_from_u128_pieces`."""
        return (self._value >> 64) & val.MASK64

    @property
    def lo64(self) -> int:
        """Low limb, unsigned -- the `lo` argument of `obj_from_u128_pieces`."""
        return self._value & val.MASK64


class I128(_ChainInt):
    """128-bit signed, with the host's limb convention."""

    __slots__ = ()

    MIN: ClassVar[int] = -(2**127)
    MAX: ClassVar[int] = 2**127 - 1
    _SCVAL_RANK: ClassVar[int] = 10
    _SMALL_TAG: ClassVar[int] = val.TAG_I128_SMALL
    _OBJECT_TAG: ClassVar[int] = val.TAG_I128_OBJECT

    @property
    def hi64(self) -> int:
        """High limb, **signed** -- the `hi: i64` of `obj_from_i128_pieces(hi, lo)`.

        `I128(-1).hi64 == -1`; `I128(-(2**64)).hi64 == -1`.
        """
        return val.as_i64(self._value >> 64)

    @property
    def lo64(self) -> int:
        """Low limb, **unsigned** -- the `lo: u64` of `obj_from_i128_pieces(hi, lo)`.

        `I128(-1).lo64 == 2**64 - 1`; `I128(-(2**64)).lo64 == 0`.
        """
        return self._value & val.MASK64
