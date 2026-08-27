"""The Env-API recognition table: dossier SS C.4, Task 7a.

`src/serpent/env.py` is the AUTHORING surface -- `env.storage()...`,
`env.ledger()...`, `env.events()...`, `addr.require_auth()...` -- and this
module is where C recognizes that surface's exact AST shapes and lowers each
one to the IR's single escape hatch, `HostCall` (SS C.1). It covers env,
storage, ledger, events and auth ONLY; container/struct construction and
their method tables are Task 7b's, and internal calls are Task 8's (see the
`TASK-7A`-marked call sites `expr.py` already leaves for this module to fill
in, in `_check_attribute`/`_check_call`, once a later task wires the two
together -- this task's own scope is `recognize.py` and its tests only).

## `RECOGNIZED`: C authors the mapping table itself (MJ-3)

`RECOGNIZED: dict[str, HostCallSpec]` is the single source of truth for
"which host function(s) does this Python surface shape reach" -- every
lowering function below looks its OWN target name(s) up in this table rather
than hardcoding them a second time, so the table and the code can never
silently drift apart. dossier SS C.4's inventory for this surface (storage,
ledger, events, auth -- eleven host functions) is the ALLOWED TARGET SET:
`test_recognize_env.py`'s completeness assertion checks both directions --
every target `RECOGNIZED` names is a real key of `_host.functions_by_name`,
and no host function outside that eleven-name inventory is referenced here.

## The three-way split at an `env.<name>` (or `<bucket>.<name>`) attribute

Dossier SS C.4 draws two lines this module enforces structurally:

1. **Recognized and lowerable now** -- `storage`/`ledger`/`events`, and each
   bucket's `set`/`get`/`has`/`del_`/`extend_ttl`, `ledger()`'s `timestamp`/
   `sequence`, `events().publish`, `require_auth`/`require_auth_for_args`.
   Each becomes a `HostCall` (or, for `get(key, T, default=d)`, an `IfExp`
   over `has_contract_data`/`get_contract_data`/`default` -- SS C.4's own
   worked example of the get-default lowering).
2. **Recognized, not lowerable in M1** -- `KNOWN_FUTURE_ENV_NAMES` (`logs`,
   `call`, `try_call`, `crypto`, `prng`, `current_contract_address`,
   `deployer`) plus `Ledger`'s own still-undeclared M2 methods (`version`,
   `network_id`, `max_live_until_ledger`). These get `SPT1033`, the M2
   pointer -- a clean "lands later" diagnostic, never a traceback.
3. **Genuinely unknown** -- anything else reachable off `env` (or a storage
   bucket / `Ledger` / `Events`) gets `SPT2006`, the unresolved-attribute
   code. `Event.publish(env)` is its own dedicated reject, `SPT1032`
   (dossier ruling E12): `_serpent_type_` carries no topic/data split (B14),
   so M1-C recognizes only `env.events().publish(topics, data)` and rejects
   the `<Event instance>.publish(env)` form outright, pointing at sub-plan E.

## What this module deliberately does NOT decide

The missing-key runtime trap for `<bucket>.get(key, T)` with no `default`
(dossier SS C.4, "C must allocate the reserved code", E14) is ALREADY
allocated -- `serpent.errors.CODE_MISSING_VALUE`, whose own docstring names
this module as the recognition table that names it (`RECOGNIZED["storage.
get"].missing_value_code`). Inserting the runtime guard around a bare
`get_contract_data` `HostCall` is sub-plan D's job (SS C.1: D owns control
flow), matching the "one thin `HostCall`" principle -- this module lowers
`.get(key, T)` to exactly the same single `HostCall` the dossier row
describes and records the reserved code as data, rather than inventing an
IR shape the frozen SS C.2 node inventory has no node for (there is no
"trap" expression node, and `ErrorVal`/`Raise` are raise-position/statement
-only, dossier SS C.2).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum, auto

from serpent import errors, val
from serpent._host import STORAGE_TYPE, functions_by_name
from serpent.compiler import codes
from serpent.compiler.ctx import FuncCtx
from serpent.compiler.diagnostics import Loc
from serpent.compiler.expr import check_expr
from serpent.compiler.ir import (
    Const,
    HostCall,
    IfExp,
    IRExpr,
    MakeTopics,
    RawScalar,
    RawScalarKind,
)
from serpent.compiler.types_ import Ty, TyTag, resolve_annotation
from serpent.decorators import _METADATA_ATTR

__all__ = [
    "ENV_HOST_FN_TARGETS",
    "KNOWN_FUTURE_ENV_NAMES",
    "RECOGNIZED",
    "HostCallSpec",
    "SurfaceKind",
    "recognize_attribute",
    "recognize_call",
]

#: `code -> message_intent`, matching every other checker module's convention
#: (`expr.py`/`ctx.py`/`types_.py`) -- every diagnostic below carries its
#: registry row's own wording as the message's first clause.
_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

#: `help:` text for the SPT1xxx codes this module raises (mandatory --
#: `Diagnostics.error` rejects an SPT1xxx diagnostic with no `help`, F.2.11).
_HELP: dict[str, str] = {
    "SPT1032": "use env.events().publish(topics, data) instead",
    "SPT1033": "this Env surface is deferred to M2; there is no rewrite available yet",
    "SPT1035": "pass the argument positionally, or by the name the recognized API uses",
}


# --- the recognition table itself (MJ-3) --------------------------------------


class SurfaceKind(Enum):
    """The lowering SHAPE a `RECOGNIZED` row produces.

    `HOST_CALL`: exactly one `HostCall` (SS C.1's common case). `GET_DEFAULT`:
    the `has_contract_data` -> `IfExp` -> `get_contract_data`/`default`
    lowering SS C.4 spells out for `<bucket>.get(key, T, default=d)`.
    `REJECT`: never reaches a host function at all -- `Event.publish(env)`
    (E12), rejected pointing at sub-plan E.
    """

    HOST_CALL = auto()
    GET_DEFAULT = auto()
    REJECT = auto()


@dataclass(frozen=True, kw_only=True)
class HostCallSpec:
    """One row of the Env-API recognition table (dossier SS C.4).

    `surface` is the human-readable Python spelling, for documentation and
    test IDs. `host_fns` is every host function name this row can reach --
    one for a plain `HOST_CALL` row, two for `GET_DEFAULT` (`has_contract_
    data` then `get_contract_data`), empty for `REJECT`. Every name in it
    must be a key of `_host.functions_by_name` AND a member of `ENV_HOST_FN_
    TARGETS` (the completeness assertion checks both directions -- MJ-3).
    `reject_code` is set only for `REJECT` rows. `missing_value_code` is set
    only on `storage.get`: the reserved runtime code (dossier E14) sub-plan D
    must insert a guard for around the bare `get_contract_data` `HostCall`
    this row produces when the source gave no `default`.
    """

    surface: str
    kind: SurfaceKind
    host_fns: tuple[str, ...] = ()
    reject_code: str | None = None
    missing_value_code: int | None = None


RECOGNIZED: dict[str, HostCallSpec] = {
    "storage.set": HostCallSpec(
        surface="<bucket>.set(key, value)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("put_contract_data",),
    ),
    "storage.get": HostCallSpec(
        surface="<bucket>.get(key, T)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("get_contract_data",),
        missing_value_code=errors.CODE_MISSING_VALUE,
    ),
    "storage.get_default": HostCallSpec(
        surface="<bucket>.get(key, T, default=d)",
        kind=SurfaceKind.GET_DEFAULT,
        host_fns=("has_contract_data", "get_contract_data"),
    ),
    "storage.has": HostCallSpec(
        surface="<bucket>.has(key)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("has_contract_data",),
    ),
    "storage.del_": HostCallSpec(
        surface="<bucket>.del_(key)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("del_contract_data",),
    ),
    "storage.instance.extend_ttl": HostCallSpec(
        surface="instance().extend_ttl(threshold, extend_to)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("extend_current_contract_instance_and_code_ttl",),
    ),
    "storage.keyed.extend_ttl": HostCallSpec(
        surface="persistent()/temporary().extend_ttl(key, threshold, extend_to)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("extend_contract_data_ttl",),
    ),
    "ledger.timestamp": HostCallSpec(
        surface="env.ledger().timestamp()",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("get_ledger_timestamp",),
    ),
    "ledger.sequence": HostCallSpec(
        surface="env.ledger().sequence()",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("get_ledger_sequence",),
    ),
    "events.publish": HostCallSpec(
        surface="env.events().publish(topics, data)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("contract_event",),
    ),
    "address.require_auth": HostCallSpec(
        surface="addr.require_auth()",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("require_auth",),
    ),
    "address.require_auth_for_args": HostCallSpec(
        surface="addr.require_auth_for_args(args)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("require_auth_for_args",),
    ),
    "event.publish_reject": HostCallSpec(
        surface="<Event instance>.publish(env)",
        kind=SurfaceKind.REJECT,
        reject_code="SPT1032",
    ),
}

#: The union of every `RECOGNIZED` row's `host_fns` -- dossier SS C.4's
#: env/storage/ledger/events/auth inventory, eleven names, checked against
#: `_host.functions_by_name` by the completeness test (both directions).
ENV_HOST_FN_TARGETS: frozenset[str] = frozenset(
    fn for spec in RECOGNIZED.values() for fn in spec.host_fns
)

#: M2/future Env surfaces (dossier SS C.4's "Recognized but not lowerable in
#: M1" list, minus the ledger-nested names -- see `_LEDGER_FUTURE_METHODS`):
#: `env.logs()` (`log_from_linear_memory`), `env.call`/`env.try_call`,
#: `env.crypto()`, `env.prng()`, `env.current_contract_address()`,
#: `env.deployer()`. Reaching one of these draws `SPT1033`, the M2 pointer --
#: never `SPT2006` (unresolved), because the name IS recognized, just not yet
#: lowerable.
KNOWN_FUTURE_ENV_NAMES: frozenset[str] = frozenset(
    {
        "logs",
        "call",
        "try_call",
        "crypto",
        "prng",
        "current_contract_address",
        "deployer",
    }
)

#: `Ledger`'s own not-yet-declared M2 methods (dossier SS C.4: `get_ledger_
#: version` `x.2`, `get_max_live_until_ledger` `x.8`, `get_ledger_network_id`)
#: -- kept separate from `KNOWN_FUTURE_ENV_NAMES` because they live under
#: `env.ledger()`, not directly on `env`, and the M1 `Ledger` class
#: (`env.py`) declares only `timestamp`/`sequence`.
_LEDGER_FUTURE_METHODS: frozenset[str] = frozenset(
    {"version", "network_id", "max_live_until_ledger"}
)

#: The three `env.<name>` surfaces this module recognizes and lowers.
_CORE_ENV_SURFACES: frozenset[str] = frozenset({"storage", "ledger", "events"})

_STORAGE_BUCKETS: frozenset[str] = frozenset(STORAGE_TYPE)


# --- diagnostics helpers (matches expr.py's `_error`/`_invalid` convention) ---


def _error(
    ctx: FuncCtx,
    code: str,
    loc: Loc,
    detail: str = "",
    *,
    help: str | None = None,
    notes: tuple[str, ...] = (),
) -> None:
    intent = _INTENT[code]
    message = f"{intent}: {detail}" if detail else intent
    ctx.sink.error(
        code, loc, message, help=help if help is not None else _HELP.get(code), notes=notes
    )


def _invalid(loc: Loc) -> IRExpr:
    return Const(loc=loc, ty=Ty.Invalid, py_value=None)


def _failed(node: IRExpr) -> bool:
    return node.ty.tag is TyTag.INVALID


# --- argument binding (mirrors env.py's real signatures) ----------------------


def _bind(
    node: ast.Call,
    ctx: FuncCtx,
    loc: Loc,
    surface: str,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> dict[str, ast.expr] | None:
    """Bind `node`'s args/keywords against `required` (+ `optional`) parameter
    names, positionally then by keyword -- mirroring `env.py`'s own
    signatures (e.g. `get(self, key, ty, default=None)`). Returns `{name:
    ast.expr}` with every `required` name present (an absent `optional` name
    is simply not a key), or `None` after reporting (sink convention)."""
    names = required + optional
    if len(node.args) > len(names):
        _error(
            ctx,
            "SPT3018",
            loc,
            f"`{surface}` takes at most {len(names)} argument(s), got {len(node.args)}",
        )
        return None
    bound: dict[str, ast.expr] = dict(zip(names, node.args, strict=False))
    for keyword in node.keywords:
        if keyword.arg is None or keyword.arg not in names:
            shown = keyword.arg or "**"
            _error(ctx, "SPT1035", loc, f"`{shown}` is not a recognized keyword for `{surface}`")
            return None
        if keyword.arg in bound:
            _error(ctx, "SPT3018", loc, f"`{surface}` got multiple values for `{keyword.arg}`")
            return None
        bound[keyword.arg] = keyword.value
    missing = [name for name in required if name not in bound]
    if missing:
        _error(
            ctx,
            "SPT3018",
            loc,
            f"`{surface}` is missing required argument(s): {', '.join(missing)}",
        )
        return None
    return bound


# --- the entry points -----------------------------------------------------


def recognize_call(node: ast.Call, ctx: FuncCtx) -> IRExpr | None:
    """Recognize one `ast.Call` as part of the env/storage/ledger/events/auth
    surface, returning its lowered `IRExpr`.

    Returns `None` when `node` is not shaped like anything this module
    recognizes at all -- the caller (eventually `expr.py`'s `_check_call`,
    Task 7a/7b's integration) should then try Task 7b's container/struct
    method tables. Once the SHAPE is recognized (a real storage/ledger/
    events/auth call, however malformed), this function ALWAYS returns an
    `IRExpr` -- diagnosing through `ctx.sink` and returning the `Ty.Invalid`
    placeholder (sink convention, minor 13) rather than falling through, so a
    typo in a real env call is never silently treated as "not applicable".
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    method = func.attr
    base = func.value

    bucket = _match_storage_bucket(base)
    if bucket is not None:
        return _recognize_storage_method(node, ctx, bucket, method)

    if _match_no_arg_chain(base, "ledger"):
        return _recognize_ledger_method(node, ctx, method)

    if _match_no_arg_chain(base, "events"):
        return _recognize_events_method(node, ctx, method)

    if method == "publish" and _is_event_construction(base, ctx):
        return _reject_event_publish(node, ctx)

    if method in ("require_auth", "require_auth_for_args"):
        return _recognize_require_auth(node, ctx, method, base)

    if isinstance(base, ast.Name) and base.id == "env":
        return _recognize_env_top_level(ctx, Loc.from_node(ctx.path, node), method)

    return None


def recognize_attribute(node: ast.Attribute, ctx: FuncCtx) -> IRExpr | None:
    """Recognize a BARE (uncalled) `env.<name>` attribute reference.

    `storage`/`ledger`/`events` are only ever legitimately CALLED and
    immediately chained (`env.storage().instance()...`); every well-formed
    chain reaches `recognize_call` instead, via `_match_storage_bucket`/
    `_match_no_arg_chain`, before this function is ever consulted. This
    function exists for the shapes that never form a recognizable call at
    all -- a bare `env.storage` with no `()`, or `env.logs`/`env.frobnicate`
    -- which is exactly where SS C.4's future-name-vs-unknown-name split
    (`SPT1033` vs `SPT2006`) is observable on its own, without a call.
    """
    if not (isinstance(node.value, ast.Name) and node.value.id == "env"):
        return None
    return _recognize_env_top_level(ctx, Loc.from_node(ctx.path, node), node.attr)


def _recognize_env_top_level(ctx: FuncCtx, loc: Loc, name: str) -> IRExpr:
    if name in _CORE_ENV_SURFACES:
        _error(
            ctx,
            "SPT3018",
            loc,
            f"`env.{name}` must be called and chained, e.g. `env.{name}().<method>(...)`",
        )
        return _invalid(loc)
    if name in KNOWN_FUTURE_ENV_NAMES:
        _error(ctx, "SPT1033", loc, f"`env.{name}` is recognized but not lowerable in M1")
        return _invalid(loc)
    _error(
        ctx,
        "SPT2006",
        loc,
        f"`env` has no attribute `{name}`",
        help="see env.storage(), env.ledger(), env.events(), or an Address's require_auth()",
    )
    return _invalid(loc)


# --- structural matchers ---------------------------------------------------


def _is_env_name(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "env"


def _match_no_arg_chain(node: ast.expr, attr: str) -> bool:
    """Whether `node` is exactly `env.<attr>()` -- a no-argument call on the
    `env` name. Shared by `env.ledger()`/`env.events()`; `env.storage()` is
    matched separately (`_match_storage_bucket`) because a bucket call
    always follows it."""
    return (
        isinstance(node, ast.Call)
        and not node.args
        and not node.keywords
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
        and _is_env_name(node.func.value)
    )


def _match_storage_bucket(node: ast.expr) -> str | None:
    """Whether `node` is exactly `env.storage().<bucket>()` for a real
    `StorageType` bucket name; returns the bucket name or `None`."""
    if not isinstance(node, ast.Call) or node.args or node.keywords:
        return None
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _STORAGE_BUCKETS:
        return None
    if not _match_no_arg_chain(func.value, "storage"):
        return None
    return func.attr


def _is_event_construction(node: ast.expr, ctx: FuncCtx) -> bool:
    """Whether `node` constructs a `@contractevent` instance -- the receiver
    shape of the rejected `<Event instance>.publish(env)` form (E12)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Name):
        return False
    obj = ctx.loaded.namespace.get(func.id)
    if not isinstance(obj, type):
        return False
    metadata = vars(obj).get(_METADATA_ATTR)
    return isinstance(metadata, dict) and metadata.get("kind") == "event"


# --- storage ----------------------------------------------------------------


def _storage_type_immediate(bucket: str, loc: Loc) -> RawScalar:
    """The `StorageType` immediate (dossier B6): instance=2, persistent=1,
    temporary=0, from `_host._scalars.STORAGE_TYPE` -- never a compiler-local
    constant, so a re-pin of the host tables cannot silently drift from this
    module's own numbers."""
    return RawScalar(
        loc=loc, ty=Ty.U32, value=STORAGE_TYPE[bucket], kind=RawScalarKind.STORAGE_TYPE
    )


def _recognize_storage_method(node: ast.Call, ctx: FuncCtx, bucket: str, method: str) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)
    if method == "set":
        return _storage_set(node, ctx, loc, bucket)
    if method == "get":
        return _storage_get(node, ctx, loc, bucket)
    if method == "has":
        return _storage_has(node, ctx, loc, bucket)
    if method == "del_":
        return _storage_del(node, ctx, loc, bucket)
    if method == "extend_ttl":
        return _storage_extend_ttl(node, ctx, loc, bucket)
    _error(
        ctx,
        "SPT2006",
        loc,
        f"storage bucket has no method `{method}`",
        help="use .set/.get/.has/.del_/.extend_ttl",
    )
    return _invalid(loc)


def _storage_set(node: ast.Call, ctx: FuncCtx, loc: Loc, bucket: str) -> IRExpr:
    bound = _bind(node, ctx, loc, RECOGNIZED["storage.set"].surface, ("key", "value"))
    if bound is None:
        return _invalid(loc)
    key = check_expr(bound["key"], ctx)
    value = check_expr(bound["value"], ctx)
    if _failed(key) or _failed(value):
        return _invalid(loc)
    (fn_name,) = RECOGNIZED["storage.set"].host_fns
    return HostCall(
        loc=loc,
        ty=Ty.Void,
        fn_name=fn_name,
        args=(key, value, _storage_type_immediate(bucket, loc)),
    )


def _storage_has(node: ast.Call, ctx: FuncCtx, loc: Loc, bucket: str) -> IRExpr:
    bound = _bind(node, ctx, loc, RECOGNIZED["storage.has"].surface, ("key",))
    if bound is None:
        return _invalid(loc)
    key = check_expr(bound["key"], ctx)
    if _failed(key):
        return _invalid(loc)
    (fn_name,) = RECOGNIZED["storage.has"].host_fns
    # `has()` returns the chain `Bool` the host hands back (ruling: minor 9 /
    # decisions.md "storage keys" entry), NEVER a python bool.
    return HostCall(
        loc=loc, ty=Ty.Bool, fn_name=fn_name, args=(key, _storage_type_immediate(bucket, loc))
    )


def _storage_del(node: ast.Call, ctx: FuncCtx, loc: Loc, bucket: str) -> IRExpr:
    bound = _bind(node, ctx, loc, RECOGNIZED["storage.del_"].surface, ("key",))
    if bound is None:
        return _invalid(loc)
    key = check_expr(bound["key"], ctx)
    if _failed(key):
        return _invalid(loc)
    (fn_name,) = RECOGNIZED["storage.del_"].host_fns
    return HostCall(
        loc=loc, ty=Ty.Void, fn_name=fn_name, args=(key, _storage_type_immediate(bucket, loc))
    )


def _resolve_type_arg(node: ast.expr, ctx: FuncCtx, loc: Loc) -> Ty | None:
    """The `T` in `<bucket>.get(key, T)`: a bare `Name` naming a chain type
    or `@contracttype` struct. Container-generic forms (`Vec[U32]`) are
    Task 7b's extension of this function; only a bare `Name` is recognized
    here (SS C.4's own worked examples -- `get(NAME_KEY, String)`, `get(key,
    U32, default=U32(0))` -- are all bare type names)."""
    if not isinstance(node, ast.Name):
        _error(ctx, "SPT3018", loc, "the type argument must name a chain type directly")
        return None
    obj = ctx.loaded.namespace.get(node.id)
    if obj is None:
        _error(ctx, "SPT2001", loc, f"`{node.id}` is not defined in this contract")
        return None
    return resolve_annotation(obj, ctx.loaded, loc, ctx.sink)


def _storage_get(node: ast.Call, ctx: FuncCtx, loc: Loc, bucket: str) -> IRExpr:
    has_default = any(keyword.arg == "default" for keyword in node.keywords) or len(node.args) >= 3
    spec_key = "storage.get_default" if has_default else "storage.get"
    bound = _bind(
        node, ctx, loc, RECOGNIZED[spec_key].surface, ("key", "ty"), optional=("default",)
    )
    if bound is None:
        return _invalid(loc)

    target_ty = _resolve_type_arg(bound["ty"], ctx, loc)
    if target_ty is None:
        return _invalid(loc)

    key = check_expr(bound["key"], ctx)
    if _failed(key):
        return _invalid(loc)

    imm = _storage_type_immediate(bucket, loc)
    if "default" not in bound:
        # No `default`: a single `get_contract_data` HostCall, exactly SS
        # C.4's row. The missing-key runtime trap (`errors.CODE_MISSING_
        # VALUE`, dossier E14) is sub-plan D's guard to insert around this
        # HostCall -- see this module's docstring.
        (fn_name,) = RECOGNIZED["storage.get"].host_fns
        return HostCall(loc=loc, ty=target_ty, fn_name=fn_name, args=(key, imm))

    default_expr = check_expr(bound["default"], ctx, expected=target_ty)
    if _failed(default_expr):
        return _invalid(loc)
    if default_expr.ty != target_ty:
        _error(
            ctx,
            "SPT3018",
            loc,
            f"default value is {default_expr.ty.render()}, not {target_ty.render()}",
        )
        return _invalid(loc)

    has_fn, get_fn = RECOGNIZED["storage.get_default"].host_fns
    has_call = HostCall(loc=loc, ty=Ty.Bool, fn_name=has_fn, args=(key, imm))
    get_call = HostCall(loc=loc, ty=target_ty, fn_name=get_fn, args=(key, imm))
    return IfExp(loc=loc, ty=target_ty, cond=has_call, then=get_call, orelse=default_expr)


def _storage_extend_ttl(node: ast.Call, ctx: FuncCtx, loc: Loc, bucket: str) -> IRExpr:
    if bucket == "instance":
        spec = RECOGNIZED["storage.instance.extend_ttl"]
        bound = _bind(node, ctx, loc, spec.surface, ("threshold", "extend_to"))
        if bound is None:
            return _invalid(loc)
        threshold = check_expr(bound["threshold"], ctx, expected=Ty.U32)
        extend_to = check_expr(bound["extend_to"], ctx, expected=Ty.U32)
        if _failed(threshold) or _failed(extend_to):
            return _invalid(loc)
        if not _both_u32(ctx, loc, threshold, extend_to):
            return _invalid(loc)
        (fn_name,) = spec.host_fns
        return HostCall(loc=loc, ty=Ty.Void, fn_name=fn_name, args=(threshold, extend_to))

    spec = RECOGNIZED["storage.keyed.extend_ttl"]
    bound = _bind(node, ctx, loc, spec.surface, ("key", "threshold", "extend_to"))
    if bound is None:
        return _invalid(loc)
    key = check_expr(bound["key"], ctx)
    threshold = check_expr(bound["threshold"], ctx, expected=Ty.U32)
    extend_to = check_expr(bound["extend_to"], ctx, expected=Ty.U32)
    if _failed(key) or _failed(threshold) or _failed(extend_to):
        return _invalid(loc)
    if not _both_u32(ctx, loc, threshold, extend_to):
        return _invalid(loc)
    (fn_name,) = spec.host_fns
    imm = _storage_type_immediate(bucket, loc)
    return HostCall(loc=loc, ty=Ty.Void, fn_name=fn_name, args=(key, imm, threshold, extend_to))


def _both_u32(ctx: FuncCtx, loc: Loc, threshold: IRExpr, extend_to: IRExpr) -> bool:
    for name, side in (("threshold", threshold), ("extend_to", extend_to)):
        if side.ty != Ty.U32:
            _error(ctx, "SPT3018", loc, f"`{name}` must be U32, not {side.ty.render()}")
            return False
    return True


# --- ledger -------------------------------------------------------------------


def _recognize_ledger_method(node: ast.Call, ctx: FuncCtx, method: str) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)
    if method == "timestamp":
        spec = RECOGNIZED["ledger.timestamp"]
        bound = _bind(node, ctx, loc, spec.surface, ())
        if bound is None:
            return _invalid(loc)
        (fn_name,) = spec.host_fns
        return HostCall(loc=loc, ty=Ty.U64, fn_name=fn_name, args=())
    if method == "sequence":
        spec = RECOGNIZED["ledger.sequence"]
        bound = _bind(node, ctx, loc, spec.surface, ())
        if bound is None:
            return _invalid(loc)
        (fn_name,) = spec.host_fns
        return HostCall(loc=loc, ty=Ty.U32, fn_name=fn_name, args=())
    if method in _LEDGER_FUTURE_METHODS:
        _error(
            ctx, "SPT1033", loc, f"`env.ledger().{method}()` is recognized but not lowerable in M1"
        )
        return _invalid(loc)
    _error(
        ctx,
        "SPT2006",
        loc,
        f"`Ledger` has no method `{method}`",
        help="ledger() supports .timestamp() and .sequence() in M1",
    )
    return _invalid(loc)


# --- events -------------------------------------------------------------------


def _recognize_events_method(node: ast.Call, ctx: FuncCtx, method: str) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)
    if method != "publish":
        _error(
            ctx,
            "SPT2006",
            loc,
            f"`Events` has no method `{method}`",
            help="events() supports .publish(topics, data) in M1",
        )
        return _invalid(loc)
    return _events_publish(node, ctx, loc)


