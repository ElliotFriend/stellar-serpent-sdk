"""The decorators' runtime behavior AND the zero-plugin `mypy --strict` gate.

This file is itself the static proof: `uv run mypy --strict` covers `tests`,
so the authoring forms exercised below (`raise Error.LimitExceeded`, kwargs
construction of a `@contracttype`, storage chains) must type-check with no
plugin. The negative `@contract` cases are deliberately ill-typed Python --
they carry narrow `type: ignore` codes naming the exact static error the
decorator also catches at runtime.
"""

import dataclasses

import pytest

# Submodule imports: the public serpent/__init__.py is assembled in Task 10.
from serpent.decorators import (
    NAME_LIMIT,
    contract,
    contracterror,
    contractevent,
    contracttype,
    errorcode,
)
from serpent.env import (
    Env,
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


@contractevent
class Bumped:
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
        env.events().publish(Vec(Symbol, [Symbol("bump")]), count)
        return count

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
        vars(type(event))["publish"](event, Env())


def test_contract_metadata_lists_constructor_and_public_methods() -> None:
    metadata = _meta(Example)
    assert metadata["kind"] == "contract"
    methods = metadata["methods"]
    assert isinstance(methods, list)
    assert [name for name, _, _ in methods] == [
        "__init__",
        "bump",
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
        Events().publish(Vec(Symbol), U32(1))
