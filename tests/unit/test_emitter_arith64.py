"""Tests for `serpent.emitter.arith` -- 32/64-bit checked arithmetic and boxing.

These are **executed** goldens, not byte snapshots: every vector below is
compiled into a module by `tests/harness/testmod` and *run* on the pinned
wasmtime (`tests/harness/engine`). A byte snapshot of a lowering that is
subtly wrong would pin the wrong bytes forever; a run does not.

What is being protected, in order of how badly it would bite:

* **A4's checked-arithmetic contract.** `//` truncates toward zero, `%` takes
  the DIVIDEND's sign, `MIN % -1 == 0`, and every out-of-range result reaches
  `fail_with_error(ArithmeticOverflow)` rather than wrapping. Wrapping output
  validates and deploys, so the expectations here are written out as integers
  computed under A4's rules (spelled inline, `a4_floordiv`/`a4_mod`), never
  as Python's own `//`/`%` -- Python floors and takes the divisor's sign, and
  reusing it would encode the wrong contract in the very test meant to pin it.
* **Review B10's unbox recipe.** The small form's 56-bit body already carries
  its sign bit at word bit 63, so a signed unbox is ONE `i64.shr_s 8` and
  nothing else. The superseded "shl 8 then shr_s 8" recipe corrupts every
  value -- `I64(-1)` decodes to -249 -- so `I64(-1)`, `I64(-2**50)`, and
  `MIN_SMALL_I64` are named regression vectors here.
* **F.1.3's named anti-pattern.** The spike repacked a 32-bit result with a
  wrapping `i64.shl 32` and no range check, so `U32(2**32 - 1) + U32(1)`
  silently became `U32(0)`. Every 32-bit repack here is range-checked, and
  `test_rebox_u32_range_checks_instead_of_wrapping` is the vector that fails
  loudly if the check is ever dropped.
* **Review M6's negation rules.** No `neg` part exists at 32 or 64 bits:
  unsigned `-x` is "nonzero is overflow, else 0", signed `-x` is "MIN is
  overflow, else 0 - value".
* **Review M7's mul overflow algorithms**, especially the `MIN` operand cases
  a magnitude-based signed multiply gets wrong.
* **Review B1's index space.** `ensure_part` hands out defined-space indices
  after the module's own functions; a wrong index calls the wrong function of
  the same type and still validates, so `test_ensure_part_indices_survive_a_
  real_call` asserts a returned VALUE, not a byte.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Sequence

import pytest
import wasmtime
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from serpent import val
from serpent.compiler.ir import BinaryOp
from serpent.compiler.types_ import Ty
from serpent.emitter import arith, frame
from serpent.emitter.arith import EmitCtx
from serpent.emitter.layout import Memory
from serpent.errors import CODE_ARITHMETIC_OVERFLOW
from tests.harness import engine, i256, testmod

# --- the values every vector is written against -------------------------------

U32_MAX = (1 << 32) - 1
I32_MIN = -(1 << 31)
I32_MAX = (1 << 31) - 1
U64_MAX = (1 << 64) - 1
I64_MIN = -(1 << 63)
I64_MAX = (1 << 63) - 1

#: The exact error `Val` an overflowing part must hand `fail_with_error`.
OVERFLOW_VAL = val.error_val(CODE_ARITHMETIC_OVERFLOW)

#: A sentinel standing in for "this vector must abort with OVERFLOW_VAL".
OVERFLOW = object()


# --- A4's arithmetic, written out rather than borrowed from Python -----------
# Public (no leading underscore) because `test_emitter_arith128.py` imports
# them: one oracle for A4's rules across both files, so a "fix" to one cannot
# leave the other pinning the opposite contract.


def a4_floordiv(a: int, b: int) -> int:
    """`//` TRUNCATED TOWARD ZERO (A4) -- not Python's floor division."""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def a4_mod(a: int, b: int) -> int:
    """`%` taking the DIVIDEND's sign (A4) -- not Python's divisor's sign."""
    return a - a4_floordiv(a, b) * b


def a4_apply(op: BinaryOp, a: int, b: int) -> int:
    if op is BinaryOp.ADD:
        return a + b
    if op is BinaryOp.SUB:
        return a - b
    if op is BinaryOp.MUL:
        return a * b
    if op is BinaryOp.FLOORDIV:
        return a4_floordiv(a, b)
    return a4_mod(a, b)


# --- the host's integer bridges, as Python callbacks --------------------------


class _ObjectStore:
    """A toy stand-in for the host's u64/i64/timepoint/duration object handles.

    Real handles are opaque; all these tests need is that `obj_from_u64` and
    `obj_to_u64` round-trip a raw 64-bit word through something that is NOT the
    small form, so the object arm of every unbox/rebox is actually exercised.
    The handle's body is an index into `words`.
    """

    def __init__(self) -> None:
        self.words: list[int] = []

    def handle(self, raw_word: int, tag: int) -> int:
        self.words.append(val.as_u64(raw_word))
        return val.from_body_tag(len(self.words) - 1, tag)

    def raw(self, handle: int) -> int:
        return self.words[val.body_of(handle)]

    def bridges(self) -> dict[str, Callable[..., int]]:
        return {
            "obj_from_u64": lambda x: self.handle(x, val.TAG_U64_OBJECT),
            "obj_to_u64": self.raw,
            "obj_from_i64": lambda x: self.handle(x, val.TAG_I64_OBJECT),
            "obj_to_i64": self.raw,
            "timepoint_obj_to_u64": self.raw,
            "duration_obj_to_u64": self.raw,
            # Task 9's `tagcheck_bytes_n` is the one part that calls a host
            # function; bound (rather than modelled) so the whole ratified
            # inventory still instantiates in one module below.
            "bytes_len": lambda handle: val.pack_u32val(0),
        }


