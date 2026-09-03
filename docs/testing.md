# Testing serpent contracts

serpent tests a contract at four tiers, and each one answers a different
question. None of them, except the last, is the chain -- and the last is only
a recording of it. Read this before trusting a green run to mean more than it
does.

## The four tiers

**Tier 1 -- the model.** `serpent.env` (`Env`, `deploy`) is a hand-written,
in-memory model of the host, run as ordinary Python. It is the fast authoring
loop: no engine, no wasm, no network. Its failure mode is *silent false
green* -- the test passes, the docstrings read well, and the contract behaves
differently on chain -- so `serpent.env`'s own module docstring names exactly
what it does not model (no footprint, no budget metering, no frame rollback,
no TTL clamp/trap, no archival). A green tier-1 test is evidence about the
model, never about the chain.

**Tier 2a -- the mock.** `tests/harness` compiles a contract to real wasm and
runs it under a wasmtime pinned to the chain's feature set, with a mini host
(`tests/harness/hostfns.py`) that mirrors tier 1's semantics BY CONSTRUCTION.
A green run means "the codegen is self-consistent with tier 1", not "this
contract is correct on chain" -- the mini host is not an oracle, and it has
named gaps of its own: no TTL model at all (`extend_contract_data_ttl` is a
recorded no-op), `require_auth_for_args`'s args are discarded, and there is no
authorization allow-set to refuse against. `tests/semantics/env_scenarios.py`
marks every row that reaches one of those gaps `mini_host_gap`, so a row can
opt out of this leg only for a reason the table states.

**The real host, "2b".** `serpent.testing` (`RealEnv`, `RealContract`)
embeds the actual `soroban-env-host` behind a `serpent_host` PyO3 extension,
and `tests/real_host/` runs the frozen tables and scenarios against it. This
is the first tier that is actual evidence about the chain, not another model
of it -- and it is also where a model's assumption gets to be wrong: a
`host_diverges` declaration says tier 1 and the real host are known to
disagree and why; a bare divergence with no declaration is a bug, not a
finding.

**Tier 3 -- recorded simulation.** `serpent.testing.testnet` replays
`simulateTransaction` calls against a contract genuinely deployed to Stellar
testnet, recorded once as fixtures (`tests/real_host/fixtures/testnet/shapes/`,
including the deployed `deployed.wasm`) and replayed offline forever after.
It never signs or submits anything, and it is the only tier that says
anything about the NETWORK rather than an embedded host -- including the one
thing the real host cannot: persistent-entry archival, which the sdk's own test
host does not model either (a lapsed persistent entry there is silently
restored with a fresh TTL on access; only the chain archives it and refuses
the access until a restore footprint pays for it).

None of tiers 1-2b sees archival. That gap is `chain_unproven` in
`tests/semantics/host_facts.py`, carried to M2/M3.

## Writing a contract test with `RealEnv`

A tier-1 test re-points at the real host by swapping one fixture. Against
`examples/counter.py`:

```python
import pytest
from serpent import U32
from serpent.testing import RealEnv

pytestmark = pytest.mark.real_host


def test_counter_increments_on_the_real_host() -> None:
    env = RealEnv()
    counter = env.deploy_source(EXAMPLE_COUNTER)  # a Path to counter.py

    assert counter.invoke("increment", U32(5)) == U32(5)
    assert counter.invoke("increment", U32(3)) == U32(8)


def test_counter_traps_past_its_ceiling() -> None:
    from serpent.testing import RealContractError

    env = RealEnv()
    counter = env.deploy_source(EXAMPLE_COUNTER)
    counter.invoke("increment", U32(600))

    with pytest.raises(RealContractError) as excinfo:
        counter.invoke("increment", U32(500))  # 1100 > 1000
    assert excinfo.value.code == 1  # Error.MaxReached
```

`env.deploy_source(path, *constructor_args)` compiles the module at `path`
(`build_file`, the same compiler tier 1 and tier 2a use) and deploys it to a
fresh, empty `RealEnv` ledger; `RealContract.invoke(method, *args)` decodes
the result as the method's own return annotation declares it, so a struct, a
union and an int enum each decode as what the contract says they are, not as
whatever the host's raw ScVal happens to look like. `env.deploy(cls, ...)` is
the convenience form for a class already on `sys.modules`; `deploy_source` is
the primary one, because an example loaded by path never is.

## Building the extension

From the repository root:

```bash
VIRTUAL_ENV=$PWD/.venv uvx maturin develop --release --manifest-path host/Cargo.toml
```

Three traps, in order of how often they bite:

1. **The wrong interpreter.** `maturin develop` installs into whatever
   virtualenv it finds. `VIRTUAL_ENV=$PWD/.venv` from the repo root is what
   puts the module in the venv the test suite actually imports; building from
   inside `host/` with a bare `uv run --with maturin ...` links into a
   different, throwaway environment, and the suite then skips every
   real-host test while looking green.
