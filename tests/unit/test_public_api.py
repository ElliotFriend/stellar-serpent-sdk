"""Pins `serpent.__all__`: the whole public authoring surface, in one place.

A contract author imports everything from the package root
(`from serpent import U32, contract, Env, ...`); the submodules
(`serpent.types`, `serpent.decorators`, `serpent.env`, `serpent.errors`) are
implementation seams, not the documented surface. This test is the freeze --
adding, renaming or removing a public name means editing the sorted list
below, on purpose, in the same diff.

U256/I256 are deliberately absent (deferred to M2, per the amended spec).
`BytesN` does not exist as a name -- the fixed-length family is `Bytes32`,
`Bytes64` and the `bytes_n(n)` factory.
"""

import typing

import pytest

import serpent
from serpent import decorators as decorators_module
from tests.fixtures import token_style

#: Ordered by ruff's `RUF022` `__all__`-sort convention (which `ruff check
#: --fix` enforces on `serpent/__init__.py`): all-caps names, then CapWord
#: names, then lowercase/dunder names, alphabetically within each group. So a
#: diff on this list is always a single clean insertion/removal, never a
#: reordering.
EXPECTED_ALL = [
    "I32",
    "I64",
    "I128",
    "U32",
    "U64",
    "U128",
    "AbiCheckFailed",
    "Address",
    "Annotated",
    "ArithmeticOverflow",
    "BadArgument",
    "Bool",
    "Bytes",
    "Bytes32",
    "Bytes64",
    "ChainValue",
    "ContractError",
    "Duration",
    "Env",
    "Event",
    "Map",
    "MissingValue",
    "String",
    "Symbol",
    "Timepoint",
    "Vec",
    "__version__",
    "bytes_n",
    "contract",
    "contracterror",
    "contractevent",
    "contracttype",
    "errorcode",
    "topic",
]


def test_public_all_is_exactly_the_frozen_export_list() -> None:
    assert serpent.__all__ == EXPECTED_ALL


def test_annotated_and_topic_are_authorable_off_the_root() -> None:
    """A contract module may import ONLY from `serpent` (SPT2005), so the event
    convention's two spellings -- `typing.Annotated` and serpent's own `topic`
    marker -- have to be reachable there. `Annotated` is re-exported, never
    re-defined: `Annotated[Address, topic]` must be the same object a type
    checker already understands."""
    assert serpent.Annotated is typing.Annotated
    assert repr(serpent.topic) == "topic"
    assert serpent.topic is decorators_module.topic


def test_every_name_in_all_is_actually_importable() -> None:
    for name in serpent.__all__:
        assert hasattr(serpent, name), f"serpent.__all__ names {name!r}, but it is not defined"


def test_version_string() -> None:
    assert serpent.__version__ == "0.0.1"


def test_missing_value_and_abi_check_failed_are_catchable_off_the_root() -> None:
    """M4: an author writes `except serpent.MissingValue`, never
    `except serpent.errors.MissingValue` -- `serpent.errors` is an
    implementation seam, not the documented surface (module docstring)."""
    assert issubclass(serpent.MissingValue, serpent.ContractError)
    assert issubclass(serpent.AbiCheckFailed, serpent.ContractError)
    with pytest.raises(serpent.MissingValue):
        raise serpent.MissingValue()
    with pytest.raises(serpent.AbiCheckFailed):
        raise serpent.AbiCheckFailed()


def test_u256_i256_and_bytesn_are_not_exported() -> None:
    """U256/I256 are deferred to M2; `BytesN` was never the name (`bytes_n`
    and `Bytes32`/`Bytes64` are)."""
    for absent in ("U256", "I256", "BytesN"):
        assert absent not in serpent.__all__
        assert not hasattr(serpent, absent)


def test_token_style_fixture_imports_and_declares_its_shapes() -> None:
    """`tests/fixtures/token_style.py` imports ONLY from the `serpent` root
    (proof the root export list suffices to author a real contract) and is
    covered by the tests-wide `mypy --strict` run (the zero-plugin proof)."""
    assert token_style.TokenError.InsufficientBalance.code == 1
    assert token_style.TokenError.Unauthorized.code == 2
    assert issubclass(token_style.Transfer, serpent.Event)
    metadata = vars(token_style.TokenStyle)["_serpent_type_"]
    method_names = [name for name, _params, _returns in metadata["methods"]]
    assert method_names == [
        "__init__",
        "name",
        "is_admin",
        "balance",
        "mint",
        "transfer",
    ]
