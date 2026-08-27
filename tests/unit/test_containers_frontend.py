"""Tests for Task 7b: container/struct recognition and MJ-13 subscripts.

Four obligations, mirroring the task brief:

* **Every authored container/struct row** (`recognize.RECOGNIZED`'s
  `family == "container"` half) lowers to the exact IR shape dossier SS C.4
  describes -- `MakeVec`/`MakeMap`/`MakeStruct`/`FieldGet` for the
  construction and field rows, one `HostCall` per reader row -- with a
  completeness assertion in both directions (MJ-3) and a differential check
  that every method row names a REAL tier-1 method (`serpent.types`), so the
  compiler can never accept a surface the oracle cannot run.
* **MJ-15's literal-key ordering**: `MakeMap` pairs pre-sorted by
  `(scval_rank, val_cmp)` when C can totally order them, `all_static=False`
  (map_new + map_put) when it cannot -- struct keys per E3.
* **P7's struct field sort**, owned by C and asserted as a golden.
* **MJ-13's four subscript cases** (`expr.py`): `Bytes[i]` -> `bytes_get`,
  `Bytes[a:b]`/`Vec[a:b]` -> the `.slice(lo, hi)` reject, the annotation-only
  generic form in a value position, and the negative-LITERAL reject (D6).

F.1.8's `Bytes`-family asymmetry gets its own row on both sides: a `BytesN`
value is accepted where the declared element type is `Bytes` (tier 1's
`isinstance` check accepts the subclass), and a `Bytes` value is REJECTED
where the declared type is `Bytes32` -- reproducing tier 1's strictness rather
than the host's permissiveness.
"""

from __future__ import annotations

import ast

import pytest

from serpent._host import functions_by_name
from serpent.compiler import codes
from serpent.compiler.ctx import AliasTable, FuncCtx, SlotTable
from serpent.compiler.diagnostics import Diagnostic, Diagnostics, Loc
from serpent.compiler.expr import check_expr
from serpent.compiler.ir import (
    Const,
    FieldGet,
    HostCall,
    IRExpr,
    MakeMap,
    MakeStruct,
    MakeVec,
    ParamRef,
)
from serpent.compiler.loader import LoadedModule, load_module
from serpent.compiler.recognize import (
    BYTES_METHODS,
    CONTAINER_HOST_FN_TARGETS,
    MAP_METHODS,
    RECOGNIZED,
    UNREACHED_CONTAINER_HOST_FNS,
    VEC_METHODS,
    SurfaceKind,
    recognize_attribute,
    recognize_call,
    static_map_order,
)
from serpent.compiler.types_ import Ty, TyTag
from serpent.types import Bytes, Map, Vec

PATH = "contract.py"

_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

_SOURCE = '''
"""Container-recognition fixture."""
from serpent import (
    U32,
    Address,
    Bytes,
    Bytes32,
    Env,
    Map,
    Symbol,
    Vec,
    contract,
    contracttype,
)


@contracttype
class Balance:
    owner: Address
    amount: U32


@contracttype
class BalanceKey:
    owner: Address


@contract
class Go:
    def go(
        self,
        env: Env,
        v: Vec[U32],
        vb: Vec[Bytes],
        vb32: Vec[Bytes32],
        m: Map[Symbol, U32],
        mk: Map[BalanceKey, U32],
        mv: Map[Symbol, Vec[U32]],
        b: Bytes,
        b32: Bytes32,
        bal: Balance,
        amt: U32,
        sym: Symbol,
        addr: Address,
    ) -> U32:
        return amt
'''

#: `(name, Ty)` in declaration order, `self`/`env` already dropped (SS C.3).
_PARAMS: list[tuple[str, Ty]] = [
    ("v", Ty.Vec(Ty.U32)),
    ("vb", Ty.Vec(Ty.Bytes)),
    ("vb32", Ty.Vec(Ty.BytesN(32))),
    ("m", Ty.Map(Ty.Symbol, Ty.U32)),
    ("mk", Ty.Map(Ty.Struct("BalanceKey"), Ty.U32)),
    ("mv", Ty.Map(Ty.Symbol, Ty.Vec(Ty.U32))),
    ("b", Ty.Bytes),
    ("b32", Ty.BytesN(32)),
    ("bal", Ty.Struct("Balance")),
    ("amt", Ty.U32),
    ("sym", Ty.Symbol),
    ("addr", Ty.Address),
]

