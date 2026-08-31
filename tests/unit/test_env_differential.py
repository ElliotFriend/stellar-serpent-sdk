"""The E9 differential: every `EnvScenario`, run at tier 1 AND as WASM.

`tests/unit/test_examples.py` runs five whole contracts both ways by hand.
This file does the same thing for a TABLE
(`tests/semantics/env_scenarios.py`'s `ENV_SCENARIOS`), so that the stateful
half of the `Env` -- storage across invocations, defaults, `MissingValue`,
events, auth records, ledger reads, TTL -- is covered by rows a reviewer can
count rather than by prose. Each row is replayed twice:

1. against the tier-1 model -- `deploy(cls, env, *constructor)`, then one
   `with env.frame():` per call, exactly as an author's test would;
2. against the compiled contract under `tests/harness`'s mini host --
   `mini.invoke(name, *words)`, with each argument encoded through the host's
   own `val_word`.

The two outcomes are compared to EACH OTHER first, and only then to the
expectation the table pins. That order is the point (it is
`test_examples.py`'s reasoning applied to a table): two absolute pins can both
be edited to agree with a drifting convention, and comparing the legs cannot
be satisfied that way -- a failure says which half moved.

**The honest limit (ruling E9, verbatim): it compares two models E/D wrote;
F's tier-2b is where it becomes evidence.** Tier 1 is a hand-written model of
host semantics, `tests/harness` is a mini host that mirrors it, and neither is
the chain. `ENV_SCENARIOS` is importable precisely so sub-plan F's tier 2b can
re-run this corpus against a real host; a green run here is self-consistency.

**What the WASM leg cannot do, and how a row says so.** A row carrying
`tier1_only_reason` runs at tier 1 only, and the reason names the harness limit
that rules the second leg out -- no TTL model at all, discarded
`require_auth_for_args` args (review M11), no authorization state to refuse
with. `test_a_row_is_tier_1_only_exactly_when_it_reaches_a_harness_limit`
asserts the biconditional, so a row cannot quietly opt out.

**The ledger defaults are shared, not matched.** Both models read
`DEFAULT_LEDGER_TIMESTAMP`/`DEFAULT_LEDGER_SEQUENCE` from `serpent.env` (the
mini host's stubs import them from there), so the ledger rows agree BY
CONSTRUCTION rather than by two literals that happen to be equal --
`test_the_two_models_share_one_ledger_default` is the pin that keeps it that
way.

Four properties sit below the table, each about a claim a scenario row cannot
express on its own: the storage-key round trip over generated `ChainValue`s
(dossier D.5.1), the publish-then-raise no-rollback pin (F.1.8), the
`SCSpecEventV0` round trip for every generated event class, and F.1.6 -- every
rewrite an `SPT1034` `help:` line recommends really compiles.
"""

from __future__ import annotations

import copy
import functools
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from hypothesis import assume, given
from stellar_sdk import xdr

from serpent import val
from serpent.compiler.diagnostics import CompileError
from serpent.compiler.frontend import compile_module
from serpent.compiler.ir import FuncKind

# `_METADATA_ATTR` is the decorator's OWN attribute name for the `@contract`/
# `@contractevent` metadata, read through the constant rather than as a
# restated string literal.
from serpent.decorators import _METADATA_ATTR
from serpent.emitter import BuildResult, build_file
from serpent.env import (
    DEFAULT_LEDGER_SEQUENCE,
    DEFAULT_LEDGER_TIMESTAMP,
    AuthorizationFailed,
    ChainValue,
    Env,
    PublishedEvent,
    RecordedAuth,
    deploy,
)
from serpent.spec.sections import _DATA_FORMATS, _PARAM_LOCATIONS
from serpent.types import U32, U64, Address, Bool, String, Symbol
from serpent.types._storage_key import storage_key
from tests.harness import engine
from tests.harness.hostfns import FullHost
from tests.semantics.env_scenarios import (
    AUTH_ARGS_METHODS,
    ENV_SCENARIOS,
    ENV_SURFACE,
    TOKEN_STYLE,
    TOKEN_STYLE_CANONICAL,
    TTL_METHODS,
    Advance,
    Call,
    EnvScenario,
)
from tests.unit.conftest import deployed_env

