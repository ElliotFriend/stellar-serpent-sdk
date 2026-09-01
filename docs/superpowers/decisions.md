# serpent — Decision Log

Judgment calls made autonomously during M1 execution, recorded for review and
reversal. Each entry: context, decision, why, and reversal cost. Fine-grained
task rulings live in the per-plan SDD ledgers during execution; entries here are
the ones with lasting consequence. (Decisions made *with* Elliot in-session are
in the spec/findings docs, not here.)

Format:

```
## YYYY-MM-DD <short title>
- Context:
- Decision:
- Why:
- Reversal cost:
```

Append new entries at the end of the file; never insert before an existing
entry.

## 2026-08-26 Standing-autonomy mechanics for M1
- Context: Elliot granted continue-through-M1 autonomy with a reviewable
  decision record ("keep chugging... clearly see where those decisions were
  made so we can reverse/change them").
- Decision: This log is that record. Sub-plans B–G are authored, adversarially
  reviewed, triaged, executed, and locally merged to main without per-phase
  sign-off; every judgment call lands here in the same commit series as the
  work. Hard stops remain: irreversible/outward actions and the spec-mandated
  user-approved testnet deployment at M1's end.
- Why: Matches the granted balance of momentum vs. auditability.
- Reversal cost: None — process-level; revoke anytime.

## 2026-08-26 M1-A review adoptions that shape user-facing surface
- Context: M1-A adversarial review; all findings adopted (triage presented
  in-session). Three shape the authoring surface beyond the session summary:
  `errorcode(N)` declarations, `Bytes32`/`Bytes64` aliases (both spec-corrected,
  flagged to Elliot), and `Vec(U32)`/`Map(Symbol, U32)` explicit element types
  at construction with `Vec[U32]`/`Map[K, V]` as annotation-only forms.
- Decision: as stated; also reflected ops supported so `sum()` works over chain
  ints, and `**`/`divmod`/bitwise are explicit TypeErrors until a contract
  needs them; U256/I256 deferred to M2.
- Why: Each verified against mypy repros / runtime-generics limits; details in
  the M1-A plan's Global Constraints and the review transcript summary.
- Reversal cost: Low before sub-plan C consumes the surfaces; moderate after
  (compiler frontend patterns would need updating).

## 2026-08-26 Chain-int truthiness: `bool(x)` is `value != 0`
- Context: Task 5 implementer escalated — `_ChainInt` had no `__bool__`, so
  `bool(U32(0))` was `True` (Python object default), making `if amount:` a trap.
- Decision: `__bool__` returns `value != 0` on every numeric chain type;
  semantics-table cases added in Task 10; the sub-plan C frontend must lower
  truthiness tests to the equivalent zero-comparison.
- Why: Matches Python intuition AND compiles exactly (i64.eqz); the TypeError
  alternative forces noisier code without a fidelity gain.
- Reversal cost: Low before sub-plan C; a compile-reject could replace it later
  at the cost of breaking `if amount:` in existing contracts.

## 2026-08-26 Timepoint/Duration: no arithmetic in M1-A
- Context: Task 5 review — both types inherited full _ChainInt arithmetic by
  default (Timepoint * Timepoint "worked"), while Rust's newtypes expose no ops.
- Decision: disable ALL arithmetic on Timepoint/Duration (TypeError naming the
  omission and pointing at the to_u64/from_u64 bridges). Deliberate time algebra
  (Duration+Duration, Timepoint-Timepoint→Duration) is a sub-plan E decision.
  Also: Python bool accepted wherever int is for numeric operands (bool ⊂ int is
  Python; the compiler tier rejects it statically anyway) — Task 10 table
  documents it; Bool's ordering accepts plain bool, matching its equality.
- Why: oracle fidelity to the Rust/host surface beats convenience acquired by
  inheritance; additive to re-enable later.
- Reversal cost: trivial (re-allow ops); reverse direction would break contracts.

## 2026-08-26 Bytes-family equality is payload-based
- Context: Task 6 — should Bytes32(p) == Bytes(p)?
- Decision: yes — equality/ordering/hash across Bytes/Bytes32/Bytes64 compare
  payloads (same _SCVAL_RANK). Fixed-length-ness is an authoring constraint;
  on-chain all three are the same BytesObject host type, and val_cmp answers 0
  for equal payloads.
- Why: type-exact equality would diverge from on-chain observable behavior.
- Reversal cost: one predicate + tests, before sub-plan C freezes patterns.

## 2026-08-26 No negative indexing on chain containers/buffers
- Context: Task 7 flagged Vec.get(-1) IndexError vs Bytes[-1] following Python.
- Decision: indexing is chain-faithful everywhere — negative indices raise
  IndexError on Vec.get AND Bytes.__getitem__ (aligned in Task 8). Slicing keeps
  Python semantics as authoring sugar (compiler tier will bound what compiles).
- Why: host vec_get/bytes_get take U32Vals; negative indices are unrepresentable
  on-chain, and oracle surfaces must not answer questions the chain cannot.
- Reversal cost: two lines + tests.

## 2026-08-26 Storage keys are any chain value, not Symbol-only
- Context: Task 9's Env surface typed storage keys as Symbol; Rust storage keys
  are any Val (DataKey enums like Balance(Address) are the dominant pattern).
- Decision: widen key annotations to the chain-value surface (chain types +
  @contracttype instances); implementer picks the exact spelling. Also accepted
  implementer rulings: ledger().timestamp() -> U64 (Rust parity; Timepoint
  bridge exists for opting in), has() -> Bool, @contract rejects static/classmethods.
- Why: Symbol-only would break the moment a token contract needs composite keys.
- Reversal cost: annotation-level now; breaking later.

## 2026-08-26 Event authoring form: inherit from serpent Event base
- Context: Task 9 review — a decorator cannot add statically visible members,
  so `evt.publish(env)` failed mypy strict on @contractevent classes.
- Decision: events inherit a serpent `Event` base declaring publish(env) (still
  NotImplementedError until sub-plan E); @contractevent validates the base is
  present. Also: event topics are heterogeneous (chain-value tuple), not
  Vec[Symbol] — canonical token topics are (Symbol, Address, Address).
- Why: inheritance is the only zero-plugin path to static visibility; Vec is
  homogeneous by design.
- Reversal cost: authoring-surface change — cheap now, breaking after docs/examples.

## 2026-08-27 stellar-sdk becomes a runtime dep of serpent.spec only
- Context: M1-B needs stellar_sdk XDR classes for section emission (spec §7
  mandate); serpent core is zero-dep by design.
- Decision: new optional extra `serpent[spec]` carries stellar-sdk>=15,<16;
  core modules (val/types/errors/decorators/env) stay zero-dep, enforced by an
  import-graph test. Authored contracts never need the extra; building them does.
- Why: spec §7 vs zero-dep rule, reconciled at the subpackage boundary.
- Reversal cost: packaging-level; low.

## 2026-08-27 Spec type/case names restricted to Symbol charset
- Context: M1-B sections validate UDT type and error-case names against
  [a-zA-Z0-9_] (stricter than XDR string<60>, which permits any bytes).
- Decision: keep the restriction — ecosystem tools render type names as Rust
  identifiers; permitting non-identifier names produces specs that render
  brokenly downstream. Promoted from the plan ledger per final review (lasting
  user-facing authoring constraint).
- Reversal cost: relaxing later is non-breaking; tightening later breaks contracts.

## 2026-08-27 M1-C frontend rulings (dossier §E, all recommendations adopted)
- Context: the M1-C inputs dossier (specs/2026-08-27-m1c-inputs-dossier.md)
  posed 20 open questions with recommendations; controller adopted all.
- Architecture: HYBRID frontend (E1) — import the module for declarations/
  annotations (matching build_spec_entries' executed-class contract; build-time
  execution of contract modules documented prominently), AST for method bodies,
  with a mandatory inventory cross-check. Collect-all diagnostics per method
  (E16); STABLE SPT#### error codes as public API (E17).
- Authoring surface line (user-visible): for-in-vec and range(stop|start,stop)
  SUPPORTED via C-side desugaring (E4/E5); arithmetic augmented assignment
  SUPPORTED (E6); early return anywhere with definite-return proof (E7);
  module-level helpers + private methods as internal calls, RECURSION REJECTED
  (E8); and/or/not restricted to Bool operands (E9); truthiness only for
  numeric chain types — `if vec:` is a compile error naming the explicit test
  (E10); container mutation only on unaliased locals C owns — the functional-
  host-op divergence guard (E11); Event.publish(env) REJECTED in M1-C pointing
  at sub-plan E, env.events().publish() is the supported form, and
  tests/fixtures/token_style.py is AMENDED accordingly (E12); raw str/bytes
  literals in == with chain types REJECTED (E13, settles the tier1_only trio);
  slicing via Vec.slice()/Bytes method form only (E18); len() typed U32 (E19);
  bytes_n(N) annotations supported via the hybrid import (E20); chained
  comparisons rejected; walrus rejected.
- Divergence guards (F.1): every Symbol comparison routes through obj_cmp
  (never raw packed-Val compare — the "_"-vs-"A" trap); NO arithmetic constant
  folding (compile-time bounds checks apply to literal coercion only); I32/U32
  carry exact width in the IR; struct field sort owned by C.
- Also: Vec/Map TypeVar bounds widened to include structs (E2, small M1-A
  follow-up landed inside C); struct storage keys allowed with an explicit
  "not modelled in tier 1" ordering note (E3); C owns the reserved runtime
  error-code registry 0xFFFF_FF00.. (E14); tests/must_reject/ excluded from
  mypy/ruff-format/pytest-collection by explicit commented config (E15).
- Why: each recommendation was evidence-cited in the dossier; the conservative
  reject-first line matches spec §1 (reject rather than approximate).
- Reversal cost: per-item low before C's tasks consume them; the fixture
  amendment (E12) is user-visible and trivially revertable when E lands.

## 2026-08-27 M1-C plan-review rulings (all findings adopted)
- Context: adversarial review of the M1-C plan — 4 blockers (unreachable
  SPT6xxx band; must_reject runner inside its own mypy exclusion; semantics-
  classification obligation false vs 3 real cases; error-code registry left
  underivable), 16 majors, 13 minors. Zero disputes; two findings promoted to
  rulings:
- Ruling: Bytes.slice added to the tier-1 surface (narrow M1-A edit — E18's
  method-form slicing needs a method to name; bytes_slice b.f exists); len()
  scoped to Vec/Map/Bytes only (Symbol/String have no __len__ at tier 1 —
  len(Symbol) becomes a compile reject, not an oracle-unrunnable accept).
- Ruling: Map struct VALUES supported at tier-1 runtime (require_chain_value
  widened on the value path only; keys stay per E3) — struct values are
  ordinary on-chain shapes and E2's annotation widening would otherwise create
  a new static/runtime split.
- Also structural: compile_module gains target_protocol; the SPT code registry
  becomes Task 1's primary deliverable (public API); Tasks 7/10 split (plan is
  now 15 tasks); zero-dep exemption instruction deleted (the existing walk
  already handles compiler/ correctly).
