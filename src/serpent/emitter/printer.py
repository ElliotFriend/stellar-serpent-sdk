"""D's own minimal wasm disassembler (dossier D.2): reviewable lowerings
without an external toolchain.

``wasm-tools`` is optional (ruling E5) -- CI has it, a contributor's laptop may
not -- so a reviewer checking WHAT sub-plan D actually lowered a contract to
needs a renderer this package owns outright. ``disassemble`` prints section
headers, then for every defined function: its locals, then one mnemonic per
line with every immediate decoded -- a host call rendered BY NAME (through the
same ``(module, field) -> registered name`` reverse lookup
``module.recompute_import_names`` already established for review B1's net) and
a call to another defined function rendered as its export name when it has
one, ``$fn<N>`` (``N`` its DEFINED-space index, the same numbering
``frame.CallDefined`` uses) otherwise.

**Never cited as evidence of correctness** (B12's discipline -- see
``tests/goldens/README.md``'s SELF-SNAPSHOT class). This prints what the
emitter DID, not a proof that what it did is right.

**No decoding is duplicated here.** Every low-level reader
(``iter_sections``, ``read_uleb``, ``read_name``, ``read_byte``) is imported
from ``validate.py``, the opcode -> immediate-shape table is imported from
``module._instruction_immediates()`` (which also carries its own
vocabulary/table consistency guard, run here as a side effect), the
size-prefixed code-section walk is ``module.split_code_entries`` (factored
out of ``module._decode_code_section`` in review, so the same truncation
check is not maintained twice), and the import-name reverse lookup is
``module.recompute_import_names`` itself -- the exact same table review B1's
net already resolves calls against. A byte neither table recognizes is a
loud ``EmitError``, never a guessed mnemonic (module.py's own established
discipline for ``call``, extended here to the whole instruction stream).
"""

from __future__ import annotations

from collections.abc import Callable

from serpent.emitter import opcodes
from serpent.emitter.frame import EmitError
from serpent.emitter.module import (
    _IMM_BLOCKTYPE,
    _IMM_MEMARG,
    _IMM_NONE,
    _IMM_SLEB,
    _IMM_ULEB,
    _IMMEDIATE_BY_NAME,
    _SEC_CODE,
    _SEC_DATA,
    _SEC_FUNCTION,
    _instruction_immediates,
    recompute_import_names,
    split_code_entries,
)
from serpent.emitter.validate import (
    _SEC_CUSTOM,
    _SEC_EXPORT,
    _SEC_IMPORT,
    _SEC_MEMORY,
    _SEC_TYPE,
    iter_sections,
    read_byte,
    read_name,
    read_uleb,
)

__all__ = ["disassemble"]

_FUNCTYPE_TAG = 0x60
_KIND_FUNC = 0x00
_KIND_MEMORY = 0x02

#: Valtype bytes this printer can name. ``opcodes.py`` pins only ``i64``
#: (``VALTYPE_I64``, on-chain-verified, P17) because D never emits any other
#: signature type (``module.py``'s own ``_VALTYPE`` table); ``0x7F`` is core
#: wasm's ``i32`` byte, spelled here as its own literal rather than invented
#: from ``opcodes.py`` because no D lowering ever puts it in a functype --
#: refusing to render a legal ``i32`` param would be presumptuous, not loud.
_VALTYPE_NAMES: dict[int, str] = {opcodes.VALTYPE_I64: "i64", 0x7F: "i32"}

