env.json pin: rs-soroban-env tag v28.0.2

Build under test: `spikes/spike1/spike.wasm`, **877 bytes**, sha256
`bc2e806302f655686084f5c604b4e642900e0fa7812310378667a9cabe4a9920`.
Reproduce with
`uv run python spikes/spike1/build.py spikes/spike1/contract_src.py -o spikes/spike1/spike.wasm`
(byte-identical on rebuild; the wasm itself is git-ignored).
env-meta declares protocol **27**; the floor computed from the eight imports is
**20** (none of them carries a `min_supported_protocol` in v28.0.2).

| # | Check | How verified |
|---|-------|--------------|
| 1 | Module passes `wasm-tools validate --features=-all,mutable-global,sign-extension,bulk-memory` | **Task 4: PASS.** Exit 0 on `spike.wasm`. `build.py` runs the same command on a temp copy *before* writing `-o`, so an invalid module cannot reach the output path — proven by corrupting one opcode: validate fails (`func 8 failed to validate: invalid value type`), `build.py` exits 1, no file appears. `test_module_validates` runs the gate in CI. |
| 2 | Module declares memory, exports it as `memory`, has a data section | **Task 4: PASS.** `wasm-tools print` shows `(memory (;0;) 1)`, `(export "memory" (memory 0))`, and one active data segment at offset 0 holding `counter_limit`, `display_name`, the 8-byte `(ptr,len)` map-key descriptors, and `serpent phase zero`. Section ids present and ascending: 1,2,3,5,7,10,11 (`test_module_has_memory_export_and_data`). Layout finding recorded below. |
| 3 | `stellar contract info interface --wasm` renders setup/bump + Settings struct + Error enum LOCALLY, pre-deploy; same via --id post-deploy | **PASS (both halves).** Task 4: local half PASS — from the local file only: `fn setup(env, counter_limit: u32)`, `fn bump(env) -> u32`, `pub struct Settings { counter_limit: u32, display_name: String }`, `pub enum Error { LimitExceeded = 7 }`. Both 13- and 12-char field names round-trip. Task 6: `stellar contract info interface --id CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI --network testnet` renders **byte-for-byte identical output on stdout** to the local `--wasm` render (the leading `ℹ️`/`🌎` progress lines are stderr and necessarily differ by source; see `DEPLOY_LOG.md` Step 3 for the stdout-only diff). |
| 4 | Deployed to testnet; `stellar contract fetch` bytes == local bytes | **PASS.** Deployed contract ID `CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI` (deploy tx `b51fa39e24a31e7f5ec31417639f72677231b01e4c66955f5b6d478e1a904e05`). `stellar contract fetch --id ... --out-file /tmp/fetched.wasm` then `cmp /tmp/fetched.wasm spikes/spike1/spike.wasm` → `IDENTICAL`; fetched wasm's sha256 `bc2e806302f655686084f5c604b4e642900e0fa7812310378667a9cabe4a9920` matches the pinned local value exactly. See `DEPLOY_LOG.md` Step 2. |
| 5 | `setup(3)` then `bump()` x3 returns 1, 2, 3 on-chain | **PASS.** `setup --counter_limit 3` submitted successfully (tx `5298fc5afb0de550f966733e87722dfd36bb4b117f969273593bf7029787ea4b`). Three subsequent `bump` invocations returned `1`, `2`, `3` in order (tx `cba5598f…`, `268d69ec…`, `18490b08…` respectively). See `DEPLOY_LOG.md` Step 4. |
| 6 | 4th `bump()` fails; the failure surfaces contract error code **7** (raw number in CLI output; not a generic trap) in simulation/getTransaction | **PASS.** 4th `bump` invocation exits nonzero (exit code 1) with `❌ error: transaction simulation failed: HostError: Error(Contract, #7)`; diagnostic events also show `Error(Contract, #7)` and `["failing with contract error", 7]`. This is the raw numeric form, not the enum variant name (`LimitExceeded`) — expected per stellar-cli issue #2377, called out in the task brief. Critically, the error surfaced as contract error code 7, **not** a generic `InvalidAction`/`UnreachableCodeReached` trap — no FINDING needed here; the `fail_with_error` path works correctly on-chain. See `DEPLOY_LOG.md` Step 4. |
| 7 | Settings struct round-trips through instance storage on-chain — proven behaviorally: bump can only enforce counter_limit=3 (row 6) by reading back the Map that setup stored, and it reaches the 13-char `counter_limit` field through a runtime `symbol_new_from_linear_memory` SymbolObject (a SymbolSmall cannot hold it). (Optional extra: getLedgerEntries on the ContractInstance entry via stellar_sdk.) | **PASS (proven behaviorally).** Rows 5-6 together are the proof: `bump` returned exactly `1, 2, 3` and then enforced the `counter_limit=3` cutoff on the 4th call, which is only possible if `bump` read back the `Settings` Map (including the 13-char `counter_limit` field) that `setup` wrote to instance storage on a prior, separate transaction. No direct storage inspection was performed beyond this behavioral proof. |
| 8 | Same bytes produce 1,2,3 then code-7 failure in wasmtime harness locally | **Task 5: PASS.** `spikes/spike1/harness.py` runs the committed `spike.wasm` (sha256 `bc2e806…`, unchanged) under wasmtime with a mini-host implementing only the eight imported host functions: `setup(3)` then `bump()`×3 returns `U32Val(1..3)`, and the 4th raises the contract error `Val` `0x0000000700000003` — code **7**, not a generic trap (`test_bump_sequence_and_error`). Passing a Void `Val` where a U32 is declared is rejected by the ABI prologue as `0xFFFFFFFF00000003` (`test_tag_check_prologue`). Also shown locally: the `Settings` Map round-trips through instance storage with both long field names and the string literal (`test_settings_map_round_trips_through_instance_storage` — the local half of row 7's mechanism), and the harness enforces the host's ascending-key invariant. **Caveat:** the harness is a stand-in, not an oracle — no budget, footprint, auth, or persistence, and the wasmtime `Config` sets the feature flags it knows about without proving the set matches the chain's; **the full feature-set assertion test is deferred to M1 tier-2a.** Row 4/5/6 still need the real host. |
| 9 | env-meta built with stellar_sdk XDR byte-matches the known-good 12-byte golden (protocol 27); spec section parses in the local interface render of row 3 | **Task 4: PASS.** `sections.env_meta(27) == 000000000000001b00000000` (`test_env_meta_golden_bytes`); `stellar contract info env-meta --wasm` reports `Protocol: v27`. All three custom sections come from `stellar_sdk.xdr`, no hand-rolled XDR: `contractspecv0` re-decodes with `SCSpecEntry.unpack` into struct + error-enum + 2 functions (`test_spec_stream_round_trips`) and renders in row 3; `contractmetav0` reports `serpent: 0.0.1-spike1`. Docstrings reach the spec `doc` fields (visible in `info interface --output json`). |
| 10 | mypy --strict findings on the designed authoring surface recorded, each with a chosen resolution (feeds spec §2 amendment in Task 8) | Task 2 |

## Finding: `map_new_from_linear_memory` keys are descriptors, not Vals

The plan assumed the keys array was `len` 8-byte Symbol `Val`s, which would have
forced the >9-char field names through `symbol_new_from_linear_memory` at
runtime and made the keys array runtime state. env.json's own doc string for
the host function says otherwise:

> Key strings are specified as `len` 8 byte slices consisting of the 4 byte
> pointer and 4 byte length. Actual keys must be byte strings sorted in
> ascending order and be convertible to `Symbol` type. Values may be arbitrary
> `Val`s.

So keys are `(u32 ptr, u32 len)` descriptors into linear memory — pure
compile-time data, sorted at compile time, emitted in the data segment. Only
the **values** array is `Val`s and needs runtime scratch. The wrong layout
would have produced a module that passes `wasm-tools validate` and then panics
in the host, i.e. a failure that only surfaces at row 5/8. Long field names
still cost a runtime `symbol_new_from_linear_memory` on the *read* side, where
`map_get` does take a real `Symbol` `Val`.
