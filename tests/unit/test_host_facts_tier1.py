"""The tier-1 leg of `HOST_FACTS`, and the table's own meta-tests (dossier D.3).

Two jobs, deliberately in one module:

* **the tier-1 leg.** Every row whose `tier1` is not `Unmodelled` is replayed
  against `Env`/`deploy` in the same shape as
  `test_env_differential._tier_1` -- the row's ledger and allow-set, the setup in
  order, then the one observable call -- and the answer is matched against the
  row's `tier1` expectation. An `Unmodelled` row is an ENUMERABLE
  `pytest.skip(reason)` (`pytest -rs`), the way `test_env_ttl.py` skips the same
  TTL-maximum gap, so "tier 1 has no answer here" is counted rather than absent;
* **the table meta-tests**, which are UNMARKED (review M12) and therefore run on
  every checkout, Rust extension or not: a table that has drifted is a fact
  nobody can trust, and finding that out should not require a built host.

**The mapping table is the interesting half.** Tier 1 raises its own exception
classes and the host reports `(ScErrorType, ScErrorCode)` pairs, so a row that
claims "both legs refuse this" has to say what refusing MEANS on each side.
`_TIER_1_MAPPING` is that statement, and it is deliberately a small closed table
rather than a rule:

* `StorageTrap` -> `("Storage", <the row's own code>)`. The model's message
  already carries the host's words (findings F1/F2 taught it both), and which
  code a given call reports is the ROW's fact, not the class's -- `extend_ttl`
  raises `StorageTrap` for the inverted window (`InvalidInput`) and for the
  absent entry (`MissingValue`) alike;
* `AuthorizationFailed` -> `("Auth", "InvalidAction")`;
* `ZeroDivisionError` -> `("Object", "ArithDomain")`. Python's own exception,
  because the tier-1 model does Python arithmetic -- and the mapping is the claim
  that it MEANS the host's 128-bit divide-by-zero (E15).

A class not in the table is a failure naming it, never a pass: the whole value of
the mapping is that it is exhaustive for this table, so a fourth kind of tier-1
refusal has to be ruled on rather than absorbed.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from serpent.decorators import _METADATA_ATTR
from serpent.env import (
    DEFAULT_LEDGER_SEQUENCE,
    DEFAULT_LEDGER_TIMESTAMP,
    AuthorizationFailed,
    Env,
    StorageTrap,
    deploy,
)
from serpent.errors import ContractError
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

#: What a tier-1 refusal MEANS in the host's vocabulary. `None` in the second
#: position is "the row's own code": `StorageTrap` covers two distinct host
#: codes, and which one a call reports is the row's fact.
_TIER_1_MAPPING: dict[type[BaseException], tuple[str, str | None]] = {
    StorageTrap: ("Storage", None),
    AuthorizationFailed: ("Auth", "InvalidAction"),
    ZeroDivisionError: ("Object", "ArithDomain"),
}


@functools.cache
def _module() -> ModuleType:
    """The fixture module, imported by path once per session.

    By path, like `RealEnv.deploy_source` and `test_env_differential`: the file
    is a test fixture, not an importable package member.
    """
    return load_example(HOST_FACTS_CONTRACT)


def _contract_class() -> type:
    """The one `@contract` class in the fixture -- discovered, not named."""
    found = [
        member
        for member in vars(_module()).values()
        if isinstance(member, type)
        and isinstance(vars(member).get(_METADATA_ATTR), dict)
        and vars(member)[_METADATA_ATTR].get("kind") == "contract"
    ]
    assert len(found) == 1, found
    return found[0]


def _mapped(exc: BaseException, row: HostFact) -> tuple[str, str]:
    """`exc` as the `(ScErrorType, ScErrorCode)` pair the row claims it means.

    The `type` half comes from the mapping and the `code` half from the mapping
    OR the row, which is what lets one tier-1 class stand for two host codes
    without the mapping having to re-derive the row's fact.
    """
    for cls, (type_name, code_name) in _TIER_1_MAPPING.items():
        if isinstance(exc, cls):
            if code_name is not None:
                return (type_name, code_name)
            assert isinstance(row.tier1, HostErr) and row.tier1.underlying is not None, row
            return (type_name, row.tier1.underlying[1])
    raise AssertionError(
        f"{row.name}: tier 1 raised {type(exc).__name__} ({exc}), which "
        f"tests/unit/test_host_facts_tier1.py's mapping table does not cover. "
        "A fourth kind of tier-1 refusal in this table is a controller decision, "
        "not a mapping to widen in passing."
    )


def _tier_1(row: HostFact) -> None:
    """Replay `row` against the tier-1 model and assert its `tier1` expectation.

    The assertion lives here rather than in the test body because the three
    outcome kinds need three different `pytest.raises` shapes, and a helper that
    returned "the outcome" would have to invent a fourth vocabulary to carry
    them (`test_env_differential`'s `Outcome` exists because its rows compare two
    legs; here the row IS the expectation).
    """
    env = Env(
        timestamp=DEFAULT_LEDGER_TIMESTAMP,
        sequence=DEFAULT_LEDGER_SEQUENCE if row.sequence is None else row.sequence,
        auths=row.auth_allow_set,
    )
    instance: Any = deploy(_contract_class(), env, *row.constructor)
    for step in row.setup:
        if isinstance(step, Advance):
            env.advance(step.ledgers)
            continue
        with env.frame():
            getattr(instance, step.method)(env, *step.args)

    method = getattr(instance, row.invoke.method)
    expected = row.tier1
    with env.frame():
        if isinstance(expected, Value):
            answer = method(env, *row.invoke.args)
            assert answer == expected.value, f"{row.name}: tier 1 answered {answer!r}"
            # `Bool(True) == True` in Python, so the VALUE alone would let a
            # model answering a plain `bool` pass. The type name is what makes
            # that a failure -- `test_env_differential.Outcome` carries
            # `answer_type` for the same reason.
            assert type(answer).__name__ == type(expected.value).__name__, (
                f"{row.name}: tier 1 answered {type(answer).__name__}, "
                f"the row expects {type(expected.value).__name__}"
            )
        elif isinstance(expected, ContractErr):
            with pytest.raises(ContractError) as failed:
                method(env, *row.invoke.args)
            assert failed.value.code == expected.code
        elif isinstance(expected, HostErr):
            with pytest.raises(Exception) as refused:
                method(env, *row.invoke.args)
            assert not isinstance(refused.value, ContractError), (
                f"{row.name}: tier 1 laundered a host refusal into contract code "
                f"{getattr(refused.value, 'code', None)!r}; the host TRAPS here, so the model "
                "must too (finding F2)"
            )
            assert _mapped(refused.value, row) == expected.underlying
        else:
            # `Unmodelled` never reaches here -- the test body skips it -- and
            # saying so is what keeps the three-branch dispatch total.
            raise TypeError(f"{row.name}: an Unmodelled row reached the tier-1 replay")


@pytest.mark.parametrize("row", HOST_FACTS, ids=[r.name for r in HOST_FACTS])
def test_the_tier_1_model_answers_a_row_as_the_table_says_it_does(row: HostFact) -> None:
    if isinstance(row.tier1, Unmodelled):
        pytest.skip(f"{row.name}: {row.tier1.reason}")
    _tier_1(row)


def test_a_refused_auth_records_nothing_at_tier_1() -> None:
    """The half of the auth row's fact that is not the exception (O19).

    "Nothing is recorded" is a claim about `env.recorded_auths`, which the
    replay above discards -- and it is the half a test could easily miss, since
    a model that recorded the refused authorization would still raise the right
    exception.
    """
    row = _row("a_refused_auth_is_an_auth_trap_and_records_nothing")
    env = Env(sequence=DEFAULT_LEDGER_SEQUENCE, auths=row.auth_allow_set)
    instance: Any = deploy(_contract_class(), env)
    with env.frame(), pytest.raises(AuthorizationFailed):
        getattr(instance, row.invoke.method)(env, *row.invoke.args)
    assert env.recorded_auths == ()


def test_the_event_published_before_a_raise_survives_at_tier_1() -> None:
    """Tier 1 KEEPS the event; the host rolls it back (S9/O15).

    The row's expectation is the OUTCOME, which both legs agree on, so the
    difference has nowhere else to be asserted -- and asserting it here is what
    makes `test_host_facts_real.py`'s `events() == ()` a measured divergence
    rather than an unexamined absence.
    """
    row = _row("an_event_published_before_a_raise_is_rolled_back")
    env = Env(sequence=DEFAULT_LEDGER_SEQUENCE)
    instance: Any = deploy(_contract_class(), env)
    with env.frame(), pytest.raises(ContractError):
        getattr(instance, row.invoke.method)(env, *row.invoke.args)
    assert len(env.published_events) == 1, env.published_events


def _row(name: str) -> HostFact:
    (row,) = [r for r in HOST_FACTS if r.name == name]
    return row


# ===========================================================================
# the table's own meta-tests -- UNMARKED, so they run without the real host
# ===========================================================================


def test_the_row_names_are_unique() -> None:
    names = [row.name for row in HOST_FACTS]
    assert len(set(names)) == len(names)


def test_every_row_cites_a_dossier_id() -> None:
    """A row whose `fact` cites nothing is a row nobody can check against a
    decision. The pattern is the ID families the plan and dossier use."""
    for row in HOST_FACTS:
        assert re.search(r"\b(S\d+|O\d+|E\d+|D\d+|B\d+|M\d+)\b", row.fact), row


def test_no_host_error_row_is_still_unmeasured() -> None:
    """`HostErr(None)` means "measure this on the first run" (M9), so one
    surviving in the committed table would be a placeholder masquerading as a
    fact -- and the real leg would assert nothing about it."""
    unpinned = [
        row.name
        for row in HOST_FACTS
        for side in (row.real, row.tier1)
        if isinstance(side, HostErr) and side.underlying is None
    ]
    assert unpinned == []


def test_every_compare_vector_sign_is_pinned() -> None:
    """Same rule for `COMPARE_VECTORS` (E12): a `None` sign is an unasked
    question, and the real leg would compare the host's answer to nothing."""
    assert [i for i, (_a, _b, sign) in enumerate(COMPARE_VECTORS) if sign is None] == []
    assert all(sign in (-1, 0, 1) for _a, _b, sign in COMPARE_VECTORS)


