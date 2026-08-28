"""The hybrid module loader: parse, shape-check, execute, cross-check.

Task 3 of the M1-C plan, implementing ruling **E1 (hybrid frontend)**: a
contract module is *executed* so that declarations, annotations and
`_serpent_type_` metadata are real Python objects (which is what
`spec.sections.build_spec_entries` contracts for, B9, and what makes
`bytes_n(20)` in an annotation resolvable at all, E20), while method *bodies*
are compiled from the AST by later tasks. A mandatory inventory cross-check
asserts the two views agree (F.1.14).

**Build-time execution is real and documented**: compiling a contract runs its
module-level code. The loader narrows the blast radius as far as it can --
module shape is validated *before* anything executes, and a statement the
shape check rejects is never executed at all.

## Why one statement at a time

`decorators.py` and `spec/sections.py` enforce the whole declaration-shape
inventory (dossier SS B.3) as bare `ValueError`s/`TypeError`s carrying **no
line number**. The compiler's job is to re-report each one as a located
diagnostic. Executing the module as a single unit would leave only a Python
traceback to mine for a location; instead the loader compiles and executes
**one top-level AST statement at a time** (`compile(ast.Module([stmt], []),
path, "exec")`, MJ-4), so every exec-time exception already comes with the
exact failing statement node. Name-matching on the exception message is then
needed only to disambiguate *within* a class body -- decorators.py's messages
are uniformly prefixed `Cls.member: ...`, which is what
`_locate`/`_classify` reads. Anything that cannot be narrowed further lands on
its own statement's `Loc`; a `WHOLE_FILE` location is used only for a fact
that is genuinely module-scoped (P2). A raw traceback never escapes.

Two consequences of the statement-at-a-time design are load-bearing and easy
to get wrong:

* `compile()` inherits the *calling* module's `__future__` flags unless
  `dont_inherit=True` is passed -- and this file uses `from __future__ import
  annotations`, so without it every contract would silently be compiled under
  PEP 563 whether it asked for it or not.
* A `from __future__ import annotations` statement in the contract only
  affects the unit it is compiled with, so its flag must be re-applied to
  every subsequent statement explicitly. Under PEP 563 the decorators resolve
  annotations through `typing.get_type_hints`, which reads
  `sys.modules[cls.__module__].__dict__` for a class -- so the loader executes
  into a real `types.ModuleType` that is registered in `sys.modules` for the
  duration of the load and removed afterwards.

Together these keep the recorded metadata identical for a PEP 563 module and
its non-PEP-563 twin (ruling E4), which is exactly what `decorators.py`
promises.

## Code selection

Every diagnostic cites a code from the frozen `serpent.compiler.codes`
registry; the loader invents none. Its `message` is the registry row's
`message_intent` (optionally prefixed with the `Cls.member` the decorator
named), so wording stays aligned with the code, and the decorator's own
full-text explanation is preserved verbatim as a `note`. Every message
contains its code's registry intent; the uses worth naming are:

* `SPT4019` and `SPT4020` were appended to the registry for this loader by
  controller ruling in Task 3's review round: the module-scope
  "expected exactly one `@contract` class" fact (SS C.3), and the
  decorated-class-body member-shape rule (SS C.3's "there are no class
  attributes"). Both had been reusing a code whose wording did not fit.
* `SPT4015` is now exactly the *top-level class decorator/base* shape: an
  undecorated or multiply-decorated class, or a base class other than `Event`
  on an event (SS B.1's `ClassDef` row, D8).
* `SPT2004` ("name shadows an existing declaration") covers both a
  module-level redeclaration and a duplicate member inside one class body.
* `SPT1037` -- MJ-11's explicit exhaustive-dispatch catch-all -- is the
  last resort: a Python `SyntaxError` (from `ast.parse` OR from `compile`,
  which rejects a whole class of source that parses fine), and any exec-time
  exception the bridge table below does not recognize. The original text
  always rides along as a note, so nothing is lost even in that case.
* `SPT5001`/`SPT3004`/`SPT2001` are owned by later tasks (9, 5, 5), which
  pre-empt them against the AST *before* exec. The bridge keeps entries for
  them anyway, as the backstop for the window where the decorator is the only
  thing checking.

## What of SS B.3 lands here, and what does not

The bridge covers **every check `decorators.py` performs**, which is what runs
at exec time. The remaining SS B.3 items fire elsewhere and are other tasks':
`spec/sections.py`'s limits (type/case name > 60, doc > 1024 encoded bytes)
run at spec-emission time, and the `__constructor`/parameter names the
decorators never check at all (B11) have no runtime trigger -- Task 9's
`validate_limits` pre-empts all of those against the AST. Parameter
annotations that are resolvable but unmappable to the contract spec (B7) are
Task 4's `resolve_annotation`; the decorators only apply the chain-type test
to struct/event *fields*, which is the `SPT4012` row below.
"""

from __future__ import annotations
import __future__ as _future_module

import ast
import builtins
import inspect
import itertools
import re
import sys
import types
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple, NoReturn

import serpent
from serpent.compiler import codes
from serpent.compiler.diagnostics import CompileError, Diagnostics, Loc, LocKind

# Imported, never restated (MJ-5's principle applied here too): the metadata
# attribute name is `decorators.py`'s to define.
from serpent.decorators import _METADATA_ATTR

__all__ = [
    "CompilerBugError",
    "DecoratedDecl",
    "Helper",
    "LoadedModule",
    "ModuleConst",
    "load_module",
]


class CompilerBugError(AssertionError):
    """The metadata view and the AST view of a declaration disagree (F.1.14).

    Deliberately an `AssertionError`, NOT a `ValueError`: it is an internal
    invariant failure, so it must not be catchable alongside `CompileError`
    (which is a `ValueError`, A10) by a caller collecting user diagnostics.
    A skew here means the compiler is wrong, not the contract.
    """


# --- the loaded-module model ------------------------------------------------


