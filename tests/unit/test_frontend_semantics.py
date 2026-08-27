"""Task 11a: classify `tests/semantics/cases.py` against the real frontend.

`SemCase.frontend` is a second, independent axis over the frozen tier-1
semantics table (BL-3): where each case's `source` lands against
`serpent.compiler.frontend.compile_module`, as opposed to tier 1's plain-
Python execution (`tests/semantics/test_semantics.py`). This module owns the
thin harness that makes `source` -- a bare expression, eval-able at tier 1 --
into something `compile_module` can see at all, plus the properties the
classification must hold end to end:

* every `frontend="rejects"` case draws a LOCATED compile diagnostic;
* every `frontend="accepts"` case compiles, and the compiled IR's final
  expression type equals the operand type F.1.11 says it must (a
  `contract_error`/`trap` case is asserted on compile+type only -- the
  runtime behavior belongs to tier 1 and the differential harness, not here);
* every `frontend="not_expressible"` case carries a one-line reason;
* every accepted case's source stays within the frontend's supported AST node
  inventory (F.2.4), the operator-folding wrinkle included.

`tests/semantics/test_semantics.py` reuses `compile_case` from here for the
F.2.2 tier1_only<->rejects reconciliation, rather than re-deriving the
wrapping (BL-3: one harness, not two that could drift apart).
"""

from __future__ import annotations

import ast
import re

import pytest

import serpent
from serpent.compiler.diagnostics import CompileError, LocKind
from serpent.compiler.expr import fold_literal
from serpent.compiler.frontend import CompiledModule, compile_module
from serpent.compiler.types_ import Ty
from tests.semantics.cases import CASES, SemCase

# --- the harness: one case's `source` -> a compilable module -----------------

#: `serpent.__all__` names a case's `source` may reference (dunders excluded,
#: e.g. `__version__`) -- the same "public root only" scope
#: `tests/semantics/test_semantics.py`'s own eval namespace uses.
_IMPORTABLE: tuple[str, ...] = tuple(
    name for name in serpent.__all__ if name.isidentifier() and not name.startswith("__")
)


def _imports_for(source: str) -> tuple[str, ...]:
    """Every public name `source` mentions, plus `Env`/`contract` (the
    minimal method scaffold below always needs both, and neither is ever
    itself the SUBJECT of a case)."""
    names = {name for name in _IMPORTABLE if re.search(rf"\b{re.escape(name)}\b", source)}
    names.add("Env")
    names.add("contract")
    return tuple(sorted(names))


def wrap_case(source: str) -> str:
    """One case's `source` as the sole statement of a minimal `@contract`
    method.

    `x = <source>` -- never `return <source>` -- because an unannotated
    assignment lets the checker INFER `x`'s type from the expression alone
    (`stmt._assign_to_name`'s `declared=None` path, taken whenever `x` is a
    brand-new local with no annotation): the compiled local's own `ty` IS the
    compiler's answer, extracted after the fact rather than guessed at ahead
    of time to satisfy a declared return type. A bare `<source>` statement
    would not work here instead: `stmt._check_expr_stmt` reports SPT1028 for
    any non-void expression written as a statement on its own ("computing a
    value and throwing it away is always a bug").
    """
    imports = ", ".join(_imports_for(source))
    return (
        f"from serpent import {imports}\n\n\n"
        "@contract\n"
        "class C:\n"
        "    def go(self, env: Env) -> None:\n"
        f"        x = {source}\n"
        "        return\n"
    )


def compile_case(case: SemCase) -> CompiledModule:
    """Compile `case.source` through the real frontend. Raises `CompileError`
    exactly when the wrapped module does not compile -- the caller decides
    what that means for `case.frontend`."""
    return compile_module(wrap_case(case.source), f"semantics/{case.name}.py")


def _local_ty(compiled: CompiledModule, name: str = "x") -> Ty:
    (fn,) = compiled.functions
    for _slot, local_name, ty in fn.locals:
        if local_name == name:
            return ty
    raise AssertionError(f"no local named {name!r} in the compiled function")  # pragma: no cover


# --- the classification partitions --------------------------------------------

_ACCEPTS = [case for case in CASES if case.frontend == "accepts"]
_REJECTS = [case for case in CASES if case.frontend == "rejects"]
_NOT_EXPRESSIBLE = [case for case in CASES if case.frontend == "not_expressible"]


def test_every_case_is_classified_into_exactly_one_partition() -> None:
    assert len(_ACCEPTS) + len(_REJECTS) + len(_NOT_EXPRESSIBLE) == len(CASES)


# --- "rejects": a located compile diagnostic, on the RIGHT code -------------

