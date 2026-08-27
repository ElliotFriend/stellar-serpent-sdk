"""The compiler diagnostics core: `Loc`, `Diagnostic`, the `Diagnostics` sink,
and `CompileError`.

Implements dossier SS D.1-D.2
(docs/superpowers/specs/2026-08-27-m1c-inputs-dossier.md). Every compiler
error is a structured `Diagnostic` -- never a pre-formatted string -- so
`tests/must_reject/` can assert on `code` + a message substring while a CLI
renders the mypy/ruff/rustc-shaped text produced by `render()`.

`Loc` is mandatory on every diagnostic (P2): there is no synthetic `(1, 0)`.
Module-level facts ("expected exactly one @contract class") use
`LocKind.WHOLE_FILE`, which renders `path:` with no line/column.

Diagnostics are collected, not raised one at a time (E16): a `Diagnostics`
sink accumulates errors across a whole compile and only `raise_if_any()`
turns them into a `CompileError`, sorted by location. `CompileError`
subclasses `ValueError`, matching serpent's existing error convention (A10) --
`SpecTypeError`/`SpecNameError`/`SpecDocError`/`ProtocolGateError` are all
`ValueError`s too, so a caller can catch one class of failure from the whole
build.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto


class LocKind(Enum):
    """What a `Loc` points at.

    `NODE` carries a real source span (line/col through end_line/end_col).
    `WHOLE_FILE` is for module-level facts that are not about any one AST
    node -- "expected exactly one @contract class" -- and renders as
    `path:` with no line or column (dossier C.3).
    """

    NODE = auto()
    WHOLE_FILE = auto()


@dataclass(frozen=True)
class Loc:
    """A mandatory, full-span source location (dossier C.2/C.3, E5).

    `line`/`col`/`end_line`/`end_col` mirror `ast`'s own convention: `line`
    and `end_line` are 1-indexed, `col` and `end_col` are 0-indexed byte
    offsets into their line. `render()` displays `col + 1` (1-indexed), to
    match the mypy/ruff/rustc convention diagnostics are shaped after.
    """

    path: str
    kind: LocKind
    line: int
    col: int
    end_line: int
    end_col: int

    @classmethod
    def from_node(cls, path: str, node: ast.expr | ast.stmt) -> Loc:
        """Build a `NODE` `Loc` from an AST node's full span.

        `node` is `ast.expr | ast.stmt` (not the bare `ast.AST` base) because
        only those two families carry location attributes in the `ast`
        typeshed stubs -- every node `serpent.compiler` dispatches over is
        one or the other. Python 3.11+ (the project floor, E5) always
        populates `end_lineno`/`end_col_offset` on parsed nodes; a `None`
        here would mean a synthetic node was passed in, which is a compiler
        bug, not a user error -- hence the assert rather than a silent
        fallback (P2: "M1 must never fabricate a location").
        """
        end_lineno = node.end_lineno
        end_col_offset = node.end_col_offset
        assert end_lineno is not None, f"{node!r} has no end_lineno; not a parsed node"
        assert end_col_offset is not None, f"{node!r} has no end_col_offset; not a parsed node"
        return cls(
            path=path,
            kind=LocKind.NODE,
            line=node.lineno,
            col=node.col_offset,
            end_line=end_lineno,
            end_col=end_col_offset,
        )

    @classmethod
    def whole_file(cls, path: str) -> Loc:
        """A `WHOLE_FILE` `Loc` for module-level facts (P2)."""
        return cls(path=path, kind=LocKind.WHOLE_FILE, line=0, col=0, end_line=0, end_col=0)

    def sort_key(self) -> tuple[str, int, int, int, int]:
        """Stable ordering for `Diagnostics.raise_if_any()` (dossier D.2)."""
        return (self.path, self.line, self.col, self.end_line, self.end_col)


@dataclass(frozen=True)
class Diagnostic:
    """A single, structured compiler diagnostic (dossier D.2)."""

    code: str
    loc: Loc
    message: str
    help: str | None
    notes: tuple[str, ...] = ()

    def render(self, source_lines: Sequence[str]) -> str:
        """Render the mypy/ruff/rustc-shaped text form (dossier D.2)."""
        if self.loc.kind is LocKind.WHOLE_FILE:
            header = f"{self.loc.path}: error[{self.code}]: {self.message}"
            body_lines = [header]
        else:
            header = (
                f"{self.loc.path}:{self.loc.line}:{self.loc.col + 1}: "
                f"error[{self.code}]: {self.message}"
            )
            body_lines = [header, *self._render_source_snippet(source_lines)]
        if self.help is not None:
            body_lines.append(f"   help: {self.help}")
        for note in self.notes:
            body_lines.append(f"   note: {note}")
        return "\n".join(body_lines)

    def _render_source_snippet(self, source_lines: Sequence[str]) -> list[str]:
        idx = self.loc.line - 1
        if not (0 <= idx < len(source_lines)):
            return []
        line_text = source_lines[idx]
        gutter = f"{self.loc.line:>5} |"
        blank_gutter = f"{'':>5} |"
        if self.loc.end_line == self.loc.line:
            caret_len = max(1, self.loc.end_col - self.loc.col)
        else:
            caret_len = max(1, len(line_text) - self.loc.col)
        caret_line = blank_gutter + " " * self.loc.col + "^" * caret_len
        return [f"{gutter}{line_text}", caret_line]


class Diagnostics:
    """The collect-all diagnostics sink (dossier D.2, E16).

    `error()` is the sink convention every checker in `serpent.compiler`
    reports through -- no `X | Diagnostic` return unions (minor 13). It
    enforces the SPT1xxx-requires-help rule: every "unsupported construct"
    diagnostic must carry a rewrite the author can act on (F.2.11).
    """

    def __init__(self) -> None:
        self._diagnostics: list[Diagnostic] = []

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return tuple(self._diagnostics)

    def error(
        self,
        code: str,
        loc: Loc,
        message: str,
        *,
        help: str | None = None,
        notes: tuple[str, ...] = (),
    ) -> None:
        if code.startswith("SPT1") and not help:
            raise ValueError(
                f"{code}: SPT1xxx (unsupported construct) diagnostics must carry a non-empty "
                "`help` rewrite (dossier F.2.11)"
            )
        self._diagnostics.append(
            Diagnostic(code=code, loc=loc, message=message, help=help, notes=notes)
        )

    def extend(self, other: Diagnostics | Iterable[Diagnostic]) -> None:
        """Merge another sink's (or a plain iterable of) diagnostics in.

        Diagnostics coming from `other` were already validated by whichever
        `.error()` call produced them, so this does not re-run the
        help-required check.
        """
        source: Iterable[Diagnostic] = (
            other.diagnostics if isinstance(other, Diagnostics) else other
        )
        self._diagnostics.extend(source)

    def raise_if_any(self) -> None:
        """Raise `CompileError` (sorted by loc) if anything was collected."""
        if self._diagnostics:
            raise CompileError(tuple(self._diagnostics))

    def __len__(self) -> int:
        return len(self._diagnostics)

    def __bool__(self) -> bool:
        return bool(self._diagnostics)


@dataclass(eq=False)
class CompileError(ValueError):
    """A failed compile, carrying every collected `Diagnostic` (dossier D.2).

    Subclasses `ValueError`, matching serpent's existing convention (A10):
    `SpecTypeError`/`SpecNameError`/`SpecDocError`/`ProtocolGateError` are
    all `ValueError`s, so a caller can catch one class of failure from the
    whole build. `.diagnostics` is always sorted by `Loc` (dossier D.2).
    """

    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        self.diagnostics = tuple(sorted(self.diagnostics, key=lambda d: d.loc.sort_key()))
        super().__init__(f"compile failed with {len(self.diagnostics)} diagnostic(s)")

    def render(self, source_lines: Sequence[str]) -> str:
        """Render every diagnostic, in sorted order, separated by blank lines."""
        return "\n\n".join(d.render(source_lines) for d in self.diagnostics)
