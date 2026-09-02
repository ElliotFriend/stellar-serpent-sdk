"""`serpent.testing.RealEnv`: the façade, the hierarchy, the drift pins, the skip policy."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from serpent import U32, Address, Symbol
from serpent._host._codegen import PINNED_TAG
from serpent.emitter import build_file
from serpent.testing import (
    DEFAULT_PROTOCOL,
    FrozenTableDisagreement,
    HostPanic,
    RealContractError,
    RealEnv,
    RealHostError,
    RealHostUnavailable,
)
from serpent.testing._marker import (
    REBUILD_COMMAND,
    REQUIRE_ENV_VAR,
    is_available,
    unavailable_reason,
)
from serpent.testing._real import DEFAULT_MAX_ENTRY_TTL
from serpent.testing._scval import from_xdr
from tests.unit.test_emitter_end_to_end import EXAMPLE_COUNTER, EXAMPLE_ERRORS
from tests.unit.test_examples import load_example

ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"

# --- run everywhere: the pins that do not need the extension ------------------


def test_the_default_protocol_is_the_env_json_pins_major() -> None:
    """E11: the embedded host and the emitter's bindings are the same release line."""
    assert DEFAULT_PROTOCOL == int(PINNED_TAG.removeprefix("v").split(".")[0]) == 28


def test_the_unavailable_reason_names_the_rebuild_command_and_the_readme() -> None:
    """U2: a skip nobody can act on is a silent pass. The reason has to carry
    the command that fixes it and the file that explains it -- and the command
    has to be the one `host/README.md` documents, not a second spelling of it.
    """
    reason = unavailable_reason()
    assert REBUILD_COMMAND in reason
    assert "maturin develop" in reason
    assert "host/README.md" in reason
    readme = (Path(__file__).resolve().parents[2] / "host" / "README.md").read_text(
        encoding="utf-8"
    )
    assert REBUILD_COMMAND in readme, "the skip reason and host/README.md have drifted apart"


def test_the_facade_agrees_with_the_markers_availability_answer() -> None:
    """`is_available()` is the one thing that decides the marker's outcome
    (`tests/conftest.py`), so it must agree with what `RealEnv()` does: build
    an env where the extension is importable, refuse where it is not."""
    if is_available():
        assert RealEnv().protocol_version() == DEFAULT_PROTOCOL
    else:
        with pytest.raises(RealHostUnavailable):
            RealEnv()


def _rust_less(tmp_path: Path) -> dict[str, str]:
    """An environment in which `serpent_host` is not importable.

    `sys.modules["serpent_host"] = None` is what a Rust-less checkout looks like
    from inside Python: `importlib.util.find_spec` answers `None` and `import
    serpent_host` raises `ModuleNotFoundError`. A `sitecustomize` shim on
    `PYTHONPATH` is how that reaches a SUBPROCESS, which is the only place the
    policy can be observed end to end -- the policy runs at session start, and
    this session already has the extension.
    """
    shim = tmp_path / "sitecustomize.py"
    shim.write_text("import sys; sys.modules['serpent_host'] = None\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    env.pop(REQUIRE_ENV_VAR, None)
    return env


def _pytest_run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).resolve().parents[2],
        check=False,  # a non-zero exit is the POINT of the required-mode runs
    )


def test_a_rust_less_checkout_skips_loudly_and_a_required_run_fails(tmp_path: Path) -> None:
    """U2 both ways. The selection is one real-host test that COLLECTS without
    the extension, which is the whole point of the lazy import in
    `_real._require_host`."""
    env = _rust_less(tmp_path)
    target = ["tests/real_host/test_real_env.py", "-k", "counter"]
    skipped = _pytest_run(env, *target, "-rs")
    assert skipped.returncode == 0, skipped.stdout
    assert "skipped" in skipped.stdout, skipped.stdout
    assert "maturin develop" in skipped.stdout, "the skip reason must carry the rebuild command"

    required = _pytest_run({**env, REQUIRE_ENV_VAR: "1"}, *target)
    assert required.returncode != 0, required.stdout
    assert REQUIRE_ENV_VAR in required.stdout, "the reason must name the switch that caused it"
    assert "maturin develop" in required.stdout, required.stdout
    # Neither a pass nor an xfail, which is the property review B4 asked for:
    # `xfail(run=False, strict=True)` was probed to exit 0, so it could not be
    # the mechanism.
    assert "xfail" not in required.stdout, required.stdout
    assert "passed" not in required.stdout, required.stdout


