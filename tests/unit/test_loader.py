"""Tests for the hybrid module loader (`serpent.compiler.loader`).

Task 3 of the M1-C plan. Three things are under test:

1. **Module-shape validation** (dossier SS B.1's `Module`/`Import` rows): what a
   contract module's top level may contain, each violation a located
   diagnostic.
2. **Statement-wise exec bridging** (MJ-4): the FULL SS B.3 matrix -- every
   check `decorators.py` enforces as a location-free `ValueError`/`TypeError`
   -- re-reported as a LOCATED diagnostic against the right statement, never
   a raw traceback and never a fabricated location.
3. **The inventory cross-check** (F.1.14): `_serpent_type_` versus the AST. A
   skew is a COMPILER BUG, so it raises an internal hard error, not a user
   diagnostic.

Expected lines are never written as literals: every source below marks the
line a diagnostic must land on with a trailing `# HERE` comment and
`_here_line()` resolves it, the same anchor convention `tests/must_reject/`
uses (MJ-14). Inserting a line above the marker therefore never requires
renumbering a test.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from serpent.compiler import codes
from serpent.compiler.diagnostics import CompileError, Diagnostic, Diagnostics, LocKind
from serpent.compiler.loader import (
    CompilerBugError,
    DecoratedDecl,
    LoadedModule,
    _cross_check_inventory,
    load_module,
)

PATH = "contract.py"

#: Every name any test source below might reference, so a source only has to
#: declare the interesting part. Unused imports are harmless at runtime.
_IMPORTS = (
    "from serpent import ("
    "Address, Bool, Bytes, ContractError, Env, Event, String, Symbol, U32, U64, "
    "contract, contracterror, contractevent, contracttype, errorcode"
    ")"
)

#: A minimal, valid `@contract` class. Appended to sources whose interesting
#: part is elsewhere, so the module also satisfies the exactly-one-@contract
#: rule and the diagnostic under test is the only one produced.
_MIN_CONTRACT = """
@contract
class C:
    def go(self, env: Env) -> U32:
        return U32(0)
