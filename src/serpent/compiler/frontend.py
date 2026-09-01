"""`compile_module`: the frontend's one public entry point (Task 10).

Tasks 3 through 9 each own one phase; this module is the assembly that runs
them in order, builds the `ir.ModuleIR` out of what they produced, and derives
the five things dossier SS C.2 says C must hand sub-plan D "beyond the tree":

1. `host_fns_used` -- feeds `_host.declared_protocol` (B4/S18) and the import
   section. `host_fns_reachable` is its conservative twin; see below.
2. `needs_memory` plus the literal inventory (`LiteralInventory`) -- the SS 5
   memory-export decision and the data-section layout (S19).
3. `runtime_parts_needed` -- the guest-runtime pieces D must link (spec SS 6).
4. `spec_inputs` -- B9/B10's "`types` is DECLARED, not discovered" inventory,
   with events kept SEPARATE (MJ-9/B14).
5. `diagnostics` -- which "must be empty for D to run", and so are not a field
   at all: a compile with any diagnostic raises `CompileError` and hands back
   no `CompiledModule` (E16's collect-all decides WHEN to raise, not whether).

## The pipeline

    load_module            parse, shape-check, execute, cross-check (E1)
    validate_limits        the SPT5xxx band, pre-empted against the AST (S12)
    check_declarations     structs/enums/events/consts/signatures + call graph
    per function:          FuncCtx  ->  check_function_body  ->  FuncIR
    protocol floor         declared_protocol over the reachable host-fn set,
                           raised by the FEATURE gates (`_resolve_protocol`)

Everything reports into ONE sink, so a 200-line contract yields every problem
in a single `CompileError` sorted by location (E16) rather than one per run.
The only phase that cannot collect is parsing: with no tree there is nothing to
keep going with, so `load_module` raises for a `SyntaxError`.

## `FuncCtx` seeding is not optional

`FuncCtx.fn_kind` and `FuncCtx.has_self` are seeded from each function's own
`decls.FuncSig`, never derived from its name: two functions can share a name
(spec SS 2 nearly writes exactly that -- a module-level `balance` helper beside
a `balance` export), so only the declaration knows what it is. Their defaults
are the conservative answer, which means forgetting to seed them shows up as a
loud reject on a legitimate `self._helper(...)` rather than as a silent accept
of one that cannot run at tier 1.

## Two host-function sets, because D chooses some lowerings

`HostCall` names its function exactly (B2). Several SS C.2 nodes do not,
because the lowering FORM is D's choice: a `MakeVec`/`MakeMap` with
`all_static=True` may use the linear-memory constructor or the build-up chain
(MJ-15), and `MakeTopics` the same. So:

* `host_fns_used` is what the module DEFINITELY reaches -- every `HostCall`,
  plus the dedicated nodes whose SS C.4 row names exactly one function
  (`Raise` -> `fail_with_error`, an `obj_cmp` comparison, `FieldGet`,
  `MakeStruct`, and a `Symbol`/`String`/`Bytes` literal that needs linear
  memory).
* `host_fns_reachable` adds the alternatives D may pick instead. The protocol
  floor is computed over the REACHABLE set, because a floor computed over a
  subset could be too low for what D actually emits.

Three families are in NEITHER set, because C cannot decide them at all:

* the small-vs-object integer bridges (`obj_from_u64`/`obj_to_u64`,
  `obj_from_i64`/`obj_to_i64`) -- which one an expression needs depends on the
  VALUE at run time, not on the type (A3);
* the 128-bit piece constructors and accessors (`obj_from_u128_pieces`,
  `obj_to_u128_lo64`/`hi64` and the signed twins), plus the **i256 family D's
  128-bit division and remainder route through** (`obj_from_i256_pieces`,
  `i256_div`, `i256_rem_euclid` -- SS C.4's own row; the `i256_checked_*`
  variants, which ARE gated at protocol 26, are deliberately not that path);
* `Convert`'s `Timepoint`/`Duration` bridges (`timepoint_obj_from_u64`,
  `duration_obj_from_u64` and their `_to_u64` directions), which no Task 5-9
  checker emits today.

`tests/unit/test_frontend.py::test_the_omitted_host_fn_families_are_ungated`
enumerates all three and asserts none of them is gated above `BASE_PROTOCOL`.
That is the condition which makes the omissions floor-safe: an omitted name
could otherwise have raised the real floor above what C computed.
"""

from __future__ import annotations

import ast
import dataclasses
from dataclasses import dataclass
from typing import Any

from serpent import val
from serpent._host._protocol import (
    BASE_PROTOCOL,
    CONSTRUCTOR_MIN_PROTOCOL,
    DEFAULT_TARGET_PROTOCOL,
    ProtocolGateError,
    check_protocol_target,
    declared_protocol,
)
from serpent.compiler import codes
from serpent.compiler.ctx import AliasTable, FuncCtx, SlotTable
from serpent.compiler.decls import Declarations, FuncSig, check_declarations
from serpent.compiler.diagnostics import Diagnostics, Loc
from serpent.compiler.ir import Binary as BinaryNode
from serpent.compiler.ir import (
    Compare,
    Const,
    ContractIR,
    FieldGet,
    FuncIR,
    FuncKind,
    HostCall,
    IfExp,
    IRNode,
    MakeMap,
    MakeStruct,
    MakeTopics,
    MakeUnion,
    MakeVec,
    ModuleIR,
    Raise,
    RawScalar,
    RawScalarKind,
    Unary,
    UnaryOp,
    walk,
)
from serpent.compiler.limits import validate_limits
from serpent.compiler.loader import CompilerBugError, LoadedModule, load_module
from serpent.compiler.recognize import RECOGNIZED
from serpent.compiler.stmt import check_function_body
from serpent.compiler.types_ import Ty, TyTag

# The IR must not disagree with the spec about the same text (F.2.7), so the
# class docstring comes from `spec.sections`' own reader -- the same deliberate
# private-name import `decls.py` already makes for exactly this reason.
from serpent.spec.sections import _class_doc

