# M1-F: Testing Tiers Implementation Plan (v2, post-adversarial-review)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make spec §8's sentence "the real host is the release gate" true:
every table this repo already runs against two models it wrote itself (the
35 in-scope semantics cases, the 62 `ENV_SCENARIOS` rows, the six examples)
gains a leg on the REAL `soroban-env-host` (protocol 28, the host testnet
runs), a new F-owned `HOST_FACTS` table pins the facts the models only
assumed (TTL clamp/trap, rollback, `del_` absent, 128-bit division, container
ordering, footprint counts, the un-toggleable wasm proposals), users get
`serpent.testing` to write contract tests against that host, and tier 3
becomes a simulation-only fixture runner proven against the deployed shapes
contract.

**Architecture:** Ruling E1/E5: ONE PyO3 class (`serpent_host.RealEnv`, a
separate `serpent-host` distribution under `host/`, built from source in M1
by maturin) wrapping the `soroban-sdk` test `Env` at `=28.0.0-rc.1` (→
`soroban-env-host =28.0.2`, exactly the `env.json` pin). Rust stays DUMB:
every method is ScVal-XDR bytes in and out, every method is wrapped in
`catch_unwind`, every failure is one structured `HostFailure` exception the
Python layer re-raises as a typed hierarchy (E3/E4). Ruling E2: ONE Python
module (`serpent.testing._scval`) marshals tier-1 chain values ↔ `SCVal`,
decoding DRIVEN BY THE REQUESTED `ty` exactly like tier-1 `get`'s re-typing
(D11) — this is the decoder O4/O5 wanted and the one tier 3 decodes with.
Ruling E9/E10: the runner compares legs to each other FIRST (O28); a row may
DECLARE an expected model/host divergence (`host_diverges=`) which the runner
asserts EXISTS; an undeclared mismatch on a frozen-table row raises
`FrozenTableDisagreement` and the implementer returns BLOCKED. Ruling E7: the
mini-host stays `tests/harness/` (dev-only, unshipped); its productization is
exactly three items (Task 8). Ruling E14: tier 3 has NO signing or
`sendTransaction` code path — simulation only, fixtures committed, replay
needs no network.

**Tech Stack:** Rust 1.97 / pyo3 0.29 / maturin 1.15 / soroban-sdk 28.0.0-rc.1
(`testutils`) for `host/`; Python 3.11+, `stellar-sdk 15` (`scval`, `xdr`,
`SorobanServer`) for `serpent.testing`; pytest markers; Hypothesis; the
existing M1-D build (`serpent.emitter.build_file`) and mini-host
(`tests/harness`) for the 2a legs.

**Spec:** `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`
(§8, §10, §12, §13). **Dossier (citation target for every S/R/D/P/U/O/K/C ID
and §-reference below):** `docs/superpowers/specs/2026-09-02-m1f-inputs-dossier.md`.
**Rulings:** `docs/superpowers/decisions.md` 2026-09-02 "M1-F rulings (dossier
E1-E16, all recommendations adopted)" — binding. **Verified ground truth the
plan rests on (dossier §B):** testnet is on protocol 28 (core 28.0.1 embeds
env-host v28.0.0 `ba37ea5`); crates.io `soroban-env-host` 28.0.2 = the repo's
`PINNED_TAG = "v28.0.2"`; `soroban-sdk 28.0.0-rc.1` pins `=28.0.2`; the
deployed shapes contract `CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW`
is 4,171 bytes, sha256 `6a9dd135…6e33`, byte-identical to
`build_file(Path("examples/shapes.py")).wasm` at main tip.

> **v2 note.** The adversarial plan review returned **10 Blockers, 13 Majors,
> 16 Minors, verdict AMEND THEN EXECUTE** — triage in
> `.superpowers/sdd/2026-09-02-m1f-testing-tiers/plan-review.md`; rulings in
> decisions.md 2026-09-02 "M1-F plan-review rulings". The controller adopted
> ALL findings (two with corrections: M2 observes container order through the
> host's `Compare` trait, not `obj_cmp`; tier 3 replays the DEPLOYED bytes as a
> committed artifact). The review's own summary is worth carrying: the one-
> pyclass / ScVal-XDR / `catch_unwind` / `HostFailure` surface compiles verbatim
> on pyo3 0.29, the sdk `Env` at 28.0.0-rc.1 runs serpent's protocol-20/22
> artifacts unmodified, and every name Tasks 4-9 import exists under the name
> used. What failed: **B1, a shipped emitter bug the real host finds
> immediately** — every `Symbol` compare lowers to `obj_cmp`, which the host
> refuses for two small operands; the mini host accepted it; the deployed
> shapes contract traps on `area` on testnet today. v2 adds **Task 0** for the
> fix (E16 amended). The other structural changes: the host reports ONE frame-
> level error for every guest failure, so evidence lives in the UNDERLYING
> `(type, code)` from diagnostics (B5); allow-set authorizers must be CONTRACT
> addresses on the test host (B2); `deploy` takes a source PATH (B3); the TTL
> readout is relative and the clamp lands on `max_ttl()` (B9/B10); the require-
> real-host policy fails through `pytest_runtest_setup` (B4); a direct
> `soroban-env-host` dependency (B6); the test host does not model archival
> (M3); tier 3 seeds the contract-INSTANCE entry (M4). The produces-before-
> consumes trace was re-verified after inserting Task 0.

> **First-run pins.** A handful of table cells (`EXPECTED_UNDERLYING_ERROR`
> in Task 4, every `HostErr(type, None)` code in Task 6, the archived-persistent
> outcome) are host FACTS this repo does not have until the real host runs.
> They are the plan's only deliberate blanks: the task fills them from the first
> run, with the run date in a comment, and re-runs green. They are not
> placeholders for design work.

## Global Constraints

