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
from serpent.errors import ContractError
from tests.semantics.cases import CASES, SemCase

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
