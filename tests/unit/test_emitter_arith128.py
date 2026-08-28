"""Tests for `serpent.emitter.arith`'s 128-bit half -- limbs and the i256 route.

**Executed** goldens, like the 64-bit file: every vector is assembled into a
module by `tests/harness/testmod` and RUN on the pinned wasmtime, against a
big-integer oracle (`tests/harness/i256`) written independently of the limb
code it checks. This is the module where a wrong byte does not crash -- it
validates, deploys, and returns a plausible wrong number -- so nothing here is
a byte snapshot.

What is being protected, in order of how badly it would bite:

* **A4, verbatim and NOT Python's.** `//` truncates toward zero and `%` takes
  the DIVIDEND's sign, so the literal expectations are `-7 // 2 == -3` and
  `-7 % 2 == -1` (Python says `-4` and `+1`). The oracle is
  `a4_floordiv`/`a4_mod`, imported from the 64-bit file rather than
  re-derived, and `MIN % -1 == 0` gets its own vector.
* **F.1.2, the dossier's "single most consequential arithmetic finding".**
  `{i,u}256_rem_euclid` is EUCLIDEAN and would answer `+1` for `-7 % 2`. It is
  never bound in the harness, so a lowering that reached for one could not
  link -- and `test_the_route_never_names_a_rem_euclid_binding` says so out
  loud.
* **Review B4.** `{i,u}256_div` returns a SMALL-form Val for almost every real
  quotient; the four `obj_to_*` accessors take an object and nothing else. The
  harness's accessors refuse a small form exactly as the host does, so
  `U128(100) // U128(5)` -- the review's headline case -- fails loudly if the
  tag branch is ever dropped, and a `> 2**56` quotient covers the other arm.
* **Review B9.** The unsigned "did a bit escape the window" criterion
  false-positives on signed operands: `I128(-1) * I128(-1)` has all-ones limbs
  on both sides and a nonzero high half. `(-1)*(-1) == 1`, `MIN * -1` →
  overflow, `MIN * 1 == MIN` and `MIN * 0 == 0` are the mandatory vectors.
* **F.1.12.** A negative I128 widens into i256 as TWO ALL-ONES words. Zero-fill
  them and every negative dividend becomes a colossal positive that divides
  perfectly and returns nonsense; `test_i256_sign_extension_widens_negatives`
  pins the widening on its own, without a division in the way.
* **Ruling E16.** `i128_cmp` compares the HIGH limb signed and the LOW limb
  unsigned. An unsigned hi compare inverts every straddling pair; a signed lo
  compare inverts every pair whose low limbs straddle `2**63`.
* **Review m7.** A small `I128` body sign-extends into the high limb, and the
  runtime `fits_small` test on a PAIR is a two-limb test.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Sequence

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from serpent import val
from serpent.compiler.ir import BinaryOp
from serpent.compiler.types_ import Ty
from serpent.emitter import arith, encode, frame, opcodes
from serpent.emitter.arith import EmitCtx
from serpent.emitter.layout import Memory
from tests.harness import engine, i256, testmod
from tests.unit.test_emitter_arith64 import (
    OVERFLOW,
    OVERFLOW_VAL,
    a4_apply,
    a4_floordiv,
    a4_mod,
)

# --- the values every vector is written against -------------------------------

U64_MAX = (1 << 64) - 1
U128_MAX = (1 << 128) - 1
I128_MIN = -(1 << 127)
I128_MAX = (1 << 127) - 1

#: The small-form boundary review m7 and B4 both live on.
SMALL = val.MAX_SMALL_U64  # 2**56 - 1


def limbs(value: int) -> tuple[int, int]:
    """A 128-bit value as its two RAW limbs, `(hi, lo)`.

    Two's complement for a negative one: `-1` is `(2**64 - 1, 2**64 - 1)`, not
    `(-1, -1)` in any other sense -- the guest sees words.
    """
    word = value & U128_MAX
    return word >> 64, word & U64_MAX


def from_limbs(hi: int, lo: int, *, signed: bool) -> int:
    """The inverse of `limbs`, so a run's answer can be read as a number."""
    word = ((hi & U64_MAX) << 64) | (lo & U64_MAX)
    if signed and word >= 1 << 127:
        return word - (1 << 128)
    return word


# --- building and running a 128-bit probe module ------------------------------
#
# One module holding EVERY 128-bit part, built once. Two reasons: the scratch
# slots then have fixed addresses the tests can read back, and building the
# whole family together proves the parts coexist in one index space and that
# every import they name is bindable.

_Spec = testmod.FunctionSpec

#: The `peek` probe's own parameter is a scratch ADDRESS, not a `Val`.
_PEEK = "peek"

#: Every 128-bit part, plus the 64x64->128 helper they share. Derived from the
#: registry rather than listed, so a part added later is exercised here without
#: anyone remembering to add it -- and a part RENAMED out of the family shows up
#: as a missing export rather than as a silently skipped one.
_WIDE_PARTS = tuple(
    sorted(name for name in arith.PART_BUILDERS if "128" in name or name == "mul64_wide")
)


def _peek_fn() -> frame.Fn:
    """`peek(addr) -> i64.load(addr)` -- how a test reads a part's `lo` slot.

    Written with raw `op()` calls because `frame.Fn` has no memory helpers;
    the pushes and pops are done by hand so the operand-stack tracker still
    sees them.
    """
    fn = frame.Fn(_PEEK, 1, 0, ("i64",))
    fn.local_get(0)
    fn.pop("i64")
    fn.op(opcodes.I32_WRAP_I64)
    fn.push("i32")
    fn.pop("i32")
    fn.op(opcodes.I64_LOAD, encode.uleb(3), encode.uleb(0))
    fn.push("i64")
    fn.ret()
    return fn


