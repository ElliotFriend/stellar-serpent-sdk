"""Scalar expression checking: one `ast.expr` -> one typed `IRExpr`.

This is where the tier-1 oracle's *behavioral* contracts become *static* rules.
`serpent.types` answers "what happens when this runs"; this module answers "may
this be written at all", and dossier SS B.2 (the expression inventory, BINDING)
is the row-by-row specification of the difference. Three habits keep the two
tiers from drifting apart:

* **Delegate validation to the oracle.** A literal argument is validated by
  CONSTRUCTING the tier-1 value and discarding it (`Symbol("")`,
  `Bytes32(b"x")`, `U32(2**32)`, `Address("not-a-strkey")`), so the compile
  error carries the oracle's own refusal text and cannot disagree with it. The
  instance is never kept: `Const` holds the raw Python literal, and pooling
  stays sub-plan D's decision.
* **Reject rather than approximate** (S1). Every construct SS B.2 marks REJECT
  gets a registry code, a message carrying that row's intent, and a `help`
  naming the rewrite that works.
* **Record the decisions C owns** (R4/F.1): `via_obj_cmp` on every `Compare`
  (F.1.2/T5 -- a `Symbol` NEVER compares as a raw packed immediate), the
  explicit `IsZero` truthiness lowering (D3/E10), and NO arithmetic constant
  folding (F.1.10).

## What "no folding" means here, precisely (F.1.10)

`I32(2**31 - 1) + I32(1)` type-checks and overflows AT RUNTIME -- folding it
into a compile error would make `cases.py`'s `contract_error` expectation
unreachable. But `U32(2**32)` and `U32(5) + 2**32` ARE compile rejects, because
S3's bounds checks apply to literal COERCION. Both facts hold at once because
`fold_literal` folds only *plain-Python* literal arithmetic over `int`/`str`/
`bytes` CONSTANTS -- `2**32`, `-(2**31)`, `"a" * 33`, `b"x" * 10`, all of which
are just how Python spells a large or repeated literal -- and never touches a
subtree containing a chain value. That is also why `**` folds for plain ints
while `U32(2) ** U32(3)` is the SPT3005 reject A5/D2 require.

## Positions and the `expected` argument

A literal has no type of its own; it takes one from its position (S3). Callers
pass that position's type as `expected`: a constructor argument, the other side
of an arithmetic or comparison operator, a `Bool` argument, a condition
(MJ-12's `while True:`), or -- from Task 6 -- an annotated local or a return
type. With no `expected`, a bare literal is a reject naming the wrap.

## What this module does NOT own

`Subscript` (MJ-13), container/struct construction and their method tables
(Task 7b), the `Env` recognition table (Task 7a), and internal calls (Task 8).
Every one of those reaches a single `_deferred` helper that emits MJ-11's
catch-all code with a note naming the owning task -- a clean located
diagnostic, never a traceback (F.2.5) -- and every call site is marked with a
`TASK-7A`/`TASK-7B`/`TASK-8` comment so the owning task can find them all.
"""

from __future__ import annotations

import ast
import builtins
import operator
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Final

from serpent.compiler import codes
from serpent.compiler.ctx import FuncCtx
from serpent.compiler.diagnostics import Diagnostics, Loc
from serpent.compiler.ir import (
    Binary,
    BinaryOp,
    BoolOp,
    BoolOpKind,
    Compare,
    CompareOp,
    Const,
    ConstRef,
    HostCall,
    IfExp,
    IRExpr,
    IsZero,
    LocalRef,
    ParamRef,
    Unary,
    UnaryOp,
)
from serpent.compiler.types_ import ReprForm, Ty, TyTag, resolve_annotation
from serpent.decorators import _METADATA_ATTR
from serpent.types import (
    I32,
    I64,
    I128,
    U32,
    U64,
    U128,
    Address,
    Bool,
    Bytes,
    Duration,
    Map,
    String,
    Symbol,
    Timepoint,
    Vec,
    bytes_n,
)

__all__ = ["NODE_KIND_CODES", "check_condition", "check_expr", "fold_literal"]

#: `code -> message_intent`, so every diagnostic carries its registry row's own
#: wording (the convention `loader.py`/`types_.py`/`ctx.py` already follow).
_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

#: MJ-11's catch-all: any AST node kind no other row covers.
_FALLBACK_CODE = "SPT1037"


# --- the exhaustive-dispatch default table (MJ-11) ---------------------------

#: `ast` node kind -> the SS B.2 REJECT row's code. Every concrete `ast.expr`
#: subclass is either handled by `check_expr`'s dispatch or listed here, so an
#: unconsidered node is a clean diagnostic and never a traceback; anything not
#: listed (a synthetic node, or a node kind a future Python adds) falls to
#: `_FALLBACK_CODE`.
NODE_KIND_CODES: Final[dict[type[ast.AST], str]] = {
    ast.ListComp: "SPT1003",
    ast.SetComp: "SPT1003",
    ast.DictComp: "SPT1003",
    ast.GeneratorExp: "SPT1003",
    ast.JoinedStr: "SPT1004",
    ast.FormattedValue: "SPT1004",
    ast.Lambda: "SPT1005",
    ast.NamedExpr: "SPT1006",
    ast.Starred: "SPT1007",
    ast.Yield: "SPT1008",
    ast.YieldFrom: "SPT1008",
    ast.Slice: "SPT1009",
    ast.Tuple: "SPT1014",
    ast.List: "SPT1015",
    ast.Dict: "SPT1015",
    ast.Set: "SPT1015",
    ast.Await: "SPT1002",
}

