"""The real host: which WASM proposals the chain's host actually accepts (dossier O3).

serpent's emitter targets a RESTRICTED WASM: no floats, 32-bit memory, constant
global initializers, no exception handling. Until this module existed, every one
of those was a claim from a config file nobody in this repo had asked the host to
enforce. This is the asking.

**The vacuity trap, and why `custom_sections` had to be added first.** The host
REJECTS every module with no `contractenvmetav0` custom section, before it looks
at a single opcode -- measured 2026-09-02: `HostPanic`, underlying
`("WasmVm", "InvalidInput")`, diagnostic "contract missing metadata section". So
a negative built with the bare `build_test_module` would be refused for the wrong
reason and the test would pass while proving nothing. Every module here therefore
carries `{"contractenvmetav0": serpent.spec.build_env_meta(20)}` -- written by the
emitter's own writer, so there is ONE implementation of that payload -- and
`test_the_same_module_without_the_metadata_section_is_refused` keeps the trap
itself on record.

**The POSITIVE control is what makes the negatives mean anything.** A module
using `i64.extend8_s` -- the sign-extension proposal, ON in the chain's config,
and an opcode serpent's emitter never emits (it uses `i64.extend32_s` for the I32
unbox) -- plus the metadata section MUST register AND be invokable. If that
failed, "the host refused my module" would say nothing about the feature under
test.

**Why two of the four negatives are hand-assembled.** `build_test_module` has no
knob for a memory's limits FLAG and emits no global section at all, and both are
deliberate -- it is the harness's minimal module builder, not a fuzzer. The two
shapes that need those bytes are written out here with a comment per byte, and
each has its own LEGAL twin (same bytes, flag `0x00` / a constant initializer)
that registers -- so the refusal is attributable to the one byte that changed and
not to a hand-assembly mistake.

Measured 2026-09-02, protocol 28 (soroban-env-host 28.0.2). Every rejection
arrives as a `HostPanic`, not a `RealHostError`: the sdk's `register` panics on a
rejected module and the Rust layer's `contained` call turns that into kind
"panic" (E4). The underlying pairs:

* `f64.const` -> `("WasmVm", "InvalidAction")`, diagnostic
  "floating-point instruction disallowed" (the only one of the four whose
  diagnostic names the feature);
* memory64 limits flag `0x04` -> `("WasmVm", "InvalidAction")`;
* a global with an extended-const initializer -> `("WasmVm", "InvalidAction")`;
* `try` (`0x06`) -> `("WasmVm", "InvalidAction")`;
* and, for contrast, NO metadata section -> `("WasmVm", "InvalidInput")` -- a
  malformed CONTRACT rather than a forbidden instruction, which is exactly the
  distinction that would have made the negatives vacuous.
"""

from __future__ import annotations

import pytest

from serpent import spec, val
from serpent.emitter import encode, opcodes
from serpent.testing import HostPanic, RealEnv
from tests.harness.testmod import build_test_module

real = pytest.mark.real_host  # per-test (review M12): the byte-shape tests below run everywhere

#: The metadata section every module here carries. Protocol 20 is the plain
#: import floor -- the same one `env_surface.py` declares -- and the payload comes
#: from the emitter's own writer rather than a literal.
META = {"contractenvmetav0": spec.build_env_meta(20)}

#: Opcodes `serpent.emitter.opcodes` has no constant for, because the emitter
#: never emits them. Spec-pinned, with the proposal each belongs to:
I64_EXTEND8_S = 0xC2  # sign-extension proposal (ON in the chain's config)
F64_CONST = 0x44  # 64-bit float constant, followed by 8 raw bytes
I32_ADD = 0x6A  # only reachable in a const expression under extended-const
TRY = 0x06  # exception-handling proposal

_FUNCTYPE = 0x60  # the functype tag in a type section entry
_VALTYPE_I32 = 0x7F
_VALTYPE_I64 = 0x7E
_KIND_FUNC = 0x00

_SEC_TYPE = 1
_SEC_FUNCTION = 3
_SEC_MEMORY = 5
_SEC_GLOBAL = 6
_SEC_EXPORT = 7
_SEC_CODE = 10

#: A body that returns Void and nothing else, as the raw bytes the
#: hand-assembled modules need (the ones that go through `build_test_module`
#: pass their body as items instead).
_VOID_BODY = bytes([opcodes.I64_CONST]) + encode.sleb(val.VOID_VAL) + bytes([opcodes.END])


