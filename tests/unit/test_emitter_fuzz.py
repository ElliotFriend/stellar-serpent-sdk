"""IR fuzz -> the validators (D.5.2), plus the two whole-fixture budgets.

## The property (D.5.2)

**For any well-typed scalar `FuncIR`, `build_wasm` either returns a module that
two INDEPENDENT validators accept, or refuses it with one of the emitter's own
declared failures. It never hands back bytes that are not valid wasm.**

`build_wasm` runs `validate.validate_internal` on every module before returning
it (P8: "an invalid module is a compile error, never an output file"), so
re-running the same validator here would prove nothing -- a matched pair of
bugs in the encoder and the decoder would agree. The two validators used
instead are genuinely independent of this repo:

* `wasm-tools validate` (ruling E5) -- the reference implementation's own
  validator, shelled out to when it is on PATH;
* **wasmtime**, by INSTANTIATING the module under `tests/harness`'s pinned
  engine (`engine.make_config`, the chain's accepted feature set). Wasmtime
  validates on `Module(...)` and links on `instantiate(...)`, so this also
  catches an import entry whose signature disagrees with the pin -- a failure
  no byte-level validator can see.

`test_a_corrupted_module_is_rejected_by_the_independent_validators` is what
keeps that half honest: it takes a module the emitter really produced, damages
it (a truncation, and a bad wasm version word), and asserts the independent
check says NO. Without it, a bug in the checking helper -- or a `wasm-tools`
that is simply not installed together with a wasmtime call that swallowed its
error -- would make every fuzz example pass for no reason.

**The declared failures, and how narrow the list is.** `build_wasm`'s own
contract (E15/M10's split) is that exactly two failure classes reach a caller:
a `BuildLimitError` becomes a LOCATED SPT800x `CompileError`, and a bare
`EmitError` becomes a `CompilerBugError`. The property below therefore catches
only those two -- a raw `EmitError` or `BuildLimitError` escaping unmapped is a
violation of that contract and must fail the test, not be absorbed by it, and
an `IndexError` or a `KeyError` out of the emitter is a found bug exactly as
`test_frontend_fuzz.py` treats one out of the frontend.
`test_an_ir_that_breaks_a_lowering_invariant_raises_rather_than_emitting` proves
the `CompilerBugError` arm is REACHABLE, so "either validates or refuses" is not
satisfied vacuously by an emitter that never refuses.

## Why the IR is generated directly rather than through source text

`tests/unit/test_frontend_fuzz.py` already fuzzes `compile_module` over
generated SOURCE, and everything it generates is by construction inside (or
outside) the authoring subset. This fuzz targets the OTHER seam: IR shapes the
lowering must handle, reached without asking whether an author could have
written them -- nested `If`/`While` frames, a `Return` in an arm, an `IfExp`
inside a `Binary`, a `BoolOp`'s short-circuit blocks. Frames are the point:
the operand-stack-checked `Fn` builder and the block/loop label discipline are
where an emitter produces plausible-looking invalid bytes.

The generated `FuncIR` is spliced into a REAL `CompiledModule` (one of four
pre-compiled one-method templates, chosen by the generated function's return
type) so the whole public path runs: `module.assemble`'s two passes, the
literal pool, the three custom sections, `validate_internal`, and the
`BuildResult`. The template's parameter and return types MATCH the generated
function's, so the `contractspecv0` section describes the module's real
interface rather than a stale one.

## The two whole-fixture budgets

`FIXTURES` is imported from `tests/unit/test_emitter_end_to_end.py` (one list,
not two):

* **the size tripwire (§B.1)** -- every fixture's `module_size` must stay at or
  under 20% of S22's 131072-byte cap. A tripwire, not a limit: the emitter
  already enforces S22 itself (SPT8001), and this is the early warning that
  says a fixture's module grew.
* **`runtime_parts_needed <= runtime_parts_linked` (ruling E3)** -- the
  frontend's hint about which guest-runtime parts a contract needs must be a
  SUBSET of what the build actually linked. A part the frontend named and the
  emitter did not link is a call to a function that is not there.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

import pytest
import wasmtime
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from serpent.compiler.diagnostics import CompileError, Loc
from serpent.compiler.frontend import CompiledModule, compile_module
from serpent.compiler.ir import (
    Binary,
    BinaryOp,
    BoolOp,
    BoolOpKind,
    Break,
    Compare,
    CompareOp,
    Const,
    Continue,
    FuncIR,
    FuncKind,
    If,
    IfExp,
    IRExpr,
    IRStmt,
    LetLocal,
    LocalRef,
    Nop,
    ParamRef,
    Return,
    SetLocal,
    Unary,
    UnaryOp,
    While,
)
from serpent.compiler.loader import CompilerBugError
from serpent.compiler.types_ import Ty
from serpent.emitter import build_wasm
from serpent.emitter import validate as _validate
from tests.harness import engine
from tests.harness.hostfns import FullHost
from tests.unit.test_emitter_end_to_end import FIXTURES, build_fixture

# ===========================================================================
# the settings profile (mirrors test_frontend_fuzz.py's, and says where it
# deviates)
# ===========================================================================

#: Opt-in deeper campaign: `SERPENT_FUZZ_EXAMPLES=2000 uv run pytest -q -k emitter_fuzz`.
_EXAMPLES = int(os.environ.get("SERPENT_FUZZ_EXAMPLES", "100"))

#: `derandomize`/`database=None` for the same reason `test_frontend_fuzz.py`
#: gives: no run-to-run state at all, so a green suite stays green and a red one
#: stays red with the same minimal example.
#:
#: `deadline=None` is the one deliberate deviation. One example here assembles a
#: module, decodes it internally, shells out to `wasm-tools`, and instantiates it
#: under a fresh wasmtime engine -- tens of milliseconds, dominated by a
#: subprocess and a JIT, both of which vary by an order of magnitude on a loaded
#: machine. A per-example wall-clock deadline over that is a flake generator, and
#: it would be measuring the toolchain rather than the emitter. The example
#: BUDGET is what keeps the suite bounded instead.
CI_FUZZ: Final = settings(
    derandomize=True,
    database=None,
    max_examples=_EXAMPLES,
    deadline=None,
    suppress_health_check=(HealthCheck.too_slow, HealthCheck.data_too_large),
)

LOC = Loc.whole_file("fuzz/generated.py")

# ===========================================================================
# the four templates: a real CompiledModule per return type
# ===========================================================================

#: The scalar types the generator works over (the brief's set): the two true
#: 32-bit types and the two natively-64-bit ones. `U128`/`I128` are excluded on
#: purpose -- their arithmetic is a guest-runtime limb sequence with its own
#: dedicated tests (`test_emitter_arith128.py`), and including them here would
#: make the generator mostly exercise those parts rather than the frames.
TYPES: Final[tuple[Ty, ...]] = (Ty.U32, Ty.I32, Ty.U64, Ty.I64)

#: `Ty` -> (min, max) for a `Const` of that type. `Const.py_value` is
#: bounds-checked against its `ty` at compile time (S3), and `layout`/`lower`
#: assume that has already happened, so a generator that drew an out-of-range
#: literal would be testing an input the emitter is documented never to see.
_RANGES: Final[dict[Ty, tuple[int, int]]] = {
    Ty.U32: (0, 2**32 - 1),
    Ty.I32: (-(2**31), 2**31 - 1),
    Ty.U64: (0, 2**64 - 1),
    Ty.I64: (-(2**63), 2**63 - 1),
}

_TEMPLATE = """\
from serpent import {name}, Env, contract