@dataclass(frozen=True)
class DecoratedDecl:
    """One serpent-decorated class, in both views.

    `cls`/`metadata` are the executed (import) view; `node` is the AST view.
    `kind` is the `_serpent_type_["kind"]` string -- `"contract"`,
    `"struct"`, `"error_enum"` or `"event"`.
    """

    name: str
    kind: str
    cls: type[Any]
    node: ast.ClassDef
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class ModuleConst:
    """A module-level chain constant (P5), with its executed value."""

    name: str
    node: ast.Assign
    value: object


@dataclass(frozen=True)
class Helper:
    """A module-level private helper function (E8), with its executed object."""

    name: str
    node: ast.FunctionDef
    func: Any


@dataclass(frozen=True)
class LoadedModule:
    """Everything later tasks need from a loaded contract module.

    The two contract views are deliberately NOT symmetric, because they fail
    at different phases and a located diagnostic is worth more than a uniform
    `None`:

    * `contract_node` is set whenever the module has exactly one shape-valid
      `@contract` class -- even if *executing* it then failed. Later
      diagnostics can still point at real source.
    * `contract_cls`/`contract_decl` are set only when that class also
      executed and carries `_serpent_type_` metadata.

    So `contract_node is not None and contract_cls is None` means "the class
    is there, its declaration is broken", and `diagnostics` says how. Both
    are `None` when the module has no `@contract` class or more than one.
    """

    path: str
    source_lines: tuple[str, ...]
    tree: ast.Module
    namespace: dict[str, Any]
    contract_cls: type[Any] | None
    contract_node: ast.ClassDef | None
    contract_decl: DecoratedDecl | None
    #: STRUCTS + ERROR ENUMS in declaration order (B10). Events are tracked
    #: separately per MJ-9 -- `spec.sections` refuses an event class.
    decorated_types_in_order: tuple[DecoratedDecl, ...]
    events: tuple[DecoratedDecl, ...]
    module_consts: tuple[ModuleConst, ...]
    helpers: tuple[Helper, ...]
    diagnostics: Diagnostics


# --- registry lookups -------------------------------------------------------

#: `code -> message_intent`, so a diagnostic's wording tracks its registry row
#: instead of being restated here.
_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

#: The last resort for anything unrecognized (MJ-11's catch-all row).
_FALLBACK_CODE = "SPT1037"

#: `help:` rewrites. Mandatory for every SPT1xxx (F.2.11, enforced by the
#: sink); supplied for the rest because a declaration error is exactly where
#: an author wants the corrected form spelled out.
_HELP: dict[str, str] = {
    "SPT1031": (
        "a contract module's top level may contain only `from __future__ import "
        "annotations`, `from serpent import ...`, module-level chain constants, "
        "serpent-decorated classes, and module-level helper functions"
    ),
    "SPT1037": (
        "the file must be valid Python that the serpent subset covers; see the "
        "note for the original error"
    ),
    "SPT2001": "define the name at module level, or import it from serpent",
    "SPT2003": (
        "annotated types must be resolvable at module level -- declare the type "
        "before the declaration that annotates it, or import it from serpent"
    ),
    "SPT2004": "give the declaration a name no other module-level name already uses",
    "SPT2005": (
        "a contract's only imports are `from serpent import <name>` (names in "
        "`serpent.__all__`) and `from __future__ import annotations`"
    ),
    "SPT3004": "use a value the target chain type can represent",
    "SPT4001": "give the method `self` as its first parameter",
    "SPT4002": "list every parameter explicitly; an export has a fixed arity",
    "SPT4003": "drop the default and require the argument at every call site",
    "SPT4004": "annotate the parameter with a chain type",
    "SPT4005": "annotate the return type (`-> None` for a method returning nothing)",
    "SPT4006": "annotate `__init__` as `-> None`",
    "SPT4007": "make it a plain method taking `self` first",
    "SPT4008": "declare the member as `NAME = errorcode(N)` with an int `N`",
    "SPT4009": "pick a code in [0, 0xFFFFFF00)",
    "SPT4010": "give every member of the enum a distinct code",
    "SPT4011": "declare at least one `NAME = errorcode(N)` member",
    "SPT4012": (
        "annotate the field with a chain type, a `@contracttype` struct, or `X | None` of one"
    ),
    "SPT4013": "apply exactly one serpent decorator per class",
    "SPT4014": "declare the event as `class Name(Event):`",
    "SPT4015": (
        "give every top-level class exactly one of @contract/@contracttype/"
        "@contracterror/@contractevent, and no base class other than `Event` on an "
        "event"
    ),
    "SPT4019": "declare exactly one @contract class in the module",
    "SPT5001": "use at most 30 characters from [a-zA-Z0-9_]",
}


# --- module-shape vocabulary -----------------------------------------------

_DECORATOR_KINDS: dict[str, str] = {
    "contract": "contract",
    "contracttype": "struct",
    "contracterror": "error_enum",
    "contractevent": "event",
}

_FUTURE_ANNOTATIONS_FLAG = _future_module.annotations.compiler_flag

#: Distinct `sys.modules` key per load, so two loads (or a nested one) can
#: never collide with each other or with a real module.
_MODULE_COUNTER = itertools.count()


# --- the exec-time bridge table (dossier SS B.3) ---------------------------

_REFINE_ERRORCODE = "errorcode_call"

#: The exception types MJ-4 names: `decorators.py` raises `ValueError` for
#: every declaration-shape check, `TypeError` for `errorcode(<not an int>)`,
#: and a `NameError` reaches exec first when an annotation is evaluated
#: eagerly. Anything else is caught by a second, wider clause in `_execute`
#: and bridged to the catch-all code -- a traceback must never escape the
#: loader (F.2.5), and a module-level constant can raise anything its chain
#: type raises.
_BRIDGED_EXCEPTIONS = (ValueError, TypeError, NameError)


class _BridgeRule(NamedTuple):
    """One `(exception, message needle) -> code` bridging row.

    Matching on a message substring is what makes an unlocated `ValueError`
    classifiable at all; the loader's own tests exercise every row from real
    source, so a reworded check in `decorators.py` fails loudly here rather
    than silently degrading to the catch-all code.
    """

    exc_types: tuple[type[BaseException], ...]
    needle: str
    code: str
    refine: str = ""


_VALUE_ERROR = (ValueError,)