def test_a_divergence_is_declared_exactly_where_the_two_legs_differ() -> None:
    """`divergence_reason` iff `real != tier1` (and tier 1 has an answer).

    Both directions matter. A row whose legs differ with no reason is a
    divergence nobody ruled on; a reason on a row whose legs agree is a
    declaration that has stopped being true -- which is exactly what happened to
    the archival row here (tier 1 answers `U32(7)` too, because a never-extended
    entry never lapses), and the only thing that catches it is asserting the
    biconditional rather than one half of it.
    """
    for row in HOST_FACTS:
        if isinstance(row.tier1, Unmodelled):
            assert row.divergence_reason is None, row
            continue
        differ = row.real != row.tier1
        assert (row.divergence_reason is not None) == differ, (
            f"{row.name}: real={row.real!r} tier1={row.tier1!r}, "
            f"divergence_reason={row.divergence_reason!r}"
        )


def test_exactly_one_row_is_declared_unproven_against_the_chain() -> None:
    """M3, pinned by NAME: which rows are evidence about the TEST host only is a
    decision, not an implementation detail. A second one appearing quietly would
    be a row claiming more than it can."""
    assert {row.name for row in HOST_FACTS if row.chain_unproven is not None} == {
        "a_lapsed_persistent_entry_stays_readable_on_the_test_host"
    }


