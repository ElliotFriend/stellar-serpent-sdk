"""HOST_FACTS: the facts about the real host the two models only ASSUMED (dossier D.3).

`cases.py` is the frozen expression table and `env_scenarios.py` the frozen
stateful one. Both are DIFFERENTIAL tables: every row is a question both legs
answer, and the row's value is a decision the models agree on. This third table
is not that. Every row here is a question only the REAL host can answer -- what
`extend_ttl` does past the network maximum, which `ScErrorCode` a 128-bit
division by zero reports, whether a published event survives the raise that
follows it -- and the row's job is to record the host's answer NEXT TO the
model's, including where the two differ and which of them is right.

Three fields carry the honesty:

* **`real` and `tier1` are separate expectations.** Where they are equal the row
  is a differential row like any other. Where they differ the row must say
  which side is right and why (`divergence_reason`), and a meta-test in
  `tests/unit/test_host_facts_tier1.py` requires it -- so a divergence cannot be
  written down as a shrug;
* **`tier1=Unmodelled(reason)`** is the honest answer where the tier-1 model has
  no opinion at all. The TTL maximum is the whole of it today: `extend_to` is
  applied exactly as given at any magnitude in every bucket, because the
  network's maximum live-until is only readable through an M2 host function
  (`serpent.env`'s "NOT modelled, named rather than approximated");
* **`chain_unproven`** marks a row whose evidence is about the TEST host and is
  known not to hold on chain. There is exactly one: the sdk test host restores a
  lapsed persistent entry on access, where the chain archives it and refuses the
  access until a restore footprint pays for it. Such a row is deliberately NOT
  "corrected" from a run -- if the test host ever starts archiving, the row fails
  and the declaration is retired on purpose.

**Why `HostErr` carries only the UNDERLYING pair.** The frame-level
`(error_type, code)` of every guest-side failure on this host is
`("Context", 6)` (review B5), so it distinguishes nothing; the classification
survives in the innermost error diagnostic, which is what
`RealHostError.underlying` reads. A tier-1 `HostErr` is the same pair reached
through the tier-1 mapping table that `test_host_facts_tier1.py` owns and
states: the model raises its own exception classes, and the row's claim is that
those classes MEAN the host's pair.

**Footprint counts, not footprint keys.** `write_entries`/`read_entries` are
resource COUNTS off `RealContract.resources()` (E6). Which ledger KEYS are in
the footprint is a tier-3 question (`e2e_invoke`, M2); a count is what this tier
can see, and it is enough to catch a write that touches an entry nobody expected.

**Ordering does not go through a contract.** `COMPARE_VECTORS` asks
`RealEnv.compare` directly, because `Bool(a < b)` on two containers is `SPT3005`
in the subset -- there is no contract that could ask the question (review M2).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from serpent.env import ChainValue
from serpent.testing._real import (
    DEFAULT_MAX_ENTRY_TTL,
    DEFAULT_MIN_PERSISTENT_ENTRY_TTL,
    DEFAULT_MIN_TEMP_ENTRY_TTL,
)

# Every expectation is built with the public root import, exactly as a contract
# author would -- the same rule `cases.py` and `env_scenarios.py` follow.
from serpent.types import I128, U32, U128, Address, Bool, Symbol, Vec
from tests.semantics.env_scenarios import CONTRACT, SHAPES_CONTRACT, Advance, Call, Step

_ROOT = Path(__file__).resolve().parents[2]

#: The contract every row drives. One fixture, one method per fact.
HOST_FACTS_CONTRACT = _ROOT / "tests" / "fixtures" / "host_facts.py"

#: The auth row's two addresses, both CONTRACT strkeys (review B2: the test host
#: mocks an authorization by registering a `MockAuthContract` AT the
#: authorizer's address, which a `G...` account cannot host). Lifted from
#: `env_scenarios.py`'s own constants rather than re-typed, so the strkeys this
#: table authorizes with are ones the suite already knows decode.
ALLOWED = Address(SHAPES_CONTRACT)
OTHER = Address(CONTRACT)


@dataclass(frozen=True)
class Value:
    """The call ANSWERED. `Value(None)` is Void -- the answer a `-> None` method
    gives -- which is why the field is Optional rather than the row omitting it."""

    value: ChainValue | None


@dataclass(frozen=True)
class ContractErr:
    """The contract's own `fail_with_error` code (`RealContractError.code`)."""

    code: int