# --- building and running a probe module --------------------------------------

#: `(name, nparams, nlocals, results, body)` -- `testmod`'s own function spec.
_Spec = testmod.FunctionSpec


def _part_specs(ctx: EmitCtx) -> list[_Spec]:
    return [(p.name, p.nparams, p.nlocals, p.results, p.body) for p in ctx.parts]


def _module(ctx: EmitCtx, head: Sequence[_Spec] = ()) -> bytes:
    """Assemble `head` (the module's own functions) followed by `ctx`'s parts.

    A page of memory is added iff a linked part reserved scratch (review B8):
    the two-result 128-bit parts write their `lo` limb there, so linking one
    forces memory even though nothing in this file interns a literal.
    """
    return testmod.build_test_module(
        [*head, *_part_specs(ctx)],
        imports=ctx.import_order,
        memory_pages=1 if ctx.needs_memory else None,
    )


@functools.cache
def _parts_wasm(names: tuple[str, ...]) -> bytes:
    ctx = EmitCtx(n_module_functions=0, memory=Memory())
    for name in names:
        ctx.ensure_part(name)
    return _module(ctx)


def _part_host(name: str) -> tuple[engine.MiniHost, _ObjectStore]:
    """A fresh host exporting `name` (and whatever it pulled in), plus its store.

    Fresh per call rather than cached: a vector that overflows aborts through a
    Python exception raised inside a host callback, and a `Store` that has been
    unwound is not something these tests should be reasoning about.
    """
    store = _ObjectStore()
    return engine.MiniHost(_parts_wasm((name,)), imports=store.bridges()), store


_Emit = Callable[[frame.Fn, EmitCtx], None]


@functools.cache
def _inline_wasm(emit: _Emit, nparams: int) -> bytes:
    """A one-function module whose body is `emit`, run over its own params.

    `n_module_functions=1` because the probe itself occupies defined index 0;
    any part `emit` reaches lands after it, which is exactly the arrangement
    `module.assemble` will use.
    """
    ctx = EmitCtx(n_module_functions=1, memory=Memory())
    fn = frame.Fn("probe", nparams, 0, ("i64",))
    for i in range(nparams):
        fn.local_get(i)
    emit(fn, ctx)
    fn.ret()
    body = fn.finish()
    head: list[_Spec] = [("probe", nparams, fn.nlocals, ("i64",), body)]
    return _module(ctx, head)


def _inline_host(emit: _Emit, nparams: int) -> tuple[engine.MiniHost, _ObjectStore]:
    store = _ObjectStore()
    return engine.MiniHost(_inline_wasm(emit, nparams), imports=store.bridges()), store


def _expect(host: engine.MiniHost, name: str, args: Sequence[int], want: object) -> None:
    """Invoke `name` and assert either its raw word or an ArithmeticOverflow."""
    words = [val.as_u64(a) for a in args]
    if want is OVERFLOW:
        with pytest.raises(engine.HostError) as caught:
            host.invoke(name, *words)
        assert caught.value.val == OVERFLOW_VAL
        return
    assert isinstance(want, int)
    assert host.invoke(name, *words) == val.as_u64(want)


def _check_part(name: str, args: Sequence[int], want: object) -> None:
    host, _store = _part_host(name)
    _expect(host, name, args, want)


def _check_inline(emit: _Emit, args: Sequence[int], want: object) -> None:
    host, _store = _inline_host(emit, len(args))
    _expect(host, "probe", args, want)


# --- named emitters, so `lru_cache` above has something hashable to key on ----


def _binary(ty: Ty, op: BinaryOp) -> _Emit:
    def emit(fn: frame.Fn, ctx: EmitCtx) -> None:
        arith.lower_binary(fn, ctx, ty, op)

    return emit


def _neg(ty: Ty) -> _Emit:
    def emit(fn: frame.Fn, ctx: EmitCtx) -> None:
        arith.lower_neg(fn, ctx, ty)

    return emit


def _unbox(ty: Ty) -> _Emit:
    def emit(fn: frame.Fn, ctx: EmitCtx) -> None:
        arith.unbox(fn, ctx, ty)

    return emit


def _rebox(ty: Ty) -> _Emit:
    def emit(fn: frame.Fn, ctx: EmitCtx) -> None:
        arith.rebox(fn, ctx, ty)

    return emit


def _round_trip(ty: Ty) -> _Emit:
    def emit(fn: frame.Fn, ctx: EmitCtx) -> None:
        arith.rebox(fn, ctx, ty)
        arith.unbox(fn, ctx, ty)

    return emit


# `lru_cache` on `_inline_wasm` keys on the emitter object, so build each one
# ONCE here rather than per call site.
_EMIT_BINARY = {
    (ty, op): _binary(ty, op) for ty in (Ty.U32, Ty.I32, Ty.U64, Ty.I64) for op in BinaryOp
}
_EMIT_NEG = {ty: _neg(ty) for ty in (Ty.U32, Ty.I32, Ty.U64, Ty.I64)}
_EMIT_UNBOX = {ty: _unbox(ty) for ty in (Ty.U32, Ty.I32, Ty.U64, Ty.I64, Ty.Timepoint, Ty.Duration)}
_EMIT_REBOX = {ty: _rebox(ty) for ty in (Ty.U32, Ty.I32, Ty.U64, Ty.I64)}
_ROUND_TRIP = {ty: _round_trip(ty) for ty in (Ty.U32, Ty.I32, Ty.U64, Ty.I64)}


