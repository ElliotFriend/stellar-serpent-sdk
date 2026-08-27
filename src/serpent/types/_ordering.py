"""`val_cmp` -- serpent's model of the host's cross-type value ordering.

The total order is `(ScValType rank, then the within-type payload)`:

* **`ScValType` rank, NOT tag rank.** The host's `obj_cmp` falls back to
  `Tag::get_scval_type().cmp()`, and its object-vs-object discriminant is
  deliberately kept in `ScVal` order. Tag order gives the WRONG answer -- for
  example `SymbolSmall` is tag 14 while `BytesObject` is tag 72, yet the host
  sorts `Bytes` (ScValType 13) before `Symbol` (ScValType 15). Verified against
  `compare.rs`/`host.rs`/`comparison.rs` @ v28.0.2.
* **Partial model of host `obj_cmp`, differential-validated in sub-plans D/F;
  extending the supported set requires extending the differential tests.**

This module imports NOTHING from `serpent.types`: it reads `_SCVAL_RANK` and
`_cmp_payload()` structurally off the values it is given. That keeps it free of
import cycles and makes every new chain type (Task 8's `Address`, later the
containers' nested ordering) a purely additive change.

That same freedom from imports is why `Struct` -- the structural view of a
`@contracttype` instance -- is declared HERE rather than in `serpent.env`,
which used to own the only copy: `containers.py` cannot import `env.py` (env
imports the containers), so a second, identical Protocol would otherwise have
to exist for M1-C's `Vec[Settings]`/`Map[Symbol, Settings]` widening (E2/MJ-7).
`env.Struct` now re-exports this one, so there is exactly one definition.
"""

from typing import Any, ClassVar, Protocol, TypeAlias, runtime_checkable


@runtime_checkable
class ChainValue(Protocol):
    """Structural view of a chain value, as `val_cmp` needs it.

    Every chain type declares `_SCVAL_RANK` (the host's `ScValType` order) and
    returns its comparable payload from `_cmp_payload()`.
    """

    _SCVAL_RANK: ClassVar[int]

    def _cmp_payload(self) -> object: ...


@runtime_checkable
class Struct(Protocol):
    """Structurally, any `@contracttype` instance.

    `@contracttype` is a `dataclass_transform`, so a decorated class is a
    dataclass to the type checker and carries `__dataclass_fields__`. Matching
    on that is what lets a container's element/value bound admit user structs
    without every struct needing a common base class -- a decorator cannot add
    one that a checker would see.

    Deliberately NOT a `ChainValue`: a struct has no `_SCVAL_RANK` and no
    `_cmp_payload`, so tier 1 cannot ORDER one (A15 forbids inventing an order
    the host has not been differentially verified against). It is admitted
    exactly where ordering is never asked for -- `Vec` elements and `Map`
    VALUES -- and refused where it is (`Map` keys, per E3).
    """

    __dataclass_fields__: ClassVar[dict[str, Any]]


#: What a `Vec` element or a `Map` VALUE may be (ruling E2 (b)): any chain
#: value, or a `@contracttype` struct. `Map`'s KEY bound stays `ChainValue`
#: (E3/MJ-7) -- every key is compared by the binary search, and a struct
#: cannot be.
ContainerValue: TypeAlias = "ChainValue | Struct"


def _rank_of(value: ChainValue) -> int:
    rank = getattr(type(value), "_SCVAL_RANK", None)
    if not isinstance(rank, int):
        raise TypeError(f"not a chain value (no _SCVAL_RANK): {value!r}")
    return rank


def require_chain_value(value: ChainValue) -> None:
    """Raise `TypeError` unless `value` is a chain value.

    The same check `val_cmp` applies, available on its own for callers that must
    reject a non-chain value *before* any comparison happens -- `Map`, whose
    binary search never calls `val_cmp` on an empty map and would otherwise let
    an uncomparable key in.
    """
    _rank_of(value)
    if not callable(getattr(value, "_cmp_payload", None)):
        raise TypeError(f"not a chain value (no _cmp_payload): {value!r}")


def require_map_value(value: ContainerValue) -> None:
    """Raise `TypeError` unless `value` may be a `Map` VALUE (ruling MJ-7).

    The value path is the widened one: a chain value OR a `@contracttype`
    struct. That is sound because a `Map`'s values are never ordered -- only
    its keys go through `val_cmp` -- so admitting a value tier 1 cannot
    compare costs nothing, while admitting such a KEY would break the binary
    search (which is why `_search` keeps calling `require_chain_value`, E3).

    Everything else -- a raw `int`, `str`, `None`, a list, a bare `object` --
    is still refused on the way in, at every map size, with
    `require_chain_value`'s own message.
    """
    if isinstance(value, Struct):
        return
    require_chain_value(value)


def val_cmp(a: ChainValue, b: ChainValue) -> int:
    """Three-way compare two chain values: negative, zero or positive.

    Cross-type comparisons are answered by `ScValType` rank alone, so two values
    of different types never have their payloads compared (which is what lets a
    container -- whose payload comparison is still deferred -- be ordered
    against any scalar). Within one type, the payloads decide.

    Raises `TypeError` for anything that is not a chain value, and whatever the
    payload hook raises for types whose within-type ordering is not modelled yet
    (`Vec`/`Map` raise `NotImplementedError`).
    """
    rank_a = _rank_of(a)
    rank_b = _rank_of(b)
    if rank_a != rank_b:
        return -1 if rank_a < rank_b else 1

    payload_a = a._cmp_payload()
    payload_b = b._cmp_payload()
    if isinstance(payload_a, int) and isinstance(payload_b, int):
        # bool is an int: False < True, which is the host's Bool order.
        if payload_a == payload_b:
            return 0
        return -1 if payload_a < payload_b else 1
    if isinstance(payload_a, bytes) and isinstance(payload_b, bytes):
        if payload_a == payload_b:
            return 0
        return -1 if payload_a < payload_b else 1
    raise TypeError(
        "val_cmp does not model this payload comparison: "
        f"{type(payload_a).__name__} vs {type(payload_b).__name__}"
    )
