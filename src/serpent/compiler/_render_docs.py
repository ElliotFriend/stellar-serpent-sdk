"""Task 12: render `docs/subset.md` from the compiler's own executable truth.

S14's rule -- "docs generated from the truth, never hand-drifted" -- means this
module, not a hand-maintained markdown file, is the specification's source.
Everything it renders comes from four artifacts that already exist for other
reasons and cannot silently drift out from under this file without a test
noticing first:

1. `serpent.compiler.codes.REGISTRY` -- every `SPT####` code's band, the
   AST-shape it covers (`construct`), and its `message_intent` (both are
   asserted, elsewhere, to be a literal substring of every diagnostic that
   code ever raises -- `tests/unit/test_must_reject.py`'s diagnostics-quality
   sweep).
2. `tests/must_reject/*.py` -- the 95 fixtures. Each is compiled live (the
   same way `tests/unit/test_must_reject.py`'s runner does) so the rendered
   example carries the diagnostic's REAL `message`/`help`, never a
   hand-copied guess that could go stale.
3. `codes.NO_FIXTURE_ALLOWLIST` / `codes.NO_FIXTURE_REASONS` -- the codes with
   no real-source trigger, and why.
4. `serpent.compiler.recognize.RECOGNIZED` plus the container method tables
   (`VEC_METHODS`/`MAP_METHODS`/`BYTES_METHODS`) -- the positive authoring
   surface (what a call/attribute lowers to), as opposed to (1)-(3), which are
   all reject-side.

## Import-graph compliance (dossier D9 / `tests/unit/test_core_zero_dep.py`)

This module lives under `src/serpent/compiler/`, which the zero-dep walk
covers directly -- there is no `[spec]`-extra exemption for it the way there
is for `serpent.spec`. Every import this file's own source writes resolves to
the standard library or another `serpent` module, which is all that walk
checks: a static AST scan of each file's OWN `import` statements, never the
transitive closure a real interpreter loads. That is NOT a claim that
importing this module needs no `stellar_sdk` at runtime -- it does:
`serpent.compiler.__init__` already pulls in `frontend` -> `ctx` -> `ir` ->
`types_` -> `serpent.spec` -> `stellar_sdk` unconditionally, a pre-existing
fact about the whole `serpent.compiler` package this file does not change, so
`stellar_sdk` must be installed to import `_render_docs` at all, exactly like
every other checker module in this package.

## Determinism (S14)

No timestamp, no absolute path, and no unordered container is ever rendered:
`REGISTRY`'s own tuple order (grouped by band, ascending code) drives the
"what rejects" section; fixtures are grouped by the code they declare while
preserving `sorted(FIXTURES_DIR.glob("**/*.py"))`'s order; `NO_FIXTURE_
ALLOWLIST`, a `frozenset`, is always rendered `sorted()`. Re-running `render()`
on an unchanged tree therefore always produces the same bytes -- the byte-drift
test (`tests/unit/test_subset_docs.py`) is exactly that claim, checked.

## The regeneration command

`docs/gen_subset.py` is a five-line shim for `python -m
serpent.compiler._render_docs` (this module's own `__main__` block, which
calls `main()` below). The byte-drift test calls `render()` directly and NEVER
imports that shim, so a bug in the shim can never mask a doc/generator
mismatch.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

from serpent.compiler import codes
from serpent.compiler.codes import CodeEntry
from serpent.compiler.diagnostics import CompileError, Diagnostic
from serpent.compiler.frontend import compile_module
from serpent.compiler.recognize import (
    BYTES_METHODS,
    KNOWN_FUTURE_ENV_NAMES,
    MAP_METHODS,
    RECOGNIZED,
    VEC_METHODS,
)

#: The exact command this file's header names, and the one `docs/gen_subset.py`
#: shims. Kept as one constant so the header and the shim's own docstring can
#: never quietly disagree about it.
GENERATOR_COMMAND = "python -m serpent.compiler._render_docs"

#: Human titles for each band, transcribed verbatim from `codes.py`'s own
#: `# --- SPTNxxx: <title> ...` section comments -- not a second, independent
#: naming of the bands.
_BAND_TITLES: dict[str, str] = {
    "SPT1xxx": "unsupported construct",
    "SPT2xxx": "name resolution / imports / scope",
    "SPT3xxx": "types",
    "SPT4xxx": "contract shape (declarations)",
    "SPT5xxx": "spec / XDR limits",
    "SPT6xxx": "protocol gating",
    "SPT7xxx": "flow analysis",
    "SPT8xxx": "emitter limits",
}

#: This module's own directory is `src/serpent/compiler/`; the repo root is
#: three `parent`s up from there (`src/serpent/compiler` -> `src/serpent` ->
#: `src` -> root). Only used to LOCATE the fixture tree and the default output
#: path -- never rendered into the doc itself (S14: no absolute paths in the
#: output).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURES_DIR = _REPO_ROOT / "tests" / "must_reject"

_DIRECTIVE_RE = re.compile(
    r"^#\s*serpent:(?P<key>reject|at|message|doc-title)\s+(?P<value>.+?)\s*$"
)
_HERE_MARKER_RE = re.compile(r"#\s*HERE\b")
_REQUIRED_DIRECTIVES = frozenset({"reject", "at", "message", "doc-title"})


class FixtureFormatError(ValueError):
    """A `tests/must_reject/` fixture's header could not be parsed.

    This module deliberately re-implements the header parse rather than
    importing `tests/unit/test_must_reject.py` (a test module, not a
    `serpent.compiler` dependency `src/` may reach into) -- see the module
    docstring's zero-dep note. Both parsers read the same four-directive
    contract documented in `tests/must_reject/README.md`.
    """


@dataclass(frozen=True)
class _FixtureExample:
    """One parsed-and-compiled `must_reject/` fixture, ready to render."""

    rel_path: str
    code: str
    doc_title: str
    source: str
    diagnostic: Diagnostic


def _parse_fixture_header(path: Path) -> tuple[str, str, str, str, int]:
    """`(code, message, doc_title, source_text, here_line)` for one fixture.

    Mirrors `tests/unit/test_must_reject.py::_parse_fixture`'s directive and
    `# HERE`-marker rules (MJ-14): `at` must be the literal `HERE`, the anchor
    line is the FIRST `# HERE` marker comment in the file, a repeated `#
    serpent:<key>` directive is malformed, and a SECOND `# HERE` marker is
    malformed (the plan review's HERE-duplicate-marker guard) -- both copied
    from the runner's own parser, not imported from it (this module's
    docstring explains why `src/` cannot import a `tests/` module), so a
    future malformed fixture cannot silently pass one parser and not the
    other.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    directives: dict[str, str] = {}
    here_line: int | None = None
    for lineno, line in enumerate(lines, start=1):
        m = _DIRECTIVE_RE.match(line)
        if m:
            key = m.group("key")
            if key in directives:
                raise FixtureFormatError(f"{path}: duplicate '# serpent:{key}' directive")
            directives[key] = m.group("value")
            continue
        if _HERE_MARKER_RE.search(line):
            if here_line is not None:
                raise FixtureFormatError(
                    f"{path}: more than one '# HERE' marker comment found (lines "
                    f"{here_line} and {lineno}); exactly one is allowed, so the anchor "
                    "is never ambiguous"
                )
            here_line = lineno

    missing = _REQUIRED_DIRECTIVES - directives.keys()
    if missing:
        raise FixtureFormatError(f"{path}: missing directive(s): {sorted(missing)}")
    if directives["at"] != "HERE":
        raise FixtureFormatError(f"{path}: '# serpent:at' must be the literal 'HERE'")
    if here_line is None:
        raise FixtureFormatError(f"{path}: no '# HERE' marker comment found")

    return directives["reject"], directives["message"], directives["doc-title"], text, here_line


