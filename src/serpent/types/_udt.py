"""Tagged unions and int enums: the tier-1 VALUE layer (rulings E1, E6, E9).

**The three shapes, byte-verified** against real Rust builds on soroban-sdk
22.0.11 and 27.0.6 (identical output), which is why they are stated as facts
here rather than as choices:

* a UNIT variant's value is a **one-element `ScVec`** holding the variant-name
  `Symbol` -- not a bare `Symbol`;
* a TUPLE variant is `ScVec[Symbol, payload...]`, the payload in DECLARATION
  order;
* an INT ENUM value is a **bare `U32`** -- no wrapper, no name.

Everything else in this module follows from those three. A `ContractUnion`
instance holds the `ScVec` it IS (`Vec`, built once at construction) and
delegates its storage key to it, so there is one definition of the vec shape;
`ContractUnion._SCVAL_RANK` is `Vec`'s and `ContractEnum._SCVAL_RANK` is
`U32`'s, so both order and key exactly like the value they are on chain.

**Neither kind is a dataclass, and that is load-bearing (ruling E9).**
`types._ordering.Struct` is a `runtime_checkable` Protocol matching
`__dataclass_fields__`, and it is the FALLTHROUGH arm in
`env.tag_of_chain_value`, `env._families_of_ty` and `_storage_key.storage_key`.
A dataclass union would therefore be classified silently as a `Map`: wrong tag
family, wrong storage key, wrong ABI tag, and no error anywhere. Both bases are
plain slotted classes with a rejecting `__setattr__` instead -- and both are
matched by `env._FAMILY_BY_TYPE` before the `Struct` arm is ever reached.

**Ordering (ruling D10).** An int enum is fully ordered: its `_cmp_payload()`
is the discriminant, so `val_cmp` compares it against another member or a bare
`U32` exactly as it compares two `U32`s. A union is NOT: `_cmp_payload()`
raises `Vec`'s own deferred `NotImplementedError` (the string is imported from
`containers`, not restated), so a union is a hashable storage key and a
single-entry `Map` key, and a SECOND `Map` entry keyed by one raises. That is
"not modelled in tier 1", not an invented order.

**The authoring surface (ruling E1).** A case is declared with
`variant()` / `variant(U32)` / `variant(U32, U32)` ... up to
`MAX_PAYLOAD_ARITY` payload values -- S4's tuple arity, so serpent has ONE
arity story (E6). `variant()` returns a `_VariantSpec` PLACEHOLDER, exactly as
`errorcode(N)` returns an `_ErrorCode`, and `@contractunion` swaps each for the
bound descriptor that knows its case NAME (the attribute name, which the
factory cannot see). The descriptors are what make `mypy --strict` catch the
author's mistakes with no plugin: `Shape.Circle` types as `(U32) -> Shape`, so
a wrong payload type, a wrong arity, calling a unit variant, or using one kind
where another is declared are all static errors
(`tests/unit/test_authoring_types.py` pins the five; `tests/fixtures/
udt_style.py` is the clean positive half).

An int-enum member needs no such rebinding: `enumvalue(n)` already carries the
discriminant, and a descriptor's `__get__` is handed the owner class, so
`_EnumValue` is both the placeholder and the finished descriptor.

**`payload()` and the deferred import.** `payload(index, ty)` re-uses
`env._require_ty` -- the same function the model's `storage().get()` tag-checks
with, so a payload read and a storage read fail identically (`AbiCheckFailed`,
`CODE_ABI_CHECK_FAILED`). The import is DEFERRED into the function body and
must stay there: `serpent.env` imports `serpent.types`, so a module-level
`from serpent.env import _require_ty` here is a hard cycle. It is still an
`import serpent.env`, so `tests/unit/test_core_zero_dep.py`'s whole-AST walk
still sees root `serpent` and the zero-dep boundary is unaffected.
"""

