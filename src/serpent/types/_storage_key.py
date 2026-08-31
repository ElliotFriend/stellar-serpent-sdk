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
* a `ContractUnion` value normalizes to the key of the `ScVec` it IS on chain
  (§B.1: `ScVec[Symbol(case), payload...]`), by DELEGATING to the `Vec` it
  holds -- so a union key and the equivalent hand-built vec key are one value,
  and an `ScVec`'s order-sensitivity applies to a variant payload for free. A
  `ContractEnum` value gets no rule of its own: it IS a bare `U32`, so it is
  answered by the scalar line;
* a `@contracttype` struct instance normalizes IDENTICALLY to its equivalent
  field-name-keyed `Map`: `("map", frozenset((storage_key(Symbol(field_name)),
  storage_key(field_value)) for each field))`. A struct and its equivalent map
  ARE the same on-chain value (S9, `MapObject` either way), and this mirrors
  `objects.py:436-441`'s word-level struct/map convergence at the value level.
* `None` -- the value an `X | None` struct field holds when absent, a
  legitimate on-chain map value, not an authoring error -- normalizes to
  `(_VOID_RANK,)` (review M2): `Void`'s own A8 `ScValType` rank (1, between
  `Bool` at 0 and `Error` at 2), scalar-shaped but with no payload because
  `Void` carries none. `serpent.types` has no `Void` class to hand
  `_SCVAL_RANK`/`_cmp_payload()` off to, so this is the one case
  `storage_key` special-cases directly instead of reading them structurally.

Chain values are acyclic by construction -- a `Vec`/`Map`/struct built from
values built from values cannot contain itself, there being no way to name an
in-progress construction from within it -- so `storage_key` recurses with no
cycle guard.
"""

from __future__ import annotations

from collections.abc import Hashable

from serpent.types._ordering import ContainerValue, Struct
from serpent.types._udt import ContractUnion
from serpent.types.containers import Map, Vec
from serpent.types.symbol import Symbol

__all__ = ["storage_key"]

#: `Void`'s A8 `ScValType` rank (`SCV_VOID = 1` in the host's XDR enum,
#: between `SCV_BOOL = 0` and `SCV_ERROR = 2` -- `Bool._SCVAL_RANK` and
#: `Address._SCVAL_RANK` pin the same table's ends at 0 and 18). No
#: `serpent.types` class models `Void` (A9), so this is spelled here rather
#: than read off a class the way every other rank in this module is.
_VOID_RANK = 1


def storage_key(value: ContainerValue | None) -> Hashable:
    """The value-equal, hashable key one chain value (`None` for Void, or a
    struct) stands for.

    Two calls on structurally-equal-but-distinct values (two fresh struct
    instances built the same way, a `Vec`/`Map` built in a different order)
    always compare equal and hash equal; two calls on differing values never
    do (barring an accidental collision in the payload space, exactly as for
    any hash-based key).
    """
    if value is None:
        return (_VOID_RANK,)
    if isinstance(value, ContractUnion):
        # DELEGATED to the `ScVec` a union IS on chain (§B.1), so the vec shape
        # has one definition and not a second copy. Placed above the `Struct`
        # arm as belt and braces: neither new kind is a dataclass (ruling E9),
        # so the order is not load-bearing today, and
        # `test_storage_key.py::test_a_union_is_never_keyed_as_a_map` pins it.
        # An int enum needs no arm at all -- it IS a bare `U32`, so it falls
        # through to the scalar line below.
        return storage_key(value._vec)
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
    """A struct's key: the SAME shape its equivalent field-keyed `Map` gets.

    `getattr(value, name)` for each of `__dataclass_fields__` rather than
    walking the class's annotations directly: safe because `@contracttype`
    (`decorators._build_record`) rejects `ClassVar`/`InitVar` annotations
    along with every non-chain one, so every name in `__dataclass_fields__` is
    a real per-instance field `dataclasses` has already validated -- there is
    no pseudo-field here that `getattr` could return something misleading for.
    """
    field_names = type(value).__dataclass_fields__.keys()
    return (
        "map",
        frozenset(
            (storage_key(Symbol(name)), storage_key(getattr(value, name))) for name in field_names
        ),
    )
