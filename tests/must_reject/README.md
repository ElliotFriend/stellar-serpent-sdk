# `must_reject/` -- the executable subset specification

This directory is the serpent compiler's "must reject" contract: one small
fixture file per rejected Python construct, docstring check, or type rule.
Together they ARE the specification of what the serpent subset does not
support -- not a sample of it. The runner
(`tests/unit/test_must_reject.py`, deliberately OUTSIDE this tree, dossier
SS D.3 / plan BL-2) globs every fixture here, compiles it, and asserts it
produces exactly the diagnostic the fixture declares.

`docs/subset.md` (Task 12) is generated from these same files, so the docs
and the compiler cannot drift against each other -- both read this directory.

## This directory is fixtures-only

- **No `__init__.py`.** This tree is never imported as a package.
- **No `test_*.py` (or `*_test.py`) filenames.** pytest's default collection
  would otherwise try to import a fixture directly as a test module, which
  both executes it (see "exec-safety" below) and gives it none of the
  header-driven assertions the runner provides. Only the runner, from
  `tests/unit/`, may execute anything in here.
- `pyproject.toml` excludes this tree from `mypy --strict`, ruffs every rule
  off it (`"tests/must_reject/**" = ["ALL"]`), and excludes it from
  `ruff format --check` -- each exclusion is commented, citing dossier E15 /
  plan BL-2. Fixtures are deliberately **invalid as typed/linted/formatted**
  Python in the shape/, types/, and some constructs/ cases (a bare-int error
  member, a parameter with no annotation, mismatched-width arithmetic): that
  invalidity is the point, and the exclusion exists precisely because there
  is precedent for the opposite in this repo (E3, `src`/`tests` under strict
  mypy with no excludes).

## Adding a fixture

1. Pick the band-appropriate subdirectory: `constructs/` (SPT1xxx),
   `names/` (SPT2xxx), `types/` (SPT3xxx), `shape/` (SPT4xxx), `limits/`
   (SPT5xxx), `protocol/` (SPT6xxx), `flow/` (SPT7xxx). Only `constructs/`,
   `shape/`, and `types/` are seeded as of Task 2; the rest arrive with the
   fixtures that need them.
2. Write the smallest **syntactically valid, decoration-safe** Python module
   that reaches the one construct under test -- see "exec-safety" below.
3. Give it a four-line machine-readable header, in this exact order, as the
   first four `#` comment lines of the file:

   ```python
   # serpent:reject SPT1003
   # serpent:at HERE
   # serpent:message comprehensions are not supported
   # serpent:doc-title list comprehension
   ```

   - `reject` -- the exact `SPT####` code the fixture must produce. It must
     already exist in `serpent.compiler.codes.REGISTRY` (meta-test A checks
     this for every fixture, and runs today -- it does not need
     `compile_module`).
   - `at HERE` -- always the literal word `HERE`, never an absolute
     `line:col`. See "the HERE anchor" below.
   - `message` -- a substring of the diagnostic's `message` field. Keep it
     short and stable; do not transcribe the whole message (message wording
     is allowed to improve without every fixture needing an edit).
   - `doc-title` -- the human-facing title Task 12's docs generator will use
     as this construct's heading (e.g. "list comprehension").

4. Mark the exact line the diagnostic should land on with a trailing
   `# HERE` comment:

   ```python
   return Vec(U32, [x + U32(1) for x in v])  # HERE
   ```

## The `HERE` anchor

The dossier's own D.3 example writes an absolute `# serpent:at 9:17`. This
repository does not: `# serpent:at HERE` always resolves to the line number
of the **first** `# HERE` comment found in the fixture body (MJ-14),
searching only non-header lines. This means inserting or deleting a line
above the marker never requires renumbering the header by hand, and a
fixture with no `# HERE` marker (or a header whose `at` value is not the
literal string `HERE`) fails to parse with a clear `FixtureHeaderError`
before any compiler is even invoked.

Put `# HERE` on the same line as the AST node the diagnostic's `Loc` will
point at -- generally the smallest statement or expression containing the
rejected construct, matching how `Diagnostic.loc` is built from `node.lineno`
elsewhere in the compiler (`serpent.compiler.diagnostics.Loc.from_node`).

## Exec-safety (plan minor 12)

The hybrid module loader (Task 3) **imports/execs** every contract module --
that is how it reads `_serpent_type_` metadata and resolves annotations
(dossier E1). The runner therefore never `import`s a fixture directly (that
would run decorators outside the loader's own bridging, defeating the whole
point of a located diagnostic) -- but `compile_module` itself will exec the
fixture's top-level statements one at a time. Two consequences for authors:

- **A fixture must be side-effect-free beyond its own declarations.** No
  network calls, no filesystem access, no `print`, no module-level code that
  does anything other than define classes/functions/constants. A method
  *body* is never executed by the loader (Python does not run a function
  body until it is called, and nothing here calls contract methods), so a
  rejected construct living inside a method body -- a comprehension, a
  `with` statement, a chained comparison -- is always exec-safe no matter how
  nonsensical it would be if actually run.
- **A `shape/` (SPT4xxx) fixture is often deliberately decoration-time
  invalid** -- e.g. `@contracterror class E: Bad = 1` raises a bare
  `ValueError` the instant `@contracterror` runs, *before* any AST-based
  check gets a chance to look at it. This is expected and by design: Task 3's
  loader catches `(ValueError, TypeError, NameError)` from exec, matches the
  exception back to the offending statement, and re-reports it as a located
  diagnostic (the "decorator-error bridging" concrete requirement, dossier
  D.2). Do not work around this by hand-catching the error inside the
  fixture -- the whole point is that the loader does the catching.

## The runner's assertion

For each fixture, the runner:

1. Reads the file **as text** (never imports it) and calls
   `compile_module(source, path)`.
2. Asserts it raises `CompileError`.
3. Collects every `Diagnostic` in `CompileError.diagnostics` and asserts
   **exactly one** matches all three of: `code == <declared reject code>`,
   `loc.line == <the HERE line>`, and `<declared message>` is a substring of
   `d.message`. Extra, unrelated diagnostics from the same compile (a
   fixture is allowed to trip more than one check) do not fail the
   assertion -- only the declared triple must have exactly one match.

Until `compile_module` exists (it is Task 10's deliverable), every one of
these per-fixture tests is skipped with `"compile_module lands in Task 10"`.
Meta-test A (every declared code is registered) and the fixture-tree
integrity checks do not depend on `compile_module` and run today.