# ===========================================================================
# EmitCtx: import order, part linking, and the defined-index space (review B1)
# ===========================================================================


def test_host_import_name_registers_first_use_order() -> None:
    ctx = EmitCtx(n_module_functions=0, memory=Memory())
    assert ctx.host_import_name("obj_to_u64") == "obj_to_u64"
    assert ctx.host_import_name("fail_with_error") == "fail_with_error"
    # A repeat use does NOT move the name: first use fixes the order, and that
    # order IS the import section's.
    assert ctx.host_import_name("obj_to_u64") == "obj_to_u64"
    assert ctx.import_order == ("obj_to_u64", "fail_with_error")


def test_host_import_name_refuses_a_name_the_pin_does_not_have() -> None:
    ctx = EmitCtx(n_module_functions=0, memory=Memory())
    with pytest.raises(frame.EmitError, match="not a host function"):
        ctx.host_import_name("obj_to_u65")


def test_ensure_part_builds_each_part_once() -> None:
    ctx = EmitCtx(n_module_functions=7, memory=Memory())
    first = ctx.ensure_part("u64_add")
    assert first == 7  # straight after the module's own seven functions
    assert ctx.ensure_part("u64_add") == 7
    assert ctx.ensure_part("i64_add") == 8
    assert ctx.parts_linked == frozenset({"u64_add", "i64_add"})
    assert [p.name for p in ctx.parts] == ["u64_add", "i64_add"]
    assert [p.defidx for p in ctx.parts] == [7, 8]


def test_ensure_part_refuses_an_unknown_part() -> None:
    ctx = EmitCtx(n_module_functions=0, memory=Memory())
    with pytest.raises(frame.EmitError, match="no runtime part named"):
        ctx.ensure_part("u64_pow")


# --- re-entrancy: a part whose own body links another part -------------------
# Task 6's 128-bit parts are the first nesters (`u128_mul` links `mul64_wide`,
# `u128_mod` links `u128_floordiv`), so the three states a nested build can be
# in are pinned here with synthetic builders rather than only through them.


def _outer_calling(inner: str) -> Callable[[EmitCtx], frame.Fn]:
    """A builder whose body links `inner` and calls it -- the nesting shape."""

    def build(ctx: EmitCtx) -> frame.Fn:
        fn = frame.Fn("outer", 2, 0, ("i64",))
        fn.local_get(0)
        fn.local_get(1)
        fn.call_defined(ctx.ensure_part(inner), 2, ("i64",))
        fn.ret()
        return fn

    return build


def test_a_part_may_link_another_part_mid_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(arith.PART_BUILDERS, "outer", _outer_calling("u64_add"))
    ctx = EmitCtx(n_module_functions=1, memory=Memory())
    assert ctx.ensure_part("outer") == 1
    # The OUTER part reserved its index first, so the inner one lands after it
    # even though the inner body was finished first.
    assert [(p.name, p.defidx) for p in ctx.parts] == [("outer", 1), ("u64_add", 2)]


def test_a_part_that_links_itself_is_a_compiler_bug(monkeypatch: pytest.MonkeyPatch) -> None:
    # C12 rejects recursion at tier 1; a part that reached itself would not
    # terminate, so the reserved-but-not-yet-built state is the detection.
    monkeypatch.setitem(arith.PART_BUILDERS, "outer", _outer_calling("outer"))
    ctx = EmitCtx(n_module_functions=0, memory=Memory())
    with pytest.raises(frame.EmitError, match="links itself"):
        ctx.ensure_part("outer")


def test_two_parts_that_link_each_other_are_a_compiler_bug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(arith.PART_BUILDERS, "outer", _outer_calling("other"))
    monkeypatch.setitem(arith.PART_BUILDERS, "other", _outer_calling("outer"))
    ctx = EmitCtx(n_module_functions=0, memory=Memory())
    with pytest.raises(frame.EmitError, match="links itself"):
        ctx.ensure_part("outer")


def test_parts_read_mid_build_says_so_instead_of_raising_keyerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `_part_order` gains a name before its builder runs, so `parts` has to
    # answer for a half-built list. A `KeyError` naming a part is a baffling
    # way to learn that; an `EmitError` naming the state is not.
    seen: list[str] = []

    def build(ctx: EmitCtx) -> frame.Fn:
        with pytest.raises(frame.EmitError, match="still being built") as caught:
            _ = ctx.parts
        seen.append(str(caught.value))
        fn = frame.Fn("outer", 0, 0, ("i64",))
        fn.i64_const(0)
        fn.ret()
        return fn

    monkeypatch.setitem(arith.PART_BUILDERS, "outer", build)
    ctx = EmitCtx(n_module_functions=0, memory=Memory())
    ctx.ensure_part("outer")
    assert len(seen) == 1
    assert "outer" in seen[0]
    # And once the builder has returned, the same read succeeds.
    assert [p.name for p in ctx.parts] == ["outer"]


def test_every_ratified_part_builds_and_validates() -> None:
    # Building the whole inventory into one module also proves the parts do not
    # collide in the index space and that every import they name is bindable --
    # which is why the 128-bit bindings are merged in here rather than the
    # 128-bit parts being excluded: an unbindable import is exactly the kind of
    # break this test exists to catch.
    names = tuple(sorted(arith.PART_BUILDERS))
    store = _ObjectStore()
    wide = i256.Wide256Host()
    host = engine.MiniHost(_parts_wasm(names), imports=store.bridges() | wide.bindings())
    assert host.invoke("u64_add", 2, 3) == 5


