"""The four contract decorators and the `errorcode` field specifier.

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
  so kwargs construction type-checks against the field annotations.
* `@contract` methods take `self` first, which is what makes them ordinary,
  strict-clean Python methods (the compiler ignores `self`).

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
from typing import Any, TypeVar, cast, dataclass_transform

from serpent import val
from serpent.env import Env
from serpent.errors import RESERVED_CODE_MIN, ContractError
from serpent.types import Map, Vec
from serpent.types._base import _ChainValue

__all__ = [
    "contract",
    "contracterror",
    "contractevent",
    "contracttype",
    "errorcode",
]

_T = TypeVar("_T")

#: Spec XDR caps function and field names at 30 characters. This is *not*
#: `val.SCSYMBOL_LIMIT` (32, the on-chain Symbol cap) -- a 31-character name is
#: a representable Symbol that the contract spec cannot carry.
NAME_LIMIT = 30

#: The metadata every serpent-decorated class carries. Sub-plan C reads it.
_METADATA_ATTR = "_serpent_type_"


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
            # ValueError, not TypeError (TRY004): this is a malformed
            # *declaration*, and every decoration-site failure in this module
            # is a ValueError so authors can catch one class of error.
            raise ValueError(  # noqa: TRY004
                f"{cls.__name__}.{name}: @contracterror members must be declared "
                f"as `{name} = errorcode(N)`, not `{name} = {value!r}`. "
                "A bare value is inferred as its Python type by static checkers, "
                f"so `raise {cls.__name__}.{name}` would fail mypy --strict."
            )
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

    for name, code in cases:
        setattr(cls, name, _make_error_class(cls, name, code))

    setattr(cls, _METADATA_ATTR, {"kind": "error_enum", "cases": cases})
    return cls


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


@dataclass_transform()
def contractevent(cls: type[_T]) -> type[_T]:
    """Declare a contract event.

    Same field rules and frozen-dataclass treatment as `@contracttype`, plus
    a `publish(env)` method. Emission needs the host bridge, so `publish`
    raises `NotImplementedError("sub-plan E")` for now.

    Static-visibility caveat: because a decorator cannot add members a type
    checker can see, `publish` is installed at runtime only. Sub-plan E gives
    it a statically visible home.
    """
    decorated = _build_record(cls, "event")
    # One shared function object, installed unbound on every event class: it
    # must not be renamed per class, or the last decorated event would rewrite
    # every earlier one's `__qualname__`.
    #
    # setattr, not attribute assignment (B010): `decorated` is `type[_T]`, so a
    # direct assignment is an attr-defined error under mypy --strict.
    setattr(decorated, "publish", _event_publish)  # noqa: B010
    return decorated


def _event_publish(self: object, env: Env) -> None:
    """Emit this event via the host's `contract_event`."""
    raise NotImplementedError("sub-plan E")


def _build_record(cls: type[_T], kind: str) -> type[_T]:
    """The shared `@contracttype`/`@contractevent` body."""
    fields: list[tuple[str, object]] = []
    for name, annotation in _annotations_of(cls).items():
        _check_name(cls, name, "field")
        if not _is_contract_annotation(annotation):
            raise ValueError(
                f"{cls.__name__}.{name}: annotation {_render(annotation)} is not a "
                "chain type, a serpent-decorated class, or `X | None`"
            )
        fields.append((name, annotation))

    decorated = dataclasses.dataclass(frozen=True, eq=True)(cls)
    setattr(decorated, _METADATA_ATTR, {"kind": kind, "fields": fields})
    return decorated


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
            # ValueError for consistency with every other decoration-site
            # failure here (TRY004 would prefer TypeError).
            raise ValueError(  # noqa: TRY004
                f"{cls.__name__}.{name}: contract methods are plain methods taking "
                "`self` first; staticmethod/classmethod are not exportable"
            )
        if not inspect.isfunction(member):
            continue
        if name != "__init__":
            _check_name(cls, name, "method")
        methods.append(_check_method(cls, name, member))

    setattr(cls, _METADATA_ATTR, {"kind": "contract", "methods": methods})
    return cls


def _check_method(
    cls: type[Any], name: str, func: Any
) -> tuple[str, list[tuple[str, object]], object]:
    """Validate one method's signature; return its metadata entry."""
    signature = inspect.signature(func)
    parameters = list(signature.parameters.values())

    if not parameters or parameters[0].name != "self":
        first = parameters[0].name if parameters else "<none>"
        raise ValueError(
            f"{cls.__name__}.{name}: contract methods take `self` as their first "
            f"parameter (got {first!r}). The compiler ignores `self`; it is what "
            "makes the method strict-clean Python."
        )

    params: list[tuple[str, object]] = []
    for parameter in parameters[1:]:
        if parameter.annotation is inspect.Parameter.empty:
            raise ValueError(
                f"{cls.__name__}.{name}: parameter {parameter.name!r} needs a type "
                "annotation -- exported signatures are compiled into contractspecv0"
            )
        params.append((parameter.name, parameter.annotation))

    returns = signature.return_annotation
    if returns is inspect.Signature.empty:
        raise ValueError(
            f"{cls.__name__}.{name}: the return type needs an annotation "
            "(use `-> None` for a method that returns nothing)"
        )
    # `"None"` covers a contract module that uses `from __future__ import
    # annotations`, where every annotation reaches us as a string.
    if name == "__init__" and returns not in (None, types.NoneType, "None"):
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
            f"{cls.__name__}.{name}: {what} names must be valid Symbols "
            "(a-z, A-Z, 0-9, _)"
        )


def _annotations_of(cls: type[Any]) -> dict[str, Any]:
    """Resolved annotations, with an unresolvable name reported at the class."""
    try:
        return typing.get_type_hints(cls)
    except NameError as exc:
        raise ValueError(
            f"{cls.__name__}: cannot resolve field annotations ({exc}). "
            "Field types must be importable at the module level."
        ) from exc


def _is_contract_annotation(annotation: object) -> bool:
    """A chain type, a serpent-decorated class, or `X | None` of one."""
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
    return hasattr(annotation, _METADATA_ATTR)


def _render(annotation: object) -> str:
    """A readable name for an annotation in an error message."""
    if isinstance(annotation, type):
        return annotation.__name__
    return repr(annotation)
