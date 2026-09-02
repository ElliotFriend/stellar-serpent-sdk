"""ONE marshalling layer between tier-1 chain values and ScVal (ruling E2, dossier §D.2).

Decoding is DRIVEN BY THE REQUESTED `ty` -- the same rule tier-1 `get(key, ty)`
follows (D11's re-typing): the host hands back a bare word or a bare Vec/Map, and
the requested type says whether that word is a `U32` or a `Color`, that Vec a
`Vec[U32]` or a `Shape`, that Map a `Map[Symbol, U32]` or a `Point`. Without a
`ty` (`decode_loose`) the three pairs are D6-coarse by construction, and that
is documented rather than guessed.

Struct field order is SORTED field names (M1-C's P7 sort, which `sections.py`'s
struct entry and the emitter's `MakeStruct` both use), so the ScMap this
produces is byte-identical to what the compiled contract builds.

`stellar_sdk` is imported at module import: this module lives under
`serpent.testing`, the second recorded exemption from the zero-dep walk
(`tests/unit/test_core_zero_dep.py`); `import serpent` never imports it.

**The licensed private seam.** Everything else here goes through public
surfaces, but three accesses reach into serpent's own internals on purpose,
because no public reader answers the question:

* `serpent.decorators._METADATA_ATTR` (`"_serpent_type_"`) -- the recorded
  declaration of a union's cases and payload annotations, an int enum's
  discriminants and a struct's field annotations. `payload(index, ty)` cannot
  stand in for it: it needs the very `ty` the metadata is being read to find.
* `ContractUnion._payload_items()` (`types/_udt.py:265`) -- the payload tuple
  without the leading case `Symbol`. `tag()` beside it is public; there is no
  public whole-payload reader.
* `serpent.types._ordering.ChainValue` / `Struct` -- the two permissive element
  types `Vec`/`Map` themselves fall back to for heterogeneous contents
  (`containers._value_element_type_for`), reused here so a decoded container
  never claims an element type its own contents fail.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any

from stellar_sdk import scval
from stellar_sdk.xdr import SCVal, SCValType

from serpent.decorators import _METADATA_ATTR
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
    ContractEnum,
    ContractUnion,
    Duration,
    Map,
    String,
    Symbol,
    Timepoint,
    Vec,
)
from serpent.types._ordering import ChainValue, Struct

__all__ = ["ScValError", "decode", "decode_loose", "encode", "from_xdr", "to_xdr"]


class ScValError(ValueError):
    """An ScVal and a requested type disagree, or a value has no ScVal form."""


# --- encode ---------------------------------------------------------------------


def encode(value: object) -> SCVal:
    """A tier-1 chain value, a `@contracttype` struct, or `None` as one `SCVal`.

    The three composite shapes are the byte-verified M1 conventions (dossier
    §D.2, `types/_udt.py`'s header): a struct is an ScMap with `Symbol` keys in
    SORTED field-name order, a union is an ScVec led by the case `Symbol` with
    its payload in DECLARATION order (a unit case being a one-element ScVec),
    and an int enum is a bare `U32`.
    """
    if value is None:
        return scval.to_void()
    if isinstance(value, Bool):
        return scval.to_bool(value.value)
    if isinstance(value, U32):
        return scval.to_uint32(value.value)
    if isinstance(value, I32):
        return scval.to_int32(value.value)
    if isinstance(value, Timepoint):
        return scval.to_timepoint(value.value)
    if isinstance(value, Duration):
        return scval.to_duration(value.value)
    if isinstance(value, U64):
        return scval.to_uint64(value.value)
    if isinstance(value, I64):
        return scval.to_int64(value.value)
    if isinstance(value, U128):
        return scval.to_uint128(value.value)
    if isinstance(value, I128):
        return scval.to_int128(value.value)
    if isinstance(value, Symbol):
        return scval.to_symbol(value.text)
    if isinstance(value, String):
        return scval.to_string(value.text)
    if isinstance(value, Bytes):  # Bytes32/Bytes64/bytes_n(N) subclass Bytes: one ScVal kind
        return scval.to_bytes(value.data)
    if isinstance(value, Address):
        return scval.to_address(value.strkey)
    if isinstance(value, ContractEnum):
        return scval.to_uint32(_enum_discriminant(value))
    if isinstance(value, ContractUnion):
        items = [encode(value.tag()), *(encode(item) for item in value._payload_items())]
        return scval.to_vec(items)
    if isinstance(value, Vec):
        return scval.to_vec([encode(item) for item in value])
    if isinstance(value, Map):
        keys = value.keys()  # already in `val_cmp` order; `to_map` sorts by the same rules
        return scval.to_map({encode(key): encode(value.get(key)) for key in keys})
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        names = sorted(field.name for field in dataclasses.fields(value))
        return scval.to_map({scval.to_symbol(n): encode(getattr(value, n)) for n in names})
    raise ScValError(f"{value!r} ({type(value).__name__}) has no ScVal form")


def _enum_discriminant(value: ContractEnum) -> int:
    """An int-enum member's `u32`, read off its class's recorded cases.

    `ContractEnum` deliberately exposes no `.value` (ruling E5's sub-ruling), so
    the discriminant is found the way `__repr__` finds the member NAME: by
    matching the member against each declared case. `==` compares discriminants
    and they are unique within a class (`@contractenum` refuses a repeat), so
    exactly one case can match.
    """
    metadata = _value_metadata(type(value), "enum")
    for name, discriminant in metadata["cases"]:
        if value == getattr(type(value), name):
            return int(discriminant)
    raise ScValError(f"{value!r} is not a declared case of {type(value).__name__}")


def _value_metadata(ty: type[Any], *kinds: str) -> dict[str, Any]:
    """`ty`'s own `_serpent_type_` record, refusing an undeclared class.

    `vars(ty)`, not `getattr`: a record must be the class's OWN, never one
    inherited from a base (`ContractUnion` itself carries none, and neither
    base may be subclassed twice over).
    """
    metadata = vars(ty).get(_METADATA_ATTR)
    if not isinstance(metadata, dict) or metadata.get("kind") not in kinds:
        raise ScValError(
            f"{ty.__name__} carries no {' or '.join(kinds)} declaration -- decorate it with "
            "@contractunion/@contractenum/@contracttype before marshalling it"
        )
    return metadata


# --- decode ---------------------------------------------------------------------


def decode(sc: SCVal, ty: object) -> object:
    """`sc` as the value of type `ty` -- D11's re-typing, applied at the XDR boundary.

    `ty` is a chain-type class, a `@contracttype` class, a `ContractUnion`/
    `ContractEnum` subclass, a `Vec[T]`/`Map[K, V]` generic alias, `X | None`,
    or `type(None)`. Any disagreement raises `ScValError` naming the ScVal kind
    and the requested type: on chain a struct and a `Map`, a union and a `Vec`,
    an int enum and a `U32` are the same three words (D6), so the requested type
    is the ONLY thing that can tell them apart, and a silent guess here is the
    exact failure mode tier 1 exists to avoid.

    A BARE `Vec` or `Map` is accepted as `ty` (and as a generic alias's element
    or key/value type) and means "decode the elements with `decode_loose`"
    (review M5). `CHAIN_VALUES` generates nested containers whose element type
    IS the bare class -- `Vec(Vec[U32], ...)` is itself a `TypeError`, so
    `Vec(Vec, [...])` is the only spelling -- and loose elements are exact for
    such a container, because container equality is by CONTENT: `Vec(U32, []) ==
    Vec(Bool, [])` is True, while content equality still keeps the scalar kinds
    inside distinct (`U32(1) != I32(1)`). The one shape it cannot recover is a
    `@contracttype` struct reached through a bare container type, which comes
    back as the `Map` it is on chain; pass the precise alias (`Vec[Holder]`) for
    that.
    """
    origin = typing.get_origin(ty)
    if origin is typing.Union or origin is types.UnionType:
        return _decode_option(sc, ty)
    if origin is not None:
        args = typing.get_args(ty)
        if origin is Vec and len(args) == 1:
            return _decode_vec(sc, args[0], ty)
        if origin is Map and len(args) == 2:
            return _decode_map(sc, args[0], args[1], ty)
        raise ScValError(f"expected {ty!r}, got ScVal {sc.type.name}")
    if ty is None or ty is types.NoneType:
        _require_kind(sc, SCValType.SCV_VOID, ty)
        return None
    if not isinstance(ty, type):
        raise ScValError(f"{ty!r} is not a type serpent can decode an ScVal as")
    if ty in _LOOSE_TYPES:
        # The permissive element types a container falls back to for
        # heterogeneous contents, plus the bare containers themselves: none of
        # them narrows anything, so the honest reading is the untyped one.
        return decode_loose(sc)
    if issubclass(ty, Vec):
        return _decode_vec(sc, _LOOSE, ty)
    if issubclass(ty, Map):
        return _decode_map(sc, _LOOSE, _LOOSE, ty)
    if issubclass(ty, ContractUnion):
        return _decode_union(sc, ty)
    if issubclass(ty, ContractEnum):
        return _decode_enum(sc, ty)
    if issubclass(ty, Bytes):  # the whole family is ONE ScVal kind; the class checks the length
        return _decode_bytes(sc, ty)
    scalar = _SCALARS.get(ty)
    if scalar is not None:
        return scalar(_require_kind(sc, _scalar_kind(ty), ty))
    if dataclasses.is_dataclass(ty):
        return _decode_struct(sc, ty)
    raise ScValError(f"{ty!r} is not a type serpent can decode an ScVal as")


def decode_loose(sc: SCVal) -> object:
    """`sc` as a chain value with NO type guidance: by ScVal kind alone.

    Scalars come back as their one chain class, an ScVec as a `Vec`, an ScMap as
    a `Map`, and Void as `None`. **D6-coarse by design**: a struct arrives as a
    `Map`, a union as a `Vec` and an int enum as a `U32`, because on chain that
    is all they are and nothing here has been told otherwise. This is the reader
    for the positions that carry no declared type -- event topics and data, and
    auth arguments -- and it is deliberately the only place in this module that
    answers without a `ty`.
    """
    kind = sc.type
    if kind is SCValType.SCV_VOID:
        return None
    if kind is SCValType.SCV_VEC:
        return _decode_vec(sc, _LOOSE, Vec)
    if kind is SCValType.SCV_MAP:
        return _decode_map(sc, _LOOSE, _LOOSE, Map)
    loose = _LOOSE_BY_KIND.get(kind)
    if loose is None:
        raise ScValError(f"ScVal {kind.name} has no tier-1 chain value")
    return loose(sc)


def to_xdr(value: object) -> bytes:
    """`encode(value)` as XDR bytes -- the wire form the real host speaks."""
    return encode(value).to_xdr_bytes()


def from_xdr(xdr: bytes, ty: object) -> object:
    """XDR bytes back as the value of type `ty` (`decode`'s rules throughout)."""
    return decode(SCVal.from_xdr_bytes(xdr), ty)


# --- the decode arms ------------------------------------------------------------


def _require_kind(sc: SCVal, kind: SCValType, ty: object) -> SCVal:
    """`sc`, once its kind is exactly `kind`; `ScValError` otherwise.

    Exactly, not compatibly: a `U64` is not a `U32` even when its value fits, so
    a wrong-width word is refused here rather than silently narrowed.
    """
    if sc.type is not kind:
        raise ScValError(f"expected {_render(ty)}, got ScVal {sc.type.name}")
    return sc


def _render(ty: object) -> str:
    return getattr(ty, "__name__", None) or repr(ty)


def _decode_option(sc: SCVal, ty: object) -> object:
    """`X | None`: Void is `None`, anything else is decoded as `X`."""
    args = [arg for arg in typing.get_args(ty) if arg is not types.NoneType]
    if sc.type is SCValType.SCV_VOID:
        return None
    if len(args) != 1:
        raise ScValError(f"{ty!r} is not `X | None` of one type serpent can decode")
    return decode(sc, args[0])


class _Loose:
    """The internal "no type guidance" marker for a container's element type.

    A distinct sentinel rather than `None`, because `None` is already a
    MEANINGFUL `ty` at this boundary (`decode(sc, None)` requires Void), and one
    object cannot be both "decode this as Void" and "decode this however it
    comes".
    """

    def __repr__(self) -> str:
        return "<loose>"


_LOOSE = _Loose()


def _decode_element(sc: SCVal, ty: object) -> object:
    return decode_loose(sc) if ty is _LOOSE else decode(sc, ty)


def _decode_vec(sc: SCVal, element_ty: object, ty: object) -> Vec[Any]:
    if sc.type is not SCValType.SCV_VEC or sc.vec is None:
        raise ScValError(f"expected {_render(ty)}, got ScVal {sc.type.name}")
    items = [_decode_element(element, element_ty) for element in sc.vec.sc_vec]
    return Vec(_runtime_class(element_ty, items), items)


def _decode_map(sc: SCVal, key_ty: object, value_ty: object, ty: object) -> Map[Any, Any]:
    if sc.type is not SCValType.SCV_MAP or sc.map is None:
        raise ScValError(f"expected {_render(ty)}, got ScVal {sc.type.name}")
    keys = [_decode_element(entry.key, key_ty) for entry in sc.map.sc_map]
    values = [_decode_element(entry.val, value_ty) for entry in sc.map.sc_map]
    decoded: Map[Any, Any] = Map(_runtime_class(key_ty, keys), _runtime_class(value_ty, values))
    for key, value in zip(keys, values, strict=True):
        try:
            decoded.set(key, value)
        except TypeError as exc:  # a Void key or value: no tier-1 `Map` holds one
            raise ScValError(f"ScMap entry has no tier-1 form: {exc}") from exc
    return decoded


def _decode_union(sc: SCVal, ty: type[Any]) -> object:
    """An ScVec `[Symbol(case), *payload]` as the case `ty` declares.

    The payload is decoded slot by slot through the case's DECLARED annotations
    -- the same order `@contractunion` recorded them in -- and the value is built
    through the case descriptor, so `mypy --strict`'s constructor is also the
    runtime's.
    """
    if sc.type is not SCValType.SCV_VEC or sc.vec is None:
        raise ScValError(f"expected {_render(ty)}, got ScVal {sc.type.name}")
    elements = sc.vec.sc_vec
    if not elements or elements[0].type is not SCValType.SCV_SYMBOL:
        raise ScValError(
            f"expected {_render(ty)}, got an ScVec that does not lead with a case Symbol"
        )
    case = scval.from_symbol(elements[0])
    cases: list[tuple[str, tuple[object, ...]]] = _value_metadata(ty, "union")["cases"]
    for name, slots in cases:
        if name != case:
            continue
        payload = elements[1:]
        if len(payload) != len(slots):
            raise ScValError(
                f"{_render(ty)}.{name} declares {len(slots)} payload slot(s), "
                f"but the ScVec carries {len(payload)}"
            )
        decoded = [decode(element, slot) for element, slot in zip(payload, slots, strict=True)]
        descriptor = getattr(ty, name)
        # A unit case's descriptor IS the value; every other case is called.
        return descriptor if not slots else descriptor(*decoded)
    declared = ", ".join(name for name, _ in cases)
    raise ScValError(f"{_render(ty)} declares no case {case!r} (it has: {declared})")


def _decode_enum(sc: SCVal, ty: type[Any]) -> object:
    """A bare `U32` as the `ty` case with that discriminant."""
    discriminant = scval.from_uint32(_require_kind(sc, SCValType.SCV_U32, ty))
    cases: list[tuple[str, int]] = _value_metadata(ty, "enum")["cases"]
    for name, declared in cases:
        if declared == discriminant:
            return getattr(ty, name)
    known = ", ".join(f"{name}={declared}" for name, declared in cases)
    raise ScValError(f"{_render(ty)} declares no case with discriminant {discriminant} ({known})")


def _decode_bytes(sc: SCVal, ty: type[Bytes]) -> Bytes:
    """An ScBytes as `Bytes`, `Bytes32`, `Bytes64` or a `bytes_n(N)` class.

    The whole family is ONE ScVal kind, so the LENGTH is what a fixed-length
    request adds -- and the class's own constructor is what enforces it, exactly
    as it does for an author's literal. Its refusal is re-raised as `ScValError`
    so one exception type covers the whole boundary.
    """
    data = scval.from_bytes(_require_kind(sc, SCValType.SCV_BYTES, ty))
    try:
        return ty(data)
    except ValueError as exc:
        raise ScValError(f"expected {_render(ty)}, got {len(data)} bytes: {exc}") from exc


def _decode_struct(sc: SCVal, ty: type[Any]) -> object:
    """An ScMap with `Symbol` keys as the `@contracttype` struct `ty`.

    Field annotations come from the recorded `fields` pairs, which are already
    resolved to real objects (so a contract module using `from __future__ import
    annotations` needs no re-resolution here) and already stripped of any
    `Annotated[..., topic]` marker. A generic-alias annotation recurses, which is
    what a struct with a container field needs (`Holder.items: Vec[U32]`).
    """
    if sc.type is not SCValType.SCV_MAP or sc.map is None:
        raise ScValError(f"expected {_render(ty)}, got ScVal {sc.type.name}")
    entries: dict[str, SCVal] = {}
    for entry in sc.map.sc_map:
        if entry.key.type is not SCValType.SCV_SYMBOL:
            raise ScValError(
                f"expected {_render(ty)}, got an ScMap keyed by {entry.key.type.name} "
                "rather than by field-name Symbols"
            )
        entries[scval.from_symbol(entry.key)] = entry.val
    fields = _struct_fields(ty)
    missing = sorted({name for name, _ in fields} - entries.keys())
    unknown = sorted(entries.keys() - {name for name, _ in fields})
    if missing or unknown:
        raise ScValError(f"expected {_render(ty)}, got an ScMap with fields {missing=} {unknown=}")
    return ty(**{name: decode(entries[name], annotation) for name, annotation in fields})


def _struct_fields(ty: type[Any]) -> list[tuple[str, object]]:
    metadata = vars(ty).get(_METADATA_ATTR)
    if isinstance(metadata, dict) and metadata.get("kind") in ("struct", "event"):
        recorded: list[tuple[str, object]] = metadata["fields"]
        return recorded
    # A plain dataclass that no serpent decorator declared: fall back to its
    # annotations, resolved the way `decorators._annotations_of` resolves them.
    hints = typing.get_type_hints(ty)
    return [(field.name, hints[field.name]) for field in dataclasses.fields(ty)]


# --- element types --------------------------------------------------------------


def _runtime_class(ty: object, items: list[Any]) -> type[Any]:
    """The class a decoded `Vec`/`Map` should declare for `items`.

    A generic alias contributes its ORIGIN (`Vec[U32]` -> `Vec`), since that is
    what the container constructor takes. Anything else -- `_LOOSE` (no `ty` at
    all), a bare container, an `X | None` -- is read off the decoded
    contents by the same widening ladder `containers._value_element_type_for`
    uses, so the container never claims an element type its own contents fail
    (which would break its `push_back`/`slice`/`first_index_of`).
    """
    if isinstance(ty, type) and ty not in _LOOSE_TYPES:
        return ty
    origin = typing.get_origin(ty)
    if isinstance(origin, type):
        return origin
    return _class_of_items(items)


def _class_of_items(items: list[Any]) -> type[Any]:
    if not items:
        return U32  # an empty container's element type is unobservable (`Vec.__eq__`)
    first = type(items[0])
    if all(isinstance(item, first) for item in items):
        return first
    for candidate in (ChainValue, Struct):
        if all(isinstance(item, candidate) for item in items):
            return candidate
    return object


# --- the scalar tables ----------------------------------------------------------


def _bool(sc: SCVal) -> Bool:
    return Bool(scval.from_bool(sc))


def _string(sc: SCVal) -> String:
    return String(scval.from_string(sc).decode("utf-8"))


def _address(sc: SCVal) -> Address:
    return Address(scval.from_address(sc).address)


#: One ScVal kind and one reader per scalar chain class. Read by `decode` for
#: the requested type and by `decode_loose` for the kind alone, which is what
#: keeps the two directions from drifting apart.
_SCALARS: dict[type[Any], Any] = {
    Bool: _bool,
    U32: lambda sc: U32(scval.from_uint32(sc)),
    I32: lambda sc: I32(scval.from_int32(sc)),
    U64: lambda sc: U64(scval.from_uint64(sc)),
    I64: lambda sc: I64(scval.from_int64(sc)),
    Timepoint: lambda sc: Timepoint(scval.from_timepoint(sc)),
    Duration: lambda sc: Duration(scval.from_duration(sc)),
    U128: lambda sc: U128(scval.from_uint128(sc)),
    I128: lambda sc: I128(scval.from_int128(sc)),
    Symbol: lambda sc: Symbol(scval.from_symbol(sc)),
    String: _string,
    Bytes: lambda sc: Bytes(scval.from_bytes(sc)),
    Address: _address,
}

_KINDS: dict[type[Any], SCValType] = {
    Bool: SCValType.SCV_BOOL,
    U32: SCValType.SCV_U32,
    I32: SCValType.SCV_I32,
    U64: SCValType.SCV_U64,
    I64: SCValType.SCV_I64,
    Timepoint: SCValType.SCV_TIMEPOINT,
    Duration: SCValType.SCV_DURATION,
    U128: SCValType.SCV_U128,
    I128: SCValType.SCV_I128,
    Symbol: SCValType.SCV_SYMBOL,
    String: SCValType.SCV_STRING,
    Bytes: SCValType.SCV_BYTES,
    Address: SCValType.SCV_ADDRESS,
}

#: `decode_loose`'s inverse of `_KINDS`, built from it rather than restated, so
#: a scalar can never be encodable one way and not the other.
_LOOSE_BY_KIND: dict[SCValType, Any] = {_KINDS[ty]: reader for ty, reader in _SCALARS.items()}

#: Requested types that narrow NOTHING: the bare containers (review M5) and the
#: permissive element types `Vec`/`Map` fall back to for heterogeneous contents.
#: `decode` reads all of them as "no type guidance" and answers `decode_loose`.
_LOOSE_TYPES: tuple[object, ...] = (Vec, Map, ChainValue, Struct, object)


def _scalar_kind(ty: type[Any]) -> SCValType:
    """The one ScVal kind a scalar chain class is on chain."""
    return _KINDS[ty]
