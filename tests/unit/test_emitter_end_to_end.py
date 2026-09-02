"""End to end: every whole contract the repo has, built and RUN (F.2.2/F.2.3/F.2.4/F.2.7/F.2.8).

`tests/unit/test_emitter_semantics.py` runs one expression per module. This
file runs whole contracts -- storage, structs, error enums, events, auth, a
constructor, a module-level helper, a `Vec` handed back across the ABI -- and
the headline one is anchored to Phase 0:

* **`spike1_reauthored.py`** is serpent's re-authoring of the contract that was
  actually deployed to testnet, fetched back byte-identical, and exercised on
  chain (`spikes/spike1/ACCEPTANCE.md` rows 1-9). Three separate claims are
  made against the deployed artifact -- the IMPORT set, the `contractspecv0`
  bytes, and the behavior sequence -- because each is anchored to a different
  recorded fact.
* **`token_style.py`** (F.2.7) is the realistic shape: a struct storage key, a
  heterogeneous event topic tuple, `require_auth`, an `Address` comparison. Its
  event is published through the AUTHORING form (`Transfer(...).publish(env)`,
  M1-E), and **`token_style_canonical.py`** publishes the equivalent event the
  CANONICAL way (`env.events().publish((Symbol, Address, Address), data)`) --
  the pair is what proves the desugar and the hand-written call reach the host
  with the same topics and the same data
  (`test_both_publish_spellings_record_the_same_event`).
* **the two promoted sandbox contracts** (F.2.8) are the contracts an author
  actually plays with, copied into `tests/fixtures/` so that playing with
  `sandbox/` cannot turn the suite red. Each copy is asserted to build to a
  module byte-identical to its `sandbox/` original, which is what keeps the
  copy honest without a text compare that a reflowed comment would break.
* **the `examples/` contracts** (M1-E, `EXAMPLES`) are the SHIPPED examples, and
  they are fixtures of this suite for the same reason the sandbox copies are:
  every whole-contract property in this file's last section runs over them for
  free, so a shipped example cannot rot. Their tier-1 runs and the
  tier-1-vs-wasm cross-checks live in `tests/unit/test_examples.py`, which
  imports `EXAMPLES` from here so there is one list.

## `spikes/` is read-only, and `spike.wasm` is not tracked (R5, review M14)

`spikes/spike1/spike.wasm` is in `.gitignore`'s shadow: `git ls-files` does not
list it, so a fresh clone does not have it. Every assertion against it is
therefore guarded with `pytest.mark.skipif(not SPIKE_WASM.exists())` --
skipped, never silently passed. `tests/unit/test_sections.py`'s three
spike-artifact tests had NO such guard when this task landed (they would have
errored with `FileNotFoundError` on a fresh clone rather than skipping); the
same guard was added there, so the convention is one convention and not two.

## What a green run here means

Ruling E1 again: `tests/harness` mirrors tier 1, so a green run is "the codegen
is self-consistent", not "this contract is correct on chain". The three claims
that ARE chain-anchored are the ones compared against the deployed artifact's
own bytes; everything else is self-consistency, and sub-plan F's tier 2b is
where it gets re-proved against a real host.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from serpent import val
from serpent.compiler.frontend import compile_module
from serpent.compiler.ir import FuncKind
from serpent.compiler.types_ import Ty
from serpent.emitter import BuildResult, build_wasm
from serpent.spec import build_env_meta
from serpent.types import U32, Address, Bool, String, Symbol
from tests.fixtures.token_style import Transfer
from tests.harness import cache, engine
from tests.harness.hostfns import FullHost
from tests.unit.conftest import deployed_env
from tests.unit.test_emitter_semantics import decode_val

# The eight Phase 0 host functions, imported from the file that pins them.
from tests.unit.test_protocol_floor import PHASE0_FNS

# `_wasm_custom_section` (a deliberately tiny section reader), `SPIKE_WASM` and
# its on-chain sha256 are imported from `test_sections.py` rather than
# re-derived: that file is where the artifact's provenance is pinned, and two
# copies of a path plus a hash is exactly the drift this repo keeps avoiding.
from tests.unit.test_sections import SPIKE_WASM, SPIKE_WASM_SHA256, _wasm_custom_section

_ROOT = Path(__file__).resolve().parents[2]

#: Two real strkeys (account, contract), lifted from `tests/semantics/cases.py`
#: so the `Address` values here are ones already known to decode.
ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"
CONTRACT = "CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI"

SPIKE1 = _ROOT / "tests" / "fixtures" / "spike1_reauthored.py"
TOKEN_STYLE = _ROOT / "tests" / "fixtures" / "token_style.py"
TOKEN_STYLE_CANONICAL = _ROOT / "tests" / "fixtures" / "token_style_canonical.py"
SANDBOX_COUNTER = _ROOT / "tests" / "fixtures" / "sandbox_counter.py"
SANDBOX_HELLO_WORLD = _ROOT / "tests" / "fixtures" / "sandbox_hello_world.py"

#: `examples/` -- the SHIPPED contracts (M1-E sub-plan G's wave 1), which are
#: fixtures of this suite as well as documentation. Defined here rather than in
#: `tests/unit/test_examples.py` so that `EXAMPLES` and `FIXTURES` cannot
#: disagree: the tier-1 legs import these names FROM here, and
#: `test_examples_is_a_flat_directory_of_modules` asserts the tuple is the whole
#: directory, so a new example joins the whole-contract property sweep (build,
#: validate, size, `needed <= linked`, exports, protocol floor) by existing.
EXAMPLES_DIR = _ROOT / "examples"
EXAMPLE_COUNTER = EXAMPLES_DIR / "counter.py"
EXAMPLE_ERRORS = EXAMPLES_DIR / "errors.py"
EXAMPLE_STRUCTS = EXAMPLES_DIR / "structs.py"
EXAMPLE_EVENTS = EXAMPLES_DIR / "events.py"
EXAMPLE_ALLOWANCE_TOKEN = EXAMPLES_DIR / "allowance_token.py"
EXAMPLE_SHAPES = EXAMPLES_DIR / "shapes.py"
EXAMPLES: tuple[Path, ...] = (
    EXAMPLE_COUNTER,
    EXAMPLE_ERRORS,
    EXAMPLE_STRUCTS,
    EXAMPLE_EVENTS,
    EXAMPLE_ALLOWANCE_TOKEN,
    EXAMPLE_SHAPES,
)

#: THE fixture list this sub-plan's whole-contract properties run over. Defined
#: here, where the contracts are built and invoked, and imported by
#: `tests/unit/test_emitter_fuzz.py` for the two budget-shaped properties (the
#: size tripwire and `needed <= linked`) so there is ONE list of fixtures.
FIXTURES: tuple[Path, ...] = (
    SPIKE1,
    TOKEN_STYLE,
    TOKEN_STYLE_CANONICAL,
    SANDBOX_COUNTER,
    SANDBOX_HELLO_WORLD,
    *EXAMPLES,
)

#: `pytest.mark.skipif` for every assertion that reads the deployed artifact
#: (review M14). The file is NOT git-tracked, so a fresh clone legitimately
#: does not have it -- and a skip says so, where a silently-passing test would
#: claim an on-chain anchor the run never checked.
requires_spike_wasm = pytest.mark.skipif(
    not SPIKE_WASM.exists(),
    reason=f"{SPIKE_WASM} is not git-tracked (R5); build it with spikes/spike1/build.py",
)


def build_fixture(path: Path) -> BuildResult:
    """`cache.built(path)`, kept as a thin alias so no other caller moves.

    `build_file` with external validation left at its default (ruling E5: run
    `wasm-tools` when it is on PATH, skip it when it is not), memoised by
    `tests/harness/cache.py` on `(resolved path, sha256 of the file text)` --
    this file's whole-contract properties replay every fixture repeatedly, and
    the cache is what keeps that from recompiling each one every time.
    """
    return cache.built(path)


def start(path: Path) -> tuple[BuildResult, FullHost, engine.MiniHost]:
    """Build `path` and instantiate it under the full mini host."""
    built = build_fixture(path)
    host = FullHost()
    mini = engine.MiniHost(built.wasm, imports=host.bindings())
    host.attach(mini)
    return built, host, mini


# ===========================================================================
# spike1_reauthored: the Phase 0 anchor
# ===========================================================================

#: The import set `spike1_reauthored.py` must emit, DERIVED rather than
#: recorded from a run:
#:
#: * `spikes/spike1/ACCEPTANCE.md` records EIGHT imports for the deployed
#:   artifact, and `tests/unit/test_protocol_floor.py` pins them as
#:   `PHASE0_FNS`: `put_contract_data`, `has_contract_data`,
#:   `get_contract_data`, `map_new_from_linear_memory`, `map_get`,
#:   `symbol_new_from_linear_memory`, `string_new_from_linear_memory`,
#:   `fail_with_error`.
#: * Ruling E13 makes sub-plan D emit a `has_contract_data` guard before every
#:   `get_contract_data` (a read of an absent key is undefined on a real host),
#:   and every error path calls `fail_with_error`. Both were ALREADY in the
#:   eight -- the spike's own frontend emitted the same guard and the same
#:   raise -- so E13 adds NO new name here and the expected set is exactly
#:   `PHASE0_FNS`.
#:
#: That last bullet is the point of stating the set at all: "the eight plus
#: whatever E13 added" could easily have been nine or ten, and asserting
#: equality (not a subset) is what makes a ninth import a loud failure.
SPIKE1_IMPORTS: frozenset[str] = frozenset(PHASE0_FNS)


def test_spike1_emits_exactly_the_eight_phase0_imports() -> None:
    built = build_fixture(SPIKE1)
    assert set(built.imports) == SPIKE1_IMPORTS


def test_spike1_import_set_is_the_eight_and_e13_added_none() -> None:
    """The derivation above, asserted rather than only commented: the two names
    ruling E13's guard and the raise path reach are IN the recorded eight, so
    the emitted set neither grew nor shrank."""
    assert {"has_contract_data", "fail_with_error"} <= SPIKE1_IMPORTS
    assert len(SPIKE1_IMPORTS) == 8


def test_spike1_exports_the_two_deployed_entry_points_in_source_order() -> None:
    assert build_fixture(SPIKE1).exports == ("setup", "bump")


@requires_spike_wasm
def test_the_spike_artifact_is_the_one_fetched_back_off_testnet() -> None:
    """The provenance pin, restated here so this file's chain-anchored claims
    do not depend on another module's test having run first."""
    import hashlib

    assert hashlib.sha256(SPIKE_WASM.read_bytes()).hexdigest() == SPIKE_WASM_SHA256


