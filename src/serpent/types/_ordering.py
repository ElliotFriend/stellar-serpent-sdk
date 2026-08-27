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
"""

from typing import ClassVar, Protocol


class ChainValue(Protocol):
    """Structural view of a chain value, as `val_cmp` needs it.

    Every chain type declares `_SCVAL_RANK` (the host's `ScValType` order) and
    returns its comparable payload from `_cmp_payload()`.
    """

    _SCVAL_RANK: ClassVar[int]

    def _cmp_payload(self) -> object: ...


def _rank_of(value: ChainValue) -> int:
    rank = getattr(type(value), "_SCVAL_RANK", None)
    if not isinstance(rank, int):
        raise TypeError(f"not a chain value (no _SCVAL_RANK): {value!r}")
    return rank


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
