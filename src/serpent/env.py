"""The `Env` authoring surface: the shape of the host -- and a tier-1 MODEL of it.

Every contract method takes an `Env` and reaches the host through it --
`env.storage().instance().get(...)`, `env.ledger().timestamp()`,
`env.events().publish(...)`. That chain is what sub-plan C compiles into host
function calls, and what sub-plan F re-proves against the real Soroban host.

**This module is also a model, and a model is not an oracle.** Sub-plan E gave
every method here an in-memory body so that an authored contract can be
constructed and called with no engine at all -- the fast authoring loop, tier 1.
A hand-written model of host semantics has *silent false green* as its failure
mode: the test passes, the docstrings read well, and the contract behaves
differently on chain. Nothing in this file is evidence that a contract is
correct on chain. Sub-plan F's tier 2b -- the real host -- is the gate.

What is deliberately NOT modelled, named here rather than approximated:

* **no footprint recording** and **no budget metering** -- a tier-1 run cannot
  tell you a contract fits;
* **no frame rollback.** A method that publishes an event and then raises
  leaves the event in `published_events`; on chain the event rolls back with
  the frame. The mini-host does not model it either;
* **no auth trees.** `auths=` is a mock-all-auths allow-set, not the host's
  nonce-consuming authorization machinery;
* **no instance-storage flush semantics** -- unobservable in M1, which has no
  cross-contract call to be re-entrant with;
* **no TTL clamp and no TTL trap, and no archival** -- see the TTL section
  below, which is deliberately half a model and says which half.

## Deploy, the frame, and S12's laundering (ruling E7)

Two things the host supplies structurally and Python does not, so the model
supplies them explicitly -- and REFUSES the states it cannot otherwise rule out:

* **`deploy(MyContract, env, *args)`** constructs the instance and runs
  `__init__` -- which IS `__constructor` on chain -- exactly once, inside a
  frame of its own. **S12's error laundering is modelled**: an exception out of
  `__init__` surfaces as `ConstructorFailed`, never as the author's own error
  class or code, because that is what the deployer sees on chain. S12 says the
  caveat "must say so, prominently" precisely because Python developers expect
  `__init__` exception semantics; `ConstructorFailed`'s docstring is where it
  says so, and the original is chained as `__cause__`. A FAILED deploy poisons
  the env -- it deploys nothing and refuses every later deploy, because the dead
  constructor's writes are still in the store and a chain deploy is atomic;
* **`with env.frame():`** is one invocation. Every host accessor here, and every
  operation on the objects they hand out, refuses outside a frame, and a frame
  refuses to open before `deploy` -- `_require_frame` carries the reasoning.
  Contract METHODS are ordinary Python (ruling E1): the frame is the only thing
  a tier-1 test has to say out loud.

What that gate buys is narrow and worth naming: it removes tier-1-only states
(an export called before the constructor ran; a stray `require_auth` with no
invocation to attribute it to). It is NOT a model of the host's frame -- there
is still no rollback, no reentry, no cross-contract call, and no auth tree.

`recorded_auths` is the whole auth model: `require_auth` records and succeeds
(mock-all-auths, S4), an allow-set refuses a non-member with
`AuthorizationFailed`, and `require_auth_for_args` records a deep copy of its
args. Neither exception is a `ContractError`: both stand for host actions that
carry no contract error code.

## TTL: a PARTIAL model, and the half it refuses (ruling E4(c))

Spec S8 states five TTL rules: "persistent extension past max **clamps**,
temporary **traps**; live-until arithmetic carries `-1`; **extensions never
reduce**; **extending a dead entry errors**." Three of them are arithmetic on
numbers this model owns, and those are modelled. Two of them need the host's
maximum live-until ledger, whose only source is `get_max_live_until_ledger` --
an M2 host function the frontend refuses by name (`SPT1033`) and which M1
therefore cannot read. A serpent-chosen maximum would be a guess, and a guessed
ceiling is worse than no ceiling: a test would go green over an `extend_to`
that TRAPS on chain for a temporary entry. So:

**Modelled.** A per-entry `live_until: int | None` compared against the ledger
sequence; `extend_ttl` on all three buckets; the threshold guard (extend only
when `live_until - sequence < threshold`); never-reduce
(`live_until = max(live_until or 0, sequence + extend_to)`); lazy expiry in
`get`/`has` once the sequence is strictly past `live_until`; and S8's
dead-entry error for both deaths a tier-1 sequence can produce -- a key that
was never written, and an entry that has expired.

**NOT modelled, named rather than approximated.** The clamp, the trap, and
hence the whole persistent/temporary asymmetry: `extend_to` is applied exactly
as given, at any magnitude, in every bucket. There is no archive and no
restore, so a re-set of a lapsed persistent entry revives it here while the
chain would refuse the write until the entry was restored.
`tests/unit/test_env_ttl.py` holds the two rules as `pytest.skip`s, next to the
tests, so the gap is enumerable (`pytest -rs`) instead of silent. **Named
carried obligation to sub-plan F:** the clamp/trap asymmetry is unproven at
every tier in this repo -- the mini-host's TTL calls are recorded no-ops
(deliberately, and not E's to change) -- and F's tier 2b is where it gets
proven, once `get_max_live_until_ledger` is reachable.

**Four model choices, stated because they are choices and not host facts.**

* a fresh `set()` entry has `live_until = None`, meaning "never extended", and
  never expires. Every comparison guards the `None`;
* `extend_ttl` on a `None` entry always passes the threshold guard: a
  never-extended entry's remaining lifetime is genuinely unknowable at tier 1
  (it is a network-configured default the model cannot read), so the first
  extension always applies rather than being silently swallowed by a large
  threshold;
* `Env.advance(n)` moves the SEQUENCE only. Ledger close time is not a
  protocol constant this model may invent, so `ledger().timestamp()` stands
  still while entries expire -- inconsistent-looking on purpose, and cheaper
  than a made-up seconds-per-ledger;
* an expired entry is NOT removed from the store. Expiry is answered lazily on
  every read, which keeps `advance` O(1) and keeps the value around for the
  re-set path; nothing observable distinguishes the two, since an expired entry
  reads absent through the whole surface.

One code is reused rather than invented: the dead-entry error is `MissingValue`
(`CODE_MISSING_VALUE`), which is honest about the shape of the failure -- the
entry named is not there -- but is a TIER-1 signal only. The compiled form of
`extend_ttl` raises no serpent code at all; the host itself errors, with its own
`ScError`. What the two tiers promise each other here is the LOUDNESS, not the
number.

Two places tier 1 answers a question differently from the host, on purpose:

* `Events.publish` **rejects** a first topic that is not a short `Symbol`
  (spec's authoring convention). The host does not enforce that. Nothing a
  compiled contract can express reaches the reject -- the frontend refuses the
  same shape at compile time -- so this is a tier-1-only reject that keeps the
  two ends of the convention in agreement;
* `<bucket>.del_` of an absent key is a silent no-op (see `del_`).

**Deep copy is law.** The model never stores or returns a reference the caller
can mutate: `set()` deep-copies in, `get()` deep-copies out, `publish()`
snapshots its topics and data, `require_auth_for_args()` snapshots its args, and
`published_events`/`recorded_auths` copy on the way out. `serpent.types`
containers mutate in place while the host's operations are functional, so a
model that stored a reference would report a post-write mutation from storage
while the chain reported the snapshot -- the single most likely silent
divergence in this file. The frontend's escape-analysis exemptions for
`<bucket>.set(k, v)` and `require_auth_for_args(args)`
(`recognize.note_escapes`) depend on exactly this, and the isolation properties
in `tests/unit/test_env_model.py` and `tests/unit/test_env_deploy.py` are what
keep them true.

Storage buckets are three distinct types rather than one parameterized bucket
because their TTL operations genuinely differ: an instance entry's TTL covers
the whole instance and is extended without a key, while persistent and
temporary entries are extended per key. Typing them separately means the
wrong call is a type error, not a runtime surprise.
"""

from __future__ import annotations

import contextlib
import copy
import inspect
from collections.abc import Callable, Hashable, Iterable, Iterator
from types import UnionType
from typing import Any, ClassVar, TypeAlias, TypeVar, Union, cast, get_args, get_origin, overload

from serpent import _frame
from serpent._host._scalars import STORAGE_TYPE
from serpent.errors import AbiCheckFailed, BadArgument, ContractError, MissingValue
from serpent.types import (
    I32,
    I64,
    I128,
    U32,
    U64,
    U128,
    Address,
    Bool,
    Bytes,
    ContractEnum,
    ContractUnion,
    Duration,
    Map,
    String,
    Symbol,
    Timepoint,
    Vec,
)
from serpent.types._base import _ChainValue

# One definition, shared (E2/MJ-7): `containers.py` needs the same structural
# view of a `@contracttype` instance for its element/value bound and cannot
# import this module (env imports the containers), so `Struct` lives in
# `types._ordering` -- the module with no `serpent.types` imports at all -- and
# is re-exported here, where it has always been part of the public surface.
from serpent.types._ordering import Struct
from serpent.types._storage_key import storage_key

__all__ = [
    "ChainValue",
    "Env",
    "Event",
    "Events",
    "InstanceStorage",
    "Ledger",
    "PersistentStorage",
    "Storage",
    "Struct",
    "TemporaryStorage",
]

_T = TypeVar("_T")

#: The contract class `deploy` is handed, so the instance it returns is typed.
_C = TypeVar("_C")


