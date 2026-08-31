"""`storage_key` (M-2): ONE definition of storage/map key equality across tiers.

S13's motivating failure lives in `tests/harness/objects.py:36-49` (the module
docstring there): a map/storage key must be compared BY VALUE, never by handle,
because a contract builds a fresh struct key on every invocation
(`BalanceKey(owner=Address(...))`). `storage_key` is the value-level twin of
`objects.py`'s word-level `map_key`, so this file is the tier-1-only regression
net -- no wasm, no harness, no host at all.
"""

from serpent.decorators import contracttype
from serpent.types import U32, Address, Bool, Bytes, Bytes32, Map, Symbol, Vec
from serpent.types._storage_key import storage_key

# The union/enum declarations, hand-bound there because `@contractunion` is
# M1-E2 Task 2's -- imported rather than declared a second time here.
from tests.unit.test_udt_values import Color, Shape

ADDRESS_A = "GA7QYNF7SOWQ3GLR2BGMZEHXAVIRZA4KVWLTJJFC7MGXUA74P7UJVSGZ"
ADDRESS_B = "GAAQEAYEAUDAOCAJBIFQYDIOB4IBCEQTCQKRMFYYDENBWHA5DYPSABOV"


@contracttype
class BalanceKey:
    owner: Address


@contracttype
class Point:
    """A multi-field struct with no nested containers -- the plainest S9 case."""

    x: U32
    y: U32


@contracttype
class Wrapper:
    tag: Symbol
    amounts: Vec[U32]
    flags: Map[Symbol, Bool]


# --- scalars -----------------------------------------------------------------


def test_scalars_of_the_same_value_produce_the_same_key() -> None:
    assert storage_key(U32(7)) == storage_key(U32(7))
    assert storage_key(Symbol("counter")) == storage_key(Symbol("counter"))


def test_scalars_of_different_values_produce_different_keys() -> None:
    assert storage_key(U32(7)) != storage_key(U32(8))
    assert storage_key(Symbol("a")) != storage_key(Symbol("b"))


def test_cross_type_scalars_never_collide() -> None:
    """A `U32` and a `Symbol` never share a key, even by accident of payload."""
    assert storage_key(U32(0)) != storage_key(Bool(False))


def test_scalar_keys_are_hashable() -> None:
    hash(storage_key(U32(1)))
    hash(storage_key(Symbol("k")))


# --- Vec -----------------------------------------------------------------


def test_vecs_with_equal_elements_in_order_produce_the_same_key() -> None:
    a = Vec(U32, [U32(1), U32(2), U32(3)])
    b = Vec(U32, [U32(1), U32(2), U32(3)])
    assert storage_key(a) == storage_key(b)


def test_vecs_are_order_sensitive() -> None:
    a = Vec(U32, [U32(1), U32(2)])
    b = Vec(U32, [U32(2), U32(1)])
    assert storage_key(a) != storage_key(b)


def test_vec_keys_are_hashable() -> None:
    hash(storage_key(Vec(U32, [U32(1), U32(2)])))


# --- Map -----------------------------------------------------------------


def test_maps_with_equal_entries_produce_the_same_key_regardless_of_insert_order() -> None:
    a = Map(Symbol, U32, [(Symbol("a"), U32(1)), (Symbol("b"), U32(2))])
    b = Map(Symbol, U32, [(Symbol("b"), U32(2)), (Symbol("a"), U32(1))])
    assert storage_key(a) == storage_key(b)


def test_maps_differing_only_in_a_value_produce_different_keys() -> None:
    """Review B7: `Map.__iter__` yields keys only, so a normalization that
    iterated the map directly would collapse two maps whose VALUES differ.
    `storage_key` must not make that mistake."""
    a = Map(Symbol, U32, [(Symbol("a"), U32(1))])
    b = Map(Symbol, U32, [(Symbol("a"), U32(2))])
    assert storage_key(a) != storage_key(b)


def test_maps_differing_in_keys_produce_different_keys() -> None:
    a = Map(Symbol, U32, [(Symbol("a"), U32(1))])
    b = Map(Symbol, U32, [(Symbol("z"), U32(1))])
    assert storage_key(a) != storage_key(b)


def test_map_keys_are_hashable() -> None:
    hash(storage_key(Map(Symbol, U32, [(Symbol("a"), U32(1))])))


def test_map_in_vec_recurses() -> None:
    inner_a = Map(Symbol, U32, [(Symbol("a"), U32(1))])
    inner_b = Map(Symbol, U32, [(Symbol("a"), U32(1))])
    inner_c = Map(Symbol, U32, [(Symbol("a"), U32(2))])
    va = Vec(Map, [inner_a])
    vb = Vec(Map, [inner_b])
    vc = Vec(Map, [inner_c])
    assert storage_key(va) == storage_key(vb)
    assert storage_key(va) != storage_key(vc)