"""


def source(body: str, *, with_contract: bool = True) -> str:
    """Assemble a complete contract module from `body`."""
    parts = [_IMPORTS, "", body.strip("\n")]
    if with_contract:
        parts.append(_MIN_CONTRACT.strip("\n"))
    return "\n".join(parts) + "\n"


_HERE_RE = re.compile(r"#\s*HERE\b")


def _here_line(src: str) -> int:
    """The 1-indexed line of the first `# HERE` marker comment in `src`."""
    for lineno, line in enumerate(src.splitlines(), start=1):
        if _HERE_RE.search(line):
            return lineno
    raise AssertionError("test source has no `# HERE` marker")


def load(src: str, path: str = PATH) -> LoadedModule:
    return load_module(src, path)


def diags(src: str, path: str = PATH) -> tuple[Diagnostic, ...]:
    return load(src, path).diagnostics.diagnostics


def of_code(src: str, code: str) -> tuple[Diagnostic, ...]:
    return tuple(d for d in diags(src) if d.code == code)


def expect_at_here(src: str, code: str) -> Diagnostic:
    """Assert exactly one `code` diagnostic, on the `# HERE` line."""
    found = of_code(src, code)
    assert len(found) == 1, f"expected one {code}; got {diags(src)!r}"
    diagnostic = found[0]
    assert diagnostic.loc.kind is LocKind.NODE
    assert diagnostic.loc.line == _here_line(src), (
        f"{code} landed on line {diagnostic.loc.line}, expected {_here_line(src)}"
    )
    assert diagnostic.loc.path == PATH
    return diagnostic


# ---------------------------------------------------------------------------
# A. The happy path
# ---------------------------------------------------------------------------

TOKEN_SHAPED = '''"""A token-shaped module docstring, which P1 says is skipped."""

from serpent import (
    U32,
    Address,
    Bool,
    Env,
    Event,
    String,
    Symbol,
    contract,
    contracterror,
    contractevent,
    contracttype,
    errorcode,
)

ADMIN = Symbol("ADMIN")
NAME_KEY = Symbol("NAME")


@contracterror
class TokenError:
    InsufficientBalance = errorcode(1)
    Unauthorized = errorcode(2)


@contracttype
class BalanceKey:
    """A struct storage key."""

    owner: Address


@contractevent
class Transfer(Event):
    frm: Address
    to: Address
    amount: U32


def double(env: Env, amount: U32) -> U32:
    return amount + amount


@contract
class TokenStyle:
    """A minimal fungible-token-shaped contract."""

    def __init__(self, env: Env, admin: Address, name: String) -> None:
        env.storage().instance().set(ADMIN, admin)

    def name(self, env: Env) -> String:
        return env.storage().instance().get(NAME_KEY, String)

    def _private(self, env: Env) -> U32:
        return U32(1)

    def balance(self, env: Env, owner: Address) -> U32:
        key = BalanceKey(owner=owner)
        return env.storage().persistent().get(key, U32, default=U32(0))
'''


def test_token_shaped_module_loads_with_no_diagnostics() -> None:
    loaded = load(TOKEN_SHAPED)
    assert loaded.diagnostics.diagnostics == ()


def test_loaded_module_carries_path_source_lines_and_tree() -> None:
    loaded = load(TOKEN_SHAPED)
    assert loaded.path == PATH
    assert loaded.source_lines == tuple(TOKEN_SHAPED.splitlines())
    assert isinstance(loaded.tree, ast.Module)


def test_contract_cls_and_node_are_the_single_contract_class() -> None:
    loaded = load(TOKEN_SHAPED)
    assert loaded.contract_cls is not None
    assert loaded.contract_cls.__name__ == "TokenStyle"
    assert loaded.contract_node is not None
    assert loaded.contract_node.name == "TokenStyle"


def test_decorated_types_in_order_is_structs_and_error_enums_only() -> None:
    # MJ-9: events are tracked SEPARATELY, and declaration order is pinned
    # (B10) -- TokenError is declared before BalanceKey.
    loaded = load(TOKEN_SHAPED)
    assert [(d.name, d.kind) for d in loaded.decorated_types_in_order] == [
        ("TokenError", "error_enum"),
        ("BalanceKey", "struct"),
    ]


def test_events_are_a_separate_inventory() -> None:
    loaded = load(TOKEN_SHAPED)
    assert [(e.name, e.kind) for e in loaded.events] == [("Transfer", "event")]
    assert "Transfer" not in {d.name for d in loaded.decorated_types_in_order}


def test_module_consts_are_collected_with_their_executed_values() -> None:
    # P5: module-level chain constants.
    loaded = load(TOKEN_SHAPED)
    assert [c.name for c in loaded.module_consts] == ["ADMIN", "NAME_KEY"]
    admin = loaded.module_consts[0]
    assert admin.value == loaded.namespace["ADMIN"]
    assert isinstance(admin.node, ast.Assign)


def test_module_level_helpers_are_collected() -> None:
    loaded = load(TOKEN_SHAPED)
    assert [h.name for h in loaded.helpers] == ["double"]
    assert callable(loaded.helpers[0].func)
    assert isinstance(loaded.helpers[0].node, ast.FunctionDef)


def test_namespace_holds_every_executed_declaration() -> None:
    loaded = load(TOKEN_SHAPED)
    for name in ("ADMIN", "TokenError", "BalanceKey", "Transfer", "double", "TokenStyle"):
        assert name in loaded.namespace


def test_decls_carry_the_serpent_type_metadata_and_their_ast_node() -> None:
    loaded = load(TOKEN_SHAPED)
    struct = next(d for d in loaded.decorated_types_in_order if d.name == "BalanceKey")
    assert struct.metadata["kind"] == "struct"
    assert [f[0] for f in struct.metadata["fields"]] == ["owner"]
    assert isinstance(struct.node, ast.ClassDef)
    assert struct.node.name == "BalanceKey"


def test_a_private_method_does_not_break_the_cross_check() -> None:
    # `_private` is not exported and `decorators.contract` never records it,
    # so the AST side of the cross-check must skip it too.
    loaded = load(TOKEN_SHAPED)
    assert loaded.contract_cls is not None
    methods = [m[0] for m in loaded.contract_cls._serpent_type_["methods"]]
    assert "_private" not in methods


def test_the_repos_only_complete_authored_contract_loads_clean() -> None:
    """`tests/fixtures/token_style.py` (A23/F.2.8) through the real loader.

    It is the only complete authored contract in the repo, and it is loaded
    AS TEXT here -- not imported -- so this exercises the shape check, the
    statement-wise exec and the cross-check on real authored source rather
    than a test fabrication. (Its method *bodies* are later tasks' business;
    the loader does not look inside them, which is why the `Event.publish`
    line E12 rejects does not affect this.)
    """
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "token_style.py"
    loaded = load(fixture.read_text(), str(fixture))
    assert loaded.diagnostics.diagnostics == ()
    assert loaded.contract_cls is not None
    assert loaded.contract_cls.__name__ == "TokenStyle"
    assert [(d.name, d.kind) for d in loaded.decorated_types_in_order] == [
        ("TokenError", "error_enum"),
        ("BalanceKey", "struct"),
    ]
    assert [e.name for e in loaded.events] == ["Transfer"]
    assert [c.name for c in loaded.module_consts] == ["ADMIN", "NAME_KEY"]


def test_an_unimported_decorator_says_so_rather_than_undecorated() -> None:
    loaded = load_module("@contract\nclass C:\n    pass\n", PATH)
    notes = " ".join(note for d in loaded.diagnostics.diagnostics for note in d.notes)
    assert "@contract" in notes
    assert "imported from `serpent`" in notes


def test_future_annotations_import_is_accepted() -> None:
    src = "from __future__ import annotations\n" + source("")
    assert diags(src) == ()


def test_pep563_and_plain_modules_record_identical_metadata() -> None:
    # E4: `from __future__ import annotations` must not change the recorded
    # metadata. The loader compiles each statement separately, so the future
    # flag has to be propagated explicitly -- this is the test that proves it.
    body = """
