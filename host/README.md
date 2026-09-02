# `serpent-host` — the real host, embedded

## What it is

serpent's **tier 2b** test host (dossier §D.1). One PyO3 extension module,
`serpent_host`, wrapping ONE `soroban_sdk::Env` (the sdk's test host, which is
the real `soroban-env-host`) as a single `#[pyclass(unsendable)]` called
`RealEnv`.

The boundary is deliberately dumb: **ScVal XDR bytes in, ScVal XDR bytes out**.
Rust knows nothing about serpent's types, its error enums, or its storage
model; `serpent.testing` (Python, Task 3) owns all of that. Every method is
wrapped in `catch_unwind`, and every failure — a contract's own error code, a
host error, a residual panic out of the sdk, bad input, a failed conversion —
arrives in Python as one exception class, `HostFailure`, whose `args` are
`(kind, error_type, code, message)`. Nothing here ever raises
`pyo3_runtime.PanicException` (P3/E4).

Tier 1 (`serpent.env`) is a hand-written model of the host and a fast authoring
loop. Only this tier is evidence.

- `src/lib.rs` — `RealEnv`, `HostFailure`, the containment.
- `src/errors.rs` — the one classification of a `soroban_sdk::Error` (P4): a
  `Context(InvalidAction)` must never read as contract code 6, and
  `InternalError = 7` must never impersonate a contract's code 7.
- `src/validate.rs` — boundary pre-validation (P3): symbols, contract strkeys,
  and the wasm header are checked BEFORE anything reaches the sdk, because
  "returns `Result`" is an unverified claim in this SDK.
- `serpent_host.pyi` — the Python-visible contract, and what Task 3 type-checks
  against.

## Building

From the **repository root**:

```bash
VIRTUAL_ENV=$PWD/.venv uvx maturin develop --release --manifest-path host/Cargo.toml
```

Then check what you got:

```bash
uv run --no-sync python -c "import serpent_host; print(serpent_host.__file__)"
```

The path must be under the repo's own `.venv`.

The Rust gate, from `host/`:

```bash
cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test
```

## The four traps

1. **`--release`, always** (P1). `maturin develop` defaults to a DEBUG build,
   which runs this host about 11x slower. There is no reason to ever want it.
2. **The right interpreter** (P6). `maturin develop` installs into whatever
   virtualenv it finds. `VIRTUAL_ENV=$PWD/.venv` from the repo root is what
   puts the module in the venv the test suite actually imports; running
   `uv run --with maturin ...` from inside `host/` builds into a DIFFERENT,
   throwaway environment and the suite then skips every real-host test while
   looking green.
3. **`uv sync` prunes it** (P6). `serpent-host` is not a declared dependency of
   `serpent`, so `uv sync` removes it. Run the suite with
   `uv run --no-sync pytest ...`, and rebuild after any `uv sync`.
4. **Plain `cargo` and the macOS linker** (P8). An extension-module cdylib must
   leave Python's symbols unresolved until the interpreter loads it. Two things
   arrange that here: `build.rs` (which emits `-undefined dynamic_lookup` as
   cdylib link args and therefore travels with the crate, whatever the current
   directory) and `.cargo/config.toml` (the same flags as `rustflags`, which
   cargo reads only when the working directory is `host/`). `build.rs` is what
   makes the `--manifest-path host/Cargo.toml` command above link at all;
   without it the build fails with `Undefined symbols:
   _PyBytes_FromStringAndSize, ...`.

## `unsendable`: one env per thread

`RealEnv` is `#[pyclass(unsendable)]`. The sdk's `Env` is full of `Rc` and is
not `Send`, so touching one `RealEnv` from a thread other than the one that
created it raises rather than corrupting memory. That is a deliberate choice
(P9): real-host tests parallelise at the PROCESS level (`pytest -p xdist`
workers, separate interpreters), never by sharing an env across threads. Each
`RealEnv()` is an independent, empty ledger.

`capture_snapshot_at_drop` is forced to `false` (P11): otherwise the sdk's
`Drop` writes `test_snapshots/*.json` into the current working directory of
whoever ran the tests.

## Version pins, and what bumps them

| Pin | Why |
| --- | --- |
| `soroban-sdk = "=28.0.0-rc.1"` | The protocol-28 test `Env`. Rulings E1/E11. |
| `soroban-env-host = "=28.0.2"` | The raw Host API the sdk hides: `Host::current_test_protocol()`, `get_diagnostic_events()`, the `Compare` trait, `InvocationResources`. The sdk pins this exact release, so the direct dependency adds nothing to the lock graph — it only makes a private module reachable (review B6). |
| `stellar-strkey = "=0.0.16"` | A `Result`-returning contract-strkey parser, so a bad address is `invalid_input` and never a panic inside `Address::from_string`. |
| `pyo3 = "0.29"` with `abi3-py311` | One wheel for 3.11+. |

`soroban-env-host`'s version must stay equal to `PINNED_TAG` in
`src/serpent/_host/_codegen.py` (`v28.0.2`): that is the release serpent's own
host-function table is generated from. Bumping one without the other is exactly
the drift Task 3's version test exists to catch. When protocol 29 lands, both
move together — and `test_protocol_is_28_and_equals_the_compiled_in_ceiling`
in `tests/real_host/` is the tripwire that says so.
