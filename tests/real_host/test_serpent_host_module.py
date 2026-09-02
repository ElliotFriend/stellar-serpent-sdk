"""The raw `serpent_host` extension: the P4 discrimination and the P3 containment.

Everything here talks to the extension WITHOUT `serpent.testing` (Task 3 wraps
it); the point is that the Rust layer's contract -- bytes in, bytes out, one
`HostFailure` shape, no `PanicException` -- holds on its own. Skipped loudly
when the extension is not built (`tests/conftest.py`, ruling U2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from stellar_sdk import scval
from stellar_sdk.xdr import ContractEvent, DiagnosticEvent, SCVal, SCValType

from serpent.emitter import build_file
from serpent.env import DEFAULT_LEDGER_SEQUENCE, DEFAULT_LEDGER_TIMESTAMP

serpent_host = pytest.importorskip("serpent_host")

# Every test here drives the extension; no table-only tests live in this module.
pytestmark = pytest.mark.real_host

_ROOT = Path(__file__).resolve().parents[2]
COUNTER = _ROOT / "examples" / "counter.py"
ERRORS = _ROOT / "examples" / "errors.py"
EVENTS = _ROOT / "examples" / "events.py"
# `guard(who: Address)` calls `who.require_auth()`, which is what `auths()` and
# `mock_auths()` need; no example contract calls it.
ENV_SURFACE = _ROOT / "tests" / "fixtures" / "env_surface.py"
ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"
# Two valid contract strkeys that are never the deployed contract's own address:
# `StrKey.encode_contract(bytes([n]) * 32)` for n = 0xC3 and n = 1.
OTHER_CONTRACT = "CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW"
UNRELATED_CONTRACT = "CAAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQC526"


def _topics(event: ContractEvent) -> list[SCVal]:
    """An event's topic list. `body.v0` is Optional in the generated XDR."""
    v0 = event.body.v0
    assert v0 is not None
    return list(v0.topics)


def _innermost_error(env: Any) -> tuple[str, str] | None:
    """The LAST diagnostic whose topics are `[Symbol("error"), Error(...)]`, as
    `(ScErrorType, ScErrorCode)` names -- the shape `serpent.testing` (Task 3)
    reads to recover what the frame-level error hides (B5)."""
    found = None
    for raw in env.diagnostics():
        topics = _topics(DiagnosticEvent.from_xdr_bytes(raw).event)
        if not topics or scval.from_symbol(topics[0]) != "error":
            continue
        for topic in topics[1:]:
            if topic.type == SCValType.SCV_ERROR and topic.error is not None:
                error = topic.error
                if error.code is not None:
                    found = (error.type.name, error.code.name)
    return found


def _env() -> Any:
    return serpent_host.RealEnv(
        protocol_version=28,
        sequence_number=DEFAULT_LEDGER_SEQUENCE,
        timestamp=DEFAULT_LEDGER_TIMESTAMP,
        network_id=bytes(32),
        base_reserve=5_000_000,
        min_temp_entry_ttl=16,
        min_persistent_entry_ttl=4096,
        max_entry_ttl=6_312_000,
    )


def _u32(xdr_bytes: bytes) -> int:
    return scval.from_uint32(SCVal.from_xdr_bytes(xdr_bytes))


def test_the_counter_example_runs_on_the_real_host() -> None:
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    assert cid.startswith("C") and len(cid) == 56
    step = [scval.to_uint32(1).to_xdr_bytes()]
    first = _u32(env.invoke(cid, "increment", step))
    second = _u32(env.invoke(cid, "increment", step))
    assert (first, second) == (1, 2)
    assert _u32(env.invoke(cid, "total", [])) == 2


def test_protocol_is_28_and_equals_the_compiled_in_ceiling() -> None:
    env = _env()
    assert env.protocol_version() == 28
    # `==`, not `>=`: a p29 host would skew every tier-3 comparison (K2).
    assert env.host_protocol_ceiling() == 28


def test_the_extension_lives_in_the_repo_venv() -> None:
    """F.1.7: a stale system-wide install must not shadow the repo's build."""
    assert ".venv" in serpent_host.__file__, serpent_host.__file__


