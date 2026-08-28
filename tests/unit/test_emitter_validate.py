"""`serpent.emitter.validate`: the independent decoder and the wasm-tools gate.

S2/P8: "an invalid module is a compile error, never an output file". The
validator's whole value is that it does NOT consult the emitter's bookkeeping --
it re-derives every fact from the bytes -- so every negative control here is a
hand-built module carrying the defect, not a mocked emitter state. Each one is
RED-proven: the module is byte-identical to a passing one except for the single
injected fault.
"""

import shutil
import subprocess

import pytest

from serpent.compiler.frontend import compile_module
from serpent.emitter import encode, module, opcodes, validate
from serpent.emitter.frame import BuildLimitError, EmitError
from tests.unit.test_emitter_module import (
    _TOKEN_STYLE,
    COUNTER_SRC,
    HELPED_SRC,
    NAMER_SRC,
    WIDE_SRC,
    build,
)

_MAGIC = b"\x00asm\x01\x00\x00\x00"

_SEC_TYPE = 1
_SEC_IMPORT = 2
_SEC_FUNCTION = 3
_SEC_MEMORY = 5
_SEC_EXPORT = 7
_SEC_START = 8
_SEC_CODE = 10

_FUNCTYPE = 0x60
_KIND_FUNC = 0x00
_KIND_MEMORY = 0x02


# ===========================================================================
# Hand-built modules: one helper per section, so each negative control differs
# from the passing case by exactly one injected fault.
# ===========================================================================


def _functype(nparams: int, nresults: int) -> bytes:
    i64 = bytes([opcodes.VALTYPE_I64])
    return bytes([_FUNCTYPE]) + encode.vec([i64] * nparams) + encode.vec([i64] * nresults)


def _type_section(shapes: list[tuple[int, int]]) -> bytes:
    return encode.section(_SEC_TYPE, encode.vec([_functype(p, r) for p, r in shapes]))


def _import_section(entries: list[tuple[str, str]]) -> bytes:
    return encode.section(
        _SEC_IMPORT,
        encode.vec(
            [
                encode.wasm_name(mod)
                + encode.wasm_name(field)
                + bytes([_KIND_FUNC])
                + encode.uleb(0)
                for mod, field in entries
            ]
        ),
    )


def _function_section(typeidxs: list[int]) -> bytes:
    return encode.section(_SEC_FUNCTION, encode.vec([encode.uleb(t) for t in typeidxs]))


def _memory_section(count: int = 1) -> bytes:
    return encode.section(_SEC_MEMORY, encode.vec([b"\x00" + encode.uleb(1)] * count))


def _export_section(entries: list[tuple[str, int, int]]) -> bytes:
    return encode.section(
        _SEC_EXPORT,
        encode.vec(
            [
                encode.wasm_name(name) + bytes([kind]) + encode.uleb(index)
                for name, kind, index in entries
            ]
        ),
    )


def _code_section(bodies: list[bytes]) -> bytes:
    entries = [encode.uleb(0) + body for body in bodies]
    return encode.section(_SEC_CODE, encode.vec([encode.uleb(len(e)) + e for e in entries]))


#: The smallest module this validator accepts: one function, no memory. Every
#: negative control below is this module with one section replaced or added.
_END = bytes([opcodes.END])


def _passing(**overrides: bytes) -> bytes:
    parts = {
        "type": _type_section([(0, 0)]),
        "function": _function_section([0]),
        "export": _export_section([("go", _KIND_FUNC, 0)]),
        "code": _code_section([_END]),
    }
    parts.update({k: v for k, v in overrides.items()})
    return _MAGIC + b"".join(parts[k] for k in ("type", "function", "export", "code"))


def test_the_hand_built_baseline_module_passes() -> None:
    """The control for every control: if this ever fails, the negatives below
    are proving nothing."""
    validate.validate_internal(_passing(), expect_memory=False)


# ===========================================================================
# The real article
# ===========================================================================


