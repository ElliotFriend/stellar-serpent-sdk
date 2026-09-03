"""The `examples/` contracts, run BOTH ways and cross-checked (dossier D.2).

Every example carries the R1 acceptance triple, and all three legs are asserted
here or in `tests/unit/test_emitter_end_to_end.py`:

1. **it compiles** -- `compile_module` returns a `CompiledModule`, which IS the
   no-diagnostics claim (`CompiledModule` has no `diagnostics` field on purpose:
   "anything else raised `CompileError`");
2. **it runs at tier 1** -- `deploy(Cls, Env(...))` then `with env.frame():` and
   ordinary Python method calls, asserting the answers and the stored state;
3. **it runs as WASM under `FullHost`** -- the `start(path)` pattern, and the
   examples join `FIXTURES` in `test_emitter_end_to_end.py`, which is what buys
   them the build/validate/size/`needed <= linked`/protocol-floor properties.

**The cross-check is the point of legs 2 and 3 living in ONE test.** Two
absolute pins ("tier 1 answers 12", "the wasm answers 12") can both be edited to
agree with a drifting convention; comparing the two legs to each other cannot be
satisfied that way. So each example's headline test runs the SAME call sequence
on both sides and asserts the decoded chain values are EQUAL -- S13's
differential applied to a whole contract rather than to one expression -- and
only then pins the literal answers, so a failure says which half moved.

What a green run here does NOT mean (ruling E1, restated because these are the
files an author will copy): tier 1 is a hand-written model, `tests/harness` is a
mini host that mirrors it, and neither is the chain. The real host
(`tests/real_host/test_examples_real.py`) is the gate.

## Import mechanics

`examples/` is a FLAT directory of modules -- no `__init__.py`, so it is not a
package and nothing imports it as one. `load_example` reads each file through
`importlib.util.spec_from_file_location`, which is how a path-addressed module
is loaded without touching `sys.path` (the WASM leg needs no import at all --
`build_file` takes the path). `[tool.mypy] files` names `examples`, so every
example is under `mypy --strict` as a module, which is all mypy needs.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from serpent import val
from serpent.compiler.frontend import compile_module
from serpent.compiler.ir import FuncKind
from serpent.env import AuthorizationFailed, ConstructorFailed, Env, deploy
from serpent.errors import ContractError
from serpent.types import U32, Address, Bool, String, Symbol, Vec
from tests.harness import engine
from tests.unit.test_emitter_end_to_end import (
    ACCOUNT,
    CONTRACT,
    EXAMPLE_ALLOWANCE_TOKEN,
    EXAMPLE_COUNTER,
    EXAMPLE_ERRORS,
    EXAMPLE_EVENTS,
    EXAMPLE_SHAPES,
    EXAMPLE_STRUCTS,
    EXAMPLES,
    EXAMPLES_DIR,
    build_fixture,
    start,
)

# `_answer` (invoke, then decode the returned `Val` through the host's store) is
# imported rather than re-written: `tests/unit/test_emitter_end_to_end.py` is
# where the whole-contract WASM legs live, and a second copy of "invoke and
# decode" is the drift this repo keeps avoiding (the same reason
# `_wasm_custom_section` is imported across test modules rather than re-derived).
from tests.unit.test_emitter_end_to_end import _answer as answer

# The spec-section readers, imported for the same anti-drift reason `_answer`
# is: `tests/unit/test_sections.py` owns the tiny custom-section reader and the
# `SCSpecEntry` stream decoder, and a second copy of either would be a second
# thing to keep right.
from tests.unit.test_sections import _unpack, _wasm_custom_section

#: Two more real strkeys, beyond `ACCOUNT`/`CONTRACT`, for `allowance_token`'s
#: four distinct roles (admin, owner, spender, recipient) -- lifted from
#: `tests/unit/test_env_deploy.py` (`OWNER`) and `tests/unit/test_storage_key.py`
#: (`SPENDER`) rather than hand-written, so both are strkeys already proven to
#: decode correctly elsewhere in the suite.
OWNER = "GA7QYNF7SOWQ3GLR2BGMZEHXAVIRZA4KVWLTJJFC7MGXUA74P7UJVSGZ"
SPENDER = "GAAQEAYEAUDAOCAJBIFQYDIOB4IBCEQTCQKRMFYYDENBWHA5DYPSABOV"


def _allowance_token_roles() -> tuple[Address, Address, Address, Address]:
    """`allowance_token`'s four distinct roles: `(admin, owner, spender, to)`.

    One function so every test that deploys this example builds the same four
    `Address`es the same way, rather than the same four-tuple literal copied
    into each one.
    """
    return Address(ACCOUNT), Address(OWNER), Address(SPENDER), Address(CONTRACT)


def load_example(path: Path) -> ModuleType:
    """Import one example as a module, BY PATH.

    `examples/` is not a package (`test_examples_is_a_flat_directory_of_modules`
    keeps it that way), so there is no `import examples.counter` to make -- and
    a `sys.path` insert would import the file under a name that depends on the
    working directory. `spec_from_file_location` is the same mechanism the
    loader itself uses, and it gives the module a name that cannot collide with
    anything importable.
    """
    spec = importlib.util.spec_from_file_location(f"serpent_example_{path.stem}", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_examples_is_a_flat_directory_of_modules() -> None:
    """No `__init__.py`, and the directory listing IS the `EXAMPLES` tuple.

    The second half is the M8 lesson in test form: an example added to
    `examples/` without joining `EXAMPLES` -- and therefore `FIXTURES`, and
    therefore the whole-contract property sweep -- fails here instead of being
    silently uncovered.
    """
    assert not (EXAMPLES_DIR / "__init__.py").exists()
    assert sorted(path.name for path in EXAMPLES_DIR.glob("*.py")) == sorted(
        path.name for path in EXAMPLES
    )


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_every_example_compiles(path: Path) -> None:
    """Leg 1 of the triple. A returned `CompiledModule` is the no-diagnostics
    claim (its own docstring: "anything else raised `CompileError`"), and the
    module really declares a contract rather than compiling to nothing.

    The declared protocol is the COMPUTED floor over both kinds of gate: no
    example reaches a gated host function, so the import floor is 20 for the
    examples with no `__init__` -- but `errors.py` and `allowance_token.py`
    each have one, and `__constructor` is a capability the host only honors
    from protocol 22 (spec SS 13 / CAP-0058), so those two declare 22. The
    split is derived from the module's own IR rather than listed by name, so
    adding an `__init__` to an example cannot silently invalidate the pin.
    """
    compiled = compile_module(path.read_text(encoding="utf-8"), str(path))
    contract = compiled.ir.contract
    assert contract is not None
    has_constructor = any(m.kind is FuncKind.CONSTRUCTOR for m in contract.methods)
    assert has_constructor == (path.stem in {"errors", "allowance_token"}), (
        path.stem,
        has_constructor,
    )
    assert compiled.declared_protocol == (22 if has_constructor else 20)


# ===========================================================================
# counter: the graduated sandbox contract
# ===========================================================================


def test_the_counter_example_answers_the_same_at_tier_1_and_as_wasm() -> None:
    """One `increment`/`total` sequence, run twice: once through the tier-1
    model, once as WASM under `FullHost`, then compared to each other.

    `total()` is called before anything is stored, so the `default=U32(0)` arm
    is in the sequence too -- on the WASM side that is the `has_contract_data`
    then `get`-or-default pair, and on the tier-1 side the model's own default
    handling, which is exactly the kind of divergence a cross-check catches.
    """
    module = load_example(EXAMPLE_COUNTER)
    env = Env()
    counter = deploy(module.Counter, env)
    with env.frame():
        tier_1 = [
            counter.total(env),
            counter.increment(env, U32(5)),
            counter.increment(env, U32(7)),
            counter.total(env),
        ]

    _built, host, mini = start(EXAMPLE_COUNTER)
    from_wasm = [
        answer(host, mini, "total"),
        answer(host, mini, "increment", val.pack_u32val(5)),
        answer(host, mini, "increment", val.pack_u32val(7)),
        answer(host, mini, "total"),
    ]

    assert from_wasm == tier_1
    assert tier_1 == [U32(0), U32(5), U32(12), U32(12)]


def test_the_counter_example_refuses_the_ceiling_the_same_way_on_both_legs() -> None:
    """`raise Error.MaxReached` -- `errorcode(1)` -- and the total is UNCHANGED
    on both legs, so the ceiling is checked before the write in the model and in
    the compiled contract alike.

    The two legs cannot assert the same OBJECT: tier 1 raises the author's own
    `Error.MaxReached` class, while the compiled contract calls
    `fail_with_error` and the host reports a Contract-type error `Val` carrying
    the code. So what is compared is the code (1) and the state afterwards (12),
    which is all a client on chain can see either.
    """
    module = load_example(EXAMPLE_COUNTER)
    env = Env()
    counter = deploy(module.Counter, env)
    with env.frame():
        counter.increment(env, U32(12))
        with pytest.raises(module.Error.MaxReached) as tier_1_error:
            counter.increment(env, U32(1000))
        assert tier_1_error.value.code == 1
        assert counter.total(env) == U32(12)

    _built, host, mini = start(EXAMPLE_COUNTER)
    answer(host, mini, "increment", val.pack_u32val(12))
    with pytest.raises(engine.HostError) as wasm_error:
        mini.invoke("increment", val.pack_u32val(1000))
    assert val.error_code_of(wasm_error.value.val) == 1
    assert val.error_type_of(wasm_error.value.val) == val.ERROR_TYPE_CONTRACT
    assert answer(host, mini, "total") == U32(12)


# ===========================================================================
# errors: the error-enum example, and S12's laundering
# ===========================================================================


def test_the_errors_example_answers_the_same_at_tier_1_and_as_wasm() -> None:
    """The happy path plus every in-band failure mode, both legs, compared.

    Each refusal is recorded as its CODE -- the only half of the author's error
    that crosses the boundary -- and the balance is read once more at the end of
    the sequence, after all three refusals, because "the call failed" and "the
    call failed WITHOUT writing" are different contracts and only the second one
    is true here. That last read is the fourth element of both lists, so the
    cross-check covers the state as well as the answers.
    """
    module = load_example(EXAMPLE_ERRORS)
    env = Env()
    vault = deploy(module.Vault, env, Address(ACCOUNT), U32(100))
    with env.frame():
        tier_1 = [
            vault.deposit(env, U32(40)),
            vault.withdraw(env, U32(10)),
            vault.balance(env),
        ]
        tier_1_codes = [
            _tier_1_code(vault.deposit, env, U32(1000)),
            _tier_1_code(vault.withdraw, env, U32(1000)),
            _tier_1_code(vault.set_limit, env, Address(CONTRACT), U32(5)),
        ]
        tier_1.append(vault.balance(env))

    _built, host, mini = start(EXAMPLE_ERRORS)
    owner = host.val_word(Address(ACCOUNT))
    assert mini.invoke("__constructor", owner, val.pack_u32val(100)) == val.VOID_VAL
    from_wasm = [
        answer(host, mini, "deposit", val.pack_u32val(40)),
        answer(host, mini, "withdraw", val.pack_u32val(10)),
        answer(host, mini, "balance"),
    ]
    from_wasm_codes = [
        _wasm_code(mini, "deposit", val.pack_u32val(1000)),
        _wasm_code(mini, "withdraw", val.pack_u32val(1000)),
        _wasm_code(mini, "set_limit", host.val_word(Address(CONTRACT)), val.pack_u32val(5)),
    ]
    from_wasm.append(answer(host, mini, "balance"))

    assert from_wasm == tier_1
    assert from_wasm_codes == tier_1_codes
    assert tier_1 == [U32(40), U32(30), U32(30), U32(30)]
    # LimitExceeded, InsufficientBalance, Unauthorized -- the codes the example
    # declares, reached one method at a time.
    assert tier_1_codes == [3, 4, 2]


def test_the_errors_example_demonstrates_S12_constructor_laundering() -> None:
    """The caveat the example's docstring quotes, asserted on both legs.

    At tier 1 `deploy` raises `ConstructorFailed` -- NOT `LimitTooSmall` -- and
    chains the author's error as `__cause__`, which is the model of the host's
    laundering: the identity is hidden from the deployer, not thrown away. Under
    the mini host the guest's `fail_with_error` still carries code 1, because the
    mini host is the CONTRACT's view of the call and the laundering happens
    above it, in the deploy operation the mini host does not model.

    That gap is exactly why S12 says the docs must warn: on chain the deployer
    sees `Context(InvalidAction)` and never the 1. The test asserts the two
    things that are true here, and the example's docstring is where the on-chain
    consequence is stated to the author.
    """
    module = load_example(EXAMPLE_ERRORS)
    env = Env()
    with pytest.raises(ConstructorFailed) as info:
        deploy(module.Vault, env, Address(ACCOUNT), U32(0))
    assert isinstance(info.value.__cause__, module.VaultError.LimitTooSmall)
    assert info.value.__cause__.code == 1
    # The failed deploy deployed nothing, so there is no instance to call.
    with pytest.raises(RuntimeError):
        env.frame().__enter__()

    _built, host, mini = start(EXAMPLE_ERRORS)
    assert (
        _wasm_code(mini, "__constructor", host.val_word(Address(ACCOUNT)), val.pack_u32val(0)) == 1
    )


# ===========================================================================
# structs: a struct storage key, and a field name linear memory has to carry
# ===========================================================================


def test_the_structs_example_answers_the_same_at_tier_1_and_as_wasm() -> None:
    """A struct VALUE stored under a struct KEY, read back field by field.

    The two legs share the ledger sequence (`DEFAULT_LEDGER_SEQUENCE`, which
    `FullHost` takes from `serpent.env`), so `joined_ledger` is a real
    cross-check rather than two unrelated numbers -- and `display_name_of`
    proves the stored struct survived the round trip through a struct-keyed
    entry, which a store keyed on the object HANDLE would silently fail.
    """
    module = load_example(EXAMPLE_STRUCTS)
    env = Env()
    registry = deploy(module.Registry, env)
    with env.frame():
        registry.join(env, Address(ACCOUNT), String("Ana Registrar"))
        tier_1 = [
            registry.display_name_of(env, Address(ACCOUNT)),
            registry.joined_ledger_of(env, Address(ACCOUNT)),
        ]
        tier_1_codes = [
            _tier_1_code(registry.join, env, Address(ACCOUNT), String("Ana Again")),
            _tier_1_code(registry.display_name_of, env, Address(CONTRACT)),
        ]

    _built, host, mini = start(EXAMPLE_STRUCTS)
    member = host.val_word(Address(ACCOUNT))
    assert mini.invoke("join", member, host.val_word(String("Ana Registrar"))) == val.VOID_VAL
    from_wasm = [
        answer(host, mini, "display_name_of", member),
        answer(host, mini, "joined_ledger_of", member),
    ]
    from_wasm_codes = [
        _wasm_code(mini, "join", member, host.val_word(String("Ana Again"))),
        _wasm_code(mini, "display_name_of", host.val_word(Address(CONTRACT))),
    ]

    assert from_wasm == tier_1
    assert from_wasm_codes == tier_1_codes
    assert tier_1 == [String("Ana Registrar"), U32(1_000_000)]
    assert tier_1_codes == [1, 2]  # AlreadyJoined, NotAMember


def test_the_structs_examples_long_field_name_goes_through_linear_memory() -> None:
    """The consequence the example exists to show: `display_name` is 12
    characters, and a `SymbolSmall` holds 9 -- so reading that field has to
    materialize a `SymbolObject` through `symbol_new_from_linear_memory`.

    Asserted through the host's own call log as well as the import set, so a
    lowering that started inlining an over-long symbol would be visible here
    rather than only in a golden diff. `map_new_from_linear_memory` is in the
    same sequence because the struct itself is laid out from the literal pool.
    """
    built, host, mini = start(EXAMPLE_STRUCTS)
    assert "symbol_new_from_linear_memory" in built.imports
    assert "map_new_from_linear_memory" in built.imports

    member = host.val_word(Address(ACCOUNT))
    mini.invoke("join", member, host.val_word(String("Ana Registrar")))
    assert host.count("map_new_from_linear_memory") > 0
    answer(host, mini, "display_name_of", member)
    assert host.count("symbol_new_from_linear_memory") > 0
    assert host.count("map_get") > 0


# ===========================================================================
# events: both publish spellings, a topics-marked event and an all-data one
# ===========================================================================


def test_the_events_example_answers_the_same_at_tier_1_and_as_wasm() -> None:
    """`record_score` (the AUTHORING form, topics-marked) then
    `record_round_closed` (the CANONICAL form, all-data `"vec"`), both
    published events compared between tier 1 and WASM as decoded chain values.

    `record_round_closed`'s data is a `VecObject` on the WASM side, so it is
    decoded through `host.chain_value_as(word, Vec[U32])` rather than through
    the single `host.chain_value` call that suffices for `record_score`'s bare
    `U32`.

    Its topic is `round_closed` -- 12 characters, so a `SymbolObject` pooled
    through linear memory rather than a `SymbolSmall` word. Ruling E11 is what
    makes that spellable by hand: `SPT3019` used to refuse a hand-written
    `topics[0]` past nine characters, and the example had to carry a
    deliberately short name to be reachable through both spellings.

    **Discharged: `chain_value_as`.** This test used to reach past the
    harness's own surface into the private `host._vec` to read a published
    vec -- a coupling a rename inside the harness could have broken silently.
    `ObjectStore.chain_value_as` (tier-2a productization, ruling E7) is the
    public container decoder that replaces it.
    """
    module = load_example(EXAMPLE_EVENTS)
    env = Env()
    scoreboard = deploy(module.Scoreboard, env)
    with env.frame():
        scoreboard.record_score(env, Address(ACCOUNT), U32(7))
        scoreboard.record_round_closed(env, U32(3), U32(1))
    (score_topics, score_data), (round_topics, round_data) = env.published_events

    _built, host, mini = start(EXAMPLE_EVENTS)
    player = host.val_word(Address(ACCOUNT))
    assert mini.invoke("record_score", player, val.pack_u32val(7)) == val.VOID_VAL
    assert (
        mini.invoke("record_round_closed", val.pack_u32val(3), val.pack_u32val(1)) == val.VOID_VAL
    )
    (wasm_score_topics, wasm_score_data), (wasm_round_topics, wasm_round_data) = host.events
    # `round_data` is a `ChainValue` union; narrow it to `Vec` before iterating
    # (a struct or a scalar has no `__iter__` mypy --strict can see).
    assert isinstance(round_data, Vec)

    assert [host.chain_value(t) for t in wasm_score_topics] == list(score_topics)
    assert host.chain_value(wasm_score_data) == score_data
    assert [host.chain_value(t) for t in wasm_round_topics] == list(round_topics)
    assert host.chain_value_as(wasm_round_data, Vec[U32]) == round_data

    assert score_topics == (Symbol("scored"), Address(ACCOUNT))
    assert score_data == U32(7)
    # Both legs pinned to the literal 12-character name, not just to each
    # other: a rename that drifted in ONE leg would already have failed the
    # comparisons above, and a rename that drifted in BOTH fails here.
    assert round_topics == (Symbol("round_closed"),)
    assert [host.chain_value(t) for t in wasm_round_topics] == [Symbol("round_closed")]
    assert list(round_data) == [U32(3), U32(1)]


def test_the_events_examples_canonical_spelling_matches_the_authoring_forms_desugar() -> None:
    """The equivalence claim, checked on THIS file's own all-data event.

    `record_round_closed` hand-writes `env.events().publish((Symbol(
    "round_closed"),), Vec(U32, [wins, losses]))`; `RoundClosed(wins=...,
    losses=...).publish(env)` is the authoring form the module docstring says
    produces the identical record. Both are published into the SAME frame
    here, and the two `PublishedEvent` snapshots compare equal -- topics word
    for word (chain values, via `ChainValue.__eq__`) and the `Vec` data the
    same way.

    The WASM leg is then compared to the AUTHORING form's snapshot, which the
    cross-check above never does (it compares WASM to the CANONICAL form).
    That closes the triangle on the 12-character prefix topic: the declared
    name, the hand-written name, and the `SymbolObject` the compiled contract
    pools through linear memory are one and the same `round_closed`.
    """
    module = load_example(EXAMPLE_EVENTS)
    env = Env()
    scoreboard = deploy(module.Scoreboard, env)
    with env.frame():
        scoreboard.record_round_closed(env, U32(3), U32(1))
        module.RoundClosed(wins=U32(3), losses=U32(1)).publish(env)
    canonical, authored = env.published_events
    assert canonical == authored
    authored_topics, authored_data = authored
    assert authored_topics == (Symbol("round_closed"),)
    assert isinstance(authored_data, Vec)

    _built, host, mini = start(EXAMPLE_EVENTS)
    assert (
        mini.invoke("record_round_closed", val.pack_u32val(3), val.pack_u32val(1)) == val.VOID_VAL
    )
    ((wasm_topics, wasm_data),) = host.events
    assert [host.chain_value(t) for t in wasm_topics] == list(authored_topics)
    assert host.chain_value_as(wasm_data, Vec[U32]) == authored_data


# ===========================================================================
# allowance_token: the S6 allowance-style token, without cross-contract calls
# ===========================================================================


def test_the_allowance_token_example_answers_the_same_at_tier_1_and_as_wasm() -> None:
    """The whole surface -- `mint`, `approve`, `transfer_from`, its events, and
    both of its refusals -- run identically on both legs, with no
    `env.advance(...)` anywhere: the mini host has no TTL model at all
    (`extend_contract_data_ttl` is a recorded no-op), so this is the
    WITHOUT-expiry half of the cross-check the module docstring names. The
    expiry half is tier-1 only (the dedicated test below); the underlying host
    fact -- a lapsed temporary entry reads absent -- is proven on the real
    host in HOST_FACTS row `a_lapsed_temporary_entry_reads_absent`
    (`tests/real_host/test_host_facts_real.py`).

    The two refusal codes are reached in an order that isolates each guard:
    `transfer_from(spender, owner, to, 100)` exceeds the balance (75 left)
    but not the allowance (175 left), so it is `InsufficientBalance`;
    `transfer_from(to, owner, spender, 1)` uses an address with NO allowance
    at all (`to` was never approved), so it is `InsufficientAllowance`. A
    final read of both the balance and the allowance proves neither refusal
    wrote anything.

    Two things `errors.py`/`structs.py`'s cross-checks did not need to cover,
    which this contract's enumerated requirements ("events on approve/
    transfer") do: the published `Approval` and `Transfer` events, decoded and
    compared the same way `events.py`'s cross-check compares `Scored` and
    `RoundClosed` (`test_the_events_example_answers_the_same_at_tier_1_and_as_wasm`);
    and that `approve` and the successful `transfer_from` each really reach
    `extend_contract_data_ttl` once, via the host's own call count -- proving
    the re-extend-on-access call in `transfer_from` is not dead code.
    """
    module = load_example(EXAMPLE_ALLOWANCE_TOKEN)
    env = Env()
    admin, owner, spender, to = _allowance_token_roles()
    token = deploy(module.AllowanceToken, env, admin)
    with env.frame():
        token.mint(env, owner, U32(100))
        token.approve(env, owner, spender, U32(200))
        token.transfer_from(env, spender, owner, to, U32(25))
        tier_1_events = env.published_events
        tier_1 = [
            token.balance(env, owner),
            token.balance(env, to),
            token.allowance(env, owner, spender),
        ]
        tier_1_codes = [
            _tier_1_code(token.transfer_from, env, spender, owner, to, U32(100)),
            _tier_1_code(token.transfer_from, env, to, owner, spender, U32(1)),
        ]
        tier_1.append(token.balance(env, owner))
        tier_1.append(token.allowance(env, owner, spender))

    _built, host, mini = start(EXAMPLE_ALLOWANCE_TOKEN)
    admin_w = host.val_word(admin)
    owner_w = host.val_word(owner)
    spender_w = host.val_word(spender)
    to_w = host.val_word(to)
    assert mini.invoke("__constructor", admin_w) == val.VOID_VAL
    assert mini.invoke("mint", owner_w, val.pack_u32val(100)) == val.VOID_VAL
    assert mini.invoke("approve", owner_w, spender_w, val.pack_u32val(200)) == val.VOID_VAL
    assert host.count("extend_contract_data_ttl") == 1  # approve's own call
    assert (
        mini.invoke("transfer_from", spender_w, owner_w, to_w, val.pack_u32val(25)) == val.VOID_VAL
    )
    # transfer_from's re-extend-on-access call, reached on top of approve's.
    assert host.count("extend_contract_data_ttl") == 2
    wasm_events = list(host.events)
    from_wasm = [
        answer(host, mini, "balance", owner_w),
        answer(host, mini, "balance", to_w),
        answer(host, mini, "allowance", owner_w, spender_w),
    ]
    from_wasm_codes = [
        _wasm_code(mini, "transfer_from", spender_w, owner_w, to_w, val.pack_u32val(100)),
        _wasm_code(mini, "transfer_from", to_w, owner_w, spender_w, val.pack_u32val(1)),
    ]
    from_wasm.append(answer(host, mini, "balance", owner_w))
    from_wasm.append(answer(host, mini, "allowance", owner_w, spender_w))
    # The two refusals raised before reaching the host's extend_ttl call at
    # all (both fail on the allowance/balance CHECK, before either write), so
    # the count from above is unchanged by them.
    assert host.count("extend_contract_data_ttl") == 2

    assert from_wasm == tier_1
    assert from_wasm_codes == tier_1_codes
    assert tier_1 == [U32(75), U32(25), U32(175), U32(75), U32(175)]
    assert tier_1_codes == [2, 1]  # InsufficientBalance, InsufficientAllowance

    # `Approval` (from `approve`) then `Transfer` (from the successful
    # `transfer_from`), decoded and compared the way `events.py`'s
    # cross-check compares its own two events.
    (approval_topics, approval_data), (transfer_topics, transfer_data) = tier_1_events
    (wasm_approval_topics, wasm_approval_data), (wasm_transfer_topics, wasm_transfer_data) = (
        wasm_events
    )
    assert [host.chain_value(t) for t in wasm_approval_topics] == list(approval_topics)
    assert host.chain_value(wasm_approval_data) == approval_data
    assert [host.chain_value(t) for t in wasm_transfer_topics] == list(transfer_topics)
    assert host.chain_value(wasm_transfer_data) == transfer_data

    assert approval_topics == (Symbol("approve"), owner, spender)
    assert approval_data == U32(200)
    assert transfer_topics == (Symbol("transfer"), owner, to)
    assert transfer_data == U32(25)


def test_the_allowance_token_example_records_auth_for_admin_owner_and_spender() -> None:
    """`require_auth` is called on a DIFFERENT address per method -- the STORED
    admin in `mint` (read back out of instance storage, not a parameter), the
    owner in `approve`, and the SPENDER (not the owner again) in
    `transfer_from` -- which is the full authorized shape
    `examples/errors.py`'s `set_limit` docstring points at and deliberately
    does not show. `recorded_auths` is the whole tier-1 auth model
    (mock-all-auths, S4): each call is recorded, in order, and nothing else is.

    The recorded admin is EQUAL to the deployed one and not the same object
    (`get` hands back a deep copy, ruling E5), which is exactly the claim: the
    address authorized came out of the store.
    """
    module = load_example(EXAMPLE_ALLOWANCE_TOKEN)
    env = Env()
    admin, owner, spender, to = _allowance_token_roles()
    token = deploy(module.AllowanceToken, env, admin)
    with env.frame():
        token.mint(env, owner, U32(100))
        token.approve(env, owner, spender, U32(40))
        token.transfer_from(env, spender, owner, to, U32(10))
    assert env.recorded_auths == ((admin, None), (owner, None), (spender, None))
    assert env.recorded_auths[0][0] is not admin


def test_the_allowance_tokens_mint_enforces_the_stored_admin() -> None:
    """`mint` has no `admin` parameter: it reads `ADMIN` back and authorizes
    THAT address, so the constructor's write is what decides who may mint.

    Pinned through an allow-set (S4) rather than through `recorded_auths`,
    because the allow-set is the only tier-1 surface that can REFUSE: the same
    `mint(env, owner, U32(1))` call succeeds on an env whose allow-set holds the
    stored admin and fails on one that holds everybody else. The older shape --
    `mint(env, admin, to, amount)` authorizing its own argument -- could not
    fail this way at all: a caller simply named an address it could authorize.

    Tier-1 only, and for a reason the mini host owns: its `require_auth` records
    and always succeeds (there is no authorization state to consult), so an
    allow-set refusal has no WASM leg here. `tests/semantics/env_scenarios.py`'s
    `ALLOW_SET_REASON` is the same limitation, stated for the table.
    """
    module = load_example(EXAMPLE_ALLOWANCE_TOKEN)
    admin, owner, spender, _to = _allowance_token_roles()

    allowed = Env(auths=(admin,))
    token = deploy(module.AllowanceToken, allowed, admin)
    with allowed.frame():
        token.mint(allowed, owner, U32(1))
        assert token.balance(allowed, owner) == U32(1)

    refused = Env(auths=(owner, spender))
    other = deploy(module.AllowanceToken, refused, admin)
    with refused.frame(), pytest.raises(AuthorizationFailed):
        other.mint(refused, owner, U32(1))
    with refused.frame():
        # The refusal is not a partial write: nothing was credited.
        assert other.balance(refused, owner) == U32(0)


def test_the_allowance_tokens_approve_always_reextends_from_the_current_sequence() -> None:
    """The write-resets-TTL property, demonstrated with NO threshold-value
    claim: `approve` always writes the amount immediately before calling
    `extend_ttl`, so that call always sees a never-extended (`None`) entry and
    always takes the FULL `ALLOWANCE_TTL_EXTEND_TO` from the CURRENT sequence
    (`env.py`'s `_extended_live_until`: "the first extension always
    applies") -- never from whatever live-until an earlier `approve` already
    granted, and never actually filtered through `ALLOWANCE_TTL_THRESHOLD`.

    The SECOND `approve` below lands one ledger after the first, where
    `ALLOWANCE_TTL_EXTEND_TO - 1` ledgers of the first grant's lifetime
    remain -- well OVER `ALLOWANCE_TTL_THRESHOLD` (asserted below), which on
    an entry that was NOT just reset by a write would leave a plain
    `extend_ttl` call a no-op (the guard's real direction: `live_until -
    sequence >= threshold` blocks). `approve` extends anyway, to one ledger
    LATER than the first grant -- observable only because its own write
    erased the memory a guard check would have needed.
    """
    module = load_example(EXAMPLE_ALLOWANCE_TOKEN)
    extend_to = module.ALLOWANCE_TTL_EXTEND_TO.value
    threshold = module.ALLOWANCE_TTL_THRESHOLD.value
    assert extend_to - 1 >= threshold, "the second approve must land with plenty of life left"
    env = Env()
    admin, owner, spender, _to = _allowance_token_roles()
    token = deploy(module.AllowanceToken, env, admin)
    with env.frame():
        token.mint(env, owner, U32(100))
        token.approve(env, owner, spender, U32(10))  # live-until = sequence + extend_to
    env.advance(1)
    with env.frame():
        # `extend_to - 1` ledgers remain here -- over the threshold, which
        # would block a guard-respecting extend_ttl on an UNWRITTEN entry.
        token.approve(env, owner, spender, U32(20))
        assert token.allowance(env, owner, spender) == U32(20)
    env.advance(extend_to)
    with env.frame():
        # Exactly at the SECOND call's live-until (one ledger past the
        # FIRST call's): still alive. If the second `approve` had genuinely
        # been blocked by the guard, the FIRST call's live-until would still
        # govern, and it would already be one ledger dead here.
        assert token.allowance(env, owner, spender) == U32(20)
    env.advance(1)
    with env.frame():
        assert token.allowance(env, owner, spender) == U32(0)


def test_the_allowance_expires_and_transfer_from_then_fails_with_the_authors_error() -> None:
    """E4's TTL model, the showcase spec S6's example forces (dossier §B.5.3).

    `approve` grants an allowance and extends its live-until by exactly the
    contract's own `ALLOWANCE_TTL_EXTEND_TO` ledgers past the current
    sequence (a never-extended entry always takes the full extension).
    Advancing the sequence to EXACTLY the live-until still finds the entry
    alive (S8's "strictly past" rule, `env.py`'s `_absent`); one more ledger
    and it reads absent, and `allowance(...)` answers `U32(0)` through its own
    `default=` -- an expired approval and a never-made one are the same
    contract state. `transfer_from` then refuses with THIS CONTRACT'S OWN
    `InsufficientAllowance` (code 1), not a generic missing-value trap, which
    is the point of reading the allowance through `default=U32(0)` rather than
    a bare `get`.

    **Not runnable on the WASM leg at all.** `tests/harness`'s mini host has no
    TTL model (`extend_contract_data_ttl` is a recorded no-op, `env.py`'s TTL
    section), so there is nothing to cross-check this scenario against here --
    the underlying host fact -- a lapsed temporary entry reads absent -- is
    proven on the real host in HOST_FACTS row
    `a_lapsed_temporary_entry_reads_absent`
    (`tests/real_host/test_host_facts_real.py`).
    """
    module = load_example(EXAMPLE_ALLOWANCE_TOKEN)
    extend_to = module.ALLOWANCE_TTL_EXTEND_TO.value
    env = Env()
    admin, owner, spender, to = _allowance_token_roles()
    token = deploy(module.AllowanceToken, env, admin)
    with env.frame():
        token.mint(env, owner, U32(50))
        token.approve(env, owner, spender, U32(50))
        assert token.allowance(env, owner, spender) == U32(50)

    env.advance(extend_to)
    with env.frame():
        # Exactly at the live-until ledger: still alive (expiry is strict).
        assert token.allowance(env, owner, spender) == U32(50)

    env.advance(1)
    with env.frame():
        # One ledger past: the entry reads absent, i.e. U32(0) via `default=`.
        assert token.allowance(env, owner, spender) == U32(0)
        code = _tier_1_code(token.transfer_from, env, spender, owner, to, U32(1))
        assert code == 1  # AllowanceError.InsufficientAllowance
        # The refused call wrote nothing: the balance mint left is untouched.
        assert token.balance(env, owner) == U32(50)


# ===========================================================================
# shapes: the tagged union and the int enum (M1-E2)
# ===========================================================================


def test_the_shapes_example_answers_the_same_at_tier_1_and_as_wasm() -> None:
    """The whole-contract differential (S13) for the two new value kinds:
    construct each variant shape, store it, read it back, branch on `tag()`,
    read `payload()`, and compare the decoded answers between the legs.

    The sequence walks all three arities in order -- the unit `Empty` that a
    never-written key defaults to, then `Circle` (one payload), then `Rect`
    (two) -- and reads `kind()`/`area()` after each, so both union read
    patterns run against every case rather than only against the one the
    contract happens to end on. `palette()` is the int-enum half: an
    `if`/`elif` chain over `Color` equality, which is the operation an int
    enum exists for and the one this file's cross-check would have caught
    going missing.

    `pin()`/`is_pinned()` are in the sequence because a union is a storage
    KEY there, not a value: the `Shape.Circle(U32(3))` that `pin` writes and
    the one `is_pinned` looks up are two separately built objects, so an
    entry found by object identity rather than by VALUE would answer `False`
    on the tier-1 leg and diverge from the host's own key hashing.

    Every entry point answers a `Symbol`, a `U32` or a `Bool` on purpose --
    see the example's docstring. The mini host has no spec decoder, so a
    method returning a `Shape` would decode to an opaque vec rank on this
    leg and to the author's own `Shape` on the other, and the two lists could
    not be compared at all. That is a harness limitation carried to sub-plan
    F, not a language one -- the spec-entry test below is what proves both
    types really reach the contract's interface.
    """
    module = load_example(EXAMPLE_SHAPES)
    env = Env()
    drawing = deploy(module.Drawing, env)
    with env.frame():
        tier_1 = [drawing.kind(env), drawing.area(env), drawing.palette(env)]
        drawing.draw_circle(env, U32(3))
        tier_1 += [drawing.kind(env), drawing.area(env), drawing.is_pinned(env)]
        drawing.pin(env)
        tier_1 += [drawing.is_pinned(env)]
        drawing.draw_rect(env, U32(4), U32(5))
        tier_1 += [drawing.kind(env), drawing.area(env), drawing.is_pinned(env)]
        drawing.paint(env, module.Color.Blue)
        tier_1 += [drawing.palette(env)]
        drawing.clear(env)
        tier_1 += [drawing.kind(env), drawing.area(env)]

    _built, host, mini = start(EXAMPLE_SHAPES)
    from_wasm = [
        answer(host, mini, "kind"),
        answer(host, mini, "area"),
        answer(host, mini, "palette"),
    ]
    assert mini.invoke("draw_circle", val.pack_u32val(3)) == val.VOID_VAL
    from_wasm += [
        answer(host, mini, "kind"),
        answer(host, mini, "area"),
        answer(host, mini, "is_pinned"),
    ]
    assert mini.invoke("pin") == val.VOID_VAL
    from_wasm += [answer(host, mini, "is_pinned")]
    assert mini.invoke("draw_rect", val.pack_u32val(4), val.pack_u32val(5)) == val.VOID_VAL
    from_wasm += [
        answer(host, mini, "kind"),
        answer(host, mini, "area"),
        answer(host, mini, "is_pinned"),
    ]
    # `Color.Blue` crosses the ABI as the bare `u32` 2 -- the int enum's whole
    # on-chain representation (M1-E2 SS B.1), which is why the WASM leg can
    # spell it with `pack_u32val` and no encoder.
    assert mini.invoke("paint", val.pack_u32val(2)) == val.VOID_VAL
    from_wasm += [answer(host, mini, "palette")]
    assert mini.invoke("clear") == val.VOID_VAL
    from_wasm += [answer(host, mini, "kind"), answer(host, mini, "area")]

    assert from_wasm == tier_1
    assert tier_1 == [
        # nothing drawn yet: `default=Shape.Empty`, and the default palette
        Symbol("Empty"),
        U32(0),
        Symbol("red"),
        # a Circle, unpinned then pinned
        Symbol("Circle"),
        U32(27),
        Bool(False),
        Bool(True),
        # a Rect -- a DIFFERENT union value, so the pinned Circle's key misses
        Symbol("Rect"),
        U32(20),
        Bool(False),
        # painted blue, read back through the enum `==` chain
        Symbol("blue"),
        # cleared: the unit variant again
        Symbol("Empty"),
        U32(0),
    ]


def test_the_shapes_examples_union_survives_all_three_durabilities_at_tier_1() -> None:
    """Durability is a property of the ENTRY, not of the value: the same
    `Shape.Rect(U32(4), U32(5))` written into instance, persistent and
    temporary storage reads back EQUAL out of each one.

    Driven against the model's store directly rather than through the
    example's methods, because the example picks one bucket per key the way a
    real contract does -- instance for the shape being drawn, persistent for
    the color, temporary for the pinned set -- and three near-identical
    methods that exist only to write the same union three ways would be
    test-shaped code in teaching material. What makes this a statement about
    `Shape` and not about `Vec` is that it is the EXAMPLE's own union type,
    round-tripped through `get(key, Shape)`, which is the tag-checked read an
    author writes.

    Tier-1 only: the mini host has no spec decoder, so there is no WASM leg to
    compare a returned union against; the real host leg does decode it
    (`tests/real_host/test_examples_real.py::test_a_union_and_an_int_enum_return_decode_through_their_types`).
    """
    module = load_example(EXAMPLE_SHAPES)
    env = Env()
    deploy(module.Drawing, env)
    rect = module.Shape.Rect(U32(4), U32(5))
    key = Symbol("K")
    with env.frame():
        buckets = (
            env.storage().instance(),
            env.storage().persistent(),
            env.storage().temporary(),
        )
        for bucket in buckets:
            bucket.set(key, rect)
        read_back = [bucket.get(key, module.Shape) for bucket in buckets]

    assert read_back == [rect, rect, rect]
    # Read back as the union it was stored as, not as a bare vec: the tag and
    # both payload slots survive, which is what "round trip" has to mean.
    for shape in read_back:
        assert shape.tag() == Symbol("Rect")
        assert shape.payload(U32(0), U32) == U32(4)
        assert shape.payload(U32(1), U32) == U32(5)


def test_the_shapes_example_declares_both_new_kinds_in_its_spec() -> None:
    """The Task 3 property, now on SHIPPED source: the built module's
    `contractspecv0` carries a `UDT_UNION_V0` entry for `Shape` and a
    `UDT_ENUM_V0` entry for `Color`.

    This is what makes the harness limitation the headline test discloses a
    harness limitation and not a hole in the contract's interface: a client
    fetching this module's spec off the ledger gets both types, every variant
    with its arity, and every discriminant -- whatever the mini host is able
    to decode a returned `Val` into.

    Read out of the ASSEMBLED wasm rather than from `build_spec_entries`
    directly, the same way `test_env_differential.py` reads its event entries
    back, because what a client sees is the section and not the builder call.
    """
    built = build_fixture(EXAMPLE_SHAPES)
    entries = _unpack(_wasm_custom_section(built.wasm, "contractspecv0"))

    (union,) = [e.udt_union_v0 for e in entries if e.udt_union_v0 is not None]
    assert union.name == b"Shape"
    assert [case.void_case.name for case in union.cases if case.void_case is not None] == [b"Empty"]
    assert [case.tuple_case.name for case in union.cases if case.tuple_case is not None] == [
        b"Circle",
        b"Rect",
    ]
    # The arities the example's two tuple variants declare, in declaration order.
    arities = [len(case.tuple_case.type) for case in union.cases if case.tuple_case is not None]
    assert arities == [1, 2]

    (int_enum,) = [e.udt_enum_v0 for e in entries if e.udt_enum_v0 is not None]
    assert int_enum.name == b"Color"
    assert [(case.name, case.value.uint32) for case in int_enum.cases] == [
        (b"Red", 0),
        (b"Green", 1),
        (b"Blue", 2),
    ]


# --- small readers -----------------------------------------------------------


def _tier_1_code(method: Callable[..., object], *args: object) -> int:
    """Call a tier-1 method that must raise a `ContractError`, and answer its code.

    A code rather than the class, because the code is the only half of the
    author's error that the WASM leg -- or a client on chain -- can report. The
    expected type is `ContractError` and not a bare `Exception`, so a method that
    failed for some OTHER reason (one of the model's own refusals, a `TypeError`
    from a bad call in this test) propagates instead of being recorded as "it
    raised, close enough".
    """
    with pytest.raises(ContractError) as info:
        method(*args)
    code = info.value.code
    assert isinstance(code, int), code
    return code


def _wasm_code(mini: engine.MiniHost, name: str, *args: int) -> int:
    """Invoke `name` expecting a Contract-type error `Val`, and answer its code."""
    with pytest.raises(engine.HostError) as info:
        mini.invoke(name, *args)
    assert val.error_type_of(info.value.val) == val.ERROR_TYPE_CONTRACT
    return val.error_code_of(info.value.val)
