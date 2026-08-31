"""Task 11c / F.2.5: `compile_module` never fails in a non-diagnostic way.

The property, in one sentence: **for any input at all, `compile_module` either
returns a `CompiledModule` or raises `CompileError` carrying at least one
located, registered diagnostic.** Anything else -- a `KeyError`, an
`AttributeError`, an `IndexError`, a `RecursionError`, a bare `ValueError`
escaping a decorator, or the frontend's own `CompilerBugError` -- is a found
bug, because P2/P3's whole point is that a user never sees a traceback.

Two independent generators feed that one property, because they fail in
different directions:

1. **A grammar of contract-module shapes** (`module_sources`). Valid Python by
   construction -- decorators, method shapes, statements, expressions drawn
   from template pools -- but only sometimes inside serpent's subset. This is
   the "accidental support creep / unconsidered AST shape" direction: it finds
   the node the dispatcher forgot, the annotation nobody resolved, the
   decorator that raised instead of reporting.
2. **A mangled-fixture corpus** (`mangled_sources`). The 95
   `tests/must_reject/` fixtures plus `tests/fixtures/*.py` and `examples/*.py`
   -- every complete authored contract the repo has -- put through systematic
   damage:
   truncation at line boundaries, token swaps, indentation damage, unicode
   injection. This is the "half-written file in an editor" direction, where
   the interesting inputs are *nearly* valid and the parse/exec bridge is what
   gets stressed.

## Why the property is stated as "never raises anything else", not "rejects"

A generated module that happens to be a legal contract MUST compile; a
generated module that is not MUST draw a diagnostic. The fuzzer deliberately
does not care which of the two happens for a given input -- pinning that would
make it a (very bad) specification of the subset rather than a robustness
check, and the subset's boundary is specified precisely elsewhere
(`tests/must_reject/`, `tests/unit/test_frontend_semantics.py`).

## The CI profile (determinism)

`CI_FUZZ` is applied per-test rather than loaded as a global Hypothesis
profile, so this module cannot change how the repo's other property tests
(`test_val_properties.py`, `test_numeric_properties.py`) behave:

* `derandomize=True` -- the seed is derived from the test itself, so a run on
  CI, a run on a laptop, and a re-run after a failure all draw the *same*
  examples. A green suite stays green; a red one stays red with the same
  minimal example.
* `database=None` -- no `.hypothesis/` example database, so a failure recorded
  on one machine cannot change what a later run on another machine draws.
  Combined with `derandomize`, the suite has no run-to-run state at all.
* `deadline` -- a per-example wall-clock cap, so a pathological input that
  sends the frontend quadratic (or into an accidental unbounded loop) fails
  the suite instead of hanging CI. It is deliberately generous relative to a
  real compile (milliseconds): the point is to catch a hang, not to be a
  performance test that flakes on a loaded machine.
* `max_examples` -- the default is modest so the suite stays fast; set
  `SERPENT_FUZZ_EXAMPLES` to run a deeper campaign locally (that is the one
  knob that makes a run non-default, and it is opt-in).

Also `suppress_health_check`: `too_slow` because a compile is genuinely slower
than the microsecond-scale draws Hypothesis calibrates against, and
`filter_too_much` because `mangled_sources` filters out the small fraction of
mutations that would put a loop at module level (see `_would_execute_a_loop`).

## Compiling EXECUTES module-level code, and the generators respect that

`compile_module`'s docstring is explicit: ruling E1's hybrid frontend runs the
module's top-level statements, so decorators and annotations exist at all.
A fuzzer that generated arbitrary module-level code would therefore be running
it. Both generators are built so that cannot bite:

* the grammar puts generated statements only inside function/method bodies,
  which are compiled but never called; its module level is a fixed set of
  shapes (imports, a constant, decorated class declarations);
* the corpus mutations only rearrange text that was already in the repo, and
  `_would_execute_a_loop` rejects the mutation if it moved a `while`/`for` out
  to module level or into a class body -- the one shape that could turn a
  damaged fixture into an unbounded loop at exec time. (The Hypothesis
  deadline would report such a case as a failure only *after* it returned,
  which for an infinite loop is never.)
"""

from __future__ import annotations

import ast
import os
import re
import warnings
from collections.abc import Iterator, Sequence
from datetime import timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

import serpent
from serpent.compiler import codes
from serpent.compiler.diagnostics import CompileError, Diagnostic, LocKind
from serpent.compiler.frontend import compile_module

# --- the settings profile ----------------------------------------------------

#: Opt-in deeper campaign: `SERPENT_FUZZ_EXAMPLES=5000 uv run pytest -q -k fuzz`.
_EXAMPLES = int(os.environ.get("SERPENT_FUZZ_EXAMPLES", "150"))

CI_FUZZ = settings(
    derandomize=True,
    database=None,
    max_examples=_EXAMPLES,
    deadline=timedelta(seconds=10),
    suppress_health_check=(HealthCheck.too_slow, HealthCheck.filter_too_much),
)

PATH = "fuzz/generated.py"

_CODE_RE = re.compile(r"^SPT\d{4}$")
_REGISTERED: frozenset[str] = frozenset(entry.code for entry in codes.REGISTRY)


# --- the one property --------------------------------------------------------