_BYTES32_LITERAL = 'Bytes32(b"a" * 32)'


def _loaded() -> LoadedModule:
    loaded = load_module(_SOURCE, PATH)
    assert not loaded.diagnostics, loaded.diagnostics.diagnostics
    return loaded


_LOADED = _loaded()


def _ctx() -> FuncCtx:
    loc = Loc.whole_file(PATH)
    params = [(name, ty, loc) for name, ty in _PARAMS]
    reserved = {name: "a parameter" for name, _ in _PARAMS}
    return FuncCtx(
        loaded=_LOADED,
        sink=Diagnostics(),
        params=params,
        locals=SlotTable(reserved=reserved),
        loop_depth=0,
        return_ty=Ty.U32,
        alias_sets=AliasTable(),
        fn_name="go",
        path=PATH,
    )


def _parse(source: str) -> ast.expr:
    return ast.parse(source, mode="eval").body


def _parse_call(source: str) -> ast.Call:
    node = _parse(source)
    assert isinstance(node, ast.Call), source
    return node


def _parse_attr(source: str) -> ast.Attribute:
    node = _parse(source)
    assert isinstance(node, ast.Attribute), source
    return node


def _ok_call(source: str) -> IRExpr:
    ctx = _ctx()
    node = recognize_call(_parse_call(source), ctx)
    assert node is not None, f"{source!r} was not recognized at all"
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    return node


def _reject_call(source: str) -> Diagnostic:
    ctx = _ctx()
    node = recognize_call(_parse_call(source), ctx)
    assert node is not None, f"{source!r} was not recognized at all"
    assert len(ctx.sink) == 1, [(d.code, d.message) for d in ctx.sink.diagnostics]
    return ctx.sink.diagnostics[0]


def _ok_attr(source: str) -> IRExpr:
    ctx = _ctx()
    node = recognize_attribute(_parse_attr(source), ctx)
    assert node is not None, f"{source!r} was not recognized at all"
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    return node


def _reject_attr(source: str) -> Diagnostic:
    ctx = _ctx()
    node = recognize_attribute(_parse_attr(source), ctx)
    assert node is not None, f"{source!r} was not recognized at all"
    assert len(ctx.sink) == 1, [(d.code, d.message) for d in ctx.sink.diagnostics]
    return ctx.sink.diagnostics[0]


def _ok_expr(source: str) -> IRExpr:
    """`check_expr` on a whole expression -- the entry point `expr.py` owns
    (the MJ-13 subscript rows)."""
    ctx = _ctx()
    node = check_expr(_parse(source), ctx)
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    return node


def _reject_expr(source: str) -> Diagnostic:
    ctx = _ctx()
    check_expr(_parse(source), ctx)
    assert len(ctx.sink) == 1, [(d.code, d.message) for d in ctx.sink.diagnostics]
    return ctx.sink.diagnostics[0]


def _assert_reject(diag: Diagnostic, code: str, detail_substring: str) -> None:
    """Assert `diag` is `code`, carries its registry intent as the message's
    PREFIX, and carries `detail_substring` in the part AFTER that prefix (the
    same discipline `test_recognize_env.py` adopted in Task 7a's fix round:
    asserting on the whole message would pass on the unconditional intent
    prefix alone)."""
    assert diag.code == code, f"expected {code}, got {diag.code}: {diag.message}"
    intent = _INTENT[code]
    assert diag.message.startswith(intent), (
        f"{code}: message does not start with its registry intent\n"
        f"  message: {diag.message}\n  intent:  {intent}"
    )
    detail = diag.message[len(intent) :]
    assert detail_substring in detail, (
        f"{code}: {detail_substring!r} not in the code-specific detail {detail!r} "
        f"(full message: {diag.message})"
    )
    if code.startswith("SPT1"):
        assert diag.help, f"{code}: SPT1xxx diagnostics must carry help (F.2.11)"


def _host_call(node: IRExpr) -> HostCall:
    assert isinstance(node, HostCall), node
    return node


# --- the table itself (MJ-3) --------------------------------------------------


_EXPECTED_CONTAINER_ROWS: frozenset[str] = frozenset(
    {
        "vec.new",
        "vec.push_back",
        "vec.push_front",
        "vec.pop_back",
        "vec.pop_front",
        "vec.get",
        "vec.put",
        "vec.del_",
        "vec.insert",
        "vec.append",
        "vec.slice",
        "vec.first_index_of",
        "map.new",
        "map.set",
        "map.get",
        "map.has",
        "map.del_",
        "map.keys",
        "map.values",
        "map.key_by_pos",
        "map.val_by_pos",
        "bytes.slice",
        "struct.new",
        "struct.field",
    }
)


