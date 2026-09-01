"""Tests for `serpent.compiler.recognize`'s union/int-enum surface (M1-E2
Task 4): construction, the `tag()`/`payload()` reads, and the rejects.

Three obligations:

* **The IR shape.** A tagged union value IS a heterogeneous `Vec` led by the
  variant-name `Symbol` (§B.1, byte-verified), so a construction is a
  `MakeUnion` whose `items[0]` is a real `Const` the FRONTEND put there -- the
  property that makes a long variant name pool through linear memory with no
  `LiteralInventory` change at all. An int-enum case is a bare `U32` on chain,
  so it is a `Const` typed `Ty.Enum(name)` and nothing more.
* **The reads.** `tag()` is `vec_get` at 0; `payload(i, ty)` is `vec_get` at
  `i + 1`, because the index is 0-based over the PAYLOAD (ruling E2). Zero new
  host functions -- both are `vec_get`, which the container surface already
  imports, and `_lower_host_call`'s `narrow_to` gives the tag-level check for
  free.
* **The rejects, on the codes that already exist wherever one does.** A wrong
  ARITY is `SPT3020` (an arity-shaped mistake against a KNOWN shape has its
  own code); a per-slot TYPE disagreement is `SPT3018`; `for x in <union>` is
  `SPT1019` and an int enum's non-equality comparisons are `SPT3005`, both with
  no new code and no new arm anywhere -- they fall out of the two new `TyTag`s
  not being in `_ORDERABLE_TAGS`/`TyTag.VEC`. Only `payload()` misuse the
  compiler can decide statically needed a new row (`SPT3021`).
"""

from __future__ import annotations

import ast
import textwrap
from typing import Any

import pytest

from serpent.compiler import codes
from serpent.compiler.ctx import AliasTable, FuncCtx, SlotTable
from serpent.compiler.diagnostics import CompileError, Diagnostic, Diagnostics, Loc
from serpent.compiler.frontend import CompiledModule, compile_module
from serpent.compiler.ir import Const, HostCall, IRExpr, MakeUnion
from serpent.compiler.loader import LoadedModule, load_module
from serpent.compiler.recognize import recognize_attribute, recognize_call
from serpent.compiler.types_ import Ty
from serpent.emitter import BuildResult, build_wasm
from tests.harness import engine
from tests.harness.hostfns import FullHost

PATH = "contract.py"

_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

_SOURCE = '''
"""Union and int-enum recognition fixture."""
from serpent import (
    Bool, ContractEnum, ContractUnion, Env, Symbol, U32, Vec, contract,
    contractenum, contractunion, enumvalue, variant,
)


@contractunion
class Shape(ContractUnion):
    Empty = variant()
    Circle = variant(U32)
    Rect = variant(U32, U32)
    Named = variant(Symbol)


@contractenum
class Color(ContractEnum):
    Red = enumvalue(0)
    Green = enumvalue(7)


@contract
class Go:
    def go(
        self,
        env: Env,
        s: Shape,
        c: Color,
        amt: U32,
        sym: Symbol,
        items: Vec[U32],
    ) -> U32:
        return amt
'''

#: `(name, Ty)` in declaration order, `self`/`env` already dropped (§C.3).
_PARAMS: list[tuple[str, Ty]] = [
    ("s", Ty.Union("Shape")),
    ("c", Ty.Enum("Color")),
    ("amt", Ty.U32),
    ("sym", Ty.Symbol),
    ("items", Ty.Vec(Ty.U32)),
]


def _loaded() -> LoadedModule:
    loaded = load_module(_SOURCE, PATH)
    assert not loaded.diagnostics, loaded.diagnostics.diagnostics
    return loaded


_LOADED = _loaded()


def _ctx() -> FuncCtx:
    loc = Loc.whole_file(PATH)
    params = [(name, ty, loc) for name, ty in _PARAMS]
    reserved = {name: "a parameter" for name, _ in _PARAMS}
    return FuncCtx(
        loaded=_LOADED,
        sink=Diagnostics(),
        params=params,
        locals=SlotTable(reserved=reserved),
        loop_depth=0,
        return_ty=Ty.U32,
        alias_sets=AliasTable(),
        fn_name="go",
        path=PATH,
    )