# --- Struct ----------------------------------------------------------------


def test_two_structurally_equal_struct_keys_from_fresh_instances_are_equal() -> None:
    """The exact S9/motivating case: a contract builds a fresh struct on every
    invocation, and the two must still be the SAME storage key."""
    a = BalanceKey(owner=Address(ADDRESS_A))
    b = BalanceKey(owner=Address(ADDRESS_A))
    assert a is not b
    assert storage_key(a) == storage_key(b)


def test_structs_with_different_field_values_produce_different_keys() -> None:
    a = BalanceKey(owner=Address(ADDRESS_A))
    b = BalanceKey(owner=Address(ADDRESS_B))
    assert storage_key(a) != storage_key(b)


def test_struct_key_is_hashable() -> None:
    hash(storage_key(BalanceKey(owner=Address(ADDRESS_A))))


def test_struct_normalizes_identically_to_its_equivalent_field_keyed_map() -> None:
    """S9: a struct and its equivalent map ARE the same on-chain value."""
    struct_key = storage_key(BalanceKey(owner=Address(ADDRESS_A)))
    equivalent_map = Map(Symbol, Address, [(Symbol("owner"), Address(ADDRESS_A))])
    assert struct_key == storage_key(equivalent_map)


def test_recursion_through_vec_and_map_inside_a_struct() -> None:
    a = Wrapper(
        tag=Symbol("w"),
        amounts=Vec(U32, [U32(1), U32(2)]),
        flags=Map(Symbol, Bool, [(Symbol("on"), Bool(True))]),
    )
    b = Wrapper(
        tag=Symbol("w"),
        amounts=Vec(U32, [U32(1), U32(2)]),
        flags=Map(Symbol, Bool, [(Symbol("on"), Bool(True))]),
    )
    c = Wrapper(
        tag=Symbol("w"),
        amounts=Vec(U32, [U32(1), U32(9)]),
        flags=Map(Symbol, Bool, [(Symbol("on"), Bool(True))]),
    )
    assert a is not b
    assert storage_key(a) == storage_key(b)
    assert storage_key(a) != storage_key(c)
    hash(storage_key(a))


# --- coverage (review M3) ---------------------------------------------------


def test_bytes32_and_bytes_with_equal_payloads_collapse_to_one_key() -> None:
    """D5: `Bytes32`/`Bytes64` are the SAME `ScVal` case as `Bytes`
    (`_SCVAL_RANK` is inherited, not restated), so a fixed-length value and a
    plain `Bytes` with the same payload are one storage key, exactly as tier
    1's own `__eq__` treats them."""
    payload = bytes(range(32))
    assert storage_key(Bytes32(payload)) == storage_key(Bytes(payload))
    assert storage_key(Bytes32(payload)) != storage_key(Bytes(payload[:-1]))


def test_multi_field_struct_normalizes_identically_to_its_equivalent_map() -> None:
    """S9 with more than one field: order in the struct's own declaration
    must not matter, since the equivalent map is a `frozenset` of pairs."""
    point_key = storage_key(Point(x=U32(1), y=U32(2)))
    equivalent_map = Map(Symbol, U32, [(Symbol("x"), U32(1)), (Symbol("y"), U32(2))])
    assert point_key == storage_key(equivalent_map)
    # Order-independent on the MAP side too, since it is a frozenset.
    reordered_map = Map(Symbol, U32, [(Symbol("y"), U32(2)), (Symbol("x"), U32(1))])
    assert point_key == storage_key(reordered_map)


def test_empty_vec_and_empty_map_produce_stable_hashable_keys() -> None:
    assert storage_key(Vec(U32, [])) == storage_key(Vec(U32, []))
    assert storage_key(Vec(U32, [])) != storage_key(Vec(U32, [U32(1)]))
    hash(storage_key(Vec(U32, [])))

    assert storage_key(Map(Symbol, U32)) == storage_key(Map(Symbol, U32, []))
    assert storage_key(Map(Symbol, U32)) != storage_key(Map(Symbol, U32, [(Symbol("a"), U32(1))]))
    hash(storage_key(Map(Symbol, U32)))


