"""The pinned wasmtime engine and a mini host, just big enough to run a module.

Ported **by copy** from `spikes/spike1/harness.py:143-158` (R5: `spikes/` is
read-only evidence, never imported from). Two things were deliberately NOT
copied: the spike's stale object-tag upper bound (a second, drifting
transcription of the tag table -- `Val` facts come from `serpent.val`, and the
number itself is deliberately not repeated here so a grep for it stays clean),
and the spike's `env.json` loader (F.1.17: bindings are looked up by name in
`serpent._host.functions_by_name`, the same pin the emitter compiles against, so
a re-pin that moves an export code cannot silently mis-wire the harness into
testing the wrong import).

Signedness
----------
wasmtime speaks **signed** i64; Soroban `Val`s are **unsigned** 64-bit words.
Every crossing converts, and it converts in exactly ONE place (P4): the
`_trampoline` wrapper every host callback goes through, plus `invoke`'s two
ends. The original throwaway emulator got this wrong in one spot and produced
plausible-looking nonsense for every `Val` with the high bit set -- which is
every `Error` `Val` with a large code. A per-callback `& mask` sprinkled where
it seemed necessary is precisely how that happens, so the mask is structural.

`HostError`/`HostTrap` (M-3)
----------------------------
The two abort classes live in `tests.harness.errors` now, wasmtime-free, so a
caller that needs only `pytest.raises(HostError)` never pays for a `wasmtime`
import. Imported here and re-exported for compatibility: every existing
`from tests.harness.engine import HostError` and `engine.HostError` site keeps
working unchanged.
"""

from collections.abc import Callable, Mapping

import wasmtime

from serpent import val
from serpent._host import functions_by_name

#: Explicit `as`-reexport (mypy `--strict`'s `--no-implicit-reexport`): every
#: `engine.HostError`/`from tests.harness.engine import HostError` site must
#: keep type-checking after the M-3 move.
from tests.harness.errors import HostError as HostError  # noqa: PLC0414 -- mypy reexport
from tests.harness.errors import HostTrap as HostTrap  # noqa: PLC0414 -- mypy reexport

#: The name a Soroban module exports its one memory under; `testmod` writes it.
#: Spelled here too rather than imported, so `engine` stays independent of the
#: module builder (a test may hand `MiniHost` bytes from anywhere).
MEMORY_EXPORT_NAME = "memory"


def make_config() -> wasmtime.Config:
    """The chain's accepted feature set, as wasmtime flags.

    Only flags that actually exist on wasmtime 48's `Config` are set (review
    B2). This matters more than it looks: `Config`'s feature properties are
    write-only setters, and assigning an attribute that does not exist on the
    class silently succeeds -- so a plausible-looking `config.wasm_sign_extension
    = True` would leave the feature at wasmtime's default while reading as a
    pin. `tests/unit/test_harness_engine.py` asserts the set of names assigned
    here EQUALS the expected list and that each is a real descriptor, and asserts
    the resulting feature set BEHAVIOURALLY (probe modules that must be rejected
    or accepted), because there is nothing to read back.

    **How complete this pin can be.** Floating point and the extended-const
    proposal have no wasmtime-48 Python toggle at all -- an f64 global and an
    `(i32.const 1) (i32.const 2) i32.add` global initializer both instantiate
    here and cannot be switched off. Those are the emitter's problem to never
    emit, not this config's to forbid; every proposal the Python `Config` DOES
    expose is pinned below, so this is as tight as the API allows.
    """
    config = wasmtime.Config()
    # relaxed-simd must go first: wasmtime refuses "simd off, relaxed-simd on"
    # and enforces it by **aborting the process**, not by raising, so the
    # ordering here is load-bearing and the crash it prevents is one you cannot
    # catch (P3).
    config.wasm_relaxed_simd = False
    config.wasm_simd = False
    config.wasm_multi_value = False
    config.wasm_reference_types = False
    config.wasm_tail_call = False
    config.wasm_threads = False
    # S13: `i64.mul_wide_s` is banned -- the chain's wasmi 0.31 does not have
    # the wide-arithmetic proposal, so a module using it would run here and
    # trap on chain, the one direction a harness must never be wrong in.
    config.wasm_wide_arithmetic = False
    # --- chain-fidelity pins -------------------------------------------------
    # None of these four are in the spike's frozen flag list, and every one of
    # them is ON by wasmtime 48's default -- the behavioural probes are what
    # found that, one module at a time. The chain's wasmi 0.31 accepts none of
    # them, so leaving any at its default would give the harness a strictly
    # laxer VM than the host: a module that runs green here and fails on chain,
    # the one direction a rig like this must never be wrong in.
    config.wasm_multi_memory = False  # Soroban allows exactly one memory
    config.wasm_memory64 = False  # S23 lists memory64 OFF by name
    config.wasm_exceptions = False  # `try_table` validates without this
    config.wasm_gc = False  # a struct type in the type section, likewise
    # Enabled, matching the emitter's `wasm-tools validate --features=` line.
    config.wasm_bulk_memory = True
    return config