#: `help:` rewrites for the dispatch table's codes. Mandatory for SPT1xxx
#: (F.2.11, enforced by the sink) and supplied for everything this module
#: raises, because a rejected construct is exactly where the author needs the
#: working form spelled out.
_HELP: Final[dict[str, str]] = {
    "SPT1002": "write a plain synchronous method; contract calls never await",
    "SPT1003": (
        "build the container explicitly -- Vec(U32, [...]) / Map(Symbol, U32, [...]) -- "
        "or fill it in a while loop"
    ),
    "SPT1004": (
        "there is no runtime string formatting on chain; String literals are compile-time constants"
    ),
    "SPT1005": "give the code a name: write a module-level helper function instead",
    "SPT1006": "assign on its own line, then use the name",
    "SPT1007": "pass every argument explicitly; a contract export has a fixed arity",
    "SPT1008": "return a value; there are no generators on chain",
    "SPT1009": "take a sub-range with the .slice(lo, hi) method",
    "SPT1014": (
        "a tuple is only a value as an event's topic tuple, in env.events().publish(topics, data)"
    ),
    "SPT1015": (
        "build a Vec(T, [...]) or Map(K, V, [...]); there is no python list, dict, or set on chain"
    ),
    "SPT1016": "read the value through a chain operation instead; this property is tier-1 only",
    "SPT1017": "use a chain type's own operators and methods",
    "SPT1035": "pass the argument positionally",
    "SPT1037": "rewrite the expression using the serpent subset",
    "SPT2001": "define the name as a parameter, a local, or a module-level constant",
    "SPT2002": "read and write contract state through env.storage(), not through self",
    "SPT3002": "an error case may only appear in `raise <ErrorEnum>.<Member>`",
    "SPT3004": "use a value the target chain type can represent",
    "SPT3009": "len() is defined for Vec, Map, and Bytes",
    "SPT3010": "give both branches the same chain type",
    "SPT3014": "use the runtime construction form in a value position, e.g. Vec(U32, [...])",
    "SPT3018": "pass a value of the expected chain type, converting explicitly if needed",
}


#: Notes reused across several diagnostics. They are module constants rather
#: than inline strings because each is cited by more than one row and because a
#: note is a fact about the DIVERGENCE, not about one call site.
_BOOL_AS_INT_NOTE = (
    "tier 1 accepts a bool wherever an int is accepted because python's bool IS an "
    "int (D4); on chain Bool is its own type with no arithmetic, so the compiler "
    "rejects it (T2/F.1.6)"
)
_LEN_SCOPE_NOTE = (
    "a Symbol's or String's length is not an operation M1 exposes; MJ-1 scopes len() "
    "to Vec, Map, and Bytes"
)
_TIME_ALGEBRA_NOTE = "time algebra is a sub-plan E decision (D4/A17)"
_RAW_LITERAL_NOTE = (
    "tier 1 answers False forever here, because a chain payload type does not coerce "
    "from a raw literal and __eq__ never raises (E13/T4/A7)"
)


# --- type-family vocabulary ---------------------------------------------------

#: `Timepoint`/`Duration`: full chain integers with NO arithmetic at all (D4,
#: A17). Rejecting on the DECLARED type -- never on the i64 representation --
#: is F.1.9's divergence guard.
_TIME_TAGS: Final[frozenset[TyTag]] = frozenset({TyTag.TIMEPOINT, TyTag.DURATION})

#: The types A4's checked-arithmetic contract covers.
_ARITH_TAGS: Final[frozenset[TyTag]] = frozenset(
    {TyTag.U32, TyTag.I32, TyTag.U64, TyTag.I64, TyTag.U128, TyTag.I128}
)

#: Truthiness scope (E10/D3): numeric chain types and `Bool`. Everything else
#: is a reject, because tier 1 answers `True` forever for a `Symbol`/`Vec`/
#: struct (no `__bool__`), which is F.1.3's silent trap.
_NUMERIC_TAGS: Final[frozenset[TyTag]] = _ARITH_TAGS | _TIME_TAGS

#: Tier-1 introspection properties with no host equivalent (SS B.2).
_INTROSPECTION_PROPERTIES: Final[frozenset[str]] = frozenset(
    {"value", "text", "data", "strkey", "is_account", "hi64", "lo64", "element_type"}
)

#: `TyTag -> the tier-1 class whose constructor VALIDATES a literal`. The
#: compile-time check is the oracle's own check, so the two cannot disagree
#: (A18). `BytesN(n)` is keyed off `_LENGTH` through `bytes_n` (B8), never a
#: class whitelist.
_ORACLE_CLASS: Final[dict[TyTag, type]] = {
    TyTag.BOOL: Bool,
    TyTag.U32: U32,
    TyTag.I32: I32,
    TyTag.U64: U64,
    TyTag.I64: I64,
    TyTag.U128: U128,
    TyTag.I128: I128,
    TyTag.TIMEPOINT: Timepoint,
    TyTag.DURATION: Duration,
    TyTag.SYMBOL: Symbol,
    TyTag.STRING: String,
    TyTag.BYTES: Bytes,
    TyTag.ADDRESS: Address,
}

#: `len()`'s ruled scope (MJ-1), typed `U32` (E19/F.1.4's documented one-way
#: divergence from tier 1's `int`). Host functions are named, never coded (B2).
_LEN_HOST_FN: Final[dict[TyTag, str]] = {
    TyTag.VEC: "vec_len",
    TyTag.MAP: "map_len",
    TyTag.BYTES: "bytes_len",
    TyTag.BYTES_N: "bytes_len",
}

_BINARY_OPS: Final[dict[type[ast.operator], BinaryOp]] = {
    ast.Add: BinaryOp.ADD,
    ast.Sub: BinaryOp.SUB,
    ast.Mult: BinaryOp.MUL,
    ast.FloorDiv: BinaryOp.FLOORDIV,
    ast.Mod: BinaryOp.MOD,
}

#: A5/D2: the operators serpent omits until a contract needs them.
_OMITTED_BINARY_OPS: Final[dict[type[ast.operator], str]] = {
    ast.Pow: "**",
    ast.MatMult: "@",
    ast.BitAnd: "&",
    ast.BitOr: "|",
    ast.BitXor: "^",
    ast.LShift: "<<",
    ast.RShift: ">>",
}

_COMPARE_OPS: Final[dict[type[ast.cmpop], CompareOp]] = {
    ast.Eq: CompareOp.EQ,
    ast.NotEq: CompareOp.NE,
    ast.Lt: CompareOp.LT,
    ast.LtE: CompareOp.LE,
    ast.Gt: CompareOp.GT,
    ast.GtE: CompareOp.GE,
}


# --- literal folding ---------------------------------------------------------

#: "not a compile-time literal" -- distinct from a literal whose value happens
#: to be `None` (`x: U32 | None = None` is a real literal).
_MISSING: Final[object] = object()

#: Guards so a typo cannot turn constant folding into a denial of service:
#: `2 ** 10**9` would otherwise allocate until the process dies. U128's widest
#: literal needs 128 bits and the longest Symbol 32 characters, so these caps
#: are far above anything a contract can legitimately spell.
_MAX_FOLD_BITS: Final[int] = 4096
_MAX_FOLD_LENGTH: Final[int] = 1 << 16


