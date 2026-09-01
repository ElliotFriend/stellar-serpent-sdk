"""Tests for `serpent.compiler.types_` (the `Ty` model + `resolve_annotation`)
and `serpent.compiler.ctx` (`FuncCtx`, `SlotTable`, `AliasTable`).

Task 4 of the M1-C plan. See
docs/superpowers/specs/2026-08-27-m1c-inputs-dossier.md §C.2/§C.3 for the `Ty`
node shape and the `Locals` rules this implements, and the task brief for the
derivation of `resolve_annotation`'s behavior.
"""

from __future__ import annotations

import typing

import pytest

from serpent import (
    I32,
    U32,
    Env,
    Map,
    Vec,
    contracttype,
)
from serpent.compiler.ctx import AliasTable, FuncCtx, LocalSlot, Ownership, SlotTable
from serpent.compiler.diagnostics import Diagnostics, Loc
from serpent.compiler.loader import CompilerBugError, LoadedModule, load_module
from serpent.compiler.types_ import ReprForm, Ty, TyTag, resolve_annotation

PATH = "contract.py"

# --- a loaded module exercising every mappable annotation shape ------------

_SOURCE = """
from serpent import (
    Address, Bool, Bytes, Bytes32, ContractError, Duration, Env, Event,
    ContractEnum, ContractUnion,
    I32, I64, I128, Map, String, Symbol, Timepoint, U32, U64, U128, Vec,
    bytes_n, contract, contracterror, contractenum, contractevent, contracttype,
    contractunion, enumvalue, errorcode, variant,
)


@contracttype
class Balance:
    amount: U32
    owner: Address


@contracterror
class Err:
    NotFound = errorcode(1)


@contractevent
class Transfer(Event):
    amount: U32


@contractunion
class Shape(ContractUnion):
    Empty = variant()
    Circle = variant(U32)


@contractenum
class Color(ContractEnum):
    Red = enumvalue(0)
    Green = enumvalue(1)


@contract
class C:
    def go(
        self,
        env: Env,
        p_bool: Bool,
        p_u32: U32,
        p_i32: I32,
        p_u64: U64,
        p_i64: I64,
        p_u128: U128,
        p_i128: I128,
        p_timepoint: Timepoint,
        p_duration: Duration,
        p_symbol: Symbol,
        p_string: String,
        p_bytes: Bytes,
        p_bytes32: Bytes32,
        p_bytes20: bytes_n(20),
        p_address: Address,
        p_vec: Vec[U32],
        p_map: Map[Symbol, U32],
        p_struct: Balance,
        p_option: U32 | None,
        p_vec_struct: Vec[Balance],
        p_union: Shape,
        p_enum: Color,
        p_vec_union: Vec[Shape],
        p_option_enum: Color | None,
    ) -> U32:
        return p_u32
"""


def _loaded() -> LoadedModule:
    loaded = load_module(_SOURCE, PATH)
    assert not loaded.diagnostics, loaded.diagnostics.diagnostics
    return loaded


def _hints(loaded: LoadedModule) -> dict[str, object]:
    assert loaded.contract_cls is not None
    method = loaded.contract_cls.__dict__["go"]
    return typing.get_type_hints(method)


def _loc() -> Loc:
    return Loc.whole_file(PATH)


# --- Ty: rank / repr_form / wasm_arith_width goldens ------------------------