#: In `decorators.py` source order, most specific needle first.
_BRIDGE_RULES: tuple[_BridgeRule, ...] = (
    # errorcode(N) with a non-int N raises a TypeError from the *class body*,
    # so its message names no member at all -- hence the AST refinement.
    _BridgeRule((TypeError,), "errorcode() takes an int code", "SPT4008", _REFINE_ERRORCODE),
    _BridgeRule(_VALUE_ERROR, "@contracterror members must be declared", "SPT4008"),
    _BridgeRule(_VALUE_ERROR, "is out of range -- contract codes are", "SPT4009"),
    _BridgeRule(_VALUE_ERROR, "is already used by", "SPT4010"),
    _BridgeRule(_VALUE_ERROR, "@contracterror needs at least one member", "SPT4011"),
    _BridgeRule(_VALUE_ERROR, "is not a chain type, a `@contracttype` struct", "SPT4012"),
    _BridgeRule(_VALUE_ERROR, "already declared as a serpent", "SPT4013"),
    _BridgeRule(_VALUE_ERROR, "@contractevent classes must inherit", "SPT4014"),
    _BridgeRule(_VALUE_ERROR, "contract methods are plain methods taking", "SPT4007"),
    _BridgeRule(_VALUE_ERROR, "contract methods take `self` as their first", "SPT4001"),
    _BridgeRule(_VALUE_ERROR, "is not allowed -- a contract export has a fixed arity", "SPT4002"),
    _BridgeRule(_VALUE_ERROR, "is not allowed -- the host invokes exports positionally", "SPT4002"),
    _BridgeRule(
        _VALUE_ERROR, "has a default value, which contractspecv0 cannot express", "SPT4003"
    ),
    _BridgeRule(_VALUE_ERROR, "needs a type annotation -- exported signatures", "SPT4004"),
    _BridgeRule(_VALUE_ERROR, "the return type needs an annotation", "SPT4005"),
    _BridgeRule(_VALUE_ERROR, "must be annotated `-> None`", "SPT4006"),
    _BridgeRule(_VALUE_ERROR, "names are capped at", "SPT5001"),
    _BridgeRule(_VALUE_ERROR, "names must be valid Symbols", "SPT5001"),
    _BridgeRule(_VALUE_ERROR, "cannot resolve annotations", "SPT2003"),
    # A chain-type constructor refusing a literal, reachable from a
    # module-level constant or a class-body value.
    _BridgeRule(_VALUE_ERROR, "is out of range for", "SPT3004"),
    # An empty needle: any NameError the cascade filter did not swallow.
    _BridgeRule((NameError,), "", "SPT2001"),
)

#: `decorators.py` prefixes every declaration-site message with the offender
#: as `Cls.member: ...` (or `Cls.member must ...` for `__init__`), which is
#: the only within-class disambiguation the loader needs (MJ-4).
_QUALIFIER_RE = re.compile(r"^(?P<cls>\w+)\.(?P<member>\w+)\b")

#: Both the raw `NameError` and `decorators._annotations_of`'s wrapped
#: `ValueError` spell the unresolvable name the same way.
_MISSING_NAME_RE = re.compile(r"name '(?P<name>\w+)' is not defined")


# --- public entry point -----------------------------------------------------


def load_module(source: str, path: str) -> LoadedModule:
    """Parse, shape-check and execute one contract module.

    Diagnostics are collected in the returned `LoadedModule.diagnostics` sink
    rather than raised (E16), so a caller can keep going and report
    everything at once. The one exception is a `SyntaxError`: with no tree
    there is nothing to keep going with, so it is raised as a `CompileError`
    carrying a single located diagnostic.
    """
    sink = Diagnostics()
    source_lines = tuple(source.splitlines())

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        _report_syntax_error(exc, path, sink)
        raise CompileError(sink.diagnostics) from exc

    plan = _validate_module_shape(tree, path, sink)
    namespace, failed_statements = _execute(plan, path, sink)
    (
        contract_decl,
        decorated_types,
        events,
    ) = _collect_inventories(plan, namespace, failed_statements)

    all_decls = [*decorated_types, *events]
    if contract_decl is not None:
        all_decls.append(contract_decl)
    _cross_check_inventory(all_decls)

    return LoadedModule(
        path=path,
        source_lines=source_lines,
        tree=tree,
        namespace=namespace,
        contract_cls=None if contract_decl is None else contract_decl.cls,
        contract_node=plan.contract_node,
        contract_decl=contract_decl,
        decorated_types_in_order=tuple(decorated_types),
        events=tuple(events),
        module_consts=tuple(
            ModuleConst(
                name=_assign_target(node), node=node, value=namespace.get(_assign_target(node))
            )
            for node in plan.consts
            if id(node) not in failed_statements
        ),
        helpers=tuple(
            Helper(name=node.name, node=node, func=namespace.get(node.name))
            for node in plan.helpers
            if id(node) not in failed_statements
        ),
        diagnostics=sink,
    )


def _report_syntax_error(
    exc: SyntaxError, path: str, sink: Diagnostics, *, fallback: Loc | None = None
) -> None:
    """Report one `SyntaxError` -- from `ast.parse` or from `compile` -- located.

    `fallback` is the statement's own `Loc`, used only if the exception
    carries no line of its own (P2: a location is never fabricated, but a
    real statement span is always better than `WHOLE_FILE`).
    """
    loc = _loc_from_syntax_error(path, exc)
    if loc.kind is LocKind.WHOLE_FILE and fallback is not None:
        loc = fallback
    sink.error(
        _FALLBACK_CODE,
        loc,
        f"{_INTENT[_FALLBACK_CODE]}: invalid Python syntax ({exc.msg})",
        help=_HELP[_FALLBACK_CODE],
    )


def _loc_from_syntax_error(path: str, exc: SyntaxError) -> Loc:
    """A real `Loc` for a `SyntaxError`, never a fabricated one (P2)."""
    if exc.lineno is None:
        return Loc.whole_file(path)
    line = max(1, exc.lineno)
    col = max(0, (exc.offset or 1) - 1)
    end_line = exc.end_lineno if exc.end_lineno is not None else line
    end_col = max(col + 1, (exc.end_offset or (col + 2)) - 1)
    return Loc(path=path, kind=LocKind.NODE, line=line, col=col, end_line=end_line, end_col=end_col)


