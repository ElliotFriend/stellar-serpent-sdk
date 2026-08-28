"""Tests for `serpent.emitter.lower` -- the scalar half of SS B.3.1.

Two kinds of assertion, chosen per node rather than uniformly:

* **Executed** vectors (`tests/harness`), for anything where a wrong lowering
  produces a *plausible* wrong number: every comparison, every straddle of a
  small-value bound, the short-circuit proofs. A byte snapshot of a subtly
  wrong lowering pins the wrong bytes forever; a run does not.
* **Item-level** assertions on `Fn.finish()`'s symbolic list, for the pooled
  literals and for `ConstRef` memoisation. Review M8: the linear-memory
  harness callbacks do not exist until Task 8 (they would have to read the
  guest's memory), so "exactly one `symbol_new_from_linear_memory` call site
  in the finished body" is checked structurally here and re-proven by
  execution in Task 13.

What is being protected, in order of how badly it would bite:

* **F.1.1, unbox before you compare.** An EITHER-repr value is a small
  immediate below its 56-bit bound and an object handle above it, so comparing
  the packed `Val` WORDS compares tags and handle indices. Every direct
  compare here straddles `MAX_SMALL_U64`/`MIN_SMALL_I64` in BOTH directions and
  is checked against the tier-1 oracle (`serpent.types`), which is the mitigation
  F.1.1 names.
* **F.1.11, signedness from the OPERAND type.** A raw `I64` and a raw `U64`
  word are the same 64 bits; only the source type picks `lt_s` over `lt_u`.
  The matrix below is width x operator x sign boundary.
* **C8, short-circuit.** Proven with a recording `obj_cmp` callback: the
  second operand's host call must NOT happen when the first operand already
  decided the answer.
* **C13**, a `RawScalar` is a bare number, not a `U32Val`.
* **C11/F.1.15**, `Convert` and any unknown node are LOUD, never silent.
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from serpent import val
from serpent._strkey import VERSION_ACCOUNT
from serpent._strkey import encode as strkey_encode
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
    Convert,
    ErrorVal,
    HostCall,
    IfExp,
    InternalCall,
    IRExpr,
    IsZero,
    LocalRef,
    MakeStruct,
    MakeVec,
    ParamRef,
    RawScalar,
    RawScalarKind,
    Unary,
    UnaryOp,
)
from serpent.compiler.types_ import Ty
from serpent.emitter import encode, lower, opcodes
from serpent.emitter.frame import CallImport, CodeItem, EmitError, Fn
from serpent.emitter.layout import Memory
from serpent.emitter.lower import LowerCtx
from serpent.types import I32, I64, I128, U32, U64, U128, Duration, Timepoint
from tests.harness import engine, testmod

LOC = Loc.whole_file("contracts/t.py")

U32_MAX = (1 << 32) - 1
I32_MIN = -(1 << 31)
I32_MAX = (1 << 31) - 1
U64_MAX = (1 << 64) - 1
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1
U128_MAX = (1 << 128) - 1
I128_MIN = -(1 << 127)
I128_MAX = (1 << 127) - 1

#: A structurally valid account strkey, built rather than pasted.
STRKEY = strkey_encode(VERSION_ACCOUNT, bytes(range(32)))


# --- IR construction shorthand -------------------------------------------------


def const(ty: Ty, value: object) -> Const:
    return Const(loc=LOC, ty=ty, py_value=value)


def param(index: int, ty: Ty) -> ParamRef:
    return ParamRef(loc=LOC, ty=ty, index=index, name=f"p{index}")


def compare(op: CompareOp, lhs: IRExpr, rhs: IRExpr, *, via_obj_cmp: bool = False) -> Compare:
    return Compare(loc=LOC, ty=Ty.Bool, op=op, lhs=lhs, rhs=rhs, via_obj_cmp=via_obj_cmp)


#: A Bool-typed expression that COSTS a host call, so its evaluation is
#: observable from a recording callback. Used to prove short-circuiting: a
#: `Symbol` comparison always routes through `obj_cmp` (F.1.2/T5).
def probe_effect() -> Compare:
    return compare(
        CompareOp.EQ,
        const(Ty.Symbol, "a"),
        const(Ty.Symbol, "b"),
        via_obj_cmp=True,
    )


# --- byte-level helpers ---------------------------------------------------------


def i64c(v: int) -> bytes:
    return bytes([opcodes.I64_CONST]) + encode.sleb(val.as_i64(v))


TAIL = bytes([opcodes.RETURN, opcodes.END])


def items(
    e: IRExpr, *, memory: Memory | None = None, consts: Any = None, nparams: int = 4
) -> list[CodeItem]:
    """The finished symbolic body of a one-expression probe function.

    `nparams` defaults to a handful so a `ParamRef` probe has something to
    read; the params cost nothing in the emitted body.
    """
    ctx = LowerCtx(n_module_functions=1, memory=memory or Memory(), consts=consts)
    fn = Fn("probe", nparams, 0, ("i64",))
    lower.lower_expr(fn, ctx, e)
    fn.ret()
    return fn.finish()


def import_calls(body: Sequence[CodeItem]) -> list[str]:
    return [item.name for item in body if isinstance(item, CallImport)]


# --- the host's object bridges, as Python callbacks ------------------------------


class Store:
    """A toy stand-in for the host's object handles (64- and 128-bit).

    Real handles are opaque; all these tests need is that the object arm of
    every box/unbox is actually exercised, and that a test can MINT a handle to
    pass in as an argument -- which is what makes the straddle vectors possible
    at all.
    """

    def __init__(self) -> None:
        self.words: list[tuple[int, int]] = []
        #: Every `obj_cmp` call, as the pair of `Val` words it saw.
        self.cmp_calls: list[tuple[int, int]] = []

    def handle(self, hi: int, lo: int, tag: int) -> int:
        self.words.append((val.as_u64(hi), val.as_u64(lo)))
        return val.from_body_tag(len(self.words) - 1, tag)

    def narrow(self, word: int, tag: int) -> int:
        return self.handle(0, word, tag)

    def lo(self, h: int) -> int:
        return self.words[val.body_of(h)][1]

    def hi(self, h: int) -> int:
        return self.words[val.body_of(h)][0]

    def obj_cmp(self, a: int, b: int) -> int:
        self.cmp_calls.append((a, b))
        return val.as_u64(-1 if a < b else (1 if a > b else 0))

    def bridges(self) -> dict[str, Callable[..., int]]:
        return {
            "obj_from_u64": lambda x: self.narrow(x, val.TAG_U64_OBJECT),
            "obj_to_u64": self.lo,
            "obj_from_i64": lambda x: self.narrow(x, val.TAG_I64_OBJECT),
            "obj_to_i64": self.lo,
            "timepoint_obj_from_u64": lambda x: self.narrow(x, val.TAG_TIMEPOINT_OBJECT),
            "timepoint_obj_to_u64": self.lo,
            "duration_obj_from_u64": lambda x: self.narrow(x, val.TAG_DURATION_OBJECT),
            "duration_obj_to_u64": self.lo,
            "obj_from_u128_pieces": lambda hi, lo: self.handle(hi, lo, val.TAG_U128_OBJECT),
            "obj_to_u128_hi64": self.hi,
            "obj_to_u128_lo64": self.lo,
            "obj_from_i128_pieces": lambda hi, lo: self.handle(hi, lo, val.TAG_I128_OBJECT),
            "obj_to_i128_hi64": self.hi,
            "obj_to_i128_lo64": self.lo,
            "obj_cmp": self.obj_cmp,
        }


# --- boxing test ARGUMENTS the way the host would -----------------------------


def box(store: Store, ty: Ty, value: int) -> int:
    """The `Val` word a host would hand a guest for `value` at `ty`.

    Written from `serpent.val` (the one codec) rather than from the emitter, so
    a lowering bug cannot make its own mistake look correct.
    """
    if ty == Ty.Bool:
        return val.pack_bool(bool(value))
    if ty == Ty.U32:
        return val.pack_u32val(value)
    if ty == Ty.I32:
        return val.pack_i32val(value)
    if ty in (Ty.U64, Ty.Timepoint, Ty.Duration):
        small, obj = {
            Ty.U64: (val.TAG_U64_SMALL, val.TAG_U64_OBJECT),
            Ty.Timepoint: (val.TAG_TIMEPOINT_SMALL, val.TAG_TIMEPOINT_OBJECT),
            Ty.Duration: (val.TAG_DURATION_SMALL, val.TAG_DURATION_OBJECT),
        }[ty]
        if val.fits_small_u(value):
            return val.pack_small_u64(value, small)
        return store.narrow(value, obj)
    if ty == Ty.I64:
        if val.fits_small_i(value):
            return val.pack_small_i64(value, val.TAG_I64_SMALL)
        return store.narrow(value & val.MASK64, val.TAG_I64_OBJECT)
    if ty in (Ty.U128, Ty.I128):
        signed = ty == Ty.I128
        fits = val.fits_small_i(value) if signed else val.fits_small_u(value)
        if fits:
            tag = val.TAG_I128_SMALL if signed else val.TAG_U128_SMALL
            return val.pack_small_i64(value, tag) if signed else val.pack_small_u64(value, tag)
        pattern = value & ((1 << 128) - 1)
        tag = val.TAG_I128_OBJECT if signed else val.TAG_U128_OBJECT
        return store.handle(pattern >> 64, pattern & val.MASK64, tag)
    raise AssertionError(f"no test boxing for {ty.render()}")


# --- building and running a probe module ----------------------------------------


def build(
    e: IRExpr,
    *,
    nparams: int = 0,
    consts: Any = None,
) -> tuple[bytes, Memory]:
    memory = Memory()
    ctx = LowerCtx(n_module_functions=1, memory=memory, consts=consts)
    fn = Fn("probe", nparams, 0, ("i64",))
    lower.lower_expr(fn, ctx, e)
    fn.ret()
    body = fn.finish()
    head: list[testmod.FunctionSpec] = [("probe", nparams, fn.nlocals, ("i64",), body)]
    parts: list[testmod.FunctionSpec] = [
        (p.name, p.nparams, p.nlocals, p.results, p.body) for p in ctx.parts
    ]
    pool = memory.pool_bytes()
    needs_memory = ctx.needs_memory or bool(pool)
    wasm = testmod.build_test_module(
        [*head, *parts],
        imports=ctx.import_order,
        memory_pages=1 if needs_memory else None,
        data=pool if needs_memory else None,
    )
    return wasm, memory


def run(e: IRExpr, *args: int, store: Store | None = None, consts: Any = None) -> int:
    """Lower `e`, run it over `args` (already `Val` words), return its `Val`."""
    store = store if store is not None else Store()
    wasm, _memory = build(e, nparams=len(args), consts=consts)
    host = engine.MiniHost(wasm, imports=store.bridges())
    result = host.invoke("probe", *args)
    assert result is not None
    return result


# ===========================================================================
# Const: the compile-time small/object split (SS B.3.1, F.2.6)
# ===========================================================================


@pytest.mark.parametrize(
    ("ty", "value", "word"),
    [
        (Ty.Bool, True, val.TRUE_VAL),
        (Ty.Bool, False, val.FALSE_VAL),
        (Ty.U32, 0, val.pack_u32val(0)),
        (Ty.U32, U32_MAX, val.pack_u32val(U32_MAX)),
        (Ty.I32, -1, val.pack_i32val(-1)),
        (Ty.I32, I32_MIN, val.pack_i32val(I32_MIN)),
        (Ty.I32, I32_MAX, val.pack_i32val(I32_MAX)),
        # Both sides of every EITHER bound, in both directions (F.2.6).
        (Ty.U64, val.MAX_SMALL_U64, val.pack_small_u64(val.MAX_SMALL_U64, val.TAG_U64_SMALL)),
        (Ty.I64, val.MAX_SMALL_I64, val.pack_small_i64(val.MAX_SMALL_I64, val.TAG_I64_SMALL)),
        (Ty.I64, val.MIN_SMALL_I64, val.pack_small_i64(val.MIN_SMALL_I64, val.TAG_I64_SMALL)),
        (Ty.I64, -1, val.pack_small_i64(-1, val.TAG_I64_SMALL)),
        (
            Ty.Timepoint,
            val.MAX_SMALL_U64,
            val.pack_small_u64(val.MAX_SMALL_U64, val.TAG_TIMEPOINT_SMALL),
        ),
        (Ty.Duration, 7, val.pack_small_u64(7, val.TAG_DURATION_SMALL)),
        # 128-bit small forms are tags 10 and 11 over the SAME 56-bit bodies.
        (Ty.U128, val.MAX_SMALL_U64, val.pack_small_u64(val.MAX_SMALL_U64, val.TAG_U128_SMALL)),
        (Ty.I128, val.MIN_SMALL_I64, val.pack_small_i64(val.MIN_SMALL_I64, val.TAG_I128_SMALL)),
        # A short Symbol is a SymbolSmall immediate -- no pool, no host call.
        (Ty.Symbol, "transfer", val.symbol_small("transfer")),
        (Ty.Symbol, "abcdefghi", val.symbol_small("abcdefghi")),
        # `x: T | None = None` is the Void tag.
        (Ty.Option(Ty.U32), None, val.VOID_VAL),
    ],
)
def test_immediate_const_is_one_i64_const(ty: Ty, value: object, word: int) -> None:
    assert items(const(ty, value)) == [i64c(word) + TAIL]


@pytest.mark.parametrize(
    ("ty", "value", "constructor"),
    [
        (Ty.U64, val.MAX_SMALL_U64 + 1, "obj_from_u64"),
        (Ty.U64, U64_MAX, "obj_from_u64"),
        (Ty.I64, val.MAX_SMALL_I64 + 1, "obj_from_i64"),
        (Ty.I64, val.MIN_SMALL_I64 - 1, "obj_from_i64"),
        (Ty.I64, I64_MIN, "obj_from_i64"),
        (Ty.Timepoint, val.MAX_SMALL_U64 + 1, "timepoint_obj_from_u64"),
        (Ty.Duration, val.MAX_SMALL_U64 + 1, "duration_obj_from_u64"),
    ],
)
def test_wide_64_bit_const_becomes_a_host_object(ty: Ty, value: int, constructor: str) -> None:
    """Past its small bound, a 64-bit literal is one raw word plus a constructor.

    The word is RAW -- no tag, no shift: `obj_from_u64` takes the number, and
    packing it as a small form first would hand the host a `Val` where it
    expects a `u64`.
    """
    assert items(const(ty, value)) == [i64c(value), CallImport(constructor), TAIL]


@pytest.mark.parametrize(
    ("ty", "value", "constructor"),
    [
        (Ty.U128, val.MAX_SMALL_U64 + 1, "obj_from_u128_pieces"),
        (Ty.U128, U128_MAX, "obj_from_u128_pieces"),
        (Ty.I128, val.MIN_SMALL_I64 - 1, "obj_from_i128_pieces"),
        (Ty.I128, I128_MIN, "obj_from_i128_pieces"),
        (Ty.I128, I128_MAX, "obj_from_i128_pieces"),
    ],
)
def test_wide_128_bit_const_becomes_two_limbs(ty: Ty, value: int, constructor: str) -> None:
    """The split is over the TWO'S-COMPLEMENT 128-bit pattern (F.1.12's shape).

    A negative `I128` therefore gets a high limb that is its sign extension,
    not a magnitude with the sign lost -- `I128(-(2**55) - 1)` must not arrive
    at the host as an enormous positive number.
    """
    pattern = value & ((1 << 128) - 1)
    assert items(const(ty, value)) == [
        i64c(pattern >> 64) + i64c(pattern & val.MASK64),
        CallImport(constructor),
        TAIL,
    ]


@pytest.mark.parametrize(
    ("ty", "value", "blob", "constructor"),
    [
        (Ty.Symbol, "a_long_symbol_name", b"a_long_symbol_name", "symbol_new_from_linear_memory"),
        (Ty.String, "hello", b"hello", "string_new_from_linear_memory"),
        (Ty.Bytes, b"\x01\x02", b"\x01\x02", "bytes_new_from_linear_memory"),
        (Ty.BytesN(2), b"\xaa\xbb", b"\xaa\xbb", "bytes_new_from_linear_memory"),
    ],
)
def test_pooled_literal_reads_the_seeded_pool(
    ty: Ty, value: object, blob: bytes, constructor: str
) -> None:
    """A pooled literal is `(offset, length)` as U32Vals, from the SEEDED pool.

    Both arguments are `U32Val`s and not raw numbers -- the pin types them
    that way -- and the offset is whatever `Memory` already assigned, which
    Task 10 fixes by seeding the inventory before any body is lowered (E7).
    """
    memory = Memory()
    memory.intern(b"decoy")  # so a hard-coded offset 0 would not pass
    offset = memory.intern(blob)
    assert items(const(ty, value), memory=memory) == [
        i64c(val.pack_u32val(offset)) + i64c(val.pack_u32val(len(blob))),
        CallImport(constructor),
        TAIL,
    ]


def test_address_literal_pools_its_strkey_then_converts_it() -> None:
    """Review B6: pooled strkey -> `string_new_from_linear_memory` -> `strkey_to_address`.

    `Address` has no small form and no linear-memory constructor of its own.
    The strkey's ASCII text is what goes in the pool -- not the decoded
    32-byte payload -- because `strkey_to_address` is documented as taking the
    `G.../C...` STRING.
    """
    memory = Memory()
    offset = memory.intern(STRKEY.encode("utf-8"))
    assert items(const(Ty.Address, STRKEY), memory=memory) == [
        i64c(val.pack_u32val(offset)) + i64c(val.pack_u32val(len(STRKEY))),
        CallImport("string_new_from_linear_memory"),
        CallImport("strkey_to_address"),
        TAIL,
    ]


def test_symbol_of_exactly_ten_characters_is_pooled_not_packed() -> None:
    """The SymbolSmall bound is 9 characters (S22), and 10 is over it."""
    assert import_calls(items(const(Ty.Symbol, "abcdefghi"))) == []
    assert import_calls(items(const(Ty.Symbol, "abcdefghij"))) == ["symbol_new_from_linear_memory"]


@pytest.mark.parametrize(
    ("ty", "value"),
    [
        (Ty.U64, val.MAX_SMALL_U64 + 1),
        (Ty.U64, U64_MAX),
        (Ty.I64, val.MIN_SMALL_I64 - 1),
        (Ty.I64, I64_MIN),
        (Ty.I64, I64_MAX),
    ],
)
def test_wide_64_bit_const_round_trips_through_the_host(ty: Ty, value: int) -> None:
    """EXECUTED: the object the const builds really carries the right number."""
    store = Store()
    result = run(const(ty, value), store=store)
    assert val.is_object(result)
    assert store.lo(result) == val.as_u64(value)


@pytest.mark.parametrize("value", [val.MAX_SMALL_U64 + 1, U128_MAX, 1 << 100])
def test_wide_u128_const_round_trips_through_the_host(value: int) -> None:
    store = Store()
    result = run(const(Ty.U128, value), store=store)
    assert val.is_object(result)
    assert (store.hi(result) << 64) | store.lo(result) == value


@pytest.mark.parametrize("value", [I128_MIN, I128_MAX, -(1 << 100), val.MIN_SMALL_I64 - 1])
def test_wide_i128_const_round_trips_through_the_host(value: int) -> None:
    store = Store()
    result = run(const(Ty.I128, value), store=store)
    assert val.is_object(result)
    assert (store.hi(result) << 64) | store.lo(result) == value & ((1 << 128) - 1)


# ===========================================================================
# The trivial reads, and the two nodes that must NOT be Vals
# ===========================================================================


def test_param_ref_reads_its_own_index() -> None:
    body = items(param(2, Ty.U32))
    assert body == [bytes([opcodes.LOCAL_GET]) + encode.uleb(2) + TAIL]


def test_local_ref_is_offset_past_the_params() -> None:
    """A `LocalRef` slot is numbered from the FIRST declared local, not from 0."""
    ctx = LowerCtx(n_module_functions=1, memory=Memory())
    fn = Fn("probe", 3, 2, ("i64",))
    lower.lower_expr(fn, ctx, LocalRef(loc=LOC, ty=Ty.U32, slot=1, name="x"))
    fn.ret()
    assert fn.finish() == [bytes([opcodes.LOCAL_GET]) + encode.uleb(3 + 1) + TAIL]


def test_raw_scalar_is_a_bare_number_not_a_u32val() -> None:
    """C13: `StorageType`/`ContractTtlExtension` immediates are NOT `Val`s.

    The host reads these as plain numbers. Packing one as a `U32Val` would
    pass `1 << 32 | 4` where `1` was meant and would validate perfectly.
    """
    node = RawScalar(loc=LOC, ty=Ty.U32, value=1, kind=RawScalarKind.STORAGE_TYPE)
    assert items(node) == [i64c(1) + TAIL]
    assert items(node) != [i64c(val.pack_u32val(1)) + TAIL]


def test_raw_scalar_in_a_raw_position_is_not_unboxed_again() -> None:
    """Its `Val` form and its raw form are the SAME bytes (C13).

    `RawScalar.ty` is `Ty.U32` -- the wasm-level width, not a claim that the
    word is a `U32Val` -- so falling through to `arith.unbox` would shift a
    number that has no tag and quietly produce `0`.
    """
    node = RawScalar(loc=LOC, ty=Ty.U32, value=1, kind=RawScalarKind.CONTRACT_TTL_EXTENSION)
    ctx = LowerCtx(n_module_functions=1, memory=Memory())
    fn = Fn("probe", 0, 0, ("i64",))
    lower.lower_expr_raw(fn, ctx, node)
    fn.ret()
    assert fn.finish() == [i64c(1) + TAIL]


def test_error_val_packs_its_code() -> None:
    node = ErrorVal(loc=LOC, ty=Ty.ErrorEnum("E"), enum="E", case="Bad", code=7)
    assert items(node) == [i64c(val.error_val(7)) + TAIL]


# ===========================================================================
# IsZero / Unary
# ===========================================================================


@pytest.mark.parametrize("ty", [Ty.U32, Ty.I32, Ty.U64, Ty.I64])
@pytest.mark.parametrize("value", [0, 1, 5])
def test_is_zero_asks_about_the_number_not_the_word(ty: Ty, value: int) -> None:
    """D3's polarity: this is IS zero, and it unboxes first.

    `U64(0)` boxed is `pack_small_u64(0, TAG_U64_SMALL) == 6`, a NONZERO word.
    An `i64.eqz` on the `Val` would answer `False` for a value that is plainly
    zero -- which is why the operand is unboxed first.
    """
    store = Store()
    node = IsZero(loc=LOC, ty=Ty.Bool, operand=param(0, ty))
    assert run(node, box(store, ty, value), store=store) == val.pack_bool(value == 0)


@pytest.mark.parametrize("ty", [Ty.U128, Ty.I128])
@pytest.mark.parametrize("value", [0, 1, 1 << 100])
def test_is_zero_at_128_bits_needs_both_limbs(ty: Ty, value: int) -> None:
    """`1 << 100` has a zero LOW limb: testing only that limb says "zero"."""
    store = Store()
    node = IsZero(loc=LOC, ty=Ty.Bool, operand=param(0, ty))
    assert run(node, box(store, ty, value), store=store) == val.pack_bool(value == 0)


@pytest.mark.parametrize("value", [True, False])
def test_not_inverts_a_bool_val(value: bool) -> None:
    node = Unary(loc=LOC, ty=Ty.Bool, op=UnaryOp.NOT, operand=param(0, Ty.Bool))
    assert run(node, val.pack_bool(value)) == val.pack_bool(not value)


def test_not_refuses_a_non_bool_operand() -> None:
    node = Unary(loc=LOC, ty=Ty.Bool, op=UnaryOp.NOT, operand=param(0, Ty.U32))
    with pytest.raises(EmitError, match="`not` takes a Bool"):
        items(node)


@pytest.mark.parametrize(
    ("ty", "value"),
    [(Ty.I32, 5), (Ty.I32, -5), (Ty.I64, 1 << 40), (Ty.I64, -1), (Ty.U32, 0), (Ty.U64, 0)],
)
def test_neg_round_trips_through_unbox_and_rebox(ty: Ty, value: int) -> None:
    store = Store()
    node = Unary(loc=LOC, ty=ty, op=UnaryOp.NEG, operand=param(0, ty))
    result = run(node, box(store, ty, value), store=store)
    # Every vector here negates into the SMALL form, so the expected word is
    # `val`'s own packing and no handle identity is involved.
    assert result == box(Store(), ty, -value)


def test_neg_of_a_large_i64_boxes_back_into_an_object() -> None:
    """`-(2**60)` is past `MIN_SMALL_I64`, so the result is a fresh handle."""
    store = Store()
    node = Unary(loc=LOC, ty=Ty.I64, op=UnaryOp.NEG, operand=param(0, Ty.I64))
    result = run(node, box(store, Ty.I64, 1 << 60), store=store)
    assert val.is_object(result)
    assert store.lo(result) == val.as_u64(-(1 << 60))


@pytest.mark.parametrize("value", [1, -1, 1 << 100, I128_MAX])
def test_neg_at_128_bits_goes_through_the_limb_part(value: int) -> None:
    store = Store()
    node = Unary(loc=LOC, ty=Ty.I128, op=UnaryOp.NEG, operand=param(0, Ty.I128))
    result = run(node, box(store, Ty.I128, value), store=store)
    if val.is_object(result):
        got = (store.hi(result) << 64) | store.lo(result)
        assert got == (-value) & ((1 << 128) - 1)
    else:
        assert got_small(result) == -value


def got_small(word: int) -> int:
    return val.unpack_small_i64(word, val.TAG_I128_SMALL)


# ===========================================================================
# Binary: raw in, raw out, boxed here -- and NO folding (F.1.10/F.1.16)
# ===========================================================================


@pytest.mark.parametrize(
    ("ty", "op", "a", "b", "want"),
    [
        (Ty.U32, BinaryOp.ADD, 1, 2, 3),
        (Ty.I32, BinaryOp.SUB, -5, 7, -12),
        (Ty.U64, BinaryOp.MUL, 1 << 40, 3, 3 << 40),
        (Ty.I64, BinaryOp.FLOORDIV, -7, 2, -3),  # A4 truncates toward zero
        (Ty.I64, BinaryOp.MOD, -7, 2, -1),  # A4 takes the DIVIDEND's sign
        (Ty.U128, BinaryOp.ADD, 1 << 100, 1 << 100, 1 << 101),
        (Ty.I128, BinaryOp.SUB, 0, 1, -1),
    ],
)
def test_binary_boxes_the_checked_result(ty: Ty, op: BinaryOp, a: int, b: int, want: int) -> None:
    store = Store()
    node = Binary(loc=LOC, ty=ty, op=op, lhs=param(0, ty), rhs=param(1, ty))
    result = run(node, box(store, ty, a), box(store, ty, b), store=store)
    if val.is_object(result):
        got = (store.hi(result) << 64) | store.lo(result)
        assert got == want & ((1 << 128) - 1)
    else:
        assert result == box(Store(), ty, want)


def test_two_literal_operands_still_overflow_at_runtime() -> None:
    """F.1.10/F.1.16: no constant folding, ever.

    `I32(2**31 - 1) + I32(1)` must survive to runtime as an
    `ArithmeticOverflow`, not become a compile-time error or a silently
    wrapped `Const`. The raw-const shortcut in `lower_expr_raw` is a
    same-semantics rewrite of ONE node; it must not become an evaluator.
    """
    node = Binary(
        loc=LOC,
        ty=Ty.I32,
        op=BinaryOp.ADD,
        lhs=const(Ty.I32, I32_MAX),
        rhs=const(Ty.I32, 1),
    )
    blob = items(node)[0]
    assert isinstance(blob, bytes)
    # The two operands are still there, as raw numbers, and the add still runs.
    assert blob.startswith(i64c(I32_MAX) + i64c(1))
    with pytest.raises(engine.HostError):
        run(node)


# ===========================================================================
# Compare, F.1.1: unbox BEFORE you compare
# ===========================================================================

ORACLE_OP: dict[CompareOp, Callable[[Any, Any], bool]] = {
    CompareOp.EQ: operator.eq,
    CompareOp.NE: operator.ne,
    CompareOp.LT: operator.lt,
    CompareOp.LE: operator.le,
    CompareOp.GT: operator.gt,
    CompareOp.GE: operator.ge,
}

#: The tier-1 class for each directly-comparable `Ty` -- the ORACLE (A18), so
#: the expected answers are the ones a contract author's Python already gives.
ORACLE_CLASS = {
    Ty.U32: U32,
    Ty.I32: I32,
    Ty.U64: U64,
    Ty.I64: I64,
    Ty.U128: U128,
    Ty.I128: I128,
    Ty.Timepoint: Timepoint,
    Ty.Duration: Duration,
}


def check_compare(ty: Ty, op: CompareOp, a: int, b: int) -> None:
    store = Store()
    node = compare(op, param(0, ty), param(1, ty))
    got = run(node, box(store, ty, a), box(store, ty, b), store=store)
    cls = ORACLE_CLASS[ty]
    want = ORACLE_OP[op](cls(a), cls(b))
    assert got == val.pack_bool(want), f"{ty.render()} {a} {op.value} {b}"


#: `U64` pairs that STRADDLE `MAX_SMALL_U64`, in both directions, plus a pair
#: on each side of it. F.1.1's exact mitigation: with a small form on one side
#: and an object handle on the other, a raw compare of the `Val` words is
#: comparing a 56-bit body against a handle INDEX, and the handle index is
#: tiny -- so `U64(MAX_SMALL_U64 + 1) > U64(1)` comes out FALSE.
U64_STRADDLE = [
    (val.MAX_SMALL_U64, val.MAX_SMALL_U64 + 1),
    (val.MAX_SMALL_U64 + 1, val.MAX_SMALL_U64),
    (1, val.MAX_SMALL_U64 + 1),
    (val.MAX_SMALL_U64 + 1, 1),
    (U64_MAX, 0),
    (0, U64_MAX),
    (val.MAX_SMALL_U64 + 1, val.MAX_SMALL_U64 + 1),
    (0, 0),
]

#: The same across `MIN_SMALL_I64` and `MAX_SMALL_I64`, both directions.
I64_STRADDLE = [
    (val.MIN_SMALL_I64, val.MIN_SMALL_I64 - 1),
    (val.MIN_SMALL_I64 - 1, val.MIN_SMALL_I64),
    (val.MAX_SMALL_I64, val.MAX_SMALL_I64 + 1),
    (val.MAX_SMALL_I64 + 1, val.MAX_SMALL_I64),
    (-1, val.MIN_SMALL_I64 - 1),
    (val.MIN_SMALL_I64 - 1, -1),
    (I64_MIN, I64_MAX),
    (I64_MAX, I64_MIN),
    (-1, 0),
    (0, -1),
]


@pytest.mark.parametrize("op", list(CompareOp))
@pytest.mark.parametrize(("a", "b"), U64_STRADDLE)
def test_u64_compare_straddling_the_small_bound(op: CompareOp, a: int, b: int) -> None:
    check_compare(Ty.U64, op, a, b)


@pytest.mark.parametrize("op", list(CompareOp))
@pytest.mark.parametrize(("a", "b"), I64_STRADDLE)
def test_i64_compare_straddling_the_small_bound(op: CompareOp, a: int, b: int) -> None:
    check_compare(Ty.I64, op, a, b)


@pytest.mark.parametrize("op", list(CompareOp))
@pytest.mark.parametrize(
    ("a", "b"),
    [
        (val.MAX_SMALL_U64, val.MAX_SMALL_U64 + 1),
        (val.MAX_SMALL_U64 + 1, val.MAX_SMALL_U64),
        (0, U64_MAX),
    ],
)
def test_timepoint_compare_is_a_scalar_compare(op: CompareOp, a: int, b: int) -> None:
    """D4 removes Timepoint/Duration ARITHMETIC, not their comparisons.

    They are EITHER-repr like `U64`, so they straddle the same bound and go
    through their own unbox parts (`unbox_timepoint`/`unbox_duration`, whose
    bodies are UNSIGNED).
    """
    check_compare(Ty.Timepoint, op, a, b)
    check_compare(Ty.Duration, op, a, b)


# --- the signedness matrix (F.1.11) --------------------------------------------

#: Pairs that straddle each width's SIGN boundary -- the values where reading
#: the relop's signedness off the representation instead of the operand type
#: inverts the answer. `U32(2**31)` has bit 31 set; `I64(-1)` is `2**64 - 1`
#: unsigned; `I32(-1)` sign-extends to all ones.
SIGN_BOUNDARY: dict[Ty, list[tuple[int, int]]] = {
    Ty.U32: [(1 << 31, 1), (1, 1 << 31), (U32_MAX, 0), (0, U32_MAX)],
    Ty.I32: [(-1, 1), (1, -1), (I32_MIN, I32_MAX), (I32_MAX, I32_MIN), (-1, -1)],
    Ty.U64: [(1 << 63, 1), (1, 1 << 63), (U64_MAX, 1), (1, U64_MAX)],
    Ty.I64: [(-1, 1), (1, -1), (I64_MIN, I64_MAX), (I64_MAX, I64_MIN), (-1, -1)],
    Ty.U128: [(1 << 127, 1), (1, 1 << 127), (U128_MAX, 1)],
    Ty.I128: [(-1, 1), (1, -1), (I128_MIN, I128_MAX), (I128_MAX, I128_MIN)],
}


@pytest.mark.parametrize("ty", list(SIGN_BOUNDARY))
@pytest.mark.parametrize("op", list(CompareOp))
def test_compare_signedness_comes_from_the_operand_type(ty: Ty, op: CompareOp) -> None:
    for a, b in SIGN_BOUNDARY[ty]:
        check_compare(ty, op, a, b)


def test_bool_compare_is_a_direct_word_compare() -> None:
    """A Bool `Val` IS 0/1, so there is nothing to unbox and nothing to tag."""
    for a in (False, True):
        for b in (False, True):
            node = compare(CompareOp.EQ, param(0, Ty.Bool), param(1, Ty.Bool))
            got = run(node, val.pack_bool(a), val.pack_bool(b))
            assert got == val.pack_bool(a == b)


# --- the obj_cmp route (F.1.2/T5, B2) -------------------------------------------


@pytest.mark.parametrize("op", list(CompareOp))
def test_obj_cmp_route_passes_val_words_and_tests_the_answer_signed(op: CompareOp) -> None:
    """`via_obj_cmp` sends both sides in as `Val`s and gets a RAW -1/0/1 back.

    The host's answer is a plain signed `i64` (`val_typed_ret` is `False`, B2),
    so the operator becomes a SIGNED comparison against zero. An unsigned test
    would call `-1 < 0` false and invert every `<` on every object type.
    """
    for a, b, three_way in ((1, 2, -1), (2, 1, 1), (2, 2, 0)):
        store = Store()
        node = compare(op, param(0, Ty.Symbol), param(1, Ty.Symbol), via_obj_cmp=True)
        # Words chosen so the toy `obj_cmp` reproduces `three_way` (it compares
        # the raw words) -- the point of the vector is the -1/0/1 handling.
        got = run(node, a, b, store=store)
        assert store.cmp_calls == [(a, b)]
        assert got == val.pack_bool(ORACLE_OP[op](three_way, 0))


def test_obj_cmp_route_never_unboxes_its_operands() -> None:
    """Both sides go in as `Val` WORDS: that is what the host compares."""
    node = compare(CompareOp.LT, const(Ty.Symbol, "a"), const(Ty.Symbol, "b"), via_obj_cmp=True)
    body = items(node)
    assert import_calls(body) == ["obj_cmp"]
    assert body[0] == i64c(val.symbol_small("a")) + i64c(val.symbol_small("b"))


# ===========================================================================
# BoolOp / IfExp: short-circuit and lazy arms (C8, SS B.2's select ban)
# ===========================================================================


@pytest.mark.parametrize(
    ("kind", "first", "evaluates_second"),
    [
        (BoolOpKind.AND, False, False),
        (BoolOpKind.AND, True, True),
        (BoolOpKind.OR, True, False),
        (BoolOpKind.OR, False, True),
    ],
)
def test_boolop_short_circuits(kind: BoolOpKind, first: bool, evaluates_second: bool) -> None:
    """C8: the second operand's HOST CALL must not happen when it cannot matter.

    Proven with a side effect, not with a byte count: the second operand is a
    `Symbol` comparison, which always routes through `obj_cmp`, and the
    harness callback records every call it sees. An operand can trap, fail
    with a contract error, or spend budget, so eager evaluation is observably
    wrong on chain -- not merely slower.
    """
    store = Store()
    node = BoolOp(
        loc=LOC,
        ty=Ty.Bool,
        op=kind,
        operands=(param(0, Ty.Bool), probe_effect()),
    )
    got = run(node, val.pack_bool(first), store=store)
    assert bool(store.cmp_calls) is evaluates_second
    second = val.pack_bool(val.symbol_small("a") == val.symbol_small("b"))
    want = second if evaluates_second else val.pack_bool(first)
    assert got == want


@pytest.mark.parametrize("kind", list(BoolOpKind))
@pytest.mark.parametrize("bits", [(a, b, c) for a in (0, 1) for b in (0, 1) for c in (0, 1)])
def test_boolop_of_three_operands_matches_python(
    kind: BoolOpKind, bits: tuple[int, int, int]
) -> None:
    node = BoolOp(
        loc=LOC,
        ty=Ty.Bool,
        op=kind,
        operands=(param(0, Ty.Bool), param(1, Ty.Bool), param(2, Ty.Bool)),
    )
    args = [val.pack_bool(bool(b)) for b in bits]
    flags = [bool(b) for b in bits]
    want = (flags[0] and flags[1] and flags[2]) if kind is BoolOpKind.AND else any(flags)
    assert run(node, *args) == val.pack_bool(want)


def test_boolop_refuses_a_single_operand() -> None:
    node = BoolOp(loc=LOC, ty=Ty.Bool, op=BoolOpKind.AND, operands=(param(0, Ty.Bool),))
    with pytest.raises(EmitError, match="at least two operands"):
        items(node)


@pytest.mark.parametrize("cond", [True, False])
def test_if_exp_evaluates_only_the_taken_arm(cond: bool) -> None:
    """SS B.2 bans `select`, which evaluates BOTH arms.

    The untaken arm here is a `Symbol` comparison; if it ran, the recording
    `obj_cmp` would see it.
    """
    store = Store()
    node = IfExp(
        loc=LOC,
        ty=Ty.Bool,
        cond=param(0, Ty.Bool),
        then=const(Ty.Bool, True),
        orelse=probe_effect(),
    )
    got = run(node, val.pack_bool(cond), store=store)
    assert bool(store.cmp_calls) is (not cond)
    assert got == (val.TRUE_VAL if cond else val.pack_bool(False))


def test_if_exp_uses_a_result_bearing_if_not_select() -> None:
    node = IfExp(
        loc=LOC,
        ty=Ty.U32,
        cond=param(0, Ty.Bool),
        then=const(Ty.U32, 1),
        orelse=const(Ty.U32, 2),
    )
    (blob,) = items(node)
    assert isinstance(blob, bytes)
    assert bytes([opcodes.IF, opcodes.BLOCKTYPE_I64]) in blob
    assert bytes([opcodes.ELSE]) in blob


# --- lower_condition, the ONE helper (review m2) ---------------------------------


def test_lower_condition_is_one_wrap() -> None:
    """A Bool `Val` is literally 0/1, so the branch condition is `i32.wrap_i64`.

    Not `i64.const 1; i64.eq`, and not a tag test: `val.pack_bool` produces
    `TAG_FALSE == 0` / `TAG_TRUE == 1`, and the ABI prologues guarantee the
    same for parameters.
    """
    ctx = LowerCtx(n_module_functions=1, memory=Memory())
    fn = Fn("probe", 1, 0, ("i64",))
    lower.lower_condition(fn, ctx, param(0, Ty.Bool))
    fn.begin_if("i64")
    fn.i64_const(0)
    fn.else_()
    fn.i64_const(1)
    fn.end_if()
    fn.ret()
    (blob,) = fn.finish()
    assert isinstance(blob, bytes)
    assert blob.startswith(
        bytes([opcodes.LOCAL_GET]) + encode.uleb(0) + bytes([opcodes.I32_WRAP_I64])
    )


def test_lower_condition_refuses_a_non_bool() -> None:
    ctx = LowerCtx(n_module_functions=1, memory=Memory())
    fn = Fn("probe", 1, 0, ("i64",))
    with pytest.raises(EmitError, match="condition must be Bool"):
        lower.lower_condition(fn, ctx, param(0, Ty.U32))


# ===========================================================================
# ConstRef: inline at each use (E11), memoise a POOLED one (review M8)
# ===========================================================================

POOLED_CONST = {"ADMIN": const(Ty.Symbol, "administrator")}
SMALL_CONST = {"K": const(Ty.Symbol, "k")}


def test_const_ref_inlines_the_declared_value() -> None:
    node = ConstRef(loc=LOC, ty=Ty.Symbol, name="K")
    assert items(node, consts=SMALL_CONST) == [i64c(val.symbol_small("k")) + TAIL]


def test_const_ref_without_a_const_table_is_loud() -> None:
    with pytest.raises(EmitError, match="has no entry in LowerCtx.consts"):
        items(ConstRef(loc=LOC, ty=Ty.Symbol, name="K"))


def test_pooled_const_ref_used_twice_builds_its_object_once() -> None:
    """Review M8, at the BYTE level: one `symbol_new_from_linear_memory`, not two.

    Ruling E11 inlines a chain constant at every use, which for an immediate
    is free and for a POOLED literal is a host call that allocates. The first
    use `local.tee`s the object into a hidden local and every later use reads
    it back. Checked structurally rather than by execution because the
    linear-memory harness callback does not exist until Task 8; Task 13
    re-proves it end to end.
    """
    node = compare(
        CompareOp.EQ,
        ConstRef(loc=LOC, ty=Ty.Symbol, name="ADMIN"),
        ConstRef(loc=LOC, ty=Ty.Symbol, name="ADMIN"),
        via_obj_cmp=True,
    )
    body = items(node, consts=POOLED_CONST)
    assert import_calls(body) == ["symbol_new_from_linear_memory", "obj_cmp"]


def test_immediate_const_ref_used_twice_is_not_memoised() -> None:
    """An immediate is one `i64.const`; a hidden local would only cost more."""
    node = compare(
        CompareOp.EQ,
        ConstRef(loc=LOC, ty=Ty.Symbol, name="K"),
        ConstRef(loc=LOC, ty=Ty.Symbol, name="K"),
        via_obj_cmp=True,
    )
    ctx = LowerCtx(n_module_functions=1, memory=Memory(), consts=SMALL_CONST)
    fn = Fn("probe", 0, 0, ("i64",))
    lower.lower_expr(fn, ctx, node)
    fn.ret()
    body = fn.finish()
    assert fn.n_hidden == 0
    assert body[0] == i64c(val.symbol_small("k")) + i64c(val.symbol_small("k"))


def test_pooled_const_ref_first_used_inside_a_branch_is_not_memoised() -> None:
    """The memo is a DOMINANCE argument, not a peephole.

    A wasm local starts at `0`, which is a perfectly valid `Val`
    (`FALSE_VAL`), so reading one that was never written is silent corruption
    rather than a trap. A definition inside an `if` arm does not dominate a
    use in the sibling arm, so the first use here must NOT claim a slot -- both
    arms build the object for themselves.
    """
    node = IfExp(
        loc=LOC,
        ty=Ty.Symbol,
        cond=param(0, Ty.Bool),
        then=ConstRef(loc=LOC, ty=Ty.Symbol, name="ADMIN"),
        orelse=ConstRef(loc=LOC, ty=Ty.Symbol, name="ADMIN"),
    )
    ctx = LowerCtx(n_module_functions=1, memory=Memory(), consts=POOLED_CONST)
    fn = Fn("probe", 1, 0, ("i64",))
    lower.lower_expr(fn, ctx, node)
    fn.ret()
    body = fn.finish()
    assert import_calls(body) == [
        "symbol_new_from_linear_memory",
        "symbol_new_from_linear_memory",
    ]
    assert fn.n_hidden == 0


def test_the_const_memo_does_not_leak_between_functions() -> None:
    """A hidden local belongs to ONE body: slot 3 of `f` is not slot 3 of `g`."""
    ctx = LowerCtx(n_module_functions=2, memory=Memory(), consts=POOLED_CONST)
    node = ConstRef(loc=LOC, ty=Ty.Symbol, name="ADMIN")
    for _ in range(2):
        fn = Fn("probe", 0, 0, ("i64",))
        lower.lower_expr(fn, ctx, node)
        fn.ret()
        assert import_calls(fn.finish()) == ["symbol_new_from_linear_memory"]


# ===========================================================================
# The loud defaults (C11, F.1.15)
# ===========================================================================


def test_convert_is_loud() -> None:
    """C11: no producer builds a `Convert` today, and a no-op would be silent."""
    node = Convert(
        loc=LOC, ty=Ty.U64, from_ty=Ty.Timepoint, to_ty=Ty.U64, operand=param(0, Ty.Timepoint)
    )
    with pytest.raises(EmitError, match="has no lowering"):
        items(node)


@pytest.mark.parametrize(
    ("node", "message"),
    [
        # An empty struct has no key descriptors to lay out at all.
        (
            MakeStruct(loc=LOC, ty=Ty.Struct("S"), struct_name="S", fields=()),
            "has no fields",
        ),
        # `vec_len` takes one argument in the pin; the IR handed it none (B2).
        (
            HostCall(loc=LOC, ty=Ty.U32, fn_name="vec_len", args=()),
            "takes 1 argument",
        ),
        # A helper the context has no defidx for (review B1).
        (
            InternalCall(loc=LOC, ty=Ty.U32, fn_name="helper", args=()),
            "has no entry in LowerCtx.functions",
        ),
    ],
)
def test_the_object_kinds_refuse_a_malformed_node_loudly(node: IRExpr, message: str) -> None:
    """These four used to be Task 8's "not lowered here" placeholder.

    They are lowered now (`test_emitter_lower_objects.py`), so what is pinned
    here is the other half: each still refuses LOUDLY when the node it is
    handed cannot be lowered, rather than emitting something that validates.
    """
    with pytest.raises(EmitError, match=message):
        items(node)


def test_an_empty_static_vec_falls_back_to_the_chain() -> None:
    """C4's gate answers "no" for an empty vector -- there is nothing to lay
    out, and `vec_new` with no pushes is the honest lowering (it is also what
    `recognize._all_static` already decided, MJ-15)."""
    node = MakeVec(loc=LOC, ty=Ty.Vec(Ty.U32), elem_ty=Ty.U32, items=(), all_static=True)
    assert import_calls(items(node)) == ["vec_new"]


def test_an_unknown_node_kind_is_loud() -> None:
    """F.1.15: the dispatch is exhaustive; a new node must not fall through."""

    class Invented(IRExpr):
        pass

    with pytest.raises(EmitError, match="no lowering for IR node"):
        items(Invented(loc=LOC, ty=Ty.U32))


def test_a_const_carrying_ty_invalid_is_loud() -> None:
    with pytest.raises(EmitError, match="Ty.Invalid"):
        items(const(Ty.Invalid, 1))


# ===========================================================================
# The value model itself (review M1)
# ===========================================================================


def test_every_scalar_lowering_leaves_exactly_one_i64() -> None:
    """`expr_scope` is what makes this checkable at the node that broke it."""
    nodes: list[IRExpr] = [
        const(Ty.U32, 1),
        const(Ty.U64, U64_MAX),
        param(0, Ty.Bool),
        IsZero(loc=LOC, ty=Ty.Bool, operand=param(0, Ty.U64)),
        Unary(loc=LOC, ty=Ty.I64, op=UnaryOp.NEG, operand=param(0, Ty.I64)),
        Binary(loc=LOC, ty=Ty.U64, op=BinaryOp.ADD, lhs=param(0, Ty.U64), rhs=param(0, Ty.U64)),
        compare(CompareOp.LT, param(0, Ty.I64), param(0, Ty.I64)),
        compare(CompareOp.LT, param(0, Ty.Symbol), param(0, Ty.Symbol), via_obj_cmp=True),
        BoolOp(loc=LOC, ty=Ty.Bool, op=BoolOpKind.AND, operands=(param(0, Ty.Bool),) * 2),
        IfExp(
            loc=LOC,
            ty=Ty.U32,
            cond=param(0, Ty.Bool),
            then=const(Ty.U32, 1),
            orelse=const(Ty.U32, 2),
        ),
    ]
    for node in nodes:
        ctx = LowerCtx(n_module_functions=1, memory=Memory())
        fn = Fn("probe", 1, 0, ("i64",))
        lower.lower_expr(fn, ctx, node)
        assert fn.stack == ["i64"], node
        fn.ret()
        fn.finish()


def test_lower_expr_raw_of_a_const_skips_the_box_unbox_round_trip() -> None:
    """The shortcut F.1.16 permits: one raw `i64.const`, not box-then-unbox."""
    ctx = LowerCtx(n_module_functions=1, memory=Memory())
    fn = Fn("probe", 0, 0, ("i64",))
    lower.lower_expr_raw(fn, ctx, const(Ty.U64, U64_MAX))
    fn.ret()
    assert fn.finish() == [i64c(U64_MAX) + TAIL]
    # And nothing was registered as an import: no `obj_from_u64` at all.
    assert ctx.import_order == ()


def test_lower_expr_raw_refuses_a_128_bit_operand() -> None:
    """A 128-bit value is a limb PAIR; there is no single raw word to leave."""
    ctx = LowerCtx(n_module_functions=1, memory=Memory())
    fn = Fn("probe", 1, 0, ("i64",))
    with pytest.raises(EmitError, match="limb"):
        lower.lower_expr_raw(fn, ctx, param(0, Ty.U128))
