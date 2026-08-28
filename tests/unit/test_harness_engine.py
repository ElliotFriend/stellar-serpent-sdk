"""Tests for `tests/harness` -- the emitter's dev-only execution rig.

The harness is **not an oracle** (ruling E1): a green run here means "the bytes
the emitter produced are self-consistent under a wasmtime pinned to the chain's
feature set", not "this contract is correct on chain". Sub-plan F re-proves
everything against the real host. What these tests protect is the rig itself,
where three failure modes would silently invalidate everything built on it:

* **The feature set drifts.** wasmtime's `Config` feature properties are
  write-only, and assigning a name that does not exist on the class silently
  succeeds (review B2) -- so a rename at upgrade time would leave a flag quietly
  at wasmtime's default while the source still read as a pin. The assertions
  here are therefore BEHAVIOURAL (hand-encoded probe modules that must be
  rejected, or accepted), plus a descriptor probe that fails loudly if a name
  `make_config` sets is not a real `Config` property.
* **The signedness masking goes missing.** wasmtime speaks *signed* i64, Soroban
  `Val`s are *unsigned* 64-bit words. Every pin below is on the FULL 64-bit
  word: `assert result & 0xFF == 0xFF` would pass with the mask deleted, which
  is exactly the bug the mask exists to prevent (P4).
* **`testmod` mis-wires a call.** Import order and the defined-function index
  base are asserted on returned *values*, because a call to the wrong function
  of the same type validates perfectly and returns a plausible number.

The P3 ordering test is deliberately *structural*. Setting `wasm_simd = False`
while relaxed-simd is still enabled makes wasmtime **abort the process**, not
raise, so the test that proved it empirically would take the runner down with it
and could not be caught.
"""

import ast
import inspect

import pytest
import wasmtime

from serpent import val
from serpent._host import functions_by_name
from serpent.emitter import encode, frame, opcodes
from tests.harness import engine, testmod

# --- building bodies ----------------------------------------------------------
# `frame.Fn` has no `i64.const` helper yet (that is the lowering tasks' job), so
# these two wrap `op` + the stack bookkeeping.


def _i64_const(fn: frame.Fn, value: int) -> None:
    fn.op(opcodes.I64_CONST, encode.sleb(val.as_i64(value)))
    fn.push("i64")


def _binop(fn: frame.Fn, opcode: int) -> None:
    fn.pop("i64")
    fn.pop("i64")
    fn.op(opcode)
    fn.push("i64")


# --- probe-module construction ------------------------------------------------
# Hand-encoded rather than built with `testmod`: these modules are deliberately
# outside what `testmod` can express (a v128 local, a multi-value blocktype, two
# memories), which is exactly what makes them feature probes.

_MAGIC = b"\x00asm\x01\x00\x00\x00"

_SEC_TYPE = 1
_SEC_FUNCTION = 3
_SEC_MEMORY = 5
_SEC_CODE = 10

_FUNCTYPE = 0x60
_STRUCT_TYPE = 0x5F  # the GC proposal's struct type-section form
_VALTYPE_V128 = 0x7B
_VALTYPE_EXTERNREF = 0x6F

_TRY_TABLE = 0x1F  # exception handling
_RETURN_CALL = 0x12  # tail call
_LIMITS_MIN_ONLY = 0x00
_LIMITS_MEMORY64 = 0x04  # the memory64 proposal's limits flag

_MISC_PREFIX = 0xFC  # the 0xFC-prefixed opcode space (bulk memory, wide arith)
_MEMORY_FILL = 11  # bulk-memory proposal
# Wide arithmetic numbers its four opcodes 19..22 (add128, sub128, mul_wide_s,
# mul_wide_u); `mul_wide_s` is 21, NOT 19. Established empirically against
# wasmtime 48 with the proposal enabled, not read off a table: 19 and 20 take
# four operands and 21/22 take two, which is what tells them apart.
_I64_MUL_WIDE_S = 21  # wide-arithmetic proposal: [i64 i64] -> [i64 i64]