@requires_spike_wasm
def test_spike1_spec_section_byte_equals_the_deployed_spec_section() -> None:
    """serpent's whole build, not just `serpent.spec`: the `contractspecv0`
    payload the EMITTER wrote into the module byte-equals the section in the
    artifact that ran on chain.

    `tests/unit/test_sections.py` makes the same claim about
    `build_spec_entries` called directly; this one goes through
    `compile_module` -> `build_wasm` -> section assembly, so it also covers the
    emitter's own choice of which types to pass and in what order (B10).
    """
    built = build_fixture(SPIKE1)
    deployed = _wasm_custom_section(SPIKE_WASM.read_bytes(), "contractspecv0")
    assert _wasm_custom_section(built.wasm, "contractspecv0") == deployed


def test_spike1_env_meta_declares_the_computed_floor_of_twenty() -> None:
    """E9's documented divergence from the artifact, asserted in both
    directions.

    serpent declares the COMPUTED floor: all eight host functions are ungated,
    so the floor is `BASE_PROTOCOL == 20` and `contractenvmetav0` carries 20.
    The deployed artifact carries 27 -- the spike hand-set its target -- and
    that difference is a decision (spec §4: "declared protocol is computed,
    never hand-set"), not a bug, which is why the byte compare above is scoped
    to `contractspecv0` and this test states the env-meta claim separately.
    """
    built = build_fixture(SPIKE1)
    assert built.declared_protocol == 20
    assert _wasm_custom_section(built.wasm, "contractenvmetav0") == build_env_meta(20)


