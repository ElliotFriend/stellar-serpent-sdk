"""The ambient invocation frame: one contextvar, and nothing else.

`Address.require_auth()` takes no `Env` (verified: `types/address.py`, and it is
the shape spec Sec.2's own example uses -- `from_.require_auth()`), so a
tier-1 authorization has to find the env it is authorizing against some other
way. Ruling E7(iii) chose the standard Python answer for ambient per-frame
context: a `contextvars.ContextVar` that the deploy/frame helpers set while an
invocation frame is open. `Env` reads it to refuse a call from outside a frame;
`Address` reads it to know where to record.

**Why this is its own module (review B4).** `serpent.env` imports
`serpent.types`, which imports `types.address` -- so `address.py` cannot import
`env.py`, and `env.py` cannot hold state that `address.py` needs. The verified
cycle is `env -> types -> address -> env`. This module is the leaf that breaks
it: it imports `contextvars` and NOTHING from serpent, so both ends can import
it with no cycle at all. That is also why the contextvar is typed `object`
rather than `Env` -- naming `Env` here, even under `TYPE_CHECKING`, would put
the import back. `serpent.env` is the only writer, so the only thing this var
can ever hold is an `Env`, and `env.py`/`address.py` say so where they read it.

**Why a contextvar and not a module global.** A global would be shared across
threads and across asyncio tasks, so two test contexts running concurrently
would see each other's frame -- and because mock-all-auths means a
`require_auth` against the wrong env SUCCEEDS (recording is the whole model),
that failure mode is a green test, not a crash. A `ContextVar` is per-context
by construction, which is the cheapest way to not have to think about it.

The entry/exit pair is deliberately token-based rather than a set/clear pair:
`reset(token)` restores the PREVIOUS value, so a nested frame on the same env
(a contract calling its own method) leaves the outer frame standing when the
inner one exits, and a frame that raises restores exactly what was there
before. `serpent.env.Env.frame` is where the `try/finally` around it lives --
dossier F.1.7 is the test that a raising frame leaves nothing stale.
"""

from __future__ import annotations

import contextvars

#: The `Env` whose invocation frame is currently active, or `None` when no frame
#: is open. Written only by `serpent.env`; read by `serpent.env` and
#: `serpent.types.address`.
_CURRENT: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "serpent_invocation_frame", default=None
)


def current() -> object | None:
    """The `Env` whose frame is active, or `None`.

    Typed `object` for the leaf-module reason in the module docstring; every
    caller narrows it to `Env`, which is sound because `enter` is only ever
    called from `Env.frame`.
    """
    return _CURRENT.get()


def enter(env: object) -> contextvars.Token[object | None]:
    """Make `env` the ambient frame; return the token that undoes it."""
    return _CURRENT.set(env)


def leave(token: contextvars.Token[object | None]) -> None:
    """Restore whatever was ambient before the matching `enter`."""
    _CURRENT.reset(token)
