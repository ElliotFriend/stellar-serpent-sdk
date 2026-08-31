"""The Env surface as ONE contract, for the E9 stateful differential.

`tests/semantics/env_scenarios.py`'s table drives this contract (and the two
`token_style` fixtures) through the tier-1 model and the compiled-WASM
mini-host, comparing the two legs. It exists because two things the table has
to cover are unreachable through every contract the repo already ships:

* **a bare `get(key, T)` with no `default=`**, i.e. `MissingValue` at both
  tiers. Most existing contracts write the key in their constructor
  (`errors.py`, `token_style.py`) or guard the read with `has`
  (`structs.py`); the one exception is `spike1_reauthored.py`'s `bump()`,
  which reads `SETTINGS` before any constructor writes it (there is no
  `__init__`, only a plain `setup()` method) -- but that fixture is not one
  of this table's contracts, so the reserved-code miss is still not reachable
  through anything THIS table drives;
* **`require_auth_for_args`**, which no example or fixture calls at all.

Everything else the table needs is here too, one narrow method per surface, so
that a scenario is a sequence of calls rather than a contract of its own: the
three durabilities, `default=`, `has`, `del_`, all three `extend_ttl` shapes,
both event spellings, `require_auth`, and both ledger reads.

**Deliberately NOT in `test_emitter_end_to_end.py`'s `FIXTURES`.** That tuple
carries the contracts the whole-contract property sweep and the WAT goldens run
over -- the shipped examples and the chain-anchored fixtures. This one is a test
instrument for one table: `tests/unit/test_env_differential.py` builds it,
validates it and invokes every method in it on every run -- an exact set
equality the differential asserts, not "almost every" -- which is the
coverage that matters for it.

No constructor, on purpose: `deploy` takes no arguments and the declared
protocol is the plain import floor (20), which keeps the scenario table's
`__constructor` handling exercised by the `token_style` rows instead of by
every row (`errors.py`/`allowance_token.py` and the 2026-08-28 protocol-floor
decision are where the constructor's own floor of 22 is pinned).

Imports ONLY from the `serpent` package root, like every other fixture here, so
this file is also under the tests-wide `uv run mypy --strict` run.
"""

from serpent import (
    U32,
    U64,
    Address,
    Annotated,
    Bool,
    Env,
    Event,
    Symbol,
    Vec,
    contract,
    contracterror,
    contractevent,
    contracttype,
    errorcode,
    topic,
)


@contracterror
class SurfaceError:
    """The one in-band refusal this contract can raise."""

    Refused = errorcode(1)


@contracttype
class Slot:
    """A struct storage key -- the dominant real-world shape (keyed on an
    `Address`), and what makes the persistent rows a KEY round-trip: the key
    the read rebuilds is a different object from the one the write used."""

    owner: Address


@contractevent(topics=("logged",), data_format="single-value")
class Logged(Event):
    """`(Symbol("logged"), who)` as topics, the amount as bare data.

    The prefix topic is 6 characters so that the CANONICAL spelling can publish
    the identical record: a hand-written `topics[0]` is held to the 9-character
    `SymbolSmall` bound (`SPT3019`), where a declared prefix may run to 32
    (`examples/events.py` explains the split).
    """

    who: Annotated[Address, topic]
    amount: U32


@contract
class EnvSurface:
    """One method per Env surface the E9 table drives."""

    # --- instance storage ---------------------------------------------------

    def put_instance(self, env: Env, key: Symbol, value: U32) -> None:
        env.storage().instance().set(key, value)

    def read_instance(self, env: Env, key: Symbol) -> U32:
        """A BARE `get` -- the `MissingValue` path, at both tiers."""
        return env.storage().instance().get(key, U32)

    def read_instance_or(self, env: Env, key: Symbol, fallback: U32) -> U32:
        return env.storage().instance().get(key, U32, default=fallback)

    def has_instance(self, env: Env, key: Symbol) -> Bool:
        return env.storage().instance().has(key)

    def drop_instance(self, env: Env, key: Symbol) -> None:
        env.storage().instance().del_(key)

    def bump_instance(self, env: Env, threshold: U32, extend_to: U32) -> None:
        """The KEYLESS extend: the instance sub-map has one shared live-until."""
        env.storage().instance().extend_ttl(threshold, extend_to)

    # --- persistent storage, under a struct key -----------------------------

    def put_slot(self, env: Env, owner: Address, value: U32) -> None:
        key = Slot(owner=owner)
        env.storage().persistent().set(key, value)

    def read_slot(self, env: Env, owner: Address) -> U32:
        key = Slot(owner=owner)
        return env.storage().persistent().get(key, U32)

    def read_slot_or(self, env: Env, owner: Address, fallback: U32) -> U32:
        key = Slot(owner=owner)
        return env.storage().persistent().get(key, U32, default=fallback)

    def has_slot(self, env: Env, owner: Address) -> Bool:
        key = Slot(owner=owner)
        return env.storage().persistent().has(key)

    def drop_slot(self, env: Env, owner: Address) -> None:
        key = Slot(owner=owner)
        env.storage().persistent().del_(key)

    def bump_slot(self, env: Env, owner: Address, threshold: U32, extend_to: U32) -> None:
        key = Slot(owner=owner)
        env.storage().persistent().extend_ttl(key, threshold, extend_to)

    # --- temporary storage --------------------------------------------------

    def put_temp(self, env: Env, key: Symbol, value: U32) -> None:
        env.storage().temporary().set(key, value)

    def read_temp_or(self, env: Env, key: Symbol, fallback: U32) -> U32:
        return env.storage().temporary().get(key, U32, default=fallback)

    def has_temp(self, env: Env, key: Symbol) -> Bool:
        return env.storage().temporary().has(key)

    def bump_temp(self, env: Env, key: Symbol, threshold: U32, extend_to: U32) -> None:
        env.storage().temporary().extend_ttl(key, threshold, extend_to)

    # --- events, both spellings ---------------------------------------------

    def log_declared(self, env: Env, who: Address, amount: U32) -> None:
        """The AUTHORING spelling: the declaration carries the convention."""
        Logged(who=who, amount=amount).publish(env)

    def log_canonical(self, env: Env, who: Address, amount: U32) -> None:
        """The CANONICAL spelling, hand-written to match `log_declared`'s
        desugar exactly: the same topics, and the same bare data value."""
        env.events().publish((Symbol("logged"), who), amount)

    def log_then_refuse(self, env: Env, who: Address, amount: U32) -> None:
        """Publish, then raise -- the F.1.8 no-rollback observable.

        Both models keep the event; the chain rolls it back with the frame.
        `tests/unit/test_env_differential.py` states that gap where it asserts
        this method's outcome, and it is a carried obligation to sub-plan F.
        """
        Logged(who=who, amount=amount).publish(env)
        raise SurfaceError.Refused

    # --- auth ---------------------------------------------------------------

    def guard(self, env: Env, who: Address) -> None:
        who.require_auth()

    def guard_args(self, env: Env, who: Address, amount: U32) -> None:
        """`require_auth_for_args` -- the args-carrying form.

        Tier 1 records a `Vec` SNAPSHOT of the args; the mini host shape-checks
        the vec and DISCARDS it, so every scenario that asserts on the args is
        tier-1-only (review M11).
        """
        args = Vec(U32, [amount])
        who.require_auth_for_args(args)

    # --- ledger -------------------------------------------------------------

    def ledger_time(self, env: Env) -> U64:
        return env.ledger().timestamp()

    def ledger_seq(self, env: Env) -> U32:
        return env.ledger().sequence()