- Reversal cost: per-item low pre-execution.

## 2026-08-27 SPT registry: honest-code remap for env API misuse (Task 7a)
- Context: Task 7a review demonstrated SPT3018 ("value's type does not match
  the declared/expected type") prefixing arity errors, uncalled-attribute
  misuse, and empty-topics rejects — incoherent user-facing messages the
  task's own tests could not catch (tautological intent assertion).
- Decision: registry discipline is "no renumber, no delete, no meaning
  reversal" — not "no edits". Sanctioned: (a) SPT3020's wording widened from
  chain-type-constructor arity to general call arity (every existing emission
  remains a valid instance); (b) one new 1xxx code added for "env API used
  with an unsupported call shape"; (c) type-argument-shape rejects use
  SPT3013, matching the Task 6 annotation-shape ruling. Genuine type
  disagreements stay on SPT3018.
- Why: diagnostic codes are frozen public API precisely so their meanings can
  be trusted; shipping wrong-in-kind codes would freeze the incoherence.
- Reversal cost: trivial pre-release (codes unpublished); the widened SPT3020
  wording cannot be re-narrowed after release without abandoning emissions.

## 2026-08-27 Ownership is flow-insensitive per function (M1-C Task 10)
- Context: Task 7b's alias pass was order-sensitive (alias facts recorded as
  the checker reached each statement), unsound in loop bodies. Task 10's
  assembly runs a syntactic pre-pass per function collecting every alias and
  escape fact before any statement is checked.
