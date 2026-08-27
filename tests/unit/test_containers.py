import copy
from functools import cmp_to_key
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from serpent.types import (
    I32,
    I64,
    I128,
    U32,
    U64,
    U128,
    Bool,
    Bytes,
    Bytes32,
    Duration,
    Map,
    String,
    Symbol,
    Timepoint,
    Vec,
)
from serpent.types._ordering import ChainValue, val_cmp

# --- val_cmp ------------------------------------------------------------------


def test_scval_rank_not_tag_rank() -> None:
    # Host orders by ScValType (Bytes=13 < Symbol=15), NOT by tag
    # (SymbolSmall=14 < BytesObject=72). Verified against obj_cmp @ v28.0.2.
    assert val_cmp(Bytes(b"\xff"), Symbol("a")) < 0
    assert val_cmp(Symbol("a"), Bytes(b"\xff")) > 0


def test_full_rank_table() -> None:
    # One instance of every M1-A type, in ScValType order. Void=1 and Error=2 are
    # not chain value types in M1-A; U256=11/I256=12 are deferred to M2;
    # Address=18 arrives in Task 8 and appends to the end of this list.
    ordered: list[ChainValue] = [
        Bool(False),      # 0
        U32(5),           # 3
        I32(5),           # 4
        U64(5),           # 5
        I64(5),           # 6
        Timepoint(5),     # 7
        Duration(5),      # 8
        U128(5),          # 9
        I128(5),          # 10
        Bytes(b"\xff"),   # 13
        String("zzz"),    # 14
        Symbol("a"),      # 15
        Vec(U32),         # 16
        Map(U32, U32),    # 17
    ]
    ranks = [v._SCVAL_RANK for v in ordered]
    assert ranks == [0, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 15, 16, 17]
    for i, left in enumerate(ordered):
        for j, right in enumerate(ordered):
            if i == j:
                continue
            # Payloads deliberately disagree with rank order, so only the rank
            # can be producing these answers.
            assert (val_cmp(left, right) < 0) is (i < j)


def test_val_cmp_within_a_type() -> None:
    assert val_cmp(U32(1), U32(2)) < 0
    assert val_cmp(U32(2), U32(1)) > 0
    assert val_cmp(U32(1), U32(1)) == 0
    assert val_cmp(I32(-1), I32(0)) < 0
    assert val_cmp(Bool(False), Bool(True)) < 0
    assert val_cmp(Bytes(b"a"), Bytes(b"b")) < 0
    assert val_cmp(String("a"), String("b")) < 0
    assert val_cmp(Symbol("a"), Symbol("b")) < 0
    assert val_cmp(Bytes32(b"\x00" * 32), Bytes(b"\x01")) < 0


def test_val_cmp_same_payload_different_rank_orders_by_rank() -> None:
    # Identical payloads: only the ScValType rank can break the tie.
    assert val_cmp(U32(1), U64(1)) < 0            # 3 < 5
    assert val_cmp(I128(1), U128(1)) > 0          # 10 > 9
    assert val_cmp(Timepoint(5), Duration(5)) < 0  # 7 < 8
    assert val_cmp(Bytes(b"a"), String("a")) < 0   # 13 < 14, same UTF-8 payload
    assert val_cmp(String("a"), Symbol("a")) < 0   # 14 < 15, same UTF-8 payload
    assert val_cmp(Bool(True), U32(1)) < 0         # 0 < 3, both payload 1


def test_val_cmp_is_a_total_order_on_the_supported_set() -> None:
    values: list[ChainValue] = [U32(1), U32(2), I32(1), Bytes(b"a"), Symbol("a"), Bool(True)]
    for a in values:
        assert val_cmp(a, a) == 0
        for b in values:
            assert val_cmp(a, b) == -val_cmp(b, a)


def test_val_cmp_phase_0_map_golden() -> None:
    assert val_cmp(Symbol("counter_limit"), Symbol("display_name")) < 0


def test_val_cmp_rejects_non_chain_values() -> None:
    with pytest.raises(TypeError, match="chain value"):
        val_cmp(U32(1), 1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="chain value"):
        val_cmp("a", Symbol("a"))  # type: ignore[arg-type]


def test_val_cmp_defers_container_payload_comparison() -> None:
    # Different ranks never touch the payload, so containers still order against
    # every scalar ...
    assert val_cmp(U32(1), Vec(U32)) < 0
    assert val_cmp(Map(U32, U32), Vec(U32)) > 0
    # ... but comparing two containers of the same rank needs host semantics we
    # have not verified yet.
    with pytest.raises(NotImplementedError, match="sub-plan B"):
        val_cmp(Vec(U32), Vec(U32))
    with pytest.raises(NotImplementedError, match="sub-plan B"):
        val_cmp(Map(U32, U32), Map(U32, U32))


