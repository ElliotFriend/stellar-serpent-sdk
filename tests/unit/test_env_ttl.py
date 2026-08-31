"""The tier-1 TTL model: the honestly-modellable half of S8, and its named gaps.

Every assertion here is about a MODEL, never about the chain (`serpent.env`'s
header docstring: silent false green is this model's failure mode, and sub-plan
F's tier 2b is the gate). TTL is the sharpest case of that in the whole module,
because the model is deliberately PARTIAL: it owns the arithmetic whose inputs
it has (never-reduce, the threshold guard, expiry against a sequence a test can
advance) and refuses the two rules whose input is a host fact M1 cannot read
(`get_max_live_until_ledger` is M2) -- so this file pins the refusals as
`pytest.skip`s next to the tests, which is what makes the gap ENUMERABLE
(`pytest -rs` lists them) instead of silent.

S8's five rules, verbatim, and where each one is in this file:

* **extensions never reduce** -- modelled; `test_an_extension_never_reduces`;
* **the `threshold` guard** -- modelled, both sides;
  `test_the_threshold_guard_*`;
* **extending a dead entry errors** -- modelled for BOTH deaths a fixed-then-
  advanced sequence can produce: the never-written key and the expired entry
  (`test_extend_ttl_on_*`). It is a real test, not a skip: an expired entry
  reads absent at tier 1, and "extending something that is not there errors" is
  the same answer the chain gives (a lapsed temporary entry is deleted, a
  lapsed persistent entry is archived, and the host refuses an extension of
  either);
* **persistent extension past max clamps** -- NOT modelled; the skip;
* **temporary extension past max traps** -- NOT modelled; the skip.

The `-1` in S8's "live-until arithmetic carries `-1`" is the host's own
off-by-one convention on the wire, not an observable of this model: tier 1
compares `sequence > live_until` and never encodes a live-until into a `Val`.

Every env comes from `deployed_env()` (`conftest.py`): storage is refused
outside an invocation frame and a frame is refused before `deploy` (ruling
E7(ii)), so a TTL test needs a deployed contract to have storage at all.
`advance` is the exception, and deliberately so -- it is a test hook, not a host
call, so it answers with or without a frame.
"""

import pytest

from serpent import U32, U64, Bool, Symbol
from serpent.env import DEFAULT_LEDGER_SEQUENCE, DEFAULT_LEDGER_TIMESTAMP
from serpent.errors import MissingValue
from tests.unit.conftest import deployed_env

KEY = Symbol("k")
OTHER = Symbol("other")


# --- what a fresh entry is -------------------------------------------------


def test_a_never_extended_entry_never_expires() -> None:
    """`live_until=None` is "immortal until first extended" -- the model choice
    review M14 asked for, and the one that keeps every algebra `None`-safe."""
    env = deployed_env()
    env.storage().persistent().set(KEY, U32(1))
    env.advance(10_000_000)
    assert env.storage().persistent().get(KEY, U32) == U32(1)
    assert env.storage().persistent().has(KEY) == Bool(True)


def test_a_never_extended_entry_takes_the_first_extension_however_small_the_threshold() -> None:
    """The `None` guard rule: a never-extended entry's remaining lifetime is
    unknowable at tier 1, so the threshold guard always PASSES and the first
    extension always applies (documented model choice, not a host fact)."""
    env = deployed_env()
    env.storage().persistent().set(KEY, U32(1))
    env.storage().persistent().extend_ttl(KEY, U32(0), U32(100))
    env.advance(100)
    assert env.storage().persistent().has(KEY) == Bool(True)
    env.advance(1)
    assert env.storage().persistent().has(KEY) == Bool(False)


# --- the algebra: never-reduce and the threshold guard ---------------------


def test_an_extension_never_reduces() -> None:
    """S8's never-reduce: a smaller `extend_to` after a larger one cannot pull
    the live-until back, even when the threshold lets the call through."""
    env = deployed_env()
    env.storage().persistent().set(KEY, U32(1))
    env.storage().persistent().extend_ttl(KEY, U32(0), U32(1_000))
    env.storage().persistent().extend_ttl(KEY, U32(4_000_000_000), U32(10))
    env.advance(1_000)
    assert env.storage().persistent().has(KEY) == Bool(True)
    env.advance(1)
    assert env.storage().persistent().has(KEY) == Bool(False)


