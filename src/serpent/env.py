"""The `Env` authoring surface: the shape of the host, and nothing else.

Every contract method takes an `Env` and reaches the host through it --
`env.storage().instance().get(...)`, `env.ledger().timestamp()`,
`env.events().publish(...)`. That chain is what sub-plan C compiles into host
function calls, and what sub-plan E backs with a real host at test time.

This module is **pure type surface**: every method is fully annotated with
chain types and every body raises `NotImplementedError("sub-plan E")`. It
exists now so that contracts, IDEs and `mypy --strict` can already see the
complete shape, and so that Task 10's strict-typed fixture compiles.

Storage buckets are three distinct types rather than one parameterized bucket
because their TTL operations genuinely differ: an instance entry's TTL covers
the whole instance and is extended without a key, while persistent and
temporary entries are extended per key. Typing them separately means the
wrong call is a type error, not a runtime surprise.
"""

from __future__ import annotations

from typing import Any, ClassVar, Protocol, TypeAlias, TypeVar, runtime_checkable

from serpent.types import U32, U64, Bool, Map, Vec
from serpent.types._base import _ChainValue

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


@runtime_checkable
class Struct(Protocol):
    """Structurally, any `@contracttype` instance.

    `@contracttype` is a `dataclass_transform`, so a decorated class is a
    dataclass to the type checker and carries `__dataclass_fields__`. Matching
    on that is what lets `ChainValue` admit user structs without every struct
    needing a common base class -- a decorator cannot add one that a checker
    would see.
    """

    __dataclass_fields__: ClassVar[dict[str, Any]]


#: Everything that can cross the host boundary as a value: any scalar chain
#: type (`_ChainValue` is the shared base of `Bool`/`U32`/.../`Symbol`/
#: `Bytes`/`Address`), the containers, or a `@contracttype` struct.
#:
#: This is deliberately a closed union rather than `object`: a raw `str` or
#: `int` key is a static error, which is the whole point of the chain types.
ChainValue: TypeAlias = _ChainValue[Any] | Vec[Any] | Map[Any, Any] | Struct


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
    """

    __slots__ = ()

    def get(self, key: ChainValue, ty: type[_T], default: _T | None = None) -> _T:
        """Read `key`, decoding it as `ty`.

        `ty` is passed explicitly because the host returns an untyped `Val`;
        it is what tells both the compiler and the type checker what comes
        back. Without a `default`, a missing key is a contract error.
        """
        raise NotImplementedError("sub-plan E")

    def set(self, key: ChainValue, value: ChainValue) -> None:
        """Write `value` under `key`."""
        raise NotImplementedError("sub-plan E")

    def has(self, key: ChainValue) -> Bool:
        """Whether `key` is present.

        Returns the chain `Bool` the host hands back, not a Python `bool`, so
        the value stays a chain value all the way through; `Bool` is truthy in
        an `if` statement.
        """
        raise NotImplementedError("sub-plan E")

    def del_(self, key: ChainValue) -> None:
        """Delete `key`. Named `del_` because `del` is a Python keyword."""
        raise NotImplementedError("sub-plan E")


class InstanceStorage(_StorageBucket):
    """Instance storage: lives and dies with the contract instance.

    All instance entries share one TTL with the contract instance itself, so
    `extend_ttl` takes no key.
    """

    __slots__ = ()

    def extend_ttl(self, threshold: U32, extend_to: U32) -> None:
        """Extend the instance's TTL to `extend_to` if it falls below
        `threshold` ledgers remaining."""
        raise NotImplementedError("sub-plan E")


class PersistentStorage(_StorageBucket):
    """Persistent storage: archived when its TTL lapses, restorable."""

    __slots__ = ()

    def extend_ttl(self, key: ChainValue, threshold: U32, extend_to: U32) -> None:
        """Extend `key`'s TTL to `extend_to` if it falls below `threshold`
        ledgers remaining."""
        raise NotImplementedError("sub-plan E")


class TemporaryStorage(_StorageBucket):
    """Temporary storage: deleted outright when its TTL lapses."""

    __slots__ = ()

    def extend_ttl(self, key: ChainValue, threshold: U32, extend_to: U32) -> None:
        """Extend `key`'s TTL to `extend_to` if it falls below `threshold`
        ledgers remaining."""
        raise NotImplementedError("sub-plan E")


class Storage:
    """`env.storage()`: the three durabilities, each its own bucket type."""

    __slots__ = ()

    def instance(self) -> InstanceStorage:
        raise NotImplementedError("sub-plan E")

    def persistent(self) -> PersistentStorage:
        raise NotImplementedError("sub-plan E")

    def temporary(self) -> TemporaryStorage:
        raise NotImplementedError("sub-plan E")


class Ledger:
    """`env.ledger()`: read-only facts about the ledger being applied."""

    __slots__ = ()

    def timestamp(self) -> U64:
        """Seconds since the Unix epoch, as the host reports it.

        `U64`, not `Timepoint`: the host's `get_ledger_timestamp` returns a
        `U64Val`, and serpent does not silently reinterpret an `ScVal` case.
        """
        raise NotImplementedError("sub-plan E")

    def sequence(self) -> U32:
        """The sequence number of the ledger being applied."""
        raise NotImplementedError("sub-plan E")


class Events:
    """`env.events()`: the contract event sink."""

    __slots__ = ()

    def publish(self, topics: tuple[ChainValue, ...], data: ChainValue) -> None:
        """Emit an event.

        `topics` is a heterogeneous tuple, not a homogeneous `Vec`: the
        canonical Soroban shape is `(Symbol, Address, Address)` -- an event
        name followed by the addresses it concerns. `topics[0]` is
        conventionally a short `Symbol` naming the event; the host does not
        enforce that, but indexers and RPC filtering assume it.
        """
        raise NotImplementedError("sub-plan E")


class Env:
    """The host, as a contract sees it.

    The compiler injects the real `Env`; a contract never constructs one, and
    every method here raises until sub-plan E lands the host bridge.
    """

    __slots__ = ()

    def storage(self) -> Storage:
        raise NotImplementedError("sub-plan E")

    def ledger(self) -> Ledger:
        raise NotImplementedError("sub-plan E")

    def events(self) -> Events:
        raise NotImplementedError("sub-plan E")
