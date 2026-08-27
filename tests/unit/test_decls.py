"""Task 8: declaration checking, internal calls, and the call graph.

Three groups of tests, matching `serpent.compiler.decls`'s three jobs:

1. **Declarations** -- the module's structs, error enums, events, module
   constants and function signatures become IR declarations, with the
   MJ-9/B9/B10 inventory (STRUCTS + ERROR ENUMS in declaration order, events
   SEPARATE) proven by feeding it to the real `build_spec_entries`.
2. **Internal calls** (E8) -- a module-level helper and a private method
   compile to `InternalCall`, checked through the ONLY entry point a call site
   has (`expr.check_expr`), so the dispatch ORDER is pinned by construction.
3. **The call graph** -- recursion and mutual recursion are located rejects
   (SPT7005), a DAG of helpers is not.
"""

import ast

import pytest

from serpent.compiler import codes
from serpent.compiler.ctx import AliasTable, FuncCtx, Ownership, SlotTable
from serpent.compiler.decls import (
    Declarations,
    FuncSig,
    check_declarations,
    is_static_const_value,
)
from serpent.compiler.diagnostics import Diagnostic, Diagnostics, Loc
from serpent.compiler.expr import RECOGNIZED_BUILTINS, check_expr
from serpent.compiler.ir import (
    Binary,
    BinaryOp,
    Const,
    ConstDecl,
    ErrorEnumDecl,
    EventDecl,
    FuncKind,
    InternalCall,
    LocalRef,
    MakeMap,
    MakeStruct,
    MakeVec,
    ParamRef,
    StructDecl,
)
from serpent.compiler.loader import LoadedModule, load_module
from serpent.compiler.types_ import Ty
from serpent.spec import build_spec_entries

PATH = "contracts/bank.py"

_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

_SOURCE = '''"""A bank-shaped module exercising every declaration form."""

from serpent import (
    U32,
    Address,
    Env,
    Event,
    Symbol,
    Vec,
    bytes_n,
    contract,
    contracterror,
    contractevent,
    contracttype,
    errorcode,
)

ADMIN = Symbol("ADMIN")
LIMIT = U32(10)


@contracterror
class Err:
    """What this contract can fail with."""

    TooBig = errorcode(1)
    Denied = errorcode(7)


@contracttype
class Balance:
    """One holder's balance."""

    owner: Address
    amount: U32


@contractevent
class Paid(Event):
    """Emitted when a payment settles."""

    to: Address
    amount: U32


def double(env: Env, amount: U32) -> U32:
    """A module-level helper (E8): env first, dropped from the signature."""
    return amount + amount


def label(name: Symbol) -> Symbol:
    """A helper that takes no env at all."""
    return name


def has(env: Env, amount: U32) -> U32:
    """A helper whose name collides with a CONTAINER method name."""
    return amount


def keeper(env: Env, items: Vec[U32]) -> U32:
    """A helper taking a container -- the E8 escape position."""
    return U32(0)


@contract
class Bank:
    """A bank."""

    def __init__(self, env: Env, admin: Address) -> None:
        env.storage().instance().set(ADMIN, admin)

    def top_up(self, env: Env, amount: U32) -> U32:
        """Doc for top_up."""
        return amount

    def get(self, env: Env, amount: U32) -> U32:
        """An EXPORT whose name is also a container method name."""
        return amount

    def _fee(self, env: Env, amount: U32) -> U32:
        """A private method (E8): an internal wasm function."""
        return amount

    def _flat(self, amount: U32) -> U32:
        """A private method that takes no env."""
        return amount

    def _log(self, env: Env, amount: U32) -> None:
        """A void private method."""
        env.storage().instance().set(ADMIN, ADMIN)
'''


def _load(source: str = _SOURCE) -> LoadedModule:
    loaded = load_module(source, PATH)
    assert not loaded.diagnostics, [d.message for d in loaded.diagnostics.diagnostics]
    return loaded


def _decls(source: str = _SOURCE) -> tuple[Declarations, Diagnostics]:
    """Check `source`'s declarations against a FRESH sink."""
    loaded = load_module(source, PATH)
    sink = Diagnostics()
    sink.extend(loaded.diagnostics)
    return check_declarations(loaded, sink), sink


def _ok(source: str = _SOURCE) -> Declarations:
    decls, sink = _decls(source)
    assert not sink, [(d.code, d.message) for d in sink.diagnostics]
    return decls


def _reject_decls(source: str) -> Diagnostic:
    """Check `source`, asserting exactly one diagnostic, and return it."""
    _, sink = _decls(source)
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
    assert diag.loc.path == PATH
    if code.startswith("SPT1"):
        assert diag.help, f"{code}: an SPT1xxx diagnostic must carry a help rewrite"


# --- declarations: structs, error enums, events --------------------------------


