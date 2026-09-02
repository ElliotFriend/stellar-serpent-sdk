"""`serpent.testing.RealEnv`: the façade, the hierarchy, the drift pins, the skip policy."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from serpent import U32, Address, Symbol
from serpent._host._codegen import PINNED_TAG
from serpent.testing import (
    DEFAULT_PROTOCOL,
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


def test_a_rust_less_checkout_skips_loudly_and_a_required_run_fails(tmp_path: Path) -> None:
    """U2 both ways, in a subprocess that HIDES serpent_host via a sitecustomize shim.

    `sys.modules["serpent_host"] = None` is what a Rust-less checkout looks like
    from inside Python: `importlib.util.find_spec` answers `None` and `import
    serpent_host` raises `ModuleNotFoundError`. The selection is one real-host
    test that COLLECTS without the extension, which is the whole point of the
    lazy import in `_real._require_host`.
    """
    shim = tmp_path / "sitecustomize.py"
    shim.write_text("import sys; sys.modules['serpent_host'] = None\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    env.pop(REQUIRE_ENV_VAR, None)
    probe = ["-q", "-p", "no:cacheprovider", "tests/real_host/test_real_env.py", "-k", "counter"]
    root = Path(__file__).resolve().parents[2]
    skipped = subprocess.run(
        [sys.executable, "-m", "pytest", *probe, "-rs"],
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        check=False,
    )
    assert skipped.returncode == 0, skipped.stdout
    assert "skipped" in skipped.stdout, skipped.stdout
    assert "maturin develop" in skipped.stdout, "the skip reason must carry the rebuild command"
    required = subprocess.run(
        [sys.executable, "-m", "pytest", *probe, "-rEf"],
        capture_output=True,
        text=True,
        env={**env, REQUIRE_ENV_VAR: "1"},
        cwd=root,
        check=False,  # a non-zero exit is the POINT of the second run
    )
    # MEASURED on pytest 8 (the brief predicted a FAILED line): `pytest.fail`
    # raised from `pytest_runtest_setup` lands in the SETUP phase, which pytest
    # reports as an ERROR, not a FAILED. That is louder, not weaker, and the
    # property review B4 actually asked for is the one asserted here -- a
    # non-zero exit and an outcome that is neither a pass nor an xfail, which is
    # exactly what `xfail(run=False, strict=True)` could not give.
    assert required.returncode != 0, required.stdout
    assert " error" in required.stdout, required.stdout
    assert "xfail" not in required.stdout, required.stdout
    assert REQUIRE_ENV_VAR in required.stdout, "the reason must name the switch that caused it"


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
