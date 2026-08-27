"""Tests for `serpent.compiler.ir` and `serpent.compiler.expr` (Task 5).

The table-driven core mirrors dossier SS B.2 (the expression inventory, BINDING)
row by row: a SUPPORT row asserts the exact IR shape it lowers to -- including
`ty` and, for comparisons, `via_obj_cmp` (F.1.2's divergence guard) -- and a
REJECT row asserts the registry `code`, a message substring, and a non-empty
`help`.

Three obligations get their own sections:

* **The 17 `kind="reject"` cases of `tests/semantics/cases.py`** (T1/T6): their
  sources appear VERBATIM here, because C is the only tier that can prove the
  reject side.
* **`Symbol` comparison routes through `obj_cmp`** (T5/F.1.2): the single
  highest-value IR pin in this task.
* **Exhaustive dispatch** (MJ-11): a synthetic `ast.expr` subclass no table row
  covers must produce the catch-all diagnostic, never a traceback.
"""

from __future__ import annotations

import ast
import builtins
import dataclasses
import time
from dataclasses import replace

import pytest

from serpent.compiler import codes
from serpent.compiler.ctx import AliasTable, FuncCtx, SlotTable
from serpent.compiler.diagnostics import Diagnostic, Diagnostics, Loc
from serpent.compiler.expr import (
    NODE_KIND_CODES,
    check_condition,
    check_expr,
    fold_literal,
)
from serpent.compiler.ir import (
    Binary,
    BinaryOp,
    BoolOp,
    BoolOpKind,
    Compare,
    CompareOp,
    Const,
    ConstRef,
    FuncKind,
    HostCall,
    IfExp,
    IsZero,
    LocalRef,
    ParamRef,
    Unary,
    UnaryOp,
)
from serpent.compiler.loader import LoadedModule, load_module
from serpent.compiler.types_ import Ty

PATH = "contract.py"

_ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"

#: `code -> message_intent`, so every REJECT assertion can check that the
#: diagnostic's message really carries its registry row's own wording.
_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}


# --- ir.py: the SS C.2 node inventory ----------------------------------------

#: Every name dossier SS C.2 lists, plus the two bases/enums the dataclasses
#: need. Tasks 6-10 IMPORT these; a missing node is what forces a later task to
#: invent one, which is what this golden prevents.
_SS_C2_NODES: frozenset[str] = frozenset(
    {
        # expressions
        "Const",
        "ParamRef",
        "LocalRef",
        "ConstRef",
        "Unary",
        "Binary",
        "Compare",
        "BoolOp",
        "IfExp",
        "IsZero",
        "MakeStruct",
        "FieldGet",
        "MakeVec",
        "MakeMap",
        "MakeTopics",
        "HostCall",
        "RawScalar",
        "ErrorVal",
        "Convert",
        "InternalCall",
        # statements
        "LetLocal",
        "SetLocal",
        "Eval",
        "If",
        "While",
        "Break",
        "Continue",
        "Raise",
        "Return",
        "Nop",
        # declarations
        "ModuleIR",
        "StructDecl",
        "ErrorEnumDecl",
        "EventDecl",
        "ConstDecl",
        "FuncIR",
        "ContractIR",
    }
)


def test_ir_exports_every_dossier_c2_node() -> None:
    from serpent.compiler import ir

    exported = set(ir.__all__)
    assert _SS_C2_NODES <= exported, sorted(_SS_C2_NODES - exported)
    for name in _SS_C2_NODES:
        assert hasattr(ir, name), name


def test_ir_nodes_are_frozen_and_compare_by_value() -> None:
    loc = Loc.whole_file(PATH)
    one = Const(loc=loc, ty=Ty.U32, py_value=5)
    same = Const(loc=loc, ty=Ty.U32, py_value=5)
    other = Const(loc=loc, ty=Ty.U32, py_value=6)
    assert one == same
    assert one != other
    assert hash(one) == hash(same)
    with pytest.raises(dataclasses.FrozenInstanceError):
        one.py_value = 6  # type: ignore[misc]


def test_walk_yields_every_nested_node() -> None:
    """SS C.1's protocol-floor one-liner depends on this traversal."""
    from serpent.compiler.ir import walk

    node = _ok("len(v) + len(m)")
    assert isinstance(node, Binary)
    host_calls = sorted(n.fn_name for n in walk(node) if isinstance(n, HostCall))
    assert host_calls == ["map_len", "vec_len"]
    assert node in list(walk(node))


def test_walk_descends_into_statement_bodies() -> None:
    from serpent.compiler.ir import Eval, If, Return, walk

    loc = Loc.whole_file(PATH)
    inner = Const(loc=loc, ty=Ty.U32, py_value=1)
    stmt = If(
        loc=loc,
        cond=Const(loc=loc, ty=Ty.Bool, py_value=True),
        body=(Return(loc=loc, value=inner),),
        orelse=(Eval(loc=loc, value=Const(loc=loc, ty=Ty.Void, py_value=None)),),
    )
    assert inner in list(walk(stmt))
    assert len(list(walk(stmt))) == 6


# --- the fixture module every expression is checked against ------------------

_SOURCE = '''
"""A fixture contract exercising every scalar annotation shape."""

from serpent import (
    Address,
    Bool,
    Bytes,
    Bytes32,
    Bytes64,
    Duration,
    Env,
    Event,
    I32,
    I64,
    I128,
    Map,
    String,
    Symbol,
    Timepoint,
    U32,
    U64,
    U128,
    Vec,
    bytes_n,
    contract,
    contracterror,
    contractevent,
    contracttype,
    errorcode,
)

ADMIN = Symbol("ADMIN")
LIMIT = U32(100)


@contracttype
class Balance:
    amount: U32
    owner: Address


@contracterror
class Err:
    NotFound = errorcode(1)
    NotAuthorized = errorcode(2)


@contractevent
class Transfer(Event):
    amount: U32


def helper(x: U32) -> U32:
    return x


@contract
class C:
    def go(
        self,
        env: Env,
        a: U32,
        b: U32,
        c: U64,
        d: I32,
        e: I64,
        f: U128,
        g: I128,
        t: Timepoint,
        dur: Duration,
        s: Symbol,
        st: String,
        by: Bytes,
        b32: Bytes32,
        ad: Address,
        v: Vec[U32],
        m: Map[Symbol, U32],
        bal: Balance,
        flag: Bool,
        b64: Bytes64,
        oad: Address | None,
        osym: Symbol | None,
        ovec: Vec[U32] | None,
        ou32: U32 | None,
    ) -> U32:
        return a
'''