__all__ = [
    "CompiledModule",
    "LiteralInventory",
    "SpecInputs",
    "compile_module",
    "guarded_storage_get",
]

_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

# --- the host functions the dedicated SS C.2 nodes name (B2: BY NAME) --------

#: `raise <ErrorEnum>.<Member>` -> `fail_with_error` (S7/R3). A module-level
#: name rather than an inline string so the SPT6xxx band can be exercised
#: end-to-end against a fake gated HostFn: no real M1-C surface is gated, which
#: is exactly why SPT6001 is on `codes.NO_FIXTURE_ALLOWLIST`.
_RAISE_HOST_FN = "fail_with_error"

#: A comparison on a HOST_OBJECT-repr type, and every `Symbol` comparison
#: (F.1.2/T5), routes through `obj_cmp`. `Compare.via_obj_cmp` is the decision
#: `expr.py` already recorded; this is only its host-function name.
_OBJ_CMP_HOST_FN = "obj_cmp"

#: The linear-memory literal constructors (S19/S22). A `Symbol` of 9 characters
#: or fewer is a SymbolSmall immediate and needs none of them.
_SYMBOL_LM_FN = "symbol_new_from_linear_memory"
_STRING_LM_FN = "string_new_from_linear_memory"
_BYTES_LM_FN = "bytes_new_from_linear_memory"

#: An `Address` literal's second host function (review B6). `Address` is a
#: HOST_OBJECT-repr type with no small Val form and no linear-memory
#: constructor of its OWN, so a literal one is built in two steps: pool the
#: strkey's ASCII bytes and call `_STRING_LM_FN`, then convert that
#: `StringObject` with `strkey_to_address` (`a.1`, whose `strkey` parameter
#: documents accepting either a `BytesObject` or a `StringObject`). Both names
#: are CERTAIN uses of an `Address` `Const` -- neither is a route D may choose
#: against.
_ADDRESS_FROM_STRKEY_FN = "strkey_to_address"

#: The three names ruling E13's storage-get accounting turns on, sourced from
#: the recognition table's own rows (MJ-5) rather than restated as literals.
_STORAGE_HAS_FN, _STORAGE_GET_FN = RECOGNIZED["storage.get_default"].host_fns
#: The guard's failure call -- the same `fail_with_error` a `raise` reaches.
_STORAGE_MISSING_FN = _RAISE_HOST_FN

#: Names sourced from the recognition table's own rows rather than restated
#: (MJ-5): the struct constructor, and the struct field read's two functions.
_STRUCT_NEW_FN = RECOGNIZED["struct.new"].host_fns[0]
_FIELD_GET_FN, _FIELD_SYMBOL_FN = RECOGNIZED["struct.field"].host_fns

#: Every host function a `Vec`/`Map` construction (or an event's topic vector)
#: CAN reach -- D picks between the linear-memory form and the build-up chain
#: from `all_static` (MJ-15), so all of them are reachable and none is certain.
_VEC_BUILD_FNS: tuple[str, ...] = RECOGNIZED["vec.new"].host_fns
_MAP_BUILD_FNS: tuple[str, ...] = RECOGNIZED["map.new"].host_fns

#: Every host function the frontend can name that reads from linear memory --
#: enumerated rather than matched on a `"linear_memory" in name` substring,
#: which would silently start answering for any future binding that happens to
#: share the suffix (`sparse_map_new_from_linear_memory`, gated at protocol 28,
#: is already in the pin) and would silently STOP answering if a re-pin renamed
#: one. `test_frontend.py` asserts every member is a real pinned binding.
_LINEAR_MEMORY_HOST_FNS: frozenset[str] = frozenset(
    {
        _SYMBOL_LM_FN,
        _STRING_LM_FN,
        _BYTES_LM_FN,
        _FIELD_SYMBOL_FN,
        _STRUCT_NEW_FN,
        "vec_new_from_linear_memory",
        "map_new_from_linear_memory",
    }
)

#: `TyTag` -> the guest-runtime prefix for the widths whose checked `Binary`
#: is a CALL rather than an inline sequence (ruling E3). 64-bit is here because
#: the unbox branch alone is 6+ instructions and appears at every use; 128-bit
#: because there is no native wasm instruction at all (spec SS 6). U32/I32 are
#: deliberately absent: their checked ops are a shift, an op, and a range
#: compare -- cheaper inline than the call overhead (S25's break-even).
_ARITH_PREFIX: dict[TyTag, str] = {
    TyTag.U64: "u64",
    TyTag.I64: "i64",
    TyTag.U128: "u128",
    TyTag.I128: "i128",
}

#: `TyTag` -> the guest-runtime prefix for the widths whose `NEG` and whose
#: direct (non-`obj_cmp`) `Compare` are also calls: 128-bit only. At 32 and 64
#: bits `NEG` is inline (review M6 -- unsigned is "nonzero is overflow, else
#: 0", signed is "MIN is overflow, else 0 - value") and a compare is one wasm
#: relop; at 128 bits both are limb code.
_WIDE_ARITH_PREFIX: dict[TyTag, str] = {TyTag.U128: "u128", TyTag.I128: "i128"}


# --- the outputs --------------------------------------------------------------


