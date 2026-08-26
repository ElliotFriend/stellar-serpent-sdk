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
| 3 | `stellar contract info interface --wasm` renders setup/bump + Settings struct + Error enum LOCALLY, pre-deploy; same via --id post-deploy | **Task 4: local half PASS.** From the local file only: `fn setup(env, counter_limit: u32)`, `fn bump(env) -> u32`, `pub struct Settings { counter_limit: u32, display_name: String }`, `pub enum Error { LimitExceeded = 7 }`. Both 13- and 12-char field names round-trip. Post-deploy `--id` half remains Task 6. |
| 4 | Deployed to testnet; `stellar contract fetch` bytes == local bytes | Task 6 |
| 5 | `setup(3)` then `bump()` x3 returns 1, 2, 3 on-chain | Task 6 |
| 6 | 4th `bump()` fails; the failure surfaces contract error code **7** (raw number in CLI output; not a generic trap) in simulation/getTransaction | Task 6 |
| 7 | Settings struct round-trips through instance storage on-chain — proven behaviorally: bump can only enforce counter_limit=3 (row 6) by reading back the Map that setup stored, and it reaches the 13-char `counter_limit` field through a runtime `symbol_new_from_linear_memory` SymbolObject (a SymbolSmall cannot hold it). (Optional extra: getLedgerEntries on the ContractInstance entry via stellar_sdk.) | Task 6 |
| 8 | Same bytes produce 1,2,3 then code-7 failure in wasmtime harness locally | Task 5 |
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
