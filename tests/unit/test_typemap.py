"""`serpent.spec.typemap`: chain type / metadata annotation -> `SCSpecTypeDef`.

Every expected value below is built with `stellar_sdk.xdr` classes *directly*
and compared on `.to_xdr_bytes()`, so a test failure means serpent disagrees
with the protocol encoding, not with a paraphrase of it. One case per mapping
row (the plan's Global Constraints table), plus the parameterized, `Option`,
UDT and unmappable cases.

`serpent.spec` is the ONE subpackage allowed to import `stellar_sdk`, which is
why this file may too (`tests/unit/test_core_zero_dep.py` pins the boundary for
everything else).
"""

import typing
from typing import Optional, Union

import pytest
from stellar_sdk import xdr

from serpent import (
    I32,
    I64,
    I128,
    U32,
    U64,
    U128,
    Address,
    Bool,
    Bytes,
    Bytes32,
    Bytes64,
    Duration,
    Env,
    Event,
    Map,
    String,
    Symbol,
    Timepoint,
    Vec,
    bytes_n,
    contract,
    contracterror,
    contractevent,
    contracttype,
    errorcode,
)
from serpent.spec import SpecTypeError, to_spec_type


@contracttype
class Settings:
    """A struct fixture: `@contracttype` -> UDT by class name."""

    counter_limit: U32
    owner: Address


class UndecoratedSettings(Settings):
    """An undecorated subclass: it *inherits* `_serpent_type_` but is not
    declared, so it must NOT be mapped as a UDT (the `vars()` rule)."""


@contracterror
class TokenError:
    LimitExceeded = errorcode(7)


@contractevent
class Transfer(Event):
    amount: I128


@contract
class Counter:
    def bump(self, env: Env) -> U32:
        return U32(0)


def _simple(spec_type: xdr.SCSpecType) -> xdr.SCSpecTypeDef:
    return xdr.SCSpecTypeDef(type=spec_type)


def _bytes_n(n: int) -> xdr.SCSpecTypeDef:
    return xdr.SCSpecTypeDef(
        type=xdr.SCSpecType.SC_SPEC_TYPE_BYTES_N,
        bytes_n=xdr.SCSpecTypeBytesN(n=xdr.Uint32(n)),
    )


def _option(inner: xdr.SCSpecTypeDef) -> xdr.SCSpecTypeDef:
    return xdr.SCSpecTypeDef(
        type=xdr.SCSpecType.SC_SPEC_TYPE_OPTION,
        option=xdr.SCSpecTypeOption(value_type=inner),
    )


def _vec(element: xdr.SCSpecTypeDef) -> xdr.SCSpecTypeDef:
    return xdr.SCSpecTypeDef(
        type=xdr.SCSpecType.SC_SPEC_TYPE_VEC,
        vec=xdr.SCSpecTypeVec(element_type=element),
    )


def _map(key: xdr.SCSpecTypeDef, value: xdr.SCSpecTypeDef) -> xdr.SCSpecTypeDef:
    return xdr.SCSpecTypeDef(
        type=xdr.SCSpecType.SC_SPEC_TYPE_MAP,
        map=xdr.SCSpecTypeMap(key_type=key, value_type=value),
    )


def _udt(name: str) -> xdr.SCSpecTypeDef:
    return xdr.SCSpecTypeDef(
        type=xdr.SCSpecType.SC_SPEC_TYPE_UDT,
        udt=xdr.SCSpecTypeUDT(name=name.encode()),
    )