@dataclass(frozen=True)
class LiteralInventory:
    """The literals D must lay out in linear memory (SS C.2 output 2, S19).

    Each tuple is deduplicated and sorted, so the inventory is a property of
    the module rather than of the order the checker happened to walk it.

    * `symbols_over_9` -- `Symbol` literals past the SymbolSmall bound (S22),
      which are the only `Symbol`s that need `symbol_new_from_linear_memory`.
    * `strings` / `bytes_literals` -- every `String` and `Bytes`/`BytesN`
      literal; both host forms are linear-memory-only.
    * `address_strkeys` -- every `Address` literal's strkey, as the ASCII text
      the source spelled (review B6). Kept SEPARATE from `strings` even though
      both are pooled as UTF-8: they are different authoring surfaces with
      different lowerings (an `Address` literal needs
      `strkey_to_address` after the string constructor), and folding one into
      the other would make `strings` a claim that is no longer true.
    * `struct_key_descriptor_sets` -- one entry per distinct `@contracttype`
      field-name set, ALREADY in the P7 byte-string order `MakeStruct` fixed.
      `map_new_from_linear_memory` needs the key descriptors in that order at
      compile time, and the wrong layout validates then panics on chain
      (F.1.13), so C owns the sort and D must not re-sort.
    """

    symbols_over_9: tuple[str, ...]
    strings: tuple[str, ...]
    bytes_literals: tuple[bytes, ...]
    address_strkeys: tuple[str, ...]
    struct_key_descriptor_sets: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class SpecInputs:
    """What `spec.sections.build_spec_entries` needs (SS C.2 output 4).

    `declared_types_in_order` is B9's "`types` is declared, not discovered"
    inventory -- now FOUR kinds of CLASSES (M1-E2 adds tagged unions and int
    enums to the struct and error-enum pair), in declaration order, which is
    the order `build_spec_entries(cls, types=...)` emits them in (B10). Ruling
    E7 is what then decides the EMITTED order among those four kinds --
    `declared_types_in_order` itself stays in DECLARATION order regardless.

    `events` is kept strictly SEPARATE (MJ-9): an event is not a declared TYPE
    but its own spec entry kind, so it travels to
    `sections.build_spec_entries` through the `events=` keyword and
    `_declared_type_entry` refuses one handed to `types=`. Handing an event to
    `types=` is a hard failure at emission, so the two inventories are two
    fields. (Before M1-E Task 5 the refusal was unconditional: `_serpent_type_`
    carried no topic/data split, and emitting a guess "would ship a
    valid-but-lying spec" -- B14/D8.)

    `contract_cls` is `None` only when the module declared no usable
    `@contract` class -- which always comes with a diagnostic, and therefore
    never reaches a caller of `compile_module`.
    """

    contract_cls: type[Any] | None
    declared_types_in_order: tuple[type[Any], ...]
    events: tuple[type[Any], ...]


@dataclass(frozen=True)
class CompiledModule:
    """A successfully compiled module: the IR plus every derived fact D needs.

    There is no `diagnostics` field. SS C.2's output list requires the sink to
    be empty for D to run, so a `CompiledModule` existing at all is the proof:
    anything else raised `CompileError`.

    `functions` is the flat `FuncIR` list -- the contract's methods
    (`__constructor` and exports and private methods, in source order) followed
    by the module-level helpers. It is a projection of `ir`, offered because
    every consumer wants exactly this list.

    **The invariant `returns_on_every_path` carries.** `True` does NOT mean the
    body ends in a `Return`: a loop with no `break` DIVERGES and satisfies the
    rule vacuously. A wasm validator types a `loop` as falling through with its
    result type, so the emitter must terminate a function with `unreachable`
    whenever its last reachable statement is not a `Return` -- exactly the
    discipline spec SS 4 demands of "early returns, missing returns" (S17/P6).
    `stmt.CheckedBody` is the origin of both the flag and this warning.
    """

    ir: ModuleIR
    functions: tuple[FuncIR, ...]
    host_fns_used: frozenset[str]
    host_fns_reachable: frozenset[str]
    needs_memory: bool
    literals: LiteralInventory
    runtime_parts_needed: frozenset[str]
    spec_inputs: SpecInputs
    declared_protocol: int


# --- the entry point ----------------------------------------------------------


def compile_module(source: str, path: str, *, target_protocol: int | None = None) -> CompiledModule:
    """Compile one contract module's `source` into IR plus D's inputs.

    `path` is used verbatim in every diagnostic and in `ModuleIR.path`; it is
    never opened, so a caller may pass a display path for source it read
    itself (which is what `tests/must_reject/`'s runner does).

    `target_protocol` is BL-1's threading of the build target into the
    computed floor. `None` -- the default, checked with `is None` and never for
    truthiness (B4) -- means "declare the computed floor", which is what spec
    SS 4's "declared protocol is computed, never hand-set" requires; an integer
    means "declare exactly this", and a host function gated outside it is a
    located `SPT6001` naming the function (B5/S18).

    Raises `CompileError` carrying every diagnostic, sorted by location, if
    the module does not compile (E16). Raises `CompilerBugError` -- an
    `AssertionError`, deliberately NOT catchable alongside `CompileError` --
    if the frontend's own invariants break: the metadata and AST views
    disagreeing (F.1.14), a `Ty.Invalid` node with no diagnostic behind it, or
    a host-function name the pinned bindings do not carry.

    **Compiling a module EXECUTES its module-level code** (ruling E1's hybrid
    frontend): decorators must run for `_serpent_type_` and the resolved
    annotations to exist at all. `loader.load_module` narrows the blast radius
    -- module shape is validated before anything executes -- but the execution
    is real and is documented here because a caller must know it.
    """
    loaded = load_module(source, path)
    sink = loaded.diagnostics

    validate_limits(loaded, sink)
    declarations = check_declarations(loaded, sink)
    ir = _build_module_ir(loaded, declarations, sink)

    used, reachable, host_fn_locs = _collect_host_fns(ir)
    protocol = _resolve_protocol(
        reachable,
        target_protocol,
        host_fn_locs,
        path,
        sink,
        constructor_loc=_constructor_loc(ir),
    )

    sink.raise_if_any()
    _assert_no_invalid_ir(ir)

    literals = _collect_literals(ir)
    return CompiledModule(
        ir=ir,
        functions=_flat_functions(ir),
        host_fns_used=used,
        host_fns_reachable=reachable,
        needs_memory=_needs_memory(ir, literals, used),
        literals=literals,
        runtime_parts_needed=_collect_runtime_parts(ir),
        spec_inputs=SpecInputs(
            contract_cls=loaded.contract_cls,
            declared_types_in_order=declarations.spec_types,
            events=tuple(event.cls for event in loaded.events),
        ),
        declared_protocol=protocol,
    )


