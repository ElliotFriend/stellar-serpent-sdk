"""Tests for `tests/harness/hostfns.py` -- the completed dev-only mini host.

The harness is **not an oracle** (ruling E1): a green run here means "the
codegen is self-consistent", not "this contract is correct on chain". Sub-plan F
re-proves everything against the real Soroban host. What these tests protect is
the rig, and specifically the four places where a wrong mini host would make a
green emitter suite meaningless:

* **`obj_cmp` is the tier-1 oracle or it is a second, drifting model.** Every
  assertion below compares the callback's answer against
  `serpent.types._ordering.val_cmp` on the same two values rather than against a
  hand-written expectation, so the harness cannot quietly disagree with the
  oracle the compiler is proven against (A9: extending the supported set
  extends these tests, which is why the rank matrix is generated from one
  table).
* **Small-vs-object forms.** An `obj_cmp` argument is any `Val` word: a small
  symbol, a small integer, or an object handle. Decoding the small forms is the
  whole content of the callback, and every mixed pair below would pass if the
  decoder were skipped for one side only when the two forms happened to sort
  the same way -- so the pairs are chosen so they do not.
* **The `m.9` ascending-key panic.** Ported by copy from the spike (F.1.5) and
  re-proved here directly against the store, so a refactor of the map
  callbacks cannot delete the one check that catches an emitter which stopped
  sorting struct fields.
* **The inventory.** Task 13 runs the in-scope semantics cases and four
  fixtures with an EMPTY skip list. `test_every_task13_fixture_is_fully_bound`
  is what makes that reachable: it compiles each one and asserts every host
  function it reaches has a callback registered.
"""

from collections.abc import Callable
from functools import cmp_to_key
from pathlib import Path

import pytest

from serpent import val
from serpent.compiler.frontend import compile_module
from serpent.compiler.recognize import (
    CONTAINER_HOST_FN_TARGETS,
    ENV_HOST_FN_TARGETS,
    UNREACHED_CONTAINER_HOST_FNS,
)
from serpent.decorators import contracttype
from serpent.emitter import build_wasm
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
from tests.harness import engine, testmod
from tests.harness.hostfns import (
    INVALID_POSITION_ERROR_VAL,
    FullHost,
)
from tests.harness.objects import STORAGE_INSTANCE, STORAGE_PERSISTENT, STORAGE_TEMPORARY
from tests.semantics.cases import CASES, SemCase
from tests.unit.test_frontend_semantics import wrap_case

#: Two real strkeys, an account and a contract, so the `Address` rank has both
#: `SCAddressType` discriminants available (`_order_key` compares the
#: discriminant first). Lifted from `tests/semantics/cases.py`'s own fixtures.
_ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"
_CONTRACT = "CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI"


@contracttype
class _BalanceKey:
    """The dominant real-world struct storage key, for the I1 cross-tier test."""

    owner: Address


#: One representative value per rank the harness models, in ascending
#: `_SCVAL_RANK` order, each rank contributing a LOW and a HIGH value so the
#: matrix below covers both the within-rank payload compare and every
#: cross-rank pair. The wide members are deliberately past the 56-bit small
#: body (`U64`, `Timepoint`, `U128`, `I128`) so the object form is exercised.
_LADDER: tuple[tuple[ChainValue, ChainValue], ...] = (
    (Bool(False), Bool(True)),
    (U32(1), U32(2)),
    (I32(-1), I32(1)),
    (U64(5), U64(2**60)),
    (I64(-(2**60)), I64(7)),
    (Timepoint(1), Timepoint(2**60)),
    (Duration(1), Duration(2)),
    (U128(1), U128(2**100)),
    (I128(-(2**100)), I128(1)),
    (Bytes(b"a"), Bytes(b"b")),
    (String("a"), String("b")),
    (Symbol("A"), Symbol("_")),
    (Address(_ACCOUNT), Address(_CONTRACT)),
)

_EVERY_REPRESENTATIVE: tuple[ChainValue, ...] = tuple(v for pair in _LADDER for v in pair)


def _sign(n: int) -> int:
    return (n > 0) - (n < 0)


def _memory_host(store: FullHost, data: bytes) -> engine.MiniHost:
    """A `MiniHost` over a module that is nothing but one page of `data`.

    The linear-memory callbacks cannot be called until a memory exists to read
    (`ObjectStore.attach`), and none of them needs a guest function to do it --
    so the smallest rig for them is a module with a memory, a data segment, and
    no code at all.
    """
    wasm = testmod.build_test_module([], imports=(), memory_pages=1, data=data)
    host = engine.MiniHost(wasm, imports=store.bindings())
    store.attach(host)
    return host


# --- the Val codec both directions -------------------------------------------


@pytest.mark.parametrize("value", _EVERY_REPRESENTATIVE, ids=repr)
def test_val_word_and_chain_value_round_trip(value: ChainValue) -> None:
    """`chain_value(val_word(v)) == v` for every rank the harness models.

    The two halves are what `obj_cmp` and Task 13's result decoding are built
    on, so they are proved as a pair: an encoder that picked the wrong tag and
    a decoder that read it back would agree with each other and with nothing
    else, which is why the ladder's wide members (past the 56-bit small body)
    are in it.
    """
    store = FullHost()
    assert store.chain_value(store.val_word(value)) == value


def test_val_word_uses_the_small_form_when_the_value_fits() -> None:
    store = FullHost()
    assert val.tag_of(store.val_word(U64(5))) == val.TAG_U64_SMALL
    assert val.tag_of(store.val_word(U64(2**60))) == val.TAG_U64_OBJECT
    assert val.tag_of(store.val_word(Symbol("short"))) == val.TAG_SYMBOL_SMALL
    assert val.tag_of(store.val_word(Symbol("a_long_symbol"))) == val.TAG_SYMBOL_OBJECT


# --- obj_cmp -----------------------------------------------------------------