def test_the_threshold_guard_refuses_when_enough_lifetime_remains() -> None:
    """`live_until - sequence < threshold` is the whole guard: 1_000 ledgers
    remaining against a threshold of 100 is a no-op, not an extension."""
    env = deployed_env()
    env.storage().temporary().set(KEY, U32(1))
    env.storage().temporary().extend_ttl(KEY, U32(0), U32(1_000))
    env.storage().temporary().extend_ttl(KEY, U32(100), U32(5_000))
    env.advance(1_001)
    assert env.storage().temporary().has(KEY) == Bool(False)


def test_the_threshold_guard_lets_the_extension_through_when_lifetime_is_short() -> None:
    env = deployed_env()
    env.storage().temporary().set(KEY, U32(1))
    env.storage().temporary().extend_ttl(KEY, U32(0), U32(1_000))
    env.storage().temporary().extend_ttl(KEY, U32(2_000), U32(5_000))
    env.advance(1_001)
    assert env.storage().temporary().has(KEY) == Bool(True)
    env.advance(5_000 - 1_001)
    assert env.storage().temporary().has(KEY) == Bool(True)
    env.advance(1)
    assert env.storage().temporary().has(KEY) == Bool(False)


def test_the_guard_compares_against_the_advanced_sequence() -> None:
    """The extension is measured from the CURRENT sequence, not the one the
    `Env` was constructed with -- `advance` moves the number the algebra reads."""
    env = deployed_env()
    env.storage().persistent().set(KEY, U32(1))
    env.advance(500)
    env.storage().persistent().extend_ttl(KEY, U32(0), U32(100))
    env.advance(100)
    assert env.storage().persistent().has(KEY) == Bool(True)
    env.advance(1)
    assert env.storage().persistent().has(KEY) == Bool(False)


# --- lazy expiry in get/has ------------------------------------------------


def test_an_expired_entry_reads_as_absent() -> None:
    """Expiry-on-advance: `get` raises `MissingValue`, `get(default=)` returns
    the default, `has` is `Bool(False)`. Exactly the miss path, no new code."""
    env = deployed_env()
    env.storage().persistent().set(KEY, U32(7))
    env.storage().persistent().extend_ttl(KEY, U32(0), U32(10))
    env.advance(11)
    assert env.storage().persistent().has(KEY) == Bool(False)
    assert env.storage().persistent().get(KEY, U32, default=U32(0)) == U32(0)
    with pytest.raises(MissingValue, match="persistent"):
        env.storage().persistent().get(KEY, U32)


def test_expiry_is_strictly_past_the_live_until_ledger() -> None:
    """`sequence > live_until`, not `>=`: the live-until ledger is the last one
    on which the entry is still live."""
    env = deployed_env()
    env.storage().temporary().set(KEY, U32(7))
    env.storage().temporary().extend_ttl(KEY, U32(0), U32(10))
    env.advance(10)
    assert env.storage().temporary().get(KEY, U32) == U32(7)


@pytest.mark.parametrize("durability", ["persistent", "temporary"])
def test_a_re_set_revives_an_expired_entry(durability: str) -> None:
    """A re-set is a fresh entry: `live_until` goes back to `None`, so the
    revived entry is immortal-until-extended again.

    A tier-1 CONVENIENCE for the persistent case, and named as one: on chain a
    lapsed persistent entry is ARCHIVED and the host refuses a write to it
    until it is restored, and a lapsed temporary entry is gone for good. The
    model has no archive and no restore, so a re-set is how a test gets a live
    entry back. Sub-plan F's tier 2b is where the real answer lives.
    """
    env = deployed_env()
    bucket = getattr(env.storage(), durability)()
    bucket.set(KEY, U32(7))
    bucket.extend_ttl(KEY, U32(0), U32(10))
    env.advance(11)
    assert bucket.has(KEY) == Bool(False)
    bucket.set(KEY, U32(8))
    assert bucket.has(KEY) == Bool(True)
    assert bucket.get(KEY, U32) == U32(8)
    env.advance(10_000_000)
    assert bucket.get(KEY, U32) == U32(8)


