"""The internal validator (an INDEPENDENT decoder) and the wasm-tools gate.

S2/P8: *an invalid module is a compile error, never an output file*. Two gates
serve that, and they are deliberately unequal:

* ``validate_internal`` always runs (Task 11's ``build_wasm`` calls it on every
  build) and is an independent DECODER over the emitted bytes. It re-derives
  every fact it checks from the module itself and consults **none** of the
  emitter's bookkeeping -- no ``EmitCtx``, no ``Fn``, no import order. A
  validator that asked the assembler what it had emitted would agree with a
  buggy assembler perfectly.
* ``validate_external`` is the optional wasm-tools shell-out (ruling E5), which
  answers ``None`` when the tool is absent so a caller can tell "not answered"
  from "answered no".

What ``validate_internal`` checks, and why each one is here rather than left to
the engine:

| Check | Source |
|---|---|
| magic + version | the format; a wrong prefix is unreadable, not invalid |
| section ids strictly ascend (customs exempt) | B.1: a section may be omitted, never reordered or repeated |
| no start section | S23, verbatim: "No start section" |
| at most one memory | S23; the harness pins ``wasm_multi_memory=False`` to match |
| a ``memory`` export iff one is expected | S10/S11 + ruling E10, checked in BOTH directions |
| export names unique | the format; a duplicate makes the ABI ambiguous |
| functype params/results <= 32 | S23/C18's contract arity cap |
| import FIELD names <= 10 chars | S23's import-symbol cap |
| module size <= 131072 | S22, the network's own limit |

Only the last one is the USER's problem, so it -- and it alone here -- raises
``BuildLimitError`` with ``limit="module_size"``, which Task 11 maps to
SPT8001. (The pool and scratch limits raise their own ``BuildLimitError``s
inside ``layout.Memory.check``, long before there are bytes to validate.)
Everything else is an invariant break and raises bare ``EmitError``.

The decoder walks only as deep as those checks need: section framing always,
then the type/import/memory/export sections' own vectors. Code bodies are NOT
decoded -- ``frame.Fn`` already validated every operand stack at lowering time,
and wasm-tools is the independent check on the instruction stream.
"""

import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

from serpent.emitter.frame import BuildLimitError, EmitError

__all__ = [
    "MAX_FUNCTYPE_ARITY",
    "MAX_IMPORT_FIELD_CHARS",
    "MAX_MODULE_SIZE",
    "WASM_FEATURES",
    "iter_sections",
    "read_name",
    "read_uleb",
    "validate_external",
    "validate_internal",
]

#: S22: the network's contract-size limit, in bytes.
MAX_MODULE_SIZE = 131072

#: S23/C18: a contract export takes at most 32 arguments, so no functype in a
#: serpent module has more than 32 params (or results -- multi-value is off, but
#: the cap is checked on both sides rather than assuming which one broke).
MAX_FUNCTYPE_ARITY = 32

#: S23: an import symbol name is at most 10 characters. The pin's own names
#: (`x.5`, `m.9`, ...) satisfy it by construction, which is precisely why an
#: independent check is worth the ten lines: nothing else would notice a
#: hand-added import.
MAX_IMPORT_FIELD_CHARS = 10

#: The chain's wasm feature set, as ONE named constant (S23). `-all` clears the
#: defaults, then exactly three features come back: `mutable-global`,
#: `sign-extension`, `bulk-memory`. Everything else -- SIMD, reference types,
#: tail calls, multi-value, threads, exceptions, memory64, wide arithmetic --
#: stays off, matching `tests/harness/engine.make_config`'s behavioural pins.
WASM_FEATURES = "-all,mutable-global,sign-extension,bulk-memory"

_MAGIC = b"\x00asm"
_VERSION = b"\x01\x00\x00\x00"

#: Section ids the core binary format defines. Anything else is not a section.
_MAX_SECTION_ID = 12

_SEC_CUSTOM = 0
_SEC_TYPE = 1
_SEC_IMPORT = 2
_SEC_MEMORY = 5
_SEC_EXPORT = 7
_SEC_START = 8

_FUNCTYPE_TAG = 0x60
_KIND_MEMORY = 0x02

