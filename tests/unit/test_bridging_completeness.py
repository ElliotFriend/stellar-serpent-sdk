"""Bridging completeness (Task 11b, dossier F.2.12).

`tests/unit/test_loader.py` (Task 3) already proves every individual §B.3
check produces a located diagnostic; its coverage is thorough but spread
across ~20 separate test functions, so the COMPLETENESS of that coverage --
"every check the loader's exec-time bridge (`loader._BRIDGE_RULES`) and
module-shape validator can raise is exercised" -- is not visible in any one
place. This module is that one place: `_ROWS` lists every §B.3 row by name,
each row compiles a minimal module through the FULL `compile_module` pipeline
(not just `load_module`) and asserts a LOCATED diagnostic with the row's code
-- proving no raw traceback escapes end-to-end -- and
`test_every_bridge_rule_needle_has_a_row` pins the row set against
`loader._BRIDGE_RULES` itself, so a bridge rule added later with no matching
row here fails loudly instead of silently losing coverage.
"""

from __future__ import annotations

import re

import pytest

from serpent.compiler import compile_module, loader
from serpent.compiler.diagnostics import CompileError, LocKind

PATH = "contract.py"

_IMPORTS = (
    "from serpent import ("
    "Env, U32, contract, contracterror, contractevent, contracttype, errorcode"
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


def test_every_bridge_rule_code_has_a_row() -> None:
    """Every code `loader._BRIDGE_RULES` can produce -- and the two rows
    (`SyntaxError`, module-shape checks) that are not `_BRIDGE_RULES` rows at
    all -- is exercised somewhere in `_ROWS`."""
    rule_codes = {rule.code for rule in loader._BRIDGE_RULES}
    row_codes = {code for _id, code, _needle, _body in _ROWS}
    missing = rule_codes - row_codes
    assert not missing, f"bridge rule code(s) with no completeness row: {missing}"
