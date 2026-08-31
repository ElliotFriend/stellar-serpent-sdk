"""A functional mini-host for the OBJECT half of the pin: vecs, maps, blobs, storage.

`engine.MiniHost` binds host functions; this is what most of them *do*. Ported
in spirit -- and, for the one check that matters, **by copy** -- from
`spikes/spike1/harness.py:220-290` (R5: `spikes/` is read-only evidence, never
imported from).

## The ascending-key descriptor check is the point (F.1.5)

`map_new_from_linear_memory`'s keys array is `(u32 ptr, u32 len)` descriptors of
the key *name bytes*, and env.json says: "Actual keys must be byte strings
sorted in ascending order... **Panics if any of the invariants above are
violated.**" (P1). A harness that skipped that check would go green on an
emitter that stopped sorting struct fields, and the contract would panic only on
chain -- the one direction a rig like this must never be wrong in. So the check
is enforced here, and `test_emitter_lower_objects.py` keeps a negative control
that feeds it a DESCENDING blob and proves it still fires.

## Relative handle space (P1)

Value words read back out of guest memory are in *relative* handle space. With
one contract and one host there is nothing to translate: the guest writes the
same `Val` word it holds, and this store reads it back and uses it as-is --
which is exactly what the on-chain-verified spike does.

## What the objects ARE (A9, and where it bends)

Handles are indices into `objects`. `Symbol`, `String` and `Bytes` are stored as
the tier-1 `serpent.types` instances, so the oracle really is the model for
them. Vecs and maps are **not**: `serpent.types.Vec`/`Map` are statically typed
in their element/key/value classes, and a host handed `vec_new()` has no element
type to give them -- the host's own model of a vec is a sequence of untyped
`Val` words, so that is what is stored. Scalar `Val`s inside are still decoded
through `serpent.val`, the one codec.

## Keys are compared BY VALUE, never by handle (`map_key`)

A map key and a storage key are compared by the host **structurally** -- with
`obj_cmp`, i.e. by the value, not by the handle. Nothing about that is optional
here: a struct storage key (`BalanceKey(owner=Address(...))`, the dominant
real-world shape, `tests/fixtures/token_style.py`) is a `MapObject`, and the
contract builds a FRESH one on every invocation. A store that keyed on the
handle word would file `mint`'s write under handle 844 and then look `balance`
up under handle 901, find nothing, and return the storage default -- a
plausible number, silently wrong, from a module that validates and runs. That
is the exact failure mode this rig exists to catch, so `map_key` normalizes
every key to a value-equal Python key, recursively for containers, and
`key_word` remembers the `Val` word each normalized key first arrived as (which
is what lets `map_keys` hand real `Val`s back).

## The Val codec, and the ordering it borrows (A8, A9)

`chain_value`/`val_word` are the two halves of the codec between a `Val` word
and a tier-1 `serpent.types` instance, and `compare` is the ordering `obj_cmp`
(in `hostfns.py`) and the map key order are both built on. Every ordering
answer comes from `types._ordering.val_cmp` -- the oracle the compiler is
proven against -- applied to DECODED operands, so the rig cannot quietly
disagree with it. Cross-type order is `ScValType` rank (A8) and lives only in
`val_cmp`; a tag `serpent.types` has no class for (`Void`, `Error`, the 256-bit
family) raises rather than guessing (A9).
"""

import struct
from collections.abc import Callable, Mapping
from typing import ClassVar

from serpent import val
from serpent._host._scalars import STORAGE_TYPE
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
from serpent.types._ordering import ChainValue, val_cmp
from serpent.types._storage_key import storage_key
from tests.harness.engine import MiniHost
from tests.harness.errors import HostError, HostTrap

__all__ = ["ObjectStore"]