def _is_short_symbol(node: IRExpr) -> bool:
    """Whether `node` (already proven `Ty.Symbol`) is PROVABLY a short
    (<= 9 character) Symbol -- S11's `topics[0]` requirement. A literal
    `Symbol("...")` construction is checked against `val.fits_symbol_small`
    directly; a non-literal Symbol-typed expression (a param, a local, a
    module constant) is accepted, because M1 supports no long-Symbol host
    form at all yet (`Symbol.to_val()` raises `NotImplementedError` past 9
    characters -- sub-plan B), so nothing reaching this point could denote a
    long Symbol in the first place."""
    if isinstance(node, Const) and isinstance(node.py_value, str):
        return val.fits_symbol_small(node.py_value)
    return True


def _events_publish(node: ast.Call, ctx: FuncCtx, loc: Loc) -> IRExpr:
    spec = RECOGNIZED["events.publish"]
    bound = _bind(node, ctx, loc, spec.surface, ("topics", "data"))
    if bound is None:
        return _invalid(loc)

    topics_node = bound["topics"]
    if not isinstance(topics_node, ast.Tuple) or not topics_node.elts:
        _error(
            ctx,
            "SPT3018",
            loc,
            "topics must be a non-empty tuple, e.g. (Symbol('name'), addr)",
        )
        return _invalid(loc)

    topic_irs = [check_expr(elt, ctx) for elt in topics_node.elts]
    if any(_failed(topic) for topic in topic_irs):
        return _invalid(loc)

    first = topic_irs[0]
    if first.ty != Ty.Symbol or not _is_short_symbol(first):
        _error(ctx, "SPT3019", loc, f"topics[0] is {first.ty.render()}")
        return _invalid(loc)

    data = check_expr(bound["data"], ctx)
    if _failed(data):
        return _invalid(loc)

    # `MakeTopics.ty` has no dossier-specified meaning (SS C.2's node
    # inventory carries no distinct "topic tuple" Ty; the tuple is
    # heterogeneous by design, D8): it is consumed only as a `HostCall`
    # argument, never as a value in its own right, so `Ty.Void` is the
    # harmless placeholder every other Void-only IR position already uses.
    topics = MakeTopics(loc=loc, ty=Ty.Void, topics=tuple(topic_irs))
    (fn_name,) = spec.host_fns
    return HostCall(loc=loc, ty=Ty.Void, fn_name=fn_name, args=(topics, data))


