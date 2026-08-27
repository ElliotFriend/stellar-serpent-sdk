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

from typing import TypeVar

from serpent.types import U32, U64, Bool, Symbol, Vec

__all__ = [
    "Env",
    "Events",
    "InstanceStorage",
    "Ledger",
    "PersistentStorage",
    "Storage",
    "TemporaryStorage",
]

_T = TypeVar("_T")


class _StorageBucket:
    """The operations every storage durability shares.

    Keys are `Symbol`s: the storage key of a contract data entry is an
    arbitrary `Val`, but serpent's authoring model fixes it to a Symbol so
    that keys are readable in ledger dumps and cheap on the small-value path.
    """

    __slots__ = ()

    def get(self, key: Symbol, ty: type[_T], default: _T | None = None) -> _T:
        """Read `key`, decoding it as `ty`.

        `ty` is passed explicitly because the host returns an untyped `Val`;
        it is what tells both the compiler and the type checker what comes
        back. Without a `default`, a missing key is a contract error.
        """
        raise NotImplementedError("sub-plan E")

    def set(self, key: Symbol, value: object) -> None:
        """Write `value` under `key`."""
        raise NotImplementedError("sub-plan E")

    def has(self, key: Symbol) -> Bool:
        """Whether `key` is present.

        Returns the chain `Bool` the host hands back, not a Python `bool`, so
        the value stays a chain value all the way through; `Bool` is truthy in
        an `if` statement.
        """
        raise NotImplementedError("sub-plan E")

    def del_(self, key: Symbol) -> None:
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

    def extend_ttl(self, key: Symbol, threshold: U32, extend_to: U32) -> None:
        """Extend `key`'s TTL to `extend_to` if it falls below `threshold`
        ledgers remaining."""
        raise NotImplementedError("sub-plan E")


class TemporaryStorage(_StorageBucket):
    """Temporary storage: deleted outright when its TTL lapses."""

    __slots__ = ()

    def extend_ttl(self, key: Symbol, threshold: U32, extend_to: U32) -> None:
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

    def publish(self, topics: Vec[Symbol], data: object) -> None:
        """Emit an event. `topics[0]` is conventionally a short `Symbol`
        naming the event -- the host does not enforce it, but indexers and
        RPC filtering assume it."""
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
