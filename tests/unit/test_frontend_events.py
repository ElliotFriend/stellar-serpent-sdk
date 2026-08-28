"""M1-E Task 6: the `Event.publish(env)` desugar (dossier §E2, ruling E2).

`<Event instance>.publish(env)` was `SPT1032` ("deferred to sub-plan E") for
the whole of M1-C. Task 5 gave `@contractevent` the topic convention its
metadata was missing, and this file pins what the frontend now does with it:
the authoring form LOWERS to the exact `HostCall("contract_event", (MakeTopics
(...), <data>))` the canonical `env.events().publish(topics, data)` spelling
already produced, which is why the IR node inventory and the whole emitter are
untouched by this feature.

**The headline assertion is `test_both_spellings_produce_the_same_hostcall`**:
two contracts whose only difference is the spelling compile to
STRUCTURALLY EQUAL `HostCall` trees. That equality is the evidence for the
"emitter unchanged" claim -- everything else here is the convention's
translation table (prefix topics first, then topic-marked fields in
DECLARATION order; data per `data_format`).
"""

from __future__ import annotations

import textwrap
from typing import Any

import pytest

from serpent.compiler import codes
from serpent.compiler.diagnostics import CompileError
from serpent.compiler.frontend import CompiledModule, compile_module
from serpent.compiler.ir import (
    Const,
    Eval,
    FuncIR,
    HostCall,
    IRExpr,
    MakeStruct,
    MakeTopics,
    MakeVec,
    ParamRef,
)
from serpent.compiler.recognize import RECOGNIZED, SurfaceKind
from serpent.compiler.types_ import Ty

PATH = "contracts/events.py"


def _compile(source: str) -> CompiledModule:
    return compile_module(textwrap.dedent(source).lstrip(), PATH)


def _reject(source: str, code: str) -> CompileError:
    with pytest.raises(CompileError) as info:
        _compile(source)
    found = [d.code for d in info.value.diagnostics]
    assert code in found, found
    return info.value


def _method(compiled: CompiledModule, name: str) -> FuncIR:
    (found,) = [fn for fn in compiled.functions if fn.py_name == name]
    return found


def _published(compiled: CompiledModule, name: str = "go") -> HostCall:
    """The one `contract_event` `HostCall` a method's body publishes."""
    calls = [
        stmt.value
        for stmt in _method(compiled, name).body
        if isinstance(stmt, Eval)
        and isinstance(stmt.value, HostCall)
        and stmt.value.fn_name == "contract_event"
    ]
    assert len(calls) == 1, calls
    return calls[0]


def _topics(call: HostCall) -> tuple[IRExpr, ...]:
    topics, _data = call.args
    assert isinstance(topics, MakeTopics)
    return topics.topics


def _data(call: HostCall) -> IRExpr:
    _topics_arg, data = call.args
    return data


#: The convention's worked example, in the dossier §C.2 shape: two marked
#: topic fields, one data field, an explicit single-character prefix topic.
_TRANSFER = """
from serpent import Address, Annotated, Env, Event, U32, contract, contractevent, topic


@contractevent(topics=("transfer",), data_format="single-value")
class Transfer(Event):
    from_: Annotated[Address, topic]
    to: Annotated[Address, topic]
    amount: U32


@contract
class C:
    def go(self, env: Env, frm: Address, to: Address, amount: U32) -> None:
        Transfer(from_=frm, to=to, amount=amount).publish(env)
"""

#: The same event, published the canonical way. `topics` and `data` are spelled
#: to match the convention's own output exactly, which is what makes the
#: equivalence assertion meaningful rather than a tautology.
_TRANSFER_CANONICAL = """
from serpent import Address, Env, Symbol, U32, contract


@contract
class C:
    def go(self, env: Env, frm: Address, to: Address, amount: U32) -> None:
        env.events().publish((Symbol("transfer"), frm, to), amount)
"""


# --- the recognition row -----------------------------------------------------


