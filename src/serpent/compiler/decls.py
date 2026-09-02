"""Declaration checking, internal-call signatures, and the call graph (Task 8).

Where `expr.py`/`stmt.py` check what happens INSIDE a function, this module
checks everything a contract module declares AROUND them, and turns each
declaration into its `ir.py` node:

* **structs, error enums and events** -- resolved from the loader's two views
  (`_serpent_type_` metadata + the AST) into `StructDecl`/`ErrorEnumDecl`/
  `EventDecl`. Events are tracked SEPARATELY from the spec-type inventory
  (MJ-9): an event is not a declared TYPE, it is its own spec entry kind, so
  `spec.sections.build_spec_entries` takes the module's events through its own
  `events=` keyword and refuses an event handed to `types=`. (Until M1-E Task 5
  the refusal was unconditional -- `_serpent_type_` carried no topic/data split
  at all, B14/D8 -- and events reached the spec not at all.)
* **module constants** (P5) -- `ADMIN = Symbol("ADMIN")` becomes a
  `ConstDecl` whose value is checked by the ordinary expression checker and
  then required to be STATIC (see `is_static_const_value`).
* **function signatures** -- every export, `__init__`, module-level helper and
  private method becomes a `FuncSig`: params resolved to `Ty` with `self` and
  a leading `env: Env` dropped (SS C.3), return type resolved, docstring taken
  from `spec.sections`' own reader so the IR and the spec can never disagree.
* **internal calls** (E8) -- the helper/private-method signatures also become
  the `InternalSig` table a call site consumes (`FuncCtx.internal_sigs`), and
  the CALL GRAPH over those targets is checked for cycles: recursion and
  mutual recursion are rejects (`SPT7005`) naming the cycle.

## Two views, one truth (MJ-5)

Nothing here re-derives what another module already owns:
`decorators._annotations_of` resolves annotations (so a PEP 563 module and its
non-PEP-563 twin behave identically, E4), `types_.resolve_annotation` maps an
annotation to a `Ty` (so B7's unmappable set is decided in exactly one place),
`spec.sections._own_doc`/`_class_doc`/`CONSTRUCTOR_NAME` supply the doc text
and the reserved constructor name, and `loader.py` has already cross-checked
the metadata and AST inventories against each other (F.1.14).

## Which shapes THIS module is the only checker of

`decorators.contract` validates the shape of every EXPORT (`self` first, all
parameters and the return annotated, no defaults, no `*args`/`**kwargs`) and
the loader re-reports each failure located. It deliberately does not look at
module-level helpers or at private, underscore-prefixed methods at all -- so
those shapes are checked here, against the same registry codes the decorator
errors bridge to, with the diagnostic located at the parameter itself.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import NoneType
from typing import Any

from serpent.compiler import codes
from serpent.compiler.ctx import AliasTable, FuncCtx, InternalSig, SlotTable
from serpent.compiler.diagnostics import Diagnostics, Loc, LocKind
from serpent.compiler.expr import RECOGNIZED_BUILTINS, check_expr
from serpent.compiler.ir import (
    Const,
    ConstDecl,
    ErrorEnumDecl,
    EventDecl,
    FuncKind,
    IRExpr,
    MakeMap,
    MakeStruct,
    MakeVec,
    StructDecl,
)
from serpent.compiler.loader import DecoratedDecl, LoadedModule
from serpent.compiler.types_ import Ty, TyTag, resolve_annotation
from serpent.decorators import _annotations_of
from serpent.env import Env
from serpent.spec.sections import CONSTRUCTOR_NAME, _class_doc, _own_doc

__all__ = [
    "Declarations",
    "FuncSig",
    "check_declarations",
    "is_static_const_value",
]

#: `code -> message_intent`, so every diagnostic carries its registry row's
#: own wording (the convention every `serpent.compiler` module follows).
_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

#: MJ-11's exhaustive-dispatch catch-all, used here for the one declaration
#: shape with no dedicated row: a module constant that is not a literal.
_FALLBACK_CODE = "SPT1037"

_CONST_HELP = (
    'a module constant is a compile-time literal, e.g. `ADMIN = Symbol("ADMIN")` or '
    "`LIMIT = U32(10)`; compute derived values inside a method"
)

_CYCLE_HELP = (
    "give the helper a bounded `while` loop instead of a recursive call, or inline the "
    "shared step at each call site"
)

_COLLISION_HELP = (
    "rename one of them: every function a contract module declares -- exports, "
    "module-level helpers, private methods -- has to be reachable under a name of its own"
)

#: `expr.py`'s by-name builtin dispatch, imported rather than restated (MJ-5):
#: a helper under one of these names could never be called.
_RECOGNIZED_BUILTINS = RECOGNIZED_BUILTINS


# --- the resolved declarations ------------------------------------------------


@dataclass(frozen=True)
class FuncSig:
    """One function's resolved signature -- an export, `__constructor`, a
    module-level helper, or a private method.

    This is what a `FuncIR` needs before its body is checked (`params`, `ret`,
    `doc`, `kind`, `export_name`) plus the two things only a CALL site needs
    (`takes_env`, `surface`). Task 10 builds `FuncCtx`s and `FuncIR`s from it;
    `expr.py` sees only the `InternalSig` projection.

    * `params` drops `self` and a leading `env: Env` (SS C.3's Function
      scope), matching `FuncCtx.params` exactly.
    * `takes_env` records that the dropped `env` was there, which is what a
      call site needs in order to require it in source (`double(env, x)`) and
      to contribute NO argument node for it.
    * `surface` is the call spelling for an INTERNAL function (`double`,
      `self._fee`) and `""` for an export -- an export is called by the HOST,
      never from contract code (E8 (b) allows helpers and private methods
      only).
    * `has_self` is whether `self` is in scope inside this function's BODY:
      true for every method of the `@contract` class, false for a
      module-level helper. It goes onto `FuncCtx` (`ctx.has_self`) so a call
      site decides `self.<method>(...)` from the enclosing declaration's
      IDENTITY -- two functions can share a name, so a name comparison cannot
      answer it (fix round 1, I-1).
    """

    py_name: str
    export_name: str
    kind: FuncKind
    params: tuple[tuple[str, Ty, Loc], ...]
    ret: Ty
    doc: str
    loc: Loc
    node: ast.FunctionDef
    takes_env: bool
    surface: str
    has_self: bool

    def internal_sig(self) -> InternalSig:
        """The call-site projection (E8). Only valid for an INTERNAL kind."""
        assert self.kind is FuncKind.INTERNAL, self.kind
        return InternalSig(
            surface=self.surface,
            fn_name=self.py_name,
            params=self.params,
            ret=self.ret,
            takes_env=self.takes_env,
            decl_loc=self.loc,
        )


@dataclass(frozen=True)
class Declarations:
    """Everything `check_declarations` resolved, ready for Task 10's assembly.

    `spec_types` is B9/B10's "declared, not discovered" inventory -- the
    STRUCT, UNION, INT ENUM and ERROR ENUM classes in declaration order, which
    is exactly what `build_spec_entries(cls, types=...)` takes. Events are in
    `events` and NOWHERE near `spec_types` (MJ-9). M1-E2's union and int-enum
    classes join this tuple with no IR node of their own (unlike a struct or
    an error enum, which also get a `StructDecl`/`ErrorEnumDecl`) -- nothing
    downstream of `spec_types` needs one yet, so none is built.
    """

    structs: tuple[StructDecl, ...]
    error_enums: tuple[ErrorEnumDecl, ...]
    events: tuple[EventDecl, ...]
    consts: tuple[ConstDecl, ...]
    spec_types: tuple[type[Any], ...]
    signatures: tuple[FuncSig, ...]
    internal_sigs: Mapping[str, InternalSig]

    @classmethod
    def empty(cls) -> Declarations:
        """The all-empty inventory, for a caller with nothing declared (and
        for a test that needs an unwired `FuncCtx`)."""
        return cls(
            structs=(),
            error_enums=(),
            events=(),
            consts=(),
            spec_types=(),
            signatures=(),
            internal_sigs={},
        )

    @property
    def exports(self) -> tuple[FuncSig, ...]:
        """The exported methods, in declaration order (`__init__` excluded --
        it is `constructor`)."""
        return tuple(sig for sig in self.signatures if sig.kind is FuncKind.EXPORT)

    @property
    def constructor(self) -> FuncSig | None:
        """`__init__`'s signature, or `None` when the contract declares none."""
        for sig in self.signatures:
            if sig.kind is FuncKind.CONSTRUCTOR:
                return sig
        return None

    @property
    def internals(self) -> tuple[FuncSig, ...]:
        """Every non-exported function: module-level helpers and private
        methods, in declaration order (contract methods first)."""
        return tuple(sig for sig in self.signatures if sig.kind is FuncKind.INTERNAL)