def test_struct_declarations_resolve_their_fields_in_declaration_order() -> None:
    decls = _ok()
    (struct,) = decls.structs
    assert isinstance(struct, StructDecl)
    assert struct.name == "Balance"
    assert struct.doc == "One holder's balance."
    # Declaration order (B10) -- `MakeStruct` is where P7's byte-string sort
    # lives, not the declaration.
    assert struct.fields == (("owner", Ty.Address, ""), ("amount", Ty.U32, ""))
    # Located at the `class` statement itself, not at its decorator line.
    assert struct.loc.line == _SOURCE.splitlines().index("class Balance:") + 1


def test_error_enum_declarations_carry_their_codes() -> None:
    decls = _ok()
    (enum,) = decls.error_enums
    assert isinstance(enum, ErrorEnumDecl)
    assert enum.name == "Err"
    assert enum.doc == "What this contract can fail with."
    assert enum.cases == (("TooBig", 1, ""), ("Denied", 7, ""))


def test_events_are_tracked_separately_from_the_spec_types() -> None:
    """MJ-9: `spec.sections` REFUSES an event class, so an event must never
    reach the `types=` inventory -- but it is still a declaration C records
    (its topic/data split is sub-plan E's, B14/D8)."""
    decls = _ok()
    (event,) = decls.events
    assert isinstance(event, EventDecl)
    assert event.name == "Paid"
    assert event.fields == (("to", Ty.Address, ""), ("amount", Ty.U32, ""))
    assert all(cls.__name__ != "Paid" for cls in decls.spec_types)


def test_spec_types_inventory_feeds_build_spec_entries() -> None:
    """MJ-9's pin, on this module and on the real fixture: the inventory C
    hands `build_spec_entries(cls, types=...)` must be exactly what that
    function accepts -- structs and error enums, in declaration order."""
    loaded = _load()
    sink = Diagnostics()
    decls = check_declarations(loaded, sink)
    assert not sink, [d.message for d in sink.diagnostics]
    assert [cls.__name__ for cls in decls.spec_types] == ["Err", "Balance"]
    assert loaded.contract_cls is not None
    payload = build_spec_entries(loaded.contract_cls, types=list(decls.spec_types))
    assert payload


def test_token_style_fixture_feeds_build_spec_entries() -> None:
    """The MJ-9 pin on `tests/fixtures/token_style.py` -- the only complete
    authored contract in the repo (A23), post-MJ-8 edit."""
    path = "tests/fixtures/token_style.py"
    with open(path) as handle:
        loaded = load_module(handle.read(), path)
    sink = Diagnostics()
    sink.extend(loaded.diagnostics)
    decls = check_declarations(loaded, sink)
    assert not sink, [(d.code, d.message) for d in sink.diagnostics]
    assert [cls.__name__ for cls in decls.spec_types] == ["TokenError", "BalanceKey"]
    assert [event.name for event in decls.events] == ["Transfer"]
    assert loaded.contract_cls is not None
    assert build_spec_entries(loaded.contract_cls, types=list(decls.spec_types))


# --- declarations: module constants (P5) --------------------------------------


def test_module_constants_become_const_decls() -> None:
    decls = _ok()
    assert [const.name for const in decls.consts] == ["ADMIN", "LIMIT"]
    admin, limit = decls.consts
    assert isinstance(admin, ConstDecl)
    assert admin.ty == Ty.Symbol
    assert isinstance(admin.value, Const) and admin.value.py_value == "ADMIN"
    assert limit.ty == Ty.U32
    assert isinstance(limit.value, Const) and limit.value.py_value == 10


def test_a_bare_literal_module_constant_names_the_wrap() -> None:
    diag = _reject_decls(_SOURCE.replace('ADMIN = Symbol("ADMIN")', "ADMIN = 5"))
    _assert_reject(diag, "SPT3008", "U32")


def test_a_computed_module_constant_is_rejected() -> None:
    """A module constant is a compile-time value: there is no init phase in
    which to run arithmetic (and F.1.10 forbids folding it away)."""
    diag = _reject_decls(_SOURCE.replace("LIMIT = U32(10)", "LIMIT = U32(10) + U32(1)"))
    _assert_reject(diag, "SPT1037", "LIMIT")
    assert "literal" in (diag.help or "")


def test_an_annotated_module_constant_is_rejected_with_the_rewrite() -> None:
    """The ruling carried from Task 3 (module-level `AnnAssign`), pinned here
    rather than widened: a module constant's type IS its constructor, so the
    annotated spelling is redundant, and the loader's diagnostic already names
    the exact rewrite. Widening it would add a second spelling plus an
    annotation-vs-value agreement rule for no new expressiveness.
    """
    diag = _reject_decls(_SOURCE.replace('ADMIN = Symbol("ADMIN")', 'ADMIN: Symbol = Symbol("A")'))
    _assert_reject(diag, "SPT1031", "NAME = U32(1)")