@contracttype
class S:
    counter_limit: U32
    display_name: String
"""
    plain = load(source(body))
    pep563 = load("from __future__ import annotations\n" + source(body))
    assert plain.diagnostics.diagnostics == ()
    assert pep563.diagnostics.diagnostics == ()
    assert plain.decorated_types_in_order[0].metadata == pep563.decorated_types_in_order[0].metadata


def test_pep563_allows_a_helper_to_forward_reference_a_later_struct() -> None:
    # Under PEP 563 a helper's annotations are never evaluated, so a forward
    # reference is legal. If the loader dropped the future flag this would
    # raise NameError at def time and the module would be rejected.
    src = "from __future__ import annotations\n" + source(
        """
def widen(s: Settings) -> U32:
    return U32(0)


@contracttype
class Settings:
    counter_limit: U32
"""
    )
    assert diags(src) == ()


def test_an_aliased_serpent_import_still_resolves_the_decorator() -> None:
    src = source(
        """
from serpent import contract as ct


@ct
class Aliased:
    def go(self, env: Env) -> U32:
        return U32(0)
""",
        with_contract=False,
    )
    loaded = load(src)
    assert loaded.diagnostics.diagnostics == ()
    assert loaded.contract_cls is not None
    assert loaded.contract_cls.__name__ == "Aliased"


def test_load_leaves_sys_modules_clean() -> None:
    before = set(sys.modules)
    load(TOKEN_SHAPED)
    load(source("@contracterror\nclass E:\n    Bad = 1  # HERE"))
    assert set(sys.modules) == before


# ---------------------------------------------------------------------------
# B. Module-shape validation (dossier SS B.1 Module/Import rows)
# ---------------------------------------------------------------------------


def test_syntax_error_becomes_a_located_diagnostic_not_a_traceback() -> None:
    with pytest.raises(CompileError) as exc_info:
        load("def broken(:\n    pass\n")
    (diagnostic,) = exc_info.value.diagnostics
    assert diagnostic.code == "SPT1037"
    assert diagnostic.loc.kind is LocKind.NODE
    assert diagnostic.loc.line == 1
    assert diagnostic.help


def test_syntax_error_diagnostic_renders_the_offending_line() -> None:
    src = "x = U32(1)\ndef broken(:\n    pass\n"
    with pytest.raises(CompileError) as exc_info:
        load(src)
    rendered = exc_info.value.render(src.splitlines())
    assert "contract.py:2:" in rendered
    assert "def broken(:" in rendered


IMPORT_REJECTS = {
    "plain_import": "import os  # HERE",
    "from_other_module": "from os import path  # HERE",
    "dotted_serpent_submodule": "from serpent.types import U32  # HERE",
    "relative": "from . import helpers  # HERE",
    "star": "from serpent import *  # HERE",
    "name_not_in_all": "from serpent import U256  # HERE",
    "other_future_feature": "from __future__ import division  # HERE",
}


@pytest.mark.parametrize("body", IMPORT_REJECTS.values(), ids=IMPORT_REJECTS.keys())
def test_rejected_imports_get_the_import_code(body: str) -> None:
    src = source(body)
    expect_at_here(src, "SPT2005")


def test_serpent_import_of_an_all_name_is_accepted() -> None:
    assert diags(source("from serpent import Bytes")) == ()


MODULE_BODY_REJECTS = {
    "if_statement": "if U32(1) == U32(1):  # HERE\n    pass",
    "for_statement": "for _ in ():  # HERE\n    pass",
    "bare_expression": "U32(1)  # HERE",
    "async_def": "async def f() -> None:  # HERE\n    pass",
    "annassign": "LIMIT: U32 = U32(1)  # HERE",
    "tuple_assign": "A, B = U32(1), U32(2)  # HERE",
    "chained_assign": "A = B = U32(1)  # HERE",
    "attribute_assign": "U32.x = U32(1)  # HERE",
    "with_statement": "with open('x'):  # HERE\n    pass",
    "try_statement": "try:  # HERE\n    pass\nexcept ValueError:\n    pass",
    "delete": "del U32  # HERE",
}


@pytest.mark.parametrize("body", MODULE_BODY_REJECTS.values(), ids=MODULE_BODY_REJECTS.keys())
def test_other_top_level_statements_get_the_module_body_code(body: str) -> None:
    src = source(body)
    diagnostic = expect_at_here(src, "SPT1031")
    assert diagnostic.help, "SPT1xxx diagnostics must carry a help rewrite (F.2.11)"


def test_a_rejected_top_level_statement_is_never_executed() -> None:
    # The shape check runs before exec, so a rejected statement's side effects
    # must not happen -- the loader must not run arbitrary top-level code it
    # has already refused.
    src = source("if U32(1) == U32(1):  # HERE\n    LEAKED = U32(1)")
    loaded = load(src)
    assert "LEAKED" not in loaded.namespace


def test_a_module_docstring_is_skipped_not_rejected() -> None:
    # P1: the module docstring is a supported `Expr` statement.
    src = '"""A docstring."""\n' + source("")
    assert diags(src) == ()


def test_a_second_string_expression_is_not_a_docstring() -> None:
    src = '"""A docstring."""\n' + source('"""Not a docstring."""  # HERE')
    expect_at_here(src, "SPT1031")


def test_an_undecorated_top_level_class_is_rejected() -> None:
    src = source("class Plain:  # HERE\n    pass")
    expect_at_here(src, "SPT4015")


def test_a_class_with_no_serpent_decorator_is_rejected() -> None:
    src = source("@staticmethod\nclass Odd:  # HERE\n    pass")
    expect_at_here(src, "SPT4015")


def test_two_serpent_decorators_on_one_class_are_rejected() -> None:
    src = source("@contracttype\n@contracterror\nclass Both:  # HERE\n    x: U32")
    expect_at_here(src, "SPT4013")


def test_zero_contract_classes_is_a_whole_file_diagnostic() -> None:
    src = source("@contracttype\nclass S:\n    x: U32", with_contract=False)
    found = of_code(src, "SPT4015")
    assert len(found) == 1
    assert found[0].loc.kind is LocKind.WHOLE_FILE
    assert "@contract" in found[0].message


def test_two_contract_classes_report_the_extra_one() -> None:
    src = source(
        """