def _assert_diagnostic_shape(exc: CompileError, source: str) -> None:
    """Every diagnostic in `exc` is registered and located (P2/P3, E16)."""
    assert exc.diagnostics, f"CompileError with no diagnostics for:\n{source}"
    for diag in exc.diagnostics:
        assert isinstance(diag, Diagnostic)
        assert _CODE_RE.match(diag.code), f"{diag.code!r} is not an SPTnnnn code"
        assert diag.code in _REGISTERED, f"{diag.code} is not in codes.REGISTRY"
        assert diag.message, f"{diag.code} has an empty message"
        # P2: never fabricate a location. `WHOLE_FILE` is a real answer for a
        # module-level fact ("expected exactly one @contract class"); what is
        # never acceptable is a location pointing at the wrong file, or a
        # `NODE` span that does not name a line.
        assert diag.loc.path == PATH, f"{diag.code} points at {diag.loc.path!r}"
        if diag.loc.kind is LocKind.NODE:
            assert diag.loc.line >= 1, f"{diag.code} has a NODE loc on line {diag.loc.line}"


def check_robust(source: str) -> None:
    """The F.2.5 property for one source: diagnostics or success, never a
    traceback.

    `except Exception` (not `BaseException`) is deliberate and sufficient:
    `CompilerBugError` is an `AssertionError`, every accidental frontend
    failure mode named in F.2.5 is an `Exception`, and leaving
    `KeyboardInterrupt` and Hypothesis's own `BaseException`-derived control
    flow alone is what keeps the fuzzer interruptible. Nothing inside the
    `try` draws from Hypothesis, so no `assume()` can be swallowed here.

    `SyntaxWarning` is silenced around the call, not globally: CPython emits
    it while COMPILING nonsense the generators produce on purpose
    ("'bool' object is not subscriptable; perhaps you missed a comma?"), and
    it is noise about the input rather than a signal about the frontend.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        try:
            compile_module(source, PATH)
        except CompileError as exc:
            _assert_diagnostic_shape(exc, source)
        except Exception as exc:  # pragma: no cover -- a green suite never gets here
            raise AssertionError(
                f"compile_module raised {type(exc).__name__} instead of a diagnostic "
                f"CompileError -- F.2.5 violation.\n"
                f"--- minimal repro ---\n{source}\n--- end repro ---\n"
                f"{type(exc).__name__}: {exc}"
            ) from exc


# --- generator (a): a grammar of contract-module shapes ----------------------


def _fill(template: str, args: Sequence[str]) -> str:
    """Substitute `<0>`, `<1>`, `<2>` in `template`.

    Placeholders are angle-bracketed rather than `str.format`'s `{}` so a
    template can contain literal braces (a dict or set display, an f-string)
    with no escaping to get wrong.
    """
    filled = template
    for index, arg in enumerate(args):
        filled = filled.replace(f"<{index}>", arg)
    return filled


#: Expression leaves: in-subset chain-type constructions, out-of-subset raw
#: Python literals, and the names a generated method body has in scope.
_LEAF_EXPRS: tuple[str, ...] = (
    "U32(1)",
    "I32(-1)",
    "U64(0)",
    "I64(7)",
    "U128(1)",
    "I128(-1)",
    "Bool(True)",
    'Symbol("k")',
    'Symbol("a_symbol_over_nine")',
    'String("s")',
    'Bytes(b"\\x01")',
    'Bytes32(b"\\x00" * 32)',
    'Address("GA7QYNF7SOWQ3GLR2BGMZEHXAVIRZA4KVWLTJJFC7MGXUA74P7UJVSGZ")',
    "Timepoint(0)",
    "Duration(0)",
    "Vec(U32, [U32(1)])",
    "Map(Symbol, U32, [])",
    "K",
    "env",
    "self",
    "a",
    "b",
    "x",
    "0",
    "1",
    "-1",
    "2**64",
    "True",
    "False",
    "None",
    '"raw"',
    'b"raw"',
    'f"{a}"',
    "1.5",
    "...",
    "Error.Boom",
    "S",
    "U32",
)

# Every sub-expression slot below is written PARENTHESIZED (`(<0>)`, never a
# bare `<0>`). Parentheses are transparent to `ast.parse` -- there is no node
# for them, so the compiler sees exactly the same tree -- but they make the
# grammar precedence-proof, which is what keeps it a *valid-Python* grammar:
# without them, unary `-` over `not x` composes into `-not x`, a `SyntaxError`,
# and the generator would spend its examples testing the parse bridge instead
# of the checkers it is aimed at.
_UNARY_TEMPLATES: tuple[str, ...] = (
    "-(<0>)",
    "+(<0>)",
    "~(<0>)",
    "not (<0>)",
    "len((<0>))",
    "abs((<0>))",
    "int((<0>))",
    "U32((<0>))",
    "Vec(U32, [(<0>)])",
    "(<0>).field",
    "(<0>).counter_limit",
    "(<0>).len()",
    "(<0>)()",
    "(<0>)[0]",
    "(<0>)[0:1]",
    "(<0>)[-1]",
    "((<0>),)",
    "[(<0>)]",
    "{(<0>)}",
    "lambda: (<0>)",
    "f(*(<0>))",
    "(w := (<0>))",
    "[i for i in (<0>)]",
    "{i: i for i in (<0>)}",
    "S(field=(<0>))",
    "env.storage().instance().get((<0>), U32)",
    "env.storage().persistent().set((<0>), U32(1))",
    'env.events().publish((Symbol("e"), (<0>)), U32(1))',
    "(<0>).require_auth()",
    "(<0>).push_back(U32(1))",
    "(<0>).pop_back()",
)

_BINARY_TEMPLATES: tuple[str, ...] = tuple(
    f"(<0>) {op} (<1>)"
    for op in (
        "+",
        "-",
        "*",
        "/",
        "//",
        "%",
        "**",
        "&",
        "|",
        "^",
        "<<",
        ">>",
        "@",
        "==",
        "!=",
        "<",
        "<=",
        ">",
        ">=",
        "in",
        "not in",
        "is",
        "is not",
        "and",
        "or",
    )
) + (
    "(<0>)[(<1>)]",
    "{(<0>): (<1>)}",
    "[(<0>), (<1>)]",
    "((<0>), (<1>))",
    "f((<0>), (<1>))",
    "divmod((<0>), (<1>))",
    "Map(Symbol, U32, [((<0>), (<1>))])",
    "Vec(U32, [(<0>), (<1>)])",
    "env.storage().persistent().get((<0>), U32, default=(<1>))",
    "S(field=(<0>), other=(<1>))",
)

_TERNARY_TEMPLATES: tuple[str, ...] = (
    "(<0>) if (<1>) else (<2>)",
    "(<0>) < (<1>) < (<2>)",
    "(<0>)[(<1>):(<2>)]",
    "(<0>)[(<1>), (<2>)]",
    "f((<0>), (<1>), key=(<2>))",
)

#: One-line statement templates, valid inside any FUNCTION body. `<0>` is an
#: expression. Three shapes are deliberately absent here because they are
#: context-sensitively invalid Python rather than merely out-of-subset:
#: `break`/`continue` (a `SyntaxError` outside a loop -- see
#: `_LOOP_ONLY_STMT_TEMPLATES`), `nonlocal` (a `SyntaxError` with no enclosing
#: binding), and `yield` in an EXPRESSION position (a `SyntaxError` inside a
#: lambda, an f-string, or a comprehension). `yield` as a STATEMENT is here,
#: which covers the shape the checker has to reject.
_SIMPLE_STMT_TEMPLATES: tuple[str, ...] = (
    "pass",
    "x = (<0>)",
    "x: U32 = (<0>)",
    "x, y = (<0>), (<0>)",
    "x += (<0>)",
    "x -= (<0>)",
    "self.field = (<0>)",
    "x[0] = (<0>)",
    "del x",
    "return",
    "return (<0>)",
    "raise Error.Boom",
    "raise ValueError((<0>))",
    "raise",
    "assert (<0>)",
    "assert (<0>), (<0>)",
    "(<0>)",
    "global K",
    "import os",
    "from serpent import U32",
    "print((<0>))",
    "x = [i for i in range(3)]",
    "x = lambda: (<0>)",
    "yield (<0>)",
    "yield",
)

#: Valid only inside a loop body; a `SyntaxError` anywhere else.
_LOOP_ONLY_STMT_TEMPLATES: tuple[str, ...] = ("break", "continue")

#: Compound-statement templates. `<0>` is an expression; a line whose content
#: after its own indentation is `@B` is replaced by a generated block, indented
#: four past that line's own indentation.
#:
#: They are split into THREE named pools rather than one, because a block's
#: scope decides which simple statements are legal inside it, and getting that
#: wrong produces unparseable Python (which
#: `test_the_grammar_emits_only_valid_python` would then reject wholesale).
#: Named pools rather than index slices of one tuple, so adding a template
#: cannot silently reclassify its neighbours.
#:
#: `_SCOPE_KEEPING_TEMPLATES` -- the block inherits the enclosing loop scope,
#: which is CPython's own rule for `if`/`with`/`try`/`match`.
#:
#: `class Inner:` carries a FIXED body rather than a generated one: a generated
#: block could hold a `return` or a `yield`, both `SyntaxError`s in a class
#: body. (A class defined inside a method body never executes -- the method is
#: compiled and never called -- so this is purely about staying parseable.)
_SCOPE_KEEPING_TEMPLATES: tuple[tuple[str, ...], ...] = (
    ("if (<0>):", "@B"),
    ("if (<0>):", "@B", "else:", "@B"),
    ("if (<0>):", "@B", "elif (<0>):", "@B"),
    ("with (<0>):", "@B"),
    ("try:", "@B", "except Exception:", "@B"),
    ("try:", "@B", "finally:", "@B"),
    ("class Inner:", "    field: U32"),
    ("match (<0>):", "    case _:", "    @B"),
    ("if (<0>):", "@B", "if (<0>):", "@B"),
)

#: A nested `def`/`async def` starts a FRESH function scope, so an enclosing
#: loop's `break`/`continue` are no longer legal inside its body.
_FUNC_TEMPLATES: tuple[tuple[str, ...], ...] = (
    ("def inner(a: U32) -> U32:", "@B"),
    ("async def inner(a: U32) -> U32:", "@B"),
)

#: The templates whose `@B` block is a LOOP body, which is what makes
#: `break`/`continue` legal inside it.
_LOOP_TEMPLATES: tuple[tuple[str, ...], ...] = (
    ("while (<0>):", "@B"),
    ("while (<0>):", "@B", "else:", "@B"),
    ("for i in (<0>):", "@B"),
    ("for i in range(3):", "@B"),
    ("for i, j in (<0>):", "@B"),
)

#: Every compound template, for the anti-vacuity sweep below.
_COMPOUND_TEMPLATES: tuple[tuple[str, ...], ...] = (
    *_SCOPE_KEEPING_TEMPLATES,
    *_FUNC_TEMPLATES,
    *_LOOP_TEMPLATES,
)

_MAX_EXPR_DEPTH = 3
_MAX_STMT_DEPTH = 2


def _expressions(depth: int) -> st.SearchStrategy[str]:
    leaves = st.sampled_from(_LEAF_EXPRS)
    if depth <= 0:
        return leaves
    sub = _expressions(depth - 1)
    return st.one_of(
        leaves,
        st.builds(_fill, st.sampled_from(_UNARY_TEMPLATES), st.tuples(sub)),
        st.builds(_fill, st.sampled_from(_BINARY_TEMPLATES), st.tuples(sub, sub)),
        st.builds(_fill, st.sampled_from(_TERNARY_TEMPLATES), st.tuples(sub, sub, sub)),
    )


EXPRESSIONS = _expressions(_MAX_EXPR_DEPTH)


def _indent(lines: Sequence[str], prefix: str) -> tuple[str, ...]:
    return tuple(prefix + line for line in lines)


def _expand(
    template: Sequence[str], expr: str, blocks: Sequence[tuple[str, ...]]
) -> tuple[str, ...]:
    """One compound template -> its lines, with every `@B` marker filled in."""
    out: list[str] = []
    used = 0
    for line in template:
        stripped = line.lstrip(" ")
        if stripped == "@B":
            own_indent = line[: len(line) - len(stripped)]
            out.extend(_indent(blocks[used % len(blocks)], own_indent + "    "))
            used += 1
        else:
            out.append(_fill(line, (expr, expr)))
    return tuple(out)


def _one_line(template: str, expr: str) -> tuple[str, ...]:
    return (_fill(template, (expr,)),)


def _statements(depth: int, *, in_loop: bool) -> st.SearchStrategy[tuple[str, ...]]:
    """One statement (possibly compound) as a tuple of un-indented lines.

    `in_loop` widens the simple pool with `break`/`continue`; it is set for the
    body of a `_LOOP_TEMPLATES` template and, once set, stays set for the
    nested `if`/`try`/`with` blocks inside that body -- which is exactly
    CPython's own rule, and is cleared again by a nested `def`/`class`.
    """
    pool = _SIMPLE_STMT_TEMPLATES + (_LOOP_ONLY_STMT_TEMPLATES if in_loop else ())
    simple = st.builds(_one_line, st.sampled_from(pool), EXPRESSIONS)
    if depth <= 0:
        return simple
    same_scope = _blocks(depth - 1, in_loop=in_loop)
    loop_scope = _blocks(depth - 1, in_loop=True)
    fresh_scope = _blocks(depth - 1, in_loop=False)
    return st.one_of(
        simple,
        # `if`/`with`/`try`/`match` keep the enclosing loop scope; a nested
        # `def`/`async def` does not, so its block is drawn fresh.
        st.builds(
            _expand,
            st.sampled_from(_SCOPE_KEEPING_TEMPLATES),
            EXPRESSIONS,
            st.tuples(same_scope, same_scope),
        ),
        st.builds(
            _expand,
            st.sampled_from(_FUNC_TEMPLATES),
            EXPRESSIONS,
            st.tuples(fresh_scope, fresh_scope),
        ),
        st.builds(
            _expand,
            st.sampled_from(_LOOP_TEMPLATES),
            EXPRESSIONS,
            st.tuples(loop_scope, loop_scope),
        ),
    )


def _blocks(depth: int, *, in_loop: bool) -> st.SearchStrategy[tuple[str, ...]]:
    """A non-empty suite: at least one statement, so the Python stays valid."""
    return st.lists(_statements(depth, in_loop=in_loop), min_size=1, max_size=3).map(
        lambda groups: tuple(line for group in groups for line in group)
    )


BLOCKS = _blocks(_MAX_STMT_DEPTH, in_loop=False)

#: Method names worth generating: an ordinary export, the constructor spelling,
#: a private method, the reserved export name, a dunder, and a name that
#: collides with a module-level helper (spec Sec.2 nearly writes that one).
_METHOD_NAMES: tuple[str, ...] = (
    "go",
    "__init__",
    "_private",
    "__constructor",
    "__repr__",
    "f",
    "balance",
)

#: Parameter lists, in and out of the subset: `self`-first (the amended style),
#: `env`-first (the spike's retired style), missing annotations, `*args`,
#: `**kwargs`, defaults, positional-only and keyword-only markers.
_PARAM_LISTS: tuple[str, ...] = (
    "self, env: Env",
    "self, env: Env, a: U32",
    "self, env: Env, a: U32, b: Symbol",
    "self, env: Env, a",
    "self, env: Env, a: U32 = U32(0)",
    "self, env: Env, *args: U32",
    "self, env: Env, **kwargs: U32",
    "self, env: Env, /, a: U32",
    "self, env: Env, *, a: U32",
    "self, a: U32",
    "self",
    "env: Env, a: U32",
    "",
    "cls, env: Env",
    "self, env: U32",
    "self, env: Env, a: int",
    "self, env: Env, a: Vec[U32]",
    "self, env: Env, a: Map[Symbol, U32]",
    "self, env: Env, a: S",
    "self, env: Env, a: Error",
)

_RETURNS: tuple[str, ...] = (
    " -> None",
    " -> U32",
    " -> Bool",
    " -> Symbol",
    " -> Vec[U32]",
    " -> S",
    " -> Error",
    " -> int",
    "",
)

_CLASS_DECORATORS: tuple[str, ...] = (
    "@contract",
    "@contracttype",
    "@contracterror",
    "@contractevent",
    "@contract\n@contracttype",
    "@staticmethod",
    "",
)

_METHOD_DECORATORS: tuple[str, ...] = ("", "    @staticmethod\n", "    @property\n")

#: The `serpent` root names a generated module imports. Fixed rather than drawn,
#: and pinned against `serpent.__all__` by
#: `test_the_generated_import_list_is_importable`, so a failed import never
#: becomes the (uninteresting) reason every generated module is rejected -- one
#: unimportable name in this list would sink the whole `from serpent import ...`
#: statement and leave the grammar generating nothing but SPT4019.
_IMPORTED_NAMES: tuple[str, ...] = (
    "I32",
    "I64",
    "I128",
    "U32",
    "U64",
    "U128",
    "Address",
    "Bool",
    "Bytes",
    "Bytes32",
    "Duration",
    "Env",
    "Event",
    "Map",
    "String",
    "Symbol",
    "Timepoint",
    "Vec",
    "contract",
    "contracterror",
    "contractevent",
    "contracttype",
    "errorcode",
)

_IMPORTS = "from serpent import (\n" + "".join(f"    {n},\n" for n in _IMPORTED_NAMES) + ")\n"

#: Module-level declarations a generated module may carry alongside the class
#: under test. Executed at compile time (E1), so these are fixed text, never
#: generated statements.
_PRELUDE_PARTS: tuple[str, ...] = (
    'K = Symbol("K")\n',
    "K = 5\n",
    "@contracterror\nclass Error:\n    Boom = errorcode(1)\n",
    "@contracttype\nclass S:\n    field: U32\n    other: U32\n",
    "@contractevent\nclass Ev(Event):\n    field: U32\n",
)


@st.composite
def module_sources(draw: st.DrawFn) -> str:
    """A whole contract module: imports, an optional prelude, one decorated
    class with 0-3 generated methods, and an optional module-level helper.

    Valid Python by construction (the templates and the indentation are both
    generated, never damaged); in serpent's subset only sometimes, which is
    the point.
    """
    parts: list[str] = [_IMPORTS, "\n"]
    for part in _PRELUDE_PARTS:
        if draw(st.booleans()):
            parts.append("\n" + part)

    decorator = draw(st.sampled_from(_CLASS_DECORATORS))
    class_lines: list[str] = ["\n"]
    if decorator:
        class_lines.append(decorator + "\n")
    class_lines.append("class Generated:\n")
    if draw(st.booleans()):
        class_lines.append('    """A generated class."""\n')

    methods = draw(st.lists(st.booleans(), min_size=0, max_size=3))
    if not methods:
        class_lines.append("    pass\n")
    for _ in methods:
        method_decorator = draw(st.sampled_from(_METHOD_DECORATORS))
        name = draw(st.sampled_from(_METHOD_NAMES))
        params = draw(st.sampled_from(_PARAM_LISTS))
        returns = draw(st.sampled_from(_RETURNS))
        body = draw(BLOCKS)
        class_lines.append(method_decorator)
        class_lines.append(f"    def {name}({params}){returns}:\n")
        if draw(st.booleans()):
            class_lines.append('        """A generated method."""\n')
        class_lines.extend(line + "\n" for line in _indent(body, "        "))
    parts.extend(class_lines)

    if draw(st.booleans()):
        helper_body = draw(BLOCKS)
        parts.append("\n\ndef helper(a: U32) -> U32:\n")
        parts.extend(line + "\n" for line in _indent(helper_body, "    "))

    return "".join(parts)


@given(module_sources())
@CI_FUZZ
def test_generated_contract_shapes_never_escape_the_diagnostic_family(source: str) -> None:
    """F.2.5, generator (a): a grammar of VALID Python contract-module shapes."""
    check_robust(source)


@given(module_sources())
@CI_FUZZ
def test_the_grammar_emits_only_valid_python(source: str) -> None:
    """Generator (a)'s own contract: every example PARSES.

    Without this, a grammar bug (a precedence slip, a `break` outside a loop,
    a `yield` in a class body) would send most examples down the `SyntaxError`
    bridge and the fuzz above would be vacuously green -- passing while never
    reaching a single checker. The brief's own wording for generator (a) is
    "VALID Python that may or may not be in the subset", and this is that half
    of it, enforced.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            ast.parse(source)
    except (SyntaxError, ValueError) as exc:  # pragma: no cover -- never in a green suite
        raise AssertionError(f"the grammar emitted unparseable Python: {exc}\n{source}") from exc


