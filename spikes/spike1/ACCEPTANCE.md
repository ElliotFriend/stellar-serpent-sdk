env.json pin: rs-soroban-env tag v28.0.2

| # | Check | How verified |
|---|-------|--------------|
| 1 | Module passes `wasm-tools validate --features=-all,mutable-global,sign-extension,bulk-memory` | Task 4 |
| 2 | Module declares memory, exports it as `memory`, has a data section | Task 4 (wasm-tools print) |
| 3 | `stellar contract info interface --wasm` renders setup/bump + Settings struct + Error enum LOCALLY, pre-deploy; same via --id post-deploy | Task 4 + Task 6 |
| 4 | Deployed to testnet; `stellar contract fetch` bytes == local bytes | Task 6 |
| 5 | `setup(3)` then `bump()` x3 returns 1, 2, 3 on-chain | Task 6 |
| 6 | 4th `bump()` fails; the failure surfaces contract error code **7** (raw number in CLI output; not a generic trap) in simulation/getTransaction | Task 6 |
| 7 | Settings struct round-trips through instance storage on-chain — proven behaviorally: bump can only enforce counter_limit=3 (row 6) by reading back the Map that setup stored under a >9-char SymbolObject key. (Optional extra: getLedgerEntries on the ContractInstance entry via stellar_sdk.) | Task 6 |
| 8 | Same bytes produce 1,2,3 then code-7 failure in wasmtime harness locally | Task 5 |
| 9 | env-meta built with stellar_sdk XDR byte-matches the known-good 12-byte golden (protocol 27); spec section parses in the local interface render of row 3 | Task 4 |
| 10 | mypy --strict findings on the designed authoring surface recorded, each with a chosen resolution (feeds spec §2 amendment in Task 8) | Task 2 |
