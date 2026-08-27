"""The `HostFn` model and the exhaustive env-type -> wasm-type table.

Kept separate from `bindings.py` (the generated `HOST_FUNCTIONS` tuple) so
the codegen script, `bindings.py`, and `__init__.py` never form an import
cycle: `bindings.py` imports only stdlib and this module; `__init__.py`
imports both this module and `bindings.py`; this module imports neither.
"""

from dataclasses import dataclass

#: Argument/return types passed as a bare host-native scalar rather than as
#: an encoded `Val`. A SUPERSET of what v28.0.2 actually uses: `u32`/`i32`
#: are declared interface types with zero occurrences in this pin -- tests
#: assert membership, never occurrence.
RAW_SCALAR_TYPES: frozenset[str] = frozenset(
    {"u64", "i64", "u32", "i32", "StorageType", "ContractTtlExtension"}
)

#: Every arg/return type name that appears anywhere in the pinned env.json
#: (v28.0.2), mapped to the wasm type its ABI carries. Every entry is "i64"
#: at this pin: `Val` itself, its `*Object`/`*Val` handle forms, `Error`,
#: `Symbol`, `Bool`, `Void`, and the raw scalars are all passed and returned
#: as a single 64-bit wasm value -- `test_wasm_types_uniform_i64_at_this_pin`
#: promotes that from an assumption to an asserted invariant. The key set is
#: EXHAUSTIVE: codegen (`_codegen.py`) and a test both assert it equals the
#: type vocabulary actually observed in env.json, so an unrecognized type at
#: re-pin time is a hard failure naming it, not a silent `KeyError` deep
#: inside a contract build.
ENV_TYPE_TO_WASM_TYPE: dict[str, str] = {
    "AddressObject": "i64",
    "Bool": "i64",
    "BytesObject": "i64",
    "ContractTtlExtension": "i64",
    "DurationObject": "i64",
    "Error": "i64",
    "ExecutableTagObject": "i64",
    "I128Object": "i64",
    "I256Object": "i64",
    "I256Val": "i64",
    "I64Object": "i64",
    "MapObject": "i64",
    "MuxedAddressObject": "i64",
    "StorageType": "i64",
    "StringObject": "i64",
    "Symbol": "i64",
    "SymbolObject": "i64",
    "TimepointObject": "i64",
    "U128Object": "i64",
    "U256Object": "i64",
    "U256Val": "i64",
    "U32Val": "i64",
    "U64Object": "i64",
    "U64Val": "i64",
    "Val": "i64",
    "VecObject": "i64",
    "Void": "i64",
    "i64": "i64",
    "u64": "i64",
}


@dataclass(frozen=True)
class HostFn:
    """One host function entry from the pinned env.json.

    `name` through `docs` are emitted verbatim by codegen as declared
    fields. `val_typed_args`, `wasm_params`, and `wasm_result` are computed
    `@property`s -- never emitted -- derived from `arg_types`/`ret_type` via
    `RAW_SCALAR_TYPES` and `ENV_TYPE_TO_WASM_TYPE`.
    """

    name: str
    module: str
    export: str
    arity: int
    arg_names: tuple[str, ...]
    arg_types: tuple[str, ...]
    ret_type: str
    min_protocol: int | None
    max_protocol: int | None
    docs: str = ""

    @property
    def val_typed_args(self) -> tuple[bool, ...]:
        """Per-argument: `True` if passed as an encoded `Val`, `False` if a raw scalar."""
        return tuple(t not in RAW_SCALAR_TYPES for t in self.arg_types)

    @property
    def wasm_params(self) -> tuple[str, ...]:
        """The wasm import's parameter types, one per arg (exhaustive type table)."""
        return tuple(ENV_TYPE_TO_WASM_TYPE[t] for t in self.arg_types)

    @property
    def wasm_result(self) -> str:
        """The wasm import's result type (exhaustive type table)."""
        return ENV_TYPE_TO_WASM_TYPE[self.ret_type]


def index_functions_by_name(functions: tuple[HostFn, ...]) -> dict[str, HostFn]:
    """Build the by-name lookup `__init__.py` re-exports as `functions_by_name`.

    Bindings are looked up BY NAME everywhere else in serpent; export codes
    are data, never hardcoded.
    """
    return {fn.name: fn for fn in functions}
