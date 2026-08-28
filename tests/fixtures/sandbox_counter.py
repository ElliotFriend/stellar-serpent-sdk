"""`sandbox/counter.py`, promoted into the test suite (F.2.8).

`sandbox/` is the author-facing scratch directory: it is explicitly there to be
broken on purpose (its own README invites it), it is NOT under the repo's
`mypy --strict` gate (`pyproject.toml`'s `files = ["src", "tests"]`), and this
task must not touch it. A test that built `sandbox/counter.py` directly would
therefore go red the first time somebody played with the sandbox, which is the
one thing the sandbox is for.

So the contract is COPIED here, reviewed, and it is this copy that
`tests/unit/test_emitter_end_to_end.py` builds and invokes. The copy is
byte-for-byte the sandbox source except for:

* the module docstring (this one -- the sandbox's own text is instructions for
  a human playing with `sandbox/compile.py`, which is meaningless here);

and nothing else: the same `Error.MaxReached = errorcode(1)`, the same
`increment(step)`/`total()` pair, the same 1000 ceiling, the same `TOTAL`
persistent key.

**The anti-drift check is a BYTE COMPARE OF THE BUILDS**, not of the text:
`tests/unit/test_emitter_end_to_end.py` builds both this file and
`sandbox/counter.py` and asserts the two modules are byte-identical. That is
the assertion worth making -- a module docstring, a blank line, or a reflowed
comment cannot change the emitted wasm, while any change to the CONTRACT can --
and it doubles as one more witness for Task 11's determinism claim (the same
contract text through two different `path` arguments is the same module).


Not collected by pytest directly (no `test_*` functions); read as text and
built by `tests/unit/test_emitter_end_to_end.py`, and -- being under `tests/`
-- covered by the repo-wide `uv run mypy --strict src tests` gate, which is
what the sandbox original never was.
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