# Imported, never re-written (the rule `test_examples.py` states when it
# imports `_answer`): `load_example` is THE path-addressed module loader for a
# contract source, `CHAIN_VALUES`/`_grow` are THE generated-chain-value
# strategy and its in-place mutator, and `_unpack`/`_wasm_custom_section` are
# THE readers for a built module's `contractspecv0` section. A second copy of
# any of them is the drift this repo keeps avoiding.
from tests.unit.test_emitter_end_to_end import (
    EXAMPLE_ALLOWANCE_TOKEN,
    EXAMPLE_EVENTS,
)
from tests.unit.test_emitter_end_to_end import (
    TOKEN_STYLE as END_TO_END_TOKEN_STYLE,
)
from tests.unit.test_emitter_end_to_end import (
    TOKEN_STYLE_CANONICAL as END_TO_END_TOKEN_STYLE_CANONICAL,
)
from tests.unit.test_env_model import CHAIN_VALUES, _grow
from tests.unit.test_examples import _tier_1_code, _wasm_code, load_example
from tests.unit.test_sections import _unpack, _wasm_custom_section

# ===========================================================================
# the two legs
# ===========================================================================


@dataclass(frozen=True)
class Outcome:
    """What one replay of a scenario produced, in the two models' common terms.

    `answer_type` is carried beside `answer` on purpose: `Bool(True) == True`
    in Python, so a model whose `has` answered a plain `bool` would compare
    EQUAL to the WASM leg's chain `Bool` and the divergence would pass. The
    type name makes it a failure.

    `auths` is the tier-1 form -- `(address, args-or-None)` -- and is dropped
    before the two legs are compared (`_comparable`): the mini host records
    only the address (review M11).
    """

    answer: ChainValue | None
    answer_type: str
    code: int | None
    refused: bool
    events: tuple[PublishedEvent, ...]
    auth_addresses: tuple[Address, ...]
    auths: tuple[RecordedAuth, ...] = ()


def _comparable(outcome: Outcome) -> Outcome:
    """`outcome` with the half the mini host cannot report dropped.

    Only the auth ARGS: everything else -- the answer, its type, the error
    code, the events, the addresses that authorized -- both models report, and
    dropping any of those would be dropping the differential.
    """
    return replace(outcome, auths=())


@functools.cache
def _built(path: Path) -> BuildResult:
    """`build_file(path)`, once per path per session.

    The table replays ~60 rows over three contracts; rebuilding per row would
    pay for the same three compiles sixty times. The BUILD is cached, never the
    host: every row gets a fresh `FullHost` and a fresh instance, because a
    shared store would make one row's writes another row's setup.
    """
    return build_file(path)


@functools.cache
def _module(path: Path) -> ModuleType:
    """The contract module at `path`, imported once per path per session."""
    return load_example(path)


def _contract_class(path: Path) -> type:
    """The one `@contract` class in the module at `path`.

    Discovered rather than named in the table: a serpent module declares
    exactly one contract (the frontend enforces it), so a second field naming
    the class could only ever disagree with the file.
    """
    module = _module(path)
    found = [
        member
        for member in vars(module).values()
        if isinstance(member, type)
        and isinstance(vars(member).get(_METADATA_ATTR), dict)
        and vars(member)[_METADATA_ATTR].get("kind") == "contract"
    ]
    assert len(found) == 1, f"{path} declares {len(found)} @contract classes, not one"
    return found[0]


def _tier_1(scenario: EnvScenario) -> Outcome:
    """Replay `scenario` against the tier-1 model."""
    env = Env(
        timestamp=DEFAULT_LEDGER_TIMESTAMP if scenario.timestamp is None else scenario.timestamp,
        sequence=DEFAULT_LEDGER_SEQUENCE if scenario.sequence is None else scenario.sequence,
        auths=scenario.auth_allow_set,
    )
    instance: Any = deploy(_contract_class(scenario.contract), env, *scenario.constructor)
    for step in scenario.setup:
        if isinstance(step, Advance):
            env.advance(step.ledgers)
            continue
        with env.frame():
            getattr(instance, step.method)(env, *step.args)

    call = scenario.invoke
    method = getattr(instance, call.method)
    answer: ChainValue | None = None
    code: int | None = None
    refused = False
    with env.frame():
        if scenario.kind == "contract_error":
            code = _tier_1_code(method, env, *call.args)
        elif scenario.kind == "auth_failed":
            with pytest.raises(AuthorizationFailed):
                method(env, *call.args)
            refused = True
        else:
            answer = method(env, *call.args)
    return Outcome(
        answer=answer,
        answer_type=type(answer).__name__,
        code=code,
        refused=refused,
        events=env.published_events,
        auth_addresses=tuple(address for address, _args in env.recorded_auths),
        auths=env.recorded_auths,
    )