_GOLDENS: list[tuple[Ty, ReprForm, int, int | None]] = [
    (Ty.Bool, ReprForm.IMMEDIATE, 0, None),
    (Ty.U32, ReprForm.IMMEDIATE, 3, 32),
    (Ty.I32, ReprForm.IMMEDIATE, 4, 32),
    (Ty.U64, ReprForm.EITHER, 5, 64),
    (Ty.I64, ReprForm.EITHER, 6, 64),
    (Ty.Timepoint, ReprForm.EITHER, 7, None),
    (Ty.Duration, ReprForm.EITHER, 8, None),
    (Ty.U128, ReprForm.EITHER, 9, 64),
    (Ty.I128, ReprForm.EITHER, 10, 64),
    (Ty.Bytes, ReprForm.HOST_OBJECT, 13, None),
    (Ty.BytesN(32), ReprForm.HOST_OBJECT, 13, None),
    (Ty.String, ReprForm.HOST_OBJECT, 14, None),
    (Ty.Symbol, ReprForm.EITHER, 15, None),
    (Ty.Vec(Ty.U32), ReprForm.HOST_OBJECT, 16, None),
    (Ty.Map(Ty.Symbol, Ty.U32), ReprForm.HOST_OBJECT, 17, None),
    (Ty.Struct("Balance"), ReprForm.HOST_OBJECT, 17, None),
    # M1-E2 §B.1: a tagged union IS an `ScVec` on chain (Vec's rank, always an
    # object handle); an int enum IS a bare `u32` (U32's rank, always inline).
    (Ty.Union("Shape"), ReprForm.HOST_OBJECT, 16, None),
    (Ty.Enum("Color"), ReprForm.IMMEDIATE, 3, None),
    (Ty.Address, ReprForm.HOST_OBJECT, 18, None),
    (Ty.Void, ReprForm.IMMEDIATE, 1, None),
    (Ty.ErrorEnum("Err"), ReprForm.IMMEDIATE, 2, None),
    # Option folds through to its wrapped type for both facts.
    (Ty.Option(Ty.U32), ReprForm.IMMEDIATE, 3, None),
    (Ty.Option(Ty.U64), ReprForm.EITHER, 5, None),
    (Ty.Option(Ty.Vec(Ty.U32)), ReprForm.EITHER, 16, None),
]


@pytest.mark.parametrize("ty,repr_form,rank,width", _GOLDENS)
def test_ty_goldens(ty: Ty, repr_form: ReprForm, rank: int, width: int | None) -> None:
    assert ty.repr_form is repr_form
    assert ty.scval_rank == rank
    assert ty.wasm_arith_width == width


_RENDER_GOLDENS: list[tuple[Ty, str]] = [
    (Ty.Bool, "Bool"),
    (Ty.U32, "U32"),
    (Ty.I32, "I32"),
    (Ty.U64, "U64"),
    (Ty.I64, "I64"),
    (Ty.U128, "U128"),
    (Ty.I128, "I128"),
    (Ty.Timepoint, "Timepoint"),
    (Ty.Duration, "Duration"),
    (Ty.Symbol, "Symbol"),
    (Ty.String, "String"),
    (Ty.Bytes, "Bytes"),
    (Ty.BytesN(32), "BytesN(32)"),
    (Ty.Address, "Address"),
    (Ty.Vec(Ty.U32), "Vec(U32)"),
    (Ty.Map(Ty.Symbol, Ty.U32), "Map(Symbol, U32)"),
    (Ty.Struct("Balance"), "Struct(Balance)"),
    (Ty.Union("Shape"), "Union(Shape)"),
    (Ty.Enum("Color"), "Enum(Color)"),
    (Ty.Option(Ty.U32), "Option(U32)"),
    (Ty.Void, "Void"),
    (Ty.ErrorEnum("Err"), "ErrorEnum(Err)"),
    (Ty.Invalid, "<invalid>"),
    (Ty.Vec(Ty.Struct("Balance")), "Vec(Struct(Balance))"),
]


@pytest.mark.parametrize("ty,expected", _RENDER_GOLDENS)
def test_ty_render(ty: Ty, expected: str) -> None:
    assert ty.render() == expected


def test_ty_equality_and_hash_are_structural() -> None:
    assert Ty.Vec(Ty.U32) == Ty.Vec(Ty.U32)
    assert Ty.Vec(Ty.U32) != Ty.Vec(Ty.I32)
    assert Ty.Map(Ty.Symbol, Ty.U32) != Ty.Map(Ty.U32, Ty.Symbol)
    assert hash(Ty.Vec(Ty.U32)) == hash(Ty.Vec(Ty.U32))
    assert {Ty.U32, Ty.U32, Ty.I32} == {Ty.U32, Ty.I32}
    assert Ty.Struct("Balance") == Ty.Struct("Balance")
    assert Ty.Struct("Balance") != Ty.Struct("Other")


def test_ty_invalid_sentinel_rejects_representation_questions() -> None:
    with pytest.raises(ValueError, match="sentinel"):
        _ = Ty.Invalid.repr_form
    with pytest.raises(ValueError, match="Invalid"):
        _ = Ty.Invalid.scval_rank
    # wasm_arith_width is a plain lookup with no "real type" precondition.
    assert Ty.Invalid.wasm_arith_width is None


