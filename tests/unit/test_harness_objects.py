"""Tier-2a productization, scoped (ruling E7): the build cache and the typed
container codec that discharge sub-plan F's two carried debts (O1, O4, O5, O8,
E15).

Three things, and nothing else (O8: no new mock semantics):

1. `tests/harness/cache.py: built(path)` -- `build_file(path)` memoised on
   `(resolved path, sha256 of the file text)`, so the ~60-row differential
   tables (`test_env_differential.py`, `test_emitter_end_to_end.py`) rebuild a
   changed fixture and compile an unchanged one once per session (the HOST is
   never cached -- only the bytes, C7's rule).
2. `ObjectStore.chain_value_as(word, ty)` -- the public typed decoder that
   replaces `test_examples.py`'s reach into the private `host._vec` (O4): a
   chain scalar class, `Vec[T]`, `Map[K, V]`, a `ContractUnion`/`ContractEnum`
   subclass, or a `@contracttype` class, walked recursively through the store.
   `val_word` gains the matching encoders (O5), so a `Call` argument may be a
   container, a union, an enum member, or a struct on the mini leg too.
3. `i256.DIV_ERROR_VAL` renamed to the discriminant pair the real host actually
   reported for a 128-bit `//0` (E15): `tests/semantics/host_facts.py`'s
   `DIV128_BY_ZERO_HOST_ERROR.underlying`, `("Object", "ArithDomain")`. The
   test below derives both XDR discriminants from that pair through
   `stellar_sdk.xdr.SCErrorType`/`SCErrorCode`'s own enum member names, so the
   module constant cannot silently drift from the pinned fact.

None of this touches `obj_cmp`'s container comparison (still `NotImplementedError`,
O8) or invents a TTL/auth/footprint model -- both are out of scope by ruling E7.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from stellar_sdk.xdr import SCErrorCode, SCErrorType

from serpent import (
    U32,
    ContractEnum,
    ContractUnion,
    Symbol,
    contractenum,
    contracttype,
    contractunion,
    enumvalue,
    val,
    variant,
)
from serpent.types import Map, Vec
from tests.harness import cache
from tests.harness.i256 import DIV_ERROR_VAL
from tests.harness.objects import ObjectStore
from tests.semantics.host_facts import DIV128_BY_ZERO_HOST_ERROR
from tests.unit.test_emitter_end_to_end import EXAMPLE_COUNTER

# --- fixtures for the typed decoder/encoder ---------------------------------


@contractunion
class Shape(ContractUnion):
    """A unit case and a tuple case -- the two on-chain shapes a union has."""

    Empty = variant()
    Rect = variant(U32, U32)


@contractenum
class Color(ContractEnum):
    Red = enumvalue(0)
    Green = enumvalue(1)


@contracttype
class Point:
    x: U32
    y: U32


# --- chain_value_as / val_word: containers, union, enum, struct ------------


def test_chain_value_as_round_trips_a_vec() -> None:
    store = ObjectStore()
    value = Vec(U32, [U32(1), U32(2), U32(3)])
    word = store.val_word(value)
    assert store.chain_value_as(word, Vec[U32]) == value


def test_chain_value_as_round_trips_a_map() -> None:
    store = ObjectStore()
    value = Map(Symbol, U32, [(Symbol("a"), U32(1)), (Symbol("b"), U32(2))])
    word = store.val_word(value)
    assert store.chain_value_as(word, Map[Symbol, U32]) == value


def test_chain_value_as_round_trips_a_union_case_with_payload() -> None:
    store = ObjectStore()
    value = Shape.Rect(U32(3), U32(4))
    word = store.val_word(value)
    assert store.chain_value_as(word, Shape) == value


def test_chain_value_as_round_trips_a_unit_case() -> None:
    store = ObjectStore()
    value = Shape.Empty
    word = store.val_word(value)
    assert store.chain_value_as(word, Shape) == value


def test_chain_value_as_round_trips_an_enum_member() -> None:
    store = ObjectStore()
    value = Color.Green
    word = store.val_word(value)
    assert store.chain_value_as(word, Color) == value


def test_chain_value_as_round_trips_a_struct() -> None:
    store = ObjectStore()
    value = Point(x=U32(1), y=U32(2))
    word = store.val_word(value)
    assert store.chain_value_as(word, Point) == value


def test_chain_value_as_on_a_symbol_word_requesting_u32_raises() -> None:
    """A9's convention: a mismatch is a loud `AssertionError`, never a guess."""
    store = ObjectStore()
    word = store.val_word(Symbol("nope"))
    with pytest.raises(AssertionError):
        store.chain_value_as(word, U32)


# --- DIV_ERROR_VAL, derived rather than restated ---------------------------


def _shout(name: str) -> str:
    """CamelCase -> SCREAMING_SNAKE_CASE: `"ArithDomain"` -> `"ARITH_DOMAIN"`.

    An underscore goes before every uppercase letter that follows a lowercase
    one -- the same boundary rule `decorators._snake_case` uses, upper-cased.
    """
    out: list[str] = []
    for index, char in enumerate(name):
        if index and char.isupper() and name[index - 1].islower():
            out.append("_")
        out.append(char.upper())
    return "".join(out)


def test_div_error_val_is_derived_from_the_pinned_host_fact() -> None:
    """`i256.DIV_ERROR_VAL` must equal the discriminant pair the real host
    reported for a 128-bit `//0` -- `DIV128_BY_ZERO_HOST_ERROR.underlying`,
    `("Object", "ArithDomain")` -- so this derives both XDR discriminants from
    that pair via `stellar_sdk.xdr.SCErrorType`/`SCErrorCode`'s own member
    names (`"Object"` -> `SCE_OBJECT`, `"ArithDomain"` -> `SCEC_ARITH_DOMAIN`)
    rather than restating them as a second, driftable literal.
    """
    underlying = DIV128_BY_ZERO_HOST_ERROR.underlying
    assert underlying is not None
    error_type_name, error_code_name = underlying
    error_type = SCErrorType["SCE_" + _shout(error_type_name)]
    error_code = SCErrorCode["SCEC_" + _shout(error_code_name)]
    assert DIV_ERROR_VAL == val.error_val(int(error_code), int(error_type))


# --- cache.built --------------------------------------------------------------


def test_built_caches_an_unchanged_file_and_rebuilds_a_changed_one(tmp_path: Path) -> None:
    path = tmp_path / "counter.py"
    shutil.copyfile(EXAMPLE_COUNTER, path)

    first = cache.built(path)
    second = cache.built(path)
    assert first is second

    path.write_text(path.read_text() + "\n# a harmless trailing comment\n")
    third = cache.built(path)
    assert third is not first
