"""Tier 2b: the 62 `ENV_SCENARIOS` rows on the REAL host (dossier O16, rulings E8/E9/E10).

The mini host cannot run the TTL, auth-args, and allow-set rows (`mini_host_gap`);
the real host runs ALL 62. Three comparisons per row, in this order (O28):

1. real vs tier 1 -- equal, unless the row DECLARES a divergence (`host_diverges`),
   in which case the real outcome must match the declaration and DIFFER from tier 1;
2. real vs the row's pinned expectation (`kind`/`expect`/`code`);
3. the row's `events`/`auths` pins, against the real host's records.

An undeclared real-vs-tier-1 mismatch raises `FrozenTableDisagreement` (E10): the
implementer returns BLOCKED and the controller rules. Nobody edits a row's values,
the tier-1 model, or a declaration to make this green.

**What a row needs from the façade beyond one invocation.** A scenario is a
SEQUENCE -- setup calls, then the observable -- and its `events`/`auths` are pinned
over the whole of it, while the raw host reports only the last invocation. That is
what `RealContract.events_for_sequence()`/`auths_for_sequence()` accumulate, and
what `add_mock_auths` exists for: in allow-set mode a `require_auth_for_args` call
authorizes CUSTOM args, which no per-call entry can match. All three are covered
directly at the foot of this file, because the table rows alone exercise
`add_mock_auths` only with an empty entry list.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from serpent import U32, Address, Bool, Symbol, Vec
from serpent.env import (
    DEFAULT_LEDGER_SEQUENCE,
    DEFAULT_LEDGER_TIMESTAMP,
    ChainValue,
    RecordedAuth,
)
from serpent.testing import (
    FrozenTableDisagreement,
    RealContractError,
    RealEnv,
    RealHostError,
)
from tests.semantics.env_scenarios import (
    ENV_SCENARIOS,
    ENV_SURFACE,
    SHAPES_CONTRACT,
    Advance,
    EnvScenario,
)
from tests.unit.test_env_differential import Outcome, _tier_1

real = pytest.mark.real_host  # per-test (M12): the table meta-tests run on every checkout


def _real(scenario: EnvScenario) -> Outcome:
    """Replay `scenario` against the embedded real host.

    Deliberately the same shape as `test_env_differential._tier_1`, step for
    step: the `Env` config comes from the row, the setup is replayed in order
    (an `Advance` moves the embedded host's ledger, which is exactly what the
    mini host could not do), and the ONE observable call is made last.
    """
    if scenario.real_unrunnable is not None:
        # LOUDLY (finding F3): a skip is counted in the summary and prints with
        # `-rs`, so "no real leg for this row" stays enumerable instead of
        # becoming a row nobody notices is missing. Raised from the replay
        # helper rather than the test body so no ordering mistake can run the
        # host first and panic.
        pytest.skip(f"{scenario.name}: {scenario.real_unrunnable}")
    env = RealEnv(
        timestamp=DEFAULT_LEDGER_TIMESTAMP if scenario.timestamp is None else scenario.timestamp,
        sequence=DEFAULT_LEDGER_SEQUENCE if scenario.sequence is None else scenario.sequence,
        auths=scenario.auth_allow_set,
    )
    c = env.deploy_source(scenario.contract, *scenario.constructor)
    if scenario.auth_allow_set is not None:
        # In allow-set mode the façade's per-call entry carries the
        # INVOCATION's args, which is the shape a bare `require_auth` needs. A
        # `require_auth_for_args` call authorizes something else entirely, so
        # the args the row expects to be recorded are registered here as
        # pending mock auths. (With `auths=None` the host mocks every
        # authorization and no entry is needed at all.)
        c.add_mock_auths([(who, args) for who, args in scenario.auths if args is not None])
    for step in scenario.setup:
        if isinstance(step, Advance):
            env.advance(step.ledgers)
        else:
            c.invoke(step.method, *step.args)
    call = scenario.invoke
    answer: ChainValue | None = None
    code: int | None = None
    refused = False
    trapped = False
    if scenario.kind == "contract_error":
        with pytest.raises(RealContractError) as failed:
            c.invoke(call.method, *call.args)
        code = failed.value.code
    elif scenario.kind == "auth_failed":
        with pytest.raises(RealHostError) as refusal:
            c.invoke(call.method, *call.args)
        assert not isinstance(refusal.value, RealContractError)
        underlying = refusal.value.underlying
        assert underlying is not None and underlying[0] == "Auth", refusal.value
        refused = True
    elif scenario.kind == "host_error":
        # Finding F2: a TRAP, so a `RealHostError` that is NOT a
        # `RealContractError` -- and the evidence is the UNDERLYING pair, since
        # the frame level is `("Context", 6)` for every guest-side failure (B5).
        with pytest.raises(RealHostError) as trap:
            c.invoke(call.method, *call.args)
        assert not isinstance(trap.value, RealContractError), trap.value
        assert trap.value.underlying == scenario.host_error, (
            f"{scenario.name}: the host reported underlying {trap.value.underlying!r}, "
            f"the row pins {scenario.host_error!r}"
        )
        trapped = True
    else:
        answer = cast("ChainValue | None", c.invoke(call.method, *call.args))
    auths = c.auths_for_sequence()  # every auth recorded across setup + invoke, in order
    return Outcome(
        answer=answer,
        answer_type=type(answer).__name__,
        code=code,
        refused=refused,
        events=c.events_for_sequence(),
        auth_addresses=tuple(a for a, _ in auths),
        auths=auths,
        trapped=trapped,
    )


def _blanked_against(
    real_auths: tuple[RecordedAuth, ...], pinned: tuple[RecordedAuth, ...]
) -> tuple[RecordedAuth, ...]:
    """`real_auths` with its args dropped wherever `pinned` records `None`.

    The real host always records an argument list (the invocation's own, even
    for a bare `require_auth`); the table and tier 1 write `None` where only the
    address is claimed. This is the one translation between the two
    vocabularies, and it is deliberately shared by the tier-1 comparison and the
    declared-auths assertion so they cannot drift. A length mismatch is left to
    the caller, which is where the two have different things to say about it.
    """
    return tuple(
        (address, None if pinned_args is None else real_args)
        for (address, real_args), (_, pinned_args) in zip(real_auths, pinned, strict=False)
    )


def _comparable_to_tier1(
    scenario: EnvScenario, real_outcome: Outcome, tier1: Outcome
) -> tuple[Outcome, Outcome]:
    """Tier 1 records `None` args for a bare `require_auth`; the host records the
    invocation's own args there. Compare addresses always; compare args only where
    tier 1 recorded some (a `require_auth_for_args` call), by blanking the real
    leg's args wherever tier 1's are `None`.

    Both legs record one entry per successful `require_auth*` call in invocation
    order, deploy excluded (M8) and refused calls excluded (O19) -- so the lengths
    agree unless the accumulation itself diverges, which is a table matter, not a
    helper's assert (review M13).
    """
    if len(real_outcome.auths) != len(tier1.auths):
        raise FrozenTableDisagreement(
            f"{scenario.name}: real recorded {len(real_outcome.auths)} auths "
            f"{real_outcome.auths!r}, tier 1 recorded {len(tier1.auths)} {tier1.auths!r}; "
            "controller decision required (O17/O19)"
        )
    return replace(real_outcome, auths=_blanked_against(real_outcome.auths, tier1.auths)), tier1


@real
@pytest.mark.parametrize("scenario", ENV_SCENARIOS, ids=[s.name for s in ENV_SCENARIOS])
def test_a_row_answers_on_the_real_host_as_tier_1_does(scenario: EnvScenario) -> None:
    real_outcome = _real(scenario)
    tier1 = _tier_1(scenario)
    divergence = scenario.host_diverges
    if divergence is None:
        r, t = _comparable_to_tier1(scenario, real_outcome, tier1)
        if r != t:
            raise FrozenTableDisagreement(
                f"{scenario.name}: real {real_outcome!r} != tier 1 {tier1!r}; "
                "controller decision required"
            )
    else:
        assert real_outcome.events == divergence.events, divergence.reason
        if divergence.answer is not None:
            assert real_outcome.answer == divergence.answer, divergence.reason
            assert type(real_outcome.answer).__name__ == type(divergence.answer).__name__
        if divergence.auths is not None:
            assert _blanked_against(real_outcome.auths, divergence.auths) == divergence.auths, (
                f"{scenario.name}: real auths {real_outcome.auths!r} do not match the "
                f"declared {divergence.auths!r} -- {divergence.reason}"
            )
        # The divergence must still EXIST: a model fix that removes it retires
        # the declaration loudly rather than leaving a green test asserting a
        # difference that is no longer there (ruling E9). All three facets
        # count -- M3's archival rows differ in the ANSWER with no event in
        # sight, and F5's row differs only in the AUTHS.
        real_facets = (real_outcome.events, real_outcome.answer, real_outcome.auth_addresses)
        tier1_facets = (tier1.events, tier1.answer, tier1.auth_addresses)
        assert real_facets != tier1_facets, (
            f"{scenario.name}: the declared divergence did not occur; retire the declaration"
        )

    if scenario.kind == "value":
        # A row that declares an answer is pinned to THE DECLARATION here; its
        # `expect` stays the model's, which is the half `test_env_differential`
        # asserts.
        expected = scenario.expect if divergence is None or divergence.answer is None else None
        if expected is not None:
            assert real_outcome.answer == expected
            assert type(real_outcome.answer).__name__ == real_outcome.answer_type
    elif scenario.kind == "contract_error":
        assert real_outcome.code == scenario.code
    elif scenario.kind == "auth_failed":
        assert real_outcome.refused
    elif scenario.kind == "host_error":
        assert real_outcome.trapped  # the underlying pair was matched inside `_real`
    if divergence is None:
        assert real_outcome.events == scenario.events
    # The auth pin comes from the DECLARATION where there is one (F5), and from
    # the row otherwise: the row's `auths` stay the model's.
    pinned = scenario.auths if divergence is None or divergence.auths is None else divergence.auths
    assert real_outcome.auth_addresses == tuple(a for a, _ in pinned)


def test_every_row_runs_here_including_the_mini_host_gap_rows() -> None:  # unmarked: table-only
    """The point of the leg: zero rows opt out."""
    gapped = [s.name for s in ENV_SCENARIOS if s.mini_host_gap is not None]
    assert gapped, "the table has gap rows; if it stops having them, delete this assertion"
    # parametrization above covers ENV_SCENARIOS in full; this pins that no filter crept in


def test_exactly_the_two_u32_edge_rows_have_no_real_host_leg() -> None:
    """Finding F3, pinned by NAME (unmarked: this is about the table).

    `real_unrunnable` is the one way a row opts out of this leg, so which rows
    carry it is a decision and not an implementation detail. Both of these
    drive the ledger sequence to the top of the `U32` range to pin the tier-1
    model's own rough edge; a third row appearing here would be a row quietly
    losing its real leg.
    """
    assert {s.name for s in ENV_SCENARIOS if s.real_unrunnable is not None} == {
        "an_entry_whose_live_until_is_the_last_u32_ledger_is_alive_there",
        "expiry_still_answers_one_ledger_past_the_u32_range",
    }


def test_at_least_one_declared_divergence_exists() -> None:
    """Dossier F.1.1: a differential with no declared divergence has not asked the host
    anything the models did not already agree on."""
    assert any(s.host_diverges is not None for s in ENV_SCENARIOS)


def test_the_authorizer_constant_can_never_collide_with_a_deployed_contract() -> None:
    """B2's safety half, as an assertion rather than a comment.

    `mock_auths` registers a `MockAuthContract` AT the authorizer's address, so
    the authorizer must never be the address of the contract under test. It
    cannot be: `register` generates the deployed address, and `SHAPES_CONTRACT`
    is a fixed strkey from a different network's ledger. Unmarked and cheap
    where the extension is absent, marked where it is not -- so the claim is
    checked against real generated addresses too (below).
    """
    assert SHAPES_CONTRACT not in {s.contract.name for s in ENV_SCENARIOS}, (
        "sanity: the constant is a strkey, not a path"
    )


@real
def test_a_deployed_address_is_never_the_mocked_authorizer() -> None:
    """The same claim, measured: two deploys, neither at `SHAPES_CONTRACT`."""
    env = RealEnv()
    first = env.deploy_source(ENV_SURFACE)
    second = env.deploy_source(ENV_SURFACE)
    assert first.address != second.address
    assert {first.address.strkey, second.address.strkey}.isdisjoint({SHAPES_CONTRACT})


# ===========================================================================
# the three façade additions this leg needed, covered directly
# ===========================================================================
#
# `add_mock_auths`, `events_for_sequence` and `auths_for_sequence` were declared
# by Task 3 and implemented here, so their coverage lives here too. The table
# rows above drive the two accumulators hard (every multi-call row depends on
# them) but reach `add_mock_auths` only with an EMPTY entry list, because the
# one `require_auth_for_args` row runs under mock-all-auths. These three tests
# are what actually pin the method.

_ALLOWED = Address(SHAPES_CONTRACT)


@real
def test_add_mock_auths_authorizes_require_auth_for_args_custom_args() -> None:
    """The reason the method exists (M6/B2).

    `guard_args` calls `require_auth_for_args(who, Vec(U32, [amount]))`, so the
    entry the host looks for carries THOSE args -- not the invocation's
    `(who, amount)`. In allow-set mode the per-call entry the façade builds
    cannot match it, and the call is refused; a pending entry paired with the
    method being invoked is what makes it pass. Both directions are asserted in
    one test on purpose: the refusal is what makes the success mean anything.
    """
    env = RealEnv(auths=(_ALLOWED,))
    c = env.deploy_source(ENV_SURFACE)
    with pytest.raises(RealHostError) as info:
        c.invoke("guard_args", _ALLOWED, U32(9))
    assert info.value.underlying is not None and info.value.underlying[0] == "Auth"

    c.add_mock_auths([(_ALLOWED, Vec(U32, [U32(9)]))])
    assert c.invoke("guard_args", _ALLOWED, U32(9)) is None
    assert c.auths() == ((_ALLOWED, Vec(U32, [U32(9)])),)


@real
def test_a_pending_entry_survives_later_invokes_and_the_per_call_entries_do_too() -> None:
    """One complete set per invoke (M6: `mock_auths` REPLACES).

    A pending entry is registered ONCE and the bare `require_auth` call that
    follows it still works, which is the whole risk in re-setting the entry set
    per call: a façade that passed only the pending entries would refuse
    `guard`, and one that passed only the per-call entries would refuse
    `guard_args`.
    """
    env = RealEnv(auths=(_ALLOWED,))
    c = env.deploy_source(ENV_SURFACE)
    c.add_mock_auths([(_ALLOWED, Vec(U32, [U32(9)]))])
    c.invoke("guard", _ALLOWED)
    c.invoke("guard_args", _ALLOWED, U32(9))
    c.invoke("guard", _ALLOWED)
    assert c.auths_for_sequence() == (
        (_ALLOWED, Vec(Address, [_ALLOWED])),
        (_ALLOWED, Vec(U32, [U32(9)])),
        (_ALLOWED, Vec(Address, [_ALLOWED])),
    )


@real
def test_add_mock_auths_refuses_an_account_authorizer_and_the_contract_itself() -> None:
    """The two fences, at the door rather than as a host panic (B2, M6)."""
    env = RealEnv(auths=(_ALLOWED,))
    c = env.deploy_source(ENV_SURFACE)
    account = Address("GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY")
    with pytest.raises(ValueError, match="contract"):
        c.add_mock_auths([(account, Vec(U32, [U32(9)]))])
    with pytest.raises(ValueError, match="MockAuthContract"):
        c.add_mock_auths([(c.address, Vec(U32, [U32(9)]))])


@real
def test_add_mock_auths_refuses_outside_allow_set_mode() -> None:
    """With `auths=None` the host mocks EVERY authorization, and a mock entry
    set would replace that blanket mode with an enforcing one -- turning a
    registration into a refusal of every call it does not cover. Refused, with
    the fix in the message, rather than silently ignored."""
    c = RealEnv().deploy_source(ENV_SURFACE)
    with pytest.raises(ValueError, match="RealEnv\\(auths="):
        c.add_mock_auths([(_ALLOWED, Vec(U32, [U32(9)]))])


@real
def test_the_two_accumulators_span_the_sequence_where_the_raw_readers_do_not() -> None:
    """`events()`/`auths()` stay last-invocation; the `_for_sequence` pair does not.

    Measured on this host: the raw readers are strictly per-invocation -- an
    invocation that publishes nothing answers `()` even when the one before it
    published -- which is exactly why a scenario's whole-sequence pins need
    their own accumulation.
    """
    env = RealEnv()
    c = env.deploy_source(ENV_SURFACE)
    assert c.events_for_sequence() == ()
    assert c.auths_for_sequence() == ()

    c.invoke("log_declared", _ALLOWED, U32(3))
    c.invoke("guard", _ALLOWED)
    c.invoke("log_canonical", _ALLOWED, U32(4))

    logged = (Symbol("logged"), _ALLOWED)
    assert c.events() == ((logged, U32(4)),)  # the LAST invocation only
    assert c.events_for_sequence() == ((logged, U32(3)), (logged, U32(4)))
    assert c.auths() == ()  # the last invocation authorized nothing
    assert c.auths_for_sequence() == ((_ALLOWED, Vec(Address, [_ALLOWED])),)


@real
def test_a_failed_invocation_contributes_nothing_to_either_accumulator() -> None:
    """The S9 host fact, at the façade's level (review m7).

    `log_then_refuse` publishes and then raises. The host records the event with
    `failed_call: true` and the sdk's `Events::all()` drops it, so the failed
    frame contributes no event -- and no auth either, measured on the
    `token_style` over-balance path. The accumulators ASK after a failure
    anyway: the buffer is the failed invocation's own (it is not the previous
    call's, which would double-count), so the honest answer is whatever the
    host says, and today it says nothing.
    """
    env = RealEnv()
    c = env.deploy_source(ENV_SURFACE)
    c.invoke("log_declared", _ALLOWED, U32(3))
    with pytest.raises(RealContractError) as info:
        c.invoke("log_then_refuse", _ALLOWED, U32(3))
    assert info.value.code == 1
    assert c.events() == ()
    assert c.events_for_sequence() == (((Symbol("logged"), _ALLOWED), U32(3)),)
    assert c.invoke("has_instance", Symbol("K")) == Bool(False)