def _strip_header(source: str) -> str:
    """The fixture's body, with its `# serpent:` directive lines removed.

    The directives are machine-readable metadata for the runner and this
    generator, not part of the authored contract a reader is meant to copy.
    """
    kept = [line for line in source.splitlines() if not _DIRECTIVE_RE.match(line)]
    # Fixtures open with a blank line once the header is stripped (the
    # directives are conventionally the first four lines); drop leading
    # blanks so the rendered snippet starts at the first real statement.
    while kept and not kept[0].strip():
        kept.pop(0)
    return "\n".join(kept)


def _diagnostic_for(
    code: str, message: str, here_line: int, source: str, rel_path: str
) -> Diagnostic:
    """The one live `Diagnostic` a fixture's declared triple identifies.

    Compiles `source` exactly as `tests/unit/test_must_reject.py`'s runner
    does (AS TEXT, never imported) and applies the SAME matching rule, so the
    rendered `message`/`help` can never diverge from what the runner already
    proved that fixture produces.
    """
    try:
        compile_module(source, rel_path)
    except CompileError as exc:
        matching = [
            d
            for d in exc.diagnostics
            if d.code == code and d.loc.line == here_line and message in d.message
        ]
        if len(matching) == 1:
            return matching[0]
        raise FixtureFormatError(
            f"{rel_path}: expected exactly one diagnostic matching code={code!r} "
            f"line={here_line} message~={message!r}; got {exc.diagnostics!r}"
        ) from exc
    raise FixtureFormatError(f"{rel_path}: expected to reject, but it compiled cleanly")


