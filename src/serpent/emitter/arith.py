"""Checked 32/64-bit arithmetic, negation, and the small-vs-object boxing bridge.

This is the module where a wrong byte does not crash: it validates, deploys,
and silently computes the wrong number. Everything here therefore restates
its contract next to the instructions that implement it.

## A4, the checked-arithmetic contract

``//`` **truncates toward zero** and ``%`` **takes the dividend's sign** -- C's
rule and wasm's, NOT Python's (Python floors and takes the divisor's sign, so
``-7 // 2`` is ``-4`` at tier 1's expense if anyone reaches for it here).
``MIN % -1`` is ``0``, which ``i64.rem_s`` gives natively. Every out-of-range
result reaches ``fail_with_error`` with ``ArithmeticOverflow`` rather than
wrapping. Division and remainder **by zero are left to wasm's own trap** (A10's
trap-class mapping records it; tier 1 raises ``ZeroDivisionError``), so nothing
here tests for a zero divisor.

The one place A4's rules and wasm's instructions disagree: ``i64.div_s`` TRAPS
on ``MIN / -1`` (the quotient is not representable). A trap is the wrong
outcome -- tier 1 raises ``ArithmeticOverflow`` -- so ``i64_floordiv`` branches
that pair to the error **before** the instruction runs.

## What is a part and what is inline (ruling E3, S25)

A *part* is a guest-runtime function ``EmitCtx.ensure_part`` links once and
every use calls. 64-bit checked ops are parts (their overflow analysis is 6+
instructions, and it appears at every use); 32-bit checked ops are **inline**
(a shift, an op, and a range compare -- below S25's call-overhead break-even);
``NEG`` is inline at both widths (review M6). Parts take and return **RAW
unboxed words**, never ``Val``s: boxing is the caller's business, and mixing
the two is how a tag ends up inside an addend.

## The unbox recipe (review B10)

A small-form ``Val`` is ``(body << 8) | tag`` with a 56-bit body, so the body's
own sign bit already sits at word bit 63. A signed unbox is therefore **one
``i64.shr_s 8`` and nothing else**. The superseded "``shl 8`` then ``shr_s 8``"
recipe corrupts every value: ``I64(-1)`` decodes to ``-249``.

## The repack anti-pattern (F.1.3)

The spike repacked a 32-bit result with a bare, wrapping ``i64.shl 32``, so
``U32(2**32 - 1) + U32(1)`` silently became ``U32(0)``. Every repack here
range-checks first.

## Overflow inside a part

``fail_with_error`` "does not actually return" -- the host traps inside it --
so the error path is ``i64.const <error Val>``, ``call fail_with_error``,
``drop``, and **no ``unreachable`` after it**. The code that follows is
statically reachable and must stay well-typed, but no execution reaches it.
"""

from collections.abc import Callable
from dataclasses import dataclass

from serpent import val
from serpent._host import functions_by_name
from serpent.compiler.ir import BinaryOp
from serpent.compiler.types_ import Ty, TyTag
from serpent.emitter import opcodes
from serpent.emitter.frame import CodeItem, EmitError, Fn
from serpent.emitter.layout import Memory
from serpent.errors import CODE_ARITHMETIC_OVERFLOW

__all__ = [
    "PART_BUILDERS",
    "EmitCtx",
    "Part",
    "lower_binary",
    "lower_neg",
    "rebox",
    "unbox",
]


# --- the bounds every check below is written against -------------------------

_U32_MAX = (1 << 32) - 1
_I32_MIN = -(1 << 31)
_I32_MAX = (1 << 31) - 1
_I64_MIN = -(1 << 63)
_I64_MAX = (1 << 63) - 1

#: The magnitude bound for a NEGATIVE i64 product: ``|MIN| == 2**63``, one more
#: than ``I64_MAX``. Which of the two bounds a signed multiply checks against
#: depends on the sign of its own result, which is why it is computed at
#: runtime rather than baked in (``2**62 * -2`` is exactly ``MIN``, and legal;
#: ``2**62 * 2`` is not).
_I64_NEG_MAGNITUDE_MAX = 1 << 63

#: The low byte of a `Val`: its tag (spec SS 10, `serpent.val`).
_TAG_MASK = 0xFF

#: How far a small-form body sits above the tag.
_TAG_BITS = 8

#: Where a U32/I32 immediate's payload sits: `body = major << 24 | minor` and
#: `val = body << 8 | tag`, with `minor == 0`, so the payload starts at bit 32.
_IMMEDIATE_SHIFT = 32


# --- one linked runtime part --------------------------------------------------


