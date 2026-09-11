"""Bridging completeness (Task 11b, dossier F.2.12).

`tests/unit/test_loader.py` (Task 3) already proves every individual
exec-time-bridge and module-shape §B.3 check produces a located diagnostic;
its coverage is thorough but spread across ~20 separate test functions, so
the COMPLETENESS of that coverage -- "every check the loader's exec-time
bridge (`loader._BRIDGE_RULES`) and module-shape validator can raise is
exercised" -- is not visible in any one place. This module is that one
place: `_ROWS` lists every `loader._BRIDGE_RULES` needle and every
module-shape check by name (plus `SyntaxError`, which is neither), each row
compiles a minimal module through the FULL `compile_module` pipeline (not
just `load_module`) and asserts a LOCATED diagnostic with the row's code --
proving no raw traceback escapes end-to-end -- and
`test_every_bridge_rule_needle_has_a_row` pins the row set against
`loader._BRIDGE_RULES` itself, so a bridge rule added later with no matching
row here fails loudly instead of silently losing coverage.

This module does NOT cover the rest of dossier §B.3: the `spec/sections.py`
limits (type/case name length, docstring length) and the `__constructor`/
parameter-name checks `Diagnostics`/`limits.py` pre-empt at Task 9 are a
different check family, at a different compile phase, with their own
`tests/must_reject/limits/` fixtures -- not `loader.py` exec-time bridging,
and out of scope here.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import re
import textwrap
from types import ModuleType

import pytest

from serpent.compiler import compile_module, loader
from serpent.compiler.diagnostics import CompileError, LocKind

PATH = "contract.py"

_IMPORTS = (
    "from serpent import ("
    "Annotated, ContractEnum, ContractUnion, Env, Event, U32, contract, contractenum, "
    "contracterror, contractevent, contracttype, contractunion, enumvalue, errorcode, "
    "topic, variant"
    ")"
)

_MIN_CONTRACT = """
@contract
class C:
    def go(self, env: Env) -> U32:
        return U32(0)