def test_the_inventory_covers_every_name_the_frontend_can_ask_for_at_64_bits() -> None:
    # The narrow half of Task 13's `runtime_parts_needed <= parts D links`,
    # pinned here because ruling E3 renamed BOTH sides in one task: a typo in
    # either `_collect_runtime_parts` or `PART_BUILDERS` would otherwise only
    # surface as an `EmitError` in whichever contract happened to use that
    # operator first.
    wanted = {f"{prefix}_{op.name.lower()}" for prefix in ("u64", "i64") for op in BinaryOp}
    assert wanted <= set(arith.PART_BUILDERS)
    # And nothing 32-bit or `neg`-shaped crept back in at a native width.
    assert not {n for n in arith.PART_BUILDERS if n.startswith(("u32_", "i32_"))}
    assert "u64_neg" not in arith.PART_BUILDERS
    assert "i64_neg" not in arith.PART_BUILDERS
    assert "overflow_check" not in arith.PART_BUILDERS


def test_ensure_part_indices_survive_a_real_call() -> None:
    # Review B1's bug class: a call to the WRONG function of the same type
    # validates perfectly, so this asserts a returned VALUE. The probe is
    # defined function 0 and calls `u64_sub` -- if `ensure_part`'s defidx were
    # off by one it would land on `u64_add` and return 15, not 5.
    def emit(fn: frame.Fn, ctx: EmitCtx) -> None:
        ctx.ensure_part("u64_add")
        fn.call_defined(ctx.ensure_part("u64_sub"), 2, ("i64",))

    host, _store = _inline_host(emit, 2)
    assert host.invoke("probe", 10, 5) == 5


def test_a_part_body_names_its_own_imports() -> None:
    ctx = EmitCtx(n_module_functions=0, memory=Memory())
    ctx.ensure_part("unbox_i64")
    # `unbox_i64` reaches `obj_to_i64` and nothing else; no overflow is
    # possible in an unbox, so it must not have pulled in `fail_with_error`.
    assert ctx.import_order == ("obj_to_i64",)


# ===========================================================================
# 64-bit checked arithmetic: the A4 edge matrix, per width and operator
# ===========================================================================

U64_VECTORS: list[tuple[str, int, int, object]] = [
    # --- u64_add: overflow iff the sum wrapped below the left operand -------
    ("u64_add", 0, 0, 0),
    ("u64_add", 1, 2, 3),
    ("u64_add", U64_MAX, 0, U64_MAX),
    ("u64_add", 0, U64_MAX, U64_MAX),
    ("u64_add", U64_MAX, 1, OVERFLOW),
    ("u64_add", 1, U64_MAX, OVERFLOW),
    ("u64_add", 1 << 63, 1 << 63, OVERFLOW),
    ("u64_add", U64_MAX, U64_MAX, OVERFLOW),
    # Straddles the small/object boundary: nothing about it is special to a
    # part that works on RAW words, which is the point of asserting it.
    ("u64_add", val.MAX_SMALL_U64, 1, val.MAX_SMALL_U64 + 1),
    # --- u64_sub: overflow iff lhs < rhs ------------------------------------
    ("u64_sub", 0, 0, 0),
    ("u64_sub", 5, 3, 2),
    ("u64_sub", 3, 5, OVERFLOW),
    ("u64_sub", 0, 1, OVERFLOW),
    ("u64_sub", U64_MAX, U64_MAX, 0),
    ("u64_sub", U64_MAX, 0, U64_MAX),
    ("u64_sub", val.MAX_SMALL_U64 + 1, 1, val.MAX_SMALL_U64),
    # --- u64_mul (M7): r = a*b; overflow iff a != 0 and div_u(r, a) != b ----
    ("u64_mul", 0, 0, 0),
    ("u64_mul", 0, U64_MAX, 0),
    ("u64_mul", U64_MAX, 0, 0),
    ("u64_mul", 1, U64_MAX, U64_MAX),
    ("u64_mul", U64_MAX, 1, U64_MAX),
    ("u64_mul", 3, 5, 15),
    ("u64_mul", (1 << 32) - 1, (1 << 32) - 1, ((1 << 32) - 1) ** 2),
    ("u64_mul", 1 << 32, 1 << 32, OVERFLOW),
    ("u64_mul", 2, 1 << 63, OVERFLOW),
    ("u64_mul", U64_MAX, 2, OVERFLOW),
    ("u64_mul", U64_MAX, U64_MAX, OVERFLOW),
    # --- u64_floordiv / u64_mod: no overflow exists in the unsigned domain --
    ("u64_floordiv", 7, 2, 3),
    ("u64_floordiv", 0, 5, 0),
    ("u64_floordiv", U64_MAX, 1, U64_MAX),
    ("u64_floordiv", U64_MAX, U64_MAX, 1),
    ("u64_floordiv", 1 << 63, 2, 1 << 62),
    ("u64_mod", 7, 2, 1),
    ("u64_mod", 0, 5, 0),
    ("u64_mod", 5, 5, 0),
    ("u64_mod", U64_MAX, 2, 1),
]