def test_obj_cmp_agrees_with_val_cmp_on_every_supported_rank_pair() -> None:
    """A9: `obj_cmp` IS `val_cmp` over decoded operands, at every rank pair.

    Every expectation is computed by the oracle, never written out, so this
    test grows with `_LADDER` -- which is what "extending the supported set
    requires extending the differential tests" (A9) means in practice. All
    26x26 ordered pairs are checked, so the 13 within-rank payload compares
    and the 156 cross-rank rank compares are both covered.
    """
    store = FullHost()
    disagreements = []
    for left in _EVERY_REPRESENTATIVE:
        for right in _EVERY_REPRESENTATIVE:
            answer = val.as_i64(store.obj_cmp(store.val_word(left), store.val_word(right)))
            expected = _sign(val_cmp(left, right))
            if answer != expected:
                disagreements.append((repr(left), repr(right), answer, expected))
    assert disagreements == []


@pytest.mark.parametrize(
    ("low", "high"),
    [(low, high) for low, high in _LADDER],
    ids=[type(low).__name__ for low, _high in _LADDER],
)
def test_obj_cmp_orders_within_each_rank(low: ChainValue, high: ChainValue) -> None:
    """The within-rank payload compare, stated as an ordering rather than as
    agreement -- a `val_cmp` that answered 0 for every pair would satisfy the
    agreement test above and nothing here."""
    store = FullHost()
    lo, hi = store.val_word(low), store.val_word(high)
    assert val.as_i64(store.obj_cmp(lo, hi)) == -1
    assert val.as_i64(store.obj_cmp(hi, lo)) == 1
    assert val.as_i64(store.obj_cmp(lo, lo)) == 0


def test_obj_cmp_decodes_a_small_operand_against_an_object_operand() -> None:
    """A mixed pair per type that HAS both forms (review B4's shape).

    The values are chosen so a decoder that skipped the small side would get
    the answer WRONG rather than accidentally right: the small operand is the
    larger number, so comparing a raw word against a handle body (a small
    index) inverts it.
    """
    store = FullHost()
    small = store.val_word(U64(val.MAX_SMALL_U64))
    big = store.val_word(U64(2**60))
    assert val.tag_of(small) == val.TAG_U64_SMALL
    assert val.tag_of(big) == val.TAG_U64_OBJECT
    assert val.as_i64(store.obj_cmp(small, big)) == -1
    assert val.as_i64(store.obj_cmp(big, small)) == 1


def test_obj_cmp_compares_a_small_symbol_against_a_symbol_object() -> None:
    """A `Symbol` compares over its TEXT however it arrived: `"zzz"` (small,
    9 chars or fewer) against `"aaaaaaaaaaaa"` (an object) must answer by
    characters, not by form."""
    store = FullHost()
    small = store.val_word(Symbol("zzz"))
    wide = store.val_word(Symbol("aaaaaaaaaaaa"))
    assert val.tag_of(small) == val.TAG_SYMBOL_SMALL
    assert val.tag_of(wide) == val.TAG_SYMBOL_OBJECT
    assert val.as_i64(store.obj_cmp(small, wide)) == 1
    assert val.as_i64(store.obj_cmp(wide, small)) == -1


def test_obj_cmp_gives_the_tier1_ascii_answer_for_underscore_versus_A() -> None:
    """`Symbol("_") > Symbol("A")` -- the tier-1 ASCII pin, deliberately.

    THE top sub-plan D/F differential vector (dossier D.4,
    `cases.py:symbol_underscore_vs_A_ascii_order`). Tier 1 orders `Symbol` by
    raw UTF-8 bytes, so `"A"` (65) sorts before `"_"` (95). The real host packs
    each character through a 6-bit alphabet (`val.SYMBOL_CHARS`, where `"_"` is
    code 1 and `"A"` is 12) and, if it compares packed CODES, answers the
    opposite. **This harness is not the oracle for that question** (ruling E1):
    it mirrors tier 1 so the compiled answer can be compared against tier 1
    today, and F's tier-2b run against a real host is what settles it. If the
    host disagrees, that is a controller decision on the frozen table, not a
    change here.
    """
    store = FullHost()
    underscore = store.val_word(Symbol("_"))
    letter = store.val_word(Symbol("A"))
    assert val.symbol_char_code("_") < val.symbol_char_code("A")  # the 6-bit codes disagree
    assert val.as_i64(store.obj_cmp(underscore, letter)) == 1
    assert val.as_i64(store.obj_cmp(letter, underscore)) == -1


def test_obj_cmp_orders_a_vec_against_a_scalar_by_rank_alone() -> None:
    """A9's boundary: a container's PAYLOAD order is not modelled in tier 1,
    but its rank is -- so a vec still sorts after every scalar."""
    store = FullHost()
    vec = store.vec_new()
    for scalar in _EVERY_REPRESENTATIVE:
        if isinstance(scalar, Address):
            # Address (rank 18) sorts AFTER Vec (16) and Map (17).
            assert val.as_i64(store.obj_cmp(vec, store.val_word(scalar))) == -1
        else:
            assert val.as_i64(store.obj_cmp(vec, store.val_word(scalar))) == 1


def test_obj_cmp_refuses_to_order_two_vecs() -> None:
    """Tier 1 raises `NotImplementedError` for a container payload compare
    (`containers.py`), so the harness does too rather than inventing an order
    the host has never been differentially checked against (A15/A9)."""
    store = FullHost()
    first, second = store.vec_new(), store.vec_new()
    with pytest.raises(NotImplementedError, match="container comparison"):
        store.obj_cmp(first, second)


@pytest.mark.parametrize(
    "word",
    [val.VOID_VAL, val.error_val(7), val.pack_small_u64(1, val.TAG_U256_SMALL)],
    ids=["void", "error", "u256_small"],
)
def test_obj_cmp_names_a_tag_tier1_has_no_type_for(word: int) -> None:
    """`Void`, `Error` and the 256-bit family have no `serpent.types` class, so
    there is no oracle answer to delegate to -- and the harness says so loudly
    instead of guessing (A9)."""
    store = FullHost()
    with pytest.raises(AssertionError, match="no tier-1 chain type"):
        store.obj_cmp(word, word)


# --- storage -----------------------------------------------------------------