#: The `go` parameter list, in declaration order, with `self` and the leading
#: `Env` dropped (SS C.3's Function scope) -- exactly what a `FuncCtx` carries.
_PARAMS: list[tuple[str, Ty]] = [
    ("a", Ty.U32),
    ("b", Ty.U32),
    ("c", Ty.U64),
    ("d", Ty.I32),
    ("e", Ty.I64),
    ("f", Ty.U128),
    ("g", Ty.I128),
    ("t", Ty.Timepoint),
    ("dur", Ty.Duration),
    ("s", Ty.Symbol),
    ("st", Ty.String),
    ("by", Ty.Bytes),
    ("b32", Ty.BytesN(32)),
    ("ad", Ty.Address),
    ("v", Ty.Vec(Ty.U32)),
    ("m", Ty.Map(Ty.Symbol, Ty.U32)),
    ("bal", Ty.Struct("Balance")),
    ("flag", Ty.Bool),
    ("b64", Ty.BytesN(64)),
    ("oad", Ty.Option(Ty.Address)),
    ("osym", Ty.Option(Ty.Symbol)),
    ("ovec", Ty.Option(Ty.Vec(Ty.U32))),
    ("ou32", Ty.Option(Ty.U32)),
]

_PARAM_INDEX: dict[str, int] = {name: i for i, (name, _) in enumerate(_PARAMS)}


def _loaded() -> LoadedModule:
    loaded = load_module(_SOURCE, PATH)
    assert not loaded.diagnostics, loaded.diagnostics.diagnostics
    return loaded


_LOADED = _loaded()


def _ctx() -> FuncCtx:
    """A fresh `FuncCtx` (fresh sink) over the fixture module.

    `total: U32` is pre-declared as local slot 0 so `LocalRef` has something
    to resolve to without this task reaching into Task 6's binding logic.
    """
    loc = Loc.whole_file(PATH)
    params = [(name, ty, loc) for name, ty in _PARAMS]
    reserved = {name: "a parameter" for name, _ in _PARAMS}
    slots = SlotTable(reserved=reserved)
    sink = Diagnostics()
    slots.declare("total", Ty.U32, loc, sink)
    assert not sink
    return FuncCtx(
        loaded=_LOADED,
        sink=sink,
        params=params,
        locals=slots,
        loop_depth=0,
        return_ty=Ty.U32,
        alias_sets=AliasTable(),
        fn_name="go",
        path=PATH,
        # `go` is a method of the fixture's @contract class, so `self` is in
        # scope inside it -- identity, not a name lookup (Task 8 fix round 1).
        fn_kind=FuncKind.EXPORT,
        has_self=True,
    )


def _parse(source: str) -> ast.expr:
    return ast.parse(source, mode="eval").body


def _check(source: str, *, expected: Ty | None = None) -> tuple[object, Diagnostics]:
    ctx = _ctx()
    node = check_expr(_parse(source), ctx, expected=expected)
    return node, ctx.sink


def _ok(source: str, *, expected: Ty | None = None) -> object:
    """Check `source`, asserting it produced NO diagnostics."""
    node, sink = _check(source, expected=expected)
    assert not sink, [d.message for d in sink.diagnostics]
    return node


def _reject(source: str, *, expected: Ty | None = None) -> Diagnostic:
    """Check `source`, asserting exactly one diagnostic, and return it."""
    _, sink = _check(source, expected=expected)
    assert len(sink) == 1, [(d.code, d.message) for d in sink.diagnostics]
    return sink.diagnostics[0]


def _assert_reject(diag: Diagnostic, code: str, substring: str) -> None:
    assert diag.code == code, f"expected {code}, got {diag.code}: {diag.message}"
    assert _INTENT[code] in diag.message, (
        f"{code}: message does not carry its registry intent\n  message: {diag.message}\n"
        f"  intent:  {_INTENT[code]}"
    )
    assert substring in diag.message or any(substring in n for n in diag.notes), (
        f"{code}: {substring!r} not in message/notes: {diag.message} {diag.notes}"
    )
    assert diag.help, f"{code}: every reject this task raises carries a help rewrite"
    assert diag.loc.path == PATH


# --- Constant: literal coercion in a typed position (S3), no folding (F.1.10) --


@pytest.mark.parametrize(
    ("source", "expected", "ty", "value"),
    [
        ("U32(5)", None, Ty.U32, 5),
        ("U32(0)", None, Ty.U32, 0),
        ("U32(2**32 - 1)", None, Ty.U32, 2**32 - 1),
        ("I32(-(2**31))", None, Ty.I32, -(2**31)),
        ("I32(2**31 - 1)", None, Ty.I32, 2**31 - 1),
        ("U64(2**40)", None, Ty.U64, 2**40),
        ("I64(-(2**63))", None, Ty.I64, -(2**63)),
        ("U128(2**128 - 1)", None, Ty.U128, 2**128 - 1),
        ("I128(-(2**127))", None, Ty.I128, -(2**127)),
        ("Timepoint(5)", None, Ty.Timepoint, 5),
        ("Duration(3)", None, Ty.Duration, 3),
        ("Bool(True)", None, Ty.Bool, True),
        ("Bool(False)", None, Ty.Bool, False),
        ('Symbol("transfer")', None, Ty.Symbol, "transfer"),
        ('Symbol("a" * 32)', None, Ty.Symbol, "a" * 32),
        ('String("hello, world")', None, Ty.String, "hello, world"),
        ('Bytes(b"abc")', None, Ty.Bytes, b"abc"),
        ('Bytes32(b"\\x00" * 32)', None, Ty.BytesN(32), b"\x00" * 32),
        (f'Address("{_ACCOUNT}")', None, Ty.Address, _ACCOUNT),
        # Bare literals in a typed position (the `expected` argument).
        ("5", Ty.U32, Ty.U32, 5),
        ("True", Ty.Bool, Ty.Bool, True),
        ("False", Ty.Bool, Ty.Bool, False),
        ('"abc"', Ty.Symbol, Ty.Symbol, "abc"),
        ('b"abc"', Ty.Bytes, Ty.Bytes, b"abc"),
        ("None", Ty.Option(Ty.U32), Ty.Option(Ty.U32), None),
    ],
)
def test_literal_support(source: str, expected: Ty | None, ty: Ty, value: object) -> None:
    node = _ok(source, expected=expected)
    assert isinstance(node, Const)
    assert node.ty == ty
    assert node.py_value == value
    assert type(node.py_value) is type(value)