@dataclass(frozen=True)
class Part:
    """One built guest-runtime function, ready for the module assembler.

    ``defidx`` counts from the first DEFINED function (imports excluded), which
    is what ``frame.CallDefined`` carries and what pass 2 turns into a real
    index once the import list is frozen (review B1).
    """

    name: str
    defidx: int
    nparams: int
    nlocals: int
    results: tuple[str, ...]
    body: tuple[CodeItem, ...]


# --- the lowering context -----------------------------------------------------


class EmitCtx:
    """What one module's lowering shares: its imports, its parts, its memory.

    Two index spaces are decided here, and both are decided by **first use**,
    which makes them a pure function of lowering order (which is source order):

    * ``host_import_name`` registers a host function the first time a lowering
      names it; ``import_order`` is that order, and it IS the import section's.
    * ``ensure_part`` builds a named runtime part once and appends it to the
      defined-function space **after** the module's own ``n_module_functions``
      functions, so a part's ``defidx`` is stable from the first call (the
      pre-flight ruling that ``n_module_functions`` is a construction
      argument).

    Neither index is ever baked into a body: both are recorded symbolically and
    resolved in pass 2 (review B1).
    """

    def __init__(self, n_module_functions: int, memory: Memory) -> None:
        if n_module_functions < 0:
            raise EmitError(f"n_module_functions must be >= 0, got {n_module_functions}")
        self.n_module_functions = n_module_functions
        self.memory = memory
        self._import_order: list[str] = []
        self._part_order: list[str] = []
        self._defidx: dict[str, int] = {}
        self._built: dict[str, Part] = {}

    # -- host imports ---------------------------------------------------------

    def host_import_name(self, name: str) -> str:
        """Register ``name`` as an import on FIRST USE and hand it straight back.

        Returns the name so a call site reads
        ``fn.call_import(ctx.host_import_name("obj_to_u64"), 1, True)`` -- the
        registration cannot be forgotten, because the name the call records is
        the registration's own return value.
        """
        if name not in functions_by_name:
            raise EmitError(
                f"{name!r} is not a host function in the pin "
                "(serpent._host.functions_by_name); an import cannot be invented here"
            )
        if name not in self._import_order:
            self._import_order.append(name)
        return name

    @property
    def import_order(self) -> tuple[str, ...]:
        """The import section's order: first use, and therefore deterministic."""
        return tuple(self._import_order)

    # -- runtime parts --------------------------------------------------------

    def ensure_part(self, name: str) -> int:
        """Build the named part if it is not linked yet; return its ``defidx``.

        The index is reserved BEFORE the body is built, so a part whose own
        body links another part still gets the index it was promised.
        """
        if name in self._defidx:
            return self._defidx[name]
        builder = PART_BUILDERS.get(name)
        if builder is None:
            raise EmitError(
                f"no runtime part named {name!r}; the linkable parts are {sorted(PART_BUILDERS)}"
            )
        defidx = self.n_module_functions + len(self._part_order)
        self._part_order.append(name)
        self._defidx[name] = defidx
        fn = builder(self)
        self._built[name] = Part(
            name=name,
            defidx=defidx,
            nparams=fn.nparams,
            nlocals=fn.nlocals,
            results=fn.results,
            body=tuple(fn.finish()),
        )
        return defidx

    @property
    def parts_linked(self) -> frozenset[str]:
        """Every part this module links -- Task 13's superset of C's hint."""
        return frozenset(self._part_order)

    @property
    def parts(self) -> tuple[Part, ...]:
        """The linked parts in ``defidx`` order, ready to append to the module."""
        return tuple(self._built[name] for name in self._part_order)


# --- small emission helpers ---------------------------------------------------


def _eqz(fn: Fn) -> None:
    """``i64.eqz``: one i64 in, an i32 boolean out."""
    fn.pop("i64")
    fn.op(opcodes.I64_EQZ)
    fn.push("i32")


def _extend_u(fn: Fn) -> None:
    """``i64.extend_i32_u``: widen a comparison's 0/1 so it can be `and`ed."""
    fn.pop("i32")
    fn.op(opcodes.I64_EXTEND_I32_U)
    fn.push("i64")


def _extend32_s(fn: Fn) -> None:
    """``i64.extend32_s``: sign-extend the low 32 bits (the I32 unbox tail)."""
    fn.pop("i64")
    fn.op(opcodes.I64_EXTEND32_S)
    fn.push("i64")


