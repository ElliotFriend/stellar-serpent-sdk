"""The AUTHORING-TIME half of the union/enum surface: what `mypy --strict` says.

The headline claim of ruling E1's descriptor surface is that an author's
mistakes -- the wrong payload type, the wrong arity, calling a unit variant,
using one union where another kind is declared -- are caught by `mypy --strict`
with **no plugin**. That claim is only worth as much as the checks that pin it,
and it cannot be pinned by an ordinary tracked file: gate 2 runs
`mypy --strict` with no path arguments over `src`, `tests` and `examples`
(`pyproject.toml`), so a tracked file containing deliberate type errors would
fail the gate itself.

So the surface is pinned from both ends (review B5):

* the POSITIVE half is `tests/fixtures/udt_style.py`, an ordinary fixture whose
  cleanliness gate 2 asserts by configuration -- no subprocess, no assertion
  here;
* the NEGATIVE half is the snippet below, written to `tmp_path` at test time
  and fed to a subprocess `mypy --strict`. It is ONE file with one function per
  mistake, so the whole set costs a single interpreter start.

`run_mypy` is the shared helper; it is imported by later tasks rather than
copied.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: `snippet.py:12: error: Too few arguments  [call-arg]`
_ERROR_RE = re.compile(r"^.*?:(\d+): error: .*\[([a-z-]+)\]\s*$")


def _parse(stdout: str) -> list[tuple[int, str]]:
    """Every `(line, error-code)` mypy reported, in order.

    A diagnostic with no `[code]` suffix (a syntax error, an internal failure)
    is deliberately NOT dropped silently: it comes back as `(line, "")`, so a
    snippet that fails for a reason nobody expected still shows up in an
    assertion instead of reading as "no errors".
    """
    pairs: list[tuple[int, str]] = []
    for line in stdout.splitlines():
        if ": error: " not in line:
            continue
        match = _ERROR_RE.match(line)
        if match is None:
            number, _, _ = line.partition(": error: ")
            pairs.append((int(number.rsplit(":", 1)[-1] or 0), ""))
        else:
            pairs.append((int(match.group(1)), match.group(2)))
    return pairs


def run_mypy(source: str, tmp_path: Path) -> list[tuple[int, str]]:
    """`(line, error-code)` pairs for one snippet, checked in isolation.

    An explicit path argument OVERRIDES pyproject's `files`, so this checks the
    snippet and not the repo; `cwd=_REPO_ROOT` is what makes `import serpent`
    resolve. The cost is real -- one interpreter start per call, seconds-scale
    -- so callers batch their snippets into as few files as the assertions
    allow.
    """
    snippet = tmp_path / "snippet.py"
    snippet.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--no-incremental",
            "--hide-error-context",
            "--no-color-output",
            str(snippet),
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return _parse(result.stdout)


_ONE_KNOWN_ERROR = '''\
"""A snippet with exactly one error, for `run_mypy`'s own smoke test."""

from serpent.types import U32, Symbol


def wrong() -> U32:
    return Symbol("nope")
'''


def test_run_mypy_reports_the_one_error_a_known_bad_snippet_has(tmp_path: Path) -> None:
    """The helper's own smoke test: a misconfigured invocation -- a path mypy
    cannot resolve, a `cwd` where `serpent` is not importable, an output format
    `_parse` does not match -- would report NOTHING and make every negative
    assertion below vacuously green. This is what makes that impossible."""
    assert run_mypy(_ONE_KNOWN_ERROR, tmp_path) == [(7, "return-value")]


# --- the negative surface ----------------------------------------------------

#: The five author mistakes, each in its own function so its declared return
#: type can be the one that makes the mistake expressible: rows 1-4 return the
#: union they build, and row 5 declares the OTHER kind, which is the whole
#: point of that row.
_NEGATIVE_SOURCE = '''\
"""Every mistake the descriptor surface must catch, one per function."""

from serpent.types import U32, ContractEnum, ContractUnion, Symbol, enumvalue, variant


class Shape(ContractUnion):
    Empty = variant()
    Circle = variant(U32)
    Rect = variant(U32, U32)


class Color(ContractEnum):
    Red = enumvalue(0)


def wrong_payload_type() -> Shape:
    return Shape.Circle(Symbol("nope"))


def too_few_arguments() -> Shape:
    return Shape.Rect(U32(1))


def too_many_arguments() -> Shape:
    return Shape.Circle(U32(1), U32(2))


def calling_a_unit_variant() -> Shape:
    return Shape.Empty()


def one_kind_where_the_other_is_declared() -> Color:
    return Shape.Empty