def _reject_event_publish(node: ast.Call, ctx: FuncCtx) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)
    spec = RECOGNIZED["event.publish_reject"]
    assert spec.reject_code is not None
    _error(
        ctx,
        spec.reject_code,
        loc,
        "`<Event instance>.publish(env)` is deferred to sub-plan E",
    )
    return _invalid(loc)


# --- auth -----------------------------------------------------------------


def _recognize_require_auth(node: ast.Call, ctx: FuncCtx, method: str, base: ast.expr) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)
    addr = check_expr(base, ctx)
    if _failed(addr):
        return _invalid(loc)
    if addr.ty != Ty.Address:
        _error(
            ctx, "SPT3018", loc, f"`{method}()` is only defined on Address, not {addr.ty.render()}"
        )
        return _invalid(loc)

    if method == "require_auth":
        spec = RECOGNIZED["address.require_auth"]
        bound = _bind(node, ctx, loc, spec.surface, ())
        if bound is None:
            return _invalid(loc)
        (fn_name,) = spec.host_fns
        return HostCall(loc=loc, ty=Ty.Void, fn_name=fn_name, args=(addr,))

    spec = RECOGNIZED["address.require_auth_for_args"]
    bound = _bind(node, ctx, loc, spec.surface, ("args",))
    if bound is None:
        return _invalid(loc)
    args_expr = check_expr(bound["args"], ctx)
    if _failed(args_expr):
        return _invalid(loc)
    if args_expr.ty.tag is not TyTag.VEC:
        _error(
            ctx,
            "SPT3018",
            loc,
            f"`require_auth_for_args` needs a Vec argument, not {args_expr.ty.render()}",
        )
        return _invalid(loc)
    (fn_name,) = spec.host_fns
    return HostCall(loc=loc, ty=Ty.Void, fn_name=fn_name, args=(addr, args_expr))


# --- completeness support (MJ-3; the assertion itself lives in the test) -----


def target_functions() -> dict[str, object]:
    """Every `ENV_HOST_FN_TARGETS` name resolved against `_host.functions_
    by_name`; a `KeyError` here means `RECOGNIZED` names a host function the
    pinned `env.json` does not have -- the completeness test's forward
    direction, exposed here so the test needs no private access."""
    return {name: functions_by_name[name] for name in ENV_HOST_FN_TARGETS}
