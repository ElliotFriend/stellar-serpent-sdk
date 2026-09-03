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
`tests/real_host/test_env_scenarios_real.py` re-runs the same corpus against a
real host -- which is where the comparison stops being two models agreeing and
starts being evidence.

**Why a row is ever marked `mini_host_gap`.**

* **TTL.** `tests/harness`'s mini host has no TTL model at all
  (`extend_contract_data_ttl` is a recorded no-op), so an expiry-sensitive
  answer has no second leg to compare against;
* **auth args, and the auth allow-set.** The mini host DISCARDS
  `require_auth_for_args`' args -- it shape-checks the vec and records only the
  address (review M11) -- and it has no allow-set at all: its `require_auth`
  records and always succeeds.

Both are the MINI HOST's limits, not the scenarios' and not every host's --
which is why ruling E8 renamed the field from `tier1_only_reason`:
`tests/real_host/test_env_scenarios_real.py` replays all 62 rows on the real
host, the marked ones included. `test_env_differential.py` asserts the
biconditional (a row carries a reason if and only if it reaches one of those
surfaces), so a row cannot quietly opt out of the WASM leg.

**Why a row ever carries `host_diverges`.** Ruling E9: where the tier-1 model
is known wrong-by-omission, the row DECLARES the difference rather than the
real leg discovering it, and the real-host runner asserts the difference still
EXISTS -- so the day the model is fixed (an M2 oracle edit) the declaration
fails loudly instead of rotting.

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

#: The deployed shapes contract's id -- a real, decodable CONTRACT strkey.
#: `_ADMIN` is built from THIS rather than from `ACCOUNT`, and that is the one
#: value-level edit ruled into this frozen table (B2, 2026-09-02): the test
#: host mocks an authorization by registering a `MockAuthContract` AT the
#: authorizer's address, which a `G...` account cannot host (account
#: authorization needs real ed25519 signatures, which is M2). Every row here
#: treats the authorizer as OPAQUE -- it is passed in, recorded, and compared,
#: never parsed -- so the rows' meaning is unchanged and the allow-set rows
#: become replayable against the real host (`test_env_scenarios_real.py`). Safe
#: against the `MockAuthContract`
#: registration too: the deployed contract's own address is freshly generated
#: by `register`, so it can never equal this constant (the one collision
#: `serpent_host.mock_auths` warns about).
SHAPES_CONTRACT = "CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW"

_ADMIN = Address(SHAPES_CONTRACT)
_OTHER = Address(CONTRACT)
_OWNER = Address(OWNER)
_SPENDER = Address(SPENDER)

#: The keys and the values the storage rows reuse. Two DISTINCT symbol keys,
#: because "the write landed under the key it was given" needs a second key to
#: be a claim at all.
_K = Symbol("K")
_OTHER_KEY = Symbol("Z")

#: The three MINI-HOST gaps, written once and shared, so every row that opts
#: out of the WASM leg opts out for a reason this module states. Each one names
#: the mini host and says what the real host does with the row instead (O18,
#: ruling E8): the old `TTL_REASON` also claimed a missing ledger sequence,
#: which was never true of `FullHost` -- it tracks one, it just has no
#: per-entry live-until to compare it against.
TTL_REASON = (
    "the MINI HOST has no TTL model at all -- `extend_contract_data_ttl` is a "
    "recorded no-op and no read applies expiry, so an expiry-sensitive answer "
    "has no WASM leg to be compared with; tests/real_host/test_env_scenarios_real.py "
    "runs this row against the real host"
)
AUTH_ARGS_REASON = (
    "the MINI HOST DISCARDS `require_auth_for_args`' args (it shape-checks the "
    "vec and records only the address, review M11), so an args-sensitive "
    "assertion has no WASM leg; tests/real_host/test_env_scenarios_real.py runs "
    "this row against the real host"
)
ALLOW_SET_REASON = (
    "the MINI HOST has no authorization state to consult: its `require_auth` "
    "records and always succeeds, so an `Env(auths=...)` allow-set -- and the "
    "refusal it produces -- does not exist on the WASM leg; "
    "tests/real_host/test_env_scenarios_real.py runs this row against the real "
    "host"
)

