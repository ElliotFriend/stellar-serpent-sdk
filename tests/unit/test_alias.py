"""Tests for the E11/BL-3 alias-analysis pass (Task 7b).

E11 is the sharpest correctness question in the frontend and F.1's #1 silent
divergence: the host's container ops are FUNCTIONAL (`vec_push_back(v, x) ->
VecObject`) while `serpent.types.Vec.push_back` mutates in place, so
`v.push_back(x)` is only soundly lowerable as `v = vec_push_back(v, x)` -- a
REBIND of a binding the compiler owns. `a = b; a.push_back(x)` mutates `b` at
tier 1 and does not on chain, with no error on either side.

So the pass has two halves, tested separately here:

* **Classification** (`classify_binding`/`note_local_binding`): where a
  container-typed binding came from, and the `Ownership` that follows from it.
  Construction and a fresh host result (`map_keys`, `vec_slice`) are OWNED; a
  parameter, a field read, an element read (`map_get` hands back the SAME host
  object tier 1 hands back), and another container local are ALIASED -- and
  `a = b` aliases BOTH names, because after it either name's rebind would
  diverge from tier 1's shared object.
* **The mutation guard** (`recognize_mutation`): legal ONLY on an unaliased
  local whose binding C owns, lowering to `SetLocal(slot, HostCall(...))`.
  Every other receiver -- an aliased local, an unclassified local, a
  parameter, a field, a subscript/element read, and a TEMPORARY receiver such
  as `Vec(U32).pop_back()` -- is `SPT1034` with the functional-host-op
  explanation and a rebind rewrite in `help`.
"""

from __future__ import annotations

import ast

import pytest

from serpent._host import functions_by_name
from serpent.compiler import codes
from serpent.compiler.ctx import AliasTable, FuncCtx, Ownership, SlotTable
from serpent.compiler.diagnostics import Diagnostic, Diagnostics, Loc
from serpent.compiler.expr import check_expr
from serpent.compiler.ir import (
    Const,
    FieldGet,
    HostCall,
    IfExp,
    IRExpr,
    LocalRef,
    MakeVec,
    Nop,
    ParamRef,
    SetLocal,
)
from serpent.compiler.loader import LoadedModule, load_module
from serpent.compiler.recognize import (
    BindingSource,
    classify_binding,
    note_escapes,
    note_local_binding,
    recognize_call,
    recognize_mutation,
)
from serpent.compiler.types_ import Ty

PATH = "contract.py"

_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

_SOURCE = '''
"""Alias-analysis fixture."""
from serpent import U32, Env, Map, Symbol, Vec, contract, contracttype


@contracttype
class Holder:
    items: Vec[U32]


@contract
class Go:
    def go(
        self,
        env: Env,
        pv: Vec[U32],
        pm: Map[Symbol, U32],
        pmv: Map[Symbol, Vec[U32]],
        hold: Holder,
        amt: U32,
        sym: Symbol,
    ) -> U32:
        return amt
'''

_PARAMS: list[tuple[str, Ty]] = [
    ("pv", Ty.Vec(Ty.U32)),
    ("pm", Ty.Map(Ty.Symbol, Ty.U32)),
    ("pmv", Ty.Map(Ty.Symbol, Ty.Vec(Ty.U32))),
    ("hold", Ty.Struct("Holder")),
    ("amt", Ty.U32),
    ("sym", Ty.Symbol),
]

#: The container-typed locals every mutation test shares, and the `Ownership`
#: each one starts in. `unowned` deliberately has NO entry in the table: an
#: unclassified slot is not KNOWN to be owned, and the guard must fail loudly
#: rather than assume (a wiring mistake in a later task then shows up as a
#: reject, never as a silent unsound rebind).
_LOCALS: list[tuple[str, Ty, Ownership | None]] = [
    ("own", Ty.Vec(Ty.U32), Ownership.OWNED),
    ("aliased", Ty.Vec(Ty.U32), Ownership.ALIASED),
    ("unowned", Ty.Vec(Ty.U32), None),
    ("ownmap", Ty.Map(Ty.Symbol, Ty.U32), Ownership.OWNED),
    # The two embedding receivers Critical 1's repros need.
    ("nest", Ty.Vec(Ty.Vec(Ty.U32)), Ownership.OWNED),
    ("mapofvec", Ty.Map(Ty.Symbol, Ty.Vec(Ty.U32)), Ownership.OWNED),
]