#: The ledger model's starting values. Arbitrary but deliberately not zero: a
#: zero timestamp is a plausible-looking answer, and a contract that read one
#: without the ledger ever being configured would look like it worked. ONE
#: definition across tiers (S13) -- `tests/harness/hostfns.py`'s mini-host
#: stubs import these rather than restating them. Settable per `Env`
#: (`Env(timestamp=..., sequence=...)`).
DEFAULT_LEDGER_TIMESTAMP = 1_700_000_000
DEFAULT_LEDGER_SEQUENCE = 1_000_000


#: Everything that can cross the host boundary as a value: any scalar chain
#: type (`_ChainValue` is the shared base of `Bool`/`U32`/.../`Symbol`/
#: `Bytes`/`Address`), the containers, a `@contracttype` struct, or a
#: `@contractunion`/`@contractenum` value (M1-E2: an `ScVec` and a bare `U32`
#: on chain respectively).
#:
#: This is deliberately a closed union rather than `object`: a raw `str` or
#: `int` key is a static error, which is the whole point of the chain types.
ChainValue: TypeAlias = (
    _ChainValue[Any] | Vec[Any] | Map[Any, Any] | Struct | ContractUnion | ContractEnum
)

#: One entry of the model's store. Keyed on the storage type FIRST so the three
#: buckets are visibly separate namespaces, then on `storage_key(key)` -- the
#: value-equal, hashable normalization every tier shares
#: (`serpent.types._storage_key`), which is what makes a struct key rebuilt on a
#: later call find the entry an earlier call wrote.
_StoreKey: TypeAlias = tuple[int, Hashable]
_Store: TypeAlias = dict[_StoreKey, "ChainValue"]

#: One recorded `publish` (`published_events`): the topic tuple and the data,
#: both snapshots.
PublishedEvent: TypeAlias = tuple[tuple["ChainValue", ...], "ChainValue"]

#: One recorded authorization (`recorded_auths`): the address and, for
#: `require_auth_for_args`, a snapshot of the args (`None` for bare
#: `require_auth`).
RecordedAuth: TypeAlias = tuple[Address, "Vec[Any] | None"]


# --- the ty check: TAG level, mirroring the emitter's `abi_check` -------------

#: Which TAG FAMILY each chain type belongs to, for `get`'s `ty` check.
#:
#: This MIRRORS `serpent.emitter.lower`'s `abi_check` tables
#: (`_IMMEDIATE_ABI_WORD`, `_EITHER_ABI_TAGS`, `_OBJECT_ABI_TAG`) and is
#: restated rather than imported because `serpent.env` is inside the core
#: zero-dep walk and the emitter is not. The restatement is not trusted:
#: `tests/unit/test_env_model.py::
#: test_the_tag_families_agree_with_the_emitters_abi_check_tables` pins the two
#: to the same partition of the type space, in both directions.
#:
#: Two consequences of being tag-level, both deliberate (ruling B6):
#:
#: * the whole `Bytes` family (`Bytes`, `Bytes32`, `Bytes64`, `bytes_n(n)`)
#:   is ONE family -- the emitter compares `TAG_BYTES_OBJECT` for all of them,
#:   so a `Bytes32` REQUEST accepts a stored plain `Bytes` of the right length,
#:   exactly as on chain. The family is not the whole check for them, though:
#:   the emitter pairs that tag compare with a REAL length compare
#:   (`tagcheck_bytes_n`, `lower.py:1102-1115`), so `_require_ty` checks the
#:   length too whenever the requested type carries one. Dropping it would make
#:   tier 1 ACCEPT what the chain REJECTS, which is the wrong direction to be
#:   coarse in;
#: * a `@contracttype` struct and a `Map` are ONE family -- a struct IS a
#:   `Map<Symbol, V>` on chain (S9), which is why the emitter maps
#:   `TyTag.STRUCT` to `TAG_MAP_OBJECT`.
#:
#: `Vec`/`Map` ELEMENT types are not part of any family: the emitter's check is
#: tag-only, and one runtime class serves every element type.
_FAMILY_BY_TYPE: dict[type[Any], str] = {
    Bool: "bool",
    U32: "u32",
    I32: "i32",
    U64: "u64",
    I64: "i64",
    U128: "u128",
    I128: "i128",
    Timepoint: "timepoint",
    Duration: "duration",
    Symbol: "symbol",
    String: "string",
    Bytes: "bytes",
    Address: "address",
    Vec: "vec",
    Map: "map",
    # M1-E2: a union IS an `ScVec` and an int enum IS a bare `U32` on chain
    # (§B.1, byte-verified), so neither needs a family of its own -- and both
    # are matched HERE, by the MRO walk `tag_of_chain_value`/`_families_of_ty`
    # already do, which is what keeps the `Struct` fallthrough (a dataclass
    # match, ruling E9) from ever seeing them.
    ContractUnion: "vec",
    ContractEnum: "u32",
}

#: The family a `@contracttype` struct shares with `Map`, and the family a
#: `None` (Void) value stands in -- the `Option` case the emitter COMPOSES
#: (`VOID_VAL` or the wrapped type's own check) rather than tabulating.
_MAP_FAMILY = "map"
_VOID_FAMILY = "void"

#: The one family whose check is not finished by the family alone: a requested
#: fixed-length `Bytes` type also pins the payload's length (see
#: `_FAMILY_BY_TYPE`'s first bullet and `_require_ty`).
_BYTES_FAMILY = "bytes"


def tag_of_chain_value(value: ChainValue | None) -> str:
    """The tag family `value` belongs to, for the tier-1 `get` ty check.

    Not in `__all__`: this is a model internal plus a test surface, never a name
    an authored contract resolves.
    """
    if value is None:
        return _VOID_FAMILY
    for cls in type(value).__mro__:
        family = _FAMILY_BY_TYPE.get(cls)
        if family is not None:
            return family
    if isinstance(value, Struct):
        return _MAP_FAMILY
    raise TypeError(f"not a chain value: {value!r}")


def _is_chain_value(value: object) -> bool:
    """Whether `value` is ALREADY a chain value -- the `ChainValue` alias, asked
    at runtime.

    The alias itself (`_ChainValue[Any] | Vec | Map | Struct | ContractUnion |
    ContractEnum`) is a static type and cannot be `isinstance`d, so its six
    arms are spelled out here, in the one place that needs the runtime answer:
    `get`'s default adoption, which passes a chain value straight through and
    adopts anything else through the requested type. `Struct` is a Protocol
    with a non-method member, which is why it is matched by `isinstance` and
    not `issubclass` (the same reason `_families_of_ty` gives).

    The two M1-E2 arms are here for the same reason as the other four rather
    than for a new one: a union or int-enum `default=` is ALREADY a chain
    value, and adopting it through `ty` would not merely be redundant -- it
    would fail. Neither base takes a constructor argument (both are slotted,
    and a case is built through its descriptor), so `ty(default)` raises
    `TypeError: Shape() takes no arguments`. Missing an arm here is a crash,
    not a wasted rebuild.
    """
    return isinstance(value, (_ChainValue, Vec, Map, Struct, ContractUnion, ContractEnum))


def _adopt(value: object, ty: object) -> Any:
    """`value` as the type `ty` names: a chain value passes through, anything
    else is ADOPTED through `ty` (M1-C's literal adoption).

    The ONE adoption door in the model, so the rule is stated once. `get`'s
    raw-literal `default=` is the position it was written for; `types._udt`'s
    variant payload slots reach it through the same deferred import
    `payload()` uses, because a literal in a typed payload slot is the same
    mistake-that-is-not-a-mistake as `default=0` in a typed `get` -- the
    compiler adopts it through the declared type, so a model that refused it
    would refuse what the chain runs.

    `ty`'s own error propagates unsoftened: `U32(-1)` raises `ValueError`
    here exactly where the frontend reports `SPT3004`.
    """
    if _is_chain_value(value):
        return value
    # `ty` is a chain-type class, whose `__init__` signature is unknown to the
    # checker; the adoption is exactly the compiled tier's.
    return cast("Callable[[object], Any]", ty)(value)


def _ty_members(ty: object) -> tuple[object, ...]:
    """`ty`'s non-`None` members: the union's arms, or `ty` itself."""
    origin = get_origin(ty)
    if origin is Union or origin is UnionType:
        return tuple(member for member in get_args(ty) if member is not type(None))
    return (ty,)


def _families_of_ty(ty: object) -> frozenset[str]:
    """Every tag family a requested `ty` accepts.

    `ty` may be a chain-type class, a `@contracttype` struct class, a
    `Vec[...]`/`Map[...]` generic alias (matched on its ORIGIN only -- element
    types are not checked), or `X | None`, which accepts Void as well as `X`'s
    own family.
    """
    origin = get_origin(ty)
    if origin is Union or origin is UnionType:
        families: set[str] = set()
        for member in get_args(ty):
            if member is type(None):
                families.add(_VOID_FAMILY)
            else:
                families |= _families_of_ty(member)
        return frozenset(families)
    if origin is not None:
        ty = origin
    if isinstance(ty, type):
        for cls in ty.__mro__:
            family = _FAMILY_BY_TYPE.get(cls)
            if family is not None:
                return frozenset({family})
        # A `@contracttype` struct CLASS, recognized through the one `Struct`
        # definition `tag_of_chain_value` uses for an instance -- `isinstance`
        # rather than `issubclass` because `Struct` is a Protocol with a
        # non-method member (`__dataclass_fields__`), which `issubclass` refuses
        # outright, and because a class object carrying that attribute is
        # exactly what a struct class is. Both sides therefore answer "is this a
        # struct?" the same way, including for a plain dataclass that never went
        # through `@contracttype`.
        if isinstance(ty, Struct):
            return frozenset({_MAP_FAMILY})
    raise TypeError(f"not a chain type, struct or Option of one: {ty!r}")