import copy as _copy
from collections.abc import Callable
from typing import Any, ClassVar, Generic, NoReturn, Self, TypeVar, cast, get_origin, overload

from serpent.types._ordering import ChainValue, require_map_value
from serpent.types.containers import _DEFERRED, Vec, _value_element_type_for
from serpent.types.numeric import U32
from serpent.types.symbol import Symbol

__all__ = ["ContractEnum", "ContractUnion", "enumvalue", "variant"]

#: The widest payload a variant may carry: S4's tuple arity, deliberately the
#: same number, so a union payload and a tuple return cannot disagree about how
#: wide a shape may be (ruling E6). The compile-time refusal is `SPT5006`, in
#: the SPT5xxx limits band, and it is THIS raise re-reported located: a 13th
#: payload is refused in the class body, before `@contractunion` can run, so
#: the decorator never sees an over-wide payload and `compiler/limits.py` has
#: no arity check of its own to make.
MAX_PAYLOAD_ARITY = 12

#: The owner class an access goes through. METHOD-scoped on every `__get__`
#: below, never a class parameter: it is what types `Shape.Empty` as `Shape`
#: and `Shape.Circle` as `(U32) -> Shape` without either descriptor having to
#: name a class its own declaration site cannot name.
_O = TypeVar("_O")

#: The type a `payload()` read is decoded as -- `get`'s `ty` argument, one
#: payload slot at a time.
_T = TypeVar("_T")

#: A variant's declared payload types, one TypeVar per slot up to the arity
#: cap. One descriptor class per arity is what makes `Shape.Rect` type as
#: `(U32, U32) -> Shape`; a single non-generic descriptor would leave every
#: payload unchecked.
_P1 = TypeVar("_P1")
_P2 = TypeVar("_P2")
_P3 = TypeVar("_P3")
_P4 = TypeVar("_P4")
_P5 = TypeVar("_P5")
_P6 = TypeVar("_P6")
_P7 = TypeVar("_P7")
_P8 = TypeVar("_P8")
_P9 = TypeVar("_P9")
_P10 = TypeVar("_P10")
_P11 = TypeVar("_P11")
_P12 = TypeVar("_P12")

#: `_EnumValue`'s class parameter: the owner as SPELLED at a declaration site,
#: which is always `Any` (a class body cannot name the class it is declaring).
_D = TypeVar("_D")


# --- the two bases -----------------------------------------------------------