_RETURNS_I64 = 0
_NO_LOCALS = encode.vec([])


def _functype(params: bytes, results: bytes) -> bytes:
    return (
        bytes([_FUNCTYPE]) + encode.uleb(len(params)) + params + encode.uleb(len(results)) + results
    )


def _code(locals_decl: bytes, body: bytes) -> bytes:
    entry = locals_decl + body
    return encode.uleb(len(entry)) + entry


def _one_function_module(
    types: list[bytes], locals_decl: bytes, body: bytes, *, memories: list[bytes] | None = None
) -> bytes:
    out = _MAGIC + encode.section(_SEC_TYPE, encode.vec(types))
    out += encode.section(_SEC_FUNCTION, encode.vec([encode.uleb(_RETURNS_I64)]))
    if memories is not None:
        out += encode.section(_SEC_MEMORY, encode.vec(memories))
    return out + encode.section(_SEC_CODE, encode.vec([_code(locals_decl, body)]))


_I64_RESULT_TYPE = [_functype(b"", bytes([opcodes.VALTYPE_I64]))]


def _permissive_config() -> wasmtime.Config:
    """Everything the probes need, ON -- the control arm of each probe pair.

    Every rejection probe is asserted twice: it must FAIL under `make_config()`
    *and* SUCCEED here. Without the second half, a probe that was simply
    malformed would "prove" the flag is off while testing nothing at all.
    """
    config = wasmtime.Config()
    config.wasm_multi_memory = True
    config.wasm_wide_arithmetic = True
    return config


def _instantiates(config: wasmtime.Config, wasm: bytes) -> bool:
    """Compile and instantiate `wasm` under `config`; False if wasmtime rejects it."""
    try:
        eng = wasmtime.Engine(config)
        module = wasmtime.Module(eng, wasm)
        wasmtime.Instance(wasmtime.Store(eng), module, [])
    except wasmtime.WasmtimeError:
        return False
    return True


def _assert_gated(wasm: bytes, feature: str) -> None:
    assert _instantiates(_permissive_config(), wasm), (
        f"the {feature} probe module is malformed -- it does not instantiate even "
        "with the feature enabled, so its rejection below would prove nothing"
    )
    assert not _instantiates(engine.make_config(), wasm), (
        f"{feature} was accepted by make_config()'s engine; the chain does not "
        "accept it, so the harness would be testing a laxer VM than the host"
    )


def _multi_value_probe() -> bytes:
    """`() -> i64` whose body opens a block with TWO results."""
    types = _I64_RESULT_TYPE + [_functype(b"", bytes([opcodes.VALTYPE_I64, opcodes.VALTYPE_I64]))]
    body = (
        bytes([opcodes.BLOCK])
        + encode.sleb(1)  # blocktype = type index 1, spelled as a signed s33
        + bytes([opcodes.I64_CONST])
        + encode.sleb(1)
        + bytes([opcodes.I64_CONST])
        + encode.sleb(2)
        + bytes([opcodes.END, opcodes.DROP, opcodes.END])
    )
    return _one_function_module(types, _NO_LOCALS, body)


def _v128_local_probe() -> bytes:
    """`() -> i64` that declares one v128 local and never touches it."""
    body = bytes([opcodes.I64_CONST]) + encode.sleb(0) + bytes([opcodes.END])
    locals_decl = encode.vec([encode.uleb(1) + bytes([_VALTYPE_V128])])
    return _one_function_module(_I64_RESULT_TYPE, locals_decl, body)