def test_the_generated_import_list_is_importable() -> None:
    """Every name in `_IMPORTED_NAMES` is a real `serpent` root export."""
    missing = sorted(set(_IMPORTED_NAMES) - set(serpent.__all__))
    assert not missing, missing


def test_the_grammar_reaches_past_the_parse_bridge() -> None:
    """A second guard on the same risk, from the other side: the fixed shapes
    the grammar is built out of must really compose into a module the frontend
    accepts, so "valid Python" is not being satisfied by emitting only
    trivially-rejected text."""
    source = (
        _IMPORTS
        + "\n\n@contract\nclass Generated:\n"
        + "    def go(self, env: Env) -> U32:\n"
        + "        x = U32(1)\n"
        + "        if (x) < (U32(2)):\n"
        + "            return (x)\n"
        + "        return (U32(0))\n"
    )
    ast.parse(source)
    compile_module(source, PATH)  # no CompileError: a fully in-subset assembly


def _composed(decorator: str, params: str, returns: str, body: Sequence[str]) -> str:
    """One module assembled from the grammar's own pools, WITHOUT Hypothesis.

    Deterministic by construction, so the anti-vacuity check below is a plain
    unit test with no seed, no draws, and no run-to-run variation -- and it
    exercises the same template text the random generator draws from, so a pool
    entry that stopped composing shows up here too.
    """
    prelude = "".join("\n" + part for part in _PRELUDE_PARTS[2:])
    head = f"\n{decorator}\n" if decorator else "\n"
    return (
        _IMPORTS
        + "\n"
        + prelude
        + head
        + "class Generated:\n"
        + f"    def go({params}){returns}:\n"
        + "".join(line + "\n" for line in _indent(body, "        "))
    )