def test_symbol_keys_agree_past_the_small_symbol_length_boundary() -> None:
    """`storage_key` works purely at the tier-1 value level and never encodes
    a `Val`, so the 9-character `SymbolSmall` boundary (`to_val()`'s own
    `NotImplementedError` past it) has no bearing on it at all."""
    long_a = Symbol("a_very_long_symbol_name")
    long_b = Symbol("a_very_long_symbol_name")
    assert len(long_a.text) > 9
    assert long_a is not long_b
    assert storage_key(long_a) == storage_key(long_b)
    assert storage_key(long_a) != storage_key(Symbol("a_different_long_name"))


def test_struct_as_a_map_value_recurses_too() -> None:
    """Ruling MJ-7's widened `Map`-VALUE bound (a struct, not just a chain
    value) applies at the key level too: two maps holding equal-but-distinct
    struct values are one key, and differing struct values are not."""
    same_a = Map(Symbol, BalanceKey, [(Symbol("k"), BalanceKey(owner=Address(ADDRESS_A)))])
    same_b = Map(Symbol, BalanceKey, [(Symbol("k"), BalanceKey(owner=Address(ADDRESS_A)))])
    different = Map(Symbol, BalanceKey, [(Symbol("k"), BalanceKey(owner=Address(ADDRESS_B)))])
    assert storage_key(same_a) == storage_key(same_b)
    assert storage_key(same_a) != storage_key(different)
    hash(storage_key(same_a))


def test_none_normalizes_to_the_void_rank_with_no_payload() -> None:
    """Review M2: `Void`'s A8 rank (1) with no payload -- a legitimate value
    for an `X | None` struct field to hold, not an authoring error."""
    assert storage_key(None) == (1,)
    assert storage_key(None) == storage_key(None)
    hash(storage_key(None))


def test_an_option_field_none_vs_set_produces_a_different_struct_key() -> None:
    @contracttype
    class WithOptional:
        owner: Address | None

    unset_a = WithOptional(owner=None)
    unset_b = WithOptional(owner=None)
    set_ = WithOptional(owner=Address(ADDRESS_A))
    assert storage_key(unset_a) == storage_key(unset_b)
    assert storage_key(unset_a) != storage_key(set_)


# --- unions and int enums (M1-E2) --------------------------------------------


def test_a_union_key_is_its_held_vecs_key_and_nothing_new() -> None:
    """The union arm DELEGATES: one definition of the vec shape, not a second
    copy of it. §B.1's byte-verified shape is `ScVec[Symbol, payload...]`, so a
    union and the hand-built `Vec` of the same elements are ONE key -- which is
    also what makes a union key found by an equal-but-distinct rebuild.
    """
    rect = Shape.Rect(U32(1), U32(2))
    assert storage_key(rect) == storage_key(rect._vec)
    assert storage_key(rect) == ("vec", ((15, b"Rect"), (3, 1), (3, 2)))
    assert storage_key(Shape.Rect(U32(1), U32(2))) == storage_key(Shape.Rect(U32(1), U32(2)))
    hash(storage_key(Shape.Rect(U32(1), U32(2))))


def test_a_unit_variant_key_is_a_one_element_vec_not_a_bare_symbol() -> None:
    """The shape fact the whole sub-plan rests on, at the key level: a unit
    variant is NOT its name."""
    assert storage_key(Shape.Empty) == ("vec", (storage_key(Symbol("Empty")),))
    assert storage_key(Shape.Empty) != storage_key(Symbol("Empty"))


def test_union_keys_separate_by_case_and_by_payload() -> None:
    assert storage_key(Shape.Circle(U32(1))) != storage_key(Shape.Circle(U32(2)))
    assert storage_key(Shape.Circle(U32(1))) != storage_key(Shape.Empty)
    assert storage_key(Shape.Circle(U32(1))) != storage_key(Shape.Rect(U32(1), U32(2)))


def test_a_union_is_never_keyed_as_a_map() -> None:
    """Ruling E9: neither new kind is a dataclass, so the `Struct` arm -- which
    would produce a `("map", ...)` key -- is never reached. The union arm sits
    ABOVE that line as well (belt and braces), and this is what pins it.
    """
    for value in (Shape.Empty, Shape.Rect(U32(1), U32(2))):
        key = storage_key(value)
        assert isinstance(key, tuple)
        assert key[0] == "vec"


def test_an_int_enum_key_is_exactly_a_u32_key() -> None:
    """No arm at all: an int enum IS a bare `U32` on chain, so it falls through
    to the scalar `(_SCVAL_RANK, _cmp_payload())` line for free."""
    assert storage_key(Color.Red) == storage_key(U32(0))
    assert storage_key(Color.Green) == storage_key(U32(1))
    assert storage_key(Color.Red) != storage_key(Color.Green)
    hash(storage_key(Color.Red))