def _wide_arithmetic_probe() -> bytes:
    """`() -> i64` using `i64.mul_wide_s` (0xFC 21), dropping the high half.

    S13: the chain's wasmi 0.31 has no wide-arithmetic support, so a module
    using it would run here and fail on chain -- the one direction a harness
    must never be wrong in. The `drop` keeps this a wide-arithmetic probe rather
    than an accidental multi-value one: the instruction pushes two results, but
    multi-value is about *block and function types*, not instruction results.
    """
    body = (
        bytes([opcodes.I64_CONST])
        + encode.sleb(3)
        + bytes([opcodes.I64_CONST])
        + encode.sleb(5)
        + bytes([_MISC_PREFIX])
        + encode.uleb(_I64_MUL_WIDE_S)
        + bytes([opcodes.DROP, opcodes.END])
    )
    return _one_function_module(_I64_RESULT_TYPE, _NO_LOCALS, body)


def _two_memories_probe() -> bytes:
    """A module declaring two memories (the multi-memory proposal)."""
    limits = bytes([_LIMITS_MIN_ONLY]) + encode.uleb(1)
    return _MAGIC + encode.section(_SEC_MEMORY, encode.vec([limits, limits]))


def _memory64_probe() -> bytes:
    """One memory declared with the memory64 limits flag (0x04).

    S23 lists memory64 OFF by name, and wasmtime 48 defaults it ON -- so without
    an explicit pin the harness would happily run 64-bit-addressed modules the
    chain rejects.
    """
    limits = bytes([_LIMITS_MEMORY64]) + encode.uleb(1)
    return _MAGIC + encode.section(_SEC_MEMORY, encode.vec([limits]))


def _exceptions_probe() -> bytes:
    """`() -> i64` opening a `try_table` with an empty catch vector.

    Not named in S23, but excluded by the same argument as multi-memory: the
    chain's wasmi 0.31 has no exception handling, so a module using it would run
    green here and fail on chain.
    """
    body = (
        bytes([_TRY_TABLE, opcodes.BLOCKTYPE_VOID])
        + encode.vec([])  # no catch clauses
        + bytes([opcodes.END])
        + bytes([opcodes.I64_CONST])
        + encode.sleb(0)
        + bytes([opcodes.END])
    )
    return _one_function_module(_I64_RESULT_TYPE, _NO_LOCALS, body)


def _gc_struct_type_probe() -> bytes:
    """A type section declaring a GC struct type with no fields.

    `wasm_gc` defaults ON and, unlike most of the GC surface, is NOT transitively
    gated by `wasm_reference_types = False` -- verified by toggling `wasm_gc`
    alone against this module. It needs its own pin.
    """
    return _MAGIC + encode.section(_SEC_TYPE, encode.vec([bytes([_STRUCT_TYPE]) + encode.vec([])]))


def _externref_local_probe() -> bytes:
    """`() -> i64` declaring one `externref` local (the reference-types proposal)."""
    body = bytes([opcodes.I64_CONST]) + encode.sleb(0) + bytes([opcodes.END])
    locals_decl = encode.vec([encode.uleb(1) + bytes([_VALTYPE_EXTERNREF])])
    return _one_function_module(_I64_RESULT_TYPE, locals_decl, body)


def _return_call_probe() -> bytes:
    """`() -> i64` that tail-calls itself (the tail-call proposal).

    Never executed -- there is no start section, and instantiation is the whole
    assertion -- so the unbounded self-recursion is inert.
    """
    body = bytes([_RETURN_CALL]) + encode.uleb(0) + bytes([opcodes.END])
    return _one_function_module(_I64_RESULT_TYPE, _NO_LOCALS, body)


def _sign_extension_probe() -> bytes:
    """`() -> i64` using `i64.extend32_s` -- sign extension, which IS accepted."""
    body = (
        bytes([opcodes.I64_CONST]) + encode.sleb(-1) + bytes([opcodes.I64_EXTEND32_S, opcodes.END])
    )
    return _one_function_module(_I64_RESULT_TYPE, _NO_LOCALS, body)