- **Frozen surfaces.** `spikes/` read-only (R3; G's cleanup owns it). The
  59 `CASES` rows in `tests/semantics/cases.py` and the 62 `ENV_SCENARIOS`
  rows in `tests/semantics/env_scenarios.py` are NOT edited by any task:
  Task 5's edit to `env_scenarios.py` is METADATA ONLY (a field rename, two
  new optional fields, docstrings, the three reason constants' text) and is
  listed in the licensed-edits contract below. `src/serpent/compiler/codes.py`
  and `docs/superpowers/decisions.md` are controller-owned (D12): F ADDS NO
  SPT CODE; an implementer who thinks one is needed returns BLOCKED. **No
  emitter or frontend change is in any task EXCEPT Task 0** (E16 as amended
  by the plan-review rulings: the small-Symbol `obj_cmp` lowering fix); any
  OTHER emitter bug the real host reveals is an out-of-plan fix under Opus
  review, ledgered.
- **Licensed edits (the exhaustive list of pre-existing files any task may
  modify; anything else is BLOCKED):** `src/serpent/emitter/**` and
  `tests/goldens/wasm/*` + `tests/unit/test_emitter_*.py` pins (Task 0 ONLY:
  the Symbol-compare lowering, its runtime part, the regenerated
  disassembly goldens and size tripwires); `pyproject.toml` (Task 2:
  `testing` extra; Task 3: `[tool.pytest.ini_options] markers`, `[tool.mypy]
  mypy_path` for the `.pyi`), `.gitignore` (Task 1),
  `tests/unit/test_core_zero_dep.py` (Task 2: the exemption grows to `spec/`
  AND `testing/`, FOUR sites), `tests/semantics/env_scenarios.py` (Task 5:
  metadata + the `_ADMIN` constant per the B2 ruling), `tests/unit/
  test_env_differential.py` (Task 5: the rename; Task 8: `_built` → the
  shared cache), `tests/unit/test_no_stale_promises.py` (Task 5: the E net's
  allowlist migrates to text keys; Task 10: the F net), `tests/unit/
  test_frontend_fuzz.py` (Task 6: the EXACT fixture inventory gains
  `host_facts.py`), `tests/harness/testmod.py` (Task 6: `custom_sections`),
  `tests/harness/objects.py` (Task 8: `chain_value_as`, `val_word`
  containers), `tests/harness/i256.py` (Task 8: `DIV_ERROR_VAL` names the
  pinned UNDERLYING host code), `tests/harness/hostfns.py` + `tests/harness/
  engine.py` (Task 8 + Task 10: docstring repoints only), `tests/unit/
  test_examples.py` + `tests/unit/test_emitter_end_to_end.py` (Task 8:
  `_built`/`build_fixture` → the shared cache), `tests/fixtures/udt_style.py`
  (Task 7: `current_shape`), `src/serpent/env.py`, `src/serpent/errors.py`,
  `tests/unit/test_env_ttl.py`, `README.md`, `examples/shapes.py` (Task 10:
  module docstring only — probe-verified NOT to change the emitted bytes),
  `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`
  (Task 10: prose only), `docs/superpowers/process.md` (Completion).
- **A test that reads only tables or metadata is NEVER `real_host`-marked**
  (review M12): real-leg modules mark per-test, never module-level, so the
  coverage/coherence/non-vacuity meta-tests run on a Rust-less checkout.
- **Errors have two levels (B5).** The real host reports a FRAME-level
  `(error_type, code)` that is `("Context", 6)` for every guest-side failure
  except a contract's own `fail_with_error`; the true classification is the
  innermost `Error(Type(Code))` DIAGNOSTIC event, exposed as
  `RealHostError.underlying`. Every host-fact assertion asserts `underlying`;
  `error_type`/`code` are asserted only for the Contract/non-Contract split
  (P4). No map of expected errors may have all-identical values.
- **Gates on every task, non-negotiable:** `uv run pytest -q`,
  `uv run mypy --strict src tests` (mypy's `files` already includes
  `examples`; `src/serpent/testing/` is under `src` so it is strict by
  construction), `uv run ruff check .`, `uv run ruff format --check src tests
  examples`. **Rust gate (Tasks 1, 3, 9 — any task that touches `host/`):**
  `cd host && cargo fmt --check && cargo clippy --all-targets -- -D warnings
  && cargo test`, then the rebuild
  `VIRTUAL_ENV=<repo-root>/.venv uvx maturin develop --release
  --manifest-path host/Cargo.toml` and `SERPENT_REQUIRE_REAL_HOST=1 uv run
  --no-sync pytest -q tests/real_host` (the `--no-sync` matters: a plain
  `uv run` may prune the maturin-installed module, P6). The four Python
  gates must ALSO pass with the extension absent (`uv run pytest -q` on a
  checkout that never ran maturin skips `tests/real_host` loudly, U2) — Task
  3 pins that with a subprocess test.
- **Build facts every task brief repeats (P1, P6, P8):** `maturin develop`
  DEFAULTS TO DEBUG and debug is ~11x slower — always `--release`. The
  extension is ONLY reachable through the repo's `.venv`; `uv run --with
  maturin` from `host/` builds against the wrong interpreter. `cargo build`
  cannot link the cdylib on macOS without `host/.cargo/config.toml` (Task 1
  commits it). `uv sync` prunes the module; re-run maturin after any sync.
- **Model seating (process.md):** Opus for Tasks 1, 2, 3, 4, 5, 6, 9
  (anything that decodes host answers, declares an expected divergence, or
  discriminates errors); Sonnet for Tasks 7, 8, 10 and every scoped
  re-review; Fable for the final whole-branch review. Set the model
  explicitly on dispatch.
- **Escalation is structural, not advisory (E10):** a real-leg test on a
  `CASES` or `ENV_SCENARIOS` row that disagrees with tier 1 raises
  `serpent.testing.FrozenTableDisagreement` (Task 3) and the implementer
  returns BLOCKED with the row name and both answers. No implementer edits
  a frozen row or the tier-1 model to make a real-leg test pass.
- **Ledger defaults have ONE home (D9):** `DEFAULT_LEDGER_TIMESTAMP` /
  `DEFAULT_LEDGER_SEQUENCE` come from `serpent.env`; the real host's other
  `LedgerInfo` fields are constants in `serpent/testing/_real.py`
  (`DEFAULT_MIN_TEMP_ENTRY_TTL = 16`, `DEFAULT_MIN_PERSISTENT_ENTRY_TTL =
  4096`, `DEFAULT_MAX_ENTRY_TTL = 6_312_000`, `DEFAULT_BASE_RESERVE =
  5_000_000`, `DEFAULT_NETWORK_ID = bytes(32)`), named once, imported
  everywhere.
- **Drift pins (E11):** `serpent_host.RealEnv(...).protocol_version() ==
  host_protocol_ceiling() == 28 == int(PINNED_TAG.removeprefix("v").split(".")[0])`
  (`==`, not `>=`: a p29 host would silently skew K2); the ledger protocol
  is read through `env.ledger().get().protocol_version` (the trait method is
  deprecated and fails `-D warnings`); every tier-3
  fixture header's `protocol` equals the same number; `host/Cargo.lock` is
  committed; `pyo3 = "0.29"`, `soroban-sdk = "=28.0.0-rc.1"`.
- **Conventions:** conventional commits, no emoji, no em dashes, Oxford
  commas, AI attribution trailer on model-authored commits; 1Password
  signing with the ~40 s fallback to `--no-gpg-sign` + `.git/unsigned-commits.log`
  (check for a `gpgsig` header before logging). Branch `m1f-testing-tiers`
  from main. Docstrings explain WHY and cite the dossier ID; every "not
  modelled"/"not proven" sentence names where the proof lives (O33's
  three outcomes: IMPLEMENTED / REPOINTED / REMOVED).
- **Produces-before-consumes trace:** Task 0 produces the corrected Symbol
  lowering (no new names; regenerated goldens) → Task 1 produces `serpent_host` (the
  raw class + `HostFailure`) → Task 2 produces `_scval.encode/decode` (needs
  nothing from Task 1) → Task 3 consumes both, produces `RealEnv`,
  `RealContract`, the exception hierarchy, the marker, the conftest skip,
  the drift test → Task 4 consumes Task 3 (`RealEnv` + `FrozenTableDisagreement`)
  → Task 5 consumes Task 3 and produces `mini_host_gap`/`host_diverges` →
  Task 6 consumes Task 3, produces `HOST_FACTS` + the pinned 128-bit `//0`
  code constant → Task 7 consumes Task 3 → Task 8 consumes Task 6's
  constant → Task 9 consumes Tasks 2/3 (`_scval`, `RealEnv.storage(...).set`)
  → Task 10 consumes everything (sweep). No task imports a name a later task
  defines.

## File Structure

```
src/serpent/emitter/...                 Task 0 ONLY — the Symbol-compare lowering + one runtime part
tests/goldens/wasm/*.wat                Task 0 — regenerated disassembly snapshots (READ THE DIFF)
host/                                   NEW — the serpent-host distribution
  Cargo.toml, Cargo.lock, pyproject.toml, .cargo/config.toml, README.md
  serpent_host.pyi                      the typed surface mypy sees (m8); listed in [tool.mypy] mypy_path
  src/lib.rs                            RealEnv pyclass, HostFailure, catch_unwind, validation
  src/errors.rs                         classify(soroban_sdk::Error) -> (type name, code, is_contract)
  src/diagnostics.rs                    innermost Error(Type(Code)) from get_diagnostic_events (B5)
  src/validate.rs                       symbol / strkey / wasm pre-validation (pure, unit-tested)
src/serpent/testing/                    NEW — public test surface (imports stellar_sdk lazily)
  __init__.py                           __all__: RealEnv, RealContract, errors, marker name
  _errors.py                            RealHostError, RealContractError, HostPanic, RealHostUnavailable,
                                        FrozenTableDisagreement
  _scval.py                             encode(value) -> SCVal; decode(scval, ty) -> value; decode_loose
  _real.py                              RealEnv / RealContract / RealStorage / ledger constants
  _marker.py                            REAL_HOST_MARKER, is_available(), REBUILD_COMMAND, unavailable_reason()
  testnet.py                            tier 3: simulate(), record_fixture(), load_fixture(), Fixture
tests/conftest.py                       NEW — root conftest: skip-or-fail policy for the real_host marker
tests/real_host/                        NEW — every real-leg test lives here (all marked real_host)
  test_serpent_host_module.py           Task 1 smoke + discrimination + panic containment
  test_real_env.py                      Task 3 façade + drift pin + skip-policy subprocess test
  test_semantics_real.py                Task 4
  test_env_scenarios_real.py            Task 5
  test_host_facts_real.py               Task 6 (real leg) + test_feature_set_real.py (un-toggleable proposals)
  test_examples_real.py                 Task 7
  test_testnet_fixtures.py              Task 9 (replay; NOT marked real_host for the header checks,
                                        marked for the three-way compare)
  fixtures/testnet/shapes/*.json        Task 9 recorded fixtures
tests/semantics/host_facts.py           NEW — the F-owned HOST_FACTS table (Task 6)
tests/fixtures/host_facts.py            NEW — the contract HOST_FACTS drives (Task 6)
tests/unit/test_scval.py                Task 2
tests/unit/test_host_facts_tier1.py     Task 6 (tier-1 leg of the modelled rows)
tests/unit/test_harness_objects.py      NEW — Task 8 (chain_value_as, val_word containers, cache)
tests/harness/cache.py                  NEW — built(path) compiled-module cache (Task 8)
tests/real_host/fixtures/testnet/shapes/deployed.wasm   Task 9 — the 4,171 deployed bytes (K6)
docs/testing.md                         NEW — Task 10
```

---

### Task 0: The small-Symbol compare lowering (review B1; E16 as amended)

**Files:**
- Modify: `src/serpent/emitter/` — the compare lowering (`_lower_compare` and
  the `_via_obj_cmp` route in `arith.py`/`lower.py`; the implementer locates
  the exact functions from `serpent/emitter`'s dispatch) and the runtime-part
  inventory (one new part `symsmall_cmp`)
- Modify: `tests/goldens/wasm/*.wat` for every `FIXTURE_SOURCES` entry whose
  bytes change (regenerate with the command in `tests/unit/test_emitter_printer.py`'s
  header and READ THE DIFF); the size tripwires in `tests/unit/test_emitter_fuzz.py`
  if a fixture crosses one
- Test: `tests/unit/test_emitter_symbol_compare.py` (new), plus the existing
  `test_emitter_semantics.py` (mini-host leg) and Task 4's real leg as the proof

**Interfaces:**
- Consumes: `serpent.val` (`TAG_SYMBOL_SMALL`, `symbol_char_code`,
  `SYMBOL_CHARS`, `is_object`); the pinned `soroban-env-common` source at
  v28.0.2 (`src/symbol.rs` `impl Ord for SymbolSmall`, `src/compare.rs` the
  `Compare<Val>` symbol special case) — READ BOTH before writing a byte.
- Produces: no new Python names. The lowering contract: for `Symbol ==`/`!=`
  the emitted code is `if is_object(a) || is_object(b) then obj_cmp(a, b) == 0
  else a == b` (canonical small packing makes word equality exact); for
  `Symbol <, <=, >, >=` it is `if is_object(a) || is_object(b) then obj_cmp
  else symsmall_cmp(a, b)`, where `symsmall_cmp` is a guest runtime part that
  reproduces `SymbolSmall`'s ordering EXACTLY as the pinned source defines it
  (the review's reading: `Iterator::cmp` over DECODED characters, i.e. ASCII
  order; if the source says packed-body order instead, THAT is what the part
  implements — the part mirrors the host, never tier 1). Every other type's
  compare lowering is untouched.

- [ ] **Step 1: Read the ground truth and write it down.** Open the two pinned
  files (curl the raw GitHub URLs at tag `v28.0.2`), quote the `SymbolSmall`
  ordering definition and the host's small-vs-small / small-vs-object branch
  verbatim into the new test module's docstring with the URL and tag. If the
  host's small-vs-small order is NOT decoded-ASCII, STOP and return BLOCKED:
  tier 1 (`Symbol.__lt__`, ASCII) would then be wrong and that is a controller
  decision on the frozen table (E10/O12), not this task's.

- [ ] **Step 2: Failing tests.** `tests/unit/test_emitter_symbol_compare.py`:
  build a contract with `def eq(self, env, a: Symbol, b: Symbol) -> Bool: return Bool(a == b)`
  and `def lt(...) -> Bool: return Bool(a < b)`; disassemble via
  `serpent.emitter.printer.disassemble`; assert the `eq` body contains NO
  unconditional `call $obj_cmp` before an `i64.eq` on the operands (a
  structural assertion on the instruction list: the `obj_cmp` call is inside
  a branch guarded by the object-tag test), and that `lt` links the
  `symsmall_cmp` part (`built.runtime_parts_linked`). Then the BEHAVIORAL pins
  under the mini host via `FullHost`: `eq(Symbol("ab"), Symbol("ab"))` is
  `True`, `eq(Symbol("ab"), Symbol("abcdefghijk"))` is `False`,
  `lt(Symbol("A"), Symbol("_"))` per Step 1's order, and — the point — a
  `FullHost` whose `obj_cmp` REFUSES two non-object words (add a strict mode
  flag to the test's host subclass, not to `tests/harness`) runs all four
  without calling `obj_cmp`. Run: FAIL (the current lowering calls `obj_cmp`
  unconditionally; the strict host raises).

- [ ] **Step 3: Implement.** The tag test is `(word & 0xFF) >= 64` per
  `val.is_object`; the `symsmall_cmp` part decodes both bodies six bits at a
  time from the high end (A3's SymbolSmall layout: 9 chars × 6 bits, high-
  order-first, zero-padded) and compares per the Step 1 order, returning
  -1/0/1 as an i64; register it in the runtime-part namespace beside
  `tagcheck_bytes_n` (D's E3 ruling says parts are emitted from the same
  encoder as user code — no WAT). Keep `runtime_parts_needed ⊆ linked`.

- [ ] **Step 4: Regenerate goldens and run the suite.** `uv run pytest -q`;
  regenerate each changed `.wat` golden; read every diff and confirm only
  Symbol-compare sites and the new part moved. `examples/shapes.py`'s bytes
  WILL change (its `area` compares `tag()` results): note the new sha256 in
  the commit message — Task 9 pins the DEPLOYED hash separately.

- [ ] **Step 5: Gates, then commit**

```bash
git add src/serpent/emitter tests/goldens tests/unit/test_emitter_symbol_compare.py tests/unit/test_emitter_fuzz.py
git commit -m "fix(emitter): compare two small Symbols without obj_cmp, which the host refuses"
```

---

### Task 1: The `serpent-host` crate — `RealEnv`, `HostFailure`, containment

**Files:**
- Create: `host/Cargo.toml`, `host/pyproject.toml`, `host/.cargo/config.toml`,
  `host/README.md`, `host/src/lib.rs`, `host/src/errors.rs`, `host/src/validate.rs`
- Create: `tests/real_host/__init__.py` (empty), `tests/real_host/test_serpent_host_module.py`
- Modify: `.gitignore` (add `host/target/`)

**Interfaces:**
- Consumes: nothing from other tasks. `serpent.emitter.build_file(Path) -> BuildResult`
  (`.wasm: bytes`) to build test contracts; `stellar_sdk.scval` in tests only.
- Produces (the Python-visible contract of `serpent_host`, consumed by Task 3):

```python
class RealEnv:  # #[pyclass(unsendable)]
    def __init__(self, *, protocol_version: int, sequence_number: int, timestamp: int,
                 network_id: bytes, base_reserve: int, min_temp_entry_ttl: int,
                 min_persistent_entry_ttl: int, max_entry_ttl: int) -> None: ...
    def protocol_version(self) -> int: ...          # env.ledger().get().protocol_version (NOT the deprecated trait method)
    def host_protocol_ceiling(self) -> int: ...     # soroban_env_host::Host::current_test_protocol()
    def diagnostics(self) -> list[bytes]: ...        # xdr.DiagnosticEvent bytes, the LAST invocation's (B5)
    def compare(self, a_xdr: bytes, b_xdr: bytes) -> int: ...
        # the host's Compare<Val> verdict (-1/0/1) for ANY two Vals, small or object -- NOT obj_cmp,
        # which refuses two small operands (review M2); the O12/E12 ordering evidence
    def max_ttl(self) -> int: ...                    # env.storage().max_ttl(); observed 6_311_999
    def set_ledger(self, *, sequence_number: int | None = None, timestamp: int | None = None) -> None: ...
    def register(self, wasm: bytes, constructor_args_xdr: list[bytes]) -> str: ...   # strkey C...
    def invoke(self, contract: str, function: str, args_xdr: list[bytes]) -> bytes: ...  # ScVal XDR
    def mock_all_auths(self) -> None: ...
    def mock_auths(self, entries: list[tuple[str, str, str, list[bytes]]]) -> None: ...
        # (authorizer CONTRACT strkey, contract strkey, function name, args ScVal XDR).
        # REPLACES the whole entry set (sdk semantics, review M6); the authorizer MUST be a
        # contract strkey (the sdk registers a MockAuthContract AT that address and panics
        # for a G... account, review B2) and must never be the deployed contract's own address.
    def events(self) -> list[bytes]: ...             # xdr.ContractEvent bytes, the LAST invocation's
    def auths(self) -> list[tuple[str, str, str, list[bytes]]]: ...
        # (address, contract, function, args ScVal XDR) for the LAST invocation's ROOT
        # CONTRACT auths; non-contract functions (register's CreateContractV2HostFn,
        # review M8) are SKIPPED, never an error
    def storage_get(self, contract: str, durability: str, key_xdr: bytes) -> bytes | None: ...
        # durability in {"persistent", "temporary", "instance"}
    def storage_has(self, contract: str, durability: str, key_xdr: bytes) -> bool: ...
    def storage_set(self, contract: str, durability: str, key_xdr: bytes, value_xdr: bytes) -> None: ...
    def storage_ttl(self, contract: str, durability: str, key_xdr: bytes) -> int | None: ...
        # RELATIVE: ledgers remaining EXCLUDING the current ledger (testutils::storage), None when
        # absent/expired (the sdk method panics there; contained + mapped). live_until = sequence + ttl.
        # durability "instance" takes NO key: pass b"" or get invalid_input.
    def budget(self) -> tuple[int, int]: ...         # (cpu instructions, memory bytes), last invocation
    def resources(self) -> dict[str, int] | None: ...
        # every InvocationResources field by its Rust name (exhaustive destructure, review M10);
        # None before the first invocation (the sdk panics there, review m14)

class HostFailure(Exception):
    # args == (kind, error_type, code, message)
    # kind in {"contract", "host", "panic", "invalid_input", "conversion"}
    # error_type: ScErrorType variant name ("Contract", "WasmVm", "Context", "Storage", "Object",
    #             "Crypto", "Events", "Budget", "Value", "Auth") or "" when kind != contract/host
    # code: the u32 error code (contract code, or the ScErrorCode discriminant)
```

- [ ] **Step 1: Write the crate manifests and the link config**

`host/Cargo.toml`:

```toml
[package]
name = "serpent-host"
version = "0.0.1"
edition = "2021"
description = "serpent's tier-2b test host: the real soroban-env-host, embedded via PyO3"
license = "Apache-2.0"

[lib]
name = "serpent_host"
crate-type = ["cdylib", "rlib"]

[features]
# maturin enables this (pyproject.toml); `cargo test` runs WITHOUT it so the
# test binary links libpython normally (pyo3's documented pattern).
extension-module = ["pyo3/extension-module"]

[dependencies]
pyo3 = { version = "0.29", features = ["abi3-py311"] }
# Ruling E1/E11: the sdk test Env at the protocol-28 rc; it pins
# soroban-env-host =28.0.2, the same release as src/serpent/_host/_codegen.py's
# PINNED_TAG. Bumping either without the other is the drift Task 3 pins.
soroban-sdk = { version = "=28.0.0-rc.1", features = ["testutils"] }
# The sdk's `env` module is PRIVATE (review B6), so the raw Host API the
# dossier's E1 escape hatch needs (`current_test_protocol`, diagnostics, the
# `Compare` trait) comes from a direct dependency on the SAME release the sdk
# pins -- nothing new enters the lock graph.
soroban-env-host = { version = "=28.0.2", features = ["testutils"] }
# The sdk pins the same version; a direct dep so `validate.rs` can call the
# Result-returning strkey parser without going through Address::from_string.
stellar-strkey = "=0.0.16"

[profile.release]
opt-level = 3
```

`host/pyproject.toml`:

```toml
[project]
name = "serpent-host"
version = "0.0.1"
description = "serpent's tier-2b test host (the real soroban-env-host, embedded via PyO3)"
requires-python = ">=3.11"
license = { text = "Apache-2.0" }

[build-system]
requires = ["maturin>=1.15,<2.0"]
build-backend = "maturin"

[tool.maturin]
module-name = "serpent_host"
features = ["extension-module"]
```

`host/.cargo/config.toml` (P8):

```toml
# `cargo build`/`cargo clippy --all-targets` of a pyo3 extension-module cdylib
# cannot link on macOS without these: Python symbols must stay unresolved
# until the interpreter loads the module. maturin injects them itself; this
# file is what lets plain cargo commands work in the same tree.
[target.aarch64-apple-darwin]
rustflags = ["-C", "link-arg=-undefined", "-C", "link-arg=dynamic_lookup"]

[target.x86_64-apple-darwin]
rustflags = ["-C", "link-arg=-undefined", "-C", "link-arg=dynamic_lookup"]
```

Append to `.gitignore`: `host/target/`.

- [ ] **Step 2: Write `host/src/validate.rs` with its unit tests (pure Rust, no host)**

```rust
//! Boundary pre-validation (P3): nothing crosses into soroban-sdk that could
//! make it panic. "Returns Result" is an unverified claim in this SDK.

pub const SCSYMBOL_LIMIT: usize = 32;

pub fn check_symbol(text: &str) -> Result<(), String> {
    if text.len() > SCSYMBOL_LIMIT {
        return Err(format!(
            "{text:?} is not a valid Symbol: {} bytes exceeds the {SCSYMBOL_LIMIT}-byte limit",
            text.len()
        ));
    }
    if let Some(bad) = text.chars().find(|c| !c.is_ascii_alphanumeric() && *c != '_') {
        return Err(format!(
            "{text:?} is not a valid Symbol: character {bad:?} is outside [a-zA-Z0-9_]"
        ));
    }
    Ok(())
}

/// A contract strkey: 56 chars, base32 alphabet, leading 'C'. The full
/// checksum is verified by `stellar_strkey::Contract::from_string`, which
/// returns `Result` (verified panic-free at 0.0.16 by the Task 1 unit test).
pub fn check_contract_strkey(text: &str) -> Result<(), String> {
    const ALPHABET: &str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
    if text.len() != 56 || !text.starts_with('C') {
        return Err(format!("{text:?} is not a contract strkey (56 chars, leading 'C')"));
    }
    if let Some(bad) = text.chars().find(|c| !ALPHABET.contains(*c)) {
        return Err(format!("{text:?} is not a contract strkey: character {bad:?} is not base32"));
    }
    // Full checksum verification through a Result-returning parser (never a panic).
    stellar_strkey::Contract::from_string(text)
        .map(|_| ())
        .map_err(|e| format!("{text:?} is not a contract strkey: {e:?}"))
}

/// The wasm magic + version; the host's own validator does the rest and
/// reports through `HostError`, not a panic (verified by Task 1's Python test
/// `test_register_of_garbage_is_a_host_failure_not_a_panic`).
pub fn check_wasm_header(bytes: &[u8]) -> Result<(), String> {
    if bytes.len() < 8 || &bytes[0..4] != b"\0asm" || &bytes[4..8] != [1, 0, 0, 0] {
        return Err("not a wasm module: bad magic or version header".to_string());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn symbol_limits_and_charset() {
        assert!(check_symbol("bump").is_ok());
        assert!(check_symbol("a".repeat(32).as_str()).is_ok());
        assert!(check_symbol("a".repeat(33).as_str()).is_err());
        assert!(check_symbol("has-dash").is_err());
        assert!(check_symbol("two words").is_err());
        assert!(check_symbol("").is_ok(), "the host accepts the empty symbol; the frontend never emits it");
    }

    #[test]
    fn strkey_shape() {
        assert!(check_contract_strkey("CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW").is_ok());
        assert!(check_contract_strkey("NOTANADDRESS").is_err());
        assert!(check_contract_strkey("GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY").is_err());
        let mut bad = String::from("CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GN");
        bad.push('0'); // '0' is not base32
        assert!(check_contract_strkey(&bad).is_err());
    }

    #[test]
    fn wasm_header() {
        assert!(check_wasm_header(b"\0asm\x01\0\0\0").is_ok());
        assert!(check_wasm_header(b"hello").is_err());
        assert!(check_wasm_header(b"\0asm\x02\0\0\0").is_err());
    }
}
```

- [ ] **Step 3: Write `host/src/errors.rs` — the discrimination (P4) with unit tests**

```rust
//! One classification for every `soroban_sdk::Error` (P4): the TYPE is tested
//! with `is_type` for each `ScErrorType` variant (`get_type` deliberately
//! does not exist upstream), the code with `get_code`. A `Context(InvalidAction)`
//! must never be reported as contract code 6, and `InternalError = 7` must
//! never impersonate a contract's code 7.

use soroban_sdk::xdr::{ScErrorCode, ScErrorType};
use soroban_sdk::Error;

pub const ALL_TYPES: [(ScErrorType, &str); 10] = [
    (ScErrorType::Contract, "Contract"),
    (ScErrorType::WasmVm, "WasmVm"),
    (ScErrorType::Context, "Context"),
    (ScErrorType::Storage, "Storage"),
    (ScErrorType::Object, "Object"),
    (ScErrorType::Crypto, "Crypto"),
    (ScErrorType::Events, "Events"),
    (ScErrorType::Budget, "Budget"),
    (ScErrorType::Value, "Value"),
    (ScErrorType::Auth, "Auth"),
];

pub struct Classified {
    pub is_contract: bool,
    pub type_name: &'static str,
    pub code: u32,
    pub message: String,
}

pub fn classify(e: Error) -> Classified {
    let (type_name, is_contract) = ALL_TYPES
        .iter()
        .find(|(ty, _)| e.is_type(*ty))
        .map(|(ty, name)| (*name, *ty == ScErrorType::Contract))
        .unwrap_or(("Unknown", false));
    let code = e.get_code();
    let message = if is_contract {
        format!("contract error code {code}")
    } else {
        let code_name = ScErrorCode::try_from(code as i32)
            .map(|c| format!("{c:?}"))
            .unwrap_or_else(|_| code.to_string());
        format!("host error Error({type_name}, {code_name})")
    };
    Classified { is_contract, type_name, code, message }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_contract_error_is_contract_with_its_code() {
        let c = classify(Error::from_contract_error(7));
        assert!(c.is_contract);
        assert_eq!(c.type_name, "Contract");
        assert_eq!(c.code, 7);
        assert_eq!(c.message, "contract error code 7");
    }

    #[test]
    fn internal_error_seven_is_not_a_contract_error() {
        // The P4 spoof: ScErrorCode::InternalError == 7.
        let c = classify(Error::from_type_and_code(ScErrorType::Context, ScErrorCode::InternalError));
        assert!(!c.is_contract);
        assert_eq!(c.type_name, "Context");
        assert_eq!(c.code, ScErrorCode::InternalError as u32);
        assert!(c.message.starts_with("host error Error(Context, InternalError)"));
    }

    #[test]
    fn every_type_is_named() {
        for (ty, name) in ALL_TYPES {
            let c = classify(Error::from_type_and_code(ty, ScErrorCode::InvalidAction));
            assert_eq!(c.type_name, name);
        }
    }
}
```

(Implementer: the exact constructor names on `soroban_sdk::Error` —
`from_contract_error`, `from_type_and_code` — are verified from the pinned
crate at implementation time; if they differ, use the `From<(ScErrorType,
ScErrorCode)>` impl and keep the assertions.)

- [ ] **Step 4: Write `host/src/lib.rs` — the pyclass, containment, bytes-in/bytes-out**

```rust
//! serpent's tier-2b host (dossier §D.1): ONE `#[pyclass(unsendable)]` over
//! the soroban-sdk test `Env`, every method ScVal-XDR bytes in and out, every
//! method wrapped in `catch_unwind`, every failure one `HostFailure`. Rust
//! knows nothing about serpent types; `serpent.testing` (Python) does.

mod errors;
mod validate;

use std::panic::{catch_unwind, AssertUnwindSafe};

use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use soroban_sdk::testutils::{Address as _, EnvTestConfig, Events as _, Ledger as _, LedgerInfo,
                              MockAuth, MockAuthInvoke};
use soroban_sdk::xdr::{Limits, ReadXdr, ScVal, WriteXdr};
use soroban_sdk::{Address, Env, Symbol, TryFromVal, TryIntoVal, Val, Vec as SorobanVec};

create_exception!(serpent_host, HostFailure, PyException,
    "args == (kind, error_type, code, message); see serpent.testing._errors");

fn failure(kind: &str, error_type: &str, code: u32, message: String) -> PyErr {
    HostFailure::new_err((kind.to_string(), error_type.to_string(), code, message))
}

fn invalid(message: String) -> PyErr { failure("invalid_input", "", 0, message) }
fn conversion(message: String) -> PyErr { failure("conversion", "", 0, message) }

/// E4: a residual panic anywhere below becomes a catchable `HostFailure`
/// of kind "panic", never a `pyo3_runtime.PanicException` (P3).
fn contained<T>(f: impl FnOnce() -> PyResult<T>) -> PyResult<T> {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(result) => result,
        Err(payload) => {
            let text = payload.downcast_ref::<String>().cloned()
                .or_else(|| payload.downcast_ref::<&str>().map(|s| s.to_string()))
                .unwrap_or_else(|| "non-string panic payload".to_string());
            Err(failure("panic", "", 0, format!("the embedded host panicked: {text}")))
        }
    }
}

fn scval_from(bytes: &[u8], what: &str) -> PyResult<ScVal> {
    ScVal::from_xdr(bytes, Limits::none()).map_err(|e| invalid(format!("{what}: not ScVal XDR: {e:?}")))
}

fn xdr_bytes<'py>(py: Python<'py>, v: &ScVal) -> PyResult<Bound<'py, PyBytes>> {
    let bytes = v.to_xdr(Limits::none()).map_err(|e| conversion(format!("ScVal -> XDR: {e:?}")))?;
    Ok(PyBytes::new(py, &bytes))
}

fn to_val(env: &Env, bytes: &[u8], what: &str) -> PyResult<Val> {
    let scval = scval_from(bytes, what)?;
    scval.try_into_val(env).map_err(|e| conversion(format!("{what}: ScVal -> Val: {e:?}")))
}

fn address_of(env: &Env, strkey: &str) -> PyResult<Address> {
    validate::check_contract_strkey(strkey).map_err(invalid)?;
    Ok(Address::from_string(&soroban_sdk::String::from_str(env, strkey)))
}

fn symbol_of(env: &Env, name: &str) -> PyResult<Symbol> {
    validate::check_symbol(name).map_err(invalid)?;
    Symbol::try_from_val(env, &name).map_err(|e| invalid(format!("{name:?}: {e:?}")))
}

#[pyclass(unsendable)]
struct RealEnv { env: Env, invoked: std::cell::Cell<bool> }