def _wasm(scenario: EnvScenario) -> Outcome:
    """Replay `scenario` against the compiled contract under the mini host."""
    built = _built(scenario.contract)
    host = FullHost()
    mini = engine.MiniHost(built.wasm, imports=host.bindings())
    host.attach(mini)
    if scenario.timestamp is not None:
        host.ledger_timestamp = scenario.timestamp
    if scenario.sequence is not None:
        host.ledger_sequence = scenario.sequence
    if scenario.constructor:
        assert mini.invoke("__constructor", *_words(host, scenario.constructor)) == val.VOID_VAL
    for step in scenario.setup:
        # Unreachable: an `Advance` forces `tier1_only_reason` (the
        # biconditional test below), and this leg only runs without one.
        assert isinstance(step, Call), f"{scenario.name}: {step!r} has no WASM leg"
        mini.invoke(step.method, *_words(host, step.args))

    call = scenario.invoke
    words = _words(host, call.args)
    answer: ChainValue | None = None
    code: int | None = None
    if scenario.kind == "contract_error":
        code = _wasm_code(mini, call.method, *words)
    else:
        returned = mini.invoke(call.method, *words)
        assert returned is not None, f"{call.method} returned nothing"
        answer = None if returned == val.VOID_VAL else _decoded(host, returned)
    return Outcome(
        answer=answer,
        answer_type=type(answer).__name__,
        code=code,
        refused=False,
        events=tuple(
            (tuple(_decoded(host, topic) for topic in topics), _decoded(host, data))
            for topics, data in host.events
        ),
        auth_addresses=tuple(_address(host, word) for word in host.auths),
    )


#: Every value this table can observe coming back from the mini host. The
#: harness's own decoder is typed with the `ChainValue` PROTOCOL
#: (`serpent.types._ordering`) and answers a RANK PLACEHOLDER for a vec or a
#: map, which is not a value any expectation could be written against -- so a
#: container decoded here is a table bug, and `_decoded` says so rather than
#: comparing it.
_DECODABLE = (Bool, U32, U64, Symbol, String, Address)


def _decoded(host: FullHost, word: int) -> ChainValue:
    """One `Val` word as the chain value the table's expectations are built from."""
    value = host.chain_value(word)
    assert isinstance(value, _DECODABLE), f"no scenario observes a {type(value).__name__}"
    return value


def _words(host: FullHost, args: tuple[ChainValue, ...]) -> tuple[int, ...]:
    """Each argument as the `Val` word the guest is handed."""
    return tuple(host.val_word(arg) for arg in args)


def _address(host: FullHost, word: int) -> Address:
    address = _decoded(host, word)
    assert isinstance(address, Address), address
    return address


# ===========================================================================
# the table
# ===========================================================================


@pytest.mark.parametrize("scenario", ENV_SCENARIOS, ids=[s.name for s in ENV_SCENARIOS])
def test_env_scenario(scenario: EnvScenario) -> None:
    """One row: both legs, compared to each other, then pinned to the table."""
    tier_1 = _tier_1(scenario)
    if scenario.tier1_only_reason is None:
        from_wasm = _wasm(scenario)
        assert _comparable(from_wasm) == _comparable(tier_1), (
            f"{scenario.name}: the two models disagree -- tier 1 said {tier_1}, "
            f"the compiled contract said {from_wasm}"
        )

    if scenario.kind == "value":
        assert tier_1.answer == scenario.expect, scenario.name
        assert tier_1.answer_type == type(scenario.expect).__name__, scenario.name
    elif scenario.kind == "void":
        assert tier_1.answer is None, scenario.name
    elif scenario.kind == "contract_error":
        assert tier_1.code == scenario.code, scenario.name
    else:
        assert tier_1.refused, scenario.name
    assert tier_1.events == scenario.events, scenario.name
    assert tier_1.auths == scenario.auths, scenario.name


def test_the_scenario_names_are_unique() -> None:
    names = [scenario.name for scenario in ENV_SCENARIOS]
    assert len(names) == len(set(names))


