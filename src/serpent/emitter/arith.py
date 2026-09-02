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

## 128-bit values: two limbs, and the two-result convention (review m4)

A 128-bit value travels as **two raw i64 limbs, ``(hi, lo)``** -- never as a
``Val``, and never as one word. A wasm function returns one value, so a part
that produces a 128-bit result writes ``lo`` to a fixed scratch slot
(``EmitCtx.lo_slot``, one slot PER PART, reserved at build time -- P12) and
returns ``hi``.

**That convention is safe under exactly two invariants. Both are load-bearing
and neither is locally visible from a call site:**

1. **No part ever reaches itself**, directly or through another part. C12
   rejects recursion at tier 1 and ``ensure_part`` refuses a self-linking
   part, so "one slot per part" is also "one LIVE slot per part": there is
   never a second activation of ``u128_mul`` holding a different ``lo``.
2. **The caller loads ``lo`` IMMEDIATELY** -- into a local, before any other
   part call can be emitted. ``call_wide_part`` is the only sanctioned way to
   call a two-result part and it emits that load itself, which is what makes
   ``(a*b) + (c*d)`` safe: each product's ``lo`` is already in a local before
   the next call exists.

**Consequence for Task 10 (review B8):** linking any two-result part forces
linear memory even when ``compiled.needs_memory`` is ``False`` -- a contract
that only multiplies ``U128``s has no literals and no linear-memory host call
and still needs a page. ``EmitCtx.needs_memory`` and ``PARTS_NEEDING_MEMORY``
are the markers to read; do not re-derive the fact from the IR.

## The 128-bit division route (S13, F.1.2, review B4)

There is no ``i128.div`` and no wide-arithmetic proposal on chain, so ``//``
and ``%`` go out to the host's 256-bit integers: sign-extend the pair into
four limbs (**all-ones words for a negative i128** -- F.1.12; zero for u128),
``obj_from_{i,u}256_pieces``, ``{i,u}256_div``, then back. Two traps in that
sentence:

