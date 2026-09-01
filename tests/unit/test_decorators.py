"""The decorators' runtime behavior AND the zero-plugin `mypy --strict` gate.

This file is itself the static proof: `uv run mypy --strict` covers `tests`,
so the authoring forms exercised below (`raise Error.LimitExceeded`, kwargs
construction of a `@contracttype`, storage chains) must type-check with no
plugin. The negative `@contract` cases are deliberately ill-typed Python --
they carry narrow `type: ignore` codes naming the exact static error the
decorator also catches at runtime.
"""

import dataclasses
import pathlib
import typing
from typing import Annotated, cast

import pytest

# Submodule imports: the public serpent/__init__.py is assembled in Task 10.
from serpent import decorators as decorators_module
from serpent import env as env_module
from serpent.decorators import (
    NAME_LIMIT,
    contract,
    contractenum,
    contracterror,
    contractevent,
    contracttype,
    contractunion,
    errorcode,
    topic,
)
from serpent.env import (
    ChainValue,
    Env,
    Event,
    Events,
    InstanceStorage,
    Ledger,
    PersistentStorage,
    Storage,
    TemporaryStorage,
)
from serpent.errors import RESERVED_CODE_MIN, ContractError
from serpent.types import (
    U32,
    U64,
    Address,
    Bool,
    ContractEnum,
    ContractUnion,
    Map,
    String,
    Symbol,
    Vec,
    enumvalue,
    variant,
)
from serpent.types._udt import _VariantSpec
from tests.unit.conftest import deployed_env


def test_contracterror_members_are_exception_classes() -> None:
    @contracterror
    class Error:
        LimitExceeded = errorcode(7)
        Unauthorized = errorcode(2)

    assert issubclass(Error.LimitExceeded, ContractError)
    assert Error.LimitExceeded.code == 7
    with pytest.raises(ContractError) as exc_info:
        raise Error.LimitExceeded
    assert exc_info.value.code == 7


def test_contracterror_rejects_bare_int_reserved_and_duplicate() -> None:
    with pytest.raises(ValueError, match="errorcode"):

        @contracterror
        class Bare:
            X = 1  # bare int: must instruct errorcode(...)

    with pytest.raises(ValueError):

        @contracterror
        class Reserved:
            X = errorcode(0xFFFF_FFFF)

    with pytest.raises(ValueError):

        @contracterror
        class Dup:
            X = errorcode(1)
            Y = errorcode(1)


def test_contracttype_kwargs_and_field_validation() -> None:
    @contracttype
    class Settings:
        counter_limit: U32
        display_name: String

    s = Settings(counter_limit=U32(3), display_name=String("hi"))
    assert s.counter_limit == U32(3)
    with pytest.raises(ValueError):

        @contracttype
        class Bad:
            this_field_name_is_way_over_thirty: U32


def test_contract_requires_self_and_annotations() -> None:
    with pytest.raises(ValueError):

        @contract
        class C1:
            def f(env: Env) -> None: ...  # type: ignore[misc]  # no self

    with pytest.raises(ValueError):

        @contract
        class C2:
            # unannotated param (self exempt)
            def f(self, env) -> None: ...  # type: ignore[no-untyped-def]

    with pytest.raises(ValueError):

        @contract
        class C3:
            # constructor must return None
            def __init__(self, env: Env) -> U32: ...  # type: ignore[misc, empty-body]


# --------------------------------------------------------------------------
# The static gate, as real authoring code. Everything below is checked by the
# file-wide `uv run mypy --strict` run with no plugin: the `raise`, the kwargs
# construction, the storage chain and its `type[T]` decoding, the ledger and
# event calls.
# --------------------------------------------------------------------------


@contracterror
class TokenError:
    LimitExceeded = errorcode(7)
    Unauthorized = errorcode(2)


@contracttype
class Settings:
    counter_limit: U32
    display_name: String


@contracttype
class Wrapper:
    settings: Settings
    tags: Vec[Symbol]
    balances: Map[Symbol, U32]
    owner: Address | None


@contracttype
class BalanceKey:
    owner: Address


@contractevent
class Bumped(Event):
    count: U32


@contract
class Example:
    """A whole contract in the amended spec-2 authoring style."""

    def __init__(self, env: Env, counter_limit: U32) -> None:
        settings = Settings(counter_limit=counter_limit, display_name=String("hi"))
        env.storage().instance().set(Symbol("SETTINGS"), settings)

    def bump(self, env: Env) -> U32:
        """Increment a persistent counter; raise above the configured limit."""
        settings = env.storage().instance().get(Symbol("SETTINGS"), Settings)
        count = env.storage().persistent().get(Symbol("COUNT"), U32, default=U32(0))
        count = count + U32(1)
        if count > settings.counter_limit:
            raise TokenError.LimitExceeded
        env.storage().persistent().set(Symbol("COUNT"), count)
        env.events().publish((Symbol("bump"),), count)
        return count

    def credit(self, env: Env, owner: Address, amount: U32) -> None:
        """Keyed on a struct, not a Symbol: the widened key surface."""
        key = BalanceKey(owner=owner)
        balance = env.storage().persistent().get(key, U32, default=U32(0))
        env.storage().persistent().set(key, balance + amount)
        env.storage().persistent().extend_ttl(key, U32(100), U32(1000))
        # The canonical Soroban topic shape: (Symbol, Address, ...).
        env.events().publish((Symbol("credit"), owner), amount)

    def configured(self, env: Env) -> Bool:
        return env.storage().instance().has(Symbol("SETTINGS"))

    def now(self, env: Env) -> U64:
        return env.ledger().timestamp()

    # Single-underscore privates are not exported and are not checked.
    def _helper(self, whatever) -> None:  # type: ignore[no-untyped-def]
        ...