def test_the_recognized_row_is_a_lowering_row_now() -> None:
    """`event.publish` reaches `contract_event`; nothing is a REJECT row."""
    spec = RECOGNIZED["event.publish"]
    assert spec.kind is SurfaceKind.HOST_CALL
    assert spec.host_fns == ("contract_event",)
    assert spec.reject_code is None
    assert not [key for key, row in RECOGNIZED.items() if row.kind is SurfaceKind.REJECT]


def test_spt1032_is_retired_to_the_allowlist() -> None:
    """D9's discipline: the row survives, un-renumbered, and joins the
    no-fixture allowlist with a reason -- it is not deleted and its meaning is
    not reversed."""
    assert "SPT1032" in codes.CODES
    assert "SPT1032" in codes.NO_FIXTURE_ALLOWLIST
    assert "sub-plan E" in codes.NO_FIXTURE_REASONS["SPT1032"]
    (entry,) = [row for row in codes.REGISTRY if row.code == "SPT1032"]
    assert "superseded" in entry.message_intent


# --- the equivalence that keeps the emitter unchanged ------------------------


def test_both_spellings_produce_the_same_hostcall() -> None:
    """THE test: the desugar's `HostCall` tree equals the canonical one's.

    Compared field by field rather than with `==` on the nodes, because every
    IR node carries a `Loc` and the two sources are different files -- the
    claim is about the SHAPE the emitter sees (fn_name, the `MakeTopics`
    contents, the data expression), not about the source coordinates.
    """
    desugared = _published(_compile(_TRANSFER))
    canonical = _published(_compile(_TRANSFER_CANONICAL))

    assert desugared.fn_name == canonical.fn_name == "contract_event"
    assert desugared.ty == canonical.ty == Ty.Void
    assert _shape(desugared) == _shape(canonical)


def _shape(node: IRExpr) -> Any:
    """A location-free, class-and-payload rendering of one IR expression."""
    if isinstance(node, HostCall):
        return ("HostCall", node.fn_name, node.ty, tuple(_shape(a) for a in node.args))
    if isinstance(node, MakeTopics):
        return ("MakeTopics", node.ty, tuple(_shape(t) for t in node.topics))
    if isinstance(node, MakeStruct):
        return (
            "MakeStruct",
            node.ty,
            node.struct_name,
            tuple((name, _shape(value)) for name, value in node.fields),
        )
    if isinstance(node, MakeVec):
        return (
            "MakeVec",
            node.ty,
            node.elem_ty,
            node.all_static,
            tuple(_shape(i) for i in node.items),
        )
    if isinstance(node, Const):
        return ("Const", node.ty, node.py_value)
    if isinstance(node, ParamRef):
        return ("ParamRef", node.ty, node.index, node.name)
    return (type(node).__name__, node.ty)


# --- the topic list ----------------------------------------------------------


def test_topics_are_the_prefix_then_the_marked_fields_in_declaration_order() -> None:
    call = _published(_compile(_TRANSFER))
    prefix, first, second = _topics(call)
    assert isinstance(prefix, Const)
    assert prefix.ty == Ty.Symbol
    assert prefix.py_value == "transfer"
    assert isinstance(first, ParamRef) and first.name == "frm"
    assert isinstance(second, ParamRef) and second.name == "to"


def test_the_default_prefix_topic_is_the_snake_cased_class_name() -> None:
    compiled = _compile(
        """
        from serpent import Env, Event, U32, contract, contractevent


        @contractevent
        class Bumped(Event):
            count: U32


        @contract
        class C:
            def go(self, env: Env, n: U32) -> None:
                Bumped(count=n).publish(env)
        """
    )
    (prefix,) = _topics(_published(compiled))
    assert isinstance(prefix, Const) and prefix.py_value == "bumped"


def test_two_prefix_topics_are_published_in_order() -> None:
    compiled = _compile(
        """
        from serpent import Env, Event, U32, contract, contractevent


        @contractevent(topics=("token", "mint"))
        class Minted(Event):
            amount: U32


        @contract
        class C:
            def go(self, env: Env, n: U32) -> None:
                Minted(amount=n).publish(env)
        """
    )
    first, second = _topics(_published(compiled))
    assert isinstance(first, Const) and first.py_value == "token"
    assert isinstance(second, Const) and second.py_value == "mint"