# --- module-shape validation (dossier SS B.1 Module / Import / ClassDef) ----


@dataclass
class _ShapePlan:
    """What the shape check learned, and what exec is allowed to run."""

    executable: list[ast.stmt] = field(default_factory=list)
    #: Accepted top-level classes, with the kind their decorator declares.
    classes: list[tuple[ast.ClassDef, str]] = field(default_factory=list)
    consts: list[ast.Assign] = field(default_factory=list)
    helpers: list[ast.FunctionDef] = field(default_factory=list)
    #: Local name -> `serpent.__all__` name, so an aliased import still
    #: resolves a decorator or the `Event` base.
    alias_map: dict[str, str] = field(default_factory=dict)
    #: Names bound by a statement the shape check refused, so a later
    #: `NameError` for one of them is recognized as a cascade, not an error.
    failed_names: set[str] = field(default_factory=set)
    #: Module-level name -> the statement that claimed it, so a redeclaration
    #: is refused before it can desynchronize the two views (`_claim_name`).
    declared: dict[str, ast.stmt] = field(default_factory=dict)
    has_future_annotations: bool = False
    contract_node: ast.ClassDef | None = None


def _validate_module_shape(tree: ast.Module, path: str, sink: Diagnostics) -> _ShapePlan:
    plan = _ShapePlan()
    for index, stmt in enumerate(tree.body):
        if index == 0 and _is_docstring(stmt):
            continue  # P1: the module docstring is skipped, not rejected.
        if isinstance(stmt, ast.ImportFrom):
            _check_import_from(stmt, path, plan, sink)
        elif isinstance(stmt, ast.Import):
            _reject_statement(
                stmt,
                path,
                plan,
                sink,
                "SPT2005",
                tuple(f"`import {alias.name}` is not allowed" for alias in stmt.names),
                {alias.asname or alias.name.split(".")[0] for alias in stmt.names},
            )
        elif isinstance(stmt, ast.ClassDef):
            if _claim_name(stmt, stmt.name, path, plan, sink):
                _check_class_def(stmt, path, plan, sink)
        elif isinstance(stmt, ast.FunctionDef):
            if _claim_name(stmt, stmt.name, path, plan, sink):
                plan.helpers.append(stmt)
                plan.executable.append(stmt)
        elif isinstance(stmt, ast.Assign) and _is_single_name_assign(stmt):
            if _claim_name(stmt, _assign_target(stmt), path, plan, sink):
                plan.consts.append(stmt)
                plan.executable.append(stmt)
        else:
            _reject_statement(
                stmt,
                path,
                plan,
                sink,
                "SPT1031",
                _module_body_notes(stmt),
                _bound_names(stmt),
            )
    _check_contract_count(plan, path, sink)
    return plan


def _claim_name(stmt: ast.stmt, name: str, path: str, plan: _ShapePlan, sink: Diagnostics) -> bool:
    """Reserve one module-level declaration name, or reject a redeclaration.

    Two top-level declarations of the same name would leave the AST view and
    the executed view pointing at *different* objects (the namespace keeps
    only the last binding), which the F.1.14 cross-check would then report as
    a compiler bug on perfectly parseable user input. Refusing the second
    declaration keeps that hard failure unreachable from source, and is the
    right answer anyway: two same-named declarations cannot both appear in
    one `contractspecv0`.

    The registry has no module-level-redeclaration row, so this reuses
    `SPT2004` (shadowing) -- the closest intent, "name shadows an existing
    declaration".
    """
    previous = plan.declared.get(name)
    if previous is not None:
        sink.error(
            "SPT2004",
            Loc.from_node(path, stmt),
            _INTENT["SPT2004"],
            help=_HELP["SPT2004"],
            notes=(
                (
                    f"`{name}` is already declared at line {previous.lineno}; a contract "
                    "module's declarations all share one namespace"
                ),
            ),
        )
        plan.failed_names |= _bound_names(stmt)
        return False
    plan.declared[name] = stmt
    return True


def _module_body_notes(stmt: ast.stmt) -> tuple[str, ...]:
    """Why this top-level statement is not one of the four supported forms."""
    kind = type(stmt).__name__
    base = f"`{kind}` is not a supported top-level statement"
    if isinstance(stmt, ast.AnnAssign):
        # A module constant carries its type in its constructor, so the
        # annotated form is redundant rather than merely unsupported. P5
        # names the plain-assignment form; Task 8 owns constant checking.
        return (base, "a module constant is written `NAME = U32(1)`; its type is the constructor")
    if isinstance(stmt, ast.Assign):
        return (base, "assign one name at a time")
    return (base,)


def _reject_statement(
    stmt: ast.stmt,
    path: str,
    plan: _ShapePlan,
    sink: Diagnostics,
    code: str,
    notes: tuple[str, ...],
    bound: set[str],
) -> None:
    """Refuse one top-level statement: diagnose it and do not execute it."""
    sink.error(code, Loc.from_node(path, stmt), _INTENT[code], help=_HELP[code], notes=notes)
    plan.failed_names |= bound


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _is_single_name_assign(stmt: ast.Assign) -> bool:
    return len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)


def _assign_target(stmt: ast.Assign) -> str:
    target = stmt.targets[0]
    assert isinstance(target, ast.Name)
    return target.id


