"""Task 10: `compile_module`'s assembly and its emitter-facing outputs.

Covers the dossier SS C.2 output contract ("What C must hand D beyond the
tree"), the SPT6xxx protocol band (wired end-to-end through a fake gated
HostFn, since no real M1-C source can trip it -- `codes.NO_FIXTURE_
ALLOWLIST`), the E11 alias pre-pass's loop soundness, and the two invariants
`compile_module` asserts about its own output.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from serpent._host import functions_by_name
from serpent._host._protocol import BASE_PROTOCOL, DEFAULT_TARGET_PROTOCOL
from serpent.compiler import codes, frontend
from serpent.compiler.diagnostics import CompileError, Diagnostic, Loc, LocKind
from serpent.compiler.frontend import CompiledModule, compile_module
from serpent.compiler.ir import (
    Const,
    ConstDecl,
    ContractIR,
    FuncKind,
    ModuleIR,
)
from serpent.compiler.loader import CompilerBugError
from serpent.compiler.types_ import Ty

TOKEN_STYLE = Path(__file__).resolve().parents[1] / "fixtures" / "token_style.py"

PATH = "contracts/t.py"


def _compile(source: str, **kwargs: Any) -> CompiledModule:
    return compile_module(textwrap.dedent(source).lstrip(), PATH, **kwargs)


def _codes(exc: CompileError) -> list[str]:
    return [d.code for d in exc.diagnostics]


def _expect_reject(source: str, code: str) -> CompileError:
    with pytest.raises(CompileError) as info:
        _compile(source)
    assert code in _codes(info.value), _codes(info.value)
    return info.value


# --- token_style.py end to end (A23, F.2.8) ---------------------------------


@pytest.fixture(scope="module")
def token_style() -> CompiledModule:
    return compile_module(TOKEN_STYLE.read_text(), str(TOKEN_STYLE))


def test_token_style_compiles_end_to_end(token_style: CompiledModule) -> None:
    assert isinstance(token_style.ir, ModuleIR)
    assert isinstance(token_style.ir.contract, ContractIR)
    assert token_style.ir.contract.name == "TokenStyle"


def test_token_style_function_inventory(token_style: CompiledModule) -> None:
    contract = token_style.ir.contract
    assert contract is not None
    assert [f.py_name for f in contract.methods] == [
        "__init__",
        "name",
        "is_admin",
        "balance",
        "mint",
        "transfer",
    ]
    by_name = {f.py_name: f for f in contract.methods}
    assert by_name["__init__"].kind is FuncKind.CONSTRUCTOR
    assert by_name["__init__"].export_name == "__constructor"
    assert by_name["name"].kind is FuncKind.EXPORT
    assert token_style.ir.helpers == ()
    # `functions` is the flat list D iterates: contract methods then helpers.
    assert token_style.functions == contract.methods


def test_token_style_returns_on_every_path(token_style: CompiledModule) -> None:
    contract = token_style.ir.contract
    assert contract is not None
    by_name = {f.py_name: f for f in contract.methods}
    # A non-Void method proves definite return; a `-> None` method need not.
    assert by_name["name"].returns_on_every_path is True
    assert by_name["balance"].returns_on_every_path is True
    assert by_name["mint"].returns_on_every_path is False


def test_token_style_host_fns_used_exactly(token_style: CompiledModule) -> None:
    assert token_style.host_fns_used == frozenset(
        {
            "put_contract_data",
            "get_contract_data",
            "has_contract_data",
            "obj_cmp",
            "map_new_from_linear_memory",
            "require_auth",
            "fail_with_error",
            "contract_event",
        }
    )


def test_token_style_reachable_adds_the_forms_d_chooses(token_style: CompiledModule) -> None:
    # `MakeTopics` is a VecObject whose build form is D's choice (MJ-15), so
    # its candidates are reachable-but-not-certain.
    extra = token_style.host_fns_reachable - token_style.host_fns_used
    assert extra == frozenset({"vec_new", "vec_push_back", "vec_new_from_linear_memory"})


def test_every_host_fn_the_frontend_names_is_in_the_pinned_bindings(
    token_style: CompiledModule,
) -> None:
    # B2: bindings are looked up BY NAME, so a typo must fail loudly here
    # rather than at emission.
    unknown = sorted(token_style.host_fns_reachable - set(functions_by_name))
    assert not unknown, unknown


def test_token_style_needs_memory_and_literal_inventory(token_style: CompiledModule) -> None:
    assert token_style.needs_memory is True
    literals = token_style.literals
    assert literals.symbols_over_9 == ()
    assert literals.strings == ()
    assert literals.bytes_literals == ()
    # One struct shape (`BalanceKey(owner=...)`), constructed four times: the
    # key-descriptor SET is what D lays out, so it is deduped and P7-sorted.
    assert literals.struct_key_descriptor_sets == (("owner",),)


def test_token_style_runtime_parts(token_style: CompiledModule) -> None:
    # `current + amount` etc. are checked U32 arithmetic (A4); nothing here is
    # 128-bit, so no guest-runtime arithmetic part is needed.
    assert token_style.runtime_parts_needed == frozenset({"overflow_check"})


def test_token_style_spec_inputs_keep_events_separate(token_style: CompiledModule) -> None:
    spec_inputs = token_style.spec_inputs
    assert spec_inputs.contract_cls is not None
    assert spec_inputs.contract_cls.__name__ == "TokenStyle"
    assert [t.__name__ for t in spec_inputs.declared_types_in_order] == [
        "TokenError",
        "BalanceKey",
    ]
    # MJ-9/B14: `build_spec_entries(..., types=...)` refuses an event class, so
    # an event must never appear in `declared_types_in_order`.
    assert [t.__name__ for t in spec_inputs.events] == ["Transfer"]
    assert not set(spec_inputs.events) & set(spec_inputs.declared_types_in_order)


def test_token_style_declares_the_base_protocol(token_style: CompiledModule) -> None:
    # Nothing token_style reaches is gated above the base, so the COMPUTED
    # floor (never hand-set, S18) is the base itself.
    assert token_style.declared_protocol == BASE_PROTOCOL


def test_token_style_module_level_facts(token_style: CompiledModule) -> None:
    ir = token_style.ir
    assert ir.doc is not None and ir.doc.startswith("A realistic token-shaped contract")
    assert [c.name for c in ir.consts] == ["ADMIN", "NAME_KEY"]
    assert all(isinstance(c, ConstDecl) for c in ir.consts)
    assert [s.name for s in ir.structs] == ["BalanceKey"]
    assert [e.name for e in ir.error_enums] == ["TokenError"]
    assert [e.name for e in ir.events] == ["Transfer"]
    assert "Symbol" in ir.imports and "contract" in ir.imports


# --- the derived outputs on their own --------------------------------------


_MINIMAL = """
from serpent import Env, U32, contract