@dataclass(frozen=True)
class HostErr:
    """A non-contract host failure, classified by its UNDERLYING diagnostic.

    `underlying` is the `(ScErrorType, ScErrorCode)` pair in the Rust spelling
    the façade reports (`("Storage", "MissingValue")`, not
    `("SCE_STORAGE", "SCEC_MISSING_VALUE")`) -- review B5, and
    `serpent.testing._errors`' own module docstring for why that end of the
    choice. The frame level is `("Context", 6)` for every guest-side failure and
    so is not worth a field.

    `None` means "measure it on the first run": a meta-test in
    `test_host_facts_tier1.py` asserts no `HostErr(None)` survives in the table,
    so a placeholder cannot be committed as if it were a fact.
    """

    underlying: tuple[str, str] | None = None


@dataclass(frozen=True)
class Unmodelled:
    """The tier-1 model has no answer, and this is why.

    Not a skip decided by the runner: the reason belongs to the row, so it reads
    without a plan next to it, and the tier-1 leg turns it into an enumerable
    `pytest.skip` (`-rs`) the way `test_env_ttl.py` does for the same gap.
    """

    reason: str


Expectation: TypeAlias = Value | ContractErr | HostErr


@dataclass(frozen=True, kw_only=True)
class HostFact:
    """One question for the real host, with both legs' answers side by side."""

    name: str
    #: The dossier/spec sentence this row proves, with its ID -- a meta-test
    #: requires the ID, so a row cannot claim to prove something unattributable.
    fact: str
    constructor: tuple[ChainValue, ...] = ()
    sequence: int | None = None
    #: `None` is mock-all-auths; a tuple is the allow-set (S4). Carried on the
    #: ROW rather than derived from its name by the runners: the auth row is the
    #: only one that needs an allow-set today, and a runner that recognised it
    #: by name would silently stop authorizing anything the day it was renamed.
    auth_allow_set: tuple[Address, ...] | None = None
    setup: tuple[Step, ...] = ()
    invoke: Call
    real: Expectation
    tier1: Expectation | Unmodelled
    #: REQUIRED exactly when `real != tier1` and the tier-1 side is not
    #: `Unmodelled`: which side is RIGHT, and why. A divergence with no reason is
    #: a fact nobody decided.
    divergence_reason: str | None = None
    #: M3: set when the TEST host is known to differ from the CHAIN. Such a row
    #: is evidence about the test host only.
    chain_unproven: str | None = None
    #: E6: the footprint COUNTS the real leg asserts off `resources()`.
    write_entries: int | None = None
    #: `memory_read_entries + disk_read_entries`.
    read_entries: int | None = None


#: The tier-1 model's one blind spot in this table, written once: the network
#: maximum live-until is readable only through `get_max_live_until_ledger`, an M2
#: host function the frontend refuses by name (`SPT1033`), so the model applies
#: `extend_to` exactly as given at any magnitude in every bucket. That is not an
#: approximation to compare against -- it is an absence, and these three rows say
#: so instead of pretending the model has an answer.
_NO_MAXIMUM = "no max live-until at tier 1 (D6/E4): `get_max_live_until_ledger` is M2 (SPT1033)"

#: The pinned UNDERLYING classification of 128-bit `//0` on the real host (E15;
#: consumed by Task 8). Probe-confirmed 2026-09-02 for BOTH signednesses:
#: `("Object", "ArithDomain")`. Note what it is NOT: 32-bit `//0` is a WASM trap
#: (`("WasmVm", "ArithDomain")`), because the 32-bit divide is a wasm `i32.div`
#: instruction while the 128-bit one is a host call -- two different layers
#: reporting the same arithmetic domain error, and this constant is the host
#: call's.
DIV128_BY_ZERO_HOST_ERROR: HostErr = HostErr(("Object", "ArithDomain"))

