"""`sandbox/hello_world.py`, promoted into the test suite (F.2.8).

See `sandbox_counter.py`'s docstring for why the promotion exists at all
(`sandbox/` is a scratch directory that is meant to be broken, and is outside
the `mypy --strict` gate). This contract is the more interesting of the two
sandbox fixtures because of what it reaches that `counter.py` does not: a
MODULE-LEVEL helper called from two methods (`FuncKind.INTERNAL`, ruling E8),
an `__init__` compiled as `__constructor` (S6/B11), a `Symbol` comparison
routed through `obj_cmp` (T5 -- `Symbol`'s small form is an immediate but its
packed 6-bit codes order differently from raw ASCII, so the compare may not be
a bare `i64.eq`), and a `Vec[Symbol]` return, i.e. a host object built by the
guest and handed back across the ABI.

The copy differs from the sandbox source only in what the `tests/` gates
require of it:

* this module docstring is added (the sandbox file has none);
* `ruff format`'s blank-line and spacing conventions are applied (the sandbox
  file is hand-spaced -- one blank line between top-level definitions, no
  two-blank-line separation);
* the sandbox file's commented-out `# from .storage import
  set_greeting_salutation` line is dropped: it documents a refactor an author
  might try in the sandbox, and a relative import is not a thing a promoted
  test fixture should appear to be one edit away from.

Nothing about the CONTRACT changes -- same `Error.Unimaginative = errorcode(1)`,
same helper, same four entry points, same `GREETING` instance key, same
`Symbol("Hola")` default -- and
`tests/unit/test_emitter_end_to_end.py` proves that by building both files and
asserting the two modules are byte-identical (see `sandbox_counter.py`'s
docstring on why a build compare beats a text compare here).

Not collected by pytest directly (no `test_*` functions); read as text and
built by `tests/unit/test_emitter_end_to_end.py`.
"""

from serpent import Env, Symbol, Vec, contract, contracterror, errorcode


def set_greeting_salutation(env: Env, greeting: Symbol) -> Symbol:
    if greeting == Symbol("Hello"):
        raise Error.Unimaginative

    env.storage().instance().set(Symbol("GREETING"), greeting)
    return greeting


@contracterror
class Error:
    Unimaginative = errorcode(1)


@contract
class HelloWorld:
    def __init__(self, env: Env, greeting: Symbol) -> None:
        _ = set_greeting_salutation(env, greeting)

    def set_greeting(self, env: Env, greeting: Symbol) -> Symbol:
        return set_greeting_salutation(env, greeting)

    def get_greeting(self, env: Env) -> Symbol:
        return env.storage().instance().get(Symbol("GREETING"), Symbol)

    def hello(self, env: Env, name: Symbol) -> Vec[Symbol]:
        greeting = env.storage().instance().get(Symbol("GREETING"), Symbol, default=Symbol("Hola"))
        my_vec = Vec(Symbol, [greeting, name])

        return my_vec