# --- entry point --------------------------------------------------------------


def check_declarations(loaded: LoadedModule, sink: Diagnostics) -> Declarations:
    """Check every declaration in `loaded`, reporting into `sink`.

    Never raises for bad SOURCE (collect-all, E16): a declaration that fails
    to check contributes nothing to the returned inventory and its diagnostic
    is in the sink, so a caller can keep checking the rest of the module and
    report everything at once. A `CompilerBugError` from the shared resolvers
    still propagates -- that is an internal invariant failure, not a user
    error (F.1.14).
    """
    structs: list[StructDecl] = []
    error_enums: list[ErrorEnumDecl] = []
    spec_types: list[type[Any]] = []
    for decl in loaded.decorated_types_in_order:
        if decl.kind == "struct":
            struct = _struct_decl(decl, loaded, sink)
            if struct is not None:
                structs.append(struct)
                spec_types.append(decl.cls)
        elif decl.kind == "error_enum":
            error_enums.append(_error_enum_decl(decl, loaded))
            spec_types.append(decl.cls)
        elif decl.kind in ("union", "enum"):
            # M1-E2: a tagged union and an int enum are declared TYPES a UDT
            # reference can name (E7), so each joins `spec_types` exactly as a
            # struct does -- but neither gets an IR node of its own
            # (`Declarations`' own docstring), so there is nothing else to do
            # here for either kind.
            spec_types.append(decl.cls)

    events = tuple(
        event
        for event in (_event_decl(decl, loaded, sink) for decl in loaded.events)
        if event is not None
    )

    consts = _const_decls(loaded, sink)
    signatures = _signatures(loaded, sink)
    _check_internal_name_collisions(signatures, loaded, sink)
    _check_call_graph(signatures, loaded, sink)

    internal_sigs = {sig.surface: sig.internal_sig() for sig in signatures if sig.surface}
    return Declarations(
        structs=tuple(structs),
        error_enums=tuple(error_enums),
        events=events,
        consts=consts,
        spec_types=tuple(spec_types),
        signatures=signatures,
        internal_sigs=internal_sigs,
    )