def test_a_row_is_tier_1_only_exactly_when_it_reaches_a_harness_limit() -> None:
    """The biconditional, so no row can opt out of the WASM leg for free.

    The three limits are the mini host's, and each is DERIVED from the row
    rather than trusted: an `Advance` step or a `bump_*` call means TTL, a
    `guard_args` call means the discarded args (review M11), and a non-`None`
    allow-set means the authorization state the mini host does not have.
    """
    for scenario in ENV_SCENARIOS:
        steps = (*scenario.setup, scenario.invoke)
        methods = {step.method for step in steps if isinstance(step, Call)}
        limited = (
            any(isinstance(step, Advance) for step in steps)
            or bool(methods & TTL_METHODS)
            or bool(methods & AUTH_ARGS_METHODS)
            or scenario.auth_allow_set is not None
        )
        assert (scenario.tier1_only_reason is not None) == limited, (
            f"{scenario.name}: tier1_only_reason="
            f"{scenario.tier1_only_reason!r} but the row "
            f"{'does' if limited else 'does not'} reach a harness limit"
        )


def test_the_table_drives_every_method_of_the_scenario_contract() -> None:
    """`env_surface.py` exists for this table, so every method in it is driven.

    The M8 lesson in the shape this task's fixture needs: a method added to the
    surface contract without a row that reaches it would be a surface the
    differential silently does not cover.
    """
    declared = {
        name
        for name, _params, _returns in vars(_contract_class(ENV_SURFACE))[_METADATA_ATTR]["methods"]
    }
    exercised = {
        step.method
        for scenario in ENV_SCENARIOS
        if scenario.contract == ENV_SURFACE
        for step in (*scenario.setup, scenario.invoke)
        if isinstance(step, Call)
    }
    assert exercised == declared


def test_the_two_models_share_one_ledger_default() -> None:
    """The pin the ledger rows rest on: ONE home for both defaults (S13).

    `tests/harness/hostfns.py` imports `DEFAULT_LEDGER_TIMESTAMP`/
    `DEFAULT_LEDGER_SEQUENCE` from `serpent.env`, so the mini host's stubs and
    the tier-1 `Ledger` cannot drift -- and this test restates no literal, which
    is the whole point: a test carrying its own copy of 1_700_000_000 would go
    green after someone changed one of the two homes.
    """
    host = FullHost()
    assert host.ledger_timestamp == DEFAULT_LEDGER_TIMESTAMP
    assert host.ledger_sequence == DEFAULT_LEDGER_SEQUENCE

    env = deployed_env()
    assert env.ledger().timestamp().value == DEFAULT_LEDGER_TIMESTAMP
    assert env.ledger().sequence().value == DEFAULT_LEDGER_SEQUENCE


def test_the_scenario_contract_paths_are_the_end_to_end_fixtures() -> None:
    """The two shared fixtures are named once, in `test_emitter_end_to_end.py`."""
    assert TOKEN_STYLE == END_TO_END_TOKEN_STYLE
    assert TOKEN_STYLE_CANONICAL == END_TO_END_TOKEN_STYLE_CANONICAL


@pytest.mark.parametrize(
    "path", sorted({scenario.contract for scenario in ENV_SCENARIOS}), ids=lambda p: p.stem
)
def test_a_scenario_contracts_declared_protocol_follows_its_constructor(path: Path) -> None:
    """The 2026-08-28 floor decision, applied to this table's own contracts.

    A `__constructor` export is a capability the host only honors from protocol
    22 (spec S13 / CAP-0058), so a constructor-bearing contract declares 22 and
    a constructor-less one declares the import floor, 20. Derived from the IR
    rather than listed by name, so adding an `__init__` to `env_surface.py`
    cannot silently invalidate the pin.
    """
    compiled = compile_module(path.read_text(encoding="utf-8"), str(path))
    contract = compiled.ir.contract
    assert contract is not None
    has_constructor = any(m.kind is FuncKind.CONSTRUCTOR for m in contract.methods)
    assert compiled.declared_protocol == (22 if has_constructor else 20)


