"""A realistic token-shaped contract, in serpent's current authoring style.

Two purposes, both load-bearing:

1. **Imports ONLY from the `serpent` package root.** Nothing here reaches into
   `serpent.types`, `serpent.decorators`, `serpent.env` or `serpent.errors`
   directly -- proof by construction that Task 10's root export list is
   sufficient to author a real contract, not just the unit-test snippets that
   exercise submodules directly.
2. **Covered by the tests-wide `uv run mypy --strict` run** (`pyproject.toml`'s
   `[tool.mypy] files = ["src", "tests"]`, landed in Task 1). This module is
   therefore the executable proof of the zero-plugin `mypy --strict` claim:
   every authoring form below -- `NAME = errorcode(N)`, kwargs construction of
   a `@contracttype`, a `@contractevent` class inheriting `Event`, chained
   storage calls, `raise Error.NAME` -- must type-check with no plugin.

**Authoring forms demonstrated (the amended spec-Sec.2 style):**
* Methods take `self` first (ordinary, strict-clean Python methods).
* `NAME = errorcode(N)` error-enum members (`raise TokenError.NAME` type-checks
  because `errorcode` is annotated `-> type[ContractError]`).
* A `@contractevent` class inheriting `Event`, declared and mypy-visible. It is
  NOT published through `Transfer(...).publish(env)`: ruling E12 defers that
  form's topic/data split to sub-plan E (`_serpent_type_` carries none, B14),
  so M1-C compiles only the canonical `env.events().publish(topics, data)`
  line below -- which is exactly what `transfer` emits.
* Storage keys demonstrate BOTH halves of the widened key surface (Task 9's
  ruling): a plain `Symbol` key (`ADMIN`, `NAME_KEY`) and a `@contracttype`
  struct key (`BalanceKey`, keyed on an `Address` -- the dominant real-world
  pattern, e.g. a balance or allowance keyed on one or more addresses).
* A heterogeneous event-topic tuple (`Symbol`, `Address`, `Address`) -- the
  canonical Soroban shape, not a homogeneous `Vec[Symbol]`.

Not collected by pytest directly (no `test_*` functions); imported by
`tests/unit/test_public_api.py`, which is what puts it under the mypy gate and
proves the import succeeds at runtime too.
"""

from serpent import (
    U32,
    Address,
    Bool,
    Env,
    Event,
    String,
    Symbol,
    contract,
    contracterror,
    contractevent,
    contracttype,
    errorcode,
)

ADMIN = Symbol("ADMIN")
NAME_KEY = Symbol("NAME")


@contracterror
class TokenError:
    InsufficientBalance = errorcode(1)
    Unauthorized = errorcode(2)


@contracttype
class BalanceKey:
    """A struct storage key -- the widened surface, not just `Symbol`."""

    owner: Address


@contractevent
class Transfer(Event):
    frm: Address
    to: Address
    amount: U32


@contract
class TokenStyle:
    """A minimal fungible-token-shaped contract."""

    def __init__(self, env: Env, admin: Address, name: String) -> None:
        env.storage().instance().set(ADMIN, admin)
        env.storage().instance().set(NAME_KEY, name)

    def name(self, env: Env) -> String:
        return env.storage().instance().get(NAME_KEY, String)

    def is_admin(self, env: Env, who: Address) -> Bool:
        admin = env.storage().instance().get(ADMIN, Address)
        return Bool(who == admin)

    def balance(self, env: Env, owner: Address) -> U32:
        key = BalanceKey(owner=owner)
        return env.storage().persistent().get(key, U32, default=U32(0))

    def mint(self, env: Env, admin: Address, to: Address, amount: U32) -> None:
        admin.require_auth()
        key = BalanceKey(owner=to)
        current = env.storage().persistent().get(key, U32, default=U32(0))
        env.storage().persistent().set(key, current + amount)

    def transfer(self, env: Env, frm: Address, to: Address, amount: U32) -> None:
        frm.require_auth()
        from_key = BalanceKey(owner=frm)
        to_key = BalanceKey(owner=to)
        from_balance = env.storage().persistent().get(from_key, U32, default=U32(0))
        if from_balance < amount:
            raise TokenError.InsufficientBalance
        to_balance = env.storage().persistent().get(to_key, U32, default=U32(0))
        env.storage().persistent().set(from_key, from_balance - amount)
        env.storage().persistent().set(to_key, to_balance + amount)
        # The canonical heterogeneous topic shape: (Symbol, Address, Address).
        env.events().publish((Symbol("transfer"), frm, to), amount)
