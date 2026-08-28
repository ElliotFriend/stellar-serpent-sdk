"""IR -> wasm: dossier SS B.3.1 and SS B.3.2, plus one whole function.

Three layers, outermost first: ``compile_function`` (a ``FuncIR`` -> a finished
``Fn``: ABI prologue, body, tail), ``lower_stmt`` (SS B.3.2's statements), and
the expression pair below (SS B.3.1).

Two expression entry points, and the difference between them is the whole
value model:

* ``lower_expr`` leaves **one i64 ``Val`` word** -- the tagged, host-visible
  form. Every statement position, every host-call argument, every ``obj_cmp``
  operand wants this.
* ``lower_expr_raw`` leaves the **raw unboxed word** -- the untagged number.
  Only arithmetic and direct (non-``obj_cmp``) comparison operand positions
  want this, and mixing the two is exactly how a tag ends up inside an addend
  (``arith``'s module docstring).

Both are wrapped in ``Fn.expr_scope`` (review M1), so a lowering that leaks a
value or forgets a ``drop`` fails at the node that caused it.

## Unbox BEFORE you compare (F.1.1)

The single worst silent divergence available here: an ``EITHER``-repr type
(``U64``, ``I64``, ``Timepoint``, ``Duration``, ``U128``, ``I128``) is a small
immediate below its 56-bit bound and an object handle above it, so a raw
``i64`` comparison of two ``Val`` WORDS answers a question about tags and
handle indices, not about numbers. ``I64(2**55) > I64(2**55 - 1)`` is true, and
comparing the packed words says otherwise the moment one side crosses
``MAX_SMALL_I64``. Every non-``obj_cmp`` compare below therefore lowers BOTH
sides through ``lower_expr_raw`` first, and the test suite straddles each
bound in both directions against the tier-1 oracle.

## Signedness comes from the OPERAND type, never the representation (F.1.11)

The relop is chosen from ``Compare.lhs.ty``. A raw ``I64`` word and a raw
``U64`` word are the same 64 bits; only the source type says which of
``i64.lt_s`` and ``i64.lt_u`` is the right question. ``U32``/``I32`` are 32-bit
types living in 64-bit locals (``Ty.wasm_arith_width`` keeps that visible), and
their unboxes zero- and sign-extend respectively, so the same rule holds one
width down.

## Short-circuit is semantics, not an optimization (C8)

``and``/``or`` lower to nested ``if (result i64)`` chains. An operand can trap,
fail with a contract error, or spend budget, so evaluating the right-hand side
of a decided ``and`` would be observably wrong on chain. ``IfExp`` is lowered
the same way and specifically NOT with ``select``, which evaluates both arms
(SS B.2's ban).

## One condition helper (review m2)

A ``Bool`` ``Val`` is *literally* the word ``0`` or ``1`` (``val.pack_bool``),
and the ABI prologues guarantee that for parameters too, so an i32 branch
condition is one ``i32.wrap_i64``. ``lower_condition`` is the only place that
knows this; ``If``, ``While``, ``IfExp`` and ``BoolOp`` all call it rather than
each inventing a comparison.

## No constant folding (F.1.16/C10)

``lower_expr_raw`` of a ``Const`` emits the raw number directly instead of
boxing it and immediately unboxing it again. That is a same-semantics
shortcut on ONE node, not folding: ``I32(2**31 - 1) + I32(1)`` still reaches
``lower_binary`` as two constants and still fails at runtime with
``ArithmeticOverflow``, which is what F.1.10 requires.

## Host objects are IMMUTABLE, so every mutator result is rebound (F.1.9)

``vec_push_back`` does not lengthen a vector; it returns a handle to a new,
longer one. So does ``map_put``. A build-up chain that dropped those results
and kept pushing onto the original handle would produce a one-element
container, report success, and be wrong in a way no wasm check can see -- which
is why every chain below threads its handle through a hidden local that each
step reads and rewrites, and why Task 9's statement path rebinds a mutator's
result with ``SetLocal`` rather than discarding it.

## Two lowerings per container, and the gate is NOT ``all_static`` (C4/E12)

``MakeVec``/``MakeMap`` carry ``all_static``, and it is necessary but not
sufficient. It records that every item is a literal C validated; it says
nothing about whether that literal HAS an inline ``Val`` word to lay in the
data section. A ``String`` item is entirely static and its ``Val`` is an object
handle that does not exist until ``string_new_from_linear_memory`` has run
(A3). ``_static_word`` is the actual test, and it answers ``None`` -- meaning
"take the build-up chain" -- for every EITHER-repr literal above its 56-bit
bound too.

``MakeMap`` has a second, sharper gate (ruling E12): the descriptor form's keys
are byte strings "convertible to ``Symbol`` type" (P1), so a fully static
``Map[U32, U32]`` literal takes the chain regardless. Reading ``all_static`` as
a licence there produces a module that validates and then panics on chain.

## The storage-get guard, and the one shape exempt from it (E13/B7/M12)

``get_contract_data`` on an absent key is undefined at the host, so serpent
gives it a defined answer: ``has_contract_data`` first, ``fail_with_error``
with ``CODE_MISSING_VALUE`` on a miss. The exception is
``get(k, T, default=d)``, whose ``IfExp`` already asked ``has`` -- guarding
that one would ask twice for a single read.

Recognizing it is ``frontend.guarded_storage_get``, on node IDENTITY, and both
halves of that matter. Sharing the key node is what lets this module evaluate
the key ONCE into a hidden local (review B7): a key can be a call, and lowering
the two arms independently would run its effects twice. And identity rather
than equality is what keeps a hand-written ``a.get(k, T) if a.has(k2) else d``
guarded (review M12) -- its two key subtrees are distinct nodes even when they
compare ``==``, because there is no promise the two evaluations agree.

## The ABI boundary is checked in ONE place, from two directions (S3/E14)

A wasm export's signature is ``(i64...) -> i64`` and says nothing at all about
what those words MEAN, so a caller can hand a ``SymbolObject`` to a method
whose parameter is a ``U32`` and the unbox will read the handle index as a
number and answer confidently. ``abi_check`` is the per-type tag-and-range
table that stops it, and ``narrow_to`` applies the SAME table to a host-call
return -- S3's second sentence, because ``map_get`` and ``get_contract_data``
hand back an ANY-typed ``Val`` and the checker's type for that expression is a
claim, not a proof. One table for both, per review M9: two implementations of
one rule diverge, with nothing to say which half is the weaker check.

## The only ``unreachable`` in the module is E4's guard, and it comes second

After ``fail_with_error`` the emitter stops (P14): the host does not return,
and an ``unreachable`` there would replace the contract error a client needs to
see with a generic VM trap. The one exception is a body wasm still considers
able to fall off its end while C proved it returns on every path (C1/C16) --
``while True:`` with no ``break``, a body ending in ``raise``, an exhaustive
if/else. That tail gets ``fail_with_error(CODE_UNREACHABLE_GUARD)``, ``drop``,
then ``unreachable``, in that order. Reversed, the module still validates and
still aborts -- with no code for a client to read, which is R3 broken in the
one place it is checkable.
"""

import struct as _struct
from collections.abc import Mapping
from weakref import WeakKeyDictionary

from serpent import errors, val
from serpent._host import functions_by_name
from serpent._host._model import HostFn
from serpent.compiler.frontend import guarded_storage_get
from serpent.compiler.ir import (
    Binary,
    BoolOp,
    BoolOpKind,
    Break,
    Compare,
    CompareOp,
    Const,
    ConstRef,
    Continue,
    Convert,
    ErrorVal,
    Eval,
    FieldGet,
    FuncIR,
    FuncKind,
    HostCall,
    If,
    IfExp,
    InternalCall,
    IRExpr,
    IRStmt,
    IsZero,
    LetLocal,
    LocalRef,
    MakeMap,
    MakeStruct,
    MakeTopics,
    MakeVec,
    Nop,
    ParamRef,
    Raise,
    RawScalar,
    Return,
    SetLocal,
    Unary,
    UnaryOp,
    While,
    walk,
)
from serpent.compiler.recognize import RECOGNIZED
from serpent.compiler.types_ import Ty, TyTag
from serpent.emitter import arith, encode, opcodes
from serpent.emitter.arith import EmitCtx
from serpent.emitter.frame import EmitError, Fn
from serpent.emitter.layout import Memory

__all__ = [
    "ABI_CHECKED_TAGS",
    "LowerCtx",
    "abi_check",
    "compile_function",
    "lower_body",
    "lower_condition",
    "lower_expr",
    "lower_expr_raw",
    "lower_stmt",
    "narrow_to",
]


# --- the lowering context ------------------------------------------------------


class LowerCtx(EmitCtx):
    """``EmitCtx`` plus the three things expression lowering needs on top of it.

    * ``consts`` -- the module's chain-constant table (P5). Ruling E11 inlines
      a ``ConstRef`` at every use rather than emitting a wasm global, so the
      lowering has to be able to reach the ``ConstDecl.value`` expression the
      name stands for. A ``Mapping`` rather than the ``ConstDecl``s themselves
      because that is all this module reads.
    * ``functions`` -- ``FuncIR.py_name -> defidx`` for the module's OWN
      functions, in ``CompiledModule.functions`` order, so an ``InternalCall``
      resolves to a ``CallDefined`` (review B1: a defined-space index, never a
      combined-space one, and never baked into a body before pass 2).
    * a per-function memo of already-materialized POOLED constants -- see
      ``_memoise``.

    A subclass rather than a wrapper so every ``arith`` entry point (which
    takes an ``EmitCtx``) keeps accepting it unchanged: there is one context
    object per module, not two that could drift.
    """

    def __init__(
        self,
        n_module_functions: int,
        memory: Memory,
        consts: Mapping[str, IRExpr] | None = None,
        functions: Mapping[str, int] | None = None,
    ) -> None:
        super().__init__(n_module_functions, memory)
        self.consts: Mapping[str, IRExpr] = {} if consts is None else consts
        self.functions: Mapping[str, int] = {} if functions is None else functions
        # Keyed on the `Fn` itself, WEAKLY: a hidden local belongs to exactly
        # one function body, and keying on `id(fn)` would let a recycled
        # address hand one function's slot number to another -- which would
        # still validate, and read a completely unrelated local.
        self._const_memo: WeakKeyDictionary[Fn, dict[str, int]] = WeakKeyDictionary()

    def const_memo(self, fn: Fn) -> dict[str, int]:
        """``ConstRef.name -> hidden local`` for the function under construction."""
        memo = self._const_memo.get(fn)
        if memo is None:
            memo = {}
            self._const_memo[fn] = memo
        return memo


# --- small emission helpers -----------------------------------------------------


def _bool_from_flag(fn: Fn) -> None:
    """``i64.extend_i32_u``: a comparison's i32 0/1 IS the Bool ``Val`` word."""
    fn.pop("i32")
    fn.op(opcodes.I64_EXTEND_I32_U)
    fn.push("i64")