# --- decorated types ----------------------------------------------------------


def _struct_decl(decl: DecoratedDecl, loaded: LoadedModule, sink: Diagnostics) -> StructDecl | None:
    fields = _fields_of(decl, loaded, sink)
    if fields is None:
        return None
    return StructDecl(
        loc=Loc.from_node(loaded.path, decl.node),
        name=decl.name,
        doc=_class_doc(decl.cls),
        fields=fields,
    )


def _event_decl(decl: DecoratedDecl, loaded: LoadedModule, sink: Diagnostics) -> EventDecl | None:
    fields = _fields_of(decl, loaded, sink)
    if fields is None:
        return None
    return EventDecl(
        loc=Loc.from_node(loaded.path, decl.node),
        name=decl.name,
        doc=_class_doc(decl.cls),
        fields=fields,
    )


def _fields_of(
    decl: DecoratedDecl, loaded: LoadedModule, sink: Diagnostics
) -> tuple[tuple[str, Ty, str], ...] | None:
    """`(name, Ty, doc)` per field, in DECLARATION order (B10).

    Per-field `doc` is always `""`: `_serpent_type_` records no per-field doc
    (B13's named gap), and `StructDecl`/`EventDecl` carry the slot so closing
    that gap does not change the IR shape.

    `None` means a field annotation failed to resolve -- which real source
    cannot reach (the decorator already refused a non-chain field annotation,
    `SPT4012`, before the class ever executed), so this is the belt-and-braces
    path rather than the expected one.
    """
    fields: list[tuple[str, Ty, str]] = []
    declared: Sequence[tuple[str, object]] = decl.metadata["fields"]
    for name, annotation in declared:
        loc = _member_loc(loaded.path, decl.node, name)
        ty = resolve_annotation(annotation, loaded, loc, sink)
        if ty is None:
            return None
        fields.append((name, ty, ""))
    return tuple(fields)