#: The diagnostic code each rejected case's `source` should draw, independent
#: of whatever the compiler itself reports (fix round 1: asserting only
#: `LocKind.NODE` let a case start failing for an unrelated wrong reason
#: unnoticed -- a compensating passing test either way). Derived from the
#: RULE each source violates -- `expr.py`'s dispatch plus `codes.py`'s own
#: registry text -- not from re-running the case, same non-circularity
#: discipline as `EXPECTED_TY` above:
#: * SPT3003 -- `_check_binop`'s "operands must share the same chain-integer
#:   type" family: cross-width, cross-signedness, a bare bool literal used as
#:   an int operand, AND two operands of different chain types generally
#:   (`Bool(True) + U32(1)` hits this same branch, not a bool-literal check --
#:   neither side is a BARE bool literal, so it falls to the plain
#:   `lhs.ty != rhs.ty` arm).
#: * SPT3004 -- `_coerce_literal`/`_validate_literal`'s out-of-range-or-
#:   invalid literal coercion: an out-of-range int, an empty/too-long
#:   `Symbol`, a wrong-length `Bytes32`/`Bytes64`, a malformed `Address`
#:   strkey -- one code for "this literal is not a valid instance of the
#:   target type", however the oracle class's own validator says so.
#: * SPT3005 -- `_check_binop`/`_check_unaryop`'s omitted-operator and
#:   time-type-has-no-arithmetic branches (`**`, `&`, and `Timepoint`/
#:   `Duration` arithmetic of any shape, D4).
#: * SPT3011 -- `_check_subscript`'s negative-literal-index rule (D6).
#: * SPT3016 -- `_check_compare`'s chain-value-vs-raw-str/bytes-literal-via-==
#:   rule (E13/T4).
#: * SPT1017 -- `_check_call`'s rejected-builtin dispatch (`divmod` is not
#:   `RECOGNIZED_BUILTINS`, so it falls to the generic builtin-call reject).
EXPECTED_CODE: dict[str, str] = {
    "bool_leaks_as_int_operand": "SPT3003",
    "out_of_range_int_operand_rejected": "SPT3004",
    "cross_width_unsigned_add_rejected": "SPT3003",
    "cross_signedness_add_rejected": "SPT3003",
    "pow_operator_omitted": "SPT3005",
    "divmod_operator_omitted": "SPT1017",
    "bitwise_and_operator_omitted": "SPT3005",
    "bool_has_no_arithmetic": "SPT3003",
    "timepoint_plus_duration_rejected": "SPT3005",
    "timepoint_plus_timepoint_rejected": "SPT3005",
    "duration_unary_minus_rejected": "SPT3005",
    "duration_times_int_rejected": "SPT3005",
    "symbol_does_not_coerce_from_str": "SPT3016",
    "bytes_does_not_coerce_from_raw_bytes": "SPT3016",
    "bytes_negative_index_traps": "SPT3011",
    "symbol_empty_rejected": "SPT3004",
    "symbol_too_long_rejected": "SPT3004",
    "bytes32_wrong_length_rejected": "SPT3004",
    "bytes64_wrong_length_rejected": "SPT3004",
    "address_rejects_malformed_strkey": "SPT3004",
}


def test_expected_code_covers_exactly_the_rejects_cases() -> None:
    assert {case.name for case in _REJECTS} == set(EXPECTED_CODE)


@pytest.mark.parametrize("case", _REJECTS, ids=[case.name for case in _REJECTS])
def test_frontend_rejects_cases_are_located_compile_rejects(case: SemCase) -> None:
    with pytest.raises(CompileError) as info:
        compile_case(case)
    diagnostics = info.value.diagnostics
    assert any(d.loc.kind is LocKind.NODE for d in diagnostics), diagnostics
    assert EXPECTED_CODE[case.name] in [d.code for d in diagnostics], diagnostics


# --- "accepts": compiles, and the IR type is the operand type (F.1.11) -------

