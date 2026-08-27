"""A deliberately simple serpent contract to play with and expand.

Compile it (from the repo root):

    uv run python sandbox/compile.py sandbox/counter.py

Then break it on purpose and compile again -- the diagnostics are the fun
part. Some things to try are listed in sandbox/README.md.
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
