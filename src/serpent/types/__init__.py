"""serpent's chain value types.

Each type mirrors one host `ScVal` case and defines its on-chain semantics
exactly (see `numeric.py` for the checked-arithmetic and comparison contracts,
and `buffers.py` for the payload types). Re-exported here so contracts import
them from one place.

`ContractUnion`/`ContractEnum` and their case factories `variant()`/
`enumvalue()` (`_udt.py`) belong here for the same reason: a tagged union IS an
`ScVec` on chain and an int enum IS a bare `U32`, so both are chain values
built out of the types beside them, not a separate authoring layer. The two
DECORATORS that declare such a type live in `serpent.decorators` with the other
declaration forms.
"""

from serpent.types._udt import (
    ContractEnum,
    ContractUnion,
    enumvalue,
    variant,
)
from serpent.types.address import Address
from serpent.types.buffers import (
    Bytes,
    Bytes32,
    Bytes64,
    String,
    bytes_n,
)
from serpent.types.containers import Map, Vec
from serpent.types.numeric import (
    I32,
    I64,
    I128,
    U32,
    U64,
    U128,
    Bool,
    Duration,
    Timepoint,
)
from serpent.types.symbol import Symbol

__all__ = [
    "I32",
    "I64",
    "I128",
    "U32",
    "U64",
    "U128",
    "Address",
    "Bool",
    "Bytes",
    "Bytes32",
    "Bytes64",
    "ContractEnum",
    "ContractUnion",
    "Duration",
    "Map",
    "String",
    "Symbol",
    "Timepoint",
    "Vec",
    "bytes_n",
    "enumvalue",
    "variant",
]
