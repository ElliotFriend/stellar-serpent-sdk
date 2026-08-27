"""The compiler's `Ty` model and annotation resolution (dossier §C.2).

`Ty` is the CLOSED union `serpent.compiler` reasons about internally -- it is
deliberately a different type from anything in `serpent.types`: a contract is
authored against `serpent.U32`/`serpent.Vec`/... (real, instantiable Python
classes with runtime behavior), while the compiler reasons about the SHAPE of
values at compile time, where there is no instance, only a type. Keeping them
distinct is what lets `Ty` carry compiler-only facts a runtime class has no use
for -- `.scval_rank`, `.repr_form`, `.wasm_arith_width` -- without polluting the
tier-1 oracle `serpent.types` is (A18).

`resolve_annotation` is the other half: turning one *authoring-surface*
annotation (whatever `typing.get_type_hints` handed back, under the hybrid
frontend's real-object resolution, E1) into a `Ty`. It deliberately REUSES
`serpent.spec.typemap.to_spec_type` for classification rather than
re-deriving B7's unmappable-annotation rules a second time: every annotation
`to_spec_type` accepts is exactly the set `resolve_annotation` can build a
`Ty` for (module docstring lower down details the two carve-outs), and every
`SpecTypeError` it raises is reused verbatim as this function's diagnostic
text (dossier B7: "C's type checker must pre-empt every one of these with a
better, source-located error").

Per `serpent.compiler`'s zero-dep discipline (MJ-6), this module reaches
`stellar_sdk` only transitively, through `serpent.spec` -- never directly.
"""

from __future__ import annotations

import types
import typing
from dataclasses import dataclass
from enum import Enum, auto
from typing import ClassVar

from serpent.compiler import codes
from serpent.compiler.diagnostics import Diagnostics, Loc
from serpent.compiler.loader import CompilerBugError, LoadedModule
from serpent.decorators import _METADATA_ATTR
from serpent.spec import SpecTypeError, to_spec_type
from serpent.types import (
    I32,
    I64,
    I128,
    U32,
    U64,
    U128,
    Address,
    Bool,
    Bytes,
    Duration,
    Map,
    String,
    Symbol,
    Timepoint,
    Vec,
)

__all__ = ["ReprForm", "Ty", "TyTag", "resolve_annotation"]

#: `code -> message_intent`, so every diagnostic this module raises carries
#: its registry row's own wording (matches `loader.py`'s convention).
_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}


class ReprForm(Enum):
    """How a `Ty` is represented as a Val (dossier §C.2 `.repr`; A3).

    `IMMEDIATE`: always the inline/small Val form -- no host object handle is
    ever allocated. `HOST_OBJECT`: always an object handle. `EITHER`: which
    form a given VALUE takes depends on its magnitude/length at runtime (the
    small-value bounds in `serpent.val`/A3), so the compiler cannot decide
    `obj_cmp` vs. a raw `i64` compare from the TYPE alone -- T5's mandate
    ("route every Symbol comparison through obj_cmp") is exactly what a naive
    per-type dispatch here would get wrong for `EITHER` types.
    """

    IMMEDIATE = auto()
    HOST_OBJECT = auto()
    EITHER = auto()


class TyTag(Enum):
    """The discriminant of `Ty`'s closed union (dossier §C.2)."""

    BOOL = auto()
    U32 = auto()
    I32 = auto()
    U64 = auto()
    I64 = auto()
    U128 = auto()
    I128 = auto()
    TIMEPOINT = auto()
    DURATION = auto()
    SYMBOL = auto()
    STRING = auto()
    BYTES = auto()
    BYTES_N = auto()
    ADDRESS = auto()
    VEC = auto()
    MAP = auto()
    STRUCT = auto()
    OPTION = auto()
    VOID = auto()
    ERROR_ENUM = auto()
    #: The sink-reported-failure sentinel (minor 13). Never a real type.
    INVALID = auto()