# --- Vec ----------------------------------------------------------------------


def test_vec_construction_and_annotation() -> None:
    empty: Vec[U32] = Vec(U32)
    assert len(empty) == 0 and list(empty) == []
    filled: Vec[U32] = Vec(U32, [U32(1), U32(2)])
    assert len(filled) == 2 and list(filled) == [U32(1), U32(2)]
    assert Vec(U32, [U32(1)]).element_type is U32
    with pytest.raises(TypeError):
        # mypy widens T to the common protocol here, so only the runtime check
        # catches the mixed element type -- which is exactly why it exists.
        Vec(U32, [I32(1)])
    with pytest.raises(TypeError):
        Vec(7)  # type: ignore[arg-type]


def test_vec_element_type_is_enforced() -> None:
    v = Vec(U32)
    with pytest.raises(TypeError, match="element"):
        v.push_back(I32(1))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="element"):
        v.push_front(Symbol("a"))  # type: ignore[arg-type]
    v.push_back(U32(1))
    with pytest.raises(TypeError, match="element"):
        v.put(0, U64(1))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="element"):
        v.insert(0, 5)  # type: ignore[arg-type]
    # A subclass of the element type is still that ScVal case, so it is accepted.
    b: Vec[Bytes] = Vec(Bytes)
    b.push_back(Bytes32(b"\x00" * 32))
    assert len(b) == 1


def test_vec_push_and_pop() -> None:
    v = Vec(U32)
    v.push_back(U32(2))
    v.push_back(U32(3))
    v.push_front(U32(1))
    assert list(v) == [U32(1), U32(2), U32(3)]
    assert v.pop_back() == U32(3)
    assert v.pop_front() == U32(1)
    assert list(v) == [U32(2)]
    assert v.pop_back() == U32(2)
    with pytest.raises(IndexError):
        v.pop_back()
    with pytest.raises(IndexError):
        v.pop_front()


def test_vec_get_put_del_insert() -> None:
    v = Vec(U32, [U32(1), U32(2), U32(3)])
    assert v.get(0) == U32(1) and v.get(2) == U32(3)
    v.put(1, U32(9))
    assert list(v) == [U32(1), U32(9), U32(3)]
    v.del_(1)
    assert list(v) == [U32(1), U32(3)]
    v.insert(1, U32(2))
    assert list(v) == [U32(1), U32(2), U32(3)]
    v.insert(3, U32(4))  # at len() == append
    assert list(v) == [U32(1), U32(2), U32(3), U32(4)]


def test_vec_out_of_bounds_raises_index_error() -> None:
    v = Vec(U32, [U32(1)])
    for bad in (1, 2, -1):
        with pytest.raises(IndexError):
            v.get(bad)
        with pytest.raises(IndexError):
            v.put(bad, U32(0))
        with pytest.raises(IndexError):
            v.del_(bad)
    with pytest.raises(IndexError):
        v.insert(2, U32(0))
    with pytest.raises(IndexError):
        v.insert(-1, U32(0))
    with pytest.raises(IndexError):
        Vec(U32).get(0)


def test_vec_append_and_slice() -> None:
    v = Vec(U32, [U32(1), U32(2)])
    v.append(Vec(U32, [U32(3), U32(4)]))  # host vec_append concatenates
    assert list(v) == [U32(1), U32(2), U32(3), U32(4)]
    assert list(v.slice(1, 3)) == [U32(2), U32(3)]
    assert len(v.slice(0, 0)) == 0
    assert isinstance(v.slice(0, 1), Vec)
    assert v.slice(0, 4) == v and v.slice(0, 4) is not v
    for lo, hi in ((0, 5), (3, 2), (-1, 2), (5, 5)):
        with pytest.raises(IndexError):
            v.slice(lo, hi)
    with pytest.raises(TypeError):
        v.append(Vec(I32, [I32(1)]))  # type: ignore[arg-type,list-item]
    with pytest.raises(TypeError):
        v.append([U32(5)])  # type: ignore[arg-type]


def test_vec_first_index_of() -> None:
    v = Vec(U32, [U32(7), U32(8), U32(7)])
    assert v.first_index_of(U32(7)) == U32(0)
    assert v.first_index_of(U32(8)) == U32(1)
    assert v.first_index_of(U32(9)) is None
    with pytest.raises(TypeError, match="element"):
        v.first_index_of(I32(7))  # type: ignore[arg-type]