def test_a_module_level_importorskip_cannot_hide_from_the_required_mode(tmp_path: Path) -> None:
    """The vacuity hole that made the required-mode guard session-level.

    `tests/real_host/test_serpent_host_module.py` (Task 1's, not this task's)
    calls `pytest.importorskip("serpent_host")` at MODULE scope, so on a
    Rust-less checkout it contributes zero items -- the skip happens during
    collection, before any item exists for an item-level hook to mark. Measured
    against the first version of `tests/conftest.py`: `1 skipped`, exit code 5,
    a completely vacuous pass under `SERPENT_REQUIRE_REAL_HOST=1`. The guard
    now runs at `pytest_sessionstart`, so there is nothing left to hide behind.
    """
    env = _rust_less(tmp_path)
    target = "tests/real_host/test_serpent_host_module.py"

    quiet = _pytest_run(env, target, "-rs")
    assert "skipped" in quiet.stdout, "the unrequired mode still skips the module"
    # MEASURED: exit code 5, pytest's NO_TESTS_COLLECTED, not 0 -- selecting
    # ONLY this file on a Rust-less checkout leaves nothing to run, because the
    # module skipped during collection. That is pytest's own answer to an empty
    # selection and has nothing to do with this policy; a whole-suite run on the
    # same checkout collects thousands of other items and exits 0. What the
    # unrequired mode must not do is manufacture a failure, which is what the
    # last two assertions pin.
    assert quiet.returncode in (0, 5), quiet.stdout
    assert "failed" not in quiet.stdout, quiet.stdout
    assert "error" not in quiet.stdout, quiet.stdout

    required = _pytest_run({**env, REQUIRE_ENV_VAR: "1"}, target)
    assert required.returncode != 0, (
        "a module-level importorskip hid the whole file from the required mode"
    )
    assert REQUIRE_ENV_VAR in required.stdout, required.stdout
    assert "maturin develop" in required.stdout, required.stdout


def test_an_account_authorizer_is_refused_at_construction() -> None:
    """B2, the honest fence: account auth needs real signatures (M2).

    Checked BEFORE the extension is required, so the fence is the same answer on
    a Rust-less checkout as on a built one.
    """
    with pytest.raises(ValueError, match="contract"):
        RealEnv(auths=(Address(ACCOUNT),))


def test_realenv_without_the_extension_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "serpent_host", None)
    with pytest.raises(RealHostUnavailable) as info:
        RealEnv()
    assert "maturin" in str(info.value)


# --- the real-host half -----------------------------------------------------------

pytestmark_real = pytest.mark.real_host


@pytestmark_real
def test_counter_deploy_invoke_and_storage_read_back() -> None:
    env = RealEnv()
    c = env.deploy_source(EXAMPLE_COUNTER)  # B3: path-loaded classes have no sys.modules entry
    assert c.invoke("increment", U32(2)) == U32(2)
    assert c.invoke("increment", U32(3)) == U32(5)
    assert c.invoke("total") == U32(5)
    # counter.py keys `TOTAL` in PERSISTENT storage (examples/counter.py:56-66); the
    # other bucket is asserted absent so the durability ROUTING is tested, not just the key.
    assert c.storage("persistent").get(Symbol("TOTAL"), U32) == U32(5)
    assert c.storage("persistent").has(Symbol("TOTAL"))
    assert c.storage("instance").get(Symbol("TOTAL"), U32) is None
    assert c.storage("persistent").ttl(Symbol("TOTAL")) is not None
    assert c.storage("temporary").ttl(Symbol("TOTAL")) is None