def _check_import_from(
    stmt: ast.ImportFrom, path: str, plan: _ShapePlan, sink: Diagnostics
) -> None:
    bound = {alias.asname or alias.name for alias in stmt.names if alias.name != "*"}
    if stmt.level != 0 or stmt.module is None:
        _reject_statement(
            stmt,
            path,
            plan,
            sink,
            "SPT2005",
            ("a relative import has no meaning in a contract",),
            bound,
        )
        return
    if stmt.module == "__future__":
        names = [(alias.name, alias.asname) for alias in stmt.names]
        if names == [("annotations", None)]:
            plan.has_future_annotations = True
            plan.executable.append(stmt)
        else:
            _reject_statement(
                stmt,
                path,
                plan,
                sink,
                "SPT2005",
                ("`annotations` is the only supported `__future__` import (E4)",),
                bound,
            )
        return
    if stmt.module != "serpent":
        _reject_statement(
            stmt,
            path,
            plan,
            sink,
            "SPT2005",
            (f"`{stmt.module}` is not `serpent`",),
            bound,
        )
        return
    if any(alias.name == "*" for alias in stmt.names):
        # Error recovery: `from serpent import *` is refused, but every name it
        # WOULD have bound is registered anyway -- in `alias_map` so decorator
        # and `Event`-base recognition still work, and in `failed_names` so the
        # NameErrors that follow are recognized as cascades. Without this, one
        # star import produced a pile of misleading "undecorated class" and
        # "no @contract class" diagnostics on top of the one true error.
        for name in serpent.__all__:
            plan.alias_map.setdefault(name, name)
            plan.declared.setdefault(name, stmt)
        _reject_statement(
            stmt,
            path,
            plan,
            sink,
            "SPT2005",
            ("import the names you use explicitly: `from serpent import U32, contract, ...`",),
            set(serpent.__all__),
        )
        return
    unexported = [alias.name for alias in stmt.names if alias.name not in serpent.__all__]
    if unexported:
        _reject_statement(
            stmt,
            path,
            plan,
            sink,
            "SPT2005",
            (
                "not part of the serpent authoring surface (`serpent.__all__`): "
                + ", ".join(repr(name) for name in unexported),
            ),
            bound,
        )
        return
    for alias in stmt.names:
        local = alias.asname or alias.name
        plan.alias_map[local] = alias.name
        # `setdefault`, so re-importing the same name is not itself an error;
        # what matters is that a later *declaration* cannot reuse it.
        plan.declared.setdefault(local, stmt)
    plan.executable.append(stmt)


def _decorator_serpent_name(deco: ast.expr, plan: _ShapePlan) -> str | None:
    """The `serpent.__all__` name one class decorator applies, or `None`.

    Both spellings, because `@contractevent` has a FACTORY form
    (`@contractevent(topics=..., data_format=...)`, M1-E Task 5) and an
    authored contract has to be able to use it: a bare `ast.Name`, and an
    `ast.Call` on one. Only `contractevent` takes arguments; calling any other
    serpent decorator is a misuse this shape check deliberately lets through, so
    that the decorator's own `TypeError` (reported as an execution failure with
    its real message) is what the author reads, rather than "carries no serpent
    decorator", which would be false.
    """
    if isinstance(deco, ast.Call):
        deco = deco.func
    if not isinstance(deco, ast.Name):
        return None
    return plan.alias_map.get(deco.id)


def _check_class_def(stmt: ast.ClassDef, path: str, plan: _ShapePlan, sink: Diagnostics) -> None:
    kinds = [
        _DECORATOR_KINDS[serpent_name]
        for serpent_name in (_decorator_serpent_name(deco, plan) for deco in stmt.decorator_list)
        if serpent_name in _DECORATOR_KINDS
    ]
    if not kinds:
        # Naming what WAS found matters: the usual cause is a decorator that
        # was never imported from `serpent`, which reads nothing like
        # "undecorated" in the source.
        applied = [ast.unparse(deco) for deco in stmt.decorator_list]
        detail = (
            f"`{stmt.name}` carries no serpent decorator"
            if not applied
            else (
                f"`{stmt.name}` carries {', '.join('@' + name for name in applied)}, none of "
                "which is a serpent decorator imported from `serpent`"
            )
        )
        _reject_statement(stmt, path, plan, sink, "SPT4015", (detail,), {stmt.name})
        return
    if len(kinds) > 1:
        _reject_statement(
            stmt,
            path,
            plan,
            sink,
            "SPT4013",
            (f"`{stmt.name}` carries {len(kinds)} serpent decorators",),
            {stmt.name},
        )
        return
    kind = kinds[0]
    # SS B.1's ClassDef row: no base classes, except `Event` on an event
    # (D8) -- which `@contractevent` itself enforces, so a single wrong base
    # is left to it (and its more precise SPT4014). Only an EXTRA base, and
    # any base at all on a non-event, is refused here. This is also what
    # keeps the F.1.14 cross-check honest: `typing.get_type_hints` reports
    # INHERITED annotations, so a struct with a base could otherwise declare
    # fields the AST never shows.
    allowed_bases = 1 if kind == "event" else 0
    if len(stmt.bases) > allowed_bases or stmt.keywords:
        _reject_statement(
            stmt,
            path,
            plan,
            sink,
            "SPT4015",
            (
                (
                    f"`{stmt.name}` declares a base class or class keyword; a "
                    "serpent-decorated class has no bases (an event inherits `Event` "
                    "and nothing else)"
                ),
            ),
            {stmt.name},
        )
        return
    if not _check_class_body(stmt, kind, path, plan, sink):
        return
    plan.classes.append((stmt, kind))
    plan.executable.append(stmt)


#: Which declaration form each decorated class KIND admits in its body.
#: `field` is `name: T`, `annotated_value` is `name: T = v` (a struct field
#: default, or an annotated error-enum member), `assign` is `NAME = v`, and
#: `method` is `def`/`async def`. A docstring and `pass` are always allowed.
_ALLOWED_MEMBER_FORMS: dict[str, frozenset[str]] = {
    "contract": frozenset({"method"}),
    "struct": frozenset({"field", "annotated_value"}),
    "event": frozenset({"field", "annotated_value"}),
    "error_enum": frozenset({"assign", "annotated_value"}),
}

_BODY_HELP: dict[str, str] = {
    "contract": (
        "a @contract class body declares methods (`def name(self, env: Env) -> T`) and "
        "nothing else -- contract state lives in storage, not on the class"
    ),
    "struct": "a @contracttype class body declares fields as `name: T`",
    "event": "a @contractevent class body declares fields as `name: T`",
    "error_enum": "a @contracterror class body declares members as `NAME = errorcode(N)`",
}


def _member_form(member: ast.stmt) -> tuple[str, str] | None:
    """`(form, declared name)` for a class-body declaration, else `None`."""
    if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
        return "method", member.name
    if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
        return ("annotated_value" if member.value is not None else "field"), member.target.id
    if isinstance(member, ast.Assign) and _is_single_name_assign(member):
        return "assign", _assign_target(member)
    return None