"""


def _source(body: str, *, with_contract: bool = True) -> str:
    parts = [_IMPORTS, "", body.strip("\n")]
    if with_contract:
        parts.append(_MIN_CONTRACT.strip("\n"))
    return "\n".join(parts) + "\n"


_HERE_RE = re.compile(r"#\s*HERE\b")


def _here_line(src: str) -> int:
    for lineno, line in enumerate(src.splitlines(), start=1):
        if _HERE_RE.search(line):
            return lineno
    raise AssertionError("row source has no `# HERE` marker")


#: Row ids whose body already declares its own @contract class(es) and must
#: NOT have `_MIN_CONTRACT` appended (it would add an unrelated diagnostic --
#: an extra @contract class of its own -- to a row that is precisely about
#: counting @contract classes).
_NO_AUTO_CONTRACT: frozenset[str] = frozenset({"module_extra_contract_class"})

#: One row per §B.3 check: `(id, code, needle substring from the check's own
#: raised exception -- "" for a check this row does not exercise via
#: `loader._BRIDGE_RULES`, module body). The needle is what
#: `test_every_bridge_rule_needle_has_a_row` cross-checks against
#: `loader._BRIDGE_RULES`, so every exec-time bridge row is provably present.
_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "errorcode_non_int_arg",
        "SPT4008",
        "errorcode() takes an int code",
        '@contracterror\nclass E:\n    Bad = errorcode("7")  # HERE',
    ),
    (
        "error_member_bare_int",
        "SPT4008",
        "@contracterror members must be declared",
        "@contracterror\nclass E:\n    Bad = 1  # HERE",
    ),
    (
        "error_code_out_of_range",
        "SPT4009",
        "is out of range -- contract codes are",
        "@contracterror\nclass E:\n    Bad = errorcode(0xFFFFFF00)  # HERE",
    ),
    (
        "error_code_duplicate",
        "SPT4010",
        "is already used by",
        "@contracterror\nclass E:\n    A = errorcode(1)\n    B = errorcode(1)  # HERE",
    ),
    (
        "error_enum_empty",
        "SPT4011",
        "@contracterror needs at least one member",
        "@contracterror\nclass E:  # HERE\n    pass",
    ),
    (
        "struct_field_non_chain_type",
        "SPT4012",
        "is not a chain type, a `@contracttype` struct",
        "@contracttype\nclass S:\n    x: int  # HERE",
    ),
    (
        # The exec-time needle for this code ("already declared as a
        # serpent ...", `decorators._reject_redecoration`) is dominated by
        # the module-shape check below it on every real-source path: two
        # serpent decorators on one `ClassDef` are refused SYNTACTICALLY
        # (`loader._check_class_def`'s `len(kinds) > 1`) before the class
        # ever executes, and a manual re-application (`S = contracttype(S)`)
        # is itself refused as a module-level redeclaration (`SPT2004`,
        # `loader._claim_name`) before the assignment executes either --
        # verified empirically for task-11b-report.md. This row therefore
        # exercises SPT4013 through the shape check, which is the only
        # reachable path; the needle column is left blank on purpose.
        "class_two_serpent_decorators",
        "SPT4013",
        "",
        "@contracttype\n@contracterror\nclass Both:  # HERE\n    x: U32",
    ),
    (
        "event_not_inheriting_event",
        "SPT4014",
        "@contractevent classes must inherit",
        "@contractevent\nclass T:  # HERE\n    x: U32",
    ),
    (
        "staticmethod_export",
        "SPT4007",
        "contract methods are plain methods taking",
        (
            "@contract\nclass D:\n    @staticmethod\n    def go(env: Env) -> U32:  # HERE\n"
            "        return U32(0)"
        ),
    ),
    (
        "self_not_first",
        "SPT4001",
        "contract methods take `self` as their first",
        "@contract\nclass D:\n    def go(env: Env) -> U32:  # HERE\n        return U32(0)",
    ),
    (
        "export_var_positional",
        "SPT4002",
        "is not allowed -- a contract export has a fixed arity",
        (
            "@contract\nclass D:\n    def go(self, env: Env, *rest: U32) -> U32:  # HERE\n"
            "        return U32(0)"
        ),
    ),
    (
        "export_var_keyword",
        "SPT4002",
        "is not allowed -- the host invokes exports positionally",
        (
            "@contract\nclass D:\n    def go(self, env: Env, **rest: U32) -> U32:  # HERE\n"
            "        return U32(0)"
        ),
    ),
    (
        "export_default_value",
        "SPT4003",
        "has a default value, which contractspecv0 cannot express",
        (
            "@contract\nclass D:\n    def go(self, env: Env, x: U32 = U32(0)) -> U32:  # HERE\n"
            "        return U32(0)"
        ),
    ),
    (
        "export_missing_param_annotation",
        "SPT4004",
        "needs a type annotation -- exported signatures",
        (
            "@contract\nclass D:\n    def go(self, env: Env, x) -> U32:  # HERE\n"
            "        return U32(0)"
        ),
    ),
    (
        "export_missing_return_annotation",
        "SPT4005",
        "the return type needs an annotation",
        "@contract\nclass D:\n    def go(self, env: Env):  # HERE\n        return U32(0)",
    ),
    (
        "constructor_wrong_return",
        "SPT4006",
        "must be annotated `-> None`",
        (
            "@contract\nclass D:\n    def __init__(self, env: Env) -> U32:  # HERE\n"
            "        return U32(0)"
        ),
    ),
    (
        "method_name_too_long",
        "SPT5001",
        "names are capped at",
        "@contract\nclass D:\n    def " + "a" * 34 + "(self, env: Env) -> U32:  # HERE\n"
        "        return U32(0)",
    ),
    (
        "field_name_outside_symbol_charset",
        "SPT5001",
        "names must be valid Symbols",
        "@contracttype\nclass S:\n    caf\u00e9: U32  # HERE",
    ),
    (
        "unresolvable_annotation",
        "SPT2003",
        "cannot resolve annotations",
        (
            "@contract\nclass D:\n    def go(self, env: Env, x: Missing) -> U32:  # HERE\n"
            "        return U32(0)"
        ),
    ),
    (
        "module_const_out_of_range",
        "SPT3004",
        "is out of range for",
        "LIMIT = U32(2 ** 40)  # HERE",
    ),
    (
        "raw_name_error",
        "SPT2001",
        "",  # the empty-needle catch-all row: any undischarged NameError
        "LIMIT = MISSING_CONST  # HERE",
    ),
    (
        "syntax_error",
        "SPT1037",
        "",  # not a `_BRIDGE_RULES` row at all: `ast.parse`/`compile()` itself
        "def broken(:  # HERE\n    pass",
    ),
    (
        "module_top_level_bad_statement",
        "SPT1031",
        "",  # module-shape check, not an exec-time bridge row
        "if U32(1) == U32(1):  # HERE\n    pass",
    ),
    (
        "module_non_serpent_import",
        "SPT2005",
        "",
        "import os  # HERE",
    ),
    (
        "module_undecorated_class",
        "SPT4015",
        "",
        "class Plain:  # HERE\n    pass",
    ),
    (
        "module_extra_contract_class",
        "SPT4019",
        "",
        (
            "@contract\nclass First:\n    def go(self, env: Env) -> U32:\n"
            "        return U32(0)\n\n\n"
            "@contract\nclass Second:  # HERE\n    def go(self, env: Env) -> U32:\n"
            "        return U32(0)"
        ),
    ),
    (
        "class_body_wrong_kind_member",
        "SPT4020",
        "",
        "@contracttype\nclass S:\n    def go(self) -> None:  # HERE\n        return None",
    ),
    (
        "class_body_duplicate_member",
        "SPT2004",
        "",
        "@contracttype\nclass S:\n    a: U32\n    a: U32  # HERE",
    ),
    # --- M1-E2 Task 2: the union / int-enum declaration checks ---------------
    # One row per new needle (P8: the meta-test below pins the row set against
    # `_BRIDGE_RULES` itself). Each needle is shared by BOTH new kinds -- the
    # two decorators word one rule one way -- so the enum spelling of each is
    # covered by `tests/unit/test_loader.py`'s bridging matrix rather than by a
    # second row here.
    (
        "union_empty_body",
        "SPT4021",
        "declares at least one case",
        "@contractunion\nclass U(ContractUnion):  # HERE\n    pass",
    ),
    (
        "union_case_bare_value",
        "SPT4022",
        "case is declared as",
        "@contractunion\nclass U(ContractUnion):\n    Circle = 3  # HERE",
    ),
    (
        "int_enum_discriminant_out_of_range",
        "SPT4023",
        "is out of range -- an int-enum member",
        "@contractenum\nclass L(ContractEnum):\n    Bad = enumvalue(-1)  # HERE",
    ),
    (
        "int_enum_duplicate_discriminant",
        "SPT4024",
        "is already declared by",
        (
            "@contractenum\nclass L(ContractEnum):\n    Low = enumvalue(1)\n"
            "    Also = enumvalue(1)  # HERE"
        ),
    ),
    (
        "union_without_its_base",
        "SPT4025",
        "class declares exactly one base",
        "@contractunion\nclass U:  # HERE\n    Empty = variant()",
    ),
    (
        "variant_payload_arity",
        "SPT5006",
        "a variant payload carries at most",
        (
            "@contractunion\nclass U(ContractUnion):  # HERE\n    Big = variant("
            + "U32, " * 12
            + "U32)"
        ),
    ),
    # --- fix round 1: the two case factories' own TypeErrors -----------------
    # Both are raised from the CLASS BODY, so each carries the `errorcode`
    # baseline's AST refinement and lands on the member, not the class.
    (
        "int_enum_discriminant_not_an_int",
        "SPT4023",
        "enumvalue() takes an int discriminant",
        '@contractenum\nclass L(ContractEnum):\n    Low = enumvalue("x")  # HERE',
    ),
    (
        "variant_payload_is_a_value",
        "SPT4022",
        "variant() takes payload types",
        "@contractunion\nclass U(ContractUnion):\n    Circle = variant(U32(3))  # HERE",
    ),
    # --- M1-E2 final review: the two unrunnable declaration accepts ---------
    # Neither spends a new code: an Option payload breaks the payload/field
    # annotation rule SPT4012 already carries for this position, and a case
    # named after a base reader breaks SPT2004's shadowing rule.
    (
        "union_option_payload",
        "SPT4012",
        "a variant payload cannot be absent",
        "@contractunion\nclass U(ContractUnion):\n    Some = variant(U32 | None)  # HERE",
    ),
    (
        "union_case_shadows_a_reader",
        "SPT2004",
        "the reader every union value is read through",
        "@contractunion\nclass U(ContractUnion):\n    tag = variant(U32)  # HERE",
    ),
    # --- M1-E2 Task 5: the `topic`-marker refusal (fed item X2, ruling E10) --
    # `SPT4026` in a position that has no topics. One row per new needle
    # (P8): the struct-field spelling (`_build_record`, recoded off the
    # SPT1037 catch-all) and the `_check_method` spelling, whose needle is
    # shared by BOTH new positions (a parameter and the return type), so one
    # row here (the parameter spelling) proves the shared needle; the return
    # spelling and the located-message assertions for all three positions are
    # `tests/unit/test_decorators.py`'s job.
    (
        "struct_field_topic_marker",
        "SPT4026",
        "a @contracttype struct has no topics",
        "@contracttype\nclass S:\n    a: Annotated[U32, topic]  # HERE",
    ),
    (
        "method_parameter_topic_marker",
        "SPT4026",
        "of a contract method has no topics, so the marker",
        (
            "@contract\nclass D:\n"
            "    def go(self, env: Env, x: Annotated[U32, topic]) -> U32:  # HERE\n"
            "        return U32(0)"
        ),
    ),
    # --- M1-E2 Task 10: `_split_topic`'s NESTED-marker refusal --------------
    # The third SPT4026 needle, and the one raise in the whole `topic` seam
    # that had no rule at all until this task (it fell to the SPT1037
    # catch-all, which says the construct is unsupported -- false for an
    # annotation that is merely mismarked). One row per needle (P8): the
    # needle is shared by all three positions the helper is reached from, so
    # the method-parameter spelling proves it here and the struct-field
    # spelling is `test_the_struct_field_nested_marker_lands_on_spt4026`'s
    # job below.
    (
        "method_parameter_nested_topic_marker",
        "SPT4026",
        "`topic` must mark the whole annotation",
        (
            "@contract\nclass D:\n"
            "    def go(self, env: Env, x: Annotated[U32, topic] | None) -> U32:  # HERE\n"
            "        return U32(0)"
        ),
    ),
    # --- M1-G Task 5: the raise this round bridged AND can reach (O-HYG4) --
    # `_check_topic_list`'s prefixless-event refusal. `topics=()` makes the
    # first `Annotated[T, topic]` field the event's `topics[0]`, which names
    # the event, so a non-Symbol there is SPT3019 -- held at the declaration
    # rather than at the `publish` call the code was written for. The
    # `amount` field is load-bearing: an event whose ONLY field is a topic
    # trips `_check_data_format`'s container-needs-a-data-field refusal first
    # (itself an `_UNBRIDGED_DEBT` shape).
    (
        "event_prefixless_first_topic_field_not_symbol",
        "SPT3019",
        "this field is the event's topics[0]",
        (
            "@contractevent(topics=())\nclass Moved(Event):\n"
            "    who: Annotated[U32, topic]  # HERE\n"
            "    amount: U32"
        ),
    ),
    # The OTHER rule this round added -- `_enum_member`'s owner check ->
    # SPT4025 -- has no row: no contract source reaches it at all. See
    # `_UNREACHABLE_NEEDLES` below for the dominance argument.
)


@pytest.mark.parametrize(
    ("row_id", "code", "body"),
    [(row_id, code, body) for row_id, code, _needle, body in _ROWS],
    ids=[row_id for row_id, _code, _needle, _body in _ROWS],
)
def test_every_b3_row_is_a_located_diagnostic(row_id: str, code: str, body: str) -> None:
    """Each row compiles through the FULL `compile_module` pipeline (not just
    `load_module`) and produces exactly one `code` diagnostic at its `# HERE`
    line -- never a raw traceback, never a `WHOLE_FILE` fallback."""
    src = _source(body, with_contract=row_id not in _NO_AUTO_CONTRACT)
    with pytest.raises(CompileError) as exc_info:
        compile_module(src, PATH)
    found = [d for d in exc_info.value.diagnostics if d.code == code]
    assert len(found) == 1, f"expected exactly one {code}; got {exc_info.value.diagnostics!r}"
    diagnostic = found[0]
    assert diagnostic.loc.kind is LocKind.NODE
    assert diagnostic.loc.line == _here_line(src)


def test_the_struct_field_nested_marker_lands_on_spt4026() -> None:
    """The nested-marker needle's OTHER position (M1-E2 Task 10).

    `_split_topic` is one helper reached from three positions, and its refusal
    is worded for all of them, so the struct-field spelling has to arrive at
    the same code as the method-parameter row above -- not at the SPT1037
    catch-all it fell to before this task. Asserted here rather than as a
    second `must_reject/` fixture: the fixture tree already carries the
    method-parameter spelling, and one fixture per POSITION of one shared
    needle is the duplication `_ROWS`'s own one-row-per-needle rule avoids.
    """
    src = _source("@contracttype\nclass S:\n    a: Annotated[U32, topic] | None  # HERE")
    with pytest.raises(CompileError) as exc_info:
        compile_module(src, PATH)
    (diagnostic,) = exc_info.value.diagnostics
    assert diagnostic.code == "SPT4026"
    assert diagnostic.loc.kind is LocKind.NODE
    assert diagnostic.loc.line == _here_line(src)
    assert "must mark the whole annotation" in "\n".join(diagnostic.notes)


#: Needles `test_every_b3_row_is_a_located_diagnostic` cannot exercise
#: directly because no real contract source reaches them -- verified
#: empirically for task-11b-report.md, not asserted here. Each is still
#: represented in `_ROWS` under its CODE (via the reachable path that
#: produces the same code), so the code-level completeness check below still
#: sees full coverage; only the exact exec-time needle is unreachable.
_UNREACHABLE_NEEDLES: frozenset[str] = frozenset(
    {
        # `decorators._reject_redecoration`'s ValueError: dominated on every
        # real-source path by `loader._check_class_def`'s syntactic
        # more-than-one-decorator check (same code, SPT4013, reached first),
        # and a manual re-application (`S = contracttype(S)`) is itself
        # refused as a module-level redeclaration (SPT2004,
        # `loader._claim_name`) before the reassignment ever executes.
        "already declared as a serpent",
        # `types._udt._enum_member`'s TypeError (bridged to SPT4025 in M1-G
        # Task 5): reached only by reading `X.Member` where `Member` is an
        # `enumvalue(...)` placeholder and `X` is not a `ContractEnum`
        # subclass -- and every route to that read is dominated by an earlier
        # check (verified empirically, task-5 report): an undecorated class is
        # SPT4015 (a module-shape check, before the module body ever runs);
        # `enumvalue(...)` in a @contract / @contracttype / @contractevent
        # body is SPT4020, in a @contracterror body SPT4008, in a
        # @contractunion body SPT4022; and in a @contractenum body the owner
        # IS a `ContractEnum` subclass, so the check passes. SPT4025 itself is
        # exercised by the `union_without_its_base` row.
        "declares a member of a ContractEnum subclass",
    }
)


def test_every_bridge_rule_needle_has_a_row() -> None:
    """Every non-empty `loader._BRIDGE_RULES` needle is exercised by a row
    above, OR is named in `_UNREACHABLE_NEEDLES` with its reachability
    argument -- the "visibly complete" property F.2.12 asks for: a bridge
    rule added later with no matching row (and no unreachability argument)
    here fails this assertion immediately, rather than silently losing
    coverage."""
    rule_needles = {rule.needle for rule in loader._BRIDGE_RULES if rule.needle}
    row_needles = {needle for _id, _code, needle, _body in _ROWS if needle}
    missing = rule_needles - row_needles - _UNREACHABLE_NEEDLES
    assert not missing, f"bridge rule needle(s) with no completeness row: {missing}"
    stale = _UNREACHABLE_NEEDLES - rule_needles
    assert not stale, f"_UNREACHABLE_NEEDLES cite needle(s) no longer in _BRIDGE_RULES: {stale}"


#: The declaration-layer MODULES (not functions): every `raise` in them --
#: outside dunder methods, which are RUNTIME surface (`__setattr__`'s
#: immutability refusals are reached by a contract body, never a declaration)
#: and are excluded by construction -- is either bridged (its message carries
#: a `loader._BRIDGE_RULES` needle), listed in `_UNBRIDGED_BY_DESIGN` with a
#: reason, or listed in `_UNBRIDGED_DEBT` with the M2 item that owes it a
#: code. M1-E2's version of this gate named FUNCTIONS, so a raise added to any
#: other function was invisible (one-directional blind spot, E2 attn section 3;
#: O-HYG4/D11) -- deriving the walk from the module closes it.
_DECLARATION_LAYER_MODULES: tuple[str, ...] = ("serpent.decorators", "serpent.types._udt")

#: `(module, message fragment)` for raises a USER cannot reach through a
#: declaration -- internal invariants, or paths the loader intercepts before
#: the decorator runs -- each with its reason. Keyed on TEXT (the M1-F
#: allowlist lesson), so a reworded message re-enters the gate. MAY NOT hold a
#: user-reachable raise: that is `_UNBRIDGED_DEBT`'s job.
_UNBRIDGED_BY_DESIGN: frozenset[tuple[str, str]] = frozenset(
    {
        # `_ContractUnion._cmp_payload`'s deferred refusal: RUNTIME value
        # surface, not a declaration. It is `Vec`'s own `_DEFERRED` text, and
        # it is reached only through a comparison operator (`__lt__` ->
        # `_ordering.val_cmp`), i.e. the same runtime path as the
        # `__setattr__` refusals the dunder rule already excludes -- a
        # declaration never orders two union values.
        ("serpent.types._udt", "container comparison"),
    }
)

#: User-REACHABLE declaration raises with NO honest registry code today: the
#: M1-E Task 5 event-convention shapes M1-E2's gate declined to fix and this
#: gate now makes VISIBLE (a new raise here fails the gate; an entry here is a
#: named debt, not a design). Each entry cites the M2 registry item owing it a
#: code ("event-convention shape codes: data_format, prefix-topic cap,
#: prefix-topic charset, bare-string topics, no topics"). Keyed on TEXT like
#: the list above.
_UNBRIDGED_DEBT: frozenset[tuple[str, str]] = frozenset(
    {
        ("serpent.decorators", "an event declares at most"),  # prefix-topic cap
        ("serpent.decorators", "topics= takes a sequence of topics, not one string"),
        ("serpent.decorators", "data_format must be one of"),
        ("serpent.decorators", "data_format 'single-value' publishes exactly one"),
        # data_format map/vec with no data fields
        ("serpent.decorators", "publishes the non-topic"),
        ("serpent.decorators", "publishes the data fields as one"),  # vec with mixed types
        ("serpent.decorators", "an event publishes at least one topic, and this one has"),
        # `_prefix_topics`' charset/length refusal, whose message is built by
        # `_bad_prefix_topic(...)` -- visible here only through
        # `_indirect_message_chunks` below, and keyed on the one chunk both of
        # that helper's two wordings share.
        ("serpent.decorators", "a valid Symbol of 1 to"),
    }
)


def _dunder_body_nodes(tree: ast.Module) -> set[int]:
    """Every node inside a dunder method -- runtime surface, not declaration."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name.startswith("__")
            and node.name.endswith("__")
        ):
            inside.update(id(n) for n in ast.walk(node))
    return inside


