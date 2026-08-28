"""Tests for ``serpent.emitter.frame`` -- the operand-stack + control-frame tracker.

``frame.py`` is a by-copy port of ``spikes/spike1/emitter.py:358-506`` (R5,
P13) extended for D's larger language. The tests below are split into two
groups, and the split is the point:

* **The nine failure tests** (dossier S2's "that class of bug must be
  structurally impossible") -- each one is a bug the spike shipped or could
  have shipped, and each was RED-proven against a deliberately weakened
  tracker before the real guard existed.
* **The behavior tests** -- byte-level pins on the emitted encoding
  (blocktypes, `br` depths, symbolic call sites) and on the stack algebra.

Nothing here byte-compares against the on-chain artifact: the tracker's
bytes are self-snapshot class (B12), the constants they are built from are
opcodes.py's problem.
"""

import ast
import dataclasses
from pathlib import Path

import pytest

from serpent import val
from serpent.emitter import encode, frame, opcodes
from serpent.emitter.frame import (
    BuildLimitError,
    CallDefined,
    CallImport,
    CodeItem,
    EmitError,
    Fn,
    FrameKind,
)

# --- helpers ---------------------------------------------------------------


def _fn(
    name: str = "f",
    nparams: int = 0,
    nlocals_declared: int = 0,
    results: tuple[str, ...] = ("i64",),
) -> Fn:
    return Fn(name=name, nparams=nparams, nlocals_declared=nlocals_declared, results=results)


def _bytes_of(items: list[CodeItem]) -> bytes:
    """Concatenate a finished body's byte items, refusing symbolic call sites."""
    out = bytearray()
    for item in items:
        if not isinstance(item, bytes):
            raise TypeError(f"unexpected symbolic item {item!r}")
        out += item
    return bytes(out)


# ===========================================================================
# The nine failure tests (brief Step 1)
# ===========================================================================


def test_leaked_operand_at_ret_p2_repro() -> None:
    """P2's exact repro: a void host result never dropped, caught at ``return``.

    ``put_contract_data`` returns a Void ``Val``; drop the ``drop`` and the
    module still *validates* (wasm's polymorphic return tolerates operands
    left dangling under the result), which is precisely why the balance check
    lives at ``ret()`` and not at ``finish()``.
    """
    fn = _fn()
    for v in (1, 2, 3, 4):
        fn.i64_const(v)
    fn.call_import("put_contract_data", 4, has_result=True)  # ... and no drop()
    fn.i64_const(val.VOID_VAL)
    with pytest.raises(EmitError, match="at return"):
        fn.ret()


def test_control_frame_left_open_at_finish() -> None:
    fn = _fn()
    fn.begin_block(None)
    with pytest.raises(EmitError, match="control frame"):
        fn.finish()


def test_pop_i64_against_an_i32() -> None:
    fn = _fn()
    fn.i32_const(1)
    with pytest.raises(EmitError, match="expected i64 .* found i32"):
        fn.pop("i64")


def test_pop_from_an_empty_stack() -> None:
    fn = _fn()
    with pytest.raises(EmitError, match="empty operand stack"):
        fn.pop("i64")


def test_br_break_outside_any_block() -> None:
    fn = _fn()
    with pytest.raises(EmitError, match="no enclosing block"):
        fn.br_break()


def test_br_break_inside_a_loop_with_no_block_outside_it() -> None:
    """A `loop` is not a `break` target: `br 0` there jumps to the loop head."""
    fn = _fn()
    fn.begin_loop()
    with pytest.raises(EmitError, match="no enclosing block"):
        fn.br_break()


def test_br_continue_outside_any_loop() -> None:
    fn = _fn()
    fn.begin_block(None)
    with pytest.raises(EmitError, match="no enclosing loop"):
        fn.br_continue()


def test_local_index_past_the_declared_count() -> None:
    fn = _fn(nparams=1, nlocals_declared=2)  # valid indices: 0, 1, 2
    fn.local_get(2)
    fn.op(opcodes.DROP)
    fn.pop("i64")
    # The range check runs before any stack effect, on all three accessors.
    for accessor in (fn.local_get, fn.local_set, fn.local_tee):
        with pytest.raises(EmitError, match="local index 3"):
            accessor(3)
    assert fn.stack == []