def _check_class_body(
    stmt: ast.ClassDef, kind: str, path: str, plan: _ShapePlan, sink: Diagnostics
) -> bool:
    """Validate a decorated class body: kind-appropriate, uniquely named members.

    Two rules, both shape rules in their own right (SS C.3: "Contract class:
    method names only. There are no class attributes") and both also what
    keeps the F.1.14 cross-check unreachable from ordinary source:

    * **Kind-appropriate.** A member form the class's kind does not declare is
      recorded by exactly one of the two views. A field in a `@contract` class
      is invisible to `decorators.contract` but sits in the AST; `g = f` in a
      `@contract` class is the reverse -- `inspect.isfunction` accepts it, so
      the metadata gains a method the AST has no `def` for.
    * **Uniquely named.** A duplicate field, error-enum member or method
      leaves the executed view holding ONE binding (the last) while the AST
      view has two, so the inventories cannot line up. It is a real authoring
      bug too: the second declaration silently replaces the first.

    Both were reachable from valid Python and raised `CompilerBugError` before
    this check existed.
    """
    allowed = _ALLOWED_MEMBER_FORMS[kind]
    declared: dict[str, ast.stmt] = {}
    valid = True
    for index, member in enumerate(stmt.body):
        if (index == 0 and _is_docstring(member)) or isinstance(member, ast.Pass):
            continue
        form = _member_form(member)
        if form is None or form[0] not in allowed:
            sink.error(
                "SPT4020",
                Loc.from_node(path, member),
                _INTENT["SPT4020"],
                help=_BODY_HELP[kind],
                notes=(
                    (
                        f"`{type(member).__name__}` declares nothing"
                        if form is None
                        else f"`{type(member).__name__}` is not how a {kind} declares a member"
                    ),
                ),
            )
            valid = False
            continue
        name = form[1]
        previous = declared.get(name)
        if previous is not None:
            sink.error(
                "SPT2004",
                Loc.from_node(path, member),
                _INTENT["SPT2004"],
                help=_HELP["SPT2004"],
                notes=(
                    (
                        f"`{stmt.name}.{name}` is already declared at line "
                        f"{previous.lineno}; the later declaration would silently "
                        "replace it"
                    ),
                ),
            )
            valid = False
            continue
        declared[name] = member
    if not valid:
        plan.failed_names.add(stmt.name)
    return valid


def _check_contract_count(plan: _ShapePlan, path: str, sink: Diagnostics) -> None:
    """Exactly one `@contract` class per module (SS C.3, a module-scope fact).

    Reported under `SPT4019`, the row added for exactly this fact in Task 3's
    review round, so the message contains its own registry intent. Zero is a
    `WHOLE_FILE` location (there is no node to point at); each class after
    the first gets its own `Loc`.
    """
    contracts = [node for node, kind in plan.classes if kind == "contract"]
    intent = _INTENT["SPT4019"]
    if not contracts:
        sink.error(
            "SPT4019",
            Loc.whole_file(path),
            f"{intent}; this module declares none",
            help=_HELP["SPT4019"],
        )
        return
    plan.contract_node = contracts[0]
    for extra in contracts[1:]:
        sink.error(
            "SPT4019",
            Loc.from_node(path, extra),
            f"{intent}; this is an extra one",
            help=_HELP["SPT4019"],
            notes=(
                (
                    f"the first @contract class is `{contracts[0].name}` on line "
                    f"{contracts[0].lineno}"
                ),
            ),
        )


# --- statement-wise execution + bridging (MJ-4) -----------------------------


def _execute(plan: _ShapePlan, path: str, sink: Diagnostics) -> tuple[dict[str, Any], set[int]]:
    """Execute the shape-approved statements, one at a time.

    Returns the module namespace and the `id()`s of statements that failed,
    so the inventories skip declarations that never came into existence.
    """
    module_name = f"_serpent_contract_{next(_MODULE_COUNTER)}"
    module = types.ModuleType(module_name)
    namespace: dict[str, Any] = module.__dict__
    namespace["__builtins__"] = builtins
    namespace["__file__"] = path

    failed: set[int] = set()
    failed_names = set(plan.failed_names)
    flags = _FUTURE_ANNOTATIONS_FLAG if plan.has_future_annotations else 0

    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        for stmt in plan.executable:
            try:
                # `compile()` is INSIDE the try: `ast.parse` accepts a whole
                # class of source that `compile` then rejects in its symtable
                # and codegen phases -- `continue` outside a loop, `nonlocal`
                # with no binding, a duplicate parameter name, `yield` outside
                # a function, `from serpent import *` inside a function,
                # assigning to `__debug__`. Those SyntaxErrors carry their own
                # precise line, so they bridge through the same helper the
                # parse-time path uses.
                code_object = compile(
                    ast.Module(body=[stmt], type_ignores=[]),
                    path,
                    "exec",
                    flags=flags,
                    dont_inherit=True,
                )
                exec(code_object, namespace)  # noqa: S102 -- E1: the hybrid design
            except SyntaxError as exc:
                failed.add(id(stmt))
                failed_names |= _bound_names(stmt)
                _report_syntax_error(exc, path, sink, fallback=Loc.from_node(path, stmt))
            except _BRIDGED_EXCEPTIONS as exc:
                # The cascade test runs BEFORE this statement's own names are
                # recorded: `LIMIT = LIMIT` raises a NameError for a name it
                # would itself have bound, and must be reported, not swallowed.
                cascade = _is_cascade(exc, failed_names)
                failed.add(id(stmt))
                failed_names |= _bound_names(stmt)
                if not cascade:
                    _bridge(stmt, exc, path, sink)
            # `SystemExit` is a BaseException, so `Exception` alone misses it:
            # `X = exit()` at module level would otherwise tear down the
            # compiler instead of producing a diagnostic.
            except (Exception, SystemExit) as exc:  # noqa: BLE001 -- see above
                failed.add(id(stmt))
                failed_names |= _bound_names(stmt)
                _bridge(stmt, exc, path, sink)
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
    return namespace, failed


