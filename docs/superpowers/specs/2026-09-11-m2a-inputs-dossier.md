# M2-A DESIGN-INPUTS DOSSIER -- Env reach: the leaf host surface (`current_contract_address`, three ledger accessors, five non-ZK crypto functions, PRNG, `env.logs()`, String/Bytes conversion, the ninth example)

Compiled 2026-09-11 for the sub-plan M2-A plan author and its adversarial
reviewer. Every claim carries a citation ID; the plan cites IDs, not prose.
Facts marked **verified 2026-09-11** were re-checked live this session (the
pinned Rust host sources on disk, RPC, local toolchain, and executable probes
run against this checkout); everything else is quoted from the frozen input it
cites. Where a claim could not be checked it says **UNVERIFIED** and names what
would settle it.

Citation-ID families:

| Prefix | Source |
|---|---|
| S# | design spec `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md` |
| R# | M2 roadmap `docs/superpowers/plans/2026-09-11-m2-roadmap.md` (row A and the standing constraints) |
| D# | rulings in `docs/superpowers/decisions.md` (dated title) |
| O# | obligations carried INTO A by the C/D/E/E2/F/G attention files and the G dossier §A.6 |
| K# | chain, host-source, and toolchain facts verified 2026-09-11 (this dossier, §B) |
| C# | what the repo contains today (this dossier, §C) |
| E# | open questions for the controller, recommendation first (§E) |
| F# | risks and the checks that answer them (§F) |

There is no U# family. Every prior sub-plan had a set of inputs decided WITH
Elliot in session; A does not. Elliot extended the autonomy grant to M2 and
stepped away (D12), and the roadmap explicitly asks the A plan review to
critique the decomposition itself because he is not here to approve it (R7).
Everything in §E is therefore a controller ruling made under that grant, and
every one of them is recorded with a reversal cost.

How A differs from every M1 sub-plan: A adds no control flow, no new value
kinds, and no new IR node. Almost every function it lands is a LEAF: arguments
in, one host call, a value out. The whole of A's difficulty is concentrated in
two places. First, the tier-1 oracle must now compute things the host computes
with real cryptography and a real stream cipher, in pure Python, with the core
staying zero-dependency (S12, C4). Second, the PRNG is the first host surface
whose answer depends on hidden host STATE rather than on its arguments, so
"what can the differential honestly pin" is a question A has to answer before
it writes a single test. The controlling risk is therefore not "the compiler is
wrong" but **a plausible-looking oracle**: a keccak that passes its own
vectors, an ed25519 that accepts what the host rejects, a PRNG whose
distribution is right and whose stream is wrong. Every one of those is green at
tier 1 and wrong on chain.

---

## A. FROZEN INPUTS

### A.1 Spec obligations (`2026-08-26-serpent-python-soroban-sdk-design.md`)

- **S1** §11 M2: "Cross-contract calls ..., **crypto host functions, PRNG**,
  deployer, TTL helpers, full SEP-41 token example, U256/I256." A takes the
  crypto and PRNG clauses, plus the leaf context and ledger accessors the
  roadmap adds.
- **S2** §5, verbatim: "Adversarial review B2 killed 'no linear memory':
  Symbols > 9 chars ..., string/bytes literals, **logging
  (`log_from_linear_memory` is the only logging path)**, and efficient Vec/Map
  bulk construction all need it." `env.logs()` is named in the spec as a
  linear-memory consumer, not as an afterthought.
- **S3** §5 build-time assertion (M13): "if any linear-memory host function is
  imported, the module must declare exactly one memory and export it under the
  literal name `memory`. The host resolves the memory export *lazily* -- a
  contract missing it deploys fine and fails in production on the first
  string-touching path." A's log lowering joins that assertion's input set
  (C11).
- **S4** §2 authoring model: chain types are real Python classes with operator
  overloading and no mypy plugin; M1 types include `Bytes32`/`Bytes64` "fixed
  length aliases via a `bytes_n(N)` factory -- a bare-int `BytesN[32]`
  subscript is not a valid type under strict mypy, per M1-A adversarial
  review". This is the constraint that decides A's `Bytes65` question (E4,
  K19).
- **S5** §4 "Declared protocol is computed, never hand-set (M9)":
  `env_meta.protocol = max(min_supported_protocol of imports actually
  emitted)` with a floor. A imports two protocol-23 functions and one
  protocol-21 function (K3, K5), so A is the first sub-plan whose ordinary
  surface moves an artifact's declared floor above `BASE_PROTOCOL`.
- **S6** §10 one Val codec, verbatim: "`serpent/val.py` is the only Val
  encode/decode implementation; the type classes, the compiler, the mini-host,
  and the test harnesses all import it." The same principle, applied to A's new
  primitives, is what forces ONE shared crypto/PRNG module rather than one in
  `serpent.env` and another in `tests/harness/` (E1, E2, D6).
- **S7** §8 tier 1: pure unit tests; tier 2a: the wasmtime mini host,
  explicitly LOWER fidelity; tier 2b: the real host, "the release gate"; tier
  3: on-chain, opt-in. A adds legs at 1, 2a, and 2b; it adds nothing at tier 3
  (E11).
- **S8** §8 headline, verbatim: a hand-written mock of host semantics "has
  *silent false green* as its failure mode". For A this is the literal risk:
  a wrong keccak or a wrong ChaCha stream is silent everywhere except against
  the real host.
- **S9** §13 verified facts A leans on: Val is 64-bit with an 8-bit tag; object
  tags Bytes 72, String 73; `SCSYMBOL_LIMIT` = 32; one memory, one table;
  import symbol names <= 10 chars; "Host interface: 199 fns, 11 modules (x10
  i52 m14 v19 l21 d2 b26 c37 a12 t2 p4)" -- confirmed live against the pin
  (K1).
- **S10** §13 cost model: "WasmInsnExec 4/instr; DispatchHostFunction 295;
  VisitObject 60 -- one host call is about 74 instructions of fixed overhead."
  Relevant to E6: a `log!` that costs a host call plus a scratch write per
  value is not free, even though the host swallows its errors (K12).
- **S11** §12 risks A inherits: tier-2 fidelity drift (real host is the gate),
  env.json/protocol churn (pinned by SHA, per-function gates in the generated
  bindings).
- **S12** §3 package layout names `src/serpent/env/` as a PACKAGE containing
  "storage (3 tiers + TTL), events, auth, ledger, **logging**". The spec has
  always put logging inside the env package; today `env.py` is a single
  1858-line module (C8) and the promotion is E's row (R5).

### A.2 Roadmap row A and the standing constraints (`2026-09-11-m2-roadmap.md`)

- **R1** Row A, verbatim: "`env.current_contract_address()`; `env.ledger()`
  gains `version()`, `network_id()`, `max_live_until_ledger()`; `env.crypto()`
  with `sha256`, `keccak256`, `ed25519_verify`, `secp256k1_recover`,
  `secp256r1_verify` (the five non-ZK functions; bls12-381, bn254, and poseidon
  are M3-or-later); `env.prng()` with reseed, ranged u64, bytes, and shuffle;
  `env.logs()` (`log_from_linear_memory`, debug output); `String`/`Bytes`
  conversions (`string_to_bytes`/`bytes_to_string`, the protocol-23 gate);
  tier-1 implementations with the zero-dep core intact (one shared pure-Python
  crypto module used by tier 1 AND the mini host, or a ruled alternative);
  real-host differential legs and HOST_FACTS rows for every new surface;
  SPT1033 retired for the names A lands and sanctioned new codes for their
  misuse shapes; the ninth example (a commit-reveal raffle: hash + prng +
  logs)". Key consumers: B, C, D.
- **R2** Order: "A -> B -> C -> D -> E ... B needs A's `current_contract_address`
  and `sha256` (deployer salts, the token's contract identity) and is the
  headline risk of M2, so it follows A directly."
- **R3** Standing constraints for every sub-plan: single Val codec;
  validate-inside-compiler; error codes never lost to `unreachable`; the
  zero-dep core (`tests/unit/test_core_zero_dep.py`); the frozen, append-only
  SPT registry (new codes by controller sanction only, pins in the same
  commit); the four gates plus the Rust gate and `mkdocs build --strict` on
  every task.
- **R4** The byte freeze, verbatim: "no edit may move an emitted byte of
  `examples/shapes.py` or `examples/bounty_board.py`; the byte-equality tests
  pin HEAD's build of each to its deployed bytes. Any A-E change to a lowering
  those two examples reach (hashing, symbol handling, the ABI prologue, the
  runtime parts) is a controller decision, and the deployed-bytes tests are the
  tripwire. The guestbook is NOT deployed and may move."
- **R5** Row E owns "`env.py` promoted to the `serpent/env/` package (1858 lines
  today; E10's trigger is long tripped)". A is not asked to do it (E9).
- **R6** Row D owns "minimum-TTL floors for all buckets and
  `get_max_live_until_ledger` at tier 1". Row A owns the same host function's
  name in its own list (R1). The overlap is real and needs settling (E10).
- **R7** "The M2-A plan review is asked to critique THIS decomposition as well
  as the plan."
- **R8** Signing for the M2 run: `commit.gpgsign` is locally false, a
  `post-commit` hook logs every unsigned sha, and ONE rebase from `11de4bd`
  re-signs M1-G and all of M2 afterwards.

### A.3 Decision-log rulings that bind A

- **D1** 2026-08-26 standing autonomy, extended to M2 (D12): decide and record;
  hard stops are pushes, publishes, and deployments. A performs no outward
  action. Read-only RPC is not a hard stop (F dossier D1).
- **D2** 2026-08-27 x2, registry discipline: `codes.py` is frozen public API,
  append-only; no renumber, no delete, no meaning reversal; wording widenings
  and new codes ONLY by controller sanction with snapshot pins updated in the
  same commit. Subagents never touch `codes.py`, `decisions.md`, or `spikes/`;
  if a code seems missing the implementer returns BLOCKED.
- **D3** 2026-08-27 "SPT registry: honest-code remap for env API misuse (Task
  7a)": a code's intent text must fit the shape it reports. SPT3018 is a
  genuine TYPE mismatch only; SPT3020 is call ARITY against a known signature;
  SPT1038 is "env API used with an unsupported call shape". A's new misuse
  shapes are triaged against exactly this rule (E12, C6).
- **D4** 2026-08-28 M1-E E4: TTL clamp/trap is NOT modelled at tier 1, because
  "the max is `get_max_live_until_ledger`, an M2 host fact -- a chosen constant
  would be a guess". A is the sub-plan that lands the host function that ends
  that excuse (K6), which is exactly why E10 has to say who spends it.
- **D5** 2026-08-28 M1-E E10, verbatim: "env.py stays a module; the package
  promotion is recorded as M2 cleanup, unless the model pushes it past ~600
  lines mid-E." It is 1858 lines today (C8).
- **D6** 2026-08-28 M1-E plan-review: tier-1 ledger defaults are pinned to the
  harness constants through ONE shared home; F's real-host `LedgerInfo` is fed
  from the same home, "not a third copy". A adds three more ledger-shaped
  constants (network id, max entry TTL, protocol) and must extend that home
  rather than start a fourth copy (C9, E5).
- **D7** 2026-09-02 M1-F E-series: the real host is where a tier-1 claim
  becomes evidence; `ENV_SCENARIOS` is importable so the real leg re-runs it;
  `HOST_FACTS` is the table for "questions only the real host can answer".
- **D8** 2026-09-10 M1-G E10: `DEFAULT_TARGET_PROTOCOL` stays 27 (mainnet's
  protocol) "because the target is an upper bound, and the default build must
  deploy on the lowest live network". Verified still correct 2026-09-11:
  mainnet is on protocol 27, testnet on 28 (K17).
- **D9** 2026-09-10 M1-G plan review, ruling M13: the byte freeze (= R4).
- **D10** 2026-09-11 M1-G execution rulings: `_UNBRIDGED_DEBT` has eight
  entries and is "deleted entry by entry as M2's registry pass lands codes" --
  that pass is E's row, not A's (O13).
- **D11** 2026-09-11 post-M1 rulings: the real host runs Wasm constructors
  under recording auth with non-root allowed; `RealEnv.deploy_module` exists;
  `examples/guestbook.py` is the EIGHTH example, and the per-example test
  pattern is `tests/unit/test_example_<name>.py` plus
  `tests/real_host/test_example_<name>_real.py`, a WAT golden, a docs page, a
  nav entry, an index row, and membership in every inventory. Google-style
  docstrings are the example convention from here.
- **D12** 2026-09-11 "M2 roadmap decomposition and the M2 run's mechanics": the
  decomposition itself, the autonomy extension, and the run mechanics (R8).

### A.4 Obligations carried INTO A, deduplicated, with an owner

Sources: `.superpowers/sdd/2026-09-10-m1g-cli-and-ship/final-review-attention.md`
§5 and §7; `.superpowers/sdd/2026-09-02-m1f-testing-tiers/final-review-attention.md`
§6; `.superpowers/sdd/2026-08-28-m1e-env-runtime/final-review-attention.md`
"Carried obligations OUT of M1-E"; `.superpowers/sdd/2026-08-31-m1e2-unions/final-review-attention.md`
§5; and the G dossier §A.6 M2 list. Deduplicated; each says whether A OWNS it,
which other M2 sub-plan owns it per the roadmap, or that it is out of M2.