#: One entry per row of the plan's type-mapping table, then the composites.
MAPPED: list[tuple[str, object, xdr.SCSpecTypeDef]] = [
    ("Bool", Bool, _simple(xdr.SCSpecType.SC_SPEC_TYPE_BOOL)),
    ("U32", U32, _simple(xdr.SCSpecType.SC_SPEC_TYPE_U32)),
    ("I32", I32, _simple(xdr.SCSpecType.SC_SPEC_TYPE_I32)),
    ("U64", U64, _simple(xdr.SCSpecType.SC_SPEC_TYPE_U64)),
    ("I64", I64, _simple(xdr.SCSpecType.SC_SPEC_TYPE_I64)),
    ("Timepoint", Timepoint, _simple(xdr.SCSpecType.SC_SPEC_TYPE_TIMEPOINT)),
    ("Duration", Duration, _simple(xdr.SCSpecType.SC_SPEC_TYPE_DURATION)),
    ("U128", U128, _simple(xdr.SCSpecType.SC_SPEC_TYPE_U128)),
    ("I128", I128, _simple(xdr.SCSpecType.SC_SPEC_TYPE_I128)),
    ("Bytes", Bytes, _simple(xdr.SCSpecType.SC_SPEC_TYPE_BYTES)),
    ("Bytes32", Bytes32, _bytes_n(32)),
    ("Bytes64", Bytes64, _bytes_n(64)),
    # The BYTES_N rule keys off `_LENGTH`, so an arbitrary factory length maps
    # exactly like the two named classes -- never a Bytes32/Bytes64 whitelist.
    ("bytes_n(20)", bytes_n(20), _bytes_n(20)),
    ("bytes_n(1)", bytes_n(1), _bytes_n(1)),
    # `_LENGTH == 0` is falsy: a truthiness check here would emit plain BYTES.
    ("bytes_n(0)", bytes_n(0), _bytes_n(0)),
    ("String", String, _simple(xdr.SCSpecType.SC_SPEC_TYPE_STRING)),
    ("Symbol", Symbol, _simple(xdr.SCSpecType.SC_SPEC_TYPE_SYMBOL)),
    ("Address", Address, _simple(xdr.SCSpecType.SC_SPEC_TYPE_ADDRESS)),
    ("Vec[U32]", Vec[U32], _vec(_simple(xdr.SCSpecType.SC_SPEC_TYPE_U32))),
    (
        "Map[Symbol, I128]",
        Map[Symbol, I128],
        _map(
            _simple(xdr.SCSpecType.SC_SPEC_TYPE_SYMBOL),
            _simple(xdr.SCSpecType.SC_SPEC_TYPE_I128),
        ),
    ),
    (
        "Vec[Map[Symbol, I128]]",
        Vec[Map[Symbol, I128]],
        _vec(
            _map(
                _simple(xdr.SCSpecType.SC_SPEC_TYPE_SYMBOL),
                _simple(xdr.SCSpecType.SC_SPEC_TYPE_I128),
            )
        ),
    ),
    (
        "Map[Address, Vec[Bytes32]]",
        Map[Address, Vec[Bytes32]],
        _map(
            _simple(xdr.SCSpecType.SC_SPEC_TYPE_ADDRESS),
            _vec(_bytes_n(32)),
        ),
    ),
    # Both spellings of the same annotation must produce the same bytes: `X |
    # None` arrives as a `types.UnionType`, `Optional[X]`/`Union[X, None]` as a
    # `typing.Union`. The legacy spellings are the whole point of these rows, so
    # ruff's modernization rules are silenced HERE and nowhere else.
    ("U32 | None", U32 | None, _option(_simple(xdr.SCSpecType.SC_SPEC_TYPE_U32))),
    (
        "Optional[U32]",
        Optional[U32],  # noqa: UP045
        _option(_simple(xdr.SCSpecType.SC_SPEC_TYPE_U32)),
    ),
    (
        "Union[U32, None]",
        Union[U32, None],  # noqa: UP007
        _option(_simple(xdr.SCSpecType.SC_SPEC_TYPE_U32)),
    ),
    (
        "Optional[Vec[U32]]",
        Optional[Vec[U32]],  # noqa: UP045
        _option(_vec(_simple(xdr.SCSpecType.SC_SPEC_TYPE_U32))),
    ),
    ("Settings", Settings, _udt("Settings")),
    ("Settings | None", Settings | None, _option(_udt("Settings"))),
    # A UDT nested in a container. `Vec`'s type variable is bound to the
    # `ChainValue` protocol, which a `@contracttype` struct does NOT satisfy (it
    # has no `_SCVAL_RANK`/`_cmp_payload`), so mypy rejects the annotation even
    # though the decorators accept it and the mapping is well defined -- a real
    # authoring-surface gap for sub-plan C, recorded here with the narrowest
    # possible ignore rather than dropped from the table.
    ("Vec[Settings]", Vec[Settings], _vec(_udt("Settings"))),  # type: ignore[type-var]
]


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [pytest.param(a, e, id=name) for name, a, e in MAPPED],
)
def test_annotation_maps_to_spec_type(annotation: object, expected: xdr.SCSpecTypeDef) -> None:
    assert to_spec_type(annotation).to_xdr_bytes() == expected.to_xdr_bytes()