def test_an_expired_entry_does_not_leak_into_another_key() -> None:
    """Expiry is per entry, and the per-key live-until map is keyed the same
    way the store is -- one key's death is not another's."""
    env = deployed_env()
    env.storage().persistent().set(KEY, U32(1))
    env.storage().persistent().set(OTHER, U32(2))
    env.storage().persistent().extend_ttl(KEY, U32(0), U32(10))
    env.advance(11)
    assert env.storage().persistent().has(KEY) == Bool(False)
    assert env.storage().persistent().has(OTHER) == Bool(True)


# --- S8's dead-entry rule: both deaths tier 1 can produce ------------------


@pytest.mark.parametrize("durability", ["persistent", "temporary"])
def test_extend_ttl_on_a_never_written_key_errors(durability: str) -> None:
    """S8's "extending a dead entry errors", for the never-written case -- the
    one dead-entry death a fixed-sequence model owns outright. LOUD, unlike
    `del_`'s absent-key no-op."""
    env = deployed_env()
    bucket = getattr(env.storage(), durability)()
    with pytest.raises(MissingValue, match="extend"):
        bucket.extend_ttl(KEY, U32(0), U32(100))


@pytest.mark.parametrize("durability", ["persistent", "temporary"])
def test_extend_ttl_on_an_expired_entry_errors(durability: str) -> None:
    """The second death: an entry that HAS expired. It reads absent everywhere
    else in the model, so `extend_ttl` gives the same answer as for a key that
    was never written -- which is also the chain's answer (a lapsed temporary
    entry is deleted; a lapsed persistent entry is archived and must be
    restored, not extended)."""
    env = deployed_env()
    bucket = getattr(env.storage(), durability)()
    bucket.set(KEY, U32(1))
    bucket.extend_ttl(KEY, U32(0), U32(10))
    env.advance(11)
    with pytest.raises(MissingValue, match="extend"):
        bucket.extend_ttl(KEY, U32(0), U32(100))


def test_a_deleted_key_cannot_be_extended() -> None:
    env = deployed_env()
    env.storage().persistent().set(KEY, U32(1))
    env.storage().persistent().del_(KEY)
    with pytest.raises(MissingValue, match="extend"):
        env.storage().persistent().extend_ttl(KEY, U32(0), U32(100))


def test_a_deleted_key_does_not_keep_its_live_until() -> None:
    """`del_` drops the live-until with the value, so a later `set` under the
    same key is a genuinely fresh entry, not a resurrected expiry."""
    env = deployed_env()
    env.storage().persistent().set(KEY, U32(1))
    env.storage().persistent().extend_ttl(KEY, U32(0), U32(10))
    env.storage().persistent().del_(KEY)
    env.storage().persistent().set(KEY, U32(2))
    env.advance(11)
    assert env.storage().persistent().get(KEY, U32) == U32(2)


# --- the instance bucket: ONE live-until, no key ---------------------------


def test_the_instance_bucket_has_one_bucket_wide_live_until() -> None:
    """S7: instance storage is a sub-map in the instance entry with ONE shared
    TTL, so `extend_ttl` takes no key and expiry takes the whole sub-map."""
    env = deployed_env()
    env.storage().instance().set(KEY, U32(1))
    env.storage().instance().set(OTHER, U32(2))
    env.storage().instance().extend_ttl(U32(0), U32(10))
    env.advance(10)
    assert env.storage().instance().has(KEY) == Bool(True)
    assert env.storage().instance().has(OTHER) == Bool(True)
    env.advance(1)
    assert env.storage().instance().has(KEY) == Bool(False)
    assert env.storage().instance().has(OTHER) == Bool(False)