def _container_rows() -> dict[str, object]:
    return {key: spec for key, spec in RECOGNIZED.items() if spec.family == "container"}


def test_recognized_has_every_expected_container_row() -> None:
    assert set(_container_rows()) == _EXPECTED_CONTAINER_ROWS


def test_container_rows_are_internally_consistent() -> None:
    for key, spec in RECOGNIZED.items():
        if spec.family != "container":
            continue
        assert spec.kind is not SurfaceKind.REJECT, key
        assert spec.reject_code is None, key
        assert spec.missing_value_code is None, key
        assert spec.host_fns, key
        for fn_name in spec.host_fns:
            assert fn_name in functions_by_name, (key, fn_name)


#: SS C.4's Vec/Map/Bytes/struct host-function inventory, named directly from
#: the dossier table -- the independent oracle the completeness assertion
#: checks the table against, so a typo cannot mark itself "complete".
_DOSSIER_C4_CONTAINER_INVENTORY: frozenset[str] = frozenset(
    {
        # Vec ops
        "vec_new",
        "vec_put",
        "vec_get",
        "vec_del",
        "vec_len",
        "vec_push_front",
        "vec_pop_front",
        "vec_push_back",
        "vec_pop_back",
        "vec_front",
        "vec_back",
        "vec_insert",
        "vec_append",
        "vec_slice",
        "vec_first_index_of",
        "vec_last_index_of",
        "vec_new_from_linear_memory",
        # Map ops
        "map_new",
        "map_put",
        "map_get",
        "map_del",
        "map_len",
        "map_has",
        "map_key_by_pos",
        "map_val_by_pos",
        "map_keys",
        "map_values",
        "map_new_from_linear_memory",
        # Bytes ops
        "bytes_get",
        "bytes_len",
        "bytes_slice",
        "string_len",
        "symbol_len",
        # struct construction / field read
        "symbol_new_from_linear_memory",
    }
)


def test_container_targets_are_real_host_functions() -> None:
    for name in CONTAINER_HOST_FN_TARGETS:
        assert name in functions_by_name, name
    for name in UNREACHED_CONTAINER_HOST_FNS:
        assert name in functions_by_name, name


def test_container_targets_plus_unreached_are_exactly_the_dossier_inventory() -> None:
    """Both directions (MJ-3): every SS C.4 container target is either reached
    by a row or explicitly listed as unreached WITH a reason, and no row
    reaches a host function outside the inventory."""
    assert CONTAINER_HOST_FN_TARGETS.isdisjoint(UNREACHED_CONTAINER_HOST_FNS)
    assert (
        CONTAINER_HOST_FN_TARGETS | frozenset(UNREACHED_CONTAINER_HOST_FNS)
    ) == _DOSSIER_C4_CONTAINER_INVENTORY
    for name, reason in UNREACHED_CONTAINER_HOST_FNS.items():
        assert reason.strip(), name


def test_no_authoring_surface_targets_stay_unreached() -> None:
    """The ruled trio: `vec_front`/`vec_back`/`vec_last_index_of` have no
    tier-1 method at all, so no row may target them."""
    for name in ("vec_front", "vec_back", "vec_last_index_of"):
        assert name in UNREACHED_CONTAINER_HOST_FNS
        assert name not in CONTAINER_HOST_FN_TARGETS


_TIER1_CLASSES: dict[str, type] = {"vec": Vec, "map": Map, "bytes": Bytes}


@pytest.mark.parametrize(
    ("method", "row"),
    sorted(
        [(name, row) for name, row in VEC_METHODS.items()]
        + [(name, row) for name, row in MAP_METHODS.items()]
        + [(name, row) for name, row in BYTES_METHODS.items()]
    ),
)
def test_every_method_row_names_a_real_tier1_method(method: str, row: str) -> None:
    """Differential (A18): the compiler must never recognize a surface the
    tier-1 oracle has no method for -- that is an oracle-unrunnable accept,
    the exact shape MJ-1's `len()` scoping ruling avoided.

    `Bytes.slice` is the one known gap: it is a RULED tier-1 addition that
    lands in Task 8 (decisions.md 2026-08-27, plan-review ruling "Bytes.slice
    added to the tier-1 surface"), so this row is xfail(strict=True) until
    then -- see `test_bytes_slice_row_awaits_task_8`.
    """
    if row == "bytes.slice":
        pytest.skip("covered by test_bytes_slice_row_awaits_task_8 (xfail until Task 8)")
    assert row in RECOGNIZED, row
    cls = _TIER1_CLASSES[row.split(".")[0]]
    assert callable(getattr(cls, method, None)), f"{cls.__name__} has no method {method}"


