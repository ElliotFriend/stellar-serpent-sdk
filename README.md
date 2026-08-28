# serpent

Write Soroban smart contracts in Python. Experimental.

## Status

M1 (the compiler + host-interface milestone) is in progress; this repo has no
release yet and no stable API. Phase 0 (the technical bet the project rests
on -- that a WAT-assembled guest can round-trip through the real Soroban host
and network) is testnet-proven: a hand-assembled counter contract deployed
and invoked correctly on Stellar testnet, byte-identical to the equivalent
Rust-SDK build where compared. See
`docs/superpowers/specs/2026-08-26-phase0-findings.md` for the verified
claims and evidence.

Nothing here should be treated as production-ready or API-stable. If you're
evaluating serpent for a real contract, read the spec and findings docs
below before writing anything you intend to keep.

## Architecture at a glance

serpent is layered from the ground up; each layer is usable (and tested) on
its own, and higher layers depend only on the ones below them:

- **Val codec** (`serpent/val.py`) -- the one place that packs/unpacks
  Soroban's tagged 64-bit `Val` representation. Every other layer that needs
  to cross the host boundary goes through here rather than reimplementing the
  bit layout.
- **Chain types** (`serpent/types/`) -- the Python-side value types a
  contract author writes against (`U32`, `I128`, `Address`, `Bytes32`,
  `Vec`, `Map`, ...), each knowing how to encode itself via the val codec.
- **Decorators** (`serpent/decorators.py`) -- `@contract`, `@contracttype`,
  `@contractevent`, `@contracterror`, and the `errorcode` field-declaration
  function (`LimitExceeded = errorcode(7)`, not a decorator): the authoring
  surface that attaches the metadata later layers consume.
- **Env surface** (`serpent/env.py`) -- the `Env`/`ChainValue`/`Event` API a
  contract method calls against for storage, events, and other host-mediated
  effects.
- **`_host` bindings** (`serpent/_host/`) -- the pinned, code-generated table
  of all 199 Soroban host functions (from a pinned `env.json`), with export
  codes, arities, and the protocol-gate logic used to compute a build's
  minimum required protocol.
- **Spec builders** (`serpent/spec/`) -- builds the three custom WASM
  sections (`contractenvmetav0`, `contractspecv0`, `contractmetav0`) from
  decorator metadata via `stellar_sdk`'s XDR classes; the only layer allowed
  a runtime dependency outside the standard library, and only as an install
  extra (`pip install serpent[spec]`), not a base dependency.
- **Emitter** (`serpent/emitter/`) -- lowers a compiled contract to a
  validated, deployable wasm32 module; `build_file`/`build_wasm` are its only
  public entry points, and every module they hand back has already passed an
  internal, dependency-free structural validator plus (when `wasm-tools` is
  on `PATH`) an external one, so an invalid module never reaches a caller.
  `serpent.emitter.printer.disassemble` renders any such module back to a
  reviewable, WAT-style text -- section headers, host calls by name -- for
  when a change to a lowering needs to be read rather than trusted.

Design rationale, the full milestone plan, and load-bearing facts (interface
counts, byte layouts, on-chain-verified constants) live in
`docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`.
Judgment calls made along the way, with context and reversal cost, are
recorded in `docs/superpowers/decisions.md`.
