"""The smallest contract that can have a state bug: a counter with a ceiling.

Start here. Two entry points over one persistent storage key, and every piece of
serpent's authoring surface that a contract cannot avoid:

* `@contract` on the class -- the methods of the decorated class are the
  contract's entry points, and each one takes `self` first and `env: Env`
  second;
* chain types on every parameter and every return (`U32`, never a bare `int`) --
  the compiler rejects an unannotated parameter and a bare literal, because the
  on-chain value model has no `int`;
* `@contracterror` with `errorcode(N)` members, raised with
  `raise Error.MaxReached` -- the code is what a client sees on chain;
* `env.storage().persistent()` with an explicit type on `get`, and `default=` for
  the "nothing stored yet" case. Without a `default` a missing key is a trap
  carrying a reserved runtime code, which is a different contract.

Compile it to wasm (the script prints the module's size, exports and imports):

    uv run python sandbox/compile.py examples/counter.py

Run it two ways -- `tests/unit/test_examples.py` does both and asserts the two
agree:

    from serpent.env import Env, deploy

    env = Env()
    counter = deploy(Counter, env)
    with env.frame():
        counter.increment(env, U32(5))   # -> U32(5)

`deploy` and `Env(...)` are TEST-facing (`serpent.env`, deliberately not
importable from a contract): tier 1 runs the contract as ordinary Python with an
in-memory model of the host. It is a fast authoring loop, not a chain -- the
model is hand-written, and only the real host is evidence.

This file is byte-identical, below the docstring, to `sandbox/counter.py` and to
`tests/fixtures/sandbox_counter.py`; `tests/unit/test_emitter_end_to_end.py`
compares the BUILDS to keep the three from drifting.
"""

from serpent import U32, Env, Symbol, contract, contracterror, errorcode


@contracterror
class Error:
    MaxReached = errorcode(1)


@contract
class Counter:
    def increment(self, env: Env, step: U32) -> U32:
        """Add `step` to the running total; refuse to pass 1000."""
        total = env.storage().persistent().get(Symbol("TOTAL"), U32, default=U32(0))
        total = total + step
        if total > U32(1000):
            raise Error.MaxReached
        env.storage().persistent().set(Symbol("TOTAL"), total)
        return total

    def total(self, env: Env) -> U32:
        """Read the running total without changing it."""
        return env.storage().persistent().get(Symbol("TOTAL"), U32, default=U32(0))