@contract
class First:
    def go(self, env: Env) -> U32:
        return U32(0)


@contract
class Second:  # HERE
    def go(self, env: Env) -> U32:
        return U32(0)
""",
        with_contract=False,
    )
    diagnostic = expect_at_here(src, "SPT4015")
    assert "@contract" in diagnostic.message


def test_a_base_class_on_a_non_event_decorated_class_is_rejected() -> None:
    # SS B.1: "No base classes except `Event` on `@contractevent` (D8)".
    src = source("@contracttype\nclass S(ContractError):  # HERE\n    x: U32")
    expect_at_here(src, "SPT4015")


def test_an_extra_base_class_on_an_event_is_rejected() -> None:
    src = source("@contractevent\nclass T(Event, ContractError):  # HERE\n    x: U32")
    expect_at_here(src, "SPT4015")


CLASS_BODY_REJECTS = {
    "if_in_a_struct_body": "@contracttype\nclass S:\n    if True:  # HERE\n        a: U32",
    "for_in_a_contract_body": "@contract\nclass D:\n    for _ in ():  # HERE\n        pass",
    "bare_expression_in_a_class_body": "@contracttype\nclass S:\n    U32(1)  # HERE\n    a: U32",
    "second_string_in_a_class_body": (
        '@contracttype\nclass S:\n    """Doc."""\n\n    "not a doc"  # HERE\n    a: U32'
    ),
}


@pytest.mark.parametrize("body", CLASS_BODY_REJECTS.values(), ids=CLASS_BODY_REJECTS.keys())
def test_only_declarations_are_allowed_in_a_decorated_class_body(body: str) -> None:
    # SS C.3 ("there are no class attributes") AND the guard that keeps the
    # F.1.14 cross-check unreachable from source: Python binds whatever a
    # nested `if`/`for` in a class body executes, while the AST view
    # enumerates the body's top level.
    diagnostic = expect_at_here(source(body), "SPT1037")
    assert diagnostic.help


def test_an_async_method_does_not_trip_the_cross_check() -> None:
    # `inspect.isfunction` accepts an async function, so `decorators.contract`
    # records it; the AST side of the cross-check must agree. Rejecting the
    # `async` body itself is Task 6's job, not an internal hard error here.
    src = source(
        """