@pytestmark_real
def test_the_persistent_live_until_is_the_sequence_plus_the_relative_ttl() -> None:
    """B10: the host's own quantity is RELATIVE; `live_until` is what tier 1's
    `_TtlState` speaks, and the Task-5 differential compares THAT."""
    env = RealEnv()
    c = env.deploy_source(EXAMPLE_COUNTER)
    c.invoke("increment", U32(1))
    slot = c.storage("persistent")
    ttl = slot.ttl(Symbol("TOTAL"))
    assert ttl is not None
    assert slot.live_until(Symbol("TOTAL")) == env.sequence + ttl
    assert slot.live_until(Symbol("ABSENT")) is None


@pytestmark_real
def test_a_contract_error_maps_to_the_declared_member() -> None:
    errors = load_example(EXAMPLE_ERRORS)
    env = RealEnv()
    vault = env.deploy_source(EXAMPLE_ERRORS, Address(ACCOUNT), U32(10))
    with pytest.raises(RealContractError) as info:
        vault.invoke("deposit", U32(11))
    assert info.value.code == 3
    assert info.value.error_type == "Contract"
    assert info.value.member is not None
    assert info.value.member.__name__ == errors.VaultError.LimitExceeded.__name__
    # A second by-path load is a distinct class object, so the CODE is what ties
    # the recovered member to the declaration, not identity.
    assert info.value.member.code == errors.VaultError.LimitExceeded.code


@pytestmark_real
def test_a_host_error_is_not_a_contract_error() -> None:
    c = RealEnv().deploy_source(EXAMPLE_COUNTER)
    with pytest.raises(RealHostError) as info:
        c.invoke("no_such_method")
    assert not isinstance(info.value, RealContractError)
    assert info.value.error_type == "Context"
    # B5: the frame says Context/6 for every guest failure; the diagnostics say
    # what actually happened.
    assert info.value.underlying == ("WasmVm", "MissingValue")


@pytestmark_real
def test_a_contained_panic_carries_its_own_underlying_error() -> None:
    """The `HostPanic` arm, and the measurement that lets it have an
    `underlying` at all.

    A rejected wasm module is escalated to a panic by the sdk (E4 contains it),
    so the frame-level pair is `("", 0)` and B5's classification would be lost
    if the diagnostics could not be trusted here. They can: the failing invoke
    above leaves `(WasmVm, MissingValue)` in the buffer, and the panic REPLACES
    it with its own event rather than appending to it.
    """
    env = RealEnv()
    c = env.deploy_source(EXAMPLE_COUNTER)
    with pytest.raises(RealHostError) as stale:
        c.invoke("no_such_method")
    assert stale.value.underlying == ("WasmVm", "MissingValue")
    with pytest.raises(HostPanic) as info:
        env.register_raw(b"\0asm\x01\0\0\0" + b"\xff" * 16, [])
    assert (info.value.error_type, info.value.code) == ("", 0)
    assert info.value.underlying == ("WasmVm", "InvalidAction")


@pytestmark_real
def test_compare_and_max_ttl_answer_from_the_host() -> None:
    """The two host facts the Task-5 differential reads straight off the env.

    `compare` is the host's `Compare<Val>` verdict for two SMALL vals, which is
    the case `obj_cmp` refuses (review M2); `max_ttl` is one below the
    `max_entry_ttl` the env was built with, because the sdk computes
    `max_live_until - sequence`.
    """
    env = RealEnv()
    assert env.compare(Symbol("A"), Symbol("A")) == 0
    assert env.compare(Symbol("A"), Symbol("_")) == -env.compare(Symbol("_"), Symbol("A")) != 0
    assert env.max_ttl() == DEFAULT_MAX_ENTRY_TTL - 1