def test_a_missing_function_carries_an_underlying_diagnostic() -> None:
    """B5: the frame-level error is Context/InvalidAction for EVERY guest failure;
    the real classification is in the diagnostics."""
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    with pytest.raises(serpent_host.HostFailure) as info:
        env.invoke(cid, "no_such_export", [])
    raw = env.diagnostics()
    assert raw, "the host emitted no diagnostic events (diagnostic level not Debug?)"
    # Every item decodes as a DiagnosticEvent, and the error ones are topic'd
    # `[Symbol("error"), Error(Type(Code))]` exactly as review B5 describes.
    events = [DiagnosticEvent.from_xdr_bytes(b) for b in raw]
    topic_names = [scval.from_symbol(_topics(e.event)[0]) for e in events]
    assert topic_names[0] == "fn_call"
    assert "error" in topic_names
    # Observed: the frame says Context/InvalidAction (code 6) while the
    # innermost diagnostic says what actually happened -- a missing export is
    # SCE_WASM_VM / SCEC_MISSING_VALUE.
    assert info.value.args[:3] == ("host", "Context", 6)
    assert _innermost_error(env) == ("SCE_WASM_VM", "SCEC_MISSING_VALUE")


def test_compare_orders_two_small_symbols_where_obj_cmp_refuses() -> None:
    """M2: the Compare trait answers for two small Vals; obj_cmp would trap."""
    env = _env()
    a = scval.to_symbol("A").to_xdr_bytes()
    u = scval.to_symbol("_").to_xdr_bytes()
    assert env.compare(a, a) == 0
    assert env.compare(a, u) in (-1, 1)
    assert env.compare(a, u) == -env.compare(u, a)


def test_a_contract_error_is_kind_contract_with_its_code() -> None:
    env = _env()
    # `examples/errors.py`'s Vault: `__init__(owner, limit)`, then `deposit(amount)`
    # raises `VaultError.LimitExceeded` (errorcode 3) past the limit.
    cid = env.register(
        build_file(ERRORS).wasm,
        [scval.to_address(ACCOUNT).to_xdr_bytes(), scval.to_uint32(10).to_xdr_bytes()],
    )
    with pytest.raises(serpent_host.HostFailure) as info:
        env.invoke(cid, "deposit", [scval.to_uint32(11).to_xdr_bytes()])
    kind, error_type, code, message = info.value.args
    assert (kind, error_type, code) == ("contract", "Contract", 3)
    assert message == "contract error code 3"


def test_a_missing_function_is_a_host_error_never_a_contract_error() -> None:
    """P4: `Context(InvalidAction)` has code 6; it must not read as contract code 6."""
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    with pytest.raises(serpent_host.HostFailure) as info:
        env.invoke(cid, "no_such_export", [])
    kind, error_type, _code, message = info.value.args
    assert kind == "host"
    assert error_type == "Context"
    assert "contract error" not in message


@pytest.mark.parametrize("name", ["has-dash", "two words", "a" * 33])
def test_an_invalid_function_name_is_invalid_input_and_the_env_survives(name: str) -> None:
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    with pytest.raises(serpent_host.HostFailure) as info:
        env.invoke(cid, name, [])
    assert info.value.args[0] == "invalid_input"
    assert _u32(env.invoke(cid, "increment", [scval.to_uint32(1).to_xdr_bytes()])) == 1


def test_a_bad_strkey_is_invalid_input_not_a_panic() -> None:
    env = _env()
    with pytest.raises(serpent_host.HostFailure) as info:
        env.invoke("NOTANADDRESS", "total", [])
    assert info.value.args[0] == "invalid_input"


def test_register_of_garbage_is_a_host_failure_not_a_panic() -> None:
    """P3's `Env::register` row: the sdk panics on a rejected module; E4's
    `catch_unwind` makes that a catchable `Exception` subclass."""
    env = _env()
    # Deliberately broad: the CLASS of the raised object is the assertion below.
    with pytest.raises(Exception) as info:
        env.register(b"\0asm\x01\0\0\0" + b"\xff" * 16, [])
    assert isinstance(info.value, serpent_host.HostFailure)
    # Measured at soroban-sdk 28.0.0-rc.1: the sdk escalates the module's
    # rejection to a panic, so this is kind "panic" and the classification
    # (WasmVm, InvalidAction) survives only in the message text.
    assert info.value.args[0] == "panic"
    assert "Error(WasmVm, InvalidAction)" in info.value.args[3]
    # Not BaseException-only: `except Exception` must catch it. (The brief's
    # `__mro__[-2] is Exception` indexes BaseException -- the MRO of a plain
    # Exception subclass is (cls, Exception, BaseException, object).)
    assert Exception in type(info.value).__mro__
    assert type(info.value).__mro__ == (
        serpent_host.HostFailure,
        Exception,
        BaseException,
        object,
    )


def test_not_even_a_wasm_header_is_invalid_input() -> None:
    env = _env()
    with pytest.raises(serpent_host.HostFailure) as info:
        env.register(b"hello", [])
    assert info.value.args[0] == "invalid_input"


