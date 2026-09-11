# serpent

Write Soroban smart contracts in Python. **Experimental**: M1 is the
compiler-and-host-interface milestone; there is no PyPI release and no stable
API. Read [Getting started](getting-started.md) to build your first contract,
[The subset](subset.md) for exactly what compiles, and [Testing](testing.md)
before trusting a green run.

## What it is

- A **restricted-Python-subset → WASM compiler**, all Python: `@contract`
  classes with typed methods compile to a deployable Soroban module whose
  `contractspecv0` the stock `stellar` CLI renders and invokes unmodified.
- **Chain types that behave chain-exactly in plain Python** (`U32`, `I128`,
  `Symbol`, `Address`, `Vec`, `Map`, structs, tagged unions, int enums), so a
  contract's methods run as ordinary Python against an in-memory `Env` model
  with no build in the loop.
- **Four testing tiers**, from that model up to recorded testnet simulations,
  with the embedded real `soroban-env-host` as the gate.
- **A Stellar CLI plugin**: `stellar serpent build | inspect | doctor`.

## Honest boundary

Tier 1 and the mini host are models this repository wrote, not the chain.
Only the real host (`serpent.testing.RealEnv`) and recorded testnet fixtures
are evidence about the network. Named gaps -- frame rollback, minimum TTL
floors, container ordering, account authorizers, archival -- are listed in
[Testing](testing.md) and carried to M2.

## Where things are

| | |
|---|---|
| The eight examples | [Examples](examples/index.md), rendered from `examples/` |
| What compiles and what rejects | [The subset](subset.md), generated from the executable `must_reject/` spec |
| The CLI | [CLI reference](cli.md) |
| The public names | [API reference](api.md) |
| Contracts on testnet | [Deployments](deployments.md) |
| Design, decisions, plans | `docs/superpowers/` in the repository |
