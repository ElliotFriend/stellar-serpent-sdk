"""Chain type / metadata annotation -> `SCSpecTypeDef`.

This is the one place that turns an *authoring-surface* annotation (what
`serpent.decorators` recorded in `_serpent_type_`, or what a contract method
was annotated with) into the XDR type the `contractspecv0` section carries.
Every value comes out of `stellar_sdk.xdr`'s generated classes, so the encoding
is the protocol's rather than ours.

**`SCSpecType`, never a literal.** The numeric table in spec Sec.13 is the list
of Val *tags*, which coincide with `SCSpecType` on 4-13 and diverge at 1 and 14
(`SCSpecType.BOOL` is 1 while tag 1 is `Void`; `SCSpecType.BYTES` is 14 while
tag 14 is `SymbolSmall`). Reading one table for the other is a real trap, so
nothing here spells a number: the mapping is written with
`xdr.SCSpecType.SC_SPEC_TYPE_*` symbols only, and the tests compare against
independently constructed `stellar_sdk` objects.

**The `BytesN` rule keys off `_LENGTH`, never off a class whitelist.** `Bytes`
(`_LENGTH is None`) is `BYTES`; *any* subclass with `_LENGTH == n` is
`BYTES_N(n)` -- `Bytes32`, `Bytes64` and every `bytes_n(k)` class alike. A
whitelist of the two named classes would silently mis-emit plain `BYTES` for
`bytes_n(20)`, which is a valid spec that lies about the length.

**What has no spec type** (all `SpecTypeError`, naming the annotation):

* `None` / `NoneType` -- a void return is an EMPTY `outputs` list in
  `spec.sections`, not a `VOID` type def. There is deliberately no `VOID`
  mapping here for a caller to reach for by mistake.
* `Env` -- the host handle. Every contract method takes `env: Env` second and
  the spec has no input for it, so `spec.sections` DROPS that parameter.
* `Event` and `@contractevent` classes -- an event is a spec *entry*
  (`EVENT_V0`, built by `spec.sections` from `events=`), not a type an author
  can annotate with.
* `@contracterror` enums -- a spec *entry* (`UDT_ERROR_ENUM_V0`), not a type.
  (`@contractunion` and `@contractenum` are the opposite case: M1-E2 makes
  both of THOSE mappable, right alongside `@contracttype` -- a tagged union
  or an int enum is a value an author can hold in a field or pass as a
  parameter, so it needs a spec *type*, not only the entry `spec.sections`
  builds for it.)
* `@contract` classes -- the contract is not a value.
* `U256`/`I256` (M2-deferred), `MuxedAddress`, `Val`, `Result`, `Tuple`: no
  authoring surface exists for these at all, so they arrive only as some other
  unmappable annotation (a plain Python type, say) and are refused as one.
* plain `int`/`str`/`bytes`/`bool` and any other non-chain annotation -- the
  chain type is what the compiler needs to see.

**Name caps: the split.** The 30-character cap on function/field names and the
60-character cap on the *declaration* of a UDT are checked in `spec.sections`,
which knows the declaration site and can raise a source-located error;
`to_spec_type` otherwise only emits what it is given. The one exception is
forced by the XDR itself: `SCSpecTypeUDT.name` is a `string<60>` and
`stellar_sdk` enforces that in the CONSTRUCTOR, so an over-long struct name
cannot be carried out of this module to be reported later. Rather than let a
bare `stellar_sdk` `ValueError` escape with no idea which annotation caused it,
`_udt_name` refuses it as a `SpecTypeError` naming the class and its length.
Sections still validates first, so in a normal build that error is the
source-located one.
"""

import types
import typing
from typing import Final

from stellar_sdk import xdr

from serpent.decorators import _METADATA_ATTR
from serpent.env import Env, Event
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

__all__ = ["SpecTypeError", "to_spec_type"]


class SpecTypeError(ValueError):
    """An annotation that the contract spec cannot express.

    A `ValueError`, like every other authoring-time failure in serpent, so a
    caller can catch one class of error from the whole build.
    """


#: The scalar rows of the mapping table, keyed by the EXACT class. Membership,
#: not `issubclass`: a hypothetical author subclass of `U32` is a refinement
#: the spec cannot carry, and mapping it to plain `U32` would emit a spec that
#: silently drops whatever the subclass meant. The one family that IS matched
#: structurally is `Bytes`, whose fixed-length subclasses have an exact spec
#: representation (`BYTES_N`) -- see `_bytes_family`.
_SCALARS: Final[dict[type, xdr.SCSpecType]] = {
    Bool: xdr.SCSpecType.SC_SPEC_TYPE_BOOL,
    U32: xdr.SCSpecType.SC_SPEC_TYPE_U32,
    I32: xdr.SCSpecType.SC_SPEC_TYPE_I32,
    U64: xdr.SCSpecType.SC_SPEC_TYPE_U64,
    I64: xdr.SCSpecType.SC_SPEC_TYPE_I64,
    Timepoint: xdr.SCSpecType.SC_SPEC_TYPE_TIMEPOINT,
    Duration: xdr.SCSpecType.SC_SPEC_TYPE_DURATION,
    U128: xdr.SCSpecType.SC_SPEC_TYPE_U128,
    I128: xdr.SCSpecType.SC_SPEC_TYPE_I128,
    String: xdr.SCSpecType.SC_SPEC_TYPE_STRING,
    Symbol: xdr.SCSpecType.SC_SPEC_TYPE_SYMBOL,
    Address: xdr.SCSpecType.SC_SPEC_TYPE_ADDRESS,
}

