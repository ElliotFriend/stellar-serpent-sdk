"""`serpent.emitter.module` + `sections`: section assembly and the three customs.

The module under test is the first thing in this sub-plan that produces a WHOLE
wasm module, so this file's decoder is deliberately its own: a hand-rolled
section walk over the emitted bytes (`_sections` below), never
`validate.iter_sections`. An anatomy test that reads the module through the
emitter's own validator would pass a matched pair of bugs, which is exactly
what dossier B.1's table exists to prevent.

Evidence classes (B12): every byte assertion here is either **structural**
(section ids, counts, indices decoded and compared against the pin's own
`HostFn.wasm_params`/`wasm_result`) or a **delegation check** (a custom
section's payload byte-equals what `serpent.spec` returns for the same input).
There is no golden for the code section or `contractmetav0` -- S8 forbids both.
"""

import dataclasses
import importlib.metadata
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from stellar_sdk import xdr
from xdrlib3 import Unpacker

import serpent
from serpent import errors, val
from serpent._host import HOST_FUNCTIONS
from serpent.compiler.frontend import CompiledModule, compile_module
from serpent.emitter import encode, module, opcodes, sections
from serpent.emitter.frame import BuildLimitError, CodeItem, EmitError
from serpent.spec import build_env_meta, build_meta, build_spec_entries
from serpent.types import String
from tests.harness.engine import HostError, MiniHost
from tests.harness.objects import STORAGE_PERSISTENT, ObjectStore

# ===========================================================================
# Sources under test
# ===========================================================================

#: Mirrors `sandbox/counter.py`, the dossier's live MEMORYLESS example (B.7's
#: "memoryless path" row, verified there as `needs memory: False`). Inlined
#: rather than read from `sandbox/` so this test never depends on a scratch
#: directory an author is invited to break on purpose.
COUNTER_SRC = '''\
from serpent import U32, Env, Symbol, contract, contracterror, errorcode


@contracterror
class Error:
    MaxReached = errorcode(1)


@contract
class Counter:
    def increment(self, env: Env, step: U32) -> U32:
        """Add `step` to the running total; refuse to pass 1000."""
        total = env.storage().persistent().get(Symbol("TOTAL"), U32, default=U32(0))
        total = total + step
        if total > U32(1000):
            raise Error.MaxReached
        env.storage().persistent().set(Symbol("TOTAL"), total)
        return total

    def total(self, env: Env) -> U32:
        """Read the running total without changing it."""
        return env.storage().persistent().get(Symbol("TOTAL"), U32, default=U32(0))
'''

#: A pooled `String` literal: the pool is non-empty AND a `*_linear_memory`
#: host function is imported, so E10 says sections 5/11 and the memory export
#: all appear.
NAMER_SRC = """\
from serpent import Env, String, contract


@contract
class Namer:
    def name(self, env: Env) -> String:
        return String("serpent phase zero")
"""

#: Review B8's case: C reports `needs_memory=False` (no literals, no LM host
#: call) and D still needs a page, because `u128_mul` is a two-result runtime
#: part and writes its low limb to scratch.
WIDE_SRC = """\
from serpent import U128, Env, contract


@contract
class Mul:
    def product(self, env: Env, a: U128, b: U128) -> U128:
        return a * b
"""

#: A constructor, an export, and an INTERNAL helper -- the export-section
#: question (B.1 row 7: internal functions are not exported).
HELPED_SRC = """\
from serpent import U32, Env, Symbol, contract


@contract
class Helped:
    def __init__(self, env: Env, seed: U32) -> None:
        env.storage().instance().set(Symbol("S"), seed)

    def go(self, env: Env, x: U32) -> U32:
        return self._double(x)

    def _double(self, x: U32) -> U32:
        return x + x
"""

#: The topic convention (M1-E Task 5), spelled the only way a contract may
#: spell it: `Annotated` and `topic` imported from the `serpent` ROOT (SPT2005).
#: `publish` on the instance is still deferred to Task 6, so the contract emits
#: the canonical `env.events().publish(topics, data)` line.
EVENT_SRC = """\
from serpent import (
    U32,
    Address,
    Annotated,
    Env,
    Event,
    Symbol,
    contract,
    contractevent,
    topic,
)


@contractevent
class Moved(Event):
    who: Annotated[Address, topic]
    amount: U32


@contract
class Mover:
    def move(self, env: Env, who: Address, amount: U32) -> None:
        env.events().publish((Symbol("moved"), who), amount)
"""

#: One `String` literal larger than the whole pool budget (P12: the pool must
#: not reach `SCRATCH_BASE`, 0x1000).
TOO_MANY_LITERALS_SRC = """\
from serpent import Env, String, contract


@contract
class Big:
    def name(self, env: Env) -> String:
        return String("%s")
""" % ("x" * 5000)

_TOKEN_STYLE = Path(__file__).parent.parent / "fixtures" / "token_style.py"

_MAGIC = b"\x00asm\x01\x00\x00\x00"

#: `0xFC` opens every bulk-memory instruction, so it is the byte the emitter's
#: vocabulary must never carry -- and therefore a byte no body can hold.
_BULK_MEMORY_PREFIX = 0xFC


def compiled(src: str, path: str = "contracts/t.py") -> CompiledModule:
    return compile_module(src, path)


def build(src: str, *, meta: dict[str, str] | None = None, version: str | None = None) -> bytes:
    return module.assemble(compiled(src), meta={} if meta is None else meta, version=version).wasm


# ===========================================================================
# This file's own decoder (see the module docstring)
# ===========================================================================