class ContractUnion:
    """Base of every `@contractunion` type: one case, with its payload.

    An instance holds the `ScVec` it is on chain -- `[Symbol(case), *payload]`
    -- and nothing else. It is immutable: the `Vec` is built once, never handed
    out, and every attribute rebinding is refused, which is what makes the
    value hashable (and therefore a storage key) safely.

    Not a dataclass, deliberately: see the module docstring's E9 paragraph.
    """

    __slots__ = ("_vec",)

    #: `Vec`'s own `ScValType` rank: a union IS an `ScVec` on chain.
    _SCVAL_RANK: ClassVar[int] = 16

    _vec: Vec[Any]

    # --- construction --------------------------------------------------------

    @classmethod
    def _construct(cls, case: str, payload: tuple[Any, ...]) -> Self:
        """Build the case `case` with `payload`, in declaration order.

        Private: an author constructs through the descriptor
        (`Shape.Circle(U32(1))`), which is the spelling `mypy --strict` checks
        the payload types of. What is checked HERE is chain-ness, which is the
        same invariant `Vec`/`Map` enforce on the way in
        (`require_map_value`): a payload may be any chain value or a
        `@contracttype` struct, and a raw `int`/`str`/`None` is refused before
        it can produce a garbage storage key.

        The element type is the widening ladder `Map.values()` already uses,
        because the elements are heterogeneous by construction (a `Symbol`
        followed by the payload) and a `Vec` must never claim an element type
        its own contents fail.
        """
        items: list[Any] = [Symbol(case)]
        for value in payload:
            require_map_value(value)
            items.append(value)
        instance = object.__new__(cls)
        object.__setattr__(instance, "_vec", Vec(_value_element_type_for(ChainValue, items), items))
        return instance

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError(f"{type(self).__name__} is immutable")

    # --- reads ---------------------------------------------------------------

    def tag(self) -> Symbol:
        """This value's case name -- element 0 of the on-chain `ScVec`."""
        return cast("Symbol", self._vec.get(0))

    def payload(self, index: U32, ty: type[_T]) -> _T:
        """Payload slot `index`, decoded as `ty`.

        `index` is **0-based over the PAYLOAD**: slot 0 is the element after
        the name `Symbol`, so `Rect(a, b).payload(U32(0), U32)` is `a`. An
        index past the payload raises `IndexError`, like every other `Vec`
        read; the wrong `ty` raises `AbiCheckFailed`, exactly where a storage
        `get` of the wrong type would (`env._require_ty`, called for its raise
        -- it returns `None`).

        The held element comes back WITHOUT a copy, which is `Vec.get`'s own
        rule; the union is immutable, so the only way to observe that is
        through a mutable payload.
        """
        from serpent.env import _require_ty  # deferred: see the module docstring

        value = self._vec.get(index.value + 1)
        _require_ty(value, ty)
        return cast("_T", value)

    # --- value semantics -----------------------------------------------------

    def _cmp_payload(self) -> object:
        """Deferred, with `Vec`'s own message (D10): a union is a hashable
        storage key at tier 1, and comparing two of them needs the nested
        container semantics sub-plan B verifies."""
        raise NotImplementedError(_DEFERRED)

    def _payload_items(self) -> tuple[Any, ...]:
        """The payload, without the leading name `Symbol`."""
        return tuple(self._vec)[1:]

    def __eq__(self, other: object) -> bool:
        """Over `(case, payload)`, and never raises.

        Two unions of DIFFERENT declared types with the same case name and
        payload are equal, because on chain they are the same `ScVec` -- the
        same reasoning `Vec.__eq__` gives for ignoring element types.
        """
        if isinstance(other, ContractUnion):
            other_vec: Vec[Any] = other._vec
            return self._vec == other_vec
        return NotImplemented

    def __hash__(self) -> int:
        """Over the same `(case, payload)` `__eq__` reads.

        A payload that is itself a container is unhashable, and this raises for
        it: `storage_key` is the way to key on such a value, and it is what
        `env`'s store uses.
        """
        return hash(tuple(self._vec))

    def __repr__(self) -> str:
        payload = self._payload_items()
        if not payload:
            return f"{type(self).__name__}.{self.tag().text}"
        return f"{type(self).__name__}.{self.tag().text}({', '.join(repr(v) for v in payload)})"

    def __copy__(self) -> Self:
        """A new instance over the same elements.

        Spelled out because a slotted class with a rejecting `__setattr__`
        cannot go through the default copy protocol at all -- and `env.py`'s
        deep-copy law runs `copy.deepcopy` over every stored value.
        """
        return type(self)._construct(self.tag().text, self._payload_items())

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        return type(self)._construct(
            self.tag().text,
            tuple(_copy.deepcopy(value, memo) for value in self._payload_items()),
        )


