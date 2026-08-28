"""The recognition table: dossier SS C.4 (Tasks 7a and 7b).

`src/serpent/env.py` and `src/serpent/types/` are the AUTHORING surface --
`env.storage()...`, `env.ledger()...`, `addr.require_auth()`, `Vec(U32, [...])`,
`v.push_back(x)`, `m.get(k)`, `Balance(amount=...)`, `bal.amount` -- and this
module is where C recognizes that surface's exact AST shapes and lowers each
one to the IR (SS C.1's single escape hatch `HostCall`, plus the `MakeVec`/
`MakeMap`/`MakeStruct`/`FieldGet` nodes SS C.2 already declares for the
container and struct shapes). Internal calls are Task 8's, and `Subscript` is
`expr.py`'s (MJ-13, for the import-cycle reason that module's docstring gives).
The `TASK-7A`/`TASK-7B`-marked call sites in `expr.py` are where a later
assembly task joins the two modules; nothing here imports back into that
dispatch.

## `RECOGNIZED`: C authors the mapping table itself (MJ-3)

`RECOGNIZED: dict[str, HostCallSpec]` is the single source of truth for
"which host function(s) does this Python surface shape reach" -- every
lowering function below looks its OWN target name(s) up in this table rather
than hardcoding them a second time, so the table and the code can never
silently drift apart. Each row declares its `family`, which is what splits the
two completeness assertions:

* `family="env"` -- dossier SS C.4's storage/ledger/events/auth inventory,
  eleven host functions, exactly `ENV_HOST_FN_TARGETS`
  (`test_recognize_env.py` checks both directions).
* `family="container"` -- SS C.4's Vec/Map/Bytes/struct inventory.
  `CONTAINER_HOST_FN_TARGETS` is what the rows reach and
  `UNREACHED_CONTAINER_HOST_FNS` names, WITH A REASON, every inventory member
  no row reaches: the ruled trio with no authoring surface at all
  (`vec_front`/`vec_back`/`vec_last_index_of`), the `*_len` family reached from
  `expr.py`'s `len()` instead (MJ-1), `bytes_get` reached from `expr.py`'s
  subscript (MJ-13), and `string_len`/`symbol_len`, which MJ-1's ruling makes
  unreachable on purpose. `test_containers_frontend.py` asserts the union is
  EXACTLY the dossier inventory, so neither direction can drift.

A second, differential assertion covers the container method rows: every one
must name a REAL method of the tier-1 class (`Vec`/`Map`/`Bytes`). Recognizing
a surface the oracle has no method for would be an "oracle-unrunnable accept",
the exact failure MJ-1's `len()` scoping ruling exists to avoid.

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
   code.

`Event.publish(env)` used to be a fourth case -- its own dedicated reject,
`SPT1032` (dossier ruling E12), because `_serpent_type_` carried no topic/data
split (B14) and a guessed one would have shipped a lying spec. M1-E Task 5
added the convention (`@contractevent(topics=..., data_format=...)` plus
`Annotated[T, topic]`), so Task 6 turned the reject into a DESUGAR: the
authoring form lowers to the same `HostCall("contract_event", (MakeTopics(...),
<data>))` the canonical `env.events().publish(topics, data)` spelling produces
(`_event_publish`). `SPT1032` is retired to `codes.NO_FIXTURE_ALLOWLIST` --
un-renumbered, per D9's append-only rule -- and both spellings are supported.
Event-instance-as-a-LOCAL stays rejected (`expr.py`'s `SPT1037`):
construction-and-publish in one expression is the shape the desugar reads.

## Diagnostic codes: matching the KIND of mistake, not just its severity

Fix round 1 (review finding) tightened four call sites that were all
reporting `SPT3018` ("declared-vs-actual type mismatch") for mistakes that
are not type mismatches at all:

* `_bind`'s three call-shape failures -- too many positional arguments, a
  missing required argument, a duplicate keyword -- are ARITY mistakes
  against a known signature, the same kind `SPT3020` already covers for a
  chain-type constructor's own arity check (widened, this round, to cover
  any recognized call, not just a constructor).
* A recognized Env attribute referenced without being called/chained
  (`env.storage`, no `()`) and a structurally malformed recognized call that
  is neither an arity nor a type mismatch (`env.events().publish((), d)`'s
  empty topics tuple) are UNSUPPORTED CALL SHAPES, not type disagreements --
  `SPT1038` (added this round).
* `_resolve_type_arg`'s non-`Name` type argument is an ANNOTATION-shape
  mistake, the same kind `SPT3013` already covers everywhere else in the
  compiler (Task 6's own annotation-shape fix made the identical call).

`SPT3018` stays exactly where the mistake really IS a type disagreement:
`_both_u32`'s threshold/extend_to check, the get-default `default` value's
type, and the Address/Vec receiver-type checks in `_recognize_require_auth`.

## Containers: the functional-host-op guard (E11) and MJ-15's key ordering

Two decisions C owns outright live in the container half:

* **Mutation is a REBIND, and only where C owns the binding** (E11/BL-3,
  F.1's #1 silent divergence). The host's ops are functional --
  `vec_push_back(v, x) -> VecObject` -- while `types.Vec.push_back` mutates in
  place, so `v.push_back(x)` is lowerable only as `v = vec_push_back(v, x)`.
  That rebind is a STATEMENT, so mutation is recognized through
  `recognize_mutation` (returning the existing `SetLocal` node -- no new IR
  node was needed) and is legal only when the receiver is an unaliased local
  slot whose `Ownership` is `OWNED`. `classify_binding`/`note_local_binding`
  are the standalone, table-tested pass that decides ownership; every other
  receiver -- an aliased local, an unclassified local, a parameter, a field
  read, an element read, or a TEMPORARY (`Vec(U32).pop_back()`) -- is
  `SPT1034`, carrying the functional-host-op explanation as a note and a
  rewrite in `help`. A mutator reached in a VALUE position is the same reject:
  an expression has no binding to rewrite (and tier 1's mutators return
  `None`, so `x = v.push_back(y)` is not valid there either). Ownership is also
  lost by EMBEDDING (`note_escapes`): once a local's handle is stored in
  another container or a struct, tier 1 can see a later mutation of it through
  that container and the chain cannot -- the mirror image of the element-read
  reject.
* **`MakeMap`'s literal keys** (MJ-15). When every key AND value is a
  compile-time literal and the ORACLE can totally order the keys
  (`static_map_order`, which delegates to tier 1's own `val_cmp` -- rank, then
  payload), C hands D the pairs already in the host's key order and sets
  `all_static=True` so `map_new_from_linear_memory` can be used directly.
  Otherwise `all_static=False`, SOURCE order is preserved untouched (F.1.12:
  any C-side reordering of a `Map` literal is observable on chain, and the
  value expressions' evaluation order is observable too), and D falls back to
  `map_new` + `map_put`, letting the host order them. C never invents an order
  the oracle cannot check (A15/E3 -- struct keys are exactly that case). On the
  static path C additionally proves the keys UNIQUE (`duplicate_static_key`,
  the same `val_cmp` relation): the linear-memory form cannot represent a
  repeated key, and tier 1 silently keeps the last one, so a repeat is
  `SPT1039` rather than either surprise.

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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from functools import cmp_to_key
from typing import Any

from serpent import errors, val
from serpent._host import STORAGE_TYPE, functions_by_name
from serpent.compiler import codes
from serpent.compiler.ctx import MUTABLE_TAGS, FuncCtx, Ownership
from serpent.compiler.diagnostics import Loc
from serpent.compiler.expr import (
    NODE_KIND_CODES,
    RECOGNIZED_BUILTINS,
    check_expr,
    oracle_class,
)
from serpent.compiler.ir import (
    Const,
    FieldGet,
    HostCall,
    IfExp,
    IRExpr,
    IRStmt,
    LocalRef,
    MakeMap,
    MakeStruct,
    MakeTopics,
    MakeVec,
    Nop,
    ParamRef,
    RawScalar,
    RawScalarKind,
    SetLocal,
)
from serpent.compiler.types_ import Ty, TyTag, resolve_annotation
from serpent.decorators import _METADATA_ATTR, DATA_LOCATION, TOPIC_LOCATION

# `val_cmp` is the ORACLE's own cross-type ordering (rank, then payload) and is
# what MJ-15 names for the `MakeMap` key pre-sort. It is not re-exported from
# `serpent.types`, so this is a deliberate private-name import -- the same
# discipline `_METADATA_ATTR` above already follows: reuse the one
# implementation rather than restate a model A15 calls explicitly partial.
from serpent.types import Map as _MapType
from serpent.types import Vec as _VecType
from serpent.types._ordering import val_cmp

__all__ = [
    "BYTES_METHODS",
    "CONTAINER_HOST_FN_TARGETS",
    "ENV_HOST_FN_TARGETS",
    "KNOWN_FUTURE_ENV_NAMES",
    "MAP_METHODS",
    "RECOGNIZED",
    "UNREACHED_CONTAINER_HOST_FNS",
    "VEC_METHODS",
    "BindingSource",
    "HostCallSpec",
    "SurfaceKind",
    "classify_binding",
    "collect_never_owned",
    "duplicate_static_key",
    "note_escapes",
    "note_local_binding",
    "recognize_attribute",
    "recognize_call",
    "recognize_mutation",
    "static_map_order",
]

#: `code -> message_intent`, matching every other checker module's convention
#: (`expr.py`/`ctx.py`/`types_.py`) -- every diagnostic below carries its
#: registry row's own wording as the message's first clause.
_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

#: MJ-11's catch-all, for the container shapes no other row describes: an
#: items/entries argument that is not a display written in place (D2/A13).
_FALLBACK_CODE = "SPT1037"

#: `help:` text for the SPT1xxx codes this module raises (mandatory --
#: `Diagnostics.error` rejects an SPT1xxx diagnostic with no `help`, F.2.11).
#: `SPT1034`'s entry is the LAST-RESORT default: every real emission passes a
#: receiver-specific `help` (see `_mutation_help`).
_HELP: dict[str, str] = {
    "SPT1034": (
        "mutate only a local this method owns, and let C rebind it (v = vec_push_back(v, x))"
    ),
    "SPT1037": "rewrite the expression using the serpent subset",
    "SPT1033": "this Env surface is deferred to M2; there is no rewrite available yet",
    "SPT1035": "pass the argument positionally, or by the name the recognized API uses",
    "SPT1038": (
        "call it and chain the recognized form, e.g. env.storage().instance().get(...), "
        "or env.events().publish((Symbol('name'), ...), data)"
    ),
    "SPT3019": "make topics[0] a short Symbol, e.g. Symbol('transfer')",
}

#: The comprehension node kinds, and the rewrite their SS B.2 row names. Used
#: by `_display_items` so a comprehension in an items position is reported as
#: the construct it is (SPT1003) rather than as a malformed display.
_COMPREHENSION_KINDS: tuple[type[ast.expr], ...] = (
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)

_COMPREHENSION_HELP = (
    "build the container explicitly -- Vec(U32, [...]) / Map(Symbol, U32, [...]) -- "
    "or fill it in a while loop"
)


# --- the recognition table itself (MJ-3) --------------------------------------


class SurfaceKind(Enum):
    """The lowering SHAPE a `RECOGNIZED` row produces.

    `HOST_CALL`: exactly one `HostCall` (SS C.1's common case) -- including
    `Event.publish(env)`, whose desugar produces one `contract_event` call.
    `GET_DEFAULT`: the `has_contract_data` -> `IfExp` ->
    `get_contract_data`/`default` lowering SS C.4 spells out for
    `<bucket>.get(key, T, default=d)`.
    `REJECT`: never reaches a host function at all. **No row uses it today**:
    the one that did (`Event.publish(env)`, `SPT1032`, dossier E12) became a
    lowering row in M1-E Task 6. The branch is kept for future rows -- it is the
    honest shape for a surface serpent recognizes and deliberately refuses, and
    reinventing it later would be pointless -- but with no row to exercise it,
    `test_recognize_env.py`'s REJECT-row invariants are now VACUOUSLY true
    rather than checked against anything.

    The container rows add four shapes (Task 7b). `MUTATOR`: a functional host
    op plus the E11 rebind, i.e. `SetLocal(slot, HostCall(...))` -- a
    STATEMENT, which is why it is reached through `recognize_mutation` and not
    through `recognize_call`. `MAKE_VEC`/`MAKE_MAP`/`MAKE_STRUCT`/`FIELD_GET`:
    the SS C.2 nodes of those names, whose `host_fns` record which host
    functions D can reach when it lowers them (D chooses between the
    linear-memory form and the build-up form -- MJ-15/`all_static`).
    """

    HOST_CALL = auto()
    GET_DEFAULT = auto()
    REJECT = auto()
    MUTATOR = auto()
    MAKE_VEC = auto()
    MAKE_MAP = auto()
    MAKE_STRUCT = auto()
    FIELD_GET = auto()


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
    #: Which SS C.4 inventory this row belongs to, and therefore which
    #: completeness assertion owns it: `"env"` (Task 7a: storage/ledger/
    #: events/auth) or `"container"` (Task 7b: Vec/Map/Bytes/struct). Default
    #: `"env"` so the Task 7a rows read exactly as they were authored.
    family: str = "env"


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
    "event.publish": HostCallSpec(
        # M1-E Task 6: the authoring form, DESUGARED. It reaches exactly the
        # host function `events.publish` reaches, over exactly the same
        # `MakeTopics`/data argument shape -- which is why the IR node
        # inventory and the emitter are untouched by the feature (ruling E2).
        # The container nodes the data payload may be (`MakeStruct` for the
        # `"map"` format, `MakeVec` for `"vec"`) account for their own host
        # functions through the frontend's node walk, exactly as they do when
        # an author writes the struct or the vector out by hand.
        surface="<Event instance>.publish(env)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("contract_event",),
    ),
    # --- containers and structs (Task 7b) ---------------------------------
    "vec.new": HostCallSpec(
        surface="Vec(T[, [items]])",
        kind=SurfaceKind.MAKE_VEC,
        host_fns=("vec_new", "vec_push_back", "vec_new_from_linear_memory"),
        family="container",
    ),
    "vec.push_back": HostCallSpec(
        surface="v.push_back(value)",
        kind=SurfaceKind.MUTATOR,
        host_fns=("vec_push_back",),
        family="container",
    ),
    "vec.push_front": HostCallSpec(
        surface="v.push_front(value)",
        kind=SurfaceKind.MUTATOR,
        host_fns=("vec_push_front",),
        family="container",
    ),
    "vec.pop_back": HostCallSpec(
        surface="v.pop_back()",
        kind=SurfaceKind.MUTATOR,
        host_fns=("vec_pop_back",),
        family="container",
    ),
    "vec.pop_front": HostCallSpec(
        surface="v.pop_front()",
        kind=SurfaceKind.MUTATOR,
        host_fns=("vec_pop_front",),
        family="container",
    ),
    "vec.get": HostCallSpec(
        surface="v.get(index)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("vec_get",),
        family="container",
    ),
    "vec.put": HostCallSpec(
        surface="v.put(index, value)",
        kind=SurfaceKind.MUTATOR,
        host_fns=("vec_put",),
        family="container",
    ),
    "vec.del_": HostCallSpec(
        surface="v.del_(index)",
        kind=SurfaceKind.MUTATOR,
        host_fns=("vec_del",),
        family="container",
    ),
    "vec.insert": HostCallSpec(
        surface="v.insert(index, value)",
        kind=SurfaceKind.MUTATOR,
        host_fns=("vec_insert",),
        family="container",
    ),
    "vec.append": HostCallSpec(
        surface="v.append(other)",
        kind=SurfaceKind.MUTATOR,
        host_fns=("vec_append",),
        family="container",
    ),
    "vec.slice": HostCallSpec(
        surface="v.slice(lo, hi)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("vec_slice",),
        family="container",
    ),
    "vec.first_index_of": HostCallSpec(
        surface="v.first_index_of(value)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("vec_first_index_of",),
        family="container",
    ),
    "map.new": HostCallSpec(
        surface="Map(K, V[, [(k, v), ...]])",
        kind=SurfaceKind.MAKE_MAP,
        host_fns=("map_new", "map_put", "map_new_from_linear_memory"),
        family="container",
    ),
    "map.set": HostCallSpec(
        surface="m.set(key, value)",
        kind=SurfaceKind.MUTATOR,
        host_fns=("map_put",),
        family="container",
    ),
    "map.get": HostCallSpec(
        surface="m.get(key)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("map_get",),
        family="container",
    ),
    "map.has": HostCallSpec(
        surface="m.has(key)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("map_has",),
        family="container",
    ),
    "map.del_": HostCallSpec(
        surface="m.del_(key)",
        kind=SurfaceKind.MUTATOR,
        host_fns=("map_del",),
        family="container",
    ),
    "map.keys": HostCallSpec(
        surface="m.keys()",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("map_keys",),
        family="container",
    ),
    "map.values": HostCallSpec(
        surface="m.values()",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("map_values",),
        family="container",
    ),
    "map.key_by_pos": HostCallSpec(
        surface="m.key_by_pos(position)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("map_key_by_pos",),
        family="container",
    ),
    "map.val_by_pos": HostCallSpec(
        surface="m.val_by_pos(position)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("map_val_by_pos",),
        family="container",
    ),
    "bytes.slice": HostCallSpec(
        surface="b.slice(lo, hi)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("bytes_slice",),
        family="container",
    ),
    "struct.new": HostCallSpec(
        # kwargs only, and C owns the ascending-byte-string field sort (P7):
        # `map_new_from_linear_memory` needs the key descriptors in that order
        # at COMPILE time, and the wrong layout validates then panics on-chain
        # (F.1.13).
        surface="MyStruct(field=value, ...)",
        kind=SurfaceKind.MAKE_STRUCT,
        host_fns=("map_new_from_linear_memory",),
        family="container",
    ),
    "struct.field": HostCallSpec(
        # SS C.4's struct-field-read row: the field's `Symbol` key (built with
        # `symbol_new_from_linear_memory` when it is over 9 characters -- D's
        # data-layout choice), then `map_get`.
        surface="value.field",
        kind=SurfaceKind.FIELD_GET,
        host_fns=("map_get", "symbol_new_from_linear_memory"),
        family="container",
    ),
}

#: The union of the `family="env"` rows' `host_fns` -- dossier SS C.4's
#: env/storage/ledger/events/auth inventory, eleven names, checked against
#: `_host.functions_by_name` by the completeness test (both directions).
ENV_HOST_FN_TARGETS: frozenset[str] = frozenset(
    fn for spec in RECOGNIZED.values() if spec.family == "env" for fn in spec.host_fns
)

#: The union of the `family="container"` rows' `host_fns` -- the reached half
#: of SS C.4's Vec/Map/Bytes/struct inventory.
CONTAINER_HOST_FN_TARGETS: frozenset[str] = frozenset(
    fn for spec in RECOGNIZED.values() if spec.family == "container" for fn in spec.host_fns
)

#: The OTHER half, with the reason each name is not reached by a row -- what
#: makes the container completeness assertion checkable in both directions
#: without silently tolerating a missing row (MJ-3).
UNREACHED_CONTAINER_HOST_FNS: Mapping[str, str] = {
    "vec_front": "no authoring surface: types.Vec has no front() method (ruled)",
    "vec_back": "no authoring surface: types.Vec has no back() method (ruled)",
    "vec_last_index_of": ("no authoring surface: types.Vec has only first_index_of() (ruled)"),
    "vec_len": "reached from expr.py's len() (MJ-1's ruled scope), not from a row",
    "map_len": "reached from expr.py's len() (MJ-1's ruled scope), not from a row",
    "bytes_len": "reached from expr.py's len() (MJ-1's ruled scope), not from a row",
    "bytes_get": "reached from expr.py's Bytes[i] subscript (MJ-13), not from a row",
    "string_len": "unreachable by ruling: len(String) is a compile reject (MJ-1)",
    "symbol_len": "unreachable by ruling: len(Symbol) is a compile reject (MJ-1)",
}

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
            "SPT3020",
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
            _error(ctx, "SPT3020", loc, f"`{surface}` got multiple values for `{keyword.arg}`")
            return None
        bound[keyword.arg] = keyword.value
    missing = [name for name in required if name not in bound]
    if missing:
        _error(
            ctx,
            "SPT3020",
            loc,
            f"`{surface}` is missing required argument(s): {', '.join(missing)}",
        )
        return None
    return bound


# --- the entry points -----------------------------------------------------


def recognize_call(node: ast.Call, ctx: FuncCtx) -> IRExpr | None:
    """Recognize one `ast.Call` as part of the surfaces this module owns --
    env/storage/ledger/events/auth (Task 7a) and container/struct
    construction plus the container READER methods (Task 7b) -- returning its
    lowered `IRExpr`.

    Returns `None` when `node` is not shaped like anything this module
    recognizes at all: a chain-type constructor (`expr.py`'s), an internal
    call (Task 8's), a struct method that does not exist, or a container
    method name on a non-container receiver. Once the SHAPE is recognized
    (a real env call or a real container/struct call, however malformed), this
    function ALWAYS returns an `IRExpr` -- diagnosing through `ctx.sink` and
    returning the `Ty.Invalid` placeholder (sink convention, minor 13) rather
    than falling through, so a typo in a real call is never silently treated
    as "not applicable".

    MUTATING container methods are deliberately NOT lowered here: their
    lowering is a rebind (a statement), so they belong to
    `recognize_mutation` and reaching one in a value position is `SPT1034`.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return _recognize_construction(node, ctx, func.id)
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

    if method == "publish":
        event = _event_construction(base, ctx)
        if event is not None:
            construction, name, metadata = event
            return _event_publish(node, ctx, construction, name, metadata)

    if method in ("require_auth", "require_auth_for_args"):
        return _recognize_require_auth(node, ctx, method, base)

    # BEFORE the container tables: `get`/`set`/`has`/`del_` are method names
    # both surfaces use, and `env.get(...)` is an Env mistake (SPT2006), not a
    # container one.
    if isinstance(base, ast.Name) and base.id == "env":
        return _recognize_env_top_level(ctx, Loc.from_node(ctx.path, node), method)

    if method in _CONTAINER_METHOD_NAMES:
        return _recognize_container_method(node, ctx, method, base)

    # Nothing above claimed the SHAPE. Before giving up, check whether this is
    # an `env` chain with one broken LINK (`env.storage(1).instance()...`):
    # the surface is recognized and supported, only miscalled, and saying so at
    # the offending step is worth far more than the catch-all's "not supported"
    # at the wrong link.
    malformed = _malformed_env_chain_step(node, ctx)
    if malformed is not None:
        return malformed

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

    It ALSO owns struct field reads (Task 7b, SS C.4's struct-field-read row),
    which is the one attribute shape that needs the base's TYPE rather than
    its syntax: any name can be a field name, so the base is checked and a
    `Ty.Struct` receiver becomes a `FieldGet`. A base that checks cleanly to
    anything else returns `None` with the sink untouched (the caller may
    re-check it, which is bounded by attribute-chain depth); a base whose own
    checking FAILED returns the `Ty.Invalid` placeholder, because its
    diagnostic is already in the sink and a `None` there would invite a
    second, cascaded one.
    """
    loc = Loc.from_node(ctx.path, node)
    if isinstance(node.value, ast.Name) and node.value.id == "env":
        return _recognize_env_top_level(ctx, loc, node.attr)
    base = _check_value(node.value, ctx)
    if _failed(base):
        return _invalid(loc)
    if base.ty.tag is TyTag.STRUCT:
        return _recognize_field_get(node, ctx, loc, base)
    return None


def _recognize_env_top_level(ctx: FuncCtx, loc: Loc, name: str) -> IRExpr:
    if name in _CORE_ENV_SURFACES:
        _error(
            ctx,
            "SPT1038",
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


#: Every step name an `env` chain is built from: the three core surfaces and
#: the three storage buckets. All of them take NO arguments, which is what
#: makes a broken link mechanically detectable.
_NO_ARG_CHAIN_STEPS: frozenset[str] = _CORE_ENV_SURFACES | _STORAGE_BUCKETS

_CHAIN_HELP = (
    "every step of an env chain takes no arguments -- write it exactly as "
    "`env.storage().<instance|persistent|temporary>().<method>(...)`, "
    "`env.ledger().<method>()` or `env.events().publish(topics, data)`"
)


def _chain_links(node: ast.expr) -> list[ast.expr]:
    """The `.`-chain `node` sits at the end of, OUTERMOST first.

    Each `ast.Call` is followed by its own `func`, and each `ast.Attribute` by
    its `value`, so the last element is the chain's root (a `Name` for a
    well-formed env chain).
    """
    links: list[ast.expr] = []
    current: ast.expr = node
    while True:
        links.append(current)
        if isinstance(current, ast.Call):
            current = current.func
        elif isinstance(current, ast.Attribute):
            current = current.value
        else:
            return links


def _malformed_env_chain_step(node: ast.expr, ctx: FuncCtx) -> IRExpr | None:
    """Report the first broken LINK of an `env` chain, or `None`.

    An env chain is built entirely from steps that take no arguments
    (`env.storage()`, `.instance()`, `.ledger()`, `.events()`), so a step whose
    NAME is one of them but whose call shape is not is a miscalled recognized
    API -- exactly what `SPT3020` covers ("a recognized API call with the wrong
    arguments"), and NOT an unsupported construct. Reporting it at that step is
    the point: `env.storage(1).instance().set(k, v)` breaks at `storage(1)`,
    while every structural matcher above simply fails to match and the
    catch-all would name `.instance` -- a link that is written correctly.

    Two broken shapes are recognized, and the chain is scanned ROOT-FIRST so
    the earliest bad link is the one named (every later failure cascades from
    it):

    * a recognized step name CALLED with arguments -> `SPT3020`;
    * a recognized core surface referenced without being called at all
      (`env.storage.instance()`) -> `SPT1038`, through the same
      `_recognize_env_top_level` that owns the standalone `env.storage` case,
      so the two spellings cannot drift apart.

    A third shape has every link written correctly but STOPS SHORT of a method
    (`x = env.storage().instance()`) -> `SPT1038` as well; see the comment at
    that branch.

    The chain must be rooted at the `env` NAME. Without that guard a struct
    field or local called `instance` would collect an env diagnostic
    (`holder.instance(1)`), which is the opposite of naming the right link.
    """
    links = _chain_links(node)
    if not _is_env_name(links[-1]):
        return None

    for index in range(len(links) - 1, -1, -1):
        link = links[index]
        if isinstance(link, ast.Call):
            func = link.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _NO_ARG_CHAIN_STEPS
                and (link.args or link.keywords)
            ):
                count = len(link.args) + len(link.keywords)
                _error(
                    ctx,
                    "SPT3020",
                    Loc.from_node(ctx.path, link),
                    f"`{func.attr}()` takes no arguments; got {count}",
                    help=_CHAIN_HELP,
                )
                return _invalid(Loc.from_node(ctx.path, node))
            continue
        if isinstance(link, ast.Attribute) and link.attr in _CORE_ENV_SURFACES:
            called = index > 0 and isinstance(links[index - 1], ast.Call)
            if not called and _is_env_name(link.value):
                _recognize_env_top_level(ctx, Loc.from_node(ctx.path, link), link.attr)
                return _invalid(Loc.from_node(ctx.path, node))

    # Every LINK is well formed, but the chain STOPS SHORT: a storage bucket is
    # not a value, it is the receiver a method is called on. `env.storage()`,
    # `env.ledger()` and `env.events()` already reach `_recognize_env_top_level`
    # through `recognize_call`'s own `env`-name branch; the bucket step is the
    # one spelling with no such branch, and it used to fall through to the
    # catch-all's "this construct is not supported". That is the same
    # wrong-in-kind wording the miscalled-step case above fixes: the surface IS
    # supported, and SPT1038 ("env API used with an unsupported call shape") is
    # its literal intent. The shared `help` names the missing method step.
    bucket = _match_storage_bucket(node)
    if bucket is not None:
        _error(
            ctx,
            "SPT1038",
            Loc.from_node(ctx.path, node),
            f"`env.storage().{bucket}()` selects a storage bucket; it is not a value on its own",
        )
        return _invalid(Loc.from_node(ctx.path, node))
    return None


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


def _event_construction(
    node: ast.expr, ctx: FuncCtx
) -> tuple[ast.Call, str, Mapping[str, Any]] | None:
    """`(the construction call, the event's name, its metadata)`, or `None`.

    The receiver shape of `<Event instance>.publish(env)`: a DIRECT
    construction of a `@contractevent` class. `vars(obj)`, not `getattr`, for
    the same reason `_recognize_construction` reads a struct's metadata that
    way -- an undecorated subclass of an event inherits `_serpent_type_` and is
    not itself declared.
    """
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not isinstance(func, ast.Name):
        return None
    obj = ctx.loaded.namespace.get(func.id)
    if not isinstance(obj, type):
        return None
    metadata = vars(obj).get(_METADATA_ATTR)
    if not isinstance(metadata, dict) or metadata.get("kind") != "event":
        return None
    return node, func.id, metadata


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
    key = _check_value(bound["key"], ctx)
    value = _check_value(bound["value"], ctx)
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
    key = _check_value(bound["key"], ctx)
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
    key = _check_value(bound["key"], ctx)
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
        _error(
            ctx,
            "SPT3013",
            loc,
            "the type argument must name a chain type directly, e.g. `get(key, U32)`",
            help="pass a bare chain type or @contracttype struct name, not an expression",
        )
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

    key = _check_value(bound["key"], ctx)
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

    default_expr = _check_value(bound["default"], ctx, expected=target_ty)
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
        threshold = _check_value(bound["threshold"], ctx, expected=Ty.U32)
        extend_to = _check_value(bound["extend_to"], ctx, expected=Ty.U32)
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
    key = _check_value(bound["key"], ctx)
    threshold = _check_value(bound["threshold"], ctx, expected=Ty.U32)
    extend_to = _check_value(bound["extend_to"], ctx, expected=Ty.U32)
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
            "SPT1038",
            loc,
            "topics must be a non-empty tuple literal, e.g. (Symbol('name'), addr)",
            help="pass a non-empty tuple literal, e.g. (Symbol('name'), addr)",
        )
        return _invalid(loc)

    topic_irs = [_check_value(elt, ctx) for elt in topics_node.elts]
    if any(_failed(topic) for topic in topic_irs):
        return _invalid(loc)

    first = topic_irs[0]
    if first.ty != Ty.Symbol:
        _error(ctx, "SPT3019", loc, f"topics[0] is {first.ty.render()}, not Symbol")
        return _invalid(loc)
    if not _is_short_symbol(first):
        _error(
            ctx,
            "SPT3019",
            loc,
            "topics[0] is too long; event topic Symbols must be <= 9 characters",
        )
        return _invalid(loc)

    data = _check_value(bound["data"], ctx)
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


def _event_publish(
    node: ast.Call,
    ctx: FuncCtx,
    construction: ast.Call,
    name: str,
    metadata: Mapping[str, Any],
) -> IRExpr:
    """`Transfer(from_=a, to=b, amount=x).publish(env)`, DESUGARED (ruling E2).

    The whole point of this function is that it produces NO new IR: the
    authoring form lowers to the same `HostCall("contract_event", (MakeTopics
    (...), <data>))` that `env.events().publish(topics, data)` produces, so the
    emitter needs no knowledge of events at all (`test_frontend_events.py`
    asserts the two trees are equal).

    The convention is read back from `@contractevent`'s metadata, never
    re-derived (Task 5 validated all of it at the declaration site):

    * TOPICS -- every `prefix_topics` entry as a `Const` `Symbol`, in order,
      then every field whose location is `"topic"` in DECLARATION order. A
      prefix topic past nine characters is legal and simply pools through
      linear memory; it needs no special case here, because the ordinary
      `Const` walk in `frontend.py` puts it in `symbols_over_9` and adds
      `symbol_new_from_linear_memory` to the host-function set.
    * DATA -- per `data_format`: `"map"` is a `MakeStruct` over the non-topic
      fields (P7-sorted keys, runtime values -- byte-for-byte a struct's
      lowering, which is also what feeds `struct_key_descriptor_sets` and
      `needs_memory`), `"vec"` a `MakeVec` in declaration order, and
      `"single-value"` the lone data field's expression, bare.

    Construction is KWARGS-ONLY and type-checked through the very same helper
    `@contracttype` construction uses (review B3): one rule, one message set,
    one checker path.
    """
    loc = Loc.from_node(ctx.path, node)
    spec = RECOGNIZED["event.publish"]
    bound = _bind(node, ctx, loc, spec.surface, ("env",))
    if bound is None:
        return _invalid(loc)
    if not _is_env_name(bound["env"]):
        _error(
            ctx,
            "SPT1038",
            loc,
            "`publish` takes the method's `env` parameter",
            help=f"write {name}(...).publish(env)",
        )
        return _invalid(loc)

    fields: list[tuple[str, Any]] = [
        (str(field_name), annotation) for field_name, annotation in metadata["fields"]
    ]
    values = _bind_record_fields(
        construction, ctx, Loc.from_node(ctx.path, construction), name, fields
    )
    if values is None:
        return _invalid(loc)

    locations: Mapping[str, str] = metadata["locations"]
    topics: list[IRExpr] = [
        Const(loc=loc, ty=Ty.Symbol, py_value=prefix) for prefix in metadata["prefix_topics"]
    ]
    topics += [
        values[field_name]
        for field_name, _annotation in fields
        if locations[field_name] == TOPIC_LOCATION
    ]

    data = _event_data(ctx, loc, name, metadata["data_format"], fields, locations, values)
    if data is None or _failed(data):
        return _invalid(loc)

    # `MakeTopics.ty` is `Ty.Void` for `_events_publish`'s own reason: the
    # heterogeneous topic tuple is consumed only as a `HostCall` argument.
    (fn_name,) = spec.host_fns
    return HostCall(
        loc=loc,
        ty=Ty.Void,
        fn_name=fn_name,
        args=(MakeTopics(loc=loc, ty=Ty.Void, topics=tuple(topics)), data),
    )


def _event_data(
    ctx: FuncCtx,
    loc: Loc,
    name: str,
    data_format: str,
    fields: Sequence[tuple[str, Any]],
    locations: Mapping[str, str],
    values: Mapping[str, IRExpr],
) -> IRExpr | None:
    """The `data` argument of one desugared publish, per `data_format`.

    `None` after reporting (sink convention). Every arity and uniformity
    question is already settled at the declaration site (`decorators.
    _check_data_format`): `"single-value"` has exactly one data field,
    `"map"`/`"vec"` at least one, and a `"vec"` payload's fields all share one
    type -- which is what lets `MakeVec` carry a single `elem_ty` here. The
    asserts below are those guarantees, not checks a source can trip.
    """
    data_fields = [
        (field_name, annotation)
        for field_name, annotation in fields
        if locations[field_name] == DATA_LOCATION
    ]
    assert data_fields, f"@contractevent {name} declares no data field for {data_format!r}"

    if data_format == "single-value":
        assert len(data_fields) == 1, f"@contractevent {name} is not single-valued"
        (only_name, _annotation) = data_fields[0]
        return values[only_name]

    if data_format == "vec":
        elem_ty = resolve_annotation(data_fields[0][1], ctx.loaded, loc, ctx.sink)
        if elem_ty is None:
            return None
        items = [values[field_name] for field_name, _annotation in data_fields]
        return MakeVec(
            loc=loc,
            ty=Ty.Vec(elem_ty),
            elem_ty=elem_ty,
            items=tuple(items),
            all_static=_all_static(items),
        )

    # `"map"`: the struct lowering, keyed by field name. C owns the P7 sort
    # (`map_new_from_linear_memory` needs the key descriptors ascending as byte
    # strings at COMPILE time) exactly as it does for `MyStruct(...)`.
    assert data_format == "map", data_format
    pairs = [(field_name, values[field_name]) for field_name, _annotation in data_fields]
    pairs.sort(key=lambda item: item[0].encode())
    # No `note_escapes` (unlike `_struct_construction`): this struct is built
    # for one `contract_event` argument and is never bound, stored or returned,
    # so no local's handle survives inside it -- the same reason
    # `_events_publish` does not mark its own arguments as escaping.
    return MakeStruct(loc=loc, ty=Ty.Struct(name), struct_name=name, fields=tuple(pairs))


# --- auth -----------------------------------------------------------------


def _recognize_require_auth(node: ast.Call, ctx: FuncCtx, method: str, base: ast.expr) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)
    addr = _check_value(base, ctx)
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
    args_expr = _check_value(bound["args"], ctx)
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


# --- containers and structs: the method tables (MJ-3, Task 7b) --------------

#: `method name -> row key`, one table per receiver family, authored from the
#: REAL tier-1 API (`types/containers.py`, `types/buffers.py`). The
#: differential test walks these dicts and asserts each name is a real method
#: of the tier-1 class, so a row can never recognize a surface the oracle
#: cannot run. `Vec` has no `front`/`back`/`last_index_of` and no `len`
#: method (`len()` is the builtin, MJ-1), which is why those host functions
#: appear only in `UNREACHED_CONTAINER_HOST_FNS`.
VEC_METHODS: Mapping[str, str] = {
    "push_back": "vec.push_back",
    "push_front": "vec.push_front",
    "pop_back": "vec.pop_back",
    "pop_front": "vec.pop_front",
    "get": "vec.get",
    "put": "vec.put",
    "del_": "vec.del_",
    "insert": "vec.insert",
    "append": "vec.append",
    "slice": "vec.slice",
    "first_index_of": "vec.first_index_of",
}

MAP_METHODS: Mapping[str, str] = {
    "set": "map.set",
    "get": "map.get",
    "has": "map.has",
    "del_": "map.del_",
    "keys": "map.keys",
    "values": "map.values",
    "key_by_pos": "map.key_by_pos",
    "val_by_pos": "map.val_by_pos",
}

#: `Bytes` is immutable, so its only row is the sub-range READER. The method
#: itself was a RULED tier-1 addition (E18/MJ-1: E18's method-form slicing
#: needs a method to name), landed in Task 8 -- so the differential test now
#: checks this row with no exemption.
BYTES_METHODS: Mapping[str, str] = {"slice": "bytes.slice"}

#: The syntactic gate: only an attribute call whose method name appears here is
#: even considered a container surface, so an unrelated method call
#: (`key.some_struct_method()`) is still "not recognized at all" and the
#: receiver is never checked for it.
_CONTAINER_METHOD_NAMES: frozenset[str] = (
    frozenset(VEC_METHODS) | frozenset(MAP_METHODS) | frozenset(BYTES_METHODS)
)

#: Receiver tags that have a method table at all.
_CONTAINER_TAGS: frozenset[TyTag] = frozenset({TyTag.VEC, TyTag.MAP, TyTag.BYTES, TyTag.BYTES_N})

#: The tags whose values are MUTABLE at tier 1 -- the ones E11's alias
#: analysis tracks -- imported from `ctx.py`, where the alias STATE and its
#: shared escape walk live (Task 8 moved both down so `expr.py`'s
#: internal-call site can reach them without importing this module).
_MUTABLE_TAGS: frozenset[TyTag] = MUTABLE_TAGS


class _ArgKind(Enum):
    """What a container method's argument must be typed as, RELATIVE to the
    receiver (whose `Ty` carries the element/key/value types)."""

    INDEX = auto()
    ELEM = auto()
    KEY = auto()
    VALUE = auto()
    SAME = auto()


class _ResultKind(Enum):
    """What a container method's result is typed as, relative to the receiver.

    `BOOL` is `Map.has`: tier 1 answers a PLAIN python `bool` there while the
    host's `map_has` returns a chain `Bool` (F.1.5's named asymmetry). The
    compiler has one `Bool`, so it types this precisely as the chain one and
    the divergence is one-way and documented, exactly like `len()` (E19).
    """

    RECEIVER = auto()
    ELEM = auto()
    KEY = auto()
    VALUE = auto()
    VEC_OF_KEYS = auto()
    VEC_OF_VALUES = auto()
    OPTION_U32 = auto()
    BOOL = auto()
    BYTES = auto()


@dataclass(frozen=True, kw_only=True)
class _MethodShape:
    """One method row's call shape: the tier-1 PARAMETER NAMES (so a keyword
    argument is accepted under the name the real API uses, minor 8), the type
    each argument must have, and the result type."""

    params: tuple[str, ...]
    args: tuple[_ArgKind, ...]
    result: _ResultKind


_METHOD_SHAPES: Mapping[str, _MethodShape] = {
    "vec.push_back": _MethodShape(
        params=("value",), args=(_ArgKind.ELEM,), result=_ResultKind.RECEIVER
    ),
    "vec.push_front": _MethodShape(
        params=("value",), args=(_ArgKind.ELEM,), result=_ResultKind.RECEIVER
    ),
    "vec.pop_back": _MethodShape(params=(), args=(), result=_ResultKind.RECEIVER),
    "vec.pop_front": _MethodShape(params=(), args=(), result=_ResultKind.RECEIVER),
    "vec.get": _MethodShape(params=("index",), args=(_ArgKind.INDEX,), result=_ResultKind.ELEM),
    "vec.put": _MethodShape(
        params=("index", "value"),
        args=(_ArgKind.INDEX, _ArgKind.ELEM),
        result=_ResultKind.RECEIVER,
    ),
    "vec.del_": _MethodShape(
        params=("index",), args=(_ArgKind.INDEX,), result=_ResultKind.RECEIVER
    ),
    "vec.insert": _MethodShape(
        params=("index", "value"),
        args=(_ArgKind.INDEX, _ArgKind.ELEM),
        result=_ResultKind.RECEIVER,
    ),
    "vec.append": _MethodShape(
        params=("other",), args=(_ArgKind.SAME,), result=_ResultKind.RECEIVER
    ),
    "vec.slice": _MethodShape(
        params=("lo", "hi"),
        args=(_ArgKind.INDEX, _ArgKind.INDEX),
        result=_ResultKind.RECEIVER,
    ),
    "vec.first_index_of": _MethodShape(
        params=("value",), args=(_ArgKind.ELEM,), result=_ResultKind.OPTION_U32
    ),
    "map.set": _MethodShape(
        params=("key", "value"),
        args=(_ArgKind.KEY, _ArgKind.VALUE),
        result=_ResultKind.RECEIVER,
    ),
    "map.get": _MethodShape(params=("key",), args=(_ArgKind.KEY,), result=_ResultKind.VALUE),
    "map.has": _MethodShape(params=("key",), args=(_ArgKind.KEY,), result=_ResultKind.BOOL),
    "map.del_": _MethodShape(params=("key",), args=(_ArgKind.KEY,), result=_ResultKind.RECEIVER),
    "map.keys": _MethodShape(params=(), args=(), result=_ResultKind.VEC_OF_KEYS),
    "map.values": _MethodShape(params=(), args=(), result=_ResultKind.VEC_OF_VALUES),
    "map.key_by_pos": _MethodShape(
        params=("position",), args=(_ArgKind.INDEX,), result=_ResultKind.KEY
    ),
    "map.val_by_pos": _MethodShape(
        params=("position",), args=(_ArgKind.INDEX,), result=_ResultKind.VALUE
    ),
    "bytes.slice": _MethodShape(
        params=("lo", "hi"),
        args=(_ArgKind.INDEX, _ArgKind.INDEX),
        result=_ResultKind.BYTES,
    ),
}


def _resolve_container_row(recv_ty: Ty, method: str) -> str | None:
    """The row key for `<recv_ty>.<method>`, or `None` when that receiver has
    no such method (`m.push_back(...)`)."""
    if recv_ty.tag is TyTag.VEC:
        return VEC_METHODS.get(method)
    if recv_ty.tag is TyTag.MAP:
        return MAP_METHODS.get(method)
    if recv_ty.tag in (TyTag.BYTES, TyTag.BYTES_N):
        return BYTES_METHODS.get(method)
    return None


def _methods_of(recv_ty: Ty) -> tuple[str, ...]:
    if recv_ty.tag is TyTag.VEC:
        return tuple(VEC_METHODS)
    if recv_ty.tag is TyTag.MAP:
        return tuple(MAP_METHODS)
    return tuple(BYTES_METHODS)


def _expected_arg_ty(kind: _ArgKind, recv_ty: Ty) -> Ty:
    if kind is _ArgKind.INDEX:
        return Ty.U32
    if kind is _ArgKind.SAME:
        return recv_ty
    if kind is _ArgKind.ELEM:
        assert recv_ty.elem is not None
        return recv_ty.elem
    if kind is _ArgKind.KEY:
        assert recv_ty.key is not None
        return recv_ty.key
    assert recv_ty.value is not None
    return recv_ty.value


def _result_ty(kind: _ResultKind, recv_ty: Ty) -> Ty:
    if kind is _ResultKind.RECEIVER:
        return recv_ty
    if kind is _ResultKind.ELEM:
        assert recv_ty.elem is not None
        return recv_ty.elem
    if kind is _ResultKind.KEY:
        assert recv_ty.key is not None
        return recv_ty.key
    if kind is _ResultKind.VALUE:
        assert recv_ty.value is not None
        return recv_ty.value
    if kind is _ResultKind.VEC_OF_KEYS:
        assert recv_ty.key is not None
        return Ty.Vec(recv_ty.key)
    if kind is _ResultKind.VEC_OF_VALUES:
        assert recv_ty.value is not None
        return Ty.Vec(recv_ty.value)
    if kind is _ResultKind.OPTION_U32:
        # `vec_first_index_of` returns the host's `Option<u32>` (a `Val`), and
        # tier 1 returns `U32 | None` -- the same shape.
        return Ty.Option(Ty.U32)
    if kind is _ResultKind.BOOL:
        return Ty.Bool
    # `bytes_slice` hands back a variable-length BytesObject even for a
    # fixed-length receiver: a sub-range of a Bytes32 is not a Bytes32.
    return Ty.Bytes


def _assignable(value: Ty, declared: Ty) -> bool:
    """Whether a `value`-typed expression may be stored where `declared` is
    required -- container elements, map keys/values, and struct fields.

    This is F.1.8's asymmetry, and it is deliberately NOT `expr.py`'s
    `_comparable`: tier 1's element/key check is `isinstance`, so a
    fixed-length `BytesN` IS accepted where plain `Bytes` is declared (a
    `Bytes32` is a `Bytes`), while a plain `Bytes` where `Bytes32` is declared
    is a tier-1 `TypeError` -- even though the host would happily accept it.
    C reproduces the ORACLE's strictness, never the host's permissiveness.

    The `Option` clause mirrors `expr.py`'s own literal rule: a `T` where
    `T | None` is declared is the ordinary widening (a struct field annotated
    `Symbol | None` takes a `Symbol`).
    """
    if value == declared:
        return True
    if declared.tag is TyTag.BYTES and value.tag is TyTag.BYTES_N:
        return True
    if declared.tag is TyTag.VEC and value.tag is TyTag.VEC:
        assert declared.elem is not None and value.elem is not None
        return _assignable(value.elem, declared.elem)
    if declared.tag is TyTag.OPTION and value.tag is not TyTag.OPTION:
        assert declared.elem is not None
        return _assignable(value, declared.elem)
    return False


def _check_value(node: ast.expr, ctx: FuncCtx, *, expected: Ty | None = None) -> IRExpr:
    """Check one sub-expression, letting THIS module's own surfaces be reached
    inside it.

    `expr.py`'s dispatch cannot call into this module (it would be an import
    cycle -- see that module's docstring), so a container construction, a
    container method, or a struct field read nested inside a recognized call
    (`env.storage().instance().set(k, Vec(U32, [...]))`, `m.get(k).get(i)`)
    has to be routed here explicitly until the assembly task joins the two
    dispatches. `check_expr` remains the fallback for everything else, and
    `expected` is passed through for literal coercion (S3).
    """
    if isinstance(node, ast.Call):
        recognized = recognize_call(node, ctx)
        if recognized is not None:
            return recognized
    elif isinstance(node, ast.Attribute):
        recognized = recognize_attribute(node, ctx)
        if recognized is not None:
            return recognized
    return check_expr(node, ctx, expected=expected)


# --- container construction (D2/A13) ----------------------------------------


def _recognize_construction(node: ast.Call, ctx: FuncCtx, name: str) -> IRExpr | None:
    """`Vec(T[, items])`, `Map(K, V[, entries])`, `MyStruct(field=...)`.

    Returns `None` for any other `name(...)` call -- a chain-type constructor
    (`U32(5)`), an event construction, a helper call -- none of which is this
    module's surface.
    """
    obj = ctx.loaded.namespace.get(name)
    loc = Loc.from_node(ctx.path, node)
    if obj is _VecType:
        return _vec_construction(node, ctx, loc)
    if obj is _MapType:
        return _map_construction(node, ctx, loc)
    if isinstance(obj, type):
        metadata = vars(obj).get(_METADATA_ATTR)
        if isinstance(metadata, dict) and metadata.get("kind") == "struct":
            return _struct_construction(node, ctx, loc, name, metadata)
    return None


def _display_items(
    node: ast.expr | None, ctx: FuncCtx, loc: Loc, example: str
) -> list[ast.expr] | None:
    """The elements of a LIST DISPLAY argument, or `None` after reporting.

    D2/A13: a list display is a value only in these two positions, so the
    items argument must be spelled literally -- there is no iterable protocol
    on chain to accept anything else.
    """
    if node is None:
        return []
    if isinstance(node, _COMPREHENSION_KINDS):
        # MJ-14 reconciliation (Task 10): a comprehension HAS its own SS B.2
        # reject row, and `tests/must_reject/constructs/comprehension_list.py`
        # declares it. `Vec(U32, [x + U32(1) for x in v])` is "comprehensions
        # are not supported" -- with the container rewrite in its `help:` --
        # not the generic "the items must be a list display", which describes
        # the shape rather than the construct the author actually wrote.
        _error(
            ctx,
            NODE_KIND_CODES[type(node)],
            Loc.from_node(ctx.path, node),
            f"`{type(node).__name__}` is not part of the serpent subset",
            help=_COMPREHENSION_HELP,
        )
        return None
    if not isinstance(node, ast.List):
        _error(
            ctx,
            _FALLBACK_CODE,
            loc,
            "the items must be a list display written in place",
            help=f"pass a list display, e.g. {example}",
        )
        return None
    return list(node.elts)


def _vec_construction(node: ast.Call, ctx: FuncCtx, loc: Loc) -> IRExpr:
    spec = RECOGNIZED["vec.new"]
    bound = _bind(node, ctx, loc, spec.surface, ("element_type",), optional=("items",))
    if bound is None:
        return _invalid(loc)
    elem_ty = _resolve_type_arg(bound["element_type"], ctx, loc)
    if elem_ty is None:
        return _invalid(loc)

    elements = _display_items(bound.get("items"), ctx, loc, f"Vec({elem_ty.render()}, [...])")
    if elements is None:
        return _invalid(loc)

    items: list[IRExpr] = []
    for element in elements:
        item = _check_value(element, ctx, expected=elem_ty)
        if _failed(item):
            return _invalid(loc)
        if not _assignable(item.ty, elem_ty):
            _error(
                ctx,
                "SPT3018",
                loc,
                f"item is {item.ty.render()}, not {elem_ty.render()}",
            )
            return _invalid(loc)
        items.append(item)

    note_escapes(items, ctx)
    return MakeVec(
        loc=loc,
        ty=Ty.Vec(elem_ty),
        elem_ty=elem_ty,
        items=tuple(items),
        all_static=_all_static(items),
    )


def _map_construction(node: ast.Call, ctx: FuncCtx, loc: Loc) -> IRExpr:
    spec = RECOGNIZED["map.new"]
    bound = _bind(node, ctx, loc, spec.surface, ("key_type", "value_type"), optional=("entries",))
    if bound is None:
        return _invalid(loc)
    key_ty = _resolve_type_arg(bound["key_type"], ctx, loc)
    if key_ty is None:
        return _invalid(loc)
    value_ty = _resolve_type_arg(bound["value_type"], ctx, loc)
    if value_ty is None:
        return _invalid(loc)

    example = f"Map({key_ty.render()}, {value_ty.render()}, [(k, v)])"
    elements = _display_items(bound.get("entries"), ctx, loc, example)
    if elements is None:
        return _invalid(loc)

    pairs: list[tuple[IRExpr, IRExpr]] = []
    for element in elements:
        if not isinstance(element, ast.Tuple) or len(element.elts) != 2:
            _error(
                ctx,
                _FALLBACK_CODE,
                loc,
                "each entry must be a (key, value) tuple written in place",
                help=f"pass (key, value) tuples, e.g. {example}",
            )
            return _invalid(loc)
        key_node, value_node = element.elts
        key = _check_value(key_node, ctx, expected=key_ty)
        if _failed(key):
            return _invalid(loc)
        if not _assignable(key.ty, key_ty):
            _error(ctx, "SPT3018", loc, f"key is {key.ty.render()}, not {key_ty.render()}")
            return _invalid(loc)
        value = _check_value(value_node, ctx, expected=value_ty)
        if _failed(value):
            return _invalid(loc)
        if not _assignable(value.ty, value_ty):
            _error(ctx, "SPT3018", loc, f"value is {value.ty.render()}, not {value_ty.render()}")
            return _invalid(loc)
        pairs.append((key, value))

    note_escapes([side for pair in pairs for side in pair], ctx)
    ordered, all_static = _order_map_pairs(tuple(pairs))
    if all_static:
        # The static path is the only one that needs the uniqueness check: it
        # is what lets D emit `map_new_from_linear_memory` (`m.9`), which
        # requires strictly-ascending UNIQUE keys. The fallback path lowers to
        # `map_new` + `map_put`, whose last-write-wins is exactly what tier 1's
        # `Map.set` does, so a runtime-keyed map needs no compile-time check
        # (and could not have one -- the keys are not known yet).
        duplicate = duplicate_static_key(tuple(key for key, _ in ordered))
        if duplicate is not None:
            _error(
                ctx,
                "SPT1039",
                loc,
                f"the key {_short_literal(duplicate)} appears more than once",
                help=(
                    "remove the repeated entry: tier 1 keeps the LAST value silently, but a "
                    "map literal on chain is laid out with unique keys and cannot represent "
                    "the repeat at all"
                ),
            )
            return _invalid(loc)
    return MakeMap(
        loc=loc,
        ty=Ty.Map(key_ty, value_ty),
        key_ty=key_ty,
        value_ty=value_ty,
        pairs=ordered,
        all_static=all_static,
    )


#: Cap on a literal quoted back inside a diagnostic (`expr.py` keeps the same
#: discipline): a contract may hold a 100 KB Bytes literal, and quoting it in
#: full would be a 100 KB error message.
_MAX_LITERAL_CHARS = 60


def _short_literal(node: IRExpr) -> str:
    if not isinstance(node, Const):
        return node.ty.render()
    text = repr(node.py_value)
    return text if len(text) <= _MAX_LITERAL_CHARS else f"{text[:_MAX_LITERAL_CHARS]}..."


def _all_static(nodes: Sequence[IRExpr]) -> bool:
    """MJ-15/`MakeVec.all_static`: whether D can lay the items out in linear
    memory. An EMPTY container is deliberately NOT static -- there is nothing
    to lay out, and `vec_new`/`map_new` is the honest lowering. A `ConstRef`
    (a module-level chain constant, P5) is conservatively not counted: it is a
    compile-time value, but treating it as one here would commit D to a data
    layout decision C has not proven, and the fallback path is always sound.
    """
    return bool(nodes) and all(isinstance(item, Const) for item in nodes)


def _tier1_key_values(keys: Sequence[IRExpr]) -> list[Any] | None:
    """The tier-1 chain value of every LITERAL key, or `None` when C cannot
    build them all -- a key that is not a literal, or a key type with no
    tier-1 literal form (a struct: E3's "not modelled in tier 1").

    Rebuilding the oracle's own value (through the same `Ty -> class` map
    `expr.py` validates literals with) is what keeps both key questions MJ-15
    asks -- the order, and now uniqueness -- answered by tier 1 rather than by
    a second model of `val_cmp` living here (A15).
    """
    values: list[Any] = []
    for key in keys:
        if not isinstance(key, Const):
            return None
        cls = oracle_class(key.ty)
        if cls is None:
            return None
        try:
            values.append(cls(key.py_value))
        except Exception:  # noqa: BLE001 -- the literal was already validated;
            # a constructor that refuses it here is a reason to decline the
            # pre-sort, never a second diagnostic (or a traceback, F.2.5).
            return None
    return values


def static_map_order(keys: Sequence[IRExpr]) -> tuple[int, ...] | None:
    """MJ-15: the indices of `keys` in the HOST's key order, or `None` when C
    cannot totally order them.

    The ordering is delegated to the oracle's own `val_cmp` (`ScValType` rank,
    then the within-type payload -- A8/A14) by rebuilding the tier-1 value of
    each literal key, exactly as `expr.py` rebuilds one to validate a literal.
    `None` comes back for a key that is not a literal at all, a key type with
    no tier-1 literal form (a struct -- E3's "not modelled in tier 1"), and any
    pair `val_cmp` itself refuses (a `Vec`/`Map` key, whose within-type
    ordering tier 1 leaves deferred). A15 forbids inventing an order the
    oracle cannot check, so the caller must then leave the map to the host.
    """
    values = _tier1_key_values(keys)
    if values is None:
        return None

    def compare(left: int, right: int) -> int:
        return val_cmp(values[left], values[right])

    try:
        return tuple(sorted(range(len(values)), key=cmp_to_key(compare)))
    except (TypeError, NotImplementedError):
        return None


def duplicate_static_key(keys: Sequence[IRExpr]) -> IRExpr | None:
    """The first literal key that repeats an earlier one, or `None`.

    "Repeats" is `val_cmp(a, b) == 0` -- the SAME oracle relation the pre-sort
    uses -- so the `Bytes` family's payload equality counts (D5:
    `Bytes32(p)` and `Bytes(p)` are one key on chain, and tier 1 agrees), and
    no separate notion of key identity is invented here. `None` also comes back
    when C cannot build the keys at all, which is the same condition
    `static_map_order` declines on: the caller only asks about a map it already
    knows is fully static.
    """
    values = _tier1_key_values(keys)
    if values is None:
        return None
    try:
        for index in range(1, len(values)):
            for earlier in range(index):
                if val_cmp(values[earlier], values[index]) == 0:
                    return keys[index]
    except (TypeError, NotImplementedError):
        return None
    return None


def _order_map_pairs(
    pairs: tuple[tuple[IRExpr, IRExpr], ...],
) -> tuple[tuple[tuple[IRExpr, IRExpr], ...], bool]:
    """`(pairs in the order D receives them, all_static)`.

    Reordering happens ONLY when every key and value is a literal and the
    oracle could order the keys: F.1.12 makes a `Map` literal's order
    observable on chain, and the evaluation order of non-literal value
    expressions (which can trap or spend budget) is observable too. So the
    non-static path hands back SOURCE order untouched and D lets the host
    sort.
    """
    keys = [key for key, _ in pairs]
    values = [value for _, value in pairs]
    if not (_all_static(keys) and _all_static(values)):
        return pairs, False
    order = static_map_order(keys)
    if order is None:
        return pairs, False
    return tuple(pairs[index] for index in order), True


# --- struct construction (kwargs only) and field reads ----------------------


def _struct_fields(ctx: FuncCtx, name: str) -> Sequence[tuple[str, Any]] | None:
    """A declared struct's `(field name, resolved annotation)` pairs in
    DECLARATION order, straight from `_serpent_type_` (A19/B9)."""
    for decl in ctx.loaded.decorated_types_in_order:
        if decl.name == name and decl.kind == "struct":
            fields = decl.metadata.get("fields")
            if isinstance(fields, list):
                return [(str(field_name), annotation) for field_name, annotation in fields]
    return None


def _bind_record_fields(
    node: ast.Call,
    ctx: FuncCtx,
    loc: Loc,
    name: str,
    fields: Sequence[tuple[str, Any]],
) -> dict[str, IRExpr] | None:
    """One `@contracttype`/`@contractevent` construction's checked field values.

    THE kwargs-only construction rule, in one place because both records share
    it (review B3): keywords only, every declared field required, no unknown
    field, no `**` unpacking, and each value checked against its declared
    annotation. Returns `{field name: value}` in DECLARATION order, or `None`
    after reporting (sink convention).

    Keywords only because a record is a `Map<Symbol, V>` on chain (S9) whose
    field order is the SORTED one, not the declaration one -- positional
    arguments would make the source order look meaningful when it is not. The
    values are nevertheless checked in DECLARATION order, because the order the
    author wrote is the order any diagnostic reads best in; the caller sorts
    afterwards if its lowering needs P7 order.
    """
    names = [field_name for field_name, _annotation in fields]

    if node.args:
        _error(
            ctx,
            "SPT3020",
            loc,
            f"`{name}(...)` takes keyword arguments only, got {len(node.args)} positional",
            help=f"name every field, e.g. {name}({names[0]}=...)" if names else None,
        )
        return None

    supplied: dict[str, ast.expr] = {}
    for keyword in node.keywords:
        if keyword.arg is None:
            _error(ctx, "SPT1035", loc, f"`**` unpacking is not a way to build `{name}`")
            return None
        if keyword.arg not in names:
            _error(
                ctx,
                "SPT3020",
                loc,
                f"`{keyword.arg}` is not a field of `{name}`",
                help=f"the declared fields are: {', '.join(names)}",
            )
            return None
        if keyword.arg in supplied:
            _error(ctx, "SPT3020", loc, f"`{name}` got multiple values for `{keyword.arg}`")
            return None
        supplied[keyword.arg] = keyword.value

    missing = [field_name for field_name in names if field_name not in supplied]
    if missing:
        _error(
            ctx,
            "SPT3020",
            loc,
            f"`{name}` is missing field(s): {', '.join(missing)}",
        )
        return None

    built: dict[str, IRExpr] = {}
    for field_name, annotation in fields:
        field_ty = resolve_annotation(annotation, ctx.loaded, loc, ctx.sink)
        if field_ty is None:
            return None
        value = _check_value(supplied[field_name], ctx, expected=field_ty)
        if _failed(value):
            return None
        if not _assignable(value.ty, field_ty):
            _error(
                ctx,
                "SPT3018",
                loc,
                f"field `{field_name}` is {value.ty.render()}, not {field_ty.render()}",
            )
            return None
        built[field_name] = value
    return built


def _struct_construction(
    node: ast.Call, ctx: FuncCtx, loc: Loc, name: str, metadata: Mapping[str, Any]
) -> IRExpr:
    """`MyStruct(field=value, ...)`: KEYWORDS ONLY, every field required.

    The construction rule itself is `_bind_record_fields`' (shared with an
    event's). What is this function's own is the SORT: `map_new_from_linear_
    memory` needs the key descriptors ascending as byte strings at compile time,
    and the wrong layout validates and then panics on-chain (F.1.13), so C owns
    the order and D must not re-sort.
    """
    declared = metadata.get("fields")
    assert isinstance(declared, list), f"@contracttype {name} carries no field list"
    fields: list[tuple[str, Any]] = [
        (str(field_name), annotation) for field_name, annotation in declared
    ]

    values = _bind_record_fields(node, ctx, loc, name, fields)
    if values is None:
        return _invalid(loc)

    built = list(values.items())
    note_escapes([value for _, value in built], ctx)
    built.sort(key=lambda item: item[0].encode())
    return MakeStruct(loc=loc, ty=Ty.Struct(name), struct_name=name, fields=tuple(built))


def _recognize_field_get(node: ast.Attribute, ctx: FuncCtx, loc: Loc, base: IRExpr) -> IRExpr:
    """`value.field` on a `@contracttype` value: a `Symbol` key, then `map_get`
    (SS C.4's struct-field-read row)."""
    assert base.ty.name is not None
    struct_name = base.ty.name
    fields = _struct_fields(ctx, struct_name)
    assert fields is not None, (
        f"Ty.Struct({struct_name!r}) resolved outside this module's declared-type "
        "inventory; resolve_annotation already refuses that (F.1.14)"
    )
    for field_name, annotation in fields:
        if field_name == node.attr:
            field_ty = resolve_annotation(annotation, ctx.loaded, loc, ctx.sink)
            if field_ty is None:
                return _invalid(loc)
            return FieldGet(
                loc=loc, ty=field_ty, obj=base, field=field_name, struct_name=struct_name
            )
    _error(
        ctx,
        "SPT2001",
        loc,
        f"`{struct_name}` has no field `{node.attr}`",
        help=f"the declared fields are: {', '.join(name for name, _ in fields)}",
    )
    return _invalid(loc)


# --- container methods: readers (value position) ----------------------------


def _bound_args(
    node: ast.Call, ctx: FuncCtx, loc: Loc, row: str, recv: IRExpr
) -> tuple[IRExpr, ...] | None:
    """Bind and type-check one method row's arguments, or `None` after
    reporting. Shared by the reader and the mutator paths so a method's call
    shape is checked in exactly one place."""
    spec = RECOGNIZED[row]
    shape = _METHOD_SHAPES[row]
    bound = _bind(node, ctx, loc, spec.surface, shape.params)
    if bound is None:
        return None
    args: list[IRExpr] = []
    for param, kind in zip(shape.params, shape.args, strict=True):
        declared = _expected_arg_ty(kind, recv.ty)
        value = _check_value(bound[param], ctx, expected=declared)
        if _failed(value):
            return None
        if not _assignable(value.ty, declared):
            _error(
                ctx,
                "SPT3018",
                loc,
                f"`{param}` is {value.ty.render()}, not {declared.render()}",
            )
            return None
        args.append(value)
    return tuple(args)


def _recognize_container_method(
    node: ast.Call, ctx: FuncCtx, method: str, base: ast.expr
) -> IRExpr | None:
    """A container method in a VALUE position.

    Returns `None` when the receiver turns out not to be a container at all
    (a struct method call, `amt.get(0)`), leaving the sink untouched -- the
    method NAME alone is not enough to claim the surface, and the receiver's
    type is the only way to know.
    """
    loc = Loc.from_node(ctx.path, node)
    recv = _check_value(base, ctx)
    if _failed(recv):
        # Committed: the receiver's own diagnostic is already in the sink, so
        # returning `None` here would invite a second, cascaded one.
        return _invalid(loc)
    if recv.ty.tag not in _CONTAINER_TAGS:
        return None

    row = _resolve_container_row(recv.ty, method)
    if row is None:
        _error(
            ctx,
            "SPT2001",
            loc,
            f"`{recv.ty.render()}` has no method `{method}`",
            help=f"it supports: {', '.join(_methods_of(recv.ty))}",
        )
        return _invalid(loc)

    spec = RECOGNIZED[row]
    if spec.kind is SurfaceKind.MUTATOR:
        return _reject_mutator_in_value_position(ctx, loc, row)

    args = _bound_args(node, ctx, loc, row, recv)
    if args is None:
        return _invalid(loc)
    (fn_name,) = spec.host_fns
    return HostCall(
        loc=loc,
        ty=_result_ty(_METHOD_SHAPES[row].result, recv.ty),
        fn_name=fn_name,
        args=(recv, *args),
    )


# --- the E11 alias-analysis pass (BL-3) -------------------------------------

#: Host functions whose result is a BRAND-NEW container. Tier 1 agrees: both
#: `Map.keys`/`Map.values` build a fresh `Vec` through the validating
#: constructor and `Vec.slice` returns a new `Vec`, so a local bound from one
#: of these is genuinely the only reference and is safe to rebind.
_FRESH_CONTAINER_FNS: frozenset[str] = frozenset({"vec_slice", "map_keys", "map_values"})

#: Host functions that hand back something the RECEIVER still holds. At tier 1
#: `Vec.get`/`Map.get`/`key_by_pos`/`val_by_pos` return the very object stored
#: inside the container, so mutating the result in place edits the container at
#: tier 1 and cannot on chain (the rebind touches only the local) -- E11's
#: divergence, one level down.
_ELEMENT_FNS: frozenset[str] = frozenset({"vec_get", "map_get", "map_key_by_pos", "map_val_by_pos"})

_FUNCTIONAL_OP_NOTE = (
    "the host's container operations are functional -- vec_push_back(v, x) returns a NEW "
    "VecObject -- while types.Vec.push_back mutates in place, so C lowers a mutation to a "
    "rebind of the receiver's own binding (E11). Wherever C does not own that binding the "
    "two tiers silently disagree: after `a = b`, `a.push_back(x)` also changes `b` at "
    "tier 1 and cannot on chain"
)


class BindingSource(Enum):
    """Where a container-typed expression's binding came from (E11).

    `CONSTRUCTION` and `FRESH_HOST_RESULT` are the only two that can be
    `Ownership.OWNED`; every other source is a reference C cannot prove
    exclusive, so it is `ALIASED` and mutation through it is a reject.
    """

    CONSTRUCTION = auto()
    FRESH_HOST_RESULT = auto()
    LOCAL_ALIAS = auto()
    PARAM = auto()
    FIELD = auto()
    ELEMENT = auto()
    OTHER = auto()


_OWNED_SOURCES: frozenset[BindingSource] = frozenset(
    {BindingSource.CONSTRUCTION, BindingSource.FRESH_HOST_RESULT}
)


def classify_binding(value: IRExpr) -> BindingSource:
    """Classify one container-typed expression by where its handle came from.

    A `HostCall` this module does not classify explicitly falls to `OTHER`,
    which is `ALIASED`: a new host function reaching the IR must be ADDED to
    `_FRESH_CONTAINER_FNS` deliberately, so the default answer is the
    conservative one (a reject) rather than an unsound rebind.
    """
    if isinstance(value, (MakeVec, MakeMap)):
        return BindingSource.CONSTRUCTION
    if isinstance(value, HostCall):
        if value.fn_name in _FRESH_CONTAINER_FNS:
            return BindingSource.FRESH_HOST_RESULT
        if value.fn_name in _ELEMENT_FNS:
            return BindingSource.ELEMENT
        return BindingSource.OTHER
    if isinstance(value, LocalRef):
        return BindingSource.LOCAL_ALIAS
    if isinstance(value, ParamRef):
        return BindingSource.PARAM
    if isinstance(value, FieldGet):
        return BindingSource.FIELD
    return BindingSource.OTHER


def note_escapes(values: Iterable[IRExpr], ctx: FuncCtx, reason: str | None = None) -> None:
    """Mark every container local whose handle ESCAPES into `values` as
    `ALIASED` (E11, review fix round 1's Critical 1).

    A local stops being exclusively C's the moment its handle is stored
    somewhere else -- an item of a `Vec`, a key or value of a `Map`, a field of
    a struct, or an argument of a MUTATION (`nest.push_back(own)`,
    `mapofvec.set(k, own)`, `Holder(items=own)`). After any of those, tier 1
    sees a later `own.push_back(x)` through the container that now holds the
    same object, and on chain it cannot: the rebind touches only `own`. That is
    E11's divergence with the containers swapped, and it is the exact MIRROR of
    the element-read reject in `_mutation_slot` (`m.get(k).push_back(x)`) --
    one direction is "the container holds my object", the other is "I hold the
    container's object", and both have to be refused.

    Four recognized container-argument positions deliberately do NOT count as
    escapes: `<bucket>.set(k, v)`, `events().publish(topics, data)`,
    `<Event instance>.publish(env)` (M1-E's desugar, which builds the same
    topics and data from the construction's own arguments -- `_event_publish`
    says so at the one place it could have called this hook), and
    `addr.require_auth_for_args(args)`. All four serialize their argument out
    to the host rather than storing a handle, and the tier-1 model
    deep-copies at the boundaries it has bodies for (ruling E5: `set` stores a
    deep copy, and BOTH publish spellings snapshot their topics and data
    through one `Events._record`), so tier 1 still has no shared-object model to
    diverge from -- not because those surfaces cannot run, but because what they
    keep is a copy.
    `tests/unit/test_env_model.py`'s isolation property is what holds that
    justification up. `require_auth_for_args` is a CARRIED obligation: its body
    is still `NotImplementedError`, and whoever lands it must snapshot the args
    it records and pin that here-shaped property alongside it. If any of the
    four ever stores a reference instead, that position becomes an escape and
    belongs in this hook.

    The exemption applies to KEYWORD arguments of those calls as well as
    positional ones (`collect_never_owned`'s escape-facts note): the spelling of
    the call cannot change what the host does with the value.

    One further position belongs to the WIRING task rather than to this hook:
    `<bucket>.get(key, T, default=d)` lowers to an `IfExp` whose `orelse` IS
    `d` (SS C.4's GET_DEFAULT row), so a container local passed as `default`
    can be the value of the whole expression -- exactly the conditional-arm
    escape `_escaping_locals` already understands. It is unreachable today (a
    `get`'s type argument must be a bare chain-type or struct name, so no
    container type can be requested), but Task 8's wiring must route that
    lowering's result through the same escape handling if it becomes
    reachable.

    **The walk itself lives on `AliasTable.mark_escapes` (`ctx.py`)**, because
    Task 8's internal-call site in `expr.py` needs the same rule and cannot
    import this module (`recognize` imports `check_expr`). This function stays
    the documented entry point -- and the place the "which positions are NOT
    escapes" ruling above is recorded -- while there is exactly one copy of
    the "which locals can this expression BE" walk.

    `reason` is the optional, author-facing cause recorded on every slot this
    call aliases (`AliasTable.mark_aliased`), for the shapes where "aliased to
    another binding" would not tell the author what they actually wrote.
    """
    ctx.alias_sets.mark_escapes(values, reason)


def note_local_binding(
    slot: int, value: IRExpr, ctx: FuncCtx, reason: str | None = None
) -> Ownership | None:
    """Record what binding `slot` to `value` means for E11, and return the
    `Ownership` recorded (`None` when `value` is not a mutable container type,
    which is most bindings -- nothing to track).

    `a = b` where `b` names another container local marks BOTH slots
    `ALIASED`, not just `a`: after that assignment the two names share one
    object at tier 1, so rebinding EITHER of them would diverge. Rebinding a
    slot from a fresh value restores `OWNED` (the old alias relationship is
    gone), while the other name stays `ALIASED` -- C cannot prove no third
    reference exists, and the conservative answer is a reject, not a silent
    rebind.

    **The right-hand side goes through `note_escapes` first** (review fix
    round 2). `a = b` is only the simplest shape that shares a handle:
    `w = own if flag else other` shares BOTH arms' handles, and classifying
    the VALUE alone (an `IfExp` is `OTHER`, so the target is `ALIASED`) would
    leave `own` and `other` reading `OWNED` and accept a later
    `own.push_back(x)` -- E11's divergence again, one shape further out.
    `_escaping_locals` already answers "which locals can this expression BE"
    for exactly the value-preserving shapes (a bare `LocalRef`, either arm of
    a conditional, and nothing for a `Make*`/`HostCall`/`FieldGet`, which
    build new values), so routing every right-hand side through it subsumes
    the `a = b` case rather than special-casing it.
    """
    if value.ty.tag not in _MUTABLE_TAGS:
        return None
    # Any local the right-hand side can BE loses ownership, whatever the
    # target ends up classified as.
    note_escapes([value], ctx, reason)
    source = classify_binding(value)
    if source is BindingSource.LOCAL_ALIAS:
        # `note_escapes` already marked the SOURCE slot; this marks the target.
        ctx.alias_sets.mark_aliased(slot, reason)
        return Ownership.ALIASED
    if source in _OWNED_SOURCES:
        ctx.alias_sets.mark_owned(slot)
        return Ownership.OWNED
    ctx.alias_sets.mark_aliased(slot, reason)
    return Ownership.ALIASED


# --- the syntactic alias/escape pre-pass (E11, Task 10) ---------------------

#: Method names that MUTATE their receiver, and therefore store every argument
#: past it. Derived from `RECOGNIZED` rather than restated (MJ-5).
_MUTATOR_METHOD_NAMES: frozenset[str] = frozenset(
    row.rsplit(".", 1)[1] for row, spec in RECOGNIZED.items() if spec.kind is SurfaceKind.MUTATOR
)

#: Method names of every RECOGNIZED row that is not a mutator -- the container
#: readers plus the env surfaces. A recognized non-mutator does not store a
#: positional argument's handle anywhere, so passing a container to one is not
#: an escape. A name that is BOTH (e.g. `set`, which is `map.set`'s mutator and
#: `storage.set`'s serializing call) stays out of this set: the receiver-shape
#: check in `_is_serializing_call` is what exempts the storage form, and
#: treating the ambiguous name conservatively everywhere else is the sound
#: direction.
_NON_STORING_METHOD_NAMES: frozenset[str] = (
    frozenset(
        row.rsplit(".", 1)[1]
        for row, spec in RECOGNIZED.items()
        if spec.kind is not SurfaceKind.MUTATOR
    )
    - _MUTATOR_METHOD_NAMES
)


def _value_names(node: ast.expr) -> set[str]:
    """The names `node` can BE -- the syntactic twin of `ctx._escaping_locals`.

    A bare `Name` is that name; a conditional expression can be either arm.
    Everything else (a call, a display, a `BinOp`, an attribute read) builds a
    NEW value from its operands, so a name mentioned inside one of those is not
    a name the expression can be.
    """
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.IfExp):
        return _value_names(node.body) | _value_names(node.orelse)
    return set()


def _bound_names(target: ast.expr) -> set[str]:
    """Every name an assignment TARGET binds (tuple/list targets included --
    they are rejected on their own, but the pre-pass must not depend on the
    order the two checks run in)."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for element in target.elts:
            names |= _bound_names(element)
        return names
    return set()


def _is_serializing_call(func: ast.Attribute) -> bool:
    """Whether `func` names one of the three recognized argument positions that
    SERIALIZE their argument out to the host instead of storing a handle --
    `<bucket>.set(k, v)`, `events().publish(topics, data)` and
    `require_auth_for_args(args)`. `note_escapes`' docstring carries the full
    ruling, including what would change it."""
    return (
        (func.attr == "set" and _match_storage_bucket(func.value) is not None)
        or (func.attr == "publish" and _match_no_arg_chain(func.value, "events"))
        or func.attr == "require_auth_for_args"
    )


def _positional_args_escape(node: ast.Call, ctx: FuncCtx) -> bool:
    """Whether `node`'s POSITIONAL arguments can store a container handle.

    Keyword arguments are handled separately (they always can -- the only
    keyword positions the whole authoring surface has are a `@contracttype`'s
    fields and `<bucket>.get`'s `default`, and both are storing or
    value-preserving positions).
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        if _is_serializing_call(func):
            return False
        # A recognized reader does not store its arguments; a mutator does, and
        # an unrecognized method name is a struct/internal call C classifies
        # without inspecting the callee.
        return func.attr not in _NON_STORING_METHOD_NAMES
    if isinstance(func, ast.Name):
        if func.id in RECOGNIZED_BUILTINS:
            # `len(v)`/`bool(v)` read a value; they store nothing. Without this
            # exemption the pre-pass would refuse `v.push_back(x)` in any
            # method that also asks `len(v)`.
            return False
        obj = ctx.loaded.namespace.get(func.id)
        # A chain-type constructor, `Vec`/`Map`, or a decorated class: the
        # handles that get stored are the ITEMS of a display or the values of
        # keyword fields, both covered by their own rules. Anything else under
        # a bare name is an E8 internal call, whose callee may embed what it
        # was passed.
        return not isinstance(obj, type)
    return True


def collect_never_owned(body: Sequence[ast.stmt], ctx: FuncCtx) -> frozenset[str]:
    """Every local NAME in `body` that can never be exclusively C-owned (E11).

    This is the SYNTACTIC PRE-PASS `recognize_mutation`'s hand-off contract
    requires. Ownership has to be decided flow-INSENSITIVELY, because a
    per-statement walk is order-dependent and a loop body makes that unsound:

        while cond:
            own.push_back(U32(1))   # checked FIRST, while `own` still reads OWNED
            w = own                 # aliases it -- for every later iteration

    Collecting every alias and escape fact in the whole body BEFORE any
    statement is checked makes the answer independent of where the checker
    happens to be, so the second iteration cannot be classified differently
    from the first. The result is a set of NAMES rather than slots because
    slots do not exist yet -- `SlotTable` numbers them as the body is checked
    -- and `stmt.py` applies it at the one point that could otherwise conclude
    `OWNED`: immediately after `note_local_binding` classifies a binding.

    Being name-based rather than slot-based is also the conservative direction:
    a function whose `own` is legitimately owned in one region and aliased in
    another gets the aliased answer everywhere, which is a reject rather than
    an unsound rebind.

    The facts collected, all over `ast.walk` so nested `if`/`while`/`for`
    bodies are included:

    * **Alias facts.** An assignment whose right-hand side is value-preserving
      (a bare name, or either arm of a conditional expression) shares a handle:
      both the source names and the target names are marked. A tuple/list
      target is marked whatever its value, and a `for` target is marked because
      it is bound from an element read.
    * **Escape facts.** A name in an element of any display (a `Vec`/`Map`
      items argument, an event-topic tuple), in any keyword-argument value (a
      `@contracttype` field, `<bucket>.get`'s `default`), or in a positional
      argument of a call that can store it (`_positional_args_escape`).

      The keyword rule has ONE exemption, and it is the same one
      `_positional_args_escape` applies: the three SERIALIZING calls
      (`<bucket>.set`, `events().publish`, `require_auth_for_args`) do not
      store a handle in any argument position, so `set(key=k, value=own)` is
      not an escape any more than `set(k, own)` is. Without that, the same
      write escaped or not depending on whether the author spelled it with
      keywords -- an asymmetry with no semantic content. Every other call's
      keyword arguments still escape, because a `@contracttype` field and
      `<bucket>.get`'s `default` both hold on to what they are given.

    One escape position deliberately has no pre-pass rule: `<bucket>.get(key,
    T, default=d)` lowers to an `IfExp` whose `orelse` IS `d`, so a container
    handed to `default` is a conditional arm of the whole expression --
    `note_local_binding` routes every right-hand side through `note_escapes`,
    whose walk already understands both arms. The keyword rule above covers the
    syntactic side of the same position, so the two agree.
    """
    names: set[str] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, (ast.Tuple, ast.List)):
                        names |= _bound_names(target)
                if node.value is not None:
                    shared = _value_names(node.value)
                    if shared:
                        names |= shared
                        for target in targets:
                            names |= _bound_names(target)
            elif isinstance(node, ast.For):
                # The target is bound from an element read, and the ITERABLE's
                # own handle is copied into the desugaring's hidden `$iter`
                # local -- so both lose ownership (`stmt._desugar_for_vec`
                # carries the tier-divergence this refuses).
                names |= _bound_names(node.target)
                names |= _value_names(node.iter)
            elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
                for element in node.elts:
                    names |= _value_names(element)
            elif isinstance(node, ast.Call):
                func = node.func
                serializing = isinstance(func, ast.Attribute) and _is_serializing_call(func)
                if not serializing:
                    for keyword in node.keywords:
                        names |= _value_names(keyword.value)
                if _positional_args_escape(node, ctx):
                    for arg in node.args:
                        names |= _value_names(arg)
    return frozenset(names)