@pytest.mark.parametrize(
    ("source", "code", "substring"),
    [
        # S3's compile-time bounds checks, on literal COERCION only.
        ("U32(2**32)", "SPT3004", "out of range for U32"),
        ("U32(-1)", "SPT3004", "out of range for U32"),
        ("I32(2**31)", "SPT3004", "out of range for I32"),
        ("U64(2**64)", "SPT3004", "out of range for U64"),
        ("U128(2**128)", "SPT3004", "out of range for U128"),
        # Symbol/String/Bytes literal size + charset validation.
        ('Symbol("")', "SPT3004", "not a valid Symbol"),
        ('Symbol("a" * 33)', "SPT3004", "not a valid Symbol"),
        ('Symbol("bad-char")', "SPT3004", "not a valid Symbol"),
        ('Bytes32(b"x")', "SPT3004", "takes exactly 32 bytes"),
        ('Bytes64(b"x" * 10)', "SPT3004", "takes exactly 64 bytes"),
        ('Address("not-a-strkey")', "SPT3004", "not an account"),
        # Wrong literal KIND for the target type.
        ('U32("x")', "SPT3018", "takes an int"),
        ("Bool(1)", "SPT3018", "takes a bool"),
        ("Symbol(5)", "SPT3018", "takes a str"),
        ('Bytes("abc")', "SPT3018", "takes bytes"),
    ],
)
def test_literal_reject(source: str, code: str, substring: str) -> None:
    _assert_reject(_reject(source), code, substring)


def test_no_expression_result_folding() -> None:
    """F.1.10: bounds checks apply to literal COERCION, never to an
    expression RESULT -- `I32(MAX) + I32(1)` type-checks and overflows at
    runtime (`cases.py:i32_max_plus_one_overflows` is a `contract_error`, not
    a `reject`), while `U32(2**32)` is a compile reject."""
    node = _ok("I32(2**31 - 1) + I32(1)")
    assert isinstance(node, Binary)
    assert node.ty == Ty.I32
    assert isinstance(node.lhs, Const) and node.lhs.py_value == 2**31 - 1
    assert isinstance(node.rhs, Const) and node.rhs.py_value == 1


@pytest.mark.parametrize(
    ("source", "wrap"),
    [
        ("5", "U32(5)"),
        ("True", "Bool(True)"),
        ("False", "Bool(False)"),
        ('"abc"', "Symbol("),
        ('b"abc"', "Bytes(b"),
        ("2**32", "U32("),
    ],
)
def test_bare_literal_without_a_chain_type_is_rejected(source: str, wrap: str) -> None:
    """MJ-12/SS B.2: a bare literal with no chain type in scope names the wrap."""
    diag = _reject(source)
    _assert_reject(diag, "SPT3008", "")
    assert wrap in (diag.help or "") or wrap in diag.message, (diag.message, diag.help)


def test_true_literal_coerces_in_condition_position() -> None:
    """MJ-12: `while True:` is SUPPORT -- the coercion happens here."""
    ctx = _ctx()
    node = check_condition(_parse("True"), ctx)
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert isinstance(node, Const)
    assert node.ty == Ty.Bool
    assert node.py_value is True


# --- fold_literal: plain-Python literal arithmetic, never chain values -------


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("5", 5),
        ("2**32", 2**32),
        ("-(2**31)", -(2**31)),
        ("2**31 - 1", 2**31 - 1),
        ("1 << 40", 1 << 40),
        ('"a" * 33', "a" * 33),
        ('b"x" * 10', b"x" * 10),
        ('b"\\x00" * 32', b"\x00" * 32),
        ('"ab" + "cd"', "abcd"),
    ],
)
def test_fold_literal_accepts_plain_literal_arithmetic(source: str, value: object) -> None:
    assert fold_literal(_parse(source)) == value


@pytest.mark.parametrize(
    "source",
    [
        "a",  # a name
        "U32(5)",  # a chain value -- F.1.10 forbids folding these
        "U32(5) + U32(1)",
        "True",  # bool is not an int literal here (T2)
        "5 // 0",  # not evaluable
        "None",
    ],
)
def test_fold_literal_declines(source: str) -> None:
    assert fold_literal(_parse(source)) is None


@pytest.mark.parametrize(
    "source",
    [
        "2 ** 10**9",  # would allocate until the process dies
        "(2**512)**512",  # nested, past the bit cap
        '("a" * 65536) * 65536',  # nested, past the length cap
        "1 << 10**9",
    ],
)
def test_fold_literal_declines_absurd_sizes(source: str) -> None:
    """A typo must not turn constant folding into a denial of service."""
    assert fold_literal(_parse(source)) is None
    _, sink = _check(source)
    assert len(sink) == 1


@pytest.mark.parametrize(
    "source",
    [
        "U32(2**4096)",
        "U64(2 ** 10**9)",
        'String("a" * 100000)',
        'Symbol("a" * 100000)',
        "2**4096",
    ],
)
def test_size_declined_literal_reports_the_size_story(source: str) -> None:
    """A literal the folding caps declined must report the LITERAL's size, not
    an omitted operator: `U32(2**4096)` misuses no operator."""
    diag = _reject(source)
    _assert_reject(diag, "SPT3004", "too large")
    assert "4096 bits" in " ".join(diag.notes)


def test_pow_is_pre_guarded_before_computing() -> None:
    """The size check runs BEFORE the multiplication, so a 871K-bit literal is
    declined rather than computed and then thrown away."""
    start = time.perf_counter()
    assert fold_literal(_parse("(2**1024)**851")) is None
    assert time.perf_counter() - start < 0.5


def test_a_literal_just_under_the_cap_still_folds() -> None:
    """The Pow pre-guard uses a provable LOWER bound on the result width, so it
    declines only what really exceeds the cap."""
    assert fold_literal(_parse("2**4000")) == 2**4000


def test_help_truncates_a_huge_literal_repr() -> None:
    """A 100 KB literal must not produce a 131 KB error message."""
    diag = _reject('b"' + "a" * 100_000 + '"')
    assert diag.code == "SPT3008"
    rendered = diag.message + (diag.help or "") + "".join(diag.notes)
    assert len(rendered) < 1000, len(rendered)
    assert "..." in (diag.help or "")


# --- Name resolution ---------------------------------------------------------


def test_param_ref() -> None:
    node = _ok("a")
    assert isinstance(node, ParamRef)
    assert node.index == _PARAM_INDEX["a"]
    assert node.name == "a"
    assert node.ty == Ty.U32


def test_param_ref_of_every_declared_type() -> None:
    for name, ty in _PARAMS:
        node = _ok(name)
        assert isinstance(node, ParamRef)
        assert node.ty == ty


def test_local_ref() -> None:
    node = _ok("total")
    assert isinstance(node, LocalRef)
    assert node.slot == 0
    assert node.name == "total"
    assert node.ty == Ty.U32


@pytest.mark.parametrize(
    ("source", "ty"),
    [("ADMIN", Ty.Symbol), ("LIMIT", Ty.U32)],
)
def test_module_const_ref(source: str, ty: Ty) -> None:
    node = _ok(source)
    assert isinstance(node, ConstRef)
    assert node.name == source
    assert node.ty == ty


def test_self_use_is_rejected() -> None:
    _assert_reject(_reject("self"), "SPT2002", "self")


def test_unresolved_name_is_rejected() -> None:
    _assert_reject(_reject("nope"), "SPT2001", "nope")