def _recognize(source: str, ctx: FuncCtx) -> IRExpr | None:
    node = ast.parse(source, mode="eval").body
    if isinstance(node, ast.Call):
        return recognize_call(node, ctx)
    assert isinstance(node, ast.Attribute), source
    return recognize_attribute(node, ctx)


def _ok(source: str) -> IRExpr:
    ctx = _ctx()
    node = _recognize(source, ctx)
    assert node is not None, f"{source!r} was not recognized at all"
    assert not ctx.sink, [(d.code, d.message) for d in ctx.sink.diagnostics]
    return node


def _reject(source: str) -> Diagnostic:
    ctx = _ctx()
    node = _recognize(source, ctx)
    assert node is not None, f"{source!r} was not recognized at all"
    assert len(ctx.sink) == 1, [(d.code, d.message) for d in ctx.sink.diagnostics]
    return ctx.sink.diagnostics[0]


def _assert_reject(diag: Diagnostic, code: str, detail_substring: str) -> None:
    """`test_recognize_env.py`'s own assertion, verbatim in behavior: the code,
    the registry intent as the message PREFIX, and `detail_substring` in the
    part AFTER that prefix -- never in the intent wording, which every
    `_error()` prepends unconditionally."""
    assert diag.code == code, f"expected {code}, got {diag.code}: {diag.message}"
    intent = _INTENT[code]
    assert diag.message.startswith(intent), (
        f"{code}: message does not start with its registry intent\n"
        f"  message: {diag.message}\n  intent:  {intent}"
    )
    detail = diag.message[len(intent) :]
    assert detail_substring in detail, (
        f"{code}: {detail_substring!r} not in the code-specific detail {detail!r} "
        f"(full message: {diag.message})"
    )
    if code.startswith("SPT1"):
        assert diag.help, f"{code}: SPT1xxx diagnostics must carry help (F.2.11)"


# --- whole-module helpers (the paths that run through expr.py/stmt.py) --------

_MODULE = """
from serpent import (
    Bool, ContractEnum, ContractUnion, Env, Symbol, U32, Vec, contract,
    contractenum, contractunion, enumvalue, variant,
)


@contractunion
class Shape(ContractUnion):
    Empty = variant()
    Circle = variant(U32)
    Rect = variant(U32, U32)


@contractenum
class Color(ContractEnum):
    Red = enumvalue(0)
    Green = enumvalue(7)


@contract
class Go:
    def go(self, env: Env, s: Shape, c: Color, amt: U32, sym: Symbol) -> {ret}:
        {body}
"""


def _module(body: str, ret: str = "U32") -> str:
    return _MODULE.format(ret=ret, body=textwrap.indent(textwrap.dedent(body), "        ").strip())


def _compile(body: str, ret: str = "U32") -> CompiledModule:
    return compile_module(_module(body, ret), PATH)


def _expect_module_reject(body: str, code: str, ret: str = "U32") -> CompileError:
    with pytest.raises(CompileError) as info:
        _compile(body, ret)
    assert code in [d.code for d in info.value.diagnostics], [
        (d.code, d.message) for d in info.value.diagnostics
    ]
    return info.value


def _message_for(exc: CompileError, code: str) -> str:
    return next(d.message for d in exc.diagnostics if d.code == code)


def _diag_for(exc: CompileError, code: str) -> Diagnostic:
    return next(d for d in exc.diagnostics if d.code == code)


def _line_of(source: str, needle: str) -> int:
    """The 1-based line of `needle` in `source` -- so a located diagnostic is
    checked against the line the marker really is on, not a hardcoded number
    that the fixture's own shape could silently invalidate."""
    for lineno, line in enumerate(source.splitlines(), start=1):
        if needle in line:
            return lineno
    raise AssertionError(f"{needle!r} is not in the rendered source")


# --- construction (§B.1's on-chain shape) ------------------------------------


def test_a_union_construction_lowers_to_a_symbol_led_vec() -> None:
    ir = _ok("Shape.Rect(U32(1), U32(2))")
    assert isinstance(ir, MakeUnion)
    assert ir.ty == Ty.Union("Shape")
    assert ir.items[0] == Const(loc=ir.loc, ty=Ty.Symbol, py_value="Rect")
    assert len(ir.items) == 3  # 1 + arity, §B.1
    # The one remaining overlap between `case` and `items[0]`, pinned so it
    # cannot drift: `ty` names the UNION, `case` names the VARIANT.
    assert ir.case == ir.items[0].py_value
    assert ir.all_static is True


