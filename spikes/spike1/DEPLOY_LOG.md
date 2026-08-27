# Spike 1 — Testnet Deploy + Invoke Round-Trip Log

Network: **Test SDF Network ; September 2015** (testnet only). CLI: `stellar 27.1.0
(8e402ea28202950b272fbabc34caad4d2f64fe87)`.

Artifact under test: `spikes/spike1/spike.wasm`, 877 bytes, sha256
`bc2e806302f655686084f5c604b4e642900e0fa7812310378667a9cabe4a9920` — verified
against the pinned value before deploying (matched, no rebuild needed).

Note: every command below also printed this harness warning to stderr, which is
unrelated to the spike and is elided from the transcripts after this line to
reduce noise:

```
⚠️  A local config was found at "/Users/elliotvoris/.stellar" but is no longer read.
     Run `stellar config migrate` to move the local config into the global config ("/Users/elliotvoris/.config/stellar").
```

## Step 1: Fresh identity + fund

`serpent-spike` did not already exist under `stellar keys ls`, so this was a
plain (non-`--overwrite`) generate.

```
$ stellar keys generate serpent-spike --network testnet --fund
✅ Key saved with alias serpent-spike in "/Users/elliotvoris/.config/stellar/identity/serpent-spike.toml"
✅ Account serpent-spike funded on "Test SDF Network ; September 2015"
```

(No `--global` flag used — confirmed removed from stellar-cli 27.1.0, per the brief.)

## Step 2: Deploy

```
$ stellar contract deploy --wasm spikes/spike1/spike.wasm --source serpent-spike --network testnet
ℹ️  Uploading contract WASM…
ℹ️  Simulating transaction…
ℹ️  Signing transaction: 6715b1f0c00734c8c40513e8791a498f168229cfdef9f9b7d2b5bb535627327b
🌎 Sending transaction…
✅ Transaction submitted successfully!
🔗 https://stellar.expert/explorer/testnet/tx/6715b1f0c00734c8c40513e8791a498f168229cfdef9f9b7d2b5bb535627327b
ℹ️  Deploying contract using wasm hash bc2e806302f655686084f5c604b4e642900e0fa7812310378667a9cabe4a9920
ℹ️  Simulating transaction…
ℹ️  Signing transaction: b51fa39e24a31e7f5ec31417639f72677231b01e4c66955f5b6d478e1a904e05
🌎 Sending transaction…
✅ Transaction submitted successfully!
🔗 https://stellar.expert/explorer/testnet/tx/b51fa39e24a31e7f5ec31417639f72677231b01e4c66955f5b6d478e1a904e05
🔗 https://lab.stellar.org/r/testnet/contract/CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI
✅ Deployed!
CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI
```

**Contract ID:** `CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI`

Upload tx: `6715b1f0c00734c8c40513e8791a498f168229cfdef9f9b7d2b5bb535627327b`
Create-contract tx: `b51fa39e24a31e7f5ec31417639f72677231b01e4c66955f5b6d478e1a904e05`
On-chain wasm hash reported by the CLI matches the pinned sha256:
`bc2e806302f655686084f5c604b4e642900e0fa7812310378667a9cabe4a9920`.

### Byte-fidelity check

```
$ stellar contract fetch --id CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI --network testnet --out-file /tmp/fetched.wasm
$ cmp /tmp/fetched.wasm spikes/spike1/spike.wasm && echo IDENTICAL
IDENTICAL
$ shasum -a 256 /tmp/fetched.wasm
bc2e806302f655686084f5c604b4e642900e0fa7812310378667a9cabe4a9920  /tmp/fetched.wasm
```

`cmp` reports no differences; the fetched wasm's sha256 matches the local
artifact's sha256 exactly.

## Step 3: Confirm tooling interop on-chain (row 3, second half)

```
$ stellar contract info interface --id CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI --network testnet
ℹ️  Network: Test SDF Network ; September 2015
🌎 Downloading contract spec: CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI
#[soroban_sdk::contractargs(name = "Args")]
#[soroban_sdk::contractclient(name = "Client")]
pub trait Contract {
    fn setup(env: soroban_sdk::Env, counter_limit: u32);
    fn bump(env: soroban_sdk::Env) -> u32;
}
#[soroban_sdk::contracttype(export = false)]
#[derive(Debug, Clone, Eq, PartialEq, Ord, PartialOrd)]
pub struct Settings {
    pub counter_limit: u32,
    pub display_name: soroban_sdk::String,
}
#[soroban_sdk::contracterror(export = false)]
#[derive(Debug, Copy, Clone, Eq, PartialEq, Ord, PartialOrd)]
pub enum Error {
    LimitExceeded = 7,
}
```

Re-ran the local `--wasm` render (Task 4) side-by-side for comparison:

```
$ stellar contract info interface --wasm spikes/spike1/spike.wasm
ℹ️  Loading contract spec from file...
#[soroban_sdk::contractargs(name = "Args")]
#[soroban_sdk::contractclient(name = "Client")]
pub trait Contract {
    fn setup(env: soroban_sdk::Env, counter_limit: u32);
    fn bump(env: soroban_sdk::Env) -> u32;
}
#[soroban_sdk::contracttype(export = false)]
#[derive(Debug, Clone, Eq, PartialEq, Ord, PartialOrd)]
pub struct Settings {
    pub counter_limit: u32,
    pub display_name: soroban_sdk::String,
}
#[soroban_sdk::contracterror(export = false)]
#[derive(Debug, Copy, Clone, Eq, PartialEq, Ord, PartialOrd)]
pub enum Error {
    LimitExceeded = 7,
}
```

**Byte-for-byte identical rendering on stdout** between local `--wasm` and
on-chain `--id` (the leading `ℹ️`/`🌎` progress lines visible in the two
transcripts above are stderr and necessarily differ by source — `Loading
contract spec from file...` locally vs. `Network: ...` / `Downloading contract
spec: ...` on-chain; the spec body itself, captured on stdout, is identical —
re-verified by capturing stdout separately with `1>out 2>/dev/null` for each
command and running `diff`, which reported no differences). Row 3 (second
half) confirmed PASS.

## Step 4: Invoke round-trip (rows 5-7)

### setup(counter_limit=3)

```
$ stellar contract invoke --id CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI --source serpent-spike --network testnet -- setup --counter_limit 3
ℹ️  Simulating transaction…
ℹ️  Signing transaction: 5298fc5afb0de550f966733e87722dfd36bb4b117f969273593bf7029787ea4b
🌎 Sending transaction…
✅ Transaction submitted successfully!
🔗 https://stellar.expert/explorer/testnet/tx/5298fc5afb0de550f966733e87722dfd36bb4b117f969273593bf7029787ea4b
```

Exit code 0, no return value printed (setup returns unit) — matches the
wasmtime harness's local behavior from Task 5.

### bump x3

```
$ for i in 1 2 3; do stellar contract invoke --id CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI --source serpent-spike --network testnet -- bump; done
```

| bump # | tx hash | return value |
|---|---|---|
| 1 | `cba5598f7c18899822492ced8d263ea6833076b80cc6a57c01f9d964bc8bdf6a` | `1` |
| 2 | `268d69ecdfd3c0474659d95d3eb4f284f8e091537b604eaaf4135f0d8cffd5ef` | `2` |
| 3 | `18490b08586ea25e3ea8173a335b242e224f491c83b55831c32205a97ef0ff55` | `3` |

Each invocation printed `✅ Transaction submitted successfully!` (exit 0)
followed by the printed return value (`1`, `2`, `3` respectively) — exactly the
sequence expected and matching the Task 5 wasmtime-harness result.

### bump #4 (expected to FAIL)

```
$ stellar contract invoke --id CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI --source serpent-spike --network testnet -- bump
❌ error: transaction simulation failed: HostError: Error(Contract, #7)

Event log (newest first):
   0: [Diagnostic Event] contract:CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI, topics:[error, Error(Contract, #7)], data:"escalating error to VM trap from failed host function call: fail_with_error"
   1: [Diagnostic Event] contract:CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI, topics:[error, Error(Contract, #7)], data:["failing with contract error", 7]
   2: [Diagnostic Event] topics:[fn_call, CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI, bump], data:Void

EXIT CODE: 1
```

**Result: exit code 1 (as expected — nonzero exit here is SUCCESS for this
check).** The diagnostic output contains the raw numeric form
`Error(Contract, #7)` in three places (top-level error, and both diagnostic
events), matching the `fail_with_error` call site in `contract_src.py`. The CLI
does **not** print the enum variant name `LimitExceeded` — this is the expected
stellar-cli issue #2377 limitation called out in the brief, not a spike defect.

**No FINDING here**: the error surfaced as contract error code 7 exactly as
designed, not as a generic `InvalidAction`/`UnreachableCodeReached` trap. The
`fail_with_error` path is confirmed correct on-chain.

## Summary

| Item | Value |
|---|---|
| Contract ID | `CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI` |
| Deployer identity | `serpent-spike` (fresh, testnet-funded) |
| Upload tx | `6715b1f0c00734c8c40513e8791a498f168229cfdef9f9b7d2b5bb535627327b` |
| Create-contract tx | `b51fa39e24a31e7f5ec31417639f72677231b01e4c66955f5b6d478e1a904e05` |
| setup tx | `5298fc5afb0de550f966733e87722dfd36bb4b117f969273593bf7029787ea4b` |
| bump #1 tx | `cba5598f7c18899822492ced8d263ea6833076b80cc6a57c01f9d964bc8bdf6a` → `1` |
| bump #2 tx | `268d69ecdfd3c0474659d95d3eb4f284f8e091537b604eaaf4135f0d8cffd5ef` → `2` |
| bump #3 tx | `18490b08586ea25e3ea8173a335b242e224f491c83b55831c32205a97ef0ff55` → `3` |
| bump #4 | simulation failed, exit 1, `Error(Contract, #7)` — expected |
| Fetch/cmp | `IDENTICAL`, sha256 `bc2e806302f655686084f5c604b4e642900e0fa7812310378667a9cabe4a9920` matches pinned value |
| Interface render (--id) | byte-identical to local `--wasm` render |

All testnet operations succeeded on the first attempt — no retries were
needed, no friendbot/RPC flakiness observed.