def _eqz(fn: Fn) -> None:
    """``i64.eqz``: one i64 in, an i32 boolean out."""
    fn.pop("i64")
    fn.op(opcodes.I64_EQZ)
    fn.push("i32")


def _wrap(fn: Fn) -> None:
    """``i32.wrap_i64``: the Bool ``Val`` word 0/1 becomes a branch condition."""
    fn.pop("i64")
    fn.op(opcodes.I32_WRAP_I64)
    fn.push("i32")


# --- comparison tables ----------------------------------------------------------

#: The signed relop per source operator. Also the table the ``obj_cmp`` and
#: ``{u,i}128_cmp`` routes use against ``0``: their answer is a signed -1/0/1,
#: whatever the operands' own signedness was.
_SIGNED_RELOP: dict[CompareOp, int] = {
    CompareOp.EQ: opcodes.I64_EQ,
    CompareOp.NE: opcodes.I64_NE,
    CompareOp.LT: opcodes.I64_LT_S,
    CompareOp.LE: opcodes.I64_LE_S,
    CompareOp.GT: opcodes.I64_GT_S,
    CompareOp.GE: opcodes.I64_GE_S,
}

_UNSIGNED_RELOP: dict[CompareOp, int] = {
    CompareOp.EQ: opcodes.I64_EQ,
    CompareOp.NE: opcodes.I64_NE,
    CompareOp.LT: opcodes.I64_LT_U,
    CompareOp.LE: opcodes.I64_LE_U,
    CompareOp.GT: opcodes.I64_GT_U,
    CompareOp.GE: opcodes.I64_GE_U,
}

#: Operand types whose raw word is a SIGNED number (F.1.11). Everything else
#: this module can compare directly is unsigned: `U32`/`U64` by definition,
#: `Timepoint`/`Duration` because their small bodies are unsigned (`arith`'s
#: `unbox_timepoint`/`unbox_duration` shift with `shr_u`), and `Bool` because
#: its raw word is 0 or 1.
_SIGNED_TAGS: frozenset[TyTag] = frozenset({TyTag.I32, TyTag.I64, TyTag.I128})

#: The 128-bit types: a limb PAIR, never one word, at every entry point here.
_WIDE_TAGS: frozenset[TyTag] = frozenset({TyTag.U128, TyTag.I128})

#: Types whose ``Val`` word IS the raw word, so unbox and rebox are both
#: nothing. Only ``Bool``: ``pack_bool`` is the identity on 0/1.
_RAW_IDENTITY_TAGS: frozenset[TyTag] = frozenset({TyTag.BOOL})


# --- constants -------------------------------------------------------------------

#: The small-form tag for each EITHER 64-bit type, and the host constructor for
#: the value that does not fit it.
_SMALL64: dict[TyTag, tuple[int, str, bool]] = {
    # tag, obj_from_* host function, signed body
    TyTag.U64: (val.TAG_U64_SMALL, "obj_from_u64", False),
    TyTag.I64: (val.TAG_I64_SMALL, "obj_from_i64", True),
    TyTag.TIMEPOINT: (val.TAG_TIMEPOINT_SMALL, "timepoint_obj_from_u64", False),
    TyTag.DURATION: (val.TAG_DURATION_SMALL, "duration_obj_from_u64", False),
}

#: The same, one width up. A 128-bit object is built from two raw limbs
#: (``hi``, ``lo``) -- there is no one-word form to hand the host.
_SMALL128: dict[TyTag, tuple[int, str, bool]] = {
    TyTag.U128: (val.TAG_U128_SMALL, "obj_from_u128_pieces", False),
    TyTag.I128: (val.TAG_I128_SMALL, "obj_from_i128_pieces", True),
}

_MASK128 = (1 << 128) - 1

#: The linear-memory constructor for each pooled literal type (S19/S22).
_POOL_CONSTRUCTOR: dict[TyTag, str] = {
    TyTag.SYMBOL: "symbol_new_from_linear_memory",
    TyTag.STRING: "string_new_from_linear_memory",
    TyTag.BYTES: "bytes_new_from_linear_memory",
    TyTag.BYTES_N: "bytes_new_from_linear_memory",
    # Review B6: an `Address` literal is a pooled strkey STRING, converted by
    # `strkey_to_address` (verified present in the pin: module `a`, export `1`,
    # `strkey` accepts a `BytesObject` or a `StringObject`).
    TyTag.ADDRESS: "string_new_from_linear_memory",
}

#: The second call an `Address` literal needs, after its strkey string exists.
_STRKEY_TO_ADDRESS = "strkey_to_address"

#: The two names ruling E13's guard is built from, read out of the SAME
#: recognition-table row the frontend's own accounting reads (MJ-5) rather than
#: restated as literals here. `guarded_storage_get` matches on that row too, so
#: the predicate, the accounting and this lowering cannot drift apart.
_STORAGE_HAS_FN, _STORAGE_GET_FN = RECOGNIZED["storage.get_default"].host_fns

#: What a missing key becomes (E14): a CONTRACT error, never an `unreachable`
#: (R3), carrying `CODE_MISSING_VALUE` so a client sees which rule was broken.
_FAIL_WITH_ERROR_FN = "fail_with_error"

#: The container build-up chains (F.1.9/F.1.10) and their linear-memory
#: alternatives (C4/E12).
_VEC_NEW_FN = "vec_new"
_VEC_PUSH_FN = "vec_push_back"
_VEC_LM_FN = "vec_new_from_linear_memory"
_MAP_NEW_FN = "map_new"
_MAP_PUT_FN = "map_put"
_MAP_GET_FN = "map_get"
_MAP_LM_FN = "map_new_from_linear_memory"


# --- the entry points -------------------------------------------------------------


def lower_expr(fn: Fn, ctx: LowerCtx, e: IRExpr) -> None:
    """Lower ``e``, leaving exactly one i64 ``Val`` word on the stack."""
    with fn.expr_scope(is_void=False):
        _lower_val(fn, ctx, e)


def lower_expr_raw(fn: Fn, ctx: LowerCtx, e: IRExpr) -> None:
    """Lower ``e``, leaving its RAW (unboxed) word on the stack.

    For a ``Const`` the raw number is emitted directly rather than boxed and
    immediately unboxed -- a same-semantics shortcut on one node, NOT constant
    folding (F.1.16/C10: nothing here evaluates an operator at compile time).

    A ``RawScalar`` takes the same route for a different reason: it is
    ALREADY a raw word (C13), so its ``Val`` form and its raw form are the
    same bytes. Falling through to the unbox would shift a number that has no
    tag, and quietly produce ``0``.
    """
    with fn.expr_scope(is_void=False):
        if isinstance(e, RawScalar):
            fn.i64_const(e.value)
            return
        if isinstance(e, Const):
            raw = _raw_const_word(e)
            if raw is not None:
                fn.i64_const(raw)
                return
        _lower_val(fn, ctx, e)
        _unbox(fn, ctx, e.ty)


def lower_condition(fn: Fn, ctx: LowerCtx, cond: IRExpr, *, negate: bool = False) -> None:
    """Lower ``cond`` to the i32 an ``if``/``br_if`` wants (review m2).

    THE one condition helper: ``If``, ``While``, ``IfExp`` and ``BoolOp`` all
    call it, so no site invents its own comparison. A ``Bool`` ``Val`` is
    literally the word 0 or 1 -- ``val.pack_bool`` says so, ``obj_cmp``-derived
    Bools are built by ``i64.extend_i32_u`` of a relop, and the ABI prologues
    guarantee it for parameters -- so the whole conversion is one
    ``i32.wrap_i64``.

    ``negate=True`` is ``While``'s exit test, which wants "the condition is
    FALSE" as an i32 (§B.3.2's ``i32.eqz; br_if $exit``). From the same premise
    that is one ``i64.eqz`` rather than a wrap followed by a second negation:
    the word is 0 or 1, so testing it against zero at 64 bits answers exactly
    the same question and yields exactly the same i32. It lives here rather
    than in the ``While`` lowering so that the "a Bool Val IS 0 or 1" fact
    still has one home.
    """
    if cond.ty.tag is not TyTag.BOOL:
        raise EmitError(
            f"a condition must be Bool, got {cond.ty.render()}; the frontend lowers "
            "chain-int truthiness to Unary(NOT, IsZero(x)) (D3/E10), so D never "
            "re-derives it"
        )
    lower_expr(fn, ctx, cond)
    if negate:
        _eqz(fn)
    else:
        _wrap(fn)


# --- the dispatch (F.1.15: exhaustive, with a LOUD default) ------------------------


def _lower_val(fn: Fn, ctx: LowerCtx, e: IRExpr) -> None:
    if isinstance(e, Const):
        _lower_const(fn, ctx, e)
    elif isinstance(e, ParamRef):
        fn.local_get(e.index)
    elif isinstance(e, LocalRef):
        fn.local_get(fn.nparams + e.slot)
    elif isinstance(e, ConstRef):
        _lower_const_ref(fn, ctx, e)
    elif isinstance(e, RawScalar):
        # C13: a raw immediate is NOT a Val. `StorageType`/`ContractTtlExtension`
        # arguments are plain u32s the host reads as numbers, so packing one as
        # a `U32Val` would pass the wrong argument and still validate.
        fn.i64_const(e.value)
    elif isinstance(e, ErrorVal):
        fn.i64_const(val.error_val(e.code))
    elif isinstance(e, IsZero):
        _lower_is_zero(fn, ctx, e)
    elif isinstance(e, Unary):
        _lower_unary(fn, ctx, e)
    elif isinstance(e, Binary):
        _lower_binary(fn, ctx, e)
    elif isinstance(e, Compare):
        _lower_compare(fn, ctx, e)
    elif isinstance(e, BoolOp):
        _lower_boolop(fn, ctx, e, 0)
    elif isinstance(e, IfExp):
        _lower_if_exp(fn, ctx, e)
    elif isinstance(e, Convert):
        # C11: no producer builds one today. Silently treating it as a no-op
        # would be a representation bridge that never happens -- a
        # `Timepoint` handed to something expecting a `U64` -- so it is loud.
        raise EmitError(
            f"Convert({e.from_ty.render()} -> {e.to_ty.render()}) has no lowering: "
            "nothing in the frontend builds this node today (C11), and treating it "
            "as a no-op would silently skip a representation bridge"
        )
    elif isinstance(e, HostCall):
        _lower_host_call(fn, ctx, e)
    elif isinstance(e, InternalCall):
        _lower_internal_call(fn, ctx, e)
    elif isinstance(e, MakeStruct):
        _lower_make_struct(fn, ctx, e)
    elif isinstance(e, FieldGet):
        _lower_field_get(fn, ctx, e)
    elif isinstance(e, MakeVec):
        _lower_make_vec(fn, ctx, e.items, e.all_static)
    elif isinstance(e, MakeTopics):
        # D8: topics are a heterogeneous tuple with no `all_static` flag, so
        # the gate is `_bulk_construction_can_use_memory`'s own test
        # (`frontend.py`) -- every topic a `Const` -- applied by `_static_words`.
        _lower_make_vec(fn, ctx, e.topics, True)
    elif isinstance(e, MakeMap):
        _lower_make_map(fn, ctx, e)
    else:
        raise EmitError(
            f"no lowering for IR node {type(e).__name__} ({e!r}); the expression "
            "dispatch is exhaustive over serpent.compiler.ir by design (F.1.15) -- "
            "a new node kind must be added here, never silently skipped"
        )