# --- building the IR ----------------------------------------------------------


def _build_module_ir(
    loaded: LoadedModule, declarations: Declarations, sink: Diagnostics
) -> ModuleIR:
    """Check every function body and assemble the `ModuleIR`."""
    reserved = _reserved_names(loaded)
    contract_methods: list[FuncIR] = []
    helpers: list[FuncIR] = []
    for sig in declarations.signatures:
        func_ir = _compile_function(sig, loaded, declarations, reserved, sink)
        # `has_self` is the DECLARATION's own answer to "is this a method of
        # the @contract class" (`decls.FuncSig`), which is why it -- and not a
        # name lookup -- decides where the `FuncIR` belongs.
        (contract_methods if sig.has_self else helpers).append(func_ir)

    contract: ContractIR | None = None
    if loaded.contract_decl is not None and loaded.contract_node is not None:
        contract = ContractIR(
            loc=Loc.from_node(loaded.path, loaded.contract_node),
            name=loaded.contract_decl.name,
            doc=_class_doc(loaded.contract_decl.cls),
            methods=tuple(contract_methods),
        )

    return ModuleIR(
        loc=Loc.whole_file(loaded.path),
        path=loaded.path,
        doc=ast.get_docstring(loaded.tree) or "",
        imports=_imported_names(loaded.tree),
        consts=declarations.consts,
        structs=declarations.structs,
        error_enums=declarations.error_enums,
        events=declarations.events,
        contract=contract,
        helpers=tuple(helpers),
    )


def _compile_function(
    sig: FuncSig,
    loaded: LoadedModule,
    declarations: Declarations,
    module_reserved: dict[str, str],
    sink: Diagnostics,
) -> FuncIR:
    """One `FuncSig` + its AST body -> one `FuncIR`."""
    reserved = dict(module_reserved)
    for name, _ty, _loc in sig.params:
        reserved[name] = "a parameter"
    ctx = FuncCtx(
        loaded=loaded,
        sink=sink,
        params=list(sig.params),
        locals=SlotTable(reserved=reserved),
        loop_depth=0,
        return_ty=sig.ret,
        alias_sets=AliasTable(),
        fn_name=sig.py_name,
        path=loaded.path,
        internal_sigs=declarations.internal_sigs,
        # Seeded from the declaration, never from the name (`FuncCtx`'s own
        # docstring, fix round 1's I-1).
        fn_kind=sig.kind,
        has_self=sig.has_self,
    )
    checked = check_function_body(sig.node.body, ctx, loc=sig.loc)
    return FuncIR(
        loc=sig.loc,
        py_name=sig.py_name,
        export_name=sig.export_name,
        kind=sig.kind,
        params=sig.params,
        ret=sig.ret,
        doc=sig.doc,
        locals=tuple((slot.slot, slot.name, slot.ty) for slot in ctx.locals.slots),
        body=checked.stmts,
        returns_on_every_path=checked.returns_on_every_path,
    )


def _reserved_names(loaded: LoadedModule) -> dict[str, str]:
    """The module-level names a NEW local may not shadow (SS C.3 rule 4), each
    mapped to the human-readable kind a shadowing diagnostic names."""
    reserved: dict[str, str] = {}
    for name in _imported_names(loaded.tree):
        reserved[name] = "an imported name"
    for const in loaded.module_consts:
        reserved[const.name] = "a module constant"
    for helper in loaded.helpers:
        reserved[helper.name] = "a module-level helper"
    for decl in (*loaded.decorated_types_in_order, *loaded.events):
        reserved[decl.name] = "a declared type"
    if loaded.contract_decl is not None:
        reserved[loaded.contract_decl.name] = "the contract class"
    return reserved


def _imported_names(tree: ast.Module) -> tuple[str, ...]:
    """The `serpent.__all__` names the module imported, in source order (A22).

    `from __future__ import annotations` is deliberately excluded: it is a
    compiler directive, not a name the contract can use.
    """
    names: list[str] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "serpent":
            for alias in stmt.names:
                names.append(alias.asname or alias.name)
    return tuple(names)


def _flat_functions(ir: ModuleIR) -> tuple[FuncIR, ...]:
    methods = ir.contract.methods if ir.contract is not None else ()
    return (*methods, *ir.helpers)


def _constructor_loc(ir: ModuleIR) -> Loc | None:
    """The `__init__` definition's `Loc`, or `None` if the module has none.

    The ASSEMBLY is asked, not the AST or the loader: `FuncKind.CONSTRUCTOR` is
    the decision `decls.py` already recorded (there is exactly one, and only a
    `@contract` method can carry it), so nothing here re-derives "is this a
    constructor" from a name. The `Loc` rather than a bare `bool` is returned
    because the diagnostic the feature gate can raise must point AT the
    `__init__` that needs the higher protocol (P2).
    """
    methods = ir.contract.methods if ir.contract is not None else ()
    return next((f.loc for f in methods if f.kind is FuncKind.CONSTRUCTOR), None)


# --- the host-function sets (SS C.2 output 1) ---------------------------------