@contract
class C:
    def go(self, env: Env) -> U32:
        return U32(1)
"""


def test_a_memoryless_contract_compiles_memoryless() -> None:
    # Spec SS 5 keeps this supported: nothing here is a wide Symbol, a
    # String/Bytes literal, a struct, or a static bulk construction.
    compiled = _compile(_MINIMAL)
    assert compiled.needs_memory is False
    assert compiled.literals == frontend.LiteralInventory((), (), (), ())
    assert compiled.host_fns_used == frozenset()
    assert compiled.runtime_parts_needed == frozenset()


def test_literal_inventory_collects_every_linear_memory_literal() -> None:
    compiled = _compile(
        """
        from serpent import Bytes, Env, String, Symbol, U32, contract


        @contract
        class C:
            def go(self, env: Env) -> U32:
                env.storage().instance().set(Symbol("a_long_symbol_name"), String("hello"))
                env.storage().instance().set(Symbol("k"), Bytes(b"\\x01\\x02"))
                return U32(0)
        """
    )
    assert compiled.needs_memory is True
    assert compiled.literals.symbols_over_9 == ("a_long_symbol_name",)
    assert compiled.literals.strings == ("hello",)
    assert compiled.literals.bytes_literals == (b"\x01\x02",)
    assert {
        "symbol_new_from_linear_memory",
        "string_new_from_linear_memory",
        "bytes_new_from_linear_memory",
    } <= compiled.host_fns_used


def test_wide_integer_arithmetic_names_its_guest_runtime_parts() -> None:
    compiled = _compile(
        """
        from serpent import Env, I128, U128, contract


        @contract
        class C:
            def go(self, env: Env, a: U128, b: U128, c: I128) -> U128:
                d = -c
                return a * b + a
        """
    )
    assert compiled.runtime_parts_needed == frozenset(
        {"overflow_check", "u128_mul", "u128_add", "i128_neg"}
    )


def test_static_bulk_construction_needs_memory() -> None:
    compiled = _compile(
        """
        from serpent import Env, U32, Vec, contract


        @contract
        class C:
            def go(self, env: Env) -> U32:
                v = Vec(U32, [U32(1), U32(2)])
                return len(v)
        """
    )
    assert compiled.needs_memory is True


def test_a_non_static_bulk_construction_does_not_need_memory() -> None:
    # `all_static=False` means D falls back to vec_new + vec_push_back, which
    # touches no linear memory at all.
    compiled = _compile(
        """
        from serpent import Env, U32, Vec, contract


        @contract
        class C:
            def go(self, env: Env, x: U32) -> U32:
                v = Vec(U32, [x])
                return len(v)
        """
    )
    assert compiled.needs_memory is False


# --- target_protocol threading (BL-1, B4, S18) ------------------------------


def test_target_protocol_none_returns_the_computed_floor() -> None:
    assert _compile(_MINIMAL).declared_protocol == BASE_PROTOCOL


def test_explicit_target_protocol_is_returned_verbatim() -> None:
    compiled = _compile(_MINIMAL, target_protocol=DEFAULT_TARGET_PROTOCOL)
    assert compiled.declared_protocol == DEFAULT_TARGET_PROTOCOL


def test_target_protocol_below_the_floor_is_spt6001() -> None:
    exc = _expect_reject_kwargs(_MINIMAL, "SPT6001", target_protocol=BASE_PROTOCOL - 1)
    assert any("floor" in d.message or "gated" in d.message for d in exc.diagnostics)


def _expect_reject_kwargs(source: str, code: str, **kwargs: Any) -> CompileError:
    with pytest.raises(CompileError) as info:
        _compile(source, **kwargs)
    assert code in _codes(info.value), _codes(info.value)
    return info.value


# --- SPT6xxx wired end to end through a fake gated HostFn -------------------

_RAISES = """
from serpent import Env, U32, contract, contracterror, errorcode


