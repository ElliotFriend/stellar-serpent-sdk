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
"""

import struct
from collections.abc import Callable, Mapping

from serpent import val
from serpent.types import Bytes, String, Symbol
from tests.harness.engine import HostError, MiniHost

__all__ = ["ObjectStore"]

#: Every `(storage_type, key)` bucket is a plain dict; these are the raw
#: `StorageType` immediates the frontend emits (`RawScalarKind.STORAGE_TYPE`).
STORAGE_TEMPORARY = 0
STORAGE_PERSISTENT = 1
STORAGE_INSTANCE = 2


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
            "get_contract_data": self.get_contract_data,
            "put_contract_data": self.put_contract_data,
            "has_contract_data": self.has_contract_data,
            "del_contract_data": self.del_contract_data,
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

    def map_key(self, word: int) -> object:
        """The Python key one `Val` word stands for, inside a map's dict.

        A `Symbol` is normalized to its TEXT, because the host compares symbols
        by their characters regardless of whether they arrived as a
        `SymbolSmall` immediate or as a handle -- a map built through
        `map_new_from_linear_memory` (whose keys are name bytes) and one built
        through `map_put` (whose keys are `Val`s) must answer `map_get` the
        same way. Everything else is keyed on its `Val` word.
        """
        if val.tag_of(word) == val.TAG_SYMBOL_SMALL:
            return val.symbol_small_text(word)
        if val.tag_of(word) == val.TAG_SYMBOL_OBJECT:
            return self.text_of(word)
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
        self._log("vec_get", vec, index)
        items = self._vec(vec)
        i = self._u32(index)
        if i >= len(items):
            raise AssertionError(f"vec_get: index {i} past the end of a {len(items)}-item vec")
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
        self._log("map_get", m, key)
        entries = self._map(m)
        k = self.map_key(key)
        if k not in entries:
            raise AssertionError(f"map_get: no key {k!r} (have {sorted(map(repr, entries))})")
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
            entries[name.decode("utf-8")] = int.from_bytes(values[8 * i : 8 * i + 8], "little")
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

    def del_contract_data(self, key: int, storage_type: int) -> int:
        self._log("del_contract_data", key, storage_type)
        self.storage.pop((storage_type, self.map_key(key)), None)
        return val.VOID_VAL
