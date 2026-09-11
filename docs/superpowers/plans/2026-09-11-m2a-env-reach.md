# M2-A: Env reach (the leaf host surface) -- Implementation Plan

> **v2 (2026-09-11)** after the adversarial plan review (`.superpowers/sdd/2026-09-11-m2a-env-reach/plan-review.md`: 4 blockers, 14 majors, 15 minors, ALL adopted; rulings in decisions.md "2026-09-11 M2-A plan-review rulings"). Every finding is folded into the task text below; the review's finding ids are cited inline as `[B1]`, `[M3]`, `[m7]` where a step changed because of one.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the leaf half of M2's Env surface -- `env.current_contract_address()`, three `env.ledger()` accessors, five non-ZK crypto functions, the four PRNG functions, `env.logs().add`, and the two `String`/`Bytes` conversions -- at tier 1, at tier 2a, in the compiler, in the emitter, and against the real host, with a pure-Python oracle that reproduces the host's own vectors bit for bit, plus the ninth example `examples/raffle.py`.

**Architecture:** Two new private core modules (`serpent/_crypto.py`, `serpent/_prng.py`) hold every primitive, shared by the tier-1 `Env` AND by `tests/harness/hostfns.py` so the two oracles cannot disagree by construction (S6, D1 of the rulings). `serpent/env.py` grows four ledger-shaped constants and three thin facade classes (`Crypto`, `Prng`, `Logs`) over those modules; the compiler gains `RECOGNIZED` rows and recognizer arms, no new IR node; the emitter gains exactly one new branch (a raw-scalar return must be BOXED, C7a) and one new lowering (`log_from_linear_memory`, the `_lower_make_struct` recipe). Evidence is `ENV_SCENARIOS` rows, `HOST_FACTS` rows, and one dedicated three-tier PRNG stream differential.

**Tech Stack:** Python >= 3.11 stdlib only in the core (`hashlib`, `hmac`, `struct`); the pinned `soroban-env-host` 28.0.2 / `soroban-sdk` 28.0.0-rc.1 embedded host behind `host/` (PyO3 0.29, maturin); wasmtime-py for the tier-2a mini host; pytest, ruff, mypy --strict; mkdocs-material for the docs page.