def _required_bytes_lengths(ty: object) -> frozenset[int]:
    """Every exact payload length a requested `Bytes` type will accept.

    Empty when the request imposes none (`Bytes` itself, or a non-`Bytes` type):
    `Bytes._LENGTH` is `None` and `Bytes32`/`Bytes64`/`bytes_n(n)` carry 32/64/n.

    A union naming SEVERAL lengths accepts any of them. A union mixing a
    fixed-length member with plain `Bytes` (`Bytes | Bytes32`) is not a shape the
    authoring surface can produce -- only `X | None` is -- and this reads it
    conservatively, as the fixed length: coarse in the direction that rejects
    what the chain accepts, which surfaces as a test failure rather than as a
    green test over a value the chain would refuse.
    """
    lengths: set[int] = set()
    for member in _ty_members(ty):
        origin = get_origin(member)
        cls = origin if origin is not None else member
        length = getattr(cls, "_LENGTH", None) if isinstance(cls, type) else None
        if isinstance(length, int):
            lengths.add(length)
    return frozenset(lengths)


def _require_ty(value: ChainValue, ty: object) -> None:
    """Raise `AbiCheckFailed` unless `value` really is the type `ty` names.

    The tier-1 twin of the emitter's `narrow_to` on a host result: both answer
    "is this value really the type the program says it is?", both fail with
    `CODE_ABI_CHECK_FAILED` (so the two tiers name one failure, ruling E8), and
    both answer at TAG level -- with the one exception the emitter itself makes,
    a requested fixed-length `Bytes` type, whose `tagcheck_bytes_n` part
    compares the payload length as well as the tag. Tier 1 makes the same pair
    of comparisons: being coarser here would ACCEPT at tier 1 what the chain
    REJECTS, which is the direction that produces a green test and a failed
    invocation.
    """
    family = tag_of_chain_value(value)
    accepted = _families_of_ty(ty)
    name = getattr(ty, "__name__", repr(ty))
    if family not in accepted:
        raise AbiCheckFailed(f"stored value is a {family}, not a {name}")
    if family == _BYTES_FAMILY:
        lengths = _required_bytes_lengths(ty)
        if lengths and len(cast("Bytes", value)) not in lengths:
            raise AbiCheckFailed(
                f"stored value is {len(cast('Bytes', value))} bytes, "
                f"not the {'/'.join(str(length) for length in sorted(lengths))} {name} wants"
            )


def _retyped_as(value: ChainValue | None, ty: object) -> ChainValue | None:
    """`value` re-typed to the type `ty` names, across the union/int-enum family.

    Called immediately after `_require_ty` passes, by every read that decodes a
    held value under a caller-supplied `ty`: `_StorageBucket.get` and
    `ContractUnion.payload`.

    **Why a tag-level check is not the whole answer here** (the M1-E2
    final-review ruling that amends D6 x E9). The check `get` makes is TAG
    level -- "is this an `ScVec`?", "is this a `u32`?" -- because that is the
    only check the CHAIN makes: the host hands back a bare word and looks up no
    spec. But the chain does not hand back a *typed object* either, and the
    `ty` argument is what the program reads the word as. Tier 1 holds real
    Python objects, so without this step a stored `Color.Green` read as
    `get(K, U32)` came back as the `Color` object it went in as, and E9's
    no-coercion rule (`ContractEnum != U32`, deliberately kept) then answered
    `== U32(7)` False at tier 1 and True on chain. Re-typing to what was ASKED
    for is what makes the two tiers say the same thing; E9's rule about
    comparing two values is untouched.

    The four crossings, each exactly what the word alone supports:

    * a `ContractEnum` subclass requested over a stored `U32` (or over another
      enum's member): the requested class's member for that discriminant. The
      discriminant is NOT checked against the class's declared cases, because
      the chain cannot check it either -- there is no case list at a storage
      read, and refusing here would reject at tier 1 a read the chain performs
      happily. An undeclared discriminant therefore reads back as a member the
      class never declared, whose `repr` says so (`<Color discriminant 9>`);
    * a `ContractEnum` stored, anything else requested (the family check has
      already narrowed that to `U32`): the bare `U32` it is on chain;
    * a `ContractUnion` subclass requested over a stored `Vec` led by a
      `Symbol` (or over another union's value): the requested class, rebuilt
      through its own construction path from that name and the remaining
      elements. The name is likewise NOT checked against the declared cases;
      a `Symbol` naming no variant rebuilds anyway, and `tag()` then answers
      it, which is what the compiled `vec_get` at 0 does;
    * a `ContractUnion` stored, a container requested: a copy of the `ScVec` it
      IS on chain (a copy, because the union's own vec is never handed out).

    A vec that does not LEAD with a `Symbol` (or is empty) names no case at
    all, so there is nothing to rebuild: it stays the plain `Vec` it is, and
    the union readers fail on it at tier 1 exactly where the chain's own
    `tag()` read fails its `Symbol` narrow. Everything else -- a same-class
    request above all, which is every ordinary read -- passes through
    UNCHANGED, so ruling E5's identity properties are untouched.
    """
    if value is None:
        # Void: `X | None` accepts it (`_families_of_ty` composes the arm), and
        # there is nothing to re-type -- the word IS Void at both tiers, and
        # reaching into it is what raised `'NoneType' object has no attribute
        # 'value'` before this guard.
        return value
    members = _ty_members(ty)
    if len(members) != 1:
        return value
    origin = get_origin(members[0])
    cls = origin if origin is not None else members[0]
    if isinstance(cls, type) and issubclass(cls, ContractEnum):
        if isinstance(value, cls):
            return value
        discriminant = (
            value._discriminant if isinstance(value, ContractEnum) else cast("U32", value).value
        )
        return cls._construct(discriminant)
    if isinstance(cls, type) and issubclass(cls, ContractUnion):
        if isinstance(value, cls):
            return value
        vec = value._vec if isinstance(value, ContractUnion) else cast("Vec[Any]", value)
        case = vec.get(0) if len(vec) else None
        if not isinstance(case, Symbol):
            return value
        return cls._construct(case.text, tuple(vec)[1:])
    if isinstance(value, ContractEnum):
        return U32(value._discriminant)
    if isinstance(value, ContractUnion):
        return Vec(value._vec.element_type, list(value._vec))
    return value


class ConstructorFailed(RuntimeError):
    """A contract's `__init__` raised, and the host LAUNDERS that (S12).

    Spec S12, verbatim: "the host *launders* constructor errors -- any
    recoverable error raised in the constructor reaches the deployer as
    `Context(InvalidAction)`, not the user's error code (`lifecycle.rs`). **The
    docs must say so, prominently**, because Python developers will expect
    `__init__` exception semantics."

    So this is what `deploy` raises, and the author's exception -- its class, and
    its contract error code -- is deliberately NOT what surfaces. A tier-1 model
    that let `Error.LimitExceeded` out of `__init__` would teach exactly the
    expectation S12 says to warn against, and a test written against it would be
    asserting something the chain never does.

    The original is chained (`raise ... from exc`), so it is reachable as
    `__cause__` for a test that wants to prove which failure happened. That is
    the model of the laundering: the identity is hidden from the caller, not
    thrown away.

    **Not a `ContractError`, and it carries no code.** It stands for a HOST
    action, and no reserved runtime code in `serpent.errors` describes it --
    inventing one would put a number in a tier-1 trace that no on-chain trace
    can contain. A `RuntimeError` instead: loud, and not catchable by an
    `except ContractError` that meant to catch the contract's own errors.

    **What is laundered is what S12 says is laundered: RECOVERABLE errors**
    (`_LAUNDERED_BY_THE_HOST`). An `AuthorizationFailed` out of a constructor is
    not one of them -- the host TRAPS an unauthorized invocation, and a trap is
    not a `Context(InvalidAction)` the deployer reads as "the constructor
    refused" -- so it propagates from `deploy` unchanged. Neither are the model's
    own refusals (`_require_frame`'s `RuntimeError`s): laundering a test-harness
    misuse into "the contract's constructor failed" would blame the contract for
    a bad test.

    **A failed deploy POISONS the env** (`deploy`): nothing is deployed, and no
    second deploy is allowed either, because the failed constructor's writes are
    still in the store -- there is no frame rollback here.
    """


class AuthorizationFailed(RuntimeError):
    """`require_auth` was refused by the `Env`'s allow-set.

    `Env(auths=[...])` is a mock-all-auths ALLOW-SET (S4), and an address that
    is not in it is refused here. On chain the host TRAPS an unauthorized
    invocation -- it does not return a contract error -- so like
    `ConstructorFailed` this is a plain loud `RuntimeError` with no reserved
    code: the auth failure is not part of the contract's error vocabulary, and
    giving it a code would name a number the chain never reports.

    What this is NOT: the host's authorization machinery. There are no auth
    trees, no nonces, no signature checks and no sub-invocation authorization
    anywhere in this repo (see the module docstring). A contract's auth LOGIC is
    therefore not under test at tier 1; sub-plan F's tier 2b is the gate.

    Raised from a CONSTRUCTOR it is NOT laundered into `ConstructorFailed`
    (`_LAUNDERED_BY_THE_HOST`): S12's laundering covers recoverable errors, and
    an unauthorized invocation is a trap.
    """