def _bulk_memory_probe() -> bytes:
    """`() -> i64` using `memory.fill` -- bulk memory, which IS accepted."""
    body = (
        bytes([opcodes.I32_CONST])
        + encode.sleb(0)
        + bytes([opcodes.I32_CONST])
        + encode.sleb(0)
        + bytes([opcodes.I32_CONST])
        + encode.sleb(0)
        + bytes([_MISC_PREFIX])
        + encode.uleb(_MEMORY_FILL)
        + encode.uleb(0)  # memory index
        + bytes([opcodes.I64_CONST])
        + encode.sleb(0)
        + bytes([opcodes.END])
    )
    return _one_function_module(
        _I64_RESULT_TYPE, _NO_LOCALS, body, memories=[b"\x00" + encode.uleb(1)]
    )


# --- make_config: structure ---------------------------------------------------


#: Exactly the flags `make_config` is expected to set. Asserted as a SET
#: EQUALITY, not a subset and not a count: `wasm_reference_types` and
#: `wasm_tail_call` have behavioural probes below, but a bare ">= n" sentinel
#: would let a deletion hide behind an addition elsewhere.
_EXPECTED_CONFIG_FLAGS = frozenset(
    {
        "wasm_relaxed_simd",
        "wasm_simd",
        "wasm_multi_value",
        "wasm_reference_types",
        "wasm_tail_call",
        "wasm_threads",
        "wasm_wide_arithmetic",
        "wasm_multi_memory",
        "wasm_memory64",
        "wasm_exceptions",
        "wasm_gc",
        "wasm_bulk_memory",
    }
)


def _config_flag_assignments() -> list[str]:
    """The `config.<flag> = ...` names `make_config` sets, in source order.

    Read out of the AST rather than by calling `make_config` and inspecting the
    result, because `Config`'s feature properties are WRITE-ONLY: there is
    nothing to read back (review B2).

    Only attributes of the `Config` object `make_config` itself builds are
    collected -- the local it binds `wasmtime.Config()` to, found by looking for
    that call rather than by assuming a name. Any other `something.attr = ...`
    in the function would otherwise be miscounted as a feature flag.
    """
    tree = ast.parse(inspect.getsource(engine))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "make_config"
    )
    config_name = next(
        target.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "Config"
        for target in node.targets
        if isinstance(target, ast.Name)
    )
    names: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == config_name
            ):
                names.append(target.attr)
    return names


def test_relaxed_simd_is_disabled_before_simd() -> None:
    """P3: "simd off, relaxed-simd on" makes wasmtime ABORT THE PROCESS.

    Not raise -- abort. So the ordering is asserted structurally, on the source,
    because the empirical version of this test would kill the test runner
    uncatchably and could never be allowed to run.
    """
    names = _config_flag_assignments()
    assert "wasm_relaxed_simd" in names
    assert "wasm_simd" in names
    assert names.index("wasm_relaxed_simd") < names.index("wasm_simd"), (
        "wasm_relaxed_simd must be disabled BEFORE wasm_simd (P3): wasmtime "
        "refuses the intermediate 'simd off, relaxed-simd on' state by aborting "
        "the process, which no test can catch"
    )


def test_every_configured_flag_is_a_real_config_property() -> None:
    """Assigning a nonexistent `Config` attribute silently succeeds (review B2).

    `wasm_mutable_global` and `wasm_sign_extension`, for instance, do NOT exist
    on wasmtime 48's `Config`. This fails loudly if an upgrade renames a flag,
    instead of leaving the feature at wasmtime's default.
    """
    descriptors = {name for klass in wasmtime.Config.__mro__ for name in vars(klass)}
    configured = _config_flag_assignments()
    unknown = [name for name in configured if name not in descriptors]
    assert not unknown, (
        f"make_config sets {unknown}, which are not properties of wasmtime.Config; "
        "the assignment silently succeeds and the feature is left at its default"
    )
    assert len(configured) == len(set(configured)), f"a flag is set twice: {configured}"


def test_the_configured_flag_set_is_exactly_the_expected_one() -> None:
    """Set EQUALITY, so a deleted flag cannot hide behind an added one.

    Several of these flags are also held in place behaviourally below, but not
    all of them can be: `wasm_wide_arithmetic` is already off by wasmtime 48's
    default, so only this assertion keeps S13's ban in the source at all.
    """
    assert set(_config_flag_assignments()) == _EXPECTED_CONFIG_FLAGS