@contract
class D:
    async def go(self, env: Env) -> None:
        return None
""",
        with_contract=False,
    )
    assert diags(src) == ()


def test_a_property_method_does_not_trip_the_cross_check() -> None:
    # `@property` leaves a non-function attribute, so `decorators.contract`
    # skips it; neither view records it and the cross-check must not fire.
    src = source(
        """
@contract
class D:
    @property
    def p(self, env: Env) -> U32:
        return U32(0)
""",
        with_contract=False,
    )
    assert diags(src) == ()


def test_an_event_inheriting_only_event_is_accepted() -> None:
    assert diags(source("@contractevent\nclass T(Event):\n    x: U32")) == ()


# ---------------------------------------------------------------------------
# C. The FULL SS B.3 bridging matrix (MJ-4)
# ---------------------------------------------------------------------------

#: (id, expected code, module body). Every body marks the statement the
#: diagnostic must land on with `# HERE`. Each row is one check that
#: `decorators.py` raises as a location-free `ValueError`/`TypeError`
#: (dossier SS B.3), which the loader must re-report LOCATED.
BRIDGING_MATRIX: list[tuple[str, str, str]] = [
    (
        "self_not_first",
        "SPT4001",
        """
@contract
class C2:
    def go(env: Env) -> U32:  # HERE
        return U32(0)
""",
    ),
    (
        "var_positional",
        "SPT4002",
        """
@contract
class C2:
    def go(self, env: Env, *rest: U32) -> U32:  # HERE
        return U32(0)
""",
    ),
    (
        "var_keyword",
        "SPT4002",
        """
@contract
class C2:
    def go(self, env: Env, **rest: U32) -> U32:  # HERE
        return U32(0)
""",
    ),
    (
        "default_value",
        "SPT4003",
        """
@contract
class C2:
    def go(self, env: Env, amount: U32 = U32(0)) -> U32:  # HERE
        return U32(0)
""",
    ),
    (
        "missing_param_annotation",
        "SPT4004",
        """
@contract
class C2:
    def go(self, env: Env, amount) -> U32:  # HERE
        return U32(0)
""",
    ),
    (
        "missing_return_annotation",
        "SPT4005",
        """
@contract
class C2:
    def go(self, env: Env):  # HERE
        return U32(0)
""",
    ),
    (
        "init_not_returning_none",
        "SPT4006",
        """
@contract
class C2:
    def __init__(self, env: Env) -> U32:  # HERE
        return U32(0)
""",
    ),
    (
        "staticmethod",
        "SPT4007",
        """
@contract
class C2:
    @staticmethod
    def go(env: Env) -> U32:  # HERE
        return U32(0)
""",
    ),
    (
        "classmethod",
        "SPT4007",
        """
@contract
class C2:
    @classmethod
    def go(cls, env: Env) -> U32:  # HERE
        return U32(0)
""",
    ),
    (
        "bare_int_error_member",
        "SPT4008",
        """
@contracterror
class E:
    BadThing = 1  # HERE
""",
    ),
    (
        "errorcode_with_a_non_int_arg",
        "SPT4008",
        """
@contracterror
class E:
    BadThing = errorcode("7")  # HERE
""",
    ),
    (
        "error_code_out_of_range",
        "SPT4009",
        """
@contracterror
class E:
    BadThing = errorcode(0xFFFFFF00)  # HERE
""",
    ),
    (
        "duplicate_error_code",
        "SPT4010",
        """
@contracterror
class E:
    First = errorcode(1)
    Second = errorcode(1)  # HERE
""",
    ),
    (
        "empty_error_enum",
        "SPT4011",
        """
@contracterror
class E:  # HERE
    pass
""",
    ),
    (
        "non_chain_field_annotation",
        "SPT4012",
        """
@contracttype
class S:
    amount: int  # HERE
""",
    ),
    (
        "event_without_the_event_base",
        "SPT4014",
        """
@contractevent
class T:  # HERE
    amount: U32