def fold_literal(node: ast.expr) -> int | str | bytes | None:
    """Evaluate `node` as a compile-time `int`/`str`/`bytes` literal, or return
    `None` if it is not one.

    This is PLAIN-PYTHON literal arithmetic over constants -- `2**32`,
    `-(2**31)`, `2**31 - 1`, `1 << 40`, `"a" * 33`, `b"\\x00" * 32` -- which is
    simply how Python spells a large or repeated literal. It is NOT arithmetic
    constant folding: any subtree containing a chain value, a name, or a call
    declines, so F.1.10's rule stands intact (`U32(2) ** U32(3)` is still the
    SPT3005 reject A5/D2 require, and `I32(MAX) + I32(1)` still overflows at
    runtime).

    `bool` constants deliberately DECLINE: Python's `True` is an `int`, and
    accepting it here would silently re-open the bool-as-int-operand leak T2
    and D4 require the compiler to reject.
    """
    value = _fold(node)
    if isinstance(value, bool) or value is _MISSING:
        return None
    if isinstance(value, (int, str, bytes)):
        return value
    return None


def _fold(node: ast.expr) -> object:
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool):
            return _MISSING
        return value if isinstance(value, (int, str, bytes)) else _MISSING
    if isinstance(node, ast.UnaryOp):
        operand = _fold(node.operand)
        if not isinstance(operand, int) or isinstance(operand, bool):
            return _MISSING
        if isinstance(node.op, ast.USub):
            return _guard(-operand)
        if isinstance(node.op, ast.UAdd):
            return _guard(+operand)
        if isinstance(node.op, ast.Invert):
            return _guard(~operand)
        return _MISSING
    if isinstance(node, ast.BinOp):
        left = _fold(node.left)
        right = _fold(node.right)
        if left is _MISSING or right is _MISSING:
            return _MISSING
        return _guard(_apply_fold(node.op, left, right))
    return _MISSING


def _apply_fold(op: ast.operator, left: object, right: object) -> object:
    """Apply one plain-Python literal operator, declining anything unsafe."""
    if isinstance(op, ast.Pow) and not (isinstance(right, int) and 0 <= right <= 512):
        return _MISSING
    if isinstance(op, (ast.LShift, ast.RShift)) and not (
        isinstance(right, int) and 0 <= right <= 512
    ):
        return _MISSING
    if isinstance(op, ast.Mult):
        # `"a" * 33` / `b"x" * 10`: cap the RESULT length before allocating it,
        # so a typo (or a nested `("a" * 65536) * 65536`) cannot ask for
        # gigabytes -- `_guard` runs after the operation and would be too late.
        # Anything else (`"a" * "b"`) falls through to the handler's TypeError.
        for sequence, count in ((left, right), (right, left)):
            if (
                isinstance(sequence, (str, bytes))
                and isinstance(count, int)
                and count > 0
                and len(sequence) * count > _MAX_FOLD_LENGTH
            ):
                return _MISSING
    handler = _FOLD_OPS.get(type(op))
    if handler is None:
        return _MISSING
    try:
        return handler(left, right)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError, MemoryError):
        return _MISSING