def _hand_assembled(*, memory_flag: int | None = None, global_init: bytes | None = None) -> bytes:
    """One module exporting `go() -> i64`, with an optional memory or global.

    Written byte by byte because the two shapes it exists for -- a limits FLAG
    other than `0x00`, and a global section at all -- are things
    `build_test_module` deliberately cannot say. Everything else about the module
    is the same minimum it builds, so the only difference between a negative here
    and its legal twin is the byte under test.
    """
    out = bytearray(b"\x00asm\x01\x00\x00\x00")  # magic + version 1
    out += encode.section(
        _SEC_TYPE,
        # one type: () -> (i64)
        encode.vec([bytes([_FUNCTYPE]) + encode.vec([]) + encode.vec([bytes([_VALTYPE_I64])])]),
    )
    out += encode.section(_SEC_FUNCTION, encode.vec([encode.uleb(0)]))  # func 0 : type 0
    if memory_flag is not None:
        # limits: FLAG byte then the minimum page count. 0x00 is "min only"
        # (what a legal module says); 0x04 sets the memory64 bit.
        out += encode.section(_SEC_MEMORY, encode.vec([bytes([memory_flag]) + encode.uleb(1)]))
    if global_init is not None:
        # one global: valtype i32, mutability 0x00 (immutable), then the init
        # expression (which the caller supplies, terminated by `end`).
        out += encode.section(_SEC_GLOBAL, encode.vec([bytes([_VALTYPE_I32, 0x00]) + global_init]))
    out += encode.section(
        _SEC_EXPORT,
        # one export: the name "go", descriptor kind 0x00 (func), index 0
        encode.vec([encode.wasm_name("go") + bytes([_KIND_FUNC]) + encode.uleb(0)]),
    )
    entry = b"\x00" + _VOID_BODY  # zero local declarations, then the body
    out += encode.section(_SEC_CODE, encode.vec([encode.uleb(len(entry)) + entry]))
    out += encode.custom_section("contractenvmetav0", spec.build_env_meta(20))
    return bytes(out)


def _positive_control() -> bytes:
    """`go()` = `i64.extend8_s(i64.const 0x7FFFFF02)` -- Void, the hard way.

    The constant is deliberately NOT `VOID_VAL`: `0x7FFFFF02`'s low byte is
    `0x02`, so sign-extending it yields `VOID_VAL` exactly, and a host that
    somehow skipped the opcode would return a `Val` with Void's tag and a
    nonzero body instead. The answer is therefore evidence that the instruction
    RAN, not merely that the module validated.
    """
    body = [
        bytes([opcodes.I64_CONST]) + encode.sleb(0x7FFFFF02),
        bytes([I64_EXTEND8_S]),
        bytes([opcodes.END]),
    ]
    return build_test_module([("go", 0, 0, ("i64",), body)], custom_sections=META)


@real
def test_the_positive_control_registers_and_is_invokable() -> None:
    """The control the four negatives depend on (O3).

    `i64.extend8_s` is in the sign-extension proposal, which the chain's config
    turns ON, and the emitter never emits this particular opcode -- so this is a
    real question with a real answer, and the answer is yes.
    """
    c = RealEnv().deploy_wasm(_positive_control())
    assert c.invoke("go") is None  # Void: the sign extension produced VOID_VAL


@real
def test_the_same_module_without_the_metadata_section_is_refused() -> None:
    """The vacuity trap, on record (review M1).

    The ONLY difference from the control above is the missing custom section, and
    the host refuses it before it ever validates the code -- which is why every
    module in this file carries the section, and why a feature-set negative
    written without one would have proved nothing at all.
    """
    body = [
        bytes([opcodes.I64_CONST]) + encode.sleb(0x7FFFFF02),
        bytes([I64_EXTEND8_S]),
        bytes([opcodes.END]),
    ]
    with pytest.raises(HostPanic) as refused:
        RealEnv().deploy_wasm(build_test_module([("go", 0, 0, ("i64",), body)]))
    assert refused.value.underlying == ("WasmVm", "InvalidInput")
    assert "metadata section" in str(refused.value)


@real
def test_a_floating_point_instruction_is_refused() -> None:
    """`f64.const 0` then `drop` (O3). Diagnostic: "floating-point instruction
    disallowed" -- the one negative here whose diagnostic names the feature, and
    the reason serpent has no float type at all."""
    body = [
        bytes([F64_CONST]) + b"\x00" * 8,  # f64.const 0.0: the opcode, then 8 raw bytes
        bytes([opcodes.DROP]),
        bytes([opcodes.I64_CONST]) + encode.sleb(val.VOID_VAL),
        bytes([opcodes.END]),
    ]
    with pytest.raises(HostPanic) as refused:
        RealEnv().deploy_wasm(
            build_test_module([("go", 0, 0, ("i64",), body)], custom_sections=META)
        )
    assert refused.value.underlying == ("WasmVm", "InvalidAction")
    assert "floating-point instruction disallowed" in str(refused.value)