def _mutation_help(recv: IRExpr, *, temporary: bool) -> str:
    if temporary:
        return (
            "bind the container to a local first and mutate that: `v = Vec(U32, [...])` on "
            "one line, `v.push_back(x)` on the next -- C then rebinds v for you"
        )
    if recv.ty.tag is TyTag.VEC:
        return (
            "mutate only a local this method owns: take a copy you own with "
            "`v = <container>.slice(U32(0), len(<container>))`, then `v.push_back(...)` -- "
            "C rebinds v (v = vec_push_back(v, x)) for you"
        )
    return (
        "mutate only a local this method owns: build it with `m = Map(K, V)` and `set(...)` "
        "into it -- C rebinds it for you. A Map reached through a parameter, a field, or "
        "another local cannot be mutated in place in M1"
    )


def _mutation_slot(recv: IRExpr, ctx: FuncCtx, loc: Loc, surface: str) -> int | None:
    """The local slot a mutation may rebind, or `None` after reporting
    `SPT1034` (E11/BL-3: mutation is legal ONLY on an unaliased local whose
    binding C owns)."""
    if isinstance(recv, LocalRef):
        ownership = ctx.alias_sets.ownership_of(recv.slot)
        if ownership is Ownership.OWNED:
            return recv.slot
        # A recorded reason beats the generic wording: some shapes alias a
        # local without the author writing `a = b` anywhere (iterating it, for
        # one), and "aliased to another binding" sends them looking for an
        # assignment that is not there.
        recorded = ctx.alias_sets.alias_reason(recv.slot)
        detail = (
            f"`{recv.name}` {recorded}"
            if ownership is Ownership.ALIASED and recorded is not None
            else f"`{recv.name}` is aliased to another binding"
            if ownership is Ownership.ALIASED
            # No entry at all: an unclassified slot is not KNOWN to be owned.
            # Failing loudly here is deliberate -- a later task that forgets to
            # run `note_local_binding` gets a reject, never a silent unsound
            # rebind of the highest-severity divergence in the frontend.
            else f"`{recv.name}` has no binding C has classified as its own"
        )
        _error(
            ctx,
            "SPT1034",
            loc,
            f"`{surface}` cannot mutate it: {detail}",
            help=_mutation_help(recv, temporary=False),
            notes=(_FUNCTIONAL_OP_NOTE,),
        )
        return None

    source = classify_binding(recv)
    if source is BindingSource.PARAM:
        detail = "the receiver is a parameter, whose binding belongs to the caller"
    elif source is BindingSource.FIELD:
        detail = "the receiver is a struct field read, which is a copy C does not own"
    elif source is BindingSource.ELEMENT:
        # The MIRROR of `note_escapes`: there, a local's handle went INTO a
        # container; here, the receiver came OUT of one. Both leave two names
        # for one tier-1 object and one handle on chain, so both are refused.
        detail = (
            "the receiver is an element read, which hands back the value the container "
            "itself still holds"
        )
    else:
        detail = "the receiver is a temporary with no binding to rebind"
    _error(
        ctx,
        "SPT1034",
        loc,
        f"`{surface}` cannot mutate it: {detail}",
        help=_mutation_help(
            recv, temporary=source not in (BindingSource.PARAM, BindingSource.FIELD)
        ),
        notes=(_FUNCTIONAL_OP_NOTE,),
    )
    return None