@requires_spike_wasm
def test_the_deployed_artifact_declares_twenty_seven_and_serpent_declares_twenty() -> None:
    """The other half of E9: the divergence is real and is pinned, so nobody
    later "fixes" serpent's 20 to match an artifact number without noticing
    that the artifact's number is the hand-set one."""
    deployed = _wasm_custom_section(SPIKE_WASM.read_bytes(), "contractenvmetav0")
    assert deployed == build_env_meta(27)
    assert build_fixture(SPIKE1).declared_protocol == 20


def test_spike1_behaves_as_the_deployed_contract_did() -> None:
    """The recorded on-chain behavior sequence (P7 / findings §1 rows 5-6).

    `setup(3)` stores the settings struct; `bump()` answers 1, then 2, then 3;
    the FOURTH `bump()` crosses `counter_limit` and raises
    `Error.LimitExceeded`, whose `errorcode(7)` must arrive as a Contract-type
    error `Val` with code 7 -- `SCE_CONTRACT` in XDR terms, which is what makes
    it a contract error a client can classify rather than an opaque host
    failure (S10).

    The intermediate answers are asserted one at a time rather than as a list,
    because a counter that returned 1, 1, 1 and a counter that returned 1, 2, 3
    both "raise on the fourth call" if the limit check is wrong in the matching
    way.
    """
    _built, host, mini = start(SPIKE1)
    assert mini.invoke("setup", val.pack_u32val(3)) == val.VOID_VAL

    for expected in (1, 2, 3):
        word = mini.invoke("bump")
        assert word is not None
        assert host.chain_value(word) == U32(expected)

    with pytest.raises(engine.HostError) as info:
        mini.invoke("bump")
    error_val = info.value.val
    assert val.error_code_of(error_val) == 7
    assert val.error_type_of(error_val) == val.ERROR_TYPE_CONTRACT
    assert val.is_contract_error_val(error_val)
    # The counter did not advance past the limit: the raise happens BEFORE the
    # write, so a fifth `bump()` fails the same way rather than succeeding.
    with pytest.raises(engine.HostError):
        mini.invoke("bump")


