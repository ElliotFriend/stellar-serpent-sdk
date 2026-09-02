"""THE stateful Env table: E9's second differential corpus.

`cases.py` is the FROZEN 59-case table, and it is untouched: every case there is
one EXPRESSION with one outcome, which is the right shape for arithmetic,
containers and ordering, and the wrong shape for the `Env` -- storage answers
depend on what was written before the read, an event is observable only as a
side record, and a TTL answer depends on how far the ledger moved. So ruling E9
called for a SECOND table, owned by sub-plan E, of STATEFUL scenarios. This is
it.

One `EnvScenario` is:

* a contract (the path to its source; the `@contract` class in it is
  discovered) and, if it has one, its constructor arguments;
* the `Env` configuration a scenario needs (`timestamp`, `sequence`, and the
  auth allow-set);
* `setup`: a sequence of steps -- `Call`s whose answers are ignored, and
  `Advance`s that move the ledger sequence so a TTL can lapse;
* `invoke`: the ONE call whose outcome is the observable;
* the expected outcome: a decoded chain value (`kind="value"`), nothing
  (`"void"`), a contract error code (`"contract_error"`), or a refused
  authorization (`"auth_failed"`);
* the events and the authorizations the whole sequence must have recorded, in
  order -- pinned exactly, so a spurious extra event fails the row.

`ENV_SCENARIOS` is importable on purpose (the named carried obligation,
mirroring D11's shape): `tests/unit/test_env_differential.py` runs every row
against the tier-1 model AND against the compiled WASM under the mini host, and
sub-plan F's tier 2b re-runs the same corpus against a real host -- which is
where the comparison stops being two models agreeing and starts being evidence.

**Why a row is ever marked `tier1_only_reason`.**

* **TTL.** `tests/harness`'s mini host has no TTL model at all
  (`extend_contract_data_ttl` is a recorded no-op), so an expiry-sensitive
  answer has no second leg to compare against;
* **auth args, and the auth allow-set.** The mini host DISCARDS
  `require_auth_for_args`' args -- it shape-checks the vec and records only the
  address (review M11) -- and it has no allow-set at all: its `require_auth`
  records and always succeeds.

Both are the harness's limits, not the scenarios': `test_env_differential.py`
asserts the biconditional (a row carries a reason if and only if it reaches one
of those surfaces), so a row cannot quietly opt out of the WASM leg.

**The contracts.** `env_surface.py` is the fixture written for this table -- one
narrow method per Env surface, including the two shapes no shipped contract can
reach (a bare `get` with no `default=`, and `require_auth_for_args`). The two
`token_style` fixtures carry the rows about a REAL contract shape and the two
event spellings: `token_style.py` publishes through the authoring form
(`Transfer(...).publish(env)`) and `token_style_canonical.py` through the
canonical one (`env.events().publish(topics, data)`).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from serpent.env import (
    DEFAULT_LEDGER_SEQUENCE,
    DEFAULT_LEDGER_TIMESTAMP,
    ChainValue,
    PublishedEvent,
    RecordedAuth,
)
from serpent.errors import CODE_MISSING_VALUE

# Every expectation is built with the public root import, exactly as a contract
# author would -- the same rule `cases.py` follows for its `expect` values.
from serpent.types import U32, U64, Address, Bool, String, Symbol, Vec

_ROOT = Path(__file__).resolve().parents[2]

#: The contracts this table drives. `test_env_differential.py` pins the two
#: shared ones against `test_emitter_end_to_end.py`'s own constants, so the
#: paths cannot drift apart.
ENV_SURFACE = _ROOT / "tests" / "fixtures" / "env_surface.py"
TOKEN_STYLE = _ROOT / "tests" / "fixtures" / "token_style.py"
TOKEN_STYLE_CANONICAL = _ROOT / "tests" / "fixtures" / "token_style_canonical.py"

#: Four real strkeys, lifted from `tests/semantics/cases.py` (the first two) and
#: `tests/unit/test_examples.py` (the last two) rather than hand-written, so
#: every `Address` here is one the suite already knows decodes.
ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"
CONTRACT = "CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI"
OWNER = "GA7QYNF7SOWQ3GLR2BGMZEHXAVIRZA4KVWLTJJFC7MGXUA74P7UJVSGZ"
SPENDER = "GAAQEAYEAUDAOCAJBIFQYDIOB4IBCEQTCQKRMFYYDENBWHA5DYPSABOV"

_ADMIN = Address(ACCOUNT)
_OTHER = Address(CONTRACT)
_OWNER = Address(OWNER)
_SPENDER = Address(SPENDER)

#: The keys and the values the storage rows reuse. Two DISTINCT symbol keys,
#: because "the write landed under the key it was given" needs a second key to
#: be a claim at all.
_K = Symbol("K")
_OTHER_KEY = Symbol("Z")

#: The three tier-1-only reasons, written once and shared, so every row that
#: opts out of the WASM leg opts out for a reason this module states.
TTL_REASON = (
    "the mini host has no TTL model at all -- `extend_contract_data_ttl` is a "
    "recorded no-op, and FullHost tracks a ledger sequence but no per-entry "
    "live-until state and applies no expiry on reads -- so an expiry-sensitive "
    "answer has no WASM leg to be compared with"
)
AUTH_ARGS_REASON = (
    "the mini host DISCARDS `require_auth_for_args`' args (it shape-checks the "
    "vec and records only the address, review M11), so an args-sensitive "
    "assertion has no WASM leg"
)
ALLOW_SET_REASON = (
    "the mini host has no authorization state to consult: its `require_auth` "
    "records and always succeeds, so an `Env(auths=...)` allow-set -- and the "
    "refusal it produces -- does not exist on the WASM leg"
)

# `TTL_METHODS`/`AUTH_ARGS_METHODS` -- which method names in a row FORCE
# `tier1_only_reason` -- are RUNNER knowledge (how `test_env_differential.py`'s
# biconditional test derives "is this row tier-1-only" from the surfaces a row
# reaches), not table data any row here reads, so they live next to that test
# in `tests/unit/test_env_differential.py` rather than in this module.


@dataclass(frozen=True)
class Call:
    """One invocation: a method name, and the chain values to pass it.

    Scalars only, and deliberately: the WASM leg encodes each argument with the
    mini host's `val_word`, whose scalar coverage is complete and whose
    container coverage is not (a container argument would have to be built
    through the host's own constructors, which is a different test). A container
    reaches these scenarios as a RESULT -- an event's data, an auth's args --
    never as an argument.
    """

    method: str
    args: tuple[ChainValue, ...] = ()


@dataclass(frozen=True)
class Advance:
    """Move the ledger sequence on by `ledgers`, so a TTL can lapse.

    Tier-1 only by construction: `Env.advance` is a test hook with no analogue
    on either the mini host or the chain (a real ledger closes; nothing
    "advances" it from inside a test).
    """

    ledgers: int


Step: TypeAlias = Call | Advance


@dataclass(frozen=True, kw_only=True)
class EnvScenario:
    """One stateful Env observable, and the outcome both models must produce.

    Frozen, and shaped so that every field is data a re-runner can act on
    without reading this module's tests: sub-plan F's tier 2b deploys
    `contract`'s class with `constructor`, replays `setup`, invokes `invoke` and
    compares the outcome, exactly as `test_env_differential.py` does for the two
    tiers here.
    """

    name: str
    #: The contract source. The `@contract` class in it is DISCOVERED rather
    #: than named here: a serpent module declares exactly one, and a second
    #: field naming it could disagree with the file.
    contract: Path
    #: The constructor arguments, for a contract that has an `__init__`.
    constructor: tuple[ChainValue, ...] = ()
    #: The ledger the scenario starts on. Defaults are NOT restated here --
    #: `Env`'s own defaults apply when a row says nothing, and the differential
    #: pins those defaults equal to the mini host's.
    timestamp: int | None = None
    sequence: int | None = None
    #: `None` is mock-all-auths; a tuple is the allow-set (S4).
    auth_allow_set: tuple[Address, ...] | None = None
    setup: tuple[Step, ...] = ()
    invoke: Call
    kind: Literal["value", "void", "contract_error", "auth_failed"]
    #: The decoded answer, for `kind="value"`.
    expect: ChainValue | None = None
    #: The contract error code, for `kind="contract_error"`.
    code: int | None = None
    #: Every event the WHOLE sequence must have published, in order, and every
    #: authorization it must have recorded. Pinned exactly (the default is
    #: "none at all"), so a spurious extra record fails the row rather than
    #: passing unnoticed.
    events: tuple[PublishedEvent, ...] = ()
    auths: tuple[RecordedAuth, ...] = ()
    #: Set exactly when the row reaches a surface THIS mini host does not
    #: model (TTL, auth args, the allow-set) -- i.e. it has no SECOND LEG to
    #: compare against in this differential today. The name overstates the
    #: reach: a real host at sub-plan F's tier 2b CAN replay the allow-set and
    #: auth-args rows (the mini host's limits are its own, not every host's),
    #: so "tier1_only" means "no mini-host leg here," not "no host can ever
    #: run this."
    tier1_only_reason: str | None = None


#: The event both spellings publish, pinned ONCE and shared by the two rows
#: that reach it through different source spellings -- which is what makes
#: `test_both_publish_spellings_pin_one_record` a claim about the two
#: LOWERINGS rather than about two independently written expectations.
LOGGED_EVENT: PublishedEvent = ((Symbol("logged"), _ADMIN), U32(3))

#: `token_style.py`'s `Transfer`, and `token_style_canonical.py`'s hand-written
#: equivalent -- the same shape, on the two fixtures the M1-E event work pairs.
_TRANSFER_EVENT: PublishedEvent = ((Symbol("transfer"), _OWNER, _SPENDER), U32(25))
_SEND_EVENT: PublishedEvent = ((Symbol("transfer"), _OWNER, _SPENDER), U32(9))

ENV_SCENARIOS: tuple[EnvScenario, ...] = (
    # === instance storage: set/get, the two default arms, has, del_ ==========
    EnvScenario(
        name="instance_set_then_get",
        contract=ENV_SURFACE,
        setup=(Call("put_instance", (_K, U32(7))),),
        invoke=Call("read_instance", (_K,)),
        kind="value",
        expect=U32(7),
    ),
    EnvScenario(
        name="instance_overwrite_is_the_later_value",
        contract=ENV_SURFACE,
        setup=(Call("put_instance", (_K, U32(7))), Call("put_instance", (_K, U32(8)))),
        invoke=Call("read_instance", (_K,)),
        kind="value",
        expect=U32(8),
    ),
    EnvScenario(
        # The reserved-code miss, at BOTH tiers: tier 1 raises `MissingValue`,
        # and the compiled form's `has_contract_data` guard fails with the same
        # `CODE_MISSING_VALUE` (ruling E13/E14).
        name="instance_bare_get_of_a_missing_key_is_the_reserved_missing_value_code",
        contract=ENV_SURFACE,
        invoke=Call("read_instance", (_OTHER_KEY,)),
        kind="contract_error",
        code=CODE_MISSING_VALUE,
    ),
    EnvScenario(
        name="instance_get_with_a_default_answers_the_default_on_a_miss",
        contract=ENV_SURFACE,
        setup=(Call("put_instance", (_K, U32(7))),),
        invoke=Call("read_instance_or", (_OTHER_KEY, U32(1))),
        kind="value",
        expect=U32(1),
    ),
    EnvScenario(
        # The EAGER-DEFAULT row: a model that reached for `default` before
        # asking whether the entry is there answers 1 here.
        name="instance_get_with_a_default_still_answers_the_stored_value_on_a_hit",
        contract=ENV_SURFACE,
        setup=(Call("put_instance", (_K, U32(7))),),
        invoke=Call("read_instance_or", (_K, U32(1))),
        kind="value",
        expect=U32(7),
    ),
    EnvScenario(
        # The RAW-LITERAL default row: `default=0`, not `default=U32(0)`. The
        # compiled tier ADOPTS the literal (M1-C, typed position), so the
        # `IfExp` orelse is `U32(0)`; a tier-1 model that handed back the Python
        # `0` would agree with this row's `expect` and disagree with the WASM leg
        # about the answer's TYPE. Both the two-leg compare and the
        # `answer_type` assertion against `expect` catch it.
        name="instance_get_with_a_raw_literal_default_adopts_it_as_a_chain_value",
        contract=ENV_SURFACE,
        setup=(Call("put_instance", (_K, U32(7))),),
        invoke=Call("read_instance_or_zero", (_OTHER_KEY,)),
        kind="value",
        expect=U32(0),
    ),
    EnvScenario(
        name="instance_has_is_false_before_the_write",
        contract=ENV_SURFACE,
        invoke=Call("has_instance", (_K,)),
        kind="value",
        expect=Bool(False),
    ),
    EnvScenario(
        # The chain `Bool` row: the differential compares the TYPE of the two
        # answers as well as their value, so a `has` that answered a plain
        # Python `bool` fails here against the WASM leg's `Bool`.
        name="instance_has_is_true_after_the_write",
        contract=ENV_SURFACE,
        setup=(Call("put_instance", (_K, U32(7))),),
        invoke=Call("has_instance", (_K,)),
        kind="value",
        expect=Bool(True),
    ),
    EnvScenario(
        name="instance_del_then_has_is_false",
        contract=ENV_SURFACE,
        setup=(Call("put_instance", (_K, U32(7))), Call("drop_instance", (_K,))),
        invoke=Call("has_instance", (_K,)),
        kind="value",
        expect=Bool(False),
    ),
    EnvScenario(
        name="instance_del_then_a_bare_get_is_missing",
        contract=ENV_SURFACE,
        setup=(Call("put_instance", (_K, U32(7))), Call("drop_instance", (_K,))),
        invoke=Call("read_instance", (_K,)),
        kind="contract_error",
        code=CODE_MISSING_VALUE,
    ),
    EnvScenario(
        name="instance_del_of_an_absent_key_is_a_silent_no_op",
        contract=ENV_SURFACE,
        setup=(Call("put_instance", (_K, U32(7))), Call("drop_instance", (_OTHER_KEY,))),
        invoke=Call("read_instance", (_K,)),
        kind="value",
        expect=U32(7),
    ),
    # === persistent storage, under a STRUCT key =============================
    EnvScenario(
        # The key round trip: the read REBUILDS `Slot(owner=...)` from the
        # address it was passed, so a store keyed on the object handle rather
        # than on the value would miss (`tests/harness/objects.py`'s own
        # docstring names that bug).
        name="persistent_struct_key_round_trips_across_two_invocations",
        contract=ENV_SURFACE,
        setup=(Call("put_slot", (_OWNER, U32(5))),),
        invoke=Call("read_slot", (_OWNER,)),
        kind="value",
        expect=U32(5),
    ),
    EnvScenario(
        name="persistent_struct_keys_with_different_fields_are_different_entries",
        contract=ENV_SURFACE,
        setup=(Call("put_slot", (_OWNER, U32(5))),),
        invoke=Call("read_slot_or", (_SPENDER, U32(0))),
        kind="value",
        expect=U32(0),
    ),
    EnvScenario(
        name="persistent_bare_get_of_a_missing_struct_key_is_missing",
        contract=ENV_SURFACE,
        invoke=Call("read_slot", (_OWNER,)),
        kind="contract_error",
        code=CODE_MISSING_VALUE,
    ),
    EnvScenario(
        name="persistent_has_is_true_after_the_write",
        contract=ENV_SURFACE,
        setup=(Call("put_slot", (_OWNER, U32(5))),),
        invoke=Call("has_slot", (_OWNER,)),
        kind="value",
        expect=Bool(True),
    ),
    EnvScenario(
        name="persistent_del_then_has_is_false",
        contract=ENV_SURFACE,
        setup=(Call("put_slot", (_OWNER, U32(5))), Call("drop_slot", (_OWNER,))),
        invoke=Call("has_slot", (_OWNER,)),
        kind="value",
        expect=Bool(False),
    ),
    # === temporary storage ==================================================
    EnvScenario(
        name="temporary_set_then_get",
        contract=ENV_SURFACE,
        setup=(Call("put_temp", (_K, U32(7))),),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(7),
    ),
    EnvScenario(
        name="temporary_has_is_false_before_the_write",
        contract=ENV_SURFACE,
        invoke=Call("has_temp", (_K,)),
        kind="value",
        expect=Bool(False),
    ),
    EnvScenario(
        name="temporary_has_is_true_after_the_write",
        contract=ENV_SURFACE,
        setup=(Call("put_temp", (_K, U32(7))),),
        invoke=Call("has_temp", (_K,)),
        kind="value",
        expect=Bool(True),
    ),
    # === the three durabilities are three namespaces ========================
    EnvScenario(
        name="an_instance_write_is_not_visible_in_temporary_storage",
        contract=ENV_SURFACE,
        setup=(Call("put_instance", (_K, U32(1))),),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(0),
    ),
    EnvScenario(
        name="a_temporary_write_is_not_visible_in_instance_storage",
        contract=ENV_SURFACE,
        setup=(Call("put_temp", (_K, U32(1))),),
        invoke=Call("read_instance_or", (_K, U32(0))),
        kind="value",
        expect=U32(0),
    ),
    EnvScenario(
        name="the_same_key_in_two_durabilities_holds_two_values",
        contract=ENV_SURFACE,
        setup=(Call("put_instance", (_K, U32(1))), Call("put_temp", (_K, U32(2)))),
        invoke=Call("read_instance", (_K,)),
        kind="value",
        expect=U32(1),
    ),
    # === events, both spellings, and the no-rollback pin ====================
    EnvScenario(
        name="the_declared_event_spelling_publishes_the_record",
        contract=ENV_SURFACE,
        invoke=Call("log_declared", (_ADMIN, U32(3))),
        kind="void",
        events=(LOGGED_EVENT,),
    ),
    EnvScenario(
        name="the_canonical_event_spelling_publishes_the_same_record",
        contract=ENV_SURFACE,
        invoke=Call("log_canonical", (_ADMIN, U32(3))),
        kind="void",
        events=(LOGGED_EVENT,),
    ),
    EnvScenario(
        name="both_spellings_in_one_sequence_publish_two_identical_records",
        contract=ENV_SURFACE,
        setup=(Call("log_declared", (_ADMIN, U32(3))),),
        invoke=Call("log_canonical", (_ADMIN, U32(3))),
        kind="void",
        events=(LOGGED_EVENT, LOGGED_EVENT),
    ),
    EnvScenario(
        # F.1.8: BOTH models keep the event of a method that then raises. The
        # chain rolls it back with the frame, and neither model does -- the
        # honest pin, carried to sub-plan F (see the differential's own
        # docstring for that row).
        name="an_event_published_before_a_raise_survives_at_both_tiers",
        contract=ENV_SURFACE,
        invoke=Call("log_then_refuse", (_ADMIN, U32(3))),
        kind="contract_error",
        code=1,
        events=(LOGGED_EVENT,),
    ),
    # === auth ===============================================================
    EnvScenario(
        name="require_auth_records_the_address",
        contract=ENV_SURFACE,
        invoke=Call("guard", (_ADMIN,)),
        kind="void",
        auths=((_ADMIN, None),),
    ),
    EnvScenario(
        name="require_auth_records_one_entry_per_call_in_order",
        contract=ENV_SURFACE,
        setup=(Call("guard", (_ADMIN,)),),
        invoke=Call("guard", (_OTHER,)),
        kind="void",
        auths=((_ADMIN, None), (_OTHER, None)),
    ),
    EnvScenario(
        name="require_auth_for_args_records_a_snapshot_of_its_args",
        contract=ENV_SURFACE,
        invoke=Call("guard_args", (_ADMIN, U32(9))),
        kind="void",
        auths=((_ADMIN, Vec(U32, [U32(9)])),),
        tier1_only_reason=AUTH_ARGS_REASON,
    ),
    EnvScenario(
        name="an_address_in_the_allow_set_is_recorded_and_allowed",
        contract=ENV_SURFACE,
        auth_allow_set=(_ADMIN,),
        invoke=Call("guard", (_ADMIN,)),
        kind="void",
        auths=((_ADMIN, None),),
        tier1_only_reason=ALLOW_SET_REASON,
    ),
    EnvScenario(
        # The refusal is NOT recorded: the host traps, so there is no
        # invocation left to have recorded anything (`_record_auth`).
        name="an_address_outside_the_allow_set_is_refused_and_not_recorded",
        contract=ENV_SURFACE,
        auth_allow_set=(_OTHER,),
        invoke=Call("guard", (_ADMIN,)),
        kind="auth_failed",
        tier1_only_reason=ALLOW_SET_REASON,
    ),
    # === ledger reads =======================================================
    EnvScenario(
        name="the_default_ledger_timestamp_is_the_same_on_both_legs",
        contract=ENV_SURFACE,
        invoke=Call("ledger_time"),
        kind="value",
        # IMPORTED, never restated: `DEFAULT_LEDGER_TIMESTAMP` is the one home
        # both models read (`tests/harness/hostfns.py` imports it from
        # `serpent.env` too), which is what makes this row agree by
        # construction rather than by two matching literals.
        expect=U64(DEFAULT_LEDGER_TIMESTAMP),
    ),
    EnvScenario(
        name="the_default_ledger_sequence_is_the_same_on_both_legs",
        contract=ENV_SURFACE,
        invoke=Call("ledger_seq"),
        kind="value",
        expect=U32(DEFAULT_LEDGER_SEQUENCE),
    ),
    EnvScenario(
        name="a_configured_ledger_timestamp_is_what_the_contract_reads",
        contract=ENV_SURFACE,
        timestamp=1_800_000_000,
        invoke=Call("ledger_time"),
        kind="value",
        expect=U64(1_800_000_000),
    ),
    EnvScenario(
        name="a_configured_ledger_sequence_is_what_the_contract_reads",
        contract=ENV_SURFACE,
        sequence=2_000_000,
        invoke=Call("ledger_seq"),
        kind="value",
        expect=U32(2_000_000),
    ),
    # === TTL: tier 1 only, every row (the mini host has no TTL model) =======
    EnvScenario(
        # S8's expiry is STRICTLY past the live-until ledger.
        name="a_temporary_entry_is_alive_exactly_at_its_live_until",
        contract=ENV_SURFACE,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(2000), U32(1000))),
            Advance(1000),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(7),
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        name="a_temporary_entry_reads_absent_one_ledger_past_its_live_until",
        contract=ENV_SURFACE,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(2000), U32(1000))),
            Advance(1001),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(0),
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        name="an_expired_entry_reads_absent_through_has_too",
        contract=ENV_SURFACE,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(2000), U32(1000))),
            Advance(1001),
        ),
        invoke=Call("has_temp", (_K,)),
        kind="value",
        expect=Bool(False),
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        # NEVER-REDUCE: the second extension asks for 10 ledgers where 1000 are
        # already granted, and the entry is still alive 1000 ledgers later.
        name="a_smaller_extension_after_a_larger_one_never_reduces_the_live_until",
        contract=ENV_SURFACE,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(2000), U32(1000))),
            Call("bump_temp", (_K, U32(2000), U32(10))),
            Advance(1000),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(7),
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        # THE THRESHOLD BOUNDARY, at exact equality (Task 3's carried
        # obligation): the second call sees `live_until - sequence == 1000 ==
        # threshold`, and the guard's `>=` makes it a NO-OP. A guard written
        # `>` would extend to sequence + 5000 and this entry would still be
        # alive below.
        name="the_threshold_guard_blocks_at_exact_equality",
        contract=ENV_SURFACE,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(2000), U32(1000))),
            Call("bump_temp", (_K, U32(1000), U32(5000))),
            Advance(1001),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(0),
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        # One ledger later the SAME two calls fall BELOW the threshold (999 <
        # 1000), so the extension applies and the entry outlives the first
        # grant's live-until.
        name="the_threshold_guard_lets_a_shorter_remaining_lifetime_through",
        contract=ENV_SURFACE,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(2000), U32(1000))),
            Advance(1),
            Call("bump_temp", (_K, U32(1000), U32(5000))),
            Advance(1001),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(7),
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        # A keyed WRITE clears the entry's live-until (Task 8's empirical
        # finding): 1000 ledgers after a 10-ledger grant, the re-set value is
        # still there, because the write made the entry never-extended again.
        name="a_keyed_write_clears_the_live_until",
        contract=ENV_SURFACE,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(2000), U32(10))),
            Call("put_temp", (_K, U32(8))),
            Advance(1000),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(8),
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        # The INSTANCE bucket's live-until is bucket-wide, so a write does NOT
        # clear it -- the opposite answer to the row above, on purpose
        # (`InstanceStorage._forget_live_until` is a deliberate no-op).
        name="an_instance_write_does_not_clear_the_bucket_wide_live_until",
        contract=ENV_SURFACE,
        setup=(
            Call("put_instance", (_K, U32(7))),
            Call("bump_instance", (U32(2000), U32(100))),
            Call("put_instance", (_K, U32(8))),
            Advance(101),
        ),
        invoke=Call("read_instance_or", (_K, U32(0))),
        kind="value",
        expect=U32(0),
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        # Task 3's other carried obligation: `del_` of ONE instance key does
        # not touch the instance's shared live-until, so the remaining key is
        # still alive exactly at it.
        name="the_instance_live_until_survives_a_del_of_one_key",
        contract=ENV_SURFACE,
        setup=(
            Call("put_instance", (_K, U32(1))),
            Call("put_instance", (_OTHER_KEY, U32(2))),
            Call("bump_instance", (U32(2000), U32(100))),
            Call("drop_instance", (_K,)),
            Advance(100),
        ),
        invoke=Call("read_instance_or", (_OTHER_KEY, U32(0))),
        kind="value",
        expect=U32(2),
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        name="the_instance_live_until_still_governs_the_keys_a_del_left_behind",
        contract=ENV_SURFACE,
        setup=(
            Call("put_instance", (_K, U32(1))),
            Call("put_instance", (_OTHER_KEY, U32(2))),
            Call("bump_instance", (U32(2000), U32(100))),
            Call("drop_instance", (_K,)),
            Advance(101),
        ),
        invoke=Call("read_instance_or", (_OTHER_KEY, U32(0))),
        kind="value",
        expect=U32(0),
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        name="a_persistent_struct_keyed_entry_expires_the_same_way",
        contract=ENV_SURFACE,
        setup=(
            Call("put_slot", (_OWNER, U32(5))),
            Call("bump_slot", (_OWNER, U32(2000), U32(1000))),
            Advance(1001),
        ),
        invoke=Call("read_slot_or", (_OWNER, U32(0))),
        kind="value",
        expect=U32(0),
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        # S8's dead-entry rule, for the never-written death.
        name="extending_the_ttl_of_a_key_that_was_never_written_is_loud",
        contract=ENV_SURFACE,
        invoke=Call("bump_temp", (_K, U32(1000), U32(1000))),
        kind="contract_error",
        code=CODE_MISSING_VALUE,
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        # And for the expired death, which is the same answer from outside.
        name="extending_the_ttl_of_an_expired_entry_is_loud",
        contract=ENV_SURFACE,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(2000), U32(1000))),
            Advance(1001),
        ),
        invoke=Call("bump_temp", (_K, U32(1000), U32(1000))),
        kind="contract_error",
        code=CODE_MISSING_VALUE,
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        # Task 3's third carried obligation: the rough edge of the U32 range.
        # The live-until lands on 2**32 - 1 EXACTLY, and the entry is alive
        # there -- the last sequence a `U32` ledger read can even name.
        name="an_entry_whose_live_until_is_the_last_u32_ledger_is_alive_there",
        contract=ENV_SURFACE,
        sequence=2**32 - 1 - 10,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(2000), U32(10))),
            Advance(10),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(7),
        tier1_only_reason=TTL_REASON,
    ),
    EnvScenario(
        # One ledger further the sequence leaves the U32 range entirely. The
        # model's arithmetic is plain Python ints and carries on -- there is NO
        # clamp and NO trap here (S8's asymmetry is the model's named gap), so
        # what this row pins is only that expiry keeps answering past the edge.
        # `test_env_differential.py` pins what the LEDGER READ does there.
        name="expiry_still_answers_one_ledger_past_the_u32_range",
        contract=ENV_SURFACE,
        sequence=2**32 - 1 - 10,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(2000), U32(10))),
            Advance(11),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(0),
        tier1_only_reason=TTL_REASON,
    ),
    # === token_style: a real contract shape, and the authoring spelling =====
    EnvScenario(
        name="token_style_reads_a_string_written_by_its_constructor",
        contract=TOKEN_STYLE,
        constructor=(_ADMIN, String("Serpent Token")),
        invoke=Call("name"),
        kind="value",
        expect=String("Serpent Token"),
    ),
    EnvScenario(
        name="token_style_compares_an_address_read_from_storage",
        contract=TOKEN_STYLE,
        constructor=(_ADMIN, String("Serpent Token")),
        invoke=Call("is_admin", (_ADMIN,)),
        kind="value",
        expect=Bool(True),
    ),
    EnvScenario(
        name="token_style_says_a_stranger_is_not_the_admin",
        contract=TOKEN_STYLE,
        constructor=(_ADMIN, String("Serpent Token")),
        invoke=Call("is_admin", (_OWNER,)),
        kind="value",
        expect=Bool(False),
    ),
    EnvScenario(
        name="token_style_balance_defaults_to_zero_for_an_unknown_owner",
        contract=TOKEN_STYLE,
        constructor=(_ADMIN, String("Serpent Token")),
        invoke=Call("balance", (_OWNER,)),
        kind="value",
        expect=U32(0),
    ),
    EnvScenario(
        name="token_style_mint_records_the_admins_auth_and_credits_the_balance",
        contract=TOKEN_STYLE,
        constructor=(_ADMIN, String("Serpent Token")),
        setup=(Call("mint", (_ADMIN, _OWNER, U32(100))),),
        invoke=Call("balance", (_OWNER,)),
        kind="value",
        expect=U32(100),
        auths=((_ADMIN, None),),
    ),
    EnvScenario(
        name="token_style_transfer_moves_the_balance_and_publishes_the_authoring_form_event",
        contract=TOKEN_STYLE,
        constructor=(_ADMIN, String("Serpent Token")),
        setup=(
            Call("mint", (_ADMIN, _OWNER, U32(100))),
            Call("transfer", (_OWNER, _SPENDER, U32(25))),
        ),
        invoke=Call("balance", (_OWNER,)),
        kind="value",
        expect=U32(75),
        events=(_TRANSFER_EVENT,),
        auths=((_ADMIN, None), (_OWNER, None)),
    ),
    EnvScenario(
        name="token_style_transfer_credits_the_recipient",
        contract=TOKEN_STYLE,
        constructor=(_ADMIN, String("Serpent Token")),
        setup=(
            Call("mint", (_ADMIN, _OWNER, U32(100))),
            Call("transfer", (_OWNER, _SPENDER, U32(25))),
        ),
        invoke=Call("balance", (_SPENDER,)),
        kind="value",
        expect=U32(25),
        events=(_TRANSFER_EVENT,),
        auths=((_ADMIN, None), (_OWNER, None)),
    ),
    EnvScenario(
        # The refusal writes nothing and publishes nothing -- the auth is
        # recorded, because `require_auth` runs before the balance check.
        name="token_style_transfer_over_the_balance_refuses_with_the_contracts_own_code",
        contract=TOKEN_STYLE,
        constructor=(_ADMIN, String("Serpent Token")),
        setup=(Call("mint", (_ADMIN, _OWNER, U32(10))),),
        invoke=Call("transfer", (_OWNER, _SPENDER, U32(25))),
        kind="contract_error",
        code=1,
        auths=((_ADMIN, None), (_OWNER, None)),
    ),
    # === token_style_canonical: the canonical spelling on a real contract ====
    EnvScenario(
        name="token_style_canonical_send_publishes_the_hand_written_topic_tuple",
        contract=TOKEN_STYLE_CANONICAL,
        constructor=(_ADMIN,),
        invoke=Call("send", (_OWNER, _SPENDER, U32(9))),
        kind="void",
        events=(_SEND_EVENT,),
        auths=((_OWNER, None),),
    ),
    # === unions and int enums (M1-E2 ruling E13) =============================
    # A union or an int enum reaches these rows as a RESULT or through a
    # setup-built store, never as a `Call` argument (`Call.args` is scalars
    # only, above) -- so every row here writes the new kind from INSIDE the
    # contract and reads a scalar back out.
    EnvScenario(
        # E13: a union's storage round trip exercises `storage_key`'s `Vec`
        # branch and the `"vec"` tag family, which no existing scenario
        # reaches with a UDT.
        name="union_round_trips_through_persistent_storage",
        contract=ENV_SURFACE,
        setup=(Call("put_shape", (U32(3),)),),
        invoke=Call("read_shape_area", ()),
        kind="value",
        expect=U32(3),
    ),
    EnvScenario(
        name="int_enum_round_trips_and_compares",
        contract=ENV_SURFACE,
        setup=(Call("put_color", (U32(1),)),),
        invoke=Call("color_is_green", ()),
        kind="value",
        expect=Bool(True),
    ),
    EnvScenario(
        # S13's key round trip, over a UNION key rather than a struct key: the
        # read rebuilds a fresh, value-equal key rather than remembering the
        # one the write used.
        name="union_keyed_entry_is_found_by_a_rebuilt_value_equal_key",
        contract=ENV_SURFACE,
        setup=(Call("put_by_shape_key", (U32(7),)),),
        invoke=Call("read_by_shape_key", ()),
        kind="value",
        expect=U32(7),
    ),
)