def test_at_least_one_row_diverges_and_at_least_one_is_unmodelled() -> None:
    """A host-fact table where the model already agreed everywhere has not asked
    the host anything (dossier F.1.1)."""
    assert any(row.divergence_reason is not None for row in HOST_FACTS)
    assert any(isinstance(row.tier1, Unmodelled) for row in HOST_FACTS)


def test_the_table_drives_every_method_of_the_fixture() -> None:
    """The fixture exists for this table alone (its docstring says so), so an
    unreached method is dead code in the mutation-fuzz corpus rather than a
    fact -- and a fact with no method is a row that cannot run."""
    declared = {
        name
        for name, member in vars(_contract_class()).items()
        if callable(member) and not name.startswith("_")
    }
    driven = {row.invoke.method for row in HOST_FACTS} | {
        step.method for row in HOST_FACTS for step in row.setup if not isinstance(step, Advance)
    }
    assert driven == declared


def test_the_fixture_path_is_the_file_that_exists() -> None:
    assert HOST_FACTS_CONTRACT.is_file()
    assert HOST_FACTS_CONTRACT.parent == Path(__file__).resolve().parents[1] / "fixtures"


def test_every_tier_1_refusal_class_is_in_the_mapping_table() -> None:
    """The mapping is what makes a `HostErr` row's two legs comparable, so it is
    asserted non-empty and closed rather than left as a lookup that might miss.
    """
    assert set(_TIER_1_MAPPING) == {StorageTrap, AuthorizationFailed, ZeroDivisionError}
    assert all(
        isinstance(pair[0], str) and (pair[1] is None or isinstance(pair[1], str))
        for pair in _TIER_1_MAPPING.values()
    )