def test_the_instance_bucket_can_be_extended_with_no_entries() -> None:
    """The instance entry itself exists once the contract is deployed, so a
    keyless extension is valid even when the sub-map is empty -- and it governs
    entries written AFTERWARDS, because there is only one live-until."""
    env = deployed_env()
    env.storage().instance().extend_ttl(U32(0), U32(10))
    env.storage().instance().set(KEY, U32(1))
    assert env.storage().instance().get(KEY, U32) == U32(1)
    env.advance(11)
    assert env.storage().instance().has(KEY) == Bool(False)


def test_a_set_does_not_revive_an_expired_instance_bucket() -> None:
    """The one place a re-set does NOT revive: there is no per-key live-until
    in the instance sub-map to reset, and writing one key cannot honestly
    resurrect the whole instance entry. (On chain an archived instance entry
    means the invocation does not run at all.)"""
    env = deployed_env()
    env.storage().instance().set(KEY, U32(1))
    env.storage().instance().extend_ttl(U32(0), U32(10))
    env.advance(11)
    env.storage().instance().set(KEY, U32(2))
    assert env.storage().instance().has(KEY) == Bool(False)


def test_extend_ttl_on_an_expired_instance_bucket_errors() -> None:
    """The dead-entry rule again, for the keyless bucket: once the instance
    entry's TTL has lapsed the model refuses to extend it, rather than quietly
    reviving a contract the chain would have archived."""
    env = deployed_env()
    env.storage().instance().extend_ttl(U32(0), U32(10))
    env.advance(11)
    with pytest.raises(MissingValue, match="extend"):
        env.storage().instance().extend_ttl(U32(0), U32(100))


def test_the_instance_live_until_is_separate_from_the_other_buckets() -> None:
    env = deployed_env()
    env.storage().instance().set(KEY, U32(1))
    env.storage().persistent().set(KEY, U32(2))
    env.storage().temporary().set(KEY, U32(3))
    env.storage().instance().extend_ttl(U32(0), U32(10))
    env.advance(11)
    assert env.storage().instance().has(KEY) == Bool(False)
    assert env.storage().persistent().get(KEY, U32) == U32(2)
    assert env.storage().temporary().get(KEY, U32) == U32(3)


def test_the_instance_algebra_is_the_same_algebra() -> None:
    """Never-reduce and the threshold guard, bucket-wide."""
    env = deployed_env()
    env.storage().instance().extend_ttl(U32(0), U32(1_000))
    env.storage().instance().extend_ttl(U32(4_000_000_000), U32(10))
    env.storage().instance().set(KEY, U32(1))
    env.advance(1_000)
    assert env.storage().instance().has(KEY) == Bool(True)
    env.advance(1)
    assert env.storage().instance().has(KEY) == Bool(False)


# --- no clamp, no trap: `extend_to` is taken as given ---------------------


@pytest.mark.parametrize("durability", ["persistent", "temporary"])
def test_an_extend_to_above_any_bound_is_accepted_as_is(durability: str) -> None:
    """No clamp and no trap: the model has no maximum live-until to compare
    against (`get_max_live_until_ledger` is an M2 host fact), so the largest
    `U32` is simply applied -- for the TEMPORARY bucket too, where the chain
    would trap. This is the model's loudest gap, and the two skips below are
    the enumerable record of it."""
    env = deployed_env()
    bucket = getattr(env.storage(), durability)()
    bucket.set(KEY, U32(1))
    bucket.extend_ttl(KEY, U32(0), U32(0xFFFF_FFFF))
    env.advance(0xFFFF_FFFF)
    assert bucket.has(KEY) == Bool(True)


_UNMODELLED = (
    "clamp and trap are unmodelled at every tier -- the maximum live-until is "
    "get_max_live_until_ledger, an M2 host fact; F's tier-2b proves them"
)


def test_persistent_extension_past_the_maximum_clamps() -> None:
    """S8, rule 3, NOT MODELLED. On chain a persistent extension past the
    network's maximum live-until is clamped to that maximum. Tier 1 has no
    maximum to clamp to, and a serpent-chosen constant would be a guess of
    exactly the kind this repo refuses; a named carried obligation to sub-plan
    F instead."""
    pytest.skip(_UNMODELLED)


