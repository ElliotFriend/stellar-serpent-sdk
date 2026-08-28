"""Error codes: one `@contracterror` enum, one failure mode per method.

A Soroban contract's failures are part of its interface. `@contracterror` is how
a contract declares them, `errorcode(N)` fixes each member's number, and
`raise VaultError.LimitExceeded` is the whole call: the compiled contract calls
the host's `fail_with_error` with a Contract-type error carrying code 3, and a
client can classify that. Nothing else about the failure crosses the boundary --
no message, no traceback, no Python exception class -- so the NUMBER and the
storage state left behind are the entire observable contract.

Four rules worth copying out of this file:

* **one code per failure mode, and never reuse a number.** The codes are your
  public API; renumbering one is a breaking change for every client that
  branches on it. Add new members at the end.
* **check before you write.** Every raise below happens before the matching
  `storage().set(...)`, so a refused call leaves the balance exactly as it was.
  Tier 1 has no frame rollback and neither does the mini host, so a contract
  that wrote first and raised second would leave a half-applied state in a test
  as well as (for the guest-trap case) surprising you on chain.
* **`raise` is the only failure surface.** There is no `try`/`except` in the
  subset: a contract either completes or fails the invocation.
* **a bare `get(key, T)` with no `default=` traps on a missing key** with a
  reserved runtime code, not with one of yours. `balance()` below passes
  `default=U32(0)` because "nothing deposited yet" is not an error; `deposit()`
  reads `LIMIT` without a default because the constructor always stored it, and
  a missing `LIMIT` would be a serpent bug rather than a contract state.

## The one caveat that surprises every Python developer: `__init__` errors

`__init__` compiles to `__constructor`, which the deploy operation runs exactly
once. If it raises, spec S12 applies:

    the host *launders* constructor errors -- any recoverable error raised in
    the constructor reaches the deployer as `Context(InvalidAction)`, not the
    user's error code (`lifecycle.rs`). **The docs must say so, prominently**,
    because Python developers will expect `__init__` exception semantics.

So `VaultError.LimitTooSmall` below is REAL as a guard -- it stops a nonsense
deploy -- but the deployer never sees the 1. On chain they see
`Context(InvalidAction)`: "the constructor refused", with no code to branch on.
Two consequences for the way you write a constructor:

* do not design a client that reads constructor error codes. It cannot;
* if a failure mode needs to be reportable, move it out of `__init__` into an
  ordinary `initialize`-style method the deployer calls afterwards, where the
  code survives.

serpent models the laundering rather than papering over it: `deploy` raises
`ConstructorFailed`, never your error class, and chains your error as
`__cause__` so a test can still prove WHICH guard fired:

    from serpent.env import ConstructorFailed, Env, deploy

    env = Env()
    try:
        deploy(Vault, env, owner, U32(0))
    except ConstructorFailed as exc:
        assert isinstance(exc.__cause__, VaultError.LimitTooSmall)

`tests/unit/test_examples.py` asserts exactly that, and also that the compiled
contract's own `__constructor` still fails with code 1 under the mini host --
the mini host is the contract's view of the call, and the laundering happens
above it, in the deploy operation.
"""

from serpent import U32, Address, Env, Symbol, contract, contracterror, errorcode

#: Instance-storage keys. `Symbol` keys are the simple case; `examples/structs.py`
#: shows the struct-keyed shape a real balance map needs.
OWNER = Symbol("OWNER")
LIMIT = Symbol("LIMIT")
BALANCE = Symbol("BALANCE")


@contracterror
class VaultError:
    """Every way an invocation of `Vault` can fail, numbered.

    `LimitTooSmall` is raised from both `__init__` and `set_limit`, on purpose:
    the same guard is reportable from the method and laundered from the
    constructor, which is the asymmetry the module docstring is about.
    """

    LimitTooSmall = errorcode(1)
    Unauthorized = errorcode(2)
    LimitExceeded = errorcode(3)
    InsufficientBalance = errorcode(4)


@contract
class Vault:
    """A deposit balance with an owner-settable ceiling."""

    def __init__(self, env: Env, owner: Address, limit: U32) -> None:
        """Store the owner and the ceiling. A zero ceiling is refused (S12!)."""
        if limit == U32(0):
            raise VaultError.LimitTooSmall
        env.storage().instance().set(OWNER, owner)
        env.storage().instance().set(LIMIT, limit)

    def deposit(self, env: Env, amount: U32) -> U32:
        """Add `amount` to the balance; refuse to cross the ceiling."""
        limit = env.storage().instance().get(LIMIT, U32)
        balance = env.storage().instance().get(BALANCE, U32, default=U32(0))
        if balance + amount > limit:
            raise VaultError.LimitExceeded
        balance = balance + amount
        env.storage().instance().set(BALANCE, balance)
        return balance

    def withdraw(self, env: Env, amount: U32) -> U32:
        """Take `amount` out of the balance; refuse to overdraw it."""
        balance = env.storage().instance().get(BALANCE, U32, default=U32(0))
        if amount > balance:
            raise VaultError.InsufficientBalance
        balance = balance - amount
        env.storage().instance().set(BALANCE, balance)
        return balance

    def set_limit(self, env: Env, caller: Address, limit: U32) -> None:
        """Move the ceiling. Only the owner may, and it may not be zero.

        The owner check is a stored-`Address` comparison, which is a compile-time
        `Address == Address` and a host `obj_cmp` at runtime. A real contract
        would also call `caller.require_auth()` -- the check that the caller
        really authorized THIS invocation, rather than merely being named by it.
        It is left out here so that this file stays about error codes;
        `tests/fixtures/token_style.py` shows the authorized shape.
        """
        owner = env.storage().instance().get(OWNER, Address)
        if caller != owner:
            raise VaultError.Unauthorized
        if limit == U32(0):
            raise VaultError.LimitTooSmall
        env.storage().instance().set(LIMIT, limit)

    def balance(self, env: Env) -> U32:
        """Read the balance. Never fails: "nothing yet" is `U32(0)`."""
        return env.storage().instance().get(BALANCE, U32, default=U32(0))
