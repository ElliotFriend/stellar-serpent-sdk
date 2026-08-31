"""Task 11c: the frontend's anchored checks -- F.2.9, F.2.10, F.2.7, F.2.6.

Four differential obligations, all built on ONE set of representative example
modules (`EXAMPLE_NAMES`, compiled once by the `examples` fixture), because
they are four independent views of the same compile and a divergence is only
interesting if the same source produces it:

* **F.2.9, the Phase 0 host-fn golden.** The eight host functions
  `spikes/spike1/spike.wasm` imports are a RECORDED fact about a module that
  was validated, deployed to testnet, fetched back byte-identical and run on
  chain. `tests/fixtures/spike1_reauthored.py` says the same contract in
  serpent's authoring surface, and its `host_fns_used` must be exactly those
  eight. This is the only end-to-end anchor the frontend has before sub-plan D
  exists.
* **F.2.10, golden IR snapshots.** A textual rendering of each example's
  `FuncIR` list, stored under `tests/goldens/ir/`, so a refactor that changes
  lowering arrives as a reviewable diff instead of a silent behavioral change.
  `SERPENT_REGEN_GOLDENS=1` rewrites them.
* **F.2.7, the spec-view cross-check.** `compile_module`'s `spec_inputs`, fed
  to `spec.sections.build_spec_entries`, must byte-match what an INDEPENDENT
  collection of the module's decorated classes produces through the same
  builder -- i.e. the frontend's declared-type inventory reproduces the M1-B
  path exactly, ordering included. The bytes only encode ordering when two
  types of the same kind are present, so the `containers` example carries two
  structs on purpose and
  `test_the_spec_byte_match_is_order_sensitive` proves the byte-match really
  is order-sensitive. Plus the decoded function entries are cross-checked
  against the AST-derived `FuncIR` list, which is what catches F.1.14's
  import/AST skew.
* **F.2.6, the host-fn <-> protocol cross-check.** `declared_protocol` must
  equal the max `min_protocol` over `host_fns_reachable` **raised by the
  feature gates**, computed two independent ways, and no function in the used
  set may be gated above the target. Nothing M1-C can reach is gated today, so
  the per-example IMPORT floor is always `BASE_PROTOCOL` -- which is recorded as
  its own assertion, and is why `_independent_floor` is additionally exercised
  on synthetic gated/ungated inputs rather than only on the examples. The one
  FEATURE gate (2026-08-28 ruling) is `__constructor`, which the host honors
  only from protocol 22 (spec SS 13 / CAP-0058) and which no binding can carry
  because it is an export NAME, not an import: `_independent_expected_protocol`
  applies it here, so the two examples with an `__init__` (`memoryless`,
  `token_style`) are cross-checked at 22 and the other three at 20.

## Two obligations, two different strengths -- deliberately

The host-function set is pinned **exactly** (F.2.9 asserts frozenset equality
against the eight recorded Phase 0 names), while the protocol is pinned only as
a **floor** (F.2.6 asserts a max over that set). That asymmetry is not an
oversight: the exact set is what sub-plan D writes into the wasm IMPORT
section, where a missing or extra name is a broken module, whereas the floor is
what D writes into `contractenvmetav0`, where the only property that matters is
that no reached function is gated outside it. Pinning the protocol exactly
would pin `20` -- a fact about the current `rs-soroban-env` pin, not about the
frontend -- and would fail on every future re-pin that gates something new
without saying anything about whether the frontend computed it correctly.

Plus the **11a ruling rider** at the bottom: the compiler-side coverage the
controller asked for after `vec_pop_back_of_empty_traps` turned out not to be
compiler-expressible in its frozen spelling.

## Why the rendering drops `Loc`

The golden is about LOWERING SHAPE -- which nodes, in which order, with which
types and which recorded decisions. Every node's `Loc` is dropped from the
rendering, deliberately:

* location correctness is already asserted, exhaustively and by construction,
  everywhere it matters: all 95 `tests/must_reject/` fixtures pin a diagnostic
  at a `# HERE` marker, `test_must_reject.py`'s diagnostics-quality sweep
  requires a `NODE` (never `WHOLE_FILE`) `Loc` on every one, and
  `test_frontend_fuzz.py` requires a located diagnostic for arbitrary input;
* including line numbers would make every unrelated edit to an example --
  adding a docstring line, reflowing a comment -- rewrite the whole golden,
  which is exactly the churn that trains a reviewer to regenerate goldens
  without reading them.

Nothing else is dropped: object identity never enters the rendering (there are
no ids, addresses or paths in it -- `test_goldens_have_no_identity_leaks`
enforces that), so the text is a pure function of the source.
"""

from __future__ import annotations

import ast
import dataclasses
import itertools
import os
import sys
import types
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from stellar_sdk import xdr
from xdrlib3 import Unpacker

import serpent
from serpent._host import functions_by_name
from serpent._host._protocol import (
    BASE_PROTOCOL,
    CONSTRUCTOR_MIN_PROTOCOL,
    DEFAULT_TARGET_PROTOCOL,
    declared_protocol,
)
from serpent.compiler.diagnostics import CompileError, Loc
from serpent.compiler.frontend import CompiledModule, compile_module
from serpent.compiler.ir import (
    FuncIR,
    FuncKind,
    HostCall,
    IRNode,
    LetLocal,
    LocalRef,
    SetLocal,
    walk,
)
from serpent.compiler.types_ import Ty
from serpent.decorators import _METADATA_ATTR
from serpent.spec.sections import CONSTRUCTOR_NAME, build_spec_entries
from serpent.spec.typemap import SpecTypeError
from tests.semantics.cases import CASES
from tests.unit.test_frontend_semantics import compile_case

# --- the example set ---------------------------------------------------------

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "goldens" / "ir"

#: The environment variable that rewrites every golden instead of comparing.
REGEN_ENV = "SERPENT_REGEN_GOLDENS"

REGEN_HINT = f"{REGEN_ENV}=1 uv run pytest tests/unit/test_frontend_goldens.py"

#: The two complete authored contracts on disk. `token_style.py` is A23's only
#: real contract (F.2.8); `spike1_reauthored.py` is F.2.9's fresh re-author of
#: Phase 0's on-chain-verified spike (the spike itself is FROZEN, R5, and is
#: never read, imported or modified by this module).
_ON_DISK: tuple[str, ...] = ("token_style", "spike1_reauthored")

