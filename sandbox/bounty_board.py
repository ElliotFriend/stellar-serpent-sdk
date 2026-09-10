"""A bounty board: one contract that touches every M1 authoring surface.

What is in here, and where to look:

* a **constructor** (`__init__`) that records the admin in INSTANCE storage;
* a `@contracttype` **struct** (`Bounty`) stored in PERSISTENT storage under a
  struct **key** (`BountyKey`);
* a `@contractunion` **tagged union** (`Status`) with a unit case, an `Address`
  payload, and a `U32` payload, read back with `tag()` and `payload()`;
* a `@contractenum` **int enum** (`Priority`) passed in, stored, and returned;
* a `@contracterror` **error enum** raised from four different guards;
* three `@contractevent` **events**, one per data format (`map`,
  `single-value`, `vec`), published through the authoring form;
* TEMPORARY storage with an explicit **TTL** (`extend_ttl`) so a claim lapses
  if the worker never finishes;
* `require_auth()` on the poster, the worker, and the stored admin;
* the ledger sequence, a checked-arithmetic counter, a `Vec[U32]` built in a
  `while` loop, and private helper methods.

Every method runs unchanged at tier 1 (plain Python, `serpent.env.Env`), on
the embedded real host (`serpent.testing.RealEnv`), and as the deployable
WASM `sandbox/compile.py` writes next to this file.
"""