@pytest.mark.parametrize(
    "value",
    [
        Const(loc=Loc.whole_file(PATH), ty=Ty.U32, py_value=1),
        MakeVec(
            loc=Loc.whole_file(PATH), ty=Ty.Vec(Ty.U32), elem_ty=Ty.U32, items=(), all_static=True
        ),
        MakeMap(
            loc=Loc.whole_file(PATH),
            ty=Ty.Map(Ty.Symbol, Ty.U32),
            key_ty=Ty.Symbol,
            value_ty=Ty.U32,
            pairs=(),
            all_static=True,
        ),
        MakeStruct(
            loc=Loc.whole_file(PATH), ty=Ty.Struct("Balance"), struct_name="Balance", fields=()
        ),
    ],
)
def test_static_const_values(value: object) -> None:
    assert is_static_const_value(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        Binary(
            loc=Loc.whole_file(PATH),
            ty=Ty.U32,
            op=BinaryOp.ADD,
            lhs=Const(loc=Loc.whole_file(PATH), ty=Ty.U32, py_value=1),
            rhs=Const(loc=Loc.whole_file(PATH), ty=Ty.U32, py_value=2),
        ),
        ParamRef(loc=Loc.whole_file(PATH), ty=Ty.U32, index=0, name="amount"),
        MakeVec(
            loc=Loc.whole_file(PATH),
            ty=Ty.Vec(Ty.U32),
            elem_ty=Ty.U32,
            items=(ParamRef(loc=Loc.whole_file(PATH), ty=Ty.U32, index=0, name="amount"),),
            all_static=False,
        ),
        MakeStruct(
            loc=Loc.whole_file(PATH),
            ty=Ty.Struct("Balance"),
            struct_name="Balance",
            fields=(("amount", ParamRef(loc=Loc.whole_file(PATH), ty=Ty.U32, index=0, name="a")),),
        ),
    ],
)
def test_non_static_const_values(value: object) -> None:
    assert not is_static_const_value(value)  # type: ignore[arg-type]


# --- declarations: function signatures ---------------------------------------


def test_the_constructor_signature_is_the_reserved_export() -> None:
    decls = _ok()
    init = decls.constructor
    assert init is not None
    assert init.py_name == "__init__"
    assert init.export_name == "__constructor"
    assert init.kind is FuncKind.CONSTRUCTOR
    assert init.takes_env
    assert [(name, ty) for name, ty, _loc in init.params] == [("admin", Ty.Address)]
    assert init.ret == Ty.Void


def test_export_signatures_drop_self_and_env() -> None:
    decls = _ok()
    assert [sig.py_name for sig in decls.exports] == ["top_up", "get"]
    top_up = decls.exports[0]
    assert top_up.kind is FuncKind.EXPORT
    assert top_up.export_name == "top_up"
    assert [(name, ty) for name, ty, _loc in top_up.params] == [("amount", Ty.U32)]
    assert top_up.ret == Ty.U32
    assert top_up.doc == "Doc for top_up."
    assert top_up.takes_env
    # Every param Loc points at the parameter itself, not at the def.
    (_name, _ty, param_loc) = top_up.params[0]
    assert _SOURCE.splitlines()[param_loc.line - 1].lstrip().startswith("def top_up")


def test_internal_signatures_cover_helpers_and_private_methods() -> None:
    decls = _ok()
    assert sorted(decls.internal_sigs) == [
        "double",
        "has",
        "keeper",
        "label",
        "self._fee",
        "self._flat",
        "self._log",
    ]
    double = decls.internal_sigs["double"]
    assert double.fn_name == "double"
    assert double.takes_env
    assert [(name, ty) for name, ty, _loc in double.params] == [("amount", Ty.U32)]
    assert double.ret == Ty.U32
    label = decls.internal_sigs["label"]
    assert not label.takes_env
    assert [(name, ty) for name, ty, _loc in label.params] == [("name", Ty.Symbol)]
    fee = decls.internal_sigs["self._fee"]
    assert fee.fn_name == "_fee" and fee.takes_env
    assert decls.internal_sigs["self._flat"].takes_env is False
    assert decls.internal_sigs["self._log"].ret == Ty.Void
    # `render()` is what a diagnostic spells back at a call site.
    assert double.render() == "double(env, amount: U32) -> U32"


def test_internal_functions_are_marked_internal() -> None:
    decls = _ok()
    internals = [sig for sig in decls.signatures if sig.kind is FuncKind.INTERNAL]
    assert sorted(sig.py_name for sig in internals) == [
        "_fee",
        "_flat",
        "_log",
        "double",
        "has",
        "keeper",
        "label",
    ]
    # An INTERNAL function has no export name of its own (SS C.2's FuncIR).
    for sig in internals:
        assert sig.export_name == sig.py_name