def test_temporary_extension_past_the_maximum_traps() -> None:
    """S8, rule 4, NOT MODELLED. On chain a temporary extension past the
    maximum TRAPS rather than clamping -- the asymmetry that makes a green
    tier-1 test over a big `extend_to` the most dangerous shape in this file.
    Same missing host fact, same carried obligation to sub-plan F."""
    pytest.skip(_UNMODELLED)


# --- the `advance` test hook ----------------------------------------------


def test_advance_moves_the_sequence_and_leaves_the_timestamp_alone() -> None:
    """Deliberate: ledger close time is not a protocol constant the model may
    invent, so `advance` moves only the number TTL is measured against."""
    env = deployed_env()
    env.advance(5)
    assert env.ledger().sequence() == U32(DEFAULT_LEDGER_SEQUENCE + 5)
    assert env.ledger().timestamp() == U64(DEFAULT_LEDGER_TIMESTAMP)


def test_advance_accumulates() -> None:
    env = deployed_env(sequence=10)
    env.advance(1)
    env.advance(2)
    assert env.ledger().sequence() == U32(13)


@pytest.mark.parametrize("n", [0, -1])
def test_advance_refuses_a_non_positive_n(n: int) -> None:
    """The ledger sequence does not go backwards, and a zero advance is a
    test-authoring mistake worth naming. A plain `ValueError`, not a
    `ContractError`: no contract can reach `advance`."""
    env = deployed_env()
    with pytest.raises(ValueError, match="positive"):
        env.advance(n)


def test_advance_refuses_a_bool() -> None:
    """`bool` is an `int` to both Python and mypy, so the guard is a runtime
    one: `advance(True)` is a typo, not an advance by one ledger."""
    env = deployed_env()
    with pytest.raises(TypeError, match="int"):
        env.advance(True)


def test_a_bucket_held_across_an_advance_sees_the_new_sequence() -> None:
    """The sequence lives in ONE place: a bucket handed out before the advance
    is not holding a stale snapshot of it."""
    env = deployed_env()
    bucket = env.storage().persistent()
    bucket.set(KEY, U32(1))
    bucket.extend_ttl(KEY, U32(0), U32(10))
    env.advance(11)
    assert bucket.has(KEY) == Bool(False)


def test_a_ledger_held_across_an_advance_sees_the_new_sequence() -> None:
    """The same snapshot hazard `_TtlState`'s docstring names, on the OTHER
    reader of the same number.

    `env.ledger()` used to hand `Ledger` an `int` copy of the sequence, so a
    `Ledger` bound before an `advance` answered the pre-advance number while
    every expiry comparison used the moved one -- two readers of one ledger
    disagreeing about which ledger it is. A test that binds `led =
    env.ledger()` once and advances between assertions is the normal way to
    write this, exactly as `test_a_bucket_held_across_an_advance_sees_the_new_
    sequence` is for a bucket.
    """
    env = deployed_env()
    led = env.ledger()
    assert led.sequence() == U32(DEFAULT_LEDGER_SEQUENCE)
    env.advance(5)
    assert led.sequence() == U32(DEFAULT_LEDGER_SEQUENCE + 5)
    # And it still agrees with a Ledger taken AFTER the advance.
    assert led.sequence() == env.ledger().sequence()


def test_two_envs_do_not_share_ttl_state() -> None:
    """Two `Env`s are two unrelated contracts, so one's expiry is invisible to
    the other.

    Both frames are opened explicitly and one at a time: two envs framed at once
    is what the model refuses (there is no cross-contract call in M1), which is
    also why `advance` is called from OUTSIDE a frame here -- it is a test hook,
    not a host call.
    """
    first = deployed_env(frame=False)
    second = deployed_env(frame=False)
    with first.frame():
        first.storage().persistent().set(KEY, U32(1))
        first.storage().persistent().extend_ttl(KEY, U32(0), U32(10))
    first.advance(11)
    with second.frame():
        second.storage().persistent().set(KEY, U32(1))
        assert second.storage().persistent().has(KEY) == Bool(True)
        assert second.ledger().sequence() == U32(DEFAULT_LEDGER_SEQUENCE)
    with first.frame():
        assert first.storage().persistent().has(KEY) == Bool(False)