@pytest.mark.parametrize("source", ["self.total", "self.admin", "self.value"])
def test_self_attribute_read_is_rejected_as_state(source: str) -> None:
    """`self.<attr>` is a STATE read -- SS C.3's scope rule and SPT2002's own
    intent ("contract state lives in storage, not on self") -- never a
    deferred surface with a false Task-7b promise."""
    diag = _reject(source)
    _assert_reject(diag, "SPT2002", "self")
    assert "storage" in (diag.help or "")


def test_a_self_method_call_is_resolved_against_the_contract() -> None:
    """A private METHOD is E8-supported (an `InternalCall` on a non-exported
    wasm function), which Task 8 landed -- so `self.<attr>(...)` is resolved
    against the contract's own methods rather than deferred. This module's
    fixture contract declares no `_helper`, so the honest answer here is
    "not a method of this contract"; the supported and rejected E8 shapes
    (a private method, an export called through `self`) are covered in
    `test_decls.py`, which is where a declaration table exists."""
    diag = _reject("self._helper(a)")
    _assert_reject(diag, "SPT2001", "_helper")


def test_bare_chain_type_name_in_a_value_position_is_rejected() -> None:
    _assert_reject(_reject("U32"), "SPT3014", "U32")


def test_annotation_generic_in_a_value_position_is_rejected() -> None:
    _assert_reject(_reject("Vec[U32]"), "SPT3014", "Vec")


# --- Attribute ---------------------------------------------------------------


def test_error_member_outside_raise_position_is_rejected() -> None:
    """S8: an error case is not a value."""
    _assert_reject(_reject("Err.NotFound"), "SPT3002", "NotFound")


def test_unknown_error_member_is_rejected() -> None:
    _assert_reject(_reject("Err.Bogus"), "SPT2001", "Bogus")


@pytest.mark.parametrize(
    ("source", "prop"),
    [
        ("a.value", "value"),
        ("s.text", "text"),
        ("by.data", "data"),
        ("ad.strkey", "strkey"),
        ("ad.is_account", "is_account"),
        ("f.hi64", "hi64"),
        ("f.lo64", "lo64"),
        ("v.element_type", "element_type"),
    ],
)
def test_chain_type_introspection_property_is_rejected(source: str, prop: str) -> None:
    _assert_reject(_reject(source), "SPT1016", prop)


# --- Constructors with runtime arguments (P4) --------------------------------


def test_bool_of_a_comparison_is_the_comparison(  # token_style.py:87's shape
) -> None:
    node = _ok("Bool(a == b)")
    assert isinstance(node, Compare)
    assert node.ty == Ty.Bool


def test_same_type_constructor_of_a_runtime_value_is_the_identity() -> None:
    """`U32(len(v))` must compile: `len()` is `int` at tier 1 and `U32` in the
    compiler (E19/F.1.4), so a same-type constructor cannot be a reject."""
    node = _ok("U32(len(v))")
    assert isinstance(node, HostCall)
    assert node.fn_name == "vec_len"
    assert node.ty == Ty.U32


def test_cross_type_constructor_of_a_runtime_value_is_rejected() -> None:
    _assert_reject(_reject("U32(c)"), "SPT3018", "U32")


def test_keyword_argument_to_a_constructor_is_rejected() -> None:
    _assert_reject(_reject("U32(value=5)"), "SPT1035", "value")


@pytest.mark.parametrize("source", ["U32()", "U32(1, 2)", "Symbol()", "Bool(True, False)"])
def test_constructor_arity_is_checked(source: str) -> None:
    """A chain-type constructor takes exactly one payload argument. This had
    no registry row in the first round and was falling to MJ-11's catch-all,
    whose "not supported by the serpent subset" wording is wrong for a
    construct that IS supported and merely miscalled."""
    diag = _reject(source)
    _assert_reject(diag, "SPT3020", "exactly one argument")


# --- BinOp: A4's contract, statically ---------------------------------------


@pytest.mark.parametrize(
    ("source", "op", "ty"),
    [
        ("a + b", BinaryOp.ADD, Ty.U32),
        ("a - b", BinaryOp.SUB, Ty.U32),
        ("a * b", BinaryOp.MUL, Ty.U32),
        ("a // b", BinaryOp.FLOORDIV, Ty.U32),
        ("a % b", BinaryOp.MOD, Ty.U32),
        ("d + d", BinaryOp.ADD, Ty.I32),
        ("c + c", BinaryOp.ADD, Ty.U64),
        ("e - e", BinaryOp.SUB, Ty.I64),
        ("f * f", BinaryOp.MUL, Ty.U128),
        ("g // g", BinaryOp.FLOORDIV, Ty.I128),
    ],
)
def test_arithmetic_support(source: str, op: BinaryOp, ty: Ty) -> None:
    node = _ok(source)
    assert isinstance(node, Binary)
    assert node.op is op
    assert node.ty == ty


@pytest.mark.parametrize(
    ("source", "literal_side"),
    [("a + 10", "rhs"), ("10 + a", "lhs")],
)
def test_in_range_int_literal_operand_coerces_either_side(source: str, literal_side: str) -> None:
    """A6: an in-range plain `int` operand coerces on either side."""
    node = _ok(source)
    assert isinstance(node, Binary)
    literal = node.rhs if literal_side == "rhs" else node.lhs
    other = node.lhs if literal_side == "rhs" else node.rhs
    assert isinstance(literal, Const)
    assert literal.ty == Ty.U32
    assert literal.py_value == 10
    assert isinstance(other, ParamRef)
    assert node.ty == Ty.U32


@pytest.mark.parametrize(
    ("source", "code", "substring"),
    [
        # Out-of-range int operand (S3/A6).
        ("a + 2**32", "SPT3004", "out of range for U32"),
        # Cross-width and cross-signedness (T1).
        ("a + c", "SPT3003", "U64"),
        ("d + e", "SPT3003", "I64"),
        ("a + f", "SPT3003", "U128"),
        # bool as an int operand (T2/D4/F.1.6).
        ("a + True", "SPT3003", "bool"),
        ("True + a", "SPT3003", "bool"),
        ("flag + a", "SPT3003", "Bool"),
        # Omitted operators (A5/D2).
        ("a ** b", "SPT3005", "**"),
        ("a & b", "SPT3005", "&"),
        ("a | b", "SPT3005", "|"),
        ("a ^ b", "SPT3005", "^"),
        ("a << b", "SPT3005", "<<"),
        ("a >> b", "SPT3005", ">>"),
        ("a @ b", "SPT3005", "@"),
        # True divide names the `//` rewrite.
        ("a / b", "SPT3006", "/"),
        # Timepoint/Duration have no arithmetic at all (D4/A17/F.1.9).
        ("t + dur", "SPT3005", "Timepoint"),
        ("t + t", "SPT3005", "Timepoint"),
        ("dur * 2", "SPT3005", "Duration"),
        # Non-arithmetic chain types.
        ("s + s", "SPT3005", "Symbol"),
        ("by + by", "SPT3005", "Bytes"),
    ],
)
def test_arithmetic_reject(source: str, code: str, substring: str) -> None:
    _assert_reject(_reject(source), code, substring)