# --- resolve_annotation: the mapping rows -----------------------------------

_MAPPING_ROWS: list[tuple[str, Ty]] = [
    ("p_bool", Ty.Bool),
    ("p_u32", Ty.U32),
    ("p_i32", Ty.I32),
    ("p_u64", Ty.U64),
    ("p_i64", Ty.I64),
    ("p_u128", Ty.U128),
    ("p_i128", Ty.I128),
    ("p_timepoint", Ty.Timepoint),
    ("p_duration", Ty.Duration),
    ("p_symbol", Ty.Symbol),
    ("p_string", Ty.String),
    ("p_bytes", Ty.Bytes),
    ("p_bytes32", Ty.BytesN(32)),
    ("p_bytes20", Ty.BytesN(20)),
    ("p_address", Ty.Address),
    ("p_vec", Ty.Vec(Ty.U32)),
    ("p_map", Ty.Map(Ty.Symbol, Ty.U32)),
    ("p_struct", Ty.Struct("Balance")),
    ("p_option", Ty.Option(Ty.U32)),
    ("p_vec_struct", Ty.Vec(Ty.Struct("Balance"))),
    ("p_union", Ty.Union("Shape")),
    ("p_enum", Ty.Enum("Color")),
    ("p_vec_union", Ty.Vec(Ty.Union("Shape"))),
    ("p_option_enum", Ty.Option(Ty.Enum("Color"))),
]


@pytest.mark.parametrize("param_name,expected", _MAPPING_ROWS)
def test_resolve_annotation_mapping_rows(param_name: str, expected: Ty) -> None:
    loaded = _loaded()
    hints = _hints(loaded)
    sink = Diagnostics()
    result = resolve_annotation(hints[param_name], loaded, _loc(), sink)
    assert not sink
    assert result == expected


def test_a_union_and_an_int_enum_annotation_never_resolve_to_a_struct() -> None:
    """The transient `Ty.Struct` collision M1-E2 Task 3 opened and this task
    closes (carried acceptance criterion).

    `resolve_annotation` DELEGATES classification to `to_spec_type`
    (`types_.py`'s own docstring), and Task 3 widened `to_spec_type` to accept
    a union/int-enum class as a name-only UDT reference. Until `_build_ty`
    grew its two arms, both fell through the tail whose comment claimed "the
    only shape `to_spec_type` still accepts at this point is a
    `@contracttype` struct" -- silently, with no diagnostic, to
    `Ty.Struct(name)`: `Map`'s ScVal rank, `Map`'s ABI tag, and `map_get`
    field reads on a value that is an `ScVec` or a bare `u32` on chain.

    So this asserts the NEGATIVE as well as the positive: neither annotation
    may resolve to `TyTag.STRUCT`, whatever else changes.
    """
    loaded = _loaded()
    hints = _hints(loaded)
    for param_name, tag, expected in (
        ("p_union", TyTag.UNION, Ty.Union("Shape")),
        ("p_enum", TyTag.ENUM, Ty.Enum("Color")),
    ):
        sink = Diagnostics()
        result = resolve_annotation(hints[param_name], loaded, _loc(), sink)
        assert not sink, [d.message for d in sink.diagnostics]
        assert result == expected
        assert result is not None and result.tag is tag
        assert result.tag is not TyTag.STRUCT


def test_resolve_annotation_option_both_spellings() -> None:
    """`X | None` (`types.UnionType`) and `typing.Optional[X]`/`typing.Union[X,
    None]` (`typing.Union`) both resolve to the same `Ty.Option` -- which
    spelling an author (or, here, a direct caller) used must not change the
    result (dossier B7's `_option` handles both origins identically)."""
    loaded = _loaded()
    sink = Diagnostics()
    assert resolve_annotation(U32 | None, loaded, _loc(), sink) == Ty.Option(Ty.U32)
    assert not sink

    sink2 = Diagnostics()
    # The `typing.Optional` spelling, deliberately -- ruff would otherwise
    # suggest rewriting it to `X | None`, which is the OTHER spelling this
    # test exists to distinguish.
    optional_spelling: object = typing.Optional[U32]  # noqa: UP045
    assert resolve_annotation(optional_spelling, loaded, _loc(), sink2) == Ty.Option(Ty.U32)
    assert not sink2

    sink3 = Diagnostics()
    # The `typing.Union` spelling, deliberately -- same reasoning as above.
    union_spelling: object = typing.Union[U32, None]  # noqa: UP007
    assert resolve_annotation(union_spelling, loaded, _loc(), sink3) == Ty.Option(Ty.U32)
    assert not sink3


