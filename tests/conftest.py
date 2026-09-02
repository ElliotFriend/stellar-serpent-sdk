"""Repo-wide pytest policy: the `real_host` marker (ruling U2, dossier D.2).

Tests marked `real_host` need the `serpent_host` extension, which a Rust-less
checkout does not have. They SKIP -- loudly, counted in the summary, with the
rebuild command in the reason -- unless `SERPENT_REQUIRE_REAL_HOST=1`, in which
case a missing extension is a FAILURE (CI's Rust job sets it, so the real-host
suite can never pass vacuously: dossier F.1.6, the D4 skip-never-silently-passed
convention).
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

#: Set during collection when marked items exist, the extension does not, and
#: the operator asked for the required mode. Read by `pytest_runtest_setup`.
_REQUIRED_BUT_ABSENT = pytest.StashKey[bool]()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if is_available():
        return
    marked = [item for item in items if item.get_closest_marker(REAL_HOST_MARKER) is not None]
    if not marked:
        return
    if os.environ.get(REQUIRE_ENV_VAR) == "1":
        # Probed (review B4): xfail(run=False, strict=True) reports XFAIL [NOTRUN] with
        # exit code 0 -- NOT a failure. So the required-mode outcome is produced by
        # `pytest_runtest_setup` below, which fails each marked item for real.
        config.stash[_REQUIRED_BUT_ABSENT] = True
        return
    skip = pytest.mark.skip(reason=unavailable_reason())
    for item in marked:
        item.add_marker(skip)


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.config.stash.get(_REQUIRED_BUT_ABSENT, False) and item.get_closest_marker(
        REAL_HOST_MARKER
    ):
        pytest.fail(
            f"{REQUIRE_ENV_VAR}=1 but serpent_host is not importable. {unavailable_reason()}",
            pytrace=False,
        )