def test_a_unit_variant_lowers_to_a_ONE_element_vec() -> None:
    ir = _ok("Shape.Empty")
    assert isinstance(ir, MakeUnion)
    assert len(ir.items) == 1
    assert ir.items[0] == Const(loc=ir.loc, ty=Ty.Symbol, py_value="Empty")
    assert ir.case == "Empty"
    assert ir.all_static is True


def test_a_construction_with_a_dynamic_payload_is_not_static() -> None:
    ir = _ok("Shape.Circle(amt)")
    assert isinstance(ir, MakeUnion)
    assert ir.all_static is False


def test_an_int_enum_case_is_a_u32_const_typed_by_the_enum() -> None:
    """An int-enum value IS a bare `u32` on chain (§B.1), so there is no node
    of its own: the DISCRIMINANT is the value, and `Ty.Enum(name)` is what
    keeps it from being confused with a plain `U32` by the type checker."""
    ir = _ok("Color.Green")
    assert ir == Const(loc=ir.loc, ty=Ty.Enum("Color"), py_value=7)


# --- tag() and payload() (ruling E2's 0-based payload index) ------------------


def test_tag_is_a_vec_get_at_index_zero() -> None:
    ir = _ok("s.tag()")
    assert isinstance(ir, HostCall)
    assert ir.ty == Ty.Symbol
    assert ir.fn_name == "vec_get"
    assert ir.args[1] == Const(loc=ir.loc, ty=Ty.U32, py_value=0)
    assert ir.args[0].ty == Ty.Union("Shape")


def test_payload_is_a_vec_get_at_index_plus_one() -> None:
    ir = _ok("s.payload(U32(0), U32)")
    assert isinstance(ir, HostCall)
    assert ir.ty == Ty.U32
    assert ir.fn_name == "vec_get"
    assert ir.args[1] == Const(loc=ir.loc, ty=Ty.U32, py_value=1)


def test_payload_slot_one_reads_element_two() -> None:
    ir = _ok("s.payload(U32(1), U32)")
    assert isinstance(ir, HostCall)
    assert ir.args[1] == Const(loc=ir.loc, ty=Ty.U32, py_value=2)


def test_a_payload_ty_a_variant_declares_at_that_index_is_accepted() -> None:
    """The per-slot type SET, not one variant's: `Named` declares a `Symbol` at
    slot 0 and `Circle`/`Rect` declare a `U32` there, and the variant is not
    known at the call site -- so both reads are legal (ruling E2)."""
    assert _ok("s.payload(U32(0), Symbol)").ty == Ty.Symbol
    assert _ok("s.payload(U32(0), U32)").ty == Ty.U32


# --- SPT3021: the statically decidable payload() misuse -----------------------


def test_a_payload_index_at_or_above_the_widest_variant_is_SPT3021() -> None:
    """The variant is not known at the call site, but the union's MAXIMUM
    payload arity is (ruling E2), so an index no variant could have is a
    compile reject rather than a `vec_get` that traps on chain."""
    _assert_reject(_reject("s.payload(U32(2), U32)"), "SPT3021", "widest variant")


def test_a_payload_ty_no_variant_declares_at_that_index_is_SPT3021() -> None:
    """`Rect` is the only variant with a slot 1, and it declares `U32` there --
    so reading slot 1 as a `Symbol` can never succeed, however the value was
    built."""
    _assert_reject(_reject("s.payload(U32(1), Symbol)"), "SPT3021", "Symbol")
    _assert_reject(_reject("s.payload(U32(0), Bool)"), "SPT3021", "Bool")


def test_a_non_literal_payload_index_is_not_part_of_the_subset() -> None:
    """The per-slot types differ, so the index is what makes `ty` checkable at
    all; a computed index would leave both SPT3021 arms undecidable. A reject
    is allowed to be stricter than tier 1 (only ACCEPTS must be runnable)."""
    _assert_reject(_reject("s.payload(amt, U32)"), "SPT1037", "literal")


# --- the arity/type split (review M2: SPT3020 vs SPT3018) --------------------


def test_a_wrong_arity_variant_call_is_SPT3020_not_SPT3018() -> None:
    """An arity mistake against a KNOWN shape has its own code
    (`codes.py`'s SPT3020 row), and reusing SPT3018 for a non-type
    disagreement is "the wrong KIND of error" (the SPT1038 row records that
    controller ruling)."""
    _assert_reject(_reject("Shape.Rect(U32(1))"), "SPT3020", "2 payload value(s)")
    _assert_reject(_reject('Shape.Circle(Symbol("x"))'), "SPT3018", "Symbol")


