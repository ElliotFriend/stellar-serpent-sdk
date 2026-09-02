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
both event spellings, `require_auth`, both ledger reads, and (M1-E2 ruling
E13) a union and an int enum as stored VALUES and a union as a storage KEY.

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
    ContractEnum,
    ContractUnion,
    Env,
    Event,
    Symbol,
    Vec,
    contract,
    contractenum,
    contracterror,
    contractevent,
    contracttype,
    contractunion,
    enumvalue,
    errorcode,
    topic,
    variant,
)

#: The fixed keys the union/int-enum methods use, since their `Call`s (below)
#: carry only the value -- `Call.args` is scalars only, so there is no room
#: for a caller-supplied key on these rows.
SHAPE_KEY = Symbol("SHAPE")
COLOR_KEY = Symbol("COLOR")


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


@contractunion
class Shape(ContractUnion):
    """The E13 union kind on this table: a unit case (the "nothing yet"
    value) and a one-payload case, which is every arity `read_shape_area`'s
    `tag()`-branching read needs to demonstrate."""

    Empty = variant()
    Circle = variant(U32)


@contractenum
class Color(ContractEnum):
    """The E13 int-enum kind on this table: a numbered choice with no
    payload, read by `==` rather than by unwrapping anything."""

    Red = enumvalue(0)
    Green = enumvalue(1)


@contractevent(topics=("logged",), data_format="single-value")
class Logged(Event):
    """`(Symbol("logged"), who)` as topics, the amount as bare data.

    `logged` is 6 characters, so it packs into a `SymbolSmall` immediate instead
    of pooling through linear memory -- this fixture drives the E9 differential
    table, and keeping its topic on the immediate path leaves the pooled-symbol
    lowering to the files that exist to exercise it (`examples/events.py`,
    `examples/structs.py`). Nothing FORCES the length: `SPT3019` asks only that
    `topics[0]` be a Symbol, so `log_declared` and `log_canonical` would publish
    the identical record at any length up to the Symbol's 32.
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

    def read_instance_or_zero(self, env: Env, key: Symbol) -> U32:
        """A RAW-LITERAL `default=`, not a chain value -- the cross-tier pin.

        M1-C ADOPTS a literal in a typed position, so the compiled form's
        `IfExp` orelse is `U32(0)` and the WASM leg answers a chain `U32`. The
        tier-1 model has to adopt through `ty` the same way
        (`_StorageBucket.get`), or the two legs agree about the answer's VALUE
        and disagree about its TYPE -- silently, because `U32(0) == 0`. The
        differential compares `answer_type`, which is what turns that into a
        failure.

        Before M1-E2 Task 7, `mypy --strict` could not see M1-C's adoption: a
        single signature solved `_T` against both `U32` and `int` and got
        `object`, so this return carried the one narrow ignore code naming
        exactly that. Task 7 split `get` into four `@overload`s
        (`_StorageBucket.get`), one keyword-only over `int | str | bytes |
        bool`, so a raw-literal `default` no longer joins `_T`: it solves from
        `ty` alone, this returns a plain `U32`, and the ignore is gone.
        """
        return env.storage().instance().get(key, U32, default=0)

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

    # --- unions and int enums (M1-E2 ruling E13) ----------------------------

    def put_shape(self, env: Env, area: U32) -> None:
        """Store a `Shape.Circle` in PERSISTENT storage -- the union VALUE
        round trip. The payload IS the area directly, not a radius the read
        below would have to square: the point of this method is the
        tag/payload round trip, not arithmetic.
        """
        env.storage().persistent().set(SHAPE_KEY, Shape.Circle(area))

    def read_shape_area(self, env: Env) -> U32:
        """The `tag()`-branching read: `Circle`'s payload IS the answer, and
        the unit case -- never written by `put_shape` -- answers zero."""
        shape = env.storage().persistent().get(SHAPE_KEY, Shape, default=Shape.Empty)
        if shape.tag() == Symbol("Circle"):
            return shape.payload(U32(0), U32)
        else:
            return U32(0)

    def put_color(self, env: Env, n: U32) -> None:
        """Map the argument onto an enum MEMBER and store that, not the bare
        `U32` -- the round trip this method carries is the int-enum kind's,
        not a scalar's. `n == U32(1)` picks green; anything else picks red."""
        color = Color.Green if n == U32(1) else Color.Red
        env.storage().persistent().set(COLOR_KEY, color)

    def color_is_green(self, env: Env) -> Bool:
        """The int-enum read pattern: an `==` comparison, never `<`."""
        color = env.storage().persistent().get(COLOR_KEY, Color, default=Color.Red)
        return Bool(color == Color.Green)

    def put_by_shape_key(self, env: Env, value: U32) -> None:
        """A union as a storage KEY: `Shape.Circle(U32(1))` is rebuilt fresh
        on every access (a union has no identity on chain), with a PAYLOAD --
        not the unit case -- so `storage_key`'s `Vec` branch recurses into an
        element on both this write and the read below, on both legs."""
        env.storage().temporary().set(Shape.Circle(U32(1)), value)

    def read_by_shape_key(self, env: Env) -> U32:
        """The read rebuilds `Shape.Circle(U32(1))` again, independently of
        the write above -- the host compares the key's VALUE, so a
        separately built, equal-payload union still finds the entry the
        write used."""
        return env.storage().temporary().get(Shape.Circle(U32(1)), U32)