def test_the_grammar_pools_reach_deep_into_the_checkers() -> None:
    """Anti-vacuity: the template pools must reach the SUBSET checkers, not
    just the module-shape gate.

    A fuzzer that only ever produced "expected exactly one @contract class"
    would pass F.2.5 while testing nothing -- and that is a live failure mode,
    not a theoretical one: one unimportable name in `_IMPORTED_NAMES` sinks the
    whole `from serpent import ...` statement and every subsequent declaration
    with it (see `test_the_generated_import_list_is_importable`).

    So this composes modules out of the real pools and asserts the diagnostics
    span several BANDS -- SPT1xxx (unsupported construct), SPT3xxx (types),
    SPT4xxx (declaration shape), SPT7xxx (flow) -- each of which can only be
    reached by a checker that ran on a real declaration. The thresholds are
    floors, set well under what the pools currently reach (a 1500-example
    random run of generator (a) hits 53 distinct codes across 5 bands, and
    generator (b) 82 across 6), so this fails on a collapse rather than
    churning on a pool edit.
    """
    # One axis is varied at a time against a fixed in-subset baseline, so every
    # pool entry is exercised at least once in O(sum of pool sizes) compiles
    # rather than O(product) -- the cartesian product is ~14k compiles and
    # reaches no additional code, since a diagnostic is drawn by ONE axis.
    base_decorator = "@contract"
    base_params = "self, env: Env, a: U32"
    base_returns = " -> None"
    base_body: tuple[str, ...] = ("x = (a)",)

    sources: list[str] = []
    for decorator in _CLASS_DECORATORS:
        sources.append(_composed(decorator, base_params, base_returns, base_body))
    for params in _PARAM_LISTS:
        sources.append(_composed(base_decorator, params, base_returns, base_body))
    for returns in _RETURNS:
        sources.append(_composed(base_decorator, base_params, returns, base_body))
    for template in _COMPOUND_TEMPLATES:
        body = _expand(template, "U32(1)", (("pass",), ("pass",)))
        sources.append(_composed(base_decorator, base_params, base_returns, body))
    for simple in _SIMPLE_STMT_TEMPLATES:
        sources.append(
            _composed(base_decorator, base_params, base_returns, (_fill(simple, ("U32(1)",)),))
        )

    codes: set[str] = set()
    for source in sources:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            try:
                compile_module(source, PATH)
            except CompileError as exc:
                codes.update(diag.code for diag in exc.diagnostics)
    bands = {code[:4] for code in codes}
    assert len(codes) >= 12, sorted(codes)
    assert {"SPT1", "SPT3", "SPT4", "SPT7"} <= bands, sorted(bands)