def _reject_mutator_in_value_position(ctx: FuncCtx, loc: Loc, row: str) -> IRExpr:
    """A mutator reached as a VALUE.

    There is no binding to rewrite in an expression position, and tier 1
    agrees the shape is wrong: its mutators return `None`, so
    `x = v.push_back(y)` is not valid Python against the authoring surface
    either. `pop_back`/`pop_front` get their own `help`, because tier 1 DOES
    return the popped element there while the host's `vec_pop_back` returns
    the new `Vec` -- reading the element first is the rewrite that works.
    """
    surface = RECOGNIZED[row].surface
    if row in ("vec.pop_back", "vec.pop_front"):
        help_text = (
            "read the element first -- `x = v.get(U32(0))` for pop_front, "
            "`x = v.get(len(v) - U32(1))` for pop_back -- then pop on a line of its own"
        )
    else:
        help_text = (
            f"put `{surface}` on a line of its own; C lowers it to a rebind of the "
            "receiver (v = vec_push_back(v, x))"
        )
    _error(
        ctx,
        "SPT1034",
        loc,
        f"`{surface}` mutates its receiver, so it is supported only as a statement of its own",
        help=help_text,
        notes=(_FUNCTIONAL_OP_NOTE,),
    )
    return _invalid(loc)


def _is_env_surface(node: ast.expr) -> bool:
    """Whether `node` is one of Task 7a's own receivers -- which matters
    because `get`/`has`/`set`/`del_` are method names BOTH surfaces use."""
    return (
        _match_storage_bucket(node) is not None
        or _match_no_arg_chain(node, "ledger")
        or _match_no_arg_chain(node, "events")
        or _is_env_name(node)
    )