def test_a_real_assembled_memoryless_module_validates() -> None:
    validate.validate_internal(build(COUNTER_SRC), expect_memory=False)


def test_a_real_assembled_module_with_memory_validates() -> None:
    validate.validate_internal(build(NAMER_SRC), expect_memory=True)


def test_a_real_module_carries_no_section_the_validator_rejects() -> None:
    """Belt and braces over the fixture set the module tests assemble."""
    for src, expect_memory in ((COUNTER_SRC, False), (NAMER_SRC, True)):
        validate.validate_internal(build(src), expect_memory=expect_memory)


# ===========================================================================
# magic, version, framing
# ===========================================================================


def test_bytes_that_are_not_a_wasm_module_are_refused() -> None:
    with pytest.raises(EmitError, match="magic"):
        validate.validate_internal(b"not wasm at all", expect_memory=False)


def test_a_wasm_version_other_than_1_is_refused() -> None:
    forged = b"\x00asm\x02\x00\x00\x00" + _passing()[8:]
    with pytest.raises(EmitError, match="version"):
        validate.validate_internal(forged, expect_memory=False)


def test_a_truncated_section_payload_is_refused() -> None:
    whole = _passing()
    with pytest.raises(EmitError, match="truncated"):
        validate.validate_internal(whole[:-2], expect_memory=False)


def test_a_section_id_the_binary_format_does_not_define_is_refused() -> None:
    forged = _passing() + encode.section(13, b"")
    with pytest.raises(EmitError, match="section id"):
        validate.validate_internal(forged, expect_memory=False)


# ===========================================================================
# Section order (B.1: fixed by id; customs anywhere)
# ===========================================================================


def test_sections_out_of_id_order_are_refused() -> None:
    forged = (
        _MAGIC
        + _function_section([0])
        + _type_section([(0, 0)])
        + _export_section([("go", _KIND_FUNC, 0)])
        + _code_section([_END])
    )
    with pytest.raises(EmitError, match="ascend"):
        validate.validate_internal(forged, expect_memory=False)


def test_a_repeated_non_custom_section_is_refused() -> None:
    """Strictly ascending, not merely non-descending: a second export section
    would be ignored by some readers and honoured by others."""
    forged = _passing() + _code_section([_END])
    with pytest.raises(EmitError, match="ascend"):
        validate.validate_internal(forged, expect_memory=False)


def test_custom_sections_may_appear_anywhere() -> None:
    forged = (
        _MAGIC
        + encode.custom_section("first", b"\x01")
        + _type_section([(0, 0)])
        + encode.custom_section("middle", b"\x02")
        + _function_section([0])
        + _export_section([("go", _KIND_FUNC, 0)])
        + _code_section([_END])
        + encode.custom_section("last", b"\x03")
    )
    validate.validate_internal(forged, expect_memory=False)


# ===========================================================================
# S23's hard bans and caps
# ===========================================================================


def test_a_start_section_is_refused() -> None:
    """S23: "No start section." The injected section is well-formed -- the only
    thing wrong with it is that it exists."""
    forged = (
        _MAGIC
        + _type_section([(0, 0)])
        + _function_section([0])
        + _export_section([("go", _KIND_FUNC, 0)])
        + encode.section(_SEC_START, encode.uleb(0))
        + _code_section([_END])
    )
    with pytest.raises(EmitError, match="start section"):
        validate.validate_internal(forged, expect_memory=False)


def test_a_functype_with_33_parameters_is_refused() -> None:
    """S23/C18 cap contract arity at 32. 32 passes, 33 does not -- the boundary
    is asserted on both sides so an off-by-one cannot hide."""
    validate.validate_internal(_passing(type=_type_section([(32, 1)])), expect_memory=False)
    with pytest.raises(EmitError, match="33"):
        validate.validate_internal(_passing(type=_type_section([(33, 1)])), expect_memory=False)


def test_a_functype_with_33_results_is_refused() -> None:
    with pytest.raises(EmitError, match="result"):
        validate.validate_internal(_passing(type=_type_section([(0, 33)])), expect_memory=False)