@contract
class Fuzz:
    def go(self, env: Env, a: {name}) -> {name}:
        return a
"""


def _template_for(ty: Ty) -> CompiledModule:
    return compile_module(_TEMPLATE.format(name=ty.render()), "fuzz/generated.py")


#: Compiled once at import: compiling four tiny modules per example would make
#: the frontend most of the runtime, and none of it is what is under test.
TEMPLATES: Final[dict[Ty, CompiledModule]] = {ty: _template_for(ty) for ty in TYPES}


def splice(fn: FuncIR) -> CompiledModule:
    """`fn` in place of the template's `go`, as a whole `CompiledModule`.

    The template is chosen by `fn.ret`, and every generated function keeps the
    template's signature (`go(a: <ret>) -> <ret>`), so `spec_inputs` still
    describes the module's actual interface -- the fuzz is not quietly emitting
    a `contractspecv0` section that contradicts the exports.

    Everything else the emitter reads is re-derived from the IR by `assemble`
    itself: which host functions to import, which runtime parts to link, whether
    a memory page is needed (ruling E10). `literals` is the one exception, and
    the template's is empty and stays correct: the generator emits no `Const` of
    a pooled type (`Symbol`/`String`/`Bytes`/`Address`), only scalars.
    """
    base = TEMPLATES[fn.ret]
    contract = base.ir.contract
    assert contract is not None
    return dataclasses.replace(
        base,
        ir=dataclasses.replace(base.ir, contract=dataclasses.replace(contract, methods=(fn,))),
        functions=(fn,),
    )


# ===========================================================================
# the generator
# ===========================================================================

_Draw = Callable[[st.SearchStrategy[object]], object]

#: Expression nesting depth. Three is enough to put an `IfExp` inside a
#: `Binary` inside a `Binary` -- i.e. a block with a result type inside an
#: arithmetic sequence, which is the shape that stresses the operand-stack
#: checker -- while keeping an example's assemble-and-validate cost bounded.
_MAX_EXPR_DEPTH: Final = 3
_MAX_STMT_DEPTH: Final = 2


class _Gen:
    """One example's draw context: the parameter's type and the bound locals.

    A class rather than threaded arguments because the recursion needs both
    plus `draw`, and because `bound` GROWS as `LetLocal`s are emitted -- a
    generator that let an expression read a local before its `LetLocal` would
    still produce valid wasm (wasm locals are zero-initialized), but it would
    not be the well-typed IR the frontend is documented to produce.
    """

    def __init__(self, draw: _Draw, param_ty: Ty) -> None:
        self.draw = draw
        self.param_ty = param_ty
        self.bound: dict[int, Ty] = {}

    # --- expressions ------------------------------------------------------

    def _pick(self, options: Sequence[str]) -> str:
        chosen = self.draw(st.sampled_from(list(options)))
        assert isinstance(chosen, str)
        return chosen

    def _int(self, low: int, high: int) -> int:
        drawn = self.draw(st.integers(min_value=low, max_value=high))
        assert isinstance(drawn, int)
        return drawn

    def const(self, ty: Ty) -> Const:
        low, high = _RANGES[ty]
        # The boundaries are drawn explicitly as well as uniformly: a uniform
        # draw over 2**64 values essentially never produces 0, 1, or a MAX, and
        # those are the encodings (an sleb128 of a negative minimum, a u64 whose
        # high bit is set) most likely to be encoded wrongly.
        value = self.draw(
            st.one_of(
                st.sampled_from([low, high, 0, 1] if low <= 0 else [low, high, 1]),
                st.integers(min_value=low, max_value=high),
            )
        )
        assert isinstance(value, int)
        return Const(loc=LOC, ty=ty, py_value=value)

    def slots_of(self, ty: Ty) -> list[int]:
        return [slot for slot, slot_ty in self.bound.items() if slot_ty == ty]

    def expr(self, ty: Ty, depth: int) -> IRExpr:
        """One well-typed expression of type `ty`."""
        options = ["const"]
        if ty == self.param_ty:
            options.append("param")
        if self.slots_of(ty):
            options.append("local")
        if depth > 0:
            options += ["binary", "neg", "ifexp"]
        kind = self._pick(options)
        if kind == "const":
            return self.const(ty)
        if kind == "param":
            return ParamRef(loc=LOC, ty=ty, index=0, name="a")
        if kind == "local":
            slot = self.draw(st.sampled_from(self.slots_of(ty)))
            assert isinstance(slot, int)
            return LocalRef(loc=LOC, ty=ty, slot=slot, name=f"v{slot}")
        if kind == "binary":
            op = self.draw(st.sampled_from(list(BinaryOp)))
            assert isinstance(op, BinaryOp)
            return Binary(
                loc=LOC,
                ty=ty,
                op=op,
                lhs=self.expr(ty, depth - 1),
                rhs=self.expr(ty, depth - 1),
            )
        if kind == "neg":
            return Unary(loc=LOC, ty=ty, op=UnaryOp.NEG, operand=self.expr(ty, depth - 1))
        return IfExp(
            loc=LOC,
            ty=ty,
            cond=self.bool_expr(depth - 1),
            then=self.expr(ty, depth - 1),
            orelse=self.expr(ty, depth - 1),
        )

    def bool_expr(self, depth: int) -> IRExpr:
        """One well-typed `Ty.Bool` expression.

        `via_obj_cmp=False` on every `Compare`: the operands are always one of
        the four scalar types, all of whose comparisons are direct (T5's
        `obj_cmp` routing is for HOST_OBJECT types and for `Symbol`, none of
        which this generator produces). Setting it `True` here would be
        generating IR the frontend never emits for these types.
        """
        options = ["compare"]
        if depth > 0:
            options += ["not", "boolop"]
        kind = self._pick(options)
        if kind == "compare":
            ty = self.draw(st.sampled_from(list(TYPES)))
            assert isinstance(ty, Ty)
            op = self.draw(st.sampled_from(list(CompareOp)))
            assert isinstance(op, CompareOp)
            return Compare(
                loc=LOC,
                ty=Ty.Bool,
                op=op,
                lhs=self.expr(ty, depth),
                rhs=self.expr(ty, depth),
                via_obj_cmp=False,
            )
        if kind == "not":
            return Unary(loc=LOC, ty=Ty.Bool, op=UnaryOp.NOT, operand=self.bool_expr(depth - 1))
        op_kind = self.draw(st.sampled_from(list(BoolOpKind)))
        assert isinstance(op_kind, BoolOpKind)
        count = self._int(2, 3)
        return BoolOp(
            loc=LOC,
            ty=Ty.Bool,
            op=op_kind,
            operands=tuple(self.bool_expr(depth - 1) for _ in range(count)),
        )

    # --- statements -------------------------------------------------------

    def stmt(self, depth: int, *, in_loop: bool) -> IRStmt:
        """One statement. `Break`/`Continue` are offered only inside a loop --
        outside one they are an `EmitError` by design (Task 2's loud contract),
        and a generator that produced them would be testing the negative
        control below rather than the property."""
        options = ["nop", "set"] if self.bound else ["nop"]
        if depth > 0:
            options += ["if", "while"]
        if in_loop:
            options += ["break", "continue"]
        kind = self._pick(options)
        if kind == "nop":
            return Nop(loc=LOC)
        if kind == "break":
            return Break(loc=LOC)
        if kind == "continue":
            return Continue(loc=LOC)
        if kind == "set":
            slot = self.draw(st.sampled_from(sorted(self.bound)))
            assert isinstance(slot, int)
            return SetLocal(loc=LOC, slot=slot, value=self.expr(self.bound[slot], 1))
        if kind == "if":
            return If(
                loc=LOC,
                cond=self.bool_expr(1),
                body=self.block(depth - 1, in_loop=in_loop),
                orelse=self.block(depth - 1, in_loop=in_loop),
            )
        # A `While` whose condition never goes false loops forever AT RUNTIME,
        # which is harmless here: nothing in this module invokes a generated
        # function, only builds and instantiates it.
        return While(
            loc=LOC,
            cond=self.bool_expr(1),
            body=self.block(depth - 1, in_loop=True),
        )

    def block(self, depth: int, *, in_loop: bool) -> tuple[IRStmt, ...]:
        count = self._int(1, 2)
        return tuple(self.stmt(depth, in_loop=in_loop) for _ in range(count))


@st.composite
def scalar_funcs(draw: _Draw) -> FuncIR:
    """One well-typed scalar `FuncIR`, shaped like something C could emit.

    Structure: the declared locals are bound first, in slot order (§C.3 rule 1
    -- `SlotTable` numbers slots in first-binding order, so they are contiguous
    from 0 and a `LetLocal` always precedes any read), then a short run of
    statements, then a `Return`. `returns_on_every_path=True` is therefore true
    by construction rather than asserted: the body's last statement is the
    `Return`.
    """
    ret = draw(st.sampled_from(list(TYPES)))
    assert isinstance(ret, Ty)
    gen = _Gen(draw, param_ty=ret)

    nlocals = draw(st.integers(min_value=0, max_value=2))
    assert isinstance(nlocals, int)
    body: list[IRStmt] = []
    for slot in range(nlocals):
        ty = draw(st.sampled_from(list(TYPES)))
        assert isinstance(ty, Ty)
        body.append(LetLocal(loc=LOC, slot=slot, ty=ty, init=gen.expr(ty, _MAX_EXPR_DEPTH - 1)))
        gen.bound[slot] = ty

    tail = draw(st.integers(min_value=0, max_value=3))
    assert isinstance(tail, int)
    for _ in range(tail):
        body.append(gen.stmt(_MAX_STMT_DEPTH, in_loop=False))
    body.append(Return(loc=LOC, value=gen.expr(ret, _MAX_EXPR_DEPTH)))

    return FuncIR(
        loc=LOC,
        py_name="go",
        export_name="go",
        kind=FuncKind.EXPORT,
        params=(("a", ret, LOC),),
        ret=ret,
        doc="",
        locals=tuple((slot, f"v{slot}", gen.bound[slot]) for slot in range(nlocals)),
        body=tuple(body),
        returns_on_every_path=True,
    )


# ===========================================================================
# the independent checks
# ===========================================================================


def assert_independently_valid(wasm: bytes) -> None:
    """`wasm` is valid, according to two validators that are not serpent's.

    `wasm-tools` is skipped -- not failed -- when it is absent (ruling E5's
    `None` verdict); wasmtime always runs, so there is never a path where
    NEITHER independent validator looked at the bytes.
    """
    verdict = _validate.validate_external(wasm)
    assert verdict is not False, "wasm-tools rejected a module build_wasm returned"

    host = FullHost()
    host.attach(engine.MiniHost(wasm, imports=host.bindings()))


def independently_valid(wasm: bytes) -> bool:
    """`assert_independently_valid` as a predicate, for the negative control."""
    try:
        assert_independently_valid(wasm)
    except (AssertionError, wasmtime.WasmtimeError):
        return False
    return True


# ===========================================================================
# THE property
# ===========================================================================


@given(scalar_funcs())
@CI_FUZZ
def test_a_generated_ir_function_either_validates_or_raises_emit_error(fn: FuncIR) -> None:
    """D.5.2's property, in one place.

    `validate_external=False` on the build so that `wasm-tools` runs HERE, in
    `assert_independently_valid`, rather than inside `build_wasm` -- the point
    is an independent verdict on what the public API handed back, not a second
    invocation of the same gate.

    Exactly TWO failure classes are caught, and that narrowness is the point:
    `build_wasm`'s own contract (E15/M10) says a `BuildLimitError` becomes a
    located SPT800x `CompileError` and a bare `EmitError` becomes a
    `CompilerBugError`, so neither of those two should ever escape unmapped.
    Catching them here as well would hide a violation of that contract; letting
    them through means Hypothesis reports the offending example.
    """
    try:
        built = build_wasm(splice(fn), validate_external=False)
    except CompileError as exc:
        # A budget the module outgrew -- the only compile-shaped failure a build
        # may have, and it must be located and registered like any diagnostic.
        assert exc.diagnostics
        for diag in exc.diagnostics:
            assert diag.code.startswith("SPT800"), diag
        return
    except CompilerBugError:
        # An `EmitError` -- a lowering invariant broke. Permitted by the
        # property (the emitter REFUSED rather than emitting bytes); any example
        # that reaches this arm is written down in the task report.
        return
    assert_independently_valid(built.wasm)
    assert built.exports == ("go",)
    assert built.module_size == len(built.wasm)


@given(scalar_funcs())
@CI_FUZZ
def test_a_generated_ir_function_builds_deterministically(fn: FuncIR) -> None:
    """The same IR built twice is the same bytes twice (Task 11's determinism
    claim, over generated input rather than over the four fixtures).

    Cheap, and it catches the class of bug a single build cannot: a dict or set
    iterated in insertion order that happens to be stable for the fixtures and
    is not in general -- the import order, the runtime-part order, the literal
    pool's layout.
    """
    try:
        first = build_wasm(splice(fn), validate_external=False)
        second = build_wasm(splice(fn), validate_external=False)
    except (CompileError, CompilerBugError):
        return
    assert first.wasm == second.wasm
    assert first.imports == second.imports
    assert first.runtime_parts_linked == second.runtime_parts_linked


# --- the negative controls: both arms of the disjunction are real -----------


def _func(body: Sequence[IRStmt], **kwargs: object) -> FuncIR:
    """A minimal `U32 go(a: U32)` whose body is `body`, for the probes below."""
    defaults: dict[str, object] = {
        "loc": LOC,
        "py_name": "go",
        "export_name": "go",
        "kind": FuncKind.EXPORT,
        "params": (("a", Ty.U32, LOC),),
        "ret": Ty.U32,
        "doc": "",
        "locals": (),
        "body": tuple(body),
        "returns_on_every_path": True,
    }
    defaults.update(kwargs)
    return FuncIR(**defaults)  # type: ignore[arg-type]


_RETURN_ONE = Return(loc=LOC, value=Const(loc=LOC, ty=Ty.U32, py_value=1))

#: Two IRs that break a documented lowering invariant. Both are things the
#: FRONTEND never produces (a `Break` outside a loop is a compile reject; slot
#: numbers are contiguous by construction), which is exactly why the emitter's
#: reaction to them matters: it must be a loud refusal, not a branch that lands
#: somewhere plausible.
_INVARIANT_BREAKERS: Final[dict[str, FuncIR]] = {
    "break_outside_a_loop": _func([Break(loc=LOC), _RETURN_ONE]),
    "a_gap_in_the_declared_slots": _func(
        [
            LetLocal(loc=LOC, slot=0, ty=Ty.U32, init=Const(loc=LOC, ty=Ty.U32, py_value=1)),
            Return(loc=LOC, value=LocalRef(loc=LOC, ty=Ty.U32, slot=0, name="v0")),
        ],
        locals=((0, "v0", Ty.U32), (2, "v2", Ty.U32)),
    ),
}


@pytest.mark.parametrize("name", sorted(_INVARIANT_BREAKERS), ids=sorted(_INVARIANT_BREAKERS))
def test_an_ir_that_breaks_a_lowering_invariant_raises_rather_than_emitting(name: str) -> None:
    """The `or raises` arm of the property is REACHABLE.

    Without this, "either validates or raises `EmitError`" could be satisfied
    by an emitter that never raises at all, and the fuzz above would be a
    one-sided check. `EmitError` surfaces through the public API as
    `loader.CompilerBugError` (E15/M10: an emitter invariant break is never a
    user error, so it is not a diagnostic).
    """
    with pytest.raises(CompilerBugError):
        build_wasm(splice(_INVARIANT_BREAKERS[name]), validate_external=False)


def test_a_corrupted_module_is_rejected_by_the_independent_validators() -> None:
    """The `validates` arm has TEETH.

    A checking helper that silently accepted everything -- a `wasm-tools` that
    is not installed AND a wasmtime call that swallowed its error -- would make
    every fuzz example pass. So: build a real module, damage it, and assert the
    same helper says no. The damage is applied to the LAST byte of the module,
    which is inside the final custom section's payload, and to the wasm version
    word, which no validator can miss.
    """
    built = build_wasm(splice(_func([_RETURN_ONE])), validate_external=False)
    assert independently_valid(built.wasm)

    truncated = built.wasm[:-1]
    assert not independently_valid(truncated)

    bad_version = bytearray(built.wasm)
    bad_version[4] = 0x09  # the wasm version is 1; 9 is not a version
    assert not independently_valid(bytes(bad_version))


def test_the_generator_only_produces_types_the_ranges_table_covers() -> None:
    """A meta-check on the generator itself: `TYPES` and `_RANGES` must name the
    same set, or a drawn `Const` would raise `KeyError` from inside the strategy
    and Hypothesis would report it as a failing example of the property rather
    than as a broken generator."""
    assert set(TYPES) == set(_RANGES)
    assert set(TYPES) == set(TEMPLATES)


# ===========================================================================
# the two whole-fixture budgets
# ===========================================================================

#: §B.1's tripwire: 20% of S22's 131072-byte module cap. Deliberately far below
#: the real limit -- the emitter enforces S22 itself (SPT8001) and this is the
#: early warning, so a fixture that doubled in size trips this long before it
#: trips the cap.
SIZE_BUDGET: Final = 26214


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_every_fixture_stays_within_the_size_budget(path: Path) -> None:
    built = build_fixture(path)
    assert built.module_size <= SIZE_BUDGET, (
        f"{path.name}: {built.module_size} bytes exceeds the {SIZE_BUDGET}-byte "
        f"tripwire (20% of S22's 131072 cap) by {built.module_size - SIZE_BUDGET}"
    )


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_every_part_the_frontend_needs_is_a_part_the_build_linked(path: Path) -> None:
    """Ruling E3: `runtime_parts_needed <= runtime_parts_linked`.

    A subset, not an equality, and in that direction on purpose: the frontend's
    set is a HINT over the alternatives D may pick (`host_fns_reachable`'s twin,
    C21), so D linking MORE than the hint named is fine while linking less means
    a call to a function that is not in the module.
    """
    compiled = compile_module(path.read_text(encoding="utf-8"), str(path))
    built = build_fixture(path)
    missing = sorted(compiled.runtime_parts_needed - built.runtime_parts_linked)
    assert missing == [], (
        f"{path.name}: the frontend says these guest-runtime parts are needed and the "
        f"build linked none of them: {missing}"
    )