def _fail_overflow(fn: Fn, ctx: EmitCtx) -> None:
    """Abort with ``ArithmeticOverflow`` (A4/S20).

    No ``unreachable`` follows: the host traps inside ``fail_with_error``, and
    an ``unreachable`` here would only make the code after it invisible to the
    operand-stack tracker that is meant to be checking it.
    """
    fn.i64_const(val.error_val(CODE_ARITHMETIC_OVERFLOW))
    fn.call_import(ctx.host_import_name("fail_with_error"), 1, has_result=True)
    fn.drop()


def _fail_if(fn: Fn, ctx: EmitCtx) -> None:
    """Consume the i32 on the stack; abort with ``ArithmeticOverflow`` if true."""
    fn.begin_if(None)
    _fail_overflow(fn, ctx)
    fn.end_if()


def _guard_u32(fn: Fn, ctx: EmitCtx) -> None:
    """Range-check the i64 on the stack against U32, leaving it in place (F.1.3)."""
    slot = fn.new_local()
    fn.local_tee(slot)
    fn.i64_const(_U32_MAX)
    fn.relop_i64(opcodes.I64_GT_U)
    _fail_if(fn, ctx)
    fn.local_get(slot)


def _guard_i32(fn: Fn, ctx: EmitCtx) -> None:
    """Range-check the i64 on the stack against I32, leaving it in place (F.1.3).

    Both ends: a 32-bit result can leave the range downward (``I32_MIN - 1``)
    as easily as upward (``I32_MIN // -1``, which is ``2**31``).
    """
    slot = fn.new_local()
    fn.local_tee(slot)
    fn.i64_const(_I32_MAX)
    fn.relop_i64(opcodes.I64_GT_S)
    _fail_if(fn, ctx)
    fn.local_get(slot)
    fn.i64_const(_I32_MIN)
    fn.relop_i64(opcodes.I64_LT_S)
    _fail_if(fn, ctx)
    fn.local_get(slot)


def _abs_into(fn: Fn, src: int, dst: int) -> None:
    """``dst = src < 0 ? 0 - src : src``.

    Only ever reached with ``src != MIN`` (``i64_mul`` checks that first), so
    the negation is exact rather than wrapping.
    """
    fn.local_get(src)
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_LT_S)
    fn.begin_if("i64")
    fn.i64_const(0)
    fn.local_get(src)
    fn.binop_i64(opcodes.I64_SUB)
    fn.else_()
    fn.local_get(src)
    fn.end_if()
    fn.local_set(dst)


def _part_fn(name: str, nparams: int) -> Fn:
    """A part shell: ``nparams`` i64 params, one i64 result, no declared locals."""
    return Fn(name, nparams, 0, ("i64",))


# --- the 64-bit unsigned parts -------------------------------------------------


def _build_u64_add(ctx: EmitCtx) -> Fn:
    """``a + b``; overflow iff the sum wrapped, which shows as ``r < a``."""
    fn = _part_fn("u64_add", 2)
    result = fn.new_local()
    fn.local_get(0)
    fn.local_get(1)
    fn.binop_i64(opcodes.I64_ADD)
    fn.local_tee(result)
    fn.local_get(0)
    fn.relop_i64(opcodes.I64_LT_U)
    _fail_if(fn, ctx)
    fn.local_get(result)
    fn.ret()
    return fn


def _build_u64_sub(ctx: EmitCtx) -> Fn:
    """``a - b``; overflow iff ``a < b`` (there are no negative U64s)."""
    fn = _part_fn("u64_sub", 2)
    fn.local_get(0)
    fn.local_get(1)
    fn.relop_i64(opcodes.I64_LT_U)
    _fail_if(fn, ctx)
    fn.local_get(0)
    fn.local_get(1)
    fn.binop_i64(opcodes.I64_SUB)
    fn.ret()
    return fn


def _build_u64_mul(ctx: EmitCtx) -> Fn:
    """``a * b``; overflow iff ``a != 0 and div_u(r, a) != b`` (review M7).

    The division is the exact inverse of the wrapping multiply: it recovers
    ``b`` from an untruncated product and cannot from a truncated one. The
    ``a != 0`` guard is not an optimization -- ``i64.div_u`` by zero traps.
    """
    fn = _part_fn("u64_mul", 2)
    result = fn.new_local()
    fn.local_get(0)
    fn.local_get(1)
    fn.binop_i64(opcodes.I64_MUL)
    fn.local_set(result)
    fn.local_get(0)
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_NE)
    fn.begin_if(None)
    fn.local_get(result)
    fn.local_get(0)
    fn.binop_i64(opcodes.I64_DIV_U)
    fn.local_get(1)
    fn.relop_i64(opcodes.I64_NE)
    _fail_if(fn, ctx)
    fn.end_if()
    fn.local_get(result)
    fn.ret()
    return fn