def test_an_import_field_name_over_ten_characters_is_refused() -> None:
    """S23 caps import symbol names at 10 characters. Satisfied by construction
    for the pin's own names (`x.5`, `m.9`), which is exactly why an independent
    check is worth having: nothing else would notice a hand-added import."""
    ten = (
        _MAGIC
        + _type_section([(0, 0)])
        + _import_section([("x", "0123456789")])
        + _function_section([0])
        + _export_section([("go", _KIND_FUNC, 1)])
        + _code_section([_END])
    )
    validate.validate_internal(ten, expect_memory=False)
    forged = (
        _MAGIC
        + _type_section([(0, 0)])
        + _import_section([("x", "01234567890")])
        + _function_section([0])
        + _export_section([("go", _KIND_FUNC, 1)])
        + _code_section([_END])
    )
    with pytest.raises(EmitError, match="10"):
        validate.validate_internal(forged, expect_memory=False)


def test_a_module_over_the_network_size_limit_is_a_BUILD_LIMIT_not_a_bug() -> None:
    """S22: 131072 bytes. This is the one check whose failure is the USER's
    problem, so it raises the user-visible subclass carrying the discriminator
    Task 11 maps to SPT8001."""
    padded = _passing() + encode.custom_section("pad", b"\x00" * validate.MAX_MODULE_SIZE)
    assert len(padded) > validate.MAX_MODULE_SIZE
    with pytest.raises(BuildLimitError) as excinfo:
        validate.validate_internal(padded, expect_memory=False)
    assert excinfo.value.limit == "module_size"
    assert str(validate.MAX_MODULE_SIZE) in str(excinfo.value)


def test_the_size_limit_is_the_networks_number() -> None:
    assert validate.MAX_MODULE_SIZE == 131072


# ===========================================================================
# Exports and memory
# ===========================================================================


def test_a_repeated_export_name_is_refused() -> None:
    """Two exports under one name make the ABI ambiguous, and the wasm spec
    forbids it outright."""
    forged = _passing(export=_export_section([("go", _KIND_FUNC, 0), ("go", _KIND_FUNC, 0)]))
    with pytest.raises(EmitError, match="export name"):
        validate.validate_internal(forged, expect_memory=False)


def test_more_than_one_memory_is_refused() -> None:
    """S23 allows exactly one, and the harness pins `wasm_multi_memory=False`
    to match -- but a module that declared two would be a compiler bug worth
    catching before the engine's own error message."""
    forged = (
        _MAGIC
        + _type_section([(0, 0)])
        + _function_section([0])
        + _memory_section(count=2)
        + _export_section([("memory", _KIND_MEMORY, 0)])
        + _code_section([_END])
    )
    with pytest.raises(EmitError, match="at most one"):
        validate.validate_internal(forged, expect_memory=True)


def test_a_missing_memory_export_is_refused_when_one_is_expected() -> None:
    with pytest.raises(EmitError, match="memory"):
        validate.validate_internal(_passing(), expect_memory=True)


def test_an_unexpected_MEMORY_is_refused() -> None:
    """The other direction of the same check: E10 says a memoryless module has
    no memory at all, so declaring one means the decision and the layout
    disagreed. Deliberately WITHOUT the export, so nothing but the
    memory-count check can be what fires."""
    forged = (
        _MAGIC
        + _type_section([(0, 0)])
        + _function_section([0])
        + _memory_section()
        + _export_section([("go", _KIND_FUNC, 0)])
        + _code_section([_END])
    )
    with pytest.raises(EmitError, match="memoryless"):
        validate.validate_internal(forged, expect_memory=False)


def test_an_unexpected_memory_EXPORT_is_refused() -> None:
    """And the export half on its own: a module with no memory section that
    exports one anyway. Isolated the same way -- with the memory section present
    the count check would fire first, and then this branch would be shadowed."""
    forged = (
        _MAGIC
        + _type_section([(0, 0)])
        + _function_section([0])
        + _export_section([("go", _KIND_FUNC, 0), ("memory", _KIND_MEMORY, 0)])
        + _code_section([_END])
    )
    with pytest.raises(EmitError, match="as memory"):
        validate.validate_internal(forged, expect_memory=False)