# `TTL_METHODS`/`AUTH_ARGS_METHODS` -- which method names in a row FORCE
# `mini_host_gap` -- are RUNNER knowledge (how `test_env_differential.py`'s
# biconditional test derives "does this row have a mini-host leg" from the
# surfaces a row reaches), not table data any row here reads, so they live
# next to that test in `tests/unit/test_env_differential.py` rather than in
# this module.


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

    Tier-1 and real-host: `Env.advance` moves the model's sequence;
    `RealEnv.advance` moves the embedded host's ledger sequence. The MINI host
    has no per-entry live-until state, so an `Advance` forces `mini_host_gap`.
    Nothing "advances" the chain from inside a test -- tier 3 never replays
    this step.
    """

    ledgers: int


Step: TypeAlias = Call | Advance


@dataclass(frozen=True)
class HostDivergence:
    """A declared, EXPECTED difference between the tier-1 model and the real host.

    Ruling E9: where the tier-1 model is known wrong-by-omission, the row says
    so HERE and `tests/real_host/test_env_scenarios_real.py` asserts the
    difference still exists. A divergence the runner cannot find is a
    declaration to retire, which is the point -- the alternative is a comment
    that quietly stops being true the day the model is fixed (an M2 oracle
    edit, once the evidence is in).

    * `reason` cites the dossier/review fact, so the row does not have to be
      read alongside a plan to be understood;
    * `events` is what the REAL host records for the WHOLE sequence. The
      model's `events` field stays the MODEL's -- neither is edited to match
      the other;
    * `answer` is the real leg's expected answer where it differs too, and
      `None` where the answer agrees and only the events diverge. It exists
      because the archival divergence (M3) is an ANSWER difference with no
      event in sight;
    * `auths` is the real leg's expected authorization record, in the ROW's
      vocabulary -- `None` args where only the address is claimed, exactly as
      `EnvScenario.auths` is written -- and `None` for the whole field when the
      auths agree with tier 1. Added for finding F5 (ruled 2026-09-02): a frame
      that later fails records no auth on the host, where tier 1 keeps it, and
      that is the auth half of the same S9 rollback the publish-then-raise row
      declares for events.

    At least one of the three has to differ from the row, which
    `test_env_differential.py`'s row-coherence test asserts: a declaration that
    matches the model everywhere declares nothing.
    """

    reason: str
    events: tuple[PublishedEvent, ...]
    answer: ChainValue | None = None
    auths: tuple[RecordedAuth, ...] | None = None


@dataclass(frozen=True, kw_only=True)
class EnvScenario:
    """One stateful Env observable, and the outcome both models must produce.

    Frozen, and shaped so that every field is data a re-runner can act on
    without reading this module's tests: `tests/real_host/test_env_scenarios_real.py`
    deploys `contract`'s class with `constructor`, replays `setup`, invokes
    `invoke` and compares the outcome, exactly as `test_env_differential.py`
    does for the two tiers here.
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
    #: `"host_error"` is the fifth kind, added for finding F2 (ruled
    #: 2026-09-02): the call TRAPS rather than answering or returning a
    #: contract code. Each leg asserts it in its own vocabulary -- tier 1
    #: raises `serpent.env.StorageTrap`, the real leg raises `RealHostError`
    #: (never `RealContractError`) and matches `host_error` against the
    #: underlying diagnostic pair. Every such row is `mini_host_gap`ped today,
    #: because reaching a storage trap needs the TTL surface the mini host does
    #: not model.
    kind: Literal["value", "void", "contract_error", "auth_failed", "host_error"]
    #: The decoded answer, for `kind="value"`.
    expect: ChainValue | None = None
    #: The contract error code, for `kind="contract_error"`.
    code: int | None = None
    #: The host's `(ScErrorType, ScErrorCode)` pair, for `kind="host_error"` --
    #: the UNDERLYING diagnostic, because the frame-level pair is
    #: `("Context", 6)` for every guest-side failure (review B5) and so carries
    #: no information.
    host_error: tuple[str, str] | None = None
    #: Every event the WHOLE sequence must have published, in order, and every
    #: authorization it must have recorded. Pinned exactly (the default is
    #: "none at all"), so a spurious extra record fails the row rather than
    #: passing unnoticed.
    events: tuple[PublishedEvent, ...] = ()
    auths: tuple[RecordedAuth, ...] = ()
    #: Set exactly when the row reaches a surface THIS MINI HOST does not model
    #: (TTL, auth args, the allow-set) -- i.e. it has no WASM leg to compare
    #: against in `test_env_differential.py` today, and nothing more than that.
    #: Named `mini_host_gap` rather than `tier1_only_reason` (ruling E8)
    #: because the old name overstated the reach: the REAL host has a settable
    #: ledger sequence, honours `require_auth_for_args`' args and has a real
    #: allow-set, so `tests/real_host/test_env_scenarios_real.py`
    #: runs every row marked here. The gap is one harness's, not the corpus's.
    mini_host_gap: str | None = None
    #: A declared, expected real-host-vs-tier-1 difference (ruling E9), or
    #: `None` when the two are expected to agree. Set BEFORE the real leg is
    #: ever run, from a fact already on record -- a first run that "discovers"
    #: what to declare here is a differential fitted to its host.
    host_diverges: HostDivergence | None = None
    #: Why the REAL leg cannot host this row at all, or `None` (finding F3,
    #: ruled 2026-09-02). Not a divergence and not a gap in the row: the two
    #: rows that carry it drive the ledger sequence to the top of the `U32`
    #: range, which the model can do and no host can. The real leg SKIPS such a
    #: row loudly -- counted in the summary, with this reason -- and
    #: `tests/real_host/test_env_scenarios_real.py` pins exactly which rows
    #: carry it, so a third one cannot appear quietly.
    real_unrunnable: str | None = None