* ``i256_rem_euclid``/``u256_rem_euclid`` are **never used** (F.1.2). Their
  documented modulo is EUCLIDEAN and A4's ``%`` follows the DIVIDEND's sign,
  so they disagree at ``-7 % 2`` -- ``+1`` against A4's ``-1``. ``%`` is
  computed as ``lhs - (lhs // rhs) * rhs`` from this route's own division.
* The division RESULT is an ``I256Val``/``U256Val``, and the host returns the
  **small form** for any quotient inside the 56-bit body -- which is almost
  every real quotient (``U128(100) // U128(5)`` among them). ``obj_to_*``
  accepts an object and nothing else, so the result is TAG-BRANCHED: small
  tag -> one ``shr_s``/``shr_u`` 8 IS the low limb and its extension IS the
  other three; object tag -> the four accessors (review B4).
"""

from collections.abc import Callable
from dataclasses import dataclass

from serpent import val
from serpent._host import functions_by_name
from serpent.compiler.ir import BinaryOp
from serpent.compiler.types_ import Ty, TyTag
from serpent.emitter import encode, opcodes
from serpent.emitter.frame import CodeItem, EmitError, Fn
from serpent.emitter.layout import Memory
from serpent.errors import CODE_ABI_CHECK_FAILED, CODE_ARITHMETIC_OVERFLOW

__all__ = [
    "PARTS_NEEDING_MEMORY",
    "PART_BUILDERS",
    "EmitCtx",
    "Part",
    "call_wide_part",
    "lower_binary",
    "lower_neg",
    "rebox",
    "rebox_wide",
    "unbox",
    "unbox_wide",
    "wide_binary",
    "wide_cmp",
    "wide_neg",
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

#: The low 32 bits of a limb -- the half a 32x32->64 partial product takes.
_MASK32 = (1 << 32) - 1

#: Half a limb: how far the high 32 bits of a 64-bit word sit.
_HALF_LIMB = 32

#: Where a limb's sign bit is. `x >>s 63` is therefore `x`'s sign extension:
#: all-ones for a negative limb, zero otherwise (F.1.12).
_SIGN_SHIFT = 63

#: `i64.load`/`i64.store`'s natural alignment, as the memarg's log2 form.
_ALIGN_8 = encode.uleb(3)

#: The memarg's static offset. Every scratch access here puts the whole
#: address in the `i32.const` instead, so this is always zero.
_OFFSET_0 = encode.uleb(0)


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
        self._building: list[str] = []
        self._lo_slot: dict[str, int] = {}

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
        body links another part still gets the index it was promised -- and
        parts DO nest: ``u128_mul`` links ``mul64_wide``, ``u128_mod`` links
        ``u128_floordiv``.

        Registration order and the build map are therefore kept consistent by
        construction: ``_part_order`` gains the name at reservation,
        ``_built`` gains its ``Part`` only when the builder returns, and
        ``parts`` refuses to answer in between rather than raising ``KeyError``
        at whichever name happened to be half-finished.

        **A part that reaches itself is a compiler bug**, directly or through
        another part: it would not terminate, and C12 rejects recursion at
        tier 1 precisely so that nothing downstream has to model it. That
        state is exactly "reserved but not built", which is what the second
        test below detects.
        """
        if name in self._defidx:
            if name not in self._built:
                chain = " -> ".join([*self._building, name])
                raise EmitError(
                    f"runtime part {name!r} links itself ({chain}); a part's body "
                    "must terminate, and C12 rejects recursion -- this is a "
                    "compiler bug in the part builders, not a contract error"
                )
            return self._defidx[name]
        builder = PART_BUILDERS.get(name)
        if builder is None:
            raise EmitError(
                f"no runtime part named {name!r}; the linkable parts are {sorted(PART_BUILDERS)}"
            )
        defidx = self.n_module_functions + len(self._part_order)
        self._part_order.append(name)
        self._defidx[name] = defidx
        self._building.append(name)
        try:
            fn = builder(self)
        finally:
            self._building.pop()
        self._built[name] = Part(
            name=name,
            defidx=defidx,
            nparams=fn.nparams,
            nlocals=fn.nlocals,
            results=fn.results,
            body=tuple(fn.finish()),
        )
        return defidx

    def lo_slot(self, name: str) -> int:
        """The scratch address part ``name`` writes its ``lo`` limb to (P12, m4).

        One slot PER PART, not per call site, and allocated on first mention so
        the address is a pure function of lowering order. Safe against
        re-entrancy under the two invariants this module's docstring states:
        no part reaches itself (``ensure_part`` proves it), and the caller
        loads ``lo`` immediately after the call -- which ``call_wide_part`` is
        the only sanctioned way to do.
        """
        if name not in self._lo_slot:
            self._lo_slot[name] = self.memory.scratch(8)
        return self._lo_slot[name]

    @property
    def needs_memory(self) -> bool:
        """True iff a linked part reserved scratch -- review B8's marker.

        Task 10 reads this: linking any two-result 128-bit part forces linear
        memory even for a contract whose own ``compiled.needs_memory`` is
        ``False`` (a contract that only multiplies ``U128``s has no literals
        and no linear-memory host call, yet D needs a page for the ``lo``
        slots).
        """
        return bool(self._lo_slot)

    @property
    def parts_linked(self) -> frozenset[str]:
        """Every part this module links -- Task 13's superset of C's hint."""
        return frozenset(self._part_order)

    @property
    def parts(self) -> tuple[Part, ...]:
        """The linked parts in ``defidx`` order, ready to append to the module.

        Refuses to answer while a builder is still running: ``_part_order``
        gains a name at reservation, so a mid-build read would otherwise be a
        ``KeyError`` naming a part that is merely unfinished.
        """
        unfinished = [name for name in self._part_order if name not in self._built]
        if unfinished:
            raise EmitError(
                f"parts read while {unfinished} are still being built; the part "
                "list is complete only once every builder has returned"
            )
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


# ==============================================================================
# 128-bit limb arithmetic
# ==============================================================================
#
# Everything below works on RAW limb PAIRS. A four-parameter part reads
# ``(a_hi, a_lo, b_hi, b_lo)`` from locals 0-3; a two-parameter one reads
# ``(hi, lo)`` from locals 0-1. Nothing here ever sees a ``Val`` except the
# box/unbox pair, and nothing here ever returns two values -- see the module
# docstring's two-result convention and its two safety invariants.


# --- small emission helpers, limb flavour --------------------------------------


def _truthy(fn: Fn) -> None:
    """An i64 0/1 flag on the stack -> the i32 boolean an ``if`` wants."""
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_NE)


def _flag(fn: Fn, a: int, b: int, opcode: int) -> None:
    """``a <op> b`` over two locals, left on the stack as an i64 0/1 flag.

    i64 rather than i32 so flags compose with ``i64.and``/``i64.or`` -- the
    overflow criteria below are conjunctions of three and four of them.
    """
    fn.local_get(a)
    fn.local_get(b)
    fn.relop_i64(opcode)
    _extend_u(fn)


def _flag_const(fn: Fn, a: int, value: int, opcode: int) -> None:
    """``a <op> value``, left on the stack as an i64 0/1 flag."""
    fn.local_get(a)
    fn.i64_const(value)
    fn.relop_i64(opcode)
    _extend_u(fn)


def _limbs_eq(fn: Fn, hi: int, lo: int, want_hi: int, want_lo: int) -> None:
    """``(hi, lo) == (want_hi, want_lo)``, as an i64 0/1 flag.

    Both limbs, always: ``MIN`` is ``hi == 2**63`` AND ``lo == 0``, and a test
    that checked only the hi limb would call every value in
    ``[MIN, MIN + 2**64)`` ``MIN``.
    """
    _flag_const(fn, hi, want_hi, opcodes.I64_EQ)
    _flag_const(fn, lo, want_lo, opcodes.I64_EQ)
    fn.binop_i64(opcodes.I64_AND)


def _store_lo(fn: Fn, addr: int, slot: int) -> None:
    """``i64.store`` local ``slot`` at scratch address ``addr``."""
    fn.i32_const(addr)
    fn.local_get(slot)
    fn.pop("i64")
    fn.pop("i32")
    fn.op(opcodes.I64_STORE, _ALIGN_8, _OFFSET_0)


def _load_lo(fn: Fn, addr: int) -> None:
    """``i64.load`` from scratch address ``addr``, leaving the word on the stack."""
    fn.i32_const(addr)
    fn.pop("i32")
    fn.op(opcodes.I64_LOAD, _ALIGN_8, _OFFSET_0)
    fn.push("i64")


def _return_wide(fn: Fn, ctx: EmitCtx, hi: int, lo: int) -> None:
    """The two-result return: ``lo`` to THIS part's slot, ``hi`` on the stack.

    The slot is looked up from ``fn.name`` rather than passed in, so a part
    cannot be given the wrong one by a copy-paste -- writing another part's
    ``lo`` slot would be silent, and the caller would read a stale limb.
    """
    _store_lo(fn, ctx.lo_slot(fn.name), lo)
    fn.local_get(hi)
    fn.ret()


def call_wide_part(fn: Fn, ctx: EmitCtx, name: str, nargs: int) -> tuple[int, int]:
    """Call two-result part ``name`` (args already on the stack); return its limbs.

    **The only sanctioned way to call one.** The ``i64.load`` of ``lo`` is
    emitted here, immediately after the call and before any other instruction
    the caller might want -- which is invariant (2) of the module docstring's
    two-result convention (review m4). A caller that hand-rolled the call could
    interleave a second part call before the load and read the wrong limb, and
    that would validate.
    """
    defidx = ctx.ensure_part(name)
    slot = ctx.lo_slot(name)
    fn.call_defined(defidx, nargs, ("i64",))
    hi = fn.new_local()
    fn.local_set(hi)
    lo = fn.new_local()
    _load_lo(fn, slot)
    fn.local_set(lo)
    return hi, lo


def _copy128_into(fn: Fn, hi: int, lo: int, hi_dst: int, lo_dst: int) -> None:
    fn.local_get(hi)
    fn.local_set(hi_dst)
    fn.local_get(lo)
    fn.local_set(lo_dst)


def _neg128_into(fn: Fn, hi: int, lo: int, hi_dst: int, lo_dst: int) -> None:
    """``(hi_dst, lo_dst) = -(hi, lo)``, two's complement, WRAPPING.

    ``lo' = 0 - lo`` and ``hi' = 0 - hi - borrow``, where the borrow is "the
    low limb was nonzero". Wrapping is correct for every input except ``MIN``,
    whose negation is itself; callers check for ``MIN`` first.

    The high limb is written FIRST so that aliasing ``lo_dst`` onto ``lo`` (a
    caller negating in place) still reads the original low limb for the borrow.
    """
    borrow = fn.new_local()
    _flag_const(fn, lo, 0, opcodes.I64_NE)
    fn.local_set(borrow)
    fn.i64_const(0)
    fn.local_get(hi)
    fn.binop_i64(opcodes.I64_SUB)
    fn.local_get(borrow)
    fn.binop_i64(opcodes.I64_SUB)
    fn.local_set(hi_dst)
    fn.i64_const(0)
    fn.local_get(lo)
    fn.binop_i64(opcodes.I64_SUB)
    fn.local_set(lo_dst)


def _magnitude_into(fn: Fn, hi: int, lo: int, hi_dst: int, lo_dst: int) -> int:
    """``(hi_dst, lo_dst) = |(hi, lo)|``; returns the local holding "was negative".

    Exact only because every caller has already answered ``MIN`` (``0 - MIN``
    wraps back to ``MIN``, so a magnitude route cannot represent it).
    """
    negative = fn.new_local()
    _flag_const(fn, hi, 0, opcodes.I64_LT_S)
    fn.local_set(negative)
    fn.local_get(negative)
    _truthy(fn)
    fn.begin_if(None)
    _neg128_into(fn, hi, lo, hi_dst, lo_dst)
    fn.else_()
    _copy128_into(fn, hi, lo, hi_dst, lo_dst)
    fn.end_if()
    return negative


# --- addition and subtraction ---------------------------------------------------


def _add_limbs(fn: Fn, hi_dst: int, lo_dst: int) -> int:
    """``(hi_dst, lo_dst) = a + b``, WRAPPING; returns the CARRY-OUT flag's local.

    The carry out of the LOW limb is "the sum came out below the addend", the
    same unsigned-wrap test ``u64_add`` uses.

    The HIGH limb then sums THREE values -- ``a_hi``, ``b_hi``, and that carry
    -- so it can wrap in either step and **both** are tested. One test is not
    enough, and the vector that proves it is ``U128_MAX + U128_MAX``: the first
    addition wraps to ``2**64 - 2`` and folding the carry in brings it back to
    ``2**64 - 1``, which is exactly ``a_hi``, so a lone ``hi <u a_hi`` reports
    no overflow for a sum that plainly escaped.
    """
    carry = fn.new_local()
    partial = fn.new_local()
    carry_out = fn.new_local()
    fn.local_get(1)
    fn.local_get(3)
    fn.binop_i64(opcodes.I64_ADD)
    fn.local_set(lo_dst)
    _flag(fn, lo_dst, 1, opcodes.I64_LT_U)
    fn.local_set(carry)
    fn.local_get(0)
    fn.local_get(2)
    fn.binop_i64(opcodes.I64_ADD)
    fn.local_set(partial)
    _flag(fn, partial, 0, opcodes.I64_LT_U)
    fn.local_get(partial)
    fn.local_get(carry)
    fn.binop_i64(opcodes.I64_ADD)
    fn.local_set(hi_dst)
    _flag(fn, hi_dst, partial, opcodes.I64_LT_U)
    fn.binop_i64(opcodes.I64_OR)
    fn.local_set(carry_out)
    return carry_out


def _sub_limbs(fn: Fn, hi_dst: int, lo_dst: int) -> None:
    """``(hi_dst, lo_dst) = a - b``, WRAPPING, with the borrow propagated."""
    borrow = fn.new_local()
    _flag(fn, 1, 3, opcodes.I64_LT_U)
    fn.local_set(borrow)
    fn.local_get(1)
    fn.local_get(3)
    fn.binop_i64(opcodes.I64_SUB)
    fn.local_set(lo_dst)
    fn.local_get(0)
    fn.local_get(2)
    fn.binop_i64(opcodes.I64_SUB)
    fn.local_get(borrow)
    fn.binop_i64(opcodes.I64_SUB)
    fn.local_set(hi_dst)


def _build_u128_add(ctx: EmitCtx) -> Fn:
    """``a + b``; overflow iff the 128-bit sum carried out of the HIGH limb.

    Which is exactly ``_add_limbs``' carry-out flag -- see its docstring for
    why that flag is the OR of two wrap tests and not one.
    """
    fn = _part_fn("u128_add", 4)
    hi = fn.new_local()
    lo = fn.new_local()
    carry_out = _add_limbs(fn, hi, lo)
    fn.local_get(carry_out)
    _truthy(fn)
    _fail_if(fn, ctx)
    _return_wide(fn, ctx, hi, lo)
    return fn


def _build_i128_add(ctx: EmitCtx) -> Fn:
    """``a + b``; overflow iff ``((a_hi ^ hi) & (b_hi ^ hi)) < 0``.

    ``i64_add``'s sign analysis, applied to the HIGH limb -- which is where a
    128-bit two's-complement value keeps its sign. The carry from the low limb
    does not change the criterion: it only changes what the hi limb sums to.
    """
    fn = _part_fn("i128_add", 4)
    hi = fn.new_local()
    lo = fn.new_local()
    _add_limbs(fn, hi, lo)
    fn.local_get(0)
    fn.local_get(hi)
    fn.binop_i64(opcodes.I64_XOR)
    fn.local_get(2)
    fn.local_get(hi)
    fn.binop_i64(opcodes.I64_XOR)
    fn.binop_i64(opcodes.I64_AND)
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_LT_S)
    _fail_if(fn, ctx)
    _return_wide(fn, ctx, hi, lo)
    return fn