class ContractEnum:
    """Base of every `@contractenum` type: one `u32` discriminant.

    The value IS a bare `U32` on chain, so this carries `U32`'s rank and its
    discriminant as the comparison payload: `val_cmp`, `storage_key` and
    hashing all answer exactly what they answer for the equivalent `U32`.

    There is no `.value` (ruling E5's sub-ruling): M1 models no way to read a
    discriminant back out of a member, on chain or off, so exposing one at
    tier 1 would be a tier-1-only surface. Not a dataclass, for E9's reason.
    """

    __slots__ = ("_discriminant",)

    #: `U32`'s own `ScValType` rank: the value IS a bare `U32` on chain.
    _SCVAL_RANK: ClassVar[int] = 3

    _discriminant: int

    @classmethod
    def _construct(cls, discriminant: int) -> Self:
        instance = object.__new__(cls)
        object.__setattr__(instance, "_discriminant", discriminant)
        return instance

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def _cmp_payload(self) -> object:
        return self._discriminant

    def __eq__(self, other: object) -> bool:
        """Over the discriminant, and never raises.

        Two members of different enums with the same discriminant are equal:
        both are the same bare `U32` on chain. A `U32` itself is NOT equal to a
        member, though -- `Symbol`'s no-coercion rule, which keeps the chain
        type visible exactly where the compiler needs to see it.
        """
        if isinstance(other, ContractEnum):
            return self._discriminant == other._discriminant
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._discriminant)

    def __repr__(self) -> str:
        """`Color.Red` -- the member NAME, found by matching the discriminant
        against the class's own `enumvalue` declarations (there is no `.value`
        to print, and a bare number would be the less useful half anyway)."""
        for name, member in vars(type(self)).items():
            if isinstance(member, _EnumValue) and member._discriminant == self._discriminant:
                return f"{type(self).__name__}.{name}"
        return f"<{type(self).__name__} discriminant {self._discriminant}>"  # pragma: no cover

    def __copy__(self) -> Self:
        """Itself: an int-payload value with nothing mutable inside it, so a
        copy would be the same value (`copy.copy(1) is 1`). Spelled out for
        `__setattr__`'s reason -- the default protocol cannot rebuild it."""
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        return self


# --- the descriptors ---------------------------------------------------------


class _BoundCase:
    """The state a BOUND variant descriptor carries: its case name, and the
    union class the case was declared in.

    One base for all thirteen arities (`_Unit` and `_Variant1` ..
    `_Variant12`), which differ only in what `__get__` says statically. The
    instance is built through the DECLARING class, so a case always constructs
    the union it was declared in.
    """

    __slots__ = ("_case", "_owner")

    def __init__(self, case: str, owner: type[ContractUnion]) -> None:
        self._case = case
        self._owner = owner

    def _make(self, *payload: Any) -> ContractUnion:
        return self._owner._construct(self._case, payload)

    def __repr__(self) -> str:
        return f"{self._owner.__name__}.{self._case}"


class _Unit(_BoundCase):
    """A unit variant. `__get__` types `Shape.Empty` as `Shape` itself.

    NOT generic -- `_O` is bound per-call by `__get__` (there is nothing a
    class parameter could be filled with at a declaration site). Each access
    builds a fresh, equal value; a union has no identity on chain.
    """

    __slots__ = ()

    def __get__(self, obj: object | None, owner: type[_O]) -> _O:
        return cast("_O", self._make())


class _Variant1(_BoundCase, Generic[_P1]):
    """A one-payload variant: `Shape.Circle` types as `(U32) -> Shape`.

    `Generic[_P1]` is load-bearing: a non-generic descriptor would leave
    every payload type silently unchecked, which is the whole reason
    ruling E1 chose this surface. `_Variant2` .. `_Variant12` are the same
    class one payload wider, to `MAX_PAYLOAD_ARITY`.
    """

    __slots__ = ()

    def __get__(self, obj: object | None, owner: type[_O]) -> Callable[[_P1], _O]:
        return cast("Callable[[_P1], _O]", self._make)


class _Variant2(_BoundCase, Generic[_P1, _P2]):
    __slots__ = ()

    def __get__(self, obj: object | None, owner: type[_O]) -> Callable[[_P1, _P2], _O]:
        return cast("Callable[[_P1, _P2], _O]", self._make)


class _Variant3(_BoundCase, Generic[_P1, _P2, _P3]):
    __slots__ = ()

    def __get__(self, obj: object | None, owner: type[_O]) -> Callable[[_P1, _P2, _P3], _O]:
        return cast("Callable[[_P1, _P2, _P3], _O]", self._make)


