"""THE cross-tier semantics table: serpent's frozen behavioral oracle.

Every `SemCase` names one on-chain-observable behavior as a single Python
expression, `source`, together with the outcome tier 1 (this file's runner,
plain-Python execution against the chain types) must observe. The same table
is meant to drive tier 2 (sub-plan D's differential harness, which compiles
`source` into a contract method body and runs it on a real host) -- which is
why `source` is a string rather than a `Callable`: a callable is opaque to a
compiler, a source string is not.

**Kind -> outcome mapping (frozen, matches the Task 10 brief verbatim):**

* `"value"` -- `source` evaluates to a chain value. Compared to `expect` by
  `==`, which for every chain type already answers the payload-equality
  question the host would (see `types/_base.py`); `to_val()` is not used for
  the comparison itself because several chain values (`Bytes`, `String`,
  wide `Symbol`s, `U64`/`U128`/`I128` outside the 56-bit small range) raise
  `NotImplementedError("host object form; sub-plan B")` from `to_val()` and
  are still perfectly comparable by `==` today.
* `"contract_error"` -- evaluating `source` raises a `ContractError`; tier 1
  asserts `.code == case.code`. Tier 2 will assert the on-chain error code.
* `"trap"` -- evaluating `source` raises the builtin named by `case.trap`
  (`IndexError`, `KeyError`, `ZeroDivisionError`, ...). Tier 2 asserts a VM
  trap.
* `"reject"` -- authoring-time misuse: evaluating `source` raises `TypeError`
  or `ValueError` *before* any chain semantics are exercised (an out-of-range
  literal, a cross-type operation, an omitted operator, a malformed
  Symbol/Bytes literal, ...). `case.trap` pins the *exact* exception class
  where that is unambiguous (every case below sets it). **Tier-1-only: this
  is sub-plan C's compiler rejecting the program at compile time, so
  sub-plan D skips `"reject"` cases BY CONSTRUCTION** -- there is no VM trap
  to differentially compare, because the "contract" never compiles.

**Truthiness convention.** `source` must be one expression that is both
eval-able against the chain-type namespace AND a body a future compiler can
lower (per the Task 10 brief). A bare `bool(U32(0))` evaluates to a *plain*
Python `bool`, which would force `kind="value"` to sometimes mean "compare
with `==` against a chain instance" and sometimes "compare against a raw
`bool"" -- two conventions for one kind. Wrapping the zero-test in `Bool(...)`
(`"Bool(bool(U32(0)))"`) keeps `expect` a chain-type instance in every
`"value"` case, uniformly, while the inner `bool(...)` call is exactly the
zero-test the compiler lowers to `i64.eqz` (2026-08-26 decision log entry).
Every other boolean-valued observable in this table (payload equality, Map
key ordering, Symbol ordering) follows the same `Bool(...)`-wrapping
convention for the same reason.

**Map/rank-ordering cases.** `val_cmp`/`_SCVAL_RANK` are not public API (only
the containers that use them, `Vec`/`Map`, are), so a case cannot call
`val_cmp` directly. Ordering is instead pinned through an *observable*: the
first key of a `Map` built from out-of-rank-order inserts, read back through
the public `Map.keys()`/`Vec.get()` surface.

**Symbol `"_"` vs `"A"` ordering -- the top sub-plan D/F differential
vector.** `Symbol._order_key()` orders by raw UTF-8 bytes (ASCII), so under
the *current* model `Symbol("A") < Symbol("_")` (`ord("A")==65 <
ord("_")==95`). The host's `SymbolSmall` Val instead packs each character
through a 6-bit alphabet code (`serpent.val.SYMBOL_CHARS`, where `"_"` is
code 1 and `"A"` is code 12) -- if the host compares packed *codes* rather
than original bytes, `Symbol("_") < Symbol("A")` would hold there instead,
the opposite answer. This table pins the ASCII model tier 1 implements today;
sub-plan D/F's differential harness is exactly what must confirm or refute it
against the real host, and the case below exists so that the day it does, a
regression here is a five-second diff, not a rediscovery.

**Error round-trip.** `BadArgument` is declared but not yet raised anywhere
in the runtime surface (no operation reaches for it before sub-plan B/E), so
the only *expression* in the current surface that raises a real
`ContractError` is the checked-arithmetic boundary: `ArithmeticOverflow`,
code `0xFFFF_FFFE`. The overflow cases below are simultaneously the checked-
arithmetic boundary cases AND the error-round-trip case the brief asks for --
one `contract_error` case is named explicitly for the latter.

`CASES` must stay >= 40 entries (the brief's floor); `test_semantics.py`
asserts the count and runs every case.
"""