def test_true_divide_help_names_the_floordiv_rewrite() -> None:
    diag = _reject("a / b")
    assert "//" in (diag.help or "")


def test_time_type_help_names_the_u64_bridge() -> None:
    diag = _reject("t + t")
    assert "to_u64" in (diag.help or "") or "to_u64" in " ".join(diag.notes)


# --- UnaryOp ----------------------------------------------------------------


@pytest.mark.parametrize("source, ty", [("-a", Ty.U32), ("-d", Ty.I32), ("-g", Ty.I128)])
def test_unary_minus_support(source: str, ty: Ty) -> None:
    node = _ok(source)
    assert isinstance(node, Unary)
    assert node.op is UnaryOp.NEG
    assert node.ty == ty


@pytest.mark.parametrize(
    ("source", "code", "substring"),
    [
        ("+a", "SPT3007", "+"),
        ("~a", "SPT3007", "~"),
        ("-dur", "SPT3005", "Duration"),
        ("-t", "SPT3005", "Timepoint"),
        ("-s", "SPT3005", "Symbol"),
        ("-flag", "SPT3005", "Bool"),
    ],
)
def test_unary_reject(source: str, code: str, substring: str) -> None:
    _assert_reject(_reject(source), code, substring)


def test_not_on_a_bool_is_supported() -> None:
    node = _ok("not flag")
    assert isinstance(node, Unary)
    assert node.op is UnaryOp.NOT
    assert node.ty == Ty.Bool


def test_not_on_a_comparison_is_supported() -> None:
    node = _ok("not (a == b)")
    assert isinstance(node, Unary)
    assert node.op is UnaryOp.NOT
    assert isinstance(node.operand, Compare)


@pytest.mark.parametrize("source", ["not a", "not s", "not v"])
def test_not_on_a_non_bool_is_rejected(source: str) -> None:
    _assert_reject(_reject(source), "SPT3012", "not")


# --- Compare ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "op"),
    [
        ("a == b", CompareOp.EQ),
        ("a != b", CompareOp.NE),
        ("a < b", CompareOp.LT),
        ("a <= b", CompareOp.LE),
        ("a > b", CompareOp.GT),
        ("a >= b", CompareOp.GE),
    ],
)
def test_comparison_support(source: str, op: CompareOp) -> None:
    node = _ok(source)
    assert isinstance(node, Compare)
    assert node.op is op
    assert node.ty == Ty.Bool


@pytest.mark.parametrize(
    ("source", "via_obj_cmp"),
    [
        # F.1.2/T5: Symbol NEVER compares as a raw packed immediate, even
        # though its repr_form is EITHER (a <= 9-char Symbol IS an immediate).
        ("s == s", True),
        ("s < s", True),
        # Every HOST_OBJECT-repr type.
        ("st == st", True),
        ("by == by", True),
        ("b32 == b32", True),
        ("ad < ad", True),
        ("v == v", True),
        ("m == m", True),
        ("bal == bal", True),
        # An Option may be the Void immediate on one side and a handle on the
        # other, so there is no single scalar lowering -- whatever it wraps.
        ("oad == oad", True),
        ("osym == osym", True),
        ("ovec == ovec", True),
        ("ou32 == ou32", True),
        # The Bytes family is one comparison family (D5), all host objects.
        ("b32 == by", True),
        ("by == b32", True),
        ("b32 < b64", True),
        # Immediates and the EITHER numerics compare as scalars.
        ("a == b", False),
        ("d < d", False),
        ("c < c", False),
        ("e == e", False),
        ("f == f", False),
        ("g == g", False),
        ("t < t", False),
        ("dur < dur", False),
        ("flag == flag", False),
    ],
)
def test_via_obj_cmp(source: str, via_obj_cmp: bool) -> None:
    node = _ok(source)
    assert isinstance(node, Compare)
    assert node.via_obj_cmp is via_obj_cmp


def test_symbol_comparison_always_routes_through_obj_cmp() -> None:
    """The F.1.2/T5 divergence guard, pinned on its own.

    Tier 1 orders small Symbols by raw ASCII bytes; the host's `SymbolSmall`
    packs 6-bit alphabet codes where `_` is 1 and `A` is 12. A raw i64 compare
    of the packed Val would flip `Symbol("_") < Symbol("A")`, so EVERY Symbol
    comparison carries `via_obj_cmp=True` -- for every operator.
    """
    for op in ("==", "!=", "<", "<=", ">", ">="):
        node = _ok(f"s {op} s")
        assert isinstance(node, Compare)
        assert node.via_obj_cmp is True, f"Symbol {op} must route through obj_cmp"


@pytest.mark.parametrize(
    ("source", "code", "substring"),
    [
        ("a < b < a", "SPT1010", "two values at a time"),
        ("a is b", "SPT1012", "is"),
        ("a is not b", "SPT1012", "is"),
        ("a in v", "SPT1011", "in"),
        ("a not in v", "SPT1011", "in"),
        # E13/T4: a raw str/bytes literal never coerces into a chain payload.
        ('s == "abc"', "SPT3016", "Symbol"),
        ('"abc" == s', "SPT3016", "Symbol"),
        ('by == b"abc"', "SPT3016", "Bytes"),
        ('st == "abc"', "SPT3016", "String"),
        # Cross-WIDTH comparison of two chain integers: SPT3003 states that
        # rule correctly ("operands must share the same chain-integer type").
        ("a == c", "SPT3003", "U64"),
        ("d < e", "SPT3003", "I64"),
        ("t == c", "SPT3003", "Timepoint"),
        # A mismatch involving a NON-integer type takes the generic
        # type-mismatch row instead: "chain-integer type" would be wrong in
        # kind for a Symbol or a Vec.
        ("s == a", "SPT3018", "Symbol"),
        ("by == v", "SPT3018", "Bytes"),
        ("bal == a", "SPT3018", "Balance"),
        ("oad == ad", "SPT3018", "Address"),
    ],
)
def test_comparison_reject(source: str, code: str, substring: str) -> None:
    _assert_reject(_reject(source), code, substring)


# --- D5: the Bytes family is ONE comparison family --------------------------


def test_cases_py_bytes32_equals_bytes_same_payload_compiles() -> None:
    """D5, and `cases.py::bytes32_equals_bytes_same_payload` (kind="value").

    Equality and ordering across Bytes/Bytes32/Bytes64/bytes_n(N) are
    payload-based and share one `_SCVAL_RANK`; fixed-length-ness is an
    authoring constraint only. Rejecting this would have broken a frozen
    `kind="value"` case -- there was no cross-family row in the first round,
    which is exactly how it slipped through.
    """
    from tests.semantics.cases import CASES

    by_name = {case.name: case for case in CASES}
    source = by_name["bytes32_equals_bytes_same_payload"].source
    assert source == 'Bool(Bytes32(b"\\x00" * 32) == Bytes(b"\\x00" * 32))'
    node = _ok(source)
    assert isinstance(node, Compare)
    assert node.ty == Ty.Bool
    assert node.via_obj_cmp is True