#[pymethods]
impl RealEnv {
    #[new]
    #[pyo3(signature = (*, protocol_version, sequence_number, timestamp, network_id, base_reserve,
                        min_temp_entry_ttl, min_persistent_entry_ttl, max_entry_ttl))]
    #[allow(clippy::too_many_arguments)]
    fn new(protocol_version: u32, sequence_number: u32, timestamp: u64, network_id: &[u8],
           base_reserve: u32, min_temp_entry_ttl: u32, min_persistent_entry_ttl: u32,
           max_entry_ttl: u32) -> PyResult<Self> {
        contained(|| {
            let id: [u8; 32] = network_id.try_into()
                .map_err(|_| invalid("network_id must be exactly 32 bytes".to_string()))?;
            // P11: without this the sdk's Drop writes test_snapshots/*.json into the CWD.
            let env = Env::new_with_config(EnvTestConfig { capture_snapshot_at_drop: false });
            env.ledger().set(LedgerInfo {
                protocol_version, sequence_number, timestamp, network_id: id, base_reserve,
                min_temp_entry_ttl, min_persistent_entry_ttl, max_entry_ttl,
            });
            Ok(RealEnv { env, invoked: std::cell::Cell::new(false) })
        })
    }

    /// `Ledger::protocol_version()` is `#[deprecated]` and fails `-D warnings`
    /// (review B6); the `LedgerInfo` read is the supported form.
    fn protocol_version(&self) -> u32 { self.env.ledger().get().protocol_version }

    /// The compiled-in ceiling (P10), from the env-host crate directly (the
    /// sdk's `env::internal` is private, review B6).
    fn host_protocol_ceiling(&self) -> u32 { soroban_env_host::Host::current_test_protocol() }

    /// The LAST invocation's diagnostic events as XDR (review B5): the innermost
    /// `topics: [error, Error(Type(Code))]` is the real classification the frame-
    /// level error hides behind `Context(InvalidAction)`.
    fn diagnostics<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyBytes>>> {
        contained(|| {
            let host: &soroban_env_host::Host = self.env.host();
            let events = host.get_diagnostic_events()
                .map_err(|e| conversion(format!("diagnostics: {e:?}")))?;
            events.0.iter().map(|ev| {
                let b = ev.event.to_xdr(Limits::none()).map_err(|e| conversion(format!("{e:?}")))?;
                Ok(PyBytes::new(py, &b))
            }).collect()
        })
    }

    /// The host's own `Compare<Val>` verdict for any two Vals (review M2) --
    /// `obj_cmp` refuses two small operands, this does not.
    fn compare(&self, a_xdr: &[u8], b_xdr: &[u8]) -> PyResult<i32> {
        contained(|| {
            use soroban_env_host::Compare;
            let a = to_val(&self.env, a_xdr, "a")?;
            let b = to_val(&self.env, b_xdr, "b")?;
            let host: &soroban_env_host::Host = self.env.host();
            let ord = host.compare(&a, &b).map_err(|e| {
                let c = errors::classify(e.error.into());
                failure("host", c.type_name, c.code, c.message)
            })?;
            Ok(ord as i32)
        })
    }

    fn max_ttl(&self) -> u32 { self.env.storage().max_ttl() }

    #[pyo3(signature = (*, sequence_number=None, timestamp=None))]
    fn set_ledger(&self, sequence_number: Option<u32>, timestamp: Option<u64>) -> PyResult<()> {
        contained(|| {
            self.env.ledger().with_mut(|l| {
                if let Some(s) = sequence_number { l.sequence_number = s; }
                if let Some(t) = timestamp { l.timestamp = t; }
            });
            Ok(())
        })
    }

    fn register(&self, wasm: &[u8], constructor_args_xdr: Vec<Vec<u8>>) -> PyResult<String> {
        contained(|| {
            validate::check_wasm_header(wasm).map_err(invalid)?;
            let mut args: SorobanVec<Val> = SorobanVec::new(&self.env);
            for (i, b) in constructor_args_xdr.iter().enumerate() {
                args.push_back(to_val(&self.env, b, &format!("constructor arg {i}"))?);
            }
            // The sdk `Register` impl for `&[u8]` uploads + instantiates; a
            // host-side rejection of the module surfaces as a HostError the
            // sdk PANICS on (P3's `Env::register` row), which `contained`
            // turns into kind "panic" -- Task 1's Python test pins that a
            // garbage module is catchable. Implementer: if `SorobanVec<Val>`
            // does not implement `ConstructorArgs` at the pin, use
            // `env.deployer().with_address(..).deploy_v2(env.deployer()
            // .upload_contract_wasm(wasm), args)` and keep the contract.
            let addr = self.env.register(wasm, args);
            Ok(addr.to_string().to_string())
        })
    }

    fn invoke<'py>(&self, py: Python<'py>, contract: &str, function: &str,
                   args_xdr: Vec<Vec<u8>>) -> PyResult<Bound<'py, PyBytes>> {
        contained(|| {
            let env = &self.env;
            let addr = address_of(env, contract)?;
            let sym = symbol_of(env, function)?;
            let mut args: SorobanVec<Val> = SorobanVec::new(env);
            for (i, b) in args_xdr.iter().enumerate() {
                args.push_back(to_val(env, b, &format!("arg {i}"))?);
            }
            self.invoked.set(true);
            match env.try_invoke_contract::<Val, soroban_sdk::Error>(&addr, &sym, args) {
                Ok(Ok(val)) => {
                    let scval = ScVal::try_from_val(env, &val)
                        .map_err(|e| conversion(format!("result: Val -> ScVal: {e:?}")))?;
                    xdr_bytes(py, &scval)
                }
                Ok(Err(e)) => Err(conversion(format!("result conversion: {e:?}"))),
                Err(Ok(e)) => {
                    let c = errors::classify(e);
                    Err(failure(if c.is_contract { "contract" } else { "host" },
                                c.type_name, c.code, c.message))
                }
                Err(Err(invoke_err)) => Err(failure("host", "", 0, format!("{invoke_err:?}"))),
            }
        })
    }

    fn mock_all_auths(&self) -> PyResult<()> { contained(|| { self.env.mock_all_auths(); Ok(()) }) }

    fn mock_auths(&self, entries: Vec<(String, String, String, Vec<Vec<u8>>)>) -> PyResult<()> {
        contained(|| {
            let env = &self.env;
            // Build owned Addresses/Vecs first; MockAuth borrows them.
            let mut owned = Vec::new();
            for (who, contract, fn_name, args_xdr) in &entries {
                // CONTRACT strkeys only (review B2): the sdk registers a MockAuthContract at
                // `who`, which panics for an account address; account auth needs real
                // signatures and is M2. `address_of` pre-validates, so this is invalid_input.
                let who = address_of(env, who)?;
                let contract = address_of(env, contract)?;
                let mut args: std::vec::Vec<Val> = std::vec::Vec::new();
                for (i, b) in args_xdr.iter().enumerate() {
                    args.push(to_val(env, b, &format!("mock auth arg {i}"))?);
                }
                owned.push((who, contract, fn_name.clone(), args));
            }
            let invokes: Vec<MockAuthInvoke> = owned.iter().map(|(_, c, f, a)| MockAuthInvoke {
                contract: c, fn_name: f.as_str(), args: SorobanVec::from_slice(env, a), sub_invokes: &[],
            }).collect();
            let mocks: Vec<MockAuth> = owned.iter().zip(invokes.iter())
                .map(|((who, _, _, _), inv)| MockAuth { address: who, invoke: inv }).collect();
            env.mock_auths(&mocks);
            Ok(())
        })
    }

    fn events<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyBytes>>> {
        contained(|| {
            self.env.events().all().events().iter().map(|ev| {
                let b = ev.to_xdr(Limits::none()).map_err(|e| conversion(format!("event: {e:?}")))?;
                Ok(PyBytes::new(py, &b))
            }).collect()
        })
    }

    fn auths<'py>(&self, py: Python<'py>) -> PyResult<Vec<(String, String, String, Vec<Bound<'py, PyBytes>>)>> {
        contained(|| {
            use soroban_sdk::testutils::AuthorizedFunction;
            let env = &self.env;
            env.auths().into_iter().map(|(who, inv)| {
                // `register` itself records a CreateContractV2HostFn authorization
                // (review M8): skip non-contract functions, never error on them.
                let (contract, name, args) = match inv.function {
                    AuthorizedFunction::Contract((c, f, a)) => (c.to_string().to_string(),
                        f.to_string(), a),
                    _ => return Ok(None),
                };
                let args = args.iter().map(|v| {
                    let sc = ScVal::try_from_val(env, &v).map_err(|e| conversion(format!("{e:?}")))?;
                    xdr_bytes(py, &sc)
                }).collect::<PyResult<Vec<_>>>()?;
                Ok(Some((who.to_string().to_string(), contract, name, args)))
            }).filter_map(|r| r.transpose()).collect()
        })
    }

    fn storage_get<'py>(&self, py: Python<'py>, contract: &str, durability: &str, key_xdr: &[u8])
        -> PyResult<Option<Bound<'py, PyBytes>>> {
        contained(|| {
            let env = &self.env;
            let addr = address_of(env, contract)?;
            let key = to_val(env, key_xdr, "key")?;
            if !matches!(durability, "persistent" | "temporary" | "instance") {
                return Err(invalid(format!("durability {durability:?} is not one of persistent/temporary/instance")));
            }
            let got: Option<Val> = env.as_contract(&addr, || match durability {
                "persistent" => env.storage().persistent().get::<Val, Val>(&key),
                "temporary" => env.storage().temporary().get::<Val, Val>(&key),
                _ => env.storage().instance().get::<Val, Val>(&key),
            });
            match got {
                None => Ok(None),
                Some(v) => {
                    let sc = ScVal::try_from_val(env, &v).map_err(|e| conversion(format!("{e:?}")))?;
                    Ok(Some(xdr_bytes(py, &sc)?))
                }
            }
        })
    }

    // storage_has / storage_set follow storage_get's shape exactly: `has(&key)`,
    // `set(&key, &val)` inside `as_contract`. storage_ttl (review B10): the
    // testutils `get_ttl` is RELATIVE and PANICS on an absent or expired entry,
    // and the instance form is `env.storage().instance().get_ttl()` with NO key
    // -- so: pre-check `has` (persistent/temporary) and return None when absent;
    // wrap the call in its own catch_unwind and map a panic to None (expired);
    // for "instance", require `key_xdr.is_empty()` (else invalid_input). The
    // Python tests below exercise each branch.

    fn budget(&self) -> PyResult<(u64, u64)> {
        contained(|| {
            let b = self.env.cost_estimate().budget();
            Ok((b.cpu_instruction_cost(), b.memory_bytes_cost()))
        })
    }

    fn resources<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, pyo3::types::PyDict>>> {
        // `cost_estimate().resources()` PANICS before the first invocation (review
        // m14); the façade tracks whether an invoke has happened and this returns
        // None until then. Exhaustive destructure (review M10): a field the host
        // adds is a COMPILE error here, never a silent omission.
        if !self.invoked.get() { return Ok(None); }
        contained(|| {
            let soroban_env_host::InvocationResources {
                instructions, mem_bytes, disk_read_entries, memory_read_entries, write_entries,
                disk_read_bytes, write_bytes, contract_events_size_bytes,
                persistent_rent_ledger_bytes, persistent_entry_rent_bumps,
                temporary_rent_ledger_bytes, temporary_entry_rent_bumps,
            } = self.env.cost_estimate().resources();
            let d = pyo3::types::PyDict::new(py);
            d.set_item("instructions", instructions)?;
            d.set_item("mem_bytes", mem_bytes)?;
            d.set_item("disk_read_entries", disk_read_entries)?;
            d.set_item("memory_read_entries", memory_read_entries)?;
            d.set_item("write_entries", write_entries)?;
            d.set_item("disk_read_bytes", disk_read_bytes)?;
            d.set_item("write_bytes", write_bytes)?;
            d.set_item("contract_events_size_bytes", contract_events_size_bytes)?;
            d.set_item("persistent_rent_ledger_bytes", persistent_rent_ledger_bytes)?;
            d.set_item("persistent_entry_rent_bumps", persistent_entry_rent_bumps)?;
            d.set_item("temporary_rent_ledger_bytes", temporary_rent_ledger_bytes)?;
            d.set_item("temporary_entry_rent_bumps", temporary_entry_rent_bumps)?;
            Ok(Some(d))
        })
    }
}