@pytestmark_real
def test_advance_moves_the_sequence_the_contract_reads() -> None:
    from tests.semantics.env_scenarios import ENV_SURFACE

    env = RealEnv(sequence=1_000_000)
    c = env.deploy_source(ENV_SURFACE)
    assert c.invoke("ledger_seq") == U32(1_000_000)
    env.advance(5)
    assert c.invoke("ledger_seq") == U32(1_000_005)


SHAPES_ID = "CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW"  # a real CONTRACT strkey


@pytestmark_real
def test_the_allow_set_refuses_an_address_not_in_it() -> None:
    """B2: authorizers are CONTRACT strkeys on the test host; the refusal is a
    frame-level Context error whose UNDERLYING diagnostic is Auth (B5)."""
    from tests.semantics.env_scenarios import ENV_SURFACE

    allowed = Address(SHAPES_ID)
    other = Address("CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI")
    env = RealEnv(auths=(allowed,))
    c = env.deploy_source(ENV_SURFACE)
    c.invoke("guard", allowed)  # allowed: recorded
    assert c.auths()[0][0] == allowed
    with pytest.raises(RealHostError) as info:
        c.invoke("guard", other)  # refused
    assert not isinstance(info.value, RealContractError)
    assert info.value.underlying is not None and info.value.underlying[0] == "Auth"
    assert c.auths() == ()


# --- the surface later tasks import, pinned here rather than on first use ---------


def test_frozen_table_disagreement_is_an_assertion_with_its_message() -> None:
    """A producer contract for Tasks 4/5, so it is pinned where it is DEFINED.

    An `AssertionError` subclass on purpose: a frozen-table disagreement is a
    test failure, and pytest reports it the way it reports every other one. The
    message is the whole payload -- it has to name the row, both answers, and
    that a controller decision is required (E10) -- so what this asserts is that
    nothing in the subclassing eats it.
    """
    exc = FrozenTableDisagreement(
        "val_cmp row 12: tier 1 says -1, the real host says 1 -- controller decision required"
    )
    assert isinstance(exc, AssertionError)
    assert "controller decision required" in str(exc)
    with pytest.raises(AssertionError, match="controller decision required"):
        raise exc