def test_no_snapshot_files_are_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P11: `capture_snapshot_at_drop: false`, proven by the absence of `test_snapshots/`."""
    monkeypatch.chdir(tmp_path)
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    env.invoke(cid, "increment", [scval.to_uint32(1).to_xdr_bytes()])
    del env
    assert not (tmp_path / "test_snapshots").exists()


def test_budget_and_resources_report_the_last_invocation() -> None:
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    env.invoke(cid, "increment", [scval.to_uint32(1).to_xdr_bytes()])
    cpu, mem = env.budget()
    assert cpu > 0 and mem > 0
    r = env.resources()
    assert r is not None
    assert r["write_entries"] >= 1  # the counter writes its slot
    assert set(r) == {
        "instructions",
        "mem_bytes",
        "disk_read_entries",
        "memory_read_entries",
        "write_entries",
        "disk_read_bytes",
        "write_bytes",
        "contract_events_size_bytes",
        "persistent_rent_ledger_bytes",
        "persistent_entry_rent_bumps",
        "temporary_rent_ledger_bytes",
        "temporary_entry_rent_bumps",
    }


def test_resources_is_none_before_the_first_invocation() -> None:
    assert _env().resources() is None


# --- storage: the four accessors written from `storage_get`'s shape ----------
# The brief hands over `storage_get` and leaves `storage_has`/`storage_set`/
# `storage_ttl` to the implementer, so the branches they add (the durability
# word, the RELATIVE and panic-on-absent `get_ttl` of review B10, the keyless
# instance form) are pinned here rather than left to Task 3.


def test_storage_round_trips_the_counters_own_slot() -> None:
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    key = scval.to_symbol("TOTAL").to_xdr_bytes()
    assert env.storage_has(cid, "persistent", key) is False
    assert env.storage_get(cid, "persistent", key) is None
    env.invoke(cid, "increment", [scval.to_uint32(7).to_xdr_bytes()])
    assert env.storage_has(cid, "persistent", key) is True
    assert _u32(env.storage_get(cid, "persistent", key)) == 7
    # A write from outside is visible to the contract: the counter resumes from it.
    env.storage_set(cid, "persistent", key, scval.to_uint32(40).to_xdr_bytes())
    assert _u32(env.invoke(cid, "increment", [scval.to_uint32(2).to_xdr_bytes()])) == 42


def test_storage_ttl_is_relative_and_none_when_absent() -> None:
    """B10: ledgers remaining EXCLUDING the current one, and no panic for a
    key that was never written."""
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    key = scval.to_symbol("TOTAL").to_xdr_bytes()
    assert env.storage_ttl(cid, "persistent", key) is None
    env.invoke(cid, "increment", [scval.to_uint32(1).to_xdr_bytes()])
    # min_persistent_entry_ttl above is 4096 and the count EXCLUDES the current
    # ledger, so a freshly written entry reads 4095: live_until = sequence + ttl.
    assert env.storage_ttl(cid, "persistent", key) == 4095
    assert env.storage_ttl(cid, "temporary", key) is None  # a different map


def test_storage_ttl_of_the_instance_entry_takes_no_key() -> None:
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    assert env.storage_ttl(cid, "instance", b"") is not None
    with pytest.raises(serpent_host.HostFailure) as info:
        env.storage_ttl(cid, "instance", scval.to_symbol("TOTAL").to_xdr_bytes())
    assert info.value.args[0] == "invalid_input"


@pytest.mark.parametrize("method", ["storage_get", "storage_has", "storage_ttl"])
def test_an_unknown_durability_is_invalid_input(method: str) -> None:
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    with pytest.raises(serpent_host.HostFailure) as info:
        getattr(env, method)(cid, "Persistent", scval.to_symbol("TOTAL").to_xdr_bytes())
    assert info.value.args[0] == "invalid_input"
    assert "persistent/temporary/instance" in info.value.args[3]


def test_auths_skips_the_deploy_authorization() -> None:
    """M8: `register` records a CreateContractV2HostFn entry; `auths()` drops
    non-contract functions instead of failing on them."""
    env = _env()
    env.mock_all_auths()
    env.register(build_file(COUNTER).wasm, [])
    assert env.auths() == []


def test_max_ttl_is_one_below_max_entry_ttl() -> None:
    """The sdk computes max_live_until - sequence, so the ceiling is 6_311_999."""
    assert _env().max_ttl() == 6_311_999


def test_set_ledger_moves_the_sequence_and_leaves_the_rest_alone() -> None:
    """There is no sequence getter on this surface, so the moved sequence is
    read through the entry it ages: a relative TTL counts down by exactly the
    number of ledgers passed. Passing only `timestamp` moves nothing else."""
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    key = scval.to_symbol("TOTAL").to_xdr_bytes()
    env.invoke(cid, "increment", [scval.to_uint32(1).to_xdr_bytes()])
    assert env.storage_ttl(cid, "persistent", key) == 4095
    env.set_ledger(sequence_number=DEFAULT_LEDGER_SEQUENCE + 10)
    assert env.storage_ttl(cid, "persistent", key) == 4085
    env.set_ledger(timestamp=DEFAULT_LEDGER_TIMESTAMP + 5)
    assert env.storage_ttl(cid, "persistent", key) == 4085
    assert env.protocol_version() == 28


def test_events_are_the_last_invocations_contract_events() -> None:
    """`examples/events.py` publishes one event with the `round_closed` topic."""
    env = _env()
    cid = env.register(build_file(EVENTS).wasm, [])
    assert env.events() == []
    env.invoke(
        cid,
        "record_round_closed",
        [scval.to_uint32(2).to_xdr_bytes(), scval.to_uint32(1).to_xdr_bytes()],
    )
    (event,) = [ContractEvent.from_xdr_bytes(b) for b in env.events()]
    assert [scval.from_symbol(t) for t in _topics(event)] == ["round_closed"]


def test_mock_auths_refuses_an_account_authorizer() -> None:
    """B2: the sdk registers a MockAuthContract AT the authorizer address and
    PANICS for a `G...` account, so the strkey is checked before it gets there.
    Account auth needs real signatures and is out of scope for M1."""
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    with pytest.raises(serpent_host.HostFailure) as info:
        env.mock_auths([(ACCOUNT, cid, "increment", [])])
    assert info.value.args[0] == "invalid_input"
    # A contract authorizer is accepted (and REPLACES the whole set, M6).
    env.mock_auths([(OTHER_CONTRACT, cid, "increment", [])])


def test_an_expired_temporary_entry_reads_as_absent() -> None:
    """B10, the other half: `min_temp_entry_ttl` is 16, so a fresh temporary
    entry has 15 ledgers left; past that the entry is gone, not an error."""
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    key = scval.to_symbol("T").to_xdr_bytes()
    env.storage_set(cid, "temporary", key, scval.to_uint32(1).to_xdr_bytes())
    assert env.storage_ttl(cid, "temporary", key) == 15
    env.set_ledger(sequence_number=DEFAULT_LEDGER_SEQUENCE + 100)
    assert env.storage_has(cid, "temporary", key) is False
    assert env.storage_ttl(cid, "temporary", key) is None


def test_a_contained_panic_leaves_the_env_usable() -> None:
    """The sdk panics deep inside `as_contract` when the contract has no
    instance entry at all (`Error(Storage, MissingValue)`). `contained` turns
    that into a `HostFailure`, and -- the part worth pinning -- the env is
    still healthy afterwards, so one bad call cannot poison a whole test."""
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    with pytest.raises(serpent_host.HostFailure) as info:
        env.storage_get(OTHER_CONTRACT, "persistent", scval.to_symbol("X").to_xdr_bytes())
    assert info.value.args[0] == "panic"
    assert _u32(env.invoke(cid, "increment", [scval.to_uint32(3).to_xdr_bytes()])) == 3


# --- auth: the positive paths (`auths()` and `mock_auths()` accepting) -------


def test_auths_records_the_require_auth_call() -> None:
    """The `auths()` positive path: `tests/fixtures/env_surface.py`'s
    `guard(who)` is one `who.require_auth()`, and with `mock_all_auths()` the
    host records it as the invocation's one root contract auth."""
    env = _env()
    env.mock_all_auths()
    cid = env.register(build_file(ENV_SURFACE).wasm, [])
    who = scval.to_address(OTHER_CONTRACT).to_xdr_bytes()
    env.invoke(cid, "guard", [who])
    (row,) = env.auths()
    address, contract, function, args = row
    assert address == OTHER_CONTRACT
    assert contract == cid
    assert function == "guard"
    # The host records the `require_auth` ARGS, which for the bare form is the
    # invocation's own argument list.
    assert [SCVal.from_xdr_bytes(a) for a in args] == [SCVal.from_xdr_bytes(who)]


