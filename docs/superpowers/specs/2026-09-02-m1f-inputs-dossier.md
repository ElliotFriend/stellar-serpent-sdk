# M1-F DESIGN-INPUTS DOSSIER — Testing tiers (tier 2a productized, tier 2b real host, the differential runner, the tier-3 fixture runner)

Compiled 2026-09-02 for the sub-plan F plan author and its adversarial
reviewer. Every claim carries a citation ID; the plan cites IDs, not
prose. Facts marked **verified 2026-09-02** were re-checked live this
session (RPC, crates.io, GitHub at tag, local builds); everything else is
quoted from the frozen inputs it cites.

Citation-ID families:

| Prefix | Source |
|---|---|
| S# | design spec `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md` |
| R# | roadmap `docs/superpowers/plans/2026-08-26-m1-roadmap.md` |
| D# | rulings in `docs/superpowers/decisions.md` (dated title) |
| P# | Phase 0 findings `docs/superpowers/specs/2026-08-26-phase0-findings.md` + `spikes/spike2/FINDINGS.md` |
| U# | F inputs decided WITH Elliot 2026-09-02 (`.superpowers/sdd/2026-08-31-m1e2-unions/final-review-attention.md` §6) |
| O# | obligations carried INTO F by the C/D/E/E2 attention files and ledgers |
| K# | chain/toolchain facts verified 2026-09-02 (this dossier, §B) |
| C# | what the repo contains today (this dossier, §C) |