def _guard(value: object) -> object:
    """Decline a folded value too big to be a legitimate contract literal."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value.bit_length() <= _MAX_FOLD_BITS else _MISSING
    if isinstance(value, (str, bytes)):
        return value if len(value) <= _MAX_FOLD_LENGTH else _MISSING
    return value


#: The plain-Python operators `fold_literal` may apply to int/str/bytes
#: constants. `Pow`/`LShift`/`RShift`/bitwise are here because `2**32`,
#: `1 << 40` and `0xFF & mask` are LITERAL SPELLINGS in Python source, not
#: chain arithmetic -- chain-typed `**`, `<<` and `&` remain the SPT3005
#: rejects A5/D2 require, because a subtree holding a chain value never folds.
_FOLD_OPS: Final[dict[type[ast.operator], Callable[[Any, Any], Any]]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
}


def _literal_value(node: ast.expr) -> object:
    """The compile-time literal `node` denotes, or `_MISSING`.

    Wider than `fold_literal`: a bare `True`/`False`/`None` Constant IS a
    literal for coercion purposes (MJ-12's `while True:`, and `None` for an
    `Option`), even though neither may be folded into arithmetic.
    """
    if isinstance(node, ast.Constant):
        return node.value
    folded = fold_literal(node)
    return _MISSING if folded is None else folded


def _is_bool_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, bool)


# --- diagnostics helpers ------------------------------------------------------


def _error(
    ctx: FuncCtx,
    code: str,
    loc: Loc,
    detail: str = "",
    *,
    help: str | None = None,
    notes: tuple[str, ...] = (),
) -> None:
    """Report `code` at `loc`, with the registry's own intent as the message's
    first clause (so a `must_reject` fixture can assert on either)."""
    intent = _INTENT[code]
    message = f"{intent}: {detail}" if detail else intent
    ctx.sink.error(
        code, loc, message, help=help if help is not None else _HELP.get(code), notes=notes
    )


def _invalid(loc: Loc) -> IRExpr:
    """The sink convention's error placeholder (minor 13): a `Ty.Invalid`-typed
    node, so a caller can keep walking without cascading a second error."""
    return Const(loc=loc, ty=Ty.Invalid, py_value=None)


def _failed(node: IRExpr) -> bool:
    return node.ty.tag is TyTag.INVALID


def _deferred(node: ast.expr, ctx: FuncCtx, owner: str, what: str) -> IRExpr:
    """A construct the M1-C subset SUPPORTS but a LATER task checks.

    MJ-13 rules this for `Subscript` explicitly ("route to a placeholder that
    Task 7b replaces") and the same reasoning covers container/struct
    construction, the `Env` recognition table, and internal calls: a clean
    located diagnostic today, replaced by real checking when its task lands.
    The catch-all code is deliberate -- the SPT registry is frozen at Task 1
    and no row describes "recognized, not yet implemented" -- and the note
    names the owning task so the placeholder cannot quietly outlive it.
    """
    loc = Loc.from_node(ctx.path, node)
    _error(
        ctx,
        _FALLBACK_CODE,
        loc,
        what,
        notes=(f"checking this construct lands in {owner} of the M1-C plan",),
    )
    return _invalid(loc)


def _peek_ty(node: ast.expr, ctx: FuncCtx) -> Ty:
    """The `Ty` `node` would check to, WITHOUT reporting anything.

    Used where a diagnostic needs to name the other operand's type but must
    not double-report its errors (E13's raw-literal comparison) or where the
    shape of the base decides which task owns the node (an introspection
    property vs. a struct field read). A scratch sink keeps the peek silent;
    the caller re-checks through the real sink when it wants the errors.
    """
    return check_expr(node, replace(ctx, sink=Diagnostics())).ty


# --- literal coercion (S3) ----------------------------------------------------


def _describe(value: object) -> str:
    if isinstance(value, bool):
        return "a bool literal"
    if isinstance(value, int):
        return "an int literal"
    if isinstance(value, str):
        return "a str literal"
    if isinstance(value, bytes):
        return "a bytes literal"
    if value is None:
        return "None"
    return f"a {type(value).__name__} literal"


def _wrap_help(value: object) -> str:
    """The `help:` line for a bare literal: the wrap that works (SS B.2)."""
    if isinstance(value, bool):
        return f"wrap it in a chain type: Bool({value})"
    if isinstance(value, int):
        return f"wrap it in a chain type, e.g. U32({value}) or I128({value})"
    if isinstance(value, str):
        return f"wrap it in a chain type, e.g. Symbol({value!r}) or String({value!r})"
    if isinstance(value, bytes):
        return f"wrap it in a chain type, e.g. Bytes({value!r})"
    if value is None:
        return "None is only a value where the type is X | None"
    return "wrap the literal in one of serpent's chain types"


def _oracle_class(ty: Ty) -> type | None:
    if ty.tag is TyTag.BYTES_N:
        assert ty.n is not None
        return bytes_n(ty.n)
    return _ORACLE_CLASS.get(ty.tag)


def _validate_literal(value: object, cls: type, loc: Loc, ctx: FuncCtx) -> bool:
    """Validate `value` by CONSTRUCTING the tier-1 chain value and discarding
    it, so the compile error is the oracle's own refusal text (A18/B7).

    A `ValueError` is a value the type cannot represent (S3's compile-time
    bounds check: an out-of-range int, an invalid `Symbol` charset/length, the
    wrong fixed `Bytes` length, a malformed strkey); a `TypeError` is the
    wrong KIND of literal for the type. The two map to different codes because
    they are different author mistakes with different fixes.
    """
    try:
        # Every chain type's constructor takes exactly one payload argument;
        # `cls` is a bare `type` here, so mypy cannot see that signature.
        cls(value)
    except ValueError as exc:
        _error(ctx, "SPT3004", loc, str(exc))
        return False
    except TypeError as exc:
        _error(ctx, "SPT3018", loc, str(exc))
        return False
    return True


def _coerce_literal(value: object, loc: Loc, ctx: FuncCtx, expected: Ty | None) -> IRExpr:
    """Coerce one compile-time literal to `expected` with S3's bounds check."""
    if expected is None or expected.tag is TyTag.INVALID:
        _error(
            ctx,
            "SPT3008",
            loc,
            f"{_describe(value)} has no chain type in this position",
            help=_wrap_help(value),
        )
        return _invalid(loc)

    if isinstance(value, bool):
        if expected.tag is TyTag.BOOL:
            return Const(loc=loc, ty=Ty.Bool, py_value=value)
        _error(
            ctx,
            "SPT3018",
            loc,
            f"a bool literal is not a {expected.render()}",
            notes=(_BOOL_AS_INT_NOTE,),
        )
        return _invalid(loc)

    if value is None:
        if expected.tag is TyTag.OPTION:
            return Const(loc=loc, ty=expected, py_value=None)
        _error(ctx, "SPT3018", loc, f"None is not a {expected.render()}")
        return _invalid(loc)

    if expected.tag is TyTag.OPTION:
        # `x: U32 | None = 5`: coerce to the wrapped type; widening the result
        # to the Option is the assigning statement's concern (Task 6).
        assert expected.elem is not None
        return _coerce_literal(value, loc, ctx, expected.elem)

    cls = _oracle_class(expected)
    if cls is None:
        _error(
            ctx,
            "SPT3018",
            loc,
            f"{_describe(value)} cannot be a {expected.render()}",
            help=f"build the {expected.render()} explicitly",
        )
        return _invalid(loc)

    if not _validate_literal(value, cls, loc, ctx):
        return _invalid(loc)
    return Const(loc=loc, ty=expected, py_value=value)


# --- the dispatch (MJ-11) -----------------------------------------------------


def check_expr(node: ast.expr, ctx: FuncCtx, *, expected: Ty | None = None) -> IRExpr:
    """Check one expression, returning its typed `IRExpr`.

    Errors go to `ctx.sink` (the sink convention, minor 13) and the return
    value is then a `Ty.Invalid`-typed placeholder -- this function never
    raises and never returns a `Diagnostic`.

    `expected` is the chain type this POSITION demands, and is what a literal
    coerces to (S3): a constructor argument, the other side of an operator, a
    `Bool` argument, a condition (MJ-12), or an annotated target from Task 6.
    It is a HINT, not a constraint: a non-literal expression is checked on its
    own terms and the caller compares the result (so an annotated-assignment
    mismatch is reported once, at the assignment, not twice).

    The dispatch is EXHAUSTIVE (MJ-11): the final branch consults
    `NODE_KIND_CODES` and falls back to the catch-all code, so an
    unconsidered node kind is a clean diagnostic, never a traceback.
    """
    if isinstance(node, ast.Constant):
        return _coerce_literal(node.value, Loc.from_node(ctx.path, node), ctx, expected)
    if isinstance(node, ast.Name):
        return _check_name(node, ctx)
    if isinstance(node, ast.Attribute):
        return _check_attribute(node, ctx)
    if isinstance(node, ast.Call):
        return _check_call(node, ctx)
    if isinstance(node, ast.BinOp):
        return _check_binop(node, ctx, expected)
    if isinstance(node, ast.UnaryOp):
        return _check_unaryop(node, ctx, expected)
    if isinstance(node, ast.Compare):
        return _check_compare(node, ctx)
    if isinstance(node, ast.BoolOp):
        return _check_boolop(node, ctx)
    if isinstance(node, ast.IfExp):
        return _check_ifexp(node, ctx, expected)
    if isinstance(node, ast.Subscript):
        return _check_subscript(node, ctx)
    return _reject_node_kind(node, ctx)


def _reject_node_kind(node: ast.expr, ctx: FuncCtx) -> IRExpr:
    """MJ-11's default branch."""
    loc = Loc.from_node(ctx.path, node)
    code = NODE_KIND_CODES.get(type(node), _FALLBACK_CODE)
    _error(ctx, code, loc, f"`{type(node).__name__}` is not part of the serpent subset")
    return _invalid(loc)


def check_condition(node: ast.expr, ctx: FuncCtx) -> IRExpr:
    """Check `node` in a CONDITION position, returning a `Bool`-typed `IRExpr`.

    The one entry point Task 6's `if`/`while` and this module's `IfExp` share,
    so the two coercions a condition position performs happen in exactly one
    place: `True`/`False` become `Bool` (MJ-12 -- which is what makes `while
    True:` supported), and a numeric chain value becomes D3's explicit
    zero-test. Truthiness of anything else is a reject (E10/F.1.3).
    """
    return _truthiness(check_expr(node, ctx, expected=Ty.Bool), ctx)


def _truthiness(operand: IRExpr, ctx: FuncCtx) -> IRExpr:
    """Lower a truthiness test (D3/E10). See `IsZero`'s docstring on polarity:
    `bool(x)` is `x != 0`, i.e. `not IsZero(x)`."""
    if _failed(operand):
        return operand
    if operand.ty.tag is TyTag.BOOL:
        return operand
    if operand.ty.tag in _NUMERIC_TAGS:
        return Unary(
            loc=operand.loc,
            ty=Ty.Bool,
            op=UnaryOp.NOT,
            operand=IsZero(loc=operand.loc, ty=Ty.Bool, operand=operand),
        )
    _error(
        ctx,
        "SPT3015",
        operand.loc,
        f"{operand.ty.render()} has no truthiness on chain",
        help=(
            "write the explicit test, e.g. len(v) > U32(0), storage.has(k), or "
            "s == Symbol('expected')"
        ),
    )
    return _invalid(operand.loc)


# --- Name ---------------------------------------------------------------------


def _check_name(node: ast.Name, ctx: FuncCtx) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)
    name = node.id

    if name == "self":
        _error(ctx, "SPT2002", loc, "`self` has no fields and no state")
        return _invalid(loc)

    for index, (param_name, ty, _param_loc) in enumerate(ctx.params):
        if param_name == name:
            return ParamRef(loc=loc, ty=ty, index=index, name=name)

    slot = ctx.locals.lookup(name)
    if slot is not None:
        return LocalRef(loc=loc, ty=slot.ty, slot=slot.slot, name=name)

    for const in ctx.loaded.module_consts:
        if const.name == name:
            # The const's Ty comes from its VALUE's class through the same
            # resolver annotations use, so a module constant and a parameter of
            # the same chain type can never resolve to different `Ty`s.
            const_ty = resolve_annotation(type(const.value), ctx.loaded, loc, ctx.sink)
            if const_ty is None:
                return _invalid(loc)
            return ConstRef(loc=loc, ty=const_ty, name=name)

    obj = ctx.loaded.namespace.get(name)
    if obj is None:
        _error(ctx, "SPT2001", loc, f"`{name}` is not defined in this contract")
        return _invalid(loc)

    if isinstance(obj, type):
        metadata = vars(obj).get(_METADATA_ATTR)
        if isinstance(metadata, dict) and metadata.get("kind") == "error_enum":
            _error(ctx, "SPT3002", loc, f"`{name}` is an error enum, not a value")
            return _invalid(loc)
        _error(
            ctx,
            "SPT3014",
            loc,
            f"`{name}` is a type; it may only appear in an annotation or as a "
            "construction argument",
        )
        return _invalid(loc)

    # A module-level helper referenced without calling it (E8's surface is
    # `helper(x)`, never a function value: there are no first-class functions).
    _error(
        ctx,
        _FALLBACK_CODE,
        loc,
        f"`{name}` cannot be used as a value",
        help=f"call it: {name}(...)",
    )
    return _invalid(loc)


# --- Attribute ----------------------------------------------------------------


def _check_attribute(node: ast.Attribute, ctx: FuncCtx) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)

    base = node.value
    if isinstance(base, ast.Name):
        obj = ctx.loaded.namespace.get(base.id)
        if isinstance(obj, type):
            metadata = vars(obj).get(_METADATA_ATTR)
            if isinstance(metadata, dict) and metadata.get("kind") == "error_enum":
                return _check_error_member(node, ctx, base.id, metadata, loc)

    if node.attr in _INTROSPECTION_PROPERTIES:
        base_ty = _peek_ty(base, ctx)
        # A struct field can legitimately be named `value` -- that is a field
        # read, which is Task 7b's; only a real chain-type base is this row.
        if base_ty.tag not in (TyTag.INVALID, TyTag.STRUCT):
            _error(
                ctx,
                "SPT1016",
                loc,
                f"`.{node.attr}` on {base_ty.render()} is tier-1 introspection with no "
                "host equivalent",
            )
            return _invalid(loc)

    # TASK-7B: struct field reads (`bal.amount`).
    # TASK-7A: the Env surface (`env.storage()`, `env.ledger()`, ...).
    return _deferred(node, ctx, "Task 7a/7b", f"attribute access `.{node.attr}`")


