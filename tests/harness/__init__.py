"""The emitter's dev-only execution rig: a wasmtime pinned to the chain's features.

**This is not an oracle** (ruling E1). It is a fast local loop that answers one
narrow question -- *do the bytes the emitter just produced compute what the
Python source said?* -- before a testnet round trip. Sub-plan F re-proves
everything against the real Soroban host; a green run here means the codegen is
self-consistent, not that a contract is correct on chain.

Two modules:

* `engine` -- `make_config()` (the pinned feature set), the single host-call
  trampoline that masks every crossing between wasmtime's *signed* i64 and
  Soroban's *unsigned* `Val` words, and `MiniHost`.
* `testmod` -- `build_test_module()`, a minimal hand-assembled module builder so
  the lowering tasks can execute function bodies before the real assembler
  exists. Test-only, deliberately simple, and never part of the emitter.
"""