#: **The F1 rewrite (ruled 2026-09-02, "M1-F Task 5 rulings").** Twelve rows
#: below were written with `extend_ttl(threshold=2000, extend_to=1000)`-shaped
#: calls, which the real host REFUSES outright: it requires
#: `threshold <= extend_to` (`Storage(InvalidInput)`, "threshold must be <=
#: extend_to"). The rows were unrunnable on every host, so their TTL
#: PARAMETERS were rewritten to host-legal values that keep each row's claim,
#: one ledgered `# F1 rewrite:` line per row. `(1000, 1000)` is the canonical
#: replacement -- a fresh temporary entry has 15 ledgers of TTL on the host, so
#: `15 < 1000` opens the guard and the grant lands on `sequence + 1000`, which
#: is what the old `(2000, 1000)` meant to say.
#:
#: Two claims could not survive host semantics and are named where they were
#: narrowed: NEVER-REDUCE is unreachable through any host-legal call (it needs
#: `threshold <= extend_to < remaining < threshold`), and a keyed write does
#: not clear a temporary entry's live-until on the host (measured: the TTL
#: survives a re-write), which is a declared divergence rather than a rewrite.

#: The M3 archival divergence, written once and shared by every row whose
#: `Advance` lapses a PERSISTENT or INSTANCE entry and then reads it. Declared
#: before the real leg was ever run, from Task 1's measurement -- so no row
#: below spends an E10 escalation cycle on a known harness limit.
ARCHIVAL_REASON = (
    "review M3, and the host fact Task 1 measured on this test host: the sdk "
    "test `Env` does not model archival. A PERSISTENT entry here never expires "
    "-- its TTL counts down to 0 and the next access RESTORES it with a fresh "
    "TTL (4095 at these ledger defaults) -- and Task 1's probe table records "
    "instance storage behaving the same way. So the three legs give three "
    "answers: tier 1 reads absent and answers the default, the test host "
    "answers the STORED value, and the CHAIN archives the entry. The archival "
    "half is proven only at tier 3 and is carried to M2; neither the tier-1 "
    "model nor this row is edited to match the test host, because writing its "
    "answer down as a host fact is the inversion M3 forbids. At the F1-rewritten "
    "values two host facts compound: a fresh persistent or instance entry starts "
    "with 4095 ledgers of TTL, so a `(threshold=1000, extend_to=1000)` extension "
    "is a NO-OP on the host to begin with, and even past the TTL the entry would "
    "be restored rather than gone."
)

