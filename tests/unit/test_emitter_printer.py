"""`serpent.emitter.printer`: the reviewable disassembler (dossier D.2, Task 14).

Two obligations, both from the task brief:

* every opcode `opcodes.py`'s two provenance sets pin must actually render,
  through a real `frame.Fn`-built body -- never a hand-rolled byte string
  that happens to look right -- and a byte outside that vocabulary is a loud
  `EmitError`, never a guessed mnemonic;
* host calls resolve to their registered NAME (never a bare index), and a
  call to another defined function resolves to its export name when it has
  one, `$fn<N>` (its DEFINED-space index) otherwise.

The per-fixture snapshots under `tests/goldens/wasm/*.wat.txt` are the OTHER
half of Task 14 and live in this same file, following
`tests/unit/test_frontend_goldens.py`'s own harness shape (`REGEN_ENV`,
`REGEN_HINT`, write-then-compare, the stale/missing-golden sweep, the
identity-leak sweep) -- see `tests/goldens/README.md`'s `wasm/` section for
the SELF-SNAPSHOT discipline this belongs to (B12): read as "this is what D
currently lowers to", never as "this is correct".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from serpent._host import functions_by_name
from serpent.emitter import build_file, encode, opcodes
from serpent.emitter.frame import EmitError, Fn
from serpent.emitter.module import _IMMEDIATE_BY_NAME
from serpent.emitter.printer import _MNEMONIC_TEXT, disassemble

# ===========================================================================
# A hand-built module exercising every opcode Task 1 defines
# ===========================================================================

#: Two real, pinned host functions -- used for their real `(module, export)`
#: pair, so `disassemble`'s reverse lookup (`module.recompute_import_names`)
#: resolves them for real instead of against a fabricated pin entry.
_FAIL = functions_by_name["fail_with_error"]
_PUT = functions_by_name["put_contract_data"]

_MAGIC = b"\x00asm\x01\x00\x00\x00"


def _fnty(nparams: int, nresults: int) -> bytes:
    i64 = bytes([opcodes.VALTYPE_I64])
    return bytes([0x60]) + encode.vec([i64] * nparams) + encode.vec([i64] * nresults)


def _entry(nlocals: int, body: bytes) -> bytes:
    groups = [encode.uleb(nlocals) + bytes([opcodes.VALTYPE_I64])] if nlocals else []
    payload = encode.vec(groups) + body
    return encode.uleb(len(payload)) + payload


def _raw_body(fn: Fn) -> bytes:
    """``fn.finish()``'s items, concatenated as plain bytes.

    Every body in this file is built with ``fn.op`` directly for ``call``
    (never ``call_import``/``call_defined``), specifically so ``finish()``
    hands back a single raw byte run rather than a symbolic ``CallImport``/
    ``CallDefined`` review B1's pass 2 would need to resolve -- there is no
    pass 2 here, only ``disassemble`` decoding real bytes. Asserted, not just
    assumed, so a future edit that reintroduces a symbolic call site fails
    loudly here instead of mis-typing silently.
    """
    raw: list[bytes] = []
    for item in fn.finish():
        if not isinstance(item, bytes):
            raise TypeError(f"{fn.name}: expected only raw bytes, found {item!r}")
        raw.append(item)
    return b"".join(raw)


def _vocab_body() -> bytes:
    """One `frame.Fn` body touching every instruction `_IMMEDIATE_BY_NAME` names.

    Opens with `unreachable_()` (itself one of the covered opcodes) and stays
    there: `frame.Fn`'s own rule is that once a function is unreachable,
    every operand-stack height check becomes a no-op (§C.1/P13's
    polymorphic-stack behavior), so every remaining instruction can be
    emitted for its BYTES alone without also constructing a real, balanced
    computation -- while every STRUCTURAL rule (frame nesting, `else`/`end`
    pairing, local-index range) still runs in full, so this is still a real
    `Fn`-built body, not a hand-rolled byte string.
    """
    fn = Fn("vocab", nparams=1, nlocals_declared=2, results=("i64",))
    fn.unreachable_()  # UNREACHABLE
    fn.i32_const(5)  # I32_CONST
    fn.i64_const(7)  # I64_CONST
    fn.local_get(0)  # LOCAL_GET
    fn.local_set(1)  # LOCAL_SET
    fn.local_tee(2)  # LOCAL_TEE
    hidden = fn.new_local()
    fn.local_get(hidden)
    fn.pop("i64")
    fn.op(opcodes.I64_EQZ)  # I64_EQZ
    fn.push("i32")
    fn.binop_i64(opcodes.I64_EQ)
    fn.binop_i64(opcodes.I64_NE)
    fn.relop_i64(opcodes.I64_LT_S)
    fn.relop_i64(opcodes.I64_LT_U)
    fn.relop_i64(opcodes.I64_GT_S)
    fn.relop_i64(opcodes.I64_GT_U)
    fn.relop_i64(opcodes.I64_LE_S)
    fn.relop_i64(opcodes.I64_LE_U)
    fn.relop_i64(opcodes.I64_GE_S)
    fn.relop_i64(opcodes.I64_GE_U)
    fn.binop_i64(opcodes.I64_ADD)
    fn.binop_i64(opcodes.I64_SUB)
    fn.binop_i64(opcodes.I64_MUL)
    fn.binop_i64(opcodes.I64_DIV_S)
    fn.binop_i64(opcodes.I64_DIV_U)
    fn.binop_i64(opcodes.I64_REM_S)
    fn.binop_i64(opcodes.I64_REM_U)
    fn.binop_i64(opcodes.I64_AND)
    fn.binop_i64(opcodes.I64_OR)
    fn.binop_i64(opcodes.I64_XOR)
    fn.binop_i64(opcodes.I64_SHL)
    fn.binop_i64(opcodes.I64_SHR_S)
    fn.binop_i64(opcodes.I64_SHR_U)
    fn.pop("i64")
    fn.op(opcodes.I32_WRAP_I64)  # I32_WRAP_I64
    fn.push("i32")
    fn.pop("i32")
    fn.op(opcodes.I64_EXTEND_I32_U)  # I64_EXTEND_I32_U
    fn.push("i64")
    fn.pop("i64")
    fn.op(opcodes.I64_EXTEND32_S)  # I64_EXTEND32_S
    fn.push("i64")
    fn.pop("i32")
    fn.op(opcodes.I64_LOAD, encode.uleb(3), encode.uleb(0))  # I64_LOAD
    fn.push("i64")
    fn.pop("i64")
    fn.pop("i32")
    fn.op(opcodes.I64_STORE, encode.uleb(3), encode.uleb(8))  # I64_STORE
    fn.drop()  # DROP
    fn.op(opcodes.CALL, encode.uleb(0))  # CALL an import: fail_with_error
    fn.op(opcodes.CALL, encode.uleb(2))  # CALL a defined+exported function
    fn.op(opcodes.CALL, encode.uleb(3))  # CALL a defined, unexported function

    fn.begin_block(None, breakable=True)  # BLOCK
    fn.begin_loop()  # LOOP
    fn.br_continue()  # BR
    fn.br_if_break()  # BR_IF
    fn.end()  # END (closes loop)
    fn.end()  # END (closes block)

    fn.begin_if("i64")  # IF
    fn.i64_const(1)
    fn.else_()  # ELSE
    fn.i64_const(2)
    fn.end_if()  # END (closes if)
    fn.drop()

    fn.begin_block(None, breakable=True)
    fn.br_break()
    fn.end()

    fn.ret()  # RETURN
    return _raw_body(fn)


def _build_vocab_module() -> bytes:
    """Two imports, three defined functions (one exported, one exported under
    a different name, one internal) -- named so every call-rendering path in
    `disassemble` is exercised: an import by name, a defined+exported call by
    its export name, and a defined-but-unexported call as `$fn<N>`."""
    types = [
        _fnty(1, 1),  # 0: fail_with_error
        _fnty(3, 1),  # 1: put_contract_data
        _fnty(0, 0),  # 2: helper (void)
        _fnty(0, 1),  # 3: internal_only
        _fnty(1, 1),  # 4: vocab
    ]
    import_entries = [
        encode.wasm_name(_FAIL.module) + encode.wasm_name(_FAIL.export) + b"\x00" + encode.uleb(0),
        encode.wasm_name(_PUT.module) + encode.wasm_name(_PUT.export) + b"\x00" + encode.uleb(1),
    ]
    func_entries = [encode.uleb(2), encode.uleb(3), encode.uleb(4)]
    export_entries = [
        encode.wasm_name("helper_export") + b"\x00" + encode.uleb(2),
        encode.wasm_name("go") + b"\x00" + encode.uleb(4),
        encode.wasm_name("memory") + b"\x02" + encode.uleb(0),
    ]
    memory_entry = b"\x00" + encode.uleb(1)

    helper = Fn("helper", nparams=0, nlocals_declared=0, results=())
    helper.unreachable_()
    helper.ret()
    internal_only = Fn("internal_only", nparams=0, nlocals_declared=1, results=("i64",))
    internal_only.unreachable_()
    internal_only.ret()

    code_entries = [
        _entry(0, _raw_body(helper)),
        _entry(1, _raw_body(internal_only)),
        _entry(3, _vocab_body()),
    ]

    data_payload = b"hi"
    data_segment = (
        encode.uleb(0)
        + bytes([opcodes.I32_CONST])
        + encode.sleb(0)
        + bytes([opcodes.END])
        + encode.uleb(len(data_payload))
        + data_payload
    )

    return (
        _MAGIC
        + encode.section(1, encode.vec(types))
        + encode.section(2, encode.vec(import_entries))
        + encode.section(3, encode.vec(func_entries))
        + encode.section(5, encode.vec([memory_entry]))
        + encode.section(7, encode.vec(export_entries))
        + encode.section(10, encode.vec(code_entries))
        + encode.section(11, encode.vec([data_segment]))
    )


@pytest.fixture(scope="module")
def vocab_text() -> str:
    return disassemble(_build_vocab_module())


# ===========================================================================
# Every opcode renders
# ===========================================================================


def test_the_mnemonic_table_names_exactly_the_emitters_instruction_vocabulary() -> None:
    """The static half: `printer._MNEMONIC_TEXT`'s key set equals
    `module._IMMEDIATE_BY_NAME`'s -- the same completeness guard
    `_mnemonic_texts()` runs at call time, pinned here as its own assertion
    so a future divergence fails with a clean diff instead of only inside a
    `disassemble` call somewhere else."""
    assert set(_MNEMONIC_TEXT) == set(_IMMEDIATE_BY_NAME)


def test_every_instruction_mnemonic_appears_in_the_vocab_disassembly(vocab_text: str) -> None:
    missing = [text for text in _MNEMONIC_TEXT.values() if text not in vocab_text]
    assert not missing, missing


def test_the_void_and_i64_blocktypes_both_render(vocab_text: str) -> None:
    """`BLOCKTYPE_VOID` (a bare `block`/`loop`) and `BLOCKTYPE_I64`
    (`if (result i64)`) are the two non-instruction provenance-set members a
    body can carry; both appear in the vocab module. Checked against
    STRIPPED lines rather than a hardcoded indentation depth, so this stays
    true if the renderer's nesting depth ever changes."""
    stripped = [line.strip() for line in vocab_text.splitlines()]
    assert "block" in stripped
    assert "loop" in stripped
    assert "if (result i64)" in vocab_text