#: One WAT-style mnemonic per instruction NAME in
#: ``module._IMMEDIATE_BY_NAME`` -- checked for exact agreement with that set
#: by ``_mnemonic_texts`` below, so a new opcode has to earn a rendering
#: before this module can print it (never a guess).
_MNEMONIC_TEXT: dict[str, str] = {
    "UNREACHABLE": "unreachable",
    "BLOCK": "block",
    "LOOP": "loop",
    "IF": "if",
    "ELSE": "else",
    "END": "end",
    "BR": "br",
    "BR_IF": "br_if",
    "RETURN": "return",
    "CALL": "call",
    "DROP": "drop",
    "LOCAL_GET": "local.get",
    "LOCAL_SET": "local.set",
    "LOCAL_TEE": "local.tee",
    "I32_CONST": "i32.const",
    "I64_CONST": "i64.const",
    "I64_EQZ": "i64.eqz",
    "I64_EQ": "i64.eq",
    "I64_NE": "i64.ne",
    "I64_LT_S": "i64.lt_s",
    "I64_LT_U": "i64.lt_u",
    "I64_GT_S": "i64.gt_s",
    "I64_GT_U": "i64.gt_u",
    "I64_LE_S": "i64.le_s",
    "I64_LE_U": "i64.le_u",
    "I64_GE_S": "i64.ge_s",
    "I64_GE_U": "i64.ge_u",
    "I64_ADD": "i64.add",
    "I64_SUB": "i64.sub",
    "I64_MUL": "i64.mul",
    "I64_DIV_S": "i64.div_s",
    "I64_DIV_U": "i64.div_u",
    "I64_REM_S": "i64.rem_s",
    "I64_REM_U": "i64.rem_u",
    "I64_AND": "i64.and",
    "I64_OR": "i64.or",
    "I64_XOR": "i64.xor",
    "I64_SHL": "i64.shl",
    "I64_SHR_S": "i64.shr_s",
    "I64_SHR_U": "i64.shr_u",
    "I32_WRAP_I64": "i32.wrap_i64",
    "I64_EXTEND_I32_U": "i64.extend_i32_u",
    "I64_EXTEND32_S": "i64.extend32_s",
    "I64_LOAD": "i64.load",
    "I64_STORE": "i64.store",
}


def _mnemonic_texts() -> dict[str, str]:
    """``_MNEMONIC_TEXT``, checked against the emitter's own vocabulary.

    Mirrors ``module._instruction_immediates``'s own completeness check: an
    opcode present in ``_IMMEDIATE_BY_NAME`` but absent here would make the
    renderer guess at *something* to print, and the mirror-image mistake (a
    name in this table nothing emits any more) is symmetrically caught too.
    """
    names = set(_IMMEDIATE_BY_NAME)
    if names != set(_MNEMONIC_TEXT):
        missing = sorted(names - set(_MNEMONIC_TEXT))
        extra = sorted(set(_MNEMONIC_TEXT) - names)
        raise EmitError(
            "the printer's mnemonic table and the emitter's instruction vocabulary "
            f"disagree: missing {missing}, unknown {extra} -- rendering an opcode this "
            "table does not name would be a guess, not a decode"
        )
    return _MNEMONIC_TEXT


def _valtype_name(byte: int) -> str:
    name = _VALTYPE_NAMES.get(byte)
    if name is None:
        raise EmitError(f"valtype byte {byte:#04x} is not one this printer recognizes")
    return name


def _signature(params: tuple[int, ...], results: tuple[int, ...]) -> str:
    out = ""
    if params:
        out += " (param " + " ".join(_valtype_name(b) for b in params) + ")"
    if results:
        out += " (result " + " ".join(_valtype_name(b) for b in results) + ")"
    return out


def _render_blocktype(byte: int) -> str:
    if byte == opcodes.BLOCKTYPE_VOID:
        return ""
    if byte == opcodes.BLOCKTYPE_I64:
        return " (result i64)"
    raise EmitError(
        f"blocktype byte {byte:#04x} is neither void ({opcodes.BLOCKTYPE_VOID:#04x}) nor "
        f"i64 ({opcodes.BLOCKTYPE_I64:#04x}); D never emits a third blocktype"
    )