@real
def test_an_exception_handling_opcode_is_refused() -> None:
    """A body containing `try` (`0x06`) (O3).

    The blocktype byte after it is `0x40` (empty), and the validator never gets
    as far as caring: the opcode itself is not in the accepted instruction set,
    so this is a refusal of the PROPOSAL and not of a malformed `try ... catch`.
    """
    body = [
        bytes([TRY, 0x40]),  # try, blocktype 0x40 (no result)
        bytes([opcodes.END]),
        bytes([opcodes.I64_CONST]) + encode.sleb(val.VOID_VAL),
        bytes([opcodes.END]),
    ]
    with pytest.raises(HostPanic) as refused:
        RealEnv().deploy_wasm(
            build_test_module([("go", 0, 0, ("i64",), body)], custom_sections=META)
        )
    assert refused.value.underlying == ("WasmVm", "InvalidAction")


@real
def test_a_memory64_limits_flag_is_refused_where_the_legal_flag_is_not() -> None:
    """Limits flag `0x04` (memory64) against `0x00` (O3).

    Both assertions in one test on purpose: the legal twin is what makes the
    refusal attributable to the flag byte rather than to the hand-assembly.
    """
    assert RealEnv().deploy_wasm(_hand_assembled(memory_flag=0x00)).invoke("go") is None
    with pytest.raises(HostPanic) as refused:
        RealEnv().deploy_wasm(_hand_assembled(memory_flag=0x04))
    assert refused.value.underlying == ("WasmVm", "InvalidAction")


@real
def test_an_extended_const_global_initializer_is_refused_where_a_constant_is_not() -> None:
    """`i32.const 1; i32.const 2; i32.add; end` against `i32.const 3; end` (O3).

    The extended-const proposal is what would allow arithmetic in a global's
    init expression. The legal twin initializes the same global with a plain
    constant and registers, so again the refusal is the initializer's.
    """
    constant = bytes([opcodes.I32_CONST]) + encode.sleb(3) + bytes([opcodes.END])
    assert RealEnv().deploy_wasm(_hand_assembled(global_init=constant)).invoke("go") is None
    extended = (
        bytes([opcodes.I32_CONST])
        + encode.sleb(1)
        + bytes([opcodes.I32_CONST])
        + encode.sleb(2)
        + bytes([I32_ADD])
        + bytes([opcodes.END])
    )
    with pytest.raises(HostPanic) as refused:
        RealEnv().deploy_wasm(_hand_assembled(global_init=extended))
    assert refused.value.underlying == ("WasmVm", "InvalidAction")


# ===========================================================================
# byte-shape tests -- UNMARKED, so a Rust-less checkout still checks the bytes
# ===========================================================================


def test_the_custom_section_lands_after_the_code_section() -> None:
    """`build_test_module`'s one new parameter (review M1), checked as BYTES.

    A custom section is legal anywhere between two known sections, so "it is
    there" is not the claim: the claim is that it is emitted LAST, where the
    emitter puts serpent's own -- which is what makes these modules the same
    shape as the ones the host already accepts from this repo.
    """
    body = [bytes([opcodes.I64_CONST]) + encode.sleb(val.VOID_VAL), bytes([opcodes.END])]
    wasm = build_test_module([("go", 0, 0, ("i64",), body)], custom_sections=META)
    section = encode.custom_section("contractenvmetav0", spec.build_env_meta(20))
    assert wasm.endswith(section)
    assert wasm.count(section) == 1
    assert build_test_module([("go", 0, 0, ("i64",), body)]) == wasm[: -len(section)]


def test_two_custom_sections_are_emitted_in_the_callers_order() -> None:
    """Insertion order, stated in the parameter's docstring and pinned here."""
    body = [bytes([opcodes.I64_CONST]) + encode.sleb(val.VOID_VAL), bytes([opcodes.END])]
    wasm = build_test_module(
        [("go", 0, 0, ("i64",), body)], custom_sections={"first": b"\x01", "second": b"\x02"}
    )
    assert wasm.endswith(
        encode.custom_section("first", b"\x01") + encode.custom_section("second", b"\x02")
    )


def test_the_two_hand_assembled_shapes_differ_by_exactly_one_byte() -> None:
    """The memory negative's whole argument, as arithmetic on the bytes.

    If the legal and the memory64 modules differed anywhere else, the refusal
    above would be evidence about the difference and not about the flag.
    """
    legal = _hand_assembled(memory_flag=0x00)
    memory64 = _hand_assembled(memory_flag=0x04)
    assert len(legal) == len(memory64)
    assert sum(a != b for a, b in zip(legal, memory64, strict=True)) == 1