def test_the_i64_valtype_renders_in_locals_and_signatures(vocab_text: str) -> None:
    """`VALTYPE_I64`, the third non-instruction provenance-set member,
    appears in every local declaration and every param/result list."""
    assert "(local i64 i64 i64)" in vocab_text
    assert "(param i64) (result i64)" in vocab_text


# ===========================================================================
# Calls resolve to names, never bare indices
# ===========================================================================


def test_a_host_import_call_renders_by_its_registered_name(vocab_text: str) -> None:
    assert "call $fail_with_error" in vocab_text
    assert "call $0" not in vocab_text


def test_a_call_to_an_exported_defined_function_renders_by_its_export_name(
    vocab_text: str,
) -> None:
    assert "call $helper_export" in vocab_text


def test_a_call_to_an_unexported_defined_function_renders_as_fn_plus_its_defined_index(
    vocab_text: str,
) -> None:
    """`internal_only` is the module's second defined function (defined-space
    index 1: `helper` is 0, `vocab` itself is 2) and carries no export, so it
    must render as `$fn1` -- never a bare combined-space index (`3`)."""
    assert "call $fn1" in vocab_text
    assert "call 3" not in vocab_text


def test_the_export_and_import_sections_are_rendered_too(vocab_text: str) -> None:
    assert '(import "x" "5" (func $fail_with_error (param i64) (result i64)))' in vocab_text
    assert '(export "go" (func $go))' in vocab_text
    assert '(export "helper_export" (func $helper_export))' in vocab_text


