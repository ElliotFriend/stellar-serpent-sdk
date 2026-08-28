"""A minimal hand-assembled WASM module builder, for executing lowered bodies.

**Test-only, and deliberately simple.** This is not the emitter's module
assembler (that is Task 10's job, with the Soroban custom sections, the spec
entries, and the metadata a real contract needs). It exists so the lowering
tasks can take a `frame.Fn`'s finished body, wrap it in the smallest module
wasmtime will accept, and *run* it -- months before there is anything else to
run it in.

Bodies arrive **symbolic** (review B1): a `Sequence[bytes | CallImport |
CallDefined]`, exactly what `Fn.finish()` returns. Pass 2 lives here, and it is
the whole reason the split exists -- no function index is baked into a body
before the section that defines it is frozen, so the call-the-wrong-function-
but-still-validate bug class is structurally impossible rather than merely
avoided. `imports` fixes the import order; `CallImport(name)` resolves through
it by name, `CallDefined(d)` resolves to `len(imports) + d`.
"""

from collections.abc import Sequence

from serpent._host import functions_by_name
from serpent.emitter import encode, opcodes
from serpent.emitter.frame import CallDefined, CallImport, CodeItem

#: `(name, nparams, nlocals, results, body_items)`. `nlocals` counts the i64
#: locals declared after the params; `results` is `()` or `("i64",)`.
FunctionSpec = tuple[str, int, int, tuple[str, ...], Sequence[CodeItem]]

_MAGIC = b"\x00asm\x01\x00\x00\x00"

_SEC_TYPE = 1
_SEC_IMPORT = 2
_SEC_FUNCTION = 3
_SEC_MEMORY = 5
_SEC_EXPORT = 7
_SEC_CODE = 10
_SEC_DATA = 11

_FUNCTYPE = 0x60  # spec-pinned: the functype tag in a type section entry
_VALTYPE_I32 = 0x7F  # spec-pinned: `opcodes` has no i32 valtype constant
_KIND_FUNC = 0x00  # spec-pinned: import/export descriptor kinds
_KIND_MEMORY = 0x02

#: The memory a Soroban module exports, spelled the way the host expects.
MEMORY_EXPORT_NAME = "memory"

_VALTYPES = {"i64": opcodes.VALTYPE_I64, "i32": _VALTYPE_I32}


def _functype(params: Sequence[str], results: Sequence[str]) -> bytes:
    return (
        bytes([_FUNCTYPE])
        + encode.vec([bytes([_VALTYPES[t]]) for t in params])
        + encode.vec([bytes([_VALTYPES[t]]) for t in results])
    )


def _serialize(items: Sequence[CodeItem], first_defined: int, order: Sequence[str]) -> bytes:
    """Pass 2: resolve every symbolic call site against the frozen index space."""
    import_index = {name: i for i, name in enumerate(order)}
    out = bytearray()
    for item in items:
        if isinstance(item, bytes):
            out += item
        elif isinstance(item, CallImport):
            out += bytes([opcodes.CALL]) + encode.uleb(import_index[item.name])
        elif isinstance(item, CallDefined):
            out += bytes([opcodes.CALL]) + encode.uleb(first_defined + item.defidx)
        else:  # pragma: no cover - the union is closed
            raise TypeError(f"not a code item: {item!r}")
    return bytes(out)


def build_test_module(
    functions: Sequence[FunctionSpec],
    imports: Sequence[str] = (),
    memory_pages: int | None = None,
    data: bytes | None = None,
) -> bytes:
    """Assemble the smallest valid module holding `functions`.

    `imports` are host-function names; their `(module, field)` strings and
    arities come from the pin (`serpent._host.functions_by_name`), never from a
    literal, and an unknown name raises `KeyError` naming it. Every function is
    exported under its own name, and the memory -- present iff `memory_pages` is
    given -- is exported as `memory`. `data` becomes one active segment at
    offset 0 of memory 0.
    """
    if data is not None and memory_pages is None:
        raise ValueError("a data segment needs a memory: pass memory_pages")

    types: list[bytes] = []

    def type_index(params: Sequence[str], results: Sequence[str]) -> int:
        entry = _functype(params, results)
        if entry not in types:
            types.append(entry)
        return types.index(entry)

    # Import entries first, so the type section's order follows the module's.
    import_entries: list[bytes] = []
    for name in imports:
        host_fn = functions_by_name[name]
        import_entries.append(
            encode.wasm_name(host_fn.module)
            + encode.wasm_name(host_fn.export)
            + bytes([_KIND_FUNC])
            + encode.uleb(type_index(host_fn.wasm_params, (host_fn.wasm_result,)))
        )

    func_types = [
        encode.uleb(type_index(("i64",) * nparams, results))
        for _name, nparams, _nlocals, results, _body in functions
    ]

    first_defined = len(imports)
    exports = [
        encode.wasm_name(name) + bytes([_KIND_FUNC]) + encode.uleb(first_defined + i)
        for i, (name, *_rest) in enumerate(functions)
    ]
    if memory_pages is not None:
        exports.append(
            encode.wasm_name(MEMORY_EXPORT_NAME) + bytes([_KIND_MEMORY]) + encode.uleb(0)
        )

    code_entries: list[bytes] = []
    for _name, _nparams, nlocals, _results, body in functions:
        decl = encode.vec([encode.uleb(nlocals) + bytes([opcodes.VALTYPE_I64])] if nlocals else [])
        entry = decl + _serialize(body, first_defined, imports)
        code_entries.append(encode.uleb(len(entry)) + entry)

    out = bytearray(_MAGIC)
    out += encode.section(_SEC_TYPE, encode.vec(types))
    if import_entries:
        out += encode.section(_SEC_IMPORT, encode.vec(import_entries))
    out += encode.section(_SEC_FUNCTION, encode.vec(func_types))
    if memory_pages is not None:
        # Flags 0x00: a minimum, no maximum.
        out += encode.section(_SEC_MEMORY, encode.vec([b"\x00" + encode.uleb(memory_pages)]))
    out += encode.section(_SEC_EXPORT, encode.vec(exports))
    out += encode.section(_SEC_CODE, encode.vec(code_entries))
    if data is not None:
        # One active segment in memory 0 at offset 0. Emitted for `data=b""` too,
        # so "was a data section requested?" is the same question everywhere in
        # this function -- `if data:` would make an empty payload silently mean
        # "no segment" while the ValueError above says it means "a segment".

        segment = (
            encode.uleb(0)
            + bytes([opcodes.I32_CONST])
            + encode.sleb(0)
            + bytes([opcodes.END])
            + encode.uleb(len(data))
            + data
        )
        out += encode.section(_SEC_DATA, encode.vec([segment]))
    return bytes(out)