HOST_FACTS: tuple[HostFact, ...] = (
    # --- TTL: the window, the maximum, and the two deaths ----------------------
    HostFact(
        name="an_extension_whose_threshold_is_below_the_current_ttl_is_a_no_op",
        fact="B9: extend_ttl is conditional on threshold; threshold 0 changes nothing",
        sequence=1_000_000,
        setup=(Call("put_p", (U32(1),)),),
        invoke=Call("extend_p", (U32(0), U32(DEFAULT_MAX_ENTRY_TTL + 10_000))),
        # Void on both legs -- and NOT a divergence, even though the two get
        # there differently: the host declines to extend because the remaining
        # TTL (4095) is above the threshold (0), while tier 1 extends, because
        # a never-extended entry's `live_until` is `None` and always passes the
        # guard. The row's observable is the ANSWER, and the real leg asserts
        # the ttl is still 4095 -- which is the half tier 1 cannot be asked.
        real=Value(None),
        tier1=Value(None),
    ),
    HostFact(
        name="max_live_until_is_max_entry_ttl_minus_one",
        fact="S9's `-1`: max_ttl() == max_entry_ttl - 1 (observed 6_311_999, review m12)",
        # Any call: the observable is `env.max_ttl()`, which is a LEDGER fact and
        # not a contract's. `del_absent` is the cheapest method that proves the
        # invocation ran at all.
        invoke=Call("del_absent", ()),
        real=Value(Bool(True)),
        tier1=Unmodelled(_NO_MAXIMUM),
    ),
    HostFact(
        name="persistent_extension_past_the_maximum_clamps",
        fact="S9: persistent extension past max CLAMPS (O14; test_env_ttl skip 349)",
        sequence=1_000_000,
        setup=(Call("put_p", (U32(1),)),),
        invoke=Call("extend_p", (U32(DEFAULT_MAX_ENTRY_TTL), U32(DEFAULT_MAX_ENTRY_TTL + 88_000))),
        # Measured 2026-09-02: Void, and `ttl(KEY) == max_ttl() == 6_311_999`.
        real=Value(None),
        tier1=Unmodelled(_NO_MAXIMUM),
    ),
    HostFact(
        name="temporary_extension_past_the_maximum_traps",
        fact="S9: temporary extension past max TRAPS (O14; test_env_ttl skip 357)",
        sequence=1_000_000,
        setup=(Call("put_t", (U32(1),)),),
        invoke=Call("extend_t", (U32(DEFAULT_MAX_ENTRY_TTL), U32(DEFAULT_MAX_ENTRY_TTL + 88_000))),
        # First-run measurement 2026-09-02: `Storage(InvalidAction)`, frame
        # `("Context", 6)`. The pair the temporary bucket reports for "you asked
        # for a lifetime this bucket may not have" is InvalidACTION, where the
        # inverted window below is InvalidINPUT -- the host distinguishes a
        # malformed request from a forbidden one, and this table is the only
        # place in the repo that records the difference. The SAME call on the
        # persistent bucket clamps instead (the row above): that asymmetry is
        # S9's, and this pair of rows is its whole proof.
        real=HostErr(("Storage", "InvalidAction")),
        tier1=Unmodelled(_NO_MAXIMUM),
    ),
    HostFact(
        name="extend_to_below_threshold_is_itself_an_error",
        fact="B9 probe (O14): extend_p(threshold=1_000_000, extend_to=100_000) errors",
        sequence=1_000_000,
        setup=(Call("put_p", (U32(1),)),),
        invoke=Call("extend_p", (U32(1_000_000), U32(100_000))),
        real=HostErr(("Storage", "InvalidInput")),
        # Tier 1 mirrors this since finding F1 (2026-09-02): `StorageTrap`
        # carrying the host's own `Storage(InvalidInput)` words, mapped by the
        # tier-1 runner. Not a `ContractError` on either leg -- the host TRAPS.
        tier1=HostErr(("Storage", "InvalidInput")),
    ),
    HostFact(
        name="extending_a_never_written_key_errors",
        fact="S9: extending a dead entry errors (O14)",
        # `threshold <= extend_to`, DELIBERATELY, and not the brief's
        # `(MAX, 100)`: the host checks the window precondition BEFORE it looks
        # for the entry (measured 2026-09-02 -- `(MAX, 100)` reports
        # `Storage(InvalidInput)`, the row above's pair), so an inverted window
        # here would prove the row above twice and this row's fact not at all.
        invoke=Call("extend_p", (U32(100), U32(1_000))),
        real=HostErr(("Storage", "MissingValue")),
        tier1=HostErr(("Storage", "MissingValue")),
    ),
    HostFact(
        name="a_lapsed_temporary_entry_reads_absent",
        fact="O14: a lapsed temporary entry is gone for good",
        sequence=1_000_000,
        setup=(Call("put_t", (U32(7),)), Advance(DEFAULT_MIN_TEMP_ENTRY_TTL + 1)),
        invoke=Call("get_t_or", (U32(0),)),
        # Measured 2026-09-02: a fresh temporary entry's ttl is 15 (the 16-ledger
        # floor, less the current ledger), and 17 ledgers later the read falls
        # through to the default.
        real=Value(U32(0)),
        tier1=Value(U32(7)),
        divergence_reason=(
            "the HOST is right; tier-1 model gap: a never-extended entry has `live_until = None` "
            "at tier 1 and therefore lives forever (`serpent.env`'s four model choices), so the "
            "model never reaches the temporary bucket's 16-ledger floor at all. Carried to M2, "
            "where the model learns the network's minimum entry TTLs"
        ),
    ),
    HostFact(
        name="a_lapsed_persistent_entry_stays_readable_on_the_test_host",
        fact="O14/M3: chain ARCHIVES a lapsed persistent entry; the sdk test Env does NOT model that",
        sequence=1_000_000,
        setup=(Call("put_p", (U32(7),)), Advance(DEFAULT_MIN_PERSISTENT_ENTRY_TTL + 1)),
        invoke=Call("get_p_or", (U32(0),)),
        # Probe-confirmed 2026-09-02: STILL READABLE past live_until, and the
        # access RESTORES the entry -- `ttl(KEY)` reads a fresh 4095 afterwards.
        real=Value(U32(7)),
        # And tier 1 answers the same, for a COMPLETELY different reason: its
        # never-extended entry never expired in the first place. Two right
        # answers by accident is still agreement, so this row carries no
        # `divergence_reason` -- what it carries is `chain_unproven`, because
        # the third answer, the chain's, is the one neither leg gives.
        tier1=Value(U32(7)),
        chain_unproven=(
            "archival is ledger-level behaviour the sdk test Env does not model: tier 1 says "
            "present (a never-extended entry never lapses), the test host says present (it "
            "RESTORES the entry on access, with a fresh 4095), and the chain says ARCHIVED -- the "
            "access fails until a restore footprint pays for it. Provable only at tier 3, carried "
            "to M2. If this test host ever starts archiving, this row fails and the declaration is "
            "retired deliberately rather than edited away"
        ),
    ),
    # --- footprint counts (E6) -------------------------------------------------
    HostFact(
        name="del_of_an_absent_key_is_a_no_op",
        fact="O13: `del_` on an absent key is a no-op on the host, as both models assume",
        invoke=Call("del_absent", ()),
        real=Value(Bool(True)),
        tier1=Value(Bool(True)),
        # Measured 2026-09-02: the DELETED KEY is in the read-write footprint
        # whether or not anything was there, so it counts as a write entry with
        # `write_bytes == 0`. The three reads are the wasm code entry, the
        # contract instance, and the data key.
        write_entries=1,
        read_entries=3,
    ),
    HostFact(
        name="a_single_slot_write_is_one_write_entry",
        fact="E6: a derivable footprint count -- put_p writes the slot, and only the slot",
        invoke=Call("put_p", (U32(1),)),
        real=Value(None),
        tier1=Value(None),
        # The PREDICTION was 2 (the slot AND the contract instance entry) and the
        # first run FALSIFIED it: measured 1 on 2026-09-02, with
        # `write_bytes == 76` and `persistent_entry_rent_bumps == 1`. The
        # instance entry is READ, not rewritten, by a data write -- it appears in
        # `memory_read_entries` (3, as in the row above) and never in
        # `write_entries`. Recorded here rather than quietly re-typed, because
        # M9's rule is that a footprint count is a claim to be checked: the check
        # failed, and this is the corrected claim.
        write_entries=1,
        read_entries=3,
    ),
    # --- rollback and auth (S9/O15, O19/O26) -----------------------------------
    HostFact(
        name="an_event_published_before_a_raise_is_rolled_back",
        fact="S9/O15: events roll back with the failed frame",
        invoke=Call("publish_then_raise", (ALLOWED,)),
        real=ContractErr(9),
        # The SAME outcome on both legs -- and the EVENTS differ, which is why
        # this row is here: tier 1 keeps the event, the host does not. The real
        # leg asserts `events() == ()` (measured 2026-09-02); the difference is
        # not a `divergence_reason`, because the row's expectation -- the outcome
        # -- genuinely agrees, and `ENV_SCENARIOS`' publish-then-raise row already
        # carries the event-level declaration (`HostDivergence`) that this fact
        # belongs to.
        tier1=ContractErr(9),
    ),
    HostFact(
        name="a_refused_auth_is_an_auth_trap_and_records_nothing",
        fact="O19/O26: refusal traps (underlying Auth); nothing is recorded",
        auth_allow_set=(ALLOWED,),
        invoke=Call("guard", (OTHER,)),
        real=HostErr(("Auth", "InvalidAction")),
        # Tier 1's `AuthorizationFailed`, mapped by the tier-1 runner. Both legs
        # record NOTHING (measured 2026-09-02: `auths() == ()` on the host,
        # `env.recorded_auths == ()` at tier 1) -- a refused authorization is
        # not a recorded one, which is the half of O19 a test could easily miss.
        tier1=HostErr(("Auth", "InvalidAction")),
    ),
    # --- 128-bit division (O10/O11/E15) ----------------------------------------
    HostFact(
        name="i128_floordiv_truncates_toward_zero",
        fact="O10 (D3): rounding of i256_div",
        invoke=Call("div_i128", (I128(-7), I128(2))),
        real=Value(I128(-3)),
        tier1=Value(I128(-3)),
    ),
    HostFact(
        name="i128_mod_takes_the_dividends_sign",
        fact="O10: `%` sign (A4)",
        invoke=Call("mod_i128", (I128(-7), I128(2))),
        real=Value(I128(-1)),
        tier1=Value(I128(-1)),
    ),
    HostFact(
        name="i128_min_mod_minus_one_is_zero",
        fact="O10: MIN % -1 == 0 without overflow",
        invoke=Call("mod_i128", (I128(-(2**127)), I128(-1))),
        real=Value(I128(0)),
        tier1=Value(I128(0)),
    ),
    HostFact(
        name="i128_div_by_zero_is_a_host_error_not_a_trap_code",
        fact="O11 (E15): the real XDR code",
        invoke=Call("div_i128", (I128(1), I128(0))),
        real=DIV128_BY_ZERO_HOST_ERROR,
        # Tier 1 raises `ZeroDivisionError` -- Python's own, deliberately, since
        # the model runs Python arithmetic -- which the tier-1 runner maps to
        # this pair.
        tier1=DIV128_BY_ZERO_HOST_ERROR,
    ),
    HostFact(
        name="u128_div_by_zero_is_the_same_host_error",
        fact="O11: the UNSIGNED divide reports the same pair (a separate host call)",
        invoke=Call("div_u128", (U128(1), U128(0))),
        real=DIV128_BY_ZERO_HOST_ERROR,
        tier1=DIV128_BY_ZERO_HOST_ERROR,
    ),
)