def test_a_memory_exported_under_the_wrong_name_does_not_count() -> None:
    """P15 again, from the validator's side: the host looks up the literal name
    `memory`, so a memory exported as `mem` is a memory the host cannot see."""
    forged = (
        _MAGIC
        + _type_section([(0, 0)])
        + _function_section([0])
        + _memory_section()
        + _export_section([("mem", _KIND_MEMORY, 0)])
        + _code_section([_END])
    )
    with pytest.raises(EmitError, match="memory"):
        validate.validate_internal(forged, expect_memory=True)


def test_the_expected_memory_shape_passes() -> None:
    ok = (
        _MAGIC
        + _type_section([(0, 0)])
        + _function_section([0])
        + _memory_section()
        + _export_section([("go", _KIND_FUNC, 0), (module.MEMORY_EXPORT_NAME, _KIND_MEMORY, 0)])
        + _code_section([_END])
    )
    validate.validate_internal(ok, expect_memory=True)


# ===========================================================================
# The optional external gate (ruling E5)
# ===========================================================================


def test_the_feature_string_is_the_chains_pinned_set() -> None:
    """S23's feature set, as one named constant so the harness config, the
    docs, and the shell-out cannot drift apart."""
    assert validate.WASM_FEATURES == "-all,mutable-global,sign-extension,bulk-memory"


def test_validate_external_returns_None_when_wasm_tools_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`None` means "not answered", which is different from `False` ("answered
    no") -- Task 11's `validate_external=True` needs to tell them apart."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert validate.validate_external(build(COUNTER_SRC)) is None


@pytest.mark.skipif(
    shutil.which("wasm-tools") is None,
    reason="wasm-tools is not installed (ruling E5: optional-if-present, never silently passed)",
)
def test_wasm_tools_accepts_every_module_this_sub_plan_can_assemble() -> None:
    """The independent instruction-stream check (D.3): `frame.Fn` validated
    every operand stack at lowering time, and this is the second opinion. The
    fixture set spans the shapes that differ structurally -- memoryless, pooled
    literals, a constructor plus an internal call, and runtime parts with a
    scratch slot -- plus the richest contract in the repo."""
    for src in (COUNTER_SRC, NAMER_SRC, WIDE_SRC, HELPED_SRC):
        assert validate.validate_external(build(src)) is True, src.splitlines()[0]
    token_style = _TOKEN_STYLE.read_text(encoding="utf-8")
    wasm = module.assemble(compile_module(token_style, str(_TOKEN_STYLE)), meta={}, version=None)
    assert validate.validate_external(wasm) is True


@pytest.mark.skipif(
    shutil.which("wasm-tools") is None,
    reason="wasm-tools is not installed (ruling E5: optional-if-present, never silently passed)",
)
def test_wasm_tools_rejects_bytes_that_are_not_a_module() -> None:
    assert validate.validate_external(b"\x00asm\x01\x00\x00\x00\x63") is False


@pytest.mark.skipif(
    shutil.which("wasm-tools") is None,
    reason="wasm-tools is not installed (ruling E5: optional-if-present, never silently passed)",
)
def test_the_external_gate_really_shells_out_with_the_pinned_features() -> None:
    """Proves the constant reaches the tool rather than merely existing: the
    same command, run by hand, agrees."""
    wasm = build(COUNTER_SRC)
    tool = shutil.which("wasm-tools")
    assert tool is not None
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".wasm") as handle:
        handle.write(wasm)
        handle.flush()
        done = subprocess.run(
            [tool, "validate", f"--features={validate.WASM_FEATURES}", handle.name],
            capture_output=True,
            check=False,
        )
    assert done.returncode == 0, done.stderr.decode()