#: Tags whose Val is always the inline/small form (Bool/U32/I32 always fit;
#: Void's TAG_VOID and an Error Val's packed (code, error_type) are both
#: inline forms too -- see the `ReprForm` docstring above for why EITHER
#: types cannot be folded in here).
_IMMEDIATE_TAGS: frozenset[TyTag] = frozenset(
    {TyTag.BOOL, TyTag.U32, TyTag.I32, TyTag.VOID, TyTag.ERROR_ENUM}
)

#: Tags whose Val form depends on the VALUE, not the type (A3's small-value
#: bounds): U64/I64/U128/I128/Timepoint/Duration split at the small-int
#: bounds, Symbol at the <=9-character SymbolSmall bound (S22).
_EITHER_TAGS: frozenset[TyTag] = frozenset(
    {
        TyTag.U64,
        TyTag.I64,
        TyTag.U128,
        TyTag.I128,
        TyTag.TIMEPOINT,
        TyTag.DURATION,
        TyTag.SYMBOL,
    }
)

#: Tags that are always a host object -- no small form exists at all.
_HOST_OBJECT_TAGS: frozenset[TyTag] = frozenset(
    {
        TyTag.STRING,
        TyTag.BYTES,
        TyTag.BYTES_N,
        TyTag.ADDRESS,
        TyTag.VEC,
        TyTag.MAP,
        TyTag.STRUCT,
    }
)

#: `_SCVAL_RANK` (dossier A8, `ScValType` order -- NOT tag order). `Bytes`
#: and `BytesN(n)` share one rank (D5: payload-based equality/ordering across
#: the whole `Bytes` family). `Struct` shares `Map`'s rank because a struct
#: compiles to `Map<Symbol, V>` (S9) -- it is the same `ScVal` case on-chain.
#: `ErrorEnum` takes the table's `Error` rank; `Option` has no rank of its
#: OWN (it is not a distinct `ScVal` case -- on-chain it is either `Void` or
#: whatever the wrapped type is), so its `scval_rank` delegates to its
#: wrapped `Ty` rather than appearing in this table (see the property below).
_SCVAL_RANK: dict[TyTag, int] = {
    TyTag.BOOL: 0,
    TyTag.VOID: 1,
    TyTag.ERROR_ENUM: 2,
    TyTag.U32: 3,
    TyTag.I32: 4,
    TyTag.U64: 5,
    TyTag.I64: 6,
    TyTag.TIMEPOINT: 7,
    TyTag.DURATION: 8,
    TyTag.U128: 9,
    TyTag.I128: 10,
    TyTag.BYTES: 13,
    TyTag.BYTES_N: 13,
    TyTag.STRING: 14,
    TyTag.SYMBOL: 15,
    TyTag.VEC: 16,
    TyTag.MAP: 17,
    TyTag.STRUCT: 17,
    TyTag.ADDRESS: 18,
}

#: `.wasm_arith_width` (F.1.11): 32 for the true 32-bit types, 64 for
#: everything wider. `U32`/`I32` are 32 -- the divergence risk F.1.11 names
#: is exactly typing an `I32` expression as `I64` and silently losing 32-bit
#: overflow checking, so the field's whole job is to keep that distinction
#: visible in `Ty` itself. `U64`/`I64` are natively 64-bit wasm arithmetic;
#: `U128`/`I128` still route through 64-bit wasm locals (the guest runtime
#: library operates limb-by-limb over `hi64`/`lo64`, spec §6), so they are
#: also 64, not a third "128" value that no wasm type has. `Timepoint`/
#: `Duration` have no arithmetic at all (D4) and every non-arithmetic `Ty`
#: is `None` -- there is no wasm arithmetic width question to answer.
_ARITH_WIDTH: dict[TyTag, int] = {
    TyTag.U32: 32,
    TyTag.I32: 32,
    TyTag.U64: 64,
    TyTag.I64: 64,
    TyTag.U128: 64,
    TyTag.I128: 64,
}