def guarded_storage_get(node: IRNode) -> HostCall | None:
    """The `get_contract_data` an `IfExp`'s own `has` already proved present.

    Returns that `HostCall` when `node` is EXACTLY the shape
    `recognize.py`'s `get(key, T, default=d)` builds -- and `None` otherwise,
    which is the answer that means "this `get` still needs the E13 guard".

    **The test is node IDENTITY, not structural equality** (review M12). The
    frontend SHARES one key expression between the `has` and the `get`
    (`recognize.py`'s `_storage_get`: `key` and `imm` are passed to both
    `HostCall`s), so `cond.args[0] is then.args[0]` holds for the recognized
    shape and for nothing else. A hand-written
    `a.get(k, T) if a.has(k2) else d` parses to two DISTINCT key subtrees --
    even when they are spelled identically and therefore compare `==`, since
    every IR node is a frozen dataclass with structural equality -- so it fails
    this test and its `get` is guarded. Keying on the condition alone would
    silently suppress the guard on exactly that program, and keying on `==`
    would suppress it whenever the two spellings happened to match while the
    two evaluations did not have to.

    Public, and the ONE place the shape is recognized: `emitter.lower` calls it
    to choose the arm-lowering, and `_collect_host_fns` calls it so
    `host_fns_used` accounts for the guard D will emit (ruling E13, D13's
    licence). Two copies of this predicate could disagree, and the direction
    that disagreement breaks is a missing import for a function the emitted
    body calls.
    """
    if not isinstance(node, IfExp):
        return None
    cond, then = node.cond, node.then
    if not (isinstance(cond, HostCall) and cond.fn_name == _STORAGE_HAS_FN):
        return None
    if not (isinstance(then, HostCall) and then.fn_name == _STORAGE_GET_FN):
        return None
    if len(cond.args) != 2 or len(then.args) != 2:  # pragma: no cover - the pin fixes arity
        return None
    if cond.args[0] is not then.args[0]:
        return None
    # The storage BUCKET has to match too: `has` in the instance bucket proves
    # nothing about the persistent one, and the two immediates are the same
    # shared `RawScalar` node in the recognized shape.
    has_imm, get_imm = cond.args[1], then.args[1]
    if not (isinstance(has_imm, RawScalar) and isinstance(get_imm, RawScalar)):
        return None
    if has_imm.kind is not RawScalarKind.STORAGE_TYPE:
        return None
    if get_imm.kind is not RawScalarKind.STORAGE_TYPE:
        return None
    if has_imm.value != get_imm.value:
        return None
    return then


def _collect_host_fns(
    ir: ModuleIR,
) -> tuple[frozenset[str], frozenset[str], dict[str, Loc]]:
    """`(definitely used, reachable, name -> first Loc)` over the whole IR.

    The `Loc` map is what lets a protocol-gate diagnostic point at the source
    that reached the offending function instead of at the whole file (P2).
    """
    used: set[str] = set()
    reachable: set[str] = set()
    locs: dict[str, Loc] = {}

    def note(names: tuple[str, ...], loc: Loc, *, certain: bool) -> None:
        for name in names:
            reachable.add(name)
            if certain:
                used.add(name)
            locs.setdefault(name, loc)

    # Ruling E13: a `get_contract_data` that no `has` already covered is
    # wrapped by D in a guard that calls `has_contract_data` and, on a miss,
    # `fail_with_error` (`CODE_MISSING_VALUE`, E14). Those two are CERTAIN
    # uses -- the guard is not a form D chooses between -- so they belong in
    # `host_fns_used`, and omitting them would under-report both the import
    # set and the protocol floor. The exemption is the `get` whose own `IfExp`
    # already proved presence; `guarded_storage_get` is the single place that
    # shape is recognized, shared with `emitter.lower` so the two cannot drift.
    # Identity is stable here because `ir` holds every node alive for the walk.
    gets_covered_by_a_has = {
        id(get) for get in map(guarded_storage_get, walk(ir)) if get is not None
    }

    for node in walk(ir):
        if isinstance(node, HostCall):
            note((node.fn_name,), node.loc, certain=True)
            if node.fn_name == _STORAGE_GET_FN and id(node) not in gets_covered_by_a_has:
                note((_STORAGE_HAS_FN, _STORAGE_MISSING_FN), node.loc, certain=True)
        elif isinstance(node, Raise):
            note((_RAISE_HOST_FN,), node.loc, certain=True)
        elif isinstance(node, Compare) and node.via_obj_cmp:
            note((_OBJ_CMP_HOST_FN,), node.loc, certain=True)
        elif isinstance(node, MakeStruct):
            note((_STRUCT_NEW_FN,), node.loc, certain=True)
        elif isinstance(node, FieldGet):
            note((_FIELD_GET_FN,), node.loc, certain=True)
            # The key `Symbol` is an immediate at 9 characters or fewer (S22),
            # so whether the linear-memory constructor is reached is decided
            # HERE, not by D.
            if not val.fits_symbol_small(node.field):
                note((_FIELD_SYMBOL_FN,), node.loc, certain=True)
        elif isinstance(node, (MakeVec, MakeTopics, MakeUnion)):
            # M1-E2 SS B.1: a union value IS a `Vec` on chain, built by the
            # very same trio -- and REACHABLE, not certain, because which of
            # the two forms runs is D's choice.
            note(_VEC_BUILD_FNS, node.loc, certain=False)
        elif isinstance(node, MakeMap):
            note(_MAP_BUILD_FNS, node.loc, certain=False)
        elif isinstance(node, Const):
            note(_literal_host_fns(node), node.loc, certain=True)

    return frozenset(used), frozenset(reachable), locs


def _literal_host_fns(node: Const) -> tuple[str, ...]:
    """The host functions `node`'s literal form needs; empty for an immediate.

    A tuple rather than one optional name because of review B6's `Address`
    case: its literal costs TWO calls (the string constructor, then
    `strkey_to_address`), and both are certain.
    """
    if node.ty.tag is TyTag.SYMBOL and isinstance(node.py_value, str):
        return () if val.fits_symbol_small(node.py_value) else (_SYMBOL_LM_FN,)
    if node.ty.tag is TyTag.STRING and isinstance(node.py_value, str):
        return (_STRING_LM_FN,)
    if node.ty.tag in (TyTag.BYTES, TyTag.BYTES_N) and isinstance(node.py_value, bytes):
        return (_BYTES_LM_FN,)
    if node.ty.tag is TyTag.ADDRESS and isinstance(node.py_value, str):
        return (_STRING_LM_FN, _ADDRESS_FROM_STRKEY_FN)
    return ()


# --- the protocol floor (B4/B5/S18, BL-1) -------------------------------------

_GATE_HELP = (
    "raise the build's target protocol, or use a surface available at it -- the declared "
    "protocol is computed from the host functions and gated features the contract reaches, "
    "never hand-set"
)

