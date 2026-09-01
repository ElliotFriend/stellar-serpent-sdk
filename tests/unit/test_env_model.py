"""The tier-1 `Env` model: storage, events, the ledger, and deep-copy isolation.

Every assertion here is about a MODEL, never about the chain. `serpent.env`'s
own header docstring says it: silent false green is this model's failure mode,
and sub-plan F's tier 2b is the gate. Two habits keep that honest in this file:

* structural equality is asserted with `storage_key` (the one cross-tier
  definition of value identity, `serpent.types._storage_key`) as well as `==`,
  so a test cannot pass because two tier-1 objects happen to be the same
  object;
* the places tier 1 answers a question the host answers differently, or does
  not answer at all, are pinned as such with the divergence named in the test's
  own docstring -- an honest pin beats a silent gap.

Every env here comes from `deployed_env()` (`conftest.py`) rather than `Env()`,
because the model refuses storage, events and the ledger outside an invocation
frame and refuses a frame before `deploy` (ruling E7(ii)). The helper deploys a
do-nothing contract and opens the frame, so these tests stay tests OF THE MODEL;
the framing itself is `test_env_deploy.py`'s subject. A plain `Env()` is used
where the test is about a surface that is deliberately NOT gated -- the
test-facing inspection hooks.
"""

import copy
from typing import Optional

import pytest
from hypothesis import given
from hypothesis import strategies as st

from serpent import (
    I32,
    I64,
    I128,
    U32,
    U64,
    U128,
    Address,
    Annotated,
    Bool,
    Bytes,
    Bytes32,
    Bytes64,
    Duration,
    Event,
    Map,
    String,
    Symbol,
    Timepoint,
    Vec,
    contractevent,
    contracttype,
    topic,
)
from serpent import env as env_module
from serpent.env import (
    DEFAULT_LEDGER_SEQUENCE,
    DEFAULT_LEDGER_TIMESTAMP,
    ChainValue,
    Env,
    Struct,
    _families_of_ty,
    tag_of_chain_value,
)
from serpent.errors import AbiCheckFailed, BadArgument, MissingValue
from serpent.types import bytes_n
from serpent.types._storage_key import storage_key
from tests.unit.conftest import deployed_env

ACCOUNT = "GA7QYNF7SOWQ3GLR2BGMZEHXAVIRZA4KVWLTJJFC7MGXUA74P7UJVSGZ"
CONTRACT = "CA3D5KRYM6CB7OWQ6TWYRR3Z4T7GNZLKERYNZGGA5SOAOPIFY6YQGAXE"


@contracttype
class AllowanceKey:
    """The dossier's Q11 composite key: an allowance keyed by `(from, spender)`."""

    owner: Address
    spender: Address


@contracttype
class Holder:
    items: Vec[U32]


@contracttype
class Named:
    name: Symbol
    count: U32


# The four `@contractevent` shapes `Event.publish` has to build a payload for
# (Task 6's tier-1 half): the three `data_format` cases and a long prefix topic.


@contractevent(topics=("transfer",), data_format="single-value")
class Transfer(Event):
    from_: Annotated[Address, topic]
    to: Annotated[Address, topic]
    amount: U32


@contractevent
class Traded(Event):
    who: Annotated[Address, topic]
    amount: U32
    memo: String


@contractevent(data_format="vec")
class Scored(Event):
    first: U32
    second: U32


@contractevent
class Carried(Event):
    items: Vec[U32]


@contractevent
class TransferCompleted(Event):
    amount: U32


def _buckets(env: Env) -> tuple[object, ...]:
    storage = env.storage()
    return (storage.instance(), storage.persistent(), storage.temporary())


# --- round-trips, per bucket, over the whole key surface --------------------


def test_each_bucket_round_trips_a_scalar() -> None:
    env = deployed_env()
    storage = env.storage()
    for bucket in (storage.instance(), storage.persistent(), storage.temporary()):
        bucket.set(Symbol("k"), U32(7))
        assert bucket.get(Symbol("k"), U32) == U32(7)


def test_the_three_buckets_are_separate_namespaces() -> None:
    """One store keyed `(durability, storage_key(key))`, mirroring the harness."""
    env = deployed_env()
    storage = env.storage()
    storage.instance().set(Symbol("k"), U32(1))
    storage.persistent().set(Symbol("k"), U32(2))
    storage.temporary().set(Symbol("k"), U32(3))
    assert storage.instance().get(Symbol("k"), U32) == U32(1)
    assert storage.persistent().get(Symbol("k"), U32) == U32(2)
    assert storage.temporary().get(Symbol("k"), U32) == U32(3)
    assert not storage.instance().has(Symbol("nope"))


def test_the_durability_ints_come_from_the_pinned_host_table() -> None:
    """ONE definition (S13): never a local literal in `env.py`."""
    from serpent._host._scalars import STORAGE_TYPE

    storage = deployed_env().storage()
    assert storage.temporary()._DURABILITY == STORAGE_TYPE["temporary"]
    assert storage.persistent()._DURABILITY == STORAGE_TYPE["persistent"]
    assert storage.instance()._DURABILITY == STORAGE_TYPE["instance"]


