"""Statement checking and flow analysis: one function body -> `[IRStmt]`.

Dossier SS B.1 (statements) and SS C.3 (the scope/flow model) are this module's
specification. It sits on top of Task 5's expression checker and adds the two
things a statement walk can answer that an expression walk cannot:

* **The desugarings C owns.** `for x in vec` and `for i in range(...)` become a
  `While` plus a hidden induction local INSIDE the frontend (E4/E5), so
  sub-plan D never sees a `For` node at all. The increment's POSITION is part
  of the contract: it runs BEFORE the user's body, because an increment at the
  end is skipped by `continue` and the loop then spins until the budget runs
  out. `AugAssign` desugars the same way -- into the plain `BinOp` Task 5
  already checks -- so `x += y` and `x = x + y` cannot drift apart (E6).
* **Flow analysis.** SS C.3's rule 3 (definite return on every path) is proved
  HERE, because `wasm-tools validate` provably cannot catch a missing return
  (P6) and spec SS 4 names it as a bug class that "must be structurally
  impossible" (S17). Rule 2 (definite assignment) is proved here too, and both
  ride along one walk of the body.

## The flow model

`_check_block` returns a `_Block`: the IR it built, whether control FALLS
THROUGH to the next statement (`terminates`), and which local slots are
definitely assigned afterwards. Everything else follows from composing those
three facts:

* `if`/`else` intersects the two arms' assignment sets, unless one arm
  terminates -- then the other arm's set is what reaches the code below.
* a loop body's assignments never escape the loop (it may run zero times), and
  the loop variable is therefore NOT assigned after the loop.
* `while True:` with no `break` terminates: the loop has no normal exit, so
  every exit path already returns or raises. That is the plan's explicit
  termination rule, and it is what makes the `while True:` idiom compile.
* a statement after a terminating one is unreachable (`SPT7004`), reported once
  per block.

Definite ASSIGNMENT is checked by walking the IR each expression produced and
testing every `LocalRef` against the assigned set (`_check_reads`). Doing it on
the IR rather than inside Task 5's resolver keeps the two concerns separate:
`check_expr` answers "what does this name mean", this module answers "has it
been given a value on every path that reaches here".

`LocalSlot.definitely_assigned` (the field `ctx.py` reserves for this analysis)
is kept in sync: `SlotTable.mark_assigned` fires on every binding, and
`check_function_body` does a final pass so the field ends up telling the truth
about the END of the body rather than about whichever branch was walked last.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, replace
from typing import Final

from serpent.compiler import codes, recognize
from serpent.compiler.ctx import FuncCtx, Ownership
from serpent.compiler.diagnostics import Loc
from serpent.compiler.expr import check_condition, check_expr, fold_literal
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
    IRExpr,
    IRStmt,
    LetLocal,
    LocalRef,
    Nop,
    Raise,
    Return,
    SetLocal,
    While,
    walk,
)
from serpent.compiler.types_ import Ty, TyTag, resolve_annotation
from serpent.decorators import _METADATA_ATTR

__all__ = [
    "STMT_KIND_CODES",
    "CheckedBody",
    "check_body",
    "check_function_body",
]

_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

#: MJ-11's catch-all, statement side.
_FALLBACK_CODE = "SPT1037"

#: Why a container a `for` loop iterates cannot be mutated (E11). Recorded as
#: an `AliasTable` reason rather than left to the generic "aliased to another
#: binding" detail, because the author wrote no assignment: the alias is the
#: `for` statement itself, whose desugaring copies the iterable's handle into a
#: hidden induction local.
_ITERATION_ALIAS_REASON = (
    "is the container a `for` loop iterates over, and the loop holds its handle for the "
    "whole iteration"
)

#: Statement kinds SS B.1 rejects outright, keyed by `ast` node class. Every
#: concrete `ast.stmt` subclass is either handled by `_check_stmt`'s dispatch
#: or listed here; anything else (a synthetic node, or a kind a future Python
#: adds) falls to `_FALLBACK_CODE`, so an unconsidered statement is a clean
#: diagnostic and never a traceback (MJ-11).
STMT_KIND_CODES: Final[dict[type[ast.AST], str]] = {
    ast.FunctionDef: "SPT1001",
    ast.AsyncFunctionDef: "SPT1002",
    ast.AsyncFor: "SPT1002",
    ast.AsyncWith: "SPT1002",
    ast.Try: "SPT1022",
    ast.TryStar: "SPT1022",
    ast.With: "SPT1023",
    ast.Match: "SPT1024",
    ast.Assert: "SPT1025",
    ast.Delete: "SPT1026",
    ast.Global: "SPT1027",
    ast.Nonlocal: "SPT1027",
    # No SS B.1 row describes these three inside a METHOD BODY (the module-level
    # forms are the loader's, Task 3): a nested class, and an import that is not
    # at module level. MJ-11's catch-all is their honest home.
    ast.ClassDef: _FALLBACK_CODE,
    ast.Import: _FALLBACK_CODE,
    ast.ImportFrom: _FALLBACK_CODE,
}

# `type X = ...` (PEP 695) exists only on 3.12+, and the project floor is 3.11
# (E5). Registered conditionally so the exhaustive-dispatch property test holds
# on every interpreter CI runs.
_TYPE_ALIAS = getattr(ast, "TypeAlias", None)
if _TYPE_ALIAS is not None:  # pragma: no cover -- 3.12+ only
    STMT_KIND_CODES[_TYPE_ALIAS] = _FALLBACK_CODE

_HELP: Final[dict[str, str]] = {
    "SPT1001": ("define the helper at module level, or as a private method on the contract class"),
    "SPT1002": "write a plain synchronous method; contract calls never await",
    "SPT1018": "drop the else clause and put the code after the loop",
    "SPT1019": (
        "iterate a Vec, or walk the values with a while loop -- map.keys() for a Map, "
        "bytes[i] up to len(b) for Bytes"
    ),
    "SPT1020": "use range(stop) or range(start, stop)",
    "SPT1021": "raise an error case, e.g. `raise Err.NotFound`",
    "SPT1022": (
        "validate before acting: a failing frame is rolled back by the host, and "
        "footprint violations are not recoverable at all"
    ),
    "SPT1023": "there is no context-manager protocol on chain; call what you need directly",
    "SPT1024": "use an if/elif chain",
    "SPT1025": "raise an error case, e.g. `raise Err.NotFound`, so the caller gets a code",
    "SPT1026": "use storage.del_(key), Vec.del_(i), or Map.del_(k)",
    "SPT1027": "contract state lives in storage; module-level names are compile-time constants",
    "SPT1028": "assign the value to a local, or drop the line",
    "SPT1029": "assign one name at a time",
    "SPT1030": "use Vec.put(i, v) or Map.set(k, v)",
    "SPT1036": "give it a value, e.g. `x: U32 = U32(0)`",
    "SPT1037": "rewrite the statement using the serpent subset",
    "SPT2001": "bind the name before reading it",
    "SPT2002": "read and write contract state through env.storage(), not through self",
    "SPT2003": ("annotate with a chain type that is imported or declared in this module"),
    "SPT3003": "convert one side explicitly; serpent never widens or narrows implicitly",
    "SPT3013": ("annotate with a chain type, `X | None`, `Vec[T]`/`Map[K, V]`, or `bytes_n(N)`"),
    "SPT3018": "pass a value of the expected chain type, converting explicitly if needed",
    "SPT4016": "@contracttype values are immutable; build a new one instead",
    "SPT4017": "drop the value, or annotate the method with the type it returns",
    "SPT7001": "return a value on every path, or raise an error case",
    "SPT7002": (
        "assign the local on every path that reaches this read -- give it a value before "
        "the branch, or assign it in both branches"
    ),
    "SPT7003": "put the break/continue inside the loop it belongs to",
    "SPT7004": "delete the unreachable code, or move it before the return/raise",
}


#: Notes reused across diagnostics; module constants because each states a
#: FACT about the compiler's obligations rather than a fact about one site.
_WASM_CANNOT_CATCH_NOTE = (
    "wasm validation cannot catch this -- a leaked or missing operand still validates (P6) "
    "-- so the frontend proves it instead (S17)"
)
_ERROR_IS_A_CODE_NOTE = (
    "contract errors are u32 codes delivered through fail_with_error, not exception "
    "instances (S7/S10)"
)


def _error(
    ctx: FuncCtx,
    code: str,
    loc: Loc,
    detail: str = "",
    *,
    help: str | None = None,
    notes: tuple[str, ...] = (),
) -> None:
    """Report `code` at `loc`, with the registry's own intent leading the
    message (the convention every `serpent.compiler` module follows)."""
    intent = _INTENT[code]
    message = f"{intent}: {detail}" if detail else intent
    ctx.sink.error(
        code, loc, message, help=help if help is not None else _HELP.get(code), notes=notes
    )


def _failed(node: IRExpr) -> bool:
    return node.ty.tag is TyTag.INVALID


# --- results ------------------------------------------------------------------


@dataclass(frozen=True)
class CheckedBody:
    """One checked function body.

    `returns_on_every_path` is the RESULT of SS C.3 rule 3's analysis, ready
    for `FuncIR.returns_on_every_path` -- recorded so no later task re-derives
    it (R4).

    **Invariant the emitter must honour.** `returns_on_every_path=True` does
    NOT guarantee the body ends in a `Return` node -- an infinite loop with no
    `break` also satisfies it (it DIVERGES rather than returning, so the rule
    holds vacuously: there is no normal exit for a missing return to escape
    through). A wasm validator, however, types a `loop` as falling through with
    its result type, so a function whose body ends after an infinite loop with
    an empty stack FAILS validation. The emitter must therefore terminate the
    function with `unreachable` whenever the last reachable statement is not a
    `Return` -- exactly the discipline spec SS 4 already demands ("early
    returns, missing returns" must be structurally impossible, S17/P6).
    """

    stmts: tuple[IRStmt, ...]
    returns_on_every_path: bool


@dataclass(frozen=True)
class _Block:
    """What checking one block of statements produced.

    `terminates` means "control does not fall through to the statement after
    this block" -- a `return`, a `raise`, a `break`/`continue` (which jump), an
    `if` whose both arms terminate, or a `while True:` with no `break`. It is
    deliberately ONE flag rather than separate "returns" and "jumps" flags:
    `break`/`continue` are only legal inside a loop, and a loop's own
    termination is computed from its condition and its breaks, so nothing
    outside a loop can mistake a jump for a return.
    """

    stmts: list[IRStmt]
    terminates: bool
    assigned: frozenset[int]


@dataclass
class _FnState:
    """Per-function bookkeeping that must NOT be branch-local.

    * `hidden` names hidden induction locals uniquely across every loop in the
      body.
    * `never_owned` is E11's syntactic pre-pass result
      (`recognize.collect_never_owned`): the local NAMES this body proves can
      never be exclusively C-owned. It is computed ONCE, from the whole body,
      before any statement is checked -- see `collect_never_owned`'s docstring
      for why a per-statement classification is unsound in a loop -- and is
      applied at the single point that could otherwise conclude `OWNED`
      (`_note_binding`).
    """

    hidden: int = 0
    never_owned: frozenset[str] = frozenset()

    def next_loop_id(self) -> int:
        self.hidden += 1
        return self.hidden - 1


def _new_state(stmts: list[ast.stmt], ctx: FuncCtx) -> _FnState:
    """Fresh per-function state, with E11's pre-pass already run.

    Running it HERE rather than in the assembly task is what makes the
    conservative answer the default: every entry into statement checking gets
    the whole-body alias/escape facts, so there is no way for a caller to
    check a body without them (`recognize_mutation`'s hand-off contract).
    """
    return _FnState(never_owned=recognize.collect_never_owned(stmts, ctx))


def _note_binding(
    name: str,
    slot: int,
    value: IRExpr,
    ctx: FuncCtx,
    state: _FnState,
    *,
    reason: str | None = None,
) -> None:
    """Record what binding `slot` to `value` means for E11.

    `recognize.note_local_binding` does the per-binding classification (and
    marks every local the right-hand side can BE as escaped); the pre-pass then
    OVERRIDES an `OWNED` verdict for any name the whole body proves can never
    be exclusively owned. The override is applied after, not before, because
    `note_local_binding`'s escape marking must run either way.

    `reason` is the author-facing cause a later `SPT1034` quotes back, for the
    shapes where the generic "aliased to another binding" would send the author
    looking for an assignment they never wrote.
    """
    ownership = recognize.note_local_binding(slot, value, ctx, reason)
    if ownership is Ownership.OWNED and name in state.never_owned:
        ctx.alias_sets.mark_aliased(slot, reason)


# --- public entry points ------------------------------------------------------


def check_body(stmts: list[ast.stmt], ctx: FuncCtx) -> list[IRStmt]:
    """Check a function body, returning its IR (errors go to `ctx.sink`).

    This is the statement-level counterpart to `check_expr`: it never raises,
    and a statement that failed to check contributes NOTHING to the returned
    list, so the IR only ever holds well-formed statements.

    The function-LEVEL definite-return rule (SS C.3 rule 3) is NOT applied
    here, because it needs a location to report against when a body simply
    falls off the end -- see `check_function_body`.

    One consequence of that split: `LocalSlot.definitely_assigned` is left
    describing whichever branch was walked LAST, because only
    `check_function_body` reconciles the field against the end of the body. A
    caller that needs the field to be true must use that entry point.
    """
    state = _new_state(stmts, ctx)
    params_assigned: frozenset[int] = frozenset()
    block = _check_block(stmts, ctx, state, params_assigned, is_function_body=True)
    return block.stmts


def check_function_body(stmts: list[ast.stmt], ctx: FuncCtx, *, loc: Loc) -> CheckedBody:
    """`check_body` plus SS C.3 rule 3: a non-`Void` function must return on
    every path (P6/S17).

    `loc` is the FUNCTION's own span, which is where a "not every path returns"
    diagnostic points -- the same choice mypy makes, because the missing return
    is a property of the whole body rather than of any one statement in it.
    """
    state = _new_state(stmts, ctx)
    block = _check_block(stmts, ctx, state, frozenset(), is_function_body=True)

    if ctx.return_ty.tag is not TyTag.VOID and not block.terminates:
        _error(
            ctx,
            "SPT7001",
            loc,
            f"`{ctx.fn_name}` returns {ctx.return_ty.render()}, but control can reach the "
            "end of the body without returning",
            notes=(_WASM_CANNOT_CATCH_NOTE,),
        )

    # Leave `definitely_assigned` describing the END of the body rather than
    # whichever branch happened to be walked last (see the module docstring).
    for slot in ctx.locals.slots:
        slot.definitely_assigned = slot.slot in block.assigned

    return CheckedBody(stmts=tuple(block.stmts), returns_on_every_path=block.terminates)


# --- the block walk -----------------------------------------------------------


def _check_block(
    stmts: list[ast.stmt],
    ctx: FuncCtx,
    state: _FnState,
    assigned: frozenset[int],
    *,
    is_function_body: bool = False,
) -> _Block:
    out: list[IRStmt] = []
    terminates = False
    for index, stmt in enumerate(stmts):
        if terminates:
            # Report once per block and stop: every later statement in this
            # block is unreachable for the same reason, and repeating that is
            # noise, not information.
            _error(
                ctx,
                "SPT7004",
                Loc.from_node(ctx.path, stmt),
                "this statement cannot be reached",
            )
            break
        if index == 0 and is_function_body and _is_docstring(stmt):
            # P1: skipped at module, class AND function level. It is not a
            # statement at all -- it flows into contractspecv0 (S4/B12).
            continue
        result = _check_stmt(stmt, ctx, state, assigned)
        out.extend(result.stmts)
        terminates = result.terminates
        assigned = result.assigned
    return _Block(stmts=out, terminates=terminates, assigned=assigned)


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


# --- the statement dispatch (MJ-11, statement side) --------------------------


def _check_stmt(stmt: ast.stmt, ctx: FuncCtx, state: _FnState, assigned: frozenset[int]) -> _Block:
    if isinstance(stmt, ast.Assign):
        return _check_assign(stmt, ctx, state, assigned)
    if isinstance(stmt, ast.AnnAssign):
        return _check_ann_assign(stmt, ctx, state, assigned)
    if isinstance(stmt, ast.AugAssign):
        return _check_aug_assign(stmt, ctx, state, assigned)
    if isinstance(stmt, ast.If):
        return _check_if(stmt, ctx, state, assigned)
    if isinstance(stmt, ast.While):
        return _check_while(stmt, ctx, state, assigned)
    if isinstance(stmt, ast.For):
        return _check_for(stmt, ctx, state, assigned)
    if isinstance(stmt, (ast.Break, ast.Continue)):
        return _check_loop_jump(stmt, ctx, assigned)
    if isinstance(stmt, ast.Raise):
        return _check_raise(stmt, ctx, assigned)
    if isinstance(stmt, ast.Return):
        return _check_return(stmt, ctx, state, assigned)
    if isinstance(stmt, ast.Expr):
        return _check_expr_stmt(stmt, ctx, state, assigned)
    if isinstance(stmt, ast.Pass):
        return _Block([Nop(loc=Loc.from_node(ctx.path, stmt))], False, assigned)
    return _reject_stmt_kind(stmt, ctx, assigned)


def _reject_stmt_kind(stmt: ast.stmt, ctx: FuncCtx, assigned: frozenset[int]) -> _Block:
    loc = Loc.from_node(ctx.path, stmt)
    code = STMT_KIND_CODES.get(type(stmt), _FALLBACK_CODE)
    _error(ctx, code, loc, f"`{type(stmt).__name__}` is not part of the serpent subset")
    return _nothing(assigned)


def _nothing(assigned: frozenset[int]) -> _Block:
    """A statement that produced no IR (it was rejected)."""
    return _Block([], False, assigned)


# --- expression helpers -------------------------------------------------------


def _check_value(
    node: ast.expr,
    ctx: FuncCtx,
    assigned: frozenset[int],
    *,
    expected: Ty | None = None,
) -> IRExpr:
    """Check one expression AND its definite-assignment obligations."""
    value = check_expr(node, ctx, expected=expected)
    _check_reads(value, ctx, assigned)
    return value


def _check_reads(value: IRExpr, ctx: FuncCtx, assigned: frozenset[int]) -> None:
    """SS C.3 rule 2: every local this expression READS must be definitely
    assigned on every path that reaches it.

    Walking the IR is what makes this cheap and complete at once: every read
    of a local is a `LocalRef` node, wherever in the expression tree it sits.
    Reported once per slot, so `x + x` is one diagnostic, not two.
    """
    seen: set[int] = set()
    for node in walk(value):
        if isinstance(node, LocalRef) and node.slot not in assigned and node.slot not in seen:
            seen.add(node.slot)
            _error(
                ctx,
                "SPT7002",
                node.loc,
                f"`{node.name}` is not assigned on every path that reaches this read",
            )


# --- Assign / AnnAssign / AugAssign ------------------------------------------


def _check_assign(
    stmt: ast.Assign, ctx: FuncCtx, state: _FnState, assigned: frozenset[int]
) -> _Block:
    if len(stmt.targets) != 1:
        _error(
            ctx,
            "SPT1029",
            Loc.from_node(ctx.path, stmt),
            "chained assignment binds several names at once",
        )
        return _nothing(assigned)
    return _assign_to_target(stmt.targets[0], stmt.value, None, ctx, state, assigned)


def _check_ann_assign(
    stmt: ast.AnnAssign, ctx: FuncCtx, state: _FnState, assigned: frozenset[int]
) -> _Block:
    loc = Loc.from_node(ctx.path, stmt)
    if stmt.value is None:
        _error(ctx, "SPT1036", loc, "a local with no value would have nothing to hold")
        return _nothing(assigned)
    declared = _resolve_annotation_expr(stmt.annotation, ctx)
    if declared is None:
        return _nothing(assigned)
    return _assign_to_target(stmt.target, stmt.value, declared, ctx, state, assigned)


def _check_aug_assign(
    stmt: ast.AugAssign, ctx: FuncCtx, state: _FnState, assigned: frozenset[int]
) -> _Block:
    """`x <op>= y` desugars to `x = x <op> y` BEFORE typing (E6).

    Building a real `ast.BinOp` and handing it to Task 5's checker -- rather
    than re-deriving the operator rules here -- is what guarantees `x += y` and
    `x = x + y` agree on everything: the omitted operators (`**=`, `&=`, ...
    become the same SPT3005 their binary forms get, A5/D2), `/=` pointing at
    `//`, cross-type operands, bool operands (T2), and the time types' total
    lack of arithmetic (D4).
    """
    if not isinstance(stmt.target, ast.Name):
        return _assign_to_target(stmt.target, stmt.value, None, ctx, state, assigned)
    desugared = ast.copy_location(ast.BinOp(left=stmt.target, op=stmt.op, right=stmt.value), stmt)
    return _assign_to_target(stmt.target, desugared, None, ctx, state, assigned)


def _assign_to_target(
    target: ast.expr,
    value_node: ast.expr,
    declared: Ty | None,
    ctx: FuncCtx,
    state: _FnState,
    assigned: frozenset[int],
) -> _Block:
    """Dispatch on the assignment TARGET (SS B.1's `Assign` rows)."""
    loc = Loc.from_node(ctx.path, target)

    if isinstance(target, (ast.Tuple, ast.List)):
        _error(ctx, "SPT1029", loc, "tuple unpacking binds several names at once")
        return _nothing(assigned)
    if isinstance(target, ast.Attribute):
        # A21: this is precisely the hole mypy cannot close on the 3.11 floor
        # (`dataclass_transform(frozen_default=)` is 3.12+), so the compiler
        # closes it instead.
        _error(
            ctx,
            "SPT4016",
            loc,
            f"`{target.attr}` cannot be assigned: a @contracttype value is immutable",
        )
        return _nothing(assigned)
    if isinstance(target, ast.Subscript):
        _error(ctx, "SPT1030", loc, "container elements are not assigned through a subscript")
        return _nothing(assigned)
    if not isinstance(target, ast.Name):
        _error(ctx, "SPT1029", loc, "only a single name can be assigned")
        return _nothing(assigned)

    return _assign_to_name(target, value_node, declared, ctx, state, assigned)


def _assign_to_name(
    target: ast.Name,
    value_node: ast.expr,
    declared: Ty | None,
    ctx: FuncCtx,
    state: _FnState,
    assigned: frozenset[int],
) -> _Block:
    loc = Loc.from_node(ctx.path, target)
    name = target.id

    if name == "self":
        _error(ctx, "SPT2002", loc, "`self` cannot be assigned; a @contract class has no state")
        return _nothing(assigned)

    existing = ctx.locals.lookup(name)
    # A literal takes its type from the annotation when there is one, else from
    # the local's already-fixed type (SS C.3 rule 1) -- so `total = 5` after
    # `total = U32(0)` coerces, while a bare first `x = 5` names the wrap
    # (MJ-12).
    expected = declared if declared is not None else (existing.ty if existing else None)
    value = _check_value(value_node, ctx, assigned, expected=expected)
    if _failed(value):
        return _nothing(assigned)

    if value.ty.tag is TyTag.VOID:
        _error(
            ctx,
            "SPT3018",
            loc,
            "a void expression has no value to assign",
            help="call it as a statement on its own line",
        )
        return _nothing(assigned)

    if declared is not None and value.ty != declared:
        _error(
            ctx,
            "SPT3018",
            loc,
            f"`{name}` is annotated {declared.render()} but the value is {value.ty.render()}",
        )
        return _nothing(assigned)

    slot = ctx.locals.declare(name, declared if declared is not None else value.ty, loc, ctx.sink)
    if slot is None:
        return _nothing(assigned)  # SPT3017 (rebind) or SPT2004 (shadow), already reported
    ctx.locals.mark_assigned(name)
    _note_binding(name, slot.slot, value, ctx, state)

    node: IRStmt
    if existing is None:
        node = LetLocal(loc=loc, slot=slot.slot, ty=slot.ty, init=value)
    else:
        node = SetLocal(loc=loc, slot=slot.slot, value=value)
    return _Block([node], False, assigned | {slot.slot})


def _resolve_annotation_expr(node: ast.expr, ctx: FuncCtx) -> Ty | None:
    """Resolve a FUNCTION-BODY annotation (`x: Vec[U32] = ...`) to a `Ty`.

    Task 4's `resolve_annotation` takes a real OBJECT, which is what the hybrid
    frontend (E1) has for declarations because the module was executed. A LOCAL
    annotation has no such object -- PEP 526 is explicit that annotations on a
    local are never evaluated at runtime, so nothing ever built one -- and it
    exists only as an AST node. Evaluating it here therefore runs code that
    merely importing the module does NOT, which is a step past E1's trust
    boundary rather than a use of it. Two independent guards keep that step
    small, and both are why this is not simply `eval` on user input:

    1. `_annotation_shape_ok` walks the annotation FIRST and refuses anything
       outside the shapes a real annotation takes -- a name, an attribute, a
       subscript, a constant, a tuple, `X | None`, and a call whose callee is a
       plain NAME. Nothing is compiled or evaluated until that passes, so a
       rejected annotation has no side effects at all.
    2. Evaluation happens in a COPY of the module's namespace with a RESTRICTED
       `__builtins__`: the copy stops an annotation rebinding a module name, and
       the restriction removes `__import__`/`open`/`eval`/`getattr` from reach
       while keeping the plain type names (`int`, `str`, ...) resolvable, so
       `x: int = ...` still gets B7's proper "not a chain type" refusal instead
       of a confusing `NameError`.

    Guard 1 still permits a call of a name to EXECUTE that name, which is
    required: `bytes_n(N)` is a real call the compiler must evaluate (A16/E20).
    The callee must be resolvable in the loaded module's own namespace, whose
    top-level code has already run by this point.

    PEP 563 (`from __future__ import annotations`, E4) does not affect any of
    this: it stringifies `__annotations__`, not the AST, so the node is still a
    real expression tree here.
    """
    loc = Loc.from_node(ctx.path, node)
    if not _annotation_shape_ok(node):
        # SPT3013, not SPT2003 (controller ruling, Task 6 fix round 2): a
        # rejected annotation SHAPE is not an undefined NAME, and SPT codes are
        # frozen public API, so a wrong-in-kind intent clause is a real cost.
        # SPT2003 stays for a genuinely undefined name -- the `except` clause
        # below, which is where a `NameError` from evaluation lands.
        _error(
            ctx,
            "SPT3013",
            loc,
            "the annotation was not evaluated: only chain-type annotation forms are "
            "resolved here -- a name, `X | None`, `Vec[T]`/`Map[K, V]`, or `bytes_n(N)`",
        )
        return None
    try:
        compiled = compile(ast.Expression(body=node), ctx.path, "eval")
        # `__builtins__` goes LAST: an executed module's namespace carries its
        # own real `__builtins__`, which would otherwise overwrite the
        # restricted one and hand `__import__` straight back.
        obj = eval(compiled, {**ctx.loaded.namespace, "__builtins__": _ANNOTATION_BUILTINS})
    except Exception as exc:  # noqa: BLE001 -- any failure is a user annotation error
        _error(
            ctx,
            "SPT2003",
            loc,
            f"the annotation could not be resolved: {type(exc).__name__}: {exc}",
        )
        return None
    return resolve_annotation(obj, ctx.loaded, loc, ctx.sink)


#: AST node kinds an annotation may contain outright. `BinOp` and `Call` are
#: NOT here: they are admitted only in one specific shape each (see
#: `_annotation_shape_ok`).
_ANNOTATION_NODES: Final[tuple[type[ast.AST], ...]] = tuple(
    kind
    for kind in (
        ast.Name,
        ast.Attribute,
        ast.Subscript,
        ast.Constant,
        ast.Tuple,
        ast.Load,
        ast.BitOr,
        # Present-but-unused on 3.9+; the parser stopped wrapping subscripts in
        # it, and it is slated for removal, so it is admitted only if it exists.
        getattr(ast, "Index", None),
    )
    if kind is not None
)

#: The only builtins an annotation can reach. Plain type names are kept so B7's
#: "plain int/str/bytes/bool is not a chain type" refusal still fires with its
#: own wording; everything that can touch the filesystem, import a module, or
#: reflect (`__import__`, `open`, `eval`, `exec`, `getattr`, ...) is absent.
_ANNOTATION_BUILTINS: Final[dict[str, object]] = {
    name: getattr(builtins, name)
    for name in (
        "bool",
        "bytearray",
        "bytes",
        "complex",
        "dict",
        "float",
        "frozenset",
        "int",
        "list",
        "memoryview",
        "object",
        "set",
        "str",
        "tuple",
        "type",
    )
}


def _annotation_shape_ok(node: ast.expr) -> bool:
    """Whether every node in `node` is a shape a real annotation takes.

    Checked BEFORE anything is compiled, so a rejected annotation never
    executes. `X | None` is the only binary operator form (a `BitOr`), and a
    call is admitted only when its callee is a plain NAME and it has no keyword
    arguments -- which covers `bytes_n(N)` (A16/E20) and nothing that needs an
    attribute lookup to reach, such as `__import__('pathlib').Path(...)`.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.BinOp):
            if not isinstance(sub.op, ast.BitOr):
                return False
        elif isinstance(sub, ast.Call):
            if not isinstance(sub.func, ast.Name) or sub.keywords:
                return False
        elif not isinstance(sub, _ANNOTATION_NODES):
            return False
    return True


# --- If / While ---------------------------------------------------------------


def _check_if(stmt: ast.If, ctx: FuncCtx, state: _FnState, assigned: frozenset[int]) -> _Block:
    loc = Loc.from_node(ctx.path, stmt)
    cond = check_condition(stmt.test, ctx)
    _check_reads(cond, ctx, assigned)

    body = _check_block(stmt.body, ctx, state, assigned)
    orelse = _check_block(stmt.orelse, ctx, state, assigned)

    if _failed(cond):
        return _nothing(assigned)

    node = If(loc=loc, cond=cond, body=tuple(body.stmts), orelse=tuple(orelse.stmts))
    return _Block([node], body.terminates and orelse.terminates, _merge(assigned, body, orelse))


def _merge(before: frozenset[int], body: _Block, orelse: _Block) -> frozenset[int]:
    """Which locals are definitely assigned after an `if`/`else`.

    A terminating arm cannot reach the code below, so it contributes nothing to
    the answer -- which is what makes the "bind in one arm, return in the
    other" shape work. When neither arm terminates the answer is the
    intersection; when the `else` is absent its arm is simply "assigned
    unchanged", and the intersection then correctly yields `before`.
    """
    if body.terminates and orelse.terminates:
        return before
    if body.terminates:
        return orelse.assigned
    if orelse.terminates:
        return body.assigned
    return body.assigned & orelse.assigned


def _check_while(
    stmt: ast.While, ctx: FuncCtx, state: _FnState, assigned: frozenset[int]
) -> _Block:
    loc = Loc.from_node(ctx.path, stmt)
    if stmt.orelse:
        _error(
            ctx,
            _FALLBACK_CODE,
            loc,
            "a `while ... else` clause is not supported",
            help="drop the else clause and put the code after the loop",
        )
        return _nothing(assigned)

    cond = check_condition(stmt.test, ctx)
    _check_reads(cond, ctx, assigned)
    body = _check_block(stmt.body, replace(ctx, loop_depth=ctx.loop_depth + 1), state, assigned)
    if _failed(cond):
        return _nothing(assigned)

    node = While(loc=loc, cond=cond, body=tuple(body.stmts))
    # The plan's explicit termination rule: `while True:` with no `break` has
    # no normal exit, so every exit path already returns or raises, and the
    # statement after the loop is unreachable. Any other condition may be
    # false on the first test, so the loop may contribute nothing at all --
    # neither termination nor assignments.
    terminates = _is_true_literal(stmt.test) and not _has_break(stmt.body)
    return _Block([node], terminates, assigned)


def _is_true_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _has_break(stmts: list[ast.stmt]) -> bool:
    """Whether any `break` in `stmts` targets the loop `stmts` is the body of.

    Does NOT descend into a nested `while`/`for`: a `break` there belongs to
    the inner loop and says nothing about whether the outer one can exit.
    """
    for stmt in stmts:
        if isinstance(stmt, ast.Break):
            return True
        if isinstance(stmt, (ast.While, ast.For, ast.AsyncFor)):
            continue
        for field, value in ast.iter_fields(stmt):
            del field
            if isinstance(value, list) and _has_break(
                [s for s in value if isinstance(s, ast.stmt)]
            ):
                return True
    return False


def _check_loop_jump(
    stmt: ast.Break | ast.Continue, ctx: FuncCtx, assigned: frozenset[int]
) -> _Block:
    loc = Loc.from_node(ctx.path, stmt)
    if ctx.loop_depth == 0:
        spelling = "break" if isinstance(stmt, ast.Break) else "continue"
        _error(ctx, "SPT7003", loc, f"`{spelling}` has no enclosing loop")
        return _nothing(assigned)
    node: IRStmt = Break(loc=loc) if isinstance(stmt, ast.Break) else Continue(loc=loc)
    return _Block([node], True, assigned)


# --- For: the two desugarings (E4/E5) ----------------------------------------


def _check_for(stmt: ast.For, ctx: FuncCtx, state: _FnState, assigned: frozenset[int]) -> _Block:
    loc = Loc.from_node(ctx.path, stmt)
    if stmt.orelse:
        _error(ctx, "SPT1018", loc, "a `for ... else` clause is not supported")
        return _nothing(assigned)
    if not isinstance(stmt.target, ast.Name):
        _error(ctx, "SPT1029", Loc.from_node(ctx.path, stmt.target), "one loop variable at a time")
        return _nothing(assigned)
    if _is_range_call(stmt.iter, ctx):
        assert isinstance(stmt.iter, ast.Call)
        return _desugar_for_range(stmt, stmt.iter, ctx, state, assigned)
    return _desugar_for_vec(stmt, ctx, state, assigned)


def _is_range_call(node: ast.expr, ctx: FuncCtx) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "range"
        # `range` is not importable from serpent (A22), so a module-level name
        # of that spelling would be the author's own -- and then this is not
        # the builtin form at all.
        and ctx.loaded.namespace.get("range") is None
    )


def _declare_hidden(name: str, ty: Ty, loc: Loc, ctx: FuncCtx) -> int | None:
    """Declare a hidden induction local and return its slot.

    The name is deliberately NOT a Python identifier (`$for0_index`), so it can
    never collide with a source name, a parameter, or a module constant --
    there is nothing to shadow and nothing an author can accidentally read.
    """
    slot = ctx.locals.declare(name, ty, loc, ctx.sink)
    if slot is None:  # pragma: no cover -- a `$` name cannot collide
        return None
    ctx.locals.mark_assigned(name)
    return slot.slot


def _bind_loop_target(
    target: ast.Name, ty: Ty, init: IRExpr, loc: Loc, ctx: FuncCtx, state: _FnState
) -> tuple[IRStmt, int] | None:
    """Bind a loop variable, mirroring `_assign_to_name`'s LetLocal/SetLocal
    choice (`ir.py` documents `LetLocal` as the FIRST binding of a slot).

    A loop variable is an ordinary local: it may already exist -- bound before
    the loop, or by an earlier loop over the same name -- and then this is a
    reassignment, not a declaration. `SlotTable.declare` still owns the two
    rules that can refuse it: a pre-bound slot of a DIFFERENT type is SS C.3
    rule 1's `SPT3017` (the same diagnostic an ordinary reassignment gets), and
    a name that collides with a parameter or module-level name is rule 4's
    `SPT2004`.
    """
    if target.id == "self":
        _error(ctx, "SPT2002", loc, "`self` cannot be a loop variable")
        return None
    existing = ctx.locals.lookup(target.id)
    slot = ctx.locals.declare(target.id, ty, loc, ctx.sink)
    if slot is None:
        return None
    ctx.locals.mark_assigned(target.id)
    _note_binding(target.id, slot.slot, init, ctx, state)
    node: IRStmt
    if existing is None:
        node = LetLocal(loc=loc, slot=slot.slot, ty=ty, init=init)
    else:
        node = SetLocal(loc=loc, slot=slot.slot, value=init)
    return node, slot.slot


def _desugar_for_vec(
    stmt: ast.For, ctx: FuncCtx, state: _FnState, assigned: frozenset[int]
) -> _Block:
    """`for x in vec:` -> `While` over `vec_len`/`vec_get` (E4).

    $iter = <vec>                       # evaluated ONCE
    $idx  = U32(0)
    while $idx < vec_len($iter):
        x = vec_get($iter, $idx)
        $idx = $idx + U32(1)            # BEFORE the body, so `continue` cannot skip it
        <body>
    """
    loc = Loc.from_node(ctx.path, stmt)
    assert isinstance(stmt.target, ast.Name)

    iterable = _check_value(stmt.iter, ctx, assigned)
    if _failed(iterable):
        return _nothing(assigned)
    if iterable.ty.tag is not TyTag.VEC:
        _error(
            ctx,
            "SPT1019",
            Loc.from_node(ctx.path, stmt.iter),
            f"{iterable.ty.render()} cannot be iterated",
        )
        return _nothing(assigned)
    elem_ty = iterable.ty.elem
    assert elem_ty is not None

    loop_id = state.next_loop_id()
    iter_slot = _declare_hidden(f"$for{loop_id}_iter", iterable.ty, loc, ctx)
    index_slot = _declare_hidden(f"$for{loop_id}_index", Ty.U32, loc, ctx)
    if iter_slot is None or index_slot is None:  # pragma: no cover
        return _nothing(assigned)

    iter_ref = LocalRef(loc=loc, ty=iterable.ty, slot=iter_slot, name=f"$for{loop_id}_iter")
    index_ref = LocalRef(loc=loc, ty=Ty.U32, slot=index_slot, name=f"$for{loop_id}_index")

    # The hidden `$iter` local holds the ITERABLE's own handle, so binding it is
    # an alias in exactly the E11 sense and both slots lose ownership. That is
    # what refuses `for x in v: v.push_back(x)`, where the two tiers genuinely
    # disagree: on chain the rebind leaves `$iter` pointing at the original
    # Vec and the loop terminates, at tier 1 the iteration sees the growing
    # container and does not. The cost is a conservative reject for a mutation
    # of `v` AFTER the loop, where the tiers would in fact have agreed --
    # E11's documented "when in doubt, ALIASED" direction.
    #
    # The `reason` matters more here than anywhere else the alias analysis
    # fires: the author wrote no `a = b` at all, so the generic detail would
    # describe an assignment that does not exist in their source.
    _note_binding(
        f"$for{loop_id}_iter",
        iter_slot,
        iterable,
        ctx,
        state,
        reason=_ITERATION_ALIAS_REASON,
    )

    bound = _bind_loop_target(
        stmt.target,
        elem_ty,
        HostCall(loc=loc, ty=elem_ty, fn_name="vec_get", args=(iter_ref, index_ref)),
        loc,
        ctx,
        state,
    )
    if bound is None:
        return _nothing(assigned)
    bind_target, target_slot_index = bound

    inner_assigned = assigned | {iter_slot, index_slot, target_slot_index}
    body = _check_block(
        stmt.body, replace(ctx, loop_depth=ctx.loop_depth + 1), state, inner_assigned
    )

    header: list[IRStmt] = [
        LetLocal(loc=loc, slot=iter_slot, ty=iterable.ty, init=iterable),
        LetLocal(loc=loc, slot=index_slot, ty=Ty.U32, init=Const(loc=loc, ty=Ty.U32, py_value=0)),
    ]
    cond = Compare(
        loc=loc,
        ty=Ty.Bool,
        op=CompareOp.LT,
        lhs=index_ref,
        rhs=HostCall(loc=loc, ty=Ty.U32, fn_name="vec_len", args=(iter_ref,)),
        via_obj_cmp=False,
    )
    loop_body: list[IRStmt] = [bind_target, _bump(index_ref, loc), *body.stmts]
    header.append(While(loc=loc, cond=cond, body=tuple(loop_body)))
    # The loop may run zero times, so neither the loop variable nor anything
    # the body bound is assigned afterwards -- only the two hidden locals are.
    return _Block(header, False, assigned | {iter_slot, index_slot})


def _desugar_for_range(
    stmt: ast.For, call: ast.Call, ctx: FuncCtx, state: _FnState, assigned: frozenset[int]
) -> _Block:
    """`for i in range(stop)` / `range(start, stop)` -> `While` (E5).

        $stop = <stop>                      # evaluated ONCE (python semantics)
        $idx  = <start or 0>
        while $idx < $stop:
            i = $idx                        # the body reads a COPY...
            $idx = $idx + 1                 # ...so the bump can precede it safely
            <body>

    The 3-argument and negative-step forms are rejected in M1: they need a
    signed step and a direction check for no benefit yet.
    """
    loc = Loc.from_node(ctx.path, stmt)
    assert isinstance(stmt.target, ast.Name)

    if call.keywords:
        named = ", ".join(kw.arg or "**" for kw in call.keywords)
        _error(
            ctx,
            "SPT1020",
            Loc.from_node(ctx.path, call),
            f"range() takes its bounds positionally here; keyword arguments ({named}) "
            "are not supported",
        )
        return _nothing(assigned)
    if not 1 <= len(call.args) <= 2:
        _error(
            ctx,
            "SPT1020",
            Loc.from_node(ctx.path, call),
            f"range() takes one or two positional arguments here, got {len(call.args)}",
        )
        return _nothing(assigned)

    start_node = call.args[0] if len(call.args) == 2 else None
    stop_node = call.args[-1]

    # The bound that is NOT a literal fixes the induction type (A6's rule, in
    # statement position); when both are literals there is nothing to infer
    # from, so the index takes the natural index type.
    stop_literal = _is_literal_expr(stop_node)
    start_literal = start_node is None or _is_literal_expr(start_node)
    if stop_literal and start_literal:
        index_ty: Ty | None = Ty.U32
        stop = _check_value(stop_node, ctx, assigned, expected=Ty.U32)
        start = (
            _check_value(start_node, ctx, assigned, expected=Ty.U32)
            if start_node is not None
            else Const(loc=loc, ty=Ty.U32, py_value=0)
        )
    elif not stop_literal:
        stop = _check_value(stop_node, ctx, assigned)
        index_ty = None if _failed(stop) else stop.ty
        start = (
            _check_value(start_node, ctx, assigned, expected=index_ty)
            if start_node is not None
            else Const(loc=loc, ty=index_ty or Ty.U32, py_value=0)
        )
    else:
        assert start_node is not None
        start = _check_value(start_node, ctx, assigned)
        index_ty = None if _failed(start) else start.ty
        stop = _check_value(stop_node, ctx, assigned, expected=index_ty)

    if _failed(stop) or _failed(start) or index_ty is None:
        return _nothing(assigned)
    if index_ty.wasm_arith_width is None:
        _error(
            ctx,
            "SPT3018",
            Loc.from_node(ctx.path, call),
            f"range() needs chain-integer bounds, not {index_ty.render()}",
        )
        return _nothing(assigned)
    if start.ty != index_ty:
        _error(
            ctx,
            "SPT3003",
            Loc.from_node(ctx.path, call),
            f"range() bounds are {start.ty.render()} and {stop.ty.render()}",
        )
        return _nothing(assigned)

    loop_id = state.next_loop_id()
    stop_slot = _declare_hidden(f"$for{loop_id}_stop", index_ty, loc, ctx)
    index_slot = _declare_hidden(f"$for{loop_id}_index", index_ty, loc, ctx)
    if stop_slot is None or index_slot is None:  # pragma: no cover
        return _nothing(assigned)

    stop_ref = LocalRef(loc=loc, ty=index_ty, slot=stop_slot, name=f"$for{loop_id}_stop")
    index_ref = LocalRef(loc=loc, ty=index_ty, slot=index_slot, name=f"$for{loop_id}_index")

    bound = _bind_loop_target(stmt.target, index_ty, index_ref, loc, ctx, state)
    if bound is None:
        return _nothing(assigned)
    bind_target, target_slot_index = bound

    inner_assigned = assigned | {stop_slot, index_slot, target_slot_index}
    body = _check_block(
        stmt.body, replace(ctx, loop_depth=ctx.loop_depth + 1), state, inner_assigned
    )

    header: list[IRStmt] = [
        LetLocal(loc=loc, slot=stop_slot, ty=index_ty, init=stop),
        LetLocal(loc=loc, slot=index_slot, ty=index_ty, init=start),
    ]
    cond = Compare(
        loc=loc,
        ty=Ty.Bool,
        op=CompareOp.LT,
        lhs=index_ref,
        rhs=stop_ref,
        via_obj_cmp=False,
    )
    loop_body: list[IRStmt] = [bind_target, _bump(index_ref, loc), *body.stmts]
    header.append(While(loc=loc, cond=cond, body=tuple(loop_body)))
    return _Block(header, False, assigned | {stop_slot, index_slot})


def _bump(index_ref: LocalRef, loc: Loc) -> IRStmt:
    """`$idx = $idx + 1`, under A4's checked arithmetic like any other add."""
    return SetLocal(
        loc=loc,
        slot=index_ref.slot,
        value=Binary(
            loc=loc,
            ty=index_ref.ty,
            op=BinaryOp.ADD,
            lhs=index_ref,
            rhs=Const(loc=loc, ty=index_ref.ty, py_value=1),
        ),
    )


def _is_literal_expr(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) or fold_literal(node) is not None


# --- Raise / Return / Expr ----------------------------------------------------


def _check_raise(stmt: ast.Raise, ctx: FuncCtx, assigned: frozenset[int]) -> _Block:
    """Only `raise <ErrorEnum>.<Member>` (S7/A20): a contract error is a u32
    code, not an exception instance, and it must reach the host through
    `fail_with_error` -- never a bare `unreachable` (R3)."""
    loc = Loc.from_node(ctx.path, stmt)
    exc = stmt.exc
    if isinstance(exc, ast.Attribute) and stmt.cause is None:
        enum = _error_enum_cases(exc, ctx)
        if enum is not None:
            enum_name, cases = enum
            code = cases.get(exc.attr)
            if code is None:
                # A real error enum with a member that does not exist: "no such
                # member" is a far better error than "this raise form is
                # unsupported", so it is reported instead of falling through.
                _error(
                    ctx,
                    "SPT2001",
                    Loc.from_node(ctx.path, exc),
                    f"`{enum_name}` has no member `{exc.attr}`",
                    help=f"declare it as `{exc.attr} = errorcode(N)` in `{enum_name}`",
                )
                return _rejected_raise(assigned)
            return _Block(
                [Raise(loc=loc, enum=enum_name, case=exc.attr, code=code)], True, assigned
            )
    _error(
        ctx,
        "SPT1021",
        loc,
        "only `raise <ErrorEnum>.<Member>` is supported",
        notes=(_ERROR_IS_A_CODE_NOTE,),
    )
    return _rejected_raise(assigned)


def _rejected_raise(assigned: frozenset[int]) -> _Block:
    """A rejected `raise` still TERMINATES its path, exactly as a rejected
    `return` does.

    The author wrote a statement that ends the path; the form of it is what the
    diagnostic is about. Reporting `terminates=False` here would draw a second,
    purely consequential "not every path returns" (SPT7001) on top of the real
    error, which is the cascade collect-all is supposed to avoid (E16).
    """
    return _Block([], True, assigned)


def _error_enum_cases(node: ast.Attribute, ctx: FuncCtx) -> tuple[str, dict[str, int]] | None:
    """`(enum name, {case: code})` if `node`'s base names an `@contracterror`
    class, else `None`. The codes come from `_serpent_type_`, which is where
    `errorcode(N)` already recorded them (A19/A20) -- never re-read from the
    AST, so the two views cannot disagree."""
    if not isinstance(node.value, ast.Name):
        return None
    obj = ctx.loaded.namespace.get(node.value.id)
    if not isinstance(obj, type):
        return None
    metadata = vars(obj).get(_METADATA_ATTR)
    if not isinstance(metadata, dict) or metadata.get("kind") != "error_enum":
        return None
    cases = metadata.get("cases")
    if not isinstance(cases, list):
        return None  # pragma: no cover -- decorators always record a list
    return node.value.id, {name: int(code) for name, code in cases}


def _check_return(
    stmt: ast.Return, ctx: FuncCtx, state: _FnState, assigned: frozenset[int]
) -> _Block:
    del state
    loc = Loc.from_node(ctx.path, stmt)
    is_void = ctx.return_ty.tag is TyTag.VOID

    if stmt.value is None:
        if not is_void:
            _error(
                ctx,
                "SPT7001",
                loc,
                f"a bare `return` in a method that returns {ctx.return_ty.render()}",
            )
            # Still TERMINATES: the path really does end here, and claiming
            # otherwise would add a second, redundant diagnostic at the end of
            # the body.
            return _Block([], True, assigned)
        return _Block([Return(loc=loc, value=None)], True, assigned)

    if is_void:
        _error(
            ctx,
            "SPT4017",
            loc,
            f"`{ctx.fn_name}` is annotated `-> None`",
        )
        return _Block([], True, assigned)

    value = _check_value(stmt.value, ctx, assigned, expected=ctx.return_ty)
    if _failed(value):
        return _Block([], True, assigned)
    if value.ty.tag is TyTag.ERROR_ENUM:
        # Belt and braces for S8: expression checking already refuses an error
        # case as a value (SPT3002), so this is the same rule stated at the one
        # other place it could ever be reached.
        _error(
            ctx,
            "SPT3001",
            loc,
            f"`{value.ty.render()}` cannot be returned",
            help="raise the error case instead: `raise Err.Case`",
        )
        return _Block([], True, assigned)
    if value.ty != ctx.return_ty:
        _error(
            ctx,
            "SPT3018",
            loc,
            f"`{ctx.fn_name}` returns {ctx.return_ty.render()}, but this value is "
            f"{value.ty.render()}",
        )
        return _Block([], True, assigned)
    return _Block([Return(loc=loc, value=value)], True, assigned)


def _check_expr_stmt(
    stmt: ast.Expr, ctx: FuncCtx, state: _FnState, assigned: frozenset[int]
) -> _Block:
    """A bare expression statement: a void call, or a reject.

    SS B.1's own worked example is `count + U32(1)` on a line of its own --
    computing a value and throwing it away is always a bug on chain, where the
    computation costs budget and may trap.

    A container MUTATION is recognized first, because its lowering is a REBIND
    (`v = vec_push_back(v, x)`, E11) and therefore a statement -- there is no
    value-position form of it to fall through to. `recognize_mutation` returns
    `None` for anything that is not a mutation at all, which is when ordinary
    void-expression checking takes over.
    """
    del state
    loc = Loc.from_node(ctx.path, stmt)
    if isinstance(stmt.value, ast.Call):
        mutation = recognize.recognize_mutation(stmt.value, ctx)
        if mutation is not None:
            if isinstance(mutation, SetLocal):
                # SS C.3 rule 2 still applies to the receiver and every
                # argument: `recognize_mutation` types the expressions, this
                # module owns definite assignment.
                _check_reads(mutation.value, ctx, assigned)
            # A `Nop` means the mutation was refused and reported; it lowers to
            # nothing and control still falls through.
            return _Block([mutation], False, assigned)
    if isinstance(stmt.value, ast.Constant):
        # A docstring was already skipped at index 0 of the body (P1); any
        # other bare literal is a no-op line.
        _error(ctx, "SPT1028", loc, "a literal on a line of its own does nothing")
        return _nothing(assigned)

    value = _check_value(stmt.value, ctx, assigned)
    if _failed(value):
        return _nothing(assigned)
    if value.ty.tag is not TyTag.VOID:
        _error(
            ctx,
            "SPT1028",
            loc,
            f"this expression produces a {value.ty.render()} that nothing consumes",
        )
        return _nothing(assigned)
    return _Block([Eval(loc=loc, value=value)], False, assigned)