@functools.cache
def _wide_module() -> tuple[bytes, tuple[str, ...], dict[str, int]]:
    """The all-parts module, its import order, and each part's `lo` slot."""
    ctx = EmitCtx(n_module_functions=1, memory=Memory())
    for name in _WIDE_PARTS:
        ctx.ensure_part(name)
    peek = _peek_fn()
    head: list[_Spec] = [(_PEEK, 1, peek.nlocals, ("i64",), peek.finish())]
    specs = [*head, *((p.name, p.nparams, p.nlocals, p.results, p.body) for p in ctx.parts)]
    slots = {name: ctx.lo_slot(name) for name in arith.PARTS_NEEDING_MEMORY}
    wasm = testmod.build_test_module(specs, imports=ctx.import_order, memory_pages=1)
    return wasm, ctx.import_order, slots


def _host() -> tuple[engine.MiniHost, i256.Wide256Host]:
    """A fresh host over the all-parts module, plus its big-integer oracle.

    Fresh per call: an overflowing vector aborts through a Python exception
    raised inside a host callback, and an unwound `Store` is not something
    these tests should be reasoning about.
    """
    wasm, _imports, _slots = _wide_module()
    wide = i256.Wide256Host()
    return engine.MiniHost(wasm, imports=wide.bindings()), wide


def _slot(name: str) -> int:
    _wasm, _imports, slots = _wide_module()
    return slots[name]


def _invoke_wide(host: engine.MiniHost, name: str, args: Sequence[int]) -> tuple[int, int]:
    """Call a two-result part and read BOTH limbs: `hi` returned, `lo` from scratch."""
    hi = host.invoke(name, *(val.as_u64(a) for a in args))
    assert hi is not None
    lo = host.invoke(_PEEK, _slot(name))
    assert lo is not None
    return hi, lo


def _check_wide(name: str, args: Sequence[int], want: object, *, signed: bool) -> None:
    """Assert a two-result part's answer, or that it aborted with ArithmeticOverflow."""
    host, _wide = _host()
    if want is OVERFLOW:
        with pytest.raises(engine.HostError) as caught:
            _invoke_wide(host, name, args)
        assert caught.value.val == OVERFLOW_VAL
        return
    assert isinstance(want, int)
    hi, lo = _invoke_wide(host, name, args)
    assert from_limbs(hi, lo, signed=signed) == want


def _check_binary(prefix: str, op: BinaryOp, a: int, b: int, want: object) -> None:
    a_hi, a_lo = limbs(a)
    b_hi, b_lo = limbs(b)
    _check_wide(
        f"{prefix}_{op.name.lower()}", (a_hi, a_lo, b_hi, b_lo), want, signed=prefix == "i128"
    )


def _check_cmp(prefix: str, a: int, b: int, want: int) -> None:
    host, _wide = _host()
    a_hi, a_lo = limbs(a)
    b_hi, b_lo = limbs(b)
    got = host.invoke(f"{prefix}_cmp", a_hi, a_lo, b_hi, b_lo)
    assert got is not None
    assert val.as_i64(got) == want


# ===========================================================================
# The two-result convention itself (review m4, B8)
# ===========================================================================


def test_every_two_result_part_reserves_its_own_scratch_slot() -> None:
    # One slot PER PART, never shared: two parts writing the same address would
    # be silent, and a nested call (`u128_mul` -> `mul64_wide`) would clobber
    # the outer part's `lo` between its store and its caller's load.
    _wasm, _imports, slots = _wide_module()
    assert set(slots) == set(arith.PARTS_NEEDING_MEMORY)
    assert len(set(slots.values())) == len(slots)
    assert all(addr >= Memory.SCRATCH_BASE for addr in slots.values())
    assert all(addr % 8 == 0 for addr in slots.values())


def test_parts_needing_memory_matches_what_the_parts_actually_reserve() -> None:
    # The static marker Task 10 reads and the dynamic fact must not drift: a
    # part that grew a scratch slot without joining the frozenset would make
    # `needs_memory` right and `PARTS_NEEDING_MEMORY` a lie.
    ctx = EmitCtx(n_module_functions=0, memory=Memory())
    assert not ctx.needs_memory
    for name in sorted(arith.PART_BUILDERS):
        ctx.ensure_part(name)
    assert ctx.needs_memory
    reserved = {name for name in arith.PART_BUILDERS if name in arith.PARTS_NEEDING_MEMORY}
    assert reserved == set(arith.PARTS_NEEDING_MEMORY)


def test_linking_a_wide_part_forces_memory_even_with_no_literals() -> None:
    # Review B8's exact case: a contract that only multiplies U128s has an
    # empty pool and no linear-memory host call, so `compiled.needs_memory` is
    # False -- and D still needs a page.
    memory = Memory()
    ctx = EmitCtx(n_module_functions=1, memory=memory)
    assert memory.is_empty
    ctx.ensure_part("u128_mul")
    assert ctx.needs_memory
    assert not memory.is_empty


def test_a_single_result_part_reserves_nothing() -> None:
    for name in ("u128_cmp", "i128_cmp", "box_u128", "box_i128"):
        ctx = EmitCtx(n_module_functions=0, memory=Memory())
        ctx.ensure_part(name)
        assert not ctx.needs_memory, name


def test_the_wide_parts_nest_and_keep_their_promised_indices() -> None:
    # `u128_mod` links `u128_floordiv`, which is the re-entrancy the 64-bit
    # file exercises synthetically. A wrong defidx here calls a function of the
    # same type and still validates, so this asserts an ANSWER.
    ctx = EmitCtx(n_module_functions=4, memory=Memory())
    assert ctx.ensure_part("u128_mod") == 4
    linked = [p.name for p in ctx.parts]
    assert linked[0] == "u128_mod"
    assert {"u128_floordiv", "mul64_wide"} <= set(linked)
    _check_binary("u128", BinaryOp.MOD, 17, 5, 2)


