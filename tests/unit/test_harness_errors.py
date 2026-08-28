"""`tests/harness/errors.py` (M-3): `HostError`/`HostTrap`, wasmtime-free.

The whole point of the move is that a caller who only needs the two abort
classes -- Task 2's tier-1 tests, say -- never drags `wasmtime` in to get them.
`engine.py` keeps importing and re-exporting both names so every existing
`from tests.harness.engine import HostError` (and `engine.HostError`) site
keeps working unchanged; this file is the regression net for the module split
itself, not a restatement of `engine.py`'s own `HostError`/`HostTrap` tests
(`test_harness_engine.py` already covers those behaviourally).
"""

import subprocess
import sys

from tests.harness.engine import HostError as EngineHostError
from tests.harness.engine import HostTrap as EngineHostTrap
from tests.harness.errors import HostError, HostTrap


def test_engine_reexports_the_same_classes() -> None:
    """`engine.HostError`/`HostTrap` ARE `tests.harness.errors`'s classes, not a
    second, drifting pair -- every existing `engine.HostError` site still
    means the same thing."""
    assert EngineHostError is HostError
    assert EngineHostTrap is HostTrap


def test_host_error_masks_and_carries_the_val() -> None:
    err = HostError(-1)
    assert err.val == 0xFFFF_FFFF_FFFF_FFFF


def test_host_trap_is_a_distinct_exception_class() -> None:
    assert issubclass(HostTrap, Exception)
    assert not issubclass(HostTrap, HostError)
    assert not issubclass(HostError, HostTrap)


def test_importing_tests_harness_errors_never_imports_wasmtime() -> None:
    """The load-bearing claim of M-3, checked in a fresh interpreter: importing
    the module must not put `wasmtime` into `sys.modules`, even though
    `wasmtime` itself is importable in this environment (so a missing
    dependency can't be mistaken for a clean split)."""
    probe = (
        "import sys;"
        "import tests.harness.errors;"
        "assert not any(m == 'wasmtime' or m.startswith('wasmtime.') for m in sys.modules), "
        "'tests.harness.errors pulled in wasmtime';"
        "import wasmtime;"  # prove wasmtime IS importable in this environment
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