def _raises_outside_dunders(tree: ast.Module) -> list[ast.Raise]:
    """Every `raise` in the module except those inside a dunder method."""
    dunder_bodies = _dunder_body_nodes(tree)
    return [n for n in ast.walk(tree) if isinstance(n, ast.Raise) and id(n) not in dunder_bodies]


def _body_string_literals(function: ast.FunctionDef) -> list[str]:
    """Every non-empty string literal in `function`'s body, minus its docstring.

    The docstring is excluded on purpose: it is prose ABOUT the refusal, and
    letting it into the chunk list would let a needle match documentation
    rather than a message a user ever sees. So is the empty string: a
    zero-length chunk would make `fragment in chunk` false and
    `"" in chunk` true, i.e. noise on both sides of the comparison.
    """
    body = function.body[1:] if ast.get_docstring(function) else function.body
    return [
        literal.value
        for stmt in body
        for literal in ast.walk(stmt)
        if isinstance(literal, ast.Constant) and isinstance(literal.value, str) and literal.value
    ]


def _module_level_functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """The module's TOP-LEVEL functions by name.

    `tree.body`, never `ast.walk`: a method sharing a module function's name
    would otherwise substitute the wrong body (the walk is last-wins).
    """
    return {stmt.name: stmt for stmt in tree.body if isinstance(stmt, ast.FunctionDef)}