def _build_u128_sub(ctx: EmitCtx) -> Fn:
    """``a - b``; overflow iff ``a < b`` (there are no negative U128s).

    The 128-bit unsigned compare, written out: the hi limbs decide unless they
    are equal, and then the lo limbs do -- UNSIGNED, always, a low limb having
    no sign of its own.
    """
    fn = _part_fn("u128_sub", 4)
    hi = fn.new_local()
    lo = fn.new_local()
    _flag(fn, 0, 2, opcodes.I64_LT_U)
    _flag(fn, 0, 2, opcodes.I64_EQ)
    _flag(fn, 1, 3, opcodes.I64_LT_U)
    fn.binop_i64(opcodes.I64_AND)
    fn.binop_i64(opcodes.I64_OR)
    _truthy(fn)
    _fail_if(fn, ctx)
    _sub_limbs(fn, hi, lo)
    _return_wide(fn, ctx, hi, lo)
    return fn


def _build_i128_sub(ctx: EmitCtx) -> Fn:
    """``a - b``; overflow iff ``((a_hi ^ b_hi) & (a_hi ^ hi)) < 0``.

    ``i64_sub``'s analysis one limb up: the operands' signs must differ AND
    the result's sign must differ from the minuend's.
    """
    fn = _part_fn("i128_sub", 4)
    hi = fn.new_local()
    lo = fn.new_local()
    _sub_limbs(fn, hi, lo)
    fn.local_get(0)
    fn.local_get(2)
    fn.binop_i64(opcodes.I64_XOR)
    fn.local_get(0)
    fn.local_get(hi)
    fn.binop_i64(opcodes.I64_XOR)
    fn.binop_i64(opcodes.I64_AND)
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_LT_S)
    _fail_if(fn, ctx)
    _return_wide(fn, ctx, hi, lo)
    return fn


# --- multiplication -------------------------------------------------------------


def _build_mul64_wide(ctx: EmitCtx) -> Fn:
    """``a * b`` as a FULL 128-bit product, from four 32x32->64 partials (S13).

    ``i64.mul_wide_s`` is **banned**: the chain's wasmi 0.31 has no
    wide-arithmetic proposal, so a module using it would run in a laxer
    harness and trap on chain -- the one direction a lowering must never be
    wrong in. The harness pins the feature off for the same reason.

    Splitting each operand into 32-bit halves makes every partial product
    exact in 64 bits, and so is every accumulation below:

    * ``mid`` is one 32-bit value plus two more, so it stays under ``2**34``;
    * ``hi`` is ``a1*b1 <= (2**32 - 1)**2`` plus three values under ``2**32``,
      which is still under ``2**64``.

    Two-result: ``lo`` goes to this part's scratch slot, ``hi`` is returned.
    """
    fn = _part_fn("mul64_wide", 2)
    a0 = fn.new_local()
    a1 = fn.new_local()
    b0 = fn.new_local()
    b1 = fn.new_local()
    p00 = fn.new_local()
    p01 = fn.new_local()
    p10 = fn.new_local()
    mid = fn.new_local()
    hi = fn.new_local()
    lo = fn.new_local()

    for src, low, high in ((0, a0, a1), (1, b0, b1)):
        fn.local_get(src)
        fn.i64_const(_MASK32)
        fn.binop_i64(opcodes.I64_AND)
        fn.local_set(low)
        fn.local_get(src)
        fn.i64_const(_HALF_LIMB)
        fn.binop_i64(opcodes.I64_SHR_U)
        fn.local_set(high)

    for dst, left, right in ((p00, a0, b0), (p01, a0, b1), (p10, a1, b0)):
        fn.local_get(left)
        fn.local_get(right)
        fn.binop_i64(opcodes.I64_MUL)
        fn.local_set(dst)

    # mid = high(p00) + low(p01) + low(p10) -- the column at 2**32.
    fn.local_get(p00)
    fn.i64_const(_HALF_LIMB)
    fn.binop_i64(opcodes.I64_SHR_U)
    fn.local_get(p01)
    fn.i64_const(_MASK32)
    fn.binop_i64(opcodes.I64_AND)
    fn.binop_i64(opcodes.I64_ADD)
    fn.local_get(p10)
    fn.i64_const(_MASK32)
    fn.binop_i64(opcodes.I64_AND)
    fn.binop_i64(opcodes.I64_ADD)
    fn.local_set(mid)

    # lo = low(p00) | (low(mid) << 32)
    fn.local_get(p00)
    fn.i64_const(_MASK32)
    fn.binop_i64(opcodes.I64_AND)
    fn.local_get(mid)
    fn.i64_const(_HALF_LIMB)
    fn.binop_i64(opcodes.I64_SHL)
    fn.binop_i64(opcodes.I64_OR)
    fn.local_set(lo)

    # hi = a1*b1 + high(p01) + high(p10) + high(mid)
    fn.local_get(a1)
    fn.local_get(b1)
    fn.binop_i64(opcodes.I64_MUL)
    for src in (p01, p10, mid):
        fn.local_get(src)
        fn.i64_const(_HALF_LIMB)
        fn.binop_i64(opcodes.I64_SHR_U)
        fn.binop_i64(opcodes.I64_ADD)
    fn.local_set(hi)

    _return_wide(fn, ctx, hi, lo)
    return fn


def _mul64_wide(fn: Fn, ctx: EmitCtx, a: int, b: int) -> tuple[int, int]:
    """``mul64_wide(a, b)`` over two locals; returns its ``(hi, lo)`` locals."""
    fn.local_get(a)
    fn.local_get(b)
    return call_wide_part(fn, ctx, "mul64_wide", 2)


def _umul128_checked(
    fn: Fn, ctx: EmitCtx, a_hi: int, a_lo: int, b_hi: int, b_lo: int
) -> tuple[int, int]:
    """The 128-bit product of two UNSIGNED limb pairs, or ArithmeticOverflow.

    Overflow iff **any bit escapes the 128-bit window** (review B9's unsigned
    criterion). The four cross products sit at weights ``2**0``, ``2**64``,
    ``2**64`` and ``2**128``::

        a_lo*b_lo -> (h0, l0)      a_lo*b_hi -> (h1, l1)
        a_hi*b_lo -> (h2, l2)      a_hi*b_hi -> entirely above the window

    so the answer is ``(h0 + l1 + l2, l0)`` and a bit escapes iff ``a_hi`` and
    ``b_hi`` are both nonzero, or either of ``h1``/``h2`` is nonzero, or one of
    the two high-limb additions carries out. ``a_hi * b_hi`` is never computed:
    as an exact integer it is zero iff one of its operands is.
    """
    h0, l0 = _mul64_wide(fn, ctx, a_lo, b_lo)
    h1, l1 = _mul64_wide(fn, ctx, a_lo, b_hi)
    h2, l2 = _mul64_wide(fn, ctx, a_hi, b_lo)

    _flag_const(fn, a_hi, 0, opcodes.I64_NE)
    _flag_const(fn, b_hi, 0, opcodes.I64_NE)
    fn.binop_i64(opcodes.I64_AND)
    _truthy(fn)
    _fail_if(fn, ctx)

    _flag_const(fn, h1, 0, opcodes.I64_NE)
    _flag_const(fn, h2, 0, opcodes.I64_NE)
    fn.binop_i64(opcodes.I64_OR)
    _truthy(fn)
    _fail_if(fn, ctx)

    partial = fn.new_local()
    fn.local_get(h0)
    fn.local_get(l1)
    fn.binop_i64(opcodes.I64_ADD)
    fn.local_tee(partial)
    fn.local_get(h0)
    fn.relop_i64(opcodes.I64_LT_U)
    _fail_if(fn, ctx)

    hi = fn.new_local()
    fn.local_get(partial)
    fn.local_get(l2)
    fn.binop_i64(opcodes.I64_ADD)
    fn.local_tee(hi)
    fn.local_get(partial)
    fn.relop_i64(opcodes.I64_LT_U)
    _fail_if(fn, ctx)
    return hi, l0