#: Three constructed modules, chosen to cover the lowering shapes the two
#: on-disk contracts do not reach between them:
#:
#: * `control_flow` -- `While`/`Break`/`Continue`, the desugared `for` over
#:   `range`, `If`/`elif`/`else`, `IfExp`, `Raise`, `Unary(NOT)`/`IsZero`,
#:   `InternalCall` to BOTH a private method and a module-level helper, and
#:   128-bit arithmetic (the `i128_mul`/`i128_neg` guest-runtime parts).
#: * `containers` -- static and dynamic `MakeVec`, `MakeMap`, `MakeStruct` and
#:   `FieldGet`, container mutation on an owned local, `for` over a `Vec`,
#:   `len`, an over-9-character `Symbol`, a `String` literal, and an
#:   `obj_cmp`-routed comparison. It is also the ONLY example with two types of
#:   the same spec kind (two `@contracttype` structs), which is what makes
#:   F.2.7's byte-match order-sensitive -- see `Label`'s own docstring.
#: * `memoryless` -- a contract that needs NO linear memory (spec Sec.5 keeps
#:   that supported), plus `__init__` -> `__constructor` and a storage `has`.
_CONSTRUCTED: dict[str, str] = {
    "control_flow": '''"""Control flow and 128-bit arithmetic: loops, jumps, helpers, early returns."""

from serpent import I128, U32, U64, Bool, Env, Symbol, contract, contracterror, errorcode

LIMIT = U32(10)


@contracterror
class FlowError:
    TooBig = errorcode(1)
    Empty = errorcode(2)


def clamp(value: U32) -> U32:
    """A module-level helper, called from a method (E8)."""
    if value > LIMIT:
        return LIMIT
    return value


@contract
class Flow:
    """Loops, jumps, early returns and wide arithmetic in one contract."""

    def sum_to(self, env: Env, n: U32) -> U32:
        total = U32(0)
        i = U32(0)
        while i < n:
            i = i + U32(1)
            if i % U32(2) == U32(0):
                continue
            if i > LIMIT:
                break
            total = total + i
        return clamp(total)

    def countdown(self, env: Env, n: U32) -> U32:
        seen = U32(0)
        for i in range(3):
            seen = seen + U32(1)
        return seen

    def wide(self, env: Env, a: I128, b: I128) -> I128:
        scaled = a * b
        if scaled < I128(0):
            return -scaled
        return scaled

    def classify(self, env: Env, n: U32) -> Symbol:
        if n == U32(0):
            raise FlowError.Empty
        elif n > LIMIT:
            raise FlowError.TooBig
        else:
            return Symbol("ok")

    def ternary(self, env: Env, flag: Bool, n: U64) -> U64:
        return n if flag else U64(0)

    def _private(self, env: Env, n: U32) -> Bool:
        return Bool(not (n == U32(0)))

    def uses_private(self, env: Env, n: U32) -> Bool:
        return self._private(env, n)
''',
    "containers": '''"""Container construction, mutation and iteration (A13/MJ-15/E11)."""

from serpent import U32, Address, Bool, Env, Map, String, Symbol, Vec, contract, contracttype


@contracttype
class Holder:
    owner: Address
    tally: U32


@contracttype
class Label:
    """A SECOND struct, so the spec-view byte-match is order-sensitive.

    `sections.build_spec_entries` partitions `types` into structs-then-enums,
    so a module with at most one struct and at most one error enum emits the
    same bytes whatever order the inventory arrives in -- which would make
    F.2.7's byte-match blind to an inventory ORDERING bug. Two structs is the
    smallest shape that is not: reversing `declared_types_in_order` swaps
    `Holder` and `Label` inside the struct partition and changes the payload.
    `test_the_spec_byte_match_is_order_sensitive` asserts exactly that.
    """

    text: String
    weight: U32


@contract
class Containers:
    """Every container node kind, static and dynamic."""

    def static_vec(self, env: Env) -> Vec[U32]:
        return Vec(U32, [U32(1), U32(2), U32(3)])

    def dynamic_vec(self, env: Env, seed: U32) -> Vec[U32]:
        out = Vec(U32, [])
        out.push_back(seed)
        out.push_back(seed + U32(1))
        return out

    def built_in_a_loop(self, env: Env, n: U32) -> Vec[U32]:
        out = Vec(U32, [])
        i = U32(0)
        while i < n:
            out.push_back(i)
            i = i + U32(1)
        return out

    def static_map(self, env: Env) -> Map[Symbol, U32]:
        return Map(Symbol, U32, [(Symbol("a"), U32(1)), (Symbol("b"), U32(2))])

    def sum_vec(self, env: Env, items: Vec[U32]) -> U32:
        total = U32(0)
        for item in items:
            total = total + item
        return total

    def size(self, env: Env, items: Vec[U32]) -> U32:
        return len(items)

    def read_struct(self, env: Env, who: Address) -> U32:
        holder = Holder(owner=who, tally=U32(7))
        return holder.tally

    def read_label(self, env: Env) -> U32:
        label = Label(text=String("tag"), weight=U32(3))
        return label.weight

    def long_string(self, env: Env) -> String:
        return String("a string literal that lives in linear memory")

    def long_symbol(self, env: Env) -> Symbol:
        return Symbol("over_nine_chars")

    def compare_addresses(self, env: Env, a: Address, b: Address) -> Bool:
        return Bool(a == b)
''',
    "memoryless": '''"""A contract that needs NO linear memory (spec Sec.5 keeps this supported)."""

from serpent import U32, Bool, Env, Symbol, contract

COUNT = Symbol("COUNT")


@contract
class Memoryless:
    """Short Symbol keys are SymbolSmall immediates (S22): no data section."""

    def __init__(self, env: Env, start: U32) -> None:
        env.storage().instance().set(COUNT, start)

    def bump(self, env: Env) -> U32:
        current = env.storage().instance().get(COUNT, U32)
        nxt = current + U32(1)
        env.storage().instance().set(COUNT, nxt)
        return nxt

    def is_set(self, env: Env) -> Bool:
        return Bool(env.storage().instance().has(COUNT))
''',
}

#: Every example's name, sorted so parametrization ids and the golden-directory
#: comparison are both stable.
EXAMPLE_NAMES: tuple[str, ...] = tuple(sorted((*_ON_DISK, *_CONSTRUCTED)))


def example_source(name: str) -> tuple[str, str]:
    """`(source, display path)` for one example.

    The display path is deliberately RELATIVE for the on-disk fixtures
    (`compile_module` never opens `path`, it only quotes it), so nothing that
    depends on it -- a diagnostic, a golden -- can carry an absolute path off
    this machine.
    """
    if name in _CONSTRUCTED:
        return _CONSTRUCTED[name], f"examples/{name}.py"
    return (_FIXTURES / f"{name}.py").read_text(), f"tests/fixtures/{name}.py"


@pytest.fixture(scope="module")
def examples() -> dict[str, CompiledModule]:
    """Every example compiled once. A `CompileError` here fails every test in
    this module, which is the correct blast radius: an example that stopped
    compiling invalidates all four obligations at once."""
    return {name: compile_module(*example_source(name)) for name in EXAMPLE_NAMES}


def test_every_example_compiles_clean(examples: dict[str, CompiledModule]) -> None:
    assert sorted(examples) == list(EXAMPLE_NAMES)
    for name, compiled in examples.items():
        assert compiled.ir.contract is not None, name
        assert compiled.functions, name