def _is_cascade(exc: BaseException, failed_names: set[str]) -> bool:
    """A `NameError` for a name an earlier failed statement should have bound.

    Reporting it would only add noise on top of the real error.
    """
    return isinstance(exc, NameError) and getattr(exc, "name", None) in failed_names


def _bound_names(stmt: ast.stmt) -> set[str]:
    """The module-level names `stmt` would have bound had it succeeded."""
    if isinstance(stmt, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
        return {stmt.name}
    if isinstance(stmt, ast.Assign):
        return {t.id for t in stmt.targets if isinstance(t, ast.Name)}
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return {stmt.target.id}
    if isinstance(stmt, ast.ImportFrom | ast.Import):
        return {alias.asname or alias.name for alias in stmt.names if alias.name != "*"}
    return set()


def _bridge(stmt: ast.stmt, exc: BaseException, path: str, sink: Diagnostics) -> None:
    """Re-report one exec-time exception as a located diagnostic."""
    code, loc, qualifier = _classify(stmt, exc, path)
    intent = _INTENT[code]
    message = f"{qualifier}: {intent}" if qualifier else intent
    sink.error(
        code,
        loc,
        message,
        help=_HELP.get(code),
        notes=(f"{type(exc).__name__}: {exc}",),
    )


def _classify(stmt: ast.stmt, exc: BaseException, path: str) -> tuple[str, Loc, str]:
    """Pick the code, the location and the `Cls.member` qualifier.

    Location narrowing, in order: the `Cls.member:` prefix decorators.py puts
    on every declaration-site message; the unresolvable-name refinement (the
    only message shape that names something other than the offending member);
    the `errorcode(<non-int>)` AST refinement (its `TypeError` names nothing);
    a unique class-body member name mentioned anywhere in the message. If none
    apply, the failing statement's own `Loc` stands -- never `WHOLE_FILE`.
    """
    rule = _match_rule(exc)
    code = rule.code if rule is not None else _FALLBACK_CODE
    refine = rule.refine if rule is not None else ""
    members = _class_members(stmt) if isinstance(stmt, ast.ClassDef) else {}
    message = str(exc)
    qualifier = ""
    target: ast.stmt | None = None

    match = _QUALIFIER_RE.match(message)
    if match is not None and isinstance(stmt, ast.ClassDef) and match.group("cls") == stmt.name:
        qualifier = f"{match.group('cls')}.{match.group('member')}"
        target = members.get(match.group("member"))

    missing = _missing_name(exc)
    if missing is not None:
        found, in_annotation = _find_missing_name(stmt, missing)
        if in_annotation and code == "SPT2001":
            # PEP 563 parity: whether the unresolvable name surfaced as a raw
            # NameError (annotations evaluated eagerly) or as
            # `_annotations_of`'s wrapped ValueError (PEP 563), the author
            # gets the same code on the same statement.
            code = "SPT2003"
        if target is None:
            target = found

    if target is None and refine == _REFINE_ERRORCODE:
        target = _find_non_int_errorcode(stmt)

    if target is None and members:
        mentioned = [
            node for name, node in members.items() if re.search(rf"\b{re.escape(name)}\b", message)
        ]
        if len(mentioned) == 1:
            target = mentioned[0]

    return code, Loc.from_node(path, target if target is not None else stmt), qualifier


def _match_rule(exc: BaseException) -> _BridgeRule | None:
    message = str(exc)
    for rule in _BRIDGE_RULES:
        if isinstance(exc, rule.exc_types) and rule.needle in message:
            return rule
    return None


def _class_members(stmt: ast.ClassDef) -> dict[str, ast.stmt]:
    """Class-body statements that declare a name, in declaration order."""
    members: dict[str, ast.stmt] = {}
    for member in stmt.body:
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
            members.setdefault(member.name, member)
        elif isinstance(member, ast.Assign):
            for target in member.targets:
                if isinstance(target, ast.Name):
                    members.setdefault(target.id, member)
        elif isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
            members.setdefault(member.target.id, member)
    return members


def _missing_name(exc: BaseException) -> str | None:
    if isinstance(exc, NameError):
        name = getattr(exc, "name", None)
        if isinstance(name, str):
            return name
    match = _MISSING_NAME_RE.search(str(exc))
    return None if match is None else match.group("name")


def _annotation_nodes(stmt: ast.stmt) -> list[ast.expr]:
    """Every annotation subtree `stmt` carries at its own level."""
    if isinstance(stmt, ast.AnnAssign):
        return [stmt.annotation]
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
        args = stmt.args
        nodes = [
            arg.annotation
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg)
            if arg is not None and arg.annotation is not None
        ]
        if stmt.returns is not None:
            nodes.append(stmt.returns)
        return nodes
    return []


def _find_missing_name(stmt: ast.stmt, name: str) -> tuple[ast.stmt | None, bool]:
    """Locate the statement referencing `name`, and say whether it is an annotation.

    A class body is searched member by member so the diagnostic lands on the
    offending field or method rather than the whole class.
    """
    candidates: Sequence[ast.stmt] = stmt.body if isinstance(stmt, ast.ClassDef) else [stmt]
    in_annotation = [
        member
        for member in candidates
        if any(_references(node, name) for node in _annotation_nodes(member))
    ]
    elsewhere = [
        member
        for member in candidates
        if _references(member, name) and not any(member is hit for hit in in_annotation)
    ]
    if in_annotation and not elsewhere:
        return (in_annotation[0] if len(in_annotation) == 1 else None), True
    if len(elsewhere) == 1 and not in_annotation:
        return elsewhere[0], False
    return None, bool(in_annotation)


def _references(node: ast.AST, name: str) -> bool:
    return any(isinstance(child, ast.Name) and child.id == name for child in ast.walk(node))


def _find_non_int_errorcode(stmt: ast.ClassDef | ast.stmt) -> ast.stmt | None:
    """The single `NAME = errorcode(<not an int literal>)` member, if unique.

    `errorcode()`'s `TypeError` comes from the class body and names nothing,
    so the offending member is found structurally instead.
    """
    if not isinstance(stmt, ast.ClassDef):
        return None
    offenders = [
        member
        for member in stmt.body
        if isinstance(member, ast.Assign | ast.AnnAssign)
        and _is_errorcode_call(member.value)
        and _int_literal_arg(member.value) is None
    ]
    return offenders[0] if len(offenders) == 1 else None


