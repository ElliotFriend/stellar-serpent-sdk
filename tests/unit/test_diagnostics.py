"""Tests for `serpent.compiler.diagnostics` and `serpent.compiler.codes`.

Task 1 of the M1-C plan: the diagnostics core (`Loc`/`Diagnostic`/
`Diagnostics`/`CompileError`) and the COMPLETE `SPT####` code registry, which
is this task's primary deliverable and review gate (BL-4). See
docs/superpowers/specs/2026-08-27-m1c-inputs-dossier.md SS D.1-D.2 for the
format this is implementing, and the task brief for the derivation rule.
"""

from __future__ import annotations

import ast
import re

import pytest

from serpent.compiler import codes
from serpent.compiler.diagnostics import (
    CompileError,
    Diagnostic,
    Diagnostics,
    Loc,
    LocKind,
)
from serpent.errors import (
    CODE_ABI_CHECK_FAILED,
    CODE_ARITHMETIC_OVERFLOW,
    CODE_BAD_ARGUMENT,
    CODE_MISSING_VALUE,
    CODE_UNREACHABLE_GUARD,
    CODE_UNSUPPORTED_AT_RUNTIME,
    RESERVED_CODE_MIN,
)

# --- Loc ----------------------------------------------------------------


def test_loc_from_node_captures_full_span() -> None:
    tree = ast.parse("x = [a for a in b]\n")
    comp = tree.body[0].value  # type: ignore[attr-defined]
    loc = Loc.from_node("contracts/token.py", comp)
    assert loc.kind is LocKind.NODE
    assert loc.path == "contracts/token.py"
    assert (loc.line, loc.col) == (1, 4)
    assert (loc.end_line, loc.end_col) == (1, 18)


def test_loc_whole_file() -> None:
    loc = Loc.whole_file("contracts/token.py")
    assert loc.kind is LocKind.WHOLE_FILE
    assert loc.path == "contracts/token.py"
    assert loc.line == 0 and loc.col == 0


def test_loc_is_frozen_and_hashable() -> None:
    loc = Loc.whole_file("a.py")
    with pytest.raises(AttributeError):
        loc.line = 5  # type: ignore[misc]
    assert hash(loc) == hash(loc)  # must be hashable


# --- Diagnostic rendering (goldens) --------------------------------------


def test_render_node_golden() -> None:
    source = "def totals(self, env, v):\n    return [x for x in v]\n"
    lines = source.splitlines()
    tree = ast.parse(source)
    comp = tree.body[0].body[0].value  # type: ignore[attr-defined]
    loc = Loc.from_node("contracts/token.py", comp)
    diag = Diagnostic(
        code="SPT1003",
        loc=loc,
        message="comprehensions are not supported",
        help="build the container explicitly, e.g. Vec(U32, [...]), or fill it in a while loop",
        notes=("the supported subset is documented at docs/subset.md#comprehensions",),
    )
    rendered = diag.render(lines)
    assert rendered == (
        "contracts/token.py:2:12: error[SPT1003]: comprehensions are not supported\n"
        "    2 |    return [x for x in v]\n"
        "      |           ^^^^^^^^^^^^^^\n"
        "   help: build the container explicitly, e.g. Vec(U32, [...]), or fill it in a "
        "while loop\n"
        "   note: the supported subset is documented at docs/subset.md#comprehensions"
    )


def test_render_whole_file_golden() -> None:
    loc = Loc.whole_file("contracts/token.py")
    diag = Diagnostic(
        code="SPT4001",
        loc=loc,
        message="expected exactly one @contract class",
        help=None,
    )
    rendered = diag.render(["irrelevant"])
    assert rendered == "contracts/token.py: error[SPT4001]: expected exactly one @contract class"
    assert "help" not in rendered


def test_render_whole_file_with_help_and_notes() -> None:
    loc = Loc.whole_file("a.py")
    diag = Diagnostic(
        code="SPT4001",
        loc=loc,
        message="msg",
        help="do the thing",
        notes=("a note",),
    )
    rendered = diag.render([])
    assert rendered == "a.py: error[SPT4001]: msg\n   help: do the thing\n   note: a note"


def test_diagnostic_is_frozen() -> None:
    diag = Diagnostic(code="SPT1001", loc=Loc.whole_file("a.py"), message="m", help="h")
    with pytest.raises(AttributeError):
        diag.message = "other"  # type: ignore[misc]


# --- Diagnostics sink -----------------------------------------------------


def test_sink_error_requires_help_for_spt1xxx() -> None:
    sink = Diagnostics()
    with pytest.raises(ValueError, match="help"):
        sink.error("SPT1001", Loc.whole_file("a.py"), "nested functions are not supported")


def test_sink_error_allows_missing_help_outside_spt1xxx() -> None:
    sink = Diagnostics()
    sink.error("SPT2001", Loc.whole_file("a.py"), "unresolved name 'foo'")
    assert len(sink.diagnostics) == 1


def test_sink_error_accepts_spt1xxx_with_help() -> None:
    sink = Diagnostics()
    sink.error(
        "SPT1001",
        Loc.whole_file("a.py"),
        "nested functions are not supported",
        help="flatten it into a module-level helper",
    )
    assert len(sink.diagnostics) == 1


def test_sink_extend_merges_another_sink() -> None:
    a = Diagnostics()
    a.error("SPT2001", Loc.whole_file("a.py"), "one")
    b = Diagnostics()
    b.error("SPT2002", Loc.whole_file("a.py"), "two", help=None)
    a.extend(b)
    assert len(a.diagnostics) == 2


def test_sink_raise_if_any_noop_when_empty() -> None:
    sink = Diagnostics()
    sink.raise_if_any()  # must not raise


