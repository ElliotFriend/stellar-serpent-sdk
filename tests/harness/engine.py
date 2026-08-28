"""The pinned wasmtime engine and a mini host, just big enough to run a module.

Ported **by copy** from `spikes/spike1/harness.py:143-158` (R5: `spikes/` is
read-only evidence, never imported from). Two things were deliberately NOT
copied: the spike's `OBJECT_CODE_UPPER_BOUND = 79` (stale -- `Val` facts come
from `serpent.val`, never from a second transcription of the tag table), and the
spike's `env.json` loader (F.1.17: bindings are looked up by name in
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
"""

from collections.abc import Callable, Mapping

import wasmtime

from serpent import val
from serpent._host import functions_by_name


def make_config() -> wasmtime.Config:
    """The chain's accepted feature set, as wasmtime flags.

    Only flags that actually exist on wasmtime 48's `Config` are set (review
    B2). This matters more than it looks: `Config`'s feature properties are
    write-only setters, and assigning an attribute that does not exist on the
    class silently succeeds -- so a plausible-looking `config.wasm_sign_extension
    = True` would leave the feature at wasmtime's default while reading as a
    pin. `tests/unit/test_harness_engine.py` asserts every name set here is a
    real descriptor, and asserts the resulting feature set BEHAVIOURALLY (probe
    modules that must be rejected or accepted), because there is nothing to read
    back.
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
    # Not in the spike's list, and NOT off by wasmtime 48's default -- the
    # behavioural second-memory probe is what found that. Soroban's wasmi
    # accepts exactly one memory, so a two-memory module must be rejected here.
    config.wasm_multi_memory = False
    # Enabled, matching the emitter's `wasm-tools validate --features=` line.
    config.wasm_bulk_memory = True
    return config


class HostError(Exception):
    """The contract aborted through `fail_with_error`, carrying its `Val`.

    `fail_with_error` "does not actually return" (env.json), so a Python
    exception is the honest model of it. `val` is the **unsigned** error `Val`
    word, which is what a client would see.
    """

    def __init__(self, error_val: int) -> None:
        word = val.as_u64(error_val)
        super().__init__(f"contract aborted with Val {word:#018x}")
        self.val = word


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
    sub-plan F's productization problem, not this rig's.
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

    def invoke(self, name: str, *vals: int) -> int:
        """Call an exported function with u64 `Val` words; return its raw u64 word.

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
        return val.as_u64(result)