def _loaded() -> LoadedModule:
    loaded = load_module(_SOURCE, PATH)
    assert not loaded.diagnostics, loaded.diagnostics.diagnostics
    return loaded


_LOADED = _loaded()


def _ctx() -> FuncCtx:
    loc = Loc.whole_file(PATH)
    params = [(name, ty, loc) for name, ty in _PARAMS]
    reserved = {name: "a parameter" for name, _ in _PARAMS}
    ctx = FuncCtx(
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
    for name, ty, ownership in _LOCALS:
        slot = ctx.locals.declare(name, ty, loc, ctx.sink)
        assert slot is not None
        ctx.locals.mark_assigned(name)
        if ownership is Ownership.OWNED:
            ctx.alias_sets.mark_owned(slot.slot)
        elif ownership is Ownership.ALIASED:
            ctx.alias_sets.mark_aliased(slot.slot)
    assert not ctx.sink
    return ctx


def _slot(ctx: FuncCtx, name: str) -> int:
    slot = ctx.locals.lookup(name)
    assert slot is not None
    return slot.slot


def _parse_call(source: str) -> ast.Call:
    node = ast.parse(source, mode="eval").body
    assert isinstance(node, ast.Call), source
    return node


def _assert_reject(diag: Diagnostic, code: str, detail_substring: str) -> None:
    assert diag.code == code, f"expected {code}, got {diag.code}: {diag.message}"
    intent = _INTENT[code]
    assert diag.message.startswith(intent), (
        f"{code}: message does not start with its registry intent\n"
        f"  message: {diag.message}\n  intent:  {intent}"
    )
    detail = diag.message[len(intent) :]
    assert detail_substring in detail, (
        f"{code}: {detail_substring!r} not in the code-specific detail {detail!r}"
    )
    if code.startswith("SPT1"):
        assert diag.help, f"{code}: SPT1xxx diagnostics must carry help (F.2.11)"


# --- classification ----------------------------------------------------------

_LOC = Loc.whole_file(PATH)


def _fresh_host_result() -> HostCall:
    return HostCall(loc=_LOC, ty=Ty.Vec(Ty.Symbol), fn_name="map_keys", args=())


def _element_host_result() -> HostCall:
    return HostCall(loc=_LOC, ty=Ty.Vec(Ty.U32), fn_name="map_get", args=())


def _construction() -> MakeVec:
    return MakeVec(loc=_LOC, ty=Ty.Vec(Ty.U32), elem_ty=Ty.U32, items=(), all_static=False)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (_construction(), BindingSource.CONSTRUCTION),
        (_fresh_host_result(), BindingSource.FRESH_HOST_RESULT),
        (_element_host_result(), BindingSource.ELEMENT),
        (LocalRef(loc=_LOC, ty=Ty.Vec(Ty.U32), slot=0, name="own"), BindingSource.LOCAL_ALIAS),
        (ParamRef(loc=_LOC, ty=Ty.Vec(Ty.U32), index=0, name="pv"), BindingSource.PARAM),
        (
            FieldGet(
                loc=_LOC,
                ty=Ty.Vec(Ty.U32),
                obj=ParamRef(loc=_LOC, ty=Ty.Struct("Holder"), index=2, name="hold"),
                field="items",
                struct_name="Holder",
            ),
            BindingSource.FIELD,
        ),
        (
            IfExp(
                loc=_LOC,
                ty=Ty.Vec(Ty.U32),
                cond=Const(loc=_LOC, ty=Ty.Bool, py_value=True),
                then=_construction(),
                orelse=_construction(),
            ),
            BindingSource.OTHER,
        ),
    ],
)
def test_classify_binding(value: IRExpr, expected: BindingSource) -> None:
    assert classify_binding(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (_construction(), Ownership.OWNED),
        (_fresh_host_result(), Ownership.OWNED),
        (_element_host_result(), Ownership.ALIASED),
        (ParamRef(loc=_LOC, ty=Ty.Vec(Ty.U32), index=0, name="pv"), Ownership.ALIASED),
    ],
)
def test_note_local_binding_records_ownership(value: IRExpr, expected: Ownership) -> None:
    ctx = _ctx()
    slot = _slot(ctx, "own")
    assert note_local_binding(slot, value, ctx) is expected
    assert ctx.alias_sets.ownership_of(slot) is expected


