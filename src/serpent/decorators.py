"""The six contract decorators and the `errorcode` field specifier.

These are serpent's *authoring* surface: they run at class-creation time in
CPython, validate the declaration, and record a small `_serpent_type_`
metadata dict that sub-plan C's compiler reads back. They add no runtime
behavior to a deployed contract -- on chain, the compiler has already turned
the decorated class into WASM.

**The zero-plugin `mypy --strict` claim lives here.** A type checker never
executes a decorator, so anything a decorator *installs* is invisible to it.
Every authoring form below is therefore designed to be correct under strict
type checking with the decorator treated as an identity function:

* `NAME = errorcode(N)` is annotated `-> type[ContractError]`, so
  `raise Error.LimitExceeded` type-checks. The bare `NAME = N` form cannot:
  it is inferred `int` and the raise site fails with "Exception must be
  derived from BaseException" (verified by live repro; no decorator return-type
  trick rescues it). `@contracterror` rejects bare ints with a message showing
  the `errorcode(...)` form.
* `@contracttype` / `@contractevent` are `@dataclass_transform()`-annotated,
  so kwargs construction type-checks against the field annotations. That holds
  for `@contractevent`'s FACTORY spelling too (`@contractevent(topics=...)`):
  the transform is declared on the overloads, and mypy synthesizes the same
  `__init__` either way (probe-verified -- `Transfer(nope=1)` is a `call-arg`
  error under both spellings).
* `@contractevent` classes inherit `Event`, so `Transfer(...).publish(env)`
  resolves to a method the checker can actually see. The same rule in reverse
  is why `_serpent_type_` is *never* read through `getattr` in typed code: a
  decorator-installed attribute is invisible, so it is metadata for the
  compiler, not part of the authoring surface.
* `@contractunion` / `@contractenum` classes inherit `ContractUnion` /
  `ContractEnum` for the same reason `@contractevent` classes inherit `Event`:
  the base is the only part of the declaration a checker reads, and SS C.8
  probe-verified that a base-less class is not statically a `ChainValue` at any
  position. Their CASES are typed by the descriptors `types._udt` declares --
  `Shape.Circle` as `(U32) -> Shape` -- so a wrong payload type, a wrong arity
  and calling a unit variant are all static errors with no plugin; the
  decorator's own job is to bind each case to its NAME (which the `variant()`
  factory cannot see) and to refuse a malformed declaration at its own site.
* `@contract` methods take `self` first, which is what makes them ordinary,
  strict-clean Python methods (the compiler ignores `self`).

**The event topic convention's one seam** is `_build_record`'s
`get_type_hints(..., include_extras=True)`. `Annotated[Address, topic]` is how
an author marks a topic field, and WITHOUT that flag `get_type_hints` strips
the `Annotated` wrapper silently, so the marker would never be seen and every
event would compile as all-data. The marker is read there and the annotation
recorded in `_serpent_type_` is the STRIPPED one, which is why nothing
downstream -- `spec.typemap`, the compiler's annotation resolver -- knows
`Annotated` exists at all. `topic` and `Annotated` are both exported from the
`serpent` root because a contract may import from `serpent` and nowhere else.

NOTE (Python 3.11 floor): `dataclass_transform(frozen_default=True)` is 3.12+,
and serpent takes no runtime dependencies, so the transform cannot advertise
frozen-ness to the checker. The consequence is narrow and static-only: mypy
will not flag mutation of a `@contracttype` field. The runtime is genuinely
frozen -- `dataclasses.dataclass(frozen=True)` still raises
`FrozenInstanceError` -- and sub-plan C rejects field assignment at compile
time.
"""

from __future__ import annotations

import dataclasses
import inspect
import types
import typing
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, NoReturn, TypeVar, cast, dataclass_transform, overload

from serpent import val
from serpent.env import Event
from serpent.errors import RESERVED_CODE_MIN, ContractError
from serpent.types import U32, ContractEnum, ContractUnion, Map, Symbol, Vec
from serpent.types._base import _ChainValue
from serpent.types._udt import _bind_variant, _EnumValue, _VariantSpec

__all__ = [
    "contract",
    "contractenum",
    "contracterror",
    "contractevent",
    "contracttype",
    "contractunion",
    "errorcode",
    "topic",
]

_T = TypeVar("_T")

#: Spec XDR caps function and field names at 30 characters. This is *not*
#: `val.SCSYMBOL_LIMIT` (32, the on-chain Symbol cap) -- a 31-character name is
#: a representable Symbol that the contract spec cannot carry.
NAME_LIMIT = 30

#: The metadata every serpent-decorated class carries. Sub-plan C reads it.
_METADATA_ATTR = "_serpent_type_"

#: An event's `prefix_topics` is an XDR `SCSymbol<2>`: two, never three. The cap
#: is pre-validated HERE, at the declaration site, so an author never sees the
#: `stellar_sdk` constructor's field-only ValueError (R5).
PREFIX_TOPIC_LIMIT: Final = 2

#: The three `SCSpecEventDataFormat` cases, spelled as an author writes them.
#: `"single-value"` carries the field arity rule: the host publishes ONE data
#: value, so the event must declare exactly one non-topic field.
DATA_FORMATS: Final = ("map", "vec", "single-value")

#: A field's `location` in `SCSpecEventParamV0`: a topic-list entry or a data
#: member. Recorded per field in an event's metadata under `"locations"`.
TOPIC_LOCATION: Final = "topic"
DATA_LOCATION: Final = "data"


