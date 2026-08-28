"""The COMPLETE dev-only mini host: `obj_cmp`, the containers, the env surface.

**This is not an oracle** (ruling E1). It is a fast local loop that answers one
narrow question -- do the bytes the emitter just produced compute what the
Python source said? -- before a testnet round trip. Sub-plan F re-proves
everything against the real Soroban host. A green run here means "the codegen is
self-consistent", not "this contract is correct on chain"
(`spikes/spike1/harness.py:18-21`, verbatim).

Where this sits
---------------

* `engine.py` is the pinned wasmtime configuration and `MiniHost`, which binds
  whatever callbacks it is handed. It stays free of any model of what a host
  function *does*.
* `objects.py`'s `ObjectStore` is the object half Tasks 6/8 needed and proved:
  the object table, the vec/map/blob constructors, the three storage buckets,
  the `m.9` ascending-key panic, and the call log.
* `i256.py`'s `Wide256Host` is the 128/256-bit arithmetic oracle, written
  independently of the guest limb code it checks.
* **This module completes the surface.** `FullHost` is an `ObjectStore` that
  also binds every host function the compiler can emit and that no earlier task
  needed: `obj_cmp`, the rest of the vec/map/bytes accessors, the recording
  event/auth/ledger/TTL surface, the `u64`/`i64`/`Timepoint`/`Duration` object
  bridges, `strkey_to_address`, and the wide-integer table -- the last one
  allocating out of the SAME handle space, so a module that reaches both a vec
  and a 128-bit piece cannot have two different objects share a handle body.

`test_harness_hostfns.py` asserts the coverage claim against the compiler's own
tables (`recognize.ENV_HOST_FN_TARGETS`/`CONTAINER_HOST_FN_TARGETS`) rather than
against a list restated here, so a new lowering that reaches a new host function
fails there instead of in the differential run.

`obj_cmp` delegates; it does not decide (A8, A9, D.4)
-----------------------------------------------------

`obj_cmp` is the one callback here with real semantic content, and it has none
of its own: it is `ObjectStore.compare`, which decodes both operands with
`chain_value` and hands them to `serpent.types._ordering.val_cmp` -- the tier-1
oracle the compiler is proven against. The codec and the delegation live in
`objects.py` (the object table is what a handle has to be read through, and the
map key order needs the same comparison); what this module owns is the binding
and the pin's return convention. Three consequences worth stating where the
callback is:

* **Small forms are decoded first.** An `obj_cmp` argument is any `Val` word --
  a `SymbolSmall` immediate, a small integer, or an object handle -- so the
  callback cannot look at the object table alone. A comparison that skipped the
  small side would compare a raw payload against a handle *index* and answer
  plausible nonsense.
* **Cross-type order is `ScValType` rank, not tag rank** (A8), and `val_cmp`
  owns that table. Neither this module nor `objects.py` repeats it.
* **`Symbol` compares over its DECODED TEXT, in ASCII order.** That is tier 1's
  pin, and it is deliberately mirrored here so the compiled answer can be
  compared against tier 1 today. It may be the WRONG answer about the real
  host: `SymbolSmall` packs each character through a 6-bit alphabet
  (`val.SYMBOL_CHARS`, where `"_"` is code 1 and `"A"` is 12), and a host that
  compares packed codes reverses `Symbol("_")` vs `Symbol("A")`. Settling that
  is F's tier-2b obligation (dossier D.4, "the top sub-plan D/F differential
  vector"); if the host disagrees, it is a controller decision on the frozen
  table, not a change here.
* **`val_cmp` is an explicitly PARTIAL model** (A9): "extending the supported
  set requires extending the differential tests". So a tag with no
  `serpent.types` class (`Void`, `Error`, the 256-bit family) raises rather than
  guessing, and two containers refuse to be ordered against each other exactly
  as tier 1 does.

`require_auth` always succeeds (S17)
------------------------------------

The real host TRAPS when the invocation was not authorized. This rig has no
authorization state to consult, so `require_auth`/`require_auth_for_args`
record the address and return -- mock-all-auths semantics, and S17's documented
tier-2a fidelity line. A contract's auth logic is therefore NOT under test
here; tier 2b is where these can fail.
"""

from collections.abc import Callable
from functools import cmp_to_key