**Spec:** `docs/superpowers/specs/2026-09-11-m2a-inputs-dossier.md` (the M2-A dossier; this plan cites its IDs -- S#, R#, D#, O#, K#, C#, E#, F# -- inline and argues from them, never from memory). Rulings: `docs/superpowers/decisions.md`, entry "2026-09-11 M2-A rulings (dossier E1-E13, all recommendations adopted, four sharpened)". Process: `docs/superpowers/process.md`.

---

## Global Constraints

- **Zero-dep core** (R3, C4, S12): `src/serpent/_crypto.py` and `src/serpent/_prng.py` are CORE modules under `tests/unit/test_core_zero_dep.py`'s static `ast` walk. They are NOT in `EXEMPT` and must never be: stdlib only, and only `hashlib`, `hmac`, and `struct` (all three are in `sys.stdlib_module_names`, verified 2026-09-11). **No third-party crypto library is available at any price** -- not as an extra, not as an optional import, not behind a `try/except ImportError`. An implementer who believes one is needed returns BLOCKED.
- **`serpent.__all__` is frozen at 41 names** after Task 2 (D4/E4): the single new name is `Bytes65`. `tests/unit/test_public_api.py` is edited in Task 2 and in NO other task. The primitives modules lead with an underscore (`_crypto`, `_prng`, following `_strkey.py`/`_frame.py`, C15) and never join `__all__`.
- **Registry discipline** (D2, E12): `src/serpent/compiler/codes.py` is edited in **Task 4 only**, and ONLY for these enumerated changes -- four edits and one deliberate non-edit:
  1. ADD **SPT1040** -- "a log message must be a string literal; it is interned into the module's data segment at build time";
  2. **SPT1041 is NOT added.** The ruling allocates it conditionally -- "used ONLY if the plan shows the SPT3018 reduction is dishonest for an untyped literal; otherwise it is allocated-unused and joins `NO_FIXTURE_ALLOWLIST` with a reason, or is not added at all -- the plan states which". The plan states: **not added at all**. Measured 2026-09-11 against this checkout, an untyped literal in an existing value-position slot (`env.events().publish((Symbol('a'),), 1)`) already draws **SPT3008** -- "an int literal has no chain type in this position", help "wrap it in a chain type, e.g. U32(1) or I128(1)" -- which is the honest code for that shape and needs no reduction to SPT3018 at all. Every other log-value mistake is covered: a bucket in a value position is SPT1038, an unknown name is SPT2001/SPT2006, a wrong arity is SPT3020. A code with no uncovered shape must not be allocated (D2 is append-only; an unused published code can never be withdrawn). See Task 4 Step 2 for the enumeration;
  3. NARROW **SPT1033**'s construct list and intent by sanctioned wording to B's three names (`env.call()`, `env.try_call()`, `env.deployer()`);
  4. WIDEN **SPT1034**'s construct list by sanctioned wording to name `env.prng().shuffle`;
  5. CORRECT `NO_FIXTURE_REASONS["SPT6001"]`'s now-false clause (E8, C13).
  Snapshot pins and `docs/subset.md` regenerate in the SAME commit. Any other apparent need to touch `codes.py` -- a renumber, a delete, a meaning reversal, a second new code -- is **BLOCKED**, not a judgment call.
- **`decisions.md`, `spikes/`, `sandbox/counter.py`, and `sandbox/hello_world.py` are never edited by an implementer** (process.md; C7 of the G dossier). `docs/superpowers/specs/` is likewise frozen input.
- **The four gates on every task**, non-negotiable, always `--no-sync` (the D10 prune trap: `uv sync` removes the maturin-built `serpent_host` from `.venv`):
  ```
  uv run --no-sync ruff check .
  uv run --no-sync ruff format --check src tests examples
  uv run --no-sync mypy --strict
  SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q
  ```
  **Baseline at main 314c5ed: 4792 passed / 7 skipped** with `SERPENT_REQUIRE_REAL_HOST=1`. Every task reports its own passed/skipped counts against that baseline.
- **The Rust gate** on any task that touches `host/`, from the `host/` directory:
  ```
  cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test
  ```
  followed by a rebuild into `.venv` and the import check:
  ```
  VIRTUAL_ENV=$PWD/.venv uvx maturin develop --release --manifest-path host/Cargo.toml
  uv run --no-sync python -c "import serpent_host; print(serpent_host.__file__)"
  ```
- **`mkdocs build --strict`** on any task that touches `docs/` or `mkdocs.yml`:
  ```
  uv run --no-sync mkdocs build --strict
  ```
- **THE BYTE FREEZE** (R4, D9, C27, F9). No edit may move an emitted byte of `examples/shapes.py` or `examples/bounty_board.py`. These two node ids run as an **explicit, quoted gate step** on every task that touches anything under `src/serpent/emitter/` or `src/serpent/compiler/` -- Tasks 4, 5, 6, 7, and the close -- not only at the end:
  ```
  SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q \
    "tests/real_host/test_testnet_fixtures.py::test_this_trees_build_equals_the_deployed_bytes[shapes]" \
    "tests/real_host/test_testnet_fixtures.py::test_this_trees_build_equals_the_deployed_bytes[bounty_board]" \
    "tests/real_host/test_testnet_fixtures.py::test_the_fixtures_were_recorded_against_the_deployed_bytes[shapes]" \
    "tests/real_host/test_testnet_fixtures.py::test_the_fixtures_were_recorded_against_the_deployed_bytes[bounty_board]"
  ```
  Expected: `4 passed`. A byte move is a **controller decision** (D9), never a fix-wave edit: the implementer reports BLOCKED with the two sha256 digests it produced and the two it was compared against.
- **No pushes, no publishes, no deployments, no writes outside the repository.** No `uv tool install`, no `pip install --user`, nothing under `$HOME`. A read-only RPC call is not a hard stop (D1) but no task in A needs one.
- **Commit style**: conventional commits, imperative mood, subject <= 72 characters, no emoji, no em dashes, no en dashes, Oxford commas in any list of three or more. **Every commit in M2-A is made with `git commit --no-gpg-sign`** (R8): `commit.gpgsign` is locally false and `.git/hooks/post-commit` logs the sha automatically -- the implementer **does NOT edit `.git/unsigned-commits.log` by hand**. Every commit body ends with an attribution trailer, and **the implementer appends its own harness attribution trailer** -- the trailer names the model that authored the commit (decisions.md 2026-09-11) -- rather than a literal copied out of this plan [M14]. The plan seats three different models across its 13 tasks, so a single hardcoded name would attribute code to a model that did not write it. The `git commit -m` blocks below omit the trailer and the flag for brevity; the implementer adds both, every time.
- **Determinism**: every new golden, snapshot, and pinned vector renders from fixed inputs. No `time`, no `random`, no `os.urandom`, no network, no filesystem state outside the repo, anywhere in `src/serpent/` or in a test's expected value. The tier-1 PRNG's pre-reseed seed is a DOCUMENTED constant (E2), never entropy.
- **What A does NOT do** (scope fences, each with its owner):
  - `env.py` is **not** promoted to a package (E9; that is row E, R5).
  - The TTL **clamp**, the TTL **trap**, and the per-bucket **minimum floors** stay in **M2-D** (E10, R6). A lands the `max_live_until_ledger` ACCESSOR and the `max_entry_ttl` constant and nothing more. The three `Unmodelled(_NO_MAXIMUM)` rows in `host_facts.py` are **re-worded, never re-classified** (F15).
  - `env.call()`, `env.try_call()`, and `env.deployer()` stay deferred; **SPT1033 is NOT retired** (E12), it is narrowed.
  - bls12-381, bn254, and poseidon are **M3-or-later** (O16, R1). No row, no binding, no docstring promise.
  - Tier-1 frame rollback is **B**'s (O7). A's `Prng` frame reset is the only frame-scoped state A adds; Task 3 documents it for B to fold in.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `src/serpent/_crypto.py` | keccak-256, ed25519 `verify_strict`, a shared short-Weierstrass curve, secp256k1 recovery, P-256 prehash verify. Stdlib only. Shared by tier 1 AND the mini host (D-1, E1). |
| `src/serpent/_prng.py` | The HMAC-SHA256 unbias with the protocol salt, ChaCha20, `BlockRng` geometry, rand 0.8.8's two sampler arms, Fisher-Yates. Stdlib only (D-1, E2). |
| `tests/unit/test_crypto_primitives.py` | K20's vendored vectors plus F1/F2/F3/F4's negatives, against `_crypto` alone. |
| `tests/unit/test_prng_primitives.py` | K15's three sdk doctest vectors plus the geometry and sampler-arm tests, against `_prng` alone. |
| `tests/unit/test_env_leaf_surface.py` | Tier 1: the four new `Env` facts, `Ledger.version/network_id/max_live_until_ledger`, `Env.current_contract_address()`, `Bytes65`, the two conversions and the `surrogateescape` round trip. |
| `tests/unit/test_env_crypto_prng_logs.py` | Tier 1: the `Crypto`/`Prng`/`Logs` facades, `CryptoTrap`, `PrngTrap`, the frame-local PRNG lifetime, the log no-trap rule. |
| `tests/must_reject/constructs/log_message_not_a_literal.py` | SPT1040's fixture. |
| `tests/must_reject/constructs/shuffle_result_discarded.py` | The SPT1034 widening's fixture. |
| `tests/must_reject/types/ed25519_signature_wrong_length.py` | The SPT3018 reduction's fixture (a `Bytes32` in the signature slot). |
| `tests/fixtures/crypto_surface.py` | The contract the four signature/hash surfaces the raffle does not use are exercised through (E13): `keccak256`, `ed25519_verify`, `secp256k1_recover`, `secp256r1_verify`, plus both conversions. |
| `tests/unit/test_emitter_leaf_calls.py` | Emitter unit tests for the fifteen plain calls, including the raw-return boxing branch (C7a/F16). |
| `tests/unit/test_emitter_log.py` | Emitter unit tests for `log_from_linear_memory`: the interned message, the scratch accounting (F8), the memory-export assertion, SPT8002/SPT8003. |
| `tests/semantics/prng_vectors.py` | `PRNG_VECTORS`: the reseeded-stream corpus every tier replays (E11.1), importable like `ENV_SCENARIOS` is (D7). |
| `tests/unit/test_prng_differential.py` | Tier 1 vs tier 2a over `PRNG_VECTORS`. |
| `tests/real_host/test_prng_real.py` | The real leg of `PRNG_VECTORS` plus the base-seed probe that shows why unseeded draws are not pinned (E11.4). |
| `examples/raffle.py` | The ninth example: the host authors' own commit-reveal construction (E13, K13). |
| `tests/goldens/wasm/raffle.wat.txt` | The ninth example's disassembly snapshot. |
| `tests/unit/test_example_raffle.py` | The raffle at tier 1. |
| `tests/real_host/test_example_raffle_real.py` | The raffle on the real host. |
| `docs/examples/raffle.md` | The raffle's docs page. |

**Modified**

| File | Change |
|---|---|
| `src/serpent/types/buffers.py:32-70, 180-237` | `Bytes65` (`:184`-`:192` neighbourhood); `_BYTES_N_CACHE` (`:204`) gains 65; `bytes_n`'s stale "sub-plan C" sentence (`:214`) corrected (E4); `String` accepts and orders `surrogateescape` text -- `String.__init__` is at `:45`, inside the class at `:32`, NOT in the `:180-237` block [m1]; `buffers.py:37`'s docstring ("a lone surrogate has no on-chain representation") is corrected too, since after the change that is true only of the HIGH-surrogate half. |
| `src/serpent/__init__.py:70-132` | `Bytes65` imported and added to `__all__` (41 names). |
| `tests/unit/test_public_api.py:30-70` | `EXPECTED_ALL` gains `Bytes65`. Edited in Task 2 ONLY. |
| `src/serpent/env.py:215-242, 1346-1600` | Four `DEFAULT_*` constants; `Ledger`'s three new readers; `Env.__init__`'s four keywords; `Env.current_contract_address()`; `Crypto`/`Prng`/`Logs` and their accessors; `CryptoTrap`/`PrngTrap` (siblings of the existing `StorageTrap` at `env.py:642`; the ruling takes the review's TUPLE option, so NO shared base class is added) [B1]; the frame PRNG **save/restore** in `_invocation` -- which is at `:1568`, NOT inside `:1346-1560` (`:1560` lands mid-docstring of `frame()`) [m2] [M3]; `recorded_logs`. `__all__` is at `:215-227` and gains `Crypto`, `Prng`, `Logs`, `CryptoTrap`, `PrngTrap` -- the three facade classes as well as the two traps, because `Events`, `Ledger`, `Storage`, and the three bucket classes are already there and Task 12 adds mkdocstrings entries for all three [m10]. |
| `src/serpent/testing/_real.py:60-82, 112-165` | Imports `DEFAULT_NETWORK_ID`/`DEFAULT_MAX_ENTRY_TTL`/`DEFAULT_PROTOCOL_VERSION` from `serpent.env` instead of restating them (E5); `network_id()`, `sequence_number()`, `set_base_prng_seed()` wrappers. |
| `src/serpent/testing/_scval.py:107-111, 518-519` | `String` marshals through `surrogateescape` in both directions (E7). |
| `src/serpent/compiler/recognize.py` | 15 `RECOGNIZED` rows; `_recognize_ledger_method` grows three arms and loses `_LEDGER_FUTURE_METHODS`; `_recognize_crypto_method`, `_recognize_prng_method`, `_recognize_logs_method`, `_recognize_current_contract_address`; `_CORE_ENV_SURFACES` grows to six names, which also moves `_NO_ARG_CHAIN_STEPS` (`:860`), the hand-written user-facing `_CHAIN_HELP` (`:862-867`), and the chain-link walk (`:941`) [m9]; `_recognize_env_top_level` gains a FOURTH arm for the bare `current_contract_address` attribute (SPT1038, `:840-846`'s fallback would otherwise answer SPT2006) [M6]; `KNOWN_FUTURE_ENV_NAMES` shrinks to three; the two conversion rows; `_HELP` for SPT1040 and a reworded `_HELP["SPT1033"]` (`:244`, still says "deferred to M2", stale now that M2 has started) [m8]; `src/serpent/env.py:69` and `recognize.py:52-55` both describe the deferred list in prose and both go stale [M1]. |
| `src/serpent/compiler/codes.py` | The FOUR sanctioned edits (Task 4 ONLY). |
| `src/serpent/compiler/frontend.py:197-213, 626-665, 904-946` | `log_from_linear_memory` joins `_LINEAR_MEMORY_HOST_FNS`; `_needs_memory` is unchanged in shape (the `used & _LINEAR_MEMORY_HOST_FNS` row answers it) and gains a test. AND `_collect_host_fns` (`:662-663`) EXEMPTS the log message's `Const`, mirroring the `gets_covered_by_a_has` exemption at `:630-636` -- without it every logging contract certainly-reports `string_new_from_linear_memory`, a host function the emitter never imports [M2]. |
| `src/serpent/emitter/lower.py:1348-1366` | The `val_typed_ret is False` boxing branch; `_lower_log`. |
| `tests/unit/test_recognize_env.py:273-306, 451-456` | `_DOSSIER_C4_INVENTORY` grows to the enumerated 25 names. AND `test_ledger_future_method_is_m2_pointer` (`:451-452`, which pins `env.ledger().version()` as SPT1033) is REPLACED by a positive `test_ledger_version_is_u32` beside the existing `test_ledger_sequence_is_u32` (`:459`) -- it is not self-healing, unlike the two parametrized tests at `:599`/`:604` which iterate `KNOWN_FUTURE_ENV_NAMES` and shrink on their own. `test_ledger_unknown_method_is_unresolved` (`:455`) still holds and stays [M1]. |
| `tests/unit/test_containers_frontend.py:261-353` | `_EXPECTED_CONTAINER_ROWS` and `_DOSSIER_C4_CONTAINER_INVENTORY` gain the two conversions. |
| `tests/unit/test_diagnostics.py:256-350` | `range(1, 40)` -> `range(1, 41)`; `== 109` -> `== 110`, with the rationale paragraph. |
| `tests/must_reject/constructs/env_deferred_surface.py` | Repointed off `env.logs()` onto `env.deployer()`; its `serpent:message` follows SPT1033's narrowed intent (F12). |
| `docs/subset.md` | Regenerated (Task 4, and once more at the close). |
| `tests/harness/hostfns.py` | **16** new bindings, **six** new attributes plus the private `_prng_state`, the log reader and `logs` list [m7]. The three hash/id bindings return `Bytes32(...)` and the recovery returns `Bytes65(...)`, matching the facade's classes so the differential's `answer_type` compares equal [B3]. |
| `tests/harness/objects.py` | Nothing (the log reader lives in `hostfns.py` beside `contract_event`). |
| `tests/unit/test_harness_hostfns.py:868-976, 1000-1022` | `_EMITTER_ADDITIONS` unchanged; `_FIXTURES` gains the raffle and `crypto_surface`. |
| `host/src/lib.rs`, `host/serpent_host.pyi` | `network_id()`, `sequence_number()`, `set_base_prng_seed()`. |
| `tests/semantics/env_scenarios.py` | `CRYPTO_SURFACE` and `RAFFLE`-shaped rows for every A surface, incl. the two `host_error` PRNG rows and the unseeded `host_diverges` row. |
| `tests/unit/test_env_differential.py:231-241, 274-283, 302-308` | **The differential runner itself** -- unnamed in v1 and the module that owns both non-real legs [B1] [B2] [B3]. The tier-1 `host_error` arm widens from the literal `StorageTrap` to the tuple `(StorageTrap, CryptoTrap, PrngTrap)`; `_wasm` gains its first `host_error` arm; `_DECODABLE` gains `Bytes`. |
| `tests/semantics/host_facts.py:161-244` | `_NO_MAXIMUM` reworded to name M2-D (three rows, re-worded NOT re-classified); new rows for keccak, the reseeded stream, and `max_live_until`. |
| `tests/fixtures/host_facts.py` | The methods the new `HOST_FACTS` rows invoke. |
| `tests/real_host/test_host_facts_real.py:263-272` | The `Unmodelled`-row pin keeps naming exactly the same three rows. |
| `tests/unit/test_emitter_end_to_end.py:102-121, 684-694` | `EXAMPLE_RAFFLE` joins `EXAMPLES` and `CONSTRUCTOR_BEARING`. |
| `tests/unit/test_emitter_printer.py:382-396` | `FIXTURE_SOURCES` gains the raffle. |
| `tests/unit/test_frontend_fuzz.py` | `CORPUS` gains the raffle. |
| `tests/unit/test_examples.py:135-159` | The prose naming the protocol-22 examples gains the raffle, AND -- the enforcing half -- the `has_constructor` set at `:154-159` gains `"raffle"`. The prose alone does not enforce anything; `test_every_example_compiles[raffle]` fails until that set is edited [M7]. |
| `tests/unit/test_docs_site.py:70-81` | The strict build asserts the raffle's page too. |
| `tests/unit/test_no_stale_promises.py` | A FIFTH net for A's own forward references (`sub-plan A` and `M2-A`; `\bA's\b` is NOT used, and `docs/subset.md` is EXCLUDED from the walk because it is regenerated byte-for-byte from `codes.py`, which the net already walks [M13] -- see Task 12). |
| `docs/examples/index.md`, `docs/index.md`, `mkdocs.yml` | The ninth example joins the site (nothing tests the index tables: F10). |
| `docs/guides/*.md` (or `docs/getting-started.md`) | Authoring sections for crypto, PRNG, logging, and the conversions. |
| `.github/workflows/ci.yml:140-148` | `REAL_HOST_FLOOR` bumped to the newly measured floor. |

**Model seating** (process.md; the rulings' "T1-T8 Opus implementer + Opus review; T9-T11 Sonnet, except that any task touching a divergence guard or the emitter is Opus on both sides", mapped onto this plan's 13-task re-cut):

| Task | Implementer | Review | Why |
|---|---|---|---|
| 1 | Opus | Opus | This IS the oracle. A wrong stream is silently green everywhere else (S8). |
| 2 | Opus | Opus | Value-layer semantics (`String` widening) plus the tier-1 ledger model. |
| 3 | Opus | Opus | Oracle edit; the frame-local PRNG lifetime is a semantics decision. |
| 4 | Opus | Opus | The frozen registry plus diagnostics adjacency (D2/D3). |
| 5 | Opus | Opus | Emitter. |
| 6 | Opus | Opus | Emitter, and the one place A can ship a silent wrong answer (C7a/F16). |
| 7 | Opus | Opus | Emitter plus linear memory: S2/S3's named hazard. |
| 8 | Opus | Opus | Tier-2a oracle edit. |
| 9 | Opus | Opus | Touches `host/`, the tier-2b gate. |
| 10 | Opus | Opus | This is where "hollow" is caught or missed. |
| 11 | **Sonnet** | **Opus** | Mechanical once the surfaces exist (D11's pattern), but it produces a WAT golden and a real-host leg, so the REVIEW is seated up. **RULED (2026-09-11, B4): the seating STANDS.** The review argued for Opus on both sides because the raffle's construction was an unmade semantics call; the controller instead RULED the construction into the plan (Task 11 Step 1 now carries the full contract), which restores the "mechanical once the surfaces exist" premise the seating rests on [B4]. |
| 12 | Sonnet | Sonnet | Text, with one generated-file regen. |
| 13 | Sonnet | Sonnet | Mechanical. |

**Task order and dependency graph**

```
T1 (_crypto/_prng) ─┐
                    ├─> T3 (facades) ─> T4 (recognition + registry) ─> T5 ─> T6 ─> T7 ─> T8 ─┐
T2 (value + Env) ───┘                                                                        ├─> T10 ─> T11 ─> T12 ─> T13
                    └──────────────> T9 (testing + Rust facade) ────────────────────────────┘
```

- **T1 and T2 may run in PARALLEL**: T1 touches only the two new private modules and their tests; T2 touches only the value layer, `env.py`'s constants and `Ledger`, and `_real.py`'s imports. They share no file.
- **T9 may run in parallel with T4-T8**, once T2 has landed the constants it imports.
- Everything else is strictly sequential. T5 -> T6 -> T7 are three emitter tasks in one file (`lower.py`) and must not be parallelised.

---

### Task 1: `serpent/_prng.py` and `serpent/_crypto.py` -- the primitives, against the host's own vectors

**Files:**
- Create: `src/serpent/_prng.py`, `src/serpent/_crypto.py`, `tests/unit/test_prng_primitives.py`, `tests/unit/test_crypto_primitives.py`
- Modify: nothing. **This task touches no existing file.**
- Test: `tests/unit/test_prng_primitives.py`, `tests/unit/test_crypto_primitives.py`, `tests/unit/test_core_zero_dep.py` (unchanged, must stay green)

**Interfaces:**
- Produces `serpent._prng`: `UNBIAS_SALT: bytes`, `unbias_seed(seed: bytes) -> bytes`, `class ChaCha20Rng` with `__init__(self, key32: bytes) -> None`, `next_u32(self) -> int`, `next_u64(self) -> int`, `fill_bytes(self, n: int) -> bytes`; `from_prng_seed(seed32: bytes) -> ChaCha20Rng`; `u64_in_inclusive_range(rng: ChaCha20Rng, lo: int, hi: int) -> int`; `shuffle(rng: ChaCha20Rng, items: Sequence[_T]) -> list[_T]`; `SEED_BYTES = 32`.
  **`u64_in_inclusive_range`'s precondition is CHECKED, not documented** [M11]: `0 <= lo <= hi <= 2**64 - 1`, else `ValueError`. The host's arguments are `u64` so it cannot express the violation; the Python facade and `tests/harness/hostfns.py` both can, and before the guard the arithmetic wrapped silently.
- Produces `serpent._crypto`: `sha256(data: bytes) -> bytes`, `keccak256(data: bytes) -> bytes`, `ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool`, `secp256k1_recover(digest: bytes, signature: bytes, recovery_id: int) -> bytes | None`, `secp256r1_verify(public_key: bytes, digest: bytes, signature: bytes) -> bool`, and the internals the tests pin: `edwards_decompress(data: bytes) -> tuple[int, int, int, int] | None`, `compress(point: tuple[int, int, int, int]) -> bytes` [M10], `edwards_equal(a, b) -> bool`, `ED25519_P`, `ED25519_L`, `SECP256K1`, `SECP256R1`.
  **`edwards_decompress` has NO `x == 0 && sign` refusal** [M9]: `curve25519-dalek-4.1.3/src/edwards.rs:194-234` has no such check -- `decompress()` returns `Some` iff `sqrt_ratio_i` reports a square, and step 2 does `X.conditional_negate(sign_bit)` with no rejection. v1 attributed the branch to dalek and it is not dalek's. The branch is DROPPED and the small-order screen does the work it was reaching for.
- Consumes: nothing from serpent. `hashlib`, `hmac`, `struct`, `collections.abc`, `typing` only.

- [ ] **Step 1: The PRNG vectors, failing first**

Create `tests/unit/test_prng_primitives.py`:

```python
"""`serpent._prng` against soroban-sdk's own pinned doctest vectors (K15).

The host's frame PRNG is ChaCha20 seeded by HMAC-SHA256 of the caller's 32
bytes under a protocol-level salt (K13.3), so after `prng_reseed(b)` every
draw is a pure function of `b` -- which is exactly what makes an
exactly-reproducing tier-1 PRNG possible, and what these vectors pin. Each
one is preceded in `soroban-sdk-28.0.0-rc.1/src/prng.rs` by
`env.prng().seed(Bytes::from_array(&env, &[1; 32]))`, so each starts from a
fresh stream.

A distribution test would prove nothing here: a wrong unbias, a wrong buffer
boundary, or the wrong sampler arm all produce a uniform-looking stream that
is not the host's (F5).
"""

from __future__ import annotations

import pytest

from serpent import _prng

#: soroban-sdk's own doctest seed.
SEED = bytes([1]) * 32

#: The unbiased ChaCha20 key for SEED, measured 2026-09-11 against the three
#: vectors below. Pinned separately so a broken HMAC step fails HERE, naming
#: the unbias, instead of three vectors away.
UNBIASED_KEY = bytes.fromhex("fa2778247d5a04abbb47831d29dbca9a9ba8034c429a09fc7fefb00bd5693c50")


def test_the_salt_is_the_public_network_id() -> None:
    """`src/crypto/mod.rs:456-486`: "Salt is fixed and must not be changed; it
    is effectively 'part of the protocol'." It is sha256 of the Stellar public
    network passphrase."""
    assert _prng.UNBIAS_SALT == bytes.fromhex(
        "7ac33997544e3175d266bd022439b22cdb16508c01163f26e5cb2a3e1045a979"
    )
    assert len(_prng.UNBIAS_SALT) == 32


def test_the_unbias_step_is_hmac_sha256_under_the_salt() -> None:
    assert _prng.unbias_seed(SEED) == UNBIASED_KEY


def test_a_seed_that_is_not_32_bytes_is_refused() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        _prng.unbias_seed(bytes(31))


def test_sdk_vector_gen_u64() -> None:
    """`env.prng().gen::<u64>()` == `prng_u64_in_inclusive_range(0, u64::MAX)`.

    The full range makes `range` wrap to 0, which rand 0.8.8's
    `UniformInt::<u64>::sample` answers with a bare `rng.gen::<u64>()` (K14).
    """
    rng = _prng.from_prng_seed(SEED)
    assert _prng.u64_in_inclusive_range(rng, 0, 2**64 - 1) == 8478755077819529274


def test_sdk_vector_bytes_new_32() -> None:
    """`env.prng().gen_len::<Bytes>(32)` == `prng_bytes_new(32)`."""
    rng = _prng.from_prng_seed(SEED)
    assert list(rng.fill_bytes(32)) == [
        58, 248, 248, 38, 210, 150, 170, 117, 122, 110, 9, 101, 244, 57, 221, 102,
        164, 48, 43, 104, 222, 229, 242, 29, 25, 148, 88, 204, 130, 148, 2, 66,
    ]  # fmt: skip


def test_sdk_vector_gen_range_1_to_100() -> None:
    """`env.prng().gen_range::<u64>(1..=100)`: the Lemire arm, with rejection."""
    rng = _prng.from_prng_seed(SEED)
    assert _prng.u64_in_inclusive_range(rng, 1, 100) == 46


def test_two_reseeds_with_the_same_seed_produce_the_same_draws() -> None:
    """What the host's own `prng_test` asserts (K20), at tier 1 (E11.2)."""
    first = _prng.from_prng_seed(SEED).fill_bytes(64)
    second = _prng.from_prng_seed(SEED).fill_bytes(64)
    assert first == second


def test_a_different_seed_produces_a_different_stream() -> None:
    other = bytes([2]) * 32
    assert _prng.from_prng_seed(SEED).fill_bytes(32) != _prng.from_prng_seed(other).fill_bytes(32)


def test_the_buffer_is_four_blocks_of_sixteen_words() -> None:
    """K14: `ChaCha20Rng` buffers 64 u32 words (`BUF_BLOCKS = 4`). The geometry
    is what makes `next_u64`'s boundary case reachable at all, so it is pinned
    rather than left implicit -- and it is pinned by CONSTRUCTION, not by a
    statistical smell test: 64 u32 draws must reproduce exactly the first 256
    bytes `fill_bytes` produces, which can only hold if both read the same
    buffer in the same order."""
    assert _prng.BUFFER_WORDS == 64
    assert _prng.BUF_BLOCKS == 4 and _prng.BLOCK_WORDS == 16
    by_word = _prng.from_prng_seed(SEED)
    words = b"".join(by_word.next_u32().to_bytes(4, "little") for _ in range(64))
    assert words == _prng.from_prng_seed(SEED).fill_bytes(256)


def test_next_u64_at_the_buffer_boundary_splits_across_two_buffers() -> None:
    """`rand_core-0.6.4/src/block.rs:197-218`'s documented split case: with the
    index at `len - 1`, `next_u64` takes the low word from the OLD buffer and
    the high word from a freshly generated one. Reproduced by drawing 63 u32s
    first, which is the only way to reach index 63."""
    a = _prng.from_prng_seed(SEED)
    low_words = [a.next_u32() for _ in range(63)]
    split = a.next_u64()

    b = _prng.from_prng_seed(SEED)
    for _ in range(63):
        b.next_u32()
    expected_low = b.next_u32()  # word 63 of buffer 0
    expected_high = b.next_u32()  # word 0 of buffer 1
    assert split == (expected_high << 32) | expected_low
    assert len(low_words) == 63


def test_shuffle_is_fisher_yates_over_the_u32_sampler_arm() -> None:
    """K14: `SliceRandom::shuffle` uses `gen_index`, which for a bound under
    `u32::MAX` is `gen_range(0..n as u32)` -- a DIFFERENT sampler arm from the
    u64 one, with an approximate rejection zone and one u32 drawn per attempt.
    Reusing the u64 sampler would give the right distribution and the wrong
    stream, so this test reproduces the arm by hand and compares."""
    items = list(range(10))
    shuffled = _prng.shuffle(_prng.from_prng_seed(SEED), items)
    assert sorted(shuffled) == items

    expected = list(items)
    rng = _prng.from_prng_seed(SEED)
    for i in range(len(expected) - 1, 0, -1):
        bound = i + 1
        zone = ((bound << (32 - bound.bit_length())) - 1) & 0xFFFFFFFF
        while True:
            v = rng.next_u32()
            product = v * bound
            if (product & 0xFFFFFFFF) <= zone:
                j = product >> 32
                break
        expected[i], expected[j] = expected[j], expected[i]
    assert shuffled == expected


def test_shuffle_does_not_mutate_its_input() -> None:
    """The host's `vec_shuffle` clones and returns a NEW VecObject (K13.6)."""
    items = [1, 2, 3, 4, 5, 6, 7, 8]
    before = list(items)
    _prng.shuffle(_prng.from_prng_seed(SEED), items)
    assert items == before


def test_an_inverted_range_is_refused() -> None:
    """`Error(Value, InvalidInput)` on the host (K13.5); a `ValueError` here --
    `env.py` is what turns it into a `PrngTrap`."""
    with pytest.raises(ValueError):
        _prng.u64_in_inclusive_range(_prng.from_prng_seed(SEED), 10, 9)


def test_a_degenerate_range_answers_lo() -> None:
    """`lo == hi`: `span == 1`, `z == 0`, and the FIRST draw always lands.

    Named for what it asserts. The v1 name said "without drawing", which is
    false: `span == 1` gives `rejected == 0`, so one `next_u64()` is still
    consumed and `_index` moves 64 -> 2 [m3].
    """
    assert _prng.u64_in_inclusive_range(_prng.from_prng_seed(SEED), 7, 7) == 7


@pytest.mark.parametrize(
    "lo,hi",
    [
        (0, 2**64),  # hi is one past u64::MAX
        (0, 2**70),
        (2**64, 2**64 + 5),  # lo is out of range
        (-1, 10),  # negative lo
        (-100, -50),
        (1, 0),  # lo > hi, the host's own refusal (K13.5)
    ],
)
def test_an_out_of_u64_range_is_refused(lo: int, hi: int) -> None:
    """The host's arguments are `u64`, so it cannot see these; the Python
    facade can, and `_prng` is also called directly by
    `tests/harness/hostfns.py`. Before the guard these WRAPPED silently:
    measured `(0, 2**64) -> 0`, `(-1, 10) -> 4`,
    `(-100, -50) -> 18446744073709551539` [M11].
    """
    with pytest.raises(ValueError):
        _prng.u64_in_inclusive_range(_prng.from_prng_seed(SEED), lo, hi)


@pytest.mark.parametrize("lo,hi", [(5, 5), (0, 2**64 - 1), (0, 0)])
def test_the_u64_boundaries_are_accepted(lo: int, hi: int) -> None:
    """The guard is `0 <= lo <= hi <= 2**64 - 1`, inclusive on every side, so
    the full-width range and a degenerate one both pass [M11]."""
    drawn = _prng.u64_in_inclusive_range(_prng.from_prng_seed(SEED), lo, hi)
    assert lo <= drawn <= hi
```

Run: `uv run --no-sync pytest -q tests/unit/test_prng_primitives.py`
Expected: `ModuleNotFoundError: No module named 'serpent._prng'` -- collection error, 0 tests run.

**[m3] [M11] note for the task review:** this fence now holds **18** tests (14 in v1, plus the six-case and three-case parametrizations replacing nothing, minus nothing -- `pytest` counts a parametrization once per case, so the file collects 14 + 6 + 3 = **23**). The counts in Step 5 are stated against that.

- [ ] **Step 2: `src/serpent/_prng.py`**

Create it with exactly this body:

```python
"""The host's frame PRNG, reproduced exactly: HMAC-SHA256 unbias, ChaCha20,
and rand 0.8.8's two sampler arms.

**A CORE module** (`tests/unit/test_core_zero_dep.py` walks it): `hashlib`,
`hmac`, and `struct` only, forever. Private, like `_strkey.py` and
`_frame.py`, so it is implementation rather than authoring surface and never
joins `serpent.__all__`.

ONE implementation, shared by the tier-1 `Env` and by the mini host
(`tests/harness/hostfns.py`) -- spec SS10's one-Val-codec principle applied to
the PRNG (S6). Two implementations of one semantics is exactly the shape that
produces a silent false green (S8).

## Why this can be exact

`prng_reseed(b)` REPLACES the frame PRNG with `Prng::new_from_seed(b)`
(`soroban-env-host-28.0.2/src/host.rs:3734-3763`); it does not mix into it. So
after a reseed every draw is a pure function of the 32 seed bytes --
independent of the base PRNG, the frame, and the transaction. That is the
whole reason this module can claim bit-for-bit agreement with the chain, and
it is why the tier-1 model refuses to pin an UNSEEDED draw: before a reseed
the host's stream is derived from an embedder seed (in stellar-core, the txset
hash and the transaction's apply-order position) that serpent cannot know.

## The three ways this goes subtly wrong

1. **Forgetting the unbias.** `Prng::new_from_seed` is
   `ChaCha20Rng::from_seed(unbias_prng_seed(seed))`, and `unbias_prng_seed` is
   HMAC-SHA256 under a salt the host's own comment calls "effectively part of
   the protocol". The salt is sha256 of the Stellar public network passphrase.
2. **The buffer geometry.** `ChaCha20Rng` is a `BlockRng` over 64 u32 words
   (four ChaCha blocks), and `next_u64` has a documented split case at the
   buffer boundary. Getting it wrong shifts the stream by one word every 32
   draws -- invisible in any distribution test.
3. **The sampler arms.** `prng_u64_in_inclusive_range` uses
   `UniformInt::<u64>` (Lemire with EXACT rejection); `vec_shuffle` uses
   `gen_index`, which for any bound below `u32::MAX` is
   `UniformInt::<u32>::sample_single_inclusive` -- a different arm, with an
   APPROXIMATE zone, drawing one u32 per attempt. The two consume different
   word widths, so reusing one for the other produces the right distribution
   and the wrong stream.

Pinned by soroban-sdk 28.0.0-rc.1's own three doctest vectors in
`tests/unit/test_prng_primitives.py`, and by the three-tier differential in
`tests/semantics/prng_vectors.py`.
"""

from __future__ import annotations

import hmac
import struct
from collections.abc import Sequence
from hashlib import sha256
from typing import TypeVar

_T = TypeVar("_T")

#: The host's fixed unbias salt (`src/crypto/mod.rs:456-486`), which is
#: `sha256("Public Global Stellar Network ; September 2015")`. The host's
#: comment: "Salt is fixed and must not be changed; it is effectively 'part of
#: the protocol' and must be the same for all implementations."
UNBIAS_SALT = bytes.fromhex("7ac33997544e3175d266bd022439b22cdb16508c01163f26e5cb2a3e1045a979")

#: `prng_reseed` takes exactly this many bytes; anything else is
#: `Error(Value, UnexpectedSize)` on the host (K13.4).
SEED_BYTES = 32

#: `rand_chacha-0.3.1`: `BUF_BLOCKS = 4`, `BLOCK_WORDS = 16`.
BLOCK_WORDS = 16
BUF_BLOCKS = 4
BUFFER_WORDS = BLOCK_WORDS * BUF_BLOCKS

_MASK32 = 0xFFFFFFFF
_MASK64 = 0xFFFFFFFFFFFFFFFF
#: "expand 32-byte k", little-endian.
_SIGMA = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)
_QUARTER_ROUNDS = (
    (0, 4, 8, 12),
    (1, 5, 9, 13),
    (2, 6, 10, 14),
    (3, 7, 11, 15),
    (0, 5, 10, 15),
    (1, 6, 11, 12),
    (2, 7, 8, 13),
    (3, 4, 9, 14),
)


def unbias_seed(seed: bytes) -> bytes:
    """The host's `unbias_prng_seed`: HMAC-SHA256 of `seed` keyed by the salt.

    The host runs a user-supplied seed through this because ChaCha's security
    argument assumes an unbiased key, and a guest may hand it 32 zero bytes.
    """
    if len(seed) != SEED_BYTES:
        raise ValueError(f"a PRNG seed is exactly {SEED_BYTES} bytes, not {len(seed)}")
    return hmac.new(UNBIAS_SALT, seed, sha256).digest()


def _rotl32(v: int, n: int) -> int:
    return ((v << n) | (v >> (32 - n))) & _MASK32


def _chacha20_block(key: tuple[int, ...], counter: int) -> list[int]:
    """One 64-byte ChaCha20 block as 16 little-endian u32 words.

    `ChaCha20Rng::from_seed` sets stream 0, so both nonce words are zero, and
    the 64-bit block counter occupies words 12 and 13.
    """
    state = [*_SIGMA, *key, counter & _MASK32, (counter >> 32) & _MASK32, 0, 0]
    x = list(state)
    for _ in range(10):  # 20 rounds = 10 double rounds
        for a, b, c, d in _QUARTER_ROUNDS:
            x[a] = (x[a] + x[b]) & _MASK32
            x[d] = _rotl32(x[d] ^ x[a], 16)
            x[c] = (x[c] + x[d]) & _MASK32
            x[b] = _rotl32(x[b] ^ x[c], 12)
            x[a] = (x[a] + x[b]) & _MASK32
            x[d] = _rotl32(x[d] ^ x[a], 8)
            x[c] = (x[c] + x[d]) & _MASK32
            x[b] = _rotl32(x[b] ^ x[c], 7)
    return [(x[i] + state[i]) & _MASK32 for i in range(BLOCK_WORDS)]


class ChaCha20Rng:
    """`rand_chacha::ChaCha20Rng` as a `BlockRng` over 64 u32 words.

    The word INDEX is the whole state that matters for stream fidelity: a
    fresh generator starts with an exhausted buffer (index == `BUFFER_WORDS`),
    so the first draw generates blocks 0 through 3 and reads word 0.
    """

    __slots__ = ("_buffer", "_counter", "_index", "_key")

    def __init__(self, key32: bytes) -> None:
        if len(key32) != 32:
            raise ValueError(f"a ChaCha20 key is exactly 32 bytes, not {len(key32)}")
        self._key: tuple[int, ...] = struct.unpack("<8I", key32)
        self._counter = 0
        self._buffer: list[int] = []
        self._index = BUFFER_WORDS

    def _generate_and_set(self, index: int) -> None:
        words: list[int] = []
        for _ in range(BUF_BLOCKS):
            words.extend(_chacha20_block(self._key, self._counter))
            self._counter += 1
        self._buffer = words
        self._index = index

    def next_u32(self) -> int:
        if self._index >= BUFFER_WORDS:
            self._generate_and_set(0)
        value = self._buffer[self._index]
        self._index += 1
        return value

    def next_u64(self) -> int:
        """`rand_core-0.6.4`'s `BlockRng::next_u64`, split case included.

        Two adjacent words, low first. When the index sits on the LAST word the
        low half comes from this buffer and the high half from the next one --
        and the index is then left at 1, not 2, which is the detail a
        reimplementation gets wrong.
        """
        index = self._index
        if index < BUFFER_WORDS - 1:
            self._index += 2
            low, high = self._buffer[index], self._buffer[index + 1]
            return ((high << 32) | low) & _MASK64
        if index >= BUFFER_WORDS:
            self._generate_and_set(2)
            low, high = self._buffer[0], self._buffer[1]
            return ((high << 32) | low) & _MASK64
        low = self._buffer[BUFFER_WORDS - 1]
        self._generate_and_set(1)
        high = self._buffer[0]
        return ((high << 32) | low) & _MASK64

    def fill_bytes(self, n: int) -> bytes:
        """`n` bytes, little-endian per u32 word -- `chacha20_fill_bytes`.

        A partial word at the end still CONSUMES the whole word, which is
        `fill_via_u32_chunks`' own behaviour.
        """
        if n < 0:
            raise ValueError(f"fill_bytes takes a non-negative length, not {n}")
        out = bytearray()
        while len(out) < n:
            if self._index >= BUFFER_WORDS:
                self._generate_and_set(0)
            available = self._buffer[self._index :]
            wanted = n - len(out)
            words = min(len(available), (wanted + 3) // 4)
            blob = struct.pack(f"<{words}I", *available[:words])
            out += blob[: min(wanted, len(blob))]
            self._index += words
        return bytes(out)


def from_prng_seed(seed: bytes) -> ChaCha20Rng:
    """`Prng::new_from_seed`: unbias, then `ChaCha20Rng::from_seed`."""
    return ChaCha20Rng(unbias_seed(seed))


def u64_in_inclusive_range(rng: ChaCha20Rng, lo: int, hi: int) -> int:
    """`prng_u64_in_inclusive_range`: rand 0.8.8's `UniformInt::<u64>`.

    Lemire's method with EXACT rejection. `range` is computed wrapping, so the
    full u64 range gives `range == 0`, which the sampler answers with one bare
    `next_u64` and no rejection loop at all -- the branch the sdk's
    `gen::<u64>()` vector exercises.

    Both arguments must be in `[0, 2**64 - 1]`. The host cannot express a
    violation (its arguments ARE `u64`), but this module is called directly
    from Python by `serpent.env.Prng` and by `tests/harness/hostfns.py`, and
    without the check the wrapping arithmetic below answers plausibly for
    nonsense: `(0, 2**64)` gives `span == 1` and so always returns `lo`,
    `(-1, 10)` returned 4, `(-100, -50)` returned 18446744073709551539 [M11].
    """
    if not (0 <= lo <= hi <= 2**64 - 1):
        raise ValueError(
            f"prng_u64_in_inclusive_range needs 0 <= lo <= hi <= 2**64 - 1, got lo={lo} hi={hi}"
        )
    span = (hi - lo + 1) & _MASK64
    if span == 0:
        return rng.next_u64()
    rejected = (_MASK64 - span + 1) % span
    zone = _MASK64 - rejected
    while True:
        drawn = rng.next_u64()
        product = drawn * span
        if (product & _MASK64) <= zone:
            return (lo + (product >> 64)) & _MASK64


def _gen_index(rng: ChaCha20Rng, bound: int) -> int:
    """`rand::seq::index::gen_index(rng, bound)` for `bound <= u32::MAX`.

    `gen_range(0..bound as u32)` -> `UniformInt::<u32>::sample_single_inclusive`,
    whose zone is the APPROXIMATE `(range << range.leading_zeros()) - 1` rather
    than the exact modulus the u64 arm computes, and which draws one u32 per
    attempt. Using the u64 arm here is the F5 failure: right distribution,
    wrong stream.
    """
    span = bound & _MASK32
    if span == 0:
        # [m12] A KNOWN, UNREACHABLE divergence, written down rather than left
        # silent: rand 0.8.8 routes `ubound > u32::MAX` to the usize arm and
        # DRAWS a u64 (`seq/mod.rs:659-665`), where this returns 0 without
        # drawing. Reaching it needs a Vec of 2**32 elements, which no host
        # allocation limit permits. Do not "fix" it by drawing -- that would
        # change the stream for `bound == 0`, which shuffle never passes.
        return 0
    zone = ((span << (32 - span.bit_length())) - 1) & _MASK32
    while True:
        drawn = rng.next_u32()
        product = drawn * span
        if (product & _MASK32) <= zone:
            return (product >> 32) & _MASK32


def shuffle(rng: ChaCha20Rng, items: Sequence[_T]) -> list[_T]:
    """`prng_vec_shuffle`: `SliceRandom::shuffle` over a CLONE.

    Fisher-Yates descending: for `i` from `len - 1` down to 1, swap `i` with
    `gen_index(rng, i + 1)`. Returns a new list -- the host's op is functional
    and hands back a fresh `VecObject`.
    """
    out = list(items)
    for i in range(len(out) - 1, 0, -1):
        j = _gen_index(rng, i + 1)
        out[i], out[j] = out[j], out[i]
    return out
```

Run: `uv run --no-sync pytest -q tests/unit/test_prng_primitives.py`
Expected: `23 passed` [M8] [M11] -- 14 as v1 wrote them, plus the nine parametrized range-guard cases Step 1 adds.

- [ ] **Step 3: The crypto vectors, failing first**

Create `tests/unit/test_crypto_primitives.py`:

```python
"""`serpent._crypto` against the host's own test vectors.

Every hash and signature vector below is copied from
`soroban-env-host-28.0.2/src/test/crypto.rs` (K20), so a green run here is
agreement with the code the chain runs, not with this module's own idea of
what the answer should be. Two vectors are additionally drawn from outside
the host (keccak of `b"abc"` and of a million `a`s, both long-standing
Ethereum/Keccak-KAT reference values) so the multi-block path has a witness
that did not come from Stellar at all.

The NEGATIVE vectors matter more than the positive ones (F3/F4): a textbook
ed25519 that rejects a non-canonical `y`, or an ECDSA that accepts a high
`s`, passes every positive vector and diverges from the host on exactly the
inputs an attacker chooses.
"""

from __future__ import annotations

from serpent import _crypto

# --- sha256 / keccak256 (K20) ------------------------------------------------

SHA256_VECTORS: tuple[tuple[bytes, str], ...] = (
    (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    (bytes([1]), "4bf5122f344554c53bde2ebb8cd2b7e3d1600ad631c385a5d7cce23c7785459a"),
    (
        b"test vector for soroban",
        "91a8e0fbbf626bf0c24d3cb7adbbef332e42339f56dd943cf272be28978dc294",
    ),
)

KECCAK256_VECTORS: tuple[tuple[bytes, str], ...] = (
    # The empty input is THE discriminator between keccak-256 and SHA3-256:
    # the two differ only in the padding byte (0x01 versus 0x06), and an
    # implementation that copied a SHA3 reference produces plausible-looking
    # 32-byte digests that are wrong for every input (F1).
    (b"", "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"),
    (bytes([1]), "5fe7f977e71dba2ea1a68e21057beebb9be2ac30c6410aa38d4f3fbe41dcffd2"),
    (
        b"test vector for soroban",
        "352fe2eaddf44eb02eb3eab1f8d6ff4ba426df4f1734b1e3f210d621ee8853d9",
    ),
    # NOT from the host: the long-standing Ethereum reference value, so the
    # single-block path has a witness from outside this ecosystem.
    (b"abc", "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"),
)


def test_sha256_matches_the_hosts_vectors() -> None:
    for data, digest in SHA256_VECTORS:
        assert _crypto.sha256(data).hex() == digest


def test_keccak256_matches_the_hosts_vectors() -> None:
    for data, digest in KECCAK256_VECTORS:
        assert _crypto.keccak256(data).hex() == digest


def test_keccak256_over_a_multi_block_input() -> None:
    """F2: the 136-byte rate means a single-block test passes with a broken
    absorb loop. Both of these span thousands of blocks, and neither digest
    came from this implementation -- the first is the host's own vector, the
    second the Keccak KAT's million-`a` value."""
    assert (
        _crypto.keccak256(bytes([1]) * 1_000_000).hex()
        == "eb8c4805c2569851fe8a82ed3bf5a95f61090aad0489058aaa99d9b98019aad3"
    )
    assert (
        _crypto.keccak256(b"a" * 1_000_000).hex()
        == "fadae6b49f129bbb812be8407b7b2894f34aecf6dbd1f9b0f0c7e9853098fc96"
    )


def test_keccak256_at_the_rate_boundary_is_self_consistent() -> None:
    """135, 136, and 137 bytes straddle the padding-block boundary. No external
    vector exists for these, so the claim is deliberately the weak one -- three
    DISTINCT digests, each 32 bytes -- rather than a number this module
    produced being dressed up as an oracle."""
    digests = {_crypto.keccak256(bytes([0xAB]) * n) for n in (135, 136, 137)}
    assert len(digests) == 3
    assert all(len(d) == 32 for d in digests)


# --- ed25519 (RFC 8032 SS7.1, via K20) ----------------------------------------

TEST1_KEY = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
TEST1_SIG = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc"
    "61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
)
TEST2_KEY = bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
TEST2_MSG = bytes.fromhex("72")
TEST2_SIG = bytes.fromhex(
    "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e4"
    "58f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"
)
LONG_KEY = bytes.fromhex("25ddcf6f16fe15f846553ceee99faad07b4c12e063cd817b7df66310d7338fda")
LONG_SIG = bytes.fromhex(
    "1c2e91002652bf75c61d35055b674b7aab7a868a9feffd70b5f07abd8d95b09fb3d2e55b4a4b48f80"
    "bb92d1ed6b9d1cfe1388be443a2b37f95ee744756e1f702"
)


def test_ed25519_rfc8032_test_1_empty_message() -> None:
    assert _crypto.ed25519_verify(TEST1_KEY, b"", TEST1_SIG) is True


def test_ed25519_rfc8032_test_2_one_byte_message() -> None:
    assert _crypto.ed25519_verify(TEST2_KEY, TEST2_MSG, TEST2_SIG) is True


def test_ed25519_over_a_hundred_thousand_byte_message() -> None:
    """The host's own long-message vector: proof the SHA-512 absorb is right
    over an input far past one block."""
    assert _crypto.ed25519_verify(LONG_KEY, b"a" * 100_000, LONG_SIG) is True


def test_ed25519_rejects_the_wrong_message() -> None:
    assert _crypto.ed25519_verify(TEST2_KEY, bytes.fromhex("73"), TEST2_SIG) is False


def test_ed25519_rejects_a_scalar_at_or_above_the_group_order() -> None:
    """F3: `verify_strict` deserialises `s` through `Scalar::from_canonical_bytes`
    (the non-legacy arm), so `s >= l` is refused. A plain `verify` would reduce
    it and accept -- malleability the host does not have."""
    at_order = TEST2_SIG[:32] + _crypto.ED25519_L.to_bytes(32, "little")
    assert _crypto.ed25519_verify(TEST2_KEY, TEST2_MSG, at_order) is False
    above = TEST2_SIG[:32] + (_crypto.ED25519_L + 1).to_bytes(32, "little")
    assert _crypto.ed25519_verify(TEST2_KEY, TEST2_MSG, above) is False


def test_ed25519_rejects_a_small_order_public_key() -> None:
    """F3: `verify_strict` refuses a small-order `R` or public key outright.
    The identity point (y == 1) is the cheapest witness."""
    identity = bytes([1]) + bytes(31)
    assert _crypto.ed25519_verify(identity, TEST2_MSG, TEST2_SIG) is False


def test_ed25519_accepts_a_non_canonical_y_and_reduces_it() -> None:
    """F3, and the shape is NOT what the dossier's prose implies.

    `VerifyingKey::from_bytes` does not reject `y >= p`: curve25519-dalek's
    `CompressedEdwardsY::decompress` masks the sign bit and reduces the field
    element, so a non-canonical encoding is silently accepted (K11). A tier-1
    implementation that checked `y < p` would refuse a key the host accepts.

    It cannot be exercised through a full `ed25519_verify`, because the key's
    RAW BYTES feed the `SHA512(R || A || M)` challenge -- re-encoding the key
    non-canonically changes `k` and the signature stops verifying for an
    unrelated reason. So the claim is made where it lives: at the
    decompression, which must map `y` and `y + p` to the same point.
    """
    for small_y in range(19):  # the only y values for which y + p fits in 255 bits
        # [M5] wrapped: the single-expression form was 104 columns and
        # `ruff format --check` (gate 2) reformatted the file.
        canonical = _crypto.edwards_decompress(small_y.to_bytes(32, "little"))
        non_canonical = _crypto.edwards_decompress(
            (small_y + _crypto.ED25519_P).to_bytes(32, "little")
        )
        assert (canonical is None) == (non_canonical is None), small_y
        if canonical is not None and non_canonical is not None:
            assert _crypto.edwards_equal(canonical, non_canonical), small_y


def test_ed25519_refuses_a_non_canonically_encoded_r() -> None:
    """[M10] dalek compares the two `R` values as COMPRESSED BYTES, not as
    points (`ed25519-dalek-2.2.0/src/verifying.rs:375`,
    `expected_R == signature.R`). A signature whose `R` is a non-canonical
    encoding (low 255 bits >= p) therefore verifies under a point comparison
    and is refused on chain.

    Ten of the nineteen non-canonical `y` values (3, 4, 5, 6, 9, 10, 14, 15,
    16, 18) decompress to points that are NOT small order, so they survive the
    `is_small_order` screen and reach the final comparison. The test is
    written at the level where the two implementations actually differ,
    because a signature whose `R` is a non-canonical encoding of the CORRECT
    point cannot be built from a fixture (RFC 8032 TEST 2's `R` has
    `y + p >= 2**255`, measured, so it has no second spelling, and finding a
    signature whose `R` does is discrete-log-hard).

    So the discriminator is stated directly: for every such `y`, a POINT
    comparison cannot tell the two encodings apart (`edwards_equal` is True)
    while a BYTE comparison can, because `compress` always answers the
    canonical spelling. That is exactly the difference between v1's code and
    dalek's.
    """
    for small_y in (3, 4, 5, 6, 9, 10, 14, 15, 16, 18):
        canonical_bytes = small_y.to_bytes(32, "little")
        non_canonical_bytes = (small_y + _crypto.ED25519_P).to_bytes(32, "little")
        canonical = _crypto.edwards_decompress(canonical_bytes)
        non_canonical = _crypto.edwards_decompress(non_canonical_bytes)
        assert canonical is not None and non_canonical is not None, small_y
        # Not small order, so `is_small_order` does NOT screen them out.
        assert not _crypto._is_small_order(canonical), small_y
        # A point comparison accepts the non-canonical spelling ...
        assert _crypto.edwards_equal(canonical, non_canonical), small_y
        # ... and a byte comparison refuses it, which is what dalek does.
        assert _crypto.compress(canonical) == canonical_bytes, small_y
        assert _crypto.compress(non_canonical) != non_canonical_bytes, small_y


def test_ed25519_refuses_a_wrongly_sized_key_or_signature() -> None:
    """K10: a 31-byte key is `Error(Crypto, InvalidInput)` and a 65-byte
    signature is `Error(Object, UnexpectedSize)` -- two different host error
    kinds, both traps from the guest's point of view, so this module answers
    `False` for each and `env.py` raises `CryptoTrap` either way."""
    assert _crypto.ed25519_verify(TEST2_KEY[:31], TEST2_MSG, TEST2_SIG) is False
    assert _crypto.ed25519_verify(TEST2_KEY, TEST2_MSG, TEST2_SIG + bytes(1)) is False


# --- secp256k1 recovery (go-ethereum's vector, via K20) ----------------------

K1_DIGEST = bytes.fromhex("ce0677bb30baa8cf067c88db9811f4333d131bf8bcf12fe7065d211dce971008")
K1_SIG = bytes.fromhex(
    "90f27b8b488db00b00606796d2987f6a5f59ae62ea05effe84fef5b8b0e549984a691139ad57a3f0b"
    "906637673aa2f63d1f55cb1a69199d4009eea23ceaddc93"
)
K1_RECOVERED = (
    "04e32df42865e97135acfb65f3bae71bdc86f4d49150ad6a440b6f15878109880a0a2b2667f7e725c"
    "eea70c673093bf67663e0312623c8e091b13cf2c0f11ef652"
)


def test_secp256k1_recovers_the_hosts_own_vector() -> None:
    recovered = _crypto.secp256k1_recover(K1_DIGEST, K1_SIG, 1)
    assert recovered is not None
    assert recovered.hex() == K1_RECOVERED
    assert len(recovered) == 65 and recovered[0] == 0x04


def test_secp256k1_refuses_a_high_s_signature() -> None:
    """F4: every ECDSA signature reaches the host through
    `ecdsa_signature_from_bytes`, which refuses `sig.s().is_high()` with
    `Error(Crypto, InvalidInput)`. Most reference implementations do not do
    this check, so an oracle that omits it accepts what the chain refuses.
    `n - s` is the same signature in the other half of the order."""
    high_s = (_crypto.SECP256K1.n - int.from_bytes(K1_SIG[32:], "big")).to_bytes(32, "big")
    assert _crypto.secp256k1_recover(K1_DIGEST, K1_SIG[:32] + high_s, 1) is None


def test_secp256k1_refuses_a_recovery_id_above_three() -> None:
    """K10: `recovery_id > 3` is `Error(Crypto, InvalidInput)`."""
    assert _crypto.secp256k1_recover(K1_DIGEST, K1_SIG, 4) is None
    assert _crypto.secp256k1_recover(K1_DIGEST, K1_SIG, 5) is None
    assert _crypto.secp256k1_recover(K1_DIGEST, K1_SIG, 0xFFFFFFFF) is None


def test_secp256k1_refuses_a_wrongly_sized_digest_or_signature() -> None:
    assert _crypto.secp256k1_recover(K1_DIGEST[:31], K1_SIG, 1) is None
    assert _crypto.secp256k1_recover(K1_DIGEST, K1_SIG + bytes(1), 1) is None


def test_secp256k1_with_the_wrong_recovery_id_does_not_recover_the_same_key() -> None:
    other = _crypto.secp256k1_recover(K1_DIGEST, K1_SIG, 0)
    assert other is None or other.hex() != K1_RECOVERED


# --- P-256 (a NIST CAVP vector, via K20) -------------------------------------

R1_KEY = bytes.fromhex(
    "04e424dc61d4bb3cb7ef4344a7f8957a0c5134e16f7a67c074f82e6e12f49abf3c970eed7aa2bc486"
    "51545949de1dddaf0127e5965ac85d1243d6f60e7dfaee927"
)
R1_DIGEST = bytes.fromhex("d1b8ef21eb4182ee270638061063a3f3c16c114e33937f69fb232cc833965a94")
R1_SIG = bytes.fromhex(
    "bf96b99aa49c705c910be33142017c642ff540c76349b9dab72f981fd9347f4f17c55095819089c2e"
    "03b9cd415abdf12444e323075d98f31920b9e0f57ec871c"
)


def test_secp256r1_verifies_the_hosts_own_vector() -> None:
    assert _crypto.secp256r1_verify(R1_KEY, R1_DIGEST, R1_SIG) is True


def test_secp256r1_rejects_a_modified_digest() -> None:
    """The host's own negative 1c: one byte changed is `Error(Crypto, InvalidInput)`."""
    modified = bytes.fromhex("d1b8ef21eb4182ee270638061063a3f3c16c114e33937f69fb232cc833965a95")
    assert _crypto.secp256r1_verify(R1_KEY, modified, R1_SIG) is False


def test_secp256r1_rejects_a_modified_public_key() -> None:
    """The host's own negative 2c."""
    modified = R1_KEY[:-1] + bytes([R1_KEY[-1] ^ 0x01])
    assert _crypto.secp256r1_verify(modified, R1_DIGEST, R1_SIG) is False


def test_secp256r1_refuses_a_high_s_signature() -> None:
    """F4, the P-256 half."""
    high_s = (_crypto.SECP256R1.n - int.from_bytes(R1_SIG[32:], "big")).to_bytes(32, "big")
    assert _crypto.secp256r1_verify(R1_KEY, R1_DIGEST, R1_SIG[:32] + high_s) is False


def test_secp256r1_refuses_a_compressed_public_key() -> None:
    """K10: the host checks the SEC-1 tag byte is `0x04` (UNCOMPRESSED) BEFORE
    parsing, so a perfectly valid compressed key is refused. This is the host's
    own negative 2d."""
    compressed = bytes([0x02]) + R1_KEY[1:33]
    assert _crypto.secp256r1_verify(compressed, R1_DIGEST, R1_SIG) is False


def test_secp256r1_refuses_a_wrongly_sized_digest() -> None:
    assert _crypto.secp256r1_verify(R1_KEY, R1_DIGEST[:31], R1_SIG) is False
    assert _crypto.secp256r1_verify(R1_KEY, R1_DIGEST + bytes(1), R1_SIG) is False
```

Run: `uv run --no-sync pytest -q tests/unit/test_crypto_primitives.py`
Expected: `ModuleNotFoundError: No module named 'serpent._crypto'`.

- [ ] **Step 4: `src/serpent/_crypto.py`**

Create it with exactly this body:

```python
"""The five non-ZK crypto primitives the host exposes, in pure Python.

**A CORE module** (`tests/unit/test_core_zero_dep.py` walks it): `hashlib`
only, forever. Private, like `_strkey.py`, so it is implementation rather than
authoring surface and never joins `serpent.__all__`. ONE implementation,
shared by the tier-1 `Env` and by the mini host -- S6's one-Val-codec
principle applied to cryptography, because two implementations of one
semantics is exactly the shape that produces a silent false green (S8).

`sha256` is `hashlib`'s. The other four are written here because `hashlib`
offers SHA3, not Keccak (different padding), and no third-party library is
reachable from the zero-dep core at any price.

## What "matches the host" means here, and where it is subtle

Each function answers what `soroban-env-host-28.0.2` answers, INCLUDING its
refusals, and the refusals are where a textbook implementation drifts:

* **ed25519** is `verify_strict`, not `verify`: a non-canonical scalar
  `s >= l` is refused, and a small-order `R` or public key is refused. But a
  non-canonical `y` encoding (`y >= p`) is ACCEPTED and reduced, because
  curve25519-dalek's decompression masks the sign bit and reduces the field
  element. An implementation that "helpfully" rejects `y >= p` refuses keys the
  chain accepts.
* **Both ECDSA curves** go through the host's `ecdsa_signature_from_bytes`,
  which refuses a high `s` (`s > n / 2`). Most reference implementations do
  not, so omitting the check accepts signatures the chain refuses.
* **P-256 public keys** must carry the SEC-1 UNCOMPRESSED tag `0x04`; the host
  checks the tag byte before parsing, so a valid compressed key is refused.
* **Lengths** are the host's: 32-byte ed25519 keys, 64-byte signatures,
  32-byte digests, 65-byte recovered keys. A wrong length is a host trap
  either way (`Crypto/InvalidInput` or `Object/UnexpectedSize`, K10), so this
  module reports it as a plain refusal and `serpent.env` raises `CryptoTrap`.

Performance, RE-measured 2026-09-11 on CPython 3.11 [m4]: keccak256 0.32 ms on
a short input and 2.36 s on a megabyte, ed25519 3.54 ms, secp256k1 recovery
11.90 ms, P-256 verify 11.81 ms. The "~6 ms" v1 quoted for P-256 (and ruling
E1's "0.3-6.5 ms per operation" band) was low by about 2x at the top end; the
strategy ruling stands, the number is corrected here and in decisions.md.
Modular
inverses use `pow(x, -1, p)` (extended Euclid, CPython 3.8+) rather than
`pow(x, p - 2, p)`: measured 8.6x faster, and the reason no Jacobian rewrite
is warranted.

Pinned by the host's own vectors in `tests/unit/test_crypto_primitives.py`.
"""

from __future__ import annotations

import hashlib

# --- SHA-256 -----------------------------------------------------------------


def sha256(data: bytes) -> bytes:
    """`compute_hash_sha256`. Stdlib; the host has no behaviour to mirror here
    beyond a budget limit no tier-1 model has."""
    return hashlib.sha256(data).digest()


# --- Keccak-256 --------------------------------------------------------------

_KECCAK_RATE = 136  # 1600 bits of state minus 2 * 256 bits of capacity, in bytes
_M64 = 0xFFFFFFFFFFFFFFFF
_KECCAK_ROUND_CONSTANTS: tuple[int, ...] = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)  # fmt: skip
#: rho offsets, indexed `[x][y]`.
_KECCAK_ROTATIONS: tuple[tuple[int, ...], ...] = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rotl64(value: int, n: int) -> int:
    return ((value << n) | (value >> (64 - n))) & _M64


def _keccak_f1600(a: list[list[int]]) -> None:
    """The permutation, in place. 24 rounds of theta, rho+pi, chi, iota."""
    for round_constant in _KECCAK_ROUND_CONSTANTS:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl64(a[x][y], _KECCAK_ROTATIONS[x][y])
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y] & _M64) & b[(x + 2) % 5][y])
        a[0][0] ^= round_constant


def keccak256(data: bytes) -> bytes:
    """`compute_hash_keccak256`: the ORIGINAL Keccak padding, not SHA3's.

    The whole difference between this and `hashlib.sha3_256` is the domain
    separator -- `0x01` here, `0x06` for SHA3 -- and it changes every digest,
    including the empty input's. That is why `keccak256(b"")` is the first
    vector in the test file (F1).
    """
    state = [[0] * 5 for _ in range(5)]
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % _KECCAK_RATE != 0:
        padded.append(0x00)
    padded[-1] ^= 0x80
    for offset in range(0, len(padded), _KECCAK_RATE):
        block = padded[offset : offset + _KECCAK_RATE]
        for i in range(_KECCAK_RATE // 8):
            state[i % 5][i // 5] ^= int.from_bytes(block[i * 8 : i * 8 + 8], "little")
        _keccak_f1600(state)
    out = bytearray()
    for i in range(4):  # 32 bytes squeezed from the first four lanes
        out += state[i % 5][i // 5].to_bytes(8, "little")
    return bytes(out)


# --- ed25519 -----------------------------------------------------------------

#: The curve25519 field prime.
ED25519_P = 2**255 - 19
#: The prime order of the base point's subgroup.
ED25519_L = 2**252 + 27742317777372353535851937790883648493
_ED_D = (-121665 * pow(121666, -1, ED25519_P)) % ED25519_P
_ED_SQRT_M1 = pow(2, (ED25519_P - 1) // 4, ED25519_P)

#: Extended homogeneous coordinates `(X, Y, Z, T)` with `x = X/Z`, `y = Y/Z`,
#: `x*y = T/Z`. Chosen over affine because the double-and-add below runs ~256
#: times per verification and affine would need an inversion per step.
EdwardsPoint = tuple[int, int, int, int]

_ED_IDENTITY: EdwardsPoint = (0, 1, 1, 0)


def _edwards_add(p: EdwardsPoint, q: EdwardsPoint) -> EdwardsPoint:
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = ((y1 - x1) * (y2 - x2)) % ED25519_P
    b = ((y1 + x1) * (y2 + x2)) % ED25519_P
    c = (2 * t1 * t2 * _ED_D) % ED25519_P
    d = (2 * z1 * z2) % ED25519_P
    e, f, g, h = b - a, d - c, d + c, b + a
    return ((e * f) % ED25519_P, (g * h) % ED25519_P, (f * g) % ED25519_P, (e * h) % ED25519_P)


def _edwards_mul(point: EdwardsPoint, scalar: int) -> EdwardsPoint:
    result = _ED_IDENTITY
    while scalar > 0:
        if scalar & 1:
            result = _edwards_add(result, point)
        point = _edwards_add(point, point)
        scalar >>= 1
    return result


def edwards_equal(p: EdwardsPoint, q: EdwardsPoint) -> bool:
    """Projective equality: cross-multiply rather than normalise."""
    x1, y1, z1, _ = p
    x2, y2, z2, _ = q
    return (x1 * z2 - x2 * z1) % ED25519_P == 0 and (y1 * z2 - y2 * z1) % ED25519_P == 0


def edwards_decompress(encoded: bytes) -> EdwardsPoint | None:
    """curve25519-dalek's `CompressedEdwardsY::decompress`, refusals included.

    Public because the non-canonical-`y` behaviour (K11/F3) cannot be reached
    through `ed25519_verify` -- the key's raw bytes feed the challenge hash --
    so `tests/unit/test_crypto_primitives.py` pins it here instead.

    The bit-255 sign is masked off and the remaining 255 bits are taken as a
    field element MOD P. A `y >= p` encoding is therefore accepted and reduced,
    never rejected. `None` means exactly one thing: the `y` names no curve
    point, i.e. `sqrt_ratio_i` reports no square.

    [M9] There is deliberately NO `x == 0 and sign` refusal. v1 attributed one
    to dalek; `curve25519-dalek-4.1.3/src/edwards.rs:194-234` has no such
    check -- step 2 is an unconditional `X.conditional_negate(sign_bit)`.
    Measured, the invented branch returned `None` for `y = 1 | sign` and
    `y = p - 1 | sign` where dalek returns `Some(point)`. It is invisible
    through `ed25519_verify` (`x == 0` implies `y == +/-1`, both small order,
    both refused by the `is_small_order` screen), but this function is PUBLIC
    and pinned by its own tests, so it must be dalek's.
    """
    if len(encoded) != 32:
        return None
    y = int.from_bytes(encoded, "little")
    sign = (y >> 255) & 1
    y = (y & ((1 << 255) - 1)) % ED25519_P
    yy = (y * y) % ED25519_P
    u = (yy - 1) % ED25519_P
    v = (_ED_D * yy + 1) % ED25519_P
    # The standard candidate root: x = u * v^3 * (u * v^7) ** ((p - 5) / 8).
    x = u * pow(v, 3, ED25519_P) * pow(u * pow(v, 7, ED25519_P), (ED25519_P - 5) // 8, ED25519_P)
    x %= ED25519_P
    if (v * x * x - u) % ED25519_P != 0:
        if (v * x * x + u) % ED25519_P != 0:
            return None
        x = (x * _ED_SQRT_M1) % ED25519_P
    if x % 2 != sign:
        x = (ED25519_P - x) % ED25519_P
    return (x, y, 1, (x * y) % ED25519_P)


def compress(point: EdwardsPoint) -> bytes:
    """`EdwardsPoint::compress`: the CANONICAL 32-byte encoding.

    Normalise out of the projective `Z`, then pack `y` little-endian with
    `x`'s low bit in bit 255. Public because `ed25519_verify`'s final check
    is a comparison of these BYTES, not of points, and the tests pin that
    [M10].
    """
    x, y, z, _ = point
    z_inverse = pow(z, -1, ED25519_P)
    x_affine = (x * z_inverse) % ED25519_P
    y_affine = (y * z_inverse) % ED25519_P
    return (y_affine | ((x_affine & 1) << 255)).to_bytes(32, "little")


def _is_small_order(point: EdwardsPoint) -> bool:
    """`EdwardsPoint::is_small_order`: `[8]P` is the identity."""
    return edwards_equal(_edwards_mul(point, 8), _ED_IDENTITY)


def _ed25519_basepoint() -> EdwardsPoint:
    """The standard base point, derived from `y = 4/5` with an even `x`."""
    y = (4 * pow(5, -1, ED25519_P)) % ED25519_P
    point = edwards_decompress(y.to_bytes(32, "little"))
    assert point is not None  # the base point is on the curve by construction
    x, y_value, z, _ = point
    if x % 2 != 0:
        x = ED25519_P - x
    return (x, y_value, z, (x * y_value) % ED25519_P)


_ED_BASEPOINT = _ed25519_basepoint()


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """`verify_sig_ed25519`, i.e. ed25519-dalek 2.2.0's `verify_strict`.

    `False` covers every way the host refuses -- a wrong length, an
    undecompressable point, a non-canonical `s`, a small-order `R` or key, and
    an equation that does not hold. The host distinguishes those as
    `Crypto/InvalidInput` versus `Object/UnexpectedSize` (K10), but both are
    traps from the guest's point of view, so the distinction is a message
    concern and `serpent.env` raises one `CryptoTrap` for all of them.
    """
    if len(public_key) != 32 or len(signature) != 64:
        return False
    key_point = edwards_decompress(public_key)
    if key_point is None:
        return False
    r_point = edwards_decompress(signature[:32])
    if r_point is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= ED25519_L:  # `Scalar::from_canonical_bytes`, the non-legacy arm
        return False
    if _is_small_order(r_point) or _is_small_order(key_point):
        return False
    challenge = hashlib.sha512(signature[:32] + public_key + message).digest()
    k = int.from_bytes(challenge, "little") % ED25519_L
    minus_key = (
        (ED25519_P - key_point[0]) % ED25519_P,
        key_point[1],
        key_point[2],
        (ED25519_P - key_point[3]) % ED25519_P,
    )
    expected = _edwards_add(_edwards_mul(_ED_BASEPOINT, s), _edwards_mul(minus_key, k))
    # [M10] dalek's final line is `expected_R == signature.R`, a comparison of
    # two `CompressedEdwardsY` BYTE strings (`verifying.rs:375`), NOT of
    # points. v1 compared points, which accepts a signature whose `R` is
    # non-canonically encoded (low 255 bits >= p) and which the chain refuses.
    # Ten of the nineteen such `y` values are not small order, so they survive
    # the screen above and reach here. The byte comparison is also cheaper.
    return compress(expected) == signature[:32]


# --- the two short-Weierstrass curves ----------------------------------------

#: An affine point, or `None` for the point at infinity.
AffinePoint = tuple[int, int]


class Curve:
    """`y^2 = x^3 + a*x + b` over `F_p`, with a base point of prime order `n`.

    Affine arithmetic with `pow(x, -1, p)`: one inversion per addition, which
    is the measured-fast choice at this call volume (module docstring).
    """

    __slots__ = ("a", "b", "g", "n", "p")

    def __init__(self, p: int, a: int, b: int, n: int, gx: int, gy: int) -> None:
        self.p = p
        self.a = a
        self.b = b
        self.n = n
        self.g: AffinePoint = (gx, gy)

    def is_on_curve(self, point: AffinePoint) -> bool:
        x, y = point
        return (y * y - x * x * x - self.a * x - self.b) % self.p == 0

    def add(self, p1: AffinePoint | None, p2: AffinePoint | None) -> AffinePoint | None:
        if p1 is None:
            return p2
        if p2 is None:
            return p1
        x1, y1 = p1
        x2, y2 = p2
        if x1 == x2 and (y1 + y2) % self.p == 0:
            return None
        if p1 == p2:
            slope = (3 * x1 * x1 + self.a) * pow(2 * y1, -1, self.p) % self.p
        else:
            slope = (y2 - y1) * pow(x2 - x1, -1, self.p) % self.p
        x3 = (slope * slope - x1 - x2) % self.p
        return (x3, (slope * (x1 - x3) - y1) % self.p)

    def mul(self, point: AffinePoint | None, scalar: int) -> AffinePoint | None:
        result: AffinePoint | None = None
        scalar %= self.n
        while scalar:
            if scalar & 1:
                result = self.add(result, point)
            point = self.add(point, point)
            scalar >>= 1
        return result


SECP256K1 = Curve(
    p=2**256 - 2**32 - 977,
    a=0,
    b=7,
    n=0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141,
    gx=0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    gy=0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)
SECP256R1 = Curve(
    p=0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF,
    a=-3,
    b=0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B,
    n=0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551,
    gx=0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    gy=0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
)


def _signature_parts(curve: Curve, signature: bytes) -> tuple[int, int] | None:
    """`Host::ecdsa_signature_from_bytes`: 64 big-endian bytes, `r` and `s` in
    `[1, n)`, and `s` NOT high. The high-`s` refusal is the one most reference
    implementations omit (F4)."""
    if len(signature) != 64:
        return None
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if not 1 <= r < curve.n or not 1 <= s < curve.n:
        return None
    if s > curve.n // 2:  # `sig.s().is_high()`
        return None
    return (r, s)


def secp256k1_recover(digest: bytes, signature: bytes, recovery_id: int) -> bytes | None:
    """`recover_key_ecdsa_secp256k1` -> the 65-byte SEC-1 UNCOMPRESSED key.

    `None` is every refusal the host has: a malformed or high-`s` signature, a
    recovery id above 3, a digest that is not 32 bytes, an `x` off the curve, a
    recovered point at infinity.

    The checks run in the HOST'S ORDER (`host.rs:3050-3062` [m5]): the signature
    first, then the recovery id, then the digest. That ordering is observable
    -- a call that is wrong in two ways reports the first -- so it is mirrored
    rather than tidied.

    **Hazmat**: `digest` must be the output of a secure cryptographic hash. The
    Rust SDK's safe wrapper takes a `Hash<32>` newtype to enforce this and
    serpent has no such type, so the warning lives in the docstring of
    `serpent.env.Crypto.secp256k1_recover` instead.
    """
    parts = _signature_parts(SECP256K1, signature)
    if parts is None:
        return None
    r, s = parts
    if not 0 <= recovery_id <= 3:
        return None
    if len(digest) != 32:
        return None
    curve = SECP256K1
    x = r + curve.n if recovery_id & 2 else r
    if x >= curve.p:
        return None
    alpha = (x * x * x + curve.a * x + curve.b) % curve.p
    y = pow(alpha, (curve.p + 1) // 4, curve.p)  # p == 3 mod 4
    if (y * y - alpha) % curve.p != 0:
        return None
    if (y & 1) != (recovery_id & 1):
        y = curve.p - y
    z = int.from_bytes(digest, "big") % curve.n
    r_inverse = pow(r, -1, curve.n)
    recovered = curve.add(
        curve.mul((x, y), (s * r_inverse) % curve.n),
        curve.mul(curve.g, (-z * r_inverse) % curve.n),
    )
    if recovered is None:
        return None
    return b"\x04" + recovered[0].to_bytes(32, "big") + recovered[1].to_bytes(32, "big")


def secp256r1_verify(public_key: bytes, digest: bytes, signature: bytes) -> bool:
    """`verify_sig_ecdsa_secp256r1`: prehash ECDSA over P-256.

    The public key must be 65 SEC-1 UNCOMPRESSED bytes with the `0x04` tag --
    the host checks the tag byte before parsing, so a valid COMPRESSED key is
    refused (K10) -- and the signature goes through the same high-`s` refusal
    as secp256k1's.
    """
    curve = SECP256R1
    if len(public_key) != 65 or public_key[0] != 0x04:
        return False
    qx = int.from_bytes(public_key[1:33], "big")
    qy = int.from_bytes(public_key[33:], "big")
    if qx >= curve.p or qy >= curve.p or not curve.is_on_curve((qx, qy)):
        return False
    parts = _signature_parts(curve, signature)
    if parts is None:
        return False
    r, s = parts
    if len(digest) != 32:
        return False
    z = int.from_bytes(digest, "big") % curve.n
    s_inverse = pow(s, -1, curve.n)
    point = curve.add(
        curve.mul(curve.g, (z * s_inverse) % curve.n),
        curve.mul((qx, qy), (r * s_inverse) % curve.n),
    )
    if point is None:
        return False
    return point[0] % curve.n == r
```

Run: `uv run --no-sync pytest -q tests/unit/test_crypto_primitives.py`
Expected: `24 passed` [m6] [M10] -- the fence held **23** tests as v1 wrote it (not 20; counted and measured), plus the one non-canonical-`R` negative M10 adds. The million-byte keccak vectors make this file take about 5 seconds; that is the price of F2's evidence and is stated here so nobody "optimises" it away.

- [ ] **Step 5: The zero-dep gate, the four gates, and the commit**

Run: `uv run --no-sync pytest -q tests/unit/test_core_zero_dep.py`
Expected: all pass with no edit to that file -- `hashlib`, `hmac`, and `struct` are in `sys.stdlib_module_names`, and `EXEMPT` is untouched. If `test_core_zero_dep.py` needs ANY edit, stop and report BLOCKED: the two new modules are core by design and an exemption would be the wrong fix.

Then the four gates in full, and report the counts:

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check src tests examples
uv run --no-sync mypy --strict
SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q
```
Expected: `4839 passed / 7 skipped`. Report the actual numbers.

**The arithmetic, since v1 got it wrong and a wrong expected count trains a reviewer to shrug at a mismatch** [M8]: v1 said "4792 + 34 new = 4826". The two fences as v1 wrote them hold **37** tests, not 34 -- 14 in `test_prng_primitives.py` and **23** (not 20) in `test_crypto_primitives.py`; measured `37 passed in 4.90s`. So the M8-corrected v1 figures are **4829 / 4839 / 4857** for Tasks 1 / 2 / 3. v2's own amendments then add ten more tests in this task (nine parametrized range-guard cases for [M11], one non-canonical-`R` negative for [M10]), giving 14 + 9 = 23 and 23 + 1 = 24, so **47 new tests and 4792 + 47 = 4839** here. Tasks 2 and 3 carry the same +10 forward (and Task 3 adds one more of its own, [M3]'s nested-frame test): **4849** and **4868**. Every one of these is a prediction, not a pin -- report MEASURED and explain any gap.

Commit:

```bash
git add src/serpent/_crypto.py src/serpent/_prng.py \
        tests/unit/test_crypto_primitives.py tests/unit/test_prng_primitives.py
git commit -m "feat(core): add the pure-Python crypto and PRNG primitives

keccak-256, ed25519 verify_strict, secp256k1 recovery, and P-256 prehash
verify, plus the host's frame PRNG: HMAC-SHA256 unbias under the protocol
salt, ChaCha20 with rand 0.8.8's two sampler arms, and Fisher-Yates. Both
modules are stdlib-only and stay under the zero-dep walk, so tier 1 and the
mini host can share one implementation.

Pinned by the host's own test vectors and by soroban-sdk's three PRNG
doctest vectors, with negative vectors for the four refusals a textbook
implementation omits: a non-canonical ed25519 scalar, a small-order key, a
high-s ECDSA signature, and a compressed P-256 key."
```

**Report:** the four gate outputs quoted; the passed/skipped counts; the wall time of `tests/unit/test_crypto_primitives.py`; any vector that did NOT reproduce on the first attempt (that is a finding, not a fix opportunity -- report it rather than adjusting the implementation until it matches); any deferred minor; any BLOCKED condition.

---

### Task 2: the value layer and the tier-1 `Env`'s four new facts

**Files:**
- Modify: `src/serpent/types/buffers.py:32-70` (`String`), `:180-237` (`Bytes65`, the cache, the docstring); `src/serpent/__init__.py:70-132`; `tests/unit/test_public_api.py:30-70`; `src/serpent/env.py:216-242` (the constants), `:1346-1386` (`Ledger`), `:1481-1530` (`Env.__slots__`, `__init__`, the accessors); `src/serpent/testing/_real.py:60-82, 148-158`; `src/serpent/testing/_scval.py:107-111, 518-519`
- Create: `tests/unit/test_env_leaf_surface.py`
- Test: the new file, plus `tests/unit/test_public_api.py`, `tests/unit/test_buffers.py`, `tests/unit/test_env*.py`, `tests/unit/test_real_env.py`

**Interfaces:**
- Produces: `serpent.Bytes65` (a real `class Bytes65(Bytes)` with `_LENGTH = 65`); `serpent.__all__` at 41 names.
- Produces in `serpent/env.py`: `DEFAULT_NETWORK_ID: bytes = bytes(32)`, `DEFAULT_MAX_ENTRY_TTL: int = 6_312_000`, `DEFAULT_PROTOCOL_VERSION: int = 28`, `DEFAULT_CONTRACT_ADDRESS: str` (a `C...` strkey); `Env.__init__(self, *, timestamp: int = ..., sequence: int = ..., auths: Iterable[Address] | None = None, contract_address: str = DEFAULT_CONTRACT_ADDRESS, network_id: bytes = DEFAULT_NETWORK_ID, protocol_version: int = DEFAULT_PROTOCOL_VERSION, max_entry_ttl: int = DEFAULT_MAX_ENTRY_TTL) -> None`; `Env.current_contract_address(self) -> Address`; `Ledger.version(self) -> U32`, `Ledger.network_id(self) -> Bytes32`, `Ledger.max_live_until_ledger(self) -> U32`.
- Produces on the value layer: `String.to_bytes(self) -> Bytes`, `Bytes.to_string(self) -> String`.
- Consumes: `serpent._strkey.encode`, `serpent._strkey.VERSION_CONTRACT` (C15); `serpent.env._require_frame`; `_TtlState.sequence`.

- [ ] **Step 1: `Bytes65` and the conversions, failing first**

Add to `tests/unit/test_buffers.py` (or create `tests/unit/test_env_leaf_surface.py` and put these there; either is fine as long as they run):

```python
def test_bytes65_is_a_real_class_with_the_length_invariant() -> None:
    """E4/K19: `bytes_n(65)` compiles as an annotation but fails `mypy
    --strict` with `[valid-type]`, and a module-level `Bytes65 = bytes_n(65)`
    is SPT3014 at the frontend, so the recovered secp256k1 key's type has to
    be a real `class` statement -- exactly why `Bytes32`/`Bytes64` are."""
    from serpent import Bytes, Bytes65, bytes_n

    assert issubclass(Bytes65, Bytes)
    assert Bytes65._LENGTH == 65
    assert bytes_n(65) is Bytes65
    assert Bytes65(bytes(65)).data == bytes(65)
    with pytest.raises(ValueError):
        Bytes65(bytes(64))


def test_string_round_trips_through_bytes_for_arbitrary_payloads() -> None:
    """E7/F6: the host's `ScString` is a byte string it never validates as
    UTF-8 (K9), so `bytes_to_string(string_to_bytes(s)) == s` must hold for
    every byte -- which at tier 1 means `surrogateescape` in both directions.
    A strict decode would invent a trap the host does not have; a "replace"
    decode would silently corrupt."""
    from serpent import Bytes

    payload = Bytes(bytes(range(256)))
    assert payload.to_string().to_bytes() == payload


def test_a_string_holding_surrogate_escapes_still_orders_by_its_bytes() -> None:
    from serpent import Bytes, String

    low = Bytes(bytes([0x00])).to_string()
    high = Bytes(bytes([0xFF])).to_string()
    assert low < high
    assert String("a").to_bytes() == Bytes(b"a")
```

Run: `uv run --no-sync pytest -q tests/unit/test_buffers.py -k "bytes65 or round_trips or surrogate"` -> FAIL (`ImportError: cannot import name 'Bytes65'`).

- [ ] **Step 2: the value-layer edits**

In `src/serpent/types/buffers.py`, after `class Bytes64` (line ~198), add:

```python
class Bytes65(Bytes):
    """Exactly 65 bytes: a SEC-1 uncompressed secp256k1 public key.

    The shape `env.crypto().secp256k1_recover(...)` returns (`0x04` followed by
    the 32-byte `x` and 32-byte `y`). A real `class` statement, like its two
    siblings and for the same reason: a name bound to a factory call is a
    VARIABLE and `k: Bytes65` would not type-check under `mypy --strict`.
    """

    __slots__ = ()

    _LENGTH: ClassVar[int | None] = 65
```

Change the cache line to `_BYTES_N_CACHE: dict[int, type[Bytes]] = {32: Bytes32, 64: Bytes64, 65: Bytes65}`, extend the comment above it to name `Bytes65`, and replace `bytes_n`'s stale sentence

```
    Annotating an arbitrary length awaits compiler support in sub-plan C.
```

with (E4's "corrected in passing"; the claim is stale -- `types_.py:510-515` already resolves an arbitrary length, K19):

```
    An arbitrary length IS resolvable as an annotation by the compiler, but
    only through a name the module already binds: `bytes_n(48)` written inline
    in an annotation fails `mypy --strict` with `[valid-type]`, and a
    module-level `B48 = bytes_n(48)` is SPT3014, so a contract annotates with
    `Bytes32`, `Bytes64`, or `Bytes65`.
```

In `class String`, replace the constructor's validation and `_order_key` so the chain's byte-string semantics survive (E7):

```python
    def __init__(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError(f"String() takes a str, not {type(text).__name__}")
        try:
            text.encode("utf-8", "surrogateescape")
        except UnicodeEncodeError as exc:
            raise ValueError(f"String has no on-chain byte form: {text!r}") from exc
        object.__setattr__(self, "_payload", text)

    def _order_key(self) -> bytes:
        return self._payload.encode("utf-8", "surrogateescape")

    def to_bytes(self) -> Bytes:
        """`string_to_bytes` (`b.n`, protocol 23): the same payload, re-tagged.

        The host copies the bytes and changes the `ScVal` case; it validates
        nothing (K9). Tier 1 carries those bytes in a Python `str` through
        `surrogateescape`, so this round-trips exactly for every input,
        including bytes that are not valid UTF-8.
        """
        return Bytes(self._order_key())
```

**Why the widening, and what it costs.** `String` used to refuse a lone surrogate, on the reasoning that such text "has no on-chain representation". That reasoning is backwards for the chain: `ScString` is a byte string with no UTF-8 validation at all, so the host accepts payloads this class refused -- serpent's model was NARROWER than the host on a value constructor, which is a false reject. `surrogateescape` maps each undecodable byte to a lone surrogate and back to exactly that byte, so the widened class accepts precisely the byte strings the host accepts and orders them by the same bytes. The only text still refused is a `str` containing a surrogate that `surrogateescape` cannot encode (a lone HIGH surrogate outside the `\udc80`-`\udcff` range), which no decode of any byte string can produce.

**Also correct `buffers.py:37`** [m1], the class docstring one line above `__init__` (`:45`), which still says a lone surrogate "has no on-chain representation". After this change that sentence is true only of the HIGH-surrogate half; reword it to say so, naming the `\udc80`-`\udcff` range as the half that now round-trips. The v1 File Structure table put `String` in the `:180-237` block; it is at `:32-70`, which is why this correction is easy to miss.

In `class Bytes`, add beside `slice`:

```python
    def to_string(self) -> String:
        """`bytes_to_string` (`b.o`, protocol 23): the same payload, re-tagged.

        Decoded with `surrogateescape` so arbitrary bytes survive the trip
        (E7): the host does no UTF-8 validation, and a strict decode here would
        invent a trap the chain does not have.
        """
        return String(self._payload.decode("utf-8", "surrogateescape"))
```

(`String` is defined above `Bytes` in this module, so no forward reference is needed; `Bytes` is referenced from `String.to_bytes` by name at call time, which is fine under `from __future__ import annotations`.)

In `src/serpent/__init__.py`, add `Bytes65` to the `from serpent.types import (...)` block and to `__all__` (after `Bytes64`, preserving RUF022's ordering). Re-export it from `src/serpent/types/__init__.py` the same way `Bytes64` is.

In `tests/unit/test_public_api.py`, add `"Bytes65",` to `EXPECTED_ALL` after `"Bytes64",`. **This file is edited in this task and in no other** (Global Constraints).

In `src/serpent/testing/_scval.py`, change the two `String` sites so the marshalling matches:

```python
    if isinstance(value, String):
        return scval.to_string(value.text.encode("utf-8", "surrogateescape"))
```
```python
def _string(sc: SCVal) -> String:
    return String(scval.from_string(sc).decode("utf-8", "surrogateescape"))
```

(`stellar_sdk.scval.to_string` accepts `bytes` as well as `str`; if it does not on the pinned version, build the `SCVal` directly with `SCVal(SCValType.SCV_STRING, str=SCString(payload))` and say so in the task report.)

Run: `uv run --no-sync pytest -q tests/unit/test_buffers.py tests/unit/test_public_api.py` -> pass.

- [ ] **Step 3: the four `Env` facts and `Ledger`'s three readers, failing first**

Create `tests/unit/test_env_leaf_surface.py`:

```python
"""Tier 1: the leaf Env facts M2-A adds -- contract identity, the three ledger
accessors, and the `String`/`Bytes` conversions.

Every reader is `_require_frame`-gated like `timestamp()`/`sequence()`, for
`Ledger`'s own stated reason: on chain each is a host function, and a read
with no invocation open is a state the chain cannot produce.
"""

from __future__ import annotations

import pytest

from serpent import U32, Bytes32, Env
from serpent import _strkey
from serpent.env import (
    DEFAULT_CONTRACT_ADDRESS,
    DEFAULT_LEDGER_SEQUENCE,
    DEFAULT_MAX_ENTRY_TTL,
    DEFAULT_NETWORK_ID,
    DEFAULT_PROTOCOL_VERSION,
    deploy,
)
from tests.unit.test_emitter_end_to_end import EXAMPLE_COUNTER
from tests.unit.test_examples import load_example

counter = load_example(EXAMPLE_COUNTER)


def _deployed(contract_address: str = DEFAULT_CONTRACT_ADDRESS) -> tuple[Env, object]:
    """A deployed contract, so the frame gate can open at all.

    `examples/counter.py` on purpose: it has NO `__init__`, so `deploy` takes
    no constructor arguments and nothing here depends on a fixture's signature.
    Which contract is deployed is irrelevant -- every reader under test is a
    LEDGER or CONTEXT fact, not a contract's.
    """
    env = Env(contract_address=contract_address)
    return env, deploy(counter.Counter, env)


def test_current_contract_address_is_the_env_it_was_deployed_into() -> None:
    """E5: the differential asserts the INVARIANT (`current_contract_address()`
    equals the address this env deployed at), never an sdk-derived literal."""
    env, _ = _deployed()
    with env.frame():
        assert env.current_contract_address().strkey == DEFAULT_CONTRACT_ADDRESS
    # And the keyword is what moves it: a second Env built at a DIFFERENT
    # address answers that one, which is what makes the constructor argument
    # (rather than a hardcoded constant) the source of truth.
    elsewhere = _strkey.encode(_strkey.VERSION_CONTRACT, bytes([7]) * 32)
    env2, _ = _deployed(contract_address=elsewhere)
    with env2.frame():
        assert env2.current_contract_address().strkey == elsewhere


def test_the_ledger_readers_answer_the_envs_own_configuration() -> None:
    env, _ = _deployed()
    with env.frame():
        assert env.ledger().version() == U32(DEFAULT_PROTOCOL_VERSION)
        assert env.ledger().network_id() == Bytes32(DEFAULT_NETWORK_ID)
        assert isinstance(env.ledger().network_id(), Bytes32)


def test_max_live_until_is_sequence_plus_max_entry_ttl_minus_one() -> None:
    """K6, verbatim from `ledger_info.rs:25-30`:
    `sequence_number.checked_add(max_entry_ttl.saturating_sub(1))`. A closed
    form, so tier 1 reproduces it exactly -- no guessing (contra the M1-E D4
    excuse, which this accessor retires)."""
    env, _ = _deployed()
    with env.frame():
        expected = DEFAULT_LEDGER_SEQUENCE + DEFAULT_MAX_ENTRY_TTL - 1
        assert env.ledger().max_live_until_ledger() == U32(expected)


def test_max_live_until_moves_with_the_ledger_sequence() -> None:
    """Read LIVE off `_TtlState`, exactly as `sequence()` is: a `Ledger` bound
    before an `advance` must not keep answering the pre-advance number."""
    env, _ = _deployed()
    with env.frame():
        ledger = env.ledger()
        before = ledger.max_live_until_ledger()
    env.advance(10)
    with env.frame():
        assert ledger.max_live_until_ledger() == U32(before.value + 10)


def test_every_new_reader_refuses_outside_a_frame() -> None:
    env, _ = _deployed()
    for read in (
        lambda: env.current_contract_address(),
        lambda: env.ledger().version(),
        lambda: env.ledger().network_id(),
        lambda: env.ledger().max_live_until_ledger(),
    ):
        with pytest.raises(RuntimeError, match="invocation frame"):
            read()


def test_a_max_live_until_that_would_overflow_a_u32_is_refused_loudly() -> None:
    """K6: the host reports `Error(Context, InternalError)` and calls it "a
    misconfiguration of the network". Tier 1 refuses the Env at construction
    instead, so the bad configuration is named where it is made rather than
    two calls later."""
    with pytest.raises(ValueError, match="max_live_until"):
        Env(sequence=2**32 - 2, max_entry_ttl=DEFAULT_MAX_ENTRY_TTL)


def test_the_defaults_are_the_ones_the_real_host_is_fed() -> None:
    """D6/E5: ONE shared home. `serpent.testing._real` IMPORTS these rather
    than restating them, so a tier-1 answer and a real-host answer cannot drift
    apart through two copies of a constant."""
    from serpent.testing import _real

    assert _real.DEFAULT_NETWORK_ID is DEFAULT_NETWORK_ID
    assert _real.DEFAULT_MAX_ENTRY_TTL == DEFAULT_MAX_ENTRY_TTL
    assert _real.DEFAULT_PROTOCOL == DEFAULT_PROTOCOL_VERSION
```

Run -> FAIL (`ImportError: cannot import name 'DEFAULT_CONTRACT_ADDRESS'`).

- [ ] **Step 4: `serpent/env.py`**

Beside `DEFAULT_LEDGER_TIMESTAMP`/`DEFAULT_LEDGER_SEQUENCE` (env.py:241-242), add:

```python
#: The network id the model reports (`get_ledger_network_id`, always 32 bytes).
#: All zeros, because that is what `serpent.testing._real` already feeds the
#: embedded host -- ONE definition, not a third copy (D6/E5). It is NOT any
#: live network's id: a tier-1 run is not on a network, and a plausible-looking
#: testnet id would invite a contract to branch on it.
DEFAULT_NETWORK_ID = bytes(32)

#: `LedgerInfo.max_entry_ttl`, matching the embedded host's configuration.
DEFAULT_MAX_ENTRY_TTL = 6_312_000

#: The protocol `get_ledger_version` reports. Pinned to the MAJOR of the
#: embedded host's release line, which `serpent.testing._real` imports.
DEFAULT_PROTOCOL_VERSION = 28

#: The contract identity `env.current_contract_address()` answers. Synthesised
#: once from a documented 32-byte id through the internal strkey codec, so it
#: is a GENUINE `C...` address (it decodes, it compares, it can be a Map key)
#: with no dependency and no pretence of being a deployed contract. A test that
#: needs a different one passes `Env(contract_address=...)`.
DEFAULT_CONTRACT_ADDRESS = _strkey.encode(_strkey.VERSION_CONTRACT, bytes(range(32)))
```

Measured 2026-09-11, so the implementer and the reviewer are looking at the
same string: that expression evaluates to
`CAAACAQDAQCQMBYIBEFAWDANBYHRAEISCMKBKFQXDAMRUGY4DUPB6N4O`, which `Address()`
parses as a contract address. Do not paste the literal into the source -- the
derivation is what documents where the 32 bytes came from.

(add `from serpent import _strkey` to the imports; it is a leaf module with no serpent imports, so there is no cycle.)

In `Ledger`, after `sequence()`:

```python
    def version(self) -> U32:
        """The ledger protocol version (`get_ledger_version`, `x.2`).

        A `U32Val` on the host, so a `U32` here -- serpent does not reinterpret
        an `ScVal` case (the same reasoning `timestamp()` gives for `U64`).
        """
        _require_frame(self._env, "a ledger version read")
        return U32(self._protocol_version)

    def network_id(self) -> Bytes32:
        """The network id: sha256 of the network passphrase (`x.6`).

        `Bytes32`, not `Bytes`: env.json's own doc says the value "is always 32
        bytes in length" and soroban-sdk returns `BytesN<32>` (E7).
        """
        _require_frame(self._env, "a ledger network id read")
        return Bytes32(self._network_id)

    def max_live_until_ledger(self) -> U32:
        """The highest ledger an entry may live to, inclusive (`x.8`).

        `sequence + max_entry_ttl - 1`, the host's own closed form
        (`ledger_info.rs:25-30`), computed off the LIVE `_TtlState` sequence
        exactly as `sequence()` is -- so an `advance` moves this number too.

        **This is the ACCESSOR and nothing more.** The TTL clamp, the temporary
        bucket's trap, and the per-bucket minimum floors are M2-D's; tier 1
        still applies `extend_to` exactly as given at any magnitude, and
        `tests/semantics/host_facts.py` says so in three rows.
        """
        _require_frame(self._env, "a ledger max live-until read")
        return U32(self._ttl.sequence + self._max_entry_ttl - 1)
```

`Ledger.__slots__` becomes `("_env", "_max_entry_ttl", "_network_id", "_protocol_version", "_timestamp", "_ttl")` and `__init__` takes the three new values from the `Env`. `Env.ledger()` passes them.

`Env.__slots__` gains `"_contract_address"`, `"_max_entry_ttl"`, `"_network_id"`, `"_protocol_version"` (keep the tuple sorted). `Env.__init__` gains the four keywords with the constants as defaults and validates them where a bad value is a configuration mistake rather than a contract one:

```python
        self._contract_address = Address(contract_address)
        if len(network_id) != 32:
            raise ValueError(f"network_id is exactly 32 bytes, not {len(network_id)}")
        self._network_id = bytes(network_id)
        self._protocol_version = protocol_version
        self._max_entry_ttl = max_entry_ttl
        # K6: the host treats an overflow here as a NETWORK misconfiguration
        # (`Error(Context, InternalError)`, "a logic bug on our part"), not as
        # something a contract can provoke -- so it is refused where the
        # configuration is made, not on the read.
        if not 0 <= sequence + max_entry_ttl - 1 <= 0xFFFFFFFF:
            raise ValueError(
                f"max_live_until would overflow a u32: sequence={sequence} + "
                f"max_entry_ttl={max_entry_ttl} - 1"
            )
```

`Address(contract_address)` is what rejects a `G...` or malformed strkey, with the codec's own message.

And, beside `events()`:

```python
    def current_contract_address(self) -> Address:
        """The address of the contract this invocation is running (`x.7`).

        On chain the only failure is "Current context has no contract ID",
        which the host's own comment calls "a logic bug on our part" and which
        is unreachable from inside a contract (K8). `_require_frame` is the
        tier-1 analogue: outside an invocation there is no contract to name.
        """
        _require_frame(self, "env.current_contract_address()")
        return self._contract_address
```

In `src/serpent/testing/_real.py`, delete the three redeclared constants and import them (E5):

```python
from serpent.env import (
    DEFAULT_LEDGER_SEQUENCE,
    DEFAULT_LEDGER_TIMESTAMP,
    DEFAULT_MAX_ENTRY_TTL,
    DEFAULT_NETWORK_ID,
    DEFAULT_PROTOCOL_VERSION as DEFAULT_PROTOCOL,
)
```

Keep the name `DEFAULT_PROTOCOL` bound in `_real`'s namespace (it is imported by `tests/real_host/*` and by `test_testnet_fixtures.py`), and keep `DEFAULT_MIN_TEMP_ENTRY_TTL`/`DEFAULT_MIN_PERSISTENT_ENTRY_TTL`/`DEFAULT_BASE_RESERVE` local -- those are knobs the tier-1 model has no opinion about, and D's row is where they get one. Update the comment block above them to say which three moved and why.

Run: `uv run --no-sync pytest -q tests/unit/test_env_leaf_surface.py` -> `7 passed`.

- [ ] **Step 5: Gates and commit**

The four gates. Expected: `4849 passed / 7 skipped` [M8] (the M8-corrected v1 figure is 4839; v2's Task 1 amendments carry +10 forward -- see Task 1 Step 5's arithmetic). Report the actual numbers and any test elsewhere that moved (a `String`-strictness test asserting the OLD refusal is a real finding: report it, and change it only to the new documented behaviour).

```bash
git add src/serpent/types/buffers.py src/serpent/types/__init__.py src/serpent/__init__.py \
        src/serpent/env.py src/serpent/testing/_real.py src/serpent/testing/_scval.py \
        tests/unit/test_public_api.py tests/unit/test_buffers.py tests/unit/test_env_leaf_surface.py
git commit -m "feat(env): give the tier-1 model contract identity and the ledger facts

Env gains contract_address, network_id, protocol_version, and max_entry_ttl
keywords over four module-level defaults, and serpent.testing._real imports
three of them instead of restating them. Ledger gains version(),
network_id(), and max_live_until_ledger() (sequence + max_entry_ttl - 1, the
host's own closed form); Env gains current_contract_address(). The accessor
only: the TTL clamp, the trap, and the per-bucket floors stay in M2-D.

Bytes65 joins the public surface for the recovered secp256k1 key, and String
now carries arbitrary chain bytes through surrogateescape so
bytes_to_string/string_to_bytes round-trip exactly, as they do on the host."
```

**Report:** the gate outputs; the counts; whether `stellar_sdk.scval.to_string` accepted `bytes` (Step 2's conditional); every pre-existing test that changed behaviour because of the `String` widening, quoted; any deferred minor; any BLOCKED condition.

---

### Task 3: the `Crypto`, `Prng`, and `Logs` facades, and the two trap classes

**Files:**
- Modify: `src/serpent/env.py:215-232` (the module `__all__`, which starts at `:215` [m10]), `:640-700` (beside `StorageTrap`, which is at `:642`), `:1386-1460` (the three new classes), `:1481-1620` (`Env`'s accessors, `recorded_logs`, and **`_invocation` at `:1568`** -- v1's table stopped at `:1560`, mid-docstring of `frame()` [m2])
- Create: `tests/unit/test_env_crypto_prng_logs.py`
- Test: the new file, plus every existing `tests/unit/test_env*.py`

**Interfaces:**
- Consumes: `serpent._crypto` (all five), `serpent._prng` (`from_prng_seed`, `u64_in_inclusive_range`, `shuffle`, `SEED_BYTES`), `_require_frame`, `Bytes`/`Bytes32`/`Bytes65`/`U64`/`Vec`.
- Produces: `class CryptoTrap(RuntimeError)`, `class PrngTrap(RuntimeError)` -- siblings of the existing `StorageTrap` (`env.py:642`), with NO shared base class [B1]; `class Crypto` with `sha256(self, data: Bytes) -> Bytes32`, `keccak256(self, data: Bytes) -> Bytes32`, `ed25519_verify(self, public_key: Bytes, message: Bytes, signature: Bytes) -> None`, `secp256k1_recover(self, digest: Bytes, signature: Bytes, recovery_id: U32) -> Bytes65`, `secp256r1_verify(self, public_key: Bytes, digest: Bytes, signature: Bytes) -> None`; `class Prng` with `seed(self, s: Bytes) -> None`, `bytes_new(self, length: U32) -> Bytes`, `u64_in_range(self, lo: U64, hi: U64) -> U64`, `shuffle(self, v: Vec[_T]) -> Vec[_T]`; `class Logs` with `add(self, message: str, *values: ChainValue) -> None`; `Env.crypto(self) -> Crypto`, `Env.prng(self) -> Prng`, `Env.logs(self) -> Logs`, `Env.recorded_logs` (a property, `tuple[tuple[str, tuple[ChainValue, ...]], ...]`).

- [ ] **Step 1: the facade tests, failing first**

Create `tests/unit/test_env_crypto_prng_logs.py` with these tests (full bodies; `_deployed()` is Task 2's helper, copied into this file so the two modules do not couple):

```python
"""Tier 1: `env.crypto()`, `env.prng()`, and `env.logs()`.

The arithmetic lives in `serpent._crypto`/`serpent._prng` and is pinned
against the host's own vectors there; what THIS file pins is the FACADE --
the frame gate, the chain types in and out, the two trap classes, the
frame-local PRNG lifetime, and the one rule that makes logging safe: a log
can never fail.
"""
```

1. `test_every_crypto_reader_refuses_outside_a_frame` -- all five, `pytest.raises(RuntimeError, match="invocation frame")`.
2. `test_sha256_and_keccak256_return_bytes32` -- `isinstance(..., Bytes32)` and the value equals `_crypto.sha256(...)`.
3. `test_ed25519_verify_returns_none_on_success` -- `assert env.crypto().ed25519_verify(k, m, s) is None` with Task 1's TEST 2 vector.
4. `test_a_failed_ed25519_verification_raises_crypto_trap` -- `pytest.raises(CryptoTrap)`, and `assert not isinstance(exc.value, ContractError)`.
5. `test_crypto_trap_is_not_a_contract_error_and_carries_no_code` -- `issubclass(CryptoTrap, RuntimeError)`, `not issubclass(CryptoTrap, ContractError)`, `not hasattr(CryptoTrap, "code")`.
6. `test_secp256k1_recover_returns_bytes65` -- Task 1's go-ethereum vector, `isinstance(..., Bytes65)`.
7. `test_a_failed_recovery_raises_crypto_trap` -- the high-`s` signature.
8. `test_secp256r1_verify_returns_none_and_traps_on_failure`.
9. `test_a_seed_that_is_not_32_bytes_raises_prng_trap` -- the message carries `Value(UnexpectedSize)`, the host's own words (K13.4).
10. `test_an_inverted_range_raises_prng_trap` -- the message carries `Value(InvalidInput)` (K13.5).
11. `test_a_reseeded_draw_is_the_hosts_own_vector` -- `env.prng().seed(Bytes(bytes([1]) * 32))` then `bytes_new(U32(32))` equals K15's 32 bytes; and `u64_in_range(U64(0), U64(2**64 - 1))` after a fresh reseed equals `8478755077819529274`.
12. `test_the_full_range_draw_is_expressible_and_exact` -- F16 at tier 1.
13. `test_shuffle_returns_a_new_vec_and_does_not_mutate` -- `Vec` in, a new `Vec` out, the input unchanged.
14. `test_the_frame_prng_is_destroyed_at_frame_exit` -- reseed, draw, leave the frame, enter a new one, draw again: the second draw is the UNSEEDED stream, not a continuation.
15. `test_the_unseeded_stream_is_deterministic_but_documented_as_not_the_chains` -- two fresh `Env`s give the same first draw, and the docstring of `Prng` says so.
16. `test_a_log_records_and_never_traps` (F7) -- `env.logs().add("drew", U32(1))`, then `env.recorded_logs == (("drew", (U32(1),)),)`; and a log with ZERO values, and a log with twelve values, both succeed.
17. `test_a_log_outside_a_frame_still_refuses` -- the frame gate is serpent's, not the host's; a log outside an invocation is a test-authoring mistake, and refusing it is the same `_require_frame` rule every other accessor has.
18. `test_recorded_logs_is_a_deep_copy` -- mutating a returned `Vec` does not change the record.

- [ ] **Step 2: the two trap classes**

In `src/serpent/env.py`, immediately after `class StorageTrap`, add:

```python
class CryptoTrap(RuntimeError):
    """A crypto host function the real host REFUSES, mirrored at tier 1.

    `StorageTrap`'s sibling, and shaped the same way for the same reason: the
    host TRAPS these calls, so this is a plain loud `RuntimeError` with NO
    reserved code rather than a `ContractError`. Giving it a code would put a
    number in a tier-1 trace that no on-chain trace can contain, and it would
    teach an author to write `except ...` around a verification whose failure
    kills the whole invocation on chain.

    **This is why the two verifies return `None` and never `Bool`.** A
    `-> Bool` surface would let a contract write `if not
    env.crypto().ed25519_verify(...)`, which compiles to a branch that can
    never be taken: `verify_sig_ed25519` returns `Void`, and every failure is
    `Error(Crypto, InvalidInput)` or `Error(Object, UnexpectedSize)` (K10).

    The message carries the host's own `(ScErrorType, ScErrorCode)` words so a
    tier-1 failure and a real-host failure read as the same event. Note the
    asymmetry those words record and this model does not smooth over: a wrong
    LENGTH on a value the host reads through `fixed_length_bytes_from_bytesobj_input`
    is `Object(UnexpectedSize)`, while a wrong length or a wrong value on
    anything the crypto crates parse is `Crypto(InvalidInput)`.

    Not in `_LAUNDERED_BY_THE_HOST`, for `AuthorizationFailed`'s reason: S12's
    laundering covers RECOVERABLE errors, and a trap is not one. Whether a
    `Crypto` error can be observed as a recoverable value across a `try_call`
    frame boundary is UNVERIFIED and is M2-B's first probe.
    """


class PrngTrap(RuntimeError):
    """A PRNG host function the real host REFUSES, mirrored at tier 1.

    `CryptoTrap`'s sibling, with the same no-reserved-code rationale. Exactly
    two refusals exist, both read from the host:

    * `Value(UnexpectedSize)` -- `prng_reseed` with anything but 32 bytes
      (`host.rs:3734-3763`);
    * `Value(InvalidInput)` -- `prng_u64_in_inclusive_range` with `lo > hi`
      (`prng.rs`, "rand::Uniform panics if start > end").

    `prng_bytes_new` and `prng_vec_shuffle` have no refusal of their own.
    """
```

Add `"CryptoTrap"`, `"PrngTrap"`, **and the three facade class names `"Crypto"`, `"Prng"`, `"Logs"`** to `env.py`'s module `__all__` -- which is at `:215-227`, not `:216-227` [m10] -- keeping it sorted. The facade classes belong there for the reason the existing list already implies: `Events`, `Ledger`, `Storage`, and the three bucket classes are all in it, and Task 12 adds an mkdocstrings entry for each of the three new ones, which mkdocstrings resolves through `__all__`. The five names are deliberately NOT in `serpent.__all__`: like `StorageTrap`, they are reached from `serpent.env`, which is where the tier-1 model lives, and `serpent.__all__` is the AUTHORING surface a compiled contract resolves names against (it stays frozen at 41).

**No shared base class** [B1]. The review's cheaper option was a `class HostTrap(RuntimeError)` base with the three traps as subclasses; the controller ruled for the TUPLE instead -- `tests/unit/test_env_differential.py`'s tier-1 arm catches `(StorageTrap, CryptoTrap, PrngTrap)` explicitly (Task 10). Adding a base would change `StorageTrap`'s MRO and its published meaning for an existing surface, which is a bigger edit than the three-name tuple it saves. If M2-B wants a fourth trap family it adds a fourth name to that tuple, and the plan for B decides then whether a base has earned itself.

- [ ] **Step 3: the three facades**

After `class Events` in `env.py`, add:

```python
class Crypto:
    """`env.crypto()`: the five non-ZK crypto host functions.

    Every one is a LEAF -- bytes in, one host call, a value out -- and every
    one is gated on the invocation frame like the rest of the Env surface.
    The arithmetic is `serpent._crypto`'s, shared with the mini host so the
    two tiers cannot disagree by construction (S6).

    The ZK families (bls12-381, bn254, poseidon) are deliberately absent: they
    are M3-or-later, and this surface is shaped so adding them is additive.
    """

    __slots__ = ("_env",)

    def __init__(self, env: Env) -> None:
        self._env = env

    def sha256(self, data: Bytes) -> Bytes32:
        """SHA-256 of `data` (`c._`).

        `Bytes32`, not a `Hash` newtype: Rust returns `Hash<32>` to mark "this
        came from a secure hash", and serpent has no such wrapper. The
        invariant is documented rather than typed.
        """
        _require_frame(self._env, "env.crypto().sha256()")
        return Bytes32(_crypto.sha256(data.data))

    def keccak256(self, data: Bytes) -> Bytes32:
        """Keccak-256 of `data` (`c.1`) -- the ORIGINAL padding, not SHA3's."""
        _require_frame(self._env, "env.crypto().keccak256()")
        return Bytes32(_crypto.keccak256(data.data))

    def ed25519_verify(self, public_key: Bytes, message: Bytes, signature: Bytes) -> None:
        """Verify an ed25519 signature (`c.0`), or TRAP.

        Returns nothing on success and raises `CryptoTrap` on every failure,
        because that is what the host does: `verify_sig_ed25519` returns `Void`
        and a bad signature kills the invocation. There is no boolean here to
        branch on, by design.

        This is `verify_strict`: a non-canonical scalar and a small-order key
        or `R` are refused, while a non-canonical `y` encoding is accepted and
        reduced (K11).
        """
        _require_frame(self._env, "env.crypto().ed25519_verify()")
        if not _crypto.ed25519_verify(public_key.data, message.data, signature.data):
            raise CryptoTrap(
                "Crypto(InvalidInput): failed ED25519 verification. The host traps on a "
                "bad signature, a key that is not 32 bytes or not a point, a signature "
                "that is not 64 bytes (which it reports as Object(UnexpectedSize)), a "
                "non-canonical scalar, and a small-order key or R."
            )

    def secp256k1_recover(self, digest: Bytes, signature: Bytes, recovery_id: U32) -> Bytes65:
        """Recover the secp256k1 public key that signed `digest` (`c.2`).

        Returns the 65-byte SEC-1 UNCOMPRESSED key; raises `CryptoTrap` for a
        malformed or high-`s` signature, a recovery id above 3, a digest that
        is not 32 bytes, or a recovery that fails.

        **The message digest must be produced by a secure cryptographic hash
        function; otherwise an attacker can potentially forge signatures.**
        This is the HAZMAT shape: Rust's safe `Crypto::secp256k1_recover` takes
        a `Hash<32>` newtype to enforce it and serpent has no such type, so the
        obligation is the caller's. `env.crypto().sha256(...)` produces a
        suitable digest.
        """
        _require_frame(self._env, "env.crypto().secp256k1_recover()")
        recovered = _crypto.secp256k1_recover(digest.data, signature.data, recovery_id.value)
        if recovered is None:
            raise CryptoTrap(
                "Crypto(InvalidInput): ECDSA-secp256k1 key recovery failed. The host "
                "refuses a signature that is not 64 bytes or whose 's' part is not "
                "normalized to low form, a recovery_id above 3, a digest that is not 32 "
                "bytes (Object(UnexpectedSize)), and a recovery with no answer."
            )
        return Bytes65(recovered)

    def secp256r1_verify(self, public_key: Bytes, digest: Bytes, signature: Bytes) -> None:
        """Verify a P-256 signature over a prehashed message (`c.3`), or TRAP.

        `public_key` is 65 SEC-1 UNCOMPRESSED bytes: the host checks the `0x04`
        tag before parsing, so a valid COMPRESSED key is refused. The same
        high-`s` refusal as secp256k1 applies. Protocol 21 and above; the
        default build target is 27, so no gate fires (E8).
        """
        _require_frame(self._env, "env.crypto().secp256r1_verify()")
        if not _crypto.secp256r1_verify(public_key.data, digest.data, signature.data):
            raise CryptoTrap(
                "Crypto(InvalidInput): failed secp256r1 verification. The host refuses a "
                "public key whose SEC-1 tag is not 0x04, a signature that is malformed "
                "or high-s, a digest that is not 32 bytes (Object(UnexpectedSize)), and "
                "a signature that does not verify."
            )


class Prng:
    """`env.prng()`: the frame-local pseudo-random number generator.

    **What is exact, and what is not.** After `seed(b)` this model produces
    the IDENTICAL stream the chain produces, value for value, because
    `prng_reseed` REPLACES the frame PRNG with `ChaCha20Rng::from_seed(
    HMAC_SHA256(protocol_salt, b))` -- so every subsequent draw is a pure
    function of the 32 seed bytes. That is a genuinely useful property, and it
    is what makes a commit-reveal contract testable in the innermost dev loop.

    BEFORE any reseed this model uses its own documented constant seed, and
    **those draws are not the chain's draws**. On chain the frame PRNG is
    derived from a base PRNG the embedder seeds -- in stellar-core, from the
    txset hash and the transaction's apply-order position -- which serpent
    cannot know and must not pretend to. No tier pins an unseeded draw
    anywhere; `tests/semantics/env_scenarios.py` carries a `host_diverges` row
    that DECLARES the difference rather than leaving a test to discover it.

    **Lifetime.** The host gives every FRAME its own PRNG and destroys it with
    the frame, so a reseed does not survive an invocation -- AND the parent's
    PRNG is untouched by the child's existence. This model does the same, as a
    STACK: `Env._invocation` saves the outer generator, installs a fresh one,
    and restores the outer one in `finally` [M3]. Note for M2-B, which owns
    tier-1 frame rollback: this is the only frame-scoped state M2-A adds, it
    is a save/restore stack discipline rather than a clobber, and the rollback
    design folds it in as such.
    """

    __slots__ = ("_env",)

    def __init__(self, env: Env) -> None:
        self._env = env

    def seed(self, s: Bytes) -> None:
        """Replace this frame's PRNG with one seeded by `s` (`p._`).

        Exactly 32 bytes; anything else is a `PrngTrap`.
        """
        _require_frame(self._env, "env.prng().seed()")
        if len(s.data) != _prng.SEED_BYTES:
            raise PrngTrap(
                f"Value(UnexpectedSize): Unexpected size of BytesObject in prng_reseed: "
                f"prng_reseed takes exactly {_prng.SEED_BYTES} bytes, not {len(s.data)}."
            )
        self._env._prng_state = _prng.from_prng_seed(s.data)

    def bytes_new(self, length: U32) -> Bytes:
        """`length` pseudo-random bytes (`p.0`)."""
        _require_frame(self._env, "env.prng().bytes_new()")
        return Bytes(self._env._prng_state.fill_bytes(length.value))

    def u64_in_range(self, lo: U64, hi: U64) -> U64:
        """A `u64` uniform on the INCLUSIVE range `[lo, hi]` (`p.1`).

        Inclusive at both ends, matching the host function's own name and
        semantics. A full-range draw is `u64_in_range(U64(0), U64(2**64 - 1))`;
        there is no second spelling for it.

        `lo > hi` is a `PrngTrap`.
        """
        _require_frame(self._env, "env.prng().u64_in_range()")
        if lo.value > hi.value:
            raise PrngTrap(
                f"Value(InvalidInput): prng_u64_in_inclusive_range needs lo <= hi, got "
                f"lo={lo.value} hi={hi.value}."
            )
        return U64(_prng.u64_in_inclusive_range(self._env._prng_state, lo.value, hi.value))

    def shuffle(self, v: Vec[_T]) -> Vec[_T]:
        """A Fisher-Yates shuffle of `v` (`p.2`), as a NEW `Vec`.

        The host's op is FUNCTIONAL -- it clones and returns a fresh
        `VecObject` -- so the result must be rebound: `v = env.prng().shuffle(v)`.
        A shuffle whose result is discarded is `SPT1034` at compile time.
        """
        _require_frame(self._env, "env.prng().shuffle()")
        return Vec(v.element_type, _prng.shuffle(self._env._prng_state, list(v)))


class Logs:
    """`env.logs()`: diagnostic output (`log_from_linear_memory`, `x._`).

    **A log can never fail.** The host wraps the whole body in
    `with_debug_mode` and then unconditionally returns `Ok(Val::VOID)`: a bad
    pointer, a bad length, a forged `Val` -- all swallowed. With diagnostics
    off (the chain default outside a diagnostic run) the body does not execute
    at all, so the message bytes are never even read, and the budget charged is
    the SHADOW budget. This model must therefore never invent a failure the
    chain does not have, which is why `add` validates nothing about its values.

    A log is cheap but not free: one interned pool entry per distinct message
    (paid once, at build time), `8 * n` bytes of scratch reserved permanently
    for the call SITE, `n` stores, and one host call at roughly 74 instructions
    of fixed overhead.

    `add`, not `log`: soroban-sdk's `Logs::log` is deprecated in favour of
    `Logs::add`.
    """

    __slots__ = ("_env", "_recorded")

    def __init__(self, env: Env, recorded: list[tuple[str, tuple[ChainValue, ...]]]) -> None:
        self._env = env
        self._recorded = recorded

    def add(self, message: str, *values: ChainValue) -> None:
        """Record a diagnostic message and a sequence of chain values.

        `message` must be a string LITERAL in a compiled contract (SPT1040): it
        is interned into the module's data segment at build time, and a
        computed message would need a different lowering entirely. Tier 1 does
        not enforce the literal rule -- it is a COMPILE-time property, and a
        tier-1 test may pass any `str` -- which is stated here so nobody
        mistakes the tier-1 permissiveness for an accept the compiler shares.
        """
        _require_frame(self._env, "env.logs().add()")
        self._recorded.append((message, copy.deepcopy(values)))
```

- [ ] **Step 4: `Env`'s wiring**

`Env.__slots__` gains `"_logs"` and `"_prng_state"`. In `__init__`:

```python
        self._logs: list[tuple[str, tuple[ChainValue, ...]]] = []
        #: The frame-local PRNG (`Prng`'s docstring). Seeded from a DOCUMENTED
        #: constant rather than entropy, so an unseeded tier-1 draw is
        #: reproducible for a test -- and, because it is not the chain's
        #: derivation, never pinned in any differential.
        self._prng_state = _prng.from_prng_seed(UNSEEDED_PRNG_SEED)
```

with, beside the other constants:

```python
#: What the tier-1 frame PRNG is seeded from before any `env.prng().seed(...)`.
#: A documented constant, NOT entropy: a tier-1 test has to be reproducible.
#: It is deliberately NOT any value the chain uses -- on chain the frame PRNG
#: descends from an embedder seed serpent cannot know -- and no differential
#: pins a draw made before a reseed (E11.4).
UNSEEDED_PRNG_SEED = b"serpent tier-1 unseeded prng!!!\x00"
```

(exactly 32 bytes; assert it in `test_env_crypto_prng_logs.py`.)

Three accessors beside `events()`:

```python
    def crypto(self) -> Crypto:
        _require_frame(self, "env.crypto()")
        return Crypto(self)

    def prng(self) -> Prng:
        _require_frame(self, "env.prng()")
        return Prng(self)

    def logs(self) -> Logs:
        _require_frame(self, "env.logs()")
        return Logs(self, self._logs)
```

In `_invocation` (which is at `env.py:1568`, NOT inside the `:1346-1560` block the v1 table named [m2]), wrap the existing `try`/`finally` so the frame PRNG is SAVED and RESTORED rather than clobbered [M3]:

```python
        token = _frame.enter(self)
        # The host gives every FRAME its own PRNG and destroys it with the
        # frame (K13.2), so a reseed cannot leak out of an invocation -- and
        # the PARENT frame's PRNG is untouched by the child's existence. A
        # reset on entry alone would satisfy the first half and break the
        # second: a nested `with env.frame():` would silently destroy the
        # outer frame's seeded stream and the next outer draw would not be a
        # continuation of it. So this is a stack, saved here and restored
        # below, which also answers the objection a reset-on-EXIT has (it
        # would leave the state of a frame that raised visible to the next
        # one) because the restore is unconditional.
        outer_prng = self._prng_state
        self._prng_state = _prng.from_prng_seed(UNSEEDED_PRNG_SEED)
        try:
            yield
        finally:
            # Unconditional, and a RESTORE rather than a clear, exactly like
            # `_frame.leave(token)` one line down and for the same reason.
            self._prng_state = outer_prng
            _frame.leave(token)
```

**Test 14b, the nested case** [M3], beside Task 3 Step 1's test 14 (which still passes unchanged):

```python
def test_a_nested_frame_does_not_destroy_the_outer_frames_prng() -> None:
    """The host's frame PRNG is a STACK, not a single slot: a child frame gets
    its own and the parent's is untouched (`host.rs`'s frame stack). A reset
    on entry with no restore made `b` below a fresh unseeded draw instead of a
    continuation of the seeded stream -- a divergence the model would have
    introduced, and one M2-B's frame rollback would have inherited.
    """
    env = Env()
    with env.frame():
        env.prng().seed(Bytes(bytes(32)))
        first = env.prng().bytes_new(U32(8))
        with env.frame():
            inner = env.prng().bytes_new(U32(8))
        second = env.prng().bytes_new(U32(8))

    replay = Env()
    with replay.frame():
        replay.prng().seed(Bytes(bytes(32)))
        assert replay.prng().bytes_new(U32(8)) == first
        # The outer stream CONTINUES across the nested frame.
        assert replay.prng().bytes_new(U32(8)) == second

    # And the inner frame really did get its own unseeded generator.
    assert inner != first and inner != second
```

And the inspection surface beside `published_events`:

```python
    @property
    def recorded_logs(self) -> tuple[tuple[str, tuple[ChainValue, ...]], ...]:
        """Every `env.logs().add(...)` this `Env` saw, in order. TEST-FACING.

        Deep-copied on the way out, like `published_events` and for the same
        reason. Deliberately not in `serpent.__all__`.

        On chain these are DIAGNOSTIC events, not contract events: they are not
        in the transaction's event stream, they do not roll back with a frame in
        any way this model could observe, and with diagnostics off they are never
        produced at all. A contract must not depend on them.
        """
        return copy.deepcopy(tuple(self._logs))
```

Run: `uv run --no-sync pytest -q tests/unit/test_env_crypto_prng_logs.py` -> `19 passed` (18 as v1 wrote them, plus test 14b [M3]).

- [ ] **Step 5: Gates and commit**

The four gates. Expected: `4868 passed / 7 skipped` [M8] (the M8-corrected v1 figure is 4857; +10 carried from Task 1 and +1 for [M3]'s nested-frame test -- see Task 1 Step 5's arithmetic).

```bash
git add src/serpent/env.py tests/unit/test_env_crypto_prng_logs.py
git commit -m "feat(env): add the crypto, prng, and logs facades at tier 1

env.crypto() covers the five non-ZK functions, env.prng() the four PRNG
functions, and env.logs().add the diagnostic path. The two verifies return
None and a refusal raises the new CryptoTrap; the two PRNG refusals raise
the new PrngTrap. Both are plain RuntimeErrors with no reserved code, like
StorageTrap: the host traps, so no on-chain trace can carry a number.

The frame PRNG resets on frame entry, mirroring the host's frame-local
generator, and is seeded before any reseed from a documented constant that
no tier pins. After a reseed the stream is the chain's, exactly."
```

**Report:** the gate outputs; the counts; confirmation that `UNSEEDED_PRNG_SEED` is 32 bytes; any deferred minor; any BLOCKED condition.

---

### Task 4: recognition, the two conversions, and the ONE sanctioned registry pass

**Files:**
- Modify: `src/serpent/compiler/codes.py` (**the only task that may touch it**); `src/serpent/compiler/recognize.py:234-251` (`_HELP`), `:335-565` (`RECOGNIZED`), `:592-622` (the future sets, `_CORE_ENV_SURFACES`), `:703-757` (`recognize_call`'s dispatch), `:828-850` (`_recognize_env_top_level`), `:1233-1261` (`_recognize_ledger_method`), `:1510-1550` (the container method tables), `:1559-1680` (`_ResultKind`, `_METHOD_SHAPES`, `_result_ty`, `_resolve_container_row`, `_methods_of`), `:3329-3360` (`recognize_mutation`), and -- named because `_CORE_ENV_SURFACES` growing to six MOVES them and v1 named none [m9] -- `:860` (`_NO_ARG_CHAIN_STEPS`), `:862-867` (`_CHAIN_HELP`, which enumerates the chains BY HAND and is user-facing text), `:941` (the chain-link walk), `:52-55` (the prose description of the deferred list, which goes stale when `KNOWN_FUTURE_ENV_NAMES` shrinks [M1]); `src/serpent/env.py:69` (the same prose, same reason [M1]); `tests/unit/test_recognize_env.py:273-306` **and `:451-456`** [M1]; `tests/unit/test_containers_frontend.py:261-382`; `tests/unit/test_diagnostics.py:256-350`; `tests/must_reject/constructs/env_deferred_surface.py`; `docs/subset.md`
- Create: `tests/must_reject/constructs/log_message_not_a_literal.py`, `tests/must_reject/constructs/shuffle_result_discarded.py`, `tests/must_reject/types/ed25519_signature_wrong_length.py`
- Test: `tests/unit/test_recognize_env.py`, `test_containers_frontend.py`, `test_must_reject.py`, `test_diagnostics.py`, `test_subset_docs.py`

**Interfaces:**
- Consumes: `HostCallSpec`, `SurfaceKind.HOST_CALL`, `_bind`, `_check_value`, `_error`, `Ty` (note: the constructors are `Ty.BytesN(n)`, `Ty.U32`, `Ty.U64`, `Ty.Void`, `Ty.Bytes`, `Ty.String`, `Ty.Address` -- **PascalCase singletons and static methods, NOT `Ty.bytes_n(n)`**, which is how the M2-A rulings entry spells it; the ruling's intent is this constructor and the spelling below is the real one).
- Produces: 14 `family="env"` rows, 2 `family="container"` rows, four recognizer functions, `_ResultKind.STRING`, `_FIXED_LENGTH_SLOTS`, and one new registry code.

- [ ] **Step 1: the enumerated new inventory (write it down BEFORE touching the table)**

F11: `ENV_HOST_FN_TARGETS` is DERIVED, so it moves on its own; the only thing that catches a mistake is the equality against `_DOSSIER_C4_INVENTORY`, a literal in the test file. An implementer who "fixes" that failure by pasting whatever the table now produces has DELETED the check. So the intended value is enumerated here, and the task review verifies the ENUMERATION, not the diff.

`_DOSSIER_C4_INVENTORY` becomes exactly these **25** names -- the eleven it holds today plus fourteen:

```
put_contract_data                              (existing)
get_contract_data                              (existing)
has_contract_data                              (existing)
del_contract_data                              (existing)
extend_current_contract_instance_and_code_ttl  (existing)
extend_contract_data_ttl                       (existing)
get_ledger_timestamp                           (existing)
get_ledger_sequence                            (existing)
contract_event                                 (existing)
require_auth                                   (existing)
require_auth_for_args                          (existing)
get_ledger_version                             NEW  x.2  env.ledger().version()
get_ledger_network_id                          NEW  x.6  env.ledger().network_id()
get_max_live_until_ledger                      NEW  x.8  env.ledger().max_live_until_ledger()
get_current_contract_address                   NEW  x.7  env.current_contract_address()
log_from_linear_memory                         NEW  x._  env.logs().add(msg, *vals)
compute_hash_sha256                            NEW  c._  env.crypto().sha256(b)
compute_hash_keccak256                         NEW  c.1  env.crypto().keccak256(b)
verify_sig_ed25519                             NEW  c.0  env.crypto().ed25519_verify(k, m, s)
recover_key_ecdsa_secp256k1                    NEW  c.2  env.crypto().secp256k1_recover(d, s, r)
verify_sig_ecdsa_secp256r1                     NEW  c.3  env.crypto().secp256r1_verify(k, d, s)
prng_reseed                                    NEW  p._  env.prng().seed(b)
prng_bytes_new                                 NEW  p.0  env.prng().bytes_new(n)
prng_u64_in_inclusive_range                    NEW  p.1  env.prng().u64_in_range(lo, hi)
prng_vec_shuffle                               NEW  p.2  env.prng().shuffle(v)
```

`_DOSSIER_C4_CONTAINER_INVENTORY` gains exactly **two**: `string_to_bytes` (`b.n`) and `bytes_to_string` (`b.o`). `_EXPECTED_CONTAINER_ROWS` gains exactly two keys: `"string.to_bytes"` and `"bytes.to_string"`.

`KNOWN_FUTURE_ENV_NAMES` becomes exactly `{"call", "try_call", "deployer"}`. `_LEDGER_FUTURE_METHODS` is DELETED along with its dispatch arm (it becomes the empty set, and an empty set with an unreachable arm is worse than no set: it reads as a live promise). `_CORE_ENV_SURFACES` becomes `{"storage", "ledger", "events", "crypto", "prng", "logs"}`. `current_contract_address` is NOT a chain step -- it is a direct call on `env` -- so it joins neither set and gets its own arm.

- [ ] **Step 2: the registry pass -- the ONLY edit to `codes.py` in this sub-plan**

Two reductions first, with their outcomes recorded (E12 requires the attempt and the record):

**Reduction A (Candidate 3, a statically wrong-length crypto argument) -- ADOPTED, as SPT3018.** The slot types are declared `Ty.Bytes`, not `Ty.BytesN(n)`, because `_assignable` (recognize.py:1740) widens `BytesN(n)` to `Bytes` and NOT the reverse: declaring the signature slot `Ty.BytesN(64)` would refuse a signature a contract read out of storage as `Bytes`, which the host accepts. So the reduction is done with a targeted check instead: when an argument's `Ty` IS `BYTES_N` with a known `n` that disagrees with the length the host requires, the mismatch is statically certain and SPT3018 is the honest code ("value's type does not match the declared/expected type"). A plain `Bytes` passes and traps at run time exactly as it does on chain. **No new code.**

**Reduction B (Candidate 4, a discarded shuffle) -- ADOPTED, as SPT1034 with the sanctioned widening.** `env.prng().shuffle(v)` on a line of its own draws SPT1028 today ("this expression produces a Vec[U32] that nothing consumes"), which is true but unhelpful. SPT1034's rule is exactly this shape -- "host container operations are functional ... and C rebinds it" -- so `recognize_mutation` gains an arm that recognises the statement and reports SPT1034 with the rebind advice. **No new code**, one sanctioned construct-list widening.
**A finding to record in the ledger:** the ruling's other half, "or whose receiver is not an owned local", has NO instance. `shuffle` is functional and never rebinds its receiver, so `w = env.prng().shuffle(v)` with `v` aliased is perfectly sound -- there is nothing to alias-analyse. The widening therefore names the discard case only.

**SPT1041 -- NOT ADDED.** Measured on this checkout: an untyped literal in a value-position slot draws `SPT3008` ("wrap the literal in a chain type, e.g. U32(5): an int literal has no chain type in this position"), which is honest and specific. The rest of the log-value space is covered by SPT1038 (a bucket in a value position), SPT2001/SPT2006 (an unknown name), and SPT3020 (arity). No uncovered shape remains, and an append-only registry must not publish a code with no trigger.

So exactly ONE code is added. In `codes.py`, append inside `_SPT1XXX` immediately before its closing `)` at line 389 (band contiguity is load-bearing: `_render_docs` groups with `itertools.groupby` over `REGISTRY` order):

```python
    # M2-A (controller-sanctioned, decisions.md 2026-09-11 E12). `env.logs()
    # .add(msg, *vals)` interns `msg` into the module's data segment at BUILD
    # time and passes the host a (pointer, length) pair, exactly as Rust's
    # `log!` requires a `$fmt:literal`. A computed message would need a
    # different lowering entirely -- build a String object at run time and copy
    # it out -- so this is not an arity mistake (SPT3020), not a type
    # disagreement (SPT3018: a `String` expression has the right type and is
    # still refused), and not an Env call SHAPE mistake (SPT1038: the chain and
    # the arity are both correct). It is a construct the subset does not cover,
    # which is what SPT1xxx means.
    CodeEntry(
        "SPT1040",
        "SPT1xxx",
        "env.logs().add(msg, ...) where `msg` is not a string literal",
        "a log message must be a string literal; it is interned into the module's data "
        "segment at build time",
        "M2-A Task 4",
    ),
```

Narrow **SPT1033** (wording only; no renumber, no meaning reversal -- the code still means "recognized, not yet lowerable", it simply has three inputs instead of nine):

```python
    CodeEntry(
        "SPT1033",
        "SPT1xxx",
        "Recognized Env surface not yet lowerable: env.call(), env.try_call(), "
        "env.deployer()",
        "this Env surface is recognized but not yet supported; it lands in M2-B",
        "M1-C Task 7a",
    ),
```

Widen **SPT1034**'s construct list (wording only, and additive):

```python
        "Container mutation through an aliased binding or a temporary receiver, or a "
        "functional host result discarded (`env.prng().shuffle(v)` on a line of its own)",
```
leaving its `message_intent` and `owning_task` untouched, so `tests/must_reject/constructs/mutation_temporary_receiver.py`'s `serpent:message` still substring-matches.

Correct `NO_FIXTURE_REASONS["SPT6001"]` (E8/C13 -- the bolded clause is now false, because `string_to_bytes`/`bytes_to_string` are gated at protocol 23):

```python
    "SPT6001": (
        "no fixture-reachable trigger: the two host functions the frontend emits that ARE "
        "gated above the base protocol -- string_to_bytes and bytes_to_string, both "
        "min_protocol 23 -- sit below the default target of 27, so a contract using them "
        "raises its DECLARED floor to 23 and never trips the gate (that arm is wired "
        "end-to-end via a synthetic-bindings unit test, M1-C Task 10), and the one FEATURE "
        "gate -- a contract with a constructor needs protocol >= 22, CAP-0058 -- fires "
        "only against an explicit target_protocol, which is a compile_module keyword a "
        "must_reject fixture cannot set"
    ),
```

Then the three pins in `tests/unit/test_diagnostics.py`:
- line 263: `*(f"SPT1{n:03d}" for n in range(1, 40))` -> `range(1, 41)`;
- line 342: `assert len(codes.REGISTRY) == 109` -> `== 110`;
- append to the comment block above line 342, in the file's own convention:

```python
    # M2-A Task 4 (controller-sanctioned, decisions.md 2026-09-11 E12) appends
    # SPT1040, the one new code in M2-A: a log message must be a string
    # literal. SPT1041 was allocated conditionally by the same ruling and is
    # deliberately NOT added -- an untyped literal in a log value slot already
    # draws SPT3008, so the code would have no trigger, and an append-only
    # registry cannot withdraw a published one. SPT1033 and SPT1034 were
    # REWORDED in the same commit (a construct-list narrowing and a widening);
    # neither moves a number, so neither moves this count.
```

Run: `uv run --no-sync pytest -q tests/unit/test_diagnostics.py` -> pass.
Run: `uv run --no-sync python -c "from serpent.compiler import codes; codes.validate(); print(len(codes.REGISTRY))"` -> `110`.

**If anything else in this task appears to need a registry edit, return BLOCKED** (F13). That includes "the help text would read better as its own code" and "this construct deserves a code": both are controller decisions.

- [ ] **Step 3: the recognition rows**

In `RECOGNIZED`, after the `ledger.sequence` row (recognize.py:517-521), add:

```python
    "ledger.version": HostCallSpec(
        surface="env.ledger().version()",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("get_ledger_version",),
    ),
    "ledger.network_id": HostCallSpec(
        surface="env.ledger().network_id()",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("get_ledger_network_id",),
    ),
    "ledger.max_live_until_ledger": HostCallSpec(
        surface="env.ledger().max_live_until_ledger()",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("get_max_live_until_ledger",),
    ),
    "env.current_contract_address": HostCallSpec(
        surface="env.current_contract_address()",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("get_current_contract_address",),
    ),
    "crypto.sha256": HostCallSpec(
        surface="env.crypto().sha256(data)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("compute_hash_sha256",),
    ),
    "crypto.keccak256": HostCallSpec(
        surface="env.crypto().keccak256(data)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("compute_hash_keccak256",),
    ),
    "crypto.ed25519_verify": HostCallSpec(
        surface="env.crypto().ed25519_verify(public_key, message, signature)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("verify_sig_ed25519",),
    ),
    "crypto.secp256k1_recover": HostCallSpec(
        surface="env.crypto().secp256k1_recover(digest, signature, recovery_id)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("recover_key_ecdsa_secp256k1",),
    ),
    "crypto.secp256r1_verify": HostCallSpec(
        surface="env.crypto().secp256r1_verify(public_key, digest, signature)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("verify_sig_ecdsa_secp256r1",),
    ),
    "prng.seed": HostCallSpec(
        surface="env.prng().seed(s)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("prng_reseed",),
    ),
    "prng.bytes_new": HostCallSpec(
        surface="env.prng().bytes_new(length)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("prng_bytes_new",),
    ),
    "prng.u64_in_range": HostCallSpec(
        surface="env.prng().u64_in_range(lo, hi)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("prng_u64_in_inclusive_range",),
    ),
    "prng.shuffle": HostCallSpec(
        surface="env.prng().shuffle(v)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("prng_vec_shuffle",),
    ),
    "logs.add": HostCallSpec(
        surface="env.logs().add(message, *values)",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("log_from_linear_memory",),
    ),
    "string.to_bytes": HostCallSpec(
        surface="s.to_bytes()",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("string_to_bytes",),
        family="container",
    ),
    "bytes.to_string": HostCallSpec(
        surface="b.to_string()",
        kind=SurfaceKind.HOST_CALL,
        host_fns=("bytes_to_string",),
        family="container",
    ),
```

The two conversion NAMES are a plan-author choice the rulings did not settle (E3 enumerates the crypto, PRNG, ledger, address, and log spellings and stops). `s.to_bytes()`/`b.to_string()` are chosen because (a) they are METHODS on the value, which is where serpent already puts `b.slice(lo, hi)`, (b) they read in the host function's own direction, and (c) the Rust SDK reaches these host functions through `Bytes`/`String` conversions rather than through an `env.` chain, so a value method is the closer analogue. **Ratify or overturn at plan review.**

- [ ] **Step 4: the env recognizers**

**Before writing the code, deal with the test that pins the OLD answer** [M1]. `tests/unit/test_recognize_env.py:451-452` is

```python
def test_ledger_future_method_is_m2_pointer() -> None:
    _assert_reject(_reject_call("env.ledger().version()"), "SPT1033", "ledger")
```

and it fails the moment `_LEDGER_FUTURE_METHODS` is deleted. It is NOT self-healing -- unlike the two parametrized tests at `:599`/`:604`, which iterate `KNOWN_FUTURE_ENV_NAMES` and shrink on their own, which is why the file read as covered in v1. DELETE it and put the honest positive in its place, beside the existing `test_ledger_sequence_is_u32` (`:459`):

```python
def test_ledger_version_is_u32() -> None:
    """`get_ledger_version` answers a `U32`, like `sequence`. This test
    REPLACES `test_ledger_future_method_is_m2_pointer`, which pinned the same
    call as SPT1033 and which M2-A retires by landing the surface [M1].
    """
    assert _accept_call("env.ledger().version()").ty == Ty.U32
```

`test_ledger_unknown_method_is_unresolved` (`:455`) still holds -- an unknown `env.ledger()` method is still unresolved -- and stays untouched. The same staleness reaches two prose sites that no test guards: `src/serpent/env.py:69` and `recognize.py:52-55` both describe the deferred list in words; both are corrected in this commit.

Then replace `_recognize_ledger_method`'s tail so the three new methods are lowered and `_LEDGER_FUTURE_METHODS` disappears (the helper below removes the repetition the three-arm shape would otherwise add):

```python
#: `env.ledger()`'s no-argument readers: method -> (row key, result type).
_LEDGER_READERS: Mapping[str, tuple[str, Ty]] = {
    "timestamp": ("ledger.timestamp", Ty.U64),
    "sequence": ("ledger.sequence", Ty.U32),
    "version": ("ledger.version", Ty.U32),
    "network_id": ("ledger.network_id", Ty.BytesN(32)),
    "max_live_until_ledger": ("ledger.max_live_until_ledger", Ty.U32),
}


def _recognize_ledger_method(node: ast.Call, ctx: FuncCtx, method: str) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)
    reader = _LEDGER_READERS.get(method)
    if reader is None:
        _error(
            ctx,
            "SPT2006",
            loc,
            f"`Ledger` has no method `{method}`",
            help="ledger() supports .timestamp(), .sequence(), .version(), .network_id(), "
            "and .max_live_until_ledger()",
        )
        return _invalid(loc)
    row, result = reader
    spec = RECOGNIZED[row]
    if _bind(node, ctx, loc, spec.surface, ()) is None:
        return _invalid(loc)
    (fn_name,) = spec.host_fns
    return HostCall(loc=loc, ty=result, fn_name=fn_name, args=())
```

`network_id` is typed `Ty.BytesN(32)` because env.json's own doc says the value "is always 32 bytes in length" and `narrow_to` already checks `_LENGTH` for a length-bearing Bytes request (E7): the ABI check on the returned `BytesObject` is what turns that documentation into an assertion.

Add, beside it:

```python
#: The crypto surfaces, each one leaf-shaped: (row key, parameter names,
#: per-slot expected `Ty`, result `Ty`). The slot types are `Ty.Bytes`, NOT
#: `Ty.BytesN(n)`: `_assignable` widens `BytesN(n)` to `Bytes` and not the
#: reverse, so a `BytesN` slot would refuse a signature a contract read out of
#: storage as `Bytes` -- which the host accepts. The fixed lengths live in
#: `_FIXED_LENGTH_SLOTS` below and are checked only when the argument's type
#: CARRIES a length that disagrees.
_CRYPTO_METHODS: Mapping[str, tuple[str, tuple[str, ...], tuple[Ty, ...], Ty]] = {
    "sha256": ("crypto.sha256", ("data",), (Ty.Bytes,), Ty.BytesN(32)),
    "keccak256": ("crypto.keccak256", ("data",), (Ty.Bytes,), Ty.BytesN(32)),
    "ed25519_verify": (
        "crypto.ed25519_verify",
        ("public_key", "message", "signature"),
        (Ty.Bytes, Ty.Bytes, Ty.Bytes),
        Ty.Void,
    ),
    "secp256k1_recover": (
        "crypto.secp256k1_recover",
        ("digest", "signature", "recovery_id"),
        (Ty.Bytes, Ty.Bytes, Ty.U32),
        Ty.BytesN(65),
    ),
    "secp256r1_verify": (
        "crypto.secp256r1_verify",
        ("public_key", "digest", "signature"),
        (Ty.Bytes, Ty.Bytes, Ty.Bytes),
        Ty.Void,
    ),
}

#: `(row, parameter) -> the exact length the HOST requires`, for the slots
#: where a statically-known wrong length is certain to trap on chain (K10).
#: Reduction A: a `BytesN(n)` argument with the wrong `n` IS a type
#: disagreement, so SPT3018 is honest; a plain `Bytes` carries no length and is
#: accepted here, then trapped by the host exactly as it would be on chain.
_FIXED_LENGTH_SLOTS: Mapping[tuple[str, str], int] = {
    ("crypto.ed25519_verify", "public_key"): 32,
    ("crypto.ed25519_verify", "signature"): 64,
    ("crypto.secp256k1_recover", "digest"): 32,
    ("crypto.secp256k1_recover", "signature"): 64,
    ("crypto.secp256r1_verify", "public_key"): 65,
    ("crypto.secp256r1_verify", "digest"): 32,
    ("crypto.secp256r1_verify", "signature"): 64,
    ("prng.seed", "s"): 32,
}


def _recognize_crypto_method(node: ast.Call, ctx: FuncCtx, method: str) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)
    entry = _CRYPTO_METHODS.get(method)
    if entry is None:
        _error(
            ctx,
            "SPT2006",
            loc,
            f"`Crypto` has no method `{method}`",
            help="crypto() supports .sha256(), .keccak256(), .ed25519_verify(), "
            ".secp256k1_recover(), and .secp256r1_verify(); the bls12-381, bn254, and "
            "poseidon families are not in the subset",
        )
        return _invalid(loc)
    row, params, slots, result = entry
    args = _leaf_args(node, ctx, loc, row, params, slots)
    if args is None:
        return _invalid(loc)
    (fn_name,) = RECOGNIZED[row].host_fns
    return HostCall(loc=loc, ty=result, fn_name=fn_name, args=args)


def _leaf_args(
    node: ast.Call,
    ctx: FuncCtx,
    loc: Loc,
    row: str,
    params: tuple[str, ...],
    slots: tuple[Ty, ...],
) -> tuple[IRExpr, ...] | None:
    """Bind and type-check one leaf surface's arguments, or `None` after
    reporting. The container family's `_bound_args` cannot be reused: its
    expected types are RELATIVE to a receiver, and a leaf surface has none."""
    spec = RECOGNIZED[row]
    bound = _bind(node, ctx, loc, spec.surface, params)
    if bound is None:
        return None
    checked: list[IRExpr] = []
    for param, declared in zip(params, slots, strict=True):
        value = _check_value(bound[param], ctx, expected=declared)
        if _failed(value):
            return None
        if not _assignable(value.ty, declared):
            # [M5] wrapped: the single-line form was 101 columns and
            # `ruff format --check` (gate 2) reformatted the file.
            _error(
                ctx,
                "SPT3018",
                loc,
                f"`{param}` is {value.ty.render()}, not {declared.render()}",
            )
            return None
        required = _FIXED_LENGTH_SLOTS.get((row, param))
        if required is not None and value.ty.tag is TyTag.BYTES_N and value.ty.n != required:
            # Reduction A (E12): the length is carried in the TYPE, so the
            # mismatch is static and certain -- the host refuses any other
            # length outright. A plain `Bytes` reaches here with no `n` and is
            # accepted, then checked by the host at run time.
            _error(
                ctx,
                "SPT3018",
                loc,
                f"`{param}` is {value.ty.render()}, and this slot is exactly "
                f"{required} bytes",
            )
            return None
        checked.append(value)
    return tuple(checked)


#: `env.prng()`'s four surfaces: (row key, parameter names, slot types, result).
#: `shuffle`'s slot and result are both `None`: its type is the RECEIVER's,
#: resolved from the argument in `_recognize_prng_method`.
_PRNG_METHODS: Mapping[str, tuple[str, tuple[str, ...], tuple[Ty, ...], Ty | None]] = {
    "seed": ("prng.seed", ("s",), (Ty.Bytes,), Ty.Void),
    "bytes_new": ("prng.bytes_new", ("length",), (Ty.U32,), Ty.Bytes),
    "u64_in_range": ("prng.u64_in_range", ("lo", "hi"), (Ty.U64, Ty.U64), Ty.U64),
    "shuffle": ("prng.shuffle", ("v",), (), None),
}


def _recognize_prng_method(node: ast.Call, ctx: FuncCtx, method: str) -> IRExpr:
    loc = Loc.from_node(ctx.path, node)
    entry = _PRNG_METHODS.get(method)
    if entry is None:
        _error(
            ctx,
            "SPT2006",
            loc,
            f"`Prng` has no method `{method}`",
            help="prng() supports .seed(), .bytes_new(), .u64_in_range(), and .shuffle()",
        )
        return _invalid(loc)
    row, params, slots, result = entry
    if method == "shuffle":
        # The one non-leaf shape: the result type IS the argument's, so the
        # slot cannot be declared in the table.
        bound = _bind(node, ctx, loc, RECOGNIZED[row].surface, params)
        if bound is None:
            return _invalid(loc)
        value = _check_value(bound["v"], ctx)
        if _failed(value):
            return _invalid(loc)
        if value.ty.tag is not TyTag.VEC:
            _error(ctx, "SPT3018", loc, f"`v` is {value.ty.render()}, not a Vec")
            return _invalid(loc)
        (fn_name,) = RECOGNIZED[row].host_fns
        return HostCall(loc=loc, ty=value.ty, fn_name=fn_name, args=(value,))
    args = _leaf_args(node, ctx, loc, row, params, slots)
    if args is None:
        return _invalid(loc)
    assert result is not None
    (fn_name,) = RECOGNIZED[row].host_fns
    return HostCall(loc=loc, ty=result, fn_name=fn_name, args=args)


def _recognize_logs_method(node: ast.Call, ctx: FuncCtx, method: str) -> IRExpr:
    """`env.logs().add(message, *values)`: a literal message and N chain values.

    Hand-bound rather than `_bind`-bound because the signature is VARIADIC and
    `_bind` names a fixed parameter list. The message check is SPT1040's only
    site.

    There is deliberately NO starred-argument arm here [M4]. `expr._check_call`
    (`expr.py:1178-1180`) rejects every `ast.Starred` BEFORE dispatching to
    `recognize_call`, with SPT1007 -- "argument unpacking is not supported; a
    contract export has a fixed arity" -- which is the honest code and has a
    `_HELP` entry (`expr.py:198`). An arm here would be unreachable, and the
    one v1 wrote used SPT1030 ("Assign -- subscript target"), which is about
    something else and carries no `help=`; the diagnostics sink REFUSES an
    SPT1xxx with no help (`diagnostics.py:176-180`) and would have raised a
    bare `ValueError` out of the compiler if the arm had ever been reached.
    """
    loc = Loc.from_node(ctx.path, node)
    if method != "add":
        _error(
            ctx,
            "SPT2006",
            loc,
            f"`Logs` has no method `{method}`",
            help="logs() supports .add(message, *values)",
        )
        return _invalid(loc)
    if node.keywords:
        _error(ctx, "SPT1035", loc, "`env.logs().add(message, *values)` takes no keywords")
        return _invalid(loc)
    if not node.args:
        _error(
            ctx,
            "SPT3020",
            loc,
            "`env.logs().add(message, *values)` is missing required argument(s): message",
        )
        return _invalid(loc)
    message = node.args[0]
    if not isinstance(message, ast.Constant) or not isinstance(message.value, str):
        _error(
            ctx,
            "SPT1040",
            loc,
            "the log message is not a string literal",
            help="write the message inline, e.g. `env.logs().add(\"drew\", n)`",
        )
        return _invalid(loc)
    values: list[IRExpr] = []
    for argument in node.args[1:]:
        # No `ast.Starred` arm: `expr._check_call:1178` already answered with
        # SPT1007 before this function was reached. See the docstring [M4].
        value = _check_value(argument, ctx)
        if _failed(value):
            return _invalid(loc)
        values.append(value)
    (fn_name,) = RECOGNIZED["logs.add"].host_fns
    # `Log` is NOT a new IR node: the message literal is an ordinary `Const`
    # and the emitter's lowering reads it back off `args[0]`, exactly as
    # `MakeStruct` reads its field names. The `Const`'s `Ty` is `Ty.String`
    # because that is what the literal IS; nothing lowers it as a String
    # object, and `_lower_log` says so.
    literal = Const(loc=Loc.from_node(ctx.path, message), ty=Ty.String, py_value=message.value)
    return HostCall(loc=loc, ty=Ty.Void, fn_name=fn_name, args=(literal, *values))
```

**[M4]** v1 ended this step with "Confirm `SPT1030`'s intent covers a starred call argument; if it does not, use `_FALLBACK_CODE`." Both codes are wrong and the arm is unreachable, so the hedge is resolved in the plan instead of shipped: the arm is DELETED. Verify once, as a positive check rather than a hedge -- `env.logs().add("x", *values)` must report **SPT1007**:

```bash
uv run --no-sync python - <<'PY'
from serpent.compiler import compile_module
from serpent.compiler.diagnostics import CompileError

SRC = '''
from serpent import U32, Env, Vec, contract


@contract
class Probe:
    def go(self, env: Env, values: Vec[U32]) -> U32:
        env.logs().add("drew", *values)
        return U32(1)
'''
try:
    compile_module(SRC, "probe.py")
    raise SystemExit("expected a rejection")
except CompileError as exc:
    for d in exc.diagnostics:
        print(d.code, "|", d.message)
PY
```

Expected: `SPT1007 | argument unpacking is not supported; a contract export has a fixed arity: `Starred` is not part of the serpent subset`. If it reports anything else, that is a real finding about `expr._check_call`'s ordering -- report it, do not add a code.

`recognize_call`'s dispatch (recognize.py:715-721) gains three arms beside the `ledger`/`events` ones:

```python
    if _match_no_arg_chain(base, "crypto"):
        return _recognize_crypto_method(node, ctx, method)

    if _match_no_arg_chain(base, "prng"):
        return _recognize_prng_method(node, ctx, method)

    if _match_no_arg_chain(base, "logs"):
        return _recognize_logs_method(node, ctx, method)
```

and, before `_recognize_env_top_level` is reached for a called `env.<method>`, the direct surface:

```python
    if isinstance(base, ast.Name) and base.id == "env" and method == "current_contract_address":
        spec = RECOGNIZED["env.current_contract_address"]
        loc = Loc.from_node(ctx.path, node)
        if _bind(node, ctx, loc, spec.surface, ()) is None:
            return _invalid(loc)
        (fn_name,) = spec.host_fns
        return HostCall(loc=loc, ty=Ty.Address, fn_name=fn_name, args=())
```

`_recognize_env_top_level`'s SPT2006 help becomes:

```python
        help="see env.storage(), env.ledger(), env.events(), env.crypto(), env.prng(), "
        "env.logs(), env.current_contract_address(), or an Address's require_auth()",
```

and its SPT1033 detail becomes `f"`env.{name}` is recognized but not lowerable yet"` -- **without** a second "it lands in M2-B" [m8]. The registry's `message_intent` already ends with that clause and the renderer prints both, so v1's detail made the message say it twice, in `docs/subset.md` as well as in a terminal.

While in `_HELP`, also reword `recognize._HELP["SPT1033"]` (`recognize.py:244`), which still reads "deferred to M2" [m8]. M2 has started; the honest text names the owner the way SPT1033's intent now does, e.g. `"env.call(), env.try_call(), and env.deployer() land in M2-B; storage, ledger, events, crypto, prng, and logs are available today"`. This string renders into `docs/subset.md` too, so it regenerates in the same commit.

**A FOURTH arm in `_recognize_env_top_level`** [M6]. Today `_recognize_env_top_level` has three arms -- `_CORE_ENV_SURFACES` (SPT1038), `KNOWN_FUTURE_ENV_NAMES` (SPT1033), and an SPT2006 fallback at `:840-846`. `current_contract_address` is not a chain step, so after this task it joins NEITHER set, and the bare attribute form `env.current_contract_address` (no parentheses) would fall through to `"`env` has no attribute `current_contract_address`"` -- which is FALSE, and a regression from today's honest SPT1033. Add:

```python
    if name == "current_contract_address":
        # Not a chain step (it answers directly, it does not return a bucket),
        # so it is in neither _CORE_ENV_SURFACES nor KNOWN_FUTURE_ENV_NAMES --
        # and without this arm the SPT2006 fallback below would claim `env`
        # has no such attribute, which is a lie about a surface that exists.
        # SPT1038 is the code for "env API used with an unsupported call
        # shape", which is exactly what a missing `()` is.
        _error(
            ctx,
            "SPT1038",
            loc,
            "`env.current_contract_address` must be called: "
            "`env.current_contract_address()`",
        )
        return _invalid(loc)
```

and PIN it: `tests/must_reject/constructs/env_attribute_uncalled.py` already exists for this shape, and `tests/unit/test_recognize_env.py` gets a direct assertion that the bare attribute is SPT1038, not SPT2006.

In `recognize_mutation`, as the FIRST thing after `func = node.func` (Reduction B):

```python
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "shuffle"
        and _match_no_arg_chain(func.value, "prng")
    ):
        # `env.prng().shuffle(v)` on a line of its own. The host's shuffle is
        # FUNCTIONAL -- it clones and hands back a new VecObject -- so a
        # discarded result shuffles nothing, which is SPT1034's rule
        # (sanctioned construct-list widening, M2-A E12). Reported here rather
        # than left to SPT1028's generic "nothing consumes this", which is true
        # but does not say what to write instead.
        _error(
            ctx,
            "SPT1034",
            Loc.from_node(ctx.path, node),
            "`env.prng().shuffle(v)` returns a NEW Vec and changes nothing in place",
            help="rebind the vector: `v = env.prng().shuffle(v)`",
        )
        return Nop(loc=Loc.from_node(ctx.path, node))
```

- [ ] **Step 5: the two conversions in the container family**

In recognize.py's container tables:

```python
#: `String`'s one method: the protocol-23 re-tag to `Bytes` (`b.n`).
STRING_METHODS: Mapping[str, str] = {"to_bytes": "string.to_bytes"}

BYTES_METHODS: Mapping[str, str] = {"slice": "bytes.slice", "to_string": "bytes.to_string"}
```
```python
_CONTAINER_METHOD_NAMES: frozenset[str] = (
    frozenset(VEC_METHODS)
    | frozenset(MAP_METHODS)
    | frozenset(BYTES_METHODS)
    | frozenset(STRING_METHODS)
)

_CONTAINER_TAGS: frozenset[TyTag] = frozenset(
    {TyTag.VEC, TyTag.MAP, TyTag.BYTES, TyTag.BYTES_N, TyTag.STRING}
)
```

**`to_bytes` is also `int.to_bytes`** [m15]. `_CONTAINER_METHOD_NAMES` is matched on the METHOD NAME before the receiver's type is known (`recognize.py:758`), so adding `to_bytes` routes `x.to_bytes()` on ANY receiver into `_recognize_container_method` -- and `int.to_bytes` is a real Python method an author might reach for on a `U32`. This is an improvement, not a regression: `_recognize_container_method` resolves the row from the receiver's `Ty` and reports a typed diagnostic naming `String.to_bytes`, where the pre-change path reported a generic unknown-method error. But it is a behaviour change to a name outside the new surface, so it gets a fixture: add `tests/must_reject/` coverage (or extend an existing container fixture) for `U32(5).to_bytes()` and record the code it draws in the task report. If the code is WORSE than what a contract sees today, that is a finding -- report it, do not widen the registry.

`_ResultKind` gains `STRING = auto()` and `_result_ty` gains, before its `Ty.Bytes` fallthrough:

```python
    if kind is _ResultKind.STRING:
        # `bytes_to_string` re-tags the payload; the length invariant of a
        # fixed-length receiver does not survive the trip and there is no
        # fixed-length String anyway.
        return Ty.String
```

`_METHOD_SHAPES` gains:

```python
    "bytes.to_string": _MethodShape(params=(), args=(), result=_ResultKind.STRING),
    "string.to_bytes": _MethodShape(params=(), args=(), result=_ResultKind.BYTES),
```

`_resolve_container_row` gains an arm and `_methods_of` loses its implicit fallthrough:

```python
    if recv_ty.tag in (TyTag.BYTES, TyTag.BYTES_N):
        return BYTES_METHODS.get(method)
    if recv_ty.tag is TyTag.STRING:
        return STRING_METHODS.get(method)
    return None
```
```python
def _methods_of(recv_ty: Ty) -> tuple[str, ...]:
    if recv_ty.tag is TyTag.VEC:
        return tuple(VEC_METHODS)
    if recv_ty.tag is TyTag.MAP:
        return tuple(MAP_METHODS)
    if recv_ty.tag is TyTag.STRING:
        return tuple(STRING_METHODS)
    return tuple(BYTES_METHODS)
```

Add `"STRING_METHODS"` to recognize.py's `__all__` (it sits beside `BYTES_METHODS` at line 203) so `tests/unit/test_containers_frontend.py`'s method/tier-1 differential can reach it, and add `"string": String` to that file's `_TIER1_CLASSES`.

- [ ] **Step 6: the fixtures**

`tests/must_reject/constructs/log_message_not_a_literal.py`:

```python
# serpent:reject SPT1040
# serpent:at HERE
# serpent:message a log message must be a string literal
# serpent:doc-title log message built at run time
from serpent import Env, String, U32, contract


@contract
class Contract:
    def compute(self, env: Env, label: String, x: U32) -> U32:
        env.logs().add(label, x)  # HERE
        return x
```

`tests/must_reject/constructs/shuffle_result_discarded.py`:

```python
# serpent:reject SPT1034
# serpent:at HERE
# serpent:message host container operations are functional
# serpent:doc-title a discarded prng shuffle
from serpent import Env, U32, Vec, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        v = Vec(U32, [x, x])
        env.prng().shuffle(v)  # HERE
        return x
```

`tests/must_reject/types/ed25519_signature_wrong_length.py`:

```python
# serpent:reject SPT3018
# serpent:at HERE
# serpent:message value's type does not match the declared/expected type
# serpent:doc-title a 32-byte value in the ed25519 signature slot
from serpent import Bytes32, Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, key: Bytes32, message: Bytes32, x: U32) -> U32:
        env.crypto().ed25519_verify(key, message, message)  # HERE
        return x
```

Repoint the SPT1033 fixture (F12) -- `env.logs()` now COMPILES, so the fixture would start failing:

```python
# serpent:reject SPT1033
# serpent:at HERE
# serpent:message this Env surface is recognized but not yet supported; it lands in M2-B
# serpent:doc-title recognized-but-deferred Env surface (env.deployer)
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        env.deployer()  # HERE
        return x
```

Run: `uv run --no-sync pytest -q tests/unit/test_must_reject.py` -> pass, with `len(FIXTURES)` now 120.

- [ ] **Step 7: the inventories, the docs regen, the byte freeze, the gates, the commit**

Update `_DOSSIER_C4_INVENTORY` to Step 1's enumerated 25; `_EXPECTED_CONTAINER_ROWS` and `_DOSSIER_C4_CONTAINER_INVENTORY` to Step 1's two additions. Do NOT paste what the table produces: transcribe the enumeration, then let the equality assert.

Regenerate the subset doc and READ the diff:

```bash
uv run --no-sync python -m serpent.compiler._render_docs
git diff --stat docs/subset.md
```
Expected changes, and nothing else: the SPT1033 sentence in SS1.3 shrinks from seven names to three (`_render_env_api` renders it from `KNOWN_FUTURE_ENV_NAMES`); the SPT1033 section's construct, intent, title, body, and message all change; SPT1034's construct grows and a second fixture example appears under it; SPT1040's section is new; SPT3018 gains a fixture example; SS3's SPT6001 row is reworded; the container prose in `_render_containers` needs one sentence for the conversions (hand-written in the generator: add it and say so in the report).

**The byte freeze** (R4/F9) -- this task edits `src/serpent/compiler/`, so run it explicitly and quote the output:

```bash
SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q \
  "tests/real_host/test_testnet_fixtures.py::test_this_trees_build_equals_the_deployed_bytes[shapes]" \
  "tests/real_host/test_testnet_fixtures.py::test_this_trees_build_equals_the_deployed_bytes[bounty_board]" \
  "tests/real_host/test_testnet_fixtures.py::test_the_fixtures_were_recorded_against_the_deployed_bytes[shapes]" \
  "tests/real_host/test_testnet_fixtures.py::test_the_fixtures_were_recorded_against_the_deployed_bytes[bounty_board]"
```
Expected: `4 passed`. A failure is BLOCKED, not a fix.

Then the four gates.

```bash
git add src/serpent/compiler/codes.py src/serpent/compiler/recognize.py \
        tests/unit/test_recognize_env.py tests/unit/test_containers_frontend.py \
        tests/unit/test_diagnostics.py tests/must_reject/ docs/subset.md
git commit -m "feat(compiler): recognize the leaf Env surface and the two conversions

Fourteen env rows (three ledger accessors, current_contract_address, the
five non-ZK crypto functions, the four prng functions, and the log call)
plus two container rows for string_to_bytes and bytes_to_string.
KNOWN_FUTURE_ENV_NAMES shrinks to call, try_call, and deployer, and
_LEDGER_FUTURE_METHODS is gone.

Sanctioned registry pass: SPT1040 for a log message that is not a string
literal, SPT1033 narrowed to the three names M2-B owns, SPT1034 widened to
name a discarded env.prng().shuffle, and SPT6001's no-fixture reason
corrected now that two emitted host functions are gated at protocol 23. A
statically wrong-length crypto argument reduces to SPT3018 and an untyped
log value to SPT3008, so no further code was needed."
```

**Report:** the enumerated inventory as it was transcribed (so the reviewer checks the ENUMERATION, F11); the two reductions' outcomes and the finding that SPT1034's "receiver not an owned local" half has no instance; the `git diff --stat docs/subset.md` and a summary of every changed section; the byte-freeze output quoted; the four gate outputs; the counts; the measured code for a starred log argument (expected SPT1007, [M4]); the measured code for the bare `env.current_contract_address` attribute (expected SPT1038, [M6]); any BLOCKED condition.

---

### Task 5: the emitter, plain calls

**Files:**
- Create: `tests/unit/test_emitter_leaf_calls.py`
- Modify: nothing in `src/` -- **that is the claim this task proves.**
- Test: the new file, plus `tests/unit/test_emitter_semantics.py`, `test_emitter_end_to_end.py`

**Interfaces:**
- Consumes: `lower._lower_host_call`, `lower._lower_host_call_raw`, `lower.narrow_to`, `ctx.host_import_name`, `HostFn.val_typed_args` (recognize.py's rows name the host functions; `_lower_host_call_raw` reads the pin per position and never re-derives it, C7).

Thirteen of the fifteen new host calls are ORDINARY `HOST_CALL` rows whose arguments are all `Val`-typed and whose return is `Val`-typed, so `_lower_host_call` already lowers them with no change at all. This task's whole job is to PROVE that rather than assume it, and to find the two that are different before Tasks 6 and 7 handle them.

- [ ] **Step 1: the ABI table, from the pin, as a test**

Create `tests/unit/test_emitter_leaf_calls.py` opening with the fact table, read from the pin rather than restated:

```python
"""Every M2-A host call, lowered, against the PIN's own per-position ABI.

`HostFn.val_typed_args`/`val_typed_ret` are computed properties on the pinned
`env.json` bindings, and the model's docstring says consumers "must not
re-derive that ABI fact". So this file reads them and asserts the LOWERING
agrees, rather than restating what the ABI ought to be.

Two rows are not ordinary, and both are named here so a reader meets them
before they meet the code:

* `prng_u64_in_inclusive_range` takes TWO RAW u64 arguments and returns a RAW
  u64. The argument side has precedent (the storage type immediate); the
  RETURN side has none -- it is the first `SurfaceKind.HOST_CALL` row in the
  table whose result is not a `Val` (Task 6).
* `log_from_linear_memory` takes four `U32Val` pointer/length immediates and
  reads guest memory (Task 7).
"""

from __future__ import annotations

import pytest

from serpent._host import functions_by_name

M2A_HOST_FNS: tuple[str, ...] = (
    "get_ledger_version",
    "get_ledger_network_id",
    "get_max_live_until_ledger",
    "get_current_contract_address",
    "log_from_linear_memory",
    "compute_hash_sha256",
    "compute_hash_keccak256",
    "verify_sig_ed25519",
    "recover_key_ecdsa_secp256k1",
    "verify_sig_ecdsa_secp256r1",
    "prng_reseed",
    "prng_bytes_new",
    "prng_u64_in_inclusive_range",
    "prng_vec_shuffle",
    "string_to_bytes",
    "bytes_to_string",
)


def test_exactly_one_m2a_host_call_returns_a_raw_scalar() -> None:
    """C7a/F16: the generic lowering ends in an UNCONDITIONAL `narrow_to`,
    which ABI-checks the stack top AS A VAL. Every row but one may take that
    path; the one that may not is the whole reason Task 6 exists, and this
    test is what would catch a second one appearing in a future pin."""
    raw = [n for n in M2A_HOST_FNS if not functions_by_name[n].val_typed_ret]
    assert raw == ["prng_u64_in_inclusive_range"]


def test_exactly_one_m2a_host_call_takes_raw_scalar_arguments() -> None:
    raw = [n for n in M2A_HOST_FNS if not all(functions_by_name[n].val_typed_args)]
    assert raw == ["prng_u64_in_inclusive_range"]
    assert functions_by_name["prng_u64_in_inclusive_range"].val_typed_args == (False, False)


def test_every_m2a_host_call_is_all_i64_at_the_wasm_boundary() -> None:
    for name in M2A_HOST_FNS:
        fn = functions_by_name[name]
        assert set(fn.wasm_params) <= {"i64"}, (name, fn.wasm_params)
        assert fn.wasm_result == "i64", name


def test_only_the_two_conversions_carry_a_protocol_gate() -> None:
    """E8: the highest floor M2-A imports is 23, and the default target is 27,
    so no gate fires -- but the DECLARED floor of a contract using them moves,
    which is `inspect`'s first non-trivial input."""
    gated = {n: functions_by_name[n].min_protocol for n in M2A_HOST_FNS}
    assert {n: v for n, v in gated.items() if v is not None} == {
        "string_to_bytes": 23,
        "bytes_to_string": 23,
        "verify_sig_ecdsa_secp256r1": 21,
    }
```

- [ ] **Step 2: lowering shape tests, using `tests/unit/test_emitter_lower_objects.py`'s idiom**

Add to the same file, importing `build`/`run`/`items`/`import_calls`/`host`/`const` from `tests.unit.test_emitter_lower_objects` (they are module-level helpers there):

1. `test_a_no_argument_context_call_is_one_import_and_a_narrow` -- `host("get_ledger_version", Ty.U32)` lowers to exactly one `CallImport("get_ledger_version")` plus the `fail_with_error` the ABI check inserts, and NOTHING else.
2. `test_network_id_narrows_through_the_bytesn_part` -- `host("get_ledger_network_id", Ty.BytesN(32))`'s body contains a `CallDefined` of the `tagcheck_bytes_n` part and a `CallImport("bytes_len")`, which is what makes E7's "always 32 bytes" an assertion rather than a comment.
3. `test_current_contract_address_narrows_to_an_address_tag` -- one object-tag compare, no part.
4. `test_the_three_val_typed_crypto_calls_pass_every_argument_as_a_val` -- using the mini-host store's `calls` log (the `start`/`invoke` idiom), assert `compute_hash_sha256` sees the Bytes HANDLE word, not a raw pointer.
5. `test_a_protocol_23_conversion_moves_the_declared_floor` -- build a one-method contract calling `s.to_bytes()` through `compile_module`/`build_wasm` and assert `built.declared_protocol == 23`; the same contract without it declares 20. This is E8's regression test: **no gate fires**, and the floor moves.
6. `test_no_m2a_surface_trips_the_protocol_gate_at_the_default_target` -- build a contract touching ALL of them and assert it compiles with no `target_protocol` argument and declares 23.

Run: `uv run --no-sync pytest -q tests/unit/test_emitter_leaf_calls.py`
Expected: tests 1-4 and 6 pass with no `src/` change; test 5 passes; **and the `prng_u64_in_inclusive_range` tests in Task 6 are the ones that fail**. If a test here fails, that is a finding about the generic path, not something to route around.

- [ ] **Step 3: the byte freeze, the gates, the commit**

Run the four byte-freeze node ids (Global Constraints). Expected `4 passed` -- this task changes no `src/` file, so a failure here means something else moved and is BLOCKED.

The four gates.

```bash
git add tests/unit/test_emitter_leaf_calls.py
git commit -m "test(emitter): pin the leaf host calls against the pin's own ABI

Thirteen of the fifteen surfaces M2-A lowers take the generic HOST_CALL
path unchanged; this proves it instead of assuming it, and names the two
that do not: prng_u64_in_inclusive_range (a raw scalar in both directions)
and log_from_linear_memory. Also pins that no M2-A import trips the
protocol gate at the default target while the two conversions do move a
contract's declared floor to 23."
```

**Report:** which of the six tests needed no `src/` change; the byte-freeze output; the gate outputs; the counts.

---

### Task 6: the emitter's raw-scalar return branch (C7a, F16)

**Files:**
- Modify: `src/serpent/emitter/lower.py:1348-1366` (`_lower_host_call`)
- Modify: `tests/unit/test_emitter_leaf_calls.py`
- Test: the same, plus the whole emitter suite

**Interfaces:**
- Consumes: `HostFn.val_typed_ret` (read, never re-derived); `arith.rebox(fn, ctx, ty)` (arith.py:2364-2394), which for `Ty.U64` calls the `box_u64` part -- `fits_small_u` at run time, else `obj_from_u64`.
- Produces: `_lower_host_call` with a `val_typed_ret is False` branch.

**This is the one place in M2-A that can ship a silent wrong answer.** `_lower_host_call` ends in an unconditional `narrow_to(fn, ctx, e.ty)`, and `narrow_to` `local.tee`s the stack top and runs `abi_check` on it AS A VAL. For `Ty.U64` that is the EITHER-repr check: the word's tag byte must be `U64Small` or `U64Object`. A raw `u64` from the host has no tag byte -- its low byte is DATA. For most draws that is a loud `fail_with_error`; for a draw whose low 8 bits happen to match the small tag it is a silently wrong number. **A test that draws a small range may pass by accident.** A reviewer must treat "the small-range test passes" as no evidence at all.

- [ ] **Step 1: the failing tests**

Add to `tests/unit/test_emitter_leaf_calls.py`:

```python
def test_a_full_range_u64_draw_survives_the_lowering() -> None:
    """F16. The mini host's `prng_u64_in_inclusive_range` returns a RAW word;
    the lowering must BOX it before anything treats it as a Val. The range is
    the full u64 on purpose: a small draw lands in the small-Val range and
    would pass even with the bug."""
    node = host(
        "prng_u64_in_inclusive_range",
        Ty.U64,
        const(Ty.U64, 0),
        const(Ty.U64, 2**64 - 1),
    )
    store, host_ = start(node)
    word = host_.invoke("probe")
    assert val.tag_of(word) in (val.TAG_U64_SMALL, val.TAG_U64_OBJECT)
    assert store.u64_of(word) == store.last_prng_draw


def test_the_boxing_branch_is_taken_and_the_narrow_is_not() -> None:
    """Structural, so the test cannot be satisfied by an accident of value: the
    body must contain the `box_u64` part and must NOT contain the EITHER-tag
    ABI check `narrow_to(Ty.U64)` would emit."""
    node = host("prng_u64_in_inclusive_range", Ty.U64, const(Ty.U64, 0), const(Ty.U64, 100))
    body, ctx = items_and_ctx(node)
    assert "box_u64" in ctx.parts_linked
    assert import_calls(body).count("prng_u64_in_inclusive_range") == 1


def test_both_arguments_reach_the_host_as_raw_words() -> None:
    """The argument half already works (`val_typed_args == (False, False)` and
    `_lower_host_call_raw` reads it), and it is pinned here beside the return
    half so the two are never separated: lowering one of these as a `Val`
    would be "wrong by a factor of 2^32 and still validate"."""
    node = host("prng_u64_in_inclusive_range", Ty.U64, const(Ty.U64, 7), const(Ty.U64, 9))
    store, host_ = start(node)
    host_.invoke("probe")
    assert store.calls[0] == ("prng_u64_in_inclusive_range", (7, 9))
```

(`start`, `host`, and `const` come from `tests/unit/test_emitter_lower_objects.py`. **`items_and_ctx` does NOT exist there and this task CREATES it** [m14] -- it is not an optional parenthetical: only `items` exists, and `items_and_ctx` is its two-line sibling returning `(probe.finish(), ctx)` so a test can inspect the `LowerCtx` as well as the emitted body. List it as a deliverable in the task report. `store.last_prng_draw` and `store.u64_of` -- the latter also CREATED, in Task 8 [m14] -- land in Task 8; until then, write the first test to assert only the TAG and add the value assertion in Task 8, and say so in the report.)

Run -> the first two FAIL. Read the failure: it must be the ABI check firing (`fail_with_error` with `CODE_ABI_CHECK_FAILED`), not a link error.

- [ ] **Step 2: the branch**

Replace `_lower_host_call` (lower.py:1348-1366):

```python
def _lower_host_call(fn: Fn, ctx: LowerCtx, e: HostCall) -> None:
    """One host call, arguments positionally, each Val or RAW per the PIN (B2).

    `HostFn.val_typed_args` is read, never re-derived: it is the pin's own
    per-position answer, and lowering a raw position as a `Val` would pass a
    `U32Val` where the host reads a plain number -- an argument that is wrong
    by a factor of 2^32 and still validates.

    **The RETURN side reads the same pin, and until M2-A nothing needed it.**
    `val_typed_ret` is `False` for 19 pinned functions, but the only one
    serpent lowered was `obj_cmp`, which has its own lowering in
    `_lower_compare` and consumes the raw -1/0/1 as a signed comparison.
    `prng_u64_in_inclusive_range` is the first `SurfaceKind.HOST_CALL` row
    whose result is a raw scalar, and sending it through `narrow_to` would
    ABI-check a bare `u64` as if it carried a tag byte: a loud failure for most
    values, and a SILENTLY WRONG number for one whose low byte happens to match
    the `U64Small` tag (C7a/F16). So a raw result is BOXED here and skips
    `narrow_to` -- there is nothing to narrow, because the word was built by
    `rebox` from a number the host produced and its tag is correct by
    construction.

    A `get_contract_data` reaching HERE is by definition one no `has` covered
    (`_lower_if_exp` intercepts the other shape), so it takes the guard.
    """
    if e.fn_name == _STORAGE_GET_FN:
        _lower_guarded_storage_get(fn, ctx, e)
        return
    if e.fn_name == _LOG_FN:
        _lower_log(fn, ctx, e)
        return
    _lower_host_call_raw(fn, ctx, e.fn_name, e.args)
    if not _host_fn(e.fn_name).val_typed_ret:
        arith.rebox(fn, ctx, e.ty)
        return
    narrow_to(fn, ctx, e.ty)
```

(`_LOG_FN = "log_from_linear_memory"` and `_lower_log` arrive in Task 7; add the constant and a `_lower_log` that raises `EmitError("Task 7")`... **no** -- that is a placeholder. Land the `_LOG_FN` arm in Task 7 instead, and in THIS task write only the `val_typed_ret` branch. The docstring above still says what it says; the `_LOG_FN` two lines are Task 7's diff.)

`arith` is already imported by `lower.py`. If `arith.rebox` refuses `e.ty` (it raises for a tag with no box part), that is a real finding: a raw-returning host call whose declared type has no boxing lowering has no honest answer, and the implementer reports BLOCKED rather than inventing one.

Run -> the three tests pass (modulo Step 1's note). Run the WHOLE emitter suite: `uv run --no-sync pytest -q tests/unit/ -k emitter`.

- [ ] **Step 3: the byte freeze, the gates, the commit**

The byte freeze is NOT optional here: `_lower_host_call` is on the path `examples/shapes.py` and `examples/bounty_board.py` both reach. Run the four node ids and quote the output. The new branch is guarded by `val_typed_ret`, which is `True` for every host function those two contracts import, so the expected result is `4 passed` -- and a failure is BLOCKED.

The four gates.

```bash
git add src/serpent/emitter/lower.py tests/unit/test_emitter_leaf_calls.py \
        tests/unit/test_emitter_lower_objects.py
git commit -m "fix(emitter): box a host call's raw scalar result instead of narrowing it

_lower_host_call ended in an unconditional narrow_to, which ABI-checks the
stack top as a Val. That held while obj_cmp was the only raw-returning host
function serpent lowered, because it has its own lowering.
prng_u64_in_inclusive_range is the first recognition-table row that returns
a raw u64, and narrowing one would fail loudly for most values and produce a
silently wrong number for a value whose low byte matches the U64Small tag.

The branch reads val_typed_ret off the pin and reboxes, and the test draws
the FULL u64 range: a small draw lands in the small-Val range and would pass
with the bug in place."
```

**Report:** the failure message from Step 1 quoted (it must be the ABI check, not a link error); the byte-freeze output; the gate outputs; whether `arith.rebox` handled `Ty.U64` without change.

---

### Task 7: `log_from_linear_memory`

**Files:**
- Modify: `src/serpent/compiler/frontend.py:197-213` (`_LINEAR_MEMORY_HOST_FNS`) **and `:626-665` (`_collect_host_fns`, [M2])**; `src/serpent/emitter/lower.py` (`_LOG_FN`, `_lower_log`, the arm in `_lower_host_call`)
- Create: `tests/unit/test_emitter_log.py`
- Test: the new file, plus `tests/unit/test_frontend.py`, `test_emitter_end_to_end.py`, and `tests/unit/test_emitter_module.py` (the `host_fns_used` invariant at `:427-434`)

**Interfaces:**
- Consumes: `ctx.memory.intern(blob, align=...)` and `ctx.memory.scratch(nbytes)` (layout.py:64-86); `_store_val(fn, ctx, address, e)` (lower.py:1557-1569); `val.pack_u32val`; `module.check_linear_memory_abi`; `frontend._needs_memory`'s `used & _LINEAR_MEMORY_HOST_FNS` row; `frontend._literal_host_fns` (`:667-681`) and the `gets_covered_by_a_has` exemption pattern (`:630-636`).
- Produces: `lower._LOG_FN`, `lower._lower_log(fn, ctx, e)`; a `_collect_host_fns` exemption set for log message literals.

- [ ] **Step 1: the failing tests**

Create `tests/unit/test_emitter_log.py`:

```python
"""`env.logs().add(msg, *vals)` -> `log_from_linear_memory` (`x._`).

The `_lower_make_struct` recipe with `intern(msg.encode("utf-8"))` in place of
the key blob: a compile-time message in the data segment, a run-time values
array in scratch, four `U32Val` immediates, one import call.

Two facts shape every test here, both read from `host.rs:1152-1188`:

* **a log can never fail.** The host wraps the whole body in
  `with_debug_mode` and returns `Ok(Val::VOID)` unconditionally, so a bad
  pointer, a bad length, or a forged Val are all swallowed -- and with
  diagnostics off the body does not run at all. Nothing here may invent a
  failure the chain does not have (F7).
* **the scratch is permanent and per-CALL-SITE.** `layout.Memory.scratch` is a
  monotonic compile-time bump allocator that never reuses, so a log in a loop
  costs its bytes ONCE (the address is compile-time) and a contract with very
  many log sites can exhaust one 64 KiB page and fail the build with SPT8003
  (F8).
"""
```

Tests:
1. `test_a_log_interns_its_message_once_per_distinct_text` -- two log sites with the same message share one pool offset; a third with different text adds a second. Assert against `memory.pool_bytes()` and the offsets `intern` returns.
2. `test_a_log_reserves_eight_bytes_of_scratch_per_value` -- one site with three values reserves 24 bytes; `memory.scratch_size` grows by exactly `8 * sum(n_i)` across several sites (F8).
3. `test_a_log_with_no_values_reserves_no_scratch_and_passes_a_zero_length` -- `env.logs().add("tick")` passes `vals_len = 0`; assert the immediate, and assert `scratch_size` did not move. The pointer passed for an empty array is whatever `scratch(0)` returns, which the host never dereferences because the length is zero.
4. `test_the_four_immediates_are_u32vals_in_the_pins_order` -- through the mini-host call log: `("log_from_linear_memory", (pack_u32val(msg_pos), pack_u32val(msg_len), pack_u32val(vals_pos), pack_u32val(vals_len)))`.
5. `test_the_values_array_holds_eight_byte_little_endian_val_words` -- read the guest memory back with `MiniHost.read_memory` and compare each 8-byte slot to `val_word` of the value.
6. `test_a_module_that_logs_exports_its_memory` -- S3/M13: `build_file` of a logging contract produces a module with exactly one exported memory named `memory`, and `compiled.needs_memory` is `True`.
7. `test_a_hand_built_module_that_logs_without_a_memory_export_is_refused` -- call `module.check_linear_memory_abi(["log_from_linear_memory"], [])` directly and assert the `EmitError` names the function. This is the negative control for the `_LINEAR_MEMORY_HOST_FNS` membership; without it, the membership is untested.
8. `test_too_many_log_sites_report_spt8003` -- build a synthetic `ModuleIR` (or call `ctx.memory.scratch` in a loop and then `memory.check()`) until scratch passes one page, and assert the `BuildLimitError(limit="scratch")` maps to SPT8003 through `emitter.__init__._limit_code` (F8).
9. `test_a_log_in_a_loop_reserves_its_scratch_once` -- the address is compile-time, so N iterations cost the same bytes as one.
10. **`test_a_contract_that_only_logs_does_not_list_string_new_from_linear_memory`** [M2] -- the frontend's side, which none of the nine above looks at. Compile a contract whose only host reach is `env.logs().add("tick")` and assert `string_new_from_linear_memory` is NOT in `compiled.host_fns_used`, and that the emitted import set equals it exactly (the invariant `test_only_host_functions_the_code_actually_calls_are_imported`, `test_emitter_module.py:427-434`, states but scopes to `COUNTER_SRC`).

Run -> FAIL (`env.logs()` compiles to a `HostCall` the generic path lowers, so the message `Const(Ty.String, ...)` is `lower_expr`'d as a STRING OBJECT -- a wrong and loud failure, which is exactly the shape the test should report before the fix).

- [ ] **Step 2: the lowering**

In `lower.py`, beside `_MAP_LM_FN`:

```python
_LOG_FN = "log_from_linear_memory"
```

and the arm in `_lower_host_call` (Task 6 left the two lines for here):

```python
    if e.fn_name == _LOG_FN:
        _lower_log(fn, ctx, e)
        return
```

and the lowering itself, beside `_lower_make_struct`:

```python
def _lower_log(fn: Fn, ctx: LowerCtx, e: HostCall) -> None:
    """`env.logs().add(msg, *vals)` -> `log_from_linear_memory` (`x._`, S2).

    `_lower_make_struct`'s recipe with one substitution: the compile-time blob
    is the MESSAGE BYTES rather than a key-descriptor array, and the run-time
    array is the values.

    The message arrives as `args[0]`, a `Const` of `Ty.String` the recognizer
    built from the source literal -- NOT a String object. Nothing constructs a
    `StringObject` here: the host reads `(msg_pos, msg_len)` out of guest
    memory itself, so the literal never becomes a chain value at all. That is
    also why a non-literal message is a compile reject (SPT1040): a computed
    message would have to be materialised as a String object and copied out,
    which is a different lowering.

    `intern` is what makes a repeated message free after the first: equal bytes
    are stored once. The values array is `scratch(8 * n)`, reserved forever for
    this CALL SITE -- so a log inside a loop pays for it once, and a contract
    with thousands of log sites fails the build with SPT8003 rather than
    corrupting memory.

    **This lowering cannot trap.** The host swallows every error inside
    `log_from_linear_memory` (K12.1), so there is no guard here, no length
    check, and no validation of the values: inventing one would fail a build
    the chain would have run.
    """
    message, *values = e.args
    if not isinstance(message, Const) or not isinstance(message.py_value, str):
        raise EmitError(
            "a log's message must reach the emitter as a string Const; the recognizer "
            "refuses anything else with SPT1040, so this is a compiler bug"
        )
    blob = message.py_value.encode("utf-8")
    msg_pos = ctx.memory.intern(blob)
    vals_pos = ctx.memory.scratch(8 * len(values))
    for i, value in enumerate(values):
        _store_val(fn, ctx, vals_pos + 8 * i, value)
    fn.i64_const(val.pack_u32val(msg_pos))
    fn.i64_const(val.pack_u32val(len(blob)))
    fn.i64_const(val.pack_u32val(vals_pos))
    fn.i64_const(val.pack_u32val(len(values)))
    fn.call_import(ctx.host_import_name(_LOG_FN), 4, has_result=True)
```

`narrow_to` is not called and does not need to be: the result is `Void`, which `_UNNARROWED_TAGS` exempts anyway -- but the call returns through `_lower_host_call`'s `_LOG_FN` arm before reaching it, so the Void `Val` stays on the stack for the `Eval` statement to drop, exactly as `contract_event`'s does.

In `src/serpent/compiler/frontend.py`, add `_LOG_FN` to the enumerated set:

```python
_LOG_LM_FN = "log_from_linear_memory"
```
```python
_LINEAR_MEMORY_HOST_FNS: frozenset[str] = frozenset(
    {
        _SYMBOL_LM_FN,
        _STRING_LM_FN,
        _BYTES_LM_FN,
        _FIELD_SYMBOL_FN,
        _STRUCT_NEW_FN,
        _LOG_LM_FN,
        "vec_new_from_linear_memory",
        "map_new_from_linear_memory",
    }
)
```

`_needs_memory` needs NO edit: its `if used & _LINEAR_MEMORY_HOST_FNS: return True` row answers for the log as soon as `log_from_linear_memory` is in the definite `used` set, which it is for any contract whose IR holds a `logs.add` `HostCall`. **Prove it rather than assume it** -- test 6 above is that proof, and `module.py:685`'s restricted consistency assertion (`the frontend reported needs_memory=False, but the emitter pooled ... and imported ...`) is what would fire if it were wrong.

**`_collect_host_fns` MUST exempt the log message's `Const`** [M2]. This is the one non-obvious edit in the task, and without it every logging contract is silently wrong about its own host reach. `_collect_host_fns` walks every IR node, and at `frontend.py:662-663`:

```python
        elif isinstance(node, Const):
            note(_literal_host_fns(node), node.loc, certain=True)
```

`_literal_host_fns` (`:667-681`) answers unconditionally on the tag, and a `Const(Ty.String, ...)` answers `("string_new_from_linear_memory",)`. Reproduced against this checkout. So the message literal -- which `_lower_log` reads back off `args[0]` and never `lower_expr`s -- would put `string_new_from_linear_memory` into `host_fns_used`, the set that feeds `_host.declared_protocol` (B4/S18) AND the import section. Nothing crashes: the restricted consistency assertion at `module.py:683-696` only fires in the other direction, and `string_new_from_linear_memory` is ungated so the protocol floor does not move. What breaks, silently, is the invariant `test_only_host_functions_the_code_actually_calls_are_imported` states -- "an unused import is dead bytes plus a false protocol input ... the emitted set must equal it exactly".

Mirror the exemption the walker already has for a `get` covered by a `has` (`frontend.py:630-636`): collect the ids of the message `Const`s first, then skip them.

```python
    # The message literal of every `log_from_linear_memory` call. The emitter
    # reads it back off `args[0]` and interns the BYTES into the data segment;
    # it never builds a StringObject, so the contract does not reach
    # `string_new_from_linear_memory` and must not report that it does (M2-A
    # [M2]). Same shape as `gets_covered_by_a_has` above, and same reason: the
    # generic per-node rule is right except for one lowering that consumes the
    # node structurally.
    log_message_literals: set[int] = {
        id(node.args[0])
        for node in walk(ir)
        if isinstance(node, HostCall) and node.fn_name == _LOG_LM_FN and node.args
    }
```

and at the `Const` arm:

```python
        elif isinstance(node, Const):
            if id(node) in log_message_literals:
                continue
            note(_literal_host_fns(node), node.loc, certain=True)
```

(Match the surrounding walker's own idiom for enumerating nodes and for skipping -- `continue` versus a guarded `note` -- rather than importing a new one. The alternative the review names, giving the message a `Ty` that is not `String`, is REJECTED: the recognizer's type model says the literal IS a string, and lying about it to dodge a collector would make SPT3018's messages wrong.)

Run -> `10 passed`.

- [ ] **Step 3: the byte freeze, the gates, the commit**

The byte freeze matters MOST here (F9): this task changes `layout.py` usage patterns and the emitter's import set, and `_LINEAR_MEMORY_HOST_FNS` is read by `module.check_linear_memory_abi` for every module. Neither deployed example logs, so the expected result is `4 passed`; anything else is BLOCKED with both digests reported.

The four gates.

```bash
git add src/serpent/compiler/frontend.py src/serpent/emitter/lower.py tests/unit/test_emitter_log.py
git commit -m "feat(emitter): lower env.logs().add through linear memory

The _lower_make_struct recipe with the message interned into the data
segment in place of the key-descriptor blob: a compile-time (pointer,
length) pair, 8 * n bytes of scratch for the values, four U32Val immediates,
one import call. log_from_linear_memory joins the enumerated
_LINEAR_MEMORY_HOST_FNS set, so a logging module declares and exports its
memory or fails the build.

No guard and no validation: the host swallows every error inside
log_from_linear_memory and returns Void unconditionally, so a lowering that
could fail would fail builds the chain would have run."
```

**Report:** the Step 1 failure message; the scratch and pool numbers from tests 1-3; the byte-freeze output; the gate outputs; the counts.

---

### Task 8: the mini host binds the same primitives, by delegation

**Files:**
- Modify: `tests/harness/hostfns.py:84-124` (imports, `__all__`), `:157-258` (`FullHost.__init__`, `bindings`), and a new section beside the env surface; `tests/unit/test_harness_hostfns.py:941-976, 1000-1022`
- Test: `tests/unit/test_harness_hostfns.py`, `tests/unit/test_emitter_leaf_calls.py`, `tests/unit/test_emitter_log.py`

**Interfaces:**
- Consumes: `serpent._crypto`, `serpent._prng`, `serpent.env`'s four new `DEFAULT_*` constants, `ObjectStore._new`/`_object`/`_vec`/`_u32`/`_blob`/`bytes_of`/`val_word`/`_log`, `MiniHost.read_memory`, `val.pack_u32val`, `tests.harness.errors.HostError`/`HostTrap`.
- Produces on `FullHost`: `contract_address`, `network_id`, `protocol_version`, `max_entry_ttl` attributes; a `logs: list[tuple[str, tuple[int, ...]]]` record; `last_prng_draw`; sixteen new bindings.

**Every crypto and PRNG body is a ONE-LINE delegation into `serpent._crypto`/`serpent._prng`.** That is D-6's whole point: the mini host and tier 1 cannot disagree, because there is one implementation. A binding that re-derived an answer here would be a second oracle, which is the shape S8 names.

- [ ] **Step 1: the coverage assertions, failing first**

`tests/unit/test_harness_hostfns.py:941` (`test_the_bindings_cover_every_host_function_the_compiler_can_emit`) computes `required` from `ENV_HOST_FN_TARGETS | CONTAINER_HOST_FN_TARGETS | _UNREACHED_BUT_STILL_EMITTED | _EMITTER_ADDITIONS`, so Task 4's grown tables make it FAIL with the sixteen missing names. Run it first and QUOTE the list -- it is the task's own work order, derived from the compiler rather than restated:

```bash
uv run --no-sync pytest -q tests/unit/test_harness_hostfns.py::test_the_bindings_cover_every_host_function_the_compiler_can_emit
```

`_FIXTURES` (line 1006) gains `_ROOT / "tests" / "fixtures" / "crypto_surface.py"` and `_ROOT / "examples" / "raffle.py"` -- both land later (Tasks 10 and 11), so add them in THOSE tasks, not here, and say so in the report.

- [ ] **Step 2: the bindings**

In `tests/harness/hostfns.py`, extend the imports and `__init__`:

```python
from serpent import _crypto, _prng
from serpent.env import (
    DEFAULT_CONTRACT_ADDRESS,
    DEFAULT_LEDGER_SEQUENCE,
    DEFAULT_LEDGER_TIMESTAMP,
    DEFAULT_MAX_ENTRY_TTL,
    DEFAULT_NETWORK_ID,
    DEFAULT_PROTOCOL_VERSION,
    UNSEEDED_PRNG_SEED,
)
```
```python
        #: The context stubs, settable per instance like the ledger pair above
        #: and IMPORTED rather than restated: one definition across tiers (S13).
        self.contract_address = Address(DEFAULT_CONTRACT_ADDRESS)
        self.network_id = DEFAULT_NETWORK_ID
        self.protocol_version = DEFAULT_PROTOCOL_VERSION
        self.max_entry_ttl = DEFAULT_MAX_ENTRY_TTL
        #: Every `log_from_linear_memory` call, as `(message, val words)`.
        #: The host RECORDS a diagnostic event; this rig records the same
        #: thing, and like the host it never fails.
        self.logs: list[tuple[str, tuple[int, ...]]] = []
        #: This mini host's frame PRNG. Seeded from the tier-1 constant so an
        #: unseeded mini-host draw equals an unseeded tier-1 draw -- which is
        #: convenient and is NOT evidence about the chain: no tier pins an
        #: unseeded draw (E11.4).
        self._prng_state = _prng.from_prng_seed(UNSEEDED_PRNG_SEED)
        #: The last `prng_u64_in_inclusive_range` answer, as a RAW word, so an
        #: emitter test can compare the boxed guest value against it (F16).
        self.last_prng_draw = 0
```

and the table:

```python
            # -- context (x)
            "get_ledger_version": self.get_ledger_version,
            "get_ledger_network_id": self.get_ledger_network_id,
            "get_max_live_until_ledger": self.get_max_live_until_ledger,
            "get_current_contract_address": self.get_current_contract_address,
            "log_from_linear_memory": self.log_from_linear_memory,
            # -- crypto (c)
            "compute_hash_sha256": self.compute_hash_sha256,
            "compute_hash_keccak256": self.compute_hash_keccak256,
            "verify_sig_ed25519": self.verify_sig_ed25519,
            "recover_key_ecdsa_secp256k1": self.recover_key_ecdsa_secp256k1,
            "verify_sig_ecdsa_secp256r1": self.verify_sig_ecdsa_secp256r1,
            # -- prng (p)
            "prng_reseed": self.prng_reseed,
            "prng_bytes_new": self.prng_bytes_new,
            "prng_u64_in_inclusive_range": self.prng_u64_in_inclusive_range,
            "prng_vec_shuffle": self.prng_vec_shuffle,
            # -- buf (b), the two protocol-23 conversions
            "string_to_bytes": self.string_to_bytes,
            "bytes_to_string": self.bytes_to_string,
```

and the bodies, in a new section beside the env surface:

```python
    # -- context, crypto, prng (M2-A) -----------------------------------------

    def get_ledger_version(self) -> int:
        """`x.2`: the protocol version as a `U32Val`."""
        self._log("get_ledger_version")
        return val.pack_u32val(self.protocol_version)

    def get_ledger_network_id(self) -> int:
        """`x.6`: a fresh 32-byte `BytesObject`. env.json: "always 32 bytes".

        `Bytes32`, not `Bytes` [B3]. The rig's job is to be the SAME oracle as
        the tier-1 facade, and `Ledger.network_id()` returns a `Bytes32`. The
        two payloads compare equal either way, but
        `test_env_differential.Outcome.answer_type` is `type(answer).__name__`
        (`:144`), so a plain `Bytes` here makes every network-id row fail with
        "the two models disagree" over a type NAME while the VALUES match.
        """
        self._log("get_ledger_network_id")
        return self._new(val.TAG_BYTES_OBJECT, Bytes32(self.network_id))

    def get_max_live_until_ledger(self) -> int:
        """`x.8`: `sequence + max_entry_ttl - 1` (K6, `ledger_info.rs:25-30`).

        Computed from this rig's OWN ledger stub, so a test that moves
        `ledger_sequence` moves this too -- the same live-read discipline
        `serpent.env.Ledger` documents.
        """
        self._log("get_max_live_until_ledger")
        return val.pack_u32val(self.ledger_sequence + self.max_entry_ttl - 1)

    def get_current_contract_address(self) -> int:
        """`x.7`: the running contract's address as an `AddressObject`."""
        self._log("get_current_contract_address")
        return self._new(val.TAG_ADDRESS_OBJECT, self.contract_address)

    def log_from_linear_memory(self, msg_pos: int, msg_len: int, vals_pos: int,
                               vals_len: int) -> int:
        """`x._`: read the message and the `Val` array, record, return Void.

        **This can never fail**, exactly as the host cannot (K12.1): the host
        wraps its whole body in `with_debug_mode` and returns `Ok(Val::VOID)`
        unconditionally, so a bad pointer or a forged Val is swallowed. A rig
        that raised here would fail tests on shapes the chain tolerates and,
        worse, would teach an author to "fix" a non-problem -- so every read
        below is wrapped and a failure records a marker instead.
        """
        try:
            message = self._blob(msg_pos, msg_len).decode("utf-8", "surrogateescape")
            count = self._u32(vals_len)
            raw = self._read(self._u32(vals_pos), 8 * count)
            words = tuple(
                int.from_bytes(raw[8 * i : 8 * i + 8], "little") for i in range(count)
            )
        except Exception:  # noqa: BLE001 -- the host swallows EVERYTHING here
            self._log("log_from_linear_memory", msg_pos, msg_len, vals_pos, vals_len)
            self.logs.append(("<unreadable>", ()))
            return val.VOID_VAL
        self._log("log_from_linear_memory", msg_pos, msg_len, vals_pos, vals_len)
        self.logs.append((message, words))
        return val.VOID_VAL

    def compute_hash_sha256(self, x: int) -> int:
        """`c._`. One line into `serpent._crypto`: the mini host and tier 1
        share ONE implementation, so they cannot disagree (D-6/S6).

        `Bytes32`, matching `Crypto.sha256`'s return type [B3] -- see
        `get_ledger_network_id` for why the CLASS and not just the payload has
        to match.
        """
        self._log("compute_hash_sha256", x)
        return self._new(val.TAG_BYTES_OBJECT, Bytes32(_crypto.sha256(self.bytes_of(x))))

    def compute_hash_keccak256(self, x: int) -> int:
        """`c.1`, same delegation, same `Bytes32` [B3]."""
        self._log("compute_hash_keccak256", x)
        return self._new(val.TAG_BYTES_OBJECT, Bytes32(_crypto.keccak256(self.bytes_of(x))))

    def verify_sig_ed25519(self, k: int, x: int, s: int) -> int:
        """`c.0`: Void on success, a TRAP on failure.

        A `HostTrap`, not a `HostError`: the host's failure is
        `Error(Crypto, InvalidInput)` (or `Object(UnexpectedSize)` for a wrong
        signature length), neither of which the guest can catch -- so the
        observable is "the invocation died", which is what `HostTrap` means in
        this rig.
        """
        self._log("verify_sig_ed25519", k, x, s)
        if not _crypto.ed25519_verify(self.bytes_of(k), self.bytes_of(x), self.bytes_of(s)):
            raise HostTrap("verify_sig_ed25519: failed ED25519 verification")
        return val.VOID_VAL

    def recover_key_ecdsa_secp256k1(self, msg_digest: int, signature: int,
                                    recovery_id: int) -> int:
        """`c.2`: the 65-byte SEC-1 uncompressed key, or a trap.

        `Bytes65`, matching `Crypto.secp256k1_recover`'s return type [B3].
        """
        self._log("recover_key_ecdsa_secp256k1", msg_digest, signature, recovery_id)
        recovered = _crypto.secp256k1_recover(
            self.bytes_of(msg_digest), self.bytes_of(signature), self._u32(recovery_id)
        )
        if recovered is None:
            raise HostTrap("recover_key_ecdsa_secp256k1: ECDSA-secp256k1 recovery failed")
        return self._new(val.TAG_BYTES_OBJECT, Bytes65(recovered))

    def verify_sig_ecdsa_secp256r1(self, public_key: int, msg_digest: int,
                                   signature: int) -> int:
        """`c.3`: Void on success, a trap on failure."""
        self._log("verify_sig_ecdsa_secp256r1", public_key, msg_digest, signature)
        if not _crypto.secp256r1_verify(
            self.bytes_of(public_key), self.bytes_of(msg_digest), self.bytes_of(signature)
        ):
            raise HostTrap("verify_sig_ecdsa_secp256r1: failed secp256r1 verification")
        return val.VOID_VAL

    def prng_reseed(self, seed: int) -> int:
        """`p._`: REPLACE this frame's PRNG. Exactly 32 bytes."""
        self._log("prng_reseed", seed)
        payload = self.bytes_of(seed)
        if len(payload) != _prng.SEED_BYTES:
            raise HostTrap(
                f"prng_reseed: Unexpected size of BytesObject ({len(payload)}, want 32)"
            )
        self._prng_state = _prng.from_prng_seed(payload)
        return val.VOID_VAL

    def prng_bytes_new(self, length: int) -> int:
        """`p.0`."""
        self._log("prng_bytes_new", length)
        return self._new(
            val.TAG_BYTES_OBJECT, Bytes(self._prng_state.fill_bytes(self._u32(length)))
        )

    def prng_u64_in_inclusive_range(self, lo: int, hi: int) -> int:
        """`p.1`: RAW u64 in, RAW u64 out -- the pin's only such row here.

        No `pack_u32val`, no `val_word`: `val_typed_args` is `(False, False)`
        and `val_typed_ret` is `False`, so the trampoline's unsigned masking is
        the ONLY transformation that happens on either side. `last_prng_draw`
        records the answer so an emitter test can prove the guest BOXED it
        rather than narrowing it (F16).
        """
        self._log("prng_u64_in_inclusive_range", lo, hi)
        if lo > hi:
            raise HostTrap(f"prng_u64_in_inclusive_range: lo={lo} > hi={hi}")
        self.last_prng_draw = _prng.u64_in_inclusive_range(self._prng_state, lo, hi)
        return self.last_prng_draw

    def prng_vec_shuffle(self, vec: int) -> int:
        """`p.2`: a NEW `VecObject`; the input is untouched (functional)."""
        self._log("prng_vec_shuffle", vec)
        return self._new(
            val.TAG_VEC_OBJECT, _prng.shuffle(self._prng_state, self._vec(vec))
        )

    def string_to_bytes(self, s: int) -> int:
        """`b.n` (protocol 23): the same payload, re-tagged. No validation."""
        self._log("string_to_bytes", s)
        return self._new(
            val.TAG_BYTES_OBJECT, Bytes(self.text_of(s).encode("utf-8", "surrogateescape"))
        )

    def bytes_to_string(self, b: int) -> int:
        """`b.o` (protocol 23): the same payload, re-tagged.

        `surrogateescape`, matching `serpent.types.String` (E7): the host does
        no UTF-8 validation, so a strict decode here would invent a trap.
        """
        self._log("bytes_to_string", b)
        return self._new(
            val.TAG_STRING_OBJECT, String(self.bytes_of(b).decode("utf-8", "surrogateescape"))
        )
```

**CREATE `ObjectStore.u64_of(self, word: int) -> int`** [m14]. It does NOT exist today (checked); it is not an "if it does not exist" hedge but a deliverable of this task, and Task 6's boxing test consumes it to unbox a `U64Small` or a `U64Object` uniformly. Mirror `bytes_of`/`text_of`'s shape in `tests/harness/objects.py`: read the tag, return the inline payload for the small encoding and the stored `U64`'s value for the object one, and raise the store's own error for anything else. If `val` turns out to have an equivalent helper, use it instead and say which in the report.

**The re-typed bindings are load-bearing** [B3]. Three bindings above answer `Bytes32` and one answers `Bytes65` rather than plain `Bytes`, which means `hostfns.py` imports `Bytes32` and `Bytes65` alongside `Bytes`. This is the half of B3's fix that lives here; the other half (`_DECODABLE` gaining `Bytes`) is Task 10's. Neither alone makes the differential's hash, recovery, or network-id rows runnable. Add a test in this task that pins it directly:

```python
def test_the_mini_hosts_byte_answers_carry_the_facades_classes() -> None:
    """B3: `Outcome.answer_type` is a type NAME, so `Bytes` where the facade
    says `Bytes32` reads as a disagreement even though the payloads are equal
    (`Bytes32(bytes(32)) == Bytes(bytes(32))` is True). The rig is meant to be
    the same oracle, so it carries the same classes.
    """
    host = FullHost()
    empty = host.val_word(Bytes(b""))  # objects.py:564, the public encoder
    assert type(host.chain_value(host.compute_hash_sha256(empty))) is Bytes32
    assert type(host.chain_value(host.compute_hash_keccak256(empty))) is Bytes32
    assert type(host.chain_value(host.get_ledger_network_id())) is Bytes32
```

- [ ] **Step 3: the coverage tests, the gates, the commit**

Re-run the two coverage tests; both must pass with no edit to `_EMITTER_ADDITIONS` (every new name comes in through `ENV_HOST_FN_TARGETS`/`CONTAINER_HOST_FN_TARGETS`, which is the point). `test_the_bindings_omit_what_no_lowering_can_reach` must STILL pass -- no ZK name, no `set_base_prng_seed`, nothing the compiler cannot reach.

Add three tests to `tests/unit/test_harness_hostfns.py`:
1. `test_a_mini_host_log_never_raises` -- call `log_from_linear_memory` with a pointer past the end of memory and assert it returns `val.VOID_VAL` and records `("<unreadable>", ())` (F7).
2. `test_the_mini_hosts_crypto_is_the_same_implementation_as_tier_1` -- for each of the five, assert the binding's answer equals the tier-1 facade's on the same input. This is D-6's claim, as a test.
3. `test_the_mini_hosts_reseeded_stream_is_the_hosts_own_vector` -- reseed with `[1] * 32` and assert `prng_bytes_new(32)` matches K15's bytes.

Then Tasks 5, 6, and 7's emitter files run green end to end. The four gates.

```bash
git add tests/harness/hostfns.py tests/harness/objects.py tests/unit/test_harness_hostfns.py \
        tests/unit/test_emitter_leaf_calls.py tests/unit/test_emitter_log.py
git commit -m "feat(harness): bind the sixteen new host functions in the mini host

Four context functions, the log reader, the five crypto functions, the four
prng functions, and the two protocol-23 conversions. Every crypto and prng
body is a one-line delegation into serpent._crypto / serpent._prng, so the
mini host and the tier-1 model cannot disagree by construction.

The log binding swallows every read error and returns Void, exactly as the
host does, and prng_u64_in_inclusive_range passes raw u64s in both
directions with no Val packing -- the pin's only such row here."
```

**Report:** the missing-name list quoted from Step 1; the three new tests' outcomes; whether `ObjectStore` needed a `u64_of`; the gate outputs; the counts.

---

### Task 9: `RealEnv` and the Rust facade

**Files:**
- Modify: `host/src/lib.rs` (three new `#[pymethods]`), `host/serpent_host.pyi`, `src/serpent/testing/_real.py`
- Test: `tests/real_host/test_real_env.py` (or wherever `RealEnv`'s own tests live)

**Interfaces:**
- Produces on the Rust class: `fn network_id(&self) -> PyResult<Vec<u8>>`, `fn sequence_number(&self) -> PyResult<u32>`, `fn set_base_prng_seed(&self, seed: &[u8]) -> PyResult<()>`.
- Produces on `RealEnv`: `network_id(self) -> bytes`, `set_base_prng_seed(self, seed: bytes) -> None`. (`RealEnv.sequence` already exists as a PYTHON-side counter; the Rust getter is what proves the counter has not drifted.)
- Consumes: `self.env.host()` (`soroban_sdk::Env::host`, already used at `lib.rs:223/255/315`); `testutils::Ledger::get`.

This task may run in parallel with Tasks 4 through 8; it depends only on Task 2.

- [ ] **Step 1: the Rust methods**

In `host/src/lib.rs`, inside the single `#[pymethods] impl RealEnv` block, each body wrapped in `contained(|| ...)` like every existing method:

```rust
    /// The ledger's network id, read back out of the host's own `LedgerInfo`.
    ///
    /// Not a restatement of what `new` was given: a differential that compared
    /// tier 1's answer against a Python-side copy of the constructor argument
    /// would prove nothing about `get_ledger_network_id`.
    fn network_id<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        contained(|| {
            let info = self.env.ledger().get();
            Ok(PyBytes::new(py, &info.network_id))
        })
    }

    /// The ledger sequence, read back out of the host's own `LedgerInfo`.
    fn sequence_number(&self) -> PyResult<u32> {
        contained(|| Ok(self.env.ledger().get().sequence_number))
    }

    /// Seed the host's BASE PRNG (`Host::set_base_prng_seed`).
    ///
    /// A PROBE, not a modelling surface. soroban-sdk seeds the base PRNG to
    /// all zeros for every test env, so an unreseeded draw is already
    /// deterministic here -- but it is a function of the sdk's private
    /// frame-derivation order, which serpent must not pin (M2-A E11.4). This
    /// exists so the differential can DEMONSTRATE that dependence: change the
    /// base seed, and an unreseeded draw changes while a reseeded one does
    /// not.
    fn set_base_prng_seed(&self, seed: &[u8]) -> PyResult<()> {
        contained(|| {
            let bytes: [u8; 32] = seed
                .try_into()
                .map_err(|_| invalid("set_base_prng_seed takes exactly 32 bytes".to_string()))?;
            self.env
                .host()
                .set_base_prng_seed(bytes)
                .map_err(|e| failure("host", "Context", 0, format!("{e:?}")))?;
            Ok(())
        })
    }
```

If `Host::set_base_prng_seed` is not reachable through `soroban_env_host` at this pin, report BLOCKED with the compiler error and drop the method: E11 does not require it (it is "a probe"), and the unseeded `host_diverges` row in Task 10 can be justified from the source alone. Do NOT reach for an unsafe or private path.

Mirror all three in `host/serpent_host.pyi` with the same docstrings (the stub is the CONTRACT: `mypy_path = ["host"]` types `_real.py` against it whether or not the extension is built).

- [ ] **Step 2: the Python wrappers**

In `src/serpent/testing/_real.py`, beside `protocol_version()`:

```python
    def network_id(self) -> bytes:
        """The ledger's network id, read back out of the embedded host.

        Equal to `serpent.env.DEFAULT_NETWORK_ID` by construction (the same
        constant is what `__init__` fed the Rust layer), and read back rather
        than restated so a differential over `env.ledger().network_id()` is
        evidence about the host function rather than about a Python constant.
        """
        return bytes(self._raw.network_id())

    def set_base_prng_seed(self, seed: bytes) -> None:
        """Seed the host's BASE prng. A PROBE (see the Rust docstring).

        serpent models the RESEEDED stream exactly and refuses to model the
        unseeded one, because on chain the frame PRNG descends from an embedder
        seed (the txset hash and the transaction's apply-order position). This
        method exists so one test can show that dependence rather than assert
        it: `tests/real_host/test_prng_real.py`.
        """
        self._raw.set_base_prng_seed(seed)
```

and, in `advance`/`set_ledger`, add an assertion-free consistency test rather than changing behaviour: a new test asserts `env.sequence == env._raw.sequence_number()` after a deploy, an `advance`, and a `set_ledger`.

- [ ] **Step 3: the Rust gate, the four gates, the commit**

```bash
cd host && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test
cd .. && VIRTUAL_ENV=$PWD/.venv uvx maturin develop --release --manifest-path host/Cargo.toml
uv run --no-sync python -c "import serpent_host; print(serpent_host.__file__)"
```

Then the four gates. **Never run `uv sync` after the maturin build** (D10's prune trap).

```bash
git add host/src/lib.rs host/serpent_host.pyi src/serpent/testing/_real.py \
        tests/real_host/test_real_env.py
git commit -m "feat(testing): expose the network id, the ledger sequence, and the base prng seed

network_id() and sequence_number() read the embedded host's own LedgerInfo
back rather than restating what the constructor was given, so a differential
over env.ledger().network_id() is evidence about the host function.
set_base_prng_seed is a probe: it exists so one test can demonstrate that an
unreseeded draw depends on the embedder's seed, which is why no tier pins
one."
```

**Report:** the Rust gate output quoted; whether `set_base_prng_seed` was reachable; the four gate outputs; the counts.

---

### Task 10: the three-tier differential -- `PRNG_VECTORS`, `ENV_SCENARIOS`, `HOST_FACTS`

**Files:**
- Create: `tests/fixtures/crypto_surface.py`, `tests/semantics/prng_vectors.py`, `tests/unit/test_prng_differential.py`, `tests/real_host/test_prng_real.py`
- Modify: **`tests/unit/test_env_differential.py:144, 231-241, 274-283, 302-308`** [B1] [B2] [B3] -- the differential RUNNER, which v1 did not name anywhere and which owns both non-real legs; `tests/semantics/env_scenarios.py`, `tests/semantics/host_facts.py:161-244`, `tests/fixtures/host_facts.py`, `tests/unit/test_harness_hostfns.py:1006` (`_FIXTURES`), `.github/workflows/ci.yml:140-148`
- Test: all of the above, plus `tests/real_host/test_env_scenarios_real.py`, `test_host_facts_real.py`

**This is where "hollow" is caught or missed.** Tier 1 agreeing with the mini host proves nothing about either -- they share `serpent._crypto`/`serpent._prng` by construction. Only the real leg is evidence.

- [ ] **Step 0: make the runner able to carry A's rows AT ALL** [B1] [B2] [B3]

**Do this before writing a single row.** Twenty of A's rows are unrunnable against `tests/unit/test_env_differential.py` as it stands today, for three independent reasons, each reproduced by the plan review. Nothing else in this task can be trusted green until these three edits land.

**(a) The tier-1 `host_error` arm is hardcoded to one exception class.** `test_env_differential.py:231-241`:

```python
        elif scenario.kind == "host_error":
            # Finding F2 (ruled 2026-09-02): the model mirrors the host's TRAP
            # rather than laundering it into a contract code. ...
            with pytest.raises(StorageTrap):
                method(env, *call.args)
            trapped = True
```

Every `host_error` row that exists today is a storage row, so the literal has never been wrong. A's seven `host_error` rows raise `CryptoTrap` or `PrngTrap` and would fail with `DID NOT RAISE StorageTrap`, with `pytest.raises` re-raising the real trap out of the `with`. Widen it to the TUPLE (the controller's ruling; a shared `HostTrap` base in `env.py` was the review's alternative and was NOT taken -- see Task 3 Step 2):

```python
        elif scenario.kind == "host_error":
            # Finding F2 (ruled 2026-09-02): the model mirrors the host's TRAP
            # rather than laundering it into a contract code.
            # M2-A [B1]: the row promises the CLASS of outcome -- a trap the
            # guest cannot catch -- not the identity of the Python exception.
            # The tuple is the model's whole trap family; a new family in a
            # later sub-plan adds a name here.
            with pytest.raises((StorageTrap, CryptoTrap, PrngTrap)):
                method(env, *call.args)
            trapped = True
```

**(b) `_wasm` has NO `host_error` branch at all.** `test_env_differential.py:274-283`:

```python
    if scenario.kind == "contract_error":
        code = _wasm_code(mini, call.method, *words)
    else:
        returned = mini.invoke(call.method, *words)
        assert returned is not None, f"{call.method} returned nothing"
```

A trapping call propagates the harness's `HostTrap` (`tests/harness/errors.py:35`) straight out of `_wasm` and the test ERRORS. The two `host_error` rows that exist today (`env_scenarios.py:999` and `:1017`) both carry `mini_host_gap=TTL_REASON`, which is what has kept this path unexercised. A's rows have no legitimate gap -- Task 8 binds `verify_sig_ed25519`, `prng_reseed`, and the rest in `FullHost` and makes them raise `HostTrap` ON PURPOSE, so the mini host CAN run them and a `mini_host_gap` would be a false claim about the rig. Add the arm, mirroring the tier-1 shape and the real leg's (`tests/real_host/test_env_scenarios_real.py:111`):

```python
    elif scenario.kind == "host_error":
        # M2-A [B2]: the mini host's first host_error rows. `FullHost` raises
        # the harness's HostTrap for exactly the inputs the real host refuses,
        # so this leg is a real check and not a formality.
        with pytest.raises(HostTrap):
            mini.invoke(call.method, *words)
        trapped = True
```

and set `trapped=True` on the returned `Outcome` so `_comparable` compares the two legs on it -- the field already exists (`test_env_differential.py:153`).

**Leave the two existing TTL rows' `mini_host_gap` in place.** This edit incidentally makes them runnable at tier 2a, which they are not today. That is NOT this task's scope: removing their gap is a separate claim about the rig that M2-D owns along with the rest of the TTL algebra. The task review checks that both rows still carry `TTL_REASON`.

**(c) `_DECODABLE` cannot decode a `Bytes`, and `answer_type` splits `Bytes32` from `Bytes`.** `test_env_differential.py:302-308`:

```python
_DECODABLE = (Bool, U32, U64, Symbol, String, Address)


def _decoded(host: FullHost, word: int) -> ChainValue:
    """One `Val` word as the chain value the table's expectations are built from."""
    value = host.chain_value(word)
    assert isinstance(value, _DECODABLE), f"no scenario observes a {type(value).__name__}"
```

`ObjectStore.chain_value` (`tests/harness/objects.py:388-393`) returns a `Bytes` for a `TAG_BYTES_OBJECT`, so every hash row, the recovery row, and the network-id row fire `no scenario observes a Bytes`. A is the first sub-plan whose headline answers ARE byte strings. Add it:

```python
# M2-A [B3]: `Bytes` covers `Bytes32`/`Bytes64`/`Bytes65` by isinstance, which
# is what the crypto rows answer with. `Vec`/`Map` stay out for the reason
# `_decoded`'s docstring gives (_VecRank/_MapRank); the PRNG shuffle is
# compared in tests/unit/test_prng_differential.py instead, through
# `ObjectStore.chain_value_as`.
_DECODABLE = (Bool, U32, U64, Symbol, String, Bytes, Address)
```

The second half of (c) lives in Task 8 and is already done there: the mini host's bindings answer `Bytes32`/`Bytes65`, matching the facade, so `Outcome.answer_type` (`:144`, `type(answer).__name__`) compares equal. **`answer_type` is NOT relaxed.** The payloads compare equal regardless (`Bytes32(bytes(32)) == Bytes(bytes(32))` is `True`, reproduced), so relaxing the type check would silently stop checking something real; the rig re-types instead, which is also what "the rig is the same oracle" means.

Run the existing suite before adding any A row and confirm it is unchanged:

```bash
uv run --no-sync pytest -q tests/unit/test_env_differential.py
```

Expected: the same count as at HEAD. If a storage row moved, one of the three edits above was wider than described -- stop and report.

- [ ] **Step 1: `tests/fixtures/crypto_surface.py`**

The contract the four surfaces the raffle does not use are exercised through (E13). One method per surface, each one leaf-shaped, plus the two conversions:

```python
"""Every M2-A leaf surface the ninth example does not reach.

`examples/raffle.py` uses sha256, the PRNG, the logs, and the ledger
accessors because a commit-reveal raffle genuinely needs them. keccak256,
ed25519_verify, secp256k1_recover, secp256r1_verify, and the two
String/Bytes conversions have no natural place in one -- an example that
verifies a signature nobody produced teaches nothing -- so they are covered
HERE, by `ENV_SCENARIOS` rows and `HOST_FACTS` rows, as ruled.
"""

from serpent import U32, Bytes, Bytes32, Bytes65, Env, String, contract


@contract
class CryptoSurface:
    def keccak(self, env: Env, data: Bytes) -> Bytes32:
        return env.crypto().keccak256(data)

    def sha(self, env: Env, data: Bytes) -> Bytes32:
        return env.crypto().sha256(data)

    def ed25519(self, env: Env, key: Bytes, message: Bytes, signature: Bytes) -> U32:
        env.crypto().ed25519_verify(key, message, signature)
        return U32(1)

    def recover(self, env: Env, digest: Bytes, signature: Bytes, rid: U32) -> Bytes65:
        return env.crypto().secp256k1_recover(digest, signature, rid)

    def p256(self, env: Env, key: Bytes, digest: Bytes, signature: Bytes) -> U32:
        env.crypto().secp256r1_verify(key, digest, signature)
        return U32(1)

    def to_bytes(self, env: Env, text: String) -> Bytes:
        return text.to_bytes()

    def to_string(self, env: Env, data: Bytes) -> String:
        return data.to_string()

    def round_trip(self, env: Env, data: Bytes) -> Bytes:
        return data.to_string().to_bytes()

    def identity(self, env: Env) -> Bytes32:
        return env.ledger().network_id()

    def max_live_until(self, env: Env) -> U32:
        return env.ledger().max_live_until_ledger()

    def protocol(self, env: Env) -> U32:
        return env.ledger().version()
```

(The `ed25519`/`p256` methods return `U32(1)` rather than `None` so a `kind="value"` row can assert the call SUCCEEDED; a failure traps and the row is `kind="host_error"`.)

Add its path to `_FIXTURES` in `tests/unit/test_harness_hostfns.py` (its own comment says "Adding a contract anywhere means adding it here too") and to `tests/unit/test_frontend_fuzz.py`'s `CORPUS` if that list is not glob-driven for `tests/fixtures/`.

**This fixture declares a protocol floor of 23, the first in the tree above 22** [m11]. `to_bytes`/`to_string` reach `string_to_bytes`/`bytes_to_string`, both `min_protocol = 23`, so `CryptoSurface.declared_protocol` is **23** while every existing fixture and example is 22 or below. Nothing breaks: `crypto_surface.py` is not in `test_emitter_end_to_end.FIXTURES` (`:126-133`), so no floor pin moves. But E8's whole claim is about WHICH floors move, so **state the measured 23 explicitly in the task report** rather than leaving a reader to infer it, and say which pins were checked and found not to cover it.

- [ ] **Step 2: `tests/semantics/prng_vectors.py`**

One importable corpus, three legs -- the shape `ENV_SCENARIOS` established (D7):

```python
"""The reseeded PRNG stream, as a corpus every tier replays (E11.1).

This is the strongest evidence M2-A can produce, and the ONLY evidence that
will ever exist for `prng_vec_shuffle`: soroban-sdk's doctests and the host's
own tests carry no shuffle vector, so the `gen_index` arm is verified by
source reading plus THIS row running against the real host (F5).

What is pinned, and nothing else (E11):

1. the reseeded stream, tier 1 == tier 2a == the real host, value for value;
2. reseed determinism within one tier (two reseeds, same seed, same draws);
3. the two error shapes.

What is NOT pinned, at any tier: a draw made BEFORE a reseed. On chain the
frame PRNG descends from an embedder seed serpent cannot know, and pinning
the sdk test host's zero-base derivation would couple this suite to
soroban-sdk's private frame ordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
PRNG_SURFACE = _ROOT / "tests" / "fixtures" / "prng_surface.py"


@dataclass(frozen=True)
class PrngVector:
    """One reseeded draw sequence, with the answer every tier must give."""

    name: str
    #: The 32 bytes passed to `env.prng().seed(...)`.
    seed: bytes
    #: The contract method to invoke, and its arguments after the seed.
    method: str
    args: tuple[int, ...]
    #: The expected answer, as a plain Python value the leg re-types.
    expect: object
    #: Why this row exists -- a citation, so it reads without a plan beside it.
    fact: str


PRNG_VECTORS: tuple[PrngVector, ...] = (
    PrngVector(
        name="bytes_new_32_matches_the_sdk_doctest",
        seed=bytes([1]) * 32,
        method="draw_bytes",
        args=(32,),
        expect=bytes([
            58, 248, 248, 38, 210, 150, 170, 117, 122, 110, 9, 101, 244, 57, 221, 102,
            164, 48, 43, 104, 222, 229, 242, 29, 25, 148, 88, 204, 130, 148, 2, 66,
        ]),  # fmt: skip
        fact="K15: soroban-sdk 28.0.0-rc.1 `prng.rs` doctest, `gen_len::<Bytes>(32)`",
    ),
    PrngVector(
        name="full_range_u64_matches_the_sdk_doctest",
        seed=bytes([1]) * 32,
        method="draw_full_range",
        args=(),
        expect=8478755077819529274,
        fact="K15/F16: `gen::<u64>()`; the FULL range, which is also the boxing test",
    ),
    PrngVector(
        name="gen_range_one_to_a_hundred_matches_the_sdk_doctest",
        seed=bytes([1]) * 32,
        method="draw_range",
        args=(1, 100),
        expect=46,
        fact="K15: `gen_range::<u64>(1..=100)`, the Lemire arm with exact rejection",
    ),
    PrngVector(
        name="shuffle_of_a_known_vec_after_a_known_reseed",
        seed=bytes([1]) * 32,
        method="draw_shuffle",
        args=(10,),
        # [M12] MEASURED on the real host and pinned, with the run that
        # produced it quoted RIGHT HERE so the number carries its own
        # provenance in the file rather than only in a commit message:
        #
        #   $ SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -s \
        #       tests/real_host/test_prng_real.py -k shuffle
        #
        # and the run's own output line is copied in beneath that command,
        # verbatim, in the same commit that fills `expect` in. Until that run
        # has happened `expect` is None and the assertion in
        # test_prng_differential.py fails. Do NOT fill it from a tier-1 run.
        expect=None,  # measured on the FIRST real-host run and pinned then; see below
        fact=(
            "F5: the `gen_index` u32 sampler arm has NO vector in soroban-sdk's doctests "
            "or the host's own tests, so this row is the only evidence it will ever have"
        ),
    ),
    PrngVector(
        name="a_second_reseed_with_the_same_seed_repeats_the_draw",
        seed=bytes([1]) * 32,
        method="draw_twice",
        args=(32,),
        expect=True,
        fact="E11.2, and what the host's own `prng_test` asserts",
    ),
)
```

**The shuffle row's `expect=None` is a MEASUREMENT, not a placeholder**, and it is the one row in M2-A whose expected value is not known before the run. Procedure, stated so it cannot become a fitted test: run the REAL leg first, in isolation, with `-s`; record the permutation it produced; write that literal into the row in a commit of its own whose message quotes the real-host output; THEN run the tier-1 and tier-2a legs against it. If tier 1 disagrees with the recorded real value, `serpent._prng.shuffle` is wrong and the FIX is in `_prng.py` -- never in the row. A `PrngVector` whose `expect` is still `None` when the task closes is a BLOCKED condition, and `tests/unit/test_prng_differential.py` asserts `all(v.expect is not None for v in PRNG_VECTORS)` so it cannot be forgotten.

**[M12] The guard is made as mechanical as it can be, and the rest is a named review duty.** The `all(v.expect is not None)` assertion stops the row being FORGOTTEN; nothing in the code can distinguish "measured on the real host, then tier 1 checked against it" from "measured at tier 1, then written down" once the commit has landed. Two things close that gap, and BOTH are required:

1. **the quoted real-host `-s` output lives in a comment beside the literal**, in the row itself (see the fence above) -- not only in the commit message, which no future reader of `prng_vectors.py` will go looking for;
2. **the Task 10 review MUST confirm the quoted output PREDATES the tier-1 run.** This is a stated report requirement, checked against `git log` order and the commit that introduced the literal: the measuring commit contains ONLY the real-host run and the literal, and the tier-1/tier-2a legs are green in a LATER commit. A review that cannot establish that ordering returns the task BLOCKED. Same for `REAL_UNSEEDED_FIRST_DRAW` in Step 3.

`tests/fixtures/prng_surface.py` is a small contract with one method per row (`draw_bytes`, `draw_full_range`, `draw_range`, `draw_shuffle`, `draw_twice`), each one seeding first from a `Bytes` parameter. `draw_range` is where `u64_in_range` gets its coverage: the RAFFLE no longer uses it (the winner is drawn by `shuffle`, ruling B4), so this fixture plus the `gen_range_one_to_a_hundred_matches_the_sdk_doctest` row are `prng_u64_in_inclusive_range`'s only end-to-end witnesses, and they are sufficient because that arm HAS a pinned sdk vector, which the shuffle arm does not.

**`tests/unit/test_prng_differential.py`, written out rather than described** [M12]. v1 gave it one sentence, and the shuffle row cannot be replayed by the obvious code: `draw_shuffle` answers a `VecObject`, which `_decoded`'s `_DECODABLE` deliberately does not cover (`_VecRank`/`_MapRank`, the same wall B3 hits). The typed decode that exists for exactly this is `ObjectStore.chain_value_as` (`tests/harness/objects.py:413`), and the comparison is over VALUES:

```python
"""`PRNG_VECTORS` at tier 1 and tier 2a. The real leg is `tests/real_host/`.

Tier 1 and tier 2a share `serpent._prng`, so their agreement is a TYPING and
PLUMBING check, not evidence about the chain -- the evidence is
`tests/real_host/test_prng_real.py`, and the `expect` values these two legs
are checked against were measured THERE (M12). What this module does catch:
a Val-encoding mistake in the mini host's bindings, a boxing mistake in the
emitter, and a row that was never given a measured value at all.
"""

from __future__ import annotations

import pytest

import pytest

from serpent import Bytes, U64, Vec
from serpent.env import Env, deploy
from tests.semantics.prng_vectors import PRNG_SURFACE, PRNG_VECTORS, PrngVector
from tests.unit.test_emitter_end_to_end import start


def test_every_vector_carries_a_measured_expectation() -> None:
    """A row whose `expect` is still `None` was never measured against the
    real host. It cannot be filled in from a tier-1 run (M12)."""
    missing = [v.name for v in PRNG_VECTORS if v.expect is None]
    assert not missing, f"unmeasured PRNG vectors: {missing}"


@pytest.mark.parametrize("vector", PRNG_VECTORS, ids=lambda v: v.name)
def test_tier_1_replays_the_vector(vector: PrngVector) -> None:
    env, contract = deploy(PRNG_SURFACE)
    with env.frame():
        answer = getattr(contract, vector.method)(env, Bytes(vector.seed), *_typed(vector))
    assert _plain(answer) == vector.expect


@pytest.mark.parametrize("vector", PRNG_VECTORS, ids=lambda v: v.name)
def test_tier_2a_replays_the_vector(vector: PrngVector) -> None:
    # `start` is the established tier-2a idiom (tests/unit/test_examples.py:188
    # and throughout): it returns the built module, the `FullHost` store, and
    # the `MiniHost`, and it is imported rather than re-written for the same
    # anti-drift reason `_answer` is.
    _built, host, mini = start(PRNG_SURFACE)
    word = mini.invoke(vector.method, host.val_word(Bytes(vector.seed)), *vector.args)
    if vector.method == "draw_shuffle":
        # A VecObject: `_decoded`'s `_DECODABLE` cannot observe one (the
        # `_VecRank` path, test_env_differential.py:295-308), so decode it
        # through the public typed path that exists for exactly this and
        # compare the VALUES, not the shapes (M12).
        drawn = host.chain_value_as(word, Vec[U64])
        assert [item.value for item in drawn] == list(vector.expect)
    else:
        assert _plain(host.chain_value(word)) == vector.expect
```

(`_typed` re-types a row's plain `args` into the chain types the method declares -- `U32` for a length, `U64` for a range bound -- and `_plain` is its inverse for the answer: `bytes` for a `Bytes`, `int` for a `U32`/`U64`, `bool` for a `Bool`. Both are four-line helpers in this module; write them, do not import a test-private one. `Env` is imported for the tier-1 leg's type annotations.)

`tests/real_host/test_prng_real.py` replays them against `RealEnv`, decoding the shuffle the same way, plus:

```python
@real
def test_an_unreseeded_draw_depends_on_the_embedders_base_seed() -> None:
    """Why no tier pins an unseeded draw (E11.4).

    soroban-sdk seeds every test env's base PRNG to all zeros, so an
    unreseeded draw IS reproducible here -- but only as a function of the
    sdk's private frame-derivation order under that seed. Changing the base
    seed changes it, and a RESEEDED draw does not move at all. That contrast
    is the evidence; the numbers themselves are pinned nowhere.
    """
    env = RealEnv()
    c = env.deploy_source(PRNG_SURFACE)
    first = c.invoke("draw_unseeded")
    other = RealEnv()
    other.set_base_prng_seed(bytes([7]) * 32)
    d = other.deploy_source(PRNG_SURFACE)
    assert d.invoke("draw_unseeded") != first
    assert d.invoke("draw_bytes", Bytes(bytes([1]) * 32), U32(32)) == c.invoke(
        "draw_bytes", Bytes(bytes([1]) * 32), U32(32)
    )
```

(If Task 9 reported `set_base_prng_seed` BLOCKED, this test is replaced by one that asserts only the second half -- a reseeded draw is identical across two independent `RealEnv`s -- and the first half is recorded as UNVERIFIED in the attention file.)

- [ ] **Step 3: `ENV_SCENARIOS` rows**

Add `CRYPTO_SURFACE = _ROOT / "tests" / "fixtures" / "crypto_surface.py"` and `PRNG_SURFACE` beside `ENV_SURFACE`, then these rows (each `kind`/`expect` written from the vectors already pinned in Task 1, never measured here):

| name | contract | invoke | kind | pins |
|---|---|---|---|---|
| `keccak256_of_the_empty_bytes` | crypto | `keccak(Bytes(b""))` | value | F1's discriminator, three tiers |
| `keccak256_of_a_multi_block_input` | crypto | `keccak(Bytes(bytes([1]) * 1000))` | value | F2 |
| `sha256_of_the_empty_bytes` | crypto | `sha(Bytes(b""))` | value | the stdlib half, for symmetry |
| `ed25519_verifies_rfc8032_test_2` | crypto | `ed25519(...)` | value (`U32(1)`) | the positive path |
| `ed25519_refuses_a_high_scalar` | crypto | `ed25519(..., s >= l)` | host_error `("Crypto", "InvalidInput")` | F3 |
| `ed25519_refuses_a_small_order_key` | crypto | `ed25519(identity, ...)` | host_error `("Crypto", "InvalidInput")` | F3 |
| `ed25519_refuses_a_sixty_five_byte_signature` | crypto | `ed25519(..., sig + b"\x00")` | host_error `("Object", "UnexpectedSize")` | K10's asymmetry |
| `secp256k1_recovers_the_go_ethereum_vector` | crypto | `recover(...)` | value (`Bytes65`) | the positive path |
| `secp256k1_refuses_a_high_s_signature` | crypto | `recover(..., n - s)` | host_error `("Crypto", "InvalidInput")` | F4 |
| `secp256r1_verifies_the_nist_vector` | crypto | `p256(...)` | value (`U32(1)`) | the positive path |
| `secp256r1_refuses_a_compressed_key` | crypto | `p256(0x02 || x, ...)` | host_error `("Crypto", "InvalidInput")` | K10 |
| `bytes_to_string_round_trips_every_byte` | crypto | `round_trip(Bytes(bytes(range(256))))` | value | E7/F6, the UNVERIFIED `_scval` question |
| `the_network_id_is_thirty_two_bytes` | crypto | `identity()` | value (`Bytes32(bytes(32))`) | E7 |
| `max_live_until_is_sequence_plus_max_entry_ttl_minus_one` | crypto | `max_live_until()` | value | K6/E10 |
| `the_ledger_version_is_the_hosts_protocol` | crypto | `protocol()` | value (`U32(28)`) | K7 |
| `a_contract_can_read_its_own_address` | raffle | `who_am_i()` | value | E5's INVARIANT, see below |
| `a_log_does_not_change_the_answer` | raffle | a method that logs and returns | value | F7, three tiers |
| `prng_seed_refuses_a_thirty_one_byte_seed` | prng | `bad_seed()` | host_error `("Value", "UnexpectedSize")` | E11.3 |
| `prng_u64_in_range_refuses_an_inverted_range` | prng | `bad_range()` | host_error `("Value", "InvalidInput")` | E11.3 |
| `an_unreseeded_draw_is_not_the_chains_draw` | prng | `draw_unseeded()` | value | `host_diverges`, below |

The address row is where E5's invariant lives, and it cannot be a plain equality: tier 1 answers `DEFAULT_CONTRACT_ADDRESS` and the real host answers whatever `register` derived. So it carries a `host_diverges` whose `answer` is left `None` and whose `reason` states the invariant, and `tests/real_host/test_env_scenarios_real.py` gets one extra assertion by name: the real answer equals `contract.address`. Write that assertion explicitly rather than relying on the generic comparison.

The unseeded row is the E11.4 declaration:

```python
    EnvScenario(
        name="an_unreseeded_draw_is_not_the_chains_draw",
        contract=PRNG_SURFACE,
        invoke=Call("draw_unseeded", ()),
        kind="value",
        expect=U64(TIER1_UNSEEDED_FIRST_DRAW),
        host_diverges=HostDivergence(
            reason=(
                "E11.4: before any reseed the host's frame PRNG descends from a base "
                "seed the EMBEDDER sets -- in stellar-core, the txset hash and the "
                "transaction's apply-order position -- which serpent cannot reproduce "
                "and must not pretend to. Tier 1 uses its own documented constant so a "
                "test is deterministic. The sdk's test host happens to seed the base to "
                "all zeros, so the real answer here is stable, but it is a function of "
                "soroban-sdk's PRIVATE frame-derivation order and pinning it would "
                "couple this suite to an sdk internal. Declared rather than discovered; "
                "the RESEEDED stream is pinned exactly, in tests/semantics/prng_vectors.py."
            ),
            events=(),
            answer=U64(REAL_UNSEEDED_FIRST_DRAW),
        ),
    ),
```

`REAL_UNSEEDED_FIRST_DRAW` is measured once, exactly like the shuffle row, and carries a comment saying it is an sdk-internal-dependent number that may move on an sdk bump -- which is itself the point the row is making.

- [ ] **Step 4: `HOST_FACTS` -- the three `_NO_MAXIMUM` rewordings and four new rows (E10, F15)**

`host_facts.py:161-167`. **Re-worded, NOT re-classified** -- all three rows keep `tier1=Unmodelled(...)`:

```python
#: The tier-1 model's one remaining blind spot in this table. M2-A landed the
#: ACCESSOR -- `env.ledger().max_live_until_ledger()` answers
#: `sequence + max_entry_ttl - 1`, the host's own closed form -- but NOT the
#: TTL algebra that uses it: the persistent bucket's clamp, the temporary
#: bucket's trap, and the per-bucket minimum floors are M2-D's row, because
#: they change `_TtlState` and the three bucket classes together and landing
#: half of a TTL model in one sub-plan and half in another is how a model ends
#: up inconsistent. So tier 1 still applies `extend_to` exactly as given at any
#: magnitude, and these three rows say so instead of pretending otherwise.
_NO_MAXIMUM = (
    "no max live-until ALGEBRA at tier 1: M2-A landed the accessor "
    "(`env.ledger().max_live_until_ledger()`), and the clamp, the trap, and the "
    "per-bucket floors are M2-D"
)
```

Note the wording deliberately avoids `SPT1033`, which no longer describes it, and names **M2-D** so the promise net and a future reader both see the owner. The three rows themselves change by ONE character (none): only the shared constant moves. `tests/real_host/test_host_facts_real.py::test_the_unmodelled_rows_still_run_here` must still name exactly those three, and the task review verifies that it does (F15).

Four new `HostFact` rows, each with a method added to `tests/fixtures/host_facts.py`:

1. `keccak256_of_the_empty_input_is_the_keccak_not_the_sha3_digest` -- `real=Value(Bytes32(...c5d24601...))`, `tier1=` the same. F1's claim as EVIDENCE rather than self-agreement.
2. `max_live_until_is_sequence_plus_max_entry_ttl_minus_one` -- `real=Value(U32(7_311_999))`, `tier1=Value(U32(7_311_999))`. C10 measured this on the embedded host 2026-09-11: `sequence 1_000_000`, `max_ttl() == 6_311_999`, so `max_live_until == 7_311_999`. The row is what turns the formula from a reading of `ledger_info.rs` into a measurement.
3. `a_reseeded_draw_is_the_sdks_own_doctest_value` -- `real=Value(U64(8478755077819529274))`, `tier1=` the same. The single strongest row A produces.
4. `a_log_is_not_in_the_contract_event_stream` -- invoke a method that logs and publishes, assert `events()` holds the contract event only. A log is a DIAGNOSTIC event; a contract must not depend on it, and this is the row that says so with evidence.

- [ ] **Step 5: the CI floor, the gates, the commit**

Count the new `real_host` tests and raise `REAL_HOST_FLOOR` in `ci.yml:142` to the newly measured count less a small margin (its comment: "bump it deliberately when a sub-plan adds real-host tests, never lower it"):

```bash
uv run --no-sync pytest -q -m real_host --collect-only -p no:cacheprovider | tail -2
```

Then the four gates, with `SERPENT_REQUIRE_REAL_HOST=1`.

```bash
git add tests/fixtures/crypto_surface.py tests/fixtures/prng_surface.py \
        tests/fixtures/host_facts.py tests/semantics/ tests/unit/test_prng_differential.py \
        tests/unit/test_harness_hostfns.py tests/real_host/ .github/workflows/ci.yml
git commit -m "test(semantics): run every M2-A surface against the real host

Twenty ENV_SCENARIOS rows over two new fixture contracts, four HOST_FACTS
rows, and a dedicated PRNG corpus replayed at all three tiers. The reseeded
stream is pinned value for value; the two PRNG error shapes are host_error
rows; the unseeded draw carries a host_diverges that DECLARES why no tier
pins it.

host_facts.py's _NO_MAXIMUM reason is reworded to say what M2-A landed (the
accessor) and what M2-D still owns (the clamp, the trap, and the per-bucket
floors). The three rows stay Unmodelled: nothing was reclassified."
```

**Report:** the measured shuffle permutation and the measured unseeded draws, quoted from the real-host run that produced them, **with the commit shas showing that run PREDATES the tier-1 run** [M12]; the three Step 0 runner edits, quoted as a diff, plus confirmation that the two existing TTL `host_error` rows still carry `mini_host_gap=TTL_REASON` [B2]; confirmation that `Outcome.answer_type` was NOT relaxed and that the mini host re-typed instead [B3]; `crypto_surface.py`'s measured `declared_protocol` (expected **23**, the first fixture in the tree above 22) and which floor pins were checked against it [m11]; confirmation that the three `_NO_MAXIMUM` rows are still `Unmodelled` and still the only three; whether `bytes(range(256))` survived `_scval` (E7's UNVERIFIED question -- answer it); the new `REAL_HOST_FLOOR`; the gate outputs; the counts.

**The Task 10 REVIEW's own checklist**, over and above the usual: (1) the measured values predate the tier-1 run, established from `git log`, not from the implementer's assertion [M12]; (2) the `host_error` rows actually RAN on both non-real legs -- check for `mini_host_gap` on any A row, which would be a false claim about the rig [B2]; (3) the seven trap rows raise the trap the row's `fact` names, not merely "a trap".

---

### Task 11: the ninth example -- `examples/raffle.py` and its TEN inventory joins [M7]

**Files:**
- Create: `examples/raffle.py`, `tests/goldens/wasm/raffle.wat.txt`, `tests/unit/test_example_raffle.py`, `tests/real_host/test_example_raffle_real.py`, `docs/examples/raffle.md`
- Modify: `tests/unit/test_emitter_end_to_end.py:102-121, 684-694`; `tests/unit/test_emitter_printer.py:382-396`; `tests/unit/test_frontend_fuzz.py` (`CORPUS`); `tests/unit/test_harness_hostfns.py:1006` (`_FIXTURES`); **`tests/unit/test_examples.py:135-159`** -- the prose at `:137-148` AND the enforcing `has_constructor` set at `:154-159` [M7]; `tests/unit/test_docs_site.py:70-81`; `docs/examples/index.md`; `docs/index.md:34`; `mkdocs.yml:71-81`
- Test: `tests/unit/test_examples.py`, `test_docs_site.py`, `test_emitter_end_to_end.py`, and the two new modules

**Interfaces:**
- Consumes: every M2-A surface the example reaches, plus `@contractunion`, `@contracttype` (the `Entrant` key struct, new in v2 [B4]), `@contracterror`, `@contractevent`, `Vec` (built in a `while` loop), the three storage buckets, and two private helper methods.
- Produces: `EXAMPLE_RAFFLE` (the constant every inventory keys on), and a ninth `@contract` class.

- [ ] **Step 1: the contract**

> **[B4] RESHAPED AT PLAN REVIEW, AND THE SHAPE IS RULED.** v1's raffle did not compile, for three reproduced reasons, two of which belong to M2-C:
>
> * `U64(len(entrants))` -- `len()` answers a `U32` and there is no implicit width conversion; **SPT3018**, "`U64()` takes a U64, not U32; there is no implicit conversion between chain types";
> * `U32(index)` where `index` is a `U64` -- **SPT3018** the other way;
> * `env.storage().instance().get(DataKey.Entrants, Vec[Address])` (four sites) -- **SPT3013**, "the type argument must name a chain type directly". No example or fixture in the tree does a container-typed storage read; that is row **C**'s deliverable, named verbatim in the M2 roadmap.
>
> The controller RULED (decisions.md 2026-09-11) that the example is reshaped INSIDE A rather than fed from C. Concretely, and this is the design, not a suggestion:
>
> 1. **entrants are stored indexed under a count** -- `Entrant(index=i) -> Address` in PERSISTENT storage with the count in INSTANCE storage. This is exactly `bounty_board.open_ids`'s pattern (`examples/bounty_board.py:258-267`), and it is better contract design than a growing `Vec` in one instance entry anyway: an entry that grows without bound is a real on-chain footgun.
> 2. **the `Vec[Address]` is BUILT in a `while` loop** where it is needed -- `Vec(Address, [])` then `push_back` -- verified in-subset at `bounty_board.py:260-266`.
> 3. **the winner is drawn by SHUFFLE, not by index**: `env.prng().seed(secret)` then `env.prng().shuffle(field).get(U32(0))`. That removes both width conversions, is a sound commit-reveal draw, and -- the reason it is strictly better evidence -- gives `prng_vec_shuffle` the only end-to-end witness it will ever get. F5's whole complaint is that the shuffle arm has no vector in soroban-sdk's doctests or the host's own tests.
>
> **No `U64(<U32 expr>)`, no `U32(<U64 expr>)`, and no `get(key, Vec[...])` appears anywhere in this example.** Both are M2-C's. `u64_in_range` keeps its coverage through `tests/fixtures/prng_surface.py` and `PRNG_VECTORS` (Task 10 Step 2), which is where the plan already put it; it does not need an example too.
>
> Every construct below was re-checked against `docs/subset.md` and the existing examples, and the whole shape was run through `serpent.compiler.compile_module` with the M2-A-only surfaces stubbed out: **it compiles at `declared_protocol == 22` against HEAD.** The one construct that cannot be probed before Tasks 3-7 land is the chained `env.prng().shuffle(field).get(U32(0))`; the equivalent chain on a `Vec[Address]`-returning private method (`self._field(env).get(U32(0))`) compiles today, and so does the local-binding form (`field = ...` then `field.get(U32(0))`), which is the named fallback if the chain is refused.

`examples/raffle.py`, built on the host authors' OWN documented two-transaction construction (`soroban-env-host-28.0.2/src/host/prng.rs`, the module comment's "tx1: write commitment ... tx2: re-read all committed values, if ledger > N, prng_reseed(S)"). Google-style docstrings, the convention from `examples/guestbook.py` onward.

```python
"""Raffle: a commit-reveal draw, built the way the host's own PRNG docs say.

The hazard a naive on-chain raffle has is that the caller can see the result
inside the same transaction and abort if it dislikes it, retrying until the
draw goes its way. `soroban-env-host`'s `prng` module documents the fix, and
this contract is that construction, verbatim:

    tx1: commit. Draw a secret `S = env.prng().bytes_new(32)`, store
         `sha256(S)` as a public commitment plus `N = env.ledger().sequence()`,
         and keep `S` in temporary storage.
    tx2: reveal. Refuse unless `env.ledger().sequence() > N`, re-read `S`,
         check `sha256(S)` still equals the commitment, `env.prng().seed(S)`,
         shuffle the entrant field, and take its head as the winner.

Because the commitment is written in one ledger and consumed in a later one,
the caller of tx1 cannot know whether aborting helps it, and by tx2 the seed
is locked in.

**How the entrants are stored, and why.** One PERSISTENT entry per entrant
under `Entrant(index=n)`, with the count in INSTANCE storage -- the shape
`examples/bounty_board.py` uses. A single growing `Vec` in one entry makes
every read and every write pay for the whole list, which is a real on-chain
cost, and the field is only materialised (in a `while` loop, `_field`) when a
draw or a display actually needs it.

**Why the winner is drawn by SHUFFLE.** `env.prng().shuffle(field).get(U32(0))`
rather than an index into the field. After the reseed the permutation is a
pure function of the 32 committed bytes, so the draw is fair, reproducible,
and tamper-evident -- and the whole ordering is available if a later version
wants runners-up. Drawing by index would need to narrow the PRNG's `u64` to a
`U32`, which the subset does not do.

**The PRNG contract this relies on.** After `env.prng().seed(s)` every draw is
a pure function of the 32 seed bytes, identically at tier 1, under the dev
mini host, and on chain -- the host replaces the frame PRNG on a reseed rather
than mixing into it. Draws made BEFORE a reseed are not reproducible off
chain, which is exactly why `commit` stores `S` and `reveal` reseeds from it.

Build: `stellar serpent build examples/raffle.py`.
"""

from serpent import (
    U32,
    Address,
    Annotated,
    Bytes,
    Bytes32,
    ContractUnion,
    Env,
    Event,
    Vec,
    contract,
    contracterror,
    contractevent,
    contracttype,
    contractunion,
    errorcode,
    topic,
    variant,
)


@contractunion
class DataKey(ContractUnion):
    """The instance-storage keys: one entry each, all of them small."""

    Admin = variant()
    Count = variant()
    Commitment = variant()
    CommitLedger = variant()
    Secret = variant()
    Winner = variant()


@contracttype
class Entrant:
    """The persistent-storage key for the `index`-th entrant.

    Entrants are stored ONE PER ENTRY under a count rather than as a single
    growing `Vec`, the shape `examples/bounty_board.py` uses for its bounty
    records. A list that grows without bound inside one instance entry is a
    real on-chain footgun -- every read and every write pays for the whole
    list -- and indexing under a count costs one extra instance entry to
    avoid it.
    """

    index: U32


@contracterror
class Error:
    NotAdmin = errorcode(1)  # Only the admin may commit or reveal.
    AlreadyCommitted = errorcode(2)  # The draw is already committed.
    NotCommitted = errorcode(3)  # Nothing has been committed yet.
    TooSoon = errorcode(4)  # The reveal must happen in a later ledger.
    SecretLost = errorcode(5)  # The temporary secret expired before the reveal.
    Tampered = errorcode(6)  # The revealed secret does not match the commitment.
    NoEntrants = errorcode(7)  # Nobody entered.
    AlreadyDrawn = errorcode(8)  # A winner has already been drawn.


@contractevent(topics=("entered",))
class Entered(Event):
    who: Annotated[Address, topic]
    at_ledger: U32


@contractevent(topics=("drawn",))
class Drawn(Event):
    winner: Annotated[Address, topic]
    entrants: U32


@contract
class Raffle:
    def __init__(self, env: Env, admin: Address) -> None:
        """Open an empty raffle owned by `admin`.

        Args:
            admin: The address allowed to commit and reveal.
        """
        admin.require_auth()
        env.storage().instance().set(DataKey.Admin, admin)
        env.storage().instance().set(DataKey.Count, U32(0))

    def enter(self, env: Env, who: Address) -> U32:
        """Add `who` to the entrant list.

        Entrants are numbered from 1 and stored one per PERSISTENT entry under
        `Entrant(index=n)`, with the count in INSTANCE storage.

        Args:
            who: The entrant, who must authorize the call.

        Returns:
            The number of entrants after the call.

        Raises:
            Error.AlreadyCommitted: If the draw is already committed.
        """
        who.require_auth()
        if env.storage().instance().has(DataKey.Commitment):
            raise Error.AlreadyCommitted
        count = env.storage().instance().get(DataKey.Count, U32) + U32(1)
        env.storage().persistent().set(Entrant(index=count), who)
        env.storage().instance().set(DataKey.Count, count)
        Entered(who=who, at_ledger=env.ledger().sequence()).publish(env)
        return count

    def commit(self, env: Env) -> Bytes32:
        """Draw the secret and publish its hash. Admin only. (tx1.)

        Returns:
            The commitment: `sha256` of the secret nobody can see yet.

        Raises:
            Error.NotAdmin: If the caller is not the admin.
            Error.AlreadyCommitted: If `commit` already ran.
            Error.NoEntrants: If nobody has entered.
        """
        self._require_admin(env)
        if env.storage().instance().has(DataKey.Commitment):
            raise Error.AlreadyCommitted
        if env.storage().instance().get(DataKey.Count, U32) == U32(0):
            raise Error.NoEntrants
        secret = env.prng().bytes_new(U32(32))
        commitment = env.crypto().sha256(secret)
        env.storage().instance().set(DataKey.Commitment, commitment)
        env.storage().instance().set(DataKey.CommitLedger, env.ledger().sequence())
        env.storage().temporary().set(DataKey.Secret, secret)
        env.logs().add("raffle committed at ledger", env.ledger().sequence())
        return commitment

    def reveal(self, env: Env) -> Address:
        """Reseed from the committed secret and draw the winner. (tx2.)

        The draw is a SHUFFLE, not an index: after `env.prng().seed(secret)`
        the permutation `env.prng().shuffle(field)` produces is a pure function
        of the 32 committed bytes, so `field.get(U32(0))` after the shuffle is
        a fair, reproducible, tamper-evident winner. Drawing by index would
        need a `U64 -> U32` narrowing, which the subset does not have and
        which is M2-C's row -- and the shuffle is the better construction
        anyway, because the whole permutation is available if a future version
        wants runners-up.

        Returns:
            The winning entrant.

        Raises:
            Error.NotAdmin: If the caller is not the admin.
            Error.NotCommitted: If `commit` has not run.
            Error.AlreadyDrawn: If a winner was already drawn.
            Error.TooSoon: If this is still the ledger `commit` ran in.
            Error.SecretLost: If the temporary secret expired.
            Error.Tampered: If the secret no longer hashes to the commitment.
        """
        self._require_admin(env)
        if not env.storage().instance().has(DataKey.Commitment):
            raise Error.NotCommitted
        if env.storage().instance().has(DataKey.Winner):
            raise Error.AlreadyDrawn
        committed_at = env.storage().instance().get(DataKey.CommitLedger, U32)
        if env.ledger().sequence() <= committed_at:
            raise Error.TooSoon
        if not env.storage().temporary().has(DataKey.Secret):
            raise Error.SecretLost
        secret = env.storage().temporary().get(DataKey.Secret, Bytes)
        if env.crypto().sha256(secret) != env.storage().instance().get(DataKey.Commitment, Bytes32):
            raise Error.Tampered
        env.prng().seed(secret)
        count = env.storage().instance().get(DataKey.Count, U32)
        winner = env.prng().shuffle(self._field(env)).get(U32(0))
        env.storage().instance().set(DataKey.Winner, winner)
        env.logs().add("raffle winner drawn", winner)
        Drawn(winner=winner, entrants=count).publish(env)
        return winner

    def entrants(self, env: Env) -> Vec[Address]:
        """Every entrant, in the order they entered.

        Built in a loop out of the indexed persistent entries. This is the
        `Vec` the draw shuffles, and it is materialised only when something
        asks for it -- reading one entrant costs one entry, not the whole
        field.
        """
        return self._field(env)

    def who_am_i(self, env: Env) -> Address:
        """This contract's own address, as the host reports it."""
        return env.current_contract_address()

    def network(self, env: Env) -> Bytes32:
        """The id of the network this contract is running on."""
        return env.ledger().network_id()

    def entry_horizon(self, env: Env) -> U32:
        """The highest ledger any entry of this contract could live to."""
        return env.ledger().max_live_until_ledger()

    def _field(self, env: Env) -> Vec[Address]:
        """The entrant list, built in a loop out of the indexed entries.

        `bounty_board.open_ids`'s shape (`examples/bounty_board.py:258-267`):
        `Vec(Address, [])` then `push_back` under a `while`. Private, so the
        two callers compile to an internal call rather than a re-entry through
        the export ABI (E8) -- an export calling an export is SPT1037.
        """
        field = Vec(Address, [])
        count = env.storage().instance().get(DataKey.Count, U32)
        i = U32(1)
        while i <= count:
            field.push_back(env.storage().persistent().get(Entrant(index=i), Address))
            i = i + U32(1)
        return field

    def _require_admin(self, env: Env) -> None:
        admin = env.storage().instance().get(DataKey.Admin, Address)
        admin.require_auth()
```

`_require_admin` and `_field` are private methods, which E8's internal-call machinery supports -- verified against HEAD: a public method calling `self._field(env)` compiles, and calling a PUBLIC method that way is SPT1037 ("`self.field(...)` calls an exported method ... move the shared step into a module-level helper or a private `_`-prefixed method"). Do not promote either to public.

**This contract must COMPILE before anything else in this task runs, and a failure is BLOCKED, not a licence to simplify** [B4].

```bash
uv run --no-sync python -c "
from pathlib import Path
from serpent.compiler import compile_module
p = Path('examples/raffle.py')
m = compile_module(p.read_text(), str(p))
print('declared_protocol =', m.declared_protocol)
"
```

Expected: a clean compile at `declared_protocol == 22` (the raffle reaches no gated host function: `verify_sig_ecdsa_secp256r1` is 21, and the two protocol-23 conversions are not used here).

**BLOCKED conditions, by code, quoted so the answer cannot be "make the example simpler":**

* **SPT3018** on any `U64(...)`/`U32(...)` around an expression of the other width -- a width conversion. **STOP.** There is no implicit conversion in the subset and narrowing is M2-C's row. If the reshaped contract above somehow still needs one, report the exact source line: the design is wrong and it is a controller decision, not an implementer edit.
* **SPT3013** on a `get(key, <container type>)` -- a typed container read. **STOP.** Also M2-C's row. The contract above has no such read by construction; if one appears, something was re-introduced from v1.
* **SPT1037** on `self.<method>(...)` -- an export calling an export. Fix by making the callee private (`_field`, `_require_admin` already are); this one IS an implementer fix.
* **Anything else** -- report the code, the message, and the source line, and return BLOCKED. Every rejection is a real finding about the M2-A surface. Do NOT route around it by simplifying the example until it passes; an example trimmed to fit the compiler stops being evidence that the surface works.

One construct could not be probed before this task's dependencies landed: the chained `env.prng().shuffle(self._field(env)).get(U32(0))`. If the chain is refused for a reason that is about the CHAIN and not about `shuffle` itself, the fallback is the local-binding form, which is verified in-subset:

```python
        field = env.prng().shuffle(self._field(env))
        winner = field.get(U32(0))
```

Take the fallback only for a chaining diagnostic, say so in the report, and file the chaining limitation as a finding.

- [ ] **Step 2: the TEN inventory joins (C26, F10) [M7]**

1. `tests/unit/test_emitter_end_to_end.py`: `EXAMPLE_RAFFLE = EXAMPLES_DIR / "raffle.py"` and a ninth entry in `EXAMPLES`; add it to `CONSTRUCTOR_BEARING` (it has an `__init__`), so `test_every_fixture_instantiates_and_declares_the_protocol_floor` expects `declared_protocol == 22`.
2. `tests/unit/test_emitter_printer.py`: `("examples/raffle.py", "raffle")` in `FIXTURE_SOURCES`.
3. `tests/goldens/wasm/raffle.wat.txt`: `SERPENT_REGEN_GOLDENS=1 uv run --no-sync pytest -q tests/unit/test_emitter_printer.py`, then READ the diff -- this is the first golden in the repo containing `log_from_linear_memory`, the crypto calls, and the prng calls, and it is the disassembly evidence for all three lowerings.
4. `tests/unit/test_harness_hostfns.py`: the path in `_FIXTURES`.
5. `tests/unit/test_frontend_fuzz.py`: `"examples/raffle.py"` in `CORPUS` (glob-driven; confirm it appears without an edit and say so).
6. `tests/real_host/test_example_raffle_real.py`: must contain the literal `EXAMPLE_RAFFLE` (the census test greps for it).
7. `docs/examples/raffle.md` with `--8<-- "examples/raffle.py"`.
8. `docs/examples/index.md`: a table row, **plus** "Eight contracts" -> "Nine contracts" **plus** the protocol-22 sentence gains `raffle`. **Nothing tests these three** (measured: no test reads `docs/examples/index.md`), so they are the likeliest omission in the whole task.
9. `mkdocs.yml`: `      - Raffle: examples/raffle.md` under `  - Examples:`. The nav test's regex is `examples/([a-z_]+)\.md` and the assertion is a SET EQUALITY, so a missing entry and a stray one both fail.
10. **`tests/unit/test_examples.py:154-159`** [M7] -- the tenth join, and the only one v1 missed. The prose block at `:137-148` is documentation; the ENFORCING code is

    ```python
        assert has_constructor == (
            path.stem in {"errors", "allowance_token", "bounty_board", "guestbook"}
        ), (
    ```

    The raffle has an `__init__`, so `test_every_example_compiles[raffle]` fails until `"raffle"` joins that set. The next line asserts `declared_protocol == 22`, which is right for the raffle: it reaches no gated host function.

Also: `docs/index.md:34` says "The eight examples"; `tests/unit/test_examples.py:142-144`'s docstring names the four constructor-bearing examples. Both are prose nothing tests. Update both.

`tests/unit/test_docs_site.py:80` asserts two example pages build; add `raffle`.

**Write nothing in `examples/raffle.py` or `docs/examples/raffle.md` containing the strings `sub-plan F`, `sub-plan G`, `tier 2b`, `F's`, or `G's`**: `tests/unit/test_no_stale_promises.py`'s third and fourth nets walk `examples/` and `docs/` with `rglob("*")` and would fail immediately.

- [ ] **Step 3: the two test modules**

`tests/unit/test_example_raffle.py` (tier 1), in `test_example_guestbook.py`'s shape -- `load_example(EXAMPLE_RAFFLE)`, deterministic `_address(label)` contract strkeys, `deploy`, `with env.frame():`. At minimum: a full happy path (`enter` x3, `commit`, `env.advance(1)`, `reveal`), `TooSoon` before the advance, `NotAdmin` from an outsider, `AlreadyCommitted` on a second commit, `NoEntrants`, `AlreadyDrawn`, `SecretLost` after enough advances to lapse the temporary entry, `Tampered` (write a different secret into temporary storage behind the contract's back, then reveal), an `entrants()` order check (the entries come back in entry order, 1..count), `who_am_i()` equal to `DEFAULT_CONTRACT_ADDRESS`, and `env.recorded_logs` holding the two messages. **Plus the two the reshape makes worth pinning** [B4]: `reveal` twice from two independent tier-1 `Env`s with the same entrants and the same committed secret draws the SAME winner (the shuffle is a pure function of the seed), and the drawn winner is a member of `entrants()`.

`tests/real_host/test_example_raffle_real.py` (the real host), in `test_example_guestbook_real.py`'s shape, with a PER-TEST `@real` mark and never a module-level `pytestmark`. At minimum: the happy path; `who_am_i()` equal to `contract.address` (E5's INVARIANT, which is the whole reason the tier-1 answer is not pinned there); the two `Drawn`/`Entered` events; and -- the row that matters -- **the same reveal run twice from two independent `RealEnv`s with the same entrants and the same committed secret produces the same winner**, which is the reseeded-determinism property at the example level.

- [ ] **Step 4: the census, the byte freeze, the gates, the commit**

```bash
uv run --no-sync pytest -q tests/unit/test_examples.py tests/unit/test_docs_site.py
uv run --no-sync mkdocs build --strict
```
Quote both. Then the four byte-freeze node ids (a new example touches no lowering `shapes`/`bounty_board` reach, so `4 passed`), then the four gates.

```bash
git add examples/raffle.py tests/goldens/wasm/raffle.wat.txt docs/ mkdocs.yml tests/unit/ tests/real_host/
git commit -m "feat(examples): promote the commit-reveal raffle to the ninth example

The construction the host's own prng module documents: commit a secret and
its sha256 in one ledger, reveal and reseed from it in a later one, so a
caller cannot retry the draw. It reaches every M2-A surface a raffle
genuinely needs -- sha256, the four prng functions, env.logs().add, the
three ledger accessors, and current_contract_address -- and joins all ten
hand-kept inventories.

Entrants are stored indexed under a count and the field is built in a loop,
so no typed container read and no width conversion enters M2-A: both are
M2-C's. The winner is drawn by shuffling the field and taking its head,
which also gives prng_vec_shuffle its only end-to-end witness.

The four signature functions a raffle has no use for are covered by
tests/fixtures/crypto_surface.py instead: an example that verifies a
signature nobody produced teaches nothing."
```

**Report:** whether the contract compiled unchanged and every construct that had to move; the census and docs-site outputs; the WAT golden diff summarised (which host calls appear); the byte-freeze output; the gate outputs; the counts.

---

### Task 12: docs, the authoring guide, and the promise net

**Files:**
- Modify: `docs/` (the authoring guide or `docs/getting-started.md`, whichever holds the per-surface sections), `docs/api.md` (mkdocstrings entries for the new public names), `tests/unit/test_no_stale_promises.py`, `docs/subset.md` (final regen)
- Test: `tests/unit/test_no_stale_promises.py`, `test_subset_docs.py`, `test_docs_site.py`

- [ ] **Step 1: the authoring sections**

Four new sections, each one written from the code it describes and each one carrying the fact a reader would otherwise get wrong:

- **Crypto.** The five functions, their argument lengths, and the two that RETURN NOTHING and trap. The hazmat warning on `secp256k1_recover` (the digest must come from a secure hash). An explicit "bls12-381, bn254, and poseidon are not in the subset" -- phrased as a statement of scope, not a promise, so the stale-promise nets stay quiet.
- **Randomness.** The commit-reveal pattern, with `examples/raffle.py` as the worked example. The sentence that matters: *after `env.prng().seed(s)` the stream is identical at every tier; before a reseed it is not the chain's, and no test should depend on it.* And `u64_in_range` is INCLUSIVE at both ends.
- **Logging.** `env.logs().add("message", *values)`, the string-literal rule and why (interned at build time; SPT1040), the cost (one pool entry per distinct message, `8 * n` bytes of scratch per call site, one host call), and the two facts that surprise people: a log NEVER fails, and with diagnostics off the host does not read it at all -- so a log is not an audit trail.
- **Ledger and identity.** The three new accessors and `env.current_contract_address()`; `max_live_until_ledger()` is the network's ceiling, and the TTL algebra around it is not modelled at tier 1 yet.

`docs/api.md` gains mkdocstrings entries for `Bytes65` and for `serpent.env`'s `Crypto`, `Prng`, `Logs`, `CryptoTrap`, `PrngTrap`.

- [ ] **Step 2: the fifth promise net**

Follow the G net's shape exactly (`tests/unit/test_no_stale_promises.py:508-626`), appended after `test_the_m1_end_deployment_forward_references_are_gone`, with its own banner comment recording which needles were chosen and which were deliberately omitted:

```python
# --- the M2-A promise sweep (row A's own forward references) -----------------
#
# COPIES THE FOURTH NET'S SHAPE (this is the FIFTH net), with A's letter.
# [m13] -- v1's comment opened "The fourth net's shape", which reads as a
# claim that this IS the fourth net while the prose beside it and the module
# docstring both say five. TWO needles and no more:
#
# * `sub-plan a` (case-insensitive) -- the spelling every prior net uses.
# * `M2-A` -- and this one IS new. The earlier nets had no milestone needle
#   because "M1-F" always appeared beside "sub-plan F"; M2's sub-plans are
#   cited as "M2-A"/"M2-B" far more often than as "sub-plan A", so a net
#   without it would have almost no reach.
#
# Deliberately NOT a needle: `\bA's\b`. Unlike `F's`/`G's`, a capital-A
# possessive is an ordinary English word ("A's" as in the letter grade, and
# more to the point `A's` appears inside quoted prose), and the measured
# false-positive rate made the net useless. Recorded here so the omission is a
# decision rather than an oversight.
#
# Also deliberately excluded: `docs/subset.md`. It is GENERATED byte for byte
# from codes.py by `serpent.compiler._render_docs` and pinned by
# tests/unit/test_subset_docs.py, and SPT1040's `owning_task` ("M2-A Task 4")
# renders into it -- so the needle fires on a line this net cannot ask anyone
# to change. Its INPUT is already covered, because the walk reaches codes.py
# directly. Allowlisting it instead would be worse than excluding it: the
# allowlist is keyed on (path, exact line text), so a later registry reword
# would move the generated line and break the allowlist ENTRY rather than the
# promise it was recording.

_NEEDLE_SUBPLAN_A = re.compile(r"sub-plan a", re.IGNORECASE)
_NEEDLE_M2A = re.compile(r"\bM2-A\b")

#: Generated files the A net does not walk. See the note above.
_A_NET_EXCLUDED = (Path("docs") / "subset.md",)


def _is_an_a_mention(line: str) -> bool:
    return bool(_NEEDLE_SUBPLAN_A.search(line) or _NEEDLE_M2A.search(line))
```

plus `_ALLOWLIST_A`, `_mentions_a`/`_all_mentions_a`/`_keyed_a` (the G net's bodies verbatim, reusing `_WALKED_F` and `_EXCLUDED_DOCS_PREFIX`, and skipping `_A_NET_EXCLUDED` [M13]), and the three tests: the allowlist test, the moved-line test, and a teeth test whose `forward_looking` string is `"the TTL clamp is not modelled; M2-A lands the accessor and M2-D the algebra"` (must TRIP) and whose `repointed` string is `"proven on the real host: tests/real_host/test_prng_real.py's reseeded row"` (must NOT).

Then run it and TRIAGE every hit. A mention in `src/` or `tests/` that reads as a live promise must be implemented, repointed at M2-B/M2-C/M2-D, or removed. A genuine historical record joins `_ALLOWLIST_A` with a reason comment. Expect hits in `host_facts.py` (Task 10's reworded `_NO_MAXIMUM` names M2-D, which is a REPOINT, not a promise -- allowlist it with that reason), in `serpent/env.py`'s `Prng` docstring (a note FOR M2-B, likewise), and in `codes.py`'s new SPT1040 row (`"M2-A Task 4"` as its owning task -- a provenance field, allowlist it). **Expect MORE than the three v1 anticipated** [M13]: every new `tests/semantics/` file this sub-plan adds cites M2-A or M2-D somewhere, and Task 10's runner edits carry `# M2-A [B1]`-style provenance comments. Triage each; do not pre-write the allowlist from this paragraph.

`docs/subset.md` must NOT appear in the hit list at all. If it does, the exclusion above was not wired in -- fix the walk, do not allowlist the line [M13].

Update the module docstring, which enumerates the nets ("FOUR nets. The first two ... The third ... The fourth ...") -> five.

- [ ] **Step 3: the final subset regen, the site, the gates, the commit**

```bash
uv run --no-sync python -m serpent.compiler._render_docs
uv run --no-sync mkdocs build --strict
```
Both must be no-ops for `docs/subset.md` if Task 4 left it correct; a diff here is a finding (something changed the registry or a fixture after Task 4).

The four gates, then commit:

```bash
git add docs/ tests/unit/test_no_stale_promises.py
git commit -m "docs: document the crypto, randomness, logging, and ledger surfaces

Four authoring sections written from the code, each carrying the fact a
reader would otherwise get wrong: the two verifies trap rather than
returning a bool, a reseeded PRNG stream is the chain's and an unseeded one
is not, a log can never fail and is not an audit trail, and
max_live_until_ledger is an accessor with no TTL algebra behind it yet.

A fifth stale-promise net covers this sub-plan's own forward references,
with M2-A as a needle because M2's rows are cited by milestone far more
often than by the sub-plan spelling."
```

**Report:** every hit the fifth net found and how each was triaged; whether the subset regen was a no-op; the `mkdocs build --strict` output; the gate outputs.

---

### Task 13: close

**Files:**
- Create: `.superpowers/sdd/2026-09-11-m2a-env-reach/final-review-attention.md`
- Modify: `.superpowers/sdd/2026-09-11-m2a-env-reach/progress.md` (the ledger)
- Test: everything

- [ ] **Step 1: the full gate sweep, quoted**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check src tests examples
uv run --no-sync mypy --strict
SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q
cd host && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test && cd ..
uv run --no-sync mkdocs build --strict
SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q \
  "tests/real_host/test_testnet_fixtures.py::test_this_trees_build_equals_the_deployed_bytes[shapes]" \
  "tests/real_host/test_testnet_fixtures.py::test_this_trees_build_equals_the_deployed_bytes[bounty_board]" \
  "tests/real_host/test_testnet_fixtures.py::test_the_fixtures_were_recorded_against_the_deployed_bytes[shapes]" \
  "tests/real_host/test_testnet_fixtures.py::test_the_fixtures_were_recorded_against_the_deployed_bytes[bounty_board]"
```

Every one quoted in the report and in the ledger. The suite must be at or above the baseline `4792 passed / 7 skipped`, and the skipped count must still be `7`: a new skip is a finding.

- [ ] **Step 2: the attention file**

`final-review-attention.md`, in the M1-C/D/E/E2/F/G shape, with these sections:

1. **What M2-A landed**, surface by surface, with the evidence for each (which tier proved it).
2. **The risk register**, every F-item from the dossier with its outcome: F1-F5 (the oracle), F6 (the round trip), F7-F8 (logging), F9 (the byte freeze), F10 (the inventories), F11 (the completeness literal), F12 (the SPT1033 fixture), F13 (`codes.py`), F14 (signing), F15 (the scope boundary into M2-D), F16 (the boxing branch). Each says "held", "held with a caveat, here it is", or "open, and here is who owns it".
3. **Decisions the rulings did not settle.** After the plan review every one of these was either RATIFIED by the reviewer and the controller or RULED (see "Decisions this plan made", below), so this section records the outcome rather than asking again -- plus any decision the EXECUTION added, which is the only open list.
4. **UNVERIFIED claims carried out**, each with what would settle it: whether a `Crypto` error is observable as a recoverable value across a `try_call` boundary (M2-B's first probe, E3); the shadow-budget cost of a log at chain-realistic scale (F8); the shuffle arm beyond the one measured row (F5); **the F3 relocation** -- `edwards_decompress`'s non-canonical-`y` behaviour cannot be exercised end to end through `ed25519_verify` (the key's RAW bytes feed the challenge hash), so it has no real-host row and is pinned at the primitive only; **the corrected performance numbers** [m4] -- P-256 verify and secp256k1 recovery measure ~12 ms each under CPython 3.11, not the "~6 ms" v1 quoted nor the top of ruling E1's "0.3-6.5 ms" band; the strategy stands, the number is corrected here, in `_crypto.py`'s docstring, and in decisions.md.
5. **Obligations carried OUT of M2-A**, with an owner: the TTL clamp/trap/floors and the three `Unmodelled` rows (M2-D); tier-1 frame rollback, into which A's frame-local PRNG **save/restore stack** must be folded -- a stack discipline, not a clobber [M3] (M2-B); the differential runner's `host_error` arms, which now run for the first time and which a fourth trap family will extend [B1] [B2] (M2-B/M2-D); the two existing TTL `host_error` rows, still carrying `mini_host_gap` although `_wasm` could now run them (M2-D); `env.py`'s package promotion, now ~2,150 lines (M2-E); the ZK crypto families (M3).
6. **The signing state** (F14, R8), restated in full so it survives the sub-plan boundary: `commit.gpgsign` is locally false; `.git/hooks/post-commit` logged every sha in this run; **the single re-sign base is still `11de4bd`** (M1-G was never re-signed either), the command is
   `git rebase --rebase-merges --exec 'git commit --amend --no-edit -S' 11de4bd`,
   and **the `v0.1.0` tag must be re-pointed onto the re-signed tip afterwards**. The implementer does NOT hand-edit `.git/unsigned-commits.log`; it verifies the hook wrote every sha and reports any gap.

- [ ] **Step 3: the ledger and the commit**

Append the close to `progress.md`: every task's `complete` line, every ruling made during execution, the carried-obligations list, and the measured suite counts.

```bash
git add .superpowers/sdd/  # git-ignored; the add is a no-op and that is expected
git commit --allow-empty -m "chore(m2a): close the env-reach sub-plan

All thirteen tasks complete; four gates, the Rust gate, mkdocs --strict, and
the deployed-bytes equality tests green. Carried obligations, the UNVERIFIED
claims, and the re-sign base are recorded in the attention file."
```

(The SDD directory is git-ignored, so this commit is deliberately empty; if the repo policy refuses an empty commit, skip it and report that the close is recorded in the ledger only.)

**Report:** every gate output quoted; the final counts against the `4792 / 7` baseline; the carried-obligations list; the re-sign base and the `v0.1.0` note; any BLOCKED condition.

---

## Coverage

Walked against the dossier's SS D (the proposed architecture and its task
decomposition), SS E1-E13 (the open questions), SS F1-F16 (the risks), SS A.4's
O-items, and the decisions.md entry "2026-09-11 M2-A rulings". Every row names
the task that discharges it. **No row is unmapped.**

| Dossier / ruling ID | Where it lands |
|---|---|
| **D-1** one shared primitives home, under the zero-dep walk | T1 (both modules + the zero-dep gate run unchanged) |
| **D-2** the tier-1 `Env`'s four new facts | T2 Steps 3-4 |
| **D-3** three `_require_frame`-gated facade classes | T3 Steps 2-4 |
| **D-4** recognition rows, not new IR | T4 Steps 3-5 |
| **D-5** two lowerings, one new branch | T6 (the branch), T7 (the log lowering); T5 proves the other thirteen need neither |
| **D-6** the mini host gains the same functions by delegation | T8 Step 2 |
| **D-7** evidence at the real host | T10 (Step 0 plus all five original steps) |
| **[B1] [B2] [B3]** the differential runner can carry A's rows at all | **T10 Step 0** (the tier-1 trap tuple, `_wasm`'s first `host_error` arm, `_DECODABLE` gaining `Bytes`) + T8 Step 2 (the mini host re-types to `Bytes32`/`Bytes65`) |
| **[B4]** the ninth example compiles, without borrowing from M2-C | **T11 Step 1** (the ruled reshape: indexed entrant storage, a loop-built `Vec`, a shuffle draw; probed against HEAD at `declared_protocol == 22`) |
| **A needs a slice of D's "testing ergonomics"** (the review's roadmap note) | T10 Step 0 -- stated in A's row rather than discovered mid-task |
| **D** 11-task decomposition and seating | Re-cut to 13; the mapping and the one seating refinement are in **Model seating** |
| **E1** pure-Python crypto, shared, no extra, no stub | T1 |
| **E2** exact post-reseed PRNG; documented unseeded constant; frame reset | T1 (the model), T3 Step 4 (`UNSEEDED_PRNG_SEED`, the reset), T10 Step 2 (what is pinned) |
| **E3** Rust SDK names; `Bytes32` hashes; verifies return `None`; `CryptoTrap`/`PrngTrap` | T3 Steps 2-3; the names appear in T4's `RECOGNIZED` rows |
| **E4** a real `Bytes65`; `__all__` at 41; the `bytes_n` docstring | T2 Steps 1-2 |
| **E5** four `DEFAULT_*` in `env.py`; `_real` imports three; the address INVARIANT | T2 Step 4; T10 Step 3 (the invariant row); T11 Step 3 (the real-host assertion) |
| **E6** `env.logs().add(msg, *vals)`; literal message; interned; never traps | T3 (the facade), T4 Step 4 (SPT1040), T7 (the lowering), T8 (the mini host) |
| **E7** `network_id() -> Bytes32`; `surrogateescape` both ways | T2 Steps 1-2; T4 Step 5 (the rows); T8 (the mini host); T10 Step 3 (`round_trip` over `bytes(range(256))`) |
| **E8** no protocol gate fires; a regression test; SPT6001's reason | T5 Step 2 tests 5-6; T4 Step 2 (the reason) |
| **E9** `env.py` is NOT promoted | Global Constraints (a scope fence); T13 Step 2 carries it to M2-E |
| **E10** the accessor only; `_NO_MAXIMUM` names M2-D | T2 Step 4 (the accessor and its docstring); T10 Step 4 (the reword, not a reclassification) |
| **E11** what the differential pins and refuses | T10 Steps 2-3 |
| **E12** the two reductions, SPT1040, SPT1041's condition, SPT1033/SPT1034 wording | T4 Step 2 (both reductions recorded, SPT1041 NOT added with the measurement that settles it) |
| **E13** `examples/raffle.py`; the four unused functions via a fixture contract | T11 (RESHAPED at plan review, [B4]: entrants indexed under a count, the `Vec[Address]` built in a `while` loop, the winner drawn by `env.prng().shuffle(field).get(U32(0))` -- no width conversion and no typed container read, both of which are M2-C's; `u64_in_range`'s coverage moves entirely to `tests/fixtures/prng_surface.py` + `PRNG_VECTORS`); T10 Step 1 (`crypto_surface.py`) |
| **F1** a keccak that passes its own vectors | T1 Step 3 (the empty input first, plus `b"abc"` from outside Stellar); T10 Step 4 (a real-host row) |
| **F2** a rate/capacity error visible only on multi-block input | T1 Step 3 (two million-byte vectors + the 135/136/137 boundary) |
| **F3** an ed25519 that rejects what the host accepts | T1 Step 3 (three negatives, with the non-canonical-`y` one relocated to the decompression -- see below); T10 Step 3 (two real-host rows) |
| **F4** an ECDSA that accepts a high `s` | T1 Step 3 (one per curve); T10 Step 3 |
| **F5** a PRNG whose stream is wrong | T1 Steps 1-2 (the three sdk vectors, the geometry, both sampler arms); T10 Step 2 (the three-tier corpus, incl. the ONLY shuffle evidence that will ever exist); **T11** -- after the [B4] reshape the EXAMPLE draws its winner by shuffling, so `prng_vec_shuffle` gains an end-to-end witness v1 gave it nowhere |
| **F6** `bytes_to_string` losing bytes | T2 Step 1; T10 Step 3 |
| **F7** a log that traps where the host does not | T3 Step 1 test 16; T8 Step 3 test 1; T10 Step 3 (`a_log_does_not_change_the_answer`) |
| **F8** a log that exhausts scratch | T7 Step 1 tests 2, 3, 8, 9 |
| **F9** a byte-freeze violation | Global Constraints (the four node ids); an explicit step in T4, T5, T6, T7, T11, T13 |
| **F10** an inventory omission in the ninth example | T11 Step 2 (all **ten**, with the three untested ones called out; join 10 is `test_examples.py:154-159`'s `has_constructor` set, which v1 missed [M7]) |
| **F11** a completeness test that silently loosens | T4 Step 1 (the intended inventory enumerated BEFORE the table is touched; the review verifies the enumeration) |
| **F12** SPT1033's fixture would start passing | T4 Step 6 (repointed at `env.deployer()`) |
| **F13** an implementer touching `codes.py` | Global Constraints + T4 Step 2's closing sentence |
| **F14** the unsigned-commit log and the re-sign base | Global Constraints; T13 Step 2 section 6 (`11de4bd`, the rebase command, the `v0.1.0` re-point) |
| **F15** scope drift into M2-D | Global Constraints (the fence); T10 Step 4 (reworded, not reclassified); the T10 review verifies it |
| **F16** the raw u64 return silently mis-narrowed | T5 Step 1 (the ABI table finds it), T6 (the branch, with the FULL-range draw), T8 (`last_prng_draw`), T10 Step 2 (`full_range_u64` at all three tiers) |
| **O1** `env.logs()` | T3, T4, T7, T8 |
| **O2** the TTL accessor only | T2 Step 4; the clamp/trap/floors fenced to M2-D |
| **O3** the package promotion | Not done (E9); carried to M2-E in T13 |
| **O4** crypto + PRNG; ZK out | T1, T3, T4, T8 |
| **O5** `env.current_contract_address()` | T2 Step 4, T4 Step 4 |
| **O6/O7/O15** cross-contract, rollback, deploy-from-frame | M2-B; T13 records the PRNG-reset note for the rollback design |
| **O8** U256/I256, time algebra, typed reads | M2-C |
| **O9/O10** TTL floors, ordering, account authorizers, the host-fact candidates | M2-D |
| **O11/O12** `match`, `Option`, `.value`, the event-convention codes | M2-E |
| **O13** Google docstrings in mkdocs, the authoring guide | M2-E, except A's own surfaces: T12 Step 1 |
| **O14/O16** wheels, sdk stable, live tier 3, the ZK families | M3 |
| **C2/C5** the completeness tests move | T4 Steps 1, 7 |
| **C11** `_LINEAR_MEMORY_HOST_FNS` and `needs_memory` | T7 Step 2 (and test 6/7 as the proof) |
| **C13** SPT6001's reason | T4 Step 2 |
| **C16** the `__all__` pin | T2 Step 2 (and nowhere else) |
| **C22/C23** the two corpora | T10 Steps 3-4 |
| **C24** the promise nets | T12 Step 2 (a fifth net, with the `\bA's\b` omission recorded AND `docs/subset.md` excluded because it is generated [M13]) |
| **C25** `docs/subset.md` | T4 Step 7, re-checked in T12 Step 3 |
| **C26** the example inventories | T11 Step 2 (ten, not nine [M7]) |
| **C27** the deployed-bytes pins | Global Constraints; the node ids are named verbatim |
| **Ruling: deployed-bytes tests on every emitter task** | An explicit step in T4, T5, T6, T7, T11, T13 |
| **Ruling: `codes.py` in ONE enumerated task** | T4 only, with a BLOCKED instruction everywhere else |
| **Ruling: the plan review critiques the M2 decomposition too** | Recorded below, with the two decomposition defects this plan found |

### Decisions this plan made that the rulings did not settle

**v2: all eight were put to the adversarial review and then to the controller. Seven are RATIFIED and one (number 6) was challenged and RULED. Nothing here is still open.**

1. **`String` is widened to carry arbitrary chain bytes through `surrogateescape`** (T2 Step 2). Ruling E7 adopts a `surrogateescape` round trip, but `String.__init__` currently REFUSES text that is not strictly UTF-8 encodable -- so the adopted behaviour is not implementable without this change. **RATIFIED** (review + controller): the model was NARROWER than the host on a value constructor, which is a false reject; the one existing pin that could have broken (`test_symbol_string_bytes.py:72`, `String("\ud800")`) survives, because a lone HIGH surrogate is exactly what `surrogateescape` still cannot encode (measured), and `stellar_sdk.scval.to_string` is typed `(data: str | bytes)` so `_scval` needs no fallback. Carries one addition: `buffers.py:37`'s docstring is corrected too [m1]. **Reversal cost: low but public.**
2. **The conversion surfaces are `s.to_bytes()` and `b.to_string()`** (T4 Step 3), value methods in the container family. **RATIFIED**: they sit where `b.slice(lo, hi)` already lives, read in the host function's own direction, and soroban-sdk reaches these through value conversions rather than an `env.` chain. One cost, now folded in: `to_bytes` collides with `int.to_bytes` in `_CONTAINER_METHOD_NAMES`, which gets a sentence and a fixture [m15]. **Reversal cost: medium (a documented authoring name).**
3. **SPT1041 is not added** (Global Constraints, T4 Step 2), because an untyped log value already draws SPT3008. **RATIFIED**, with the evidence promoted into the plan: the SPT3008 comes through `expr._coerce_literal`'s generic `expected is None` arm (`expr.py:772-782`), not a publish-specific path, so it behaves identically in a log value slot. **Reversal cost: zero now, HIGH once published.**
4. **The SPT1034 widening names only the discarded-result shape** (T4 Step 2). **RATIFIED**: `prng_vec_shuffle` is functional and never rebinds its receiver, so the ruling's "receiver is not an owned local" half genuinely has no instance, and `mutation_temporary_receiver.py`'s `serpent:message` still substring-matches. **Reversal cost: zero (wording).**
5. **The crypto argument slots are typed `Ty.Bytes`, with a targeted fixed-length check** rather than `Ty.BytesN(n)` (T4 Step 4). **RATIFIED**: `_assignable(value, declared)` takes the value type FIRST and its only Bytes clause is `declared BYTES / value BYTES_N` (`recognize.py:1723-1748`), so a `BytesN(64)` slot would refuse a signature read out of storage as `Bytes` -- which the host accepts. **Reversal cost: zero.**
6. **Task 11 is Sonnet implementer + Opus review** rather than Sonnet on both sides (Model seating). **CHALLENGED -> RULED.** The review moved to overturn (Opus on both sides), on the ground that the raffle as written did not compile and the fix was a contract redesign -- a semantics call, not a mechanical task. The controller took the reviewer's own cheaper amendment instead: **rule the raffle's construction into the plan, THEN leave Sonnet on it** (T11 Step 1's `[B4]` block is that ruling). The seating stands because its premise has been restored, not because the challenge was wrong. **Reversal cost: zero.**
7. **The fifth promise net uses `M2-A` as a needle and deliberately omits `\bA's\b`** (T12 Step 2). **RATIFIED, with one carve-out added**: `docs/subset.md` is excluded from the walk, because it is regenerated byte for byte and its inputs are already covered by walking `codes.py` [M13]. **Reversal cost: zero.**
8. **The shuffle vector's and the unseeded draws' expected values are MEASURED once on the real host and then pinned** (T10 Step 2). **RATIFIED as a procedure; the GUARD was ruled stronger** [M12]: the quoted real-host `-s` output lives in a comment beside each literal, and the Task 10 review MUST confirm from `git log` that it predates the tier-1 run. The `all(v.expect is not None)` assertion alone only prevents forgetting, not fitting. **Reversal cost: zero.**

### The four rulings the plan review produced (decisions.md, 2026-09-11 "M2-A plan-review rulings")

- **[B4] The ninth example is RESHAPED inside A, not fed from C.** `examples/raffle.py` stores entrants indexed under a count (the `bounty_board.open_ids` pattern), builds the `Vec[Address]` in a loop, and draws the winner with `env.prng().seed(secret)` then `env.prng().shuffle(field).get(U32(0))`. No `U64 <-> U32` narrowing and no typed container read enters A; both stay C's. `u64_in_range` keeps its coverage through `tests/fixtures/prng_surface.py` and `PRNG_VECTORS`. Task 11 Step 1's compile check is an explicit BLOCKED condition quoting SPT3018 and SPT3013, never "simplify until green". Seating stays Sonnet + Opus. **Lands in: T11 Step 1, T11 Step 3, T10 Step 2, Coverage rows E13/F5/F10.**
- **[B1-B3] The differential runner joins the plan.** `tests/unit/test_env_differential.py` is in Task 10's Files with line ranges; the tier-1 `host_error` arm accepts the tuple `(StorageTrap, CryptoTrap, PrngTrap)` (NOT a new shared base class); `_wasm` gains a `host_error` arm (`pytest.raises(HostTrap)`, `trapped=True`); the two existing TTL rows KEEP their `mini_host_gap`; `_DECODABLE` gains `Bytes`; Task 8's mini-host bindings re-type to `Bytes32`/`Bytes65` so `answer_type` compares equal. **`answer_type` is NOT relaxed.** **Lands in: T10 Step 0, T8 Step 2, T3 Step 2.**
- **[M3] The frame PRNG is a SAVE/RESTORE, not a reset-on-entry clobber.** The outer frame's generator is saved before `_invocation`'s `try` and restored in `finally`, beside `_frame.leave(token)`. A nested-frame test pins it, and the note to M2-B says "a stack discipline", not "a clobber". **Lands in: T3 Step 3 (the `Prng` docstring), T3 Step 4 (the code and test 14b), T13 Step 2 section 5.**
- **[M14] No hardcoded attribution trailer.** The plan seats three different models across 13 tasks; the implementer appends its OWN harness attribution trailer, which names the model that authored the commit. **Lands in: Global Constraints; every `git commit -m` block already omitted the trailer for brevity and still does.**

### The decomposition defects (R7 asks the review to look)

**Two this plan found:**

1. **The roadmap lists `get_max_live_until_ledger` at tier 1 in BOTH row A and row D.** E10 already settled it (A lands the accessor, D lands the algebra); this plan makes the split a Global Constraint and a per-task fence, and T10 Step 4 is the wording that records it where a future reader will meet it.
2. **Row A's "SPT1033 retired for the names A lands" is wrong as written.** SPT1033 is not retired -- three names remain -- and E12 already corrected it. Recorded here so the roadmap text is amended rather than re-litigated.

**A third the plan review found, and it is the one that bit [B4]:** row A's headline example could not be built from row A's own surfaces. An entrant list read back is a typed container read (row C, verbatim) and picking a winner by the `u64` the PRNG hands you needs a `U64 -> U32` narrowing (row C's numeric reach). Three ways out existed -- reshape the example, move typed container reads into A, or move the ninth example into C -- and the controller took the first, which costs nothing from C and produces strictly better evidence. **Two smaller mis-assignments, also adopted:** row A needs a slice of D's "testing ergonomics" (the `_DECODABLE`/`answer_type` work, because A is the first sub-plan whose headline answers are byte strings), and row B's frame-rollback design inherits A's frame-PRNG discipline, which is now a stack rather than a clobber. Both are named in A's roadmap row rather than discovered mid-task.

**One process note the review added, adopted:** the sub-plan dossier template gains a "does the example compile today, minus the new surfaces?" probe. Three of B4's four diagnostics were reachable with a five-line contract against HEAD, before a word of this plan was written.

### Self-review notes

- **Placeholder scan.** Run: `grep -niE 'TBD|FIXME|as (above|below) but|similar to Task|and so on|<placeholder>|\bTODO\b'` over this file returns nothing. `XXX` appears only inside the band names `SPT1xxx`/`SPT3xxx`/`SPT6xxx`/`SPT8xxx`; every `...` is either a real type annotation (`tuple[int, ...]`), an idiomatic elision inside quoted prose (`env.crypto().sha256(...)`), or a strkey prefix (`C...`, `G...`). There is no elided code anywhere: code is REPEATED where a later task needs it rather than cross-referenced, and every "see Task N" points at a decision, never at a body of work that is missing here. One earlier draft of T2 Step 3's `_deployed()` helper did elide a fixture's constructor arguments; it was rewritten to deploy `examples/counter.py`, which has no constructor at all.
- **Type consistency.** The names below are spelled identically at every occurrence in this plan: `CryptoTrap`, `PrngTrap`, `HostTrap` (the HARNESS's, `tests/harness/errors.py:35` -- there is no `serpent.env.HostTrap` and v2 deliberately does not add one [B1]), `StorageTrap` (`env.py:642`), `_DECODABLE`, `Outcome.trapped`, `chain_value_as`, `Bytes65`, `DEFAULT_NETWORK_ID`, `DEFAULT_MAX_ENTRY_TTL`, `DEFAULT_PROTOCOL_VERSION`, `DEFAULT_CONTRACT_ADDRESS`, `UNSEEDED_PRNG_SEED`, `Env(contract_address=..., network_id=..., protocol_version=..., max_entry_ttl=...)`, `env.logs().add`, `env.prng().u64_in_range`, `env.prng().bytes_new`, `env.prng().seed`, `env.prng().shuffle`, `env.ledger().version/network_id/max_live_until_ledger`, `env.current_contract_address()`, `_prng.from_prng_seed`, `_prng.u64_in_inclusive_range`, `_prng.shuffle`, `_crypto.sha256/keccak256/ed25519_verify/secp256k1_recover/secp256r1_verify`, `_crypto.edwards_decompress`, `_crypto.edwards_equal`, `_crypto.compress` [M10], `UNSEEDED_PRNG_SEED`, and -- the raffle's own names, all new in v2 [B4] -- `Entrant`, `DataKey.Count`, `enter`, `commit`, `reveal`, `entrants`, `who_am_i`, `network`, `entry_horizon`, `_field`, `_require_admin`. **One deliberate divergence from the rulings' letter**: the ruling writes `Ty.bytes_n(n)`; the real constructor is `Ty.BytesN(n)` (a PascalCase `@staticmethod`), and this plan uses the real one, flagged in T4's Interfaces.
- **Executed sanity checks** (run from the scratch directory against this file's OWN code blocks, extracted verbatim, 2026-09-11, `.venv/bin/python` 3.11.7):
  - `_prng.py`: the unbias key for `[1] * 32` is `fa2778247d5a04abbb47831d29dbca9a9ba8034c429a09fc7fefb00bd5693c50`; **all three soroban-sdk doctest vectors reproduce** (`8478755077819529274`; the 32 bytes `[58, 248, ...]`; `46`); the `next_u64` buffer-boundary split, the degenerate range, and the inverted-range refusal all behave; a 10-element shuffle is a permutation.
  - `_crypto.py`: **all four host keccak vectors** (including the empty input and the 1,000,000-byte input) plus `b"abc"` and a million `a`s from outside Stellar; sha256's empty vector; **RFC 8032 TEST 1 and TEST 2 and the 100,000-byte message**; the wrong-message, `s == l`, `s == l + 1`, small-order-key, 31-byte-key, and 65-byte-signature refusals; the non-canonical-`y` equality for all 19 reachable `y` values; **the go-ethereum secp256k1 recovery vector byte for byte**, plus the high-`s`, `recovery_id > 3`, and wrong-length refusals; **the NIST P-256 vector**, plus the modified-digest, modified-key, high-`s`, compressed-key, and wrong-length refusals.
  - Both modules pass `mypy --strict` and `ruff check --line-length 100` with no findings, and `ruff format --diff` is clean. **v1's sanity check was run on a NARROWER set than gate 2** [M5]: `ruff format --check` also walks `tests/`, and two fenced lines were over 100 columns (`_crypto.edwards_decompress`'s non-canonical assertion in the test file, and `_leaf_args`'s SPT3018 `_error` call). Both are wrapped in v2, and the fence-wide scan below now covers every ```python block in the file, not just the two module bodies.
- **v2 sanity checks** (2026-09-11, the AMENDED fences extracted verbatim by line range into the scratch tree, `.venv/bin/python` 3.11.7):
  - **`_crypto.py`, the ed25519 path.** RFC 8032 TEST 1 (empty message) and TEST 2 (one byte) both verify `True`; the wrong-message negative is `False`. [M10]'s new negative passes for all ten non-small-order non-canonical `y` values (3, 4, 5, 6, 9, 10, 14, 15, 16, 18): each pair decompresses to the SAME point (`edwards_equal` True, so a point comparison cannot tell them apart), `compress` answers the canonical spelling, and the non-canonical spelling is therefore refused by the byte comparison. [M9]'s dropped branch is confirmed dropped: `y = 1 | sign` and `y = p - 1 | sign` now both decompress to `Some`, matching dalek, where v1 returned `None`.
  - **`_prng.u64_in_inclusive_range`** [M11]. Refused with `ValueError`: `(0, 2**64)`, `(0, 2**70)`, `(2**64, 2**64 + 5)`, `(-1, 10)`, `(-100, -50)`, `(1, 0)`. Accepted: `(5, 5) -> 5`, `(0, 0) -> 0`, `(0, 2**64 - 1) -> 8478755077819529274`. The guard does NOT disturb the full-range arm -- all three soroban-sdk doctest vectors still reproduce exactly (`8478755077819529274`, `46`, and the 32 bytes beginning `58, 248, 248, 38, 210, 150`).
  - **The reshaped raffle** [B4]. The contract in T11 Step 1, extracted verbatim with only the M2-A-only surfaces stubbed out (crypto, prng, logs, `current_contract_address`, the three ledger readers), compiles through `serpent.compiler.compile_module` against HEAD at **`declared_protocol == 22`**. That covers the storage shape (`Entrant(index=i)` as a `@contracttype` persistent key with the count in instance storage), the loop-built `Vec(Address, [])` with `push_back`, the public `entrants()` returning `Vec[Address]`, the chained `.get(U32(0))` on a `Vec[Address]`-valued expression, both private helpers, and every event, error, and union. Separately confirmed: making `_field` public turns the call into **SPT1037** ("`self.field(...)` calls an exported method"), so both helpers must stay `_`-prefixed. The one construct that cannot be probed before Tasks 3-7 land is `env.prng().shuffle(...)` itself; T11 Step 1 names the local-binding fallback and it is verified in-subset.
- **One dossier claim corrected by reading the code.** F3 says the non-canonical-`y` behaviour is checked by "a tier-1 unit test ... plus a real-host differential row". It cannot be, in that shape: the public key's RAW BYTES feed the `SHA512(R || A || M)` challenge, so re-encoding a key non-canonically changes `k` and the signature stops verifying for an unrelated reason -- a full `ed25519_verify` can never exercise the divergence. T1 Step 3 makes the claim where it lives instead, at `edwards_decompress`, and says so in the test's own docstring. There is correspondingly NO real-host row for it (there is no input that would distinguish the two implementations end to end), which is recorded in T13's UNVERIFIED section rather than papered over.
