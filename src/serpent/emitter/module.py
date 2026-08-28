"""Section assembly: a ``CompiledModule`` becomes deployable wasm32 bytes.

The two-pass structure the spike proved and dossier C.1 keeps:

**Pass 1** seeds the literal pool from the WHOLE ``LiteralInventory`` before any
body is lowered (Task 7's carried constraint: ``Memory.intern`` silently appends
an unseeded blob, and seeding first is what makes every pool offset -- and so
every scratch address handed out mid-body -- a pure function of the inventory,
E7), then lowers every function and every runtime part a lowering reached. Not a
byte of the module is laid out until every operand stack has been checked, which
is what makes "an invalid module is a compile error, never an output file"
(S2/P8) structural rather than a convention.

**Pass 2** lays out the sections in the order the binary format fixes (dossier
B.1's table) and only then resolves the symbolic call sites (review B1):
``CallImport(name)`` becomes ``call`` + the name's index in the FINAL import
section, ``CallDefined(d)`` becomes ``call`` + ``n_imports + d``. No index is
ever baked into a body before the section that defines it is frozen, so the
"calls the wrong function and still validates" class is structurally impossible
rather than merely avoided.

**The net under that** is two checks over the finished bytes, and it is worth
being precise about what each one can catch, because every host function shares
the all-i64 shape (B3) -- so a call to the WRONG function of the right arity
validates perfectly, in this emitter and in wasm-tools alike:

* ``recompute_import_names`` re-derives the import NAME order from the emitted
  import section, through the pin, and ``assemble`` refuses when it disagrees
  with the order pass 2 resolved against. This catches a reordered or
  mis-encoded import section. On its own it says nothing about any call
  immediate.
* ``check_call_targets`` DECODES every code body -- a real walk over the
  emitter's instruction vocabulary, so it lands on each ``call``'s immediate --
  and checks every target is in range. ``assemble`` then compares the decoded
  target sequence of each body, position by position, against the sequence
  re-derived from that body's symbolic items using the index map and import
  count read back out of the BYTES. An off-by-one, a dropped ``n_imports``, a
  corrupted LEB, or a body serialized against the wrong map all fail there
  rather than in a mini-host test that happens to exercise that one call.

What this module deliberately does NOT emit (B.1, row by row): a table (4 -- no
indirect calls; C12 rejects recursion, and there are no closures or function
values), globals (6 -- scratch is bump-allocated at COMPILE time, P12, so there
is no runtime bump pointer), a start section (8 -- S23 forbids it), elements
(9 -- no table), or a datacount section (12 -- mandatory only when a
bulk-memory instruction appears, which ``check_no_bulk_memory`` proves none
does).

Validation is Task 11's gate, not this function's: ``build_wasm`` always runs
``validate.validate_internal`` over these bytes and is the only public path to
them.

``assemble`` returns an ``AssembledModule``, not bare bytes (Task 10 handoff,
controller-adopted): the wasm bytes plus every fact pass 1 alone knows and
``build_wasm``/``BuildResult`` need -- whether the module expects a memory
(ruling E10), the emitted import names, the exported names, which runtime
parts got linked, and the pool/scratch byte counts. Recomputing any of these
from the bytes a second time in Task 11 would be a second place for the same
truth; ``assemble`` already had to compute every one of them to lay out the
sections in the first place.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from serpent._host import HOST_FUNCTIONS, functions_by_name

# The exact set C enumerates, imported rather than re-derived: the spike matched
# `"linear_memory" in name`, and the dossier (B.7, M13 row) is explicit that the
# enumerated set is the right form -- `sparse_map_new_from_linear_memory` is
# gated at protocol 28 and deliberately outside it. Reading the frontend's
# constant means a new linear-memory host function cannot be added there and
# missed here.
from serpent.compiler.frontend import _LINEAR_MEMORY_HOST_FNS, CompiledModule
from serpent.compiler.ir import FuncKind
from serpent.emitter import encode, opcodes, sections
from serpent.emitter.frame import CallDefined, CallImport, CodeItem, EmitError
from serpent.emitter.layout import Memory
from serpent.emitter.lower import LowerCtx, compile_function
from serpent.emitter.validate import iter_sections, read_byte, read_name, read_uleb

__all__ = [
    "MEMORY_EXPORT_NAME",
    "AssembledModule",
    "assemble",
    "check_call_targets",
    "check_linear_memory_abi",
    "check_no_bulk_memory",
    "recompute_import_names",
    "split_code_entries",
]

#: The name the Soroban host reads the guest's linear memory under. The M13
#: check below spells the same string as its OWN literal rather than reading
#: this constant (P15): a check that agrees with the constant it is guarding
#: cannot catch a wrong constant.
MEMORY_EXPORT_NAME = "memory"

#: One 64 KiB page, no maximum -- P12's whole layout (pool below `0x1000`,
#: scratch above it) fits in one page by construction, and `layout.Memory.check`
#: is what proves it for a given module.
MEMORY_PAGES = 1

_SEC_TYPE = 1
_SEC_IMPORT = 2
_SEC_FUNCTION = 3
_SEC_MEMORY = 5
_SEC_EXPORT = 7
_SEC_CODE = 10
_SEC_DATA = 11

#: The functype tag that opens every type-section entry.
_FUNCTYPE = 0x60

#: Import/export descriptor kinds. Only these two appear: D imports nothing but
#: functions and exports nothing but functions and the one memory.
_KIND_FUNC = 0x00
_KIND_MEMORY = 0x02

#: Limits flags `0x00`: a minimum with no maximum.
_LIMITS_MIN_ONLY = 0x00

#: Every bulk-memory instruction is encoded `0xFC <sub-opcode>`; the prefix is
#: what `check_no_bulk_memory` looks for.
_BULK_MEMORY_PREFIX = 0xFC

#: The wasm type names a functype can carry, as valtype bytes. Only `i64`
#: appears: every Soroban `Val` is an i64, and `frame.Fn` keeps its transient
#: `i32`s inside a single expression lowering, so no signature ever names one.
#: A missing key is an EmitError rather than a `KeyError` (`_functype` below),
#: because a signature type this table cannot spell is a compiler bug that must
#: not be reported as a dict lookup.
_VALTYPE = {"i64": opcodes.VALTYPE_I64}

#: Function kinds that get an entry in the export section (B.1 row 7).
_EXPORTED_KINDS = (FuncKind.EXPORT, FuncKind.CONSTRUCTOR)


@dataclass(frozen=True)
class _Defined:
    """One defined function ready for sections 3, 7 and 10.

    Module functions and runtime parts arrive from different places -- a
    ``frame.Fn`` and an ``arith.Part`` -- and this is the one shape the layout
    reads, so neither path can grow a field the other lacks.
    """

    name: str
    nparams: int
    nlocals: int
    results: tuple[str, ...]
    body: tuple[CodeItem, ...]


# --- the two checks that run before any byte is laid out ------------------------


def check_no_bulk_memory() -> None:
    """Refuse a module whose emitter could have written a bulk-memory instruction.

    B.1 row 12: the DataCount section is mandatory *only* when ``memory.init``
    or ``data.drop`` appears -- and mandatory BEFORE the code section when it
    is, an ordering wart worth never meeting. Omitting section 12 is therefore
    sound exactly while no bulk-memory instruction can be emitted.

    The check is over the VOCABULARY, not the bytes, and that is the whole
    point: every instruction byte the emitter writes comes from ``opcodes`` (its
    two provenance sets partition the constant names, pinned by
    ``test_emitter_opcodes.py``), so if no constant carries the ``0xFC`` prefix,
    no body can contain one. Scanning the emitted bytes instead would
    false-positive on the first ``0xFC`` that happened to fall inside an LEB128
    operand -- which is why the assertion is written this way round.
    """
    vocabulary = opcodes.ON_CHAIN_VERIFIED | opcodes.SPEC_PINNED
    offenders = sorted(
        name
        for name in vocabulary
        if getattr(opcodes, name) == _BULK_MEMORY_PREFIX or getattr(opcodes, name) > 0xFF
    )
    if offenders:
        raise EmitError(
            f"the opcode table now carries prefixed instruction(s) {offenders}: a "
            f"bulk-memory instruction ({_BULK_MEMORY_PREFIX:#04x}-prefixed) makes the "
            "DataCount section (id 12) mandatory, and it must precede the code "
            "section -- teach module.py to emit it before emitting one"
        )


def check_linear_memory_abi(
    import_names: Iterable[str], memory_export_names: Sequence[str]
) -> None:
    """The M13 assertion: an LM import requires the exported ``memory`` (S10/P15).

    If the module imports any of the ``*_from_linear_memory`` host functions,
    the host will read the guest's memory -- through an export named, exactly,
    ``memory``. Without it the contract does not fail at build time or at
    instantiation; it fails on the first literal it constructs, on chain.

    The literal is spelled HERE (P15), separately from
    ``MEMORY_EXPORT_NAME``: this check's job is to catch a wrong constant, and
    it cannot do that by reading the constant. A named function rather than an
    inline block because the negative control needs a callable seam (review m6).
    """
    used = sorted(name for name in import_names if name in _LINEAR_MEMORY_HOST_FNS)
    if not used:
        return
    if len(memory_export_names) != 1:
        raise EmitError(
            f"the module imports linear-memory host function(s) {used} but exports "
            f"{len(memory_export_names)} memories ({list(memory_export_names)}); it "
            "needs exactly one"
        )
    if memory_export_names[0] != "memory":
        raise EmitError(
            f"the module imports linear-memory host function(s) {used} but exports its "
            f"memory as {memory_export_names[0]!r}; the host reads guest memory under "
            "the name 'memory' and nothing else"
        )


# --- review B1's pass-2 safety net ---------------------------------------------


def recompute_import_names(wasm: bytes) -> tuple[str, ...]:
    """The host-function names an assembled module imports, re-read from its bytes.

    Half of review B1's net (see this module's docstring for the split): pass 2
    resolved every ``CallImport`` against an index map built from
    ``EmitCtx.import_order``, and this recomputes the same map from the FINAL
    import section -- ``(module, field)`` pairs mapped back through the pin -- so
    ``assemble`` can assert the two agree. It says nothing about any call
    immediate; ``check_call_targets`` is the half that reads those.
    """
    by_pair = {(fn.module, fn.export): fn.name for fn in HOST_FUNCTIONS}
    names: list[str] = []
    for sid, payload in iter_sections(wasm):
        if sid != _SEC_IMPORT:
            continue
        count, i = read_uleb(payload, 0)
        for _ in range(count):
            module_name, i = read_name(payload, i)
            field, i = read_name(payload, i)
            kind = read_byte(payload, i, f"import ({module_name!r}, {field!r})'s descriptor")
            _index, i = read_uleb(payload, i + 1)
            if kind != _KIND_FUNC:
                raise EmitError(
                    f"import ({module_name!r}, {field!r}) has kind {kind:#04x}; D imports "
                    "nothing but functions"
                )
            name = by_pair.get((module_name, field))
            if name is None:
                raise EmitError(
                    f"import ({module_name!r}, {field!r}) is not in the pin "
                    "(serpent._host.HOST_FUNCTIONS): the emitted import section and the "
                    "host-function registry have diverged"
                )
            names.append(name)
    return tuple(names)


# --- decoding the emitted bodies (the other half of B1's net) -------------------

#: Immediate shapes, keyed by instruction NAME. Every name in `opcodes`' two
#: provenance sets that is not a valtype/blocktype appears here exactly once,
#: and `_INSTRUCTION_IMMEDIATES` below asserts that partition -- so a later task
#: adding an opcode fails loudly here instead of desynchronizing this decoder
#: (which would then report a bogus call target three instructions later).
_IMM_NONE = "none"
_IMM_BLOCKTYPE = "blocktype"  # one byte: 0x40 (void) or a valtype
_IMM_ULEB = "uleb"  # a label, local, or function index
_IMM_SLEB = "sleb"  # a const operand
_IMM_MEMARG = "memarg"  # two ulebs: alignment then offset

_IMMEDIATE_BY_NAME: dict[str, str] = {
    "UNREACHABLE": _IMM_NONE,
    "BLOCK": _IMM_BLOCKTYPE,
    "LOOP": _IMM_BLOCKTYPE,
    "IF": _IMM_BLOCKTYPE,
    "ELSE": _IMM_NONE,
    "END": _IMM_NONE,
    "BR": _IMM_ULEB,
    "BR_IF": _IMM_ULEB,
    "RETURN": _IMM_NONE,
    "CALL": _IMM_ULEB,
    "DROP": _IMM_NONE,
    "LOCAL_GET": _IMM_ULEB,
    "LOCAL_SET": _IMM_ULEB,
    "LOCAL_TEE": _IMM_ULEB,
    "I32_CONST": _IMM_SLEB,
    "I64_CONST": _IMM_SLEB,
    "I64_EQZ": _IMM_NONE,
    "I64_EQ": _IMM_NONE,
    "I64_NE": _IMM_NONE,
    "I64_LT_S": _IMM_NONE,
    "I64_LT_U": _IMM_NONE,
    "I64_GT_S": _IMM_NONE,
    "I64_GT_U": _IMM_NONE,
    "I64_LE_S": _IMM_NONE,
    "I64_LE_U": _IMM_NONE,
    "I64_GE_S": _IMM_NONE,
    "I64_GE_U": _IMM_NONE,
    "I64_ADD": _IMM_NONE,
    "I64_SUB": _IMM_NONE,
    "I64_MUL": _IMM_NONE,
    "I64_DIV_S": _IMM_NONE,
    "I64_DIV_U": _IMM_NONE,
    "I64_REM_S": _IMM_NONE,
    "I64_REM_U": _IMM_NONE,
    "I64_AND": _IMM_NONE,
    "I64_OR": _IMM_NONE,
    "I64_XOR": _IMM_NONE,
    "I64_SHL": _IMM_NONE,
    "I64_SHR_S": _IMM_NONE,
    "I64_SHR_U": _IMM_NONE,
    "I32_WRAP_I64": _IMM_NONE,
    "I64_EXTEND_I32_U": _IMM_NONE,
    "I64_EXTEND32_S": _IMM_NONE,
    "I64_LOAD": _IMM_MEMARG,
    "I64_STORE": _IMM_MEMARG,
}


def _instruction_immediates() -> dict[int, str]:
    """``opcode byte -> immediate shape``, built from the provenance sets.

    Valtype and blocktype names are excluded by prefix: their bytes are not
    instructions, and `0x7E` really is `i64.mul`, the `i64` valtype, AND an
    `if (result i64)` blocktype at once (B.2's named trap). Two instruction names
    sharing a byte, on the other hand, would make this table ambiguous, so it is
    refused.
    """
    names = {
        name
        for name in opcodes.ON_CHAIN_VERIFIED | opcodes.SPEC_PINNED
        if not name.startswith(("VALTYPE_", "BLOCKTYPE_"))
    }
    if names != set(_IMMEDIATE_BY_NAME):
        missing = sorted(names - set(_IMMEDIATE_BY_NAME))
        extra = sorted(set(_IMMEDIATE_BY_NAME) - names)
        raise EmitError(
            "the immediate table and the opcode vocabulary disagree: "
            f"missing {missing}, unknown {extra}. A body decoder that does not know "
            "an instruction's operand length desynchronizes silently, so this table "
            "must be extended in the same commit as the opcode"
        )
    table: dict[int, str] = {}
    for name, shape in _IMMEDIATE_BY_NAME.items():
        byte = getattr(opcodes, name)
        if byte in table:
            raise EmitError(f"two instructions share byte {byte:#04x}; the table is ambiguous")
        table[byte] = shape
    return table


def _call_targets(entry: bytes, where: str) -> list[int]:
    """Every ``call`` immediate in one CODE-SECTION ENTRY, in body order.

    Walks the locals declaration first, then the instruction stream. This is a
    real decode over the emitter's whole instruction vocabulary rather than a
    scan for ``0x10`` bytes: ``0x10`` occurs constantly inside `i64.const`
    operands and local indices, so a scan would report call targets that are not
    calls (and miss the check entirely on the ones that are).
    """
    immediates = _instruction_immediates()
    groups, i = read_uleb(entry, 0)
    for _ in range(groups):
        _count, i = read_uleb(entry, i)
        read_byte(entry, i, f"{where}'s locals declaration")
        i += 1

    targets: list[int] = []
    while i < len(entry):
        opcode = entry[i]
        at = i
        i += 1
        shape = immediates.get(opcode)
        if shape is None:
            raise EmitError(
                f"{where}: byte {opcode:#04x} at offset {at} is not an instruction the "
                "emitter can produce; the body and the opcode table have diverged"
            )
        if shape == _IMM_NONE:
            continue
        if shape == _IMM_BLOCKTYPE:
            read_byte(entry, i, f"{where}'s blocktype at offset {at}")
            i += 1
        elif shape == _IMM_ULEB:
            value, i = read_uleb(entry, i)
            if opcode == opcodes.CALL:
                targets.append(value)
        elif shape == _IMM_SLEB:
            i = _skip_leb(entry, i, f"{where}'s const operand at offset {at}")
        else:  # _IMM_MEMARG
            _align, i = read_uleb(entry, i)
            _offset, i = read_uleb(entry, i)
    return targets


def _skip_leb(data: bytes, i: int, what: str) -> int:
    """Step past one LEB128 operand, signed or not (the framing is identical)."""
    while True:
        byte = read_byte(data, i, what)
        i += 1
        if not byte & 0x80:
            return i


def split_code_entries(payload: bytes) -> list[bytes]:
    """Every code-section entry's raw bytes, in body order.

    The size-prefixed-vector walk shared by `_decode_code_section` below
    (which decodes only the `call` targets out of each entry) and
    `serpent.emitter.printer` (which decodes the WHOLE entry -- locals plus
    every instruction -- for disassembly): both need the same per-entry byte
    slices first, so that splitting lives in exactly one place rather than
    two copies of the same truncation check drifting apart.
    """
    count, i = read_uleb(payload, 0)
    entries: list[bytes] = []
    for index in range(count):
        size, i = read_uleb(payload, i)
        if i + size > len(payload):
            raise EmitError(
                f"truncated code section: body {index} declares {size} bytes but "
                f"only {len(payload) - i} remain"
            )
        entries.append(payload[i : i + size])
        i += size
    return entries


def _decode_code_section(wasm: bytes) -> list[list[int]]:
    """The ``call`` targets of every function body, in definition order."""
    bodies: list[list[int]] = []
    for sid, payload in iter_sections(wasm):
        if sid != _SEC_CODE:
            continue
        for index, entry in enumerate(split_code_entries(payload)):
            bodies.append(_call_targets(entry, f"body {index}"))
    return bodies


def check_call_targets(wasm: bytes) -> None:
    """Every ``call`` in every body names a function the module really has.

    The other half of review B1's net, and the half that reads the immediates:
    each target either indexes the import section (and so resolves, through the
    pin, to a registered host function) or lands inside the defined range
    ``[n_imports, n_imports + n_defined)``. A target past the end is the
    wrong-target class -- and note it is a class wasm-tools cannot narrow for us
    beyond "in range", because every host function shares the all-i64 shape (B3),
    so a call to the wrong function of the right arity type-checks.
    """
    imports = recompute_import_names(wasm)
    n_imports = len(imports)
    bodies = _decode_code_section(wasm)
    total = n_imports + len(bodies)
    for index, targets in enumerate(bodies):
        for target in targets:
            if target >= total:
                raise EmitError(
                    f"body {index} calls function {target}, but the module has "
                    f"{n_imports} imports and {len(bodies)} defined functions "
                    f"({total} in the combined index space)"
                )


# --- pass 2 helpers -------------------------------------------------------------


def _valtypes(names: Sequence[str]) -> list[bytes]:
    out: list[bytes] = []
    for name in names:
        byte = _VALTYPE.get(name)
        if byte is None:
            raise EmitError(
                f"no valtype byte for wasm type {name!r}; the type section can spell "
                f"{sorted(_VALTYPE)} and nothing else"
            )
        out.append(bytes([byte]))
    return out


def _functype(params: Sequence[str], results: Sequence[str]) -> bytes:
    return bytes([_FUNCTYPE]) + encode.vec(_valtypes(params)) + encode.vec(_valtypes(results))


class _TypeTable:
    """The deduped type section, in first-mention order."""

    def __init__(self) -> None:
        self._entries: list[bytes] = []

    def index(self, params: Sequence[str], results: Sequence[str]) -> int:
        entry = _functype(params, results)
        if entry not in self._entries:
            self._entries.append(entry)
        return self._entries.index(entry)

    def section(self) -> bytes:
        return encode.section(_SEC_TYPE, encode.vec(self._entries))


def _serialize(
    where: str,
    items: Sequence[CodeItem],
    import_index: Mapping[str, int],
    n_imports: int,
    n_defined: int,
) -> bytes:
    """Pass 2 for one body: resolve every symbolic call site (review B1)."""
    out = bytearray()
    for item in items:
        if isinstance(item, bytes):
            out += item
        elif isinstance(item, CallImport):
            index = import_index.get(item.name)
            if index is None:
                raise EmitError(
                    f"{where} calls host function {item.name!r}, which is not in the "
                    "emitted import list; every call site registers its import through "
                    "EmitCtx.host_import_name, so this is a compiler bug"
                )
            out += bytes([opcodes.CALL]) + encode.uleb(index)
        elif isinstance(item, CallDefined):
            if not 0 <= item.defidx < n_defined:
                raise EmitError(
                    f"{where} calls defined function {item.defidx}, outside the "
                    f"{n_defined} functions this module defines"
                )
            out += bytes([opcodes.CALL]) + encode.uleb(n_imports + item.defidx)
        else:  # pragma: no cover - CodeItem is a closed union
            raise EmitError(f"{where}: not a code item: {item!r}")
    return bytes(out)


def _code_entry(defined: _Defined, body: bytes) -> bytes:
    """One code-section entry: the locals declaration, the body, size-prefixed.

    One ``i64`` group covers every local (declared plus hidden): every value D
    keeps in a local is a ``Val`` word. A function with none declares an EMPTY
    group vector rather than a zero-count group.
    """
    groups = (
        [encode.uleb(defined.nlocals) + bytes([opcodes.VALTYPE_I64])] if defined.nlocals else []
    )
    entry = encode.vec(groups) + body
    return encode.uleb(len(entry)) + entry


def _data_section(pool: bytes) -> bytes:
    """One ACTIVE segment at offset 0 of memory 0 (B.1 row 11)."""
    segment = (
        encode.uleb(0)
        + bytes([opcodes.I32_CONST])
        + encode.sleb(0)
        + bytes([opcodes.END])
        + encode.uleb(len(pool))
        + pool
    )
    return encode.section(_SEC_DATA, encode.vec([segment]))


# --- the assembler --------------------------------------------------------------


@dataclass(frozen=True)
class AssembledModule:
    """Everything ``assemble`` produced: the wasm bytes plus pass 1's own facts.

    The Task 10 handoff record, controller-adopted: ``build_wasm`` and
    ``BuildResult`` (Task 11) need several facts that are cheap here and
    expensive -- or, for ``expect_memory``, impossible -- to recompute
    independently from ``wasm`` afterwards.

    * ``expect_memory`` is ruling E10's own decision (``needs_memory`` above),
      handed straight to ``validate.validate_internal`` -- the validator must
      not re-derive it, since it has no other way to distinguish "no literals
      and no linear-memory host call" from "assembled memoryless on purpose".
    * ``import_names``/``exports`` are the emitted host-fn import names and the
      contract's own exported names (``FuncKind.EXPORT``/``CONSTRUCTOR``),
      already computed for the import/export sections above.
    * ``runtime_parts_linked`` is ``ctx.parts_linked`` (Task 13's superset of
      C's ``runtime_parts_needed`` hint) -- which parts THIS build actually
      linked, not which the frontend merely expected.
    * ``pool_size``/``scratch_size`` are ``layout.Memory``'s own byte counts:
      the literal pool's length and the scratch bump allocator's total
      reservation (``Memory.scratch_size``), the same two numbers
      ``Memory.check`` guards against ``SCRATCH_BASE``/``PAGE``.
    """

    wasm: bytes
    expect_memory: bool
    import_names: tuple[str, ...]
    exports: tuple[str, ...]
    runtime_parts_linked: frozenset[str]
    pool_size: int
    scratch_size: int


def assemble(
    compiled: CompiledModule, *, meta: Mapping[str, str], version: str | None
) -> AssembledModule:
    """Assemble ``compiled`` into an ``AssembledModule`` (see this module's docstring).

    ``meta`` is the user's ``contractmetav0`` pairs and ``version`` the
    contract's own version, omitted from the section when ``None`` (ruling E8).
    A ``meta`` key colliding with a reserved one raises ``ValueError`` from
    ``build_meta``; every other failure here is an ``EmitError``, or a
    ``BuildLimitError`` for a budget the contract outgrew.
    """
    check_no_bulk_memory()
    contract = compiled.ir.contract
    if contract is None:
        raise EmitError(
            "ir.contract is None; that state always comes with a diagnostic and a "
            "module with diagnostics never reaches the emitter (C14) -- reaching it "
            "here is a compiler bug"
        )
    if compiled.spec_inputs.contract_cls is None:
        raise EmitError(
            "spec_inputs.contract_cls is None; like ir.contract that state always comes "
            "with a diagnostic (dossier C.3) and cannot reach the emitter"
        )

    # === pass 1: lower everything, then freeze the layout ======================
    memory = Memory()
    # The FULL inventory, before any body: `Memory.intern` appends an unseeded
    # blob silently, so seeding first is what keeps pool offsets a pure function
    # of the inventory (E7, Task 7's carried constraint).
    memory.seed(compiled.literals)
    ctx = LowerCtx(
        n_module_functions=len(compiled.functions),
        memory=memory,
        consts={decl.name: decl.value for decl in compiled.ir.consts},
        functions={func.py_name: index for index, func in enumerate(compiled.functions)},
    )

    defined: list[_Defined] = []
    for func in compiled.functions:
        fn = compile_function(func, ctx)
        defined.append(
            _Defined(
                name=fn.name,
                nparams=fn.nparams,
                nlocals=fn.nlocals,
                results=fn.results,
                body=tuple(fn.finish()),
            )
        )
    # Every part a lowering reached, in the `defidx` order `ensure_part`
    # promised -- appended AFTER the module's own functions, which is what makes
    # a part's index stable from its first call.
    for part in ctx.parts:
        defined.append(
            _Defined(
                name=part.name,
                nparams=part.nparams,
                nlocals=part.nlocals,
                results=part.results,
                body=part.body,
            )
        )
    for offset, part in enumerate(ctx.parts):
        if part.defidx != len(compiled.functions) + offset:
            raise EmitError(
                f"runtime part {part.name!r} was promised defidx {part.defidx} but sits "
                f"at {len(compiled.functions) + offset} in the defined index space"
            )

    # Both layout guards, once the last scratch slot has been handed out.
    memory.check()

    import_order = ctx.import_order
    pool = memory.pool_bytes()

    # === ruling E10: does THIS module need linear memory? ======================
    # D's own post-lowering facts, not C's over-approximation. All four are
    # spelled out even though they overlap (`memory.is_empty` already covers the
    # pool, and `ctx.needs_memory` is a scratch reservation): each is a separate
    # reason a page is needed, and a reader checking one of them against the
    # ruling should find it by name rather than by inference.
    literal_or_lm = bool(pool) or any(name in _LINEAR_MEMORY_HOST_FNS for name in import_order)
    needs_memory = literal_or_lm or not memory.is_empty or ctx.needs_memory
    if not compiled.needs_memory and literal_or_lm:
        # The RESTRICTED consistency assertion (review B8). C computed the
        # literal/linear-memory answer itself, so disagreement there is a
        # compiler bug. Runtime-part SCRATCH is deliberately NOT part of this
        # test: the 128-bit two-result convention forces a memory C could not
        # foresee, and that is correct.
        raise EmitError(
            f"the frontend reported needs_memory=False, but the emitter pooled "
            f"{len(pool)} literal bytes and imported {sorted(import_order)}; C's answer "
            "over-approximates (C21), so it may be True where D disagrees -- never "
            "False"
        )

    memory_export_names = (MEMORY_EXPORT_NAME,) if needs_memory else ()
    # BEFORE the layout (S10/P15): the failure must precede the artifact.
    check_linear_memory_abi(import_order, memory_export_names)

    # === pass 2: sections, in the order the format fixes ======================
    types = _TypeTable()

    import_entries: list[bytes] = []
    for name in import_order:
        host_fn = functions_by_name[name]
        import_entries.append(
            encode.wasm_name(host_fn.module)
            + encode.wasm_name(host_fn.export)
            + bytes([_KIND_FUNC])
            + encode.uleb(types.index(host_fn.wasm_params, (host_fn.wasm_result,)))
        )

    func_entries = [
        encode.uleb(types.index(("i64",) * entry.nparams, entry.results)) for entry in defined
    ]

    n_imports = len(import_entries)
    export_entries: list[bytes] = []
    for index, func in enumerate(compiled.functions):
        if func.kind not in _EXPORTED_KINDS:
            continue
        export_entries.append(
            encode.wasm_name(func.export_name)
            + bytes([_KIND_FUNC])
            + encode.uleb(n_imports + index)
        )
    for name in memory_export_names:
        export_entries.append(encode.wasm_name(name) + bytes([_KIND_MEMORY]) + encode.uleb(0))

    import_index = {name: index for index, name in enumerate(import_order)}
    code_entries = [
        _code_entry(
            entry,
            _serialize(entry.name, entry.body, import_index, n_imports, len(defined)),
        )
        for entry in defined
    ]

    out = bytearray(b"\x00asm\x01\x00\x00\x00")
    out += types.section()
    if import_entries:
        out += encode.section(_SEC_IMPORT, encode.vec(import_entries))
    out += encode.section(_SEC_FUNCTION, encode.vec(func_entries))
    if needs_memory:
        out += encode.section(
            _SEC_MEMORY,
            encode.vec([bytes([_LIMITS_MIN_ONLY]) + encode.uleb(MEMORY_PAGES)]),
        )
    out += encode.section(_SEC_EXPORT, encode.vec(export_entries))
    out += encode.section(_SEC_CODE, encode.vec(code_entries))
    if pool:
        out += _data_section(pool)

    out += encode.custom_section(
        sections.ENV_META_SECTION_NAME, sections.env_meta_payload(compiled)
    )
    out += encode.custom_section(sections.SPEC_SECTION_NAME, sections.spec_payload(compiled))
    out += encode.custom_section(
        sections.META_SECTION_NAME, sections.meta_payload(compiled, meta, version)
    )

    wasm = bytes(out)
    # Review B1's net, closed: the index map every call site was resolved
    # against, re-derived from the bytes that shipped.
    recomputed = recompute_import_names(wasm)
    if recomputed != import_order:
        raise EmitError(
            f"the emitted import section reads back as {recomputed} but pass 2 resolved "
            f"call targets against {import_order}: every baked call index is wrong"
        )
    check_call_targets(wasm)
    _check_call_sites_agree(wasm, defined, recomputed)
    return AssembledModule(
        wasm=wasm,
        expect_memory=needs_memory,
        import_names=import_order,
        # `FuncKind.EXPORT`/`CONSTRUCTOR` in `compiled.functions` order -- the
        # same filter `export_entries` above used to build the export section,
        # so the two can never disagree about which names are exported.
        exports=tuple(
            func.export_name for func in compiled.functions if func.kind in _EXPORTED_KINDS
        ),
        runtime_parts_linked=ctx.parts_linked,
        pool_size=len(pool),
        scratch_size=memory.scratch_size,
    )


def _check_call_sites_agree(
    wasm: bytes, defined: Sequence[_Defined], imports: Sequence[str]
) -> None:
    """Every baked call index equals what the SYMBOLIC site says it should be.

    The closing half of review B1's net. The expected sequence is rebuilt from
    the symbolic items -- which carry a NAME and a defined-space index, never a
    combined-space one -- against the index map and import count read back out
    of the emitted bytes. So a serializer that resolved a body against a stale
    map, dropped ``n_imports`` from a defined target, or mis-encoded an LEB
    disagrees here, at the function and position where it happened, instead of
    surfacing as one mini-host test that happens to exercise that call.

    What it cannot catch, stated plainly: a lowering that recorded the wrong NAME
    in the first place. Every host function shares the all-i64 shape (B3), so
    that call validates everywhere -- the defence against it is that the name is
    the return value of ``EmitCtx.host_import_name``, not a literal at the call
    site.
    """
    index_of = {name: index for index, name in enumerate(imports)}
    n_imports = len(imports)
    decoded = _decode_code_section(wasm)
    if len(decoded) != len(defined):
        raise EmitError(
            f"the emitted code section holds {len(decoded)} bodies but the module "
            f"defines {len(defined)} functions"
        )
    for entry, targets in zip(defined, decoded, strict=True):
        expected: list[int] = []
        for item in entry.body:
            if isinstance(item, CallImport):
                expected.append(index_of[item.name])
            elif isinstance(item, CallDefined):
                expected.append(n_imports + item.defidx)
        if targets != expected:
            raise EmitError(
                f"{entry.name}: the emitted body calls {targets} but its symbolic call "
                f"sites resolve to {expected} against the index space the module "
                "actually shipped"
            )
