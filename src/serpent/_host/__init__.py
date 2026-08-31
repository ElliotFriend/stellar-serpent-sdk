"""The pinned Soroban host interface: `HOST_FUNCTIONS`, looked up by name.

Re-exports only -- no logic is defined here. `HostFn` and the exhaustive
type tables live in `_model.py`; the 199 pinned host function entries are
generated into `bindings.py` from `env.json` by `_codegen.py`; the computed
protocol floor and gate checks live in `_protocol.py`.
"""

from serpent._host._model import (
    ENV_TYPE_TO_WASM_TYPE,
    RAW_SCALAR_TYPES,
    HostFn,
    index_functions_by_name,
)
from serpent._host._protocol import (
    BASE_PROTOCOL,
    CONSTRUCTOR_MIN_PROTOCOL,
    DEFAULT_TARGET_PROTOCOL,
    ProtocolGateError,
    check_protocol_target,
    compute_protocol_floor,
    declared_protocol,
)
from serpent._host._scalars import CONTRACT_TTL_EXTENSION, STORAGE_TYPE
from serpent._host.bindings import HOST_FUNCTIONS

functions_by_name: dict[str, HostFn] = index_functions_by_name(HOST_FUNCTIONS)

__all__ = [
    "BASE_PROTOCOL",
    "CONSTRUCTOR_MIN_PROTOCOL",
    "CONTRACT_TTL_EXTENSION",
    "DEFAULT_TARGET_PROTOCOL",
    "ENV_TYPE_TO_WASM_TYPE",
    "HOST_FUNCTIONS",
    "RAW_SCALAR_TYPES",
    "STORAGE_TYPE",
    "HostFn",
    "ProtocolGateError",
    "check_protocol_target",
    "compute_protocol_floor",
    "declared_protocol",
    "functions_by_name",
]