@pytest.mark.parametrize(
    "source",
    [
        "b32 == by",
        "by == b32",
        "b32 != b64",
        "b32 < by",
        "b64 >= by",
        'by == Bytes32(b"a" * 32)',
    ],
)
def test_bytes_family_cross_subtype_comparison_is_supported(source: str) -> None:
    node = _ok(source)
    assert isinstance(node, Compare)
    assert node.ty == Ty.Bool
    assert node.via_obj_cmp is True


# --- ordering restricted to the tier-1-orderable types (F.1.8) --------------


@pytest.mark.parametrize(
    "source",
    [
        "a < b",
        "d <= d",
        "c > c",
        "e >= e",
        "f < f",
        "g < g",
        "t < t",
        "dur >= dur",
        "flag < flag",
        "s < s",
        "st <= st",
        "by > by",
        "b32 < b64",
        "ad >= ad",
    ],
)
def test_ordering_supported_types(source: str) -> None:
    node = _ok(source)
    assert isinstance(node, Compare)
    assert node.ty == Ty.Bool


@pytest.mark.parametrize(
    "source",
    [
        "v < v",
        "v >= v",
        "m < m",
        "m > m",
        "bal < bal",
        "bal <= bal",
        "ovec < ovec",
        "ou32 < ou32",
    ],
)
def test_ordering_rejected_for_unorderable_types(source: str) -> None:
    """Tier 1 raises TypeError for these orderings and its val_cmp model is
    explicitly partial (A15), so the compiler reproduces the STRICTNESS rather
    than the host's permissiveness (F.1.8). Equality stays supported."""
    diag = _reject(source)
    _assert_reject(diag, "SPT3005", "")
    assert "==" in (diag.help or "")


@pytest.mark.parametrize("source", ["v == v", "m == m", "bal == bal", "ovec == ovec"])
def test_equality_stays_supported_for_unorderable_types(source: str) -> None:
    node = _ok(source)
    assert isinstance(node, Compare)
    assert node.op is CompareOp.EQ
    assert node.via_obj_cmp is True


def test_in_help_names_the_container_methods() -> None:
    diag = _reject("a in v")
    assert "has" in (diag.help or "") or "first_index_of" in (diag.help or "")


# --- BoolOp / IfExp / truthiness --------------------------------------------


@pytest.mark.parametrize(
    ("source", "op"),
    [("flag and flag", BoolOpKind.AND), ("flag or flag", BoolOpKind.OR)],
)
def test_boolop_support(source: str, op: BoolOpKind) -> None:
    node = _ok(source)
    assert isinstance(node, BoolOp)
    assert node.op is op
    assert node.ty == Ty.Bool
    assert len(node.operands) == 2


def test_boolop_over_comparisons() -> None:
    node = _ok("(a == b) and (b < a)")
    assert isinstance(node, BoolOp)
    assert all(isinstance(operand, Compare) for operand in node.operands)


@pytest.mark.parametrize("source", ["a and b", "flag and a", "v or flag", "s and flag"])
def test_boolop_non_bool_operand_is_rejected(source: str) -> None:
    """E9: `U32(0) and U32(5)` is `U32(0)` in Python -- no sound single-type
    lowering exists, so and/or are Bool-only."""
    _assert_reject(_reject(source), "SPT3012", "and/or")


def test_ifexp_support() -> None:
    node = _ok("a if flag else b")
    assert isinstance(node, IfExp)
    assert node.ty == Ty.U32
    assert isinstance(node.cond, ParamRef)


def test_ifexp_arms_must_agree() -> None:
    _assert_reject(_reject("a if flag else c"), "SPT3010", "U64")


@pytest.mark.parametrize("source", ["a if flag else 5", "5 if flag else a"])
def test_ifexp_literal_arm_takes_the_other_arm_type(source: str) -> None:
    """Symmetric in which arm holds the literal, like an operator pair (A6)."""
    node = _ok(source)
    assert isinstance(node, IfExp)
    assert node.ty == Ty.U32
    literal = node.then if isinstance(node.then, Const) else node.orelse
    assert isinstance(literal, Const)
    assert literal.ty == Ty.U32
    assert literal.py_value == 5


def test_ifexp_both_literal_arms_take_the_expected_type() -> None:
    node = _ok("5 if flag else 7", expected=Ty.I64)
    assert isinstance(node, IfExp)
    assert node.ty == Ty.I64


def test_ifexp_condition_is_a_truthiness_position() -> None:
    node = _ok("a if b else a")
    assert isinstance(node, IfExp)
    assert isinstance(node.cond, Unary)
    assert node.cond.op is UnaryOp.NOT
    assert isinstance(node.cond.operand, IsZero)


@pytest.mark.parametrize("source", ["a", "d", "c", "e", "f", "g", "t", "dur"])
def test_truthiness_of_a_numeric_lowers_to_a_zero_test(source: str) -> None:
    """D3/E10: `bool(x)` is `x != 0`, lowered as `not IsZero(x)`."""
    ctx = _ctx()
    node = check_condition(_parse(source), ctx)
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert isinstance(node, Unary)
    assert node.op is UnaryOp.NOT
    assert node.ty == Ty.Bool
    inner = node.operand
    assert isinstance(inner, IsZero)
    assert isinstance(inner.operand, ParamRef)
    assert inner.ty == Ty.Bool


def test_truthiness_of_a_bool_is_the_value_itself() -> None:
    ctx = _ctx()
    node = check_condition(_parse("flag"), ctx)
    assert not ctx.sink
    assert isinstance(node, ParamRef)
    assert node.ty == Ty.Bool


def test_truthiness_of_a_comparison_is_the_comparison() -> None:
    ctx = _ctx()
    node = check_condition(_parse("a == b"), ctx)
    assert not ctx.sink
    assert isinstance(node, Compare)


@pytest.mark.parametrize("source", ["s", "st", "by", "b32", "ad", "v", "m", "bal"])
def test_truthiness_of_a_non_numeric_is_rejected(source: str) -> None:
    """E10/F.1.3: tier 1 answers `True` forever for these -- a genuine trap."""
    ctx = _ctx()
    check_condition(_parse(source), ctx)
    assert len(ctx.sink) == 1
    diag = ctx.sink.diagnostics[0]
    _assert_reject(diag, "SPT3015", "")
    assert "len(" in (diag.help or "") or "has(" in (diag.help or "")


# --- bool() / len() / rejected builtins -------------------------------------