def test_call_wide_part_loads_lo_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    # Invariant (2) of the two-result convention: the `i64.load` is emitted by
    # `call_wide_part` itself, before anything the caller might interleave. If
    # it ever moved, `(a*b) + (c*d)` would read the second product's `lo` for
    # the first, which validates and returns a plausible wrong number.
    ctx = EmitCtx(n_module_functions=1, memory=Memory())
    fn = frame.Fn("probe", 4, 0, ("i64",))
    for i in range(4):
        fn.local_get(i)
    arith.call_wide_part(fn, ctx, "u128_add", 4)
    fn.i64_const(0)
    fn.ret()
    body = b"".join(item for item in fn.finish() if isinstance(item, bytes))
    # The call site is symbolic, so the bytes AFTER it start with the `lo`
    # bookkeeping: local.set (the returned hi), then i32.const/i64.load.
    tail = body[body.index(bytes([opcodes.LOCAL_SET])) :]
    assert tail[0] == opcodes.LOCAL_SET
    assert opcodes.I64_LOAD in tail[:8]


# ===========================================================================
# Addition and subtraction: carry, borrow, and the sign analysis
# ===========================================================================

U128_ADD_SUB: list[tuple[BinaryOp, int, int, object]] = [
    (BinaryOp.ADD, 0, 0, 0),
    (BinaryOp.ADD, 1, 2, 3),
    # The carry out of the LOW limb: drop it and this answers 0.
    (BinaryOp.ADD, U64_MAX, 1, 1 << 64),
    (BinaryOp.ADD, U64_MAX, U64_MAX, (1 << 65) - 2),
    (BinaryOp.ADD, (1 << 64) - 1, (1 << 64) - 1, (1 << 65) - 2),
    (BinaryOp.ADD, U128_MAX, 0, U128_MAX),
    (BinaryOp.ADD, U128_MAX, 1, OVERFLOW),
    (BinaryOp.ADD, 1, U128_MAX, OVERFLOW),
    (BinaryOp.ADD, 1 << 127, 1 << 127, OVERFLOW),
    (BinaryOp.ADD, U128_MAX, U128_MAX, OVERFLOW),
    # A carry into a hi limb that is already 2**64 - 1: `hi == a_hi` and the
    # sum still escaped, which a naive `hi <u a_hi` test alone would miss.
    (BinaryOp.ADD, (U64_MAX << 64) | U64_MAX, 1, OVERFLOW),
    (BinaryOp.ADD, SMALL, 1, SMALL + 1),
    (BinaryOp.SUB, 0, 0, 0),
    (BinaryOp.SUB, 5, 3, 2),
    (BinaryOp.SUB, 3, 5, OVERFLOW),
    (BinaryOp.SUB, 1 << 64, 1, U64_MAX),
    (BinaryOp.SUB, U128_MAX, U128_MAX, 0),
    (BinaryOp.SUB, U128_MAX, 0, U128_MAX),
    (BinaryOp.SUB, 1 << 64, (1 << 64) + 1, OVERFLOW),
    # Equal hi limbs, so the LOW limbs decide -- and unsigned, always.
    (BinaryOp.SUB, (1 << 64) | 1, (1 << 64) | 2, OVERFLOW),
    (BinaryOp.SUB, (1 << 64) | (1 << 63), (1 << 64) | 1, (1 << 63) - 1),
]

I128_ADD_SUB: list[tuple[BinaryOp, int, int, object]] = [
    (BinaryOp.ADD, 0, 0, 0),
    (BinaryOp.ADD, 1, -1, 0),
    (BinaryOp.ADD, -1, -1, -2),
    (BinaryOp.ADD, I128_MAX, 0, I128_MAX),
    (BinaryOp.ADD, I128_MIN, 0, I128_MIN),
    (BinaryOp.ADD, I128_MAX, I128_MIN, -1),
    (BinaryOp.ADD, I128_MAX, 1, OVERFLOW),
    (BinaryOp.ADD, I128_MIN, -1, OVERFLOW),
    (BinaryOp.ADD, -1, I128_MIN, OVERFLOW),
    # The carry crossing the limb boundary in the negative direction.
    (BinaryOp.ADD, -(1 << 64), -1, -(1 << 64) - 1),
    (BinaryOp.ADD, (1 << 64) - 1, 1, 1 << 64),
    (BinaryOp.SUB, 0, 0, 0),
    (BinaryOp.SUB, 3, 5, -2),
    (BinaryOp.SUB, I128_MIN, I128_MIN, 0),
    (BinaryOp.SUB, I128_MIN, -1, I128_MIN + 1),
    (BinaryOp.SUB, -1, I128_MAX, I128_MIN),
    (BinaryOp.SUB, 0, I128_MIN, OVERFLOW),
    (BinaryOp.SUB, I128_MIN, 1, OVERFLOW),
    (BinaryOp.SUB, I128_MAX, -1, OVERFLOW),
    (BinaryOp.SUB, 0, 1 << 64, -(1 << 64)),
]


@pytest.mark.parametrize(("op", "a", "b", "want"), U128_ADD_SUB)
def test_u128_add_and_sub(op: BinaryOp, a: int, b: int, want: object) -> None:
    _check_binary("u128", op, a, b, want)


@pytest.mark.parametrize(("op", "a", "b", "want"), I128_ADD_SUB)
def test_i128_add_and_sub(op: BinaryOp, a: int, b: int, want: object) -> None:
    _check_binary("i128", op, a, b, want)


# ===========================================================================
# Multiplication: four 32x32 partials, and review B9's signed criterion
# ===========================================================================


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (0, 0),
        (0, U64_MAX),
        (1, U64_MAX),
        (U64_MAX, U64_MAX),
        (1 << 32, 1 << 32),
        ((1 << 32) - 1, (1 << 32) - 1),
        (1 << 63, 2),
        (0xDEADBEEFCAFEBABE, 0x0123456789ABCDEF),
        (1 << 31, 1 << 33),
        (U64_MAX, 2),
    ],
)
def test_mul64_wide_is_the_full_128_bit_product(a: int, b: int) -> None:
    # S13: four 32x32->64 partials, because `i64.mul_wide_s` is BANNED (the
    # chain's wasmi 0.31 has no wide-arithmetic proposal). A dropped carry in
    # the middle column is invisible for small operands, so the vectors here
    # are deliberately full-width.
    host, _wide = _host()
    hi, lo = _invoke_wide(host, "mul64_wide", (a, b))
    assert (hi << 64) | lo == a * b