def _build_u64_floordiv(_ctx: EmitCtx) -> Fn:
    """``a // b``: unsigned division cannot overflow; ``b == 0`` is wasm's trap."""
    fn = _part_fn("u64_floordiv", 2)
    fn.local_get(0)
    fn.local_get(1)
    fn.binop_i64(opcodes.I64_DIV_U)
    fn.ret()
    return fn


def _build_u64_mod(_ctx: EmitCtx) -> Fn:
    """``a % b``: unsigned remainder cannot overflow; ``b == 0`` is wasm's trap."""
    fn = _part_fn("u64_mod", 2)
    fn.local_get(0)
    fn.local_get(1)
    fn.binop_i64(opcodes.I64_REM_U)
    fn.ret()
    return fn


# --- the 64-bit signed parts ---------------------------------------------------


def _build_i64_add(ctx: EmitCtx) -> Fn:
    """``a + b``; overflow iff ``((a ^ r) & (b ^ r)) < 0``.

    Signed addition overflows exactly when both addends share a sign and the
    sum does not. Each ``xor``'s sign bit is "this addend disagrees with the
    sum"; the ``and`` is "both do", which can only happen if they agreed with
    each other.
    """
    fn = _part_fn("i64_add", 2)
    result = fn.new_local()
    fn.local_get(0)
    fn.local_get(1)
    fn.binop_i64(opcodes.I64_ADD)
    fn.local_set(result)
    fn.local_get(0)
    fn.local_get(result)
    fn.binop_i64(opcodes.I64_XOR)
    fn.local_get(1)
    fn.local_get(result)
    fn.binop_i64(opcodes.I64_XOR)
    fn.binop_i64(opcodes.I64_AND)
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_LT_S)
    _fail_if(fn, ctx)
    fn.local_get(result)
    fn.ret()
    return fn


def _build_i64_sub(ctx: EmitCtx) -> Fn:
    """``a - b``; overflow iff ``((a ^ b) & (a ^ r)) < 0``.

    Signed subtraction overflows exactly when the operands' signs differ AND
    the result's sign differs from the minuend's.
    """
    fn = _part_fn("i64_sub", 2)
    result = fn.new_local()
    fn.local_get(0)
    fn.local_get(1)
    fn.binop_i64(opcodes.I64_SUB)
    fn.local_set(result)
    fn.local_get(0)
    fn.local_get(1)
    fn.binop_i64(opcodes.I64_XOR)
    fn.local_get(0)
    fn.local_get(result)
    fn.binop_i64(opcodes.I64_XOR)
    fn.binop_i64(opcodes.I64_AND)
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_LT_S)
    _fail_if(fn, ctx)
    fn.local_get(result)
    fn.ret()
    return fn


def _min_operand_arm(fn: Fn, ctx: EmitCtx, other: int) -> None:
    """The ``MIN * other`` arm of ``i64_mul`` (review M7).

    ``MIN`` has no positive twin, so a magnitude-based multiply cannot handle
    it: ``0 - MIN`` wraps back to ``MIN``. Only two products involving ``MIN``
    are representable -- ``MIN * 0 == 0`` and ``MIN * 1 == MIN`` -- so they are
    answered here and everything else is the overflow.

    The trailing ``i64.const 0; return`` is never executed (the host traps
    inside ``fail_with_error``); it is there so the arm returns a value on
    paper rather than falling through into the magnitude code, which is
    precisely the code that cannot represent ``MIN``.
    """
    fn.local_get(other)
    _eqz(fn)
    fn.begin_if(None)
    fn.i64_const(0)
    fn.ret()
    fn.end_if()
    fn.local_get(other)
    fn.i64_const(1)
    fn.relop_i64(opcodes.I64_EQ)
    fn.begin_if(None)
    fn.i64_const(_I64_MIN)
    fn.ret()
    fn.end_if()
    _fail_overflow(fn, ctx)
    fn.i64_const(0)
    fn.ret()