def _module_only_names(tree: ast.Module) -> frozenset[str]:
    """Names bound at module level (assigned or imported) and NEVER rebound
    inside any function -- so reading one off the live module cannot be
    mistaking a local variable for the module constant it shadows."""
    module_level: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            module_level.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            module_level.add(stmt.target.id)
        elif isinstance(stmt, ast.Import | ast.ImportFrom):
            module_level.update(alias.asname or alias.name for alias in stmt.names)
    rebound: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assign):
                rebound.update(t.id for t in inner.targets if isinstance(t, ast.Name))
            elif isinstance(inner, ast.AnnAssign) and isinstance(inner.target, ast.Name):
                rebound.add(inner.target.id)
    return frozenset(module_level - rebound)


def _indirect_message_chunks(raised: ast.Raise, tree: ast.Module, module: ModuleType) -> list[str]:
    """The message text a `raise` builds INDIRECTLY, resolved one hop.

    `_raise_message_chunks` reads literal fragments only, so
    `raise ValueError(_bad_prefix_topic(...))` and
    `raise NotImplementedError(_DEFERRED)` carry no literal chunk at all and
    were invisible to this gate -- a blind spot in the same family as the
    hand-kept function list. This resolves exactly the two shapes the
    declaration layer uses, and NOTHING else:

    * `raise Exc(<module-level function>(...))` -> that function's own
      literals;
    * `raise Exc(<module-level str constant>)` -> its live value (which may
      have been imported from another module).

    The exception's ONE top-level argument, never a nested node (fix round 1,
    I1). An f-string interpolating a formatting helper --
    `raise ValueError(f"... {_render(x)} ...")` -- is NOT resolved: walking
    into it mixed an unrelated helper's literals into the message set, and the
    day such a helper carried a needle-bearing literal an unbridged raise
    would have read as bridged, which is precisely the blind spot this gate
    exists to close. A raise whose message this cannot resolve stays visible
    (it fails the gate until it is bridged or listed), which is the safe
    direction to be wrong in.
    """
    if not isinstance(raised.exc, ast.Call) or len(raised.exc.args) != 1 or raised.exc.keywords:
        return []
    argument = raised.exc.args[0]
    if isinstance(argument, ast.Call) and isinstance(argument.func, ast.Name):
        helper = _module_level_functions(tree).get(argument.func.id)
        return [] if helper is None else _body_string_literals(helper)
    if isinstance(argument, ast.Name) and argument.id in _module_only_names(tree):
        value = getattr(module, argument.id, None)
        return [value] if isinstance(value, str) and value else []
    return []