def test_void_function_whose_body_leaves_a_value() -> None:
    fn = _fn(results=())
    fn.i64_const(7)
    with pytest.raises(EmitError, match="end of the body"):
        fn.finish()


def test_else_frame_height_mismatch() -> None:
    """An `if (result i64)` arm that produced nothing is caught at `else`."""
    fn = _fn()
    fn.i32_const(1)
    fn.begin_if("i64")
    with pytest.raises(EmitError, match="at else"):
        fn.else_()


def test_end_frame_height_mismatch() -> None:
    """A void `if` arm that leaked a value is caught at `end`."""
    fn = _fn()
    fn.i32_const(1)
    fn.begin_if(None)
    fn.i64_const(7)
    with pytest.raises(EmitError, match="at end"):
        fn.end_if()


def test_entry_unreachable_restored_after_a_diverging_arm() -> None:
    fn = _fn()
    fn.i32_const(1)
    fn.begin_if(None)
    fn.i64_const(7)
    fn.ret()
    assert fn.unreachable is True
    fn.else_()
    assert fn.unreachable is False, "else must restore the enclosing frame's state"
    fn.end_if()
    assert fn.unreachable is False, "end must restore the enclosing frame's state"
    # ... and the still-reachable tail is still checked.
    fn.i64_const(7)
    fn.i64_const(9)
    with pytest.raises(EmitError, match="at return"):
        fn.ret()


def test_entry_unreachable_is_restored_to_unreachable_too() -> None:
    """A frame closed while the *enclosing* code was already dead stays dead."""
    fn = _fn()
    fn.i64_const(7)
    fn.ret()
    assert fn.unreachable is True
    fn.i32_const(1)
    fn.begin_if(None)
    fn.end_if()
    assert fn.unreachable is True


def test_expr_scope_catches_a_net_plus_two_lowering() -> None:
    fn = _fn()
    with pytest.raises(EmitError, match="net"), fn.expr_scope(is_void=False):
        fn.i64_const(1)
        fn.i64_const(2)


def test_expr_scope_catches_a_void_expression_that_pushed() -> None:
    fn = _fn()
    with pytest.raises(EmitError, match="net"), fn.expr_scope(is_void=True):
        fn.i64_const(1)


def test_expr_scope_catches_a_value_expression_that_pushed_nothing() -> None:
    fn = _fn()
    with pytest.raises(EmitError, match="net"), fn.expr_scope(is_void=False):
        pass  # a lowering that emitted nothing at all


def test_expr_scope_requires_an_i64_result() -> None:
    fn = _fn()
    with pytest.raises(EmitError, match="i64"), fn.expr_scope(is_void=False):
        fn.i32_const(1)


def test_expr_scope_accepts_the_two_legal_shapes() -> None:
    fn = _fn()
    with fn.expr_scope(is_void=False):
        fn.i64_const(1)
    with fn.expr_scope(is_void=True):
        fn.i64_const(2)
        fn.op(opcodes.DROP)
        fn.pop("i64")
    assert fn.stack == ["i64"]


def test_expr_scope_skips_the_delta_check_when_unreachable() -> None:
    """Heights are meaningless in the polymorphic state; do not invent errors."""
    fn = _fn()
    fn.i64_const(1)
    fn.ret()
    with fn.expr_scope(is_void=False):
        pass  # pushed nothing, but the fn is unreachable
    with fn.expr_scope(is_void=True):
        pass


def test_expr_scope_skips_the_check_when_the_body_diverged() -> None:
    fn = _fn()
    with fn.expr_scope(is_void=False):
        fn.unreachable_()


def test_expr_scope_does_not_mask_an_exception_from_its_body() -> None:
    fn = _fn()
    with pytest.raises(ZeroDivisionError), fn.expr_scope(is_void=False):
        raise ZeroDivisionError


# ===========================================================================
# Exception taxonomy (E15 / review M10)
# ===========================================================================


def test_emit_error_is_a_plain_exception() -> None:
    assert issubclass(EmitError, Exception)
    assert not issubclass(EmitError, AssertionError)