@pytestmark_real
def test_resources_is_none_before_the_first_invoke_and_a_full_dict_after() -> None:
    """m14: the sdk PANICS for `resources()` before an invocation, so `None` is
    a mapped answer rather than a natural one and has to be pinned."""
    env = RealEnv()
    c = env.deploy_source(EXAMPLE_COUNTER)
    assert c.resources() is None
    c.invoke("increment", U32(1))
    resources = c.resources()
    assert resources is not None
    assert resources["write_entries"] >= 1  # the counter writes its slot
    assert set(resources) == {
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
    cpu, mem = c.budget()
    assert cpu > 0 and mem > 0


@pytestmark_real
def test_events_decode_to_the_tier_one_published_event_shape() -> None:
    """`PublishedEvent` is `(topics, data)` with both sides read loosely, which
    is the same shape tier 1's `published_events` records -- that is what makes
    the two comparable at all (Task 5).

    `log_declared` publishes `Logged(who, amount)`, declared with `who` as a
    topic and the amount as bare single-value data
    (`tests/fixtures/env_surface.py`).
    """
    from tests.semantics.env_scenarios import ENV_SURFACE

    who = Address(SHAPES_ID)
    c = RealEnv().deploy_source(ENV_SURFACE)
    assert c.events() == ()
    c.invoke("log_declared", who, U32(7))
    assert c.events() == (((Symbol("logged"), who), U32(7)),)


@pytestmark_real
def test_deploy_of_a_path_loaded_class_names_deploy_source() -> None:
    """B3, from the failing side: a class whose module is not in `sys.modules`
    cannot be traced back to its file, so `deploy` refuses instead of guessing
    and points at the form that works."""
    module = load_example(EXAMPLE_COUNTER)
    assert sys.modules.get(module.__name__) is None, "load_example must not register the module"
    with pytest.raises(ValueError, match="deploy_source"):
        RealEnv().deploy(module.Counter)


@pytestmark_real
def test_deploy_wasm_and_invoke_raw_hand_back_undecoded_xdr() -> None:
    """The Task-4 path: pre-built wasm, no class, and the result as BYTES.

    `invoke_raw` is for a test that owns the decode, so what it must not do is
    decode; the assertion is therefore on the XDR, with `from_xdr` applied by
    the test itself. `deploy_wasm`'s own `invoke` falls back to `decode_loose`,
    which for a `U32` answer is exact.
    """
    env = RealEnv()
    c = env.deploy_wasm(build_file(EXAMPLE_COUNTER).wasm)
    assert c.cls is None
    assert c.invoke("increment", U32(4)) == U32(4)
    raw = env.invoke_raw(c.address.strkey, "increment", [U32(3)])
    assert isinstance(raw, bytes)
    assert from_xdr(raw, U32) == U32(7)


@pytestmark_real
def test_set_ledger_moves_the_sequence_the_contract_reads() -> None:
    """`advance`'s absolute counterpart, observed through the contract rather
    than through the façade's own bookkeeping."""
    from tests.semantics.env_scenarios import ENV_SURFACE

    env = RealEnv()
    c = env.deploy_source(ENV_SURFACE)
    env.set_ledger(sequence=2_000_000)
    assert env.sequence == 2_000_000
    assert c.invoke("ledger_seq") == U32(2_000_000)


@pytestmark_real
def test_advance_refuses_a_non_positive_or_non_int_step() -> None:
    """The precondition is tier 1's, with tier 1's wording (`Env.advance`): one
    differential row must not get two different answers for a bad `n`."""
    env = RealEnv()
    with pytest.raises(ValueError, match="positive number of ledgers"):
        env.advance(0)
    with pytest.raises(ValueError, match="positive number of ledgers"):
        env.advance(-1)
    with pytest.raises(TypeError, match="takes an int"):
        env.advance(True)
    assert env.sequence == 1_000_000, "a refused advance must not have moved anything"


@pytestmark_real
def test_storage_set_is_visible_to_get_and_to_the_contract() -> None:
    """Tier-3 seeding (Task 9): a write from outside puts the ledger in a state
    no sequence of invocations reaches, and the contract resumes from it."""
    env = RealEnv()
    c = env.deploy_source(EXAMPLE_COUNTER)
    slot = c.storage("persistent")
    assert slot.has(Symbol("TOTAL")) is False
    slot.set(Symbol("TOTAL"), U32(40))
    assert slot.has(Symbol("TOTAL")) is True
    assert slot.get(Symbol("TOTAL"), U32) == U32(40)
    assert c.invoke("increment", U32(2)) == U32(42)


@pytestmark_real
def test_an_invalid_method_name_is_a_value_error_not_a_host_error() -> None:
    """The `invalid_input` arm: the Rust layer rejected the call before the host
    saw it, so this is the CALLER's bug and must not be catchable as an answer
    the host gave about a contract (P4)."""
    c = RealEnv().deploy_source(EXAMPLE_COUNTER)
    with pytest.raises(ValueError, match="invalid_input") as info:
        c.invoke("has-dash")
    assert not isinstance(info.value, RealHostError)


@pytestmark_real
def test_a_module_with_two_contract_classes_is_refused(tmp_path: Path) -> None:
    """The discovery rule's failing side: a serpent module declares exactly one
    contract, so two is a ValueError before anything is compiled."""
    two = tmp_path / "two_contracts.py"
    two.write_text(
        "from serpent import U32, Env, contract\n"
        "\n"
        "@contract\n"
        "class First:\n"
        "    def f(self, env: Env) -> U32:\n"
        "        return U32(1)\n"
        "\n"
        "@contract\n"
        "class Second:\n"
        "    def g(self, env: Env) -> U32:\n"
        "        return U32(2)\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="2 @contract classes"):
        RealEnv().deploy_source(two)
