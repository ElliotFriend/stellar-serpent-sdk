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

## 2026-08-27 M1-C final-review minors folded into parked passes
- Minors 2 and 3 from the final whole-branch review (registry intent strings
  hardcode limit numbers; frontend.py imports _host._protocol via the private
  path) are folded into the already-parked sanctioned wording/cleanup passes
  (see the M1-C attention file §8-9). Minor 4 (runtime_parts ratification
  caveat) fixed in-code at merge time; Minor 1 is the entry above.