def test_a_struct_key_round_trips_and_is_compared_by_value() -> None:
    """A fresh, structurally-equal key finds the entry -- the failure
    `tests/harness/objects.py:36-49` was written to prevent."""
    env = deployed_env()
    bucket = env.storage().temporary()
    owner = Address(ACCOUNT)
    spender = Address(CONTRACT)
    bucket.set(AllowanceKey(owner=owner, spender=spender), I128(50))
    fresh = AllowanceKey(owner=Address(ACCOUNT), spender=Address(CONTRACT))
    assert bucket.get(fresh, I128) == I128(50)
    assert bucket.get(AllowanceKey(owner=spender, spender=owner), I128, I128(0)) == I128(0)


def test_container_keys_round_trip() -> None:
    env = deployed_env()
    bucket = env.storage().persistent()
    bucket.set(Vec(Symbol, [Symbol("a"), Symbol("b")]), U32(1))
    bucket.set(Map(Symbol, U32, [(Symbol("a"), U32(1))]), U32(2))
    assert bucket.get(Vec(Symbol, [Symbol("a"), Symbol("b")]), U32) == U32(1)
    assert bucket.get(Map(Symbol, U32, [(Symbol("a"), U32(1))]), U32) == U32(2)
    # Order-sensitive for a Vec, per `storage_key`.
    assert not bucket.has(Vec(Symbol, [Symbol("b"), Symbol("a")]))


def test_a_struct_value_round_trips() -> None:
    env = deployed_env()
    bucket = env.storage().instance()
    bucket.set(Symbol("h"), Named(name=Symbol("n"), count=U32(2)))
    got = bucket.get(Symbol("h"), Named)
    assert got == Named(name=Symbol("n"), count=U32(2))
    assert type(got) is Named


# --- the absent-key rules --------------------------------------------------


def test_a_defaultless_get_of_an_absent_key_raises_missing_value() -> None:
    """The SAME class the emitter's E13 guard's code names (ruling E8)."""
    bucket = deployed_env().storage().persistent()
    with pytest.raises(MissingValue) as excinfo:
        bucket.get(Symbol("absent"), U32)
    from serpent.errors import CODE_MISSING_VALUE

    assert type(excinfo.value).code == CODE_MISSING_VALUE


def test_a_default_is_returned_for_an_absent_key_and_never_ty_checked() -> None:
    """`get`'s default path mirrors the emitter's GET_DEFAULT `IfExp`: the
    `orelse` IS the default, un-narrowed."""
    bucket = deployed_env().storage().persistent()
    assert bucket.get(Symbol("absent"), U32, U32(9)) == U32(9)
    # A present key ignores the default.
    bucket.set(Symbol("k"), U32(1))
    assert bucket.get(Symbol("k"), U32, U32(9)) == U32(1)


def test_a_chain_value_default_comes_back_as_the_identical_object() -> None:
    """Ruling E5's default half, as an IDENTITY assertion.

    The compiled form is an `IfExp` whose `orelse` IS the default expression,
    so no host call and no copy stand between the author's object and the value
    of the whole expression. `==` cannot see the difference between "returned
    the object" and "returned a copy of it"; `is` can.
    """
    bucket = deployed_env().storage().persistent()
    default = U32(9)
    assert bucket.get(Symbol("absent"), U32, default=default) is default


def test_a_raw_default_is_adopted_through_the_requested_type() -> None:
    """The cross-tier fix: a NON-chain default is adopted through `ty`.

    `default=0` compiles -- M1-C adopts a literal in a typed position, so the
    compiled tier's `IfExp` orelse is the ADOPTED `U32(0)`. A tier-1 model that
    handed back the raw Python `0` would diverge SILENTLY, because `U32(0) == 0`
    is True and a type-blind assertion goes green either way. `type(...) is` is
    what makes it a failure.
    """
    bucket = deployed_env().storage().persistent()
    got = bucket.get(Symbol("absent"), U32, default=0)
    assert got == U32(0)
    assert type(got) is U32
    # Every scalar family the adoption reaches, not just the integers.
    assert type(bucket.get(Symbol("absent"), Bool, default=True)) is Bool
    assert type(bucket.get(Symbol("absent"), Symbol, default="NAME")) is Symbol
    assert type(bucket.get(Symbol("absent"), Bytes, default=b"\x01")) is Bytes


def test_a_raw_default_the_requested_type_refuses_raises_the_types_own_error() -> None:
    """Adoption does not soften the constructor: `ty`'s own loud error wins.

    A model that swallowed this (returning the raw value, or `None`) would hide
    a value the chain cannot represent behind a green test.
    """
    bucket = deployed_env().storage().persistent()
    with pytest.raises((ValueError, TypeError, OverflowError)):
        bucket.get(Symbol("absent"), U32, default=-1)


def test_an_explicit_none_default_is_the_no_default_case() -> None:
    """`default=None` is the sentinel, at both tiers: tier 1 raises
    `MissingValue` exactly as a bare `get` does, and the compiled tier refuses
    the spelling outright (`SPT3018`), so no contract can reach this."""
    bucket = deployed_env().storage().persistent()
    with pytest.raises(MissingValue):
        bucket.get(Symbol("absent"), U32, default=None)


def test_del_of_an_absent_key_is_a_silent_no_op() -> None:
    """Mirrors the mini-host (`hostfns.py:365-370`), and is an UNVERIFIED
    assumption about the real host -- see `del_`'s own docstring."""
    bucket = deployed_env().storage().temporary()
    bucket.del_(Symbol("never_written"))
    bucket.set(Symbol("k"), U32(1))
    bucket.del_(Symbol("k"))
    assert not bucket.has(Symbol("k"))
    bucket.del_(Symbol("k"))