def test_mock_auths_authorizes_the_named_address_and_no_other() -> None:
    """The `mock_auths()` accept path, and its refusal.

    B5 again, from the auth side: an unauthorized `require_auth` reports the
    same frame-level `Context(InvalidAction)` code 6 as every other guest-side
    failure, and only the diagnostics say it was an AUTH failure.
    """
    env = _env()
    cid = env.register(build_file(ENV_SURFACE).wasm, [])
    allowed = scval.to_address(OTHER_CONTRACT).to_xdr_bytes()
    env.mock_auths([(OTHER_CONTRACT, cid, "guard", [allowed])])

    env.invoke(cid, "guard", [allowed])  # authorized: no HostFailure
    assert [(a, c, f) for a, c, f, _args in env.auths()] == [(OTHER_CONTRACT, cid, "guard")]

    with pytest.raises(serpent_host.HostFailure) as info:
        env.invoke(cid, "guard", [scval.to_address(UNRELATED_CONTRACT).to_xdr_bytes()])
    assert info.value.args[:3] == ("host", "Context", 6)
    assert _innermost_error(env) == ("SCE_AUTH", "SCEC_INVALID_ACTION")


def test_a_second_invocation_replaces_events_and_auths() -> None:
    """Both are the LAST invocation's, not an accumulation (Task 3 and Task 5
    build the per-sequence views on top of that)."""
    env = _env()
    env.mock_all_auths()
    cid = env.register(build_file(ENV_SURFACE).wasm, [])
    first = scval.to_address(OTHER_CONTRACT).to_xdr_bytes()
    second = scval.to_address(UNRELATED_CONTRACT).to_xdr_bytes()

    env.invoke(cid, "guard", [first])
    assert [a for a, _c, _f, _args in env.auths()] == [OTHER_CONTRACT]
    env.invoke(cid, "guard", [second])
    assert [a for a, _c, _f, _args in env.auths()] == [UNRELATED_CONTRACT]

    amount = scval.to_uint32(1).to_xdr_bytes()
    env.invoke(cid, "log_declared", [first, amount])
    assert len(env.events()) == 1
    env.invoke(cid, "log_declared", [second, amount])
    assert len(env.events()) == 1, "events() accumulated instead of replacing"
    # ... and the last one is the one that survived.
    (event,) = [ContractEvent.from_xdr_bytes(b) for b in env.events()]
    assert _topics(event)[1] == SCVal.from_xdr_bytes(second)