""",
    ),
    (
        "method_name_too_long",
        "SPT5001",
        """
@contract
class C2:
    def aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa(self, env: Env) -> U32:  # HERE
        return U32(0)
""",
    ),
    (
        "field_name_outside_the_symbol_charset",
        "SPT5001",
        """
@contracttype
class S:
    caf\u00e9: U32  # HERE
""",
    ),
    (
        "unresolvable_class_annotation",
        "SPT2003",
        """
@contracttype
class S:
    amount: Missing  # HERE
""",
    ),
    (
        "unresolvable_method_annotation",
        "SPT2003",
        """
@contract
class C2:
    def go(self, env: Env, amount: Missing) -> U32:  # HERE
        return U32(0)
""",
    ),
]


@pytest.mark.parametrize(
    ("code", "body"),
    [(code, body) for _, code, body in BRIDGING_MATRIX],
    ids=[name for name, _, _ in BRIDGING_MATRIX],
)
def test_bridging_matrix_produces_a_located_diagnostic(code: str, body: str) -> None:
    src = source(body)
    diagnostic = expect_at_here(src, code)
    # The bridged diagnostic keeps the decorator's own explanation as a note,
    # so nothing the author needs is lost when the message is normalized to
    # the registry's stable wording.
    assert diagnostic.notes, "a bridged diagnostic must carry the original message as a note"


@pytest.mark.parametrize(
    ("code", "body"),
    [(code, body) for _, code, body in BRIDGING_MATRIX],
    ids=[name for name, _, _ in BRIDGING_MATRIX],
)
def test_bridging_matrix_messages_carry_the_registry_wording(code: str, body: str) -> None:
    intent = next(entry.message_intent for entry in codes.REGISTRY if entry.code == code)
    diagnostic = expect_at_here(source(body), code)
    assert intent in diagnostic.message


def test_a_pep563_module_bridges_the_same_way() -> None:
    # Under PEP 563 the annotation NameError surfaces from
    # `typing.get_type_hints` inside the decorator rather than from the class
    # body, so both paths must land on the same statement.
    src = "from __future__ import annotations\n" + source(
        """
@contracttype
class S:
    amount: Missing  # HERE
"""
    )
    expect_at_here(src, "SPT2003")


def test_a_raw_name_error_is_reported_as_an_unresolved_name() -> None:
    src = source("LIMIT = MISSING_CONST  # HERE")
    expect_at_here(src, "SPT2001")


def test_an_out_of_range_module_constant_is_reported_as_a_literal_range_error() -> None:
    src = source("LIMIT = U32(2 ** 40)  # HERE")
    expect_at_here(src, "SPT3004")


def test_a_cascading_name_error_is_suppressed() -> None:
    # The struct fails, so `S` never binds and the helper referencing it would
    # otherwise produce a second, useless NameError diagnostic.
    src = source(
        """
@contracttype
class S:
    amount: int  # HERE


def widen(env: Env, s: S) -> U32:
    return U32(0)
"""
    )
    found = diags(src)
    assert [d.code for d in found] == ["SPT4012"]


def test_a_self_referential_name_error_is_reported_not_swallowed() -> None:
    # The cascade filter must not treat a statement's OWN unbound name as
    # collateral damage from an earlier failure.
    src = source("LIMIT = LIMIT  # HERE")
    expect_at_here(src, "SPT2001")


REDECLARATIONS = {
    "two_structs": "@contracttype\nclass S:\n    a: U32\n\n\n@contracttype\nclass S:  # HERE\n    b: U32",
    "struct_then_const": "@contracttype\nclass S:\n    a: U32\n\n\nS = U32(1)  # HERE",
    "two_helpers": (
        "def h(env: Env) -> U32:\n    return U32(0)\n\n\n"
        "def h(env: Env) -> U32:  # HERE\n    return U32(1)"
    ),
    "shadowing_an_import": "def U32(env: Env) -> None:  # HERE\n    return None",
}


@pytest.mark.parametrize("body", REDECLARATIONS.values(), ids=REDECLARATIONS.keys())
def test_a_module_level_redeclaration_is_rejected(body: str) -> None:
    # Two top-level declarations of one name would leave the AST view and the
    # executed view pointing at different objects, which the F.1.14
    # cross-check would report as a compiler bug on ordinary user input. The
    # shape check must keep that unreachable from source.
    expect_at_here(source(body), "SPT2004")


def test_a_redeclared_struct_does_not_crash_the_cross_check() -> None:
    src = source("@contracttype\nclass S:\n    a: U32\n\n\n@contracttype\nclass S:\n    b: U32")
    loaded = load(src)  # must not raise CompilerBugError
    assert [d.code for d in loaded.diagnostics.diagnostics] == ["SPT2004"]
    # The surviving declaration is the first one, and it agrees with its node.
    assert [d.name for d in loaded.decorated_types_in_order] == ["S"]
    assert [f[0] for f in loaded.decorated_types_in_order[0].metadata["fields"]] == ["a"]


def test_collect_all_reports_every_independent_declaration_error() -> None:
    src = source(
        """