@pytest.fixture
def grammar_codes() -> Iterator[set[str]]:
    """Accumulates diagnostic codes ACROSS one `@given` run, and asserts the
    floor in teardown.

    A function-scoped fixture is set up once around a whole `@given` run rather
    than once per example -- normally a footgun, which is why Hypothesis has a
    `function_scoped_fixture` health check for it. Here it is exactly the
    behavior wanted (the assertion is about the run, not any one example), so
    the health check is suppressed at the one test that uses this, with this
    docstring as the justification.

    The floors are deliberately far below the measured values (the committed
    150-example profile draws 25 distinct codes across all 5 reachable bands):
    the point is to fail when the generator COLLAPSES, not to pin a number that
    churns whenever a template or a diagnostic message is edited. Verified to
    bite: degenerating `module_sources()` to a single fixed module drops it to
    2 codes and this fails, while the pool-based guard above still passes.
    """
    seen: set[str] = set()
    yield seen
    bands = {code[:4] for code in seen}
    assert len(seen) >= 8, f"generator (a) drew only {len(seen)} distinct codes: {sorted(seen)}"
    assert len(bands) >= 3, f"generator (a) drew only these bands: {sorted(bands)}"


# `source=` keyword form, not positional: a positional `@given` binds the
# test's arguments from the RIGHT, which would try to fill `grammar_codes` from
# the strategy and leave `source` looking like a missing fixture.
@given(source=module_sources())
@settings(
    derandomize=True,
    database=None,
    max_examples=_EXAMPLES,
    deadline=None,
    suppress_health_check=(HealthCheck.too_slow, HealthCheck.function_scoped_fixture),
)
def test_the_grammar_as_drawn_reaches_deep_into_the_checkers(
    source: str, grammar_codes: set[str]
) -> None:
    """Anti-vacuity for `module_sources()` ITSELF, not just its pools.

    `test_the_grammar_pools_reach_deep_into_the_checkers` composes modules from
    the pools by hand, so it stays green even if the *strategy* degenerated --
    a `st.sampled_from` accidentally narrowed to one entry, a `draw` that
    stopped varying, a `booleans()` that always came back `False`. The pools
    would still be rich; the fuzzer would still be testing one module.

    This closes that hole by measuring what the generator actually DRAWS, over
    the same examples the F.2.5 property runs on: the union of diagnostic codes
    across the run must span at least 8 codes and 3 bands (see
    `grammar_codes`). It shares the `max_examples` knob with the property
    tests, so a deeper campaign deepens this too.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        try:
            compile_module(source, PATH)
        except CompileError as exc:
            grammar_codes.update(diag.code for diag in exc.diagnostics)


# --- generator (b): the mangled-fixture corpus -------------------------------

_TESTS_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _TESTS_ROOT.parent

#: Every complete contract source the repo has, as (name, text): the
#: `must_reject/` fixtures (each a minimal, deliberately-invalid module), the
#: complete VALID contracts in `tests/fixtures/`, and the SHIPPED contracts in
#: `examples/` (M1-E) -- a published example is exactly the file a newcomer will
#: half-edit in an editor, so it belongs in the "nearly valid" corpus. Names are
#: relative to the repo root for `examples/` and to `tests/` for the rest, which
#: is what the two inventory assertions below read. Sorted so the corpus -- and
#: therefore what `derandomize` draws from it -- is stable.
CORPUS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            str(path.relative_to(_TESTS_ROOT if _TESTS_ROOT in path.parents else _REPO_ROOT)),
            path.read_text(),
        )
        for path in (
            *(_TESTS_ROOT / "must_reject").rglob("*.py"),
            *(_TESTS_ROOT / "fixtures").glob("*.py"),
            *(_REPO_ROOT / "examples").glob("*.py"),
        )
        if path.name != "__init__.py"
    )
)

#: Characters worth injecting into an otherwise-fine source line. Each is a
#: real-world hazard rather than random noise: an invisible space, a combining
#: mark, an astral-plane character (two UTF-16 units, one code point), a
#: bidirectional override (the Trojan Source class), a byte-order mark, two
#: non-ASCII spaces that are not indentation-equivalent, a non-ASCII identifier
#: character, and a NUL -- which `compile()` rejects with `ValueError`, not
#: `SyntaxError`, and so is the one most likely to escape a parse bridge that
#: only catches `SyntaxError`.
_INJECTABLE: tuple[str, ...] = (
    "\u200b",
    "\u0301",
    "\U0001f600",
    "\u202e",
    "\ufeff",
    "\u00a0",
    "\u3000",
    "\u00e9",
    "\x00",
    "\U0001d54f",
)

#: Indentation damage: what a line's leading whitespace gets replaced with.
_INDENTS: tuple[str, ...] = ("", " ", "  ", "   ", "    ", "     ", "        ", "\t", " \t")

_TOKEN_RE = re.compile(r"\w+|\s+|[^\w\s]")


def _truncate(lines: list[str], keep: int) -> list[str]:
    """Truncation at a line boundary -- a file saved half-written."""
    return lines[:keep]


def _swap_lines(lines: list[str], i: int, j: int) -> list[str]:
    out = list(lines)
    out[i], out[j] = out[j], out[i]
    return out


def _swap_tokens(lines: list[str], index: int, first: int, second: int) -> list[str]:
    """Swap two tokens WITHIN one line, preserving the rest of the file."""
    out = list(lines)
    line = out[index].rstrip("\n")
    tokens = _TOKEN_RE.findall(line)
    positions = [k for k, tok in enumerate(tokens) if not tok.isspace()]
    if len(positions) < 2:
        return out
    a = positions[first % len(positions)]
    b = positions[second % len(positions)]
    tokens[a], tokens[b] = tokens[b], tokens[a]
    out[index] = "".join(tokens) + ("\n" if out[index].endswith("\n") else "")
    return out


def _damage_indent(lines: list[str], index: int, replacement: str) -> list[str]:
    out = list(lines)
    out[index] = replacement + out[index].lstrip(" \t")
    return out


def _inject(lines: list[str], index: int, offset: int, char: str) -> list[str]:
    out = list(lines)
    line = out[index].rstrip("\n")
    at = offset % (len(line) + 1)
    out[index] = line[:at] + char + line[at:] + ("\n" if out[index].endswith("\n") else "")
    return out


def _would_execute_a_loop(source: str) -> bool:
    """Whether the source has a `while`/`for` that compiling would RUN.

    `compile_module` executes module-level statements (E1), and a class body is
    part of its own statement's execution -- so a loop in either place runs at
    compile time. A mutation that dedented a method-body loop out to module
    level could therefore hand the fuzzer an unbounded loop, which a Hypothesis
    deadline cannot rescue (the deadline is checked *after* the call returns).
    Function bodies are skipped: they are compiled, never called, which is
    exactly why the grammar generator puts everything there.

    A source that does not parse returns `False`: nothing executes at all.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False

    def scan(nodes: Sequence[ast.stmt]) -> bool:
        for node in nodes:
            if isinstance(node, (ast.While, ast.For, ast.AsyncFor)):
                return True
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.stmt) and scan([child]):
                    return True
        return False

    return scan(tree.body)


