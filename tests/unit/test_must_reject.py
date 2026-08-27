"""The `tests/must_reject/` runner: an executable subset specification.

Task 2 of the M1-C plan (BL-2): the runner lives HERE, OUTSIDE the fixture
tree it globs, because `tests/must_reject/` is fixtures-only -- no
`__init__.py`, no `test_*.py` in there, so pytest never collects a fixture as
a test module on its own (dossier SS D.3, plan Global Constraints).

Each fixture is a small, syntactically valid contract module carrying exactly
one rejected construct, declared by a machine-readable `# serpent:` header
(see `tests/must_reject/README.md` for the authoring contract). This module:

1. Globs every fixture, parses its header (`_parse_fixture`) -- this does NOT
   need `compile_module` and so runs today.
2. Meta-test A: every declared `# serpent:reject` code exists in
   `serpent.compiler.codes.CODES` -- live now (the SPT code registry
   already exists; this is the review gate the task brief names).
3. Meta-test B: every non-`NO_FIXTURE_ALLOWLIST` registry code has >= 1
   fixture -- a hard, enforced check since Task 11b completed the fixture
   set (95 fixtures covering all 92 required codes).
4. The per-fixture runner test (live since Task 10 landed `compile_module`):
   compiles the fixture AS TEXT -- never imported, because importing would
   execute decorators outside the loader's own bridging and defeat the point
   -- and asserts exactly one diagnostic matches the header's declared
   `(code, HERE line, message substring)` triple.

`# serpent:at HERE` never carries an absolute `line:col` (MJ-14 corrects the
dossier's D.3 example): it always resolves to the line of the first `# HERE`
marker comment in the fixture body, so inserting/removing a line above the
marker does not require renumbering the header.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from serpent.compiler import codes, compile_module
from serpent.compiler.diagnostics import CompileError, LocKind

#: The fixture tree, glob'd from OUTSIDE it (BL-2): `parents[1]` from
#: `tests/unit/test_must_reject.py` is `tests/`.
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "must_reject"

_DIRECTIVE_RE = re.compile(
    r"^#\s*serpent:(?P<key>reject|at|message|doc-title)\s+(?P<value>.+?)\s*$"
)
_HERE_MARKER_RE = re.compile(r"#\s*HERE\b")
_REQUIRED_DIRECTIVES = frozenset({"reject", "at", "message", "doc-title"})

#: `code -> message_intent`, so the diagnostics-quality sweep can assert every
#: fixture's matched diagnostic carries its registry row's own wording.
_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}


class FixtureHeaderError(ValueError):
    """A fixture's `# serpent:` header block (or `# HERE` marker) is malformed."""


@dataclass(frozen=True)
class FixtureSpec:
    """One parsed `must_reject/` fixture: its declared expectation."""

    path: Path
    code: str
    message: str
    doc_title: str
    here_line: int

    @property
    def rel_path(self) -> str:
        return str(self.path.relative_to(FIXTURES_DIR))


def _parse_fixture(path: Path) -> FixtureSpec:
    """Parse one fixture's `# serpent:` header and locate its `# HERE` marker.

    Directives may appear anywhere in the file (the README mandates the top,
    but parsing does not depend on position); the `# HERE` marker is the
    first line -- excluding header directive lines themselves -- containing
    a `# HERE` comment (MJ-14).

    A SECOND `# HERE` marker anywhere later in the file is a fixture-authoring
    bug, not a valid fixture (plan review, MJ-item): the README's own anchor
    rule names "the first" marker as authoritative, which silently implies
    that a second one is dead weight an author meant to delete -- and, worse,
    an ambiguous rewrite target if the file is ever edited. Refusing it here,
    at parse time, keeps that ambiguity from ever reaching the runner's
    line-comparison assertion, where it would silently do nothing rather than
    fail loudly.
    """
    lines = path.read_text().splitlines()
    directives: dict[str, str] = {}
    here_line: int | None = None
    for lineno, line in enumerate(lines, start=1):
        m = _DIRECTIVE_RE.match(line)
        if m:
            key = m.group("key")
            if key in directives:
                raise FixtureHeaderError(f"{path}: duplicate '# serpent:{key}' directive")
            directives[key] = m.group("value")
            continue
        if _HERE_MARKER_RE.search(line):
            if here_line is not None:
                raise FixtureHeaderError(
                    f"{path}: more than one '# HERE' marker comment found (lines "
                    f"{here_line} and {lineno}); exactly one is allowed, so the anchor "
                    "is never ambiguous"
                )
            here_line = lineno

    missing = _REQUIRED_DIRECTIVES - directives.keys()
    if missing:
        raise FixtureHeaderError(f"{path}: missing directive(s): {sorted(missing)}")
    if directives["at"] != "HERE":
        raise FixtureHeaderError(
            f"{path}: '# serpent:at' must be the literal anchor 'HERE' (MJ-14); "
            f"got {directives['at']!r} -- absolute line:col anchors are not supported"
        )
    if here_line is None:
        raise FixtureHeaderError(f"{path}: no '# HERE' marker comment found in the fixture body")

    return FixtureSpec(
        path=path,
        code=directives["reject"],
        message=directives["message"],
        doc_title=directives["doc-title"],
        here_line=here_line,
    )