#: The operand type each accepted case's `source` denotes, independent of
#: whatever the compiler itself computes (that independence is the whole
#: point of asserting it below rather than merely trusting a clean compile).
#: Derived by inspection, case by case:
#: * a `kind="value"` case: the `Ty` of `type(case.expect)` (every `expect` in
#:   this table is `Bool`, `I32`, `U32`, or `Bytes` -- never a container or a
#:   struct).
#: * a `kind="contract_error"`/`"trap"` case with no `expect`: the type the
#:   literal chain-type constructor(s) IN THE SOURCE name directly (checked
#:   arithmetic never widens past the operand type, F.1.11; a container's
#:   `.get`/`[i]` read answers with its declared element/value type, which the
#:   source spells out explicitly too -- `Vec(U32, ...)`, `Map(Symbol, U32)`,
#:   `Bytes[i] -> U32` per `types/buffers.py`'s own `__getitem__` signature).
EXPECTED_TY: dict[str, Ty] = {
    "truthiness_u32_zero_is_false": Ty.Bool,
    "truthiness_u32_nonzero_is_true": Ty.Bool,
    "truncating_floordiv_negative_dividend": Ty.I32,
    "truncating_mod_takes_dividend_sign": Ty.I32,
    "min_mod_neg1_is_zero_not_a_trap": Ty.I32,
    "reflected_rfloordiv_truncates": Ty.I32,
    "reflected_rmod_truncates": Ty.I32,
    "i32_min_floordiv_neg1_overflows": Ty.I32,
    "u32_unary_minus_of_one_overflows": Ty.U32,
    "u32_max_plus_one_overflows": Ty.U32,
    "u32_zero_minus_one_underflows": Ty.U32,
    "i32_max_plus_one_overflows": Ty.I32,
    "i32_min_minus_one_overflows": Ty.I32,
    "i32_min_negated_overflows": Ty.I32,
    "u64_max_plus_one_overflows": Ty.U64,
    "i64_min_minus_one_overflows": Ty.I64,
    "u128_max_plus_one_overflows": Ty.U128,
    "i128_min_minus_one_overflows": Ty.I128,
    "error_roundtrip_contract_error_code": Ty.I32,
    "unary_minus_ordinary_value": Ty.I32,
    "unary_minus_of_unsigned_zero_stays_in_range": Ty.U128,
    "reflected_add_int_plus_chain_int": Ty.U32,
    "reflected_sub_int_minus_chain_int": Ty.U32,
    "reflected_mul_int_times_chain_int": Ty.U32,
    "int_operand_accepted_in_range": Ty.U32,
    "floordiv_by_zero_traps": Ty.I32,
    "mod_by_zero_traps": Ty.I32,
    "unsigned_floordiv_by_zero_traps": Ty.U32,
    "bytes32_equals_bytes_same_payload": Ty.Bool,
    "map_orders_same_type_keys_by_payload": Ty.U32,
    "symbol_underscore_vs_A_ascii_order": Ty.Bool,
    "bytes_positive_out_of_range_traps": Ty.U32,
    "vec_get_out_of_bounds_traps": Ty.U32,
    "map_get_missing_key_traps": Ty.U32,
    "address_account_orders_before_contract": Ty.Bool,
}


def test_expected_ty_covers_exactly_the_accepted_cases() -> None:
    assert {case.name for case in _ACCEPTS} == set(EXPECTED_TY)


@pytest.mark.parametrize("case", _ACCEPTS, ids=[case.name for case in _ACCEPTS])
def test_frontend_accepts_cases_compile_with_the_operand_type(case: SemCase) -> None:
    compiled = compile_case(case)
    assert _local_ty(compiled) == EXPECTED_TY[case.name]


# --- "not_expressible": a one-line reason, always ----------------------------


def test_not_expressible_cases_carry_a_one_line_reason() -> None:
    for case in _NOT_EXPRESSIBLE:
        reason = case.not_expressible_reason
        assert reason is not None and reason.strip(), case.name
        assert "\n" not in reason, case.name


# --- F.2.4: the AST-allowlist property over every accepted source ------------

#: The frontend's supported expression-node inventory (`expr.check_expr`'s own
#: exhaustive dispatch, MJ-11), plus `List`/`Tuple` for the container-literal
#: argument shapes (`Vec(U32, [...])`, `Map(K, V, [(k, v), ...])`) that never
#: reach `check_expr` themselves -- `recognize.py`'s construction readers walk
#: them directly.
_SUPPORTED_EXPR_KINDS: frozenset[type[ast.expr]] = frozenset(
    {
        ast.Constant,
        ast.Name,
        ast.Attribute,
        ast.Call,
        ast.BinOp,
        ast.UnaryOp,
        ast.Compare,
        ast.BoolOp,
        ast.IfExp,
        ast.Subscript,
        ast.List,
        ast.Tuple,
    }
)


def _assert_supported_kind(node: ast.expr) -> None:
    assert type(node) in _SUPPORTED_EXPR_KINDS, (
        f"{type(node).__name__} is not in the frontend's supported expression "
        f"inventory: {ast.dump(node)}"
    )