def test_binding_from_another_local_aliases_both_names() -> None:
    """`a = b` makes a rebind of EITHER name unsound: on chain each has its
    own handle, at tier 1 they are one object."""
    ctx = _ctx()
    target = _slot(ctx, "unowned")
    source = _slot(ctx, "own")
    value = LocalRef(loc=_LOC, ty=Ty.Vec(Ty.U32), slot=source, name="own")
    assert note_local_binding(target, value, ctx) is Ownership.ALIASED
    assert ctx.alias_sets.ownership_of(target) is Ownership.ALIASED
    assert ctx.alias_sets.ownership_of(source) is Ownership.ALIASED


def test_rebinding_a_fresh_value_restores_ownership() -> None:
    ctx = _ctx()
    slot = _slot(ctx, "aliased")
    assert ctx.alias_sets.ownership_of(slot) is Ownership.ALIASED
    assert note_local_binding(slot, _construction(), ctx) is Ownership.OWNED


def test_note_local_binding_ignores_non_container_values() -> None:
    ctx = _ctx()
    slot = _slot(ctx, "own")
    scalar = Const(loc=_LOC, ty=Ty.U32, py_value=1)
    assert note_local_binding(slot, scalar, ctx) is None


# --- the mutation guard: legal shapes ----------------------------------------


_MUTATOR_ROWS: list[tuple[str, str, str]] = [
    ("own", "own.push_back(amt)", "vec_push_back"),
    ("own", "own.push_front(amt)", "vec_push_front"),
    ("own", "own.pop_back()", "vec_pop_back"),
    ("own", "own.pop_front()", "vec_pop_front"),
    ("own", "own.put(amt, amt)", "vec_put"),
    ("own", "own.del_(amt)", "vec_del"),
    ("own", "own.insert(amt, amt)", "vec_insert"),
    ("own", "own.append(pv)", "vec_append"),
    ("ownmap", "ownmap.set(sym, amt)", "map_put"),
    ("ownmap", "ownmap.del_(sym)", "map_del"),
]


@pytest.mark.parametrize(("local", "source", "fn_name"), _MUTATOR_ROWS)
def test_mutation_of_an_owned_local_rebinds_the_slot(local: str, source: str, fn_name: str) -> None:
    """E11's whole lowering: `v.push_back(x)` becomes `v = vec_push_back(v,
    x)`, recorded as the existing `SetLocal` statement node -- no new IR node
    and no way for D to lose the rebind."""
    ctx = _ctx()
    stmt = recognize_mutation(_parse_call(source), ctx)
    assert stmt is not None, source
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert isinstance(stmt, SetLocal)
    assert stmt.slot == _slot(ctx, local)
    call = stmt.value
    assert isinstance(call, HostCall)
    assert call.fn_name == fn_name
    assert len(call.args) == functions_by_name[fn_name].arity
    receiver = call.args[0]
    assert isinstance(receiver, LocalRef) and receiver.name == local
    # The rebound value keeps the container's own type: the host op is
    # functional and hands back a new handle of the same shape.
    assert call.ty == receiver.ty


def test_mutation_keeps_ownership() -> None:
    ctx = _ctx()
    stmt = recognize_mutation(_parse_call("own.push_back(amt)"), ctx)
    assert stmt is not None
    assert ctx.alias_sets.ownership_of(_slot(ctx, "own")) is Ownership.OWNED


