"""Tier 2b: the frozen semantics table, run against the REAL host (dossier O9, ruling E10).

`tests/unit/test_emitter_semantics.py` runs the 35 in-scope cases under the mini
host; this module runs the SAME wrapped modules on the embedded soroban-env-host
and compares to tier 1. Where the mini host is "a pin so the disagreement is a
five-second diff" (its Symbol-ordering test's own words), this is the diff.

Escalation is structural: a `value` case whose real answer differs from tier 1's
raises `FrozenTableDisagreement` -- the implementer returns BLOCKED and the
controller rules (E10). Nobody edits `cases.py` or `serpent.types` to make this
green.

Three things about the shape of this module are decisions rather than taste:

* the wasm comes from `test_emitter_semantics.build_case`, not from a second
  `compile_module`/`build_wasm` pair spelled out here (BL-3). That helper is
  what reconciles the B5 two-step wrapper -- step 1 reads the expression's `Ty`
  off an unannotated `x = <source>`, step 2 re-wraps the same source with
  `annotation_of(ty)` in the RETURN position and asserts the round trip closed.
  Re-deriving those two steps here would give tier 2b a wrapper that could
  drift from tier 2's, which is exactly the divergence the differential exists
  to find;
* the ordering vector (O12) keeps two failure modes apart (review B1). A host
  that REFUSES the compare is an emitter bug -- Task 0's territory, BLOCKED
  under E16 -- and a host that ANSWERS differently is the frozen-table
  escalation (E10). One `except RealHostError` is the whole difference;
* the frame-level `(error_type, code)` of a guest-side failure is
  `("Context", 6)` for EVERY trap (review B5), so it carries no information and
  the evidence is the UNDERLYING diagnostic. `EXPECTED_UNDERLYING_ERROR` is the
  measured table, with the two meta-tests (coverage, non-vacuity) that keep it
  honest.

The `Option`/`Symbol` equality probes at the foot of the file are ruling C1's
explicit re-check that Task 0's compare guard holds on the real host: the mini
host cannot see this failure, because it never refused `obj_cmp` on two small
operands in the first place.
"""

from __future__ import annotations

import pytest

from serpent import U32, Bool, Symbol, val
from serpent.compiler.frontend import compile_module
from serpent.compiler.types_ import Ty
from serpent.emitter import build_wasm
from serpent.testing import FrozenTableDisagreement, RealContractError, RealEnv, RealHostError
from serpent.testing._scval import from_xdr
from tests.semantics.cases import SemCase
from tests.semantics.test_semantics import _eval_case
from tests.unit.test_emitter_semantics import (
    IN_SCOPE,
    IN_SCOPE_COUNT,
    annotation_of,
    build_case,
)

real = pytest.mark.real_host  # per-test (review M12): the meta-tests below run everywhere


def _run(case: SemCase) -> tuple[Ty, bytes]:
    """(the compiler's Ty for the expression, the raw ScVal XDR the host returned)."""
    ty, wasm = build_case(case)
    env = RealEnv()
    address = env.register_raw(wasm, ())
    return ty, env.invoke_raw(address, "go", ())  # undecoded: the decode is THIS test's assertion


def test_the_in_scope_count_is_unchanged() -> None:
    assert len(IN_SCOPE) == IN_SCOPE_COUNT == 35


_VALUE = [c for c in IN_SCOPE if c.kind == "value"]
_ERROR = [c for c in IN_SCOPE if c.kind == "contract_error"]
_TRAP = [c for c in IN_SCOPE if c.kind == "trap"]


def test_the_three_partitions_union_to_the_whole_in_scope_set() -> None:
    """Step 4's requirement: all 35 in-scope cases really are exercised here.

    The same guard `test_emitter_semantics` carries for tier 2 -- a typo in one
    of the three `kind` filters would leave its cases built and never asserted,
    which is a silent skip in every way that matters.
    """
    partitioned = [*_VALUE, *_ERROR, *_TRAP]
    assert sorted(c.name for c in partitioned) == sorted(c.name for c in IN_SCOPE)
    assert len(partitioned) == len(IN_SCOPE)  # no case counted twice


