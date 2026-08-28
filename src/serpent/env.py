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
* **no TTL** -- no clamp, no trap, no dead entries, no archival. The host's
  maximum live-until ledger is not reachable in M1, and a serpent-chosen
  constant would be a guess.

Two places tier 1 answers a question differently from the host, on purpose:

* `Events.publish` **rejects** a first topic that is not a short `Symbol`
  (spec's authoring convention). The host does not enforce that. Nothing a
  compiled contract can express reaches the reject -- the frontend refuses the
  same shape at compile time -- so this is a tier-1-only reject that keeps the
  two ends of the convention in agreement;
* `<bucket>.del_` of an absent key is a silent no-op (see `del_`).

**Deep copy is law.** The model never stores or returns a reference the caller
can mutate: `set()` deep-copies in, `get()` deep-copies out, `publish()`
snapshots its topics and data. `serpent.types` containers mutate in place while
the host's operations are functional, so a model that stored a reference would
report a post-write mutation from storage while the chain reported the snapshot
-- the single most likely silent divergence in this file. The frontend's
escape-analysis exemption for `<bucket>.set(k, v)` (`recognize.note_escapes`)
depends on exactly this, and
`tests/unit/test_env_model.py`'s isolation property is what keeps it true.

Storage buckets are three distinct types rather than one parameterized bucket
because their TTL operations genuinely differ: an instance entry's TTL covers
the whole instance and is extended without a key, while persistent and
temporary entries are extended per key. Typing them separately means the
wrong call is a type error, not a runtime surprise.
"""

from __future__ import annotations

import copy
from collections.abc import Hashable, Iterable
from types import UnionType
from typing import Any, ClassVar, TypeAlias, TypeVar, Union, cast, get_args, get_origin

from serpent import val
from serpent._host._scalars import STORAGE_TYPE
from serpent.errors import AbiCheckFailed, BadArgument, MissingValue
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
#: `Bytes`/`Address`), the containers, or a `@contracttype` struct.
#:
#: This is deliberately a closed union rather than `object`: a raw `str` or
#: `int` key is a static error, which is the whole point of the chain types.
ChainValue: TypeAlias = _ChainValue[Any] | Vec[Any] | Map[Any, Any] | Struct

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
#:   and only `BytesN`'s extra length check is finer. Tier 1 takes the tag half,
#:   because a check the chain does not make would reject what the chain
#:   accepts;
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
}

#: The family a `@contracttype` struct shares with `Map`, and the family a
#: `None` (Void) value stands in -- the `Option` case the emitter COMPOSES
#: (`VOID_VAL` or the wrapped type's own check) rather than tabulating.
_MAP_FAMILY = "map"
_VOID_FAMILY = "void"


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
        if hasattr(ty, "__dataclass_fields__"):
            return frozenset({_MAP_FAMILY})
    raise TypeError(f"not a chain type, struct or Option of one: {ty!r}")


def _require_ty(value: ChainValue, ty: object) -> None:
    """Raise `AbiCheckFailed` unless `value`'s tag family is one `ty` accepts.

    The tier-1 twin of the emitter's `narrow_to` on a host result: both answer
    "is this value really the type the program says it is?", both answer it at
    tag level, and both fail with `CODE_ABI_CHECK_FAILED`, so the two tiers name
    one failure (ruling E8).
    """
    family = tag_of_chain_value(value)
    accepted = _families_of_ty(ty)
    if family not in accepted:
        name = getattr(ty, "__name__", repr(ty))
        raise AbiCheckFailed(f"stored value is a {family}, not a {name}")


def _require_frame() -> None:
    """The seam the deploy/frame gate hangs on. A no-op today.

    A tier-1 run can reach a state the chain cannot -- calling an export before
    the constructor ran, or authorizing outside any invocation frame -- and
    refusing that is the cheapest structural guard the model has. The refusal
    itself belongs with the deploy/invoke helpers that know when a frame is
    open; this hook is where they attach, so the bodies below do not have to
    move when they land.
    """


class Event:
    """Base class for `@contractevent` types.

    A decorator cannot add a member that a type checker can see, so `publish`
    lives on a real base class that event types inherit -- that is what makes
    `Transfer(...).publish(env)` type-check under `mypy --strict`.
    `@contractevent` requires this base.
    """

    __slots__ = ()

    def publish(self, env: Env) -> None:
        """Emit this event via the host's `contract_event`."""
        raise NotImplementedError("sub-plan E")


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
    """

    __slots__ = ("_store",)

    #: Set by each subclass from `STORAGE_TYPE`.
    _DURABILITY: ClassVar[int]
    _DURABILITY_NAME: ClassVar[str]

    def __init__(self, store: _Store) -> None:
        self._store = store

    def _entry_key(self, key: ChainValue) -> _StoreKey:
        return (self._DURABILITY, storage_key(key))

    def get(self, key: ChainValue, ty: type[_T], default: _T | None = None) -> _T:
        """Read `key`, decoding it as `ty`.

        `ty` is passed explicitly because the host returns an untyped `Val`;
        it is what tells both the compiler and the type checker what comes
        back. Without a `default`, a missing key is a contract error.

        The model's three rules, each mirroring the compiled form:

        * a hit returns a DEEP COPY, so a caller mutating the result cannot
          reach back into the store;
        * a miss with no `default` raises `MissingValue`, whose
          `CODE_MISSING_VALUE` is the code the emitter's own guard emits;
        * a hit is TAG-CHECKED against `ty` (`_require_ty`), failing with
          `AbiCheckFailed` exactly where the emitter's narrow check fails.

        A miss WITH a `default` returns that default as-is, un-copied and
        un-checked -- deliberately, because the compiled form is an `IfExp`
        whose `orelse` IS the default expression: no host call, no narrowing,
        and the caller's own object is the value of the whole expression at
        both tiers. (The frontend's escape analysis marks a container passed to
        `default=` accordingly.)
        """
        entry = self._entry_key(key)
        if entry not in self._store:
            if default is not None:
                return default
            raise MissingValue(f"no {self._DURABILITY_NAME} storage entry for {key!r}")
        stored = self._store[entry]
        _require_ty(stored, ty)
        return cast("_T", copy.deepcopy(stored))

    def set(self, key: ChainValue, value: ChainValue) -> None:
        """Write `value` under `key`.

        Stores a DEEP COPY (ruling E5). The host serializes a `Val` into the
        ledger entry, so a later mutation of the caller's object cannot change
        what was written; a model that stored the reference would report the
        mutation from storage and diverge silently. The key is normalized to a
        `storage_key` here and now, so mutating the key object afterwards
        cannot move the entry either.
        """
        self._store[self._entry_key(key)] = copy.deepcopy(value)

    def has(self, key: ChainValue) -> Bool:
        """Whether `key` is present.

        Returns the chain `Bool` the host hands back, not a Python `bool`, so
        the value stays a chain value all the way through; `Bool` is truthy in
        an `if` statement.
        """
        return Bool(self._entry_key(key) in self._store)

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
        """
        self._store.pop(self._entry_key(key), None)


class InstanceStorage(_StorageBucket):
    """Instance storage: lives and dies with the contract instance.

    All instance entries share one TTL with the contract instance itself, so
    `extend_ttl` takes no key.
    """

    __slots__ = ()

    _DURABILITY: ClassVar[int] = STORAGE_TYPE["instance"]
    _DURABILITY_NAME: ClassVar[str] = "instance"

    def extend_ttl(self, threshold: U32, extend_to: U32) -> None:
        """Extend the instance's TTL to `extend_to` if it falls below
        `threshold` ledgers remaining."""
        raise NotImplementedError("sub-plan E")


class PersistentStorage(_StorageBucket):
    """Persistent storage: archived when its TTL lapses, restorable."""

    __slots__ = ()

    _DURABILITY: ClassVar[int] = STORAGE_TYPE["persistent"]
    _DURABILITY_NAME: ClassVar[str] = "persistent"

    def extend_ttl(self, key: ChainValue, threshold: U32, extend_to: U32) -> None:
        """Extend `key`'s TTL to `extend_to` if it falls below `threshold`
        ledgers remaining."""
        raise NotImplementedError("sub-plan E")


class TemporaryStorage(_StorageBucket):
    """Temporary storage: deleted outright when its TTL lapses."""

    __slots__ = ()

    _DURABILITY: ClassVar[int] = STORAGE_TYPE["temporary"]
    _DURABILITY_NAME: ClassVar[str] = "temporary"

    def extend_ttl(self, key: ChainValue, threshold: U32, extend_to: U32) -> None:
        """Extend `key`'s TTL to `extend_to` if it falls below `threshold`
        ledgers remaining."""
        raise NotImplementedError("sub-plan E")


class Storage:
    """`env.storage()`: the three durabilities, each its own bucket type."""

    __slots__ = ("_store",)

    def __init__(self, store: _Store) -> None:
        self._store = store

    def instance(self) -> InstanceStorage:
        return InstanceStorage(self._store)

    def persistent(self) -> PersistentStorage:
        return PersistentStorage(self._store)

    def temporary(self) -> TemporaryStorage:
        return TemporaryStorage(self._store)


class Ledger:
    """`env.ledger()`: read-only facts about the ledger being applied."""

    __slots__ = ("_sequence", "_timestamp")

    def __init__(self, timestamp: int, sequence: int) -> None:
        self._timestamp = timestamp
        self._sequence = sequence

    def timestamp(self) -> U64:
        """Seconds since the Unix epoch, as the host reports it.

        `U64`, not `Timepoint`: the host's `get_ledger_timestamp` returns a
        `U64Val`, and serpent does not silently reinterpret an `ScVal` case.
        """
        return U64(self._timestamp)

    def sequence(self) -> U32:
        """The sequence number of the ledger being applied."""
        return U32(self._sequence)


class Events:
    """`env.events()`: the contract event sink."""

    __slots__ = ("_published",)

    def __init__(self, published: list[PublishedEvent]) -> None:
        self._published = published

    def publish(self, topics: tuple[ChainValue, ...], data: ChainValue) -> None:
        """Emit an event.

        `topics` is a heterogeneous tuple, not a homogeneous `Vec`: the
        canonical Soroban shape is `(Symbol, Address, Address)` -- an event
        name followed by the addresses it concerns. `topics[0]` is
        conventionally a short `Symbol` naming the event; the host does not
        enforce that, but indexers and RPC filtering assume it.

        The model DOES enforce it, with `BadArgument`, and that is a
        tier-1-only reject: the frontend already refuses all three shapes at
        compile time (`SPT1038` for an empty topic tuple, `SPT3019` for a first
        topic that is not a `Symbol` or is longer than the 9-character short
        bound), so no compiled contract can reach these raises, and a
        hand-written tier-1 call gets the same answer the compiler would have
        given. The host itself enforces none of it.

        The recorded event is a SNAPSHOT (ruling E5): a later mutation of the
        topics or the data cannot change what was published, exactly as on
        chain, where `contract_event` serializes them.
        """
        if not topics:
            raise BadArgument("an event needs at least one topic, naming it")
        name = topics[0]
        if not isinstance(name, Symbol):
            raise BadArgument(
                f"topics[0] must be a Symbol naming the event, not a {type(name).__name__}"
            )
        if not val.fits_symbol_small(name.text):
            raise BadArgument(
                f"topics[0] must be a short Symbol (at most 9 characters): {name.text!r}"
            )
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
    """

    __slots__ = ("_auths", "_events", "_recorded_auths", "_sequence", "_store", "_timestamp")

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
        self._sequence = sequence

    def storage(self) -> Storage:
        _require_frame()
        return Storage(self._store)

    def ledger(self) -> Ledger:
        _require_frame()
        return Ledger(self._timestamp, self._sequence)

    def events(self) -> Events:
        _require_frame()
        return Events(self._events)

    # --- test-facing inspection (NOT `serpent.__all__` names) ----------------

    @property
    def published_events(self) -> tuple[PublishedEvent, ...]:
        """Every event published through this `Env`, in order, as snapshots.

        Deliberately not in `serpent.__all__`: `published_events` is a test
        surface, and `serpent.__all__` is the AUTHORING surface a contract
        resolves names against.

        No frame rollback (module docstring): an event published by a method
        that then raises is still here.
        """
        return tuple(self._events)

    @property
    def recorded_auths(self) -> tuple[RecordedAuth, ...]:
        """Every authorization asked for through this `Env`, in order.

        Recording IS the auth model (mock-all-auths): the host's real
        authorization trees -- nonces written to storage, sub-invocation trees,
        signature verification -- are not modelled anywhere in this repo.
        """
        return tuple(self._recorded_auths)