def test_both_publish_spellings_pin_one_record() -> None:
    """The equivalence claim, read off the table.

    The declared-spelling row and the canonical-spelling row share ONE pinned
    `PublishedEvent` object (`LOGGED_EVENT`), so the two rows cannot be edited
    into agreeing separately -- and `test_env_scenario` has already asserted
    each spelling's tier-1 and WASM legs agree with it.
    """
    declared = _row("the_declared_event_spelling_publishes_the_record")
    canonical = _row("the_canonical_event_spelling_publishes_the_same_record")
    assert declared.events == canonical.events
    assert len(declared.events) == 1


def _row(name: str) -> EnvScenario:
    (scenario,) = [s for s in ENV_SCENARIOS if s.name == name]
    return scenario


# ===========================================================================
# property: the storage-key round trip (dossier D.5.1)
# ===========================================================================


@given(key=CHAIN_VALUES, other=CHAIN_VALUES, value=CHAIN_VALUES)
def test_a_storage_key_round_trips_by_value_and_only_under_its_own_key(
    key: ChainValue, other: ChainValue, value: ChainValue
) -> None:
    """D.5.1, as a property over every `ChainValue` shape a key can take.

    Four claims, and each of them is a real divergence the model could have:

    * the entry is found by a key REBUILT from equal parts (a deep copy, i.e. a
      different object graph). A store keyed on the object identity -- or on the
      host's handle, which is the same bug one tier down
      (`tests/harness/objects.py`'s docstring) -- passes every same-object test
      and fails this one;
    * it is NOT found under a different key, so "the write landed somewhere" is
      not mistaken for "the write landed here";
    * it is not visible in the other two durabilities: three buckets are three
      namespaces;
    * mutating the value the caller still holds cannot change what storage
      answers (ruling E5's deep-copy law) -- a `set` that stored the reference
      answers the mutated value here.

    `has` is asserted to answer a chain `Bool` rather than a Python `bool` in
    passing, because that is the one divergence value equality cannot see.
    """
    assume(storage_key(other) != storage_key(key))
    snapshot = storage_key(value)
    rebuilt = copy.deepcopy(key)
    # `frame=False` plus an explicit frame: Hypothesis runs this body many
    # times, and the helper's frame would stay open for the whole test (two
    # envs framed at once is a state the model refuses).
    env = deployed_env(frame=False)
    with env.frame():
        persistent = env.storage().persistent()
        persistent.set(key, value)

        found = persistent.get(rebuilt, type(value))
        assert type(found) is type(value)
        assert storage_key(found) == snapshot
        assert type(persistent.has(rebuilt)) is Bool
        assert persistent.has(rebuilt)

        assert not persistent.has(other)
        assert not env.storage().instance().has(rebuilt)
        assert not env.storage().temporary().has(rebuilt)

        _grow(value)
        _grow(found)
        assert storage_key(persistent.get(rebuilt, type(value))) == snapshot


# ===========================================================================
# property: publish-then-raise, the F.1.8 honest pin
# ===========================================================================


def test_an_event_published_before_a_raise_survives_in_both_models() -> None:
    """F.1.8, pinned as the DIVERGENCE it is rather than as a passing test.

    `log_then_refuse` publishes `Logged` and then raises `SurfaceError.Refused`.
    Tier 1 keeps the event (`serpent.env`'s module docstring: "no frame
    rollback... A method that publishes an event and then raises leaves the
    event in `published_events`"), and the mini host keeps it too -- it has no
    frame to roll back either.

    **On chain the answer is different: the event rolls back with the failed
    frame, and a client sees no `Logged` at all.** So this test is not evidence
    about the chain; it is the pin that says the two models agree with each
    other and that BOTH are known to differ from the host here. It is a named
    carried obligation to sub-plan F's tier 2b, which is the only tier that can
    settle it.
    """
    scenario = _row("an_event_published_before_a_raise_survives_at_both_tiers")
    tier_1 = _tier_1(scenario)
    from_wasm = _wasm(scenario)
    assert tier_1.code == 1
    assert len(tier_1.events) == 1
    assert from_wasm.events == tier_1.events


# ===========================================================================
# property: every generated event class round-trips through SCSpecEventV0
# ===========================================================================

#: The contracts whose `@contractevent` declarations this property decodes:
#: the table's own fixture, the realistic token, and the two examples that
#: declare events. Every event class in each is covered.
_EVENT_CORPUS: tuple[Path, ...] = (
    ENV_SURFACE,
    TOKEN_STYLE,
    EXAMPLE_EVENTS,
    EXAMPLE_ALLOWANCE_TOKEN,
)