def test_an_empty_prefix_publishes_only_the_marked_fields() -> None:
    """`topics=()` is deliberately legal (Task 5, review M3): the topic list is
    then exactly the marked fields."""
    compiled = _compile(
        """
        from serpent import Address, Annotated, Env, Event, U32, contract, contractevent, topic


        @contractevent(topics=())
        class Ping(Event):
            who: Annotated[Address, topic]
            n: U32


        @contract
        class C:
            def go(self, env: Env, who: Address, n: U32) -> None:
                Ping(who=who, n=n).publish(env)
        """
    )
    (only,) = _topics(_published(compiled))
    assert isinstance(only, ParamRef) and only.name == "who"


def test_a_long_prefix_topic_pools_through_linear_memory() -> None:
    """A prefix topic past the 9-character SymbolSmall bound is LEGAL (Task 5)
    and flows into the literal inventory and the host-function accounting
    through the ordinary `Const` walk -- no special case in the desugar."""
    compiled = _compile(
        """
        from serpent import Env, Event, U32, contract, contractevent


        @contractevent
        class TransferCompleted(Event):
            amount: U32


        @contract
        class C:
            def go(self, env: Env, n: U32) -> None:
                TransferCompleted(amount=n).publish(env)
        """
    )
    (prefix,) = _topics(_published(compiled))
    assert isinstance(prefix, Const) and prefix.py_value == "transfer_completed"
    assert compiled.literals.symbols_over_9 == ("transfer_completed",)
    assert "symbol_new_from_linear_memory" in compiled.host_fns_used
    assert compiled.needs_memory is True


# --- the data payload, per data_format ---------------------------------------


def test_single_value_data_is_the_lone_data_field_expression() -> None:
    data = _data(_published(_compile(_TRANSFER)))
    assert isinstance(data, ParamRef)
    assert data.name == "amount"
    assert data.ty == Ty.U32


def test_map_data_is_a_makestruct_with_p7_sorted_fields() -> None:
    """The `"map"` format is byte-for-byte a struct's own lowering (M1-D C9):
    compile-time sorted `Symbol` keys, runtime values. `MakeStruct` is the node
    that does that, so the desugar reuses it -- which is also what feeds
    `struct_key_descriptor_sets` and `needs_memory` for free.
    """
    compiled = _compile(
        """
        from serpent import Address, Annotated, Env, Event, String, U32, contract
        from serpent import contractevent, topic


        @contractevent
        class Traded(Event):
            who: Annotated[Address, topic]
            amount: U32
            memo: String


        @contract
        class C:
            def go(self, env: Env, who: Address, amount: U32, memo: String) -> None:
                Traded(who=who, amount=amount, memo=memo).publish(env)
        """
    )
    data = _data(_published(compiled))
    assert isinstance(data, MakeStruct)
    assert data.struct_name == "Traded"
    assert [name for name, _value in data.fields] == ["amount", "memo"]
    assert [type(value).__name__ for _name, value in data.fields] == ["ParamRef", "ParamRef"]
    assert ("amount", "memo") in compiled.literals.struct_key_descriptor_sets
    assert "map_new_from_linear_memory" in compiled.host_fns_used
    assert compiled.needs_memory is True


def test_vec_data_is_a_makevec_in_declaration_order() -> None:
    compiled = _compile(
        """
        from serpent import Annotated, Address, Env, Event, U32, contract, contractevent, topic


        @contractevent(data_format="vec")
        class Scored(Event):
            who: Annotated[Address, topic]
            first: U32
            second: U32


        @contract
        class C:
            def go(self, env: Env, who: Address, a: U32, b: U32) -> None:
                Scored(who=who, first=a, second=b).publish(env)
        """
    )
    data = _data(_published(compiled))
    assert isinstance(data, MakeVec)
    assert data.elem_ty == Ty.U32
    assert data.ty == Ty.Vec(Ty.U32)
    assert [item.name for item in data.items if isinstance(item, ParamRef)] == ["a", "b"]
    assert data.all_static is False