def test_spike1_stores_the_long_field_name_through_linear_memory() -> None:
    """Why the fixture has a 13-character field: `counter_limit` cannot fit a
    `SymbolSmall` (9 characters), so the struct key must be built through
    `symbol_new_from_linear_memory` -- which is what puts that name in the
    eight, and what makes this contract exercise the literal pool and the data
    section at all. Asserted through the host's own call log, so a lowering
    that started inlining a 13-character symbol would be visible.

    The two linear-memory constructors land in DIFFERENT methods, which is why
    both are invoked here: `setup` builds the struct with
    `map_new_from_linear_memory` (whose key descriptors are read straight out
    of the pool, so no standalone symbol is ever materialized) and pools the
    `String`; `bump` has to materialize `counter_limit` as a `SymbolObject` of
    its own before it can `map_get` the field back out.
    """
    _built, host, mini = start(SPIKE1)
    mini.invoke("setup", val.pack_u32val(1))
    assert host.count("string_new_from_linear_memory") > 0
    assert host.count("map_new_from_linear_memory") > 0
    assert host.count("symbol_new_from_linear_memory") == 0

    mini.invoke("bump")
    assert host.count("symbol_new_from_linear_memory") > 0
    assert host.count("map_get") > 0


# ===========================================================================
# token_style: the realistic shape (F.2.7)
# ===========================================================================


def test_token_style_runs_a_full_mint_and_transfer_sequence() -> None:
    """The F.2.7 fixture end to end, in one sequence because the state is the
    point: a struct storage key written by `mint` must be FOUND by `balance`
    and by `transfer`, and each of those builds a fresh `BalanceKey` map object
    with a fresh `AddressObject` inside it. A store keyed on the handle would
    give every call its own empty balance -- a plausible number, silently
    wrong (`test_harness_hostfns.py` holds the same bug down at the rig
    level; this is the end-to-end version).
    """
    _built, host, mini = start(TOKEN_STYLE)
    admin = host.val_word(Address(ACCOUNT))
    other = host.val_word(Address(CONTRACT))

    assert mini.invoke("__constructor", admin, host.val_word(String("Serpent"))) == val.VOID_VAL
    name = mini.invoke("name")
    assert name is not None
    assert decode_val(name, _ty_of(TOKEN_STYLE, "name"), host) == String("Serpent")

    assert _answer(host, mini, "is_admin", admin) == Bool(True)
    assert _answer(host, mini, "is_admin", other) == Bool(False)

    assert mini.invoke("mint", admin, other, val.pack_u32val(100)) == val.VOID_VAL
    assert _answer(host, mini, "balance", other) == U32(100)
    assert _answer(host, mini, "balance", admin) == U32(0)

    assert mini.invoke("transfer", other, admin, val.pack_u32val(40)) == val.VOID_VAL
    assert _answer(host, mini, "balance", other) == U32(60)
    assert _answer(host, mini, "balance", admin) == U32(40)

    # `require_auth` was called by `mint` (on `admin`) and by `transfer` (on
    # `frm`, i.e. `other`) -- in that order, and by nobody else.
    assert [host.chain_value(a) for a in host.auths] == [Address(ACCOUNT), Address(CONTRACT)]

    # One event, with the canonical heterogeneous topic tuple.
    ((topics, data),) = host.events
    assert [host.chain_value(t) for t in topics] == [
        Symbol("transfer"),
        Address(CONTRACT),
        Address(ACCOUNT),
    ]
    assert host.chain_value(data) == U32(40)


def test_the_wasm_event_equals_the_tier_1_record_for_the_same_publish() -> None:
    """The S13 differential for `Event.publish`, stated DIRECTLY (review m8).

    Two absolute pins ("the WASM leg records these words", "the tier-1 leg
    records these values") can BOTH be updated to agree with a drifting
    convention. This compares the two legs to each other instead: the same
    `Transfer` publish, once compiled and run under `FullHost`, once executed by
    the tier-1 model through the ordinary authoring surface, decoded to the same
    chain values.

    Same fixture class on both sides (`tests.fixtures.token_style.Transfer`), so
    the convention is read from ONE declaration -- which is the whole point of
    the five-layer design: the decorator metadata is what both tiers consume.
    """
    _built, host, mini = start(TOKEN_STYLE)
    admin = host.val_word(Address(ACCOUNT))
    other = host.val_word(Address(CONTRACT))
    mini.invoke("__constructor", admin, host.val_word(String("Serpent")))
    mini.invoke("mint", admin, other, val.pack_u32val(100))
    mini.invoke("transfer", other, admin, val.pack_u32val(40))
    ((topics, data),) = host.events
    from_wasm = (tuple(host.chain_value(t) for t in topics), host.chain_value(data))

    env = deployed_env()
    Transfer(from_=Address(CONTRACT), to=Address(ACCOUNT), amount=U32(40)).publish(env)
    (from_tier_1,) = env.published_events

    assert from_wasm == from_tier_1