#: Render form for the tags with no parameters at all.
_SCALAR_RENDER: dict[TyTag, str] = {
    TyTag.BOOL: "Bool",
    TyTag.U32: "U32",
    TyTag.I32: "I32",
    TyTag.U64: "U64",
    TyTag.I64: "I64",
    TyTag.U128: "U128",
    TyTag.I128: "I128",
    TyTag.TIMEPOINT: "Timepoint",
    TyTag.DURATION: "Duration",
    TyTag.SYMBOL: "Symbol",
    TyTag.STRING: "String",
    TyTag.BYTES: "Bytes",
    TyTag.ADDRESS: "Address",
    TyTag.VOID: "Void",
}


@dataclass(frozen=True)
class Ty:
    """The compiler's closed type union (dossier §C.2).

    One dataclass for every variant, discriminated by `tag`; only the fields
    a given tag actually uses are populated (`elem` for `Vec`/`Option`,
    `key`/`value` for `Map`, `name` for `Struct`/`ErrorEnum`, `n` for
    `BytesN`). This -- rather than one subclass per variant -- is what gives
    equality/hashing for free from `@dataclass(frozen=True)`: two `Ty`s
    compare equal exactly when their `(tag, elem, key, value, name, n)`
    tuples match, so `Ty.Vec(Ty.U32) == Ty.Vec(Ty.U32)` holds structurally and
    `Ty` is usable as a dict key or set member.

    The no-parameter variants are pre-built singletons (`Ty.Bool`, `Ty.U32`,
    ...); the parametrized ones are built through the `Ty.Vec(...)`-style
    static methods below, mirroring the dossier's own `Vec(Ty)`/`Map(Ty,
    Ty)`/`Struct(name)`/`Option(Ty)`/`ErrorEnum(name)`/`BytesN(n)` notation.
    """

    tag: TyTag
    elem: Ty | None = None
    key: Ty | None = None
    value: Ty | None = None
    name: str | None = None
    n: int | None = None

    # --- singletons (populated after the class body; see the bottom of this
    # module) and parametrized constructors -----------------------------

    Bool: ClassVar[Ty]
    U32: ClassVar[Ty]
    I32: ClassVar[Ty]
    U64: ClassVar[Ty]
    I64: ClassVar[Ty]
    U128: ClassVar[Ty]
    I128: ClassVar[Ty]
    Timepoint: ClassVar[Ty]
    Duration: ClassVar[Ty]
    Symbol: ClassVar[Ty]
    String: ClassVar[Ty]
    Bytes: ClassVar[Ty]
    Address: ClassVar[Ty]
    Void: ClassVar[Ty]
    #: The sink-reported-failure sentinel (minor 13): a resolver that has
    #: already reported a diagnostic to the sink may return this instead of
    #: `None`, so a caller that must keep flowing a `Ty` (rather than an
    #: `Optional[Ty]` at every step) has one to flow. `resolve_annotation`
    #: itself returns `Ty | None` per its own interface; `Ty.Invalid` is for
    #: the later checkers (Tasks 5-9) that prefer a non-`None` placeholder.
    Invalid: ClassVar[Ty]

    @staticmethod
    def BytesN(n: int) -> Ty:
        return Ty(TyTag.BYTES_N, n=n)

    @staticmethod
    def Vec(elem: Ty) -> Ty:
        return Ty(TyTag.VEC, elem=elem)

    @staticmethod
    def Map(key: Ty, value: Ty) -> Ty:
        return Ty(TyTag.MAP, key=key, value=value)

    @staticmethod
    def Struct(name: str) -> Ty:
        return Ty(TyTag.STRUCT, name=name)

    @staticmethod
    def Option(elem: Ty) -> Ty:
        return Ty(TyTag.OPTION, elem=elem)

    @staticmethod
    def ErrorEnum(name: str) -> Ty:
        return Ty(TyTag.ERROR_ENUM, name=name)

    # --- derived facts ---------------------------------------------------

    @property
    def repr_form(self) -> ReprForm:
        """IMMEDIATE | HOST_OBJECT | EITHER (dossier §C.2 `.repr`, A3)."""
        if self.tag is TyTag.INVALID:
            raise ValueError(
                "Ty.Invalid carries no representation -- it is a sink-reported-failure "
                "sentinel (minor 13), never a real type; a checker that reaches this is a "
                "compiler bug (the diagnostic was already reported; nothing should keep "
                "asking the invalid Ty representation questions)"
            )
        if self.tag is TyTag.OPTION:
            assert self.elem is not None
            # An Option's Val is either the immediate Void tag or the wrapped
            # type's own encoding. When the wrapped type is always immediate,
            # BOTH possibilities are immediate; otherwise at least one
            # possibility is an object handle, so the honest answer is EITHER.
            return (
                ReprForm.IMMEDIATE if self.elem.repr_form is ReprForm.IMMEDIATE else ReprForm.EITHER
            )
        if self.tag in _IMMEDIATE_TAGS:
            return ReprForm.IMMEDIATE
        if self.tag in _EITHER_TAGS:
            return ReprForm.EITHER
        if self.tag in _HOST_OBJECT_TAGS:
            return ReprForm.HOST_OBJECT
        raise AssertionError(f"unhandled Ty tag in repr_form: {self.tag!r}")  # pragma: no cover

    @property
    def scval_rank(self) -> int:
        """The A8 `_SCVAL_RANK` table, for any compiler-side ordering."""
        if self.tag is TyTag.INVALID:
            raise ValueError("Ty.Invalid has no scval_rank -- see repr_form's docstring")
        if self.tag is TyTag.OPTION:
            assert self.elem is not None
            return self.elem.scval_rank
        return _SCVAL_RANK[self.tag]

    @property
    def wasm_arith_width(self) -> int | None:
        """32 or 64 for the natively-arithmetic int types; `None` otherwise
        (F.1.11)."""
        return _ARITH_WIDTH.get(self.tag)

    def render(self) -> str:
        """A short, diagnostic-friendly spelling (mirrors the dossier's own
        `Vec(Ty)`/`Map(Ty, Ty)`/... notation)."""
        if self.tag is TyTag.BYTES_N:
            assert self.n is not None
            return f"BytesN({self.n})"
        if self.tag is TyTag.VEC:
            assert self.elem is not None
            return f"Vec({self.elem.render()})"
        if self.tag is TyTag.MAP:
            assert self.key is not None and self.value is not None
            return f"Map({self.key.render()}, {self.value.render()})"
        if self.tag is TyTag.STRUCT:
            assert self.name is not None
            return f"Struct({self.name})"
        if self.tag is TyTag.OPTION:
            assert self.elem is not None
            return f"Option({self.elem.render()})"
        if self.tag is TyTag.ERROR_ENUM:
            assert self.name is not None
            return f"ErrorEnum({self.name})"
        if self.tag is TyTag.INVALID:
            return "<invalid>"
        return _SCALAR_RENDER[self.tag]