# --- Const --------------------------------------------------------------------------


def _lower_const(fn: Fn, ctx: LowerCtx, e: Const) -> None:
    """One literal -> its ``Val``, split at COMPILE time wherever it can be.

    Every EITHER-repr width is decided here by ``val.fits_small_*`` rather than
    at runtime by the ``box_*`` parts: the value is known, so the branch is
    not. What survives to runtime is only what has to -- the host call that
    allocates an object, or the linear-memory constructor that reads the pool.
    """
    ty = e.ty
    if ty.tag is TyTag.INVALID:
        raise EmitError(
            "a Const with Ty.Invalid reached the emitter; Ty.Invalid is the "
            "sink-reported-failure sentinel (minor 13) and a module carrying one "
            "never reaches D"
        )
    if e.py_value is None:
        # `x: U32 | None = None` -- the Option's empty case is the Void tag.
        fn.i64_const(val.VOID_VAL)
        return
    if ty.tag is TyTag.OPTION:
        # A present Option value is just the wrapped type's own encoding.
        assert ty.elem is not None
        _lower_const(fn, ctx, Const(loc=e.loc, ty=ty.elem, py_value=e.py_value))
        return

    if ty.tag is TyTag.BOOL:
        fn.i64_const(val.pack_bool(_as_bool(e)))
        return
    if ty.tag is TyTag.U32:
        fn.i64_const(val.pack_u32val(_as_int(e)))
        return
    if ty.tag is TyTag.I32:
        fn.i64_const(val.pack_i32val(_as_int(e)))
        return

    if ty.tag is TyTag.SYMBOL:
        text = _as_str(e)
        if val.fits_symbol_small(text):
            # S22: 9 characters or fewer pack into a SymbolSmall immediate --
            # no pool entry, no host call, no linear memory.
            fn.i64_const(val.symbol_small(text))
            return

    narrow = _SMALL64.get(ty.tag)
    if narrow is not None:
        _lower_const_64(fn, ctx, _as_int(e), narrow)
        return
    wide = _SMALL128.get(ty.tag)
    if wide is not None:
        _lower_const_128(fn, ctx, _as_int(e), wide)
        return

    blob = _pooled_blob(e)
    if blob is not None:
        _lower_pooled(fn, ctx, ty.tag, blob)
        return

    raise EmitError(f"no Const lowering for {ty.render()} (py_value {e.py_value!r})")


def _lower_const_64(fn: Fn, ctx: LowerCtx, value: int, spec: tuple[int, str, bool]) -> None:
    tag, constructor, signed = spec
    fits = val.fits_small_i(value) if signed else val.fits_small_u(value)
    if fits:
        fn.i64_const(val.pack_small_i64(value, tag) if signed else val.pack_small_u64(value, tag))
        return
    fn.i64_const(value)
    fn.call_import(ctx.host_import_name(constructor), 1, has_result=True)


def _lower_const_128(fn: Fn, ctx: LowerCtx, value: int, spec: tuple[int, str, bool]) -> None:
    """A 128-bit literal: the tag-10/11 small form, or two limb constants.

    The small forms are the SAME 56-bit bodies the 64-bit types use -- only the
    tag differs -- so the compile-time test is `val.fits_small_*` again rather
    than a 128-bit-specific bound. Above it, the value is split into the two
    raw limbs `obj_from_{u,i}128_pieces` takes; the split is over the value's
    two's-complement 128-bit pattern, so a negative `I128` gets an all-ones-ish
    high limb rather than a sign-lost magnitude.
    """
    tag, constructor, signed = spec
    fits = val.fits_small_i(value) if signed else val.fits_small_u(value)
    if fits:
        fn.i64_const(val.pack_small_i64(value, tag) if signed else val.pack_small_u64(value, tag))
        return
    pattern = value & _MASK128
    fn.i64_const(pattern >> 64)
    fn.i64_const(pattern & val.MASK64)
    fn.call_import(ctx.host_import_name(constructor), 2, has_result=True)


def _pooled_blob(e: Const) -> bytes | None:
    """The bytes ``e`` needs in the literal pool, or ``None`` if it is immediate.

    A ``Symbol`` of 9 characters or fewer is a ``SymbolSmall`` immediate (S22)
    and needs no pool entry at all; every ``String``/``Bytes``/``BytesN``, and
    every ``Address``' strkey (review B6), does.
    """
    tag = e.ty.tag
    if tag is TyTag.SYMBOL:
        text = _as_str(e)
        return None if val.fits_symbol_small(text) else text.encode("utf-8")
    if tag is TyTag.STRING:
        return _as_str(e).encode("utf-8")
    if tag in (TyTag.BYTES, TyTag.BYTES_N):
        payload = e.py_value
        if not isinstance(payload, bytes):
            raise EmitError(f"a {e.ty.render()} literal must be bytes, got {payload!r}")
        return payload
    if tag is TyTag.ADDRESS:
        return _as_str(e).encode("utf-8")
    return None


def _lower_pooled(fn: Fn, ctx: LowerCtx, tag: TyTag, blob: bytes) -> None:
    """``constructor(pack_u32val(offset), pack_u32val(len))`` over the pool.

    The offset comes from ``Memory.intern``, which Task 10 has already SEEDED
    from the ``LiteralInventory`` (E7) -- so it is a pure function of the
    inventory rather than of which body happened to be lowered first. Both
    arguments are ``U32Val``s, not raw numbers: the pin types them that way.
    """
    if tag is TyTag.SYMBOL and len(blob) > val.SCSYMBOL_LIMIT:  # pragma: no cover - S3 pre-empts
        raise EmitError(f"symbol literal is longer than {val.SCSYMBOL_LIMIT} characters: {blob!r}")
    offset = ctx.memory.intern(blob)
    fn.i64_const(val.pack_u32val(offset))
    fn.i64_const(val.pack_u32val(len(blob)))
    fn.call_import(ctx.host_import_name(_POOL_CONSTRUCTOR[tag]), 2, has_result=True)
    if tag is TyTag.ADDRESS:
        # Review B6: the pooled bytes are the strkey TEXT; the object is what
        # `strkey_to_address` makes of the `StringObject` just built.
        fn.call_import(ctx.host_import_name(_STRKEY_TO_ADDRESS), 1, has_result=True)


def _raw_const_word(e: Const) -> int | None:
    """``e``'s RAW word, when the type has a one-word raw form; else ``None``.

    ``None`` means "no shortcut" -- the caller boxes and unboxes, which is
    correct for every type, just longer. 128-bit types are always ``None``
    here: their raw form is a limb PAIR.
    """
    tag = e.ty.tag
    if e.py_value is None:
        return None
    if tag is TyTag.BOOL:
        return val.pack_bool(_as_bool(e))
    if tag in (TyTag.U32, TyTag.I32, TyTag.U64, TyTag.I64, TyTag.TIMEPOINT, TyTag.DURATION):
        return _as_int(e)
    return None


def _as_int(e: Const) -> int:
    value = e.py_value
    # `bool` is an `int` in Python and T2/D4 make that a compile reject, so the
    # frontend never builds one here -- but an int-typed Const holding `True`
    # would pack as 1 with no complaint at all, which is why it is refused.
    if not isinstance(value, int) or isinstance(value, bool):
        raise EmitError(f"a {e.ty.render()} literal must be an int, got {value!r}")
    return value


def _as_bool(e: Const) -> bool:
    value = e.py_value
    if not isinstance(value, bool):
        raise EmitError(f"a Bool literal must be a bool, got {value!r}")
    return value


def _as_str(e: Const) -> str:
    value = e.py_value
    if not isinstance(value, str):
        raise EmitError(f"a {e.ty.render()} literal must be a str, got {value!r}")
    return value


# --- ConstRef (ruling E11) -------------------------------------------------------


def _lower_const_ref(fn: Fn, ctx: LowerCtx, e: ConstRef) -> None:
    """Inline the module constant's value expression at this use (E11).

    A chain constant is not a wasm global: `ADMIN = Symbol("ADMIN")` has no
    initializer form the host understands, so each use rebuilds the value. For
    an immediate that is one `i64.const` and nothing is gained by doing
    otherwise; for a POOLED literal it is a host call, and repeating it is
    repeating an allocation -- hence `_memoise`.
    """
    value = ctx.consts.get(e.name)
    if value is None:
        raise EmitError(
            f"ConstRef({e.name!r}) has no entry in LowerCtx.consts; ruling E11 inlines "
            "a chain constant's value at each use, so the module's constant table has "
            "to reach the lowering"
        )
    memo = ctx.const_memo(fn)
    slot = memo.get(e.name)
    if slot is not None:
        fn.local_get(slot)
        return
    _lower_val(fn, ctx, value)
    _memoise(fn, memo, e.name, value)


def _memoise(fn: Fn, memo: dict[str, int], name: str, value: IRExpr) -> None:
    """Keep a just-built POOLED constant in a hidden local for later uses.

    Two conditions, and the second is a DOMINANCE argument rather than a
    heuristic:

    1. The value must be pooled -- a `symbol_new_from_linear_memory` or
       `string_new_from_linear_memory` call. An immediate is one `i64.const`,
       cheaper than the `local.get` that would replace it.
    2. The defining use must be at control-frame depth **0**. A wasm local
       starts at `0`, which is a perfectly valid `Val` (`FALSE_VAL`), so
       reading a local that was never written is silent corruption, not a
       trap. A definition inside an `if` arm does not dominate a use in the
       sibling arm -- or anywhere after the `end` -- and `Fn` is lowered in
       source order, so "no open frames" is exactly the condition under which
       everything emitted afterwards is dominated by this store.

    A `local.tee` rather than a set-then-get: the value is already on the
    stack and this use still needs it.
    """
    if fn.ctrl:
        return
    if not isinstance(value, Const) or _pooled_blob(value) is None:
        return
    slot = fn.new_local()
    fn.local_tee(slot)
    memo[name] = slot


# --- IsZero / Unary --------------------------------------------------------------