# ===========================================================================
# An unrecognized byte is loud, never a guess
# ===========================================================================


def _tiny_module(body: bytes) -> bytes:
    """The smallest module carrying one defined function body and no imports."""
    i64 = bytes([opcodes.VALTYPE_I64])
    functype = b"\x60" + encode.vec([]) + encode.vec([i64])
    entry = encode.vec([]) + body
    return (
        _MAGIC
        + encode.section(1, encode.vec([functype]))
        + encode.section(3, encode.vec([encode.uleb(0)]))
        + encode.section(7, encode.vec([encode.wasm_name("go") + b"\x00" + encode.uleb(0)]))
        + encode.section(10, encode.vec([encode.uleb(len(entry)) + entry]))
    )


def test_a_byte_outside_the_emitters_vocabulary_is_a_loud_error() -> None:
    """`0xFC` opens every bulk-memory instruction -- none of which is in
    D's vocabulary (`module.check_no_bulk_memory`'s own premise) -- so a body
    carrying one is exactly the "byte this printer must never guess at"
    case."""
    body = bytes([0xFC, 0x00]) + bytes([opcodes.END])
    with pytest.raises(EmitError, match="not an instruction"):
        disassemble(_tiny_module(body))


def test_a_call_to_an_import_pair_outside_the_pin_is_a_loud_error() -> None:
    """`disassemble` reuses `module.recompute_import_names`, so an import the
    pin does not recognize fails there, loudly, exactly as review B1's net
    already establishes -- proof the printer really reuses that lookup rather
    than rendering a made-up name."""
    forged = _MAGIC + encode.section(
        2,
        encode.vec([encode.wasm_name("x") + encode.wasm_name("zz") + b"\x00" + encode.uleb(0)]),
    )
    with pytest.raises(EmitError, match="not in the pin"):
        disassemble(forged)