2. **`uv sync` prunes it.** `serpent-host` is not a declared dependency of
   `serpent`, so a plain `uv sync` removes the module `maturin` just built.
   Run the suite with `uv run --no-sync pytest ...`, and rebuild after any
   `uv sync`.
3. **The cargo link.** An extension-module cdylib must leave Python's symbols
   unresolved until the interpreter loads it; `host/build.rs` and
   `host/.cargo/config.toml` are both what arrange that (the former travels
   with the crate, the latter applies only when cargo's working directory is
   `host/`), and `--manifest-path host/Cargo.toml` needs `build.rs` present to
   link at all.

Check what you got:

```bash
uv run --no-sync python -c "import serpent_host; print(serpent_host.__file__)"
```

## The `real_host` marker and `SERPENT_REQUIRE_REAL_HOST`

Every test that needs the extension carries `pytest.mark.real_host`.
`tests/conftest.py` decides its fate: without `SERPENT_REQUIRE_REAL_HOST=1` a
missing extension SKIPS such a test loudly (counted in the summary, with the
rebuild command in the reason); with it set, a missing extension fails the
whole session before collection can hide anything, so the real-host suite can
never pass vacuously. CI's Rust job sets the switch. Locally:

```bash
uv run --no-sync pytest -q tests/real_host                       # skips if unbuilt
SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q tests/real_host   # fails if unbuilt
```

## The divergence vocabulary

- **`mini_host_gap`** (`EnvScenario`, `tests/semantics/env_scenarios.py`) --
  set when a row reaches a surface the tier-2a MINI host does not model (TTL,
  auth-args, the allow-set). Not a gap in the corpus: the real host runs
  every row marked here.
- **`host_diverges`** (`HostDivergence`, same module) -- a DECLARED, expected
  difference between the real host and tier 1, set before the real leg is
  ever run from a fact already on record. The real-host runner asserts the
  difference still EXISTS, so a model fix that closes the gap fails the
  declaration loudly instead of letting it rot.
- **`real_unrunnable`** (same module) -- why the real leg cannot host a row
  at all (today: two rows that drive the ledger sequence to the top of the
  `U32` range, which no real host can do). Not a divergence and not a gap.
- **`chain_unproven`** (`HostFact`, `tests/semantics/host_facts.py`) -- marks
  a row whose evidence is about the TEST host and is known not to hold on the
  actual chain. There is exactly one today: persistent-entry archival.
- **`divergence_reason`** (`HostFact`) -- required exactly when a `HostFact`
  row's `real` and `tier1` expectations differ and `tier1` is not
  `Unmodelled`: which side is right, and why.
- **`FrozenTableDisagreement`** (`serpent.testing`) -- raised when a real-leg
  answer disagrees with tier 1 on a FROZEN table row with no declaration
  covering it. Not a bug to silently fix: it names the row and both answers,
  and escalates to a controller decision rather than editing the table to
  match the host.
- **`HOST_FACTS`** (`tests/semantics/host_facts.py`) -- the table of
  questions only the real host can answer (TTL clamp/trap, 128-bit
  division-by-zero's error code, whether a published event survives a raise,
  `COMPARE_VECTORS`' ordering answers), each with the model's assumption
  recorded next to the host's measured answer.

## Recording tier-3 fixtures

```bash
SERPENT_TESTNET_RECORD=1 uv run --no-sync python -m serpent.testing.testnet \
    record --contract <id> --out <dir> <methods...>
```

What it needs: a contract genuinely deployed to Stellar testnet (this repo's
own fixture is the deployed `examples/shapes.py`, `tests/real_host/fixtures/testnet/shapes/deployed.wasm`)
and the methods to simulate. What it never does, under any flag: sign or
submit a transaction. Recording only ever calls `simulateTransaction`, which
the network evaluates without committing anything -- there is no code path in
`serpent.testing.testnet` that reaches a signing key. Once recorded, the
fixtures replay OFFLINE: `tests/real_host/test_testnet_fixtures.py` never
touches the network again.

## Version pins, and what a protocol bump touches

| Pin | Value |
| --- | --- |
| `soroban-sdk` | `28.0.0-rc.1` (the protocol-28 test `Env`) |
| `soroban-env-host` | `28.0.2`, which must equal `src/serpent/_host/_codegen.py`'s `PINNED_TAG` (`v28.0.2`) -- that is the release serpent's own host-function table is generated from |
| `pyo3` | `0.29`, `abi3-py311` |

Testnet moved to protocol 28 (core 28.0.1) before 2026-09-02; mainnet stayed
on 27 (`docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`
§13). The embedded test host tracks testnet, which is why `DEFAULT_PROTOCOL`
above is 28 and not 27. When protocol 29 lands, `soroban-sdk` and
`soroban-env-host` move together, `PINNED_TAG` moves with them, and
`tests/real_host/`'s own protocol-ceiling test is the tripwire that says the
three have drifted apart if they ever do.