from dataclasses import dataclass
from typing import Literal

# Every `expect` chain instance is built with the public root import, same as
# a real contract author would use -- the table is itself a small proof that
# the root export list suffices.
from serpent import (
    I32,
    U32,
    U128,
    Bool,
    Bytes,
)

# Two real strkeys (account, contract), lifted from test_address.py's fixture
# constants so Address cases use values already known to decode.
_ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"
_CONTRACT = "CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI"


@dataclass(frozen=True)
class SemCase:
    name: str
    # `frontend` (Task 11a, BL-3): where `source` lands against the real
    # compiler frontend (`serpent.compiler.frontend.compile_module`), NOT
    # against tier 1's plain-Python execution above -- `kind`/`expect`/`code`/
    # `trap` stay the frozen tier-1 oracle; this is a second, independent axis
    # `tests/unit/test_frontend_semantics.py` classifies every case against.
    # * "accepts" -- `source` compiles, and the compiled IR's final
    #   expression type equals the operand type (F.1.11: an accepted
    #   contract_error/trap case is asserted on compile+type only; the
    #   runtime behavior is tier 1's/the differential harness's job).
    # * "rejects" -- `source` draws a located compile diagnostic.
    # * "not_expressible" -- neither answer is meaningful: the source cannot
    #   be probed through the compiler at all, or the compiler rejects it for
    #   a reason UNRELATED to the behavior this case pins (masking it).
    #   `not_expressible_reason` is then required (one line, why).
    frontend: Literal["accepts", "rejects", "not_expressible"]
    source: str  # single expression, eval-able in the chain-type
    # namespace AND compilable by sub-plan D in a method body
    kind: Literal["value", "contract_error", "trap", "reject"]
    expect: object | None = None  # chain-type instance, for kind == "value"
    code: int | None = None  # contract error code, for kind == "contract_error"
    trap: type[BaseException] | None = None  # tier-1 builtin, for kind == "trap"
    # Cases marked tier1_only are observable only in tier-1 Python execution --
    # sub-plan D skips them by construction, because the compiler tier
    # statically rejects their source. F.2.2: tier1_only holds for a
    # non-"reject"-kind case if and only if frontend == "rejects" (a
    # kind == "reject" case is tier-1-only BY CONSTRUCTION, per this class's
    # own kind docstring above, so the flag is not required for it; a
    # not_expressible case is carved out of the biconditional entirely).
    tier1_only: bool = False
    # One line, required exactly when frontend == "not_expressible".
    not_expressible_reason: str | None = None


