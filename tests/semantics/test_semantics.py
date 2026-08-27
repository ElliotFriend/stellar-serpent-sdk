"""Runs every `SemCase` in `cases.py`: the tier-1 half of the cross-tier oracle.

Every case's `source` is `eval`-ed against a namespace built ONLY from
`serpent.__all__` -- the public root, and nothing else (no test helpers, no
submodule internals) -- which is what makes this run both a semantics check
and a second, independent proof (alongside `test_public_api.py` and the
`tests/fixtures/token_style.py` fixture) that the public root is a complete
authoring surface.
"""

import pytest

import serpent
from serpent.compiler.diagnostics import CompileError
from serpent.errors import ContractError
from tests.semantics.cases import CASES, SemCase
from tests.unit.test_frontend_semantics import compile_case

#: `serpent.__all__` names ONLY. `eval`'s two-argument form still lets Python
#: inject `__builtins__` (needed for `bool`, `True`/`False` literals, and
#: negative-int literals in a couple of sources below); nothing serpent-side
#: beyond the public root is added.
_NAMESPACE = {name: getattr(serpent, name) for name in serpent.__all__}


def _eval_case(source: str) -> object:
    return eval(source, dict(_NAMESPACE))


def test_semantics_table_has_at_least_forty_cases() -> None:
    assert len(CASES) >= 40


def test_semantics_table_case_names_are_unique() -> None:
    names = [case.name for case in CASES]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_semantics_case(case: SemCase) -> None:
    if case.kind == "value":
        result = _eval_case(case.source)
        assert result == case.expect
    elif case.kind == "contract_error":
        with pytest.raises(ContractError) as exc_info:
            _eval_case(case.source)
        assert exc_info.value.code == case.code
    elif case.kind == "trap" or case.kind == "reject":
        assert case.trap is not None
        with pytest.raises(case.trap):
            _eval_case(case.source)
    else:  # pragma: no cover - Literal exhausts the real cases
        pytest.fail(f"unknown SemCase kind: {case.kind!r}")


# --- meta-test: tier1_only <-> a real frontend reject (F.2.2) ----------------

# Task 11a retires the three placeholder regexes that used to stand in here
# ("sub-plan C's real frontend checks are what will actually enforce these",
# the retired meta-test's own docstring): `compile_case` (the Task 11a
# harness, `tests/unit/test_frontend_semantics.py`) now runs the real
# frontend, so the reconciliation below is exact rather than approximated.
#
# `kind == "reject"` cases are excluded: they are tier-1-only BY CONSTRUCTION
# (this module's own docstring, "authoring-time misuse... before any chain
# semantics are exercised"), so `tier1_only` is never required for them.
# `frontend == "not_expressible"` cases are excluded too -- neither side of
# the biconditional is meaningful for a case the compiler cannot probe for
# the reason it was written to pin (see each one's `not_expressible_reason`).
_F22_CASES = [
    case for case in CASES if case.kind != "reject" and case.frontend != "not_expressible"
]


@pytest.mark.parametrize("case", _F22_CASES, ids=[case.name for case in _F22_CASES])
def test_tier1_only_matches_a_frontend_reject(case: SemCase) -> None:
    """F.2.2: `tier1_only` holds if and only if the frontend rejects `source`."""
    try:
        compile_case(case)
        compiled = True
    except CompileError:
        compiled = False
    assert case.tier1_only == (not compiled), (
        f"{case.name}: tier1_only={case.tier1_only} but the frontend "
        f"{'accepted' if compiled else 'rejected'} it -- the table is frozen, "
        "so this mismatch is a controller decision, not something to silently "
        "reflag"
    )