# ===========================================================================
# Never cited as evidence -- the per-fixture SELF-SNAPSHOTs (tests/goldens/README.md)
# ===========================================================================

_REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = Path(__file__).resolve().parents[1] / "goldens" / "wasm"

#: The environment variable that rewrites every golden instead of comparing.
REGEN_ENV = "SERPENT_REGEN_GOLDENS"
REGEN_HINT = f"{REGEN_ENV}=1 uv run pytest tests/unit/test_emitter_printer.py"

#: Every snapshotted contract, as `(source path relative to the repo root,
#: golden stem)`.
#:
#: **This list is this file's OWN inventory and is deliberately not
#: `test_emitter_end_to_end.py`'s `FIXTURES`** (review M8): a golden is a file on
#: disk keyed by a stem, and `test_no_stale_or_missing_wasm_goldens` asserts the
#: directory listing EXACTLY, so the two lists have different jobs and a shared
#: one would make "add a fixture" silently mean "add a golden".
#:
#: The rows: the Phase 0 re-author, the one real richly-shaped contract, the two
#: promoted sandbox contracts, M1-E's `token_style_canonical` (the contract that
#: keeps the canonical `env.events().publish(topics, data)` spelling covered now
#: that `token_style` publishes through `Event.publish`), M1-E's shipped
#: `examples/`, and M1-E2's sixth example `shapes.py` (the tagged union and the
#: int enum).
#:
#: The pair replaced a bare stem tuple when the examples arrived: the stems were
#: enough only while every source sat in `tests/fixtures/`, and `examples/` is a
#: second root. The stems stay distinct across both roots (`counter` vs
#: `sandbox_counter`), which is what keeps one flat golden directory workable.
#:
#: `counter.wat.txt` is, below its header, necessarily identical to
#: `sandbox_counter.wat.txt`: `examples/counter.py` is the same contract, and
#: `test_a_promoted_sandbox_copy_builds_the_same_module_as_its_original` is where
#: that identity is ASSERTED. The duplicate snapshot is kept anyway, because a
#: golden is a per-source snapshot and a missing one would read as "this shipped
#: example is not snapshotted".
FIXTURE_SOURCES: tuple[tuple[str, str], ...] = (
    ("tests/fixtures/sandbox_counter.py", "sandbox_counter"),
    ("tests/fixtures/sandbox_hello_world.py", "sandbox_hello_world"),
    ("tests/fixtures/spike1_reauthored.py", "spike1_reauthored"),
    ("tests/fixtures/token_style.py", "token_style"),
    ("tests/fixtures/token_style_canonical.py", "token_style_canonical"),
    ("examples/counter.py", "counter"),
    ("examples/errors.py", "errors"),
    ("examples/structs.py", "structs"),
    ("examples/events.py", "events"),
    ("examples/allowance_token.py", "allowance_token"),
    ("examples/shapes.py", "shapes"),
)

