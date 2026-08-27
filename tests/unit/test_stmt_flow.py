"""Tests for `serpent.compiler.stmt` (Task 6): statements + flow analysis.

Two halves, matching the task's two deliverables:

* **Statements** -- dossier SS B.1 row by row. A SUPPORT row asserts the exact
  IR it lowers to (including the two desugarings C owns outright: `for x in
  vec` and `for i in range(...)` become `While` + a hidden induction local, so
  sub-plan D never sees a `For`); a REJECT row asserts the registry code, a
  message substring, and a non-empty `help`.
* **Flow** -- SS C.3's four `Locals` rules, of which Task 6 owns the two
  flow-sensitive ones: definite RETURN on every path (rule 3, P6/S17 -- wasm
  validation provably cannot catch a missing return) and definite ASSIGNMENT
  (rule 2). Plus unreachable code, and MJ-12's explicit termination rule:
  "`while True:` with no `break` satisfies definite-return when every exit
  path returns/raises".
"""

from __future__ import annotations

import ast
import textwrap
import time
from pathlib import Path

import pytest

from serpent.compiler import codes
from serpent.compiler.ctx import AliasTable, FuncCtx, SlotTable
from serpent.compiler.diagnostics import Diagnostic, Diagnostics, Loc
from serpent.compiler.ir import (
    Binary,
    BinaryOp,
    Break,
    Compare,
    CompareOp,
    Const,
    Continue,
    Eval,
    HostCall,
    If,
    IRStmt,
    IsZero,
    LetLocal,
    LocalRef,
    Nop,
    ParamRef,
    Raise,
    Return,
    SetLocal,
    Unary,
    UnaryOp,
    While,
    walk,
)
from serpent.compiler.loader import LoadedModule, load_module
from serpent.compiler.stmt import STMT_KIND_CODES, check_body, check_function_body
from serpent.compiler.types_ import Ty

PATH = "contract.py"

_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

_SOURCE = '''
"""A fixture contract for statement checking."""

from serpent import (
    Address,
    Bool,
    Bytes,
    Env,
    Map,
    Symbol,
    U32,
    U64,
    Vec,
    bytes_n,
    contract,
    contracterror,
    contracttype,
    errorcode,
)

ADMIN = Symbol("ADMIN")


@contracttype
class Balance:
    amount: U32
    owner: Address


@contracterror
class Err:
    NotFound = errorcode(1)
    NotAuthorized = errorcode(2)


def helper(x: U32) -> U32:
    return x


@contract
class C:
    def go(
        self,
        env: Env,
        a: U32,
        b: U32,
        c: U64,
        s: Symbol,
        by: Bytes,
        ad: Address,
        v: Vec[U32],
        m: Map[Symbol, U32],
        bal: Balance,
        flag: Bool,
        b12: bytes_n(12),
        ou32: U32 | None,
    ) -> U32:
        return a
'''

_PARAMS: list[tuple[str, Ty]] = [
    ("a", Ty.U32),
    ("b", Ty.U32),
    ("c", Ty.U64),
    ("s", Ty.Symbol),
    ("by", Ty.Bytes),
    ("ad", Ty.Address),
    ("v", Ty.Vec(Ty.U32)),
    ("m", Ty.Map(Ty.Symbol, Ty.U32)),
    ("bal", Ty.Struct("Balance")),
    ("flag", Ty.Bool),
    ("b12", Ty.BytesN(12)),
    ("ou32", Ty.Option(Ty.U32)),
]


def _loaded() -> LoadedModule:
    loaded = load_module(_SOURCE, PATH)
    assert not loaded.diagnostics, loaded.diagnostics.diagnostics
    return loaded


_LOADED = _loaded()


def _ctx(return_ty: Ty = Ty.U32) -> FuncCtx:
    loc = Loc.whole_file(PATH)
    return FuncCtx(
        loaded=_LOADED,
        sink=Diagnostics(),
        params=[(name, ty, loc) for name, ty in _PARAMS],
        locals=SlotTable(reserved={name: "a parameter" for name, _ in _PARAMS}),
        loop_depth=0,
        return_ty=return_ty,
        alias_sets=AliasTable(),
        fn_name="go",
        path=PATH,
    )


def _parse(source: str) -> list[ast.stmt]:
    return ast.parse(textwrap.dedent(source).strip() + "\n").body


def _check(source: str, *, return_ty: Ty = Ty.U32) -> tuple[list[IRStmt], FuncCtx]:
    ctx = _ctx(return_ty)
    stmts = check_body(_parse(source), ctx)
    return stmts, ctx


def _ok(source: str, *, return_ty: Ty = Ty.U32) -> list[IRStmt]:
    stmts, ctx = _check(source, return_ty=return_ty)
    assert not ctx.sink, [(d.code, d.message) for d in ctx.sink.diagnostics]
    return stmts


