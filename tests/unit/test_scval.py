"""`serpent.testing._scval`: ONE marshalling layer between tier-1 values and ScVal (ruling E2).

Two kinds of pin. GOLDENS against `stellar_sdk.scval`'s own constructors prove the
scalar encodings are the ecosystem's, not serpent's. ROUND-TRIPS over the generated
chain-value strategy prove `decode(encode(v), type(v)) == v` for every M1 shape,
including the three D6-coarse ones (struct vs Map, union vs Vec, enum vs U32) where
the requested `ty` is what disambiguates (D11).
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from stellar_sdk import scval
from stellar_sdk.xdr import SCVal, SCValType

from serpent import (
    I32,
    I64,
    I128,
    U32,
    U64,
    U128,
    Address,
    Bool,
    Bytes,
    Bytes32,
    Bytes64,
    ContractEnum,
    ContractUnion,
    Duration,
    Map,
    String,
    Symbol,
    Timepoint,
    Vec,
    bytes_n,
    contractenum,
    contracttype,
    contractunion,
    enumvalue,
    variant,
)
from serpent.testing._scval import ScValError, decode, decode_loose, encode, from_xdr, to_xdr
from tests.unit.test_env_model import CHAIN_VALUES
from tests.unit.test_examples import OWNER

#: `Vec[Vec]`/`Vec[Map]` as VALUES, not as annotations: a nested container's
#: element type IS the bare class at runtime (review M5, and `Vec(Vec[U32], ...)`
#: is itself a TypeError), so the missing parameter here is the subject of the
#: tests below rather than an omission.
VEC_OF_VEC: object = Vec[Vec]  # type: ignore[type-arg]
VEC_OF_MAP: object = Vec[Map]  # type: ignore[type-arg]

#: `Vec[U32 | None]` -- a declared type `spec/typemap` resolves (it recurses
#: through `X | None`), bound here because tier-1 `Vec`'s element bound admits no
#: union and `Vec(U32 | None, ...)` is itself a TypeError.
VEC_OF_OPTION: object = Vec[U32 | None]  # type: ignore[type-var]

ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"
CONTRACT = "CA3D5KRYM6CB7OWQ6TWYRR3Z4T7GNZLKERYNZGGA5SOAOPIFY6YQGAXE"


@contracttype
class Point:
    x: U32
    y: I64


@contracttype
class Boxed:
    inner: U32 | None


@contractunion
class Shape(ContractUnion):
    Empty = variant()
    Circle = variant(U32)
    Rect = variant(U32, U32)


@contractenum
class Color(ContractEnum):
    Red = enumvalue(0)
    Green = enumvalue(1)


# --- goldens: the scalar encodings are stellar_sdk's --------------------------

GOLDENS = [
    (U32(7), scval.to_uint32(7)),
    (I32(-7), scval.to_int32(-7)),
    (U64(2**40), scval.to_uint64(2**40)),
    (I64(-(2**40)), scval.to_int64(-(2**40))),
    (U128(2**100), scval.to_uint128(2**100)),
    (I128(-(2**100)), scval.to_int128(-(2**100))),
    (Bool(True), scval.to_bool(True)),
    (Symbol("hello"), scval.to_symbol("hello")),
    (String("hi there"), scval.to_string("hi there")),
    (Bytes(b"\x01\x02"), scval.to_bytes(b"\x01\x02")),
    (Bytes32(bytes(32)), scval.to_bytes(bytes(32))),
    (Address(ACCOUNT), scval.to_address(ACCOUNT)),
    (Timepoint(1_700_000_000), scval.to_timepoint(1_700_000_000)),
    (Duration(60), scval.to_duration(60)),
    (None, scval.to_void()),
]


@pytest.mark.parametrize(("value", "expected"), GOLDENS, ids=[type(v).__name__ for v, _ in GOLDENS])
def test_scalar_encoding_matches_stellar_sdk(value: object, expected: SCVal) -> None:
    assert encode(value).to_xdr_bytes() == expected.to_xdr_bytes()


# --- the three D6-coarse shapes, disambiguated by ty (D11) ----------------------


def test_a_struct_is_a_map_with_sorted_symbol_keys() -> None:
    sc = encode(Point(x=U32(1), y=I64(-2)))
    assert sc.type == SCValType.SCV_MAP
    assert sc.map is not None
    keys = [scval.from_symbol(e.key) for e in sc.map.sc_map]
    assert keys == sorted(keys) == ["x", "y"]
    assert decode(sc, Point) == Point(x=U32(1), y=I64(-2))
    loose = decode_loose(sc)
    assert isinstance(loose, Map)  # D6-coarse without a ty


def test_a_union_is_a_vec_led_by_the_case_symbol() -> None:
    sc = encode(Shape.Rect(U32(2), U32(3)))
    assert sc.type == SCValType.SCV_VEC
    assert sc.vec is not None
    assert scval.from_symbol(sc.vec.sc_vec[0]) == "Rect"
    assert decode(sc, Shape) == Shape.Rect(U32(2), U32(3))
    assert decode(encode(Shape.Empty), Shape) == Shape.Empty
    assert isinstance(decode_loose(sc), Vec)


def test_an_int_enum_is_a_bare_u32() -> None:
    sc = encode(Color.Green)
    assert sc.to_xdr_bytes() == scval.to_uint32(1).to_xdr_bytes()
    assert decode(sc, Color) == Color.Green
    assert decode(sc, U32) == U32(1)  # the same word, re-typed (D11)


def test_option_decodes_void_to_none_and_a_value_to_the_inner_type() -> None:
    assert decode(scval.to_void(), U32 | None) is None
    assert decode(scval.to_uint32(4), U32 | None) == U32(4)


def test_containers_decode_element_types() -> None:
    v = Vec(U32, [U32(1), U32(2)])
    assert decode(encode(v), Vec[U32]) == v
    m = Map(Symbol, U32)
    m.set(Symbol("a"), U32(1))
    assert decode(encode(m), Map[Symbol, U32]) == m


def test_a_bare_container_ty_decodes_its_elements_loosely() -> None:
    """Review M5: `Vec(Vec[U32], ...)` is a TypeError, so `Vec(Vec, [...])` is the
    only spelling a nested container has -- and a bare element type is EXACT for it,
    because container equality is by content while content equality keeps the scalar
    kinds inside distinct."""
    nested: Vec[Any] = Vec(Vec, [Vec(U32, [U32(1)]), Vec(U32, [])])
    assert decode(encode(nested), VEC_OF_VEC) == nested
    assert decode(encode(nested), Vec) == nested  # the bare class as the whole ty
    maps: Vec[Any] = Vec(Map, [Map(Symbol, U32, [(Symbol("a"), U32(1))])])
    assert decode(encode(maps), VEC_OF_MAP) == maps
    differing: Vec[Any] = Vec(Vec, [Vec(I32, [I32(1)]), Vec(U32, [])])
    assert decode(encode(nested), VEC_OF_VEC) != differing


def test_a_struct_under_a_bare_container_ty_comes_back_as_the_map_it_is() -> None:
    """The one shape a bare element type cannot recover, pinned rather than hidden.

    A struct IS an ScMap on chain (D6), so a `ty` that says no more than "a Map"
    cannot tell the two apart -- and this is what `_ty_of` recurses to avoid, since
    the caller who holds `Vec(Vec, [Vec(Point, ...)])` does know. A bare element
    type is coarse, NOT blind: `Vec[Vec]` still refuses the ScMap outright, because
    the container kind is checked even when the elements go loose.
    """
    structs = Vec(Point, [Point(x=U32(1), y=I64(2))])
    for coarse in (VEC_OF_MAP, Vec):  # the alias, and the bare class as the whole ty
        loose = decode(encode(structs), coarse)
        assert isinstance(loose, Vec)
        assert isinstance(loose.get(0), Map)
        assert loose != structs
    with pytest.raises(ScValError):
        decode(encode(structs), VEC_OF_VEC)  # an ScMap is not an ScVec
    assert decode(encode(structs), Vec[Point]) == structs


def test_a_bare_container_ty_still_checks_the_container_kind() -> None:
    """Review M5's rule is about the ELEMENTS, not the container: a bare `Vec`/`Map`
    narrows one thing -- the kind -- and only what is inside it goes loose."""
    assert decode(encode(Vec(U32, [U32(1)])), Vec) == Vec(U32, [U32(1)])
    assert decode(encode(Map(Symbol, U32, [(Symbol("a"), U32(1))])), Map) == Map(
        Symbol, U32, [(Symbol("a"), U32(1))]
    )
    for ty in (Vec, Map, VEC_OF_VEC):
        with pytest.raises(ScValError):
            decode(scval.to_uint32(1), ty)


def test_a_vec_of_options_is_refused_rather_than_faked() -> None:
    """`Vec[U32 | None]` is spellable as a DECLARED type but has no tier-1 value.

    `Vec`'s element bound is `ContainerValue`, which admits no `None`, and
    `Vec(U32 | None, ...)` is itself a `TypeError` because a union is not a class.
    Widening the decoded element class to `object` would accept a `None` -- and
    that is the trap this pins shut: it would build a `Vec` no author could write,
    holding an element with no `_SCVAL_RANK`, which the next `val_cmp` or
    `storage_key` would trap on far from the decode that made it. So the boundary
    refuses it LOUDLY, with `ScValError` rather than a raw `TypeError` out of the
    container, and `Map` already refuses the same thing structurally.
    """
    present = Vec(U32, [U32(1)])
    assert decode(encode(present), VEC_OF_OPTION) == present  # every element present
    with pytest.raises(ScValError):
        decode(scval.to_vec([scval.to_uint32(1), scval.to_void()]), VEC_OF_OPTION)
    with pytest.raises(ScValError):
        decode_loose(scval.to_vec([scval.to_void()]))
    with pytest.raises(ScValError):
        decode_loose(scval.to_map({scval.to_symbol("k"): scval.to_void()}))


def test_an_option_needs_a_none_arm_before_void_answers_none() -> None:
    """`U32 | I32` is not an Option, and M1 has no union of two chain types -- so
    reading Void as `None` for it would answer a value the `ty` never admitted."""
    with pytest.raises(ScValError):
        decode(scval.to_void(), U32 | I32)
    with pytest.raises(ScValError):
        decode(scval.to_uint32(1), U32 | I32)


def test_the_whole_bytes_family_is_one_kind_and_the_class_checks_the_length() -> None:
    b3 = bytes_n(3)
    for value in (Bytes(b"abc"), b3(b"abc")):
        assert encode(value).type == SCValType.SCV_BYTES
    assert decode(encode(Bytes64(bytes(64))), Bytes64) == Bytes64(bytes(64))
    assert decode(encode(b3(b"abc")), b3) == b3(b"abc")
    assert decode(encode(b3(b"abc")), Bytes) == Bytes(b"abc")
    with pytest.raises(ScValError):
        decode(encode(b3(b"abc")), Bytes32)


def test_a_contract_address_round_trips_as_well_as_an_account_one() -> None:
    for strkey in (ACCOUNT, CONTRACT):
        assert decode(encode(Address(strkey)), Address) == Address(strkey)


def test_the_time_types_are_their_own_kinds_not_u64() -> None:
    assert decode(encode(Timepoint(5)), Timepoint) == Timepoint(5)
    assert decode(encode(Duration(5)), Duration) == Duration(5)
    with pytest.raises(ScValError):
        decode(encode(Timepoint(5)), U64)
    with pytest.raises(ScValError):
        decode(encode(U64(5)), Duration)


def test_a_union_carrying_one_payload_and_a_struct_field_option_round_trip() -> None:
    assert decode(encode(Shape.Circle(U32(4))), Shape) == Shape.Circle(U32(4))
    assert decode(encode(Boxed(inner=None)), Boxed) == Boxed(inner=None)
    assert decode(encode(Boxed(inner=U32(1))), Boxed) == Boxed(inner=U32(1))


def test_an_option_of_a_container_composes() -> None:
    v = Vec(U32, [U32(1)])
    assert decode(encode(v), Vec[U32] | None) == v
    assert decode(scval.to_void(), Vec[U32] | None) is None


def test_decode_loose_answers_every_scalar_kind_by_kind_alone() -> None:
    for value in (
        Bool(False),
        U32(1),
        I32(-1),
        U64(1),
        I64(-1),
        U128(1),
        I128(-1),
        Timepoint(1),
        Duration(1),
        Symbol("s"),
        String("s"),
        Bytes(b"s"),
        Address(ACCOUNT),
    ):
        got = decode_loose(encode(value))
        assert got == value
        assert type(got) is type(value)
    assert decode_loose(scval.to_void()) is None
    # A fixed-length payload is coarse the way the chain is: one BYTES kind.
    assert type(decode_loose(encode(Bytes32(bytes(32))))) is Bytes


def test_to_xdr_and_from_xdr_are_the_wire_forms_of_encode_and_decode() -> None:
    assert to_xdr(U32(7)) == scval.to_uint32(7).to_xdr_bytes()
    assert from_xdr(to_xdr(Point(x=U32(1), y=I64(-2))), Point) == Point(x=U32(1), y=I64(-2))


# --- mismatches are loud ----------------------------------------------------------


@pytest.mark.parametrize(
    ("sc", "ty"),
    [
        (scval.to_uint32(1), Symbol),
        (scval.to_symbol("x"), U32),
        (scval.to_vec([scval.to_symbol("Nope")]), Shape),
        (scval.to_uint32(9), Color),  # no case with discriminant 9
        (scval.to_uint64(1), U32),  # a U64 is not a U32 even when it fits
        (scval.to_void(), U32),  # Void is only `None` or `X | None`
        (scval.to_map({scval.to_symbol("x"): scval.to_uint32(1)}), Point),  # y is missing
        (scval.to_map({scval.to_uint32(0): scval.to_uint32(1)}), Point),  # not Symbol-keyed
        (scval.to_uint32(1), "U32"),  # not a type at all
        (scval.to_uint32(1), Vec[U32]),
        (scval.to_vec([]), Shape),  # no leading case Symbol
        (scval.to_uint32(1), Vec),  # a bare container still checks the KIND
        (scval.to_uint32(1), Map),
        (scval.to_vec([scval.to_uint32(1)]), VEC_OF_VEC),  # element declared Vec, got a U32
    ],
)
def test_decode_refuses_a_kind_or_case_the_ty_does_not_name(sc: SCVal, ty: object) -> None:
    with pytest.raises(ScValError):
        decode(sc, ty)


def test_encode_refuses_a_raw_python_scalar() -> None:
    with pytest.raises(ScValError):
        encode(3)


# --- round-trips ------------------------------------------------------------------


@given(CHAIN_VALUES)
@settings(max_examples=200)
def test_every_generated_chain_value_round_trips_through_its_own_type(value: object) -> None:
    ty = _ty_of(value)
    assert decode(encode(value), ty) == value
    assert from_xdr(to_xdr(value), ty) == value


def _ty_of(value: object) -> object:
    """The `ty` a caller would pass for `value` -- element types read off containers.

    `element_type`/`key_type`/`value_type` are PROPERTIES (review M5). `CHAIN_VALUES`
    generates nested containers whose element type is the BARE class (`Vec[Vec]`,
    `Vec[Map]` -- `Vec(Vec[U32], ...)` is itself a TypeError), so a container's own
    declared element type does not always say what is inside it; `decode` accepts a
    bare `Vec`/`Map` and decodes such elements loosely, which is exact for a
    container of containers because container equality is by content.

    It is NOT exact for a struct, which is the same ScMap as a `Map` on chain (D6)
    -- and the strategy reaches exactly that: `Vec(Vec, [Vec(Holder, [...])])`, whose
    inner `Vec`'s Holders come back as `Map`s under a bare element type. That shape
    is deliberately left in the strategy; what changes is the `ty` a caller would
    pass for it, which is the precise alias `Vec[Vec[Holder]]`. So a bare container
    element type recurses into the CONTENTS one step, and nothing else does -- an
    honest reading of "the type the caller asks for" (D11), not a filter.
    """
    if isinstance(value, Vec):
        return Vec[_element_ty(value.element_type, list(value))]  # type: ignore[misc]
    if isinstance(value, Map):
        return Map[  # type: ignore[misc]
            _element_ty(value.key_type, list(value.keys())),
            _element_ty(value.value_type, list(value.values())),
        ]
    return type(value)


def _element_ty(declared: type[object], items: list[object]) -> object:
    """`declared`, unless it is a BARE container that hides what is inside it."""
    if declared in (Vec, Map) and items:
        return _ty_of(items[0])
    return declared


def test_owner_strkey_round_trips() -> None:
    assert decode(encode(Address(OWNER)), Address) == Address(OWNER)
