# serpent — a Python SDK for Soroban smart contracts

**Status:** approved design (2026-08-26)
**Owner:** Elliot Voris
**Codename:** `serpent` (import name `serpent`; PyPI/dist name decided at publication time — renaming is cheap until then)

## 1. Summary and goals

serpent lets developers author Soroban (Stellar) smart contracts in a typed, documented
subset of Python and compile them to real, deployable WASM. It aims for feature parity
with Rust's `soroban-sdk` over time, delivered in phases, at publishable quality:
comprehensive tests, real docs, CI.

Feasibility is proven: a spike compiler (pure Python, stdlib `ast`, hand-rolled WASM
encoder) produced contracts that deployed to testnet, with `contractspecv0` and
`contractenvmetav0` sections byte-identical to Rust SDK output, at roughly half the
Rust SDK's code size (344 B vs 654 B for a counter). The spike was throwaway; this
design incorporates the findings of an adversarial review of both the spike and the
original design.

**Goals**

- Real deployable WASM from Python source; success = Python-authored contracts running
  on testnet/mainnet, inspectable by standard Stellar tooling.
- Pythonic authoring with zero-plugin IDE support (chain types are real Python classes).
- A testing story at least as good as Rust `soroban-sdk`'s testutils.
- Interop with the existing ecosystem: `stellar` CLI (as a plugin), Stellar Lab,
  `stellar-contract-bindings`, `stellar_sdk` (Python) for RPC/XDR.

**Non-goals**

- Compiling arbitrary Python. serpent is a Python-*shaped* language (the Vyper lesson):
  the subset is a specification, and the compiler rejects everything outside it with
  source-located errors rather than approximating.
- Re-implementing what the ecosystem provides: client bindings generation
  (`stellar-contract-bindings`), XDR codegen (`stellar_sdk.xdr`), deploy/invoke
  (`stellar contract deploy/invoke`).
- Gas/budget-accurate local execution in the fast dev loop (only the real-host tier
  meters; see §8).

## 2. Authoring model

Contracts are Python modules using chain types and decorators from `serpent`:

```python
from serpent import contract, contracttype, contracterror, Env, U32, I128, Symbol, Address, Vec

@contracterror
class Error:
    InsufficientBalance = 1
    Unauthorized = 2

@contracttype
class State:              # named-field struct → Map<Symbol, V>
    owner: Address
    total_supply: I128    # >9-char field names REQUIRE linear memory (see §5)

@contract
class Token:
    def __init__(env: Env, admin: Address) -> None:
        ...               # compiled to the reserved `__constructor` export

    def transfer(env: Env, from_: Address, to: Address, amount: I128) -> None:
        from_.require_auth()
        if balance(env, from_) < amount:
            raise Error.InsufficientBalance
        ...
```

Rules:

- **Chain types** are real Python classes with operator overloading, so contracts
  type-check in any IDE with no plugins. M1 types: `Bool`, `U32`, `I32`, `U64`, `I64`,
  `U128`, `I128`, `Symbol`, `Bytes`, `BytesN[N]`, `String`, `Vec[T]`, `Map[K, V]`,
  `Address`, `Timepoint`, `Duration`, `Void` (as `None` return). Later: `U256`, `I256`,
  `MuxedAddress`, `Val` (escape hatch — but see the `Error` rule below).
- Plain `int`/`str`/`bool` literals coerce to the annotated chain type with
  compile-time bounds checks. Unbounded `int` arithmetic is rejected.
- **Exported signatures require annotations.** Docstrings flow into `contractspecv0`
  doc fields (verified wiring, not aspiration — the spike did not do this).
- **`__init__` compiles to `__constructor`** (host-reserved name, protocol ≥ 22).
  Documented caveat: the host *launders* constructor errors — any recoverable error
  raised in the constructor reaches the deployer as `Context(InvalidAction)`, not the
  user's error code (`lifecycle.rs`). The docs must say so, prominently, because Python
  developers will expect `__init__` exception semantics.
- **`raise MyError.X`** compiles to the code-preserving form: the `Error` Val is
  `(code << 32) | 3` (tag 3, minor = `ScErrorType::Contract` = 0), delivered via
  `fail_with_error` (`x.5`) or an escalated return. Never a bare `unreachable` — that
  collapses every error into one opaque `WasmVm(InvalidAction)`.