def test_sink_raise_if_any_sorts_by_loc() -> None:
    sink = Diagnostics()
    late = Loc(path="a.py", kind=LocKind.NODE, line=20, col=0, end_line=20, end_col=1)
    early = Loc(path="a.py", kind=LocKind.NODE, line=5, col=0, end_line=5, end_col=1)
    sink.error("SPT2001", late, "late one")
    sink.error("SPT2002", early, "early one")
    with pytest.raises(CompileError) as exc_info:
        sink.raise_if_any()
    err = exc_info.value
    assert [d.loc.line for d in err.diagnostics] == [5, 20]
    assert isinstance(err, ValueError)


def test_compile_error_render_joins_diagnostics() -> None:
    sink = Diagnostics()
    sink.error("SPT2001", Loc.whole_file("a.py"), "first")
    sink.error("SPT2002", Loc.whole_file("a.py"), "second")
    try:
        sink.raise_if_any()
    except CompileError as exc:
        rendered = exc.render([])
        assert "first" in rendered and "second" in rendered
        assert rendered.index("first") < rendered.index("second")
    else:
        pytest.fail("expected CompileError")


# --- codes.py: the complete registry --------------------------------------

_CODE_RE = re.compile(r"^SPT([1-7])\d{3}$")


def test_registry_codes_are_unique() -> None:
    seen = [entry.code for entry in codes.REGISTRY]
    assert len(seen) == len(set(seen))


def test_registry_codes_are_band_prefixed_correctly() -> None:
    for entry in codes.REGISTRY:
        m = _CODE_RE.match(entry.code)
        assert m, f"{entry.code} is not a well-formed SPT#### code"
        assert entry.band == f"SPT{m.group(1)}xxx", (entry.code, entry.band)


def test_registry_rows_name_an_owning_task() -> None:
    for entry in codes.REGISTRY:
        assert entry.owning_task.strip(), f"{entry.code} has no owning task"


def test_registry_rows_name_a_construct_and_message_intent() -> None:
    for entry in codes.REGISTRY:
        assert entry.construct.strip()
        assert entry.message_intent.strip()


def test_registry_is_complete_not_a_sample() -> None:
    # ~55 is the dossier's own approximate count (F.3); the task brief is
    # explicit this is a floor, not a target ("not >= 40"). See
    # docs.../task-1-report.md for the row-by-row derivation.
    assert len(codes.REGISTRY) >= 50


def test_no_fixture_allowlist_is_subset_of_registry() -> None:
    registry_codes = {entry.code for entry in codes.REGISTRY}
    assert codes.NO_FIXTURE_ALLOWLIST <= registry_codes


def test_no_fixture_allowlist_reasons_cover_exactly_the_allowlist() -> None:
    assert set(codes.NO_FIXTURE_REASONS) == codes.NO_FIXTURE_ALLOWLIST
    for code, reason in codes.NO_FIXTURE_REASONS.items():
        assert reason.strip(), code


def test_no_fixture_allowlist_seeds_the_protocol_gate_code() -> None:
    # Task 10: SPT6xxx has no gated authoring surface at M1-C.
    protocol_codes = {entry.code for entry in codes.REGISTRY if entry.band == "SPT6xxx"}
    assert protocol_codes
    assert protocol_codes <= codes.NO_FIXTURE_ALLOWLIST


def test_codes_validate_passes_on_the_real_registry() -> None:
    codes.validate()  # must not raise


def test_codes_validate_rejects_a_duplicate_code(monkeypatch: pytest.MonkeyPatch) -> None:
    bogus = codes.REGISTRY + (codes.REGISTRY[0],)
    monkeypatch.setattr(codes, "REGISTRY", bogus)
    with pytest.raises(ValueError, match="duplicate"):
        codes.validate()


def test_codes_validate_rejects_an_allowlist_code_missing_from_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codes, "NO_FIXTURE_ALLOWLIST", frozenset({"SPT9999"}))
    monkeypatch.setattr(codes, "NO_FIXTURE_REASONS", {"SPT9999": "bogus"})
    with pytest.raises(ValueError, match="SPT9999"):
        codes.validate()


# --- errors.py: reserved runtime codes (E14 complete) ---------------------


def test_reserved_codes_are_present_unique_and_in_band() -> None:
    reserved = {
        CODE_BAD_ARGUMENT,
        CODE_ARITHMETIC_OVERFLOW,
        CODE_MISSING_VALUE,
        CODE_UNREACHABLE_GUARD,
        CODE_ABI_CHECK_FAILED,
        CODE_UNSUPPORTED_AT_RUNTIME,
    }
    assert len(reserved) == 6, "reserved codes must be pairwise unique"
    for code in reserved:
        assert RESERVED_CODE_MIN <= code <= 0xFFFF_FFFF


def test_reserved_codes_exact_values() -> None:
    assert CODE_MISSING_VALUE == 0xFFFF_FFFD
    assert CODE_UNREACHABLE_GUARD == 0xFFFF_FFFC
    assert CODE_ABI_CHECK_FAILED == 0xFFFF_FFFB
    assert CODE_UNSUPPORTED_AT_RUNTIME == 0xFFFF_FFFA


def test_reserved_codes_documented_in_module_docstring() -> None:
    import serpent.errors as errors_mod

    doc = errors_mod.__doc__ or ""
    for name in (
        "CODE_MISSING_VALUE",
        "CODE_UNREACHABLE_GUARD",
        "CODE_ABI_CHECK_FAILED",
        "CODE_UNSUPPORTED_AT_RUNTIME",
    ):
        assert name in doc, f"{name} missing from the errors module docstring table"