def _reject(source: str, *, return_ty: Ty = Ty.U32) -> Diagnostic:
    _, ctx = _check(source, return_ty=return_ty)
    assert len(ctx.sink) == 1, [(d.code, d.message) for d in ctx.sink.diagnostics]
    return ctx.sink.diagnostics[0]


def _assert_reject(diag: Diagnostic, code: str, substring: str = "") -> None:
    assert diag.code == code, f"expected {code}, got {diag.code}: {diag.message}"
    assert _INTENT[code] in diag.message, (
        f"{code}: message does not carry its registry intent\n  message: {diag.message}\n"
        f"  intent:  {_INTENT[code]}"
    )
    if substring:
        assert substring in diag.message or any(substring in n for n in diag.notes), (
            f"{code}: {substring!r} not in {diag.message} {diag.notes}"
        )
    assert diag.help, f"{code}: every reject this task raises carries a help rewrite"
    assert diag.loc.path == PATH


def _function(source: str, *, return_ty: Ty = Ty.U32) -> tuple[bool, FuncCtx]:
    ctx = _ctx(return_ty)
    checked = check_function_body(_parse(source), ctx, loc=Loc.whole_file(PATH))
    return checked.returns_on_every_path, ctx


# --- Assign -------------------------------------------------------------------


def test_first_binding_is_a_let_local() -> None:
    stmts = _ok("total = U32(0)")
    assert len(stmts) == 1
    let = stmts[0]
    assert isinstance(let, LetLocal)
    assert let.slot == 0
    assert let.ty == Ty.U32
    assert isinstance(let.init, Const)
    assert let.init.py_value == 0


def test_later_assignment_is_a_set_local() -> None:
    stmts = _ok("total = U32(0)\ntotal = U32(1)")
    assert isinstance(stmts[0], LetLocal)
    second = stmts[1]
    assert isinstance(second, SetLocal)
    assert second.slot == 0


def test_a_literal_takes_the_locals_established_type() -> None:
    stmts = _ok("total = U32(0)\ntotal = 5")
    second = stmts[1]
    assert isinstance(second, SetLocal)
    assert isinstance(second.value, Const)
    assert second.value.ty == Ty.U32
    assert second.value.py_value == 5


def test_rebinding_a_local_at_another_type_is_rejected() -> None:
    """SS C.3 rule 1: the first binding fixes the type."""
    _assert_reject(_reject("total = U32(0)\ntotal = c"), "SPT3017", "U64")


def test_binding_a_name_that_shadows_a_param_is_rejected() -> None:
    """SS C.3 rule 4."""
    _assert_reject(_reject("a = U32(0)"), "SPT2004", "parameter")


def test_bare_literal_assignment_names_the_wrap() -> None:
    """MJ-12: `x = True` with no chain type in scope is a reject."""
    diag = _reject("x = True")
    _assert_reject(diag, "SPT3008")
    assert "Bool(True)" in (diag.help or "")


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("x, y = a, b", "SPT1029"),
        ("[x, y] = a, b", "SPT1029"),
        ("bal.amount = a", "SPT4016"),
        ("v[0] = a", "SPT1030"),
    ],
)
def test_unsupported_assignment_targets(source: str, code: str) -> None:
    _assert_reject(_reject(source), code)


# --- AnnAssign ----------------------------------------------------------------


def test_annotated_local_with_a_value() -> None:
    stmts = _ok("x: U32 = U32(5)")
    let = stmts[0]
    assert isinstance(let, LetLocal)
    assert let.ty == Ty.U32


def test_annotated_local_coerces_a_literal() -> None:
    stmts = _ok("x: U64 = 5")
    let = stmts[0]
    assert isinstance(let, LetLocal)
    assert let.ty == Ty.U64
    assert isinstance(let.init, Const)
    assert let.init.ty == Ty.U64


def test_annotated_local_resolves_a_generic_annotation() -> None:
    stmts = _ok("x: Vec[U32] = v")
    let = stmts[0]
    assert isinstance(let, LetLocal)
    assert let.ty == Ty.Vec(Ty.U32)


def test_annotated_local_disagreeing_with_its_annotation_is_rejected() -> None:
    _assert_reject(_reject("x: U32 = c"), "SPT3018", "U64")


def test_annotated_local_without_a_value_is_rejected() -> None:
    _assert_reject(_reject("x: U32"), "SPT1036")


def test_unresolvable_annotation_is_rejected() -> None:
    _assert_reject(_reject("x: Bogus = a"), "SPT2003", "Bogus")


def test_unmappable_annotation_is_rejected() -> None:
    _assert_reject(_reject("x: int = a"), "SPT3013")


def test_annotation_shape_allowlist_blocks_arbitrary_code(tmp_path: Path) -> None:
    """Fix round 1, Important 2: a local annotation must not execute code.

    PEP 526 says annotations on a local are NEVER evaluated at runtime, so
    anything the compiler evaluates here is code that importing the module does
    not run. The AST allowlist is checked BEFORE anything is compiled, so the
    write never happens -- asserted on the filesystem, not just on the
    diagnostic.
    """
    victim = tmp_path / "written.txt"
    source = f"x: __import__('pathlib').Path({str(victim)!r}).write_text('pwned') = U32(1)"
    diag = _reject(source)
    _assert_reject(diag, "SPT2003", "not evaluated")
    assert not victim.exists(), "the annotation was evaluated despite the allowlist"