#: Every `(storage_type, key)` bucket is a plain dict; these are the raw
#: `StorageType` immediates the frontend emits (`RawScalarKind.STORAGE_TYPE`).
#: Re-derived from the pinned source (M-1) rather than restated as local
#: literals, so a re-pin of `StorageType`'s ordinal values cannot silently
#: leave this rig testing the wrong bucket numbers.
STORAGE_TEMPORARY = STORAGE_TYPE["temporary"]
STORAGE_PERSISTENT = STORAGE_TYPE["persistent"]
STORAGE_INSTANCE = STORAGE_TYPE["instance"]


class _RankOnly:
    """A container as `val_cmp` can see it here: a rank, and no payload order.

    `serpent.types.Vec`/`Map` are statically typed in their element/key/value
    classes and a host handed `vec_new()` has no element type to give them (see
    this module's docstring), so the store's model of a vec is a list of untyped
    `Val` words. For ORDERING that is enough: `val_cmp` answers a cross-type
    compare on rank alone, and refuses a same-type one -- which is exactly what
    tier 1 itself does for a container (`containers.py`'s `_cmp_payload`
    raises).
    """

    __slots__ = ()

    def _cmp_payload(self) -> object:
        raise NotImplementedError(
            "container comparison; sub-plan B -- tier 1 defers the payload order "
            "for Vec/Map (A15: no inventing an order the host has not been "
            "differentially checked against), so this rig defers it too"
        )


class _VecRank(_RankOnly):
    """Rank read off tier 1, never restated (A8's table lives in one place)."""

    __slots__ = ()
    _SCVAL_RANK: ClassVar[int] = Vec._SCVAL_RANK


class _MapRank(_RankOnly):
    __slots__ = ()
    _SCVAL_RANK: ClassVar[int] = Map._SCVAL_RANK


#: Every `Val` tag whose value is a NUMBER, mapped to the tier-1 class that
#: models it. Both forms of each type are here: the same class answers for the
#: small immediate and for the object handle, which is what makes a mixed
#: compare (`U64` small vs `U64` object) work without a special case.
#: `U256`/`I256` are ABSENT because `serpent.types` has no class for them --
#: there is no oracle answer to delegate to, so `chain_value` says so loudly
#: (A9).
_NUMERIC_BY_TAG: dict[int, Callable[[int], ChainValue]] = {
    val.TAG_U32: U32,
    val.TAG_I32: I32,
    val.TAG_U64_SMALL: U64,
    val.TAG_U64_OBJECT: U64,
    val.TAG_I64_SMALL: I64,
    val.TAG_I64_OBJECT: I64,
    val.TAG_TIMEPOINT_SMALL: Timepoint,
    val.TAG_TIMEPOINT_OBJECT: Timepoint,
    val.TAG_DURATION_SMALL: Duration,
    val.TAG_DURATION_OBJECT: Duration,
    val.TAG_U128_SMALL: U128,
    val.TAG_U128_OBJECT: U128,
    val.TAG_I128_SMALL: I128,
    val.TAG_I128_OBJECT: I128,
}

#: The small tags whose 56-bit body is SIGNED. Getting this set wrong is silent:
#: every non-negative operand agrees either way.
_SMALL_SIGNED = frozenset({val.TAG_I64_SMALL, val.TAG_I128_SMALL})
_SMALL_UNSIGNED = frozenset(
    {
        val.TAG_U64_SMALL,
        val.TAG_TIMEPOINT_SMALL,
        val.TAG_DURATION_SMALL,
        val.TAG_U128_SMALL,
    }
)

#: The object tags whose stored payload IS the tier-1 instance, so the decoder
#: hands the payload straight back.
_PAYLOAD_TAGS = frozenset(
    {
        val.TAG_SYMBOL_OBJECT,
        val.TAG_STRING_OBJECT,
        val.TAG_BYTES_OBJECT,
        val.TAG_ADDRESS_OBJECT,
    }
)