def test_has_returns_a_chain_bool_not_a_python_bool() -> None:
    """Q12, and dossier F.1.2's named silent case."""
    bucket = deployed_env().storage().instance()
    absent = bucket.has(Symbol("k"))
    bucket.set(Symbol("k"), U32(1))
    present = bucket.has(Symbol("k"))
    assert type(absent) is Bool
    assert type(present) is Bool
    assert bool(present) and not bool(absent)


# --- the ty check: TAG level, mirroring the emitter's abi_check -------------


def test_a_ty_mismatch_raises_abi_check_failed() -> None:
    bucket = deployed_env().storage().persistent()
    bucket.set(Symbol("k"), U32(1))
    with pytest.raises(AbiCheckFailed) as excinfo:
        bucket.get(Symbol("k"), I32)
    from serpent.errors import CODE_ABI_CHECK_FAILED

    assert type(excinfo.value).code == CODE_ABI_CHECK_FAILED


def test_the_bytes_family_shares_one_tag_family() -> None:
    """`Bytes32` stored and read back as `Bytes` is what the emitter's
    `TAG_BYTES_OBJECT` compare accepts (review B6's first bullet) -- and so is
    a plain `Bytes` of the right length read back as `Bytes32`: on chain there
    is no difference between the two, only a payload of some length."""
    bucket = deployed_env().storage().persistent()
    bucket.set(Symbol("k"), Bytes32(b"x" * 32))
    assert bucket.get(Symbol("k"), Bytes) == Bytes32(b"x" * 32)
    assert bucket.get(Symbol("k"), Bytes32) == Bytes32(b"x" * 32)
    bucket.set(Symbol("plain"), Bytes(b"x" * 32))
    assert bucket.get(Symbol("plain"), Bytes32) == Bytes32(b"x" * 32)
    b3 = bytes_n(3)
    bucket.set(Symbol("n"), b3(b"abc"))
    assert bucket.get(Symbol("n"), Bytes) == b3(b"abc")


def test_a_fixed_length_bytes_request_also_checks_the_length() -> None:
    """The tag family is not the whole check for the `Bytes` family.

    The emitter pairs its `TAG_BYTES_OBJECT` compare with a REAL length compare
    (`tagcheck_bytes_n`, `lower.py:1102-1115`), so a 3-byte payload read back as
    `Bytes32` fails on chain. This assertion used to say the opposite -- tier 1
    accepted it, which is the wrong direction to be coarse in: coarse the other
    way rejects what the chain accepts (a test failure), coarse this way accepts
    what the chain rejects (a green test and a failed invocation).
    """
    bucket = deployed_env().storage().persistent()
    b3 = bytes_n(3)
    bucket.set(Symbol("n"), b3(b"abc"))
    with pytest.raises(AbiCheckFailed, match="3 bytes"):
        bucket.get(Symbol("n"), Bytes32)
    with pytest.raises(AbiCheckFailed):
        bucket.get(Symbol("n"), Bytes64)
    # ...and the length rides through an Option, which composes rather than
    # tabulating.
    with pytest.raises(AbiCheckFailed):
        bucket.get(Symbol("n"), Bytes32 | None)  # type: ignore[arg-type]
    # A request that imposes no length still accepts anything in the family.
    assert bucket.get(Symbol("n"), Bytes) == b3(b"abc")
    assert bucket.get(Symbol("n"), b3) == b3(b"abc")


def test_a_struct_and_a_map_share_one_tag_family() -> None:
    """A struct IS a `Map<Symbol, V>` on chain (S9): the emitter maps
    `TyTag.STRUCT` to `TAG_MAP_OBJECT`, so tier 1 must accept both directions
    or it rejects what the chain accepts."""
    bucket = deployed_env().storage().persistent()
    bucket.set(Symbol("s"), Named(name=Symbol("n"), count=U32(1)))
    struct_read_as_map: object = bucket.get(Symbol("s"), Map)
    assert type(struct_read_as_map) is Named
    bucket.set(Symbol("m"), Map(Symbol, U32, [(Symbol("a"), U32(1))]))
    map_read_as_struct: object = bucket.get(Symbol("m"), Named)
    assert type(map_read_as_struct) is Map


def test_vec_and_map_element_types_are_not_checked() -> None:
    """The emitter's check is tag-only; a deeper tier-1 check would reject
    what the chain accepts (S13)."""
    bucket = deployed_env().storage().persistent()
    bucket.set(Symbol("v"), Vec(U32, [U32(1)]))
    assert bucket.get(Symbol("v"), Vec) == Vec(U32, [U32(1)])
    assert bucket.get(Symbol("v"), Vec[I32]) == Vec(U32, [U32(1)])
    bucket.set(Symbol("m"), Map(Symbol, U32, [(Symbol("a"), U32(1))]))
    assert bucket.get(Symbol("m"), Map[Symbol, I32]) == Map(Symbol, U32, [(Symbol("a"), U32(1))])
    # A Vec is still not a Map.
    with pytest.raises(AbiCheckFailed):
        bucket.get(Symbol("v"), Map)