@pytest.mark.parametrize(
    "annotation",
    [
        "[U32]",
        "(lambda: U32)()",
        "U32 if flag else U64",
        "{'a': U32}",
        "-U32",
        "U32 + U64",
    ],
)
def test_annotation_forms_outside_the_allowlist_are_rejected(annotation: str) -> None:
    _assert_reject(_reject(f"x: {annotation} = a"), "SPT2003", "not evaluated")


@pytest.mark.parametrize(
    ("source", "ty"),
    [
        ("x: U64 = c", Ty.U64),
        ("x: Vec[U32] = v", Ty.Vec(Ty.U32)),
        ("x: Map[Symbol, U32] = m", Ty.Map(Ty.Symbol, Ty.U32)),
        # The one form that must still EXECUTE a call (A16/E20).
        ("x: bytes_n(12) = b12", Ty.BytesN(12)),
        ("x: U32 | None = ou32", Ty.Option(Ty.U32)),
        ("x: Balance = bal", Ty.Struct("Balance")),
    ],
)
def test_annotation_forms_inside_the_allowlist_still_resolve(source: str, ty: Ty) -> None:
    stmts = _ok(source)
    let = stmts[0]
    assert isinstance(let, LetLocal)
    assert let.ty == ty


def test_annotation_cannot_reach_dangerous_builtins() -> None:
    """The second, independent guard: even a shape the allowlist admits (a call
    of a plain NAME) cannot reach `__import__`, because the evaluation namespace
    carries a restricted `__builtins__`."""
    diag = _reject("x: __import__('pathlib') = a")
    _assert_reject(diag, "SPT2003", "__import__")


def test_annotation_cannot_rebind_a_module_name() -> None:
    """A walrus is outside the allowlist, so the rebind never runs -- and even
    if it did, evaluation uses a COPY of the module namespace."""
    before = _LOADED.namespace["ADMIN"]
    _assert_reject(_reject("x: (ADMIN := U32) = a"), "SPT2003", "not evaluated")
    assert _LOADED.namespace["ADMIN"] is before


# --- AugAssign ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "op"),
    [
        ("total += U32(1)", BinaryOp.ADD),
        ("total -= U32(1)", BinaryOp.SUB),
        ("total *= U32(2)", BinaryOp.MUL),
        ("total //= U32(2)", BinaryOp.FLOORDIV),
        ("total %= U32(2)", BinaryOp.MOD),
        ("total += 1", BinaryOp.ADD),
    ],
)
def test_augmented_assignment_desugars(source: str, op: BinaryOp) -> None:
    """E6: `count += U32(1)` is what everyone writes; it desugars to
    `count = count <op> U32(1)` BEFORE typing, so the operator rules are
    shared with the plain form by construction."""
    stmts = _ok(f"total = U32(0)\n{source}")
    assign = stmts[1]
    assert isinstance(assign, SetLocal)
    assert assign.slot == 0
    value = assign.value
    assert isinstance(value, Binary)
    assert value.op is op
    assert isinstance(value.lhs, LocalRef)
    assert value.lhs.slot == 0
    assert value.ty == Ty.U32


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("total **= U32(2)", "SPT3005"),
        ("total &= U32(2)", "SPT3005"),
        ("total |= U32(2)", "SPT3005"),
        ("total ^= U32(2)", "SPT3005"),
        ("total <<= U32(2)", "SPT3005"),
        ("total >>= U32(2)", "SPT3005"),
        ("total /= U32(2)", "SPT3006"),
        ("total += s", "SPT3003"),
        ("total += True", "SPT3003"),
    ],
)
def test_augmented_assignment_rejects(source: str, code: str) -> None:
    _assert_reject(_reject(f"total = U32(0)\n{source}"), code)


def test_augmented_assignment_to_an_unbound_name_is_rejected() -> None:
    _assert_reject(_reject("nope += U32(1)"), "SPT2001", "nope")


def test_augmented_assignment_to_a_param_is_rejected() -> None:
    """A param is not a slot; binding its name is SS C.3 rule 4's shadowing."""
    _assert_reject(_reject("a += U32(1)"), "SPT2004")


# --- If / While ---------------------------------------------------------------


def test_if_else() -> None:
    stmts = _ok(
        """
        if flag:
            total = U32(0)
        else:
            total = U32(1)
        """
    )
    node = stmts[0]
    assert isinstance(node, If)
    assert isinstance(node.cond, ParamRef)
    assert len(node.body) == 1
    assert len(node.orelse) == 1


def test_elif_is_a_nested_if() -> None:
    stmts = _ok(
        """
        if flag:
            total = U32(0)
        elif a == b:
            total = U32(1)
        else:
            total = U32(2)
        """
    )
    outer = stmts[0]
    assert isinstance(outer, If)
    assert len(outer.orelse) == 1
    inner = outer.orelse[0]
    assert isinstance(inner, If)
    assert isinstance(inner.cond, Compare)