# --- make_config: behaviour ---------------------------------------------------


def test_multi_value_is_rejected() -> None:
    _assert_gated(_multi_value_probe(), "multi-value")


def test_simd_is_rejected() -> None:
    _assert_gated(_v128_local_probe(), "a v128 local")


def test_wide_arithmetic_is_rejected() -> None:
    """wasmtime 48 already defaults this OFF, so this probe alone cannot hold the
    flag in place -- deleting `wasm_wide_arithmetic = False` leaves it green.
    What it guards is a future wasmtime flipping the default; the flag's actual
    presence is asserted by `test_every_configured_flag_is_a_real_config_property`.
    """
    _assert_gated(_wide_arithmetic_probe(), "i64.mul_wide_s")


def test_multi_memory_is_rejected() -> None:
    _assert_gated(_two_memories_probe(), "a second memory")


def test_memory64_is_rejected() -> None:
    _assert_gated(_memory64_probe(), "a memory64 memory")


def test_exception_handling_is_rejected() -> None:
    _assert_gated(_exceptions_probe(), "try_table")


def test_gc_types_are_rejected() -> None:
    _assert_gated(_gc_struct_type_probe(), "a GC struct type")


def test_reference_types_are_rejected() -> None:
    _assert_gated(_externref_local_probe(), "an externref local")


def test_tail_calls_are_rejected() -> None:
    _assert_gated(_return_call_probe(), "return_call")


def test_sign_extension_is_accepted() -> None:
    assert _instantiates(engine.make_config(), _sign_extension_probe())


def test_bulk_memory_is_accepted() -> None:
    assert _instantiates(engine.make_config(), _bulk_memory_probe())


# --- testmod ------------------------------------------------------------------


def test_add_one_module_runs() -> None:
    """The smallest thing the rig must do: build a body, run it, get an i64 back."""
    fn = frame.Fn("add_one", 1, 0, ("i64",))
    fn.local_get(0)
    _i64_const(fn, 1)
    _binop(fn, opcodes.I64_ADD)
    fn.ret()

    wasm = testmod.build_test_module([("add_one", 1, 0, ("i64",), fn.finish())])
    assert engine.MiniHost(wasm).invoke("add_one", 41) == 42


def test_locals_are_declared_and_usable() -> None:
    fn = frame.Fn("via_local", 1, 1, ("i64",))
    fn.local_get(0)
    fn.local_set(1)
    fn.local_get(1)
    fn.ret()

    wasm = testmod.build_test_module([("via_local", 1, 1, ("i64",), fn.finish())])
    assert engine.MiniHost(wasm).invoke("via_local", 9) == 9


def test_memory_and_data_segment() -> None:
    """A module with a memory and an active data segment reads back what was written."""
    payload = (0x0123_4567_89AB_CDEF).to_bytes(8, "little")
    fn = frame.Fn("read", 0, 0, ("i64",))
    fn.op(opcodes.I32_CONST, encode.sleb(0))
    fn.push("i32")
    fn.pop("i32")
    fn.op(opcodes.I64_LOAD, encode.uleb(3), encode.uleb(0))  # align 2^3, offset 0
    fn.push("i64")
    fn.ret()

    wasm = testmod.build_test_module(
        [("read", 0, 0, ("i64",), fn.finish())], memory_pages=1, data=payload
    )
    assert engine.MiniHost(wasm).invoke("read") == 0x0123_4567_89AB_CDEF


def test_data_without_a_memory_is_refused() -> None:
    with pytest.raises(ValueError, match="needs a memory"):
        testmod.build_test_module([], data=b"\x00")


