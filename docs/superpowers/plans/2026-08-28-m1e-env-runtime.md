# M1-E: Env Runtime Semantics + Examples Implementation Plan (v2, post-adversarial-review)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the authored `serpent` package's `Env` real tier-1 runtime
semantics (a deliberately minimal in-memory model — storage with deep-copy
isolation, events, auth recording, a partial-and-honest TTL), land the
five-layer `Event.publish(env)` convention end-to-end (decorator metadata →
spec entry → frontend desugar → the already-shipped emitter path), and ship
the five examples (counter, errors, structs, events, allowance-token)
compiled, running at tier 1, and running as WASM with the same answers.

**Architecture:** Ruling E1(d): the model lives in `src/serpent/env.py`
(zero-dep, slotted, non-models NAMED in code); ruling E5(b): deep-copy on
`set()`/`publish()`/`require_auth_for_args()` instead of the escape-list flip
— the isolation property is the decision procedure; ruling E2: the event
convention mirrors Rust (`Annotated[T, topic]`, `topics=`, `data_format=`,
defaults snake_case/all-data/"map") and the frontend DESUGARS
`Event.publish(env)` into the existing
`HostCall("contract_event", (MakeTopics, …))` so the IR and emitter change
not at all; ruling E3: time algebra defers to M2, load-bearing; ruling E4:
TTL models exactly what a fixed-max-free model can honestly own.

**Tech Stack:** stdlib-only model; `serpent.spec` (stellar_sdk) for
`SCSpecEventV0` in `spec/sections.py` only; the existing M1-D build +
harness for the WASM legs.

**Spec:** `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`
(§2, §3, §8, §11, §13). **Dossier (citation target for all S/R/D/X/Q/P IDs):**
`docs/superpowers/specs/2026-08-28-m1e-inputs-dossier.md`. **Rulings:**
`docs/superpowers/decisions.md` 2026-08-28 "M1-E rulings" (E1–E10 all
adopted; the reasoning for E1 and E5 is recorded there and is binding) and
"M1-E plan-review rulings" (7 blockers + 8 majors adopted — triage in
`.superpowers/sdd/2026-08-28-m1e-env-runtime/plan-review.md`). This v2
integrates every finding. The review's positive evidence stands recorded:
the E5 isolation property PROVABLY HOLDS (deepcopy isolates and preserves
types for Vec, Map, frozen @contracttype recursing into held containers,
and Address) — Task 2's STOP clause is not expected to fire.

## Global Constraints

