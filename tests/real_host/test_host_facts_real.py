"""The real host: the `HOST_FACTS` table, asked of it (dossier D.3, E6/E12/E15).

`tests/unit/test_host_facts_tier1.py` runs the model's leg and owns the table's
meta-tests; this module runs the host's. Per row: build the `RealEnv` the row
asks for, deploy the fixture, replay the setup, make the one observable call, and
match the row's `real` expectation -- plus, for the rows that carry them, the
extras the expectation cannot hold (a TTL that must be unchanged, an event tuple
that must be empty, a footprint count).

**This is the leg that MEASURES.** Everything the table pins here was unknown
until a first run: which `ScErrorCode` a temporary extension past the maximum
reports, whether a persistent one clamps, how many write entries a single slot
write costs. Where a run and the table disagree the table is not edited to match:
a `Value`/`ContractErr` mismatch is a plain assertion failure to escalate, and an
ordering mismatch against tier 1 is a `FrozenTableDisagreement` (E10).

**Footprint COUNTS, not footprint keys** (E6). `resources()` reports how many
ledger entries an invocation read and wrote, which is enough to catch a call that
touched an entry nobody expected; WHICH keys are in the footprint is only visible
through a real transaction's `e2e_invoke` and is M2's.

**The `chain_unproven` row's extra is the RESTORE, not a tier-1 comparison.** The
brief expected that row to be two-sided (the host readable, tier 1 absent), and
measured it is not: tier 1 answers `U32(7)` too, because a never-extended entry
never lapses there (`serpent.env`'s four model choices). So the evidence that the
TEST host differs from the CHAIN is the host's own behaviour -- it hands the value
back AND restores the entry with a fresh minimum TTL, where the chain would
refuse the access until a restore footprint paid for it -- and that is what this
leg asserts.
"""

from __future__ import annotations

import pytest

from serpent.env import DEFAULT_LEDGER_SEQUENCE, DEFAULT_LEDGER_TIMESTAMP
from serpent.testing import FrozenTableDisagreement, RealContractError, RealEnv, RealHostError
from serpent.testing._real import (
    DEFAULT_MAX_ENTRY_TTL,
    DEFAULT_MIN_PERSISTENT_ENTRY_TTL,
    RealContract,
)
from serpent.types import U32, Symbol
from tests.semantics.env_scenarios import Advance
from tests.semantics.host_facts import (
    COMPARE_VECTORS,
    HOST_FACTS,
    HOST_FACTS_CONTRACT,
    ContractErr,
    HostErr,
    HostFact,
    Unmodelled,
    Value,
)
from tests.unit.test_examples import load_example

real = pytest.mark.real_host  # per-test (review M12): the table meta-tests live in the tier-1 leg

#: The fixture's own storage key, read off the module rather than re-typed here:
#: the TTL extras below index the very entry the contract wrote, and a second
#: literal is a second thing that can drift.
KEY: Symbol = load_example(HOST_FACTS_CONTRACT).KEY


def _deploy(row: HostFact) -> tuple[RealEnv, RealContract]:
    """The env and contract `row` asks for, with its setup already replayed."""
    env = RealEnv(
        timestamp=DEFAULT_LEDGER_TIMESTAMP,
        sequence=DEFAULT_LEDGER_SEQUENCE if row.sequence is None else row.sequence,
        auths=row.auth_allow_set,
    )
    c = env.deploy_source(HOST_FACTS_CONTRACT, *row.constructor)
    for step in row.setup:
        if isinstance(step, Advance):
            env.advance(step.ledgers)
        else:
            c.invoke(step.method, *step.args)
    return env, c