def _event_classes(path: Path) -> list[type]:
    module = _module(path)
    return [
        member
        for member in vars(module).values()
        if isinstance(member, type)
        and isinstance(vars(member).get(_METADATA_ATTR), dict)
        and vars(member)[_METADATA_ATTR].get("kind") == "event"
    ]


_EVENT_CLASSES: tuple[tuple[Path, type], ...] = tuple(
    (path, declared) for path in _EVENT_CORPUS for declared in _event_classes(path)
)


def test_the_event_corpus_is_not_empty() -> None:
    """The guard that keeps the property below from being vacuously green."""
    assert len(_EVENT_CLASSES) >= 5
    assert {path for path, _declared in _EVENT_CLASSES} == set(_EVENT_CORPUS)


@pytest.mark.parametrize(
    ("path", "declared"),
    _EVENT_CLASSES,
    ids=[f"{path.stem}.{declared.__name__}" for path, declared in _EVENT_CLASSES],
)
def test_a_generated_event_class_round_trips_through_its_spec_entry(
    path: Path, declared: type
) -> None:
    """Every `@contractevent` class decodes back to the convention it declares.

    The bytes are read out of the BUILT module's own `contractspecv0` section
    -- not from `build_spec_entries` called directly -- so what round-trips is
    what a client would actually fetch from the ledger. Three things are
    compared against the decorator's metadata, and they are exactly the three a
    publisher and an indexer have to agree on: the PREFIX TOPICS, the
    per-parameter LOCATIONS (in declaration order), and the DATA FORMAT.
    """
    entries = _unpack(_wasm_custom_section(_built(path).wasm, "contractspecv0"))
    (entry,) = [
        candidate.event_v0
        for candidate in entries
        if candidate.event_v0 is not None
        and candidate.event_v0.name.sc_symbol.decode() == declared.__name__
    ]
    metadata = vars(declared)[_METADATA_ATTR]

    assert [topic.sc_symbol.decode() for topic in entry.prefix_topics] == list(
        metadata["prefix_topics"]
    )
    assert [param.name.decode() for param in entry.params] == [
        name for name, _annotation in metadata["fields"]
    ]
    assert [_location_name(param.location) for param in entry.params] == [
        metadata["locations"][name] for name, _annotation in metadata["fields"]
    ]
    assert _format_name(entry.data_format) == metadata["data_format"]


def _location_name(location: xdr.SCSpecEventParamLocationV0) -> str:
    """The decorator's own location word for one decoded XDR case."""
    (name,) = [key for key, case in _PARAM_LOCATIONS.items() if case == location]
    return name


def _format_name(data_format: xdr.SCSpecEventDataFormat) -> str:
    (name,) = [key for key, case in _DATA_FORMATS.items() if case == data_format]
    return name


# ===========================================================================
# property: F.1.6 -- every SPT1034 help rewrite compiles
# ===========================================================================

#: `(a rejected program, the substring its `help:` must carry, the rewrite that
#: help recommends)`. The rewrite is the CLAIM: a `help:` line that recommends
#: something the compiler refuses is worse than no help at all, because the
#: author follows it and gets a second rejection.
_HELP_REWRITES: tuple[tuple[str, str, str, str], ...] = (
    (
        "a_temporary_receiver",
        """
from serpent import Env, U32, Vec, contract


@contract
class C:
    def go(self, env: Env) -> U32:
        Vec(U32, [U32(1)]).push_back(U32(2))
        return U32(0)
""",
        "bind the container to a local first",
        """
from serpent import Env, U32, Vec, contract


@contract
class C:
    def go(self, env: Env) -> U32:
        v = Vec(U32, [U32(1)])
        v.push_back(U32(2))
        return len(v)
""",
    ),
    (
        "a_vec_parameter",
        """
from serpent import Env, U32, Vec, contract


@contract
class C:
    def go(self, env: Env, v: Vec[U32]) -> U32:
        v.push_back(U32(2))
        return U32(0)
""",
        "slice(U32(0), len(<container>))",
        """
from serpent import Env, U32, Vec, contract


@contract
class C:
    def go(self, env: Env, source: Vec[U32]) -> U32:
        v = source.slice(U32(0), len(source))
        v.push_back(U32(2))
        return len(v)
""",
    ),
    (
        "a_map_parameter",
        """
from serpent import Env, Map, Symbol, U32, contract


@contract
class C:
    def go(self, env: Env, m: Map[Symbol, U32]) -> U32:
        m.set(Symbol("k"), U32(1))
        return U32(0)
""",
        "build it with `m = Map(K, V)` and `set(...)` into it",
        """
from serpent import Env, Map, Symbol, U32, contract


@contract
class C:
    def go(self, env: Env, key: Symbol) -> U32:
        m = Map(Symbol, U32)
        m.set(key, U32(1))
        return len(m)
""",
    ),
)