U128_MUL: list[tuple[int, int, object]] = [
    (0, 0, 0),
    (0, U128_MAX, 0),
    (1, U128_MAX, U128_MAX),
    (U128_MAX, 1, U128_MAX),
    (3, 5, 15),
    (U64_MAX, U64_MAX, U64_MAX * U64_MAX),
    (1 << 63, 2, 1 << 64),
    (1 << 64, 1 << 63, 1 << 127),
    (1 << 64, 1 << 64, OVERFLOW),
    (U128_MAX, 2, OVERFLOW),
    (U128_MAX, U128_MAX, OVERFLOW),
    (1 << 127, 2, OVERFLOW),
    # A carry out of the hi limb's own accumulation, with both operands inside
    # the window: the escape is in the ADDITION, not in a partial product.
    ((1 << 127) | 1, 2, OVERFLOW),
    (SMALL, SMALL, SMALL * SMALL),
]

I128_MUL: list[tuple[int, int, object]] = [
    # Review B9's four mandatory vectors.
    (-1, -1, 1),
    (I128_MIN, -1, OVERFLOW),
    (I128_MIN, 1, I128_MIN),
    (I128_MIN, 0, 0),
    # ... and the rest of the sign matrix.
    (0, 0, 0),
    (0, I128_MIN, 0),
    (1, I128_MIN, I128_MIN),
    (-1, I128_MIN, OVERFLOW),
    (I128_MIN, 2, OVERFLOW),
    (I128_MAX, 1, I128_MAX),
    (I128_MAX, -1, -I128_MAX),
    (I128_MAX, 2, OVERFLOW),
    (-3, 4, -12),
    (3, -4, -12),
    (-3, -4, 12),
    (-(1 << 64), 1 << 62, -(1 << 126)),
    (-(1 << 64), 1 << 63, I128_MIN),
    (-(1 << 64), 1 << 64, OVERFLOW),
    # Exactly representable only because the result is NEGATIVE: the bound a
    # signed multiply checks against depends on the sign of its own result.
    (1 << 126, -2, I128_MIN),
    (1 << 126, 2, OVERFLOW),
    (val.MIN_SMALL_I64, -1, -val.MIN_SMALL_I64),
]


@pytest.mark.parametrize(("a", "b", "want"), U128_MUL)
def test_u128_mul(a: int, b: int, want: object) -> None:
    _check_binary("u128", BinaryOp.MUL, a, b, want)


@pytest.mark.parametrize(("a", "b", "want"), I128_MUL)
def test_i128_mul(a: int, b: int, want: object) -> None:
    _check_binary("i128", BinaryOp.MUL, a, b, want)


def test_the_unsigned_mul_criterion_would_false_positive_on_signed_limbs() -> None:
    # Review B9 in one assertion: `I128(-1)` is all-ones in BOTH limbs, and the
    # unsigned 256-bit product of two such values has a nonzero high half. A
    # signed multiply that reused `u128_mul`'s criterion would call `1` an
    # overflow; a signed multiply that reused `u128_mul` outright would return
    # the same `1` by accident, which is why the vector below is `MIN * -1`.
    hi, lo = limbs(-1)
    assert (hi, lo) == (U64_MAX, U64_MAX)
    assert ((((hi << 64) | lo) ** 2) >> 128) != 0
    _check_binary("i128", BinaryOp.MUL, -1, -1, 1)
    _check_binary("u128", BinaryOp.MUL, (1 << 128) - 1, (1 << 128) - 1, OVERFLOW)


# ===========================================================================
# Negation
# ===========================================================================


@pytest.mark.parametrize(
    ("prefix", "value", "want"),
    [
        # Unsigned: there is no negative U128, so anything nonzero overflows.
        ("u128", 0, 0),
        ("u128", 1, OVERFLOW),
        ("u128", 1 << 64, OVERFLOW),
        ("u128", U128_MAX, OVERFLOW),
        # Signed: MIN has no positive twin; everything else negates limb-wise.
        ("i128", 0, 0),
        ("i128", 1, -1),
        ("i128", -1, 1),
        ("i128", 1 << 64, -(1 << 64)),
        ("i128", -(1 << 64), 1 << 64),
        ("i128", I128_MAX, -I128_MAX),
        ("i128", I128_MIN + 1, I128_MAX),
        ("i128", I128_MIN, OVERFLOW),
        # The borrow out of the low limb: `-(2**64)` has a zero low limb and
        # `-(2**64 + 1)` does not, and only one of them borrows.
        ("i128", (1 << 64) + 1, -((1 << 64) + 1)),
    ],
)
def test_wide_neg(prefix: str, value: int, want: object) -> None:
    hi, lo = limbs(value)
    _check_wide(f"{prefix}_neg", (hi, lo), want, signed=prefix == "i128")


# ===========================================================================
# Comparison (ruling E16): hi signed for i128, lo ALWAYS unsigned
# ===========================================================================

CMP_PAIRS: list[tuple[str, int, int, int]] = [
    ("u128", 0, 0, 0),
    ("u128", 1, 0, 1),
    ("u128", 0, 1, -1),
    ("u128", U128_MAX, U128_MAX, 0),
    ("u128", U128_MAX, 0, 1),
    # Equal hi limbs: the LOW limbs decide, and unsigned. Comparing them
    # signed would call `2**63` less than `1`.
    ("u128", 1 << 63, 1, 1),
    ("u128", 1, 1 << 63, -1),
    # Hi limbs straddling 2**63: unsigned at U128, so the bigger word wins.
    ("u128", 1 << 127, 1 << 126, 1),
    ("u128", 1 << 126, 1 << 127, -1),
    ("i128", 0, 0, 0),
    ("i128", 1, 0, 1),
    ("i128", -1, 0, -1),
    ("i128", 0, -1, 1),
    ("i128", -1, -2, 1),
    ("i128", I128_MIN, I128_MAX, -1),
    ("i128", I128_MAX, I128_MIN, 1),
    ("i128", I128_MIN, I128_MIN, 0),
    # The vector an UNSIGNED hi compare gets backwards: as words, `I128_MIN`'s
    # hi limb is 2**63 and `0`'s is 0, so unsigned says MIN is the larger.
    ("i128", I128_MIN, 0, -1),
    ("i128", -1, 1, -1),
    # Equal (negative) hi limbs, low limbs straddling 2**63: still unsigned.
    ("i128", -1, -(1 << 63) - 1, 1),
]


