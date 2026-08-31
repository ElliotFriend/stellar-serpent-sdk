"""The complete serpent IR: expressions, statements and declarations.

This module is dossier SS C.2's node inventory, in full and in one place
(docs/superpowers/specs/2026-08-27-m1c-inputs-dossier.md). Task 5 lands ALL of
it -- statements and declarations included, even though Task 5 itself only
builds expression nodes -- because Tasks 6 through 10 must IMPORT these nodes,
never redefine them. Five slightly-different `If` nodes across five tasks is
exactly the failure mode this file exists to prevent.

## Design principles carried from the dossier

* **One thin `HostCall`** (SS C.1). Every `Env`/storage/auth/event/container
  host operation lowers to `HostCall(fn_name, args)` -- bindings are looked up
  BY NAME (B2), never by export code, so the protocol floor (B4/S18) is a
  one-line IR walk: `declared_protocol({n.fn_name for n in walk(ir) if
  isinstance(n, HostCall)}, requested)`. Sub-plan D special-cases only control
  flow, literal pooling/data layout, i128/u128 arithmetic, overflow checks, ABI
  prologues, and the raw-scalar immediates.
* **Every node carries a mandatory `Loc`** (P2, SS C.3): there is no synthetic
  `(1, 0)` anywhere in the compiler, so every diagnostic and every future
  emitter error can point at real source.
* **Every EXPRESSION carries a `ty: Ty`** (SS C.2). A node whose checking
  failed carries `Ty.Invalid` (minor 13's sentinel), which is how a checker
  keeps walking after reporting to the sink without cascading.
* **Frozen dataclasses**, `kw_only=True`. Frozen buys structural equality and
  hashing (golden IR snapshots, F.2.10, are then plain `==` diffs) and makes
  the tree safe to share; `kw_only` keeps construction self-documenting and
  immune to the field-ordering hazard that inheriting `loc`/`ty` from a base
  class would otherwise create.
* **The decisions C owns are RECORDED in the node, not left to D** (R4): the
  `via_obj_cmp` flag on `Compare` (F.1.2/T5), the pre-sorted field order on
  `MakeStruct` (P7), `all_static` on `MakeVec`/`MakeMap` (MJ-15), and the
  explicit `IsZero` truthiness node (D3).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum, auto

from serpent.compiler.diagnostics import Loc
from serpent.compiler.types_ import Ty

__all__ = [
    "Binary",
    "BinaryOp",
    "BoolOp",
    "BoolOpKind",
    "Break",
    "Compare",
    "CompareOp",
    "Const",
    "ConstDecl",
    "ConstRef",
    "Continue",
    "ContractIR",
    "Convert",
    "ErrorEnumDecl",
    "ErrorVal",
    "Eval",
    "EventDecl",
    "FieldGet",
    "FuncIR",
    "FuncKind",
    "HostCall",
    "IRDecl",
    "IRExpr",
    "IRNode",
    "IRStmt",
    "If",
    "IfExp",
    "InternalCall",
    "IsZero",
    "LetLocal",
    "LocalRef",
    "MakeMap",
    "MakeStruct",
    "MakeTopics",
    "MakeVec",
    "ModuleIR",
    "Nop",
    "ParamRef",
    "Raise",
    "RawScalar",
    "RawScalarKind",
    "Return",
    "SetLocal",
    "StructDecl",
    "Unary",
    "UnaryOp",
    "While",
    "walk",
]


# --- operators ---------------------------------------------------------------


class BinaryOp(Enum):
    """The five arithmetic operators A4's checked-arithmetic contract covers.

    `**`, `@`, `&`, `|`, `^`, `<<`, `>>` and `/` are deliberately ABSENT: they
    are compile rejects (A5/D2, SS B.2), so there is no IR node kind for them
    at all -- an omitted operator cannot reach sub-plan D even by mistake.
    Each value is the Python source spelling, for diagnostics.
    """

    ADD = "+"
    SUB = "-"
    MUL = "*"
    FLOORDIV = "//"
    MOD = "%"


class UnaryOp(Enum):
    """Unary `-` (overflow-checked, A4) and `not` (Bool-only, E9).

    Unary `+` and `~` are compile rejects (SS B.2) and have no node kind.
    """

    NEG = "-"
    NOT = "not"


class CompareOp(Enum):
    """The six single comparison operators. Chained comparison is a reject."""

    EQ = "=="
    NE = "!="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="


class BoolOpKind(Enum):
    """`and` / `or`, restricted to Bool operands (E9)."""

    AND = "and"
    OR = "or"


class RawScalarKind(Enum):
    """Which `_scalars.py` table a `RawScalar` immediate came from (B6)."""

    STORAGE_TYPE = auto()
    CONTRACT_TTL_EXTENSION = auto()


class FuncKind(Enum):
    """`FuncIR`'s role: an exported method, `__constructor`, or an internal
    (non-exported) WASM function -- a module-level helper or private method
    (E8)."""

    EXPORT = auto()
    CONSTRUCTOR = auto()
    INTERNAL = auto()


# --- bases -------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class IRNode:
    """Every IR node carries a mandatory full-span `Loc` (P2, E5)."""

    loc: Loc


@dataclass(frozen=True, kw_only=True)
class IRExpr(IRNode):
    """Every IR expression additionally carries its resolved `Ty` (SS C.2).

    `ty == Ty.Invalid` means "this subtree failed to check and the diagnostic
    is already in the sink" (minor 13) -- never a real type. A consumer that
    sees `Ty.Invalid` must stay silent rather than reporting a second,
    cascaded error.
    """

    ty: Ty


@dataclass(frozen=True, kw_only=True)
class IRStmt(IRNode):
    """Every IR statement carries a `Loc` and nothing else in common."""


@dataclass(frozen=True, kw_only=True)
class IRDecl(IRNode):
    """A module-level declaration (struct, error enum, event, constant)."""


# --- expressions -------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Const(IRExpr):
    """One unified literal node; POOLING IS SUB-PLAN D'S DECISION (SS C.2).

    `py_value` is the plain Python value the source spelled -- `int`, `str`,
    `bytes`, `bool`, or `None` for an `Option`'s empty case -- already
    validated and bounds-checked against `ty` at compile time (S3). It is
    NEVER a `serpent.types` instance: the checker builds tier-1 instances only
    transiently, to reuse their validators, and keeps the raw literal here.

    `Const(ty=Ty.Invalid, py_value=None)` is the checker's error placeholder
    (minor 13): a reported failure returns one so the walk can continue.
    """

    py_value: object


@dataclass(frozen=True, kw_only=True)
class ParamRef(IRExpr):
    """A read of a function parameter, by its index in `FuncCtx.params` --
    `self` and a leading `env: Env` already dropped (SS C.3)."""

    index: int
    name: str


@dataclass(frozen=True, kw_only=True)
class LocalRef(IRExpr):
    """A read of a local, by its flat slot number (SS C.3's `SlotTable`)."""

    slot: int
    name: str


@dataclass(frozen=True, kw_only=True)
class ConstRef(IRExpr):
    """A read of a module-level chain constant (P5)."""

    name: str


@dataclass(frozen=True, kw_only=True)
class Unary(IRExpr):
    """`-operand` (overflow-checked per A4) or `not operand` (Bool-only)."""

    op: UnaryOp
    operand: IRExpr


@dataclass(frozen=True, kw_only=True)
class Binary(IRExpr):
    """`lhs <op> rhs` under A4's checked-arithmetic contract.

    Both operands always share `ty` (the checker rejects cross-width and
    cross-signedness statically, T1), and NO constant folding has happened:
    F.1.10 is explicit that `I32(2**31 - 1) + I32(1)` must survive to runtime
    as an `ArithmeticOverflow`, not become a compile error.
    """

    op: BinaryOp
    lhs: IRExpr
    rhs: IRExpr


@dataclass(frozen=True, kw_only=True)
class Compare(IRExpr):
    """`lhs <op> rhs`, always `ty == Ty.Bool`.

    `via_obj_cmp` is the F.1.2/T5 divergence guard, decided HERE and recorded
    so sub-plan D cannot re-derive it wrongly: `True` means lower through
    `obj_cmp` (`x.0`, whose `val_typed_ret` is `False` -- a raw i64), `False`
    means a direct scalar compare on the narrowed type. It is `True` for every
    HOST_OBJECT-repr type AND for `Symbol`, whose small form is an immediate
    but whose packed 6-bit alphabet codes order DIFFERENTLY from the raw ASCII
    bytes tier 1 pins (`Symbol("_") < Symbol("A")` flips).
    """

    op: CompareOp
    lhs: IRExpr
    rhs: IRExpr
    via_obj_cmp: bool


@dataclass(frozen=True, kw_only=True)
class BoolOp(IRExpr):
    """`and`/`or` over Bool operands, `ty == Ty.Bool` (E9).

    **Short-circuit semantics are part of this node's contract**: sub-plan D
    lowers it to nested `if`/`else` blocks that evaluate `operands[i + 1]`
    only when `operands[i]` did not already decide the result -- `and` stops
    at the first false, `or` at the first true. Evaluating every operand
    eagerly would be observably wrong on chain, because an operand can trap,
    fail with a contract error, or spend budget.
    """

    op: BoolOpKind
    operands: tuple[IRExpr, ...]


@dataclass(frozen=True, kw_only=True)
class IfExp(IRExpr):
    """`then if cond else orelse`; both arms share `ty` (SS B.2)."""

    cond: IRExpr
    then: IRExpr
    orelse: IRExpr


@dataclass(frozen=True, kw_only=True)
class IsZero(IRExpr):
    """`operand == 0`, i.e. wasm `i64.eqz`; `ty == Ty.Bool`.

    **Read the polarity carefully.** This node means "is zero", NOT "is
    truthy". D3 rules that chain-int truthiness is `value != 0`, so a
    truthiness test lowers to `Unary(NOT, IsZero(x))` -- the checker composes
    the two rather than overloading one node with the inverted meaning, so
    there is exactly one way to read either shape and no chance of sub-plan D
    inverting a condition. `not IsZero(x)` collapses to two `eqz`
    instructions, which is D's peephole to make, not C's to pre-empt.
    """

    operand: IRExpr


@dataclass(frozen=True, kw_only=True)
class MakeStruct(IRExpr):
    """A `@contracttype` value: `Map<Symbol, V>` on chain (S9).

    `fields` is **pre-sorted ascending as byte strings** by C (P7), because
    `map_new_from_linear_memory` (`m.9`) requires the key descriptors in that
    order at compile time -- the wrong layout validates and then panics
    on-chain (F.1.13). C owns the sort; D must not re-sort or reorder.
    """

    struct_name: str
    fields: tuple[tuple[str, IRExpr], ...]


@dataclass(frozen=True, kw_only=True)
class FieldGet(IRExpr):
    """A struct field read: a `Symbol` key, then `map_get` (`m.1`)."""

    obj: IRExpr
    field: str
    struct_name: str


@dataclass(frozen=True, kw_only=True)
class MakeVec(IRExpr):
    """`Vec(T, [items])` (D2/A13). `all_static` records whether every item is
    a compile-time constant, which is what lets D choose
    `vec_new_from_linear_memory` (`v.g`) over `vec_new` + `vec_push_back`.

    **`all_static` is a fact about the ITEMS, not a licence to lay them out.**
    It says "every item is a literal C validated at compile time"; whether
    those literals can go in the data section as raw `Val`s is a separate
    question D answers from `Ty.repr_form` (A3). A HOST_OBJECT-repr element
    (`Bytes`, `String`, a nested container, a struct) has no inline Val form at
    all, so its object has to be built first and the linear-memory shortcut
    does not apply to it however static the item is.
    """

    elem_ty: Ty
    items: tuple[IRExpr, ...]
    all_static: bool


@dataclass(frozen=True, kw_only=True)
class MakeMap(IRExpr):
    """`Map(K, V, [(k, v), ...])` (D2/A13).

    When `all_static` is `True`, `pairs` is already in the host's key order
    (rank then `val_cmp`, A8/A14) AND the keys are proven unique, so D can emit
    `map_new_from_linear_memory` (`m.9`) directly. MJ-15: when C cannot
    TOTALLY order the literal keys (heterogeneous keys, or struct keys whose
    ordering tier 1 does not model, E3), `all_static` is `False` and D falls
    back to `map_new` + `map_put`, letting the host order them.

    `MakeVec`'s note on `all_static` vs `Ty.repr_form` applies here too: a
    HOST_OBJECT-repr key or value cannot be laid out in the data section from
    the flag alone.
    """

    key_ty: Ty
    value_ty: Ty
    pairs: tuple[tuple[IRExpr, IRExpr], ...]
    all_static: bool


@dataclass(frozen=True, kw_only=True)
class MakeTopics(IRExpr):
    """An event's topic tuple -- a `VecObject` fed to `contract_event`
    (`x.1`). Deliberately its own node, not a `MakeVec`, because topics are a
    HETEROGENEOUS chain-value tuple by design (D8) with `topics[0]` required
    to be a short `Symbol` naming the event (S11)."""

    topics: tuple[IRExpr, ...]


@dataclass(frozen=True, kw_only=True)
class HostCall(IRExpr):
    """The IR's single escape hatch: one host function, called BY NAME (B2).

    `fn_name` is a key of `serpent._host.functions_by_name`; the export code
    (`l.1`, `x.5`, ...) is data D looks up, never something C inlines. `ty` is
    the chain type of the RESULT as the checker sees it -- `HostFn`'s own
    `val_typed_ret`/`wasm_result` (B3) are what tell D whether that result
    arrives as a Val or a raw scalar.
    """

    fn_name: str
    args: tuple[IRExpr, ...]


@dataclass(frozen=True, kw_only=True)
class RawScalar(IRExpr):
    """A raw (non-Val) integer immediate from `_host._scalars` (B6): a
    `StorageType` or `ContractTtlExtension` argument. `ty` is `Ty.U32` -- the
    wasm-level width -- but `kind` is what says which table the number came
    from, so D never has to guess (and C never invents an unsourced
    constant)."""

    value: int
    kind: RawScalarKind


@dataclass(frozen=True, kw_only=True)
class ErrorVal(IRExpr):
    """The Error Val `(code << 32) | 3` (S7). RAISE POSITION ONLY: `Error` is
    never a value or a return type (S8), so this node exists only as the
    argument of `fail_with_error` (`x.5`) -- never a bare `unreachable` (R3).
    `ty` is `Ty.ErrorEnum(enum)`."""

    enum: str
    case: str
    code: int


@dataclass(frozen=True, kw_only=True)
class Convert(IRExpr):
    """An explicit representation bridge, e.g. `Timepoint`/`Duration` <-> `U64`
    (A17, `i.D`/`i.F`). `ty == to_ty`. There is deliberately NO implicit
    numeric conversion in serpent -- cross-width operands are a compile reject
    (T1), so this node only ever appears where the author wrote the bridge."""

    from_ty: Ty
    to_ty: Ty
    operand: IRExpr


@dataclass(frozen=True, kw_only=True)
class InternalCall(IRExpr):
    """A call to a module-level helper or a private method (E8), compiled as a
    non-exported WASM function. Recursion is rejected by a call-graph cycle
    check (SPT7005, Task 8), so `fn_name` always names a strictly "lower"
    function."""

    fn_name: str
    args: tuple[IRExpr, ...]


# --- statements --------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class LetLocal(IRStmt):
    """The FIRST binding of a local slot: `slot`'s type is fixed here (SS C.3
    rule 1) and `init` is the value it starts with -- there are no
    uninitialized locals in serpent."""

    slot: int
    ty: Ty
    init: IRExpr


@dataclass(frozen=True, kw_only=True)
class SetLocal(IRStmt):
    """A later assignment to an already-declared slot, at the SAME type."""

    slot: int
    value: IRExpr


@dataclass(frozen=True, kw_only=True)
class Eval(IRStmt):
    """A void expression evaluated for its effect -- a storage `set`/`del_`,
    `require_auth`, `events().publish`, a container mutator. A NON-void
    expression discarded as a statement is a reject (SPT1028), so this node
    always wraps something whose `ty` is `Ty.Void`."""

    value: IRExpr


@dataclass(frozen=True, kw_only=True)
class If(IRStmt):
    """Structured `if`/`else`; `elif` is a nested `If` in `orelse`. `cond` is
    always `Ty.Bool` -- truthiness has already been lowered to `IsZero` by the
    checker (D3/E10), so D never re-derives it."""

    cond: IRExpr
    body: tuple[IRStmt, ...]
    orelse: tuple[IRStmt, ...]


@dataclass(frozen=True, kw_only=True)
class While(IRStmt):
    """`block` + `loop` + `br_if`. `cond` is always `Ty.Bool`. `for` loops are
    desugared into this node INSIDE C (E4/E5), so D never sees a `For`."""

    cond: IRExpr
    body: tuple[IRStmt, ...]


@dataclass(frozen=True, kw_only=True)
class Break(IRStmt):
    """`br` to the enclosing block label. Outside a loop is a reject."""


@dataclass(frozen=True, kw_only=True)
class Continue(IRStmt):
    """`br` to the enclosing loop label. Outside a loop is a reject."""


@dataclass(frozen=True, kw_only=True)
class Raise(IRStmt):
    """`raise <ErrorEnum>.<Member>`, lowering to `fail_with_error` (`x.5`)
    over an `ErrorVal`.

    A DEDICATED statement node, rather than a bare `Eval(HostCall(...))`, so
    that R3's standing constraint -- "error codes are never lost to
    `unreachable`" -- is checkable STRUCTURALLY: every raise in the IR carries
    its `code`, and an emitter that dropped one would be visible as a missing
    node, not as a silently different instruction.
    """

    enum: str
    case: str
    code: int


@dataclass(frozen=True, kw_only=True)
class Return(IRStmt):
    """`return <expr>`, or `return`/fall-off for a `-> None` method (`value is
    None`). C proves definite return on every path (P6/S17); wasm validation
    provably cannot."""

    value: IRExpr | None


@dataclass(frozen=True, kw_only=True)
class Nop(IRStmt):
    """`pass`, and any statement that lowers to nothing."""


# --- declarations ------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class StructDecl(IRDecl):
    """A `@contracttype` struct. `fields` is `(name, ty, doc)` in DECLARATION
    order (the spec-entry order, B10) -- `MakeStruct` is where the P7
    byte-string sort lives, because that sort is a lowering concern, not a
    declaration one. Per-field `doc` is always `""` today: `_serpent_type_`
    records no per-field doc (B13's named gap); the field exists so closing
    that gap does not change this dataclass."""

    name: str
    doc: str
    fields: tuple[tuple[str, Ty, str], ...]


@dataclass(frozen=True, kw_only=True)
class ErrorEnumDecl(IRDecl):
    """A `@contracterror` enum: `cases` is `(name, code, doc)` in declaration
    order, each code read from its `errorcode(N)` call (A20/S10)."""

    name: str
    doc: str
    cases: tuple[tuple[str, int, str], ...]


@dataclass(frozen=True, kw_only=True)
class EventDecl(IRDecl):
    """A `@contractevent` class. Tracked SEPARATELY from structs and error
    enums (MJ-9): `spec.sections` still refuses an event class handed to
    `types=`, but `SC_SPEC_ENTRY_EVENT_V0` is no longer deferred -- it is
    built straight from the decorated class's own `_serpent_type_` metadata
    (`prefix_topics`/`locations`/`data_format`, M1-E Task 5), not from this
    node, which is why this declaration still has no topic/data fields to
    record."""

    name: str
    doc: str
    fields: tuple[tuple[str, Ty, str], ...]


@dataclass(frozen=True, kw_only=True)
class ConstDecl(IRDecl):
    """A module-level chain constant (P5): `ADMIN = Symbol("ADMIN")`."""

    name: str
    ty: Ty
    value: IRExpr


@dataclass(frozen=True, kw_only=True)
class FuncIR(IRNode):
    """One compiled function.

    `export_name` is the name the host sees -- the Python name for an ordinary
    method, `__constructor` for `__init__` (S6/B11), and unused (equal to
    `py_name`) for an INTERNAL function. `params` drops `self` and a leading
    `env: Env` (SS C.3, `sections.py:226-228`). `returns_on_every_path` is the
    RESULT of C's definite-return analysis, recorded so a consumer never
    re-derives it (P6/S17).
    """

    py_name: str
    export_name: str
    kind: FuncKind
    params: tuple[tuple[str, Ty, Loc], ...]
    ret: Ty
    doc: str
    locals: tuple[tuple[int, str, Ty], ...]
    body: tuple[IRStmt, ...]
    returns_on_every_path: bool


@dataclass(frozen=True, kw_only=True)
class ContractIR(IRNode):
    """The single `@contract` class: its methods, in declaration order.

    `__constructor` ordering for the spec entries is `sections.py`'s job
    (B10), so this node preserves SOURCE order and does not pre-sort.
    """

    name: str
    doc: str
    methods: tuple[FuncIR, ...]


@dataclass(frozen=True, kw_only=True)
class ModuleIR(IRNode):
    """A whole compiled contract module (SS C.2).

    `imports` is the set of `serpent.__all__` names the module imported (A22),
    kept for the AST/metadata cross-check (F.1.14).

    **`contract` is `ContractIR | None`, widening SS C.2's plain `ContractIR`.**
    The reason is `loader.LoadedModule`'s own asymmetry: a module can have zero
    `@contract` classes, more than one, or exactly one whose declaration failed
    to execute, and each is a located `SPT4019`/`SPT4xxx` diagnostic rather
    than a reason to abandon the compile. Collect-all (E16) means the frontend
    keeps checking everything else it can -- structs, error enums, helpers --
    and hands back a `ModuleIR` describing what it did understand. `None` is
    therefore always accompanied by at least one diagnostic, and sub-plan D
    never sees such a module: `compile_module` raises `CompileError` before D
    is reached (SS C.2's output list: "`diagnostics` -- must be empty for D to
    run"). A non-optional field would force either a fabricated empty
    `ContractIR` or an exception, and P2's discipline ("never fabricate")
    argues against the first.

    The five emitter-facing
    OUTPUTS beyond this tree -- `host_fns_used`, `needs_memory` plus the
    literal inventory, `runtime_parts_needed`, `spec_inputs`, `diagnostics` --
    are computed by `compile_module`'s assembly (Task 10) and are deliberately
    NOT fields here: they are derived facts about the tree (a `walk` away, SS
    C.1), and storing them would create two places for the same truth.
    """

    path: str
    doc: str
    imports: tuple[str, ...]
    consts: tuple[ConstDecl, ...]
    structs: tuple[StructDecl, ...]
    error_enums: tuple[ErrorEnumDecl, ...]
    events: tuple[EventDecl, ...]
    contract: ContractIR | None
    helpers: tuple[FuncIR, ...]


# --- traversal ---------------------------------------------------------------


def walk(node: IRNode) -> Iterator[IRNode]:
    """Yield `node` and every `IRNode` nested inside it, depth-first.

    The one traversal every later task shares, so the protocol floor (B4/S18)
    really is SS C.1's one-liner:

        declared_protocol(
            frozenset(n.fn_name for n in walk(ir) if isinstance(n, HostCall)),
            requested,
        )

    Reflection over `dataclasses.fields` rather than a hand-written visitor is
    deliberate: a node kind added later is traversed automatically instead of
    being silently skipped by a visitor nobody remembered to extend.
    """
    yield node
    for f in dataclasses.fields(node):
        yield from _walk_value(getattr(node, f.name))


def _walk_value(value: object) -> Iterator[IRNode]:
    if isinstance(value, IRNode):
        yield from walk(value)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _walk_value(item)