def _discover_fixtures() -> list[_FixtureExample]:
    examples: list[_FixtureExample] = []
    for path in sorted(_FIXTURES_DIR.glob("**/*.py")):
        code, message, doc_title, raw_source, here_line = _parse_fixture_header(path)
        rel_path = str(path.relative_to(_FIXTURES_DIR))
        diagnostic = _diagnostic_for(code, message, here_line, raw_source, rel_path)
        examples.append(
            _FixtureExample(
                rel_path=rel_path,
                code=code,
                doc_title=doc_title,
                source=_strip_header(raw_source),
                diagnostic=diagnostic,
            )
        )
    return examples


# --- rendering ---------------------------------------------------------------


def _render_header(fixture_count: int) -> list[str]:
    return [
        "<!--",
        "GENERATED FILE -- do not hand-edit.",
        f"Generated by: {GENERATOR_COMMAND}",
        "Source of truth: src/serpent/compiler/codes.py, tests/must_reject/,",
        "src/serpent/compiler/recognize.py (dossier S14). A byte-drift test",
        "(tests/unit/test_subset_docs.py) fails if this file and its generator",
        "ever disagree; the failure message names the regeneration command.",
        "-->",
        "",
        "# The serpent subset",
        "",
        "serpent compiles a restricted subset of Python to a Soroban contract.",
        "This document is generated, in full, from the compiler's own registry",
        (
            "of diagnostic codes, its `tests/must_reject/` fixture suite "
            f"({fixture_count} minimal counter-examples, one per rejected"
        ),
        "construct), and its recognized-surface tables -- never hand-authored,",
        "so it cannot say something the compiler does not actually do.",
        "",
        "1. [What compiles](#1-what-compiles) -- the authoring surface, by area.",
        "2. [What rejects](#2-what-rejects) -- every `SPT####` code, by band,",
        "   with a real fixture and the exact diagnostic it produces.",
        "3. [Allowlisted codes](#3-allowlisted-codes-no-fixture) -- registry",
        "   codes with no real-source trigger, and why.",
        "",
    ]


def _registry_by_code() -> dict[str, CodeEntry]:
    return {entry.code: entry for entry in codes.REGISTRY}


def _cap(text: str) -> str:
    """Capitalize a registry `message_intent`'s first letter for use as the
    start of a rendered sentence -- the registry writes every intent as a
    lower-case CLAUSE (it is normally read as a diagnostic message's tail),
    so quoting one verbatim as a sentence's opening word needs this."""
    return text[:1].upper() + text[1:]


def _render_env_api() -> list[str]:
    lines = [
        "### 1.3 Env API",
        "",
        "`env.storage()`/`env.ledger()`/`env.events()` and address authorization",
        "each lower to exactly one host function call (or, for a defaulted",
        "storage read, a `has` then `get`-or-default pair). Every recognized",
        "surface:",
        "",
        "| Python | Host function(s) |",
        "| --- | --- |",
    ]
    env_rows = sorted(
        (spec.surface, spec.host_fns)
        for spec in RECOGNIZED.values()
        if spec.family == "env" and spec.host_fns
    )
    for surface, host_fns in env_rows:
        lines.append(f"| `{surface}` | `{'`, `'.join(host_fns)}` |")
    lines += [
        "",
        "Storage keys are drawn from one of three buckets: `instance()`,",
        "`persistent()`, `temporary()`.",
        "",
        "A bare `<bucket>.get(key, T)` with no `default` carries a reserved",
        f"runtime error code (`{hex(_missing_value_code())}`) for a missing key;",
        "give `default=...` to get a value back instead of a trap.",
        "",
        "#### Storage keys",
        "",
        "A storage (or `Map`) key is not restricted to `Symbol`: any chain",
        "value may key a bucket, including a `@contracttype` (struct) instance",
        "(dossier D7) -- `BalanceKey(owner=...)` in `tests/fixtures/token_style.py`",
        "is the intended shape for a keyed record.",
        "",
        "**Note (E3):** struct storage keys are not modelled in tier 1's",
        "ordering. The compiler's own key-ordering model (`val_cmp`, the same",
        "one used to pre-sort a literal `Map(...)`'s keys onto the host's",
        "linear-memory form) has no rank for structs, so it can never answer an",
        "ordering question about one at compile time. This does not block",
        "compiling a struct-keyed `Map` or storage entry -- the host itself",
        "orders a struct correctly on chain -- it only means the compiler will",
        "never claim to have PROVEN an order over struct keys the way it does",
        "for scalars; that behavior is pinned by the differential test suite",
        "instead, not by this compiler's static model.",
        "",
        "Recognized, but not yet lowerable (`SPT1033`, landing in M2): "
        + ", ".join(f"`env.{name}()`" for name in sorted(KNOWN_FUTURE_ENV_NAMES))
        + ".",
        "",
    ]
    return lines