def test_vec_equality_and_hashability() -> None:
    assert Vec(U32, [U32(1)]) == Vec(U32, [U32(1)])
    assert (Vec(U32, [U32(1)]) == Vec(U32, [U32(2)])) is False
    assert (Vec(U32, [U32(1)]) == Vec(U32)) is False
    # Two empty vecs are the same empty ScVec, whatever their element types.
    assert Vec(U32) == Vec(I32)
    assert (Vec(U32, [U32(1)]) == U32(1)) is False
    assert (Vec(U32) == "not a vec") is False
    with pytest.raises(TypeError):
        hash(Vec(U32))  # mutable containers are unhashable


def test_vec_copy_is_independent() -> None:
    v = Vec(U32, [U32(1)])
    shallow = copy.copy(v)
    deep = copy.deepcopy(v)
    shallow.push_back(U32(2))
    deep.push_back(U32(3))
    assert list(v) == [U32(1)]
    assert list(shallow) == [U32(1), U32(2)]
    assert list(deep) == [U32(1), U32(3)]
    assert type(shallow) is Vec and type(deep) is Vec


def test_vec_repr() -> None:
    assert repr(Vec(U32, [U32(1)])) == "Vec(U32, [U32(1)])"
    assert repr(Vec(U32)) == "Vec(U32, [])"


# --- Map ----------------------------------------------------------------------


def test_map_basic_operations() -> None:
    m: Map[Symbol, U32] = Map(Symbol, U32)
    assert len(m) == 0
    m.set(Symbol("b"), U32(2))
    m.set(Symbol("a"), U32(1))
    assert len(m) == 2
    assert m.get(Symbol("a")) == U32(1)
    assert m.has(Symbol("a")) and not m.has(Symbol("zz"))
    m.set(Symbol("a"), U32(9))  # replace, not duplicate
    assert len(m) == 2 and m.get(Symbol("a")) == U32(9)
    m.del_(Symbol("a"))
    assert len(m) == 1 and not m.has(Symbol("a"))
    assert m.key_type is Symbol and m.value_type is U32


def test_map_missing_key_raises_key_error() -> None:
    m: Map[Symbol, U32] = Map(Symbol, U32)
    with pytest.raises(KeyError):
        m.get(Symbol("nope"))
    with pytest.raises(KeyError):
        m.del_(Symbol("nope"))
    m.set(Symbol("a"), U32(1))
    with pytest.raises(KeyError):
        m.get(Symbol("b"))


def test_map_iterates_in_val_cmp_order() -> None:
    m: Map[Symbol, U32] = Map(Symbol, U32)
    for name in ("display_name", "counter_limit", "admin"):
        m.set(Symbol(name), U32(1))
    # Phase 0 golden: counter_limit sorts before display_name.
    assert [k.text for k in m] == ["admin", "counter_limit", "display_name"]
    ordered_keys = m.keys()
    assert [k.text for k in ordered_keys] == ["admin", "counter_limit", "display_name"]


def test_map_keys_and_values_are_vecs_in_key_order() -> None:
    m: Map[U32, Symbol] = Map(U32, Symbol)
    m.set(U32(2), Symbol("two"))
    m.set(U32(1), Symbol("one"))
    keys = m.keys()
    values = m.values()
    assert isinstance(keys, Vec) and isinstance(values, Vec)
    assert list(keys) == [U32(1), U32(2)]
    assert list(values) == [Symbol("one"), Symbol("two")]
    assert keys.element_type is U32 and values.element_type is Symbol


def test_map_positional_access() -> None:
    m: Map[U32, Symbol] = Map(U32, Symbol)
    m.set(U32(2), Symbol("two"))
    m.set(U32(1), Symbol("one"))
    assert m.key_by_pos(0) == U32(1) and m.val_by_pos(0) == Symbol("one")
    assert m.key_by_pos(1) == U32(2) and m.val_by_pos(1) == Symbol("two")
    for bad in (2, -1):
        with pytest.raises(IndexError):
            m.key_by_pos(bad)
        with pytest.raises(IndexError):
            m.val_by_pos(bad)