@pytest.mark.parametrize(
    "bucket", [STORAGE_TEMPORARY, STORAGE_PERSISTENT, STORAGE_INSTANCE], ids=["t", "p", "i"]
)
def test_storage_round_trips_in_each_bucket(bucket: int) -> None:
    store = FullHost()
    key = val.symbol_small("k")
    assert store.has_contract_data(key, bucket) == val.FALSE_VAL
    assert store.put_contract_data(key, val.pack_u32val(7), bucket) == val.VOID_VAL
    assert store.has_contract_data(key, bucket) == val.TRUE_VAL
    assert store.get_contract_data(key, bucket) == val.pack_u32val(7)
    assert store.del_contract_data(key, bucket) == val.VOID_VAL
    assert store.has_contract_data(key, bucket) == val.FALSE_VAL


def test_the_three_buckets_are_separate_namespaces() -> None:
    """The same key in two buckets is two entries -- a store keyed on the key
    alone would make `temporary()` and `persistent()` alias, and every storage
    test in the suite would still pass."""
    store = FullHost()
    key = val.symbol_small("k")
    store.put_contract_data(key, val.pack_u32val(1), STORAGE_TEMPORARY)
    store.put_contract_data(key, val.pack_u32val(2), STORAGE_PERSISTENT)
    assert store.get_contract_data(key, STORAGE_TEMPORARY) == val.pack_u32val(1)
    assert store.get_contract_data(key, STORAGE_PERSISTENT) == val.pack_u32val(2)
    assert store.has_contract_data(key, STORAGE_INSTANCE) == val.FALSE_VAL


def test_a_symbol_key_answers_the_same_whichever_form_it_arrived_in() -> None:
    """A key written through a `SymbolSmall` immediate is readable through a
    `SymbolObject` handle with the same text, because the host compares symbols
    by their characters."""
    store = FullHost()
    store.put_contract_data(val.symbol_small("k"), val.pack_u32val(9), STORAGE_INSTANCE)
    handle = store.val_word(Symbol("k"))
    assert val.tag_of(handle) == val.TAG_SYMBOL_SMALL
    wide = store._new(val.TAG_SYMBOL_OBJECT, Symbol("k"))
    assert store.get_contract_data(wide, STORAGE_INSTANCE) == val.pack_u32val(9)


def test_a_freshly_built_struct_key_finds_the_entry_the_first_one_wrote() -> None:
    """THE storage-key bug this task found, held down.

    A struct storage key (`BalanceKey(owner=Address(...))`) is a `MapObject`,
    and the contract builds a FRESH one on every invocation. A store that keyed
    on the handle word filed `mint`'s write under one handle and looked
    `balance` up under another, found nothing, and returned the storage default
    -- a plausible number, silently wrong, out of a module that validates and
    runs. `map_key` normalizes a key by VALUE, recursively, so the two agree.
    """
    store = FullHost()

    def balance_key() -> int:
        # `owner` is a FRESH AddressObject each time, the way an `Address`
        # literal's `strkey_to_address` call makes one per invocation -- so the
        # struct key differs from the last one at BOTH levels, and only a
        # recursive normalization makes the two agree.
        owner = store._new(val.TAG_ADDRESS_OBJECT, Address(_ACCOUNT))
        return store.map_put(store.map_new(), val.symbol_small("owner"), owner)

    written = balance_key()
    store.put_contract_data(written, val.pack_u32val(10), STORAGE_PERSISTENT)

    read = balance_key()
    assert read != written
    assert store.has_contract_data(read, STORAGE_PERSISTENT) == val.TRUE_VAL
    assert store.get_contract_data(read, STORAGE_PERSISTENT) == val.pack_u32val(10)


def test_map_key_agrees_with_storage_key_across_tiers() -> None:
    """Review I1: the harness's word-level `map_key` and tier-1's value-level
    `storage_key` are ONE definition of key equality, not two that happen to
    agree within the harness. For a `Symbol` key, a struct key, and a nested
    container key, `map_key` of the harness's `Val` word must equal
    `storage_key` of the equivalent tier-1 value -- not just equal ITSELF
    across the small/object forms, which `test_a_symbol_key_answers_the_same_
    whichever_form_it_arrived_in` and the struct test above already cover.
    """
    store = FullHost()

    # A Symbol key, small and object forms, both against the SAME tier-1 value.
    assert store.map_key(store.val_word(Symbol("k"))) == storage_key(Symbol("k"))
    wide_symbol = store._new(val.TAG_SYMBOL_OBJECT, Symbol("k"))
    assert store.map_key(wide_symbol) == storage_key(Symbol("k"))

    # A struct key: `owner` written the way `map_new`/`map_put` build it, kept
    # in agreement with `storage_key` normalizing the EQUIVALENT `_BalanceKey`.
    owner = store._new(val.TAG_ADDRESS_OBJECT, Address(_ACCOUNT))
    struct_word = store.map_put(store.map_new(), val.symbol_small("owner"), owner)
    assert store.map_key(struct_word) == storage_key(_BalanceKey(owner=Address(_ACCOUNT)))

    # A nested container key: a Vec of Symbols, inside a Map value -- built at
    # the word level, compared against the tier-1 `Vec`/`Map` it stands for.
    tier1_tags = Vec(Symbol, [Symbol("a"), Symbol("b")])
    tags_word = store.vec_push_back(
        store.vec_push_back(store.vec_new(), store.val_word(Symbol("a"))),
        store.val_word(Symbol("b")),
    )
    assert store.map_key(tags_word) == storage_key(tier1_tags)

    tier1_flags = Map(Symbol, Bool, [(Symbol("tags"), Bool(True))])
    flags_word = store.map_put(store.map_new(), store.val_word(Symbol("tags")), val.TRUE_VAL)
    assert store.map_key(flags_word) == storage_key(tier1_flags)


def test_equal_object_keys_are_one_key_however_they_were_built() -> None:
    """The same value-equality for the other object key shapes, and across the
    small/object forms of one number -- the host compares keys with `obj_cmp`,
    which cares about neither the handle nor the encoding."""
    store = FullHost()
    m = store.map_new()
    m = store.map_put(m, store._new(val.TAG_BYTES_OBJECT, Bytes(b"k")), val.pack_u32val(1))
    m = store.map_put(m, store.val_word(U64(2**60)), val.pack_u32val(2))
    assert store.map_len(m) == val.pack_u32val(2)

    again = store._new(val.TAG_BYTES_OBJECT, Bytes(b"k"))
    assert store.map_get(m, again) == val.pack_u32val(1)
    # A `U64` past the small range only has the object form, so build the
    # collapse the other way: a vec of one element, twice.
    first = store.vec_push_back(store.vec_new(), val.pack_u32val(3))
    second = store.vec_push_back(store.vec_new(), val.pack_u32val(3))
    m = store.map_put(m, first, val.pack_u32val(9))
    assert store.map_get(m, second) == val.pack_u32val(9)
    assert store.map_len(m) == val.pack_u32val(3)