@contracterror
class E:
    BadThing = 1


@contracttype
class S:
    amount: int
"""
    )
    assert sorted(d.code for d in diags(src)) == ["SPT4008", "SPT4012"]


def test_an_unclassified_decorator_error_is_still_located(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The last-resort path: if `decorators.py` ever grows a check the bridge
    # table does not know, the result must still be a located diagnostic --
    # never a traceback escaping the loader.
    import serpent

    def exploding(cls: Any) -> Any:
        raise ValueError("something entirely new went wrong")

    # Patched on the root package: the loader executes `from serpent import
    # contracttype`, so that is the binding a contract actually reaches.
    monkeypatch.setattr(serpent, "contracttype", exploding)
    src = source("@contracttype\nclass S:  # HERE\n    amount: U32")
    diagnostic = expect_at_here(src, "SPT1037")
    assert "something entirely new went wrong" in " ".join(diagnostic.notes)


def test_an_exception_outside_the_mj4_tuple_is_still_bridged() -> None:
    # MJ-4 names (ValueError, TypeError, NameError) because those are what
    # `decorators.py` raises, but a module-level constant can raise anything
    # its chain type raises. F.2.5 forbids a traceback escaping either way.
    src = source("LIMIT = Symbol('OK').no_such_attribute  # HERE")
    diagnostic = expect_at_here(src, "SPT1037")
    assert "AttributeError" in " ".join(diagnostic.notes)


def test_an_annotated_module_constant_gets_the_plain_assignment_rewrite() -> None:
    src = source("LIMIT: U32 = U32(1)  # HERE")
    diagnostic = expect_at_here(src, "SPT1031")
    assert any("NAME = U32(1)" in note for note in diagnostic.notes)


def test_bridging_never_reports_a_whole_file_location_for_a_statement() -> None:
    # P2/MJ-4: a statement-scoped failure must never fall back to WHOLE_FILE.
    for _name, _code, body in BRIDGING_MATRIX:
        for diagnostic in diags(source(body)):
            if diagnostic.code == "SPT4015":
                continue  # the module-scope "exactly one @contract" fact
            assert diagnostic.loc.kind is LocKind.NODE, diagnostic


def test_a_bridged_diagnostic_renders_the_offending_source_line() -> None:
    src = source(
        """
@contracterror
class E:
    BadThing = 1  # HERE