Ty.Bool = Ty(TyTag.BOOL)
Ty.U32 = Ty(TyTag.U32)
Ty.I32 = Ty(TyTag.I32)
Ty.U64 = Ty(TyTag.U64)
Ty.I64 = Ty(TyTag.I64)
Ty.U128 = Ty(TyTag.U128)
Ty.I128 = Ty(TyTag.I128)
Ty.Timepoint = Ty(TyTag.TIMEPOINT)
Ty.Duration = Ty(TyTag.DURATION)
Ty.Symbol = Ty(TyTag.SYMBOL)
Ty.String = Ty(TyTag.STRING)
Ty.Bytes = Ty(TyTag.BYTES)
Ty.Address = Ty(TyTag.ADDRESS)
Ty.Void = Ty(TyTag.VOID)
Ty.Invalid = Ty(TyTag.INVALID)


#: `resolve_annotation`'s scalar leaves, once `to_spec_type` has already
#: proven `obj` mappable -- exact-class membership (B8), mirroring
#: `typemap._SCALARS` (never `issubclass`; an author subclass of `U32` is not
#: in this dict, matching `to_spec_type`'s own strictness).
_SCALAR_TY: dict[type, Ty] = {
    Bool: Ty.Bool,
    U32: Ty.U32,
    I32: Ty.I32,
    U64: Ty.U64,
    I64: Ty.I64,
    Timepoint: Ty.Timepoint,
    Duration: Ty.Duration,
    U128: Ty.U128,
    I128: Ty.I128,
    String: Ty.String,
    Symbol: Ty.Symbol,
    Address: Ty.Address,
}