I64_VECTORS: list[tuple[str, int, int, object]] = [
    # --- i64_add: sign analysis --------------------------------------------
    ("i64_add", 0, 0, 0),
    ("i64_add", 1, -1, 0),
    ("i64_add", I64_MAX, 0, I64_MAX),
    ("i64_add", I64_MIN, 0, I64_MIN),
    ("i64_add", I64_MAX, I64_MIN, -1),
    ("i64_add", I64_MAX, 1, OVERFLOW),
    ("i64_add", 1, I64_MAX, OVERFLOW),
    ("i64_add", I64_MIN, -1, OVERFLOW),
    ("i64_add", -1, I64_MIN, OVERFLOW),
    ("i64_add", val.MIN_SMALL_I64, -1, val.MIN_SMALL_I64 - 1),
    # --- i64_sub ------------------------------------------------------------
    ("i64_sub", 0, 0, 0),
    ("i64_sub", 5, 3, 2),
    ("i64_sub", 3, 5, -2),
    ("i64_sub", I64_MIN, I64_MIN, 0),
    ("i64_sub", I64_MIN, -1, I64_MIN + 1),
    ("i64_sub", -1, I64_MAX, I64_MIN),
    ("i64_sub", 0, I64_MIN, OVERFLOW),
    ("i64_sub", I64_MIN, 1, OVERFLOW),
    ("i64_sub", I64_MAX, -1, OVERFLOW),
    # --- i64_mul (M7): the MIN operand is the whole reason this is hard -----
    ("i64_mul", 0, 0, 0),
    ("i64_mul", I64_MIN, 0, 0),
    ("i64_mul", 0, I64_MIN, 0),
    ("i64_mul", I64_MIN, 1, I64_MIN),
    ("i64_mul", 1, I64_MIN, I64_MIN),
    ("i64_mul", I64_MIN, -1, OVERFLOW),
    ("i64_mul", -1, I64_MIN, OVERFLOW),
    ("i64_mul", I64_MIN, 2, OVERFLOW),
    ("i64_mul", I64_MAX, 1, I64_MAX),
    ("i64_mul", I64_MAX, -1, -I64_MAX),
    ("i64_mul", I64_MAX, 2, OVERFLOW),
    ("i64_mul", -3, 4, -12),
    ("i64_mul", 3, -4, -12),
    ("i64_mul", -3, -4, 12),
    ("i64_mul", 1 << 31, 1 << 31, 1 << 62),
    ("i64_mul", 1 << 32, 1 << 32, OVERFLOW),
    ("i64_mul", -(1 << 32), 1 << 32, OVERFLOW),
    # Exactly representable only because the result is NEGATIVE: the bound a
    # signed multiply checks against depends on the sign of its own result.
    ("i64_mul", 1 << 62, -2, I64_MIN),
    ("i64_mul", 1 << 62, 2, OVERFLOW),
    # --- i64_floordiv: TRUNCATES TOWARD ZERO (A4), and MIN/-1 is the one
    # case wasm would TRAP on, so the part must reach the error first -------
    ("i64_floordiv", 7, 2, 3),
    ("i64_floordiv", -7, 2, -3),
    ("i64_floordiv", 7, -2, -3),
    ("i64_floordiv", -7, -2, 3),
    ("i64_floordiv", 0, 5, 0),
    ("i64_floordiv", I64_MIN, 1, I64_MIN),
    ("i64_floordiv", I64_MIN, 2, -(1 << 62)),
    ("i64_floordiv", I64_MAX, -1, -I64_MAX),
    ("i64_floordiv", I64_MIN, -1, OVERFLOW),
    # --- i64_mod: takes the DIVIDEND's sign (A4); MIN % -1 == 0 ------------
    ("i64_mod", 7, 2, 1),
    ("i64_mod", -7, 2, -1),
    ("i64_mod", 7, -2, 1),
    ("i64_mod", -7, -2, -1),
    ("i64_mod", 5, 5, 0),
    ("i64_mod", I64_MIN, 1, 0),
    ("i64_mod", I64_MAX, -2, 1),
    ("i64_mod", I64_MIN, -1, 0),
]


@pytest.mark.parametrize(("part", "a", "b", "want"), U64_VECTORS + I64_VECTORS)
def test_sixty_four_bit_golden_vectors(part: str, a: int, b: int, want: object) -> None:
    _check_part(part, (a, b), want)


@pytest.mark.parametrize("part", ["u64_floordiv", "u64_mod", "i64_floordiv", "i64_mod"])
def test_division_by_zero_is_left_to_the_wasm_trap(part: str) -> None:
    # A4: `//0` and `%0` are NOT routed to `fail_with_error`; they are the
    # host's own trap, which A10's trap-class mapping already records. A part
    # that "helpfully" turned them into ArithmeticOverflow would diverge from
    # tier 1's `ZeroDivisionError`.
    host, _store = _part_host(part)
    with pytest.raises(wasmtime.Trap):
        host.invoke(part, 1, 0)


# ===========================================================================
# 32-bit checked arithmetic: INLINE, no part (S25 break-even)
# ===========================================================================

U32_VECTORS: list[tuple[BinaryOp, int, int, object]] = [
    (BinaryOp.ADD, 0, 0, 0),
    (BinaryOp.ADD, 1, 2, 3),
    (BinaryOp.ADD, U32_MAX, 0, U32_MAX),
    (BinaryOp.ADD, U32_MAX, 1, OVERFLOW),
    (BinaryOp.ADD, 1 << 31, 1 << 31, OVERFLOW),
    (BinaryOp.SUB, 5, 3, 2),
    (BinaryOp.SUB, 0, 0, 0),
    (BinaryOp.SUB, U32_MAX, U32_MAX, 0),
    (BinaryOp.SUB, 3, 5, OVERFLOW),
    (BinaryOp.SUB, 0, 1, OVERFLOW),
    (BinaryOp.MUL, 0, U32_MAX, 0),
    (BinaryOp.MUL, U32_MAX, 1, U32_MAX),
    (BinaryOp.MUL, (1 << 16) - 1, (1 << 16) - 1, ((1 << 16) - 1) ** 2),
    (BinaryOp.MUL, 1 << 16, 1 << 16, OVERFLOW),
    (BinaryOp.MUL, U32_MAX, 2, OVERFLOW),
    (BinaryOp.FLOORDIV, 7, 2, 3),
    (BinaryOp.FLOORDIV, U32_MAX, U32_MAX, 1),
    (BinaryOp.FLOORDIV, 0, 5, 0),
    (BinaryOp.MOD, 7, 2, 1),
    (BinaryOp.MOD, U32_MAX, 2, 1),
]

