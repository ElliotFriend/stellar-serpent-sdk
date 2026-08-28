"""Tier 2: the frozen semantics table, run against COMPILED WASM (spec §10).

This is the differential half of the cross-tier oracle. `tests/semantics/
cases.py` is the frozen table; `tests/semantics/test_semantics.py` runs every
case as plain Python (tier 1); `tests/unit/test_frontend_semantics.py`
classifies every case against the real frontend (compile/reject/type). This
module closes the loop: it compiles each in-scope case's `source` into a
contract method, builds a real wasm module with `build_wasm`, runs it under
`tests/harness`, and asserts the OBSERVED on-chain behavior against tier 1's
answer for the same source.

**The in-scope predicate (F.2.1/S18), stated here rather than inherited**::

    case.kind in {"value", "contract_error", "trap"}
        and not case.tier1_only
        and case.frontend != "not_expressible"

35 cases today, and `test_the_in_scope_set_is_the_thirty_five_cases_the_plan_
counted` asserts the COUNT so that a change to the table is loud here instead
of quietly shrinking the differential (review M15). `tests/unit/
test_harness_hostfns.py` states the same predicate for the binding inventory --
deliberately, because that test is what makes this module's **empty skip list**
reachable: every host function every in-scope case reaches has a callback, so a
case that cannot run is a plan bug, not a skip.

## The two-step wrapper (review B5)

`test_frontend_semantics.wrap_case` deliberately writes `x = <source>` and
never returns the value -- an unannotated assignment is what lets the checker
INFER the expression's type instead of being told it. That is exactly right for
the frontend classification and useless for a differential: a method that
returns nothing has no observable answer.

So the harness compiles each case TWICE:

1. `compile_case(case)` (imported, not re-derived -- BL-3's "one harness, not
   two that could drift apart") and `_local_ty` read the `LetLocal`'s own `Ty`.
   That `Ty` is the COMPILER's answer about the expression, arrived at with no
   declared return type to satisfy.
2. `annotation_of(ty)` spells that `Ty` back as an AUTHORING-SURFACE
   annotation, and `wrap_returning` re-wraps the same `source` as
   `def go(self, env: Env) -> <annotation>: return <source>` -- which is a real
   contract method with a real ABI, exportable and callable.

Step 1 is what keeps step 2 honest: the return annotation is not a guess this
file makes about each case, it is read out of the compiler, so a case whose
type nobody predicted still gets the right wrapper (and a case whose type the
compiler gets WRONG fails in `test_frontend_semantics.py`'s `EXPECTED_TY`
table, which is derived by inspection rather than by re-running).

## Decoding the answer

`decode_val(word, ty, host)` turns the returned `Val` word back into a tier-1
chain value. Two steps, both load-bearing:

* the word's TAG is checked against `_VAL_TAGS_FOR[ty.tag]` -- the set of `Val`
  forms a value of that type may legally arrive in (A3's small/object split).
  This is the assertion that catches an emitter which returned the right NUMBER
  under the wrong tag: `U32(1)` and `I32(1)` have different tags and the same
  body, and a client reading the wrong one gets a plausible answer.
* then `ObjectStore.chain_value` does the decode itself -- the harness's own
  codec, proved both directions in `test_harness_hostfns.py`, so a small
  immediate is unpacked through `serpent.val` and an object handle is read out
  of the store.

Comparison is by the ORACLE's own equality (`==` on the chain classes, which is
payload equality per `types/_base.py`), against BOTH `case.expect` and tier 1's
own `eval` of the same source. Both, because they are different claims: `expect`
is the frozen table entry, and the `eval` is what tier 1 actually does today.

## What a green run here does and does not mean

Ruling E1: `tests/harness` is NOT the chain. A green run means the compiled
module's behavior agrees with tier 1 under a mini host that mirrors tier 1 --
"the codegen is self-consistent", not "this contract is correct on chain".
Sub-plan F re-runs this against the real Soroban host, and the flagged
divergence vector (`symbol_underscore_vs_A_ascii_order`) is pinned below
precisely so the day the real host disagrees, it is a one-line diff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest
import wasmtime

from serpent import errors, val
from serpent.compiler.frontend import compile_module
from serpent.compiler.types_ import Ty, TyTag
from serpent.emitter import build_wasm
from serpent.types._ordering import ChainValue
from tests.harness import engine
from tests.harness.hostfns import FullHost
from tests.semantics.cases import CASES, SemCase

# `_eval_case` is tier 1's own evaluator (a `serpent.__all__`-only namespace);
# `compile_case`/`_local_ty`/`_IMPORTABLE` are the Task 11a wrapping harness.
# Imported rather than re-derived, per BL-3: one harness, not two that could
# drift apart. (`tests/semantics/test_semantics.py` already imports from
# `test_frontend_semantics` the same way.)
from tests.semantics.test_semantics import _eval_case
from tests.unit.test_frontend_semantics import _IMPORTABLE, _local_ty, compile_case
from tests.unit.test_harness_hostfns import _in_scope as inventory_predicate

# --- the in-scope predicate (F.2.1/S18) --------------------------------------


def in_scope(case: SemCase) -> bool:
    """The differential's scope, verbatim from the plan.

    * `kind == "reject"` is out BY CONSTRUCTION (`cases.py`'s own kind
      docstring): the contract never compiles, so there is no compiled
      behavior to compare.
    * `tier1_only` is out because the compiler statically rejects the source
      (F.2.2 makes that biconditional with `frontend == "rejects"`).
    * `frontend == "not_expressible"` is out because the case has no compiled
      form at all -- each one carries a one-line reason saying why.
    """
    return (
        case.kind in {"value", "contract_error", "trap"}
        and not case.tier1_only
        and case.frontend != "not_expressible"
    )


IN_SCOPE: list[SemCase] = [case for case in CASES if in_scope(case)]

#: The number the plan counted. Asserted rather than merely computed (review
#: M15): the table is FROZEN, so a change to its size is a controller decision,
#: and a differential that silently ran 34 cases instead of 35 would still be
#: green.
IN_SCOPE_COUNT = 35


def test_the_in_scope_set_is_the_thirty_five_cases_the_plan_counted() -> None:
    assert len(IN_SCOPE) == IN_SCOPE_COUNT


def test_the_in_scope_set_matches_the_binding_inventorys_own_predicate() -> None:
    """`test_harness_hostfns.py` states the same predicate to prove every
    in-scope case is fully bound -- which is what makes the skip list below
    EMPTY. If the two predicates ever disagree, one of them is proving
    something about a different set of cases than the other tests."""
    assert [case.name for case in IN_SCOPE] == [
        case.name for case in CASES if inventory_predicate(case)
    ]


def test_every_in_scope_case_is_asserted_by_exactly_one_kind_partition() -> None:
    """ "The skip list is EMPTY" (the plan's requirement), as a real check.

    A `skipif`-counting test would be vacuous -- there is nothing to count.
    What CAN go wrong is a case that reaches no per-kind assertion at all: the
    three parametrized behavior tests below split `IN_SCOPE` by `kind`, and a
    new kind (or a typo in one of the filters) would leave its cases built and
    instantiated but with their BEHAVIOR never asserted, which is a silent skip
    in every way that matters. The B6 `Address` fix (Task 7) is what made the
    last of the 35 reachable in the first place.
    """
    partitioned = [*_VALUE_CASES, *_ERROR_CASES, *_TRAP_CASES]
    assert sorted(case.name for case in partitioned) == sorted(case.name for case in IN_SCOPE)
    assert len(partitioned) == len(IN_SCOPE)  # no case counted twice


# --- step 1 of the B5 two-step wrapper: what type IS this expression? --------


def observed_ty(case: SemCase) -> Ty:
    """The `Ty` the compiler gives `case.source`, read off the `LetLocal`.

    `compile_case` wraps the source as `x = <source>` with no annotation, so
    this is the compiler's own inference and not a type this file asserted.
    """
    return _local_ty(compile_case(case))


# --- annotation_of: a `Ty`, spelled as an authoring-surface annotation -------

#: `TyTag` -> the `serpent.__all__` name an author writes for it. This is the
#: AUTHORING surface, not `Ty.render()`'s diagnostic notation -- the two agree
#: for every scalar and disagree for every parametrized type (`Ty.render()`
#: says `Vec(U32)`, an author writes `Vec[U32]`), which is why this mapping
#: exists rather than reusing `render()`.
_SCALAR_ANNOTATION: dict[TyTag, str] = {
    TyTag.BOOL: "Bool",
    TyTag.U32: "U32",
    TyTag.I32: "I32",
    TyTag.U64: "U64",
    TyTag.I64: "I64",
    TyTag.U128: "U128",
    TyTag.I128: "I128",
    TyTag.TIMEPOINT: "Timepoint",
    TyTag.DURATION: "Duration",
    TyTag.SYMBOL: "Symbol",
    TyTag.STRING: "String",
    TyTag.BYTES: "Bytes",
    TyTag.ADDRESS: "Address",
}


def annotation_of(ty: Ty) -> str:
    """One `Ty`, as the annotation an author would write for it.

    The parametrized spellings are subscript generics and a union, because that
    is what `resolve_annotation` consumes (`types_.py`'s own docstring lists
    them: "a chain-type class, a `Vec[T]`/`Map[K, V]` generic alias, a
    `X | None` union, ... `bytes_n(N)`'s generated subclass").

    * `Ty.BYTES_N` -> `bytes_n(N)`, the factory CALL: `Bytes32`/`Bytes64` are
      the same objects as `bytes_n(32)`/`bytes_n(64)` (`buffers.py` guarantees
      `bytes_n(32) is Bytes32`), so the factory spelling is correct for every
      length including those two and needs no special case.
    * `Ty.VOID` -> `None`, the only annotation a void return has (`Ty.Void` is
      constructed for that position directly, never resolved from an
      annotation).
    * `Ty.STRUCT`/`Ty.ERROR_ENUM` have no spelling this helper can produce: the
      name is a class in the CONTRACT's own module, which a semantics case
      never declares. Raising beats returning `ty.name` and emitting a module
      with an undefined name in it.
    """
    if ty.tag is TyTag.BYTES_N:
        assert ty.n is not None
        return f"bytes_n({ty.n})"
    if ty.tag is TyTag.VEC:
        assert ty.elem is not None
        return f"Vec[{annotation_of(ty.elem)}]"
    if ty.tag is TyTag.MAP:
        assert ty.key is not None and ty.value is not None
        return f"Map[{annotation_of(ty.key)}, {annotation_of(ty.value)}]"
    if ty.tag is TyTag.OPTION:
        assert ty.elem is not None
        return f"{annotation_of(ty.elem)} | None"
    if ty.tag is TyTag.VOID:
        return "None"
    if ty.tag not in _SCALAR_ANNOTATION:
        raise AssertionError(
            f"{ty.render()} has no authoring-surface annotation this harness can spell: "
            "a struct or an error enum names a class declared in the contract's own "
            "module, which a semantics case never declares"
        )
    return _SCALAR_ANNOTATION[ty.tag]


# --- step 2 of the B5 two-step wrapper: a method that RETURNS the value ------


def _imports_for(source: str, annotation: str) -> tuple[str, ...]:
    """Every public name the wrapped module mentions, plus `Env`/`contract`.

    The ANNOTATION is scanned as well as the source, which is not a detail:
    `Bytes(b"ab")[5]` has type `U32` (`buffers.py`'s `__getitem__` answers
    `U32`) and mentions `U32` nowhere, so a scan of the source alone would
    emit a module whose return annotation is an undefined name.
    """
    text = f"{source}\n{annotation}"
    names = {name for name in _IMPORTABLE if re.search(rf"\b{re.escape(name)}\b", text)}
    names.add("Env")
    names.add("contract")
    return tuple(sorted(names))


def wrap_returning(source: str, annotation: str) -> str:
    """One case's `source` as the RETURN of a minimal `@contract` method.

    The B5 counterpart to `wrap_case`: same minimal scaffold, but the value
    leaves the method, which is the whole point here. Nothing else is added --
    no parameters, no storage, no helper -- so what runs is the case's
    expression and the ABI around it.
    """
    imports = ", ".join(_imports_for(source, annotation))
    return (
        f"from serpent import {imports}\n\n\n"
        "@contract\n"
        "class C:\n"
        f"    def go(self, env: Env) -> {annotation}:\n"
        f"        return {source}\n"
    )


# --- decode_val: a returned `Val` word, back to a tier-1 chain value --------

#: `TyTag` -> every `Val` tag a value of that type may legally arrive in (A3's
#: small/object split, mirroring `Ty.repr_form` but as the concrete tag sets
#: `serpent.val` defines). Written out rather than derived from `repr_form`
#: because it is the INDEPENDENT statement of the same fact: a decoder that
#: asked `repr_form` would agree with the compiler by construction and so could
#: not catch the compiler tagging a value wrongly.
_VAL_TAGS_FOR: dict[TyTag, frozenset[int]] = {
    TyTag.BOOL: frozenset({val.TAG_FALSE, val.TAG_TRUE}),
    TyTag.U32: frozenset({val.TAG_U32}),
    TyTag.I32: frozenset({val.TAG_I32}),
    TyTag.U64: frozenset({val.TAG_U64_SMALL, val.TAG_U64_OBJECT}),
    TyTag.I64: frozenset({val.TAG_I64_SMALL, val.TAG_I64_OBJECT}),
    TyTag.U128: frozenset({val.TAG_U128_SMALL, val.TAG_U128_OBJECT}),
    TyTag.I128: frozenset({val.TAG_I128_SMALL, val.TAG_I128_OBJECT}),
    TyTag.TIMEPOINT: frozenset({val.TAG_TIMEPOINT_SMALL, val.TAG_TIMEPOINT_OBJECT}),
    TyTag.DURATION: frozenset({val.TAG_DURATION_SMALL, val.TAG_DURATION_OBJECT}),
    TyTag.SYMBOL: frozenset({val.TAG_SYMBOL_SMALL, val.TAG_SYMBOL_OBJECT}),
    TyTag.STRING: frozenset({val.TAG_STRING_OBJECT}),
    TyTag.BYTES: frozenset({val.TAG_BYTES_OBJECT}),
    TyTag.BYTES_N: frozenset({val.TAG_BYTES_OBJECT}),
    TyTag.ADDRESS: frozenset({val.TAG_ADDRESS_OBJECT}),
    TyTag.VEC: frozenset({val.TAG_VEC_OBJECT}),
    TyTag.MAP: frozenset({val.TAG_MAP_OBJECT}),
    TyTag.STRUCT: frozenset({val.TAG_MAP_OBJECT}),
}


def decode_val(word: int, ty: Ty, host: FullHost) -> ChainValue:
    """One returned `Val` word as the tier-1 chain value it stands for.

    The tag check comes FIRST and is the half that can fail: `chain_value`
    decodes whatever tag it is given, so `U32(1)` returned under `TAG_I32`
    would decode cleanly to `I32(1)` and compare unequal to `U32(1)` for a
    reason that reads like a value bug rather than a tagging one. Checking the
    tag against the declared type says which it is.

    An `Option`'s `Void` case is accepted for any wrapped type -- on chain an
    empty `Option` IS `Void`, which is not a tag the wrapped type itself has.
    """
    tag = val.tag_of(word)
    if ty.tag is TyTag.OPTION:
        assert ty.elem is not None
        if tag == val.TAG_VOID:
            raise AssertionError(
                "an empty Option decodes to no tier-1 chain value (there is no "
                "serpent.types class for Void); a case returning one needs its own "
                "assertion, not decode_val"
            )
        return decode_val(word, ty.elem, host)
    allowed = _VAL_TAGS_FOR.get(ty.tag)
    assert allowed is not None, f"decode_val has no Val-tag set for {ty.render()}"
    assert tag in allowed, (
        f"a {ty.render()} came back under Val tag {tag} ({word:#018x}), which is not one "
        f"of the forms that type may take ({sorted(allowed)}) -- the value may be right "
        "and the tag wrong, which is a bug a client sees as a wrong-typed answer"
    )
    return host.chain_value(word)


# --- running one case --------------------------------------------------------


def build_case(case: SemCase) -> tuple[Ty, bytes]:
    """`case` as validated wasm, plus the `Ty` its expression has.

    `validate_external` is left at its default (`None` -- run `wasm-tools` when
    it is on PATH), so every one of the 35 modules is externally validated on a
    developer machine and on CI without being unrunnable where the tool is
    absent (ruling E5).

    **The two steps are reconciled here, not assumed.** Step 1 reads the `Ty`
    off an UNANNOTATED `x = <source>` (the compiler's own inference) and step 2
    writes `annotation_of(ty)` into a RETURN position, where the compiler
    resolves it independently -- a different code path (`resolve_annotation`)
    over a different question. `fn.ret == ty` is what says the round trip
    closed: if `annotation_of` mis-spelled the type in a way that still
    compiled (a `U64` written where the expression is `Timepoint`, both of
    which are 64-bit and both of which have a small form), the differential
    would go on to compare a value of one type against an oracle answer of
    another. This assertion is cheap and it is the whole reason step 1 exists.
    """
    ty = observed_ty(case)
    source = wrap_returning(case.source, annotation_of(ty))
    compiled = compile_module(source, f"semantics/{case.name}.py")
    (fn,) = compiled.functions
    assert fn.ret == ty, (
        f"{case.name}: step 1 inferred {ty.render()} from the unannotated `x = <source>`, "
        f"but the re-wrapped module's declared return resolved to {fn.ret.render()} -- "
        f"annotation_of({ty.render()}) == {annotation_of(ty)!r} does not round-trip"
    )
    built = build_wasm(compiled)
    return ty, built.wasm


def start_case(case: SemCase) -> tuple[Ty, FullHost, engine.MiniHost]:
    ty, wasm = build_case(case)
    host = FullHost()
    mini = engine.MiniHost(wasm, imports=host.bindings())
    host.attach(mini)
    return ty, host, mini


@pytest.mark.parametrize("case", IN_SCOPE, ids=[case.name for case in IN_SCOPE])
def test_every_in_scope_case_compiles_to_a_module_that_links(case: SemCase) -> None:
    """The floor under everything below: the wrapper really does build and
    instantiate for all 35, so a failure further down is about BEHAVIOR rather
    than about a case the harness could not run at all."""
    _ty, _host, mini = start_case(case)
    assert mini is not None


# --- kind == "value": the decoded answer IS tier 1's answer -----------------

_VALUE_CASES = [case for case in IN_SCOPE if case.kind == "value"]


@pytest.mark.parametrize("case", _VALUE_CASES, ids=[case.name for case in _VALUE_CASES])
def test_a_value_case_returns_the_oracles_value(case: SemCase) -> None:
    """The differential proper, compared by the ORACLE's own equality.

    Both claims are asserted: against `case.expect` (the frozen table entry)
    and against tier 1's own `eval` of the same source. They agree today --
    `tests/semantics/test_semantics.py` asserts exactly that -- and they are
    different claims, so a table edit that changed one without the other shows
    up here as a disagreement rather than as a silently narrower check.
    """
    ty, host, mini = start_case(case)
    word = mini.invoke("go")
    assert word is not None, "a value case's method returns a Val, never nothing"
    decoded = decode_val(word, ty, host)
    assert decoded == case.expect, f"{case.name}: compiled {decoded!r} != table {case.expect!r}"
    assert decoded == _eval_case(case.source), (
        f"{case.name}: the compiled answer and tier 1's own eval of the same source "
        "disagree -- this is the cross-tier divergence the table exists to catch"
    )


def test_the_flagged_symbol_ordering_vector_still_gives_the_tier1_answer() -> None:
    """THE top sub-plan D/F differential vector, called out by name.

    `Symbol("_") < Symbol("A")` is `False` under tier 1's raw-UTF-8 ordering
    (`ord("A") == 65 < ord("_") == 95`) and would be `True` if the host
    compared `SymbolSmall`'s packed 6-bit alphabet codes (`"_"` is code 1,
    `"A"` is 12). The mini host mirrors tier 1 BY CONSTRUCTION (ruling E1), so
    a green here is not evidence about the real host -- it is a pin, so that
    when sub-plan F runs this against a real Soroban host the disagreement is
    a five-second diff instead of a rediscovery. `cases.py`'s module docstring
    is the long version.
    """
    (case,) = [c for c in IN_SCOPE if c.name == "symbol_underscore_vs_A_ascii_order"]
    ty, host, mini = start_case(case)
    word = mini.invoke("go")
    assert word is not None
    assert decode_val(word, ty, host) == case.expect
    assert val.symbol_char_code("_") < val.symbol_char_code("A")  # the 6-bit codes disagree


# --- kind == "contract_error": a HostError with the SAME code ----------------

_ERROR_CASES = [case for case in IN_SCOPE if case.kind == "contract_error"]


@pytest.mark.parametrize("case", _ERROR_CASES, ids=[case.name for case in _ERROR_CASES])
def test_a_contract_error_case_aborts_with_the_same_code(case: SemCase) -> None:
    """`kind == "contract_error"` -> `fail_with_error` with tier 1's own code.

    Three assertions, because they fail for three different reasons:

    * the error CODE equals `case.code`, which is the same number tier 1's
      `ContractError.code` carries (every in-scope error case is the
      checked-arithmetic boundary, `errors.CODE_ARITHMETIC_OVERFLOW`);
    * the error TYPE is `Contract` (S10): a client discriminates a contract's
      own error from a host-level one by this field, and an abort carrying the
      wrong one is unclassifiable;
    * `host.errors` recorded exactly one abort, so the code came out of the
      contract's `fail_with_error` call rather than out of a trap the harness
      happened to translate.
    """
    _ty, host, mini = start_case(case)
    with pytest.raises(engine.HostError) as info:
        mini.invoke("go")
    error_val = info.value.val
    assert val.error_code_of(error_val) == case.code
    assert val.error_type_of(error_val) == val.ERROR_TYPE_CONTRACT
    assert val.is_contract_error_val(error_val)
    assert host.errors == [error_val]


def test_every_in_scope_error_case_is_the_arithmetic_overflow_boundary() -> None:
    """Non-circularity: the codes above are compared against `case.code`, and
    this says what that number IS -- `errors.CODE_ARITHMETIC_OVERFLOW`, the
    only `ContractError` the current expression surface can raise (`cases.py`'s
    "Error round-trip" note). A future table row with a different code makes
    this loud rather than quietly widening the assertion above."""
    assert {case.code for case in _ERROR_CASES} == {errors.CODE_ARITHMETIC_OVERFLOW}


# --- kind == "trap": the trap CLASS *and* its CAUSE -------------------------
#
# `cases.py` says tier 2 "asserts a VM trap", and a trap carries no error `Val`
# a client could read -- so the CLASS is the conformance floor. Review round 1
# (controller re-ruling of Important 1) makes the point that a floor is not a
# ceiling: this rig knows strictly more than "something refused", and throwing
# that away is what would let the wrong CAUSE pass. So each case also pins why
# it trapped:
#
# * a `WASM_TRAP` row pins wasmtime's own `TrapCode`. `wasmtime.Trap.trap_code`
#   is a clean accessor on wasmtime 48 (`wasmtime.TrapCode.
#   INTEGER_DIVISION_BY_ZERO`), so no message-text matching is needed -- which
#   matters, because the message is a multi-line backtrace whose text is not a
#   stable interface.
# * a `HOST_TRAP` row pins the host function that raised, read off the
#   harness's own call log. `call_names()[-1]` (not merely `count(...) > 0`) is
#   the assertion: "the last host call the contract made is the one that
#   trapped" distinguishes trapping IN `vec_get` from reaching `vec_get`
#   earlier and then trapping somewhere else entirely.

#: The three shapes a "the host refused" outcome takes in this rig, and what
#: each one MEANS.
WASM_TRAP = "wasm_trap"
HOST_TRAP = "host_trap"
NONCONTRACT_ERROR = "noncontract_error"


def trap_class_of(exc: BaseException) -> str:
    """Classify one raised exception into the trap taxonomy above.

    * `wasmtime.Trap` -- the GUEST trapped: an `i32.div_s` by zero is a wasm
      instruction-level trap, with no host call involved at all.
    * `engine.HostTrap` -- an env.json "Traps if ..." precondition was violated
      inside a host function (`vec_get` out of bounds, `map_get` on a missing
      key).
    * `engine.HostError` whose `Val` is NOT a Contract-type error -- the
      Task 6 ruling: the 128-bit division-by-zero path surfaces as
      `u128_div`/`i256_div` RETURNING an error `Val` of a host error type,
      which the VM turns into an abort. It is trap-CLASS (no contract error
      code a client can act on), not a `contract_error`, and
      `test_the_noncontract_host_error_is_trap_class` exercises it.

    A Contract-type `HostError` is deliberately NOT in the taxonomy: that is a
    `contract_error` outcome, and conflating the two would make a
    `kind="trap"` case pass on a contract that aborted with a code.
    """
    if isinstance(exc, wasmtime.Trap):
        return WASM_TRAP
    if isinstance(exc, engine.HostTrap):
        return HOST_TRAP
    if isinstance(exc, engine.HostError) and not val.is_contract_error_val(exc.val):
        return NONCONTRACT_ERROR
    raise AssertionError(f"{type(exc).__name__} is not a trap-class outcome: {exc}")


@dataclass(frozen=True)
class TrapExpectation:
    """What one `kind="trap"` case must do, class AND cause.

    Exactly one of `trap_code`/`host_fn` is set, and which one is set is
    determined by `kind` -- a `WASM_TRAP` has no host function to name (the
    guest never called out) and a `HOST_TRAP` has no wasmtime `TrapCode` (the
    guest is still happily executing when the callback raises).
    """

    kind: str
    #: For `WASM_TRAP`: wasmtime's own trap code for the faulting instruction.
    trap_code: wasmtime.TrapCode | None = None
    #: For `HOST_TRAP`: the host function that raised, i.e. the LAST call the
    #: contract made before the trap.
    host_fn: str | None = None

    def __post_init__(self) -> None:
        if self.kind == WASM_TRAP:
            assert self.trap_code is not None and self.host_fn is None
        elif self.kind == HOST_TRAP:
            assert self.host_fn is not None and self.trap_code is None
        else:  # pragma: no cover - the table below uses only the two
            raise AssertionError(f"no cause pinned for trap kind {self.kind!r}")


#: Which trap each in-scope `kind="trap"` case takes, DERIVED from where the
#: trap comes from rather than recorded from a run:
#:
#: * `//`/`%` by zero on a 32-bit type lowers to `i32.div_s`/`i32.rem_s`/
#:   `i32.div_u` (spec §6: the guest does not call the host to divide a 32-bit
#:   number), and the wasm spec's trap for those with a zero divisor is
#:   "integer divide by zero" -- `TrapCode.INTEGER_DIVISION_BY_ZERO`.
#:   Deliberately NOT `INTEGER_OVERFLOW`, which is the OTHER trap `div_s` can
#:   take (`INT_MIN / -1`) and which serpent turns into a contract error
#:   instead (`i32_min_floordiv_neg1_overflows` is a `contract_error` case, not
#:   a trap case) -- so pinning the code is what keeps those two apart.
#: * every other in-scope trap case is a container/buffer precondition, i.e. an
#:   env.json "Traps if ..." row inside one named host function: `bytes_get`
#:   past the end, `vec_get` out of bounds, `map_get` on a missing key.
#:
#: Pinning the class alone would be satisfied by a lowering that started
#: routing 32-bit division through a host function, by a `div_s` that trapped
#: on overflow instead of dividing by zero, or by a `vec_get` bounds check that
#: moved into `vec_len`. Pinning the cause is what rejects all three.
EXPECTED_TRAP: dict[str, TrapExpectation] = {
    "floordiv_by_zero_traps": TrapExpectation(
        WASM_TRAP, trap_code=wasmtime.TrapCode.INTEGER_DIVISION_BY_ZERO
    ),
    "mod_by_zero_traps": TrapExpectation(
        WASM_TRAP, trap_code=wasmtime.TrapCode.INTEGER_DIVISION_BY_ZERO
    ),
    "unsigned_floordiv_by_zero_traps": TrapExpectation(
        WASM_TRAP, trap_code=wasmtime.TrapCode.INTEGER_DIVISION_BY_ZERO
    ),
    "bytes_positive_out_of_range_traps": TrapExpectation(HOST_TRAP, host_fn="bytes_get"),
    "vec_get_out_of_bounds_traps": TrapExpectation(HOST_TRAP, host_fn="vec_get"),
    "map_get_missing_key_traps": TrapExpectation(HOST_TRAP, host_fn="map_get"),
}

_TRAP_CASES = [case for case in IN_SCOPE if case.kind == "trap"]


def test_expected_trap_covers_exactly_the_trap_cases() -> None:
    assert {case.name for case in _TRAP_CASES} == set(EXPECTED_TRAP)


@pytest.mark.parametrize("case", _TRAP_CASES, ids=[case.name for case in _TRAP_CASES])
def test_a_trap_case_traps_for_the_expected_reason(case: SemCase) -> None:
    """`kind == "trap"` -> the expected trap CLASS, and the expected CAUSE.

    Deliberately not asserted: an error code on the trap itself. Tier 1 raises a
    Python builtin (`ZeroDivisionError`, `IndexError`, `KeyError`) which has no
    on-chain counterpart, and a trap carries no error `Val` -- so there is
    nothing to compare across the tiers there. The cause pinned below is a
    TIER-2 fact (which instruction faulted, which callback raised), not a
    translation of tier 1's exception class.

    Also asserted: `host.errors == []`. A trap is not an abort, so nothing
    should have gone through `fail_with_error` -- a lowering that turned an
    out-of-bounds read into a contract error would otherwise pass a class-only
    check on the wrong outcome.
    """
    expectation = EXPECTED_TRAP[case.name]
    _ty, host, mini = start_case(case)
    # `BaseException` deliberately: the trap CLASS is what `trap_class_of`
    # asserts below, and narrowing this `raises` would duplicate that decision
    # here (and split a wasm trap and a HostTrap into two different tests).
    with pytest.raises(BaseException) as info:
        mini.invoke("go")
    exc = info.value
    assert trap_class_of(exc) == expectation.kind
    assert host.errors == []

    if expectation.kind == WASM_TRAP:
        assert isinstance(exc, wasmtime.Trap)
        assert exc.trap_code == expectation.trap_code, (
            f"{case.name} trapped in the guest, but on {exc.trap_code!r} rather than "
            f"{expectation.trap_code!r}"
        )
        # The guest faulted on its own instruction, so it never called out --
        # which is the other half of "this is a wasm trap, not a host one".
        assert host.call_names() == []
    else:
        assert isinstance(exc, engine.HostTrap)
        calls = host.call_names()
        assert calls and calls[-1] == expectation.host_fn, (
            f"{case.name} raised HostTrap, but the last host call was "
            f"{calls[-1] if calls else None!r} rather than {expectation.host_fn!r} -- "
            f"the trap did not come from the callback this case is about (log: {calls})"
        )
        assert host.count(expectation.host_fn) > 0


def test_the_noncontract_host_error_is_trap_class() -> None:
    """The Task 6 ruling, exercised rather than only documented.

    128-bit division routes through the guest-runtime `u128_div` part, which
    reports a divide-by-zero by RETURNING an error `Val` -- an
    `engine.HostError` whose `Val` is of a host error type, not `Contract`. No
    in-scope table case is a 128-bit division (the table's three
    division-by-zero rows are all 32-bit), so without this test the
    `NONCONTRACT_ERROR` branch of `trap_class_of` would be dead code that
    could rot: it says "trap-class" about a shape nothing checks.
    """
    source = wrap_returning("U128(5) // U128(0)", "U128")
    built = build_wasm(compile_module(source, "semantics/probe_u128_div_zero.py"))
    host = FullHost()
    mini = engine.MiniHost(built.wasm, imports=host.bindings())
    host.attach(mini)
    with pytest.raises(engine.HostError) as info:
        mini.invoke("go")
    assert not val.is_contract_error_val(info.value.val)
    assert trap_class_of(info.value) == NONCONTRACT_ERROR


# --- the harness's own discrimination probes ---------------------------------
#
# Every assertion above is only as good as the helpers under it, and both
# helpers have a failure mode that would make the whole differential vacuous:
# an `annotation_of` that spelled the wrong type would compile a DIFFERENT
# expression, and a `decode_val` that ignored the tag would accept a
# wrong-typed answer. These probe both directly.


@pytest.mark.parametrize(
    ("ty", "expected"),
    [
        (Ty.Bool, "Bool"),
        (Ty.U32, "U32"),
        (Ty.I128, "I128"),
        (Ty.Symbol, "Symbol"),
        (Ty.Address, "Address"),
        (Ty.Void, "None"),
        (Ty.BytesN(20), "bytes_n(20)"),
        (Ty.BytesN(32), "bytes_n(32)"),
        (Ty.Vec(Ty.U32), "Vec[U32]"),
        (Ty.Vec(Ty.Vec(Ty.Symbol)), "Vec[Vec[Symbol]]"),
        (Ty.Map(Ty.Symbol, Ty.U32), "Map[Symbol, U32]"),
        (Ty.Map(Ty.Symbol, Ty.Vec(Ty.U32)), "Map[Symbol, Vec[U32]]"),
        (Ty.Option(Ty.U32), "U32 | None"),
        (Ty.Option(Ty.Vec(Ty.U32)), "Vec[U32] | None"),
    ],
    ids=lambda arg: arg if isinstance(arg, str) else "",
)
def test_annotation_of_spells_the_authoring_surface(ty: Ty, expected: str) -> None:
    assert annotation_of(ty) == expected


@pytest.mark.parametrize(
    ("ty", "annotation"),
    [
        (Ty.Bool, "Bool"),
        (Ty.U32, "U32"),
        (Ty.I64, "I64"),
        (Ty.Symbol, "Symbol"),
        (Ty.BytesN(20), "bytes_n(20)"),
        (Ty.Vec(Ty.U32), "Vec[U32]"),
        (Ty.Map(Ty.Symbol, Ty.U32), "Map[Symbol, U32]"),
        (Ty.Option(Ty.U32), "U32 | None"),
    ],
    ids=lambda arg: arg if isinstance(arg, str) else "",
)
def test_every_annotation_of_spelling_really_resolves_back_to_its_ty(
    ty: Ty, annotation: str
) -> None:
    """`annotation_of` is only useful if the FRONTEND reads its output back as
    the same `Ty`. Proven by round trip through a real compile rather than by
    inspecting the string, because the string is exactly what this file could
    get subtly wrong: `Vec(U32)` instead of `Vec[U32]` would read as a CALL,
    and `bytes_n(20)` must be the factory call and not a bare name.

    The annotation goes in a PARAMETER position, not the return position the
    differential itself uses, because a parameter needs no value: a return
    annotation would force this probe to also author a literal of every type,
    and a case where no such literal is expressible (`bytes_n(20)`) would fail
    for a reason that has nothing to do with the annotation. `resolve_
    annotation` is the same function for both positions (`types_.py`: "a
    parameter, return type, or field").
    """
    imports = ", ".join(_imports_for("", annotation))
    source = (
        f"from serpent import {imports}\n\n\n"
        "@contract\n"
        "class C:\n"
        f"    def go(self, env: Env, x: {annotation}) -> None:\n"
        "        return\n"
    )
    compiled = compile_module(source, "semantics/probe_annotation.py")
    (fn,) = compiled.functions
    assert [(name, param_ty) for name, param_ty, _loc in fn.params] == [("x", ty)]


def test_decode_val_refuses_a_word_whose_tag_disagrees_with_the_type() -> None:
    """The probe that keeps `decode_val` from being a bare `chain_value` call.

    `val.pack_i32val(1)` and `val.pack_u32val(1)` have the SAME body and
    different tags, and `chain_value` decodes both happily -- to `I32(1)` and
    `U32(1)`. Handed the `I32` word while `ty` says `U32`, `decode_val` must
    say so; without the tag check it would return `I32(1)`, the comparison
    would fail on the VALUE, and a tagging bug would read as an arithmetic one.
    """
    host = FullHost()
    assert decode_val(val.pack_u32val(1), Ty.U32, host) == host.chain_value(val.pack_u32val(1))
    with pytest.raises(AssertionError, match="not one of the forms"):
        decode_val(val.pack_i32val(1), Ty.U32, host)


def test_decode_val_accepts_both_forms_of_a_split_type() -> None:
    """The other direction: a `U64` legitimately arrives small or as an object
    depending on its magnitude (A3), so a tag check that pinned ONE form would
    reject a perfectly good answer -- which is how an over-strict version of
    the check above would break the suite."""
    from serpent.types import U64

    host = FullHost()
    small = host.val_word(U64(5))
    wide = host.val_word(U64(2**60))
    assert val.tag_of(small) == val.TAG_U64_SMALL
    assert val.tag_of(wide) == val.TAG_U64_OBJECT
    assert decode_val(small, Ty.U64, host) == U64(5)
    assert decode_val(wide, Ty.U64, host) == U64(2**60)


def test_wrap_returning_imports_a_name_only_the_annotation_mentions() -> None:
    """`_imports_for`'s reason for existing, as a test.

    `Bytes(b"ab")[5]` is typed `U32` and never writes `U32`, so a wrapper that
    scanned only the source would emit `def go(self, env: Env) -> U32` in a
    module that never imported `U32` -- a `NameError` at module-exec time
    (ruling E1's hybrid frontend really runs the module), which is a confusing
    way to learn about a missing import.
    """
    source = 'Bytes(b"ab")[5]'
    assert "U32" not in source
    wrapped = wrap_returning(source, "U32")
    assert "U32" in wrapped.splitlines()[0]
    compile_module(wrapped, "semantics/probe_annotation_import.py")