def _missing_value_code() -> int:
    code = RECOGNIZED["storage.get"].missing_value_code
    assert code is not None, "storage.get must carry the reserved missing-value code"
    return code


def _render_container_method_table(title: str, methods: Mapping[str, str]) -> list[str]:
    lines = [f"**{title}**", "", "| Method | Lowers to |", "| --- | --- |"]
    for method, row_key in sorted(methods.items()):
        surface = RECOGNIZED[row_key].surface
        lines.append(f"| `{method}` | `{surface}` |")
    lines.append("")
    return lines


def _render_containers() -> list[str]:
    lines = [
        "### 1.4 Containers",
        "",
        f"`{RECOGNIZED['vec.new'].surface}`, `{RECOGNIZED['map.new'].surface}`, and",
        f"`{RECOGNIZED['struct.new'].surface}` construct the three container/struct",
        "shapes; a literal built entirely from compile-time values lays out",
        "directly, and anything else falls back to the incremental host",
        f"build-up form. `{RECOGNIZED['struct.field'].surface}` reads a struct",
        "field. The mutating methods below are every method these classes carry:",
        "",
    ]
    lines += _render_container_method_table("`Vec` methods", VEC_METHODS)
    lines += _render_container_method_table("`Map` methods", MAP_METHODS)
    lines += _render_container_method_table("`Bytes`/`BytesN` methods", BYTES_METHODS)
    lines += [
        "A container mutator (`push_back`, `set`, `del_`, ...) is only legal as",
        "its own statement on a local this method owns outright -- the host's",
        "container ops are functional, so `v.push_back(x)` compiles to a",
        "rebind (`v = vec_push_back(v, x)`); anywhere else it is `SPT1034`.",
        "",
    ]
    return lines


def _render_what_compiles() -> list[str]:
    declarations_entry = _registry_by_code()["SPT4015"]
    types_entry = _registry_by_code()["SPT3008"]
    for_map_entry = _registry_by_code()["SPT1019"]
    range_entry = _registry_by_code()["SPT1020"]
    flow_entry = _registry_by_code()["SPT7001"]
    n_declaration_rules = len(_band(codes.REGISTRY, "SPT4xxx"))
    n_type_rules = len(_band(codes.REGISTRY, "SPT3xxx"))
    n_flow_rules = len(_band(codes.REGISTRY, "SPT7xxx"))

    lines = [
        "## 1. What compiles",
        "",
        "### 1.1 Declarations",
        "",
        f"{_cap(declarations_entry.message_intent)}. A method's `self` must come",
        "first, every parameter and the return both need a chain-type",
        f"annotation, and `__init__` compiles to the constructor. {n_declaration_rules}",
        (
            "declaration-shape rules are enforced end to end; see "
            f"[{declarations_entry.band}](#{_anchor(declarations_entry.band)}) below"
        ),
        "for the exact list.",
        "",
        "### 1.2 Types",
        "",
        "A bare literal with no chain type in scope is rejected (SPT3008):",
        f"{types_entry.message_intent}. Operators are restricted to",
        "same-width, same-signedness operands, and `==`/`<`/`<=`/`>`/`>=` are",
        "restricted to the types the on-chain value model can actually order.",
        (
            f"{n_type_rules} type rules are enforced end to end; see "
            f"[{types_entry.band}](#{_anchor(types_entry.band)}) below for the exact"
        ),
        "list.",
        "",
    ]
    lines += _render_env_api()
    lines += _render_containers()
    lines += [
        "### 1.5 Control flow",
        "",
        "`if`/`while`/`for x in range(...)`/`for x in <Vec>` all compile, as do",
        "`return` and `raise <ErrorEnum>.<Member>`. Iteration is narrower",
        f"elsewhere: {for_map_entry.message_intent}; {range_entry.message_intent}.",
        "Every method proves it returns on every",
        "path and never reads a local before it is definitely assigned;",
        (
            f"{n_flow_rules} flow rules are enforced end to end -- see "
            f"[{for_map_entry.band}](#{_anchor(for_map_entry.band)}) (statement/loop shape) "
            f"and [{flow_entry.band}](#{_anchor(flow_entry.band)}) "
            "(return/definite-assignment/recursion) below for the exact lists."
        ),
        "",
    ]
    return lines