#: The host looks the guest's linear memory up under this exact name. Spelled
#: here as its own literal (P15): `module.MEMORY_EXPORT_NAME` is the assembler's
#: constant, and a validator that imported it would agree with a wrong constant
#: instead of catching it.
_MEMORY_EXPORT_NAME = "memory"


# --- the decoder ---------------------------------------------------------------


def read_uleb(data: bytes, i: int) -> tuple[int, int]:
    """One unsigned LEB128 at ``i``, as ``(value, next index)``.

    Public because ``module.recompute_import_names`` -- review B1's pass-2
    safety net -- decodes the emitted import section too, and two LEB readers in
    one package is two places for the same off-by-one.
    """
    value = 0
    shift = 0
    while True:
        if i >= len(data):
            raise EmitError("truncated module: an LEB128 integer runs off the end")
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7
        if shift > 63:
            raise EmitError("malformed module: an LEB128 integer exceeds 64 bits")


def read_name(data: bytes, i: int) -> tuple[str, int]:
    """One wasm ``name`` at ``i`` (uleb length then UTF-8 bytes)."""
    length, i = read_uleb(data, i)
    if i + length > len(data):
        raise EmitError("truncated module: a name runs past the end of its section")
    return data[i : i + length].decode("utf-8"), i + length


def iter_sections(wasm: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield every section as ``(id, payload)`` in byte order.

    Framing only: the magic and version are checked, then each section's id byte
    and uleb length. A payload shorter than its declared length is a truncated
    module and raises rather than yielding a short slice.
    """
    if len(wasm) < 8 or wasm[:4] != _MAGIC:
        raise EmitError(f"not a wasm module: the magic bytes are {wasm[:4]!r}, not {_MAGIC!r}")
    if wasm[4:8] != _VERSION:
        raise EmitError(f"unsupported wasm version {wasm[4:8]!r}; serpent emits {_VERSION!r}")
    i = 8
    while i < len(wasm):
        sid = wasm[i]
        size, i = read_uleb(wasm, i + 1)
        if i + size > len(wasm):
            raise EmitError(
                f"truncated module: section {sid} declares {size} bytes but only "
                f"{len(wasm) - i} remain"
            )
        yield sid, wasm[i : i + size]
        i += size


# --- the checks ----------------------------------------------------------------


def _check_order(sections: list[tuple[int, bytes]]) -> None:
    """Section ids strictly ascend; customs (id 0) may appear anywhere (B.1)."""
    previous = 0
    for sid, _payload in sections:
        if sid > _MAX_SECTION_ID:
            raise EmitError(f"section id {sid} is not defined by the core binary format")
        if sid == _SEC_CUSTOM:
            continue
        if sid <= previous:
            raise EmitError(
                f"section ids must strictly ascend: section {sid} follows section "
                f"{previous} (a section may be omitted, never reordered or repeated)"
            )
        previous = sid
    if any(sid == _SEC_START for sid, _payload in sections):
        raise EmitError("the module has a start section; S23 forbids one outright")


def _check_types(payload: bytes) -> None:
    """Every functype is well-formed and within S23/C18's arity cap."""
    count, i = read_uleb(payload, 0)
    for index in range(count):
        if payload[i] != _FUNCTYPE_TAG:
            raise EmitError(
                f"type {index} starts with {payload[i]:#04x}, not the functype tag "
                f"{_FUNCTYPE_TAG:#04x}"
            )
        nparams, i = read_uleb(payload, i + 1)
        if nparams > MAX_FUNCTYPE_ARITY:
            raise EmitError(
                f"type {index} takes {nparams} parameters; S23/C18 cap a contract "
                f"function at {MAX_FUNCTYPE_ARITY}"
            )
        i += nparams
        nresults, i = read_uleb(payload, i)
        if nresults > MAX_FUNCTYPE_ARITY:
            raise EmitError(
                f"type {index} returns {nresults} results; the cap is {MAX_FUNCTYPE_ARITY}"
            )
        i += nresults
        if i > len(payload):
            raise EmitError(f"truncated type section: type {index} runs past the payload")


def _check_imports(payload: bytes) -> int:
    """Check each import's field-name length; return how many memories it imports."""
    count, i = read_uleb(payload, 0)
    memories = 0
    for _ in range(count):
        _module_name, i = read_name(payload, i)
        field, i = read_name(payload, i)
        if len(field) > MAX_IMPORT_FIELD_CHARS:
            raise EmitError(
                f"import field name {field!r} is {len(field)} characters; S23 caps an "
                f"import symbol at {MAX_IMPORT_FIELD_CHARS}"
            )
        kind = payload[i]
        i += 1
        if kind == _KIND_MEMORY:
            memories += 1
            # limits: a flags byte, a minimum, and a maximum when the flag says so.
            flags = payload[i]
            _minimum, i = read_uleb(payload, i + 1)
            if flags & 0x01:
                _maximum, i = read_uleb(payload, i)
        else:
            _index, i = read_uleb(payload, i)
    return memories


def _count_memories(payload: bytes) -> int:
    count, _i = read_uleb(payload, 0)
    return count


def _check_exports(payload: bytes) -> list[str]:
    """Names are unique; return the names under which a MEMORY is exported."""
    count, i = read_uleb(payload, 0)
    seen: set[str] = set()
    memory_exports: list[str] = []
    for _ in range(count):
        name, i = read_name(payload, i)
        if name in seen:
            raise EmitError(f"duplicate export name {name!r}: the ABI would be ambiguous")
        seen.add(name)
        kind = payload[i]
        _index, i = read_uleb(payload, i + 1)
        if kind == _KIND_MEMORY:
            memory_exports.append(name)
    return memory_exports


def validate_internal(wasm: bytes, *, expect_memory: bool) -> None:
    """Validate ``wasm`` structurally, from the bytes alone.

    ``expect_memory`` is the assembler's own E10 decision, and the check runs in
    BOTH directions: a module that should have a memory must export one under
    the literal name ``memory``, and a memoryless one must have no memory at
    all. Disagreement means the layout and the decision diverged.

    Raises ``BuildLimitError(limit="module_size")`` for S22's size cap -- the
    one user-visible failure here -- and bare ``EmitError`` for every other
    violation.
    """
    if len(wasm) > MAX_MODULE_SIZE:
        raise BuildLimitError(
            limit="module_size",
            message=(
                f"the compiled module is {len(wasm)} bytes; the network's limit is "
                f"{MAX_MODULE_SIZE}"
            ),
        )

    sections = list(iter_sections(wasm))
    _check_order(sections)

    memories = 0
    memory_exports: list[str] = []
    for sid, payload in sections:
        if sid == _SEC_TYPE:
            _check_types(payload)
        elif sid == _SEC_IMPORT:
            memories += _check_imports(payload)
        elif sid == _SEC_MEMORY:
            memories += _count_memories(payload)
        elif sid == _SEC_EXPORT:
            memory_exports = _check_exports(payload)

    if memories > 1:
        raise EmitError(f"the module declares {memories} memories; S23 allows at most one")

    exported = _MEMORY_EXPORT_NAME in memory_exports
    if expect_memory:
        if memories != 1:
            raise EmitError(
                f"the module was assembled with linear memory but declares {memories} memories"
            )
        if not exported:
            raise EmitError(
                f"the module declares a memory but exports no {_MEMORY_EXPORT_NAME!r}: "
                f"the host reads guest memory through that exact name, so "
                f"{memory_exports or 'nothing'} is invisible to it"
            )
    else:
        if memories:
            raise EmitError(
                f"the module was assembled memoryless (ruling E10) but declares {memories} memories"
            )
        if memory_exports:
            raise EmitError(
                f"the module was assembled memoryless but exports {memory_exports} as memory"
            )


# --- the optional external gate (ruling E5) ------------------------------------


def validate_external(wasm: bytes) -> bool | None:
    """Run ``wasm-tools validate`` over ``wasm``; ``None`` when it is not installed.

    Three answers, not two (ruling E5): ``None`` is "not answered" -- the tool
    is absent, which is a normal state for a contributor's laptop and must never
    read as a pass -- while ``True``/``False`` are the tool's own verdict.
    ``--features`` carries ``WASM_FEATURES`` so the shell-out asks about the
    CHAIN's wasm, not wasm-tools' current defaults.
    """
    tool = shutil.which("wasm-tools")
    if tool is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "module.wasm"
        path.write_bytes(wasm)
        done = subprocess.run(
            [tool, "validate", f"--features={WASM_FEATURES}", str(path)],
            capture_output=True,
            check=False,
        )
    return done.returncode == 0
