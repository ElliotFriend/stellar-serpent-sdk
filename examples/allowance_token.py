"""An allowance-style token, without cross-contract calls (spec S6).

`tests/fixtures/token_style.py` is a *mint/transfer* token: an owner moves
their own balance, and nobody else's. This example is the shape that fixture
does not cover -- an OWNER authorizes a SPENDER to move up to some amount on
their behalf (`approve`/`transfer_from`, the ERC-20-style pattern), which is
the case `env.py`'s storage-key docstring names by name: "the host's storage
key is an arbitrary `Val` and real contracts key on tuples/structs (an
allowance keyed by `(from, spender)` ...)". `AllowanceKey` below is exactly
that composite key.

## Why the allowance lives in `temporary()`, not `persistent()`

A balance should outlive its owner's next login; a stale, forgotten approval
should not sit around forever as a standing risk. `temporary()` is the bucket
that models that: an allowance's storage entry can EXPIRE, and this file's
`approve` extends its TTL explicitly, with an author-chosen `threshold`/
`extend_to` pair -- the same `extend_ttl(key, threshold, extend_to)` call
`env.py`'s TTL section documents for both keyed buckets.

**The threshold guard and never-reduce, in one call.** `approve`'s `extend_ttl`
call only actually moves the live-until when fewer than `threshold` ledgers of
lifetime remain (so re-approving the same allowance every block does not
re-extend it every time), and a later, smaller `extend_to` can never pull a
live-until backwards. Both rules are the model's, not this contract's -- see
`env.py`'s `_extended_live_until`.

## The expiry story, and where it stops being provable

`tests/unit/test_examples.py` runs the showcase E4's TTL model exists for:
`approve`, then `env.advance(...)` past the granted live-until, then
`transfer_from` -- which reads the lapsed allowance as `U32(0)` (an expired
temporary entry reads absent, and `allowance(...)`'s `get(..., default=U32(0))`
turns "absent" into a plain zero, exactly as "nothing approved yet" does) and
raises this file's OWN `AllowanceError.InsufficientAllowance`, not a generic
missing-value trap. That is the point of building the allowance read on
`default=`, the same way `examples/errors.py`'s `balance()` does: an expired
approval and a NEVER-made one are the same contract state, and both fail
through the contract's own error vocabulary.

**The WASM leg cannot run that scenario at all.** `tests/harness` (the mini
host under `FullHost`) has no TTL model -- `extend_contract_data_ttl` is
recorded and nothing else (`env.py`'s TTL section, and sub-plan F's carried
obligation on the clamp/trap asymmetry). So the cross-check this file's tests
run is narrower than usual: the SAME call sequence WITHOUT ever advancing past
a live-until is asserted to agree between tier 1 and WASM, and the expiry
itself is proven at tier 1 only, with a comment naming sub-plan F as the place
that eventually proves it against a real host.

## `require_auth`, on the party that is actually acting

Three methods call it, each on the address whose funds or allowance the call
moves: `mint` on the admin (only the admin may create balance out of nothing),
`approve` on the owner (only the owner may authorize a spender), and
`transfer_from` on the SPENDER (not the owner -- the owner already authorized
this spender once, in `approve`; requiring the owner's auth again on every
`transfer_from` would defeat the entire point of an allowance). This is the
full authorized shape `examples/errors.py`'s `set_limit` docstring points at
and deliberately does not show itself.

Run it two ways -- `tests/unit/test_examples.py` does both and asserts the two
legs agree (minus the expiry scenario, which only tier 1 can run):

    from serpent.env import Env, deploy

    env = Env()
    token = deploy(AllowanceToken, env, admin)
    with env.frame():
        token.mint(env, admin, owner, U32(100))
        token.approve(env, owner, spender, U32(40), U32(0), U32(1000))
        token.transfer_from(env, spender, owner, to, U32(10))   # -> None
"""

from serpent import (
    U32,
    Address,
    Annotated,
    Env,
    Event,
    Symbol,
    contract,
    contracterror,
    contractevent,
    contracttype,
    errorcode,
    topic,
)

ADMIN = Symbol("ADMIN")


@contracterror
class AllowanceError:
    """Both failure modes `transfer_from` can hit, checked in this order."""

    InsufficientAllowance = errorcode(1)
    InsufficientBalance = errorcode(2)


@contracttype
class BalanceKey:
    """One persistent entry per holder."""

    owner: Address


@contracttype
class AllowanceKey:
    """The composite key: one temporary entry per `(owner, spender)` pair --
    Q11's literal case (`env.py`'s storage-key docstring)."""

    owner: Address
    spender: Address