def _build_i64_mul(ctx: EmitCtx) -> Fn:
    """``a * b`` (review M7): ``MIN`` first, then magnitudes, then the sign.

    After the two ``MIN`` arms both operands have a representable magnitude, so
    the product is computed unsigned and checked twice:

    1. the same ``div_u`` inverse ``u64_mul`` uses, which catches a product
       that did not fit in 64 UNSIGNED bits at all;
    2. the magnitude against the bound for the RESULT's sign -- ``2**63 - 1``
       for a non-negative result, ``2**63`` for a negative one. Two bounds, not
       one: ``2**62 * -2`` is exactly ``MIN`` and legal, while ``2**62 * 2`` is
       not.
    """
    fn = _part_fn("i64_mul", 2)
    lhs_mag = fn.new_local()
    rhs_mag = fn.new_local()
    product = fn.new_local()
    negative = fn.new_local()
    bound = fn.new_local()

    fn.local_get(0)
    fn.i64_const(_I64_MIN)
    fn.relop_i64(opcodes.I64_EQ)
    fn.begin_if(None)
    _min_operand_arm(fn, ctx, other=1)
    fn.end_if()

    fn.local_get(1)
    fn.i64_const(_I64_MIN)
    fn.relop_i64(opcodes.I64_EQ)
    fn.begin_if(None)
    _min_operand_arm(fn, ctx, other=0)
    fn.end_if()

    _abs_into(fn, 0, lhs_mag)
    _abs_into(fn, 1, rhs_mag)
    fn.local_get(lhs_mag)
    fn.local_get(rhs_mag)
    fn.binop_i64(opcodes.I64_MUL)
    fn.local_set(product)

    # (1) did the unsigned product itself wrap?
    fn.local_get(lhs_mag)
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_NE)
    fn.begin_if(None)
    fn.local_get(product)
    fn.local_get(lhs_mag)
    fn.binop_i64(opcodes.I64_DIV_U)
    fn.local_get(rhs_mag)
    fn.relop_i64(opcodes.I64_NE)
    _fail_if(fn, ctx)
    fn.end_if()

    # The result's sign: the operands' signs, exclusive-or'd.
    fn.local_get(0)
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_LT_S)
    _extend_u(fn)
    fn.local_get(1)
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_LT_S)
    _extend_u(fn)
    fn.binop_i64(opcodes.I64_XOR)
    fn.local_set(negative)

    # (2) the magnitude against the bound for THAT sign.
    fn.local_get(negative)
    _eqz(fn)
    fn.begin_if("i64")
    fn.i64_const(_I64_MAX)
    fn.else_()
    fn.i64_const(_I64_NEG_MAGNITUDE_MAX)
    fn.end_if()
    fn.local_set(bound)
    fn.local_get(product)
    fn.local_get(bound)
    fn.relop_i64(opcodes.I64_GT_U)
    _fail_if(fn, ctx)

    fn.local_get(negative)
    _eqz(fn)
    fn.begin_if("i64")
    fn.local_get(product)
    fn.else_()
    fn.i64_const(0)
    fn.local_get(product)
    fn.binop_i64(opcodes.I64_SUB)
    fn.end_if()
    fn.ret()
    return fn


def _build_i64_floordiv(ctx: EmitCtx) -> Fn:
    """``a // b``, TRUNCATED TOWARD ZERO (A4) -- which is ``i64.div_s``.

    ``MIN // -1`` is branched to the overflow error BEFORE the instruction
    because ``i64.div_s`` TRAPS on it: the quotient ``2**63`` is not
    representable. A trap would surface as the wrong error class (A10), so it
    must never be reached.
    """
    fn = _part_fn("i64_floordiv", 2)
    fn.local_get(1)
    fn.i64_const(-1)
    fn.relop_i64(opcodes.I64_EQ)
    fn.begin_if(None)
    fn.local_get(0)
    fn.i64_const(_I64_MIN)
    fn.relop_i64(opcodes.I64_EQ)
    _fail_if(fn, ctx)
    fn.end_if()
    fn.local_get(0)
    fn.local_get(1)
    fn.binop_i64(opcodes.I64_DIV_S)
    fn.ret()
    return fn


def _build_i64_mod(_ctx: EmitCtx) -> Fn:
    """``a % b``, taking the DIVIDEND's sign (A4) -- which is ``i64.rem_s``.

    No overflow branch, and specifically none for ``MIN % -1``: unlike
    ``i64.div_s``, ``i64.rem_s`` does not trap there and natively yields ``0``,
    which is what A4 requires.
    """
    fn = _part_fn("i64_mod", 2)
    fn.local_get(0)
    fn.local_get(1)
    fn.binop_i64(opcodes.I64_REM_S)
    fn.ret()
    return fn


# --- the boxing bridge (S14) ---------------------------------------------------


def _unbox_either(fn: Fn, ctx: EmitCtx, *, small_tag: int, shift: int, accessor: str) -> None:
    """The EITHER-repr unbox: tag test, then the small shift or the host call.

    ``shift`` is ``i64.shr_u`` for an unsigned body and ``i64.shr_s`` for a
    signed one, and that single shift is the WHOLE small path (review B10): the
    56-bit body's sign bit already sits at word bit 63, so there is nothing to
    align first.
    """
    fn.local_get(0)
    fn.i64_const(_TAG_MASK)
    fn.binop_i64(opcodes.I64_AND)
    fn.i64_const(small_tag)
    fn.relop_i64(opcodes.I64_EQ)
    fn.begin_if("i64")
    fn.local_get(0)
    fn.i64_const(_TAG_BITS)
    fn.binop_i64(shift)
    fn.else_()
    fn.local_get(0)
    fn.call_import(ctx.host_import_name(accessor), 1, has_result=True)
    fn.end_if()
    fn.ret()


