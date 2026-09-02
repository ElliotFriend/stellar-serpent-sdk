"""The tier-1 VALUE layer for tagged unions and int enums (M1-E2, ruling E1/E9).

§B.1 is ground truth here, byte-verified against real Rust builds on
soroban-sdk 22.0.11 and 27.0.6 (identical output):

* a UNIT variant's on-chain value is a **one-element `ScVec`** holding the
  variant-name `Symbol` -- not a bare `Symbol`, which is the single most
  consequential shape fact in this sub-plan;
* a TUPLE variant is `ScVec[Symbol, payload...]` in declaration order;
* an INT ENUM value is a **bare `U32`**.

Everything asserted below follows from those three facts, which is why the
storage-key expectations are spelled as literal shapes rather than as
comparisons against a second construction of the same thing.

`@contractunion`/`@contractenum` are Task 2's, so this file binds the
`variant()` placeholders by hand (`_bind_cases`, which is exactly what the
decorator will do) and tests the value layer on its own.
"""

import copy
from collections.abc import Callable
from typing import NamedTuple, cast

import pytest

from serpent.decorators import contracttype
from serpent.env import _families_of_ty, tag_of_chain_value
from serpent.errors import AbiCheckFailed
from serpent.types import (
    U32,
    U64,
    ContractEnum,
    ContractUnion,
    Map,
    Symbol,
    Vec,
    enumvalue,
    variant,
)
from serpent.types._ordering import Struct, val_cmp
from serpent.types._storage_key import storage_key
from serpent.types._udt import MAX_PAYLOAD_ARITY, _bind_variant, _VariantSpec


class Shape(ContractUnion):
    """The dossier's running example: a unit, a one-payload and a two-payload
    variant, in declaration order, plus one container payload."""

    Empty = variant()
    Circle = variant(U32)
    Rect = variant(U32, U32)
    Boxed = variant(Vec[U32])


class Color(ContractEnum):
    """An int enum with explicit discriminants (M1 has no implicit ones)."""

    Red = enumvalue(0)
    Green = enumvalue(1)


@contracttype
class Point:
    x: U32


class Wrapped(ContractUnion):
    """A struct payload -- `Vec`'s own widened element bound (E2 (b)/MJ-7),
    which a variant payload shares: a struct is an ordinary contract shape."""

    At = variant(Point)


def _bind_cases(cls: type[ContractUnion]) -> None:
    """Bind every `variant()` placeholder in `cls` to its attribute NAME.

    Task 2's `@contractunion` does precisely this, the way `@contracterror`
    swaps `errorcode(N)`'s `_ErrorCode` placeholder for a generated class: the
    factory cannot know the case name (it is the attribute name), so the
    declaration layer supplies it. Spelled here so Task 1's value layer is
    testable before the decorator exists.
    """
    for name, member in list(vars(cls).items()):
        if isinstance(member, _VariantSpec):
            setattr(cls, name, _bind_variant(name, cls, member))


_bind_cases(Shape)
_bind_cases(Wrapped)


class _Declarations(NamedTuple):
    """`Shape` and `Color`, as the one namespace the tests read them from."""

    Shape: type[Shape]
    Color: type[Color]


_DECLARATIONS = _Declarations(Shape=Shape, Color=Color)


def _shape_module() -> _Declarations:
    return _DECLARATIONS


# --- §B.1's three shapes -----------------------------------------------------


def test_a_unit_variant_is_a_one_element_vec_led_by_its_name() -> None:
    """§B.1, byte-verified: unit variant == Vec[Symbol("Empty")], not a bare
    Symbol. The single most consequential shape fact in this sub-plan."""
    shape = _shape_module().Shape
    assert shape.Empty.tag() == Symbol("Empty")
    # Symbol's rank is 15 and its _cmp_payload is BYTES, verified live:
    # storage_key(Vec(Symbol, [Symbol("Empty")])) == ('vec', ((15, b'Empty'),))
    assert storage_key(shape.Empty) == ("vec", ((15, b"Empty"),))


def test_a_tuple_variant_is_its_name_then_its_payload_in_declaration_order() -> None:
    """§B.1's second fact: the payload FOLLOWS the name, in declaration order,
    so `Rect(1, 2)` and `Rect(2, 1)` are different values."""
    shape = _shape_module().Shape
    assert storage_key(shape.Rect(U32(1), U32(2))) == ("vec", ((15, b"Rect"), (3, 1), (3, 2)))
    assert storage_key(shape.Rect(U32(1), U32(2))) != storage_key(shape.Rect(U32(2), U32(1)))