def test_mutation_argument_types_are_checked() -> None:
    ctx = _ctx()
    stmt = recognize_mutation(_parse_call("own.push_back(sym)"), ctx)
    assert stmt is not None
    assert isinstance(stmt, Nop)
    assert len(ctx.sink) == 1
    _assert_reject(ctx.sink.diagnostics[0], "SPT3018", "U32")


def test_recognize_mutation_returns_none_for_a_non_mutation_call() -> None:
    ctx = _ctx()
    assert recognize_mutation(_parse_call("own.get(amt)"), ctx) is None
    assert recognize_mutation(_parse_call("env.ledger().sequence()"), ctx) is None
    assert not ctx.sink


# --- the mutation guard: every reject ----------------------------------------


_REJECTED_RECEIVERS: list[tuple[str, str, str]] = [
    ("an aliased local", "aliased.push_back(amt)", "aliased"),
    ("an unclassified local", "unowned.push_back(amt)", "unowned"),
    ("a parameter", "pv.push_back(amt)", "parameter"),
    ("a field read", "hold.items.push_back(amt)", "field"),
    ("an element read", "pmv.get(sym).push_back(amt)", "element"),
    ("a temporary construction", "Vec(U32).pop_back()", "temporary"),
    ("a temporary host result", "pm.keys().push_back(sym)", "temporary"),
]


@pytest.mark.parametrize(("label", "source", "detail"), _REJECTED_RECEIVERS)
def test_mutation_through_an_unowned_receiver_is_rejected(
    label: str, source: str, detail: str
) -> None:
    del label
    ctx = _ctx()
    stmt = recognize_mutation(_parse_call(source), ctx)
    assert stmt is not None, source
    assert isinstance(stmt, Nop)
    assert len(ctx.sink) == 1, [(d.code, d.message) for d in ctx.sink.diagnostics]
    diag = ctx.sink.diagnostics[0]
    _assert_reject(diag, "SPT1034", detail)
    # The diagnostic must EXPLAIN the divergence and name a rewrite (E11's
    # "a diagnostic that explains the functional-host-op reason").
    assert any("functional" in note for note in diag.notes), diag.notes
    assert diag.help


def test_mutation_in_a_value_position_is_rejected() -> None:
    """A mutator's lowering is a REBIND, which no expression position can
    express -- `x = v.push_back(y)` is not tier-1 valid either (`push_back`
    returns `None`)."""
    ctx = _ctx()
    node = recognize_call(_parse_call("own.push_back(amt)"), ctx)
    assert node is not None
    assert len(ctx.sink) == 1
    _assert_reject(ctx.sink.diagnostics[0], "SPT1034", "statement")


def test_popped_value_position_names_the_read_first_rewrite() -> None:
    ctx = _ctx()
    node = recognize_call(_parse_call("own.pop_back()"), ctx)
    assert node is not None
    assert len(ctx.sink) == 1
    diag = ctx.sink.diagnostics[0]
    _assert_reject(diag, "SPT1034", "statement")
    assert ".get(" in (diag.help or "")


# --- alias by EMBEDDING (fix round 1, Critical 1) ---------------------------

#: The three shapes the reviewer verified against tier 1: after each, tier 1
#: sees a later `own.push_back(...)` through the container that now holds the
#: same object, and the chain cannot (the rebind touches only `own`).
_EMBEDDING_ESCAPES: list[tuple[str, str]] = [
    ("into a struct field", "Holder(items=own)"),
    ("into another Vec", "nest.push_back(own)"),
    ("into a Map value", "mapofvec.set(sym, own)"),
]


@pytest.mark.parametrize(("label", "escape"), _EMBEDDING_ESCAPES)
def test_embedding_a_local_costs_it_ownership(label: str, escape: str) -> None:
    del label
    ctx = _ctx()
    slot = _slot(ctx, "own")
    assert ctx.alias_sets.ownership_of(slot) is Ownership.OWNED

    # The escaping statement itself is legal.
    node = _parse_call(escape)
    if recognize_mutation(node, ctx) is None:
        assert recognize_call(node, ctx) is not None
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert ctx.alias_sets.ownership_of(slot) is Ownership.ALIASED

    # The mutation AFTER it is not.
    stmt = recognize_mutation(_parse_call("own.push_back(amt)"), ctx)
    assert isinstance(stmt, Nop)
    assert len(ctx.sink) == 1, [(d.code, d.message) for d in ctx.sink.diagnostics]
    diag = ctx.sink.diagnostics[0]
    _assert_reject(diag, "SPT1034", "own")
    assert any("functional" in note for note in diag.notes), diag.notes