def test_numeric_condition_lowers_to_a_zero_test() -> None:
    """D3/E10, through Task 5's `check_condition`."""
    stmts = _ok("if a:\n    total = U32(0)")
    node = stmts[0]
    assert isinstance(node, If)
    assert isinstance(node.cond, Unary)
    assert node.cond.op is UnaryOp.NOT
    assert isinstance(node.cond.operand, IsZero)


def test_non_numeric_condition_is_rejected() -> None:
    _assert_reject(_reject("if s:\n    total = U32(0)"), "SPT3015")


def test_while() -> None:
    stmts = _ok("while flag:\n    pass")
    node = stmts[0]
    assert isinstance(node, While)
    assert isinstance(node.cond, ParamRef)
    assert node.body == (Nop(loc=node.body[0].loc),)


def test_while_true_is_supported() -> None:
    """MJ-12: the `True` -> `Bool` coercion in condition position is what makes
    `while True:` compile at all."""
    stmts = _ok("while True:\n    break")
    node = stmts[0]
    assert isinstance(node, While)
    assert isinstance(node.cond, Const)
    assert node.cond.ty == Ty.Bool
    assert node.cond.py_value is True
    assert isinstance(node.body[0], Break)


def test_break_and_continue_inside_a_loop() -> None:
    stmts = _ok(
        """
        while flag:
            if a == b:
                continue
            break
        """
    )
    loop = stmts[0]
    assert isinstance(loop, While)
    inner = loop.body[0]
    assert isinstance(inner, If)
    assert isinstance(inner.body[0], Continue)
    assert isinstance(loop.body[1], Break)


@pytest.mark.parametrize("source", ["break", "continue", "if flag:\n    break"])
def test_break_or_continue_outside_a_loop_is_rejected(source: str) -> None:
    _assert_reject(_reject(source), "SPT7003")


# --- for x in vec: the desugaring C owns (E4) --------------------------------


def test_for_in_vec_desugars_to_a_while_loop() -> None:
    """E4/F.1.12: the desugaring happens INSIDE C, so sub-plan D never sees a
    `For` node. The shape is pinned structurally, because the increment's
    POSITION is load-bearing: it must run before the body so that a `continue`
    cannot skip it and spin forever."""
    stmts = _ok("for x in v:\n    total = x")

    assert len(stmts) == 3, [type(s).__name__ for s in stmts]
    bind_iter, bind_index, loop = stmts

    # 1. the iterable is evaluated ONCE into a hidden local
    assert isinstance(bind_iter, LetLocal)
    assert bind_iter.ty == Ty.Vec(Ty.U32)
    assert isinstance(bind_iter.init, ParamRef)
    assert bind_iter.init.name == "v"

    # 2. a hidden U32 induction local, starting at zero
    assert isinstance(bind_index, LetLocal)
    assert bind_index.ty == Ty.U32
    assert isinstance(bind_index.init, Const)
    assert bind_index.init.py_value == 0

    # 3. while $idx < vec_len($iter)
    assert isinstance(loop, While)
    cond = loop.cond
    assert isinstance(cond, Compare)
    assert cond.op is CompareOp.LT
    assert cond.via_obj_cmp is False
    assert isinstance(cond.lhs, LocalRef)
    assert cond.lhs.slot == bind_index.slot
    assert isinstance(cond.rhs, HostCall)
    assert cond.rhs.fn_name == "vec_len"
    assert cond.rhs.ty == Ty.U32
    assert isinstance(cond.rhs.args[0], LocalRef)
    assert cond.rhs.args[0].slot == bind_iter.slot

    # 4. body: x = vec_get($iter, $idx); $idx = $idx + 1; <user body>
    bind_elem, bump, *user_body = loop.body
    assert isinstance(bind_elem, LetLocal)
    assert bind_elem.ty == Ty.U32
    assert isinstance(bind_elem.init, HostCall)
    assert bind_elem.init.fn_name == "vec_get"
    assert bind_elem.init.ty == Ty.U32
    assert [type(arg) for arg in bind_elem.init.args] == [LocalRef, LocalRef]

    assert isinstance(bump, SetLocal)
    assert bump.slot == bind_index.slot
    assert isinstance(bump.value, Binary)
    assert bump.value.op is BinaryOp.ADD
    assert isinstance(bump.value.rhs, Const)
    assert bump.value.rhs.py_value == 1
    assert bump.value.rhs.ty == Ty.U32

    assert len(user_body) == 1
    assert isinstance(user_body[0], LetLocal)


def test_for_in_vec_declares_distinct_hidden_slots_per_loop() -> None:
    ctx = _ctx()
    check_body(_parse("for x in v:\n    pass\nfor y in v:\n    pass"), ctx)
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    names = [slot.name for slot in ctx.locals.slots]
    assert len(set(names)) == len(names), names
    hidden = [name for name in names if not name.isidentifier()]
    assert len(hidden) == 4, names  # two loops x (iterable + index)