def test_an_int_enum_value_is_a_bare_u32() -> None:
    """§B.1's third fact: no wrapper, no vec, no symbol -- the discriminant
    itself, which is why `ContractEnum` carries `U32`'s rank."""
    color = _shape_module().Color
    assert storage_key(color.Red) == storage_key(U32(0))
    assert storage_key(color.Green) == (3, 1)


# --- reads -------------------------------------------------------------------


def test_a_payload_index_is_zero_based_over_the_payload() -> None:
    rect = _shape_module().Shape.Rect(U32(1), U32(2))
    assert rect.payload(U32(0), U32) == U32(1)
    assert rect.payload(U32(1), U32) == U32(2)


def test_a_payload_read_tag_checks_like_the_storage_get_does() -> None:
    circle = _shape_module().Shape.Circle(U32(3))
    with pytest.raises(AbiCheckFailed):
        circle.payload(U32(0), Symbol)


def test_a_payload_read_is_re_typed_like_a_storage_read() -> None:
    """`payload()` and `get()` decode a held word the same way (the M1-E2
    final-review ruling): the slot is an `ScVec` element on chain, and `ty` is
    what the program reads it as, so an int-enum slot read as `U32` answers
    the bare `u32` it is and a `u32` slot read as an int enum answers that
    member. `env._retyped_as` is the ONE helper both reads call."""
    shape = _shape_module().Shape
    color = _shape_module().Color
    assert shape.Circle(U32(1)).payload(U32(0), color) == color.Green
    untyped: Callable[..., object] = shape.Circle
    held = cast("ContractUnion", untyped(color.Green))
    assert held.payload(U32(0), U32) == U32(1)


def test_a_payload_read_past_the_payload_is_an_index_error() -> None:
    """Host-shaped, like every other `Vec` read: the name `Symbol` at slot 0 is
    not reachable through `payload`, and a unit variant has none at all."""
    shape = _shape_module().Shape
    with pytest.raises(IndexError):
        shape.Circle(U32(3)).payload(U32(1), U32)
    with pytest.raises(IndexError):
        shape.Empty.payload(U32(0), U32)


def test_a_payload_read_hands_back_the_held_element_without_a_copy() -> None:
    """`Vec.get`'s own rule: a read is not a copy. Observable only through a
    mutable payload -- and matching `Vec.get` is what keeps the two reads one
    story."""
    inner = Vec(U32, [U32(1)])
    assert _shape_module().Shape.Boxed(inner).payload(U32(0), Vec) is inner


def test_a_struct_payload_reads_back_under_its_own_type() -> None:
    """`payload(index, ty)` goes through `env._require_ty`, the same door a
    storage `get` uses, so a `@contracttype` payload is decoded under the
    struct class itself -- and a WRONG struct-shaped `ty` still fails there."""
    at = Wrapped.At(Point(x=U32(4)))
    assert at.payload(U32(0), Point) == Point(x=U32(4))
    with pytest.raises(AbiCheckFailed):
        at.payload(U32(0), U32)


def test_a_twelve_payload_variant_constructs_at_the_arity_cap() -> None:
    """The `_VARIANT_CLASSES[12]` path, which nothing else reaches: the widest
    payload E6 allows, built and read back end to end (the descriptor table has
    one class per arity, so the last one is a real, separately-typed row)."""

    class Widest(ContractUnion):
        All = variant(*([U32] * MAX_PAYLOAD_ARITY))

    _bind_cases(Widest)
    # Constructed through an UNTYPED reference, the same way the arity-cap test
    # below asks `variant` itself: a splatted payload list matches the
    # zero-argument overload statically, so the twelve-payload descriptor is
    # only reachable at runtime -- which is precisely the path under test.
    build = cast("Callable[..., Widest]", Widest.All)
    value = build(*[U32(i) for i in range(MAX_PAYLOAD_ARITY)])
    assert value.tag() == Symbol("All")
    assert [value.payload(U32(i), U32) for i in range(MAX_PAYLOAD_ARITY)] == [
        U32(i) for i in range(MAX_PAYLOAD_ARITY)
    ]
    with pytest.raises(IndexError):
        value.payload(U32(MAX_PAYLOAD_ARITY), U32)