def test_embedding_a_copy_does_not_cost_ownership() -> None:
    """`slice` builds a NEW Vec on both tiers, so what escapes is the copy."""
    ctx = _ctx()
    assert recognize_call(_parse_call("Holder(items=own.slice(amt, amt))"), ctx) is not None
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert ctx.alias_sets.ownership_of(_slot(ctx, "own")) is Ownership.OWNED
    assert isinstance(recognize_mutation(_parse_call("own.push_back(amt)"), ctx), SetLocal)


def test_embedding_an_element_of_a_local_does_not_cost_ownership() -> None:
    """Only the local's OWN handle escaping counts: copying an element out
    (`own.get(i)`) leaves the container exclusively C's, and both tiers
    agree."""
    ctx = _ctx()
    assert recognize_call(_parse_call("Vec(U32, [own.get(amt)])"), ctx) is not None
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert ctx.alias_sets.ownership_of(_slot(ctx, "own")) is Ownership.OWNED


def test_note_escapes_marks_both_arms_of_a_conditional() -> None:
    """An `IfExp` can BE either local, so both lose ownership."""
    ctx = _ctx()
    own = LocalRef(loc=_LOC, ty=Ty.Vec(Ty.U32), slot=_slot(ctx, "own"), name="own")
    other = LocalRef(loc=_LOC, ty=Ty.Vec(Ty.U32), slot=_slot(ctx, "unowned"), name="unowned")
    note_escapes(
        [
            IfExp(
                loc=_LOC,
                ty=Ty.Vec(Ty.U32),
                cond=Const(loc=_LOC, ty=Ty.Bool, py_value=True),
                then=own,
                orelse=other,
            )
        ],
        ctx,
    )
    assert ctx.alias_sets.ownership_of(own.slot) is Ownership.ALIASED
    assert ctx.alias_sets.ownership_of(other.slot) is Ownership.ALIASED


def _conditional(then: IRExpr, orelse: IRExpr, ty: Ty) -> IfExp:
    return IfExp(
        loc=_LOC,
        ty=ty,
        cond=Const(loc=_LOC, ty=Ty.Bool, py_value=True),
        then=then,
        orelse=orelse,
    )


def _local_ref(ctx: FuncCtx, name: str, ty: Ty) -> LocalRef:
    return LocalRef(loc=_LOC, ty=ty, slot=_slot(ctx, name), name=name)


def test_binding_a_conditional_aliases_both_arms() -> None:
    """Fix round 2's hole: `w = own if flag else other` shares BOTH arms'
    handles at tier 1, so classifying the VALUE alone (an `IfExp` is `OTHER`,
    which only aliases the TARGET) left the arms reading `OWNED` and accepted
    a later mutation of them."""
    ctx = _ctx()
    vec = Ty.Vec(Ty.U32)
    own = _local_ref(ctx, "own", vec)
    other = _local_ref(ctx, "nest", Ty.Vec(vec))
    target = _slot(ctx, "unowned")

    assert note_local_binding(target, _conditional(own, other, vec), ctx) is Ownership.ALIASED
    assert ctx.alias_sets.ownership_of(target) is Ownership.ALIASED
    assert ctx.alias_sets.ownership_of(own.slot) is Ownership.ALIASED
    assert ctx.alias_sets.ownership_of(other.slot) is Ownership.ALIASED

    for source in ("own.push_back(amt)", "nest.push_back(own)"):
        ctx.sink = Diagnostics()
        stmt = recognize_mutation(_parse_call(source), ctx)
        assert isinstance(stmt, Nop), source
        assert len(ctx.sink) == 1, [(d.code, d.message) for d in ctx.sink.diagnostics]
        diag = ctx.sink.diagnostics[0]
        _assert_reject(diag, "SPT1034", "aliased")
        assert any("functional" in note for note in diag.notes), diag.notes