def test_a_void_key_normalizes_via_storage_key_of_none() -> None:
    """Review M2: `Void` has no `serpent.types` class, but it is NOT lumped in
    with the "no tier-1 model, keep the raw word" case below -- an
    Option-typed struct field holding `None` is a legitimate on-chain map
    value, and `storage_key(None)` (`(1,)`, `Void`'s own A8 rank) is what
    `_storage_key.storage_key` normalizes it to at the value level. `map_key`
    must agree, so the two tiers key a `None`-valued field identically."""
    store = FullHost()
    m = store.map_put(store.map_new(), val.VOID_VAL, val.pack_u32val(1))
    assert store.map_get(m, val.VOID_VAL) == val.pack_u32val(1)
    assert store.map_key(val.VOID_VAL) == storage_key(None) == (1,)


def test_a_value_with_no_tier1_model_keeps_its_raw_word() -> None:
    """A9's boundary: `Error` and the 256-bit family have no `serpent.types`
    class at all (unlike `Void`, which `storage_key(None)` now models), so
    `map_key` cannot normalize one by value and keeps the word instead -- and
    is honest about not being an equality this rig has for everything."""
    store = FullHost()
    error_word = val.error_val(7)
    m = store.map_put(store.map_new(), error_word, val.pack_u32val(1))
    assert store.map_get(m, error_word) == val.pack_u32val(1)
    assert store.map_key(error_word) == error_word


def test_get_contract_data_on_an_absent_key_is_a_rig_assertion() -> None:
    """Deliberately NOT a `HostError`: a real host's behaviour on an absent key
    is undefined, and ruling E13's emitted guard is what prevents the call --
    so reaching it means the guard was not emitted, which must read as a broken
    lowering rather than as the contract error a correct guard raises (E14)."""
    store = FullHost()
    with pytest.raises(AssertionError, match="E13 storage guard was not emitted"):
        store.get_contract_data(val.symbol_small("nope"), STORAGE_PERSISTENT)


def test_the_ttl_calls_are_recorded_no_ops() -> None:
    """There is no ledger sequence here to extend against, so the TTL calls are
    recorded and nothing else. What they prove is that the ARGUMENT DISPATCH
    links: `extend_contract_data_ttl` is the pin's only 4-arity mixed row
    (`(True, False, True, True)`), where position 1 arrives as the bare
    storage-type number while 2 and 3 arrive as `U32Val`s."""
    store = FullHost()
    key = val.symbol_small("k")
    assert (
        store.extend_contract_data_ttl(
            key, STORAGE_PERSISTENT, val.pack_u32val(10), val.pack_u32val(100)
        )
        == val.VOID_VAL
    )
    assert (
        store.extend_current_contract_instance_and_code_ttl(
            val.pack_u32val(10), val.pack_u32val(100)
        )
        == val.VOID_VAL
    )
    assert store.storage == {}
    assert store.call_names() == [
        "extend_contract_data_ttl",
        "extend_current_contract_instance_and_code_ttl",
    ]


# --- events and auth ---------------------------------------------------------


def test_contract_event_records_its_topics_and_data() -> None:
    store = FullHost()
    topics = store.vec_push_back(store.vec_new(), val.symbol_small("transfer"))
    topics = store.vec_push_back(topics, store.val_word(U32(1)))
    assert store.contract_event(topics, val.pack_u32val(42)) == val.VOID_VAL
    assert store.events == [
        ((val.symbol_small("transfer"), val.pack_u32val(1)), val.pack_u32val(42))
    ]


def test_require_auth_records_the_address_and_succeeds() -> None:
    """Mock-all-auths semantics, and S17's documented tier-2a fidelity line:
    the real host TRAPS when the invocation was not authorized, and this rig
    has no authorization state to consult, so it records and succeeds. A
    contract's auth logic is therefore NOT under test here -- F's tier 2b is
    where `require_auth` can actually fail."""
    store = FullHost()
    address = store.val_word(Address(_ACCOUNT))
    assert store.require_auth(address) == val.VOID_VAL
    args = store.vec_push_back(store.vec_new(), val.pack_u32val(3))
    assert store.require_auth_for_args(address, args) == val.VOID_VAL
    assert store.auths == [address, address]


# --- the ledger stubs --------------------------------------------------------


def test_the_ledger_accessors_return_settable_stubs() -> None:
    store = FullHost()
    assert store.get_ledger_timestamp() == store.val_word(U64(store.ledger_timestamp))
    assert store.get_ledger_sequence() == val.pack_u32val(store.ledger_sequence)
    store.ledger_timestamp = 2**60
    store.ledger_sequence = 77
    assert val.tag_of(store.get_ledger_timestamp()) == val.TAG_U64_OBJECT
    assert store.chain_value(store.get_ledger_timestamp()) == U64(2**60)
    assert store.get_ledger_sequence() == val.pack_u32val(77)


# --- the vector surface ------------------------------------------------------


def _vec_items(store: FullHost, handle: int) -> list[int]:
    return [
        store.vec_get(handle, val.pack_u32val(i)) for i in range(store._u32(store.vec_len(handle)))
    ]


def _u32s(*ns: int) -> list[int]:
    return [val.pack_u32val(n) for n in ns]


def test_the_vector_mutators_all_return_new_handles() -> None:
    """Host objects are immutable, which is exactly why every mutator result
    has to be rebound (F.1.9). A callback that mutated in place and returned
    its argument would pass every value assertion below and hide the one bug
    the immutability is there to expose."""
    store = FullHost()
    empty = store.vec_new()
    one = store.vec_push_back(empty, val.pack_u32val(1))
    assert one != empty
    assert _vec_items(store, empty) == []
    assert _vec_items(store, one) == _u32s(1)