# --- ruling E9: neither kind is a dataclass ----------------------------------


def test_neither_new_kind_is_a_dataclass() -> None:  # ruling E9, risk F.1.2
    """The trap: `Struct` is a runtime_checkable Protocol matching
    `__dataclass_fields__`, and it is the FALLTHROUGH in tag_of_chain_value,
    _families_of_ty and storage_key. A dataclass union would be classified a
    Map -- wrong family, wrong key, wrong ABI tag, no error anywhere."""
    module = _shape_module()
    for value in (module.Shape.Empty, module.Color.Red):
        assert not isinstance(value, Struct)
        assert not hasattr(type(value), "__dataclass_fields__")
    assert tag_of_chain_value(module.Shape.Empty) == "vec"
    assert tag_of_chain_value(module.Color.Red) == "u32"
    assert _families_of_ty(module.Shape) == frozenset({"vec"})
    assert _families_of_ty(module.Color) == frozenset({"u32"})


# --- ordering, hashing, storage keys -----------------------------------------


def test_an_int_enum_orders_and_hashes_exactly_like_a_u32() -> None:
    color = _shape_module().Color
    assert storage_key(color.Green) == storage_key(U32(1))
    assert val_cmp(color.Red, color.Green) < 0
    assert len({color.Red, color.Red}) == 1


def test_an_int_enum_orders_against_a_u32_by_rank_then_discriminant() -> None:
    """`ContractEnum._SCVAL_RANK` IS `U32`'s (3), so `val_cmp` never reaches a
    cross-type answer against a `U32` -- the discriminants decide."""
    color = _shape_module().Color
    assert val_cmp(color.Green, U32(0)) > 0
    assert val_cmp(color.Green, U32(1)) == 0
    assert val_cmp(color.Red, U64(0)) < 0  # rank 3 sorts before rank 4


def test_a_union_is_a_storage_key_but_not_orderable_at_tier_1() -> None:
    """D10's exact wording, and F.1.8's worse-than-refusal failure mode: the
    first `set` succeeds because a one-element binary search compares nothing
    -- and nothing else about that map works (the read pin below)."""
    shape = _shape_module().Shape
    assert storage_key(shape.Circle(U32(3))) == storage_key(shape.Circle(U32(3)))
    with pytest.raises(NotImplementedError, match="container comparison"):
        shape.Empty._cmp_payload()


def test_a_second_map_entry_keyed_by_a_union_hits_the_deferred_refusal() -> None:
    """F.1.8 spelled out: the FIRST insert succeeds (nothing to compare), the
    second raises `Vec`'s own deferred error. Not modelled in tier 1."""
    shape = _shape_module().Shape
    keyed: Map[ContractUnion, U32] = Map(ContractUnion, U32)
    keyed.set(shape.Circle(U32(1)), U32(1))
    with pytest.raises(NotImplementedError, match="container comparison"):
        keyed.set(shape.Circle(U32(2)), U32(2))


def test_a_one_entry_map_keyed_by_a_union_cannot_be_READ_either() -> None:
    """The correction the M1-E2 final review made to F.1.8: the first `set`
    succeeding is not a working single-entry map.

    A `get`/`has` compares the probe key against the stored one, so BOTH raise
    `Vec`'s deferred refusal at every entry count, one entry included -- the
    "keep the map to a single entry" workaround the prose used to offer never
    existed. A `Map` keyed by a union is not modelled in tier 1 at all; key on
    a `@contracttype` struct instead. (Storage keyed by a union is a different
    mechanism -- hash-based, not ordered -- and is unaffected.)
    """
    shape = _shape_module().Shape
    keyed: Map[ContractUnion, U32] = Map(ContractUnion, U32)
    keyed.set(shape.Circle(U32(1)), U32(1))
    assert len(keyed) == 1
    with pytest.raises(NotImplementedError, match="container comparison"):
        keyed.get(shape.Circle(U32(1)))
    with pytest.raises(NotImplementedError, match="container comparison"):
        keyed.has(shape.Circle(U32(1)))