def test_pre_bound_loop_variable_is_a_set_local() -> None:
    """Fix round 1, Important 3: `LetLocal` is the FIRST binding of a slot, so
    a loop variable that already exists must be reassigned, not re-declared."""
    stmts = _ok("x = U32(0)\nfor x in v:\n    total = x")
    lets = [s for s in stmts if isinstance(s, LetLocal)]
    assert len(lets) == 3  # x, then the two hidden locals
    loop = stmts[-1]
    assert isinstance(loop, While)
    bind_target = loop.body[0]
    assert isinstance(bind_target, SetLocal)
    assert bind_target.slot == lets[0].slot


def test_two_loops_sharing_a_target_declare_it_once() -> None:
    stmts = _ok("for x in v:\n    pass\nfor x in v:\n    pass")
    first, second = stmts[2], stmts[5]
    assert isinstance(first, While)
    assert isinstance(second, While)
    assert isinstance(first.body[0], LetLocal)
    assert isinstance(second.body[0], SetLocal)
    assert second.body[0].slot == first.body[0].slot


def test_pre_bound_loop_variable_of_another_type_is_rejected() -> None:
    """The same diagnostic an ordinary reassignment at a different type gets."""
    _assert_reject(_reject("x = s\nfor x in v:\n    pass"), "SPT3017")


def test_pre_bound_range_variable_is_a_set_local() -> None:
    stmts = _ok("i = U32(0)\nfor i in range(a):\n    total = i")
    loop = stmts[-1]
    assert isinstance(loop, While)
    assert isinstance(loop.body[0], SetLocal)


def test_for_loop_body_continue_still_advances_the_index() -> None:
    """The regression this shape exists to prevent: an increment at the END of
    the body is skipped by `continue`, and the loop spins until the budget
    runs out."""
    stmts = _ok("for x in v:\n    continue")
    loop = stmts[-1]
    assert isinstance(loop, While)
    kinds = [type(s) for s in loop.body]
    assert kinds.index(SetLocal) < kinds.index(Continue)


# --- for i in range(...) (E5) ------------------------------------------------


def test_for_in_range_stop_desugars() -> None:
    stmts = _ok("for i in range(a):\n    total = i")
    assert len(stmts) == 3
    bind_stop, bind_index, loop = stmts

    assert isinstance(bind_stop, LetLocal)
    assert bind_stop.ty == Ty.U32
    assert isinstance(bind_stop.init, ParamRef)  # evaluated ONCE (python semantics)

    assert isinstance(bind_index, LetLocal)
    assert isinstance(bind_index.init, Const)
    assert bind_index.init.py_value == 0

    assert isinstance(loop, While)
    cond = loop.cond
    assert isinstance(cond, Compare)
    assert cond.op is CompareOp.LT
    assert isinstance(cond.lhs, LocalRef)
    assert isinstance(cond.rhs, LocalRef)
    assert cond.rhs.slot == bind_stop.slot

    bind_i, bump, *user_body = loop.body
    assert isinstance(bind_i, LetLocal)
    assert bind_i.ty == Ty.U32
    assert isinstance(bind_i.init, LocalRef)
    assert bind_i.init.slot == bind_index.slot
    assert isinstance(bump, SetLocal)
    assert len(user_body) == 1


def test_for_in_range_start_stop_desugars() -> None:
    stmts = _ok("for i in range(a, b):\n    total = i")
    bind_stop, bind_index, loop = stmts
    assert isinstance(bind_index, LetLocal)
    assert isinstance(bind_index.init, ParamRef)
    assert bind_index.init.name == "a"
    assert isinstance(bind_stop, LetLocal)
    assert isinstance(bind_stop.init, ParamRef)
    assert bind_stop.init.name == "b"
    assert isinstance(loop, While)


def test_for_in_range_of_literals_defaults_to_u32() -> None:
    stmts = _ok("for i in range(10):\n    total = i")
    bind_stop, _bind_index, loop = stmts
    assert isinstance(bind_stop, LetLocal)
    assert bind_stop.ty == Ty.U32
    assert isinstance(loop, While)
    body_bind = loop.body[0]
    assert isinstance(body_bind, LetLocal)
    assert body_bind.ty == Ty.U32


def test_for_in_range_follows_the_bound_type() -> None:
    stmts = _ok("for i in range(c):\n    total2 = i")
    bind_stop = stmts[0]
    assert isinstance(bind_stop, LetLocal)
    assert bind_stop.ty == Ty.U64


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("for i in range(a, b, b):\n    pass", "SPT1020"),
        ("for i in range():\n    pass", "SPT1020"),
        ("for i in range(a, c):\n    pass", "SPT3003"),
        ("for x in m:\n    pass", "SPT1019"),
        ("for x in by:\n    pass", "SPT1019"),
        ("for x in s:\n    pass", "SPT1019"),
        ("for x, y in v:\n    pass", "SPT1029"),
        ("for x in v:\n    pass\nelse:\n    pass", "SPT1018"),
    ],
)
def test_for_loop_rejects(source: str, code: str) -> None:
    _assert_reject(_reject(source), code)