#: `type(value) -> (small tag, object tag)` for `val_word`, the encoder. Keyed
#: on the EXACT type: `Timepoint`/`Duration` share `U64`'s payload shape, and an
#: encoder that reused `U64`'s tag for either would round-trip perfectly while
#: sorting in the wrong rank.
_NUMERIC_FORMS: dict[type, tuple[int, int]] = {
    U64: (val.TAG_U64_SMALL, val.TAG_U64_OBJECT),
    I64: (val.TAG_I64_SMALL, val.TAG_I64_OBJECT),
    Timepoint: (val.TAG_TIMEPOINT_SMALL, val.TAG_TIMEPOINT_OBJECT),
    Duration: (val.TAG_DURATION_SMALL, val.TAG_DURATION_OBJECT),
    U128: (val.TAG_U128_SMALL, val.TAG_U128_OBJECT),
    I128: (val.TAG_I128_SMALL, val.TAG_I128_OBJECT),
}

#: Every tag `chain_value` can answer for, other than the two container tags
#: and `Void` (handled separately in `map_key`, since `chain_value` has no
#: model for it at all -- `storage_key(None)` stands in for it instead, review
#: I1/M2). `SymbolSmall` IS included: `chain_value` answers for it, and
#: `map_key` used to short-circuit it to bare text before reaching this set --
#: it no longer does (I1), so nothing about that special case belongs here now.
_MODELLED_TAGS = (
    frozenset(_NUMERIC_BY_TAG) | _PAYLOAD_TAGS | {val.TAG_FALSE, val.TAG_TRUE, val.TAG_SYMBOL_SMALL}
)


