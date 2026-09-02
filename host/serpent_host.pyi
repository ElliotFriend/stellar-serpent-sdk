"""Type stub for the `serpent_host` extension module (review m8).

`serpent.testing._real` (Task 3) is typed against THIS file, via
`mypy_path = ["host"]`, so `mypy --strict` gives the same answer whether or not
the extension is built -- and no `# type: ignore` is needed on either side.
Keep it in step with `#[pymethods] impl RealEnv` in `src/lib.rs`: the Rust is
the implementation, this is the contract.

Every value crossing the boundary is ScVal XDR `bytes` (never a decoded
object): the Rust layer knows nothing about serpent's types.
"""

HOST_CRATE_VERSION: str

class HostFailure(Exception):
    """Every failure of every method below, in one shape.

    `args == (kind, error_type, code, message)`:

    * `kind` in `{"contract", "host", "panic", "invalid_input", "conversion"}`;
    * `error_type` is an `ScErrorType` variant name ("Contract", "WasmVm",
      "Context", "Storage", "Object", "Crypto", "Events", "Budget", "Value",
      "Auth") when `kind` is "contract" or "host", and `""` otherwise;
    * `code` is the u32 error code: a contract's own code, or the `ScErrorCode`
      discriminant. `0` when `kind` is neither "contract" nor "host".
    """

class RealEnv:
    """One soroban-sdk test `Env`. `#[pyclass(unsendable)]`: one env per thread
    (P9), so parallelism across real-host tests is process-level only."""

    def __init__(
        self,
        *,
        protocol_version: int,
        sequence_number: int,
        timestamp: int,
        network_id: bytes,
        base_reserve: int,
        min_temp_entry_ttl: int,
        min_persistent_entry_ttl: int,
        max_entry_ttl: int,
    ) -> None: ...
    def protocol_version(self) -> int:
        """`env.ledger().get().protocol_version` (not the deprecated trait method)."""

    def host_protocol_ceiling(self) -> int:
        """`soroban_env_host::Host::current_test_protocol()`: the compiled-in ceiling."""

    def diagnostics(self) -> list[bytes]:
        """`xdr.DiagnosticEvent` XDR for the host's diagnostic buffer (B5)."""

    def compare(self, a_xdr: bytes, b_xdr: bytes) -> int:
        """The host's `Compare<Val>` verdict (-1/0/1) for ANY two Vals, small or
        object -- NOT `obj_cmp`, which refuses two small operands (review M2)."""

    def max_ttl(self) -> int:
        """`env.storage().max_ttl()`; observed 6_311_999 at max_entry_ttl 6_312_000."""

    def set_ledger(
        self, *, sequence_number: int | None = None, timestamp: int | None = None
    ) -> None: ...
    def register(self, wasm: bytes, constructor_args_xdr: list[bytes]) -> str:
        """Upload + instantiate; returns the contract's `C...` strkey."""

    def invoke(self, contract: str, function: str, args_xdr: list[bytes]) -> bytes:
        """The result as ScVal XDR; every failure is a `HostFailure`."""

    def mock_all_auths(self) -> None: ...
    def mock_auths(self, entries: list[tuple[str, str, str, list[bytes]]]) -> None:
        """`(authorizer CONTRACT strkey, contract strkey, function, args ScVal XDR)`.

        REPLACES the whole entry set (sdk semantics, review M6); the authorizer
        MUST be a contract strkey (the sdk registers a MockAuthContract AT that
        address and panics for a `G...` account, review B2) and must never be
        the deployed contract's own address.
        """

    def events(self) -> list[bytes]:
        """`xdr.ContractEvent` XDR, the LAST invocation's."""

    def auths(self) -> list[tuple[str, str, str, list[bytes]]]:
        """`(address, contract, function, args ScVal XDR)` for the LAST
        invocation's root contract auths; non-contract functions (`register`'s
        CreateContractV2HostFn, review M8) are SKIPPED, never an error."""

    def storage_get(self, contract: str, durability: str, key_xdr: bytes) -> bytes | None:
        """`durability` in `{"persistent", "temporary", "instance"}`."""

    def storage_has(self, contract: str, durability: str, key_xdr: bytes) -> bool: ...
    def storage_set(
        self, contract: str, durability: str, key_xdr: bytes, value_xdr: bytes
    ) -> None: ...
    def storage_ttl(self, contract: str, durability: str, key_xdr: bytes) -> int | None:
        """RELATIVE: ledgers remaining EXCLUDING the current ledger
        (`testutils::storage`, review B10), so `live_until = sequence + ttl`.
        `None` when the entry is absent or expired (the sdk method panics
        there; contained and mapped). Durability "instance" takes NO key: pass
        `b""` or get `invalid_input`."""

    def budget(self) -> tuple[int, int]:
        """`(cpu instructions, memory bytes)` of the last invocation."""

    def resources(self) -> dict[str, int] | None:
        """Every `InvocationResources` field by its Rust name (exhaustive
        destructure, review M10); `None` before the first invocation (the sdk
        panics there, review m14)."""