- Decision: ownership classification is flow-insensitive-conservative over
  the whole function body. User-visible consequence: mutating a container and
  aliasing/embedding it later IN STRAIGHT-LINE CODE now rejects too (SPT1034
  with a rebind/slice-copy rewrite), even though the tiers would agree there.
  Also: a container iterated by a for loop may not be mutated anywhere in the
  function (the hidden iterator handle is an alias).
- Why: soundness in loops requires facts before the body is walked; a region-
  sensitive analysis is real dataflow work M1 does not need. Reject-rather-
  than-approximate matches spec §1; every reject carries a compiling rewrite.
- Reversal cost: relaxing to region-sensitivity later is additive (accepts
  strictly grow); no on-chain artifact depends on it.

## 2026-08-27 compile_module outputs: two host-fn sets, floor over reachable
- Context: dossier §C.1 described one host-fn set. HostCall names alone omit
  fail_with_error (Raise) and obj_cmp (Compare), and D chooses between forms
  (vec_new vs vec_new_from_linear_memory).
- Decision: host_fns_used (exact) + host_fns_reachable (superset incl. D's
  choices); the protocol floor is computed over REACHABLE. runtime_parts_needed
  names (overflow_check, u128_mul, i128_neg, i128_floordiv, i128_mod) are
  C-coined pending D's ratification; the i256 helpers D's 128-bit div/rem may
  reach are in neither set (documented, all ungated today, pinned by test).
- Why: floor over the superset is the safe direction — over-approximation can
  only raise a floor, never under-declare; verified none of the omitted
  conversions is gated above base.
- Reversal cost: collapsing to one set later is a field deletion; D not yet
  written, so zero consumers break.

## 2026-08-27 Three registry codes formally never-emitted (Task 11b)
- Context: fixture completion proved three codes unreachable from real
  source: SPT1009 (bare Slice, always intercepted by SPT1013/1014), SPT4018
  (struct positional args — Task 7b's review adjudicated SPT3020 as the
  honest code for that shape), SPT7003 (break/continue outside a loop —
  CPython's compile() raises SyntaxError, bridged to SPT1037, before the
  frontend's own check can run).
- Decision: all three added to NO_FIXTURE_ALLOWLIST with reachability
  reasons; rows retained under the append-only rule; SPT4018's text carries
  a supersession note; the defensive branches (expr.py SPT1009, stmt.py
  SPT7003) stay as defense-in-depth for AST-only entry paths.
- Why: the executable subset spec (must_reject/) must be complete over
  emittable codes without forcing fake fixtures; deleting rows would break
  the append-only public-API guarantee.
- Reversal cost: none — any code that later becomes reachable just gets its
  fixture and leaves the allowlist.

## 2026-08-27 compile_expression retired in favor of compile_module-only API
- Context: the M1-C plan's Global Constraints named a `compile_expression`
  export; Task 11a instead built a test-side harness (wrap_case +
  compile_module) and the final whole-branch review flagged the substitution
  as unrecorded.
- Decision: compile_module is the single public compiler entry point; no
  expression-level API ships in M1. The semantics-classification harness
  lives in tests (test_frontend_semantics.py).
- Why: one entry point keeps the public contract reviewable; an
  expression-level API has no consumer outside tests.
- Reversal cost: additive — export a wrapper later if D/E want one.

## 2026-08-27 M1-C final-review minors folded into parked passes
- Minors 2 and 3 from the final whole-branch review (registry intent strings
  hardcode limit numbers; frontend.py imports _host._protocol via the private
  path) are folded into the already-parked sanctioned wording/cleanup passes
  (see the M1-C attention file §8-9). Minor 4 (runtime_parts ratification
  caveat) fixed in-code at merge time; Minor 1 is the
  `compile_expression retired` entry.

## 2026-08-27 M1-D emitter rulings (dossier §E, all 16 recommendations adopted)
- Context: the M1-D inputs dossier (specs/2026-08-27-m1d-inputs-dossier.md)
  posed 16 open questions with recommendations; controller verified the three
  sharpest claims live (expr.py's _via_obj_cmp table; i256_rem_euclid's
  Euclidean docs in the pin; the no-default storage-get host-fn gap) and
  adopted all 16.
- Execution proof (E1): D ships a dev-only wasmtime mini-host, ported BY COPY
  from spikes/spike1/harness.py (stale object bound excluded, A8), running the
  ~38 in-scope semantics cases at merge. It is NOT an oracle; F re-runs the
  same table on tier-2b (named carried obligation to F). wasmtime==48.0.0 is
  already a dev dep, so this adds no dependency.
- Runtime parts (E2/E3): parts are emitted from the SAME Python encoder as
  user code -- no WAT toolchain; the WAT spelling lives in per-part docstrings.
  This deliberately re-reads spec §6's "pre-assembled WAT" heading (design-era
  wording); rationale: one implementation of one semantics (spec §10's own
  drift rule) and S1's hand-rolled-encoder pipeline. The C-coined
  runtime_parts_needed names are ratified and then RENAMED/extended in the
  same commit series per C2's licence (zero consumers): per-width overflow
  checks, {u128,i128}_cmp (the S13 gap), box/unbox parts, tagcheck/narrow
  helpers where a check exceeds ~8 instructions (S25 break-even).
  runtime_parts_needed is documented as a hint, not a manifest; test pins
  needed ⊆ linked.