def test_two_equal_unions_are_equal_and_hash_equal() -> None:
    shape = _shape_module().Shape
    assert shape.Circle(U32(3)) == shape.Circle(U32(3))
    assert shape.Circle(U32(3)) != shape.Circle(U32(4))
    assert shape.Circle(U32(3)) != shape.Empty
    assert len({shape.Circle(U32(3)), shape.Circle(U32(3))}) == 1


def test_a_container_payload_union_is_unhashable_and_says_so() -> None:
    """Documented at `_udt.py`'s `__hash__`: a payload that is itself a
    container is unhashable, and `hash()` raises for it rather than inventing
    an answer -- `storage_key` is the way to key on such a value, and it is
    what `env`'s store uses."""
    boxed = _shape_module().Shape.Boxed(Vec(U32, [U32(1)]))
    with pytest.raises(TypeError):
        hash(boxed)
    # ... and the storage-key door still answers, which is the whole point.
    assert storage_key(boxed) == ("vec", ((15, b"Boxed"), ("vec", ((3, 1),))))


def test_two_unions_of_DIFFERENT_declared_types_are_equal_by_shape() -> None:
    """`Vec.__eq__`'s own reasoning, one layer up: on chain both values ARE the
    same `ScVec`, so the declared Python type cannot be what separates them."""

    class Twin(ContractUnion):
        Circle = variant(U32)

    _bind_cases(Twin)
    assert Twin.Circle(U32(3)) == _shape_module().Shape.Circle(U32(3))
    assert storage_key(Twin.Circle(U32(3))) == storage_key(_shape_module().Shape.Circle(U32(3)))


def test_two_members_of_DIFFERENT_int_enums_are_equal_by_discriminant() -> None:
    """The same fact for the other kind: both are the same bare `U32`."""

    class Level(ContractEnum):
        Low = enumvalue(0)

    assert Level.Low == _shape_module().Color.Red
    assert Level.Low != _shape_module().Color.Green


def test_an_enum_member_is_equal_to_itself_and_not_to_another_case() -> None:
    color = _shape_module().Color
    assert color.Red == color.Red
    assert color.Red != color.Green
    # No coercion, `Symbol`'s rule: a bare U32 of the same discriminant is a
    # different chain type, whatever the two share on the wire.
    assert color.Red != U32(0)


# --- immutability and copying ------------------------------------------------


def test_neither_kind_can_be_mutated_after_construction() -> None:
    """Ruling E9's immutability: the held `Vec` is never handed out and no
    attribute can be rebound, so a union's hash cannot change under a dict."""
    shape = _shape_module().Shape.Circle(U32(1))
    with pytest.raises(AttributeError, match="immutable"):
        shape._vec = Vec(U32)
    with pytest.raises(AttributeError, match="immutable"):
        del shape._vec


def test_a_deep_copy_is_an_equal_value_with_an_independent_payload() -> None:
    """`env.py`'s deep-copy law runs over stored values, so `deepcopy` must
    work on both kinds -- a slotted class with a rejecting `__setattr__` breaks
    the default copy protocol, which is exactly the bug this pins."""
    module = _shape_module()
    inner = Vec(U32, [U32(1)])
    original = module.Shape.Boxed(inner)
    clone = copy.deepcopy(original)
    assert clone == original
    assert clone.payload(U32(0), Vec) is not inner
    inner.push_back(U32(2))
    assert clone != original
    assert copy.deepcopy(module.Color.Green) == module.Color.Green


# --- the factories -----------------------------------------------------------


def test_variant_refuses_a_payload_arity_over_the_cap() -> None:
    """E6: ONE arity story -- S4's tuple arity, 12, so a union payload and a
    tuple return cannot disagree about how wide a shape may be.

    Asked through an UNTYPED reference: the overload set has no 13-argument arm
    at all (that is the static half of the same cap) and a splat matches no arm
    either, so this is the runtime half, which is what an author reaches past a
    `# type: ignore` -- or what the compiler's own reader reaches.
    """
    assert MAX_PAYLOAD_ARITY == 12
    untyped: Callable[..., object] = variant
    assert isinstance(untyped(*([U32] * MAX_PAYLOAD_ARITY)), _VariantSpec)
    with pytest.raises(ValueError, match="12"):
        untyped(*([U32] * (MAX_PAYLOAD_ARITY + 1)))


