"""The bounty board on the real host, tier 1 as the other leg (M1-G Task 4, U1).

The seventh example is the one contract that touches every M1 authoring
surface, so its sequences are the closest thing the suite has to a user's
own test file. Every test here runs the SAME steps at tier 1 (`serpent.env`)
and on the embedded real host (`serpent.testing.RealEnv`), asserts the decoded
answers, error codes, and events are EQUAL, and only then pins the literals.
Per-test `real_host` marks (M12), never a module-level `pytestmark`.
"""

from __future__ import annotations

import functools
import hashlib
from collections.abc import Callable
from typing import Any, cast

import pytest
from stellar_sdk.strkey import StrKey

from serpent import U32, Address, Bool, Symbol, Vec
from serpent.env import AuthorizationFailed, Env, deploy
from serpent.testing import RealContractError, RealEnv, RealHostError
from tests.unit.test_emitter_end_to_end import EXAMPLE_BOUNTY_BOARD
from tests.unit.test_examples import load_example

board = load_example(EXAMPLE_BOUNTY_BOARD)


def _contract_address(label: str) -> Address:
    """A deterministic CONTRACT strkey per role: the real host mocks
    authorization by registering a stand-in contract at the authorizer's
    address (ruling F-B2; account authorizers need real signatures: M2)."""
    return Address(StrKey.encode_contract(hashlib.sha256(label.encode()).digest()))


ADMIN = _contract_address("admin")
POSTER = _contract_address("poster")
WORKER = _contract_address("worker")
OUTSIDER = _contract_address("outsider")
ALLOWED = (ADMIN, POSTER, WORKER)

Step = tuple[str, tuple[Any, ...]]
Outcome = object  # a chain value, None, or ("error", code)


def _outcome(call: Callable[[], object]) -> Outcome:
    try:
        return call()
    except RealContractError as exc:
        return ("error", exc.code)
    except Exception as exc:  # tier 1: @contracterror members are exception classes
        code = getattr(type(exc), "code", None)
        if code is None:
            raise
        return ("error", code)


def _invoke_tier1(inst: Any, method: str, args: tuple[Any, ...], env: Env) -> object:
    return getattr(inst, method)(env, *args)


def _tier1(
    steps: list[Step], *, advance_before: dict[int, int] | None = None
) -> tuple[list[Outcome], Any]:
    env = Env(auths=ALLOWED)
    inst = deploy(board.BountyBoard, env, ADMIN)
    answers: list[Outcome] = []
    for n, (method, args) in enumerate(steps):
        if advance_before and n in advance_before:
            env.advance(advance_before[n])
        with env.frame():
            answers.append(_outcome(functools.partial(_invoke_tier1, inst, method, args, env)))
    return answers, env.published_events


def _real(
    steps: list[Step], *, advance_before: dict[int, int] | None = None
) -> tuple[list[Outcome], Any]:
    env = RealEnv(auths=ALLOWED)
    contract = env.deploy_source(EXAMPLE_BOUNTY_BOARD, ADMIN)
    answers: list[Outcome] = []
    for n, (method, args) in enumerate(steps):
        if advance_before and n in advance_before:
            env.advance(advance_before[n])
        answers.append(_outcome(functools.partial(contract.invoke, method, *args)))
    return answers, contract.events_for_sequence()


HAPPY_PATH: list[Step] = [
    ("post", (POSTER, U32(50), board.Priority.High)),
    ("post", (POSTER, U32(20), board.Priority.Low)),
    ("total_posted", ()),
    ("status_of", (U32(1),)),
    ("open_ids", ()),
    ("claim", (U32(1), WORKER)),
    ("status_of", (U32(1),)),
    ("worker_of", (U32(1),)),
    ("open_ids", ()),
    ("complete", (U32(1),)),
    ("status_of", (U32(1),)),
    ("priority_of", (U32(2),)),
    ("is_urgent", (U32(1),)),
    ("reward_of", (U32(2),)),
]