def test_a_variant_keyword_argument_is_SPT3020() -> None:
    """A variant's payload is a TUPLE (§B.1), so the slots are positional --
    unlike a struct's fields, which are keyword-only because a struct is a
    `Map<Symbol, V>` whose order is the sorted one."""
    _assert_reject(_reject("Shape.Circle(radius=U32(1))"), "SPT3020", "positional")


def test_a_unit_variant_that_is_CALLED_is_SPT3020() -> None:
    """`Shape.Empty` IS the value at tier 1 (`_Unit.__get__` builds one), so
    `Shape.Empty()` calls a `Shape` instance and raises -- and `mypy --strict`
    catches it too. An accept here would not be oracle-runnable."""
    _assert_reject(_reject("Shape.Empty()"), "SPT3020", "not a call")


def test_a_payload_variant_referenced_without_its_payload_is_SPT3020() -> None:
    """The mirror image: `Shape.Circle` alone is a bound factory at tier 1, not
    a chain value."""
    _assert_reject(_reject("Shape.Circle"), "SPT3020", "1 payload value(s)")


def test_an_unknown_variant_or_member_name_is_SPT2001() -> None:
    _assert_reject(_reject("Shape.Cirlce(U32(1))"), "SPT2001", "Cirlce")
    _assert_reject(_reject("Color.Blue"), "SPT2001", "Blue")


def test_an_int_enum_member_that_is_CALLED_is_SPT3020() -> None:
    _assert_reject(_reject("Color.Red()"), "SPT3020", "not a call")


# --- the rejects that need NO new code --------------------------------------


def test_for_in_over_a_union_is_refused_with_no_new_code() -> None:
    """A union is not a `Vec` in the IR even though it is an `ScVec` on chain
    -- which is exactly why `MakeUnion` is its own node -- so the for-in
    desugar's `TyTag.VEC` test refuses it for free."""
    exc = _expect_module_reject("for x in s:\n    return amt\nreturn amt", "SPT1019")
    assert "Union(Shape) cannot be iterated" in _message_for(exc, "SPT1019")


def test_an_int_enum_compares_but_does_not_order_or_add() -> None:
    """Equality is a raw `Val` compare (an int enum is an IMMEDIATE, and not a
    `Symbol`, so no `obj_cmp`); ordering and arithmetic are refused by
    `_ORDERABLE_TAGS`/`_ARITH_TAGS` not containing the tag."""
    compiled = _compile("if c == Color.Red:\n    return U32(1)\nreturn U32(0)")
    assert "obj_cmp" not in compiled.host_fns_used
    _expect_module_reject("if c < Color.Red:\n    return U32(1)\nreturn U32(0)", "SPT3005")
    _expect_module_reject("return c + U32(1)", "SPT3003")


def test_a_union_has_no_truthiness_and_no_field_read() -> None:
    _expect_module_reject("if s:\n    return U32(1)\nreturn U32(0)", "SPT3015")
    _expect_module_reject("return s.width", "SPT1037")


# --- the frontend's own two arms (literals, needs_memory, host fns) ----------

_LONG_VARIANT_SOURCE = """
from serpent import ContractUnion, Env, U32, contract, contractunion, variant


@contractunion
class Wide(ContractUnion):
    AVeryLongVariantName = variant(U32)


@contract
class Go:
    def go(self, env: Env, amt: U32) -> Wide:
        return Wide.AVeryLongVariantName(amt)
"""


def test_a_long_variant_name_pools_through_linear_memory() -> None:
    """Because the FRONTEND puts a real `Const` in `MakeUnion.items`,
    `ir.walk`'s reflective traversal finds it with no `LiteralInventory` change
    at all -- and a tag synthesized at lowering time would be absent from the
    pool and fail interning."""
    compiled = compile_module(_LONG_VARIANT_SOURCE, PATH)
    assert "AVeryLongVariantName" in compiled.literals.symbols_over_9
    assert compiled.needs_memory
    assert "symbol_new_from_linear_memory" in compiled.host_fns_used