def test_both_publish_spellings_record_the_same_event() -> None:
    """The end-to-end half of M1-E's both-spellings equivalence (ruling E2).

    `token_style.transfer` publishes through `Transfer(...).publish(env)` and
    `token_style_canonical.send` publishes the same event through
    `env.events().publish((Symbol("transfer"), frm, to), amount)`. Run under the
    mini host, the two must arrive at `contract_event` with the SAME topic words
    and the same data word -- compared as decoded chain values, because the
    handles are per-instantiation.

    This is the claim that makes "the emitter needed no change" observable: the
    frontend IR goldens show the two trees are equal, and this shows the two
    modules really do call the host the same way.
    """
    _built, host, mini = start(TOKEN_STYLE)
    admin = host.val_word(Address(ACCOUNT))
    other = host.val_word(Address(CONTRACT))
    mini.invoke("__constructor", admin, host.val_word(String("Serpent")))
    mini.invoke("mint", admin, other, val.pack_u32val(100))
    mini.invoke("transfer", other, admin, val.pack_u32val(40))
    ((desugared_topics, desugared_data),) = host.events

    _built2, host2, mini2 = start(TOKEN_STYLE_CANONICAL)
    admin2 = host2.val_word(Address(ACCOUNT))
    other2 = host2.val_word(Address(CONTRACT))
    mini2.invoke("__constructor", admin2)
    mini2.invoke("send", other2, admin2, val.pack_u32val(40))
    ((canonical_topics, canonical_data),) = host2.events

    assert [host.chain_value(t) for t in desugared_topics] == [
        host2.chain_value(t) for t in canonical_topics
    ]
    assert host.chain_value(desugared_data) == host2.chain_value(canonical_data)
    # ... and both are the shape D4 calls canonical.
    assert [host2.chain_value(t) for t in canonical_topics] == [
        Symbol("transfer"),
        Address(CONTRACT),
        Address(ACCOUNT),
    ]


#: The two data formats no fixture publishes, in one throwaway contract: the
#: default `"map"` (a heterogeneous payload -- `U32` and `String`) and `"vec"`.
#: The fixtures cover `"single-value"` twice over.
_EVENT_FORMATS = '''"""Both container data formats, published through the authoring form."""

from serpent import Address, Annotated, Env, Event, String, U32, contract, contractevent, topic


@contractevent
class Traded(Event):
    who: Annotated[Address, topic]
    amount: U32
    memo: String


@contractevent(data_format="vec")
class Scored(Event):
    first: U32
    second: U32


@contract
class Formats:
    def trade(self, env: Env, who: Address, amount: U32, memo: String) -> None:
        Traded(who=who, amount=amount, memo=memo).publish(env)

    def score(self, env: Env, first: U32, second: U32) -> None:
        Scored(first=first, second=second).publish(env)
'''


def test_the_container_event_data_formats_build_and_run_unchanged() -> None:
    """`"map"` and `"vec"` event data, through the UNTOUCHED emitter (E2).

    The desugar's premise is that an event's data payload is already an IR node
    the emitter lowers: `"map"` is a `MakeStruct` (compile-time sorted `Symbol`
    key descriptors + runtime `Val` words, `map_new_from_linear_memory`) and
    `"vec"` is a `MakeVec`. This runs both and reads the host objects back, so
    the claim is observed rather than argued -- including the ORDER of the map's
    keys, which the host requires ascending and which C, not D, decided.
    """
    built = build_wasm(compile_module(_EVENT_FORMATS, "contracts/formats.py"))
    host = FullHost()
    mini = engine.MiniHost(built.wasm, imports=host.bindings())
    host.attach(mini)

    who = host.val_word(Address(ACCOUNT))
    mini.invoke("trade", who, val.pack_u32val(5), host.val_word(String("hi")))
    mini.invoke("score", val.pack_u32val(1), val.pack_u32val(2))

    (map_topics, map_data), (vec_topics, vec_data) = host.events
    assert [host.chain_value(t) for t in map_topics] == [Symbol("traded"), Address(ACCOUNT)]
    # The map's keys are `(tag, bytes)` pairs as the store normalizes them; what
    # matters here is the names, their ASCENDING order, and the values.
    keys = [key for key in host._map(map_data)]
    names = [key[1] for key in keys if isinstance(key, tuple)]
    assert len(names) == len(keys)
    assert names == [b"amount", b"memo"] == sorted(names)
    values = [host.chain_value(word) for word in host._map(map_data).values()]
    assert values == [U32(5), String("hi")]

    assert [host.chain_value(t) for t in vec_topics] == [Symbol("scored")]
    assert [host.chain_value(item) for item in host._vec(vec_data)] == [U32(1), U32(2)]


