# M1-C: Compiler Frontend Implementation Plan (v2, post-adversarial-review)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The serpent compiler frontend: Python contract source → resolved, typed
IR (consumable by sub-plan D's emitter), with a structured diagnostics engine,
the executable `must_reject/` subset specification, and the differential checks
that keep the frontend honest against the tier-1 oracle.

**Architecture:** HYBRID frontend (ruling E1): the contract module is imported for
declarations/annotations, method bodies compile from the AST, and a mandatory
cross-check asserts the two views agree. One thin `HostCall` IR node carries every
host operation by binding NAME (dossier §C.1). Collect-all diagnostics with stable
`SPT####` codes. This v2 integrates every finding of the plan's adversarial review
(BL-1..4, MJ-1..16, all minors — triage in decisions.md 2026-08-27).

**Spec:** `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`
(§2, §4, §11). **Dossier (citation target for all S/R/D/A/B/P/T/E IDs):**
`docs/superpowers/specs/2026-08-27-m1c-inputs-dossier.md`. **Rulings:**
`docs/superpowers/decisions.md` entries of 2026-08-27 (all E1–E20 adopted, plus
the plan-review rulings: Bytes.slice tier-1 addition, len() scoping, Map
struct-value runtime widening).

## Global Constraints

- Layout: `src/serpent/compiler/` — `diagnostics.py`, `codes.py`, `ctx.py`,
  `loader.py`, `types_.py`, `ir.py`, `expr.py`, `stmt.py`, `recognize.py`,
  `limits.py`, `frontend.py`, `_render_docs.py`. Public:
  `from serpent.compiler import compile_module, compile_expression,
  CompileError, Diagnostic`.
- `serpent/compiler/` stays INSIDE the zero-dep walk (MJ-6): it reaches
  `stellar_sdk` only transitively through `serpent.spec` and must never import
  it directly — the existing `test_core_zero_dep.py` enforces that for free;
  do NOT edit its exemption.
- Dossier §B subset lines BINDING; AST-allowlist property test enforces the
  closed set; **exhaustive dispatch** (MJ-11): the default branch emits SPT1xxx
  from a `NODE_KIND_CODES` table covering every §B REJECT row — an unconsidered
  node is a clean diagnostic, never a traceback.
- Diagnostics per §D.2: `Diagnostic(code, loc, message, help, notes)`; `Loc`
  full-span, `WHOLE_FILE` for module-level facts (P2); collect-all per method
  (E16); errors reported via the `Diagnostics` SINK everywhere — no
  `X | Diagnostic` return unions (minor 13); resolvers return `Ty | None`
  with the sink carrying the error.
- SPT codes: stable public API (E17); the COMPLETE registry is Task 1's primary
  deliverable (BL-4); every SPT1xxx carries non-empty `help` (F.2.11); bands
  with no source-level trigger live on an explicit `NO_FIXTURE_ALLOWLIST` in
  codes.py (BL-1c) that meta-test B consults.
- Divergence guards are law: Symbol comparisons ALWAYS via obj_cmp (F.1.2); NO
  arithmetic constant folding (F.1.10); exact width in `Ty` (F.1.11); struct
  fields sorted ascending-as-byte-strings by C (P7); `MakeMap(all_static)` keys
  pre-sorted in rank/val_cmp order, falling back to map_new+map_put when C
  cannot totally order them (MJ-15); container mutation only on unaliased
  C-owned LOCALS — a temporary receiver (e.g. `Vec(U32).pop_back()`) is a
  reject (E11/BL-3); truthiness numeric+Bool only (E10); `True`/`False`
  literals coerce to Bool in condition/Bool-argument position, so `while True:`
  is supported (MJ-12); `Error` never a value/return (S8); `len()` scoped to
  Vec/Map/Bytes — `len(Symbol)`/`len(String)` are SPT3xxx rejects (MJ-1).
- Frontend outputs per §C.2: `host_fns_used`, `needs_memory` + literal
  inventory, `runtime_parts_needed`, `spec_inputs` where
  `declared_types_in_order` carries STRUCTS + ERROR ENUMS ONLY (events tracked
  separately as `events: [EventDecl]` — sections refuses event classes, MJ-9),
  `declared_protocol()`. `compile_module(source, path, *,
  target_protocol: int | None = None)` (BL-1a).
- must_reject: FIXTURES ONLY under `tests/must_reject/` (no `__init__.py`, no
  `test_*.py`); the runner lives at `tests/unit/test_must_reject.py` and globs
  the fixture tree (BL-2). Fixtures compiled AS TEXT via compile_module — but
  note (minor 12) the hybrid loader execs the module, so fixtures must be
  exec-safe; decorator errors are EXPECTED to fire in the exec step and are
  bridged there. `# serpent:at` anchors on a `# HERE` marker comment, not an
  absolute line (MJ-14). Config: mypy exclude + ruff ALL-ignore + format
  exclude for `tests/must_reject/` only, each with a comment citing E15/BL-2.
- Keyword arguments accepted only where the recognition table names the
  parameter and in `@contracttype`/event construction; anywhere else SPT1xxx
  (minor 8).
- Gates at every commit: full suite, mypy --strict, ruff check, ruff format
  --check src tests. TDD RED/GREEN. Conventional commits, no emojis, explicit
  paths, trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`;
  signing fallback (+ `.git/unsigned-commits.log`).
- Spikes frozen (R5); decisions.md controller-owned; tier-1 oracle files CLOSED
  except the ruled edits landing in Task 8: E2 bound widening, Map struct-VALUE
  runtime support (MJ-7 ruling), Bytes.slice (MJ-1 ruling), E14 registry codes
  (Task 1), and the E12 fixture line deletion (MJ-8).
- "496 pre-existing tier-1 tests (as of main)" is the baseline count; it grows
  with every task (minor 1).

---

### Task 1: Diagnostics core + COMPLETE code registry + reserved runtime codes

**Files:**
- Create: `src/serpent/compiler/__init__.py`, `diagnostics.py`, `codes.py`
- Modify: `src/serpent/errors.py` — E14 COMPLETE (MJ-2): `CODE_MISSING_VALUE =
  0xFFFF_FFFD`, `CODE_UNREACHABLE_GUARD = 0xFFFF_FFFC`, `CODE_ABI_CHECK_FAILED
  = 0xFFFF_FFFB` (ONE code for all argument positions — position is a message
  concern), `CODE_UNSUPPORTED_AT_RUNTIME = 0xFFFF_FFFA`; documented table.
- Test: `tests/unit/test_diagnostics.py`

**Interfaces:**
- `Loc`/`LocKind`/`Diagnostic`/`Diagnostics`/`CompileError` per §D.2 (sink
  convention binding, minor 13; `Diagnostics.error` enforces SPT1xxx-has-help).
- `codes.py`: **the complete registry table — the primary deliverable and the
  review gate for this task** (BL-4): one row per §B.1/§B.2 REJECT line, per
  §B.3 declaration check, per §C.4 "not in M1" surface, per §C.3 scope/flow
  rule — `CODE = ("SPT####", band, construct, message-intent, owning-task)`;
  ~55 rows (§F.3's count, not "≥40"). Plus `NO_FIXTURE_ALLOWLIST:
  frozenset[str]` (codes provably without a source-level fixture trigger,
  each with a reason string — initially the SPT6xxx low-target-only gate code)
  and `codes.validate()`.

- [ ] **Step 1: failing tests** — render goldens (NODE + WHOLE_FILE); sink
  ordering + raise_if_any; registry validation (unique, band-prefixed, owning
  task named, allowlist ⊆ registry); help-required rule; the four reserved
  codes present/unique/in-band.
- [ ] **Steps 2-5: RED → implement → GREEN + gates → commit**
  (`feat: add compiler diagnostics core with the complete code registry`).

---

### Task 2: must_reject scaffold (runner OUTSIDE the fixture tree)

**Files:**
- Create: `tests/unit/test_must_reject.py` (the runner + meta-tests — BL-2),
  `tests/must_reject/README.md`, 15 seed fixtures: 12 `constructs/`
  (comprehension, f-string, try/except, nested def, lambda, async, global,
  with, match, assert, walrus, chained compare) + 2 `shape/` (bare-int error
  member, missing param annotation) + 1 `types/` (cross-width add) (minor 5)
- Modify: `pyproject.toml` (mypy exclude `tests/must_reject`; ruff
  `"tests/must_reject/**" = ["ALL"]`; format exclude — commented E15/BL-2).
  NO ci.yml change (minor 6).
- Test: the runner's own meta-tests

**Interfaces:**
- Fixture header per §D.3 + the `# HERE` anchor convention (MJ-14):
  `# serpent:at HERE` resolves to the line of the first `# HERE` comment.
- Runner: reads fixtures AS TEXT → lazy-imports `compile_module` → whole-module
  `pytest.skip("compile_module lands in Task 10")` until then; asserts exactly
  one MATCHING diagnostic (code + HERE-line + message substring). Meta-test A
  (declared codes ∈ registry) live now; meta-test B (every non-allowlisted
  registry code has ≥1 fixture) `xfail(strict=False)` until Task 11b.

- [ ] **Steps 1-5** as Task-2-v1, relocated per BL-2
  (`feat: add executable must_reject subset spec scaffold`).

---

### Task 3: Module loader (hybrid) + statement-wise exec bridging

**Files:**
- Create: `src/serpent/compiler/loader.py`
- Test: `tests/unit/test_loader.py`

**Interfaces:** as v1 Task 3, with two review corrections:
- Bridging catches `(ValueError, TypeError, NameError)` (MJ-4 — decorators.py
  raises TypeError for non-int errorcode args; NameError precedes conversion).
- **Exec one top-level AST statement at a time** (`compile(ast.Module([stmt],
  []), path, "exec")`), so every exec-time exception already carries the exact
  failing statement node; name-matching only disambiguates within a class body
  (MJ-4). Fallback WHOLE_FILE diagnostic if unmatched; never a raw traceback.
- Inventory cross-check (F.1.14): `_serpent_type_` vs AST — internal hard
  failure on mismatch.

- [ ] **Step 1: failing tests** — the FULL §B.3 bridging matrix (every
  decorators.py check listed in the dossier, each asserting a LOCATED
  diagnostic on the right statement); module-docstring skip (P1); module-const
  acceptance (P5); non-serpent import reject; the TypeError case
  (`errorcode("7")`) specifically; cross-check mutation test.
- [ ] **Steps 2-5** (`feat: add hybrid module loader with statement-wise error bridging`).

---

### Task 4: FuncCtx + the Ty model + annotation resolution

**Files:**
- Create: `src/serpent/compiler/ctx.py` (MJ-10 — defined HERE, consumed by 5-9),
  `src/serpent/compiler/types_.py`
- Test: `tests/unit/test_compiler_types.py`

**Interfaces:**
- `FuncCtx` (MJ-10, field by field): `loaded: LoadedModule`, `sink:
  Diagnostics`, `params: list[(name, Ty, Loc)]` (self/env dropped),
  `locals: SlotTable` (slot, name, Ty, definitely_assigned: bool),
  `loop_depth: int`, `return_ty: Ty`, `alias_sets: AliasTable` (E11 state:
  which container locals are C-owned vs aliased), `fn_name`, `path`.
- `Ty` per §C.2 with `.repr_form`/`.scval_rank`/`.wasm_arith_width`/`render()`;
  `Ty.Invalid` sentinel for sink-reported failures (minor 13).
- `resolve_annotation(obj, ctx_or_loaded, loc, sink) -> Ty | None` — hybrid
  object resolution incl. `bytes_n(N)` via `_LENGTH` (E20/B8); pre-empts every
  typemap unmappable with SPT3xxx reusing typemap's refusal text (B7);
  `Error`-as-return (S8); Env leading-param-only.

- [ ] **Step 1: failing tests** — mapping rows + unmappable matrix (LOCATED
  codes); rank/repr/width goldens; FuncCtx construction + slot-table basics.
- [ ] **Steps 2-5** (`feat: add compiler context and type model`).

---

### Task 5: IR nodes + scalar expression checking

**Files:**
- Create: `src/serpent/compiler/ir.py` (ALL §C.2 nodes, statements included),
  `src/serpent/compiler/expr.py` (scalar part)
- Test: `tests/unit/test_expr_scalars.py`

**Interfaces:** as v1 Task 5 plus review corrections:
- **Exhaustive dispatch + NODE_KIND_CODES default** (MJ-11), tested with a
  synthetic unsupported node.
- `True`/`False` literal → `Bool` coercion in condition/Bool-arg position;
  `x = True` without annotation context → reject (MJ-12; both tested).
- `len()` scoped Vec/Map/Bytes → U32; `len(Symbol)`/`len(String)` → SPT3xxx
  (MJ-1 ruling).
- Subscript is NOT handled here — owned by Task 7b (MJ-13); the dispatch
  routes it there (or rejects annotation-position leaks with the right code).
- Everything else per v1: literal coercion (no folding F.1.10), constructors
  (literal + runtime P4), arithmetic per A4 (omissions named A5/D2; `/` →
  "use //"; bool-operand reject T2), comparisons (via_obj_cmp for Symbol/host
  objects F.1.2; chained/is/in reject; raw-literal eq reject E13), BoolOp
  Bool-only (E9), IfExp same-type, truthiness numeric+Bool (E10) via IsZero,
  bool(), rejected builtins named.

- [ ] **Step 1: failing tests** — table-driven §B.2 scalar rows (SUPPORT → IR
  shape asserted; REJECT → code+substring+help); T1's 17 reject-case sources
  verbatim (minor 3 — seventeen, not twenty); obj_cmp-for-Symbol IR pin;
  exhaustive-dispatch synthetic-node test.
- [ ] **Steps 2-5** (`feat: add IR and scalar expression checking`).

---

### Task 6: Statements + flow analysis

As v1 Task 6, with: `while True:` SUPPORT via the Task 5 coercion rule and the
explicit termination rule ("`while True:` with no `break` satisfies
definite-return when every exit path returns/raises"); desugaring goldens; the
full §B.1 reject matrix. Files/tests as v1.
(`feat: add statement checking with definite-return flow analysis`)

---

### Task 7a: Env-API recognition table

**Files:**
- Create: `src/serpent/compiler/recognize.py` (env/storage/auth/events only)
- Test: `tests/unit/test_recognize_env.py`

**Interfaces:**
- **C authors the Python-surface → host-fn mapping table itself** (MJ-3):
  `RECOGNIZED: dict[surface-shape, HostCallSpec]` for the env.py surface:
  storage buckets (get/get-default/set/has/del_/extend_ttl keyed+keyless),
  ledger (timestamp→U64, sequence→U32), events().publish (MakeTopics,
  topic[0]-Symbol S11), require_auth/require_auth_for_args,
  Event.publish(env) → SPT reject pointing at sub-plan E (E12).
  §C.4's inventories are the ALLOWED TARGET SET: a completeness assertion
  checks every table target exists in `_host.functions_by_name` and no
  unlisted host fn is referenced (MJ-3).
- M2/future surfaces (minor 11): a `KNOWN_FUTURE_ENV_NAMES` set (logs, call,
  try_call, crypto, prng, current_contract_address, …) recognized as known
  names on Env → SPT1xxx with the M2 pointer; unknown attributes → SPT2xxx
  unresolved. `storage…has() -> Bool` vs `Map.has() -> bool` typed precisely
  (minor 9 — row in the test matrix).

- [ ] **Step 1: failing tests** — every RECOGNIZED row (fn_name + arg IR
  shapes incl. StorageType immediates B6, get-default → has/If/get lowering);
  future-name vs unknown-name split; completeness assertion.
- [ ] **Steps 2-5** (`feat: add env API recognition table`).

---

### Task 7b: Containers, structs, subscripts, alias analysis

**Files:**
- Modify: `src/serpent/compiler/recognize.py` (container/struct rows),
  `expr.py` (Subscript ownership — MJ-13)
- Test: `tests/unit/test_containers_frontend.py`, `tests/unit/test_alias.py`

**Interfaces:**
- Container construction (explicit-type forms D2/A13; displays only in those
  positions; all_static detection); struct construction kwargs-only, C-owned
  field sort (P7); MakeMap literal-key ordering per MJ-15 (rank/val_cmp
  pre-sort, map_new+map_put fallback for un-orderable keys — struct keys E3);
  field reads; the container method table (mutators + readers) authored here
  per MJ-3, mapping the REAL API (`Vec`: push_back/push_front/pop_back/
  pop_front/get/put/del_/insert/append/slice/first_index_of; `Map`:
  set/get/has/del_/keys/values/key_by_pos/val_by_pos) — no rows for
  vec_front/vec_back/vec_last_index_of (no authoring surface; unreachable
  targets stay in the allowed set only).
- Subscript (MJ-13, all four cases): `Bytes[i]` → bytes_get→U32;
  `Bytes[a:b]`/`Vec[a:b]` → reject pointing at `.slice(lo, hi)` (Bytes.slice
  exists as of Task 8 — this task's tests may mark that row xfail until Task 8
  lands, then flip; ledger the ordering); annotation-position generics → Task 4
  path; negative LITERAL index → reject (D6).
- **Alias analysis** (E11/BL-3): mutation legal only on an unaliased local the
  binding of which C owns — params, field-get results, subscripts, aliased
  locals, AND TEMPORARY RECEIVERS (`Vec(U32).pop_back()`) are rejects with the
  functional-host-op explanation + rebind rewrite in help. Standalone pass,
  table-tested.

- [ ] **Step 1: failing tests** — every authored row; alias matrix incl. the
  temporary-receiver case; struct field-sort golden; Vec(Bytes32) lookup
  asymmetry preserved (F.1.8); heterogeneous-literal-key fallback case.
- [ ] **Steps 2-5** (`feat: add container recognition and alias analysis`).

---

### Task 8: Declarations, internal calls, and the ruled tier-1 edits

**Files:**
- Modify: `src/serpent/types/containers.py` + `types/_ordering.py` (E2 bound
  widening; MJ-7 ruling: `require_chain_value` widened on the Map VALUE path
  only — keys stay per E3 — with a DIRECT `Map(Symbol, Settings)` round-trip
  test, not suite-reliance), `src/serpent/types/buffers.py` (MJ-1 ruling:
  `Bytes.slice(lo, hi) -> Bytes` matching Vec.slice semantics + tests; slicing
  sugar `[a:b]` unchanged), `tests/fixtures/token_style.py` (MJ-8 precise fix:
  DELETE the `Transfer(...).publish(env)` line — the canonical
  `env.events().publish(...)` line above it stands; update the module
  docstring bullet; `Transfer` stays declared), `tests/unit/test_typemap.py`
  (in Files list per minor 2: remove the now-unneeded `type: ignore[type-var]`
  — mandatory under warn_unused_ignores)
- Create: `src/serpent/compiler/decls.py`
- Test: `tests/unit/test_decls.py`

**Interfaces:** module consts (P5), helpers + private methods as InternalCall
(E8) with call-graph cycle rejection, decorated-types inventory = STRUCTS +
ERROR ENUMS in declaration order with events SEPARATE (MJ-9, B9/B10).

- [ ] **Step 1: failing tests** — as v1 Task 8 plus: Map struct-value
  round-trip; Bytes.slice unit tests; `build_spec_entries(cls,
  types=inventory)` succeeds on token_style (MJ-9 pin); fixture still
  mypy-clean + full pre-existing suite green.
- [ ] **Steps 2-5** (`feat: add declaration checking and ruled tier-1 edits`).

---

### Task 9: Limits validation (the SPT5xxx band)

**Files:**
- Create: `src/serpent/compiler/limits.py`
- Test: `tests/unit/test_limits.py`, `tests/must_reject/limits/` fixtures

**Interfaces (MJ-5):** `validate_limits(loaded, sink)` re-implements every
sections/typemap limit pre-emptively against AST nodes, IMPORTING the constants
(`decorators.NAME_LIMIT`, `sections.TYPE_NAME_LIMIT`, `CASE_NAME_LIMIT`,
`DOC_LIMIT`) never restating them: doc ≤ 1024 encoded bytes (B12), type/case
names ≤ 60, function/field/param names ≤ 30 + Symbol charset (D10), **the
`__constructor` name + every parameter name that decorators never check
(B11)**, export param count ≤ 32 (S23). Each → located SPT5xxx.

- [ ] **Steps 1-5** with the boundary matrix (1024/1025 encoded bytes incl.
  multibyte; 30/31; 60/61; 32/33 params)
  (`feat: add pre-emptive spec-limit validation`).

---

### Task 10: `compile_module` assembly + outputs + fixture reconciliation

**Files:**
- Create: `src/serpent/compiler/frontend.py`
- Modify: `tests/unit/test_must_reject.py` (un-skip)
- Test: `tests/unit/test_frontend.py`

**Interfaces:** as v1 Task 9 plus BL-1: `compile_module(source, path, *,
target_protocol: int | None = None)`, threaded into `declared_protocol`.
SPT6xxx reachability, resolved (supersedes BL-1b's l.f suggestion, since C
only ever emits the ungated v1 TTL form l.7): today NO C-emitted host fn
carries a protocol gate above the base, so no real source can trip the band —
SPT6xxx therefore goes on `NO_FIXTURE_ALLOWLIST` with the reason string "no
gated authoring surface at M1-C; band wired end-to-end via a synthetic-
bindings unit test", and the unit test monkeypatches a fake gated HostFn into
the used-set path to prove the ProtocolGateError → SPT6xxx mapping (offender
naming included) end-to-end. The band exists for the M2 surfaces that ARE
gated (crypto p22+, delegated auth p27).
Seed-fixture reconciliation per MJ-14 (fix diagnostics unless the fixture is
provably wrong; ledger each).

- [ ] **Step 1: failing tests** — token_style compiles end-to-end (post-MJ-8);
  outputs asserted exactly (host_fns_used/needs_memory/spec_inputs with events
  separate); the synthetic-gate SPT6xxx test; runner live, all 15 seeds pass.
- [ ] **Steps 2-5** (`feat: assemble compile_module with emitter-facing outputs`).

---

### Task 11a: Semantics-table classification + allowlist property

**Files:**
- Modify: `tests/semantics/cases.py` (BL-3: add `frontend:
  Literal["accepts","rejects","not_expressible"]` field, set case-by-case;
  the three review-named cases marked `not_expressible` with one-line reasons:
  heterogeneous-key Map literals ×2, temporary-receiver mutation ×1),
  `tests/semantics/test_semantics.py` (REPLACE the three placeholder regexes
  with compile_expression-based classification, T3)
- Test: `tests/unit/test_frontend_semantics.py`

**Interfaces:** every `frontend="rejects"` case → located reject; every
`frontend="accepts"` case → compile_expression succeeds with IR type == the
OPERAND type (contract_error/trap cases assert compile+type only, BL-3);
`not_expressible` documented; tier1_only ⟺ rejects both directions (F.2.2);
AST-allowlist property over every accepted source (F.2.4).

- [ ] **Steps 1-5** (`test: replace semantics placeholders with frontend classification`).

---

### Task 11b: Fixture completion + bridging completeness

**Files:**
- Create: remaining `tests/must_reject/` fixtures (every non-allowlisted
  registry code; every §B REJECT row; every T1 case)
- Modify: `tests/unit/test_must_reject.py` (meta-test B → hard, consulting
  NO_FIXTURE_ALLOWLIST)
- Test: bridging completeness (F.2.12 — every §B.3 check has a located
  diagnostic test), diagnostics-quality sweep (F.2.11)

- [ ] **Steps 1-5** (`test: complete the must_reject subset specification`).

---

### Task 11c: Fuzz, goldens, cross-checks

**Files:**
- Create: `tests/unit/test_frontend_fuzz.py`, `tests/unit/test_frontend_goldens.py`,
  a fresh spike re-author fixture (spike untouched, R5)
- Test: as listed

**Interfaces:** robustness fuzz (F.2.5 — Hypothesis grammar + mangled-fixture
corpus; always CompileError-with-Loc); Phase 0 host-fn golden (F.2.9 — assert
against the eight names copied from `spikes/spike1/ACCEPTANCE.md`/`harness.py`
as the recorded source of truth, never parsing spike.wasm); golden IR snapshots
(F.2.10, regeneration-flagged); spec-view cross-check (F.2.7); host-fn ↔
protocol cross-check (F.2.6 — declared_protocol equals the build's declaration
for each example; minor 4's row properly homed here).

- [ ] **Steps 1-5** (`test: land frontend fuzz, goldens, and cross-checks`).

---

### Task 12: Subset docs generation

As v1 Task 11 with minor 7's correction: ALL logic in
`src/serpent/compiler/_render_docs.py` (typed, gated); `docs/gen_subset.py` is
a ≤5-line `python -m serpent.compiler._render_docs` shim the drift test never
imports; generated `docs/subset.md` committed with header + byte-drift test;
the E3 "not modelled in tier 1" note rendered on the storage-keys page section
(minor 10).
(`docs: generate the subset specification from must_reject fixtures`)
