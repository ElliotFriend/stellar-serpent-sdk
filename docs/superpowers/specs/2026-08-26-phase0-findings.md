# Phase 0 Findings and Gate Decision

**Date:** 2026-08-26
**Plan:** `docs/superpowers/plans/2026-08-26-phase0-spikes.md`
**Spec:** `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`
**Branch:** `phase0` (10 commits, `c2d1dfd..dd9de2a` plus this report)

## Gate decision: GO (with spec changes)

Both Phase 0 risks are retired with on-chain and real-host evidence. M1 may proceed
once the spec amendments in §4 are decided. Nothing found invalidates the
architecture; several plan-level assumptions were corrected by evidence, all in ways
the M1 design absorbs cleanly.

## 1. Acceptance matrix results (all 10 rows)

Contract (designed authoring style, compiled by the spike emitter):
**`CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI`** (testnet).
Module: 877 bytes, sha256 `bc2e8063…a9920`, byte-identical on fetch; the on-chain
`ContractInstance.executable.wasm_hash` is that same digest.

| # | Check | Result |
|---|-------|--------|
| 1 | `wasm-tools validate` (chain feature set) | PASS — gate runs inside `build.py` before the artifact is written; negative control proven (corrupted opcode → no file) |
| 2 | Memory declared + exported as `memory`, data section present | PASS — plus a build-time ABI assertion when any `*_linear_memory` fn is imported |
| 3 | `stellar contract info interface` renders functions/struct/error enum | PASS locally pre-deploy and on-chain (stdout byte-identical) |
| 4 | Deploy + fetch byte-fidelity | PASS — first attempt, no retries (corroborated by gapless account sequence numbers) |
| 5 | `setup(3)`; `bump()`×3 → 1, 2, 3 on-chain | PASS — decoded from transaction result meta, not just CLI output |
| 6 | 4th `bump()` surfaces contract error code 7 | PASS — structured `SCE_CONTRACT` code 7 in the diagnostic XDR (`Error(Contract, #7)`), a real `fail_with_error`, not a trap |
| 7 | Struct round-trips through instance storage | PASS — behaviorally, and directly: `getLedgerEntries` shows `SETTINGS => map{counter_limit: u32 3, display_name: "serpent phase zero"}` on-chain |
| 8 | Same bytes in the wasmtime mini-host | PASS — 1, 2, 3, error 7; ABI prologue rejects a mistyped arg |
| 9 | Sections via `stellar_sdk` XDR byte-match goldens | PASS — env-meta golden exact; spec section renders in the Rust tooling |
| 10 | `mypy --strict` findings recorded with resolutions | PASS — three finding classes; see §4 |

### Gate-time re-verification (Task 8/final review)

Three checks cited above were re-run read-only at gate time by two independent
reviewers via read-only RPC, beyond what the Task 6 evidence docs recorded:

- **Row 4 (no retries):** corroborated by gapless account sequence numbers.
- **Row 5 (`bump()` returns 1, 2, 3):** decoded directly from transaction result
  meta, not just CLI output.
- **Row 7 (struct round-trips through instance storage):** proven directly, not
  just behaviorally — `getLedgerEntries` on the `ContractInstance` entry shows
  `SETTINGS => map{counter_limit: u32 3, display_name: "serpent phase zero"}`
  on-chain.

## 2. Spike 2: PyO3-embedded real host — ADOPT as tier-2b

The same 877 bytes reproduce testnet behavior exactly on an embedded
`soroban-env-host` (via `soroban-sdk` 27.0.6 testutils, resolved env-host 27.0.1):
1, 2, 3, then contract error 7 discriminated correctly from host errors.

- **Performance:** 20.4 µs per invocation (release; 20.2 µs on the fix-round
  re-run) (~49k/sec, release build; debug ≈
  worse and `maturin develop` defaults to debug — documented). Fresh
  `RealEnv()`+`register`: 0.133 ms. This is *faster* than the wasmtime mini-host and
  carries real auth/budget/storage/comparison semantics.
- **Cost:** 140 lines of Rust, clean first compile, one `cdylib` + maturin.
- **Recommendation adopted:** tier-2b (real host) becomes the authoritative local
  test engine in M1, exactly as the spec's §8 hoped. The wasmtime mini-host remains
  the fast dev-loop tier.
- **Open M1 design item:** the host's protocol ceiling is compiled in, so the wheel
  matrix becomes platform × Python × protocol. Needs a distribution decision before
  M1's testing package ships (candidates: build-from-source via maturin as default,
  prebuilt wheels per protocol tag, or pinning testing-extra releases to protocol
  releases).

## 3. Surprises (evidence-corrected assumptions)

Things the plan or its briefs asserted that evidence overturned — each is now
encoded in code, tests, or this report:

1. **`map_new_from_linear_memory` keys are `(u32 ptr, u32 len)` descriptor pairs,
   not Vals.** The wrong layout validates and then panics on-chain. Confirmed in
   `env.json` docs and `mem_helper.rs`/`data_helper.rs` @ v28.0.2. (Related:
   value words read from guest memory are in *relative* handle space — the spike
   handles this correctly.)