def _check_error_member(
    node: ast.Attribute,
    ctx: FuncCtx,
    enum_name: str,
    metadata: dict[str, object],
    loc: Loc,
) -> IRExpr:
    """`ErrorEnum.Member` outside a `raise` (S8): `Error` is never a value.

    Task 6 matches `raise <ErrorEnum>.<Member>` structurally on the AST, so a
    member reference that reaches expression checking is BY CONSTRUCTION out
    of raise position.
    """
    cases = metadata.get("cases")
    names = {name for name, _code in cases} if isinstance(cases, list) else set()
    if node.attr in names:
        _error(
            ctx,
            "SPT3002",
            loc,
            f"`{enum_name}.{node.attr}` is an error case, not a value",
            notes=("Error is never a returnable value (S8)",),
        )
    else:
        _error(
            ctx,
            "SPT2001",
            loc,
            f"`{enum_name}` has no member `{node.attr}`",
            help=f"declare it as `{node.attr} = errorcode(N)` in `{enum_name}`",
        )
    return _invalid(loc)


# --- Subscript (MJ-13: Task 7b owns this) ------------------------------------


def _check_subscript(node: ast.Subscript, ctx: FuncCtx) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)
    base = node.value
    if isinstance(base, ast.Name):
        obj = ctx.loaded.namespace.get(base.id)
        if obj is Vec or obj is Map:
            _error(
                ctx,
                "SPT3014",
                loc,
                f"`{base.id}[...]` is the annotation-only form",
                help=(f"in a value position use the construction form, e.g. {base.id}(U32, [...])"),
            )
            return _invalid(loc)
    # TASK-7B: `Bytes[i]` -> bytes_get, slices -> the .slice(lo, hi) rewrite,
    # negative literal indices (D6), and the alias-sensitive forms (E11).
    return _deferred(node, ctx, "Task 7b", "subscripting")