@real
@pytest.mark.parametrize("case", _VALUE, ids=[c.name for c in _VALUE])
def test_a_value_case_answers_as_tier_1_does(case: SemCase) -> None:
    ty, raw = _run(case)
    answered = from_xdr(raw, _authoring_type(ty))
    tier1 = _eval_case(case.source)
    if answered != tier1 or type(answered) is not type(tier1):
        raise FrozenTableDisagreement(
            f"{case.name}: real host answered {answered!r} ({type(answered).__name__}), tier 1 "
            f"answered {tier1!r} ({type(tier1).__name__}); controller decision required "
            "(ruling E10)"
        )
    assert answered == case.expect


@real
def test_the_symbol_ordering_vector_on_the_real_host() -> None:
    """THE top differential vector (O12): `Symbol("_") < Symbol("A")`.

    Two failure modes are kept apart (review B1): the host REFUSING the compare is
    an emitter bug (Task 0's territory -- BLOCKED under E16, not a table matter);
    the host ANSWERING differently is the frozen-table escalation (E10).
    """
    (case,) = [c for c in IN_SCOPE if c.name == "symbol_underscore_vs_A_ascii_order"]
    try:
        ty, raw = _run(case)
    except RealHostError as exc:
        raise AssertionError(
            f"the host refused the Symbol compare ({exc.underlying}): an emitter bug, not a table "
            "disagreement -- Task 0 must have landed first"
        ) from exc
    answered = from_xdr(raw, _authoring_type(ty))
    assert val.symbol_char_code("_") < val.symbol_char_code("A")  # the 6-bit codes DO disagree
    if answered != case.expect:
        raise FrozenTableDisagreement(
            f"symbol ordering: real host says {answered!r}, the frozen table says {case.expect!r}; "
            "controller decision on the frozen table required (dossier O12, ruling E10)"
        )


@real
def test_the_hosts_compare_trait_agrees_with_the_compiled_answer() -> None:
    """M2: the same question asked of the host directly, no contract in between."""
    (case,) = [c for c in IN_SCOPE if c.name == "symbol_underscore_vs_A_ascii_order"]
    verdict = RealEnv().compare(Symbol("_"), Symbol("A"))
    assert (verdict < 0) == bool(case.expect), (
        f"compare says {verdict}, the table says {case.expect!r}"
    )


@real
@pytest.mark.parametrize("case", _ERROR, ids=[c.name for c in _ERROR])
def test_a_contract_error_case_aborts_with_the_same_code(case: SemCase) -> None:
    _ty, wasm = build_case(case)
    env = RealEnv()
    address = env.register_raw(wasm, ())
    with pytest.raises(RealContractError) as info:
        env.invoke_raw(address, "go", ())
    assert info.value.code == case.code
    assert info.value.error_type == "Contract"


@real
@pytest.mark.parametrize("case", _TRAP, ids=[c.name for c in _TRAP])
def test_a_trap_case_is_a_non_contract_host_error(case: SemCase) -> None:
    """A trap on chain is a HOST error, never a contract code. The frame-level
    type is Context/InvalidAction for EVERY guest failure (review B5), so the
    evidence is the UNDERLYING diagnostic."""
    _ty, wasm = build_case(case)
    env = RealEnv()
    address = env.register_raw(wasm, ())
    with pytest.raises(RealHostError) as info:
        env.invoke_raw(address, "go", ())
    assert not isinstance(info.value, RealContractError)
    assert info.value.underlying == EXPECTED_UNDERLYING_ERROR[case.name], (
        f"{case.name}: the host reported frame {info.value.error_type}/{info.value.code}, "
        f"underlying {info.value.underlying}"
    )