#: Asked of the host's Compare trait directly (`RealEnv.compare`), no contract in
#: between (review M2). Vectors chosen to separate lexicographic-then-length from
#: length-then-lexicographic, and to answer O12 for small Symbols.
#:
#: Every sign is PINNED (first run, 2026-09-02) -- a `None` here would mean
#: "unmeasured", and a meta-test refuses one:
#:
#: * rows 1-3 are the container answer, and rows 1 and 2 together are the whole
#:   point. `[1]` sorts BEFORE `[1, 0]` (a proper prefix is smaller) but `[2]`
#:   sorts AFTER it -- so the host compares ELEMENTS first and length only as a
#:   tiebreak. A length-major order would have answered `-1` to both. Tier 1 has
#:   no answer for these at all (A15: containers have no `<` in the subset), so
#:   the host's order is recorded here as E12's evidence and the tier-1
#:   implementation is M2's;
#: * rows 4-7 are Symbols, where tier 1 DOES have an answer (`Symbol.__lt__`),
#:   and the real leg compares the two. All four agreed on the first run:
#:   ASCII text order, so `"_"` (0x5F) > `"A"` (0x41) and `"a"` (0x61) > `"B"`
#:   (0x42) -- NOT case-insensitive, and not a length-first order either, since
#:   `"abc" < "abcdefghijk"` holds across the small/object boundary (a Symbol of
#:   up to 9 characters is an immediate, a longer one is a host object, and rows
#:   6 and 7 straddle that boundary on purpose: the host's order does not notice
#:   the representation change). A Symbol disagreement is a
#:   `FrozenTableDisagreement` (E10), never a table edit.
COMPARE_VECTORS: tuple[tuple[ChainValue, ChainValue, int | None], ...] = (
    (Vec(U32, [U32(1)]), Vec(U32, [U32(1), U32(0)]), -1),
    (Vec(U32, [U32(2)]), Vec(U32, [U32(1), U32(0)]), 1),
    (Vec(U32, [U32(1), U32(2)]), Vec(U32, [U32(1), U32(3)]), -1),
    (Symbol("_"), Symbol("A"), 1),
    (Symbol("a"), Symbol("B"), 1),
    (Symbol("abcdefghijk"), Symbol("abcdefghijl"), -1),  # object vs object
    (Symbol("abc"), Symbol("abcdefghijk"), -1),  # small vs object
)