def test_range_keyword_argument_message_does_not_claim_zero_arguments() -> None:
    """Fix round 1, Minor 5: `len(call.args)` is 0 for a keyword call, so the
    old detail claimed "got 0" about a call that clearly had an argument."""
    diag = _reject("for i in range(stop=a):\n    pass")
    _assert_reject(diag, "SPT1020", "keyword")
    assert "got 0" not in diag.message


# --- Raise / Return -----------------------------------------------------------


def test_raise_error_member() -> None:
    stmts = _ok("raise Err.NotAuthorized")
    node = stmts[0]
    assert isinstance(node, Raise)
    assert node.enum == "Err"
    assert node.case == "NotAuthorized"
    assert node.code == 2


def test_raise_unknown_member_is_rejected() -> None:
    _assert_reject(_reject("raise Err.Bogus"), "SPT2001", "Bogus")


@pytest.mark.parametrize(
    "source",
    [
        "raise",
        "raise Err(1)",
        "raise Err.NotFound from None",
        "raise a",
        "raise Balance",
    ],
)
def test_unsupported_raise_forms(source: str) -> None:
    _assert_reject(_reject(source), "SPT1021")


def test_rejected_raise_does_not_cascade_into_a_missing_return() -> None:
    """Fix round 1, Minor 4: the author's statement DOES end the path; only its
    form is wrong, so the flow analysis must not add SPT7001 on top."""
    ctx = _ctx()
    check_function_body(_parse('raise ValueError("x")'), ctx, loc=Loc.whole_file(PATH))
    assert [d.code for d in ctx.sink.diagnostics] == ["SPT1021"]


def test_rejected_raise_member_does_not_cascade_either() -> None:
    ctx = _ctx()
    check_function_body(_parse("raise Err.Bogus"), ctx, loc=Loc.whole_file(PATH))
    assert [d.code for d in ctx.sink.diagnostics] == ["SPT2001"]


def test_return_a_value() -> None:
    stmts = _ok("return a")
    node = stmts[0]
    assert isinstance(node, Return)
    assert isinstance(node.value, ParamRef)


def test_return_a_literal_coerces_to_the_return_type() -> None:
    stmts = _ok("return 5")
    node = stmts[0]
    assert isinstance(node, Return)
    assert isinstance(node.value, Const)
    assert node.value.ty == Ty.U32


def test_bare_return_in_a_void_method() -> None:
    stmts = _ok("return", return_ty=Ty.Void)
    node = stmts[0]
    assert isinstance(node, Return)
    assert node.value is None


def test_returning_a_value_from_a_void_method_is_rejected() -> None:
    _assert_reject(_reject("return a", return_ty=Ty.Void), "SPT4017")


def test_bare_return_in_a_value_method_is_rejected() -> None:
    _assert_reject(_reject("return"), "SPT7001")


def test_returning_the_wrong_type_is_rejected() -> None:
    _assert_reject(_reject("return c"), "SPT3018", "U64")


def test_returning_an_error_is_rejected() -> None:
    """S8: `Error` is never a returnable value, wherever it is checked."""
    diag = _reject("return Err.NotFound")
    assert diag.code in ("SPT3001", "SPT3002")
    assert diag.help


# --- Expr statements ----------------------------------------------------------


def test_pass_is_a_nop() -> None:
    stmts = _ok("pass")
    assert isinstance(stmts[0], Nop)


def test_function_docstring_is_skipped() -> None:
    stmts = _ok('"""What this method does."""\nreturn a')
    assert len(stmts) == 1
    assert isinstance(stmts[0], Return)


def test_a_string_expression_that_is_not_the_docstring_is_rejected() -> None:
    _assert_reject(_reject('return a\n"""not a docstring"""'), "SPT7004")


def test_discarded_non_void_expression_is_rejected() -> None:
    """SS B.1's own worked example: `count + U32(1)` on a line by itself."""
    _assert_reject(_reject("a + b"), "SPT1028")


def test_void_expression_statement_becomes_an_eval() -> None:
    """No Task-5 surface produces a Void expression yet (storage set/publish
    are Task 7a), so this pins the shape through a synthetic Void HostCall --
    the node `Eval` will carry once that table lands."""
    ctx = _ctx()
    loc = Loc.whole_file(PATH)
    void_call = HostCall(loc=loc, ty=Ty.Void, fn_name="put_contract_data", args=())
    stmt = Eval(loc=loc, value=void_call)
    assert stmt.value.ty == Ty.Void
    assert not ctx.sink