def test_token_style_refuses_a_transfer_larger_than_the_balance() -> None:
    """`raise TokenError.InsufficientBalance` -> `errorcode(1)`, Contract type,
    and the state is UNCHANGED: the check is before both writes, so a partial
    transfer is not possible."""
    _built, host, mini = start(TOKEN_STYLE)
    admin = host.val_word(Address(ACCOUNT))
    other = host.val_word(Address(CONTRACT))
    mini.invoke("__constructor", admin, host.val_word(String("Serpent")))
    mini.invoke("mint", admin, other, val.pack_u32val(10))

    with pytest.raises(engine.HostError) as info:
        mini.invoke("transfer", other, admin, val.pack_u32val(11))
    assert val.error_code_of(info.value.val) == 1
    assert val.error_type_of(info.value.val) == val.ERROR_TYPE_CONTRACT
    assert _answer(host, mini, "balance", other) == U32(10)
    assert _answer(host, mini, "balance", admin) == U32(0)
    assert host.events == []


# ===========================================================================
# the two promoted sandbox contracts (F.2.8)
# ===========================================================================


@pytest.mark.parametrize(
    ("promoted", "original"),
    [
        (SANDBOX_COUNTER, _ROOT / "sandbox" / "counter.py"),
        (SANDBOX_HELLO_WORLD, _ROOT / "sandbox" / "hello_world.py"),
        (EXAMPLE_COUNTER, _ROOT / "sandbox" / "counter.py"),
    ],
    ids=["counter", "hello_world", "graduated_counter"],
)
def test_a_promoted_sandbox_copy_builds_the_same_module_as_its_original(
    promoted: Path, original: Path
) -> None:
    """The anti-drift check for F.2.8's promotion, stated on the BUILDS.

    **The third row is M1-E Task 7's graduation.** `examples/counter.py` is
    `tests/fixtures/sandbox_counter.py`'s contract verbatim, promoted out of the
    test suite into the shipped examples, and the brief offered two ways to keep
    the chain honest: turn the fixture into a path constant that re-exports the
    example, or point the byte compare at the example. The second is by far the
    smaller edit -- one parametrize row -- because the fixture is named by four
    separate inventories (`FIXTURES` here, `FIXTURE_NAMES` and a golden in
    `test_emitter_printer.py`, `_FIXTURES` in `test_harness_hostfns.py`, the fuzz
    corpus in `test_frontend_fuzz.py`), every one of which builds it as a
    contract. So the row is ADDED rather than retargeted: all three files now
    build to the same module, and any two of them drifting apart fails here.

    A text compare would break on the added docstring and on `ruff format`'s
    spacing, neither of which can change a single emitted byte. A build compare
    breaks on exactly what matters -- a change to the contract -- and doubles
    as one more witness for Task 11's determinism claim: the same contract
    through two different `path` arguments is the same module, byte for byte.

    **The sandbox side is a SKIP, not a failure, when it does not compile**
    (review round 1, Important 2). The whole reason the copies exist is that
    `sandbox/` is scratch an author is invited to break on purpose -- so a test
    that builds the original unconditionally would put a mid-suite
    `CompileError` traceback in front of somebody who broke the sandbox
    deliberately, which is precisely the coupling the promotion removed. The
    PROMOTED copy is built first and unconditionally, so a real emitter
    regression still fails here; only the comparison against a currently-broken
    original is skipped, and it says so.

    The `except Exception` is deliberately broad and it is not hiding anything:
    compiling EXECUTES a module's top-level code (ruling E1's hybrid frontend),
    so a half-finished sandbox edit can raise essentially anything -- a
    `CompileError`, a `SyntaxError` from the loader, a `NameError` out of a
    decorator, an `OSError` if the file was renamed. Every one of those means
    the same thing here ("the sandbox is mid-edit"), and none of them can come
    from the promoted copy, which was already built on the line above.

    If this FAILS (rather than skips), the fix is to re-review the sandbox
    change and re-promote it deliberately, NOT to relax the assertion.
    """
    ours = build_fixture(promoted).wasm
    try:
        theirs = build_fixture(original).wasm
    except Exception as exc:  # noqa: BLE001 - see the docstring
        pytest.skip(
            f"sandbox original does not currently compile -- sandbox/ is scratch and is "
            f"meant to be broken; drift compare skipped ({original.name}: "
            f"{type(exc).__name__}: {exc})"
        )
    assert ours == theirs