- Trailing-unreachable reconciliation (E4): after fail_with_error -- NOTHING
  (P14, on-chain-verified); a genuinely-diverging function tail gets
  fail_with_error(CODE_UNREACHABLE_GUARD) THEN unreachable; the ABI prologue
  uses CODE_ABI_CHECK_FAILED (one code, all positions) -- all three exactly as
  errors.py:17-24 documents. D's code section legitimately differs from the
  Phase 0 artifact's prologue code (0xFFFF_FFFF), which S8 already makes
  non-comparable.
- Validation (E5): the internal validator is unconditional and is the gate;
  wasm-tools runs when on PATH (exact S23 feature string as a named constant),
  skipif-never-silently-passed in tests, installed+pinned in one CI job.
- API (E6): new serpent/emitter/ subpackage exporting build_wasm(compiled) and
  build_file(path); NOT in serpent.__all__ (the authoring namespace stays
  clean); CLI naming is G's.
- Determinism (E7): byte-reproducible output is a tested guarantee
  (subprocess double-build under differing PYTHONHASHSEED; sets sorted at the
  emitter boundary; minimal-length LEB property tests). sha256(wasm) is the
  on-chain wasm_hash, so this is user-visible verifiability.
- contractmetav0 (E8): emitted by default; serpentver from
  importlib.metadata; name = the @contract class name; version only when the
  caller supplies it; structural test (S8 forbids byte goldens here).
- env-meta protocol (E9): D writes the COMPUTED FLOOR (compiled.
  declared_protocol) into build_env_meta -- the literal S6/B4 reading; the
  build target stays a frontend gate. Both numbers surface in BuildResult and
  the build line. Serpent artifacts therefore declare lower protocols than the
  Phase 0 artifact (20 vs 27 for ungated contracts) BY DESIGN.
- Memoryless (E10): D decides from its own post-lowering facts (empty pool,
  no LM host fn emitted), omitting sections 5/11 and the memory export;
  asserts C's needs_memory=False implies D agrees (the reverse may differ,
  C21).
- ConstRef/void helpers (E11): module constants inline at each use, with
  per-function memoisation into a hidden local when a POOLED ConstRef repeats
  (74-instruction host-call price, S25); internal -> None helpers are typed
  () -> () in wasm (exports stay () -> i64 per S23).
- MakeMap LM form (E12): gated on key_ty Symbol AND all-Const keys (the m.9
  descriptor contract, P1); all_static's meaning is unchanged.
- Storage get without default (E13): D emits the has-then-get guard raising
  CODE_MISSING_VALUE, and host_fns_used/_reachable gain both names via a
  frontend change in D's commit series (C2/D13 licence), with a pinning test.
