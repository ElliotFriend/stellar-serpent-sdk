"""serpent: write Soroban smart contracts in Python (experimental).

This is the whole authoring surface. A contract imports everything it needs
from here -- `from serpent import U32, contract, Env, ...` -- never from the
submodules (`serpent.types`, `serpent.decorators`, `serpent.env`,
`serpent.errors`), which are implementation seams, not documented API.
`tests/unit/test_public_api.py` pins the exact `__all__` below (ordered by
ruff's `RUF022` convention: all-caps names, then CapWord names, then
lowercase/dunder names, alphabetically within each group);
`tests/fixtures/token_style.py` and `tests/semantics/cases.py` are both
proof-by-use that this list is sufficient to author and reason about a real
contract.

**`ChainValue` stays homed in `serpent.env`** (where Task 9 introduced it,
next to the `Env`/storage surface that is its main consumer) and is simply
re-exported here rather than moved into `serpent.types`. Moving it would ripple
through every existing `from serpent.env import ChainValue` in the test suite
for a purely cosmetic gain; re-exporting keeps one canonical definition and
still gives contract authors the single-import root they expect.

**Not exported (by design):**
* `U256`/`I256` -- deferred to M2 (amended spec).
* `BytesN` -- never the name; the fixed-length family is `Bytes32`, `Bytes64`,
  and the `bytes_n(n)` factory for other lengths.
"""

from serpent.decorators import (
    contract,
    contracterror,
    contractevent,
    contracttype,
    errorcode,
)
from serpent.env import ChainValue, Env, Event
from serpent.errors import (
    AbiCheckFailed,
    ArithmeticOverflow,
    BadArgument,
    ContractError,
    MissingValue,
)
from serpent.types import (
    I32,
    I64,
    I128,
    U32,
    U64,
    U128,
    Address,
    Bool,
    Bytes,
    Bytes32,
    Bytes64,
    Duration,
    Map,
    String,
    Symbol,
    Timepoint,
    Vec,
    bytes_n,
)

__version__ = "0.0.1"

__all__ = [
    "I32",
    "I64",
    "I128",
    "U32",
    "U64",
    "U128",
    "AbiCheckFailed",
    "Address",
    "ArithmeticOverflow",
    "BadArgument",
    "Bool",
    "Bytes",
    "Bytes32",
    "Bytes64",
    "ChainValue",
    "ContractError",
    "Duration",
    "Env",
    "Event",
    "Map",
    "MissingValue",
    "String",
    "Symbol",
    "Timepoint",
    "Vec",
    "__version__",
    "bytes_n",
    "contract",
    "contracterror",
    "contractevent",
    "contracttype",
    "errorcode",
]