def _mul128_wrapping(
    fn: Fn, ctx: EmitCtx, a_hi: int, a_lo: int, b_hi: int, b_lo: int
) -> tuple[int, int]:
    """``a * b`` truncated to 128 bits, UNCHECKED -- the ``%`` reconstruction.

    Used only for ``lhs - (lhs // rhs) * rhs``, where the product is
    mathematically no larger in magnitude than ``lhs`` (the quotient came out
    of an exactness-checked division), so nothing can escape and there is
    nothing to check. Signedness does not enter: two's-complement multiply is
    the same operation for both.
    """
    h0, l0 = _mul64_wide(fn, ctx, a_lo, b_lo)
    hi = fn.new_local()
    fn.local_get(h0)
    fn.local_get(a_lo)
    fn.local_get(b_hi)
    fn.binop_i64(opcodes.I64_MUL)
    fn.binop_i64(opcodes.I64_ADD)
    fn.local_get(a_hi)
    fn.local_get(b_lo)
    fn.binop_i64(opcodes.I64_MUL)
    fn.binop_i64(opcodes.I64_ADD)
    fn.local_set(hi)
    return hi, l0


def _build_u128_mul(ctx: EmitCtx) -> Fn:
    """``a * b``; overflow iff any bit escapes the 128-bit window (review B9)."""
    fn = _part_fn("u128_mul", 4)
    hi, lo = _umul128_checked(fn, ctx, 0, 1, 2, 3)
    _return_wide(fn, ctx, hi, lo)
    return fn


def _min128_arm(fn: Fn, ctx: EmitCtx, other_hi: int, other_lo: int) -> None:
    """The ``MIN * other`` arm of ``i128_mul`` -- ``i64_mul``'s M7 arm, widened.

    ``MIN`` has no positive twin, so the magnitude route below cannot handle
    it. Only two products involving it are representable -- ``MIN * 0 == 0``
    and ``MIN * 1 == MIN`` -- so they are answered here and everything else,
    ``MIN * -1`` included, is the overflow.

    The trailing return is never executed (the host traps inside
    ``fail_with_error``); it is there so the arm returns on paper rather than
    falling through into the magnitude code, which is exactly the code that
    cannot represent ``MIN``.
    """
    out_hi = fn.new_local()
    out_lo = fn.new_local()
    for other_wanted_lo, result_hi in ((0, 0), (1, _I64_MIN)):
        _limbs_eq(fn, other_hi, other_lo, 0, other_wanted_lo)
        _truthy(fn)
        fn.begin_if(None)
        fn.i64_const(result_hi)
        fn.local_set(out_hi)
        fn.i64_const(0)
        fn.local_set(out_lo)
        _return_wide(fn, ctx, out_hi, out_lo)
        fn.end_if()
    _fail_overflow(fn, ctx)
    fn.i64_const(0)
    fn.local_set(out_hi)
    fn.i64_const(0)
    fn.local_set(out_lo)
    _return_wide(fn, ctx, out_hi, out_lo)


def _build_i128_mul(ctx: EmitCtx) -> Fn:
    """``a * b`` over MAGNITUDES, with the sign applied last (review B9).

    The unsigned criterion is **wrong** for signed operands and the review
    names the vector: ``I128(-1) * I128(-1)`` has limbs ``hi = lo = 2**64 - 1``
    on both sides, whose 256-bit unsigned product has a nonzero high half --
    "a bit escaped the window" would report overflow for an answer of ``1``.

    So: answer ``MIN`` first (it has no magnitude), take magnitudes, multiply
    them with the unsigned check, then compare against the bound for the
    RESULT's own sign -- ``2**127 - 1`` when non-negative, ``2**127`` when
    negative, because ``2**126 * -2`` is exactly ``MIN`` and legal while
    ``2**126 * 2`` is not -- and only then apply the sign.
    """
    fn = _part_fn("i128_mul", 4)
    for operand_hi, operand_lo, other_hi, other_lo in ((0, 1, 2, 3), (2, 3, 0, 1)):
        _limbs_eq(fn, operand_hi, operand_lo, _I64_MIN, 0)
        _truthy(fn)
        fn.begin_if(None)
        _min128_arm(fn, ctx, other_hi, other_lo)
        fn.end_if()

    lhs_hi = fn.new_local()
    lhs_lo = fn.new_local()
    rhs_hi = fn.new_local()
    rhs_lo = fn.new_local()
    lhs_negative = _magnitude_into(fn, 0, 1, lhs_hi, lhs_lo)
    rhs_negative = _magnitude_into(fn, 2, 3, rhs_hi, rhs_lo)

    product_hi, product_lo = _umul128_checked(fn, ctx, lhs_hi, lhs_lo, rhs_hi, rhs_lo)

    negative = fn.new_local()
    fn.local_get(lhs_negative)
    fn.local_get(rhs_negative)
    fn.binop_i64(opcodes.I64_XOR)
    fn.local_set(negative)

    # The magnitude's top bit set means it is at least 2**127; the ONE such
    # magnitude that is still representable is exactly 2**127, and only when
    # the result is negative (it is |MIN|).
    exactly_min = fn.new_local()
    fn.local_get(negative)
    _limbs_eq(fn, product_hi, product_lo, _I64_MIN, 0)
    fn.binop_i64(opcodes.I64_AND)
    fn.local_set(exactly_min)
    _flag_const(fn, product_hi, 0, opcodes.I64_LT_S)
    _flag_const(fn, exactly_min, 0, opcodes.I64_EQ)
    fn.binop_i64(opcodes.I64_AND)
    _truthy(fn)
    _fail_if(fn, ctx)

    hi = fn.new_local()
    lo = fn.new_local()
    fn.local_get(negative)
    _truthy(fn)
    fn.begin_if(None)
    _neg128_into(fn, product_hi, product_lo, hi, lo)
    fn.else_()
    _copy128_into(fn, product_hi, product_lo, hi, lo)
    fn.end_if()
    _return_wide(fn, ctx, hi, lo)
    return fn


# --- negation and comparison ----------------------------------------------------


def _build_u128_neg(ctx: EmitCtx) -> Fn:
    """``-x``: there is no negative U128, so anything nonzero is the overflow."""
    fn = _part_fn("u128_neg", 2)
    hi = fn.new_local()
    lo = fn.new_local()
    fn.local_get(0)
    fn.local_get(1)
    fn.binop_i64(opcodes.I64_OR)
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_NE)
    _fail_if(fn, ctx)
    fn.i64_const(0)
    fn.local_set(hi)
    fn.i64_const(0)
    fn.local_set(lo)
    _return_wide(fn, ctx, hi, lo)
    return fn


def _build_i128_neg(ctx: EmitCtx) -> Fn:
    """``-x``: ``MIN`` is the overflow (no positive twin), else limb-wise negate."""
    fn = _part_fn("i128_neg", 2)
    hi = fn.new_local()
    lo = fn.new_local()
    _limbs_eq(fn, 0, 1, _I64_MIN, 0)
    _truthy(fn)
    _fail_if(fn, ctx)
    _neg128_into(fn, 0, 1, hi, lo)
    _return_wide(fn, ctx, hi, lo)
    return fn


def _wide_cmp_fn(name: str, hi_lt: int) -> Fn:
    """``cmp(a, b)`` -> raw ``-1``/``0``/``1`` (ruling E16).

    ``hi_lt`` is ``i64.lt_s`` for I128 and ``i64.lt_u`` for U128 -- the HIGH
    limb is where a 128-bit value's sign lives. The LOW limbs are compared
    UNSIGNED at both signednesses: a low limb is a magnitude, not a number,
    and comparing it signed inverts the order of every pair whose low limbs
    straddle ``2**63``.
    """
    fn = _part_fn(name, 4)
    for left, right, less_than in ((0, 2, hi_lt), (1, 3, opcodes.I64_LT_U)):
        fn.local_get(left)
        fn.local_get(right)
        fn.relop_i64(opcodes.I64_NE)
        fn.begin_if(None)
        fn.local_get(left)
        fn.local_get(right)
        fn.relop_i64(less_than)
        fn.begin_if("i64")
        fn.i64_const(-1)
        fn.else_()
        fn.i64_const(1)
        fn.end_if()
        fn.ret()
        fn.end_if()
    fn.i64_const(0)
    fn.ret()
    return fn