def test_the_vector_surface() -> None:
    store = FullHost()
    v = store.vec_new()
    for n in (1, 2, 3):
        v = store.vec_push_back(v, val.pack_u32val(n))
    assert _vec_items(store, v) == _u32s(1, 2, 3)

    assert _vec_items(store, store.vec_push_front(v, val.pack_u32val(0))) == _u32s(0, 1, 2, 3)
    assert _vec_items(store, store.vec_pop_front(v)) == _u32s(2, 3)
    assert _vec_items(store, store.vec_pop_back(v)) == _u32s(1, 2)
    assert _vec_items(store, store.vec_put(v, val.pack_u32val(1), val.pack_u32val(9))) == _u32s(
        1, 9, 3
    )
    assert _vec_items(store, store.vec_del(v, val.pack_u32val(1))) == _u32s(1, 3)
    assert _vec_items(store, store.vec_insert(v, val.pack_u32val(1), val.pack_u32val(9))) == _u32s(
        1, 9, 2, 3
    )
    assert _vec_items(store, store.vec_append(v, v)) == _u32s(1, 2, 3, 1, 2, 3)
    assert _vec_items(store, store.vec_slice(v, val.pack_u32val(1), val.pack_u32val(3))) == _u32s(
        2, 3
    )
    assert store.vec_len(v) == val.pack_u32val(3)


def test_vec_insert_at_the_end_appends() -> None:
    """env.json: `vec_insert` "traps if the index is out of bound", and the
    length itself is IN bounds for an insert (it is the append position) while
    being out of bounds for `vec_get`. Two different bounds, one off-by-one
    apart, so the boundary is pinned rather than inferred."""
    store = FullHost()
    v = store.vec_push_back(store.vec_new(), val.pack_u32val(1))
    assert _vec_items(store, store.vec_insert(v, val.pack_u32val(1), val.pack_u32val(2))) == _u32s(
        1, 2
    )
    with pytest.raises(engine.HostTrap):
        store.vec_insert(v, val.pack_u32val(2), val.pack_u32val(2))


def test_vec_first_index_of_answers_void_when_absent() -> None:
    """The one vec callback whose miss is a VALUE, not a trap: env.json says it
    "returns the u32 index of the value if it's there. Otherwise, it returns
    `Void`" -- and the search is structural, so a `SymbolSmall` immediate finds
    an equal `SymbolObject` element."""
    store = FullHost()
    v = store.vec_new()
    v = store.vec_push_back(v, store._new(val.TAG_SYMBOL_OBJECT, Symbol("hit")))
    v = store.vec_push_back(v, val.pack_u32val(2))
    assert store.vec_first_index_of(v, val.symbol_small("hit")) == val.pack_u32val(0)
    assert store.vec_first_index_of(v, val.pack_u32val(2)) == val.pack_u32val(1)
    assert store.vec_first_index_of(v, val.pack_u32val(3)) == val.VOID_VAL


_OUT_OF_BOUNDS: tuple[Callable[[FullHost, int], int], ...] = (
    lambda s, v: s.vec_get(v, val.pack_u32val(3)),
    lambda s, v: s.vec_put(v, val.pack_u32val(3), val.pack_u32val(0)),
    lambda s, v: s.vec_del(v, val.pack_u32val(3)),
    lambda s, v: s.vec_slice(v, val.pack_u32val(0), val.pack_u32val(4)),
    lambda s, v: s.vec_pop_front(s.vec_new()),
    lambda s, v: s.vec_pop_back(s.vec_new()),
)


@pytest.mark.parametrize(
    "call",
    _OUT_OF_BOUNDS,
    ids=["get", "put", "del", "slice", "pop_front_empty", "pop_back_empty"],
)
def test_the_vector_bounds_traps(call: Callable[[FullHost, int], int]) -> None:
    """Every env.json "Traps if ..." row is a `HostTrap`, NOT an
    `AssertionError`: an out-of-bounds index is a contract-level trap the
    semantics table pins (`vec_get_out_of_bounds_traps`), while an
    `AssertionError` out of this rig means the rig or the lowering is broken.
    Conflating the two would make Task 13 unable to tell a passing trap case
    from a broken harness."""
    store = FullHost()
    v = store.vec_new()
    for n in (1, 2, 3):
        v = store.vec_push_back(v, val.pack_u32val(n))
    with pytest.raises(engine.HostTrap):
        call(store, v)


# --- the map surface ---------------------------------------------------------


def test_map_keys_and_values_come_back_in_key_sorted_order() -> None:
    """env.json: "The new vector is ordered in the original map's key-sorted
    order." Insertion order is deliberately the REVERSE of key order here, so a
    `map_keys` that just walked the dict would return the wrong vector."""
    store = FullHost()
    m = store.map_new()
    for name in ("c", "b", "a"):
        m = store.map_put(m, val.symbol_small(name), val.pack_u32val(ord(name)))
    keys = store.map_keys(m)
    assert _vec_items(store, keys) == [val.symbol_small(n) for n in ("a", "b", "c")]
    assert _vec_items(store, store.map_values(m)) == _u32s(ord("a"), ord("b"), ord("c"))
    assert store.map_len(m) == val.pack_u32val(3)


def test_map_ordering_crosses_ranks_through_obj_cmp() -> None:
    """A heterogeneous map sorts by `ScValType` rank first (A8) -- the same
    answer `val_cmp` gives, because the sort delegates to `obj_cmp`."""
    store = FullHost()
    m = store.map_new()
    inserted: list[ChainValue] = [Symbol("s"), U32(9), Bytes(b"b"), Bool(True)]
    for key in inserted:
        m = store.map_put(m, store.val_word(key), val.pack_u32val(0))

    def oracle_cmp(left: object, right: object) -> int:
        assert isinstance(left, ChainValue) and isinstance(right, ChainValue)
        return val_cmp(left, right)

    expected = sorted(inserted, key=cmp_to_key(oracle_cmp))
    assert _vec_items(store, store.map_keys(m)) == [store.val_word(k) for k in expected]


def test_map_by_pos_indexes_the_sorted_order() -> None:
    store = FullHost()
    m = store.map_new()
    for name in ("b", "a"):
        m = store.map_put(m, val.symbol_small(name), val.pack_u32val(ord(name)))
    assert store.map_key_by_pos(m, val.pack_u32val(0)) == val.symbol_small("a")
    assert store.map_val_by_pos(m, val.pack_u32val(1)) == val.pack_u32val(ord("b"))