from serpent import val
from serpent.types import (
    U64,
    Address,
    Bytes,
)
from tests.harness.engine import HostError, HostTrap
from tests.harness.i256 import Wide256Host
from tests.harness.objects import ObjectStore

__all__ = [
    "BAD_STRKEY_ERROR_VAL",
    "DEFAULT_LEDGER_SEQUENCE",
    "DEFAULT_LEDGER_TIMESTAMP",
    "INVALID_POSITION_ERROR_VAL",
    "FullHost",
]

#: What `map_key_by_pos`/`map_val_by_pos` hand back for an invalid position.
#: env.json says they "return ScError", which the VM surfaces as an abort, so
#: the harness raises `HostError` carrying an Error `Val` of the OBJECT type.
#: The real XDR code is NOT pinned in this repo -- tests assert against this
#: constant, never a literal word, the same convention `i256.py`'s
#: `DIV_ERROR_VAL` documents.
INVALID_POSITION_ERROR_VAL = val.error_val(0, val.ERROR_TYPE_OBJECT)

#: What `strkey_to_address` hands back for a strkey that is not an account or a
#: contract ("Any other valid or invalid strkey (e.g. 'S...') will trigger an
#: error"). Unpinned XDR code, same convention as above.
BAD_STRKEY_ERROR_VAL = val.error_val(0, val.ERROR_TYPE_VALUE)

#: The ledger stubs' starting values. Arbitrary but deliberately not zero: a
#: zero timestamp is a plausible-looking answer, and a contract that read one
#: without the callback ever running would look like it worked. Settable per
#: instance (`host.ledger_timestamp = ...`).
DEFAULT_LEDGER_TIMESTAMP = 1_700_000_000
DEFAULT_LEDGER_SEQUENCE = 1_000_000


class _SharedWide(Wide256Host):
    """`Wide256Host`'s arithmetic, allocating out of ONE shared handle space.

    Used alone, `Wide256Host` keeps its own object list -- which is right for
    the arithmetic tests, and wrong the moment a single module reaches both a
    128-bit piece and a vec: two independent index-based allocators both hand
    out body 0, and a handle would name two different objects. Only the two
    allocation methods are overridden, so every arithmetic rule, every
    signedness decision and every `WideHostFailure` message stays in `i256.py`.

    The wrong-tag branch is delegated back to the base class on purpose: that
    path raises before touching the object list, so review B4's message ("a
    small-form Val reaching an accessor means the caller skipped its tag
    branch") is not transcribed a second time here.
    """

    def __init__(self, store: ObjectStore) -> None:
        self._store = store
        super().__init__()

    def _handle(self, value: int, tag: int) -> int:
        return self._store._new(tag, value)

    def _object(self, handle: int, tag: int) -> int:
        if val.tag_of(handle) != tag:
            return super()._object(handle, tag)
        value = self._store._object(handle, tag)
        assert isinstance(value, int), f"handle {handle:#018x} does not hold a wide integer"
        return value


