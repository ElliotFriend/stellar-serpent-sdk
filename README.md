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
  `@contractevent`, `@contracterror`, `@contractunion`, `@contractenum`, and
  the field-declaration functions that are not decorators: `errorcode`
  (`LimitExceeded = errorcode(7)`), `variant` (`Circle = variant(U32)`, in a
  `class Shape(ContractUnion)`) and `enumvalue` (`Red = enumvalue(0)`, in a
  `class Color(ContractEnum)`). Together they are the authoring surface that
  attaches the metadata later layers consume; `examples/shapes.py` is the
  worked contract for the last two.
- **Env surface** (`serpent/env.py`) -- the `Env`/`ChainValue`/`Event` API a
  contract method calls against for storage, events, TTL, ledger reads and
  auth. It ships with a deliberately minimal in-Python tier-1 model
  (`deploy`/`Env`) that runs a contract's own methods for real, with no WASM
  build in the loop, as a fast inner dev loop; the same contract also
  compiles and runs as WASM under `tests/harness`'s mini host, and
  `tests/unit/test_env_differential.py` checks the two agree on 62 stateful
  scenarios.
- **Examples** (`examples/`) -- six complete contracts (a counter, error
  codes, structs, events, an allowance-style token, tagged unions and int
  enums) exercising the authoring surface end to end; each one compiles,
  builds to WASM, and runs both at tier 1 and under the mini host in
  `tests/unit/test_examples.py`.
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

**Honest boundary.** The tier-1 `Env` and the mini host are two models this
repo wrote, not the chain: their agreement is self-consistency, not proof
against a real host. `ENV_SCENARIOS` (`tests/semantics/env_scenarios.py`) is
importable specifically so a real host can re-run the same corpus later and
turn that agreement into evidence. Named gaps neither model attempts today:
frame rollback, storage footprint, TTL clamp/trap at the exact chain-defined
bound, and a time-arithmetic algebra for `Timepoint`/`Duration` (bridge
through `to_u64()`/`from_u64()` in the meantime -- `examples/structs.py` has a
worked example).

## Tagged unions and int enums: the fence

`@contractunion` and `@contractenum` are the newest part of the authoring
surface, so the scope is worth stating exactly. What compiles today:

- a **unit** variant (`Empty = variant()`), a **single-payload** variant
  (`Circle = variant(U32)`), and a **multi-payload tuple** variant
  (`Rect = variant(U32, U32)`) up to **12** payload values -- the spec's
  tuple-arity cap, so serpent has one arity story rather than two;
- an int enum with **explicit u32 discriminants**, every number spelled
  (`Red = enumvalue(0)`).

A union is read in two steps, `tag()` then `payload(index, ty)`, and an int
enum by `==`. Both kinds cross the ABI, live in a struct field, and store under
any durability. Everything else is deliberately out, and each has a named thing
to write instead (every restriction here is additive to relax: accepts only
grow when one is lifted):

| Not available | Write this instead |
|---|---|
| `match` over a union | an `if`/`elif` chain over `tag()` -- the rewrite `SPT1024`'s own help text recommends |
| a named-field variant (permanent; Rust refuses it too) | a single-payload variant carrying a `@contracttype` struct |
| a 0-element tuple variant (permanent) | a unit variant, `variant()` |
| implicit int-enum discriminants (permanent) | spell the numbers, `enumvalue(N)` |
| a union as a **multi-entry** `Map` key (a union's container ordering is not modelled in tier 1) | a `@contracttype` key struct -- or keep the map to a single entry |
| an `Option` payload, `variant(X \| None)` (M2) | a unit variant for the absent case, `Nothing = variant()` |
| generic / parameterized unions (M2) | one concrete union per instantiation |
| `Option` narrowing, and `.value` introspection on an int enum (M2) | -- |
| a union as a cross-contract argument (M2) | -- (cross-contract calls are themselves M2) |

Design rationale, the full milestone plan, and load-bearing facts (interface
counts, byte layouts, on-chain-verified constants) live in
`docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`.
Judgment calls made along the way, with context and reversal cost, are
recorded in `docs/superpowers/decisions.md`.
