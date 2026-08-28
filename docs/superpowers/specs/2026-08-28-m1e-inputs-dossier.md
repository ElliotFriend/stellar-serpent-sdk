# M1-E DESIGN-INPUTS DOSSIER — `Env` runtime semantics + the five example contracts

> Compiled by a research agent 2026-08-28. Format model: `docs/superpowers/specs/2026-08-27-m1d-inputs-dossier.md`. This document is the citation target for the M1-E plan. **Nothing here is a ruling** — §E poses the questions the controller rules on; every §A/§B line is either a frozen input or a proposal explicitly marked as one.
>
> Repo root: `/Users/elliotvoris/Dev/stellar/sdk/py-soroban`. Branch at compile time: `m1d-emitter` (M1-D complete pending final review; its code is treated as landed). Absolute paths throughout.

Sources read in full: the M1-D inputs dossier (all 622 lines); the M1-C inputs dossier §C.4 (the Env-API recognition table C froze); spec §2/§3/§8/§11/§12/§13; the M1 roadmap; `decisions.md` (all 20 entries **[verified live: `grep -c "^## 20"` == 20]**); `.superpowers/sdd/2026-08-27-m1c-compiler-frontend/final-review-attention.md`; `.superpowers/sdd/2026-08-27-m1d-emitter/final-review-attention.md`; `src/serpent/env.py` (all 217 lines); `src/serpent/types/address.py`; `src/serpent/decorators.py` (event/struct/contract paths); `src/serpent/spec/sections.py`; `src/serpent/emitter/__init__.py`; `src/serpent/compiler/recognize.py`; `tests/harness/*`; `tests/fixtures/*`; `tests/semantics/cases.py`; `docs/subset.md`; `pyproject.toml`; `.github/workflows/ci.yml`.

Live verification performed (read-only): `uv run python` against the installed `stellar_sdk` 15.0.0 XDR classes for `SCSpecEventV0` / `SCSpecEventParamV0` / `SCSpecEventParamLocationV0` / `SCSpecEventDataFormat` / `SCSpecEntryKind`, including the constructors' own length caps (§B.4); a DeepWiki query against `stellar/rs-soroban-sdk` for the Rust `#[contractevent]` convention (§E2); a live import of `tests/semantics/cases.py` for the case counts (§D.1); `src/serpent/errors.py:65-80` for `ContractError.code: ClassVar[int]` and its `__init_subclass__` enforcement (§E8); `grep -c "__slots__" src/serpent/env.py` == 9 (§F.1.14). Facts so obtained are marked **[verified live]**.