class _Variant4(_BoundCase, Generic[_P1, _P2, _P3, _P4]):
    __slots__ = ()

    def __get__(self, obj: object | None, owner: type[_O]) -> Callable[[_P1, _P2, _P3, _P4], _O]:
        return cast("Callable[[_P1, _P2, _P3, _P4], _O]", self._make)


class _Variant5(_BoundCase, Generic[_P1, _P2, _P3, _P4, _P5]):
    __slots__ = ()

    def __get__(
        self, obj: object | None, owner: type[_O]
    ) -> Callable[[_P1, _P2, _P3, _P4, _P5], _O]:
        return cast("Callable[[_P1, _P2, _P3, _P4, _P5], _O]", self._make)


class _Variant6(_BoundCase, Generic[_P1, _P2, _P3, _P4, _P5, _P6]):
    __slots__ = ()

    def __get__(
        self, obj: object | None, owner: type[_O]
    ) -> Callable[[_P1, _P2, _P3, _P4, _P5, _P6], _O]:
        return cast("Callable[[_P1, _P2, _P3, _P4, _P5, _P6], _O]", self._make)


class _Variant7(_BoundCase, Generic[_P1, _P2, _P3, _P4, _P5, _P6, _P7]):
    __slots__ = ()

    def __get__(
        self, obj: object | None, owner: type[_O]
    ) -> Callable[[_P1, _P2, _P3, _P4, _P5, _P6, _P7], _O]:
        return cast("Callable[[_P1, _P2, _P3, _P4, _P5, _P6, _P7], _O]", self._make)


class _Variant8(_BoundCase, Generic[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8]):
    __slots__ = ()

    def __get__(
        self, obj: object | None, owner: type[_O]
    ) -> Callable[[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8], _O]:
        return cast("Callable[[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8], _O]", self._make)


class _Variant9(_BoundCase, Generic[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9]):
    __slots__ = ()

    def __get__(
        self, obj: object | None, owner: type[_O]
    ) -> Callable[[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9], _O]:
        return cast("Callable[[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9], _O]", self._make)


class _Variant10(_BoundCase, Generic[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9, _P10]):
    __slots__ = ()

    def __get__(
        self, obj: object | None, owner: type[_O]
    ) -> Callable[[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9, _P10], _O]:
        return cast("Callable[[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9, _P10], _O]", self._make)


class _Variant11(_BoundCase, Generic[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9, _P10, _P11]):
    __slots__ = ()

    def __get__(
        self, obj: object | None, owner: type[_O]
    ) -> Callable[[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9, _P10, _P11], _O]:
        return cast(
            "Callable[[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9, _P10, _P11], _O]", self._make
        )


class _Variant12(
    _BoundCase, Generic[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9, _P10, _P11, _P12]
):
    __slots__ = ()

    def __get__(
        self, obj: object | None, owner: type[_O]
    ) -> Callable[[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9, _P10, _P11, _P12], _O]:
        return cast(
            "Callable[[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9, _P10, _P11, _P12], _O]",
            self._make,
        )


#: The bound descriptor class for each payload arity, indexed by it.
_VARIANT_CLASSES: tuple[type[_BoundCase], ...] = (
    _Unit,
    _Variant1,
    _Variant2,
    _Variant3,
    _Variant4,
    _Variant5,
    _Variant6,
    _Variant7,
    _Variant8,
    _Variant9,
    _Variant10,
    _Variant11,
    _Variant12,
)


class _VariantSpec:
    """What `variant(...)` really returns: a placeholder carrying the declared
    payload annotations.

    `@contractunion` replaces every one of these with a bound descriptor
    (`_bind_variant`), exactly as `@contracterror` replaces `errorcode(N)`'s
    `_ErrorCode`. The placeholder exists only between the class body executing
    and the decorator running, so an author never sees one -- and `payload` is
    public (no underscore, like `_ErrorCode.code`) because the declaration
    layer reads the annotations back off it to build the spec entry.
    """

    __slots__ = ("payload",)

    def __init__(self, payload: tuple[object, ...]) -> None:
        self.payload = payload

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        names = ", ".join(getattr(entry, "__name__", repr(entry)) for entry in self.payload)
        return f"variant({names})"