#: Everything with no authoring surface as a spec type. The second element is
#: what the error message must name (the annotation itself).
UNMAPPABLE: list[tuple[str, object, str]] = [
    ("None", None, "None"),
    ("NoneType", type(None), "None"),
    ("Env", Env, "Env"),
    ("Event", Event, "Event"),
    ("event class", Transfer, "Transfer"),
    ("error enum", TokenError, "TokenError"),
    ("contract class", Counter, "Counter"),
    ("undecorated struct subclass", UndecoratedSettings, "UndecoratedSettings"),
    ("bare Vec", Vec, "Vec"),
    ("bare Map", Map, "Map"),
    ("int", int, "int"),
    ("str", str, "str"),
    ("bytes", bytes, "bytes"),
    ("bool", bool, "bool"),
    ("float", float, "float"),
    ("object", object, "object"),
    ("list[int]", list[int], "list"),
    ("dict[str, int]", dict[str, int], "dict"),
    ("tuple[U32, U32]", tuple[U32, U32], "tuple"),
    ("non-Option union", U32 | I32, "I32"),
    ("three-way union", U32 | I32 | None, "I32"),
    ("Any", typing.Any, "Any"),
    ("a string annotation", "U32", "U32"),
]


@pytest.mark.parametrize(
    ("annotation", "named"),
    [pytest.param(a, n, id=label) for label, a, n in UNMAPPABLE],
)
def test_unmappable_annotation_raises_spec_type_error(annotation: object, named: str) -> None:
    with pytest.raises(SpecTypeError) as exc_info:
        to_spec_type(annotation)
    assert named in str(exc_info.value)


def test_spec_type_error_is_a_value_error() -> None:
    """Callers may catch `ValueError`, as everywhere else in serpent."""
    assert issubclass(SpecTypeError, ValueError)


def test_env_error_points_at_the_dropped_parameter() -> None:
    """`Env` is the most likely miss: every contract method takes `env: Env`
    second, and the spec has no input for it -- sections drops it."""
    with pytest.raises(SpecTypeError, match="env"):
        to_spec_type(Env)


def test_void_error_points_at_the_empty_outputs_rule() -> None:
    with pytest.raises(SpecTypeError, match="outputs"):
        to_spec_type(None)


def test_bare_container_error_shows_the_parameterized_form() -> None:
    with pytest.raises(SpecTypeError, match=r"Vec\[T\]"):
        to_spec_type(Vec)
    with pytest.raises(SpecTypeError, match=r"Map\[K, V\]"):
        to_spec_type(Map)


def test_event_error_points_at_sub_plan_e() -> None:
    with pytest.raises(SpecTypeError, match="sub-plan E"):
        to_spec_type(Transfer)


def test_deferred_wide_integers_have_no_authoring_surface() -> None:
    """U256/I256 are M2-deferred: there is nothing to map, by construction."""
    import serpent

    assert not hasattr(serpent, "U256")
    assert not hasattr(serpent, "I256")


def test_muxed_address_val_result_tuple_have_no_authoring_surface() -> None:
    """The other SCSpecType cases serpent deliberately cannot express."""
    import serpent

    for name in ("MuxedAddress", "Val", "Result", "Tuple"):
        assert not hasattr(serpent, name)


def test_udt_name_at_the_xdr_cap_still_maps() -> None:
    """60 bytes is the `SCSpecTypeUDT.name` `string<60>` boundary, not an error."""

    @contracttype
    class _AtCap:
        field: U32

    name = "A" * 60
    _AtCap.__name__ = name
    assert to_spec_type(_AtCap).to_xdr_bytes() == _udt(name).to_xdr_bytes()


def test_over_long_udt_name_raises_spec_type_error_naming_the_class() -> None:
    """`stellar_sdk` enforces the `string<60>` cap in `SCSpecTypeUDT.__init__`,
    so an over-long name cannot be deferred to `spec.sections`' source-located
    check -- typemap must refuse it as a named `SpecTypeError` rather than let a
    bare `ValueError` out. (Sections still validates first in a real build.)"""

    @contracttype
    class _TooLong:
        field: U32

    name = "B" * 61
    _TooLong.__name__ = name
    with pytest.raises(SpecTypeError) as exc_info:
        to_spec_type(_TooLong)
    message = str(exc_info.value)
    assert name in message
    assert "61" in message
    assert "60" in message


def test_udt_name_comes_from_the_class_not_the_metadata() -> None:
    assert to_spec_type(Settings).udt is not None
    udt = to_spec_type(Settings).udt
    assert udt is not None
    assert udt.name == b"Settings"


def test_typemap_module_exports_exactly_its_two_names() -> None:
    """The type mapping's own surface. (The whole `serpent.spec` package export
    list is pinned in `test_sections.py`, which owns the section builders.)"""
    from serpent.spec import typemap

    assert typemap.__all__ == ["SpecTypeError", "to_spec_type"]
    assert typemap.to_spec_type is to_spec_type
    assert typemap.SpecTypeError is SpecTypeError