def _error_enum_decl(decl: DecoratedDecl, loaded: LoadedModule) -> ErrorEnumDecl:
    """Cases in declaration order, each with the code its `errorcode(N)` call
    carried (A20/S10). The decorator has already proven every code is an int
    in range and unique, and the loader cross-checked the two views, so there
    is nothing left here that can fail."""
    cases: Sequence[tuple[str, int]] = decl.metadata["cases"]
    return ErrorEnumDecl(
        loc=Loc.from_node(loaded.path, decl.node),
        name=decl.name,
        # `_class_doc`, not `_own_doc`: `sections._enum_entry` reads an error
        # enum's doc exactly that way, and the IR must not disagree with the
        # spec about the same text (F.2.7's cross-check compares the two).
        doc=_class_doc(decl.cls),
        cases=tuple((name, code, "") for name, code in cases),
    )


def _member_loc(path: str, node: ast.ClassDef, name: str) -> Loc:
    """The class-body statement declaring `name`, or the class itself."""
    for member in node.body:
        if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
            if member.target.id == name:
                return Loc.from_node(path, member)
        elif isinstance(member, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in member.targets
        ):
            return Loc.from_node(path, member)
    return Loc.from_node(path, node)


# --- module constants (P5) ----------------------------------------------------


def _const_decls(loaded: LoadedModule, sink: Diagnostics) -> tuple[ConstDecl, ...]:
    """Check each module-level constant's value expression (P5).

    Module-level `AnnAssign` (`ADMIN: Symbol = Symbol("A")`) does not reach
    here at all: the loader refuses it with `SPT1031` and a note naming the
    plain-assignment rewrite, because a module constant's type IS its
    constructor. That ruling is deliberate -- the annotated spelling would add
    a second form plus an annotation-vs-value agreement rule and express
    nothing new.
    """
    consts: list[ConstDecl] = []
    for const in loaded.module_consts:
        node = const.node
        assert node.value is not None
        ctx = _module_ctx(loaded, sink, const.name)
        value = check_expr(node.value, ctx)
        if value.ty.tag is TyTag.INVALID:
            continue  # already reported (sink convention)
        if not is_static_const_value(value):
            sink.error(
                _FALLBACK_CODE,
                Loc.from_node(loaded.path, node.value),
                f"{_INTENT[_FALLBACK_CODE]}: `{const.name}` is not a compile-time literal",
                help=_CONST_HELP,
                notes=(
                    (
                        "a module constant becomes data in the compiled module, so there is "
                        "no phase in which a computation could run"
                    ),
                ),
            )
            continue
        consts.append(
            ConstDecl(
                loc=Loc.from_node(loaded.path, node),
                name=const.name,
                ty=value.ty,
                value=value,
            )
        )
    return tuple(consts)


def is_static_const_value(value: IRExpr) -> bool:
    """Whether `value` is a compile-time constant a module constant may hold.

    A literal (`Const`), or a container/struct built ENTIRELY out of literals.
    Everything else -- arithmetic, a parameter or local read, a host call, a
    reference to another constant -- is a reject: a module constant becomes
    data in the compiled module (P5), and there is no initialization phase in
    which a computation could run. Note that `MakeVec`/`MakeMap` carry their
    own `all_static` flag (MJ-15) and it is checked as well as the items,
    because that flag also records "C could totally order these keys".
    """
    if isinstance(value, Const):
        return True
    if isinstance(value, MakeVec):
        return value.all_static and all(is_static_const_value(item) for item in value.items)
    if isinstance(value, MakeMap):
        return value.all_static and all(
            is_static_const_value(key) and is_static_const_value(val) for key, val in value.pairs
        )
    if isinstance(value, MakeStruct):
        return all(is_static_const_value(field) for _name, field in value.fields)
    return False


def _module_ctx(loaded: LoadedModule, sink: Diagnostics, name: str) -> FuncCtx:
    """A `FuncCtx` for checking one module-level constant's value.

    Module scope has no parameters, no locals, no internal-call targets and no
    `self`: a constant's value is a literal, and every other shape is refused
    either by expression checking or by `is_static_const_value`. The identity
    fields are passed EXPLICITLY even though they match `FuncCtx`'s
    conservative defaults, because "module scope has no `self`" is a fact
    worth stating at the construction site.
    """
    return FuncCtx(
        loaded=loaded,
        sink=sink,
        params=[],
        locals=SlotTable(),
        loop_depth=0,
        return_ty=Ty.Void,
        alias_sets=AliasTable(),
        fn_name=name,
        path=loaded.path,
        fn_kind=FuncKind.INTERNAL,
        has_self=False,
    )


# --- function signatures ------------------------------------------------------