def _read_sleb(data: bytes, i: int, what: str) -> tuple[int, int]:
    """One signed LEB128 at ``i``, decoded to its Python integer value.

    ``validate.read_uleb`` is deliberately unsigned-only (every existing
    caller outside a ``const`` operand wants an unsigned count or index), so
    this is the printer's own small counterpart -- it shares ``read_byte``'s
    truncation check rather than reimplementing it, and is the mirror image
    of ``encode.sleb`` (which only ENCODES).
    """
    value = 0
    shift = 0
    while True:
        byte = read_byte(data, i, what)
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            if byte & 0x40:
                value -= 1 << shift
            return value, i
        if shift > 70:
            raise EmitError(f"malformed module: {what} exceeds a sane LEB128 width")


# --- section-level decoding (values `validate.py`'s own checks discard) -----


def _decode_types(payload: bytes) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    count, i = read_uleb(payload, 0)
    out: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
    for index in range(count):
        tag = read_byte(payload, i, f"type {index}")
        if tag != _FUNCTYPE_TAG:
            raise EmitError(
                f"type {index} starts with {tag:#04x}, not the functype tag {_FUNCTYPE_TAG:#04x}"
            )
        nparams, i = read_uleb(payload, i + 1)
        params = tuple(payload[i : i + nparams])
        i += nparams
        nresults, i = read_uleb(payload, i)
        results = tuple(payload[i : i + nresults])
        i += nresults
        out.append((params, results))
    return out


def _decode_imports(payload: bytes) -> list[tuple[str, str, int]]:
    """Each function import as ``(module, field, typeidx)``.

    A serpent module imports nothing but functions (``module.py``'s own B.1
    row 4/6 note); a non-func import kind is refused here rather than
    guessed at, the same call ``validate._check_imports`` makes.
    """
    count, i = read_uleb(payload, 0)
    out: list[tuple[str, str, int]] = []
    for index in range(count):
        module_name, i = read_name(payload, i)
        field, i = read_name(payload, i)
        kind = read_byte(payload, i, f"import {index} ({module_name!r}, {field!r})'s descriptor")
        if kind != _KIND_FUNC:
            raise EmitError(
                f"import {index} ({module_name!r}, {field!r}) has kind {kind:#04x}; this "
                "printer only knows how to render a function import"
            )
        typeidx, i = read_uleb(payload, i + 1)
        out.append((module_name, field, typeidx))
    return out


def _decode_funcsec(payload: bytes) -> list[int]:
    count, i = read_uleb(payload, 0)
    out: list[int] = []
    for _ in range(count):
        typeidx, i = read_uleb(payload, i)
        out.append(typeidx)
    return out


def _decode_exports(payload: bytes) -> list[tuple[str, int, int]]:
    count, i = read_uleb(payload, 0)
    out: list[tuple[str, int, int]] = []
    for _ in range(count):
        name, i = read_name(payload, i)
        kind = read_byte(payload, i, f"export {name!r}'s descriptor")
        index, i = read_uleb(payload, i + 1)
        out.append((name, kind, index))
    return out


def _decode_memories(payload: bytes) -> list[tuple[int, int | None]]:
    count, i = read_uleb(payload, 0)
    out: list[tuple[int, int | None]] = []
    for _ in range(count):
        flags = read_byte(payload, i, "memory limits")
        minimum, i = read_uleb(payload, i + 1)
        maximum: int | None = None
        if flags & 0x01:
            maximum, i = read_uleb(payload, i)
        out.append((minimum, maximum))
    return out


def _decode_data(payload: bytes) -> list[tuple[int, int, bytes]]:
    count, i = read_uleb(payload, 0)
    out: list[tuple[int, int, bytes]] = []
    for index in range(count):
        memidx, i = read_uleb(payload, i)
        offset_op = read_byte(payload, i, f"data segment {index}'s offset expr")
        if offset_op != opcodes.I32_CONST:
            raise EmitError(
                f"data segment {index}'s offset expr starts with {offset_op:#04x}, not "
                f"i32.const ({opcodes.I32_CONST:#04x}); D emits only that form (B.1 row 11)"
            )
        offset, i = _read_sleb(payload, i + 1, f"data segment {index}'s offset")
        end = read_byte(payload, i, f"data segment {index}'s offset expr terminator")
        if end != opcodes.END:
            raise EmitError(f"data segment {index}'s offset expr does not end in `end`")
        i += 1
        length, i = read_uleb(payload, i)
        out.append((memidx, offset, payload[i : i + length]))
        i += length
    return out