def test_map_by_pos_at_an_invalid_position_is_an_scerror() -> None:
    """env.json: "If `i` is an invalid position, return ScError" -- a RETURNED
    error, which the VM surfaces as an abort, so the harness raises
    `HostError` rather than `HostTrap`. Tests assert against the module
    constant, never a literal word: the real XDR code is not pinned in this
    repo (the same convention `i256.py`'s `DIV_ERROR_VAL` documents)."""
    store = FullHost()
    m = store.map_put(store.map_new(), val.symbol_small("a"), val.pack_u32val(1))
    with pytest.raises(engine.HostError) as info:
        store.map_key_by_pos(m, val.pack_u32val(1))
    assert info.value.val == INVALID_POSITION_ERROR_VAL
    with pytest.raises(engine.HostError):
        store.map_val_by_pos(m, val.pack_u32val(1))


def test_map_del_removes_a_key_and_traps_when_it_is_absent() -> None:
    """env.json: "Remove a key/value mapping from a map if it exists, traps if
    doesn't." Note the asymmetry with `del_contract_data`, which is a no-op on
    an absent key -- two different host behaviours the rig must not unify."""
    store = FullHost()
    m = store.map_put(store.map_new(), val.symbol_small("a"), val.pack_u32val(1))
    emptied = store.map_del(m, val.symbol_small("a"))
    assert store.map_len(emptied) == val.pack_u32val(0)
    assert store.map_len(m) == val.pack_u32val(1)
    with pytest.raises(engine.HostTrap):
        store.map_del(emptied, val.symbol_small("a"))


def test_map_get_on_a_missing_key_traps() -> None:
    """The semantics table pins this (`map_get_missing_key_traps`), so it is a
    `HostTrap`."""
    store = FullHost()
    with pytest.raises(engine.HostTrap):
        store.map_get(store.map_new(), val.symbol_small("a"))


def test_map_has_answers_both_ways() -> None:
    store = FullHost()
    m = store.map_put(store.map_new(), val.symbol_small("a"), val.pack_u32val(1))
    assert store.map_has(m, val.symbol_small("a")) == val.TRUE_VAL
    assert store.map_has(m, val.symbol_small("b")) == val.FALSE_VAL


# --- the m.9 ascending-key panic, re-proved directly -------------------------


def _descriptor_blob(names: tuple[bytes, ...], values: tuple[int, ...]) -> bytes:
    """One page of guest memory: `len` key descriptors, then `len` `Val` words,
    then the key name bytes the descriptors point at."""
    keys_base = 0
    vals_base = 8 * len(names)
    text_base = vals_base + 8 * len(values)
    out = bytearray()
    offset = text_base
    for name in names:
        out += offset.to_bytes(4, "little") + len(name).to_bytes(4, "little")
        offset += len(name)
    for word in values:
        out += word.to_bytes(8, "little")
    for name in names:
        out += name
    assert len(out) == offset and keys_base == 0
    return bytes(out)


def test_map_new_from_linear_memory_builds_a_map_from_ascending_keys() -> None:
    store = FullHost()
    data = _descriptor_blob((b"a", b"b"), (val.pack_u32val(1), val.pack_u32val(2)))
    _memory_host(store, data)
    m = store.map_new_from_linear_memory(
        val.pack_u32val(0), val.pack_u32val(16), val.pack_u32val(2)
    )
    assert store.map_get(m, val.symbol_small("a")) == val.pack_u32val(1)
    assert store.map_get(m, val.symbol_small("b")) == val.pack_u32val(2)


def test_descending_map_keys_still_panic_after_the_refactor() -> None:
    """F.1.5's negative control, re-proved against the store itself.

    env.json: "Actual keys must be byte strings sorted in ascending order...
    Panics if any of the invariants above are violated." A harness that lost
    this check would go green on an emitter that stopped sorting struct fields
    (C9) and the contract would panic only on chain -- the one direction a rig
    like this must never be wrong in. `test_emitter_lower_objects.py` keeps the
    end-to-end version of this control; this one holds the check in place
    against a refactor of the map callbacks specifically.
    """
    store = FullHost()
    data = _descriptor_blob((b"b", b"a"), (val.pack_u32val(1), val.pack_u32val(2)))
    _memory_host(store, data)
    with pytest.raises(AssertionError, match="not in ascending order"):
        store.map_new_from_linear_memory(
            val.pack_u32val(0), val.pack_u32val(16), val.pack_u32val(2)
        )


# --- bytes -------------------------------------------------------------------


def test_the_bytes_surface() -> None:
    store = FullHost()
    b = store._new(val.TAG_BYTES_OBJECT, Bytes(b"abcd"))
    assert store.bytes_len(b) == val.pack_u32val(4)
    assert store.bytes_get(b, val.pack_u32val(1)) == val.pack_u32val(ord("b"))
    sliced = store.bytes_slice(b, val.pack_u32val(1), val.pack_u32val(3))
    assert store.bytes_of(sliced) == b"bc"
    with pytest.raises(engine.HostTrap):
        store.bytes_get(b, val.pack_u32val(4))
    with pytest.raises(engine.HostTrap):
        store.bytes_slice(b, val.pack_u32val(0), val.pack_u32val(5))


# --- the scalar object bridges ----------------------------------------------


def test_the_u64_and_i64_object_bridges_round_trip() -> None:
    """`obj_from_u64`/`obj_to_u64` are the pin's RAW-argument pair
    (`val_typed_args=(False,)`): the argument is a bare 64-bit word, not a
    `Val`. `i64`'s negative half is the case that separates a correct bridge
    from one that lost the sign at the boundary."""
    store = FullHost()
    handle = store.obj_from_u64(2**63 + 5)
    assert val.tag_of(handle) == val.TAG_U64_OBJECT
    assert store.obj_to_u64(handle) == 2**63 + 5

    negative = store.obj_from_i64(val.as_u64(-(2**62)))
    assert val.tag_of(negative) == val.TAG_I64_OBJECT
    assert val.as_i64(store.obj_to_i64(negative)) == -(2**62)