@real
@pytest.mark.parametrize("row", HOST_FACTS, ids=[r.name for r in HOST_FACTS])
def test_the_real_host_answers_a_row_as_the_table_records(row: HostFact) -> None:
    env, c = _deploy(row)
    expected = row.real
    if isinstance(expected, Value):
        answer = c.invoke(row.invoke.method, *row.invoke.args)
        assert answer == expected.value, f"{row.name}: the host answered {answer!r}"
        # The type name, not just the value: `Bool(True) == True` in Python, so a
        # decode that produced a plain `bool` would compare equal to the row.
        assert type(answer).__name__ == type(expected.value).__name__, (
            f"{row.name}: the host answered {type(answer).__name__}, "
            f"the row records {type(expected.value).__name__}"
        )
    elif isinstance(expected, ContractErr):
        with pytest.raises(RealContractError) as failed:
            c.invoke(row.invoke.method, *row.invoke.args)
        assert failed.value.code == expected.code
    else:
        with pytest.raises(RealHostError) as trap:
            c.invoke(row.invoke.method, *row.invoke.args)
        # NOT a `RealContractError`: the claim is that the host trapped, and a
        # contract code would mean the guest caught this and answered in band.
        assert not isinstance(trap.value, RealContractError), trap.value
        assert trap.value.underlying == expected.underlying, (
            f"{row.name}: the host reported underlying {trap.value.underlying!r}, "
            f"the row records {expected.underlying!r}"
        )
        # And the frame level is `("Context", 6)` for every guest-side failure
        # (B5) -- asserted so the row's silence about it stays a measured fact
        # rather than an assumption the table inherited.
        assert (trap.value.error_type, trap.value.code) == ("Context", 6), trap.value

    _footprint(row, c)
    _extras(row, env, c)

    if row.divergence_reason is not None:
        # The declaration must still be TRUE: the day the model is fixed (an M2
        # oracle edit), this fails and the reason is retired deliberately rather
        # than left as a green test asserting a difference that is gone.
        assert row.real != row.tier1, (
            f"{row.name}: the declared divergence is gone; retire divergence_reason "
            f"({row.divergence_reason})"
        )


def _footprint(row: HostFact, c: RealContract) -> None:
    """The E6 counts, where the row declares them."""
    if row.write_entries is None and row.read_entries is None:
        return
    resources = c.resources()
    assert resources is not None, f"{row.name}: no resources after an invocation"
    if row.write_entries is not None:
        assert resources["write_entries"] == row.write_entries, resources
    if row.read_entries is not None:
        reads = resources["memory_read_entries"] + resources["disk_read_entries"]
        assert reads == row.read_entries, resources


def _extras(row: HostFact, env: RealEnv, c: RealContract) -> None:
    """The per-row assertions an `Expectation` cannot carry.

    Dispatched on the row NAME, and that is safe here in a way it would not be
    in the table: these are assertions ABOUT named rows, so a rename breaks the
    lookup loudly (`_row` raises) instead of silently dropping a check -- and
    `test_every_row_with_an_extra_is_a_row_that_exists` pins the set against the table.
    """
    if row.name == "an_extension_whose_threshold_is_below_the_current_ttl_is_a_no_op":
        # The whole fact: the call returned Void and changed NOTHING. 4095 is the
        # fresh persistent TTL at these defaults (min_persistent_entry_ttl 4096,
        # less the current ledger -- review B10's relative quantity).
        assert c.storage("persistent").ttl(KEY) == DEFAULT_MIN_PERSISTENT_ENTRY_TTL - 1
    elif row.name == "max_live_until_is_max_entry_ttl_minus_one":
        assert env.max_ttl() == DEFAULT_MAX_ENTRY_TTL - 1
    elif row.name == "persistent_extension_past_the_maximum_clamps":
        # CLAMPED, not applied: the row asked for `max + 88_000`.
        assert c.storage("persistent").ttl(KEY) == env.max_ttl()
    elif row.name == "a_lapsed_persistent_entry_stays_readable_on_the_test_host":
        # The M3 evidence: the read answered AND the entry is alive again with a
        # fresh minimum TTL. That restore is precisely what the chain does not do.
        assert c.storage("persistent").ttl(KEY) == DEFAULT_MIN_PERSISTENT_ENTRY_TTL - 1
        assert row.chain_unproven is not None
    elif row.name == "an_event_published_before_a_raise_is_rolled_back":
        # The rollback itself. Tier 1 keeps the event
        # (`test_host_facts_tier1.test_the_event_published_before_a_raise_survives_at_tier_1`),
        # so this empty tuple is the divergence, measured.
        assert c.events() == ()
    elif row.name == "a_refused_auth_is_an_auth_trap_and_records_nothing":
        assert c.auths() == ()


#: Every row `_extras` has something to say about. Pinned as data so the
#: dispatch above cannot silently stop covering a row.
_ROWS_WITH_EXTRAS = frozenset(
    {
        "an_extension_whose_threshold_is_below_the_current_ttl_is_a_no_op",
        "max_live_until_is_max_entry_ttl_minus_one",
        "persistent_extension_past_the_maximum_clamps",
        "a_lapsed_persistent_entry_stays_readable_on_the_test_host",
        "an_event_published_before_a_raise_is_rolled_back",
        "a_refused_auth_is_an_auth_trap_and_records_nothing",
    }
)


def test_every_row_with_an_extra_is_a_row_that_exists() -> None:  # unmarked: about this module
    """`_extras` dispatches on names; this is what makes a rename loud."""
    assert _ROWS_WITH_EXTRAS <= {row.name for row in HOST_FACTS}