CASES: list[SemCase] = [
    # --- truthiness (2026-08-26 decision: bool(x) is a zero-test) -----------
    SemCase(
        name="truthiness_u32_zero_is_false",
        frontend="accepts",
        source="Bool(bool(U32(0)))",
        kind="value",
        expect=Bool(False),
    ),
    SemCase(
        name="truthiness_u32_nonzero_is_true",
        frontend="accepts",
        source="Bool(bool(U32(1)))",
        kind="value",
        expect=Bool(True),
    ),
    # --- truncating division: div_s/rem_s, NOT Python floor/floormod --------
    SemCase(
        name="truncating_floordiv_negative_dividend",
        frontend="accepts",
        source="I32(-7) // I32(2)",
        kind="value",
        expect=I32(-3),
    ),
    SemCase(
        name="truncating_mod_takes_dividend_sign",
        frontend="accepts",
        source="I32(-7) % I32(2)",
        kind="value",
        expect=I32(-1),
    ),
    SemCase(
        name="min_mod_neg1_is_zero_not_a_trap",
        frontend="accepts",
        source="I32(-(2**31)) % I32(-1)",
        kind="value",
        expect=I32(0),
    ),
    SemCase(
        name="reflected_rfloordiv_truncates",
        frontend="accepts",
        source="20 // I32(3)",
        kind="value",
        expect=I32(6),
    ),
    SemCase(
        name="reflected_rmod_truncates",
        frontend="accepts",
        source="20 % I32(3)",
        kind="value",
        expect=I32(2),
    ),
    # --- checked-arithmetic boundaries: every width, both directions --------
    SemCase(
        name="i32_min_floordiv_neg1_overflows",
        frontend="accepts",
        source="I32(-(2**31)) // I32(-1)",
        kind="contract_error",
        code=0xFFFF_FFFE,
    ),
    SemCase(
        name="u32_unary_minus_of_one_overflows",
        frontend="accepts",
        source="-U32(1)",
        kind="contract_error",
        code=0xFFFF_FFFE,
    ),
    SemCase(
        name="u32_max_plus_one_overflows",
        frontend="accepts",
        source="U32(2**32 - 1) + U32(1)",
        kind="contract_error",
        code=0xFFFF_FFFE,
    ),
    SemCase(
        name="u32_zero_minus_one_underflows",
        frontend="accepts",
        source="U32(0) - U32(1)",
        kind="contract_error",
        code=0xFFFF_FFFE,
    ),
    SemCase(
        name="i32_max_plus_one_overflows",
        frontend="accepts",
        source="I32(2**31 - 1) + I32(1)",
        kind="contract_error",
        code=0xFFFF_FFFE,
    ),
    SemCase(
        name="i32_min_minus_one_overflows",
        frontend="accepts",
        source="I32(-(2**31)) - I32(1)",
        kind="contract_error",
        code=0xFFFF_FFFE,
    ),
    SemCase(
        name="i32_min_negated_overflows",
        frontend="accepts",
        source="-I32(-(2**31))",
        kind="contract_error",
        code=0xFFFF_FFFE,
    ),
    SemCase(
        name="u64_max_plus_one_overflows",
        frontend="accepts",
        source="U64(2**64 - 1) + U64(1)",
        kind="contract_error",
        code=0xFFFF_FFFE,
    ),
    SemCase(
        name="i64_min_minus_one_overflows",
        frontend="accepts",
        source="I64(-(2**63)) - I64(1)",
        kind="contract_error",
        code=0xFFFF_FFFE,
    ),
    SemCase(
        name="u128_max_plus_one_overflows",
        frontend="accepts",
        source="U128(2**128 - 1) + U128(1)",
        kind="contract_error",
        code=0xFFFF_FFFE,
    ),
    SemCase(
        name="i128_min_minus_one_overflows",
        frontend="accepts",
        source="I128(-(2**127)) - I128(1)",
        kind="contract_error",
        code=0xFFFF_FFFE,
    ),
    SemCase(
        # The brief's named "error round-trip" case: a real ContractError
        # raised by evaluating an expression, whose .code round-trips through
        # the SemCase.code field. (Rationale for reusing an overflow boundary
        # as the round-trip witness: see the module docstring.)
        name="error_roundtrip_contract_error_code",
        frontend="accepts",
        source="I32(2**31 - 1) + I32(1)",
        kind="contract_error",
        code=0xFFFF_FFFE,
    ),
    # --- checked-arithmetic: ordinary in-range results, both directions -----
    SemCase(
        name="unary_minus_ordinary_value",
        frontend="accepts",
        source="-I32(5)",
        kind="value",
        expect=I32(-5),
    ),
    SemCase(
        name="unary_minus_of_unsigned_zero_stays_in_range",
        frontend="accepts",
        source="-U128(0)",
        kind="value",
        expect=U128(0),
    ),
    SemCase(
        name="reflected_add_int_plus_chain_int",
        frontend="accepts",
        source="3 + U32(5)",
        kind="value",
        expect=U32(8),
    ),
    SemCase(
        name="reflected_sub_int_minus_chain_int",
        frontend="accepts",
        source="10 - U32(3)",
        kind="value",
        expect=U32(7),
    ),
    SemCase(
        name="reflected_mul_int_times_chain_int",
        frontend="accepts",
        source="3 * U32(4)",
        kind="value",
        expect=U32(12),
    ),
    SemCase(
        name="int_operand_accepted_in_range",
        frontend="accepts",
        source="U32(5) + 10",
        kind="value",
        expect=U32(15),
    ),
    SemCase(
        # The documented bool-leak: Python bool is int, so it is accepted
        # wherever int is on a numeric chain type (2026-08-26 decision log).
        # Tier-1-only: the compiler tier statically rejects bool-as-int-operand.
        name="bool_leaks_as_int_operand",
        frontend="rejects",
        source="U32(5) + True",
        kind="value",
        expect=U32(6),
        tier1_only=True,
    ),
    # --- division/modulo by zero: host trap, not a contract error ------------
    SemCase(
        name="floordiv_by_zero_traps",
        frontend="accepts",
        source="I32(5) // I32(0)",
        kind="trap",
        trap=ZeroDivisionError,
    ),
    SemCase(
        name="mod_by_zero_traps",
        frontend="accepts",
        source="I32(5) % I32(0)",
        kind="trap",
        trap=ZeroDivisionError,
    ),
    SemCase(
        name="unsigned_floordiv_by_zero_traps",
        frontend="accepts",
        source="U32(5) // U32(0)",
        kind="trap",
        trap=ZeroDivisionError,
    ),
    # --- coercion / cross-type rejects: authoring-time, no implicit widening -
    SemCase(
        name="out_of_range_int_operand_rejected",
        frontend="rejects",
        source="U32(5) + 2**32",
        kind="reject",
        trap=ValueError,
    ),
    SemCase(
        name="cross_width_unsigned_add_rejected",
        frontend="rejects",
        source="U32(1) + U64(1)",
        kind="reject",
        trap=TypeError,
    ),
    SemCase(
        name="cross_signedness_add_rejected",
        frontend="rejects",
        source="I32(1) + I64(1)",
        kind="reject",
        trap=TypeError,
    ),
    SemCase(
        name="pow_operator_omitted",
        frontend="rejects",
        source="U32(2) ** U32(3)",
        kind="reject",
        trap=TypeError,
    ),
    SemCase(
        name="divmod_operator_omitted",
        frontend="rejects",
        source="divmod(U32(5), U32(2))",
        kind="reject",
        trap=TypeError,
    ),
    SemCase(
        name="bitwise_and_operator_omitted",
        frontend="rejects",
        source="I32(1) & I32(1)",
        kind="reject",
        trap=TypeError,
    ),
    SemCase(
        name="bool_has_no_arithmetic",
        frontend="rejects",
        source="Bool(True) + U32(1)",
        kind="reject",
        trap=TypeError,
    ),
    # --- time types: no arithmetic at all, not even same-type (2026-08-26) --
    SemCase(
        name="timepoint_plus_duration_rejected",
        frontend="rejects",
        source="Timepoint(5) + Duration(1)",
        kind="reject",
        trap=TypeError,
    ),
    SemCase(
        name="timepoint_plus_timepoint_rejected",
        frontend="rejects",
        source="Timepoint(1) + Timepoint(1)",
        kind="reject",
        trap=TypeError,
    ),
    SemCase(
        name="duration_unary_minus_rejected",
        frontend="rejects",
        source="-Duration(5)",
        kind="reject",
        trap=TypeError,
    ),
    SemCase(
        name="duration_times_int_rejected",
        frontend="rejects",
        source="Duration(3) * 2",
        kind="reject",
        trap=TypeError,
    ),
    # --- payload equality across the Bytes family (2026-08-26 decision) -----
    SemCase(
        name="bytes32_equals_bytes_same_payload",
        frontend="accepts",
        source='Bool(Bytes32(b"\\x00" * 32) == Bytes(b"\\x00" * 32))',
        kind="value",
        expect=Bool(True),
    ),
    # --- no implicit str/bytes coercion into chain payload types -------------
    # Tier-1-only: the tier-2 answer is undecided until sub-plan C settles
    # raw-operand coercion.
    SemCase(
        name="symbol_does_not_coerce_from_str",
        frontend="rejects",
        source='Bool(Symbol("abc") == "abc")',
        kind="value",
        expect=Bool(False),
        tier1_only=True,
    ),
    SemCase(
        name="bytes_does_not_coerce_from_raw_bytes",
        frontend="rejects",
        source='Bool(Bytes(b"abc") == b"abc")',
        kind="value",
        expect=Bool(False),
        tier1_only=True,
    ),
    # --- Map/rank ordering observables (val_cmp is not public API itself) ---
    SemCase(
        # The flagged Bytes-before-Symbol case: ScValType rank 13 < 15, so a
        # Bytes key sorts first however it compares to the Symbol payload.
        name="map_orders_bytes_before_symbol",
        frontend="not_expressible",
        not_expressible_reason="a Map(Symbol, U32, ...) literal's keys must match the declared key type (F.1's typed-constructor rule); a Bytes key cannot be built there, so this cross-type rank observable has no compiled form (task-7b-report.md's ledgered limitation).",
        source='Map(Symbol, U32, [(Symbol("z"), U32(1)), (Bytes(b"a"), U32(2))]).keys().get(0)',
        kind="value",
        expect=Bytes(b"a"),
    ),
    SemCase(
        name="map_orders_same_type_keys_by_payload",
        frontend="accepts",
        source="Map(U32, U32, [(U32(5), U32(1)), (U32(1), U32(2))]).keys().get(0)",
        kind="value",
        expect=U32(1),
    ),
    SemCase(
        # Bool has the lowest ScValType rank (0) of any chain type, so it
        # always sorts first against any other scalar key -- Symbol (15) here.
        name="map_orders_bool_before_symbol",
        frontend="not_expressible",
        not_expressible_reason="a Map(Symbol, U32, ...) literal's keys must match the declared key type (F.1's typed-constructor rule); a Bool key cannot be built there, so this cross-type rank observable has no compiled form (task-7b-report.md's ledgered limitation).",
        source='Map(Symbol, U32, [(Symbol("a"), U32(1)), (Bool(True), U32(2))]).keys().get(0)',
        kind="value",
        expect=Bool(True),
    ),
    SemCase(
        # Top sub-plan D/F differential vector: ASCII order says Symbol("A")
        # sorts first (ord("A")=65 < ord("_")=95). The host's SymbolSmall
        # 6-bit alphabet codes "_" as 1 and "A" as 12 -- if the host compares
        # packed codes rather than raw bytes, this answer flips. See the
        # module docstring.
        name="symbol_underscore_vs_A_ascii_order",
        frontend="accepts",
        source='Bool(Symbol("_") < Symbol("A"))',
        kind="value",
        expect=Bool(False),
    ),
    # --- indexing: chain-faithful everywhere, no negative-index sugar --------
    # Tier-1-only: negative-index literals are compile-rejected per the
    # no-negative-indexing ruling.
    SemCase(
        name="bytes_negative_index_traps",
        frontend="rejects",
        source='Bytes(b"ab")[-1]',
        kind="trap",
        trap=IndexError,
        tier1_only=True,
    ),
    SemCase(
        name="bytes_positive_out_of_range_traps",
        frontend="accepts",
        source='Bytes(b"ab")[5]',
        kind="trap",
        trap=IndexError,
    ),
    SemCase(
        name="vec_get_out_of_bounds_traps",
        frontend="accepts",
        source="Vec(U32, [U32(1), U32(2)]).get(5)",
        kind="trap",
        trap=IndexError,
    ),
    SemCase(
        name="vec_pop_back_of_empty_traps",
        frontend="not_expressible",
        not_expressible_reason="`Vec(U32).pop_back()` calls a mutator on a temporary receiver -- SPT1034 rejects it (no binding to rebind) before the empty-pop trap this case pins is ever reachable; recognize.py names this exact shape as its own canonical example. Not one of task-11a-brief.md's three named cases -- a 4th instance found while classifying, flagged in task-11a-report.md for controller confirmation.",
        source="Vec(U32).pop_back()",
        kind="trap",
        trap=IndexError,
    ),
    SemCase(
        name="map_get_missing_key_traps",
        frontend="accepts",
        source='Map(Symbol, U32).get(Symbol("missing"))',
        kind="trap",
        trap=KeyError,
    ),
    SemCase(
        name="vec_wrong_element_type_rejected",
        frontend="not_expressible",
        not_expressible_reason="`Vec(U32, [...]).push_back(...)` calls a mutator on a temporary receiver -- SPT1034 rejects it (no binding to rebind) before the wrong-element-type check this case pins is ever reached (task-10-report.md §3's MJ-14 reconciliation).",
        source='Vec(U32, [U32(1)]).push_back(Symbol("x"))',
        kind="reject",
        trap=TypeError,
    ),
    # --- Symbol validation edges ----------------------------------------------
    SemCase(
        name="symbol_empty_rejected",
        frontend="rejects",
        source='Symbol("")',
        kind="reject",
        trap=ValueError,
    ),
    SemCase(
        name="symbol_too_long_rejected",
        frontend="rejects",
        source='Symbol("a" * 33)',
        kind="reject",
        trap=ValueError,
    ),
    # --- Bytes fixed-length validation edges ----------------------------------
    SemCase(
        name="bytes32_wrong_length_rejected",
        frontend="rejects",
        source='Bytes32(b"x")',
        kind="reject",
        trap=ValueError,
    ),
    SemCase(
        name="bytes64_wrong_length_rejected",
        frontend="rejects",
        source='Bytes64(b"x" * 10)',
        kind="reject",
        trap=ValueError,
    ),
    # --- Address: ordering (account before contract) and no coercion --------
    SemCase(
        name="address_account_orders_before_contract",
        frontend="accepts",
        source=f'Bool(Address("{_ACCOUNT}") < Address("{_CONTRACT}"))',
        kind="value",
        expect=Bool(True),
    ),
    SemCase(
        name="address_rejects_malformed_strkey",
        frontend="rejects",
        source='Address("not-a-strkey")',
        kind="reject",
        trap=ValueError,
    ),
]
