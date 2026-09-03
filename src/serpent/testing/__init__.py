"""serpent.testing: the real-host and tier-3 (testnet fixture) test surface.

Import this, not `serpent_host`, in a contract's tests. `RealEnv` mirrors the
tier-1 `Env`/`deploy` verbs wherever the semantics coincide, so a tier-1 test
re-points at the real host by swapping one fixture (dossier D.2).

Two rules hold for everything that lands here:

* it is `serpent[testing]`, not `serpent`. This subpackage imports
  `stellar_sdk` (and, lazily, a Rust extension), so it is the second recorded
  exemption from the zero-dep walk, alongside `serpent.spec`
  (`tests/unit/test_core_zero_dep.py`);
* `serpent/__init__.py` never imports it. `import serpent` must not be able to
  drag `stellar_sdk` in, which the same file's subprocess probe asserts. For
  the same reason `serpent.__all__` is NOT touched: this is a subpackage import
  like `serpent.spec`, and `tests/unit/test_public_api.py` stays as it is.

Importing this module does NOT import `serpent_host`. The extension is loaded
inside `RealEnv()` (`_real._require_host`), so `tests/conftest.py` can read the
`real_host` marker's policy on a Rust-less checkout (ruling U2).
"""

from serpent.testing._errors import (
    FrozenTableDisagreement,
    HostPanic,
    RealContractError,
    RealHostError,
    RealHostUnavailable,
)
from serpent.testing._marker import REAL_HOST_MARKER, REBUILD_COMMAND, is_available
from serpent.testing._real import DEFAULT_PROTOCOL, RealContract, RealEnv, RealStorage

__all__ = [
    "DEFAULT_PROTOCOL",
    "REAL_HOST_MARKER",
    "REBUILD_COMMAND",
    "FrozenTableDisagreement",
    "HostPanic",
    "RealContract",
    "RealContractError",
    "RealEnv",
    "RealHostError",
    "RealHostUnavailable",
    "RealStorage",
    "is_available",
]