def _lower_is_zero(fn: Fn, ctx: LowerCtx, e: IsZero) -> None:
    """``operand == 0`` (D3). Read the polarity: this is IS zero, not truthy.

    The operand is unboxed first, because the question is about the NUMBER: a
    `U64` object handle is never the word 0 however zero the value behind it
    is. A 128-bit operand is zero only when BOTH limbs are.
    """
    ty = e.operand.ty
    if ty.tag in _WIDE_TAGS:
        lower_expr(fn, ctx, e.operand)
        hi, lo = arith.unbox_wide(fn, ctx, ty)
        fn.local_get(hi)
        fn.local_get(lo)
        fn.binop_i64(opcodes.I64_OR)
    else:
        lower_expr_raw(fn, ctx, e.operand)
    _eqz(fn)
    _bool_from_flag(fn)


def _lower_unary(fn: Fn, ctx: LowerCtx, e: Unary) -> None:
    if e.op is UnaryOp.NOT:
        if e.operand.ty.tag is not TyTag.BOOL:
            raise EmitError(
                f"`not` takes a Bool, got {e.operand.ty.render()}; E9 restricts it and "
                "the frontend composes truthiness as Unary(NOT, IsZero(x)) (D3)"
            )
        # A Bool Val is literally 0 or 1, so `eqz` IS the negation -- no
        # comparison against a tag, and nothing to unbox first.
        lower_expr(fn, ctx, e.operand)
        _eqz(fn)
        _bool_from_flag(fn)
        return
    ty = e.ty
    if ty.tag in _WIDE_TAGS:
        lower_expr(fn, ctx, e.operand)
        hi, lo = arith.unbox_wide(fn, ctx, ty)
        fn.local_get(hi)
        fn.local_get(lo)
        out_hi, out_lo = arith.wide_neg(fn, ctx, ty)
        fn.local_get(out_hi)
        fn.local_get(out_lo)
        arith.rebox_wide(fn, ctx, ty)
        return
    lower_expr_raw(fn, ctx, e.operand)
    arith.lower_neg(fn, ctx, ty)
    arith.rebox(fn, ctx, ty)


# --- Binary ------------------------------------------------------------------------


def _lower_binary(fn: Fn, ctx: LowerCtx, e: Binary) -> None:
    """``lhs <op> rhs`` under A4's checked-arithmetic contract.

    Raw in, raw out, boxed here: `arith` owns the overflow analysis and never
    sees a `Val`. Nothing is folded -- both operands reach `lower_binary` even
    when both are literals, so `I32(2**31 - 1) + I32(1)` still aborts with
    `ArithmeticOverflow` at runtime (F.1.10/F.1.16).
    """
    ty = e.ty
    if ty.tag in _WIDE_TAGS:
        lower_expr(fn, ctx, e.lhs)
        lhs_hi, lhs_lo = arith.unbox_wide(fn, ctx, ty)
        lower_expr(fn, ctx, e.rhs)
        rhs_hi, rhs_lo = arith.unbox_wide(fn, ctx, ty)
        for slot in (lhs_hi, lhs_lo, rhs_hi, rhs_lo):
            fn.local_get(slot)
        out_hi, out_lo = arith.wide_binary(fn, ctx, ty, e.op)
        fn.local_get(out_hi)
        fn.local_get(out_lo)
        arith.rebox_wide(fn, ctx, ty)
        return
    lower_expr_raw(fn, ctx, e.lhs)
    lower_expr_raw(fn, ctx, e.rhs)
    arith.lower_binary(fn, ctx, ty, e.op)
    arith.rebox(fn, ctx, ty)


# --- Compare -------------------------------------------------------------------------


def _lower_compare(fn: Fn, ctx: LowerCtx, e: Compare) -> None:
    """``lhs <op> rhs``, always producing a Bool ``Val``.

    Three routes, and `Compare.via_obj_cmp` -- decided by the frontend (R4,
    F.1.2/T5) and never re-derived here -- picks the first:

    * **`obj_cmp`** for every HOST_OBJECT-repr type AND for `Symbol`, whose
      packed 6-bit alphabet orders differently from the ASCII bytes tier 1
      pins. Both sides go in as `Val` WORDS (that is what the host compares),
      the result is a raw signed -1/0/1 (`val_typed_ret` is `False`, B2), and
      the operator becomes a SIGNED comparison of that against zero.
    * **`{u,i}128_cmp`** for the 128-bit widths, which have no single-word
      form; same -1/0/1 shape, same signed test against zero.
    * a **direct relop** otherwise -- and both sides are unboxed FIRST
      (F.1.1), with the relop's signedness read off the OPERAND type
      (F.1.11), never off the representation.
    """
    if e.via_obj_cmp:
        lower_expr(fn, ctx, e.lhs)
        lower_expr(fn, ctx, e.rhs)
        fn.call_import(ctx.host_import_name("obj_cmp"), 2, has_result=True)
        _compare_against_zero(fn, e.op)
        return

    ty = e.lhs.ty
    if ty.tag in _WIDE_TAGS:
        lower_expr(fn, ctx, e.lhs)
        lhs_hi, lhs_lo = arith.unbox_wide(fn, ctx, ty)
        lower_expr(fn, ctx, e.rhs)
        rhs_hi, rhs_lo = arith.unbox_wide(fn, ctx, ty)
        for slot in (lhs_hi, lhs_lo, rhs_hi, rhs_lo):
            fn.local_get(slot)
        arith.wide_cmp(fn, ctx, ty)
        _compare_against_zero(fn, e.op)
        return

    lower_expr_raw(fn, ctx, e.lhs)
    lower_expr_raw(fn, ctx, e.rhs)
    table = _SIGNED_RELOP if ty.tag in _SIGNED_TAGS else _UNSIGNED_RELOP
    fn.relop_i64(table[e.op])
    _bool_from_flag(fn)


def _compare_against_zero(fn: Fn, op: CompareOp) -> None:
    """A raw -1/0/1 on the stack -> the Bool ``Val`` the operator asked for.

    Always the SIGNED relop: the three-way answer is a signed number whatever
    the operands were, and `-1 <u 0` is false.
    """
    fn.i64_const(0)
    fn.relop_i64(_SIGNED_RELOP[op])
    _bool_from_flag(fn)


# --- BoolOp / IfExp -----------------------------------------------------------------


def _lower_boolop(fn: Fn, ctx: LowerCtx, e: BoolOp, index: int) -> None:
    """``and``/``or`` as nested ``if (result i64)`` chains -- SHORT-CIRCUIT (C8).

    `and` stops at the first false and `or` at the first true, and the operand
    that decided it is the last one evaluated. That is semantics, not an
    optimization: an operand can trap, fail with a contract error, or spend
    budget, all of which are observable on chain.

    The result is the surviving OPERAND's value, matching Python: for Bools
    (the only operands E9 allows) that is the same as the boolean answer, so
    the short-circuit arms can be the constants 0 and 1.
    """
    if len(e.operands) < 2:
        raise EmitError(f"a BoolOp needs at least two operands, got {len(e.operands)}")
    if index == len(e.operands) - 1:
        lower_expr(fn, ctx, e.operands[index])
        return
    lower_condition(fn, ctx, e.operands[index])
    fn.begin_if("i64")
    if e.op is BoolOpKind.AND:
        _lower_boolop(fn, ctx, e, index + 1)
        fn.else_()
        fn.i64_const(val.FALSE_VAL)
    else:
        fn.i64_const(val.TRUE_VAL)
        fn.else_()
        _lower_boolop(fn, ctx, e, index + 1)
    fn.end_if()


def _lower_if_exp(fn: Fn, ctx: LowerCtx, e: IfExp) -> None:
    """``then if cond else orelse`` with LAZY arms.

    An `if (result i64)`, never `select`: `select` evaluates both arms (SS
    B.2's ban), and an unevaluated arm here may be a division, a host call, or
    anything else with an effect.

    The storage `get(k, T, default=d)` shape is intercepted FIRST -- see
    `_lower_storage_get_with_default`, which is the same node this `IfExp`
    describes, lowered with its key evaluated once (review B7).
    """
    covered_get = guarded_storage_get(e)
    if covered_get is not None:
        _lower_storage_get_with_default(fn, ctx, e, covered_get)
        return
    lower_condition(fn, ctx, e.cond)
    fn.begin_if("i64")
    lower_expr(fn, ctx, e.then)
    fn.else_()
    lower_expr(fn, ctx, e.orelse)
    fn.end_if()


# --- the ABI check bodies: ONE table, two positions (E14 + S3, review M9) ---------
#
# The prologue checks an incoming ARGUMENT (S3's first sentence) and `narrow_to`
# checks a host RESULT (its second). They are the same question -- "is this Val
# really the type the program says it is?" -- so they are the same code:
# `abi_check` reads its operand from a LOCAL, and the two callers differ only in
# which local that is. Review M9 killed the per-type `tagcheck_*`/`narrow_*`
# parts for the same reason it would have killed two copies of this table: an
# "exact object-tag compare" is ~8 instructions, which loses against 74
# instructions of call overhead (S25), and two implementations of one rule
# diverge with nothing to say which half is the weaker check.

#: The tag byte of a `Val` (spec SS 10) and, one field wider, the tag PLUS the
#: minor field -- which is what a U32/I32 immediate check compares (m8, below).
_TAG_MASK = 0xFF
_MINOR_AND_TAG_MASK = 0xFFFF_FFFF

#: `U32`/`I32`: the whole low 32 bits of a well-formed immediate, tag included.
_IMMEDIATE_ABI_WORD: dict[TyTag, int] = {TyTag.U32: val.TAG_U32, TyTag.I32: val.TAG_I32}

#: Every EITHER-repr type's two legal tags -- the small form and the object
#: form (A3). Which one a VALUE takes depends on its magnitude (or, for
#: `Symbol`, its length), so a check that accepted only one of them would
#: reject perfectly valid arguments above the bound.
_EITHER_ABI_TAGS: dict[TyTag, tuple[int, int]] = {
    TyTag.U64: (val.TAG_U64_SMALL, val.TAG_U64_OBJECT),
    TyTag.I64: (val.TAG_I64_SMALL, val.TAG_I64_OBJECT),
    TyTag.TIMEPOINT: (val.TAG_TIMEPOINT_SMALL, val.TAG_TIMEPOINT_OBJECT),
    TyTag.DURATION: (val.TAG_DURATION_SMALL, val.TAG_DURATION_OBJECT),
    TyTag.U128: (val.TAG_U128_SMALL, val.TAG_U128_OBJECT),
    TyTag.I128: (val.TAG_I128_SMALL, val.TAG_I128_OBJECT),
    TyTag.SYMBOL: (val.TAG_SYMBOL_SMALL, val.TAG_SYMBOL_OBJECT),
}

#: The types that are ALWAYS an object handle: one exact tag compare each.
#: `Struct` shares `Map`'s tag because a struct IS a `Map<Symbol, V>` on chain
#: (S9) -- the same ScVal case, not a lookalike.
_OBJECT_ABI_TAG: dict[TyTag, int] = {
    TyTag.STRING: val.TAG_STRING_OBJECT,
    TyTag.BYTES: val.TAG_BYTES_OBJECT,
    TyTag.ADDRESS: val.TAG_ADDRESS_OBJECT,
    TyTag.VEC: val.TAG_VEC_OBJECT,
    TyTag.MAP: val.TAG_MAP_OBJECT,
    TyTag.STRUCT: val.TAG_MAP_OBJECT,
}