@pytest.mark.xfail(
    strict=True,
    reason="Bytes.slice is a ruled tier-1 addition landing in Task 8 (E18/MJ-1); "
    "flip this to a plain assertion when it lands",
)
def test_bytes_slice_row_awaits_task_8() -> None:
    assert callable(getattr(Bytes, "slice", None))


def test_reader_rows_match_the_host_arity() -> None:
    """Every emitted `HostCall` must pass exactly the host function's arity --
    the receiver plus the row's own arguments. (The mutator rows' arities are
    asserted the same way in `test_alias.py`, where their lowering lives.)"""
    cases = [
        ("v.get(amt)", "vec_get"),
        ("v.slice(amt, amt)", "vec_slice"),
        ("v.first_index_of(amt)", "vec_first_index_of"),
        ("m.get(sym)", "map_get"),
        ("m.has(sym)", "map_has"),
        ("m.keys()", "map_keys"),
        ("m.values()", "map_values"),
        ("m.key_by_pos(amt)", "map_key_by_pos"),
        ("m.val_by_pos(amt)", "map_val_by_pos"),
    ]
    for source, fn_name in cases:
        node = _host_call(_ok_call(source))
        assert node.fn_name == fn_name, source
        assert len(node.args) == functions_by_name[fn_name].arity, source


# --- Vec construction (D2/A13) ------------------------------------------------


def test_vec_empty_construction() -> None:
    node = _ok_call("Vec(U32)")
    assert isinstance(node, MakeVec)
    assert node.ty == Ty.Vec(Ty.U32)
    assert node.elem_ty == Ty.U32
    assert node.items == ()
    # Nothing to lay out in linear memory: D emits `vec_new`.
    assert node.all_static is False


def test_vec_construction_with_static_items() -> None:
    node = _ok_call("Vec(U32, [U32(1), U32(2)])")
    assert isinstance(node, MakeVec)
    assert node.ty == Ty.Vec(Ty.U32)
    assert [item.ty for item in node.items] == [Ty.U32, Ty.U32]
    assert all(isinstance(item, Const) for item in node.items)
    assert node.all_static is True


def test_vec_construction_with_a_runtime_item_is_not_static() -> None:
    node = _ok_call("Vec(U32, [amt])")
    assert isinstance(node, MakeVec)
    assert node.all_static is False
    (item,) = node.items
    assert isinstance(item, ParamRef) and item.name == "amt"


def test_vec_construction_coerces_bare_literals_to_the_element_type() -> None:
    node = _ok_call("Vec(U32, [1, 2])")
    assert isinstance(node, MakeVec)
    assert [item.ty for item in node.items] == [Ty.U32, Ty.U32]


def test_vec_construction_rejects_a_wrong_element_type() -> None:
    _assert_reject(_reject_call('Vec(U32, [Symbol("x")])'), "SPT3018", "U32")


def test_vec_construction_requires_a_list_display() -> None:
    diag = _reject_call("Vec(U32, amt)")
    _assert_reject(diag, "SPT1037", "list display")
    assert "Vec(U32, [" in (diag.help or "")


def test_vec_construction_rejects_a_missing_type_argument() -> None:
    _assert_reject(_reject_call("Vec()"), "SPT3020", "element_type")


def test_vec_construction_rejects_a_non_name_type_argument() -> None:
    _assert_reject(_reject_call("Vec(3, [])"), "SPT3013", "type argument")


def test_vec_of_struct_elements() -> None:
    node = _ok_call("Vec(Balance)")
    assert isinstance(node, MakeVec)
    assert node.elem_ty == Ty.Struct("Balance")


# --- F.1.8: the Bytes-family asymmetry ---------------------------------------