def _signatures(loaded: LoadedModule, sink: Diagnostics) -> tuple[FuncSig, ...]:
    """Every function the module declares, in declaration order: the contract
    class's methods first (source order, `__init__` included as written), then
    the module-level helpers.

    The class-body filter MIRRORS `loader._cross_check_contract`'s (an
    `ast.FunctionDef` whose executed attribute is still a plain function),
    because that is the filter `decorators.contract` used when it recorded the
    metadata the spec is built from. Any method the metadata records and this
    function silently skipped would be an export in `contractspecv0` with no
    `FuncIR` behind it -- F.1.14's skew, from the other direction -- which is
    why an `async def` method is REPORTED here rather than passed over: the
    decorator records it (`inspect.isfunction` accepts a coroutine function)
    and nothing else in the frontend looks at a method's `async` marker.
    """
    signatures: list[FuncSig] = []
    if loaded.contract_node is not None and loaded.contract_cls is not None:
        attributes = vars(loaded.contract_cls)
        for member in loaded.contract_node.body:
            if isinstance(member, ast.AsyncFunctionDef):
                sink.error(
                    "SPT1002",
                    Loc.from_node(loaded.path, member),
                    f"{_INTENT['SPT1002']}: `{member.name}` is declared `async`",
                    help="declare it with `def`, not `async def`",
                )
                continue
            if not isinstance(member, ast.FunctionDef):
                continue
            if _is_name_mangled(member.name):
                _reject_name_mangled(member, loaded, sink)
                continue
            func = attributes.get(member.name)
            if not inspect.isfunction(func):
                # Not a plain function in the executed view (a `staticmethod`,
                # a `@property`, or a member whose declaration the loader
                # already refused): nothing to resolve, and the diagnostic --
                # if any -- is already in the sink.
                continue
            sig = _resolve_signature(
                func,
                member,
                loaded,
                sink,
                kind=_method_kind(member.name),
                expects_self=True,
            )
            if sig is not None:
                signatures.append(sig)

    for helper in loaded.helpers:
        if not inspect.isfunction(helper.func):
            continue
        sig = _resolve_signature(
            helper.func,
            helper.node,
            loaded,
            sink,
            kind=FuncKind.INTERNAL,
            expects_self=False,
        )
        if sig is not None:
            signatures.append(sig)
    return tuple(signatures)


def _is_name_mangled(name: str) -> bool:
    """Python's private-name-mangling rule, exactly: at least two leading
    underscores and at most one trailing one (`__x` yes, `__init__` no)."""
    return name.startswith("__") and not name.endswith("__")


def _reject_name_mangled(node: ast.FunctionDef, loaded: LoadedModule, sink: Diagnostics) -> None:
    """Refuse a name-mangled method (fix round 1, I-2, controller ruling).

    `def __x(self)` inside `class Bank` binds the class attribute `_Bank__x`,
    and every `self.__x` reference in the class body is rewritten to
    `self._Bank__x` -- so the AST view says `__x` while the executed view says
    `_Bank__x`, and a compiler that resolved calls by the written name would
    silently drop the method (the shape that reached this fix round: no
    signature, a `Ty.Invalid` call result, and an EMPTY sink). Mangling
    support would be additive later; refusing it now keeps the two views in
    agreement and costs an author one underscore.

    `SPT1037` is MJ-11's catch-all, used here because no `SPT4xxx` row fits by
    KIND: the member kind is legitimate (a method in a `@contract` class body,
    so not `SPT4020`), the signature shape is legitimate (not
    `SPT4001`-`SPT4007`), and the name is within the Symbol charset and the
    30-character cap (not `SPT5001`). What is unsupported is the CONSTRUCT --
    a name the language rewrites -- which is exactly what this row says.
    """
    sink.error(
        _FALLBACK_CODE,
        Loc.from_node(loaded.path, node),
        f"{_INTENT[_FALLBACK_CODE]}: `{node.name}` is a name-mangled method",
        help=f"use a single leading underscore for a private method: `_{node.name.lstrip('_')}`",
        notes=(
            (
                f"python rewrites `self.{node.name}` to `self._<Class>{node.name}` inside the "
                "class body, so the declared name and the compiled name would disagree"
            ),
        ),
    )


def _method_kind(name: str) -> FuncKind:
    """`__init__` is the constructor; an underscore-prefixed method is an
    internal function (E8 (b)); everything else is an export."""
    if name == "__init__":
        return FuncKind.CONSTRUCTOR
    return FuncKind.INTERNAL if name.startswith("_") else FuncKind.EXPORT