#: Every ``TyTag`` ``abi_check`` has a check body for, DERIVED from the three
#: tables above plus the four rows whose check is hand-written (``Bool``'s single
#: unsigned compare, ``BytesN``'s part, ``Option``'s composition). Exported so
#: the per-type accept/reject matrix in ``test_emitter_lower_stmts.py`` can
#: police itself: a row added to a table here joins this set automatically and
#: fails that matrix until it is exercised. A test also pins the set HONEST in
#: both directions -- every tag in it emits a check, every tag outside it raises.
ABI_CHECKED_TAGS: frozenset[TyTag] = frozenset(
    {TyTag.BOOL, TyTag.BYTES_N, TyTag.OPTION}
    | _IMMEDIATE_ABI_WORD.keys()
    | _EITHER_ABI_TAGS.keys()
    | _OBJECT_ABI_TAG.keys()
)

#: The one type whose check needs a HOST call, and therefore the one that is a
#: runtime part rather than an inline sequence (review M9).
_TAGCHECK_BYTES_N = "tagcheck_bytes_n"


def _fail_abi(fn: Fn, ctx: LowerCtx) -> None:
    """``fail_with_error(CODE_ABI_CHECK_FAILED)`` -- ONE code, every position (C19).

    Which argument failed is a message/trap-context concern, not a code
    concern, so there is nothing per-position to encode here. No ``unreachable``
    follows (P14): the host does not return from ``fail_with_error``, and an
    ``unreachable`` would replace the contract error a client needs to see with
    a generic VM trap.
    """
    fn.i64_const(val.error_val(errors.CODE_ABI_CHECK_FAILED))
    fn.call_import(ctx.host_import_name(_FAIL_WITH_ERROR_FN), 1, has_result=True)
    fn.drop()


def _fail_abi_if(fn: Fn, ctx: LowerCtx) -> None:
    """Consume the i32 flag on the stack; abort when it says the check failed."""
    fn.begin_if(None)
    _fail_abi(fn, ctx)
    fn.end_if()


def _ne_flag(fn: Fn, slot: int, value: int) -> None:
    """``local[slot] != value`` as an i64 0/1 flag, so flags compose with ``and``."""
    fn.local_get(slot)
    fn.i64_const(value)
    fn.relop_i64(opcodes.I64_NE)
    _bool_from_flag(fn)


def _truthy(fn: Fn) -> None:
    """An i64 0/1 flag on the stack -> the i32 boolean an ``if`` wants."""
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_NE)


def abi_check(fn: Fn, ctx: LowerCtx, slot: int, ty: Ty) -> None:
    """Emit ``ty``'s tag AND range check over the ``Val`` in local ``slot``.

    Stack-neutral, and THE per-type table ruling E14 asks for -- option (b),
    "tag and range check per param", inline everywhere except the one row whose
    check contains a host call. Failure is always ``CODE_ABI_CHECK_FAILED``.

    The rows, and what "range" means in each:

    * ``Bool`` -- the word is *literally* 0 or 1 (``val.pack_bool``), so one
      unsigned compare is both the tag check and the range check.
    * ``U32``/``I32`` -- ``(w & 0xFFFF_FFFF) == TAG``, which tests the tag byte
      **and** a zero minor field in one compare. Deliberately STRICTER than a
      tag test (review m8): a nonzero minor is not a valid ``U32Val``/``I32Val``
      encoding -- ``val.pack_u32val``/``pack_i32val`` always set minor 0, and so
      does the host's own ``U32Val::from(u32)`` -- so nothing the host can
      produce is rejected by the extra strictness, while a hand-rolled word
      carrying junk in the minor field is.
    * every EITHER-repr type -- the tag is one of TWO legal values (A3), so it
      takes two compares. Accepting only the small form would reject every
      value above its 56-bit bound; accepting only the object form would reject
      every value below it.
    * ``String``/``Bytes``/``Address``/``Vec``/``Map``/``Struct`` -- one exact
      object-tag compare, INLINE (review M9).
    * ``BytesN(n)`` -- the one part, because only a ``bytes_len`` call can tell
      a 31-byte payload from a 32-byte one.
    * ``Option[T]`` -- ``VOID_VAL`` or ``T``'s own check, COMPOSED rather than
      tabulated, so an ``Option`` can never drift from the type it wraps.
    """
    tag = ty.tag
    if tag is TyTag.BOOL:
        fn.local_get(slot)
        fn.i64_const(val.TRUE_VAL)
        fn.relop_i64(opcodes.I64_GT_U)
        _fail_abi_if(fn, ctx)
        return

    immediate = _IMMEDIATE_ABI_WORD.get(tag)
    if immediate is not None:
        fn.local_get(slot)
        fn.i64_const(_MINOR_AND_TAG_MASK)
        fn.binop_i64(opcodes.I64_AND)
        fn.i64_const(immediate)
        fn.relop_i64(opcodes.I64_NE)
        _fail_abi_if(fn, ctx)
        return

    either = _EITHER_ABI_TAGS.get(tag)
    if either is not None:
        small, obj = either
        tag_slot = fn.new_local()
        fn.local_get(slot)
        fn.i64_const(_TAG_MASK)
        fn.binop_i64(opcodes.I64_AND)
        fn.local_set(tag_slot)
        # Fail iff the tag is NEITHER -- one `and` of two "not this one" flags,
        # never two separate `if`s (which would fail on the legal tag too).
        _ne_flag(fn, tag_slot, small)
        _ne_flag(fn, tag_slot, obj)
        fn.binop_i64(opcodes.I64_AND)
        _truthy(fn)
        _fail_abi_if(fn, ctx)
        return

    object_tag = _OBJECT_ABI_TAG.get(tag)
    if object_tag is not None:
        fn.local_get(slot)
        fn.i64_const(_TAG_MASK)
        fn.binop_i64(opcodes.I64_AND)
        fn.i64_const(object_tag)
        fn.relop_i64(opcodes.I64_NE)
        _fail_abi_if(fn, ctx)
        return

    if tag is TyTag.BYTES_N:
        if ty.n is None:  # pragma: no cover - `Ty.BytesN` always sets it
            raise EmitError("a BytesN Ty carries no length")
        fn.local_get(slot)
        # A RAW u32, not a `U32Val`: the part compares it against the unboxed
        # `bytes_len` result, and boxing it would be wrong by 2**32 (C13).
        fn.i64_const(ty.n)
        fn.call_defined(ctx.ensure_part(_TAGCHECK_BYTES_N), 2, ("i64",))
        # The part returns the value it checked so it is usable from a stack
        # position too; here the value is already in `slot`, so the result is
        # dropped rather than re-stored.
        fn.drop()
        return

    if tag is TyTag.OPTION:
        if ty.elem is None:  # pragma: no cover - `Ty.Option` always sets it
            raise EmitError("an Option Ty carries no element type")
        fn.local_get(slot)
        fn.i64_const(val.VOID_VAL)
        fn.relop_i64(opcodes.I64_NE)
        fn.begin_if(None)
        abi_check(fn, ctx, slot, ty.elem)
        fn.end_if()
        return

    raise EmitError(
        f"no ABI check for {ty.render()}; ruling E14's per-type table is exhaustive "
        "over the types that can cross the ABI boundary, and a type with no check "
        "must never pass one unchecked (F.1.15)"
    )


#: The one result type `narrow_to` does not check -- see its docstring.
_UNNARROWED_TAGS: frozenset[TyTag] = frozenset({TyTag.VOID})


def narrow_to(fn: Fn, ctx: LowerCtx, ty: Ty) -> None:
    """Check the ``Val`` on the stack top against ``ty``, leaving it in place.

    S3's second sentence: "every host-call return typed narrower than ``Val``
    is checked the same way" as an incoming argument. A host function that
    returns a `Val` returns an ANY-typed one -- `map_get` on a struct hands
    back whatever was stored, `get_contract_data` whatever the ledger holds --
    and the checker's `ty` for that expression is a CLAIM about the value, not
    a proof. Without this, a corrupted ledger entry becomes a mis-typed read
    that flows on as if it were fine: an `I32Val` where the program says `U32`
    unboxes to a different number with no error anywhere.

    It is called from every position that produces such a result -- the generic
    `HostCall`, both storage-get arms, and `FieldGet`.

    The check body is literally the prologue's (`abi_check`, which reads its
    operand from a local), applied to a hidden local the value is `local.tee`d
    into. The value stays on the stack, so this is net +1 i64 like every other
    step of the expression it belongs to (review M1).

    **`Void` is the one exemption**, and the reason is narrow: a Void `Val` is
    dropped by the statement that produced it (`Eval`, P14), so a wrong tag
    there has nowhere to flow -- while checking it would put ~6 instructions
    after every storage write in the module.
    """
    if ty.tag in _UNNARROWED_TAGS:
        return
    slot = fn.new_local()
    fn.local_tee(slot)
    abi_check(fn, ctx, slot, ty)


def _host_fn(name: str) -> HostFn:
    """The pinned binding for ``name`` -- B2: looked up, never re-derived."""
    spec = functions_by_name.get(name)
    if spec is None:
        raise EmitError(
            f"{name!r} is not a host function in the pin "
            "(serpent._host.functions_by_name); the IR names host functions BY NAME (B2)"
        )
    return spec


# --- HostCall ---------------------------------------------------------------------


def _lower_host_call(fn: Fn, ctx: LowerCtx, e: HostCall) -> None:
    """One host call, arguments positionally, each Val or RAW per the PIN (B2).

    `HostFn.val_typed_args` is read, never re-derived: it is the pin's own
    per-position answer, and the two positions where it says `False` today are
    the storage `StorageType` immediate and the TTL thresholds (C13). Lowering
    one of those as a `Val` would pass a `U32Val` where the host reads a plain
    number -- an argument that is wrong by a factor of 2^32 and still
    validates.

    A `get_contract_data` reaching HERE is by definition one no `has` covered
    (`_lower_if_exp` intercepts the other shape), so it takes the guard.
    """
    if e.fn_name == _STORAGE_GET_FN:
        _lower_guarded_storage_get(fn, ctx, e)
        return
    _lower_host_call_raw(fn, ctx, e.fn_name, e.args)
    narrow_to(fn, ctx, e.ty)