# --- the accounting the desugar must feed ------------------------------------


def test_the_desugar_puts_contract_event_in_host_fns_used() -> None:
    compiled = _compile(_TRANSFER)
    assert "contract_event" in compiled.host_fns_used
    assert "contract_event" in compiled.host_fns_reachable


def test_a_static_topic_tuple_still_reaches_the_linear_memory_vec_form() -> None:
    """`MakeTopics` has no `all_static` flag, so `needs_memory` asks its own
    question (every topic a `Const`). An all-constant topic list built by the
    desugar must answer it the same way the canonical spelling's does."""
    compiled = _compile(
        """
        from serpent import Env, Event, U32, contract, contractevent


        @contractevent
        class Tick(Event):
            n: U32


        @contract
        class C:
            def go(self, env: Env) -> None:
                Tick(n=U32(1)).publish(env)
        """
    )
    assert "vec_new_from_linear_memory" in compiled.host_fns_reachable
    assert compiled.needs_memory is True


# --- construction is kwargs-only, and type-checked --------------------------


def test_positional_construction_is_rejected() -> None:
    """Review B3: an event is constructed exactly the way a `@contracttype` is
    -- keywords only, because the on-chain field order is the sorted one."""
    _reject(
        """
        from serpent import Env, Event, U32, contract, contractevent


        @contractevent
        class Bumped(Event):
            count: U32


        @contract
        class C:
            def go(self, env: Env, n: U32) -> None:
                Bumped(n).publish(env)
        """,
        "SPT3020",
    )


@pytest.mark.parametrize(
    ("call", "code"),
    [
        ("Bumped(count=who)", "SPT3018"),
        ("Bumped(nope=n)", "SPT3020"),
        ("Bumped()", "SPT3020"),
    ],
    ids=["wrong-type", "unknown-field", "missing-field"],
)
def test_the_construction_arguments_are_checked_like_a_structs(call: str, code: str) -> None:
    _reject(
        f"""
        from serpent import Address, Env, Event, U32, contract, contractevent


        @contractevent
        class Bumped(Event):
            count: U32


        @contract
        class C:
            def go(self, env: Env, n: U32, who: Address) -> None:
                {call}.publish(env)
        """,
        code,
    )


@pytest.mark.parametrize(
    "call",
    ["publish()", "publish(env, env)", "publish(nope=env)"],
    ids=["no-argument", "two-arguments", "unknown-keyword"],
)
def test_publish_takes_exactly_the_env(call: str) -> None:
    exc = _reject(
        f"""
        from serpent import Env, Event, U32, contract, contractevent


        @contractevent
        class Bumped(Event):
            count: U32


        @contract
        class C:
            def go(self, env: Env, n: U32) -> None:
                Bumped(count=n).{call}
        """,
        "SPT3020" if call != "publish(nope=env)" else "SPT1035",
    )
    assert exc.diagnostics


def test_publishing_through_something_that_is_not_the_env_is_rejected() -> None:
    _reject(
        """
        from serpent import Env, Event, U32, contract, contractevent


        @contractevent
        class Bumped(Event):
            count: U32


        @contract
        class C:
            def go(self, env: Env, n: U32) -> None:
                Bumped(count=n).publish(n)
        """,
        "SPT1038",
    )


def test_an_event_instance_bound_to_a_local_is_still_rejected() -> None:
    """Construction-and-publish in ONE expression is the supported shape; an
    event instance is not a value (`expr.py`'s SPT1037 path, untouched)."""
    _reject(
        """
        from serpent import Env, Event, U32, contract, contractevent


        @contractevent
        class Bumped(Event):
            count: U32


        @contract
        class C:
            def go(self, env: Env, n: U32) -> None:
                event = Bumped(count=n)
                event.publish(env)
        """,
        "SPT1037",
    )
