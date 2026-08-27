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
    protocol floor         declared_protocol over the reachable host-fn set

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
    HostCall,
    IRNode,
    MakeMap,
    MakeStruct,
    MakeTopics,
    MakeVec,
    ModuleIR,
    Raise,
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

#: The guest-runtime part every checked arithmetic operation needs (A4/S20):
#: an out-of-range result must reach `fail_with_error` with the
#: `ArithmeticOverflow` code, never wrap.
_OVERFLOW_PART = "overflow_check"

#: `TyTag` -> the guest-runtime prefix for 128-bit arithmetic (spec SS 6): the
#: only widths with no native wasm instruction.
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
    * `struct_key_descriptor_sets` -- one entry per distinct `@contracttype`
      field-name set, ALREADY in the P7 byte-string order `MakeStruct` fixed.
      `map_new_from_linear_memory` needs the key descriptors in that order at
      compile time, and the wrong layout validates then panics on chain
      (F.1.13), so C owns the sort and D must not re-sort.
    """

    symbols_over_9: tuple[str, ...]
    strings: tuple[str, ...]
    bytes_literals: tuple[bytes, ...]
    struct_key_descriptor_sets: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class SpecInputs:
    """What `spec.sections.build_spec_entries` needs (SS C.2 output 4).

    `declared_types_in_order` is B9's "`types` is declared, not discovered"
    inventory -- the struct and error-enum CLASSES in declaration order, which
    is the order `build_spec_entries(cls, types=...)` emits them in (B10).

    `events` is kept strictly SEPARATE (MJ-9): `sections._declared_type_entry`
    refuses an event class unconditionally, because `SCSpecEventV0` needs a
    topic/data split `_serpent_type_` does not carry and emitting a guess
    "would ship a valid-but-lying spec" (B14/D8). Handing an event to `types=`
    is a hard failure at emission, so the two inventories are two fields.

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
    protocol = _resolve_protocol(reachable, target_protocol, host_fn_locs, path, sink)

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


# --- the host-function sets (SS C.2 output 1) ---------------------------------


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

    for node in walk(ir):
        if isinstance(node, HostCall):
            note((node.fn_name,), node.loc, certain=True)
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
        elif isinstance(node, (MakeVec, MakeTopics)):
            note(_VEC_BUILD_FNS, node.loc, certain=False)
        elif isinstance(node, MakeMap):
            note(_MAP_BUILD_FNS, node.loc, certain=False)
        elif isinstance(node, Const):
            literal_fn = _literal_host_fn(node)
            if literal_fn is not None:
                note((literal_fn,), node.loc, certain=True)

    return frozenset(used), frozenset(reachable), locs


def _literal_host_fn(node: Const) -> str | None:
    """The linear-memory constructor `node` needs, or `None` for an immediate."""
    if node.ty.tag is TyTag.SYMBOL and isinstance(node.py_value, str):
        return None if val.fits_symbol_small(node.py_value) else _SYMBOL_LM_FN
    if node.ty.tag is TyTag.STRING and isinstance(node.py_value, str):
        return _STRING_LM_FN
    if node.ty.tag in (TyTag.BYTES, TyTag.BYTES_N) and isinstance(node.py_value, bytes):
        return _BYTES_LM_FN
    return None


# --- the protocol floor (B4/B5/S18, BL-1) -------------------------------------

_GATE_HELP = (
    "raise the build's target protocol, or use a surface available at it -- the declared "
    "protocol is computed from the host functions the contract reaches, never hand-set"
)


def _resolve_protocol(
    reachable: frozenset[str],
    target_protocol: int | None,
    locs: dict[str, Loc],
    path: str,
    sink: Diagnostics,
) -> int:
    """`declared_protocol(reachable, target_protocol)`, or `SPT6001`.

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
    """
    names = sorted(reachable)
    try:
        return declared_protocol(names, target_protocol)
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
    # A gated compile never reaches sub-plan D (the sink is non-empty, so
    # `compile_module` raises), and returning the floor-ignoring target keeps
    # this function total rather than inventing a protocol.
    return target_protocol if target_protocol is not None else DEFAULT_TARGET_PROTOCOL


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
        elif isinstance(node, MakeStruct):
            # Already in P7's byte-string order -- recorded, never re-sorted.
            key_sets.add(tuple(name for name, _value in node.fields))
    return LiteralInventory(
        symbols_over_9=tuple(sorted(symbols)),
        strings=tuple(sorted(strings)),
        bytes_literals=tuple(sorted(byte_literals)),
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
    is not the same test for all three nodes:

    * `MakeVec`/`MakeMap` carry `all_static`, so that flag answers it directly.
      A construction with a non-static item falls back to the build-up chain,
      which needs no memory at all.
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
    if literals.struct_key_descriptor_sets:
        return True
    if used & _LINEAR_MEMORY_HOST_FNS:
        return True
    return any(_bulk_construction_can_use_memory(node) for node in walk(ir))


def _bulk_construction_can_use_memory(node: object) -> bool:
    """Whether `node` is a bulk construction D could lay out in linear memory."""
    if isinstance(node, (MakeVec, MakeMap)):
        return node.all_static
    if isinstance(node, MakeTopics):
        return all(isinstance(topic, Const) for topic in node.topics)
    return False


# --- the guest-runtime parts (SS C.2 output 3, spec SS 6) --------------------


def _collect_runtime_parts(ir: ModuleIR) -> frozenset[str]:
    """Which guest-runtime pieces D must link.

    Two families, and nothing invented beyond them:

    * `overflow_check` -- every checked arithmetic operation needs the
      out-of-range branch that routes to `fail_with_error` with the
      `ArithmeticOverflow` code (A4/S20). `not` is excluded: it is a Bool
      operation with nothing to overflow.
    * `u128_<op>` / `i128_<op>` -- 128-bit arithmetic has no native wasm
      instruction, so each operator it uses is a distinct guest-runtime
      routine (spec SS 6). The names follow the dossier's own examples
      (`i128_add`, `i128_mul`).

    These part names are C-coined and await sub-plan D's ratification: D may
    rename them when it authors the guest-runtime routines, updating this
    function and its pinning test together. They are not frozen API.
    """
    parts: set[str] = set()
    for node in walk(ir):
        if isinstance(node, BinaryNode):
            parts.add(_OVERFLOW_PART)
            prefix = _WIDE_ARITH_PREFIX.get(node.ty.tag)
            if prefix is not None:
                parts.add(f"{prefix}_{node.op.name.lower()}")
        elif isinstance(node, Unary) and node.op is UnaryOp.NEG:
            parts.add(_OVERFLOW_PART)
            prefix = _WIDE_ARITH_PREFIX.get(node.ty.tag)
            if prefix is not None:
                parts.add(f"{prefix}_neg")
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