def _lower_host_call_raw(fn: Fn, ctx: LowerCtx, name: str, args: tuple[IRExpr, ...]) -> None:
    """The call itself, with no guard and no narrowing check around it."""
    val_typed = _host_fn(name).val_typed_args
    if len(args) != len(val_typed):
        raise EmitError(
            f"{name} takes {len(val_typed)} argument(s), got {len(args)}; the IR's "
            "arity comes from the recognition table and the pin agrees or one of "
            "them is wrong"
        )
    for arg, is_val in zip(args, val_typed, strict=True):
        if is_val:
            lower_expr(fn, ctx, arg)
        else:
            # C13: a raw scalar position. `lower_expr_raw` of a `RawScalar` is
            # the number itself; of anything else it is the unboxed word.
            lower_expr_raw(fn, ctx, arg)
    fn.call_import(ctx.host_import_name(name), len(args), has_result=True)


# --- the storage guard (ruling E13, review B7/M12) --------------------------------


def _hoist_storage_args(fn: Fn, ctx: LowerCtx, args: tuple[IRExpr, ...]) -> tuple[int, int]:
    """Evaluate a storage call's ``(key, storage_type)`` ONCE, into two locals.

    Review B7 is the whole point. Both the guarded form and the with-default
    form name the key TWICE in the emitted code -- once for `has`, once for
    `get` -- and lowering the key expression at each of those sites would
    evaluate it twice. A key is an ordinary expression: it can be a call, so a
    second evaluation is a second set of effects and a second charge against
    the budget. Tier 1 evaluates it once, so the emitted code must too.

    The storage-type immediate is hoisted the same way rather than re-emitted.
    It is a `RawScalar` in everything the frontend builds (C13) and re-emitting
    a constant would be harmless -- but "harmless because of what the frontend
    happens to build" is the assumption that stops being true quietly, and one
    extra `local.get` is not a price worth paying to find out.
    """
    if len(args) != 2:
        raise EmitError(f"a storage call takes (key, storage_type), got {len(args)} argument(s)")
    key, storage_type = args
    key_slot = fn.new_local()
    lower_expr(fn, ctx, key)
    fn.local_set(key_slot)
    type_slot = fn.new_local()
    lower_expr_raw(fn, ctx, storage_type)
    fn.local_set(type_slot)
    return key_slot, type_slot


def _call_storage(fn: Fn, ctx: LowerCtx, name: str, key_slot: int, type_slot: int) -> None:
    """``name(key, storage_type)`` from the two hoisted locals."""
    fn.local_get(key_slot)
    fn.local_get(type_slot)
    fn.call_import(ctx.host_import_name(name), 2, has_result=True)


def _lower_guarded_storage_get(fn: Fn, ctx: LowerCtx, e: HostCall) -> None:
    """A bare ``get_contract_data``, wrapped in its presence guard (E13/E14).

    `get_contract_data` on a key the ledger does not hold is UNDEFINED at the
    host: env.json promises nothing, so a contract that reads a missing key
    has no defined behaviour to preserve. Serpent gives it one -- a contract
    error carrying `CODE_MISSING_VALUE` -- by asking `has_contract_data` first
    and calling `fail_with_error` when the answer is no.

    The shape is `if not has: fail`, with no `else` arm, so the `get` itself is
    emitted once on the straight line afterwards. `fail_with_error` "does not
    actually return" (env.json), but wasm does not know that: its result is a
    `Val` on the operand stack, and the `drop` is what keeps the frame
    balanced for the validator.
    """
    key_slot, type_slot = _hoist_storage_args(fn, ctx, e.args)

    _call_storage(fn, ctx, _STORAGE_HAS_FN, key_slot, type_slot)
    # `has` answers with a Bool Val, which IS the word 0 or 1 -- so `eqz` is
    # exactly "it was absent", with no comparison against a tag.
    _eqz(fn)
    fn.begin_if(None)
    fn.i64_const(val.error_val(errors.CODE_MISSING_VALUE))
    fn.call_import(ctx.host_import_name(_FAIL_WITH_ERROR_FN), 1, has_result=True)
    fn.drop()
    fn.end_if()

    _call_storage(fn, ctx, _STORAGE_GET_FN, key_slot, type_slot)
    narrow_to(fn, ctx, e.ty)


def _lower_storage_get_with_default(fn: Fn, ctx: LowerCtx, e: IfExp, get: HostCall) -> None:
    """``get(k, T, default=d)``: ``has`` decides, and NOTHING is guarded (E13).

    C's own `has` is the guard. Emitting the generic one inside the `then` arm
    would call `has_contract_data` twice for one read -- a second ledger access
    and a second charge, for a question already answered on the line above.

    The key is evaluated ONCE (review B7) into a hidden local shared by both
    calls, which is also why this cannot be assembled out of the generic
    `IfExp` lowering plus the generic `HostCall` one: those two would lower the
    `cond`'s key and the `then`'s key independently, and the fact that they are
    the SAME node (`guarded_storage_get`'s identity test) would buy nothing.
    """
    cond = e.cond
    if not isinstance(cond, HostCall):  # pragma: no cover - the predicate proved it
        raise EmitError("a with-default storage get must have a HostCall condition")
    key_slot, type_slot = _hoist_storage_args(fn, ctx, cond.args)

    _call_storage(fn, ctx, _STORAGE_HAS_FN, key_slot, type_slot)
    _wrap(fn)
    fn.begin_if("i64")
    _call_storage(fn, ctx, _STORAGE_GET_FN, key_slot, type_slot)
    narrow_to(fn, ctx, get.ty)
    fn.else_()
    lower_expr(fn, ctx, e.orelse)
    fn.end_if()


# --- InternalCall (E8, E11ii) -----------------------------------------------------


def _lower_internal_call(fn: Fn, ctx: LowerCtx, e: InternalCall) -> None:
    """A call to one of the module's own functions, by DEFINED-space index.

    `results` is read off the node's own `ty`: a `-> None` helper is compiled
    with zero results (E11ii, review M2), not with a `Void` `Val` nobody drops.
    An `Eval` of one therefore needs no `drop` -- which is Task 9's statement
    path, and is why that path must open a VOID expression scope for this node.
    """
    defidx = ctx.functions.get(e.fn_name)
    if defidx is None:
        raise EmitError(
            f"InternalCall({e.fn_name!r}) has no entry in LowerCtx.functions; the "
            "module's own functions are indexed in CompiledModule order, and a "
            "helper the frontend accepted must be in that table"
        )
    for arg in e.args:
        lower_expr(fn, ctx, arg)
    results: tuple[str, ...] = () if e.ty.tag is TyTag.VOID else ("i64",)
    fn.call_defined(defidx, len(e.args), results)


# --- MakeStruct (P1's asymmetric pair, C9's order) --------------------------------


def _lower_make_struct(fn: Fn, ctx: LowerCtx, e: MakeStruct) -> None:
    """A struct literal: a COMPILE-TIME keys blob + a RUNTIME values array (P1).

    The asymmetry is the thing to get right, and it is not symmetric-looking:
    `map_new_from_linear_memory`'s keys array holds 8-byte
    `(u32 pointer, u32 length)` descriptors of the field-name BYTES -- not
    `Symbol` `Val`s -- so it is entirely compile-time data and lives in the
    data segment. The values array holds 8-byte `Val` WORDS and is written at
    run time into scratch. "The wrong layout validates and then panics
    on-chain."

    **The field order is C's (C9/P7) and is never re-sorted here.** The host
    requires the key descriptors ascending as byte strings and panics
    otherwise; `MakeStruct.fields` already is, and re-sorting would be a second
    opinion that can only ever disagree. `tests/harness/objects.py` enforces the
    ascending invariant, so a lowering that reordered them fails locally rather
    than on chain (F.1.5).

    The blob is `intern`ed rather than appended: Task 10 has already SEEDED the
    identical bytes from `struct_key_descriptor_sets` (E7), so this call is a
    hit and returns the seeded offset. Interning it again with the same recipe
    is what makes that a fact rather than a hope -- a drift between the two
    would show up as a second copy in the pool, not as silent corruption.
    """
    n = len(e.fields)
    if n == 0:
        raise EmitError(
            f"struct {e.struct_name} has no fields; `map_new_from_linear_memory` "
            "over an empty key array has nothing to describe"
        )
    descriptor = bytearray()
    for name, _value in e.fields:
        name_bytes = name.encode("utf-8")
        descriptor += _struct.pack("<II", ctx.memory.intern(name_bytes), len(name_bytes))
    keys_off = ctx.memory.intern(bytes(descriptor), align=8)

    vals_off = ctx.memory.scratch(8 * n)
    for i, (_name, value) in enumerate(e.fields):
        _store_val(fn, ctx, vals_off + 8 * i, value)

    fn.i64_const(val.pack_u32val(keys_off))
    fn.i64_const(val.pack_u32val(vals_off))
    fn.i64_const(val.pack_u32val(n))
    fn.call_import(ctx.host_import_name(_MAP_LM_FN), 3, has_result=True)


def _store_val(fn: Fn, ctx: LowerCtx, address: int, e: IRExpr) -> None:
    """``i64.store`` one lowered ``Val`` word at a fixed scratch address.

    The address is an `i32.const` because it is known at compile time: scratch
    is a compile-time bump allocator over a fixed pool/scratch split (P12), so
    nothing later moves it. Alignment 3 is `2**3 == 8` bytes, which every
    scratch reservation is aligned to.
    """
    fn.i32_const(address)
    lower_expr(fn, ctx, e)
    fn.pop("i64")
    fn.pop("i32")
    fn.op(opcodes.I64_STORE, encode.uleb(3), encode.uleb(0))


# --- FieldGet ---------------------------------------------------------------------


def _lower_field_get(fn: Fn, ctx: LowerCtx, e: FieldGet) -> None:
    """``map_get(obj, Symbol(field))`` -- a struct IS a map on chain (S9).

    The key's form has to match C's own accounting exactly
    (`frontend.py`'s `_collect_host_fns`): 9 characters or fewer is a
    `SymbolSmall` immediate and reaches no host function, anything longer is a
    pooled literal and reaches `symbol_new_from_linear_memory` (S22). C already
    decided which, and put the constructor in `host_fns_used` accordingly -- so
    disagreeing here means either an import the module never calls or, worse, a
    call to an import that was never declared.
    """
    lower_expr(fn, ctx, e.obj)
    if val.fits_symbol_small(e.field):
        fn.i64_const(val.symbol_small(e.field))
    else:
        _lower_pooled(fn, ctx, TyTag.SYMBOL, e.field.encode("utf-8"))
    fn.call_import(ctx.host_import_name(_MAP_GET_FN), 2, has_result=True)
    narrow_to(fn, ctx, e.ty)


# --- the compile-time Val word (C4/E12's shared gate) -----------------------------