def test_an_option_ty_accepts_the_wrapped_family() -> None:
    """`X | None` accepts Void or `X`'s own family, the way the emitter
    COMPOSES its `Option` check rather than tabulating one.

    The two `type: ignore`s are the honest static picture: the frozen `get`
    signature is `ty: type[_T]`, and an `X | None` is not a `type`. No compiled
    contract can produce one either (a `get`'s type argument must be a bare
    chain-type or struct name), so this path exists for hand-written tier-1
    calls and for the composition rule to be pinned somewhere.
    """
    bucket = deployed_env().storage().persistent()
    bucket.set(Symbol("k"), U32(1))
    assert bucket.get(Symbol("k"), U32 | None) == U32(1)  # type: ignore[arg-type]
    # Both spellings of the same type: `X | None` reaches the predicate as a
    # `types.UnionType`, `Optional[X]` as a `typing.Union`. The older spelling
    # is written deliberately here (hence the `noqa`) because it is a value a
    # hand-written caller can pass, and the predicate answers for both.
    assert (
        bucket.get(Symbol("k"), Optional[U32])  # type: ignore[call-overload]  # noqa: UP045
        == U32(1)
    )
    with pytest.raises(AbiCheckFailed):
        bucket.get(Symbol("k"), Symbol | None)  # type: ignore[arg-type]


def test_an_unrecognized_ty_fails_loudly() -> None:
    """A `ty` the whole authoring surface cannot produce (a compiled `get`'s
    type argument must be a bare chain-type or struct name) must not be
    silently accepted."""
    bucket = deployed_env().storage().persistent()
    bucket.set(Symbol("k"), U32(1))
    with pytest.raises(TypeError, match="not a chain type"):
        bucket.get(Symbol("k"), int)


@pytest.mark.parametrize(
    ("value", "family"),
    [
        (Bool(True), "bool"),
        (U32(1), "u32"),
        (I32(1), "i32"),
        (U64(1), "u64"),
        (I64(1), "i64"),
        (U128(1), "u128"),
        (I128(1), "i128"),
        (Timepoint(1), "timepoint"),
        (Duration(1), "duration"),
        (Symbol("s"), "symbol"),
        (String("s"), "string"),
        (Bytes(b"b"), "bytes"),
        (Bytes32(b"x" * 32), "bytes"),
        (Address(ACCOUNT), "address"),
        (Vec(U32, [U32(1)]), "vec"),
        (Map(Symbol, U32), "map"),
        (Named(name=Symbol("n"), count=U32(1)), "map"),
        (None, "void"),
    ],
)
def test_tag_of_chain_value_names_one_family_per_value(
    value: ChainValue | None, family: str
) -> None:
    assert tag_of_chain_value(value) == family


def test_tag_of_chain_value_rejects_a_non_chain_value() -> None:
    with pytest.raises(TypeError, match="not a chain value"):
        tag_of_chain_value("raw")  # type: ignore[arg-type]


def test_a_union_and_an_int_enum_never_reach_the_struct_fallthrough() -> None:
    """M1-E2 ruling E9's collision pin, at the model's own three doors.

    `Struct` is a `runtime_checkable` Protocol over `__dataclass_fields__` and
    it is the FALLTHROUGH in `tag_of_chain_value`, `_families_of_ty` and
    `storage_key`. Neither new kind is a dataclass, and both are matched by
    `_FAMILY_BY_TYPE` first -- so a union answers `"vec"` (it IS an `ScVec` on
    chain) and an int enum answers `"u32"` (it IS a bare `U32`). Were either a
    dataclass, all three doors would answer `"map"` instead: wrong family,
    wrong storage key, wrong ABI tag, and no error anywhere.

    The declarations come from `test_udt_values.py` rather than a second
    hand-bound copy (`@contractunion` is M1-E2 Task 2's).
    """
    from tests.unit.test_udt_values import Color, Shape

    for value in (Shape.Empty, Shape.Rect(U32(1), U32(2)), Color.Red):
        assert not isinstance(value, Struct)
    assert tag_of_chain_value(Shape.Circle(U32(1))) == "vec"
    assert tag_of_chain_value(Color.Green) == "u32"
    assert _families_of_ty(Shape) == frozenset({"vec"})
    assert _families_of_ty(Color) == frozenset({"u32"})


def test_a_union_and_an_int_enum_round_trip_through_storage_by_value() -> None:
    """The two new kinds are ordinary stored values: keyed by `storage_key`,
    deep-copied in and out (E5), and tag-checked against `ty` on the way out.

    A fresh-but-equal union as the KEY finds the entry an earlier call wrote,
    which is S13's whole point -- and a `get` under the wrong kind fails the
    ABI check rather than handing back the other kind's value.
    """
    from tests.unit.test_udt_values import Color, Shape

    bucket = deployed_env().storage().persistent()
    bucket.set(Shape.Circle(U32(7)), Color.Green)
    assert bucket.get(Shape.Circle(U32(7)), Color) == Color.Green
    bucket.set(Symbol("shape"), Shape.Rect(U32(1), U32(2)))
    assert bucket.get(Symbol("shape"), Shape) == Shape.Rect(U32(1), U32(2))
    with pytest.raises(AbiCheckFailed):
        bucket.get(Symbol("shape"), Color)