def test_storage_ttl_of_an_undeployed_contract_panics_like_storage_get() -> None:
    """The two accessors must agree. A missing entry is `None`; a missing
    CONTRACT is a fault, and `Error(Storage, MissingValue)` out of
    `as_contract`'s frame push is the same fault in both -- so `storage_ttl`
    maps only the expired-arithmetic panic to `None`, never this one."""
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    key = scval.to_symbol("TOTAL").to_xdr_bytes()
    for method, durability, probe_key in (
        ("storage_get", "persistent", key),
        ("storage_ttl", "persistent", key),
        ("storage_ttl", "instance", b""),
    ):
        with pytest.raises(serpent_host.HostFailure) as info:
            getattr(env, method)(UNRELATED_CONTRACT, durability, probe_key)
        assert info.value.args[0] == "panic"
        assert "Error(Storage, MissingValue)" in info.value.args[3]
    # The deployed contract still answers, and the env is unharmed.
    assert env.storage_ttl(cid, "instance", b"") == 4095
    assert _u32(env.invoke(cid, "increment", [scval.to_uint32(1).to_xdr_bytes()])) == 1


def test_a_persistent_entry_is_restored_rather_than_expiring() -> None:
    """Why only a TEMPORARY entry ever reads as absent: measured on this host, a
    persistent entry counts down to 0 at its live-until ledger and is then
    RESTORED on the next access with a fresh 4095, so `storage_ttl` never has an
    expired persistent entry to report. Recorded because a test that expects a
    persistent entry to disappear would be testing a fiction."""
    key = scval.to_symbol("TOTAL").to_xdr_bytes()

    def ttl_after(delta: int) -> int | None:
        env = _env()
        cid = env.register(build_file(COUNTER).wasm, [])
        env.invoke(cid, "increment", [scval.to_uint32(1).to_xdr_bytes()])
        env.set_ledger(sequence_number=DEFAULT_LEDGER_SEQUENCE + delta)
        assert env.storage_has(cid, "persistent", key) is True
        ttl: int | None = env.storage_ttl(cid, "persistent", key)
        return ttl

    assert ttl_after(4094) == 1
    assert ttl_after(4095) == 0  # the last live ledger
    assert ttl_after(4096) == 4095  # restored, not gone