@st.composite
def mangled_sources(draw: st.DrawFn) -> str:
    """One corpus fixture with one to three systematic mutations applied.

    Mutations compose (a truncation, then an indentation change, then a unicode
    injection is a realistic mid-edit file), and each is expressed as a pure
    `list[str] -> list[str]` function so a failure's repro is the final text
    and nothing else has to be reconstructed.
    """
    _name, source = draw(st.sampled_from(CORPUS))
    lines = source.splitlines(keepends=True)
    for _ in range(draw(st.integers(min_value=1, max_value=3))):
        if not lines:
            break
        kind = draw(st.sampled_from(("truncate", "swap_lines", "swap_tokens", "indent", "unicode")))
        last = len(lines) - 1
        if kind == "truncate":
            lines = _truncate(lines, draw(st.integers(min_value=0, max_value=len(lines))))
        elif kind == "swap_lines":
            lines = _swap_lines(
                lines,
                draw(st.integers(min_value=0, max_value=last)),
                draw(st.integers(min_value=0, max_value=last)),
            )
        elif kind == "swap_tokens":
            lines = _swap_tokens(
                lines,
                draw(st.integers(min_value=0, max_value=last)),
                draw(st.integers(min_value=0, max_value=8)),
                draw(st.integers(min_value=0, max_value=8)),
            )
        elif kind == "indent":
            lines = _damage_indent(
                lines,
                draw(st.integers(min_value=0, max_value=last)),
                draw(st.sampled_from(_INDENTS)),
            )
        else:
            lines = _inject(
                lines,
                draw(st.integers(min_value=0, max_value=last)),
                draw(st.integers(min_value=0, max_value=200)),
                draw(st.sampled_from(_INJECTABLE)),
            )
    mangled = "".join(lines)
    assume(not _would_execute_a_loop(mangled))
    return mangled