# --- statement kinds SS B.1 rejects outright ---------------------------------


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("def inner(x: U32) -> U32:\n    return x", "SPT1001"),
        ("async def inner() -> None:\n    pass", "SPT1002"),
        ("try:\n    pass\nexcept Exception:\n    pass", "SPT1022"),
        ("try:\n    pass\nfinally:\n    pass", "SPT1022"),
        ("with a:\n    pass", "SPT1023"),
        ("match a:\n    case _:\n        pass", "SPT1024"),
        ("assert flag", "SPT1025"),
        ("del a", "SPT1026"),
        ("global ADMIN", "SPT1027"),
        ("nonlocal_stmt = 0", "SPT3008"),
    ],
)
def test_unsupported_statement_kinds(source: str, code: str) -> None:
    _assert_reject(_reject(source), code)


def test_nonlocal_is_rejected() -> None:
    # `nonlocal` is only syntactically valid inside a nested function, so it is
    # built directly rather than parsed at module level.
    ctx = _ctx()
    node = ast.Nonlocal(names=["x"], lineno=1, col_offset=0, end_lineno=1, end_col_offset=12)
    check_body([node], ctx)
    assert len(ctx.sink) == 1
    _assert_reject(ctx.sink.diagnostics[0], "SPT1027")


@pytest.mark.parametrize(
    "source", ["import serpent", "from serpent import U32", "class Inner:\n    pass"]
)
def test_statements_with_no_ss_b1_row_get_the_catch_all(source: str) -> None:
    diag = _reject(source)
    assert diag.code == "SPT1037"
    assert diag.help


class _SyntheticStmt(ast.stmt):
    _fields = ()


def test_synthetic_statement_kind_gets_the_catch_all() -> None:
    """MJ-11, statement side: an unconsidered node is a clean diagnostic."""
    ctx = _ctx()
    node = _SyntheticStmt(lineno=1, col_offset=0, end_lineno=1, end_col_offset=1)
    stmts = check_body([node], ctx)
    assert len(ctx.sink) == 1
    assert ctx.sink.diagnostics[0].code == "SPT1037"
    assert stmts == []


def test_dispatch_covers_every_python_statement_kind() -> None:
    handled = {
        ast.Assign,
        ast.AnnAssign,
        ast.AugAssign,
        ast.If,
        ast.While,
        ast.For,
        ast.Break,
        ast.Continue,
        ast.Raise,
        ast.Return,
        ast.Expr,
        ast.Pass,
    }
    missing = [
        kind.__name__
        for kind in ast.stmt.__subclasses__()
        if kind.__module__ in ("ast", "_ast")
        and kind not in handled
        and kind not in STMT_KIND_CODES
    ]
    assert not missing, f"unconsidered ast.stmt kinds: {missing}"


def test_statement_kind_codes_are_registered() -> None:
    for kind, code in STMT_KIND_CODES.items():
        assert code in codes.CODES, f"{kind.__name__} -> unregistered {code}"


# --- flow: definite return (SS C.3 rule 3, P6/S17) ---------------------------


def test_guard_clause_early_return_is_accepted() -> None:
    """E7: `return` anywhere, with C proving definite return -- guard-clause
    style is the natural way to write `transfer`."""
    returns, ctx = _function(
        """
        if a == b:
            return a
        return b
        """
    )
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert returns is True


def test_if_without_else_does_not_return_on_every_path() -> None:
    returns, ctx = _function("if flag:\n    return a")
    assert len(ctx.sink) == 1
    _assert_reject(ctx.sink.diagnostics[0], "SPT7001")
    assert returns is False


def test_both_branches_returning_satisfies_definite_return() -> None:
    returns, ctx = _function(
        """
        if flag:
            return a
        else:
            return b
        """
    )
    assert not ctx.sink
    assert returns is True


def test_raise_counts_as_terminating_a_path() -> None:
    returns, ctx = _function(
        """
        if flag:
            raise Err.NotFound
        return a
        """
    )
    assert not ctx.sink
    assert returns is True


def test_a_void_method_needs_no_return() -> None:
    returns, ctx = _function("total = U32(0)", return_ty=Ty.Void)
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert returns is False


def test_nested_if_missing_one_path_is_rejected() -> None:
    returns, ctx = _function(
        """
        if flag:
            if a == b:
                return a
        else:
            return b
        """
    )
    assert len(ctx.sink) == 1
    assert ctx.sink.diagnostics[0].code == "SPT7001"
    assert returns is False


def test_while_true_with_no_break_satisfies_definite_return() -> None:
    """The plan's explicit termination rule: `while True:` with no `break`
    never exits normally, so every exit path already returns or raises."""
    returns, ctx = _function("while True:\n    return a")
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert returns is True