# --- F.2.10: the golden IR rendering -----------------------------------------

_PAD = "  "


def _drop(value: object) -> bool:
    """Whether a value is omitted from the rendering entirely.

    Only `Loc` -- see this module's docstring for why. A `Loc` appears both as
    a node's own `loc` field and as an element of `FuncIR.params`' tuples, so
    the test is on the VALUE, not on a field-name allowlist that would miss the
    second position.
    """
    return isinstance(value, Loc)


def _has_node(value: object) -> bool:
    if isinstance(value, IRNode):
        return True
    if isinstance(value, (tuple, list)):
        return any(_has_node(item) for item in value)
    return False


def _scalar(value: object) -> str:
    """One non-node value as stable text.

    `Ty.render()` for a type (the dossier's own `Vec(Ty)`/`Map(Ty, Ty)`
    notation), the member NAME for an enum (never its `auto()` number, which
    would churn if a member were inserted), and `repr` for everything else --
    `int`, `str`, `bytes`, `bool`, `None`. No `repr` of an object that has no
    stable one ever reaches here: every IR field is one of those, a `Ty`, an
    enum, a `Loc`, or a node.
    """
    if isinstance(value, Ty):
        return value.render()
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, tuple):
        return "(" + ", ".join(_scalar(item) for item in value if not _drop(item)) + ")"
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(item) for item in value if not _drop(item)) + "]"
    return repr(value)


def _render_node(node: IRNode, depth: int, label: str, out: list[str]) -> None:
    """One node: its scalar fields inline, its composite fields indented.

    Reflection over `dataclasses.fields` rather than a per-node-kind visitor,
    for the same reason `ir.walk` and `frontend._invalid_ty_fields` use it: a
    node kind or a field added later is rendered automatically -- and so shows
    up in the golden diff -- instead of being silently invisible.
    """
    inline: list[str] = []
    nested: list[tuple[str, object]] = []
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        if _drop(value):
            continue
        if isinstance(value, (tuple, list)):
            if any(not _drop(item) for item in value):
                nested.append((field.name, value))
            else:
                inline.append(f"{field.name}=()")
        elif isinstance(value, IRNode):
            nested.append((field.name, value))
        else:
            inline.append(f"{field.name}={_scalar(value)}")
    head = type(node).__name__
    if inline:
        head += " " + " ".join(inline)
    out.append(_PAD * depth + (f"{label}: " if label else "") + head)
    for name, value in nested:
        _render_value(name, value, depth + 1, out)


def _render_value(label: str, value: object, depth: int, out: list[str]) -> None:
    """One labelled field value. A sequence gets one line per element (so a
    golden diff names the element that changed); an element that contains no
    node is rendered inline on that line."""
    if isinstance(value, IRNode):
        _render_node(value, depth, label, out)
        return
    if isinstance(value, (tuple, list)):
        items = [(index, item) for index, item in enumerate(value) if not _drop(item)]
        if not items:
            out.append(_PAD * depth + f"{label}: ()")
            return
        out.append(_PAD * depth + f"{label}:")
        for index, item in items:
            if _has_node(item):
                _render_value(f"[{index}]", item, depth + 1, out)
            else:
                out.append(_PAD * (depth + 1) + f"[{index}]: {_scalar(item)}")
        return
    out.append(_PAD * depth + f"{label}: {_scalar(value)}")


def render_functions(functions: Sequence[FuncIR]) -> str:
    """The golden text for one module's flat `FuncIR` list.

    `CompiledModule.functions` is rendered rather than the whole `ModuleIR`
    for one concrete reason beyond F.2.10's own wording: `ModuleIR.path` is
    the caller's display path, and a golden carrying it would either pin a
    path or have to special-case it. A `FuncIR` has no path field at all.
    """
    out: list[str] = []
    for func in functions:
        _render_node(func, 0, "", out)
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.ir.txt"


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_golden_ir_snapshot(name: str, examples: dict[str, CompiledModule]) -> None:
    """F.2.10: each example's lowered `FuncIR` list matches its stored golden."""
    rendered = render_functions(examples[name].functions)
    path = golden_path(name)
    if os.environ.get(REGEN_ENV) == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered)
    if not path.exists():
        pytest.fail(f"no golden for {name!r} at {path}; create it with `{REGEN_HINT}`")
    stored = path.read_text()
    assert stored == rendered, (
        f"the lowered IR for {name!r} no longer matches {path.name}.\n"
        f"If the change is intended, regenerate with `{REGEN_HINT}` and REVIEW the diff -- "
        "a golden diff is a behavioral change to what sub-plan D will emit, not noise."
    )


def test_no_stale_or_missing_goldens() -> None:
    """The golden directory is exactly the example set: a renamed example
    leaves no orphan file behind, and a new one cannot be silently ungoldened
    (`test_golden_ir_snapshot` would fail, but only if someone remembered to
    add it to `EXAMPLE_NAMES` -- this is the other direction)."""
    stored = sorted(path.name for path in GOLDEN_DIR.glob("*.ir.txt"))
    assert stored == sorted(f"{name}.ir.txt" for name in EXAMPLE_NAMES)


def test_the_rendering_is_deterministic(examples: dict[str, CompiledModule]) -> None:
    """Same source -> same text, across two independent compiles.

    Rendering the SAME `CompiledModule` twice would only prove the renderer is
    a function; recompiling proves the compile is, which is what a golden
    actually depends on (a set iteration order leaking into the IR would show
    up here and nowhere else).
    """
    for name in EXAMPLE_NAMES:
        first = render_functions(examples[name].functions)
        second = render_functions(compile_module(*example_source(name)).functions)
        assert first == second, name


def test_goldens_have_no_identity_leaks() -> None:
    """No golden may carry anything machine- or run-specific: an object
    address, an `id()`, or an absolute filesystem path."""
    repo_root = str(Path(__file__).resolve().parents[2])
    for name in EXAMPLE_NAMES:
        text = golden_path(name).read_text()
        assert " object at 0x" not in text, name
        assert "0x" not in text, name
        assert repo_root not in text, name
        assert "/Users/" not in text, name


def test_the_goldens_show_no_arithmetic_constant_folding() -> None:
    """F.1.10 / the standing no-folding constraint, read off the goldens.

    `I32(2**31 - 1) + I32(1)` must survive to runtime as an
    `ArithmeticOverflow`, so C folds nothing: a `Binary` over two `Const`s
    stays a `Binary` over two `Const`s. `control_flow`'s `total + i` and
    `containers`' `seed + U32(1)` are `Binary` nodes in the golden text, and
    `memoryless`' `current + U32(1)` is a `Binary` whose `rhs` is a `Const`.
    """
    text = golden_path("memoryless").read_text()
    assert "Binary ty=U32 op=ADD" in text
    assert "rhs: Const ty=U32 py_value=1" in text