@pytest.mark.parametrize(("prefix", "a", "b", "want"), CMP_PAIRS)
def test_wide_cmp_returns_exactly_minus_one_zero_or_one(
    prefix: str, a: int, b: int, want: int
) -> None:
    _check_cmp(prefix, a, b, want)


@given(
    a=st.integers(min_value=I128_MIN, max_value=I128_MAX),
    b=st.integers(min_value=I128_MIN, max_value=I128_MAX),
)
@settings(max_examples=40, deadline=None)
def test_i128_cmp_matches_python_ordering(a: int, b: int) -> None:
    _check_cmp("i128", a, b, (a > b) - (a < b))


@given(
    a=st.integers(min_value=0, max_value=U128_MAX),
    b=st.integers(min_value=0, max_value=U128_MAX),
)
@settings(max_examples=40, deadline=None)
def test_u128_cmp_matches_python_ordering(a: int, b: int) -> None:
    _check_cmp("u128", a, b, (a > b) - (a < b))


# ===========================================================================
# The i256 division route (S13, F.1.2, F.1.12, review B4)
# ===========================================================================


@pytest.mark.parametrize(
    "value",
    [0, 1, -1, I128_MIN, I128_MAX, -(1 << 64), (1 << 64) - 1, -1234567890123456789],
)
def test_i256_sign_extension_widens_negatives_with_all_ones_words(value: int) -> None:
    # F.1.12, unit-tested with no division in the way: a negative i128 widens
    # into i256 as TWO ALL-ONES words. Zero-filling them instead turns every
    # negative dividend into a colossal positive that divides perfectly well
    # and returns a plausible wrong number.
    hi, lo = limbs(value)
    extension = U64_MAX if value < 0 else 0
    wide = i256.Wide256Host()
    handle = wide.obj_from_i256_pieces(extension, extension, hi, lo)
    assert wide.read_i256(handle) == value
    # And the guest's own widening agrees, observed through a division by 1.
    _check_binary("i128", BinaryOp.FLOORDIV, value, 1, value)


def test_u128_widens_with_zero_words() -> None:
    for value in (0, 1, U64_MAX, 1 << 64, U128_MAX):
        wide = i256.Wide256Host()
        hi, lo = limbs(value)
        assert wide.read_u256(wide.obj_from_u256_pieces(0, 0, hi, lo)) == value
        _check_binary("u128", BinaryOp.FLOORDIV, value, 1, value)


def test_the_small_form_quotient_is_review_b4s_headline_case() -> None:
    # `U128(100) // U128(5)` -> `20`, which the host returns SMALL-tagged. The
    # four `obj_to_u256_*` accessors take an object and nothing else, so a
    # missing tag branch aborts here instead of returning 20.
    wide = i256.Wide256Host()
    quotient = wide.u256_div(wide.u256_val(100), wide.u256_val(5))
    assert val.tag_of(quotient) == val.TAG_U256_SMALL
    _check_binary("u128", BinaryOp.FLOORDIV, 100, 5, 20)