def _build_u128_cmp(_ctx: EmitCtx) -> Fn:
    return _wide_cmp_fn("u128_cmp", opcodes.I64_LT_U)


def _build_i128_cmp(_ctx: EmitCtx) -> Fn:
    return _wide_cmp_fn("i128_cmp", opcodes.I64_LT_S)


# --- the i256 division route (S13, F.1.2, F.1.12, review B4) --------------------


def _pack_256(fn: Fn, ctx: EmitCtx, hi: int, lo: int, *, signed: bool) -> int:
    """Widen a 128-bit limb pair to a 256-bit ``Val``; returns its local.

    **F.1.12:** the two new limbs are the pair's SIGN EXTENSION, which for a
    negative I128 is two all-ones words -- ``hi >>s 63`` produces exactly
    that, and produces zero for a non-negative one. Zero-filling them instead
    would turn every negative dividend into a colossal positive number, which
    divides perfectly well and returns a plausible wrong answer.

    ``obj_from_i256_pieces``' first argument is the pin's one SIGNED
    parameter (``('i64', 'u64', 'u64', 'u64')``); the guest passes raw words
    either way, so the note is for the reader, not the encoder.
    """
    extension = fn.new_local()
    if signed:
        fn.local_get(hi)
        fn.i64_const(_SIGN_SHIFT)
        fn.binop_i64(opcodes.I64_SHR_S)
    else:
        fn.i64_const(0)
    fn.local_set(extension)
    fn.local_get(extension)
    fn.local_get(extension)
    fn.local_get(hi)
    fn.local_get(lo)
    constructor = "obj_from_i256_pieces" if signed else "obj_from_u256_pieces"
    fn.call_import(ctx.host_import_name(constructor), 4, has_result=True)
    packed = fn.new_local()
    fn.local_set(packed)
    return packed


def _unpack_256(fn: Fn, ctx: EmitCtx, packed: int, *, signed: bool) -> tuple[int, int, int, int]:
    """A 256-bit ``Val`` -> its four raw limbs, TAG-BRANCHED (review B4).

    ``{i,u}256_div`` returns an ``I256Val``/``U256Val``, and the host hands
    back the SMALL form for any value inside the 56-bit body -- which is
    almost every real quotient, ``U128(100) // U128(5)`` included. The four
    ``obj_to_*`` accessors take an OBJECT and nothing else, so calling one on
    a small-form result is a host conversion error rather than a contract
    error: the wrong failure, on the common path.

    On the small path one shift IS the low limb (review B10's recipe: the
    56-bit body's sign bit already sits at word bit 63), and its sign or zero
    extension IS the other three limbs -- no host call at all.
    """
    hi_hi = fn.new_local()
    hi_lo = fn.new_local()
    lo_hi = fn.new_local()
    lo_lo = fn.new_local()
    small_tag = val.TAG_I256_SMALL if signed else val.TAG_U256_SMALL
    prefix = "i" if signed else "u"

    fn.local_get(packed)
    fn.i64_const(_TAG_MASK)
    fn.binop_i64(opcodes.I64_AND)
    fn.i64_const(small_tag)
    fn.relop_i64(opcodes.I64_EQ)
    fn.begin_if(None)
    fn.local_get(packed)
    fn.i64_const(_TAG_BITS)
    fn.binop_i64(opcodes.I64_SHR_S if signed else opcodes.I64_SHR_U)
    fn.local_set(lo_lo)
    if signed:
        fn.local_get(lo_lo)
        fn.i64_const(_SIGN_SHIFT)
        fn.binop_i64(opcodes.I64_SHR_S)
    else:
        fn.i64_const(0)
    fn.local_set(lo_hi)
    for src, dst in ((lo_hi, hi_lo), (lo_hi, hi_hi)):
        fn.local_get(src)
        fn.local_set(dst)
    fn.else_()
    for slot, limb in ((hi_hi, "hi_hi"), (hi_lo, "hi_lo"), (lo_hi, "lo_hi"), (lo_lo, "lo_lo")):
        fn.local_get(packed)
        fn.call_import(ctx.host_import_name(f"obj_to_{prefix}256_{limb}"), 1, has_result=True)
        fn.local_set(slot)
    fn.end_if()
    return hi_hi, hi_lo, lo_hi, lo_lo


def _check_extension(
    fn: Fn, ctx: EmitCtx, hi_hi: int, hi_lo: int, lo_hi: int, *, signed: bool
) -> None:
    """Refuse a 256-bit result whose top two limbs are not pure extension.

    A quotient of two 128-bit values only leaves the 128-bit range at
    ``MIN // -1``, which the signed route already answered -- so this never
    fires today. It is here because "the result narrows" is an assumption the
    route makes on every single division, and an unchecked narrowing is the
    F.1.3 anti-pattern one width up: it would validate, deploy, and return the
    low half of the wrong number.
    """
    extension = fn.new_local()
    if signed:
        fn.local_get(lo_hi)
        fn.i64_const(_SIGN_SHIFT)
        fn.binop_i64(opcodes.I64_SHR_S)
    else:
        fn.i64_const(0)
    fn.local_set(extension)
    _flag(fn, hi_hi, extension, opcodes.I64_NE)
    _flag(fn, hi_lo, extension, opcodes.I64_NE)
    fn.binop_i64(opcodes.I64_OR)
    _truthy(fn)
    _fail_if(fn, ctx)


def _min_over_minus_one(fn: Fn) -> None:
    """``a == MIN && b == -1``, as an i32 boolean -- A4's one special pair."""
    _limbs_eq(fn, 0, 1, _I64_MIN, 0)
    _limbs_eq(fn, 2, 3, -1, -1)
    fn.binop_i64(opcodes.I64_AND)
    _truthy(fn)


def _wide_floordiv(ctx: EmitCtx, prefix: str, *, signed: bool) -> Fn:
    """``a // b`` through the host's 256-bit division (S13).

    ``b == 0`` is NOT tested here: with no 128-bit division instruction to
    trap, the zero divisor reaches ``{i,u}256_div``, which answers ``ScError``
    and aborts the invocation. That is a different trap class from the 32/64-bit
    widths, where A4 leaves ``//0`` to wasm's own trap -- recorded in the task
    report as a divergence for A10's mapping rather than papered over here.

    ``MIN // -1`` IS tested first, and for the opposite reason: A4 makes it
    ``ArithmeticOverflow``, and letting the host answer it would produce the
    host's own error class instead.
    """
    fn = _part_fn(f"{prefix}_floordiv", 4)
    if signed:
        _min_over_minus_one(fn)
        _fail_if(fn, ctx)
    lhs = _pack_256(fn, ctx, 0, 1, signed=signed)
    rhs = _pack_256(fn, ctx, 2, 3, signed=signed)
    fn.local_get(lhs)
    fn.local_get(rhs)
    divider = "i256_div" if signed else "u256_div"
    fn.call_import(ctx.host_import_name(divider), 2, has_result=True)
    quotient = fn.new_local()
    fn.local_set(quotient)
    hi_hi, hi_lo, lo_hi, lo_lo = _unpack_256(fn, ctx, quotient, signed=signed)
    _check_extension(fn, ctx, hi_hi, hi_lo, lo_hi, signed=signed)
    _return_wide(fn, ctx, lo_hi, lo_lo)
    return fn