**Provenance discipline used throughout:** shipped code and `decisions.md` win over older docs; a claim taken from a docstring is quoted rather than paraphrased; and where an earlier document and the tree disagree, the disagreement is stated rather than resolved silently (three such cases: `sandbox/README.md:17-20` is stale about M1-D; spec §3 names an `env/` package and an `examples/` directory that do not exist; and the M1-C attention file's "the token_style E12 amendment reverts" describes a decision, not an edit already written down — see §B.4 Layer 4).

---

## A. FROZEN INPUTS

### A.1 Spec obligations (`/Users/elliotvoris/Dev/stellar/sdk/py-soroban/docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`)

| ID | Constraint | Source |
|---|---|---|
| S1 | **Package layout names an `env/` PACKAGE and a `testing/` package**, verbatim: `env/  # Env: storage (3 tiers + TTL), events, auth, ledger, logging` and `testing/  # test harnesses (§8), pytest fixtures`. **Today `env` is a single module (`src/serpent/env.py`, 217 lines) and `serpent/testing/` does not exist.** The layout also names `examples/  # workspace members: counter, events, errors, structs, token…` — **`examples/` does not exist today either.** | §3:134–161 |
| S2 | **`logging` is inside the spec's `env/` bullet** but has no authored surface today: `env.py` declares `storage()`, `ledger()`, `events()` and nothing else. The M1-C dossier's §C.4 lists `env.logs()` (`log_from_linear_memory`, `x._`) under "**Recognized but not lowerable in M1-C** … all M2 (spec §11)". | §3:143; M1-C dossier:353 |
| S3 | **Testing tiers, verbatim on tier 1**: "**Tier 1 — pure unit tests** (fast, no WASM): Val codec round-trips (Hypothesis + golden Rust-produced Vals), symbol packing, XDR goldens, emitter operand-stack validation, `must_reject/` diagnostics." **Note what is absent: the spec's tier 1 does NOT include "execute a contract as plain Python".** §E1 turns on this. | §8:258–260 |
| S4 | **Tier 2a is the spec's "fast dev loop"**, verbatim: "**Tier 2a — fast dev loop** (wasmtime-py + Python mini-host): compile-in-test, run the same bytes that would deploy. Explicitly **lower fidelity**: no budget metering, simplified auth (`mock_all_auths` semantics only), and **mandatory footprint recording** (tests declare expected footprints; silent passes are not allowed)." | §8:261–269 |
| S5 | **The real host is the release gate**, and the reason is named as *this* dossier's central risk: "a hand-written mock of ~9k LOC of host semantics — **auth trees that consume storage-written nonces, non-recoverable footprint errors, frame-rollback of events, TTL asymmetries, instance-storage flush rules** — has *silent false green* as its failure mode." Every item in that list is something E's Env model would have to model or honestly refuse. | §8:250–256 |
| S6 | **M1's scope sentence names E's deliverables**: "storage tiers + TTL, `require_auth`/`require_auth_for_args`, events, errors, structs/unions/enums, the runtime library, testing tiers 1–2, CLI build/inspect, **examples (counter, events, errors, structs, allowance-style token without cross-contract)**, docs site, CI." **"allowance-style token WITHOUT cross-contract" is the spec's own wording** — no SAC/SEP-41 client calls. | §11:318–322 |
| S7 | **Instance storage is not a durability**: "a sub-map in the instance entry, one shared TTL, **flushed at frame exit with early flush on re-entrant self-call**." | §13:375–376 |
| S8 | **TTL rules, verbatim and complete**: "TTL: persistent extension past max **clamps**, temporary **traps**; live-until arithmetic carries `-1`; **extensions never reduce**; **extending a dead entry errors**." | §13:377–378 |
| S9 | **Events roll back with failed frames.** "Footprint violations are `Storage(ExceededLimit)` and **non-recoverable** (uncatchable via `try_call`)." | §13:379–380 |
| S10 | **Events authoring convention**: "`@contractevent` classes (mirroring Rust's), emitted via `contract_event` (`x.1`). Convention enforced: **`topic[0]` is a short `Symbol` event name** — the host does not enforce a topic-count limit (the binding constraint is the **event-bytes network setting**), but indexers/RPC filtering assume Symbol-first topics." | §2:115–118 |
| S11 | **`raise MyError.X`** → `(code << 32) | 3` via `fail_with_error`; **`Error` is never a returnable value** (the host escalates `Ok(Error)` at frame exit unconditionally); the type system must not admit it as a return type. | §2:95–101 |
| S12 | **`__init__` → `__constructor`**, with the documented caveat that "the host *launders* constructor errors — any recoverable error raised in the constructor reaches the deployer as `Context(InvalidAction)`, not the user's error code (`lifecycle.rs`). **The docs must say so, prominently**, because Python developers will expect `__init__` exception semantics." Protocol ≥ 22; must return void; 0-arg constructor may be absent; args-without-constructor is an error. | §2:90–94; §13:372–374 |
| S13 | **One Val codec / the drift rule**: "the single highest-risk internal drift is between the chain-type classes' *Python runtime behavior* and the compiler's *emitted WASM behavior*." Semantics tests run the same table against the Python classes and compiled WASM in tier 2, "asserting identical results — including overflow, bounds, and error codes". **E adds a THIRD implementation of host semantics (the Env model) unless §E1 says otherwise — this is the risk E is born with.** | §10:297–307 |
| S14 | **Chain-type surface for time**: `Timepoint`, `Duration` are in the M1 type set. Nothing in §2/§13 grants them arithmetic. | §2:72–78 |
| S15 | **Cost model / feature gate / max size** unchanged from D: one host call ≈ 74 instructions; contract max 131072 bytes; exports return exactly one i64. | §13:360–371 |
| S16 | **Subset is an executable specification**: `tests/must_reject/*.py`, each annotated with its expected source-located error; "the documentation's 'unsupported constructs' table is **generated from that directory**". Any authoring-surface change E makes (e.g. accepting `Event.publish(env)`) must move a `must_reject` fixture, not just a checker. | §2:127–132 |
| S17 | **M2 boundary, explicit**: "**M2 — reach.** Cross-contract calls … crypto host functions, PRNG, deployer, **TTL helpers**, full SEP-41 token example, U256/I256." "TTL helpers" being M2 is the sharpest scope line for §E4: **`extend_ttl` itself is M1 (S6), the *helpers* are M2.** | §11:324–326 |
| S18 | **Risk register lines E inherits**: "**Tier-2 fidelity drift** — mitigated by making the real host the gate (tier 2b) and differential tests; any divergence is a release blocker"; "**Subset/docs/compiler drift** — `must_reject/` is executable and generates docs"; "**Scope creep toward 'real Python'** — the subset spec is the contract; rejections are features." | §12:332–345 |

### A.2 Roadmap standing constraints (`/Users/elliotvoris/Dev/stellar/sdk/py-soroban/docs/superpowers/plans/2026-08-26-m1-roadmap.md`)

| ID | Constraint | Source |
|---|---|---|
| R1 | **Row E produces exactly**, verbatim: "the authored `serpent` package's `Env` (storage tiers/TTL, `require_auth(_for_args)`, events, ledger) **wired end-to-end**; example contracts (counter, events, errors, structs, allowance-token-no-xcontract) **compiled and passing**". | line 22 |
| R2 | **E's consumers are F and G.** F is "testing tiers": "tier-2a productized (pytest fixtures, feature-set assertion test, footprint recording); tier-2b `serpent-host` PyO3 package …; differential runner (2a vs 2b vs testnet = release gate)". G is CLI + docs site + CI + **the M1 gate: user-approved testnet deploy of an example** — i.e. **one of E's five examples is what gets deployed at M1's end**. | line 22; rows F/G lines 23–24 |
| R3 | **`serpent.testing` belongs to F, not E** — row F's deliverable is "tier-2a **productized** (pytest fixtures …)" and spec §3 puts `testing/` in the package. E must not squat that namespace with a half-productized fixture set; §E1 turns on this. | line 23; S1 |
| R4 | **A→D are sequential, E onward partially parallelizable**: "Order is dependency-driven; A→D are sequential, **E onward partially parallelizable**." E is therefore the first sub-plan whose successors can start before it finishes — so anything E leaves ambiguous for F/G is a *concurrent* hazard, not a later one. | lines 13–14 |
| R5 | Standing constraints verbatim: "single Val codec; validate-inside-compiler; error codes never lost to `unreachable`; `self`-first methods; exception-class errors; **pre-validate at every nominally-fallible soroban-sdk boundary**; `relaxed_simd`-before-`simd`; balance checks at `ret()`; **pinned toolchain versions with drift-detection tests**; adversarial review before execution; SDD with task-scoped reviews." | lines 26–32 |
| R6 | **Spike disposition**: `spikes/` stays read-only reference "until sub-plan D supersedes the emitter **and F supersedes the harnesses**, then a cleanup task in G decides retain-vs-remove with the user." **D has now superseded the emitter; the harnesses are F's to supersede, not E's.** | lines 34–36 |

### A.3 Decision-log rulings that bind E (`/Users/elliotvoris/Dev/stellar/sdk/py-soroban/docs/superpowers/decisions.md`)

| ID | Ruling | Source |
|---|---|---|
| D1 | Sub-plans B–G are authored, reviewed, executed and merged **without per-phase sign-off**; every judgment call lands in `decisions.md` in the same commit series. Hard stops: irreversible/outward actions and the **user-approved testnet deploy at M1's end**. | lines 19–29 |
| D2 | **`Timepoint`/`Duration` have NO arithmetic, and the time algebra is E's decision** — verbatim: "disable ALL arithmetic on Timepoint/Duration (TypeError naming the omission and pointing at the to_u64/from_u64 bridges). **Deliberate time algebra (Duration+Duration, Timepoint-Timepoint→Duration) is a sub-plan E decision.**" Also from the same entry: "**Reversal cost: trivial (re-enable ops); reverse direction would break contracts**" — i.e. adding ops in E is the cheap direction, removing them later is not. | lines 56–67 |
| D3 | **Storage keys are any chain value**, `ledger().timestamp() -> U64` ("Rust parity; Timepoint bridge exists for opting in"), `has() -> Bool`, `@contract` rejects static/classmethods. | lines 87–95 |
| D4 | **Event authoring form**: events inherit a `serpent.Event` base declaring `publish(env)` "(**still NotImplementedError until sub-plan E**); `@contractevent` validates the base is present. Also: **event topics are heterogeneous (chain-value tuple), not `Vec[Symbol]`** — canonical token topics are `(Symbol, Address, Address)`." Reversal cost: "authoring-surface change — cheap now, **breaking after docs/examples**." **E ships the docs and examples, so E is the last cheap moment.** | lines 97–106 |
| D5 | **`Event.publish(env)` REJECTED in M1-C, pointing at sub-plan E**, verbatim: "`Event.publish(env)` REJECTED in M1-C pointing at sub-plan E, **`env.events().publish()` is the supported form**, and `tests/fixtures/token_style.py` is **AMENDED accordingly** (E12)". And in the same entry's reversal-cost line: "**the fixture amendment (E12) is user-visible and trivially revertable when E lands.**" | lines 141–143, 160 |
| D6 | **Struct storage keys allowed with an explicit "not modelled in tier 1" ordering note** (M1-C E3); **Map struct VALUES supported at tier-1 runtime** ("require_chain_value widened on the value path only; **keys stay per E3**"). E's Env model inherits that hole: a struct-keyed storage map has no tier-1 ordering answer. | lines 152–155, 172–175 |
| D7 | **Ownership is flow-insensitive per function.** "a container iterated by a for loop may not be mutated anywhere in the function (the hidden iterator handle is an alias)"; mutation-then-alias rejects even in straight-line code (SPT1034 with a rewrite). **Reversal cost: "relaxing to region-sensitivity later is additive (accepts strictly grow)"** — the converse, *narrowing* accepts, is what §E5 is about. | lines 199–214 |
| D8 | **`compile_module` outputs two host-fn sets, floor over reachable**; `runtime_parts_needed` names were C-coined pending D's ratification (now ratified/renamed by the M1-D rulings). | lines 216–229 |
| D9 | **Registry discipline is "no renumber, no delete, no meaning reversal" — not "no edits".** Sanctioned edits are enumerated per pass; a code that becomes reachable "just gets its fixture and leaves the allowlist" (`NO_FIXTURE_ALLOWLIST`). **E flipping `Event.publish(env)` from reject to accept is exactly a `NO_FIXTURE_ALLOWLIST`-shaped move on that code's row.** | lines 182–197, 231–246 |
| D10 | **`compile_module` is the single public compiler entry point**; no expression-level API ships in M1. | lines 248–258 |
| D11 | **M1-D rulings (all 16 dossier recommendations adopted), the E-relevant ones:** the mini-host is dev-only, ported by copy from `spikes/spike1/harness.py`, and **is NOT an oracle** — "F re-runs the same table on tier-2b (named carried obligation to F)"; runtime parts emitted from the same Python encoder; **D writes the COMPUTED FLOOR into `contractenvmetav0`**; determinism is a tested guarantee; `serpent.emitter` is not in `serpent.__all__`. | lines 260–336 |
| D12 | **M1-D plan-review rulings**, E-relevant: **three licensed frontend edits landed inside D's commit series** (runtime parts, the E13 storage-get names, the B6 Address-literal inventory) — establishing the precedent that **a sub-plan may edit the frontend when its own lowering needs it, with a pinning test, recorded as a ruling**. §E2 and §E5 both need that precedent. Also: "**128-bit //0 and %0 surface as host ScError, not a wasm trap**". | lines 338–380 |

### A.4 Obligations carried INTO E, verbatim from the two attention files

`/Users/elliotvoris/Dev/stellar/sdk/py-soroban/.superpowers/sdd/2026-08-27-m1c-compiler-frontend/final-review-attention.md:29-33`, item 6, **verbatim and complete**:

> 6. Sub-plan E: storage set()/events publish()/require_auth_for_args become
>    ESCAPES when E gives those surfaces real tier-1 behavior (documented in
>    recognize.note_escapes); tests/fixtures/token_style.py's E12 amendment
>    reverts when Event.publish(env) lands; deliberate time algebra
>    (Timepoint/Duration) is an E decision.

`/Users/elliotvoris/Dev/stellar/sdk/py-soroban/.superpowers/sdd/2026-08-27-m1d-emitter/final-review-attention.md:9-11`, item 1, **verbatim and complete**:

> 1. Obligations carried OUT of M1-D → sub-plan E: `Event.publish(env)`
>    lowering + the token_style E12 revert; `Env` runtime semantics; time
>    algebra.

| ID | The obligation, unpacked | Where E pays it |
|---|---|---|
| **X1** | **Escape-list flip**: `storage.set()`, `events().publish()`, `require_auth_for_args` become escape sites. | §B.2, §E5 |
| **X2** | **`Event.publish(env)` lowering** — named by D's attention file as an E deliverable, and it is a **five-layer** item: the topic/data split convention, the `@contractevent` metadata addition, `SCSpecEventV0` emission (the B10/B14 deferral in `spec/sections.py`), frontend recognition (replacing the M1-C reject), a new IR shape, and emitter lowering. | §B.4, §C.2, §E2 |
| **X3** | **The `token_style.py` E12 revert.** | §B.5, §E2, §E6 |
| **X4** | **`Env` runtime semantics** — the deliberately under-specified phrase. §E1 is where it gets a meaning. | §B.1, §E1 |
| **X5** | **Deliberate time algebra** (D2). | §E3 |
| **X6** | **`SC_SPEC_ENTRY_EVENT_V0` emission**, deferred in `src/serpent/spec/sections.py:35-41` and refused at `sections.py:207-213`. | §B.4, §E2 |
| **X7** | Carried to **F**, not E, but E must not accidentally absorb them: re-run the semantics table on tier-2b; prove `i256_div`'s rounding on the real host; the `val_cmp`/`obj_cmp` differential; the wasm-tools CI pin drift watch; the tier-2b wheel-matrix decision. | §D, §F.3 |

### A.5 What the shipped code says about E, quoted

Every in-tree promise that names sub-plan E. These are the contracts E is held to.

| ID | Quote (verbatim) | Source |
|---|---|---|
| **Q1** | "Every contract method takes an `Env` and reaches the host through it -- `env.storage().instance().get(...)`, `env.ledger().timestamp()`, `env.events().publish(...)`. That chain is what sub-plan C compiles into host function calls, and **what sub-plan E backs with a real host at test time**." | `/Users/elliotvoris/Dev/stellar/sdk/py-soroban/src/serpent/env.py:3-6` |
| **Q2** | "This module is **pure type surface**: every method is fully annotated with chain types and **every body raises `NotImplementedError("sub-plan E")`**. It exists now so that contracts, IDEs and `mypy --strict` can already see the complete shape, and so that Task 10's strict-typed fixture compiles." | `env.py:8-11` |
| **Q3** | "Storage buckets are three distinct types rather than one parameterized bucket because their **TTL operations genuinely differ**: an instance entry's TTL covers the whole instance and is extended without a key, while persistent and temporary entries are extended per key. **Typing them separately means the wrong call is a type error, not a runtime surprise.**" | `env.py:13-17` |
| **Q4** | "The compiler injects the real `Env`; **a contract never constructs one**, and every method here raises until sub-plan E lands **the host bridge**." | `env.py:202-206` |
| **Q5** | "An `Address` is always a host object on-chain, so `to_val()`/`from_val()` raise `NotImplementedError` until sub-plan B, and **`require_auth()` / `require_auth_for_args()` need the `Env` runtime from sub-plan E** -- they exist, fully annotated, so contracts and the type checker can already see the shape." | `/Users/elliotvoris/Dev/stellar/sdk/py-soroban/src/serpent/types/address.py:15-18` |
| **Q6** | "`SC_SPEC_ENTRY_EVENT_V0` -- `SCSpecEventV0` needs a `data_format` and a per-parameter `location` (topic vs data), and **M1-A's `@contractevent` metadata carries no topic/data split** (the events ruling left topics call-site-level). **Guessing would ship a spec that is valid XDR and a lie**, so an event class in `types` is refused, pointing at sub-plan E." | `/Users/elliotvoris/Dev/stellar/sdk/py-soroban/src/serpent/spec/sections.py:35-41` |
| **Q7** | The runtime refusal itself: "`f"{declared.__name__}: event spec entries are deferred to sub-plan E. " "SCSpecEventV0 requires a data_format and a per-parameter location " "(topic vs data), and @contractevent metadata carries no topic/data " "split -- emitting a guessed entry would ship a valid-but-lying spec"` — raised as `SpecTypeError` for `kind == "event"`. **Note it is `if kind == "event":` with no `metadata is not None` guard, unlike the struct/enum arms** — a shape worth preserving deliberately or fixing deliberately. | `sections.py:207-213` |
| **Q8** | The `Event` base's whole reason to exist: "A decorator cannot add a member that a type checker can see, so `publish` lives on a real base class that event types inherit -- that is what makes `Transfer(...).publish(env)` type-check under `mypy --strict`. `@contractevent` requires this base." — and `publish`'s own docstring: "**Emit this event via the host's `contract_event`.**" | `env.py:59-72` |
| **Q9** | `@contractevent`'s metadata is **`{"kind": "event", "fields": [(name, annotation), ...]}`** and nothing else — `_build_record` sets `{"kind": kind, "fields": fields}` where `fields` is `(name, annotation)` pairs in annotation order. **There is no per-field marker of any kind, no `topics=`, no `data_format`.** Q6's claim is therefore exactly true of the shipped code. | `/Users/elliotvoris/Dev/stellar/sdk/py-soroban/src/serpent/decorators.py:218-233` |
| **Q10** | `<bucket>.get`'s contract: "`ty` is passed explicitly because the host returns an untyped `Val`; it is what tells both the compiler and the type checker what comes back. **Without a `default`, a missing key is a contract error.**" | `env.py:87-94` |
| **Q11** | Storage keys: "Keys are any `ChainValue` -- a scalar chain type, a container, or a `@contracttype` struct -- because the host's storage key is an arbitrary `Val` and real contracts key on tuples/structs (**an allowance keyed by `(from, spender)`**, a balance keyed by `Address`) as often as on a `Symbol`. A raw `str` or `int` key is still a static error." **The allowance-token example (S6) is literally the case this docstring was written for.** | `env.py:78-83` |
| **Q12** | `has()`'s contract: "Returns the chain `Bool` the host hands back, not a Python `bool`, so the value stays a chain value all the way through; **`Bool` is truthy in an `if` statement**." | `env.py:100-107` |
| **Q13** | `ledger().timestamp()`: "**`U64`, not `Timepoint`**: the host's `get_ledger_timestamp` returns a `U64Val`, and **serpent does not silently reinterpret an `ScVal` case**." | `env.py:171-176` |
| **Q14** | `events().publish()`: "`topics` is a heterogeneous tuple, not a homogeneous `Vec`: the canonical Soroban shape is `(Symbol, Address, Address)` -- an event name followed by the addresses it concerns. **`topics[0]` is conventionally a short `Symbol` naming the event; the host does not enforce that, but indexers and RPC filtering assume it.**" | `env.py:189-198` |
| **Q15** | The `ChainValue` alias, and why: "This is deliberately a closed union rather than `object`: **a raw `str` or `int` key is a static error, which is the whole point of the chain types.**" `ChainValue: TypeAlias = _ChainValue[Any] | Vec[Any] | Map[Any, Any] | Struct`. | `env.py:50-56` |
| **Q16** | The complete `__all__` of `serpent.env` — **the surface E must keep**: `ChainValue`, `Env`, `Event`, `Events`, `InstanceStorage`, `Ledger`, `PersistentStorage`, `Storage`, `Struct`, `TemporaryStorage`. `Struct` is re-exported from `types._ordering` "where it has always been part of the public surface" (E2/MJ-7 note at `env.py:27-32`). | `env.py:34-45` |

### A.6 What M1-D actually shipped that E consumes

| ID | Item | Source |
|---|---|---|
| **P1** | `serpent.emitter`'s public surface: `build_wasm(compiled, *, meta=None, version=None, validate_external=None) -> BuildResult` and `build_file(path, *, target_protocol=None, meta=None, version=None, validate_external=None) -> BuildResult`. `__all__ = ["BuildLimitError", "BuildResult", "EmitError", "build_file", "build_wasm"]`. **`build_file` is the one-call form E's examples use.** | `/Users/elliotvoris/Dev/stellar/sdk/py-soroban/src/serpent/emitter/__init__.py:50-56,175-254` |
| **P2** | `BuildResult` fields: `wasm`, `declared_protocol`, `target_protocol`, `exports`, `imports`, `runtime_parts_linked`, `needs_memory`, `pool_size`, `scratch_size`, `module_size`. `exports`/`imports` are "**re-derived from the assembled bytes** by `module.assemble` itself (review B1's net), never from the frontend's own (over-approximating, C21) sets." | `emitter/__init__.py:59-97` |
| **P3** | **Failure taxonomy E's example-build tests must expect**: a `BuildLimitError` becomes a located `CompileError` on an `SPT800N` code (`module_size`→SPT8001, `pool`→SPT8002, `scratch`→SPT8003, `unsupported`→SPT8004); a reserved `meta` key is a plain `ValueError` **before** assembly; a bare `EmitError` becomes `CompilerBugError`. "**Only validated bytes are ever returned (P8).**" | `emitter/__init__.py:25-34,182-218` |
| **P4** | **`SPT8004` exists and is named "unsupported"** — the emitter-coverage code, paired at runtime with `CODE_UNSUPPORTED_AT_RUNTIME`. The M1-D dossier's own note on E: "**E is also where `Event.publish(env)` lands (C21) — a new IR shape D must then lower, so D should not hard-code an exhaustive `isinstance` dispatch without a loud default (`CODE_UNSUPPORTED_AT_RUNTIME`, C19, is exactly the fail-safe for this).**" | `emitter/__init__.py:100-111`; M1-D dossier line 413 |
| **P5** | The emitter module map E's `Event.publish` lowering must touch: `encode`, `opcodes`, `frame` (the operand-stack-checked `Fn` + `CallImport`/`CallDefined`), `layout` (pool + scratch), `arith`/`lower` (the `FuncIR` → `Fn` lowering), `sections` (the three custom sections — "the one place this package reaches `stellar_sdk`"), `module.assemble` (two passes), `validate`. | `emitter/__init__.py:1-35` |
| **P6** | **M1-D's mini-host lives in `tests/harness/`** and is dev-only, not an oracle (D11). Its known fidelity gaps are recorded: "the mini-host cannot pin floats or extended-const (no wasmtime-48 Python toggle) … tier-2b (F) is the real gate for those." | D attention file lines 19–22 |
| **P7** | Ratified runtime-part namespace (D12): `{u,i}64_{add,sub,mul,floordiv,mod}`, `{u,i}128_{add,sub,mul,neg,cmp,floordiv,mod}`, box/unbox per EITHER family, `tagcheck_bytes_n`. **There is no time-arithmetic part** — §E3's cross-layer cost lands here. | decisions.md:361–363 |

### A.7 The Env-API recognition table C froze (M1-C dossier §C.4) — what E must give runtime meaning

Reproduced from `/Users/elliotvoris/Dev/stellar/sdk/py-soroban/docs/superpowers/specs/2026-08-27-m1c-inputs-dossier.md:318-353`. **This is the contract between the authored surface and the host; E's Env model must answer the same questions the right-hand column answers, and E's examples must keep every row compiling.**

| Authoring surface | Lowering (C's frozen answer) | What E's tier-1 model owes it |
|---|---|---|
| `env.storage()` / `.instance()` / `.persistent()` / `.temporary()` | "No code. Resolves the `StorageType` immediate: instance=2, persistent=1, temporary=0." | Three bucket objects bound to one store; **no observable state of their own**. |
| `<bucket>.set(key, value)` | `put_contract_data(k: Val, v: Val, t: StorageType)` — `l._` | A write, **snapshotting the value** (X1's whole point). |
| `<bucket>.get(key, T)` | `get_contract_data(k, t) -> Val` — `l.1`, then a narrow-to-`T` check. **D shipped the has-then-get guard raising `CODE_MISSING_VALUE`** (ruling E13). | A read that **raises the same contract error** on a missing key, and **type-checks the decoded value against `T`** (the narrow check's tier-1 twin). |
| `<bucket>.get(key, T, default=d)` | `has_contract_data` → `If` → `get_contract_data` else `d` | A read with a default; **`d` must not be evaluated eagerly in a way tier 1 and the emitter disagree on** (the emitter emits a real `if` block, C8). |
| `<bucket>.has(key)` | `has_contract_data(k, t) -> Bool` — `l.0` | Returns chain `Bool` (Q12), not a Python `bool`. |
| `<bucket>.del_(key)` | `del_contract_data(k, t)` — `l.2` | A delete. **Deleting an absent key: host behaviour unverified in this repo — §F.1.** |
| `instance().extend_ttl(threshold, extend_to)` | `extend_current_contract_instance_and_code_ttl(threshold: U32Val, extend_to: U32Val)` — `l.8` (no key) | §E4. |
| `persistent()/temporary().extend_ttl(key, threshold, extend_to)` | `extend_contract_data_ttl(k, t, threshold, extend_to)` — `l.7`. "(`l.f` `extend_contract_data_ttl_v2` is protocol-gated ≥ 26 — **do not reach for it silently**.)" | §E4. |
| `env.ledger().timestamp()` | `get_ledger_timestamp() -> U64Val` — `x.4`, typed `U64` (D3) | A configurable `U64`. |
| `env.ledger().sequence()` | `get_ledger_sequence() -> U32Val` — `x.3` | A configurable `U32`. **Whether it ADVANCES is §E4's crux.** |
| `env.events().publish(topics_tuple, data)` | `MakeTopics` → `VecObject`, then `contract_event(topics: VecObject, data: Val)` — `x.1`. "Enforce `topics[0]` is a short `Symbol`." | An append to an event log, **rolled back with a failed frame (S9)**. |
| `<Event instance>.publish(env)` | **"Undecided — B14/D8: no topic/data split exists (§E12)."** | **§E2 — E's biggest cross-layer item.** |
| `addr.require_auth()` | `require_auth(address: AddressObject)` — `a.0` | An auth check against a recorded allow-set (S4: "`mock_all_auths` semantics only"). |
| `addr.require_auth_for_args(vec)` | `require_auth_for_args(address, args: VecObject)` — `a._` | Same, plus the args. **An escape site (X1).** |
| `raise Error.X` | `fail_with_error(error: Error)` — `x.5`, arg = `val.error_val(code)` | Python raises the `@contracterror` exception class already (M1-A); the model just must not swallow it. |
| `env.logs()` (`log_from_linear_memory` `x._`), `get_current_contract_address` `x.7`, `get_max_live_until_ledger` `x.8`, `get_ledger_version` `x.2`, `get_ledger_network_id`, `call`/`try_call`, crypto, PRNG, deployer | "**Recognized but not lowerable in M1-C** (must be a clean 'not in M1' diagnostic, not a crash) — all M2 (spec §11)." | **Stay unimplemented; §E8's honest-boundary list.** Note `get_max_live_until_ledger` is exactly the fact S8's "extension past max clamps" needs — see §E4. |

## B. INVENTORY

"★" marks a genuinely contestable line needing a controller decision (cross-referenced to §E).

### B.0 What E actually is — the frame, and where the sources disagree

The roadmap phrase is "**wired end-to-end**" (R1). Three readings are textually supported, and they are not the same sub-plan:

| Reading | Textual support | What it would ship |
|---|---|---|
| **(i) A pure-Python in-memory Env model** — a contract instance is constructed and its methods called as ordinary Python, with an `Env` object holding storage dicts, an event list and an auth set. | The M1-C attention file's own words: "become ESCAPES when E gives those surfaces **real tier-1 behavior**" (X1) — tier 1 is by definition "no WASM" (S3). `recognize.note_escapes:2170-2177` grounds the *current* exemption on "every `env.py` and `types/address.py` body on those paths raises `NotImplementedError`, sub-plan E", i.e. the exemption exists because tier 1 cannot run them. | A third implementation of host semantics (S13's drift risk), a new innermost dev tier, and the `Env` constructor a contract "never constructs" (Q4). |
| **(ii) A host bridge at test time** — `Env`'s methods stay unimplemented for direct Python execution; "wired end-to-end" means the authored chain compiles, builds, and runs under a host, and E ships the examples proving it. | `env.py:3-6` verbatim (Q1): "what sub-plan E **backs with a real host at test time**"; `env.py:202-206` (Q4): "every method here raises until sub-plan E lands **the host bridge**"; spec §8 (S3) gives tier 1 no contract-execution role at all, and names tier 2a "the fast dev loop" (S4). | The five examples running as WASM under the harness; `Event.publish` lowering; the spec-entry emission; the time-algebra ruling. **No new host model at all.** |
| **(iii) Both** — a tier-1 model *and* the WASM path, with the model held to the emitter's answers by the semantics differential. | The task's own framing; and D11's precedent that the mini-host "is NOT an oracle" implies serpent will eventually own a model it *does* trust. | Everything in (i) and (ii), plus a third leg on the S13 differential. |

**This dossier does not choose.** §E1 poses it. Everything below that depends on the choice is marked ★§E1.

Two observations the controller should have before ruling:

1. **The escape flip (X1) is only *required* under reading (i).** `note_escapes`' exemption is justified by "**tier 1 has no shared-object model to diverge from** at any of them" — that clause is about tier 1, not about the chain. Under reading (ii) the clause stays true and the exemption stays sound, and X1 becomes a no-op. Under reading (i) the clause becomes false and X1 is mandatory — and it **narrows accepts** (§E5, §B.2).
2. **Reading (ii) is the only one that fits `Address.require_auth()`'s shipped signature.** It takes no `Env` (`address.py:88-94`, verified): a pure-Python model has no ambient env to check against, so reading (i) forces either a signature change (breaking, and it is the surface spec §2's example uses — `from_.require_auth()`, spec:64) or a thread-local/contextvar ambient env. §E1 and §E7 both hinge on this.

### B.1 The complete authored Env surface, and what a tier-1 model owes each method

Every method in `/Users/elliotvoris/Dev/stellar/sdk/py-soroban/src/serpent/env.py` plus the two on `Address`. All bodies are `raise NotImplementedError("sub-plan E")` today except the two `Address` ones (`"Env runtime; sub-plan E"`) — **note the two different message strings; a test that greps for one misses the other.**

| # | Surface | Signature (verbatim) | Line | Tier-1 obligation ★ = contested |
|---|---|---|---|---|
| 1 | `Env.storage()` | `def storage(self) -> Storage` | `env.py:210-211` | Returns a `Storage` view. No state. |
| 2 | `Env.ledger()` | `def ledger(self) -> Ledger` | `env.py:213-214` | Returns a `Ledger` view. |
| 3 | `Env.events()` | `def events(self) -> Events` | `env.py:216-217` | Returns an `Events` view. |
| 4 | `Storage.instance()` | `def instance(self) -> InstanceStorage` | `env.py:156-157` | The instance sub-map (S7: "not a durability — a sub-map in the instance entry"). |
| 5 | `Storage.persistent()` | `def persistent(self) -> PersistentStorage` | `env.py:159-160` | Durability 1. |
| 6 | `Storage.temporary()` | `def temporary(self) -> TemporaryStorage` | `env.py:162-163` | Durability 0. |
| 7 | `_StorageBucket.get` | `def get(self, key: ChainValue, ty: type[_T], default: _T | None = None) -> _T` | `env.py:87-94` | Read + **decode-as-`ty` check** (the tier-1 twin of D's narrow check). Missing key with no default → **the same contract error the emitter raises**, i.e. `errors.CODE_MISSING_VALUE`. ★ how is a `CODE_MISSING_VALUE` surfaced in Python? §E1/§E8 |
| 8 | `_StorageBucket.set` | `def set(self, key: ChainValue, value: ChainValue) -> None` | `env.py:96-98` | Write, **snapshotting** (X1). ★§E5 |
| 9 | `_StorageBucket.has` | `def has(self, key: ChainValue) -> Bool` | `env.py:100-107` | Returns chain `Bool` (Q12). |
| 10 | `_StorageBucket.del_` | `def del_(self, key: ChainValue) -> None` | `env.py:109-111` | Delete. **Absent-key behaviour unverified in this repo** — §F.1.4. |
| 11 | `InstanceStorage.extend_ttl` | `def extend_ttl(self, threshold: U32, extend_to: U32) -> None` | `env.py:123-126` | ★§E4 |
| 12 | `PersistentStorage.extend_ttl` | `def extend_ttl(self, key: ChainValue, threshold: U32, extend_to: U32) -> None` | `env.py:134-137` | ★§E4 — S8's **clamp** side |
| 13 | `TemporaryStorage.extend_ttl` | `def extend_ttl(self, key: ChainValue, threshold: U32, extend_to: U32) -> None` | `env.py:145-148` | ★§E4 — S8's **trap** side |
| 14 | `Ledger.timestamp` | `def timestamp(self) -> U64` | `env.py:171-177` | Configurable `U64` (Q13: `U64`, not `Timepoint`, "serpent does not silently reinterpret an `ScVal` case"). |
| 15 | `Ledger.sequence` | `def sequence(self) -> U32` | `env.py:179-181` | Configurable `U32`. ★ does it advance? §E4 |
| 16 | `Events.publish` | `def publish(self, topics: tuple[ChainValue, ...], data: ChainValue) -> None` | `env.py:189-198` | Append to an event log; **roll back with a failed frame (S9)** ★§E1/§E7. |
| 17 | `Event.publish` | `def publish(self, env: Env) -> None` | `env.py:70-72` | **A hard compile REJECT today (SPT1032).** ★§E2 — the five-layer item. |
| 18 | `Address.require_auth` | `def require_auth(self) -> None` | `address.py:88-90` | Auth check. **No `Env` parameter** — §B.0 note 2. ★§E1/§E7 |
| 19 | `Address.require_auth_for_args` | `def require_auth_for_args(self, args: Vec[Any]) -> None` | `address.py:92-94` | Same + args. **An escape site under X1.** ★§E5 |

**Not present anywhere in the authored surface** (so E cannot "leave them NotImplementedError" — there is nothing to leave): `env.logs()`, `env.current_contract_address()`, `env.call`/`try_call`, `env.crypto`, `env.prng`, `env.deployer`, `Ledger.version()`, `Ledger.network_id()`, `Ledger.max_live_until_ledger()`. **The frontend nevertheless RECOGNIZES all of them by name** and rejects with `SPT1033` — `KNOWN_FUTURE_ENV_NAMES = {logs, call, try_call, crypto, prng, current_contract_address, deployer}` (`recognize.py:574-584`) and `_LEDGER_FUTURE_METHODS = {version, network_id, max_live_until_ledger}` (`recognize.py:591-593`), whose help string is "**this Env surface is deferred to M2; there is no rewrite available yet**" (`recognize.py:234`). **These are labelled M2, not E** — §E8's honest-boundary list must say so and E must not silently absorb them.

`get_max_live_until_ledger` (`x.8`) being on that M2 list is load-bearing for §E4: it is exactly the host fact S8's "extension past max **clamps**" needs, and it is not reachable in M1.

### B.2 The escape-list flip (X1) — the exact edit, and what it costs

The ruling that must change is `recognize.note_escapes`' docstring, `/Users/elliotvoris/Dev/stellar/sdk/py-soroban/src/serpent/compiler/recognize.py:2170-2177`, **verbatim**:

> Three recognized container-argument positions deliberately do NOT count as
> escapes: `<bucket>.set(k, v)`, `events().publish(topics, data)`, and
> `addr.require_auth_for_args(args)`. All three serialize their argument out
> to the host rather than storing a handle, and tier 1 has no shared-object
> model to diverge from at any of them (every `env.py` and
> `types/address.py` body on those paths raises `NotImplementedError`,
> sub-plan E). If sub-plan E gives those surfaces real tier-1 behaviour, they
> become escapes and belong in this hook.

**What "escape" means in this model — verified, and it is NOT what the phrase suggests.** An escape is *not* a diagnostic. `note_escapes` is a five-line delegation (`recognize.py:2200`: `ctx.alias_sets.mark_escapes(values, reason)`) to `AliasTable.mark_escapes` (`ctx.py:258-280`), which for every local the value could *be* (`_escaping_locals`, `ctx.py:200-214` — only a `LocalRef` or an `IfExp` arm; a `HostCall`/`Make*`/`FieldGet` yields nothing) and whose `ty.tag` is in `MUTABLE_TAGS = frozenset({TyTag.VEC, TyTag.MAP})` (`ctx.py:64`) calls `mark_aliased(slot)`. That flips the slot's `Ownership` from `OWNED` to `ALIASED`. **The only consumer of `Ownership` is `_mutation_slot` (`recognize.py:2443-2502`)**, whose gate is:

```python
    if isinstance(recv, LocalRef):
        ownership = ctx.alias_sets.ownership_of(recv.slot)
        if ownership is Ownership.OWNED:
            return recv.slot
```

— everything else falls through to `SPT1034`.

| Question | Verified answer |
|---|---|
| Does making these escapes **widen or narrow** accepts? | **NARROWS.** A `Vec`/`Map` local passed to `storage.set()` and *mutated anywhere in the same function* becomes `SPT1034`. |
| Does it reject code that compiles TODAY? | **Yes, definitively.** The pinned proof is `/Users/elliotvoris/Dev/stellar/sdk/py-soroban/tests/unit/test_frontend.py:700-726`, `test_a_container_built_up_in_a_loop_compiles`, whose own docstring is: "**The pattern the SPT1034 `help:` lines point at must actually compile.** A pre-pass that over-approximated escapes would make this a false reject, which would leave the diagnostics recommending something the compiler refuses." Its body mutates `rows`/`seen` in a `while` loop and then calls `env.storage().persistent().set(Symbol("rows"), rows)`. **This test starts failing.** |
| Where does the reject land relative to the `set()` line? | **Anywhere in the body, including textually BEFORE it.** The pre-pass `collect_never_owned` (`recognize.py:2347-2421`) is flow-insensitive by design; its docstring: "a function whose `own` is legitimately owned in one region and aliased in another gets the aliased answer everywhere, which is a reject rather than an unsound rebind" (`recognize.py:2366-2369`). Applied at `stmt.py:316-318`. |
| Is it one edit or several? | **Four sites**, and missing any leaves the two escape mechanisms (IR-level and syntactic) disagreeing: (1) add `note_escapes` calls in `_storage_set` (`recognize.py:994-1008`), `_events_publish` (`1209-1254`), `_recognize_require_auth` (`1273-1308`) — none has one today; (2) delete or narrow `_is_serializing_call` (`recognize.py:2302-2312`); (3) remove `"publish"` and `"require_auth_for_args"` from the effect of `_NON_STORING_METHOD_NAMES` (`recognize.py:2255-2270`) — `_positional_args_escape` returns `False` for anything in that set (`2330`), so deleting `_is_serializing_call` alone does **not** make publish/auth escape; (4) rewrite `SPT1034`'s `help` text (`recognize.py:2424-2440`) which **currently recommends the pattern the flip forbids**, and its registry row (`codes.py:318-326`). |
| Any latent inconsistency already in the tree? | **Yes, and it is evidence about the ruling, not evidence for it.** `collect_never_owned` marks **every keyword-argument value unconditionally** with no `_is_serializing_call` consultation (`recognize.py:2415-2417`), so `set(key=k, value=own)` **already** loses ownership while `set(k, own)` does not — pinned by `tests/unit/test_frontend.py:961-978`, `test_a_container_in_a_keyword_position_loses_ownership`. If E flips the ruling, the two spellings converge and *that* test keeps passing. |
| A fourth, unreachable-today position the docstring names | `<bucket>.get(key, T, default=d)` lowers to an `IfExp` whose `orelse` **is** `d`, so a container passed as `default` can be the value of the whole expression. "**It is unreachable today** (a `get`'s type argument must be a bare chain-type or struct name, so no container type can be requested), but Task 8's wiring must route that lowering's result through the same escape handling **if it becomes reachable**" (`recognize.py:2179-2187`). Reachability is blocked by `_resolve_type_arg`'s bare-`Name` rule; pinned by `tests/unit/test_frontend.py:947-958` (`SPT3013`). |

**The honest framing for §E5:** X1 is not a bug fix. It is *conditionally* a soundness fix — sound-necessary if and only if the tier-1 model stores a *reference* to the caller's live Python container. If the model **deep-copies on `set()`** (which is what the host does: `put_contract_data` serializes a `Val`), then tier 1 and the chain agree and the exemption stays correct — and **no authoring surface narrows at all**. The same argument applies to `publish` (the host serializes the event) and `require_auth_for_args` (the host hashes the args into the auth payload). §E5 recommends on that basis.

### B.3 Time algebra (X5) — the surface as it stands, and what an op costs across layers

D2 froze "no arithmetic at all" and named the algebra E's decision. The rejection is enforced in **two** places:

| Layer | Mechanism |
|---|---|
| Tier 1 (`src/serpent/types/numeric.py`) | `Timepoint`/`Duration` raise `TypeError` naming the omission and pointing at the `to_u64`/`from_u64` bridges (D2). |
| Frontend (`src/serpent/compiler/expr.py`) | `_TIME_ALGEBRA_NOTE = "time algebra is a sub-plan E decision (D4/A17)"` at `expr.py:234`, attached to `SPT3005` rejects at `expr.py:1687` and `expr.py:1755`. **The note names sub-plan E by number — a `must_reject` fixture and a `docs/subset.md` row hang off it (S16).** |

The candidate ops and their honest cross-layer cost — this is what makes §E3 a real question rather than a formality:

| Op | Type rule | Tier 1 | Frontend | IR | Emitter |
|---|---|---|---|---|---|
| `Duration + Duration -> Duration` | same width (both `u64`-backed) | new `__add__` on `Duration` only | `expr.py` binary-type table row; drop the `SPT3005` reject for this shape | existing `Binary(ADD)` with `ty=Duration` | **`Ty.Duration.wasm_arith_width` is already 64** (C5) and `Duration` is `EITHER`-repr, so it needs `box/unbox_u64` + `u64_add` + the overflow check — **the parts already exist** (P7). |
| `Timepoint + Duration -> Timepoint` | **cross-type**: operands have *different* `Ty` | new `__add__` accepting a `Duration` | the binary checker currently requires `lhs.ty == rhs.ty` for arithmetic (M1-C: "cross-type arithmetic" is an `SPT3xxx` reject) — this needs a **new asymmetric rule**, not a relaxation | `Binary(ADD)` whose `lhs.ty != rhs.ty` — **a shape no IR node contract admits today** ★ | same 64-bit route, but the *result* `Ty` differs from both operand types |
| `Timepoint - Timepoint -> Duration` | **result type differs from both operands** | `__sub__` returning a `Duration` | same new asymmetric rule | same | same, plus the "negative result → `ArithmeticOverflow`" rule (a `Duration` is unsigned) |
| `Timepoint < Timepoint`, `Duration < Duration` | same-type comparison | **ALREADY WORKS at every layer** — `_TimeValue`'s docstring, verbatim: "`+ - * // %` and unary `-` raise `TypeError` naming the omission; **comparisons, truthiness, the Val forms and the `to_u64()` / `from_u64()` bridges all work**, so arithmetic is done explicitly on `U64` (**sub-plan E needs the bridge for `env.ledger().timestamp()`**)" (`numeric.py:538-549`) | `Compare` with `via_obj_cmp=False` (C6) | existing `Compare` | existing unbox-then-relop route |

**The cross-width/cross-type question is the sharp one.** Every arithmetic `Binary` in the shipped IR carries operands that "**both share `ty`**" (M1-D dossier §B.3.1, `ir.py:247`), and the M1-D emitter's arithmetic lowering was built on that. `Timepoint + Duration` and `Timepoint - Timepoint` both violate it. That is an IR-contract change in E, in the layer D just froze — see §E3 and §F.3.

### B.4 `Event.publish(env)` (X2/X3/X6) — the five-layer item, layer by layer

This is E's biggest cross-layer deliverable. Every layer is inventoried here; the *convention* choice is §E2.

**Layer 0 — what exists today.**

| Piece | State | Source |
|---|---|---|
| The authored method | `Event.publish(self, env: Env) -> None`, docstring "Emit this event via the host's `contract_event`", body `raise NotImplementedError("sub-plan E")` | `env.py:59-72` |
| `@contractevent` metadata | **`{"kind": "event", "fields": [(name, annotation), ...]}` and nothing else.** No per-field marker, no `topics=`, no `data_format`. Set by `_build_record` (`decorators.py:218-233`); `@contractevent` only additionally checks `Event in cls.__mro__` (`decorators.py:209-215`). **[verified]** | `decorators.py:199-233` |
| Frontend recognition | A dedicated **REJECT** row: `RECOGNIZED["event.publish_reject"] = HostCallSpec(surface="<Event instance>.publish(env)", kind=SurfaceKind.REJECT, reject_code="SPT1032")` (`recognize.py:380-384`); `_reject_event_publish` (`recognize.py:1257-1267`) emits `SPT1032` with detail "`` `<Event instance>.publish(env)` is deferred to sub-plan E``" and help "use env.events().publish(topics, data) instead" (`recognize.py:233`). Registry row `codes.py:296-302`. | — |
| **A reachability subtlety** | The reject fires only when the receiver is a *direct construction*: `_is_event_construction` (`recognize.py:944-956`) requires an `ast.Call` on an `ast.Name` resolving to a class whose metadata `kind == "event"`. `Transfer(...).publish(env)` → `SPT1032`; but `ev = Transfer(...)` is rejected one step earlier by `expr.py:1246-1252` as `SPT1037` ("`Transfer(...)` is a event class, which is not a value"). **So E must decide whether an event instance becomes a *local* at all**, or stays construction-and-publish-in-one-expression. ★§E2 | `recognize.py:944-956`; `expr.py:1246-1252` |
| Spec-entry emission | Refused: `SpecTypeError` for `kind == "event"` (Q6/Q7, `sections.py:35-41,207-213`). Note the arm is `if kind == "event":` with **no `metadata is not None` guard**, unlike the struct/enum arms above it. | `sections.py:203-213` |
| Emitter | **No `Event.publish` node exists**, so nothing to lower. D's own dossier flagged this and D's fail-safe is `SPT8004`/`CODE_UNSUPPORTED_AT_RUNTIME` (P4). | — |

**Layer 1 — the XDR shape E must fill. [verified live against `stellar_sdk` 15.0.0]**

```python
xdr.SCSpecEventV0(doc: bytes, lib: bytes, name: SCSymbol,
                  prefix_topics: list[SCSymbol],      # max length 2 (enforced in __init__)
                  params: list[SCSpecEventParamV0],   # max length 2**32-1
                  data_format: SCSpecEventDataFormat)

xdr.SCSpecEventParamV0(doc: bytes, name: bytes,       # name max length 30
                       type: SCSpecTypeDef,
                       location: SCSpecEventParamLocationV0)

SCSpecEventParamLocationV0: SC_SPEC_EVENT_PARAM_LOCATION_TOPIC_LIST | SC_SPEC_EVENT_PARAM_LOCATION_DATA
SCSpecEventDataFormat:      SC_SPEC_EVENT_DATA_FORMAT_SINGLE_VALUE | ..._VEC | ..._MAP
SCSpecEntryKind:            SC_SPEC_ENTRY_EVENT_V0  (present; the entry kind exists)
```

Three facts that constrain §E2 hard:
- **`prefix_topics` is capped at 2** (a `ValueError` from `stellar_sdk`'s own constructor). R5's "pre-validate at every nominally-fallible soroban-sdk boundary" and `sections.py`'s source-located-validation discipline (`sections.py:16-21`) mean serpent must pre-check this with a named declaration, not let `stellar_sdk` raise.
- **`lib` is capped at 80** and `name` is an `SCSymbol` — so the event name is Symbol-charset-restricted, which the existing `_check_name` / D-era Symbol-charset ruling (decisions.md:117–124) already covers.
- **Per-param `name` is capped at 30** — the same cap `decorators.NAME_LIMIT` already enforces on fields.

**Layer 2 — what Rust does, so serpent can mirror it. [verified live via DeepWiki against `stellar/rs-soroban-sdk`]**

- Fields are marked topics with a **per-field `#[topic]` attribute**; unmarked fields are data. The macro sets each param's `ScSpecEventParamLocationV0` to `TopicList` or `Data` accordingly.
- `#[contractevent(topics = ["a", "b"])]` supplies **constant prefix topics**. **Default when omitted: the struct's own name in snake_case** (e.g. `MyEvent` → `"my_event"`) as the sole prefix topic.
- `#[contractevent(data_format = "map"|"vec"|"single-value")]`. **Default: `"map"`** — fields as key-value pairs, keys being field-name Symbols, **sorted alphabetically**. `"vec"` = struct declaration order. `"single-value"` = only legal with exactly one non-topic field.
- `prefix_topics` (static, always first) is **distinct** from `#[topic]` (dynamic, per-instance values).

Note how cleanly this lines up with what serpent already has: the `"map"` default's "keys are field-name Symbols sorted" is **exactly `MakeStruct`'s P7 byte-string sort, which C already owns** (M1-D dossier C9), and the snake_case-name default prefix topic is exactly S10's "`topic[0]` is a short `Symbol` event name".

**Layer 3 — the five edits E must make.** Each is a distinct task, and three of them touch layers other sub-plans own:

| # | Layer | Edit | Owner precedent |
|---|---|---|---|
| E2-a | **M1-A surface** (`decorators.py`) | `@contractevent` gains topic/data metadata. Whatever §E2 chooses, `_METADATA_ATTR`'s `"event"` record grows beyond `{"kind","fields"}` — and `spec/sections.py` reads that record, so the two move together. | D12's "licensed frontend edit" precedent, one level lower (M1-A surface). **This is an authoring-surface change; D4's reversal note says it is "cheap now, breaking after docs/examples" — and E ships the docs and examples.** |
| E2-b | **M1-B spec emission** (`spec/sections.py`) | Delete the `kind == "event"` refusal; add an `_event_entry(declared, metadata)` beside `_struct_entry`/`_enum_entry`; add `SC_SPEC_ENTRY_EVENT_V0` to the pinned entry order (`sections.py:145-151` currently pins structs → error enums → functions — **where do events go? ★§E2**); pre-validate `prefix_topics ≤ 2` and every param name; extend `build_spec_entries`' `types=` contract, whose docstring currently says an event class in `types` is refused. | The module was authored *expecting* this (Q6). |
| E2-c | **M1-C frontend** (`recognize.py`, `codes.py`, `expr.py`) | Flip `RECOGNIZED["event.publish_reject"]` from `SurfaceKind.REJECT` to a lowering row; decide whether an event instance can be a local (`expr.py:1246-1252`'s `SPT1037`); retire `SPT1032` under D9's discipline (**"no renumber, no delete, no meaning reversal"** — so the row stays, its `message_intent` "deferred to sub-plan E; use env.events().publish(topics, data)" becomes stale text a sanctioned pass rewrites, and the code either finds a new honest meaning or joins `NO_FIXTURE_ALLOWLIST`); move the `must_reject` fixture (S16) and regenerate `docs/subset.md`. | D12's three licensed frontend edits. |
| E2-d | **The IR** (`ir.py`) | A new node — or a reuse. **Reuse is available and cheap**: `env.events().publish(topics, data)` already lowers to `HostCall("contract_event", args=(MakeTopics(...), data))` (`recognize.py:1209-1254`), and `MakeTopics` exists (`ir.py:387`). If `Event.publish(env)` desugars *in the frontend* into that same shape, **the IR does not change at all and the emitter does not change at all.** ★§E2 — this is the recommendation's crux. |
| E2-e | **M1-D emitter** (`lower.py`) | Nothing, **if** E2-d desugars. Otherwise a new lowering, plus the `data_format` construction (a `MakeMap` for `"map"`, a `MakeVec` for `"vec"`, a bare value for `"single-value"`) and D's `MakeMap` LM-form gate (ruling E12: `key_ty` Symbol AND all-`Const` keys — **which a field-name-keyed map satisfies exactly**). | P4/P5 |

**Layer 4 — the `token_style.py` revert (X3).** `tests/fixtures/token_style.py` was amended in M1-C to spell `env.events().publish(...)` instead of `Transfer(...).publish(env)`. D5's reversal note calls it "**user-visible and trivially revertable when E lands**".

**Correction to the attention file's framing, verified:** the fixture carries **no comment naming a revert**. What it carries is the *rationale for the current spelling* (`/Users/elliotvoris/Dev/stellar/sdk/py-soroban/tests/fixtures/token_style.py:21-25`, verbatim):

> * A `@contractevent` class inheriting `Event`, declared and mypy-visible. It is
>   NOT published through `Transfer(...).publish(env)`: ruling E12 defers that
>   form's topic/data split to sub-plan E (`_serpent_type_` carries none, B14),
>   so M1-C compiles only the canonical `env.events().publish(topics, data)`
>   line below -- which is exactly what `transfer` emits.

and the line itself (`token_style.py:112-113`):

```python
        # The canonical heterogeneous topic shape: (Symbol, Address, Address).
        env.events().publish((Symbol("transfer"), frm, to), amount)
```

So "the revert" is a *decision E must make*, not an edit already written down waiting to be applied. **And both spellings must keep working**: D5 says `env.events().publish()` "is the supported form", not "the only form", so E needs coverage of both, and the token fixture is chain-anchored by a byte-identity test — see §B.6. ★§E2/§E6

### B.5 Fixtures, sandbox, and the five examples

**`examples/` does not exist.** Verified: `find` over the repo turns up only `.hypothesis/examples` and `spikes/spike2/target/*/examples`, both build artifacts. The spec mandates it (S1, `spec:154`) and describes each example as a **uv workspace member** — "each example is its own package, not just a loose `.py` file". `README.md` mentions neither "example" nor "sandbox". **The repo currently uses `tests/fixtures/` as the de-facto examples directory**, and says so: `sandbox/README.md:52-56` points authors at `tests/fixtures/token_style.py` ("events, errors, structs, auth") and `spike1_reauthored.py` under the heading "**Reference contracts in the reviewed tree**".

#### B.5.1 `tests/fixtures/` — the four contracts that compile, build and RUN today

| File | Lines | Exercises | Graduation verdict |
|---|---|---|---|
| `/Users/elliotvoris/Dev/stellar/sdk/py-soroban/tests/fixtures/token_style.py` | 113 | instance + **persistent** storage; a `@contracttype` **struct storage key** (`BalanceKey(owner=...)`); `@contracterror` + `raise TokenError.InsufficientBalance`; `@contractevent` inheriting `Event` + `env.events().publish` with the heterogeneous `(Symbol, Address, Address)` topic shape; `Address.require_auth()`; `Address` `==`; `get(..., default=)`; `__init__` → `__constructor`; U32 checked add/sub. Root-only imports. | **The strongest single fixture.** Covers events + errors + structs + auth at once. **But it is a mint/transfer token, NOT an allowance token**: no allowance map, no `(from, spender)` composite key, no `approve`/`transfer_from`, no `temporary()` (where allowances belong), no TTL. |
| `tests/fixtures/spike1_reauthored.py` | 83 | `@contracttype` struct with a 13-char field (forces `symbol_new_from_linear_memory`) + a `String` literal (forces the data section); instance + persistent storage; `errorcode(7)` raise; struct field read; U32 compare/add. **Chain-anchored to the deployed 877-byte testnet artifact.** | Graduate as the **structs** example, or keep as Phase-0 evidence and write a purpose-built structs example. Its docstring frames it as evidence, not pedagogy. |
| `tests/fixtures/sandbox_counter.py` | 57 | persistent `get(default=)`/`set`; `errorcode(1)` raise on a ceiling; U32 add/compare. | **The counter example, verbatim.** |
| `tests/fixtures/sandbox_hello_world.py` | 68 | a module-level helper (`FuncKind.INTERNAL`, E8) called from two methods; `__init__` → `__constructor`; `Symbol` compare via `obj_cmp` (**the `"_"`-vs-`"A"` trap vector**); a `Vec[Symbol]` built by the guest and returned across the ABI; instance storage with and without `default=`. | Useful, but **not one of the five named**. |

**The anti-drift mechanism E must preserve:** the two `sandbox_*` fixtures are copies of `sandbox/` originals, guarded by **a byte compare of the BUILDS, not the text** — `tests/unit/test_emitter_end_to_end.py:337-388` builds both and asserts byte-identical modules (`sandbox_counter.py:21-27` documents it). If E moves either file, that test moves with it. ★§E6

**The fixture list is a single shared constant** (`/Users/elliotvoris/Dev/stellar/sdk/py-soroban/tests/unit/test_emitter_end_to_end.py:79-83`, verbatim):

```python
#: THE fixture list this sub-plan's whole-contract properties run over. Defined
#: here, where the contracts are built and invoked, and imported by
#: `tests/unit/test_emitter_fuzz.py` for the two budget-shaped properties (the
#: size tripwire and `needed <= linked`) so there is ONE list of fixtures.
FIXTURES: tuple[Path, ...] = (SPIKE1, TOKEN_STYLE, SANDBOX_COUNTER, SANDBOX_HELLO_WORLD)
```

**Adding a contract to `FIXTURES` gets it four properties for free**: the size tripwire and `needed ⊆ linked` (`test_emitter_fuzz.py`), the goldens under `tests/goldens/wasm/*.wat.txt`, and the host-fn inventory check at `tests/unit/test_harness_hostfns.py:940-960` (every host function the contract reaches must have a `FullHost` callback). **That is the cheapest possible acceptance harness for E's five examples** — §D.

#### B.5.2 `sandbox/` — explicitly unreviewed

Tracked: `README.md`, `compile.py` (43 lines), `counter.py` (33), `hello_world.py` (32), `storage.py` (12). `sandbox/README.md:3-4`, verbatim: "A scratch area for playing with serpent's compiler frontend. **Nothing in here is shipped, tested, or reviewed** — expand, break, and rewrite freely." And `:57-58`: outside the mypy/pytest scope (`ruff check` still lints it).

Three E-relevant facts:
- **`sandbox/README.md:17-20` is already stale and E must fix it**, verbatim: "There is no WASM output yet — the emitter is sub-plan M1-D — and `Env`'s storage/events raise `NotImplementedError` at runtime until sub-plan E, so today the game is authoring contracts and watching the compiler judge them." M1-D has shipped; the second clause is exactly what E changes (or deliberately does not — §E1).
- **`sandbox/compile.py` does not call `build_wasm`.** It is frontend-only. A natural E-or-G upgrade to `build_file`.
- **`sandbox/storage.py` uses a relative import** (`from .hello_world import Error`), i.e. deliberately illegal in the subset — a "things to try" artifact, not a candidate.

#### B.5.3 The coverage gap — what the five examples must add that nothing exercises today

Verified by grep across `tests/fixtures/` and `sandbox/`: **nothing anywhere exercises `extend_ttl`, `temporary()`, `env.ledger()`, `require_auth_for_args`, `Timepoint`, or `Duration`.** There is exactly **one** `env.events().publish` call site in the whole reviewed tree (`token_style.py:113`).

| Example (S6's names) | Nearest existing asset | Net-new work |
|---|---|---|
| **counter** | `sandbox_counter.py` — a direct match | Prose/docs only. |
| **events** | one publish line inside `token_style.py` | **Net-new contract.** Should exercise multiple event shapes and, if §E2 lands `Event.publish(env)`, both spellings. |
| **errors** | `errorcode` raises in three fixtures | **Net-new contract** — a dedicated multi-code `@contracterror` with the S12 `__constructor`-laundering caveat demonstrated. |
| **structs** | `spike1_reauthored.py` (struct + >9-char field), `token_style.py` (struct storage key) | Graduate or rewrite. |
| **allowance-token-no-xcontract** | `token_style.py` is a *mint/transfer* token | **The largest net-new item**: an allowance map keyed on a composite `(from, spender)` (exactly what `env.py:78-83`'s docstring was written for, Q11), which belongs in **`temporary()`** storage with a **TTL** — and `temporary()`, `extend_ttl` and the TTL semantics are all unproven surface (§E4). |

**The allowance-token example is therefore the one that forces §E4's TTL ruling.** An allowance with no expiry is not an allowance-style token; an allowance with an expiry needs `temporary().set` + `temporary().extend_ttl` + a story about what "expired" means at whichever tiers E ships.

### B.6 The M1-D mini-host (`tests/harness/`) — what it models, and whether E can share it

1959 lines across six modules. Its own disclaimer, repeated verbatim at `tests/harness/__init__.py:3-8` and `tests/harness/hostfns.py:3-8`:

> **This is not an oracle** (ruling E1). It is a fast local loop that answers one
> narrow question -- *do the bytes the emitter just produced compute what the
> Python source said?* -- before a testnet round trip. Sub-plan F re-proves
> everything against the real Soroban host. A green run here means "the codegen is
> self-consistent", not "this contract is correct on chain"

| Surface | What the harness actually does | Fidelity to spec §13 |
|---|---|---|
| **Storage** | **One flat dict**: `self.storage: dict[tuple[int, object], int]` — `(storage_type, map_key(key)) -> Val word`. Three durabilities are the bare frontend immediates `STORAGE_TEMPORARY=0/PERSISTENT=1/INSTANCE=2` (`objects.py:90-94`). Keyed on `map_key(key)`, a **value**-normalizing function (`objects.py:404-448`) so "a struct storage key … is a `MapObject`, and the contract builds a FRESH one on every invocation. A store that keyed on the handle word would file `mint`'s write under handle 844 and then look `balance` up under handle 901, find nothing, and return the storage default — a plausible number, silently wrong" (`objects.py:36-49`). | Buckets correct. **S7's instance-sub-map-flushed-at-frame-exit is NOT modelled** (instance is just durability 2). |
| **`get_contract_data` on an absent key** | **An `AssertionError`, deliberately not a `HostError`** — verbatim: "`get_contract_data` on an absent key is undefined behaviour the emitter's guard exists to prevent (E13/E14). If a test ever reaches it, the guard was not emitted -- and that must read as a broken lowering, not as the `HostError` a correct guard would have raised." (`objects.py:660-673`) | A deliberate non-model. **E's tier-1 `get` must raise `0xfffffffd` (`CODE_MISSING_VALUE`) — `docs/subset.md:65-70` documents that code to users.** |
| **`del_contract_data` on an absent key** | Silent no-op (`self.storage.pop(..., None)`). Flagged at `hostfns.py:365-370` as a deliberate asymmetry: `map_del` **traps** on an absent key while `del_contract_data` does not — "two different host behaviours the rig must not unify." | Unverified against the real host. §F.1 |
| **TTL** | **Recorded no-ops that record only the CALL.** No `live_until_ledger_seq`, no per-entry TTL, no max, no clamp, no temporary trap. Verbatim (`objects.py:675-688`): "Bound purely so that row is EXECUTABLE. **TTLs are not modelled -- there is no ledger sequence here to extend against** -- so the call is recorded and nothing else. What it proves is the argument dispatch". Same for `extend_current_contract_instance_and_code_ttl` (`hostfns.py:488-497`). Pinned by `tests/unit/test_harness_hostfns.py:384-410`, which asserts `store.storage == {}` afterwards. **`extend_contract_instance_and_code_ttl` and `extend_contract_instance_ttl` are in the pin but NOT bound.** | **Zero of S8's five rules are modelled.** §E4. |
| **Events** | An append-only `list[tuple[tuple[int,...], int]]` of `(topic words, data word)` (`hostfns.py:168-170,433-439`). | **No rollback.** Verified: no frame, no snapshot, no journal anywhere in `tests/harness/`. **S9 ("events roll back with failed frames") is entirely unmodelled** — the end-to-end test's `assert host.events == []` after a failed transfer is a statement about the *fixture's* ordering (the check precedes both writes), not about rollback. §E7. |
| **Auth** | **Recorded, always succeeds.** Verbatim (`hostfns.py:68-76`): "The real host TRAPS when the invocation was not authorized. This rig has no authorization state to consult, so `require_auth`/`require_auth_for_args` record the address and return -- **mock-all-auths semantics, and S17's documented tier-2a fidelity line. A contract's auth logic is therefore NOT under test here**; tier 2b is where these can fail." `self.auths: list[int]` holds address words only — **`require_auth_for_args` shape-checks `args` and then DISCARDS them** (`hostfns.py:465-470`), so no args-sensitive auth model can be layered on it without extending the record. | Matches S4's "simplified auth (`mock_all_auths` semantics only)". §E7. |
| **Ledger** | Fixed, settable, **never advances**: `DEFAULT_LEDGER_TIMESTAMP = 1_700_000_000`, `DEFAULT_LEDGER_SEQUENCE = 1_000_000`, "**Arbitrary but deliberately not zero: a zero timestamp is a plausible-looking answer, and a contract that read one without the callback ever running would look like it worked**" (`hostfns.py:112-117`). Nothing in `tests/harness/` ever increments either. `get_ledger_timestamp` returns `val_word(U64(...))` with the `U64`-not-`Timepoint` rationale quoted from `env.py` (`hostfns.py:472-486`). | §E4's crux: **no sequence progression anywhere.** |
| **Footprint** | **Not modelled at all.** S4 demands "**mandatory footprint recording** (tests declare expected footprints; silent passes are not allowed)"; the harness has a `calls` log but no footprint concept. **This is F's row (R1), not E's** — but E's storage model choice constrains it. |
| **`obj_cmp`** | "the one callback here with real semantic content, and it has none of its own: it is `ObjectStore.compare`, which decodes both operands with `chain_value` and hands them to `serpent.types._ordering.val_cmp` -- **the tier-1 oracle the compiler is proven against**" (`hostfns.py:34-43`). | Deliberately mirrors tier 1, including the `Symbol("_")`-vs-`Symbol("A")` ASCII pin that "**may be the WRONG answer about the real host**" (`hostfns.py:53-61`). |

#### B.6.1 The oracle-sharing question, answered

**The object model is MIXED, and that is the answer.** `objects.py:26-34`, verbatim:

> ## What the objects ARE (A9, and where it bends)
>
> Handles are indices into `objects`. `Symbol`, `String` and `Bytes` are stored as
> the tier-1 `serpent.types` instances, so the oracle really is the model for
> them. Vecs and maps are **not**: `serpent.types.Vec`/`Map` are statically typed
> in their element/key/value classes, and a host handed `vec_new()` has no element
> type to give them -- the host's own model of a vec is a sequence of untyped
> `Val` words, so that is what is stored. Scalar `Val`s inside are still decoded
> through `serpent.val`, the one codec.

| Host object | Python payload |
|---|---|
| `SymbolObject`/`StringObject`/`BytesObject`/`AddressObject` | the `serpent.types` instance |
| `VecObject` | `list[int]` — **untyped Val words** |
| `MapObject` | `dict[object, int]` — normalized key → Val word |
| every numeric object (`U64…I256`) | a plain Python `int` |

**Verdict on code sharing: do NOT lift `ObjectStore`/`FullHost` into `src/serpent/`.** Measured:

| Piece | Size | Reusable at tier 1? |
|---|---|---|
| storage model | ~46 lines (`objects.py:648-693`) | **Only in shape** — its value type would change from `int` (Val word) to `ChainValue`. |
| event/auth/ledger/TTL model | ~66 lines (`hostfns.py:431-497`), of which TTL is 10 lines of no-op and auth 30 lines of append-and-return | Same: shape yes, types no. |
| everything else in `objects.py`/`hostfns.py` | **≈1100 lines** | **No tier-1 analogue at all** — object table, interning, `val_word`/`chain_value`, `_RankOnly`/`_VecRank`/`_MapRank`, the four linear-memory constructors, the reverse map `key_word` ("Recorded rather than reconstructed: a container key normalizes to its contents, and rebuilding an object from those would hand back a handle the guest has never seen", `objects.py:450-461`). |
| `i256.py` | 278 lines, pure Python big-int over `serpent.val`, wasmtime-free by design ("The import is local to keep this module usable as a pure arithmetic oracle without pulling in wasmtime", `i256.py:237-238`) | Already reusable, and **already unnecessary** at tier 1 — Python has arbitrary-precision ints. |

**The genuinely shared asset already lives in `src/serpent/`:** `/Users/elliotvoris/Dev/stellar/sdk/py-soroban/src/serpent/types/_ordering.py` (139 lines) — `val_cmp`, the `ChainValue` protocol with `_SCVAL_RANK`/`_cmp_payload`, and the `Struct` protocol. Both tiers already depend on it and `objects.py:50-60` insists it is the only ordering source.

**Two mechanical blockers on any sharing at all, worth knowing even if §E1 chooses not to share:**

1. **`HostError`/`HostTrap` live inside `engine.py`, which does `import wasmtime` at module top.** So importing *any* harness model code drags wasmtime in. Moving those two classes to a wasmtime-free module is a ~30-line move with three import-site edits (`objects.py:86`, `hostfns.py:87`, `i256.py:245`) and is what would make the model importable outside a wasm process.
2. **Two abort models already exist and have no shared base**: the harness raises `engine.HostError` carrying a Val word; tier 1 raises `serpent.errors.ContractError` with a `code: ClassVar[int]` (`src/serpent/errors.py:66-77`), which is what `tests/semantics/test_semantics.py:44-47` asserts against. **E must decide the mapping.** ★§E1/§E8

**Three minimal, well-scoped shared-code moves** (if §E1 chooses a tier-1 model), each independently defensible:

| Move | What | Why |
|---|---|---|
| M-1 | The durability constants `STORAGE_TEMPORARY/PERSISTENT/INSTANCE` (`objects.py:92-94`) into `src/serpent/` beside the frontend's `RawScalarKind.STORAGE_TYPE` immediates; `objects.py` re-imports. | Today `tests/unit/test_harness_hostfns.py:66` imports them **from the harness**, so a tier-1 Env would otherwise define a **third** copy of the same three numbers — and `_scalars.STORAGE_TYPE` is already the pinned source (`recognize.py:962-969` insists on "never a compiler-local constant"). |
| M-2 | The **value-level** key normalization — the branch `key = (value._SCVAL_RANK, value._cmp_payload())` (`objects.py:442-444`) — into a `src/serpent/` function `storage_key(value: ChainValue) -> Hashable` covering scalars, containers recursively, and `Struct`. Tier 1 calls it with a `ChainValue`; `objects.py` keeps its word→value decode and then calls the same function. | **Struct-key equality then has ONE definition** — the exact failure mode `objects.py:36-49` was written to prevent, and the one D6 flags as tier-1-unmodelled. The container branches at `objects.py:435-441` walk word lists and do **not** generalize; they stay harness-side. |
| M-3 | `HostError`/`HostTrap` out of `engine.py` (blocker 1 above). | Makes anything shareable at all. |

**Everything else E's tier-1 model needs, the harness does not have** and would have to be written fresh: a real TTL/ledger model (§E4) and a **journal for frame rollback** (§E7).

#### B.6.2 The existing WASM end-to-end driver E's examples plug into

`/Users/elliotvoris/Dev/stellar/sdk/py-soroban/tests/unit/test_emitter_end_to_end.py:95-107`, verbatim — **three lines, and E's fifth example needs nothing more**:

```python
def build_fixture(path: Path) -> BuildResult:
    """`build_file` with external validation left at its default (ruling E5:
    run `wasm-tools` when it is on PATH, skip it when it is not)."""
    return build_file(path)


def start(path: Path) -> tuple[BuildResult, FullHost, engine.MiniHost]:
    """Build `path` and instantiate it under the full mini host."""
    built = build_fixture(path)
    host = FullHost()
    mini = engine.MiniHost(built.wasm, imports=host.bindings())
    host.attach(mini)
    return built, host, mini
```

Invocation is `mini.invoke(name, *val_words)`; decoding is `host.chain_value(word)` or `decode_val(word, ty, host)`. The same three-line pattern appears in `test_emitter_semantics.py:378-383` (`start_case`) and `test_emitter_fuzz.py:458-459`.

## C. INTERFACE PROPOSALS

Every shape here is a **proposal**, explicitly marked as one. The controller rules in §E.

### C.1 The tier-1 Env model — proposed shape (conditional on §E1 choosing reading (i) or (iii))

The constraint set that makes this shape almost forced:

- `env.py`'s `__all__` (Q16) and every method signature are **frozen public API** — a contract authored today must keep type-checking. So the model cannot be a *different* `Env`; it must be **the same classes with bodies**.
- `Env` has `__slots__ = ()` on every class (`env.py:68,85,121,…,208`) — **there is nowhere to put state today**. Adding `__slots__` entries is the minimal change; adding `__dict__` would be a silent regression on a deliberately-slotted surface.
- Q4 says "**a contract never constructs one**" — so an `Env()` a *test* constructs is a new capability, and the docstring at `env.py:202-206` must change to say so.
- `Address.require_auth()` takes **no `Env`** (Q5) — the model needs an ambient env, a signature change, or a documented tier-1 non-model. §E7.
- D9's `serpent[spec]` precedent: the core (`val`/`types`/`errors`/`decorators`/`env`) is **zero-dep**, enforced by `tests/unit/test_core_zero_dep.py`. A tier-1 model in `env.py` must stay zero-dep — which it trivially can (dicts and lists).

Proposed:

```python
# src/serpent/env.py  --- or src/serpent/env/ as a package (S1); §E1
@dataclass
class _Entry:
    value: ChainValue
    live_until: int | None        # None == "TTL not modelled"; §E4

class Env:
    __slots__ = ("_store", "_events", "_auths", "_timestamp", "_sequence")

    def __init__(
        self,
        *,
        timestamp: int = ...,      # a deliberately-not-zero default, harness precedent
        sequence: int = ...,
        auths: Iterable[Address] | None = None,   # None == mock-all-auths (S4)
    ) -> None: ...

    # --- the frozen surface, now with bodies -----------------------------
    def storage(self) -> Storage: ...
    def ledger(self) -> Ledger: ...
    def events(self) -> Events: ...

    # --- NEW test-facing inspection (naming is §E1's; NOT in serpent.__all__)
    @property
    def published_events(self) -> tuple[tuple[tuple[ChainValue, ...], ChainValue], ...]: ...
    @property
    def recorded_auths(self) -> tuple[tuple[Address, Vec[Any] | None], ...]: ...
```

Five design points, each with its evidence:

| # | Proposal | Evidence |
|---|---|---|
| 1 | **One store, tuple-keyed `(durability: int, storage_key(key))`** — mirroring `objects.py:212-216` exactly, including the comment's reason ("Keyed on the storage type FIRST so the three buckets are visibly separate namespaces"), but with `ChainValue` values instead of Val words. | §B.6; M-2 gives the key function one definition. |
| 2 | **`set()` stores a DEEP COPY of the value.** | This is what makes §E5's escape flip unnecessary (§B.2's closing argument), and it is what the host does (`put_contract_data` serializes a `Val`). `serpent.types` values already support `copy`/`deepcopy`/`pickle` (`Address.__reduce__` is documented, `address.py:4-6`). |
| 3 | **`get()` raises the SAME error the emitter raises**, i.e. a `ContractError` subclass carrying `errors.CODE_MISSING_VALUE = 0xFFFF_FFFD`, and **also returns a deep copy** so a caller mutating the result cannot reach back into the store. | `docs/subset.md:65-70` documents `0xfffffffd` to users; ruling E13 made D emit it. Point 3's copy is the mirror of point 2's. |
| 4 | **`get()` type-checks the decoded value against `ty`** and raises the same class D's narrow check raises. | S3's second sentence, and the S13 drift rule: if tier 1 returns whatever was stored regardless of `ty`, the emitter's narrow check has no tier-1 twin and the differential cannot see it. |
| 5 | **Inspection surfaces are NEW names, not in `serpent.__all__`.** `serpent.__all__` is pinned by `tests/unit/test_public_api.py` and "is the **authoring** surface a contract resolves names against" (M1-C A22, restated in the M1-D dossier §E6). A contract must not be able to name `published_events`. | Precedent: `serpent.emitter` is deliberately not in `serpent.__all__` (D11). |

**Where it lives.** Three options, and the roadmap forecloses one:

| Option | Verdict |
|---|---|
| `src/serpent/env.py` (grow the module) | Simplest; keeps the frozen `__all__` intact; keeps zero-dep. **Recommended in §E1.** |
| `src/serpent/env/` (promote to a package, per S1) | Spec-sanctioned and probably right eventually (S1 names `storage/events/auth/ledger/logging` as its contents), but a package split is a refactor with no behavioural content. E can do it *and* keep `env.py`'s import surface identical. |
| `src/serpent/testing/` | **Foreclosed: `serpent.testing` is F's (R3).** E may not squat it. Any pytest *fixture* wrapping the model belongs in `tests/`, and F productizes. |

### C.2 `@contractevent` metadata + `SCSpecEventV0` — proposed shape

**The smallest sound convention (the §E2 recommendation, spelled out).** Mirror Rust exactly where Rust has already made the choice, and use serpent's existing machinery for the rest.

```python
# src/serpent/decorators.py
def contractevent(
    cls: type[_T] | None = None,
    *,
    topics: Sequence[str] | None = None,      # prefix topics; max 2 (XDR cap, verified)
    data_format: Literal["map", "vec", "single-value"] = "map",
) -> ...: ...

# The per-field topic marker, mirroring Rust's #[topic]:
class Transfer(Event):
    from_: Annotated[Address, topic]          # OR: topic(Address) — §E2 weighs the spellings
    to: Annotated[Address, topic]
    amount: I128                              # unmarked -> data
```

Metadata record grows from `{"kind": "event", "fields": [(name, annotation)]}` to:

```python
{"kind": "event",
 "fields": [(name, annotation, location)],     # location in {"topic", "data"}
 "prefix_topics": ("transfer",),               # default: snake_case(cls.__name__)
 "data_format": "map"}
```

**Defaults, taken verbatim from Rust [verified live via DeepWiki]:** if `topics=` is omitted, the sole prefix topic is **the struct name in snake_case**; if no field carries the marker, **all fields are data**; `data_format` defaults to **`"map"`** (field-name-`Symbol` keys, **sorted alphabetically**); `"single-value"` is legal only with exactly one non-topic field.

Why this is the smallest sound option, point by point:

| Concern | Why this convention answers it |
|---|---|
| "Does it lie in the spec?" (Q6's whole objection) | No: every `SCSpecEventV0` field now has an authored source — `prefix_topics` from `topics=` or the snake_case default, `location` per param from the marker, `data_format` from the argument. **Nothing is guessed.** |
| S10 / Q14 / D4: "`topic[0]` is conventionally a short `Symbol` naming the event" | The snake_case-class-name default **is** that Symbol, and it is emitted as `prefix_topics[0]`. The existing `_is_short_symbol` check (`recognize.py:1195-1206`, `val.fits_symbol_small`) already validates ≤ 9 chars — reuse it, with `SPT3019`'s message. |
| "map" default's key order | **Identical to what C already does**: `MakeStruct.fields` are pre-sorted ascending as byte strings by C (M1-D dossier C9), and D's `MakeMap` LM-form gate (ruling E12) requires exactly "`key_ty` Symbol AND all-`Const` keys" — which a field-name-keyed map satisfies. **Zero new emitter work.** |
| "does it need a new IR node?" | **No, if E desugars in the frontend** (E2-d): `Transfer(from_=a, to=b, amount=x).publish(env)` becomes the *existing* `HostCall("contract_event", args=(MakeTopics(Const(Symbol("transfer")), a, b), MakeMap(...)))`. `MakeTopics` and `MakeMap` both already exist and are already lowered. **The emitter changes not at all** — which is worth a great deal given R4 (E is concurrent with F/G) and P4 (D's fail-safe was built expecting a new node). |
| XDR caps | `prefix_topics ≤ 2`, param `name ≤ 30`, `lib ≤ 80`, `name` an `SCSymbol` — all pre-validated source-located per `sections.py:16-21`'s discipline and R5's "pre-validate at every nominally-fallible soroban-sdk boundary". |

**Entry order in `contractspecv0` is an open sub-question ★§E2.** `build_spec_entries` pins "1. `UDT_STRUCT_V0`, in `types` order, 2. `UDT_ERROR_ENUM_V0`, in `types` order, 3. `FUNCTION_V0`: `__constructor` first, then declaration order" (`sections.py:145-161`), and that order is tested independently of the golden bytes. Events need a slot. **The safest is a fourth position appended AFTER functions**, because that cannot perturb any existing golden's byte layout — and `tests/goldens/` carries a RUST-SDK-BYTE-COMPAT spec golden for spike1 that must not move.

**`spec_inputs.events` already exists and already carries them.** B10's note: "`CompiledModule.spec_inputs` keeps `events` in its own field precisely so D cannot pass them" (`frontend.py:219-241`). E's job is to make `module.assemble` → `sections.spec_payload` pass that field through, which is a plumbing change in a field designed for it.

### C.3 What each consumer takes from E

| Consumer | Needs from E | Hazard if E is vague |
|---|---|---|
| **F** (testing tiers) | (a) The **five example contracts** as the tier-2a/2b/differential corpus — F's differential runner runs "2a vs 2b vs testnet". (b) **Whatever Env model E ships becomes a THIRD leg on the S13 differential** — F must then prove tier-1-model vs mini-host vs real host agree on storage, events and auth, not just on arithmetic. (c) The `SC_SPEC_ENTRY_EVENT_V0` bytes for a golden. (d) If §E4 models TTL, F owns proving it against the real host — including S8's five rules, none of which any tier models today. | **R4 says E and F are concurrent.** If E's model lands late or changes shape, F's differential runner is built against a moving target. **E should freeze the model's *observable surface* (what a test asserts on) in its first task, before its internals.** |
| **G** (CLI + ship + the M1 gate) | (a) **One of E's five examples is what gets deployed to testnet at M1's end** (R2) — so at least one example must be genuinely deploy-worthy, not a test fixture with a contrived shape. (b) `examples/` as a docs-site source (S1 calls them uv workspace members; the docs site is G's). (c) The `docs/subset.md` regeneration E forces (see below). (d) `sandbox/README.md:17-20`'s stale text. | G's CI adds `examples/` to the gates — **§E6 must say whether `examples/` is inside `mypy --strict`'s `files` and `ruff format --check`'s scope**, because `pyproject.toml:59-68` currently lists only `["src", "tests"]` and `ci.yml:34-37` formats only `src tests`. An example that does not type-check under strict mypy would be a bad advertisement for a zero-plugin-strict-clean SDK. |

**The `docs/subset.md` regeneration is a hard, mechanical consequence of any E frontend edit.** `docs/subset.md:1-8`, verbatim:

```
GENERATED FILE -- do not hand-edit.
Generated by: python -m serpent.compiler._render_docs
Source of truth: src/serpent/compiler/codes.py, tests/must_reject/,
src/serpent/compiler/recognize.py (dossier S14). A byte-drift test
(tests/unit/test_subset_docs.py) fails if this file and its generator
ever disagree; the failure message names the regeneration command.
```

The generator compiles all 95 `tests/must_reject/*.py` fixtures **live**, so rendered messages are real. **Any change to `recognize.RECOGNIZED`, `codes.REGISTRY`, or a `must_reject` fixture forces a regenerate-and-commit**, and `tests/unit/test_subset_docs.py` fails until it happens. `SPT1032`'s two rendered lines are at `docs/subset.md:830-853`, and they are **the only two "sub-plan E" mentions in the entire 2356-line document** (grep-verified). §E2's flip rewrites both.

### C.4 The honest-boundary table — what stays unimplemented after E (the §E8 inventory)

The complete `NotImplementedError` census E is measured against, verified by grep:

| Location | Count | Message | E's disposition |
|---|---|---|---|
| `src/serpent/env.py` | **17 raises** (lines 72, 94, 98, 107, 111, 126, 137, 148, 157, 160, 163, 177, 181, 198, 211, 214, 217) | `"sub-plan E"` | **All 17 are E's, under reading (i)/(iii). Under reading (ii), all 17 stay.** ★§E1 |
| `src/serpent/types/address.py` | **2 raises** (lines 90, 94) | `"Env runtime; sub-plan E"` — **a different string** | ★§E1/§E7 |
| `src/serpent/types/address.py` | 2 raises (lines 82, 86) | `"host object form; sub-plan B"` | **Not E's.** `to_val`/`from_val`; B shipped without them and nothing needs them at tier 1. |
| `tests/harness/objects.py:113-116` | 1 | `"container comparison; sub-plan B -- tier 1 defers the payload order for Vec/Map (A15: no inventing an order the host has not been differentially checked against)"` | **Not E's** — A9/A15's deliberate partial model; F's differential. |

**Tests that go RED when the raises are implemented, and must be rewritten in the same commit series:** `tests/unit/test_address.py:139,141`; `tests/unit/test_decorators.py:337, 392-414, 428, 441, 443, 499`. **These assert the `NotImplementedError`s** — they are the pinning tests for Q2's "pure type surface" claim, and they are the mechanical proof that E's change is user-visible.

**Stays unimplemented after E regardless of §E1 (the honest list):**

| Surface | Why | Frontend behaviour today |
|---|---|---|
| `env.logs()` / `log_from_linear_memory` | S2: named in spec §3's `env/` bullet but **has no authored surface at all**. M1-C dossier §C.4 puts it under "not lowerable in M1 … all M2". | `SPT1033`, help "this Env surface is deferred to M2; there is no rewrite available yet" |
| `env.call()` / `env.try_call()` | S17: M2 ("cross-contract calls"). **And S6 says the token example is "without cross-contract" precisely because of this.** | `SPT1033` |
| `env.crypto()`, `env.prng()`, `env.deployer()`, `env.current_contract_address()` | S17: M2 | `SPT1033` |
| `Ledger.version()`, `Ledger.network_id()`, `Ledger.max_live_until_ledger()` | `_LEDGER_FUTURE_METHODS`, M2 | `SPT1033` |
| `U256`/`I256` authoring surface | D2: "U256/I256 deferred to M2" | rejected at annotation resolution |
| `Timepoint`/`Duration` **arithmetic** | ★§E3 — the one item on this list E may legitimately move |
| `Address.to_val()`/`from_val()` | "sub-plan B", and nothing needs them | — |
| **Frame rollback of events/storage** | S9/S5 — **no tier models it today** ★§E7 |
| **Footprint recording** | S4 makes it mandatory for tier 2a; **that is F's row** (R1) |
| **Budget metering** | S4: "Explicitly lower fidelity: no budget metering" — tier 2b only |
| **Real auth trees** ("auth trees that consume storage-written nonces", S5) | tier 2b only; S4 pins tier 2a at "`mock_all_auths` semantics only" |
| **Instance-storage flush semantics** (S7: "flushed at frame exit with early flush on re-entrant self-call") | Re-entrant self-call needs `call`, which is M2. **The flush is unobservable in M1** — worth stating explicitly rather than silently. |

## D. TESTING DESIGN INPUTS

Tier ownership is F's (R1), but S3/S6/S13 put specific proof obligations inside E.

### D.1 The differential story E adds — and the hole it fills

`tests/semantics/cases.py` holds **59 cases [verified live by importing the table]**: by `kind`, `value` 22 / `reject` 17 / `contract_error` 12 / `trap` 8; by `frontend`, `accepts` 35 / `rejects` 20 / `not_expressible` 4; `tier1_only=True` on 4. The in-scope predicate the M1-D WASM differential uses (`tests/unit/test_emitter_semantics.py:110-127`) yields **`IN_SCOPE_COUNT = 35`**, and the same predicate is restated at `tests/unit/test_harness_hostfns.py:916-921` "which is what makes the differential's skip list empty".

**The hole, stated plainly: the frozen table has ZERO Env cases.** Verified — every case is a pure expression over chain types; `wrap_case` *takes* `env: Env` (`test_frontend_semantics.py:73-81`) but **no case ever calls it**. `cases.py:64-71` admits the reason: "`BadArgument` is declared but not yet raised anywhere in the runtime surface (**no operation reaches for it before sub-plan B/E**)".

So the S13 drift rule has never been tested over storage, events, auth or the ledger. **That is precisely the surface E is adding.** Two candidate responses:

| Option | Cost | Note |
|---|---|---|
| **Extend `cases.py`** with Env cases | `SemCase.source` is documented as "single expression, eval-able in the chain-type namespace AND compilable by sub-plan D in a method body" (`cases.py:100-130`). **Storage is stateful — a single expression cannot express `set` then `get`.** The dataclass would need a new shape (a setup sequence), which is a change to a *frozen* table. | High friction, and `test_frontend_semantics.py:31` asserts `>= 40` so growth is fine, but the shape is the blocker. |
| **A second, E-owned table** of stateful scenarios, run against (a) the tier-1 Env model and (b) compiled WASM under `FullHost` | No change to the frozen table. Mirrors what `test_emitter_end_to_end.py` already does informally for `token_style` (mint/transfer sequences). | **Recommended.** Note the honest limit: with `FullHost` as the WASM side, this differential compares **two models E/D wrote**, not a model against the host — F's tier-2b is the only place it becomes evidence (D11). |

### D.2 The five examples' acceptance harness — nearly free

Adding each example to `FIXTURES` (`test_emitter_end_to_end.py:79-83`) buys, with no new test infrastructure:

1. **`build_file` succeeds** and the module validates internally + under `wasm-tools` when on PATH (ruling E5).
2. **The size tripwire** and **`runtime_parts_needed ⊆ runtime_parts_linked`** (`test_emitter_fuzz.py`, importing the same list).
3. **A `tests/goldens/wasm/*.wat.txt` disassembly golden** — SELF-SNAPSHOT class (B12): "a change to lowering arrives as a reviewable diff instead of a silent behavioural change".
4. **The host-fn inventory check** (`tests/unit/test_harness_hostfns.py:940-960`): every host function the contract reaches must have a `FullHost` callback. **This is the test that catches E adding a surface the mini-host cannot run** — e.g. if an example calls `temporary().extend_ttl`, the binding exists (`extend_contract_data_ttl`) but if an example ever reached `extend_contract_instance_and_code_ttl` or `extend_contract_instance_ttl`, **those are in the pin and NOT bound** (§B.6) and this test fails loudly. Good.
5. A **`start(path)` run**: build → `FullHost()` → `MiniHost` → `attach` → `invoke`.

**What "passing" must mean per R1, spelled out as three obligations per example:**

| Obligation | Mechanism |
|---|---|
| **Compiles** | `compile_module` with no diagnostics. |
| **Runs at tier 1** (new, and only under §E1 reading (i)/(iii)) | Construct the contract class, construct an `Env`, call methods, assert on state/events/auths. **No engine.** |
| **Runs as WASM** | The `start(path)` pattern above, asserting the *same* answers as the tier-1 run — which is the S13 differential applied to whole contracts. |

### D.3 Goldens E adds, under the three-class discipline (B12)

| Golden | Class | What it proves |
|---|---|---|
| `SCSpecEventV0` bytes for the event example's `@contractevent` | **RUST-SDK-BYTE-COMPAT** if compared against a locally built Rust artifact with the equivalent `#[contractevent]`; otherwise **SELF-SNAPSHOT** | The strongest available check that §E2's convention produces the *same* spec Rust would. **Worth doing** — S8 permits spec-section byte comparison (it forbids only `contractmetav0` and the code section), and this is the one place E can be checked against an independent implementation. |
| Per-example `.wat.txt` disassembly | SELF-SNAPSHOT | Reviewable lowering diffs. |
| The existing spike1 `contractspecv0` golden | **ON-CHAIN-anchored** | **Must not move.** §C.2's "append events after functions" recommendation exists to guarantee this. |
| The `sandbox_*` byte-identity tests (`test_emitter_end_to_end.py:337-388`) | SELF-SNAPSHOT | Move with the files if §E6 relocates them. |

**Attribution rule to restate (B12):** a comparison against E's own previous output is SELF-SNAPSHOT and "must never be cited as evidence that serpent's output is *correct*".

### D.4 Tests that must be rewritten, not merely added

| Test | Why | Under which §E ruling |
|---|---|---|
| `tests/unit/test_address.py:139,141` and `tests/unit/test_decorators.py:337, 392-414, 428, 441, 443, 499` | They **assert the `NotImplementedError`s**. | §E1 reading (i)/(iii) |
| `tests/unit/test_frontend.py:700-726` `test_a_container_built_up_in_a_loop_compiles` | **Flips from pass to fail** if the escape list flips (§B.2). Its docstring says the pattern "must actually compile". | §E5 |
| `recognize.py:2424-2440` (`SPT1034`'s `help`) + `codes.py:318-326` | Currently **recommend the pattern the flip forbids**. | §E5 |
| `tests/must_reject/constructs/event_instance_publish.py` + `docs/subset.md:830-853` | The `SPT1032` fixture and its two rendered doc lines. | §E2 |
| `tests/semantics/cases.py:400-428` (4 time cases asserting `TypeError`) + `test_frontend_semantics.py:147-150` (their four `SPT3005` entries) + `expr.py:234`'s `_TIME_ALGEBRA_NOTE` + `tests/unit/test_numeric.py:457` | Move together if time algebra lands. | §E3 |
| `tests/unit/test_harness_hostfns.py:384-410` `test_the_ttl_calls_are_recorded_no_ops` | Asserts `store.storage == {}` after a TTL call — i.e. it **pins the non-model**. If E models TTL at tier 1 only, this stays; if E also models it in the mini-host, it changes. | §E4 |
| `tests/unit/test_recognize_env.py:273-302` (`_DOSSIER_C4_INVENTORY`) | Asserts `ENV_HOST_FN_TARGETS` equals a hand-transcribed inventory **both directions**. Any new env host fn E reaches must be added here. | §E2/§E4 |
| `tests/unit/test_public_api.py` | Pins `serpent.__all__`. Adding anything to it is a deliberate act (§C.1 point 5 says don't). | §E1 |
| `docs/subset.md` byte-drift (`tests/unit/test_subset_docs.py`) | Fails until regenerated after any `recognize.py`/`codes.py`/`must_reject` change. | §E2, §E3, §E5 |
| `sandbox/README.md:17-20` | Already stale (§B.5.2). | any |

### D.5 Fuzz and property obligations

1. **The tier-1 Env model as a property target**: `set(k, v); get(k, T) == v` over Hypothesis-generated `ChainValue` keys **including structs and containers** — which is exactly D6's declared hole ("struct storage keys are not modelled in tier 1's ordering", `docs/subset.md:79-88`) and the failure mode `objects.py:36-49` was written to prevent. **This is the highest-value new property in E.**
2. **Deep-copy isolation** (§C.1 points 2/3): `v = Vec(U32, [...]); env.storage()...set(k, v); v.push_back(x); get(k, Vec) != v`. **If this property holds, §E5's escape flip is unnecessary; if it cannot be made to hold, the flip is mandatory.** State it that way in the plan — it is the decision procedure, not just a test.
3. **Whole-contract differential fuzz**: random invocation *sequences* against an example, compared tier-1-model vs WASM. Closes the M1-C fuzz gap the C attention file recorded (item 4: "Fuzz generators never spell `env.ledger()`/`temporary()`/`extend_ttl`/`require_auth_for_args`/most container methods") — **which names exactly the surfaces E adds**, and which M1-D's dossier already said "D should close that gap rather than inherit it" (M1-D §D.5.3). Verify whether D actually did; if not, E inherits it a second time.
4. **`@contractevent` metadata round-trip**: for a generated event class, the emitted `SCSpecEventV0` decodes and its `params` locations/`data_format`/`prefix_topics` match the declaration. Plus the XDR caps as negative controls (`prefix_topics` of 3 → a source-located serpent error, **never** a bare `stellar_sdk` `ValueError` — R5).

## E. OPEN QUESTIONS FOR THE CONTROLLER

Ten questions. Each has options and **one** recommendation with evidence. The dossier recommends; the controller rules.

---

**E1 — What "wired end-to-end" means, and the tier-1 Env model's architecture (the biggest question in E).**

The three readings and their textual support are §B.0's table; do not re-derive them here. The sub-questions this one ruling settles: does E ship an in-memory Env model at all; if so does it share code with `tests/harness/`; and where does it live.

Forces:
- **For a model**: the M1-C attention file's own words are "**real tier-1 behavior**" (X1), and `note_escapes:2170-2177` grounds the escape exemption on tier 1's inability to run those paths. `docs/subset.md:65-70` already documents `0xfffffffd` **to users** as what a defaultless `get` does. 19 `NotImplementedError`s advertise E by name in shipped public docstrings, and 8 tests pin them.
- **Against a model**: `env.py:3-6` and `:202-206` both say **"a real host at test time"** / **"the host bridge"** (Q1/Q4), not "a Python model". Spec §8's tier 1 has **no** contract-execution role (S3) and names tier 2a "the fast dev loop" (S4). S5 is a direct warning against exactly this artefact: a hand-written mock of host semantics "has *silent false green* as its failure mode", and it lists **"frame-rollback of events, TTL asymmetries, instance-storage flush rules"** — three things a tier-1 model would have to fake (§B.6 shows the mini-host fakes all three today). S13 makes a third implementation of host semantics **the named highest internal risk**. S18: "Scope creep toward 'real Python' — the subset spec is the contract."
- **On sharing**: verified negative. ~1100 of the harness's ~1300 model-adjacent lines are handle/Val-word/wasm plumbing with **no tier-1 analogue**; the genuinely shared asset (`types/_ordering.py`) is already in `src/serpent/`; and `src/serpent/` cannot import `tests/` regardless (the harness is dev-only, and `pyproject.toml`'s zero-dep test enforces the core's import graph).
- **On location**: `serpent.testing` is **F's** (R3) and E may not squat it.

Options: **(a)** reading (ii) — no model; E ships `Event.publish`, the spec entry, the examples, the time-algebra ruling, and *documents* that the `Env` bodies stay unimplemented because tier 2a is the dev loop. **(b)** reading (i)/(iii) — a full in-memory model in `src/serpent/env.py`, deep-copying, raising the emitter's own codes, with new inspection properties not in `serpent.__all__`. **(c)** (b) but in a promoted `src/serpent/env/` package per S1. **(d)** a *deliberately minimal* model: storage + events + auth-recording + ledger, with TTL and rollback **explicitly non-modelled and documented**, in `env.py`, plus the three narrow shared-code moves M-1/M-2/M-3.

**Recommendation: (d).** Rationale: it satisfies X1's premise (the escape exemption's justification stops being true, so §E5 becomes answerable on the merits rather than by default); it makes the 19 advertised promises true, which matters because they are *shipped public docstrings*, not internal notes; it gives the five examples the "run at tier 1" leg R1's "passing" most plausibly means; and it caps the S5/S13 risk by **naming the non-models in code** rather than approximating them — which is the same "reject rather than approximate" discipline spec §1 sets. Choose `env.py` over the package (option c) because the package split has no behavioural content and can be done any time without touching the import surface; and take M-1/M-2 because otherwise the durability constants get a **third** definition and struct-key equality gets a **second** — the exact drift S13 forbids. **Explicitly flag to the controller:** (d) still creates a third implementation of host semantics, and the honest mitigation is (i) §E9's differential, (ii) a docstring at the top of the model that says, in S5's own words, that silent false green is its failure mode and F's tier 2b is the gate, and (iii) **no footprint, budget, rollback, TTL or auth-tree modelling** (§C.4). If the controller prefers (a), then §E5 is a no-op, §E4 is a no-op, §E7 shrinks to "nothing", and E becomes a much smaller sub-plan centred on §E2 and §E6 — a legitimate reading of the roadmap row, and the one the `env.py` docstring's own words support best.

---

**E2 — `Event.publish(env)`: the full cross-layer decision (X2/X3/X6).**

The five layers, the XDR shape [verified live], and the Rust convention [verified live] are §B.4 and §C.2; the smallest sound convention is spelled out there. What the controller rules on:

(i) **The topic/data split convention.** Options: **(a)** mirror Rust — a per-field `topic` marker plus `topics=`/`data_format=` decorator arguments, defaults `snake_case(ClassName)` / all-fields-data / `"map"`; **(b)** first-N-fields-are-topics with an `n_topics=` argument; **(c)** no per-field split at all — the whole event is data and the only topic is the name Symbol (i.e. `data_format="map"`, zero `TOPIC_LIST` params); **(d)** keep the reject and defer the whole thing to M2.
(ii) **The marker spelling** if (a): `Annotated[Address, topic]` vs a `topic(Address)` wrapper vs a class-level `_topics = ("from_", "to")`. Note `decorators._is_contract_annotation` (`decorators.py:407-419`) accepts "a chain type, a `@contracttype` struct, or `X | None` of one" — and **`Annotated` is NOT in that set today [verified live: `_is_contract_annotation(Annotated[Address, "topic"])` is `False`; `typing.get_origin` returns `typing.Annotated`, not `Address`, so the existing `Union` branch does not catch it]**. So (a)+`Annotated` needs one new branch in `_is_contract_annotation` (unwrap `Annotated` to its first arg, recurse) plus the matching unwrap in `spec/typemap.py`'s `to_spec_type` and in the frontend's annotation resolver (`stmt.py`'s resolver, and `decls.py` for field annotations). **Four small sites, each a strict-unwrap — but four, not one.** A class-level `_topics = ("from_", "to")` needs zero annotation-machinery changes at all, which is the argument for it if the controller wants the smaller edit.
(iii) **Entry order** in `contractspecv0` (§C.2's open sub-question).
(iv) **`SPT1032`'s fate** under D9's "no renumber, no delete, no meaning reversal": the row stays; its `message_intent` ("deferred to sub-plan E; use env.events().publish(topics, data)") becomes false. Options: retire the code to `NO_FIXTURE_ALLOWLIST` with a reachability reason (D9's own precedent for `SPT1009`/`SPT4018`/`SPT7003`), or give it a new honest meaning (e.g. "an event published with a shape the convention cannot express").
(v) **The `token_style.py` revert** — and whether **both** spellings ship.

**Recommendation: (a) with `Annotated[T, topic]`; events appended AFTER functions in entry order; `SPT1032` retired to `NO_FIXTURE_ALLOWLIST` with the reason "the form it rejected is now supported (sub-plan E)"; the frontend DESUGARS `Event.publish(env)` into the existing `HostCall("contract_event", (MakeTopics(...), MakeMap/MakeVec/value))` so the IR and the emitter change not at all; `token_style.py` reverted to `Transfer(...).publish(env)` AND a second fixture keeping the `env.events().publish` spelling.**

Evidence for each half: (a) is the only option under which **nothing in `SCSpecEventV0` is guessed** — which is the literal condition Q6 set for lifting the deferral ("Guessing would ship a spec that is valid XDR and a lie"); it is what Rust does, which matters because `stellar-contract-bindings` and the CLI render serpent's spec and an ecosystem-divergent convention would render as a differently-shaped event; and its `"map"` default's sorted-field-name-Symbol keys are **byte-for-byte the layout C already produces for a struct** (M1-D C9) and that D's `MakeMap` LM gate already accepts (ruling E12), so the emitter cost is genuinely zero. `Annotated` over a wrapper because `Annotated[Address, topic]` is the standard Python idiom for "this type, plus a marker", keeps the field's *static* type exactly `Address` for mypy, and cannot collide with any chain type — at the verified price of **four unwrap sites** (`_is_contract_annotation`, `to_spec_type`, and the two annotation resolvers), each a two-line strict unwrap. **If the controller prefers the smallest possible edit, a class-level `_topics = ("from_", "to")` needs none of those four** and is the honest alternative; its cost is that the marker is no longer next to the field it marks, which is what makes `#[topic]` readable in Rust. Appending events last because the existing spike1 `contractspecv0` golden is ON-CHAIN-anchored and must not move (§D.3). Retiring `SPT1032` rather than repurposing it because `NO_FIXTURE_ALLOWLIST` exists for exactly this and D9's reversal note says "any code that later becomes reachable just gets its fixture and leaves the allowlist" — the symmetric move. Desugaring because R4 makes E concurrent with F, and a new IR node would force an emitter change in a layer D just froze and whose fail-safe (`SPT8004`/`CODE_UNSUPPORTED_AT_RUNTIME`, P4) is a *loud crash*, not a soft landing. **Both spellings because D5 called `env.events().publish()` "the supported form", not the only one**, and because `token_style` is byte-identity-anchored: dropping the canonical spelling from the corpus would leave `contract_event`'s heterogeneous-topic path (D4's canonical `(Symbol, Address, Address)`) with **zero** fixture coverage — it has exactly one call site in the whole tree today.
**Against (c):** it is the cheapest, and it is a real option if the controller wants E small — but it makes every indexer filter on the event name only, and D4 froze heterogeneous topics precisely because "canonical token topics are `(Symbol, Address, Address)`". **Against (d):** the deferral has now been carried by three sub-plans (B10 → C's E12 → D), it blocks `SC_SPEC_ENTRY_EVENT_V0` entirely, and D4's reversal note says the authoring surface is "cheap now, **breaking after docs/examples**" — **E ships the docs and examples, so E is the last cheap moment.**

---

**E3 — Time algebra (X5/D2's deferred decision).**

The candidate ops, their type rules and their cross-layer cost are §B.3. What is already true at every layer: **comparisons, truthiness, Val forms and the `to_u64`/`from_u64` bridges all work** (`numeric.py:538-549`, verbatim) — only arithmetic is absent. And `_TimeValue`'s own docstring names the bridge as E's intended path: "**sub-plan E needs the bridge for `env.ledger().timestamp()`**".

Forces: `Ledger.timestamp()` returns **`U64`, not `Timepoint`** (Q13/D3), so **nothing in E's own deliverables forces time arithmetic** — the ledger work uses `U64` arithmetic, which already exists. Against landing it: `Timepoint + Duration` and `Timepoint - Timepoint` are **heterogeneous**, and (a) `_ChainArith`'s `_operand`/`_wrap` are `Self`-in/`Self`-out and `_operand` raises `TypeError` on *any* foreign chain type (`numeric.py:303-338`), so they need new cross-type methods returning a different class; (b) every arithmetic `Binary` in the shipped IR carries operands that "both share `ty`" (`ir.py:247`) and the M1-D emitter's arithmetic was built on that invariant — **so this is an IR-contract change in the layer D just froze**; (c) there is no time-arithmetic runtime part in the ratified namespace (P7); (d) the blast radius is six sites (§D.4's row). For landing it: D2's reversal note — "trivial (re-allow ops); **reverse direction would break contracts**" — means adding is cheap *forever* and removing never is, which argues for taking the time to get it right rather than for doing it now. And S17 puts "**TTL helpers**" in M2, which is the closest analogue to time helpers.

Options: **(a)** land the full algebra (`D+D→D`, `T+D→T`, `T-T→D`, `T-D→T`, plus `D-D→D`) across tier 1, frontend, IR and emitter. **(b)** land only the **homogeneous** ops (`D+D→D`, `D-D→D`), which fit `_ChainArith`'s existing shape and the IR's same-`ty` invariant exactly, and defer the heterogeneous ones. **(c)** defer all of it to M2, with the `to_u64`/`from_u64` bridges as the **documented, tested** path and the four `cases.py` reject cases retained.

**Recommendation: (c) — defer to M2, and make the deferral load-bearing rather than passive.** Rationale: nothing in row E needs it (Q13 routes the ledger through `U64`); the heterogeneous ops require an IR-contract change one sub-plan after D froze that contract, with a concurrent F building against it (R4); the omission is already *documented at three layers* with a rewrite that works (`_NO_TIME_ARITH`'s message names the bridges, `_TIME_ALGEBRA_NOTE` names the decision, four frozen semantics cases pin it); and D2's asymmetric reversal cost means deferring costs nothing later while shipping a half-designed algebra cannot be undone. **What "load-bearing" means concretely, and what E should ship instead:** (1) update `_TIME_ALGEBRA_NOTE` (`expr.py:234`) and `_NO_TIME_ARITH` (`numeric.py:53-61`) to say **M2**, not "sub-plan E" — leaving them pointing at a sub-plan that has closed is exactly the stale-promise problem `sandbox/README.md:17-20` already demonstrates; (2) add a `must_reject` fixture and a worked docs example of the **bridge** pattern (`Timepoint.from_u64(env.ledger().timestamp().add(...))`-shaped), so the rewrite the diagnostics recommend is one users can copy; (3) record in `decisions.md` that E *considered and declined*, with (b) named as the cheap first increment whenever M2 opens. **If the controller wants ops now, take (b) and not (a)** — (b) needs no IR change, no new runtime part, and no cross-type checker rule, and it is the half of the algebra with an unambiguous answer.

---

**E4 — TTL semantics at tier 1: model faithfully, or record-and-ignore?**

S8's five rules, verbatim: "persistent extension past max **clamps**, temporary **traps**; live-until arithmetic carries `-1`; **extensions never reduce**; **extending a dead entry errors**." What is modelled today: **none of them, anywhere.** The mini-host records the call and nothing else, and says why (`objects.py:675-688`): "**TTLs are not modelled -- there is no ledger sequence here to extend against** -- so the call is recorded and nothing else. What it proves is the argument dispatch". Its pinning test asserts `store.storage == {}` afterwards. **And nothing in the repo — no fixture, no sandbox contract, no semantics case — calls `extend_ttl` at all** (grep-verified). `docs/subset.md` mentions TTL only in the recognized-surface table at `:62-63`.

**What tier 1 can honestly model without ledger progression:** exactly four of the five rules, and they are the four that are *arithmetic on numbers the model owns*, not consequences of time passing:

| S8 rule | Modellable at tier 1 with a fixed `sequence`? |
|---|---|
| extensions never reduce | **Yes.** `live_until = max(live_until, sequence + extend_to)` is pure arithmetic. |
| the `threshold` guard (extend only if remaining < threshold) | **Yes.** `if live_until - sequence < threshold`. |
| persistent extension past max **clamps** | **Only with a max.** The host fact is `get_max_live_until_ledger` (`x.8`) — **which is on the M2 `SPT1033` list and unreachable in M1** (A.7). So the max would be a serpent-chosen constant, i.e. **a guess**, which is the thing Q6 forbids. |
| temporary extension past max **traps** | Same problem, same source. |
| extending a **dead** entry errors | **Only if entries can die** — which needs sequence progression. With a fixed sequence, nothing ever dies, so this rule is **unreachable**, not "modelled". |

Options: **(a)** faithful: model `live_until`, advance the sequence on demand (an `env.advance(n)` test hook), expire entries, and pick a max. **(b)** record-and-ignore, mirroring the mini-host exactly, with the same docstring admission. **(c)** **partial-and-honest**: model `live_until` per entry and the two arithmetic rules (never-reduce, threshold), expose `env.sequence` as settable and optionally advanceable, **make expiry observable when the sequence is advanced past `live_until`**, and **refuse to model the clamp/trap at all** — `extend_to` above any bound is accepted as-is, with a documented note that the max is a host fact M1 cannot read. **(d)** (c) minus expiry (no advancing).

**Recommendation: (c).** Rationale: it models every rule whose inputs the model actually owns and **names the one it cannot** (the max), which is the discipline `objects.py`'s and `sections.py`'s docstrings both already set; it makes the allowance-token example (§B.5.3) genuinely demonstrable — an allowance that expires is the whole point, and (b) would leave `extend_ttl` with the same zero coverage it has today, in the sub-plan whose row names "storage tiers/**TTL**"; and expiry-on-advance is cheap (a lazy check in `get`/`has`) and is the only way a test can show a contract handling an expired entry. **Do NOT touch the mini-host's TTL no-ops** — S17/D11 make the mini-host's fidelity F's problem, its non-model is documented and pinned, and changing it would put a second TTL model in the tree. **Record explicitly, as a named carried obligation to F:** the clamp/trap asymmetry and the dead-entry error are **unproven at every tier**, and `get_max_live_until_ledger` must be lifted out of M2 (or the max hard-coded from a protocol constant with a drift test) before either can be. Also note for the plan: `extend_contract_instance_and_code_ttl` and `extend_contract_instance_ttl` **exist in the pin and are NOT bound by the mini-host** (§B.6) — an example must not reach them, and `test_harness_hostfns.py:940-960` will catch it if one does.

---

**E5 — The escape-list flip (X1): what it actually means, and whether it must happen.**

Everything verified about the mechanism, the four edit sites, the direction of the change, and the test that flips is §B.2. The three findings the controller needs:

1. **It NARROWS accepts.** Code that compiles today starts being rejected with `SPT1034`.
2. **The pinned proof is `tests/unit/test_frontend.py:700-726`**, whose docstring says the pattern "**must actually compile**" because otherwise "the diagnostics [would be] recommending something the compiler refuses" — and `SPT1034`'s `help` text does recommend it.
3. **It is only *necessary* if the tier-1 model stores a reference.** The exemption's justification is "tier 1 has no shared-object model to diverge from"; a model that **deep-copies on `set()`/`publish()`/`require_auth_for_args()`** has no shared-object model either, and the justification survives verbatim.

Options: **(a)** flip all four sites; accept that a container mutated anywhere in a function that also stores/publishes it becomes `SPT1034`; rewrite the pinned test, the `help` text and the registry intent; regenerate `docs/subset.md`. **(b)** **do not flip; make the model deep-copy**, and *amend* `note_escapes`' docstring to record the new reason the exemption holds (copy-on-write, not absence-of-behaviour) plus a pinning property test. **(c)** flip only `storage.set` (the one with a `MUTABLE_TAGS`-relevant argument in practice) and leave publish/auth. **(d)** flip, but relax `collect_never_owned` to be region-sensitive so the reject only bites *after* the escape — D7's "relaxing to region-sensitivity later is additive".

**Recommendation: (b) — do not flip; deep-copy instead, and rewrite the docstring's justification rather than its conclusion.** Rationale: the flip's *only* purpose is to prevent a tier-1/chain divergence, and deep-copying prevents the same divergence **without narrowing the authoring surface** — strictly better on the axis that matters. It keeps a pinned test green whose docstring says the pattern must compile, and keeps `SPT1034`'s `help` honest (a diagnostic that recommends a rejected rewrite is the specific failure that test exists to prevent). It is *more* faithful to the chain, not less: the host serializes at `put_contract_data`, `contract_event` and `require_auth_for_args`, so copy-on-write **is** the host's semantics. And it is cheaper: (b) is one deep-copy per write plus one property test (§D.5.2), against (a)'s four code sites, one test rewrite, two help-text rewrites and a `subset.md` regeneration. **The decision procedure, to state in the plan:** implement §D.5.2's isolation property first; if it holds, (b) is proven and the flip is closed with a recorded ruling; **if it cannot be made to hold for any `ChainValue` shape, (a) becomes mandatory** and the plan should already carry the four-site edit list. **Against (d):** region-sensitivity is real dataflow work, D7 declined it for good reasons, and it would be a strange thing to build in order to soften a reject that (b) avoids entirely. **One thing to fix regardless of the ruling:** the keyword/positional asymmetry (§B.2's last row) — `set(key=k, value=own)` already escapes while `set(k, own)` does not, pinned in *both* directions by two tests. That is a latent inconsistency in `collect_never_owned`, it is not evidence for the current ruling, and E should close it in whichever direction the controller picks.

---

**E6 — The five examples: exact list, home, and gate scope.**

Inventory, coverage gaps and the graduation verdicts are §B.5. The facts: `examples/` does not exist; spec §3 mandates it as **uv workspace members** at repo top level; `tests/fixtures/` is the de-facto examples directory *and is documented as such* (`sandbox/README.md:52-56`); adding a contract to `FIXTURES` buys five properties free (§D.2); `mypy --strict` covers only `["src", "tests"]` and `ruff format --check` only `src tests`; and **one of these examples is what gets deployed to testnet at M1's end** (R2).

Options for the home: **(a)** `examples/` per spec §3, as real uv workspace members. **(b)** `examples/` as a flat directory of `.py` files (no workspace members). **(c)** stay in `tests/fixtures/`. **(d)** `examples/` for the shipped, documented five **plus** a thin `tests/` module that adds each `examples/*.py` path to `FIXTURES`.

**Recommendation: (d), with (b)'s flat layout — five files in `examples/`, driven from `tests/` by extending `FIXTURES`, and `examples/` added to BOTH `mypy --strict`'s `files` and `ruff format --check`'s scope.** Rationale: spec §3 wants `examples/` and G's docs site needs a stable place to read them from; flat over workspace-members because uv workspace members buy dependency isolation these files do not need (they import only `serpent`) and each member would need its own `pyproject.toml` — pure ceremony, and reversible upward later. Driving them from `tests/` rather than duplicating them is the same anti-drift discipline `sandbox_counter.py:21-27` already established, except better: **one copy, tested in place**, so the byte-identity guard becomes unnecessary for the new files. Adding them to the strict gates because the SDK's central claim is "contracts type-check in any IDE with no plugins … under `mypy --strict`, not just default strictness" (spec §2:72,87-89) — **an example that is not strict-clean falsifies the pitch**, and this is the cheapest possible enforcement.

The list, and what graduates:

| Example | Source | Work |
|---|---|---|
| `examples/counter.py` | **`tests/fixtures/sandbox_counter.py` graduates verbatim** (and `sandbox/counter.py` can then be deleted or left as scratch; the byte-identity test follows the fixture). | Docs prose. |
| `examples/errors.py` | **Fresh.** A multi-code `@contracterror` with the S12 `__constructor`-laundering caveat demonstrated and documented (spec §2:90-94 says the docs "must say so, prominently"). | New contract. |
| `examples/structs.py` | **`spike1_reauthored.py` stays a fixture** (it is Phase-0 chain-anchored evidence, and its docstring frames it that way); write a fresh pedagogical struct example — a struct with a >9-char field (to show the linear-memory consequence, S1/§5) and a struct storage key. | New contract; fixture untouched. |
| `examples/events.py` | **Fresh**, and it is the example §E2's ruling shapes: both `Event.publish(env)` and `env.events().publish(topics, data)`, plus the heterogeneous `(Symbol, Address, Address)` shape. | New contract. |
| `examples/allowance_token.py` | **Fresh, and the largest item.** `token_style.py` is a *mint/transfer* token, not an allowance token: no allowance map, no `(from, spender)` composite key, no `approve`/`transfer_from`, **no `temporary()`, no TTL**. Keep `token_style.py` as a fixture (byte-anchored, and it is the only fixture exercising a struct storage key + auth + events together). | New contract; forces §E4. |

**Explicitly flag:** `sandbox_hello_world.py` is **not** one of the five but carries unique coverage (an internal module-level helper, a `Vec[Symbol]` returned across the ABI, the `Symbol("_")`-vs-`Symbol("A")` differential vector) — **it must stay in `FIXTURES`**, not be replaced by an example.

---

**E7 — Constructor semantics at tier 1 (and the `Address.require_auth()` env problem).**

Under §E1 reading (i)/(iii), a test constructs the contract class as ordinary Python. Three things then need answers:

(i) **What does `__init__` see and write?** `__init__` compiles to `__constructor`, which on chain runs **once, at deploy**, with a live `Env` and full storage access. As plain Python it is just a method taking `(self, env, ...)`. Options: **(a)** nothing special — the test calls `C()` then `c.__init__(env, ...)`-equivalently via a helper that mirrors deploy; **(b)** a `deploy(cls, env, *args)` helper on the model that constructs the instance and runs `__init__` exactly once, recording that it ran; **(c)** model S12's error laundering — an exception out of `__init__` is re-raised as a `Context(InvalidAction)`-equivalent, not the author's code.

(ii) **Does the model enforce "constructor runs before any export"?** On chain that is structural (deploy precedes invoke). In Python nothing stops a test calling `transfer` on a fresh instance with empty storage — which is a state the chain cannot produce, i.e. **a tier-1 accept the chain would never see** (the mirror of D6's "not modelled in tier 1" note).

(iii) **`Address.require_auth()` takes no `Env`** (Q5, verified). Options: **(1)** a module-level/contextvar "current env" the model sets while a frame is active; **(2)** change the signature to `require_auth(self, env: Env)` — **breaking, and it is the surface spec §2:64's own example uses** (`from_.require_auth()`); **(3)** leave both `Address` methods `NotImplementedError` and document that auth is a tier-2 concern only.

**Recommendation: (b) + (c) for (i); enforce (ii) with a loud error; and (1) for (iii) — a contextvar set by the same `deploy`/`invoke` helpers.** Rationale for (b): a `deploy` helper makes the once-only, at-deploy nature of `__constructor` *visible in the test*, which is the whole pedagogical point, and it is where "recording that it ran" lives for (ii). For (c): S12 says the laundering caveat must be documented "prominently, **because Python developers will expect `__init__` exception semantics**" — a tier-1 model that lets the author's error code escape `__init__` teaches exactly the wrong thing, and modelling the laundering is ~3 lines. For (ii): refusing to invoke an export before `deploy` costs one boolean and closes a whole class of tier-1-only state; a silent accept is the "silent false green" S5 warns about, at the cheapest possible place to prevent it. For (iii) option (1): it preserves the shipped signature and spec §2's own example, and a contextvar is the standard Python answer to "ambient per-frame context"; the cost is that it is *ambient state*, so the helpers must set and clear it in a `try/finally` and a stray `require_auth()` outside a frame must be a loud error, not a silent pass. **Against (2):** it breaks the authoring surface for a test-only need, and D4's reversal note ("breaking after docs/examples") applies with full force since E ships the examples. **Against (3):** it leaves `Address`'s two `NotImplementedError`s in place while `env.py`'s 17 go away, which is an incoherent boundary — and it would make the allowance-token example's `require_auth` untestable at tier 1, i.e. the one thing the example is for.

---

**E8 — What stays `NotImplementedError` after E: the honest-boundary list.**

The full census and the stays-unimplemented table are §C.4. What the controller ratifies is the **list**, plus three specific line-drawing calls:

(i) **`env.logs()`.** Spec §3 puts `logging` inside the `env/` bullet (S2) — so §3 arguably promises it in E's own module. But it has **no authored surface at all** (there is nothing to implement, only something to *add*), and the M1-C dossier's §C.4 puts `log_from_linear_memory` under "not lowerable in M1 … all M2". Options: add a minimal `env.logs().log(...)` in E, or keep it M2 and correct the §3 expectation.
(ii) **The abort-model mapping.** Two exist and share no base: `engine.HostError` (a Val word) and `serpent.errors.ContractError` (a `code: ClassVar[int]`). A tier-1 `get` on a missing key must raise *something*, and `docs/subset.md:65-70` already tells users the code is `0xfffffffd`.
(iii) **Frame rollback (S9) and footprint (S4).** Neither is modelled anywhere; footprint is explicitly F's row.

**Recommendation: keep `env.logs()` in M2 and correct the §3 expectation in E's docs; introduce ONE reserved-code exception hierarchy in `serpent.errors` that both tiers raise; and declare rollback and footprint out of scope with named carried obligations.** Rationale for logs: adding a surface is scope creep (S18 names it), the frontend already gives it a good `SPT1033` diagnostic, and spec §3 is a layout sketch that already diverges from the tree in two other places (`env/` as a package, `examples/` missing) — the honest move is a one-line note, not a feature. For (ii): the reserved codes at `src/serpent/errors.py:6-37` are *already* the shared vocabulary (`CODE_MISSING_VALUE`, `CODE_BAD_ARGUMENT`, `CODE_ARITHMETIC_OVERFLOW`, `CODE_ABI_CHECK_FAILED`, …) and `ContractError` already carries `code: ClassVar[int]`; giving `CODE_MISSING_VALUE` a named `ContractError` subclass means the tier-1 model, the emitter's guard and `tests/semantics/test_semantics.py:44-47`'s assertion all name the same thing — **one definition, per S13**. For (iii): S5 lists frame-rollback among the things a hand-written mock gets silently wrong, so E declaring it unmodelled *in code* is strictly better than E approximating it; and R1 gives footprint recording to F by name.

**The full stays-unimplemented list to ratify:** `env.logs()`; `env.call`/`try_call`; `env.crypto`/`prng`/`deployer`/`current_contract_address`; `Ledger.version`/`network_id`/`max_live_until_ledger`; `U256`/`I256` authoring; `Timepoint`/`Duration` arithmetic (per §E3); `Address.to_val`/`from_val`; frame rollback; footprint recording; budget metering; real auth trees; instance-storage flush-at-frame-exit (**unobservable in M1 — re-entrant self-call needs `call`, which is M2**); and TTL clamp/trap/dead-entry (per §E4).

---

**E9 — Where the Env differential lives: extend the frozen table, or start a second one?**

The counts, the shape blocker and the two options are §D.1. The hard fact: **the frozen 59-case table has zero Env cases**, because `SemCase.source` is "a single expression" and storage is stateful.

Options: **(a)** extend `cases.py` with a new optional `setup: tuple[str, ...]` field so a case can write-then-read. **(b)** a second, E-owned table of stateful scenarios in `tests/semantics/` (or `tests/`), run against the tier-1 model and against compiled WASM under `FullHost`. **(c)** no table — per-example assertions only, in the style `test_emitter_end_to_end.py` already uses for `token_style`'s mint/transfer sequences.

**Recommendation: (b).** Rationale: `cases.py` is a **frozen** oracle whose in-scope predicate is restated in three places (`test_emitter_semantics.py:110-127`, `test_harness_hostfns.py:916-921`, and the count constant `IN_SCOPE_COUNT = 35`), and changing its dataclass shape ripples into all of them plus `test_frontend_semantics.py`'s `EXPECTED_CODE`/`EXPECTED_TY` maps — a lot of churn on the artefact whose stability is the point. A second table keeps the frozen one frozen while giving F a **named, importable corpus** to re-run on tier 2b, which is exactly the handoff shape D used (D11: "F re-runs the same table on tier-2b (named carried obligation)"). **Against (c):** per-example assertions are what exist today and they are why the Env surface has no differential at all; a table is what makes the obligation enumerable and the skip list empty. **State the honest limit in the plan:** with `FullHost` on the WASM side, this differential compares **two models E and D wrote** — it proves self-consistency, not chain fidelity (D11's exact caveat), and F's tier 2b is where it becomes evidence.

---

**E10 — Does `src/serpent/env.py` become `src/serpent/env/`?**

Spec §3 (S1) names a package: `env/  # Env: storage (3 tiers + TTL), events, auth, ledger, logging`. Today it is one 217-line module that would grow by roughly the size of §C.1's model. Options: **(a)** keep the module. **(b)** promote to a package now, with `__init__.py` re-exporting the frozen `__all__` unchanged. **(c)** keep the module and record the promotion as an M2 cleanup.

**Recommendation: (a), recorded as (c).** Rationale: the promotion is a pure refactor with no behavioural content, and E already carries a five-layer cross-cutting item (§E2) plus a possible new host model (§E1) plus five examples — spending review budget on a file split is poor allocation. The `__all__` is frozen and import-compatible either way, so this is reversible at zero cost at any time. **Two conditions on choosing (a):** the module must stay **zero-dep** (`tests/unit/test_core_zero_dep.py` enforces it, and dicts-and-lists trivially comply), and if the model plus the surface pushes `env.py` past roughly 600 lines the split should happen inside E after all — a 900-line module with the type surface and the model interleaved is harder to review than two files, and E's own reviewers pay that cost.

## F. RISKS

### F.1 Where E can silently diverge — from the chain, or between its own tiers, with no error anywhere

Ordered by likelihood × silence. The failure mode of every entry is "tests pass, docs read well, and the contract behaves differently on chain".

| # | Divergence | Why it is silent | Mitigation |
|---|---|---|---|
| **1** | **A tier-1 Env model that stores REFERENCES.** `serpent.types.Vec`/`Map` mutate in place while the host's ops are functional (`recognize.py:2099-2105`'s `_FUNCTIONAL_OP_NOTE`, verbatim: "after `a = b`, `a.push_back(x)` also changes `b` at tier 1 and cannot on chain"). Store a reference at `set()`, mutate the local, and tier 1 reports the mutation from storage while the chain reports the snapshot. | Both tiers return *a* plausible value. Scalar-only tests — which is most tests — never see it, because scalars are immutable. | Deep-copy on `set()` **and** on `get()` (§C.1 points 2/3); §D.5.2's isolation property as the **decision procedure** for §E5; and if the property cannot hold, the escape flip becomes mandatory. |
| **2** | **The tier-1 model becomes a second oracle and drifts from the emitter.** S13 names this as "the single highest-risk internal drift", and E adds a third implementation of host semantics. The specific silent cases: a defaultless `get` that returns `None` instead of raising `CODE_MISSING_VALUE`; a `get` that ignores its `ty` argument where the emitter emits a narrow check; `has()` returning a Python `bool` instead of a chain `Bool` (Q12). | The tier-1 test passes because the model *is* the assertion. Only a differential catches it, and there is **no Env differential today** (§D.1). | §E9's table, run against both sides; §C.1 points 3/4 as explicit model requirements; §E8's single reserved-code exception hierarchy so both tiers name the same error. |
| **3** | **A guessed `SCSpecEventV0`.** Q6's own words: "**Guessing would ship a spec that is valid XDR and a lie.**" Every field of the entry decodes fine whatever serpent writes; the consumer that discovers a wrong `data_format` or a mislocated param is an *indexer*, months later. | Nothing in serpent, and nothing in `stellar-contract-bindings`, validates the spec against the contract's actual event bytes. The spec and the emission are two independent code paths. | §E2's convention makes every field author-sourced; §D.3's RUST-SDK-BYTE-COMPAT golden against an equivalent Rust `#[contractevent]` is the only independent check available — **take it**; and a test that the emitted `contract_event` topics/data **match the entry's** locations and format. |
| **4** | **`del_contract_data` on an absent key.** The mini-host makes it a silent no-op while `map_del` **traps**, and flags the asymmetry as deliberate: "two different host behaviours the rig must not unify" (`hostfns.py:365-370`). **Neither behaviour is verified against the real host in this repo.** A tier-1 model must pick one. | Whichever E picks, both tiers agree with each other and possibly neither agrees with the chain. | Pick the mini-host's (no-op) for consistency, **document it as an unverified assumption in the same voice `i256.py:28-33` uses for `i256_div`'s rounding**, and add it to F's tier-2b list. Do not silently pick. |
| **5** | **TTL modelled with a guessed maximum.** S8's clamp and trap rules both need the host's max live-until, whose only source is `get_max_live_until_ledger` — **on the M2 `SPT1033` list, unreachable in M1**. A serpent-chosen constant is a guess of exactly the kind Q6 forbids. | An example that extends a TTL "successfully" under a made-up ceiling would trap on chain for a temporary entry (S8: temporary **traps**). That is a *loud* on-chain failure produced by a *green* test. | §E4's recommendation refuses to model clamp/trap at all and names the gap; a named carried obligation to F; **never** hard-code a max without a drift test against a protocol constant. |
| **6** | **The escape flip's `SPT1034` `help` text.** If §E5 chooses (a), the `help` at `recognize.py:2424-2440` and the registry intent at `codes.py:318-326` **recommend a rewrite the compiler now refuses** — which is the exact failure `tests/unit/test_frontend.py:700-726` was written to prevent, and the test would be *deleted* as part of the flip. | The diagnostic reads plausibly; the user follows it and gets a second `SPT1034`. Nothing tests that a `help` string compiles. | Whichever way §E5 goes, **add a test that every `help` string's suggested rewrite actually compiles** — the discipline that test already embodies for one case, generalized. Regenerate `docs/subset.md`. |
| **7** | **`Address.require_auth()` via ambient state.** §E7 option (1) sets a contextvar per frame. A stray `require_auth()` outside a frame, or a frame that raises before its `finally`, leaves the ambient env stale — and the next call authorizes against the wrong env. | The auth check *succeeds* (mock-all-auths means recording is the whole model), so a stale env produces a green test with the auth recorded on the wrong contract. | `try/finally` in the helpers; a **loud error** on `require_auth()` with no active frame; a test that a raising frame clears the ambient env. |
| **8** | **Frame rollback assumed but not modelled.** S9: "Events roll back with failed frames." Neither the mini-host nor (per §E8) E's model does this. A contract that publishes and *then* raises leaves an event recorded in both tiers and **no event on chain**. | `test_emitter_end_to_end.py:313-329`'s `assert host.events == []` looks like rollback evidence and is not — it holds because the fixture checks the balance *before* both writes. A fixture ordered the other way would pass with a recorded event. | State the non-model in the model's docstring; **write an example or test that publishes-then-raises and assert the tier-1 answer is the UNMODELLED one, with a comment saying the chain answer differs** — an honest pin beats a silent gap. Carried obligation to F. |
| **9** | **`examples/` outside the strict gates.** If `examples/` is not in `mypy --strict`'s `files` and `ruff format --check`'s scope, an example that does not type-check ships as the SDK's advertisement for "strict-clean with no plugins" (spec §2:87-89). | `ruff check .` catches lint but not types; nothing else looks at the directory. The examples are also the docs-site source (G), so the error propagates into published docs. | §E6's recommendation adds both scopes. Also add each example to `FIXTURES` so it is *built and run*, not merely checked. |
| **10** | **`sandbox_*` byte-identity guards left behind.** `test_emitter_end_to_end.py:337-388` asserts the fixture and the `sandbox/` original build byte-identically. If §E6 graduates `sandbox_counter.py` to `examples/counter.py` and the guard is not moved, it either breaks loudly (fine) or is deleted quietly and the two copies drift (not fine). | A deleted test is invisible in a green run. | Move the guard with the file, or delete `sandbox/counter.py` in the same commit and record why. |
| **11** | **The `types=` contract in `build_spec_entries`.** Its docstring warns: "**a caller that omits `types` silently emits a spec whose UDT references have no matching entries**, which decodes fine and renders as an unknown type" (`sections.py:143-146`). E adds a *fourth* declared-kind (events) to that same argument. | The same silence, now for events: an event class not passed in `types=` produces a contract with no event entry, which decodes fine. | E must verify that `module.assemble` passes `spec_inputs.events` through (§C.2) **and** add a test that a contract publishing an event has a matching `SC_SPEC_ENTRY_EVENT_V0`. |
| **12** | **The `Symbol("_")`-vs-`Symbol("A")` order, inherited.** Tier 1 pins ASCII; the host packs 6-bit codes where `_`=1 and `A`=12 (`hostfns.py:53-61`: "It **may be the WRONG answer about the real host**"). E's event-name Symbols and struct-key Symbols both ride on it. | Every same-case Symbol pair agrees; only `_`-vs-letter pairs disagree. | Not E's to settle — `hostfns.py:53-61`: "if the host disagrees, it is a **controller decision on the frozen table**, not a change here." E must not reflag it, and must not pick example/field names that make it load-bearing. |
| **13** | **A tier-1-only state the chain cannot produce.** Calling an export before `__init__` ran (§E7(ii)), or reading storage a deploy would have written. | The test constructs a state, asserts on it, and passes. The chain never reaches that state, so the assertion means nothing. | §E7's loud pre-deploy refusal. This is the cheapest structural fix in E. |
| **14** | **`env.py`'s `__slots__ = ()`.** Every class is slotted. Adding state without adding slot entries would either fail loudly (good) or, if a class gains `__dict__` via a base change, succeed silently and make the deliberately-slotted surface accept arbitrary attributes. | Attribute typos on an `Env` would then be silent. | Add explicit `__slots__` entries; a test that `Env()` refuses an unknown attribute. |

### F.2 Differential and acceptance checks that belong in E's own test plan

1. **The five examples, each: compiles → builds → runs at tier 1 → runs as WASM, with the SAME answers** (§D.2's three obligations). This is R1's "compiled and passing", made checkable.
2. **§E9's Env differential table** over storage (three durabilities), events, auth-recording, and the ledger.
3. **§D.5.2's deep-copy isolation property** — the decision procedure for §E5.
4. **§D.5.1's `set`/`get` round-trip over struct and container keys** — D6's declared tier-1 hole, and the failure `objects.py:36-49` was written to prevent.
5. **The `SCSpecEventV0` round-trip** plus the XDR cap negative controls (`prefix_topics` of 3 → a source-located serpent error, never a bare `stellar_sdk` `ValueError`).
6. **A RUST-SDK-BYTE-COMPAT golden** for the event entry, if an equivalent Rust `#[contractevent]` artifact can be built (§D.3).
7. **`token_style.py` both spellings** — `Transfer(...).publish(env)` and `env.events().publish(...)` — emitting the same `contract_event` topics and data.
8. **The publish-then-raise honest pin** (F.1.8).
9. **TTL arithmetic tests** for the two rules §E4 models (never-reduce, threshold), and an **explicit non-test with a comment** for the three it does not.
10. **The 8 `NotImplementedError`-asserting tests rewritten**, not deleted (§D.4) — each becomes a positive assertion about the new behaviour, so the surface stays pinned.
11. **`docs/subset.md` regenerated** and its byte-drift test green.
12. **`help`-string-compiles property** (F.1.6).
13. **`serpent.__all__` unchanged** (`tests/unit/test_public_api.py`) — the inspection surfaces must not leak into the authoring namespace.
14. **`tests/unit/test_recognize_env.py`'s `_DOSSIER_C4_INVENTORY`** updated in both directions for any new env host fn.

### F.3 Process risks

- **E is the first sub-plan with a CONCURRENT successor (R4: "A→D are sequential, E onward partially parallelizable").** F builds tier-2a fixtures, tier-2b, and the differential runner against E's surface. **Anything E leaves in flux is a moving target for work happening at the same time**, not a problem for later. Mitigation: E should freeze the *observable* surface of whatever it ships (§E1's model API, §E2's convention, the five examples' names and exports) in its first task, and let internals follow.
- **E is where three sub-plans' deferrals converge.** `SC_SPEC_ENTRY_EVENT_V0` was deferred by B (B10), the publish form by C (E12), and the lowering by D — all three pointing at E by name in shipped code and docs. **There is no fourth sub-plan to defer to**: after E, the next stop is M2, and D4's reversal note says the events authoring surface is "cheap now, **breaking after docs/examples**" — which E ships. §E2 is therefore a genuine now-or-M2 decision, not a scheduling one.
- **Every frontend edit E makes forces a `docs/subset.md` regeneration** whose drift test fails until it happens (§C.3). Three of the ten questions (§E2, §E3, §E5) touch `recognize.py` or `codes.py`. Schedule the regeneration as an explicit step, not a cleanup.
- **The registry is append-only public API.** `SPT1032` cannot be deleted (D9). Whatever §E2 rules, the row survives; the plan must say what it *means* afterwards.
- **`spikes/` disposition is NOT E's.** R6: `spikes/` stays until "D supersedes the emitter **and F supersedes the harnesses**", then a cleanup task **in G** decides with the user. **D has now superseded the emitter; the harnesses are F's.** E should not touch `spikes/`.
- **The mini-host is F's to change, not E's.** Its TTL/auth/event non-models are documented and pinned (§B.6). If E "improves" them, there are two models of the same thing in the tree and F inherits both. §E4 recommends explicitly against it.
- **The M1 gate is at G, and it deploys one of E's examples** (R2/D1). Whatever E leaves unproven in that example is deployed unproven — and the deploy is the one hard stop in the whole autonomy grant (D1).
- **`decisions.md` is controller-owned.** E records rulings in its own SDD ledger; the controller promotes the lasting ones — the pattern every prior sub-plan followed.
- **Stale-promise hygiene.** `sandbox/README.md:17-20` is already wrong about M1-D, and `expr.py:234` / `numeric.py:53-61` point at "sub-plan E" for a decision §E3 recommends deferring to M2. **A closing sub-plan should sweep the promises that named it** — there are 19 `NotImplementedError("sub-plan E")` strings, 2 more with a different message, 2 `docs/subset.md` lines, 1 `SpecTypeError` message, 4 `recognize.py` sites, and 1 `expr.py` note. §E8's ratified list is what makes that sweep checkable.