class ObjectStore:
    """The host side of one contract run: an object table, storage, and a call log.

    Usage is two-step, because the linear-memory callbacks need to read the
    guest's memory and that does not exist until the module is instantiated::

        store = ObjectStore()
        host = engine.MiniHost(wasm, imports=store.bindings())
        store.attach(host)
    """

    def __init__(self) -> None:
        #: Handle body -> the object it names. Never compacted: a handle is an
        #: index, so removing an entry would rename every later object.
        self.objects: list[object] = []
        #: `(storage_type, key)` -> value `Val`. Keyed on the storage type
        #: FIRST so the three buckets are visibly separate namespaces, and on
        #: `map_key(key)` rather than the raw word so a `Symbol` key answers
        #: the same whether it arrived small or as a handle.
        self.storage: dict[tuple[int, object], int] = {}
        #: Every host call this store served, as `(name, args)`. The evidence
        #: for "no doubled `has` in the then-arm" and for B7's single
        #: evaluation of an effectful key.
        self.calls: list[tuple[str, tuple[int, ...]]] = []
        #: Every error `Val` `fail_with_error` saw, in order. `MiniHost` keeps
        #: the same list for its own default callback; this store binds its own
        #: so the abort shows up in `calls` beside the calls that led to it.
        self.errors: list[int] = []
        #: `(tag, payload) -> handle`, so `val_word` is idempotent.
        self._interned: dict[tuple[int, object], int] = {}
        #: One normalized key (`map_key`) -> the first `Val` word it arrived as.
        #: `key_word`'s table; see its docstring for why it is remembered rather
        #: than reconstructed.
        self._key_words: dict[object, int] = {}
        self._host: MiniHost | None = None

    def attach(self, host: MiniHost) -> None:
        """Give the linear-memory callbacks something to read."""
        self._host = host

    # -- bindings ---------------------------------------------------------

    def bindings(self) -> dict[str, Callable[..., int]]:
        """Every callback this store implements, by PINNED host-function name."""
        return {
            "vec_new": self.vec_new,
            "vec_push_back": self.vec_push_back,
            "vec_len": self.vec_len,
            "vec_get": self.vec_get,
            "vec_new_from_linear_memory": self.vec_new_from_linear_memory,
            "map_new": self.map_new,
            "map_put": self.map_put,
            "map_get": self.map_get,
            "map_has": self.map_has,
            "map_new_from_linear_memory": self.map_new_from_linear_memory,
            "symbol_new_from_linear_memory": self.symbol_new_from_linear_memory,
            "string_new_from_linear_memory": self.string_new_from_linear_memory,
            "bytes_new_from_linear_memory": self.bytes_new_from_linear_memory,
            "bytes_len": self.bytes_len,
            "get_contract_data": self.get_contract_data,
            "put_contract_data": self.put_contract_data,
            "has_contract_data": self.has_contract_data,
            "del_contract_data": self.del_contract_data,
            "extend_contract_data_ttl": self.extend_contract_data_ttl,
            "fail_with_error": self.fail_with_error,
        }

    def with_(self, extra: Mapping[str, Callable[..., int]]) -> dict[str, Callable[..., int]]:
        """`bindings()` plus `extra` -- for a test that needs a probe callback too."""
        return {**self.bindings(), **extra}

    # -- the Val codec, and nothing but `serpent.val` ---------------------

    def _new(self, tag: int, payload: object) -> int:
        self.objects.append(payload)
        return val.from_body_tag(len(self.objects) - 1, tag)

    def _object(self, word: int, tag: int) -> object:
        if val.tag_of(word) != tag:
            raise AssertionError(
                f"expected object tag {tag}, got {val.tag_of(word)} ({word:#018x})"
            )
        index = val.body_of(word)
        if index >= len(self.objects):
            raise AssertionError(f"dangling object handle {word:#018x}")
        return self.objects[index]

    def _vec(self, word: int) -> list[int]:
        obj = self._object(word, val.TAG_VEC_OBJECT)
        assert isinstance(obj, list)
        return obj

    def _map(self, word: int) -> dict[object, int]:
        obj = self._object(word, val.TAG_MAP_OBJECT)
        assert isinstance(obj, dict)
        return obj

    def _u32(self, word: int) -> int:
        return val.unpack_u32val(word)

    def _read(self, ptr: int, length: int) -> bytes:
        if self._host is None:
            raise AssertionError("ObjectStore.attach(host) was never called")
        return self._host.read_memory(ptr, length)

    def chain_value(self, word: int) -> ChainValue:
        """The tier-1 chain value one `Val` word stands for.

        The decoder half of the codec: what `compare` (and so `obj_cmp`, and so
        the map key order) delegates to, and what a test decodes a returned
        `Val` with. Raises `AssertionError` for a tag `serpent.types` has no
        class for -- there is no oracle answer to borrow, and a guess would be a
        second, drifting model (A9).
        """
        tag = val.tag_of(word)
        if tag in (val.TAG_FALSE, val.TAG_TRUE):
            return Bool(val.unpack_bool(word))
        if tag == val.TAG_SYMBOL_SMALL:
            return Symbol(val.symbol_small_text(word))
        if tag == val.TAG_VEC_OBJECT:
            self._vec(word)  # the handle must really name a vec
            return _VecRank()
        if tag == val.TAG_MAP_OBJECT:
            self._map(word)
            return _MapRank()
        if tag in _PAYLOAD_TAGS:
            payload = self._object(word, tag)
            assert isinstance(payload, (Symbol, String, Bytes, Address)), (
                f"object {word:#018x} does not hold a text/bytes/address payload: {payload!r}"
            )
            return payload
        make = _NUMERIC_BY_TAG.get(tag)
        if make is None:
            raise AssertionError(
                f"tag {tag} ({word:#018x}) has no tier-1 chain type, so there is no "
                "oracle ordering to delegate to (A9: extending the supported set "
                "requires extending the differential tests)"
            )
        if tag == val.TAG_U32:
            return make(val.unpack_u32val(word))
        if tag == val.TAG_I32:
            return make(val.unpack_i32val(word))
        if tag in _SMALL_SIGNED:
            return make(val.unpack_small_i64(word, tag))
        if tag in _SMALL_UNSIGNED:
            return make(val.unpack_small_u64(word, tag))
        number = self._object(word, tag)
        assert isinstance(number, int), f"object {word:#018x} does not hold a number: {number!r}"
        return make(number)

    def val_word(self, value: object) -> int:
        """The canonical `Val` word for a tier-1 chain value.

        The inverse of `chain_value`: the small form when the type has one and
        the value fits it, an object handle otherwise. Equal values intern to
        ONE handle (`_interned`), so this is idempotent and a caller can rebuild
        an expected word instead of remembering a handle. The store's own
        CONSTRUCTORS (`vec_new`, `bytes_new_from_linear_memory`, ...) never
        intern -- a real host hands out a fresh handle per call, and the tests
        that prove host objects are immutable depend on that.
        """
        if isinstance(value, Bool):
            return val.pack_bool(value.value)
        if isinstance(value, U32):
            return val.pack_u32val(value.value)
        if isinstance(value, I32):
            return val.pack_i32val(value.value)
        if isinstance(value, Symbol):
            if val.fits_symbol_small(value.text):
                return val.symbol_small(value.text)
            return self._intern(val.TAG_SYMBOL_OBJECT, value)
        if isinstance(value, String):
            return self._intern(val.TAG_STRING_OBJECT, value)
        if isinstance(value, Bytes):
            # `Bytes32`/`bytes_n(N)` subclass `Bytes` and are the same ScVal
            # case, so they share the tag -- `isinstance`, not `type(...)`.
            return self._intern(val.TAG_BYTES_OBJECT, value)
        if isinstance(value, Address):
            return self._intern(val.TAG_ADDRESS_OBJECT, value)
        forms = _NUMERIC_FORMS.get(type(value))
        if forms is None:
            raise AssertionError(f"no Val encoding for {value!r} in this rig (A9)")
        small_tag, object_tag = forms
        assert isinstance(value, (U64, I64, Timepoint, Duration, U128, I128))
        number = value.value
        if small_tag in _SMALL_SIGNED:
            if val.fits_small_i(number):
                return val.pack_small_i64(number, small_tag)
        elif val.fits_small_u(number):
            return val.pack_small_u64(number, small_tag)
        return self._intern(object_tag, number)

    def _intern(self, tag: int, payload: object) -> int:
        key = (tag, payload)
        if key not in self._interned:
            self._interned[key] = self._new(tag, payload)
        return self._interned[key]

    def compare(self, left: int, right: int) -> int:
        """`-1`/`0`/`1` for two `Val` words, straight out of `val_cmp`.

        The whole content of `obj_cmp` (`hostfns.py` binds it), and the order
        the host keeps a map's keys in.
        """
        answer = val_cmp(self.chain_value(left), self.chain_value(right))
        return (answer > 0) - (answer < 0)

    def map_key(self, word: int) -> object:
        """The Python key one `Val` word stands for, inside a map or storage.

        Normalized BY VALUE, because that is how the host compares a key (see
        this module's docstring for the struct-storage-key bug a handle-keyed
        store produces):

        * a vec or a map becomes its CONTENTS, recursively (a `frozenset` for a
          map, so entry order cannot matter) -- this is the struct-key case;
        * `Void` becomes `storage_key(None)` (`(1,)`, A8's Void rank with no
          payload) -- a struct field typed `X | None` can hold it, and review
          M2 requires it to key identically to `_storage_key.storage_key`'s
          own Void rule, not to fall back to the "no tier-1 model" case below;
        * every OTHER modelled value -- including `Symbol`, whether it arrived
          as a `SymbolSmall` immediate or as a handle -- delegates to
          `types._storage_key.storage_key` (M-2: the value-level twin of this
          word-level decode) applied to the decoded `chain_value`. This is
          review I1: `chain_value` already unifies the small and object forms
          of a `Symbol` (and every other modelled scalar) into ONE tier-1
          instance, so routing both through the SAME function tier 1 uses
          means a map built through `map_new_from_linear_memory` (whose keys
          are name bytes) and one built through `map_put` (whose keys are
          `Val`s) do not just answer `map_get` the same as EACH OTHER, they
          answer it the same as `storage_key` would for the equivalent tier-1
          value -- one definition of key equality, not two that happen to
          agree within this rig;
        * a value with no tier-1 model at all (`Error`, the 256-bit family)
          keeps its raw word. Canonical for every one of those that has a
          single encoding, and the alternative would be to invent an equality
          A9 says this rig does not have.

        Every normalized key is recorded against the word it arrived as, which
        is what `key_word` (and therefore `map_keys`) reads back.
        """
        tag = val.tag_of(word)
        key: object
        if tag == val.TAG_VEC_OBJECT:
            key = ("vec", tuple(self.map_key(item) for item in self._vec(word)))
        elif tag == val.TAG_MAP_OBJECT:
            key = (
                "map",
                frozenset((entry, self.map_key(value)) for entry, value in self._map(word).items()),
            )
        elif tag == val.TAG_VOID:
            key = storage_key(None)
        elif tag in _MODELLED_TAGS:
            key = storage_key(self.chain_value(word))
        else:
            key = word
        self._key_words.setdefault(key, word)
        return key

    def key_word(self, key: object) -> int:
        """A `Val` word for one normalized key -- `map_key`'s inverse.

        Recorded rather than reconstructed: a container key normalizes to its
        contents, and rebuilding an object from those would hand back a handle
        the guest has never seen. The word remembered is the FIRST one that key
        arrived as, which is a real handle to a value-equal object -- all the
        host itself promises about the keys `map_keys` returns.
        """
        word = self._key_words.get(key)
        assert word is not None, f"no Val word was ever recorded for map key {key!r}"
        return word

    def _log(self, name: str, *args: int) -> None:
        self.calls.append((name, args))

    def call_names(self) -> list[str]:
        """Just the names, in order -- the usual assertion target."""
        return [name for name, _args in self.calls]

    def count(self, name: str) -> int:
        return self.call_names().count(name)

    # -- vectors ----------------------------------------------------------

    def vec_new(self) -> int:
        self._log("vec_new")
        return self._new(val.TAG_VEC_OBJECT, [])

    def vec_push_back(self, vec: int, item: int) -> int:
        """Returns a NEW handle: host objects are immutable, which is exactly
        why every mutator result has to be rebound (F.1.9)."""
        self._log("vec_push_back", vec, item)
        return self._new(val.TAG_VEC_OBJECT, [*self._vec(vec), item])

    def vec_len(self, vec: int) -> int:
        self._log("vec_len", vec)
        return val.pack_u32val(len(self._vec(vec)))

    def vec_get(self, vec: int, index: int) -> int:
        """`v.6`, and env.json: "Traps if the index is out of bound."

        A `HostTrap`, not an `AssertionError`: the semantics table pins this as
        a `kind="trap"` observable (`vec_get_out_of_bounds_traps`), so it is a
        contract-level outcome the differential run compares, not a broken-rig
        signal.
        """
        self._log("vec_get", vec, index)
        items = self._vec(vec)
        i = self._u32(index)
        if i >= len(items):
            raise HostTrap(f"vec_get: index {i} past the end of a {len(items)}-item vec")
        return items[i]

    def vec_new_from_linear_memory(self, vals_pos: int, length: int) -> int:
        """`v.g`: an array of 8-byte `Val` words, read straight out of memory."""
        self._log("vec_new_from_linear_memory", vals_pos, length)
        count = self._u32(length)
        raw = self._read(self._u32(vals_pos), 8 * count)
        words = [int.from_bytes(raw[8 * i : 8 * i + 8], "little") for i in range(count)]
        return self._new(val.TAG_VEC_OBJECT, words)

    # -- maps -------------------------------------------------------------

    def map_new(self) -> int:
        self._log("map_new")
        return self._new(val.TAG_MAP_OBJECT, {})

    def map_put(self, m: int, key: int, value: int) -> int:
        self._log("map_put", m, key, value)
        return self._new(val.TAG_MAP_OBJECT, {**self._map(m), self.map_key(key): value})

    def map_get(self, m: int, key: int) -> int:
        """`m.1`, and env.json: "Traps if the key doesn't exist."

        A `HostTrap` for the same reason `vec_get`'s bound is
        (`map_get_missing_key_traps` is a `kind="trap"` row).
        """
        self._log("map_get", m, key)
        entries = self._map(m)
        k = self.map_key(key)
        if k not in entries:
            raise HostTrap(f"map_get: no key {k!r} (have {sorted(map(repr, entries))})")
        return entries[k]

    def map_has(self, m: int, key: int) -> int:
        self._log("map_has", m, key)
        return val.TRUE_VAL if self.map_key(key) in self._map(m) else val.FALSE_VAL

    def map_new_from_linear_memory(self, keys_pos: int, vals_pos: int, length: int) -> int:
        """`m.9`, the asymmetric one -- keys are DESCRIPTORS, values are `Val`s.

        env.json: "Key strings are specified as `len` 8 byte slices consisting
        of the 4 byte pointer and 4 byte length. Actual keys must be byte
        strings sorted in ascending order and be convertible to `Symbol` type.
        Values may be arbitrary `Val`s. Panics if any of the invariants above
        are violated."

        The sort check is ported BY COPY from the spike harness (F.1.5). It is
        the whole reason this callback is not a two-liner: without it, an
        emitter that stopped sorting struct fields (C9) would go green here and
        panic on chain.
        """
        self._log("map_new_from_linear_memory", keys_pos, vals_pos, length)
        keys_ptr = self._u32(keys_pos)
        vals_ptr = self._u32(vals_pos)
        count = self._u32(length)

        descriptors = self._read(keys_ptr, 8 * count)
        values = self._read(vals_ptr, 8 * count)

        entries: dict[object, int] = {}
        previous = b""
        for i in range(count):
            ptr, size = struct.unpack_from("<II", descriptors, 8 * i)
            name = self._read(ptr, size)
            if name <= previous:
                raise AssertionError(
                    f"map keys are not in ascending order: {name!r} follows {previous!r} "
                    "(env.json: the host PANICS here)"
                )
            previous = name
            # Through `val_word`/`map_key` rather than straight to the decoded
            # text, so this path records a `Val` word for the key like every
            # other one does -- `map_keys` on a map built here must hand back
            # real `Symbol` `Val`s, and the host's own answer for these keys is
            # a `Symbol` made from the same bytes.
            key = self.map_key(self.val_word(Symbol(name.decode("utf-8"))))
            entries[key] = int.from_bytes(values[8 * i : 8 * i + 8], "little")
        return self._new(val.TAG_MAP_OBJECT, entries)

    # -- pooled blobs -----------------------------------------------------

    def _blob(self, pos: int, length: int) -> bytes:
        return self._read(self._u32(pos), self._u32(length))

    def symbol_new_from_linear_memory(self, pos: int, length: int) -> int:
        self._log("symbol_new_from_linear_memory", pos, length)
        return self._new(val.TAG_SYMBOL_OBJECT, Symbol(self._blob(pos, length).decode("utf-8")))

    def string_new_from_linear_memory(self, pos: int, length: int) -> int:
        self._log("string_new_from_linear_memory", pos, length)
        return self._new(val.TAG_STRING_OBJECT, String(self._blob(pos, length).decode("utf-8")))

    def bytes_new_from_linear_memory(self, pos: int, length: int) -> int:
        self._log("bytes_new_from_linear_memory", pos, length)
        return self._new(val.TAG_BYTES_OBJECT, Bytes(self._blob(pos, length)))

    def bytes_len(self, b: int) -> int:
        """`b.8`: the payload length as a `U32Val`.

        The one host call an ABI check contains (E14's `BytesN(n)` row): a tag
        byte says `Bytes`, and nothing but this can say whether the payload is
        the declared length. `_object`'s tag assertion is deliberately left in
        place -- an emitter that called this BEFORE its tag check would fail
        here, loudly, rather than producing the host's own error where a
        contract error was owed.
        """
        self._log("bytes_len", b)
        return val.pack_u32val(len(self.bytes_of(b)))

    def text_of(self, word: int) -> str:
        """The characters behind a `Symbol`/`String` `Val`, however it arrived.

        `.text` rather than `str()`: these ARE the tier-1 instances (A9), and
        `str(Symbol("k"))` is the repr `Symbol('k')`, not the key.
        """
        tag = val.tag_of(word)
        if tag == val.TAG_SYMBOL_SMALL:
            return val.symbol_small_text(word)
        obj = self._object(word, tag)
        assert isinstance(obj, (Symbol, String)), f"not a text object: {obj!r}"
        return obj.text

    def bytes_of(self, word: int) -> bytes:
        """The payload behind a `Bytes` `Val`."""
        obj = self._object(word, val.TAG_BYTES_OBJECT)
        assert isinstance(obj, Bytes)
        return obj.data

    def fail_with_error(self, error: int) -> int:
        """`x.5`: record the abort, then raise -- it "does not actually return".

        The Contract-type check is the host's own precondition (env.json: the
        error "must be of error-type `ScErrorType::Contract`"), kept so a
        lowering that built the wrong kind of Error `Val` fails here rather
        than producing an abort a client cannot classify.
        """
        self._log("fail_with_error", error)
        if not val.is_contract_error_val(error):
            raise AssertionError(
                f"fail_with_error needs a Contract-type Error Val, got {error:#018x}"
            )
        self.errors.append(error)
        raise HostError(error)

    # -- the storage buckets ----------------------------------------------

    def put_contract_data(self, key: int, value: int, storage_type: int) -> int:
        self._log("put_contract_data", key, value, storage_type)
        self.storage[(storage_type, self.map_key(key))] = value
        return val.VOID_VAL

    def has_contract_data(self, key: int, storage_type: int) -> int:
        self._log("has_contract_data", key, storage_type)
        present = (storage_type, self.map_key(key)) in self.storage
        return val.TRUE_VAL if present else val.FALSE_VAL

    def get_contract_data(self, key: int, storage_type: int) -> int:
        """A MISSING key is an assertion failure, not a `HostError`.

        Deliberate: `get_contract_data` on an absent key is undefined behaviour
        the emitter's guard exists to prevent (E13/E14). If a test ever reaches
        it, the guard was not emitted -- and that must read as a broken
        lowering, not as the `HostError` a correct guard would have raised.
        """
        self._log("get_contract_data", key, storage_type)
        entry = (storage_type, self.map_key(key))
        if entry not in self.storage:
            raise AssertionError(
                f"get_contract_data reached an absent key {entry!r}: the E13 storage "
                "guard was not emitted (a real host's behaviour here is undefined)"
            )
        return self.storage[entry]

    def extend_contract_data_ttl(
        self, key: int, storage_type: int, threshold: int, extend_to: int
    ) -> int:
        """`l.7`, and the pin's only 4-arity MIXED row: `(True, False, True, True)`.

        Bound purely so that row is EXECUTABLE. TTLs are not modelled -- there
        is no ledger sequence here to extend against -- so the call is recorded
        and nothing else. What it proves is the argument dispatch: position 1
        must arrive as the bare storage-type number while positions 2 and 3
        arrive as `U32Val`s, and only a mixed row can catch a lowering that
        picked one convention for the whole call.
        """
        self._log("extend_contract_data_ttl", key, storage_type, threshold, extend_to)
        return val.VOID_VAL

    def del_contract_data(self, key: int, storage_type: int) -> int:
        self._log("del_contract_data", key, storage_type)
        self.storage.pop((storage_type, self.map_key(key)), None)
        return val.VOID_VAL