- ABI prologue (E14): tag AND range checks per argument (S3's words), inline,
  shared helpers only above the ~8-instruction break-even; host-call-return
  narrowing is the same code path and is in scope.
- Emitter failures (E15): split -- user-visible facts are located SPT8xxx
  diagnostics (appended to the registry under D15's discipline); invariant
  breaks are CompilerBugError, never catchable as CompileError.
- 128-bit compare (E16): a {u128,i128}_cmp guest part (hi signed, lo
  unsigned), operands unboxed first; obj_cmp is not used for numerics.
- Controller addition: i256_div's ROUNDING DIRECTION is undocumented in the
  pin ("checked integer division" -- verified live). The 128-bit rem lowering
  must not assume truncation: D pins div's rounding by differential test
  against Python + A4 (trunc toward zero; % takes dividend's sign;
  MIN % -1 == 0) before the i256 route ships, and F re-proves on the real
  host.
- Reversal cost: per-item low before D's tasks consume them. E2's WAT
  re-reading is reversible by checking in WAT + a drift test later; E9's
  floor-vs-target is a one-line change with artifact-hash consequences.

## 2026-08-27 M1-D plan-review rulings (all findings adopted)
- Context: adversarial review of the M1-D plan — 11 blockers, 15 majors, 9
  minors, all probe-evidenced (triage record:
  .superpowers/sdd/2026-08-27-m1d-emitter/plan-review.md). Zero disputes;
  three adoptions diverge from the reviewer's offered fix or amend earlier
  rulings:
- Ruling (B1): call sites are serialized SYMBOLICALLY (Fn.code holds
  bytes | CallImport(name) | CallDefined(defidx); pass 2 resolves indices
  after the import list is frozen, imports in first-use order). Chosen over
  the reviewer's discovery-pre-pass: no double lowering, and the
  wrong-target-that-validates class becomes structurally impossible rather
  than convention-guarded.
- Ruling (B3/B11, sanctioned-edit scope): the SPT8xxx band lands with its
  FULL blast radius edited in one commit (codes.validate band range 1-8,
  _CODE_RE, registry count 96 -> 100, NO_FIXTURE_ALLOWLIST + reasons,
  _BAND_TITLES, docs/subset.md regen) — sanctioned as enumerated in the
  plan, nothing else. build_meta gains `version: str | None` (None omits the
  entry); serpentver stays serpent.__version__ with a new drift test against
  importlib.metadata (amends ruling E8's mechanism, keeps its intent).
- Ruling (M9/M13, amends E3's part inventory): NO tagcheck_struct/vec/map or
  narrow_* parts (exact-tag compares are ~5 instructions, inline);
  tagcheck_bytes_n is the one tag-check part (needs a host call); NO 32/64-bit
  neg parts (inline, with the unsigned-neg nonzero->overflow rule per M6).
  Final ratified runtime-part namespace: {u,i}64_{add,sub,mul,floordiv,mod},
  {u,i}128_{add,sub,mul,neg,cmp,floordiv,mod}, box/unbox per EITHER family,
  tagcheck_bytes_n.
- Ruling (B6, third licensed frontend edit): Address literals are lowered
  (pool strkey -> string_new_from_linear_memory -> the pinned strkey-to-
  address fn, name verified from the pin at implementation time);
  LiteralInventory gains address_strkeys and the host-fn sets learn both
  names — chosen over excluding the semantics case, which would have holed
  the S18 differential.
- Also structural: reserved --meta keys are a plain ValueError (honest-code
  discipline — SPT8004 stays emitter-coverage only); the E10 consistency
  assertion is restricted to the literal/LM component (128-bit two-result
  parts force scratch memory C cannot foresee, B8); the semantics
  differential uses a two-step wrapper (wrap_case never returns the value,
  B5) with the in-scope predicate stated in-test (35 cases today, M15).
- Why: every finding verified against the shipped code or the reviewer's
  live probes; the three top findings (B1, B4+B9+B10, B5+B6) were all of the
  validates-deploys-wrong class the process exists to catch.
- Reversal cost: per-item low pre-execution; the SPT8xxx band widening is
  append-only public API once released.

## 2026-08-28 M1-E rulings (dossier E1-E10, all recommendations adopted)
- Context: the M1-E inputs dossier (specs/2026-08-28-m1e-inputs-dossier.md)
  posed ten questions; controller adopted all ten recommendations. The
  E1 judgment call is recorded with its reasoning since the sources
  genuinely conflicted.
- E1 (what "wired end-to-end" means): reading (d) — a DELIBERATELY MINIMAL
  in-memory tier-1 Env model in src/serpent/env.py (storage, events,
  auth-recording, ledger; TTL partial per E4) plus the three narrow
  shared-code moves (durability constants, value-level storage_key,
  HostError/HostTrap out of the wasmtime module). Why: 19 shipped PUBLIC
  docstrings promise "sub-plan E" by name and 8 tests pin them; the M1-C
  carried obligation says "real tier-1 behavior"; and a plain-Python
  innermost dev loop is the natural meaning of R1's "passing". The
  against-case (env.py's "real host at test time"/"host bridge" wording,
  which is really F's tier-2b) is answered by REWRITING those docstrings in
  E to name the model honestly and point the host bridge at F. S5/S13's
  third-implementation risk is capped by: naming every non-model in code
  (no rollback, no footprint, no budget, no auth trees, no TTL clamp/trap),
  an S5-voiced disclaimer docstring, and E9's differential.
- E2 (Event.publish, five layers): mirror Rust — per-field
  Annotated[T, topic] marker, topics=/data_format= decorator args, defaults
  snake_case(ClassName) / all-fields-data / "map" (field-name Symbols,
  sorted — byte-compatible with C's P7 struct sort); events appended AFTER
  functions in spec entry order (the on-chain spike1 golden must not move);
  SPT1032 retired to NO_FIXTURE_ALLOWLIST ("the form it rejected is now
  supported"); the frontend DESUGARS Event.publish(env) into the existing
  HostCall("contract_event", (MakeTopics, MakeMap/MakeVec/value)) — the IR
  and the emitter change NOT AT ALL; token_style.py reverts to
  Transfer(...).publish(env) AND a second fixture keeps the canonical
  env.events().publish spelling (both forms supported per D5, and the
  heterogeneous-topics path keeps coverage). The four Annotated unwrap
  sites (decorators._is_contract_annotation, typemap.to_spec_type, the two
  frontend annotation resolvers) are licensed edits with pins.
- E3 (time algebra): DEFER TO M2, load-bearing — repoint expr.py's
  _TIME_ALGEBRA_NOTE and numeric.py's _NO_TIME_ARITH at M2; ship a worked
  bridge-pattern docs example; record that homogeneous Duration ops (b) are
  the cheap first M2 increment. Nothing in row E needs the algebra, and the
  heterogeneous ops would change the IR contract D froze while F builds
  concurrently.
- E4 (TTL): partial-and-honest — model live_until, never-reduce, the
  threshold guard, and expiry-on-advance (settable/advanceable sequence);
  REFUSE to model clamp/trap (the max is get_max_live_until_ledger, an M2
  host fact — a chosen constant would be a guess). The mini-host's TTL
  no-ops are NOT touched (F's). Carried to F: clamp/trap/dead-entry are
  unproven at every tier.
- E5 (escape flip): DO NOT FLIP — the tier-1 model DEEP-COPIES on set()/
  publish()/require_auth_for_args() (copy-on-write IS the host's
  serialization semantics), so note_escapes' exemption survives with its
  justification rewritten. The isolation property (mutate-after-set must
  not reach storage) is the decision procedure, stated in the plan: if it
  cannot hold for any ChainValue shape, the four-site flip becomes
  mandatory. The keyword/positional escape asymmetry in collect_never_owned
  is closed in the kept-exemption direction (kwargs to the serializing
  calls stop escaping), with both pinned tests updated.
- E6 (examples): flat examples/ with five files (counter graduates from
  sandbox_counter verbatim; errors/structs/events/allowance_token fresh),
  driven from tests/ by extending FIXTURES (one copy, tested in place),
  and examples/ added to BOTH mypy --strict files and ruff format scope
  (a non-strict-clean example falsifies the SDK's pitch).
  sandbox_hello_world stays a fixture (unique coverage). token_style and
  spike1_reauthored stay fixtures (chain-anchored).
- E7 (constructor + ambient env): a deploy(cls, env, *args) helper runs
  __init__ exactly once and models S12's error laundering (~3 lines);
  invoking an export pre-deploy is a LOUD error; Address.require_auth()
  keeps its no-Env signature via a contextvar ambient env set/cleared with
  try/finally by the deploy/invoke helpers; a stray require_auth() outside
  a frame is a loud error.
- E8 (honest boundary): env.logs() stays M2 (spec §3's layout sketch gets a
  one-line correction note, not a feature); ONE reserved-code ContractError
  hierarchy in serpent.errors (e.g. MissingValue with CODE_MISSING_VALUE)
  that the tier-1 model, the emitter's guard, and the tests all name; frame
  rollback and footprint declared out of scope with named carried
  obligations (footprint is F's row by name). The full stays-unimplemented
  list in dossier §E8 is RATIFIED, including instance-storage
  flush-at-frame-exit being unobservable in M1.
- E9 (Env differential): a second, E-owned stateful scenario table (the
  frozen 59-case table stays frozen), run against the tier-1 model and
  compiled WASM under FullHost, importable so F re-runs it on tier-2b —
  with the honest limit stated (it compares two models E/D wrote; F's
  tier-2b is where it becomes evidence).
- E10: env.py stays a module; the package promotion is recorded as M2
  cleanup, unless the model pushes it past ~600 lines mid-E.
- Also adopted: the closing-sub-plan promise sweep (19 + 2
  NotImplementedError strings, the subset.md lines, the recognize/expr
  notes, sandbox/README's stale M1-D text) as an explicit task; and the
  carried M1-D minor "wire SPT8004 at the emitter dispatch default when the
  first accepted-but-unlowered construct exists" — E2's desugar choice
  means no such construct appears, so the SPT8004 wiring stays dormant and
  DOCUMENTED as such.
- Reversal cost: per-item low before E's tasks consume them; E2's authoring
  surface becomes breaking-after-docs (D4's own note) — which is why it
  lands now, in the last cheap moment.

## 2026-08-28 Constructor-bearing contracts raise the protocol floor to 22
- Context: Elliot caught a live gap while playing — his rolodex contract
  (with __init__ → __constructor) declared protocol 20. The floor is
  computed over host-function IMPORTS (D13/E9), but the constructor is an
  EXPORT-name capability gated at protocol 22 (spec §13 / dossier S26,
  CAP-0058) and invisible to declared_protocol. Bytes declaring 20 with a
  __constructor would deploy on a 20/21 network and simply never run the
  constructor: deployable-but-uninitialized, the honest-declaration rule
  violated.
- Decision: the floor computation gains FEATURE gates alongside import
  gates. CONSTRUCTOR_MIN_PROTOCOL = 22 lives in serpent._host._protocol
  with the S26 citation; compile_module's resolved protocol is
  max(import_floor, 22) when the module declares a constructor; an
  explicit target_protocol below 22 with a constructor is a located
  SPT6001 at the __init__ definition naming the gate. Pins updated:
  constructor-bearing fixtures/examples now declare 22; constructor-less
  ones (counter, spike1_reauthored — whose on-chain env-meta anchor is
  unchanged) stay at the import floor.
- Why: S6's "declared protocol is computed, never hand-set" only stays
  honest if the computation sees every gated capability the module uses,
  not just its imports. This is the first non-import gate; the mechanism
  is named so future ones (if any) extend it rather than re-deriving.
- Reversal cost: none — raising a floor is the safe direction; the change
  is test-pinned in both directions.

## 2026-08-28 M1-E plan-review rulings (all findings adopted)
- Context: adversarial review of the M1-E plan — 7 blockers, 8 majors, 10
  minors, all probe-evidenced (triage record:
  .superpowers/sdd/2026-08-28-m1e-env-runtime/plan-review.md). Zero
  disputes. The blockers were all real traps: Annotated unimportable inside
  a contract (loader restricts to serpent.__all__ → re-export topic AND
  Annotated); get_type_hints strips Annotated without include_extras=True
  (→ the license SHRINKS to one seam, decorators._build_record, storing
  stripped annotations); positional event construction doesn't compile
  (kwargs-only revert spelling); env→types→address→env circular import
  (→ leaf serpent/_frame.py); the "map" data node is MakeStruct, not
  MakeMap (runtime values force the chain form otherwise — and MakeStruct
  feeds the descriptor inventory for free); the get ty-check is TAG-level
  mirroring the emitter's abi_check (Bytes family one tag, struct↔Map one
  tag, element types unchecked); storage_key's Map branch normalizes ITEMS
  (keys AND values), and Struct normalizes identically to its equivalent
  Map.
- Also adopted: token_style's Transfer gains the convention reproducing
  its CURRENT event shape (topic-marked from_/to, single-value amount) so
  the both-spellings golden is provable; the printer's FIXTURE_NAMES and
  the harness inventory _FIXTURES are separate lists Tasks 7/8 must edit;
  tier-1 ledger defaults pinned to the harness constants via one shared
  home; auth-args differential scenarios are tier-1-only (the harness
  discards args); TTL live_until=None semantics + absent-key extend_ttl
  raises (the one dead-entry rule a fixed-sequence model owns); the
  promise-sweep census corrected to 38 mentions/9 files with typemap/ir
  text edits inside the stale-string sanction; examples load via
  importlib-from-path; the event-name cap is SCSymbol's 32 (not 9), >9
  prefix topics pool via linear memory.
- Positive evidence recorded: the E5 isolation property PROVABLY HOLDS
  under deepcopy for every ChainValue shape probed — the escape flip's
  four-site edit list is dead barring a Task 2 surprise.
- Reversal cost: per-item low pre-execution.

## 2026-08-31 Tagged unions / int enums get a late-M1 addendum sub-plan
- Context: Elliot caught (2026-08-28, journal-captured) that tagged unions
  and int enums have no authoring surface and no sub-plan schedule, despite
  spec §2's conventions (union → Vec led by variant Symbol; int enum → u32)
  and §11's M1 scope sentence naming "structs/unions/enums" explicitly. The
  M1-E ledger recorded that a controller decision was owed: late-M1 addendum
  vs an explicit M2 deferral.
- Decision: a late-M1 addendum sub-plan (working name M1-E2, dossier → plan
  → review → SDD, the standard loop) scheduled after the M1-E merge and
  before M1's closing deployment. Until it lands, the documented workaround
  stays per-variant @contracttype keys + Symbol constants (token_style's
  shape).
- Why: the spec is the binding authority and its M1 sentence is explicit; a
  deferral would ship M1 incomplete against its own scope line and require
  amending the spec instead. Sizing note: the surface touches decorators,
  typemap, spec sections (union/enum UDT entries), frontend lowering, and
  the emitter's descriptor inventory — a real sub-plan, not a rider on E/F.
- Reversal cost: schedule-only today (nothing is built); Elliot can
  downgrade it to an M2 deferral by striking this entry and adding the spec
  amendment note.

## 2026-08-31 M1-E final-review rulings (fix wave)
- Context: the Fable whole-branch review returned 0 Critical / 4 Important;
  two of the fixes graze settled rulings, so the calls are recorded here.
- Tier-1 `get` ADOPTS a non-ChainValue default through the requested `ty`
  (mirroring M1-C literal adoption): `get(k, U32, default=0)` now answers
  `U32(0)` at tier 1, matching the compiled IfExp's adopted literal. E5's
  "default returns un-copied" is grazed, not reversed — adopting a raw
  literal is not copying a caller's chain value; a chain-value default
  still passes through un-copied and identity-pinned. The review proved
  the old behavior was a silent cross-tier type divergence (raw `0` vs
  `U32(0)`, equality-invisible).
- Escape analysis: the construction kwargs of a DIRECTLY PUBLISHED event
  (`MyEvent(x=v).publish(env)`) join the serializing-call exemption in
  `collect_never_owned` — the two publish spellings now share one escape
  rule, which E2's one-convention position requires and note_escapes'
  docstring already claimed. Accepts strictly grow.
- The allowance-token example's `mint` enforces the STORED admin (reads
  it back and auths against it, parameter dropped) — the shipped example
  documented an auth check it did not perform.
- Ratified retroactively: loader's acceptance of the `@contractevent(...)`
  Call form (44dea69, Task 6 fix round) as a licensed deviation — the
  sanctioned factory spelling required it; test-pinned.
- Parked with reasons (the review's triage, recorded in the M1-E ledger):
  the SPT3019 relax-to-32 pass and the method-parameter `topic` refusal
  both feed M1-E2's dossier; `from_` aliasing is a G/M2 docs item;
  env.py at ~1,550 lines fires E10's package-promotion trigger for M2.
- Reversal cost: the `get` adoption is a behavior change on a 3-day-old
  surface, trivially revertable; the escape exemption only widens accepts;
  the mint change is example-local.

## 2026-08-31 M1-E2 rulings (dossier E1-E13, all recommendations adopted)
- Context: the M1-E2 inputs dossier (specs/2026-08-31-m1e2-inputs-dossier.md)
  posed thirteen questions with recommendations; controller adopted all
  thirteen. The dossier's one Rust-source-only foundation (§B.1: unit
  variant = ONE-ELEMENT ScVec [Symbol], tuple payload follows in
  declaration order; int enum = bare U32; the union/enum spec-entry
  shapes) was upgraded to byte-verified ground truth before ruling: a
  real `#[contracttype]` build, ScVal debug prints and decoded
  contractspecv0, identical across soroban-sdk 22.0.11 and 27.0.6
  (scratch crate retained for audit this session).
- E1 (authoring surface): descriptor-typed factories — `Empty = variant()`,
  `Circle = variant(U32)`, `Red = enumvalue(0)` — with exported
  ContractUnion/ContractEnum bases; the ONLY probed candidate both
  mypy-strict-clean and compilable. serpent.__all__ grows by the
  sanctioned surface names (up to five), test_public_api.py updated in
  the same commit.
- E2 (read surface): `tag() -> Symbol` + `payload(index, ty)` on the base
  (Q12's pass-ty-explicitly convention). Sub-rulings: payload index is
  0-based over the PAYLOAD (not the underlying Vec); statically decidable
  payload() misuse (index above the union's max arity, ty matching no
  variant's slot) is a compile reject.
- E3/E4 (IR): ONE new MakeUnion node dispatched to the existing
  _lower_make_vec (MakeVec.elem_ty is consumed as truth by the for-in
  desugar, so reuse would lie); TyTag.UNION + TyTag.ENUM with ABI rows
  UNION→TAG_VEC_OBJECT, ENUM→TAG_U32; int enums ride Const's U32 path —
  zero emitter change for enums, one dispatch line + two derived-set rows
  for unions. SPT8004 stays dormant only if the node joins lower.py's
  dispatch in the same task that adds it to ir.py (stated in the plan).
- E5 (int enums): mandatory explicit `enumvalue(N)` discriminants (Rust
  parity; implicit numbering would invent on-chain values that reorder
  silently) + an exported base (a base-less value is not statically a
  ChainValue). No `.value` introspection in M1 (additive to relax).
- E6 (shapes): unit + single-payload + multi-payload tuple variants IN,
  arity capped at 12 (S4's tuple cap — one arity story). Refused at
  declaration: 0-element tuple variants, named-field variants (Rust
  refuses both), empty union bodies.
- E7 (spec entries): XDR kind order — structs, unions, int enums, error
  enums, functions, events; the on-chain golden constrains nothing here
  (it declares neither kind). Unions/enums travel in the existing
  `types=` inventory, no new keyword.
- E8 (name caps): union variant names cap at 32 (the name BECOMES a
  runtime Symbol; Rust caps identically), int-enum case names at 60 (XDR;
  never a Symbol). SANCTIONED registry edit: SPT5003's wording widens
  from "@contracterror case name" to every UDT case name with per-kind
  limits in the message (same check, no new code).
- E9 (tier-1 representation): a union instance holds an immutable Vec
  internally and is NOT a dataclass — a dataclass union would silently
  match the Struct Protocol and classify as a Map (wrong family, key, and
  ABI tag, no error). Unions are hashable storage keys but NOT orderable
  at tier 1 (multi-entry Map[union, V] uses D10's "not modelled in tier
  1" wording). Int-enum instances order/hash exactly like U32.
- E10 (topic-marker refusal, fed item): refuse the two SILENT positions
  (method parameter, method return) at decorator time; re-code the
  struct-field refusal off SPT1037 onto an honest code with its bridge
  row + fixture; the function-body case stays on SPT3013. The
  include_extras=True read in _check_method is a NAMED second Annotated
  seam (D5's stripped-annotation property preserved and stated).
- E11 (SPT3019, fed item): DROP the length arm entirely — Symbol's own
  32-char enforcement (frozen semantics case + constructor) makes any
  length check dead code; the code's meaning NARROWS to "topics[0] is
  not a Symbol" (D7-compliant; accepts strictly grow). Rider adopted:
  rename the events example's Tally back to a descriptive name in its
  own commit and rewrite the now-stale cap-asymmetry prose in
  examples/events.py in the same pass.
- E12 (get overloads, rider): the three-overload get lands (raw-literal
  arm FIRST — probe-verified the order is load-bearing), WITH a
  positional-default arm added so today's positional spelling keeps
  compiling (no accepts-shrink).
- E13 (footprint): a sixth example (examples/shapes.py or similar)
  joining all seven inventories as a NAMED task (the inventories do not
  fail loudly), plus must_reject fixtures per new code, plus
  env_surface/env_scenarios rows so the new value kinds ride the E9
  stateful differential that F re-runs on tier-2b.
- Reversal cost: per-item low before the plan's tasks consume them; the
  E8/E11 registry edits are the usual append-only-discipline sanctions;
  every scope restriction in the dossier's §D table is additive to relax.

## 2026-08-31 M1-E2 plan-review rulings (all findings adopted)
- Context: adversarial review of the M1-E2 plan — 5 blockers, 8 majors, 12
  minors, all probe-evidenced (triage record:
  .superpowers/sdd/2026-08-31-m1e2-unions/plan-review.md). Zero disputes;
  plan v2 integrates every fix. The structural adoptions:
- Case-name validation routes through `limits.py`, not the decorator: the
  kind gate widens to union/int-enum, `_check_cases` gains per-kind
  limits (32 via SCSYMBOL_LIMIT for union variants, 60 for int-enum
  cases), and the decorator CEDES case-name checking (its 30-cap SPT5001
  route was an accepts-shrink vs ruling E8). SPT5002 re-attributed to
  `limits._check_type_name` — both sanctioned widenings now have a
  reachable emission path, which the plan's v1 route lacked.
- Deliberate-error typing fixtures get NO new excluded directory: positive
  halves are compilable tests/fixtures/ modules under the normal gates;
  negatives run through a tmp_path mypy subprocess helper with a smoke
  test so a no-op invocation cannot pass (the M1-C E15
  excluded-directory precedent explicitly does not apply).
- The stale-promise gate's (path, line) allowlist is a named blast-radius
  hazard: every task that grows codes.py or the pinned test files carries
  the allowlist-line update in its own commit.
- Two plan-author corrections to the rulings' letter RATIFIED: the
  emitter's Const dispatch widens at BOTH U32-tag sites (`_lower_const`
  AND `_static_word`, with a needs_memory honesty pin) — E3's "zero
  emitter change for enums" was the dossier's overstatement; and
  MakeUnion carries its variant-name Symbol as a frontend-built Const
  inside `items` so ir.walk's reflective traversal reaches the literal
  pool (>9-char variant names must intern).
- Reversal cost: per-item low pre-execution; the limits.py routing is the
  one with public-diagnostic consequences and it lands with fixtures
  pinning both kinds' messages.
