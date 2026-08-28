"""The harness's two abort classes (M-3), deliberately wasmtime-free.

`HostError`/`HostTrap` used to live in `engine.py`, which imports `wasmtime` at
module scope to build the pinned VM configuration. A caller that only needs to
`pytest.raises(HostError)` -- Task 2's tier-1 tests, for one, which never touch
wasm at all -- had no way to get that class without paying for a `wasmtime`
import along the way. Moving the two classes here breaks that coupling:
`tests.harness.errors` imports only `serpent.val` and the standard library, so
importing it never puts `wasmtime` into `sys.modules`
(`tests/unit/test_harness_errors.py` asserts this in a subprocess).

`engine.py` re-imports both names for compatibility, so every existing
`from tests.harness.engine import HostError` and `engine.HostError` site keeps
meaning exactly what it always meant -- this is a pure move, not a new pair of
classes.
"""

from serpent import val


class HostError(Exception):
    """The contract aborted through `fail_with_error`, carrying its `Val`.

    `fail_with_error` "does not actually return" (env.json), so a Python
    exception is the honest model of it. `val` is the **unsigned** error `Val`
    word, which is what a client would see.
    """

    def __init__(self, error_val: int) -> None:
        word = val.as_u64(error_val)
        super().__init__(f"contract aborted with Val {word:#018x}")
        self.val = word


class HostTrap(Exception):
    """The host TRAPPED: an env.json "Traps if ..." precondition was violated.

    A distinct class from `HostError` because the two are different on-chain
    outcomes and the semantics table pins them separately: a `HostError` is a
    contract that ABORTED with a `Val` a client can classify, while a trap has
    no error `Val` at all (an out-of-bounds `vec_get`, a `map_get` on a missing
    key). Task 13's differential run asserts the two kinds against different
    expectations, so a rig that raised one class for both could not tell a
    passing `kind="trap"` case from a passing `kind="contract_error"` one.

    Deliberately NOT an `AssertionError`: this rig keeps `AssertionError` for
    its own broken invariants -- a dangling handle, an object with the wrong
    tag, map keys the emitter failed to sort -- which mean the harness or the
    lowering is wrong, not that the contract did something the host forbids.
    """