def test_sandbox_counter_counts_and_then_refuses() -> None:
    """`increment(step)` accumulates and `total()` reads without changing --
    two entry points over one persistent key, which is the smallest contract
    that can have a state bug at all. The ceiling raise is asserted to leave
    the total untouched, so `MaxReached` really is checked before the write."""
    _built, host, mini = start(SANDBOX_COUNTER)
    assert _answer(host, mini, "total") == U32(0)
    assert _answer(host, mini, "increment", val.pack_u32val(5)) == U32(5)
    assert _answer(host, mini, "increment", val.pack_u32val(7)) == U32(12)
    assert _answer(host, mini, "total") == U32(12)

    with pytest.raises(engine.HostError) as info:
        mini.invoke("increment", val.pack_u32val(1000))
    assert val.error_code_of(info.value.val) == 1
    assert val.error_type_of(info.value.val) == val.ERROR_TYPE_CONTRACT
    assert _answer(host, mini, "total") == U32(12)


def test_sandbox_hello_world_returns_the_vec_it_built() -> None:
    """`hello(name) -> Vec[Symbol]`: a host object the GUEST built, handed back
    across the ABI.

    The vector's CONTENTS are read back through the store, not just its handle:
    a `vec_new`/`vec_push_back` sequence that pushed in the wrong order, or
    pushed the same element twice, would return a perfectly good `VecObject`
    handle of the right length. `[greeting, name]` in that order is what the
    contract says.

    Also exercised here, and nowhere else in this file: `__init__` compiled as
    `__constructor` (S6/B11) and a MODULE-LEVEL helper called from two methods
    (`FuncKind.INTERNAL`, ruling E8) -- `set_greeting_salutation` is the only
    place either happens across the four fixtures.
    """
    built, host, mini = start(SANDBOX_HELLO_WORLD)
    assert built.exports == ("__constructor", "set_greeting", "get_greeting", "hello")

    assert mini.invoke("__constructor", host.val_word(Symbol("Hi"))) == val.VOID_VAL
    assert _answer(host, mini, "get_greeting") == Symbol("Hi")

    word = mini.invoke("hello", host.val_word(Symbol("Ana")))
    assert word is not None
    assert val.tag_of(word) == val.TAG_VEC_OBJECT
    assert _vec_items(host, word) == [Symbol("Hi"), Symbol("Ana")]

    # The default arm: a fresh instance has no GREETING, so `hello` falls back
    # to `Symbol("Hola")` -- the storage `default=` path, not the stored one.
    _built2, host2, mini2 = start(SANDBOX_HELLO_WORLD)
    fresh = mini2.invoke("hello", host2.val_word(Symbol("Ana")))
    assert fresh is not None
    assert _vec_items(host2, fresh) == [Symbol("Hola"), Symbol("Ana")]


def test_sandbox_hello_world_refuses_the_unimaginative_greeting() -> None:
    """The helper's `Symbol` comparison, reached from a method: `Symbol("Hello")`
    is rejected with `errorcode(1)`. `Symbol` compares through `obj_cmp` (T5 --
    the small form's packed 6-bit codes order differently from raw bytes, so a
    bare `i64.eq` would be a divergence), and this is the only fixture that
    compares two `Symbol`s at all."""
    _built, host, mini = start(SANDBOX_HELLO_WORLD)
    mini.invoke("__constructor", host.val_word(Symbol("Hi")))
    with pytest.raises(engine.HostError) as info:
        mini.invoke("set_greeting", host.val_word(Symbol("Hello")))
    assert val.error_code_of(info.value.val) == 1
    assert val.error_type_of(info.value.val) == val.ERROR_TYPE_CONTRACT
    # Rejected BEFORE the write: the stored greeting is still the constructor's.
    assert _answer(host, mini, "get_greeting") == Symbol("Hi")
    # And an imaginative one is accepted, so the check is a check and not a
    # method that always fails.
    assert _answer(host, mini, "set_greeting", host.val_word(Symbol("Yo"))) == Symbol("Yo")
    assert _answer(host, mini, "get_greeting") == Symbol("Yo")


# ===========================================================================
# every fixture, one property at a time
# ===========================================================================