def test_the_renderer_drops_locs_and_nothing_else(examples: dict[str, CompiledModule]) -> None:
    """The one documented omission is the only omission: every OTHER field of
    every node reached by the walk appears in the rendered text by name."""
    compiled = examples["token_style"]
    text = render_functions(compiled.functions)
    seen: set[str] = set()
    for func in compiled.functions:
        for node in walk(func):
            for field in dataclasses.fields(node):
                if not isinstance(getattr(node, field.name), Loc):
                    seen.add(field.name)
    missing = sorted(name for name in seen if name not in text)
    assert not missing, missing
    assert "loc=" not in text
    assert "LocKind" not in text


# --- F.2.9: the Phase 0 host-fn golden ---------------------------------------

#: The eight host functions `spikes/spike1/spike.wasm` imports.
#:
#: **Copied, not parsed.** `spikes/` is frozen (R5) and the wasm itself is
#: git-ignored, so the source of truth is the pair of recorded files:
#:
#: * `spikes/spike1/ACCEPTANCE.md` -- row 8 names them as "the eight imported
#:   host functions" of the 877-byte artifact whose sha256 `bc2e8063...` was
#:   fetched back off testnet byte-identical (row 4), and the header records
#:   "the floor computed from the eight imports is 20";
#: * `spikes/spike1/harness.py::SpikeHost._implementations` -- the mini-host's
#:   dict, whose keys are bound BY NAME out of the same pinned `env.json` the
#:   spike's emitter compiled against, and which is cross-checked against
#:   `spikes/spike1/emitter.py::HOST_FN_NAMES`.
#:
#: The contract they belong to is re-authored (never modified, never imported)
#: as `tests/fixtures/spike1_reauthored.py`.
PHASE_0_HOST_FNS: frozenset[str] = frozenset(
    {
        "put_contract_data",
        "has_contract_data",
        "get_contract_data",
        "map_new_from_linear_memory",
        "map_get",
        "symbol_new_from_linear_memory",
        "string_new_from_linear_memory",
        "fail_with_error",
    }
)

#: `ACCEPTANCE.md`'s header: "the floor computed from the eight imports is 20
#: (none of them carries a `min_supported_protocol` in v28.0.2)".
PHASE_0_FLOOR = 20


def test_the_spike_reauthor_uses_exactly_the_eight_phase_0_host_fns(
    examples: dict[str, CompiledModule],
) -> None:
    """F.2.9: the frontend's host-fn accounting, anchored to a module that ran
    on chain.

    **Delta list: EMPTY.** `host_fns_used` is exactly the recorded eight, with
    no additions and no omissions, so no per-delta justification is needed. The
    two halves worth stating anyway, because they are the reason it lands
    exactly rather than approximately:

    * `has_contract_data` is reached only through `get(..., default=...)`
      (`bump`'s `COUNT` read), which lowers to a `has`-then-`get` `IfExp`; the
      spike's own `default=U32(0)` is what put it in the wasm's import set too.
    * `symbol_new_from_linear_memory` is reached only through the 13-character
      `counter_limit` field name -- a `Symbol` of 9 characters or fewer is a
      SymbolSmall immediate (S22), so `SETTINGS` and `COUNT` need none of it.
      That is the same mechanism `ACCEPTANCE.md` row 7 records on chain.
    """
    compiled = examples["spike1_reauthored"]
    assert compiled.host_fns_used == PHASE_0_HOST_FNS, {
        "unexpected": sorted(compiled.host_fns_used - PHASE_0_HOST_FNS),
        "missing": sorted(PHASE_0_HOST_FNS - compiled.host_fns_used),
    }


def test_the_spike_reauthor_reaches_nothing_beyond_the_eight(
    examples: dict[str, CompiledModule],
) -> None:
    """The REACHABLE set adds nothing either -- the contract builds no `Vec`,
    `Map` or event topics, so there is no lowering form left for sub-plan D to
    choose between (MJ-15) and the two sets coincide."""
    compiled = examples["spike1_reauthored"]
    assert compiled.host_fns_reachable == PHASE_0_HOST_FNS


def test_the_spike_reauthor_declares_the_recorded_phase_0_floor(
    examples: dict[str, CompiledModule],
) -> None:
    """The protocol floor over those eight is 20, exactly as `ACCEPTANCE.md`
    recorded it against `rs-soroban-env` v28.0.2."""
    assert examples["spike1_reauthored"].declared_protocol == PHASE_0_FLOOR
    assert PHASE_0_FLOOR == BASE_PROTOCOL


def test_the_spike_reauthor_keeps_the_spikes_interface(
    examples: dict[str, CompiledModule],
) -> None:
    """The re-author is the SAME contract: same two exports, same signatures,
    same struct and error case. A re-author that quietly changed the interface
    would make the eight-name assertion above meaningless.
    """
    compiled = examples["spike1_reauthored"]
    contract = compiled.ir.contract
    assert contract is not None
    assert [f.export_name for f in contract.methods] == ["setup", "bump"]
    by_name = {f.export_name: f for f in contract.methods}
    assert [(n, t.render()) for n, t, _loc in by_name["setup"].params] == [("counter_limit", "U32")]
    assert by_name["setup"].ret == Ty.Void
    assert by_name["bump"].params == ()
    assert by_name["bump"].ret == Ty.U32
    (struct,) = compiled.ir.structs
    assert struct.name == "Settings"
    assert [name for name, _ty, _doc in struct.fields] == ["counter_limit", "display_name"]
    (enum,) = compiled.ir.error_enums
    assert enum.cases == (("LimitExceeded", 7, ""),)
    # The 13-character field name is the whole point of the fixture: it is what
    # forces `symbol_new_from_linear_memory` at the read side.
    assert len("counter_limit") > 9


def test_the_spike_is_re_authored_not_modified() -> None:
    """R5: `spikes/` is frozen, so F.2.9's fixture is a RE-AUTHOR in a new
    file, not an edit of the spike.

    Both halves are asserted, because "re-authored" is only meaningful if the
    original is still the original:

    * the spike's own `contract_src.py` still carries its retired `env`-first
      method spelling and its bare-int error member -- neither of which
      serpent's surface accepts, so an "in-place migration" would have had to
      change both;
    * the fixture is a separate file that carries the amended `self`-first
      spelling and `errorcode(7)`.

    This is the only place in this module that names a path inside `spikes/`,
    and `spike.wasm` is never opened or parsed at all: the eight names are
    copied text (see `PHASE_0_HOST_FNS`).
    """
    spike = Path(__file__).resolve().parents[2] / "spikes" / "spike1" / "contract_src.py"
    spike_source = spike.read_text()
    assert "def setup(env: Env, counter_limit: U32) -> None:" in spike_source
    assert "LimitExceeded = 7" in spike_source
    assert "def setup(self, env: Env" not in spike_source

    fixture_source = (_FIXTURES / "spike1_reauthored.py").read_text()
    assert "def setup(self, env: Env, counter_limit: U32) -> None:" in fixture_source
    assert "LimitExceeded = errorcode(7)" in fixture_source
    assert fixture_source != spike_source