def test_bool_of_a_numeric_is_a_zero_test() -> None:
    node = _ok("bool(a)")
    assert isinstance(node, Unary)
    assert node.op is UnaryOp.NOT
    assert isinstance(node.operand, IsZero)
    assert node.ty == Ty.Bool


def test_bool_of_a_bool_is_the_identity() -> None:
    node = _ok("bool(flag)")
    assert isinstance(node, ParamRef)
    assert node.ty == Ty.Bool


@pytest.mark.parametrize("source", ["bool(s)", "bool(v)", "bool(bal)"])
def test_bool_of_a_non_numeric_is_rejected(source: str) -> None:
    _assert_reject(_reject(source), "SPT3015", "")


@pytest.mark.parametrize(
    ("source", "fn_name"),
    [
        ("len(v)", "vec_len"),
        ("len(m)", "map_len"),
        ("len(by)", "bytes_len"),
        ("len(b32)", "bytes_len"),
    ],
)
def test_len_support(source: str, fn_name: str) -> None:
    """MJ-1: len() is scoped to Vec/Map/Bytes and typed U32 (E19)."""
    node = _ok(source)
    assert isinstance(node, HostCall)
    assert node.fn_name == fn_name
    assert node.ty == Ty.U32
    assert len(node.args) == 1


@pytest.mark.parametrize("source", ["len(s)", "len(st)", "len(a)", "len(bal)"])
def test_len_outside_the_ruled_scope_is_rejected(source: str) -> None:
    _assert_reject(_reject(source), "SPT3009", "len()")


def test_len_host_functions_exist_in_the_bindings() -> None:
    """B2/MJ-3: every host function this task names must exist BY NAME."""
    from serpent import _host

    for fn_name in ("vec_len", "map_len", "bytes_len"):
        assert fn_name in _host.functions_by_name


@pytest.mark.parametrize(
    "source",
    [
        "sum(v)",
        "min(a, b)",
        "max(a, b)",
        "abs(d)",
        "int(a)",
        "str(a)",
        "print(a)",
        "isinstance(a, U32)",
        "divmod(a, b)",
        "sorted(v)",
        "any(v)",
        "all(v)",
        "range(a)",
        "enumerate(v)",
        "zip(v, v)",
        "type(a)",
        "hash(a)",
        "repr(a)",
        "list(v)",
        "dict(m)",
        "set(v)",
        "tuple(v)",
        "getattr(a, 'value')",
        "float(a)",
    ],
)
def test_rejected_builtin_names_itself(source: str) -> None:
    name = source.split("(", 1)[0]
    assert name in dir(builtins), f"{name} is not a builtin; the test row is wrong"
    diag = _reject(source)
    _assert_reject(diag, "SPT1017", name)


# --- displays, comprehensions, and the rest of SS B.2's REJECT rows ---------


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("[a, b]", "SPT1015"),
        ("{a: b}", "SPT1015"),
        ("{a, b}", "SPT1015"),
        ("(a, b)", "SPT1014"),
        ("[x for x in v]", "SPT1003"),
        ("{x for x in v}", "SPT1003"),
        ("{x: x for x in v}", "SPT1003"),
        ("(x for x in v)", "SPT1003"),
        ('f"{a}"', "SPT1004"),
        ("lambda: a", "SPT1005"),
        ("(x := a)", "SPT1006"),
        ("len(*v)", "SPT1007"),
    ],
)
def test_unsupported_expression_constructs(source: str, code: str) -> None:
    _assert_reject(_reject(source), code, "")


def test_subscript_is_no_longer_deferred() -> None:
    """MJ-13 landed in Task 7b: `Bytes[i]` lowers here now (`bytes_get` ->
    `U32`), and the slice/negative-literal/annotation-form rejects come with
    it. The full four-case matrix lives in `test_containers_frontend.py`; this
    row only pins that the Task 5 placeholder is gone."""
    ctx = _ctx()
    node = check_expr(ast.parse("by[0]", mode="eval").body, ctx)
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    assert node.ty == Ty.U32


# --- exhaustive dispatch (MJ-11) --------------------------------------------


class _SyntheticExpr(ast.expr):
    """An `ast.expr` node kind no `NODE_KIND_CODES` row covers."""

    _fields = ()


def test_synthetic_unsupported_node_gets_the_catch_all_code() -> None:
    ctx = _ctx()
    node = _SyntheticExpr(lineno=1, col_offset=0, end_lineno=1, end_col_offset=1)
    result = check_expr(node, ctx)
    assert len(ctx.sink) == 1
    diag = ctx.sink.diagnostics[0]
    assert diag.code == "SPT1037"
    assert diag.help
    assert result.ty == Ty.Invalid


def test_dispatch_covers_every_python_expression_node_kind() -> None:
    """Every concrete `ast.expr` subclass is either handled or has a
    NODE_KIND_CODES row -- an unconsidered node is never a traceback."""
    handled = {
        ast.Constant,
        ast.Name,
        ast.Attribute,
        ast.Call,
        ast.BinOp,
        ast.UnaryOp,
        ast.Compare,
        ast.BoolOp,
        ast.IfExp,
        ast.Subscript,
    }
    missing = [
        kind.__name__
        for kind in ast.expr.__subclasses__()
        # Only the real `ast` node kinds -- not this module's synthetic one.
        if kind.__module__ in ("ast", "_ast")
        and kind not in handled
        and kind not in NODE_KIND_CODES
    ]
    assert not missing, f"unconsidered ast.expr kinds: {missing}"


def test_node_kind_codes_are_registered() -> None:
    for kind, code in NODE_KIND_CODES.items():
        assert code in codes.CODES, f"{kind.__name__} -> unregistered {code}"


# --- error placeholders never cascade ---------------------------------------


def test_invalid_operand_does_not_cascade() -> None:
    """The sink convention (minor 13): a reported failure returns a
    `Ty.Invalid`-typed node, and consumers stay quiet about it."""
    _, sink = _check("nope + a")
    assert len(sink) == 1
    assert sink.diagnostics[0].code == "SPT2001"


def test_scratch_sink_peek_does_not_leak_diagnostics() -> None:
    ctx = _ctx()
    scratch = Diagnostics()
    check_expr(_parse("nope"), replace(ctx, sink=scratch))
    assert len(scratch) == 1
    assert not ctx.sink


def _nested_raw_literal_compare(depth: int) -> str:
    """`Bool(Bool(... Bool(s == "abc") ... == "abc") == "abc")`.

    The shape that made the raw-literal comparison path exponential: each level
    has a raw str literal on one side, so a scratch-sink peek of the other side
    followed by a real re-check doubled the work per level.
    """
    source = "s"
    for _ in range(depth):
        source = f'Bool({source} == "abc")'
    return source