def test_call_defined_is_offset_by_the_import_count() -> None:
    """`CallDefined(d)` must resolve to `len(imports) + d`, not to `d`.

    A call to the wrong function of the same type validates perfectly and
    returns a plausible number, so this is asserted on the value, with two
    imports in front of the callee to make an off-by-two visible.
    """
    callee = frame.Fn("callee", 0, 0, ("i64",))
    _i64_const(callee, 7)
    callee.ret()

    caller = frame.Fn("caller", 0, 0, ("i64",))
    caller.call_defined(0, 0, ("i64",))
    caller.ret()

    wasm = testmod.build_test_module(
        [
            ("callee", 0, 0, ("i64",), callee.finish()),
            ("caller", 0, 0, ("i64",), caller.finish()),
        ],
        imports=["obj_cmp", "get_ledger_sequence"],
    )
    host = engine.MiniHost(
        wasm,
        imports={"obj_cmp": lambda a, b: 0, "get_ledger_sequence": lambda: 0},
    )
    assert host.invoke("caller") == 7


@pytest.mark.parametrize(
    "order",
    [
        ["get_ledger_sequence", "get_ledger_version"],
        ["get_ledger_version", "get_ledger_sequence"],
    ],
)
def test_import_order_follows_the_given_sequence(order: list[str]) -> None:
    """`CallImport(name)` resolves by NAME through the given import order.

    Both imports have the same wasm type, so a swapped index would still
    validate; only the returned value tells them apart.
    """
    fn = frame.Fn("which", 0, 0, ("i64",))
    fn.call_import("get_ledger_sequence", 0, has_result=True)
    fn.ret()

    wasm = testmod.build_test_module([("which", 0, 0, ("i64",), fn.finish())], imports=order)
    host = engine.MiniHost(
        wasm,
        imports={"get_ledger_sequence": lambda: 11, "get_ledger_version": lambda: 22},
    )
    assert host.invoke("which") == 11


def test_every_function_is_exported_by_name() -> None:
    alpha = frame.Fn("alpha", 0, 0, ("i64",))
    _i64_const(alpha, 1)
    alpha.ret()
    beta = frame.Fn("beta", 0, 0, ("i64",))
    _i64_const(beta, 2)
    beta.ret()

    wasm = testmod.build_test_module(
        [
            ("alpha", 0, 0, ("i64",), alpha.finish()),
            ("beta", 0, 0, ("i64",), beta.finish()),
        ]
    )
    host = engine.MiniHost(wasm)
    assert (host.invoke("alpha"), host.invoke("beta")) == (1, 2)


def test_memory_is_exported_when_present() -> None:
    fn = frame.Fn("noop", 0, 0, ("i64",))
    _i64_const(fn, 0)
    fn.ret()
    wasm = testmod.build_test_module([("noop", 0, 0, ("i64",), fn.finish())], memory_pages=1)
    module = wasmtime.Module(wasmtime.Engine(engine.make_config()), wasm)
    exported = {e.name for e in module.exports}
    assert exported == {"noop", testmod.MEMORY_EXPORT_NAME}


def _section_payload(wasm: bytes, sid: int) -> bytes:
    """Walk `wasm`'s sections and return the payload of the first one with `sid`."""
    pos = len(_MAGIC)
    while pos < len(wasm):
        this_sid = wasm[pos]
        pos += 1
        size = 0
        shift = 0
        while True:
            byte = wasm[pos]
            pos += 1
            size |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                break
        if this_sid == sid:
            return wasm[pos : pos + size]
        pos += size
    raise AssertionError(f"no section {sid} in the module")


def _nfunctypes(wasm: bytes) -> int:
    payload = _section_payload(wasm, _SEC_TYPE)
    assert payload[0] < 0x80, "more than 127 types is not a case this helper handles"
    return payload[0]


def test_the_type_section_is_deduped() -> None:
    """Two functions of the same shape share one type entry, and differing ones do not.

    An undeduped type section still validates, so nothing else in this file
    would notice -- but it also silently invalidates any later golden that
    compares a testmod-built module against a hand-computed layout.
    """
    same = [_defined("a", 0, ("i64",), 1), _defined("b", 0, ("i64",), 2)]
    differing = [_defined("a", 0, ("i64",), 1), _defined("b", 1, ("i64",), 2)]
    assert _nfunctypes(testmod.build_test_module(same)) == 1
    assert _nfunctypes(testmod.build_test_module(differing)) == 2