@pytest.mark.real_host
def test_the_happy_path_answers_the_same_on_both_legs() -> None:
    tier1, tier1_events = _tier1(HAPPY_PATH)
    real, real_events = _real(HAPPY_PATH)
    assert real == tier1
    assert real_events == tier1_events
    assert tier1 == [
        U32(1),
        U32(2),
        U32(2),
        Symbol("Open"),
        Vec(U32, [U32(1), U32(2)]),
        None,
        Symbol("Claimed"),
        WORKER,
        Vec(U32, [U32(2)]),
        U32(50),
        Symbol("Paid"),
        board.Priority.Low,
        Bool(True),
        U32(20),
    ]
    assert [topics[0] for topics, _data in tier1_events] == [
        Symbol("posted"),
        Symbol("posted"),
        Symbol("claimed"),
        Symbol("completed"),
    ]


ERRORS: list[Step] = [
    ("post", (POSTER, U32(0), board.Priority.Low)),  # ZeroReward
    ("post", (POSTER, U32(5), board.Priority.Medium)),  # id 1
    ("complete", (U32(1),)),  # NotClaimed
    ("claim", (U32(1), WORKER)),
    ("claim", (U32(1), WORKER)),  # NotOpen
    ("worker_of", (U32(99),)),  # NoSuchBounty
]


@pytest.mark.real_host
def test_every_error_code_agrees_on_both_legs() -> None:
    tier1, _ = _tier1(ERRORS)
    real, _ = _real(ERRORS)
    assert real == tier1
    assert tier1 == [("error", 5), U32(1), ("error", 3), None, ("error", 2), ("error", 1)]


EXPIRY: list[Step] = [
    ("post", (POSTER, U32(9), board.Priority.Medium)),
    ("claim", (U32(1), WORKER)),
    ("complete", (U32(1),)),
]


@pytest.mark.real_host
def test_a_claim_lapses_after_its_ttl_on_both_legs() -> None:
    """The claim is a temporary entry extended to CLAIM_TTL ledgers; one past
    that, both legs refuse with the contract's own code rather than paying."""
    past = {2: board.CLAIM_TTL.value + 1}
    tier1, _ = _tier1(EXPIRY, advance_before=past)
    real, _ = _real(EXPIRY, advance_before=past)
    assert real == tier1
    assert tier1[2] == ("error", 4)


@pytest.mark.real_host
def test_an_address_outside_the_allow_set_cannot_post() -> None:
    """Authorization is a host TRAP, not a contract error."""
    env = Env(auths=ALLOWED)
    inst = deploy(board.BountyBoard, env, ADMIN)
    with env.frame(), pytest.raises(AuthorizationFailed):
        inst.post(env, OUTSIDER, U32(1), board.Priority.Low)

    real = RealEnv(auths=ALLOWED)
    contract = real.deploy_source(EXAMPLE_BOUNTY_BOARD, ADMIN)
    with pytest.raises(RealHostError) as info:
        contract.invoke("post", OUTSIDER, U32(1), board.Priority.Low)
    assert not isinstance(info.value, RealContractError)
    assert info.value.underlying is not None and info.value.underlying[0] == "Auth"
    assert contract.auths() == ()


@pytest.mark.real_host
def test_storage_reads_back_through_the_real_host_by_type() -> None:
    real = RealEnv(auths=ALLOWED)
    contract = real.deploy_source(EXAMPLE_BOUNTY_BOARD, ADMIN)
    contract.invoke("post", POSTER, U32(7), board.Priority.High)
    contract.invoke("claim", U32(1), WORKER)
    record = cast(
        Any, contract.storage("persistent").get(board.BountyKey(bounty_id=U32(1)), board.Bounty)
    )
    assert record == board.Bounty(
        poster=POSTER, reward=U32(7), priority=board.Priority.High, posted_at=record.posted_at
    )
    assert contract.storage("persistent").get(
        board.StatusKey(status_of=U32(1)), board.Status
    ) == board.Status.Claimed(WORKER)
    assert contract.storage("temporary").get(board.ClaimKey(claim_on=U32(1)), Address) == WORKER
    assert (
        contract.storage("temporary").ttl(board.ClaimKey(claim_on=U32(1))) == board.CLAIM_TTL.value
    )
