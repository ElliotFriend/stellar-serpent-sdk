"""Pre-emptive spec/XDR limit validation (Task 9, the SPT5xxx band).

`spec.sections.build_spec_entries` enforces a handful of caps at EMISSION
time, as bare `SpecNameError`/`SpecDocError`s that name a declaration but
carry no source location (dossier B.3, sections.py's own module docstring):
a struct/error-enum's own class name and an error case's name (never checked
by `decorators.py` at all), every doc (`decorators.py` checks none), and
every function/field/param name (`decorators.py` checks method and field
names, but -- B11's named gap -- never `__init__`'s emitted `__constructor`
name and never ANY parameter name, of an export or of the constructor). On
top of that, S23's wasm export-arity cap (at most 32 parameters) is not
checked ANYWHERE else in `src/serpent` today; nothing would fail until the
host tried to instantiate the module.

`validate_limits` re-runs all of that here, against `loaded`'s two views, so
every violation becomes a located `SPT5xxx` diagnostic instead of a bare
`ValueError`/`SpecNameError` surfacing much later out of `sections.py` with
no line number at all. It runs entirely off what `loader.load_module` already
produced -- no re-exec, no re-derivation of a `Ty` (nothing here needs one:
every check is a name, a doc string or a count).

## Why a name `decorators.py` already guarantees is checked again here

An exported method's own name and a struct/event field name ARE already
validated by `decorators.py` at class-decoration time. This module checks
export/type names again anyway (mirroring `sections.py`'s own "sections must
not trust its input" posture -- see `tests/unit/test_sections.py`'s guard
tests for the same rows). It costs nothing: real source can never reach a
violation through this path. The loader compiles and execs one top-level
statement at a time (MJ-4), so if `decorators.py` rejects a name, the WHOLE
class statement fails to execute -- the class carries no `_serpent_type_`
metadata and is therefore absent from `loaded.contract_decl` /
`loaded.decorated_types_in_order` /`loaded.events` altogether (F.1.14). A
struct/event FIELD name is consequently never re-checked here at all, and the
asymmetry with an export/type name is not incidental: this module already
visits every export/type declaration for an independent reason (building its
`Loc`, checking its length), so a charset check there is one more branch on
code already running, whereas a field has no other reason for this module to
visit it at all -- adding a field-name walk would be a wholly new path that,
by the same MJ-4 argument, could never observe a failure (untestable dead
code, not defense in depth).

## SPT5002/SPT5003 for M1-E2's two kinds (plan-review B1)

The case-name cap for a `@contractunion`/`@contractenum` lands HERE, not in
`decorators.py`, for the MJ-4/F.1.14 reason above and for one more: ruling E8
gives the two kinds DIFFERENT caps (32 for a union variant, which becomes a
runtime `Symbol` -- `val.SCSYMBOL_LIMIT`, S9 -- and 60 for an int-enum case,
which never does), and `decorators._check_name` knows only the 30-character
spec-name tier, so routing either through it would refuse a 40-character
int-enum case name the ruling makes legal. `_CASE_RULES` is that per-kind
table; `SPT5003`'s registry wording was widened to match, and its help text is
now per kind too (it used to hardcode "give the error case a name of at most 60
characters", which is wrong for a variant in both halves). `_check_type_name`
needed no edit at all: it already visits every `decorated_types_in_order`
entry, so the two new `loader._DECORATOR_KINDS` rows are what make the SPT5002
widening true.

## SPT5002/SPT5003 (Task 9 fix round 1: charset added)

`SPT5002` (type name) and `SPT5003` (error-case name) originally checked
LENGTH only. Controller review (fix round 1) caught the gap this left: a
struct/error-enum/error-case name can be a non-ASCII Python identifier (e.g.
`café`, valid Python, invalid `val.SYMBOL_CHARS`) that is well within the
60-byte cap and therefore accepted here, only for `sections._check_name` to
raise `SpecNameError` for the SAME name at emission -- exactly the
no-location failure this whole module exists to pre-empt. Both codes now
check length THEN charset, mirroring `SPT5001`'s own order exactly (an
over-long name is a length problem, not a charset one) via the same shared
`_check_symbol_name` helper D10 already governs for the 30-character tier;
the registry's `message_intent` for both was widened to match (a sanctioned
wording edit, no renumber -- `codes.py`'s only change for this fix round).

## Events are out of scope

`spec_types` (what `build_spec_entries(..., types=...)`) ever receives never
includes an event class (MJ-9); `sections._declared_type_entry` refuses
every event unconditionally, regardless of name or doc length. There is
therefore no limit here for an event class to violate that would change
whether `sections.py` raises -- events are not checked.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from typing import Final, NamedTuple

from serpent.compiler import codes
from serpent.compiler.diagnostics import Diagnostics, Loc, LocKind
from serpent.compiler.loader import DecoratedDecl, LoadedModule
from serpent.decorators import NAME_LIMIT
from serpent.env import Env
from serpent.spec.sections import (
    CASE_NAME_LIMIT,
    CONSTRUCTOR_NAME,
    DOC_LIMIT,
    TYPE_NAME_LIMIT,
    _class_doc,
    _own_doc,
)
from serpent.val import SCSYMBOL_LIMIT, SYMBOL_CHARS

__all__ = ["EXPORT_PARAM_LIMIT", "validate_limits"]

#: `code -> message_intent`, so every diagnostic carries its registry row's
#: own wording (the convention every `serpent.compiler` module follows).
_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

#: S23 (dossier, spec §13): every wasm export -- every method
#: `contractspecv0` records, `__constructor` included -- carries at most 32
#: parameters. Unlike `NAME_LIMIT`/`TYPE_NAME_LIMIT`/`CASE_NAME_LIMIT`/
#: `DOC_LIMIT`, this is NOT a promotion of an existing literal: no XDR array
#: caps `SCSpecFunctionInputV0` at 32 (an XDR-encoded input list could carry
#: far more), so nothing in `sections.py`/`typemap.py` enforces this today --
#: the real cap is the host's wasm-export-arity limit, which is why this
#: check is new rather than pre-empting an existing `sections.py` raise.
EXPORT_PARAM_LIMIT: Final = 32

_NAME_HELP = f"use at most {NAME_LIMIT} characters from [a-zA-Z0-9_]"
_TYPE_NAME_HELP = f"give the type a name of at most {TYPE_NAME_LIMIT} characters"
_DOC_HELP = f"shorten the docstring to at most {DOC_LIMIT} encoded bytes"
_PARAM_COUNT_HELP = f"an exported method may take at most {EXPORT_PARAM_LIMIT} parameters"


class _CaseRule(NamedTuple):
    """How SPT5003 reads one case-bearing kind: its label, its cap, its help.

    Three rows rather than one because the CAP genuinely differs (ruling E8): a
    union variant name becomes a runtime `Symbol`, an error-enum or int-enum
    case name never does.
    """

    what: str
    limit: int
    help_text: str


#: `decl.kind -> _CaseRule`, and simultaneously the gate: a kind absent here
#: declares no cases at all (a struct, an event, the contract class).
_CASE_RULES: Final[Mapping[str, _CaseRule]] = {
    "error_enum": _CaseRule(
        "error case",
        CASE_NAME_LIMIT,
        f"give the error case a name of at most {CASE_NAME_LIMIT} characters",
    ),
    "union": _CaseRule(
        "union variant",
        SCSYMBOL_LIMIT,
        f"give the variant a name of at most {SCSYMBOL_LIMIT} characters -- a variant name "
        "becomes a runtime Symbol",
    ),
    "enum": _CaseRule(
        "int-enum case",
        CASE_NAME_LIMIT,
        f"give the int-enum case a name of at most {CASE_NAME_LIMIT} characters",
    ),
}


# --- public entry point ------------------------------------------------------


def validate_limits(loaded: LoadedModule, sink: Diagnostics) -> None:
    """Check every spec/XDR limit `loaded` could violate, located (SPT5xxx).

    Never raises for bad source (E16, the sink convention every
    `serpent.compiler` checker follows): a violation is reported into `sink`
    and checking continues, so a caller sees every limit problem in one pass
    rather than one `SpecNameError` at a time.
    """
    if loaded.contract_decl is not None:
        _check_contract(loaded.contract_decl, loaded, sink)
    for decl in loaded.decorated_types_in_order:
        _check_type_name(decl, loaded, sink)
        _check_doc(_class_doc(decl.cls), Loc.from_node(loaded.path, decl.node), decl.name, sink)
        if decl.kind in _CASE_RULES:
            _check_cases(decl, loaded, sink)


# --- the contract: __constructor and every export -----------------------------


def _check_contract(decl: DecoratedDecl, loaded: LoadedModule, sink: Diagnostics) -> None:
    """Every export `decorators.contract` recorded, `__constructor` included.

    `decl.metadata["methods"]` already excludes every private method
    (`decorators.contract` skips a `_`-prefixed name outright, `__init__`
    excepted) -- exactly the set that matters here, since a private method
    never reaches `contractspecv0` at all.
    """
    contract_cls = decl.cls
    node_map = _method_nodes(decl.node)
    class_loc = Loc.from_node(loaded.path, decl.node)
    methods: Sequence[tuple[str, list[tuple[str, object]], object]] = decl.metadata["methods"]
    for name, params, _returns in methods:
        emitted = CONSTRUCTOR_NAME if name == "__init__" else name
        node = node_map.get(name)
        fn_loc = Loc.from_node(loaded.path, node) if node is not None else class_loc
        _check_symbol_name(
            emitted,
            fn_loc,
            f"`{emitted}`",
            sink,
            code="SPT5001",
            limit=NAME_LIMIT,
            help_text=_NAME_HELP,
        )
        _check_doc(_own_doc(vars(contract_cls).get(name)), fn_loc, emitted, sink)

        real_params = _drop_env(params)
        if len(real_params) > EXPORT_PARAM_LIMIT:
            sink.error(
                "SPT5005",
                fn_loc,
                f"{_INTENT['SPT5005']}: `{emitted}` takes {len(real_params)} parameters",
                help=_PARAM_COUNT_HELP,
            )
        for param_name, _annotation in real_params:
            param_loc = _param_loc(loaded.path, node, param_name) if node is not None else fn_loc
            _check_symbol_name(
                param_name,
                param_loc,
                f"parameter `{param_name}` of `{emitted}`",
                sink,
                code="SPT5001",
                limit=NAME_LIMIT,
                help_text=_NAME_HELP,
            )


def _drop_env(params: Sequence[tuple[str, object]]) -> list[tuple[str, object]]:
    """`params`, minus a leading `env: Env` (SS C.3) -- what actually lands in
    `contractspecv0`'s `inputs` (`sections._function_entry`'s own rule)."""
    if params and params[0][1] is Env:
        return list(params[1:])
    return list(params)


# --- declared type names, docs, and case names --------------------------------


def _check_type_name(decl: DecoratedDecl, loaded: LoadedModule, sink: Diagnostics) -> None:
    _check_symbol_name(
        decl.name,
        Loc.from_node(loaded.path, decl.node),
        f"type `{decl.name}`",
        sink,
        code="SPT5002",
        limit=TYPE_NAME_LIMIT,
        help_text=_TYPE_NAME_HELP,
    )


def _check_cases(decl: DecoratedDecl, loaded: LoadedModule, sink: Diagnostics) -> None:
    """Every case name of a case-bearing kind, against ITS cap (plan-review B1).

    The second element of a case is deliberately unused and deliberately
    `object`: an error enum and an int enum carry a discriminant there, a union
    carries its payload-annotation tuple, and widening the annotation is honest
    where a `cast` would lie. What all three share is the 2-tuple SHAPE, which
    is why one loop serves them.
    """
    rule = _CASE_RULES[decl.kind]
    cases: Sequence[tuple[str, object]] = decl.metadata["cases"]
    for name, _value in cases:
        _check_symbol_name(
            name,
            _member_loc(loaded.path, decl.node, name),
            f"{rule.what} `{name}`",
            sink,
            code="SPT5003",
            limit=rule.limit,
            help_text=rule.help_text,
        )


# --- shared checks --------------------------------------------------------------


def _check_symbol_name(
    name: str, loc: Loc, what: str, sink: Diagnostics, *, code: str, limit: int, help_text: str
) -> None:
    """Length (encoded UTF-8 bytes, matching `sections._check_name`) first,
    then the Symbol charset -- mutually exclusive, matching both
    `decorators._check_name` and `sections._check_name`'s own "length first"
    ordering (an over-long name is a length problem, not a charset one).

    Shared by `SPT5001` (function/field/param, D10's 30-character tier) and,
    since Task 9 fix round 1, `SPT5002`/`SPT5003` (type/case name, the
    60-character tier): `val.SYMBOL_CHARS` is the SAME charset authority at
    every tier -- only the length cap and the diagnostic code differ, which
    is exactly what `code`/`limit`/`help_text` parameterize.
    """
    encoded = len(name.encode("utf-8"))
    if encoded > limit:
        sink.error(
            code,
            loc,
            f"{_INTENT[code]}: {what} is {encoded} bytes (max {limit})",
            help=help_text,
        )
        return
    if any(char not in SYMBOL_CHARS for char in name):
        sink.error(
            code,
            loc,
            f"{_INTENT[code]}: {what} uses a character outside [a-zA-Z0-9_]",
            help=help_text,
        )


def _check_doc(text: str, loc: Loc, label: str, sink: Diagnostics) -> None:
    """`SPT5004`: counted in ENCODED bytes (B12), matching
    `sections._doc_bytes` exactly -- a multibyte character can push a
    docstring over the limit well before its character count does."""
    encoded = len(text.encode("utf-8"))
    if encoded > DOC_LIMIT:
        sink.error(
            "SPT5004",
            loc,
            f"{_INTENT['SPT5004']}: `{label}` docstring is {encoded} bytes (max {DOC_LIMIT})",
            help=_DOC_HELP,
        )


# --- AST location lookups ------------------------------------------------------


def _method_nodes(node: ast.ClassDef) -> Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Python name -> `def` node, for every method in a class body."""
    return {
        member.name: member
        for member in node.body
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _param_loc(path: str, node: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> Loc:
    """The `Loc` of one parameter in `node`, or the function's own `Loc` if
    `name` is not found there (never fabricated, P2 -- a real span is always
    better than `WHOLE_FILE`, and the function's own span is real)."""
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
    return Loc.from_node(path, node)


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
