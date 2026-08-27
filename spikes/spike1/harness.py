"""Spike 1: a mini Soroban host, just big enough to run ``spike.wasm`` locally.

This implements **only the eight host functions the module imports** — no
budget, no ledger, no footprint, no auth. The question it answers is narrow and
worth answering before spending a testnet deploy on it: *do the bytes the
emitter produced actually compute what the Python source said?*

What it is faithful about, because these are the things a wrong emitter gets
wrong:

* the ``Val`` tag encoding, shared with ``emitter.py`` rather than reimplemented;
* ``map_new_from_linear_memory``'s asymmetric arrays — keys are
  ``(u32 ptr, u32 len)`` descriptors, values are 8-byte ``Val`` words — read
  out of the module's exported ``memory``;
* the ascending-key invariant the host enforces (and panics on);
* ``fail_with_error`` aborting with the contract's error code intact.

What it is *not*: a semantics oracle. Storage is a `dict` that outlives
nothing, object handles are list indices, and there is no metering. A green run
here means "the codegen is self-consistent", not "this contract is correct on
chain" — that is what Task 6's testnet deploy is for.

Signedness
----------
wasmtime speaks **signed** i64; Soroban ``Val``s are **unsigned** 64-bit words.
Every crossing converts: :func:`mask` on the way in from wasm, :func:`to_wasm`
on the way back. The original throwaway emulator got this wrong in exactly one
place and produced plausible-looking nonsense for any ``Val`` with the high bit
set — which includes every ``Error`` ``Val`` with a large code, e.g. the
``0xFFFFFFFF`` the ABI prologue raises. Hence the conversion is a named
function applied at every boundary rather than an inline ``& mask`` sprinkled
where it seemed necessary.

Feature set
-----------
The wasmtime ``Config`` mirrors what the chain accepts. **The full feature-set
assertion test is deferred to M1 tier-2a** — this spike sets the flags it knows
about, it does not prove the set is exhaustive or that wasmtime's defaults
match the Soroban host's for every future proposal.
"""

from __future__ import annotations

import pathlib
import struct
from collections.abc import Callable

import wasmtime
from emitter import (
    HOST_FN_NAMES,
    TAG_ERROR,
    TAG_FALSE,
    TAG_SYMBOL,
    TAG_TRUE,
    TAG_U32,
    VOID_VAL,
    as_i64,
    load_host_fns,
    pack_u32val,
    symbol_small_text,
)

# soroban-env-common `Tag` discriminants (v28.0.2), for the object kinds this
# host hands back. Verified against the enum, not guessed.
TAG_STRING_OBJECT = 73
TAG_SYMBOL_OBJECT = 74
TAG_MAP_OBJECT = 76
OBJECT_CODE_LOWER_BOUND = 63
OBJECT_CODE_UPPER_BOUND = 79

U64_MASK = 0xFFFF_FFFF_FFFF_FFFF

SPIKE_DIR = pathlib.Path(__file__).parent
DEFAULT_ENV_JSON = SPIKE_DIR / "env.json"


def mask(v: int) -> int:
    """A ``Val`` arriving from wasm: wasmtime hands i64 back as a *signed* int."""
    return v & U64_MASK


def to_wasm(v: int) -> int:
    """A ``Val`` going into wasm: wasmtime wants the signed i64 spelling.

    Same conversion :func:`emitter.as_i64` applies to ``i64.const`` operands —
    reused rather than rewritten, so the two directions cannot drift.
    """
    return as_i64(mask(v))


class ContractError(Exception):
    """Raised by ``fail_with_error``: the contract aborted with its own code.

    ``fail_with_error`` "does not actually return" (env.json), so a Python
    exception is the honest model of it. It carries the **masked** error
    ``Val``, which is what a client would see.
    """

    def __init__(self, val: int) -> None:
        val = mask(val)
        super().__init__(f"contract error code {val >> 32} (Val {val:#x})")
        self.val = val
        self.code = val >> 32


class HostError(Exception):
    """The contract violated a host invariant — the mini-host's own panics."""