| ID | Item | Source | Owner |
|---|---|---|---|
| **O1** | `env.logs()` | E attn "To M2" | **A OWNS** |
| **O2** | TTL helpers | E attn "To M2"; S1 | **A OWNS** the `get_max_live_until_ledger` accessor only; the clamp, the trap, and the per-bucket floors are **D** (R6, E10) |
| **O3** | `env.py` package promotion (E10's trigger long tripped) | E attn, E2 attn §5, G §A.6 | **E** per R5; A's recommendation is to add sibling private modules instead (E9) |
| **O4** | Crypto host functions, PRNG | spec §11, G §5 | **A OWNS** (non-ZK five + the four prng fns); bls12-381, bn254, and poseidon are **M3-or-later** (R1) |
| **O5** | `env.current_contract_address()` (guestbook's `claim_donations` needed it) | G attn §7 | **A OWNS** |
| **O6** | Cross-contract calls, `try_call`, deployer, token client, `upgrade` | G attn §5/§7 | **B** |
| **O7** | Tier-1 frame rollback of storage, events, and auths | F attn §6, G §A.6 | **B** (roadmap moved it there: it is load-bearing for `try_call`) |
| **O8** | U256/I256, checked arithmetic, time algebra, typed container reads | spec §11, E attn, G §A.6 | **C** |
| **O9** | Minimum-TTL floors for all buckets; container ordering (host order recorded in `COMPARE_VECTORS`); account (`G...`) authorizers; key-level footprint; archival modelling | F attn §6, G §A.6 | **D** |
| **O10** | Constructor-auth reconciliation in the differential runner; `RealContractError.member`; typed clients at both tiers; a test-address generator; a `RealEnv` ledger accessor; `_scval` duplicate-key hardening; `_member_for` ambiguity; the host-fact candidates (instance-bucket ttl for absent keys, `map_del` on a missing key, `_innermost_error`) | G attn §5/§7 | **D** |
| **O11** | `match` sugar; `Option` payloads and narrowing; `.value`; the public `discriminant` rename; an explicit-discard idiom for SPT1028 | E2 attn §5, G attn §7 | **E** |
| **O12** | The event-convention registry codes (the eight `_UNBRIDGED_DEBT` raises); `Artifact.unknown_imports`; dunder carve-out reachability; O-HYG9 | G attn §5, D10 | **E** |
| **O13** | Google docstring style in mkdocs (`docstring_style: sphinx` today) and the authoring guide; the `from_` aliasing note; docs completeness | G attn §7, G §A.6 O-DOC2 | **E**, except that A documents its OWN surfaces as it lands them (R3's docs-per-task habit) |
| **O14** | Wheels, sdk 28.0.0 stable, the live tier-3 suite, the PyPI name, the `stellar-plugin` topic, the maturin pin, free-threaded CPython for `unsendable` | G attn §5, D10 | **OUT of M2** (M3) |
| **O15** | A deploy-from-frame path so a constructor's `require_auth` refusal is testable | G attn §7 | **B** (it is a deployer-API shape) |
| **O16** | Non-ZK crypto only: "bls12-381, bn254, and poseidon are M3-or-later" | R1 | **OUT of M2** |

Two O-items deserve emphasis because they are the ones A can accidentally
under-deliver: **O2** (A must not quietly claim the TTL maximum is modelled
when only the accessor exists) and **O4** (the ZK families are out, and the
`env.crypto()` surface A ships must be shaped so adding them later is additive,
not a rename).

---

## B. THE CHAIN, HOST-SOURCE, AND TOOLCHAIN TRUTH (verified 2026-09-11)

All host-source facts below were read from the pinned sources on disk at
`~/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/`:
`soroban-env-host-28.0.2/`, `soroban-env-common-28.0.2/`, and
`soroban-sdk-28.0.0-rc.1/`. Nothing here is quoted from memory.

### B.1 The exact host-function signatures A lands

From `src/serpent/_host/env.json` (the pinned v28.0.2 interface), read with
`jq`, **verified 2026-09-11**.

- **K1** The module census matches S9 exactly: `x context: 10`, `i int: 52`,
  `m map: 14`, `v vec: 19`, `l ledger: 21`, `d call: 2`, `b buf: 26`,
  `c crypto: 37`, `a address: 12`, `t test: 2`, `p prng: 4`.

- **K2** Context module (`x`), the four A lands:

  | export | name | args | return | min_proto |
  |---|---|---|---|---|
  | `x._` | `log_from_linear_memory` | `msg_pos: U32Val, msg_len: U32Val, vals_pos: U32Val, vals_len: U32Val` | `Void` | none |
  | `x.2` | `get_ledger_version` | none | `U32Val` | none |
  | `x.6` | `get_ledger_network_id` | none | `BytesObject` | none |
  | `x.7` | `get_current_contract_address` | none | `AddressObject` | none |
  | `x.8` | `get_max_live_until_ledger` | none | `U32Val` | none |

  env.json docs, verbatim: `get_ledger_network_id` "Return the network id
  (sha256 hash of network passphrase) of the current ledger as `Bytes`. The
  value is always 32 bytes in length."; `get_max_live_until_ledger` "Returns
  the max ledger sequence that an entry can live to (inclusive)."; 
  `log_from_linear_memory` "Emit a diagnostic event containing a message and
  sequence of `Val`s."

- **K3** Crypto module (`c`), the five non-ZK functions:

  | export | name | args | return | min_proto |
  |---|---|---|---|---|
  | `c._` | `compute_hash_sha256` | `x: BytesObject` | `BytesObject` | none |
  | `c.0` | `verify_sig_ed25519` | `k: BytesObject, x: BytesObject, s: BytesObject` | `Void` | none |
  | `c.1` | `compute_hash_keccak256` | `x: BytesObject` | `BytesObject` | none |
  | `c.2` | `recover_key_ecdsa_secp256k1` | `msg_digest: BytesObject, signature: BytesObject, recovery_id: U32Val` | `BytesObject` | none |
  | `c.3` | `verify_sig_ecdsa_secp256r1` | `public_key: BytesObject, msg_digest: BytesObject, signature: BytesObject` | `Void` | **21** |

  Everything else in module `c` (exports `4` through `z`, 32 functions) is
  bls12-381, bn254, or poseidon, gated at min_proto 22, 25, or 26. Out of M2
  (O16).

- **K4** PRNG module (`p`), all four:

  | export | name | args | return |
  |---|---|---|---|
  | `p._` | `prng_reseed` | `seed: BytesObject` | `Void` |
  | `p.0` | `prng_bytes_new` | `length: U32Val` | `BytesObject` |
  | `p.1` | `prng_u64_in_inclusive_range` | `lo: u64, hi: u64` | `u64` |
  | `p.2` | `prng_vec_shuffle` | `vec: VecObject` | `VecObject` |

- **K4a** The pin's own per-position ABI answers for every function A lands,
  read out of `serpent._host.functions_by_name` in this checkout
  (**verified 2026-09-11**; `val_typed_args`/`val_typed_ret` are computed
  properties on `HostFn`, and the model's docstring says consumers "must not
  re-derive that ABI fact"):

  | function | `val_typed_args` | `val_typed_ret` | `wasm_params` |
  |---|---|---|---|
  | `prng_reseed` | `(True,)` | True (`Void`) | `('i64',)` |
  | `prng_bytes_new` | `(True,)` | True | `('i64',)` |
  | **`prng_u64_in_inclusive_range`** | **`(False, False)`** | **False** | `('i64','i64')` |
  | `prng_vec_shuffle` | `(True,)` | True | `('i64',)` |
  | `compute_hash_sha256` / `compute_hash_keccak256` | `(True,)` | True | `('i64',)` |
  | `verify_sig_ed25519` | `(True, True, True)` | True (`Void`) | three `i64` |
  | `recover_key_ecdsa_secp256k1` | `(True, True, True)` | True | three `i64` |
  | `verify_sig_ecdsa_secp256r1` | `(True, True, True)` | True (`Void`) | three `i64` |
  | `log_from_linear_memory` | `(True, True, True, True)` | True (`Void`) | four `i64` |
  | `get_ledger_version` / `get_ledger_network_id` / `get_current_contract_address` / `get_max_live_until_ledger` | `()` | True | `()` |
  | `string_to_bytes` / `bytes_to_string` | `(True,)` | True | `('i64',)` |

  **`prng_u64_in_inclusive_range` is the outlier in both directions**: two raw
  scalar arguments AND a raw scalar return. The argument side is the same
  `val_typed_args=False` shape the storage `StorageType` immediate and the TTL
  thresholds already use (C12), and lowering one as a `Val` is "an argument
  that is wrong by a factor of 2^32 and still validates"
  (`lower.py:_lower_host_call`). The return side has no precedent in any
  `SurfaceKind.HOST_CALL` row (C7a). None of the four prng functions carries a
  min_proto.

- **K5** Buffer module (`b`), the two conversions: `b.n string_to_bytes(str:
  StringObject) -> BytesObject`, **min_proto 23**; `b.o
  bytes_to_string(bytes: BytesObject) -> StringObject`, **min_proto 23**.

### B.2 What the host actually does (read from the Rust)

- **K6** `get_max_live_until_ledger` is `LedgerInfo::max_live_until_ledger_checked`
  (`soroban-env-host-28.0.2/src/ledger_info.rs:25-30`), verbatim:
  `self.sequence_number.checked_add(self.max_entry_ttl.saturating_sub(1))`.
  So **max_live_until = sequence_number + max_entry_ttl - 1**, and an overflow
  is `Error(Context, InternalError)` -- deliberately non-recoverable, because
  "overflowing here means a misconfiguration of the network"
  (`src/host/ledger_info_helper.rs:25-38`). This is a closed-form the tier-1
  model can reproduce exactly, with no guessing (contra D4's premise).

- **K7** `get_ledger_network_id` returns `li.network_id` (32 bytes) as a fresh
  `BytesObject` (`host.rs:1295-1302`). `get_ledger_version` returns
  `self.get_ledger_protocol_version()` as a `U32Val` (`host.rs:1262`). Neither
  can fail except through `with_ledger_info` (no ledger set) or the budget.

- **K8** `get_current_contract_address` is
  `add_host_object(ScAddress::Contract(self.get_current_contract_id_internal()?))`
  (`host.rs:1306-1313`). The only failure is "Current context has no contract
  ID", `Error(Context, InternalError)`, which the host's own comment calls "a
  logic bug on our part" reachable only from a HostFunction frame before a
  contract is running (`frame.rs:661-677`). From inside a contract it cannot
  fail. Tier 1's `_require_frame` is the right analogue (C8).

- **K9** `string_to_bytes` and `bytes_to_string` (`host.rs:2984-2999`) are
  straight payload re-tags: `visit_obj` the source, copy the slice, and
  `add_host_object` the other ScVal case. `bytes_to_string` can fail only on
  the `ScString` length bound via `try_into`; neither validates UTF-8. That
  matters at tier 1: `String` in serpent holds a Python `str` (C7), so a
  round trip through arbitrary bytes is NOT lossless in the tier-1 model the
  way it is on the host (F6, E7).

- **K10** Crypto error shapes, read from `src/crypto/mod.rs` and
  `src/host.rs:3015-3080`, and corroborated by the host's own tests in
  `src/test/crypto.rs`. This table is the single most useful artifact in §B
  for A's tier-1 work, because the error KIND is asymmetric in ways no one
  would guess:

  | call | failure | error |
  |---|---|---|
  | `compute_hash_sha256` / `compute_hash_keccak256` | oversized input | `Error(Budget, ExceededLimit)` |
  | `verify_sig_ed25519` | public key not 32 bytes | `Error(Crypto, InvalidInput)` ("invalid length of ed25519 public key") |
  | `verify_sig_ed25519` | public key 32 bytes but not a point | `Error(Crypto, InvalidInput)` ("invalid ed25519 public key") |
  | `verify_sig_ed25519` | signature not 64 bytes | **`Error(Object, UnexpectedSize)`** -- the host's own test comments "This is a bit inconsistent with key error and returns an object error (and not a crypto error)" |
  | `verify_sig_ed25519` | signature does not verify | `Error(Crypto, InvalidInput)` ("failed ED25519 verification") |
  | `recover_key_ecdsa_secp256k1` | digest not 32 bytes | `Error(Object, UnexpectedSize)` |
  | `recover_key_ecdsa_secp256k1` | signature not 64 bytes | `Error(Crypto, InvalidInput)` ("invalid ECDSA sinature" -- the typo is upstream) |
  | `recover_key_ecdsa_secp256k1` | signature `s` is high | `Error(Crypto, InvalidInput)` ("ECDSA signature 's' part is not normalized to low form") |
  | `recover_key_ecdsa_secp256k1` | `recovery_id > 3` | `Error(Crypto, InvalidInput)` |
  | `recover_key_ecdsa_secp256k1` | recovery fails | `Error(Crypto, InvalidInput)` |
  | `verify_sig_ecdsa_secp256r1` | public key tag is not `0x04` (uncompressed SEC-1) | `Error(Crypto, InvalidInput)` |
  | `verify_sig_ecdsa_secp256r1` | msg digest not 32 bytes | `Error(Object, UnexpectedSize)` |
  | `verify_sig_ecdsa_secp256r1` | signature high-`s`, malformed, or not valid | `Error(Crypto, InvalidInput)` |

  Note the shape: a wrong length on a value the host reads through
  `fixed_length_bytes_from_bytesobj_input` is an **Object/UnexpectedSize**; a
  wrong length or a wrong value on anything the crypto crates parse is a
  **Crypto/InvalidInput**. Both are traps from the guest's point of view --
  none of these is a recoverable contract error.

- **K11** `verify_sig_ed25519` uses ed25519-dalek 2.2.0's **`verify_strict`**
  (`src/crypto/mod.rs:89`), not `verify`. Read from the dalek source:
  `verify_strict` (a) rejects a signature scalar `s` that is not canonical and
  `< l` (`check_scalar` -> `Scalar::from_canonical_bytes`, the non-legacy arm),
  (b) requires `R` to decompress, and (c) **rejects a small-order `R` or a
  small-order public key**. `VerifyingKey::from_bytes` does NOT reject a
  non-canonical `y` encoding (`y >= p`): curve25519-dalek's
  `CompressedEdwardsY::decompress` masks the sign bit and reduces the field
  element, so such an encoding is silently accepted and reduced. A tier-1
  implementation that rejects `y >= p` therefore DIVERGES from the host on
  exactly that class of input (F3).

- **K12** `log_from_linear_memory` (`host.rs:1152-1188`) wraps its entire body
  in `self.with_debug_mode(|| { ... })` and then unconditionally returns
  `Ok(Val::VOID)`. `with_debug_mode` (`host.rs:695-734`) runs the closure ONLY
  when `diagnostic_level == DiagnosticLevel::Debug`, and it runs it under
  `budget.with_shadow_mode`. Consequences, all load-bearing for A:
  1. **A log call can never fail from the guest's point of view.** A bad
     pointer, a bad length, a forged Val: all swallowed.
  2. **With diagnostics off (the chain default for non-diagnostic execution)
     the body does not run at all**, so the message bytes and the vals array
     are never read.
  3. The budget charged is the SHADOW budget, not the real one. A log is not
     free in a diagnostic run and is very nearly free otherwise.
  The vals array is read as 8-byte little-endian `Val` payloads, each passed
  through `self.relative_to_absolute(...)` -- the same treatment
  `vec_new_from_linear_memory` gives its array (`host.rs:2030`), so object
  handles written by the guest are relative handles and translate correctly
  (`host_object.rs:374-421`).

- **K13** The PRNG, read from `src/host/prng.rs`, `src/crypto/mod.rs`,
  `src/host/frame.rs:400-440`, and `src/host.rs:3734-3792`. This is the
  semantic core of A:
  1. The host holds one **base** `Prng`, seeded by the embedder via
     `Host::set_base_prng_seed(seed: [u8; 32])`.
  2. **Every frame** (each contract invocation or sub-invocation) lazily gets
     its own PRNG, derived from the base by `sub_prng` (32 bytes drawn from the
     base, used as a new ChaCha20 seed). `with_current_prng` does this on first
     use and stores it on the frame context. If the base was never seeded the
     call is `Error(Context, InternalError)` ("host base PRNG was not seeded").
  3. `Prng::new_from_seed(seed)` is `ChaCha20Rng::from_seed(unbias_prng_seed(seed))`.
     `unbias_prng_seed` is **HMAC-SHA256 with a fixed, protocol-level salt**,
     verbatim from `src/crypto/mod.rs:456-486`: `const SALT: [u8; 32] =
     hex!("7ac33997544e3175d266bd022439b22cdb16508c01163f26e5cb2a3e1045a979");`
     with the comment "Salt is fixed and must not be changed; it is effectively
     'part of the protocol' and must be the same for all implementations." The
     salt is the Stellar public-network id.
  4. **`prng_reseed(b)` REPLACES the frame PRNG with `Prng::new_from_seed(b)`**
     (`host.rs:3734-3763`). It requires exactly 32 bytes; anything else is
     `Error(Value, UnexpectedSize)` ("Unexpected size of BytesObject in
     prng_reseed"). **Therefore every draw after a reseed is a pure function of
     the seed bytes**, independent of the base PRNG, the frame, or the
     transaction. This is the fact that makes an exactly-reproducing tier-1
     PRNG possible (K14, E2).
  5. `prng_u64_in_inclusive_range(lo, hi)` errors with `Error(Value,
     InvalidInput)` when `lo > hi`, and otherwise is
     `rand::distributions::Uniform::from(lo..=hi).sample(&mut chacha)`.
  6. `prng_vec_shuffle(v)` is `rand::seq::SliceRandom::shuffle` (Fisher-Yates,
     descending index, `swap(i, gen_index(rng, i+1))`) over a metered clone.
  7. `prng_bytes_new(n)` is `chacha20_fill_bytes` into an `n`-byte buffer.

- **K14** The exact sampling algorithms, pinned by `host/Cargo.lock` to
  **rand 0.8.8**, **rand_chacha 0.3.1**, **rand_core 0.6.4**
  (**verified 2026-09-11** by reading those crates' sources on disk):
  - `Uniform::from(RangeInclusive<u64>)` -> `UniformInt::<u64>::new_inclusive`,
    which precomputes `range = hi - lo + 1` (wrapping) and
    `z = (u64::MAX - range + 1) % range`; `sample` loops
    `v = rng.gen::<u64>(); (hi_, lo_) = v.widening_mul(range); if lo_ <= u64::MAX - z { return lo + hi_ }`.
    Lemire's method with exact rejection. When `range == 0` (the full u64
    range) it is a bare `rng.gen::<u64>()`.
  - `shuffle` uses `gen_index(rng, ubound)`, which for `ubound <= u32::MAX`
    is `rng.gen_range(0..ubound as u32)` -> `UniformInt::<u32>::sample_single_inclusive`,
    a DIFFERENT arm with an approximate zone: `zone = (range << range.leading_zeros()).wrapping_sub(1)`,
    drawing one `u32` per attempt. The two arms consume different word widths,
    so a tier-1 model that reuses the u64 sampler for shuffle would produce the
    right distribution and the wrong stream.
  - `ChaCha20Rng` buffers **64 u32 words** (4 blocks, `BUF_BLOCKS = 4`,
    `BLOCK_WORDS = 16`); `from_seed` sets stream 0 and word position 0;
    `BlockRng::next_u64` reads two adjacent words little-endian and has a
    documented split case at the buffer boundary (`rand_core-0.6.4/src/block.rs:197-218`).

- **K15** **EXECUTABLE PROOF that tier 1 can reproduce the host's PRNG stream
  bit for bit (verified 2026-09-11).** A ~110-line pure-Python module
  (HMAC-SHA256 unbias + ChaCha20 + the rand 0.8.8 samplers above, stdlib only)
  was written to the scratch dir and run against the three vectors
  soroban-sdk 28.0.0-rc.1 pins in its own doctests
  (`soroban-sdk-28.0.0-rc.1/src/prng.rs`, each preceded by
  `env.prng().seed(Bytes::from_array(&env, &[1; 32]))`):

  | vector | sdk expects | pure Python produced |
  |---|---|---|
  | `env.prng().gen::<u64>()` (= `prng_u64_in_inclusive_range(0, u64::MAX)`) | `8478755077819529274` | `8478755077819529274` |
  | `env.prng().gen_len::<Bytes>(32)` (= `prng_bytes_new(32)`) | `[58, 248, 248, 38, 210, 150, 170, 117, 122, 110, 9, 101, 244, 57, 221, 102, 164, 48, 43, 104, 222, 229, 242, 29, 25, 148, 88, 204, 130, 148, 2, 66]` | identical |
  | `env.prng().gen_range::<u64>(1..=100)` | `46` | `46` |

  The unbiased key for seed `[1; 32]` is
  `fa2778247d5a04abbb47831d29dbca9a9ba8034c429a09fc7fefb00bd5693c50`.
  Three independent vectors matching on the first attempt is strong evidence
  that the unbias step, the ChaCha20 stream orientation, the buffer geometry,
  and BOTH sampler arms were reproduced correctly. **UNVERIFIED:** no vector
  for `prng_vec_shuffle` exists in the sdk doctests or the host tests, so the
  `gen_index` arm is verified only by source reading. A real-host differential
  row that shuffles a known `Vec` after a known reseed is what would settle it
  (F5, and it is a named test in the plan below).

- **K16** `Env::default()` and `Env::new_with_config(...)` in soroban-sdk
  28.0.0-rc.1 BOTH route through `new_for_testutils`, which calls
  `env_impl.set_base_prng_seed([0; 32])` (`src/env.rs:759`), **verified
  2026-09-11**. serpent's `RealEnv` uses `Env::new_with_config` (C10), so the
  embedded real host's base PRNG is seeded to all zeros and its draws are
  deterministic given the invocation order. `set_base_prng_seed` itself derives
  two sub-PRNGs unconditionally (`recording_auth_nonce_prng`, `test_prng`)
  "whether or not we're in a build that stores / reveals them, so that the
  base_prng is left in the same state regardless of build configuration"
  (`host.rs:548-565`). So even the UNRESEEDED draws are reproducible in
  principle -- but only as a function of frame count and sdk internals, which
  is exactly the coupling A should not pin (E2, F5).

- **K17** Network truth, **verified 2026-09-11** by read-only RPC:
  - Testnet `getVersionInfo`: `protocolVersion 28`, version
    `28.0.1-273f19e4fcb183b568948bd2b810abfe87150a9c`, captive core
    `stellar-core 28.0.1`. `getLatestLedger`: sequence `4627452`, protocol 28.
  - Mainnet (`https://mainnet.sorobanrpc.com`) `getVersionInfo`:
    `protocolVersion 27`, version `27.1.1-7e71281...`, captive core
    `stellar-core 27.1.0`. Horizon reports `current_protocol_version 27`,
    `core_supported_protocol_version 28`.
  - Consequence for A: `DEFAULT_TARGET_PROTOCOL = 27` (D8) still holds, and
    every function A lands is at or below 23, so **no A surface trips the
    protocol gate under the default target** (C13, E8).

- **K18** Local toolchain, **verified 2026-09-11**: `uv 0.12.10 (Homebrew
  2026-09-04 aarch64-apple-darwin)`; `wasm-tools 1.258.0` (equals the ci.yml
  pin); `cargo 1.97.1 (c980f4866 2026-06-30)`; `rustc 1.97.1 (8bab26f4f
  2026-07-14)`; `.venv` Python `3.11.7` with `serpent 0.1.0` and the
  `serpent_host` extension importable. `git log -1` is `137e1e2 docs: decompose
  M2 into sub-plans A-E and record the run mechanics` on a clean `main`.

- **K19** **Executable probe, verified 2026-09-11**: `bytes_n(65)` compiles as
  an ANNOTATION (`def echo(self, env: Env, k: bytes_n(65)) -> bytes_n(65)` ->
  `compile_module` OK) but `uv run mypy --strict` on the same file reports
  `error: Invalid type comment or annotation [valid-type]` with `Suggestion:
  use bytes_n[...] instead of bytes_n(...)`. A module-level
  `Bytes65 = bytes_n(65)` is rejected by the frontend with **SPT3014**
  ("this is an annotation-only form; it cannot appear as a value"). This is
  precisely why `Bytes32`/`Bytes64` are written as real `class` statements
  (`buffers.py:200-203`). secp256k1 recovery returns a **65-byte** SEC-1
  uncompressed key (K10), so A has a public-API decision to make (E4).

- **K20** Vendored test vectors A can use, read from
  `soroban-env-host-28.0.2/src/test/crypto.rs` (**verified 2026-09-11**; the
  hashes and the ed25519 vectors were additionally reproduced locally):
  - sha256: `""` -> `e3b0c442...b7852b855`; `[1]` -> `4bf5122f...7785459a`;
    `b"test vector for soroban"` -> `91a8e0fb...78dc294`; `[1u8; 1_000_000]` ->
    `1fb6a051...08dbb764`.
  - keccak256: `""` -> `c5d24601...5d85a470`; `[1]` -> `5fe7f977...41dcffd2`;
    `b"test vector for soroban"` -> `352fe2ea...ee8853d9`; `[1u8; 1_000_000]`
    -> `eb8c4805...8019aad3`.
  - ed25519: RFC 8032 §7.1 TEST 1 (empty message) and TEST 2 (`0x72`), plus a
    100000-byte `a` message, all with their public keys and signatures.
  - secp256k1 recovery: digest `ce0677bb...ce971008`, signature
    `90f27b8b...ceaddc93`, `recovery_id = 1` -> `04e32df4...0f11ef652`
    (from go-ethereum's `secp256_test.go`).
  - secp256r1: public key `04e424dc...dfaee927`, digest `d1b8ef21...33965a94`,
    signature `bf96b99a...7ec871c` (a NIST CAVP vector), plus five negative
    cases.
  There is also a `chacha_test_vectors` test in
  `soroban-env-host-28.0.2/src/test/prng.rs` exercising raw `ChaCha20Rng`
  against reference output, and `prng_test`, which proves two reseeds with the
  same input produce the same `prng_bytes_new` output.

- **K21** **Executable measurement of pure-Python crypto (verified
  2026-09-11)**, all on this machine's CPython 3.11.7, stdlib only:

  | primitive | approximate implementation size | measured | correctness |
  |---|---|---|---|
  | sha256 | 0 lines (`hashlib.sha256`, stdlib) | native | by construction |
  | HMAC-SHA256 (the PRNG unbias) | 0 lines (`hmac`, stdlib) | native | K15 |
  | keccak-256 (keccak-f[1600], padding `0x01`) | ~40 lines | **0.32 ms/op** on a 30-byte input | reproduces the host's `""` and the `b"abc"` reference vector |
  | ChaCha20 + rand 0.8.8 samplers | ~110 lines | ~0.05 ms per 32-byte draw | K15, three sdk vectors |
  | ed25519 `verify_strict` (extended coordinates) | ~70 lines | **3.2 ms/op** | reproduces RFC 8032 §7.1 TEST 2 |
  | shared short-Weierstrass curve arithmetic (affine, `pow(x, -1, p)`) | ~60 lines | -- | -- |
  | secp256k1 public-key recovery | ~25 lines on top of the shared code | **6.5 ms/op** | reproduces the host's own go-ethereum vector exactly |
  | P-256 ECDSA verify (prehash) | ~20 lines on top of the shared code | **6.1 ms/op** | reproduces the host's NIST vector |

  Total implementation: roughly **325 lines of code**, or 700 to 900 lines with
  serpent's docstring density. One measurement is worth calling out: using
  `pow(x, -1, p)` (extended Euclid, CPython 3.8+) instead of `pow(x, p-2, p)`
  took the two ECDSA operations from **56 ms** to **6.5 ms**, a factor of
  8.6. A tier-1 suite doing a few hundred signature verifications costs a
  couple of seconds; one doing thousands would need the Jacobian-coordinate
  rewrite, which is not warranted (E1).

---

## C. WHAT THE REPO CONTAINS TODAY

### C.1 Recognition: how an Env chain becomes a `HostCall`

- **C1** `src/serpent/compiler/recognize.py` (3369 lines) holds
  `RECOGNIZED: dict[str, HostCallSpec]`, the single source of truth for "which
  host function(s) does this Python surface shape reach". Each row carries
  `surface`, `kind` (`HOST_CALL`, `GET_DEFAULT`, `REJECT`, `MUTATOR`,
  `MAKE_VEC`, `MAKE_MAP`, `MAKE_STRUCT`, `FIELD_GET`), `host_fns`,
  `reject_code`, `missing_value_code`, and `family` (`"env"` or
  `"container"`). `ENV_HOST_FN_TARGETS` and `CONTAINER_HOST_FN_TARGETS` are
  DERIVED from the rows, and `UNREACHED_CONTAINER_HOST_FNS` names, with a
  reason, every container inventory member no row reaches.
- **C2** The completeness tests assert BOTH directions:
  `tests/unit/test_recognize_env.py:298-306` checks every
  `ENV_HOST_FN_TARGETS` name resolves in `_host.functions_by_name`, that
  `recognize.target_functions().keys() == ENV_HOST_FN_TARGETS`, and that
  `ENV_HOST_FN_TARGETS == _DOSSIER_C4_INVENTORY` (a literal frozen set in the
  test file). `tests/unit/test_harness_hostfns.py:945-954` asserts the mini
  host binds exactly the union of the two target sets. **Every one of these
  moves when A adds rows**, and the `_DOSSIER_C4_INVENTORY` literal is the
  one that will fail loudest.
- **C3** The three-way split at an `env.<name>` attribute, from the module
  docstring: recognized-and-lowerable; `KNOWN_FUTURE_ENV_NAMES` ->
  **SPT1033**; anything else -> **SPT2006**.
  `KNOWN_FUTURE_ENV_NAMES = {"logs", "call", "try_call", "crypto", "prng",
  "current_contract_address", "deployer"}`;
  `_LEDGER_FUTURE_METHODS = {"version", "network_id", "max_live_until_ledger"}`.
  **A lands `logs`, `crypto`, `prng`, and `current_contract_address` from the
  first set and ALL THREE of the second.** What remains in
  `KNOWN_FUTURE_ENV_NAMES` after A is `{"call", "try_call", "deployer"}`, all
  B's (O6). `_LEDGER_FUTURE_METHODS` becomes EMPTY, and its dispatch arm in
  `_recognize_ledger_method` (recognize.py:1249-1252) has no reachable input.
- **C4** The zero-dep gate, `tests/unit/test_core_zero_dep.py`: a STATIC `ast`
  walk over every module under `src/serpent/` except `spec/` and `testing/`,
  asserting every imported root is either `serpent` or in
  `sys.stdlib_module_names`. `hashlib`, `hmac`, and `struct` all pass. There
  are five further tests asserting `spec`, `testing`, and `cli` are
  unreachable from the package root, plus an out-of-process probe. A's new
  primitives module must live under the walk, so **no third-party crypto
  library is available at any price**.
- **C5** `RECOGNIZED` today does NOT mention `string_to_bytes` or
  `bytes_to_string`, and neither name appears in
  `UNREACHED_CONTAINER_HOST_FNS`. The container inventory was frozen from the
  M1-C dossier's SS C.4 list, so adding these two means extending the frozen
  inventory literal in `tests/unit/test_containers_frontend.py` as well as the
  table.
- **C6** How misuse is reported today (D3's honest-code discipline in force):
  `_bind`'s three call-shape failures (too many positional arguments, a
  missing required argument, a duplicate keyword) are **SPT3020**; a per-slot
  type disagreement is **SPT3018**; a recognized Env attribute referenced
  without being called or chained, and a structurally malformed recognized
  call that is neither arity nor type (the empty event-topics tuple), are
  **SPT1038**; an unresolved name on `env` or a bucket is **SPT2006**.
  `_recognize_ledger_method` currently reports `SPT2006` with the help string
  "ledger() supports .timestamp() and .sequence() in M1" -- that help text
  moves when A lands three more.

### C.2 Lowering: what the emitter already does and what it does not

- **C7** `src/serpent/emitter/lower.py:_lower_host_call_raw` reads
  `HostFn.val_typed_args` per position, "never re-derived: it is the pin's own
  per-position answer". A plain no-argument host call
  (`get_ledger_sequence`-style) is one `fn.call_import(...)` followed by
  `narrow_to(fn, ctx, e.ty)`. Every A surface except `env.logs()` and
  `prng_u64_in_inclusive_range` fits this existing path exactly, with no new
  machinery.
- **C7a. The raw-RETURN gap, and it is new.** `_lower_host_call` ends with an
  UNCONDITIONAL `narrow_to(fn, ctx, e.ty)`, and `narrow_to` (lower.py:1302-1331)
  `local.tee`s the stack top and runs `abi_check` on it **as a `Val`**. The
  generic path therefore assumes every `HOST_CALL` row returns a `Val`. Today
  that assumption holds: `HostFn.val_typed_ret` is `False` for 19 pinned
  functions, but the only one serpent lowers (`obj_cmp`) has its OWN dedicated
  lowering in `_lower_compare`, which consumes the raw -1/0/1 as a signed
  comparison and never reaches `narrow_to`. **`prng_u64_in_inclusive_range` is
  the first `SurfaceKind.HOST_CALL` row in the table whose return is a raw
  scalar** (K4a). Sending it through the generic path would `abi_check` a bare
  `u64` as if it were a `U64Val`: for most values that is a loud failure, and
  for a value whose low 8 bits happen to match the `U64Small` tag it is a
  silent wrong answer. The fix is small and has a precedent -- the raw word
  must be BOXED first, via the same runtime `fits_small_u`-or-`obj_from_u64`
  helper `arith.py:839-852` already uses for 64-bit results -- but it is a new
  branch in `_lower_host_call`, not a table row, and the plan must name it as
  such (F16).
- **C8** `src/serpent/emitter/layout.py` already owns everything
  `log_from_linear_memory` needs: an INTERNED literal pool at `0x0000` (equal
  bytes stored once) and a compile-time bump allocator for scratch from
  `SCRATCH_BASE = 0x1000`, 8-byte aligned, never reused, guarded by
  `BuildLimitError` against `SCRATCH_BASE` and one 64 KiB `PAGE` (reported as
  SPT8002/SPT8003). `lower.py:_lower_make_struct` is the worked precedent: a
  compile-time key blob via `intern` plus a RUNTIME values array via
  `ctx.memory.scratch(8 * n)` and `_store_val` per slot
  (`i32.const addr; <val>; i64.store align=3`). **A's log lowering is that
  same recipe with `intern(msg_bytes)` in place of the key blob.**
- **C9** `src/serpent/env.py` (1858 lines). `Ledger` declares only
  `timestamp()` and `sequence()`, both gated on `_require_frame`. Its
  docstring records the deliberate asymmetry: the SEQUENCE is read LIVE out of
  the shared `_TtlState` so `env.advance(n)` cannot desynchronise two readers,
  while the TIMESTAMP is an `int` copy. `Env.__slots__` today is
  `("_auths", "_events", "_instance", "_poisoned", "_recorded_auths",
  "_store", "_timestamp", "_ttl")`. **There is no contract identity, no
  network id, no protocol version, and no max entry TTL anywhere in the
  tier-1 model.** `DEFAULT_LEDGER_TIMESTAMP = 1_700_000_000` and
  `DEFAULT_LEDGER_SEQUENCE = 1_000_000` live here and are the shared home D6
  requires.
- **C10** `src/serpent/testing/_real.py`: `RealEnv` constructs the Rust class
  with `protocol_version=DEFAULT_PROTOCOL` (28), `network_id=DEFAULT_NETWORK_ID`
  (`bytes(32)`, all zeros), `base_reserve=DEFAULT_BASE_RESERVE` (5_000_000),
  `min_temp_entry_ttl=16`, `min_persistent_entry_ttl=4096`, and
  `max_entry_ttl=DEFAULT_MAX_ENTRY_TTL` (6_312_000). `host/src/lib.rs` builds
  a `soroban_sdk::Env::new_with_config(EnvTestConfig { capture_snapshot_at_drop:
  false })` and sets a `LedgerInfo` from those. It exposes
  `protocol_version()`, `host_protocol_ceiling()`, `max_ttl()`,
  `diagnostics()`, `compare()`, `set_ledger()`, and `register()`. **It does
  not expose the network id, the ledger sequence, or any prng control.**
  **Measured on the embedded host 2026-09-11**: a default `RealEnv()` reports
  `protocol_version() == 28`, `host_protocol_ceiling() == 28`,
  `max_ttl() == 6311999`, and `sequence == 1000000`, so
  `get_max_live_until_ledger` on that env is `7_311_999`, exactly K6's
  `sequence + max_entry_ttl - 1`. `RealEnv.max_ttl()` is the sdk's
  `Storage::max_ttl()`, which is
  `ledger().max_live_until_ledger().saturating_sub(ledger().sequence())`.
  So A's tier-1 formula is not a guess: it is confirmed against the same host
  the differential runs on.
- **C11** `src/serpent/emitter/module.py` holds `MEMORY_EXPORT_NAME = "memory"`,
  `MEMORY_PAGES = 1`, and the M13 assertion that a module importing any member
  of `frontend._LINEAR_MEMORY_HOST_FNS` must export `memory`. That set is
  today `{symbol_new_from_linear_memory, string_new_from_linear_memory,
  bytes_new_from_linear_memory, <the field-symbol fn>, <the struct-new fn>,
  vec_new_from_linear_memory, map_new_from_linear_memory}` -- an ENUMERATED
  set, deliberately not `"linear_memory" in name`, "so a new linear-memory host
  function cannot be added there" silently. **`log_from_linear_memory` must
  join it explicitly, and `frontend.py`'s `needs_memory` computation must
  count a log statement.**
- **C12** `src/serpent/_host/_protocol.py`: `DEFAULT_TARGET_PROTOCOL = 27`,
  `BASE_PROTOCOL = 20`, `CONSTRUCTOR_MIN_PROTOCOL = 22`.
  `declared_protocol(fn_names, requested)` runs `check_protocol_target`
  against `requested` or else `DEFAULT_TARGET_PROTOCOL`, then returns
  `compute_protocol_floor(fn_names)` when `requested is None`. So a contract
  using `string_to_bytes` declares **23**; one using `verify_sig_ecdsa_secp256r1`
  declares **21**; both are under 27 and compile with no explicit target
  (K17).
- **C13** `codes.py`'s `NO_FIXTURE_REASONS["SPT6001"]` reads, verbatim: "no
  fixture-reachable trigger: **no host function the frontend emits is gated
  above the base protocol** (that arm is wired end-to-end via a
  synthetic-bindings unit test, Task 10), and the one FEATURE gate ... fires
  only against an explicit target_protocol, which is a compile_module keyword a
  must_reject fixture cannot set". **A makes the bolded clause false.** The
  allowlisting still holds (a `must_reject` fixture still cannot set
  `target_protocol`), but the reason text is now wrong and needs a sanctioned
  wording pass in the same commit (E12).

### C.3 The value layer

- **C14** `src/serpent/types/buffers.py`: `String` wraps a Python `str`;
  `Bytes` wraps `bytes` with `_LENGTH: ClassVar[int | None] = None`;
  `Bytes32`/`Bytes64` are real `class` statements subclassing `Bytes` with
  `_LENGTH` 32 and 64; `bytes_n(n)` is a cached factory that returns those two
  by identity and synthesises others on demand. Indexing returns `U32`
  (mirroring `bytes_get`); `slice(lo, hi)` traps like the host while `[a:b]` is
  serpent-side sugar that clamps. `Bytes.to_val()`/`from_val()` raise
  `NotImplementedError("host object form; sub-plan B")` -- that is M1's sub-plan
  B, long superseded; the strings are stale but harmless.
- **C15** `src/serpent/types/address.py`: `Address` is built on the internal
  pure-Python `serpent._strkey` codec (`encode(version_byte, payload)`,
  `decode(expected_version, s)`, `VERSION_CONTRACT = 16`,
  `VERSION_ACCOUNT = 48`), so **a tier-1 `current_contract_address()` can
  synthesise a genuine `C...` strkey from 32 bytes with no dependency**.
- **C16** `serpent.__all__` has 40 names today (**verified 2026-09-11**), and
  `tests/unit/test_public_api.py` pins the list. Any new public name A exports
  moves that pin.

### C.4 The harnesses

- **C17** `tests/harness/hostfns.py` (601 lines), `FullHost`: models `obj_cmp`,
  the vec/map/bytes families, the four `*_new_from_linear_memory`
  constructors, `contract_event`, `require_auth`, `require_auth_for_args`,
  `get_ledger_timestamp`, `get_ledger_sequence`,
  `extend_current_contract_instance_and_code_ttl`, the scalar object bridges,
  `strkey_to_address`, and the wide-integer table. `get_ledger_sequence` is
  literally `return val.pack_u32val(self.ledger_sequence)` off an instance
  attribute defaulting to `serpent.env.DEFAULT_LEDGER_SEQUENCE`. **There is no
  crypto, no prng, no contract identity, and no TTL state.**
- **C18** There is no generic "unbound host function" trap. `tests/harness/engine.py`'s
  `MiniHost.__init__` builds the wasmtime `Linker` strictly from the bindings
  dict plus `fail_with_error`, so a module importing an unbound host function
  fails at **instantiation** with a wasmtime linking error.
  `test_harness_hostfns.py::test_every_task13_fixture_is_fully_bound` is the
  proactive net.
- **C19** `engine.py` owns `make_config()` (the pinned wasmtime feature set,
  with the load-bearing `wasm_relaxed_simd` before `wasm_simd` ordering),
  `read_memory(ptr, length)` off the guest's exported `"memory"`, and the
  `_trampoline` that is the ONE structural place u64 masking happens:
  `val.as_i64(impl(*(val.as_u64(v) for v in raw)))`. Module caching lives at
  `tests/harness/cache.py`, not here.
- **C20** `tests/harness/objects.py` (958 lines) owns the linear-memory reads.
  `bytes_new_from_linear_memory` is, verbatim,
  `return self._new(val.TAG_BYTES_OBJECT, Bytes(self._blob(pos, length)))`
  where `_blob` reads through `MiniHost.read_memory`. A mini-host
  `log_from_linear_memory` is that shape plus an 8-byte-per-`Val` unpack of the
  second array, appended to a new recorded list the way `contract_event`
  appends to `self.events`, returning `val.VOID_VAL`.

### C.5 The corpora and the gates

- **C21** `tests/semantics/cases.py` (589 lines): 59 `SemCase` rows
  (`name`, `frontend`, `source`, `kind`, `expect`, `code`, `trap`,
  `tier1_only`, `not_expressible_reason`). 35 of them are in scope for the
  WASM leg (`IN_SCOPE_COUNT = 35` in `tests/unit/test_emitter_semantics.py`).
  Legs: tier-1 `eval` (`tests/semantics/test_semantics.py`), frontend
  classification (`tests/unit/test_frontend_semantics.py`), mini host
  (`tests/unit/test_emitter_semantics.py`), real host
  (`tests/real_host/test_semantics_real.py`). Cases are single EXPRESSIONS
  evaluated against the `serpent.__all__` namespace, which means **A's new
  surfaces do not fit this table** (they need an `env`).
- **C22** `tests/semantics/env_scenarios.py` (1215 lines): 62 `EnvScenario`
  rows with `contract`, `constructor`, `timestamp`, `sequence`,
  `auth_allow_set`, `setup` (a tuple of `Call`/`Advance`), `invoke`, `kind`
  (`value`/`void`/`contract_error`/`auth_failed`/`host_error`), `expect`,
  `code`, `host_error`, `events`, `auths`, and three real-leg tags:
  `mini_host_gap` (a reason string: this row has no WASM leg),
  `host_diverges` (a `HostDivergence(reason, events, answer, auths)`: a
  DECLARED, expected tier-1-vs-real difference the real leg asserts still
  exists), and `real_unrunnable` (the real leg skips it loudly). This is the
  right table for A's stateful surfaces.
- **C23** `tests/semantics/host_facts.py` (441 lines): 17 `HostFact` rows plus
  7 `COMPARE_VECTORS`. A row carries `real: Expectation` and
  `tier1: Expectation | Unmodelled(reason)`, with `divergence_reason` REQUIRED
  whenever the two differ and tier 1 is not `Unmodelled`. **Three rows today
  carry `Unmodelled(_NO_MAXIMUM)`, whose reason string is literally "no max
  live-until at tier 1 (D6/E4): `get_max_live_until_ledger` is M2 (SPT1033)"**
  (`host_facts.py:167`). A invalidates the parenthetical (E10).
- **C24** `tests/unit/test_no_stale_promises.py` (654 lines) runs FOUR nets:
  a sub-plan E net (needle `"sub-plan e"`), the union/enum surface-availability
  gate (a surface token within 60 characters of a denial phrase, suppressed by
  a `_REPOINTED` match on `\b(?:M2|M3|sub-plan F|tier 1|tier 2[ab]?)\b`), a
  sub-plan F net (`"sub-plan f"`, `"tier 2b"`/`"tier-2b"`, `\bF's\b`), and a
  sub-plan G net (`"sub-plan g"`, `\bG's\b`). Allowlists are keyed on
  `(path, exact stripped line text)`. **There is no "M2" needle and no "TODO"
  needle**, so a line reading "it lands in M2" does not trip any net today --
  `docs/subset.md` carries exactly that string in two places and the suite is
  green. If A follows the M1 convention it adds a FIFTH net for its own
  forward references, and it must not leave stale "lands in M2" text behind
  for the surfaces it lands.
- **C25** `docs/subset.md` is regenerated by
  `python -m serpent.compiler._render_docs` and pinned byte for byte by
  `tests/unit/test_subset_docs.py::test_docs_subset_md_matches_its_generator_byte_for_byte`.
  It carries two SPT1033 passages: line 91 "Recognized, but not yet lowerable
  (`SPT1033`, landing in M2): `env.call()`, `env.crypto()`,
  `env.current_contract_address()`, `env.deployer()`, `env.logs()`,
  `env.prng()`, `env.try_call()`." and the generated SPT1033 section
  (lines 859-877) rendered from `tests/must_reject/constructs/env_deferred_surface.py`,
  whose doc title is "recognized-but-deferred Env surface (env.logs)" and whose
  body is `env.logs()  # HERE`. **That fixture uses a name A lands, so A must
  repoint it at `env.call()` or `env.deployer()` and regenerate.**
- **C26** The example inventories (nine places, from the cross-inventory census
  `tests/unit/test_examples.py::test_every_example_is_in_every_hand_kept_inventory`,
  labelled O-HYG8): `EXAMPLES` in `tests/unit/test_emitter_end_to_end.py`
  (lines 102-120, the ONE definition, eight members today, plus
  `CONSTRUCTOR_BEARING`); `FIXTURE_SOURCES` in `tests/unit/test_emitter_printer.py`;
  `_FIXTURES` in `tests/unit/test_harness_hostfns.py`; `CORPUS` in
  `tests/unit/test_frontend_fuzz.py` (glob-driven, automatic);
  `tests/goldens/wasm/<stem>.wat.txt`; a real-host module whose text contains
  `EXAMPLE_<STEM>`; `docs/examples/<stem>.md`; a row in
  `docs/examples/index.md`; and a `mkdocs.yml` nav entry (the last two gated
  by `tests/unit/test_docs_site.py`). Example sizes today: counter 67,
  events 107, errors 140, structs 152, guestbook 175, shapes 247,
  bounty_board 282, allowance_token 280 lines.
- **C27** The byte freeze in code: `tests/real_host/test_testnet_fixtures.py`
  has `SETS = (SHAPES, BOUNTY_BOARD)` with `deployed_sha256`
  `7ba2afb0c81ac3cf05a1dd4edfa48b98bfefab6e51901ad7676a27230f84483e`
  (shapes, `CD3KZQVZSUIM6YDGZAC2VSXNN7COV7AR7U5J5N725BAMECARV4LENHYY`) and
  `93477b8326f8e9f804355117f4b789d4bd806a5206f05493b7368557c86cfdfa`
  (bounty_board, `CBBIB2C6C3ULRHJTTPK7FPDU6RFHAJM2IQ5RHHWVDP4C7GXBZ5VF2FEW`).
  `tests/unit/test_harness_strict_obj_cmp.py` additionally pins the FIRST
  shapes deployment at `6a9dd13549bac20f2609ab3d74668963b5249a7943dc7f027cdf6c42bec86e33`.
  `docs/deployments.md` is the human-readable ledger. **The guestbook is not in
  the freeze, and a ninth example would not be either.**

---

## D. PROPOSED ARCHITECTURE

The smallest architecture that makes row A true, expressed as the seams it
adds. Everything else is an extension of a seam M1 already built.

**D-1. One shared primitives home, private, under the zero-dep walk.**
Two new core modules: `src/serpent/_crypto.py` (sha256 and HMAC through
`hashlib`/`hmac`; a keccak-f[1600] permutation; ed25519 `verify_strict`; a
shared short-Weierstrass curve module; secp256k1 recovery; P-256 prehash
verify) and `src/serpent/_prng.py` (the HMAC-SHA256 unbias with the pinned
salt, ChaCha20, `BlockRng` geometry, the two rand 0.8.8 sampler arms, and
Fisher-Yates). Leading underscore, following the `_strkey.py` and `_frame.py`
precedent (C15): they are implementation, not authoring surface, so they do not
join `serpent.__all__`. Both are imported by `serpent.env` AND by
`tests/harness/hostfns.py`, which is the S6 one-implementation rule applied to
the new primitives (E1, E2).

**D-2. The tier-1 `Env` learns four facts it does not have.** `Env.__init__`
gains `contract_address`, `network_id`, `protocol_version`, and
`max_entry_ttl` keyword arguments, each defaulting to a module-level constant
beside `DEFAULT_LEDGER_TIMESTAMP`/`DEFAULT_LEDGER_SEQUENCE` so D6's "one
shared home, not a third copy" keeps holding, and each chosen to equal what
`serpent.testing._real` already feeds the real host (C10). `Ledger` gains
`version()`, `network_id()`, and `max_live_until_ledger()`, all
`_require_frame`-gated like its two existing readers, with
`max_live_until_ledger` computed by K6's formula off the LIVE `_TtlState`
sequence exactly as `sequence()` is.

**D-3. Three new tier-1 accessor classes**, each `_require_frame`-gated and
each a thin facade over D-1: `Crypto` (`sha256`, `keccak256`,
`ed25519_verify`, `secp256k1_recover`, `secp256r1_verify`), `Prng` (`seed`,
`bytes_new`, `u64_in_range`, `shuffle`), and `Logs` (`add`). `Prng` holds its
ChaCha state on the `Env`, reset at frame entry to model the host's
frame-local PRNG (K13.2), so a tier-1 test reproduces the host's
"state advances within an invocation, is destroyed after it" contract.

**D-4. Recognition rows, not new IR.** Every A surface is an existing
`SurfaceKind.HOST_CALL` row in `RECOGNIZED` with `family="env"` (or
`family="container"` for the two conversions), plus `Ledger` dispatch arms.
`KNOWN_FUTURE_ENV_NAMES` shrinks to `{"call", "try_call", "deployer"}`;
`_LEDGER_FUTURE_METHODS` empties and its dispatch arm is deleted;
`_DOSSIER_C4_INVENTORY` in the test grows.

**D-5. Two lowerings, one of them new, plus one new branch.** Everything
except `env.logs()` is `_lower_host_call_raw` unchanged (C7), with ONE
addition: `_lower_host_call` grows a `val_typed_ret is False` branch that boxes
the raw word and skips `narrow_to`, which `prng_u64_in_inclusive_range` is the
first table row to need (C7a, F16). `env.logs().add(msg, *vals)` is the
`_lower_make_struct` recipe (C8): `intern(msg.encode("utf-8"))` for the
message, `ctx.memory.scratch(8 * n)` plus `_store_val` per value, then four
`pack_u32val` immediates and the import call. `log_from_linear_memory` joins
`_LINEAR_MEMORY_HOST_FNS` and the `needs_memory` computation (C11).

**D-6. The mini host gains the same functions, by delegation.** `FullHost`
binds the five crypto functions, the four prng functions, the four context
functions, and the two conversions -- every crypto and prng body a one-line
call into `serpent._crypto`/`serpent._prng`, so the mini host and tier 1
cannot disagree by construction. `FullHost` gains `contract_address`,
`network_id`, `protocol_version`, and `max_entry_ttl` attributes beside its
existing `ledger_timestamp`/`ledger_sequence` stubs (C17).

**D-7. Evidence at the real host.** New `ENV_SCENARIOS` rows for the stateful
surfaces, new `HOST_FACTS` rows for the ones only the real host can settle,
and one dedicated PRNG differential module that pins the reseeded stream at
tier 1, tier 2a, and tier 2b against each other.

### Task decomposition (11 tasks, with dependencies and model seating)

Model seating follows process.md: Sonnet for mechanical and well-specified
work and scoped re-reviews; **Opus for anything feeding divergence guards,
oracle edits, or the emitter**.

| # | Task | Depends on | Model | Why |
|---|---|---|---|---|
| **T1** | `src/serpent/_crypto.py` and `src/serpent/_prng.py`: the primitives, with K20's vendored vectors and K15's three sdk PRNG vectors as unit tests. No `Env`, no compiler, no host. Zero-dep gate green. | -- | **Opus** | This IS the oracle. A wrong stream here is silently green everywhere else (S8). |
| **T2** | Tier-1 `Env`: the four new facts (D-2), `Ledger`'s three methods, `Env.current_contract_address()`, and the `Crypto`/`Prng`/`Logs` facades (D-3). `String`/`Bytes` conversion helpers on the value layer. | T1 | **Opus** | Oracle edit; the frame-local PRNG lifetime is a semantics decision. |
| **T3** | Recognition (D-4): `RECOGNIZED` rows, the `KNOWN_FUTURE_ENV_NAMES`/`_LEDGER_FUTURE_METHODS` shrink, the completeness-test inventories, the repointed SPT1033 fixture, new `must_reject` fixtures for the sanctioned misuse codes, `docs/subset.md` regen. | T2 | **Opus** | Diagnostics and registry adjacency; D2/D3 discipline. Registry edits themselves are the controller's. |
| **T4** | Emitter, the plain calls (D-5 first half): the four context functions, the five crypto functions, all four prng functions, and the two conversions, PLUS the `val_typed_ret is False` boxing branch (C7a) with a full-range `u64_in_range` draw in its test plan (F16). Goldens, disassembly snapshots. **Byte-freeze check: no shapes/bounty_board byte moves** (R4). | T3 | **Opus** | Emitter, and the one place A can ship a silent wrong answer. |
| **T5** | Emitter, `log_from_linear_memory` (D-5 second half): the pool and scratch layout, the `_LINEAR_MEMORY_HOST_FNS` and `needs_memory` joins, the memory-export assertion, the SPT8002/SPT8003 limit paths. | T4 | **Opus** | Emitter plus linear memory: S2/S3's named hazard. |
| **T6** | Mini host (D-6): the fifteen new bindings, the four new `FullHost` attributes, the `log_from_linear_memory` reader, and the binding-coverage assertion against the grown target sets (C2, C18). | T1, T4, T5 | **Opus** | Tier-2a oracle edit (D9's E8 precedent seats this on Opus). |
| **T7** | `serpent.testing` and the Rust facade: expose the network id, the ledger sequence, and the contract address from `RealEnv`; add the `set_base_prng_seed` passthrough if E2 needs it. Rust gate (`cargo fmt`, `clippy -D warnings`, `test`). | T2 | **Opus** | Touches `host/src/lib.rs`, the tier-2b gate. |
| **T8** | The real-host differential: new `ENV_SCENARIOS` rows and new `HOST_FACTS` rows for every A surface, the three `_NO_MAXIMUM` reason strings updated per E10, and the dedicated PRNG stream differential (F5). | T6, T7 | **Opus** | This is where "hollow" is caught or missed. |
| **T9** | The ninth example (E13): `examples/raffle.py` plus all nine inventory joins (C26), the WAT golden, the tier-1 and real-host test modules, the docs page, the index row, and the nav entry. | T5, T6 | **Sonnet** | Mechanical once the surfaces exist and the pattern is D11's. |
| **T10** | Docs and hygiene: the authoring-guide sections for crypto, prng, and logging; the `docs/subset.md` final regen; mkdocstrings entries for any new public names; the promise-net sweep and (if ruled) a fifth net for A's own forward references; the SPT6001 reason-text correction (C13). | T3, T9 | **Sonnet** | Text, with one generated-file regen. |
| **T11** | Close: four gates plus the Rust gate plus `mkdocs build --strict`; the attention file; the ledger; the carried-obligations list for B. | all | **Sonnet** | Mechanical. |

T1 and T7 are independent and may run in parallel. T3 blocks on T2 only for
the surface shapes, so an early-start variant is possible if the plan wants it.

---

## E. OPEN QUESTIONS FOR THE CONTROLLER (recommendation first)

### E1. The tier-1 crypto implementation strategy

**Recommendation: implement all four non-stdlib primitives in pure Python in
`src/serpent/_crypto.py`, shared by tier 1 and the mini host. No optional
extra, no third-party dependency, no stub.**

Evidence (K21, all measured 2026-09-11 on this checkout): sha256 is stdlib and
free. keccak-256 is ~40 lines and 0.32 ms per call. ed25519 `verify_strict` is
~70 lines and 3.2 ms. secp256k1 recovery and P-256 verify share ~60 lines of
curve arithmetic and cost ~6.5 ms and ~6.1 ms respectively with
`pow(x, -1, p)`. Every one of them reproduced the host's own vector (K20) on
the first attempt. Total: ~325 lines of implementation.

Alternatives considered:
- *An optional `serpent[crypto]` extra wrapping `cryptography` or
  `pycryptodome`.* Rejected: it splits the oracle in two (the core has one
  answer, the extra has another), it cannot serve the mini host without making
  tier 2a conditionally installable, and the S6 one-Val-codec principle is
  precisely the argument against two implementations of one semantics.
  `hashlib` does not expose Keccak (only SHA3, a different padding), so the
  extra would not even be a clean win.
- *A tier-1 stub that raises `NotImplementedError` and defers verification to
  the real host.* Rejected: it makes every crypto-using contract untestable in
  the innermost dev loop, which is the whole point of tier 1 (D3 of the M1-E
  dossier's E1 reading), and it makes the ninth example unrunnable at tier 1.
- *Jacobian coordinates for the ECDSA curves.* Not warranted. 6 ms per
  operation supports hundreds of verifications in a suite; the measured 8.6x
  win from `pow(x, -1, p)` already bought the headroom.

Reversal cost: low. The module is private (`_crypto.py`), so swapping an
implementation later is not a public-API change. If performance ever bites,
the fix is internal.

### E2. The tier-1 PRNG model

**Recommendation: model the host exactly. After `env.prng().seed(b)` tier 1
produces the IDENTICAL stream the host produces, because K13.4 proves the
reseed makes every subsequent draw a pure function of the 32 seed bytes.
Before any reseed, tier 1 uses a DOCUMENTED, deterministic per-Env seed of its
own and says in the docstring that those draws are not the chain's draws.**

This is the single most important semantic question in A, and it is settled by
source and by execution, not by memory:
- `prng_reseed(b)` does `*prng = Prng::new_from_seed(seed32)` -- it REPLACES
  the frame PRNG, it does not mix into it (K13.4).
- `Prng::new_from_seed` is `ChaCha20Rng::from_seed(HMAC_SHA256(SALT, seed))`
  with the salt fixed at protocol level and documented as such (K13.3).
- A 110-line pure-Python reimplementation reproduced all three of soroban-sdk's
  own pinned doctest vectors exactly (K15).

So the recommended contract is testable and strong: **a test that calls
`env.prng().seed(b)` and then draws gets the same numbers at tier 1, at tier
2a, and on the real host.** That is a genuinely useful property for
commit-reveal contracts, which is exactly what the ninth example is (E13).

The unseeded case is where honesty is required. The host derives the frame
PRNG from a base PRNG the embedder seeds from the txset hash and the
transaction's apply-order position (K13.1). serpent cannot reproduce that, and
must not pretend to. The sdk's own test host seeds the base to all zeros
(K16), so even unseeded draws ARE reproducible on the embedded real host -- but
only as a function of frame count and sdk internals, and pinning that would
couple serpent's tests to soroban-sdk's private derivation order. **Recommend
not pinning unseeded draws anywhere.** Tier 1 uses its own documented constant
so tests are deterministic; the differential compares only reseeded streams.

Alternatives:
- *Use Python's `random` or `secrets` at tier 1.* Rejected: the distribution
  would match and the stream would not, which is the textbook silent-false-green
  (S8).
- *Refuse `env.prng()` at tier 1 and only allow it under the real host.*
  Rejected for the same reason as E1's stub option.
- *Reproduce the unseeded stream too, by exposing `set_base_prng_seed`.*
  Rejected as a default, but T7 may expose it on `RealEnv` for the differential
  to use as a probe. The tier-1 model should not claim the frame-derivation
  chain.

Reversal cost: low for the unseeded default (a constant plus a docstring);
essentially zero for the reseeded path, because it is pinned by the host's own
vectors and cannot drift without the pin moving.

### E3. Authoring names, and what `ed25519_verify` returns

**Recommendation: follow the Rust SDK's names exactly where the subset allows.
`env.crypto().sha256(b) -> Bytes32`, `keccak256(b) -> Bytes32`,
`ed25519_verify(pk, msg, sig) -> None`, `secp256k1_recover(digest, sig, rid)
-> Bytes65`, `secp256r1_verify(pk, digest, sig) -> None`;
`env.prng().seed(b)`, `bytes_new(n)`, `u64_in_range(lo, hi)`, `shuffle(v)`;
`env.ledger().version()/network_id()/max_live_until_ledger()`;
`env.current_contract_address()`; `env.logs().add(msg, *vals)`.**

Where serpent must diverge from Rust, and why:
- Rust returns `Hash<32>`, a newtype over `BytesN<32>` that exists to mark "this
  came from a secure hash" (`soroban-sdk/src/crypto.rs:36`). serpent has no
  such wrapper and adding one is a new value kind. **Return `Bytes32`** and
  document the loss. Consequence: serpent's `secp256k1_recover` takes a plain
  `Bytes32` digest where Rust's safe `Crypto::secp256k1_recover` takes a
  `Hash<32>` and its `CryptoHazmat` variant takes a `BytesN<32>`. serpent's
  surface is therefore the hazmat shape; the docstring must carry the same
  warning the sdk does ("the message_digest must be produced by a secure
  cryptographic hash function, otherwise the attacker can potentially forge
  signatures").
- Rust's `env.prng().gen_range(1..=100)` uses a Rust range literal; Python has
  no equivalent the subset accepts. **Use `u64_in_range(lo, hi)`, inclusive on
  both ends**, matching the host function's own name and semantics, and say
  "inclusive" in the docstring.
- Rust's `env.prng().gen::<u64>()` and `gen_len::<Bytes>(n)` are
  turbofish-typed. **Use `bytes_new(n)`** (the host's name) and express the
  full-range draw as `u64_in_range(0, 2**64 - 1)` rather than adding a second
  spelling.
- `env.logs().add(msg, *vals)` matches `Logs::add` (the sdk's `Logs::log` is
  `#[deprecated(note = "use [Logs::add]")]`). Rust's `log!` is a macro that
  compiles away under `cfg!(debug_assertions)`; serpent has no such switch, so
  the call is always emitted (E6).

**On `ed25519_verify` returning nothing:** the host TRAPS on a bad signature.
`verify_sig_ed25519` returns `Void` and every failure is `Error(Crypto,
InvalidInput)` or `Error(Object, UnexpectedSize)` (K10), and soroban-sdk's own
`Crypto::ed25519_verify` documents "### Panics: If the signature verification
fails." **Recommend tier 1 mirror this by raising a NON-recoverable exception**
(the `StorageTrap` family's sibling, or a new `CryptoTrap`), never by returning
`Bool(False)`. A `-> Bool` surface would be a lie: it would let a contract
author write `if not env.crypto().ed25519_verify(...)`, which compiles to code
that can never take the false branch on chain. Whether the trap is recoverable
through B's `try_call` is B's question, and the honest answer today is that
`Error(Crypto, ...)` is a host error, not a contract error, so it is NOT in
S12's recoverable class -- but that is a B claim and A should state it as
unverified rather than assert it. **UNVERIFIED:** whether a `try_call` across a
frame boundary can observe a `Crypto` error as a recoverable value. What would
verify it: a two-contract real-host probe, which is B's Task 0 work.

Reversal cost: medium. These are public authoring names; once documented they
are breaking to change (the D4-of-M1-E "breaking-after-docs" note). This is the
argument for settling them in the dossier rather than mid-execution.

### E4. `Bytes65` for the recovered secp256k1 key

**Recommendation: add a real `class Bytes65(Bytes)` to
`src/serpent/types/buffers.py` beside `Bytes32` and `Bytes64`, export it from
`serpent.__all__` (41 names), and move the `test_public_api.py` pin in the same
commit.**

Evidence: K19, measured. `bytes_n(65)` compiles as an annotation but fails
`uv run mypy --strict` with `[valid-type]`, and every example is under the
mypy-strict gate (M1-E attention "examples/ (five files) joined the mypy
--strict ... gates"). A module-level `Bytes65 = bytes_n(65)` is rejected by the
frontend with SPT3014. The `buffers.py` comment at lines 200-203 already states
the reason `Bytes32`/`Bytes64` are real classes: "a name bound to a factory
call is a *variable* and `x: Bytes32` would not type-check under `mypy
--strict`." `Bytes65` is the same case.

Alternatives: return a plain `Bytes` (loses the length invariant the host
guarantees and makes the ninth example's types weaker); make callers write
`# type: ignore[valid-type]` (unacceptable in an example). Note in passing that
`bytes_n`'s docstring currently says "Annotating an arbitrary length awaits
compiler support in sub-plan C", which K19 shows is stale -- `types_.py:510-515`
handles it. A one-line docstring correction, not a behaviour change.

Reversal cost: low-ish but public. A new `__all__` name is additive and never
breaking.

### E5. Where the tier-1 contract address, network id, and max entry TTL come from

**Recommendation: four new module-level constants in `serpent/env.py` beside
`DEFAULT_LEDGER_TIMESTAMP`/`DEFAULT_LEDGER_SEQUENCE`, each equal to what
`serpent.testing._real` already feeds the real host, plus matching keyword
arguments on `Env.__init__`. `DEFAULT_NETWORK_ID = bytes(32)`,
`DEFAULT_MAX_ENTRY_TTL = 6_312_000`, `DEFAULT_PROTOCOL_VERSION = 28`, and a
`DEFAULT_CONTRACT_ADDRESS` synthesised once from a documented 32-byte id
through `serpent._strkey.encode(VERSION_CONTRACT, ...)`. `serpent.testing._real`
then IMPORTS the first three instead of redeclaring them (D6's "one shared
home, not a third copy").**

The network id and max entry TTL are exact matches today (C10). The protocol
version is 28 in `_real.py` (`DEFAULT_PROTOCOL`) and must stay pinned to the
embedded host's major, so A should import rather than duplicate.

The contract address is the only genuinely new value. The real host's address
comes from `register`, which derives it deterministically from the sdk's
`Generators` under a zero base PRNG seed (K16). **Recommend the differential
assert the INVARIANT, not the literal**: "`current_contract_address()` equals
the address this env deployed at", which passes at tier 1 (the constructor
argument) and on the real host (the `register` return) without pinning an sdk
internal.

Alternatives: derive the tier-1 address from a hash of the contract class name
(cute, but it makes the value move when a class is renamed); refuse to model it
and mark the surface tier-1-unrunnable (defeats the ninth example).

Reversal cost: low. Constants and one keyword argument.

### E6. `env.logs()` versus `env.log(...)`, and what a log costs

**Recommendation: `env.logs().add(msg, *vals)`, matching the Rust SDK's
`Logs::add` and keeping the `env.<surface>()` chain shape every other Env
surface uses (`env.storage()`, `env.ledger()`, `env.events()`). `msg` must be
a string LITERAL, exactly as Rust's `log!` requires (`$fmt:literal`).**

Cost, from K12 and S10: the emitted code is one interned pool entry (paid once
per distinct message, at build time), `8 * n` bytes of scratch reserved forever
(C8's bump allocator never frees), `n` `i64.store` instructions, three
`i64.const` immediates, and one host call at about 74 instructions of fixed
overhead (S10). At run time on chain with diagnostics off, the host does not
even read the memory (K12.2). So a log is cheap but not free, and the scratch
it reserves is permanent -- a contract with a log in a hot loop pays the
scratch once, not per iteration, because the address is compile-time.

The important safety fact: **a log lowering cannot trap** (K12.1). Every error
inside `log_from_linear_memory` is swallowed by `with_debug_mode`. This removes
what would otherwise be A's scariest failure mode, and it also means the mini
host and tier 1 must NOT invent a failure the host does not have (F7).

**Recommend the `msg` literal requirement be a compile-time check with a
sanctioned code** (E12), because a non-literal message cannot be interned and
the fallback (building a String object at run time and copying it out) is a
different lowering entirely.

Reversal cost: low before docs; the chain shape is the conservative choice.

### E7. Does `network_id()` return `Bytes32`, and is `bytes_to_string` lossless?

**Recommendation: yes, `network_id() -> Bytes32`.** env.json's own doc says the
value "is always 32 bytes in length" (K2) and soroban-sdk returns
`BytesN<32>` (`ledger.rs:102`). The emitter narrows the returned `BytesObject`
through the existing `narrow_to` path, which already checks `_LENGTH` for
length-bearing Bytes requests (M1-E attention: "BytesN checks `_LENGTH` for
length-bearing requests").

On the conversions: **recommend `bytes_to_string` be documented as NOT
guaranteed lossless at tier 1.** The host does no UTF-8 validation (K9) and
`ScString` is a byte string, but serpent's tier-1 `String` wraps a Python `str`
(C14). So `bytes_to_string(Bytes(b"\xff"))` has no faithful tier-1 answer.
Three options: (a) tier 1 decodes with `errors="surrogateescape"` and
round-trips exactly, (b) tier 1 raises on non-UTF-8 input, which invents a trap
the host does not have, or (c) `String` learns a bytes payload, which is a
value-layer change. **Recommend (a)**, with the round-trip property pinned by
a tier-1 test and a real-host differential row over a non-UTF-8 input. (b) is
the silent-divergence option and should be refused. (c) is C's or E's, not A's.
**UNVERIFIED:** whether `surrogateescape` survives the `_scval` marshalling in
`serpent.testing`. What would verify it: a real-host round trip of
`Bytes(bytes(range(256)))` through `bytes_to_string`/`string_to_bytes`, which
is a named row in T8.

### E8. Do the protocol gates matter for A?

**Recommendation: no gate fires, and A should add one regression test proving
it.** Measured (C12, K17): `DEFAULT_TARGET_PROTOCOL = 27`; the highest
min_proto A imports is 23 (`string_to_bytes`/`bytes_to_string`), then 21
(`verify_sig_ecdsa_secp256r1`); mainnet is on 27 and testnet on 28. So
`check_protocol_target` passes and `declared_protocol` simply returns a HIGHER
computed floor for contracts that use those functions: 23, or 21, instead of
20.

Two consequences the plan must handle:
1. A is the first sub-plan whose ORDINARY surface raises an artifact's declared
   floor above `BASE_PROTOCOL` via an import. The `inspect` command's
   "declared versus recomputed" rendering (D10's MISMATCH ruling) gets its
   first non-trivial input, and that is worth a test.
2. `NO_FIXTURE_REASONS["SPT6001"]` becomes factually wrong (C13). **Recommend a
   sanctioned wording-only correction in the same commit**, naming the two
   protocol-23 functions and restating why the allowlisting still holds (a
   `must_reject` fixture cannot pass `target_protocol`).

Reversal cost: zero for the finding; the wording change is text under D2's
sanctioned-pass rule.

### E9. Is `env.py` promoted in A?

**Recommendation: NO. Leave the `serpent/env/` package promotion to E as the
roadmap says (R5), and keep A's growth out of `env.py` by putting the
primitives in `_crypto.py` and `_prng.py` (D-1) and the three new accessor
classes in `env.py` as thin facades.**

The facts: `env.py` is 1858 lines (C9); E10's trigger was "past ~600 lines"
and was tripped long ago (D5); the promotion is explicitly E's row. A's
additions to `env.py` itself are three small classes and four constants,
perhaps 250 lines, because all the arithmetic lives in the private modules.
Promoting a 1858-line module in the middle of a sub-plan that is also editing
it is the worst possible sequencing: the diff becomes unreviewable and every
downstream task rebases onto a moved file.

Alternative: promote FIRST, in a pure-move task before T2. Genuinely tempting,
and the argument for it is that E will have to move ~2100 lines instead of
~1858. That is a 13 percent worse problem, not a different one. Reject.

Reversal cost: zero. The promotion is a mechanical move whenever E runs.

### E10. The `get_max_live_until_ledger` overlap between A and D

**Recommendation: A lands the ACCESSOR and the `max_entry_ttl` constant only.
D keeps the clamp, the trap, and the per-bucket minimum floors. A updates the
three `Unmodelled(_NO_MAXIMUM)` reason strings in `host_facts.py` to say what
is now modelled and what is still D's, and does NOT flip those rows to a
modelled expectation.**

The roadmap lists `get_max_live_until_ledger` at tier 1 in BOTH row A and row D
(R1 and R6), which is a genuine decomposition defect worth naming for the plan
review (R7). The split above is the one that makes each row honest:
- The accessor is a closed form, `sequence + max_entry_ttl - 1` (K6). A can
  implement it exactly, and it is a leaf like every other A surface.
- The clamp and trap semantics ("persistent extension past max CLAMPS,
  temporary TRAPS", S9/D4) are a change to `_TtlState` and the three bucket
  classes, and they interact with the minimum floors D owns. Landing half of
  the TTL model in A and half in D is how a model ends up inconsistent.

The specific text to change: `host_facts.py:167`,
`_NO_MAXIMUM = "no max live-until at tier 1 (D6/E4): get_max_live_until_ledger is M2 (SPT1033)"`.
After A, the parenthetical is false. **Recommend the new string name M2-D
explicitly**, so the promise net and a future reader both see who owns it.

Reversal cost: zero; it is a scope statement plus one string.

### E11. What the real-host differential can and cannot pin for the PRNG

**Recommendation: pin exactly three things, and refuse the fourth.**

1. **Pin: the reseeded stream, across all three tiers.** Reseed with a known
   32-byte value, then draw bytes, draw a range, and shuffle a known Vec.
   Assert tier 1 == tier 2a == the real host, value for value. K13.4 and K15
   say this is sound, and it is the strongest evidence A can produce.
2. **Pin: reseed determinism within one tier.** Two reseeds with the same seed
   in the same invocation produce the same draws (this is what the host's own
   `prng_test` asserts).
3. **Pin: the error shapes.** `prng_reseed` with a non-32-byte `Bytes` is
   `Error(Value, UnexpectedSize)`; `prng_u64_in_inclusive_range(lo, hi)` with
   `lo > hi` is `Error(Value, InvalidInput)` (K13.4, K13.5). Both are
   `host_error`-kind `ENV_SCENARIOS` rows.
4. **Refuse: unseeded draws.** Do not pin a literal value for any draw made
   before a reseed, at any tier. The real host's value is a function of the
   sdk's private frame-derivation order under a zero base seed (K16); tier 1's
   is serpent's own constant. **Recommend an `ENV_SCENARIOS` row with a
   `host_diverges` declaring exactly this** -- the tag exists for "the tier-1
   model is known wrong-by-omission, so the row DECLARES the difference rather
   than the real leg discovering it" (C22), which is precisely the situation.

The honest `HOST_FACTS` rows A can claim: the reseeded-stream equality (with
`tier1` a real `Value`, not `Unmodelled`), the two error shapes, and
`max_live_until == sequence + max_entry_ttl - 1`. The row A must NOT claim: any
unseeded draw.

Reversal cost: zero. It is a statement of what the tests assert.

### E12. New sanctioned registry codes

**Recommendation: request FOUR new codes, and prove the other candidate shapes
are already covered.** New codes are controller-sanctioned (D2); A's plan must
not invent them, and an implementer who thinks one is missing returns BLOCKED.

First, what is ALREADY covered and needs no code (verified against C6):
- Wrong arity on any new call (`env.crypto().sha256()`,
  `env.prng().u64_in_range(1)`): **SPT3020**, which already covers "a
  recognized API call with the wrong arguments (wrong arity, a missing required
  argument, or a duplicate keyword)".
- A per-argument type disagreement (`sha256(U32(1))`): **SPT3018**, "a call
  argument disagreeing with an InternalCall/recognized-API parameter type".
- `env.crypto` or `env.prng` referenced without being called:
  **SPT1038**, which already covers "Env API attribute referenced without being
  called/chained".
- An unknown method on the new surfaces (`env.crypto().blake3(...)`):
  **SPT2006**, "unknown Env attribute", which is what `_recognize_ledger_method`
  and `_recognize_events_method` already do for their own surfaces.
- A still-deferred name (`env.call`, `env.try_call`, `env.deployer`):
  **SPT1033**, unchanged. **Note for the plan: SPT1033 is NOT retired in A**,
  contrary to a literal reading of R1. It stays live for B's three names; what
  A does is shrink its input set, repoint its `must_reject` fixture from
  `env.logs()` to a name B owns (C25), and (if sanctioned) widen its intent
  text, which currently reads "it lands in M2" and will be true only of B's
  names afterwards.

The four genuinely uncovered shapes, each with a proposed band, construct, and
intent:

| Proposed | Band | Construct | Intent |
|---|---|---|---|
| **Candidate 1** | SPT1xxx | `env.logs().add(msg, ...)` where `msg` is not a string literal | "a log message must be a string literal; it is interned into the module's data segment at build time" |
| **Candidate 2** | SPT1xxx | a log call with more value arguments than the scratch layout admits, or with a non-chain-value argument | "a log value must be a chain value; each is written to linear memory as one 8-byte Val" |
| **Candidate 3** | SPT4xxx or SPT5xxx | a crypto call whose fixed-length argument has a statically-known wrong length (`ed25519_verify(Bytes32(...), msg, Bytes32(...))` for the signature slot) | "this argument must be exactly N bytes; the host rejects any other length" |
| **Candidate 4** | SPT1xxx | `env.prng().shuffle(v)` in a VALUE position, or on a receiver the compiler does not own | "the host's shuffle is functional; rebind the vector (`v = env.prng().shuffle(v)`)" |

Candidate 4 deserves scrutiny: **SPT1034** already exists for exactly the
functional-host-op mutation shape ("the host's ops are functional ... while
`types.Vec.push_back` mutates in place"), and `prng_vec_shuffle(v) -> VecObject`
is the same shape. **Recommend the plan first attempt to reuse SPT1034 and only
request Candidate 4 if the construct list genuinely does not fit** -- which
would itself be a wording widening rather than a new code. Candidate 3 may
similarly be reducible to SPT3018 if the length is carried in the `Ty`
(`Ty.bytes_n(n)` exists, C14/K19), in which case a wrong-length argument IS a
type mismatch and SPT3018 is honest. **The plan must resolve both reductions
before requesting codes**, per D3.

Reversal cost: HIGH for a new code once published (append-only, D2), which is
why the reductions must be attempted first. Zero for the reductions themselves.

### E13. The ninth example

**Recommendation: build the commit-reveal raffle the roadmap names, and shape
it on the host's OWN documented pattern rather than inventing one.**

`src/host/prng.rs`'s module comment (K13, lines 44-66) spells out the intended
two-transaction protocol verbatim: "tx1: write commitment finalizing all inputs
to 'random action', plus N = current ledger and S = prng_bytes_new(32). tx2:
re-read all committed values, if ledger > N, prng_reseed(S), and use PRNG to
take 'random' action committed-to." An example that implements exactly that is
not a toy: it is the host authors' own recommended construction, and it
exercises every surface A lands.

Proposed shape, roughly 200 to 240 lines (between shapes and bounty_board,
C26):
- `__init__(env, admin)`: stores the admin, the entry deadline, and
  `env.current_contract_address()` as the raffle's identity in an event topic.
- `enter(env, who)`: `who.require_auth()`; appends to a `Vec[Address]`;
  publishes an event. Uses `env.ledger().sequence()`.
- `commit(env)`: admin-only; draws `S = env.prng().bytes_new(32)`, stores
  `sha256(S)` as the public commitment plus `N = env.ledger().sequence()`, and
  stores `S` itself in TEMPORARY storage with a TTL. `env.logs().add("committed
  at", N)`.
- `reveal(env)`: refuses unless `env.ledger().sequence() > N`; re-reads `S`;
  asserts `env.crypto().sha256(S) == commitment`; `env.prng().seed(S)`; then
  `winner = entries.get(env.prng().u64_in_range(0, len(entries) - 1))` and
  publishes the winner. `env.logs().add("winner drawn", winner)`.
- `shuffled_entrants(env)`: returns `env.prng().shuffle(entries)`, so the
  shuffle surface is exercised and the stream-equality differential has a
  contract to run against.
- One method reading `env.ledger().network_id()` and
  `env.ledger().max_live_until_ledger()` so those two are not example-less.

What it does NOT do: `keccak256`, `ed25519_verify`, `secp256k1_recover`, and
`secp256r1_verify` have no natural place in a raffle. **Recommend those four be
covered by `ENV_SCENARIOS` rows and a dedicated `tests/fixtures/` contract
rather than being forced into the example**, because an example that verifies a
signature nobody produced teaches nothing. If the controller prefers full
coverage in one example, the alternative is a "gated vault" example
(`ed25519_verify` on an off-chain authorisation, `secp256k1_recover` for an
Ethereum-style signed message) as a TENTH example -- more surface, more lines,
and a second inventory pass. Recommend not.

Byte freeze: the raffle is a new file and touches no lowering
shapes/bounty_board reach, so R4 is satisfied by construction. **The plan must
still run the byte-equality tests after T4 and T5**, because the log lowering
changes `layout.py` usage and the emitter's import ordering, and "we did not
mean to" is not evidence (F9).

Inventory joins: all nine from C26.

Reversal cost: low. An example is additive; the inventory joins are mechanical
and the census test (O-HYG8) catches an omission.

---

## F. RISKS: WHERE A CAN BE HOLLOW OR SILENTLY WRONG

Each risk names the failure, the reason it is plausible, and the check in A's
own test plan that catches it.

- **F1. A keccak that passes its own vectors and differs from the host on an
  edge input.** Keccak-256 and SHA3-256 differ ONLY in the padding byte (0x01
  versus 0x06). An implementation that copies a SHA3 reference and forgets the
  padding change produces plausible-looking 32-byte digests that are wrong for
  every input. The empty input is the discriminator.
  **Check:** all four of the host's own keccak vectors (K20), including `""`
  and the 1,000,000-byte input, as a tier-1 unit test in T1, PLUS a real-host
  `HOST_FACTS` row over `""` so the claim is evidence and not self-agreement.

- **F2. A rate/capacity error in keccak-f[1600] that only shows on multi-block
  input.** The 136-byte rate means a single-block test passes with a broken
  absorb loop.
  **Check:** a vector longer than 136 bytes and one exactly 136 bytes (the
  padding-block boundary) in T1; the host's 1,000,000-byte vector covers the
  long case.

- **F3. An ed25519 verify that rejects what the host accepts.** Read from the
  source (K11): `VerifyingKey::from_bytes` does NOT reject a non-canonical `y`
  encoding; curve25519-dalek masks the sign bit and reduces the field element.
  A textbook reference implementation that checks `y < p` and rejects otherwise
  will refuse a key the host accepts. Symmetrically, `verify_strict` DOES
  reject a small-order `R` or public key and a non-canonical scalar `s`, which
  a plain `verify` implementation would accept.
  **Check:** three tier-1 unit tests in T1 (a non-canonical `y` that must be
  accepted-and-reduced, a small-order public key that must be rejected, an
  `s >= l` that must be rejected) plus a real-host differential row for each,
  because this is precisely the class where tier 1 agreeing with itself proves
  nothing. **This is the highest-probability silent divergence in A.**

- **F4. An ECDSA implementation that accepts a high-`s` signature.** Both
  secp256k1 recovery and P-256 verify go through
  `Host::ecdsa_signature_from_bytes`, which rejects `sig.s().is_high()` with
  `Error(Crypto, InvalidInput)` (K10). Most reference implementations do not
  do this check. A tier 1 that omits it accepts signatures the host refuses.
  **Check:** a negative vector per curve (the same signature with `s` replaced
  by `n - s`) in T1 and a real-host row.

- **F5. A PRNG whose distribution is right and whose stream is wrong.** Three
  distinct ways this happens, all measured or source-read: (a) forgetting the
  HMAC-SHA256 unbias, (b) using the u64 sampler arm for `shuffle`, which
  actually uses the u32 `sample_single_inclusive` arm with a DIFFERENT
  rejection zone (K14), and (c) getting the 64-word buffer boundary wrong in
  `next_u64`. Each produces a uniform-looking stream that is not the host's.
  **Check:** the three sdk doctest vectors as tier-1 unit tests (K15, already
  proven to pass), plus the T8 three-tier stream differential. **The shuffle
  arm is currently UNVERIFIED against any vector** -- there is none in the sdk
  doctests or the host tests -- so the real-host shuffle row in T8 is not
  optional, it is the only evidence that will ever exist for it.

- **F6. `bytes_to_string` losing bytes at tier 1.** `String` wraps a Python
  `str`; the host's `ScString` is a byte string with no UTF-8 validation (K9).
  A naive `.decode("utf-8")` raises on arbitrary bytes, inventing a trap; a
  `.decode("utf-8", "replace")` silently corrupts.
  **Check:** a tier-1 round-trip over `bytes(range(256))` and a real-host
  differential row over the same input (E7).

- **F7. A log lowering that traps where the host does not.** The host swallows
  every error inside `log_from_linear_memory` (K12.1). If the mini host or
  tier 1 raises on a malformed log, tests fail on shapes the chain tolerates,
  and worse, a contract author "fixes" a non-problem.
  **Check:** a mini-host and tier-1 test that a log with a value the model
  cannot decode is a NO-OP, not an error; an `ENV_SCENARIOS` row proving a
  contract that logs still returns its value on all three tiers.

- **F8. A log lowering that traps ON THE HOST under budget.** The inverse risk.
  `with_debug_mode` runs the closure under `budget.with_shadow_mode` (K12), so
  a log in a hot loop consumes shadow budget; and the scratch reservation is
  permanent per call site (C8), so a contract with very many log sites can
  exhaust one 64 KiB page and fail the build with SPT8003.
  **Check:** an emitter unit test that N log sites reserve `8 * sum(n_i)` bytes
  and that the page limit reports SPT8003; a real-host row with a log inside a
  loop to confirm it does not exhaust the real budget at realistic iteration
  counts. **UNVERIFIED:** the shadow-budget cost of a log at chain-realistic
  scale. What would verify it: a real-host invocation metering probe (T8), for
  which `RealEnv` already has `enable_invocation_metering` upstream.

- **F9. A byte-freeze violation nobody meant.** T5 changes `layout.py` usage
  patterns and the emitter's import set; `examples/shapes.py` and
  `examples/bounty_board.py` are pinned to their deployed bytes (R4, C27).
  A change to how imports are ordered, or to a shared helper, moves those bytes.
  **Check:** run the two deployed-bytes tests as an explicit gate line on T4,
  T5, and T11, not just at close. A move is a controller decision (D9), not a
  fix-wave edit.

- **F10. An inventory omission in the ninth example.** Nine places to join
  (C26); the census test covers six of them and `test_docs_site.py` covers two
  more.
  **Check:** T9's acceptance is the census test plus the docs-site tests, run
  explicitly and quoted in the ledger.

- **F11. A completeness test that silently loosens.** `ENV_HOST_FN_TARGETS` is
  DERIVED from `RECOGNIZED`, so adding rows changes it automatically; the
  assertion that catches an error is
  `ENV_HOST_FN_TARGETS == _DOSSIER_C4_INVENTORY`, a literal in the test file
  (C2). An implementer who "fixes" the failure by editing the literal to match
  whatever the table now produces has removed the check.
  **Check:** the task brief must state that the literal is updated to the
  INTENDED new inventory, enumerated in the plan, and the task review verifies
  the enumeration rather than the diff.

- **F12. `SPT1033` quietly becoming unreachable.** If A shrank
  `KNOWN_FUTURE_ENV_NAMES` to empty the code would have no source trigger and
  would need `NO_FIXTURE_ALLOWLIST`. It does not (three names remain, C3), but
  the `must_reject` fixture still points at `env.logs()`, a name A lands, so
  the fixture would start FAILING (it would compile).
  **Check:** T3 repoints the fixture and regenerates `docs/subset.md`; the
  byte-drift test (C25) is the tripwire.

- **F13. Process: an implementer touching `codes.py`.** A's registry work is
  the largest since M1-C. D2 forbids subagents from editing it.
  **Check:** every task brief that could want a code says so, and the expected
  behaviour is BLOCKED, not a guess.

- **F14. Process: the unsigned-commit log.** The M2 run commits unsigned by
  design (R8), and M1-G is still un-re-signed. A's close must leave
  `.git/unsigned-commits.log` accurate and restate the single re-sign base
  (`11de4bd`) and the `v0.1.0` re-point in the attention file, or the
  information is lost between sub-plans.

- **F15. Scope drift into D.** Three `HOST_FACTS` rows become temptingly
  closable once tier 1 knows `max_entry_ttl` (C23, E10). Closing them means
  implementing the clamp and trap, which is D's row and interacts with the
  per-bucket floors.
  **Check:** the plan states E10's split as a non-negotiable boundary, and the
  task review for T8 verifies the three rows are re-worded, not re-classified.

---

- **F16. The raw u64 return silently mis-narrowed.** C7a: the generic
  `HOST_CALL` lowering assumes a `Val` return and `prng_u64_in_inclusive_range`
  does not have one. A test that draws a SMALL number
  (`u64_in_range(0, 100)`) may well pass by accident, because a small raw word
  can land on a tag byte that `abi_check` tolerates; the bug appears only for
  large draws. This is the exact shape of the bug M1-F's real host exposed in
  a shipped emitter (the two-small-Symbols `obj_cmp`), and it is invisible to
  wasm-tools.
  **Check:** T4's test plan must include a `u64_in_range` draw over the FULL
  range (`0, 2**64 - 1`) at all three tiers, not just a small range, plus an
  emitter unit test asserting the boxing branch is taken. A reviewer should
  treat "the small-range test passes" as no evidence at all.

## G. WHAT A LEAVES FOR B

Stated here so B's dossier can cite it rather than re-derive it:

- `env.current_contract_address()` and `env.crypto().sha256()` exist and are
  differential-tested, which is what R2 says B needs for deployer salts and
  token identity.
- `KNOWN_FUTURE_ENV_NAMES` is `{"call", "try_call", "deployer"}`, and SPT1033
  is still the live diagnostic for all three with its fixture repointed at one
  of them.
- The recoverability of a `Crypto` error through `try_call` is UNVERIFIED and
  is B's first probe (E3).
- Tier-1 frame rollback is still absent (O7), so a tier-1 `try_call` has
  nothing to roll back yet; A's `Prng` frame-reset is the only frame-scoped
  state A adds, and B must fold it into the rollback design.
- The primitives in `serpent/_crypto.py` are available to B for contract-id
  derivation if B needs to model `create_contract` addresses at tier 1.
