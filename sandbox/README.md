# Sandbox

A scratch area for playing with serpent's compiler frontend. Nothing in here
is shipped, tested, or reviewed — expand, break, and rewrite freely.

## Quick start

From the repo root:

```sh
uv run python sandbox/compile.py sandbox/counter.py
```

That compiles the example contract, prints what the frontend produced (the
protocol floor, the host functions it imports, any guest-runtime parts the
emitter needs, and each exported function's signature), then builds and
writes a deployable `sandbox/counter.wasm` next to the source — validated
before it is written, ready for `stellar contract deploy --wasm
sandbox/counter.wasm ...`.

`Env`'s storage, events, TTL and auth all run for real in plain Python too,
at tier 1 (`from serpent.env import Env, deploy`): a contract's own methods
can be called directly against a tier-1 `Env` and their answers inspected,
with no WASM build in the loop at all. So the sandbox is not limited to
watching the compiler judge a contract — `examples/` (five worked contracts,
each one compiled to WASM and run at tier 1, with `tests/unit/test_examples.py`
asserting the two legs agree) is where to see both together end to end.

## Things to try

Edit `counter.py` (or copy it) and recompile after each change:

- **Legal expansions.** Add a `reset` method; add an `Address` owner param
  with `owner.require_auth()`; store a `@contracttype` struct instead of a
  bare `U32`; publish an event via `env.events().publish((Symbol("inc"),),
  total)`; add a module-level helper or a `_private` method and call it.
- **Illegal on purpose — the diagnostics are the point.** Each of these gets
  a located `SPT####` error with a rewrite that actually compiles:
  - `w = v` then `v.push_back(...)` — the alias guard (SPT1034), with the
    functional-host-op explanation in the note
  - `v[0:2]` — subscript slicing (SPT1013, pointing at `.slice(lo, hi)`)
  - `if my_vec:` — truthiness on a container (numeric chain types like
    `U32` ARE allowed truthiness; containers are not)
  - a 31-character parameter name, or a 33rd parameter (limits, SPT5xxx)
  - `Map(U32, U32, [(U32(1), a), (U32(1), b)])` — duplicate literal keys
  - two helpers that call each other (cycle rejection, SPT7005)
  - forgetting a `return` on one branch (definite-return flow, SPT7001)
- **The full catalog** of what compiles and what rejects, with a real
  counter-example per code, is `docs/subset.md`. The 95 files under
  `tests/must_reject/` are the minimal examples it is generated from.
- **The oracle.** The chain types behave chain-exactly in plain Python too:
  `uv run python -c "from serpent import U32; print(U32(2**32 - 1) + U32(1))"`
  raises the same checked-arithmetic error a contract would hit on chain.

## Notes

- Compiling a module executes its top level (the documented build-time
  trust boundary) — fine for your own code; don't point it at untrusted
  files.
- Reference contracts in the reviewed tree: `tests/fixtures/token_style.py`
  (events, errors, structs, auth) and `tests/fixtures/spike1_reauthored.py`
  (the Phase 0 contract that is live on testnet, re-authored in this
  surface).
- This directory is outside the four quality gates' mypy/pytest scope
  (`ruff check` does still lint it), so nothing here can break the build.