#: Why the two U32-edge rows have no real-host leg (finding F3, ruled
#: 2026-09-02). Measured: `RealEnv(sequence=2**32 - 1 - 10)` constructs, and
#: `register` then PANICS with `Error(Context, InternalError)` -- the contract
#: instance entry's own live-until would be `sequence + 4095`, past `u32::MAX`.
#: The second row is further out of reach still: its `Advance(11)` would take
#: the sequence itself past `u32::MAX`, and the host's ledger sequence is a
#: `u32`. These rows exist to pin the MODEL's rough edge (see
#: `test_env_differential.test_a_ledger_sequence_past_the_u32_range_is_the_
#: models_own_rough_edge`), so "no host can host this" is the row being right
#: about itself rather than a gap in it.
U32_LEDGER_UNRUNNABLE = (
    "the real host cannot hold a ledger sequence this close to u32::MAX: `register` "
    "panics with Error(Context, InternalError) because the contract instance's "
    "live-until would be sequence + 4095, past the u32 range (measured 2026-09-02), "
    "and `advance` past 2**32 - 1 has nowhere to go at all. The row pins the tier-1 "
    "model's own rough edge -- a state the model can reach and no host can (F3)"
)

#: RETIRED, and recorded rather than deleted: `THRESHOLD_BOUNDARY_REASON` was
#: the E9 declaration on `the_threshold_guard_extends_at_exact_equality` (then
#: named `..._blocks_at_...`) for exactly one run. The real leg measured the
#: host extending at exact equality where tier 1 blocked; the addendum to the
#: 2026-09-02 "M1-F Task 5 rulings" moved the MODEL to the host's boundary
#: instead of carrying the gap, so the declaration had nothing left to declare.
#: A declared divergence is meant to be retired the day the model is fixed --
#: this is what that looks like.