I32_VECTORS: list[tuple[BinaryOp, int, int, object]] = [
    (BinaryOp.ADD, -1, 1, 0),
    (BinaryOp.ADD, I32_MAX, -1, I32_MAX - 1),
    (BinaryOp.ADD, I32_MAX, 1, OVERFLOW),
    (BinaryOp.ADD, I32_MIN, -1, OVERFLOW),
    (BinaryOp.SUB, I32_MIN, -1, I32_MIN + 1),
    (BinaryOp.SUB, I32_MIN, 1, OVERFLOW),
    (BinaryOp.SUB, I32_MAX, -1, OVERFLOW),
    (BinaryOp.SUB, 0, I32_MIN, OVERFLOW),
    (BinaryOp.MUL, -3, 4, -12),
    (BinaryOp.MUL, I32_MIN, 1, I32_MIN),
    (BinaryOp.MUL, I32_MIN, -1, OVERFLOW),
    (BinaryOp.MUL, 1 << 15, 1 << 15, 1 << 30),
    (BinaryOp.MUL, 1 << 16, 1 << 16, OVERFLOW),
    (BinaryOp.FLOORDIV, -7, 2, -3),
    (BinaryOp.FLOORDIV, 7, -2, -3),
    (BinaryOp.FLOORDIV, I32_MIN, 1, I32_MIN),
    # The 32-bit twin of the trap A4 names: `i64.div_s` does not trap here
    # (these are i64 words), so only the range check catches it.
    (BinaryOp.FLOORDIV, I32_MIN, -1, OVERFLOW),
    (BinaryOp.MOD, -7, 2, -1),
    (BinaryOp.MOD, 7, -2, 1),
    (BinaryOp.MOD, I32_MIN, -1, 0),
]


@pytest.mark.parametrize(("op", "a", "b", "want"), U32_VECTORS)
def test_u32_inline_golden_vectors(op: BinaryOp, a: int, b: int, want: object) -> None:
    _check_inline(_EMIT_BINARY[Ty.U32, op], (a, b), want)


@pytest.mark.parametrize(("op", "a", "b", "want"), I32_VECTORS)
def test_i32_inline_golden_vectors(op: BinaryOp, a: int, b: int, want: object) -> None:
    _check_inline(_EMIT_BINARY[Ty.I32, op], (a, b), want)


@pytest.mark.parametrize("ty", [Ty.U32, Ty.I32])
@pytest.mark.parametrize("op", [BinaryOp.FLOORDIV, BinaryOp.MOD])
def test_thirty_two_bit_division_by_zero_is_left_to_the_wasm_trap(ty: Ty, op: BinaryOp) -> None:
    # The 32-bit twin of `test_division_by_zero_is_left_to_the_wasm_trap`: the
    # inline path has no zero test either, so `//0` and `%0` are the host's own
    # trap and A10's trap-class mapping covers them. Pinned per width and
    # operator because "helpfully" routing one of the four to
    # `fail_with_error` would diverge from tier 1's `ZeroDivisionError` in
    # exactly one cell of the matrix.
    host, _store = _inline_host(_EMIT_BINARY[ty, op], 2)
    with pytest.raises(wasmtime.Trap):
        host.invoke("probe", 1, 0)


def test_thirty_two_bit_binary_links_no_part() -> None:
    ctx = EmitCtx(n_module_functions=1, memory=Memory())
    fn = frame.Fn("probe", 2, 0, ("i64",))
    fn.local_get(0)
    fn.local_get(1)
    arith.lower_binary(fn, ctx, Ty.U32, BinaryOp.ADD)
    fn.ret()
    fn.finish()
    assert ctx.parts_linked == frozenset()


# ===========================================================================
# Negation: inline at 32 and 64 bits (review M6)
# ===========================================================================


@pytest.mark.parametrize(
    ("ty", "value", "want"),
    [
        # Unsigned: there is no negative to produce, so anything nonzero is the
        # overflow and zero is the only legal input.
        (Ty.U64, 0, 0),
        (Ty.U64, 1, OVERFLOW),
        (Ty.U64, U64_MAX, OVERFLOW),
        (Ty.U64, val.MAX_SMALL_U64, OVERFLOW),
        (Ty.U32, 0, 0),
        (Ty.U32, 1, OVERFLOW),
        (Ty.U32, U32_MAX, OVERFLOW),
        # Signed: MIN has no positive twin, everything else is `0 - value`.
        (Ty.I64, 0, 0),
        (Ty.I64, 1, -1),
        (Ty.I64, -1, 1),
        (Ty.I64, I64_MAX, -I64_MAX),
        (Ty.I64, I64_MIN + 1, I64_MAX),
        (Ty.I64, I64_MIN, OVERFLOW),
        (Ty.I32, 0, 0),
        (Ty.I32, 5, -5),
        (Ty.I32, -5, 5),
        (Ty.I32, I32_MAX, -I32_MAX),
        (Ty.I32, I32_MIN + 1, I32_MAX),
        (Ty.I32, I32_MIN, OVERFLOW),
    ],
)
def test_neg_is_inline_and_checked(ty: Ty, value: int, want: object) -> None:
    _check_inline(_EMIT_NEG[ty], (value,), want)