@contracterror
class E:
    Nope = errorcode(1)


@contract
class C:
    def go(self, env: Env) -> U32:
        raise E.Nope
"""


def test_synthetic_gated_host_fn_maps_to_spt6001_naming_the_offender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fake gated HostFn in the used-set path must surface as SPT6001.

    No real M1-C source can trip the band -- every host function the frontend
    emits is ungated at the base protocol -- which is why SPT6001 is on
    `codes.NO_FIXTURE_ALLOWLIST` and is proven here instead. The fake is
    injected at the two seams a real gated surface would arrive through: the
    binding table `_protocol` looks names up in, and the name the frontend
    emits for a `raise` (dossier SS C.4's `fail_with_error` row). Everything
    between -- the used-set walk, `declared_protocol`, `ProtocolGateError`,
    the SPT6001 diagnostic -- is the production path.
    """
    from serpent._host import _protocol
    from serpent._host._model import HostFn

    real = functions_by_name["fail_with_error"]
    fake = HostFn(
        module=real.module,
        name="m2_gated_surface",
        export=real.export,
        arity=real.arity,
        arg_names=real.arg_names,
        arg_types=real.arg_types,
        ret_type=real.ret_type,
        min_protocol=99,
        max_protocol=None,
    )
    monkeypatch.setitem(_protocol._FUNCTIONS_BY_NAME, "m2_gated_surface", fake)
    monkeypatch.setattr(frontend, "_RAISE_HOST_FN", "m2_gated_surface")

    exc = _expect_reject(_RAISES, "SPT6001")
    gate = next(d for d in exc.diagnostics if d.code == "SPT6001")
    assert "m2_gated_surface" in gate.message
    assert "min_protocol=99" in gate.message
    # The diagnostic points at the `raise` that reached the gated function,
    # not at the whole file (P2: a real span whenever one exists).
    assert gate.loc.kind is LocKind.NODE
    assert gate.loc.line == _RAISES.strip().splitlines().index("        raise E.Nope") + 1