@pytest.mark.parametrize(
    ("bad", "code", "needle"),
    [
        ("def broken(env: Env, amount) -> U32:\n    return amount", "SPT4004", "amount"),
        ("def broken(env: Env, amount: U32):\n    return amount", "SPT4005", "broken"),
        (
            "def broken(env: Env, amount: U32 = U32(1)) -> U32:\n    return amount",
            "SPT4003",
            "amount",
        ),
        ("def broken(env: Env, *rest: U32) -> U32:\n    return env", "SPT4002", "rest"),
        ("def broken(env: Env, **rest: U32) -> U32:\n    return env", "SPT4002", "rest"),
        ("def broken(env: Env, *, amount: U32) -> U32:\n    return amount", "SPT4002", "amount"),
        ("def broken(env: Env, amount: float) -> U32:\n    return amount", "SPT3013", "float"),
        ("def broken(env: Env, amount: U32) -> Err:\n    return amount", "SPT3001", "Err"),
        (
            "def broken(env: Env, amount: U32, again: Env) -> U32:\n    return amount",
            "SPT3013",
            "Env",
        ),
    ],
)
def test_helper_signature_shapes(bad: str, code: str, needle: str) -> None:
    """A module-level helper is never seen by `decorators.py` (which only
    validates the `@contract` class), so C is the only thing that checks its
    shape -- with the same codes the decorator errors bridge to."""
    diag = _reject_decls(
        _SOURCE.replace(
            "def label(name: Symbol) -> Symbol:",
            bad + "\n\n\ndef label(name: Symbol) -> Symbol:",
            1,
        )
    )
    _assert_reject(diag, code, needle)


def test_a_private_method_must_take_self_first() -> None:
    diag = _reject_decls(_SOURCE.replace("def _flat(self, amount: U32)", "def _flat(amount: U32)"))
    _assert_reject(diag, "SPT4001", "_flat")


def test_a_private_method_needs_annotations() -> None:
    diag = _reject_decls(
        _SOURCE.replace("def _flat(self, amount: U32) -> U32:", "def _flat(self, amount) -> U32:")
    )
    _assert_reject(diag, "SPT4004", "amount")


def test_a_helper_and_a_private_method_may_not_share_a_name() -> None:
    """Both compile to a non-exported wasm function in ONE namespace, so two
    targets under one name would make `InternalCall.fn_name` ambiguous."""
    diag = _reject_decls(_SOURCE.replace("def label(name: Symbol)", "def _fee(name: Symbol)"))
    _assert_reject(diag, "SPT2004", "_fee")


# --- internal calls (E8) ------------------------------------------------------


def _ctx(
    decls: Declarations,
    loaded: LoadedModule,
    *,
    fn_name: str = "top_up",
    sig: FuncSig | None = None,
) -> FuncCtx:
    """A `FuncCtx` for one of the module's own functions, wired with the
    declaration table.

    The IDENTITY fields come off the `FuncSig` -- never from the name -- which
    is how Task 10's assembly will build them (one `FuncCtx` per signature),
    and what makes the I-1 repro meaningful. `sig` is passed explicitly
    wherever a name is ambiguous, which is precisely the repro's point: a
    helper and an export can share one name.
    """
    if sig is None:
        sig = next((s for s in decls.signatures if s.py_name == fn_name), None)
    else:
        fn_name = sig.py_name
    loc = Loc.whole_file(PATH)
    params = [("amount", Ty.U32, loc), ("v", Ty.Vec(Ty.U32), loc)]
    slots = SlotTable(reserved={name: "a parameter" for name, _ty, _loc in params})
    return FuncCtx(
        loaded=loaded,
        sink=Diagnostics(),
        params=params,
        locals=slots,
        loop_depth=0,
        return_ty=Ty.U32,
        alias_sets=AliasTable(),
        fn_name=fn_name,
        path=PATH,
        internal_sigs=decls.internal_sigs,
        fn_kind=sig.kind if sig is not None else FuncKind.INTERNAL,
        has_self=sig.has_self if sig is not None else False,
    )


def _wired() -> tuple[FuncCtx, Declarations]:
    loaded = _load()
    sink = Diagnostics()
    decls = check_declarations(loaded, sink)
    assert not sink, [d.message for d in sink.diagnostics]
    return _ctx(decls, loaded), decls


def _call(source: str) -> tuple[object, Diagnostics]:
    ctx, _decls = _wired()
    node = check_expr(ast.parse(source, mode="eval").body, ctx)
    return node, ctx.sink


def _call_ok(source: str) -> InternalCall:
    node, sink = _call(source)
    assert not sink, [(d.code, d.message) for d in sink.diagnostics]
    assert isinstance(node, InternalCall), node
    return node


