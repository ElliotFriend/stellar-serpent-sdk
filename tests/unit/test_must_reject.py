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
   `serpent.compiler.codes.CODES` -- live now (the frozen 91-code registry
   already exists; this is the review gate the task brief names).
3. Meta-test B: every non-`NO_FIXTURE_ALLOWLIST` registry code has >= 1
   fixture -- `xfail(strict=False)` until Task 11b finishes the fixture set.
4. The per-fixture runner test: lazy-imports `compile_module` (which does not
   exist until Task 10) and skips with a clear reason until then; once
   available, compiles the fixture AS TEXT (never imported -- importing would
   execute decorators outside the loader's own bridging, defeating the
   point) and asserts exactly one diagnostic matches the header's declared
   `(code, HERE line, message substring)` triple.

`# serpent:at HERE` never carries an absolute `line:col` (MJ-14 corrects the
dossier's D.3 example): it always resolves to the line of the first `# HERE`
marker comment in the fixture body, so inserting/removing a line above the
marker does not require renumbering the header.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from serpent.compiler import codes
from serpent.compiler.diagnostics import CompileError

#: The fixture tree, glob'd from OUTSIDE it (BL-2): `parents[1]` from
#: `tests/unit/test_must_reject.py` is `tests/`.
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "must_reject"

_DIRECTIVE_RE = re.compile(
    r"^#\s*serpent:(?P<key>reject|at|message|doc-title)\s+(?P<value>.+?)\s*$"
)
_HERE_MARKER_RE = re.compile(r"#\s*HERE\b")
_REQUIRED_DIRECTIVES = frozenset({"reject", "at", "message", "doc-title"})


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
        if here_line is None and _HERE_MARKER_RE.search(line):
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


def _import_compile_module() -> Callable[..., object]:
    """Lazy-import `compile_module`, skipping the calling test if absent.

    `compile_module` is Task 10's deliverable. Importing it eagerly at
    module scope would break collection of this whole file (and therefore
    meta-tests A/B, which do not need it) for every task between here and
    Task 10, so the import happens inside each fixture-compiling test body
    instead. The `type: ignore[attr-defined]` below is expected to become
    unused (and must then be deleted) the moment Task 10 lands
    `compile_module` and un-skips this module, per the plan.
    """
    try:
        from serpent.compiler import compile_module  # type: ignore[attr-defined]
    except ImportError:
        pytest.skip("compile_module lands in Task 10")
    return cast("Callable[..., object]", compile_module)


# --- scaffold integrity -----------------------------------------------------


def test_seed_fixtures_are_discovered() -> None:
    # A floor, not a moving target: Task 2 seeds exactly 15 fixtures (12
    # constructs/ + 2 shape/ + 1 types/). Task 11b adds more; this guards the
    # scaffold itself, not the eventual complete set.
    assert len(FIXTURES) >= 15


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


# --- meta-test A: every declared code is registered (live now) -------------


def test_meta_a_every_declared_code_is_registered() -> None:
    unregistered = sorted({f.code for f in FIXTURES} - codes.CODES)
    assert not unregistered, (
        f"fixture(s) declare unregistered code(s): {unregistered} -- every "
        "`# serpent:reject` must cite a code from the frozen 91-code registry"
    )


# --- meta-test B: every non-allowlisted code has >= 1 fixture (Task 11b) ---


@pytest.mark.xfail(
    strict=False,
    reason="fixture completion is Task 11b's deliverable; only 15 seed fixtures exist so far",
)
def test_meta_b_every_registry_code_has_a_fixture() -> None:
    declared = {f.code for f in FIXTURES}
    required = codes.CODES - codes.NO_FIXTURE_ALLOWLIST
    missing = sorted(required - declared)
    assert not missing, f"registry code(s) with no must_reject/ fixture: {missing}"


# --- the runner: compile each fixture and check its declared diagnostic ----


@pytest.mark.parametrize("spec", FIXTURES, ids=lambda s: s.rel_path)
def test_fixture_rejects_with_its_declared_diagnostic(spec: FixtureSpec) -> None:
    compile_module = _import_compile_module()
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