def test_map_allows_mixed_type_keys_ordered_by_rank() -> None:
    # The host allows heterogeneous keys and val_cmp totally orders them, so Map
    # does not enforce the key type at runtime (Vec does -- see the docstrings).
    m: Map[Any, Any] = Map(U32, U32)
    m.set(Symbol("a"), U32(3))
    m.set(Bytes(b"a"), U32(2))
    m.set(U32(97), U32(1))
    # Same payload (b"a"/97), different ScValType: rank decides.
    assert [type(k).__name__ for k in m] == ["U32", "Bytes", "Symbol"]
    assert [v.value for v in m.values()] == [1, 2, 3]
    assert m.get(Symbol("a")) == U32(3)
    assert m.has(Bytes(b"a")) and not m.has(Bytes(b"b"))


def test_map_equality_and_hashability() -> None:
    a: Map[Symbol, U32] = Map(Symbol, U32)
    b: Map[Symbol, U32] = Map(Symbol, U32)
    assert a == b
    a.set(Symbol("k"), U32(1))
    assert (a == b) is False
    b.set(Symbol("k"), U32(1))
    assert a == b
    b.set(Symbol("k"), U32(2))
    assert (a == b) is False
    assert (a == Vec(U32)) is False
    assert (a == "not a map") is False
    with pytest.raises(TypeError):
        hash(Map(U32, U32))


def test_map_copy_is_independent() -> None:
    m: Map[U32, U32] = Map(U32, U32)
    m.set(U32(1), U32(1))
    shallow = copy.copy(m)
    deep = copy.deepcopy(m)
    shallow.set(U32(2), U32(2))
    deep.set(U32(3), U32(3))
    assert len(m) == 1 and len(shallow) == 2 and len(deep) == 2
    assert type(shallow) is Map and type(deep) is Map


def test_map_repr() -> None:
    m: Map[U32, U32] = Map(U32, U32)
    m.set(U32(1), U32(2))
    assert repr(m) == "Map(U32, U32, [(U32(1), U32(2))])"


def test_container_ranks_and_deferred_payload() -> None:
    assert Vec._SCVAL_RANK == 16
    assert Map._SCVAL_RANK == 17
    with pytest.raises(NotImplementedError, match="sub-plan B"):
        Vec(U32)._cmp_payload()
    with pytest.raises(NotImplementedError, match="sub-plan B"):
        Map(U32, U32)._cmp_payload()


# --- properties ----------------------------------------------------------------


@given(st.lists(st.integers(min_value=0, max_value=2**32 - 1), unique=True, max_size=25))
def test_map_iteration_equals_sorted_by_val_cmp(raw_keys: list[int]) -> None:
    m: Map[U32, U32] = Map(U32, U32)
    for k in raw_keys:
        m.set(U32(k), U32(0))
    expected = sorted((U32(k) for k in raw_keys), key=cmp_to_key(val_cmp))
    assert list(m) == expected
    assert list(m.keys()) == expected


@given(
    st.lists(
        st.tuples(
            st.sampled_from(
                ["push_back", "push_front", "pop_back", "pop_front", "insert", "del_", "put"]
            ),
            st.integers(min_value=0, max_value=6),
            st.integers(min_value=0, max_value=255),
        ),
        max_size=40,
    )
)
def test_vec_matches_a_plain_list_reference_model(
    ops: list[tuple[str, int, int]],
) -> None:
    v: Vec[U32] = Vec(U32)
    model: list[int] = []
    for name, index, value in ops:
        if name == "push_back":
            v.push_back(U32(value))
            model.append(value)
        elif name == "push_front":
            v.push_front(U32(value))
            model.insert(0, value)
        elif name == "pop_back":
            if model:
                assert v.pop_back() == U32(model.pop())
            else:
                with pytest.raises(IndexError):
                    v.pop_back()
        elif name == "pop_front":
            if model:
                assert v.pop_front() == U32(model.pop(0))
            else:
                with pytest.raises(IndexError):
                    v.pop_front()
        elif name == "insert":
            if 0 <= index <= len(model):
                v.insert(index, U32(value))
                model.insert(index, value)
            else:
                with pytest.raises(IndexError):
                    v.insert(index, U32(value))
        elif name == "del_":
            if index < len(model):
                v.del_(index)
                del model[index]
            else:
                with pytest.raises(IndexError):
                    v.del_(index)
        else:  # put
            if index < len(model):
                v.put(index, U32(value))
                model[index] = value
            else:
                with pytest.raises(IndexError):
                    v.put(index, U32(value))
        assert len(v) == len(model)
        assert [x.value for x in v] == model
        if model:
            assert v.get(0) == U32(model[0])
            assert v.first_index_of(U32(model[0])) == U32(model.index(model[0]))