#: `SCSpecTypeUDT.name` is a `string<60>` in the spec XDR (and `stellar_sdk`
#: enforces it at construction) -- the same cap `spec.sections` applies to a
#: type declaration, checked here only so the failure names the annotation.
_UDT_NAME_LIMIT: Final = 60

#: Per-`_serpent_type_`-kind explanation for a decorated class that is not a
#: UDT. `struct`, `union` and `enum` are absent on purpose: they are the three
#: mappable kinds (§B.3 -- a UDT reference is name-only, so all three share
#: one spec namespace).
_REFUSED_KINDS: Final[dict[str, str]] = {
    "error_enum": (
        "an @contracterror enum is a spec ENTRY (UDT_ERROR_ENUM_V0), not a "
        "type -- pass it to build_spec_entries, do not annotate with it"
    ),
    "event": (
        "an @contractevent class is a spec ENTRY (SCSpecEventV0), not a type -- "
        "pass it to build_spec_entries(events=...), do not annotate with it"
    ),
    "contract": ("a @contract class is the contract itself, not a value it can pass or return"),
}


def to_spec_type(annotation: object) -> xdr.SCSpecTypeDef:
    """The `SCSpecTypeDef` for one authoring-surface annotation.

    Handles the scalars, the `Bytes`/`BytesN` family, `Vec[T]`, `Map[K, V]`,
    `X | None` (both the `types.UnionType` and `typing.Optional` spellings),
    and `@contracttype`/`@contractunion`/`@contractenum` classes (as `UDT` by
    class name, all three). Anything else raises `SpecTypeError` naming the
    annotation -- see the module docstring for the full list and why each is
    refused.
    """
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        return _option(annotation)
    if origin is Vec:
        return _vec(annotation)
    if origin is Map:
        return _map(annotation)
    if origin is not None:
        # Some other parameterized generic: list[int], tuple[U32, U32], ...
        raise _unmappable(annotation)
    if annotation is None or annotation is types.NoneType:
        raise _unmappable(
            annotation,
            "a void return has no type def -- it is an EMPTY outputs list in "
            "the spec entry (spec.sections builds it)",
        )
    if not isinstance(annotation, type):
        raise _unmappable(annotation)
    if annotation is Env:
        raise _unmappable(
            annotation,
            "Env is the host handle, not a contract value -- spec.sections "
            "drops the leading `env` parameter instead of mapping it",
        )
    if annotation is Vec or annotation is Map:
        raise _unmappable(
            annotation,
            f"a container needs its element types: write {annotation.__name__}"
            + ("[T]" if annotation is Vec else "[K, V]"),
        )
    if issubclass(annotation, Bytes):
        return _bytes_family(annotation)
    scalar = _SCALARS.get(annotation)
    if scalar is not None:
        return xdr.SCSpecTypeDef(type=scalar)
    return _decorated(annotation)


def _bytes_family(annotation: type[Bytes]) -> xdr.SCSpecTypeDef:
    """`Bytes` -> `BYTES`; any fixed-length subclass -> `BYTES_N(_LENGTH)`.

    `is not None`, not truthiness: `bytes_n(0)` is a real (if useless) class
    whose `_LENGTH` is `0`, and it must not fall through to plain `BYTES`.
    """
    length = annotation._LENGTH
    if length is None:
        return xdr.SCSpecTypeDef(type=xdr.SCSpecType.SC_SPEC_TYPE_BYTES)
    return xdr.SCSpecTypeDef(
        type=xdr.SCSpecType.SC_SPEC_TYPE_BYTES_N,
        bytes_n=xdr.SCSpecTypeBytesN(n=xdr.Uint32(length)),
    )


def _option(annotation: object) -> xdr.SCSpecTypeDef:
    """`X | None` -> `OPTION(X)`.

    Both spellings arrive here: `U32 | None` is a `types.UnionType` while
    `Optional[U32]`/`Union[U32, None]` is a `typing.Union`, and which one an
    author writes must not change the emitted bytes. A union that is not
    exactly one type plus `None` (`U32 | I32`, or a three-way union) has no
    spec representation -- `SCSpecTypeDef` has no sum-of-types case.
    """
    args = typing.get_args(annotation)
    present = [arg for arg in args if arg is not types.NoneType]
    if len(present) != 1:
        raise _unmappable(
            annotation,
            "the only union the spec can express is `X | None` (OPTION); "
            "there is no sum-of-types case",
        )
    return xdr.SCSpecTypeDef(
        type=xdr.SCSpecType.SC_SPEC_TYPE_OPTION,
        option=xdr.SCSpecTypeOption(value_type=to_spec_type(present[0])),
    )


