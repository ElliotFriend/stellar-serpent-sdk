"""The emitter's dev-only execution rig: a wasmtime pinned to the chain's features.

**This is not an oracle** (ruling E1). It is a fast local loop that answers one
narrow question -- *do the bytes the emitter just produced compute what the
Python source said?* -- before a testnet round trip. `tests/real_host/`
re-proves everything against the real Soroban host. A green run here means
"the codegen is self-consistent", not "this contract is correct on chain"
(`spikes/spike1/harness.py:18-21`, verbatim).

Five modules:

* `engine` -- `make_config()` (the pinned feature set), the single host-call
  trampoline that masks every crossing between wasmtime's *signed* i64 and
  Soroban's *unsigned* `Val` words, `MiniHost`, and the two abort models
  (`HostError` for a contract that aborted with an error `Val`, `HostTrap` for
  an env.json "Traps if ..." precondition).
* `testmod` -- `build_test_module()`, a minimal hand-assembled module builder so
  the lowering tasks can execute function bodies before the real assembler
  exists. Test-only, deliberately simple, and never part of the emitter.
* `objects` -- `ObjectStore`: the object table, the vec/map/blob constructors,
  the three storage buckets, the `m.9` ascending-key panic, and the call log.
* `i256` -- `Wide256Host`: the 128/256-bit arithmetic oracle, written
  independently of the guest limb code it checks.
* `hostfns` -- `FullHost`: an `ObjectStore` that binds EVERY host function the
  compiler can emit, including `obj_cmp` (delegating to the tier-1 ordering
  oracle) and the recording event/auth/ledger surface. This is the one to
  instantiate for a whole-contract run.
"""