How F differs from A–E2: F ships almost no new *language* surface. Its
product is EVIDENCE — the same bytes, the same tables, run on the real
host and (as fixtures) against testnet — plus the fixtures downstream
users will write contract tests with. The controlling risk is therefore
not "the compiler is wrong" but "the differential is hollow": a runner
that compares two models E/D wrote (D8's own honest limit) is not
evidence until the real host is one of its legs.

## A. FROZEN INPUTS

### A.1 Spec obligations (`2026-08-26-serpent-python-soroban-sdk-design.md`)

- **S1** §8 headline: four tiers; **the real host is the release gate**
  (adversarial review M7: a hand-written mock of ~9k LOC of host semantics
  has *silent false green* as its failure mode — auth trees consuming
  storage-written nonces, non-recoverable footprint errors, frame rollback
  of events, TTL asymmetries, instance-storage flush rules).
- **S2** §8 tier 1: pure unit tests (Val codec round-trips, symbol
  packing, XDR goldens, operand-stack validation, `must_reject/`). Already
  shipped by A–E2; F does not re-own it.
- **S3** §8 tier 2a: wasmtime-py + Python mini-host; compile-in-test; run
  the bytes that would deploy. Explicitly LOWER fidelity: no budget
  metering, simplified auth (`mock_all_auths` semantics only), and
  **mandatory footprint recording** ("tests declare expected footprints;
  silent passes are not allowed"). wasmtime `Config` pinned to mirror the
  chain feature set exactly, **with a test that fails if a wasmtime
  upgrade flips a default**. Every boundary crossing masks to u64.
  Throughput target: engine/module cached across tests; hundreds to low
  thousands of contract tests/sec.
- **S4** §8 tier 2b: embed `soroban-env-host` via PyO3 as the
  `serpent[testing]` extra "with prebuilt wheels"; "deletes the entire
  mock-fidelity problem — auth, footprint, comparison ordering, TTL,
  budget, engine parity — and is how the Rust SDK itself tests." Fallback
  named: Docker quickstart over RPC. (Phase 0 ADOPTED the PyO3 route, P1;
  the fallback is dead unless F's build fails.)
- **S5** §8 tier 3: on-chain integration, opt-in, testnet via
  `stellar_sdk` RPC: differential runs — same bytes, tier 2 vs chain,
  divergence is a release blocker.
- **S6** §10: one Val codec; semantics tests run **the same table of
  cases** against (a) the Python classes and (b) compiled WASM in tier 2,
  asserting identical results including overflow, bounds, and error codes.
- **S7** §11 M1 scope line: "testing tiers 1–2" are M1; §11 M3 names
  "tier-3 suite" and "differential CI against Rust SDK contracts" as M3.
  Read with U3: F BUILDS the tier-3 runner and proves it against recorded
  fixtures; the live suite matures in M3.
- **S8** §12 risks F owns: tier-2 fidelity drift (real host as the gate +
  differential tests; divergence is a release blocker); wasmtime-py
  monthly majors (exact pin + feature-set assertion test); env.json /
  protocol churn (pinned by SHA; recurring per-protocol work budgeted).
- **S9** §13 facts F re-proves on the real host (currently pinned only at
  tier 1 / mini-host): TTL — persistent extension past max **clamps**,
  temporary **traps**, extending a dead entry errors, extensions never
  reduce; events roll back with failed frames; footprint violations are
  `Storage(ExceededLimit)` and non-recoverable; `fail_with_error` accepts
  only `ScErrorType::Contract`; the wasmtime `Config` ordering hazard
  (`wasm_relaxed_simd = False` BEFORE `wasm_simd = False`).
- **S10** §13 network note is STALE: "mainnet + testnet on protocol 27;
  protocol 28 vote scheduled 2026-09-16." Testnet is on 28 today (K1).
  The spec line needs the correction note when F lands (docs task).

### A.2 Roadmap standing constraints (`2026-08-26-m1-roadmap.md`)

- **R1** F row, verbatim: "tier-2a productized (pytest fixtures,
  feature-set assertion test, footprint recording); tier-2b `serpent-host`
  PyO3 package (error discrimination, panic containment incl.
  Address/register, wheel/protocol strategy per findings §2);
  differential runner (2a vs 2b vs testnet = release gate)". Consumer: G.
- **R2** Standing constraints: single Val codec; pre-validate at every
  nominally-fallible soroban-sdk boundary; `relaxed_simd`-before-`simd`;
  pinned toolchain versions with drift-detection tests; adversarial plan
  review before execution; SDD with task-scoped reviews.
- **R3** Spike disposition: `spikes/` stays read-only "until sub-plan D
  supersedes the emitter and F supersedes the harnesses, then a cleanup
  task in G decides retain-vs-remove with the user." D has superseded the
  emitter; **F supersedes the harnesses**. F does not delete `spikes/`.
- **R4** Order: "A→E2 sequential, F onward partially parallelizable" —
  G's CI work can start on F's branch state; F's own CI Rust job is G's
  (U2).

### A.3 Decision-log rulings that bind F (`decisions.md`)

- **D1** 2026-08-26 standing autonomy: decide + record; hard stops are
  pushes/publishes/deploys and the M1-end testnet deploy (explicit
  in-session approval). Tier 3 runs that SUBMIT transactions are deploys
  or invocations on a public network = outward-facing = hard stop.
  Read-only `simulateTransaction`/`getLedgerEntries` calls are not (U3).
- **D2** 2026-08-27 M1-D rulings E1: the mini-host "is NOT an oracle; F
  re-runs the same table on tier-2b (named carried obligation to F)".
- **D3** 2026-08-27 M1-D controller addition: `i256_div`'s rounding
  direction is undocumented in the pin; D pinned trunc-toward-zero by
  differential against Python; "F re-proves on the real host."
- **D4** 2026-08-27 M1-D E5: internal validator unconditional; wasm-tools
  runs when on PATH, "skipif-never-silently-passed in tests, installed +
  pinned in one CI job" — the skip-loudly convention F's tier-2b marker
  must match (U2).
- **D5** 2026-08-28 M1-E E1: the tier-1 Env is a deliberately minimal
  model; "env.py's 'real host at test time'/'host bridge' wording is
  really F's tier-2b"; those docstrings were rewritten to point at F. F
  discharges them (O-H).
- **D6** 2026-08-28 M1-E E4: TTL clamp/trap NOT modelled ("the max is
  `get_max_live_until_ledger`, an M2 host fact — a chosen constant would
  be a guess"); "the mini-host's TTL no-ops are NOT touched (F's).
  Carried to F: clamp/trap/dead-entry are unproven at every tier."
- **D7** 2026-08-28 M1-E E8: footprint "is F's row by name"; frame
  rollback and footprint declared out of scope with named carried
  obligations; instance-storage flush-at-frame-exit unobservable in M1.
- **D8** 2026-08-28 M1-E E9: `ENV_SCENARIOS` is "importable so F re-runs
  it on tier-2b — with the honest limit stated (it compares two models
  E/D wrote; F's tier-2b is where it becomes evidence)."
- **D9** 2026-08-28 M1-E plan-review: tier-1 ledger defaults pinned to
  the harness constants via ONE shared home (F's real-host `LedgerInfo`
  must be fed from the same home, not a third copy).
- **D10** 2026-08-31 M1-E final review: tier-1 `get` adopts raw literal
  defaults through `ty` — a tier-1 behavior the real-host leg will
  observe as `U32(0)`; no divergence expected, but the runner compares it.
- **D11** 2026-09-01 M1-E2 final review: tier-1 `get`/`payload` RE-TYPE
  stored unions/enums to the requested `ty` "exactly as the host hands
  back a bare word" — a claim about the host that F's real-host leg is
  the first to test.
- **D12** Registry discipline (2026-08-27 x2): subagents never touch
  `codes.py`, `decisions.md`, `spikes/`. F is expected to add NO SPT
  codes (it compiles nothing new); if one seems needed the implementer
  returns BLOCKED.

### A.4 Phase 0 tier-2b feeds (`phase0-findings.md` §2/§3/§6, `spikes/spike2/FINDINGS.md`)

- **P1** §2: PyO3-embedded `soroban-env-host` ADOPTED as tier 2b. The
  877-byte Phase 0 artifact reproduced testnet behavior exactly
  (1, 2, 3, then contract error 7 discriminated from host errors).
  20.4 µs/invocation release (~49k/s); fresh env + register 0.133 ms;
  debug ≈ 11x slower and `maturin develop` DEFAULTS TO DEBUG. 140 lines
  of Rust, one `cdylib`.
- **P2** §2 open item: the host's protocol ceiling is compiled in → the
  wheel matrix is platform × Python × protocol. Candidates named: build
  from source via maturin as default / prebuilt wheels per protocol tag /
  pin testing-extra releases to protocol releases. RESOLVED by U1.
- **P3** §3.2: no panic-free `&str → Symbol` in soroban-sdk;
  "treat 'returns `Result`' as an unverified claim in this SDK";
  pre-validate at the boundary (`SCSYMBOL_LIMIT` + `[a-zA-Z0-9_]`).
  Residual panic sources in the spike wrapper: `Address::from_string`,
  `Env::register`. A Rust panic surfaces as `pyo3_runtime.PanicException`,
  a `BaseException` subclass — it ESCAPES `except Exception` and
  `pytest.raises(Exception)`.
- **P4** §3.3: with `E = soroban_sdk::Error` the `Err(Ok(e))` arm
  catches ALL host errors and `ScErrorCode::InternalError = 7` collides
  with contract code 7 → the headline assertion was spoofable. The fix
  is `e.is_type(ScErrorType::Contract)` (`get_type` deliberately does not
  exist). "This exact discrimination belongs in M1's tier-2b wrapper and
  its tests."
- **P5** §3.4: wasmtime `Config` ordering hazard (relaxed_simd before
  simd; else uncatchable Engine abort) — the feature-set assertion test
  must preserve the ordering.
- **P6** §3.7: maturin venv trap — invoked from a subdirectory with its
  own `pyproject.toml`, maturin built against a self-created Python 3.12
  venv and "succeeded while accomplishing nothing". Correct form:
  `VIRTUAL_ENV=<repo-root>/.venv uvx maturin develop --release`. **Any
  `uv sync` prunes the maturin-installed module.**
- **P7** §3.8: the masking assertion `err & 0xFF == 3` cannot detect a
  missing u64 mask; the mini-host pins the full 64-bit word and enforces
  masking structurally via one trampoline.
- **P8** spike2 FINDINGS "wheel-build friction" #2: plain `cargo build`
  cannot link a `pyo3/extension-module` crate on macOS (undefined
  `_PyBaseObject_Type`); maturin injects
  `-C link-arg=-undefined -C link-arg=dynamic_lookup`. Recommendation:
  commit a `.cargo/config.toml` with those flags OR document maturin as
  the only build entry point. Build cost: clean release 43 s wall /
  227 s CPU, `target/` 717 MB, `.so` 6.1 MB, incremental ~5 s.
- **P9** spike2 FINDINGS "API pain points" #2: `#[pyclass(unsendable)]`
  is MANDATORY (`Env` is `Rc`-backed, neither `Send` nor `Sync`); an env
  is pinned to its creating thread; pytest-xdist (processes) is fine,
  threads are not; free-threaded CPython needs separate thought.
- **P10** spike2 FINDINGS #4: `set_ledger(protocol=…)` is enforced only
  DOWNWARD within the linked host's range (22 → `WasmVm(InvalidInput)`
  for a p27 module; 99 → `Context(InternalError)`); the ceiling is a
  property of the compiled-in host, not a runtime knob.
- **P11** spike2 FINDINGS #5: `EnvTestConfig { capture_snapshot_at_drop:
  false }` is REQUIRED or the sdk's `Drop` writes `test_snapshots/*.json`
  into the CWD under pytest.
- **P12** spike2's boundary protocol was ScVal XDR bytes both directions
  (`ScVal::from_xdr` / `to_xdr` with `Limits::none()`), with all
  marshalling done in the Python test via `stellar_sdk.scval`; only a
  `u32` argument and `u32`/error returns were ever exercised.
- **P13** spike2 exposed NOTHING for storage reads, events, auth
  inspection or selective auth (`mock_all_auths` unconditional at
  construction), budget readouts, TTL/ledger advance beyond three raw
  fields (`timestamp`, `sequence_number`, `protocol_version`), or a
  Python exception hierarchy (everything is `RuntimeError` distinguished
  by string prefix). Every one of these is greenfield for F (§D).

### A.5 F inputs decided WITH Elliot (2026-09-02) — binding, not decisions.md material

- **U1** Tier-2b distribution: in M1 (unpublished) the PyO3 host is
  BUILT FROM SOURCE via maturin (P6's exact form). The published shape
  follows the wasmtime-py precedent: prebuilt wheels built in CI, ONE
  env-host version per `serpent[testing]` release, pinned to the protocol
  testnet runs (P2's third candidate). **Wheel production is M3's
  release-readiness item, not F's.**
- **U2** Tier 2b is an OPT-IN pytest marker that SKIPS LOUDLY (counted in
  the summary) when the extension is not built, so the four gates stay
  runnable on a Rust-less checkout; CI (G) adds a Rust job.
- **U3** Tier 3: F BUILDS the testnet differential runner and proves it
  against RECORDED fixtures; no live testnet run without Elliot's
  explicit in-session approval (deploys remain the hard stop).
- **U4** Embedded host version: track what TESTNET runs — the
  soroban-env-host release for testnet's protocol (verified in K1–K3
  below); no other network is a deployment target.
- **U5** Elliot deployed `examples/shapes.py` to testnet:
  `CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW`. A
  ready-made tier-3 fixture source: read-only simulation against it needs
  no signing and no deploy. Build commit RECOVERED in K5.

### A.6 Obligations carried INTO F (C/D/E/E2 attention files + ledgers), deduplicated

Grouped by theme. "Attn" = `final-review-attention.md` in the named
sub-plan's `.superpowers/sdd/` directory.

**Tier-2a productization**

- **O1** Caching engine/instance across tests "is sub-plan F's
  productization problem, not this rig's" (`tests/harness/engine.py:135`).
- **O2** The feature-set assertion test (S3, S8) — the mini-host sets the
  flags it knows about; it "does not prove the set is exhaustive or that
  wasmtime's defaults match the Soroban host's for every future proposal"
  (`spikes/spike1/harness.py:36-39`, carried by copy).
- **O3** The mini-host CANNOT pin floats, extended-const, memory64, or
  wasm_exceptions through wasmtime-py 48's `Config` (no Python toggle);
  tier 2b is the real gate for those (D attn item 2; D ledger 78-83,
  107-108).
- **O4** `FullHost` needs a PUBLIC container decoder mirroring the
  scalar-only `chain_value`; `test_examples.py:398-430` reaches into the
  private `host._vec` today (E attn "carried OUT"; E ledger 210/272/283).
- **O5** The mini-host's `chain_value` cannot cross-check a union/enum
  RETURN (bare `U32` / opaque Vec rank) and cannot encode a `Vec`
  ARGUMENT; three WASM-leg pins are blocked on those; every shapes entry
  point therefore returns `Symbol`/`U32`/`Bool` (E2 attn §3, §5;
  `examples/shapes.py:95-101`).
- **O6** The mini-host cannot run `union == union` (`FullHost.obj_cmp` →
  the deferred container `val_cmp`) (E2 attn §5).
- **O7** The mini-host has no TTL model: the allowance-token expiry
  scenario is "not runnable on the WASM leg at all"
  (`tests/unit/test_examples.py:480-500,700-715`).
- **O8** The mini-host's TTL/auth/event NON-models are "F's to change,
  not E's" — and if F "improves" the mini-host it creates two models of
  the same non-modelled facts; the M1-E dossier warns against exactly that
  (E dossier line 899). Read with S4: the real host, not a better mock,
  is the answer.

**Tier-2b semantics to RE-PROVE (facts pinned today only against models)**

- **O9** Re-run the whole in-scope semantics table (`tests/semantics/
  cases.py`, 35 in-scope cases) on the real host (D2; D attn item 2).
- **O10** Prove `i256_div`'s rounding on the real host (D3): the mini-host
  implements trunc-toward-zero per A4; a flooring host makes every
  negative 128-bit `//` and `%` silently wrong (`tests/harness/i256.py:
  28-33,215-225`). Also the `%` sign-correction choice (D computes
  `lhs - (lhs // rhs) * rhs` in guest code because `i256_rem_euclid`'s
  host docs claim Euclidean semantics; `i256.py:33-46`).
- **O11** `DIV_ERROR_VAL` in the harness is a CONVENTION standing in for
  an unpinned XDR code; tests must never assert a literal word
  (`i256.py:56-62`). F pins what the host actually returns for 128-bit
  `//0` / `%0` (D ledger 112-117; `src/serpent/errors.py:30-44` names
  "Tier 2b (sub-plan F)" for this re-confirmation).
- **O12** `val_cmp`/`obj_cmp` is an explicitly PARTIAL model
  "differential-validated in sub-plans D/F" (`src/serpent/types/
  _ordering.py:11-12`). THE top differential vector: `Symbol("_")` vs
  `Symbol("A")` — tier 1 pins ASCII byte order; a host comparing packed
  6-bit codes (`_`=1, `A`=12) answers the opposite. **If the host
  disagrees, that is a controller decision on the frozen table, not a
  change F makes unilaterally** (`tests/harness/hostfns.py:53-61`;
  `test_harness_hostfns.py:225-240`; C dossier T5; D dossier line 479).
- **O13** `del_` on an absent key is a no-op at both tiers "by
  assumption"; neither is evidence (`env.py` `del_` docstring;
  `tests/unit/test_env_model.py:294-300`; `tests/fixtures/env_surface.py:
  223-229`).
- **O14** TTL clamp / trap / dead-entry unproven at every tier (D6; two
  `pytest.skip`s naming F at `tests/unit/test_env_ttl.py:339-357`); the
  tier-1 "revive on re-set" convenience is tier-1-only — on chain a
  lapsed persistent entry is ARCHIVED and refuses a write until restored;
  a lapsed temporary entry is gone (`test_env_ttl.py:155-165`).
- **O15** Publish-then-raise: BOTH models keep an event published before
  a raise; the chain rolls it back with the frame. Pinned as
  `an_event_published_before_a_raise_survives_at_both_tiers`
  (`tests/semantics/env_scenarios.py:437-444`; `env.py:1519`;
  `test_env_deploy.py:546-552`; `test_env_differential.py:535-548`).
  On the real host this row is EXPECTED TO FLIP; the runner must express
  "expected divergence between model and host" without weakening the
  two-model pin.
- **O16** Re-run `ENV_SCENARIOS` (62 rows; the frozen 59-row `cases.py`
  companion) on the real host (D8). `TTL_METHODS`/`AUTH_ARGS_METHODS` are
  exported for recomputation; `EnvScenario` is shaped so a re-runner
  "deploys `contract`'s class with `constructor`, replays `setup`,
  invokes `invoke` and compares the outcome" without reading the test
  module (`env_scenarios.py:29-36,155-205,162-172`).
- **O17** `tier1_only_reason` CONFLATES "no mini-host leg" with "no host
  anywhere models this": the real host CAN replay the allow-set and
  auth-args rows the mini-host cannot (E ledger Task 9 minor 3;
  `env_scenarios.py:196-204`; `tests/harness/hostfns.py:59-76`). Rename
  or per-row sub-reasons are F's call (E-question below).
- **O18** `TTL_REASON`'s "no ledger sequence" clause is imprecise (E
  ledger Task 9 minor 4); `TTL_METHODS`/`AUTH_ARGS_METHODS` live half in
  the table module, half as a function-body import (minor 5).
- **O19** `AuthorizationFailed` re-raises UNLAUNDERED (a host trap, not a
  recoverable `ContractError`); a refused auth is never recorded (E ledger
  Task 4 ruling; `env.py` `recorded_auths` docstring) — shapes the
  real-host expectation.
- **O20** Struct↔`Map` is the last standing member of the "tag-level
  acceptance hands back the wrong Python type" class (pinned
  `test_env_model.py:368-379`); the pre-existing `Vec.get(U32)`
  compiler-accepts / tier-1-refuses mismatch tripped three E2 probes
  (E2 attn §5).
- **O21** A union's container ordering (`Vec._cmp_payload`
  NotImplementedError) is unmodelled at tier 1; `Map[union, V]` `get`/
  `has` raise at any entry count (D11 file; E2 attn §5).
- **O22** Runtime parts are meant to be "differential-tested against the
  real host" (spec §6 / S15 in the D dossier); golden vectors with Python
  as oracle are D-local (D dossier line 460).
- **O23** The container-comparison / Map payload-order deferral ("no
  inventing an order the host has not been differentially checked
  against", A15) is F's differential, not E's (E dossier line 612).

**Footprint / budget / auth**

- **O24** Footprint recording: never modelled at tier 1 or 2a; "a tier-1
  run cannot tell you a contract fits" (`env.py:18-19`); "F's row by
  name" four times over in the E dossier (402, 628, 830, 852). Read with
  S3's "mandatory footprint recording" at tier 2a — E-question below.
- **O25** Budget metering is tier-2b-only (S3; E dossier 629).
- **O26** Real auth trees are never modelled anywhere; `auths=` is a
  mock-all-auths allow-set (`env.py:1590-1600`; S1).
- **O27** Instance-storage flush-at-frame-exit is unobservable in M1
  (needs cross-contract `call`, M2) — F must NOT claim to have proven it
  (D7; `env.py:1148`).

**Differential runner / process**

- **O28** Comparison order: the two legs are compared to EACH OTHER
  first, then to the table's pinned expectation (`test_examples.py:
  14-24`; `test_env_differential.py:1-14`).
- **O29** Row-coherence meta-test exists "as sub-plan F grows this corpus"
  (`test_env_differential.py:335-350`).
- **O30** The wasm-tools CI pin drift watch is carried to F alongside O9
  and O10 (D attn item 2).
- **O31** `spikes/` supersession (R3): the harnesses are F's to
  supersede; deletion is G's decision with the user.
- **O32** Generalising the bridge-needle gate (one-directional blind spot)
  beyond the scanned tuple; Task 4 m2 (parameter shadowing of declared
  type names) — E2 attn §5 "To F". Both are frontend hygiene, not
  testing-tier work: **recommend re-routing to G's wording/hygiene pass**
  (E-question below).
- **O33** The promise-sweep pattern (`tests/unit/test_no_stale_promises.py`,
  three outcomes IMPLEMENTED / REPOINTED / REMOVED; `_REPOINTED` already
  matches "sub-plan F") — F's closing task runs the sweep for "sub-plan
  F" / "tier 2b" / "F's" mentions across `src/` (`env.py` alone names F
  at lines 6, 14, 89-94, 605, 751, 1091-1094, 1148, 1190, 1216, 1355,
  1519, 1595), `tests/`, `examples/`, `docs/subset.md`.

**To G, not F (recorded here so the plan does not absorb them)**: the
deploy-gate example choice (one of six; R6); `sandbox/compile.py` still
using `build_wasm`; the sanctioned wording pass (SPT4012, origin fields,
`_HELP` order); text-keyed `test_no_stale_promises` allowlist;
`examples/shapes.py` as a docs source.

## B. THE CHAIN TRUTH (verified 2026-09-02)

### B.1 Network state — testnet is ALREADY on protocol 28

- **K1** `getNetwork` on `https://soroban-testnet.stellar.org` →
  `protocolVersion: 28`; `getLatestLedger` → protocol 28, sequence
  4,467,927; `getVersionInfo` → RPC `28.0.1-273f19e4…`, captive core
  `stellar-core 28.0.1 (947aad84…)`, built 2026-08-27. Mainnet
  (`https://mainnet.sorobanrpc.com`) → protocol **27**, sequence
  64,241,199. S10's spec note is stale; U4 resolves to **protocol 28**.
- **K2** Consequence for F: the tier-2b host must be a 28-line host or
  the tier-3 leg compares different protocols. A 27-line host (spike2's
  pin) would still RUN serpent artifacts (they declare floors 20/22) but
  the differential would then have a protocol axis between its legs.

### B.2 Host-crate lineage (crates.io + GitHub at tag)

- **K3** `stellar-core` `v28.0.1` (what testnet runs) embeds
  `soroban-env-host` as submodule `src/rust/soroban/p28` at commit
  `ba37ea5f76a10710835992fb90f9ec7a14eca499` = rs-soroban-env tag
  **v28.0.0** (its optional `fastdev` block pins the same rev). Tags:
  v28.0.0 `ba37ea5f`, v28.0.1 `7a7629cf`, v28.0.2 `5061e9c4`. The
  p27 slot is `b03d2563` = v27.0.0.
- **K4** crates.io (2026-09-02): `soroban-env-host` 28.0.2 (2026-08-17),
  28.0.1, 28.0.0, 27.0.1, 27.0.0. `soroban-sdk` stable **27.0.6**
  (2026-08-13) pins `soroban-env-host =27.0.1`; `soroban-sdk`
  **28.0.0-rc.1** (2026-08-25) pins `soroban-env-host =28.0.2`,
  `stellar-xdr =28.0.0`, `stellar-strkey =0.0.16`. No stable sdk 28 yet.
  `soroban-env-host` 28.0.2's own pins: `soroban-env-common =28.0.2`,
  `soroban-wasmi =0.31.1-soroban.20.0.1`, `wasmparser =0.116.1`;
  features `testutils = ["soroban-env-common/testutils",
  "recording_mode"]`, `recording_mode`, `unstable-next-api`.
- **K5a** The repo's `env.json` pin is **v28.0.2** (process.md state) —
  the SAME version as crates.io's latest env-host and the sdk rc's pin.
  Embedding 28.0.2 makes the host bindings the emitter compiles against
  and the host the tests run on the same release. The 28.0.0 → 28.0.2
  delta is patch-level within one protocol (the protocol IS the semantic
  contract; patch releases are bug fixes) — recorded as a risk in §F, not
  a blocker.
- **K5b** Local cargo registry cache holds env-host ≤ 27.0.1 and sdk ≤
  27.0.6 only; a 28-line build needs a network `cargo fetch` (fine; the
  Cargo.lock is committed for reproducibility).

### B.3 The Host API F builds on (rs-soroban-env at v28.0.2, `soroban-env-host/src/`)

Two viable Rust surfaces; E1 below chooses.

**Raw `Host` (`host.rs`, `testutils.rs`, feature `testutils`)** —
constructors `Host::test_host()`, `test_host_with_recording_footprint()`
(`testutils.rs:190,202`), `.test_budget(cpu, mem)` (211);
`register_test_contract_wasm(&[u8]) -> AddressObject` (303) and the
`_from_source_account` form (266); `current_test_protocol()` (172) and
`set_test_ledger_info_with_current_test_protocol()` (176);
`set_ledger_info(LedgerInfo)` / `with_ledger_info` / `with_mut_ledger_info`
/ `get_ledger_protocol_version` (`host.rs:567-640`); `LedgerInfo {
protocol_version, sequence_number, timestamp, network_id: [u8;32],
base_reserve, min_temp_entry_ttl, min_persistent_entry_ttl, max_entry_ttl }`
(`ledger_info.rs:4-13`); invocation via the `Env` trait impl
(`impl VmCallerEnv for Host`, `fn call` / `fn try_call` at
`host.rs:2479,2515`) or the top-level
`invoke_function(HostFunction) -> Result<ScVal, HostError>`
(`host/frame.rs:1259`, ScVal in/out — the natural XDR boundary);
`with_test_contract_frame` / `try_with_test_contract_frame`
(`frame.rs:684,709`) to run storage reads "as the contract";
`inject_val(&ScVal) -> Val` (`host.rs:3802`); events
`get_events()` / `get_contract_events()` / `get_diagnostic_events()`
(`events/mod.rs:232-254`, `HostEvent`, `Events(pub Vec<HostEvent>)`);
auth `switch_to_recording_auth(disable_non_root_auth)`,
`set_authorization_entries`, `get_recorded_auth_payloads()`,
`get_authenticated_authorizations()` (`host.rs:532-546`; `auth.rs:3021,
3131`); budget `budget_cloned()`, `charge_budget`,
`set_shadow_budget_limits` (`host.rs:648-656`) with `Budget::
get_cpu_insns_consumed/get_mem_bytes_consumed/…_remaining/reset_default`
(`budget.rs:1410-1449`); TTL introspection
`get_contract_data_live_until_ledger`, `get_contract_instance_live_until_
ledger`, `get_contract_code_live_until_ledger` (`host.rs:3850-3923`);
resources `get_last_invocation_resources` /
`get_detailed_last_invocation_resources` (3971, 3981);
`try_finish(self) -> (Storage, Events)` (765) — `Storage { footprint,
mode, map }` are `pub(crate)` (`storage.rs:183-187`), so footprint
inspection goes through the sdk snapshot path or a `pub` accessor probe
at implementation time; `enable_debug()` / `set_diagnostic_level` (660,
666). `HostError { pub error: Error, info: Option<Box<DebugInfo>> }`
(`host/error.rs:26-29`); `Error::is_type(ScErrorType)` is the
discrimination (P4).

**`soroban-sdk` test `Env` (`soroban-sdk/src/env.rs` at v28.0.0-rc.1,
feature `testutils`)** — wraps exactly one `Host` (`env.host()` at 673):
`Env::new_with_config(EnvTestConfig)` (683), `register(contract,
constructor_args) -> Address` (896; takes wasm bytes as the spike did),
`register_at` (978), `upload` (1023), `invoke_contract` /
`try_invoke_contract` (469, 492), `storage()` / `events()` / `ledger()`
(370-383) usable inside `as_contract(&id, || …)` / `try_as_contract`
(1891, 1961), auth `set_auths`, `mock_auths(&[MockAuth])`,
`mock_all_auths`, `mock_all_auths_allowing_non_root_auth`, `auths() ->
Vec<(Address, AuthorizedInvocation)>` (1408-1688), `cost_estimate()`
(822) and `budget()` (2103), `logs()` (627), snapshots `to_snapshot` /
`from_snapshot` / `to_ledger_snapshot` (1989-2073). This is "how the
Rust SDK itself tests" (S4) and is what spike2 used (P1).

### B.4 The tier-3 fixture source — the deployed shapes contract (U5)

- **K6** `stellar contract fetch --id CDEU7Q4D… --network testnet`
  returned 4,171 bytes, sha256
  `6a9dd13549bac20f2609ab3d74668963b5249a7943dc7f027cdf6c42bec86e33`.
  `build_file(Path("examples/shapes.py")).wasm` at main tip `f90abc2`
  (tree-identical to the re-signed E2 tip `57668f9`) is 4,171 bytes with
  the SAME sha256 — byte-identical, Phase 0's byte-fidelity check in
  reverse. `examples/shapes.py` last changed in `87028aa` (2026-09-01,
  docstring-only guidance correction; docstrings are emitted into
  `contractspecv0`, so the deploy post-dates or equals that commit's
  bytes). The build commit is therefore recoverable as "any commit from
  87028aa to main whose `examples/shapes.py` bytes are unchanged".
- **K7** Consequence: F's tier-3 runner has a deployed contract whose
  bytes it can rebuild from source, whose spec it can decode, and whose
  every entry point is a read-only or state-writing method that
  `simulateTransaction` can evaluate WITHOUT signing or submitting
  (simulation applies the footprint against current ledger state and
  returns results/events/diagnostics). Read-only simulation is not a
  deploy and not an outward-facing write (D1, U3). State-mutating rows
  (`set`/`bump`-style) simulate but do not persist; sequences that DEPEND
  on a prior write cannot be replayed by simulation alone — tier 3's
  fixture corpus is therefore "single-invocation from a known ledger
  state" unless a signed submission is approved.

### B.5 Toolchain on this machine (verified 2026-09-02)

- **K8** `cargo 1.97.1`, `rustc 1.97.1`, `uvx maturin` → 1.15.0 (PyPI
  latest 1.15.0); `pyo3` latest stable 0.29.2 (2026-08-28; spike2 pinned
  0.23.5 — API drift between 0.23 and 0.29 is real: `Bound<'py, T>`
  everywhere, `PyResult` conventions, `#[pyo3(get)]` unchanged);
  `wasm-tools 1.258.0`; `stellar 27.1.0` (stellar-xdr 27.0.0; a 27 CLI
  reads 28-network wasm fine, K6); wasmtime-py 48.0.0 pinned as dev dep
  (PyPI latest 48.0.0); Python 3.11.7; 12 cores.

### B.6 spike2 facts F inherits or corrects (P1–P13 summarized as design constraints)

- Boundary protocol: ScVal XDR bytes (P12) — reuse; serpent already
  depends on `stellar-sdk>=15,<16` in the `spec` extra (D 2026-08-27),
  whose `scval` helpers build/parse ScVal.
- `unsendable` (P9) — inherit; document "one env per thread".
- Discrimination `is_type(ScErrorType::Contract)` (P4) — inherit, but
  surface as a Python exception HIERARCHY, not string prefixes.
- Panic containment (P3): pre-validate Symbol/Address/wasm at the
  boundary AND wrap every PyO3 entry in `std::panic::catch_unwind`
  (spike2 never did) so a residual panic becomes a catchable
  `Exception` subclass, never `PanicException`.
- `capture_snapshot_at_drop: false` (P11) — inherit if the sdk `Env`
  route is chosen.
- Build entry point (P6, P8): `VIRTUAL_ENV=<root>/.venv uvx maturin
  develop --release`; commit `.cargo/config.toml` with the macOS link
  args so `cargo check`/`cargo test` work for the Rust unit layer.

## C. WHAT EXISTS IN THE REPO TODAY (main `f90abc2`; suite 4222 passed / 2 skipped in 46 s)

### C.1 The mini-host rig (`tests/harness/`, 1,979 lines, dev-only by D's E1)

- **C1** `engine.py`: `make_config()` (51-99) pins twelve wasmtime flags
  with the relaxed-simd-first ordering documented at 72-75; f64 globals and
  extended-const have NO wasmtime-48 Python toggle (63-69). `MiniHost(wasm,
  imports=)` builds a fresh `Engine`/`Store` per instance BY DESIGN
  ("Caching is sub-plan F's productization problem", 133-135);
  `invoke(name, *vals) -> int | None`; `_trampoline` (115-127) is the ONE
  signed/unsigned boundary. Bindings are looked up by name in
  `serpent._host.functions_by_name` (8-11, 162), the same pin the emitter
  compiles against.
- **C2** The feature-set assertion S3 asks for ALREADY EXISTS:
  `tests/unit/test_harness_engine.py` 352-445 — ordering test,
  every-flag-is-a-real-property, the-set-is-exactly-expected, and eleven
  accept/reject behavioral probes. F's residual is O3 (the four
  un-toggleable proposals), provable only on the real host.
- **C3** `objects.py` `ObjectStore`: `bindings()` (18 callbacks),
  `chain_value(word)` SCALAR-ONLY decoder raising `AssertionError` for any
  tag without a class (339-345, A9), `val_word(value)` encoder raising for
  unencodable shapes incl. `Vec` arguments (389), `compare` → tier-1
  `val_cmp`, value-normalised `map_key`/`key_word`. `_RankOnly` /
  `_VecRank` / `_MapRank` (103-134): container-vs-container ordering
  raises `NotImplementedError` (A15). U256/I256 excluded from
  `_NUMERIC_BY_TAG` (144-159).
- **C4** `hostfns.py` `FullHost(ObjectStore)`: binds every host fn the
  compiler can emit; coverage asserted against `recognize.ENV_HOST_FN_
  TARGETS`/`CONTAINER_HOST_FN_TARGETS` in `test_harness_hostfns.py`.
  Documented bends: Symbol ordering over decoded ASCII text (53-61, O12);
  `require_auth` always succeeds (68-75, S17); TTL extensions are logged
  no-ops (487-496, 692-705); no footprint, no budget anywhere in the rig;
  `INVALID_POSITION_ERROR_VAL` / `BAD_STRKEY_ERROR_VAL` / `DIV_ERROR_VAL`
  are unpinned-code CONVENTIONS (111, 116; `i256.py:63`).
- **C5** `i256.py` `Wide256Host`: trunc-toward-zero `i256_div` per A4
  "NOT PINNED IN THIS REPO" (28-33); `*_rem_euclid` deliberately absent
  (34-39). `errors.py`: `HostError(.val)` and `HostTrap` — the vocabulary
  the differential already speaks.

### C.2 The corpora F re-runs

- **C6** `tests/semantics/cases.py`: `CASES` = **59** `SemCase` rows
  (frontend ∈ accepts/rejects/not_expressible; kind ∈ value/contract_error/
  trap/reject; `tier1_only`). Tier-1 leg `tests/semantics/test_semantics.py`
  (eval against `serpent.__all__`); frontend classification
  `tests/unit/test_frontend_semantics.py` (`wrap_case` 59-81, `compile_case`
  84-88, `EXPECTED_TY`/`EXPECTED_CODE`); **WASM leg `tests/unit/
  test_emitter_semantics.py`** with the in-scope predicate stated at
  110-128 (`kind in {value, contract_error, trap} and not tier1_only and
  frontend != "not_expressible"`), `IN_SCOPE_COUNT = 35` asserted (134-138),
  the two-step wrapper (`wrap_returning` + `annotation_of` read from the
  compiler's own `Ty`), `decode_val(word, ty, host)` with tag-set checks,
  and the Symbol-ordering vector asserted to give the tier-1 answer ON THE
  MINI HOST (421-441 — tautological until the real host runs it).
- **C7** `tests/semantics/env_scenarios.py`: `ENV_SCENARIOS` = **62**
  frozen `EnvScenario` rows (`contract: Path`, `constructor`, `timestamp`,
  `sequence`, `auth_allow_set`, `setup: tuple[Call | Advance, ...]`,
  `invoke: Call`, `kind ∈ value/void/contract_error/auth_failed`, `expect`,
  `code`, `events`, `auths`, `tier1_only_reason`). Three reason constants
  `TTL_REASON` / `AUTH_ARGS_REASON` / `ALLOW_SET_REASON` (102-117);
  `Advance(ledgers)` documented as "tier-1 only by construction ... no
  analogue on either the mini host or the chain" (143-151) — TRUE for the
  mini host, FALSE for the real host (it has a settable ledger sequence,
  K-B.3). Legs: `test_env_differential.py::_tier_1` (194-231) and `::_wasm`
  (234-272; build cached per path, host fresh per row, 157-166);
  biconditional guard 364-385 derives reach from `TTL_METHODS`/
  `AUTH_ARGS_METHODS`. Expected two-model divergences named in-file:
  publish-then-raise (439-445, 531-552) and auth-refusal-not-recorded
  (479-488).
- **C8** Examples (`examples/*.py`, six) pass the compile / tier-1 / WASM
  "same answers" triple in `tests/unit/test_examples.py`; union/enum
  entry points return `Symbol`/`U32`/`Bool` because of O5; the
  allowance-token expiry scenario is tier-1-only (O7). Inventories an
  example joins: `FIXTURES` + `EXAMPLES` (`test_emitter_end_to_end.py:
  108-128`), `FIXTURE_NAMES` + golden (`test_emitter_printer.py:381-397`),
  `_FIXTURES` (`test_harness_hostfns.py:999-1010`), `CORPUS`
  (`test_frontend_fuzz.py:866-880`), `_EVENT_CORPUS`
  (`test_env_differential.py:562-567`), mypy `files` (pyproject:80).
- **C9** Fixtures: `tests/fixtures/env_surface.py` exists specifically to
  reach `ENV_SCENARIOS` shapes no example reaches (bare `get` without
  default, `require_auth_for_args`).

### C.3 Packaging, gates, CI

- **C10** `pyproject.toml`: core `dependencies = []` (zero-dep, enforced by
  `test_core_zero_dep.py`); ONE extra `spec = ["stellar-sdk>=15,<16"]`;
  dev group `pytest>=8, pytest-cov>=7, hypothesis>=6, ruff>=0.16,
  mypy>=2,<3, stellar-sdk>=15,<16, wasmtime==48.0.0`. **No `testing`
  extra, no pytest markers registered, no `addopts`**; `testpaths =
  ["tests"]`. mypy `strict`, `files = ["src", "tests", "examples"]`,
  exclude `^tests/must_reject/`. ruff line-length 100, `src = ["src",
  "tests", "spikes"]`, format scope `src tests examples`.
- **C11** The skip-loudly convention in code: `test_emitter_build.py:
  136-160` (three-way: forced-on raises when absent; forced-off skips;
  default proven with the real tool under `skipif(shutil.which(...) is
  None)`). Skips in the tree today: `requires_spike_wasm` (git-ignored
  Phase 0 artifact; `test_emitter_end_to_end.py:134`, `test_sections.py:
  89`), the two TTL `pytest.skip(_UNMODELLED)` naming F
  (`test_env_ttl.py:349,357`), and three wasm-tools `skipif`s
  (`test_emitter_validate.py:501-528`).
- **C12** `tests/unit/conftest.py`: `deployed_env(...)` helper and the
  autouse `_no_leaked_invocation_frame` guard. **No `serpent.testing`
  module exists**; `Env.frame()`/`Env.advance()` are test-facing and NOT
  in `serpent.__all__` (`env.py:1440-1442, 1538-1541`; `test_public_api.py`
  pins `__all__`).
- **C13** `.github/workflows/ci.yml`: one `test` job, Python 3.11/3.12/
  3.13, `uv sync --all-groups`, ruff check + format check, mypy, a
  prebuilt `wasm-tools 1.258.0` tarball (explicitly NOT cargo — "minutes
  of build time"), `uv run --frozen pytest -q`. No Rust toolchain in CI.
  No Makefile/justfile. The only `Cargo.toml` is `spikes/spike2/`.
- **C14** `src/serpent/_host/`: `PINNED_TAG = "v28.0.2"`, `UPSTREAM_BLOB_
  SHA` (`_codegen.py:26-28`) checked against upstream by
  `test_host_bindings.py::test_env_json_matches_upstream_blob`;
  `_protocol.py`: `DEFAULT_TARGET_PROTOCOL = 27`, `BASE_PROTOCOL = 20`,
  `CONSTRUCTOR_MIN_PROTOCOL = 22`, `compute_protocol_floor`,
  `check_protocol_target`, `declared_protocol`; `functions_by_name`.
  NOTE `DEFAULT_TARGET_PROTOCOL = 27` while testnet is 28 (K1) — the
  target is a frontend GATE (an upper bound on what a module may use), so
  27 stays valid; whether to bump it to 28 is a one-line G/M2 decision
  with no artifact-hash consequence (floors, not targets, are emitted).
- **C15** `src/serpent/env.py` (1,751 lines) names sub-plan F at 6, 14,
  89-91, 605, 751, 1091-1096, 1190, 1216, 1519 (TTL clamp/trap, auth
  logic, `get_max_live_until_ledger`, `del_` absent-key, rollback).
  `serpent.errors` module docstring (30-44) names "Tier 2b (sub-plan F)"
  for the 128-bit `//0` re-confirmation. `DEFAULT_LEDGER_TIMESTAMP` /
  `DEFAULT_LEDGER_SEQUENCE` live in the D9 shared home.

## D. THE PROPOSED ARCHITECTURE (the smallest thing that makes S1 true)

The one-sentence design: **a second, real-host leg for every table the
repo already has, exposed to users as `serpent.testing`, with the mini-host
left as the dev-loop rig it is, and testnet reduced to recorded fixtures
until a live run is approved.**

### D.1 The Rust crate — `serpent-host` (a SEPARATE distribution, one PyO3 class)

- Location `host/` at the repo root (own `Cargo.toml`, `pyproject.toml`
  with `[build-system] maturin`, `.cargo/config.toml` carrying P8's macOS
  link args, committed `Cargo.lock`). Distribution name `serpent-host`,
  import name `serpent_host` (R1's name). Core `serpent` wheel stays pure
  Python and zero-dep (C10); `serpent[testing]` becomes the extra that
  pulls `serpent-host` + `stellar-sdk` + `pytest` (U1: from source in M1,
  wheels in M3).
- Pins: `soroban-sdk = "=28.0.0-rc.1"` with `testutils` (→ `soroban-env-
  host =28.0.2` = `env.json` v28.0.2, K4/K5a); `pyo3 = "0.29"` with
  `extension-module` behind a cargo feature maturin enables (so `cargo
  test` links a libpython for the Rust unit layer — plan-time probe).
- ONE `#[pyclass(unsendable)] RealEnv` (P9) whose methods are all
  bytes-in/bytes-out ScVal XDR (P12) and all wrapped in
  `std::panic::catch_unwind` + boundary pre-validation (P3, §B.6):
  `new(ledger: LedgerInfo-subset)`, `protocol_version()`,
  `register(wasm, ctor_args_xdr) -> addr_str`, `invoke(addr, fn,
  args_xdr) -> result_xdr` raising a structured error (type name, code,
  is_contract, diagnostic text), `set_ledger(...)` over the FULL
  `LedgerInfo` subset serpent needs (protocol, sequence, timestamp,
  network_id, min/max entry TTLs — the clamp/trap facts need
  `max_entry_ttl`), `storage_get/has(addr, durability, key_xdr)`,
  `ttl(addr, durability, key_xdr) -> live_until | None`, `events() ->
  list[xdr]`, `auths() -> list[xdr]`, `budget() -> (cpu, mem)`,
  `footprint() -> (ro_keys_xdr, rw_keys_xdr)`, `mock_all_auths()` /
  `mock_auths(entries_xdr)`. Rust stays dumb: no serpent type knowledge,
  no Python object construction beyond bytes/str/int/None.
- Rust unit tests: the discrimination matrix (P4 — a host `Context
  (InvalidAction)` is NEVER reported as contract code 6/7), the three
  panic sources return errors (P3), `capture_snapshot_at_drop: false`
  leaves no `test_snapshots/` (P11).

### D.2 The Python layer — `serpent.testing` (ships in the core package, imports optional deps lazily)

- `serpent/testing/__init__.py` (public), `_scval.py` (ChainValue ↔
  `stellar_sdk.xdr.SCVal`, built on `serpent.val` + `serpent.spec.typemap`
  conventions: struct fields sorted (P7 of C), union = Vec led by the
  variant Symbol, int enum = bare U32, Bytes family one ScVal kind, Address
  strkey; decoding is DRIVEN BY THE REQUESTED `ty`, exactly like tier-1
  `get`'s D11 re-typing — this is the decoder O4/O5 want, and it is
  ScVal-based so it is also what tier 3 decodes with), `_real.py`
  (`RealEnv` façade over `serpent_host.RealEnv`: `deploy(cls, *args)`,
  `invoke`, storage read-back typed by `ty`, `events()` → tier-1
  `PublishedEvent`s, `auths()` → `RecordedAuth`s, `ledger.set()`,
  `budget()`, `footprint()`), `_errors.py` (a hierarchy: `RealHostError
  (error_type, code)` base, `RealContractError(code)` for Contract-typed
  errors mapped back to the deployed class's `@contracterror` member when
  known, `HostPanic` for contained panics — never `RuntimeError`, never a
  leaked `PanicException`), `_marker.py` (the `real_host` pytest marker +
  the loud skip whose reason IS the rebuild command, U2/P6).
- Same verbs as tier-1 `Env`/`deploy` wherever semantics coincide, so a
  user's tier-1 test re-points at the real host by swapping one fixture —
  S6's "same table, two legs" applied to user tests.

### D.3 The differential runner — one module, four legs, three corpora

- Legs: `tier1` (Env model), `mini` (2a, `tests/harness`), `real` (2b),
  `testnet` (3, fixture replay). Corpora: `CASES` in-scope (35), `ENV_
  SCENARIOS` (62), the examples' same-answers tests, plus ONE new F-owned
  table `HOST_FACTS` (the S9/O10–O15/O24 facts pinned today against
  models only: TTL clamp/trap/dead-entry/archive, `del_` absent, publish-
  then-raise rollback, auth refusal + not-recorded, footprint read/write
  sets, 128-bit `//`/`%` on negative dividends and `//0`/`%0`'s real XDR
  code, container ordering, the four un-toggleable wasm proposals). The
  frozen tables stay frozen (D8 precedent: E added its OWN table).
- Comparison policy (O28): legs compared to each other FIRST, then to the
  pinned expectation. Two new row-level declarations: **expected
  model/host divergence** (`host_diverges=<reason>`; the runner asserts
  the divergence EXISTS so a model fix that removes it is loud) and
  **frozen-table escalation** (a real-vs-tier-1 mismatch on a `CASES` row
  raises `FrozenTableDisagreement`, the implementer returns BLOCKED, the
  controller rules — O12's procedure made mechanical).
- `EnvScenario` mapping onto the real leg: `constructor` → `register`
  with args; `timestamp`/`sequence` → `set_ledger`; `auth_allow_set` →
  `mock_auths` for exactly those addresses (a REAL allow-set; `None` →
  `mock_all_auths`); `Advance(n)` → sequence += n (real TTL semantics
  follow); `events`/`auths` → decoded from the host. Every
  `tier1_only_reason` row becomes runnable on the real leg → the field is
  renamed to what it is (`mini_host_gap`, O17) in a licensed edit to the
  frozen module's METADATA (rows unchanged).

### D.4 Tier 3 — a fixture runner, simulation-only

- `serpent.testing.testnet` (or `tests/tier3/`): builds an
  `InvokeHostFunction` transaction for a CONFIGURED existing source
  account (public key only — simulation needs no signature), calls
  `simulateTransaction` via `stellar_sdk.SorobanServer`, decodes the
  result/events/footprint, and either RECORDS a fixture (network, needs
  `--record`) or REPLAYS a committed fixture (default, no network). The
  replay test asserts tier 1, the real host, and the recorded testnet
  answer agree per K6's shapes contract, entry point by entry point;
  fixture headers carry contract id, wasm sha256, ledger sequence,
  protocol, and RPC version. **No signing or `sendTransaction` code path
  exists in M1** (D1/U3 made structural); state-dependent sequences are
  out of tier-3 scope until a submission is approved (K7).

### D.5 Tier-2a productization, scoped

Only: (i) a session-scoped compiled-module cache keyed by source path +
content hash (O1; the host stays fresh per row, C7's rule); (ii) the
public typed container decoder + `Vec` argument encoding (O4/O5, small
API additions to `ObjectStore`, no new semantic models — O8); (iii) the
`FullHost` docstrings' "F's obligation" wording repointed at the real
leg's evidence. NOT: footprint/budget/TTL/auth models in the mock (E6
below).

### D.6 Closing tasks

Promise sweep for "sub-plan F"/"tier 2b"/"F's" across `src/`, `tests/`,
`examples/`, `docs/` (O33; the `_REPOINTED` gate already recognises F);
spec S10 correction note; `docs/testing.md` (tiers, the rebuild command,
the marker, the divergence-declaration vocabulary); process.md state;
decisions.md entries for every §E ruling; the attention file for the
Fable final review.

## E. OPEN QUESTIONS FOR THE CONTROLLER (recommendation first)

- **E1 Rust embedding surface** — raw `soroban-env-host::Host` vs
  `soroban-sdk` test `Env`. RECOMMEND the sdk `Env` at `=28.0.0-rc.1`:
  the spike-proven path (P1), literally "how the Rust SDK itself tests"
  (S4), one surface for register/mock_auths/as_contract/events/auths/
  budget/snapshots (B.3), with `env.host()` exposing the raw `Host` for
  what the sdk lacks (`get_contract_data_live_until_ledger`, full
  `LedgerInfo`). Cost: an rc pin — accepted because M1 is unpublished,
  `Cargo.lock` is committed, the env-host it resolves (=28.0.2) is exactly
  the `env.json` pin, and a drift test asserts `protocol_version() == 28
  == PINNED_TAG's major`. Alternative (raw Host, no rc) re-implements
  plumbing the sdk already has. Reversal: crate-internal.
- **E2 Marshalling boundary** — ScVal XDR bytes via `stellar_sdk` (P12)
  vs a serpent-native ScVal codec. RECOMMEND XDR via `stellar_sdk` in a
  single `serpent.testing._scval` module driven by the requested `ty`;
  `serpent[testing]` requires `serpent[spec]`'s dep. A native codec is a
  third Val implementation (S6/§10 drift rule). Reversal: module-local.
- **E3 Exception hierarchy** — RECOMMEND `RealHostError(error_type, code)`
  → `RealContractError(code, member)` / `HostPanic`; Contract-typed errors
  map to the deployed class's `@contracterror` member so the tier-1 and
  real legs raise COMPARABLE objects; host errors carry
  `(ScErrorType, ScErrorCode)` names. No `RuntimeError` strings (P13).
- **E4 Panic containment** — RECOMMEND both belts: pre-validation
  (Symbol charset/limit, strkey shape, `wasmparser`-level module sanity
  before `register`) AND `catch_unwind` at every PyO3 method, with
  negative tests for the three P3 sources asserting an `Exception`
  subclass and a still-usable env afterwards.
- **E5 Layout and install path** — RECOMMEND `host/` as a separate
  `serpent-host` distribution (D.1), installed in M1 by U1's exact
  `VIRTUAL_ENV=<root>/.venv uvx maturin develop --release`, NOT as a uv
  workspace member (a maturin member would make `uv sync` require cargo,
  violating U2). The `uv sync` prune trap (P6) is mitigated by: the skip
  reason printing the rebuild command; `docs/testing.md`; and a plan-time
  probe of `uv sync --inexact` / `uv run --no-sync` as the documented dev
  form. Wheels/workspace are M3's shape (U1).
- **E6 Footprint recording's tier** — S3 places "mandatory footprint
  recording" under tier 2a, but the mock has no footprint concept and O8
  forbids growing the mock. RECOMMEND re-reading S3's clause onto tier 2b
  where the host RECORDS footprints for real: `HOST_FACTS` rows and
  examples declare expected read/write key sets; the mock is documented
  footprint-blind. decisions.md entry (it re-reads a spec sentence).
- **E7 What "productized tier 2a" means** — RECOMMEND D.5's three items
  only; the mini-host stays `tests/harness/` (dev-only, D's E1) and is
  NOT shipped. The shipped user-facing test surface is `serpent.testing`
  over the real host + tier-1 `Env`. Rationale: with the real host at
  ~20 µs/invoke (P1) a shipped mock has no user value and a permanent
  fidelity liability (S1).
- **E8 The `tier1_only_reason` rename** — RECOMMEND `mini_host_gap`
  (O17) with the three reason constants' text corrected (O18), the
  `Advance` docstring corrected (C7), and the biconditional guard kept.
  A licensed edit to the frozen module's METADATA; rows untouched.
- **E9 Expected-divergence rows** — RECOMMEND the `host_diverges=`
  declaration (D.3) for publish-then-raise (O15) and any `HOST_FACTS` row
  where the model is KNOWN to be wrong-by-omission; the runner asserts the
  divergence is present. The alternative (fixing the tier-1 model to roll
  events back) is an oracle edit outside F's charter — recorded as an M2
  candidate once the host evidence exists.
- **E10 Frozen-table disagreement procedure** — RECOMMEND
  `FrozenTableDisagreement` → BLOCKED → controller ruling → decisions.md
  (O12). Pre-registered: the Symbol `_`-vs-`A` vector is the row most
  likely to trip it; `i256_div` rounding (O10) is the second.
- **E11 Version pins and drift tests** — RECOMMEND: `soroban-sdk
  =28.0.0-rc.1` (→ env-host =28.0.2); a test that the embedded protocol ==
  the major of `_codegen.PINNED_TAG` == every tier-3 fixture header's
  protocol; `DEFAULT_LEDGER_*` fed from D9's shared home; `pyo3 0.29`;
  Cargo.lock committed. Upgrading to sdk 28.0.0 stable is an M3 wheel-time
  patch bump.
- **E12 Container ordering** — the real host can now answer the A15
  question the tier-1 `_cmp_payload` defers. RECOMMEND F RECORDS the
  observed Vec/Map/union ordering as `HOST_FACTS` evidence and hands the
  tier-1 implementation to M2 (an oracle edit; Opus-seated) — unless the
  observed order is plain lexicographic-by-`val_cmp`, in which case a
  ≤20-line tier-1 implementation may land inside F under a scoped Opus
  review. Ruling needed because it touches the value layer.
- **E13 Re-route O32 to G** — the bridge-needle generalisation and
  parameter shadowing are frontend hygiene, not testing-tier work.
  RECOMMEND re-routing to G's hygiene pass; record in F's attention file.
- **E14 Tier-3 source account** — simulation needs an EXISTING account's
  public key. RECOMMEND the runner takes it from config/env; the F
  recording task asks Elliot in-session for an account to use (no
  friendbot call — funding a throwaway is an outward-facing write, D1).
  Replay needs nothing. Also: `stellar_sdk`'s RPC client is in the `spec`
  extra already.
- **E15 Real-host harness for the 128-bit `//0` code** — `DIV_ERROR_VAL`
  is a harness CONVENTION (O11). RECOMMEND F pins the real XDR
  `(ScErrorType, ScErrorCode)` observed from the host into one shared
  constant home and makes the harness convention name it (a small
  licensed edit to `tests/harness/i256.py`), so tests stop asserting a
  stand-in.
- **E16 Scope fence** — F ships NO SPT codes, NO language surface, NO
  emitter change. If a real-host run reveals an emitter bug, it is fixed
  in F's branch under Opus review as an out-of-plan fix (the E2 Task 8
  precedent) and recorded in the ledger — not silently, not deferred.

## F. RISKS

### F.1 Where F can be hollow or silently wrong

- **F.1.1 Hollow differential**: a real leg that re-encodes the answer
  through the same Python code as the tier-1 leg proves nothing about the
  host. Mitigation: the real leg decodes from ScVal XDR the HOST produced
  (E2), compared as tier-1 values; the runner asserts at least one
  `host_diverges` row exists (proof the host is really being asked).
- **F.1.2 Spoofed discrimination** (P4): a host-internal failure reported
  as a contract code. Mitigation: E3's typed hierarchy + the Rust matrix
  test + a Python test that a MISSING function is a host error, never a
  contract error.
- **F.1.3 Uncatchable panics** (P3): `PanicException` escaping `pytest.
  raises`. Mitigation: E4; a test that each known panic source yields an
  `Exception` subclass.
- **F.1.4 Protocol skew**: host 28.0.2 vs testnet's embedded 28.0.0
  (K3). Patch-level; recorded. If a tier-3 fixture disagrees with the
  real leg, the FIRST hypothesis is patch delta, checked by pinning
  `=28.0.0` in a scratch build before ruling.
- **F.1.5 rc dependency** (E1): `soroban-sdk 28.0.0-rc.1` API could shift
  before stable. Mitigation: Cargo.lock; the crate's surface is ~15
  methods; the M3 bump is budgeted.
- **F.1.6 Vacuous skips** (U2/D4): the real-host suite skipping in CI
  forever. Mitigation: `SERPENT_REQUIRE_REAL_HOST=1` turns the skip into a
  failure; G's Rust job sets it; the skip count is asserted zero there.
- **F.1.7 The maturin/uv traps** (P6/P8): the wrong interpreter, the
  pruned module, the unlinkable `cargo build`. Mitigation: `.cargo/
  config.toml`; `docs/testing.md`; the skip reason; a smoke test that
  `serpent_host.__file__` lives under the repo's `.venv`.
- **F.1.8 Frozen-table temptation**: "fixing" tier 1 to match the host
  inside an implementer's task. Mitigation: E10's structural BLOCKED path;
  subagents never touch `cases.py`/`env_scenarios.py` rows.
- **F.1.9 Tier-3 outward writes**: a runner that can submit. Mitigation:
  no signing/`sendTransaction` code exists (D.4); recording requires an
  explicit `--record` and network; a test asserts the module imports no
  signing symbol.
- **F.1.10 Thread pinning** (P9): pytest-xdist threads or a fixture
  shared across threads → pyo3 panic. Mitigation: per-test env; document
  process-only parallelism.
- **F.1.11 `Limits::none()`** on XDR decode admits inputs the network
  would reject; harmless for tests, noted so nobody mistakes it for a
  validity check.

### F.2 Checks that belong in F's own test plan

- The Rust discrimination matrix; the three panic sources; snapshot files
  absent after a run; `protocol_version() == 28 == PINNED_TAG major`.
- `_scval` round-trip properties over every M1 type family (Hypothesis)
  and goldens against `stellar_sdk.scval` for scalars, plus the union/
  enum/struct conventions against the E2 dossier's byte-verified shapes.
- All 35 in-scope `CASES` on the real leg; `IN_SCOPE_COUNT` unchanged.
- All 62 `ENV_SCENARIOS` on the real leg with ZERO `mini_host_gap` rows
  skipped (the real leg runs them all); the biconditional guard still
  green for the mini leg.
- `HOST_FACTS`: each S9 fact has a row; each row states which legs run it
  and which are declared divergent.
- Examples: the six same-answers triples become quadruples; shapes'
  union/enum returns decoded via `ty` on the real leg (lifts O5).
- Tier 3: replay of every shapes entry point against committed fixtures;
  a header-consistency test (wasm sha256 == `build_file` at HEAD, K6).
- Promise sweep: zero un-repointed "sub-plan F" mentions at close.

### F.3 Process risks

- F is the first sub-plan with a Rust build in the loop: task briefs must
  state the exact build command and that debug builds are 11x slower
  (P1) so implementers do not time out perf-sensitive tests.
- Network access at execution time (crates.io fetch, K5b; RPC for
  recording). Recording tasks are controller-run or explicitly approved
  (E14); fetch is a one-time cost.
- The seating rule: everything that decodes host answers or declares an
  expected divergence is semantics-critical (Opus); the Rust crate's
  plumbing and the docs are mechanical (Sonnet); Fable for the final
  review.