# --- F.2.7: the spec-view cross-check ----------------------------------------

#: Distinct `sys.modules` key per independent collection, so two collections
#: (or one alongside a real `compile_module`) can never collide.
_MODULE_COUNTER = itertools.count()


def _independently_collected(source: str, path: str) -> tuple[type[Any], tuple[type[Any], ...]]:
    """`(contract class, declared types in declaration order)`, collected
    WITHOUT the frontend.

    This is the M1-B path as `build_spec_entries`' own docstring describes it
    ("sub-plan D collects the module's decorated classes and passes them
    here"): execute the module, then walk its namespace for classes carrying
    their OWN `_serpent_type_`, keeping declaration order and keeping events
    out (MJ-9 -- `sections._declared_type_entry` refuses an event class
    unconditionally, so an inventory that included one would be a hard failure
    at emission, not a byte difference).

    Declaration order comes from the AST, not from `dict` insertion order:
    a module namespace happens to preserve insertion order today, but the
    ordering `build_spec_entries` pins (B10) is a property of the SOURCE, and
    reading it from the source is what makes this collection independent of
    anything the frontend concluded.

    The module is executed into a real `types.ModuleType` registered in
    `sys.modules` for the duration, because `@contracttype`'s annotation
    resolution goes through `typing.get_type_hints`, which reads
    `sys.modules[cls.__module__].__dict__` -- executing into a bare `dict`
    makes every annotation unresolvable. This mirrors `loader._execute`, which
    is the same requirement seen from the other side.
    """
    module_name = f"_serpent_specview_{next(_MODULE_COUNTER)}"
    module = types.ModuleType(module_name)
    module.__dict__["__file__"] = path
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        exec(compile(source, path, "exec"), module.__dict__)  # noqa: S102 -- our own fixtures
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:  # pragma: no cover -- the counter makes a collision impossible
            sys.modules[module_name] = previous

    namespace = module.__dict__
    tree = ast.parse(source)
    order = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]

    contract_cls: type[Any] | None = None
    types_: list[type[Any]] = []
    for name in order:
        cls = namespace.get(name)
        if not isinstance(cls, type):
            continue
        metadata = vars(cls).get(_METADATA_ATTR)
        if not isinstance(metadata, dict):
            continue
        kind = metadata.get("kind")
        if kind == "contract":
            contract_cls = cls
        elif kind in ("struct", "error_enum"):
            types_.append(cls)
    assert contract_cls is not None, f"{path} declares no @contract class"
    return contract_cls, tuple(types_)


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_spec_inputs_byte_match_the_independent_collection(
    name: str, examples: dict[str, CompiledModule]
) -> None:
    """F.2.7: the frontend's declared-type inventory reproduces the M1-B path.

    Both sides call the SAME builder, so this is not a re-implementation of
    the spec format -- it is the assertion that the two INVENTORIES agree,
    which is the only thing the frontend contributes here and the only thing
    that can differ.

    **What the bytes are and are not sensitive to.** A missing type or an extra
    one always changes them. A different ORDER changes them only when the
    inventory holds two or more types of the SAME kind, because
    `build_spec_entries` partitions `types` into structs-then-enums (B10) and
    then emits each partition in `types` order -- so a module with one struct
    and one error enum emits identical bytes from either ordering. The
    `containers` example carries two structs (`Holder`, `Label`) specifically
    so that at least one example makes the byte-match order-sensitive;
    `test_the_spec_byte_match_is_order_sensitive` proves it does, and the
    name-list assertion at the end of this test is what covers ordering for
    the four examples where the bytes alone cannot.
    """
    compiled = examples[name]
    source, path = example_source(name)
    contract_cls, types = _independently_collected(source, path)

    frontend_cls = compiled.spec_inputs.contract_cls
    assert frontend_cls is not None
    from_frontend = build_spec_entries(
        frontend_cls, types=compiled.spec_inputs.declared_types_in_order
    )
    independent = build_spec_entries(contract_cls, types=types)
    assert from_frontend == independent, (
        f"{name}: spec bytes differ.\n"
        f"frontend inventory: {[c.__name__ for c in compiled.spec_inputs.declared_types_in_order]}\n"
        f"independent inventory: {[c.__name__ for c in types]}"
    )
    # The two sides are two separate EXECUTIONS of the same source, so the
    # class objects are necessarily distinct -- identity is not the property
    # under test. What must match is the inventory: same names, same order.
    assert frontend_cls.__name__ == contract_cls.__name__
    assert [c.__name__ for c in compiled.spec_inputs.declared_types_in_order] == [
        c.__name__ for c in types
    ]


def test_the_spec_byte_match_is_order_sensitive(examples: dict[str, CompiledModule]) -> None:
    """The premise of the byte-match above: the bytes really do encode ORDER.

    Without this, `test_spec_inputs_byte_match_the_independent_collection`
    could be passing on every example purely because each one's inventory is
    order-insensitive (see that test's docstring) -- it would still catch a
    missing or extra type, but not a reordered one, while its own wording
    claimed otherwise.

    `containers` declares two structs, which is the smallest shape where the
    struct partition has an internal order to get wrong. Reversing the
    frontend's inventory must change the payload.
    """
    compiled = examples["containers"]
    contract_cls = compiled.spec_inputs.contract_cls
    assert contract_cls is not None
    inventory = compiled.spec_inputs.declared_types_in_order
    assert [c.__name__ for c in inventory] == ["Holder", "Label"], [c.__name__ for c in inventory]

    forward = build_spec_entries(contract_cls, types=inventory)
    reversed_ = build_spec_entries(contract_cls, types=tuple(reversed(inventory)))
    assert forward != reversed_, (
        "reversing a two-struct inventory did not change the spec bytes, so the "
        "byte-match cross-check is not order-sensitive after all"
    )

    # The same shape read off the decoded stream, so the failure above is
    # attributable rather than just "some bytes moved".
    def struct_names(payload: bytes) -> list[str]:
        return [
            entry.udt_struct_v0.name.decode()
            for entry in _decode_entries(payload)
            if entry.udt_struct_v0 is not None
        ]

    assert struct_names(forward) == ["Holder", "Label"]
    assert struct_names(reversed_) == ["Label", "Holder"]

    # And the ordering the frontend produced is the DECLARATION order, not the
    # alphabetical accident that happens to coincide with it here: `Label` is
    # declared second in the source, and would sort second either way, so the
    # source is the authority and this asserts against the source text.
    source, _path = example_source("containers")
    assert source.index("class Holder") < source.index("class Label")


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_spec_function_entries_match_the_ast_derived_func_irs(
    name: str, examples: dict[str, CompiledModule]
) -> None:
    """F.2.7's other half, and F.1.14's guard: the METADATA-derived spec view
    and the AST-derived `FuncIR` view must describe the same functions.

    Three things are checked at once, because all three are B9/B10 rules the
    two views could disagree about:

    * `__constructor` FIRST, then declaration order (B10);
    * the leading `env: Env` DROPPED from every signature
      (B9/`sections.py`'s own note), and `self` with it;
    * private methods and module-level helpers ABSENT from the spec entirely
      (E8: they are non-exported WASM functions, not interface entries).
    """
    compiled = examples[name]
    contract_cls = compiled.spec_inputs.contract_cls
    assert contract_cls is not None
    payload = build_spec_entries(contract_cls, types=compiled.spec_inputs.declared_types_in_order)

    spec_functions: list[tuple[str, tuple[str, ...]]] = []
    for entry in _decode_entries(payload):
        function = entry.function_v0
        if function is None:
            continue
        spec_functions.append(
            (
                function.name.sc_symbol.decode(),
                tuple(inp.name.decode() for inp in function.inputs),
            )
        )

    ir_functions: list[tuple[str, tuple[str, ...]]] = []
    contract_ir = compiled.ir.contract
    assert contract_ir is not None
    exported = [f for f in contract_ir.methods if f.kind is not FuncKind.INTERNAL]
    constructors = [f for f in exported if f.kind is FuncKind.CONSTRUCTOR]
    others = [f for f in exported if f.kind is FuncKind.EXPORT]
    for func in (*constructors, *others):
        ir_functions.append((func.export_name, tuple(n for n, _ty, _loc in func.params)))

    assert spec_functions == ir_functions, {"spec": spec_functions, "ir": ir_functions}

    # `__constructor` first when there is one (B10).
    if constructors:
        assert spec_functions[0][0] == CONSTRUCTOR_NAME

    # No `self`, no `env`, in either view.
    for _fname, inputs in spec_functions:
        assert "self" not in inputs
        assert "env" not in inputs

    # Private methods and helpers never reach the spec (E8).
    internal = {f.py_name for f in compiled.functions if f.kind is FuncKind.INTERNAL}
    assert internal.isdisjoint({fname for fname, _ in spec_functions})


