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


def test_loc_from_node_asserts_on_a_synthetic_node() -> None:
    # A hand-built node with no end_lineno/end_col_offset -- the P2 "never
    # fabricate a location" guard must fire rather than silently emitting
    # Loc(..., end_line=None, ...).
    synthetic = ast.Name(
        id="x", ctx=ast.Load(), lineno=1, col_offset=0, end_lineno=None, end_col_offset=None
    )
    with pytest.raises(AssertionError, match="end_lineno"):
        Loc.from_node("a.py", synthetic)


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
        "    2 |     return [x for x in v]\n"
        "      |            ^^^^^^^^^^^^^^\n"
        "   help: build the container explicitly, e.g. Vec(U32, [...]), or fill it in a "
        "while loop\n"
        "   note: the supported subset is documented at docs/subset.md#comprehensions"
    )


def test_render_multiline_span_caps_carets_at_end_of_first_line() -> None:
    source = "def f(self, env):\n    x = Vec(\n        U32,\n    )\n"
    lines = source.splitlines()
    tree = ast.parse(source)
    call = tree.body[0].body[0].value  # type: ignore[attr-defined]
    loc = Loc.from_node("a.py", call)
    assert loc.end_line != loc.line  # the span really does cross lines
    diag = Diagnostic(code="SPT2001", loc=loc, message="m", help=None)
    rendered = diag.render(lines)
    caret_row = rendered.splitlines()[2]
    first_line_text = lines[loc.line - 1]
    expected_carets = len(first_line_text) - loc.col
    assert caret_row.count("^") == expected_carets
    assert caret_row.endswith("^" * expected_carets)


def test_render_out_of_range_line_falls_back_to_header_only() -> None:
    loc = Loc(path="a.py", kind=LocKind.NODE, line=99, col=0, end_line=99, end_col=1)
    diag = Diagnostic(code="SPT2001", loc=loc, message="m", help=None)
    rendered = diag.render(["only one line"])
    assert rendered == "a.py:99:1: error[SPT2001]: m"


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


def test_sink_error_rejects_an_unregistered_code() -> None:
    sink = Diagnostics()
    with pytest.raises(ValueError, match="SPT9999"):
        sink.error("SPT9999", Loc.whole_file("a.py"), "not a real code")


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

_CODE_RE = re.compile(r"^SPT([1-8])\d{3}$")


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


#: A frozen snapshot of the registry's code set. Deliberately exact and
#: hardcoded (not `len(...) >= N`): deleting, renumbering, or silently
#: adding a row must fail this test loudly, since codes are public API
#: (dossier E17) and `tests/must_reject/` fixtures cite them by exact
#: string starting next task. Update this list -- deliberately -- alongside
#: any registry change, never to make a test pass without reading the diff.
_EXPECTED_CODES = frozenset(
    {
        *(f"SPT1{n:03d}" for n in range(1, 40)),
        *(f"SPT2{n:03d}" for n in range(1, 7)),
        *(f"SPT3{n:03d}" for n in range(1, 21)),
        *(f"SPT4{n:03d}" for n in range(1, 21)),
        *(f"SPT5{n:03d}" for n in range(1, 6)),
        "SPT6001",
        *(f"SPT7{n:03d}" for n in range(1, 6)),
        # M1-D Task 10's CONTROLLER-SANCTIONED emitter band (plan-review B3):
        # the four emitter-side limits `build_wasm` reports.
        *(f"SPT8{n:03d}" for n in range(1, 5)),
    }
)


def test_registry_is_complete_not_a_sample() -> None:
    # ~55 is the dossier's own approximate count (F.3); the task brief is
    # explicit this is a floor, not a target ("not >= 40"). This registry
    # settled at 91 rows after the Task 1 review round added four missing
    # rows (recursion/E8, event-topic-Symbol/S11, a declared-vs-actual type
    # mismatch family, and MJ-11's exhaustive-dispatch catch-all) -- see
    # task-1-report.md for the row-by-row derivation. Task 3's review fix
    # round then added two more by controller ruling: SPT4019 (SS C.3's
    # "exactly one @contract class" module-scope fact, which had no row and
    # was being reported under SPT4015 with a mismatched message) and
    # SPT4020 (decorated-class-body member shape, which was falling to
    # MJ-11's catch-all). Task 5's review fix round added one more, for the
    # same reason: SPT3020 (a chain-type constructor called with the wrong
    # number of arguments, which was also falling to the catch-all). Task
    # 7a's review fix round added one more, SPT1038 (an Env-API attribute
    # referenced without being called/chained, or a structurally malformed
    # recognized call that is neither an arity nor a type mismatch), and
    # widened SPT3020 to cover general recognized-call arity mistakes, not
    # just chain-type constructors (controller ruling, task-7a-fix-round-1).
    # Task 7b's review fix round added the last one, SPT1039 (a map literal
    # repeating a key -- which tier 1 silently swallows and the on-chain
    # literal form cannot represent at all), and fixed SPT1034's wording
    # (controller ruling, task-7b-fix-round-1).
    #
    # M1-D Task 10 appended the SPT8xxx emitter band -- four rows, exactly as
    # the sub-plan's CONTROLLER-SANCTIONED enumeration (plan-review B3) lists
    # them: the three user-visible build limits (`module_size`, `pool`,
    # `scratch`, matching `emitter.frame.BUILD_LIMITS`) plus SPT8004 for a
    # construct the frontend accepts and this emitter version cannot lower
    # yet. Append-only (D15): nothing before SPT8001 moved.
    assert len(codes.REGISTRY) == 100