def _bind_variant(case: str, owner: type[ContractUnion], spec: _VariantSpec) -> _BoundCase:
    """The bound descriptor that replaces one `variant()` placeholder.

    The case NAME is the attribute name, which only the declaration layer can
    see, and the arity is the placeholder's -- so this is the whole of the
    swap `@contractunion` performs.
    """
    return _VARIANT_CLASSES[len(spec.payload)](case, owner)


def _is_payload_type(entry: object) -> bool:
    """Whether `entry` is a payload TYPE rather than a value.

    A parameterized container (`Vec[U32]`) is not a `type` at runtime -- and it
    is the only `mypy --strict`-clean way to spell a container payload -- so a
    generic alias counts, which is the same test `env._families_of_ty` makes of
    a requested `ty`.
    """
    return isinstance(entry, type) or get_origin(entry) is not None


@overload
def variant() -> _Unit: ...
@overload
def variant(p1: type[_P1], /) -> _Variant1[_P1]: ...
@overload
def variant(p1: type[_P1], p2: type[_P2], /) -> _Variant2[_P1, _P2]: ...
@overload
def variant(p1: type[_P1], p2: type[_P2], p3: type[_P3], /) -> _Variant3[_P1, _P2, _P3]: ...
@overload
def variant(
    p1: type[_P1], p2: type[_P2], p3: type[_P3], p4: type[_P4], /
) -> _Variant4[_P1, _P2, _P3, _P4]: ...
@overload
def variant(
    p1: type[_P1], p2: type[_P2], p3: type[_P3], p4: type[_P4], p5: type[_P5], /
) -> _Variant5[_P1, _P2, _P3, _P4, _P5]: ...
@overload
def variant(
    p1: type[_P1], p2: type[_P2], p3: type[_P3], p4: type[_P4], p5: type[_P5], p6: type[_P6], /
) -> _Variant6[_P1, _P2, _P3, _P4, _P5, _P6]: ...
@overload
def variant(
    p1: type[_P1],
    p2: type[_P2],
    p3: type[_P3],
    p4: type[_P4],
    p5: type[_P5],
    p6: type[_P6],
    p7: type[_P7],
    /,
) -> _Variant7[_P1, _P2, _P3, _P4, _P5, _P6, _P7]: ...
@overload
def variant(
    p1: type[_P1],
    p2: type[_P2],
    p3: type[_P3],
    p4: type[_P4],
    p5: type[_P5],
    p6: type[_P6],
    p7: type[_P7],
    p8: type[_P8],
    /,
) -> _Variant8[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8]: ...
@overload
def variant(
    p1: type[_P1],
    p2: type[_P2],
    p3: type[_P3],
    p4: type[_P4],
    p5: type[_P5],
    p6: type[_P6],
    p7: type[_P7],
    p8: type[_P8],
    p9: type[_P9],
    /,
) -> _Variant9[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9]: ...
@overload
def variant(
    p1: type[_P1],
    p2: type[_P2],
    p3: type[_P3],
    p4: type[_P4],
    p5: type[_P5],
    p6: type[_P6],
    p7: type[_P7],
    p8: type[_P8],
    p9: type[_P9],
    p10: type[_P10],
    /,
) -> _Variant10[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9, _P10]: ...
@overload
def variant(
    p1: type[_P1],
    p2: type[_P2],
    p3: type[_P3],
    p4: type[_P4],
    p5: type[_P5],
    p6: type[_P6],
    p7: type[_P7],
    p8: type[_P8],
    p9: type[_P9],
    p10: type[_P10],
    p11: type[_P11],
    /,
) -> _Variant11[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9, _P10, _P11]: ...
@overload
def variant(
    p1: type[_P1],
    p2: type[_P2],
    p3: type[_P3],
    p4: type[_P4],
    p5: type[_P5],
    p6: type[_P6],
    p7: type[_P7],
    p8: type[_P8],
    p9: type[_P9],
    p10: type[_P10],
    p11: type[_P11],
    p12: type[_P12],
    /,
) -> _Variant12[_P1, _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9, _P10, _P11, _P12]: ...