#: What `deploy` launders into `ConstructorFailed`, and nothing else.
#:
#: S12's rule is about "any **recoverable** error raised in the constructor", so
#: the allowlist is exactly that: the contract's own error codes
#: (`ContractError`, which is every `@contracterror` member plus serpent's
#: reserved runtime errors), and the plain-Python failures an authored body can
#: produce at tier 1 -- an arithmetic overflow, a bad index, a failed
#: assertion. Each of those is something the COMPILED contract surfaces as an
#: in-band error or a guest trap, i.e. a deploy that fails because the
#: constructor did.
#:
#: Everything outside this tuple propagates from `deploy` UNCHANGED, and the two
#: kinds that matters for are deliberate:
#:
#: * `AuthorizationFailed` -- the host traps an unauthorized invocation. A trap
#:   is not the `Context(InvalidAction)` the deployer sees for a recoverable
#:   constructor error, so surfacing it as `ConstructorFailed` would model a
#:   laundering the host does not do (and hide, at tier 1, the one auth failure
#:   a constructor can have);
#: * the model's own `RuntimeError` refusals (`_require_frame`, the frame gate)
#:   -- those say the TEST is wrong, and laundering them would rename a harness
#:   misuse into "the contract's constructor failed".
_LAUNDERED_BY_THE_HOST: tuple[type[Exception], ...] = (
    ContractError,
    ArithmeticError,
    AssertionError,
    AttributeError,
    LookupError,
    TypeError,
    ValueError,
)


def _failed_deploy_note(env: Env) -> str:
    """The clause a POISONED env earns, so "not deployed" is not misleading.

    An env whose deploy failed is not merely undeployed -- it is unusable, and
    saying only "deploy first" would send a caller into the loud refusal in
    `deploy` with no idea why. Empty for every healthy env.
    """
    if not env._poisoned:
        return ""
    return (
        " NOTE: a previous deploy on this Env already FAILED, and a failed "
        "constructor's writes are still in the store (there is no frame "
        "rollback), so this Env cannot be deployed into at all -- use a fresh Env()."
    )


def _require_frame(env: Env, what: str) -> None:
    """Refuse `what` unless `env`'s own invocation frame is active.

    **Ruling E7(ii), and the cheapest structural guard in the model.** Two
    things are structural on chain and free at tier 1 unless someone spends the
    boolean:

    * a host function is only callable from inside an invocation frame;
    * an invocation only exists after the contract was deployed -- the deploy
      operation runs `__constructor` first.

    A tier-1 run reaches both states trivially (`Env().storage()` on a fresh
    env, an export called before the constructor ran, a stray `require_auth`),
    and every assertion made in one of them is an assertion about a state the
    chain cannot produce (dossier risk 13). They are refused LOUDLY, with the
    two names that fix them -- `deploy` and `env.frame()` -- in the message.

    The third case is a frame belonging to a DIFFERENT `Env`. M1 has no
    cross-contract call, so two envs are two unrelated contracts and there is no
    ambient-frame semantics for one reaching into the other's storage.
    """
    active = _frame.current()
    if active is env:
        return
    if active is None:
        if env._instance is None:
            raise RuntimeError(
                f"{what} before the contract was deployed: on chain the deploy "
                "operation runs __init__ (the __constructor export) before any "
                "invocation, so nothing can read or write storage first. At tier 1: "
                "`instance = serpent.env.deploy(MyContract, env)`, then call inside "
                f"`with env.frame():`.{_failed_deploy_note(env)}"
            )
        raise RuntimeError(
            f"{what} outside any invocation frame: a host function is only callable "
            "from inside an invocation. Wrap the call: `with env.frame(): ...` "
            "(`deploy` enters one for the constructor itself)."
        )
    raise RuntimeError(
        f"{what} while another Env's invocation frame is active. M1 has no "
        "cross-contract call, so two Envs are two unrelated contracts: close the "
        "outer frame before framing this one."
    )


class _TtlState:
    """The ledger sequence, and every live-until the model measures against it.

    ONE object, held by the `Env` and threaded into every bucket it hands out,
    for three reasons:

    * `Env.advance` must move exactly one copy of the sequence. A bucket that
      captured its own snapshot would keep answering `has` against the
      pre-advance ledger -- and a test holding a bucket across an `advance` is
      the normal way to write a TTL test. `Ledger` is threaded the same state
      for the same reason: it reads the sequence live, so the number a contract
      OBSERVES and the number expiry is measured against are one number;
    * the instance sub-map's live-until is BUCKET-WIDE (S7: one shared TTL for
      the whole instance entry), so it cannot live in a per-key map;
    * `live_until` is a SEPARATE map from the value store rather than a field
      on a wrapped entry, so the store stays `key -> ChainValue` and the
      deep-copy law in `get`/`set` keeps working on the value itself.

    A key ABSENT from `live_until` is the `None` case: never extended, never
    expires (module docstring's first model choice).
    """

    __slots__ = ("instance_live_until", "live_until", "sequence")

    def __init__(self, sequence: int) -> None:
        self.sequence = sequence
        self.live_until: dict[_StoreKey, int] = {}
        self.instance_live_until: int | None = None


def _extended_live_until(
    live_until: int | None, sequence: int, threshold: int, extend_to: int
) -> int | None:
    """The live-until an extension produces, or `None` when it is a no-op.

    Ruling E4(c)'s algebra, in one place because all three buckets share it:

    * the THRESHOLD GUARD -- an entry with `threshold` or more ledgers of
      lifetime left is not extended at all. A `None` live-until always passes
      the guard (module docstring: the first extension always applies);
    * NEVER-REDUCE -- `max(live_until or 0, sequence + extend_to)`, so a
      smaller `extend_to` after a larger one cannot pull the live-until back;
    * NO CLAMP and NO TRAP -- `extend_to` is used exactly as given, at any
      magnitude. The host fact that would bound it is
      `get_max_live_until_ledger`, which is M2 and unreachable here; sub-plan F
      owns proving S8's clamp/trap asymmetry.

    The `None` return means "leave the live-until alone", which is a different
    `None` from the `live_until` parameter's "never extended" -- the two never
    meet, because the caller only ever writes a non-`None` result.
    """
    if live_until is not None and live_until - sequence >= threshold:
        return None
    return max(live_until or 0, sequence + extend_to)


class Event:
    """Base class for `@contractevent` types.

    A decorator cannot add a member that a type checker can see, so `publish`
    lives on a real base class that event types inherit -- that is what makes
    `Transfer(...).publish(env)` type-check under `mypy --strict`.
    `@contractevent` requires this base.
    """

    __slots__ = ()

    def publish(self, env: Env) -> None:
        """Emit this event: its declared topic list, then its data payload.

        The convention itself lives in `@contractevent`'s metadata -- prefix
        topics, which fields are topics, which `data_format` -- and the
        compiler's desugar reads the SAME dict to build the same
        `contract_event` call. This method is therefore the tier-1 half of one
        convention, not a second model of it.

        Recorded through the same snapshot path `Events.publish` uses
        (`_record`), and deliberately NOT through that method's argument
        validation: the topic list here comes from a declaration
        `@contractevent` has already checked, so re-checking it would only be a
        second copy of the same rule. The one bound either half applies to a
        topic Symbol is the Symbol's own 32 characters, held by
        `Symbol.__init__`: `transfer_completed` is an ordinary event name, and
        the compiler pools it through linear memory at the publish site rather
        than refusing it.
        """
        topics, data = _event_payload(self)
        env.events()._record(topics, data)


def _event_payload(event: Event) -> tuple[tuple[ChainValue, ...], ChainValue]:
    """One event instance's `(topics, data)`, per its declared convention.

    `serpent.decorators` imports THIS module (`Event` is the base
    `@contractevent` requires), so the metadata names are imported inside the
    function rather than at module scope -- by the time an event instance
    exists, `decorators` is fully loaded. Nothing here restates a rule the
    decorator already validated: the topic/data split, the prefix topics and
    the data format are read back, never re-derived.
    """
    from serpent.decorators import (
        _METADATA_ATTR,
        DATA_LOCATION,
        TOPIC_LOCATION,
    )

    metadata = vars(type(event)).get(_METADATA_ATTR)
    if not isinstance(metadata, dict) or metadata.get("kind") != "event":
        raise TypeError(
            f"{type(event).__name__} has no @contractevent declaration, so it has no "
            "topic convention to publish by -- decorate the class with "
            "@contractevent (a bare Event subclass declares nothing)"
        )

    fields: list[tuple[str, Any]] = metadata["fields"]
    locations: dict[str, str] = metadata["locations"]
    values: dict[str, Any] = {name: getattr(event, name) for name, _annotation in fields}

    topics: list[ChainValue] = [Symbol(prefix) for prefix in metadata["prefix_topics"]]
    topics += [values[name] for name, _annotation in fields if locations[name] == TOPIC_LOCATION]
    data_fields = [
        (name, annotation) for name, annotation in fields if locations[name] == DATA_LOCATION
    ]
    return tuple(topics), _event_data(metadata["data_format"], data_fields, values)


def _event_data(
    data_format: str, data_fields: list[tuple[str, Any]], values: dict[str, Any]
) -> ChainValue:
    """The data payload the three `SCSpecEventDataFormat` cases publish.

    The `"map"` case is the one place tier 1 cannot say quite what the chain
    says: on chain the data is a `Map<Symbol, Val>` whose values are
    heterogeneous by design, while `serpent.types.Map` is statically typed in
    its value class. The value type recorded here is therefore the FIRST data
    field's, and it is cosmetic -- a `Map`'s values are never ordered or
    type-checked (`require_map_value`, MJ-7) and `Map.__eq__` ignores it. The
    KEYS, which is what the format is actually about, are exact: one `Symbol`
    per field name, in the `val_cmp` order every tier-1 `Map` keeps.
    """
    # Imported inside the function for `_event_payload`'s cycle reason.
    from serpent.decorators import DATA_FORMATS

    if data_format not in DATA_FORMATS:  # pragma: no cover - the decorator validated it
        raise AssertionError(f"unknown data_format {data_format!r}")
    if data_format == "single-value":
        ((name, _annotation),) = data_fields
        return cast("ChainValue", values[name])
    if data_format == "vec":
        first_name, first_annotation = data_fields[0]
        element_type = _event_vec_element_type(first_annotation, values[first_name])
        return Vec(element_type, [values[name] for name, _annotation in data_fields])
    pairs = [(Symbol(name), values[name]) for name, _annotation in data_fields]
    return Map(Symbol, type(pairs[0][1]), pairs)