def test_a_gated_pinned_host_fn_also_maps_to_spt6001(monkeypatch: pytest.MonkeyPatch) -> None:
    # `protocol_gated_dummy` is a REAL pinned binding with max_protocol=19, so
    # this needs no fake HostFn at all -- only the name seam.
    monkeypatch.setattr(frontend, "_RAISE_HOST_FN", "protocol_gated_dummy")
    exc = _expect_reject(_RAISES, "SPT6001")
    gate = next(d for d in exc.diagnostics if d.code == "SPT6001")
    assert "protocol_gated_dummy" in gate.message
    assert "max_protocol=19" in gate.message


def test_spt6001_is_allowlisted_with_its_reason() -> None:
    assert "SPT6001" in codes.NO_FIXTURE_ALLOWLIST
    assert codes.NO_FIXTURE_REASONS["SPT6001"].startswith("no gated authoring surface at M1-C")


def test_an_unpinned_host_fn_name_is_a_compiler_bug(monkeypatch: pytest.MonkeyPatch) -> None:
    # A name the pinned bindings do not carry is C's bug, never the author's,
    # so it must NOT arrive as a user diagnostic.
    monkeypatch.setattr(frontend, "_RAISE_HOST_FN", "no_such_host_function")
    with pytest.raises(CompilerBugError):
        _compile(_RAISES)


# --- the E11 alias pre-pass (the loop counter-examples) ---------------------


def test_owned_container_mutation_compiles() -> None:
    compiled = _compile(
        """
        from serpent import Env, U32, Vec, contract


        @contract
        class C:
            def go(self, env: Env) -> Vec[U32]:
                v = Vec(U32, [U32(1)])
                v.push_back(U32(2))
                return v
        """
    )
    assert "vec_push_back" in compiled.host_fns_used


def test_a_container_built_up_in_a_loop_compiles() -> None:
    """The pattern the SPT1034 `help:` lines point at must actually compile.

    A pre-pass that over-approximated escapes would make this a false reject,
    which would leave the diagnostics recommending something the compiler
    refuses.
    """
    compiled = _compile(
        """
        from serpent import Env, Map, Symbol, U32, Vec, contract


        @contract
        class C:
            def go(self, env: Env, n: U32) -> U32:
                rows = Vec(U32, [])
                seen = Map(Symbol, U32, [])
                i = U32(0)
                while i < n:
                    rows.push_back(i)
                    seen.set(Symbol("k"), i)
                    i = i + U32(1)
                env.storage().persistent().set(Symbol("rows"), rows)
                return len(rows) + len(seen)
        """
    )
    assert {"vec_push_back", "map_put", "vec_len", "map_len"} <= compiled.host_fns_used


def test_mutation_before_an_alias_in_a_while_body_is_rejected() -> None:
    # `recognize_mutation`'s own counter-example: a statement-order walk would
    # accept this, because `own` still reads OWNED when the mutation is
    # checked -- and be wrong from the second iteration onwards.
    _expect_reject(
        """
        from serpent import Bool, Env, U32, Vec, contract


        @contract
        class C:
            def go(self, env: Env, flag: Bool) -> U32:
                own = Vec(U32, [U32(1)])
                while flag:
                    own.push_back(U32(2))
                    w = own
                    flag = Bool(False)
                return U32(0)
        """,
        "SPT1034",
    )


def test_mutation_before_an_escape_in_a_for_body_is_rejected() -> None:
    _expect_reject(
        """
        from serpent import Env, U32, Vec, contract


        @contract
        class C:
            def go(self, env: Env, n: U32) -> U32:
                own = Vec(U32, [U32(1)])
                nest = Vec(Vec, [])
                for i in range(n):
                    own.push_back(U32(2))
                    nest.push_back(own)
                return U32(0)
        """,
        "SPT1034",
    )