@pytest.mark.parametrize(
    ("name", "rejected", "help_text", "rewrite"),
    _HELP_REWRITES,
    ids=[name for name, _rejected, _help, _rewrite in _HELP_REWRITES],
)
def test_every_spt1034_help_rewrite_compiles(
    name: str, rejected: str, help_text: str, rewrite: str
) -> None:
    """F.1.6: the rewrite an `SPT1034` `help:` line names really compiles.

    Both halves are asserted in one test on purpose. Compiling the rewrite
    alone would go green after someone reworded the help into recommending
    something else, so the rejected program is compiled first and its OWN
    `help:` text is what has to carry the phrase the rewrite implements.
    """
    with pytest.raises(CompileError) as info:
        compile_module(rejected, f"<{name}>")
    mutation = [d for d in info.value.diagnostics if d.code == "SPT1034"]
    assert len(mutation) == 1, [(d.code, d.message) for d in info.value.diagnostics]
    assert mutation[0].help is not None
    assert help_text in mutation[0].help, mutation[0].help

    compile_module(rewrite, f"<{name}_rewrite>")


def test_the_last_resort_spt1034_help_recommends_something_that_compiles() -> None:
    """The registry's own `SPT1034` `help:` -- the last-resort default no
    emission uses today (`recognize._HELP`) -- names the owned-local rewrite,
    and that rewrite compiles too.

    Asserted separately because there is no program that draws it: every real
    emission passes a receiver-specific help (`_mutation_help`), so the default
    can only be checked against its own text.
    """
    from serpent.compiler.recognize import _HELP

    assert "mutate only a local this method owns" in _HELP["SPT1034"]
    assert "vec_push_back(v, x)" in _HELP["SPT1034"]
    compile_module(
        """
from serpent import Env, U32, Vec, contract


@contract
class C:
    def go(self, env: Env) -> Vec[U32]:
        v = Vec(U32, [U32(1)])
        v.push_back(U32(2))
        return v
""",
        "<owned_local_rewrite>",
    )


# ===========================================================================
# the model's own rough edge, at the top of the U32 ledger range
# ===========================================================================


def test_a_ledger_sequence_past_the_u32_range_is_the_models_own_rough_edge() -> None:
    """`advance` moves a plain Python int, and `ledger().sequence()` is a `U32`.

    Two TTL rows drive the sequence to `2**32 - 1` and one past it (Task 3's
    carried obligation, the rough edge), and they read STORAGE there -- which
    keeps answering, because expiry is integer arithmetic the model owns. A
    LEDGER READ is the surface that cannot follow: `U32` refuses the value, with
    a `ValueError` that is not a `ContractError`, because no contract error code
    describes a test hook driven past the range the host's own ledger sequence
    lives in.

    Pinned rather than fixed: on chain the sequence cannot exceed a `u32` at
    all, so the state this test names is tier-1-only in the strongest sense --
    the model can reach it and the chain cannot.
    """
    env = Env(sequence=2**32 - 1)
    instance: Any = deploy(_contract_class(ENV_SURFACE), env)
    with env.frame():
        assert instance.ledger_seq(env) == U32(2**32 - 1)
    env.advance(1)
    with env.frame(), pytest.raises(ValueError):
        instance.ledger_seq(env)


def test_the_scenario_fixture_reaches_the_surfaces_no_shipped_contract_can() -> None:
    """Why `tests/fixtures/env_surface.py` exists, as an assertion.

    Its two reasons are a bare `get` (the `MissingValue` path) and
    `require_auth_for_args`; both show up as host functions in the built
    module's import set, and `has_contract_data` beside `get_contract_data` is
    the E13 guard the bare read is lowered through.
    """
    imports = set(_built(ENV_SURFACE).imports)
    assert {"get_contract_data", "has_contract_data", "require_auth_for_args"} <= imports
