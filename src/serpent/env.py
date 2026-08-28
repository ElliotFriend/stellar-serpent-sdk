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


def _require_frame() -> None:
    """The seam the deploy/frame gate hangs on. A no-op today.

    A tier-1 run can reach a state the chain cannot -- calling an export before
    the constructor ran, or authorizing outside any invocation frame -- and
    refusing that is the cheapest structural guard the model has. The refusal
    itself belongs with the deploy/invoke helpers that know when a frame is
    open; this hook is where they attach, so the bodies below do not have to
    move when they land.
    """


class _TtlState:
    """The ledger sequence, and every live-until the model measures against it.

    ONE object, held by the `Env` and threaded into every bucket it hands out,
    for three reasons:

    * `Env.advance` must move exactly one copy of the sequence. A bucket that
      captured its own snapshot would keep answering `has` against the
      pre-advance ledger -- and a test holding a bucket across an `advance` is
      the normal way to write a TTL test;
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

    The `Env`'s `_TtlState` is threaded in beside the store, so a bucket can
    answer expiry against the live sequence rather than a snapshot of it.
    """

    __slots__ = ("_store", "_ttl")

    #: Set by each subclass from `STORAGE_TYPE`.
    _DURABILITY: ClassVar[int]
    _DURABILITY_NAME: ClassVar[str]

    def __init__(self, store: _Store, ttl: _TtlState) -> None:
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

    def get(self, key: ChainValue, ty: type[_T], default: _T | None = None) -> _T:
        """Read `key`, decoding it as `ty`.

        `ty` is passed explicitly because the host returns an untyped `Val`;
        it is what tells both the compiler and the type checker what comes
        back. Without a `default`, a missing key is a contract error.

        The model's three rules, each mirroring the compiled form:

        * a hit returns a DEEP COPY, so a caller mutating the result cannot
          reach back into the store;
        * a miss with no `default` raises `MissingValue`, whose
          `CODE_MISSING_VALUE` is the code the emitter's own guard emits. An
          EXPIRED entry is a miss (`_absent`): TTL expiry is answered lazily,
          here, rather than by sweeping the store on `advance`;
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
        if self._absent(entry):
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

        A write is a FRESH entry as far as TTL goes: its live-until goes back to
        `None`, so re-setting an expired persistent or temporary key revives it
        (the module docstring names that as a tier-1 convenience -- the chain
        would make you restore an archived entry first). `InstanceStorage`
        overrides the reset away, because its live-until is bucket-wide and one
        key's write cannot honestly resurrect the whole instance entry.
        """
        entry = self._entry_key(key)
        self._store[entry] = copy.deepcopy(value)
        self._forget_live_until(entry)

    def has(self, key: ChainValue) -> Bool:
        """Whether `key` is present -- and not expired (`_absent`).

        Returns the chain `Bool` the host hands back, not a Python `bool`, so
        the value stays a chain value all the way through; `Bool` is truthy in
        an `if` statement.
        """
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

    __slots__ = ("_store", "_ttl")

    def __init__(self, store: _Store, ttl: _TtlState) -> None:
        self._store = store
        self._ttl = ttl

    def instance(self) -> InstanceStorage:
        return InstanceStorage(self._store, self._ttl)

    def persistent(self) -> PersistentStorage:
        return PersistentStorage(self._store, self._ttl)

    def temporary(self) -> TemporaryStorage:
        return TemporaryStorage(self._store, self._ttl)


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

    __slots__ = ("_auths", "_events", "_recorded_auths", "_store", "_timestamp", "_ttl")

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

    def storage(self) -> Storage:
        _require_frame()
        return Storage(self._store, self._ttl)

    def ledger(self) -> Ledger:
        _require_frame()
        return Ledger(self._timestamp, self._ttl.sequence)

    def events(self) -> Events:
        _require_frame()
        return Events(self._events)

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
        record it just read and see its own mutation on the next read. (When
        Task 4 fills `recorded_auths`, it owes the same copy on the way out.)

        No frame rollback (module docstring): an event published by a method
        that then raises is still here.
        """
        return copy.deepcopy(tuple(self._events))

    @property
    def recorded_auths(self) -> tuple[RecordedAuth, ...]:
        """Every authorization asked for through this `Env`, in order.

        Recording IS the auth model (mock-all-auths): the host's real
        authorization trees -- nonces written to storage, sub-invocation trees,
        signature verification -- are not modelled anywhere in this repo.

        Empty until `require_auth`/`require_auth_for_args` have bodies. Whoever
        lands them owes the deep copy `published_events` makes, on the way in
        (the recorded args are a snapshot) and on the way out.
        """
        return tuple(self._recorded_auths)
