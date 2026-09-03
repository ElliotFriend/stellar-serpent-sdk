"""The `real_host` marker's own vocabulary: the name, the switch, the reason.

Separate from `_real.py` on purpose. `tests/conftest.py` imports THIS module to
decide every test's fate, and it has to answer on a Rust-less checkout, so
nothing here may touch `serpent_host` at import time. (Only `serpent_host`:
the package `__init__` reaches `stellar_sdk` through `_errors`, so claiming
this module keeps that import out too was never true of the import graph.)
`is_available()` asks the import system, never `import serpent_host`: a test
that hides the extension by setting `sys.modules["serpent_host"] = None` (the
shape a Rust-less checkout has from inside Python) must get the same "no" a
genuinely missing extension gives, and `importlib.util.find_spec` answers `None`
for exactly that case while a bare `import` would raise.
"""

from __future__ import annotations

import importlib.util

#: The pytest marker every real-host test carries. Registered in
#: `pyproject.toml`'s `[tool.pytest.ini_options] markers`, so a typo is an error
#: rather than a silently unmarked test.
REAL_HOST_MARKER = "real_host"

#: How to get the extension, BYTE-IDENTICAL to the command in `host/README.md`
#: (ruling U1/E5) -- `test_real_env.py` asserts that, because two spellings of
#: one build command is how a skip reason comes to be subtly wrong. `$PWD`
#: rather than a path computed from this file: the command is meant to be pasted
#: into a shell at the repository root, and a derived path would be wrong the
#: moment `serpent` is installed as a wheel.
REBUILD_COMMAND = (
    "VIRTUAL_ENV=$PWD/.venv uvx maturin develop --release --manifest-path host/Cargo.toml"
)

#: Set to "1" to turn a missing extension from a skip into a failure. CI's Rust
#: job sets it so the real-host tier can never pass vacuously (ruling U2,
#: dossier F.1.6).
REQUIRE_ENV_VAR = "SERPENT_REQUIRE_REAL_HOST"

#: The extension module's import name, for `find_spec`. `_real._require_host`
#: cannot share it -- an `import` statement takes a literal name, not a string
#: -- so the two spellings are checked against each other by the skip-policy
#: test, which hides the extension under exactly this name.
EXTENSION_MODULE = "serpent_host"


def is_available() -> bool:
    """Whether `serpent_host` can be imported, without importing it."""
    return importlib.util.find_spec(EXTENSION_MODULE) is not None


def unavailable_reason() -> str:
    """The skip/failure reason, which has to be ACTIONABLE (ruling U2).

    A skip nobody can act on is the D4 failure mode -- a suite that reports
    success while proving nothing -- so the reason carries the exact build
    command and the file that explains why the extension is built from source.
    """
    return (
        "the serpent_host extension is not built in this environment, so the "
        f"real-host tier cannot run. Build it with `{REBUILD_COMMAND}` (see "
        f"host/README.md), or set {REQUIRE_ENV_VAR}=1 to make this a failure "
        "instead of a skip."
    )
