"""`storage_key` -- ONE definition of storage/map key equality across tiers (S13).

`tests/harness/objects.py`'s module docstring documents the motivating bug: a
map key and a storage key are compared by the host **structurally**, by value,
never by handle -- a struct storage key
(`BalanceKey(owner=Address(...))`, the dominant real-world shape) is rebuilt
fresh on every contract invocation, so a store that keyed on Python object
identity or a handle word would file a write under one key and look it up
under another, value-equal one, and silently miss. `objects.py`'s `map_key`
solves this at the word level, decoding a `Val` and normalizing it; this module
is its VALUE-level twin, taking a `serpent.types` chain value directly (tier 1
never sees a `Val` word at all).

Normalization rules (review B7-corrected):

* a scalar (numeric, `Bool`, `Symbol`, `String`, the `Bytes` family, `Address`,
  `Timepoint`, `Duration`) normalizes to `(value._SCVAL_RANK,
  value._cmp_payload())` -- the same ordering surface `types._ordering.val_cmp`
  reads structurally, so two equal scalars always agree here too;
* a `Vec` normalizes to `("vec", tuple(storage_key(el) for el in ...))` over
  its elements IN ORDER -- a vec key is order-sensitive, unlike a map;
* a `Map` normalizes to `("map", frozenset((storage_key(k), storage_key(v))
  for k, v in ...))` over its ITEMS, key AND value. Iterating a `Map` directly
  yields keys only (`Map.__iter__`), and normalizing on keys alone would
  collapse two maps that agree on every key but disagree on some value -- a
  real, silent bug this rig must not reproduce (review B7);
* a `@contracttype` struct instance normalizes IDENTICALLY to its equivalent
  field-name-keyed `Map`: `("map", frozenset((storage_key(Symbol(field_name)),
  storage_key(field_value)) for each field))`. A struct and its equivalent map
  ARE the same on-chain value (S9, `MapObject` either way), and this mirrors
  `objects.py:436-441`'s word-level struct/map convergence at the value level.

Chain values are acyclic by construction -- a `Vec`/`Map`/struct built from
values built from values cannot contain itself, there being no way to name an
in-progress construction from within it -- so `storage_key` recurses with no
cycle guard.
"""

from collections.abc import Hashable

from serpent.types._ordering import ContainerValue, Struct
from serpent.types.containers import Map, Vec
from serpent.types.symbol import Symbol

__all__ = ["storage_key"]


def storage_key(value: ContainerValue) -> Hashable:
    """The value-equal, hashable key one chain value (or struct) stands for.

    Two calls on structurally-equal-but-distinct values (two fresh struct
    instances built the same way, a `Vec`/`Map` built in a different order)
    always compare equal and hash equal; two calls on differing values never
    do (barring an accidental collision in the payload space, exactly as for
    any hash-based key).
    """
    if isinstance(value, Struct):
        return _struct_key(value)
    if isinstance(value, Vec):
        return ("vec", tuple(storage_key(element) for element in value))
    if isinstance(value, Map):
        # `Map.__iter__` yields keys only (review B7), so the value for each
        # is fetched explicitly with `.get` -- iterating `value` alone would
        # normalize on keys and collapse two maps that differ only in a value.
        return (
            "map",
            frozenset((storage_key(key), storage_key(value.get(key))) for key in value),
        )
    return (value._SCVAL_RANK, value._cmp_payload())


def _struct_key(value: Struct) -> Hashable:
    """A struct's key: the SAME shape its equivalent field-keyed `Map` gets."""
    field_names = type(value).__dataclass_fields__.keys()
    return (
        "map",
        frozenset(
            (storage_key(Symbol(name)), storage_key(getattr(value, name))) for name in field_names
        ),
    )