- **The frozen surfaces:** `serpent.__all__` unchanged EXCEPT the two
  sanctioned Task-5 additions — `topic` and a re-exported `Annotated`
  (review B1: `loader.py` restricts contract imports to `serpent.__all__`
  names, so `from typing import Annotated` is SPT2005 inside a contract;
  both additions land with `test_public_api.py` updated in the same
  commit). Env inspection surfaces and the deploy/frame/advance helpers
  are env-module attributes, never `serpent.__all__` names — authored
  contracts cannot import them (the loader restriction is the guard); `env.py`'s existing `__all__` and every existing method
  signature unchanged (Q16); the frozen 59-case semantics table untouched
  (E9 — the new table is a second file); the SPT registry append-only (D9 —
  `SPT1032` retires to `NO_FIXTURE_ALLOWLIST` + `NO_FIXTURE_REASONS`, its
  row survives); `tests/harness/` internals untouched except the three
  sanctioned moves in Task 1 and mechanical import-path updates (the
  mini-host's TTL/auth/event non-models are F's — ruling E4);
  `spikes/` frozen (R6 — not E's disposition).
- **Zero-dep discipline:** `serpent.env` stays inside the core zero-dep walk
  (`test_core_zero_dep.py` unchanged); only `serpent/spec/` (and
  transitively `serpent/emitter/sections.py`) touches `stellar_sdk`.
- **Deep-copy is law (ruling E5):** the model NEVER stores or returns a
  reference the caller can mutate — `set()` stores a deep copy, `get()`
  returns a deep copy, `publish()` snapshots topics and data,
  `require_auth_for_args()` snapshots args. The isolation property test
  (Task 2) is the E5 decision procedure: if it cannot be made to hold for
  any ChainValue shape, STOP — the escape flip becomes mandatory and the
  controller must re-rule (the dossier §B.2 carries the four-site edit
  list).
- **Non-models are NAMED, never approximated (S5/S13):** no frame rollback,
  no footprint, no budget, no auth trees, no instance-flush semantics, no
  TTL clamp/trap/dead-entry (ruling E4/E8) — each named in the model's
  docstring in S5's own voice, with F's tier-2b as the gate. The model's
  header docstring states: silent false green is this model's failure mode;
  it is NOT an oracle.
- **Error vocabulary is ONE hierarchy (ruling E8):** `serpent.errors` gains
  `MissingValue(ContractError)` with `code = CODE_MISSING_VALUE`; the tier-1
  `get`, the emitter's guard (already emitting the code), and every test
  name the same class/code. No parallel exception taxonomy in `env.py`.
- **Every frontend/registry edit forces `docs/subset.md` regeneration**
  (`python -m serpent.compiler._render_docs`) in the same commit, or
  `test_subset_docs.py` fails (dossier §C.3).
- **Licensed edits, exactly these (the D12 precedent):** Task 2 —
  `recognize.note_escapes`' docstring justification rewrite + the
  keyword/positional escape asymmetry closed in the kept-exemption
  direction (`collect_never_owned` distinguishes the three serializing
  calls' kwargs — review m-fix: a verified 3-liner — while every OTHER
  call's kwargs keep escaping, and a NEW clean keyword-escape pin for a
  non-serializing call replaces the coverage the inverted test loses);
  Task 5 — ONE annotation seam (review B2: `decorators._build_record`
  reads hints with `include_extras=True` LOCALLY, records each field's
  marker, and stores the STRIPPED annotation in the metadata, so
  `typemap`/`resolve_annotation` never see `Annotated` and need NO edit;
  `decls.py` has no separate resolver — do not touch it) +
  `@contractevent`'s metadata growth + `sections.py`'s event entry +
  the two `serpent.__all__` additions; Task 6 — the
  `RECOGNIZED["event.publish_reject"]` flip to a desugar, `SPT1032`'s
  allowlist retirement (CONTROLLER-SANCTIONED registry edit: allowlist +
  reasons + count pins + `tests/unit/test_recognize_env.py`'s
  `_EXPECTED_ROWS`/REJECT-consistency pins + `recognize.py:59/:233`
  strings + `SurfaceKind`'s docstring — review M9's full list), the
  `must_reject` fixture move; Task 10 — STALE-STRING-ONLY message edits
  wherever the census names them, including `typemap.py` and `ir.py`
  docstring mentions (review M15: comment/message text only — never
  behavior — is the sanction's boundary). NOTHING else in
  frontend/registry/spec surfaces.
- **The examples join `FIXTURES`** (`tests/unit/test_emitter_end_to_end.py`)
  — each buys build/validate/size/needed⊆linked/goldens/host-fn-inventory
  for free (dossier §D.2); `examples/` joins `[tool.mypy] files` and the
  `ruff format` gate scope (ruling E6; pyproject + ci.yml edits in Task 7).
- Gates at every commit: `uv run pytest -q`, `uv run mypy --strict`
  (NO path args — config-driven from pyproject's `files`, so Task 7's
  `examples` addition takes effect; review minor), `uv run ruff check .`,
  `uv run ruff format --check src tests` (plus `examples` once Task 7
  adds it, matching ci.yml's edit). TDD RED/GREEN. Conventional
  commits, no emojis, no em dashes, explicit paths, trailer
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; signing
  fallback + timestamped `.git/unsigned-commits.log` entry.
- Sub-plan boundaries: footprint/tier-productization/mini-host fidelity are
  F's; CLI and docs site are G's; `env.logs()`, cross-contract, time
  algebra are M2's (rulings E3/E8).

---

### Task 1: Shared foundations — the three sanctioned moves + the error class

**Files:**
- Modify: `src/serpent/errors.py` (add `MissingValue(ContractError)` with
  `code = CODE_MISSING_VALUE` AND `AbiCheckFailed(ContractError)` with
  `code = CODE_ABI_CHECK_FAILED` — the second has no class today; the
  tier-1 `get` ty-check (Task 2) raises it, matching the code the emitter's
  narrow check already emits — docstrings naming both consumers)
- Create: `tests/harness/errors.py` (M-3: `HostError`/`HostTrap` move here,
  wasmtime-free; `engine.py` re-exports for compatibility)
- Modify: `tests/harness/engine.py`, `tests/harness/objects.py`,
  `tests/harness/hostfns.py`, `tests/harness/i256.py` (import-path updates
  only; M-1: `objects.py`'s `STORAGE_TEMPORARY/PERSISTENT/INSTANCE`
  re-derive from `serpent._host._scalars.STORAGE_TYPE` — the pinned source —
  instead of local literals)
- Create: `src/serpent/types/_storage_key.py` (M-2: `storage_key(value:
  ChainValue) -> Hashable` — the value-level normalization: scalars
  (Symbol included) via `(_SCVAL_RANK, _cmp_payload())`; `Vec` recursively
  over its ELEMENTS in order; **`Map` over its ITEMS — key AND value pairs
  (review B7: `Map.__iter__` yields keys only; iterating it collapses maps
  differing in values), as a frozenset of (storage_key(k), storage_key(v))
  pairs**; `Struct` normalizes to the SAME `("map", frozenset(...))` shape
  an equal field-name-keyed `Map` produces (mirroring the harness,
  `objects.py:436-441` — a struct and its equivalent map ARE the same
  on-chain value, S9); chain values are acyclic by construction so no
  cycle guard is needed (note it); `objects.py`'s `map_key` keeps its
  word→value decode and delegates the value-level branch to this function)
- Test: `tests/unit/test_storage_key.py`, edits to
  `tests/unit/test_harness_hostfns.py` (imports only)

**Interfaces (Produces):**
- `errors.MissingValue` — raised by Task 2's `get`; asserted by every
  missing-key test from Task 2 on.
- `storage_key(value)` — ONE definition of storage/map key equality across
  tiers (S13; the harness bug `objects.py:36-49` documented is the
  motivating failure).
- `tests/harness/errors.HostError/HostTrap` importable without wasmtime.

- [ ] **Step 1: failing tests** — `MissingValue().code == CODE_MISSING_VALUE`
  and `isinstance(..., ContractError)`; `storage_key` equality for two
  structurally-equal struct keys built from fresh instances, inequality for
  different keys, hashability, recursion through `Vec`-in-struct and
  `Map`-in-`Vec`; the harness suite still green after the moves (its own
  tests are the regression net); `import tests.harness.errors` succeeds in
  a subprocess with wasmtime importable but NOT imported (assert via
  sys.modules).
- [ ] **Steps 2-5: RED → implement → GREEN + gates → commit**
  (`refactor: share storage-key equality and the harness error types`).

---

### Task 2: The Env model core — storage, events, ledger, deep-copy isolation

**Files:**
- Modify: `src/serpent/env.py` (bodies for: `Env.__init__` (NEW —
  `timestamp=`, `sequence=`, `auths=None` per dossier §C.1), `storage()`,
  `ledger()`, `events()`, the three bucket accessors, `get`/`set`/`has`/
  `del_`, `Events.publish`, `Ledger.timestamp`/`sequence`; `__slots__`
  entries; inspection properties `published_events`/`recorded_auths`; the
  S5-voiced model disclaimer docstring; Q1/Q4's "real host"/"host bridge"
  docstrings REWRITTEN to name the model and point the host bridge at F's
  tier-2b)
- Modify: `src/serpent/compiler/recognize.py` (LICENSED: `note_escapes`
  docstring justification rewrite — the exemption now holds because the
  model deep-copies, not because tier 1 cannot run; the
  keyword/positional asymmetry in `collect_never_owned` closed in the
  kept-exemption direction: kwargs to the three serializing calls stop
  escaping)
- Modify: `tests/unit/test_frontend.py` (the two pinned asymmetry tests
  updated — `test_a_container_in_a_keyword_position_loses_ownership`
  inverts for the serializing calls; `test_a_container_built_up_in_a_loop_
  compiles` stays green and gains a docstring note citing ruling E5)
- Modify: `tests/unit/test_decorators.py`, `tests/unit/test_address.py`
  (ONLY the `NotImplementedError`-pinning assertions that now change —
  each REWRITTEN into a positive assertion, never deleted; dossier §D.4)
- Test: `tests/unit/test_env_model.py`

**Interfaces (Produces):**
- `Env(timestamp=..., sequence=..., auths=None)` — `auths=None` means
  mock-all-auths (S4); a non-None iterable is the allow-set Task 4's
  `require_auth` checks against.
- Semantics, binding: `set` stores a DEEP COPY keyed
  `(durability, storage_key(key))`; `get` returns a DEEP COPY, raises
  `MissingValue` when absent and no default, and TYPE-CHECKS the stored
  value against `ty` **at TAG level, mirroring the emitter's `abi_check`
  exactly (review B6): a `tag_of_chain_value(value)` helper compared
  against `ty`'s tag family — the Bytes family shares one tag,
  struct↔Map share one tag (the emitter maps `TyTag.STRUCT` to
  `TAG_MAP_OBJECT`), and Vec/Map ELEMENT types are NOT checked (the
  emitter's check is tag-only; a deeper tier-1 check would reject what the
  chain accepts — S13)** — raising `errors.AbiCheckFailed` (Task 1's new
  class carrying `CODE_ABI_CHECK_FAILED`, the same code the emitter's
  narrow check emits, so both tiers name one failure);
  `has` returns chain `Bool` (Q12); `del_` on an absent key is a silent
  no-op MIRRORING the mini-host, with the dossier §F.1.4 unverified-
  assumption comment in `i256.py`'s voice and a named carried obligation
  to F; `Events.publish` validates `topics[0]` is a short Symbol (S10,
  reuse `val.fits_symbol_small`) and appends a SNAPSHOT; `timestamp()`
  returns `U64`, `sequence()` returns `U32` (Q13).
- **The isolation property (E5's decision procedure), stated as such in the
  test's docstring:** build a `Vec`, `set` it, mutate the local,
  `get` — the stored value is unchanged; mutate the `get` result — the
  store is unchanged; same for `Map`, struct, `Vec`-in-struct; a
  Hypothesis property over generated ChainValue shapes. **If this cannot
  hold, STOP and report BLOCKED — the controller re-rules E5.**

- [ ] **Step 1: failing tests** — round-trips per bucket incl. struct and
  composite `(from, spender)`-style keys (Q11, via a `@contracttype` pair
  holder); `MissingValue` on absent; the default path; the ty-mismatch
  check; `Bool` return type asserted `type(...) is Bool`; the isolation
  property suite; `published_events` snapshot shape; the recognize.py
  asymmetry pins RED-first; `Env()` refuses unknown attributes (slots,
  dossier F.1.14).
- [ ] **Steps 2-5: RED → implement → GREEN + gates → commit**
  (`feat: give Env a deep-copying in-memory tier-1 model`).

---

### Task 3: The partial-and-honest TTL model

**Files:**
- Modify: `src/serpent/env.py` (per-entry `live_until: int | None`;
  `extend_ttl` on all three buckets per their shipped signatures;
  `Env.advance(n: int)` test hook (NEW, documented as test-facing);
  lazy expiry in `get`/`has` when `sequence > live_until`)
- Test: `tests/unit/test_env_ttl.py`

**Interfaces (Produces):**
- Ruling E4(c) exactly: `live_until = max(live_until, sequence +
  extend_to)` (never-reduce); the threshold guard (`extend only when
  live_until - sequence < threshold`); expiry-on-advance (an expired entry
  reads as absent: `get` → `MissingValue`/default, `has` → `Bool(False)`).
  **Review M14's specifications:** a never-extended entry has
  `live_until=None` meaning "immortal until first extended" — every
  algebra guards `None` (the review's TypeError repro); `extend_ttl` on an
  ABSENT key raises a loud `ContractError` (this IS S8's "extending a
  dead entry errors" for the never-written case — the one dead-entry rule
  a fixed-sequence model CAN own); instance `extend_ttl` on an
  instance bucket with no entries is still valid (the instance entry
  itself always exists once deployed — one bucket-wide live_until).
  **NO clamp, NO trap, NO expired-then-extend error** — `extend_to` above
  any bound accepted as-is, with the named missing-host-fact comment
  (`get_max_live_until_ledger` is M2) and a named carried obligation to F.

- [ ] **Step 1: failing tests** — never-reduce (a smaller extend_to after a
  larger is a no-op); the threshold guard both sides; expiry after
  `advance` (get→MissingValue, has→False, then a re-set revives); the
  EXPLICIT non-tests (dossier §F.2.9): three test functions whose bodies
  are a docstring + `pytest.skip("clamp/trap/dead-entry are unmodelled at
  every tier — host fact get_max_live_until_ledger is M2; F's tier-2b
  proves them")` so the gap is enumerable, never silent.
- [ ] **Steps 2-5: RED → implement → GREEN + gates → commit**
  (`feat: model the honestly-modellable half of TTL at tier 1`).

---

### Task 4: deploy/invoke helpers, ambient env, auth

**Files:**
- Create: `src/serpent/_frame.py` (review B4: the ambient-frame contextvar
  lives in a LEAF module — `env → types → address → env` is a verified
  circular import; `_frame` imports nothing from serpent, both `env.py`
  and `address.py` import it)
- Modify: `src/serpent/env.py` (module-level `deploy(cls, env, *args,
  **kwargs)` and the frame entry/exit calling into `_frame`; naming of the
  frame helper is the implementer's, documented)
- Modify: `src/serpent/types/address.py` (bodies for `require_auth`/
  `require_auth_for_args`: resolve the ambient env — a stray call outside a
  frame raises a LOUD RuntimeError naming deploy/invoke; check against the
  env's allow-set (None = mock-all-auths records-and-succeeds, S4);
  record `(address, args_snapshot | None)` into the env)
- Test: `tests/unit/test_env_deploy.py`

**Interfaces (Produces):**
- `deploy(ContractCls, env, *args)` → the instance: runs `__init__` exactly
  once inside an ambient frame; an exception out of `__init__` re-raises as
  a dedicated `ConstructorFailed` error NAMING the original (S12's
  laundering modelled: the author's code is NOT the surfaced identity —
  docstring quotes S12's "must say so, prominently"); a second deploy of
  the same instance is a loud error.
- Export invocation at tier 1: contract methods are ordinary Python — the
  frame is entered via a context manager `env.frame()` (or equivalent) that
  deploy uses and tests use for post-deploy calls; **calling
  `require_auth` outside any frame raises**; the pre-deploy refusal
  (ruling E7(ii)): the model refuses `get`/`set`/`publish`/auth until
  `deploy` has run — ONE boolean, loud error naming deploy. try/finally
  clears the ambient var; a raising frame leaves no stale env (F.1.7's
  test).
- `recorded_auths` surfaces `(Address, Vec | None)` snapshots.

- [ ] **Step 1: failing tests** — deploy-once semantics; the laundering
  (a `@contracterror` raise inside `__init__` surfaces as
  `ConstructorFailed`, and the test asserts the author's code is
  retrievable but NOT the exception identity); pre-deploy refusal for each
  surface; stray `require_auth` outside a frame; the raising-frame
  contextvar cleanup; allow-set auth (None records+succeeds; a non-member
  address raises); args snapshotting isolation.
- [ ] **Steps 2-5: RED → implement → GREEN + gates → commit**
  (`feat: add tier-1 deploy and invoke framing with recorded auth`).

---

### Task 5: The event convention — decorator, typemap, resolvers, spec entry

**Files:**
- Modify: `src/serpent/decorators.py` (`@contractevent(topics=...,
  data_format=...)`; the `topic` marker object exported from `serpent`
  (add to `serpent/__init__.py` and its `__all__` — THE ONE sanctioned
  `__all__` addition, with `test_public_api.py` updated in the same
  commit: authors must be able to spell `Annotated[Address, topic]`);
  metadata grows to `{"kind","fields":[(name, annotation, location)],
  "prefix_topics", "data_format"}` per dossier §C.2; defaults
  snake_case(ClassName) / all-fields-data / "map"; validation:
  `prefix_topics` ≤ 2 (the XDR cap, pre-validated source-located per R5),
  each a valid short Symbol; `"single-value"` requires exactly one
  non-topic field)
- Modify: `src/serpent/decorators.py` ONLY (review B2's narrow option):
  `_build_record` reads annotations with `typing.get_type_hints(...,
  include_extras=True)` locally — the classic trap: WITHOUT that flag the
  `Annotated` marker is silently stripped and no topic ever registers —
  records each field's marker, and stores the STRIPPED annotation in the
  metadata, so `typemap.to_spec_type`, `resolve_annotation`, and every
  downstream consumer see plain chain types and need NO edit (`decls.py`
  has no separate resolver — untouched). Pin: an `Annotated[U32, topic]`
  field resolves as `U32` in the spec entry AND the compiled `Ty`, and its
  location is `topic`. Also `serpent/__init__.py`: export `topic` and
  re-export `Annotated` (`__all__` += 2; `test_public_api.py` updated same
  commit; a compile test proves a contract module can spell
  `Annotated[Address, topic]` — review B1's SPT2005 trap).
- Modify: `src/serpent/spec/sections.py` (delete the `kind == "event"`
  refusal; `_event_entry` building `SCSpecEventV0` — doc, lib=b"" (cap 80
  pre-checked), name as SCSymbol, prefix_topics, params with per-field
  `location`, `data_format`; events appended AFTER functions in entry
  order (ruling E2 — the on-chain spike1 spec golden MUST NOT move,
  asserted); `build_spec_entries` docstring updated; the `types=` arm's event
  REFUSAL stays but its MESSAGE changes to point at the new `events=`
  keyword (review M13 — the two tests pinning the old
  deferred-to-sub-plan-E message are in this task's test list and update
  here); events arrive via the NEW `events=` keyword mirroring
  `spec_inputs.events`)
- Modify: `src/serpent/emitter/sections.py` ONLY (plumb
  `spec_inputs.events` through to the new `events=` — review minor:
  `module.py` needs no edit, `sections.spec_payload` is the seam; dossier
  F.1.11's matching-entry test). Event NAME validation: the SCSymbol cap
  is 32 (probe-verified) — pre-check name and prefix topics against
  `val.is_valid_symbol` (≤32, charset), NOT `fits_symbol_small`; a >9-char
  prefix topic is legal and pools via linear memory at the publish site
  (S19), with the needs_memory consequence noted in the desugar (Task 6).
  snake_case algorithm (review minor), specified: insert `_` before each
  uppercase letter that follows a lowercase letter or precedes a lowercase
  letter in an acronym run (`MyEvent`→`my_event`, `MyHTTPEvent`→
  `my_http_event`), then lowercase — implement once in decorators, test
  those two vectors plus a leading-acronym name.
- Test: `tests/unit/test_decorators.py` (extend), `tests/unit/test_sections.py`
  (extend), `tests/unit/test_typemap.py` (extend), `tests/unit/test_emitter_module.py`
  (the event-entry presence test)

**Interfaces (Produces):**
- Authoring: `@contractevent`, optionally `@contractevent(topics=("t1",),
  data_format="vec")`; fields `Annotated[T, topic]` for topics; `Transfer`
  in the dossier §C.2 shape.
- Metadata + `SCSpecEventV0` per dossier §B.4 Layer 1 [verified caps:
  prefix_topics ≤ 2, param name ≤ 30, lib ≤ 80].
- The spec-entry order: structs, error enums, functions, THEN events —
  pinned by a new order test AND the untouched on-chain golden.

- [ ] **Step 1: failing tests** — metadata shape for marked/unmarked/
  defaulted classes; snake_case default (`MyEvent` → `my_event`);
  data_format validation incl. the single-value arity rule; the four
  unwrap sites each pinned (an `Annotated[U32, topic]` field resolves as
  U32 everywhere); prefix_topics=3 → a source-located serpent error, never
  a stellar_sdk ValueError (R5 negative control); `_event_entry` XDR
  decodes with correct locations/format; entry-order test; the spike1
  golden byte-identical before/after; a built module containing a
  publishing contract carries the matching `SC_SPEC_ENTRY_EVENT_V0`
  (F.1.11).
- [ ] **Steps 2-5: RED → implement → GREEN + gates → commit**
  (`feat: land the contractevent topic convention and its spec entry`).

---

### Task 6: The frontend desugar + SPT1032 retirement + fixture moves

**Files:**
- Modify: `src/serpent/compiler/recognize.py` (LICENSED:
  `RECOGNIZED["event.publish_reject"]` becomes a desugar row — an
  `Event.publish(env)` call on a direct construction lowers to the
  EXISTING `HostCall("contract_event", (MakeTopics(Const(Symbol(prefix)),
  <topic fields in declaration order>), <data per data_format>))`; the
  data shapes (review B5 — MakeMap is WRONG for "map": runtime field
  values force `all_static=False` and the chain form): **"map" →
  `MakeStruct(<synthetic-or-event name>, fields sorted per P7)`** — the
  node that already does compile-time sorted Symbol keys + runtime values
  via `map_new_from_linear_memory`, and which auto-feeds
  `struct_key_descriptor_sets` and the needs_memory accounting for free
  (`frontend.py:743-745`); "vec" → `MakeVec` in declaration order;
  "single-value" → the lone data field's expression. Event CONSTRUCTION is
  kwargs-only (review B3 — the shared @contracttype rule; positional
  construction is SPT3020): the desugar maps keyword args to fields.
  Event-instance-as-local stays rejected (`expr.py`'s SPT1037 path
  unchanged — construction-and-publish in one expression is the supported
  shape, documented))
- Modify: `src/serpent/compiler/codes.py` (CONTROLLER-SANCTIONED:
  `SPT1032` → `NO_FIXTURE_ALLOWLIST` + reason "the form it rejected is now
  supported (sub-plan E)"; intent text gains a supersession note; count
  pins updated)
- Move: `tests/must_reject/constructs/event_instance_publish.py` → deleted
  (its construct now compiles); regenerate `docs/subset.md`
- Modify: `tests/fixtures/token_style.py` (X3: the revert, with review
  B3/M10's corrections — the spelling is KWARGS-ONLY:
  `Transfer(from_=frm, to=to, amount=amount).publish(env)`, and the
  `Transfer` class gains the convention that reproduces the CURRENT event
  shape: `from_`/`to` marked `Annotated[Address, topic]`,
  `data_format="single-value"` with `amount` the lone data field, and
  `topics=("transfer",)` — so topics stay `(Symbol("transfer"), frm, to)`
  and data stays the bare `amount`, making the both-spellings equivalence
  golden PROVABLE; rationale comment updated); Create:
  `tests/fixtures/token_style_canonical.py` (a minimal contract keeping
  the `env.events().publish((Symbol, Address, Address), data)` spelling —
  the heterogeneous-topics coverage, dossier §E2(v))
- Modify: `tests/unit/test_frontend*.py` pins; `FIXTURES` gains
  `token_style_canonical`
- Test: `tests/unit/test_frontend_events.py` (new — the desugar's IR
  golden: the lowered HostCall shape, host_fns_used gaining
  `contract_event`, needs_memory accounting for the prefix symbol)

**Interfaces (Produces):**
- Both spellings compile; the desugared IR is BYTE-FOR-BYTE the same shape
  the canonical spelling produces (one IR golden asserts the two forms of
  an equivalent event produce equivalent HostCall trees) — which is what
  makes the emitter unchanged (E2-d/e).
- `token_style.py` reverted; its build still passes and its `.wat.txt`
  golden regenerates (SELF-SNAPSHOT — the diff is the reviewable change).

- [ ] **Step 1: failing tests** — the desugar IR golden; both-spellings
  equivalence; SPT1032 unreachable-from-source meta-test (allowlist
  honored); subset regen byte-green; token_style compiles + builds + runs
  (mint/transfer under FullHost with the event recorded — extending the
  existing end-to-end); the canonical fixture likewise.
- [ ] **Steps 2-5: RED → implement → GREEN + gates → commit**
  (`feat: desugar Event.publish into the canonical event lowering`).

---

### Task 7: Examples wave 1 — counter, errors, structs + the gates scope

**Files:**
- Create: `examples/counter.py` (graduated VERBATIM from
  `tests/fixtures/sandbox_counter.py` — the fixture then becomes a thin
  re-export/path constant, or the byte-identity guard moves to compare
  `examples/counter.py` against `sandbox/counter.py` — implementer picks
  the smaller edit, documented), `examples/errors.py` (fresh: multi-code
  `@contracterror`, a method per failure mode, the S12
  constructor-laundering caveat DEMONSTRATED — an `__init__` that can
  raise, with the docstring quoting S12), `examples/structs.py` (fresh: a
  struct with a >9-char field + a struct storage key; `spike1_reauthored`
  stays an untouched fixture)
- Modify: `pyproject.toml` (`[tool.mypy] files` gains `"examples"`;
  ruff format scope), `.github/workflows/ci.yml` (`ruff format --check src
  tests examples`; pytest already covers via FIXTURES), `tests/unit/
  test_emitter_end_to_end.py` (`FIXTURES` gains the three; a
  tier-1-run test per example: deploy + invoke + assert, using Task 4's
  helpers), **`tests/unit/test_emitter_printer.py` (its OWN stem-keyed
  `FIXTURE_NAMES` exact-set + goldens gain the examples — review M8:
  FIXTURES does NOT feed it; verify its path root handles `examples/` or
  extend it to (path, stem) pairs — stems are distinct (`counter` vs
  `sandbox_counter`) so no collision, but the exact-set test must move in
  the same commit)** and **`tests/unit/test_harness_hostfns.py` (its own
  `_FIXTURES` inventory list — same reason)**,
  `tests/goldens/wasm/` (three new SELF-SNAPSHOT goldens).
  **Examples import mechanics (review minor), specified:** `examples/` is
  a FLAT non-package directory; the tier-1 test loads each example via
  `importlib.util.spec_from_file_location` (the pattern the loader itself
  uses), never `sys.path` hacks; the WASM leg needs only the path
  (`build_file`). mypy covers `examples/` via pyproject `files` — modules,
  not a package, is fine for mypy.
- Test: `tests/unit/test_examples.py` (the tier-1 legs; the WASM legs ride
  FIXTURES)

**Interfaces (Produces):**
- The R1 acceptance triple per example (dossier §D.2): compiles; runs at
  tier 1 (deploy → invoke → assert state/events); runs as WASM under
  FullHost with the SAME answers (assert equality of decoded results
  between the two legs in the same test — the S13 differential applied to
  whole contracts).

- [ ] **Step 1: failing tests** — per example: the tier-1 leg and the
  same-answers cross-check; the counter graduation anti-drift guard in its
  new shape; mypy/format gates green over `examples/`.
- [ ] **Steps 2-5: RED → implement → GREEN + gates → commit**
  (`feat: ship the counter, errors, and structs examples`).

---

### Task 8: Examples wave 2 — events + allowance token

**Files:**
- Create: `examples/events.py` (both publish spellings; a
  topics-marked event and an all-data event; `data_format="vec"` shown
  once), `examples/allowance_token.py` (the S6 example: balances
  persistent, allowances in `temporary()` keyed by a `@contracttype`
  `(from_, spender)` composite (Q11's literal case), `approve` with an
  `extend_ttl` call and a live-until expectation, `transfer_from`
  consuming allowance, `require_auth` on the right parties, events on
  approve/transfer; NO cross-contract (S6))
- Modify: `FIXTURES` + goldens + `tests/unit/test_examples.py`
- Test: as Task 7, plus: an expiry scenario at tier 1 (approve → advance
  past live_until → transfer_from fails with the author's error) — the E4
  model's showcase; the WASM leg CANNOT run the expiry scenario (the
  mini-host has no TTL model — assert instead that the same sequence
  WITHOUT expiry agrees, and pin the divergence with a comment naming F).

- [ ] **Step 1: failing tests** — per Task 7's triple; the expiry
  scenario; auth recorded for approve/transfer_from; the publish-spellings
  coverage.
- [ ] **Steps 2-5: RED → implement → GREEN + gates → commit**
  (`feat: ship the events and allowance-token examples`).

---

### Task 9: The Env differential table + properties

**Files:**
- Create: `tests/semantics/env_scenarios.py` (the E9 second table: a
  frozen-shaped dataclass of stateful scenarios — setup steps, an
  invocation, an expected outcome class/value — covering the three
  durabilities, defaults, MissingValue, events incl. both spellings,
  auth recording, ledger reads, TTL never-reduce/threshold at tier 1
  only), `tests/unit/test_env_differential.py` (runs every scenario
  against (a) the tier-1 model via deploy/invoke and (b) compiled WASM
  under FullHost, asserting same decoded answers; TTL scenarios AND
  auth-ARGS scenarios marked tier-1-only with reason strings — review
  M11: the harness DISCARDS require_auth_for_args' args, so an
  args-sensitive assertion has no WASM leg; **tier-1 Env's ledger
  defaults are PINNED equal to the harness's (`1_700_000_000` /
  `1_000_000`, imported from one shared constant home, not restated) so
  ledger scenarios agree by construction**; the honest-limit docstring
  from ruling E9 verbatim)
- Test: plus the properties: the struct/container-key round-trip
  (dossier §D.5.1, Hypothesis over ChainValue keys); the publish-then-raise
  honest pin (F.1.8: tier-1 keeps the event, the docstring states the
  chain answer differs, carried to F); the `SCSpecEventV0` round-trip
  property (generated event classes decode to matching
  locations/format/prefix_topics); the help-string-compiles property
  (F.1.6 — every SPT1034-family help rewrite compiles)

**Interfaces (Produces):**
- `ENV_SCENARIOS` importable — F's tier-2b re-run corpus (the named
  carried obligation, mirroring D11's shape).

- [ ] **Step 1: failing tests** — the table runner both legs; each property
  RED-provable via a seeded model bug (mutation-check at least: a
  reference-storing set, a bool-returning has, an eager default).
- [ ] **Steps 2-5: RED → implement → GREEN + gates → commit**
  (`test: add the stateful Env differential and its properties`).

---

### Task 10: The promise sweep + docs

**Files:**
- Modify: `src/serpent/compiler/expr.py:234` (`_TIME_ALGEBRA_NOTE` → M2),
  `src/serpent/types/numeric.py` (`_NO_TIME_ARITH` message → M2; docstring
  keeps the bridges), with their pins (`tests/unit/test_numeric.py:457`,
  the four `SPT3005` semantics-case notes if they cite E) — LICENSED
  message-text edits (ruling E3)
- Modify: `sandbox/README.md` (the stale §17-20 block rewritten: build
  works via `build_file`, Env runs at tier 1; `compile.py` optionally
  upgraded to print `build_file` facts — small, or left to G, implementer's
  call, documented), `README.md` (architecture bullet for the Env model +
  examples; the honest-boundary note), `docs/subset.md` regen if any
  message changed
- Sweep: every remaining `"sub-plan E"` string in src/tests resolved —
  implemented, repointed at M2/F, or (only if genuinely stale) removed.
  **The census is review M15's corrected count: 38 mentions across 9
  files** — the dossier's list PLUS `ir.py:583`, `typemap.py:31/:125`,
  `numeric.py:31/:544/:547`, `expr.py:1249`, `sections.py:41`,
  `address.py:17` (the `typemap`/`ir` mentions are message/comment-text
  edits under the Task-10 stale-string sanction). The task's test asserts
  `grep -r "sub-plan E" src/` returns ONLY deliberate historical mentions
  (an explicit allowlist; decisions/dossiers/plans excluded from the walk)
- Docs: the bridge-pattern time example (ruling E3's worked example) in
  the errors-or-structs example's docstring or README — implementer's
  placement, documented
- Test: `tests/unit/test_no_stale_promises.py` (the grep assertion, with
  an allowlist of the deliberate mentions)

- [ ] **Step 1: failing tests** — the promise-sweep grep test RED against
  the pre-sweep tree; message-pin updates RED-first.
- [ ] **Steps 2-5: RED → implement → GREEN + gates → commit**
  (`docs: close every promise that named sub-plan E`).

---

## Completion (process, not tasks)

1. Final whole-branch review on Fable, fed a `final-review-attention.md`
   accumulated from Task 1 (reconciled against the ledger's deferred lines
   — the M1-D lesson: the accumulation step can drop entries).
2. Obligations carried OUT of M1-E (record in the attention file):
   to **F** — re-run `ENV_SCENARIOS` on tier-2b; TTL clamp/trap/dead-entry
   unproven at every tier; `del_` absent-key behavior unverified;
   publish-then-raise rollback divergence; the deep-copy isolation property
   as a tier-2b differential; footprint (its row). To **G** — the examples
   as docs-site sources; `sandbox/compile.py`'s upgrade if deferred; the
   deploy-gate example choice (one of the five, Elliot approves at M1 end).
   To **M2** — time algebra (homogeneous ops first), env.logs(),
   TTL helpers, the env/ package promotion.
3. One fix wave, then local merge to main. No pushes (hard stop).