@given(mangled_sources())
@CI_FUZZ
def test_mangled_fixtures_never_escape_the_diagnostic_family(source: str) -> None:
    """F.2.5, generator (b): the 95 `must_reject/` fixtures plus
    `tests/fixtures/*.py` and `examples/*.py`, systematically damaged."""
    check_robust(source)


def test_the_corpus_is_the_whole_fixture_inventory() -> None:
    """A meta-check on generator (b): the corpus is the real inventory, so a
    fixture added later is fuzzed automatically instead of being silently
    skipped (the count is asserted as a floor: Task 11b's 95, less the SPT1032
    fixture M1-E deleted when `Event.publish(env)` became supported).

    The `fixtures/` and `examples/` lists are asserted EXACTLY rather than as a
    floor, so adding a contract is a deliberate act: Task 13 promoted the two
    `sandbox/` contracts into `tests/fixtures/` (F.2.8), M1-E added
    `examples/`, and M1-E Task 9 added `env_surface.py` (the E9 scenario
    table's contract) -- each joined this corpus automatically, which is the
    intended behavior, and updating these lines is how it gets acknowledged.
    """
    must_reject = [name for name, _ in CORPUS if name.startswith("must_reject/")]
    fixtures = [name for name, _ in CORPUS if name.startswith("fixtures/")]
    examples = [name for name, _ in CORPUS if name.startswith("examples/")]
    assert len(must_reject) >= 94, len(must_reject)
    assert sorted(fixtures) == [
        "fixtures/env_surface.py",
        "fixtures/sandbox_counter.py",
        "fixtures/sandbox_hello_world.py",
        "fixtures/spike1_reauthored.py",
        "fixtures/token_style.py",
        "fixtures/token_style_canonical.py",
    ]
    assert sorted(examples) == [
        "examples/allowance_token.py",
        "examples/counter.py",
        "examples/errors.py",
        "examples/events.py",
        "examples/structs.py",
    ]