def test_binding_a_conditional_whose_arms_are_the_same_local() -> None:
    ctx = _ctx()
    vec = Ty.Vec(Ty.U32)
    own = _local_ref(ctx, "own", vec)
    note_local_binding(_slot(ctx, "unowned"), _conditional(own, own, vec), ctx)
    assert ctx.alias_sets.ownership_of(own.slot) is Ownership.ALIASED
    assert isinstance(recognize_mutation(_parse_call("own.push_back(amt)"), ctx), Nop)


def test_binding_a_conditional_over_maps_aliases_both_arms() -> None:
    ctx = _ctx()
    map_ty = Ty.Map(Ty.Symbol, Ty.U32)
    ownmap = _local_ref(ctx, "ownmap", map_ty)
    other = _local_ref(ctx, "mapofvec", Ty.Map(Ty.Symbol, Ty.Vec(Ty.U32)))
    note_local_binding(_slot(ctx, "unowned"), _conditional(ownmap, other, map_ty), ctx)
    assert ctx.alias_sets.ownership_of(ownmap.slot) is Ownership.ALIASED
    assert ctx.alias_sets.ownership_of(other.slot) is Ownership.ALIASED
    stmt = recognize_mutation(_parse_call("ownmap.set(sym, amt)"), ctx)
    assert isinstance(stmt, Nop)
    _assert_reject(ctx.sink.diagnostics[0], "SPT1034", "ownmap")


def test_binding_a_conditional_the_checker_itself_built() -> None:
    """The same hole through the real entry point: the `IfExp` here is the one
    `check_expr` produces for `own if flag else aliased`, so the shape
    `_escaping_locals` matches is the shape the checker actually emits."""
    ctx = _ctx()
    value = check_expr(ast.parse("own if amt > U32(0) else unowned", mode="eval").body, ctx)
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert isinstance(value, IfExp)
    note_local_binding(_slot(ctx, "aliased"), value, ctx)
    assert ctx.alias_sets.ownership_of(_slot(ctx, "own")) is Ownership.ALIASED
    stmt = recognize_mutation(_parse_call("own.push_back(amt)"), ctx)
    assert isinstance(stmt, Nop)
    _assert_reject(ctx.sink.diagnostics[0], "SPT1034", "own")


def test_binding_a_host_result_aliases_nothing_else() -> None:
    """The control: a `HostCall` right-hand side BUILDS a value, so routing
    every RHS through `note_escapes` must not alias anything spuriously."""
    ctx = _ctx()
    value = recognize_call(_parse_call("pmv.values()"), ctx)
    assert value is not None
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert note_local_binding(_slot(ctx, "nest"), value, ctx) is Ownership.OWNED
    assert ctx.alias_sets.ownership_of(_slot(ctx, "own")) is Ownership.OWNED
    assert ctx.alias_sets.ownership_of(_slot(ctx, "ownmap")) is Ownership.OWNED
    assert isinstance(recognize_mutation(_parse_call("own.push_back(amt)"), ctx), SetLocal)
    assert isinstance(recognize_mutation(_parse_call("nest.push_back(own)"), ctx), SetLocal)


def test_note_escapes_ignores_non_container_locals() -> None:
    ctx = _ctx()
    scalar_slot = ctx.locals.declare("count", Ty.U32, _LOC, ctx.sink)
    assert scalar_slot is not None
    note_escapes([LocalRef(loc=_LOC, ty=Ty.U32, slot=scalar_slot.slot, name="count")], ctx)
    assert ctx.alias_sets.ownership_of(scalar_slot.slot) is None


def test_reader_through_an_aliased_receiver_is_fine() -> None:
    """The guard is about MUTATION only: reading an aliased local, a
    parameter, or a temporary is sound on both tiers."""
    ctx = _ctx()
    for source in ("aliased.get(amt)", "pv.get(amt)", "pm.keys()", "hold.items.get(amt)"):
        node = recognize_call(_parse_call(source), ctx)
        assert node is not None, source
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
