# Spike 2 findings: PyO3-embedded real host

**Question:** can the real `soroban-env-host` be embedded as a Python extension
module and run the *same* contract bytes that ran on testnet, with the *same*
results?

**Answer: yes, and it is cheap.** ~96 lines of Rust, one clean compile, and the
test passed on the first run. Recommendation below: **adopt as tier-2b.**

## Result: exact parity with testnet

The same `spikes/spike1/spike.wasm` (sha256
`bc2e806302f655686084f5c604b4e642900e0fa7812310378667a9cabe4a9920`) that
deployed to testnet as `CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI`
was uploaded to the embedded host and driven through the identical sequence.

| Step | Testnet (Task 6) | Embedded real host (this spike) |
| --- | --- | --- |
| `setup(3)` | ok | ok |
| `bump` x3 | 1, 2, 3 | 1, 2, 3 |
| 4th `bump` | `Error(Contract, #7)` | `RuntimeError: contract error code 7` |

```
$ uv run --no-sync pytest spikes/spike2/test_real_host.py -v
spikes/spike2/test_real_host.py::test_same_bytes_on_real_host PASSED      [ 16%]
...
============================== 6 passed in 0.76s ===============================
```

(The first test is the brief's, verbatim. The other five are regression guards
added for findings #1 and #3 below.)

**No divergence of any kind was found.** Nothing in this spike had to be
special-cased, tolerated, or explained away.

## Resolved versions

Recorded from `cargo tree` (full graph pinned in the committed `Cargo.lock`):

| Crate | Resolved version |
| --- | --- |
| `soroban-sdk` | 27.0.6 |
| **`soroban-env-host`** | **27.0.1** |
| `soroban-env-common` | 27.0.1 |
| `soroban-wasmi` | 0.31.1-soroban.20.0.1 |
| `pyo3` | 0.23.5 |

Toolchain: rustc 1.97.1, CPython 3.11.7, macOS arm64. 224 packages in the
lockfile.

## Performance

Release build, warm `RealEnv`, `time.perf_counter`:

| Measurement | Release | Debug |
| --- | --- | --- |
| 1,000 `bump` invocations | **20.4 ms total / 20.4 us per invoke (~49,000 invokes/sec)** | 233 ms / 233 us per invoke (~4,300/sec) |
| `RealEnv()` + `register(wasm)` | **0.133 ms** (median of 20) | 1.240 ms |

Two things worth pulling out:

- **Debug builds are ~11x slower than release.** `maturin develop` defaults to
  the debug profile, so the number a developer sees by default is the bad one.
  Whatever ships in M1 should build the wheel with `--release`.
- **Budget does not accumulate across top-level invocations.** 1,000 sequential
  `bump` calls on one `RealEnv` never tripped the budget, so a single env can
  host a long property-test campaign without being torn down.

For scale: this is a sub-millisecond setup and a ~20 microsecond call, against a
network round trip plus ledger close on testnet. Fast enough that
Hypothesis-style property testing and mutation testing against the real host are
practical, which is the entire reason to want tier-2b.

## Wheel-build friction

| Fact | Value |
| --- | --- |
| Clean release build (`cargo clean` then `maturin develop --release`) | **43.4 s wall**, 227 s CPU |
| `target/` after a release build | 717 MB (2.2 GB with debug too) |
| Shipped `.so` | **6.1 MB** |
| Incremental rebuild after editing `lib.rs` only | ~5 s |

Friction actually encountered, in order:

1. **`uv run --with maturin` from `spikes/spike2` builds against the wrong
   interpreter.** The brief expected maturin to auto-detect the repo-root
   `.venv`. It does not: `spikes/spike2/pyproject.toml` makes that directory its
   own uv project, so `uv run` created `spikes/spike2/.venv` on **Python 3.12**
   (repo root is 3.11), emitted a `spikes/spike2/uv.lock`, and installed the
   module somewhere the repo's pytest could never import it. The build
   "succeeded" while accomplishing nothing.

   Working command (from `spikes/spike2`), which pins the interpreter explicitly:

   ```
   VIRTUAL_ENV=<repo-root>/.venv uvx maturin develop --release
   # -> Found CPython 3.11 at <repo-root>/.venv/bin/python
   ```

   `uvx` (not `uv run`) matters: it runs maturin from an isolated tool env
   instead of adopting the spike directory as a project.

2. **Plain `cargo build` cannot link this crate on macOS.** With
   `pyo3/extension-module`, the Python symbols are meant to be resolved by the
   host interpreter at load time, and cargo does not pass the flags that allow
   that:

   ```
   error: linking with `cc` failed: exit status: 1
     = note: Undefined symbols for architecture arm64:
             "_PyBaseObject_Type", referenced from: ...
             "_PyBytes_AsString", referenced from: ...
   ```

   `cargo check` is clean; only the link step fails. maturin injects
   `-C link-arg=-undefined -C link-arg=dynamic_lookup` and works. M1 should
   either commit a `.cargo/config.toml` with those flags or document that
   maturin is the only supported build entry point.

3. **`uv sync` prunes the maturin-installed module** (known caveat, confirmed by
   the install layout: maturin writes `site-packages/serpent_host/` directly and
   nothing in `uv.lock` references it). Re-run maturin develop after every sync.

None of this is severe, but all three are silent-ish failure modes that will bite
contributors. They belong in a CONTRIBUTING note, not in tribal memory.

## API pain points

Rust total: **140 lines** in `src/lib.rs`, 96 non-blank non-comment (107/75 before
the Symbol-validation fix in #3). It compiled clean on the **first** `cargo
check` with zero warnings. The brief's verified API
facts were accurate except where noted.

### 1. `soroban_sdk::Error` has no `get_type()` (brief deviation)

The brief's sketch mapped the `Err(Ok(e))` arm straight to
`"contract error code {e.get_code()}"`. That is wrong, and the first version of
this spike reproduced the bug: with `E = soroban_sdk::Error`, that arm catches
**every** error the host can express, not just `Error(Contract, #N)`. Calling a
function that does not exist produced:

```
missing-fn: contract error code 6      <- a lie; it is Error(Context, InvalidAction)
```

The obvious fix, `e.get_type()`, does not exist, and `soroban-env-common`'s
source says why:

```rust
// NB: we don't provide a "get_type" to avoid casting a bad bit-pattern into
// an ScErrorType. Instead we provide an "is_type" to check any specific
// bit-pattern.
pub const fn is_type(&self, type_: ScErrorType) -> bool
```

So the correct discrimination is `e.is_type(ScErrorType::Contract)`, falling back
to `Debug` (which renders `Error(WasmVm, InvalidInput)`) for everything else.
After the fix:

```
contract-err: contract error code 7
missing-fn:   host error Error(Context, InvalidAction)
```

This is worse than a cosmetic mislabel. `ScErrorCode::InternalError` is **7**,
the same number as this contract's `LimitExceeded`, so under the brief's mapping
a host-internal failure would have surfaced as the exact string
`"contract error code 7"` that the headline test asserts on. The spike's
central claim would have been unfalsifiable: the assertion could pass on a
host crash. Guarded now by
`test_missing_function_is_not_reported_as_a_contract_error`.

**For M1: never conflate the two.** A user debugging a typo'd function name must
not be told their contract raised error 6 -- and no host failure may ever be
allowed to impersonate a contract error code.

### 2. `#[pyclass(unsendable)]` is mandatory, not a stylistic choice

Confirmed by removing it:

```
error[E0277]: `Rc<soroban_env_host::host::HostImpl>` cannot be sent between threads safely
note: required because it appears within the type `soroban_env_host::host::Host`
note: required because it appears within the type `Env`
note: required because it appears within the type `RealEnv`
```

`Env` is `Rc`-backed, so it is neither `Send` nor `Sync` and this is structural,
not incidental. Consequences for M1: a `RealEnv` is pinned to the thread that
created it (pyo3 raises if another thread touches it); process-level parallelism
(`pytest-xdist`) is fine, thread-level is not; and free-threaded CPython builds
will need thought.

### 3. Host errors escape as panics, not exceptions

Several sdk entry points panic on bad input rather than returning `Result`. A
panic prints a full Rust backtrace plus diagnostic event log to stderr and
reaches Python as `pyo3_runtime.PanicException`, which derives from
`BaseException`, **not** `Exception` — so `except Exception:` does not catch it,
and neither does `pytest.raises(Exception)`. Raw panics are not an acceptable
user-facing error channel for an SDK.

Current panic-source inventory for the three strings this API accepts from
Python:

| Entry point | Bad input | Status |
| --- | --- | --- |
| `Symbol` from `func` | `"has-dash"`, `"two words"`, 40 chars | **fixed in this spike** — pre-validated, now a catchable `RuntimeError` |
| `Address::from_string` on `contract` | `"NOTANADDRESS"` | still panics (`Error(Value, InvalidInput)`, "unexpected strkey length") |
| `Env::register` on `wasm` | malformed module | still panics |

**The nominally-fallible Symbol conversion is not actually fallible.** The
initial version used `Symbol::new`; the brief prescribed
`Symbol::try_from_val(&env, func)` instead. Both panic. `TryFromVal<Env, &str>
for Symbol` advertises `type Error = ConversionError`, but its body
(`soroban-sdk/src/symbol.rs:136`) delegates through `unwrap_optimized`, which on
non-wasm targets is a plain `.unwrap()` (`soroban-sdk/src/unwrap.rs:46`):

```
panicked at soroban-sdk-27.0.6/src/unwrap.rs:46:14:
called `Result::unwrap()` on an `Err` value: HostError: Error(Value, InvalidInput)
```

So there is **no panic-free path from an arbitrary `&str` to a `Symbol`** in
soroban-sdk 27.0.6, and swapping to the "fallible" API alone does not fix
anything. The fix is to validate first, against the SDK's own rules — at most
`SCSYMBOL_LIMIT` (32) bytes, characters drawn from `[a-zA-Z0-9_]` — using the
exported constant rather than a hardcoded 32. Verified at the boundary:

```
has-dash:           RuntimeError: ... character '-' is outside [a-zA-Z0-9_]
32-char (at limit): RuntimeError: host error Error(Context, InvalidAction)   <- reaches the host
33-char (over):     RuntimeError: ... 33 bytes exceeds the 32-byte limit
```

Covered by `test_unrepresentable_function_name_raises_catchable_error` and
`test_env_still_usable_after_bad_function_name` (rejecting a name does not
poison the env).

**For M1:** treat "returns `Result`" as an unverified claim in this SDK. Every
value crossing the Python boundary needs its own validation layer; the remaining
two rows of the table above are the outstanding work.

### 4. The protocol version is half-baked-in

`set_ledger(..., protocol)` is genuinely enforced, but only downward, and only
within the range the linked host supports:

| `set_ledger` protocol | Result |
| --- | --- |
| default (27) | ok, `bump` returns 1 |
| 22 | `HostError: Error(WasmVm, InvalidInput)` (host rejects the p27 wasm) |
| 99 | `HostError: Error(Context, InternalError)` |

So the *ceiling* is a property of the compiled-in `soroban-env-host` 27.0.1, not
a runtime knob. **Testing against protocol 28 will require a rebuild against a
newer soroban-sdk, i.e. a new wheel.** This is the single biggest strategic cost
of tier-2b: serpent's wheel matrix becomes platform x Python x *protocol*.

### 5. Minor, all as documented

`Env::new_with_config(EnvTestConfig { capture_snapshot_at_drop: false, .. })`
works and verifiably suppresses the snapshot writes (no `test_snapshots/`
appeared anywhere after the full test and benchmark runs). `env.register(wasm,
())` with `&[u8]`, `addr.to_string().to_string()`, the `Ledger as _` trait
import, inherent `mock_all_auths()`, and the ungated `xdr` re-export all behaved
exactly as the brief stated. The `Ok(Err(_))` arm of `try_invoke_contract` is
unreachable for `T = Val` (its conversion error is `Infallible`) but must still
be named.

## Recommendation: adopt as tier-2b

**Adopt.** Do not fall back to quickstart-RPC for this tier.

The case:

- **Fidelity is the whole point, and it is exact.** Same bytes, same results,
  including the error code. This is the real host, not an approximation of it, so
  a passing tier-2b test is meaningful evidence about testnet behavior.
- **The cost is trivially small.** Under 100 lines of Rust, no glue layer, no
  daemon, no container, no network. The binding compiled on the first attempt.
- **It is fast enough to change how people test.** ~49,000 invocations/sec and
  0.13 ms env setup put property-based and mutation testing against the real host
  within reach. Quickstart-RPC cannot offer that at any price: it is a container
  plus a network round trip plus ledger close per call.
- **Failure modes found are all fixable in-binding**, not inherent: error
  discrimination (#1) and panic containment (#3) are our code. Both fixes are
  demonstrated here rather than asserted -- #1 and the Symbol half of #3 are
  done and under test; the remaining two panic sources are the same shape of
  work.

The honest costs to accept going in:

1. **A wheel matrix, including a protocol axis** (#4). Needs cibuildwheel and a
   policy for which protocols are supported simultaneously. This is the item to
   design before M1, not during.
2. **Single-threaded envs** (#2). Fine for pytest, worth stating in the docs.
3. **Contributors need a Rust toolchain** for source builds, and the two
   build-invocation traps in the friction section need documenting.

A quickstart-RPC tier still has a place for end-to-end and integration coverage
(fees, ledger close, RPC surface). But it is a complement to this, not a
substitute: it cannot deliver microsecond iteration, and this cannot deliver
network semantics.

## Reproducing

```bash
cd spikes/spike2
VIRTUAL_ENV=<repo-root>/.venv uvx maturin develop --release
cd ../.. && uv run --no-sync pytest spikes/spike2/test_real_host.py -v
```