2. **No panic-free `&str → Symbol` path exists in soroban-sdk 27.0.6.**
   `Symbol::try_from_val` advertises `Error = ConversionError` but panics internally
   via `unwrap_optimized` → `.unwrap()`. M1 lesson: **treat "returns `Result`" as an
   unverified claim in this SDK**; pre-validate at the Python/Rust boundary
   (`SCSYMBOL_LIMIT` + `[a-zA-Z0-9_]`). Residual panic sources in the spike wrapper:
   `Address::from_string`, `Env::register` (documented, out of spike scope).
3. **The brief's error mapping would have spoofed the headline assertion.** With
   `E = soroban_sdk::Error`, the `Err(Ok(e))` arm catches *all* host errors, and
   `ScErrorCode::InternalError = 7` — a host-internal failure would have printed
   "contract error code 7". Fixed by discriminating `is_type(ScErrorType::Contract)`
   (`get_type` deliberately does not exist). This exact discrimination belongs in
   M1's tier-2b wrapper and its tests.
4. **wasmtime Config ordering hazard:** `wasm_relaxed_simd = False` must be set
   before `wasm_simd = False` or the Engine constructor hard-aborts the process
   (uncatchable). Comment lives at the fix site; M1's feature-set assertion test
   must preserve the ordering.
5. **A leaked operand passes `wasm-tools validate`** (polymorphic `return` tolerates
   stack leftovers), so the emitter's own balance check must live at `ret()` — it
   now does, with failure-path tests proven RED against the pre-fix code.
6. **`stellar keys generate --global` no longer exists** (CLI 27.1.0); the plan was
   corrected pre-execution by the adversarial review.
7. **maturin venv trap:** invoked from a subdirectory with its own `pyproject.toml`,
   maturin silently built against a self-created Python 3.12 venv. Correct form:
   `VIRTUAL_ENV=<repo-root>/.venv uvx maturin develop --release`; also, any
   `uv sync` prunes the maturin-installed module.
8. **The brief's masking assertion (`err & 0xFF == 3`) cannot detect a missing u64
   mask** (Python's `&` is sign-invariant on the low byte). The mini-host pins the
   full 64-bit word instead, and masking is enforced structurally via a single
   trampoline over all eight host callbacks.

## 4. Spec §2 amendment proposal (from acceptance row 10)

`mypy --strict` on the designed authoring surface produces three finding classes.
One is solved; two need a decision (options below, recommendation first):

- **SOLVED — `@contracttype` kwargs construction:** `typing.dataclass_transform` on
  the decorator clears it. Adopt into the spec.
- **DECIDE — methods without `self`:** `def setup(env: Env, ...)` fails strict mypy
  ("self parameter missing") and there is no zero-plugin decorator escape.
  Options: (a) **recommended:** contract methods take `self` as first parameter
  (`def setup(self, env: Env, ...)`) — pythonic, strict-clean, puya-precedented; the
  compiler ignores `self`; (b) keep env-first and ship a documented one-line
  `# type: ignore[misc]` convention (keeps the Rust-like surface, taxes every
  method); (c) a mypy plugin (contradicts the spec's zero-plugin claim — reject).
- **DECIDE — `raise Error.LimitExceeded` raises an int:** strict mypy rejects
  raising a non-BaseException. Options: (a) **recommended:** `@contracterror` makes
  each member an exception *class* (so `raise Error.LimitExceeded` is valid Python
  and strict-clean; the compiler reads the code from the class attribute);
  (b) `raise Error(Error.LimitExceeded)` (noisier); (c) documented ignore (taxes
  every raise site).

The spec's "contracts type-check in any IDE with zero plugins" claim is otherwise
**confirmed** — `py_compile` and default-strictness IDE checking pass today; the
amendments above make `--strict` pass too.

## 5. Effort actuals

Wall-clock for the whole phase: one working session (8 tasks, 2 fix rounds, all
reviews). Per-task subagent effort ran well above the plan's nominal step sizing on
the two hard tasks (emitter, PyO3) — consistent with the spec's honest M1 sizing
(months, not weeks). The spike surfaced eight evidence-corrected assumptions (§3);
each would have cost far more discovered mid-M1.

## 6. What M1 inherits

- **Keep (as reference evidence, still throwaway):** `spikes/spike1` (frontend, emitter,
  sections, mini-host + 37 tests across both spikes (31 + 6)), `spikes/spike2` (PyO3 wrapper,
  FINDINGS.md). M1 rewrites these properly under `src/serpent/`; the spike tests
  become golden references.
- **Facts for the spec appendix:** items §3.1–§3.5 above.
- **Design inputs:** tier-2b adoption + wheel-matrix decision (§2); §4 amendments;
  frontend minors (module-docstring skip, synthetic error location); emitter minors
  (fixed import set is fixture-specific; `_Memory.intern` align guard); deferred
  minors listed in the SDD ledger.
