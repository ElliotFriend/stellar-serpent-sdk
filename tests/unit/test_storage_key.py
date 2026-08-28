"""`storage_key` (M-2): ONE definition of storage/map key equality across tiers.

S13's motivating failure lives in `tests/harness/objects.py:36-49` (the module
docstring there): a map/storage key must be compared BY VALUE, never by handle,
because a contract builds a fresh struct key on every invocation
(`BalanceKey(owner=Address(...))`). `storage_key` is the value-level twin of
`objects.py`'s word-level `map_key`, so this file is the tier-1-only regression
net -- no wasm, no harness, no host at all.
"""

from serpent.decorators import contracttype
from serpent.types import U32, Address, Bool, Map, Symbol, Vec
from serpent.types._storage_key import storage_key

ADDRESS_A = "GA7QYNF7SOWQ3GLR2BGMZEHXAVIRZA4KVWLTJJFC7MGXUA74P7UJVSGZ"
ADDRESS_B = "GAAQEAYEAUDAOCAJBIFQYDIOB4IBCEQTCQKRMFYYDENBWHA5DYPSABOV"


@contracttype
class BalanceKey:
    owner: Address


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