def test_build_limit_error_is_an_emit_error_carrying_a_limit() -> None:
    err = BuildLimitError("pool", "the literal pool has grown into scratch")
    assert isinstance(err, EmitError)
    assert err.limit == "pool"
    assert "literal pool" in str(err)


def test_build_limit_error_rejects_an_unknown_discriminator() -> None:
    for limit in frame.BUILD_LIMITS:
        assert BuildLimitError(limit, "x").limit == limit
    with pytest.raises(EmitError, match="unknown build limit"):
        BuildLimitError("wibble", "x")


def test_frame_py_uses_no_bare_asserts() -> None:
    """P13: the checks are exceptions, not ``assert``, so they survive ``-O``."""
    src = Path(frame.__file__).read_text()
    asserts = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Assert)]
    assert asserts == [], f"bare assert(s) at line(s) {[n.lineno for n in asserts]}"


# ===========================================================================
# Stack bookkeeping
# ===========================================================================


def test_push_and_pop_are_no_ops_when_unreachable() -> None:
    fn = _fn()
    fn.i64_const(1)
    fn.ret()
    fn.push("i64")
    fn.push("i32")
    assert fn.stack == []
    fn.pop("i64")  # no error: the validator has stopped caring
    assert fn.stack == []


def test_binop_and_relop_typing() -> None:
    fn = _fn()
    fn.i64_const(1)
    fn.i64_const(2)
    fn.binop_i64(opcodes.I64_ADD)
    assert fn.stack == ["i64"]
    fn.i64_const(3)
    fn.relop_i64(opcodes.I64_EQ)
    assert fn.stack == ["i32"], "a comparison yields an i32, not an i64"


def test_i64_const_encodes_the_val_bit_pattern_as_signed_leb() -> None:
    """A3: the operand is ``sleb(val.as_i64(v))``, not ``sleb(v)``."""
    fn = _fn(results=())
    fn.i64_const(0xFFFF_FFFF_FFFF_FF07)
    fn.op(opcodes.DROP)
    fn.pop("i64")
    body = _bytes_of(fn.finish())
    # sleb(as_i64(0xFFFF_FFFF_FFFF_FF07)) == sleb(-249) == 87 7E (test_emitter_encode).
    assert body == bytes([opcodes.I64_CONST, 0x87, 0x7E, opcodes.DROP, opcodes.END])


def test_i32_const_encodes_a_plain_signed_leb() -> None:
    fn = _fn(results=())
    fn.i32_const(-1)
    fn.op(opcodes.DROP)
    fn.pop("i32")
    body = _bytes_of(fn.finish())
    assert body == bytes([opcodes.I32_CONST, 0x7F, opcodes.DROP, opcodes.END])


# ===========================================================================
# Locals, including the hidden-temp allocation rule (review M11)
# ===========================================================================


def test_new_local_allocates_after_the_declared_slots() -> None:
    fn = _fn(nparams=2, nlocals_declared=3)  # params 0-1, declared locals 2-4
    assert fn.new_local() == 5
    assert fn.new_local() == 6
    assert fn.nlocals == 5, "3 declared + 2 hidden"
    assert fn.n_hidden == 2


def test_a_declared_slot_never_trips_the_index_check() -> None:
    """review M11: ``local_set(nparams + slot)`` must be legal for every slot."""
    fn = _fn(nparams=2, nlocals_declared=3)
    for slot in range(3):
        fn.i64_const(0)
        fn.local_set(2 + slot)
    assert fn.stack == []


def test_a_hidden_local_is_usable_once_allocated() -> None:
    fn = _fn(nparams=0, nlocals_declared=0)
    with pytest.raises(EmitError, match="local index 0"):
        fn.local_get(0)
    idx = fn.new_local()
    assert idx == 0
    fn.local_get(0)
    assert fn.stack == ["i64"]