def test_resolve_annotation_return_type() -> None:
    loaded = _loaded()
    hints = _hints(loaded)
    sink = Diagnostics()
    result = resolve_annotation(hints["return"], loaded, _loc(), sink)
    assert not sink
    assert result == Ty.U32


# --- resolve_annotation: the unmappable matrix (B7) -------------------------


def test_resolve_annotation_error_enum_uses_spt3001() -> None:
    loaded = _loaded()
    assert loaded.contract_cls is not None
    err_cls = loaded.namespace["Err"]
    sink = Diagnostics()
    result = resolve_annotation(err_cls, loaded, _loc(), sink)
    assert result is None
    assert len(sink) == 1
    diag = sink.diagnostics[0]
    assert diag.code == "SPT3001"
    assert "never a returnable value" in diag.message


_UNMAPPABLE_ROWS: list[tuple[str, object]] = [
    ("none", None),
    ("nonetype", type(None)),
    ("plain_int", int),
    ("plain_str", str),
    ("plain_bytes", bytes),
    ("plain_bool", bool),
    ("bare_vec", Vec),
    ("bare_map", Map),
    ("cross_union", U32 | I32),
    ("other_generic", tuple[U32, U32]),
]


@pytest.mark.parametrize("label,obj", _UNMAPPABLE_ROWS, ids=[r[0] for r in _UNMAPPABLE_ROWS])
def test_resolve_annotation_unmappable_rows_use_spt3013(label: str, obj: object) -> None:
    loaded = _loaded()
    sink = Diagnostics()
    result = resolve_annotation(obj, loaded, _loc(), sink)
    assert result is None
    assert len(sink) == 1
    assert sink.diagnostics[0].code == "SPT3013"


def test_resolve_annotation_env_outside_leading_param_uses_spt3013() -> None:
    loaded = _loaded()
    sink = Diagnostics()
    result = resolve_annotation(Env, loaded, _loc(), sink)
    assert result is None
    assert len(sink) == 1
    diag = sink.diagnostics[0]
    assert diag.code == "SPT3013"
    assert "Env is the host handle" in diag.message


def test_resolve_annotation_contract_class_used_as_a_type_uses_spt3013() -> None:
    loaded = _loaded()
    assert loaded.contract_cls is not None
    sink = Diagnostics()
    result = resolve_annotation(loaded.contract_cls, loaded, _loc(), sink)
    assert result is None
    assert sink.diagnostics[0].code == "SPT3013"


def test_resolve_annotation_event_class_used_as_a_type_uses_spt3013() -> None:
    loaded = _loaded()
    transfer_cls = loaded.namespace["Transfer"]
    sink = Diagnostics()
    result = resolve_annotation(transfer_cls, loaded, _loc(), sink)
    assert result is None
    assert sink.diagnostics[0].code == "SPT3013"
    assert "SCSpecEventV0" in sink.diagnostics[0].message


def test_resolve_annotation_none_reuses_the_void_return_refusal_text() -> None:
    loaded = _loaded()
    sink = Diagnostics()
    result = resolve_annotation(None, loaded, _loc(), sink)
    assert result is None
    assert "EMPTY outputs list" in sink.diagnostics[0].message


def test_resolve_annotation_never_raises_and_never_returns_a_diagnostic() -> None:
    # minor 13's sink convention, spot-checked across a batch of unmappables.
    loaded = _loaded()
    for _, obj in _UNMAPPABLE_ROWS:
        sink = Diagnostics()
        result = resolve_annotation(obj, loaded, _loc(), sink)
        assert result is None or isinstance(result, Ty)


def test_resolve_annotation_asserts_struct_membership_in_loaded_module() -> None:
    """A struct not in `loaded`'s own inventory is a compiler bug (F.1.14-style)."""

    @contracttype
    class Decoy:
        x: U32

    minimal_source = (
        "from serpent import Env, U32, contract\n\n\n"
        "@contract\nclass C:\n    def go(self, env: Env) -> U32:\n        return U32(0)\n"
    )
    loaded = load_module(minimal_source, PATH)
    assert not loaded.decorated_types_in_order
    sink = Diagnostics()
    with pytest.raises(CompilerBugError, match="declared-type inventory"):
        resolve_annotation(Decoy, loaded, _loc(), sink)