def test_the_timepoint_and_duration_bridges_round_trip() -> None:
    """Two distinct `ScVal` cases, one payload shape -- so a bridge that
    reused the `U64` tag for either would round-trip perfectly and sort in the
    wrong rank. The tags are asserted, not just the values."""
    store = FullHost()
    t = store.timepoint_obj_from_u64(2**60)
    d = store.duration_obj_from_u64(2**60)
    assert val.tag_of(t) == val.TAG_TIMEPOINT_OBJECT
    assert val.tag_of(d) == val.TAG_DURATION_OBJECT
    assert store.timepoint_obj_to_u64(t) == 2**60
    assert store.duration_obj_to_u64(d) == 2**60
    assert val.as_i64(store.obj_cmp(t, d)) == -1  # Timepoint (7) before Duration (8)


def test_the_wide_bridges_allocate_from_the_same_handle_space() -> None:
    """One object table, so a module that reaches BOTH the 128-bit pieces and
    a vec cannot have two handles with the same body mean two different
    objects. `i256.py`'s `Wide256Host` keeps its own list when used alone; here
    it allocates through the store, and this test is what proves the two
    spaces were actually joined rather than merely intended to be."""
    store = FullHost()
    vec = store.vec_new()
    wide = store.wide.obj_from_u128_pieces(1, 2)
    assert val.body_of(vec) != val.body_of(wide)
    assert store.wide.obj_to_u128_hi64(wide) == 1
    assert store.wide.obj_to_u128_lo64(wide) == 2
    assert store.chain_value(wide) == U128((1 << 64) | 2)


def test_strkey_to_address_converts_a_pooled_string() -> None:
    """Review B6: an `Address` literal is a pooled strkey STRING that
    `strkey_to_address` converts, so the callback takes a `StringObject` and
    answers an `AddressObject`."""
    store = FullHost()
    text = store._new(val.TAG_STRING_OBJECT, String(_ACCOUNT))
    address = store.strkey_to_address(text)
    assert val.tag_of(address) == val.TAG_ADDRESS_OBJECT
    assert store.chain_value(address) == Address(_ACCOUNT)


def test_strkey_to_address_refuses_a_strkey_that_is_not_G_or_C() -> None:
    """env.json: "Any other valid or invalid strkey (e.g. 'S...') will trigger
    an error." A seed strkey reaching here means the emitter pooled something
    the frontend should have rejected, so it fails loudly."""
    store = FullHost()
    text = store._new(val.TAG_STRING_OBJECT, String("SBAD"))
    with pytest.raises(engine.HostError):
        store.strkey_to_address(text)


# --- the fidelity line and the binding inventory ----------------------------


def test_the_harness_docstrings_carry_the_spikes_fidelity_line() -> None:
    """Ruling E1: the mini host is NOT an oracle, and the sentence that says so
    is the spike's own, verbatim (`spikes/spike1/harness.py:18-21`). Asserted
    rather than trusted because it is the one line whose deletion would change
    what a green suite is allowed to claim."""
    import tests.harness
    import tests.harness.hostfns

    line = (
        'A green run here means "the codegen is self-consistent", not "this '
        'contract is correct on chain"'
    )
    for module in (tests.harness, tests.harness.hostfns):
        doc = module.__doc__ or ""
        assert line in " ".join(doc.split()), module.__name__


def test_every_bound_name_is_a_host_function_in_the_pin() -> None:
    """A typo in the binding table is otherwise invisible: `MiniHost._bind`
    looks the name up in the pin, so a misspelled key would only fail when a
    module happened to import it."""
    from serpent._host import functions_by_name

    unknown = sorted(set(FullHost().bindings()) - set(functions_by_name))
    assert unknown == []


#: Every host function the emitter itself can name that no authoring surface
#: reaches -- read off `lower.py`/`arith.py`'s own call sites. `obj_cmp` and
#: `bytes_len` are the two an ABI check or a comparison inserts; the box/unbox
#: bridges and the limb families are the numeric representation's;
#: `strkey_to_address` is review B6's `Address`-literal second call; the
#: linear-memory constructors are the literal pool's; `fail_with_error` is
#: every error path's.
_EMITTER_ADDITIONS = frozenset(
    {
        "bytes_len",
        "duration_obj_from_u64",
        "duration_obj_to_u64",
        "fail_with_error",
        "i256_div",
        "obj_cmp",
        "obj_from_i128_pieces",
        "obj_from_i256_pieces",
        "obj_from_i64",
        "obj_from_u128_pieces",
        "obj_from_u256_pieces",
        "obj_from_u64",
        "obj_to_i128_hi64",
        "obj_to_i128_lo64",
        "obj_to_i256_hi_hi",
        "obj_to_i256_hi_lo",
        "obj_to_i256_lo_hi",
        "obj_to_i256_lo_lo",
        "obj_to_i64",
        "obj_to_u128_hi64",
        "obj_to_u128_lo64",
        "obj_to_u256_hi_hi",
        "obj_to_u256_hi_lo",
        "obj_to_u256_lo_hi",
        "obj_to_u256_lo_lo",
        "obj_to_u64",
        "bytes_new_from_linear_memory",
        "string_new_from_linear_memory",
        "strkey_to_address",
        "timepoint_obj_from_u64",
        "timepoint_obj_to_u64",
        "u256_div",
    }
)

#: `UNREACHED_CONTAINER_HOST_FNS` names, with a reason, every inventory member
#: no recognition ROW reaches. Four of them are still reachable -- from
#: `len()`/subscript rather than from a row -- and five are unreachable by
#: ruling. The split is spelled out here so that a change to the recognizer's
#: table breaks this test loudly (the union is asserted below) instead of
#: silently leaving a callback unbound.
_UNREACHED_BUT_STILL_EMITTED = frozenset({"vec_len", "map_len", "bytes_len", "bytes_get"})
_UNREACHABLE_BY_RULING = frozenset(
    {"vec_front", "vec_back", "vec_last_index_of", "string_len", "symbol_len"}
)


def test_the_unreached_split_still_covers_the_recognizers_own_table() -> None:
    assert _UNREACHED_BUT_STILL_EMITTED | _UNREACHABLE_BY_RULING == set(
        UNREACHED_CONTAINER_HOST_FNS
    )