def test_local_accessor_bytes_and_stack_effects() -> None:
    fn = _fn(nparams=1, nlocals_declared=0, results=())
    fn.local_get(0)
    assert fn.stack == ["i64"]
    fn.local_tee(0)
    assert fn.stack == ["i64"], "tee leaves the value on the stack"
    fn.local_set(0)
    assert fn.stack == []
    body = _bytes_of(fn.finish())
    assert body == bytes(
        [
            opcodes.LOCAL_GET,
            0x00,
            opcodes.LOCAL_TEE,
            0x00,
            opcodes.LOCAL_SET,
            0x00,
            opcodes.END,
        ]
    )


def test_local_index_is_uleb_encoded() -> None:
    fn = _fn(nparams=200, nlocals_declared=0, results=())
    fn.local_get(199)
    fn.op(opcodes.DROP)
    fn.pop("i64")
    body = _bytes_of(fn.finish())
    assert body == bytes([opcodes.LOCAL_GET]) + encode.uleb(199) + bytes(
        [opcodes.DROP, opcodes.END]
    )
    assert encode.uleb(199) == bytes([0xC7, 0x01])


def test_a_negative_local_index_is_rejected() -> None:
    fn = _fn(nparams=1)
    with pytest.raises(EmitError, match="local index -1"):
        fn.local_get(-1)


# ===========================================================================
# Control frames: kinds, blocktypes, pairing
# ===========================================================================


def test_frame_kinds_exist() -> None:
    assert {k.name for k in FrameKind} == {"BLOCK", "LOOP", "IF"}


def test_if_blocktypes() -> None:
    fn = _fn(results=())
    fn.i32_const(1)
    fn.begin_if(None)
    fn.end_if()
    fn.i32_const(1)
    fn.begin_if("i64")
    fn.i64_const(1)
    fn.else_()
    fn.i64_const(2)
    fn.end_if()
    assert fn.stack == ["i64"], "an if with a result pushes it at end"
    fn.op(opcodes.DROP)
    fn.pop("i64")
    body = _bytes_of(fn.finish())
    assert body == bytes(
        [
            opcodes.I32_CONST,
            0x01,
            opcodes.IF,
            opcodes.BLOCKTYPE_VOID,
            opcodes.END,
            opcodes.I32_CONST,
            0x01,
            opcodes.IF,
            opcodes.BLOCKTYPE_I64,
            opcodes.I64_CONST,
            0x01,
            opcodes.ELSE,
            opcodes.I64_CONST,
            0x02,
            opcodes.END,
            opcodes.DROP,
            opcodes.END,
        ]
    )


def test_begin_if_pops_the_condition() -> None:
    fn = _fn()
    with pytest.raises(EmitError, match="empty operand stack"):
        fn.begin_if(None)


def test_an_unsupported_block_result_type_is_rejected() -> None:
    fn = _fn()
    fn.i32_const(1)
    with pytest.raises(EmitError, match="block result type"):
        fn.begin_if("i32")
    fn2 = _fn()
    with pytest.raises(EmitError, match="block result type"):
        fn2.begin_block("f64")


def test_else_with_no_open_if() -> None:
    fn = _fn()
    with pytest.raises(EmitError, match="no open if"):
        fn.else_()
    fn.begin_block(None)
    with pytest.raises(EmitError, match="no open if"):
        fn.else_()


def test_end_and_end_if_are_not_interchangeable() -> None:
    fn = _fn()
    fn.i32_const(1)
    fn.begin_if(None)
    with pytest.raises(EmitError, match="end_if"):
        fn.end()
    fn2 = _fn()
    fn2.begin_block(None)
    with pytest.raises(EmitError, match="end_if"):
        fn2.end_if()


def test_end_with_no_open_frame() -> None:
    fn = _fn()
    with pytest.raises(EmitError, match="no open control frame"):
        fn.end()
    with pytest.raises(EmitError, match="no open control frame"):
        fn.end_if()


def test_a_block_with_a_result_pushes_it_at_end() -> None:
    fn = _fn()
    fn.begin_block("i64")
    fn.i64_const(1)
    fn.end()
    assert fn.stack == ["i64"]


def test_loop_frames_are_void() -> None:
    fn = _fn(results=())
    fn.begin_loop()
    fn.end()
    body = _bytes_of(fn.finish())
    assert body == bytes([opcodes.LOOP, opcodes.BLOCKTYPE_VOID, opcodes.END, opcodes.END])