def _call_reject(source: str) -> Diagnostic:
    _, sink = _call(source)
    assert len(sink) == 1, [(d.code, d.message) for d in sink.diagnostics]
    return sink.diagnostics[0]


def test_a_module_helper_call_is_an_internal_call() -> None:
    node = _call_ok("double(env, amount)")
    assert node.fn_name == "double"
    assert node.ty == Ty.U32
    # The `env` argument contributes NO IR node: there is no Env value on
    # chain, exactly as `FuncIR.params` drops it.
    assert len(node.args) == 1
    (arg,) = node.args
    assert isinstance(arg, ParamRef) and arg.name == "amount"


def test_a_helper_without_env_takes_no_env_argument() -> None:
    node = _call_ok('label(Symbol("hi"))')
    assert node.fn_name == "label"
    assert node.ty == Ty.Symbol
    assert len(node.args) == 1


def test_a_private_method_call_is_an_internal_call() -> None:
    node = _call_ok("self._fee(env, amount)")
    assert node.fn_name == "_fee"
    assert node.ty == Ty.U32
    assert len(node.args) == 1


def test_a_void_private_method_call_types_as_void() -> None:
    node = _call_ok("self._log(env, amount)")
    assert node.ty == Ty.Void


def test_a_helper_named_like_a_container_method_still_resolves() -> None:
    """The carried ordering finding: internal calls are tried BEFORE the
    container-method table, so a helper called `get` is an internal call and
    not a container surface (whose receiver check would report something
    else entirely)."""
    node = _call_ok("has(env, amount)")
    assert node.fn_name == "has"


def test_calling_an_exported_method_through_self_is_rejected() -> None:
    """E8 (b) allows module-level helpers and PRIVATE methods only. The
    diagnostic must name that rule -- reaching the container table first
    (`self.get(...)`, a `get` row) would report an unrelated receiver error.
    """
    diag = _call_reject("self.get(amount)")
    _assert_reject(diag, "SPT1037", "get")
    assert "helper" in (diag.help or "")


def test_calling_an_unknown_self_attribute_is_rejected() -> None:
    diag = _call_reject("self.nope(amount)")
    _assert_reject(diag, "SPT2001", "nope")


def test_internal_call_arity_is_checked() -> None:
    _assert_reject(_call_reject("double(env)"), "SPT3020", "double")
    _assert_reject(_call_reject("double(env, amount, amount)"), "SPT3020", "double")
    # Forgetting `env` is an arity mistake against a signature that names it.
    _assert_reject(_call_reject("double(amount)"), "SPT3020", "env")


def test_internal_call_argument_types_are_checked() -> None:
    diag = _call_reject('double(env, Symbol("hi"))')
    _assert_reject(diag, "SPT3018", "amount")


def test_internal_call_literals_coerce_to_the_parameter_type() -> None:
    node = _call_ok("double(env, 7)")
    (arg,) = node.args
    assert isinstance(arg, Const) and arg.py_value == 7 and arg.ty == Ty.U32


def test_the_env_argument_must_be_the_env_name() -> None:
    """`SPT1037`, not `SPT1038`: that row is the `env.<...>` API's own
    call-shape reject (SS C.4), and this is a user-declared function that
    happens to take the host handle first (fix round 1, I-3)."""
    diag = _call_reject("double(amount, amount)")
    _assert_reject(diag, "SPT1037", "env")


def test_internal_calls_do_not_take_keyword_arguments() -> None:
    diag = _call_reject("double(env, amount=amount)")
    _assert_reject(diag, "SPT1035", "double")


def test_internal_call_arguments_are_escapes() -> None:
    """E11/E8: a callee can embed a passed container in a container of its
    own, so a container argument to an internal call loses ownership --
    conservatively, without inspecting the callee."""
    loaded = _load()
    sink = Diagnostics()
    decls = check_declarations(loaded, sink)
    assert not sink
    ctx = _ctx(decls, loaded)
    slot = ctx.locals.declare("own", Ty.Vec(Ty.U32), Loc.whole_file(PATH), ctx.sink)
    assert slot is not None
    ctx.locals.mark_assigned("own")
    ctx.alias_sets.mark_owned(slot.slot)
    node = check_expr(ast.parse("keeper(env, own)", mode="eval").body, ctx)
    assert not ctx.sink, [(d.code, d.message) for d in ctx.sink.diagnostics]
    assert isinstance(node, InternalCall)
    assert isinstance(node.args[0], LocalRef)
    assert ctx.alias_sets.ownership_of(slot.slot) is Ownership.ALIASED


def test_an_unwired_context_stays_silent_for_a_declared_target() -> None:
    """A `FuncCtx` built without a declaration pass has an empty table. A call
    to a target that DOES exist must not invent a diagnostic (the declaration
    layer is what reports those) -- and must not crash."""
    loaded = _load()
    ctx = _ctx(Declarations.empty(), loaded)
    node = check_expr(ast.parse("double(env, amount)", mode="eval").body, ctx)
    assert node.ty == Ty.Invalid
    assert not ctx.sink