def _wide_mod(ctx: EmitCtx, prefix: str, *, signed: bool) -> Fn:
    """``a % b`` as ``a - (a // b) * b``, in guest limb code (F.1.2).

    **Never ``{i,u}256_rem_euclid``.** Their documented modulo is EUCLIDEAN
    and always non-negative; A4's ``%`` follows the DIVIDEND, so the two
    disagree on ``-7 % 2`` (``+1`` against ``-1``) and agree everywhere a
    casual test would look. The quotient comes from this route's own division
    part, and the multiply-subtract that reconstructs the remainder cannot
    overflow: ``|q * b| <= |a|`` because ``q`` truncates.

    ``MIN % -1`` is answered as ``0`` first (A4). It has to be: the quotient
    part would correctly call ``MIN // -1`` an overflow.
    """
    fn = _part_fn(f"{prefix}_mod", 4)
    if signed:
        zero_hi = fn.new_local()
        zero_lo = fn.new_local()
        _min_over_minus_one(fn)
        fn.begin_if(None)
        fn.i64_const(0)
        fn.local_set(zero_hi)
        fn.i64_const(0)
        fn.local_set(zero_lo)
        _return_wide(fn, ctx, zero_hi, zero_lo)
        fn.end_if()
    for i in range(4):
        fn.local_get(i)
    quotient_hi, quotient_lo = call_wide_part(fn, ctx, f"{prefix}_floordiv", 4)
    product_hi, product_lo = _mul128_wrapping(fn, ctx, quotient_hi, quotient_lo, 2, 3)

    borrow = fn.new_local()
    hi = fn.new_local()
    lo = fn.new_local()
    _flag(fn, 1, product_lo, opcodes.I64_LT_U)
    fn.local_set(borrow)
    fn.local_get(1)
    fn.local_get(product_lo)
    fn.binop_i64(opcodes.I64_SUB)
    fn.local_set(lo)
    fn.local_get(0)
    fn.local_get(product_hi)
    fn.binop_i64(opcodes.I64_SUB)
    fn.local_get(borrow)
    fn.binop_i64(opcodes.I64_SUB)
    fn.local_set(hi)
    _return_wide(fn, ctx, hi, lo)
    return fn


def _build_u128_floordiv(ctx: EmitCtx) -> Fn:
    return _wide_floordiv(ctx, "u128", signed=False)


def _build_i128_floordiv(ctx: EmitCtx) -> Fn:
    return _wide_floordiv(ctx, "i128", signed=True)


def _build_u128_mod(ctx: EmitCtx) -> Fn:
    return _wide_mod(ctx, "u128", signed=False)


def _build_i128_mod(ctx: EmitCtx) -> Fn:
    return _wide_mod(ctx, "i128", signed=True)


# --- the 128-bit boxing bridge (S14, review m7) ---------------------------------


def _build_unbox_u128(ctx: EmitCtx) -> Fn:
    """``Val`` -> raw limbs: tag 10 is the small form, anything else an object."""
    fn = _part_fn("unbox_u128", 1)
    hi = fn.new_local()
    lo = fn.new_local()
    fn.local_get(0)
    fn.i64_const(_TAG_MASK)
    fn.binop_i64(opcodes.I64_AND)
    fn.i64_const(val.TAG_U128_SMALL)
    fn.relop_i64(opcodes.I64_EQ)
    fn.begin_if(None)
    fn.local_get(0)
    fn.i64_const(_TAG_BITS)
    fn.binop_i64(opcodes.I64_SHR_U)
    fn.local_set(lo)
    fn.i64_const(0)
    fn.local_set(hi)
    _return_wide(fn, ctx, hi, lo)
    fn.end_if()
    for slot, accessor in ((hi, "obj_to_u128_hi64"), (lo, "obj_to_u128_lo64")):
        fn.local_get(0)
        fn.call_import(ctx.host_import_name(accessor), 1, has_result=True)
        fn.local_set(slot)
    _return_wide(fn, ctx, hi, lo)
    return fn


def _build_unbox_i128(ctx: EmitCtx) -> Fn:
    """``Val`` -> raw limbs: tag 11 small (SIGN-EXTENDED into hi, m7), else object.

    The small path is ``shr_s 8`` for the low limb -- review B10's one-shift
    recipe -- and then ``>> 63`` for the high limb, because a small ``I128``
    body is a 56-bit signed number and its two's-complement 128-bit form has
    an all-ones high limb when it is negative. Zero-filling hi instead turns
    ``I128(-1)`` into ``2**64 - 1``.
    """
    fn = _part_fn("unbox_i128", 1)
    hi = fn.new_local()
    lo = fn.new_local()
    fn.local_get(0)
    fn.i64_const(_TAG_MASK)
    fn.binop_i64(opcodes.I64_AND)
    fn.i64_const(val.TAG_I128_SMALL)
    fn.relop_i64(opcodes.I64_EQ)
    fn.begin_if(None)
    fn.local_get(0)
    fn.i64_const(_TAG_BITS)
    fn.binop_i64(opcodes.I64_SHR_S)
    fn.local_set(lo)
    fn.local_get(lo)
    fn.i64_const(_SIGN_SHIFT)
    fn.binop_i64(opcodes.I64_SHR_S)
    fn.local_set(hi)
    _return_wide(fn, ctx, hi, lo)
    fn.end_if()
    for slot, accessor in ((hi, "obj_to_i128_hi64"), (lo, "obj_to_i128_lo64")):
        fn.local_get(0)
        fn.call_import(ctx.host_import_name(accessor), 1, has_result=True)
        fn.local_set(slot)
    _return_wide(fn, ctx, hi, lo)
    return fn


def _build_box_u128(ctx: EmitCtx) -> Fn:
    """Raw limbs -> ``Val``: the small form when it fits, else the object form.

    The runtime ``fits_small_u`` on a PAIR is ``hi == 0 && lo <=u MASK56``
    (review m7). The ``hi == 0`` half is not optional: without it every value
    above ``2**64`` would box as a perfectly valid small form of its low limb.
    """
    fn = _part_fn("box_u128", 2)
    _flag_const(fn, 0, 0, opcodes.I64_EQ)
    _flag_const(fn, 1, val.MAX_SMALL_U64, opcodes.I64_LE_U)
    fn.binop_i64(opcodes.I64_AND)
    _truthy(fn)
    fn.begin_if("i64")
    _pack_small(fn, 1, val.TAG_U128_SMALL)
    fn.else_()
    fn.local_get(0)
    fn.local_get(1)
    fn.call_import(ctx.host_import_name("obj_from_u128_pieces"), 2, has_result=True)
    fn.end_if()
    fn.ret()
    return fn


def _build_box_i128(ctx: EmitCtx) -> Fn:
    """Raw limbs -> ``Val``: the small form when it fits, else the object form.

    The runtime ``fits_small_i`` on a PAIR (review m7) is ``hi == (lo >>s 63)``
    -- the high limb must BE the low limb's sign extension, or the value does
    not fit in 56 bits whatever the low limb says -- AND ``lo`` inside
    ``[MIN_SMALL_I64, MAX_SMALL_I64]`` signed.
    """
    fn = _part_fn("box_i128", 2)
    extension = fn.new_local()
    fn.local_get(1)
    fn.i64_const(_SIGN_SHIFT)
    fn.binop_i64(opcodes.I64_SHR_S)
    fn.local_set(extension)
    _flag(fn, 0, extension, opcodes.I64_EQ)
    _flag_const(fn, 1, val.MIN_SMALL_I64, opcodes.I64_GE_S)
    fn.binop_i64(opcodes.I64_AND)
    _flag_const(fn, 1, val.MAX_SMALL_I64, opcodes.I64_LE_S)
    fn.binop_i64(opcodes.I64_AND)
    _truthy(fn)
    fn.begin_if("i64")
    _pack_small(fn, 1, val.TAG_I128_SMALL)
    fn.else_()
    fn.local_get(0)
    fn.local_get(1)
    fn.call_import(ctx.host_import_name("obj_from_i128_pieces"), 2, has_result=True)
    fn.end_if()
    fn.ret()
    return fn


# --- the one ABI tag-check part (E14, review M9) --------------------------------


def _fail_abi_check(fn: Fn, ctx: EmitCtx) -> None:
    """Abort with ``CODE_ABI_CHECK_FAILED`` -- ONE code for every position (C19).

    Shaped exactly like ``_fail_overflow``, and for the same reason: no
    ``unreachable`` follows, because the host traps inside ``fail_with_error``
    and an ``unreachable`` here would replace the contract error a client needs
    to see with a generic VM trap (P14).
    """
    fn.i64_const(val.error_val(CODE_ABI_CHECK_FAILED))
    fn.call_import(ctx.host_import_name("fail_with_error"), 1, has_result=True)
    fn.drop()


