"""Tier-1 invocation-frame scaffolding for the unit suite.

Ruling E7(ii) closed a whole class of tier-1-only state: the tier-1 `Env`
refuses `storage()`/`events()`/`ledger()` and every operation on them unless an
INVOCATION FRAME is active, and a frame refuses to open until `deploy` has run
(`serpent.env.deploy`). On chain both are structural -- a deploy precedes every
invocation, and a host function is only callable from inside a frame -- so a
tier-1 run that reached either state would be asserting about something the
chain cannot produce (dossier risk 13).

The model's OWN unit tests (`test_env_model.py`, `test_env_ttl.py`) are not
invocations of a real contract: they poke the store and the TTL algebra
directly. They get their frame from `deployed_env()`, which deploys a
do-nothing contract and then opens a frame that stays open for the rest of the
test -- so those files keep reading as tests of the model rather than as
contract-invocation scripts. Tests that ARE about the framing (
`test_env_deploy.py`) enter `with env.frame():` explicitly instead, because
there the frame is the subject.

`_no_leaked_invocation_frame` closes what `deployed_env` opened and then
ASSERTS that the ambient env is really gone. That assertion is not bookkeeping:
it runs after every test in `tests/unit`, so a missing `try/finally` in the
model's own frame handling (dossier F.1.7 -- a raising frame that leaves a
stale ambient env) fails the suite at the test that caused it instead of
silently authorizing the next test's `require_auth` against the wrong `Env`.
"""

from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager

import pytest

from serpent import _frame
from serpent.env import DEFAULT_LEDGER_SEQUENCE, DEFAULT_LEDGER_TIMESTAMP, Env, deploy
from serpent.types import Address


class _Model:
    """The minimal deployable contract: no constructor, no exports.

    Deliberately NOT `@contract`-decorated: `deploy` models the host's deploy
    step, not the compiler's declaration checks, and it does not require the
    decorator (`deploy`'s own docstring says so). A test that needs the
    authoring form writes one.
    """

    __slots__ = ()


#: Frames opened by `deployed_env()` and not yet closed, innermost last.
#:
#: This list is what CLOSES them at teardown, and it is also what keeps them
#: open: `env.frame()` is a generator-based context manager, so a manually
#: entered one that nothing references is finalized by the garbage collector,
#: which throws `GeneratorExit` into it and runs its `finally` -- silently
#: closing the frame mid-test. Holding the reference is not bookkeeping.
_OPEN: list[AbstractContextManager[None]] = []


def deployed_env(
    *,
    timestamp: int = DEFAULT_LEDGER_TIMESTAMP,
    sequence: int = DEFAULT_LEDGER_SEQUENCE,
    auths: Iterable[Address] | None = None,
    frame: bool = True,
) -> Env:
    """An `Env` with a contract deployed and (by default) a frame open.

    The keyword arguments are `Env`'s own. `frame=False` deploys without
    entering a frame, for the tests that need to open frames themselves -- two
    envs cannot both have one open (there is no cross-contract call in M1), so
    a test using two `Env`s frames them one at a time.
    """
    env = Env(timestamp=timestamp, sequence=sequence, auths=auths)
    deploy(_Model, env)
    if frame:
        opened = env.frame()
        opened.__enter__()
        _OPEN.append(opened)
    return env


@pytest.fixture(autouse=True)
def _no_leaked_invocation_frame() -> Iterator[None]:
    """Close `deployed_env`'s frames, then assert no ambient env survives."""
    yield
    while _OPEN:
        _OPEN.pop().__exit__(None, None, None)
    assert _frame.current() is None, (
        "an invocation frame outlived the test: the ambient env is still set, "
        "so the next test's require_auth would authorize against the wrong Env "
        "(serpent.env's frame handling must clear it in a finally)"
    )
