"""Tests for `serpent.compiler.recognize` (Task 7a): the Env-API recognition
table, dossier SS C.4.

Three obligations, mirroring the task brief:

* **Every `RECOGNIZED` row** produces the exact `HostCall`/`IfExp` shape SS
  C.4 describes -- fn_name(s), arg IR shapes, and the `StorageType` raw-scalar
  immediates (B6) -- including the `get(key, T, default=d)` -> `has`/`If`/
  `get` lowering SS C.4 spells out explicitly.
* **The future-name vs. unknown-name split** (`SPT1033` vs `SPT2006`), both
  as a bare `env.<name>` attribute and as a called `env.<name>(...)`, plus
  the `Ledger`-nested future methods.
* **The completeness assertion** (MJ-3): every `RECOGNIZED` target is a real
  `_host.functions_by_name` key, and the set of targets referenced is EXACTLY
  SS C.4's env/storage/ledger/events/auth inventory -- neither direction may
  drift.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from serpent import Symbol
from serpent import env as env_module
from serpent._host import functions_by_name
from serpent.compiler import codes, recognize
from serpent.compiler.ctx import AliasTable, FuncCtx, SlotTable
from serpent.compiler.diagnostics import Diagnostic, Diagnostics, Loc
from serpent.compiler.ir import (
    Const,
    HostCall,
    IfExp,
    IRExpr,
    MakeStruct,
    MakeTopics,
    ParamRef,
    RawScalar,
    RawScalarKind,
)
from serpent.compiler.loader import LoadedModule, load_module
from serpent.compiler.recognize import (
    ENV_HOST_FN_TARGETS,
    KNOWN_FUTURE_ENV_NAMES,
    RECOGNIZED,
    HostCallSpec,
    SurfaceKind,
    recognize_attribute,
    recognize_call,
)
from serpent.compiler.types_ import Ty, TyTag
from serpent.errors import CODE_MISSING_VALUE

PATH = "contract.py"

_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

_SOURCE = '''
"""Recognition-table fixture."""
from serpent import U32, Address, Env, Event, Symbol, Vec, contract, contractevent, contracttype


@contracttype
class BalanceKey:
    owner: Address


@contractevent
class Transfer(Event):
    frm: Address
    to: Address
    amount: U32


@contract
class Go:
    def go(
        self,
        env: Env,
        addr: Address,
        amt: U32,
        threshold: U32,
        extend_to: U32,
        items: Vec[U32],
        sym: Symbol,
        key: BalanceKey,
    ) -> U32:
        return amt
'''

#: `(name, Ty)` in declaration order, `self`/`env` already dropped (SS C.3).
_PARAMS: list[tuple[str, Ty]] = [
    ("addr", Ty.Address),
    ("amt", Ty.U32),
    ("threshold", Ty.U32),
    ("extend_to", Ty.U32),
    ("items", Ty.Vec(Ty.U32)),
    ("sym", Ty.Symbol),
    ("key", Ty.Struct("BalanceKey")),
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


def _parse_call(source: str) -> ast.Call:
    node = ast.parse(source, mode="eval").body
    assert isinstance(node, ast.Call), source
    return node


def _parse_attr(source: str) -> ast.Attribute:
    node = ast.parse(source, mode="eval").body
    assert isinstance(node, ast.Attribute), source
    return node


def _ok_call(source: str) -> IRExpr:
    ctx = _ctx()
    node = recognize_call(_parse_call(source), ctx)
    assert node is not None, f"{source!r} was not recognized at all"
    assert not ctx.sink, [d.message for d in ctx.sink.diagnostics]
    return node


def _reject_call(source: str) -> Diagnostic:
    ctx = _ctx()
    node = recognize_call(_parse_call(source), ctx)
    assert node is not None, f"{source!r} was not recognized at all"
    assert len(ctx.sink) == 1, [(d.code, d.message) for d in ctx.sink.diagnostics]
    return ctx.sink.diagnostics[0]


def _reject_attr(source: str) -> Diagnostic:
    ctx = _ctx()
    node = recognize_attribute(_parse_attr(source), ctx)
    assert node is not None, f"{source!r} was not recognized at all"
    assert len(ctx.sink) == 1, [(d.code, d.message) for d in ctx.sink.diagnostics]
    return ctx.sink.diagnostics[0]


def _assert_reject(diag: Diagnostic, code: str, detail_substring: str) -> None:
    """Assert `diag` is `code`, carries its registry intent as the message's
    PREFIX, and carries `detail_substring` in the part AFTER that prefix --
    the code-specific detail, never the intent wording itself (which every
    `_error()` call prepends unconditionally, so asserting on the whole
    message would pass even when the detail is wrong or missing -- fix
    round 1's tautology finding)."""
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


def _raw_scalar(node: IRExpr) -> RawScalar:
    assert isinstance(node, RawScalar)
    return node


# --- RECOGNIZED: the table itself (MJ-3) --------------------------------------


_EXPECTED_ROWS: frozenset[str] = frozenset(
    {
        "storage.set",
        "storage.get",
        "storage.get_default",
        "storage.has",
        "storage.del_",
        "storage.instance.extend_ttl",
        "storage.keyed.extend_ttl",
        "ledger.timestamp",
        "ledger.sequence",
        "events.publish",
        "address.require_auth",
        "address.require_auth_for_args",
        "event.publish",
    }
)


def test_recognized_has_every_expected_row() -> None:
    """Exactly these rows carry `family="env"`.

    Task 7b added the container/struct rows to the SAME table (MJ-3: one
    source of truth for the surface -> host-fn mapping), so this assertion is
    scoped by `family` rather than over the whole table -- their own exactness
    assertion lives in `test_containers_frontend.py`.
    """
    env_rows = {key for key, spec in RECOGNIZED.items() if spec.family == "env"}
    assert env_rows == _EXPECTED_ROWS


def test_recognized_rows_are_internally_consistent() -> None:
    for key, spec in RECOGNIZED.items():
        assert isinstance(spec, HostCallSpec)
        assert spec.family in ("env", "container"), key
        if spec.kind is SurfaceKind.REJECT:
            assert spec.host_fns == (), key
            assert spec.reject_code is not None, key
            assert spec.reject_code in codes.CODES, key
        elif spec.kind is SurfaceKind.GET_DEFAULT:
            assert len(spec.host_fns) == 2, key
            assert spec.reject_code is None, key
        else:
            # Every other kind -- `HOST_CALL` and Task 7b's container shapes
            # (`MUTATOR`/`MAKE_VEC`/`MAKE_MAP`/`MAKE_STRUCT`/`FIELD_GET`) --
            # names at least one host function and rejects nothing.
            assert len(spec.host_fns) >= 1, key
            assert spec.reject_code is None, key


def test_storage_get_records_the_reserved_missing_value_code() -> None:
    """SS C.4's "C must allocate the reserved code" (E14) -- already
    allocated in `serpent.errors`, and the table names it."""
    assert RECOGNIZED["storage.get"].missing_value_code == CODE_MISSING_VALUE
    for key, spec in RECOGNIZED.items():
        if key != "storage.get":
            assert spec.missing_value_code is None, key


# --- registry ruling (fix round 1): the two sanctioned codes.py edits --------


def test_registry_has_the_new_call_shape_code() -> None:
    by_code = {entry.code: entry for entry in codes.REGISTRY}
    assert "SPT1038" in by_code
    entry = by_code["SPT1038"]
    assert entry.band == "SPT1xxx"
    assert entry.message_intent == "env API used with an unsupported call shape"


def test_registry_spt3020_widened_for_recognized_calls() -> None:
    entry = next(e for e in codes.REGISTRY if e.code == "SPT3020")
    assert "7a" in entry.owning_task
    assert "constructor" not in entry.message_intent.lower()


# --- Completeness assertion (MJ-3): both directions ---------------------------

#: SS C.4's env/storage/ledger/events/auth host-function inventory, named
#: directly from the dossier table -- the independent oracle this test checks
#: `ENV_HOST_FN_TARGETS` against, so a typo in `recognize.py`'s table cannot
#: mark itself "complete".
_DOSSIER_C4_INVENTORY: frozenset[str] = frozenset(
    {
        "put_contract_data",
        "get_contract_data",
        "has_contract_data",
        "del_contract_data",
        "extend_current_contract_instance_and_code_ttl",
        "extend_contract_data_ttl",
        "get_ledger_timestamp",
        "get_ledger_sequence",
        "contract_event",
        "require_auth",
        "require_auth_for_args",
    }
)


def test_every_recognized_target_exists_in_functions_by_name() -> None:
    """Forward direction: every host fn `RECOGNIZED` names is real."""
    for name in ENV_HOST_FN_TARGETS:
        assert name in functions_by_name, name
    assert recognize.target_functions().keys() == ENV_HOST_FN_TARGETS


def test_recognized_targets_are_exactly_the_dossier_c4_inventory() -> None:
    """Both directions at once: nothing in SS C.4's inventory is missing from
    `RECOGNIZED`, and `RECOGNIZED` reaches no host function outside it."""
    assert ENV_HOST_FN_TARGETS == _DOSSIER_C4_INVENTORY


# --- storage: set/get/has/del_/extend_ttl -------------------------------------


def test_storage_set() -> None:
    node = _ok_call("env.storage().persistent().set(key, amt)")
    assert isinstance(node, HostCall)
    assert node.fn_name == "put_contract_data"
    assert node.ty == Ty.Void
    key_ir, value_ir, imm = node.args
    assert isinstance(key_ir, ParamRef) and key_ir.name == "key"
    assert isinstance(value_ir, ParamRef) and value_ir.name == "amt"
    assert _raw_scalar(imm).kind is RawScalarKind.STORAGE_TYPE
    assert _raw_scalar(imm).value == 1  # persistent


@pytest.mark.parametrize(
    ("bucket", "expected_immediate"),
    [("instance", 2), ("persistent", 1), ("temporary", 0)],
)
def test_storage_get_no_default(bucket: str, expected_immediate: int) -> None:
    node = _ok_call(f"env.storage().{bucket}().get(key, U32)")
    assert isinstance(node, HostCall)
    assert node.fn_name == "get_contract_data"
    assert node.ty == Ty.U32
    key_ir, imm = node.args
    assert isinstance(key_ir, ParamRef) and key_ir.name == "key"
    assert _raw_scalar(imm).value == expected_immediate


def test_storage_get_default_lowers_to_has_ifexp_get() -> None:
    """SS C.4's worked example: `has_contract_data` -> `IfExp` ->
    `get_contract_data`/`default`, all sharing `target_ty`."""
    node = _ok_call("env.storage().persistent().get(key, U32, default=amt)")
    assert isinstance(node, IfExp)
    assert node.ty == Ty.U32
    assert isinstance(node.cond, HostCall)
    assert node.cond.fn_name == "has_contract_data"
    assert node.cond.ty == Ty.Bool
    assert isinstance(node.then, HostCall)
    assert node.then.fn_name == "get_contract_data"
    assert node.then.ty == Ty.U32
    assert isinstance(node.orelse, ParamRef) and node.orelse.name == "amt"


def test_storage_get_default_positional() -> None:
    node = _ok_call("env.storage().instance().get(key, U32, amt)")
    assert isinstance(node, IfExp)


def test_storage_has_returns_chain_bool() -> None:
    node = _ok_call("env.storage().instance().has(sym)")
    assert isinstance(node, HostCall)
    assert node.fn_name == "has_contract_data"
    assert node.ty == Ty.Bool  # chain Bool, not python bool (minor 9)


def test_storage_del() -> None:
    node = _ok_call("env.storage().temporary().del_(sym)")
    assert isinstance(node, HostCall)
    assert node.fn_name == "del_contract_data"
    assert node.ty == Ty.Void
    _, imm = node.args
    assert _raw_scalar(imm).value == 0  # temporary


def test_storage_instance_extend_ttl_has_no_key() -> None:
    node = _ok_call("env.storage().instance().extend_ttl(threshold, extend_to)")
    assert isinstance(node, HostCall)
    assert node.fn_name == "extend_current_contract_instance_and_code_ttl"
    assert len(node.args) == 2


def test_storage_keyed_extend_ttl_has_a_key_and_storage_type() -> None:
    node = _ok_call("env.storage().persistent().extend_ttl(key, threshold, extend_to)")
    assert isinstance(node, HostCall)
    assert node.fn_name == "extend_contract_data_ttl"
    key_ir, imm, threshold_ir, extend_to_ir = node.args
    assert isinstance(key_ir, ParamRef) and key_ir.name == "key"
    assert _raw_scalar(imm).value == 1
    assert isinstance(threshold_ir, ParamRef) and threshold_ir.name == "threshold"
    assert isinstance(extend_to_ir, ParamRef) and extend_to_ir.name == "extend_to"


def test_storage_set_missing_argument() -> None:
    """`_bind`'s "missing required" branch -> SPT3020 (a call-arity mistake,
    not a type mismatch -- fix round 1)."""
    _assert_reject(_reject_call("env.storage().instance().set(sym)"), "SPT3020", "value")


def test_storage_has_too_many_arguments() -> None:
    """`_bind`'s "too many positional arguments" branch -> SPT3020."""
    _assert_reject(_reject_call("env.storage().instance().has(sym, amt)"), "SPT3020", "at most 1")


def test_storage_set_duplicate_keyword() -> None:
    """`_bind`'s "duplicate keyword" branch -> SPT3020 -- distinct from an
    UNRECOGNIZED keyword (SPT1035, `test_storage_set_unrecognized_keyword`),
    since this keyword IS recognized, just supplied twice."""
    _assert_reject(
        _reject_call("env.storage().instance().set(sym, amt, value=amt)"),
        "SPT3020",
        "multiple values",
    )


def test_storage_set_unrecognized_keyword() -> None:
    _assert_reject(
        _reject_call("env.storage().instance().set(key=sym, value=amt, extra=amt)"),
        "SPT1035",
        "extra",
    )


def test_storage_get_type_argument_must_be_a_bare_name() -> None:
    """`_resolve_type_arg`'s non-`Name` type argument is an annotation-shape
    mistake -> SPT3013 (the same code Task 6 uses for the identical shape of
    mistake elsewhere), not SPT3018 (fix round 1)."""
    _assert_reject(
        _reject_call("env.storage().instance().get(key, U32 if True else U32)"),
        "SPT3013",
        "type argument",
    )


# --- ledger --------------------------------------------------------------------


def test_ledger_timestamp_is_u64() -> None:
    node = _ok_call("env.ledger().timestamp()")
    assert isinstance(node, HostCall)
    assert node.fn_name == "get_ledger_timestamp"
    assert node.ty == Ty.U64
    assert node.args == ()


def test_ledger_sequence_is_u32() -> None:
    node = _ok_call("env.ledger().sequence()")
    assert isinstance(node, HostCall)
    assert node.fn_name == "get_ledger_sequence"
    assert node.ty == Ty.U32


def test_ledger_future_method_is_m2_pointer() -> None:
    _assert_reject(_reject_call("env.ledger().version()"), "SPT1033", "ledger")


def test_ledger_unknown_method_is_unresolved() -> None:
    _assert_reject(_reject_call("env.ledger().bogus()"), "SPT2006", "bogus")


# --- events ----------------------------------------------------------------


def test_events_publish() -> None:
    node = _ok_call('env.events().publish((Symbol("transfer"), addr), amt)')
    assert isinstance(node, HostCall)
    assert node.fn_name == "contract_event"
    assert node.ty == Ty.Void
    topics, data = node.args
    assert isinstance(topics, MakeTopics)
    assert len(topics.topics) == 2
    first = topics.topics[0]
    assert isinstance(first, Const) and first.py_value == "transfer"
    assert isinstance(data, ParamRef) and data.name == "amt"


def test_events_publish_accepts_a_long_first_topic() -> None:
    """Ruling E11: `SPT3019` means exactly one thing -- topics[0] is not a
    Symbol.

    The arm this replaces refused a hand-written `topics[0]` past the
    9-character `SymbolSmall` bound, which was dead code: the real bound is
    the Symbol's own 32 characters, enforced by `Symbol.__init__` (and by a
    frozen semantics reject case), so nothing longer could denote a Symbol
    here in the first place. A 12-character topic -- `round_closed`, the one
    `examples/events.py` publishes -- compiles, and pools through linear
    memory at the publish site like any other long Symbol.
    """
    node = _ok_call('env.events().publish((Symbol("round_closed"), addr), amt)')
    assert isinstance(node, HostCall)
    topics = node.args[0]
    assert isinstance(topics, MakeTopics)
    first = topics.topics[0]
    assert isinstance(first, Const) and first.py_value == "round_closed"


def test_the_symbol_bound_is_symbols_own_and_the_event_path_restates_no_length() -> None:
    """Ruling E11's other half, in two halves of its own.

    BEHAVIOR: the bound that survives is `Symbol`'s own, and it lives in the
    constructor -- 33 characters raise, 32 do not. That is where an event topic
    gets its length checked, and the only place this test proves anything about.

    SOURCE: a DELETION GUARD, scoped exactly to the two files ruling E11
    emptied -- `recognize.py` (`_is_short_symbol`) and `env.py`
    (`Events.publish`) -- asserting neither names `_is_short_symbol` or
    `fits_symbol_small` again. It has to be a grep rather than a behavior
    check, because a reintroduced check that AGREED with `Symbol` would be
    invisible to behavior. It deliberately does NOT claim more than that: it is
    not a tree-wide proof that nothing else bounds a topic's length (a
    hand-rolled `len(...) > 32` here, or a restatement in `frontend.py` or
    `decorators.py`, would pass it), only that the two deletions stay deleted.
    """
    with pytest.raises(ValueError, match="1-32 characters"):
        Symbol("a" * 33)
    assert Symbol("a" * 32).text == "a" * 32

    for module in (recognize, env_module):
        source = pathlib.Path(module.__file__ or "").read_text(encoding="utf-8")
        assert "fits_symbol_small" not in source, module.__name__
        assert "_is_short_symbol" not in source, module.__name__


def test_events_publish_rejects_a_non_symbol_first_topic() -> None:
    diag = _reject_call("env.events().publish((addr, addr), amt)")
    _assert_reject(diag, "SPT3019", "not Symbol")


def test_events_publish_rejects_an_empty_topics_tuple() -> None:
    """An empty topics tuple is a structurally malformed call shape, not a
    type mismatch -> the new SPT1038 (fix round 1)."""
    _assert_reject(_reject_call("env.events().publish((), amt)"), "SPT1038", "non-empty tuple")


def test_events_unknown_method() -> None:
    _assert_reject(_reject_call('env.events().subscribe("x")'), "SPT2006", "subscribe")


# --- auth --------------------------------------------------------------------


def test_require_auth() -> None:
    node = _ok_call("addr.require_auth()")
    assert isinstance(node, HostCall)
    assert node.fn_name == "require_auth"
    assert node.ty == Ty.Void
    (addr_ir,) = node.args
    assert isinstance(addr_ir, ParamRef) and addr_ir.name == "addr"


def test_require_auth_for_args() -> None:
    node = _ok_call("addr.require_auth_for_args(items)")
    assert isinstance(node, HostCall)
    assert node.fn_name == "require_auth_for_args"
    addr_ir, args_ir = node.args
    assert isinstance(addr_ir, ParamRef) and addr_ir.name == "addr"
    assert isinstance(args_ir, ParamRef) and args_ir.name == "items"


def test_require_auth_for_args_rejects_a_non_vec_argument() -> None:
    _assert_reject(_reject_call("addr.require_auth_for_args(amt)"), "SPT3018", "Vec")


def test_require_auth_on_a_non_address_is_rejected() -> None:
    _assert_reject(_reject_call("amt.require_auth()"), "SPT3018", "Address")


# --- Event.publish(env): the M1-E desugar (was SPT1032, ruling E12) ----------


def test_event_instance_publish_lowers_to_contract_event() -> None:
    """The row that used to be a REJECT. `test_frontend_events.py` owns the
    convention's translation table; what belongs HERE is that this module
    recognizes the receiver shape at all and reaches the table's host
    function."""
    node = _ok_call("Transfer(frm=addr, to=addr, amount=amt).publish(env)")
    assert isinstance(node, HostCall)
    assert node.fn_name == "contract_event"
    assert node.ty == Ty.Void
    topics, data = node.args
    assert isinstance(topics, MakeTopics)
    assert isinstance(topics.topics[0], Const)
    assert topics.topics[0].py_value == "transfer"
    assert isinstance(data, MakeStruct)


def test_events_dot_publish_is_not_the_event_instance_form() -> None:
    """`env.events().publish(...)` must NOT be caught by the `Event.publish`
    branch -- the two share a method name but not a receiver shape (and only the
    canonical one type-checks its topics as a tuple literal)."""
    node = _ok_call('env.events().publish((Symbol("t"), addr), amt)')
    assert isinstance(node, HostCall)
    assert node.fn_name == "contract_event"


# --- future-name vs. unknown-name split (KNOWN_FUTURE_ENV_NAMES) -------------


@pytest.mark.parametrize("name", sorted(KNOWN_FUTURE_ENV_NAMES))
def test_known_future_env_name_as_bare_attribute(name: str) -> None:
    _assert_reject(_reject_attr(f"env.{name}"), "SPT1033", f"env.{name}")


@pytest.mark.parametrize("name", sorted(KNOWN_FUTURE_ENV_NAMES))
def test_known_future_env_name_as_a_call(name: str) -> None:
    _assert_reject(_reject_call(f"env.{name}()"), "SPT1033", f"env.{name}")


def test_unknown_env_attribute_bare() -> None:
    _assert_reject(_reject_attr("env.frobnicate"), "SPT2006", "frobnicate")


def test_unknown_env_attribute_called() -> None:
    _assert_reject(_reject_call("env.frobnicate()"), "SPT2006", "frobnicate")


def test_bare_storage_attribute_must_be_called() -> None:
    """A recognized-but-uncalled Env attribute is an unsupported call shape,
    not a type mismatch -> the new SPT1038 (fix round 1)."""
    _assert_reject(_reject_attr("env.storage"), "SPT1038", "must be called and chained")


def test_known_future_names_and_core_surfaces_are_disjoint() -> None:
    assert KNOWN_FUTURE_ENV_NAMES.isdisjoint({"storage", "ledger", "events"})


# --- recognize_call returns None for anything outside this module's surface --


def test_recognize_call_returns_none_for_an_unrelated_call() -> None:
    ctx = _ctx()
    node = recognize_call(_parse_call("key.some_struct_method()"), ctx)
    assert node is None
    assert not ctx.sink


def test_recognize_attribute_returns_none_for_a_non_env_base() -> None:
    """A base that is neither `env` nor a struct is not this function's
    surface. (`key.owner` -- a struct FIELD read -- became `recognize_
    attribute`'s in Task 7b, and is tested in `test_containers_frontend.py`.)"""
    ctx = _ctx()
    node = recognize_attribute(_parse_attr("amt.whatever"), ctx)
    assert node is None
    assert not ctx.sink


# --- Ty.Invalid propagation: a failing sub-expression never cascades --------


def test_storage_set_with_a_bad_key_does_not_cascade() -> None:
    ctx = _ctx()
    node = recognize_call(_parse_call("env.storage().instance().set(nope, amt)"), ctx)
    assert node is not None
    assert node.ty.tag is TyTag.INVALID
    # The undefined-name diagnostic from `check_expr`, not a second one from
    # this module re-reporting the same failure.
    assert len(ctx.sink) == 1
    assert ctx.sink.diagnostics[0].code == "SPT2001"
