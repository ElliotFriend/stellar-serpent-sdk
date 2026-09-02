"""The contract `tests/semantics/host_facts.py` drives (dossier D.3's HOST_FACTS).

One narrow export per host fact the tier-1 model and the mini host only ASSUMED.
Not an example (nothing here is idiomatic) and deliberately NOT joined to the
whole-contract sweeps: `test_emitter_end_to_end.py`'s `FIXTURES`,
`test_emitter_printer.py`'s `FIXTURE_SOURCES`/`FIXTURE_NAMES`, and
`test_harness_hostfns.py`'s `_FIXTURES` all stay as they are. Those inventories
carry the shipped examples and the chain-anchored fixtures, whose WAT goldens and
property sweeps are documentation of the emitter; this file is a test instrument
for ONE table, and its only consumer is `HOST_FACTS`.

It IS in the frontend fuzz corpus, because that corpus globs `tests/fixtures/*.py`
(review B8) -- so this file has to be a fully valid module, and
`test_the_corpus_is_the_whole_fixture_inventory` names it.

**Why every method here is so narrow.** A host fact is a single question asked of
the real host ("does an extension past the maximum clamp or trap?"), and the
answer has to be attributable to ONE host call. A method that did two things
would make a trap ambiguous about which call produced it, which is exactly the
ambiguity the table exists to remove.

**Why there is no `vec_lt`.** Container ordering (O12) cannot be asked through a
contract at all: `Bool(a < b)` on two `Vec`s is `SPT3005` -- containers have no
`<` in the subset -- so the question goes to the host's `Compare` trait directly
through `RealEnv.compare`, and the table's `COMPARE_VECTORS` carries it.

Imports ONLY from the `serpent` package root, like every other fixture here, so
this file is also under the tests-wide `uv run mypy --strict` run.
"""

from serpent import (
    I128,
    U32,
    U128,
    Address,
    Bool,
    Env,
    Symbol,
    contract,
    contracterror,
    errorcode,
)

#: The one storage key every storage method here uses. One key, because a host
#: fact about TTL is about an entry's own lifetime and a second key would only
#: add a second thing for a failure to be about.
KEY = Symbol("K")


@contracterror
class Refused:
    """The in-band refusal `publish_then_raise` uses.

    Code 9 rather than 1: the rollback row asserts the code that came back, and
    a code no other fixture in the repo uses makes "this is the error the
    contract raised" unambiguous in a diagnostic buffer read by hand.
    """

    Nope = errorcode(9)


@contract
class HostFacts:
    """One method per fact `HOST_FACTS` asks the real host."""

    # --- TTL (S9/O14): clamp, trap, dead entry ------------------------------------

    def put_p(self, env: Env, v: U32) -> None:
        env.storage().persistent().set(KEY, v)

    def put_t(self, env: Env, v: U32) -> None:
        env.storage().temporary().set(KEY, v)

    def get_p_or(self, env: Env, fallback: U32) -> U32:
        """A `default=` read, not a bare `get`: the archival row asks whether the
        entry is READABLE, and a bare `get` would answer that question with a
        reserved-code error whose classification is the emitter's E13 wrapper
        rather than the host's own answer."""
        return env.storage().persistent().get(KEY, U32, default=fallback)

    def get_t_or(self, env: Env, fallback: U32) -> U32:
        return env.storage().temporary().get(KEY, U32, default=fallback)

    def extend_p(self, env: Env, threshold: U32, extend_to: U32) -> None:
        env.storage().persistent().extend_ttl(KEY, threshold, extend_to)

    def extend_t(self, env: Env, threshold: U32, extend_to: U32) -> None:
        env.storage().temporary().extend_ttl(KEY, threshold, extend_to)

    # --- del_ on an absent key (O13) ---------------------------------------------

    def del_absent(self, env: Env) -> Bool:
        """`del_` then a constant: the answer is what proves the call RETURNED.

        A `-> None` method would make "the host accepted a delete of nothing"
        indistinguishable from "the host answered Void because it trapped and
        something swallowed it"; a `Bool(True)` only comes back if the delete
        did not end the frame.
        """
        env.storage().persistent().del_(KEY)
        return Bool(True)

    # --- publish then raise (S9/O15) ---------------------------------------------

    def publish_then_raise(self, env: Env, who: Address) -> None:
        env.events().publish((Symbol("logged"), who), U32(1))
        raise Refused.Nope

    # --- auth refusal is a trap, not a recorded auth (O19/O26) -------------------

    def guard(self, env: Env, who: Address) -> None:
        who.require_auth()

    # --- 128-bit division (O10/O11) ----------------------------------------------

    def div_i128(self, env: Env, a: I128, b: I128) -> I128:
        return a // b

    def mod_i128(self, env: Env, a: I128, b: I128) -> I128:
        return a % b

    def div_u128(self, env: Env, a: U128, b: U128) -> U128:
        return a // b