# ===========================================================================
# br depths and br-target arity (dossier: "where an off-by-one becomes a
# silently wrong branch")
# ===========================================================================


def test_while_shape_br_depths() -> None:
    """`while` is block{loop{...}}: `break` is br 1, `continue` is br 0."""
    fn = _fn(results=())
    fn.begin_block(None)
    fn.begin_loop()
    fn.br_continue()
    fn.end()
    fn.end()
    assert _bytes_of(fn.finish()) == bytes(
        [
            opcodes.BLOCK,
            opcodes.BLOCKTYPE_VOID,
            opcodes.LOOP,
            opcodes.BLOCKTYPE_VOID,
            opcodes.BR,
            0x00,
            opcodes.END,
            opcodes.END,
            opcodes.END,
        ]
    )

    fn2 = _fn(results=())
    fn2.begin_block(None)
    fn2.begin_loop()
    fn2.br_break()
    fn2.end()
    fn2.end()
    assert _bytes_of(fn2.finish())[4:6] == bytes([opcodes.BR, 0x01])


def test_br_depths_from_inside_a_nested_if() -> None:
    """block{loop{if{break}else{continue}}}: br 2 and br 1."""
    fn = _fn(results=())
    fn.begin_block(None)
    fn.begin_loop()
    fn.i32_const(1)
    fn.begin_if(None)
    fn.br_break()
    fn.else_()
    fn.br_continue()
    fn.end_if()
    fn.end()
    fn.end()
    assert _bytes_of(fn.finish()) == bytes(
        [
            opcodes.BLOCK,
            opcodes.BLOCKTYPE_VOID,
            opcodes.LOOP,
            opcodes.BLOCKTYPE_VOID,
            opcodes.I32_CONST,
            0x01,
            opcodes.IF,
            opcodes.BLOCKTYPE_VOID,
            opcodes.BR,
            0x02,
            opcodes.ELSE,
            opcodes.BR,
            0x01,
            opcodes.END,
            opcodes.END,
            opcodes.END,
            opcodes.END,
        ]
    )


def test_br_break_targets_the_nearest_enclosing_block_not_the_outermost() -> None:
    fn = _fn(results=())
    fn.begin_block(None)  # outer
    fn.begin_block(None)  # inner: the nearest block
    fn.begin_loop()
    fn.br_break()
    fn.end()
    fn.end()
    fn.end()
    assert _bytes_of(fn.finish())[6:8] == bytes([opcodes.BR, 0x01])


def test_br_continue_targets_the_nearest_enclosing_loop() -> None:
    fn = _fn(results=())
    fn.begin_loop()  # outer loop
    fn.begin_block(None)
    fn.begin_loop()  # inner: the nearest loop
    fn.br_continue()
    fn.end()
    fn.end()
    fn.end()
    assert _bytes_of(fn.finish())[6:8] == bytes([opcodes.BR, 0x00])


def test_br_break_to_a_block_with_a_result_needs_the_value() -> None:
    fn = _fn()
    fn.begin_block("i64")
    with pytest.raises(EmitError, match="at br_break"):
        fn.br_break()


def test_br_break_to_a_block_with_a_result_accepts_the_value() -> None:
    fn = _fn()
    fn.begin_block("i64")
    fn.i64_const(1)
    fn.br_break()
    assert fn.unreachable is True, "code after an unconditional br is dead"


def test_br_break_rejects_a_leaked_operand() -> None:
    fn = _fn()
    fn.begin_block(None)
    fn.i64_const(1)
    with pytest.raises(EmitError, match="at br_break"):
        fn.br_break()


def test_br_continue_requires_an_empty_to_base_stack() -> None:
    """A `br` to a loop targets the loop HEADER, whose arity is its params: none."""
    fn = _fn()
    fn.begin_loop()
    fn.i64_const(1)
    with pytest.raises(EmitError, match="at br_continue"):
        fn.br_continue()


def test_br_arity_checks_are_skipped_when_unreachable() -> None:
    fn = _fn(results=())
    fn.begin_block(None)
    fn.begin_loop()
    fn.unreachable_()
    fn.br_break()  # heights are meaningless here
    fn.br_continue()
    fn.end()
    fn.end()
    fn.finish()