def resolve_annotation(obj: object, loaded: LoadedModule, loc: Loc, sink: Diagnostics) -> Ty | None:
    """Resolve one authoring-surface annotation to a `Ty`, or report it and
    return `None` (sink convention, minor 13 -- never raises, never returns a
    `Diagnostic`).

    `obj` is a real, already-resolved Python object -- what `typing.
    get_type_hints` (via `decorators._annotations_of`, run at module-exec
    time under the hybrid frontend, E1) hands back for a parameter, return
    type, or `@contracttype`/`@contractevent` field: a chain-type class, a
    `Vec[T]`/`Map[K, V]` generic alias, a `X | None` union (either spelling),
    a `@contracttype`/`@contracterror` class, `bytes_n(N)`'s generated
    subclass, or an unmappable annotation (`Env`, `None`, a plain `int`/
    `str`/`bytes`/`bool`, ...). `loaded` is the module `obj` was resolved
    from; it is used only to assert that a resolved struct really is in this
    module's OWN declared-type inventory (`loaded.decorated_types_in_order`)
    -- a mismatch would mean a class from somewhere else reached this
    function, which cannot happen from real source (imports are restricted to
    `serpent.__all__`, A22) and would therefore be a compiler bug, not a user
    error (mirrors `loader.py`'s F.1.14 cross-check discipline).

    Classification is DELEGATED to `serpent.spec.typemap.to_spec_type` rather
    than re-derived: every annotation it accepts is exactly the set this
    function can build a `Ty` for, and every `SpecTypeError` it raises is
    reused VERBATIM as this function's diagnostic text under `SPT3013`
    (dossier B7 -- "C's type checker must pre-empt every one of these with a
    better, source-located error"; reusing the string is what keeps the two
    from drifting apart under independent edits). Two cases are intercepted
    BEFORE that delegation, because they need a DIFFERENT code than
    `to_spec_type`'s own text would produce:

    * An `@contracterror` class always reports `SPT3001` ("Error is never a
      returnable value", S8) here, never the generic `SPT3013` bucket --
      `SPT3013`'s own construct text lists "@contracterror used as a type" as
      one of the many things it covers, but the registry carved S8's framing
      out into its own code because Task 6 raises the SAME code again when
      checking an actual `return <expr>` whose value is Error-typed; one code
      for the one rule, S8, wherever it is checked.
    * A bare `Error` value never reaches this function at all: `raise
      MyError.Member` is checked structurally in raise position (Task 5/6),
      never through annotation resolution, so `Ty.ErrorEnum(name)` is never
      constructed here -- it exists in the `Ty` union for whichever later
      task types the `Raise`/`ErrorVal` IR nodes.

    **Position is the CALLER's job, not this function's -- for both `Env` and
    `None`:**

    * `Env` is legitimate ONLY as a contract method's leading (post-`self`)
      parameter, and even then it never becomes a `Ty` at all (it has no
      variant in the closed union) -- `sections.py` drops it before ever
      calling `to_spec_type`, and the caller building `FuncCtx.params` must
      do the same: recognize the leading `Env` positionally and never call
      `resolve_annotation` on it. If `Env` DOES reach this function (any
      other position), `to_spec_type` rejects it with its own message
      ("Env is the host handle, not a contract value..."), reused here under
      `SPT3013` -- no separate marker return is needed because the reject
      path already exists and the legitimate path never calls in.
    * `None`/`NoneType` is similarly always rejected here (`to_spec_type`'s
      own "a void return has no type def" text, reused under `SPT3013`) --
      `Ty.Void` exists in the closed union precisely for a method's `-> None`
      return type, but the CALLER constructs `Ty.Void` directly for that one
      position without ever calling `resolve_annotation` on `None`. This is
      the same shape of decision as `Env`'s, made the same way.

    `bytes_n(N)` (A16/E20) needs no special-casing at all: by the time an
    annotation reaches this function it has already been evaluated (the
    hybrid frontend's whole point, E1), so `bytes_n(20)` in an annotation IS
    already the generated fixed-length `Bytes` subclass with `_LENGTH == 20`
    set -- it falls straight into the same `issubclass(obj, Bytes)` /
    `_LENGTH` branch `Bytes32`/`Bytes64` do (B8's own convention).
    """
    if isinstance(obj, type):
        metadata = vars(obj).get(_METADATA_ATTR)
        if isinstance(metadata, dict) and metadata.get("kind") == "error_enum":
            sink.error(
                "SPT3001",
                loc,
                _INTENT["SPT3001"],
                help=(
                    f"`{obj.__name__}` may only appear in `raise {obj.__name__}.<Member>`; "
                    "it cannot be a parameter, field, or return type"
                ),
                notes=(f"`{obj.__name__}` is declared with `@contracterror`",),
            )
            return None
    try:
        to_spec_type(obj)
    except SpecTypeError as exc:
        sink.error(
            "SPT3013",
            loc,
            f"{_INTENT['SPT3013']}: {exc}",
            help=(
                "use one of serpent's chain types, a `@contracttype` struct, `X | None` of "
                "one, `Vec[T]`, or `Map[K, V]`"
            ),
            notes=(str(exc),),
        )
        return None
    return _build_ty(obj, loaded)