def test_neg_links_no_part_at_any_native_width() -> None:
    for ty in (Ty.U32, Ty.I32, Ty.U64, Ty.I64):
        ctx = EmitCtx(n_module_functions=1, memory=Memory())
        fn = frame.Fn("probe", 1, 0, ("i64",))
        fn.local_get(0)
        arith.lower_neg(fn, ctx, ty)
        fn.ret()
        fn.finish()
        assert ctx.parts_linked == frozenset(), ty


# ===========================================================================
# Unboxing and reboxing: the B10 recipe and the F.1.3 anti-pattern
# ===========================================================================


@pytest.mark.parametrize(
    "value",
    [
        0,
        1,
        255,
        1 << 40,
        val.MAX_SMALL_U64 - 1,
        val.MAX_SMALL_U64,
    ],
)
def test_unbox_u64_small_form(value: int) -> None:
    _check_inline(_EMIT_UNBOX[Ty.U64], (val.pack_small_u64(value, val.TAG_U64_SMALL),), value)


@pytest.mark.parametrize("value", [0, 1, val.MAX_SMALL_U64 + 1, U64_MAX])
def test_unbox_u64_object_form(value: int) -> None:
    host, store = _inline_host(_EMIT_UNBOX[Ty.U64], 1)
    handle = store.handle(value, val.TAG_U64_OBJECT)
    assert host.invoke("probe", handle) == value