def _static_word(e: IRExpr) -> int | None:
    """``e``'s ``Val`` word if it is knowable at COMPILE time, else ``None``.

    This is C4's actual test, and it is narrower than `all_static`.
    `MakeVec.all_static` says "every item is a literal C validated"; it does
    NOT say those literals have an inline `Val` form. A `String` item is a
    perfectly static literal whose `Val` is an object handle that does not
    exist until `string_new_from_linear_memory` has run, so there is no word to
    lay in the data section for it -- and an EITHER-repr item is a word only
    below its 56-bit bound (A3). `None` here means the build-up chain, which is
    always available and always correct.
    """
    if not isinstance(e, Const):
        return None
    ty = e.ty
    if e.py_value is None:
        return val.VOID_VAL
    if ty.tag is TyTag.OPTION:
        assert ty.elem is not None
        return _static_word(Const(loc=e.loc, ty=ty.elem, py_value=e.py_value))
    if ty.tag is TyTag.BOOL:
        return val.pack_bool(_as_bool(e))
    if ty.tag is TyTag.U32:
        return val.pack_u32val(_as_int(e))
    if ty.tag is TyTag.I32:
        return val.pack_i32val(_as_int(e))
    if ty.tag is TyTag.SYMBOL:
        text = _as_str(e)
        return val.symbol_small(text) if val.fits_symbol_small(text) else None
    spec = _SMALL64.get(ty.tag) or _SMALL128.get(ty.tag)
    if spec is not None:
        tag, _constructor, signed = spec
        value = _as_int(e)
        if signed:
            return val.pack_small_i64(value, tag) if val.fits_small_i(value) else None
        return val.pack_small_u64(value, tag) if val.fits_small_u(value) else None
    return None


def _static_words(items: tuple[IRExpr, ...]) -> list[int] | None:
    """Every item's compile-time ``Val`` word, or ``None`` if any has none."""
    if not items:
        return None
    words: list[int] = []
    for item in items:
        word = _static_word(item)
        if word is None:
            return None
        words.append(word)
    return words


def _pack_words(words: list[int]) -> bytes:
    """The 8-byte little-endian ``Val`` array the host reads out of memory."""
    return b"".join(_struct.pack("<Q", val.as_u64(word)) for word in words)


# --- MakeVec / MakeTopics (ruling C4) ---------------------------------------------


def _lower_make_vec(fn: Fn, ctx: LowerCtx, items: tuple[IRExpr, ...], all_static: bool) -> None:
    """A vector, by whichever of the two forms is available (C4).

    The linear-memory form needs BOTH of C's flag and D's own layout question
    answered yes -- see `_static_word`. When it is available the whole vector
    is data-section bytes and one host call; when it is not, `vec_new` plus a
    `vec_push_back` per item.

    **Every mutator result is REBOUND** (F.1.9). Host objects are immutable:
    `vec_push_back` does not modify the vector, it returns a NEW handle to a
    longer one. Dropping that result and pushing onto the old handle again
    builds a one-element vector n times over and reports success -- the exact
    silent-wrong-answer shape F.1.9 names -- so the handle lives in a hidden
    local that every push reads and rewrites.
    """
    words = _static_words(items) if all_static else None
    if words is not None:
        offset = ctx.memory.intern(_pack_words(words), align=8)
        fn.i64_const(val.pack_u32val(offset))
        fn.i64_const(val.pack_u32val(len(words)))
        fn.call_import(ctx.host_import_name(_VEC_LM_FN), 2, has_result=True)
        return

    fn.call_import(ctx.host_import_name(_VEC_NEW_FN), 0, has_result=True)
    slot = fn.new_local()
    fn.local_set(slot)
    for item in items:
        fn.local_get(slot)
        lower_expr(fn, ctx, item)
        fn.call_import(ctx.host_import_name(_VEC_PUSH_FN), 2, has_result=True)
        fn.local_set(slot)
    fn.local_get(slot)


# --- MakeMap (ruling E12) ---------------------------------------------------------


def _lower_make_map(fn: Fn, ctx: LowerCtx, e: MakeMap) -> None:
    """A map literal: the descriptor form only where the CONTRACT allows it (E12).

    `map_new_from_linear_memory`'s keys are `(ptr, len)` descriptors of byte
    strings "convertible to `Symbol` type" (P1) -- so the form exists for
    `Symbol` keys and for nothing else. A `Map[U32, U32]` literal is fully
    static and still takes the chain: `all_static` says D *may* lay the
    values out, not that the KEYS have a descriptor form at all. That is
    ruling E12, and it is the one place where reading `all_static` as a licence
    would produce a module that validates and panics.

    The keys must also be strictly ascending as byte strings. C ordered them
    (A8/A14 through the tier-1 oracle), so this checks rather than sorts: a
    disagreement is a compiler bug in one of the two, and the loud version of
    it is an `EmitError` here instead of a host panic on chain.

    Values are stored to scratch at run time in the node's pair order -- they
    are arbitrary `Val`s, including object handles that do not exist until
    their own expression has run.

    The fallback is `map_new` + a `map_put` per pair in pair order (F.1.10),
    with the handle rebound each time for the same reason `vec_push_back` is
    (F.1.9): the host returns a new map, it does not mutate the old one.
    """
    keys = [key for key, _value in e.pairs]
    names = _symbol_key_names(keys) if e.key_ty.tag is TyTag.SYMBOL else None

    if e.all_static and names is not None:
        if not e.pairs:
            # The same refusal `_lower_make_struct` makes, for the same reason:
            # `map_new_from_linear_memory` over a zero-length key array has
            # nothing to describe, and `intern(b"")` + `scratch(0)` would hand
            # the host two offsets pointing at no data. Unreachable from
            # frontend IR (`recognize._all_static` answers False for an empty
            # container, MJ-15), so this is the asymmetry closing rather than a
            # live path.
            raise EmitError(
                "an empty map literal cannot use `map_new_from_linear_memory`; "
                "`map_new` with no puts is the lowering for it"
            )
        _check_ascending(names, e.pairs)
        descriptor = bytearray()
        for name_bytes in names:
            descriptor += _struct.pack("<II", ctx.memory.intern(name_bytes), len(name_bytes))
        keys_off = ctx.memory.intern(bytes(descriptor), align=8)
        vals_off = ctx.memory.scratch(8 * len(e.pairs))
        for i, (_key, value) in enumerate(e.pairs):
            _store_val(fn, ctx, vals_off + 8 * i, value)
        fn.i64_const(val.pack_u32val(keys_off))
        fn.i64_const(val.pack_u32val(vals_off))
        fn.i64_const(val.pack_u32val(len(e.pairs)))
        fn.call_import(ctx.host_import_name(_MAP_LM_FN), 3, has_result=True)
        return

    fn.call_import(ctx.host_import_name(_MAP_NEW_FN), 0, has_result=True)
    slot = fn.new_local()
    fn.local_set(slot)
    for key, value in e.pairs:
        fn.local_get(slot)
        lower_expr(fn, ctx, key)
        lower_expr(fn, ctx, value)
        fn.call_import(ctx.host_import_name(_MAP_PUT_FN), 3, has_result=True)
        fn.local_set(slot)
    fn.local_get(slot)


def _symbol_key_names(keys: list[IRExpr]) -> list[bytes] | None:
    """The key-name bytes when every key is a ``Symbol`` literal, else ``None``.

    A `Symbol` key that is not a `Const` -- a parameter, a local, a `ConstRef`
    -- has no name at compile time, so there is no descriptor to write and the
    chain is the only form (the m.9 descriptor contract, P1).

    The per-key `SYMBOL` test duplicates the caller's check on `MakeMap.key_ty`
    on purpose: `key_ty` is the map's DECLARED key type and each key node
    carries its own, and the descriptor form is unsafe unless both say Symbol.
    """
    names: list[bytes] = []
    for key in keys:
        if not isinstance(key, Const) or key.ty.tag is not TyTag.SYMBOL:
            return None
        names.append(_as_str(key).encode("utf-8"))
    return names


def _check_ascending(names: list[bytes], pairs: tuple[tuple[IRExpr, IRExpr], ...]) -> None:
    """Refuse a descriptor blob the host would panic on (P1/F.1.5).

    C sorts map-literal keys through the tier-1 oracle (A8/A14/MJ-15) and
    proves them unique, so this never fires on a frontend-built node. It exists
    because the failure it prevents is invisible: a descending pair of
    descriptors validates as wasm, passes every structural check, and panics
    inside the host. Loud here beats undiagnosable there.
    """
    previous = b""
    for name in names:
        if name <= previous:
            raise EmitError(
                f"map literal keys are not strictly ascending as byte strings: "
                f"{name!r} follows {previous!r} in a {len(pairs)}-entry literal. "
                "`map_new_from_linear_memory` PANICS on this (P1); C orders and "
                "de-duplicates static keys (A8/A14), so the two disagree"
            )
        previous = name


# --- unboxing ---------------------------------------------------------------------


def _unbox(fn: Fn, ctx: LowerCtx, ty: Ty) -> None:
    """One ``Val`` word on the stack -> its raw word, for ``ty``.

    ``Bool`` is the identity (its ``Val`` IS 0/1); everything else with a
    one-word raw form is ``arith.unbox``'s business, and ``arith`` refuses the
    128-bit widths with a message explaining that they travel as limb pairs.
    """
    if ty.tag in _RAW_IDENTITY_TAGS:
        return
    arith.unbox(fn, ctx, ty)


# --- statements (SS B.3.2) ---------------------------------------------------------


def lower_stmt(fn: Fn, ctx: LowerCtx, s: IRStmt) -> None:
    """Lower one statement. Every row of SS B.3.2, and a LOUD default (F.1.15)."""
    if isinstance(s, LetLocal):
        # SS C.3: params occupy `[0, nparams)`, so declared slot `s` is local
        # `nparams + s`. There are no uninitialized locals in serpent -- the
        # first binding IS the declaration -- so `LetLocal` and `SetLocal` are
        # the same two instructions and differ only in the checker's eyes.
        lower_expr(fn, ctx, s.init)
        fn.local_set(fn.nparams + s.slot)
    elif isinstance(s, SetLocal):
        lower_expr(fn, ctx, s.value)
        fn.local_set(fn.nparams + s.slot)
    elif isinstance(s, Eval):
        _lower_eval(fn, ctx, s)
    elif isinstance(s, If):
        _lower_if(fn, ctx, s)
    elif isinstance(s, While):
        _lower_while(fn, ctx, s)
    elif isinstance(s, Break):
        fn.br_break()
    elif isinstance(s, Continue):
        fn.br_continue()
    elif isinstance(s, Raise):
        _lower_raise(fn, ctx, s)
    elif isinstance(s, Return):
        _lower_return(fn, ctx, s)
    elif isinstance(s, Nop):
        return
    else:
        raise EmitError(
            f"no lowering for IR statement {type(s).__name__} ({s!r}); the statement "
            "dispatch is exhaustive over serpent.compiler.ir by design (F.1.15) -- a "
            "new statement kind must be added here, never silently skipped"
        )


def lower_body(fn: Fn, ctx: LowerCtx, body: tuple[IRStmt, ...]) -> None:
    """Lower a statement sequence in source order."""
    for s in body:
        lower_stmt(fn, ctx, s)