def _build_tagcheck_bytes_n(ctx: EmitCtx) -> Fn:
    """``tagcheck_bytes_n(v: Val, n: raw u32) -> Val`` -- returns ``v``, or fails.

    **The only tag-check that is a part** (review M9). Every other type's ABI
    check is an inline tag compare of about eight instructions, which loses
    against 74 instructions of call overhead (S25); this one contains a HOST
    CALL -- the tag alone cannot tell a 31-byte payload from a 32-byte one --
    so it clears the break-even and is worth linking once per module.

    **Order is load-bearing.** The tag is checked FIRST: ``bytes_len`` on a
    non-``Bytes`` ``Val`` is the host's own error, which a client cannot tell
    apart from a real one, and ``CODE_ABI_CHECK_FAILED`` is precisely the
    answer this check exists to give.

    ``bytes_len`` returns a ``U32Val`` (``val_typed_ret`` is ``True`` in the
    pin), so its result is unboxed with the same ``shr_u 32`` every other
    ``U32Val`` takes before being compared against the raw ``n`` the caller
    supplied. Returning ``v`` is what makes the signature usable from a stack
    position as well as from a local one.
    """
    fn = _part_fn("tagcheck_bytes_n", 2)
    fn.local_get(0)
    fn.i64_const(_TAG_MASK)
    fn.binop_i64(opcodes.I64_AND)
    fn.i64_const(val.TAG_BYTES_OBJECT)
    fn.relop_i64(opcodes.I64_NE)
    fn.begin_if(None)
    _fail_abi_check(fn, ctx)
    fn.end_if()

    fn.local_get(0)
    fn.call_import(ctx.host_import_name("bytes_len"), 1, has_result=True)
    fn.i64_const(_IMMEDIATE_SHIFT)
    fn.binop_i64(opcodes.I64_SHR_U)
    fn.local_get(1)
    fn.relop_i64(opcodes.I64_NE)
    fn.begin_if(None)
    _fail_abi_check(fn, ctx)
    fn.end_if()

    fn.local_get(0)
    fn.ret()
    return fn


# --- the SymbolSmall ordering part (review B1, ruling E16 as amended) ----------

#: `SymbolSmall`'s alphabet is 6 bits per character (`CODE_BITS` in
#: `soroban-env-common/src/symbol.rs` @ v28.0.2), nine characters at most
#: (`MAX_SMALL_CHARS`), packed HIGH-ORDER-FIRST with the unused high groups
#: left zero -- which is what makes those zero groups PADDING rather than
#: characters, and what the host's own iterator skips.
_SYMBOL_CODE_BITS = 6
_SYMBOL_CODE_MASK = 0x3F

#: `(MAX_SMALL_CHARS - 1) * CODE_BITS`: the shift the host's iterator uses to
#: bring the next (highest-order) character code down to the low six bits.
_SYMBOL_TOP_SHIFT = 48

#: The 6-bit code -> ASCII byte map, as the affine segments the host's own
#: `match` spells: `n @ (2..=11) => b'0' + n - 2`, `12..=37 => b'A' + n - 12`,
#: `38..=63 => b'a' + n - 38`. `(code_floor, bias)`, HIGHEST floor first so the
#: emitted chain is one monotone cascade of `>=` tests.
_SYMBOL_ASCII_SEGMENTS: tuple[tuple[int, int], ...] = (
    (38, ord("a") - 38),
    (12, ord("A") - 12),
    (2, ord("0") - 2),
)

#: Code 1 is `_`, whose ASCII is 95. The one character whose packed code and
#: ASCII rank DISAGREE -- code 1 sorts first, ASCII 95 sorts after every digit
#: and every capital -- and therefore the whole reason `symsmall_cmp` decodes
#: instead of comparing the packed bodies.
_SYMBOL_UNDERSCORE_ASCII = ord("_")

#: Not a character: the padding code, and (because no ASCII byte here is zero)
#: also the sentinel `_symbol_next_char` returns for an EXHAUSTED symbol. That
#: coincidence is load-bearing -- see `_build_symsmall_cmp`.
_SYMBOL_EXHAUSTED = 0


def _symbol_ascii_of_code(fn: Fn, code: int) -> None:
    """Push local ``code``'s ASCII byte as an i64. Precondition: ``code >= 1``.

    Three nested `if (result i64)`s, one per affine segment, with the `_`
    singleton as the innermost `else`. Code 0 never reaches here: the caller
    treats it as padding and skips it, exactly as the host's iterator does.
    """
    for floor, bias in _SYMBOL_ASCII_SEGMENTS:
        fn.local_get(code)
        fn.i64_const(floor)
        fn.relop_i64(opcodes.I64_GE_U)
        fn.begin_if("i64")
        fn.local_get(code)
        fn.i64_const(bias)
        fn.binop_i64(opcodes.I64_ADD)
        fn.else_()
    fn.i64_const(_SYMBOL_UNDERSCORE_ASCII)
    for _floor, _bias in _SYMBOL_ASCII_SEGMENTS:
        fn.end_if()


def _symbol_next_char(fn: Fn, state: int, out: int, code: int) -> None:
    """``out = next(state)``, advancing ``state`` -- ``SymbolSmallIter::next``.

    A transcription of the host's iterator, whose shape is not incidental::

        fn next(&mut self) -> Option<Self::Item> {
            while self.0 != 0 {
                let res = match ((self.0 >> ((MAX_SMALL_CHARS - 1) * CODE_BITS))
                                 & CODE_MASK) as u8 { ... _ => b'\\0' };
                self.0 <<= CODE_BITS;
                if res != b'\\0' { return Some(res as char); }
            }
            None
        }

    Two details are the reason this is a LOOP and not one shift:

    * the zero groups are SKIPPED, not compared. Canonical packing pads on the
      HIGH side, so `Symbol("B")`'s top group is padding while `Symbol("AB")`'s
      is `A` -- a position-wise compare of the two bodies would read a pad
      against a real character and answer that `"B" < "AB"`. The host reads
      characters, so this does too.
    * the loop exits on ``state == 0``, not after nine iterations. That is
      again the host: it keeps shifting until the word is empty, which for a
      54-bit body it always becomes.

    ``out`` is the ASCII byte, or `_SYMBOL_EXHAUSTED` (0) once the symbol is
    spent -- the guest's spelling of the host's `Option::None`.
    """
    fn.i64_const(_SYMBOL_EXHAUSTED)
    fn.local_set(out)
    fn.begin_block(None, breakable=True)
    fn.begin_loop()
    fn.local_get(state)
    _eqz(fn)
    fn.br_if_break()
    fn.local_get(state)
    fn.i64_const(_SYMBOL_TOP_SHIFT)
    fn.binop_i64(opcodes.I64_SHR_U)
    fn.i64_const(_SYMBOL_CODE_MASK)
    fn.binop_i64(opcodes.I64_AND)
    fn.local_set(code)
    fn.local_get(state)
    fn.i64_const(_SYMBOL_CODE_BITS)
    fn.binop_i64(opcodes.I64_SHL)
    fn.local_set(state)
    fn.local_get(code)
    fn.i64_const(_SYMBOL_EXHAUSTED)
    fn.relop_i64(opcodes.I64_NE)
    fn.begin_if(None)
    _symbol_ascii_of_code(fn, code)
    fn.local_set(out)
    fn.br_break()
    fn.end_if()
    fn.br_continue()
    fn.end()
    fn.end()