def _resolve_signature(
    func: Any,
    node: ast.FunctionDef,
    loaded: LoadedModule,
    sink: Diagnostics,
    *,
    kind: FuncKind,
    expects_self: bool,
) -> FuncSig | None:
    """One function's `FuncSig`, or `None` after reporting.

    Presence of an annotation is read off `inspect.signature` (what was
    WRITTEN) while its value comes from `decorators._annotations_of` (what it
    RESOLVES to) -- the same split `decorators._check_method` makes, which is
    what keeps a PEP 563 module and its twin identical (E4).
    """
    path = loaded.path
    loc = Loc.from_node(path, node)
    signature = inspect.signature(func)
    try:
        hints = _annotations_of(func)
    except ValueError as exc:
        # An annotation naming something the module never defined. Reported
        # against the declaration, with the resolver's own text as the note.
        sink.error(
            "SPT2003",
            loc,
            f"{_INTENT['SPT2003']}: `{node.name}`",
            notes=(str(exc),),
        )
        return None

    parameters = list(signature.parameters.values())
    if expects_self:
        if not parameters or parameters[0].name != "self":
            first = parameters[0].name if parameters else "<none>"
            sink.error(
                "SPT4001",
                loc,
                f"{_INTENT['SPT4001']}: `{node.name}` takes `{first}` first",
                help="give the method `self` as its first parameter",
            )
            return None
        parameters = parameters[1:]

    ok = True
    takes_env = False
    params: list[tuple[str, Ty, Loc]] = []
    for index, parameter in enumerate(parameters):
        param_loc = _param_loc(path, node, parameter.name) or loc
        rejected = _reject_parameter_shape(parameter, node, param_loc, sink)
        if rejected:
            ok = False
            continue
        annotation = hints.get(parameter.name)
        if index == 0 and annotation is Env:
            # SS C.3: a leading `env: Env` is the host handle, not a value --
            # dropped from the signature, never resolved to a `Ty` (which is
            # also `resolve_annotation`'s documented position rule).
            takes_env = True
            continue
        ty = resolve_annotation(annotation, loaded, param_loc, sink)
        if ty is None:
            ok = False
            continue
        params.append((parameter.name, ty, param_loc))

    ret = _resolve_return(node, signature, hints, loaded, loc, sink)
    if ret is None:
        ok = False
    if not ok:
        return None
    assert ret is not None
    surface = _surface_of(node.name, kind, expects_self)
    return FuncSig(
        py_name=node.name,
        export_name=CONSTRUCTOR_NAME if kind is FuncKind.CONSTRUCTOR else node.name,
        kind=kind,
        params=tuple(params),
        ret=ret,
        doc=_own_doc(func),
        loc=loc,
        node=node,
        takes_env=takes_env,
        surface=surface,
        has_self=expects_self,
    )


def _surface_of(name: str, kind: FuncKind, expects_self: bool) -> str:
    """How an INTERNAL target is CALLED (E8): `self.<name>` for a private
    method, the bare name for a module-level helper. `""` for anything the
    host calls instead (an export or `__constructor`)."""
    if kind is not FuncKind.INTERNAL:
        return ""
    return f"self.{name}" if expects_self else name


def _reject_parameter_shape(
    parameter: inspect.Parameter, node: ast.FunctionDef, loc: Loc, sink: Diagnostics
) -> bool:
    """Report the parameter shapes no serpent function can express, returning
    whether one fired.

    Same rules and same codes `decorators._check_method` raises for an export
    -- the point is that a module-level helper and a private method get them
    too, since the decorator never looks at either (and, unlike the
    decorator, these are located at the parameter).
    """
    name = parameter.name
    if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
        sink.error(
            "SPT4002",
            loc,
            f"{_INTENT['SPT4002']}: `*{name}` in `{node.name}`",
            help="list every parameter explicitly; a call has a fixed arity",
        )
        return True
    if parameter.kind is inspect.Parameter.VAR_KEYWORD:
        sink.error(
            "SPT4002",
            loc,
            f"{_INTENT['SPT4002']}: `**{name}` in `{node.name}`",
            help="list every parameter explicitly; arguments are passed positionally",
        )
        return True
    if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
        sink.error(
            "SPT4002",
            loc,
            f"{_INTENT['SPT4002']}: `{name}` in `{node.name}` is keyword-only",
            help="drop the `*` marker: arguments are passed positionally",
        )
        return True
    if parameter.default is not inspect.Parameter.empty:
        sink.error(
            "SPT4003",
            loc,
            f"{_INTENT['SPT4003']}: `{name}` in `{node.name}`",
            help="drop the default and require the argument at every call site",
        )
        return True
    if parameter.annotation is inspect.Parameter.empty:
        sink.error(
            "SPT4004",
            loc,
            f"{_INTENT['SPT4004']}: `{name}` in `{node.name}`",
            help="annotate the parameter with a chain type",
        )
        return True
    return False


