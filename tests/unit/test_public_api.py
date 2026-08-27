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

import serpent
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
    "Address",
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
]


def test_public_all_is_exactly_the_frozen_export_list() -> None:
    assert serpent.__all__ == EXPECTED_ALL


def test_every_name_in_all_is_actually_importable() -> None:
    for name in serpent.__all__:
        assert hasattr(serpent, name), f"serpent.__all__ names {name!r}, but it is not defined"


def test_version_string() -> None:
    assert serpent.__version__ == "0.0.1"


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