def _build_symsmall_cmp(_ctx: EmitCtx) -> Fn:
    """``symsmall_cmp(a: Val, b: Val)`` -> raw ``-1``/``0``/``1`` (review B1).

    **Why this part exists at all.** The shipped emitter lowered every
    `Symbol` comparison to `obj_cmp`, and the real host REFUSES `obj_cmp` when
    both operands are non-object `Val`s -- which two `SymbolSmall`s are -- with
    `Error(Value, UnexpectedType)`, escalated by the VM to a trap. The host's
    own `Compare<Val>` (`compare.rs` @ v28.0.2) never makes that call either:
    it reaches `SymbolSmall`'s `Ord` when neither side is an object, and this
    part is that `Ord`, in the guest.

    **The order it reproduces** is `Iterator::cmp` over DECODED characters --
    lexicographic ASCII over the symbol's text, not over the packed 6-bit
    codes, which disagree at `_` (code 1, ASCII 95). Tier 1's `Symbol.__lt__`
    compares the text, so the host and tier 1 agree; this part follows the
    HOST, and it would still follow it if they did not.

    **The `None` sentinel is free.** `Iterator::cmp` ends the shorter symbol
    with `None`, which orders BEFORE every `Some(c)`. `_symbol_next_char`
    reports exhaustion as 0, and every ASCII byte in this alphabet is at least
    48, so the plain unsigned compare of the two reported bytes already gets
    the prefix case right (`"A" < "AB"`): no separate end-of-string branch,
    and no way for one to drift out of step with the ordering.

    The two arguments are whole `Val` WORDS, so the tag byte is shifted off
    first -- `Val::get_body()` is `payload >> 8`, and the body is what the
    host's iterator walks.
    """
    fn = _part_fn("symsmall_cmp", 2)
    state_a = fn.new_local()
    state_b = fn.new_local()
    char_a = fn.new_local()
    char_b = fn.new_local()
    code = fn.new_local()
    for param, state in ((0, state_a), (1, state_b)):
        fn.local_get(param)
        fn.i64_const(_TAG_BITS)
        fn.binop_i64(opcodes.I64_SHR_U)
        fn.local_set(state)

    fn.begin_loop()
    _symbol_next_char(fn, state_a, char_a, code)
    _symbol_next_char(fn, state_b, char_b, code)
    fn.local_get(char_a)
    fn.local_get(char_b)
    fn.relop_i64(opcodes.I64_NE)
    fn.begin_if(None)
    fn.local_get(char_a)
    fn.local_get(char_b)
    fn.relop_i64(opcodes.I64_LT_U)
    fn.begin_if("i64")
    fn.i64_const(-1)
    fn.else_()
    fn.i64_const(1)
    fn.end_if()
    fn.ret()
    fn.end_if()
    # The two characters agree. If they are BOTH the exhaustion sentinel the
    # symbols ran out together, which is `Iterator::cmp`'s `(None, None)`:
    # equal. Otherwise take the next character.
    fn.local_get(char_a)
    _eqz(fn)
    fn.begin_if(None)
    fn.i64_const(0)
    fn.ret()
    fn.end_if()
    fn.br_continue()
    fn.end()
    # Unreachable: every path out of the loop above is a `return`, and the loop
    # itself has no exit edge. The tail exists so the body type-checks.
    fn.unreachable_()
    return fn


#: Every part that returns TWO results and therefore reserves a scratch slot.
#: Review B8's marker in static form: Task 10 must emit linear memory for a
#: module linking any of these, whatever `compiled.needs_memory` says.
#: `EmitCtx.needs_memory` is the dynamic twin, and a test pins the two together.
PARTS_NEEDING_MEMORY = frozenset(
    {
        "mul64_wide",
        *(
            f"{prefix}_{op}"
            for prefix in ("u128", "i128")
            for op in ("add", "sub", "mul", "neg", "floordiv", "mod")
        ),
        "unbox_u128",
        "unbox_i128",
    }
)


#: Every linkable runtime part, by name (ruling E3's ratified inventory for
#: this task). Task 6 adds the ``{u,i}128_*`` family, Task 9 ``tagcheck_bytes_n``,
#: and M1-F's review finding B1 ``symsmall_cmp``.
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
    "mul64_wide": _build_mul64_wide,
    "u128_add": _build_u128_add,
    "u128_sub": _build_u128_sub,
    "u128_mul": _build_u128_mul,
    "u128_floordiv": _build_u128_floordiv,
    "u128_mod": _build_u128_mod,
    "u128_neg": _build_u128_neg,
    "u128_cmp": _build_u128_cmp,
    "i128_add": _build_i128_add,
    "i128_sub": _build_i128_sub,
    "i128_mul": _build_i128_mul,
    "i128_floordiv": _build_i128_floordiv,
    "i128_mod": _build_i128_mod,
    "i128_neg": _build_i128_neg,
    "i128_cmp": _build_i128_cmp,
    "unbox_u128": _build_unbox_u128,
    "box_u128": _build_box_u128,
    "unbox_i128": _build_unbox_i128,
    "box_i128": _build_box_i128,
    "tagcheck_bytes_n": _build_tagcheck_bytes_n,
    "symsmall_cmp": _build_symsmall_cmp,
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


_WIDE_PREFIX: dict[TyTag, str] = {TyTag.U128: "u128", TyTag.I128: "i128"}


def _refuse(ty: Ty, what: str) -> EmitError:
    if ty.tag in _WIDE_TAGS:
        return EmitError(
            f"{ty.render()} has no {what} here: a 128-bit value travels as a limb "
            "PAIR, not one word, so it is lowered through the wide_* helpers and "
            "their runtime parts"
        )
    return EmitError(f"{ty.render()} has no {what}")


def _wide_prefix(ty: Ty, what: str) -> str:
    prefix = _WIDE_PREFIX.get(ty.tag)
    if prefix is None:
        raise EmitError(
            f"{ty.render()} is not a 128-bit type; {what} is limb code and takes U128 or I128"
        )
    return prefix


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

    ``Enum`` rides the ``U32`` arm because an int-enum value IS a bare ``u32``
    on chain (M1-E2 SS B.1) -- same ``TAG_U32``, same payload at bit 32, which
    is the fact ``lower._IMMEDIATE_ABI_WORD`` already states one layer up. It
    is the ONE arm here an int enum reaches: ``lower_binary``, ``lower_neg``
    and ``rebox`` deliberately have no ``ENUM`` row, because the frontend
    refuses enum arithmetic and enum ordering outright (SPT3003/SPT3005) and a
    lowering nothing can reach would be dead code that also quietly sanctions
    the operation. Unboxing is different: ``==``/``!=`` on an enum IS
    supported, tier 1 answers it, and ``_lower_compare``'s direct-relop arm
    unboxes both sides first (F.1.1).
    """
    if ty.tag in (TyTag.U32, TyTag.ENUM):
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


# --- the 128-bit lowering entry points -----------------------------------------
#
# Separate from the four above because a 128-bit value is a limb PAIR: these
# take and return local INDICES rather than leaving one word on the stack.
# Every one of them routes through `call_wide_part`, which is what keeps the
# two-result convention's "load lo immediately" invariant in one place.


def wide_binary(fn: Fn, ctx: EmitCtx, ty: Ty, op: BinaryOp) -> tuple[int, int]:
    """Lower a 128-bit ``lhs <op> rhs``; four RAW limbs already on the stack.

    Push order is ``a_hi, a_lo, b_hi, b_lo``. Returns the ``(hi, lo)`` locals
    holding the result -- boxing is the caller's business, as at every other
    width.
    """
    prefix = _wide_prefix(ty, "checked arithmetic")
    return call_wide_part(fn, ctx, f"{prefix}_{op.name.lower()}", 4)


def wide_neg(fn: Fn, ctx: EmitCtx, ty: Ty) -> tuple[int, int]:
    """Lower a 128-bit unary ``-``; two RAW limbs (``hi``, ``lo``) on the stack.

    A CALL, unlike review M6's inline negation at 32 and 64 bits: the limb
    negation plus its ``MIN`` check does not fit under S25's break-even, and
    the frontend's ratified inventory names ``{u,i}128_neg`` for exactly this.
    """
    prefix = _wide_prefix(ty, "checked negation")
    return call_wide_part(fn, ctx, f"{prefix}_neg", 2)


def wide_cmp(fn: Fn, ctx: EmitCtx, ty: Ty) -> None:
    """Lower a 128-bit compare; four RAW limbs on the stack, raw -1/0/1 out.

    Single-result, so no scratch is involved: the caller compares the answer
    against zero with whichever relop the source operator asked for.
    """
    prefix = _wide_prefix(ty, "comparison")
    fn.call_defined(ctx.ensure_part(f"{prefix}_cmp"), 4, ("i64",))


def symsmall_cmp(fn: Fn, ctx: EmitCtx) -> None:
    """Order two SMALL `Symbol`s; two `Val` WORDS on the stack, raw -1/0/1 out.

    The small/small arm of `lower._lower_symbol_compare` (review B1). Whole
    words, not bodies: the part shifts the tag byte off itself, so the caller
    hands over exactly what it would have handed `obj_cmp`.
    """
    fn.call_defined(ctx.ensure_part("symsmall_cmp"), 2, ("i64",))


def unbox_wide(fn: Fn, ctx: EmitCtx, ty: Ty) -> tuple[int, int]:
    """128-bit ``Val`` word on the stack -> the ``(hi, lo)`` locals holding it."""
    prefix = _wide_prefix(ty, "unboxing")
    return call_wide_part(fn, ctx, f"unbox_{prefix}", 1)


def rebox_wide(fn: Fn, ctx: EmitCtx, ty: Ty) -> None:
    """Two RAW limbs (``hi``, ``lo``) on the stack -> one 128-bit ``Val`` word."""
    prefix = _wide_prefix(ty, "boxing")
    fn.call_defined(ctx.ensure_part(f"box_{prefix}"), 2, ("i64",))