#[pymodule]
fn serpent_host(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RealEnv>()?;
    m.add("HostFailure", m.py().get_type::<HostFailure>())?;
    m.add("HOST_CRATE_VERSION", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
```

(Implementer notes: `network_id: &[u8]` binds a Python `bytes`; `Vec<Vec<u8>>`
binds `list[bytes]`; returns are `PyBytes`, never `Vec<u8>` (which would
become `list[int]`). The review's pyo3 0.29 `cargo check` accepted this
entire signature set verbatim, and `InvocationResources`' twelve fields are
exactly the list above at 28.0.2. `env.register(garbage)` PANICS with a
String payload inside the sdk; `contained` reports it as kind "panic" with
the message — Step 7's test pins that. Also ship `host/serpent_host.pyi`
declaring every method above with these Python types (review m8), and add
`mypy_path = ["host"]` under `[tool.mypy]` in Task 3, so `_real.py` is typed
against it in BOTH environments (extension present or absent) and no
`# type: ignore` is needed anywhere.)

- [ ] **Step 5: `cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test` in `host/`**

Run: `cd host && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test`
Expected: the three `validate` tests and three `errors` tests PASS; clippy clean. The first build fetches the 28-line crates (K5b; ~45 s release build later).

- [ ] **Step 6: Build the extension into the repo's venv**

Run: `VIRTUAL_ENV=$PWD/.venv uvx maturin develop --release --manifest-path host/Cargo.toml`
Expected: `Installed serpent-host-0.0.1`; `uv run --no-sync python -c "import serpent_host, sys; print(serpent_host.__file__); assert '.venv' in serpent_host.__file__"` prints a path under the repo's `.venv`.

- [ ] **Step 7: Write the Python smoke, discrimination, and containment tests**

`tests/real_host/test_serpent_host_module.py`:

```python
"""The raw `serpent_host` extension: the P4 discrimination and the P3 containment.

Everything here talks to the extension WITHOUT `serpent.testing` (Task 3 wraps
it); the point is that the Rust layer's contract -- bytes in, bytes out, one
`HostFailure` shape, no `PanicException` -- holds on its own. Skipped loudly
when the extension is not built (`tests/conftest.py`, ruling U2).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from stellar_sdk import scval
from stellar_sdk.xdr import SCVal

from serpent.emitter import build_file
from serpent.env import DEFAULT_LEDGER_SEQUENCE, DEFAULT_LEDGER_TIMESTAMP

serpent_host = pytest.importorskip("serpent_host")

pytestmark = pytest.mark.real_host  # every test here drives the extension; no table-only tests live in this module

_ROOT = Path(__file__).resolve().parents[2]
COUNTER = _ROOT / "examples" / "counter.py"
ERRORS = _ROOT / "examples" / "errors.py"
ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"


def _env() -> object:
    return serpent_host.RealEnv(
        protocol_version=28,
        sequence_number=DEFAULT_LEDGER_SEQUENCE,
        timestamp=DEFAULT_LEDGER_TIMESTAMP,
        network_id=bytes(32),
        base_reserve=5_000_000,
        min_temp_entry_ttl=16,
        min_persistent_entry_ttl=4096,
        max_entry_ttl=6_312_000,
    )


def _u32(xdr_bytes: bytes) -> int:
    return scval.to_uint32(SCVal.from_xdr_bytes(xdr_bytes))


def test_the_counter_example_runs_on_the_real_host() -> None:
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    assert cid.startswith("C") and len(cid) == 56
    step = [scval.to_uint32(1).to_xdr_bytes()]
    first = _u32(env.invoke(cid, "increment", step))
    second = _u32(env.invoke(cid, "increment", step))
    assert (first, second) == (1, 2)
    assert _u32(env.invoke(cid, "total", [])) == 2


def test_protocol_is_28_and_equals_the_compiled_in_ceiling() -> None:
    env = _env()
    assert env.protocol_version() == 28
    assert env.host_protocol_ceiling() == 28  # `==`: a p29 host would skew every tier-3 comparison (K2)


def test_the_extension_lives_in_the_repo_venv() -> None:
    """F.1.7: a stale system-wide install must not shadow the repo's build."""
    assert ".venv" in serpent_host.__file__, serpent_host.__file__


def test_a_missing_function_carries_an_underlying_diagnostic() -> None:
    """B5: the frame-level error is Context/InvalidAction for EVERY guest failure;
    the real classification is in the diagnostics."""
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    with pytest.raises(serpent_host.HostFailure):
        env.invoke(cid, "no_such_export", [])
    assert env.diagnostics(), "the host emitted no diagnostic events (diagnostic level not Debug?)"


def test_compare_orders_two_small_symbols_where_obj_cmp_refuses() -> None:
    """M2: the Compare trait answers for two small Vals; obj_cmp would trap."""
    env = _env()
    a = scval.to_symbol("A").to_xdr_bytes()
    u = scval.to_symbol("_").to_xdr_bytes()
    assert env.compare(a, a) == 0
    assert env.compare(a, u) in (-1, 1)
    assert env.compare(a, u) == -env.compare(u, a)


def test_a_contract_error_is_kind_contract_with_its_code() -> None:
    env = _env()
    # `examples/errors.py`'s Vault: `__init__(owner, limit)`, then `deposit(amount)`
    # raises `VaultError.LimitExceeded` (errorcode 3) past the limit.
    cid = env.register(
        build_file(ERRORS).wasm,
        [scval.to_address(ACCOUNT).to_xdr_bytes(), scval.to_uint32(10).to_xdr_bytes()],
    )
    with pytest.raises(serpent_host.HostFailure) as info:
        env.invoke(cid, "deposit", [scval.to_uint32(11).to_xdr_bytes()])
    kind, error_type, code, message = info.value.args
    assert (kind, error_type, code) == ("contract", "Contract", 3)
    assert message == "contract error code 3"


def test_a_missing_function_is_a_host_error_never_a_contract_error() -> None:
    """P4: `Context(InvalidAction)` has code 6; it must not read as contract code 6."""
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    with pytest.raises(serpent_host.HostFailure) as info:
        env.invoke(cid, "no_such_export", [])
    kind, error_type, _code, message = info.value.args
    assert kind == "host"
    assert error_type == "Context"
    assert "contract error" not in message


@pytest.mark.parametrize("name", ["has-dash", "two words", "a" * 33])
def test_an_invalid_function_name_is_invalid_input_and_the_env_survives(name: str) -> None:
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    with pytest.raises(serpent_host.HostFailure) as info:
        env.invoke(cid, name, [])
    assert info.value.args[0] == "invalid_input"
    assert _u32(env.invoke(cid, "increment", [scval.to_uint32(1).to_xdr_bytes()])) == 1


def test_a_bad_strkey_is_invalid_input_not_a_panic() -> None:
    env = _env()
    with pytest.raises(serpent_host.HostFailure) as info:
        env.invoke("NOTANADDRESS", "total", [])
    assert info.value.args[0] == "invalid_input"


def test_register_of_garbage_is_a_host_failure_not_a_panic() -> None:
    """P3's `Env::register` row: the sdk panics on a rejected module; E4's
    `catch_unwind` makes that a catchable `Exception` subclass."""
    env = _env()
    with pytest.raises(Exception) as info:  # noqa: B017 -- the CLASS is the assertion below
        env.register(b"\0asm\x01\0\0\0" + b"\xff" * 16, [])
    assert isinstance(info.value, serpent_host.HostFailure)
    assert info.value.args[0] in {"host", "panic"}
    assert type(info.value).__mro__[-2] is Exception  # not BaseException-only


def test_not_even_a_wasm_header_is_invalid_input() -> None:
    env = _env()
    with pytest.raises(serpent_host.HostFailure) as info:
        env.register(b"hello", [])
    assert info.value.args[0] == "invalid_input"


def test_no_snapshot_files_are_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P11: `capture_snapshot_at_drop: false`, proven by the absence of `test_snapshots/`."""
    monkeypatch.chdir(tmp_path)
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    env.invoke(cid, "increment", [scval.to_uint32(1).to_xdr_bytes()])
    del env
    assert not (tmp_path / "test_snapshots").exists()


def test_budget_and_resources_report_the_last_invocation() -> None:
    env = _env()
    cid = env.register(build_file(COUNTER).wasm, [])
    env.invoke(cid, "increment", [scval.to_uint32(1).to_xdr_bytes()])
    cpu, mem = env.budget()
    assert cpu > 0 and mem > 0
    r = env.resources()
    assert r is not None
    assert r["write_entries"] >= 1  # the counter writes its slot
    assert set(r) == {
        "instructions", "mem_bytes", "disk_read_entries", "memory_read_entries", "write_entries",
        "disk_read_bytes", "write_bytes", "contract_events_size_bytes",
        "persistent_rent_ledger_bytes", "persistent_entry_rent_bumps",
        "temporary_rent_ledger_bytes", "temporary_entry_rent_bumps",
    }


def test_resources_is_none_before_the_first_invocation() -> None:
    assert _env().resources() is None
```

- [ ] **Step 8: Run the smoke tests (they need Task 3's conftest to SKIP when absent; until then `importorskip` covers the same)**

Run: `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q tests/real_host/test_serpent_host_module.py`
Expected: all PASS. (The `real_host` marker is unregistered until Task 3; pytest warns but does not fail — acceptable for this task's gate, Task 3 registers it.)

- [ ] **Step 9: `host/README.md` (the build recipe, the traps, the threading rule)** — five short sections: what it is (D.1), building (`VIRTUAL_ENV=<root>/.venv uvx maturin develop --release --manifest-path host/Cargo.toml`), the three traps (P6 wrong interpreter, P6 `uv sync` prune, P8 cargo link), `unsendable` (P9: one env per thread; process-level parallelism only), version pins and what bumps them (E11).

- [ ] **Step 10: Gates, then commit**

Run the four Python gates. mypy: `host/serpent_host.pyi` (Step 4's note) is
on `mypy_path` from Task 3 onward; in THIS task `tests/real_host/
test_serpent_host_module.py` types `_env()` as `Any` via `importorskip`'s
return type, which is already `Any`, so no ignore is needed.

```bash
git add host/ .gitignore tests/real_host/__init__.py tests/real_host/test_serpent_host_module.py
git commit -m "feat(host): add the serpent-host PyO3 crate embedding the protocol-28 test host"
```

---

### Task 2: `serpent.testing._scval` — tier-1 chain values ↔ `SCVal`, decoding driven by `ty`

**Files:**
- Create: `src/serpent/testing/__init__.py` (minimal; Task 3 fills `__all__`),
  `src/serpent/testing/_scval.py`
- Create: `tests/unit/test_scval.py`
- Modify: `pyproject.toml` (the `testing` extra), `tests/unit/test_core_zero_dep.py`
  (`EXEMPT` becomes two directories)

**Interfaces:**
- Consumes: `serpent.types` (`U32, I32, U64, I64, U128, I128, Bool, Symbol,
  String, Bytes, Bytes32, Bytes64, bytes_n, Address, Timepoint, Duration, Vec,
  Map, ContractUnion, ContractEnum`), `serpent.decorators._METADATA_ATTR`
  (`"_serpent_type_"`; probe-verified shapes: union `{"kind": "union", "cases":
  [(name, payload_annotations_tuple), ...]}`, int enum `{"kind": "enum",
  "cases": [(name, discriminant), ...]}`, error enum `{"kind": "error_enum",
  "cases": [...]}`, struct `{"kind": "struct", "fields": [(name, annotation),
  ...]}` — structs use `fields`, not `cases`),
  `serpent.types._ordering.Struct` (the `__dataclass_fields__` protocol),
  `stellar_sdk.xdr.SCVal`, `stellar_sdk.scval`.
- Produces:

```python
class ScValError(ValueError): ...

def encode(value: object) -> SCVal:
    """ChainValue | Struct | None -> SCVal. Struct -> ScMap with Symbol keys in SORTED field
    order (C's P7 sort; matches sections.py's struct entry); ContractUnion -> ScVec
    [Symbol(case), *payload]; ContractEnum -> U32 discriminant; None -> Void."""

def decode(scval: SCVal, ty: object) -> object:
    """SCVal -> the value of type `ty` (a chain-type class, a @contracttype class, a
    ContractUnion/ContractEnum subclass, Vec[T]/Map[K, V] generic alias, X | None, or
    type(None)). Raises ScValError naming the ScVal kind and the requested ty on any
    mismatch. This is D11's re-typing applied at the XDR boundary."""

def decode_loose(scval: SCVal) -> object:
    """SCVal -> a chain value with NO type guidance: scalars by ScVal kind, ScVec -> Vec,
    ScMap -> Map, Void -> None. D6-coarse by design (a struct arrives as a Map, a union as
    a Vec, an int enum as a U32) -- for event topics/data and auth args, which carry no
    declared type. Documented as such."""

def to_xdr(value: object) -> bytes            # encode(value).to_xdr_bytes()
def from_xdr(xdr: bytes, ty: object) -> object  # decode(SCVal.from_xdr_bytes(xdr), ty)
```

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_scval.py`:

```python
"""`serpent.testing._scval`: ONE marshalling layer between tier-1 values and ScVal (ruling E2).

Two kinds of pin. GOLDENS against `stellar_sdk.scval`'s own constructors prove the
scalar encodings are the ecosystem's, not serpent's. ROUND-TRIPS over the generated
chain-value strategy prove `decode(encode(v), type(v)) == v` for every M1 shape,
including the three D6-coarse ones (struct vs Map, union vs Vec, enum vs U32) where
the requested `ty` is what disambiguates (D11).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from stellar_sdk import scval
from stellar_sdk.xdr import SCVal, SCValType

from serpent import (
    I32, I64, I128, U32, U64, U128, Address, Bool, Bytes, Bytes32, ContractEnum, ContractUnion,
    Duration, Map, String, Symbol, Timepoint, Vec, contractenum, contracttype, contractunion,
    enumvalue, variant,
)
from serpent.testing._scval import ScValError, decode, decode_loose, encode, from_xdr, to_xdr
from tests.unit.test_env_model import CHAIN_VALUES
from tests.unit.test_examples import OWNER

ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"


@contracttype
class Point:
    x: U32
    y: I64


@contractunion
class Shape(ContractUnion):
    Empty = variant()
    Circle = variant(U32)
    Rect = variant(U32, U32)


@contractenum
class Color(ContractEnum):
    Red = enumvalue(0)
    Green = enumvalue(1)


# --- goldens: the scalar encodings are stellar_sdk's --------------------------

GOLDENS = [
    (U32(7), scval.to_uint32(7)),
    (I32(-7), scval.to_int32(-7)),
    (U64(2**40), scval.to_uint64(2**40)),
    (I64(-(2**40)), scval.to_int64(-(2**40))),
    (U128(2**100), scval.to_uint128(2**100)),
    (I128(-(2**100)), scval.to_int128(-(2**100))),
    (Bool(True), scval.to_bool(True)),
    (Symbol("hello"), scval.to_symbol("hello")),
    (String("hi there"), scval.to_string("hi there")),
    (Bytes(b"\x01\x02"), scval.to_bytes(b"\x01\x02")),
    (Bytes32(bytes(32)), scval.to_bytes(bytes(32))),
    (Address(ACCOUNT), scval.to_address(ACCOUNT)),
    (Timepoint(1_700_000_000), scval.to_timepoint(1_700_000_000)),
    (Duration(60), scval.to_duration(60)),
    (None, scval.to_void()),
]


@pytest.mark.parametrize(("value", "expected"), GOLDENS, ids=[type(v).__name__ for v, _ in GOLDENS])
def test_scalar_encoding_matches_stellar_sdk(value: object, expected: SCVal) -> None:
    assert encode(value).to_xdr_bytes() == expected.to_xdr_bytes()


# --- the three D6-coarse shapes, disambiguated by ty (D11) ----------------------

def test_a_struct_is_a_map_with_sorted_symbol_keys() -> None:
    sc = encode(Point(x=U32(1), y=I64(-2)))
    assert sc.type == SCValType.SCV_MAP
    keys = [scval.from_symbol(e.key) for e in sc.map.sc_map]
    assert keys == sorted(keys) == ["x", "y"]
    assert decode(sc, Point) == Point(x=U32(1), y=I64(-2))
    loose = decode_loose(sc)
    assert isinstance(loose, Map)  # D6-coarse without a ty


def test_a_union_is_a_vec_led_by_the_case_symbol() -> None:
    sc = encode(Shape.Rect(U32(2), U32(3)))
    assert sc.type == SCValType.SCV_VEC
    assert scval.from_symbol(sc.vec.sc_vec[0]) == "Rect"
    assert decode(sc, Shape) == Shape.Rect(U32(2), U32(3))
    assert decode(encode(Shape.Empty), Shape) == Shape.Empty
    assert isinstance(decode_loose(sc), Vec)


def test_an_int_enum_is_a_bare_u32() -> None:
    sc = encode(Color.Green)
    assert sc.to_xdr_bytes() == scval.to_uint32(1).to_xdr_bytes()
    assert decode(sc, Color) == Color.Green
    assert decode(sc, U32) == U32(1)  # the same word, re-typed (D11)


def test_option_decodes_void_to_none_and_a_value_to_the_inner_type() -> None:
    assert decode(scval.to_void(), U32 | None) is None
    assert decode(scval.to_uint32(4), U32 | None) == U32(4)


def test_containers_decode_element_types() -> None:
    v = Vec(U32, [U32(1), U32(2)])
    assert decode(encode(v), Vec[U32]) == v
    m = Map(Symbol, U32)
    m.set(Symbol("a"), U32(1))
    assert decode(encode(m), Map[Symbol, U32]) == m


# --- mismatches are loud ----------------------------------------------------------

@pytest.mark.parametrize(
    ("sc", "ty"),
    [
        (scval.to_uint32(1), Symbol),
        (scval.to_symbol("x"), U32),
        (scval.to_vec([scval.to_symbol("Nope")]), Shape),
        (scval.to_uint32(9), Color),  # no case with discriminant 9
        (scval.to_uint64(1), U32),  # a U64 is not a U32 even when it fits
    ],
)
def test_decode_refuses_a_kind_or_case_the_ty_does_not_name(sc: SCVal, ty: object) -> None:
    with pytest.raises(ScValError):
        decode(sc, ty)


def test_encode_refuses_a_raw_python_scalar() -> None:
    with pytest.raises(ScValError):
        encode(3)  # type: ignore[arg-type]


# --- round-trips ------------------------------------------------------------------

@given(CHAIN_VALUES)
@settings(max_examples=200)
def test_every_generated_chain_value_round_trips_through_its_own_type(value: object) -> None:
    ty = _ty_of(value)
    assert decode(encode(value), ty) == value
    assert from_xdr(to_xdr(value), ty) == value


def _ty_of(value: object) -> object:
    """The `ty` a caller would pass for `value` -- element types read off containers.

    `element_type`/`key_type`/`value_type` are PROPERTIES (review M5). `CHAIN_VALUES`
    generates nested containers whose element type is the BARE class (`Vec[Vec]`,
    `Vec[Map]` -- `Vec(Vec[U32], ...)` is itself a TypeError), so the alias may
    carry a bare `Vec`/`Map`; `decode` accepts that and decodes such elements
    loosely, which is exact because container equality is by content.
    """
    if isinstance(value, Vec):
        return Vec[value.element_type]  # type: ignore[misc]
    if isinstance(value, Map):
        return Map[value.key_type, value.value_type]  # type: ignore[misc]
    return type(value)


def test_owner_strkey_round_trips() -> None:
    assert decode(encode(Address(OWNER)), Address) == Address(OWNER)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -q tests/unit/test_scval.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'serpent.testing'`.

- [ ] **Step 3: Implement `_scval.py`**

```python
"""ONE marshalling layer between tier-1 chain values and ScVal (ruling E2, dossier §D.2).

Decoding is DRIVEN BY THE REQUESTED `ty` -- the same rule tier-1 `get(key, ty)`
follows (D11's re-typing): the host hands back a bare word or a bare Vec/Map, and
the requested type says whether that word is a `U32` or a `Color`, that Vec a
`Vec[U32]` or a `Shape`, that Map a `Map[Symbol, U32]` or a `Point`. Without a
`ty` (`decode_loose`) the three pairs are D6-coarse by construction, and that
is documented rather than guessed.

Struct field order is SORTED field names (M1-C's P7 sort, which `sections.py`'s
struct entry and the emitter's `MakeStruct` both use), so the ScMap this
produces is byte-identical to what the compiled contract builds.

`stellar_sdk` is imported at module import: this module lives under
`serpent.testing`, the second recorded exemption from the zero-dep walk
(`tests/unit/test_core_zero_dep.py`); `import serpent` never imports it.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any

from stellar_sdk import scval
from stellar_sdk.xdr import SCVal, SCValType

from serpent.decorators import _METADATA_ATTR
from serpent.types import (
    I32, I64, I128, U32, U64, U128, Address, Bool, Bytes, ContractEnum, ContractUnion, Duration,
    Map, String, Symbol, Timepoint, Vec,
)

__all__ = ["ScValError", "decode", "decode_loose", "encode", "from_xdr", "to_xdr"]


class ScValError(ValueError):
    """An ScVal and a requested type disagree, or a value has no ScVal form."""


# --- encode ---------------------------------------------------------------------

def encode(value: object) -> SCVal:
    if value is None:
        return scval.to_void()
    if isinstance(value, Bool):
        return scval.to_bool(value.value)
    if isinstance(value, U32):
        return scval.to_uint32(value.value)
    if isinstance(value, I32):
        return scval.to_int32(value.value)
    if isinstance(value, Timepoint):
        return scval.to_timepoint(value.value)
    if isinstance(value, Duration):
        return scval.to_duration(value.value)
    if isinstance(value, U64):
        return scval.to_uint64(value.value)
    if isinstance(value, I64):
        return scval.to_int64(value.value)
    if isinstance(value, U128):
        return scval.to_uint128(value.value)
    if isinstance(value, I128):
        return scval.to_int128(value.value)
    if isinstance(value, Symbol):
        return scval.to_symbol(value.text)
    if isinstance(value, String):
        return scval.to_string(value.text)
    if isinstance(value, Bytes):  # Bytes32/Bytes64/bytes_n(N) subclass Bytes: one ScVal kind
        return scval.to_bytes(bytes(value))
    if isinstance(value, Address):
        return scval.to_address(value.strkey)
    if isinstance(value, ContractEnum):
        return scval.to_uint32(_enum_discriminant(value))
    if isinstance(value, ContractUnion):
        return scval.to_vec([encode(item) for item in _union_items(value)])
    if isinstance(value, Vec):
        return scval.to_vec([encode(item) for item in value])
    if isinstance(value, Map):
        return scval.to_map({encode(k): encode(value.get(k)) for k in value.keys()})
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = sorted(f.name for f in dataclasses.fields(value))
        return scval.to_map({scval.to_symbol(name): encode(getattr(value, name)) for name in fields})
    raise ScValError(f"{value!r} ({type(value).__name__}) has no ScVal form")
```

(The implementer completes `decode(scval, ty)` as the mirror: dispatch on
`ty` FIRST — `typing.get_origin(ty)` for `Vec`/`Map`/`X | None`
(`types.UnionType`/`typing.Union`), `issubclass(ty, ContractUnion)` → check
`SCV_VEC`, read `sc_vec[0]` as the case Symbol, look the case up in
`vars(ty)[_METADATA_ATTR]["cases"]` (a list of `(name, payload_annotations)`),
decode each payload slot by its annotation, construct via `getattr(ty, name)(*payload)`
(unit case: `getattr(ty, name)` itself); `issubclass(ty, ContractEnum)` →
check `SCV_U32`, find the case whose discriminant matches (`cases` is
`(name, discriminant)` pairs — verify the exact metadata shape from
`decorators.py:372` at implementation time), return `getattr(ty, name)`;
dataclass `ty` → check `SCV_MAP`, keys are Symbols, decode each field by
`typing.get_type_hints(ty)[name]`, construct with kwargs; scalar classes →
check the exact `SCValType` (`U32`↔`SCV_U32`, `U64`↔`SCV_U64`, `Timepoint`↔
`SCV_TIMEPOINT`, `Bytes` family↔`SCV_BYTES` with the fixed length enforced by
the class constructor, `Address`↔`SCV_ADDRESS`, …) and construct. Every
mismatch raises `ScValError(f"expected {ty!r}, got ScVal {sc.type.name}")`.
`decode_loose` maps by `sc.type` alone: `SCV_VEC` → `Vec(<elem type of first
decoded item or U32 when empty>, items)`, `SCV_MAP` → `Map(...)`. A BARE
`Vec`/`Map` as `ty` (or as a generic alias's element) means "elements by
`decode_loose`" (review M5) — state in the docstring that this is exact
because `Vec(U32, []) == Vec(Bool, [])` and content equality keeps scalar
kinds distinct. The dataclass branch must recurse into a generic-alias field
annotation (`CHAIN_VALUES`' `Holder` has `items: Vec[U32]`). Access the
union's internal items through the public readers `tag()`/`payload()`? No —
`payload(i, ty)` needs a type; use `value._payload_items()` (a private but
stable accessor on `ContractUnion`, `_udt.py:265`) and `value.tag().text`;
`_enum_discriminant` reads the case's discriminant from the metadata by
matching `value == getattr(type(value), name)`. Name the two private
accesses in a module comment as the licensed seam.)

- [ ] **Step 4: pyproject + zero-dep exemption**

`pyproject.toml` — add under `[project.optional-dependencies]`:

```toml
# `serpent[testing]`: the tier-2b/3 test surface (`serpent.testing`). It needs
# stellar-sdk for ScVal marshalling and RPC (ruling E2/E14) and pytest for the
# `real_host` marker. The Rust extension `serpent-host` is NOT a dependency in
# M1 -- it is built from source into the repo venv (ruling U1/E5; host/README.md)
# and joins here as a wheel dependency in M3.
testing = ["stellar-sdk>=15,<16", "pytest>=8"]
```

`tests/unit/test_core_zero_dep.py` — the exemption becomes two directories:

```python
#: The recorded exceptions; everything else under SRC is walked. `spec/`
#: (stellar_sdk for XDR sections, build-time only) and `testing/` (stellar_sdk
#: for ScVal marshalling + RPC, test-time only, ruling E2). Neither is
#: re-exported by the root package -- the second half of this file.
EXEMPT = (SRC / "spec", SRC / "testing")


def _core_modules() -> list[pathlib.Path]:
    return sorted(p for p in SRC.rglob("*.py") if not any(e in p.parents for e in EXEMPT))
```

and in `test_the_walk_actually_covers_the_core_modules`:

```python
    assert not any(name.startswith(("spec/", "testing/")) for name in walked)
    for exempt, probe in ((SRC / "spec", "typemap.py"), (SRC / "testing", "_scval.py")):
        assert (exempt / probe).is_file()
```

The FOURTH site (review B7 — `test_spec_subpackage_does_import_stellar_sdk`
iterates `EXEMPT.rglob`, which a tuple lacks): one assertion PER exempt
directory, so a `testing/` that stopped needing `stellar_sdk` is caught:

```python
@pytest.mark.parametrize("exempt", EXEMPT, ids=lambda d: d.name)
def test_each_exempt_subpackage_does_import_stellar_sdk(exempt: pathlib.Path) -> None:
    roots: set[str] = set()
    for path in sorted(exempt.rglob("*.py")):
        roots |= _imported_roots(path)
    assert "stellar_sdk" in roots
```

Then mirror `test_serpent_spec_is_not_reachable_from_the_package_root`'s
five source-string checks for `testing` (`from serpent.testing`, `import
serpent.testing`, `from .testing`, `from . import testing`, `from serpent
import testing`) and extend the subprocess probe to assert
`'serpent.testing' not in sys.modules`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q tests/unit/test_scval.py tests/unit/test_core_zero_dep.py`
Expected: PASS.

- [ ] **Step 6: Gates, then commit**

```bash
git add src/serpent/testing/__init__.py src/serpent/testing/_scval.py tests/unit/test_scval.py pyproject.toml tests/unit/test_core_zero_dep.py
git commit -m "feat(testing): add the ScVal marshalling layer driven by the requested type"
```

---

### Task 3: `serpent.testing` — `RealEnv`, the exception hierarchy, the marker, the drift pins

**Files:**
- Create: `src/serpent/testing/_errors.py`, `src/serpent/testing/_real.py`,
  `src/serpent/testing/_marker.py`, `tests/conftest.py`,
  `tests/real_host/test_real_env.py`
- Modify: `src/serpent/testing/__init__.py` (the `__all__`), `pyproject.toml`
  (`[tool.pytest.ini_options] markers`)

**Interfaces:**
- Consumes: Task 1's `serpent_host.RealEnv`/`HostFailure` (contract above);
  Task 2's `encode`/`decode`/`decode_loose`/`to_xdr`/`from_xdr`;
  `serpent.env.DEFAULT_LEDGER_TIMESTAMP/DEFAULT_LEDGER_SEQUENCE`,
  `serpent.env.PublishedEvent`/`RecordedAuth` aliases;
  `serpent._host._codegen.PINNED_TAG`; `serpent.emitter.build_file`;
  `serpent.decorators._METADATA_ATTR`.
- Produces (everything later tasks import from `serpent.testing`):

```python
# _errors.py
class RealHostError(Exception):
    """Any failure the real host reported. TWO LEVELS (review B5):
    `.error_type`/`.code` are the FRAME-level classification -- `("Context", 6)`
    for every guest-side failure except a contract's own fail_with_error, so they
    answer only "contract or not" (P4); `.underlying` is the innermost
    `Error(Type(Code))` DIAGNOSTIC as `(type_name, code_name_or_int)`, e.g.
    `("Object", "ArithDomain")`, `("Auth", "InvalidAction")`, `("Storage",
    "ExceededLimit")` -- the real classification every host-fact assertion uses.
    None when the host emitted no error diagnostic."""
    error_type: str
    code: int
    underlying: tuple[str, str] | None
class RealContractError(RealHostError):
    """error_type == "Contract". `.member` is the deployed class's @contracterror
    member (an exception CLASS) when the class declares that code, else None."""
    member: type[BaseException] | None
class HostPanic(RealHostError):        # kind == "panic" (E4's contained panics)
class RealHostUnavailable(RuntimeError) # raised by RealEnv() when serpent_host is unimportable
class FrozenTableDisagreement(AssertionError):
    """A real-leg answer disagrees with tier 1 on a frozen-table row (E10).
    Message names the row, both answers, and 'controller decision required'."""

# _marker.py
REAL_HOST_MARKER = "real_host"
REBUILD_COMMAND = "VIRTUAL_ENV=<repo-root>/.venv uvx maturin develop --release --manifest-path host/Cargo.toml"
REQUIRE_ENV_VAR = "SERPENT_REQUIRE_REAL_HOST"
def is_available() -> bool                    # importlib.util.find_spec("serpent_host") is not None
def unavailable_reason() -> str               # names REBUILD_COMMAND and host/README.md

# _real.py
DEFAULT_MIN_TEMP_ENTRY_TTL = 16
DEFAULT_MIN_PERSISTENT_ENTRY_TTL = 4096
DEFAULT_MAX_ENTRY_TTL = 6_312_000
DEFAULT_BASE_RESERVE = 5_000_000
DEFAULT_NETWORK_ID = bytes(32)
DEFAULT_PROTOCOL = 28    # == int(PINNED_TAG.removeprefix("v").split(".")[0]); pinned by test
Durability = Literal["persistent", "temporary", "instance"]

class RealEnv:
    def __init__(self, *, timestamp: int = DEFAULT_LEDGER_TIMESTAMP,
                 sequence: int = DEFAULT_LEDGER_SEQUENCE,
                 auths: Iterable[Address] | None = None) -> None
        # auths=None -> mock_all_auths (tier-1 parity); a tuple -> the allow-set. Every
        # allowed address MUST be a CONTRACT strkey (B2 ruling: the test host mocks auth by
        # registering a MockAuthContract at the authorizer; account authorizers need real
        # signatures, M2) -- an account strkey raises ValueError at construction with that
        # sentence. RealEnv OWNS the complete mock-entry list and RE-SETS it before every
        # invoke (`mock_auths` REPLACES, review M6): per-call entries (address, contract,
        # method, args) for every allowed address, unioned with the pending custom entries
        # `RealContract.add_mock_auths` registered.
    def protocol_version(self) -> int
    def compare(self, a: object, b: object) -> int    # the host's Compare<Val> verdict (M2)
    def max_ttl(self) -> int
    def advance(self, n: int) -> None                 # sequence += n (same verb as tier 1)
    def set_ledger(self, *, sequence: int | None = None, timestamp: int | None = None) -> None
    def deploy_source(self, path: Path, *args: object) -> RealContract
        # THE primary form (review B3: path-loaded example modules are not in sys.modules, so
        # inspect.getsourcefile(cls) raises): build_file(path), discover the one @contract
        # class in the module loaded from path (test_env_differential._contract_class's rule),
        # register with encode(arg) per constructor arg, hand RealContract the MODULE object
    def deploy(self, cls: type, *args: object) -> RealContract
        # convenience: resolves the path from sys.modules[cls.__module__].__file__ and falls
        # back to inspect.getsourcefile; raises ValueError naming deploy_source when neither works
    def deploy_wasm(self, wasm: bytes, *args: object) -> RealContract   # no class: decode falls back to decode_loose
    def register_raw(self, wasm: bytes, constructor_args: Sequence[object]) -> str
    def invoke_raw(self, address: str, method: str, args: Sequence[object]) -> bytes
        # encode args, call, return the result's ScVal XDR UNDECODED; errors re-raised typed.
        # For a test that owns the decode (Task 4) -- never reach into `_raw`.

class RealContract:
    address: Address
    cls: type | None
    def invoke(self, method: str, *args: object) -> object
        # encode args; call; decode the result with the method's declared return annotation
        # (typing.get_type_hints(getattr(cls, method))["return"]; None -> expect Void) or
        # decode_loose when cls is None. Re-raises HostFailure as RealContractError /
        # RealHostError / HostPanic; RealContractError.member is looked up in cls's
        # @contracterror classes by code.
    def events(self) -> tuple[PublishedEvent, ...]    # last invocation; topics/data via decode_loose
    def auths(self) -> tuple[RecordedAuth, ...]       # last invocation; (Address, Vec[Any]) -- ALWAYS a Vec:
                                                      # the host records the invocation args for require_auth.
                                                      # Non-contract auths (register's CreateContractV2HostFn)
                                                      # never appear; accumulation starts AFTER deploy (M8).
    def storage(self, durability: Durability) -> RealStorage
    def budget(self) -> tuple[int, int]
    def resources(self) -> dict[str, int] | None      # None before the first invoke (m14)
    def diagnostics(self) -> tuple[xdr.DiagnosticEvent, ...]   # last invocation, decoded XDR
    # Added by Task 5 (declared here so the surface is in one place):
    def add_mock_auths(self, entries: Sequence[tuple[Address, Vec[Any]]]) -> None
        # REGISTERS pending (authorizer, args) entries the façade includes in every later
        # mock_auths set for this contract (M6); never a direct host call
    def events_for_sequence(self) -> tuple[PublishedEvent, ...]   # accumulated since deploy
    def auths_for_sequence(self) -> tuple[RecordedAuth, ...]      # accumulated since deploy

class RealStorage:
    def get(self, key: object, ty: object) -> object   # None when absent
    def has(self, key: object) -> bool
    def set(self, key: object, value: object) -> None  # seeding (tier 3, Task 9)
    def ttl(self, key: object) -> int | None           # RELATIVE: ledgers remaining excl. the current
                                                       # one (the host's own quantity, B10); None absent/expired
    def live_until(self, key: object) -> int | None    # env.sequence + ttl -- the quantity tier-1
                                                       # _TtlState speaks; the differential compares THIS
```

- [ ] **Step 1: Register the marker and write the root conftest**

`pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "real_host: runs against the embedded soroban-env-host (serpent_host); skipped loudly when the extension is not built, failed when SERPENT_REQUIRE_REAL_HOST=1 (ruling U2)",
]
```

`tests/conftest.py`:

```python
"""Repo-wide pytest policy: the `real_host` marker (ruling U2, dossier D.2).

Tests marked `real_host` need the `serpent_host` extension, which a Rust-less
checkout does not have. They SKIP -- loudly, counted in the summary, with the
rebuild command in the reason -- unless `SERPENT_REQUIRE_REAL_HOST=1`, in which
case a missing extension is a FAILURE (CI's Rust job sets it, so the real-host
suite can never pass vacuously: dossier F.1.6, the D4 skip-never-silently-passed
convention).
"""

from __future__ import annotations

import os

import pytest

from serpent.testing._marker import REAL_HOST_MARKER, REQUIRE_ENV_VAR, is_available, unavailable_reason


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if is_available():
        return
    marked = [item for item in items if item.get_closest_marker(REAL_HOST_MARKER) is not None]
    if not marked:
        return
    if os.environ.get(REQUIRE_ENV_VAR) == "1":
        # Probed (review B4): xfail(run=False, strict=True) reports XFAIL [NOTRUN] with
        # exit code 0 -- NOT a failure. So the required-mode outcome is produced by
        # `pytest_runtest_setup` below, which fails each marked item for real.
        config.stash[_REQUIRED_BUT_ABSENT] = True
        return
    skip = pytest.mark.skip(reason=unavailable_reason())
    for item in marked:
        item.add_marker(skip)


_REQUIRED_BUT_ABSENT = pytest.StashKey[bool]()


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.config.stash.get(_REQUIRED_BUT_ABSENT, False) and item.get_closest_marker(REAL_HOST_MARKER):
        pytest.fail(
            f"{REQUIRE_ENV_VAR}=1 but serpent_host is not importable. {unavailable_reason()}",
            pytrace=False,
        )
```

- [ ] **Step 2: Write the failing tests**

`tests/real_host/test_real_env.py` (marked `real_host`, plus TWO unmarked tests
that run everywhere):

```python
"""`serpent.testing.RealEnv`: the façade, the hierarchy, the drift pins, the skip policy."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from serpent import U32, Address, Symbol
from serpent._host._codegen import PINNED_TAG
from serpent.testing import (
    DEFAULT_PROTOCOL, RealContractError, RealEnv, RealHostError, RealHostUnavailable,
)
from serpent.testing._marker import REBUILD_COMMAND, REQUIRE_ENV_VAR, is_available
from tests.unit.test_examples import load_example
from tests.unit.test_emitter_end_to_end import EXAMPLE_COUNTER, EXAMPLE_ERRORS

ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"

# --- run everywhere: the pins that do not need the extension ------------------

def test_the_default_protocol_is_the_env_json_pins_major() -> None:
    """E11: the embedded host and the emitter's bindings are the same release line."""
    assert DEFAULT_PROTOCOL == int(PINNED_TAG.removeprefix("v").split(".")[0]) == 28


def test_a_rust_less_checkout_skips_loudly_and_a_required_run_fails(tmp_path: Path) -> None:
    """U2 both ways, in a subprocess that HIDES serpent_host via a sitecustomize shim."""
    shim = tmp_path / "sitecustomize.py"
    shim.write_text("import sys; sys.modules['serpent_host'] = None\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    env.pop(REQUIRE_ENV_VAR, None)
    probe = ["-q", "-p", "no:cacheprovider", "tests/real_host/test_real_env.py", "-k", "counter"]
    skipped = subprocess.run(
        [sys.executable, "-m", "pytest", *probe, "-rs"], capture_output=True, text=True, env=env
    )
    assert skipped.returncode == 0, skipped.stdout
    assert "skipped" in skipped.stdout, skipped.stdout
    assert "maturin develop" in skipped.stdout, "the skip reason must carry the rebuild command"
    required = subprocess.run(
        [sys.executable, "-m", "pytest", *probe, "-rf"],
        capture_output=True, text=True, env={**env, REQUIRE_ENV_VAR: "1"},
    )
    assert required.returncode != 0, required.stdout
    assert "failed" in required.stdout, "the required mode must produce real FAILED lines, not xfails"


# --- the real-host half -----------------------------------------------------------

pytestmark_real = pytest.mark.real_host


@pytestmark_real
def test_counter_deploy_invoke_and_storage_read_back() -> None:
    counter = load_example(EXAMPLE_COUNTER)
    env = RealEnv()
    c = env.deploy(counter.Counter)
    assert c.invoke("increment", U32(2)) == U32(2)
    assert c.invoke("increment", U32(3)) == U32(5)
    assert c.invoke("total") == U32(5)
    # counter.py keys `TOTAL` in PERSISTENT storage (examples/counter.py:56-66); the
    # other bucket is asserted absent so the durability ROUTING is tested, not just the key.
    assert c.storage("persistent").get(Symbol("TOTAL"), U32) == U32(5)
    assert c.storage("persistent").has(Symbol("TOTAL"))
    assert c.storage("instance").get(Symbol("TOTAL"), U32) is None
    assert c.storage("persistent").ttl(Symbol("TOTAL")) is not None
    assert c.storage("temporary").ttl(Symbol("TOTAL")) is None


@pytestmark_real
def test_a_contract_error_maps_to_the_declared_member() -> None:
    errors = load_example(EXAMPLE_ERRORS)
    env = RealEnv()
    vault = env.deploy(errors.Vault, Address(ACCOUNT), U32(10))
    with pytest.raises(RealContractError) as info:
        vault.invoke("deposit", U32(11))
    assert info.value.code == 3
    assert info.value.error_type == "Contract"
    assert info.value.member is errors.VaultError.LimitExceeded


@pytestmark_real
def test_a_host_error_is_not_a_contract_error() -> None:
    counter = load_example(EXAMPLE_COUNTER)
    c = RealEnv().deploy(counter.Counter)
    with pytest.raises(RealHostError) as info:
        c.invoke("no_such_method")
    assert not isinstance(info.value, RealContractError)
    assert info.value.error_type == "Context"


@pytestmark_real
def test_advance_moves_the_sequence_the_contract_reads() -> None:
    from tests.semantics.env_scenarios import ENV_SURFACE
    surface = load_example(ENV_SURFACE)
    env = RealEnv(sequence=1_000_000)
    c = env.deploy_source(ENV_SURFACE)
    assert c.invoke("ledger_seq") == U32(1_000_000)
    env.advance(5)
    assert c.invoke("ledger_seq") == U32(1_000_005)


SHAPES_ID = "CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW"  # a real CONTRACT strkey


@pytestmark_real
def test_the_allow_set_refuses_an_address_not_in_it() -> None:
    """B2: authorizers are CONTRACT strkeys on the test host; the refusal is a
    frame-level Context error whose UNDERLYING diagnostic is Auth (B5)."""
    from tests.semantics.env_scenarios import ENV_SURFACE
    allowed = Address(SHAPES_ID)
    other = Address("CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI")
    env = RealEnv(auths=(allowed,))
    c = env.deploy_source(ENV_SURFACE)
    c.invoke("guard", allowed)                        # allowed: recorded
    assert c.auths()[0][0] == allowed
    with pytest.raises(RealHostError) as info:
        c.invoke("guard", other)                      # refused
    assert not isinstance(info.value, RealContractError)
    assert info.value.underlying is not None and info.value.underlying[0] == "Auth"
    assert c.auths() == ()


def test_an_account_authorizer_is_refused_at_construction() -> None:
    """B2, the honest fence: account auth needs real signatures (M2)."""
    with pytest.raises(ValueError, match="contract"):
        RealEnv(auths=(Address(ACCOUNT),))


def test_realenv_without_the_extension_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "serpent_host", None)
    with pytest.raises(RealHostUnavailable) as info:
        RealEnv()
    assert "maturin" in str(info.value)


def _contract_class(module: object) -> type:
    from serpent.decorators import _METADATA_ATTR
    found = [m for m in vars(module).values()
             if isinstance(m, type) and vars(m).get(_METADATA_ATTR, {}).get("kind") == "contract"]
    (cls,) = found
    return cls
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run --no-sync pytest -q tests/real_host/test_real_env.py`
Expected: FAIL with `ImportError: cannot import name 'RealEnv' from 'serpent.testing'`.

- [ ] **Step 4: Implement `_errors.py`, `_marker.py`, `_real.py`, `__init__.py`**

`_errors.py` — the five classes above; `RealHostError.__init__(self,
error_type: str, code: int, message: str, underlying: tuple[str, str] | None)`
stores all and passes `message` to `Exception`; `RealContractError` adds
`member`. A module-level helper `raise_from_failure(exc: BaseException,
module: ModuleType | None, diagnostics: Sequence[xdr.DiagnosticEvent]) ->
NoReturn` maps a `serpent_host.HostFailure` by `args[0]`: `"contract"` →
`RealContractError` with `member = _member_for(module, code)` (scan the
MODULE OBJECT the façade was handed — B3: it is not in `sys.modules` — for
classes whose `_serpent_type_["kind"] == "error_enum"` and whose `cases`
contain the code; the member is an exception class per M1-A's ruling);
`underlying` comes from `_innermost_error(diagnostics)`: the LAST diagnostic
whose topics are `[Symbol("error"), Error(...)]`, rendered as `(ScErrorType
name, ScErrorCode name)` via `stellar_sdk.xdr.SCError`;
`"host"` → `RealHostError`; `"panic"` → `HostPanic`; `"invalid_input"` /
`"conversion"` → `ValueError` (caller bugs, not host answers).

`_real.py` — the façade above. Key rules the implementer must encode
(each is a test in Step 2 or a later task):

```python
def _require_host() -> Any:
    try:
        import serpent_host
    except ImportError as exc:  # also covers sys.modules[...] = None
        raise RealHostUnavailable(unavailable_reason()) from exc
    return serpent_host


class RealEnv:
    def __init__(self, *, timestamp=DEFAULT_LEDGER_TIMESTAMP, sequence=DEFAULT_LEDGER_SEQUENCE, auths=None):
        host = _require_host()
        self._raw = host.RealEnv(
            protocol_version=DEFAULT_PROTOCOL, sequence_number=sequence, timestamp=timestamp,
            network_id=DEFAULT_NETWORK_ID, base_reserve=DEFAULT_BASE_RESERVE,
            min_temp_entry_ttl=DEFAULT_MIN_TEMP_ENTRY_TTL,
            min_persistent_entry_ttl=DEFAULT_MIN_PERSISTENT_ENTRY_TTL,
            max_entry_ttl=DEFAULT_MAX_ENTRY_TTL,
        )
        self._sequence = sequence
        self._allow: tuple[Address, ...] | None = None if auths is None else tuple(auths)
        if self._allow is None:
            self._raw.mock_all_auths()

    def advance(self, n: int) -> None:
        self._sequence += n
        self._raw.set_ledger(sequence_number=self._sequence)

    def deploy_source(self, path: Path, *args: object) -> RealContract:
        module = _load_by_path(path)             # spec_from_file_location, like test_examples.load_example
        cls = _the_contract_class(module)        # exactly one @contract class (frontend-enforced)
        wasm = build_file(path).wasm
        address = self._raw.register(wasm, [to_xdr(a) for a in args])
        return RealContract(self, Address(address), cls, module)

    def deploy(self, cls: type, *args: object) -> RealContract:
        module = sys.modules.get(cls.__module__)
        path = getattr(module, "__file__", None)
        if path is None:
            try:
                path = inspect.getsourcefile(cls)
            except TypeError:
                path = None
        if path is None:
            raise ValueError(f"{cls!r} was loaded by path; use RealEnv.deploy_source(path, ...)")
        return self.deploy_source(Path(path), *args)
```

(`RealEnv.__init__` validates every allow-set address with
`stellar_sdk.strkey.StrKey.is_valid_contract` and raises `ValueError` naming
the B2 rule otherwise. The façade keeps `self._pending: dict[str,
list[...]]` of custom entries per contract and, before EVERY invoke on a
contract, calls `raw.mock_auths(per_call_entries + pending)` — one complete
set, because the sdk REPLACES.)

`RealContract.invoke`: when `env._allow` is not None, first re-set the
COMPLETE mock set (per-call entries `(who.strkey, self.address.strkey,
method, [to_xdr(a) ...])` for every allowed `who`, plus this contract's
pending custom entries); then `raw.invoke(...)`, catching
`serpent_host.HostFailure` → `raise_from_failure(exc, self._module,
self.env.diagnostics())`. Decode: `ty =
typing.get_type_hints(getattr(cls, method)).get("return", type(None))`;
`type(None)` → assert the ScVal is Void and return `None`; else
`from_xdr(result, ty)`.

`events()`: `[xdr.ContractEvent.from_xdr_bytes(b) for b in raw.events()]`;
for each, `body.v0.topics` → `tuple(decode_loose(t) ...)`, `body.v0.data` →
`decode_loose(...)`; return the `PublishedEvent` tuples in order.
`auths()`: `[(Address(who), Vec(<elem type via decode_loose of items>, items))
for who, _contract, _fn, args in raw.auths()]` — a `Vec` ALWAYS (the host
records the invocation args); Task 5's runner reconciles with tier 1's
`None`. `events()`: decode `xdr.ContractEvent`; note (review m7) the sdk's
`Events::all()` already filters `failed_call` events — the rollback
evidence is the host's `failed_call` flag one layer down, and the
`host_diverges` reason text says so.

`__init__.py`:

```python
"""serpent.testing: the tier-2b (real host) and tier-3 (testnet fixture) test surface.

Import this, not `serpent_host`, in a contract's tests. `RealEnv` mirrors the
tier-1 `Env`/`deploy` verbs wherever the semantics coincide, so a tier-1 test
re-points at the real host by swapping one fixture (dossier §D.2).
"""

from serpent.testing._errors import (
    FrozenTableDisagreement, HostPanic, RealContractError, RealHostError, RealHostUnavailable,
)
from serpent.testing._marker import REAL_HOST_MARKER, REBUILD_COMMAND, is_available
from serpent.testing._real import (
    DEFAULT_PROTOCOL, RealContract, RealEnv, RealStorage,
)

__all__ = [
    "DEFAULT_PROTOCOL", "REAL_HOST_MARKER", "REBUILD_COMMAND",
    "FrozenTableDisagreement", "HostPanic", "RealContract", "RealContractError",
    "RealEnv", "RealHostError", "RealHostUnavailable", "RealStorage", "is_available",
]
```

(`serpent.__all__` is NOT touched: `serpent.testing` is a subpackage import
like `serpent.spec`; `test_public_api.py` stays as is. `pyproject.toml`
gains `mypy_path = ["host"]` under `[tool.mypy]` so `host/serpent_host.pyi`
types the extension in both environments, m8.)

- [ ] **Step 5: Run the tests**

Run: `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q tests/real_host/test_real_env.py tests/real_host/test_serpent_host_module.py`
Expected: PASS. Then `uv run pytest -q` (whole suite, extension present): PASS with the two prior TTL skips only.

- [ ] **Step 6: Gates (the four + Rust untouched), then commit**

```bash
git add src/serpent/testing tests/conftest.py tests/real_host/test_real_env.py pyproject.toml
git commit -m "feat(testing): add RealEnv, the real-host error hierarchy, and the real_host marker"
```

---

### Task 4: The semantics table's real leg (O9, O12; ruling E10)

**Files:**
- Create: `tests/real_host/test_semantics_real.py`

**Interfaces:**
- Consumes: `tests.unit.test_emitter_semantics` — `IN_SCOPE`, `IN_SCOPE_COUNT`,
  `observed_ty(case) -> Ty`, `annotation_of(ty) -> str`, and the module's
  wrapped-module builder (the function that turns `(case.source, annotation)`
  into a compilable contract source; the implementer reads
  `test_emitter_semantics.py:249-385` for its exact name — `wrap_returning`
  or the helper `start_case` calls — and IMPORTS it, never re-derives it,
  BL-3); `tests.semantics.test_semantics._eval_case`;
  `serpent.compiler.frontend.compile_module`, `serpent.emitter.build_wasm`;
  `serpent.testing.RealEnv.deploy_wasm`, `RealContract.invoke` (decode via
  `cls=None` is loose — so THIS test decodes with `from_xdr(result, ty)`
  using `RealEnv.register_raw` + the raw invoke; see Step 3);
  `serpent.testing.FrozenTableDisagreement`, `RealContractError`, `RealHostError`.
- Produces: nothing importable; the evidence.

- [ ] **Step 1: Write the failing test module**

```python
"""Tier 2b: the frozen semantics table, run against the REAL host (dossier O9, ruling E10).

`tests/unit/test_emitter_semantics.py` runs the 35 in-scope cases under the mini
host; this module runs the SAME wrapped modules on the embedded soroban-env-host
and compares to tier 1. Where the mini host is "a pin so the disagreement is a
five-second diff" (its Symbol-ordering test's own words), this is the diff.

Escalation is structural: a `value` case whose real answer differs from tier 1's
raises `FrozenTableDisagreement` -- the implementer returns BLOCKED and the
controller rules (E10). Nobody edits `cases.py` or `serpent.types` to make this
green.
"""

from __future__ import annotations

import pytest

from serpent import val
from serpent.compiler.frontend import compile_module
from serpent.emitter import build_wasm
from serpent.testing import FrozenTableDisagreement, RealContractError, RealEnv, RealHostError
from serpent.testing._scval import from_xdr
from tests.semantics.cases import SemCase
from tests.semantics.test_semantics import _eval_case
from tests.unit.test_emitter_semantics import (
    IN_SCOPE, IN_SCOPE_COUNT, annotation_of, observed_ty, wrap_returning,  # name verified at impl time
)

real = pytest.mark.real_host  # per-test (review M12): the meta-tests below run everywhere


def _run(case: SemCase) -> tuple[object, object]:
    """(the compiler's Ty for the expression, the raw ScVal XDR the host returned)."""
    ty = observed_ty(case)
    source = wrap_returning(case.source, annotation_of(ty))
    wasm = build_wasm(compile_module(source, f"semantics_real/{case.name}.py")).wasm
    env = RealEnv()
    address = env.register_raw(wasm, ())
    return ty, env.invoke_raw(address, "go", ())  # undecoded: the decode is THIS test's assertion


def test_the_in_scope_count_is_unchanged() -> None:
    assert len(IN_SCOPE) == IN_SCOPE_COUNT == 35


_VALUE = [c for c in IN_SCOPE if c.kind == "value"]
_ERROR = [c for c in IN_SCOPE if c.kind == "contract_error"]
_TRAP = [c for c in IN_SCOPE if c.kind == "trap"]


@real
@pytest.mark.parametrize("case", _VALUE, ids=[c.name for c in _VALUE])
def test_a_value_case_answers_as_tier_1_does(case: SemCase) -> None:
    ty, raw = _run(case)
    real = from_xdr(raw, _authoring_type(ty))
    tier1 = _eval_case(case.source)
    if real != tier1 or type(real) is not type(tier1):
        raise FrozenTableDisagreement(
            f"{case.name}: real host answered {real!r} ({type(real).__name__}), tier 1 answered "
            f"{tier1!r} ({type(tier1).__name__}); controller decision required (ruling E10)"
        )
    assert real == case.expect


@real
def test_the_symbol_ordering_vector_on_the_real_host() -> None:
    """THE top differential vector (O12): `Symbol("_") < Symbol("A")`.

    Two failure modes are kept apart (review B1): the host REFUSING the compare is
    an emitter bug (Task 0's territory -- BLOCKED under E16, not a table matter);
    the host ANSWERING differently is the frozen-table escalation (E10).
    """
    (case,) = [c for c in IN_SCOPE if c.name == "symbol_underscore_vs_A_ascii_order"]
    try:
        ty, raw = _run(case)
    except RealHostError as exc:
        raise AssertionError(
            f"the host refused the Symbol compare ({exc.underlying}): an emitter bug, not a table "
            "disagreement -- Task 0 must have landed first"
        ) from exc
    answered = from_xdr(raw, _authoring_type(ty))
    assert val.symbol_char_code("_") < val.symbol_char_code("A")  # the 6-bit codes DO disagree with ASCII
    if answered != case.expect:
        raise FrozenTableDisagreement(
            f"symbol ordering: real host says {answered!r}, the frozen table says {case.expect!r}; "
            "controller decision on the frozen table required (dossier O12, ruling E10)"
        )


@real
def test_the_hosts_compare_trait_agrees_with_the_compiled_answer() -> None:
    """M2: the same question asked of the host directly, no contract in between."""
    (case,) = [c for c in IN_SCOPE if c.name == "symbol_underscore_vs_A_ascii_order"]
    verdict = RealEnv().compare(Symbol("_"), Symbol("A"))
    assert (verdict < 0) == bool(case.expect), f"compare says {verdict}, the table says {case.expect!r}"


@real
@pytest.mark.parametrize("case", _ERROR, ids=[c.name for c in _ERROR])
def test_a_contract_error_case_aborts_with_the_same_code(case: SemCase) -> None:
    ty = observed_ty(case)
    source = wrap_returning(case.source, annotation_of(ty))
    wasm = build_wasm(compile_module(source, f"semantics_real/{case.name}.py")).wasm
    env = RealEnv()
    c = env.deploy_wasm(wasm)
    with pytest.raises(RealContractError) as info:
        c.invoke("go")
    assert info.value.code == case.code
    assert info.value.error_type == "Contract"


@real
@pytest.mark.parametrize("case", _TRAP, ids=[c.name for c in _TRAP])
def test_a_trap_case_is_a_non_contract_host_error(case: SemCase) -> None:
    """A trap on chain is a HOST error, never a contract code. The frame-level
    type is Context/InvalidAction for EVERY guest failure (review B5), so the
    evidence is the UNDERLYING diagnostic."""
    ty = observed_ty(case)
    source = wrap_returning(case.source, annotation_of(ty))
    wasm = build_wasm(compile_module(source, f"semantics_real/{case.name}.py")).wasm
    c = RealEnv().deploy_wasm(wasm)
    with pytest.raises(RealHostError) as info:
        c.invoke("go")
    assert not isinstance(info.value, RealContractError)
    assert info.value.underlying == EXPECTED_UNDERLYING_ERROR[case.name], (
        f"{case.name}: the host reported frame {info.value.error_type}/{info.value.code}, "
        f"underlying {info.value.underlying}"
    )


#: The UNDERLYING (ScErrorType, ScErrorCode) the real host's diagnostics report
#: per trap case. FILLED FROM THE FIRST RUN and then frozen, with the run date in
#: a comment: these are host facts this repo did not have before (dossier
#: O11/O25). A wasm-level trap (i32 division by zero, out-of-bounds memory) is
#: expected as ("WasmVm", ...), a host-function trap (vec_get past the end) as
#: ("Object", "IndexBounds"), 128-bit //0 as ("Object", "ArithDomain") -- the
#: probe-confirmed value.
EXPECTED_UNDERLYING_ERROR: dict[str, tuple[str, str]] = {
    # filled by the implementer, one row per _TRAP case
}


def test_expected_underlying_error_covers_exactly_the_trap_cases() -> None:
    assert set(EXPECTED_UNDERLYING_ERROR) == {c.name for c in _TRAP}


def test_the_expected_underlying_errors_are_not_all_identical() -> None:
    """B5's vacuity guard: a map of twelve identical rows is not evidence."""
    assert len(set(EXPECTED_UNDERLYING_ERROR.values())) > 1


def _authoring_type(ty: object) -> object:
    """The compiler's `Ty` as the class/alias `from_xdr` decodes with."""
    import serpent  # the authoring namespace, exactly what a contract imports

    return eval(annotation_of(ty), {**vars(serpent), "None": None})  # noqa: S307 -- test-only, closed vocabulary
```

- [ ] **Step 2: Run to verify failure**

Run: `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q tests/real_host/test_semantics_real.py`
Expected: FAIL — `EXPECTED_UNDERLYING_ERROR` is empty (the coverage test fails; each trap test fails on the KeyError).

- [ ] **Step 3: Run once to collect the host facts, then fill `EXPECTED_UNDERLYING_ERROR`**

Run each trap case with `-x -k <name>` and record `info.value.underlying` per case into the dict with a dated comment. If ANY value case raises `FrozenTableDisagreement`, STOP: return BLOCKED with the row name and both answers — do not proceed to Step 4. If the ordering vector raises the "host refused" AssertionError, Task 0 did not land or did not cover this shape: BLOCKED.

- [ ] **Step 4: Run to verify all pass**

Run: `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q tests/real_host/test_semantics_real.py`
Expected: PASS; 35 in-scope cases exercised (value + error + trap partitions cover all 35 — assert in a fourth meta-test that the three partitions' union is `IN_SCOPE`).

- [ ] **Step 5: Gates, then commit**

```bash
git add tests/real_host/test_semantics_real.py
git commit -m "test(real-host): run the 35 in-scope semantics cases on the embedded host"
```

---

### Task 5: `ENV_SCENARIOS` on the real leg; `mini_host_gap` and `host_diverges` (O15–O19; rulings E8/E9)

**Files:**
- Modify: `tests/semantics/env_scenarios.py` (rename `tier1_only_reason` →
  `mini_host_gap`; add `host_diverges: HostDivergence | None = None`; add the
  `HostDivergence` dataclass; correct the three reason constants and the
  `Advance` docstring; set `host_diverges` on exactly the publish-then-raise
  row(s) AND, pre-declared, on every row whose `Advance` lapses a PERSISTENT
  entry (M3); and — the ONE value-level edit, ruled 2026-09-02 (B2) —
  `_ADMIN` becomes `Address(SHAPES_CONTRACT)` where `SHAPES_CONTRACT =
  "CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW"`, a real contract
  strkey, because the test host can only mock CONTRACT authorizers; the rows
  treat the authorizer as opaque, so their meaning is unchanged)
- Modify: `tests/unit/test_env_differential.py` (the rename, everywhere it
  reads the field), `tests/unit/test_no_stale_promises.py` (the E net's
  `_ALLOWLIST` migrates from `(path, line)` to `(path, stripped line text)`
  keys — review m15 / the G item B2, paid for the last time here)
- Create: `tests/real_host/test_env_scenarios_real.py`

**Interfaces:**
- Consumes: `tests.semantics.env_scenarios` (`ENV_SCENARIOS`, `EnvScenario`,
  `Call`, `Advance`, `HostDivergence`); `tests.unit.test_env_differential`
  (`Outcome`, `_tier_1(scenario) -> Outcome`, `TTL_METHODS`,
  `AUTH_ARGS_METHODS`); `serpent.testing.RealEnv` (`deploy_source`,
  `advance`, `compare`), `RealContract` (`invoke`, `add_mock_auths`,
  `events_for_sequence`, `auths_for_sequence`), `RealContractError`,
  `RealHostError`, `FrozenTableDisagreement`.
- Produces:

```python
@dataclass(frozen=True)
class HostDivergence:
    """A declared, EXPECTED difference between the tier-1 model and the real host
    (ruling E9). `reason` cites the dossier/spec fact; `events` is what the REAL
    host records for the whole sequence (the model's `events` field stays the
    model's). The runner asserts the divergence EXISTS: a model fix that removes
    it is loud here, not silent."""
    reason: str
    events: tuple[PublishedEvent, ...]

EnvScenario.mini_host_gap: str | None      # was tier1_only_reason (E8)
EnvScenario.host_diverges: HostDivergence | None = None
```

- [ ] **Step 1: The metadata edit (rename + new field + prose), with the existing suite as the test**

In `env_scenarios.py`: rename the field and every row's keyword
(`tier1_only_reason=` → `mini_host_gap=`, ~20 rows — a mechanical
search-and-replace inside the frozen rows' KEYWORDS is the one licensed
touch of row text; values unchanged); rewrite the three constants so each
says "the MINI HOST has no …; the real host at tier 2b runs this row"
(`TTL_REASON` drops the wrong "no ledger sequence" clause, O18); rewrite the
`Advance` docstring: "Tier-1 and real-host: `Env.advance` moves the model's
sequence; `RealEnv.advance` moves the embedded host's ledger sequence. The
MINI host has no per-entry live-until state, so an `Advance` forces
`mini_host_gap`. Nothing 'advances' the chain from inside a test — tier 3
never replays this step."; rewrite the field docstring to the E8 wording.
Add `HostDivergence` and the `host_diverges` field. Set `host_diverges` on
the publish-then-raise row(s) (grep `log_then_refuse`; the E-owned row
`an_event_published_before_a_raise_survives_at_both_tiers`):

```python
    host_diverges=HostDivergence(
        reason=(
            "S9: events roll back with a failed frame on chain. Both MODELS keep the "
            "event (the mini host mirrors tier 1 by construction, E1); the real host "
            "records it with `failed_call: true` and the sdk's Events::all() drops it "
            "(review m7). Pinned as a declared divergence (ruling E9) until the tier-1 "
            "model gains frame rollback (an M2 oracle edit)."
        ),
        events=(),  # the real host: nothing survives the refused frame
    ),
```

(If that row has setup events that DO survive, `events` lists exactly those.)

In `test_env_differential.py`: rename every `tier1_only_reason` read; the
biconditional test keeps its logic under the new name; add to the
row-coherence test: `assert scenario.host_diverges is None or scenario.host_diverges.events != scenario.events`
(a declared divergence that declares the same events is not a divergence).

Run: `uv run pytest -q tests/unit/test_env_differential.py tests/semantics`
Expected: PASS, same counts as before (87 in the differential file).

- [ ] **Step 2: Write the failing real-leg module**

```python
"""Tier 2b: the 62 `ENV_SCENARIOS` rows on the REAL host (dossier O16, rulings E8/E9/E10).

The mini host cannot run the TTL, auth-args, and allow-set rows (`mini_host_gap`);
the real host runs ALL 62. Three comparisons per row, in this order (O28):

1. real vs tier 1 -- equal, unless the row DECLARES a divergence (`host_diverges`),
   in which case the real outcome must match the declaration and DIFFER from tier 1;
2. real vs the row's pinned expectation (`kind`/`expect`/`code`);
3. the row's `events`/`auths` pins, against the real host's records.

An undeclared real-vs-tier-1 mismatch raises `FrozenTableDisagreement` (E10).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from serpent import Address, Vec
from serpent.env import RecordedAuth
from serpent.testing import (
    FrozenTableDisagreement, RealContractError, RealEnv, RealHostError,
)
from tests.semantics.env_scenarios import ENV_SCENARIOS, Advance, Call, EnvScenario
from tests.unit.test_env_differential import Outcome, _contract_class, _tier_1

real = pytest.mark.real_host  # per-test (M12): the table meta-tests run on every checkout


def _real(scenario: EnvScenario) -> Outcome:
    env = RealEnv(
        timestamp=DEFAULT_LEDGER_TIMESTAMP if scenario.timestamp is None else scenario.timestamp,
        sequence=DEFAULT_LEDGER_SEQUENCE if scenario.sequence is None else scenario.sequence,
        auths=scenario.auth_allow_set,
    )
    c = env.deploy_source(scenario.contract, *scenario.constructor)
    if scenario.auth_allow_set is not None:
        # `require_auth_for_args` rows authorize CUSTOM args: mock exactly the auths the
        # row expects to be recorded, on top of the per-call entries RealContract adds.
        c.add_mock_auths([(who, args) for who, args in scenario.auths if args is not None])
    for step in scenario.setup:
        if isinstance(step, Advance):
            env.advance(step.ledgers)
        else:
            c.invoke(step.method, *step.args)
    call = scenario.invoke
    answer: object = None
    code: int | None = None
    refused = False
    if scenario.kind == "contract_error":
        with pytest.raises(RealContractError) as info:
            c.invoke(call.method, *call.args)
        code = info.value.code
    elif scenario.kind == "auth_failed":
        with pytest.raises(RealHostError) as info:
            c.invoke(call.method, *call.args)
        assert not isinstance(info.value, RealContractError)
        assert info.value.underlying is not None and info.value.underlying[0] == "Auth", info.value
        refused = True
    else:
        answer = c.invoke(call.method, *call.args)
    auths = c.auths_for_sequence()  # every auth recorded across setup + invoke, in order
    return Outcome(
        answer=answer, answer_type=type(answer).__name__, code=code, refused=refused,
        events=c.events_for_sequence(), auth_addresses=tuple(a for a, _ in auths), auths=auths,
    )


def _comparable_to_tier1(scenario: EnvScenario, real: Outcome, tier1: Outcome) -> tuple[Outcome, Outcome]:
    """Tier 1 records `None` args for a bare `require_auth`; the host records the
    invocation's own args there. Compare addresses always; compare args only where
    tier 1 recorded some (a `require_auth_for_args` call), by blanking the real
    leg's args wherever tier 1's are `None`.

    Both legs record one entry per successful `require_auth*` call in invocation
    order, deploy excluded (M8) and refused calls excluded (O19) -- so the lengths
    agree unless the accumulation itself diverges, which is a table matter, not a
    helper's assert (review M13).
    """
    if len(real.auths) != len(tier1.auths):
        raise FrozenTableDisagreement(
            f"{scenario.name}: real recorded {len(real.auths)} auths {real.auths!r}, tier 1 "
            f"recorded {len(tier1.auths)} {tier1.auths!r}; controller decision required (O17/O19)"
        )
    blanked: tuple[RecordedAuth, ...] = tuple(
        (address, None if tier1_args is None else real_args)
        for (address, real_args), (_, tier1_args) in zip(real.auths, tier1.auths, strict=True)
    )
    return replace(real, auths=blanked), tier1


@real
@pytest.mark.parametrize("scenario", ENV_SCENARIOS, ids=[s.name for s in ENV_SCENARIOS])
def test_a_row_answers_on_the_real_host_as_tier_1_does(scenario: EnvScenario) -> None:
    real_outcome = _real(scenario)
    tier1 = _tier_1(scenario)
    if scenario.host_diverges is None:
        r, t = _comparable_to_tier1(scenario, real_outcome, tier1)
        if r != t:
            raise FrozenTableDisagreement(
                f"{scenario.name}: real {real_outcome!r} != tier 1 {tier1!r}; controller decision required"
            )
    else:
        assert real_outcome.events == scenario.host_diverges.events, scenario.host_diverges.reason
        assert real_outcome.events != tier1.events, "declared divergence did not occur; retire the declaration"
    if scenario.kind == "value":
        assert real_outcome.answer == scenario.expect
        assert type(real_outcome.answer).__name__ == real_outcome.answer_type
    elif scenario.kind == "contract_error":
        assert real_outcome.code == scenario.code
    elif scenario.kind == "auth_failed":
        assert real_outcome.refused
    if scenario.host_diverges is None:
        assert real_outcome.events == scenario.events
    assert real_outcome.auth_addresses == tuple(a for a, _ in scenario.auths)


def test_every_row_runs_here_including_the_mini_host_gap_rows() -> None:  # unmarked: table-only
    """The point of the leg: zero rows opt out."""
    gapped = [s.name for s in ENV_SCENARIOS if s.mini_host_gap is not None]
    assert gapped, "the table has gap rows; if it stops having them, delete this assertion"
    # parametrization above covers ENV_SCENARIOS in full; this pins that no filter crept in


def test_at_least_one_declared_divergence_exists() -> None:
    """Dossier F.1.1: a differential with no declared divergence has not asked the host anything
    the models did not already agree on."""
    assert any(s.host_diverges is not None for s in ENV_SCENARIOS)
```

Three façade additions this test needs (declared in Task 3's interface,
implemented here with coverage from this module's first run):
`add_mock_auths(entries)` REGISTERS pending `(who, args)` entries; every
later `invoke` on the contract builds ONE complete set — the per-call entry
for the method being invoked for each allowed address, plus every pending
entry paired with that same method — and passes it to `raw.mock_auths`
(which REPLACES, M6). `events_for_sequence()` / `auths_for_sequence()`
accumulate across every `invoke` on this contract since deploy (deploy's
own CreateContractV2HostFn auth is never recorded, M8); `events()`/`auths()`
stay last-invocation.

- [ ] **Step 3: Run; collect; escalate or pin**

Run: `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q tests/real_host/test_env_scenarios_real.py -x`
Expected first run: the publish-then-raise row passes via its declaration.
TTL rows exercise REAL expiry for TEMPORARY entries (probe-confirmed: a
lapsed temporary entry reads absent). The test host does NOT model
persistent archival (review M3: a lapsed persistent entry stays readable),
so any row whose `Advance` lapses a PERSISTENT entry and then reads it is
PRE-DECLARED in Step 1 — before the run — as `host_diverges` with the M3
reason ("the sdk test Env does not model archival; tier 1 reads absent, the
test host reads present, the chain archives — proven only at tier 3,
carried") and the real leg's expected events/answer (`HostDivergence` gains
an optional `answer` field for this). The implementer identifies those rows
from `TTL_METHODS` + `Advance` steps + the durability the method touches
(`bump_slot`/`read_slot*` are persistent; `bump_temp`/`read_temp_or`
temporary; `bump_instance` instance). Any OTHER mismatch → BLOCKED with both
outcomes.

- [ ] **Step 4: Run to verify all 62 pass, then the whole suite**

Run: `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q tests/real_host tests/unit/test_env_differential.py`
Expected: PASS.

- [ ] **Step 5: Gates, then TWO commits (metadata first, leg second)**

```bash
git add tests/semantics/env_scenarios.py tests/unit/test_env_differential.py
git commit -m "test(semantics): rename tier1_only_reason to mini_host_gap and declare host divergences"
git add tests/real_host/test_env_scenarios_real.py src/serpent/testing/_real.py
git commit -m "test(real-host): run all 62 ENV_SCENARIOS rows on the embedded host"
```

---

### Task 6: `HOST_FACTS` — the F-owned table of facts the models only assumed (O10, O11, O13–O15, O24–O26, E6, E12, E15)

**Files:**
- Create: `tests/fixtures/host_facts.py` (the contract), `tests/semantics/host_facts.py`
  (the table), `tests/real_host/test_host_facts_real.py`,
  `tests/real_host/test_feature_set_real.py`, `tests/unit/test_host_facts_tier1.py`
- Modify: `tests/unit/test_frontend_fuzz.py` — the EXACT fixture inventory
  (`test_the_corpus_is_the_whole_fixture_inventory`, ~line 1060) gains
  `"fixtures/host_facts.py"` (review B8: `CORPUS` globs `tests/fixtures/*.py`,
  so the new file is mutation-fuzzed by construction and must be a fully
  valid module); `tests/harness/testmod.py` — `build_test_module` gains
  `custom_sections: Mapping[str, bytes] = {}` (review M1). The three literal
  inventories (`FIXTURES`, `_FIXTURES`, `FIXTURE_SOURCES`) are NOT joined,
  stated in the fixture's docstring.

**Interfaces:**
- Consumes: `serpent.testing.RealEnv/RealContract/RealHostError/RealContractError`;
  `serpent.env.Env/deploy`; `tests.harness.testmod.build_test_module` (for the
  hand-assembled proposal modules).
- Produces:

```python
# tests/semantics/host_facts.py
@dataclass(frozen=True, kw_only=True)
class HostFact:
    name: str
    fact: str                       # the S9/O# sentence this row proves, verbatim with its ID
    constructor: tuple[ChainValue, ...] = ()
    sequence: int | None = None
    setup: tuple[Call | Advance, ...] = ()
    invoke: Call
    real: Expectation               # what the REAL (test) host does
    tier1: Expectation | Unmodelled # what the model does, or why it cannot say
    chain_unproven: str | None = None     # M3: set when the TEST host is known to differ from the CHAIN
                                          # (archival); such a row is evidence about the test host only
    write_entries: int | None = None      # E6: footprint counts the real leg asserts
    read_entries: int | None = None       # memory_read_entries + disk_read_entries

@dataclass(frozen=True)
class Value:      value: ChainValue | None
@dataclass(frozen=True)
class ContractErr: code: int
@dataclass(frozen=True)
class HostErr:
    """A non-contract host failure. `underlying` is the (ScErrorType, ScErrorCode)
    NAMES from the diagnostics (B5) -- the frame level is always ("Context",
    "InvalidAction") and is not worth a field. None until the first run pins it."""
    underlying: tuple[str, str] | None = None
@dataclass(frozen=True)
class Unmodelled: reason: str
Expectation = Value | ContractErr | HostErr

HOST_FACTS: tuple[HostFact, ...]
#: The pinned UNDERLYING classification of 128-bit `//0` on the real host (E15; consumed
#: by Task 8). Probe-confirmed 2026-09-02: ("Object", "ArithDomain").
DIV128_BY_ZERO_HOST_ERROR: HostErr = HostErr(("Object", "ArithDomain"))
#: Ordering vectors the real leg asks the host's Compare trait directly (M2/E12/O12):
#: (a, b, expected_sign) with expected_sign None until the first run pins it.
COMPARE_VECTORS: tuple[tuple[ChainValue, ChainValue, int | None], ...]
```

- [ ] **Step 1: Write the fixture contract** `tests/fixtures/host_facts.py`

One narrow method per fact (all compile today — no new language surface):

```python
"""The contract `tests/semantics/host_facts.py` drives (dossier D.3's HOST_FACTS).

One narrow export per host fact the tier-1 model and the mini host only ASSUMED.
Not an example (nothing here is idiomatic), not in the whole-contract sweeps
(`FIXTURES` etc.) by design: its only consumer is the HOST_FACTS table.
"""

from serpent import I128, U32, U128, Address, Bool, Env, Symbol, Vec, contract, contracterror, errorcode

KEY = Symbol("K")


@contracterror
class Refused:
    Nope = errorcode(9)


@contract
class HostFacts:
    # --- TTL (S9/O14): clamp, trap, dead entry ------------------------------------
    def put_p(self, env: Env, v: U32) -> None:
        env.storage().persistent().set(KEY, v)

    def put_t(self, env: Env, v: U32) -> None:
        env.storage().temporary().set(KEY, v)

    def get_p_or(self, env: Env, fallback: U32) -> U32:
        return env.storage().persistent().get(KEY, U32, default=fallback)

    def get_t_or(self, env: Env, fallback: U32) -> U32:
        return env.storage().temporary().get(KEY, U32, default=fallback)

    def extend_p(self, env: Env, threshold: U32, extend_to: U32) -> None:
        env.storage().persistent().extend_ttl(KEY, threshold, extend_to)

    def extend_t(self, env: Env, threshold: U32, extend_to: U32) -> None:
        env.storage().temporary().extend_ttl(KEY, threshold, extend_to)

    # --- del_ on an absent key (O13) ---------------------------------------------
    def del_absent(self, env: Env) -> Bool:
        env.storage().persistent().del_(KEY)
        return Bool(True)

    # --- publish then raise (S9/O15) ---------------------------------------------
    def publish_then_raise(self, env: Env, who: Address) -> None:
        env.events().publish((Symbol("logged"), who), U32(1))
        raise Refused.Nope

    # --- auth refusal is a trap, not a recorded auth (O19/O26) -------------------
    def guard(self, env: Env, who: Address) -> None:
        who.require_auth()

    # --- 128-bit division (O10/O11) ----------------------------------------------
    def div_i128(self, env: Env, a: I128, b: I128) -> I128:
        return a // b

    def mod_i128(self, env: Env, a: I128, b: I128) -> I128:
        return a % b

    def div_u128(self, env: Env, a: U128, b: U128) -> U128:
        return a // b

```

(The review compiled this fixture: every method above compiles and builds
(3,055 bytes) — a `vec_lt` returning `Bool(a < b)` does NOT (`SPT3005`,
containers have no `<` in the subset) and is therefore not in the fixture;
container ordering is observed through the host's `Compare` trait instead
(`RealEnv.compare`, review M2) via `COMPARE_VECTORS`, and the fixture stays a
valid mutation-corpus member (B8). `extend_ttl`'s spelling is read from
`env_surface.py`.)

- [ ] **Step 2: Write the table** `tests/semantics/host_facts.py` — the dataclasses above and these rows (values FILLED where the fact is known, `HostErr(type, None)` where the first run pins the code):

```python
# Header of tests/semantics/host_facts.py: the imports the rows use.
#   from tests.semantics.env_scenarios import ACCOUNT, CONTRACT as OTHER, Advance, Call
#   from serpent.testing._real import (DEFAULT_MAX_ENTRY_TTL, DEFAULT_MIN_PERSISTENT_ENTRY_TTL,
#                                      DEFAULT_MIN_TEMP_ENTRY_TTL)
#   from serpent.types import I128, U32, U128, Address, Bool, Vec
HOST_FACTS: tuple[HostFact, ...] = (
    # `extend_ttl(threshold, extend_to)` extends ONLY when the current TTL is below
    # `threshold` (review B9: threshold 0 is a no-op that returns Void and pins
    # nothing). The negative-control row pins that semantics itself.
    HostFact(
        name="an_extension_whose_threshold_is_below_the_current_ttl_is_a_no_op",
        fact="B9: extend_ttl is conditional on threshold; threshold 0 changes nothing",
        sequence=1_000_000,
        setup=(Call("put_p", (U32(1),)),),
        invoke=Call("extend_p", (U32(0), U32(DEFAULT_MAX_ENTRY_TTL + 10_000))),
        real=Value(None),                 # the real test ALSO asserts ttl(KEY) unchanged (4095)
        tier1=Value(None),
    ),
    HostFact(
        name="max_live_until_is_max_entry_ttl_minus_one",
        fact="S9's `-1`: max_ttl() == max_entry_ttl - 1 (observed 6_311_999, review m12)",
        invoke=Call("del_absent", ()),     # any call; the real test asserts env.max_ttl()
        real=Value(Bool(True)),
        tier1=Unmodelled("no max live-until at tier 1 (D6/E4)"),
    ),
    HostFact(
        name="persistent_extension_past_the_maximum_clamps",
        fact="S9: persistent extension past max CLAMPS (O14; test_env_ttl skip 349)",
        sequence=1_000_000,
        setup=(Call("put_p", (U32(1),)),),
        invoke=Call("extend_p", (U32(DEFAULT_MAX_ENTRY_TTL), U32(DEFAULT_MAX_ENTRY_TTL + 88_000))),
        real=Value(None),                 # the real test ALSO asserts ttl(KEY) == env.max_ttl()
        tier1=Unmodelled("no max live-until at tier 1 (D6/E4)"),
    ),
    HostFact(
        name="temporary_extension_past_the_maximum_traps",
        fact="S9: temporary extension past max TRAPS (O14; test_env_ttl skip 357)",
        sequence=1_000_000,
        setup=(Call("put_t", (U32(1),)),),
        invoke=Call("extend_t", (U32(DEFAULT_MAX_ENTRY_TTL), U32(DEFAULT_MAX_ENTRY_TTL + 88_000))),
        real=HostErr(),                   # underlying pinned from the first run (probe: frame Context/6)
        tier1=Unmodelled("no max live-until at tier 1 (D6/E4)"),
    ),
    HostFact(
        name="extend_to_below_threshold_is_itself_an_error",
        fact="B9 probe: extend_p(threshold=1_000_000, extend_to=100_000) errors",
        sequence=1_000_000,
        setup=(Call("put_p", (U32(1),)),),
        invoke=Call("extend_p", (U32(1_000_000), U32(100_000))),
        real=HostErr(),
        tier1=HostErr(),                  # tier 1's own refusal; the tier-1 runner maps it
    ),
    HostFact(
        name="extending_a_never_written_key_errors",
        fact="S9: extending a dead entry errors (O14)",
        invoke=Call("extend_p", (U32(DEFAULT_MAX_ENTRY_TTL), U32(100))),
        real=HostErr(),
        tier1=HostErr(),                  # tier 1 raises its own MissingValue-class error; the runner maps
    ),
    HostFact(
        name="a_lapsed_temporary_entry_reads_absent",
        fact="O14: a lapsed temporary entry is gone for good",
        sequence=1_000_000,
        setup=(Call("put_t", (U32(7),)), Advance(DEFAULT_MIN_TEMP_ENTRY_TTL + 1)),
        invoke=Call("get_t_or", (U32(0),)),
        real=Value(U32(0)),
        tier1=Value(U32(0)),
    ),
    HostFact(
        name="a_lapsed_persistent_entry_stays_readable_on_the_test_host",
        fact="O14/M3: chain ARCHIVES a lapsed persistent entry; the sdk test Env does NOT model that",
        sequence=1_000_000,
        setup=(Call("put_p", (U32(7),)), Advance(DEFAULT_MIN_PERSISTENT_ENTRY_TTL + 1)),
        invoke=Call("get_p_or", (U32(0),)),
        real=Value(U32(7)),               # probe-confirmed: STILL READABLE past live_until (test host)
        tier1=Value(U32(0)),              # the model reads it absent
        chain_unproven=(
            "archival is ledger-level behaviour the sdk test Env does not model: tier 1 says absent, "
            "the test host says present, the chain says archived -- proven only at tier 3, carried to M2"
        ),
    ),
    HostFact(
        name="del_of_an_absent_key_is_a_no_op",
        fact="O13: `del_` on an absent key is a no-op on the host, as both models assume",
        invoke=Call("del_absent", ()),
        real=Value(Bool(True)),
        tier1=Value(Bool(True)),
        write_entries=1,                  # probe: the contract INSTANCE entry counts as a write even here (M9)
    ),
    HostFact(
        name="a_single_slot_write_is_one_write_entry_plus_the_instance",
        fact="E6: a derivable footprint count -- put_p writes the slot AND the instance entry",
        invoke=Call("put_p", (U32(1),)),
        real=Value(None),
        tier1=Value(None),
        write_entries=2,                  # a PREDICTION, not a transcription (M9); first run confirms or BLOCKS
    ),
    HostFact(
        name="an_event_published_before_a_raise_is_rolled_back",
        fact="S9/O15: events roll back with the failed frame",
        invoke=Call("publish_then_raise", (Address(ACCOUNT),)),
        real=ContractErr(9),
        tier1=ContractErr(9),             # SAME outcome; the EVENTS differ -- asserted in the real test: events == ()
    ),
    HostFact(
        name="a_refused_auth_is_an_auth_trap_and_records_nothing",
        fact="O19/O26: refusal traps (underlying Auth); nothing is recorded",
        invoke=Call("guard", (Address(OTHER),)),   # with RealEnv(auths=(ALLOWED,)) -- see the runner
        real=HostErr(("Auth", "InvalidAction")),   # underlying; first run confirms the code name
        tier1=HostErr(("Auth", "InvalidAction")),  # tier 1's AuthorizationFailed maps here
    ),
    HostFact(name="i128_floordiv_truncates_toward_zero", fact="O10 (D3): rounding of i256_div",
             invoke=Call("div_i128", (I128(-7), I128(2))), real=Value(I128(-3)), tier1=Value(I128(-3))),
    HostFact(name="i128_mod_takes_the_dividends_sign", fact="O10: `%` sign (A4)",
             invoke=Call("mod_i128", (I128(-7), I128(2))), real=Value(I128(-1)), tier1=Value(I128(-1))),
    HostFact(name="i128_min_mod_minus_one_is_zero", fact="O10: MIN % -1 == 0 without overflow",
             invoke=Call("mod_i128", (I128(-(2**127)), I128(-1))), real=Value(I128(0)), tier1=Value(I128(0))),
    HostFact(name="i128_div_by_zero_is_a_host_error_not_a_trap_code", fact="O11 (E15): the real XDR code",
             invoke=Call("div_i128", (I128(1), I128(0))), real=DIV128_BY_ZERO_HOST_ERROR,
             tier1=DIV128_BY_ZERO_HOST_ERROR),   # tier 1's ZeroDivisionError maps here
    HostFact(name="u128_div_by_zero_is_the_same_host_error", fact="O11",
             invoke=Call("div_u128", (U128(1), U128(0))), real=DIV128_BY_ZERO_HOST_ERROR,
             tier1=DIV128_BY_ZERO_HOST_ERROR),
)

#: Asked of the host's Compare trait directly (RealEnv.compare), no contract in
#: between (M2). Vectors chosen to separate lexicographic-then-length from
#: length-then-lexicographic, and to answer O12 for small Symbols. The
#: expected sign is None until the first run pins it (dated comment); tier 1
#: has no answer for the container rows (A15) and an ASCII answer for the
#: Symbol rows (`Symbol.__lt__`), which the runner ALSO compares -- a Symbol
#: disagreement is a FrozenTableDisagreement (E10).
COMPARE_VECTORS = (
    (Vec(U32, [U32(1)]), Vec(U32, [U32(1), U32(0)]), None),
    (Vec(U32, [U32(2)]), Vec(U32, [U32(1), U32(0)]), None),
    (Vec(U32, [U32(1), U32(2)]), Vec(U32, [U32(1), U32(3)]), None),
    (Symbol("_"), Symbol("A"), None),
    (Symbol("a"), Symbol("B"), None),
    (Symbol("abcdefghijk"), Symbol("abcdefghijl"), None),   # object vs object
    (Symbol("abc"), Symbol("abcdefghijk"), None),           # small vs object
)
```

Tier-1 `HostErr` rows map the model's own exception classes to underlying
pairs: the TTL model's refusals ↔ `("Storage", ...)`; `AuthorizationFailed`
↔ `("Auth", "InvalidAction")`; `ZeroDivisionError` ↔ `("Object",
"ArithDomain")` — the tier-1 runner (`test_host_facts_tier1.py`) owns that
mapping table and states it. A tier-1 `HostErr()` with `underlying=None`
matches any tier-1 refusal of the mapped family.

- [ ] **Step 3: Write the real-leg runner** `tests/real_host/test_host_facts_real.py`

Per row (each test `@real`-marked; the table meta-tests unmarked): `RealEnv(sequence=…,
auths=(Address(ALLOWED),) if row.name.startswith("a_refused_auth") else None)`
with `ALLOWED` a CONTRACT strkey (B2), `deploy_source(tests/fixtures/host_facts.py)`,
replay `setup`, run `invoke`, match `real`: `Value(v)` → `answer == v`;
`ContractErr(c)` → `RealContractError.code == c`; `HostErr(u)` →
`RealHostError` that is not a `RealContractError` and, when `u` is not
None, `.underlying == u`. Row-specific extras: the no-op row asserts
`ttl(KEY)` unchanged; the `max_ttl` row asserts `env.max_ttl() ==
DEFAULT_MAX_ENTRY_TTL - 1`; the clamp row asserts `c.storage("persistent")
.ttl(KEY) == env.max_ttl()` (B9/B10); the rollback row asserts `c.events()
== ()`; the auth row asserts `c.auths() == ()`; a `chain_unproven` row
asserts the test-host value AND that it differs from tier 1's (M3: a
two-sided declared divergence); `write_entries`/`read_entries` when
declared assert against `c.resources()["write_entries"]` and
`resources()["memory_read_entries"] + resources()["disk_read_entries"]`
(E6: footprint COUNTS — key-level footprint is M2 via `e2e_invoke`, stated
in the module docstring). `COMPARE_VECTORS`: `RealEnv().compare(a, b)`'s
sign per row; for Symbol rows also compare to `a < b` at tier 1 and raise
`FrozenTableDisagreement` on mismatch (O12/E10). After the first run, pin
every `HostErr()` underlying and every `None` sign into the table with a
dated comment, and re-run green.

- [ ] **Step 4: Write the tier-1 leg** `tests/unit/test_host_facts_tier1.py`

Runs every row whose `tier1` is not `Unmodelled` against `Env`/`deploy`
(same replay shape as `test_env_differential._tier_1`), with the exception
mapping table; `Unmodelled` rows are `pytest.skip(row.tier1.reason)` —
ENUMERABLE like `test_env_ttl.py`'s. A meta-test asserts every row's `fact`
cites an ID (`re.search(r"\b(S\d+|O\d+|E\d+|D\d+)\b", row.fact)`).

- [ ] **Step 5: The un-toggleable proposals** `tests/real_host/test_feature_set_real.py` (O3)

The host REJECTS every module without a `contractenvmetav0` custom section
(`WasmVm(InvalidInput)`, "contract missing metadata section" — review M1),
so a `build_test_module` module fails for the wrong reason and the four
negatives would pass vacuously. Therefore FIRST: `tests/harness/testmod.py`'s
`build_test_module` gains `custom_sections: Mapping[str, bytes] = {}`
(section id 0, name + payload, emitted after the code/data sections), and
every module in this file carries `{"contractenvmetav0":
serpent.spec.build_env_meta(20)}` (the emitter's own writer — one
implementation). The POSITIVE control — a module using `i64.extend8_s`
(sign-extension, ON in the chain's config) plus the meta section — MUST
register and be invokable; a meta-test asserts it passes, which is what
makes the negatives mean anything. Then four negatives, each asserting the
UNDERLYING diagnostic (not just `WasmVm`): a function body containing
`f64.const 0` (opcode `0x44` + 8 bytes) then `drop`; a memory whose limits
flag is `0x04` (memory64 — `build_test_module` has no limits-flag knob:
write those ~30 bytes by hand with a comment per byte); a GLOBAL whose init
expression is `i32.const 1; i32.const 2; i32.add; end` (extended-const —
`build_test_module` emits no global section: hand-assemble, same rule); a
body with `try` (`0x06`). Each `RealEnv().deploy_wasm(bytes)` raises a
catchable `RealHostError`/`HostPanic` (the sdk's `register` PANICS on a
rejected module — probe-confirmed with a String payload — and `contained`
turns that into kind "panic"); record which kind and which underlying code
per module in a dated comment.

- [ ] **Step 6: Run everything, pin the first-run facts, run again**

Run: `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q tests/real_host/test_host_facts_real.py tests/real_host/test_feature_set_real.py tests/unit/test_host_facts_tier1.py`
Expected: PASS after the pins; every `HostErr()` in the table has a concrete
underlying pair; every `COMPARE_VECTORS` sign is pinned. The archival row is
pre-declared (`chain_unproven`), so it must NOT be "corrected" from a run:
if the test host ever starts archiving, that row FAILS and the declaration
is retired deliberately.

- [ ] **Step 7: Gates, then commit**

```bash
git add tests/fixtures/host_facts.py tests/semantics/host_facts.py tests/real_host/test_host_facts_real.py tests/real_host/test_feature_set_real.py tests/unit/test_host_facts_tier1.py tests/unit/test_frontend_fuzz.py tests/harness/testmod.py
git commit -m "test(real-host): add the HOST_FACTS table pinning TTL, rollback, auth, ordering, and 128-bit division facts"
```

---

### Task 7: The six examples' real leg, and a union/enum RETURN decoded through `ty` (O5, C8)

**Files:**
- Create: `tests/real_host/test_examples_real.py`
- Modify: `tests/fixtures/udt_style.py` — ADD ONE method `current_shape(self, env: Env) -> Shape`
  returning the stored union (`level()`/`recorded()` already return the int
  enum `Level`; no method returns `Shape` today). The fixture is in the fuzz
  `CORPUS` and the mypy positive half ONLY (not `FIXTURES`, not the goldens),
  so the addition touches no inventory and no golden; Step 2's golden check
  is therefore expected to find nothing to regenerate.

**Interfaces:**
- Consumes: `serpent.testing.RealEnv`; `tests.unit.test_examples.load_example`,
  `_allowance_token_roles`, `OWNER`, `SPENDER`; `tests.unit.test_emitter_end_to_end`
  `EXAMPLE_*`; `serpent.env.Env/deploy`.
- Produces: nothing importable.

- [ ] **Step 1: Write the failing module**

```python
"""Tier 2b: every example's headline call sequence, tier 1 vs the REAL host (dossier C8, O5).

`tests/unit/test_examples.py` runs each example at tier 1 and under the mini host
and compares the two. This module re-runs the SAME sequences with the real host as
the second leg. The sequences are restated here rather than imported: the
existing tests interleave their two legs inline, and a shared table would mean
editing frozen-by-convention example tests. Each sequence is `(method, args)`
tuples; the tier-1 leg and the real leg replay it and the decoded answers are
compared pairwise, THEN pinned (O28's order).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from serpent import U32, Address, Bool, Symbol
from serpent.env import Env, deploy
from serpent.testing import RealContractError, RealEnv
from tests.unit.test_emitter_end_to_end import (
    EXAMPLE_ALLOWANCE_TOKEN, EXAMPLE_COUNTER, EXAMPLE_ERRORS, EXAMPLE_EVENTS, EXAMPLE_SHAPES,
    EXAMPLE_STRUCTS,
)
from tests.unit.test_examples import _allowance_token_roles, load_example

real = pytest.mark.real_host  # per-test (M12); every test here needs the host, but the rule is stated

Step = tuple[str, tuple[Any, ...]]


def _both_legs(path: Path, ctor: tuple[Any, ...], steps: Sequence[Step]) -> tuple[list[object], list[object]]:
    cls = _contract_class(load_example(path))
    env1 = Env()
    inst = deploy(cls, env1, *ctor)
    tier1: list[object] = []
    for method, args in steps:
        with env1.frame():
            tier1.append(_outcome(lambda: getattr(inst, method)(env1, *args)))
    real_env = RealEnv()
    c = real_env.deploy_source(path, *ctor)   # B3: path-loaded classes have no sys.modules entry
    real_leg: list[object] = [_outcome(lambda m=method, a=args: c.invoke(m, *a)) for method, args in steps]
    return tier1, real_leg


def _outcome(call: Any) -> object:
    """A value, or ('error', code) -- both legs normalize errors to the code."""
    try:
        return call()
    except RealContractError as exc:
        return ("error", exc.code)
    except Exception as exc:  # tier 1's @contracterror members are exception classes with .code
        code = getattr(type(exc), "code", None)
        if code is None:
            raise
        return ("error", code)


@real
def test_counter() -> None:
    tier1, real = _both_legs(EXAMPLE_COUNTER, (), [("increment", (U32(2),)), ("increment", (U32(3),)), ("total", ())])
    assert real == tier1 == [U32(2), U32(5), U32(5)]


@real
def test_errors_vault() -> None:
    owner = Address("GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY")
    steps: list[Step] = [("deposit", (U32(4),)), ("deposit", (U32(7),)), ("withdraw", (U32(9),)), ("balance", ())]
    tier1, real = _both_legs(EXAMPLE_ERRORS, (owner, U32(10)), steps)
    assert real == tier1
    assert real[1] == ("error", 3)  # LimitExceeded


# structs, events, allowance_token, shapes: the implementer restates each example's
# headline sequence from tests/unit/test_examples.py (the `..._answers_the_same_at_tier_1_and_as_wasm`
# tests) as a `steps` list and asserts `real == tier1` first, then the literal pins that
# test carries. allowance_token's auth: RealEnv() mocks all auths, matching Env(auths=None).


@real
def test_a_union_and_an_int_enum_return_decode_through_their_types() -> None:
    """O5 lifted: the mini host could not decode these; the real leg decodes via `ty`.
    `area`/`radius` on this fixture compare small Symbols (B1) -- runnable only after Task 0."""
    udt_style = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "udt_style.py"
    module = load_example(udt_style)
    c = RealEnv().deploy_source(udt_style)
    c.invoke("set_rect", U32(2), U32(3))
    assert c.invoke("current_shape") == module.Shape.Rect(U32(2), U32(3))
    c.invoke("promote")
    assert isinstance(c.invoke("level"), module.Level)
    env = Env(); inst = deploy(module.UdtStyle, env)
    with env.frame():
        inst.set_rect(env, U32(2), U32(3))
        assert inst.current_shape(env) == module.Shape.Rect(U32(2), U32(3))


def _contract_class(module: object) -> type:
    from serpent.decorators import _METADATA_ATTR
    (cls,) = [m for m in vars(module).values()
              if isinstance(m, type) and vars(m).get(_METADATA_ATTR, {}).get("kind") == "contract"]
    return cls
```

- [ ] **Step 2: Add `current_shape` to `udt_style.py`** (returns `Shape`; reads the stored
  shape the fixture already keeps — the implementer mirrors `shape_name`'s read).
  Run the existing suite: `uv run pytest -q tests/unit/test_emitter_end_to_end.py tests/unit/test_harness_hostfns.py tests/unit/test_frontend_fuzz.py -q` — the fixture's goldens (`test_emitter_printer.py` `FIXTURE_NAMES`) regenerate if `udt_style` has a disassembly snapshot: check `FIXTURE_NAMES`; if it is listed, regenerate the golden with the documented command in that file and commit it in the same change.

- [ ] **Step 3: Run the real leg**

Run: `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q tests/real_host/test_examples_real.py`
Expected: PASS for all six + the UDT return test. A mismatch is NOT a
frozen-table matter (examples are not tables) but IS an emitter/model bug:
return BLOCKED with the diff (E16).

- [ ] **Step 4: Gates, then commit**

```bash
git add tests/real_host/test_examples_real.py tests/fixtures/udt_style.py tests/goldens  # goldens only if regenerated
git commit -m "test(real-host): run the six examples on the embedded host and decode UDT returns by type"
```

---

### Task 8: Tier-2a productization, scoped (O1, O4, O5, E7, E15)

**Files:**
- Create: `tests/harness/cache.py`
- Modify: `tests/harness/objects.py` (`chain_value_as`, `val_word` containers),
  `tests/harness/i256.py` (`DIV_ERROR_VAL` names the pinned host code),
  `tests/unit/test_env_differential.py` (`_built` → `cache.built`),
  `tests/unit/test_examples.py` + `tests/unit/test_emitter_end_to_end.py`
  (`build_fixture` → `cache.built`), `tests/harness/hostfns.py` + `engine.py`
  (docstring repoints: "F's obligation" → "proven in tests/real_host/…")

**Interfaces:**
- Consumes: Task 6's `DIV128_BY_ZERO_HOST_ERROR` (`tests.semantics.host_facts`;
  its `underlying` is `("Object", "ArithDomain")`, so `DIV_ERROR_VAL` becomes
  `val.error_val(<ArithDomain discriminant, 0>, val.ERROR_TYPE_OBJECT)` — the
  code names are mapped to discriminants through `stellar_sdk.xdr.SCErrorCode`).
  NOT changed here: the mini host's `obj_cmp` container comparison stays
  `NotImplementedError` — the host's answer is HOST_FACTS' `COMPARE_VECTORS`
  evidence (O6), and the tier-1 implementation is M2 (E12).
- Produces:

```python
# tests/harness/cache.py
def built(path: Path) -> BuildResult
    """`build_file(path)` memoised on (resolved path, sha256 of the file text): a
    changed source rebuilds, an unchanged one is compiled once per session. The
    HOST is never cached (C7's rule) -- only the bytes."""

# tests/harness/objects.py
def chain_value_as(self, word: int, ty: object) -> object
    """A typed decode: `ty` a chain class, `Vec[T]`, `Map[K, V]`, a ContractUnion/
    ContractEnum subclass, or a @contracttype class. Containers are walked
    through the store (`_vec`/`_map`) and each element decoded by its element
    type; the rank placeholders are never returned. The public replacement for
    `test_examples.py`'s reach into `host._vec` (O4)."""
# val_word gains: Vec -> a fresh vec handle holding each element's word; Map -> a
# fresh map handle keyed by value; ContractUnion -> the led Vec; ContractEnum -> U32;
# struct -> the sorted-key map (so `Call` args may be containers on the mini leg too).
```

- [ ] **Step 1: Failing tests** — in the NEW `tests/unit/test_harness_objects.py`
  (there is no existing objects test module, review m3): `chain_value_as` round-trips `Vec(U32, [...])`,
  a `Map(Symbol, U32)`, `Shape.Rect(...)` (from `tests/unit/_udt_decls.py`),
  `Color.Green`, and a `@contracttype` through `val_word` → `chain_value_as`;
  `chain_value_as(word, U32)` on a Symbol word raises `AssertionError`; `built()`
  returns the same object twice for an unchanged file and a different object
  after the file's text changes (tmp_path copy of `examples/counter.py`).
  Run: expect `AttributeError`/`ImportError`.

- [ ] **Step 2: Implement** the three pieces; replace the private reach in
  `test_examples.py:398-430` with `chain_value_as(word, Vec[...])` (read the
  test to get the element type); switch `_built`/`build_fixture` callers to
  `cache.built` (keep `build_fixture` as a thin alias so nothing else moves);
  `DIV_ERROR_VAL = val.error_val(0, val.ERROR_TYPE_OBJECT)` (ArithDomain's
  discriminant is 0, Object's is 4 — derived in a test from
  `DIV128_BY_ZERO_HOST_ERROR.underlying` via `stellar_sdk.xdr.SCErrorCode`/
  `SCErrorType`, so the literal cannot drift from the pinned fact) with its
  docstring rewritten from "an unpinned XDR code" to "the UNDERLYING code the
  real host reported on 2026-09-xx (tests/semantics/host_facts.py)"; update
  the one test that pins `DIV_ERROR_VAL`'s shape (it asserted the old
  `(0, VALUE)` word).

- [ ] **Step 3: Docstring repoints** in `hostfns.py:53-76`, `engine.py:133-135`,
  `objects.py:117-122`, `i256.py:28-46`: every "sub-plan F will/is where"
  becomes "proven on the real host in `tests/real_host/<file>::<test>`" or
  "declared divergence (`host_diverges`) in `env_scenarios.py`" — the exact
  outcome per sentence, no forward-looking F promise left (O33 feeds Task 10's
  sweep).

- [ ] **Step 4: Run the whole suite** — `uv run pytest -q`; expected: PASS, wall
  time not worse (the cache removes ~60 rebuilds).

- [ ] **Step 5: Gates, then commit**

```bash
git add tests/harness tests/unit/test_env_differential.py tests/unit/test_examples.py tests/unit/test_emitter_end_to_end.py tests/unit/test_harness_objects.py
git commit -m "test(harness): cache compiled modules, add typed container decoding, and pin the 128-bit div-by-zero code"
```

---

### Task 9: Tier 3 — the simulation-only fixture runner, proven on the deployed shapes contract (U3, U5, K6, K7, E14)

**Files:**
- Create: `src/serpent/testing/testnet.py`, `tests/real_host/test_testnet_fixtures.py`,
  `tests/real_host/fixtures/testnet/shapes/*.json` (recorded),
  `tests/real_host/fixtures/testnet/shapes/deployed.wasm` (the 4,171 bytes
  fetched from the chain — `stellar contract fetch`, sha256 `6a9dd135…6e33`;
  the real leg replays THESE bytes, same-bytes per Phase 0, because Task 0
  changes what `build_file(shapes.py)` produces — B1 ruling),
  `tests/real_host/fixtures/testnet/README.md`

**Interfaces:**
- Consumes: `stellar_sdk` (`SorobanServer`, `TransactionBuilder`, `Account`,
  `Network`, `xdr`), `serpent.testing._scval`, `serpent.testing.RealEnv` +
  `RealStorage.set` (seeding), `serpent.emitter.build_file`, `serpent.env`.
- Produces:

```python
TESTNET_RPC = "https://soroban-testnet.stellar.org"
TESTNET_PASSPHRASE = "Test SDF Network ; September 2015"
RECORD_ENV_VAR = "SERPENT_TESTNET_RECORD"          # "1" enables network recording
SOURCE_ENV_VAR = "SERPENT_TESTNET_SOURCE"          # OVERRIDE only: simulation accepts a never-funded
                                                  # source (probe, E14 as amended); the default is
DEFAULT_SOURCE = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"   # the zero public key

@dataclass(frozen=True)
class Fixture:
    contract_id: str; wasm_sha256: str; protocol: int; ledger: int; rpc_version: str
    recorded_at: str; method: str; args_xdr: tuple[str, ...]      # base64
    seeded: tuple[SeededEntry, ...]   # (durability, key_xdr b64, value_xdr b64, live_until) for every
                                      # PLAIN contract-data entry the footprint read (keys decoded with
                                      # decode_loose -- the pin key is a UNION, review M4)
    instance: tuple[tuple[str, str], ...]   # the contract INSTANCE entry's storage map as (key_xdr,
                                      # value_xdr) b64 pairs -- SHAPE lives here (M4); seeded via
                                      # RealStorage("instance").set / tier-1 instance().set
    result: FixtureResult             # ok: value_xdr b64 | error: (error_type, code)
    events_xdr: tuple[str, ...]

def simulate(*, server: SorobanServer, source_account: str, contract_id: str, method: str,
             args: Sequence[object]) -> SimulateTransactionResponse
def record_fixture(*, server, source_account, contract_id, wasm_sha256, method, args, out: Path) -> Fixture
def load_fixture(path: Path) -> Fixture
def fixtures_under(directory: Path) -> list[Fixture]
```

- [ ] **Step 1: The runner module** — `simulate` builds
  `TransactionBuilder(Account(source_account, 0), TESTNET_PASSPHRASE, base_fee=100)
  .append_invoke_contract_function_op(contract_id, method, [encode(a) for a in args])
  .set_timeout(300).build()` and calls `server.simulate_transaction(tx)`;
  `record_fixture` reads `response.results[0].xdr` (the return ScVal) or
  `response.error` (an error fixture is VALID and valuable: the deployed
  contract's `area` traps on chain today, B1 — record it as `error` with the
  diagnostic text), decodes `response.transaction_data` to get the footprint
  (`SorobanTransactionData.resources.footprint.read_only/read_write`), fetches
  every `LedgerKey` of type `CONTRACT_DATA` for this contract via
  `server.get_ledger_entries(keys)`; the entry whose key is
  `SCV_LEDGER_KEY_CONTRACT_INSTANCE` is stored as `instance` (its
  `ScVal::ContractInstance.storage` map → pairs), every other as a
  `SeededEntry`; `CONTRACT_CODE` keys are the module itself and are not
  state; header fields from `server.get_network()`
  (`protocol_version`) and `server.get_version_info()`; writes JSON. The
  module contains NO `Keypair`, no `sign`, no `send_transaction` — a test
  asserts by AST that none of those names appear (F.1.9).

- [ ] **Step 2: The replay test**

```python
"""Tier 3: recorded testnet simulations replayed against tier 1 and the real host (U3/K6/K7).

No network. Each fixture under fixtures/testnet/shapes/ was recorded by
`record_fixture` (SERPENT_TESTNET_RECORD=1 + an existing account's public key,
controller-run per ruling E14) against the deployed shapes contract. Replay:
seed the fixture's footprint entries into a fresh RealEnv (and into a tier-1
Env), invoke, compare the three answers. The header test needs no extension.
"""

FIXTURES = fixtures_under(Path(__file__).parent / "fixtures" / "testnet" / "shapes")


DEPLOYED = Path(__file__).parent / "fixtures" / "testnet" / "shapes" / "deployed.wasm"
DEPLOYED_SHA256 = "6a9dd13549bac20f2609ab3d74668963b5249a7943dc7f027cdf6c42bec86e33"


def test_the_fixtures_were_recorded_against_the_deployed_bytes() -> None:
    assert FIXTURES, "no fixtures recorded"
    assert hashlib.sha256(DEPLOYED.read_bytes()).hexdigest() == DEPLOYED_SHA256
    for f in FIXTURES:
        assert f.contract_id == "CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW"
        assert f.wasm_sha256 == DEPLOYED_SHA256
        assert f.protocol == DEFAULT_PROTOCOL


def test_this_trees_shapes_build_differs_from_the_deployed_bytes_until_the_next_deploy() -> None:
    """B1: Task 0 changed the Symbol-compare lowering, so HEAD's shapes.py no longer
    builds the deployed bytes. This inverts when Elliot approves the M1-end deployment
    (G): flip the assertion then and retire this docstring."""
    built = build_file(EXAMPLE_SHAPES).wasm
    assert hashlib.sha256(built).hexdigest() != DEPLOYED_SHA256


@pytest.mark.real_host
@pytest.mark.parametrize("fixture", FIXTURES, ids=[f.method for f in FIXTURES])
def test_the_real_host_and_tier_1_agree_with_testnet(fixture: Fixture) -> None:
    """Same bytes: the real leg runs the DEPLOYED wasm, not HEAD's build (B1). Tier 1
    runs HEAD's model. Three answers, compared -- for `area` the deployed bytes TRAP on
    both the chain and the real host (the B1 bug, reproduced), while tier 1 answers;
    that row is a declared divergence with the B1 reason, retired at the next deploy."""
    shapes = load_example(EXAMPLE_SHAPES)
    real = RealEnv(sequence=fixture.ledger)
    c = real.deploy_wasm(DEPLOYED.read_bytes())
    for key_xdr, value_xdr in fixture.instance:
        c.storage("instance").set(decode_loose_xdr(key_xdr), decode_loose_xdr(value_xdr))
    for e in fixture.seeded:
        c.storage(e.durability).set(decode_loose_xdr(e.key_xdr), decode_loose_xdr(e.value_xdr))
    args = [from_xdr(b64decode(a), t) for a, t in zip(fixture.args_xdr, _param_types(shapes.Drawing, fixture.method))]
    real_answer = _outcome(lambda: c.invoke(fixture.method, *args))
    env = Env(sequence=fixture.ledger); inst = deploy(shapes.Drawing, env)
    with env.frame():
        for e in fixture.seeded:
            _tier1_bucket(env, e.durability).set(<decoded key>, <decoded value>)
        tier1_answer = _outcome(lambda: getattr(inst, fixture.method)(env, *args))
    testnet_answer = _decode_result(fixture, _return_ty(shapes.Drawing, fixture.method))
    assert real_answer == tier1_answer == testnet_answer
```

(Helpers this test defines, each a few lines: `decode_loose_xdr(b64)` =
`decode_loose(SCVal.from_xdr_bytes(b64decode(b64)))` — the union pin key and
the `Shape`/`Color` values arrive as Vec/U32 (D6-coarse) on BOTH seeding
legs, which is exactly the bare word the host stores, so re-typing happens
in the contract's own `get(..., ty)` as on chain; `_outcome` as in Task 7;
`_param_types(cls, method)` = `typing.get_type_hints(getattr(cls, method))`
minus `return` and `env`, in signature order; `_return_ty(cls, method)` = that
map's `return` (or `type(None)`); `_tier1_bucket(env, durability)` =
`env.storage().persistent()` / `.temporary()` / `.instance()`;
`_decode_result(fixture, ty)` = `from_xdr(b64decode(fixture.result.value_xdr), ty)`
when `ok`, else `("error", fixture.result.code)`. Key/value types for seeding: the shapes contract's storage schema is known
from `examples/shapes.py` — `SHAPE`/`COLOR` Symbol keys under persistent
with `Shape`/`Color` values, the pin under temporary; the implementer writes
`_key_ty`/`_val_ty` as a small table keyed by durability + decoded key
symbol, stated in the test. Tier-1 seeding uses `env.storage().persistent()
.set(...)` inside the frame, exactly what a contract method does.)

- [ ] **Step 3: RECORD the fixtures (needs network only)** —
  `SERPENT_TESTNET_RECORD=1 uv run --no-sync python -m serpent.testing.testnet record --contract CDEU7Q… --out tests/real_host/fixtures/testnet/shapes kind area palette is_pinned`
  (a tiny `__main__` in `testnet.py`). E14 as amended: simulation accepts a
  never-funded source, so `DEFAULT_SOURCE` is used and no account is asked
  for; the implementer does NOT call friendbot and the module has no signing
  path. Also fetch `deployed.wasm` (`stellar contract fetch --id CDEU7Q… --network testnet --out-file …`)
  and assert its sha256 before committing. Commit the JSON + wasm with the
  README stating the recording date, ledger, protocol, and command.

- [ ] **Step 4: Run**

Run: `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q tests/real_host/test_testnet_fixtures.py`
Expected: header test PASS everywhere; the three-way compare PASS for every
recorded method. A three-way DISAGREEMENT where testnet ≠ real host is
F.1.4's first hypothesis (28.0.0 vs 28.0.2): BLOCKED with the diff.

- [ ] **Step 5: Gates, then commit**

```bash
git add src/serpent/testing/testnet.py tests/real_host/test_testnet_fixtures.py tests/real_host/fixtures
git commit -m "feat(testing): add the simulation-only testnet fixture runner and the shapes fixtures"
```

---

### Task 10: Docs, the promise sweep, the spec note (O33, S10, D5)

**Files:**
- Create: `docs/testing.md`
- Modify: `README.md` (a "Testing" section), `src/serpent/env.py` (the nine F
  mentions), `src/serpent/errors.py:30-44`, `tests/unit/test_env_ttl.py:339-357`
  (skip reasons name the HOST_FACTS row), `tests/unit/test_no_stale_promises.py`
  (a third net: "sub-plan F"), `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`
  (§13 network line: a dated correction note, not a rewrite)

- [ ] **Step 1: The third net.** In `test_no_stale_promises.py` add the F net
  with ALL THREE O33 needles (`"sub-plan f"`, `"tier 2b"`/`"tier-2b"`, and
  the possessive `"f's"` as a word) walked over `src/`, `tests/`,
  `examples/`, and `docs/` (widen `_WALKED` for this net — review m5);
  every mention must either read as a past-tense/record sentence in a
  TEXT-keyed allowlist (`frozenset[tuple[str, str]]` of `(relative path,
  exact line text stripped)`) or be absent. Run it FIRST to get the census
  (probe: 44 "sub-plan F" + 27 "tier 2b" hits in `src`+`tests` alone), then
  Step 2 makes each one IMPLEMENTED / REPOINTED / REMOVED.

- [ ] **Step 2: The sweep.** `env.py` 6, 14, 89-91, 605, 751, 1091-1096, 1190,
  1216, 1519: each becomes "proven on the real host: `tests/real_host/
  test_host_facts_real.py::<row>`" / "a declared divergence (`host_diverges`,
  `tests/semantics/env_scenarios.py`)" / "not modelled at tier 1; observed on
  the real host in HOST_FACTS row <name>". `errors.py:30-44`: the 128-bit
  `//0` paragraph names `DIV128_BY_ZERO_HOST_ERROR`. `test_env_ttl.py`'s two
  skips keep skipping (tier 1 still does not model clamp/trap) with reasons
  naming the two HOST_FACTS rows that prove them. `examples/shapes.py:95-101`
  (the harness-limitation paragraph): now "the mini host cannot decode a
  union/enum return; the real host leg does (`test_examples_real.py`)".

- [ ] **Step 3: `docs/testing.md`** — sections: the four tiers as they exist now
  (with the honest sentence per tier: tier 1 is a model; 2a is a mock with the
  named gaps; 2b is the real host; 3 is recorded simulation); writing a
  contract test with `serpent.testing.RealEnv` (a 20-line example against
  `examples/counter.py`); building the extension (the one command, the three
  traps, `--no-sync`); the `real_host` marker and `SERPENT_REQUIRE_REAL_HOST`;
  the divergence vocabulary (`mini_host_gap`, `host_diverges`,
  `FrozenTableDisagreement`, `HOST_FACTS`); recording tier-3 fixtures (what it
  needs, what it never does); version pins and what a protocol bump touches.
  README gets a five-line "Testing" section linking it.

- [ ] **Step 4: Spec §13** — under the "Networks (2026-08-26)" bullet add:
  "*(Correction 2026-09-xx, M1-F:)* testnet moved to protocol 28 before
  2026-09-02 (core 28.0.1); mainnet stayed on 27. The embedded test host
  tracks testnet (ruling U4/E11)."

- [ ] **Step 5: Run the whole suite both ways** — `uv run pytest -q` and
  `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q`; the four gates;
  `uv run pytest -q tests/unit/test_no_stale_promises.py` shows the F net
  green with its allowlist reviewed line by line in the commit message.

- [ ] **Step 6: Commit**

```bash
git add docs/testing.md README.md src/serpent/env.py src/serpent/errors.py tests/unit/test_env_ttl.py tests/unit/test_no_stale_promises.py examples/shapes.py docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md
git commit -m "docs: repoint every sub-plan F promise at its real-host evidence and document the testing tiers"
```

(Probe-verified, review m6: lines 95-101 of `examples/shapes.py` are inside
the MODULE docstring, which `build_spec_entries` never emits — rewriting them
leaves the bytes identical (4,171 bytes, same hash). Method docstrings ARE
emitted; do not touch those.)

---

## Completion (process, not tasks)

1. **Final whole-branch review on Fable**, fed
   `.superpowers/sdd/2026-09-02-m1f-testing-tiers/final-review-attention.md`
   accumulated from Task 1 onward and reconciled against the ledger. It must
   carry: every host fact pinned FROM A FIRST RUN (the
   `EXPECTED_UNDERLYING_ERROR` map, every `HostErr` underlying pair, every
   `COMPARE_VECTORS` sign, the `max_ttl()` value) with the run date; every
   `FrozenTableDisagreement` escalation and its ruling; every `host_diverges`
   and `chain_unproven` declaration (archival: the test host does not model
   it either, M3); the P4 discrimination matrix and the fact that the frame
   level is one bit wide (B5); the containment evidence (three panic
   sources); the `mini_host_gap` rename's blast radius; the `_ADMIN` strkey
   change (B2) and the contract-authorizers-only fence; the two
   private-accessor seams `_scval` uses (`_payload_items`, `_METADATA_ATTR`);
   Task 0's lowering change with the goldens' diff summary and the new
   runtime part; that the sdk test Env installs
   `InvocationResourceLimits::mainnet()` (review m13 — a first hypothesis
   when a row fails on limits); the tier-3 recording date and the
   `deployed.wasm` artifact; the 28.0.0-vs-28.0.2 delta as a standing caveat
   on every tier-3 comparison.
2. **Obligations carried OUT of M1-F**: to **G** — the CI Rust job
   (`SERPENT_REQUIRE_REAL_HOST=1`, maturin build, cargo gates), `stellar
   serpent doctor` reporting the extension's presence and protocol,
   O32's frontend hygiene items (E13), the wasm-tools CI pin drift watch
   (O30; `ci.yml`'s own comment already asks for it), the deploy-gate example
   choice (one of six; Elliot approves), retiring `spikes/` (R3: the
   harnesses are now superseded). To **M3** — prebuilt `serpent-host` wheels (platform × Python,
   ONE protocol per release, U1), the sdk 28.0.0 stable bump, the live tier-3
   suite. To **M2** — tier-1 frame rollback (retiring the publish-then-raise
   `host_diverges`), tier-1 container ordering if E12 recorded a non-trivial
   order, key-level footprint via `e2e_invoke`, `get_max_live_until_ledger`
   at tier 1 (the clamp/trap model), archival/restore modelling (M3: the
   test host does not model it either — chain-only until then), account
   authorizers on the real host (B2: needs real ed25519 signing), O6 (the
   mini host's container `obj_cmp`) and O20 (struct↔`Map` tag-level
   acceptance) with the host's `COMPARE_VECTORS` evidence in hand, threads /
   free-threaded CPython for `unsendable`.
3. **One fix wave**, then local merge to main. **No pushes (hard stop).**
   process.md's state section gains the M1-F line and "NEXT: G". The
   M1-end testnet deployment remains G's and requires Elliot's explicit
   in-session approval — and it now also REPLACES the deployed shapes
   contract whose `area` traps (B1); until then `deployed.wasm` is the
   tier-3 truth and Task 9's "differs from HEAD" assertion stands.