"""
    )
    diagnostic = expect_at_here(src, "SPT4008")
    rendered = diagnostic.render(src.splitlines())
    assert "BadThing = 1" in rendered
    assert f"contract.py:{_here_line(src)}:" in rendered


# ---------------------------------------------------------------------------
# D. The two `must_reject/shape/` fixtures, through the loader
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "must_reject" / "shape"
_REJECT_RE = re.compile(r"^#\s*serpent:reject\s+(\S+)\s*$", re.MULTILINE)
_MESSAGE_RE = re.compile(r"^#\s*serpent:message\s+(.+?)\s*$", re.MULTILINE)


@pytest.mark.parametrize("name", ["error_member_bare_int.py", "missing_param_annotation.py"])
def test_shape_fixture_produces_its_declared_diagnostic(name: str) -> None:
    """The runner in `test_must_reject.py` stays skipped until Task 10 assembles
    `compile_module`; these two fixtures are shape-level, so the loader alone
    must already produce their declared code, HERE line and message.
    """
    path = FIXTURES_DIR / name
    src = path.read_text()
    reject = _REJECT_RE.search(src)
    message = _MESSAGE_RE.search(src)
    assert reject is not None and message is not None
    expected_line = _here_line(src)

    found = [d for d in diags(src, name) if d.code == reject.group(1)]
    assert len(found) == 1, f"{name}: got {diags(src, name)!r}"
    assert found[0].loc.line == expected_line
    assert message.group(1) in found[0].message


# ---------------------------------------------------------------------------
# E. The inventory cross-check (F.1.14) -- an internal hard error
# ---------------------------------------------------------------------------


def _decl(loaded: LoadedModule, name: str) -> DecoratedDecl:
    for decl in (*loaded.decorated_types_in_order, *loaded.events):
        if decl.name == name:
            return decl
    assert loaded.contract_decl is not None and loaded.contract_decl.name == name
    return loaded.contract_decl


def _mutated(decl: DecoratedDecl, metadata: dict[str, Any]) -> DecoratedDecl:
    return replace(decl, metadata=metadata)


def test_cross_check_passes_on_a_consistent_module() -> None:
    loaded = load(TOKEN_SHAPED)
    _cross_check_inventory(
        [*loaded.decorated_types_in_order, *loaded.events, *filter(None, [loaded.contract_decl])]
    )


CROSS_CHECK_MUTATIONS = {
    "contract_method_dropped": (
        "TokenStyle",
        lambda meta: {**meta, "methods": meta["methods"][:-1]},
    ),
    "contract_method_renamed": (
        "TokenStyle",
        lambda meta: {
            **meta,
            "methods": [("renamed", *rest) for (_n, *rest) in meta["methods"][:1]]
            + meta["methods"][1:],
        },
    ),
    "contract_param_renamed": (
        "TokenStyle",
        lambda meta: {
            **meta,
            "methods": [
                (name, [("renamed", ty) for (_p, ty) in params], ret)
                for (name, params, ret) in meta["methods"]
            ],
        },
    ),
    "struct_field_dropped": ("BalanceKey", lambda meta: {**meta, "fields": []}),
    "struct_field_renamed": (
        "BalanceKey",
        lambda meta: {**meta, "fields": [("renamed", ty) for (_n, ty) in meta["fields"]]},
    ),
    "event_field_dropped": ("Transfer", lambda meta: {**meta, "fields": meta["fields"][:-1]}),
    "error_case_renamed": (
        "TokenError",
        lambda meta: {**meta, "cases": [("Renamed", code) for (_n, code) in meta["cases"]]},
    ),
    "error_code_changed": (
        "TokenError",
        lambda meta: {**meta, "cases": [(name, code + 100) for (name, code) in meta["cases"]]},
    ),
    "error_case_dropped": ("TokenError", lambda meta: {**meta, "cases": meta["cases"][:-1]}),
}


@pytest.mark.parametrize(
    ("name", "mutate"), CROSS_CHECK_MUTATIONS.values(), ids=CROSS_CHECK_MUTATIONS.keys()
)
def test_cross_check_raises_a_compiler_bug_on_skew(name: str, mutate: Any) -> None:
    loaded = load(TOKEN_SHAPED)
    decl = _decl(loaded, name)
    with pytest.raises(CompilerBugError) as exc_info:
        _cross_check_inventory([_mutated(decl, mutate(decl.metadata))])
    assert "compiler bug" in str(exc_info.value)


def test_compiler_bug_error_is_assertion_grade() -> None:
    # It is an internal invariant failure, not a user diagnostic: it must not
    # be catchable as a `ValueError`/`CompileError` alongside real diagnostics.
    assert issubclass(CompilerBugError, AssertionError)
    assert not issubclass(CompilerBugError, ValueError)


def test_the_cross_check_actually_runs_during_load() -> None:
    # Guard against the check being defined but never wired in: monkeypatching
    # it to explode must make a normal load explode too.
    import serpent.compiler.loader as loader_module

    def exploding(decls: Any) -> None:
        raise CompilerBugError("compiler bug: wired")

    original = loader_module._cross_check_inventory
    try:
        loader_module._cross_check_inventory = exploding
        with pytest.raises(CompilerBugError):
            load(TOKEN_SHAPED)
    finally:
        loader_module._cross_check_inventory = original


# ---------------------------------------------------------------------------
# F. Diagnostics hygiene
# ---------------------------------------------------------------------------


def test_every_code_the_loader_emits_is_registered() -> None:
    emitted: set[str] = set()
    sources = [source(body) for _name, _code, body in BRIDGING_MATRIX] + [
        source(body) for body in (*IMPORT_REJECTS.values(), *MODULE_BODY_REJECTS.values())
    ]
    for src in sources:
        emitted.update(d.code for d in diags(src))
    assert emitted <= codes.CODES
    assert emitted, "the sweep produced no diagnostics at all"


def test_the_loader_returns_a_sink_not_a_raised_error_for_shape_problems() -> None:
    loaded = load(source("import os  # HERE"))
    assert isinstance(loaded.diagnostics, Diagnostics)
    assert len(loaded.diagnostics) == 1
    with pytest.raises(CompileError):
        loaded.diagnostics.raise_if_any()