def _vec(annotation: object) -> xdr.SCSpecTypeDef:
    args = typing.get_args(annotation)
    if len(args) != 1:  # pragma: no cover - Generic[T] enforces the arity
        raise _unmappable(annotation)
    return xdr.SCSpecTypeDef(
        type=xdr.SCSpecType.SC_SPEC_TYPE_VEC,
        vec=xdr.SCSpecTypeVec(element_type=to_spec_type(args[0])),
    )


def _map(annotation: object) -> xdr.SCSpecTypeDef:
    args = typing.get_args(annotation)
    if len(args) != 2:  # pragma: no cover - Generic[K, V] enforces the arity
        raise _unmappable(annotation)
    return xdr.SCSpecTypeDef(
        type=xdr.SCSpecType.SC_SPEC_TYPE_MAP,
        map=xdr.SCSpecTypeMap(key_type=to_spec_type(args[0]), value_type=to_spec_type(args[1])),
    )


#: The three `_serpent_type_["kind"]` values a UDT reference can name (§B.3):
#: a struct, a tagged union, or an int enum. All three encode identically --
#: `SCSpecTypeUDT` carries only a NAME -- which is why one tuple, not three
#: separate `if` arms, decides it.
_UDT_KINDS: Final = ("struct", "union", "enum")


def _decorated(annotation: type) -> xdr.SCSpecTypeDef:
    """A serpent-decorated class: a struct/union/int enum -> `UDT`, else out.

    `vars(...)`, not `getattr`, for the same reason `decorators` uses it: an
    *undecorated* subclass of a struct (or union, or int enum) inherits
    `_serpent_type_` without having been declared, and emitting it as a UDT
    would reference a spec entry that is never written.
    """
    metadata: object = vars(annotation).get(_METADATA_ATTR)
    kind = metadata.get("kind") if isinstance(metadata, dict) else None
    if kind in _UDT_KINDS:
        return xdr.SCSpecTypeDef(
            type=xdr.SCSpecType.SC_SPEC_TYPE_UDT,
            udt=xdr.SCSpecTypeUDT(name=_udt_name(annotation)),
        )
    if isinstance(kind, str) and kind in _REFUSED_KINDS:
        raise _unmappable(annotation, _REFUSED_KINDS[kind])
    if issubclass(annotation, Event):
        # `Event` itself, or a subclass someone forgot to decorate.
        raise _unmappable(annotation, _REFUSED_KINDS["event"])
    raise _unmappable(annotation)


def _udt_name(annotation: type) -> bytes:
    """The UDT reference name: the class's own name, UTF-8 encoded.

    The reference has to be the class name because it names the
    `UDT_STRUCT_V0`/`UDT_UNION_V0`/`UDT_ENUM_V0` entry `spec.sections` writes
    for the same class. The length check is not this module claiming the cap
    -- `SCSpecTypeUDT` is a `string<60>` and `stellar_sdk` rejects an
    over-long name in its constructor, so the alternative is a bare
    `ValueError` that names no annotation at all.
    """
    encoded = annotation.__name__.encode("utf-8")
    if len(encoded) > _UDT_NAME_LIMIT:
        raise _unmappable(
            annotation,
            f"a UDT name is capped at {_UDT_NAME_LIMIT} bytes by the spec XDR "
            f"(SCSpecTypeUDT.name is a string<{_UDT_NAME_LIMIT}>), got "
            f"{len(encoded)} -- rename the @contracttype",
        )
    return encoded


def _unmappable(annotation: object, why: str | None = None) -> SpecTypeError:
    """The one error constructor: always names the annotation."""
    rendered = _render(annotation)
    if why is None:
        why = (
            "not a chain type, a `@contracttype` struct, or `X | None` of one "
            "(the contract spec can only carry serpent's chain types)"
        )
    return SpecTypeError(f"{rendered} has no contract-spec type: {why}")


def _render(annotation: object) -> str:
    """A readable name for an annotation in an error message.

    Mirrors `decorators._render`, widened for the parameterized forms this
    module also sees (`Vec[U32]`, `U32 | None`), whose `repr` is already the
    written form. Deliberately a local, cosmetic helper -- unlike
    `_METADATA_ATTR`, which is imported so the metadata key has exactly one
    definition.
    """
    if annotation is None or annotation is types.NoneType:
        return "None"
    if isinstance(annotation, type):
        return annotation.__name__
    if typing.get_origin(annotation) is not None:
        # A parameterized form: its `str` IS the written spelling.
        return str(annotation)
    return repr(annotation)