def test_bytes_n_value_is_accepted_where_the_element_type_is_bytes() -> None:
    """Tier 1's element check is `isinstance`, so `Vec(Bytes)` accepts a
    `Bytes32` (containers.py's own docstring)."""
    node = _ok_call(f"Vec(Bytes, [{_BYTES32_LITERAL}])")
    assert isinstance(node, MakeVec)
    assert node.elem_ty == Ty.Bytes


def test_bytes_value_is_rejected_where_the_element_type_is_bytes32() -> None:
    """The other half of the asymmetry: a `Bytes` is NOT an instance of
    `Bytes32`, so tier 1 raises `TypeError` -- C reproduces that strictness
    (F.1.8), never the host's permissiveness."""
    _assert_reject(_reject_call('Vec(Bytes32, [Bytes(b"a")])'), "SPT3018", "BytesN(32)")


def test_vec_bytes32_lookup_rejects_a_bytes_argument() -> None:
    """F.1.8's named case: `Vec(Bytes32).first_index_of(Bytes(p))` is a tier-1
    `TypeError` even though the host would find the element."""
    _assert_reject(_reject_call('vb32.first_index_of(Bytes(b"a"))'), "SPT3018", "BytesN(32)")


def test_vec_bytes_lookup_accepts_a_bytes32_argument() -> None:
    node = _host_call(_ok_call(f"vb.first_index_of({_BYTES32_LITERAL})"))
    assert node.fn_name == "vec_first_index_of"
    assert node.ty == Ty.Option(Ty.U32)


# --- Map construction + MJ-15 literal-key ordering ---------------------------


def test_map_empty_construction() -> None:
    node = _ok_call("Map(Symbol, U32)")
    assert isinstance(node, MakeMap)
    assert node.ty == Ty.Map(Ty.Symbol, Ty.U32)
    assert node.pairs == ()
    assert node.all_static is False


def test_map_literal_keys_are_pre_sorted_by_val_cmp() -> None:
    """MJ-15: same-rank keys sort by payload, so C hands D the host's own key
    order and `map_new_from_linear_memory` can be used directly."""
    node = _ok_call('Map(Symbol, U32, [(Symbol("z"), U32(1)), (Symbol("a"), U32(2))])')
    assert isinstance(node, MakeMap)
    assert node.all_static is True
    keys = [key.py_value for key, _ in node.pairs if isinstance(key, Const)]
    assert keys == ["a", "z"]


def test_map_literal_int_keys_are_pre_sorted() -> None:
    node = _ok_call("Map(U32, U32, [(U32(5), U32(1)), (U32(1), U32(2))])")
    assert isinstance(node, MakeMap)
    assert node.all_static is True
    keys = [key.py_value for key, _ in node.pairs if isinstance(key, Const)]
    assert keys == [1, 5]


def test_map_with_a_runtime_key_keeps_source_order_and_is_not_static() -> None:
    node = _ok_call('Map(Symbol, U32, [(sym, U32(1)), (Symbol("a"), U32(2))])')
    assert isinstance(node, MakeMap)
    assert node.all_static is False
    first_key, _ = node.pairs[0]
    assert isinstance(first_key, ParamRef) and first_key.name == "sym"


def test_map_with_struct_keys_falls_back_to_map_new_plus_map_put() -> None:
    """MJ-15/E3: tier 1 cannot order a struct (no `_SCVAL_RANK`), so C must
    NOT invent an order -- `all_static=False` and the host orders them."""
    node = _ok_call("Map(BalanceKey, U32, [(BalanceKey(owner=addr), U32(1))])")
    assert isinstance(node, MakeMap)
    assert node.key_ty == Ty.Struct("BalanceKey")
    assert node.all_static is False


def test_static_map_order_declines_un_orderable_keys() -> None:
    """MJ-15's fallback branch, exercised on the helper directly: a key whose
    tier-1 ordering does not exist (a struct, E3) or which is not a literal at
    all makes the whole literal un-orderable, and C must then let the HOST
    order the map rather than inventing an order (A15)."""
    loc = Loc.whole_file(PATH)
    literal = Const(loc=loc, ty=Ty.Symbol, py_value="a")
    struct_key = MakeStruct(
        loc=loc, ty=Ty.Struct("BalanceKey"), struct_name="BalanceKey", fields=()
    )
    param_key = ParamRef(loc=loc, ty=Ty.Symbol, index=0, name="sym")
    assert static_map_order((literal,)) == (0,)
    assert static_map_order((struct_key,)) is None
    assert static_map_order((literal, param_key)) is None