@real
@pytest.mark.parametrize(
    ("a", "b", "sign"), COMPARE_VECTORS, ids=[f"{i}" for i in range(len(COMPARE_VECTORS))]
)
def test_the_host_orders_a_compare_vector_as_the_table_records(
    a: object, b: object, sign: int | None
) -> None:
    """`RealEnv.compare` directly, no contract in between (review M2, E12).

    Symbol rows are also compared to TIER 1's `a < b`, and a disagreement is a
    `FrozenTableDisagreement`: `Symbol.__lt__` is a frozen decision (O12), so if
    the host disputes it the controller rules and nobody edits either side. The
    container rows have no tier-1 answer at all (A15: containers have no `<` in
    the subset), which is why the table records the host's order for them as
    E12's evidence and the tier-1 implementation is M2's.
    """
    assert sign is not None, "the table's meta-test forbids an unpinned sign"
    measured = RealEnv().compare(a, b)
    assert measured == sign, (
        f"the host compared {a!r} to {b!r} as {measured}, the table says {sign}"
    )
    if isinstance(a, Symbol) and isinstance(b, Symbol):
        tier1 = -1 if a < b else (0 if a == b else 1)
        if tier1 != measured:
            raise FrozenTableDisagreement(
                f"compare({a!r}, {b!r}): the host says {measured}, tier 1's Symbol.__lt__ "
                f"says {tier1}; controller decision required (O12/E10)"
            )


@real
def test_a_lapsed_temporary_entry_is_gone_where_tier_1_keeps_it() -> None:
    """The one two-sided divergence in the table, measured from both ends.

    The row records it (`real=U32(0)`, `tier1=U32(7)`, `divergence_reason`), and
    the parametrized test above asserts the host's half; this test asserts the
    MECHANISM -- a fresh temporary entry gets the bucket's 16-ledger floor, less
    the current ledger, and once the ledger passes it the entry is simply not
    there. Tier 1 never gets here at all: its never-extended entry has
    `live_until = None` and lives forever.
    """
    row = _row("a_lapsed_temporary_entry_reads_absent")
    _env, c = _deploy(row)  # the setup already wrote the entry and advanced 17
    assert c.storage("temporary").ttl(KEY) is None
    assert c.storage("temporary").has(KEY) is False
    assert isinstance(row.tier1, Value) and row.tier1.value is not None


@real
def test_a_fresh_entrys_ttl_is_its_buckets_floor_less_the_current_ledger() -> None:
    """The numbers every TTL row above is written against (review B10).

    Measured 2026-09-02 at the façade's defaults: 4095 persistent, 15 temporary,
    4095 instance, and `max_ttl()` one below `max_entry_ttl`. Asserted here
    rather than assumed by six rows, so a host upgrade that moved a floor fails
    ONE test with the numbers in it instead of six with none.
    """
    env = RealEnv(sequence=DEFAULT_LEDGER_SEQUENCE)
    c = env.deploy_source(HOST_FACTS_CONTRACT)
    c.invoke("put_p", U32(1))
    c.invoke("put_t", U32(1))
    assert c.storage("persistent").ttl(KEY) == 4095
    assert c.storage("temporary").ttl(KEY) == 15
    assert c.storage("instance").ttl(KEY) == 4095
    assert env.max_ttl() == 6_311_999 == DEFAULT_MAX_ENTRY_TTL - 1


def _row(name: str) -> HostFact:
    (row,) = [r for r in HOST_FACTS if r.name == name]
    return row


def test_the_unmodelled_rows_still_run_here() -> None:  # unmarked: about this leg
    """The point of the real host: a row the model cannot answer is not a row nobody
    answers. Every `Unmodelled` row is in the parametrization above, and each of
    them is a TTL-maximum fact -- the three the tier-1 leg skips."""
    unmodelled = {row.name for row in HOST_FACTS if isinstance(row.tier1, Unmodelled)}
    assert unmodelled == {
        "max_live_until_is_max_entry_ttl_minus_one",
        "persistent_extension_past_the_maximum_clamps",
        "temporary_extension_past_the_maximum_traps",
    }


def test_every_host_error_row_records_a_pair_this_leg_can_assert() -> None:  # unmarked
    """A `HostErr(None)` would make the trap branch above assert
    `underlying == None`, which passes for a host that reported no diagnostic at
    all -- the one shape that must never pass silently."""
    assert all(
        side.underlying is not None
        for row in HOST_FACTS
        for side in (row.real, row.tier1)
        if isinstance(side, HostErr)
    )