def _band(registry: tuple[CodeEntry, ...], band: str) -> tuple[CodeEntry, ...]:
    return tuple(entry for entry in registry if entry.band == band)


def _anchor(band: str) -> str:
    """GitHub-flavored-markdown heading anchor for a `### SPTNxxx -- <title>`.

    GitHub's own slugifier lower-cases, drops everything but word characters/
    spaces/hyphens, and replaces each space with a hyphen WITHOUT collapsing
    the result -- so `"SPT4xxx -- contract shape (declarations)"` anchors at
    `spt4xxx----contract-shape-declarations` (four hyphens: the space before
    `--`, the two literal hyphens, and the space after), not at a
    single-hyphen-collapsed guess. Matching that exactly, rather than a
    cleaned-up approximation, is what makes the cross-reference links below
    actually resolve when this file is viewed on GitHub.
    """
    title = _BAND_TITLES[band]
    text = f"{band} -- {title}"
    slug = re.sub(r"[^a-z0-9 -]", "", text.lower())
    return slug.replace(" ", "-")


def _render_fixture_example(example: _FixtureExample) -> list[str]:
    diag = example.diagnostic
    lines = [
        f"#### {example.doc_title} (`{example.rel_path}`)",
        "",
        "```python",
        example.source,
        "```",
        "",
        f"- **message:** {diag.message}",
    ]
    if diag.help:
        lines.append(f"- **help:** {diag.help}")
    for note in diag.notes:
        lines.append(f"- **note:** {note}")
    lines.append("")
    return lines


def _render_what_rejects(fixtures: list[_FixtureExample]) -> list[str]:
    by_code: dict[str, list[_FixtureExample]] = {}
    for example in fixtures:
        by_code.setdefault(example.code, []).append(example)

    lines = ["## 2. What rejects", ""]
    for band, entries in groupby(codes.REGISTRY, key=lambda e: e.band):
        band_entries = [e for e in entries if e.code not in codes.NO_FIXTURE_ALLOWLIST]
        if not band_entries:
            continue
        lines += [f"### {band} -- {_BAND_TITLES[band]}", ""]
        for entry in band_entries:
            lines += [
                f"#### {entry.code}",
                "",
                f"**Construct:** {entry.construct}",
                "",
                f"**Intent:** {entry.message_intent}",
                "",
            ]
            for example in by_code.get(entry.code, []):
                lines += _render_fixture_example(example)
    return lines


def _render_allowlisted() -> list[str]:
    lines = [
        "## 3. Allowlisted codes (no fixture)",
        "",
        "Every other registry code is proven by a `tests/must_reject/` fixture.",
        "These are the exceptions -- codes with no real-source trigger today,",
        "each proven end to end some other way instead:",
        "",
        "| Code | Band | Reason |",
        "| --- | --- | --- |",
    ]
    entries = _registry_by_code()
    for code in sorted(codes.NO_FIXTURE_ALLOWLIST):
        entry = entries[code]
        reason = codes.NO_FIXTURE_REASONS[code].replace("|", "\\|")
        lines.append(f"| `{code}` | `{entry.band}` | {reason} |")
    lines.append("")
    return lines


def render() -> str:
    """The complete, deterministic content of `docs/subset.md`."""
    fixtures = _discover_fixtures()
    _check_completeness(fixtures)
    lines: list[str] = []
    lines += _render_header(len(fixtures))
    lines += _render_what_compiles()
    lines += _render_what_rejects(fixtures)
    lines += _render_allowlisted()
    return "\n".join(lines).rstrip("\n") + "\n"


def _check_completeness(fixtures: list[_FixtureExample]) -> None:
    """Mirror `test_must_reject.py`'s meta-test B: every non-allowlisted
    registry code must have `>= 1` fixture, or this generator would silently
    render an empty "what rejects" entry for it. A real drift here should
    fail loudly, in the generator, not just quietly produce a hole."""
    declared = {example.code for example in fixtures}
    required = codes.CODES - codes.NO_FIXTURE_ALLOWLIST
    missing = sorted(required - declared)
    if missing:
        raise FixtureFormatError(f"registry code(s) with no must_reject/ fixture: {missing}")


def main() -> None:
    """Regenerate `docs/subset.md` in place (the `docs/gen_subset.py` shim's
    entry point, and this module's own `python -m` behavior)."""
    output_path = _REPO_ROOT / "docs" / "subset.md"
    output_path.write_text(render(), encoding="utf-8")


if __name__ == "__main__":
    main()