# --- SlotTable ---------------------------------------------------------------


def test_slot_table_declares_a_new_local() -> None:
    table = SlotTable()
    sink = Diagnostics()
    slot = table.declare("total", Ty.U32, _loc(), sink)
    assert not sink
    assert slot == LocalSlot(slot=0, name="total", ty=Ty.U32, definitely_assigned=False)
    assert len(table) == 1
    assert table.lookup("total") is slot


def test_slot_table_redeclare_same_type_is_the_same_slot_not_a_new_one() -> None:
    table = SlotTable()
    sink = Diagnostics()
    first = table.declare("total", Ty.U32, _loc(), sink)
    second = table.declare("total", Ty.U32, _loc(), sink)
    assert not sink
    assert first is second
    assert len(table) == 1


def test_slot_table_rebind_at_a_different_type_is_spt3017() -> None:
    table = SlotTable()
    sink = Diagnostics()
    table.declare("total", Ty.U32, _loc(), sink)
    result = table.declare("total", Ty.I32, _loc(), sink)
    assert result is None
    assert len(sink) == 1
    diag = sink.diagnostics[0]
    assert diag.code == "SPT3017"
    assert len(table) == 1  # the bad rebind did not create a second slot


def test_slot_table_shadowing_a_reserved_name_is_spt2004() -> None:
    table = SlotTable(reserved={"admin": "a parameter"})
    sink = Diagnostics()
    result = table.declare("admin", Ty.Address, _loc(), sink)
    assert result is None
    assert len(sink) == 1
    diag = sink.diagnostics[0]
    assert diag.code == "SPT2004"
    assert "a parameter" in "".join(diag.notes)
    assert len(table) == 0


def test_slot_table_mark_assigned_and_iteration_order() -> None:
    table = SlotTable()
    sink = Diagnostics()
    table.declare("a", Ty.U32, _loc(), sink)
    table.declare("b", Ty.Bool, _loc(), sink)
    assert not sink
    table.mark_assigned("a")
    slot_a = table.lookup("a")
    slot_b = table.lookup("b")
    assert slot_a is not None and slot_b is not None
    assert slot_a.definitely_assigned is True
    assert slot_b.definitely_assigned is False
    assert [slot.name for slot in table] == ["a", "b"]


def test_slot_table_lookup_of_unknown_name_is_none() -> None:
    table = SlotTable()
    assert table.lookup("nope") is None


# --- AliasTable ---------------------------------------------------------------


def test_alias_table_starts_unclassified() -> None:
    table = AliasTable()
    assert table.ownership_of(0) is None


def test_alias_table_mark_owned_and_aliased() -> None:
    table = AliasTable()
    table.mark_owned(0)
    assert table.ownership_of(0) is Ownership.OWNED
    table.mark_aliased(0)
    assert table.ownership_of(0) is Ownership.ALIASED
    assert table.ownership_of(1) is None


# --- FuncCtx -------------------------------------------------------------------


def test_func_ctx_construction() -> None:
    loaded = _loaded()
    sink = Diagnostics()
    loc = _loc()
    params = [("amount", Ty.U32, loc), ("to", Ty.Address, loc)]
    locals_table = SlotTable(reserved={"amount": "a parameter", "to": "a parameter"})
    ctx = FuncCtx(
        loaded=loaded,
        sink=sink,
        params=params,
        locals=locals_table,
        loop_depth=0,
        return_ty=Ty.Void,
        alias_sets=AliasTable(),
        fn_name="transfer",
        path=PATH,
    )
    assert ctx.loaded is loaded
    assert ctx.sink is sink
    assert ctx.params == params
    assert ctx.locals is locals_table
    assert ctx.loop_depth == 0
    assert ctx.return_ty == Ty.Void
    assert isinstance(ctx.alias_sets, AliasTable)
    assert ctx.fn_name == "transfer"
    assert ctx.path == PATH

    # The reserved names threaded into `locals` reject shadowing a param.
    result = ctx.locals.declare("amount", Ty.U32, loc, sink)
    assert result is None
    assert sink.diagnostics[-1].code == "SPT2004"