def test_registry_code_set_matches_the_frozen_snapshot() -> None:
    actual = {entry.code for entry in codes.REGISTRY}
    missing = _EXPECTED_CODES - actual
    extra = actual - _EXPECTED_CODES
    assert not missing, f"codes removed/renumbered out from under the snapshot: {sorted(missing)}"
    assert not extra, f"codes added without updating the snapshot: {sorted(extra)}"


def test_review_round_findings_landed() -> None:
    by_code = {entry.code: entry for entry in codes.REGISTRY}

    # Finding 1: four previously-missing rows.
    recursion = [
        e for e in codes.REGISTRY if e.band == "SPT7xxx" and "recursi" in e.message_intent.lower()
    ]
    assert recursion and recursion[0].owning_task == "Task 8"

    topic_symbol = [
        e
        for e in codes.REGISTRY
        if "topic" in e.construct.lower() and "symbol" in e.message_intent.lower()
    ]
    assert topic_symbol and topic_symbol[0].owning_task == "Task 7a"

    type_mismatch = [
        e
        for e in codes.REGISTRY
        if e.band == "SPT3xxx" and "declared-vs-actual" in e.construct.lower()
    ]
    assert type_mismatch

    catchall = [
        e for e in codes.REGISTRY if "NODE_KIND_CODES" in e.construct and e.band == "SPT1xxx"
    ]
    assert catchall and catchall[0].owning_task == "Task 5"

    # Finding 2: SPT4018 duplicate deleted; SPT5001 widened to cover B11.
    assert "constructor" not in by_code["SPT4018"].construct.lower()
    assert "B11" in by_code["SPT5001"].construct

    # Minor 5: band moves landed (old numbers are gone or repurposed; the
    # content now lives under the new band).
    assert any(
        e.band == "SPT1xxx" and "AnnAssign" in e.construct and "uninitialized" in e.message_intent
        for e in codes.REGISTRY
    )
    assert any(e.band == "SPT3xxx" and "rebound" in e.construct for e in codes.REGISTRY)
    assert any(e.band == "SPT4xxx" and "positional args" in e.construct for e in codes.REGISTRY)

    # Minor 6: `in` vs `is` split, with the dossier's framing.
    assert (
        by_code["SPT1011"].message_intent
        == "use Map.has(k) or Vec.first_index_of(v) instead of `in`"
    )
    assert by_code["SPT1012"].message_intent == "identity has no on-chain meaning; use =="
    assert (
        "keys()" in by_code["SPT1019"].message_intent
        or "while loop" in by_code["SPT1019"].message_intent
    )

    # Minor 7: owning-task consistency.
    assert by_code["SPT1001"].owning_task == "Task 6"
    assert by_code["SPT1002"].owning_task == "Task 6"
    assert by_code["SPT4012"].owning_task == "Task 3"

    # Minor 8: SPT3005 names the AugAssign desugaring.
    assert "AugAssign" in by_code["SPT3005"].construct


def test_declaration_shape_intents_speak_for_every_contract_function() -> None:
    """Task 8 fix round 1, M-1 (sanctioned wording edit, no renumber): the
    parameter/return-shape rules apply identically to exports, module-level
    helpers and private methods -- `decls.py` emits all three codes for
    helpers and private methods, which `decorators.py` never validates -- so
    their intent text may not name exports alone.
    """
    intents = {entry.code: entry.message_intent for entry in codes.REGISTRY}
    for code in ("SPT4002", "SPT4003", "SPT4005"):
        assert "exported method" not in intents[code], (code, intents[code])
        assert "contract function" in intents[code], (code, intents[code])


def test_type_and_case_name_intents_also_cover_the_symbol_charset() -> None:
    """Task 9 fix round 1 (sanctioned wording edit, no renumber): controller
    review found `SPT5002`/`SPT5003` checking name LENGTH only, while
    `sections._check_name` also enforces the Symbol charset on a type/case
    name -- a non-ASCII, length-legal name was silently accepted by
    `validate_limits` and then raised `SpecNameError` with no location.
    `limits.py` now checks the charset too (matching `SPT5001`'s own
    length-then-charset order); this pins that both intents describe it.
    """
    intents = {entry.code: entry.message_intent for entry in codes.REGISTRY}
    for code in ("SPT5002", "SPT5003"):
        assert "outside [a-zA-Z0-9_]" in intents[code], (code, intents[code])
        assert "too long" in intents[code], (code, intents[code])
    # The same edit folded the keyword-only parameter into SPT4002's subject.
    assert "keyword-only" in intents["SPT4002"]


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