def _event_vec_element_type(annotation: Any, first_value: Any) -> type[Any]:
    """The element class a `"vec"`-format payload's `Vec` is built with.

    The DECLARED annotation's class, not `type(first_value)`: a field annotated
    `Bytes` may hold a `Bytes32`, and a `Vec` built on the value's own class
    would then refuse the next element (`Vec` checks `isinstance` against the
    element type). A parameterized annotation (`Vec[U32]`) contributes its
    origin; anything with no runtime class at all (`U32 | None`) falls back to
    the value's type, which is the only class available.
    """
    origin = get_origin(annotation)
    candidate = annotation if origin is None else origin
    if isinstance(candidate, type):
        return candidate
    return type(first_value)


class _StorageBucket:
    """The operations every storage durability shares.

    Keys are any `ChainValue` -- a scalar chain type, a container, or a
    `@contracttype` struct -- because the host's storage key is an arbitrary
    `Val` and real contracts key on tuples/structs (an allowance keyed by
    `(from, spender)`, a balance keyed by `Address`) as often as on a `Symbol`.
    A raw `str` or `int` key is still a static error.

    Every bucket reads and writes ONE store, held by the `Env`, keyed
    `(durability, storage_key(key))`. The durability ints come from
    `serpent._host._scalars.STORAGE_TYPE` -- the pinned generated table the
    emitter and the mini-host also read -- never from a literal restated here.

    The `Env`'s `_TtlState` is threaded in beside the store, so a bucket can
    answer expiry against the live sequence rather than a snapshot of it.

    The `Env` ITSELF is threaded in as well, and every operation below starts
    with `_require_frame`. The accessor (`env.storage()`) is gated too, but a
    bucket is a long-lived wrapper over the store: a caller that captured one
    inside a frame would otherwise keep an ungated back door into the model
    after the frame closed -- a write with no invocation to attribute it to,
    which is precisely the tier-1-only state ruling E7(ii) exists to refuse.
    """

    __slots__ = ("_env", "_store", "_ttl")

    #: Set by each subclass from `STORAGE_TYPE`.
    _DURABILITY: ClassVar[int]
    _DURABILITY_NAME: ClassVar[str]

    def __init__(self, env: Env, store: _Store, ttl: _TtlState) -> None:
        self._env = env
        self._store = store
        self._ttl = ttl

    def _entry_key(self, key: ChainValue) -> _StoreKey:
        return (self._DURABILITY, storage_key(key))

    # --- TTL: per-key here, overridden bucket-wide by `InstanceStorage` -------

    def _live_until_of(self, entry: _StoreKey) -> int | None:
        """`entry`'s live-until ledger, or `None` for a never-extended entry."""
        return self._ttl.live_until.get(entry)

    def _set_live_until(self, entry: _StoreKey, live_until: int) -> None:
        self._ttl.live_until[entry] = live_until

    def _forget_live_until(self, entry: _StoreKey) -> None:
        """Drop `entry`'s live-until: it is a fresh (or gone) entry now."""
        self._ttl.live_until.pop(entry, None)

    def _absent(self, entry: _StoreKey) -> bool:
        """Whether `entry` reads as not-there: never written, or expired.

        ONE definition, used by `get`, `has` and `extend_ttl`, so an expired
        entry cannot be missing from one of them and present in another.
        Expiry is STRICTLY past the live-until ledger (`sequence > live_until`):
        the live-until ledger is the last one the entry is live on.
        """
        if entry not in self._store:
            return True
        live_until = self._live_until_of(entry)
        return live_until is not None and self._ttl.sequence > live_until

    def _extend_entry_ttl(self, key: ChainValue, threshold: U32, extend_to: U32) -> None:
        """The keyed `extend_ttl` body shared by persistent and temporary.

        Both durabilities are extended per key and take the same algebra; the
        difference S8 states between them is the clamp/trap asymmetry, which
        this model refuses to invent (module docstring). Raises `MissingValue`
        for an entry that is not there -- S8's "extending a dead entry errors",
        for both the never-written and the expired case.
        """
        _require_frame(self._env, f"a {self._DURABILITY_NAME} storage extend_ttl")
        entry = self._entry_key(key)
        if self._absent(entry):
            raise MissingValue(
                f"cannot extend the TTL of a {self._DURABILITY_NAME} storage entry "
                f"that is not there (never written, or expired): {key!r}"
            )
        live_until = _extended_live_until(
            self._live_until_of(entry), self._ttl.sequence, threshold.value, extend_to.value
        )
        if live_until is not None:
            self._set_live_until(entry, live_until)

    @overload
    def get(self, key: ChainValue, ty: type[_T], *, default: int | str | bytes | bool) -> _T: ...
    @overload
    def get(self, key: ChainValue, ty: type[_T], *, default: _T) -> _T: ...
    @overload
    def get(self, key: ChainValue, ty: type[_T], default: _T | None = ..., /) -> _T: ...
    @overload
    def get(self, key: ChainValue, ty: type[_T]) -> _T: ...
    def get(self, key: ChainValue, ty: type[_T], default: _T | None = None) -> _T:
        """Read `key`, decoding it as `ty`.

        `ty` is passed explicitly because the host returns an untyped `Val`;
        it is what tells both the compiler and the type checker what comes
        back. Without a `default`, a missing key is a contract error.

        Ruling E12 (decisions.md 2026-08-31) splits `default` into four
        `@overload`s above so a RAW-LITERAL default (`default=0`) types as
        `_T`, not `object`: a single signature solves `_T` against both `ty`
        and `default` and joins them, which is exactly what defeats it for the
        literal case (`tests/fixtures/env_surface.py`'s now-removed
        `# type: ignore[return-value]` was that join). The four arms, in the
        order that matters: a keyword-only raw-literal `default` (adopted
        through `ty`, M1-C); a keyword-only chain-value `default` (ruling E5's
        pass-through); a POSITIONAL-ONLY `default` (today's
        `get(key, ty, default)` accepts one, and keyword-only overloads alone
        would SHRINK accepts); and no `default` at all. The third arm's
        trailing `/` is why the fourth stays reachable for `get(key=...,
        ty=...)` -- a positional-only parameter cannot bind a keyword
        argument, so that spelling can only match the last arm. This is
        typing only: the runtime signature below, and every semantic (E5's
        pass-through, M1-C's adoption, `default=None`'s no-default sentinel),
        are unchanged.

        The model's three rules, each mirroring the compiled form:

        * a hit returns a DEEP COPY, so a caller mutating the result cannot
          reach back into the store;
        * a miss with no `default` raises `MissingValue`, whose
          `CODE_MISSING_VALUE` is the code the emitter's own guard emits. An
          EXPIRED entry is a miss (`_absent`): TTL expiry is answered lazily,
          here, rather than by sweeping the store on `advance`;
        * a hit is TAG-CHECKED against `ty` (`_require_ty`), failing with
          `AbiCheckFailed` exactly where the emitter's narrow check fails, and
          is then RE-TYPED to `ty` (`_retyped_as`): the host hands back a bare
          word and `ty` is what the program reads it as, so a stored int-enum
          member read as `U32` comes back as the `U32` it is on chain, and a
          stored `Vec` read as a union comes back as that union.

        A miss WITH a `default` has two halves, because the compiled form is an
        `IfExp` whose `orelse` IS the default EXPRESSION -- no host call and no
        narrowing -- and what that expression evaluates to depends on how it was
        written:

        * a default that is already a CHAIN VALUE comes back as-is, un-copied
          and un-checked (ruling E5): the caller's own object is the value of
          the whole expression at both tiers;
        * a default that is NOT a chain value -- a raw `0`, `True`, `"NAME"`,
          `b"\\x01"` -- is ADOPTED through `ty` (`ty(default)`), because that is
          what the compiled tier does: `default=0` in this typed position is
          M1-C literal adoption, so the compiled `orelse` is `U32(0)`, and a
          model that answered the Python `0` would diverge SILENTLY (`U32(0) ==
          0` is True, so a type-blind assertion goes green). If `ty` refuses the
          value, its OWN error propagates unsoftened -- `U32(-1)` raises
          `ValueError` here exactly where the frontend reports `SPT3004`.

        `default=None` is the NO-DEFAULT sentinel, not a default of `None`: an
        explicit `default=None` raises `MissingValue` like a bare `get`. No
        compiled contract can reach that spelling -- the frontend refuses `None`
        in this position with `SPT3018` -- so the sentinel is a tier-1 signature
        detail, not a semantics the two tiers have to agree on.

        (The frontend's escape analysis marks a container passed to `default=`
        accordingly.)
        """
        _require_frame(self._env, f"a {self._DURABILITY_NAME} storage read")
        entry = self._entry_key(key)
        if self._absent(entry):
            if default is None:
                raise MissingValue(f"no {self._DURABILITY_NAME} storage entry for {key!r}")
            return cast("_T", _adopt(default, ty))
        stored = self._store[entry]
        _require_ty(stored, ty)
        return cast("_T", copy.deepcopy(_retyped_as(stored, ty)))

    def set(self, key: ChainValue, value: ChainValue) -> None:
        """Write `value` under `key`.

        Stores a DEEP COPY (ruling E5). The host serializes a `Val` into the
        ledger entry, so a later mutation of the caller's object cannot change
        what was written; a model that stored the reference would report the
        mutation from storage and diverge silently. The key is normalized to a
        `storage_key` here and now, so mutating the key object afterwards
        cannot move the entry either.

        A write is a FRESH entry as far as TTL goes: its live-until goes back to
        `None`, so re-setting an expired persistent or temporary key revives it
        (the module docstring names that as a tier-1 convenience -- the chain
        would make you restore an archived entry first). `InstanceStorage`
        overrides the reset away, because its live-until is bucket-wide and one
        key's write cannot honestly resurrect the whole instance entry.
        """
        _require_frame(self._env, f"a {self._DURABILITY_NAME} storage write")
        entry = self._entry_key(key)
        self._store[entry] = copy.deepcopy(value)
        self._forget_live_until(entry)

    def has(self, key: ChainValue) -> Bool:
        """Whether `key` is present -- and not expired (`_absent`).

        Returns the chain `Bool` the host hands back, not a Python `bool`, so
        the value stays a chain value all the way through; `Bool` is truthy in
        an `if` statement.
        """
        _require_frame(self._env, f"a {self._DURABILITY_NAME} storage has")
        return Bool(not self._absent(self._entry_key(key)))

    def del_(self, key: ChainValue) -> None:
        """Delete `key`. Named `del_` because `del` is a Python keyword.

        Deleting an absent key is a silent no-op.

        **Unverified assumption.** This mirrors the mini-host, which makes
        `del_contract_data` a no-op while `map_del` traps, and flags the
        asymmetry as two different host behaviours the rig must not unify. That
        asymmetry is not verified against the real host anywhere in this repo,
        so both tiers here agree with each other and possibly neither agrees
        with the chain. Consistency with the other model is the only reason
        this direction was picked; sub-plan F's real-host tier is where it gets
        checked, and until then a contract must not rely on it.

        The live-until goes with the value: a later `set` under the same key is
        a genuinely fresh entry, not one that inherits a dead entry's expiry.
        """
        _require_frame(self._env, f"a {self._DURABILITY_NAME} storage delete")
        entry = self._entry_key(key)
        self._store.pop(entry, None)
        self._forget_live_until(entry)