def _resolve_return(
    node: ast.FunctionDef,
    signature: inspect.Signature,
    hints: Mapping[str, Any],
    loaded: LoadedModule,
    loc: Loc,
    sink: Diagnostics,
) -> Ty | None:
    """The declared return `Ty`: `Ty.Void` for `-> None`, else the resolved
    annotation. `None` after reporting a missing or unmappable one."""
    if signature.return_annotation is inspect.Signature.empty:
        sink.error(
            "SPT4005",
            loc,
            f"{_INTENT['SPT4005']}: `{node.name}`",
            help="annotate the return type (`-> None` for a function returning nothing)",
        )
        return None
    returns = hints.get("return")
    if returns is NoneType or returns is None:
        # `Ty.Void` is constructed directly for this one position -- never
        # through `resolve_annotation`, per that function's own position rule.
        return Ty.Void
    ret_loc = Loc.from_node(loaded.path, node.returns) if node.returns is not None else loc
    return resolve_annotation(returns, loaded, ret_loc, sink)


def _param_loc(path: str, node: ast.FunctionDef, name: str) -> Loc | None:
    """The `Loc` of one parameter in the `def`, or `None` if it is not there.

    `ast.arg` carries a real span but is neither an `expr` nor a `stmt`, so
    `Loc.from_node` (typed for those two families) cannot take it; the span is
    read here with the same never-fabricate discipline (P2).
    """
    args = node.args
    for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
        if arg is None or arg.arg != name:
            continue
        end_line = arg.end_lineno
        end_col = arg.end_col_offset
        assert end_line is not None and end_col is not None, f"{arg!r} is not a parsed node"
        return Loc(
            path=path,
            kind=LocKind.NODE,
            line=arg.lineno,
            col=arg.col_offset,
            end_line=end_line,
            end_col=end_col,
        )
    return None


# --- the internal-function namespace and the call graph (E8) ------------------


def _check_internal_name_collisions(
    signatures: Sequence[FuncSig], loaded: LoadedModule, sink: Diagnostics
) -> None:
    """Refuse a module-level helper whose name is already taken.

    Three collisions, all reported as `SPT2004` ("name shadows an existing
    declaration") because all three are one name meaning two things:

    * **helper vs. private method** -- both compile to a NON-EXPORTED wasm
      function and `InternalCall.fn_name` is that function's python name, so
      two targets under one name make a lowered call ambiguous.
    * **helper vs. export** (fix round 1, M-4) -- legal python, and the shape
      spec Sec.2 nearly writes (a `balance` helper beside a `balance` export),
      but two functions of one name in one module make every mention of it
      ambiguous to a READER: `balance(env, x)` is the helper while
      `self.balance(...)` would have been the export. Refusing is the
      no-shadowing posture the rest of the compiler takes; relaxing it later
      is additive.
    * **helper vs. a compiler-recognized builtin** (M-5) -- `len` and `bool`
      are dispatched by NAME in `expr.py` before any helper lookup (D3/MJ-1),
      so a helper called `len` could never be called at all. Naming that at
      the declaration beats a mystifying call site.

    Private methods are exempt from the last two: `self._x` cannot collide
    with a bare `len(...)` or with an export's own call shape.
    """
    internal_seen: dict[str, FuncSig] = {}
    exported: dict[str, FuncSig] = {
        sig.py_name: sig for sig in signatures if sig.kind is not FuncKind.INTERNAL
    }
    for sig in signatures:
        if sig.kind is not FuncKind.INTERNAL:
            continue
        previous = internal_seen.get(sig.py_name)
        if previous is not None:
            _report_collision(
                sig,
                sink,
                (
                    f"`{previous.surface}` (line {previous.loc.line}) and `{sig.surface}` "
                    f"both compile to an internal function named `{sig.py_name}`"
                ),
            )
            continue
        internal_seen[sig.py_name] = sig
        if sig.has_self:
            continue
        export = exported.get(sig.py_name)
        if export is not None:
            _report_collision(
                sig,
                sink,
                (
                    f"`{sig.py_name}` is also a contract method (line {export.loc.line}), "
                    f"exported as `{export.export_name}`"
                ),
            )
        elif sig.py_name in _RECOGNIZED_BUILTINS:
            _report_collision(
                sig,
                sink,
                (
                    f"`{sig.py_name}(...)` is a builtin the compiler recognizes by name, so "
                    "a call would never reach this helper"
                ),
            )