class SpikeHost:
    """One instantiation of ``spike.wasm`` with a fresh store and empty storage."""

    def __init__(self, wasm_path: str, env_json: str | pathlib.Path | None = None) -> None:
        self._engine = wasmtime.Engine(self._config())
        module = wasmtime.Module.from_file(self._engine, wasm_path)
        self._store = wasmtime.Store(self._engine)

        # Object handles: index into this list, tagged by kind.
        self._objects: list[object] = []
        # Ledger stand-in, keyed by (masked key Val, storage type).
        self.storage: dict[tuple[int, int], int] = {}

        # Bind by *name* out of the same pinned env.json the emitter compiled
        # against, so a re-pin that moves a code cannot silently mis-wire the
        # harness into testing the wrong import.
        self._host_fns = load_host_fns(
            DEFAULT_ENV_JSON if env_json is None else pathlib.Path(env_json), HOST_FN_NAMES
        )

        linker = wasmtime.Linker(self._engine)
        for name, impl in self._implementations().items():
            self._bind(linker, name, impl)

        self._instance = linker.instantiate(self._store, module)
        exports = self._instance.exports(self._store)
        memory = exports["memory"]
        assert isinstance(memory, wasmtime.Memory)
        self._memory = memory

    # ---------------------------------------------------------------- wiring

    @staticmethod
    def _config() -> wasmtime.Config:
        """Mirror the chain's accepted feature set (see the module docstring)."""
        config = wasmtime.Config()
        # relaxed-simd must go first: wasmtime refuses "simd off, relaxed-simd
        # on" and enforces it by **aborting the process**, not by raising, so
        # the ordering here is load-bearing and the crash it prevents is one
        # you cannot catch.
        config.wasm_relaxed_simd = False
        config.wasm_simd = False
        config.wasm_multi_value = False
        config.wasm_reference_types = False
        config.wasm_tail_call = False
        config.wasm_threads = False
        # Enabled, matching the emitter's `wasm-tools validate --features=` line.
        config.wasm_bulk_memory = True
        return config

    def _implementations(self) -> dict[str, Callable[..., int]]:
        return {
            "put_contract_data": self._put_contract_data,
            "has_contract_data": self._has_contract_data,
            "get_contract_data": self._get_contract_data,
            "map_new_from_linear_memory": self._map_new_from_linear_memory,
            "map_get": self._map_get,
            "symbol_new_from_linear_memory": self._symbol_new_from_linear_memory,
            "string_new_from_linear_memory": self._string_new_from_linear_memory,
            "fail_with_error": self._fail_with_error,
        }

    def _bind(self, linker: wasmtime.Linker, name: str, impl: Callable[..., int]) -> None:
        """Wrap one host function so masking happens on both sides, always."""
        host_fn = self._host_fns[name]
        func_type = wasmtime.FuncType(
            [wasmtime.ValType.i64()] * host_fn.nargs, [wasmtime.ValType.i64()]
        )

        def trampoline(*raw: int, _impl: Callable[..., int] = impl) -> int:
            return to_wasm(_impl(*(mask(v) for v in raw)))

        linker.define(
            self._store,
            host_fn.module,
            host_fn.field,
            wasmtime.Func(self._store, func_type, trampoline),
        )

    # ------------------------------------------------------------- Val codec

    def _unpack_u32val(self, val: int) -> int:
        if val & 0xFF != TAG_U32:
            raise HostError(f"expected a U32Val, got tag {val & 0xFF} ({val:#x})")
        return val >> 32

    def _new_object(self, tag: int, payload: object) -> int:
        self._objects.append(payload)
        return ((len(self._objects) - 1) << 32) | tag

    def _object(self, val: int, tag: int) -> object:
        if val & 0xFF != tag:
            raise HostError(f"expected object tag {tag}, got {val & 0xFF} ({val:#x})")
        index = val >> 32
        if index >= len(self._objects):
            raise HostError(f"dangling object handle {val:#x}")
        return self._objects[index]

    def _symbol_text(self, val: int) -> str:
        """A Symbol key, whether it arrived small or as a handle.

        The host compares symbols by their characters regardless of which
        representation they came in as; so does this.
        """
        if val & 0xFF == TAG_SYMBOL:
            return symbol_small_text(val)
        text = self._object(val, TAG_SYMBOL_OBJECT)
        assert isinstance(text, str)
        return text

    def _read(self, ptr: int, length: int) -> bytes:
        return bytes(self._memory.read(self._store, ptr, ptr + length))

    # -------------------------------------------------------- host functions

    def _put_contract_data(self, key: int, val: int, storage_type: int) -> int:
        self.storage[(key, storage_type)] = val
        return VOID_VAL

    def _has_contract_data(self, key: int, storage_type: int) -> int:
        return TAG_TRUE if (key, storage_type) in self.storage else TAG_FALSE

    def _get_contract_data(self, key: int, storage_type: int) -> int:
        try:
            return self.storage[(key, storage_type)]
        except KeyError:
            raise HostError(
                f"no entry for key {key:#x} in storage type {storage_type}"
            ) from None

    def _map_new_from_linear_memory(self, keys_pos: int, vals_pos: int, length: int) -> int:
        """The asymmetric one. Keys are descriptors; values are ``Val`` words.

        env.json: "Key strings are specified as `len` 8 byte slices consisting
        of the 4 byte pointer and 4 byte length. Actual keys must be byte
        strings sorted in ascending order... Panics if any of the invariants
        above are violated." The sort check is enforced here, because an
        emitter that stopped sorting would otherwise pass this harness and fail
        only on chain.
        """
        keys_ptr = self._unpack_u32val(keys_pos)
        vals_ptr = self._unpack_u32val(vals_pos)
        count = self._unpack_u32val(length)

        descriptors = self._read(keys_ptr, 8 * count)
        values = self._read(vals_ptr, 8 * count)

        entries: dict[str, int] = {}
        previous = b""
        for i in range(count):
            ptr, size = struct.unpack_from("<II", descriptors, 8 * i)
            name = self._read(ptr, size)
            if name <= previous:
                raise HostError(
                    f"map keys are not in ascending order: {name!r} follows {previous!r}"
                )
            previous = name
            entries[name.decode()] = int.from_bytes(values[8 * i : 8 * i + 8], "little")
        return self._new_object(TAG_MAP_OBJECT, entries)

    def _map_get(self, map_val: int, key: int) -> int:
        entries = self._object(map_val, TAG_MAP_OBJECT)
        assert isinstance(entries, dict)
        name = self._symbol_text(key)
        if name not in entries:
            raise HostError(f"map_get: no key {name!r} (have {sorted(entries)})")
        value = entries[name]
        assert isinstance(value, int)
        return value

    def _symbol_new_from_linear_memory(self, pos: int, length: int) -> int:
        text = self._read(self._unpack_u32val(pos), self._unpack_u32val(length)).decode()
        return self._new_object(TAG_SYMBOL_OBJECT, text)

    def _string_new_from_linear_memory(self, pos: int, length: int) -> int:
        text = self._read(self._unpack_u32val(pos), self._unpack_u32val(length)).decode()
        return self._new_object(TAG_STRING_OBJECT, text)

    def _fail_with_error(self, error: int) -> int:
        if error & 0xFF != TAG_ERROR:
            raise HostError(f"fail_with_error needs an Error Val, got tag {error & 0xFF}")
        # env.json: the error "must be of error-type `ScErrorType::Contract`",
        # which is the type field (bits 8..32) being zero.
        if (error >> 8) & 0xFF_FFFF != 0:
            raise HostError(f"fail_with_error needs a Contract-type error, got {error:#x}")
        raise ContractError(error)

    # -------------------------------------------------------------- invoking

    def u32(self, x: int) -> int:
        """A raw u32 as the ``U32Val`` the contract's ABI expects."""
        return pack_u32val(x)

    def object_payload(self, val: int) -> object:
        """What an object handle points at — for inspecting what a contract stored.

        Not a host function; the contract can never call this. It exists so a
        test can ask "what Map actually landed in instance storage?" instead of
        inferring it from behaviour alone.
        """
        tag = val & 0xFF
        if not OBJECT_CODE_LOWER_BOUND < tag < OBJECT_CODE_UPPER_BOUND:
            raise HostError(f"{val:#x} is not an object handle (tag {tag})")
        index = val >> 32
        if index >= len(self._objects):
            raise HostError(f"dangling object handle {val:#x}")
        return self._objects[index]

    def invoke(self, name: str, args: list[int]) -> int:
        """Call an exported contract function; returns its raw (masked) ``Val``."""
        export = self._instance.exports(self._store)[name]
        assert isinstance(export, wasmtime.Func)
        return mask(export(self._store, *(to_wasm(a) for a in args)))

    def invoke_expect_error(self, name: str, args: list[int]) -> int:
        """Call an export expecting it to abort; returns the masked error ``Val``."""
        try:
            result = self.invoke(name, args)
        except ContractError as exc:
            return exc.val
        raise AssertionError(f"{name} returned {result:#x}; expected a contract error")