def _decode_entries(payload: bytes) -> list[xdr.SCSpecEntry]:
    """Every `SCSpecEntry` in a bare, unprefixed `contractspecv0` stream.

    A `contractspecv0` payload carries no outer length prefix
    (`spec/sections.py`'s module docstring), so it is decoded by reading
    entries until the buffer is exhausted -- the same `xdrlib3.Unpacker` idiom
    `tests/unit/test_sections.py::_unpack` uses against the real wasm section.
    """
    unpacker = Unpacker(payload)
    entries: list[xdr.SCSpecEntry] = []
    while unpacker.get_position() < len(payload):
        entries.append(xdr.SCSpecEntry.unpack(unpacker))
    return entries


def test_the_spec_stream_decoder_round_trips(examples: dict[str, CompiledModule]) -> None:
    """The decoder above is load-bearing for F.2.7's second half, so it gets
    its own check: re-encoding every decoded entry reproduces the payload."""
    compiled = examples["token_style"]
    contract_cls = compiled.spec_inputs.contract_cls
    assert contract_cls is not None
    payload = build_spec_entries(contract_cls, types=compiled.spec_inputs.declared_types_in_order)
    entries = _decode_entries(payload)
    assert entries
    assert b"".join(entry.to_xdr_bytes() for entry in entries) == payload


def test_events_stay_out_of_the_declared_type_inventory(
    examples: dict[str, CompiledModule],
) -> None:
    """MJ-9: `spec_inputs.events` is a SEPARATE field, and handing an event to
    `types=` is a hard failure rather than a silent wrong spec. The refusal now
    points at the `events=` keyword that DOES take one (M1-E Task 5)."""
    compiled = examples["token_style"]
    (event,) = compiled.spec_inputs.events
    assert event.__name__ == "Transfer"
    assert event not in compiled.spec_inputs.declared_types_in_order
    contract_cls = compiled.spec_inputs.contract_cls
    assert contract_cls is not None
    with pytest.raises(SpecTypeError, match=r"pass it in `events=`"):
        build_spec_entries(
            contract_cls, types=(*compiled.spec_inputs.declared_types_in_order, event)
        )


# --- F.2.6: the host-fn <-> protocol cross-check -----------------------------


def _independent_floor(names: frozenset[str]) -> int:
    """`max(BASE_PROTOCOL, *min_protocol)` over `names`, computed here.

    Deliberately NOT a call to `_protocol.compute_protocol_floor`: the point of
    a cross-check is that two independent computations agree, so this one reads
    `min_protocol` straight off the pinned bindings and takes the max itself.
    A `None` `min_protocol` contributes `BASE_PROTOCOL`, never 0.

    **This helper is exercised on synthetic inputs too, and that is not
    optional** -- see `test_the_independent_floor_actually_discriminates`. No
    host function ANY M1-C surface can reach is gated above `BASE_PROTOCOL`
    today (`test_frontend.py::test_the_omitted_host_fn_families_are_ungated`
    depends on exactly that fact), so every example's floor is the constant 20
    and the per-example cross-checks below cannot distinguish this function
    from `lambda _: 20`. The synthetic assertions are what give them teeth.
    """
    floor = BASE_PROTOCOL
    for name in sorted(names):
        minimum = functions_by_name[name].min_protocol
        if minimum is not None and minimum > floor:
            floor = minimum
    return floor


#: A pinned binding that IS gated, used only to prove `_independent_floor`
#: discriminates. No M1-C authoring surface can reach it -- which is precisely
#: why it has to be named here rather than found in an example.
_GATED_BINDING = "bls12_381_g1_add"
_GATED_BINDING_MIN_PROTOCOL = 22

#: A pinned binding with NO `min_protocol`, which must therefore contribute
#: `BASE_PROTOCOL` rather than 0.
_UNGATED_BINDING = "put_contract_data"


def test_the_independent_floor_actually_discriminates() -> None:
    """F.2.6's premise: `_independent_floor` is a real computation, not a
    constant.

    Every example's reachable set floors at `BASE_PROTOCOL` (20), because
    nothing an M1-C contract can reach carries a `min_protocol` above it. So
    the per-example assertions below would all pass against a stubbed
    `_independent_floor` that just returned 20 -- they confirm agreement
    without confirming that anything was computed. These two synthetic inputs
    close that hole from both sides: a gated name must raise the floor to its
    own `min_protocol`, and an ungated name must contribute `BASE_PROTOCOL`
    (never 0, the bug a falsy-`None` check would produce).

    Both bindings are asserted to still be gated/ungated the way this test
    assumes, so a re-pin that changed either fact fails loudly here instead of
    quietly turning this back into a tautology.
    """
    gated = functions_by_name[_GATED_BINDING]
    assert gated.min_protocol == _GATED_BINDING_MIN_PROTOCOL, gated.min_protocol
    assert functions_by_name[_UNGATED_BINDING].min_protocol is None

    assert _independent_floor(frozenset({_GATED_BINDING})) == _GATED_BINDING_MIN_PROTOCOL
    assert _independent_floor(frozenset({_UNGATED_BINDING})) == BASE_PROTOCOL
    # The max really is a max: the gated name dominates a mixed set, in either
    # iteration order.
    assert _independent_floor(frozenset({_GATED_BINDING, _UNGATED_BINDING})) == (
        _GATED_BINDING_MIN_PROTOCOL
    )
    # ... and it agrees with `_host`'s own computation on the same synthetic
    # input, which is the cross-check the examples cannot currently exercise.
    assert _independent_floor(frozenset({_GATED_BINDING})) == declared_protocol(
        [_GATED_BINDING], None
    )
    assert _GATED_BINDING_MIN_PROTOCOL > BASE_PROTOCOL