def _discover_fixtures() -> list[FixtureSpec]:
    paths = sorted(FIXTURES_DIR.glob("**/*.py"))
    return [_parse_fixture(p) for p in paths]


FIXTURES: list[FixtureSpec] = _discover_fixtures()


# --- scaffold integrity -----------------------------------------------------


def test_seed_fixtures_are_discovered() -> None:
    # A floor, not a moving target: Task 11b completes the fixture set (95
    # fixtures covering the 92 required registry codes -- 96 total minus the
    # four in `codes.NO_FIXTURE_ALLOWLIST`, SPT6001/SPT1009/SPT4018/SPT7003).
    # This guards the completed tree does not regress under a later edit, not
    # the exact count.
    assert len(FIXTURES) >= 90


def test_fixture_paths_are_unique() -> None:
    rel_paths = [f.rel_path for f in FIXTURES]
    assert len(rel_paths) == len(set(rel_paths))


def test_no_init_py_or_test_prefixed_file_in_the_fixture_tree() -> None:
    # BL-2: the fixture tree carries no `__init__.py` and no `test_*.py` --
    # it must never be collectible by pytest on its own; only this runner,
    # from OUTSIDE the tree, may execute it.
    offenders = [
        p
        for p in FIXTURES_DIR.rglob("*.py")
        if p.name == "__init__.py" or p.name.startswith("test_")
    ]
    assert not offenders, offenders


# --- the HERE-duplicate guard (plan review, MJ-item) ------------------------


def _write_fixture(tmp_path: Path, text: str) -> Path:
    fixture = tmp_path / "scratch_fixture.py"
    fixture.write_text(text)
    return fixture


def test_a_second_here_marker_is_rejected(tmp_path: Path) -> None:
    text = (
        "# serpent:reject SPT1001\n"
        "# serpent:at HERE\n"
        "# serpent:message nested functions and closures are not supported\n"
        "# serpent:doc-title nested function definition\n"
        "def outer():  # HERE\n"
        "    def inner():  # HERE\n"
        "        pass\n"
    )
    fixture = _write_fixture(tmp_path, text)
    with pytest.raises(FixtureHeaderError, match="more than one"):
        _parse_fixture(fixture)


def test_a_single_here_marker_still_parses(tmp_path: Path) -> None:
    # The guard's negative case: exactly one marker is the whole point of the
    # anchor and must keep working.
    text = (
        "# serpent:reject SPT1001\n"
        "# serpent:at HERE\n"
        "# serpent:message nested functions and closures are not supported\n"
        "# serpent:doc-title nested function definition\n"
        "def outer():\n"
        "    def inner():  # HERE\n"
        "        pass\n"
    )
    fixture = _write_fixture(tmp_path, text)
    spec = _parse_fixture(fixture)
    assert spec.here_line == 6


# --- meta-test A: every declared code is registered (live now) -------------


def test_meta_a_every_declared_code_is_registered() -> None:
    unregistered = sorted({f.code for f in FIXTURES} - codes.CODES)
    assert not unregistered, (
        f"fixture(s) declare unregistered code(s): {unregistered} -- every "
        "`# serpent:reject` must cite a code from the SPT code registry"
    )


# --- meta-test B: every non-allowlisted code has >= 1 fixture (Task 11b) ---