def _report_collision(sig: FuncSig, sink: Diagnostics, note: str) -> None:
    sink.error(
        "SPT2004",
        sig.loc,
        f"{_INTENT['SPT2004']}: `{sig.py_name}`",
        help=_COLLISION_HELP,
        notes=(note,),
    )


def _internal_call_edges(
    node: ast.FunctionDef, targets: Mapping[str, FuncSig], path: str
) -> Iterator[tuple[str, Loc]]:
    """Every internal-call edge out of one function body, with its call site.

    Purely SYNTACTIC, and deliberately so: the graph has to be complete before
    any body is type-checked (a cycle must be reported instead of recursing
    into it), and the two call shapes E8 admits are both recognizable from the
    AST alone.
    """
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        func = inner.func
        surface = ""
        if isinstance(func, ast.Name):
            surface = func.id
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
        ):
            surface = f"self.{func.attr}"
        if surface and surface in targets:
            yield surface, Loc.from_node(path, inner)


def _check_call_graph(
    signatures: Sequence[FuncSig], loaded: LoadedModule, sink: Diagnostics
) -> None:
    """Reject every cycle among the internal targets (E8/SPT7005).

    Exports are not nodes: nothing in a contract can call one (E8 (b) admits
    module-level helpers and private methods only), so every cycle lives
    entirely inside the internal set. Rejecting cycles is what keeps
    stack-depth and budget reasoning trivial, and it is checked HERE -- before
    any body is walked -- so a recursive helper is a located diagnostic rather
    than a compiler that recurses.

    The DFS keeps its own EXPLICIT stack rather than recursing (fix round 1,
    M-3): this function promises never to raise, and a module with a long
    helper chain would otherwise hit python's recursion limit -- a compiler
    that dies on a call chain while checking for call chains.
    """
    targets = {sig.surface: sig for sig in signatures if sig.kind is FuncKind.INTERNAL}
    edges = {
        surface: list(_internal_call_edges(sig.node, targets, loaded.path))
        for surface, sig in targets.items()
    }

    #: 0 = unvisited, 1 = on the current path, 2 = finished.
    state: dict[str, int] = dict.fromkeys(targets, 0)
    reported: set[frozenset[str]] = set()

    for root in targets:
        if state[root] != 0:
            continue
        # Each frame is (surface, iterator over its remaining edges); the
        # surfaces currently in `frames` ARE the path, which is what a cycle is
        # sliced out of.
        frames: list[tuple[str, Iterator[tuple[str, Loc]]]] = [(root, iter(edges[root]))]
        state[root] = 1
        while frames:
            surface, remaining = frames[-1]
            edge = next(remaining, None)
            if edge is None:
                state[surface] = 2
                frames.pop()
                continue
            target, loc = edge
            if state[target] == 1:
                path = [frame for frame, _edges in frames]
                cycle = path[path.index(target) :]
                key = frozenset(cycle)
                if key not in reported:
                    reported.add(key)
                    _report_cycle(cycle, loc, sink)
            elif state[target] == 0:
                state[target] = 1
                frames.append((target, iter(edges[target])))


def _report_cycle(cycle: Sequence[str], loc: Loc, sink: Diagnostics) -> None:
    """One `SPT7005`, located at the call that CLOSES the cycle and naming
    every function in it."""
    if len(cycle) == 1:
        detail = f"`{cycle[0]}` calls itself"
        note = "a recursive call has no bound the compiler can prove"
    else:
        detail = f"`{cycle[0]}` is reached again through its own call graph"
        note = " -> ".join([*cycle, cycle[0]])
    sink.error(
        "SPT7005",
        loc,
        f"{_INTENT['SPT7005']}: {detail}",
        help=_CYCLE_HELP,
        notes=(note,),
    )