class _Topic:
    """The type of the `topic` marker (`Annotated[Address, topic]`).

    A class of its own rather than a bare `object()` so the marker has a
    `__repr__`: an author who prints an annotation, or reads it back off a
    traceback, sees `topic` and not `<object object at 0x...>`.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "topic"


#: The event-topic marker. `Annotated[T, topic]` says "this field is published
#: as a TOPIC, not as event data"; an unmarked field is data. Exported from the
#: `serpent` root next to `Annotated` itself, because a contract module may
#: import from `serpent` and nowhere else (SPT2005).
topic: Final = _Topic()


class _ErrorCode:
    """What `errorcode(N)` really returns: a placeholder carrying the code.

    `@contracterror` replaces every placeholder in the class body with a real
    generated `ContractError` subclass. The placeholder exists only between
    the class body executing and the decorator running, so it is never visible
    to contract authors -- and the *static* type of `errorcode(N)` is
    `type[ContractError]`, which is what the raise site needs.
    """

    __slots__ = ("code",)

    def __init__(self, code: int) -> None:
        self.code = code

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"errorcode({self.code})"


def errorcode(code: int) -> type[ContractError]:
    """Declare one member of a `@contracterror` enum.

    Annotated `-> type[ContractError]` so that `raise Error.NAME` is
    strict-clean before the decorator has run; returns a placeholder that
    `@contracterror` swaps for the generated exception class. The call also
    gives sub-plan C an unambiguous `ast.Call` to read the code from.
    """
    if not isinstance(code, int) or isinstance(code, bool):
        raise TypeError(f"errorcode() takes an int code, not {type(code).__name__}")
    return cast("type[ContractError]", _ErrorCode(code))


def contracterror(cls: type[_T]) -> type[_T]:
    """Turn a class of `NAME = errorcode(N)` members into exception classes.

    Each member becomes a generated `ContractError` subclass named for the
    attribute, carrying `code = N`, so `raise Error.NAME` raises a real
    exception whose `.code` is what the host will see under
    `ScErrorType::Contract`.

    Codes must be unique within the enum and in `[0, RESERVED_CODE_MIN)` --
    the top 256 codes are serpent's own runtime errors. `ContractError`
    enforces the u32 range again at class creation; the check here exists so
    the failure is reported at the *declaration site* as a `ValueError`.
    """
    cases: list[tuple[str, int]] = []
    seen: dict[int, str] = {}

    for name, value in list(vars(cls).items()):
        if name.startswith("_"):
            continue
        if not isinstance(value, _ErrorCode):
            _reject_bare_member(cls, name, value)
        code = value.code
        if not 0 <= code < RESERVED_CODE_MIN:
            raise ValueError(
                f"{cls.__name__}.{name}: error code {code} is out of range -- "
                f"contract codes are 0 <= code < {RESERVED_CODE_MIN} "
                "(the top 256 u32 codes are reserved for serpent's runtime errors)"
            )
        if code in seen:
            raise ValueError(
                f"{cls.__name__}.{name}: error code {code} is already used by "
                f"{cls.__name__}.{seen[code]} -- codes must be unique within an enum"
            )
        seen[code] = name
        cases.append((name, code))

    if not cases:
        raise ValueError(
            f"{cls.__name__}: @contracterror needs at least one member "
            f"(`NAME = errorcode(N)`); an empty error enum contributes nothing "
            "to the contract spec"
        )

    for name, code in cases:
        setattr(cls, name, _make_error_class(cls, name, code))

    setattr(cls, _METADATA_ATTR, {"kind": "error_enum", "cases": cases})
    return cls


def _reject_bare_member(cls: type[Any], name: str, value: object) -> NoReturn:
    """Reject a member that is not an `errorcode(...)` placeholder.

    A `ValueError` rather than a `TypeError`: every decoration-site failure in
    this module is a `ValueError`, so an author can catch one class of error.
    Lives in its own function so the raise is not lexically inside an
    `isinstance` guard, which is what would otherwise read as a type check.
    """
    raise ValueError(
        f"{cls.__name__}.{name}: @contracterror members must be declared "
        f"as `{name} = errorcode(N)`, not `{name} = {value!r}`. "
        "A bare value is inferred as its Python type by static checkers, "
        f"so `raise {cls.__name__}.{name}` would fail mypy --strict."
    )


def _make_error_class(owner: type[Any], name: str, code: int) -> type[ContractError]:
    """Build the `ContractError` subclass that replaces one placeholder."""
    return type(
        name,
        (ContractError,),
        {
            "code": code,
            "__module__": owner.__module__,
            "__qualname__": f"{owner.__qualname__}.{name}",
            "__doc__": f"Contract error {code} ({owner.__name__}.{name}).",
        },
    )


def contractunion(cls: type[_T]) -> type[_T]:
    """Declare a tagged union: one case per `NAME = variant(...)` member.

    Each case becomes the bound descriptor that knows its case NAME -- which
    the `variant()` factory cannot see, because the name is the attribute it is
    assigned to -- so `Shape.Empty` is a value and `Shape.Circle(U32(1))`
    builds one, both checked by `mypy --strict` with no plugin (ruling E1).
    The metadata records `(name, payload annotations)` per case in DECLARATION
    order, which is the order the on-chain `ScVec` carries the payload in.

    The class **must** inherit `ContractUnion` and nothing else. The base is
    what makes the class statically a chain value at every position (SS C.8
    probe-verified that a base-less class is not: `error: incompatible type
    "ColorNoBase"; expected "_ChainValue | Struct"`), and a decorator cannot
    add a base a checker can see. Subclassing a DECLARED union is refused by
    the same check, and deliberately: a variant descriptor constructs the class
    the case was declared in, so `class Sub(Shape)` would type as `Sub` and
    build a `Shape`.

    Case NAMES are **not** checked here (plan-review B1). `_check_name` caps at
    `NAME_LIMIT` (30) and bridges to `SPT5001`, which would refuse a
    40-character int-enum case name ruling E8 makes legal; the located,
    per-kind refusal lives in `compiler/limits.py` (32 for a variant, which
    becomes a runtime `Symbol`; 60 for an int-enum case, which never does).
    `Symbol.__init__`'s own 32-character check is the tier-1 backstop when
    `tag()` builds the name.

    Not a dataclass, deliberately (ruling E9): see `types._udt`'s module
    docstring for the three tag doors a dataclass union would silently fall
    through.
    """
    _reject_redecoration(cls)
    _check_udt_base(cls, ContractUnion, "@contractunion")
    specs: list[tuple[str, _VariantSpec]] = []
    cases: list[tuple[str, tuple[object, ...]]] = []

    for name, value in list(vars(cls).items()):
        if name.startswith("_"):
            continue
        if not isinstance(value, _VariantSpec):
            _reject_bare_case(cls, name, value, "@contractunion", f"{name} = variant(...)")
        for annotation in value.payload:
            if not _is_contract_annotation(annotation):
                raise ValueError(
                    f"{cls.__name__}.{name}: payload annotation {_render(annotation)} is not a "
                    "chain type, a `@contracttype` struct, or `X | None` of one"
                )
        specs.append((name, value))
        cases.append((name, tuple(value.payload)))

    if not cases:
        _reject_empty_udt(cls, "@contractunion", "Name = variant(...)", "union")

    # Every case is validated before a single descriptor is installed, so a
    # rejected class is left exactly as its body built it (`@contracterror`'s
    # own no-partial-mutation property).
    for name, spec in specs:
        setattr(cls, name, _bind_variant(name, cast("type[ContractUnion]", cls), spec))

    setattr(cls, _METADATA_ATTR, {"kind": "union", "cases": cases})
    return cls


def contractenum(cls: type[_T]) -> type[_T]:
    """Declare an int enum: one case per `NAME = enumvalue(N)` member.

    Nothing is rebound here, unlike `@contractunion`: `enumvalue(N)` already
    carries the discriminant and a descriptor's `__get__` is handed the owner
    class, so `Level.Low` builds a `Level` with no swap at all. What this
    validates is the declaration -- the base, the member form, the u32 range
    and uniqueness -- and records `(name, discriminant)` per case, the SAME
    pair shape `@contracterror` records, so `sections._enum_entry`'s template
    and the loader's case cross-check are reusable rather than re-derived.

    **Discriminants are always explicit** (ruling E5): Rust unwraps on a
    missing one, so an implicit numbering would be an on-chain value serpent
    INVENTS -- and reordering the class body would then silently change stored
    data. Each one must be a `u32`, because the value IS a bare `u32` on chain;
    `enumvalue(-1)` would otherwise declare a spec entry no `u32` could hold.

    Same base rule, same case-name rule and same non-dataclass rule as
    `@contractunion` (see its docstring).
    """
    _reject_redecoration(cls)
    _check_udt_base(cls, ContractEnum, "@contractenum")
    cases: list[tuple[str, int]] = []
    seen: dict[int, str] = {}

    for name, value in list(vars(cls).items()):
        if name.startswith("_"):
            continue
        if not isinstance(value, _EnumValue):
            _reject_bare_case(cls, name, value, "@contractenum", f"{name} = enumvalue(N)")
        discriminant = value._discriminant
        if not U32.MIN <= discriminant <= U32.MAX:
            raise ValueError(
                f"{cls.__name__}.{name}: discriminant {discriminant} is out of range -- an "
                f"int-enum member IS a bare u32 on chain, so {U32.MIN} <= N <= {U32.MAX}"
            )
        if discriminant in seen:
            raise ValueError(
                f"{cls.__name__}.{name}: discriminant {discriminant} is already declared by "
                f"{cls.__name__}.{seen[discriminant]} -- two members sharing one discriminant "
                "are the same bare u32 on chain, so the pair could never be told apart"
            )
        seen[discriminant] = name
        cases.append((name, discriminant))

    if not cases:
        _reject_empty_udt(cls, "@contractenum", "NAME = enumvalue(N)", "int enum")

    setattr(cls, _METADATA_ATTR, {"kind": "enum", "cases": cases})
    return cls


def _check_udt_base(cls: type[Any], base: type[Any], decorator: str) -> None:
    """Refuse a union/int enum whose bases are not exactly `(base,)`.

    One check for three mistakes -- no base at all, the wrong base, and
    subclassing a DECLARED union/int enum -- because all three break the same
    rule: the base is what a type checker reads, and the decorator that would
    have installed anything is invisible to it. `__bases__`, not `__mro__`, is
    what makes the subclass case fall out for free.
    """
    if cls.__bases__ != (base,):
        spelled = ", ".join(b.__name__ for b in cls.__bases__ if b is not object) or "no base class"
        raise ValueError(
            f"{cls.__name__}: a {decorator} class declares exactly one base, "
            f"`{base.__name__}` (`class {cls.__name__}({base.__name__}):`) -- got {spelled}. "
            "The base is what makes the class statically a chain value at every position (a "
            "decorator cannot add one a type checker can see), and a subclass of a declared "
            "type would type as the subclass while constructing the declared one."
        )


def _reject_bare_case(
    cls: type[Any], name: str, value: object, decorator: str, form: str
) -> NoReturn:
    """Reject a union/int-enum member that is not its factory's placeholder.

    `_reject_bare_member`'s rule for the two M1-E2 kinds, and in its own
    function for the same reason: the raise is not lexically inside an
    `isinstance` guard. The `name: T = value` spelling reaches here too -- it
    is a form the class body admits (an error enum needs it), so the decorator
    is what refuses the named-FIELD variant a Rust author reaches for.
    """
    raise ValueError(
        f"{cls.__name__}.{name}: a {decorator} case is declared as `{form}`, not "
        f"`{name} = {value!r}`. A bare value is inferred as its Python type by static "
        f"checkers, so `{cls.__name__}.{name}` would not be a {cls.__name__} at all."
    )


def _reject_empty_udt(cls: type[Any], decorator: str, form: str, what: str) -> NoReturn:
    """Reject a union/int enum with no case: `@contracterror`'s empty-enum
    rule, worded once for both kinds so the loader bridges one needle."""
    raise ValueError(
        f"{cls.__name__}: a {decorator} declares at least one case (`{form}`); an empty "
        f"{what} contributes nothing to the contract spec"
    )


@dataclass_transform()
def contracttype(cls: type[_T]) -> type[_T]:
    """Declare a named-field struct (compiled to `Map<Symbol, V>`).

    Applies `dataclasses.dataclass(frozen=True, eq=True)`, so instances are
    immutable and compare by value, and validates that every field name is a
    valid Symbol of at most 30 characters and every annotation is a chain
    type, another serpent-decorated class, or `X | None`.

    See the module docstring for why mypy will not flag field mutation on the
    3.11 floor even though the runtime is frozen.
    """
    return _build_record(cls, "struct")


@overload
def contractevent(cls: type[_T], /) -> type[_T]: ...


@overload
def contractevent(
    *, topics: Sequence[str] | None = ..., data_format: str = ...
) -> Callable[[type[_T]], type[_T]]: ...


@dataclass_transform()
def contractevent(
    cls: type[Any] | None = None,
    /,
    *,
    topics: Sequence[str] | None = None,
    data_format: str = "map",
) -> Any:
    """Declare a contract event, bare or with its topic convention spelled out.

    Same field rules and frozen-dataclass treatment as `@contracttype`. The
    class **must** inherit `serpent.env.Event`, which is where `publish` comes
    from: a decorator cannot add a member a type checker can see, so
    `Transfer(...).publish(env)` only type-checks if `publish` is inherited
    from a real base class.

    **The topic convention** (dossier C.2). An event publishes a topic LIST and
    one data payload; which is which is declared here, so the compiler never
    has to guess:

    * `prefix_topics` are the leading, constant topics -- by default the single
      snake_cased class name (`Transfer` -> `transfer`, `MyHTTPEvent` ->
      `my_http_event`), overridden with `topics=("token", "transfer")`. The XDR
      caps the list at two, and each one is a Symbol of at most 32 characters
      (a topic longer than 9 is perfectly legal; it simply pools through linear
      memory at the publish site instead of being a small-value Symbol).
    * a field marked `Annotated[T, topic]` is published as a topic, after the
      prefix topics and in declaration order; every unmarked field is data.
    * `data_format` picks the `SCSpecEventDataFormat` case: `"map"` (the
      default, a `Map<Symbol, Val>` keyed by field name), `"vec"`, or
      `"single-value"` -- which publishes the ONE data value bare and therefore
      requires exactly one non-topic field. `"map"` and `"vec"` publish a
      container OF the data fields, so each needs at least one; and in M1
      `"vec"`'s data fields must all have the SAME type (see
      `_check_data_format` -- the compiler's vector node carries one element
      type, because tier 1's `Vec` is statically typed in its element class).
      A mixed payload is what the default `"map"` format is for.

    Both spellings are one decorator: `@contractevent` applies directly, and
    `@contractevent(...)` returns the decorator. Every validation happens at
    decoration time, naming the class, so a malformed event is a `ValueError`
    at the declaration and never a lying spec entry (R5).
    """

    def decorate(target: type[_T]) -> type[_T]:
        if Event not in target.__mro__:
            raise ValueError(
                f"{target.__name__}: @contractevent classes must inherit `Event` "
                f"(`class {target.__name__}(Event):`). `publish` is inherited from it "
                "-- a decorator cannot add a method that mypy can see."
            )
        return _build_record(target, "event", topics=topics, data_format=data_format)

    if cls is None:
        return decorate
    return decorate(cls)


def _build_record(
    cls: type[_T],
    kind: str,
    *,
    topics: Sequence[str] | None = None,
    data_format: str = "map",
) -> type[_T]:
    """The shared `@contracttype`/`@contractevent` body.

    **Annotations are read with `include_extras=True`** -- the one seam the
    whole event convention hangs off. Without that flag `get_type_hints`
    silently STRIPS `Annotated`, the `topic` marker vanishes before anything
    can see it, and every event would compile as all-data. With it, the marker
    is read here and the annotation stored in the metadata is the STRIPPED one,
    so `spec.typemap.to_spec_type`, the compiler's `resolve_annotation` and
    every other downstream consumer keep seeing plain chain types and need no
    knowledge of `Annotated` at all.

    `topics`/`data_format` are the event-only arguments and are ignored for a
    struct, which cannot carry a topic (a marked struct field is refused
    outright rather than silently ignored).
    """
    _reject_redecoration(cls)
    fields: list[tuple[str, object]] = []
    locations: dict[str, str] = {}
    for name, annotation in _annotations_of(cls, include_extras=True).items():
        _check_name(cls, name, "field")
        stripped, is_topic = _split_topic(cls, name, annotation)
        if is_topic and kind != "event":
            raise ValueError(
                f"{cls.__name__}.{name}: `topic` marks a field of a @contractevent "
                "class as a published topic; a @contracttype struct has no topics, "
                "so the marker would be silently ignored here"
            )
        if not _is_contract_annotation(stripped):
            raise ValueError(
                f"{cls.__name__}.{name}: annotation {_render(stripped)} is not a "
                "chain type, a `@contracttype` struct, or `X | None` of one"
            )
        fields.append((name, stripped))
        locations[name] = TOPIC_LOCATION if is_topic else DATA_LOCATION

    metadata: dict[str, Any] = {"kind": kind, "fields": fields}
    if kind == "event":
        # `fields` stays a (name, annotation) PAIR list, shared with a struct's:
        # the per-field location is a parallel `locations` map instead of a
        # third tuple element, because `compiler/loader.py`'s F.1.14 field
        # cross-check and `compiler/decls.py`'s field resolution both unpack
        # `metadata["fields"]` as pairs for structs AND events alike.
        metadata["locations"] = locations
        metadata["prefix_topics"] = _prefix_topics(cls, topics)
        metadata["data_format"] = _check_data_format(cls, data_format, fields, locations)
        _check_topic_list(cls, metadata["prefix_topics"], fields, locations)

    decorated = dataclasses.dataclass(frozen=True, eq=True)(cls)
    setattr(decorated, _METADATA_ATTR, metadata)
    return decorated


def _split_topic(cls: type[Any], name: str, annotation: object) -> tuple[object, bool]:
    """One field's `(stripped annotation, is a topic)`.

    Only a marker on the WHOLE annotation counts. `Annotated[U32, topic] | None`
    hides it inside a union, where stripping the outer layer would not find it
    and the field would quietly become data -- so that spelling is refused
    rather than misread.
    """
    if typing.get_origin(annotation) is typing.Annotated:
        args = typing.get_args(annotation)
        return args[0], any(isinstance(extra, _Topic) for extra in args[1:])
    if _mentions_topic(annotation):
        raise ValueError(
            f"{cls.__name__}.{name}: `topic` must mark the whole field annotation "
            f"(`Annotated[T, topic]`), not a type nested inside it -- got "
            f"{_render(annotation)}"
        )
    return annotation, False


def _mentions_topic(annotation: object) -> bool:
    """Whether the marker appears anywhere inside a composite annotation."""
    args = typing.get_args(annotation)
    if typing.get_origin(annotation) is typing.Annotated:
        return any(isinstance(extra, _Topic) for extra in args[1:])
    return any(_mentions_topic(arg) for arg in args)


def _prefix_topics(cls: type[Any], topics: Sequence[str] | None) -> tuple[str, ...]:
    """The validated `prefix_topics` tuple: the author's, or the default one.

    `topics=()` is DELIBERATELY legal (review M3). An event with no prefix topic
    at all is an accurate spec -- its topic list is exactly whatever its
    `Annotated[T, topic]` fields are -- not a lie, so nothing here refuses the
    empty case, and Task 6's desugar publishes an empty prefix knowingly.
    """
    if isinstance(topics, str):
        _reject_string_topics(cls, topics)
    derived = topics is None
    # Spelled out rather than a conditional expression so the `None` narrowing
    # is one mypy can see.
    declared: tuple[str, ...]
    if topics is None:
        declared = (_snake_case(cls.__name__),)
    else:
        declared = tuple(topics)
    if len(declared) > PREFIX_TOPIC_LIMIT:
        raise ValueError(
            f"{cls.__name__}: an event declares at most {PREFIX_TOPIC_LIMIT} prefix "
            f"topics (got {len(declared)}) -- `SCSpecEventV0.prefix_topics` is an "
            "XDR `SCSymbol<2>`; publish the rest as `Annotated[T, topic]` fields"
        )
    for declared_topic in declared:
        # `is_valid_symbol`, NOT `fits_symbol_small`: the cap is the Symbol's 32
        # characters, not the 9 that fit in a small value. A longer topic pools
        # through linear memory at the publish site, which is a cost, not an
        # error.
        if not isinstance(declared_topic, str) or not val.is_valid_symbol(declared_topic):
            raise ValueError(_bad_prefix_topic(cls, declared_topic, derived=derived))
    return declared


def _reject_string_topics(cls: type[Any], topics: str) -> NoReturn:
    """Refuse `topics="transfer"`: a sequence of characters, not of topics.

    A `str` IS a `Sequence[str]`, so mypy accepts it with no complaint and
    `tuple(...)` would explode it into one prefix topic PER CHARACTER -- which
    an author would then read as "at most 2 prefix topics (got 8)" for what is
    really a missing comma.

    A `ValueError` like every other decoration-site failure here, and in its own
    function so the raise is not lexically inside an `isinstance` guard (the
    same reason `_reject_bare_member` and `_reject_bound_method` exist).
    """
    raise ValueError(
        f"{cls.__name__}: topics= takes a sequence of topics, not one string -- "
        f"write `topics=({topics!r},)`. A bare string is a sequence of its "
        "characters, which would declare one prefix topic per letter"
    )


def _bad_prefix_topic(cls: type[Any], declared_topic: object, *, derived: bool) -> str:
    """Why one prefix topic is unusable -- worded for who actually wrote it.

    A DEFAULT topic is derived from the class name, so blaming "prefix topic
    'this_events_class_name_is_thirty_three'" sends the author looking for a
    `topics=` argument they never passed (review M2). Name the derivation and
    the remedy instead.
    """
    cap = f"a valid Symbol of 1 to {val.SCSYMBOL_LIMIT} characters (a-z, A-Z, 0-9, _)"
    if derived:
        return (
            f"{cls.__name__}: the default prefix topic {declared_topic!r}, derived from "
            f"the class name, is not {cap} -- declare a shorter one explicitly with "
            "`@contractevent(topics=(...))`"
        )
    return f"{cls.__name__}: prefix topic {declared_topic!r} must be {cap}"


def _check_data_format(
    cls: type[Any],
    data_format: str,
    fields: Sequence[tuple[str, object]],
    locations: Mapping[str, str],
) -> str:
    """Validate `data_format` against the event's data fields.

    Three rules, one per case, all of them about ARITY or TYPE UNIFORMITY of
    the non-topic fields:

    * `"single-value"` publishes ONE data value, so exactly one data field;
    * `"map"` and `"vec"` publish a container OF the data fields, so at least
      one -- `map_new_from_linear_memory` over an empty key array has nothing
      to describe, and a `Vec` with no element has no element type;
    * `"vec"` additionally needs every data field to share ONE type, an M1
      restriction (Task 6's controller ruling (a)): the IR's `MakeVec` carries a
      single `elem_ty` because tier 1's `Vec` is statically typed in its element
      class, and the heterogeneous vector node (`MakeTopics`) is topics-only by
      contract. Refused here, at the declaration, rather than compiling to a
      vector whose element type is a guess.

    Shared with `spec.sections._event_entry`, which re-runs it against the
    metadata it is handed rather than keeping a second copy of the rule: a
    `SINGLE_VALUE` entry over two data params is valid XDR and a lie, so the
    arity has to be checked wherever the entry is actually built.
    """
    if data_format not in DATA_FORMATS:
        raise ValueError(
            f"{cls.__name__}: data_format must be one of "
            f"{', '.join(repr(f) for f in DATA_FORMATS)} (got {data_format!r})"
        )
    data_fields = [
        (name, annotation) for name, annotation in fields if locations.get(name) == DATA_LOCATION
    ]
    names = [name for name, _annotation in data_fields]
    if data_format == "single-value" and len(data_fields) != 1:
        raise ValueError(
            f"{cls.__name__}: data_format 'single-value' publishes exactly one "
            f"data value, so the event needs exactly one non-topic field (got "
            f"{len(data_fields)}: {', '.join(names) or 'none'})"
        )
    if data_format in ("map", "vec") and not data_fields:
        raise ValueError(
            f"{cls.__name__}: data_format {data_format!r} publishes the non-topic "
            "fields as a container, so the event needs at least one of them "
            "(every field here is marked `topic`)"
        )
    if data_format == "vec":
        distinct = {_render(annotation) for _name, annotation in data_fields}
        if len(distinct) > 1:
            raise ValueError(
                f"{cls.__name__}: data_format 'vec' publishes the data fields as one "
                f"Vec, so in M1 they must all have the same type (got "
                f"{', '.join(f'{name}: {_render(annotation)}' for name, annotation in data_fields)})"
                " -- use the default 'map' format for a mixed payload"
            )
    return data_format


def _check_topic_list(
    cls: type[Any],
    prefix_topics: tuple[str, ...],
    fields: Sequence[tuple[str, object]],
    locations: Mapping[str, str],
) -> None:
    """Refuse a published topic list that breaks the `topics[0]` convention.

    Two rules, and both only bite when `topics=()` -- which is deliberately
    legal (review M3) BECAUSE the marked fields carry the topic list:

    * there must be at least one topic. With neither a prefix topic nor an
      `Annotated[T, topic]` field there is none at all, which the tier-1 model
      refuses on the way in ("an event needs at least one topic, naming it")
      and which no indexer can filter on.
    * `topics[0]` must be a `Symbol` (S11/S10's convention: the first topic
      NAMES the event, and indexers and RPC filtering assume it). With no
      prefix topic, `topics[0]` is the first marked FIELD, so its declared type
      is what decides -- an `Annotated[Address, topic]` first field would put an
      Address there. The canonical spelling refuses exactly that at compile time
      (`SPT3019`), so refusing it here is what keeps the two spellings, and the
      spec entry, telling one story.

    Both are refused at the declaration site, where the fix is, rather than at
    every publish site.
    """
    if prefix_topics:
        return
    topic_fields = [
        (name, annotation) for name, annotation in fields if locations.get(name) == TOPIC_LOCATION
    ]
    if not topic_fields:
        raise ValueError(
            f"{cls.__name__}: an event publishes at least one topic, and this one has "
            "no prefix topic and no `Annotated[T, topic]` field -- drop `topics=()` to "
            "get the class-name topic back, or mark a field as a topic"
        )
    first_name, first_annotation = topic_fields[0]
    if first_annotation is not Symbol:
        raise ValueError(
            f"{cls.__name__}.{first_name}: with `topics=()` this field is the event's "
            f"topics[0], which names the event and must therefore be a Symbol (got "
            f"{_render(first_annotation)}) -- declare a prefix topic instead, e.g. "
            f"`@contractevent(topics=({_snake_case(cls.__name__)!r},))`"
        )


def _snake_case(name: str) -> str:
    """A class name as its default prefix topic: `MyHTTPEvent` -> `my_http_event`.

    An underscore goes before every uppercase letter that FOLLOWS a lowercase
    letter or digit (the ordinary CamelCase boundary) and before the last
    uppercase letter of an acronym run, which is the one that begins the next
    word (`MyHTTPEvent` -> `my_http_event`, `HTTPEvent` -> `http_event`).
    """
    out: list[str] = []
    for index, char in enumerate(name):
        if index and char.isupper():
            previous = name[index - 1]
            following = name[index + 1] if index + 1 < len(name) else ""
            if previous.islower() or previous.isdigit() or following.islower():
                out.append("_")
        out.append(char.lower())
    return "".join(out)


def _reject_redecoration(cls: type[Any]) -> None:
    """Refuse a class that already carries serpent metadata of its own.

    Stacking `@contracttype` on an already-decorated class otherwise fails
    deep inside `dataclasses` with `TypeError: cannot inherit frozen dataclass
    from a non-frozen one` (or silently re-runs the transform). `vars(cls)`,
    not `getattr`, so that a *subclass* of a decorated struct is not mistaken
    for a re-decoration of it.
    """
    if _METADATA_ATTR in vars(cls):
        existing: dict[str, Any] = vars(cls)[_METADATA_ATTR]
        raise ValueError(
            f"{cls.__name__}: already declared as a serpent "
            f"{existing.get('kind', 'type')}; apply exactly one serpent "
            "decorator per class"
        )


def contract(cls: type[_T]) -> type[_T]:
    """Declare the contract itself: the class whose methods become exports.

    Checked at class-creation time, with the offending method or parameter
    named in the message:

    * every public method's first parameter is literally `self` (the compiler
      ignores it; it exists so the class is ordinary, strict-clean Python);
    * every *other* parameter and the return type is annotated -- exported
      signatures flow into `contractspecv0`, so they cannot be inferred;
    * public method names are valid Symbols of at most 30 characters;
    * `__init__`, which compiles to the host-reserved `__constructor` export,
      is annotated `-> None`.

    Single-underscore-private methods are not exported and are not checked
    (the host reserves only `__`-prefixed names, and only at call time).
    """
    methods: list[tuple[str, list[tuple[str, object]], object]] = []
    for name, member in list(vars(cls).items()):
        if name.startswith("_") and name != "__init__":
            continue
        if isinstance(member, staticmethod | classmethod):
            _reject_bound_method(cls, name, member)
        if not inspect.isfunction(member):
            continue
        if name != "__init__":
            _check_name(cls, name, "method")
        methods.append(_check_method(cls, name, member))

    setattr(cls, _METADATA_ATTR, {"kind": "contract", "methods": methods})
    return cls


def _reject_bound_method(cls: type[Any], name: str, member: object) -> NoReturn:
    """Reject a `staticmethod`/`classmethod` where an export is expected.

    A `ValueError` for consistency with every other decoration-site failure,
    and in its own function so the raise is not lexically inside an
    `isinstance` guard.
    """
    kind = type(member).__name__
    raise ValueError(
        f"{cls.__name__}.{name}: contract methods are plain methods taking `self` "
        f"first; {kind} is not exportable (the host invokes an export with the "
        "contract instance, and the compiler ignores `self`)"
    )


def _check_method(
    cls: type[Any], name: str, func: Any
) -> tuple[str, list[tuple[str, object]], object]:
    """Validate one method's signature; return its metadata entry.

    Presence of an annotation is read off `inspect.signature` (which reports
    what was *written*), while the annotation's value is read off
    `typing.get_type_hints` (which resolves it to a real type object). That
    split is what makes the recorded metadata identical whether or not the
    contract module uses `from __future__ import annotations` -- under PEP 563
    every annotation would otherwise be recorded as a string.

    Read with `include_extras=True` (the SECOND seam this file grants the
    marker, `_build_record`/:518 being the first, M1-E2 Task 5/E10) so a
    parameter or return annotation carrying `Annotated[T, topic]` is visible
    here instead of `get_type_hints` silently stripping it -- a contract
    method has no topics, so the marker would otherwise compile in and do
    nothing. `_split_topic` (the same helper `_build_record` uses) is run over
    every parameter annotation and the return annotation; a marker found on
    either is refused rather than silently ignored, and what is STORED in
    `params`/`returns` -- and therefore in `_serpent_type_["methods"]`, and
    downstream in `to_spec_type`/`resolve_annotation` -- is always the
    STRIPPED annotation, never an `Annotated` (risk F.1.12).
    """
    signature = inspect.signature(func)
    parameters = list(signature.parameters.values())
    hints = _annotations_of(func, include_extras=True)

    if not parameters or parameters[0].name != "self":
        first = parameters[0].name if parameters else "<none>"
        raise ValueError(
            f"{cls.__name__}.{name}: contract methods take `self` as their first "
            f"parameter (got {first!r}). The compiler ignores `self`; it is what "
            "makes the method strict-clean Python."
        )

    params: list[tuple[str, object]] = []
    for parameter in parameters[1:]:
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            raise ValueError(
                f"{cls.__name__}.{name}: `*{parameter.name}` is not allowed -- a "
                "contract export has a fixed arity in contractspecv0, so a "
                "variadic parameter has nothing to compile to"
            )
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            raise ValueError(
                f"{cls.__name__}.{name}: `**{parameter.name}` is not allowed -- the "
                "host invokes exports positionally, so keyword collection has "
                "nothing to compile to"
            )
        if parameter.default is not inspect.Parameter.empty:
            raise ValueError(
                f"{cls.__name__}.{name}: parameter {parameter.name!r} has a default "
                "value, which contractspecv0 cannot express -- every argument of an "
                "export is required, so callers must pass it explicitly"
            )
        if parameter.annotation is inspect.Parameter.empty:
            raise ValueError(
                f"{cls.__name__}.{name}: parameter {parameter.name!r} needs a type "
                "annotation -- exported signatures are compiled into contractspecv0"
            )
        stripped, is_topic = _split_topic(cls, name, hints[parameter.name])
        if is_topic:
            raise ValueError(
                f"{cls.__name__}.{name}: `topic` marks a field of a @contractevent "
                f"class as a published topic; parameter {parameter.name!r} of a "
                "contract method has no topics, so the marker would be silently "
                "ignored here"
            )
        params.append((parameter.name, stripped))

    if signature.return_annotation is inspect.Signature.empty:
        raise ValueError(
            f"{cls.__name__}.{name}: the return type needs an annotation "
            "(use `-> None` for a method that returns nothing)"
        )
    # `get_type_hints` normalizes a `-> None` annotation to `NoneType`, so this
    # holds for a PEP 563 module too, where the raw annotation is the str
    # `"None"`.
    returns, return_is_topic = _split_topic(cls, name, hints["return"])
    if return_is_topic:
        raise ValueError(
            f"{cls.__name__}.{name}: `topic` marks a field of a @contractevent "
            "class as a published topic; the return type of a contract method has "
            "no topics, so the marker would be silently ignored here"
        )
    if name == "__init__" and returns is not types.NoneType:
        raise ValueError(
            f"{cls.__name__}.__init__ must be annotated `-> None` (got "
            f"{_render(returns)}); it compiles to the `__constructor` export, "
            "which cannot return a value"
        )
    return (name, params, returns)


def _check_name(cls: type[Any], name: str, what: str) -> None:
    """Enforce the spec's 30-character name cap and the Symbol charset.

    Length is checked first on purpose: `val.is_valid_symbol` caps at
    `SCSYMBOL_LIMIT` (32), so an over-long name would otherwise be reported as
    a charset problem when the real problem is its length.
    """
    if len(name) > NAME_LIMIT:
        raise ValueError(
            f"{cls.__name__}.{name}: {what} names are capped at {NAME_LIMIT} "
            f"characters by the contract spec (got {len(name)})"
        )
    if not val.is_valid_symbol(name):
        raise ValueError(
            f"{cls.__name__}.{name}: {what} names must be valid Symbols (a-z, A-Z, 0-9, _)"
        )


def _annotations_of(owner: Any, *, include_extras: bool = False) -> dict[str, Any]:
    """Annotations of a class or function, resolved to real type objects.

    Always goes through `typing.get_type_hints`, so a contract module that
    uses `from __future__ import annotations` (PEP 563, where every annotation
    is a string at runtime) yields exactly the same result as one that does
    not. An unresolvable name is reported against the owner instead of leaking
    a bare `NameError`.

    `include_extras` keeps `Annotated[...]` wrappers intact instead of letting
    `get_type_hints` strip them. `_build_record` (:518) was the marker's one
    seam until M1-E2 Task 5/E10 named `_check_method` (:843) a SECOND: a
    method parameter or return annotation can carry `Annotated[T, topic]` too,
    and the marker has to be visible here to be refused rather than silently
    dropped. It defaults to False because most callers (module-level constants,
    and every OTHER `typing.get_type_hints` use) have no marker convention to
    see; both seams that DO pass `include_extras=True` still hand back only the
    STRIPPED annotation to their caller -- `_serpent_type_["fields"]` and
    `_serpent_type_["methods"]` never store an `Annotated`, so `to_spec_type`,
    `resolve_annotation` and every other downstream reader keep seeing plain
    chain types (risk F.1.12).
    """
    try:
        return typing.get_type_hints(owner, include_extras=include_extras)
    except NameError as exc:
        raise ValueError(
            f"{owner.__qualname__}: cannot resolve annotations ({exc}). "
            "Annotated types must be resolvable at the module level."
        ) from exc


#: The declared kinds that ARE values, and can therefore sit in a struct
#: field, a container or a variant payload. An error enum, a contract class and
#: an event type are not values, so none of them is here -- and the M1-E2 kinds
#: are, because a union and an int enum are ordinary contract shapes (SS B.3).
#: One set, read by `_is_contract_annotation` alone, so the rule is stated once.
_VALUE_KINDS: Final = frozenset({"struct", "union", "enum"})


def _is_contract_annotation(annotation: object) -> bool:
    """A chain type, a declared struct/union/int enum, or `X | None` of one.

    Values only: an error enum, a contract class or an event type is not a
    value that can sit in a field or a payload, so each is rejected here rather
    than producing nonsense in the contract spec. `vars(...)`, not `getattr`,
    closes the inheritance leak -- an undecorated subclass of a struct
    inherits `_serpent_type_` but is not itself declared.

    One function for every position an annotation can appear in (a struct or
    event field, and M1-E2's variant payload), which is why admitting the two
    new kinds is a one-place edit: a UDT reference is name-only (SS B.3), so it
    costs nothing anywhere it is now allowed.
    """
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(annotation) if a is not types.NoneType]
        return len(args) == 1 and _is_contract_annotation(args[0])
    if origin is not None:
        # A parameterized container: Vec[U32], Map[Symbol, U32].
        annotation = origin
    if not isinstance(annotation, type):
        return False
    if issubclass(annotation, _ChainValue | Vec | Map):
        return True
    metadata = vars(annotation).get(_METADATA_ATTR)
    return isinstance(metadata, dict) and metadata.get("kind") in _VALUE_KINDS


def _render(annotation: object) -> str:
    """A readable name for an annotation in an error message."""
    if isinstance(annotation, type):
        return annotation.__name__
    return repr(annotation)