def recognize_mutation(node: ast.Call, ctx: FuncCtx) -> IRStmt | None:
    """Recognize a container mutation in STATEMENT position, lowering it to
    E11's rebind: `v.push_back(x)` -> `SetLocal(slot, vec_push_back(v, x))`.

    Returns `None` when `node` is not a container mutation at all (a reader, a
    construction, an env call, a method the receiver does not have) so the
    caller can fall through to ordinary expression-statement checking, and a
    `Nop` after reporting -- the statement lowers to nothing, and a compile
    with diagnostics never reaches D anyway (SS C.2).

    `SetLocal` rather than a new IR node is the whole point: the rebind is an
    ordinary assignment to the slot C already owns, so there is no way for a
    consumer to receive the mutation and miss the rebind.

    **Hand-off contract for the task that wires the statement checker to this
    module.** The guard passes only for a slot whose `Ownership` is `OWNED`,
    and the only things that ever set an `Ownership` are `note_local_binding`
    (a local's binding) and `note_escapes` (a local's handle stored elsewhere).
    Calling them LAZILY, as the checker reaches each statement, is NOT
    sufficient -- classification would then be order-dependent, and a loop body
    makes that unsound:

        while cond:
            own.push_back(U32(1))   # checked FIRST, while `own` still reads OWNED
            w = own                 # aliases it -- for every later iteration

    On the second iteration the mutation runs with `own` aliased, so a
    statement-order walk accepts exactly the divergence E11 exists to reject.
    The wiring task must therefore run a SYNTACTIC PRE-PASS over the whole
    function body before checking any statement, collecting every alias and
    escape fact -- `a = b` aliases (including tuple targets and `for`
    targets), a conditional expression on an assignment's right-hand side
    (`w = own if flag else other` shares BOTH arms' handles, which is why
    `note_local_binding` routes the value through `note_escapes`), and the
    embedding escapes `note_escapes` documents (a local passed as a `Vec`/`Map`
    item, a struct field value, or a mutation argument) -- so the
    classification is flow-insensitive-conservative rather than dependent on
    where the checker happens to be. Per-statement calls then only ever narrow
    from a state that was already conservative.

    One escape position lives outside this module: an `InternalCall` ARGUMENT
    (E8's module-level helpers and private methods). A callee can embed a
    passed local in a container of its own, so a container argument to an
    internal call is treated as an escape the same way a mutation argument is
    -- conservatively, without inspecting the callee. Task 8 wired it at
    `expr.py`'s internal-call site, through `AliasTable.mark_escapes` (the
    same walk this function delegates to).

    An unclassified slot is a reject, so a missing call shows up as a loud,
    located diagnostic rather than an unsound rebind.
    """
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _CONTAINER_METHOD_NAMES:
        return None
    if _is_env_surface(func.value):
        return None

    loc = Loc.from_node(ctx.path, node)
    recv = _check_value(func.value, ctx)
    if _failed(recv):
        return Nop(loc=loc)
    row = _resolve_container_row(recv.ty, func.attr)
    if row is None or RECOGNIZED[row].kind is not SurfaceKind.MUTATOR:
        return None

    spec = RECOGNIZED[row]
    args = _bound_args(node, ctx, loc, row, recv)
    if args is None:
        return Nop(loc=loc)
    # Every argument PAST the receiver is an embedding position:
    # `nest.push_back(own)` and `mapofvec.set(k, own)` store `own`'s handle
    # inside the receiver. Marked before the guard runs, so the conservative
    # answer wins even when a single statement both embeds and mutates.
    note_escapes(args, ctx)
    slot = _mutation_slot(recv, ctx, loc, spec.surface)
    if slot is None:
        return Nop(loc=loc)
    (fn_name,) = spec.host_fns
    return SetLocal(
        loc=loc,
        slot=slot,
        value=HostCall(loc=loc, ty=recv.ty, fn_name=fn_name, args=(recv, *args)),
    )


# --- completeness support (MJ-3; the assertion itself lives in the test) -----


def target_functions() -> dict[str, object]:
    """Every `ENV_HOST_FN_TARGETS` name resolved against `_host.functions_
    by_name`; a `KeyError` here means `RECOGNIZED` names a host function the
    pinned `env.json` does not have -- the completeness test's forward
    direction, exposed here so the test needs no private access."""
    return {name: functions_by_name[name] for name in ENV_HOST_FN_TARGETS}