def _valtype(name: str) -> wasmtime.ValType:
    """One wasm type name from the pin, as a wasmtime `ValType`.

    Every host function's params and result are `"i64"` at this pin (that is an
    asserted invariant of `serpent._host`, not an assumption here), so anything
    else means the pin grew a type the harness has never bound -- worth a loud
    failure rather than a silent i64.
    """
    if name != "i64":
        raise NotImplementedError(f"the harness binds i64 host functions only, not {name!r}")
    return wasmtime.ValType.i64()


def _trampoline(impl: Callable[..., int]) -> Callable[..., int]:
    """THE host-call boundary (P4): one wrapper, applied to every callback.

    Arguments arrive from wasmtime as signed i64 and are handed to `impl` as
    unsigned `Val` words; the return makes the same trip back. Structural rather
    than per-callback on purpose -- a mask that each implementation is
    responsible for applying is a mask that one implementation will forget.
    """

    def wrapped(*raw: int) -> int:
        return val.as_i64(impl(*(val.as_u64(v) for v in raw)))

    return wrapped


class MiniHost:
    """One instantiation of `wasm`, with a fresh engine and store.

    An `Engine`/`Store` per instance is deliberate: instances are cheap enough
    at this size, and sharing them would leak state between tests. Caching is
    settled at the compiled-BUILD layer, not here: `tests/harness/cache.py`
    memoises `build_file`'s output (the wasm bytes), never a `MiniHost` -- every
    test still gets its own fresh `Engine`/`Store`/instance, because a shared
    one would leak state between tests exactly as this docstring says.
    """

    def __init__(
        self,
        wasm: bytes,
        *,
        imports: Mapping[str, Callable[..., int]] | None = None,
    ) -> None:
        #: Every error `Val` the default `fail_with_error` saw, in order.
        self.errors: list[int] = []

        self._engine = wasmtime.Engine(make_config())
        self._store = wasmtime.Store(self._engine)
        module = wasmtime.Module(self._engine, wasm)

        bindings: dict[str, Callable[..., int]] = {"fail_with_error": self._fail_with_error}
        if imports is not None:
            bindings.update(imports)

        linker = wasmtime.Linker(self._engine)
        for name, impl in bindings.items():
            self._bind(linker, name, impl)
        self._instance = linker.instantiate(self._store, module)

    def _bind(self, linker: wasmtime.Linker, name: str, impl: Callable[..., int]) -> None:
        """Define one host function under its PINNED module/field strings."""
        host_fn = functions_by_name[name]
        # Signature from the pin's own `wasm_params`/`wasm_result` properties --
        # the same two properties `testmod` builds the import entry from, so the
        # two sides cannot disagree about a host function's shape.
        func_type = wasmtime.FuncType(
            [_valtype(t) for t in host_fn.wasm_params], [_valtype(host_fn.wasm_result)]
        )
        linker.define(
            self._store,
            host_fn.module,
            host_fn.export,
            wasmtime.Func(self._store, func_type, _trampoline(impl)),
        )

    def _fail_with_error(self, error: int) -> int:
        """The default recording `fail_with_error`: remember it, then abort."""
        self.errors.append(error)
        raise HostError(error)

    def read_memory(self, ptr: int, length: int) -> bytes:
        """Read `length` bytes of the guest's exported linear memory at `ptr`.

        The linear-memory host functions (`*_new_from_linear_memory`) are the
        only callbacks that need this, and they cannot be bound until the
        module is instantiated -- so an `ObjectStore` is `attach`ed to a
        finished `MiniHost` rather than constructed with one (`objects.py`).
        """
        export = self._instance.exports(self._store)[MEMORY_EXPORT_NAME]
        assert isinstance(export, wasmtime.Memory), "the module exports no linear memory"
        return bytes(export.read(self._store, ptr, ptr + length))

    def invoke(self, name: str, *vals: int) -> int | None:
        """Call an exported function with u64 `Val` words; return its raw u64 word.

        Returns `None` for a `results=()` void helper (review M2, E11ii) rather
        than tripping over `None & mask`: D's internal helpers are void, so
        Tasks 5-9 will call this on functions that return nothing, and a
        `TypeError` raised from inside the masking would be a baffling way to
        learn that.

        The outbound `as_i64` is currently *defensive*: wasmtime 48 wraps an
        out-of-i64-range Python int itself, so deleting it changes no observable
        behaviour today and no test can hold it in place. It stays because the
        conversion belongs at the boundary either way (P4), and because a
        wasmtime that starts range-checking its arguments must not turn every
        high-bit `Val` in the suite into an `OverflowError`.
        """
        export = self._instance.exports(self._store)[name]
        assert isinstance(export, wasmtime.Func), f"export {name!r} is not a function"
        result = export(self._store, *(val.as_i64(v) for v in vals))
        return None if result is None else val.as_u64(result)