#: A keyed WRITE and the temporary entry's live-until: tier 1 CLEARS it (the
#: entry is never-extended, and a never-extended entry is immortal here), the
#: host leaves it exactly as granted. Measured 2026-09-02: an entry extended to
#: 1_000 still reads a TTL of 1_000 after a re-write, and a temporary entry that
#: lapses is deleted -- so the re-set value is gone one ledger past the original
#: grant, where tier 1 still answers it. Recorded as a host fact in the Task 5
#: rulings ("a fresh TEMPORARY entry has a 15-ledger TTL floor ... while tier 1's
#: never-extended entry lives forever") and declared here rather than rewritten
#: away, because the row's claim IS the model choice it names.
KEYED_WRITE_REASON = (
    "tier 1 models a keyed write as CLEARING the entry's live-until, which makes "
    "the re-set entry never-extended and therefore immortal (`serpent.env`'s first "
    "model choice). The host does no such thing: the entry keeps the live-until it "
    "was granted (measured -- the TTL still reads 1_000 across a re-write), and a "
    "lapsed temporary entry is deleted, so one ledger past the grant the real leg "
    "reads the default. The host fact is recorded in the 2026-09-02 Task 5 rulings; "
    "the model choice is an M2 oracle question (E9)"
)

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
        # chain rolls it back with the frame, and neither model does -- proven
        # on the real host (HOST_FACTS row
        # an_event_published_before_a_raise_is_rolled_back; see the
        # differential's own docstring for that row). Adopting the rollback at
        # tier 1/the mini host stays M2's.
        name="an_event_published_before_a_raise_survives_at_both_tiers",
        contract=ENV_SURFACE,
        invoke=Call("log_then_refuse", (_ADMIN, U32(3))),
        kind="contract_error",
        code=1,
        events=(LOGGED_EVENT,),
        host_diverges=HostDivergence(
            reason=(
                "S9: events roll back with a failed frame on chain. Both MODELS keep the "
                "event (the mini host mirrors tier 1 by construction, E1); the real host "
                "records it with `failed_call: true` and the sdk's Events::all() drops it "
                "(review m7). Pinned as a declared divergence (ruling E9) until the tier-1 "
                "model gains frame rollback (an M2 oracle edit)."
            ),
            events=(),  # the real host: nothing survives the refused frame
        ),
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
        mini_host_gap=AUTH_ARGS_REASON,
    ),
    EnvScenario(
        name="an_address_in_the_allow_set_is_recorded_and_allowed",
        contract=ENV_SURFACE,
        auth_allow_set=(_ADMIN,),
        invoke=Call("guard", (_ADMIN,)),
        kind="void",
        auths=((_ADMIN, None),),
        mini_host_gap=ALLOW_SET_REASON,
    ),
    EnvScenario(
        # The refusal is NOT recorded: the host traps, so there is no
        # invocation left to have recorded anything (`_record_auth`).
        name="an_address_outside_the_allow_set_is_refused_and_not_recorded",
        contract=ENV_SURFACE,
        auth_allow_set=(_OTHER,),
        invoke=Call("guard", (_ADMIN,)),
        kind="auth_failed",
        mini_host_gap=ALLOW_SET_REASON,
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
        # F1 rewrite: was (2000, 1000); claim preserved exactly -- the grant
        # still lands on sequence + 1000 and the Advance still stops on it.
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(1000), U32(1000))),
            Advance(1000),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(7),
        mini_host_gap=TTL_REASON,
    ),
    EnvScenario(
        name="a_temporary_entry_reads_absent_one_ledger_past_its_live_until",
        contract=ENV_SURFACE,
        # F1 rewrite: was (2000, 1000); claim preserved exactly.
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(1000), U32(1000))),
            Advance(1001),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(0),
        mini_host_gap=TTL_REASON,
    ),
    EnvScenario(
        name="an_expired_entry_reads_absent_through_has_too",
        contract=ENV_SURFACE,
        # F1 rewrite: was (2000, 1000); claim preserved exactly.
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(1000), U32(1000))),
            Advance(1001),
        ),
        invoke=Call("has_temp", (_K,)),
        kind="value",
        expect=Bool(False),
        mini_host_gap=TTL_REASON,
    ),
    EnvScenario(
        # A SMALLER SECOND GRANT CANNOT SHORTEN THE ENTRY: 1000 ledgers are
        # already granted, the second call asks for 10, and the entry is still
        # alive 1000 ledgers later.
        #
        # F1 rewrite: was (2000, 1000) then (2000, 10); the claim is NARROWED
        # and this is the narrowing. The old pair drove S8's NEVER-REDUCE rule
        # (`max(live_until, sequence + extend_to)`), and no host-legal call can
        # reach that rule: reducing needs `extend_to < remaining` to get past
        # the max AND `remaining < threshold` to get past the guard AND
        # `threshold <= extend_to` to be accepted at all, i.e.
        # `threshold <= extend_to < remaining < threshold`. So what the row now
        # pins is the observable half -- the second, smaller grant is a no-op
        # via the THRESHOLD GUARD (1000 remaining is not below a threshold of
        # 10) and the first grant stands. Never-reduce keeps its algebra pin at
        # tier 1 (`test_env_ttl.test_an_extension_never_reduces_as_an_algebra`).
        name="a_smaller_extension_after_a_larger_one_never_reduces_the_live_until",
        contract=ENV_SURFACE,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(1000), U32(1000))),
            Call("bump_temp", (_K, U32(10), U32(10))),
            Advance(1000),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(7),
        mini_host_gap=TTL_REASON,
    ),
    EnvScenario(
        # THE THRESHOLD BOUNDARY, at exact equality (Task 3's carried
        # obligation): the second call sees `live_until - sequence == 1000 ==
        # threshold`, and the guard EXTENDS there -- to sequence + 5000, so the
        # entry is still alive 1001 ledgers on. A guard written `>=` would make
        # it a no-op and this row would read the default.
        #
        # F1 rewrite: the first call was (2000, 1000); the second was already
        # host-legal, and it is the one the claim is about.
        #
        # F1 rewrite (round 2, the boundary itself): `expect` was U32(0) and
        # this row carried an E9 `host_diverges` for one run, because tier 1
        # blocked at equality and the host extended. Ruled by the addendum to
        # the 2026-09-02 "M1-F Task 5 rulings" -- the host is the truth, so the
        # tier-1 guard moved to the host's `<=` boundary and BOTH legs now
        # answer U32(7). The declaration is retired: there is nothing left to
        # declare, and `test_env_ttl.test_the_threshold_guard_extends_at_exact_
        # equality` is the moved tier-1 pin.
        #
        # It is the one row in this table whose named claim was measured FALSE
        # by the real host and corrected, rather than the model being wrong
        # about something it never promised -- which is what the real-host
        # tier is for.
        name="the_threshold_guard_extends_at_exact_equality",
        contract=ENV_SURFACE,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(1000), U32(1000))),
            Call("bump_temp", (_K, U32(1000), U32(5000))),
            Advance(1001),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(7),
        mini_host_gap=TTL_REASON,
    ),
    EnvScenario(
        # One ledger later the SAME two calls fall BELOW the threshold (999 <
        # 1000), so the extension applies and the entry outlives the first
        # grant's live-until.
        name="the_threshold_guard_lets_a_shorter_remaining_lifetime_through",
        contract=ENV_SURFACE,
        # F1 rewrite: the first call was (2000, 1000); claim preserved exactly
        # -- one ledger on, 999 remaining is below the threshold of 1000 on both
        # legs, so the extension applies and the entry outlives the first grant.
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(1000), U32(1000))),
            Advance(1),
            Call("bump_temp", (_K, U32(1000), U32(5000))),
            Advance(1001),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(7),
        mini_host_gap=TTL_REASON,
    ),
    EnvScenario(
        # A keyed WRITE clears the entry's live-until (Task 8's empirical
        # finding): one ledger past the grant, the re-set value is still there,
        # because the write made the entry never-extended again.
        #
        # F1 rewrite: was (2000, 10) with Advance(1000); the grant is now
        # (1000, 1000) and the Advance 1001, which is the same claim one ledger
        # past the same grant. The claim itself is TIER-1 ONLY and stays as the
        # model's, with the host's answer declared below: a write does not
        # clear a live-until on any host, and a temporary entry cannot outlive
        # its grant there.
        name="a_keyed_write_clears_the_live_until",
        contract=ENV_SURFACE,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(1000), U32(1000))),
            Call("put_temp", (_K, U32(8))),
            Advance(1001),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(8),
        mini_host_gap=TTL_REASON,
        host_diverges=HostDivergence(reason=KEYED_WRITE_REASON, events=(), answer=U32(0)),
    ),
    EnvScenario(
        # The INSTANCE bucket's live-until is bucket-wide, so a write does NOT
        # clear it -- the opposite answer to the row above, on purpose
        # (`InstanceStorage._forget_live_until` is a deliberate no-op).
        name="an_instance_write_does_not_clear_the_bucket_wide_live_until",
        contract=ENV_SURFACE,
        # F1 rewrite: was (2000, 100) with Advance(101); claim preserved --
        # the write still lands between the grant and the lapse, and the
        # Advance still stops one ledger past it.
        setup=(
            Call("put_instance", (_K, U32(7))),
            Call("bump_instance", (U32(1000), U32(1000))),
            Call("put_instance", (_K, U32(8))),
            Advance(1001),
        ),
        invoke=Call("read_instance_or", (_K, U32(0))),
        kind="value",
        expect=U32(0),
        mini_host_gap=TTL_REASON,
        # The lapse-and-read half of this row is the M3 divergence: the
        # instance entry is still there on the real host, holding the value the
        # second write left, so the real leg answers 8 where tier 1 answers the
        # default. What the row is ABOUT -- that an instance write does not
        # clear the bucket-wide live-until -- is a tier-1 claim either way.
        host_diverges=HostDivergence(reason=ARCHIVAL_REASON, events=(), answer=U32(8)),
    ),
    EnvScenario(
        # Task 3's other carried obligation: `del_` of ONE instance key does
        # not touch the instance's shared live-until, so the remaining key is
        # still alive exactly at it.
        name="the_instance_live_until_survives_a_del_of_one_key",
        contract=ENV_SURFACE,
        # F1 rewrite: was (2000, 100) with Advance(100); claim preserved --
        # the Advance still stops exactly ON the live-until.
        setup=(
            Call("put_instance", (_K, U32(1))),
            Call("put_instance", (_OTHER_KEY, U32(2))),
            Call("bump_instance", (U32(1000), U32(1000))),
            Call("drop_instance", (_K,)),
            Advance(1000),
        ),
        invoke=Call("read_instance_or", (_OTHER_KEY, U32(0))),
        kind="value",
        expect=U32(2),
        mini_host_gap=TTL_REASON,
    ),
    EnvScenario(
        name="the_instance_live_until_still_governs_the_keys_a_del_left_behind",
        contract=ENV_SURFACE,
        # F1 rewrite: was (2000, 100) with Advance(101); claim preserved --
        # one ledger past the live-until, as before.
        setup=(
            Call("put_instance", (_K, U32(1))),
            Call("put_instance", (_OTHER_KEY, U32(2))),
            Call("bump_instance", (U32(1000), U32(1000))),
            Call("drop_instance", (_K,)),
            Advance(1001),
        ),
        invoke=Call("read_instance_or", (_OTHER_KEY, U32(0))),
        kind="value",
        expect=U32(0),
        mini_host_gap=TTL_REASON,
        # M3 again, on the key the `del_` left behind: the real host still has
        # it, so the real leg answers 2. The row above it -- the same setup one
        # ledger earlier, where tier 1 also answers 2 -- needs no declaration,
        # which is what makes this pair worth keeping side by side.
        host_diverges=HostDivergence(reason=ARCHIVAL_REASON, events=(), answer=U32(2)),
    ),
    EnvScenario(
        name="a_persistent_struct_keyed_entry_expires_the_same_way",
        contract=ENV_SURFACE,
        # F1 rewrite: was (2000, 1000); claim preserved exactly.
        setup=(
            Call("put_slot", (_OWNER, U32(5))),
            Call("bump_slot", (_OWNER, U32(1000), U32(1000))),
            Advance(1001),
        ),
        invoke=Call("read_slot_or", (_OWNER, U32(0))),
        kind="value",
        expect=U32(0),
        mini_host_gap=TTL_REASON,
        # THE M3 row: a lapsed PERSISTENT entry. Tier 1 expires it and answers
        # the default; the real host restores it and answers the 5 the setup
        # wrote.
        host_diverges=HostDivergence(reason=ARCHIVAL_REASON, events=(), answer=U32(5)),
    ),
    EnvScenario(
        # S8's dead-entry rule, for the never-written death -- and it is a
        # TRAP, not a contract code (finding F2, ruled 2026-09-02). The row used
        # to pin `code=CODE_MISSING_VALUE`, which only tier 1 ever produced: the
        # emitter's E13 has-then-get guard wraps `get` alone, so the compiled
        # form lets the host's own `Storage(MissingValue)` kill the invocation.
        # Tier 1 now raises `serpent.env.StorageTrap`; the real leg matches the
        # underlying diagnostic pair below.
        name="extending_the_ttl_of_a_key_that_was_never_written_is_loud",
        contract=ENV_SURFACE,
        invoke=Call("bump_temp", (_K, U32(1000), U32(1000))),
        kind="host_error",
        host_error=("Storage", "MissingValue"),
        mini_host_gap=TTL_REASON,
    ),
    EnvScenario(
        # And for the expired death, which is the same answer from outside --
        # measured, not assumed: the host answers `Storage(MissingValue)` for a
        # LAPSED temporary key exactly as for a never-written one (2026-09-02).
        #
        # F1 rewrite: the setup's bump was (2000, 1000).
        name="extending_the_ttl_of_an_expired_entry_is_loud",
        contract=ENV_SURFACE,
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(1000), U32(1000))),
            Advance(1001),
        ),
        invoke=Call("bump_temp", (_K, U32(1000), U32(1000))),
        kind="host_error",
        host_error=("Storage", "MissingValue"),
        mini_host_gap=TTL_REASON,
    ),
    EnvScenario(
        # Task 3's third carried obligation: the rough edge of the U32 range.
        # The live-until lands on 2**32 - 1 EXACTLY, and the entry is alive
        # there -- the last sequence a `U32` ledger read can even name.
        name="an_entry_whose_live_until_is_the_last_u32_ledger_is_alive_there",
        contract=ENV_SURFACE,
        sequence=2**32 - 1 - 10,
        # F1 rewrite: was (2000, 10); claim preserved exactly -- the grant is
        # still ten ledgers, so the live-until still lands on 2**32 - 1, and a
        # fresh entry's live-until is unconditional at tier 1 whatever the
        # threshold.
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(10), U32(10))),
            Advance(10),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(7),
        mini_host_gap=TTL_REASON,
        real_unrunnable=U32_LEDGER_UNRUNNABLE,
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
        # F1 rewrite: was (2000, 10); claim preserved exactly.
        setup=(
            Call("put_temp", (_K, U32(7))),
            Call("bump_temp", (_K, U32(10), U32(10))),
            Advance(11),
        ),
        invoke=Call("read_temp_or", (_K, U32(0))),
        kind="value",
        expect=U32(0),
        mini_host_gap=TTL_REASON,
        real_unrunnable=U32_LEDGER_UNRUNNABLE,
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
        #
        # And that last clause is a MODEL claim, not a host fact: on the host
        # the failed frame is rolled back and takes its authorization with it
        # (finding F5, measured -- `auths()` reports nothing after this exact
        # call), so the real leg records only the setup mint's. Declared below,
        # in the row's own vocabulary: the addresses are what both legs claim,
        # and `None` args means "compare the address, not the args" exactly as
        # `auths` above means it.
        name="token_style_transfer_over_the_balance_refuses_with_the_contracts_own_code",
        contract=TOKEN_STYLE,
        constructor=(_ADMIN, String("Serpent Token")),
        setup=(Call("mint", (_ADMIN, _OWNER, U32(10))),),
        invoke=Call("transfer", (_OWNER, _SPENDER, U32(25))),
        kind="contract_error",
        code=1,
        auths=((_ADMIN, None), (_OWNER, None)),
        host_diverges=HostDivergence(
            reason=(
                "S9 rollback, the AUTH half (finding F5, ruled 2026-09-02): "
                "`require_auth` succeeds before the balance check, and then the frame "
                "fails and the host discards the whole invocation's record -- so the "
                "real leg records the setup mint's authorization and nothing from the "
                "refused transfer, where tier 1 keeps both (it has no frame rollback, "
                "the same gap the publish-then-raise row declares for events). Tier-1 "
                "frame rollback for events AND auths is an M2 oracle edit (E9)."
            ),
            events=(),
            auths=((_ADMIN, None),),
        ),
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
        # E13: a union's storage VALUE round trip reaches the `"vec"` tag
        # family, which no existing scenario reaches with a UDT. (The
        # `storage_key` Vec branch is a KEY-side concern -- row 3 below.)
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
        # one the write used -- exercising `storage_key`'s `Vec` branch (a
        # union IS a `Vec` on chain), which no existing scenario reaches with
        # a KEY.
        name="union_keyed_entry_is_found_by_a_rebuilt_value_equal_key",
        contract=ENV_SURFACE,
        setup=(Call("put_by_shape_key", (U32(7),)),),
        invoke=Call("read_by_shape_key", ()),
        kind="value",
        expect=U32(7),
    ),
)
