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

import pytest

# Submodule imports: the public serpent/__init__.py is assembled in Task 10.
from serpent import decorators as decorators_module
from serpent import env as env_module
from serpent.decorators import (
    NAME_LIMIT,
    contract,
    contracterror,
    contractevent,
    contracttype,
    errorcode,
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
from serpent.types import U32, U64, Address, Bool, Map, String, Symbol, Vec


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


def test_contractevent_publishes_under_sub_plan_e() -> None:
    assert _meta(Bumped) == {"kind": "event", "fields": [("count", U32)]}
    event = Bumped(count=U32(1))
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        event.publish(Env())


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


def test_env_surface_is_complete_and_defers_to_sub_plan_e() -> None:
    env = Env()
    for call in (env.storage, env.ledger, env.events):
        with pytest.raises(NotImplementedError, match="sub-plan E"):
            call()
    key = Symbol("K")
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        InstanceStorage().extend_ttl(U32(1), U32(2))
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        PersistentStorage().extend_ttl(key, U32(1), U32(2))
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        TemporaryStorage().extend_ttl(key, U32(1), U32(2))
    bucket = InstanceStorage()
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        bucket.get(key, U32)
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        bucket.set(key, U32(1))
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        bucket.has(key)
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        bucket.del_(key)
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        Storage().instance()
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        Ledger().sequence()
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        Events().publish((Symbol("e"),), U32(1))


# --------------------------------------------------------------------------
# Fix round 1
# --------------------------------------------------------------------------


def test_storage_keys_accept_the_whole_chain_value_surface() -> None:
    """(a) Keys are any chain value or `@contracttype` struct.

    The static half of this is the `credit` method on `Example` above (a
    struct key) plus `_key_surface_probe` below; here we only pin that the
    widened signatures still reach the sub-plan E stub for every key shape.
    """
    bucket = PersistentStorage()
    address = Address("GA7QYNF7SOWQ3GLR2BGMZEHXAVIRZA4KVWLTJJFC7MGXUA74P7UJVSGZ")
    keys: list[ChainValue] = [
        Symbol("SYM"),
        U32(1),
        address,
        BalanceKey(owner=address),
        Vec(Symbol, [Symbol("a")]),
        Map(Symbol, U32),
    ]
    for key in keys:
        with pytest.raises(NotImplementedError, match="sub-plan E"):
            bucket.get(key, U32)
        with pytest.raises(NotImplementedError, match="sub-plan E"):
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


def _event_surface_probe(env: Env, address: Address) -> None:
    """Also compiled by `mypy --strict`, never called."""
    # (b) `publish` is statically visible because it is inherited from `Event`.
    Bumped(count=U32(1)).publish(env)
    # (c) topics are heterogeneous; a bare Python value is still rejected.
    env.events().publish((Symbol("transfer"), address, address), U32(1))
    env.events().publish(("transfer",), U32(1))  # type: ignore[arg-type]


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
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        Events().publish(topics, U32(1))


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