class InstanceStorage(_StorageBucket):
    """Instance storage: lives and dies with the contract instance.

    All instance entries share one TTL with the contract instance itself, so
    `extend_ttl` takes no key.
    """

    __slots__ = ()

    _DURABILITY: ClassVar[int] = STORAGE_TYPE["instance"]
    _DURABILITY_NAME: ClassVar[str] = "instance"

    #: The instance sub-map has ONE live-until, so every per-key TTL hook is
    #: redirected to the bucket-wide field (S7). Overriding the three accessors
    #: rather than special-casing the shared bodies is what keeps `get`/`has`/
    #: `_absent` identical for all three durabilities.

    def _live_until_of(self, entry: _StoreKey) -> int | None:
        return self._ttl.instance_live_until

    def _set_live_until(self, entry: _StoreKey, live_until: int) -> None:
        self._ttl.instance_live_until = live_until

    def _forget_live_until(self, entry: _StoreKey) -> None:
        """A deliberate NO-OP: there is no per-key live-until to reset.

        So a `set` does not revive an expired instance sub-map, unlike the other
        two buckets, and a `del_` of one key does not extend the instance's life.
        The honest reading of S7's one shared TTL, and the chain's own answer is
        harsher still: an archived instance entry means the invocation never
        runs.
        """

    def extend_ttl(self, threshold: U32, extend_to: U32) -> None:
        """Extend the instance's TTL to `extend_to` if it falls below
        `threshold` ledgers remaining.

        No key: the whole instance sub-map shares one live-until with the
        contract instance itself (S7). Valid even when the sub-map is empty --
        the instance entry exists once the contract is deployed -- and it
        governs entries written afterwards, because there is only the one
        live-until. (S7 also names an early flush on re-entrant self-call:
        unobservable in M1, which has no cross-contract call, and not modelled.)

        Raises `MissingValue` once the instance's TTL has LAPSED, rather than
        quietly reviving a contract the chain would have archived -- S8's
        dead-entry rule, for the one entry that has no key.

        The algebra is `_extended_live_until`'s: threshold guard, never-reduce,
        and no clamp (see the module docstring's TTL section).
        """
        _require_frame(self._env, "an instance storage extend_ttl")
        ttl = self._ttl
        live_until = ttl.instance_live_until
        if live_until is not None and ttl.sequence > live_until:
            raise MissingValue(
                "cannot extend the TTL of an instance entry whose TTL has lapsed "
                f"(live until {live_until}, now {ttl.sequence})"
            )
        extended = _extended_live_until(live_until, ttl.sequence, threshold.value, extend_to.value)
        if extended is not None:
            ttl.instance_live_until = extended


class PersistentStorage(_StorageBucket):
    """Persistent storage: archived when its TTL lapses, restorable.

    Tier 1 models neither the archive nor the restore: a lapsed entry simply
    reads absent, and a re-set revives it (module docstring's TTL section).
    """

    __slots__ = ()

    _DURABILITY: ClassVar[int] = STORAGE_TYPE["persistent"]
    _DURABILITY_NAME: ClassVar[str] = "persistent"

    def extend_ttl(self, key: ChainValue, threshold: U32, extend_to: U32) -> None:
        """Extend `key`'s TTL to `extend_to` if it falls below `threshold`
        ledgers remaining.

        Threshold guard, never-reduce, and `MissingValue` for an entry that is
        not there (never written, or expired). **NOT modelled: S8's clamp** --
        an `extend_to` past the network maximum is taken as given here and
        clamped on chain. The maximum is `get_max_live_until_ledger`, an M2 host
        fact; sub-plan F owns the proof (module docstring's TTL section).
        """
        self._extend_entry_ttl(key, threshold, extend_to)


class TemporaryStorage(_StorageBucket):
    """Temporary storage: deleted outright when its TTL lapses.

    Tier 1 does not delete it, it just reads absent -- and a re-set revives it,
    which for this durability is a new entry on chain too.
    """

    __slots__ = ()

    _DURABILITY: ClassVar[int] = STORAGE_TYPE["temporary"]
    _DURABILITY_NAME: ClassVar[str] = "temporary"

    def extend_ttl(self, key: ChainValue, threshold: U32, extend_to: U32) -> None:
        """Extend `key`'s TTL to `extend_to` if it falls below `threshold`
        ledgers remaining.

        The same body as `PersistentStorage.extend_ttl`, and that is itself the
        model's loudest gap: **NOT modelled: S8's trap** -- an `extend_to` past
        the network maximum TRAPS for a temporary entry, where a persistent one
        clamps. Tier 1 accepts it, so a green test here is not evidence the call
        survives on chain. Same missing host fact
        (`get_max_live_until_ledger`, M2), same carried obligation to sub-plan F.
        """
        self._extend_entry_ttl(key, threshold, extend_to)


class Storage:
    """`env.storage()`: the three durabilities, each its own bucket type."""

    __slots__ = ("_env", "_store", "_ttl")

    def __init__(self, env: Env, store: _Store, ttl: _TtlState) -> None:
        self._env = env
        self._store = store
        self._ttl = ttl

    def instance(self) -> InstanceStorage:
        return InstanceStorage(self._env, self._store, self._ttl)

    def persistent(self) -> PersistentStorage:
        return PersistentStorage(self._env, self._store, self._ttl)

    def temporary(self) -> TemporaryStorage:
        return TemporaryStorage(self._env, self._store, self._ttl)


class Ledger:
    """`env.ledger()`: read-only facts about the ledger being applied.

    Both readers are gated on the invocation frame like every other host call
    (`_require_frame`), even though neither can change anything: on chain
    `get_ledger_timestamp` is a host function, and a tier-1 read of it with no
    invocation open is the same state the chain cannot produce.

    The SEQUENCE is read LIVE, out of the `Env`'s own `_TtlState`, for the
    reason that state's docstring gives about buckets: a `Ledger` bound before
    an `env.advance(n)` would otherwise keep answering the pre-advance number
    while every storage expiry compared against the moved one, and two readers
    of one ledger disagreeing about which ledger it is is precisely the snapshot
    hazard the single `_TtlState` exists to remove. The TIMESTAMP is an `int`
    copy because nothing moves it: `advance` deliberately leaves the clock
    alone, and a later timestamp comes from a new `Env(timestamp=...)`.
    """

    __slots__ = ("_env", "_timestamp", "_ttl")

    def __init__(self, env: Env, timestamp: int, ttl: _TtlState) -> None:
        self._env = env
        self._timestamp = timestamp
        self._ttl = ttl

    def timestamp(self) -> U64:
        """Seconds since the Unix epoch, as the host reports it.

        `U64`, not `Timepoint`: the host's `get_ledger_timestamp` returns a
        `U64Val`, and serpent does not silently reinterpret an `ScVal` case.
        """
        _require_frame(self._env, "a ledger timestamp read")
        return U64(self._timestamp)

    def sequence(self) -> U32:
        """The sequence number of the ledger being applied, read live (the class
        docstring says why it is not a snapshot)."""
        _require_frame(self._env, "a ledger sequence read")
        return U32(self._ttl.sequence)