def _raise_message_chunks(node: ast.AST) -> list[str]:
    """Every literal string fragment of every `raise` inside `node`.

    A declaration-site message is an f-string, so a needle can only ever live
    inside ONE literal fragment -- which is exactly what a bridge rule matches
    on, and therefore what this compares.
    """
    chunks: list[str] = []
    for raised in (n for n in ast.walk(node) if isinstance(n, ast.Raise)):
        for literal in ast.walk(raised):
            if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                chunks.append(literal.value)
    return chunks


def test_every_declaration_layer_raise_carries_a_bridge_needle() -> None:
    """The MISSING direction of the gate above (M1-E2 fix round 1's finding),
    now derived from the MODULE rather than a hand-kept list of functions.

    `test_every_bridge_rule_needle_has_a_row` proves every RULE is exercised;
    nothing proved the converse -- a declaration-site `raise` with no rule at
    all was invisible, and fell silently to `SPT1037` ("not supported by the
    serpent subset"), which is false for a construct that IS supported and
    merely malformed. M1-E2's version of this test named the eight+four
    functions it had audited, so a raise added to any OTHER function in the
    same modules stayed invisible; this walks every raise in them.
    """
    needles = [rule.needle for rule in loader._BRIDGE_RULES if rule.needle]
    listed = _UNBRIDGED_BY_DESIGN | _UNBRIDGED_DEBT
    unbridged: list[tuple[str, int, str]] = []
    for module_name in _DECLARATION_LAYER_MODULES:
        module = importlib.import_module(module_name)
        tree = ast.parse(pathlib.Path(module.__file__ or "").read_text(encoding="utf-8"))
        for raised in _raises_outside_dunders(tree):
            chunks = _raise_message_chunks(raised)
            chunks.extend(_indirect_message_chunks(raised, tree, module))
            if any(needle in chunk for chunk in chunks for needle in needles):
                continue
            if any(
                fragment in chunk for chunk in chunks for m, fragment in listed if m == module_name
            ):
                continue
            unbridged.append((module_name, raised.lineno, " | ".join(chunks) or "<no message>"))
    assert not unbridged, (
        "declaration-site raise(s) with neither a `loader._BRIDGE_RULES` needle nor an "
        f"`_UNBRIDGED_BY_DESIGN`/`_UNBRIDGED_DEBT` entry -- each would fall to SPT1037: {unbridged}"
    )


