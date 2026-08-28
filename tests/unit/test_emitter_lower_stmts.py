"""Tests for `serpent.emitter.lower` -- statements, the ABI prologue, assembly.

Dossier §B.3.2 in full, plus `compile_function`: the per-result-arity tail rule
(M2/E4/C1), the E14 ABI prologue, and S3's second sentence (the narrowing check
on a host-call return). Almost everything is **executed** against
`tests/harness`, for the reason the two sibling files give: every failure this
file guards against produces a *plausible* wrong answer.

What is being protected, in order of how badly it would bite:

* **E4's guard ORDER.** `fail_with_error(CODE_UNREACHABLE_GUARD)` then
  `unreachable`, never the reverse. Reversed, the only `unreachable` in the
  module becomes a bare one and R3's "an error code is never lost to
  `unreachable`" is broken -- while the module still validates and still
  aborts, so nothing else notices.
* **M3's form.** The scan is not "no `unreachable` after `fail_with_error`"
  (Task 9 emits exactly that shape on purpose); it is "every `unreachable` is
  the guard's, and the guard's code is `CODE_UNREACHABLE_GUARD`". A body whose
  last statement is a user `raise` reaches the tail REACHABLE, so its dead tail
  guard is permitted -- what a runner observes there is the user's own code.
* **E14, the prologue.** Tag AND range, per declared type. A dropped check
  turns a wrong-tag argument into a value of the wrong type flowing through
  arithmetic that cannot see it.
* **S3's second sentence, `narrow_to`.** A host `Val` return is ANY-typed; the
  checker's `ty` is a claim. A corrupted ledger entry must become a contract
  error, not a mis-typed read.
* **M2, per-result-arity.** A void INTERNAL helper is compiled with zero
  results; pushing `VOID_VAL` into one is invalid wasm.
* **Task 2's loud contract**: a `Break` with no breakable block raises at
  LOWERING time rather than emitting a branch that lands somewhere plausible.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest

from serpent import errors, val
from serpent.compiler.diagnostics import Loc
from serpent.compiler.ir import (
    Binary,
    BinaryOp,
    Break,
    Compare,
    CompareOp,
    Const,
    Continue,
    Eval,
    FuncIR,
    FuncKind,
    HostCall,
    If,
    IfExp,
    InternalCall,
    IRExpr,
    IRStmt,
    LetLocal,
    LocalRef,
    Nop,
    ParamRef,
    Raise,
    RawScalar,
    RawScalarKind,
    Return,
    SetLocal,
    While,
)
from serpent.compiler.types_ import Ty
from serpent.emitter import arith, encode, lower, opcodes
from serpent.emitter.frame import CallDefined, CallImport, CodeItem, EmitError, Fn
from serpent.emitter.layout import Memory
from serpent.emitter.lower import LowerCtx
from serpent.types import Bytes
from tests.harness import engine, testmod
from tests.harness.objects import STORAGE_INSTANCE, ObjectStore

LOC = Loc.whole_file("contracts/t.py")

ABI_FAILED = val.error_val(errors.CODE_ABI_CHECK_FAILED)
UNREACHABLE_GUARD = val.error_val(errors.CODE_UNREACHABLE_GUARD)


# --- IR shorthand ---------------------------------------------------------------


def const(ty: Ty, value: object) -> Const:
    return Const(loc=LOC, ty=ty, py_value=value)


def local(slot: int, ty: Ty = Ty.U32, name: str = "x") -> LocalRef:
    return LocalRef(loc=LOC, ty=ty, slot=slot, name=name)


def param(index: int, ty: Ty, name: str = "p") -> ParamRef:
    return ParamRef(loc=LOC, ty=ty, index=index, name=name)


def add(lhs: IRExpr, rhs: IRExpr, ty: Ty = Ty.U32) -> Binary:
    return Binary(loc=LOC, ty=ty, op=BinaryOp.ADD, lhs=lhs, rhs=rhs)


def cmp_(op: CompareOp, lhs: IRExpr, rhs: IRExpr) -> Compare:
    return Compare(loc=LOC, ty=Ty.Bool, op=op, lhs=lhs, rhs=rhs, via_obj_cmp=False)


def host(name: str, ty: Ty, *args: IRExpr) -> HostCall:
    return HostCall(loc=LOC, ty=ty, fn_name=name, args=args)


def storage_type(value: int) -> RawScalar:
    return RawScalar(loc=LOC, ty=Ty.U32, value=value, kind=RawScalarKind.STORAGE_TYPE)


def put(key: str, value: int) -> Eval:
    return Eval(
        loc=LOC,
        value=host(
            "put_contract_data",
            Ty.Void,
            const(Ty.Symbol, key),
            const(Ty.U32, value),
            storage_type(STORAGE_INSTANCE),
        ),
    )


def func(
    name: str,
    body: Sequence[IRStmt],
    *,
    kind: FuncKind = FuncKind.EXPORT,
    params: Sequence[tuple[str, Ty]] = (),
    ret: Ty = Ty.U32,
    locals_: Sequence[tuple[int, str, Ty]] = (),
    rope: bool = True,
) -> FuncIR:
    return FuncIR(
        loc=LOC,
        py_name=name,
        export_name=name,
        kind=kind,
        params=tuple((n, t, LOC) for n, t in params),
        ret=ret,
        doc="",
        locals=tuple(locals_),
        body=tuple(body),
        returns_on_every_path=rope,
    )


# --- building and running -------------------------------------------------------


def compile_all(funcs: Sequence[FuncIR]) -> tuple[list[Fn], LowerCtx, Memory]:
    """Compile every `FuncIR` against ONE shared context, in declaration order."""
    memory = Memory()
    ctx = LowerCtx(
        n_module_functions=len(funcs),
        memory=memory,
        functions={f.py_name: i for i, f in enumerate(funcs)},
    )
    return [lower.compile_function(f, ctx) for f in funcs], ctx, memory


def module_of(funcs: Sequence[FuncIR]) -> tuple[bytes, list[Fn], LowerCtx]:
    fns, ctx, memory = compile_all(funcs)
    specs: list[testmod.FunctionSpec] = [
        (f.name, f.nparams, f.nlocals, f.results, f.finish()) for f in fns
    ]
    specs.extend((p.name, p.nparams, p.nlocals, p.results, p.body) for p in ctx.parts)
    needs = not memory.is_empty or ctx.needs_memory
    wasm = testmod.build_test_module(
        specs,
        imports=ctx.import_order,
        memory_pages=1 if needs else None,
        data=memory.pool_bytes() if needs else None,
    )
    return wasm, fns, ctx


def start(
    funcs: Sequence[FuncIR], store: ObjectStore | None = None
) -> tuple[ObjectStore, engine.MiniHost]:
    store = ObjectStore() if store is None else store
    wasm, _fns, _ctx = module_of(funcs)
    host_ = engine.MiniHost(wasm, imports=store.bindings())
    store.attach(host_)
    return store, host_


def run(funcs: Sequence[FuncIR], *args: int, store: ObjectStore | None = None) -> int | None:
    _store, host_ = start(funcs, store)
    return host_.invoke(funcs[0].export_name, *args)


def body_of(f: FuncIR, *rest: FuncIR) -> list[CodeItem]:
    """The finished body of `f`, compiled alongside any helpers it calls."""
    fns, _ctx, _memory = compile_all([f, *rest])
    return fns[0].finish()


def ctx_only() -> LowerCtx:
    return LowerCtx(n_module_functions=1, memory=Memory())


# --- a real instruction walker, for the M3-form scan ------------------------------
#
# Byte-scanning for 0x00 cannot answer M3's question: `local.get 0` is
# `0x20 0x00`, a `uleb(0)` memarg offset is `0x00`, and every one of those would
# read as an `unreachable`. The scan therefore DECODES, with a table of the
# immediate shape of every opcode this emitter can emit -- and refuses an opcode
# it has never seen, so a new instruction cannot slip past the check silently.

#: opcode -> the immediates that follow it: "" none, "u" one uleb, "s" one sleb,
#: "b" one raw byte (a blocktype), "uu" a memarg.
_IMMEDIATES: dict[int, str] = {
    opcodes.UNREACHABLE: "",
    opcodes.BLOCK: "b",
    opcodes.LOOP: "b",
    opcodes.IF: "b",
    opcodes.ELSE: "",
    opcodes.END: "",
    opcodes.BR: "u",
    opcodes.BR_IF: "u",
    opcodes.RETURN: "",
    opcodes.DROP: "",
    opcodes.LOCAL_GET: "u",
    opcodes.LOCAL_SET: "u",
    opcodes.LOCAL_TEE: "u",
    opcodes.I32_CONST: "s",
    opcodes.I64_CONST: "s",
    opcodes.I64_LOAD: "uu",
    opcodes.I64_STORE: "uu",
    opcodes.I32_WRAP_I64: "",
    opcodes.I64_EXTEND_I32_U: "",
    opcodes.I64_EXTEND32_S: "",
    **{op: "" for op in range(opcodes.I64_EQZ, opcodes.I64_GE_U + 1)},
    **{op: "" for op in range(opcodes.I64_ADD, opcodes.I64_SHR_U + 1)},
}


def _leb(data: bytes, i: int, *, signed: bool) -> tuple[int, int]:
    """Consume one LEB128 group run at `i`; return `(value, next index)`.

    The walker only needs the LENGTH, but the value is decoded properly --
    SIGN-EXTENDED for an `sleb` -- because the guard assertion has to read the
    error `Val` an `i64.const` carries, and every reserved code has its high
    bit set (P4's exact trap: a masked comparison cannot tell these apart).
    """
    shift = 0
    out = 0
    while True:
        byte = data[i]
        i += 1
        out |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            if signed and byte & 0x40:
                out -= 1 << shift
            return out, i


def instructions(items: Sequence[CodeItem]) -> Iterator[tuple[object, int | None]]:
    """Every instruction of a finished body, as `(opcode | call site, immediate)`.

    A `CallImport`/`CallDefined` item is yielded as itself: the call opcode is
    not in the byte runs at all (review B1 keeps it symbolic until pass 2).
    """
    for item in items:
        if not isinstance(item, bytes):
            yield item, None
            continue
        i = 0
        while i < len(item):
            op = item[i]
            i += 1
            shape = _IMMEDIATES.get(op)
            if shape is None:
                raise AssertionError(
                    f"the M3 scan does not know opcode {op:#04x}; add it to _IMMEDIATES "
                    "with its immediate shape rather than letting it decode as garbage"
                )
            first: int | None = None
            for kind in shape:
                if kind == "b":
                    value = item[i]
                    i += 1
                else:
                    value, i = _leb(item, i, signed=kind == "s")
                first = value if first is None else first
            yield op, first


def unreachable_positions(items: Sequence[CodeItem]) -> list[int]:
    return [i for i, (op, _imm) in enumerate(instructions(items)) if op == opcodes.UNREACHABLE]


def assert_m3_form(items: Sequence[CodeItem], name: str) -> int:
    """Every `unreachable` in `items` is E4's guard, in E4's ORDER. Returns how many.

    The guard is `i64.const <CODE_UNREACHABLE_GUARD ErrorVal>`, `call
    fail_with_error`, `drop`, `unreachable` -- checked as the three
    instructions IMMEDIATELY before each `unreachable`, which is what makes a
    swapped order (the `unreachable` first) a failure rather than a rearranged
    pass.
    """
    decoded = list(instructions(items))
    found = 0
    for i, (op, _imm) in enumerate(decoded):
        if op != opcodes.UNREACHABLE:
            continue
        found += 1
        assert i >= 3, f"{name}: an `unreachable` with no room for the guard before it"
        const_op, const_imm = decoded[i - 3]
        call, _call_imm = decoded[i - 2]
        drop, _drop_imm = decoded[i - 1]
        assert const_op == opcodes.I64_CONST, f"{name}: no i64.const before the guard call"
        assert const_imm is not None
        assert val.as_u64(const_imm) == UNREACHABLE_GUARD, (
            f"{name}: the guard's error Val is {const_imm:#x}, not CODE_UNREACHABLE_GUARD"
        )
        assert call == CallImport("fail_with_error"), f"{name}: {call!r} is not fail_with_error"
        assert drop == opcodes.DROP, f"{name}: the guard's Void Val is not dropped"
    return found


# ===========================================================================
# C1 / E4: the diverging tail
# ===========================================================================


def diverging() -> FuncIR:
    """D.1.2's shape: `while True:` with no `break`, non-void, rope=True.

    The exit block is still REACHABLE to wasm (and to `Fn`): `br_if` targets
    it, and no validator evaluates `True`. So the tail is emitted -- and C16
    says only a diverging body can get there.
    """
    return func("loops", [While(loc=LOC, cond=const(Ty.Bool, True), body=())])


def test_a_diverging_tail_ends_in_fail_with_error_then_drop_then_unreachable() -> None:
    """C1 + ruling E4, in that ORDER. The `unreachable` satisfies the
    validator; the `fail_with_error` before it is what keeps R3's promise that
    an error code is never lost to a bare trap."""
    items = body_of(diverging())
    assert items[-1] == bytes([opcodes.DROP, opcodes.UNREACHABLE, opcodes.END])
    assert items[-2] == CallImport("fail_with_error")
    head = items[-3]
    assert isinstance(head, bytes)
    assert head.endswith(bytes([opcodes.I64_CONST]) + encode.sleb(val.as_i64(UNREACHABLE_GUARD)))


def test_a_diverging_body_aborts_with_CODE_UNREACHABLE_GUARD_if_it_ever_falls_out() -> None:
    """Structurally dead (C19: "should never fire at runtime") -- so it is
    reached here by a condition that is false on the first test rather than by
    the impossible `while True` exit, which proves the guard is real code."""
    node = func("loops", [While(loc=LOC, cond=const(Ty.Bool, False), body=())])
    store, host_ = start([node])
    with pytest.raises(engine.HostError) as info:
        host_.invoke("loops")
    assert info.value.val == UNREACHABLE_GUARD
    assert store.errors == [UNREACHABLE_GUARD]


def test_a_non_void_function_that_does_not_return_on_every_path_is_an_EmitError() -> None:
    """C16: C's definite-return proof is what makes the tail rule sound. A
    `returns_on_every_path=False` non-void `FuncIR` cannot reach D."""
    node = func("bad", [Nop(loc=LOC)], rope=False)
    with pytest.raises(EmitError, match="returns_on_every_path"):
        lower.compile_function(node, ctx_only())


# ===========================================================================
# M3: the form of every `unreachable` in every emitted body
# ===========================================================================


def raising_tail() -> FuncIR:
    """M3's second shape: a non-void body whose LAST statement is a user
    `raise`. `fail_with_error` does not return, but wasm does not know that, so
    the tail is reachable and gets its (dead) guard -- permitted, because what a
    runner observes there is the user's own code."""
    return func("always_raises", [Raise(loc=LOC, enum="E", case="Nope", code=7)])


def every_shape() -> list[FuncIR]:
    """One of every statement shape this file builds, for the M3 scan."""
    return [
        diverging(),
        raising_tail(),
        summing_with_break(),
        summing_with_continue(),
        func("plain", [Return(loc=LOC, value=const(Ty.U32, 1))]),
        func("branch", [if_stmt()]),
        func("void_export", [put("v", 1)], ret=Ty.Void, rope=False),
        void_helper(),
        func("prologued", [Return(loc=LOC, value=const(Ty.U32, 1))], params=[("a", Ty.U32)]),
    ]


def test_every_unreachable_in_every_body_is_the_guards_and_none_is_bare() -> None:
    """M3, as the review reworded it. Note what this does NOT say: it does not
    forbid an `unreachable` after a `fail_with_error` (Task 9 emits exactly
    that on purpose). It says every `unreachable` IS one, carrying
    `CODE_UNREACHABLE_GUARD`, with the call and its `drop` immediately before."""
    guarded = {
        f.py_name: assert_m3_form(body_of(f, void_helper()), f.py_name) for f in every_shape()
    }
    # Exactly three shapes reach their tail still REACHABLE with a non-void
    # return, and each is one of C19's two named cases: the diverging body
    # (`loops`), the body ending in a user `raise` (`always_raises`), and the
    # exhaustive if/else whose arms both return (`branch`) -- "after an
    # exhaustive dispatch or a definite-return-proven function tail". Everything
    # else ends in a `return`, which leaves the function unreachable and needs
    # no guard at all. A changed count means a shape gained or lost one.
    assert {name for name, n in guarded.items() if n} == {"loops", "always_raises", "branch"}
    assert sum(guarded.values()) == 3


def test_the_scan_would_catch_a_bare_unreachable() -> None:
    """The scan's own negative control: without it, "every `unreachable` is a
    guard" is a sentence no test disagrees with."""
    fn = Fn("bare", 0, 0, ("i64",))
    fn.unreachable_()
    with pytest.raises(AssertionError, match="no room for the guard"):
        assert_m3_form(fn.finish(), "bare")


def test_the_scan_would_catch_a_swapped_guard_order() -> None:
    """E4's order, falsified directly: `unreachable` BEFORE the call still
    validates, still aborts, and loses the code."""
    fn = Fn("swapped", 0, 0, ("i64",))
    fn.i64_const(1)
    fn.drop()
    fn.i64_const(2)
    fn.drop()
    fn.unreachable_()
    fn.i64_const(UNREACHABLE_GUARD)
    fn.call_import("fail_with_error", 1, has_result=True)
    fn.drop()
    with pytest.raises(AssertionError, match="no i64.const before the guard call"):
        assert_m3_form(fn.finish(), "swapped")


def test_a_user_raise_emits_no_unreachable_of_its_own() -> None:
    """P14, on-chain-verified: an `unreachable` after `fail_with_error` would
    replace the contract error the client needs to see with a generic VM trap.
    The only `unreachable` in `always_raises` is the tail guard's."""
    items = body_of(raising_tail())
    assert len(unreachable_positions(items)) == 1
    decoded = [op for op, _imm in instructions(items)]
    first_call = decoded.index(CallImport("fail_with_error"))
    assert decoded[first_call + 1] == opcodes.DROP
    assert decoded[first_call + 2] != opcodes.UNREACHABLE


def test_a_user_raise_is_what_a_runner_observes() -> None:
    """The dead tail guard sits behind the user's abort, not in front of it."""
    store, host_ = start([raising_tail()])
    with pytest.raises(engine.HostError) as info:
        host_.invoke("always_raises")
    assert info.value.val == val.error_val(7)
    assert store.errors == [val.error_val(7)]


# ===========================================================================
# §B.3.2: the statements
# ===========================================================================


def if_stmt() -> If:
    return If(
        loc=LOC,
        cond=cmp_(CompareOp.LT, const(Ty.U32, 1), const(Ty.U32, 2)),
        body=(Return(loc=LOC, value=const(Ty.U32, 10)),),
        orelse=(Return(loc=LOC, value=const(Ty.U32, 20)),),
    )


def test_if_takes_the_then_arm_and_else_takes_the_other() -> None:
    def branch(lhs: int, rhs: int) -> FuncIR:
        return func(
            "branch",
            [
                If(
                    loc=LOC,
                    cond=cmp_(CompareOp.LT, const(Ty.U32, lhs), const(Ty.U32, rhs)),
                    body=(Return(loc=LOC, value=const(Ty.U32, 10)),),
                    orelse=(Return(loc=LOC, value=const(Ty.U32, 20)),),
                )
            ],
        )

    assert run([branch(1, 2)]) == val.pack_u32val(10)
    assert run([branch(2, 1)]) == val.pack_u32val(20)


def test_an_if_with_no_else_falls_through_to_the_code_after_it() -> None:
    node = func(
        "maybe",
        [
            LetLocal(loc=LOC, slot=0, ty=Ty.U32, init=const(Ty.U32, 1)),
            If(
                loc=LOC,
                cond=cmp_(CompareOp.EQ, const(Ty.U32, 1), const(Ty.U32, 1)),
                body=(SetLocal(loc=LOC, slot=0, value=const(Ty.U32, 9)),),
                orelse=(),
            ),
            Return(loc=LOC, value=local(0)),
        ],
        locals_=[(0, "x", Ty.U32)],
    )
    assert run([node]) == val.pack_u32val(9)


def test_a_let_and_a_set_write_the_slot_offset_by_the_parameter_count() -> None:
    """SS C.3: params occupy `[0, nparams)`, so declared slot `s` is local
    `nparams + s`. Off by the parameter count and the function overwrites its
    own argument -- which validates, and returns a plausible number."""
    node = func(
        "shadowed",
        [
            LetLocal(loc=LOC, slot=0, ty=Ty.U32, init=const(Ty.U32, 5)),
            Return(loc=LOC, value=add(param(0, Ty.U32), local(0))),
        ],
        params=[("a", Ty.U32)],
        locals_=[(0, "x", Ty.U32)],
    )
    assert run([node], val.pack_u32val(37)) == val.pack_u32val(42)


def test_nop_lowers_to_nothing() -> None:
    with_nop = body_of(func("n", [Nop(loc=LOC), Return(loc=LOC, value=const(Ty.U32, 1))]))
    without = body_of(func("n", [Return(loc=LOC, value=const(Ty.U32, 1))]))
    assert with_nop == without


def test_eval_of_a_void_host_call_drops_the_Void_Val() -> None:
    """P14: `put_contract_data` nominally returns a Void `Val`, and the `drop`
    is what keeps the frame balanced for the validator."""
    node = func("store", [put("k", 4), Return(loc=LOC, value=const(Ty.U32, 1))])
    store, host_ = start([node])
    assert host_.invoke("store") == val.pack_u32val(1)
    assert store.storage[(STORAGE_INSTANCE, "k")] == val.pack_u32val(4)


def void_helper() -> FuncIR:
    """E11ii: a `-> None` INTERNAL helper is compiled with ZERO results."""
    return func("remember", [put("h", 8)], kind=FuncKind.INTERNAL, ret=Ty.Void, rope=False)


def test_eval_of_a_void_internal_call_has_nothing_to_drop() -> None:
    """E11ii, review M2. The callee has zero results, so a `drop` after it
    would pop an operand that was never pushed -- caught here, at the node."""
    caller = func(
        "call_it",
        [
            Eval(loc=LOC, value=InternalCall(loc=LOC, ty=Ty.Void, fn_name="remember", args=())),
            Return(loc=LOC, value=const(Ty.U32, 1)),
        ],
    )
    store, host_ = start([caller, void_helper()])
    assert host_.invoke("call_it") == val.pack_u32val(1)
    assert store.storage[(STORAGE_INSTANCE, "h")] == val.pack_u32val(8)
    assert opcodes.DROP not in [op for op, _imm in instructions(body_of(caller, void_helper()))]


def test_a_void_internal_helper_falls_off_its_end_with_no_value_at_all() -> None:
    """M2's third arity case: `results == ()` -- the tail emits NOTHING, and
    `finish()` accepts the empty stack."""
    fns, _ctx, _memory = compile_all([void_helper()])
    assert fns[0].results == ()
    store, host_ = start([void_helper()])
    assert host_.invoke("remember") is None
    assert store.storage[(STORAGE_INSTANCE, "h")] == val.pack_u32val(8)


def test_a_void_export_falls_off_with_VOID_VAL() -> None:
    """The other half of M2: an EXPORT is `("i64",)` whatever it returns (S23),
    so a `-> None` method that falls off the end returns the Void `Val`."""
    node = func("touch", [put("t", 1)], ret=Ty.Void, rope=False)
    fns, _ctx, _memory = compile_all([node])
    assert fns[0].results == ("i64",)
    assert run([node]) == val.VOID_VAL


def test_a_bare_return_in_a_void_export_returns_VOID_VAL() -> None:
    node = func("touch", [Return(loc=LOC, value=None)], ret=Ty.Void)
    assert run([node]) == val.VOID_VAL


def test_a_bare_return_in_a_void_internal_helper_pushes_nothing() -> None:
    node = func("h", [Return(loc=LOC, value=None)], kind=FuncKind.INTERNAL, ret=Ty.Void)
    fns, _ctx, _memory = compile_all([node])
    assert fns[0].results == ()
    assert run([node]) is None


# ===========================================================================
# While / Break / Continue (and frame.br_if_break)
# ===========================================================================


def summing_with_break() -> FuncIR:
    """`total = 0; i = 0; while i < 5: i += 1; total += i; if i == 3: break`."""
    return func(
        "sum_break",
        [
            LetLocal(loc=LOC, slot=0, ty=Ty.U32, init=const(Ty.U32, 0)),
            LetLocal(loc=LOC, slot=1, ty=Ty.U32, init=const(Ty.U32, 0)),
            While(
                loc=LOC,
                cond=cmp_(CompareOp.LT, local(1), const(Ty.U32, 5)),
                body=(
                    SetLocal(loc=LOC, slot=1, value=add(local(1), const(Ty.U32, 1))),
                    SetLocal(loc=LOC, slot=0, value=add(local(0), local(1))),
                    If(
                        loc=LOC,
                        cond=cmp_(CompareOp.EQ, local(1), const(Ty.U32, 3)),
                        body=(Break(loc=LOC),),
                        orelse=(),
                    ),
                ),
            ),
            Return(loc=LOC, value=local(0)),
        ],
        locals_=[(0, "total", Ty.U32), (1, "i", Ty.U32)],
    )


def summing_with_continue() -> FuncIR:
    """`while i < 6: i += 1; if i % 2 == 0: continue; total += i` -> 1 + 3 + 5."""
    return func(
        "sum_continue",
        [
            LetLocal(loc=LOC, slot=0, ty=Ty.U32, init=const(Ty.U32, 0)),
            LetLocal(loc=LOC, slot=1, ty=Ty.U32, init=const(Ty.U32, 0)),
            While(
                loc=LOC,
                cond=cmp_(CompareOp.LT, local(1), const(Ty.U32, 6)),
                body=(
                    SetLocal(loc=LOC, slot=1, value=add(local(1), const(Ty.U32, 1))),
                    If(
                        loc=LOC,
                        cond=cmp_(
                            CompareOp.EQ,
                            Binary(
                                loc=LOC,
                                ty=Ty.U32,
                                op=BinaryOp.MOD,
                                lhs=local(1),
                                rhs=const(Ty.U32, 2),
                            ),
                            const(Ty.U32, 0),
                        ),
                        body=(Continue(loc=LOC),),
                        orelse=(),
                    ),
                    SetLocal(loc=LOC, slot=0, value=add(local(0), local(1))),
                ),
            ),
            Return(loc=LOC, value=local(0)),
        ],
        locals_=[(0, "total", Ty.U32), (1, "i", Ty.U32)],
    )


def test_a_while_loop_with_a_break_stops_where_the_break_says() -> None:
    """1 + 2 + 3 -- and NOT 15, which is what a `break` that branched to the
    loop header instead of the exit block would produce. The `break` is nested
    inside an `if`, which is the everyday shape: `br_break` scans past the IF
    frame to the loop's own breakable exit block."""
    assert run([summing_with_break()]) == val.pack_u32val(6)


def test_a_while_loop_with_a_continue_skips_the_rest_of_the_body() -> None:
    """1 + 3 + 5. A `continue` that reached the exit block instead would
    return 1; one that skipped nothing would return 21."""
    assert run([summing_with_continue()]) == val.pack_u32val(9)


def test_a_while_whose_condition_is_false_runs_its_body_zero_times() -> None:
    node = func(
        "never",
        [
            LetLocal(loc=LOC, slot=0, ty=Ty.U32, init=const(Ty.U32, 7)),
            While(
                loc=LOC,
                cond=const(Ty.Bool, False),
                body=(SetLocal(loc=LOC, slot=0, value=const(Ty.U32, 99)),),
            ),
            Return(loc=LOC, value=local(0)),
        ],
        locals_=[(0, "x", Ty.U32)],
    )
    assert run([node]) == val.pack_u32val(7)


def test_a_while_is_block_then_loop_and_both_frames_are_void() -> None:
    """§B.3.2's shape, and S23's reason: multi-value is off, so neither frame
    can carry a result."""
    opened = [
        (op, imm)
        for op, imm in instructions(body_of(summing_with_break()))
        if op in (opcodes.BLOCK, opcodes.LOOP)
    ]
    assert opened == [
        (opcodes.BLOCK, opcodes.BLOCKTYPE_VOID),
        (opcodes.LOOP, opcodes.BLOCKTYPE_VOID),
    ]


def test_a_break_outside_a_breakable_block_raises_at_lowering_time() -> None:
    """Task 2's loud contract, intended: the alternative is a `br` that lands
    somewhere plausible and still validates."""
    fn = Fn("probe", 0, 0, ("i64",))
    with pytest.raises(EmitError, match="no enclosing breakable block"):
        lower.lower_stmt(fn, ctx_only(), Break(loc=LOC))


def test_a_continue_outside_a_loop_raises_at_lowering_time() -> None:
    fn = Fn("probe", 0, 0, ("i64",))
    with pytest.raises(EmitError, match="no enclosing loop"):
        lower.lower_stmt(fn, ctx_only(), Continue(loc=LOC))


def test_br_if_break_does_not_mark_the_code_after_it_dead() -> None:
    """The carried Task 2 note. A conditional branch has a FALL-THROUGH; if
    `br_if` set the unreachable state the tracker would stop checking the whole
    loop body -- silently, and only inside loops."""
    fn = Fn("probe", 0, 0, ("i64",))
    fn.begin_block(None, breakable=True)
    fn.begin_loop()
    fn.i32_const(0)
    fn.br_if_break()
    assert not fn.unreachable
    fn.end()
    fn.end()
    assert not fn.unreachable


def test_br_if_break_with_no_breakable_block_raises() -> None:
    fn = Fn("probe", 0, 0, ("i64",))
    fn.i32_const(0)
    with pytest.raises(EmitError, match="no enclosing breakable block"):
        fn.br_if_break()


def test_br_if_break_will_not_leave_an_operand_behind() -> None:
    """`br_if`'s branch arity is its target's, checked AFTER the condition is
    popped -- an operand still live at the branch is a stack the exit block
    would inherit."""
    fn = Fn("probe", 0, 0, ("i64",))
    fn.begin_block(None, breakable=True)
    fn.i64_const(1)
    fn.i32_const(0)
    with pytest.raises(EmitError, match="branch arity"):
        fn.br_if_break()


# ===========================================================================
# E14: the ABI prologue, per declared type
# ===========================================================================


def object_word(tag: int) -> int:
    """A handle word carrying `tag`. Nothing dereferences it: the object checks
    read the TAG BYTE and never the body."""
    return val.from_body_tag(0, tag)


def either_pair(small_tag: int, object_tag: int) -> list[int]:
    return [val.from_body_tag(3, small_tag), object_word(object_tag)]


#: `(label, Ty, accepted words, rejected words)`. Every row carries at least one
#: wrong-TAG rejection; the rows with a range component carry that too.
PROLOGUE_MATRIX: list[tuple[str, Ty, list[int], list[int]]] = [
    ("Bool", Ty.Bool, [val.FALSE_VAL, val.TRUE_VAL], [val.VOID_VAL, val.pack_u32val(1)]),
    (
        "U32",
        Ty.U32,
        [val.pack_u32val(0), val.pack_u32val(0xFFFF_FFFF)],
        # A wrong tag, and the RANGE case: a nonzero minor field is not a valid
        # `U32Val` encoding, and one compare covers both (review m8).
        [val.pack_i32val(1), val.from_major_minor_tag(1, 7, val.TAG_U32)],
    ),
    (
        "I32",
        Ty.I32,
        [val.pack_i32val(-1), val.pack_i32val(0)],
        [val.pack_u32val(1), val.from_major_minor_tag(1, 7, val.TAG_I32)],
    ),
    (
        "U64",
        Ty.U64,
        either_pair(val.TAG_U64_SMALL, val.TAG_U64_OBJECT),
        [val.from_body_tag(3, val.TAG_I64_SMALL), object_word(val.TAG_I64_OBJECT)],
    ),
    (
        "I64",
        Ty.I64,
        either_pair(val.TAG_I64_SMALL, val.TAG_I64_OBJECT),
        [val.from_body_tag(3, val.TAG_U64_SMALL), object_word(val.TAG_U64_OBJECT)],
    ),
    (
        "Timepoint",
        Ty.Timepoint,
        either_pair(val.TAG_TIMEPOINT_SMALL, val.TAG_TIMEPOINT_OBJECT),
        [val.from_body_tag(3, val.TAG_DURATION_SMALL), object_word(val.TAG_DURATION_OBJECT)],
    ),
    (
        "Duration",
        Ty.Duration,
        either_pair(val.TAG_DURATION_SMALL, val.TAG_DURATION_OBJECT),
        [val.from_body_tag(3, val.TAG_TIMEPOINT_SMALL), object_word(val.TAG_TIMEPOINT_OBJECT)],
    ),
    (
        "U128",
        Ty.U128,
        either_pair(val.TAG_U128_SMALL, val.TAG_U128_OBJECT),
        [val.from_body_tag(3, val.TAG_I128_SMALL), object_word(val.TAG_I128_OBJECT)],
    ),
    (
        "I128",
        Ty.I128,
        either_pair(val.TAG_I128_SMALL, val.TAG_I128_OBJECT),
        [val.from_body_tag(3, val.TAG_U128_SMALL), object_word(val.TAG_U128_OBJECT)],
    ),
    (
        "Symbol",
        Ty.Symbol,
        [val.symbol_small("hello"), object_word(val.TAG_SYMBOL_OBJECT)],
        [object_word(val.TAG_STRING_OBJECT), val.pack_u32val(1)],
    ),
    (
        "String",
        Ty.String,
        [object_word(val.TAG_STRING_OBJECT)],
        [object_word(val.TAG_SYMBOL_OBJECT), val.symbol_small("hi")],
    ),
    (
        "Bytes",
        Ty.Bytes,
        [object_word(val.TAG_BYTES_OBJECT)],
        [object_word(val.TAG_STRING_OBJECT)],
    ),
    (
        "Address",
        Ty.Address,
        [object_word(val.TAG_ADDRESS_OBJECT)],
        # A MUXED address is a different tag and a different type (78 vs 77).
        [object_word(val.TAG_MUXED_ADDRESS_OBJECT), object_word(val.TAG_MAP_OBJECT)],
    ),
    ("Vec", Ty.Vec(Ty.U32), [object_word(val.TAG_VEC_OBJECT)], [object_word(val.TAG_MAP_OBJECT)]),
    (
        "Map",
        Ty.Map(Ty.Symbol, Ty.U32),
        [object_word(val.TAG_MAP_OBJECT)],
        [object_word(val.TAG_VEC_OBJECT)],
    ),
    (
        # S9: a struct IS a `Map<Symbol, V>` on chain, so its tag is the map's.
        "Struct",
        Ty.Struct("Settings"),
        [object_word(val.TAG_MAP_OBJECT)],
        [object_word(val.TAG_VEC_OBJECT)],
    ),
    (
        "Option[U32]",
        Ty.Option(Ty.U32),
        [val.VOID_VAL, val.pack_u32val(3)],
        [val.pack_i32val(3), val.from_major_minor_tag(1, 7, val.TAG_U32)],
    ),
]

_IDS = [row[0] for row in PROLOGUE_MATRIX]


def echoing(ty: Ty) -> FuncIR:
    """`def checked(a: ty) -> U32: return 1` -- the prologue runs, and the body
    cannot mask a missing check by accidentally failing on its own."""
    return func(
        "checked", [Return(loc=LOC, value=const(Ty.U32, 1))], params=[("a", ty)], ret=Ty.U32
    )


@pytest.mark.parametrize(("label", "ty", "accepted", "rejected"), PROLOGUE_MATRIX, ids=_IDS)
def test_the_prologue_accepts_well_formed_arguments(
    label: str, ty: Ty, accepted: list[int], rejected: list[int]
) -> None:
    for word in accepted:
        assert run([echoing(ty)], word) == val.pack_u32val(1), f"{label} rejected {word:#018x}"


@pytest.mark.parametrize(("label", "ty", "accepted", "rejected"), PROLOGUE_MATRIX, ids=_IDS)
def test_the_prologue_rejects_with_CODE_ABI_CHECK_FAILED_exactly(
    label: str, ty: Ty, accepted: list[int], rejected: list[int]
) -> None:
    """C19: ONE code for every argument position and every type. Which argument
    failed is a message concern, not a code concern."""
    for word in rejected:
        store, host_ = start([echoing(ty)])
        with pytest.raises(engine.HostError) as info:
            host_.invoke("checked", word)
        assert info.value.val == ABI_FAILED, f"{label} accepted {word:#018x}"
        assert store.errors == [ABI_FAILED]


def bytes_object(store: ObjectStore, payload: bytes) -> int:
    """Seed a real `Bytes` object; `BytesN`'s check calls `bytes_len` on it."""
    store.objects.append(Bytes(payload))
    return val.from_body_tag(len(store.objects) - 1, val.TAG_BYTES_OBJECT)


def bytes_n_host(n: int) -> tuple[ObjectStore, engine.MiniHost]:
    store = ObjectStore()
    wasm, _fns, _ctx = module_of([echoing(Ty.BytesN(n))])
    host_ = engine.MiniHost(wasm, imports=store.bindings())
    store.attach(host_)
    return store, host_


def test_bytes_n_accepts_exactly_its_declared_length() -> None:
    store, host_ = bytes_n_host(32)
    assert host_.invoke("checked", bytes_object(store, b"\xab" * 32)) == val.pack_u32val(1)
    assert store.count("bytes_len") == 1


def test_bytes_n_rejects_a_31_byte_payload_with_CODE_ABI_CHECK_FAILED() -> None:
    """E14's range half at the one type whose range needs a HOST call: the tag
    says `Bytes`, and only `bytes_len` can tell 31 from 32."""
    store, host_ = bytes_n_host(32)
    with pytest.raises(engine.HostError) as info:
        host_.invoke("checked", bytes_object(store, b"\xab" * 31))
    assert info.value.val == ABI_FAILED


def test_bytes_n_rejects_a_wrong_tag_before_it_ever_calls_bytes_len() -> None:
    """Order matters: `bytes_len` on a non-`Bytes` `Val` is the HOST's error,
    which a client cannot tell from a real one. The tag check comes first."""
    store, host_ = bytes_n_host(32)
    with pytest.raises(engine.HostError) as info:
        host_.invoke("checked", object_word(val.TAG_STRING_OBJECT))
    assert info.value.val == ABI_FAILED
    assert store.count("bytes_len") == 0


def test_bytes_n_uses_the_one_tag_check_PART() -> None:
    """Review M9: only `BytesN` clears S25's break-even, because only its check
    contains a host call. Everything else stays INLINE."""
    fns, ctx, _memory = compile_all([echoing(Ty.BytesN(4))])
    assert "tagcheck_bytes_n" in ctx.parts_linked
    assert CallDefined(ctx.ensure_part("tagcheck_bytes_n")) in fns[0].finish()
    assert "bytes_len" in ctx.import_order


def test_no_other_type_gets_a_tagcheck_part() -> None:
    for _label, ty, _accepted, _rejected in PROLOGUE_MATRIX:
        _fns, ctx, _memory = compile_all([echoing(ty)])
        assert ctx.parts_linked == frozenset(), f"{ty.render()} linked {ctx.parts_linked}"


def test_an_internal_helper_gets_NO_prologue() -> None:
    """E14 is about the ABI boundary. An INTERNAL helper is called only by code
    this compiler emitted, whose arguments it already typed."""
    internal = func(
        "helper",
        [Return(loc=LOC, value=const(Ty.U32, 1))],
        kind=FuncKind.INTERNAL,
        params=[("a", Ty.U32)],
    )
    assert len(body_of(echoing(Ty.U32))) > len(body_of(internal))
    assert CallImport("fail_with_error") not in body_of(internal)


def test_a_constructor_is_prologued_like_an_export() -> None:
    node = func(
        "__constructor",
        [Return(loc=LOC, value=None)],
        kind=FuncKind.CONSTRUCTOR,
        params=[("a", Ty.U32)],
        ret=Ty.Void,
    )
    store, host_ = start([node])
    assert host_.invoke("__constructor", val.pack_u32val(1)) == val.VOID_VAL
    assert store.errors == []
    _bad_store, bad_host = start([node])
    with pytest.raises(engine.HostError) as info:
        bad_host.invoke("__constructor", val.pack_i32val(1))
    assert info.value.val == ABI_FAILED


def test_a_constructor_must_return_void() -> None:
    """S26: `__constructor` returns void, and the host launders its errors --
    a value-returning one is a compiler bug the frontend already excludes."""
    node = func(
        "__constructor", [Return(loc=LOC, value=const(Ty.U32, 1))], kind=FuncKind.CONSTRUCTOR
    )
    with pytest.raises(EmitError, match="constructor"):
        lower.compile_function(node, ctx_only())


def test_an_export_has_i64_results_whatever_it_returns() -> None:
    """S23: multi-value is off and every Soroban `Val` is an i64, so the
    exported shape is `(i64...) -> i64` for a `-> None` method too."""
    for kind in (FuncKind.EXPORT, FuncKind.CONSTRUCTOR):
        node = func("f", [Return(loc=LOC, value=None)], kind=kind, ret=Ty.Void)
        fns, _ctx, _memory = compile_all([node])
        assert fns[0].results == ("i64",)


def test_a_value_returning_internal_helper_has_i64_results() -> None:
    node = func(
        "helper", [Return(loc=LOC, value=const(Ty.U32, 1))], kind=FuncKind.INTERNAL, ret=Ty.U32
    )
    fns, _ctx, _memory = compile_all([node])
    assert fns[0].results == ("i64",)


def test_a_prologue_check_is_emitted_for_EVERY_parameter() -> None:
    """The mutation this catches: a prologue that checks only the first
    argument. Both positions are falsified independently."""
    node = func(
        "two",
        [Return(loc=LOC, value=const(Ty.U32, 1))],
        params=[("a", Ty.U32), ("b", Ty.Bool)],
    )
    assert run([node], val.pack_u32val(1), val.TRUE_VAL) == val.pack_u32val(1)
    for args in ((val.pack_i32val(1), val.TRUE_VAL), (val.pack_u32val(1), val.VOID_VAL)):
        _store, host_ = start([node])
        with pytest.raises(engine.HostError) as info:
            host_.invoke("two", *args)
        assert info.value.val == ABI_FAILED


# ===========================================================================
# S3's second sentence: the narrowing check on a host-call return
# ===========================================================================


def reading(ty: Ty = Ty.U32) -> FuncIR:
    """`return storage.get(Symbol("k"), ty)` -- the E13-guarded bare get."""
    return func(
        "read",
        [
            Return(
                loc=LOC,
                value=host(
                    "get_contract_data",
                    ty,
                    const(Ty.Symbol, "k"),
                    storage_type(STORAGE_INSTANCE),
                ),
            )
        ],
        ret=ty,
    )


def test_a_storage_read_of_the_claimed_type_passes_the_narrowing_check() -> None:
    store = ObjectStore()
    store.storage[(STORAGE_INSTANCE, "k")] = val.pack_u32val(41)
    assert run([reading()], store=store) == val.pack_u32val(41)


def test_a_storage_read_of_a_WRONG_tagged_Val_fails_with_CODE_ABI_CHECK_FAILED() -> None:
    """S3's second sentence, and the whole reason it exists: the ledger holds
    an `I32Val` where the contract's type says `U32`. Unchecked, that value
    flows into arithmetic that reads its major field as an unsigned number --
    a wrong answer with no error anywhere."""
    store = ObjectStore()
    store.storage[(STORAGE_INSTANCE, "k")] = val.pack_i32val(41)
    _store, host_ = start([reading()], store)
    with pytest.raises(engine.HostError) as info:
        host_.invoke("read")
    assert info.value.val == ABI_FAILED
    assert store.errors == [ABI_FAILED]


def test_an_object_typed_storage_read_is_narrowed_too() -> None:
    store = ObjectStore()
    store.storage[(STORAGE_INSTANCE, "k")] = object_word(val.TAG_VEC_OBJECT)
    _store, host_ = start([reading(Ty.Map(Ty.Symbol, Ty.U32))], store)
    with pytest.raises(engine.HostError) as info:
        host_.invoke("read")
    assert info.value.val == ABI_FAILED


def with_default() -> FuncIR:
    """`storage.get(k, U32, default=0)` -- E13's exempt shape, whose `then` arm
    is still an ANY-typed host result and so is still narrowed."""
    key = const(Ty.Symbol, "k")
    imm = storage_type(STORAGE_INSTANCE)
    return func(
        "read",
        [
            Return(
                loc=LOC,
                value=IfExp(
                    loc=LOC,
                    ty=Ty.U32,
                    cond=host("has_contract_data", Ty.Bool, key, imm),
                    then=host("get_contract_data", Ty.U32, key, imm),
                    orelse=const(Ty.U32, 0),
                ),
            )
        ],
    )


def test_the_with_default_arm_is_narrowed_as_well() -> None:
    """Both storage-get arms produce an ANY-typed `Val`, so both are checked --
    the E13 exemption is about the presence GUARD, not about the type claim."""
    store = ObjectStore()
    store.storage[(STORAGE_INSTANCE, "k")] = val.pack_i32val(1)
    _store, host_ = start([with_default()], store)
    with pytest.raises(engine.HostError) as info:
        host_.invoke("read")
    assert info.value.val == ABI_FAILED
    assert store.call_names() == ["has_contract_data", "get_contract_data", "fail_with_error"]


def test_a_map_read_is_narrowed_to_the_claimed_value_type() -> None:
    """`FieldGet` is `map_get` (S9), whose result is whatever was stored."""
    store = ObjectStore()
    inner = host(
        "get_contract_data", Ty.Struct("S"), const(Ty.Symbol, "k"), storage_type(STORAGE_INSTANCE)
    )
    node = func(
        "field",
        [Return(loc=LOC, value=host("map_get", Ty.U32, inner, const(Ty.Symbol, "f")))],
    )
    handle = val.from_body_tag(len(store.objects), val.TAG_MAP_OBJECT)
    store.objects.append({"f": val.pack_i32val(3)})
    store.storage[(STORAGE_INSTANCE, "k")] = handle
    _store, host_ = start([node], store)
    with pytest.raises(engine.HostError) as info:
        host_.invoke("field")
    assert info.value.val == ABI_FAILED


def test_a_Void_host_result_is_NOT_narrowed() -> None:
    """The one exemption, and the reason for it: a Void `Val` is dropped by the
    statement that produced it (P14), so a wrong tag there has nowhere to flow
    -- and a check on it would be pure size on every storage write."""
    node = func("store", [put("k", 4), Return(loc=LOC, value=const(Ty.U32, 1))])
    assert CallImport("fail_with_error") not in body_of(node)


def test_narrow_to_leaves_the_value_it_checked_on_the_stack() -> None:
    """The structural contract every call site depends on: net +1 i64, so
    `lower_expr`'s own `expr_scope` still balances (review M1)."""
    fn = Fn("probe", 0, 0, ("i64",))
    fn.i64_const(val.pack_u32val(1))
    before = list(fn.stack)
    lower.narrow_to(fn, ctx_only(), Ty.U32)
    assert fn.stack == before


# ===========================================================================
# The shared check bodies (E14 + S3 are ONE implementation, review M9)
# ===========================================================================


def _check_only(ty: Ty, *, narrow: bool) -> list[object]:
    """The instructions of `ty`'s check, from whichever of the two positions."""
    ctx = ctx_only()
    fn = Fn("probe", 1, 0, ("i64",))
    fn.local_get(0)
    if narrow:
        lower.narrow_to(fn, ctx, ty)
    else:
        lower.abi_check(fn, ctx, 0, ty)
    fn.ret()
    return [op for op, _imm in instructions(fn.finish())]


@pytest.mark.parametrize("ty", [Ty.Bool, Ty.U32, Ty.U64, Ty.Symbol, Ty.Option(Ty.U32)])
def test_the_prologue_and_the_narrowing_hook_emit_the_SAME_check(ty: Ty) -> None:
    """Review M9 killed the `narrow_*` parts precisely so there would be ONE
    check body per type. If the two positions ever diverge, one of them is the
    weaker check and nothing says which."""
    narrowed = _check_only(ty, narrow=True)
    prologue = _check_only(ty, narrow=False)
    # `narrow_to` adds the `local.tee` that stashes the stack value; strip it,
    # and the two must be instruction-for-instruction identical.
    assert opcodes.LOCAL_TEE in narrowed
    assert [op for op in narrowed if op != opcodes.LOCAL_TEE] == prologue


def test_a_type_with_no_check_body_is_refused_loudly() -> None:
    """F.1.15's discipline at the check table: a type nobody wrote a check for
    must not silently cross the ABI boundary unchecked."""
    fn = Fn("probe", 1, 0, ("i64",))
    with pytest.raises(EmitError, match="no ABI check"):
        lower.abi_check(fn, ctx_only(), 0, Ty.ErrorEnum("E"))


def _fail_words(items: Sequence[CodeItem]) -> list[int]:
    """Every constant fed to a `fail_with_error` in a finished body."""
    decoded = list(instructions(items))
    out: list[int] = []
    for i, (op, _imm) in enumerate(decoded):
        if op == CallImport("fail_with_error") and decoded[i - 1][0] == opcodes.I64_CONST:
            imm = decoded[i - 1][1]
            assert imm is not None
            out.append(val.as_u64(imm))
    return out


def test_every_prologue_failure_uses_the_one_code_and_only_it() -> None:
    """C19, structurally. The two reserved codes do different jobs; swapping
    them would make a bad argument read as a compiler bug and vice versa."""
    for _label, ty, _accepted, _rejected in PROLOGUE_MATRIX:
        words = _fail_words(body_of(echoing(ty)))
        assert words.count(ABI_FAILED) >= 1, f"{ty.render()} raises no ABI code"
        assert words.count(UNREACHABLE_GUARD) == 0, f"{ty.render()} raises the guard code"


# ===========================================================================
# Assembly: the whole function, end to end
# ===========================================================================


def test_a_module_of_several_functions_compiles_against_one_context() -> None:
    """Imports and parts are shared: `n_module_functions` is what keeps a
    part's `defidx` after every one of the module's own functions."""
    caller = func(
        "call_it",
        [
            Return(
                loc=LOC,
                value=InternalCall(loc=LOC, ty=Ty.U32, fn_name="twice", args=(const(Ty.U32, 21),)),
            )
        ],
    )
    callee = func(
        "twice",
        [Return(loc=LOC, value=add(param(0, Ty.U32), param(0, Ty.U32)))],
        kind=FuncKind.INTERNAL,
        params=[("a", Ty.U32)],
    )
    assert run([caller, callee]) == val.pack_u32val(42)


def test_the_declared_local_count_covers_every_slot_the_body_names() -> None:
    fns, _ctx, _memory = compile_all([summing_with_break()])
    assert fns[0].nlocals_declared == 2
    assert fns[0].nlocals >= 2


def test_a_gap_in_the_declared_slot_numbers_is_refused() -> None:
    """`SlotTable` numbers slots in first-binding order, so they are contiguous
    by construction. A gap means the two disagree, and `local.set` would write
    a hidden temp instead of the local the body meant."""
    node = func(
        "gappy",
        [Return(loc=LOC, value=const(Ty.U32, 1))],
        locals_=[(0, "a", Ty.U32), (2, "b", Ty.U32)],
    )
    with pytest.raises(EmitError, match="slot"):
        lower.compile_function(node, ctx_only())


def test_compile_function_names_the_export_not_the_python_name() -> None:
    node = FuncIR(
        loc=LOC,
        py_name="__init__",
        export_name="__constructor",
        kind=FuncKind.CONSTRUCTOR,
        params=(),
        ret=Ty.Void,
        doc="",
        locals=(),
        body=(Return(loc=LOC, value=None),),
        returns_on_every_path=True,
    )
    assert lower.compile_function(node, ctx_only()).name == "__constructor"


def test_the_whole_battery_validates_as_wasm() -> None:
    """Every shape in this file, in one module, instantiated by wasmtime --
    which is the only thing that answers "is this valid wasm?"."""
    store, host_ = start(every_shape())
    assert host_.invoke("plain") == val.pack_u32val(1)
    assert store.call_names() == []


def test_an_unknown_statement_kind_is_refused_loudly() -> None:
    """F.1.15 again, one level up: the statement dispatch is exhaustive over
    `serpent.compiler.ir` by design."""

    class Weird(IRStmt):
        pass

    fn = Fn("probe", 0, 0, ("i64",))
    with pytest.raises(EmitError, match="no lowering for IR statement"):
        lower.lower_stmt(fn, ctx_only(), Weird(loc=LOC))


def test_the_part_inventory_gained_exactly_one_name() -> None:
    """Ruling E3's inventory, extended by Task 9 and by nothing else -- review
    M9 killed `tagcheck_struct`/`_vec`/`_map` and the whole `narrow_*` family."""
    assert "tagcheck_bytes_n" in arith.PART_BUILDERS
    assert "tagcheck_bytes_n" not in arith.PARTS_NEEDING_MEMORY
    assert not [n for n in arith.PART_BUILDERS if n.startswith("narrow_")]
    assert [n for n in arith.PART_BUILDERS if n.startswith("tagcheck_")] == ["tagcheck_bytes_n"]


def test_the_tagcheck_part_returns_the_value_it_checked() -> None:
    """Its `-> Val` signature, exercised directly rather than trusted."""
    ctx = LowerCtx(n_module_functions=0, memory=Memory())
    ctx.ensure_part("tagcheck_bytes_n")
    specs: list[testmod.FunctionSpec] = [
        (p.name, p.nparams, p.nlocals, p.results, p.body) for p in ctx.parts
    ]
    wasm = testmod.build_test_module(specs, imports=ctx.import_order)
    store = ObjectStore()
    host_ = engine.MiniHost(wasm, imports=store.bindings())
    store.attach(host_)
    handle = bytes_object(store, b"abcd")
    assert host_.invoke("tagcheck_bytes_n", handle, 4) == handle