def test_map_rejects_a_key_of_the_wrong_type() -> None:
    """A heterogeneous-key Map literal is not expressible in a typed
    `Map(K, V)` (the semantics-table case marked `not_expressible`)."""
    _assert_reject(_reject_call('Map(Symbol, U32, [(Bytes(b"a"), U32(1))])'), "SPT3018", "Symbol")


def test_map_rejects_a_value_of_the_wrong_type() -> None:
    _assert_reject(_reject_call('Map(Symbol, U32, [(Symbol("a"), Symbol("b"))])'), "SPT3018", "U32")


def test_map_requires_a_list_of_pairs() -> None:
    _assert_reject(_reject_call("Map(Symbol, U32, amt)"), "SPT1037", "list display")


def test_map_requires_two_element_tuples() -> None:
    _assert_reject(
        _reject_call('Map(Symbol, U32, [(Symbol("a"), U32(1), U32(2))])'),
        "SPT1037",
        "(key, value)",
    )


def test_map_construction_rejects_a_missing_value_type() -> None:
    _assert_reject(_reject_call("Map(Symbol)"), "SPT3020", "value_type")


def test_map_of_struct_values_is_supported() -> None:
    """The MJ-7 ruling: struct VALUES are ordinary on-chain shapes."""
    node = _ok_call("Map(Symbol, Balance)")
    assert isinstance(node, MakeMap)
    assert node.value_ty == Ty.Struct("Balance")


# --- struct construction (kwargs only) + P7's field sort ---------------------


def test_struct_construction_sorts_fields_as_byte_strings() -> None:
    """P7/F.1.13: `map_new_from_linear_memory` needs the key descriptors sorted
    ascending as byte strings at COMPILE time. `Balance` declares
    `owner` before `amount`, so the sort is observable."""
    node = _ok_call("Balance(owner=addr, amount=amt)")
    assert isinstance(node, MakeStruct)
    assert node.struct_name == "Balance"
    assert node.ty == Ty.Struct("Balance")
    assert [name for name, _ in node.fields] == ["amount", "owner"]
    amount_ir = node.fields[0][1]
    assert isinstance(amount_ir, ParamRef) and amount_ir.name == "amt"


def test_struct_construction_is_kwargs_only() -> None:
    _assert_reject(_reject_call("Balance(addr, amt)"), "SPT3020", "keyword")


def test_struct_construction_rejects_a_missing_field() -> None:
    _assert_reject(_reject_call("Balance(owner=addr)"), "SPT3020", "amount")


def test_struct_construction_rejects_an_unknown_field() -> None:
    _assert_reject(_reject_call("Balance(owner=addr, amount=amt, extra=amt)"), "SPT3020", "extra")


def test_struct_construction_rejects_a_field_of_the_wrong_type() -> None:
    _assert_reject(_reject_call("Balance(owner=addr, amount=sym)"), "SPT3018", "U32")


def test_struct_construction_coerces_a_literal_field_value() -> None:
    node = _ok_call("Balance(owner=addr, amount=1)")
    assert isinstance(node, MakeStruct)
    amount_ir = node.fields[0][1]
    assert isinstance(amount_ir, Const) and amount_ir.ty == Ty.U32


# --- struct field reads ------------------------------------------------------


def test_struct_field_read() -> None:
    node = _ok_attr("bal.amount")
    assert isinstance(node, FieldGet)
    assert node.field == "amount"
    assert node.struct_name == "Balance"
    assert node.ty == Ty.U32
    assert isinstance(node.obj, ParamRef) and node.obj.name == "bal"


def test_struct_field_read_of_a_struct_typed_field() -> None:
    node = _ok_attr("bal.owner")
    assert isinstance(node, FieldGet)
    assert node.ty == Ty.Address


def test_unknown_struct_field_read_is_unresolved() -> None:
    _assert_reject(_reject_attr("bal.nope"), "SPT2001", "nope")


def test_field_read_on_a_nested_construction() -> None:
    node = _ok_attr("Balance(owner=addr, amount=amt).amount")
    assert isinstance(node, FieldGet)
    assert isinstance(node.obj, MakeStruct)


def test_recognize_attribute_returns_none_for_a_non_struct_base() -> None:
    ctx = _ctx()
    assert recognize_attribute(_parse_attr("amt.whatever"), ctx) is None
    assert not ctx.sink


# --- reader rows -------------------------------------------------------------