def test_a_static_union_reports_the_linear_memory_form_as_reachable() -> None:
    """`_bulk_construction_can_use_memory` joins `MakeUnion` to the
    `(MakeVec, MakeMap)` arm -- `return node.all_static` -- rather than
    recomputing `MakeTopics`' every-item-a-`Const` test, and `_collect_host_fns`
    notes the vec-build trio as REACHABLE (D chooses the form), never certain.
    """
    compiled = _compile("return U32(1)")  # baseline: no union construction
    assert "vec_new_from_linear_memory" not in compiled.host_fns_reachable

    static = _compile("_ = Shape.Rect(U32(1), U32(2))\nreturn U32(1)")
    assert {"vec_new", "vec_push_back", "vec_new_from_linear_memory"} <= (static.host_fns_reachable)
    assert static.needs_memory is True


def test_a_dynamic_union_construction_needs_no_memory_of_its_own() -> None:
    dynamic = _compile("_ = Shape.Circle(amt)\nreturn U32(1)")
    assert dynamic.needs_memory is False


# --- the WASM leg -----------------------------------------------------------

_WASM_SOURCE = """
from serpent import (
    ContractEnum, ContractUnion, Env, Symbol, U32, contract, contractenum,
    contractunion, enumvalue, variant,
)


@contractunion
class Shape(ContractUnion):
    Empty = variant()
    Circle = variant(U32)
    Rect = variant(U32, U32)


@contractenum
class Color(ContractEnum):
    Red = enumvalue(0)
    Green = enumvalue(7)


@contract
class Shapes:
    def circle(self, env: Env, r: U32) -> Shape:
        return Shape.Circle(r)

    def empty(self, env: Env) -> Shape:
        return Shape.Empty

    def name_of(self, env: Env, s: Shape) -> Symbol:
        return s.tag()

    def radius(self, env: Env, s: Shape) -> U32:
        return s.payload(U32(0), U32)

    def green(self, env: Env) -> Color:
        return Color.Green
"""

#: The host functions the whole union/int-enum surface reaches. `vec_get` is
#: the container surface's own row and the two constructors are `Vec(...)`'s,
#: so §B.1's "zero new host functions" is an assertion, not a hope.
_WASM_IMPORTS: frozenset[str] = frozenset(
    {"vec_new", "vec_push_back", "vec_new_from_linear_memory", "vec_get", "fail_with_error"}
)


@pytest.fixture(scope="module")
def built() -> BuildResult:
    return build_wasm(compile_module(_WASM_SOURCE, PATH))


def test_the_union_surface_imports_no_new_host_function(built: BuildResult) -> None:
    assert set(built.imports) <= _WASM_IMPORTS
    assert "vec_get" in built.imports


def _invoke(mini: engine.MiniHost, export: str, *args: int) -> int:
    """`invoke` for the exports here, every one of which returns a `Val`."""
    result = mini.invoke(export, *args)
    assert result is not None, f"{export} returned no value"
    return result


def test_a_union_returning_and_a_union_taking_export_run(built: BuildResult) -> None:
    """The end-to-end leg: a union built in one export, read back through
    `tag()`/`payload()` in another, with the ABI prologue's `TAG_VEC_OBJECT`
    check in between."""
    from serpent import val

    host = FullHost()
    mini = engine.MiniHost(built.wasm, imports=host.bindings())
    host.attach(mini)

    handle = _invoke(mini, "circle", val.pack_u32val(7))
    assert val.tag_of(handle) == val.TAG_VEC_OBJECT
    assert _invoke(mini, "name_of", handle) == val.symbol_small("Circle")
    assert _invoke(mini, "radius", handle) == val.pack_u32val(7)

    unit = _invoke(mini, "empty")
    assert _invoke(mini, "name_of", unit) == val.symbol_small("Empty")

    # An int enum crosses the ABI as the bare `u32` it is on chain.
    assert _invoke(mini, "green") == val.pack_u32val(7)


def test_a_union_taking_export_refuses_the_wrong_tag(built: BuildResult) -> None:
    """E14's prologue over `Ty.Union`: the check is `TAG_VEC_OBJECT`'s, so a
    map handle (a struct's tag) is refused rather than read as a union."""
    from serpent import errors, val

    host = FullHost()
    mini = engine.MiniHost(built.wasm, imports=host.bindings())
    host.attach(mini)
    with pytest.raises(engine.HostError) as info:
        mini.invoke("name_of", val.pack_u32val(1))
    assert info.value.val == val.error_val(errors.CODE_ABI_CHECK_FAILED)


