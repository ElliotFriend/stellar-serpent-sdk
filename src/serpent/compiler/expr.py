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

## `Subscript` (MJ-13) is owned HERE; the recognition tables are not

Task 7b took ownership of `Subscript` in this module rather than in
`recognize.py`, because `recognize.py` imports `check_expr` and the reverse
import would be a cycle. The four MJ-13 cases therefore live in
`_check_subscript` below, and the one host function they reach (`bytes_get`)
is named the same way `len()`'s already are (`_LEN_HOST_FN`) -- by name (B2),
never by export code.

## Internal calls (E8) ARE owned here (Task 8)

`double(env, x)` (a module-level helper) and `self._fee(env, x)` (a private
method) both lower to `InternalCall` in `_check_internal_call` below. The
signatures come from `FuncCtx.internal_sigs`, which `decls.check_declarations`
fills in: this module never resolves a callee's declaration itself, so a
broken helper is reported ONCE, against its own `def`, instead of once per call
site -- and `decls.py` can import `check_expr` (for a module constant's value)
without an import cycle.

## What this module does NOT own

Container/struct construction and their method tables, struct field reads (all
`recognize.py`, Task 7b) and the `Env` recognition table (Task 7a). Task 10
joined the two modules: `_check_attribute` and `_check_call` dispatch into
`recognize.recognize_attribute`/`recognize_call` through the two thin
`_recognize_*` wrappers, whose imports are function-LOCAL because
`recognize.py` imports `check_expr` from here at module scope and the reverse
top-level import would be a cycle. Two ordering rules are load-bearing at that
dispatch:

* the internal-call branches (`self.<name>(...)`, a module-level helper) stay
  AHEAD of it -- see the ordering note in `_check_call`;
* a shape neither module claims reaches `_unrecognized`, MJ-11's catch-all,
  so a real typo whose chain does not match (`env.storage(1).instance()...`)
  is a located diagnostic and never silence (F.2.5).
"""

from __future__ import annotations

import ast
import builtins
import operator
from collections.abc import Callable
from typing import Any, Final

from serpent.compiler import codes
from serpent.compiler.ctx import FuncCtx, InternalSig
from serpent.compiler.diagnostics import Loc
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
    InternalCall,
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

__all__ = [
    "NODE_KIND_CODES",
    "RECOGNIZED_BUILTINS",
    "check_condition",
    "check_expr",
    "fold_literal",
    "oracle_class",
]

#: `code -> message_intent`, so every diagnostic carries its registry row's own
#: wording (the convention `loader.py`/`types_.py`/`ctx.py` already follow).
_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

#: MJ-11's catch-all: any AST node kind no other row covers.
_FALLBACK_CODE = "SPT1037"

#: The builtins `_check_call` dispatches BY NAME, before any user-declared
#: function of the same name is looked up: `bool(x)` (D3's zero-test) and
#: `len(x)` (MJ-1's ruled scope). Exported because `decls.py` refuses a
#: module-level helper called `len` or `bool` -- such a helper could never be
#: reached -- and that check must read the dispatch's own list rather than
#: keeping a second copy of it (MJ-5).
RECOGNIZED_BUILTINS: Final[frozenset[str]] = frozenset({"bool", "len"})


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
_TIME_ALGEBRA_NOTE = "time algebra is deferred to M2 (D4/A17/E3)"
_ORACLE_SURPRISE_NOTE = (
    "the compiler validates a literal by constructing the tier-1 chain value; this "
    "constructor raised something other than the ValueError/TypeError serpent's error "
    "convention (A10) specifies, which is a tier-1 bug worth reporting -- but never a "
    "compiler traceback"
)
_ORDERING_STRICTNESS_NOTE = (
    "tier 1 raises TypeError for this ordering, and its val_cmp model is explicitly "
    "partial (A15), so the compiler must not invent an order the oracle cannot check "
    "(F.1.8)"
)
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

#: Every fixed-width chain integer -- the set SPT3003's "operands must share
#: the same chain-integer type" is actually a true statement about.
_CHAIN_INT_TAGS: Final[frozenset[TyTag]] = _NUMERIC_TAGS

#: ONE comparison family (D5): equality and ordering across `Bytes`/`Bytes32`/
#: `Bytes64`/`bytes_n(N)` are payload-based, and all share `_SCVAL_RANK` 13.
_BYTES_FAMILY_TAGS: Final[frozenset[TyTag]] = frozenset({TyTag.BYTES, TyTag.BYTES_N})

#: Types tier 1 can ORDER (`< <= > >=`), verified against the oracle:
#: numerics (Timepoint/Duration included -- D4 removes their arithmetic, not
#: their comparisons), `Bool`, `Symbol`, `String`, the `Bytes` family, and
#: `Address`. `Vec`/`Map`/struct/`Option` are absent because tier 1 raises
#: `TypeError` for their orderings; EQUALITY on those types stays supported.
_ORDERABLE_TAGS: Final[frozenset[TyTag]] = (
    _NUMERIC_TAGS
    | _BYTES_FAMILY_TAGS
    | frozenset({TyTag.BOOL, TyTag.SYMBOL, TyTag.STRING, TyTag.ADDRESS})
)

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

#: The one host function MJ-13's supported subscript reaches: `Bytes[i]` ->
#: `bytes_get` -> `U32` (SS C.4). Named here rather than in `recognize.py`'s
#: table for the import-cycle reason in the module docstring; the container
#: completeness assertion lists it as reached-from-`expr.py`, exactly as it
#: does for the `_LEN_HOST_FN` names above.
_BYTES_GET_HOST_FN: Final[str] = "bytes_get"

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

#: "a compile-time literal, but past the size caps below". Distinct from
#: `_MISSING` so the diagnostic can tell the honest story: `U32(2**4096)` is an
#: out-of-range LITERAL (SPT3004), not a misuse of `**` (SPT3005).
_TOO_LARGE: Final[object] = object()

#: Caps so a typo cannot turn constant folding into a denial of service:
#: `2 ** 10**9` would otherwise allocate until the process dies. U128's widest
#: literal needs 128 bits and the longest Symbol 32 characters, so these caps
#: are far above anything a contract can legitimately spell -- a literal that
#: trips one is reported, never silently mis-reported.
_MAX_FOLD_BITS: Final[int] = 4096
_MAX_FOLD_LENGTH: Final[int] = 1 << 16
#: A `**` exponent this large cannot produce anything under `_MAX_FOLD_BITS`
#: unless the base is 0, 1 or -1, so declining outright costs nothing real and
#: bounds the work even for those bases. It is the BIT cap, not a smaller
#: number, so `2**4000` (4001 bits) still folds.
_MAX_FOLD_EXPONENT: Final[int] = _MAX_FOLD_BITS

_FOLD_CAP_NOTE = (
    f"the compiler folds literal arithmetic up to {_MAX_FOLD_BITS} bits and "
    f"{_MAX_FOLD_LENGTH} characters/bytes; every chain type is far narrower than that "
    "(U128, the widest, is 128 bits)"
)


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
    if isinstance(value, bool) or value is _MISSING or value is _TOO_LARGE:
        return None
    if isinstance(value, (int, str, bytes)):
        return value
    return None


def _fold(node: ast.expr) -> object:
    """`_MISSING` (not a literal), `_TOO_LARGE` (a literal past the size caps),
    or the value.

    The two failure sentinels are deliberately DISTINCT: they produce
    different diagnostics. "Not a literal" means the ordinary operator and
    type rules apply (`U32(2) ** U32(3)` is the omitted-operator reject);
    "too large" is a fact about the LITERAL, and reporting it as an omitted
    operator would be dishonest -- `U32(2**4096)` is an out-of-range literal,
    not a misuse of `**`.
    """
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool):
            return _MISSING
        if isinstance(value, (int, str, bytes)):
            return _guard(value)
        return _MISSING
    if isinstance(node, ast.UnaryOp):
        operand = _fold(node.operand)
        if operand is _TOO_LARGE:
            return _TOO_LARGE
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
        if left is _TOO_LARGE or right is _TOO_LARGE:
            return _TOO_LARGE
        if left is _MISSING or right is _MISSING:
            return _MISSING
        return _guard(_apply_fold(node.op, left, right))
    return _MISSING


def _apply_fold(op: ast.operator, left: object, right: object) -> object:
    """Apply one plain-Python literal operator, declining anything unsafe.

    Every size check happens BEFORE the operation, never after: `_guard` sees
    the result, and by then a `2 ** 10**9` has already tried to allocate it.
    """
    if isinstance(op, ast.Pow):
        if not isinstance(right, int) or right < 0:
            return _MISSING
        if right > _MAX_FOLD_EXPONENT:
            return _TOO_LARGE
        # `bit_length(left**right)` is at least `(bit_length(left) - 1) * right
        # + 1`, so this declines only what provably exceeds the cap -- `2**4000`
        # (4001 bits) still folds, while `(2**512)**512` (262145 bits) does not.
        if isinstance(left, int) and (left.bit_length() - 1) * right + 1 > _MAX_FOLD_BITS:
            return _TOO_LARGE
    if isinstance(op, (ast.LShift, ast.RShift)):
        if not isinstance(right, int) or right < 0:
            return _MISSING
        if right > _MAX_FOLD_BITS:
            return _TOO_LARGE
    if isinstance(op, ast.Mult):
        # `"a" * 33` / `b"x" * 10`: cap the RESULT length before allocating it,
        # so a typo (or a nested `("a" * 65536) * 65536`) cannot ask for
        # gigabytes. Anything else (`"a" * "b"`) falls through to the handler's
        # TypeError.
        for sequence, count in ((left, right), (right, left)):
            if (
                isinstance(sequence, (str, bytes))
                and isinstance(count, int)
                and count > 0
                and len(sequence) * count > _MAX_FOLD_LENGTH
            ):
                return _TOO_LARGE
    handler = _FOLD_OPS.get(type(op))
    if handler is None:
        return _MISSING
    try:
        return handler(left, right)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError, MemoryError):
        return _MISSING


def _guard(value: object) -> object:
    """`_TOO_LARGE` for a literal past the size caps, else `value` unchanged."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value.bit_length() <= _MAX_FOLD_BITS else _TOO_LARGE
    if isinstance(value, (str, bytes)):
        return value if len(value) <= _MAX_FOLD_LENGTH else _TOO_LARGE
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
    """The compile-time literal `node` denotes, `_MISSING`, or `_TOO_LARGE`.

    Wider than `fold_literal`: a bare `True`/`False`/`None` Constant IS a
    literal for coercion purposes (MJ-12's `while True:`, and `None` for an
    `Option`), even though neither may be folded into arithmetic. A literal
    written out in full (a 100 KB string constant) is returned as-is -- the
    size caps exist to bound COMPUTATION, and the oracle then answers whether
    the target type can hold it.
    """
    if isinstance(node, ast.Constant):
        return node.value
    return _fold(node)


def _is_literal(node: ast.expr) -> bool:
    """Whether `node` is a compile-time literal AT ALL -- a too-large one
    included, because it is the literal path that owes it a diagnostic."""
    return _literal_value(node) is not _MISSING


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


def _recognize_call(node: ast.Call, ctx: FuncCtx) -> IRExpr | None:
    """`recognize.recognize_call`, reached through a function-LOCAL import.

    The import cannot be at module scope: `recognize.py` imports `check_expr`
    from THIS module at module scope, so importing it back would be a cycle.
    The two modules are joined at call time instead, which is exactly what the
    `TASK-7A`/`TASK-7B` markers this replaced were waiting for -- and the thin
    wrapper (rather than a bare `ModuleType` handle) keeps the signature
    checkable under `--strict`. `sys.modules` makes the lookup a dict hit after
    the first call.
    """
    from serpent.compiler.recognize import recognize_call

    return recognize_call(node, ctx)


def _recognize_attribute(node: ast.Attribute, ctx: FuncCtx) -> IRExpr | None:
    """`recognize.recognize_attribute` -- see `_recognize_call` on the import."""
    from serpent.compiler.recognize import recognize_attribute

    return recognize_attribute(node, ctx)


_UNRECOGNIZED_HELP = (
    "use a recognized surface: env.storage().<instance|persistent|temporary>()."
    "<set|get|has|del_|extend_ttl>(...), env.ledger().<timestamp|sequence>(), "
    "env.events().publish(topics, data), <Address>.require_auth(), a Vec/Map/Bytes "
    "method, a @contracttype field, or a chain-type constructor"
)


def _unrecognized(node: ast.expr, ctx: FuncCtx, loc: Loc, what: str) -> IRExpr:
    """A construct no recognition table claims and no SS B row describes.

    MJ-11's catch-all is its honest home: the SPT registry is frozen and no row
    says "shaped like an API call, but not one". The message names the shape so
    a real typo -- `env.storage(1).instance().set(...)`, whose intermediate
    `storage(1)` breaks the chain match -- gets a located diagnostic instead of
    silence.

    The `help` spells the recognized surfaces out rather than citing
    `docs/subset.md`: that page is GENERATED from `tests/must_reject/` (S14) and
    does not exist yet, and a diagnostic must not send an author to a file the
    repo does not have.
    """
    _error(
        ctx,
        _FALLBACK_CODE,
        loc,
        what,
        help=_UNRECOGNIZED_HELP,
    )
    return _invalid(loc)


def _unrecognized_attribute(node: ast.Attribute, ctx: FuncCtx, loc: Loc) -> IRExpr:
    return _unrecognized(
        node, ctx, loc, f"`.{node.attr}` is not a recognized attribute of that value"
    )


def _too_large(loc: Loc, ctx: FuncCtx) -> IRExpr:
    """Report a literal the folding caps declined (see `_TOO_LARGE`).

    Deliberately NOT the omitted-operator code: `U32(2**4096)` misuses no
    operator, it names a value nothing on chain can hold, so it belongs with
    every other out-of-range literal.
    """
    _error(
        ctx,
        "SPT3004",
        loc,
        "the literal is too large for the compiler to evaluate",
        notes=(_FOLD_CAP_NOTE,),
    )
    return _invalid(loc)


# --- literal coercion (S3) ----------------------------------------------------

#: Cap on a literal repr embedded in a diagnostic. A contract may hold a
#: 100 KB `Bytes` literal; quoting it back in a `help:` line would be a
#: 131 KB error message.
_MAX_REPR_CHARS: Final[int] = 120


def _short_repr(value: object) -> str:
    """`repr(value)`, truncated with an ellipsis past `_MAX_REPR_CHARS`."""
    text = repr(value)
    if len(text) <= _MAX_REPR_CHARS:
        return text
    return f"{text[:_MAX_REPR_CHARS]}... ({len(text)} chars)"


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
        shown = _short_repr(value)
        return f"wrap it in a chain type, e.g. U32({shown}) or I128({shown})"
    if isinstance(value, str):
        shown = _short_repr(value)
        return f"wrap it in a chain type, e.g. Symbol({shown}) or String({shown})"
    if isinstance(value, bytes):
        return f"wrap it in a chain type, e.g. Bytes({_short_repr(value)})"
    if value is None:
        return "None is only a value where the type is X | None"
    return "wrap the literal in one of serpent's chain types"


def oracle_class(ty: Ty) -> type | None:
    """The tier-1 class that VALIDATES (and, for `recognize.py`'s MJ-15 key
    ordering, REPRESENTS) a literal of type `ty` -- `None` for a `Ty` with no
    single literal form (`Vec`/`Map`/`Struct`/`Option`/`Void`).

    Public because Task 7b's `MakeMap` literal-key pre-sort has to build the
    same tier-1 values this module builds to validate a literal: MJ-15 orders
    keys by the ORACLE's `val_cmp`, and re-deriving the `Ty -> class` mapping
    there would be a second place for the same truth (B8).
    """
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

    The final clause makes the delegation safe BY CONSTRUCTION: whatever a
    chain constructor raises -- today `ValueError`/`TypeError` by A10's
    convention, tomorrow whatever a new type's validator adds -- becomes a
    located diagnostic rather than a traceback escaping the compiler (F.2.5).
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
    except Exception as exc:  # noqa: BLE001 -- delegation safety, see above
        _error(
            ctx,
            "SPT3004",
            loc,
            f"{cls.__name__}() rejected the literal: {type(exc).__name__}: {exc}",
            notes=(_ORACLE_SURPRISE_NOTE,),
        )
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

    cls = oracle_class(expected)
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
        if base.id == "self":
            # `self.<attr>` is a STATE read, which is what SS C.3's scope rule
            # and SPT2002 are about -- never a deferred surface. (A private
            # METHOD call, `self._helper(...)`, is E8-supported and is routed
            # to Task 8 from `_check_call`, which never reaches this function.)
            _error(
                ctx,
                "SPT2002",
                loc,
                f"`self.{node.attr}` is not a value: a @contract class has no attributes",
                help=(
                    "read and write contract state through env.storage(); call a private "
                    "method as self._name(...)"
                ),
            )
            return _invalid(loc)
        obj = ctx.loaded.namespace.get(base.id)
        if isinstance(obj, type):
            metadata = vars(obj).get(_METADATA_ATTR)
            if isinstance(metadata, dict) and metadata.get("kind") == "error_enum":
                return _check_error_member(node, ctx, base.id, metadata, loc)

    if node.attr in _INTROSPECTION_PROPERTIES:
        # ONE pass, through the real sink: a scratch-sink peek followed by a
        # re-check doubles the work at every nesting level, which is
        # exponential in the depth of an attribute chain.
        base_ir = check_expr(base, ctx)
        if _failed(base_ir):
            return _invalid(loc)
        # A struct field can legitimately be named `value` -- that is a field
        # read, which is Task 7b's; only a real chain-type base is this row.
        if base_ir.ty.tag is not TyTag.STRUCT:
            _error(
                ctx,
                "SPT1016",
                loc,
                f"`.{node.attr}` on {base_ir.ty.render()} is tier-1 introspection with no "
                "host equivalent",
            )
            return _invalid(loc)

    # Struct field reads (`bal.amount`) and the bare `Env` surface
    # (`env.storage` with no call) are `recognize.py`'s (Tasks 7a/7b), wired
    # here by Task 10.
    recognized = _recognize_attribute(node, ctx)
    if recognized is not None:
        return recognized
    return _unrecognized_attribute(node, ctx, loc)


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


# --- Subscript (MJ-13) -------------------------------------------------------


def _check_subscript(node: ast.Subscript, ctx: FuncCtx) -> IRExpr:
    """MJ-13's four cases, in the order their diagnostics are most honest in.

    1. **The annotation-only generic form in a value position** (`Vec[U32]`):
       `SPT3014`. In an ANNOTATION this shape never reaches here at all -- it
       is resolved by Task 4's `resolve_annotation`, which is the only place
       that knows the position is an annotation.
    2. **A slice** (`b[a:b]`, `v[a:b]`): `SPT1013` pointing at the
       `.slice(lo, hi)` method (E18). Reported from the SHAPE alone, without
       checking the receiver, so the rewrite is named even when the receiver
       itself is something the checker would reject for another reason.
    3. **A negative LITERAL index** (`b[-1]`): `SPT3011` (D6). Only a literal
       -- F.1.7 is explicit that C must not claim to have statically proved
       anything about a COMPUTED negative index, which is a runtime trap on
       both tiers.
    4. **`Bytes[i]`/`BytesN[i]`**: the one supported subscript, lowering to
       `bytes_get` -> `U32` (SS C.4's `Bytes` ops row). `Vec`/`Map` have no
       `__getitem__` at tier 1 at all, so their subscript is MJ-11's catch-all
       with `.get(...)` named in `help`.
    """
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

    if isinstance(node.slice, ast.Slice):
        _error(
            ctx,
            "SPT1013",
            loc,
            "a sub-range is taken with the .slice(lo, hi) method, never a subscript slice",
            help="take the sub-range with .slice(lo, hi), e.g. v.slice(U32(0), U32(2))",
        )
        return _invalid(loc)

    index_literal = _literal_value(node.slice)
    if isinstance(index_literal, int) and not isinstance(index_literal, bool) and index_literal < 0:
        _error(
            ctx,
            "SPT3011",
            loc,
            f"`{index_literal}` is not an index the host can take (it indexes with a u32)",
            help="index from the front, or compute the index explicitly, e.g. len(b) - U32(1)",
        )
        return _invalid(loc)

    receiver = check_expr(base, ctx)
    if _failed(receiver):
        return _invalid(loc)

    if receiver.ty.tag in _BYTES_FAMILY_TAGS:
        index = check_expr(node.slice, ctx, expected=Ty.U32)
        if _failed(index):
            return _invalid(loc)
        if index.ty != Ty.U32:
            _error(
                ctx,
                "SPT3018",
                loc,
                f"a {receiver.ty.render()} index must be U32, not {index.ty.render()}",
            )
            return _invalid(loc)
        return HostCall(loc=loc, ty=Ty.U32, fn_name=_BYTES_GET_HOST_FN, args=(receiver, index))

    if receiver.ty.tag is TyTag.VEC:
        _error(
            ctx,
            _FALLBACK_CODE,
            loc,
            f"a {receiver.ty.render()} element is not read with a subscript",
            help="read an element with .get(i), e.g. v.get(U32(0))",
        )
        return _invalid(loc)

    if receiver.ty.tag is TyTag.MAP:
        _error(
            ctx,
            _FALLBACK_CODE,
            loc,
            f"a {receiver.ty.render()} value is not read with a subscript",
            help="read a value with .get(k), e.g. m.get(Symbol('total'))",
        )
        return _invalid(loc)

    _error(
        ctx,
        _FALLBACK_CODE,
        loc,
        f"{receiver.ty.render()} cannot be subscripted",
        help="subscripting is defined for Bytes only; containers use .get(...)",
    )
    return _invalid(loc)


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
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and func.value.id == "self":
            # A private method call is E8-SUPPORTED (an InternalCall on a
            # non-exported wasm function) -- unlike a `self.<attr>` read,
            # which SPT2002 rejects outright in `_check_attribute`.
            #
            # WIRING ORDER (carried review finding, Task 7b round 1): this
            # branch must stay BEFORE any dispatch into
            # `recognize.recognize_call`. `get`/`set`/`has`/`del_`/`slice` are
            # CONTAINER method names, so a `self.get(...)` reaching the
            # container table first would be checked as a container surface
            # with `self` as its receiver and reported as an unrelated
            # receiver error instead of E8's own rule. Pinned by
            # `test_decls.py::test_calling_an_exported_method_through_self_is_
            # rejected`, which goes through `check_expr` -- the only entry a
            # call site has -- so re-ordering the dispatch fails that test.
            return _check_self_call(node, ctx, loc, func.attr)
        # `env.storage().get(...)`, `addr.require_auth()`, container/struct
        # methods, `Event(...).publish(env)` (E12) -- all `recognize.py`'s.
        recognized = _recognize_call(node, ctx)
        if recognized is not None:
            return recognized
        return _unrecognized(node, ctx, loc, f"`.{func.attr}(...)` is not a recognized method call")
    if not isinstance(func, ast.Name):
        return _unrecognized(node, ctx, loc, "a call of a computed value is not supported")

    name = func.id
    if name == "bool":  # RECOGNIZED_BUILTINS, dispatched by name
        return _check_bool_call(node, ctx, loc)
    if name == "len":  # RECOGNIZED_BUILTINS, dispatched by name
        return _check_len_call(node, ctx, loc)

    obj = ctx.loaded.namespace.get(name)
    if obj is bytes_n:
        # `bytes_n(N)` is resolved in ANNOTATION position, where the hybrid
        # frontend has already evaluated it into the generated fixed-length
        # subclass (A16/E20). In a VALUE position it is the annotation-only
        # form SPT3014 names -- not an undefined name, which is what the
        # generic fall-through below would have claimed (fix round 1, M-8).
        _error(
            ctx,
            "SPT3014",
            loc,
            f"`{name}(...)` is the annotation-only fixed-length Bytes form",
            help=f"annotate with it (`x: {name}(32)`) and construct the value with Bytes(...)",
        )
        return _invalid(loc)
    if obj is Vec or obj is Map:
        # `Vec(T[, items])` / `Map(K, V[, pairs])` (D2/A13, MJ-15).
        recognized = _recognize_call(node, ctx)
        assert recognized is not None, "recognize_call always claims Vec/Map construction"
        return recognized
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
            if kind == "struct":
                # Struct construction, kwargs only (P7).
                recognized = _recognize_call(node, ctx)
                assert recognized is not None, "recognize_call always claims struct construction"
                return recognized
            # An `@contract` class, or a `@contractevent` construction that is
            # not immediately `.publish(env)`-ed: neither is a value. The
            # publish form itself is DESUGARED by `recognize_call` (M1-E Task
            # 6), and construction-and-publish in one expression is exactly the
            # supported shape -- an event instance never becomes a local.
            return _unrecognized(
                node, ctx, loc, f"`{name}(...)` is a {kind} class, which is not a value"
            )
        return _check_chain_constructor(node, ctx, loc, name, obj)

    if any(helper.name == name for helper in ctx.loaded.helpers):
        # A module-level helper: an InternalCall on a non-exported wasm
        # function (E8). Reached before the builtin/undefined-name paths, and
        # -- like the `self.` branch above -- before any container dispatch a
        # later task adds, so a helper named `get` is a helper.
        sig = ctx.internal_sigs.get(name)
        if sig is None:
            # The helper exists but has no signature: its own declaration was
            # rejected and that diagnostic is already in the sink (or this
            # `FuncCtx` was built with no declaration table at all -- see
            # `FuncCtx.internal_sigs`). A second diagnostic here would be a
            # cascade.
            return _invalid(loc)
        return _check_internal_call(node, ctx, loc, sig)

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


def _check_self_call(node: ast.Call, ctx: FuncCtx, loc: Loc, attr: str) -> IRExpr:
    """`self.<attr>(...)`: a private-method InternalCall, or a reject (E8).

    E8 (b) admits module-level helpers and PRIVATE (underscore-prefixed)
    methods as internal functions. An EXPORT called through `self` is refused
    on purpose: the host is what invokes an export (with its own ABI prologue
    and its own frame), so `self.transfer(...)` would either duplicate that
    prologue or silently become a different function than the one the spec
    describes. Naming the rewrite -- extract the shared step -- is the useful
    answer, and it is the answer this diagnostic gives.

    `self` also has to EXIST: a module-level helper's body has no `self` at
    all, so `self._fee(...)` there is a `NameError` at tier 1 while the
    compiled internal call would work perfectly on chain -- an
    oracle-unrunnable accept (A18), and the one shape of it this dispatch could
    have introduced. Availability comes from `ctx.has_self`, the IDENTITY of
    the declaration being checked (`decls.FuncSig.has_self`) -- never from a
    name comparison: a module-level helper may share a name with an export
    (spec Sec.2 nearly writes exactly that, a `balance` helper beside a
    `balance` export), and asking "is `ctx.fn_name` one of the class's method
    names" would call that helper a method and let the unrunnable call through
    (fix round 1, I-1).
    """
    if not ctx.has_self:
        _error(
            ctx,
            "SPT2001",
            loc,
            f"`self` is not defined in `{ctx.fn_name}`",
            help=(
                "a module-level function has no `self`; call another module-level helper, "
                "or move the step into one"
            ),
            notes=(
                (
                    "`self` exists only inside a method of the @contract class; "
                    f"`{ctx.fn_name}` is compiled as a non-method function "
                    f"(kind: {ctx.fn_kind.name})"
                ),
            ),
        )
        return _invalid(loc)

    methods = _contract_method_names(ctx)
    sig = ctx.internal_sigs.get(f"self.{attr}")
    if sig is not None:
        return _check_internal_call(node, ctx, loc, sig)

    if attr in methods and not attr.startswith("_"):
        _error(
            ctx,
            _FALLBACK_CODE,
            loc,
            f"`self.{attr}(...)` calls an exported method, which is not supported",
            help=(
                "move the shared step into a module-level helper or a private "
                f"`_`-prefixed method and call that from both, e.g. `self._{attr}(env, ...)`"
            ),
            notes=(
                (
                    "an export is invoked by the host, with its own ABI prologue; only "
                    "module-level helpers and private methods compile to internal calls (E8)"
                ),
            ),
        )
        return _invalid(loc)
    if attr in methods:
        # A private method with no signature: its own declaration was rejected
        # (or this `FuncCtx` carries no declaration table -- see
        # `FuncCtx.internal_sigs`), so the diagnostic is already in the sink.
        return _invalid(loc)
    _error(
        ctx,
        "SPT2001",
        loc,
        f"`self.{attr}` is not a method of this contract",
        help=(
            f"declare `_{attr.lstrip('_')}` as a private method on the @contract class, or "
            "call a module-level helper"
        ),
    )
    return _invalid(loc)


def _contract_method_names(ctx: FuncCtx) -> frozenset[str]:
    """Every method the `@contract` class declares, from the AST view -- which
    is the view that still has the PRIVATE methods (`_serpent_type_` records
    exports only, since the decorator skips underscore names)."""
    node = ctx.loaded.contract_node
    if node is None:
        return frozenset()
    return frozenset(
        member.name
        for member in node.body
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
    )


def _check_internal_call(node: ast.Call, ctx: FuncCtx, loc: Loc, sig: InternalSig) -> IRExpr:
    """One call to a module-level helper or a private method (E8).

    Three rules the shape has to satisfy, in the order a reader would ask
    them:

    1. **Positional only.** An internal call compiles to a wasm `call`, whose
       arguments are positional; the recognized-API keyword forms
       (`v.get(index=...)`) exist because those rows pin tier-1 parameter
       NAMES, which an author-declared helper has no equivalent of.
    2. **`env` is passed but never compiled.** A helper that declares
       `env: Env` is called `double(env, x)` -- the same spelling the host
       surface uses everywhere else in the compiler -- and that argument
       contributes NO IR node, because there is no `Env` value on chain
       (`FuncIR.params` drops it on the callee side for the same reason).
    3. **Every other argument is a value of the declared parameter type**,
       with literal coercion in that position (S3) and strict type equality
       after it -- the same rule `stmt.py` applies at a `return` and at an
       annotated local, which are the other two declared positions.

    Finally, every container argument is an ESCAPE (E11): a callee can embed a
    passed `Vec`/`Map` in a container of its own, and C classifies
    conservatively rather than inspecting the callee -- so the local loses
    ownership and a later `own.push_back(x)` on it is a reject instead of a
    silent divergence from tier 1.
    """
    if node.keywords:
        keyword = node.keywords[0].arg
        _error(
            ctx,
            "SPT1035",
            loc,
            f"`{sig.surface}(...)` is called positionally (got `{keyword}=`)",
            help=f"pass the arguments positionally, e.g. `{sig.render()}`",
        )
        return _invalid(loc)

    args = list(node.args)
    expected = len(sig.params) + (1 if sig.takes_env else 0)
    if len(args) != expected:
        _error(
            ctx,
            "SPT3020",
            loc,
            f"`{sig.render()}` takes {expected} argument(s), got {len(args)}",
            help=f"pass every argument, positionally: `{sig.render()}`",
        )
        return _invalid(loc)

    if sig.takes_env:
        first = args.pop(0)
        if not (isinstance(first, ast.Name) and first.id == "env"):
            # SPT1037 (MJ-11's catch-all), NOT SPT1038: that row is "env API
            # used with an unsupported call shape" -- the `env.<...>` surface
            # SS C.4 recognizes -- and this is a user-declared function that
            # happens to take the host handle first (fix round 1, I-3).
            _error(
                ctx,
                _FALLBACK_CODE,
                loc,
                f"`{sig.surface}(...)` takes the host handle `env` as its first argument",
                help=f"pass `env` through: `{sig.render()}`",
            )
            return _invalid(loc)

    checked: list[IRExpr] = []
    failed = False
    for (param_name, param_ty, _param_loc), arg in zip(sig.params, args, strict=True):
        value = check_expr(arg, ctx, expected=param_ty)
        if _failed(value):
            failed = True
            continue
        if value.ty != param_ty:
            _error(
                ctx,
                "SPT3018",
                Loc.from_node(ctx.path, arg),
                f"`{sig.surface}` takes {param_ty.render()} for `{param_name}`, "
                f"but this value is {value.ty.render()}",
            )
            failed = True
            continue
        checked.append(value)

    # E11/E8: a container handle passed to a callee may be stored by it.
    # Marked BEFORE the failure return, the way `recognize_mutation` marks a
    # mutation's arguments: the conservative answer must win even when the call
    # as a whole did not check out.
    ctx.alias_sets.mark_escapes(checked)
    if failed:
        return _invalid(loc)
    return InternalCall(loc=loc, ty=sig.ret, fn_name=sig.fn_name, args=tuple(checked))


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
            "SPT3020",
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
    if value is _TOO_LARGE:
        # A literal argument the folding caps declined: the honest story is the
        # literal's size, not the operator its spelling happened to use.
        return _too_large(loc, ctx)
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


def _check_operand_pair(
    left: ast.expr, right: ast.expr, ctx: FuncCtx, *, expected: Ty | None = None
) -> tuple[IRExpr, IRExpr]:
    """Check two operands so a literal takes the OTHER side's type (A6).

    Symmetric in which side holds the literal: the non-literal side is checked
    FIRST and its type becomes the literal's `expected`, so `a + 10` and
    `10 + a` -- and `a if flag else 5` and `5 if flag else a` -- all behave the
    same way. When the non-literal side fails, the literal side is skipped
    entirely: a bare literal whose partner already errored would otherwise add
    a second, purely cascaded diagnostic. `expected` is the fallback when BOTH
    sides are literals (an `IfExp` in an annotated position).
    """
    left_is_literal = _is_literal(left)
    right_is_literal = _is_literal(right)

    if left_is_literal and not right_is_literal:
        rhs = check_expr(right, ctx)
        if _failed(rhs):
            return _invalid(Loc.from_node(ctx.path, left)), rhs
        return check_expr(left, ctx, expected=rhs.ty), rhs

    lhs = check_expr(left, ctx, expected=expected if left_is_literal else None)
    if _failed(lhs):
        return lhs, _invalid(Loc.from_node(ctx.path, right))
    rhs = check_expr(right, ctx, expected=lhs.ty if right_is_literal else None)
    return lhs, rhs


def _check_binop(node: ast.BinOp, ctx: FuncCtx, expected: Ty | None) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)

    folded = _fold(node)
    if folded is _TOO_LARGE:
        return _too_large(loc, ctx)
    if folded is not _MISSING:
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

    folded = _fold(node)
    if folded is _TOO_LARGE:
        return _too_large(loc, ctx)
    if folded is not _MISSING:
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
    #
    # Detected SYNTACTICALLY first (no walk), then the OTHER side is checked
    # exactly once, through the real sink. An earlier version peeked with a
    # scratch sink and then re-checked, which doubles the work at every
    # nesting level -- exponential in the depth of nested comparisons.
    raw_literal = _raw_payload_literal(left, right)
    if raw_literal is not None:
        value, other_node = raw_literal
        other = check_expr(other_node, ctx)
        if _failed(other):
            return _invalid(loc)  # the real problem is already reported
        _error(
            ctx,
            "SPT3016",
            loc,
            f"a raw {type(value).__name__} literal never equals a {other.ty.render()}",
            help=(
                f"compare against the chain value, e.g. {other.ty.render()}({_short_repr(value)})"
            ),
            notes=(_RAW_LITERAL_NOTE,),
        )
        return _invalid(loc)

    lhs, rhs = _check_operand_pair(left, right, ctx)
    if _failed(lhs) or _failed(rhs):
        return _invalid(loc)

    if not _comparable(lhs.ty, rhs.ty):
        both_integers = lhs.ty.tag in _CHAIN_INT_TAGS and rhs.ty.tag in _CHAIN_INT_TAGS
        _error(
            ctx,
            # SPT3003 states the rule for chain INTEGERS ("operands must share
            # the same chain-integer type"), which is exactly T1's cross-width
            # and cross-signedness case. For anything else that sentence would
            # be wrong in kind -- `Symbol == U32` is not an integer-width
            # problem -- so the generic type-mismatch row carries it instead.
            "SPT3003" if both_integers else "SPT3018",
            loc,
            f"{lhs.ty.render()} and {rhs.ty.render()} cannot be compared",
            help="convert one side explicitly; comparing foreign chain types is an error",
        )
        return _invalid(loc)

    if op not in (CompareOp.EQ, CompareOp.NE) and lhs.ty.tag not in _ORDERABLE_TAGS:
        # F.1.8: reproduce tier 1's STRICTNESS, not the host's permissiveness.
        # `obj_cmp` would happily order two MapObjects on chain, but tier 1
        # raises `TypeError: '<' not supported between instances of 'Vec'`, and
        # a compiler that accepted it would put an unverifiable order on chain
        # (A15: val_cmp is an explicitly PARTIAL model of obj_cmp). Equality on
        # these types stays supported -- tier 1 answers it.
        _error(
            ctx,
            "SPT3005",
            loc,
            f"`{op.value}` is not defined for {lhs.ty.render()}",
            help=(
                "compare with == or != instead; ordering is defined for the numeric "
                "types, Bool, Symbol, String, Bytes, and Address"
            ),
            notes=(_ORDERING_STRICTNESS_NOTE,),
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


def _raw_payload_literal(left: ast.expr, right: ast.expr) -> tuple[str | bytes, ast.expr] | None:
    """`(the raw literal, the other operand)` if exactly one side is a raw
    `str`/`bytes` literal (E13), else `None`. Purely syntactic -- no walk."""
    for literal_node, other_node in ((right, left), (left, right)):
        value = _literal_value(literal_node)
        if isinstance(value, (str, bytes)):
            return value, other_node
    return None


def _comparable(lhs: Ty, rhs: Ty) -> bool:
    """Whether two operand types may be compared at all.

    Identical types always may. The `Bytes` family is ONE comparison family
    across `Bytes`/`Bytes32`/`Bytes64`/`bytes_n(N)` (D5: equality and ordering
    are payload-based, and all of them share `_SCVAL_RANK` 13) -- fixed-length-
    ness is an AUTHORING constraint, so `Bytes32(p) == Bytes(p)` is `True` at
    tier 1 (`cases.py:bytes32_equals_bytes_same_payload`, a `kind="value"`
    case) and must compile.

    Note the deliberate ASYMMETRY this does NOT touch (F.1.8): a Vec's own
    lookups stay strict, so `Vec(Bytes32).first_index_of(Bytes(p))` remains a
    reject even though the host would find the element. That is Task 7b's
    surface; this function is only about the comparison operators.
    """
    if lhs == rhs:
        return True
    return lhs.tag in _BYTES_FAMILY_TAGS and rhs.tag in _BYTES_FAMILY_TAGS


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

    An `Option` is `True` for a structural reason of its own: one side may be
    the immediate `Void` tag while the other is an object handle, so there is
    no single scalar comparison that covers both possibilities -- whatever the
    wrapped type is.
    """
    if ty.tag is TyTag.OPTION:
        return True
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

    # Symmetric in which arm holds the literal (the same rule as an operator
    # pair, A6): `a if flag else 5` and `5 if flag else a` both take the
    # non-literal arm's type, and `expected` covers the both-literal case.
    then, orelse = _check_operand_pair(node.body, node.orelse, ctx, expected=expected)
    if _failed(then) or _failed(orelse):
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
