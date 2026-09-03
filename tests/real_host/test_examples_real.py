"""The real host: every example's headline call sequence, tier 1 vs it (dossier C8, O5).

`tests/unit/test_examples.py` runs each example at tier 1 and under the mini
host and compares the two. This module re-runs the SAME sequences with the
real host as the second leg. The sequences are restated here rather than
imported: the existing tests interleave their two legs inline, and a shared
table would mean editing frozen-by-convention example tests. Each sequence is
a `(method, args)` tuple list; the tier-1 leg and the real leg replay it and
the decoded answers are compared to EACH OTHER first, then to the literal pins
the headline test carries (O28's order).

The union/enum return test lifts O5: the mini host has no spec decoder, so
`tests/unit/test_examples.py`'s own shapes cross-check can only compare
`Symbol`/`U32`/`Bool` answers (its docstring says so). The real host decodes a
declared `Shape`/`Level` return through the method's own type annotation
(`RealContract.invoke`'s `_return_type`), which is what this module's last
test proves for both the union and the int enum. It also depends on B1 (the
small-Symbol compare lowering, Task 0): `udt_style.py`'s `area`/`radius`
compare small Symbols via `tag() == Symbol(...)`, which the real host used to
refuse before that fix.

Every test in this module needs the embedded host, so every one carries the
`real_host` marker individually (M12): the repo convention is per-test
marking, not a module-level `pytestmark`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from serpent import U32, Address, Bool, ContractEnum, String, Symbol, Vec
from serpent.env import Env, deploy
from serpent.testing import RealContractError, RealEnv
from tests.unit.test_emitter_end_to_end import (
    EXAMPLE_ALLOWANCE_TOKEN,
    EXAMPLE_COUNTER,
    EXAMPLE_ERRORS,
    EXAMPLE_EVENTS,
    EXAMPLE_SHAPES,
    EXAMPLE_STRUCTS,
)
from tests.unit.test_examples import _allowance_token_roles, load_example

#: Two well-known strkeys, restated locally rather than imported: every module
#: under `tests/` that needs one declares its own copy
#: (`tests/real_host/test_real_env.py`, `tests/unit/test_emitter_end_to_end.py`,
#: ...) rather than reaching into another test module for it.
ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"
CONTRACT = "CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI"

real = (
    pytest.mark.real_host
)  # per-test (M12); every test here needs the host, but the rule is stated

Step = tuple[str, tuple[Any, ...]]


def _both_legs(
    path: Path, ctor: tuple[Any, ...], steps: Sequence[Step]
) -> tuple[list[object], list[object]]:
    cls = _contract_class(load_example(path))
    env1 = Env()
    inst: Any = deploy(cls, env1, *ctor)
    tier1: list[object] = []
    for method, args in steps:
        with env1.frame():
            tier1.append(
                _outcome(lambda method=method, args=args: getattr(inst, method)(env1, *args))
            )
    real_env = RealEnv()
    c = real_env.deploy_source(path, *ctor)  # B3: path-loaded classes have no sys.modules entry
    real_leg: list[object] = [
        _outcome(lambda m=method, a=args: c.invoke(m, *a)) for method, args in steps
    ]
    return tier1, real_leg


def _outcome(call: Any) -> object:
    """A value, or ('error', code) -- both legs normalize errors to the code."""
    try:
        return call()
    except RealContractError as exc:
        return ("error", exc.code)
    except Exception as exc:  # tier 1's @contracterror members are exception classes with .code
        code = getattr(type(exc), "code", None)
        if code is None:
            raise
        return ("error", code)


@real
def test_counter() -> None:
    """Faithful restatement of
    `test_the_counter_example_answers_the_same_at_tier_1_and_as_wasm`
    (`tests/unit/test_examples.py:162-191`): `total()` FIRST, on the
    unwritten default, then `increment(5)`, `increment(7)`, `total()`."""
    steps: list[Step] = [
        ("total", ()),
        ("increment", (U32(5),)),
        ("increment", (U32(7),)),
        ("total", ()),
    ]
    tier1, real_leg = _both_legs(EXAMPLE_COUNTER, (), steps)
    assert real_leg == tier1
    assert tier1 == [U32(0), U32(5), U32(12), U32(12)]


@real
def test_errors_vault() -> None:
    """Faithful restatement of
    `test_the_errors_example_answers_the_same_at_tier_1_and_as_wasm`
    (`tests/unit/test_examples.py:229-275`): the happy path
    (`deposit(40)`, `withdraw(10)`, `balance()`), then all three refusals in
    the order the headline reaches them (`deposit(1000)` -> `LimitExceeded`,
    `withdraw(1000)` -> `InsufficientBalance`, `set_limit` by a non-owner
    -> `Unauthorized`), then a final `balance()` proving none of the three
    refusals wrote anything."""
    owner = Address(ACCOUNT)
    steps: list[Step] = [
        ("deposit", (U32(40),)),
        ("withdraw", (U32(10),)),
        ("balance", ()),
        ("deposit", (U32(1000),)),
        ("withdraw", (U32(1000),)),
        ("set_limit", (Address(CONTRACT), U32(5))),
        ("balance", ()),
    ]
    tier1, real_leg = _both_legs(EXAMPLE_ERRORS, (owner, U32(100)), steps)
    assert real_leg == tier1
    assert tier1 == [
        U32(40),
        U32(30),
        U32(30),
        ("error", 3),  # LimitExceeded
        ("error", 4),  # InsufficientBalance
        ("error", 2),  # Unauthorized
        U32(30),
    ]


@real
def test_structs_registry() -> None:
    """Faithful restatement of
    `test_the_structs_example_answers_the_same_at_tier_1_and_as_wasm`
    (`tests/unit/test_examples.py:314-352`): join once, read both fields
    back, then both refusals -- a second `join` for the same member
    (`AlreadyJoined`=1) and `display_name_of` for a never-joined address
    (`NotAMember`=2)."""
    member = Address(ACCOUNT)
    name = String("Ana Registrar")
    steps: list[Step] = [
        ("join", (member, name)),
        ("display_name_of", (member,)),
        ("joined_ledger_of", (member,)),
        ("join", (member, String("Ana Again"))),
        ("display_name_of", (Address(CONTRACT),)),
    ]
    tier1, real_leg = _both_legs(EXAMPLE_STRUCTS, (), steps)
    assert real_leg == tier1
    assert tier1 == [
        None,
        name,
        U32(1_000_000),
        ("error", 1),  # AlreadyJoined
        ("error", 2),  # NotAMember
    ]


@real
def test_events_scoreboard() -> None:
    """`examples/events.py`'s headline sequence
    (`test_the_events_example_answers_the_same_at_tier_1_and_as_wasm`,
    `tests/unit/test_examples.py:382`): both publish spellings, compared as
    decoded chain values on both legs."""
    module = load_example(EXAMPLE_EVENTS)
    env1 = Env()
    scoreboard = deploy(module.Scoreboard, env1)
    with env1.frame():
        scoreboard.record_score(env1, Address(ACCOUNT), U32(7))
        scoreboard.record_round_closed(env1, U32(3), U32(1))
    (score_topics, score_data), (round_topics, round_data) = env1.published_events

    real_env = RealEnv()
    c = real_env.deploy_source(EXAMPLE_EVENTS)
    assert c.invoke("record_score", Address(ACCOUNT), U32(7)) is None
    assert c.invoke("record_round_closed", U32(3), U32(1)) is None
    # `events()` answers the LAST invocation only; `events_for_sequence()`
    # accumulates across both invokes, which is what pairs with tier 1's
    # `env.published_events` over the whole `with env.frame()` block.
    (real_score_topics, real_score_data), (real_round_topics, real_round_data) = (
        c.events_for_sequence()
    )

    assert real_score_topics == score_topics
    assert real_score_data == score_data
    assert real_round_topics == round_topics
    assert real_round_data == round_data

    assert score_topics == (Symbol("scored"), Address(ACCOUNT))
    assert score_data == U32(7)
    assert round_topics == (Symbol("round_closed"),)
    # narrow the `ChainValue` union to `Vec` before iterating, the same guard
    # the headline test's own docstring names.
    assert isinstance(round_data, Vec)
    assert list(round_data) == [U32(3), U32(1)]


@real
def test_allowance_token() -> None:
    """Faithful restatement of
    `test_the_allowance_token_example_answers_the_same_at_tier_1_and_as_wasm`
    (`tests/unit/test_examples.py:487-586`): mint, approve, one successful
    `transfer_from`, the three reads the headline takes at that point
    (`balance(owner)`, `balance(to)`, `allowance(owner, spender)`), both
    refusals (`transfer_from` over the balance -> `InsufficientBalance`=2,
    then with no allowance at all -> `InsufficientAllowance`=1), and the
    headline's final re-reads (`balance(owner)`, `allowance(owner,
    spender)`) proving neither refusal wrote anything. `RealEnv()` mocks
    every authorization, matching tier 1's `Env(auths=None)`, so
    `require_auth` on admin/owner/spender goes through unchallenged on both
    legs.
    """
    admin, owner, spender, to = _allowance_token_roles()
    steps: list[Step] = [
        ("mint", (owner, U32(100))),
        ("approve", (owner, spender, U32(200))),
        ("transfer_from", (spender, owner, to, U32(25))),
        ("balance", (owner,)),
        ("balance", (to,)),
        ("allowance", (owner, spender)),
        ("transfer_from", (spender, owner, to, U32(100))),
        ("transfer_from", (to, owner, spender, U32(1))),
        ("balance", (owner,)),
        ("allowance", (owner, spender)),
    ]
    tier1, real_leg = _both_legs(EXAMPLE_ALLOWANCE_TOKEN, (admin,), steps)
    assert real_leg == tier1
    assert tier1 == [
        None,
        None,
        None,
        U32(75),
        U32(25),
        U32(175),
        ("error", 2),  # InsufficientBalance
        ("error", 1),  # InsufficientAllowance
        U32(75),
        U32(175),
    ]

    # The headline test also compares the `Approval`/`Transfer` events the
    # setup calls publish, on both legs -- a second, dedicated deploy, since
    # `_both_legs`/`_outcome` do not track events.
    module = load_example(EXAMPLE_ALLOWANCE_TOKEN)
    env1 = Env()
    token = deploy(module.AllowanceToken, env1, admin)
    with env1.frame():
        token.mint(env1, owner, U32(100))
        token.approve(env1, owner, spender, U32(200))
        token.transfer_from(env1, spender, owner, to, U32(25))
        tier1_codes = [
            _outcome(lambda: token.transfer_from(env1, spender, owner, to, U32(100))),
            _outcome(lambda: token.transfer_from(env1, to, owner, spender, U32(1))),
        ]
    tier1_events = env1.published_events

    real_env = RealEnv()
    c = real_env.deploy_source(EXAMPLE_ALLOWANCE_TOKEN, admin)
    c.invoke("mint", owner, U32(100))
    c.invoke("approve", owner, spender, U32(200))
    c.invoke("transfer_from", spender, owner, to, U32(25))
    real_codes = [
        _outcome(lambda: c.invoke("transfer_from", spender, owner, to, U32(100))),
        _outcome(lambda: c.invoke("transfer_from", to, owner, spender, U32(1))),
    ]
    # A failed call publishes nothing, so the two refusals above add no
    # further entries: this is still exactly `Approval` then `Transfer`.
    real_events = c.events_for_sequence()

    assert real_codes == tier1_codes
    assert tier1_codes == [("error", 2), ("error", 1)]  # InsufficientBalance, InsufficientAllowance

    (approval_topics, approval_data), (transfer_topics, transfer_data) = tier1_events
    (real_approval_topics, real_approval_data), (real_transfer_topics, real_transfer_data) = (
        real_events
    )
    assert real_approval_topics == approval_topics
    assert real_approval_data == approval_data
    assert real_transfer_topics == transfer_topics
    assert real_transfer_data == transfer_data
    assert approval_topics == (Symbol("approve"), owner, spender)
    assert approval_data == U32(200)
    assert transfer_topics == (Symbol("transfer"), owner, to)
    assert transfer_data == U32(25)


@real
def test_shapes_drawing() -> None:
    """`examples/shapes.py`'s headline sequence
    (`test_the_shapes_example_answers_the_same_at_tier_1_and_as_wasm`,
    `tests/unit/test_examples.py:744`): every read pattern over every arity of
    `Shape`, plus `Color`'s equality chain. `area`'s small-Symbol tag compares
    only became runnable on the real host after Task 0's emitter fix (B1)."""
    module = load_example(EXAMPLE_SHAPES)
    env1 = Env()
    drawing = deploy(module.Drawing, env1)
    with env1.frame():
        tier1 = [drawing.kind(env1), drawing.area(env1), drawing.palette(env1)]
        drawing.draw_circle(env1, U32(3))
        tier1 += [drawing.kind(env1), drawing.area(env1), drawing.is_pinned(env1)]
        drawing.pin(env1)
        tier1 += [drawing.is_pinned(env1)]
        drawing.draw_rect(env1, U32(4), U32(5))
        tier1 += [drawing.kind(env1), drawing.area(env1), drawing.is_pinned(env1)]
        drawing.paint(env1, module.Color.Blue)
        tier1 += [drawing.palette(env1)]
        drawing.clear(env1)
        tier1 += [drawing.kind(env1), drawing.area(env1)]

    real_env = RealEnv()
    c = real_env.deploy_source(EXAMPLE_SHAPES)
    real_leg = [c.invoke("kind"), c.invoke("area"), c.invoke("palette")]
    c.invoke("draw_circle", U32(3))
    real_leg += [c.invoke("kind"), c.invoke("area"), c.invoke("is_pinned")]
    c.invoke("pin")
    real_leg += [c.invoke("is_pinned")]
    c.invoke("draw_rect", U32(4), U32(5))
    real_leg += [c.invoke("kind"), c.invoke("area"), c.invoke("is_pinned")]
    c.invoke("paint", module.Color.Blue)
    real_leg += [c.invoke("palette")]
    c.invoke("clear")
    real_leg += [c.invoke("kind"), c.invoke("area")]

    assert real_leg == tier1
    assert tier1 == [
        Symbol("Empty"),
        U32(0),
        Symbol("red"),
        Symbol("Circle"),
        U32(27),
        Bool(False),
        Bool(True),
        Symbol("Rect"),
        U32(20),
        Bool(False),
        Symbol("blue"),
        Symbol("Empty"),
        U32(0),
    ]


@real
def test_a_union_and_an_int_enum_return_decode_through_their_types() -> None:
    """O5 lifted: the mini host could not decode these; the real leg decodes via `ty`.
    `area`/`radius` on this fixture compare small Symbols (B1) -- runnable only after Task 0.

    **Deviation from the brief's sketch (façade fact, not a bug).** The brief's
    `assert isinstance(c.invoke("level"), module.Level)` does not hold: `module`
    is loaded here via `load_example` and `deploy_source` loads the SAME path a
    second time internally (`_real._load_by_path`, review B3), which -- by the
    module's own documented design ("a second load is an honestly distinct set
    of class objects") -- gives `RealContract._return_type` a DIFFERENT `Level`
    class than `module.Level`. `ContractEnum.__eq__` compares by discriminant
    across any two enum classes, which is the same value-equality
    `current_shape`'s assertion already relies on for `Shape`, so the smallest
    faithful check is `== module.Level.High` plus `isinstance(..., ContractEnum)`
    -- which still fails if the return decoded as a bare `U32` instead of an
    enum member, so the class-correctness claim survives the swap.
    """
    udt_style = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "udt_style.py"
    module = load_example(udt_style)

    c = RealEnv().deploy_source(udt_style)
    c.invoke("set_rect", U32(2), U32(3))
    real_shape = c.invoke("current_shape")
    c.invoke("promote")
    real_level = c.invoke("level")
    assert isinstance(real_level, ContractEnum)

    env = Env()
    inst: Any = deploy(module.UdtStyle, env)
    with env.frame():
        inst.set_rect(env, U32(2), U32(3))
        tier1_shape = inst.current_shape(env)
        inst.promote(env)
        tier1_level = inst.level(env)

    # the real leg compared to tier 1 DIRECTLY, before either is pinned to a literal
    assert real_shape == tier1_shape
    assert real_level == tier1_level
    assert real_shape == module.Shape.Rect(U32(2), U32(3))
    assert real_level == module.Level.High


def _contract_class(module: object) -> type:
    from serpent.decorators import _METADATA_ATTR

    (cls,) = [
        m
        for m in vars(module).values()
        if isinstance(m, type) and vars(m).get(_METADATA_ATTR, {}).get("kind") == "contract"
    ]
    return cls