def test_unreachable_emits_the_opcode_and_poisons_the_stack() -> None:
    fn = _fn()
    fn.unreachable_()
    assert fn.unreachable is True
    body = _bytes_of(fn.finish())
    assert body == bytes([opcodes.UNREACHABLE, opcodes.END])


# ===========================================================================
# Symbolic call sites (review B1)
# ===========================================================================


def test_call_import_is_symbolic_and_does_full_stack_accounting() -> None:
    fn = _fn()
    fn.i64_const(1)
    fn.i64_const(2)
    fn.call_import("obj_cmp", 2, has_result=True)
    assert fn.stack == ["i64"]
    fn.ret()
    items = fn.finish()
    assert items == [
        bytes([opcodes.I64_CONST, 0x01, opcodes.I64_CONST, 0x02]),
        CallImport("obj_cmp"),
        bytes([opcodes.RETURN, opcodes.END]),
    ]


def test_no_call_opcode_or_index_byte_is_baked_at_lowering_time() -> None:
    """review B1: the 0x10 and its uleb index are pass-2's job, not ours."""
    fn = _fn()
    fn.i64_const(1)
    fn.call_import("obj_len", 1, has_result=True)
    fn.call_defined(3, 1, ("i64",))
    fn.ret()
    for item in fn.finish():
        if isinstance(item, bytes):
            assert opcodes.CALL not in item


def test_call_import_with_no_result_pushes_nothing() -> None:
    fn = _fn(results=())
    fn.i64_const(1)
    fn.call_import("fail_with_error", 1, has_result=False)
    assert fn.stack == []
    assert fn.finish() == [
        bytes([opcodes.I64_CONST, 0x01]),
        CallImport("fail_with_error"),
        bytes([opcodes.END]),
    ]


def test_call_defined_records_a_defidx_and_pushes_per_result() -> None:
    fn = _fn()
    fn.i64_const(1)
    fn.i64_const(2)
    fn.call_defined(7, 2, ("i64",))
    assert fn.stack == ["i64"]
    items = fn.finish()
    assert CallDefined(7) in items


def test_call_defined_void_helper_pushes_nothing() -> None:
    fn = _fn(results=())
    fn.call_defined(0, 0, ())
    assert fn.stack == []


def test_a_call_underflows_the_stack_loudly() -> None:
    fn = _fn()
    fn.i64_const(1)
    with pytest.raises(EmitError, match="empty operand stack"):
        fn.call_import("obj_cmp", 2, has_result=True)


def test_call_argument_types_are_checked() -> None:
    fn = _fn()
    fn.i32_const(1)
    with pytest.raises(EmitError, match="expected i64"):
        fn.call_import("obj_len", 1, has_result=True)


def test_call_site_items_are_frozen_and_value_typed() -> None:
    assert CallImport("a") == CallImport("a")
    assert CallDefined(1) == CallDefined(1)
    assert CallImport("a") != CallImport("b")
    assert len({CallImport("a"), CallImport("a")}) == 1
    # The two site kinds never compare equal, which is what lets pass 2
    # dispatch on the item type alone.
    mixed: list[CodeItem] = [CallImport("a"), CallDefined(1), b"\x00"]
    assert mixed[0] != mixed[1]
    assert mixed[0] != mixed[2]
    with pytest.raises(dataclasses.FrozenInstanceError):
        CallImport("a").name = "b"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        CallDefined(1).defidx = 2  # type: ignore[misc]


def test_a_negative_defidx_is_rejected() -> None:
    fn = _fn()
    with pytest.raises(EmitError, match="defidx"):
        fn.call_defined(-1, 0, ("i64",))


def test_a_negative_nargs_is_rejected() -> None:
    fn = _fn()
    with pytest.raises(EmitError, match="nargs"):
        fn.call_import("obj_len", -1, has_result=True)


# ===========================================================================
# ret() and finish()
# ===========================================================================


