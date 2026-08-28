"""IR expression -> wasm, part 1: the scalar half of dossier SS B.3.1.

Two entry points, and the difference between them is the whole value model:

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
"""

from collections.abc import Mapping
from weakref import WeakKeyDictionary

from serpent import val
from serpent.compiler.ir import (
    Binary,
    BoolOp,
    BoolOpKind,
    Compare,
    CompareOp,
    Const,
    ConstRef,
    Convert,
    ErrorVal,
    FieldGet,
    HostCall,
    IfExp,
    InternalCall,
    IRExpr,
    IsZero,
    LocalRef,
    MakeMap,
    MakeStruct,
    MakeTopics,
    MakeVec,
    ParamRef,
    RawScalar,
    Unary,
    UnaryOp,
)
from serpent.compiler.types_ import Ty, TyTag
from serpent.emitter import arith, opcodes
from serpent.emitter.arith import EmitCtx
from serpent.emitter.frame import EmitError, Fn
from serpent.emitter.layout import Memory

__all__ = ["LowerCtx", "lower_condition", "lower_expr", "lower_expr_raw"]


# --- the lowering context ------------------------------------------------------


class LowerCtx(EmitCtx):
    """``EmitCtx`` plus the two things expression lowering needs on top of it.

    * ``consts`` -- the module's chain-constant table (P5). Ruling E11 inlines
      a ``ConstRef`` at every use rather than emitting a wasm global, so the
      lowering has to be able to reach the ``ConstDecl.value`` expression the
      name stands for. A ``Mapping`` rather than the ``ConstDecl``s themselves
      because that is all this module reads.
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
    ) -> None:
        super().__init__(n_module_functions, memory)
        self.consts: Mapping[str, IRExpr] = {} if consts is None else consts
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

#: Every node kind this module deliberately does not lower: containers,
#: structs, and calls are Task 8's half of SS B.3.1. Named so the refusal says
#: which task owns them instead of reading as "unknown node".
_TASK_8_KINDS = (MakeStruct, FieldGet, MakeVec, MakeMap, MakeTopics, HostCall, InternalCall)


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


def lower_condition(fn: Fn, ctx: LowerCtx, cond: IRExpr) -> None:
    """Lower ``cond`` to the i32 an ``if``/``br_if`` wants (review m2).

    THE one condition helper: ``If``, ``While``, ``IfExp`` and ``BoolOp`` all
    call it, so no site invents its own comparison. A ``Bool`` ``Val`` is
    literally the word 0 or 1 -- ``val.pack_bool`` says so, ``obj_cmp``-derived
    Bools are built by ``i64.extend_i32_u`` of a relop, and the ABI prologues
    guarantee it for parameters -- so the whole conversion is one
    ``i32.wrap_i64``.
    """
    if cond.ty.tag is not TyTag.BOOL:
        raise EmitError(
            f"a condition must be Bool, got {cond.ty.render()}; the frontend lowers "
            "chain-int truthiness to Unary(NOT, IsZero(x)) (D3/E10), so D never "
            "re-derives it"
        )
    lower_expr(fn, ctx, cond)
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
    elif isinstance(e, _TASK_8_KINDS):
        raise EmitError(f"{type(e).__name__} is lowered in Task 8, not here")
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
    """
    lower_condition(fn, ctx, e.cond)
    fn.begin_if("i64")
    lower_expr(fn, ctx, e.then)
    fn.else_()
    lower_expr(fn, ctx, e.orelse)
    fn.end_if()


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