def test_the_fixture_source_declares_what_these_tests_assume() -> None:
    """A guard on the fixtures themselves: every assertion above reads the
    variant arities and the discriminants out of `_SOURCE`, so a silent edit
    there would weaken the suite rather than fail it."""
    metadata: dict[str, Any] = {
        decl.name: decl.metadata for decl in _LOADED.decorated_types_in_order
    }
    assert metadata["Shape"]["cases"] == [
        ("Empty", ()),
        ("Circle", (_named("U32"),)),
        ("Rect", (_named("U32"), _named("U32"))),
        ("Named", (_named("Symbol"),)),
    ]
    assert metadata["Color"]["cases"] == [("Red", 0), ("Green", 7)]


def _named(name: str) -> object:
    """The chain-type class `name` refers to in the fixture's own namespace."""
    return _LOADED.namespace[name]


# --- SPT3022: a tag() comparison against a Symbol naming no variant ----------
#
# F.1.7 calls this "the single highest-value NEW diagnostic in the surface", and
# the reason is what happens WITHOUT it: `Symbol` equality compiles (§C.6), so a
# typo'd case name is a permanently dead branch that compiles, deploys, and
# quietly takes the fallthrough forever. The variant-name set is statically
# known at the comparison, so the compiler can see it and nothing else can.


def test_a_tag_comparison_against_an_unknown_variant_is_SPT3022() -> None:
    body = 'if s.tag() == Symbol("Cirlce"):\n    return U32(1)\nreturn U32(0)'
    exc = _expect_module_reject(body, "SPT3022")
    diag = _diag_for(exc, "SPT3022")
    assert diag.loc.line == _line_of(_module(body), "Cirlce")
    assert "Cirlce" in diag.message
    assert "Circle" in (diag.help or ""), "the help must name the variants that DO exist"


def test_the_unknown_variant_is_refused_in_either_operand_order() -> None:
    """The check is over the checked IR, not the source order, so which side the
    author wrote the literal on cannot change the answer."""
    body = 'if Symbol("Cirlce") == s.tag():\n    return U32(1)\nreturn U32(0)'
    exc = _expect_module_reject(body, "SPT3022")
    assert "Cirlce" in _message_for(exc, "SPT3022")


def test_a_tag_comparison_against_a_real_variant_compiles() -> None:
    for spelling in (
        'if s.tag() == Symbol("Circle"):\n    return U32(1)\nreturn U32(0)',
        'if Symbol("Empty") == s.tag():\n    return U32(1)\nreturn U32(0)',
        'if s.tag() != Symbol("Rect"):\n    return U32(1)\nreturn U32(0)',
    ):
        assert _compile(spelling) is not None


def test_a_plain_symbol_comparison_is_untouched() -> None:
    """The no-false-positive half: the check fires only when one side really is
    the `vec_get`-at-0 read on a `Ty.Union` receiver that `_recognize_union_read`
    builds. A `Symbol` compared against anything else -- a parameter, another
    `Symbol` literal, another tag read -- is none of this rule's business, and a
    Symbol that happens not to name a variant is a perfectly ordinary value."""
    for spelling in (
        'if sym == Symbol("Cirlce"):\n    return U32(1)\nreturn U32(0)',
        'if Symbol("Cirlce") == sym:\n    return U32(1)\nreturn U32(0)',
        "if sym == s.tag():\n    return U32(1)\nreturn U32(0)",
        "if s.tag() == sym:\n    return U32(1)\nreturn U32(0)",
        "if s.tag() == s.tag():\n    return U32(1)\nreturn U32(0)",
    ):
        assert _compile(spelling) is not None


def test_an_element_read_at_index_zero_of_a_real_vec_is_untouched() -> None:
    """The narrower half of the same guard: `items.get(U32(0))` is also a
    `vec_get` at 0, and it is NOT a tag read -- the receiver's `Ty` is what
    tells the two apart, so a `Vec` element compared against a Symbol literal
    must stay compilable."""
    source = _MODULE.format(
        ret="U32",
        body='if syms.get(U32(0)) == Symbol("Cirlce"):\n            return U32(1)\n        return U32(0)',
    ).replace("sym: Symbol", "sym: Symbol, syms: Vec[Symbol]")
    assert compile_module(source, PATH) is not None