def test_a_union_default_passes_through_instead_of_being_re_adopted() -> None:
    """Ruling E5's default rule, for the two new kinds: a default that already
    IS a chain value comes back as the caller's own object, un-copied and
    un-checked, because the compiled `orelse` is that expression.

    This is what `_is_chain_value` answers, and the two M1-E2 arms are why it
    holds here -- without them a union default would be re-adopted through
    `ty`, i.e. a union rebuilt from itself.
    """
    from tests.unit.test_udt_values import Color, Shape

    bucket = deployed_env().storage().persistent()
    fallback = Shape.Circle(U32(9))
    assert bucket.get(Symbol("absent"), Shape, default=fallback) is fallback
    # The enum half in the `is` form, which is the stronger pin: an accessed
    # member is a FRESH instance every time (`_EnumValue.__get__` builds one),
    # so the identity being preserved is the pass-through itself -- and
    # `ContractEnum.__copy__` returning `self` is what makes `is` hold at all.
    enum_fallback = Color.Red
    assert bucket.get(Symbol("absent"), Color, default=enum_fallback) is enum_fallback


def test_the_tag_families_agree_with_the_emitters_abi_check_tables() -> None:
    """The cross-tier pin (review B6): `env.tag_of_chain_value`'s families and
    `emitter/lower.py`'s `abi_check` tag tables must partition the type space
    identically, or the two tiers disagree about what a `get` accepts.

    Read (never imported) by `env.py`: the emitter is not in the core zero-dep
    walk, so the model restates the mapping and THIS test is what keeps the
    restatement true.
    """
    from serpent import val
    from serpent.compiler.types_ import TyTag
    from serpent.emitter.lower import (
        _EITHER_ABI_TAGS,
        _IMMEDIATE_ABI_WORD,
        _OBJECT_ABI_TAG,
        ABI_CHECKED_TAGS,
    )
    from serpent.env import _FAMILY_BY_TYPE

    #: Which `TyTag` each env family answers for. `OPTION` is excluded: the
    #: emitter COMPOSES it (`VOID_VAL` or the wrapped type's own check) and so
    #: does the model, so it has no tag family of its own.
    family_by_tag: dict[TyTag, str] = {
        TyTag.BOOL: "bool",
        TyTag.U32: "u32",
        TyTag.I32: "i32",
        TyTag.U64: "u64",
        TyTag.I64: "i64",
        TyTag.U128: "u128",
        TyTag.I128: "i128",
        TyTag.TIMEPOINT: "timepoint",
        TyTag.DURATION: "duration",
        TyTag.SYMBOL: "symbol",
        TyTag.STRING: "string",
        TyTag.BYTES: "bytes",
        TyTag.BYTES_N: "bytes",
        TyTag.ADDRESS: "address",
        TyTag.VEC: "vec",
        TyTag.MAP: "map",
        TyTag.STRUCT: "map",
        # M1-E2 ruling E9's two new kinds, the pin `test_a_union_and_an_int_
        # enum_never_reach_the_struct_fallthrough` above makes at the model's
        # doors, restated here against the EMITTER's tables: a union IS an
        # `ScVec` (`vec`, `TAG_VEC_OBJECT`) and an int enum IS a bare `u32`
        # (`u32`, `TAG_U32`). Were either row missing, `ABI_CHECKED_TAGS` would
        # have grown a tag this cross-tier comparison never ran.
        TyTag.UNION: "vec",
        TyTag.ENUM: "u32",
    }
    assert set(family_by_tag) == ABI_CHECKED_TAGS - {TyTag.OPTION}
    # ...and the family NAMES are the model's own, both directions: a new row in
    # `_FAMILY_BY_TYPE` (a new chain type, a renamed family) fails here instead
    # of quietly not being compared against the emitter at all.
    assert set(family_by_tag.values()) == set(_FAMILY_BY_TYPE.values())

    def emitter_tags(tag: TyTag) -> frozenset[int]:
        """The `Val` tag bytes `abi_check` accepts for `tag`."""
        if tag is TyTag.BOOL:
            # `Bool`'s check is the word itself (0 or 1), whose tag byte is
            # `TAG_FALSE`/`TAG_TRUE` -- one family of its own either way.
            return frozenset({val.TAG_FALSE, val.TAG_TRUE})
        if tag is TyTag.BYTES_N:
            # The one hand-written row: `tagcheck_bytes_n` compares the SAME
            # object tag as `Bytes` and then a length, which is the one place
            # the emitter is finer-grained than a tag. Tier 1 takes the tag
            # half deliberately (ruling B6: tag level, so the two tiers agree
            # about the family).
            return frozenset({val.TAG_BYTES_OBJECT})
        immediate = _IMMEDIATE_ABI_WORD.get(tag)
        if immediate is not None:
            return frozenset({immediate & 0xFF})
        either = _EITHER_ABI_TAGS.get(tag)
        if either is not None:
            return frozenset(either)
        return frozenset({_OBJECT_ABI_TAG[tag]})

    for left, left_family in family_by_tag.items():
        for right, right_family in family_by_tag.items():
            shared_tag = bool(emitter_tags(left) & emitter_tags(right))
            assert shared_tag == (left_family == right_family), (left, right)


# --- deep-copy isolation: ruling E5's decision procedure --------------------