def test_while_true_divergence_yields_no_return_node_at_all() -> None:
    """Fix round 1, Important 1: the flag is vacuously true here.

    `while True:` with no `break` satisfies definite return by DIVERGING, so
    `returns_on_every_path` is True while the IR contains no `Return` (and no
    `Raise`) anywhere. Sub-plan D must not read the flag as "the body ends in a
    Return": a wasm validator types a `loop` as falling through with its result
    type, so a body that ends after an infinite loop fails validation unless the
    emitter terminates the function with `unreachable`. The invariant is stated
    on `CheckedBody`; this test is what keeps the shape it describes real.
    """
    ctx = _ctx()
    checked = check_function_body(
        _parse("while True:\n    total = U32(1)"), ctx, loc=Loc.whole_file(PATH)
    )
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert checked.returns_on_every_path is True
    nodes = [node for stmt in checked.stmts for node in walk(stmt)]
    assert not [n for n in nodes if isinstance(n, (Return, Raise))], nodes
    assert any(isinstance(n, While) for n in nodes)


def test_while_true_with_a_break_needs_a_return_after_the_loop() -> None:
    returns, ctx = _function(
        """
        while True:
            if flag:
                break
        """
    )
    assert len(ctx.sink) == 1
    assert ctx.sink.diagnostics[0].code == "SPT7001"
    assert returns is False


def test_while_true_with_a_break_and_a_return_after_it_is_accepted() -> None:
    returns, ctx = _function(
        """
        while True:
            if flag:
                break
        return a
        """
    )
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert returns is True


def test_a_break_in_an_inner_loop_does_not_open_the_outer_while_true() -> None:
    returns, ctx = _function(
        """
        while True:
            while flag:
                break
            return a
        """
    )
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert returns is True


def test_an_ordinary_while_does_not_satisfy_definite_return() -> None:
    returns, ctx = _function("while flag:\n    return a")
    assert len(ctx.sink) == 1
    assert ctx.sink.diagnostics[0].code == "SPT7001"
    assert returns is False


def test_definite_return_diagnostic_uses_the_caller_supplied_loc() -> None:
    ctx = _ctx()
    loc = Loc.whole_file(PATH)
    check_function_body(_parse("total = U32(0)"), ctx, loc=loc)
    assert len(ctx.sink) == 1
    assert ctx.sink.diagnostics[0].loc == loc


# --- flow: definite assignment (SS C.3 rule 2) ------------------------------


def test_branch_only_binding_read_afterwards_is_rejected() -> None:
    diag = _reject(
        """
        if flag:
            x = U32(1)
        total = x
        """
    )
    _assert_reject(diag, "SPT7002", "x")


def test_binding_in_both_branches_is_definitely_assigned() -> None:
    _ok(
        """
        if flag:
            x = U32(1)
        else:
            x = U32(2)
        total = x
        """
    )


def test_binding_in_one_branch_when_the_other_returns_is_definitely_assigned() -> None:
    """Dataflow, not syntax: the else arm cannot reach the read."""
    ctx = _ctx()
    check_function_body(
        _parse(
            """
            if flag:
                x = U32(1)
            else:
                return b
            return x
            """
        ),
        ctx,
        loc=Loc.whole_file(PATH),
    )
    assert not ctx.sink, [(d.code, d.message) for d in ctx.sink.diagnostics]


def test_a_loop_bound_local_is_not_assigned_after_the_loop() -> None:
    """The loop body may never run, so `x` is only definitely assigned inside
    it."""
    diag = _reject("for x in v:\n    pass\ntotal = x")
    _assert_reject(diag, "SPT7002", "x")


def test_a_local_assigned_before_a_loop_stays_assigned_inside_it() -> None:
    _ok(
        """
        total = U32(0)
        while flag:
            total = total + U32(1)
        """
    )


def test_reading_a_local_only_bound_later_is_rejected() -> None:
    diag = _reject("total = x\nx = U32(1)")
    _assert_reject(diag, "SPT2001", "x")


def test_the_loop_variable_is_assigned_inside_the_body() -> None:
    _ok("for x in v:\n    total = x")


# --- flow: unreachable code -------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "return a\ntotal = U32(0)",
        "raise Err.NotFound\nreturn a",
        "return a\nreturn b",
    ],
)
def test_unreachable_statement_is_rejected(source: str) -> None:
    _assert_reject(_reject(source), "SPT7004")


def test_unreachable_after_break_inside_a_loop() -> None:
    _assert_reject(
        _reject(
            """
            while flag:
                break
                total = U32(0)
            """
        ),
        "SPT7004",
    )


def test_unreachable_is_reported_once_per_block() -> None:
    _, ctx = _check("return a\ntotal = U32(0)\ntotal = U32(1)\ntotal = U32(2)")
    assert len(ctx.sink) == 1
    assert ctx.sink.diagnostics[0].code == "SPT7004"


# --- robustness -------------------------------------------------------------


def test_deeply_nested_body_checks_quickly() -> None:
    """No accidental exponential walk in the block/flow recursion."""
    source = "total = U32(0)\n"
    for depth in range(24):
        indent = "    " * depth
        source += f"{indent}if flag:\n"
    source += "    " * 24 + "total = U32(1)\n"
    start = time.perf_counter()
    stmts, ctx = _check(source)
    elapsed = time.perf_counter() - start
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert stmts
    assert elapsed < 2.0, f"checking took {elapsed:.2f}s"