#: The one FEATURE gate's help: there is no lower-protocol spelling of a
#: constructor to suggest, so the second arm is "drop it", not "use another
#: surface".
_CONSTRUCTOR_GATE_HELP = (
    f"raise the build's target protocol to {CONSTRUCTOR_MIN_PROTOCOL} or higher, or remove the "
    "contract's `__init__` and initialize from an ordinary method -- the declared protocol is "
    "computed from the host functions and gated features the contract reaches, never hand-set"
)


def _resolve_protocol(
    reachable: frozenset[str],
    target_protocol: int | None,
    locs: dict[str, Loc],
    path: str,
    sink: Diagnostics,
    *,
    constructor_loc: Loc | None,
) -> int:
    """`declared_protocol(reachable, target_protocol)` plus the feature gates, or `SPT6001`.

    `_host.declared_protocol` is THE value D writes into `build_env_meta`
    (B4), so it is called rather than reimplemented -- including its `is None`
    check and its refusal to silently raise an explicit target to the floor.

    Two failure shapes both land on `SPT6001`, because both are "a function is
    gated outside this build's target": a `ProtocolGateError` (some function's
    min/max protocol excludes the target) and the `ValueError`
    `declared_protocol` raises when an explicit target is below the computed
    floor. The first is re-run PER NAME so each offender gets its own located
    diagnostic -- reusing `check_protocol_target` rather than re-deriving which
    names offend -- and the aggregate message (B5's documented shape, naming
    every offender with its min/max) rides along on whichever one is reported.

    **This is THE feature-gate seam.** IMPORT gates -- a host function whose
    binding carries a `min_protocol`/`max_protocol` -- come from
    `declared_protocol` and are the only kind `_host` can compute, because it
    only ever sees names from `HOST_FUNCTIONS`. A FEATURE gate is a capability
    the module uses that is NOT a host-function import, so no binding carries
    it and only the frontend knows whether the module used it; such gates are
    contributed HERE, by `_feature_gate_floor`, and folded into the import
    answer. `__constructor` (spec SS 13, protocol >= 22, CAP-0058) is the first
    and currently the only one -- a future gate joins that function rather than
    re-deriving this composition.

    `_feature_gate_floor` runs FIRST and UNCONDITIONALLY, before the import
    check that may fail. That order is the whole point: a contract's `__init__`
    does not stop needing protocol 22 because some import is ALSO gated outside
    the target, so E16's collect-all requires both diagnostics from one pass.
    Running the feature check on the import check's success path instead
    silently drops it whenever the import side fails first -- which is a real
    shape (a gated import at target 21, or any target below `BASE_PROTOCOL`),
    not a hypothetical.
    """
    names = sorted(reachable)
    feature_floor = _feature_gate_floor(target_protocol, sink, constructor_loc=constructor_loc)
    try:
        resolved = declared_protocol(names, target_protocol)
    except KeyError as exc:
        # `declared_protocol` looks every name up in the pinned bindings (B2)
        # and raises `KeyError` naming the one it could not find. That means C
        # emitted a name the pin does not carry -- its bug, not the author's,
        # so it must not arrive as a user diagnostic. Catching the lookup here
        # rather than pre-checking against a second copy of the table keeps one
        # source of truth for "is this a real host function".
        raise CompilerBugError(
            f"the frontend emitted a host function name the pinned bindings do not carry: "
            f"{exc} -- bindings are looked up BY NAME (B2), so this is a compiler bug, "
            "not a contract error"
        ) from exc
    except ProtocolGateError as exc:
        _report_gate_offenders(names, target_protocol, locs, path, exc, sink)
    except ValueError as exc:
        # An explicit target below the computed floor: no single function is
        # incompatible with it, the FLOOR is, so the fact is module-scoped.
        sink.error(
            "SPT6001",
            Loc.whole_file(path),
            f"{_INTENT['SPT6001']}: {exc}",
            help=_GATE_HELP,
        )
    else:
        # "Users may raise, never lower" (B4/S6): an explicit target is
        # returned verbatim -- one below a feature gate was already reported
        # above, and `declared_protocol` already refused one below the import
        # floor. With no target, the COMPUTED floor is the max over both kinds
        # of gate, which is what keeps S18 honest.
        return resolved if target_protocol is not None else max(resolved, feature_floor)
    # A gated compile never reaches sub-plan D (the sink is non-empty, so
    # `compile_module` raises), and returning the floor-ignoring target keeps
    # this function total rather than inventing a protocol.
    return target_protocol if target_protocol is not None else DEFAULT_TARGET_PROTOCOL


def _feature_gate_floor(
    target_protocol: int | None,
    sink: Diagnostics,
    *,
    constructor_loc: Loc | None,
) -> int:
    """The floor the FEATURE gates impose, reporting any explicit target below one.

    One gate today, `__constructor` (spec SS 13's reserved-name row, dossier
    S26, CAP-0058): the host only honors the reserved export from protocol
    `CONSTRUCTOR_MIN_PROTOCOL`, so bytes declaring less than that with a
    `__constructor` in them would deploy on an older network and simply never
    run the constructor.

    Both halves of a gate live here, so there is ONE place a future gate is
    added and one place it can be reported from:

    * the CONTRIBUTION -- the returned floor, which the caller folds into the
      import answer with a `max` when no explicit target was given, since
      S18's "computed, never hand-set" only stays honest if the computation
      sees every gated capability the module uses, not only its imports;
    * the REJECTION -- an explicit target below the gate is a located
      `SPT6001` at the `__init__` that needs the higher protocol, exactly as a
      gated host function is reported at the call that reached it. An explicit
      target at or above the gate is no diagnostic at all: it already clears
      it.

    `BASE_PROTOCOL` is the identity when no gate applies -- never 0 -- because
    the caller `max`es this against a floor that is itself never below
    `BASE_PROTOCOL`, so a 0 would be a silently-harmless wrong answer that
    stopped being harmless the moment anything else consumed it.

    This is called exactly once per compile, unconditionally, which is also
    what makes the rejection un-duplicatable: there is no second call site to
    report the same gate twice.
    """
    if constructor_loc is None:
        return BASE_PROTOCOL
    if target_protocol is not None and target_protocol < CONSTRUCTOR_MIN_PROTOCOL:
        sink.error(
            "SPT6001",
            constructor_loc,
            f"{_INTENT['SPT6001']}: a contract with a constructor requires protocol "
            f">= {CONSTRUCTOR_MIN_PROTOCOL} (CAP-0058), but the build's target protocol is "
            f"{target_protocol} -- `__init__` compiles to the reserved `__constructor` export, "
            f"which the host does not honor below {CONSTRUCTOR_MIN_PROTOCOL}",
            help=_CONSTRUCTOR_GATE_HELP,
        )
    return CONSTRUCTOR_MIN_PROTOCOL