@pytest.mark.parametrize(
    "value",
    [
        # Review B10's named regressions: under the superseded "shl 8 then
        # shr_s 8" recipe `I64(-1)` decodes to -249, not -1.
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
def test_unbox_i64_small_form(value: int) -> None:
    _check_inline(_EMIT_UNBOX[Ty.I64], (val.pack_small_i64(value, val.TAG_I64_SMALL),), value)


@pytest.mark.parametrize("value", [0, -1, I64_MIN, I64_MAX, val.MIN_SMALL_I64 - 1])
def test_unbox_i64_object_form(value: int) -> None:
    host, store = _inline_host(_EMIT_UNBOX[Ty.I64], 1)
    handle = store.handle(value, val.TAG_I64_OBJECT)
    assert host.invoke("probe", handle) == val.as_u64(value)


@pytest.mark.parametrize("value", [0, 1, val.MAX_SMALL_U64])
def test_rebox_u64_takes_the_small_form_when_it_fits(value: int) -> None:
    _check_inline(_EMIT_REBOX[Ty.U64], (value,), val.pack_small_u64(value, val.TAG_U64_SMALL))


@pytest.mark.parametrize("value", [val.MAX_SMALL_U64 + 1, U64_MAX])
def test_rebox_u64_takes_the_object_form_when_it_does_not(value: int) -> None:
    host, store = _inline_host(_EMIT_REBOX[Ty.U64], 1)
    handle = host.invoke("probe", value)
    assert handle is not None
    assert val.tag_of(handle) == val.TAG_U64_OBJECT
    assert store.raw(handle) == value


@pytest.mark.parametrize("value", [0, 1, -1, val.MIN_SMALL_I64, val.MAX_SMALL_I64])
def test_rebox_i64_takes_the_small_form_when_it_fits(value: int) -> None:
    _check_inline(_EMIT_REBOX[Ty.I64], (value,), val.pack_small_i64(value, val.TAG_I64_SMALL))


@pytest.mark.parametrize("value", [val.MAX_SMALL_I64 + 1, val.MIN_SMALL_I64 - 1, I64_MIN, I64_MAX])
def test_rebox_i64_takes_the_object_form_when_it_does_not(value: int) -> None:
    host, store = _inline_host(_EMIT_REBOX[Ty.I64], 1)
    handle = host.invoke("probe", val.as_u64(value))
    assert handle is not None
    assert val.tag_of(handle) == val.TAG_I64_OBJECT
    assert store.raw(handle) == val.as_u64(value)


@pytest.mark.parametrize(
    ("ty", "tag", "object_tag"),
    [
        (Ty.Timepoint, val.TAG_TIMEPOINT_SMALL, val.TAG_TIMEPOINT_OBJECT),
        (Ty.Duration, val.TAG_DURATION_SMALL, val.TAG_DURATION_OBJECT),
    ],
)
def test_unbox_timepoint_and_duration(ty: Ty, tag: int, object_tag: int) -> None:
    # D4: these have no arithmetic at all, so they are unbox-only -- a compare
    # is the one thing that needs the raw word. Both bodies are UNSIGNED.
    for value in (0, 1, val.MAX_SMALL_U64):
        _check_inline(_EMIT_UNBOX[ty], (val.pack_small_u64(value, tag),), value)
    host, store = _inline_host(_EMIT_UNBOX[ty], 1)
    handle = store.handle(val.MAX_SMALL_U64 + 1, object_tag)
    assert host.invoke("probe", handle) == val.MAX_SMALL_U64 + 1


@pytest.mark.parametrize("ty", [Ty.Timepoint, Ty.Duration])
def test_rebox_refuses_the_compare_only_types(ty: Ty) -> None:
    ctx = EmitCtx(n_module_functions=1, memory=Memory())
    fn = frame.Fn("probe", 1, 0, ("i64",))
    fn.local_get(0)
    with pytest.raises(frame.EmitError, match="no boxing lowering"):
        arith.rebox(fn, ctx, ty)


@pytest.mark.parametrize("value", [0, 1, 255, 1 << 31, U32_MAX])
def test_unbox_and_rebox_u32_round_trip(value: int) -> None:
    _check_inline(_EMIT_UNBOX[Ty.U32], (val.pack_u32val(value),), value)
    _check_inline(_EMIT_REBOX[Ty.U32], (value,), val.pack_u32val(value))


@pytest.mark.parametrize("value", [0, 1, -1, 5, -5, I32_MIN, I32_MAX])
def test_unbox_and_rebox_i32_round_trip(value: int) -> None:
    # The I32 unbox is `shr_u 32` then `i64.extend32_s`: the raw word an I32
    # lowering works on is SIGN-extended, or every downstream signed compare
    # would read a negative I32 as a huge positive.
    _check_inline(_EMIT_UNBOX[Ty.I32], (val.pack_i32val(value),), value)
    _check_inline(_EMIT_REBOX[Ty.I32], (value,), val.pack_i32val(value))


@pytest.mark.parametrize("value", [1 << 32, U32_MAX + 1, U64_MAX])
def test_rebox_u32_range_checks_instead_of_wrapping(value: int) -> None:
    # F.1.3, the named anti-pattern: the spike repacked with a bare `shl 32`,
    # so an out-of-range value silently became a perfectly valid U32 Val.
    _check_inline(_EMIT_REBOX[Ty.U32], (value,), OVERFLOW)


@pytest.mark.parametrize("value", [1 << 31, I32_MIN - 1, I64_MAX, I64_MIN])
def test_rebox_i32_range_checks_instead_of_wrapping(value: int) -> None:
    _check_inline(_EMIT_REBOX[Ty.I32], (value,), OVERFLOW)


def test_wide_types_are_not_this_task() -> None:
    ctx = EmitCtx(n_module_functions=1, memory=Memory())
    fn = frame.Fn("probe", 2, 0, ("i64",))
    fn.local_get(0)
    fn.local_get(1)
    with pytest.raises(frame.EmitError, match="128"):
        arith.lower_binary(fn, ctx, Ty.U128, BinaryOp.ADD)


# ===========================================================================
# Property fuzz: every op against Python-with-A4's-rules
# ===========================================================================

_FUZZ = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _fuzz_binary(ty: Ty, op: BinaryOp, part: str | None, a: int, b: int, lo: int, hi: int) -> None:
    if op in (BinaryOp.FLOORDIV, BinaryOp.MOD) and b == 0:
        return  # A4 leaves this to the wasm trap; covered by its own test
    exact = a4_apply(op, a, b)
    want: object = exact if lo <= exact <= hi else OVERFLOW
    if part is not None:
        _check_part(part, (a, b), want)
    else:
        _check_inline(_EMIT_BINARY[ty, op], (a, b), want)


@given(
    op=st.sampled_from(list(BinaryOp)),
    a=st.integers(min_value=0, max_value=U64_MAX),
    b=st.integers(min_value=0, max_value=U64_MAX),
)
@_FUZZ
def test_u64_matches_python_under_a4(op: BinaryOp, a: int, b: int) -> None:
    _fuzz_binary(Ty.U64, op, f"u64_{op.name.lower()}", a, b, 0, U64_MAX)


@given(
    op=st.sampled_from(list(BinaryOp)),
    a=st.integers(min_value=I64_MIN, max_value=I64_MAX),
    b=st.integers(min_value=I64_MIN, max_value=I64_MAX),
)
@_FUZZ
def test_i64_matches_python_under_a4(op: BinaryOp, a: int, b: int) -> None:
    _fuzz_binary(Ty.I64, op, f"i64_{op.name.lower()}", a, b, I64_MIN, I64_MAX)


@given(
    op=st.sampled_from(list(BinaryOp)),
    a=st.integers(min_value=0, max_value=U32_MAX),
    b=st.integers(min_value=0, max_value=U32_MAX),
)
@_FUZZ
def test_u32_matches_python_under_a4(op: BinaryOp, a: int, b: int) -> None:
    _fuzz_binary(Ty.U32, op, None, a, b, 0, U32_MAX)


@given(
    op=st.sampled_from(list(BinaryOp)),
    a=st.integers(min_value=I32_MIN, max_value=I32_MAX),
    b=st.integers(min_value=I32_MIN, max_value=I32_MAX),
)
@_FUZZ
def test_i32_matches_python_under_a4(op: BinaryOp, a: int, b: int) -> None:
    _fuzz_binary(Ty.I32, op, None, a, b, I32_MIN, I32_MAX)


@given(value=st.integers(min_value=0, max_value=U64_MAX))
@_FUZZ
def test_u64_box_unbox_round_trips_over_the_full_range(value: int) -> None:
    _check_inline(_ROUND_TRIP[Ty.U64], (value,), value)


@given(value=st.integers(min_value=I64_MIN, max_value=I64_MAX))
@_FUZZ
def test_i64_box_unbox_round_trips_over_the_full_range(value: int) -> None:
    _check_inline(_ROUND_TRIP[Ty.I64], (value,), value)


@given(value=st.integers(min_value=0, max_value=U32_MAX))
@_FUZZ
def test_u32_box_unbox_round_trips_over_the_full_range(value: int) -> None:
    _check_inline(_ROUND_TRIP[Ty.U32], (value,), value)


@given(value=st.integers(min_value=I32_MIN, max_value=I32_MAX))
@_FUZZ
def test_i32_box_unbox_round_trips_over_the_full_range(value: int) -> None:
    _check_inline(_ROUND_TRIP[Ty.I32], (value,), value)