from serpent import (
    U32,
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

# --- instance-storage keys (the contract's own singleton state) ---------------

ADMIN = Symbol("ADMIN")
COUNT = Symbol("COUNT")

#: A claim is a temporary entry: if the worker never completes, it lapses and
#: the bounty can be claimed again. `extend_ttl(key, threshold, extend_to)`
#: extends only when the remaining TTL is at or below `threshold`, so a fresh
#: claim always lands at `CLAIM_TTL` ledgers.
CLAIM_TTL = U32(200)
CLAIM_TTL_THRESHOLD = U32(200)


# --- the user-defined types ----------------------------------------------------


@contractenum
class Priority(ContractEnum):
    """How urgent a bounty is. Each member IS its `u32` on chain."""

    Low = enumvalue(0)
    Medium = enumvalue(1)
    High = enumvalue(2)


# A struct KEY is its field map on chain: `{field_name: value, ...}`. The class
# name is NOT part of the key, so two key structs with the same field names and
# values address the SAME ledger entry. The three keys below therefore use
# DIFFERENT field names; `BountyKey(id=1)` and a hypothetical `StatusKey(id=1)`
# would have collided, and the status write would have overwritten the record.
# (This example's first draft did exactly that, and both tier 1 and the real
# host exposed it.)


@contracttype
class BountyKey:
    """The persistent-storage key for a bounty's record."""

    bounty_id: U32


@contracttype
class StatusKey:
    """The persistent-storage key for a bounty's `Status`, kept apart from the
    record so a status change never rewrites the whole `Bounty`."""

    status_of: U32


@contracttype
class ClaimKey:
    """The temporary-storage key for a live claim."""

    claim_on: U32


@contracttype
class Bounty:
    """The stored record. Field names longer than nine characters go through
    linear memory on chain; that is the compiler's problem, not the author's."""

    poster: Address
    reward: U32
    priority: Priority
    posted_at: U32


@contractunion
class Status(ContractUnion):
    """Where a bounty is in its life: open, claimed by someone, or paid out."""

    Open = variant()
    Claimed = variant(Address)
    Paid = variant(U32)


@contracterror
class BoardError:
    NoSuchBounty = errorcode(1)
    NotOpen = errorcode(2)
    NotClaimed = errorcode(3)
    ClaimExpired = errorcode(4)
    ZeroReward = errorcode(5)


# --- the events ------------------------------------------------------------------


@contractevent(topics=("posted",))
class Posted(Event):
    """Default `map` data: the topic is the id, the data is a map of the rest."""

    id: Annotated[U32, topic]
    poster: Address
    reward: U32


@contractevent(topics=("claimed",), data_format="single-value")
class Claimed(Event):
    """`single-value` data: exactly one un-marked field, the worker."""

    id: Annotated[U32, topic]
    worker: Address


@contractevent(topics=("completed",), data_format="vec")
class Completed(Event):
    """`vec` data: every field the same type, published as a `Vec[U32]`."""

    id: U32
    reward: U32


# --- the contract -------------------------------------------------------------------


@contract
class BountyBoard:
    """Post a bounty, claim it, complete it, and read it back."""

    def __init__(self, env: Env, admin: Address) -> None:
        env.storage().instance().set(ADMIN, admin)
        env.storage().instance().set(COUNT, U32(0))

    # -- writes -------------------------------------------------------------------

    def post(self, env: Env, poster: Address, reward: U32, priority: Priority) -> U32:
        """Post a new bounty and return its id. Only the poster may post as
        themself, and a zero reward is refused before anything is written."""
        poster.require_auth()
        if reward == U32(0):
            raise BoardError.ZeroReward
        bounty_id = env.storage().instance().get(COUNT, U32) + U32(1)
        env.storage().instance().set(COUNT, bounty_id)
        record = Bounty(
            poster=poster,
            reward=reward,
            priority=priority,
            posted_at=env.ledger().sequence(),
        )
        env.storage().persistent().set(BountyKey(bounty_id=bounty_id), record)
        env.storage().persistent().set(StatusKey(status_of=bounty_id), Status.Open)
        Posted(id=bounty_id, poster=poster, reward=reward).publish(env)
        return bounty_id

    def claim(self, env: Env, bounty_id: U32, worker: Address) -> None:
        """Claim an open bounty. The claim is a temporary entry that lapses
        after `CLAIM_TTL` ledgers, at which point `complete` refuses."""
        worker.require_auth()
        status = self._status(env, bounty_id)
        if status.tag() != Symbol("Open"):
            raise BoardError.NotOpen
        env.storage().persistent().set(StatusKey(status_of=bounty_id), Status.Claimed(worker))
        claim_key = ClaimKey(claim_on=bounty_id)
        env.storage().temporary().set(claim_key, worker)
        env.storage().temporary().extend_ttl(claim_key, CLAIM_TTL_THRESHOLD, CLAIM_TTL)
        Claimed(id=bounty_id, worker=worker).publish(env)

    def complete(self, env: Env, bounty_id: U32) -> U32:
        """The admin pays out a claimed bounty. Returns the reward paid.

        The stored admin authorizes, not a caller-supplied address: reading it
        back is what makes the check about THIS contract's admin.
        """
        admin = env.storage().instance().get(ADMIN, Address)
        admin.require_auth()
        status = self._status(env, bounty_id)
        if status.tag() != Symbol("Claimed"):
            raise BoardError.NotClaimed
        if not env.storage().temporary().has(ClaimKey(claim_on=bounty_id)):
            raise BoardError.ClaimExpired
        record = env.storage().persistent().get(BountyKey(bounty_id=bounty_id), Bounty)
        env.storage().persistent().set(StatusKey(status_of=bounty_id), Status.Paid(record.reward))
        env.storage().temporary().del_(ClaimKey(claim_on=bounty_id))
        Completed(id=bounty_id, reward=record.reward).publish(env)
        return record.reward

    # -- reads ----------------------------------------------------------------------

    def total_posted(self, env: Env) -> U32:
        return env.storage().instance().get(COUNT, U32)

    def status_of(self, env: Env, bounty_id: U32) -> Symbol:
        """The union's case name: `Open`, `Claimed`, or `Paid`."""
        return self._status(env, bounty_id).tag()

    def worker_of(self, env: Env, bounty_id: U32) -> Address:
        """The `Claimed` payload. Asking a non-claimed bounty is a contract error,
        never a trap: the guard runs before `payload()`."""
        status = self._status(env, bounty_id)
        if status.tag() != Symbol("Claimed"):
            raise BoardError.NotClaimed
        return status.payload(U32(0), Address)

    def reward_of(self, env: Env, bounty_id: U32) -> U32:
        return self._record(env, bounty_id).reward

    def priority_of(self, env: Env, bounty_id: U32) -> Priority:
        """An int enum RETURNED: the client receives the bare `u32`, the spec
        entry names it."""
        return self._record(env, bounty_id).priority

    def is_urgent(self, env: Env, bounty_id: U32) -> Bool:
        return Bool(self._record(env, bounty_id).priority == Priority.High)

    def posted_at(self, env: Env, bounty_id: U32) -> U32:
        return self._record(env, bounty_id).posted_at

    def open_ids(self, env: Env) -> Vec[U32]:
        """Every open bounty id, in posting order: a `Vec` built in a loop."""
        ids = Vec(U32, [])
        count = env.storage().instance().get(COUNT, U32)
        i = U32(1)
        while i <= count:
            if self._status(env, i).tag() == Symbol("Open"):
                ids.push_back(i)
            i = i + U32(1)
        return ids

    # -- private helpers --------------------------------------------------------------

    def _status(self, env: Env, bounty_id: U32) -> Status:
        key = BountyKey(bounty_id=bounty_id)
        if not env.storage().persistent().has(key):
            raise BoardError.NoSuchBounty
        return env.storage().persistent().get(StatusKey(status_of=bounty_id), Status)

    def _record(self, env: Env, bounty_id: U32) -> Bounty:
        key = BountyKey(bounty_id=bounty_id)
        if not env.storage().persistent().has(key):
            raise BoardError.NoSuchBounty
        return env.storage().persistent().get(key, Bounty)