# --- per-function rendering ---------------------------------------------------


def _render_body(
    entry: bytes,
    where: str,
    names_by_byte: dict[int, str],
    immediates: dict[int, str],
    mnemonics: dict[str, str],
    callee_name: Callable[[int], str],
) -> list[str]:
    """One function's locals declaration, then one rendered line per
    instruction, indented by control-flow nesting depth."""
    groups, i = read_uleb(entry, 0)
    local_valtypes: list[int] = []
    for _ in range(groups):
        count, i = read_uleb(entry, i)
        valtype = read_byte(entry, i, f"{where}'s locals declaration")
        i += 1
        local_valtypes.extend([valtype] * count)

    lines: list[str] = []
    if local_valtypes:
        lines.append("  (local " + " ".join(_valtype_name(b) for b in local_valtypes) + ")")

    depth = 1
    while i < len(entry):
        opcode = entry[i]
        at = i
        i += 1
        name = names_by_byte.get(opcode)
        if name is None:
            raise EmitError(
                f"{where}: byte {opcode:#04x} at offset {at} is not an instruction this "
                "printer's vocabulary recognizes; refusing to guess a mnemonic for it"
            )
        shape = immediates[opcode]
        mnemonic = mnemonics[name]

        if shape == _IMM_NONE:
            text = mnemonic
        elif shape == _IMM_BLOCKTYPE:
            block_byte = read_byte(entry, i, f"{where}'s blocktype at offset {at}")
            i += 1
            text = mnemonic + _render_blocktype(block_byte)
        elif shape == _IMM_ULEB:
            value, i = read_uleb(entry, i)
            text = f"call ${callee_name(value)}" if name == "CALL" else f"{mnemonic} {value}"
        elif shape == _IMM_SLEB:
            value, i = _read_sleb(entry, i, f"{where}'s const operand at offset {at}")
            text = f"{mnemonic} {value}"
        elif shape == _IMM_MEMARG:
            align, i = read_uleb(entry, i)
            offset, i = read_uleb(entry, i)
            text = f"{mnemonic} offset={offset} align={align}"
        else:  # pragma: no cover - `_instruction_immediates` closes this set
            raise EmitError(f"{where}: opcode {name} has an unrecognized immediate shape {shape!r}")

        if name == "ELSE":
            lines.append("  " * (depth - 1) + text)
        elif name == "END":
            depth -= 1
            lines.append("  " * depth + text)
        else:
            lines.append("  " * depth + text)
            if name in ("BLOCK", "LOOP", "IF"):
                depth += 1
    return lines