#: The UNDERLYING (ScErrorType, ScErrorCode) the real host's diagnostics report
#: per trap case. FILLED FROM THE FIRST RUN and then frozen, with the run date in
#: a comment: these are host facts this repo did not have before (dossier
#: O11/O25).
#:
#: MEASURED 2026-09-02 against the embedded soroban-env-host at
#: `DEFAULT_PROTOCOL == 28`, one row per `kind == "trap"` in-scope case. The
#: frame-level pair was `("Context", 6)` for all six, which is why the
#: assertion above reads `underlying` and reports the frame only in the failure
#: message.
#:
#: Three distinct pairs, and the split tracks WHERE the refusal happened rather
#: than anything about the table:
#:
#: * `("WasmVm", "ArithDomain")` -- an `i32.div_s`/`i32.rem_s`/`i32.div_u` by
#:   zero is a wasm INSTRUCTION-level trap with no host call in it at all, and
#:   the host classifies the trapping VM as the subsystem with the arithmetic
#:   domain as the cause. Note this is NOT the `("WasmVm", "InvalidAction")` a
#:   rejected module gets (`test_real_env.py`'s panic test): the host really
#:   does distinguish "this VM did something arithmetically undefined" from
#:   "this VM is not usable", so the row carries information;
#: * `("Object", "IndexBounds")` -- a host-function precondition on an INDEX
#:   (`bytes_get` past the end, `vec_get` out of bounds);
#: * `("Object", "MissingValue")` -- `map_get` on a key the map does not hold,
#:   which is a missing entry rather than an out-of-range index. The brief
#:   predicted `IndexBounds` here; the host says otherwise, and the host is the
#:   authority these rows exist to record.
EXPECTED_UNDERLYING_ERROR: dict[str, tuple[str, str]] = {
    "floordiv_by_zero_traps": ("WasmVm", "ArithDomain"),
    "mod_by_zero_traps": ("WasmVm", "ArithDomain"),
    "unsigned_floordiv_by_zero_traps": ("WasmVm", "ArithDomain"),
    "bytes_positive_out_of_range_traps": ("Object", "IndexBounds"),
    "vec_get_out_of_bounds_traps": ("Object", "IndexBounds"),
    "map_get_missing_key_traps": ("Object", "MissingValue"),
}


def test_expected_underlying_error_covers_exactly_the_trap_cases() -> None:
    assert set(EXPECTED_UNDERLYING_ERROR) == {c.name for c in _TRAP}


def test_the_expected_underlying_errors_are_not_all_identical() -> None:
    """B5's vacuity guard: a map of six identical rows is not evidence."""
    assert len(set(EXPECTED_UNDERLYING_ERROR.values())) > 1


def _authoring_type(ty: Ty) -> object:
    """The compiler's `Ty` as the class/alias `from_xdr` decodes with."""
    import serpent  # the authoring namespace, exactly what a contract imports

    return eval(annotation_of(ty), {**vars(serpent), "None": None})


# --- ruling C1: Option and Symbol equality no longer trap on the real host ---
#
# Task 0 fixed the emitter's small-operand compare lowering: the real host
# refuses `obj_cmp` when BOTH operands are non-object `Val`s, so `==` on two
# small values (an empty `Option`, a short `Symbol`) used to trap there while
# passing under the mini host, which never had that precondition. The frozen
# table has no row for either shape, so these live here as extra tests rather
# than as table rows -- an explicit real-host probe that the guard holds.

_C1_PROBE = """\
from serpent import U32, Bool, Env, Symbol, contract


@contract
class C:
    def eq(self, env: Env, a: U32 | None, b: U32 | None) -> Bool:
        return Bool(a == b)

    def small_symbols_equal(self, env: Env) -> Bool:
        return Bool(Symbol("ab") == Symbol("ab"))

    def small_eq_object_symbol(self, env: Env) -> Bool:
        return Bool(Symbol("ab") == Symbol("abcdefghijk"))
"""


@real
def test_option_equality_does_not_trap_on_the_real_host() -> None:
    """All four `Option` shapes, including the two-empties case (both small)."""
    wasm = build_wasm(compile_module(_C1_PROBE, "semantics_real/c1_probe.py")).wasm
    c = RealEnv().deploy_wasm(wasm)
    assert c.invoke("eq", U32(1), U32(1)) == Bool(True)
    assert c.invoke("eq", U32(1), U32(2)) == Bool(False)
    assert c.invoke("eq", U32(1), None) == Bool(False)
    assert c.invoke("eq", None, None) == Bool(True)


@real
def test_symbol_equality_does_not_trap_on_the_real_host() -> None:
    """Small-vs-small (the shape the host refuses `obj_cmp` for) and small-vs-object.

    `Symbol("abcdefghijk")` is eleven characters, so it cannot be a
    `SymbolSmall` (nine is the limit) -- which makes the second call the mixed
    small/object pair, the one arm that always reached `obj_cmp` legally.
    """
    wasm = build_wasm(compile_module(_C1_PROBE, "semantics_real/c1_probe.py")).wasm
    c = RealEnv().deploy_wasm(wasm)
    assert c.invoke("small_symbols_equal") == Bool(True)
    assert c.invoke("small_eq_object_symbol") == Bool(False)