def test_the_object_form_quotient_exercises_the_other_arm() -> None:
    # A quotient past the 56-bit small body, so the host hands back an object
    # and the four accessors really do run.
    dividend = (1 << 100) + 7
    wide = i256.Wide256Host()
    quotient = wide.u256_div(wide.u256_val(dividend), wide.u256_val(2))
    assert val.tag_of(quotient) == val.TAG_U256_OBJECT
    assert dividend // 2 > (1 << 56)
    _check_binary("u128", BinaryOp.FLOORDIV, dividend, 2, dividend // 2)
    _check_binary("i128", BinaryOp.FLOORDIV, -dividend, 2, -(dividend // 2))


def test_the_route_never_names_a_rem_euclid_binding() -> None:
    # F.1.2. `{i,u}256_rem_euclid`'s modulo is EUCLIDEAN and always
    # non-negative; A4's `%` follows the dividend. Neither is bound in the
    # harness, so a lowering that reached for one could not even link -- but
    # the import list is asserted directly so the reason is written down.
    _wasm, imports, _slots = _wide_module()
    assert "i256_rem_euclid" not in imports
    assert "u256_rem_euclid" not in imports
    assert "i256_div" in imports
    assert "u256_div" in imports
    wide = i256.Wide256Host()
    assert "i256_rem_euclid" not in wide.bindings()
    assert "u256_rem_euclid" not in wide.bindings()


#: A4's literal expectations, spelled out. Python would say `-4` and `+1`.
A4_LITERALS: list[tuple[BinaryOp, int, int, int]] = [
    (BinaryOp.FLOORDIV, -7, 2, -3),
    (BinaryOp.MOD, -7, 2, -1),
    (BinaryOp.FLOORDIV, 7, -2, -3),
    (BinaryOp.MOD, 7, -2, 1),
    (BinaryOp.FLOORDIV, -7, -2, 3),
    (BinaryOp.MOD, -7, -2, -1),
    (BinaryOp.FLOORDIV, 7, 2, 3),
    (BinaryOp.MOD, 7, 2, 1),
]


@pytest.mark.parametrize(("op", "a", "b", "want"), A4_LITERALS)
def test_a4s_literal_expectations_at_128_bits(op: BinaryOp, a: int, b: int, want: int) -> None:
    # Written as literals on purpose (A4): truncation toward zero and a
    # remainder that follows the DIVIDEND. If these ever read like Python's
    # floor semantics, the contract has changed, not the test.
    assert a4_apply(op, a, b) == want
    _check_binary("i128", op, a, b, want)


U128_DIV: list[tuple[BinaryOp, int, int, object]] = [
    (BinaryOp.FLOORDIV, 100, 5, 20),
    (BinaryOp.FLOORDIV, 7, 2, 3),
    (BinaryOp.FLOORDIV, 0, 5, 0),
    (BinaryOp.FLOORDIV, U128_MAX, 1, U128_MAX),
    (BinaryOp.FLOORDIV, U128_MAX, U128_MAX, 1),
    (BinaryOp.FLOORDIV, U128_MAX, 2, U128_MAX // 2),
    (BinaryOp.FLOORDIV, 1 << 127, 2, 1 << 126),
    (BinaryOp.FLOORDIV, 1 << 100, 1 << 40, 1 << 60),
    (BinaryOp.FLOORDIV, 5, 7, 0),
    (BinaryOp.MOD, 100, 5, 0),
    (BinaryOp.MOD, 7, 2, 1),
    (BinaryOp.MOD, 5, 7, 5),
    (BinaryOp.MOD, U128_MAX, 2, 1),
    (BinaryOp.MOD, U128_MAX, 1 << 64, (1 << 64) - 1),
    (BinaryOp.MOD, (1 << 100) + 7, 1 << 64, ((1 << 100) + 7) % (1 << 64)),
]

I128_DIV: list[tuple[BinaryOp, int, int, object]] = [
    (BinaryOp.FLOORDIV, 0, 5, 0),
    (BinaryOp.FLOORDIV, I128_MIN, 1, I128_MIN),
    (BinaryOp.FLOORDIV, I128_MIN, 2, -(1 << 126)),
    (BinaryOp.FLOORDIV, I128_MAX, -1, -I128_MAX),
    (BinaryOp.FLOORDIV, I128_MIN, -2, 1 << 126),
    (BinaryOp.FLOORDIV, -(1 << 100), 1 << 40, -(1 << 60)),
    # A4: MIN // -1 is ArithmeticOverflow, decided BEFORE the host sees it.
    (BinaryOp.FLOORDIV, I128_MIN, -1, OVERFLOW),
    (BinaryOp.MOD, I128_MIN, 1, 0),
    (BinaryOp.MOD, I128_MAX, -2, 1),
    (BinaryOp.MOD, I128_MIN, 2, 0),
    (BinaryOp.MOD, -(1 << 100), 3, a4_mod(-(1 << 100), 3)),
    # A4: MIN % -1 is 0, and it has to be answered before the quotient part
    # (correctly) calls MIN // -1 an overflow.
    (BinaryOp.MOD, I128_MIN, -1, 0),
]


@pytest.mark.parametrize(("op", "a", "b", "want"), U128_DIV)
def test_u128_division_route(op: BinaryOp, a: int, b: int, want: object) -> None:
    _check_binary("u128", op, a, b, want)


@pytest.mark.parametrize(("op", "a", "b", "want"), I128_DIV)
def test_i128_division_route(op: BinaryOp, a: int, b: int, want: object) -> None:
    _check_binary("i128", op, a, b, want)


def test_min_over_minus_one_is_answered_before_the_host_is_asked() -> None:
    # A4 makes `MIN // -1` an ArithmeticOverflow and `MIN % -1` a zero, and
    # the brief requires both to be settled BEFORE the division route runs.
    #
    # No assertion on the returned VALUE can pin that: sent to the host,
    # `MIN // -1` comes back as `2**127`, whose top limbs are not the sign
    # extension of `lo_hi`, so `_check_extension` produces the same
    # ArithmeticOverflow one step later. The two routes differ only in whether
    # the host was consulted at all -- so that is what is asserted, via the
    # harness's call log.
    host, wide = _host()
    with pytest.raises(engine.HostError) as caught:
        host.invoke("i128_floordiv", *limbs(I128_MIN), *limbs(-1))
    assert caught.value.val == OVERFLOW_VAL
    assert wide.calls == []

    host, wide = _host()
    hi, lo = _invoke_wide(host, "i128_mod", (*limbs(I128_MIN), *limbs(-1)))
    assert from_limbs(hi, lo, signed=True) == 0
    assert wide.calls == []


def test_a_normal_division_does_consult_the_host() -> None:
    # The control for the test above: without it, a route that never called
    # the host at all would satisfy both of its assertions.
    host, wide = _host()
    _invoke_wide(host, "i128_floordiv", (*limbs(-7), *limbs(2)))
    assert "i256_div" in wide.calls
    assert wide.calls.count("obj_from_i256_pieces") == 2


@pytest.mark.parametrize("part", ["u128_floordiv", "u128_mod", "i128_floordiv", "i128_mod"])
def test_division_by_zero_aborts_through_the_host(part: str) -> None:
    # A DIVERGENCE worth naming: at 32 and 64 bits A4 leaves `//0` to wasm's
    # own trap, but there is no 128-bit division instruction to trap -- the
    # zero divisor reaches `{i,u}256_div`, which answers `ScError`. The abort
    # class is therefore the host's, not wasm's, and it is pinned here rather
    # than papered over in the lowering.
    host, _wide = _host()
    with pytest.raises(engine.HostError) as caught:
        host.invoke(part, 0, 1, 0, 0)
    assert caught.value.val == i256.DIV_ERROR_VAL


# ===========================================================================
# Boxing and unboxing at 128 bits (S14, review m7, B10)
# ===========================================================================


@pytest.mark.parametrize(
    "value",
    [0, 1, 255, 1 << 40, SMALL - 1, SMALL],
)
def test_unbox_u128_small_form(value: int) -> None:
    host, _wide = _host()
    hi, lo = _invoke_wide(host, "unbox_u128", (val.pack_small_u64(value, val.TAG_U128_SMALL),))
    assert from_limbs(hi, lo, signed=False) == value


@pytest.mark.parametrize("value", [0, 1, SMALL + 1, 1 << 64, U128_MAX])
def test_unbox_u128_object_form(value: int) -> None:
    host, wide = _host()
    hi, lo = limbs(value)
    handle = wide.obj_from_u128_pieces(hi, lo)
    got_hi, got_lo = _invoke_wide(host, "unbox_u128", (handle,))
    assert from_limbs(got_hi, got_lo, signed=False) == value


@pytest.mark.parametrize(
    "value",
    [
        # Review m7 + B10: a negative small `I128` body must SIGN-EXTEND into
        # the high limb. Zero-fill it and `I128(-1)` unboxes as `2**64 - 1`.
        -1,
        -(2**50),
        val.MIN_SMALL_I64,
        val.MAX_SMALL_I64,
        0,
        1,
        -2,
        -255,
        val.MIN_SMALL_I64 + 1,
    ],
)
def test_unbox_i128_small_form_sign_extends(value: int) -> None:
    host, _wide = _host()
    hi, lo = _invoke_wide(host, "unbox_i128", (val.pack_small_i64(value, val.TAG_I128_SMALL),))
    assert from_limbs(hi, lo, signed=True) == value
    assert hi == (U64_MAX if value < 0 else 0)


@pytest.mark.parametrize(
    "value", [0, -1, I128_MIN, I128_MAX, val.MIN_SMALL_I64 - 1, val.MAX_SMALL_I64 + 1]
)
def test_unbox_i128_object_form(value: int) -> None:
    host, wide = _host()
    hi, lo = limbs(value)
    handle = wide.obj_from_i128_pieces(hi, lo)
    got_hi, got_lo = _invoke_wide(host, "unbox_i128", (handle,))
    assert from_limbs(got_hi, got_lo, signed=True) == value


@pytest.mark.parametrize("value", [0, 1, SMALL])
def test_box_u128_takes_the_small_form_when_it_fits(value: int) -> None:
    host, _wide = _host()
    hi, lo = limbs(value)
    assert host.invoke("box_u128", hi, lo) == val.pack_small_u64(value, val.TAG_U128_SMALL)


@pytest.mark.parametrize("value", [SMALL + 1, 1 << 64, U128_MAX])
def test_box_u128_takes_the_object_form_when_it_does_not(value: int) -> None:
    host, wide = _host()
    hi, lo = limbs(value)
    handle = host.invoke("box_u128", hi, lo)
    assert handle is not None
    assert val.tag_of(handle) == val.TAG_U128_OBJECT
    assert wide.obj_to_u128_hi64(handle) == hi
    assert wide.obj_to_u128_lo64(handle) == lo


@pytest.mark.parametrize("value", [0, 1, -1, val.MIN_SMALL_I64, val.MAX_SMALL_I64])
def test_box_i128_takes_the_small_form_when_it_fits(value: int) -> None:
    host, _wide = _host()
    hi, lo = limbs(value)
    assert host.invoke("box_i128", hi, lo) == val.pack_small_i64(value, val.TAG_I128_SMALL)


@pytest.mark.parametrize(
    "value",
    [val.MAX_SMALL_I64 + 1, val.MIN_SMALL_I64 - 1, I128_MIN, I128_MAX, 1 << 64, -(1 << 64)],
)
def test_box_i128_takes_the_object_form_when_it_does_not(value: int) -> None:
    host, wide = _host()
    hi, lo = limbs(value)
    handle = host.invoke("box_i128", hi, lo)
    assert handle is not None
    assert val.tag_of(handle) == val.TAG_I128_OBJECT
    assert wide.obj_to_i128_hi64(handle) == hi
    assert wide.obj_to_i128_lo64(handle) == lo


def test_box_i128_needs_both_limbs_in_its_fits_small_test() -> None:
    # Review m7: `fits_small` on a PAIR is `hi == (lo >>s 63)` AND the signed
    # range on `lo`. A low limb that would fit on its own but whose high limb
    # is not its sign extension must NOT box small -- `2**64 + 1` has `lo == 1`.
    host, _wide = _host()
    handle = host.invoke("box_i128", *limbs((1 << 64) + 1))
    assert handle is not None
    assert val.tag_of(handle) == val.TAG_I128_OBJECT
    # And the unsigned twin: `2**64` has `lo == 0`, comfortably inside MASK56.
    other = host.invoke("box_u128", *limbs(1 << 64))
    assert other is not None
    assert val.tag_of(other) == val.TAG_U128_OBJECT


@pytest.mark.parametrize(
    "value",
    [0, 1, SMALL - 1, SMALL, SMALL + 1, 1 << 64, U128_MAX, U64_MAX],
)
def test_u128_box_unbox_round_trips_across_the_small_boundary(value: int) -> None:
    host, _wide = _host()
    boxed = host.invoke("box_u128", *limbs(value))
    assert boxed is not None
    hi, lo = _invoke_wide(host, "unbox_u128", (boxed,))
    assert from_limbs(hi, lo, signed=False) == value


@pytest.mark.parametrize(
    "value",
    [
        0,
        1,
        -1,
        val.MIN_SMALL_I64,
        val.MIN_SMALL_I64 - 1,
        val.MAX_SMALL_I64,
        val.MAX_SMALL_I64 + 1,
        I128_MIN,
        I128_MAX,
        -(1 << 64),
    ],
)
def test_i128_box_unbox_round_trips_across_the_small_boundary(value: int) -> None:
    host, _wide = _host()
    boxed = host.invoke("box_i128", *limbs(value))
    assert boxed is not None
    hi, lo = _invoke_wide(host, "unbox_i128", (boxed,))
    assert from_limbs(hi, lo, signed=True) == value


# ===========================================================================
# The public 128-bit lowering entry points
# ===========================================================================


def test_lower_binary_still_refuses_a_128_bit_type() -> None:
    # A 128-bit value is a limb PAIR, so it cannot travel through the
    # one-word entry point at all; the refusal names where it does go.
    ctx = EmitCtx(n_module_functions=1, memory=Memory())
    fn = frame.Fn("probe", 2, 0, ("i64",))
    fn.local_get(0)
    fn.local_get(1)
    with pytest.raises(frame.EmitError, match="limb PAIR"):
        arith.lower_binary(fn, ctx, Ty.U128, BinaryOp.ADD)


@pytest.mark.parametrize(
    ("call", "ty"),
    [
        (arith.wide_binary, Ty.U64),
        (arith.wide_neg, Ty.I64),
        (arith.wide_cmp, Ty.U32),
        (arith.unbox_wide, Ty.Bool),
        (arith.rebox_wide, Ty.I32),
    ],
)
def test_the_wide_helpers_refuse_a_narrow_type(call: Callable[..., object], ty: Ty) -> None:
    ctx = EmitCtx(n_module_functions=1, memory=Memory())
    fn = frame.Fn("probe", 4, 0, ("i64",))
    with pytest.raises(frame.EmitError, match="not a 128-bit type"):
        if call is arith.wide_binary:
            call(fn, ctx, ty, BinaryOp.ADD)
        else:
            call(fn, ctx, ty)


@pytest.mark.parametrize("ty", [Ty.U128, Ty.I128])
def test_the_wide_helpers_link_the_parts_the_frontend_names(ty: Ty) -> None:
    # Task 13's `runtime_parts_needed <= runtime_parts_linked`, from D's side:
    # every name `_collect_runtime_parts` can produce for a 128-bit node is a
    # name `wide_*` actually links.
    prefix = "u128" if ty is Ty.U128 else "i128"
    for op in BinaryOp:
        ctx = EmitCtx(n_module_functions=1, memory=Memory())
        fn = frame.Fn("probe", 4, 0, ("i64",))
        for i in range(4):
            fn.local_get(i)
        arith.wide_binary(fn, ctx, ty, op)
        assert f"{prefix}_{op.name.lower()}" in ctx.parts_linked
    for helper, wanted in ((arith.wide_neg, f"{prefix}_neg"), (arith.wide_cmp, f"{prefix}_cmp")):
        ctx = EmitCtx(n_module_functions=1, memory=Memory())
        fn = frame.Fn("probe", 4, 0, ("i64",))
        for i in range(4 if helper is arith.wide_cmp else 2):
            fn.local_get(i)
        helper(fn, ctx, ty)
        assert wanted in ctx.parts_linked


def test_the_inventory_covers_every_128_bit_name_the_frontend_can_ask_for() -> None:
    wanted = {f"{prefix}_{op.name.lower()}" for prefix in ("u128", "i128") for op in BinaryOp}
    wanted |= {f"{prefix}_{what}" for prefix in ("u128", "i128") for what in ("neg", "cmp")}
    assert wanted <= set(arith.PART_BUILDERS)


# ===========================================================================
# Property fuzz: every op against Python ints, adjusted to A4's rules
# ===========================================================================

_FUZZ = settings(max_examples=25, deadline=None)


def _fuzz_wide(prefix: str, op: BinaryOp, a: int, b: int, lo: int, hi: int) -> None:
    if op in (BinaryOp.FLOORDIV, BinaryOp.MOD) and b == 0:
        return  # the host's own ScError; covered by its own test
    exact = a4_apply(op, a, b)
    want: object = exact if lo <= exact <= hi else OVERFLOW
    _check_binary(prefix, op, a, b, want)


@given(
    op=st.sampled_from(list(BinaryOp)),
    a=st.integers(min_value=0, max_value=U128_MAX),
    b=st.integers(min_value=0, max_value=U128_MAX),
)
@_FUZZ
def test_u128_matches_python_under_a4(op: BinaryOp, a: int, b: int) -> None:
    _fuzz_wide("u128", op, a, b, 0, U128_MAX)


@given(
    op=st.sampled_from(list(BinaryOp)),
    a=st.integers(min_value=I128_MIN, max_value=I128_MAX),
    b=st.integers(min_value=I128_MIN, max_value=I128_MAX),
)
@_FUZZ
def test_i128_matches_python_under_a4(op: BinaryOp, a: int, b: int) -> None:
    _fuzz_wide("i128", op, a, b, I128_MIN, I128_MAX)


@given(
    op=st.sampled_from([BinaryOp.FLOORDIV, BinaryOp.MOD]),
    a=st.integers(min_value=I128_MIN + 1, max_value=I128_MAX),
    b=st.sampled_from([-1, 1, 2, -2, 3, -3, 7, -7, 1 << 40, -(1 << 40), 1 << 70, -(1 << 70)]),
)
@_FUZZ
def test_i128_division_covers_every_sign_combination(op: BinaryOp, a: int, b: int) -> None:
    # A denser sweep of the sign matrix than the general fuzz reaches: the
    # divisors are small, so the QUOTIENTS are large, and both the small-form
    # and object-form unpack arms get hit for both signs.
    want = a4_floordiv(a, b) if op is BinaryOp.FLOORDIV else a4_mod(a, b)
    _check_binary("i128", op, a, b, want)


@given(value=st.integers(min_value=0, max_value=U128_MAX))
@_FUZZ
def test_u128_box_unbox_round_trips_over_the_full_range(value: int) -> None:
    host, _wide = _host()
    boxed = host.invoke("box_u128", *limbs(value))
    assert boxed is not None
    hi, lo = _invoke_wide(host, "unbox_u128", (boxed,))
    assert from_limbs(hi, lo, signed=False) == value


@given(value=st.integers(min_value=I128_MIN, max_value=I128_MAX))
@_FUZZ
def test_i128_box_unbox_round_trips_over_the_full_range(value: int) -> None:
    host, _wide = _host()
    boxed = host.invoke("box_i128", *limbs(value))
    assert boxed is not None
    hi, lo = _invoke_wide(host, "unbox_i128", (boxed,))
    assert from_limbs(hi, lo, signed=True) == value


@given(
    a=st.integers(min_value=0, max_value=U64_MAX),
    b=st.integers(min_value=0, max_value=U64_MAX),
)
@_FUZZ
def test_mul64_wide_matches_python_over_the_full_range(a: int, b: int) -> None:
    host, _wide = _host()
    hi, lo = _invoke_wide(host, "mul64_wide", (a, b))
    assert (hi << 64) | lo == a * b