def test_the_unbridged_lists_are_live_and_disjoint() -> None:
    """Every listed fragment still matches at least one raise the walk sees in
    its module (a stale entry is deleted, not kept), and no fragment is in
    both lists.

    Matched against the WALK's chunks rather than the raw source, because a
    fragment may come from a helper's text or from a string constant imported
    from another module -- the same text the gate above compares.
    """
    assert not (_UNBRIDGED_BY_DESIGN & _UNBRIDGED_DEBT)
    for module_name, fragment in _UNBRIDGED_BY_DESIGN | _UNBRIDGED_DEBT:
        module = importlib.import_module(module_name)
        tree = ast.parse(pathlib.Path(module.__file__ or "").read_text(encoding="utf-8"))
        matches = [
            raised.lineno
            for raised in _raises_outside_dunders(tree)
            if any(
                fragment in chunk
                for chunk in (
                    _raise_message_chunks(raised) + _indirect_message_chunks(raised, tree, module)
                )
            )
        ]
        assert matches, (module_name, fragment)


#: A synthetic declaration layer for the resolver's own teeth test. Four
#: raise shapes, all four with a needle-bearing literal SOMEWHERE reachable
#: from them, only two of which the resolver may ever read.
_RESOLVER_PROBE = textwrap.dedent(
    """
    _CONSTANT = "a needle in a module constant"
    _SHADOWED = "a needle in a shadowed constant"


    def _render(value: object) -> str:
        return "a needle in an unrelated formatting helper"


    def _refusal(value: object) -> str:
        return "a needle a bare helper call carries"


    def nested_call(value: object) -> None:
        raise ValueError(f"prefix {_render(value)} suffix")


    def bare_helper_call(value: object) -> None:
        raise ValueError(_refusal(value))


    def bare_constant() -> None:
        raise NotImplementedError(_CONSTANT)


    def shadowing_local() -> None:
        _SHADOWED = "a local that shadows the module constant"
        raise ValueError(_SHADOWED)
    """
)