def _defined(name: str, nparams: int, results: tuple[str, ...], value: int) -> testmod.FunctionSpec:
    """A `() -> i64` (or `(i64...) -> i64`) function returning the constant `value`."""
    fn = frame.Fn(name, nparams, 0, results)
    _i64_const(fn, value)
    fn.ret()
    return (name, nparams, 0, results, fn.finish())


def test_body_items_may_be_a_plain_byte_sequence() -> None:
    """`testmod` takes SYMBOLIC bodies: byte runs interleaved with call sites."""
    items: list[frame.CodeItem] = [
        bytes([opcodes.I64_CONST]) + encode.sleb(5),
        bytes([opcodes.END]),
    ]
    wasm = testmod.build_test_module([("five", 0, 0, ("i64",), items)])
    assert engine.MiniHost(wasm).invoke("five") == 5


def test_void_functions_are_supported() -> None:
    fn = frame.Fn("nothing", 0, 0, ())
    fn.ret()
    wasm = testmod.build_test_module([("nothing", 0, 0, (), fn.finish())])
    module = wasmtime.Module(wasmtime.Engine(engine.make_config()), wasm)
    assert [e.name for e in module.exports] == ["nothing"]


def test_invoking_a_void_export_returns_none() -> None:
    """D's internal helpers are void (review M2), and Tasks 5-9 will call them.

    Masking `None` would raise `TypeError: unsupported operand type(s) for &`
    from inside `invoke` -- a baffling way to be told a function returns nothing.
    """
    fn = frame.Fn("nothing", 0, 0, ())
    fn.ret()
    wasm = testmod.build_test_module([("nothing", 0, 0, (), fn.finish())])
    assert engine.MiniHost(wasm).invoke("nothing") is None


def test_import_types_come_from_the_pin() -> None:
    """The import's module/field strings and arity are the pinned ones, not guesses."""
    wasm = testmod.build_test_module([], imports=["obj_cmp"])
    module = wasmtime.Module(wasmtime.Engine(engine.make_config()), wasm)
    (imported,) = list(module.imports)
    host_fn = functions_by_name["obj_cmp"]
    assert (imported.module, imported.name) == (host_fn.module, host_fn.export)


def test_unknown_import_name_is_rejected() -> None:
    with pytest.raises(KeyError):
        testmod.build_test_module([], imports=["definitely_not_a_host_function"])


# --- the single trampoline (P4) -----------------------------------------------


def test_callback_returning_minus_one_arrives_as_the_full_u64() -> None:
    """P4, pinned on the FULL 64-bit word.

    `assert result & 0xFF == 0xFF` would pass with the mask deleted, which is
    precisely the bug the mask exists to prevent -- so the assertion is on all
    64 bits, or it is worthless.
    """
    fn = frame.Fn("cmp", 0, 0, ("i64",))
    _i64_const(fn, 0)
    _i64_const(fn, 0)
    fn.call_import("obj_cmp", 2, has_result=True)
    fn.ret()

    wasm = testmod.build_test_module([("cmp", 0, 0, ("i64",), fn.finish())], imports=["obj_cmp"])
    host = engine.MiniHost(wasm, imports={"obj_cmp": lambda a, b: -1})
    assert host.invoke("cmp") == 0xFFFF_FFFF_FFFF_FFFF