#: `SPT1009`, `SPT4018` and `SPT7003` were added to `codes.NO_FIXTURE_
#: ALLOWLIST` by controller ruling (task-11b-report.md §3 carries the full
#: reachability writeup for each): every real-source path to each is claimed
#: by an earlier, always-first check, so each is a dead dispatch/check branch
#: kept as defense-in-depth rather than deleted. Meta-test B now consults
#: `codes.NO_FIXTURE_ALLOWLIST` directly -- no local exemption list.


def test_meta_b_every_registry_code_has_a_fixture() -> None:
    declared = {f.code for f in FIXTURES}
    required = codes.CODES - codes.NO_FIXTURE_ALLOWLIST
    missing = sorted(required - declared)
    assert not missing, f"registry code(s) with no must_reject/ fixture: {missing}"


# --- the runner: compile each fixture and check its declared diagnostic ----


@pytest.mark.parametrize("spec", FIXTURES, ids=lambda s: s.rel_path)
def test_fixture_rejects_with_its_declared_diagnostic(spec: FixtureSpec) -> None:
    source = spec.path.read_text()

    with pytest.raises(CompileError) as exc_info:
        compile_module(source, spec.rel_path)

    diagnostics = exc_info.value.diagnostics
    matching = [
        d
        for d in diagnostics
        if d.code == spec.code and d.loc.line == spec.here_line and spec.message in d.message
    ]
    assert len(matching) == 1, (
        f"{spec.rel_path}: expected exactly one diagnostic matching "
        f"code={spec.code!r} line={spec.here_line} message~={spec.message!r}; "
        f"got: {diagnostics!r}"
    )


# --- diagnostics-quality sweep (Task 11b, dossier F.2.11) -------------------

#: Fixture ids no `must_reject/` fixture currently declares a `WHOLE_FILE`
#: diagnostic for: the fixture header format has no way to anchor `# HERE` at
#: a `WHOLE_FILE` `Loc` (it always resolves to a real, >= 1 source line, and
#: `Loc.whole_file` is `line=0`), so a fixture whose declared diagnostic is
#: `WHOLE_FILE` could never pass the runner's own `d.loc.line ==
#: spec.here_line` assertion in the first place. This set exists so that
#: fact is a checked, named invariant instead of an implicit one -- if a
#: future fixture format extension ever lets a fixture declare `WHOLE_FILE`
#: on purpose, its id belongs here.
_WHOLE_FILE_DECLARED: frozenset[str] = frozenset()


@pytest.mark.parametrize("spec", FIXTURES, ids=lambda s: s.rel_path)
def test_every_fixtures_matched_diagnostic_has_diagnostics_quality(spec: FixtureSpec) -> None:
    """Every fixture's declared-triple diagnostic must carry: a non-empty
    `help`, a `Loc` that is `NODE` (never `WHOLE_FILE`) unless the fixture is
    named in `_WHOLE_FILE_DECLARED`, and a `message` that contains its own
    registry row's `message_intent` -- the message-intent-prefix consistency
    every `serpent.compiler` module's own `_error()` helper promises (dossier
    F.2.11)."""
    source = spec.path.read_text()
    with pytest.raises(CompileError) as exc_info:
        compile_module(source, spec.rel_path)

    diagnostics = exc_info.value.diagnostics
    matching = [
        d
        for d in diagnostics
        if d.code == spec.code and d.loc.line == spec.here_line and spec.message in d.message
    ]
    assert len(matching) == 1, f"{spec.rel_path}: the declared triple must match exactly once"
    diagnostic = matching[0]

    assert diagnostic.help, f"{spec.rel_path}: {spec.code} diagnostic carries no `help` rewrite"

    if spec.rel_path not in _WHOLE_FILE_DECLARED:
        assert diagnostic.loc.kind is not LocKind.WHOLE_FILE, (
            f"{spec.rel_path}: {spec.code} diagnostic is WHOLE_FILE but this fixture does "
            "not declare that in _WHOLE_FILE_DECLARED"
        )

    intent = _INTENT[spec.code]
    assert intent in diagnostic.message, (
        f"{spec.rel_path}: {spec.code}'s message does not carry its registry row's own "
        f"intent {intent!r}; got {diagnostic.message!r}"
    )