'''


@pytest.fixture(scope="module")
def negative_errors(tmp_path_factory: pytest.TempPathFactory) -> list[tuple[int, str]]:
    """Every error in `_NEGATIVE_SOURCE`, from ONE mypy run (the helper's
    docstring explains why batching matters)."""
    return run_mypy(_NEGATIVE_SOURCE, tmp_path_factory.mktemp("authoring_types"))


#: The mistake, and the mypy error code it must produce.
_ROWS: list[tuple[str, str]] = [
    ('return Shape.Circle(Symbol("nope"))', "arg-type"),  # wrong payload type
    ("return Shape.Rect(U32(1))", "call-arg"),  # too few arguments
    ("return Shape.Circle(U32(1), U32(2))", "call-arg"),  # too many arguments
    ("return Shape.Empty()", "operator"),  # calling a unit variant
    ("return Shape.Empty", "return-value"),  # a Shape where a Color is declared
]


def _line_of(line_source: str) -> int:
    """The 1-based line `line_source` sits on, pinned to exactly one match so a
    reworded snippet cannot silently start asserting about the wrong line."""
    lines = _NEGATIVE_SOURCE.splitlines()
    matches = [index + 1 for index, text in enumerate(lines) if text.strip() == line_source]
    assert len(matches) == 1, f"{line_source!r} is not on exactly one line of the snippet"
    return matches[0]


@pytest.mark.parametrize(("line_source", "code"), _ROWS)
def test_the_descriptor_surface_catches_the_author_mistakes(
    line_source: str, code: str, negative_errors: list[tuple[int, str]]
) -> None:
    """§C.8's four probed rows plus the cross-kind one. The POSITIVE half of
    this surface is tests/fixtures/udt_style.py, whose cleanliness gate 2
    asserts by configuration (B5) -- these five are the only snippets that
    cannot live in a tracked file."""
    line = _line_of(line_source)
    codes = [reported for reported_line, reported in negative_errors if reported_line == line]
    assert code in codes, f"line {line} reported {codes}, not {code}"


def test_the_negative_snippet_is_wrong_in_exactly_five_places(
    negative_errors: list[tuple[int, str]],
) -> None:
    """Every reported line is one of the five, so a sixth error -- a typo in the
    snippet, a broken export, a declaration the surface refuses for a reason
    nobody meant -- cannot hide inside a green run."""
    assert {line for line, _ in negative_errors} == {_line_of(source) for source, _ in _ROWS}


# --- Task 7: the `get` overload set's negative half (fed item X4, E12) ------

#: The two mismatches the four `@overload`s must still catch, alongside
#: `tests/fixtures/get_default_typing.py`'s five accepts (B5's split: a
#: tracked file cannot be both clean and wrong). Both surface as
#: `[return-value]`, not `[arg-type]`/`[call-overload]` at the argument: an
#: unconstrained `_T` always has a join to fall back to (`I32`/`U32`'s common
#: `_ChainArith`, or plain `object` for two unrelated chain types), so the
#: matching arm accepts the call and the mismatch only shows up where that
#: joined `_T` meets the function's own declared return type -- the same
#: shape the RED case above has, and the same correction of the fed item's
#: own "arg-type" description.
_GET_MISMATCH_SOURCE = '''\
"""The two mismatches the get overload set must still catch."""

from serpent import I32, U32, Env, Symbol


def mismatched_chain_value_default(env: Env, key: Symbol) -> U32:
    return env.storage().persistent().get(key, U32, default=I32(0))


def mismatched_return_type(env: Env, key: Symbol) -> U32:
    return env.storage().persistent().get(key, Symbol)
'''


@pytest.fixture(scope="module")
def get_mismatch_errors(tmp_path_factory: pytest.TempPathFactory) -> list[tuple[int, str]]:
    """Every error in `_GET_MISMATCH_SOURCE`, from ONE mypy run."""
    return run_mypy(_GET_MISMATCH_SOURCE, tmp_path_factory.mktemp("get_overloads"))


#: The mistake, and the mypy error code it must produce.
_GET_ROWS: list[tuple[str, str]] = [
    ("return env.storage().persistent().get(key, U32, default=I32(0))", "return-value"),
    ("return env.storage().persistent().get(key, Symbol)", "return-value"),
]


def _line_of_get(line_source: str) -> int:
    """`_line_of`'s counterpart for `_GET_MISMATCH_SOURCE`, pinned to exactly
    one match for the same reason."""
    lines = _GET_MISMATCH_SOURCE.splitlines()
    matches = [index + 1 for index, text in enumerate(lines) if text.strip() == line_source]
    assert len(matches) == 1, f"{line_source!r} is not on exactly one line of the snippet"
    return matches[0]


@pytest.mark.parametrize(("line_source", "code"), _GET_ROWS)
def test_the_get_overloads_still_catch_the_mismatches(
    line_source: str, code: str, get_mismatch_errors: list[tuple[int, str]]
) -> None:
    """The POSITIVE half of this surface is
    `tests/fixtures/get_default_typing.py`, whose cleanliness gate 2 asserts
    by configuration (B5) -- these two are the only snippets that cannot live
    in a tracked file."""
    line = _line_of_get(line_source)
    codes = [reported for reported_line, reported in get_mismatch_errors if reported_line == line]
    assert code in codes, f"line {line} reported {codes}, not {code}"


def test_the_get_mismatch_snippet_is_wrong_in_exactly_two_places(
    get_mismatch_errors: list[tuple[int, str]],
) -> None:
    """Every reported line is one of the two, so a third error -- an arm
    reordered into `overload-cannot-match`, a join that stops happening --
    cannot hide inside a green run."""
    assert {line for line, _ in get_mismatch_errors} == {
        _line_of_get(source) for source, _ in _GET_ROWS
    }