class FullHost(ObjectStore):
    """An `ObjectStore` that binds every host function the compiler can emit.

    Usage is the two-step `ObjectStore` needs, because the linear-memory
    callbacks read the guest's memory and that does not exist until the module
    is instantiated::

        host = FullHost()
        mini = engine.MiniHost(wasm, imports=host.bindings())
        host.attach(mini)

    Inspection surfaces for a test: `storage` (three buckets, from
    `ObjectStore`), `events`, `auths`, `calls`, `errors`.
    """

    def __init__(self) -> None:
        super().__init__()
        #: Every `contract_event` call, as `(topics, data)` -- `topics` the
        #: tuple of `Val` words read out of the topics vec.
        self.events: list[tuple[tuple[int, ...], int]] = []
        #: Every address word `require_auth`/`require_auth_for_args` saw, in
        #: order. Mock-all-auths (S17): recording IS the whole model.
        self.auths: list[int] = []
        #: The ledger stubs, settable per instance.
        self.ledger_timestamp = DEFAULT_LEDGER_TIMESTAMP
        self.ledger_sequence = DEFAULT_LEDGER_SEQUENCE
        #: The 128/256-bit arithmetic oracle, allocating out of THIS store's
        #: handle space. Public so a test can reach a piece constructor
        #: directly; the bindings below relay every call through `_log` first.
        self.wide = _SharedWide(self)

    # -- bindings -------------------------------------------------------------

    def bindings(self) -> dict[str, Callable[..., int]]:
        """Every callback, by PINNED host-function name.

        `ObjectStore`'s table, plus this class's, plus the wide-integer table
        relayed through `_relayed` so a 128-bit call shows up in `calls` beside
        the object calls that surround it.
        """
        table: dict[str, Callable[..., int]] = {
            **super().bindings(),
            # -- comparison
            "obj_cmp": self.obj_cmp,
            # -- vectors
            "vec_put": self.vec_put,
            "vec_del": self.vec_del,
            "vec_insert": self.vec_insert,
            "vec_append": self.vec_append,
            "vec_slice": self.vec_slice,
            "vec_push_front": self.vec_push_front,
            "vec_pop_front": self.vec_pop_front,
            "vec_pop_back": self.vec_pop_back,
            "vec_first_index_of": self.vec_first_index_of,
            # -- maps
            "map_del": self.map_del,
            "map_len": self.map_len,
            "map_keys": self.map_keys,
            "map_values": self.map_values,
            "map_key_by_pos": self.map_key_by_pos,
            "map_val_by_pos": self.map_val_by_pos,
            # -- bytes
            "bytes_get": self.bytes_get,
            "bytes_slice": self.bytes_slice,
            # -- the env surface
            "contract_event": self.contract_event,
            "require_auth": self.require_auth,
            "require_auth_for_args": self.require_auth_for_args,
            "get_ledger_timestamp": self.get_ledger_timestamp,
            "get_ledger_sequence": self.get_ledger_sequence,
            "extend_current_contract_instance_and_code_ttl": (
                self.extend_current_contract_instance_and_code_ttl
            ),
            # -- scalar object bridges
            "obj_from_u64": self.obj_from_u64,
            "obj_to_u64": self.obj_to_u64,
            "obj_from_i64": self.obj_from_i64,
            "obj_to_i64": self.obj_to_i64,
            "timepoint_obj_from_u64": self.timepoint_obj_from_u64,
            "timepoint_obj_to_u64": self.timepoint_obj_to_u64,
            "duration_obj_from_u64": self.duration_obj_from_u64,
            "duration_obj_to_u64": self.duration_obj_to_u64,
            "strkey_to_address": self.strkey_to_address,
        }
        for name, impl in self.wide.bindings().items():
            table[name] = self._relayed(name, impl)
        return table

    def _relayed(self, name: str, impl: Callable[..., int]) -> Callable[..., int]:
        def wrapped(*args: int) -> int:
            self._log(name, *args)
            return impl(*args)

        return wrapped

    # -- comparison -----------------------------------------------------------

    def obj_cmp(self, left: int, right: int) -> int:
        """`x.0`: "Returns -1 if a<b, 1 if a>b, or 0 if a==b."

        `val_typed_ret` is `False` in the pin: the answer is a bare i64, not a
        `Val`, so it is returned as the unsigned word `engine._trampoline`
        converts back (P4 -- the mask is structural, and `-1` is exactly the
        value that shows whether it ran).
        """
        self._log("obj_cmp", left, right)
        return val.as_u64(self.compare(left, right))

    # -- vectors --------------------------------------------------------------

    def _index(self, items: list[int], index: int, *, limit: int | None = None) -> int:
        """One `U32Val` index, bounds-checked the way env.json says.

        `limit` defaults to `len(items)` (exclusive), which is the bound for
        every accessor. `vec_insert` passes `len(items) + 1`: the length itself
        is the append position, IN bounds for an insert and out of bounds for a
        get, one off-by-one apart.
        """
        i = self._u32(index)
        ceiling = len(items) if limit is None else limit
        if i >= ceiling:
            raise HostTrap(f"index {i} is out of bounds for a {len(items)}-item vector")
        return i

    def vec_put(self, vec: int, index: int, value: int) -> int:
        self._log("vec_put", vec, index, value)
        items = self._vec(vec)
        i = self._index(items, index)
        return self._new(val.TAG_VEC_OBJECT, [*items[:i], value, *items[i + 1 :]])

    def vec_del(self, vec: int, index: int) -> int:
        self._log("vec_del", vec, index)
        items = self._vec(vec)
        i = self._index(items, index)
        return self._new(val.TAG_VEC_OBJECT, [*items[:i], *items[i + 1 :]])

    def vec_insert(self, vec: int, index: int, value: int) -> int:
        self._log("vec_insert", vec, index, value)
        items = self._vec(vec)
        i = self._index(items, index, limit=len(items) + 1)
        return self._new(val.TAG_VEC_OBJECT, [*items[:i], value, *items[i:]])

    def vec_append(self, first: int, second: int) -> int:
        self._log("vec_append", first, second)
        return self._new(val.TAG_VEC_OBJECT, [*self._vec(first), *self._vec(second)])

    def vec_slice(self, vec: int, start: int, end: int) -> int:
        """`start` inclusive, `end` EXCLUSIVE; traps if either is out of bound.

        Both ends are checked explicitly rather than left to Python's slice,
        which clamps silently -- a lowering that computed an end past the tail
        would then get a short vector instead of the trap the host gives it.
        """
        self._log("vec_slice", vec, start, end)
        items = self._vec(vec)
        lo, hi = self._u32(start), self._u32(end)
        if lo > hi or hi > len(items):
            raise HostTrap(f"slice [{lo}, {hi}) is out of bounds for a {len(items)}-item vector")
        return self._new(val.TAG_VEC_OBJECT, items[lo:hi])

    def vec_push_front(self, vec: int, item: int) -> int:
        self._log("vec_push_front", vec, item)
        return self._new(val.TAG_VEC_OBJECT, [item, *self._vec(vec)])

    def vec_pop_front(self, vec: int) -> int:
        self._log("vec_pop_front", vec)
        items = self._vec(vec)
        if not items:
            raise HostTrap("vec_pop_front on an empty vector")
        return self._new(val.TAG_VEC_OBJECT, items[1:])

    def vec_pop_back(self, vec: int) -> int:
        self._log("vec_pop_back", vec)
        items = self._vec(vec)
        if not items:
            raise HostTrap("vec_pop_back on an empty vector")
        return self._new(val.TAG_VEC_OBJECT, items[:-1])

    def vec_first_index_of(self, vec: int, item: int) -> int:
        """The `u32` index, or `Void` when absent -- a VALUE, not a trap.

        The search is STRUCTURAL (`compare`), not word equality: a
        `SymbolSmall` immediate finds an equal `SymbolObject` element, which is
        the host's own answer and the one a word compare would get wrong.
        """
        self._log("vec_first_index_of", vec, item)
        for i, element in enumerate(self._vec(vec)):
            if self.compare(element, item) == 0:
                return val.pack_u32val(i)
        return val.VOID_VAL

    # -- maps -----------------------------------------------------------------

    def sorted_keys(self, m: int) -> list[object]:
        """A map's normalized keys in the host's KEY-SORTED order.

        env.json: `map_keys`/`map_values` return vectors "ordered in the
        original map's key-sorted order", and position is what
        `map_key_by_pos`/`map_val_by_pos` index. Python dict order is INSERTION
        order, so the sort is load-bearing: a callback that walked the dict
        would return the right keys in the wrong order, and a contract reading
        `keys()[0]` would silently get the wrong one.
        """

        def compare_keys(left: object, right: object) -> int:
            return self.compare(self.key_word(left), self.key_word(right))

        return sorted(self._map(m), key=cmp_to_key(compare_keys))

    def map_len(self, m: int) -> int:
        self._log("map_len", m)
        return val.pack_u32val(len(self._map(m)))

    def map_del(self, m: int, key: int) -> int:
        """env.json: "Remove a key/value mapping from a map if it exists, traps
        if doesn't."

        Note the asymmetry with `del_contract_data`, which is a NO-OP on an
        absent key -- two different host behaviours the rig must not unify.
        """
        self._log("map_del", m, key)
        entries = self._map(m)
        k = self.map_key(key)
        if k not in entries:
            raise HostTrap(f"map_del: no key {k!r} (have {sorted(map(repr, entries))})")
        return self._new(
            val.TAG_MAP_OBJECT, {other: v for other, v in entries.items() if other != k}
        )

    def map_keys(self, m: int) -> int:
        self._log("map_keys", m)
        return self._new(val.TAG_VEC_OBJECT, [self.key_word(k) for k in self.sorted_keys(m)])

    def map_values(self, m: int) -> int:
        self._log("map_values", m)
        entries = self._map(m)
        return self._new(val.TAG_VEC_OBJECT, [entries[k] for k in self.sorted_keys(m)])

    def _position(self, m: int, index: int) -> object:
        keys = self.sorted_keys(m)
        i = self._u32(index)
        if i >= len(keys):
            # env.json: "If `i` is an invalid position, return ScError" -- a
            # returned error, which the VM surfaces as an abort, so this is a
            # `HostError` and not the `HostTrap` the vec accessors raise.
            raise HostError(INVALID_POSITION_ERROR_VAL)
        return keys[i]

    def map_key_by_pos(self, m: int, index: int) -> int:
        self._log("map_key_by_pos", m, index)
        return self.key_word(self._position(m, index))

    def map_val_by_pos(self, m: int, index: int) -> int:
        self._log("map_val_by_pos", m, index)
        return self._map(m)[self._position(m, index)]

    # -- bytes ----------------------------------------------------------------

    def bytes_get(self, b: int, index: int) -> int:
        """`b.6`: one byte, as a `U32Val`. Traps if the index is out of bound.

        The semantics table pins the trap (`bytes_positive_out_of_range_traps`),
        and the `U32Val` return is what makes `Bytes[i] + U32(1)` type-check on
        chain -- a raw byte would be a `Val` with tag 0.
        """
        self._log("bytes_get", b, index)
        payload = self.bytes_of(b)
        i = self._u32(index)
        if i >= len(payload):
            raise HostTrap(f"bytes_get: index {i} past the end of {len(payload)} bytes")
        return val.pack_u32val(payload[i])

    def bytes_slice(self, b: int, start: int, end: int) -> int:
        self._log("bytes_slice", b, start, end)
        payload = self.bytes_of(b)
        lo, hi = self._u32(start), self._u32(end)
        if lo > hi or hi > len(payload):
            raise HostTrap(f"bytes_slice [{lo}, {hi}) is out of bounds for {len(payload)} bytes")
        return self._new(val.TAG_BYTES_OBJECT, Bytes(payload[lo:hi]))

    # -- events, auth, ledger, TTL --------------------------------------------

    def contract_event(self, topics: int, data: int) -> int:
        """`x.1`: record `(topics, data)`. `topics` "is expected to be a
        `SCVec`", so the vec is unpacked here -- a test asserting on the
        emitted event should not have to chase a handle."""
        self._log("contract_event", topics, data)
        self.events.append((tuple(self._vec(topics)), data))
        return val.VOID_VAL

    def _address(self, word: int) -> Address:
        """The `Address` behind an auth argument, refusing anything else.

        A loud check rather than a silent record: `require_auth` on a
        non-address is a lowering bug, and a rig that recorded the word anyway
        would let it through to a tier-2b run to discover.
        """
        address = self.chain_value(word)
        assert isinstance(address, Address), f"require_auth needs an Address, got {address!r}"
        return address

    def require_auth(self, address: int) -> int:
        """`a.0`: record the address and SUCCEED (mock-all-auths, S17).

        The real host traps when the invocation was not authorized; there is no
        authorization state here to consult, so this rig cannot fail. The
        module docstring says what that costs.
        """
        self._log("require_auth", address)
        self._address(address)
        self.auths.append(address)
        return val.VOID_VAL

    def require_auth_for_args(self, address: int, args: int) -> int:
        """`a._`: the same mock, with the args vec checked for shape only."""
        self._log("require_auth_for_args", address, args)
        self._address(address)
        self._vec(args)
        self.auths.append(address)
        return val.VOID_VAL

    def get_ledger_timestamp(self) -> int:
        """`x.4`: the stub timestamp as a `U64Val` (small or object).

        `Ty.U64`, not `Ty.Timepoint` -- the frontend's own choice, because "the
        host's `get_ledger_timestamp` returns a `U64Val`, and serpent does not
        silently reinterpret an `ScVal` case" (`env.py`). Returning a
        `TimepointVal` here would make the guest's unbox branch miss.
        """
        self._log("get_ledger_timestamp")
        return self.val_word(U64(self.ledger_timestamp))

    def get_ledger_sequence(self) -> int:
        """`x.3`: the stub sequence as a `U32Val`."""
        self._log("get_ledger_sequence")
        return val.pack_u32val(self.ledger_sequence)

    def extend_current_contract_instance_and_code_ttl(self, threshold: int, extend_to: int) -> int:
        """`l.8`: recorded and nothing else, like `extend_contract_data_ttl`.

        TTLs are not modelled -- there is no ledger sequence to extend against
        -- so what this proves is that the call LINKS and dispatches: both
        arguments are `U32Val`s here, unlike `extend_contract_data_ttl`'s mixed
        row.
        """
        self._log("extend_current_contract_instance_and_code_ttl", threshold, extend_to)
        return val.VOID_VAL

    # -- the scalar object bridges --------------------------------------------

    def obj_from_u64(self, value: int) -> int:
        """`i._`: a RAW `u64` (`val_typed_args=(False,)`) into a `U64Object`."""
        self._log("obj_from_u64", value)
        return self._new(val.TAG_U64_OBJECT, value)

    def obj_to_u64(self, obj: int) -> int:
        """`i.0`: a `U64Object` back to a raw `u64` (`val_typed_ret=False`)."""
        self._log("obj_to_u64", obj)
        return self._number(obj, val.TAG_U64_OBJECT)

    def obj_from_i64(self, value: int) -> int:
        """`i.1`: the argument word reinterpreted SIGNED, then stored as the
        mathematical integer -- the one place the i64 half of the bridge can
        lose a sign."""
        self._log("obj_from_i64", value)
        return self._new(val.TAG_I64_OBJECT, val.as_i64(value))

    def obj_to_i64(self, obj: int) -> int:
        self._log("obj_to_i64", obj)
        return val.as_u64(self._number(obj, val.TAG_I64_OBJECT))

    def timepoint_obj_from_u64(self, value: int) -> int:
        self._log("timepoint_obj_from_u64", value)
        return self._new(val.TAG_TIMEPOINT_OBJECT, value)

    def timepoint_obj_to_u64(self, obj: int) -> int:
        self._log("timepoint_obj_to_u64", obj)
        return self._number(obj, val.TAG_TIMEPOINT_OBJECT)

    def duration_obj_from_u64(self, value: int) -> int:
        self._log("duration_obj_from_u64", value)
        return self._new(val.TAG_DURATION_OBJECT, value)

    def duration_obj_to_u64(self, obj: int) -> int:
        self._log("duration_obj_to_u64", obj)
        return self._number(obj, val.TAG_DURATION_OBJECT)

    def _number(self, obj: int, tag: int) -> int:
        """The integer behind a numeric object handle, refusing any other form.

        `_object`'s tag assertion is the load-bearing part: `obj_to_*` accepts
        an OBJECT and nothing else on chain, so a small-form `Val` reaching one
        is exactly the bug a missing tag branch on a boxed result produces
        (review B4). Loud here, not on chain.
        """
        value = self._object(obj, tag)
        assert isinstance(value, int), f"object {obj:#018x} does not hold a number: {value!r}"
        return value

    def strkey_to_address(self, strkey: int) -> int:
        """`a.1`: a pooled strkey STRING (or `Bytes`) into an `AddressObject`.

        Review B6: an `Address` literal is lowered as
        `string_new_from_linear_memory` over the pooled strkey text, then this.
        env.json: "Any other valid or invalid strkey (e.g. 'S...') will trigger
        an error", so a seed key -- which the frontend should have rejected --
        aborts here rather than producing an address nobody can spend.
        """
        self._log("strkey_to_address", strkey)
        tag = val.tag_of(strkey)
        assert tag in (val.TAG_STRING_OBJECT, val.TAG_BYTES_OBJECT), (
            f"strkey_to_address takes a StringObject or a BytesObject, got tag {tag}"
        )
        text = (
            self.bytes_of(strkey).decode("utf-8")
            if tag == val.TAG_BYTES_OBJECT
            else self.text_of(strkey)
        )
        try:
            address = Address(text)
        except (TypeError, ValueError) as exc:
            raise HostError(BAD_STRKEY_ERROR_VAL) from exc
        return self._new(val.TAG_ADDRESS_OBJECT, address)