def _pack_small(fn: Fn, slot: int, tag: int) -> None:
    """``(value << 8) | tag`` -- the small form, from a raw word."""
    fn.local_get(slot)
    fn.i64_const(_TAG_BITS)
    fn.binop_i64(opcodes.I64_SHL)
    fn.i64_const(tag)
    fn.binop_i64(opcodes.I64_OR)


def _build_unbox_u64(ctx: EmitCtx) -> Fn:
    fn = _part_fn("unbox_u64", 1)
    _unbox_either(
        fn,
        ctx,
        small_tag=val.TAG_U64_SMALL,
        shift=opcodes.I64_SHR_U,
        accessor="obj_to_u64",
    )
    return fn


def _build_unbox_i64(ctx: EmitCtx) -> Fn:
    fn = _part_fn("unbox_i64", 1)
    _unbox_either(
        fn,
        ctx,
        small_tag=val.TAG_I64_SMALL,
        shift=opcodes.I64_SHR_S,
        accessor="obj_to_i64",
    )
    return fn


def _build_unbox_timepoint(ctx: EmitCtx) -> Fn:
    """Timepoint is unbox-only (D4: no arithmetic exists), body UNSIGNED."""
    fn = _part_fn("unbox_timepoint", 1)
    _unbox_either(
        fn,
        ctx,
        small_tag=val.TAG_TIMEPOINT_SMALL,
        shift=opcodes.I64_SHR_U,
        accessor="timepoint_obj_to_u64",
    )
    return fn


def _build_unbox_duration(ctx: EmitCtx) -> Fn:
    """Duration is unbox-only (D4: no arithmetic exists), body UNSIGNED."""
    fn = _part_fn("unbox_duration", 1)
    _unbox_either(
        fn,
        ctx,
        small_tag=val.TAG_DURATION_SMALL,
        shift=opcodes.I64_SHR_U,
        accessor="duration_obj_to_u64",
    )
    return fn


def _build_box_u64(ctx: EmitCtx) -> Fn:
    """``fits_small_u`` at runtime: the small form, or ``obj_from_u64``.

    The lower half of ``val.fits_small_u`` (``0 <= x``) is free here: the word
    is unsigned by construction, so one ``i64.le_u`` is the whole test.
    """
    fn = _part_fn("box_u64", 1)
    fn.local_get(0)
    fn.i64_const(val.MAX_SMALL_U64)
    fn.relop_i64(opcodes.I64_LE_U)
    fn.begin_if("i64")
    _pack_small(fn, 0, val.TAG_U64_SMALL)
    fn.else_()
    fn.local_get(0)
    fn.call_import(ctx.host_import_name("obj_from_u64"), 1, has_result=True)
    fn.end_if()
    fn.ret()
    return fn


def _build_box_i64(ctx: EmitCtx) -> Fn:
    """``fits_small_i`` at runtime: the small form, or ``obj_from_i64``.

    Both bounds are compared explicitly, mirroring ``val.fits_small_i`` rather
    than the shorter ``(x << 8) >> 8 == x`` trick -- the two agree, but only
    one of them is checkable against the codec by reading it.
    """
    fn = _part_fn("box_i64", 1)
    fits = fn.new_local()
    fn.local_get(0)
    fn.i64_const(val.MIN_SMALL_I64)
    fn.relop_i64(opcodes.I64_GE_S)
    _extend_u(fn)
    fn.local_get(0)
    fn.i64_const(val.MAX_SMALL_I64)
    fn.relop_i64(opcodes.I64_LE_S)
    _extend_u(fn)
    fn.binop_i64(opcodes.I64_AND)
    fn.local_set(fits)
    fn.local_get(fits)
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_NE)
    fn.begin_if("i64")
    _pack_small(fn, 0, val.TAG_I64_SMALL)
    fn.else_()
    fn.local_get(0)
    fn.call_import(ctx.host_import_name("obj_from_i64"), 1, has_result=True)
    fn.end_if()
    fn.ret()
    return fn