def test_vec_get_returns_the_element_type() -> None:
    node = _host_call(_ok_call("v.get(amt)"))
    assert node.fn_name == "vec_get"
    assert node.ty == Ty.U32
    recv, index = node.args
    assert isinstance(recv, ParamRef) and recv.name == "v"
    assert isinstance(index, ParamRef) and index.name == "amt"


def test_vec_get_coerces_a_bare_int_index() -> None:
    node = _host_call(_ok_call("v.get(0)"))
    index = node.args[1]
    assert isinstance(index, Const) and index.ty == Ty.U32


def test_vec_get_rejects_a_non_u32_index() -> None:
    _assert_reject(_reject_call("v.get(sym)"), "SPT3018", "U32")


def test_vec_slice_returns_a_vec() -> None:
    node = _host_call(_ok_call("v.slice(amt, amt)"))
    assert node.fn_name == "vec_slice"
    assert node.ty == Ty.Vec(Ty.U32)


def test_vec_first_index_of_returns_an_option() -> None:
    node = _host_call(_ok_call("v.first_index_of(amt)"))
    assert node.fn_name == "vec_first_index_of"
    assert node.ty == Ty.Option(Ty.U32)


def test_map_get_returns_the_value_type() -> None:
    node = _host_call(_ok_call("m.get(sym)"))
    assert node.fn_name == "map_get"
    assert node.ty == Ty.U32


def test_map_has_is_typed_bool() -> None:
    """F.1.5: tier 1's `Map.has` answers a PLAIN python bool while the host
    returns a chain `Bool`; the compiler has one Bool type and types this
    precisely as that (the documented one-way divergence, like `len()`)."""
    node = _host_call(_ok_call("m.has(sym)"))
    assert node.fn_name == "map_has"
    assert node.ty == Ty.Bool


def test_map_keys_and_values() -> None:
    keys = _host_call(_ok_call("m.keys()"))
    assert keys.fn_name == "map_keys"
    assert keys.ty == Ty.Vec(Ty.Symbol)
    values = _host_call(_ok_call("m.values()"))
    assert values.fn_name == "map_values"
    assert values.ty == Ty.Vec(Ty.U32)


def test_map_by_pos_readers() -> None:
    key = _host_call(_ok_call("m.key_by_pos(amt)"))
    assert key.fn_name == "map_key_by_pos"
    assert key.ty == Ty.Symbol
    value = _host_call(_ok_call("m.val_by_pos(amt)"))
    assert value.fn_name == "map_val_by_pos"
    assert value.ty == Ty.U32


def test_map_get_rejects_a_key_of_the_wrong_type() -> None:
    _assert_reject(_reject_call("m.get(amt)"), "SPT3018", "Symbol")


def test_bytes_slice_returns_bytes() -> None:
    node = _host_call(_ok_call("b.slice(amt, amt)"))
    assert node.fn_name == "bytes_slice"
    assert node.ty == Ty.Bytes


def test_bytes32_slice_returns_variable_length_bytes() -> None:
    node = _host_call(_ok_call("b32.slice(amt, amt)"))
    assert node.ty == Ty.Bytes


def test_reader_on_a_temporary_receiver_is_supported() -> None:
    """Only MUTATION needs an owned binding (E11); reading a temporary is
    exactly what the semantics table does (`Vec(U32, [...]).get(5)`)."""
    node = _host_call(_ok_call("Vec(U32, [U32(1), U32(2)]).get(1)"))
    assert node.fn_name == "vec_get"
    assert isinstance(node.args[0], MakeVec)


def test_chained_readers() -> None:
    """`Map(...).keys().get(0)` -- the semantics table's own shape."""
    node = _host_call(_ok_call('Map(Symbol, U32, [(Symbol("a"), U32(1))]).keys().get(0)'))
    assert node.fn_name == "vec_get"
    assert node.ty == Ty.Symbol
    inner = node.args[0]
    assert isinstance(inner, HostCall) and inner.fn_name == "map_keys"


def test_map_value_read_of_a_container_value_type() -> None:
    node = _host_call(_ok_call("mv.get(sym)"))
    assert node.ty == Ty.Vec(Ty.U32)


# --- method-name resolution --------------------------------------------------


def test_vec_method_on_a_map_receiver_is_unresolved() -> None:
    _assert_reject(_reject_call("m.push_back(amt)"), "SPT2001", "push_back")