def _has_constructor(compiled: CompiledModule) -> bool:
    """Whether the module declares a `__constructor` export (spec SS 13 / S26).

    Asked of the ASSEMBLY's own `FuncKind`, which is the same fact
    `frontend._constructor_loc` reads -- the point of the cross-check below is
    that the FEATURE gate is applied to the right modules, not that this test
    can re-detect an `__init__` from source text.
    """
    contract = compiled.ir.contract
    assert contract is not None
    return any(m.kind is FuncKind.CONSTRUCTOR for m in contract.methods)


def _independent_expected_protocol(compiled: CompiledModule) -> int:
    """`_independent_floor` over the reachable set, raised by the FEATURE gates.

    The import floor is what `_host` can compute from `HOST_FUNCTIONS`; a
    feature gate is a capability that is NOT an import, so no binding carries
    it and the max-over-bindings computation cannot see it. Today there is
    exactly one: `__constructor`, honored only from
    `CONSTRUCTOR_MIN_PROTOCOL` (spec SS 13 / CAP-0058, 2026-08-28 ruling).
    """
    floor = _independent_floor(compiled.host_fns_reachable)
    if _has_constructor(compiled):
        floor = max(floor, CONSTRUCTOR_MIN_PROTOCOL)
    return floor


def test_no_m1c_reachable_host_fn_is_gated_today(
    examples: dict[str, CompiledModule],
) -> None:
    """The constant-20 IMPORT-floor fact, recorded as an assertion rather than a
    comment.

    This is why `test_the_independent_floor_actually_discriminates` has to
    exist. It is also a genuine property worth watching: the day a re-pin (or a
    new recognized surface) puts a gated function inside M1-C's reach, this
    fails and the SPT6001 band stops being reachable only through
    `test_frontend.py`'s fake gated `HostFn`.

    The claim is about IMPORTS, so it is stated over
    `_independent_floor(host_fns_reachable)` rather than over
    `declared_protocol` -- which, since the 2026-08-28 ruling, may also carry a
    FEATURE gate that has nothing to do with whether a host function is gated.
    That other half is `test_declared_protocol_is_the_floor_over_the_reachable_set`'s.
    """
    for name, compiled in examples.items():
        assert _independent_floor(compiled.host_fns_reachable) == BASE_PROTOCOL, name
        gated = sorted(
            fn
            for fn in compiled.host_fns_reachable
            if functions_by_name[fn].min_protocol is not None
        )
        assert not gated, (name, gated)


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_declared_protocol_is_the_floor_over_the_reachable_set(
    name: str, examples: dict[str, CompiledModule]
) -> None:
    """F.2.6: `declared_protocol` equals the max `min_protocol` over
    `host_fns_reachable` raised by the feature gates, computed two ways.

    The REACHABLE set, not the used set, is the right domain: sub-plan D
    chooses between lowering forms for `MakeVec`/`MakeMap`/`MakeTopics`
    (MJ-15), so a floor computed over `host_fns_used` alone could be too low
    for what D actually emits (`frontend.py`'s module docstring).

    `_host.declared_protocol` answers only the IMPORT half -- it is fed names
    and can only see gates a binding carries -- so the second comparison adds
    the `__constructor` gate to its answer the same way `frontend` does.
    """
    compiled = examples[name]
    assert compiled.declared_protocol == _independent_expected_protocol(compiled)
    # And the same answer through `_host.declared_protocol`, which is THE
    # function sub-plan D will call for `build_env_meta` (B4), plus the feature
    # gate `_host` cannot see.
    import_answer = declared_protocol(sorted(compiled.host_fns_reachable), None)
    expected = (
        max(import_answer, CONSTRUCTOR_MIN_PROTOCOL)
        if _has_constructor(compiled)
        else import_answer
    )
    assert compiled.declared_protocol == expected


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_the_constructor_gate_splits_the_examples_two_and_three(
    name: str, examples: dict[str, CompiledModule]
) -> None:
    """The split stated absolutely, so the derivation above cannot agree with
    itself while both halves drift.

    `memoryless` and `token_style` have an `__init__`; `containers`,
    `control_flow` and `spike1_reauthored` do not. Nothing any of the five
    imports is gated, so the difference in what they declare is the FEATURE
    gate and nothing else.
    """
    compiled = examples[name]
    expected_constructor = name in {"memoryless", "token_style"}
    assert _has_constructor(compiled) is expected_constructor
    assert compiled.declared_protocol == (22 if expected_constructor else 20)


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_the_used_set_is_a_subset_of_the_reachable_set(
    name: str, examples: dict[str, CompiledModule]
) -> None:
    """`host_fns_used` is what the module DEFINITELY reaches; `reachable` adds
    the alternatives D may pick. The first can never exceed the second, and the
    floor over the subset can never exceed the floor over the superset."""
    compiled = examples[name]
    assert compiled.host_fns_used <= compiled.host_fns_reachable
    assert _independent_floor(compiled.host_fns_used) <= compiled.declared_protocol


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_no_reachable_host_fn_is_gated_outside_the_target(
    name: str, examples: dict[str, CompiledModule]
) -> None:
    """F.2.6's second half (B5): nothing in the reachable set is gated above
    the build's target or capped below it. Asserted against the bindings
    directly rather than by catching `ProtocolGateError`, so the assertion says
    which function and which bound if it ever fails."""
    compiled = examples[name]
    target = DEFAULT_TARGET_PROTOCOL
    offenders: list[str] = []
    for fn_name in sorted(compiled.host_fns_reachable):
        binding = functions_by_name[fn_name]
        if binding.min_protocol is not None and binding.min_protocol > target:
            offenders.append(f"{fn_name}: min_protocol {binding.min_protocol} > {target}")
        if binding.max_protocol is not None and binding.max_protocol < target:
            offenders.append(f"{fn_name}: max_protocol {binding.max_protocol} < {target}")
    assert not offenders, offenders


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_every_host_call_in_the_ir_is_in_the_used_set(
    name: str, examples: dict[str, CompiledModule]
) -> None:
    """The third independent derivation: every `HostCall.fn_name` in the tree
    is in `host_fns_used` (SS C.1's own one-liner), and every name in either
    set is a real pinned binding (B2)."""
    compiled = examples[name]
    in_tree = {node.fn_name for node in walk(compiled.ir) if isinstance(node, HostCall)}
    assert in_tree <= compiled.host_fns_used
    unknown = sorted(compiled.host_fns_reachable - set(functions_by_name))
    assert not unknown, unknown