def _is_errorcode_call(value: ast.expr | None) -> bool:
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "errorcode"
    )


def _int_literal_arg(value: ast.expr | None) -> int | None:
    """The int literal `errorcode(N)` declares, or `None` if it is not one."""
    if not isinstance(value, ast.Call) or len(value.args) != 1 or value.keywords:
        return None
    arg = value.args[0]
    if (
        isinstance(arg, ast.Constant)
        and isinstance(arg.value, int)
        and not isinstance(arg.value, bool)
    ):
        return arg.value
    return None


# --- inventories ------------------------------------------------------------


def _collect_inventories(
    plan: _ShapePlan, namespace: dict[str, Any], failed: set[int]
) -> tuple[DecoratedDecl | None, list[DecoratedDecl], list[DecoratedDecl]]:
    contract_decl: DecoratedDecl | None = None
    decorated_types: list[DecoratedDecl] = []
    events: list[DecoratedDecl] = []
    for node, kind in plan.classes:
        if id(node) in failed:
            continue
        cls = namespace.get(node.name)
        if not isinstance(cls, type):
            continue
        metadata = vars(cls).get(_METADATA_ATTR)
        if not isinstance(metadata, dict):
            continue
        decl = DecoratedDecl(name=node.name, kind=kind, cls=cls, node=node, metadata=metadata)
        if kind == "contract":
            if contract_decl is None and node is plan.contract_node:
                contract_decl = decl
        elif kind == "event":
            events.append(decl)
        else:
            decorated_types.append(decl)
    return contract_decl, decorated_types, events


# --- the inventory cross-check (F.1.14) ------------------------------------


def _cross_check_inventory(decls: Iterable[DecoratedDecl]) -> None:
    """Assert the `_serpent_type_` view and the AST view agree exactly.

    A skew here means the compiler would emit a `contractspecv0` that does not
    describe the code it compiled -- the silent divergence F.1.14 names. It is
    a COMPILER BUG, never a user diagnostic, so it fails hard.
    """
    for decl in decls:
        declared_kind = decl.metadata.get("kind")
        if declared_kind != decl.kind:
            _bug(decl, "the declared kind", decl.kind, declared_kind)
        if decl.kind == "contract":
            _cross_check_contract(decl)
        elif decl.kind == "error_enum":
            _cross_check_error_enum(decl)
        else:
            _cross_check_fields(decl)


def _cross_check_contract(decl: DecoratedDecl) -> None:
    # The AST side mirrors `decorators.contract`'s own filter exactly: public
    # (or `__init__`) names whose executed attribute is still a plain
    # function. `AsyncFunctionDef` is included because `inspect.isfunction`
    # accepts an async function, so the decorator records it (Task 6 is what
    # rejects `async` bodies); a method wrapped in something that is no longer
    # a function -- `@property`, say -- is recorded by neither view. Mirroring
    # rather than re-deriving is what keeps this invariant about *compiler*
    # skew instead of firing on authoring shapes.
    attributes = vars(decl.cls)
    ast_methods = [
        member
        for member in decl.node.body
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
        and (not member.name.startswith("_") or member.name == "__init__")
        and inspect.isfunction(attributes.get(member.name))
    ]
    metadata_methods: list[tuple[str, list[tuple[str, object]], object]] = list(
        decl.metadata["methods"]
    )
    if [name for name, _params, _ret in metadata_methods] != [f.name for f in ast_methods]:
        _bug(
            decl,
            "the exported method inventory",
            [name for name, _p, _r in metadata_methods],
            [f.name for f in ast_methods],
        )
    for (name, params, _ret), node in zip(metadata_methods, ast_methods, strict=True):
        args = node.args
        # `self` is dropped on both sides; `env` is kept on both, so the
        # comparison is the strictest one available (B9 drops `env` only at
        # spec-emission time).
        ast_params = [arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)][1:]
        if [param for param, _ty in params] != ast_params:
            _bug(decl, f"the parameters of {name!r}", [p for p, _t in params], ast_params)


def _cross_check_fields(decl: DecoratedDecl) -> None:
    ast_fields = [
        member.target.id
        for member in decl.node.body
        if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name)
    ]
    metadata_fields = [name for name, _annotation in decl.metadata["fields"]]
    if metadata_fields != ast_fields:
        _bug(decl, "the field inventory", metadata_fields, ast_fields)


def _cross_check_error_enum(decl: DecoratedDecl) -> None:
    ast_cases: list[tuple[str, int | None]] = []
    for member in decl.node.body:
        if isinstance(member, ast.Assign) and _is_single_name_assign(member):
            name = _assign_target(member)
        elif (
            isinstance(member, ast.AnnAssign)
            and isinstance(member.target, ast.Name)
            and member.value is not None
        ):
            name = member.target.id
        else:
            continue
        if name.startswith("_"):
            continue
        ast_cases.append((name, _int_literal_arg(member.value)))

    metadata_cases: list[tuple[str, int]] = list(decl.metadata["cases"])
    if [name for name, _code in metadata_cases] != [name for name, _code in ast_cases]:
        _bug(
            decl,
            "the error-case inventory",
            [name for name, _c in metadata_cases],
            [name for name, _c in ast_cases],
        )
    for (name, code), (_ast_name, ast_code) in zip(metadata_cases, ast_cases, strict=True):
        # `ast_code is None` means the code is not an int literal (e.g.
        # `errorcode(SOME_CONST)`); the executed view is authoritative there.
        if ast_code is not None and ast_code != code:
            _bug(decl, f"the code of case {name!r}", code, ast_code)


def _bug(decl: DecoratedDecl, what: str, metadata_view: object, ast_view: object) -> NoReturn:
    raise CompilerBugError(
        f"compiler bug: {decl.kind} {decl.name!r} disagrees on {what} between the "
        f"_serpent_type_ metadata view and the AST view "
        f"(metadata: {metadata_view!r}, AST: {ast_view!r}). The hybrid frontend (E1) "
        "requires the two views to match exactly (F.1.14); this is a defect in "
        "serpent.compiler.loader, not in the contract."
    )