def test_binding_an_over_wide_spec_says_so_instead_of_indexing_off_the_end() -> None:
    """`_VARIANT_CLASSES` is indexed by arity, so a hand-built `_VariantSpec`
    wider than the cap used to die on an opaque `IndexError` into a private
    tuple. Not authorable in a contract -- `variant()` refuses it first -- but
    reachable on this library surface, and one message serves both doors (so
    the loader bridges one needle, SPT5006, whichever raised)."""
    wide = _VariantSpec(tuple([U32] * (MAX_PAYLOAD_ARITY + 1)))
    with pytest.raises(ValueError, match="a variant payload carries at most 12"):
        _bind_variant("Big", Shape, wide)


def test_a_payload_must_be_a_chain_value_or_a_struct() -> None:
    """The invariant `Vec`/`Map` enforce on the way in (`require_map_value`):
    an unvalidated payload would sit in the held `Vec` and produce a garbage
    storage key later, somewhere else. A `@contracttype` struct payload IS
    admitted -- E2 (b)'s widened bound, which a variant payload shares.

    What is NOT refused any more is a raw LITERAL (see the adoption test
    below); a value with no chain type to adopt it through still is.
    """
    assert storage_key(Wrapped.At(Point(x=U32(1)))) == (
        "vec",
        (storage_key(Symbol("At")), storage_key(Point(x=U32(1)))),
    )
    untyped: Callable[..., object] = Shape.Circle
    with pytest.raises(TypeError, match="not a chain value"):
        untyped([1])
    with pytest.raises(TypeError, match="not a chain value"):
        untyped(None)


def test_a_raw_literal_payload_is_adopted_through_its_declared_slot() -> None:
    """M1-C's literal adoption, in a variant payload slot (the M1-E2
    final-review ruling): `Shape.Circle(1)` is a compiler ACCEPT -- the
    frontend adopts the literal through the slot's declared type -- so tier 1
    builds exactly what the compiled form does rather than refusing it.

    Adoption is the declared slot's own constructor, so its error is the one an
    out-of-range literal gets, in the same place the frontend reports SPT3004.
    """
    shape = _shape_module().Shape
    untyped: Callable[..., object] = shape.Circle
    assert untyped(1) == shape.Circle(U32(1))
    adopted = cast("ContractUnion", untyped(1))
    assert adopted.payload(U32(0), U32) == U32(1)
    with pytest.raises(ValueError, match="out of range"):
        untyped(-1)


def test_a_payload_index_may_be_a_raw_int() -> None:
    """The other half of the same ruling: `s.payload(0, U32)` compiles (the
    index is a literal the frontend reads statically), so it runs at tier 1
    too, and answers what the `U32` spelling answers."""
    rect = _shape_module().Shape.Rect(U32(1), U32(2))
    assert rect.payload(0, U32) == rect.payload(U32(0), U32)
    assert rect.payload(1, U32) == U32(2)
    with pytest.raises(IndexError):
        rect.payload(2, U32)


def test_variant_refuses_a_payload_that_is_not_a_type() -> None:
    """A static error too (hence the ignore, which names the code the surface
    really produces), and a runtime one, because a payload VALUE would
    otherwise be silently accepted as a declaration."""
    with pytest.raises(TypeError, match="payload types"):
        variant(U32(1))  # type: ignore[call-overload]


def test_variant_accepts_a_parameterized_container_annotation() -> None:
    """`Vec[U32]` is not a `type` at runtime (it is a generic alias), and it is
    the only strict-clean way to spell a container payload."""
    assert isinstance(variant(Vec[U32]), _VariantSpec)


def test_enumvalue_refuses_a_non_int_and_a_bool() -> None:
    """`errorcode`'s rule (`decorators.py:151`): `bool` is an `int` in Python
    and is never a discriminant."""
    with pytest.raises(TypeError, match="int"):
        enumvalue("0")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="int"):
        enumvalue(True)


def test_an_enumvalue_outside_a_contractenum_body_is_refused_loudly() -> None:
    """The descriptor builds an instance of the class it is accessed through,
    so a non-`ContractEnum` owner has nothing to build and says so rather than
    handing back something that is not a chain value."""

    class NotAnEnum:
        Red = enumvalue(0)

    with pytest.raises(TypeError, match="ContractEnum"):
        _ = NotAnEnum.Red