@contract
class Contract:
    """M1-E2 Task 5's seam pin (E10): `_check_method` reads with
    `include_extras=True` now, exactly like `_build_record` always has, so an
    `Annotated[...]` wrapper that carries something OTHER than `topic` must
    still come out STRIPPED -- only the marker itself is meaningful here."""

    def go(self, env: Env, x: Annotated[U32, "not a topic"]) -> U32:
        return x


def _meta(cls: type[object]) -> dict[str, object]:
    """Read `_serpent_type_` without a static attribute error: the decorators
    install it at runtime, which is exactly what a checker cannot see."""
    metadata: dict[str, object] = vars(cls)["_serpent_type_"]
    return metadata


#: A module-level singleton so the default below is not a call (ruff B008);
#: what matters to the test is only that the parameter HAS a default.
_ZERO = U32(0)


def _by_name(value: object) -> object:
    """Metadata with every type object replaced by its bare name."""
    if isinstance(value, type):
        return value.__name__
    if isinstance(value, list):
        return [_by_name(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_by_name(item) for item in value)
    if isinstance(value, dict):
        return {key: _by_name(item) for key, item in value.items()}
    return value


def test_generated_error_classes_are_named_and_distinct() -> None:
    assert TokenError.LimitExceeded.__name__ == "LimitExceeded"
    assert TokenError.LimitExceeded.__qualname__.endswith("TokenError.LimitExceeded")
    assert TokenError.LimitExceeded.__module__ == __name__
    assert TokenError.LimitExceeded is not TokenError.Unauthorized
    assert TokenError.Unauthorized.code == 2
    assert isinstance(TokenError.LimitExceeded(), ContractError)


def test_errorcode_rejects_non_int() -> None:
    with pytest.raises(TypeError):
        errorcode("7")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        errorcode(True)


def test_contracterror_code_range_boundaries() -> None:
    @contracterror
    class Edges:
        Lowest = errorcode(0)
        Highest = errorcode(RESERVED_CODE_MIN - 1)

    assert Edges.Lowest.code == 0
    assert Edges.Highest.code == RESERVED_CODE_MIN - 1

    with pytest.raises(ValueError, match="out of range"):

        @contracterror
        class JustReserved:
            X = errorcode(RESERVED_CODE_MIN)

    with pytest.raises(ValueError, match="out of range"):

        @contracterror
        class Negative:
            X = errorcode(-1)


def test_contracterror_metadata_and_no_partial_mutation() -> None:
    assert _meta(TokenError) == {
        "kind": "error_enum",
        "cases": [("LimitExceeded", 7), ("Unauthorized", 2)],
    }

    class Broken:
        Good = errorcode(1)
        AlsoOne = errorcode(1)

    with pytest.raises(ValueError, match="unique"):
        contracterror(Broken)

    # The valid first member was never replaced: the whole enum is validated
    # before any attribute is installed, so a rejected class is left untouched.
    assert not isinstance(vars(Broken)["Good"], type)


def test_contracttype_is_frozen_and_compares_by_value() -> None:
    a = Settings(counter_limit=U32(3), display_name=String("hi"))
    b = Settings(counter_limit=U32(3), display_name=String("hi"))
    assert a == b
    assert [f.name for f in dataclasses.fields(a)] == ["counter_limit", "display_name"]
    # The 3.11 floor in action: `dataclass_transform(frozen_default=True)` is
    # 3.12+, so mypy does NOT flag the line below (it needs no type: ignore --
    # adding one is reported as unused). The runtime is genuinely frozen.
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.counter_limit = U32(4)


def test_contracttype_accepts_containers_optionals_and_nesting() -> None:
    metadata = _meta(Wrapper)
    assert metadata["kind"] == "struct"
    fields = metadata["fields"]
    assert isinstance(fields, list)
    assert [name for name, _ in fields] == ["settings", "tags", "balances", "owner"]


def test_contracttype_rejects_non_chain_annotation() -> None:
    with pytest.raises(ValueError, match="not a"):

        @contracttype
        class PlainInt:
            count: int


def test_contracttype_name_rules() -> None:
    @contracttype
    class AtTheLimit:
        aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa: U32  # exactly 30 characters

    assert len(dataclasses.fields(AtTheLimit)[0].name) == NAME_LIMIT

    with pytest.raises(ValueError, match="capped at 30"):

        @contracttype
        class OneOver:
            aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa: U32  # 31 characters

    with pytest.raises(ValueError, match="valid Symbols"):

        @contracttype
        class NotASymbol:
            café: U32  # a legal Python identifier, not a legal Symbol


def test_contracttype_reports_unresolvable_annotation() -> None:
    """A forward reference to a function-local class cannot be resolved at
    decoration time; the failure names the class instead of leaking NameError."""

    @contracttype
    class Inner:
        value: U32

    with pytest.raises(ValueError, match="cannot resolve"):

        @contracttype
        class Outer:
            inner: "Inner"


def test_contractevent_records_the_convention_publish_reads() -> None:
    """The metadata shape Task 6's desugar and `Event.publish` both read back.

    `publish` is now implemented (M1-E Task 6), so the tier-1 half is exercised
    in `test_env_model.py`; what this pins is that the metadata carries every
    piece of the convention -- and that an UNDEPLOYED `Env` still refuses the
    publish for the ordinary frame reason, not for a missing implementation.
    """
    assert _meta(Bumped) == {
        "kind": "event",
        "fields": [("count", U32)],
        "locations": {"count": "data"},
        "prefix_topics": ("bumped",),
        "data_format": "map",
    }
    event = Bumped(count=U32(1))
    with pytest.raises(RuntimeError, match="before the contract was deployed"):
        event.publish(Env())


# --- the topic convention (M1-E Task 5) -------------------------------------


def test_the_topic_marker_is_a_named_sentinel_not_a_bare_object() -> None:
    """A bare `object()` would render as `<object object at 0x...>` in every
    error message and repr an author ever sees."""
    assert repr(topic) == "topic"
    assert type(topic) is not object


def test_a_marked_field_is_a_topic_and_the_annotation_is_stripped() -> None:
    """The one seam: `_build_record` reads hints with `include_extras=True`,
    records the marker, and stores the STRIPPED annotation -- so every
    downstream consumer (`typemap`, the compiler's `resolve_annotation`) sees a
    plain chain type and needs no edit."""

    @contractevent
    class Sent(Event):
        frm: Annotated[Address, topic]
        to: Annotated[Address, topic]
        amount: U32

    metadata = _meta(Sent)
    assert metadata["fields"] == [("frm", Address), ("to", Address), ("amount", U32)]
    # Identity, not equality: an `Annotated[...]` alias compares equal to
    # nothing here, but a leak would show up as a non-`type` object.
    fields = metadata["fields"]
    assert isinstance(fields, list)
    assert all(isinstance(annotation, type) for _name, annotation in fields)
    assert metadata["locations"] == {"frm": "topic", "to": "topic", "amount": "data"}
    assert metadata["prefix_topics"] == ("sent",)
    assert metadata["data_format"] == "map"


def test_an_annotated_field_without_the_marker_is_still_stripped() -> None:
    """`Annotated` is a general-purpose seam; only serpent's own marker means
    anything to the event convention."""

    @contractevent
    class Documented(Event):
        count: Annotated[U32, "not serpent's marker"]

    assert _meta(Documented)["fields"] == [("count", U32)]
    assert _meta(Documented)["locations"] == {"count": "data"}

    @contracttype
    class Struct:
        count: Annotated[U32, "not serpent's marker"]

    assert _meta(Struct) == {"kind": "struct", "fields": [("count", U32)]}


def test_the_default_prefix_topic_is_the_snake_case_class_name() -> None:
    """The three vectors the algorithm has to get right: a plain CamelCase
    name, an acronym run in the middle, and an acronym run at the front."""

    @contractevent
    class MyEvent(Event):
        amount: U32

    @contractevent
    class MyHTTPEvent(Event):
        amount: U32

    @contractevent
    class HTTPEvent(Event):
        amount: U32

    assert _meta(MyEvent)["prefix_topics"] == ("my_event",)
    assert _meta(MyHTTPEvent)["prefix_topics"] == ("my_http_event",)
    assert _meta(HTTPEvent)["prefix_topics"] == ("http_event",)


def test_explicit_topics_and_data_format_are_recorded() -> None:
    @contractevent(topics=("token", "transfer"), data_format="vec")
    class Renamed(Event):
        amount: U32

    metadata = _meta(Renamed)
    assert metadata["prefix_topics"] == ("token", "transfer")
    assert metadata["data_format"] == "vec"
    # Still a frozen dataclass, and still kwargs-constructible under
    # `mypy --strict` (`dataclass_transform` survives the factory form).
    assert Renamed(amount=U32(1)) == Renamed(amount=U32(1))


def test_a_prefix_topic_longer_than_nine_characters_is_legal() -> None:
    """`fits_symbol_small` (<=9) is the WRONG cap here: a longer prefix topic is
    a perfectly valid Symbol that pools via linear memory at the publish site
    (S19)."""

    @contractevent(topics=("transfer_from",))
    class Long(Event):
        amount: U32

    assert _meta(Long)["prefix_topics"] == ("transfer_from",)


def test_no_prefix_topics_at_all_is_legal() -> None:
    """Deliberate (review M3): an event whose topic list is exactly its
    `Annotated[T, topic]` fields is an accurate spec, not a lie.

    The first marked field carries `topics[0]`'s job, so it is a `Symbol` --
    see `test_a_prefixless_event_needs_a_symbol_first_topic_field`.
    """

    @contractevent(topics=())
    class Prefixless(Event):
        kind: Annotated[Symbol, topic]
        who: Annotated[Address, topic]
        amount: U32

    assert _meta(Prefixless)["prefix_topics"] == ()
    assert _meta(Prefixless)["locations"] == {"kind": "topic", "who": "topic", "amount": "data"}


def test_a_prefixless_event_needs_a_symbol_first_topic_field() -> None:
    """Task 6 review I1: the `topics[0]`-names-the-event convention (S10/S11),
    held at the declaration.

    With `topics=()` the first MARKED FIELD is `topics[0]`, so an
    `Annotated[Address, topic]` first field would publish an Address where every
    indexer and RPC filter expects a Symbol naming the event. The canonical
    spelling refuses exactly that at compile time (`SPT3019`), so refusing the
    declaration keeps both spellings -- and the spec entry -- telling one story.
    """
    with pytest.raises(ValueError, match="must therefore be a Symbol") as exc_info:

        @contractevent(topics=())
        class AddressFirst(Event):
            who: Annotated[Address, topic]
            amount: U32

    message = str(exc_info.value)
    assert "AddressFirst.who" in message
    # The remedy is named, spelled the way the author would write it.
    assert "topics=('address_first',)" in message


def test_a_bare_string_of_topics_is_refused_not_exploded() -> None:
    """`str` IS a `Sequence[str]`, so mypy accepts `topics="transfer"` with no
    complaint (this line carries no `type: ignore`, and `--strict` would flag an
    unused one) and `tuple()` would make it eight one-letter topics. Reported as
    the missing comma it is, not as "at most 2 prefix topics (got 8)"."""
    with pytest.raises(ValueError, match="not one string") as exc_info:

        @contractevent(topics="transfer")
        class Stringy(Event):
            amount: U32

    assert "topics=('transfer',)" in str(exc_info.value)


def test_an_over_long_DERIVED_prefix_topic_blames_the_class_name() -> None:
    """The author never wrote this topic, so the message must not read as if
    they did: it names the derivation and points at `topics=` (review M2)."""
    with pytest.raises(ValueError) as exc_info:

        @contractevent
        class ThisEventsClassNameIsThirtyThreeX(Event):  # 33 characters
            amount: U32

    message = str(exc_info.value)
    assert "derived from the class name" in message
    assert "topics=" in message
    assert "this_events_class_name_is_thirty_three_x" in message


def test_three_prefix_topics_are_refused_at_the_declaration_site() -> None:
    """The XDR caps `prefix_topics` at 2 (R5's negative control: a serpent error
    naming the class, never a bare `stellar_sdk` ValueError from deep inside an
    XDR constructor)."""
    with pytest.raises(ValueError, match="Three.*at most 2"):

        @contractevent(topics=("a", "b", "c"))
        class Three(Event):
            amount: U32


def test_a_prefix_topic_that_is_not_a_valid_symbol_is_refused() -> None:
    with pytest.raises(ValueError, match="valid Symbol"):

        @contractevent(topics=("not a symbol",))
        class Spaced(Event):
            amount: U32

    with pytest.raises(ValueError, match="valid Symbol"):

        @contractevent(topics=("t" * 33,))
        class TooLong(Event):
            amount: U32

    with pytest.raises(ValueError, match="valid Symbol"):

        @contractevent(topics=("",))
        class Empty(Event):
            amount: U32


def test_single_value_requires_exactly_one_non_topic_field() -> None:
    @contractevent(data_format="single-value")
    class Fine(Event):
        who: Annotated[Address, topic]
        amount: U32

    assert _meta(Fine)["data_format"] == "single-value"

    with pytest.raises(ValueError, match="single-value"):

        @contractevent(data_format="single-value")
        class Two(Event):
            amount: U32
            fee: U32

    with pytest.raises(ValueError, match="single-value"):

        @contractevent(data_format="single-value")
        class NoneAtAll(Event):
            who: Annotated[Address, topic]


def test_an_unknown_data_format_is_refused() -> None:
    with pytest.raises(ValueError, match="data_format"):

        @contractevent(data_format="tuple")
        class Odd(Event):
            amount: U32


# --- the three M1 shape restrictions (Task 6's desugar, ruling (a)) ----------


def test_vec_data_takes_uniformly_typed_fields_in_m1() -> None:
    """An M1 restriction, and a narrow one (Task 6, controller ruling (a)).

    `data_format="vec"` publishes the data fields as one `Vec`, and the IR node
    for a vector -- `MakeVec` -- carries exactly ONE `elem_ty`, because tier
    1's `Vec` is statically typed in its element class. A heterogeneous
    `Vec<Val>` has no node (`MakeTopics` is the heterogeneous vec, and it is
    topics-only by contract), so the declaration is refused HERE rather than
    compiling into a vector whose element type is a guess.
    """

    @contractevent(data_format="vec")
    class Uniform(Event):
        who: Annotated[Address, topic]
        first: U32
        second: U32

    assert _meta(Uniform)["data_format"] == "vec"

    with pytest.raises(ValueError, match="same type"):

        @contractevent(data_format="vec")
        class Mixed(Event):
            amount: U32
            memo: String


@pytest.mark.parametrize("data_format", ["map", "vec"])
def test_map_and_vec_data_need_at_least_one_data_field(data_format: str) -> None:
    """Every field a topic, with a container data format, has nothing to put in
    the container -- and neither `map_new_from_linear_memory` over an empty key
    array nor a `Vec` with no element type is a thing that exists. Refused at
    the declaration (R5) instead of at the publish site."""
    with pytest.raises(ValueError, match="at least one"):

        @contractevent(data_format=data_format)
        class AllTopics(Event):
            who: Annotated[Address, topic]


def test_an_event_with_no_topic_at_all_is_refused() -> None:
    """`topics=()` is legal (review M3) BECAUSE the marked fields carry the
    topic list. With neither, the published topic list would be empty -- which
    the tier-1 model refuses ("an event needs at least one topic, naming it")
    and which no indexer can filter on."""
    with pytest.raises(ValueError, match="at least one topic"):

        @contractevent(topics=())
        class Topicless(Event):
            amount: U32


def test_the_topic_marker_is_meaningless_on_a_struct_field() -> None:
    """Silently accepting it would let an author believe a struct field is a
    topic; nothing in the pipeline would ever read it."""
    with pytest.raises(ValueError, match="topic"):

        @contracttype
        class Marked:
            who: Annotated[Address, topic]


def test_the_topic_marker_must_wrap_the_WHOLE_field_annotation() -> None:
    """`Annotated[Address, topic] | None` hides the marker inside a union, where
    the strip would not find it -- refused rather than silently untopicked."""
    with pytest.raises(ValueError, match="whole"):

        @contractevent
        class Nested(Event):
            who: Annotated[Address, topic] | None


# --- the topic marker is refused on a contract method too (M1-E2 Task 5) ----
# Fed item X2, ruling E10: `topic` means something ONLY on a @contractevent
# field. Before this task a method parameter or return annotation carrying
# the marker compiled silently -- `_check_method` read hints with
# `include_extras=False` (the default), which let `get_type_hints` strip the
# marker before anything could see it, so the metadata recorded a plain type
# and no diagnostic ever fired. Both are now refused, symmetrically with the
# struct-field case above.


def test_the_topic_marker_is_refused_on_a_method_parameter() -> None:
    """Was: compiles silently, with the marker discarded, no diagnostic at
    all. `_check_method` now reads `include_extras=True` and runs the same
    `_split_topic` helper `_build_record` uses -- naming the method AND the
    parameter position in the message."""
    with pytest.raises(ValueError, match="parameter 'x' of a contract method has no topics"):

        @contract
        class C:
            def go(self, env: Env, x: Annotated[U32, topic]) -> U32:
                return x


def test_the_topic_marker_is_refused_on_a_method_return_type() -> None:
    """The second silent position -- probe-verified twice (§C.9 + review),
    since the M1-E triage that fed this task forward did not name it."""
    with pytest.raises(ValueError, match="return type of a contract method has no topics"):

        @contract
        class C:
            def go(self, env: Env) -> Annotated[U32, topic]:
                return U32(0)


def test_the_stored_annotation_is_still_stripped_everywhere() -> None:
    """D5 deliberately SHRANK the Annotated license to one seam
    (_build_record, decorators.py:344). _check_method (:669) is now a SECOND
    seam, named as such -- and the property D5 was protecting still holds:
    what flows into the metadata, and therefore into to_spec_type and
    resolve_annotation, is the STRIPPED annotation. Neither ever sees an
    Annotated (risk F.1.12)."""
    methods = _meta(Contract)["methods"]
    assert isinstance(methods, list)
    _name, params, returns = methods[0]
    assert params[1] == ("x", U32)
    assert returns is U32
    assert typing.get_origin(params[1][1]) is not typing.Annotated


def test_contract_metadata_lists_constructor_and_public_methods() -> None:
    metadata = _meta(Example)
    assert metadata["kind"] == "contract"
    methods = metadata["methods"]
    assert isinstance(methods, list)
    assert [name for name, _, _ in methods] == [
        "__init__",
        "bump",
        "credit",
        "configured",
        "now",
    ]
    name, params, returns = methods[1]
    assert name == "bump"
    assert params == [("env", Env)]
    assert returns is U32


def test_contract_rejects_unannotated_return_and_bad_names() -> None:
    with pytest.raises(ValueError, match="return type"):

        @contract
        class NoReturn:
            def f(self, env: Env):  # type: ignore[no-untyped-def]
                ...

    with pytest.raises(ValueError, match="capped at 30"):

        @contract
        class LongName:
            def aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa(self, env: Env) -> None: ...

    with pytest.raises(ValueError, match="staticmethod"):

        @contract
        class Static:
            @staticmethod
            def f(env: Env) -> None: ...


def test_contract_accepts_constructor_returning_none() -> None:
    @contract
    class Ok:
        def __init__(self, env: Env) -> None: ...

    assert _meta(Ok)["kind"] == "contract"


def test_env_surface_is_complete_and_backed_by_the_tier_1_model() -> None:
    """Every method on the surface now has a body, except the ones whose own
    task is still open.

    Rewritten (never deleted) from the assertion that every body raised
    `NotImplementedError`: the surface stays pinned, but by what it DOES.
    `tests/unit/test_env_model.py` is where the model's semantics are pinned;
    this test's job is coverage of the SHAPE -- every accessor, every bucket
    operation, once.
    """
    env = deployed_env()
    storage = env.storage()
    assert isinstance(storage, Storage)
    assert isinstance(env.ledger(), Ledger)
    assert isinstance(env.events(), Events)
    assert isinstance(storage.instance(), InstanceStorage)
    assert isinstance(storage.persistent(), PersistentStorage)
    assert isinstance(storage.temporary(), TemporaryStorage)

    key = Symbol("K")
    bucket = storage.instance()
    assert not bucket.has(key)
    bucket.set(key, U32(1))
    assert bucket.has(key)
    assert bucket.get(key, U32) == U32(1)
    bucket.del_(key)
    assert not bucket.has(key)
    assert bucket.get(key, U32, U32(9)) == U32(9)

    env.events().publish((Symbol("e"),), U32(1))
    assert env.published_events == (((Symbol("e"),), U32(1)),)
    assert isinstance(env.ledger().timestamp(), U64)
    assert isinstance(env.ledger().sequence(), U32)

    # The TTL model, which is PARTIAL by design -- no clamp and no trap, since
    # no maximum live-until ledger is reachable in M1. Shape only here (the
    # keyless instance form, the keyed forms); `tests/unit/test_env_ttl.py`
    # owns the algebra, the expiry and the two enumerated non-models.
    storage.instance().extend_ttl(U32(1), U32(2))
    storage.persistent().set(key, U32(1))
    storage.persistent().extend_ttl(key, U32(1), U32(2))
    storage.temporary().set(key, U32(1))
    storage.temporary().extend_ttl(key, U32(1), U32(2))


# --------------------------------------------------------------------------
# Fix round 1
# --------------------------------------------------------------------------


def test_storage_keys_accept_the_whole_chain_value_surface() -> None:
    """(a) Keys are any chain value or `@contracttype` struct.

    The static half of this is the `credit` method on `Example` above (a
    struct key) plus `_key_surface_probe` below; the runtime half used to pin
    that the widened signatures reached the sub-plan E stub, and now pins that
    every key shape ROUND TRIPS through the tier-1 model -- including through
    `extend_ttl`, which finds the entry a `set` of the same key wrote (a live
    entry, so the dead-entry error does not fire).
    """
    bucket = deployed_env().storage().persistent()
    address = Address("GA7QYNF7SOWQ3GLR2BGMZEHXAVIRZA4KVWLTJJFC7MGXUA74P7UJVSGZ")
    keys: list[ChainValue] = [
        Symbol("SYM"),
        U32(1),
        address,
        BalanceKey(owner=address),
        Vec(Symbol, [Symbol("a")]),
        Map(Symbol, U32),
    ]
    for index, key in enumerate(keys):
        bucket.set(key, U32(index))
    for index, key in enumerate(keys):
        assert bucket.get(key, U32) == U32(index)
        bucket.extend_ttl(key, U32(1), U32(2))


def _key_surface_probe(env: Env, address: Address) -> None:
    """Compiled by `mypy --strict`, never called: every one of these key
    shapes must be accepted statically."""
    bucket = env.storage().persistent()
    bucket.set(Symbol("SYM"), U32(1))
    bucket.set(address, U32(1))
    bucket.set(BalanceKey(owner=address), U32(1))
    bucket.del_(Vec(Symbol, [Symbol("a")]))
    bucket.has(Map(Symbol, U32))

    # ...and raw Python values must still be REJECTED. These ignores are the
    # pin: mypy runs with `warn_unused_ignores`, so if the key surface ever
    # widened to admit a bare `str`/`int`, the ignore would become unused and
    # the strict run would fail. That is the whole point of the closed union.
    bucket.set("SYM", U32(1))  # type: ignore[arg-type]
    bucket.set(1, U32(1))  # type: ignore[arg-type]
    bucket.get(b"raw", U32)  # type: ignore[arg-type]
    # ...and the same closed union applies to the value being written, not
    # just the key: a raw `str`/`int` value is also a static error.
    bucket.set(Symbol("SYM"), "raw string")  # type: ignore[arg-type]
    bucket.set(Symbol("SYM"), 1)  # type: ignore[arg-type]


def _event_surface_probe(env: Env, address: Address) -> None:
    """Also compiled by `mypy --strict`, never called."""
    # (b) `publish` is statically visible because it is inherited from `Event`.
    Bumped(count=U32(1)).publish(env)
    # (c) topics are heterogeneous; a bare Python value is still rejected.
    env.events().publish((Symbol("transfer"), address, address), U32(1))
    env.events().publish(("transfer",), U32(1))  # type: ignore[arg-type]
    # `data` is a `ChainValue` too, so a raw Python value is rejected there.
    env.events().publish((Symbol("transfer"), address, address), 42)  # type: ignore[arg-type]


def test_contractevent_requires_the_event_base() -> None:
    """(b) `publish` is inherited, so it must come from a real base class."""
    with pytest.raises(ValueError, match="must inherit `Event`"):

        @contractevent
        class NotAnEvent:
            count: U32

    assert issubclass(Bumped, Event)
    # Statically visible because it is inherited, not installed by setattr.
    assert "publish" not in vars(Bumped)
    assert Bumped(count=U32(1)).publish.__func__ is Event.publish  # type: ignore[attr-defined]


def test_event_topics_are_heterogeneous_tuples() -> None:
    """(c) The canonical shape is `(Symbol, Address, Address)`."""
    address = Address("GA7QYNF7SOWQ3GLR2BGMZEHXAVIRZA4KVWLTJJFC7MGXUA74P7UJVSGZ")
    topics: tuple[ChainValue, ...] = (Symbol("transfer"), address, address)
    env = deployed_env()
    env.events().publish(topics, U32(1))
    assert env.published_events == ((topics, U32(1)),)


def test_metadata_is_identical_with_and_without_pep_563() -> None:
    """(d) `get_type_hints` normalizes PEP 563 strings away."""
    from tests.unit import future_annotations_contract as with_future
    from tests.unit import no_future_annotations_contract as without_future

    for name in ("Settings", "Credited", "Contract"):
        # Compared by type *name*: each twin module has its own `Settings`
        # class object, so identity necessarily differs; everything the
        # metadata records about it must not.
        assert _by_name(_meta(getattr(with_future, name))) == _by_name(
            _meta(getattr(without_future, name))
        )

    methods = _meta(with_future.Contract)["methods"]
    assert isinstance(methods, list)
    # Real type objects, never strings -- the point of the normalization.
    for _method_name, params, returns in methods:
        for _param_name, annotation in params:
            assert isinstance(annotation, type)
        assert isinstance(returns, type)
    assert methods[1] == ("bump", [("env", Env), ("by", U32)], U32)
    assert methods[0][2] is type(None)


def test_only_structs_are_valid_field_annotations() -> None:
    """(e) Error enums, contracts and events are not values."""
    with pytest.raises(ValueError, match="not a"):

        @contracttype
        class WithErrorEnum:
            e: TokenError

    with pytest.raises(ValueError, match="not a"):

        @contracttype
        class WithContract:
            c: Example

    with pytest.raises(ValueError, match="not a"):

        @contracttype
        class WithEvent:
            ev: Bumped


def test_undecorated_subclass_of_a_struct_is_not_a_struct() -> None:
    """(e) `vars()`, not `getattr`: metadata must not leak through inheritance."""

    class Sneaky(Settings):
        pass

    assert Sneaky._serpent_type_ == Settings._serpent_type_  # type: ignore[attr-defined]
    assert "_serpent_type_" not in vars(Sneaky)
    with pytest.raises(ValueError, match="not a"):

        @contracttype
        class WithSneaky:
            s: Sneaky


def test_contract_rejects_varargs_kwargs_and_defaults() -> None:
    """(f) None of these are expressible in contractspecv0."""
    with pytest.raises(ValueError, match=r"\*rest"):

        @contract
        class Varargs:
            def f(self, env: Env, *rest: U32) -> None: ...

    with pytest.raises(ValueError, match=r"\*\*rest"):

        @contract
        class Kwargs:
            def f(self, env: Env, **rest: U32) -> None: ...

    with pytest.raises(ValueError, match="default value"):

        @contract
        class Defaulted:
            def f(self, env: Env, count: U32 = _ZERO) -> None: ...


def test_redecoration_and_empty_error_enum_are_serpent_errors() -> None:
    """(h) A serpent ValueError, not a raw dataclasses TypeError."""
    with pytest.raises(ValueError, match="already declared as a serpent struct"):
        contracttype(Settings)

    with pytest.raises(ValueError, match="at least one member"):

        @contracterror
        class Empty:
            """No members at all."""


def test_no_inert_noqa_directives() -> None:
    """(g) Pinned so a future edit cannot reintroduce a suppressed lint."""
    for module in (decorators_module, env_module):
        source = pathlib.Path(module.__file__ or "").read_text()
        assert "noqa" not in source


# --------------------------------------------------------------------------
# M1-E2: the DECLARATION layer for tagged unions and int enums
#
# The value layer (`tests/unit/test_udt_values.py`) binds `variant()`
# placeholders by hand; everything below goes through the two decorators,
# which is the only spelling a contract author ever writes. Each declaration
# here is also part of the file-wide `mypy --strict` gate: a decorator is an
# identity function to a checker, so `Shape.Circle(U32(1))` type-checking is
# the descriptor surface's own doing (ruling E1).
# --------------------------------------------------------------------------


@contractunion
class Shape(ContractUnion):
    """The dossier's running example, declared the authored way."""

    Empty = variant()
    Circle = variant(U32)
    Rect = variant(U32, U32)


@contractenum
class Level(ContractEnum):
    """An int enum: explicit discriminants, always (ruling E5)."""

    Low = enumvalue(0)
    High = enumvalue(7)


@contracttype
class Boxed:
    """A struct holding one of each new kind -- `_is_contract_annotation`'s
    widening, which is additive: nothing that was legal became illegal."""

    shape: Shape
    level: Level


def test_contractunion_records_its_cases_in_declaration_order() -> None:
    """The metadata shape Task 3's spec entry reads: a case NAME plus its
    payload-annotation tuple, empty for a unit variant, in declaration order
    (B10) -- and a 2-tuple, like every other `cases` list in this module."""
    assert _meta(Shape) == {
        "kind": "union",
        "cases": [("Empty", ()), ("Circle", (U32,)), ("Rect", (U32, U32))],
    }


def test_contractenum_records_the_same_pair_shape_an_error_enum_does() -> None:
    """Deliberately identical to `@contracterror`'s `(name, value)` list, so
    `sections._enum_entry`'s template and the loader's case cross-check are
    reusable rather than re-derived."""
    assert _meta(Level) == {"kind": "enum", "cases": [("Low", 0), ("High", 7)]}
    error_cases = _meta(TokenError)["cases"]
    enum_cases = _meta(Level)["cases"]
    assert isinstance(error_cases, list) and isinstance(enum_cases, list)
    for (name, value), (other_name, other_value) in zip(error_cases, enum_cases, strict=True):
        assert isinstance(name, str) and isinstance(other_name, str)
        assert isinstance(value, int) and isinstance(other_value, int)


def test_a_declared_union_round_trips_every_arity_the_decorator_binds() -> None:
    """The decorator's real job: swap each placeholder for the descriptor that
    knows its case NAME (which the factory cannot see)."""
    assert Shape.Empty.tag() == Symbol("Empty")
    assert Shape.Circle(U32(3)).tag() == Symbol("Circle")
    assert Shape.Circle(U32(3)).payload(U32(0), U32) == U32(3)
    rect = Shape.Rect(U32(2), U32(5))
    assert rect.payload(U32(0), U32) == U32(2)
    assert rect.payload(U32(1), U32) == U32(5)
    # A member is bound to the class it was DECLARED in, so an accessed case
    # constructs that union and nothing else.
    assert isinstance(Shape.Empty, Shape)


def test_a_declared_int_enum_member_is_its_own_type() -> None:
    assert isinstance(Level.Low, Level)
    assert Level.Low != Level.High
    assert repr(Level.High) == "Level.High"


def test_a_union_payload_may_be_a_chain_type_a_struct_a_union_or_an_int_enum() -> None:
    """The one-place widening of `_is_contract_annotation` (SS B.3): a UDT
    reference is name-only, so admitting the three kinds costs nothing."""

    @contractunion
    class Nested(ContractUnion):
        Plain = variant(U32)
        Struct = variant(Settings)
        Union = variant(Shape)
        Enum = variant(Level)
        Container = variant(Vec[U32])

    assert [name for name, _payload in _cases(Nested)] == [
        "Plain",
        "Struct",
        "Union",
        "Enum",
        "Container",
    ]


def test_a_struct_field_may_now_hold_a_union_or_an_int_enum() -> None:
    """The same widening, seen from `@contracttype`: additive, and the reason
    it is one function rather than a second copy of the rule."""
    fields = _meta(Boxed)["fields"]
    assert fields == [("shape", Shape), ("level", Level)]


def test_a_union_payload_that_is_not_a_declared_type_bridges_to_the_field_code() -> None:
    """The EXISTING needle (`SPT4012`), deliberately: a payload annotation and
    a struct field annotation break the same rule, so no new code is spent."""
    with pytest.raises(ValueError, match="is not a chain type, a `@contracttype` struct"):

        @contractunion
        class BarePython(ContractUnion):
            Count = variant(int)

    with pytest.raises(ValueError, match="is not a chain type, a `@contracttype` struct"):

        @contractunion
        class WithErrorEnum(ContractUnion):
            Err = variant(TokenError)

    with pytest.raises(ValueError, match="is not a chain type, a `@contracttype` struct"):

        @contractunion
        class WithEvent(ContractUnion):
            Ev = variant(Bumped)


def test_an_empty_union_or_int_enum_declares_nothing_and_is_refused() -> None:
    """`@contracterror`'s empty-enum rule, for both new kinds: an empty
    declaration contributes nothing to the contract spec."""
    with pytest.raises(ValueError, match="declares at least one case"):

        @contractunion
        class NoVariants(ContractUnion):
            """Not one case."""

    with pytest.raises(ValueError, match="declares at least one case"):

        @contractenum
        class NoMembers(ContractEnum):
            """Not one case."""


def test_a_case_that_is_not_a_placeholder_is_refused_by_name() -> None:
    """`_reject_bare_member`'s rule for the two new kinds. The second spelling
    is the NAMED-FIELD variant a Rust author reaches for: `name: T = value`
    clears the loader's body-form check (an error enum needs that form), so
    the decorator is what refuses it."""
    with pytest.raises(ValueError, match="case is declared as"):

        @contractunion
        class BareValue(ContractUnion):
            Circle = 3

    with pytest.raises(ValueError, match="case is declared as"):

        @contractunion
        class NamedField(ContractUnion):
            radius: U32 = U32(0)

    with pytest.raises(ValueError, match="case is declared as"):

        @contractenum
        class BareInt(ContractEnum):
            Low = 0


def test_an_int_enum_discriminant_outside_the_u32_range_is_refused() -> None:
    """`errorcode`'s precedent (`decorators.py`'s range check): an int-enum
    member IS a bare `u32` on chain, so `enumvalue(-1)` would otherwise
    declare a spec entry no `u32` could ever hold."""
    with pytest.raises(ValueError, match="is out of range"):

        @contractenum
        class Negative(ContractEnum):
            Bad = enumvalue(-1)

    with pytest.raises(ValueError, match="is out of range"):

        @contractenum
        class TooBig(ContractEnum):
            Bad = enumvalue(U32.MAX + 1)

    @contractenum
    class Edges(ContractEnum):
        Lowest = enumvalue(U32.MIN)
        Highest = enumvalue(U32.MAX)

    assert _meta(Edges)["cases"] == [("Lowest", 0), ("Highest", U32.MAX)]


def test_a_duplicate_discriminant_is_refused_naming_the_first_member() -> None:
    with pytest.raises(ValueError, match="is already declared by"):

        @contractenum
        class Clashing(ContractEnum):
            First = enumvalue(1)
            Second = enumvalue(1)


def test_both_new_kinds_require_their_base_class() -> None:
    """`@contractevent`'s rule (D8/D9), for the two new kinds: §C.8 verified
    that a base-less class is not statically a `ChainValue` at any position,
    and a decorator cannot add a base a checker can see."""
    with pytest.raises(ValueError, match="class declares exactly one base"):

        @contractunion
        class NoBase:  # a plain class: not a ContractUnion at all
            Empty = variant()

    with pytest.raises(ValueError, match="class declares exactly one base"):

        @contractenum
        class AlsoNoBase:
            Low = enumvalue(0)


def test_subclassing_a_declared_union_or_int_enum_is_refused() -> None:
    """A loud refusal at the declaration, not a silent type/runtime split: a
    variant descriptor constructs the class it was DECLARED in, while
    `_EnumValue.__get__` honors the class it is ACCESSED through -- so
    `class Sub(Shape)` would type as `Sub` and build a `Shape`. Forbidding it
    settles the asymmetry, and keeps `ContractEnum.__repr__`'s
    `vars(type(self))`-only walk correct."""
    with pytest.raises(ValueError, match="class declares exactly one base"):

        @contractunion
        class SubUnion(Shape):
            Extra = variant()

    with pytest.raises(ValueError, match="class declares exactly one base"):

        @contractenum
        class SubEnum(Level):
            Extra = enumvalue(9)


def test_neither_new_kind_is_turned_into_a_dataclass() -> None:
    """Ruling E9, at the declaration layer: `types._ordering.Struct` matches
    `__dataclass_fields__` and is the FALLTHROUGH arm in three separate tag
    doors, so a dataclass union would classify silently as a `Map`."""
    for cls in (Shape, Level):
        assert not dataclasses.is_dataclass(cls)


def test_redecorating_a_union_or_an_int_enum_is_a_serpent_error() -> None:
    with pytest.raises(ValueError, match="already declared as a serpent union"):
        contractunion(Shape)
    with pytest.raises(ValueError, match="already declared as a serpent enum"):
        contractenum(Level)


def test_a_rejected_union_is_left_with_its_placeholders_untouched() -> None:
    """`@contracterror`'s no-partial-mutation property: the whole declaration
    is validated before a single descriptor is installed."""

    class Broken(ContractUnion):
        Good = variant(U32)
        Bad = variant(int)

    with pytest.raises(ValueError, match="is not a chain type"):
        contractunion(Broken)

    assert isinstance(vars(Broken)["Good"], _VariantSpec)


def test_the_decorator_does_not_check_case_NAMES_at_all() -> None:
    """B1, stated as a test: `_check_name` caps at `NAME_LIMIT` (30) and
    bridges to `SPT5001`, which would refuse the 40-character int-enum case
    name ruling E8 makes legal. The located compile-time refusal for a case
    name lives in `compiler/limits.py`, per kind (32 for a variant, 60 for an
    int-enum case) -- so the decorator must let both spellings through.
    """
    name = "L" * 40
    assert len(name) > NAME_LIMIT

    @contractenum
    class LongCases(ContractEnum):
        LLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLL = enumvalue(1)  # 40 characters, E8

    @contractunion
    class LongVariants(ContractUnion):
        LLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLL = variant()  # 40 characters, E8

    assert [case for case, _value in _cases(LongCases)] == [name]
    assert [case for case, _payload in _cases(LongVariants)] == [name]


def _cases(cls: type[object]) -> list[tuple[str, object]]:
    """The `cases` list of a declared union/int enum, typed for the checker."""
    return cast("list[tuple[str, object]]", _meta(cls)["cases"])