#: The fixtures with an `__init__`, i.e. a `__constructor` export. The host
#: only honors that reserved name from protocol 22 (spec SS 13 / CAP-0058), so
#: the computed floor over these is 22 even though nothing they IMPORT is gated
#: -- see `test_every_fixture_instantiates_and_declares_the_protocol_floor`.
#:
#: This is a BY-NAME inventory, so an absence from it is a claim. **M1-E2's
#: `examples/shapes.py` has NO `__init__` and its absence here is deliberate**:
#: a unit variant is the natural empty value, so `get(SHAPE, Shape,
#: default=Shape.Empty)` covers the never-written key and there is nothing for
#: a constructor to initialize. It therefore declares protocol 20, not 22.
CONSTRUCTOR_BEARING: frozenset[Path] = frozenset(
    {
        TOKEN_STYLE,
        TOKEN_STYLE_CANONICAL,
        SANDBOX_HELLO_WORLD,
        EXAMPLE_ERRORS,
        EXAMPLE_ALLOWANCE_TOKEN,
    }
)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_every_fixture_instantiates_and_declares_the_protocol_floor(path: Path) -> None:
    """Every fixture links under the full mini host and declares the computed
    floor -- and the floor SPLITS, which is the property worth stating.

    None of these fixtures reaches a protocol-gated host function, so their
    IMPORT floor is `BASE_PROTOCOL` (20) across the board; a fixture that
    started declaring something higher for an import reason would mean a gated
    function crept into one of them. But the floor also counts FEATURE gates
    (2026-08-28 ruling): a `__constructor` export needs protocol 22 (spec SS 13
    / CAP-0058), so the five constructor-bearing fixtures declare 22 and the
    constructor-less six declare 20. Both halves are asserted, and which half
    a fixture is in is derived from its own IR rather than restated, so adding
    an `__init__` to a fixture cannot silently drop it out of the pin.
    """
    built, _host, _mini = start(path)
    assert built.target_protocol is None

    # `__constructor` in the ASSEMBLED exports is the derivation: it is the
    # export name the gate is about, read back off the bytes.
    has_constructor = "__constructor" in built.exports
    assert has_constructor == (path in CONSTRUCTOR_BEARING), (path.stem, has_constructor)
    assert built.declared_protocol == (22 if has_constructor else 20)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_every_fixtures_exports_are_exactly_its_non_internal_methods(path: Path) -> None:
    """The exports the module really has are the methods the frontend saw.

    `BuildResult.exports` is re-derived from the assembled BYTES (review B1's
    pass-2 net), never from the frontend's own list, so comparing it against
    `ContractIR.methods` checks the two halves against each other rather than
    against one shared source. The comparison is a SET, plus a separate
    statement about the one ordering fact that is a decision (B10: the
    constructor's entry comes first) -- pinning the whole tuple order would be
    pinning source order, which is not a promise the emitter makes.
    """
    built = build_fixture(path)
    compiled = compile_module(path.read_text(encoding="utf-8"), str(path))
    contract = compiled.ir.contract
    assert contract is not None
    exported = {
        method.export_name for method in contract.methods if method.kind is not FuncKind.INTERNAL
    }
    assert set(built.exports) == exported
    assert len(built.exports) == len(exported)  # no name exported twice
    if any(method.kind is FuncKind.CONSTRUCTOR for method in contract.methods):
        assert built.exports[0] == "__constructor"
    else:
        assert "__constructor" not in built.exports


# --- small readers, shared by the sequences above ----------------------------


def _vec_items(host: FullHost, handle: int) -> list[object]:
    """A `VecObject`'s elements, decoded, in order."""
    length = val.unpack_u32val(host.vec_len(handle))
    return [host.chain_value(host.vec_get(handle, val.pack_u32val(i))) for i in range(length)]


def _answer(host: FullHost, mini: engine.MiniHost, name: str, *args: int) -> object:
    """Invoke `name` and decode its returned `Val` through the store.

    Deliberately NOT routed through `decode_val`: that helper takes the `Ty`
    the compiler assigned an expression, which is the semantics differential's
    question. Here the declared return type is written in the fixture's own
    source and the tag is asserted where it matters (`hello`'s
    `TAG_VEC_OBJECT`), so a plain decode keeps these sequences readable.
    """
    word = mini.invoke(name, *args)
    assert word is not None, f"{name} returned nothing"
    return host.chain_value(word)


def _ty_of(path: Path, method: str) -> Ty:
    """The `Ty` one fixture method declares as its return type."""
    compiled = compile_module(path.read_text(encoding="utf-8"), str(path))
    (fn,) = [f for f in compiled.functions if f.export_name == method]
    return fn.ret


def test_build_wasm_and_build_file_agree_on_every_fixture() -> None:
    """`build_file` is `read_text` + `compile_module` + `build_wasm` and nothing
    else (its own docstring), so the two paths must produce identical bytes for
    all four fixtures. Cheap to state, and it is the seam every other test in
    this file runs through."""
    for path in FIXTURES:
        source = path.read_text(encoding="utf-8")
        direct = build_wasm(compile_module(source, str(path)))
        assert direct.wasm == build_fixture(path).wasm, path