- **`Error` is never a returnable value.** The host escalates `Ok(Error)` at frame exit
  unconditionally. The type system must not admit `Error` (or `Val` known to hold one)
  as a function return type.
- **User-defined types** follow Soroban spec conventions: named-field struct →
  `Map<Symbol, V>`; tuple struct → `Vec<V>`; tagged union → `Vec` led by variant-name
  `Symbol`; int enum → `u32`; `@contracterror` → `u32` codes under `SCE_CONTRACT`.
- **Events**: `@contractevent` classes (mirroring Rust's), emitted via `contract_event`
  (`x.1`). Convention enforced: `topic[0]` is a short `Symbol` event name — the host
  does not enforce a topic-count limit (the binding constraint is the event-bytes
  network setting), but indexers/RPC filtering assume Symbol-first topics.
- **Name rules**: single-underscore Python privates are fine (the host reserves only
  `__`-prefixed names, and only as a *call-time* rule). Spec XDR limits validated at
  compile time with source-located errors: function/field names ≤ 30 chars, type names
  ≤ 60, docs ≤ 1024, tuple arity ≤ 12.
- **Type mapping decisions**: `X | None` → `SC_SPEC_TYPE_OPTION`; `tuple[...]` →
  `TUPLE`; `Result` has no Python analogue — not exposed in M1 (functions either return
  a value or raise).

The subset is defined by an **executable specification**: `tests/must_reject/*.py`,
each file annotated with its expected source-located error; the documentation's
"unsupported constructs" table is generated from that directory. Constructs users will
inevitably try (`for x in vec`, comprehensions, f-strings, `try/except`, closures,
default/keyword args, `len()`, slicing) each get either support or a *good* error.
This diagnostics long tail is a first-class, ongoing work item, not a footnote.

## 3. Package layout

uv workspace, src layout, Python ≥ 3.11:

```
pyproject.toml            # workspace root; uv_build backend
src/serpent/
├── val.py                # THE Val codec — single shared implementation (§10)
├── types/                # chain-type classes (operator overloading, IDE-facing)
├── env/                  # Env: storage (3 tiers + TTL), events, auth, ledger, logging
├── _host/                # generated host bindings + codegen script + pinned env.json
├── compiler/
│   ├── frontend.py       # ast → resolved, typed IR (name resolution, type check, diagnostics)
│   ├── ir.py             # small typed IR
│   ├── emitter.py        # IR → WASM (operand-stack-validated; real `return`s)
│   ├── runtime/          # pre-assembled .wat runtime library (§6)
│   ├── specgen.py        # contractspecv0/contractenvmetav0/contractmetav0 via stellar_sdk XDR
│   └── layout.py         # data-section layout, string/symbol pooling, scratch region
├── testing/              # test harnesses (§8), pytest fixtures
└── cli.py                # `stellar-serpent` console script (§9)
examples/                 # workspace members: counter, events, errors, structs, token…
tests/
├── unit/                 # codec round-trips, golden XDR bytes, emitter validation
├── must_reject/          # executable subset spec
├── contracts/            # tier-2 contract tests
└── integration/          # tier-3, opt-in (network)
docs/                     # mkdocs-material; subset spec; API reference
```

## 4. Compiler pipeline

```
source → ast.parse → frontend (imports, name resolution, type check; every error
carries file:line:col) → typed IR → emitter (WASM binary, hand-rolled encoder) →
link runtime library parts on demand → attach custom sections → validate → artifact
```

- **Emitter discipline (adversarial-review B4):** the emitter maintains an abstract
  operand stack and asserts at every control-flow merge; `return` emits the real
  opcode 0x0F; validation runs *inside* the compiler (internal validator, plus
  `wasm-tools validate` when available) so an invalid module is a compile error, never
  an output file. The spike silently emitted invalid WASM for early returns, missing
  returns, and >9-char symbols; that class of bug must be structurally impossible.
- **ABI prologue (M5):** every exported function begins with per-argument tag-and-range
  checks (failing with a distinct error code); every host-call return typed narrower
  than `Val` is checked the same way. This costs size/CPU and is non-negotiable —
  the spike computed `add(Symbol('hello')) → 45`.
- **Checked arithmetic (M6):** WASM integer arithmetic wraps silently; serpent emits
  explicit overflow checks on chain-type arithmetic, routed to `fail_with_error` with a
  distinct `ArithmeticOverflow` contract-error code — better diagnostics than the Rust
  SDK's information-free trap. Documented, with the size/CPU cost acknowledged.
- **Host bindings** are generated from a pinned `env.json` (pinned by `rs-soroban-env`
  git SHA, checked in). Export codes are append-only stable; the generator keys by name
  for readability. Each binding records `min/max_supported_protocol`.
- **Declared protocol is computed, never hand-set (M9):**
  `env_meta.protocol = max(min_supported_protocol of imports actually emitted)`, with a
  floor of the oldest supported target. Importing a function gated above the build's
  target network protocol is a source-located compile error naming the function. Users
  may raise the declared protocol, never lower it below the computed floor.
- **Validation gate:** `wasm-tools validate
  --features=-all,mutable-global,sign-extension,bulk-memory` (exactly the host's wasmi
  config) as fast pre-check; the *release* gate is instantiation under a real host
  (§8), because `wasm-tools` cannot catch nonexistent imports, protocol gating, or a
  missing `memory` export.
- **Golden tests:** spec + env-meta sections byte-compared against Rust SDK artifacts
  (proven achievable); `contractmetav0` and code sections are never byte-compared
  (meaningless across toolchains).

## 5. Linear memory (required in M1)

Adversarial review B2 killed "no linear memory": Symbols > 9 chars (struct field names,
cross-contract function names like `transfer_from`, event names), string/bytes
literals, logging (`log_from_linear_memory` is the only logging path), and efficient
Vec/Map bulk construction all need it. The workarounds cost ~30× CPU per use, paid by
every caller forever.

- The emitter lays out a **data section** (pooled string/symbol/bytes literals) and a
  **scratch/bump region**; no allocator beyond bump-reset-per-entry is needed (contract
  invocations are short-lived).
- **Build-time assertion (M13):** if any linear-memory host function is imported, the
  module must declare exactly one memory and export it under the literal name
  `memory`. The host resolves the memory export *lazily* — a contract missing it
  deploys fine and fails in production on the first string-touching path.
- Contracts that genuinely need no memory (counter-class) still compile memoryless —
  the size win stays.

## 6. Guest runtime library (pre-assembled WAT)

Small hand-written WASM function bodies, assembled once, linked into output on demand
(the AssemblyScript stub-runtime pattern). Contents:

- **i128 arithmetic (B1):** add/sub/mul/compare with overflow checks as guest code
  (~200–400 B; WASM has no 64×64→128 multiply, so mul synthesizes from four 32×32→64).
  There are **no 128-bit host functions** — Rust gets i128 from LLVM. Routing through
  i256 host objects costs ~200–280× native (≈ 3,000–4,500 CPU units/op vs 16) and is
  used only for div/rem, where guest code is largest and the host call count lowest.
  **Never use `i64.mul_wide_s`** (wasmtime supports it; chain wasmi 0.31 does not).
- Tag-check prologue helpers, overflow-check helpers, small-val boxing/unboxing.

Each runtime part is independently unit-tested (golden vectors against Rust/native
results) and differential-tested against the real host.

## 7. Custom sections

Emitted via `stellar_sdk.xdr` classes (v15+; complete `SCSpecEntry`/`SCEnvMetaEntry`/
`SCMetaEntry` coverage), with golden-byte regression tests pinned so a `stellar_sdk`
upgrade cannot silently change output (the byte-compat proof was made with hand-rolled
XDR; switching to `stellar_sdk` is a change and must re-prove against the goldens):

- `contractenvmetav0` (required): protocol computed per §4; `preRelease = 0` (exact-
  match rule documented).
- `contractspecv0`: concatenated `SCSpecEntry` stream — functions (docstrings wired
  into doc fields), structs, unions, enums, error enums, events.
- `contractmetav0`: `name`, `version`, `serpentver`, plus user pairs. Note: readers
  must concatenate all same-named custom sections (Rust emits two meta sections).

## 8. Testing architecture

Four tiers; the **real host is the release gate**, per adversarial review M7 (a
hand-written mock of ~9k LOC of host semantics — auth trees that consume storage-
written nonces, non-recoverable footprint errors, frame-rollback of events, TTL
asymmetries, instance-storage flush rules — has *silent false green* as its failure
mode).

1. **Tier 1 — pure unit tests** (fast, no WASM): Val codec round-trips (Hypothesis +
   golden Rust-produced Vals), symbol packing, XDR goldens, emitter operand-stack
   validation, `must_reject/` diagnostics.
2. **Tier 2a — fast dev loop** (wasmtime-py + Python mini-host): compile-in-test, run
   the same bytes that would deploy. Explicitly **lower fidelity**: no budget metering,
   simplified auth (`mock_all_auths` semantics only), and **mandatory footprint
   recording** (tests declare expected footprints; silent passes are not allowed).
   wasmtime `Config` pinned to mirror the chain feature set exactly, with a test that
   fails if a wasmtime upgrade flips a default. Every boundary crossing masks to u64
   (wasmtime returns signed i64 — the spike emulator's live bug).
   Realistic throughput: engine/module cached across tests; hundreds to low thousands
   of contract tests/sec (not the 19k/s hot-loop microbenchmark).
3. **Tier 2b — real host (authoritative):** time-boxed spike embedding
   `soroban-env-host` via PyO3 (`serpent[testing]` extra with prebuilt wheels). If it
   works, it deletes the entire mock-fidelity problem — auth, footprint, comparison
   ordering, TTL, budget, engine parity — and is how the Rust SDK itself tests.
   Fallback if the spike disappoints: Docker quickstart (`stellar container start`)
   driven over RPC as the gate.
4. **Tier 3 — on-chain integration** (opt-in, testnet via `stellar_sdk` RPC):
   differential runs — same bytes, tier 2 vs chain, divergence is a release blocker.

## 9. CLI: Stellar CLI plugin

Ship a `stellar-serpent` console-script entry point (`uv tool install` puts it on
PATH), so the Stellar CLI's plugin discovery exposes:

```
stellar serpent build path/to/contract.py [--out …] [--meta k=v]
stellar serpent inspect artifact.wasm       # sections, spec, computed protocol
stellar serpent doctor                      # toolchain/env checks
```

`stellar contract build` is a **built-in** and built-ins win before plugin lookup, so
we take our own namespace (the same choice `stellar-contract-bindings` made). Deploy /
invoke / bindings are **not** rebuilt: stock `stellar contract deploy|invoke` and
`stellar-contract-bindings python` work on serpent output unmodified (proven — the CLI
already renders our spec as a typed interface). Register the repo with the
`stellar-plugin` GitHub topic at publication.

## 10. Cross-cutting rule: one Val codec

The single highest-risk internal drift is between the chain-type classes' *Python
runtime behavior* and the compiler's *emitted WASM behavior* — two implementations of
one semantics. Mitigations:

- `serpent/val.py` is the only Val encode/decode implementation; the type classes,
  the compiler, the mini-host, and the test harnesses all import it.
- Semantics tests run **the same table of cases** against (a) the Python classes and
  (b) compiled WASM in tier 2, asserting identical results — including overflow,
  bounds, and error codes.

## 11. Milestones (honest sizing: M1 ≈ 3–6 months of focused work)

**Phase 0 — re-spike in the designed style (gate for everything else).**
One `@contract` class end-to-end: annotated method, `env.storage().instance()` chain,
`@contracttype` struct with a >9-char field (forces memory), `raise Error.X` with the
code verified at RPC, compiled, deployed, and **invoked** on testnet (the original
spike's invocations were never independently verified). Plus the PyO3 tier-2b spike.
Output: go/no-go facts, not kept code.

**M1 — core SDK.** Everything in §2–§10 for the M1 type set: storage tiers + TTL,
`require_auth`/`require_auth_for_args`, events, errors, structs/unions/enums, the
runtime library, testing tiers 1–2, CLI build/inspect, examples (counter, events,
errors, structs, allowance-style token without cross-contract), docs site, CI. Ends
with a deliberate, user-approved testnet deployment.

**M2 — reach.** Cross-contract calls (`call`/`try_call` + typed client stubs consuming
`contractspecv0`, interop with `stellar-contract-bindings`), crypto host functions,
PRNG, deployer, TTL helpers, full SEP-41 token example, U256/I256.

**M3 — ecosystem polish.** Differential CI against Rust SDK contracts, fuzz corpus,
tier-3 suite, docs completeness, PyPI release readiness (name decision), plugin-topic
registration.

## 12. Risks and maintenance

- **Tier-2 fidelity drift** — mitigated by making the real host the gate (tier 2b) and
  differential tests; any divergence is a release blocker.
- **env.json / protocol churn** — pinned by SHA; CI diffs against upstream `main` so
  protocol bumps surface as failing diffs; per-function protocol gates encoded in the
  generated bindings; recurring per-protocol work is budgeted.
- **`stellar_sdk` XDR coupling** — version floor and ceiling pinned; golden bytes for
  all three sections.
- **wasmtime-py monthly majors** — exact pin + feature-set assertion test.
- **Subset/docs/compiler drift** — `must_reject/` is executable and generates docs.
- **Scope creep toward "real Python"** — the subset spec is the contract; rejections
  are features.
- **Effort** — M1 is months, not weeks; every increment ships something usable.

## 13. Appendix: load-bearing facts (verified 2026-08-26)

- Val: 64-bit; low 8 bits tag; body 56 bits; major/minor split 32/24. Tags: False 0,
  True 1, Void 2, Error 3, U32 4, I32 5, U64Small 6, I64Small 7, TimepointSmall 8,
  DurationSmall 9, U128Small 10, I128Small 11, U256Small 12, I256Small 13,
  SymbolSmall 14; objects 64–79 (U64 64, I64 65, Timepoint 66, Duration 67, U128 68,
  I128 69, U256 70, I256 71, Bytes 72, String 73, Symbol 74, Vec 75, Map 76,
  Address 77, MuxedAddress 78, ExecutableTag 79); Bad 0x7f.
- SymbolSmall: ≤ 9 chars, 6 bits each, high-order-first, zero-padded high bits
  (deliberate, for ULEB128 literal size); charset `_`=1, `0-9`=2…, `A-Z`=12…,
  `a-z`=38…; `SCSYMBOL_LIMIT` = 32.
- Error Val: `(code << 32) | (type << 8) | 3`; contract errors have type 0, so
  `(code << 32) | 3`. `fail_with_error` accepts only `ScErrorType::Contract`.
- Contract max size: **131072 bytes** (65536 is the *data-entry* limit — different).
- Feature gate (== host wasmi config): bulk-memory, mutable-global, sign-extension ON;
  floats, SIMD, multi-value, reference-types, tail-call, extended-const, threads,
  memory64 OFF. No start section. One memory, one table (≤1000 elements). Import
  symbol names ≤ 10 chars. All exports ≤ 32 params and ≤ 32 results (entry points
  return exactly one i64).
- Host interface: 199 fns, 11 modules (x10 i52 m14 v19 l21 d2 b26 c37 a12 t2 p4);
  export codes append-only stable; six raw-scalar (non-Val) interface types: u64, i64,
  u32, i32, StorageType (Temporary=0 Persistent=1 Instance=2, bare u64),
  ContractTtlExtension.
- Cost model: WasmInsnExec 4/instr; DispatchHostFunction 295; VisitObject 60 — one
  host call ≈ 74 instructions of fixed overhead.
- Reserved names: `__` prefix, call-time rule only; `__constructor` (protocol ≥ 22;
  errors laundered to `Context(InvalidAction)`; must return void; 0-arg constructor
  may be absent; args-without-constructor is an error).
- Instance storage: not a durability — a sub-map in the instance entry, one shared
  TTL, flushed at frame exit with early flush on re-entrant self-call.
- TTL: persistent extension past max **clamps**, temporary **traps**; live-until
  arithmetic carries `-1`; extensions never reduce; extending a dead entry errors.
- Events roll back with failed frames. Footprint violations are `Storage(ExceededLimit)`
  and **non-recoverable** (uncatchable via `try_call`).
- Networks (2026-08-26): mainnet + testnet on protocol 27; protocol 28 "Adapter" vote
  scheduled 2026-09-16. Pinned `env.json` is v28.0.2 — one ahead; guard via computed
  protocol floor.
- Spike testnet artifacts (throwaway evidence, not kept code):
  counter `CC3CUV2D6DBBAI5C4ZG44J46RPXMQVQWHM4GYR63VCKSESFS25DVFPXV` (344 B),
  add/sum_to/gcd `CBLOHCDAO4OZTGCDYWUVTKIIQDGKB2VVVQVXOMSSGVF3S3AMOLHBOZCF` (511 B).