# --- Call ---------------------------------------------------------------------


def _check_call(node: ast.Call, ctx: FuncCtx) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)

    for arg in node.args:
        if isinstance(arg, ast.Starred):
            return _reject_node_kind(arg, ctx)
    for keyword in node.keywords:
        if keyword.arg is None:
            _error(ctx, "SPT1007", loc, "`**` argument unpacking is not supported")
            return _invalid(loc)

    func = node.func
    if not isinstance(func, ast.Name):
        # TASK-7A: `env.storage().get(...)`, `addr.require_auth()`.
        # TASK-7B: container/struct methods; `Event(...).publish(env)` (E12).
        return _deferred(node, ctx, "Task 7a/7b", "a method call")

    name = func.id
    if name == "bool":
        return _check_bool_call(node, ctx, loc)
    if name == "len":
        return _check_len_call(node, ctx, loc)

    obj = ctx.loaded.namespace.get(name)
    if obj is Vec or obj is Map:
        # TASK-7B: Vec(T[, items]) / Map(K, V[, pairs]) (D2/A13, MJ-15).
        return _deferred(node, ctx, "Task 7b", f"`{name}(...)` construction")
    if isinstance(obj, type):
        metadata = vars(obj).get(_METADATA_ATTR)
        if isinstance(metadata, dict):
            kind = metadata.get("kind")
            if kind == "error_enum":
                _error(
                    ctx,
                    "SPT3002",
                    loc,
                    f"`{name}` is an error enum; its members are codes, not instances",
                )
                return _invalid(loc)
            # TASK-7B: struct construction (kwargs only, P7).
            # TASK-7A: event construction + publish (E12).
            return _deferred(node, ctx, "Task 7a/7b", f"`{name}(...)` construction")
        return _check_chain_constructor(node, ctx, loc, name, obj)

    if any(helper.name == name for helper in ctx.loaded.helpers):
        # TASK-8: module-level helpers and private methods (E8, InternalCall).
        return _deferred(node, ctx, "Task 8", f"the call to `{name}`")

    # Before the builtin table, because a parameter may legitimately be named
    # `id` or `type` and "that is a value, not a callable" is the true error.
    if any(param_name == name for param_name, _ty, _loc in ctx.params) or (
        ctx.locals.lookup(name) is not None
    ):
        _error(ctx, _FALLBACK_CODE, loc, f"`{name}` is a value, not something callable")
        return _invalid(loc)

    if hasattr(builtins, name):
        _error(
            ctx,
            "SPT1017",
            loc,
            f"`{name}` is a python builtin with no on-chain equivalent",
        )
        return _invalid(loc)

    _error(ctx, "SPT2001", loc, f"`{name}` is not defined in this contract")
    return _invalid(loc)


def _single_arg(node: ast.Call, ctx: FuncCtx, loc: Loc, name: str) -> ast.expr | None:
    """The one argument `name(...)` takes, or `None` after reporting."""
    if node.keywords:
        keyword = node.keywords[0].arg
        _error(
            ctx,
            "SPT1035",
            loc,
            f"`{name}()` does not name a keyword parameter (`{keyword}`)",
        )
        return None
    if len(node.args) != 1:
        _error(
            ctx,
            _FALLBACK_CODE,
            loc,
            f"`{name}()` takes exactly one argument, got {len(node.args)}",
            help=f"pass exactly one value, e.g. {name}(x)",
        )
        return None
    return node.args[0]


def _check_chain_constructor(
    node: ast.Call, ctx: FuncCtx, loc: Loc, name: str, cls: type
) -> IRExpr:
    """`U32(5)`, `Symbol("transfer")`, `Bool(who == admin)` (SS B.2, P4).

    With a LITERAL argument this is S3's coercion: validated by the oracle
    class itself, so the bounds/charset/length/strkey checks are the tier-1
    checks. With a RUNTIME argument (P4, `token_style.py:87`) it is an
    identity: the value already has the type, and the only question is whether
    it has the RIGHT type. A different chain type is a reject rather than a
    silent conversion, exactly as tier 1 refuses `U32(U64(1))` -- "no implicit
    conversion between chain types".

    Same-type identity must stay legal: `len()` is `int` at tier 1 and `U32`
    in the compiler (E19/F.1.4), so `U32(len(v))` is idiomatic and cannot be
    rejected.
    """
    ty = resolve_annotation(cls, ctx.loaded, loc, ctx.sink)
    if ty is None:
        return _invalid(loc)

    arg = _single_arg(node, ctx, loc, name)
    if arg is None:
        return _invalid(loc)

    value = _literal_value(arg)
    if value is not _MISSING:
        if isinstance(value, bool) and ty.tag is not TyTag.BOOL:
            # The oracle would ACCEPT this (python's bool is an int); T2/D4
            # make it a compile reject, so it must be caught before delegating.
            _error(
                ctx,
                "SPT3018",
                loc,
                f"a bool literal is not a {ty.render()}",
                notes=(_BOOL_AS_INT_NOTE,),
            )
            return _invalid(loc)
        if not _validate_literal(value, cls, loc, ctx):
            return _invalid(loc)
        return Const(loc=loc, ty=ty, py_value=value)

    operand = check_expr(arg, ctx, expected=ty)
    if _failed(operand):
        return _invalid(loc)
    if operand.ty == ty:
        return operand
    _error(
        ctx,
        "SPT3018",
        loc,
        f"{name}() takes a {ty.render()}, not {operand.ty.render()}",
        help=f"there is no implicit conversion between chain types; build the {name} explicitly",
    )
    return _invalid(loc)


