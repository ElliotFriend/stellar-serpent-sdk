"""Repo-wide pytest policy: the `real_host` marker (ruling U2, dossier D.2).

Tests marked `real_host` need the `serpent_host` extension, which a Rust-less
checkout does not have. They SKIP -- loudly, counted in the summary, with the
rebuild command in the reason -- unless `SERPENT_REQUIRE_REAL_HOST=1`, in which
case a missing extension fails the whole SESSION (CI's Rust job sets it, so the
real-host suite can never pass vacuously: dossier F.1.6, the D4
skip-never-silently-passed convention).

**Why the required-mode guard is session-level and not per-item.** The obvious
shape -- mark every collected `real_host` item as failed -- has a hole that was
measured, not theorised: a module that calls `pytest.importorskip("serpent_host")`
at module scope (`tests/real_host/test_serpent_host_module.py` does) contributes
ZERO items on a Rust-less checkout, because the skip happens during COLLECTION,
before any item exists to mark. Running that file alone under
`SERPENT_REQUIRE_REAL_HOST=1` reported `1 skipped` and exit code 5 -- the exact
vacuous pass the switch exists to prevent. Nothing an item-level hook can see
would catch it, so the guard runs at `pytest_sessionstart`, before collection can
hide anything: with the switch on and the extension missing, the session cannot
prove ANYTHING about the real host, and the honest outcome is to refuse to run.

That also means there is deliberately no per-item `pytest.fail` here. It would
be unreachable -- the session exits first -- and an unreachable second mechanism
reads as a belt-and-braces guarantee it does not provide.
"""

from __future__ import annotations

import os

import pytest

from serpent.testing._marker import (
    REAL_HOST_MARKER,
    REQUIRE_ENV_VAR,
    is_available,
    unavailable_reason,
)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Refuse the whole session when the real host was REQUIRED but is absent.

    `pytest.exit(..., returncode=1)` rather than `pytest.UsageError`: this is
    not a malformed command line (exit 4), it is a run whose premise does not
    hold, and exit 1 is what CI already reads as "this did not pass".
    """
    if os.environ.get(REQUIRE_ENV_VAR) == "1" and not is_available():
        pytest.exit(
            f"{REQUIRE_ENV_VAR}=1 but serpent_host is not importable, so no "
            f"real-host test in this session could prove anything. "
            f"{unavailable_reason()}",
            returncode=1,
        )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip the marked items when the extension is simply not there.

    Only reached in the UNREQUIRED mode: `pytest_sessionstart` has already
    exited the session in the required one.
    """
    if is_available():
        return
    skip = pytest.mark.skip(reason=unavailable_reason())
    for item in items:
        if item.get_closest_marker(REAL_HOST_MARKER) is not None:
            item.add_marker(skip)