def disassemble(wasm: bytes) -> str:
    """A reviewable, minimal WAT-style rendering of ``wasm``.

    Section headers, then for the code section: one ``(func ...)`` block per
    defined function, its locals, then one mnemonic per line with every
    immediate decoded -- a ``call``'s target rendered BY NAME, never a bare
    index. See this module's docstring for the evidentiary weight (none) and
    the decode machinery this reuses rather than duplicates.
    """
    sections = list(iter_sections(wasm))

    types: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
    import_entries: list[tuple[str, str, int]] = []
    func_typeidx: list[int] = []
    export_entries: list[tuple[str, int, int]] = []
    memories: list[tuple[int, int | None]] = []
    code_entries: list[bytes] = []
    data_segments: list[tuple[int, int, bytes]] = []
    customs: list[tuple[str, int]] = []

    for sid, payload in sections:
        if sid == _SEC_TYPE:
            types = _decode_types(payload)
        elif sid == _SEC_IMPORT:
            import_entries = _decode_imports(payload)
        elif sid == _SEC_FUNCTION:
            func_typeidx = _decode_funcsec(payload)
        elif sid == _SEC_MEMORY:
            memories = _decode_memories(payload)
        elif sid == _SEC_EXPORT:
            export_entries = _decode_exports(payload)
        elif sid == _SEC_CODE:
            code_entries = split_code_entries(payload)
        elif sid == _SEC_DATA:
            data_segments = _decode_data(payload)
        elif sid == _SEC_CUSTOM:
            name, name_end = read_name(payload, 0)
            customs.append((name, len(payload) - name_end))

    # The exact reverse lookup review B1's net already resolved every `call`
    # against -- reused, not re-derived, so a divergence there fails here too.
    import_names = recompute_import_names(wasm)
    n_imports = len(import_names)

    export_by_func_index: dict[int, str] = {}
    memory_export_name: str | None = None
    for name, kind, index in export_entries:
        if kind == _KIND_FUNC:
            export_by_func_index.setdefault(index, name)
        elif kind == _KIND_MEMORY:
            memory_export_name = name
        else:
            raise EmitError(f"export {name!r} has kind {kind:#04x}; D exports func/memory only")

    def callee_name(index: int) -> str:
        if index < n_imports:
            return import_names[index]
        return export_by_func_index.get(index, f"fn{index - n_imports}")

    immediates = _instruction_immediates()
    names_by_byte = {getattr(opcodes, name): name for name in _IMMEDIATE_BY_NAME}
    mnemonics = _mnemonic_texts()

    lines: list[str] = [f";; wasm module: {len(wasm)} byte(s), {len(sections)} section(s)"]

    lines.append(f";; type section: {len(types)} entry(ies)")

    if import_entries:
        lines.append(f";; import section: {len(import_entries)} entry(ies)")
        for (module_name, field, typeidx), name in zip(import_entries, import_names, strict=True):
            params, results = types[typeidx]
            lines.append(
                f'(import "{module_name}" "{field}" (func ${name}{_signature(params, results)}))'
            )

    if memories:
        lines.append(";; memory section")
        for minimum, maximum in memories:
            export_clause = f' (export "{memory_export_name}")' if memory_export_name else ""
            cap = f" {maximum}" if maximum is not None else ""
            lines.append(f"(memory{export_clause} {minimum}{cap})")

    if export_entries:
        lines.append(f";; export section: {len(export_entries)} entry(ies)")
        for name, kind, index in export_entries:
            if kind == _KIND_FUNC:
                lines.append(f'(export "{name}" (func ${export_by_func_index[index]}))')
            elif kind == _KIND_MEMORY:
                lines.append(f'(export "{name}" (memory {index}))')

    lines.append(f";; code section: {len(code_entries)} defined function(s)")
    for defidx, entry in enumerate(code_entries):
        combined = n_imports + defidx
        params, results = types[func_typeidx[defidx]]
        name = export_by_func_index.get(combined, f"fn{defidx}")
        lines.append("")
        lines.append(f";; func[{defidx}] (combined index {combined})")
        lines.append(f"(func ${name}{_signature(params, results)}")
        lines.extend(
            _render_body(
                entry, f"func[{defidx}]", names_by_byte, immediates, mnemonics, callee_name
            )
        )
        lines.append(")")

    if data_segments:
        lines.append("")
        lines.append(f";; data section: {len(data_segments)} segment(s)")
        for memidx, offset, blob in data_segments:
            lines.append(f"(data (memory {memidx}) (i32.const {offset}) {blob!r})")

    if customs:
        lines.append("")
        lines.append(f";; custom sections: {len(customs)}")
        for name, size in customs:
            lines.append(f'(custom "{name}" {size} payload byte(s))')

    return "\n".join(lines) + "\n"