@contractevent(topics=("approve",), data_format="single-value")
class Approval(Event):
    """`(owner, spender)` as topics, the granted amount as data."""

    owner: Annotated[Address, topic]
    spender: Annotated[Address, topic]
    amount: U32


@contractevent(topics=("transfer",), data_format="single-value")
class Transfer(Event):
    """The same `(from, to)`-as-topics shape `token_style.py`'s `Transfer` uses."""

    from_: Annotated[Address, topic]
    to: Annotated[Address, topic]
    amount: U32


@contract
class AllowanceToken:
    """Balances in `persistent()`, allowances in `temporary()` with a TTL."""

    def __init__(self, env: Env, admin: Address) -> None:
        """Store the admin. No ceiling, no laundering caveat here -- this
        constructor cannot fail (`examples/errors.py` is where that story is
        told)."""
        env.storage().instance().set(ADMIN, admin)

    def mint(self, env: Env, admin: Address, to: Address, amount: U32) -> None:
        """Create `amount` of balance for `to`. Only the admin may."""
        admin.require_auth()
        key = BalanceKey(owner=to)
        current = env.storage().persistent().get(key, U32, default=U32(0))
        env.storage().persistent().set(key, current + amount)

    def balance(self, env: Env, owner: Address) -> U32:
        """Read a holder's balance. Never fails: "nothing yet" is `U32(0)`."""
        key = BalanceKey(owner=owner)
        return env.storage().persistent().get(key, U32, default=U32(0))

    def allowance(self, env: Env, owner: Address, spender: Address) -> U32:
        """Read what `spender` may still move on `owner`'s behalf.

        `default=U32(0)` makes "never approved", "fully spent" and "the
        approval expired" the same answer -- which is exactly what an on-chain
        reader of an EXPIRED temporary entry would see too (S8's dead-entry
        rule from the OUTSIDE looks identical to "nothing was ever there").
        """
        key = AllowanceKey(owner=owner, spender=spender)
        return env.storage().temporary().get(key, U32, default=U32(0))

    def approve(
        self,
        env: Env,
        owner: Address,
        spender: Address,
        amount: U32,
        threshold: U32,
        extend_to: U32,
    ) -> None:
        """Grant `spender` up to `amount` of `owner`'s balance, and set the
        allowance entry's TTL.

        `owner.require_auth()` is the whole point: only the account granting
        the allowance may set it. The `extend_ttl` call is `env.py`'s
        threshold-guard/never-reduce algebra applied to a REAL allowance entry
        -- a later `approve` with a smaller `extend_to` cannot shorten a
        live-until an earlier call already set.
        """
        owner.require_auth()
        key = AllowanceKey(owner=owner, spender=spender)
        env.storage().temporary().set(key, amount)
        env.storage().temporary().extend_ttl(key, threshold, extend_to)
        Approval(owner=owner, spender=spender, amount=amount).publish(env)

    def transfer_from(
        self, env: Env, spender: Address, owner: Address, to: Address, amount: U32
    ) -> None:
        """Move `amount` from `owner` to `to`, consuming `spender`'s allowance.

        `spender.require_auth()`, not `owner`'s: the owner already authorized
        this spender, once, in `approve`. Two checks, in order, each its own
        code: the allowance first (so a stranger with no allowance at all gets
        the SAME answer an expired one does, per the module docstring), then
        the balance -- both before either storage write, so a refused call
        changes nothing.

        One tier-1 wrinkle worth knowing (`env.py`'s `set` docstring): the
        allowance's re-`set` below resets ITS OWN live-until to "never
        extended", exactly as any other write to a keyed bucket does. A spent
        allowance is therefore immortal again until the next `approve`
        extends it -- a model consequence, not a rule this contract enforces.
        """
        spender.require_auth()
        allowance_key = AllowanceKey(owner=owner, spender=spender)
        allowance = env.storage().temporary().get(allowance_key, U32, default=U32(0))
        if allowance < amount:
            raise AllowanceError.InsufficientAllowance
        owner_key = BalanceKey(owner=owner)
        owner_balance = env.storage().persistent().get(owner_key, U32, default=U32(0))
        if owner_balance < amount:
            raise AllowanceError.InsufficientBalance
        env.storage().temporary().set(allowance_key, allowance - amount)
        env.storage().persistent().set(owner_key, owner_balance - amount)
        to_key = BalanceKey(owner=to)
        to_balance = env.storage().persistent().get(to_key, U32, default=U32(0))
        env.storage().persistent().set(to_key, to_balance + amount)
        Transfer(from_=owner, to=to, amount=amount).publish(env)
