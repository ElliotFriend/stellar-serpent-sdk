"""Tests for `serpent.emitter.lower` -- the OBJECT half of SS B.3.1.

Containers, structs, host calls, internal calls, and the storage guard. Almost
everything here is **executed** against `tests/harness` rather than snapshotted,
for the reason `test_emitter_lower_scalar.py` gives: a wrong lowering in this
half produces a *plausible* wrong answer -- a one-element vector, a map missing
its last key, a read of a key that was never written -- and a byte snapshot
would pin the wrong bytes forever. Only the two questions execution cannot ask
are structural: which import a body calls, and what ended up in the data pool.

What is being protected, in order of how badly it would bite:

* **E13 + review B7/M12, the storage guard.** `get_contract_data` on an absent
  key is undefined at the host. The guard makes it a contract error; the
  with-default shape is exempt because its own `has` already answered; and the
  exemption is keyed on node IDENTITY so a hand-written `get if has` -- whose
  two key subtrees are distinct even when equal -- stays guarded. B7's test is
  the one that would go unnoticed longest: an effectful key evaluated twice.
* **F.1.9, mutator results are rebound.** `vec_push_back` returns a NEW handle.
  A chain that dropped it builds a one-element vector and reports success.
* **C9/P1/F.1.5, the struct key descriptors.** The keys blob is compile-time
  `(ptr, len)` pairs that MUST ascend as byte strings; the host panics
  otherwise. The harness enforces it, and a descending blob here proves the
  enforcement is real rather than decorative.
* **E12**, a `Map[U32, U32]` literal takes the CHAIN however static it is.
* **C4**, the linear-memory vector form needs a compile-time `Val` WORD, which
  `all_static` alone does not provide.
* **B2**, every argument's Val-vs-raw position comes from the pin.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from serpent import errors, val
from serpent.compiler.diagnostics import Loc
from serpent.compiler.frontend import LiteralInventory, compile_module
from serpent.compiler.ir import (
    Const,
    FieldGet,
    HostCall,
    IfExp,
    InternalCall,
    IRExpr,
    MakeMap,
    MakeStruct,
    MakeTopics,
    MakeVec,
    ParamRef,
    RawScalar,
    RawScalarKind,
    walk,
)
from serpent.compiler.types_ import Ty
from serpent.emitter import lower
from serpent.emitter.frame import CallDefined, CallImport, CodeItem, EmitError, Fn
from serpent.emitter.layout import Memory
from serpent.emitter.lower import LowerCtx
from tests.harness import engine, testmod
from tests.harness.objects import STORAGE_INSTANCE, STORAGE_PERSISTENT, ObjectStore

LOC = Loc.whole_file("contracts/t.py")

#: The error a missing storage key becomes (E14).
MISSING_VALUE = val.error_val(errors.CODE_MISSING_VALUE)


# --- IR shorthand ---------------------------------------------------------------


def const(ty: Ty, value: object) -> Const:
    return Const(loc=LOC, ty=ty, py_value=value)


def storage_type(value: int) -> RawScalar:
    return RawScalar(loc=LOC, ty=Ty.U32, value=value, kind=RawScalarKind.STORAGE_TYPE)


def host(name: str, ty: Ty, *args: IRExpr) -> HostCall:
    return HostCall(loc=LOC, ty=ty, fn_name=name, args=args)


def make_vec(elem: Ty, items: Sequence[IRExpr], *, all_static: bool) -> MakeVec:
    return MakeVec(
        loc=LOC, ty=Ty.Vec(elem), elem_ty=elem, items=tuple(items), all_static=all_static
    )


def make_map(
    key: Ty, value: Ty, pairs: Sequence[tuple[IRExpr, IRExpr]], *, all_static: bool
) -> MakeMap:
    return MakeMap(
        loc=LOC,
        ty=Ty.Map(key, value),
        key_ty=key,
        value_ty=value,
        pairs=tuple(pairs),
        all_static=all_static,
    )


def make_struct(name: str, fields: Sequence[tuple[str, IRExpr]]) -> MakeStruct:
    return MakeStruct(loc=LOC, ty=Ty.Struct(name), struct_name=name, fields=tuple(fields))


# --- building and running a probe module ----------------------------------------


@dataclass(frozen=True)
class Helper:
    """One extra defined function beside the probe, for `InternalCall` tests.

    `emit` receives the `Fn` and the shared `LowerCtx` and is responsible for
    everything up to (not including) `ret()`, so a helper can call imports and
    register them in the same import order the probe uses.
    """

    name: str
    nparams: int
    results: tuple[str, ...]
    emit: Callable[[Fn, LowerCtx], None]


def build(
    e: IRExpr,
    *,
    nparams: int = 0,
    consts: Any = None,
    helpers: Sequence[Helper] = (),
    memory: Memory | None = None,
) -> tuple[bytes, Memory, LowerCtx]:
    """Lower `e` into a one-expression `probe`, plus any `helpers`, as a module.

    The probe is defidx 0 and the helpers follow in order, which is the
    `functions` table an `InternalCall` resolves through (review B1).
    """
    memory = Memory() if memory is None else memory
    functions = {"probe": 0, **{h.name: i + 1 for i, h in enumerate(helpers)}}
    ctx = LowerCtx(
        n_module_functions=1 + len(helpers),
        memory=memory,
        consts=consts,
        functions=functions,
    )

    probe = Fn("probe", nparams, 0, ("i64",))
    lower.lower_expr(probe, ctx, e)
    probe.ret()
    specs: list[testmod.FunctionSpec] = [
        ("probe", nparams, probe.nlocals, ("i64",), probe.finish())
    ]
    for helper in helpers:
        fn = Fn(helper.name, helper.nparams, 0, helper.results)
        helper.emit(fn, ctx)
        fn.ret()
        specs.append((helper.name, helper.nparams, fn.nlocals, helper.results, fn.finish()))
    specs.extend((p.name, p.nparams, p.nlocals, p.results, p.body) for p in ctx.parts)

    pool = memory.pool_bytes()
    wasm = testmod.build_test_module(
        specs,
        imports=ctx.import_order,
        memory_pages=1 if not memory.is_empty or ctx.needs_memory else None,
        data=pool if not memory.is_empty or ctx.needs_memory else None,
    )
    return wasm, memory, ctx


def start(
    e: IRExpr, *, store: ObjectStore | None = None, **kwargs: Any
) -> tuple[ObjectStore, engine.MiniHost]:
    """Build and instantiate, WITHOUT invoking -- for a store that needs seeding."""
    store = ObjectStore() if store is None else store
    wasm, _memory, _ctx = build(e, **kwargs)
    host_ = engine.MiniHost(wasm, imports=store.bindings())
    store.attach(host_)
    return store, host_


def run(e: IRExpr, *args: int, store: ObjectStore | None = None, **kwargs: Any) -> int:
    """Lower `e`, run it, and return the `Val` word it produced."""
    store, host_ = start(e, store=store, **kwargs)
    result = host_.invoke("probe", *args)
    assert result is not None
    return result


def items(e: IRExpr, **kwargs: Any) -> list[CodeItem]:
    """The finished symbolic body, for the structural questions only."""
    memory = kwargs.pop("memory", None) or Memory()
    ctx = LowerCtx(
        n_module_functions=1,
        memory=memory,
        consts=kwargs.pop("consts", None),
        functions=kwargs.pop("functions", None),
    )
    fn = Fn("probe", kwargs.pop("nparams", 0), 0, ("i64",))
    lower.lower_expr(fn, ctx, e)
    fn.ret()
    return fn.finish()


def import_calls(body: Sequence[CodeItem]) -> list[str]:
    return [item.name for item in body if isinstance(item, CallImport)]


# ===========================================================================
# HostCall: positional args, Val vs RAW from the PIN (B2/C13)
# ===========================================================================


def test_a_host_call_lowers_its_arguments_positionally() -> None:
    """`map_get(m, k)` -- both positions Val-typed, in source order."""
    literal = make_map(
        Ty.Symbol, Ty.U32, [(const(Ty.Symbol, "a"), const(Ty.U32, 7))], all_static=True
    )
    assert run(host("map_get", Ty.U32, literal, const(Ty.Symbol, "a"))) == val.pack_u32val(7)


def test_the_storage_type_argument_is_a_RAW_number_not_a_U32Val() -> None:
    """C13/B2: `put_contract_data`'s third position has `val_typed_args[2] is
    False`, so it is the plain number 2 -- packing it as a `U32Val` would pass
    an argument wrong by a factor of 2**32 and still validate."""
    put = host(
        "put_contract_data",
        Ty.Void,
        const(Ty.Symbol, "k"),
        const(Ty.U32, 9),
        storage_type(STORAGE_INSTANCE),
    )
    store, host_ = start(put)
    host_.invoke("probe")
    assert store.calls[0] == (
        "put_contract_data",
        (val.symbol_small("k"), val.pack_u32val(9), STORAGE_INSTANCE),
    )


def test_a_raw_position_holding_a_non_RawScalar_still_gets_the_bare_number() -> None:
    """The test that makes the B2/C13 dispatch FALSIFIABLE.

    A `RawScalar` cannot prove this on its own: `_lower_val` of one emits the
    bare number (C13) and so does `lower_expr_raw`, so both branches of the
    dispatch produce identical bytes for it and a lowering that ignored
    `val_typed_args` entirely would pass every `RawScalar`-only test. Put a
    `Const(U32)` in the same position and the two branches finally diverge --
    `pack_u32val(2)` is `0x0000_0002_0000_0004`, the bare number is `2` -- so
    the harness can tell which one ran.
    """
    put = host(
        "put_contract_data",
        Ty.Void,
        const(Ty.Symbol, "k"),
        const(Ty.U32, 9),
        const(Ty.U32, STORAGE_INSTANCE),
    )
    store, host_ = start(put)
    host_.invoke("probe")
    assert store.calls[0] == (
        "put_contract_data",
        (val.symbol_small("k"), val.pack_u32val(9), STORAGE_INSTANCE),
    )


def test_a_raw_position_holding_a_ParamRef_is_UNBOXED_not_passed_through() -> None:
    """The same point where no compile-time shortcut can hide it.

    A `ParamRef` has no constant form at all, so the raw branch is a real
    `local.get` + unbox and the Val branch is a bare `local.get`. The caller
    hands in a `U32Val`; the host must see the number inside it.
    """
    put = host(
        "put_contract_data",
        Ty.Void,
        const(Ty.Symbol, "k"),
        const(Ty.U32, 9),
        ParamRef(loc=LOC, ty=Ty.U32, index=0, name="bucket"),
    )
    store, host_ = start(put, nparams=1)
    host_.invoke("probe", val.pack_u32val(STORAGE_PERSISTENT))
    assert store.calls[0][1][2] == STORAGE_PERSISTENT


def test_the_pins_four_arity_mixed_row_lowers_every_position_from_the_pin() -> None:
    """`extend_contract_data_ttl`: `val_typed_args == (True, False, True, True)`.

    The pin's only 4-arity row that mixes the two conventions, and it pins the
    dispatch in BOTH directions at once: position 1 must arrive as the bare
    storage-type number, and positions 2 and 3 must arrive as `U32Val`s. A
    lowering that picked one convention for the whole call fails here whichever
    convention it picked.
    """
    node = host(
        "extend_contract_data_ttl",
        Ty.Void,
        const(Ty.Symbol, "k"),
        storage_type(STORAGE_PERSISTENT),
        const(Ty.U32, 100),
        const(Ty.U32, 200),
    )
    store, host_ = start(node)
    host_.invoke("probe")
    assert store.calls[0] == (
        "extend_contract_data_ttl",
        (
            val.symbol_small("k"),
            STORAGE_PERSISTENT,
            val.pack_u32val(100),
            val.pack_u32val(200),
        ),
    )


def test_a_host_call_arity_that_disagrees_with_the_pin_is_loud() -> None:
    with pytest.raises(EmitError, match="takes 2 argument"):
        items(host("map_get", Ty.U32, const(Ty.U32, 1)))


def test_a_host_function_outside_the_pin_is_loud() -> None:
    with pytest.raises(EmitError, match="not a host function in the pin"):
        items(host("map_invent", Ty.U32))


# ===========================================================================
# The storage guard (ruling E13, review B7/M12)
# ===========================================================================

KEY = const(Ty.Symbol, "k")


def bare_get(key: IRExpr = KEY, bucket: int = STORAGE_INSTANCE) -> HostCall:
    return host("get_contract_data", Ty.U32, key, storage_type(bucket))


def test_a_bare_get_of_a_missing_key_fails_with_CODE_MISSING_VALUE() -> None:
    """E13/E14. The host promises NOTHING for a `get` on an absent key, so the
    guard is what gives the program a defined outcome -- and it is a CONTRACT
    error carrying its code (R3), never a bare `unreachable`."""
    store, host_ = start(bare_get())
    with pytest.raises(engine.HostError) as info:
        host_.invoke("probe")
    assert info.value.val == MISSING_VALUE
    assert store.errors == [MISSING_VALUE]
    # The guard asked first, and the `get` never ran.
    assert store.call_names() == ["has_contract_data", "fail_with_error"]


def test_a_bare_get_of_a_present_key_returns_it_and_asks_has_once() -> None:
    store = ObjectStore()
    store.storage[(STORAGE_INSTANCE, "k")] = val.pack_u32val(41)
    assert run(bare_get(), store=store) == val.pack_u32val(41)
    assert store.call_names() == ["has_contract_data", "get_contract_data"]


def test_a_with_default_get_returns_the_default_and_does_not_double_the_has() -> None:
    """The E13 exemption. C's own `has` proved presence, so the `then` arm gets
    NO guard -- a second `has_contract_data` would be a second ledger access
    and a second charge for one read."""
    shared_key, shared_imm = KEY, storage_type(STORAGE_PERSISTENT)
    node = IfExp(
        loc=LOC,
        ty=Ty.U32,
        cond=host("has_contract_data", Ty.Bool, shared_key, shared_imm),
        then=host("get_contract_data", Ty.U32, shared_key, shared_imm),
        orelse=const(Ty.U32, 5),
    )
    store = ObjectStore()
    assert run(node, store=store) == val.pack_u32val(5)
    assert store.call_names() == ["has_contract_data"]

    present = ObjectStore()
    present.storage[(STORAGE_PERSISTENT, "k")] = val.pack_u32val(99)
    assert run(node, store=present) == val.pack_u32val(99)
    assert present.count("has_contract_data") == 1
    assert present.count("get_contract_data") == 1


def test_a_with_default_get_evaluates_an_effectful_key_exactly_once() -> None:
    """Review B7, and the failure that would go unnoticed longest.

    The key is an `InternalCall` to a helper that writes to storage. Lowering
    the `has` arm and the `get` arm independently -- the obvious way to build
    this out of the generic `IfExp` and `HostCall` lowerings -- runs that
    helper TWICE, so the contract charges twice and writes twice for one read,
    while still returning the right number. Tier 1 evaluates it once. The
    counter is the harness's own call log, which cannot be fooled by the
    emitted code agreeing with itself.
    """

    def bump(fn: Fn, ctx: LowerCtx) -> None:
        # A visible effect...
        lower.lower_expr(
            fn,
            ctx,
            host(
                "put_contract_data",
                Ty.Void,
                const(Ty.Symbol, "n"),
                const(Ty.U32, 1),
                storage_type(STORAGE_INSTANCE),
            ),
        )
        fn.drop()
        # ...then the key itself.
        lower.lower_expr(fn, ctx, const(Ty.Symbol, "k"))

    key = InternalCall(loc=LOC, ty=Ty.Symbol, fn_name="bump", args=())
    imm = storage_type(STORAGE_PERSISTENT)
    node = IfExp(
        loc=LOC,
        ty=Ty.U32,
        cond=host("has_contract_data", Ty.Bool, key, imm),
        then=host("get_contract_data", Ty.U32, key, imm),
        orelse=const(Ty.U32, 0),
    )
    helper = Helper("bump", 0, ("i64",), bump)

    store = ObjectStore()
    store.storage[(STORAGE_PERSISTENT, "k")] = val.pack_u32val(1234)
    assert run(node, store=store, helpers=[helper]) == val.pack_u32val(1234)
    assert store.count("put_contract_data") == 1


def test_a_hand_written_get_if_has_is_still_guarded() -> None:
    """Review M12. The `is` test is what makes this work.

    Here the `has` asks about one key and the `get` reads a DIFFERENT one --
    which is what a hand-written `a.get(k, T) if a.has(k2) else d` can always
    be, since nothing ties the two spellings together. Keying the exemption on
    the condition alone would suppress the guard on exactly this program and
    let the `get` read a key nothing proved present.
    """
    probed = const(Ty.Symbol, "probed")
    node = IfExp(
        loc=LOC,
        ty=Ty.U32,
        cond=host("has_contract_data", Ty.Bool, probed, storage_type(STORAGE_INSTANCE)),
        then=bare_get(const(Ty.Symbol, "other")),
        orelse=const(Ty.U32, 0),
    )
    store = ObjectStore()
    store.storage[(STORAGE_INSTANCE, "probed")] = val.pack_u32val(1)

    _store, host_ = start(node, store=store)
    with pytest.raises(engine.HostError) as info:
        host_.invoke("probe")
    assert info.value.val == MISSING_VALUE
    # Two `has` calls: the hand-written condition, then the guard's own.
    assert store.count("has_contract_data") == 2


def test_equal_but_distinct_key_nodes_do_not_earn_the_exemption() -> None:
    """The same point one notch finer: the two keys are `==` (frozen
    dataclasses compare structurally) and still DISTINCT objects, so the
    exemption does not apply. Equality would have suppressed the guard here."""
    a, b = const(Ty.Symbol, "k"), const(Ty.Symbol, "k")
    assert a == b and a is not b
    node = IfExp(
        loc=LOC,
        ty=Ty.U32,
        cond=host("has_contract_data", Ty.Bool, a, storage_type(STORAGE_INSTANCE)),
        then=bare_get(b),
        orelse=const(Ty.U32, 0),
    )
    store = ObjectStore()
    store.storage[(STORAGE_INSTANCE, "k")] = val.pack_u32val(3)
    assert run(node, store=store) == val.pack_u32val(3)
    assert store.count("has_contract_data") == 2


def test_a_mismatched_storage_bucket_does_not_earn_the_exemption() -> None:
    """`has` in the instance bucket proves nothing about the persistent one."""
    shared = KEY
    node = IfExp(
        loc=LOC,
        ty=Ty.U32,
        cond=host("has_contract_data", Ty.Bool, shared, storage_type(STORAGE_INSTANCE)),
        then=host("get_contract_data", Ty.U32, shared, storage_type(STORAGE_PERSISTENT)),
        orelse=const(Ty.U32, 0),
    )
    store = ObjectStore()
    store.storage[(STORAGE_INSTANCE, "k")] = val.pack_u32val(1)
    _store, host_ = start(node, store=store)
    with pytest.raises(engine.HostError) as info:
        host_.invoke("probe")
    assert info.value.val == MISSING_VALUE


def test_an_immediate_from_the_wrong_scalar_table_does_not_earn_the_exemption() -> None:
    """Both immediates are checked for `STORAGE_TYPE`, not just the `has` one.

    A `RawScalar` carries the table it came from (B6), and a
    `CONTRACT_TTL_EXTENSION` immediate in the `get`'s storage-type position is
    not a storage bucket at all -- so the two calls are not talking about the
    same entry and the `has` proves nothing. Checking only the condition's
    immediate would have let this through.
    """
    shared = KEY
    node = IfExp(
        loc=LOC,
        ty=Ty.U32,
        cond=host("has_contract_data", Ty.Bool, shared, storage_type(STORAGE_INSTANCE)),
        then=host(
            "get_contract_data",
            Ty.U32,
            shared,
            RawScalar(
                loc=LOC,
                ty=Ty.U32,
                value=STORAGE_INSTANCE,
                kind=RawScalarKind.CONTRACT_TTL_EXTENSION,
            ),
        ),
        orelse=const(Ty.U32, 0),
    )
    store = ObjectStore()
    store.storage[(STORAGE_INSTANCE, "k")] = val.pack_u32val(4)
    assert run(node, store=store) == val.pack_u32val(4)
    # Guarded: the hand-written condition, then the guard's own `has`.
    assert store.count("has_contract_data") == 2


def test_the_frontends_own_with_default_shape_is_the_one_that_is_exempt() -> None:
    """The end-to-end anchor: the IR the FRONTEND builds, not a hand-made
    lookalike. If `recognize.py` ever stopped sharing the key node, this test
    is what notices -- the guard would reappear and the `has` count would go
    to two."""
    compiled = compile_module(
        "from serpent import Env, Symbol, U32, contract\n"
        "\n"
        "\n"
        "@contract\n"
        "class C:\n"
        "    def go(self, env: Env) -> U32:\n"
        '        return env.storage().persistent().get(Symbol("k"), U32, default=U32(8))\n',
        "contracts/t.py",
    )
    node = next(n for n in walk(compiled.ir) if isinstance(n, IfExp))
    store = ObjectStore()
    assert run(node, store=store) == val.pack_u32val(8)
    assert store.call_names() == ["has_contract_data"]


# ===========================================================================
# InternalCall (E8, E11ii)
# ===========================================================================


def test_an_internal_call_resolves_to_a_defined_space_index() -> None:
    node = InternalCall(loc=LOC, ty=Ty.U32, fn_name="helper", args=())
    body = items(node, functions={"probe": 0, "helper": 1})
    assert [i for i in body if isinstance(i, CallDefined)] == [CallDefined(1)]


def test_an_internal_call_passes_its_arguments_as_Vals_and_returns_one() -> None:
    def double(fn: Fn, ctx: LowerCtx) -> None:
        # `probe` hands `helper` a U32 Val; the helper hands it straight back.
        fn.local_get(0)

    node = InternalCall(loc=LOC, ty=Ty.U32, fn_name="echo", args=(const(Ty.U32, 77),))
    helper = Helper("echo", 1, ("i64",), double)
    assert run(node, helpers=[helper]) == val.pack_u32val(77)


def test_a_void_internal_call_has_zero_results() -> None:
    """E11ii/review M2: a `-> None` helper is compiled with `results=()`, so an
    `Eval` of one needs no `drop` -- which is why Task 9's statement path opens
    a VOID expression scope for this node rather than the default +1 one."""
    node = InternalCall(loc=LOC, ty=Ty.Void, fn_name="side_effect", args=())
    ctx = LowerCtx(n_module_functions=2, memory=Memory(), functions={"side_effect": 1})
    fn = Fn("probe", 0, 0, ())
    with fn.expr_scope(is_void=True):
        lower._lower_val(fn, ctx, node)
    fn.ret()
    assert [i for i in fn.finish() if isinstance(i, CallDefined)] == [CallDefined(1)]


def test_an_unknown_internal_call_target_is_loud() -> None:
    with pytest.raises(EmitError, match="has no entry in LowerCtx.functions"):
        items(InternalCall(loc=LOC, ty=Ty.U32, fn_name="nope", args=()))


# ===========================================================================
# MakeStruct (P1's asymmetric pair, C9's order)
# ===========================================================================

#: The Phase 0 shape (findings SS 1, row 7), field names already in P7's
#: byte-string order.
SETTINGS = make_struct(
    "Settings",
    [
        ("counter_limit", const(Ty.U32, 3)),
        ("display_name", const(Ty.String, "serpent phase zero")),
    ],
)

SETTINGS_INVENTORY = LiteralInventory(
    symbols_over_9=(),
    strings=("serpent phase zero",),
    bytes_literals=(),
    address_strkeys=(),
    struct_key_descriptor_sets=(("counter_limit", "display_name"),),
)


def test_a_struct_is_built_and_both_fields_read_back() -> None:
    """End to end through `map_new_from_linear_memory` and `map_get`."""
    memory = Memory()
    memory.seed(SETTINGS_INVENTORY)
    limit = FieldGet(
        loc=LOC, ty=Ty.U32, obj=SETTINGS, field="counter_limit", struct_name="Settings"
    )
    assert run(limit, memory=memory) == val.pack_u32val(3)

    memory = Memory()
    memory.seed(SETTINGS_INVENTORY)
    name = FieldGet(
        loc=LOC, ty=Ty.String, obj=SETTINGS, field="display_name", struct_name="Settings"
    )
    store, host_ = start(name, memory=memory)
    word = host_.invoke("probe")
    assert word is not None
    assert store.text_of(word) == "serpent phase zero"


def test_the_key_descriptor_blob_dedupes_to_the_seeded_offset() -> None:
    """E7: the pool is a pure function of the INVENTORY, not of emission order.

    Task 10 seeds the same descriptor blob from
    `struct_key_descriptor_sets`; lowering interns it again with the same
    recipe and must HIT. Appending a second copy would still run -- the module
    would just carry two identical blobs and a pool offset that depends on
    which body compiled first.
    """
    memory = Memory()
    memory.seed(SETTINGS_INVENTORY)
    seeded = memory.pool_bytes()
    build(SETTINGS, memory=memory)
    assert memory.pool_bytes() == seeded


def test_the_field_values_are_stored_in_the_nodes_own_order() -> None:
    """C9/P7: C sorted the fields and D never re-sorts. The value written at
    `scratch + 8*i` must be field `i`'s -- swapping the two arrays is the
    layout error that "validates and then panics on-chain" (P1)."""
    memory = Memory()
    memory.seed(SETTINGS_INVENTORY)
    store, host_ = start(SETTINGS, memory=memory)
    word = host_.invoke("probe")
    assert word is not None
    entries = store.objects[val.body_of(word)]
    assert isinstance(entries, dict)
    assert entries["counter_limit"] == val.pack_u32val(3)
    assert store.text_of(entries["display_name"]) == "serpent phase zero"


def test_a_descending_key_descriptor_blob_is_rejected_by_the_harness() -> None:
    """F.1.5, the negative control -- and the C9 mutation proof.

    A struct whose fields are NOT in ascending byte order is exactly what a
    lowering that re-sorted (or forgot to preserve) C's order would produce.
    env.json says the host PANICS; the harness says so too, so the mistake is
    caught locally instead of on chain. If this test ever passes, the check in
    `objects.py::map_new_from_linear_memory` has stopped working and every
    other struct test in this file is worth less than it looks.
    """
    descending = make_struct(
        "Backwards",
        [
            ("display_name", const(Ty.String, "serpent phase zero")),
            ("counter_limit", const(Ty.U32, 3)),
        ],
    )
    _store, host_ = start(descending)
    with pytest.raises(AssertionError, match="not in ascending order"):
        host_.invoke("probe")


def test_an_empty_struct_is_loud() -> None:
    with pytest.raises(EmitError, match="has no fields"):
        items(make_struct("Empty", []))


# ===========================================================================
# FieldGet: the key Symbol's form must match C's accounting
# ===========================================================================


def test_a_short_field_name_is_a_SymbolSmall_immediate() -> None:
    """S22 and `frontend.py`'s `_collect_host_fns`: 9 characters or fewer needs
    no pool entry and no host call, and C put no constructor in
    `host_fns_used` for it. An import this body called but C never declared
    would fail at assembly."""
    node = FieldGet(
        loc=LOC,
        ty=Ty.U32,
        obj=ParamRef(loc=LOC, ty=Ty.Struct("S"), index=0, name="s"),
        field="limit",
        struct_name="S",
    )
    # `fail_with_error` follows because a `FieldGet` result is narrowed to the
    # field's type (Task 9, S3's second sentence): `map_get` answers with
    # whatever was stored, and `U32` is a claim about it, not a proof.
    assert import_calls(items(node, nparams=1)) == ["map_get", "fail_with_error"]


def test_a_long_field_name_is_pooled_and_built_from_linear_memory() -> None:
    node = FieldGet(
        loc=LOC,
        ty=Ty.U32,
        obj=ParamRef(loc=LOC, ty=Ty.Struct("S"), index=0, name="s"),
        field="counter_limit",
        struct_name="S",
    )
    memory = Memory()
    body = items(node, nparams=1, memory=memory)
    assert import_calls(body) == [
        "symbol_new_from_linear_memory",
        "map_get",
        "fail_with_error",
    ]
    assert b"counter_limit" in memory.pool_bytes()


# ===========================================================================
# MakeVec (ruling C4)
# ===========================================================================


def test_a_static_immediate_vec_is_laid_out_in_the_data_pool() -> None:
    node = make_vec(Ty.U32, [const(Ty.U32, i) for i in (1, 2, 3)], all_static=True)
    memory = Memory()
    body = items(node, memory=memory)
    assert import_calls(body) == ["vec_new_from_linear_memory"]
    expected = b"".join(struct.pack("<Q", val.pack_u32val(i)) for i in (1, 2, 3))
    assert expected in memory.pool_bytes()


def test_a_static_immediate_vec_runs_and_holds_its_items() -> None:
    node = make_vec(Ty.U32, [const(Ty.U32, i) for i in (1, 2, 3)], all_static=True)
    assert run(host("vec_len", Ty.U32, node)) == val.pack_u32val(3)
    assert run(host("vec_get", Ty.U32, node, const(Ty.U32, 2))) == val.pack_u32val(3)


def test_an_EITHER_item_too_big_for_the_small_form_takes_the_chain() -> None:
    """C4, and the reason `all_static` is not the gate. `U64(2**56)` is a
    perfectly static literal whose `Val` is an OBJECT HANDLE that does not
    exist until `obj_from_u64` has run -- there is no word to lay out."""
    big = val.MAX_SMALL_U64 + 1
    node = make_vec(Ty.U64, [const(Ty.U64, 1), const(Ty.U64, big)], all_static=True)
    assert import_calls(items(node)) == [
        "vec_new",
        "vec_push_back",
        "obj_from_u64",
        "vec_push_back",
    ]


def test_a_host_object_item_takes_the_chain() -> None:
    node = make_vec(Ty.String, [const(Ty.String, "hello")], all_static=True)
    assert import_calls(items(node)) == [
        "vec_new",
        "string_new_from_linear_memory",
        "vec_push_back",
    ]


def test_a_non_static_vec_takes_the_chain() -> None:
    node = make_vec(Ty.U32, [ParamRef(loc=LOC, ty=Ty.U32, index=0, name="x")], all_static=False)
    assert import_calls(items(node, nparams=1)) == ["vec_new", "vec_push_back"]


def test_every_push_back_result_is_rebound(  # F.1.9
) -> None:
    """F.1.9, executed. `vec_push_back` returns a NEW handle; the harness
    models that faithfully (a fresh object per push). A chain that dropped the
    result and pushed onto the original handle again would build a ONE-element
    vector, three times over, and report success -- so the length is the
    assertion that catches it."""
    node = make_vec(
        Ty.U32,
        [ParamRef(loc=LOC, ty=Ty.U32, index=i, name=f"p{i}") for i in range(3)],
        all_static=False,
    )
    length = host("vec_len", Ty.U32, node)
    words = [val.pack_u32val(v) for v in (10, 20, 30)]
    assert run(length, *words, nparams=3) == val.pack_u32val(3)
    # ...and in the node's order, not reversed.
    third = host("vec_get", Ty.U32, node, const(Ty.U32, 2))
    assert run(third, *words, nparams=3) == val.pack_u32val(30)


# ===========================================================================
# MakeMap (ruling E12)
# ===========================================================================


def test_a_static_U32_keyed_map_still_takes_the_chain() -> None:
    """RULING E12, and the one that would validate and then panic.

    Every key and value here is a static immediate and `all_static` is `True`,
    so reading that flag as a licence gives the linear-memory form. But `m.9`'s
    keys are DESCRIPTORS of byte strings "convertible to `Symbol` type" (P1) --
    a `U32` key has no such form at all. The chain is the only lowering.
    """
    node = make_map(
        Ty.U32,
        Ty.U32,
        [(const(Ty.U32, 1), const(Ty.U32, 10)), (const(Ty.U32, 2), const(Ty.U32, 20))],
        all_static=True,
    )
    assert import_calls(items(node)) == ["map_new", "map_put", "map_put"]
    assert run(host("map_get", Ty.U32, node, const(Ty.U32, 2))) == val.pack_u32val(20)


def test_a_static_symbol_keyed_map_uses_the_descriptor_form() -> None:
    node = make_map(
        Ty.Symbol,
        Ty.U32,
        [
            (const(Ty.Symbol, "alpha"), const(Ty.U32, 1)),
            (const(Ty.Symbol, "beta"), const(Ty.U32, 2)),
        ],
        all_static=True,
    )
    assert import_calls(items(node)) == ["map_new_from_linear_memory"]
    assert run(host("map_get", Ty.U32, node, const(Ty.Symbol, "beta"))) == val.pack_u32val(2)


def test_a_symbol_keyed_map_with_a_runtime_key_takes_the_chain() -> None:
    """The descriptor contract (P1) needs the key NAME at compile time."""
    node = make_map(
        Ty.Symbol,
        Ty.U32,
        [(ParamRef(loc=LOC, ty=Ty.Symbol, index=0, name="k"), const(Ty.U32, 1))],
        all_static=False,
    )
    assert import_calls(items(node, nparams=1)) == ["map_new", "map_put"]


def test_the_chain_puts_pairs_in_the_nodes_own_order(  # F.1.10
) -> None:
    """F.1.10: the last write wins at tier 1, so the order the pairs are put in
    is observable. It is also the F.1.9 rebinding proof for `map_put`: a
    dropped result would leave a one-entry map."""
    node = make_map(
        Ty.U32,
        Ty.U32,
        [
            (const(Ty.U32, 1), const(Ty.U32, 10)),
            (const(Ty.U32, 2), const(Ty.U32, 20)),
            (const(Ty.U32, 1), const(Ty.U32, 99)),
        ],
        all_static=False,
    )
    assert run(host("map_get", Ty.U32, node, const(Ty.U32, 1))) == val.pack_u32val(99)
    assert run(host("map_get", Ty.U32, node, const(Ty.U32, 2))) == val.pack_u32val(20)


def test_descending_symbol_keys_are_refused_before_the_host_can_panic() -> None:
    """C orders static keys through the tier-1 oracle (A8/A14). If it ever
    stopped, the descriptor blob would be descending and `m.9` would PANIC --
    so this is loud at emit time rather than undiagnosable on chain."""
    node = make_map(
        Ty.Symbol,
        Ty.U32,
        [
            (const(Ty.Symbol, "beta"), const(Ty.U32, 2)),
            (const(Ty.Symbol, "alpha"), const(Ty.U32, 1)),
        ],
        all_static=True,
    )
    with pytest.raises(EmitError, match="not strictly ascending"):
        items(node)


def test_an_empty_map_literal_takes_the_chain() -> None:
    node = make_map(Ty.Symbol, Ty.U32, [], all_static=False)
    assert import_calls(items(node)) == ["map_new"]


def test_an_empty_STATIC_symbol_keyed_map_is_loud() -> None:
    """The asymmetry `MakeStruct` already closed. `map_new_from_linear_memory`
    over a zero-length key array has nothing to describe, and `intern(b"")` +
    `scratch(0)` would hand the host two offsets pointing at no data.
    Unreachable from frontend IR -- `recognize._all_static` answers `False` for
    an empty container (MJ-15) -- so this pins the refusal, not a live path."""
    node = make_map(Ty.Symbol, Ty.U32, [], all_static=True)
    with pytest.raises(EmitError, match="empty map literal"):
        items(node)


# ===========================================================================
# MakeTopics (D8)
# ===========================================================================


def test_static_topics_use_the_linear_memory_vec_form() -> None:
    """Mirrors `frontend.py`'s `_bulk_construction_can_use_memory`: every topic
    a `Const` with a knowable immediate word."""
    node = MakeTopics(
        loc=LOC,
        ty=Ty.Vec(Ty.Symbol),
        topics=(const(Ty.Symbol, "transfer"), const(Ty.U32, 1)),
    )
    assert import_calls(items(node)) == ["vec_new_from_linear_memory"]
    assert run(host("vec_len", Ty.U32, node)) == val.pack_u32val(2)


def test_topics_with_a_pooled_literal_take_the_chain() -> None:
    node = MakeTopics(
        loc=LOC,
        ty=Ty.Vec(Ty.Symbol),
        topics=(const(Ty.Symbol, "transfer"), const(Ty.String, "note")),
    )
    assert import_calls(items(node)) == [
        "vec_new",
        "vec_push_back",
        "string_new_from_linear_memory",
        "vec_push_back",
    ]


# ===========================================================================
# The Task 9 narrowing hook
# ===========================================================================


def test_narrow_to_checks_the_value_and_leaves_it_on_the_stack() -> None:
    """Task 9 replaced the stub. What survives from the handoff is the
    STRUCTURAL contract every call site in this module depends on: the check is
    net 0 on the operand stack, so `lower_expr`'s own `expr_scope` still sees
    net +1 (review M1). Its behaviour is pinned in
    `test_emitter_lower_stmts.py`."""
    fn = Fn("probe", 0, 0, ("i64",))
    fn.i64_const(val.pack_u32val(1))
    before = list(fn.stack)
    lower.narrow_to(fn, LowerCtx(1, Memory()), Ty.U32)
    assert fn.stack == before
    # And it is not the old no-op: the check really is in the body.
    assert CallImport("fail_with_error") in fn.finish()