def _build_ty(obj: object, loaded: LoadedModule) -> Ty:
    """Build the `Ty` for `obj`, which `to_spec_type` has already proven
    mappable (and which is not an `@contracterror` class -- intercepted
    before this is ever called). Mirrors `to_spec_type`'s own dispatch
    (`typing.get_origin`/`get_args`, exact-class scalar lookup, `issubclass
    (Bytes)`) so the two stay classifying the SAME tree the SAME way.
    """
    origin = typing.get_origin(obj)
    if origin is typing.Union or origin is types.UnionType:
        args = [arg for arg in typing.get_args(obj) if arg is not types.NoneType]
        assert len(args) == 1, f"to_spec_type accepted a non-Optional union: {obj!r}"
        return Ty.Option(_build_ty(args[0], loaded))
    if origin is Vec:
        (elem,) = typing.get_args(obj)
        return Ty.Vec(_build_ty(elem, loaded))
    if origin is Map:
        key, value = typing.get_args(obj)
        return Ty.Map(_build_ty(key, loaded), _build_ty(value, loaded))
    assert isinstance(obj, type), f"to_spec_type accepted a non-type, non-generic: {obj!r}"
    if issubclass(obj, Bytes):
        length = obj._LENGTH
        return Ty.Bytes if length is None else Ty.BytesN(length)
    scalar = _SCALAR_TY.get(obj)
    if scalar is not None:
        return scalar
    # The only shape `to_spec_type` still accepts at this point is a
    # `@contracttype` struct (error enums were intercepted before the
    # `to_spec_type` call; every other kind of decorated/undecorated class,
    # and every non-chain type, is one of `to_spec_type`'s OWN rejections).
    _assert_declared_struct(obj, loaded)
    return Ty.Struct(obj.__name__)


def _assert_declared_struct(obj: type, loaded: LoadedModule) -> None:
    """F.1.14-style invariant: a struct `resolve_annotation` accepts must be
    in the SAME module's own declared-type inventory. Real source cannot
    violate this (imports are restricted to `serpent.__all__`, A22, so no
    struct class from anywhere else is reachable) -- a violation here is a
    compiler bug, not a user error, exactly like `loader._cross_check_
    inventory`'s skew check.
    """
    if not any(decl.cls is obj for decl in loaded.decorated_types_in_order):
        raise CompilerBugError(
            f"resolve_annotation resolved {obj!r} as a struct, but it is not in "
            f"{loaded.path}'s declared-type inventory (loaded.decorated_types_in_order); "
            "only a class reachable from the loaded module's own namespace should ever "
            "reach this point"
        )