def test_isolation_a_stored_container_is_not_the_local() -> None:
    """**Ruling E5's decision procedure.** The model deep-copies at every
    serializing boundary, which is what makes the frontend's escape exemption
    for `<bucket>.set` still sound: mutate the local after the write and the
    store does not move, mutate the read result and the store does not move.

    If this property could NOT be made to hold, the escape flip in
    `recognize.collect_never_owned` would be mandatory instead.
    """
    bucket = deployed_env().storage().persistent()
    local = Vec(U32, [U32(1)])
    bucket.set(Symbol("v"), local)
    local.push_back(U32(2))
    stored = bucket.get(Symbol("v"), Vec)
    assert len(stored) == 1
    stored.push_back(U32(3))
    assert len(bucket.get(Symbol("v"), Vec)) == 1


def test_isolation_holds_for_maps_structs_and_nesting() -> None:
    bucket = deployed_env().storage().temporary()

    m = Map(Symbol, U32, [(Symbol("a"), U32(1))])
    bucket.set(Symbol("m"), m)
    m.set(Symbol("b"), U32(2))
    assert len(bucket.get(Symbol("m"), Map)) == 1

    holder = Holder(items=Vec(U32, [U32(1)]))
    bucket.set(Symbol("h"), holder)
    holder.items.push_back(U32(2))
    assert len(bucket.get(Symbol("h"), Holder).items) == 1

    nested = Vec(Holder, [Holder(items=Vec(U32, [U32(1)]))])
    bucket.set(Symbol("n"), nested)
    nested.get(0).items.push_back(U32(9))
    assert len(bucket.get(Symbol("n"), Vec).get(0).items) == 1


def test_isolation_two_reads_never_share_an_object() -> None:
    bucket = deployed_env().storage().instance()
    bucket.set(Symbol("v"), Vec(U32, [U32(1)]))
    first = bucket.get(Symbol("v"), Vec)
    second = bucket.get(Symbol("v"), Vec)
    assert first is not second
    first.push_back(U32(2))
    assert len(second) == 1


def test_isolation_a_key_mutated_after_the_write_still_finds_the_entry() -> None:
    """The key is normalized to a `storage_key` at write time, so mutating the
    key object afterwards cannot move the entry."""
    bucket = deployed_env().storage().persistent()
    key = Vec(Symbol, [Symbol("a")])
    bucket.set(key, U32(1))
    key.push_back(Symbol("b"))
    assert bucket.get(Vec(Symbol, [Symbol("a")]), U32) == U32(1)
    assert not bucket.has(key)


_SYMBOLS = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,9}", fullmatch=True).map(Symbol)
_U32S = st.integers(min_value=0, max_value=2**32 - 1).map(U32)

_SCALARS: st.SearchStrategy[ChainValue] = st.one_of(
    st.booleans().map(Bool),
    _U32S,
    st.integers(min_value=-(2**31), max_value=2**31 - 1).map(I32),
    st.integers(min_value=0, max_value=2**64 - 1).map(U64),
    st.integers(min_value=-(2**63), max_value=2**63 - 1).map(I64),
    st.integers(min_value=0, max_value=2**128 - 1).map(U128),
    st.integers(min_value=-(2**127), max_value=2**127 - 1).map(I128),
    st.integers(min_value=0, max_value=2**64 - 1).map(Timepoint),
    st.integers(min_value=0, max_value=2**64 - 1).map(Duration),
    _SYMBOLS,
    st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=8).map(String),
    st.binary(max_size=8).map(Bytes),
    st.binary(min_size=32, max_size=32).map(Bytes32),
    st.sampled_from([Address(ACCOUNT), Address(CONTRACT)]),
)


def _containers(inner: st.SearchStrategy[ChainValue]) -> st.SearchStrategy[ChainValue]:
    """Every mutable shape the model has to isolate: a `Vec`, a `Map`, a struct
    with a container field, a struct inside a `Vec`, and one level of the
    generated value nested inside a `Vec` of its own type."""
    return st.one_of(
        st.lists(_U32S, max_size=4).map(lambda items: Vec(U32, items)),
        st.lists(st.tuples(_SYMBOLS, _U32S), max_size=4, unique_by=lambda pair: pair[0].text).map(
            lambda pairs: Map(Symbol, U32, pairs)
        ),
        st.lists(_U32S, max_size=3).map(lambda items: Holder(items=Vec(U32, items))),
        st.lists(st.lists(_U32S, max_size=2), max_size=2).map(
            lambda outer: Vec(Holder, [Holder(items=Vec(U32, items)) for items in outer])
        ),
        inner.map(lambda value: Vec(type(value), [value])),
    )


CHAIN_VALUES: st.SearchStrategy[ChainValue] = st.recursive(_SCALARS, _containers, max_leaves=4)


def _grow(value: object) -> bool:
    """Mutate `value` in place if the model has to isolate it; report whether
    anything changed. This is the half of the property that makes it about
    ISOLATION rather than about round-tripping."""
    if isinstance(value, Vec):
        if len(value) == 0:
            return False
        value.push_back(copy.deepcopy(value.get(0)))
        return True
    if isinstance(value, Map):
        value.set(Symbol("grown"), U32(0))
        return True
    if isinstance(value, Holder):
        value.items.push_back(U32(0))
        return True
    return False