def test_arguments_reach_the_callback_as_unsigned_words() -> None:
    """The other direction: wasm hands the host a SIGNED i64; the callback sees u64."""
    seen: list[int] = []
    error_word = val.error_val(0xFFFF_FFFF)
    assert error_word >> 63 == 1, "the fixture is only meaningful with the high bit set"

    def record(a: int, b: int) -> int:
        seen.extend((a, b))
        return 0

    fn = frame.Fn("pass_through", 1, 0, ("i64",))
    fn.local_get(0)
    _i64_const(fn, error_word)
    fn.call_import("obj_cmp", 2, has_result=True)
    fn.ret()

    wasm = testmod.build_test_module(
        [("pass_through", 1, 0, ("i64",), fn.finish())], imports=["obj_cmp"]
    )
    host = engine.MiniHost(wasm, imports={"obj_cmp": record})
    host.invoke("pass_through", 0xFFFF_FFFF_FFFF_FFFF)
    assert seen == [0xFFFF_FFFF_FFFF_FFFF, error_word]


def test_invoke_round_trips_a_word_with_the_high_bit_set() -> None:
    """`invoke` takes and returns u64 words; neither end may overflow wasmtime's i64."""
    fn = frame.Fn("echo", 1, 0, ("i64",))
    fn.local_get(0)
    fn.ret()
    wasm = testmod.build_test_module([("echo", 1, 0, ("i64",), fn.finish())])
    host = engine.MiniHost(wasm)
    assert host.invoke("echo", 0xFFFF_FFFF_FFFF_FFFF) == 0xFFFF_FFFF_FFFF_FFFF


def test_an_i64_const_with_the_high_bit_set_returns_intact() -> None:
    word = val.error_val(0xFFFF_FFFF)
    fn = frame.Fn("high_bit", 0, 0, ("i64",))
    _i64_const(fn, word)
    fn.ret()
    wasm = testmod.build_test_module([("high_bit", 0, 0, ("i64",), fn.finish())])
    assert engine.MiniHost(wasm).invoke("high_bit") == word


# --- fail_with_error ----------------------------------------------------------


def _fail_with_error_module(error_word: int) -> bytes:
    fn = frame.Fn("boom", 0, 0, ("i64",))
    _i64_const(fn, error_word)
    fn.call_import("fail_with_error", 1, has_result=True)
    fn.ret()
    return testmod.build_test_module(
        [("boom", 0, 0, ("i64",), fn.finish())], imports=["fail_with_error"]
    )


def test_default_fail_with_error_raises_host_error_carrying_the_exact_val() -> None:
    word = val.error_val(0xFFFF_FFFF)
    host = engine.MiniHost(_fail_with_error_module(word))

    with pytest.raises(engine.HostError) as caught:
        host.invoke("boom")
    assert caught.value.val == word
    assert host.errors == [word]


def test_fail_with_error_can_be_overridden() -> None:
    host = engine.MiniHost(
        _fail_with_error_module(val.error_val(3)),
        imports={"fail_with_error": lambda e: val.VOID_VAL},
    )
    assert host.invoke("boom") == val.VOID_VAL
    assert host.errors == []


def test_host_error_masks_a_negative_val() -> None:
    assert engine.HostError(-1).val == 0xFFFF_FFFF_FFFF_FFFF


# --- binding discipline -------------------------------------------------------


def test_no_pinned_import_string_is_hardcoded_in_the_harness() -> None:
    """F.1.17: BOTH halves of the import pair come from the pin, never a literal.

    The spike loaded its own `env.json`; this rig looks names up in
    `serpent._host.functions_by_name`, the same pin the emitter compiles
    against, so a re-pin that moves an export code cannot silently mis-wire the
    harness into testing the wrong import. The module string is checked as well
    as the field: it is a single character at this pin, exactly the kind of value
    that gets inlined "because it never changes".
    """
    source = inspect.getsource(engine) + inspect.getsource(testmod)
    for name in ("obj_cmp", "fail_with_error", "get_ledger_sequence"):
        host_fn = functions_by_name[name]
        for label, literal in (("module", host_fn.module), ("export code", host_fn.export)):
            assert f'"{literal}"' not in source, f"{name}'s {label} {literal!r} is hardcoded"
            assert f"'{literal}'" not in source, f"{name}'s {label} {literal!r} is hardcoded"