# --- the call graph (E8: recursion is a reject) --------------------------------

_RECURSION = '''"""Recursive helpers."""

from serpent import U32, Env, contract


def down(env: Env, amount: U32) -> U32:
    return down(env, amount)


@contract
class Loop:
    """A contract."""

    def go(self, env: Env, amount: U32) -> U32:
        return down(env, amount)
'''

_MUTUAL = '''"""Mutually recursive helpers."""

from serpent import U32, Env, contract


def ping(env: Env, amount: U32) -> U32:
    return pong(env, amount)


def pong(env: Env, amount: U32) -> U32:
    return ping(env, amount)


@contract
class Loop:
    """A contract."""

    def go(self, env: Env, amount: U32) -> U32:
        return ping(env, amount)
'''

_METHOD_CYCLE = '''"""A private-method cycle."""

from serpent import U32, Env, contract


@contract
class Loop:
    """A contract."""

    def go(self, env: Env, amount: U32) -> U32:
        return self._a(env, amount)

    def _a(self, env: Env, amount: U32) -> U32:
        return self._b(env, amount)

    def _b(self, env: Env, amount: U32) -> U32:
        return self._a(env, amount)
'''

_DAG = '''"""A DAG of helpers: shared, not recursive."""

from serpent import U32, Env, contract


def leaf(env: Env, amount: U32) -> U32:
    return amount


def middle(env: Env, amount: U32) -> U32:
    return leaf(env, amount)


@contract
class Fine:
    """A contract."""

    def go(self, env: Env, amount: U32) -> U32:
        return middle(env, leaf(env, amount))

    def _also(self, env: Env, amount: U32) -> U32:
        return leaf(env, amount)
'''


def test_direct_recursion_is_rejected_naming_the_cycle() -> None:
    diag = _reject_decls(_RECURSION)
    _assert_reject(diag, "SPT7005", "down")
    assert "itself" in diag.message or any("itself" in note for note in diag.notes)
    # Located at the recursive CALL, not at the def.
    assert _RECURSION.splitlines()[diag.loc.line - 1].strip() == "return down(env, amount)"


def test_mutual_recursion_is_rejected_naming_both() -> None:
    diag = _reject_decls(_MUTUAL)
    _assert_reject(diag, "SPT7005", "ping")
    assert any("pong" in note for note in diag.notes) or "pong" in diag.message


def test_a_private_method_cycle_is_rejected() -> None:
    diag = _reject_decls(_METHOD_CYCLE)
    _assert_reject(diag, "SPT7005", "self._a")


def test_a_shared_helper_is_not_a_cycle() -> None:
    decls = _ok(_DAG)
    assert sorted(decls.internal_sigs) == ["leaf", "middle", "self._also"]


# --- robustness: a declaration that must not silently vanish -------------------

_ASYNC = '''"""An async method."""

from serpent import U32, Env, contract


@contract
class K:
    """A contract."""

    async def go(self, env: Env, amount: U32) -> U32:
        return amount
'''


def test_an_async_method_is_a_located_reject() -> None:
    """`decorators.contract` RECORDS an async method (`inspect.isfunction`
    accepts a coroutine function), so the spec would carry an export with no
    `FuncIR` behind it if the declaration layer merely skipped it -- F.1.14's
    skew. Nothing else in the frontend looks at a method's `async` marker: the
    loader accepts the class, and `stmt.py`'s `SPT1002` covers `async`
    STATEMENTS inside a body."""
    diag = _reject_decls(_ASYNC)
    _assert_reject(diag, "SPT1002", "go")


_NO_CONTRACT = '''"""A module with no @contract class."""

from serpent import U32, Env


def helper(env: Env, amount: U32) -> U32:
    return amount
'''


def test_helpers_are_still_checked_without_a_contract_class() -> None:
    """Collect-all (E16): the missing `@contract` class is the loader's
    `SPT4019`, and declaration checking keeps going -- it does not need a
    contract class to resolve the module's helpers."""
    loaded = load_module(_NO_CONTRACT, PATH)
    assert [d.code for d in loaded.diagnostics.diagnostics] == ["SPT4019"]
    sink = Diagnostics()
    decls = check_declarations(loaded, sink)
    assert not sink, [(d.code, d.message) for d in sink.diagnostics]
    assert sorted(decls.internal_sigs) == ["helper"]
    assert decls.constructor is None and decls.exports == ()