@given(key=CHAIN_VALUES, value=CHAIN_VALUES)
def test_the_isolation_property_holds_over_generated_chain_values(
    key: ChainValue, value: ChainValue
) -> None:
    """**Ruling E5's decision procedure, as a property.** For any ChainValue
    shape: what comes out of `get` is structurally equal to what went into
    `set`, has the same type, and is a DIFFERENT object graph -- so no
    mutation of either side can reach the other.
    """
    snapshot = storage_key(value)
    # `frame=False` plus an explicit frame, because Hypothesis runs this body
    # many times: the helper's frame stays open for the whole TEST, and two of
    # them at once would be two envs framed at once (which the model refuses).
    env = deployed_env(frame=False)
    with env.frame():
        bucket = env.storage().persistent()
        bucket.set(key, value)
        got = bucket.get(key, type(value))
        assert type(got) is type(value)
        assert storage_key(got) == snapshot
        assert got is not value
        # Mutating what came out cannot change the store, and neither can
        # mutating the original the caller still holds.
        _grow(got)
        _grow(value)
        assert storage_key(bucket.get(key, type(value))) == snapshot


@given(value=CHAIN_VALUES)
def test_deepcopy_preserves_the_type_of_every_generated_chain_value(
    value: ChainValue,
) -> None:
    """The property the model rests on, isolated: `copy.deepcopy` is
    type-preserving and structure-preserving for every ChainValue shape --
    including a `bytes_n(n)` factory class, whose instances are documented as
    not picklable (`buffers.py:215-217`) but which `deepcopy` reaches through
    the class OBJECT rather than by name."""
    clone = copy.deepcopy(value)
    assert type(clone) is type(value)
    assert storage_key(clone) == storage_key(value)
    assert tag_of_chain_value(clone) == tag_of_chain_value(value)


def test_deepcopy_preserves_a_bytes_n_factory_class() -> None:
    b3 = bytes_n(3)
    clone = copy.deepcopy(b3(b"abc"))
    assert type(clone) is b3


# --- events ----------------------------------------------------------------


def test_publish_records_a_snapshot() -> None:
    env = deployed_env()
    address = Address(ACCOUNT)
    data = Vec(U32, [U32(1)])
    env.events().publish((Symbol("transfer"), address, address), data)
    data.push_back(U32(2))
    assert len(env.published_events) == 1
    topics, recorded = env.published_events[0]
    assert topics == (Symbol("transfer"), address, address)
    assert isinstance(recorded, Vec)
    assert len(recorded) == 1


def test_published_events_is_an_immutable_snapshot_view() -> None:
    """The inspection surface obeys the same deep-copy law as `get`.

    A returned tuple cannot grow, and -- the half this test used to miss -- a
    returned RECORD cannot be mutated back into the model: the containers inside
    it are copies, so a test that pokes at what it just read does not corrupt
    what the next read returns.
    """
    env = deployed_env()
    env.events().publish((Symbol("a"),), Vec(U32, [U32(1)]))
    first = env.published_events
    env.events().publish((Symbol("b"),), U32(2))
    assert len(first) == 1
    assert len(env.published_events) == 2
    assert isinstance(env.published_events, tuple)

    data = env.published_events[0][1]
    assert isinstance(data, Vec)
    data.push_back(U32(99))
    reread = env.published_events[0][1]
    assert isinstance(reread, Vec)
    assert len(reread) == 1


def test_publish_requires_a_symbol_first_topic() -> None:
    """S10's convention, enforced at tier 1 and NOT by the host -- a
    deliberate tier-1-only reject, mirroring the frontend's SPT3019 so that
    nothing a compiled contract can express reaches it.

    "A Symbol" is the WHOLE of it (ruling E11). A topic past the 9-character
    `SymbolSmall` bound is an ordinary Symbol -- `Symbol.__init__` is the one
    place its 32-character bound lives -- so it is RECORDED here, exactly as a
    declared prefix topic of the same length already was.
    """
    env = deployed_env()
    with pytest.raises(BadArgument, match="topic"):
        env.events().publish((), U32(1))
    with pytest.raises(BadArgument, match="Symbol"):
        env.events().publish((U32(1),), U32(1))
    env.events().publish((Symbol("a_very_long_name"),), U32(1))
    assert env.published_events[-1] == ((Symbol("a_very_long_name"),), U32(1))


def test_an_undecorated_event_subclass_cannot_publish() -> None:
    """`publish` reads the `@contractevent` metadata, so a bare `Event`
    subclass has no topic convention to publish BY -- said plainly, rather than
    as an `AttributeError` from inside the model."""
    from serpent.env import Event

    class E(Event):
        __slots__ = ()

    with pytest.raises(TypeError, match="@contractevent"):
        E().publish(deployed_env())


def test_event_publish_builds_the_declared_topics_and_single_value_data() -> None:
    """Task 6's tier-1 half: the SAME topic list and data the compiler's
    desugar builds -- prefix topics first, then the marked fields in
    declaration order."""
    env = deployed_env()
    frm, to = Address(ACCOUNT), Address(CONTRACT)
    Transfer(from_=frm, to=to, amount=U32(7)).publish(env)
    assert env.published_events == (((Symbol("transfer"), frm, to), U32(7)),)