def _probe_chunks() -> dict[str, list[str]]:
    """`_RESOLVER_PROBE`'s raises, by enclosing function, with the literal AND
    indirect chunks the gate would compare against a needle."""
    tree = ast.parse(_RESOLVER_PROBE)
    module = ModuleType("resolver_probe")
    exec(compile(tree, "<resolver_probe>", "exec"), module.__dict__)  # noqa: S102
    chunks: dict[str, list[str]] = {}
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef):
            continue
        for raised in (n for n in ast.walk(function) if isinstance(n, ast.Raise)):
            chunks[function.name] = _raise_message_chunks(raised) + _indirect_message_chunks(
                raised, tree, module
            )
    return chunks


def test_the_indirect_resolver_reads_only_the_exceptions_top_level_argument() -> None:
    """Fix round 1, I1: the resolver's teeth.

    `raise ValueError(f"... {_render(x)} ...")` must NOT pick up `_render`'s
    text. The earlier version walked every nested node, so a formatting helper
    reached from inside an f-string contributed its literals to the message
    set -- and the day such a helper carried a bridge needle, an unbridged
    raise would have read as BRIDGED, which is the one failure mode this gate
    exists to prevent. The two shapes that ARE resolved keep working.
    """
    chunks = _probe_chunks()

    # The nested call is not followed: its own f-string literals are all the
    # gate sees, so a needle living only in `_render` cannot bridge it.
    assert "a needle in an unrelated formatting helper" not in chunks["nested_call"]
    assert chunks["nested_call"] == ["prefix ", " suffix"]

    # `raise Exc(<module function>(...))` resolves to the helper's own text.
    assert "a needle a bare helper call carries" in chunks["bare_helper_call"]

    # `raise Exc(<module str constant>)` resolves to its live value.
    assert "a needle in a module constant" in chunks["bare_constant"]

    # A LOCAL that merely shares a module constant's name is not resolved --
    # the module-level value is not what this raise carries.
    assert chunks["shadowing_local"] == []