def test_a_module_helper_has_no_self() -> None:
    """`self._fee(...)` inside a module-level helper is a tier-1 `NameError`
    while the lowered internal call would work on chain -- an
    oracle-unrunnable accept (A18). `self` is in scope exactly inside a method
    of the `@contract` class."""
    loaded = _load()
    sink = Diagnostics()
    decls = check_declarations(loaded, sink)
    assert not sink
    ctx = _ctx(decls, loaded, fn_name="double")  # a module-level helper
    check_expr(ast.parse("self._fee(env, amount)", mode="eval").body, ctx)
    assert len(ctx.sink) == 1
    _assert_reject(ctx.sink.diagnostics[0], "SPT2001", "self")


def test_a_private_method_may_call_another_private_method() -> None:
    """Inside a method, `self.<private>` resolves -- including from another
    private method (the call graph is what forbids a CYCLE, not the shape)."""
    loaded = _load()
    sink = Diagnostics()
    decls = check_declarations(loaded, sink)
    ctx = _ctx(decls, loaded, fn_name="_fee")
    node = check_expr(ast.parse("self._flat(amount)", mode="eval").body, ctx)
    assert not ctx.sink, [(d.code, d.message) for d in ctx.sink.diagnostics]
    assert isinstance(node, InternalCall) and node.fn_name == "_flat"


# --- fix round 1 --------------------------------------------------------------

_HELPER_SHARING_AN_EXPORT_NAME = '''"""The shape spec Sec.2 nearly writes: a `balance` helper beside a `balance`
export."""

from serpent import U32, Address, Env, contract


def balance(env: Env, owner: Address) -> U32:
    return U32(0)


@contract
class Token:
    """A token."""

    def balance(self, env: Env, owner: Address) -> U32:
        return balance(env, owner)

    def _fee(self, env: Env, amount: U32) -> U32:
        return amount
'''


def test_self_availability_is_decided_by_identity_not_by_name() -> None:
    """I-1: a module-level helper named the same as an export must NOT be
    mistaken for a method. Deciding from `ctx.fn_name` against the class's
    method names let `self._fee(...)` inside the helper `balance` compile to a
    working `InternalCall` with zero diagnostics -- while tier 1 raises
    `NameError`, because a module-level function has no `self`.
    """
    loaded = load_module(_HELPER_SHARING_AN_EXPORT_NAME, PATH)
    sink = Diagnostics()
    decls = check_declarations(loaded, sink)
    # M-4: the collision itself is now refused, naming both declarations.
    assert [d.code for d in sink.diagnostics] == ["SPT2004"]
    assert "balance" in sink.diagnostics[0].message

    helper = next(sig for sig in decls.signatures if sig.py_name == "balance" and not sig.has_self)
    assert helper.kind is FuncKind.INTERNAL
    export = next(sig for sig in decls.signatures if sig.kind is FuncKind.EXPORT)
    assert export.py_name == "balance" and export.has_self is True

    # The helper's own context: `self` is not in scope, whatever it is called.
    ctx = _ctx(decls, loaded, sig=helper)
    assert ctx.has_self is False
    check_expr(ast.parse("self._fee(env, amount)", mode="eval").body, ctx)
    assert len(ctx.sink) == 1
    _assert_reject(ctx.sink.diagnostics[0], "SPT2001", "self")


def test_a_method_may_still_reach_its_private_methods() -> None:
    """The other direction of I-1: identity says `self` IS in scope inside a
    method, so an export calling a private method still resolves."""
    loaded = _load()
    sink = Diagnostics()
    decls = check_declarations(loaded, sink)
    assert not sink
    ctx = _ctx(decls, loaded, fn_name="top_up")
    assert ctx.has_self is True and ctx.fn_kind is FuncKind.EXPORT
    node = check_expr(ast.parse("self._fee(env, amount)", mode="eval").body, ctx)
    assert not ctx.sink, [(d.code, d.message) for d in ctx.sink.diagnostics]
    assert isinstance(node, InternalCall) and node.fn_name == "_fee"


_MANGLED = '''"""A name-mangled private method."""

from serpent import U32, Env, contract


@contract
class Bank:
    """A bank."""

    def go(self, env: Env, amount: U32) -> U32:
        return self.__fee(env, amount)

    def __fee(self, env: Env, amount: U32) -> U32:
        return amount
'''


def test_a_name_mangled_method_is_a_located_reject() -> None:
    """I-2: `def __fee(self)` inside `class Bank` binds `_Bank__fee`, so the
    AST name and the executed name disagree and the method used to be dropped
    in silence. Rejected at the declaration, with the single-underscore
    rewrite in `help`."""
    diag = _reject_decls(_MANGLED)
    _assert_reject(diag, "SPT1037", "__fee")
    assert "_fee" in (diag.help or "")


def test_a_dunder_method_is_not_name_mangled() -> None:
    """Python's rule is "at least two leading underscores, at most one
    trailing" -- so `__init__` is NOT mangled and the I-2 reject must not fire
    on the constructor (the fixture module declares one and checks clean)."""
    decls = _ok()
    assert decls.constructor is not None
    assert decls.constructor.py_name == "__init__"