def test_ret_pops_one_i64_for_a_value_returning_function() -> None:
    fn = _fn()
    fn.i64_const(1)
    fn.ret()
    assert fn.stack == []
    assert _bytes_of(fn.finish()) == bytes([opcodes.I64_CONST, 0x01, opcodes.RETURN, opcodes.END])


def test_ret_pops_nothing_for_a_void_helper() -> None:
    """review M2: a `results=()` helper's `return` takes no operand."""
    fn = _fn(results=())
    fn.ret()
    assert _bytes_of(fn.finish()) == bytes([opcodes.RETURN, opcodes.END])


def test_ret_from_an_empty_stack_in_a_value_function() -> None:
    fn = _fn()
    with pytest.raises(EmitError, match="empty operand stack"):
        fn.ret()


def test_ret_balance_check_is_relative_to_the_innermost_frame() -> None:
    fn = _fn()
    fn.i64_const(1)  # a live operand of the *enclosing* code
    fn.begin_block("i64")
    fn.i64_const(2)
    fn.ret()  # legal: the block's base is 1, and we popped back down to it
    fn.i64_const(3)
    fn.end()
    assert fn.stack == ["i64", "i64"]


def test_finish_returns_the_item_list_with_a_trailing_end() -> None:
    fn = _fn()
    fn.i64_const(1)
    fn.ret()
    items = fn.finish()
    assert isinstance(items, list)
    assert _bytes_of(items)[-1] == opcodes.END


def test_finish_accepts_an_unreachable_body_with_any_stack() -> None:
    fn = _fn()
    fn.unreachable_()
    fn.push("i64")
    fn.finish()


def test_finish_requires_the_stack_to_equal_the_results() -> None:
    fn = _fn()
    with pytest.raises(EmitError, match="end of the body"):
        fn.finish()  # results=("i64",) but nothing was produced


def test_finish_accepts_a_falling_off_the_end_value_body() -> None:
    fn = _fn()
    fn.i64_const(1)
    assert _bytes_of(fn.finish()) == bytes([opcodes.I64_CONST, 0x01, opcodes.END])


def test_finish_is_single_shot() -> None:
    fn = _fn(results=())
    fn.finish()
    with pytest.raises(EmitError, match="twice"):
        fn.finish()


def test_adjacent_bytes_are_coalesced() -> None:
    fn = _fn()
    for v in range(5):
        fn.i64_const(v)
        fn.op(opcodes.DROP)
        fn.pop("i64")
    fn.i64_const(1)
    fn.ret()
    items = fn.finish()
    assert len(items) == 1, "no symbolic item, so the whole body is one bytes run"


def test_results_must_be_void_or_one_i64() -> None:
    with pytest.raises(EmitError, match="results"):
        Fn(name="f", nparams=0, nlocals_declared=0, results=("i32",))
    with pytest.raises(EmitError, match="results"):
        Fn(name="f", nparams=0, nlocals_declared=0, results=("i64", "i64"))


def test_negative_shape_parameters_are_rejected() -> None:
    with pytest.raises(EmitError, match="nparams"):
        Fn(name="f", nparams=-1, nlocals_declared=0, results=())
    with pytest.raises(EmitError, match="nlocals_declared"):
        Fn(name="f", nparams=0, nlocals_declared=-1, results=())


# ===========================================================================
# One end-to-end shape: a function that is all of the above at once
# ===========================================================================


def test_a_realistic_body_tracks_end_to_end() -> None:
    """`def f(x): while True: if x == 0: break; return x` shaped lowering."""
    fn = _fn(name="f", nparams=1, nlocals_declared=0)
    fn.begin_block(None)
    fn.begin_loop()
    fn.local_get(0)
    fn.i64_const(0)
    fn.relop_i64(opcodes.I64_EQ)
    fn.begin_if(None)
    fn.br_break()
    fn.end_if()
    fn.br_continue()
    fn.end()
    fn.end()
    tmp = fn.new_local()
    fn.local_get(0)
    fn.local_set(tmp)
    fn.local_get(tmp)
    fn.ret()
    items = fn.finish()
    assert fn.nlocals == 1
    assert _bytes_of(items)[-2:] == bytes([opcodes.RETURN, opcodes.END])