def _lower_eval(fn: Fn, ctx: LowerCtx, s: Eval) -> None:
    """A void expression evaluated for its EFFECT, and the ``drop`` question.

    A host function that "returns nothing" still returns a Void `Val` on the
    operand stack, and the `drop` is what keeps the frame balanced for the
    validator (P14) -- P2's named bug is exactly the missing one after
    `put_contract_data`.

    An INTERNAL call is the exception, and the only one: a `-> None` helper is
    compiled with ZERO results (E11ii, review M2), so there is nothing on the
    stack to discard and a `drop` here would pop an operand that was never
    pushed. That is also why this opens a VOID expression scope for it --
    `lower_expr`'s scope asserts net +1, which is the wrong assertion for a
    call that pushes nothing.
    """
    if isinstance(s.value, InternalCall) and s.value.ty.tag is TyTag.VOID:
        with fn.expr_scope(is_void=True):
            _lower_val(fn, ctx, s.value)
        return
    lower_expr(fn, ctx, s.value)
    fn.drop()


def _lower_if(fn: Fn, ctx: LowerCtx, s: If) -> None:
    """``if (void) ... else ... end`` -- frame discipline per P13.

    The `else` arm is emitted only when there is one: a VOID `if` is legal
    one-armed (unlike the result-bearing `if` an `IfExp` builds), and an empty
    `else` would be two bytes of nothing. `elif` needs no special case at all
    -- the checker already nested it as an `If` inside `orelse`.
    """
    lower_condition(fn, ctx, s.cond)
    fn.begin_if(None)
    lower_body(fn, ctx, s.body)
    if s.orelse:
        fn.else_()
        lower_body(fn, ctx, s.orelse)
    fn.end_if()


def _lower_while(fn: Fn, ctx: LowerCtx, s: While) -> None:
    """``block $exit { loop $head { !cond -> br_if $exit; body; br $head } }``.

    Both frames are VOID: multi-value is off (S23), so neither can carry a
    result, and the loop's own branch arity is then trivially empty.

    **The exit block MUST be opened ``breakable=True``** (Task 2's contract).
    `br_break` scans for the nearest block so marked and skips every other one,
    so a plain block opened inside the body for some unrelated reason cannot
    steal a `break` -- and a `While` that forgot the flag fails loudly at
    lowering time rather than emitting a branch to a plausible wrong label.

    The condition is lowered NEGATED (`lower_condition(negate=True)`), which is
    what makes the exit test one `br_if` rather than a branch around a branch.
    """
    fn.begin_block(None, breakable=True)
    fn.begin_loop()
    lower_condition(fn, ctx, s.cond, negate=True)
    fn.br_if_break()
    lower_body(fn, ctx, s.body)
    fn.br_continue()
    fn.end()
    fn.end()


def _lower_raise(fn: Fn, ctx: LowerCtx, s: Raise) -> None:
    """``fail_with_error(error_val(code))`` then ``drop``, and NOTHING after (P14).

    On-chain-verified: an `unreachable` after `fail_with_error` would replace
    the contract error the client needs to see with a generic VM trap, which is
    R3's "error codes are never lost to `unreachable`" broken in the one place
    it matters. The `drop` is not optional either -- the host does not return,
    but wasm does not know that, and the Void `Val` the call nominally leaves
    has to be accounted for.
    """
    fn.i64_const(val.error_val(s.code))
    fn.call_import(ctx.host_import_name(_FAIL_WITH_ERROR_FN), 1, has_result=True)
    fn.drop()


def _lower_return(fn: Fn, ctx: LowerCtx, s: Return) -> None:
    """``return <value>``, with the balance check at the ``return`` (P2).

    `value is None` is a bare `return` (or a `-> None` method's), and what it
    pushes depends on the function's RESULT ARITY, not on its source form
    (review M2): an EXPORT is `("i64",)` whatever it returns (S23), so it hands
    back the Void `Val`; a void INTERNAL helper is `()`, and pushing a value
    into one is invalid wasm.

    That substitution is only ever right for a function whose DECLARED return
    is `Void`. `compile_function` refuses a bare `return` in any other function
    up front (`_refuse_valueless_returns`) rather than letting this line hand a
    caller a Void `Val` wearing the declared type.
    """
    if s.value is None:
        if fn.results:
            fn.i64_const(val.VOID_VAL)
    else:
        lower_expr(fn, ctx, s.value)
    fn.ret()


# --- one whole function (SS B.3.3's FuncIR row) -----------------------------------

#: The kinds that cross the ABI boundary and therefore carry a prologue (E14).
#: An INTERNAL helper is called only by code this emitter produced, whose
#: arguments it already typed -- checking them again would be paying S3's cost
#: at a boundary that does not exist.
_ABI_KINDS: frozenset[FuncKind] = frozenset({FuncKind.EXPORT, FuncKind.CONSTRUCTOR})


def compile_function(func: FuncIR, ctx: LowerCtx) -> Fn:
    """One ``FuncIR`` -> a finished ``Fn``: prologue, body, tail.

    **Result arity** (S23, E11ii, review M2). An EXPORT or CONSTRUCTOR is
    `("i64",)` whatever it returns: multi-value is off and every Soroban `Val`
    is an i64, so a `-> None` method returns the Void `Val` rather than
    nothing. An INTERNAL helper is `()` when it returns `Void` and `("i64",)`
    otherwise -- a helper is not an ABI surface, so nothing forces a Void `Val`
    through it.

    **The tail** (ruling E4, review M2), reached only when the body did not
    already leave the function in the unreachable state:

    * `("i64",)` + a Void return -- the method fell off its end, so it hands
      back `VOID_VAL` (the spike's shape);
    * `()` -- nothing at all; `finish()` accepts the empty stack;
    * `("i64",)` + a NON-void return -- only a DIVERGING body can be here (C1's
      `while True:` with no `break`, or a body ending in `raise`), because C
      proved definite return on every other path (C16). Emit
      `fail_with_error(CODE_UNREACHABLE_GUARD)`, `drop`, then `unreachable`,
      **in that order**: the `unreachable` satisfies the validator, and the
      call before it is what keeps R3's promise that an error code is never
      lost to a bare trap. Reversed, the module still validates and still
      aborts -- with no code for a client to read.
    """
    if func.kind is FuncKind.CONSTRUCTOR and func.ret.tag is not TyTag.VOID:
        raise EmitError(
            f"constructor {func.export_name} returns {func.ret.render()}; S26 makes "
            "`__constructor` void (the host launders its errors to "
            "Context(InvalidAction)), and the frontend proves it -- so a "
            "value-returning one is a compiler bug"
        )
    if func.ret.tag is not TyTag.VOID:
        if not func.returns_on_every_path:
            raise EmitError(
                f"{func.export_name} returns {func.ret.render()} with "
                "returns_on_every_path=False; C's definite-return proof (C16/P6/S17) is "
                "what makes the tail rule sound, and a function that fails it never "
                "reaches the emitter"
            )
        _refuse_valueless_returns(func)

    results: tuple[str, ...] = ("i64",)
    if func.kind is FuncKind.INTERNAL and func.ret.tag is TyTag.VOID:
        results = ()

    fn = Fn(
        name=func.export_name,
        nparams=len(func.params),
        nlocals_declared=_nlocals_declared(func),
        results=results,
    )
    if func.kind in _ABI_KINDS:
        _abi_prologue(fn, ctx, func)
    lower_body(fn, ctx, func.body)
    _lower_tail(fn, ctx, func)
    return fn


def _refuse_valueless_returns(func: FuncIR) -> None:
    """A bare ``return`` inside a NON-void function is refused, never patched.

    `_lower_return` pushes `VOID_VAL` for `Return(value=None)` whenever the
    function has an i64 result, which is exactly right for a `-> None` EXPORT
    (S23 makes it `("i64",)` anyway) and exactly WRONG here: the caller would
    receive the Void `Val` typed as `U32`, `Address`, whatever the signature
    promised -- a plausible wrong answer with no error anywhere, which is the
    class F.1 exists to prevent.

    C's checker excludes the shape (a bare `return` cannot satisfy definite
    return for a value-returning function, P6/S17), so this is the same kind of
    assertion as C16's above: a compiler bug, refused loudly at the boundary
    rather than silently substituted deep inside the lowering.

    Whole-body, via `ir.walk`, because the offending `return` can be nested
    inside an `if` arm or a loop -- and because doing it here keeps
    `lower_stmt`'s signature the one the interface promises.
    """
    for node in walk(func):
        if isinstance(node, Return) and node.value is None:
            raise EmitError(
                f"{func.export_name} returns {func.ret.render()} but its body contains "
                f"a bare `return` at {node.loc.path}:{node.loc.line}; substituting "
                "VOID_VAL would "
                "hand the caller a Void Val typed as the declared return type, and C's "
                "definite-return analysis (P6/S17) already excludes the shape"
            )


def _nlocals_declared(func: FuncIR) -> int:
    """How many declared local slots the body can name -- and a contiguity check.

    `SlotTable` numbers slots in first-binding order (SS C.3), so they are
    `0..n-1` by construction. A GAP would mean the two disagree, and the
    consequence is not an error: `local_set(nparams + slot)` would still be in
    range and would write a HIDDEN TEMP instead of the local the body meant.
    """
    slots = sorted(slot for slot, _name, _ty in func.locals)
    if slots != list(range(len(slots))):
        raise EmitError(
            f"{func.export_name} declares local slots {slots}; SS C.3 numbers them in "
            "first-binding order, so they must be contiguous from 0 -- a gap would "
            "silently redirect a local.set at a hidden temp"
        )
    return len(slots)


def _abi_prologue(fn: Fn, ctx: LowerCtx, func: FuncIR) -> None:
    """Ruling E14: per-parameter tag AND range checks, before anything else.

    S3 calls this non-negotiable and prices it (`add(Symbol('hello')) -> 45`).
    The reason is that a wasm export's signature is `(i64...) -> i64` and says
    nothing at all about what those words MEAN -- a caller can hand a
    `SymbolObject` to a method whose parameter is a `U32`, and without this the
    unbox would read the handle index as a number and answer confidently.

    Every parameter, in order, and every one of them through the same
    `abi_check` the narrowing hook uses. Position `i` is local `i` (SS C.3).
    """
    for i, (_name, ty, _loc) in enumerate(func.params):
        abi_check(fn, ctx, i, ty)


def _lower_tail(fn: Fn, ctx: LowerCtx, func: FuncIR) -> None:
    """The per-result-arity tail rule -- see `compile_function`'s docstring."""
    if fn.unreachable:
        return
    if not fn.results:
        return
    if func.ret.tag is TyTag.VOID:
        fn.i64_const(val.VOID_VAL)
        fn.ret()
        return
    fn.i64_const(val.error_val(errors.CODE_UNREACHABLE_GUARD))
    fn.call_import(ctx.host_import_name(_FAIL_WITH_ERROR_FN), 1, has_result=True)
    fn.drop()
    fn.unreachable_()