def _report_gate_offenders(
    names: list[str],
    target_protocol: int | None,
    locs: dict[str, Loc],
    path: str,
    aggregate: ProtocolGateError,
    sink: Diagnostics,
) -> None:
    effective = target_protocol if target_protocol is not None else DEFAULT_TARGET_PROTOCOL
    reported = False
    for name in names:
        try:
            check_protocol_target([name], effective)
        except ProtocolGateError as one:
            sink.error(
                "SPT6001",
                locs.get(name, Loc.whole_file(path)),
                f"{_INTENT['SPT6001']}: {one}",
                help=_GATE_HELP,
                notes=(str(aggregate),) if len(names) > 1 else (),
            )
            reported = True
    if not reported:  # pragma: no cover -- the aggregate offended, so one must
        sink.error(
            "SPT6001",
            Loc.whole_file(path),
            f"{_INTENT['SPT6001']}: {aggregate}",
            help=_GATE_HELP,
        )


# --- the literal inventory and memory decision (SS C.2 output 2, S19) --------


def _collect_literals(ir: ModuleIR) -> LiteralInventory:
    symbols: set[str] = set()
    strings: set[str] = set()
    byte_literals: set[bytes] = set()
    strkeys: set[str] = set()
    key_sets: set[tuple[str, ...]] = set()
    for node in walk(ir):
        if isinstance(node, Const):
            if node.ty.tag is TyTag.SYMBOL and isinstance(node.py_value, str):
                if not val.fits_symbol_small(node.py_value):
                    symbols.add(node.py_value)
            elif node.ty.tag is TyTag.STRING and isinstance(node.py_value, str):
                strings.add(node.py_value)
            elif node.ty.tag in (TyTag.BYTES, TyTag.BYTES_N) and isinstance(node.py_value, bytes):
                byte_literals.add(node.py_value)
            elif node.ty.tag is TyTag.ADDRESS and isinstance(node.py_value, str):
                # Review B6: the strkey text, pooled as ASCII, is what
                # `string_new_from_linear_memory` reads before
                # `strkey_to_address` turns it into an `AddressObject`.
                strkeys.add(node.py_value)
        elif isinstance(node, MakeStruct):
            # Already in P7's byte-string order -- recorded, never re-sorted.
            key_sets.add(tuple(name for name, _value in node.fields))
    return LiteralInventory(
        symbols_over_9=tuple(sorted(symbols)),
        strings=tuple(sorted(strings)),
        bytes_literals=tuple(sorted(byte_literals)),
        address_strkeys=tuple(sorted(strkeys)),
        struct_key_descriptor_sets=tuple(sorted(key_sets)),
    )


def _needs_memory(ir: ModuleIR, literals: LiteralInventory, used: frozenset[str]) -> bool:
    """Whether the module needs linear memory (S19).

    Spec SS 5's list, node by node: a `Symbol` past 9 characters, a `String` or
    `Bytes` literal, a struct (whose `map_new_from_linear_memory` form is not
    optional), and a bulk construction D can lay out in the data section. Any
    host function already in the definite set that reads linear memory
    (`_LINEAR_MEMORY_HOST_FNS`) settles it too.

    **A bulk construction counts when C can see that D has the choice**, which
    is not the same test for every node:

    * `MakeVec`/`MakeMap`/`MakeUnion` carry `all_static`, so that flag answers
      it directly. A construction with a non-static item falls back to the
      build-up chain, which needs no memory at all.
    * `MakeTopics` has NO `all_static` flag -- topics are a heterogeneous tuple
      by design (D8), so the node never computed one -- and the equivalent test
      has to be made here: every topic being a `Const` is exactly the condition
      under which `vec_new_from_linear_memory` (already in
      `host_fns_reachable` for this node) is available to D. Fix round 1's I-1:
      omitting this reported `needs_memory=False` for a contract whose only
      memory-eligible construction was a static topic tuple, while
      `host_fns_reachable` simultaneously named the linear-memory constructor.
      The conservative answer is the correct one -- a memory export that D
      turns out not to use costs bytes; a missing one fails validation.

    A contract needing none of that compiles memoryless, which spec SS 5 keeps
    supported.
    """
    if literals.symbols_over_9 or literals.strings or literals.bytes_literals:
        return True
    if literals.address_strkeys:
        # Review B6: `Address` has no small Val form, so a literal one is
        # ALWAYS a pooled strkey plus `string_new_from_linear_memory`. The
        # `used` test below would settle it too; this row is here so the
        # inventory alone answers the question, as it does for every other
        # pooled literal.
        return True
    if literals.struct_key_descriptor_sets:
        return True
    if used & _LINEAR_MEMORY_HOST_FNS:
        return True
    return any(_bulk_construction_can_use_memory(node) for node in walk(ir))