#: Every linkable runtime part, by name (ruling E3's ratified inventory for
#: this task). Task 6 adds the ``{u,i}128_*`` family, Task 9 ``tagcheck_bytes_n``.
PART_BUILDERS: dict[str, Callable[[EmitCtx], Fn]] = {
    "u64_add": _build_u64_add,
    "u64_sub": _build_u64_sub,
    "u64_mul": _build_u64_mul,
    "u64_floordiv": _build_u64_floordiv,
    "u64_mod": _build_u64_mod,
    "i64_add": _build_i64_add,
    "i64_sub": _build_i64_sub,
    "i64_mul": _build_i64_mul,
    "i64_floordiv": _build_i64_floordiv,
    "i64_mod": _build_i64_mod,
    "unbox_u64": _build_unbox_u64,
    "box_u64": _build_box_u64,
    "unbox_i64": _build_unbox_i64,
    "box_i64": _build_box_i64,
    "unbox_timepoint": _build_unbox_timepoint,
    "unbox_duration": _build_unbox_duration,
}


# --- the lowering entry points -------------------------------------------------

#: Widths whose checked `Binary` is a CALL: the part name is `<prefix>_<op>`.
_PART_PREFIX: dict[TyTag, str] = {TyTag.U64: "u64", TyTag.I64: "i64"}

#: Widths Task 6 owns; named here only so the refusal says something useful.
_WIDE_TAGS = frozenset({TyTag.U128, TyTag.I128})

_UNBOX_PART: dict[TyTag, str] = {
    TyTag.U64: "unbox_u64",
    TyTag.I64: "unbox_i64",
    TyTag.TIMEPOINT: "unbox_timepoint",
    TyTag.DURATION: "unbox_duration",
}

_BOX_PART: dict[TyTag, str] = {TyTag.U64: "box_u64", TyTag.I64: "box_i64"}


def _refuse(ty: Ty, what: str) -> EmitError:
    if ty.tag in _WIDE_TAGS:
        return EmitError(
            f"{ty.render()} has no {what} here: 128-bit values are limb code, "
            "linked as their own runtime parts"
        )
    return EmitError(f"{ty.render()} has no {what}")


def _lower_u32_binary(fn: Fn, ctx: EmitCtx, op: BinaryOp) -> None:
    """U32 checked arithmetic, inline (S25).

    Both operands are raw words in ``[0, 2**32)``, so no ``i64`` op below can
    itself wrap: the sum, difference, and product of two 32-bit values all fit
    in 64 bits, and the only question is whether the ANSWER fits in 32.
    """
    if op is BinaryOp.ADD:
        fn.binop_i64(opcodes.I64_ADD)
        _guard_u32(fn, ctx)
    elif op is BinaryOp.SUB:
        rhs = fn.new_local()
        lhs = fn.new_local()
        fn.local_set(rhs)
        fn.local_set(lhs)
        fn.local_get(lhs)
        fn.local_get(rhs)
        fn.relop_i64(opcodes.I64_LT_U)
        _fail_if(fn, ctx)
        fn.local_get(lhs)
        fn.local_get(rhs)
        fn.binop_i64(opcodes.I64_SUB)
    elif op is BinaryOp.MUL:
        fn.binop_i64(opcodes.I64_MUL)
        _guard_u32(fn, ctx)
    elif op is BinaryOp.FLOORDIV:
        fn.binop_i64(opcodes.I64_DIV_U)
    else:
        fn.binop_i64(opcodes.I64_REM_U)


def _lower_i32_binary(fn: Fn, ctx: EmitCtx, op: BinaryOp) -> None:
    """I32 checked arithmetic, inline (S25).

    Both operands are SIGN-extended raw words in ``[-2**31, 2**31)``, so every
    64-bit op below is exact and one range check decides overflow. That covers
    ``I32_MIN // -1`` too: at 64 bits ``i64.div_s`` does not trap, it produces
    ``2**31``, which the check then rejects. ``%`` needs no check at all -- a
    remainder is smaller in magnitude than its divisor, and ``i64.rem_s``
    yields ``0`` for ``MIN % -1``, which is A4's answer.
    """
    if op is BinaryOp.ADD:
        fn.binop_i64(opcodes.I64_ADD)
        _guard_i32(fn, ctx)
    elif op is BinaryOp.SUB:
        fn.binop_i64(opcodes.I64_SUB)
        _guard_i32(fn, ctx)
    elif op is BinaryOp.MUL:
        fn.binop_i64(opcodes.I64_MUL)
        _guard_i32(fn, ctx)
    elif op is BinaryOp.FLOORDIV:
        fn.binop_i64(opcodes.I64_DIV_S)
        _guard_i32(fn, ctx)
    else:
        fn.binop_i64(opcodes.I64_REM_S)