def test_event_publish_maps_data_fields_by_name_for_the_map_format() -> None:
    """The `"map"` default: a `Map<Symbol, Val>` keyed by field name, sorted the
    way every tier-1 `Map` is."""
    env = deployed_env()
    who = Address(ACCOUNT)
    Traded(who=who, amount=U32(3), memo=String("hi")).publish(env)
    (topics, data) = env.published_events[0]
    assert topics == (Symbol("traded"), who)
    assert isinstance(data, Map)
    assert data.get(Symbol("amount")) == U32(3)
    assert data.get(Symbol("memo")) == String("hi")
    assert list(data.keys()) == [Symbol("amount"), Symbol("memo")]


def test_event_publish_builds_a_vec_for_the_vec_format() -> None:
    env = deployed_env()
    Scored(first=U32(1), second=U32(2)).publish(env)
    (topics, data) = env.published_events[0]
    assert topics == (Symbol("scored"),)
    assert data == Vec(U32, [U32(1), U32(2)])


def test_event_publish_records_a_snapshot_like_events_publish() -> None:
    """Same law (E5): the record cannot be changed by mutating what was
    published, because `publish` deep-copies on the way in."""
    env = deployed_env()
    items = Vec(U32, [U32(1)])
    Carried(items=items).publish(env)
    items.push_back(U32(2))
    (_topics, data) = env.published_events[0]
    assert isinstance(data, Map)
    recorded = data.get(Symbol("items"))
    assert isinstance(recorded, Vec)
    assert len(recorded) == 1


def test_event_publish_needs_a_frame_like_every_other_env_operation() -> None:
    """It reaches the model through `env.events()`, so it inherits that
    accessor's gate: no deploy, no publish (ruling E7(ii))."""
    with pytest.raises(RuntimeError, match="before the contract was deployed"):
        Scored(first=U32(1), second=U32(2)).publish(Env())


def test_event_publish_accepts_a_prefix_topic_longer_than_nine_characters() -> None:
    """The cap is the Symbol's 32, not SymbolSmall's 9 (Task 5), and the
    frontend desugar pools a long prefix through linear memory rather than
    refusing it -- so the model must not refuse it either.

    Ruling E11 made that ONE rule rather than two: `Events.publish` no longer
    holds a hand-written `topics[0]` to the shorter bound, so this length is
    now legal through both halves of the surface and not just this one."""
    env = deployed_env()
    TransferCompleted(amount=U32(1)).publish(env)
    (topics, _data) = env.published_events[0]
    assert topics == (Symbol("transfer_completed"),)


# --- the ledger ------------------------------------------------------------


def test_the_ledger_reports_the_configured_values_as_chain_types() -> None:
    env = deployed_env()
    assert env.ledger().timestamp() == U64(DEFAULT_LEDGER_TIMESTAMP)
    assert env.ledger().sequence() == U32(DEFAULT_LEDGER_SEQUENCE)
    assert type(env.ledger().timestamp()) is U64
    assert type(env.ledger().sequence()) is U32


def test_the_ledger_reports_a_configured_timestamp_and_sequence() -> None:
    """Split out from the test above rather than folded into it: a second env
    needs a frame of its own, and two envs are never framed at once (M1 has no
    cross-contract call), so one env per test keeps both readable."""
    other = deployed_env(timestamp=42, sequence=7)
    assert other.ledger().timestamp() == U64(42)
    assert other.ledger().sequence() == U32(7)


def test_the_ledger_defaults_are_not_zero_and_are_shared_with_the_harness() -> None:
    """ONE definition across tiers (S13): the harness imports these."""
    from tests.harness import hostfns

    assert DEFAULT_LEDGER_TIMESTAMP != 0
    assert DEFAULT_LEDGER_SEQUENCE != 0
    assert hostfns.DEFAULT_LEDGER_TIMESTAMP is DEFAULT_LEDGER_TIMESTAMP
    assert hostfns.DEFAULT_LEDGER_SEQUENCE is DEFAULT_LEDGER_SEQUENCE


# --- the slotted surface ---------------------------------------------------


def test_env_refuses_an_unknown_attribute() -> None:
    """Dossier F.1.14: every class here is slotted, so an attribute typo must
    fail loudly rather than land in a `__dict__`."""
    env = deployed_env()
    with pytest.raises(AttributeError):
        env.timestamp = 1  # type: ignore[attr-defined]
    assert not hasattr(env, "__dict__")
    for obj in (*_buckets(env), env.storage(), env.ledger(), env.events()):
        assert not hasattr(obj, "__dict__")


def test_recorded_auths_starts_empty() -> None:
    """An env nobody has authorized against records nothing -- with or without
    an allow-set. The auth MODEL is `tests/unit/test_env_deploy.py`'s.

    Plain `Env()`s, deliberately: `recorded_auths` is a test-facing inspection
    surface, not a host call, so it answers with no deploy and no frame (that is
    what makes it usable from a test that is asserting about a refusal).
    """
    assert Env().recorded_auths == ()
    assert Env(auths=[Address(ACCOUNT)]).recorded_auths == ()


def test_the_inspection_surfaces_are_not_in_the_authoring_namespace() -> None:
    """Dossier C.1 point 5: a contract must not be able to name these."""
    import serpent

    assert "published_events" not in serpent.__all__
    assert "recorded_auths" not in serpent.__all__
    assert "tag_of_chain_value" not in serpent.__all__
    assert "tag_of_chain_value" not in env_module.__all__
    assert "DEFAULT_LEDGER_TIMESTAMP" not in env_module.__all__