def test_every_mutation_is_reachable_and_changes_something() -> None:
    """Each mutation, applied directly, really damages the text -- so a broken
    mutation cannot quietly reduce the fuzz above to "compile the corpus"."""
    lines = ["from serpent import U32\n", "\n", "@contract\n", "class C:\n", "    x = U32(1)\n"]
    assert _truncate(lines, 2) == lines[:2]
    assert _swap_lines(lines, 0, 4)[0] == lines[4]
    assert _swap_tokens(lines, 0, 0, 1)[0] != lines[0]
    assert _damage_indent(lines, 4, "") == [*lines[:4], "x = U32(1)\n"]
    assert _inject(lines, 0, 0, "\u200b")[0].startswith("\u200b")
    # A line with fewer than two tokens is left alone rather than mangled into
    # something the mutation did not mean.
    assert _swap_tokens(["\n"], 0, 0, 1) == ["\n"]


def test_the_module_level_loop_guard_is_precise() -> None:
    """`_would_execute_a_loop` must catch the shapes that RUN and leave the
    ones that only get compiled -- otherwise it either lets a hang through or
    filters out most of the corpus."""
    assert _would_execute_a_loop("while True:\n    pass\n")
    assert _would_execute_a_loop("for i in range(3):\n    pass\n")
    assert _would_execute_a_loop("if True:\n    while True:\n        pass\n")
    assert _would_execute_a_loop("class C:\n    for i in range(3):\n        pass\n")
    assert not _would_execute_a_loop("def f():\n    while True:\n        pass\n")
    assert not _would_execute_a_loop(
        "class C:\n    def f(self):\n        while 1:\n            x = 1\n"
    )
    assert not _would_execute_a_loop("x = 1\n")
    # Unparseable and NUL-bearing sources execute nothing at all.
    assert not _would_execute_a_loop("while True\n")
    assert not _would_execute_a_loop("x = 1\x00\n")


# --- the named regression seeds ---------------------------------------------

#: Inputs that are historically or structurally dangerous, pinned as explicit
#: cases so they run on every suite rather than only when the fuzzer happens to
#: draw them. F.2.5 names the first one specifically: "`frontend.py:519` would
#: die on a module docstring" is the *historical* failure mode this whole
#: property exists to prevent.
_SEEDS: tuple[tuple[str, str], ...] = (
    ("empty", ""),
    ("only_a_module_docstring", '"""Just a docstring."""\n'),
    ("only_whitespace", "\n\n   \n"),
    ("only_a_comment", "# nothing here\n"),
    ("nul_byte", "x = 1\x00\n"),
    ("lone_bom", "\ufeffx = 1\n"),
    ("bidi_override", "x = 1  # \u202e\n"),
    ("bare_import", "from serpent import U32\n"),
    ("import_star", "from serpent import *\n"),
    ("unterminated_string", 'x = "\n'),
    ("tab_indent", "@contract\nclass C:\n\tdef f(self):\n\t\tpass\n"),
    ("mixed_indent", "@contract\nclass C:\n    def f(self):\n\t pass\n"),
    ("deep_parens", "x = " + "(" * 40 + "1" + ")" * 40 + "\n"),
    ("long_line", "x = " + " + ".join(["1"] * 500) + "\n"),
    ("crlf_only", "x = 1\r\n@contract\r\nclass C:\r\n    pass\r\n"),
    ("cr_only", "x = 1\r@contract\rclass C:\r    pass\r"),
    ("form_feed", "\x0cx = 1\n"),
    ("decorator_expression", "@(lambda c: c)\nclass C:\n    pass\n"),
    ("class_with_no_body_after_truncation", "from serpent import contract\n\n\n@contract\n"),
    ("astral_identifier", "\U0001d54f = 1\n"),
)


@pytest.mark.parametrize("source", [s for _, s in _SEEDS], ids=[n for n, _ in _SEEDS])
def test_named_regression_seeds_stay_in_the_diagnostic_family(source: str) -> None:
    """The pinned dangerous inputs, F.2.5's own historical example first."""
    check_robust(source)