def lower_binary(fn: Fn, ctx: EmitCtx, ty: Ty, op: BinaryOp) -> None:
    """Lower ``lhs <op> rhs`` for ``ty``, both operands already RAW on the stack.

    Leaves one RAW word: boxing is the caller's (Task 7's) business.
    """
    prefix = _PART_PREFIX.get(ty.tag)
    if prefix is not None:
        fn.call_defined(ctx.ensure_part(f"{prefix}_{op.name.lower()}"), 2, ("i64",))
    elif ty.tag is TyTag.U32:
        _lower_u32_binary(fn, ctx, op)
    elif ty.tag is TyTag.I32:
        _lower_i32_binary(fn, ctx, op)
    else:
        raise _refuse(ty, "checked arithmetic")


def lower_neg(fn: Fn, ctx: EmitCtx, ty: Ty) -> None:
    """Lower unary ``-`` for ``ty``, INLINE at every native width (review M6).

    On an UNSIGNED type there is no negative to produce, so any nonzero operand
    is the overflow and zero is the only legal input -- which is also why there
    is no ``u64_neg`` part to call. On a SIGNED type ``MIN`` is the overflow
    (it has no positive twin) and everything else is ``0 - value``.
    """
    if ty.tag in (TyTag.U32, TyTag.U64):
        fn.i64_const(0)
        fn.relop_i64(opcodes.I64_NE)
        _fail_if(fn, ctx)
        fn.i64_const(0)
        return
    if ty.tag in (TyTag.I32, TyTag.I64):
        minimum = _I32_MIN if ty.tag is TyTag.I32 else _I64_MIN
        slot = fn.new_local()
        fn.local_tee(slot)
        fn.i64_const(minimum)
        fn.relop_i64(opcodes.I64_EQ)
        _fail_if(fn, ctx)
        fn.i64_const(0)
        fn.local_get(slot)
        fn.binop_i64(opcodes.I64_SUB)
        return
    raise _refuse(ty, "checked negation")


def unbox(fn: Fn, ctx: EmitCtx, ty: Ty) -> None:
    """Val word on the stack -> RAW word.

    IMMEDIATE 32-bit types are inline (the payload is at bit 32 and the tag is
    fixed, so there is nothing to branch on); EITHER 64-bit types call their
    part, whose tag test picks the small shift or the host accessor.
    """
    if ty.tag is TyTag.U32:
        fn.i64_const(_IMMEDIATE_SHIFT)
        fn.binop_i64(opcodes.I64_SHR_U)
        return
    if ty.tag is TyTag.I32:
        # `shr_u` then `extend32_s`, not `shr_s`: the payload is the LOW 32
        # bits of the shifted word, and its sign lives at bit 31, not bit 63.
        fn.i64_const(_IMMEDIATE_SHIFT)
        fn.binop_i64(opcodes.I64_SHR_U)
        _extend32_s(fn)
        return
    part = _UNBOX_PART.get(ty.tag)
    if part is None:
        raise _refuse(ty, "unboxing lowering")
    fn.call_defined(ctx.ensure_part(part), 1, ("i64",))


def rebox(fn: Fn, ctx: EmitCtx, ty: Ty) -> None:
    """RAW word on the stack -> Val word.

    Every 32-bit repack RANGE-CHECKS before shifting (F.1.3): the spike's bare
    ``i64.shl 32`` turned an out-of-range result into a perfectly valid Val of
    the wrong number, which validates and deploys.
    """
    if ty.tag is TyTag.U32:
        _guard_u32(fn, ctx)
        fn.i64_const(_IMMEDIATE_SHIFT)
        fn.binop_i64(opcodes.I64_SHL)
        fn.i64_const(val.TAG_U32)
        fn.binop_i64(opcodes.I64_OR)
        return
    if ty.tag is TyTag.I32:
        _guard_i32(fn, ctx)
        # Mask before shifting: a negative raw word is sign-extended across all
        # 64 bits, and those high bits would land on top of nothing good.
        fn.i64_const(_U32_MAX)
        fn.binop_i64(opcodes.I64_AND)
        fn.i64_const(_IMMEDIATE_SHIFT)
        fn.binop_i64(opcodes.I64_SHL)
        fn.i64_const(val.TAG_I32)
        fn.binop_i64(opcodes.I64_OR)
        return
    part = _BOX_PART.get(ty.tag)
    if part is None:
        # Timepoint/Duration land here on purpose: D4 gives them no arithmetic,
        # so nothing ever needs to build one from a raw word (S14).
        raise _refuse(ty, "boxing lowering")
    fn.call_defined(ctx.ensure_part(part), 1, ("i64",))
