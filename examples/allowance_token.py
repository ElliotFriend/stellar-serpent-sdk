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
that models that: an allowance's storage entry can EXPIRE, and BOTH `approve`
and `transfer_from` extend its TTL explicitly, using this contract's OWN
`ALLOWANCE_TTL_THRESHOLD`/`ALLOWANCE_TTL_EXTEND_TO` constants -- the same
`extend_ttl(key, threshold, extend_to)` call `env.py`'s TTL section documents
for both keyed buckets. `transfer_from` re-extends rather than relying on
`approve`'s original grant because its own reducing `set()` would otherwise
reset the entry's live-until to "never extended" (`env.py`'s `set` docstring:
any write to a keyed bucket does), which would make a partially-spent
allowance IMMORTAL until the next `approve` -- exactly the "sits around
forever" risk this section opens with. Re-extending on every touch, not only
on creation, is the standard Soroban pattern for a `temporary()` entry that is
read AND written more than once.

**Why the TTL bounds are CONTRACT-chosen constants, not caller-supplied
arguments.** `transfer_from`'s caller is the SPENDER, and letting the spender
pick how long the OWNER's residual allowance keeps living would hand that
spender a decision that is not theirs to make -- precisely the standing-risk
problem `temporary()` exists to fix, reintroduced through the back door of a
parameter. `approve`'s caller is the owner, but the two methods share one key
and must agree on one policy, so both read the same two constants.

**A worth-knowing consequence of "every write resets the live-until":**
`approve` and `transfer_from` both write to the allowance entry immediately
before calling `extend_ttl`, so that call always finds a never-extended
(`None`) entry -- and a `None` entry always takes the FULL `extend_to`
(`env.py`'s `_extended_live_until` docstring: "the first extension always
applies"), REGARDLESS of what threshold is in force. The guard's actual
direction (`live_until - sequence >= threshold` is the no-op case) means a
SMALL threshold is what blocks a still-plenty-of-life-left entry, and a LARGE
one almost always lets an extension through
(`tests/unit/test_env_ttl.py::test_the_threshold_guard_refuses_when_enough_lifetime_remains`
is the blocking case; `test_an_extension_never_reduces` is the large-threshold
case this contract's write-then-extend pattern actually resembles) -- but
neither direction can be observed HERE: the threshold guard never fires
through this contract's methods at all, no matter what threshold is chosen,
including a small one that would ordinarily block a live entry. That is a
consequence of always writing before extending, not a bug, and it is exactly
why the constants below are the contract's to pick rather than a caller's:
whichever value they carried, the guard could never matter.

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
        token.approve(env, owner, spender, U32(40))
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

#: This contract's own TTL policy for an allowance entry -- a CONSTANT, not a
#: caller-supplied argument (the module docstring says why): both `approve`
#: and `transfer_from` extend the SAME key with the SAME two numbers, so the
#: policy is one the contract commits to rather than one either caller picks.
ALLOWANCE_TTL_THRESHOLD = U32(500)
ALLOWANCE_TTL_EXTEND_TO = U32(1000)


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

    def approve(self, env: Env, owner: Address, spender: Address, amount: U32) -> None:
        """Grant `spender` up to `amount` of `owner`'s balance, and extend the
        allowance entry's TTL by this contract's own bounds.

        `owner.require_auth()` is the whole point: only the account granting
        the allowance may set it. `ALLOWANCE_TTL_THRESHOLD`/
        `ALLOWANCE_TTL_EXTEND_TO` are not parameters here -- the module
        docstring says why -- and because the write just above always resets
        this entry's live-until first, the threshold guard never actually
        fires through this method regardless (the module docstring explains
        that too).
        """
        owner.require_auth()
        key = AllowanceKey(owner=owner, spender=spender)
        env.storage().temporary().set(key, amount)
        env.storage().temporary().extend_ttl(key, ALLOWANCE_TTL_THRESHOLD, ALLOWANCE_TTL_EXTEND_TO)
        Approval(owner=owner, spender=spender, amount=amount).publish(env)

    def transfer_from(
        self, env: Env, spender: Address, owner: Address, to: Address, amount: U32
    ) -> None:
        """Move `amount` from `owner` to `to`, consuming `spender`'s allowance.

        `spender.require_auth()`, not `owner`'s: the owner already authorized
        this spender, once, in `approve`. The allowance is checked first (so
        a stranger with no allowance gets the SAME answer an expired one
        does), then the balance -- both before either write, so a refused
        call changes nothing.

        The re-`set()` below is followed by its OWN `extend_ttl`, using the
        SAME contract-chosen bounds `approve` uses: without it the reducing
        write would reset the live-until to "never extended" (`env.py`'s
        `set` docstring), making a spent allowance IMMORTAL until the next
        `approve`.
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
        env.storage().temporary().extend_ttl(
            allowance_key, ALLOWANCE_TTL_THRESHOLD, ALLOWANCE_TTL_EXTEND_TO
        )
        env.storage().persistent().set(owner_key, owner_balance - amount)
        to_key = BalanceKey(owner=to)
        to_balance = env.storage().persistent().get(to_key, U32, default=U32(0))
        env.storage().persistent().set(to_key, to_balance + amount)
        Transfer(from_=owner, to=to, amount=amount).publish(env)