# --- the 11a ruling rider ----------------------------------------------------

#: 11a ruling rider. `tests/semantics/cases.py::vec_pop_back_of_empty_traps`
#: pins the empty-`pop_back` trap, but its frozen spelling
#: (`Vec(U32).pop_back()`) calls a mutator on a TEMPORARY receiver, which the
#: frontend rejects with SPT1034 before the trap is reachable -- so the case is
#: classified `frontend="not_expressible"` (task-11a-report.md Sec.3, and the
#: controller's ruling on it). The trap IS reachable through a BOUND LOCAL, and
#: the controller's ruling directs Task 11c to keep compiler-side coverage of
#: it through that spelling. This is that coverage, differential in shape:
#: the compiler half asserts the IR, the tier-1 half asserts the trap, both
#: over the same expression.
_BOUND_LOCAL_POP_BACK = "v = Vec(U32, []); v.pop_back()"

_POP_BACK_SOURCE = '''"""11a ruling rider: the empty-pop trap, reached through a bound local."""

from serpent import U32, Env, Vec, contract


@contract
class Popper:
    def drain(self, env: Env) -> None:
        v = Vec(U32, [])
        v.pop_back()
'''


@pytest.fixture(scope="module")
def pop_back() -> CompiledModule:
    return compile_module(_POP_BACK_SOURCE, "rider/pop_back.py")


def test_the_rider_bound_local_pop_back_compiles(pop_back: CompiledModule) -> None:
    """11a ruling rider, compiler half: the bound-local spelling COMPILES,
    where the frozen temporary-receiver spelling is SPT1034.

    `pop_back()` is a MUTATOR, so it is supported only as a statement of its
    own (SPT1034's own help text: "mutate only a local this method owns, on a
    statement of its own... and C rebinds it"). That is why the rider's method
    is `-> None` and does not return the popped value: the trap this covers is
    the empty-pop, which the statement form reaches just as the frozen
    `SemCase` does.
    """
    (func,) = pop_back.functions
    assert func.export_name == "drain"
    assert func.ret == Ty.Void
    assert "vec_pop_back" in pop_back.host_fns_used


def test_the_rider_ir_shape_is_pop_back_on_an_owned_local(pop_back: CompiledModule) -> None:
    """11a ruling rider, IR-shape half: the `pop_back` is a `HostCall` whose
    receiver is the OWNED local `v`, and whose result REBINDS that same local.

    That rebinding is the whole structural difference from the rejected
    temporary-receiver form: host container operations are functional (SS C.4),
    so `v.pop_back()` lowers to `SetLocal(slot, vec_pop_back(LocalRef slot))`.
    A temporary receiver has no slot to rebind, which is exactly why
    `Vec(U32).pop_back()` cannot compile and the frozen `SemCase` is
    `not_expressible`.
    """
    (func,) = pop_back.functions
    # The local really is a local: `LetLocal` binds slot 0 to the empty Vec.
    lets = [node for node in walk(func) if isinstance(node, LetLocal)]
    assert [(let.slot, let.ty.render()) for let in lets] == [(0, "Vec(U32)")]
    assert [(slot, nm, ty.render()) for slot, nm, ty in func.locals] == [(0, "v", "Vec(U32)")]

    calls = [node for node in walk(func) if isinstance(node, HostCall)]
    pops = [call for call in calls if call.fn_name == "vec_pop_back"]
    assert len(pops) == 1, [call.fn_name for call in calls]
    (pop,) = pops
    # The result is the REBOUND container, not the popped element.
    assert pop.ty.render() == "Vec(U32)"
    # One argument, and it is a read of the bound local -- not a nested
    # construction, which is what "temporary receiver" would look like here.
    (receiver,) = pop.args
    assert isinstance(receiver, LocalRef)
    assert (receiver.slot, receiver.name) == (0, "v")

    # ... and the statement wrapping it writes slot 0 back.
    rebinds = [
        node
        for node in walk(func)
        if isinstance(node, SetLocal) and node.value is pop and node.slot == 0
    ]
    assert len(rebinds) == 1, func.body


def test_the_rider_tier1_traps_with_indexerror() -> None:
    """11a ruling rider, oracle half: the SAME expression executed directly at
    tier 1 raises `IndexError` -- the `trap` the frozen `SemCase` pins.

    Executed through `serpent.__all__` names only, the same "public root
    only" scope `tests/semantics/test_semantics.py` uses, so this half really
    is the tier-1 oracle and not a private-API shortcut.
    """
    namespace = {name: getattr(serpent, name) for name in serpent.__all__}
    v = namespace["Vec"](namespace["U32"], [])
    with pytest.raises(IndexError):
        v.pop_back()
    # The two halves are over the same expression, spelled the same way.
    assert "v.pop_back()" in _BOUND_LOCAL_POP_BACK
    assert "v.pop_back()" in _POP_BACK_SOURCE


def test_the_frozen_temporary_receiver_spelling_is_still_a_reject() -> None:
    """The rider ADDS coverage; it does not reclassify the frozen case. The
    temporary-receiver spelling must still be rejected, or
    `vec_pop_back_of_empty_traps`' `not_expressible` classification (and the
    ruling that produced this rider) would be stale.

    **The frozen case is looked up and compiled, not re-typed.** An earlier
    draft hand-wrote `Vec(U32, []).pop_back()` here -- a near-spelling with a
    second argument, not what `cases.py` actually holds
    (`Vec(U32).pop_back()`, one argument). Both are SPT1034 today, so the
    near-spelling passed while proving nothing about the frozen case; if the
    one-argument form ever stopped being a reject, this test would have stayed
    green. So it reads `case.source` straight off the frozen table and runs it
    through Task 11a's own `compile_case` harness -- one harness, not a second
    that could drift from it (BL-3).
    """
    (case,) = [c for c in CASES if c.name == "vec_pop_back_of_empty_traps"]
    # The exact frozen spelling, asserted so a table edit cannot silently
    # redirect this test at something else.
    assert case.source == "Vec(U32).pop_back()", case.source
    assert case.frontend == "not_expressible"
    assert case.not_expressible_reason is not None

    with pytest.raises(CompileError) as info:
        compile_case(case)
    assert "SPT1034" in [d.code for d in info.value.diagnostics], [
        d.code for d in info.value.diagnostics
    ]

    # And the rider's spelling is genuinely DIFFERENT from the frozen one --
    # otherwise the rider would be re-testing the reject, not covering the trap.
    assert case.source != _BOUND_LOCAL_POP_BACK