def test_the_bindings_cover_every_host_function_the_compiler_can_emit() -> None:
    """The completion criterion, stated against the compiler's own tables.

    `ENV_HOST_FN_TARGETS` and `CONTAINER_HOST_FN_TARGETS` are the recognizer's
    record of every host function an authoring surface can reach;
    `_UNREACHED_BUT_STILL_EMITTED` adds the four `len()`/subscript ones, and
    `_EMITTER_ADDITIONS` the ones D inserts on its own. Their union is what
    "the full mini-host" means, and it is computed from the compiler rather
    than restated, so a new lowering that reaches a new host function fails
    HERE rather than in Task 13's differential run.
    """
    required = (
        ENV_HOST_FN_TARGETS
        | CONTAINER_HOST_FN_TARGETS
        | _UNREACHED_BUT_STILL_EMITTED
        | _EMITTER_ADDITIONS
    )
    assert sorted(required - set(FullHost().bindings())) == []


def test_the_bindings_omit_what_no_lowering_can_reach() -> None:
    """Not binding a name is a real check, not an omission (`i256.py`'s
    `rem_euclid` discipline): a lowering that reached for one of these could
    not even link, which is a loud failure at instantiation instead of a
    plausible answer from a callback nobody meant to write.

    `vec_front`/`vec_back`/`vec_last_index_of` have no authoring surface, and
    `len()` of a `String`/`Symbol` is a compile reject (MJ-1) -- both per
    `UNREACHED_CONTAINER_HOST_FNS`. `{i,u}256_rem_euclid` contradict A4's
    dividend-signed `%` (F.1.2), which is why `i256.py` refuses them.
    """
    bound = set(FullHost().bindings())
    assert sorted(_UNREACHABLE_BY_RULING & bound) == []
    assert sorted({"i256_rem_euclid", "u256_rem_euclid"} & bound) == []


# --- the Task 13 inventory ---------------------------------------------------


#: The predicate Task 13's differential run uses, stated here too because this
#: test is what proves that run can have an EMPTY skip list.
def _in_scope(case: SemCase) -> bool:
    return (
        case.kind in {"value", "contract_error", "trap"}
        and not case.tier1_only
        and case.frontend != "not_expressible"
    )


#: The whole-contract fixtures Task 13 builds (M1-E added the fifth,
#: `token_style_canonical.py`, with the canonical publish spelling, and then the
#: three `examples/` contracts of sub-plan G's wave 1). The two sandbox contracts
#: are read from `sandbox/` -- the same source Task 13 promotes into
#: `tests/fixtures/` (F.2.8) -- because `sandbox/` itself must not be touched.
#:
#: This is this FILE's own inventory, deliberately not
#: `test_emitter_end_to_end.py`'s `FIXTURES` (review M8): the question here is
#: "does `FullHost` bind every callback these modules need", which is about the
#: callback table, and it is answered for `sandbox/`'s own two files rather than
#: for the promoted copies. Adding a contract anywhere means adding it here too.
_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = (
    _ROOT / "tests" / "fixtures" / "token_style.py",
    _ROOT / "tests" / "fixtures" / "token_style_canonical.py",
    _ROOT / "tests" / "fixtures" / "spike1_reauthored.py",
    _ROOT / "sandbox" / "counter.py",
    _ROOT / "sandbox" / "hello_world.py",
    _ROOT / "examples" / "counter.py",
    _ROOT / "examples" / "errors.py",
    _ROOT / "examples" / "structs.py",
)


def _fixture_id(path: Path) -> str:
    """The test id: the path RELATIVE TO THE REPO ROOT, not the bare filename.

    `sandbox/counter.py` and `examples/counter.py` are two different files with
    the same name, and a bare-name id would leave pytest disambiguating them as
    `counter.py0`/`counter.py1` -- which says nothing about which file failed.
    """
    return str(path.relative_to(_ROOT))


def _needed(source: str, path: str) -> set[str]:
    """Every host function one module needs a callback for: the frontend's
    conservative `host_fns_reachable` UNION the names the emitter actually
    emitted as imports. Both, because neither contains the other --
    `host_fns_reachable` over-approximates the alternatives D may pick, and D
    adds box/unbox bridges the frontend never names."""
    compiled = compile_module(source, path)
    built = build_wasm(compiled, validate_external=False)
    return set(compiled.host_fns_reachable) | set(built.imports)


@pytest.mark.parametrize("path", _FIXTURES, ids=_fixture_id)
def test_every_task13_fixture_is_fully_bound(path: Path) -> None:
    missing = sorted(
        _needed(path.read_text(encoding="utf-8"), str(path)) - set(FullHost().bindings())
    )
    assert missing == []


def test_every_in_scope_semantics_case_is_fully_bound() -> None:
    """The same check for the semantics table's in-scope rows, compiled through
    the wrapper Task 13 uses. Any name missing here is a Task 13 skip, and
    "the skip list must be EMPTY" is the plan's requirement -- so it is caught
    here, in the task that owns the callbacks."""
    bound = set(FullHost().bindings())
    missing: dict[str, list[str]] = {}
    in_scope = [case for case in CASES if _in_scope(case)]
    assert len(in_scope) == 35
    for case in in_scope:
        gap = sorted(_needed(wrap_case(case.source), f"semantics/{case.name}.py") - bound)
        if gap:
            missing[case.name] = gap
    assert missing == {}


@pytest.mark.parametrize("path", _FIXTURES, ids=_fixture_id)
def test_every_task13_fixture_instantiates_under_the_full_host(path: Path) -> None:
    """The stronger half of the inventory: the module actually LINKS.

    A name check catches a missing callback; only instantiation catches a
    callback bound with the wrong arity or the wrong signature, because
    `MiniHost._bind` takes both from the pin and wasmtime refuses the link if
    the import entry disagrees. Every one of the four contracts Task 13 runs is
    instantiated here, so a signature mistake surfaces in the task that owns
    the callbacks rather than in the differential run.

    Behaviour is deliberately NOT asserted here -- that is Task 13's step 2.
    """
    built = build_wasm(
        compile_module(path.read_text(encoding="utf-8"), str(path)), validate_external=False
    )
    host = FullHost()
    host.attach(engine.MiniHost(built.wasm, imports=host.bindings()))