class Events:
    """`env.events()`: the contract event sink."""

    __slots__ = ("_env", "_published")

    def __init__(self, env: Env, published: list[PublishedEvent]) -> None:
        self._env = env
        self._published = published

    def publish(self, topics: tuple[ChainValue, ...], data: ChainValue) -> None:
        """Emit an event.

        `topics` is a heterogeneous tuple, not a homogeneous `Vec`: the
        canonical Soroban shape is `(Symbol, Address, Address)` -- an event
        name followed by the addresses it concerns. `topics[0]` is
        conventionally a `Symbol` naming the event; the host does not enforce
        that, but indexers and RPC filtering assume it.

        The model DOES enforce it, with `BadArgument`, and that is a
        tier-1-only reject: the frontend already refuses both shapes at compile
        time (`SPT1038` for an empty topic tuple, `SPT3019` for a first topic
        that is not a `Symbol`), so no compiled contract can reach these raises
        through THIS surface, and a hand-written tier-1 call gets the same
        answer the compiler would have given. The host itself enforces none of
        it.

        LENGTH is not part of it (ruling E11). A topic Symbol's one bound is
        the Symbol's own 32 characters, held by `Symbol.__init__`, so a
        hand-written `topics[0]` past the 9-character `SymbolSmall` form is
        recorded here exactly as a declared prefix topic of the same length
        already was; it pools through linear memory at the publish site.

        `Event.publish` records through `_record` instead, skipping these two
        checks deliberately -- its topic list comes from a declaration the
        decorator has already validated (see that method).

        The recorded event is a SNAPSHOT (ruling E5): a later mutation of the
        topics or the data cannot change what was published, exactly as on
        chain, where `contract_event` serializes them.
        """
        # The frame gate first, before any argument talk: `_record` re-applies
        # it (it is the shared invariant), but a publish attempted with no
        # invocation to attribute it to is that fact, not a bad argument.
        _require_frame(self._env, "an event publish")
        if not topics:
            raise BadArgument("an event needs at least one topic, naming it")
        name = topics[0]
        if not isinstance(name, Symbol):
            raise BadArgument(
                f"topics[0] must be a Symbol naming the event, not a {type(name).__name__}"
            )
        self._record(topics, data)

    def _record(self, topics: tuple[ChainValue, ...], data: ChainValue) -> None:
        """Append one SNAPSHOT of `(topics, data)` inside a frame.

        The frame gate and the deep copy, with no argument validation -- shared
        by `publish` (which validates first) and by `Event.publish`, whose
        topics come from a validated declaration. Both halves of the event
        surface therefore obey ONE isolation law rather than two copies of it.
        """
        _require_frame(self._env, "an event publish")
        self._published.append(copy.deepcopy((tuple(topics), data)))


class Env:
    """The host, as a contract sees it -- and the tier-1 model behind it.

    A CONTRACT never constructs one: the compiled form receives the host's own
    env, and `Env` is a parameter type there, nothing more. A TEST constructs
    one, and gets the in-memory model this module documents: a store, an event
    list, an auth allow-set and a fixed ledger. Read the module docstring for
    what that model refuses to pretend to be -- it is not an oracle, and sub-plan
    F's real-host tier is the gate.

    `auths=None` means mock-all-auths: every `require_auth` is recorded and
    allowed. A non-None iterable is the allow-set an authorization is checked
    against.

    **One `Env` is one deployed contract.** `deploy` runs the constructor into
    it once and marks it deployed; until then every host accessor here refuses,
    and so does `frame()` (ruling E7(ii), `_require_frame`). M1 has no
    cross-contract call, so there is no second instance to model and no
    semantics for two envs framed at once.

    **And one Env is one ATTEMPT.** A deploy whose constructor fails POISONS the
    env: it is not deployed, and it can never be deployed into again. The reason
    is the model's own honesty about rollback -- the failed constructor's writes
    are still in the store, so a retry would hand the second instance the dead
    one's leftovers, which is exactly the class of tier-1-only state the whole
    gate exists to refuse (a chain deploy is atomic: it either publishes the
    instance or leaves nothing behind).
    """

    __slots__ = (
        "_auths",
        "_events",
        "_instance",
        "_poisoned",
        "_recorded_auths",
        "_store",
        "_timestamp",
        "_ttl",
    )

    def __init__(
        self,
        *,
        timestamp: int = DEFAULT_LEDGER_TIMESTAMP,
        sequence: int = DEFAULT_LEDGER_SEQUENCE,
        auths: Iterable[Address] | None = None,
    ) -> None:
        self._store: _Store = {}
        self._events: list[PublishedEvent] = []
        self._recorded_auths: list[RecordedAuth] = []
        self._auths: tuple[Address, ...] | None = None if auths is None else tuple(auths)
        self._timestamp = timestamp
        # The sequence lives in the TTL state, not beside it: `advance` must move
        # exactly one copy of the number both `ledger().sequence()` and every
        # expiry comparison read (`_TtlState`).
        self._ttl = _TtlState(sequence)
        #: The instance `deploy` constructed, or `None` while nothing is
        #: deployed. ONE object doing two jobs: the pre-deploy refusal reads it
        #: as a boolean, and a second `deploy` names what is already here.
        self._instance: object | None = None
        #: Set by a FAILED deploy, and never cleared: the constructor's writes
        #: survive it, so this env can no longer honestly deploy anything
        #: (`deploy`, and the class docstring's second paragraph).
        self._poisoned = False

    def storage(self) -> Storage:
        _require_frame(self, "env.storage()")
        return Storage(self, self._store, self._ttl)

    def ledger(self) -> Ledger:
        _require_frame(self, "env.ledger()")
        return Ledger(self, self._timestamp, self._ttl)

    def events(self) -> Events:
        _require_frame(self, "env.events()")
        return Events(self, self._events)

    # --- the invocation frame (ruling E7) ------------------------------------

    def frame(self) -> contextlib.AbstractContextManager[None]:
        """Run a block as one invocation of the deployed contract. TEST-FACING.

        Tier-1 contract methods are ORDINARY PYTHON -- ruling E1 deliberately
        did not wrap them in an `invoke` helper, because the whole point of tier
        1 is that `counter.bump(env)` is a method call. What the host also
        supplies, and Python does not, is the FRAME the call runs in, so that is
        the one thing a test says out loud::

            env = Env()
            counter = deploy(Counter, env, U32(0))
            with env.frame():
                counter.bump(env)

        Deliberately not in `serpent.__all__` (the loader restricts a contract's
        imports to those names, so a contract cannot reach this) and not a
        method a compiled contract has any analogue of.

        Three rules, each because of something the chain does or refuses:

        * **it refuses to open before `deploy`** -- the deploy operation runs
          `__constructor` first, always (`_require_frame`'s docstring);
        * **it nests on the same env** -- a contract calling its own method is
          another host frame, and the ambient env is the same env either way.
          The exit restores the OUTER frame rather than clearing everything,
          which is what the token-based `_frame.leave` is for;
        * **it refuses a second env while one is active** -- no cross-contract
          call in M1, so there is no ambient-frame semantics to give it.

        The exit is a `try/finally`, so a frame whose body RAISES still clears
        the ambient env (dossier F.1.7). Nothing else here rolls back: the
        events and the storage writes of a failed frame stay, and the module
        docstring names that as one of the model's non-models.
        """
        return self._invocation(deploying=False)

    @contextlib.contextmanager
    def _invocation(self, *, deploying: bool) -> Iterator[None]:
        """`frame()`'s body, plus the one variant only `deploy` may use.

        `deploying=True` is how the constructor gets its frame: on chain
        `__constructor` runs inside the deploy operation's frame, so it has full
        storage access and can authorize, and it is the ONE frame that legally
        opens on an env with nothing deployed yet. Keeping it a private keyword
        is what stops that exemption from being the hole the whole gate leaks
        through.
        """
        if not deploying and self._instance is None:
            raise RuntimeError(
                "cannot open an invocation frame before the contract is deployed: "
                "`instance = serpent.env.deploy(MyContract, env)` first (deploy runs "
                "__init__, the __constructor export, in a frame of its own)"
                f"{_failed_deploy_note(self)}"
            )
        active = _frame.current()
        if active is not None and active is not self:
            raise RuntimeError(
                "an invocation frame for another Env is already active. M1 models no "
                "cross-contract call, so two contracts cannot be in each other's "
                "frames; close the outer one first."
            )
        token = _frame.enter(self)
        try:
            yield
        finally:
            # Unconditional, and a RESTORE rather than a clear: a raising frame
            # must not leave a stale ambient env behind (dossier F.1.7), and a
            # nested frame's exit must leave the outer one standing.
            _frame.leave(token)

    def _record_auth(self, address: Address, args: Vec[Any] | None) -> None:
        """Record (and allow, or refuse) one authorization. `Address` calls this.

        The auth model in four lines, and every one of them is S4's
        mock-all-auths rather than the host's machinery:

        * it needs a frame -- `Address.require_auth()` takes no `Env`, so the
          ambient frame is the only thing that says which contract is asking,
          and a stray call outside one is refused loudly (dossier risk 7: with
          mock-all-auths a silent pass would SUCCEED, recording an
          authorization against whatever env happened to be ambient);
        * `auths=None` records and allows -- that is what mock-all-auths means;
        * a non-`None` allow-set refuses a non-member with
          `AuthorizationFailed`, and refuses BEFORE recording: on chain the host
          traps, so there is no invocation left to have recorded anything.
          **That reasoning is not free of tension**, and the tension is named
          rather than smoothed over: the same argument would discard the events
          and storage writes of a frame that raises, and this model KEEPS those
          (`test_an_event_published_before_a_raise_is_not_rolled_back` pins the
          non-rollback deliberately). The difference is only that a refused auth
          never produced a record to have to roll back, so not creating one is
          the cheap answer here -- it is not evidence that the model rolls
          anything back. S9's rollback stays a named carried obligation to
          sub-plan F's tier 2b, for both surfaces;
        * the args are DEEP-COPIED in (ruling E5). The host serializes them into
          the authorization entry, and the frontend's escape exemption for
          `require_auth_for_args` (`recognize.note_escapes`) is only sound
          because of this copy -- `test_env_deploy.py`'s snapshot test is what
          holds that up.
        """
        what = "require_auth()" if args is None else "require_auth_for_args()"
        _require_frame(self, what)
        if self._auths is not None and address not in self._auths:
            raise AuthorizationFailed(
                f"{address.strkey} is not authorized: it is not in this Env's "
                f"auths allow-set ({len(self._auths)} address(es)). Pass "
                "`Env(auths=None)` for mock-all-auths, or add the address."
            )
        self._recorded_auths.append((address, copy.deepcopy(args)))

    # --- test-facing inspection (NOT `serpent.__all__` names) ----------------

    def advance(self, n: int) -> None:
        """Advance the ledger sequence by `n`, so TTLs can lapse. TEST-FACING.

        Deliberately not in `serpent.__all__` and not something a contract can
        reach: a contract observes the ledger, it does not move it. This is the
        hook that makes expiry testable at tier 1 -- without it nothing ever
        dies and S8's dead-entry rule is unreachable rather than modelled.

        `n` must be positive: a ledger sequence does not go backwards, and a
        zero advance is a test-authoring mistake worth naming rather than
        absorbing. `ValueError`/`TypeError`, not a `ContractError`, because no
        contract error code describes a misused test hook.

        **Only the sequence moves.** `ledger().timestamp()` stands still, on
        purpose: seconds-per-ledger is a network fact, not a protocol constant
        this model may invent, and the module docstring would rather have an
        obviously-frozen clock than a plausible-looking made-up one. A test that
        needs a later timestamp constructs `Env(timestamp=...)`.

        Expiry is answered lazily by `get`/`has`, so this is O(1) and no entry
        is physically removed (module docstring's TTL section).
        """
        if not isinstance(n, int) or isinstance(n, bool):
            raise TypeError(f"advance() takes an int, not {type(n).__name__}")
        if n <= 0:
            raise ValueError(f"advance() takes a positive number of ledgers, not {n}")
        self._ttl.sequence += n

    @property
    def published_events(self) -> tuple[PublishedEvent, ...]:
        """Every event published through this `Env`, in order, as snapshots.

        Deliberately not in `serpent.__all__`: `published_events` is a test
        surface, and `serpent.__all__` is the AUTHORING surface a contract
        resolves names against.

        DEEP-COPIED on the way out, for the same reason `get` is: an inspection
        surface that handed out the recorded objects would let a test mutate the
        record it just read and see its own mutation on the next read.
        `recorded_auths` makes the same copy, on the way in and on the way out.

        No frame rollback (module docstring): an event published by a method
        that then raises is still here.
        """
        return copy.deepcopy(tuple(self._events))

    @property
    def recorded_auths(self) -> tuple[RecordedAuth, ...]:
        """Every authorization asked for through this `Env`, in order.

        Each entry is `(address, args)`: the args are a `Vec` SNAPSHOT for
        `require_auth_for_args` and `None` for a bare `require_auth`, so the two
        forms are distinguishable rather than collapsed (which is what the
        mini-host does -- it shape-checks the args and discards them).

        Recording IS the auth model (mock-all-auths): the host's real
        authorization trees -- nonces written to storage, sub-invocation trees,
        signature verification -- are not modelled anywhere in this repo. A
        refused authorization is NOT here (`_record_auth`): the host traps, so
        there is no invocation left to have recorded one.

        DEEP-COPIED on the way out, for the same reason `published_events` is:
        an inspection surface that handed out its own records would let a test
        mutate the args it just read and see the mutation on the next read. The
        copy on the way IN is in `_record_auth`.
        """
        return copy.deepcopy(tuple(self._recorded_auths))