def test_mutating_the_container_being_iterated_is_rejected() -> None:
    # `for x in v:` binds a hidden `$for0_iter` local from `v` itself, so the
    # two share a handle and neither may be rebound -- which is exactly right:
    # mutating `v` mid-iteration diverges between the tiers.
    _expect_reject(
        """
        from serpent import Env, U32, Vec, contract


        @contract
        class C:
            def go(self, env: Env) -> U32:
                v = Vec(U32, [U32(1)])
                for x in v:
                    v.push_back(x)
                return U32(0)
        """,
        "SPT1034",
    )


def test_mutating_a_vec_a_struct_field_holds_is_rejected() -> None:
    _expect_reject(
        """
        from serpent import Env, U32, Vec, contract, contracttype


        @contracttype
        class Holder:
            items: Vec[U32]


        @contract
        class C:
            def go(self, env: Env) -> U32:
                own = Vec(U32, [U32(1)])
                h = Holder(items=own)
                own.push_back(U32(2))
                return U32(0)
        """,
        "SPT1034",
    )


def test_len_of_an_owned_vec_does_not_cost_ownership() -> None:
    # The pre-pass must not over-approximate: `len(v)` reads a container, it
    # does not store its handle anywhere.
    compiled = _compile(
        """
        from serpent import Env, U32, Vec, contract


        @contract
        class C:
            def go(self, env: Env) -> U32:
                v = Vec(U32, [U32(1)])
                v.push_back(U32(2))
                return len(v)
        """
    )
    assert "vec_push_back" in compiled.host_fns_used


def test_a_container_passed_to_a_helper_loses_ownership() -> None:
    _expect_reject(
        """
        from serpent import Env, U32, Vec, contract


        def total(env: Env, rows: Vec[U32]) -> U32:
            return len(rows)


        @contract
        class C:
            def go(self, env: Env) -> U32:
                v = Vec(U32, [U32(1)])
                n = total(env, v)
                v.push_back(U32(2))
                return n
        """,
        "SPT1034",
    )


def test_a_container_type_cannot_be_requested_from_storage_get() -> None:
    """The GET_DEFAULT `default=` escape position stays unreachable.

    `note_escapes`' docstring flags `<bucket>.get(key, T, default=d)` as the
    one escape position the wiring task owns: the lowering is an `IfExp` whose
    `orelse` IS `d`, so a container handed to `default` would be an arm of the
    whole expression. It is unreachable because a `get`'s type argument must
    name a chain type directly and a bare `Vec` is not one -- pinned here so a
    later widening of `_resolve_type_arg` cannot open the hole silently. Both
    halves of the routing are wired regardless: the pre-pass marks every
    keyword-argument value (see the struct-field test above), and
    `note_local_binding` routes the right-hand side through `note_escapes`,
    whose walk understands both `IfExp` arms.
    """
    _expect_reject(
        """
        from serpent import Env, Symbol, U32, Vec, contract


        @contract
        class C:
            def go(self, env: Env, k: Symbol, d: Vec[U32]) -> U32:
                v = env.storage().persistent().get(k, Vec, default=d)
                return len(v)
        """,
        "SPT3013",
    )


def test_a_container_in_a_keyword_position_loses_ownership() -> None:
    # The pre-pass rule that covers the GET_DEFAULT `default=` position (and
    # every `@contracttype` field), stated on its own.
    _expect_reject(
        """
        from serpent import Env, Symbol, U32, Vec, contract


        @contract
        class C:
            def go(self, env: Env, k: Symbol) -> U32:
                own = Vec(U32, [U32(1)])
                env.storage().persistent().set(key=k, value=own)
                own.push_back(U32(2))
                return len(own)
        """,
        "SPT1034",
    )


# --- recognition wired into real bodies ------------------------------------


def test_a_malformed_storage_chain_gets_a_located_diagnostic() -> None:
    # Carried obligation: `env.storage(1)` is a real typo, and before the
    # recognition tables were wired to body checking it fell through
    # unrecognized. It must be a located diagnostic, never silence.
    exc = _expect_reject_any(
        """
        from serpent import Env, Symbol, U32, contract


        @contract
        class C:
            def go(self, env: Env) -> U32:
                env.storage(1).instance().set(Symbol("k"), U32(1))
                return U32(0)
        """
    )
    assert all(d.loc.kind is LocKind.NODE for d in exc.diagnostics), exc.diagnostics
    assert all(d.loc.line == 7 for d in exc.diagnostics), exc.diagnostics


