"""Structs: a `@contracttype` record, stored under a `@contracttype` KEY.

`@contracttype` declares a struct -- a record of chain-typed fields that the
host carries as a map object, and that appears in the contract's spec so a
client can build and read one. Two uses, both here:

* **as a stored VALUE** (`Membership`): one entry holds several fields, read back
  with ordinary attribute access (`membership.display_name`);
* **as a storage KEY** (`MemberKey`): a storage key does not have to be a
  `Symbol`. Keying an entry on a struct is how a contract gets a MAP of records
  -- one entry per member here, one per holder in a token's balance map -- and
  it is the dominant real-world shape. `MemberKey(owner=...)` and
  `MemberKey(owner=...)` built in two different invocations are the same key,
  because the host compares the value, not the object handle.

Construction is keyword-only (`Membership(display_name=..., joined_ledger=...)`),
every field is required, and every field needs a chain-type annotation.

## Why `display_name` is 12 characters: the linear-memory consequence

A Soroban `Symbol` of up to 9 characters fits INSIDE a 64-bit `Val` (a
`SymbolSmall`, packed 6 bits per character). A longer one does not: it has to be
built at runtime out of the module's linear memory, with
`symbol_new_from_linear_memory`, from bytes the compiler laid down in the data
section.

Struct field names are symbols, so a field name over 9 characters changes what
the compiled contract does:

* `display_name` (12) and `joined_ledger` (13) go into the literal pool, and the
  module grows a data section and a memory;
* laying out the whole struct still costs one call
  (`map_new_from_linear_memory` reads its key descriptors straight from the
  pool), but reading ONE field back has to materialize that field's name as a
  `SymbolObject` first, then `map_get` -- two host calls where a short name
  would have needed one;
* every one of those is guest instructions and host work, which is budget.

Nothing here is wrong -- long field names are normal and readable, and this
example uses them deliberately so the effect is visible rather than surprising.
It is just worth knowing that `display_name` and `name` do not compile to the
same thing. `tests/unit/test_examples.py` asserts the
`symbol_new_from_linear_memory` call really happens.

## The time-algebra bridge (deferred to M2, ruling E3)

`Timepoint`/`Duration` are u64 newtypes with NO arithmetic of their own -- not
even same-type (`Timepoint(1) + Timepoint(1)` is a `TypeError` naming the
omission, same as Rust's own newtypes carry no operators either). serpent will
not invent a time algebra ahead of a real contract needing one; the bridge is
`to_u64()`/`from_u64()`, and it reads like this for a membership that expires
30 days after it is renewed (a field this `Registry` does not have, but could):

    from serpent import Duration, Timepoint

    THIRTY_DAYS = Duration(30 * 24 * 60 * 60)

    def renew(self, env: Env, owner: Address) -> None:
        now = env.ledger().timestamp()                     # U64
        expires = Timepoint.from_u64(now + THIRTY_DAYS.to_u64())
        ...                                                  # store `expires`

    def is_expired(self, env: Env, expires: Timepoint) -> Bool:
        return Bool(env.ledger().timestamp() >= expires.to_u64())

Do the arithmetic on plain `U64` (checked overflow, same as any other chain
integer), then wrap the result back into whichever type the field or return
annotation actually declares.

Run it two ways -- `tests/unit/test_examples.py` does both and asserts the two
legs agree:

    from serpent.env import Env, deploy

    env = Env()
    registry = deploy(Registry, env)
    with env.frame():
        registry.join(env, owner, String("Ana Registrar"))
        registry.display_name_of(env, owner)   # -> String('Ana Registrar')
"""

from serpent import (
    U32,
    Address,
    Env,
    String,
    contract,
    contracterror,
    contracttype,
    errorcode,
)


@contracttype
class MemberKey:
    """The storage key: one persistent entry per member address."""

    owner: Address


@contracttype
class Membership:
    """The stored record. Both field names are over 9 characters (see above)."""

    display_name: String
    joined_ledger: U32


@contracterror
class RegistryError:
    AlreadyJoined = errorcode(1)
    NotAMember = errorcode(2)


@contract
class Registry:
    """A directory of members, keyed by address."""

    def join(self, env: Env, owner: Address, display_name: String) -> None:
        """Register `owner` once, stamping the current ledger sequence."""
        key = MemberKey(owner=owner)
        if env.storage().persistent().has(key):
            raise RegistryError.AlreadyJoined
        membership = Membership(
            display_name=display_name,
            joined_ledger=env.ledger().sequence(),
        )
        env.storage().persistent().set(key, membership)

    def display_name_of(self, env: Env, owner: Address) -> String:
        """One field of the stored record.

        The `has` guard is what turns "no such member" into `NotAMember` instead
        of a trap carrying serpent's reserved missing-value code: a bare
        `get(key, T)` on an absent key fails the invocation with a number that
        is not part of this contract's vocabulary. `default=` is no help for a
        struct -- there is no sensible empty `Membership` -- so the guard is the
        way to own the failure.
        """
        key = MemberKey(owner=owner)
        if not env.storage().persistent().has(key):
            raise RegistryError.NotAMember
        membership = env.storage().persistent().get(key, Membership)
        return membership.display_name

    def joined_ledger_of(self, env: Env, owner: Address) -> U32:
        """The other field, read the same way."""
        key = MemberKey(owner=owner)
        if not env.storage().persistent().has(key):
            raise RegistryError.NotAMember
        membership = env.storage().persistent().get(key, Membership)
        return membership.joined_ledger