def _check_bool_call(node: ast.Call, ctx: FuncCtx, loc: Loc) -> IRExpr:
    """`bool(x)`: D3's zero-test, typed `Bool`."""
    arg = _single_arg(node, ctx, loc, "bool")
    if arg is None:
        return _invalid(loc)
    return _truthiness(check_expr(arg, ctx, expected=Ty.Bool), ctx)


def _check_len_call(node: ast.Call, ctx: FuncCtx, loc: Loc) -> IRExpr:
    """`len(x)` on Vec/Map/Bytes -> `U32` (MJ-1's scoping ruling, E19)."""
    arg = _single_arg(node, ctx, loc, "len")
    if arg is None:
        return _invalid(loc)
    operand = check_expr(arg, ctx)
    if _failed(operand):
        return _invalid(loc)
    fn_name = _LEN_HOST_FN.get(operand.ty.tag)
    if fn_name is None:
        _error(
            ctx,
            "SPT3009",
            loc,
            f"len() is not defined for {operand.ty.render()}",
            notes=(_LEN_SCOPE_NOTE,),
        )
        return _invalid(loc)
    return HostCall(loc=loc, ty=Ty.U32, fn_name=fn_name, args=(operand,))


# --- BinOp / UnaryOp ----------------------------------------------------------


def _check_operand_pair(left: ast.expr, right: ast.expr, ctx: FuncCtx) -> tuple[IRExpr, IRExpr]:
    """Check two operands so a literal takes the OTHER side's type (A6).

    The non-literal side is checked first and, when it fails, the literal side
    is skipped entirely -- a bare literal whose partner already errored would
    otherwise add a second, purely cascaded diagnostic.
    """
    left_is_literal = _literal_value(left) is not _MISSING
    right_is_literal = _literal_value(right) is not _MISSING

    if left_is_literal and not right_is_literal:
        rhs = check_expr(right, ctx)
        if _failed(rhs):
            return _invalid(Loc.from_node(ctx.path, left)), rhs
        return check_expr(left, ctx, expected=rhs.ty), rhs

    lhs = check_expr(left, ctx)
    if _failed(lhs):
        return lhs, _invalid(Loc.from_node(ctx.path, right))
    rhs = check_expr(right, ctx, expected=lhs.ty if right_is_literal else None)
    return lhs, rhs


def _check_binop(node: ast.BinOp, ctx: FuncCtx, expected: Ty | None) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)

    folded = fold_literal(node)
    if folded is not None:
        # A plain-Python literal spelling (`2**32`, `"a" * 33`), not chain
        # arithmetic -- see `fold_literal` and F.1.10.
        return _coerce_literal(folded, loc, ctx, expected)

    op = _BINARY_OPS.get(type(node.op))
    if op is None:
        if isinstance(node.op, ast.Div):
            _error(
                ctx,
                "SPT3006",
                loc,
                "`/` would produce a float",
                help="use // for truncating integer division",
            )
        else:
            spelling = _OMITTED_BINARY_OPS.get(type(node.op), type(node.op).__name__)
            _error(
                ctx,
                "SPT3005",
                loc,
                f"`{spelling}` is not part of serpent's arithmetic",
                help=(f"rewrite without `{spelling}`; serpent provides + - * // % and unary -"),
            )
        return _invalid(loc)

    for operand_node in (node.left, node.right):
        if _is_bool_literal(operand_node):
            _error(
                ctx,
                "SPT3003",
                loc,
                "a bool literal is not a chain integer operand",
                help="use the chain integer explicitly, e.g. U32(1), or compare with a Bool",
                notes=(_BOOL_AS_INT_NOTE,),
            )
            return _invalid(loc)

    lhs, rhs = _check_operand_pair(node.left, node.right, ctx)
    if _failed(lhs) or _failed(rhs):
        return _invalid(loc)

    for side in (lhs, rhs):
        if side.ty.tag in _TIME_TAGS:
            _error(
                ctx,
                "SPT3005",
                loc,
                f"{side.ty.render()} has no arithmetic at all",
                help=(
                    "convert explicitly through U64 -- t.to_u64() / Timepoint.from_u64(x) -- "
                    "and do the arithmetic there"
                ),
                notes=(_TIME_ALGEBRA_NOTE,),
            )
            return _invalid(loc)

    if lhs.ty != rhs.ty:
        _error(
            ctx,
            "SPT3003",
            loc,
            f"{lhs.ty.render()} and {rhs.ty.render()} are different chain types",
            help="convert one side explicitly; serpent never widens or narrows implicitly",
        )
        return _invalid(loc)

    if lhs.ty.tag not in _ARITH_TAGS:
        _error(
            ctx,
            "SPT3005",
            loc,
            f"`{op.value}` is not defined for {lhs.ty.render()}",
            help="arithmetic is defined for U32, I32, U64, I64, U128, and I128",
        )
        return _invalid(loc)

    return Binary(loc=loc, ty=lhs.ty, op=op, lhs=lhs, rhs=rhs)


def _check_unaryop(node: ast.UnaryOp, ctx: FuncCtx, expected: Ty | None) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)

    folded = fold_literal(node)
    if folded is not None:
        return _coerce_literal(folded, loc, ctx, expected)

    if isinstance(node.op, ast.Not):
        operand = check_expr(node.operand, ctx, expected=Ty.Bool)
        if _failed(operand):
            return _invalid(loc)
        if operand.ty.tag is not TyTag.BOOL:
            _error(
                ctx,
                "SPT3012",
                loc,
                f"`not` needs a Bool-typed operand, not {operand.ty.render()}",
                help=(
                    "write the explicit test, e.g. `x == U32(0)` or `len(v) == U32(0)`, "
                    "and negate that"
                ),
            )
            return _invalid(loc)
        return Unary(loc=loc, ty=Ty.Bool, op=UnaryOp.NOT, operand=operand)

    if isinstance(node.op, ast.USub):
        operand = check_expr(node.operand, ctx, expected=expected)
        if _failed(operand):
            return _invalid(loc)
        if operand.ty.tag in _TIME_TAGS:
            _error(
                ctx,
                "SPT3005",
                loc,
                f"{operand.ty.render()} has no arithmetic at all",
                help=(
                    "convert explicitly through U64 -- t.to_u64() / Duration.from_u64(x) -- "
                    "and do the arithmetic there"
                ),
                notes=(_TIME_ALGEBRA_NOTE,),
            )
            return _invalid(loc)
        if operand.ty.tag not in _ARITH_TAGS:
            _error(
                ctx,
                "SPT3005",
                loc,
                f"unary `-` is not defined for {operand.ty.render()}",
                help="arithmetic is defined for U32, I32, U64, I64, U128, and I128",
            )
            return _invalid(loc)
        return Unary(loc=loc, ty=operand.ty, op=UnaryOp.NEG, operand=operand)

    spelling = "+" if isinstance(node.op, ast.UAdd) else "~"
    _error(
        ctx,
        "SPT3007",
        loc,
        f"unary `{spelling}` is not supported",
        help=(
            "drop the unary `+`; it is a no-op"
            if spelling == "+"
            else "bitwise operators are not part of serpent's arithmetic"
        ),
    )
    return _invalid(loc)