def _expect_reject_any(source: str) -> CompileError:
    with pytest.raises(CompileError) as info:
        _compile(source)
    return info.value


def test_a_comprehension_in_a_vec_items_position_is_spt1003() -> None:
    # MJ-14 reconciliation: the items argument being a comprehension is
    # "comprehensions are not supported", not the generic "must be a list
    # display" catch-all.
    _expect_reject(
        """
        from serpent import Env, U32, Vec, contract


        @contract
        class C:
            def go(self, env: Env, v: Vec[U32]) -> Vec[U32]:
                return Vec(U32, [x + U32(1) for x in v])
        """,
        "SPT1003",
    )


def test_struct_construction_and_field_read_lower_through_recognition() -> None:
    compiled = _compile(
        """
        from serpent import Address, Env, U32, contract, contracttype


        @contracttype
        class Bal:
            owner: Address
            amount: U32


        @contract
        class C:
            def go(self, env: Env, who: Address) -> U32:
                b = Bal(owner=who, amount=U32(1))
                return b.amount
        """
    )
    assert {"map_new_from_linear_memory", "map_get"} <= compiled.host_fns_used
    # P7: C owns the byte-string sort of the struct's keys.
    assert compiled.literals.struct_key_descriptor_sets == (("amount", "owner"),)


def test_an_event_publish_instance_form_is_still_deferred() -> None:
    _expect_reject(
        """
        from serpent import Address, Env, Event, U32, contract, contractevent


        @contractevent
        class Moved(Event):
            who: Address


        @contract
        class C:
            def go(self, env: Env, who: Address) -> U32:
                Moved(who=who).publish(env)
                return U32(0)
        """,
        "SPT1032",
    )


# --- the two output invariants compile_module asserts ----------------------


def test_invalid_ty_with_no_diagnostic_behind_it_is_a_compiler_bug() -> None:
    """Sink invariant: `Ty.Invalid` in the output IR needs a diagnostic.

    `Ty.Invalid` is the checkers' "already reported" placeholder (minor 13).
    An `Invalid` node reaching a caller with an EMPTY sink would mean the
    frontend silently dropped an expression -- a compiler bug, not a contract
    error, so it must not be catchable as a `CompileError`.
    """
    loc = Loc.whole_file(PATH)
    ir = ModuleIR(
        loc=loc,
        path=PATH,
        doc="",
        imports=(),
        consts=(
            ConstDecl(
                loc=loc,
                name="BROKEN",
                ty=Ty.U32,
                value=Const(loc=loc, ty=Ty.Invalid, py_value=None),
            ),
        ),
        structs=(),
        error_enums=(),
        events=(),
        contract=None,
        helpers=(),
    )
    with pytest.raises(CompilerBugError) as info:
        frontend._assert_no_invalid_ir(ir)
    assert "Ty.Invalid" in str(info.value)


def test_a_clean_compile_has_no_invalid_nodes(token_style: CompiledModule) -> None:
    frontend._assert_no_invalid_ir(token_style.ir)


def test_compile_module_raises_before_a_caller_can_see_the_ir() -> None:
    # SS C.2's output list: `diagnostics` must be empty for D to run, so a
    # failing compile hands back no `CompiledModule` at all.
    with pytest.raises(CompileError):
        _compile(
            """
            from serpent import Env, U32, contract


            @contract
            class C:
                def go(self, env: Env) -> U32:
                    return U32(1) + U32(1)
                    return U32(2)
            """
        )


def test_a_syntax_error_is_a_single_located_diagnostic() -> None:
    with pytest.raises(CompileError) as info:
        compile_module("def (:\n", PATH)
    (diagnostic,) = info.value.diagnostics
    assert isinstance(diagnostic, Diagnostic)
    assert diagnostic.loc.kind is LocKind.NODE


def test_compile_module_is_exported_from_the_package() -> None:
    from serpent import compiler

    assert compiler.compile_module is compile_module
    assert "compile_module" in compiler.__all__