def test_the_indirect_resolver_drops_empty_chunks() -> None:
    """A zero-length chunk matches every fragment on one side of `in` and no
    fragment on the other, so it is noise the resolver must not emit. Both
    real resolutions are checked for it, `_render`'s `''` separators being
    where the earlier version picked them up."""
    for name, chunks in _probe_chunks().items():
        assert "" not in chunks, name
    for module_name in _DECLARATION_LAYER_MODULES:
        module = importlib.import_module(module_name)
        tree = ast.parse(pathlib.Path(module.__file__ or "").read_text(encoding="utf-8"))
        for raised in _raises_outside_dunders(tree):
            assert "" not in _indirect_message_chunks(raised, tree, module), (
                module_name,
                raised.lineno,
            )


def test_every_bridge_rule_code_has_a_row() -> None:
    """Every code `loader._BRIDGE_RULES` can produce -- and the two rows
    (`SyntaxError`, module-shape checks) that are not `_BRIDGE_RULES` rows at
    all -- is exercised somewhere in `_ROWS`."""
    rule_codes = {rule.code for rule in loader._BRIDGE_RULES}
    row_codes = {code for _id, code, _needle, _body in _ROWS}
    missing = rule_codes - row_codes
    assert not missing, f"bridge rule code(s) with no completeness row: {missing}"