def test_nested_raw_literal_comparison_checks_in_one_pass() -> None:
    """Regression guard with a generous bound: at depth 22 the old peek +
    re-check shape is ~4M walks, so an exponential regression cannot creep
    back in silently."""
    source = _nested_raw_literal_compare(22)
    start = time.perf_counter()
    _, sink = _check(source)
    elapsed = time.perf_counter() - start
    assert sink, "the innermost raw-literal comparison must still be rejected"
    assert elapsed < 2.0, f"checking took {elapsed:.2f}s -- the double walk is back"


def test_nested_attribute_chain_checks_in_one_pass() -> None:
    """The same shape through the introspection-property path."""
    source = "a" + ".value" * 22
    start = time.perf_counter()
    _, sink = _check(source)
    elapsed = time.perf_counter() - start
    assert sink
    assert elapsed < 2.0, f"checking took {elapsed:.2f}s -- the double walk is back"


# --- the 17 `kind="reject"` cases of tests/semantics/cases.py (T1/T6) -------

#: Source strings copied VERBATIM from `tests/semantics/cases.py`, with the
#: registry code C reports for each. Sub-plan D skips these by construction
#: (T6), so this task is the only place they can be proven.
_CASES_PY_REJECTS: list[tuple[str, str, str]] = [
    ("out_of_range_int_operand_rejected", "U32(5) + 2**32", "SPT3004"),
    ("cross_width_unsigned_add_rejected", "U32(1) + U64(1)", "SPT3003"),
    ("cross_signedness_add_rejected", "I32(1) + I64(1)", "SPT3003"),
    ("pow_operator_omitted", "U32(2) ** U32(3)", "SPT3005"),
    ("divmod_operator_omitted", "divmod(U32(5), U32(2))", "SPT1017"),
    ("bitwise_and_operator_omitted", "I32(1) & I32(1)", "SPT3005"),
    ("bool_has_no_arithmetic", "Bool(True) + U32(1)", "SPT3003"),
    ("timepoint_plus_duration_rejected", "Timepoint(5) + Duration(1)", "SPT3005"),
    ("timepoint_plus_timepoint_rejected", "Timepoint(1) + Timepoint(1)", "SPT3005"),
    ("duration_unary_minus_rejected", "-Duration(5)", "SPT3005"),
    ("duration_times_int_rejected", "Duration(3) * 2", "SPT3005"),
    # `cases.py` spells this as an EXPRESSION, and in an expression position
    # the mutator itself is the first reject: `push_back` rebinds its receiver
    # (E11), and an expression has no binding to rebind, so `SPT1034` fires
    # before any argument is type-checked. The element-type mismatch the case
    # is NAMED for is still proven -- in statement position, where the mutation
    # is legal and its argument is checked against the receiver's element type:
    # see `test_vec_wrong_element_type_in_statement_position_is_spt3018` just
    # below. Either spelling rejects, which is the T1/T6 obligation.
    (
        "vec_wrong_element_type_rejected",
        'Vec(U32, [U32(1)]).push_back(Symbol("x"))',
        "SPT1034",
    ),
    ("symbol_empty_rejected", 'Symbol("")', "SPT3004"),
    ("symbol_too_long_rejected", 'Symbol("a" * 33)', "SPT3004"),
    ("bytes32_wrong_length_rejected", 'Bytes32(b"x")', "SPT3004"),
    ("bytes64_wrong_length_rejected", 'Bytes64(b"x" * 10)', "SPT3004"),
    ("address_rejects_malformed_strkey", 'Address("not-a-strkey")', "SPT3004"),
]


def test_vec_wrong_element_type_in_statement_position_is_spt3018() -> None:
    """`vec_wrong_element_type_rejected`'s own reject, through `compile_module`.

    Fix round 1, M-6: the table entry above can only observe the expression
    spelling, where the mutator-in-a-value-position rule fires first. Putting
    the same mutation on a line of its own -- the form E11 actually supports --
    lets the argument reach `_bound_args`, which checks it against the
    receiver's element type. That is the assertion `cases.py` names the case
    for, so it is made here rather than left implied.
    """
    from serpent.compiler import compile_module
    from serpent.compiler.diagnostics import CompileError

    source = (
        "from serpent import Env, Symbol, U32, Vec, contract\n"
        "\n"
        "\n"
        "@contract\n"
        "class C:\n"
        "    def go(self, env: Env) -> U32:\n"
        "        v = Vec(U32, [U32(1)])\n"
        '        v.push_back(Symbol("x"))\n'
        "        return U32(0)\n"
    )
    with pytest.raises(CompileError) as info:
        compile_module(source, "contracts/t.py")
    (diagnostic,) = info.value.diagnostics
    assert diagnostic.code == "SPT3018"
    assert "Symbol" in diagnostic.message and "U32" in diagnostic.message
    assert diagnostic.loc.line == 8


def test_every_cases_py_reject_case_is_represented() -> None:
    """Minor 3: seventeen, not twenty."""
    from tests.semantics.cases import CASES

    reject_names = {case.name for case in CASES if case.kind == "reject"}
    assert len(reject_names) == 17
    assert reject_names == {name for name, _, _ in _CASES_PY_REJECTS}


def test_cases_py_reject_sources_are_verbatim() -> None:
    from tests.semantics.cases import CASES

    by_name = {case.name: case.source for case in CASES if case.kind == "reject"}
    for name, source, _ in _CASES_PY_REJECTS:
        assert by_name[name] == source, f"{name}: source drifted from cases.py"


@pytest.mark.parametrize(
    ("name", "source", "code"),
    _CASES_PY_REJECTS,
    ids=[name for name, _, _ in _CASES_PY_REJECTS],
)
def test_cases_py_reject_case_is_rejected(name: str, source: str, code: str) -> None:
    diag = _reject(source)
    assert diag.code == code, f"{name}: expected {code}, got {diag.code}: {diag.message}"
    assert diag.help, f"{name}: no help rewrite"
    assert diag.loc.path == PATH


# --- tier1_only cases (T2): the frontend must reject all four --------------


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("bool_leaks_as_int_operand", "U32(5) + True"),
        # Wrapped in Bool(...) in cases.py, per its truthiness convention.
        ("symbol_does_not_coerce_from_str", 'Bool(Symbol("abc") == "abc")'),
        ("bytes_does_not_coerce_from_raw_bytes", 'Bool(Bytes(b"abc") == b"abc")'),
        # The fourth case: a negative LITERAL index, rejected by MJ-13's
        # subscript checking as of Task 7b (D6/SPT3011).
        ("bytes_negative_index_traps", 'Bytes(b"ab")[-1]'),
    ],
)
def test_tier1_only_expression_cases_are_rejected(name: str, source: str) -> None:
    """F.2.2: `tier1_only` must mean "the frontend rejects it" -- all four."""
    from tests.semantics.cases import CASES

    by_name = {case.name: case for case in CASES}
    assert by_name[name].tier1_only is True
    assert by_name[name].source == source
    diag = _reject(source)
    assert diag.help