def _bulk_construction_can_use_memory(node: object) -> bool:
    """Whether `node` is a bulk construction D could lay out in linear memory."""
    if isinstance(node, (MakeVec, MakeMap, MakeUnion)):
        # `MakeUnion` HAS an `all_static` flag of its own (unlike `MakeTopics`
        # below), so it belongs on this arm: recomputing the every-item-a-
        # `Const` test for it would be a second, divergable copy of a fact the
        # node already carries.
        return node.all_static
    if isinstance(node, MakeTopics):
        return all(isinstance(topic, Const) for topic in node.topics)
    return False


# --- the guest-runtime parts (SS C.2 output 3, spec SS 6) --------------------


def _collect_runtime_parts(ir: ModuleIR) -> frozenset[str]:
    """Which guest-runtime pieces D must link -- **a hint, not a manifest**.

    Ratified by sub-plan D (ruling E3) under the licence C2 wrote into this
    function's previous docstring. What the names mean now:

    * `{u,i}64_<op>` / `{u,i}128_<op>` for `op` in add/sub/mul/floordiv/mod --
      one part per (width, operator), each taking and returning RAW unboxed
      words and routing its own out-of-range result to `fail_with_error` with
      the `ArithmeticOverflow` code (A4/S20).
    * `{u,i}128_neg` and `{u,i}128_cmp` -- 128-bit negation and 128-bit direct
      comparison are limb code, so they too are calls.

    What is deliberately NOT here, and why:

    * **`overflow_check` is gone.** There was never one shared helper: the
      out-of-range branch is three instructions specialized to the width, the
      operator, and the sign, and it lives inside whichever part (or inline
      sequence) computes the result.
    * **32-bit `Binary` names nothing.** U32/I32 checked ops lower to an
      inline shift/op/range-compare -- below S25's call-overhead break-even.
    * **`NEG` at 32 and 64 bits names nothing.** Review M6: on an unsigned
      type it is "nonzero is overflow, else 0"; on a signed type it is "MIN is
      overflow, else 0 - value". Both are inline.
    * **`Compare(via_obj_cmp=True)` names nothing** -- that is a host call, not
      a guest-runtime part -- and a comparison's part is chosen from its
      OPERAND type (`lhs.ty`), never from its own `ty`, which is always Bool.

    **A hint, not a manifest.** D links parts from its own lowering, not from
    this set; what is pinned between the two is only that D links at least
    everything named here (`runtime_parts_needed <= runtime_parts_linked`).
    A part D reaches on its own -- `box_u64`, `unbox_i64`, `tagcheck_bytes_n`
    -- is a lowering detail C cannot see and must not try to predict.
    """
    parts: set[str] = set()
    for node in walk(ir):
        if isinstance(node, BinaryNode):
            prefix = _ARITH_PREFIX.get(node.ty.tag)
            if prefix is not None:
                parts.add(f"{prefix}_{node.op.name.lower()}")
        elif isinstance(node, Unary) and node.op is UnaryOp.NEG:
            prefix = _WIDE_ARITH_PREFIX.get(node.ty.tag)
            if prefix is not None:
                parts.add(f"{prefix}_neg")
        elif isinstance(node, Compare) and not node.via_obj_cmp:
            prefix = _WIDE_ARITH_PREFIX.get(node.lhs.ty.tag)
            if prefix is not None:
                parts.add(f"{prefix}_cmp")
    return frozenset(parts)


# --- the sink invariant -------------------------------------------------------


def _invalid_ty_fields(node: IRNode) -> list[str]:
    """Every field of `node` holding a `Ty` that is (or contains) `Ty.Invalid`.

    Reflection over `dataclasses.fields` rather than a per-node-kind check, for
    the same reason `ir.walk` uses it: a node kind or a field added later is
    covered automatically instead of being silently skipped. Nested type
    parameters are followed too, so `Vec[<invalid>]` is caught as well as a
    bare `Ty.Invalid` -- an `elem`/`key`/`value` that failed to resolve is the
    same broken promise one level down.
    """
    return [f.name for f in dataclasses.fields(node) if _contains_invalid(getattr(node, f.name))]


def _contains_invalid(value: object) -> bool:
    if isinstance(value, Ty):
        if value.tag is TyTag.INVALID:
            return True
        return any(_contains_invalid(getattr(value, f.name)) for f in dataclasses.fields(value))
    if isinstance(value, (tuple, list)):
        return any(_contains_invalid(item) for item in value)
    return False


def _assert_no_invalid_ir(ir: ModuleIR) -> None:
    """No `Ty.Invalid` anywhere in the output IR, in ANY field.

    `Ty.Invalid` is the checkers' "already reported, keep walking" placeholder
    (minor 13), so an `Invalid` reaching a caller with an EMPTY sink means the
    frontend silently dropped something and handed sub-plan D a node with no
    type. That is a compiler bug, not a contract error, which is why this
    raises `CompilerBugError` (an `AssertionError`) and not `CompileError`. It
    is called only after `raise_if_any()`, so the sink is provably empty at
    that point and "no diagnostic behind it" needs no separate check.

    The scan is over every `Ty`-valued FIELD of every node, not just
    `IRExpr.ty` (fix round 1's M-4). The non-expression positions are real and
    would otherwise have been missed: `FuncIR.params`/`ret`/`locals`,
    `StructDecl`/`EventDecl.fields`, `ConstDecl.ty`, `MakeVec.elem_ty`,
    `MakeMap.key_ty`/`value_ty` and `Convert.from_ty`/`to_ty` all carry a `Ty`
    that no `IRExpr.ty` check would look at -- and an unresolved parameter type
    reaching the emitter is exactly as broken as an unresolved expression.
    """
    offenders = [(node, fields) for node in walk(ir) if (fields := _invalid_ty_fields(node))]
    if offenders:
        rendered = ", ".join(
            f"{type(node).__name__}.{'/'.join(fields)} at {node.loc.sort_key()}"
            for node, fields in offenders
        )
        raise CompilerBugError(
            f"{len(offenders)} node(s) reached the output IR carrying Ty.Invalid with an "
            f"empty diagnostics sink: {rendered} -- Ty.Invalid is the reported-failure "
            "placeholder, so an Invalid with nothing reported is a compiler bug"
        )