def deploy(cls: type[_C], env: Env, *args: Any, **kwargs: Any) -> _C:
    """Deploy `cls` into `env`: construct it and run `__init__` once. TEST-FACING.

    **`__init__` IS `__constructor` (S12).** On chain the deploy operation runs
    it exactly once, before any invocation, in a frame with full storage access
    -- so this helper constructs the instance, opens that frame, runs `__init__`
    in it, and closes the frame again on the way out (`try/finally`, so a
    constructor that raises leaves nothing ambient behind). Making the
    once-and-at-deploy nature visible in the test is the whole point of the
    helper; the alternative (`C()` then a method call) silently models a
    contract that was never deployed.

    Returns the instance, typed as `cls`, so the test can call its methods --
    which are ordinary Python, inside `with env.frame():`.

    * **a RECOVERABLE exception out of `__init__` becomes `ConstructorFailed`**,
      chaining the original as `__cause__`. That is S12's laundering, and
      `ConstructorFailed`'s docstring quotes the spec on why it must be
      prominent. What counts as recoverable is `_LAUNDERED_BY_THE_HOST`;
      anything else -- an `AuthorizationFailed` (the host traps instead), the
      model's own refusals -- propagates unchanged. Only errors from the
      constructor's BODY are considered at all: a wrong-arity or unknown-keyword
      call is bound and rejected first, as a plain `TypeError`, because that
      mistake is the test author's and no host laundering describes it;
    * **a FAILED deploy poisons the env.** It deploys nothing (so `frame()` and
      every accessor still refuse), and it also refuses every LATER deploy into
      the same env: the failed constructor's writes are still in the store,
      because this model has no frame rollback, so a retry would hand the second
      instance the dead one's leftovers. On chain a deploy is atomic -- there is
      no such half-written contract to inherit -- so retrying here would be a
      tier-1-only state, which is the one thing this gate is for. The remedy is a
      fresh `Env()`, and the message says so;
    * **a second deploy into the same env is refused** for the same one-Env-one-
      instance reason (M1 has no cross-contract call): a second one is a
      test-authoring mistake, not a second constructor run;
    * **no constructor is fine** (S12: a 0-arg constructor may be absent), but
      passing arguments to a class that has none is an error rather than a
      silent drop -- on chain the deploy operation itself fails. That path still
      enters and leaves the deploy frame, so the no-cross-contract rule applies
      to a constructor-less contract exactly as it does to any other.

    `deploy` does NOT require `@contract`: it models the host's deploy step, not
    the compiler's declaration checks (which the decorator already made at class
    creation, and which the frontend makes again at compile time). A plain class
    with an `__init__(self, env, ...)` deploys, which is what lets the model's
    own tests deploy a do-nothing contract.

    Deliberately a module attribute of `serpent.env` and NOT a
    `serpent.__all__` name: the loader restricts a contract's imports to that
    list, so a contract can never resolve `deploy`.
    """
    if env._instance is not None:
        raise RuntimeError(
            f"this Env already has {type(env._instance).__name__} deployed into it. "
            "One Env models one deployed contract instance (M1 has no cross-contract "
            "call), and a constructor runs exactly once -- use a fresh Env()."
        )
    if env._poisoned:
        raise RuntimeError(
            "a deploy into this Env already FAILED, so it cannot be deployed into "
            "again: the failed constructor's storage writes are still here (this "
            "model has no frame rollback), and a second instance must not inherit "
            "them -- on chain a deploy is atomic, so there is no half-written "
            "contract to inherit from. Use a fresh Env()."
        )

    instance = cls.__new__(cls)
    if cls.__init__ is object.__init__:
        if args or kwargs:
            raise TypeError(
                f"{cls.__name__} has no constructor (__init__), so deploy() takes no "
                "contract arguments. On chain, deploying with constructor arguments a "
                "contract has no __constructor for fails the deploy operation."
            )
        # Nothing to run -- but the frame is still entered and left, so that a
        # constructor-less contract is refused by exactly the same rules as any
        # other (notably: not while another Env's frame is active).
        with env._invocation(deploying=True):
            pass
        env._instance = instance
        return instance

    constructor = cast("Callable[..., None]", cls.__init__)
    signature = inspect.signature(constructor)
    # Bind first: a signature mistake is the caller's, and laundering it as
    # `ConstructorFailed` would blame the contract for a bad test.
    try:
        signature.bind(instance, env, *args, **kwargs)
    except TypeError as exc:
        raise TypeError(
            f"{cls.__name__}.__init__ cannot take these arguments: {exc}"
            f"{_missing_env_note(signature)}"
        ) from exc

    with env._invocation(deploying=True):
        try:
            constructor(instance, env, *args, **kwargs)
        except _LAUNDERED_BY_THE_HOST as exc:
            env._poisoned = True
            raise ConstructorFailed(
                f"the constructor of {cls.__name__} failed: "
                f"{type(exc).__name__}: {exc}. The HOST launders this -- the deployer "
                "sees Context(InvalidAction), never the contract's own error code -- "
                "so the original is available as __cause__ and nowhere else."
            ) from exc
        except BaseException:
            # NOT laundered (`_LAUNDERED_BY_THE_HOST`): an auth trap, one of the
            # model's own refusals, or an interrupt. The deploy still failed, so
            # the env is still poisoned -- only the identity of the error differs.
            env._poisoned = True
            raise

    # Only a SUCCESSFUL constructor deploys anything: a failed deploy leaves an
    # env that is both undeployed and poisoned. What the constructor already
    # wrote stays in the store, because the model has no frame rollback (module
    # docstring) -- the two refusals are what keep that unobservable.
    env._instance = instance
    return instance


def _missing_env_note(signature: inspect.Signature) -> str:
    """The hint a bind failure earns when the constructor forgot `env`.

    `__init__(self)` is the shape a Python author writes by habit, and the bare
    bind error for it ("too many positional arguments") describes the symptom
    rather than the cause: `deploy` always passes the env, because
    `__constructor` runs with a live host env. Empty for every other shape.
    """
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    variadic = any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL
        for parameter in signature.parameters.values()
    )
    if len(positional) >= 2 or variadic:
        return ""
    return (
        " A contract constructor takes the env after `self` -- "
        "`def __init__(self, env: Env, ...) -> None` -- because __constructor runs "
        "with a live host env, and deploy() always passes it."
    )