def test_no_path_produces_an_invalid_node_with_an_empty_sink() -> None:
    """The sink invariant (minor 13): a `Ty.Invalid` result always has a
    diagnostic behind it. The name-mangled repro is where it broke -- the
    declaration produced no signature and the call site reported nothing --
    so it is asserted end-to-end, on ONE shared sink, the way `compile_module`
    will run it."""
    loaded = load_module(_MANGLED, PATH)
    sink = Diagnostics()
    sink.extend(loaded.diagnostics)
    decls = check_declarations(loaded, sink)
    assert [d.code for d in sink.diagnostics] == ["SPT1037"]

    loc = Loc.whole_file(PATH)
    ctx = FuncCtx(
        loaded=loaded,
        sink=sink,
        params=[("amount", Ty.U32, loc)],
        locals=SlotTable(),
        loop_depth=0,
        return_ty=Ty.U32,
        alias_sets=AliasTable(),
        fn_name="go",
        path=PATH,
        internal_sigs=decls.internal_sigs,
        fn_kind=FuncKind.EXPORT,
        has_self=True,
    )
    node = check_expr(ast.parse("self.__fee(env, amount)", mode="eval").body, ctx)
    assert node.ty == Ty.Invalid
    assert sink, "an Invalid node must never leave the sink empty"


_HELPER_NAMED_LEN = '''"""A helper shadowing a recognized builtin."""

from serpent import U32, Env, contract


def len(env: Env, amount: U32) -> U32:  # noqa: A001
    return amount


@contract
class K:
    """k."""

    def go(self, env: Env, amount: U32) -> U32:
        return amount
'''


def test_a_helper_may_not_shadow_a_recognized_builtin() -> None:
    """M-5: `len`/`bool` are dispatched BY NAME in `expr.py` before any helper
    lookup, so a helper under one of those names could never be called at
    all. The list is imported from the dispatch, never restated."""
    diag = _reject_decls(_HELPER_NAMED_LEN)
    _assert_reject(diag, "SPT2004", "len")
    assert "len" in RECOGNIZED_BUILTINS and "bool" in RECOGNIZED_BUILTINS


def test_the_unknown_self_method_reject_carries_a_help() -> None:
    """M-2: the sibling `self.<export>(...)` site had a rewrite in `help` and
    this one did not."""
    diag = _call_reject("self.nope(amount)")
    _assert_reject(diag, "SPT2001", "nope")
    assert "private method" in (diag.help or "")


def test_bytes_n_in_a_value_position_names_the_annotation_form() -> None:
    """M-8: `B4 = bytes_n(4)` used to draw "`bytes_n` is not defined in this
    contract", which is false -- it is imported and it works in an annotation.
    The reject stands (annotation position only, SS B.2); the message is now
    true."""
    diag = _reject_decls(_SOURCE.replace("LIMIT = U32(10)", "LIMIT = bytes_n(4)"))
    _assert_reject(diag, "SPT3014", "bytes_n")


def test_a_deep_helper_chain_does_not_exhaust_the_python_stack() -> None:
    """M-3: `_check_call_graph` promises never to raise, so its DFS keeps an
    explicit stack. A 1200-long helper chain is well past python's default
    recursion limit."""
    depth = 1200
    lines = ['"""A long chain."""', "", "from serpent import U32, Env, contract", "", ""]
    for index in range(depth):
        nxt = f"    return h{index + 1}(env, amount)" if index + 1 < depth else "    return amount"
        lines += [f"def h{index}(env: Env, amount: U32) -> U32:", nxt, "", ""]
    lines += [
        "@contract",
        "class Chain:",
        '    """A contract."""',
        "",
        "    def go(self, env: Env, amount: U32) -> U32:",
        "        return h0(env, amount)",
        "",
    ]
    decls = _ok("\n".join(lines))
    assert len(decls.internal_sigs) == depth


def test_a_deep_helper_chain_that_closes_a_cycle_is_still_reported() -> None:
    """The same chain, with the last link calling the first: one located
    `SPT7005`, from the iterative walk."""
    depth = 400
    lines = ['"""A long cycle."""', "", "from serpent import U32, Env, contract", "", ""]
    for index in range(depth):
        target = f"h{(index + 1) % depth}"
        lines += [
            f"def h{index}(env: Env, amount: U32) -> U32:",
            f"    return {target}(env, amount)",
            "",
            "",
        ]
    lines += [
        "@contract",
        "class Chain:",
        '    """A contract."""',
        "",
        "    def go(self, env: Env, amount: U32) -> U32:",
        "        return h0(env, amount)",
        "",
    ]
    diag = _reject_decls("\n".join(lines))
    _assert_reject(diag, "SPT7005", "h0")