def test_map_method_on_a_vec_receiver_is_unresolved() -> None:
    _assert_reject(_reject_call("v.key_by_pos(amt)"), "SPT2001", "key_by_pos")


def test_a_method_no_container_has_is_not_recognized_at_all() -> None:
    ctx = _ctx()
    assert recognize_call(_parse_call("v.frobnicate()"), ctx) is None
    assert not ctx.sink


def test_container_method_on_a_scalar_receiver_is_not_recognized() -> None:
    ctx = _ctx()
    assert recognize_call(_parse_call("amt.get(0)"), ctx) is None
    assert not ctx.sink


def test_a_failing_receiver_does_not_cascade() -> None:
    ctx = _ctx()
    node = recognize_call(_parse_call("nope.get(0)"), ctx)
    assert node is not None
    assert node.ty.tag is TyTag.INVALID
    assert len(ctx.sink) == 1
    assert ctx.sink.diagnostics[0].code == "SPT2001"


# --- reader arity / keyword handling (shared `_bind`) ------------------------


def test_reader_missing_argument() -> None:
    _assert_reject(_reject_call("v.get()"), "SPT3020", "index")


def test_reader_too_many_arguments() -> None:
    _assert_reject(_reject_call("v.get(amt, amt)"), "SPT3020", "at most 1")


def test_reader_accepts_the_real_keyword_name() -> None:
    node = _host_call(_ok_call("v.get(index=amt)"))
    assert node.fn_name == "vec_get"


def test_reader_rejects_an_unknown_keyword() -> None:
    _assert_reject(_reject_call("v.get(idx=amt)"), "SPT1035", "idx")


# --- MJ-13: subscripts (owned by expr.py) ------------------------------------


def test_bytes_index_lowers_to_bytes_get() -> None:
    node = _host_call(_ok_expr("b[amt]"))
    assert node.fn_name == "bytes_get"
    assert node.ty == Ty.U32
    recv, index = node.args
    assert isinstance(recv, ParamRef) and recv.name == "b"
    assert isinstance(index, ParamRef) and index.name == "amt"


def test_bytes_index_coerces_a_literal() -> None:
    node = _host_call(_ok_expr("b[0]"))
    index = node.args[1]
    assert isinstance(index, Const) and index.ty == Ty.U32


def test_bytes32_index_lowers_to_bytes_get() -> None:
    node = _host_call(_ok_expr("b32[amt]"))
    assert node.fn_name == "bytes_get"


def test_bytes_index_rejects_a_non_u32_index() -> None:
    _assert_reject(_reject_expr("b[sym]"), "SPT3018", "U32")


def test_negative_literal_index_is_rejected() -> None:
    """D6/F.1.7: C rejects the negative LITERAL and claims nothing about a
    computed index."""
    diag = _reject_expr("b[-1]")
    _assert_reject(diag, "SPT3011", "-1")


def test_bytes_slice_subscript_points_at_the_method() -> None:
    diag = _reject_expr("b[0:2]")
    _assert_reject(diag, "SPT1013", "slice")
    assert ".slice(lo, hi)" in (diag.help or "")


def test_vec_slice_subscript_points_at_the_method() -> None:
    diag = _reject_expr("v[0:2]")
    _assert_reject(diag, "SPT1013", "slice")
    assert ".slice(lo, hi)" in (diag.help or "")


def test_vec_index_read_points_at_get() -> None:
    diag = _reject_expr("v[0]")
    _assert_reject(diag, "SPT1037", "subscript")
    assert ".get(" in (diag.help or "")


def test_map_index_read_points_at_get() -> None:
    diag = _reject_expr("m[sym]")
    _assert_reject(diag, "SPT1037", "subscript")
    assert ".get(" in (diag.help or "")


def test_scalar_subscript_is_rejected() -> None:
    _assert_reject(_reject_expr("amt[0]"), "SPT1037", "subscript")


def test_annotation_only_generic_in_a_value_position() -> None:
    """MJ-13's annotation-position case: `Vec[U32]` resolves through Task 4's
    `resolve_annotation` in an ANNOTATION, and is a located reject in a value
    position."""
    diag = _reject_expr("Vec[U32]")
    _assert_reject(diag, "SPT3014", "annotation")
    assert "Vec(U32, [" in (diag.help or "")


def test_annotation_only_map_generic_in_a_value_position() -> None:
    _assert_reject(_reject_expr("Map[Symbol, U32]"), "SPT3014", "annotation")