def _uleb(b: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        byte = b[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7


def _name(b: bytes, i: int) -> tuple[str, int]:
    length, i = _uleb(b, i)
    return b[i : i + length].decode("utf-8"), i + length


def _sections(wasm: bytes) -> list[tuple[int, bytes]]:
    """Every section as `(id, payload)`, in the order the bytes carry them."""
    assert wasm[:8] == _MAGIC, "not a wasm module"
    out: list[tuple[int, bytes]] = []
    i = 8
    while i < len(wasm):
        sid = wasm[i]
        size, i = _uleb(wasm, i + 1)
        out.append((sid, wasm[i : i + size]))
        i += size
    return out


def _section(wasm: bytes, sid: int) -> bytes | None:
    found = [payload for got, payload in _sections(wasm) if got == sid]
    assert len(found) <= 1, f"section {sid} appears {len(found)} times"
    return found[0] if found else None


def _customs(wasm: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for sid, payload in _sections(wasm):
        if sid == 0:
            name, i = _name(payload, 0)
            out[name] = payload[i:]
    return out


def _functypes(wasm: bytes) -> list[tuple[list[int], list[int]]]:
    payload = _section(wasm, 1)
    assert payload is not None
    count, i = _uleb(payload, 0)
    out: list[tuple[list[int], list[int]]] = []
    for _ in range(count):
        assert payload[i] == 0x60, "a functype must start with 0x60"
        nparams, i = _uleb(payload, i + 1)
        params = list(payload[i : i + nparams])
        i += nparams
        nresults, i = _uleb(payload, i)
        results = list(payload[i : i + nresults])
        i += nresults
        out.append((params, results))
    return out


def _imports(wasm: bytes) -> list[tuple[str, str, int, int]]:
    """Each import as `(module, field, kind, index)`."""
    payload = _section(wasm, 2)
    if payload is None:
        return []
    count, i = _uleb(payload, 0)
    out: list[tuple[str, str, int, int]] = []
    for _ in range(count):
        mod, i = _name(payload, i)
        field, i = _name(payload, i)
        kind = payload[i]
        index, i = _uleb(payload, i + 1)
        out.append((mod, field, kind, index))
    return out


def _funcsec(wasm: bytes) -> list[int]:
    payload = _section(wasm, 3)
    assert payload is not None
    count, i = _uleb(payload, 0)
    out: list[int] = []
    for _ in range(count):
        typeidx, i = _uleb(payload, i)
        out.append(typeidx)
    return out


def _exports(wasm: bytes) -> list[tuple[str, int, int]]:
    """Each export as `(name, kind, index)`."""
    payload = _section(wasm, 7)
    assert payload is not None
    count, i = _uleb(payload, 0)
    out: list[tuple[str, int, int]] = []
    for _ in range(count):
        name, i = _name(payload, i)
        kind = payload[i]
        index, i = _uleb(payload, i + 1)
        out.append((name, kind, index))
    return out


def _code_entries(wasm: bytes) -> list[bytes]:
    payload = _section(wasm, 10)
    assert payload is not None
    count, i = _uleb(payload, 0)
    out: list[bytes] = []
    for _ in range(count):
        size, i = _uleb(payload, i)
        out.append(payload[i : i + size])
        i += size
    return out


def _locals_decl(entry: bytes) -> list[tuple[int, int]]:
    """The `(count, valtype)` groups a code entry declares, and nothing else."""
    groups, i = _uleb(entry, 0)
    out: list[tuple[int, int]] = []
    for _ in range(groups):
        count, i = _uleb(entry, i)
        out.append((count, entry[i]))
        i += 1
    return out


def _data_segment(wasm: bytes) -> tuple[int, int, bytes] | None:
    """The single active segment as `(memidx, offset, payload)`."""
    payload = _section(wasm, 11)
    if payload is None:
        return None
    count, i = _uleb(payload, 0)
    assert count == 1, "D emits exactly one active segment (B.1 row 11)"
    memidx, i = _uleb(payload, i)
    assert payload[i] == opcodes.I32_CONST
    offset, i = _uleb(payload, i + 1)
    assert payload[i] == opcodes.END
    i += 1
    length, i = _uleb(payload, i)
    return memidx, offset, payload[i : i + length]


def _meta_pairs(payload: bytes) -> list[tuple[str, str]]:
    unpacker = Unpacker(payload)
    out: list[tuple[str, str]] = []
    while unpacker.get_position() < len(payload):
        entry = xdr.SCMetaEntry.unpack(unpacker)
        assert entry.v0 is not None
        out.append((entry.v0.key.decode(), entry.v0.val.decode()))
    return out


# ===========================================================================
# B.1: the section order table
# ===========================================================================


def test_the_module_opens_with_the_magic_and_version() -> None:
    assert build(COUNTER_SRC)[:8] == _MAGIC


def test_a_memoryless_module_carries_exactly_B1s_sections_in_id_order() -> None:
    """Dossier B.1, read row by row: type, import, function, export, code, then
    the three customs. No table (4), no memory (5), no globals (6), no start
    (8), no element (9), no data (11), no datacount (12)."""
    ids = [sid for sid, _payload in _sections(build(COUNTER_SRC))]
    assert ids == [1, 2, 3, 7, 10, 0, 0, 0]


def test_non_custom_section_ids_strictly_ascend() -> None:
    """The binary format fixes the order by id: a section may be omitted, never
    reordered. Customs (id 0) are exempt and may appear anywhere."""
    for src in (COUNTER_SRC, NAMER_SRC, WIDE_SRC, HELPED_SRC):
        ids = [sid for sid, _payload in _sections(build(src)) if sid != 0]
        assert ids == sorted(set(ids)), src.splitlines()[0]


def test_the_three_custom_sections_come_last_and_in_B1s_order() -> None:
    wasm = build(COUNTER_SRC)
    names = [_name(payload, 0)[0] for sid, payload in _sections(wasm) if sid == 0]
    assert names == [
        sections.ENV_META_SECTION_NAME,
        sections.SPEC_SECTION_NAME,
        sections.META_SECTION_NAME,
    ]
    assert [sid for sid, _p in _sections(wasm)][-3:] == [0, 0, 0]


def test_there_is_no_start_section_ever() -> None:
    """S23, restated as a test rather than a comment: "No start section"."""
    for src in (COUNTER_SRC, NAMER_SRC, WIDE_SRC, HELPED_SRC):
        assert _section(build(src), 8) is None


# ===========================================================================
# Section 1: type -- deduped, and built from the PIN (B3)
# ===========================================================================


def test_the_type_section_is_deduped() -> None:
    """The counter's four imports have three distinct shapes and its two
    functions add one more, so a deduped section holds four functypes."""
    types = _functypes(build(COUNTER_SRC))
    assert len(types) == len(set(map(str, types))) == 4


def test_every_import_points_at_the_functype_the_pin_describes() -> None:
    """Review B3: the import's shape comes from `HostFn.wasm_params`/
    `wasm_result`, not from arity -- so a re-pin that gives a host function an
    i32 argument breaks HERE rather than silently emitting an i64."""
    wasm = build(COUNTER_SRC)
    types = _functypes(wasm)
    i64 = opcodes.VALTYPE_I64
    for mod, field, kind, typeidx in _imports(wasm):
        host_fn = next(fn for fn in HOST_FUNCTIONS if fn.module == mod and fn.export == field)
        assert kind == 0x00, "every import D emits is a function"
        params, results = types[typeidx]
        assert params == [i64 for _t in host_fn.wasm_params]
        assert results == [i64]
        assert len(params) == len(host_fn.wasm_params)


def test_every_defined_function_points_at_its_own_shape() -> None:
    wasm = build(COUNTER_SRC)
    types = _functypes(wasm)
    i64 = opcodes.VALTYPE_I64
    # `increment(step)` then `total()`, both exports and so both `-> i64`.
    assert [types[t] for t in _funcsec(wasm)] == [([i64], [i64]), ([], [i64])]


# ===========================================================================
# Section 2: import -- first-use order, and ONLY what was emitted
# ===========================================================================


def test_the_import_section_is_in_lowering_FIRST_USE_order() -> None:
    """Review B1's binding order: import indices are minted as lowering names
    them, and that order IS the section's. `increment` lowers its prologue
    (`fail_with_error`) before the guarded storage read (`has`, then `get`) and
    the write (`put`), which is exactly this order."""
    assert module.recompute_import_names(build(COUNTER_SRC)) == (
        "fail_with_error",
        "has_contract_data",
        "get_contract_data",
        "put_contract_data",
    )


def test_only_host_functions_the_code_actually_calls_are_imported() -> None:
    """S6/M4: an unused import is dead bytes plus a false protocol input. The
    counter's reachable set equals its used set, so the emitted set must equal
    it exactly -- no `extend_contract_data_ttl`, no `del_contract_data`."""
    c = compiled(COUNTER_SRC)
    emitted = set(module.recompute_import_names(module.assemble(c, meta={}, version=None).wasm))
    assert emitted == set(c.host_fns_used)
    assert "extend_contract_data_ttl" not in emitted


def test_the_import_set_may_name_functions_C_deliberately_omitted() -> None:
    """Review M4/M5's other direction: the 128-bit piece constructors and
    accessors are in `OMITTED_FAMILIES` -- C never lists them, D's form choice
    is what needs them, and every one is pinned UNGATED so the declared
    protocol is unaffected."""
    c = compiled(WIDE_SRC)
    emitted = set(module.recompute_import_names(module.assemble(c, meta={}, version=None).wasm))
    assert "obj_from_u128_pieces" in emitted
    assert "obj_from_u128_pieces" not in c.host_fns_reachable


def test_every_import_field_name_fits_the_ten_character_ABI(  # S23
) -> None:
    for src in (COUNTER_SRC, NAMER_SRC, WIDE_SRC):
        for _mod, field, _kind, _typeidx in _imports(build(src)):
            assert len(field) <= 10, field


def test_recompute_import_names_refuses_a_pair_the_pin_does_not_know() -> None:
    """Review B1's safety net is only a net if it fails loudly: an import entry
    naming a `(module, field)` pair outside the pin means the emitted section
    and the registry have diverged, which is the wrong-target class the
    symbolic call sites exist to rule out."""
    forged = _MAGIC + encode.section(
        2,
        encode.vec([encode.wasm_name("x") + encode.wasm_name("zz") + b"\x00" + encode.uleb(0)]),
    )
    with pytest.raises(EmitError, match="not in the pin"):
        module.recompute_import_names(forged)


# ===========================================================================
# Review B1's net, second half: the emitted call immediates (fix round 1, I2)
# ===========================================================================


def _tiny_module(body: bytes) -> bytes:
    """The smallest module carrying one body -- no imports, one defined function."""
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


def test_split_code_entries_returns_each_bodys_raw_bytes() -> None:
    """The shared helper (Task 14: factored out so the printer's disassembler
    and `_decode_code_section` walk the same size-prefixed vector exactly
    once) hands back each entry UNTOUCHED -- `_decode_code_section` decodes
    call targets out of it, `serpent.emitter.printer` decodes everything."""
    body_a = b"\x00" + bytes([opcodes.END])
    body_b = bytes([opcodes.END])
    payload = encode.vec([encode.uleb(len(body_a)) + body_a, encode.uleb(len(body_b)) + body_b])
    assert module.split_code_entries(payload) == [body_a, body_b]


def test_split_code_entries_refuses_a_truncated_body() -> None:
    """One home for this error text now: a body that declares more bytes than
    the section actually carries is a truncated module, caught here rather
    than independently (and possibly divergently) in each caller."""
    payload = encode.uleb(1) + encode.uleb(5) + b"\x0b"  # declares 5 bytes, only 1 remains
    with pytest.raises(EmitError, match="truncated code section"):
        module.split_code_entries(payload)


def test_check_call_targets_accepts_every_module_the_emitter_assembles() -> None:
    for src in (COUNTER_SRC, NAMER_SRC, WIDE_SRC, HELPED_SRC):
        module.check_call_targets(build(src))


def test_check_call_targets_refuses_a_call_past_the_index_space() -> None:
    """The wrong-target class, at the only place it can be seen: the immediate.
    Note wasm-tools would accept a call to the wrong function of the right
    arity -- every host function shares the all-i64 shape (B3) -- so an
    out-of-range target is the strongest statement a structural check can
    make."""
    body = bytes([opcodes.CALL]) + encode.uleb(9) + bytes([opcodes.END])
    with pytest.raises(EmitError, match="calls function 9"):
        module.check_call_targets(_tiny_module(body))


def test_the_call_walk_is_a_DECODE_not_a_scan_for_0x10_bytes() -> None:
    """`0x10` is `call`, and it is also a perfectly ordinary byte inside an
    `i64.const` operand or a local index. A body whose only `0x10` is an operand
    must yield NO call targets -- a scan would invent one and then read the next
    instruction byte as its index."""
    body = (
        bytes([opcodes.I64_CONST])
        + encode.sleb(0x10)
        + bytes([opcodes.LOCAL_SET])
        + encode.uleb(0x10)
        + bytes([opcodes.END])
    )
    module.check_call_targets(_tiny_module(body))
    assert module._call_targets(encode.vec([]) + body, "probe") == []


def test_the_call_walk_refuses_a_byte_that_is_not_in_the_emitters_vocabulary() -> None:
    """A decoder that skipped an unknown opcode would desynchronize and then
    report whatever the following bytes looked like."""
    body = bytes([_BULK_MEMORY_PREFIX, 0x0B]) + bytes([opcodes.END])
    with pytest.raises(EmitError, match="not an instruction"):
        module.check_call_targets(_tiny_module(body))


def test_the_immediate_table_must_be_extended_with_the_opcode_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The decoder's own drift guard: an opcode whose operand length this table
    does not know would make every later call target in that body meaningless,
    so the mismatch is loud at the table rather than silent at the walk."""
    monkeypatch.setattr(opcodes, "I64_CLZ", 0x79, raising=False)
    monkeypatch.setattr(opcodes, "SPEC_PINNED", opcodes.SPEC_PINNED | {"I64_CLZ"})
    with pytest.raises(EmitError, match="must be extended"):
        module.check_call_targets(build(COUNTER_SRC))


def test_a_defined_call_serialized_without_the_import_offset_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mutation the positional half exists for: `CallDefined(d)` must bake
    `n_imports + d`, and dropping `n_imports` produces a module that VALIDATES
    (the target is in range, and every functype is all-i64) while calling a host
    import where it meant to call a runtime part. Only comparing the emitted
    immediates against the symbolic sites, position by position, sees it."""
    original = module._serialize

    def dropped_offset(
        where: str,
        items: Sequence[CodeItem],
        import_index: Mapping[str, int],
        n_imports: int,
        n_defined: int,
    ) -> bytes:
        return original(where, items, import_index, 0, n_defined)

    monkeypatch.setattr(module, "_serialize", dropped_offset)
    with pytest.raises(EmitError, match="symbolic call sites resolve to"):
        module.assemble(compiled(WIDE_SRC), meta={}, version=None)


def test_a_body_serialized_against_a_STALE_import_map_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same net from the import side: a map that resolves every host call to
    index 0 keeps the module in range and structurally valid, and calls
    `fail_with_error` everywhere the contract meant to read storage."""
    original = module._serialize

    def collapsed_map(
        where: str,
        items: Sequence[CodeItem],
        import_index: Mapping[str, int],
        n_imports: int,
        n_defined: int,
    ) -> bytes:
        return original(where, items, dict.fromkeys(import_index, 0), n_imports, n_defined)

    monkeypatch.setattr(module, "_serialize", collapsed_map)
    with pytest.raises(EmitError, match="symbolic call sites resolve to"):
        module.assemble(compiled(COUNTER_SRC), meta={}, version=None)


# ===========================================================================
# Sections 3/7/10: function, export, code
# ===========================================================================


def test_exports_index_past_the_imports() -> None:
    wasm = build(COUNTER_SRC)
    n_imports = len(_imports(wasm))
    assert _exports(wasm) == [("increment", 0x00, n_imports), ("total", 0x00, n_imports + 1)]


def test_internal_functions_and_runtime_parts_are_not_exported() -> None:
    """B.1 row 7: one export per EXPORT/CONSTRUCTOR `FuncIR`. `_double` is
    INTERNAL and the linked parts are D's own, so neither is an ABI surface."""
    helped = _exports(build(HELPED_SRC))
    assert [name for name, _kind, _idx in helped] == ["__constructor", "go"]
    wide = _exports(build(WIDE_SRC))
    assert [name for name, _kind, _idx in wide if _kind == 0x00] == ["product"]


def test_the_function_section_covers_the_parts_as_well_as_the_methods() -> None:
    """Parts are appended to the DEFINED index space after the module's own
    functions (`EmitCtx.ensure_part`), so section 3 and section 10 both have
    one entry per method PLUS one per linked part."""
    wasm = build(WIDE_SRC)
    # One method plus unbox_u128, u128_mul, mul64_wide, box_u128.
    assert len(_funcsec(wasm)) == len(_code_entries(wasm)) == 5


def test_the_code_section_declares_one_i64_group_sized_by_the_functions_locals() -> None:
    """B.1 row 10: the spike emitted a single `i64` group, and every local D
    allocates -- declared or hidden -- is an i64 `Val` word."""
    entries = _code_entries(build(COUNTER_SRC))
    decls = [_locals_decl(e) for e in entries]
    assert all(len(d) <= 1 for d in decls)
    assert all(valtype == opcodes.VALTYPE_I64 for d in decls for _count, valtype in d)
    # `increment` needs more slots than `total`, and both need some.
    counts = [d[0][0] if d else 0 for d in decls]
    assert counts[0] > counts[1] > 0


def test_a_function_with_no_locals_declares_an_empty_group_vector() -> None:
    """`Namer.name` allocates nothing, so its declaration vector is empty
    rather than a zero-count group (which wastes two bytes and reads as a lie)."""
    entry = _code_entries(build(NAMER_SRC))[0]
    assert _locals_decl(entry) == []
    assert entry[0] == 0x00


def test_every_body_ends_in_the_single_END_finish_already_appended() -> None:
    """Task 2's ledger note, held in place: `Fn.finish()` appends the trailing
    `end`, so pass 2 must not add a second one."""
    for entry in _code_entries(build(COUNTER_SRC)):
        assert entry[-1] == opcodes.END
        assert entry[-2] != opcodes.END


# ===========================================================================
# Sections 5/11 + the memory export: ruling E10, both directions
# ===========================================================================


def test_a_memoryless_module_omits_the_memory_the_data_and_the_export() -> None:
    wasm = build(COUNTER_SRC)
    assert compiled(COUNTER_SRC).needs_memory is False
    assert _section(wasm, 5) is None
    assert _section(wasm, 11) is None
    assert [name for name, kind, _idx in _exports(wasm) if kind == 0x02] == []


def test_a_pooled_literal_brings_the_memory_the_data_segment_and_the_export() -> None:
    wasm = build(NAMER_SRC)
    assert compiled(NAMER_SRC).needs_memory is True
    # Flags 0x00 (a minimum, no maximum), one page.
    assert _section(wasm, 5) == b"\x01\x00\x01"
    assert _data_segment(wasm) == (0, 0, b"serpent phase zero")
    assert [(name, idx) for name, kind, idx in _exports(wasm) if kind == 0x02] == [("memory", 0)]


def test_a_two_result_runtime_part_forces_a_memory_C_could_not_foresee() -> None:
    """Review B8: `u128_mul` returns its high limb and writes `lo` to scratch,
    so D needs a page even though C -- which sees no literal and no
    linear-memory host call -- reported `needs_memory=False`. That is correct,
    not an inconsistency, and the data section stays absent because the POOL is
    still empty."""
    c = compiled(WIDE_SRC)
    assert c.needs_memory is False
    wasm = module.assemble(c, meta={}, version=None).wasm
    assert _section(wasm, 5) == b"\x01\x00\x01"
    assert _section(wasm, 11) is None
    assert [name for name, kind, _idx in _exports(wasm) if kind == 0x02] == ["memory"]


def test_the_E10_consistency_assertion_fires_on_the_literal_LM_component() -> None:
    """The restricted assertion (review B8): C saying `needs_memory=False` while
    D pools a literal or imports a linear-memory host function means the two
    disagree about a fact C really did compute -- a compiler bug, not a
    contract error."""
    lying = dataclasses.replace(compiled(NAMER_SRC), needs_memory=False)
    with pytest.raises(EmitError, match="needs_memory"):
        module.assemble(lying, meta={}, version=None)


#: Two `String` literals whose SORTED inventory order ("aaa", "zzz") is the
#: reverse of their source order. Interning lazily as lowering reaches them
#: would lay the pool out as `zzzaaa`; seeding the whole inventory first lays it
#: out as `aaazzz`.
TWO_LITERALS_SRC = """\
from serpent import Env, String, contract


@contract
class Two:
    def zed(self, env: Env) -> String:
        return String("zzz")

    def ay(self, env: Env) -> String:
        return String("aaa")
"""


def test_the_pool_is_seeded_from_the_INVENTORY_not_from_lowering_order() -> None:
    """Ruling E7 / Task 7's carried constraint: the FULL `LiteralInventory` is
    seeded before any body is lowered, so every pool offset is a pure function
    of the (deduplicated, sorted) inventory rather than of which method happened
    to be compiled first. `Memory.intern` appends an unseeded blob silently, so
    nothing else would notice."""
    c = compiled(TWO_LITERALS_SRC)
    assert c.literals.strings == ("aaa", "zzz")
    segment = _data_segment(module.assemble(c, meta={}, version=None).wasm)
    assert segment == (0, 0, b"aaazzz")


def test_a_literal_pool_that_reaches_scratch_is_a_user_visible_build_limit() -> None:
    """P12's first guard, reported through the layout: a pool that reaches
    `0x1000` would overwrite the scratch region. `BuildLimitError` (not bare
    `EmitError`) is what Task 11 turns into SPT8002, so the discriminator is
    part of the contract."""
    src = TOO_MANY_LITERALS_SRC
    with pytest.raises(BuildLimitError) as excinfo:
        module.assemble(compiled(src), meta={}, version=None)
    assert excinfo.value.limit == "pool"


def test_the_pass_2_import_index_is_re_derived_from_the_bytes_that_shipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review B1's net, proven wired: pass 2 resolved every `CallImport` against
    `EmitCtx.import_order`, and `assemble` re-reads the emitted section to check
    the two agree. Make the recompute disagree and the assembly must refuse
    rather than hand back a module whose call indices point elsewhere."""

    def wrong(_wasm: bytes) -> tuple[str, ...]:
        return ("obj_cmp",)

    monkeypatch.setattr(module, "recompute_import_names", wrong)
    with pytest.raises(EmitError, match="call targets"):
        module.assemble(compiled(COUNTER_SRC), meta={}, version=None)


def test_the_data_section_is_omitted_for_an_empty_pool_even_with_a_memory() -> None:
    """B.1 row 11: emitted only when the pool is non-empty -- an empty active
    segment is bytes that say nothing."""
    assert _section(build(WIDE_SRC), 11) is None


# ===========================================================================
# M13 / P15: the linear-memory ABI check, on a named seam (review m6)
# ===========================================================================


def test_check_linear_memory_abi_accepts_the_literal_memory_export() -> None:
    module.check_linear_memory_abi(["string_new_from_linear_memory"], ("memory",))


def test_check_linear_memory_abi_refuses_an_LM_import_with_no_memory() -> None:
    with pytest.raises(EmitError, match="linear-memory"):
        module.check_linear_memory_abi(["string_new_from_linear_memory"], ())


def test_check_linear_memory_abi_refuses_a_MISSPELLED_memory_export() -> None:
    """P15: the check spells `"memory"` itself instead of reading
    `MEMORY_EXPORT_NAME`, so a wrong constant cannot satisfy the check by
    agreeing with itself."""
    with pytest.raises(EmitError, match="memory"):
        module.check_linear_memory_abi(["map_new_from_linear_memory"], ("mem",))


def test_check_linear_memory_abi_refuses_two_memory_exports() -> None:
    with pytest.raises(EmitError, match="exactly one"):
        module.check_linear_memory_abi(["map_new_from_linear_memory"], ("memory", "memory"))


def test_check_linear_memory_abi_is_silent_when_no_LM_function_is_imported() -> None:
    module.check_linear_memory_abi(["get_contract_data", "obj_cmp"], ())


def test_the_ABI_check_runs_BEFORE_any_byte_is_laid_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M13's whole point is that the failure precedes the artifact. Making the
    check raise proves `assemble` consults it rather than merely defining it."""

    def boom(_names: object, _exports: object) -> None:
        raise EmitError("m13 sentinel")

    monkeypatch.setattr(module, "check_linear_memory_abi", boom)
    with pytest.raises(EmitError, match="m13 sentinel"):
        module.assemble(compiled(NAMER_SRC), meta={}, version=None)


def test_the_memory_export_name_constant_matches_the_literal_the_check_spells() -> None:
    assert module.MEMORY_EXPORT_NAME == "memory"


# ===========================================================================
# Section 12's absence: no bulk-memory instruction exists to require it
# ===========================================================================


def test_no_bulk_memory_instruction_is_in_the_emitters_vocabulary() -> None:
    """B.1 row 12: DataCount is mandatory only when `memory.init`/`data.drop`
    appear. Every instruction byte D writes comes from `opcodes`, so the sound
    check is over the VOCABULARY -- scanning the emitted bytes would
    false-positive on any `0xFC` inside an LEB operand."""
    module.check_no_bulk_memory()


def test_check_no_bulk_memory_names_a_prefixed_opcode_if_one_is_ever_added(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(opcodes, "MEMORY_INIT", 0xFC, raising=False)
    monkeypatch.setattr(opcodes, "SPEC_PINNED", opcodes.SPEC_PINNED | {"MEMORY_INIT"})
    with pytest.raises(EmitError, match="MEMORY_INIT"):
        module.check_no_bulk_memory()


def test_no_module_carries_a_datacount_section() -> None:
    for src in (COUNTER_SRC, NAMER_SRC, WIDE_SRC, HELPED_SRC):
        assert _section(build(src), 12) is None


# ===========================================================================
# contractenvmetav0 (ruling E9)
# ===========================================================================


def test_the_env_meta_payload_is_build_env_meta_of_the_declared_protocol() -> None:
    c = compiled(COUNTER_SRC)
    payload = _customs(module.assemble(c, meta={}, version=None).wasm)[
        sections.ENV_META_SECTION_NAME
    ]
    assert payload == build_env_meta(c.declared_protocol)
    assert payload == sections.env_meta_payload(c)


def test_an_ungated_contract_declares_protocol_20_not_27() -> None:
    """Ruling E9, by design and stated as such in the ruling: serpent writes the
    COMPUTED FLOOR, so an ungated contract declares 20 where the Phase 0
    artifact declared its build target of 27."""
    c = compiled(COUNTER_SRC)
    assert c.declared_protocol == 20
    assert sections.env_meta_payload(c) == build_env_meta(20)


def test_a_requested_target_protocol_is_what_the_env_meta_declares() -> None:
    """E9's other half (B4/S6: "users may raise, never lower"). `target_protocol`
    is a FRONTEND argument, and `declared_protocol` is what it becomes -- so
    asking for 23 changes the section's bytes, and to exactly the bytes
    `build_env_meta(23)` produces."""
    c = compile_module(COUNTER_SRC, "contracts/t.py", target_protocol=23)
    assert c.declared_protocol == 23
    payload = _customs(module.assemble(c, meta={}, version=None).wasm)[
        sections.ENV_META_SECTION_NAME
    ]
    assert payload == build_env_meta(23)
    assert payload != build_env_meta(20)


# ===========================================================================
# contractspecv0 (B8/B9/B10)
# ===========================================================================


def test_the_spec_payload_is_the_direct_build_spec_entries_call() -> None:
    c = compiled(COUNTER_SRC)
    contract_cls = c.spec_inputs.contract_cls
    assert contract_cls is not None
    payload = _customs(module.assemble(c, meta={}, version=None).wasm)[sections.SPEC_SECTION_NAME]
    assert payload == build_spec_entries(contract_cls, types=c.spec_inputs.declared_types_in_order)


def test_declared_events_reach_the_events_keyword_and_never_types() -> None:
    """`spec_inputs.events` stays a SEPARATE inventory (review B10/MJ-9):
    `types=` carries UDT references and refuses an event outright, while
    `events=` carries the `EVENT_V0` entries. The proof is byte equality with
    the explicit two-keyword call, over a fixture that really does declare an
    event."""
    src = _TOKEN_STYLE.read_text(encoding="utf-8")
    c = compiled(src, str(_TOKEN_STYLE))
    contract_cls = c.spec_inputs.contract_cls
    assert contract_cls is not None
    assert len(c.spec_inputs.events) == 1
    assert len(c.spec_inputs.declared_types_in_order) == 2
    assert sections.spec_payload(c) == build_spec_entries(
        contract_cls,
        types=c.spec_inputs.declared_types_in_order,
        events=c.spec_inputs.events,
    )
    with pytest.raises(ValueError, match="events="):
        build_spec_entries(
            contract_cls,
            types=(*c.spec_inputs.declared_types_in_order, *c.spec_inputs.events),
        )


#: M1-E2: a module declaring a union but never annotating anything with it
#: (Task 4's job, not Task 3's) -- `spec_types` collects a declared type
#: whether or not any signature references it, exactly like `Balance` in
#: `test_decls.py`'s own fixture.
UNION_SRC = """\
from serpent import U32, ContractUnion, Env, contract, contractunion, variant


@contractunion
class Shape(ContractUnion):
    Empty = variant()
    Circle = variant(U32)


@contract
class C:
    def get(self, env: Env) -> U32:
        return U32(0)
"""


def _unpack_all(payload: bytes) -> list[xdr.SCSpecEntry]:
    unpacker = Unpacker(payload)
    entries: list[xdr.SCSpecEntry] = []
    while unpacker.get_position() < len(payload):
        entries.append(xdr.SCSpecEntry.unpack(unpacker))
    return entries


def test_a_declared_union_reaches_the_spec_as_a_udt_union_entry() -> None:
    """F.1.6, both directions.

    Direction 1 (`types` HONORED): `spec_inputs.declared_types_in_order`
    carries the union class, the assembled wasm's `contractspecv0` is exactly
    what `build_spec_entries` produces from it, and decoding those bytes back
    finds the matching `UDT_UNION_V0` entry.

    Direction 2 (`types` OMITTED, the Q5 footgun): calling
    `build_spec_entries` with no `types=` at all still compiles -- the
    annotation `Shape` never appears in, so nothing REFERENCES it either --
    but the point stands generally: a caller that forgets to pass a declared
    type gets a spec with no matching `UDT_UNION_V0` entry, silently.
    """
    c = compiled(UNION_SRC)
    contract_cls = c.spec_inputs.contract_cls
    assert contract_cls is not None
    assert [cls.__name__ for cls in c.spec_inputs.declared_types_in_order] == ["Shape"]

    payload = _customs(module.assemble(c, meta={}, version=None).wasm)[sections.SPEC_SECTION_NAME]
    assert payload == build_spec_entries(contract_cls, types=c.spec_inputs.declared_types_in_order)

    entries = _unpack_all(payload)
    unions = [e.udt_union_v0 for e in entries if e.udt_union_v0 is not None]
    assert len(unions) == 1
    assert unions[0].name == b"Shape"

    # Direction 2: omitting `types` entirely gets a spec with NO UDT_UNION_V0
    # entry at all -- the documented footgun `build_spec_entries`'s own
    # docstring names ("a caller that omits `types` silently emits a spec
    # whose UDT references have no matching entries").
    omitted = build_spec_entries(contract_cls)
    assert not any(e.udt_union_v0 is not None for e in _unpack_all(omitted))


def test_a_built_module_carries_the_event_spec_entry() -> None:
    """Dossier F.1.11, end to end: a contract module spelling the topic
    convention -- `from serpent import Annotated, topic` (SPT2005: a contract
    may import from `serpent` and nowhere else) -- compiles, and the assembled
    wasm's `contractspecv0` carries the matching `SC_SPEC_ENTRY_EVENT_V0`."""
    payload = _customs(build(EVENT_SRC))[sections.SPEC_SECTION_NAME]
    entries = _unpack_all(payload)
    kinds = [entry.kind for entry in entries]
    events = [entry.event_v0 for entry in entries if entry.event_v0 is not None]

    # Ruling E2's order: the event entry is LAST, after every function.
    assert kinds[-1] is xdr.SCSpecEntryKind.SC_SPEC_ENTRY_EVENT_V0
    assert len(events) == 1
    event = events[0]
    assert event.name.sc_symbol == b"Moved"
    assert [t.sc_symbol for t in event.prefix_topics] == [b"moved"]
    assert event.data_format is xdr.SCSpecEventDataFormat.SC_SPEC_EVENT_DATA_FORMAT_MAP
    assert [(p.name, p.location) for p in event.params] == [
        (
            b"who",
            xdr.SCSpecEventParamLocationV0.SC_SPEC_EVENT_PARAM_LOCATION_TOPIC_LIST,
        ),
        (b"amount", xdr.SCSpecEventParamLocationV0.SC_SPEC_EVENT_PARAM_LOCATION_DATA),
    ]


def test_the_spec_section_names_every_export(  # B9
) -> None:
    payload = _customs(build(HELPED_SRC))[sections.SPEC_SECTION_NAME]
    unpacker = Unpacker(payload)
    names: list[str] = []
    while unpacker.get_position() < len(payload):
        entry = xdr.SCSpecEntry.unpack(unpacker)
        if entry.function_v0 is not None:
            names.append(entry.function_v0.name.sc_symbol.decode())
    assert names == ["__constructor", "go"]


# ===========================================================================
# contractmetav0 (ruling E8) -- STRUCTURAL only (S8 forbids a byte golden)
# ===========================================================================


def test_the_meta_section_names_the_contract_class_and_the_compiler() -> None:
    pairs = _meta_pairs(_customs(build(COUNTER_SRC))[sections.META_SECTION_NAME])
    assert pairs == [("name", "Counter"), ("serpentver", serpent.__version__)]


def test_the_meta_sections_serpentver_is_the_installed_distributions_version() -> None:
    """E8's cannot-drift intent (S8: structural, never a byte golden)."""
    pairs = dict(_meta_pairs(_customs(build(COUNTER_SRC))[sections.META_SECTION_NAME]))
    assert pairs["serpentver"] == importlib.metadata.version("serpent")


def test_the_version_entry_is_omitted_unless_the_caller_supplies_one() -> None:
    without = dict(_meta_pairs(_customs(build(COUNTER_SRC))[sections.META_SECTION_NAME]))
    assert "version" not in without
    with_it = dict(
        _meta_pairs(_customs(build(COUNTER_SRC, version="1.2.3"))[sections.META_SECTION_NAME])
    )
    assert with_it["version"] == "1.2.3"


def test_user_meta_pairs_follow_the_reserved_keys_in_their_own_order() -> None:
    payload = _customs(build(COUNTER_SRC, meta={"zeta": "z", "alpha": "a"}))[
        sections.META_SECTION_NAME
    ]
    assert [key for key, _v in _meta_pairs(payload)] == ["name", "serpentver", "zeta", "alpha"]


@pytest.mark.parametrize("key", ["name", "version", "serpentver"])
def test_a_user_meta_pair_colliding_with_a_reserved_key_is_refused(key: str) -> None:
    """An API-argument error, deliberately NOT a registry code (Task 11 raises
    the same `ValueError` up front, before assembly)."""
    with pytest.raises(ValueError, match=key):
        build(COUNTER_SRC, meta={key: "x"})


def test_the_meta_payload_equals_the_direct_build_meta_call() -> None:
    c = compiled(COUNTER_SRC)
    assert sections.meta_payload(c, {"repo": "r"}, "9.9") == build_meta(
        "Counter", "9.9", {"repo": "r"}
    )


# ===========================================================================
# Assembly invariants
# ===========================================================================


def test_assembling_the_same_module_twice_produces_identical_bytes() -> None:
    """Ruling E7's in-process half (Task 11 adds the cross-subprocess,
    hash-seeded proof): pool offsets come from the inventory and both index
    spaces come from first use, so nothing here depends on dict iteration."""
    for src in (COUNTER_SRC, NAMER_SRC, WIDE_SRC, HELPED_SRC):
        assert build(src) == build(src)


def test_a_contract_class_the_frontend_never_found_is_a_loud_compiler_bug() -> None:
    """C14/C.3: `ir.contract is None` always comes with a diagnostic, so it can
    never reach D -- assert it rather than dereferencing `None`."""
    c = compiled(COUNTER_SRC)
    headless = dataclasses.replace(c, ir=dataclasses.replace(c.ir, contract=None))
    with pytest.raises(EmitError, match="contract"):
        module.assemble(headless, meta={}, version=None)


def test_a_spec_input_without_a_contract_class_is_a_loud_compiler_bug() -> None:
    c = compiled(COUNTER_SRC)
    headless = dataclasses.replace(
        c, spec_inputs=dataclasses.replace(c.spec_inputs, contract_cls=None)
    )
    with pytest.raises(EmitError, match="contract_cls"):
        module.assemble(headless, meta={}, version=None)


def test_the_token_style_fixture_assembles_and_stays_well_under_the_size_cap() -> None:
    """S22's budget tripwire (B.1's size-budget note): the richest fixture in
    the repo -- structs, an event, Address keys, storage -- must stay far below
    131072 bytes, so a code-size regression shows up as a number rather than as
    a deploy failure."""
    src = _TOKEN_STYLE.read_text(encoding="utf-8")
    wasm = module.assemble(compiled(src, str(_TOKEN_STYLE)), meta={}, version=None).wasm
    assert len(wasm) < 131072 * 0.20


# ===========================================================================
# The first full-module EXECUTION (dossier D.6)
# ===========================================================================


def test_the_assembled_counter_instantiates_and_runs_under_the_mini_host() -> None:
    """Setup-free: `increment` reads through the with-default storage shape, so
    the first call sees an empty store. This is the first time in the sub-plan
    that a REAL assembled module -- imports, exports, prologues, custom
    sections and all -- is instantiated and driven."""
    wasm = build(COUNTER_SRC)
    store = ObjectStore()
    host = MiniHost(wasm, imports=store.bindings())
    store.attach(host)

    assert host.invoke("increment", val.pack_u32val(5)) == val.pack_u32val(5)
    assert host.invoke("total") == val.pack_u32val(5)
    assert host.invoke("increment", val.pack_u32val(7)) == val.pack_u32val(12)
    assert host.invoke("total") == val.pack_u32val(12)
    assert store.storage[(STORAGE_PERSISTENT, (15, b"TOTAL"))] == val.pack_u32val(12)


def test_the_assembled_counter_raises_its_own_contract_error_past_the_limit() -> None:
    wasm = build(COUNTER_SRC)
    store = ObjectStore()
    host = MiniHost(wasm, imports=store.bindings())
    store.attach(host)
    with pytest.raises(HostError) as excinfo:
        host.invoke("increment", val.pack_u32val(1001))
    assert excinfo.value.val == val.error_val(1)


def test_the_assembled_counter_refuses_a_wrong_tag_argument(  # S3's prologue
) -> None:
    wasm = build(COUNTER_SRC)
    store = ObjectStore()
    host = MiniHost(wasm, imports=store.bindings())
    store.attach(host)
    with pytest.raises(HostError) as excinfo:
        host.invoke("increment", val.VOID_VAL)
    assert excinfo.value.val == val.error_val(errors.CODE_ABI_CHECK_FAILED)


def test_a_module_needing_linear_memory_instantiates_with_its_data_segment() -> None:
    """The memory export and the data segment are what make the LM host
    functions work at all: the harness reads the pooled bytes back out of the
    guest's own memory through the exported name."""
    wasm = build(NAMER_SRC)
    store = ObjectStore()
    host = MiniHost(wasm, imports=store.bindings())
    store.attach(host)
    handle = host.invoke("name")
    assert handle is not None
    assert store.objects[val.body_of(handle)] == String("serpent phase zero")


def test_a_two_result_part_module_runs_using_its_scratch_slot() -> None:
    """Review B8's memory, in use: `u128_mul` stores `lo` into scratch and the
    caller loads it straight back, so the module cannot work at all unless
    section 5 was emitted."""
    from tests.harness.i256 import Wide256Host

    wide = Wide256Host()
    wasm = build(WIDE_SRC)
    host = MiniHost(wasm, imports=wide.bindings())
    a = wide.obj_from_u128_pieces(0, 7)
    b = wide.obj_from_u128_pieces(0, 6)
    product = host.invoke("product", a, b)
    assert product is not None
    # 42 fits the 56-bit small form, so the module boxes it back as a SMALL
    # `U128Val` rather than allocating a host object -- decode the word rather
    # than comparing against a freshly built handle (handles are
    # allocation-ordered, so a second one naming 42 is a different word).
    assert val.tag_of(product) == val.TAG_U128_SMALL
    assert val.body_of(product) == 42


def test_every_export_the_spec_section_names_really_exists_in_the_module() -> None:
    """The two halves of the ABI agreed: `contractspecv0` describes exactly the
    functions section 7 exports (excluding the memory)."""
    wasm = build(HELPED_SRC)
    exported = {name for name, kind, _idx in _exports(wasm) if kind == 0x00}
    payload = _customs(wasm)[sections.SPEC_SECTION_NAME]
    unpacker = Unpacker(payload)
    spec_names: set[str] = set()
    while unpacker.get_position() < len(payload):
        entry = xdr.SCSpecEntry.unpack(unpacker)
        if entry.function_v0 is not None:
            spec_names.add(entry.function_v0.name.sc_symbol.decode())
    assert spec_names == exported


def test_the_emitter_package_does_not_reach_stellar_sdk_outside_sections() -> None:
    """Global Constraints: `emitter/sections.py` is the ONE emitter module
    allowed to import `serpent.spec` (and so, transitively, `stellar_sdk`)."""
    src_dir = Path(serpent.__file__).parent / "emitter"
    offenders = [
        path.name
        for path in sorted(src_dir.glob("*.py"))
        if path.name != "sections.py"
        and re.search(
            r"^\s*(from|import)\s+(serpent\.spec|stellar_sdk)", path.read_text(), re.MULTILINE
        )
    ]
    assert offenders == []