#: The golden stems, derived from the pairs above -- the name every test here
#: parametrizes over.
FIXTURE_NAMES: tuple[str, ...] = tuple(name for _source, name in FIXTURE_SOURCES)

_SOURCE_BY_NAME: dict[str, str] = {name: source for source, name in FIXTURE_SOURCES}

_HEADER_TEMPLATE = """\
;; SELF-SNAPSHOT (tests/goldens/README.md's third class, the `wasm/` rows):
;; this is what `serpent.emitter.printer.disassemble` currently renders for
;; `serpent.emitter.build_file("{source}")`.
;; It must NEVER be cited as evidence the wasm bytes -- or this rendering of
;; them -- are CORRECT (B12). Regenerate with
;;     {hint}
;; and READ THE DIFF: a change here is a behavioral change to what sub-plan D
;; emits, not noise.
"""


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.wat.txt"


def render_golden(name: str) -> str:
    source = _SOURCE_BY_NAME[name]
    built = build_file(_REPO_ROOT / source)
    header = _HEADER_TEMPLATE.format(source=source, hint=REGEN_HINT)
    return header + disassemble(built.wasm)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_golden_wasm_disassembly_snapshot(name: str) -> None:
    """The disassembly of `name`'s current build matches its stored golden."""
    rendered = render_golden(name)
    path = golden_path(name)
    if os.environ.get(REGEN_ENV) == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered)
    if not path.exists():
        pytest.fail(f"no golden for {name!r} at {path}; create it with `{REGEN_HINT}`")
    stored = path.read_text()
    assert stored == rendered, (
        f"the disassembly for {name!r} no longer matches {path.name}.\n"
        f"If the change is intended, regenerate with `{REGEN_HINT}` and REVIEW the diff -- "
        "a golden diff is a behavioral change to what sub-plan D emits, not noise."
    )


def test_no_stale_or_missing_wasm_goldens() -> None:
    stored = sorted(path.name for path in GOLDEN_DIR.glob("*.wat.txt"))
    assert stored == sorted(f"{name}.wat.txt" for name in FIXTURE_NAMES)


def test_the_wasm_goldens_have_no_identity_leaks() -> None:
    repo_root = str(Path(__file__).resolve().parents[2])
    for name in FIXTURE_NAMES:
        text = golden_path(name).read_text()
        assert " object at 0x" not in text, name
        assert "0x" not in text, name
        assert repo_root not in text, name
        assert "/Users/" not in text, name


def test_the_wasm_golden_rendering_is_deterministic() -> None:
    """Same fixture, rebuilt from scratch -- same disassembly, across two
    independent builds (mirrors `test_the_rendering_is_deterministic` in
    `test_frontend_goldens.py`)."""
    for name in FIXTURE_NAMES:
        first = render_golden(name)
        second = render_golden(name)
        assert first == second, name