def variant(*payload: type[Any]) -> Any:
    """Declare one case of a `@contractunion` type.

    `variant()` is a unit case; `variant(U32)` and `variant(U32, U32)` carry a
    payload, in declaration order, up to `MAX_PAYLOAD_ARITY` values. The
    overload set above is what types the resulting descriptor -- `Shape.Circle`
    as `(U32) -> Shape` -- and every arm is positional-only (`/`), because the
    runtime takes `*payload` and could not accept the `variant(p=U32)` a
    keyword-taking arm would statically admit.

    Returns a placeholder `@contractunion` swaps for the bound descriptor; the
    call also gives the compiler an unambiguous `ast.Call` to read the payload
    annotations from.
    """
    if len(payload) > MAX_PAYLOAD_ARITY:
        raise ValueError(
            f"a variant payload carries at most {MAX_PAYLOAD_ARITY} values "
            f"(S4's tuple arity), not {len(payload)}"
        )
    for entry in payload:
        if not _is_payload_type(entry):
            raise TypeError(f"variant() takes payload types, not the value {entry!r}")
    return _VariantSpec(payload)


class _EnumValue(Generic[_D]):
    """One declared member of a `@contractenum` type: placeholder AND
    descriptor, unlike `variant()`'s two-step.

    Nothing has to be bound: `enumvalue(n)` already carries the discriminant,
    and `__get__` is handed the owner class, so `Color.Red` builds a `Color`
    with no rebinding step at all. (`@contractenum` still validates the body --
    the base, the member form, the discriminant range and duplicates -- it just
    has nothing to swap. Case NAMES are checked in `compiler/limits.py`
    instead, per kind: the caps differ, and a decorator raise would take the
    whole class statement down with it, which is what would make that located
    check unreachable.)

    `_D` is spelled `Any` at every declaration site (see its own comment); the
    real owner comes back through `__get__`'s method-scoped `_O`.
    """

    __slots__ = ("_discriminant",)

    def __init__(self, discriminant: int) -> None:
        self._discriminant = discriminant

    def __get__(self, obj: object | None, owner: type[_O]) -> _O:
        return cast("_O", _enum_member(owner, self._discriminant))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"enumvalue({self._discriminant})"


def _enum_member(owner: object, discriminant: int) -> ContractEnum:
    """`owner`'s member for `discriminant`, or a loud refusal.

    An `enumvalue(n)` in a class that is not a `ContractEnum` has nothing to
    build -- and handing back the placeholder, or a bare `int`, would put a
    non-chain value into a contract where every later failure would name
    something else.
    """
    if not (isinstance(owner, type) and issubclass(owner, ContractEnum)):
        name = getattr(owner, "__name__", repr(owner))
        raise TypeError(
            f"enumvalue() declares a member of a ContractEnum subclass, and {name} is not one"
        )
    return owner._construct(discriminant)


def enumvalue(n: int) -> _EnumValue[Any]:
    """Declare one member of a `@contractenum` type, with its discriminant.

    The discriminant is explicit: M1 models no implicit numbering, so a member
    always says which `u32` it is on chain. `bool` is refused even though it is
    an `int` in Python, mirroring `errorcode`.
    """
    if not isinstance(n, int) or isinstance(n, bool):
        raise TypeError(f"enumvalue() takes an int discriminant, not {type(n).__name__}")
    member: _EnumValue[Any] = _EnumValue(n)
    return member