def _walk_allowed(node: ast.expr) -> None:
    """Walk `node`'s tree, asserting every node kind is supported.

    A `Call`'s own children are walked SPECIALLY (`_walk_call_argument`):
    every other node kind just recurses into its `ast.expr` children plain,
    since `ast.iter_child_nodes` already skips the non-`expr` fields
    (`ast.operator`/`ast.cmpop`/`ast.unaryop`/`ast.boolop`/`ast.expr_context`
    are a separate node hierarchy the inventory need not name at all).

    Fix round 1: a `BinOp`/`UnaryOp` reached HERE (i.e. NOT as a `Call`'s
    direct argument, `_walk_call_argument`'s own position) must have a
    chain-type ANCHOR somewhere in its subtree -- a `Call`/`Attribute`/
    `Subscript`/`Name` that keeps it from folding down to a bare
    plain-Python literal. A node that fully folds
    (`fold_literal(node) is not None`) has no such anchor: it is the `1 + 2`
    shape, which `_coerce_literal` rejects with SPT3008 ("no chain type in
    this position") the instant it is not a `Call` argument -- there is no
    OTHER position in the real grammar where a fully-literal BinOp/UnaryOp
    stands on its own (the one other place a literal borrows an `expected`
    type, the non-literal side of a mixed binary op, is not "fully literal"
    in the first place: the compiler only reaches that arm when EXACTLY ONE
    side is literal). Rejecting the fully-foldable shape everywhere but a
    call argument is therefore what "not blanket-permit BinOp" actually
    requires, not just documents.
    """
    _assert_supported_kind(node)
    if isinstance(node, (ast.BinOp, ast.UnaryOp)) and fold_literal(node) is not None:
        raise AssertionError(
            f"{type(node).__name__} folds to a bare plain-Python literal with no chain-type "
            f"anchor outside a Call argument -- compile_module rejects this shape (SPT3008, "
            f"'has no chain type in this position'): {ast.dump(node)}"
        )
    if isinstance(node, ast.Call):
        _assert_supported_kind(node.func)
        if isinstance(node.func, ast.Attribute):
            _walk_allowed(node.func.value)
        for arg in node.args:
            _walk_call_argument(arg)
        for kw in node.keywords:
            _walk_call_argument(kw.value)
        return
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.expr):
            _walk_allowed(child)


def _walk_call_argument(node: ast.expr) -> None:
    """A `Call`'s direct argument: `fold_literal`'s own position (S3/F.1.10).

    LEDGERED CONSTRAINT (task-11a-brief.md): a `BinOp`/`UnaryOp` here is
    `U32(2**32 - 1)`'s shape -- a plain-Python literal spelling the compiler
    folds BEFORE any chain-arithmetic rule ever sees it
    (`expr._check_chain_constructor` tries the literal path first) -- so it is
    legal here whenever `fold_literal` accepts the WHOLE subtree, without its
    own operands needing to appear in the inventory separately. That is
    PERMIT, not blanket-permit: a `BinOp`/`UnaryOp` argument that does not
    fold (an actual chain-typed sub-expression, e.g. a future `U32(a + b)`)
    falls through to the plain walk below, where it is checked -- and
    permitted -- as ordinary chain arithmetic instead.
    """
    if isinstance(node, (ast.BinOp, ast.UnaryOp)) and fold_literal(node) is not None:
        _assert_supported_kind(node)
        return
    _walk_allowed(node)


@pytest.mark.parametrize("case", _ACCEPTS, ids=[case.name for case in _ACCEPTS])
def test_accepted_case_sources_stay_within_the_ast_allowlist(case: SemCase) -> None:
    tree = ast.parse(case.source, mode="eval")
    _walk_allowed(tree.body)


# --- fix round 1: the walker must actually DISCRIMINATE, not blanket-permit --


def test_the_walker_rejects_bare_literal_arithmetic_outside_a_call_argument() -> None:
    """`1 + 2` is a `BinOp`, so a flat "BinOp is a supported kind" allowlist
    would wave it through -- exactly the false confidence the ledgered
    constraint warns about. It is not a `Call` argument (`_walk_call_
    argument`'s own position, where `U32(2**32 - 1)`'s identical SHAPE is
    legal), so `_walk_allowed` must reject it directly."""
    tree = ast.parse("1 + 2", mode="eval")
    with pytest.raises(AssertionError):
        _walk_allowed(tree.body)


def test_the_walker_agrees_with_a_real_compile_reject_on_bare_literal_arithmetic() -> None:
    """The discrimination probe, tied to the real compiler decision it
    claims to model: `x = 1 + 2` (the walker's `1 + 2`, wrapped exactly the
    way `wrap_case` wraps every case) draws `compile_module`'s own SPT3008
    ("has no chain type in this position") -- the same reject the walker's
    `AssertionError` above is standing in for."""
    with pytest.raises(CompileError) as info:
        compile_module(wrap_case("1 + 2"), "semantics/probe_bare_literal_arithmetic.py")
    assert "SPT3008" in [d.code for d in info.value.diagnostics]