# --- Compare ------------------------------------------------------------------


def _check_compare(node: ast.Compare, ctx: FuncCtx) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)

    if len(node.ops) > 1:
        _error(
            ctx,
            "SPT1010",
            loc,
            "a chained comparison evaluates its middle operand once, which has no sound lowering",
            help="split it: `a < b and b < c`",
        )
        return _invalid(loc)

    op_node = node.ops[0]
    op = _COMPARE_OPS.get(type(op_node))
    if op is None:
        if isinstance(op_node, (ast.Is, ast.IsNot)):
            spelling = "is" if isinstance(op_node, ast.Is) else "is not"
            _error(
                ctx,
                "SPT1012",
                loc,
                f"`{spelling}` compares object identity, which no host value has",
                help="compare values with ==",
            )
        else:
            spelling = "in" if isinstance(op_node, ast.In) else "not in"
            _error(
                ctx,
                "SPT1011",
                loc,
                f"`{spelling}` needs an iterator protocol the host does not provide",
                help="use Map.has(k), or Vec.first_index_of(v) for a Vec",
            )
        return _invalid(loc)

    left, right = node.left, node.comparators[0]

    # E13/T4: a raw str/bytes literal never coerces into a chain payload. At
    # tier 1 the comparison is silently `False` forever (A7: `__eq__` never
    # raises) -- exactly the bug S1 says to name rather than approximate.
    for literal_node, other_node in ((left, right), (right, left)):
        value = _literal_value(literal_node)
        if isinstance(value, (str, bytes)):
            other_ty = _peek_ty(other_node, ctx)
            if other_ty.tag is TyTag.INVALID:
                check_expr(other_node, ctx)  # report the real problem instead
                return _invalid(loc)
            _error(
                ctx,
                "SPT3016",
                loc,
                f"a raw {type(value).__name__} literal never equals a {other_ty.render()}",
                help=(f"compare against the chain value, e.g. {other_ty.render()}({value!r})"),
                notes=(_RAW_LITERAL_NOTE,),
            )
            return _invalid(loc)

    lhs, rhs = _check_operand_pair(left, right, ctx)
    if _failed(lhs) or _failed(rhs):
        return _invalid(loc)

    if lhs.ty != rhs.ty:
        _error(
            ctx,
            "SPT3003",
            loc,
            f"{lhs.ty.render()} and {rhs.ty.render()} are different chain types",
            help="convert one side explicitly; comparing foreign chain types is an error",
        )
        return _invalid(loc)

    return Compare(
        loc=loc,
        ty=Ty.Bool,
        op=op,
        lhs=lhs,
        rhs=rhs,
        via_obj_cmp=_via_obj_cmp(lhs.ty),
    )


def _via_obj_cmp(ty: Ty) -> bool:
    """F.1.2/T5: which comparisons MUST route through `obj_cmp` (`x.0`).

    Every HOST_OBJECT-repr type has no scalar form to compare at all. `Symbol`
    is the dangerous case and is included UNCONDITIONALLY even though a
    <= 9-character Symbol is an immediate: its `SymbolSmall` body packs 6-bit
    alphabet codes where `_` is 1 and `A` is 12, so a raw i64 compare of the
    packed Val flips `Symbol("_") < Symbol("A")` relative to the ASCII order
    tier 1 pins (`cases.py:symbol_underscore_vs_A_ascii_order`). That is the
    #2 silent divergence in the whole frontend; the mitigation is to never
    take the raw-compare shortcut.
    """
    return ty.tag is TyTag.SYMBOL or ty.repr_form is ReprForm.HOST_OBJECT


# --- BoolOp / IfExp -----------------------------------------------------------


def _check_boolop(node: ast.BoolOp, ctx: FuncCtx) -> IRExpr:
    """`and`/`or` over Bool operands only (E9).

    Python's `and`/`or` return an OPERAND, not a bool, so `U32(0) and U32(5)`
    is `U32(0)`: there is no sound single-type lowering for mixed operands,
    and no honest one even for same-typed ones (the result type would flip
    between `Bool` and `U32` depending on the operands). Restricting to Bool
    keeps the short-circuit semantics and the type both exact.
    """
    loc = Loc.from_node(ctx.path, node)
    operands: list[IRExpr] = []
    for value in node.values:
        operand = check_expr(value, ctx, expected=Ty.Bool)
        if _failed(operand):
            return _invalid(loc)
        if operand.ty.tag is not TyTag.BOOL:
            _error(
                ctx,
                "SPT3012",
                loc,
                f"{operand.ty.render()} is not a Bool-typed operand",
                help=(
                    "write an explicit comparison, e.g. `x > U32(0) and flag`; python's "
                    "and/or return an operand, not a bool, so there is no sound lowering"
                ),
            )
            return _invalid(loc)
        operands.append(operand)

    kind = BoolOpKind.AND if isinstance(node.op, ast.And) else BoolOpKind.OR
    return BoolOp(loc=loc, ty=Ty.Bool, op=kind, operands=tuple(operands))


def _check_ifexp(node: ast.IfExp, ctx: FuncCtx, expected: Ty | None) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)
    cond = check_condition(node.test, ctx)
    if _failed(cond):
        return _invalid(loc)

    then = check_expr(node.body, ctx, expected=expected)
    if _failed(then):
        return _invalid(loc)
    orelse = check_expr(node.orelse, ctx, expected=then.ty)
    if _failed(orelse):
        return _invalid(loc)

    if then.ty != orelse.ty:
        _error(
            ctx,
            "SPT3010",
            loc,
            f"the branches have different types: {then.ty.render()} and {orelse.ty.render()}",
        )
        return _invalid(loc)

    return IfExp(loc=loc, ty=then.ty, cond=cond, then=then, orelse=orelse)
