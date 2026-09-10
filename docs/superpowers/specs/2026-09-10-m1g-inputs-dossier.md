# M1-G DESIGN-INPUTS DOSSIER — CLI plugin + ship (`stellar serpent build|inspect|doctor`, the docs site, CI's Rust job, the hygiene pass, the seventh example, the M1-end deployment)

Compiled 2026-09-10 for the sub-plan G plan author and its adversarial
reviewer. Every claim carries a citation ID; the plan cites IDs, not prose.
Facts marked **verified 2026-09-10** were re-checked live this session (RPC,
GitHub, PyPI, local builds and probes); everything else is quoted from the
frozen inputs it cites.

Citation-ID families:

| Prefix | Source |
|---|---|
| S# | design spec `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md` |
| R# | roadmap `docs/superpowers/plans/2026-08-26-m1-roadmap.md` |
| D# | rulings in `docs/superpowers/decisions.md` (dated title) |
| P# | Phase 0 findings `docs/superpowers/specs/2026-08-26-phase0-findings.md`, `spikes/spike1/DEPLOY_LOG.md` |
| U# | G inputs decided WITH Elliot 2026-09-10 (this dossier, §A.5) |
| O# | obligations carried INTO G by the C/D/E/E2/F attention files, ledgers, and final reviews |
| K# | chain/toolchain facts verified 2026-09-10 (this dossier, §B) |
| C# | what the repo contains today (this dossier, §C) |

How G differs from A–F: G ships almost no compiler, oracle, or host
surface. Its product is the **shipping shape** of everything A–F built:
the plugin a user types, the site a user reads, the CI that keeps the four
gates and the Rust gate honest on every push, the seventh example that
proves the whole authoring surface in one contract, and the one deliberate,
user-approved testnet deployment that closes M1 (S9) and retires the B1
divergence (O-DEP1). The controlling risk is therefore not "the compiler is
wrong" but "the ship is hollow": a CLI that works only from the repo venv,
docs that drift from the code they describe, a Rust CI job that passes
vacuously, a deployment whose fixtures never get re-recorded.

---

## A. FROZEN INPUTS

### A.1 Spec obligations (`2026-08-26-serpent-python-soroban-sdk-design.md`)

- **S1** §9 (whole section): ship a `stellar-serpent` console-script entry
  point (`uv tool install` puts it on PATH) so the Stellar CLI's plugin
  discovery exposes `stellar serpent build path/to/contract.py [--out …]
  [--meta k=v]`, `stellar serpent inspect artifact.wasm` ("sections, spec,
  computed protocol"), `stellar serpent doctor` ("toolchain/env checks").
- **S2** §9: `stellar contract build` is a built-in and built-ins win before
  plugin lookup, so serpent takes its own namespace (`stellar-contract-
  bindings`' choice). Deploy/invoke/bindings are NOT rebuilt: stock `stellar
  contract deploy|invoke` and `stellar-contract-bindings python` work on
  serpent output unmodified ("proven — the CLI already renders our spec as a
  typed interface"; re-verified K5).
- **S3** §9: "Register the repo with the `stellar-plugin` GitHub topic at
  publication." §11 M3 lists "plugin-topic registration" as M3's. G does
  not register (an outward action, and M3's by the spec's own schedule).
- **S4** §3 layout: `src/serpent/cli.py  # stellar-serpent console script
  (§9)`; `docs/  # mkdocs-material; subset spec; API reference`. (The
  sketch is design-era; `env/` is a module not a package (D-E10), and
  `emitter.py`/`specgen.py` became `serpent.emitter`/`serpent.spec` — the
  layout sketch already carries one correction note (D-E8) and G's
  additions should follow the shipped shape, not the sketch.)
- **S5** §11 M1 scope sentence: "…testing tiers 1–2, CLI build/inspect,
  examples (…), docs site, CI. Ends with a deliberate, user-approved
  testnet deployment."
- **S6** §11 M3: "PyPI release readiness (name decision), plugin-topic
  registration." The distribution NAME is an M3 decision; G publishes
  nothing to PyPI.
- **S7** §12 risks: "env.json / protocol churn — pinned by SHA; CI diffs
  against upstream `main`" (the existing `test_host_bindings.py` upstream-
  blob test already does this inside `pytest`, C13); "wasm-tools drift"
  belongs with the CI pin (O30); "Subset/docs/compiler drift —
  `must_reject/` is executable and generates docs" (C6: `docs/subset.md` is
  byte-drift-tested).
- **S8** §1 goals: "Interop with the existing ecosystem: `stellar` CLI (as
  a plugin), Stellar Lab, …"; "zero-plugin IDE support".
- **S9** §11 / process.md: M1 ENDS with a user-approved testnet
  deployment — a HARD STOP; Elliot approves in-session. That deployment
  also REPLACES the deployed shapes contract whose `area` traps (O-DEP1).
- **S10** §10 one-Val-codec rule: `inspect` must not grow a second decoder
  of serpent's own value layer; it decodes WASM sections and XDR through
  `stellar_sdk` (as `serpent.spec` already does), never `Val` words.

### A.2 Roadmap standing constraints (`2026-08-26-m1-roadmap.md`)

- **R1** Row G: "`stellar-serpent` console script (`build`/`inspect`/
  `doctor`), docs site (mkdocs; subset spec generated from `must_reject/`),
  CI (lint/typecheck/test/build/validate), README; ends with the M1 gate:
  user-approved testnet deploy of an example."
- **R2** Standing constraints for every sub-plan: pinned toolchain versions
  with drift-detection tests; adversarial review before execution; SDD with
  task-scoped reviews.
- **R3** Spike disposition: "`spikes/` stays as read-only reference evidence
  until sub-plan D supersedes the emitter and F supersedes the harnesses,
  then a cleanup task in G decides retain-vs-remove with the user." —
  DECIDED, U2.
- **R4** Every sub-plan leaves `main` green and independently useful.

### A.3 Decision-log rulings that bind G (`decisions.md`)

- **D1** 2026-08-27 "stellar-sdk becomes a runtime dep of serpent.spec
  only": core stays zero-dep, enforced by `test_core_zero_dep.py` (C14).
  The CLI's `build`/`inspect` need `stellar_sdk` (sections and XDR
  decoding), so `serpent.cli` must live on the EXEMPT side of that gate or
  import lazily — §E2.
- **D2** 2026-08-27 "SPT registry: honest-code remap" + M1-D B3/B11: the
  registry is append-only; wording widenings and new codes happen ONLY by
  controller sanction with snapshot pins updated in the same commit. G's
  hygiene pass (O-HYG*) is exactly such a sanctioned pass and needs its
  edits enumerated in the plan.
- **D3** 2026-08-27 M1-C final-review minors "folded into parked passes";
  2026-08-31 M1-E final-review "the SPT3019 relax-to-32 pass and the
  method-parameter `topic` refusal both feed M1-E2's dossier; `from_`
  aliasing is a G/M2 docs item".
- **D4** 2026-08-28 M1-E E6: `examples/` is flat, in BOTH `mypy --strict`
  `files` and the ruff format scope; "a non-strict-clean example falsifies
  the SDK's pitch". A seventh example inherits this bar (K10).
- **D5** 2026-08-27 M1-D E8: `contractmetav0` is emitted by default with
  `serpentver` from `serpent.__version__` (drift-tested against
  `importlib.metadata`), `name` = the `@contract` class, `version` only
  when the caller supplies it. `build --meta k=v` maps onto `build_wasm`'s
  `meta`; reserved keys are a plain `ValueError` (M1-D plan review).
- **D6** 2026-08-27 M1-D E9: `contractenvmetav0` carries the COMPUTED
  FLOOR; the build target is a frontend gate; "both numbers surface in
  BuildResult and the build line". `inspect`'s "computed protocol" is this
  floor, RE-DERIVED from the artifact's imports + constructor export (D10)
  and compared to the declared one.
- **D7** 2026-08-27 M1-D E5: the internal validator is unconditional;
  wasm-tools runs when on PATH; "installed + pinned in one CI job".
- **D8** 2026-08-27 M1-D E6: `build_wasm`/`build_file` are NOT in
  `serpent.__all__`; "CLI naming is G's". `test_public_api.py` pins
  `__all__` at 40 names (C13); the CLI adds nothing to it.
- **D9** 2026-08-28 "Constructor-bearing contracts raise the protocol floor
  to 22": the floor computation has FEATURE gates (`CONSTRUCTOR_MIN_
  PROTOCOL = 22` in `serpent._host._protocol`) alongside import gates.
  `inspect`'s recomputation must apply the same gate (an artifact exporting
  `__constructor` floors at 22).
- **D10** 2026-09-02 M1-F E5: `host/` is a SEPARATE `serpent-host`
  distribution built from source via `VIRTUAL_ENV=<root>/.venv uvx maturin
  develop --release`, NOT a uv workspace member (a maturin member would make
  `uv sync` require cargo). CI's Rust job (U2 of F, O-CI1) must respect
  this: `uv sync` first, maturin second, `uv run --no-sync` third.
- **D11** 2026-09-02 M1-F E13: the bridge-needle generalisation (O32) and
  parameter shadowing (E2 Task 4 m2) are RE-ROUTED to G's hygiene pass.
- **D12** 2026-09-02 M1-F final-review rulings: "the strict `obj_cmp`
  opt-in on the mini host (Task 0 C3) goes to G, not M2".
- **D13** 2026-09-02 M1-F E14 (+ plan-review m1): tier 3 is simulation-
  only; "no signing or sendTransaction code path exists in M1"; recording
  uses a fixed never-funded dummy source key by default. The deployment
  itself is done with the stock `stellar` CLI by Elliot (P2), not by
  serpent code — G adds NO signing code path.
- **D14** 2026-09-02 M1-F plan-review B1: "The deployed contract stays as
  it is until the M1-end deployment (a hard stop, Elliot's approval)"; the
  tier-3 fixtures record the DEPLOYED bytes and a separate assertion pins
  that HEAD's build DIFFERS until the next approved deployment (C10).
- **D15** 2026-09-02 M1-F E1/U1: the `soroban-sdk` rc pin and wheels are
  M3's; G's CI builds the host from source (D10) and does not publish
  wheels.
- **D16** 2026-08-26 "Standing-autonomy mechanics": hard stops are
  irreversible/outward actions — pushes, publishes, deployments. For G
  concretely: the testnet deployment (S9), any push, enabling GitHub Pages
  or running a docs deploy, the `stellar-plugin` topic (S3), a PyPI
  upload (S6), pushing a tag.
- **D17** 2026-09-02 M1-F Task 5 B2: `RealEnv(auths=...)` accepts CONTRACT
  authorizers only; the `_ADMIN` scenario constant is the shapes contract
  strkey used as an opaque decodable address (C10) — a NEW shapes contract
  id from the redeploy does not invalidate it (the row treats the value as
  opaque), but the comment naming it "the shapes contract id" becomes
  stale prose to fix.

### A.4 Phase 0 feeds (`phase0-findings.md`, `spikes/spike1/DEPLOY_LOG.md`)

- **P1** The plugin-discovery claim in S2 was asserted in the spec as
  "proven" for spec RENDERING (the CLI rendered the Phase 0 spec as a Rust
  trait); plugin DISPATCH itself was not probed in Phase 0. K4 now proves
  dispatch on CLI 27.1.0.
- **P2** The deployment commands that worked (DEPLOY_LOG.md:25-166):
  `stellar keys generate <name> --network testnet --fund`; `stellar contract
  deploy --wasm <file> --source <name> --network testnet`; `stellar contract
  fetch --id <C…> --network testnet --out-file <f>` (byte fidelity check);
  `stellar contract info interface --id <C…> --network testnet`; `stellar
  contract invoke --id <C…> --source <name> --network testnet -- <fn>
  --<arg> <v>`. "no friendbot/RPC flakiness observed".
- **P3** `stellar keys generate --global` no longer exists (CLI 27.1.0);
  Elliot has identities already (K11), so no key generation is needed.
- **P4** The Phase 0 contract `CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI`
  stays on chain as frozen evidence (spike1 goldens anchor to its bytes);
  G does not touch it.

### A.5 G inputs decided WITH Elliot (2026-09-10) — binding, not decisions.md material

- **U1** The M1-end deployment deploys TWO contracts: the FIXED
  `examples/shapes.py` (retiring the B1 `area` trap, O-DEP1) and the
  **bounty board**, promoted from `sandbox/bounty_board.py` to a seventh,
  gate-covered `examples/` contract that becomes the showcase deploy and a
  tier-3 fixture source. (Chosen over "shapes only" and "shapes + an
  existing example".)
- **U2** `spikes/` is RETAINED as frozen, read-only Phase 0 evidence (R3
  closed). G's only touch is `spikes/README.md`: say that D superseded the
  emitter and F the harnesses, and that the code is kept as provenance for
  the goldens and the deploy log. Zero citation churn in `src/`/`tests/`.

### A.6 Obligations carried INTO G (C/D/E/E2/F attention files, ledgers, final reviews), deduplicated

Grouped by the G workstream that owns them. "Attn" = `final-review-
attention.md` in the named sub-plan's `.superpowers/sdd/` directory.

**CLI**

- **O-CLI1** `stellar serpent doctor` (F attn §6 "To G"; S1).
- **O-CLI2** `sandbox/compile.py` still calls `build_wasm` not `build_file`
  ("Task 10 flagged, behaviorally identical", E attn "To G"; F dossier
  §A.6 tail). With a real CLI it becomes a thin pointer or is retired.
- **O-CLI3** `DEFAULT_TARGET_PROTOCOL = 27` while testnet is 28: "whether
  to bump it to 28 is a one-line G/M2 decision with no artifact-hash
  consequence (floors, not targets, are emitted)" (F dossier C14). §E10.

**CI**

- **O-CI1** CI Rust job: `SERPENT_REQUIRE_REAL_HOST=1`, maturin, the cargo
  gates (`cargo fmt --check && cargo clippy --all-targets -- -D warnings &&
  cargo test`, host/README.md) (F attn §6; E2 attn §6 U2 "CI (G) adds a
  Rust job"; `docs/testing.md` already says "CI's Rust job sets the
  switch" — a promise the tree makes today and G must keep).
- **O-CI2 (= O30)** The wasm-tools CI pin drift watch (D attn standing item
  2; F attn §6). ci.yml pins `1.258.0` with a hand-bump comment; K8 shows
  the local tool equals the pin today.
- **O-CI3** `test_emitter_end_to_end.py`'s spike.wasm-anchored tests skip
  on fresh clones (D attn T13 "M14, by design") — CI never runs them; note,
  not a G change (U2 keeps spikes as-is).

**Docs**

- **O-DOC1** Examples as docs-site sources (E attn "To G"; E2 attn §5
  "`examples/shapes.py` as a docs-site source").
- **O-DOC2** `from_` field-name aliasing: bindings render `from_` where
  Rust renders `from` — "a G/M2 docs item" (E attn item 6; D3). Documented
  limitation, not a feature, in G.
- **O-DOC3** `docs/testing.md`, `README.md` "Testing", `host/README.md`
  exist (F Task 10); the README's status section still says "M1 … is in
  progress; this repo has no release yet" and describes six examples — the
  M1-close rewrite is G's (R1 "README").
- **O-DOC4** `sandbox/README.md` is stale: M1-D-era quick start, "five
  worked contracts", no mention of the real host or of a CLI (E plan's
  promise sweep touched only its M1-D text).

**Frontend / registry hygiene (the sanctioned pass, D2)**

- **O-HYG1** SPT4012's wording PARKED for the sanctioned wording pass (E2
  attn Task 2; "the sanctioned wording pass (SPT4012; the origin-field
  drift; `_HELP` ordering)", E2 attn §5). Today (C9): intent "struct
  fields need a chain-type annotation", title "@contracttype -- non-chain
  field annotation".
- **O-HYG2** Origin-field drift: rows spell their origin as "Task 4
  (M1-E2)" vs "M1-E2 Task 2" (E2 attn Task 4 m3). Text-only registry edit;
  snapshot pins move with it.
- **O-HYG3** `loader._HELP` ordering (E2 attn Task 5 m1) — C9's census
  shows the keys ARE in code order today (SPT4026 sits after SPT4025); the
  plan re-censuses rather than trusting either record.
- **O-HYG4 (= O32)** Generalising the bridge-needle gate: `test_bridging_
  completeness.py`'s `_BRIDGED_RAISE_SOURCES` is a hand-kept tuple of
  (module, functions) — a one-directional blind spot: a NEW declaration-
  layer raise in a function not on the tuple is invisible (E2 attn §3/§5;
  D11).
- **O-HYG5** A PARAMETER named like a declared union/struct/enum shadows
  it silently: `frontend.py:441-443` writes `reserved[name] = "a
  parameter"` over the module-level reservation of a type name (E2 attn
  Task 4 m2; D11). SPT2004 ("Local/param shadows a param, module constant,
  import, or type name") is the honest code and ALREADY EXISTS (K13) — the
  gap is a missing check, not a missing code.
- **O-HYG6** `is_pinned` docstring in `examples/shapes.py` describes the
  test's action, not the method's; fixing it moves the shapes golden
  (E2 attn Task 8 m4; process.md "needs a golden regen").
- **O-HYG7** M1-C attn §8-9 parked passes: SPT3020/SPT3014 construct-
  example lists lag their honest uses; SPT1xxx help strings could cite
  `docs/subset.md`; `_own_doc`/`_class_doc` private imports from
  `spec.sections` (promote or add `doc_of()`); registry intent strings
  hardcode limit numbers (M1-C final minor 2); `frontend.py` imports
  `_host._protocol` via the private path (minor 3).
- **O-HYG8** The example/fixture inventories do NOT fail loudly on a
  missing example (E attn item 7 "Structural fix is F/G territory"; E2
  plan Task 8 "P5 records that these inventories do NOT fail loudly").
  Today (C7): `EXAMPLES` == the directory IS asserted; the other five
  inventories (golden stems, `FIXTURE_SOURCES`, `_FIXTURES`, the fuzz
  corpus's exact list, the real-leg module's imports) are not cross-
  checked against `EXAMPLES`.
- **O-HYG9** E2 attn Task 10 review minors on the "surface denial" grep
  gate (whole-line `_REPOINTED` exemption; vocabulary gaps; `*.py`-only
  walk) — carry or close in the docs task; none has a live instance.
- **O-HYG10** F final review: nineteen deferred minors carry "with the
  reviewer's line-by-line triage" (F ledger `minor (deferred)` lines,
  `final-review.md`); G's plan lists which it takes (candidates: commit
  subjects over 72 chars are process; `_member_for` first-match across
  error enums (T3); instance-bucket `ttl` for absent keys (T3); duplicate
  Symbol keys in an ScMap collapse silently (T2)) and leaves the host-fact
  candidates (`_innermost_error` two-distinct-errors probe, `map_del` on a
  missing key) to M2.

**Testing / the mock**

- **O-MOCK1 (= C3)** The strict `obj_cmp` opt-in on `FullHost`: "the mock
  still accepts what the host refuses" — two small Vals through `obj_cmp`
  (F attn §3 last bullet; D12). The deployed shapes bytes are the natural
  regression fixture (they trap on the host and on the embedded host, and
  today pass the mock).
- **O-MOCK2** `test_examples.py:398-430`-era coupling to `host._vec` was
  fixed in F Task 8 (public container decoder) — CLOSED; listed so the
  plan does not re-open it.

**Deployment (the hard stop, S9)**

- **O-DEP1 (= B1)** REDEPLOY the shapes contract whose `area` traps
  (`CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW`); re-record
  the four tier-3 fixtures against the new bytes; empty `B1_DIVERGENCE`;
  flip `test_this_trees_shapes_build_differs_from_the_deployed_bytes_until_
  the_next_deploy`; update `DEPLOYED_SHA256`, `deployed.wasm`, and the
  fixtures README table (C10; `tests/real_host/fixtures/testnet/README.md`
  §"Re-recording" is the procedure).
- **O-DEP2 (= R6)** The deploy-gate example choice — DECIDED U1.
- **O-DEP3** `process.md` "State" and the README record the new contract
  ids; `DEPLOY_LOG.md`'s shape (commands + ids + fetched-bytes sha) is the
  precedent for a committed deployment record.

**M2/M3 (NOT G's; listed so the plan can say so)**

- M3: prebuilt wheels (U1 of F); `soroban-sdk` 28.0.0 stable bump; the
  live tier-3 suite (signed sequences: `pin()`, archival; recorder
  arguments); PyPI name (S6, K6); `stellar-plugin` topic (S3).
- M2: tier-1 frame rollback; container ordering; TTL floors for all
  buckets; account authorizers; key-level footprint; archival modelling;
  `match` sugar; Option payloads/narrowing; `.value`; `discriminant`
  rename; typed container reads; time algebra; `env.logs()`; `env.py`
  package promotion; free-threaded CPython for `unsendable`.

---

## B. THE CHAIN AND TOOLCHAIN TRUTH (verified 2026-09-10)

- **K1** Testnet: `getVersionInfo` → `protocolVersion: 28`, RPC 28.0.1,
  captive core `stellar-core 28.0.1`; `getNetwork` passphrase "Test SDF
  Network ; September 2015", friendbot `https://friendbot.stellar.org/`.
  Unchanged since F (2026-09-02); the embedded host (28.0.2) still tracks
  it.
- **K2** Mainnet: `protocolVersion: 27` (RPC 27.1.1, core 27.1.0). Mainnet
  is NOT a deployment target (F U4); it matters only for O-CLI3.
- **K3** Stellar CLI releases (GitHub `stellar/stellar-cli`): latest
  `v28.0.0` (2026-08-26), then `v27.1.0` (2026-07-31). Local install is
  **27.1.0** (`~/.cargo/bin/stellar`). K4/K5 are therefore proven on 27.1.0
  and UNVERIFIED on 28.0.0 (F.1.7).
- **K4 Plugin dispatch PROVEN (CLI 27.1.0).** A stub `stellar-serpent`
  shell script placed on PATH: `stellar serpent build foo.py --out x`
  invoked it with argv exactly `build foo.py --out x`; `stellar plugin ls`
  listed `serpent` first among the installed plugins (alongside
  `contract-bindings*`, `core`, `registry`, `xdr`). Discovery is by the
  `stellar-<name>` PATH convention; no manifest, no registration.
  `stellar plugin search` searches GitHub (the `stellar-plugin` topic, S3).
- **K5 What the stock CLI already renders from a serpent artifact** (on
  `sandbox/bounty_board.wasm`, K10): `stellar contract info env-meta
  --wasm` → "Protocol: v22"; `info meta` → `name: BountyBoard`, `serpentver:
  0.0.1`; `info interface` → the full Rust trait (`fn post(env, poster:
  Address, reward: u32, priority: Priority) -> u32 …`) plus every
  `#[contracttype]` struct/union/enum; `info build` and `info hash` also
  exist. **`inspect` must not re-render the interface** — it adds the
  serpent-specific facts the CLI cannot know (§D.1, §E3).
- **K6** PyPI names: `serpent` is **TAKEN** (HTTP 200 on
  `pypi.org/pypi/serpent/json`); `stellar-serpent`, `serpent-sdk`, and
  `soroban-serpent` are free (404). The M3 name decision (S6) has a real
  constraint; G's install path is the git URL, so G needs no rename (§E12).
- **K7** Docs toolchain on PyPI: `mkdocs-material 9.7.7`, `mkdocs 1.6.1`,
  `mkdocstrings-python 2.0.8`.
- **K8** Local toolchain: Python 3.11.7; uv 0.12.10; cargo/rustc 1.97.1;
  `wasm-tools 1.258.0` (== the ci.yml pin, O-CI2); `gh` authenticated; the
  repo is `github.com/ElliotFriend/stellar-serpent-sdk`, PUBLIC, remote
  `origin` over SSH.
- **K9** Shapes: `build_file(examples/shapes.py)` at main → 4,233 B,
  sha256 `01e4cdfb…`, declared protocol 20; the DEPLOYED fixture bytes →
  4,171 B, sha256 `6a9dd135…6e33`. They differ, as D14 requires until the
  redeploy.
- **K10** The bounty board (Elliot's commit 4b25cd0, 2026-09-10, +7671bcb
  lint fix): compiles to 6,493 B, protocol 22 (constructor), needs memory,
  14 host fns; 5/5 tests pass on both legs in 1.3 s; `mypy --strict` on
  `sandbox/bounty_board.py` is CLEAN (the three strict errors are all in
  `test_bounty_board.py`: two "Cannot infer type of lambda", one
  `record.posted_at` on `object`); `ruff format --check` would reformat
  the contract file (line length). Promotion cost is therefore small and
  concentrated in the test file.
- **K11** `stellar keys ls` shows existing identities (several; Elliot
  picks one at deploy time). The local `~/.stellar` config is flagged as
  stale by the CLI ("Run `stellar config migrate`") — a doctor-visible fact
  and a deploy-session pre-step, not G code.
- **K12** `host/target` is 2.2 GB locally after F's builds. CI build time
  for `soroban-env-host` from source is UNMEASURED; the plan measures it
  in a probe before choosing cache strategy (§E6).
- **K13** SPT2004 exists with intent "Local/param shadows a param, module
  constant, import, or type name" (`codes.py:415-420`) — O-HYG5's honest
  code, no registry edit needed.
- **K14** Gates at main 7671bcb: `ruff check .` clean; `ruff format --check
  src tests examples` 172 files clean; `mypy --strict` 170 files clean;
  `SERPENT_REQUIRE_REAL_HOST=1 pytest -q` **4614 passed / 7 skipped** in
  55 s (the 7: two `real_unrunnable` rows, the spike.wasm-anchored tests,
  and the declared TTL skips).

---

## C. WHAT EXISTS IN THE REPO TODAY (main `7671bcb`)

- **C1** No CLI: no `serpent/cli*`, no `[project.scripts]`. Three
  script-shaped entry points exist: `python -m serpent.testing.testnet
  record --contract … --out … <no-arg methods>` (argparse, RECORD gate in
  `main()` only, D13); `python -m serpent.compiler._render_docs`
  (regenerates `docs/subset.md`); `sandbox/compile.py` (O-CLI2).
- **C2** Emitter public API (`serpent.emitter.__all__`): `build_file(path,
  *, target_protocol=None, meta=…, version=…, validate_external=None)`,
  `build_wasm(compiled, …)`, `BuildResult` with fields `wasm`,
  `declared_protocol`, `target_protocol`, `exports`, `imports`,
  `runtime_parts_linked`, `needs_memory`, `pool_size`, `scratch_size`,
  `module_size`; `CompileError.render(lines)` renders located diagnostics;
  `BuildLimitError`, `EmitError`, `CompilerBugError`. `serpent.emitter.
  printer.disassemble(wasm) -> str` (WAT-style, calls by name).
  `serpent.emitter.validate` exposes `iter_sections(wasm)`, `read_uleb`,
  `read_name`, `validate_internal`, `validate_external(wasm) -> bool |
  None` (None = wasm-tools absent). `compile_module(source, path, *,
  target_protocol)` is the single frontend entry (D-C "compile_expression
  retired").
- **C3** `serpent.spec` BUILDS the three custom sections
  (`build_env_meta`, `build_spec_entries`, `build_meta`) and has NO
  decoder. `inspect` needs `stellar_sdk.xdr.SCEnvMetaEntry`, a stream of
  `SCSpecEntry`, and a stream of `SCMetaEntry` — the same XDR classes,
  read direction; the emitter's `sections.py` names the section strings
  (`contractenvmetav0`, `contractspecv0`, `contractmetav0`).
- **C4** `pyproject.toml`: `name = "serpent"`, `version = "0.0.1"`,
  `dependencies = []`; extras `spec = ["stellar-sdk>=15,<16"]`, `testing =
  ["stellar-sdk>=15,<16", "pytest>=8"]`; dev group adds pytest-cov,
  hypothesis, ruff, mypy, stellar-sdk, `wasmtime==48.0.0`; `uv_build`
  backend; the `real_host` marker registered; mypy `files = ["src",
  "tests", "examples"]`, `mypy_path = ["host"]`; ruff `src = ["src",
  "tests", "spikes"]` with the documented reasons NOT to add `examples/`.
- **C5** `.github/workflows/ci.yml`: ONE job `test` (py 3.11/3.12/3.13):
  `uv sync --all-groups`, ruff check, ruff format check (src tests
  examples), `mypy --strict`, wasm-tools 1.258.0 from the prebuilt tarball
  (exact pin, hand-bump comment), `uv run --frozen pytest -q`. No Rust; the
  real-host tests SKIP in CI today (the loud-skip mode, F U2). `docs/
  testing.md` already promises "CI's Rust job sets the switch" (O-CI1).
- **C6** `docs/`: `subset.md` (92 KB, H1 "The serpent subset", §1 "What
  compiles", §2 "What rejects" by SPT band; generated, byte-drift-tested);
  `testing.md` (10 KB, the four tiers, the rebuild command, the marker,
  the divergence vocabulary, tier-3 recording); `gen_subset.py`;
  `superpowers/` (specs, plans, decisions.md, process.md — the planning
  record, never a docs-site page). No `mkdocs.yml`.
- **C7** Six examples and the inventories a seventh must join (E2 plan
  Task 8's checklist, still exact): (1) `tests/unit/test_emitter_end_to_
  end.py` `EXAMPLE_*` + `EXAMPLES` tuple (asserted == the directory) and
  `CONSTRUCTOR_BEARING` by name (the bounty board HAS `__init__`, so it
  joins that set — floor 22); (2) `tests/goldens/wasm/<stem>.wat.txt`
  self-snapshot; (3) `test_emitter_printer.py` `FIXTURE_SOURCES`; (4)
  `test_harness_hostfns.py` `_FIXTURES` (reads `sandbox/counter.py` and
  `sandbox/hello_world.py` directly — "sandbox/ itself must not be
  touched" applies to THOSE two files); (5) `test_frontend_fuzz.py`'s
  EXACT sorted `examples` list; (6) `tests/unit/test_examples.py` tier-1 +
  mini-host two-leg tests per example; (7) `tests/real_host/test_examples_
  real.py` real-leg tests per example. mypy/ruff scope is automatic (D4).
- **C8** `sandbox/`: README (stale, O-DOC4), `compile.py` (O-CLI2),
  `counter.py`, `hello_world.py`, `storage.py`, `guestbook.py`,
  `rolodex.py`, `bounty_board.py` + `test_bounty_board.py` (outside
  `testpaths`, run explicitly; exercises `RealContract.events_for_
  sequence()`, `.storage("persistent"|"temporary").get/ttl`, `.auths()`,
  `RealHostError.underlying`, `Env(auths=…)`/`deploy`/`env.frame()`/
  `env.advance()`; derives CONTRACT strkeys per role via sha256 — the
  D17 pattern). `*.wasm` git-ignored.
- **C9** Hygiene census today: SPT4012 text as O-HYG1; `_HELP` keys in
  code order (O-HYG3 may already be closed); the stale must_reject "105"
  count from E2 attn is GONE (grep finds no 105/111/112 in
  `test_must_reject.py`) — not a G item; `_BRIDGED_RAISE_SOURCES` tuple at
  `test_bridging_completeness.py:508` (O-HYG4); the `reserved[name]`
  overwrite at `frontend.py:441-443` (O-HYG5); `FullHost.obj_cmp`
  decodes small forms first and delegates to tier-1 `val_cmp` (O-MOCK1);
  `from_` in `examples/allowance_token.py:177,280` (O-DOC2).
- **C10** Tier 3 today: `tests/real_host/fixtures/testnet/shapes/
  {kind,palette,is_pinned,area}.json` + `deployed.wasm` (4,171 B);
  `DEPLOYED_SHA256 = 6a9dd135…6e33`; `B1_DIVERGENCE = {"area": U32(10)}`;
  the differs-until-redeploy test at `test_testnet_fixtures.py:182`; the
  header test fails loudly on drift; the recorder refuses a wasm-hash
  mismatch against the chain's instance executable; it records NO-ARGUMENT
  methods only. `ENV_SCENARIOS._ADMIN = Address(SHAPES_CONTRACT)` (D17).
- **C11** Protocol constants: `_protocol.DEFAULT_TARGET_PROTOCOL = 27`,
  `BASE_PROTOCOL = 20`, `CONSTRUCTOR_MIN_PROTOCOL = 22`,
  `compute_protocol_floor`, `check_protocol_target`; `_codegen.PINNED_TAG
  = "v28.0.2"` with the upstream blob SHA test. `serpent.testing.
  DEFAULT_PROTOCOL = 28`.
- **C12** Promise-sweep nets (`tests/unit/test_no_stale_promises.py`):
  three nets ("sub-plan E"; the union/enum surface-denial gate; "sub-plan
  F"/"tier 2b"/"F's" over src/tests/examples/docs excluding docs/
  superpowers), text-keyed allowlists. Live "G" promises in the tree today:
  `test_testnet_fixtures.py:67,182` and `fixtures/testnet/README.md:71`
  ("retires at the next approved deployment (G)"); `docs/testing.md`
  ("CI's Rust job"); two historical "sub-plan G's wave 1" comments
  (`test_emitter_end_to_end.py:95`, `test_harness_hostfns.py:991`) that
  describe M1-E's examples and read as history.
- **C13** `test_public_api.py` pins `serpent.__all__` (40 names) exactly
  and that `import serpent` does not load `stellar_sdk`; `test_host_
  bindings.py` diffs env.json against the upstream blob (S7's CI diff,
  already inside pytest).
- **C14** `test_core_zero_dep.py`: walks every `src/serpent/**/*.py` NOT
  under `EXEMPT = (spec/, testing/)` and asserts imports are stdlib +
  serpent only (AST walk — a function-local `import stellar_sdk` is still
  an import node); separately asserts neither exempt subpackage is
  reachable from the package root. A `serpent.cli` that imports
  `serpent.spec`/`stellar_sdk` anywhere must be a THIRD exempt entry with
  the same not-reachable-from-root test.
- **C15** Signing: 1Password SSH signing responded within the 40 s window
  for 7671bcb (signed). The fallback procedure stands (process.md).

---

## D. THE PROPOSED ARCHITECTURE (the smallest thing that makes S1/S5/S9 true)

### D.1 `serpent.cli` — one stdlib module, three subcommands, lazy heavy imports

- `src/serpent/cli.py` (S4's name; a package only if it outgrows ~500
  lines). `argparse`, no third-party CLI framework (§E1). `[project.
  scripts] stellar-serpent = "serpent.cli:main"`; `main(argv=None) -> int`
  so tests drive it in-process (the `testnet.main` precedent, C1).
- **Import discipline** (D1/C14): `cli.py` imports only stdlib +
  `serpent` core at module load; `serpent.emitter`/`serpent.spec`/
  `stellar_sdk` are imported INSIDE `build`/`inspect`, and an
  `ImportError` there becomes exit code 3 with the one-line remedy
  (`uv tool install "serpent[spec] @ …"` / `pip install "serpent[spec]"`).
  `cli.py` joins the zero-dep gate's EXEMPT tuple AND the not-reachable-
  from-root test (§E2). `doctor` runs on a bare install by construction.
- **Exit codes**: 0 success; 1 the contract was REJECTED (rendered
  diagnostics via `CompileError.render`, exactly what `compile.py` prints
  today) or `inspect` found the artifact malformed; 2 usage (argparse's
  own); 3 environment (missing extra, missing file, unwritable `--out`).
  `CompilerBugError` is NOT caught (a traceback is the honest output for
  an invariant break).
- **`build <contract.py>`**: `--out PATH` (default `<stem>.wasm` beside
  the source, `compile.py`'s convention); `--meta k=v` repeatable (D5;
  reserved keys → exit 3 with the `ValueError` text); `--version STR` (the
  `contractmetav0` `version` entry, D5); `--target-protocol N` (D6; a
  gated fn above it is a located SPT6001 → exit 1); `--no-external-
  validate` / `--require-external-validate` (D7's three-way: default runs
  wasm-tools when present); `--quiet`; `--json` (one object with the
  build-line facts). The build line (human mode): path, bytes, sha256
  (= the on-chain wasm hash, D-E7), declared protocol, target (or
  "floor"), imports count, runtime parts, memory yes/no, external
  validation ran/skipped.
- **`inspect <artifact.wasm>`** (§E3): section inventory (id/name, byte
  size) via `iter_sections`; sha256; imports (env/name, with each fn's
  protocol gate from `serpent._host.bindings`) and exports; the DECLARED
  protocol decoded from `contractenvmetav0` (`SCEnvMetaEntry`, K5's
  "Protocol: v22"); the RECOMPUTED floor from the imports via
  `compute_protocol_floor` plus the D9 constructor gate when
  `__constructor` is exported — printed side by side with a
  `MISMATCH` marker when they differ (the honest-declaration check no
  other tool performs; for a non-serpent artifact the marker is
  informational); `contractspecv0` summarised by kind (functions, structs,
  unions, int enums, error enums, events — names only, the XDR kind order
  of E7) with a pointer to `stellar contract info interface --wasm` for
  the rendered interface; `contractmetav0` pairs (name, serpentver,
  version, user pairs); `--wat` appends `disassemble(wasm)`; `--json`.
  `inspect` never decodes `Val` words (S10).
- **`doctor`** (§E4): a table of checks, each `ok`/`warn`/`fail` with a
  remedy line: Python ≥ 3.11 (fail); `serpent` version (ok); the `spec`
  extra importable + `stellar_sdk` version within `>=15,<16` (fail when
  absent — `build` cannot run); `wasm-tools` on PATH + version vs the
  shared pin constant (warn when absent or different, O-CI2); `stellar`
  CLI on PATH + version (warn), and whether `stellar-serpent` itself is
  discoverable — `shutil.which("stellar-serpent")` resolves to THIS
  interpreter's script (warn: "installed but `stellar serpent` will not
  find it"); `serpent_host` importable + its protocol vs `PINNED_TAG`'s
  major (info/warn; the REBUILD_COMMAND as the remedy); the pinned
  `env.json` tag; `DEFAULT_TARGET_PROTOCOL`. Offline by default; a
  `--network testnet|mainnet|<rpc-url>` flag adds one read-only
  `getVersionInfo` and compares protocols (M2 if it grows). Exit 0 unless
  a `fail` row exists. `--json`.
- **Tests**: in-process `main([...])` tests for every subcommand and exit
  code; a golden of `--help` text per subcommand (the CLI reference page
  is generated FROM these goldens, §D.3, so docs cannot drift); `build`
  over all seven examples asserting sha256 == `build_file`'s; `inspect`
  over the seven builds AND the deployed shapes bytes (declared 20 ==
  recomputed 20; the bounty board declared 22 == recomputed 22 via the
  constructor gate); `doctor` under monkeypatched PATH/imports for every
  row state; a `uv tool install --from . serpent[spec]` smoke in CI
  (F.1.1) that runs `stellar-serpent doctor` and `stellar-serpent build
  examples/counter.py` from OUTSIDE the repo venv.

### D.2 CI — three jobs, one file

- `test` (existing, unchanged shape).
- `real-host` (NEW, O-CI1): ubuntu, Python 3.11 only; `dtolnay/rust-
  toolchain@stable` pinned to a toolchain string; `Swatinem/rust-cache`
  keyed on `host/Cargo.lock`; `uv sync --all-groups` → `VIRTUAL_ENV=$PWD/
  .venv uvx maturin develop --release --manifest-path host/Cargo.toml` →
  the cargo gates from `host/` → `SERPENT_REQUIRE_REAL_HOST=1 uv run
  --no-sync pytest -q` (D10's order). An assertion step greps the pytest
  summary for the real-host count (≥ the count at main today) so the job
  cannot pass with the marker silently unselected (F.1.4).
- `docs` (NEW): `uv run --group docs mkdocs build --strict`. NO deploy step
  runs by default; a `workflow_dispatch`-only deploy job (Pages) exists
  for Elliot to trigger after enabling Pages (D16 — a publish).
- `cli-install` (fold into `test` or its own small job): the `uv tool
  install` smoke of D.1.
- wasm-tools pin (O-CI2): ONE constant home (`serpent.emitter.validate.
  WASM_TOOLS_PIN`) that `doctor` reads and a unit test asserts equals
  ci.yml's `WASM_TOOLS_VERSION` (regex over the workflow file); bumping is
  a documented manual step (§E13).

### D.3 The docs site — mkdocs-material, sources rendered from the code

- `mkdocs.yml` at the root; `docs_dir: docs`; `exclude_docs: superpowers/
  **` (the planning record is never a page); `docs` dependency group:
  `mkdocs-material`, `mkdocstrings[python]`, pinned ranges (K7).
- Nav: **Home** (from README's shape: what it is, status, honest
  boundary); **Getting started** (install `uv tool install "serpent[spec]
  @ git+https://github.com/ElliotFriend/stellar-serpent-sdk"`, first
  contract, `stellar serpent build`, deploy with STOCK `stellar contract
  deploy`, invoke with `stellar contract invoke`); **The subset**
  (`subset.md` as-is); **Examples** (one page per example rendering the
  source with `pymdownx.snippets` from `examples/*.py` — a page cannot
  drift from the file, O-DOC1); **Testing** (`testing.md`); **CLI
  reference** (generated from the `--help` goldens, D.1); **API
  reference** (mkdocstrings over `serpent`'s 40 public names and
  `serpent.testing`'s 12, `show_source: false`); **Design** (links to the
  spec, roadmap, decisions — as GitHub links, not pages); **Deployments**
  (the committed record of O-DEP3).
- `mkdocs build --strict` is the gate (broken links, missing pages fail).
- A drift test: every `examples/*.py` has a docs page and every docs
  example page names an existing file (O-HYG8's spirit, docs side).

### D.4 The seventh example — `examples/bounty_board.py` (U1)

- `git mv sandbox/bounty_board.py examples/bounty_board.py`; ruff-format;
  the module docstring's last paragraph re-pointed (it names `sandbox/
  compile.py`, which D.1 supersedes). Content otherwise UNCHANGED — it is
  Elliot's contract and already strict-clean (K10).
- The test splits along the existing per-example convention (C7 items 6
  and 7): the tier-1 + mini-host two-leg test and the pinned literal
  sequences into `tests/unit/test_examples.py`; the real-leg two-leg
  tests (happy path, error codes, TTL lapse, the auth trap, typed storage
  read-back) into `tests/real_host/test_examples_real.py` — or a dedicated
  pair `test_example_bounty_board.py`/`test_example_bounty_board_real.py`
  if the shared modules would grow past readability (plan author's call,
  stated). The `_contract_address(label)` helper stays test-local (§E7).
- Joins all seven inventories (C7) + `CONSTRUCTOR_BEARING`; a new golden
  `tests/goldens/wasm/bounty_board.wat.txt`; `test_examples_is_a_flat_
  directory_of_modules` catches an omission from `EXAMPLES` and the NEW
  cross-inventory test (O-HYG8) catches the other five.
- `sandbox/test_bounty_board.py` is removed with the move (its content
  lives on in the two test modules); `sandbox/README.md` points at
  `examples/bounty_board.py` as "the one contract that touches every M1
  surface".

### D.5 The hygiene pass (one Sonnet task per theme, Opus where semantics move)

- Registry wording (D2, controller-sanctioned list in the plan): SPT4012
  (O-HYG1); origin-field normalisation (O-HYG2); SPT3020/SPT3014 construct
  lists; SPT1xxx help cites `docs/subset.md`; limit numbers out of intent
  strings (O-HYG7). Snapshot pins + `docs/subset.md` regen in the SAME
  commit. No new codes, no renumbering, no meaning change.
- Frontend (Opus): O-HYG5 — a parameter whose name is a declared type
  emits SPT2004 at the parameter (K13); a must_reject fixture; `frontend.
  py:441-443` stops overwriting the module reservation.
- Gates: O-HYG4 — `_BRIDGED_RAISE_SOURCES` becomes DERIVED (walk the
  declaration-layer modules' ASTs for `raise` sites and assert each is
  bridged or allowlisted by text), so a new raise cannot hide; O-HYG8 —
  one cross-inventory test over the five hand-kept lists.
- The mock (Opus, O-MOCK1): `FullHost(strict_obj_cmp=True)` DEFAULT: two
  small Vals through `obj_cmp` raise the host's `("Value",
  "UnexpectedType")`-class trap; the deployed shapes bytes now trap on
  `area` under the mock too (a regression fixture that makes the mock
  agree with the host on the one thing it was silently wrong about);
  `strict_obj_cmp=False` retained for archaeology.
- `examples/shapes.py` `is_pinned` docstring (O-HYG6) → shapes golden
  regen in the same commit; and the shapes contract's docs page.
- O-CLI3 per §E10; O-DOC2 documented in the examples page for
  allowance_token and in the subset doc's event section.

### D.6 The M1-end deployment (LAST task; the hard stop, S9/D16)

Prepared so the approval session is short and mechanical, all commands
run by Elliot or under his explicit in-session go:

1. Pre-flight (no chain writes): version bump to `0.1.0` (§E11) committed
   FIRST so the artifacts carry `serpentver: 0.1.0`; `stellar-serpent
   doctor` clean; `stellar-serpent build examples/shapes.py` and `…
   bounty_board.py` → the two wasm files + sha256s recorded; `stellar
   config migrate` if the CLI still flags the old config (K11); identity
   chosen from `stellar keys ls`; the identity's testnet balance checked
   read-only.
2. **HARD STOP — Elliot approves.** Then: `stellar contract deploy --wasm
   shapes.wasm --source <id> --network testnet` (no constructor);
   `stellar contract deploy --wasm bounty_board.wasm --source <id>
   --network testnet -- --admin <the identity's G… address>` (the on-chain
   admin is Elliot's own account; the CONTRACT-only rule (D17) is a
   `RealEnv` mock limitation, not a chain one). Optionally, in the same
   approved session, seed state with `stellar contract invoke … -- post
   --poster <G…> --reward 50 --priority 2` so `open_ids`/`total_posted`
   record something other than an empty board (§E15).
3. Fidelity: `stellar contract info hash --id <C…> --network testnet` ==
   the local sha256 for BOTH (Phase 0's rule; the recorder enforces it
   again).
4. Re-record tier 3: shapes (`kind palette is_pinned area` → `area` now
   answers `U32(10)`; `B1_DIVERGENCE` emptied; the differs-until test
   FLIPS to equality; `DEPLOYED_SHA256` + `deployed.wasm` + README table
   updated); bounty board (`total_posted open_ids`, the no-arg pair the
   recorder supports; a second fixture directory). The `real_host`
   replay tests run green.
5. Record: `docs/deployments.md` (commands, ids, shas, ledger, date —
   DEPLOY_LOG.md's shape, O-DEP3); README + process.md state; the `_ADMIN`
   comment (D17); local annotated tag `v0.1.0` (NOT pushed — Elliot
   pushes, D16).

### D.7 Closing tasks

A fourth promise net for "sub-plan G" / "(G)" / "G's" over src/tests/
examples/docs (C12's live mentions all retire in D.6 or become history);
README rewrite (M1 complete, install, quickstart, the seven examples, the
tiers, the deployments); `sandbox/README.md` + `compile.py` (O-CLI2/
O-DOC4); `spikes/README.md` (U2); process.md state → "M1 COMPLETE; NEXT:
M2"; decisions.md entries for every §E ruling; the attention file for the
Fable final review.

---

## E. OPEN QUESTIONS FOR THE CONTROLLER (recommendation first)

- **E1 CLI framework** — RECOMMEND stdlib `argparse`. Zero-dep holds (D1)
  and `doctor` must run on the barest install; `testnet.main` already sets
  the in-repo precedent. click/typer would add the first runtime
  dependency the core package has ever had. Reversal: module-local.
- **E2 Where `cli.py` sits relative to the zero-dep gate** — RECOMMEND:
  module-level imports stdlib + serpent core only; `serpent.emitter`/
  `serpent.spec` imported inside `build`/`inspect` with the exit-3 remedy;
  `cli.py` added to `EXEMPT` (C14) with the same not-reachable-from-root
  test `spec`/`testing` have, because the AST walk sees function-local
  imports too. Alternative (a `cli` extra aliasing `spec`) adds a name
  users must learn for no new dependency. Docs install line: `uv tool
  install "serpent[spec] @ git+https://github.com/ElliotFriend/stellar-
  serpent-sdk"`. Reversal: one tuple entry.
- **E3 `inspect`'s remit vs `stellar contract info`** — RECOMMEND serpent-
  specific facts only (D.1): sections + sizes, sha256, imports WITH
  protocol gates, exports, declared-vs-RECOMPUTED protocol with the
  constructor gate (D6/D9), spec entry names by kind, meta pairs, `--wat`;
  and a printed pointer to `stellar contract info interface` for the
  rendered interface (K5). Re-rendering the interface would be a second
  renderer of one XDR (S10's drift class). Reversal: additive.
- **E4 `doctor`'s rows and exit semantics** — RECOMMEND the D.1 list;
  `fail` only for Python version and the missing `spec` extra (the two
  things that make `build` impossible); everything else `warn`/`info`;
  offline by default with `--network` as the one read-only probe; `--json`.
  Reversal: row-level.
- **E5 Docs site scope** — RECOMMEND mkdocs-material + `pymdownx.snippets`
  (examples rendered from source) + `mkdocstrings-python` (API reference
  for `serpent`'s 40 names and `serpent.testing`'s 12), `superpowers/`
  excluded, `--strict` in CI, the CLI reference generated from `--help`
  goldens; NO deployment in G beyond a `workflow_dispatch` Pages job
  Elliot can trigger (D16). Alternative (hand-written API pages) is the
  drift S7 warns about. Reversal: config-level.
- **E6 The Rust CI job's shape** — RECOMMEND a separate `real-host` job
  (py 3.11 only; `dtolnay/rust-toolchain` + `Swatinem/rust-cache` on
  `host/Cargo.lock`; D10's `uv sync` → maturin → `uv run --no-sync` order;
  cargo fmt/clippy/test in the same job; `SERPENT_REQUIRE_REAL_HOST=1`;
  the real-host-count assertion). A plan-time probe MEASURES the cold
  `soroban-env-host` build (K12) and, if it exceeds ~15 min cold, the plan
  states the cache-miss budget rather than pretending. Reversal: workflow-
  level.
- **E7 Bounty-board promotion mechanics** — RECOMMEND `git mv` (one copy;
  the sandbox is a scratch area, C8), tests split along the existing per-
  example convention (D.4), `_contract_address(label)` stays TEST-LOCAL
  (promoting a fake-address helper into `serpent.testing` is new user-
  facing surface — M2 if the M2 dossier wants it), and the contract's
  source is otherwise untouched (Elliot's authorship; the review reads
  it as an example, not a rewrite target). Reversal: file moves.
- **E8 Strict `obj_cmp` on the mock** — RECOMMEND `strict_obj_cmp=True`
  BY DEFAULT (the emitter never emits two-small-Val `obj_cmp` since F Task
  0; a mock that accepts what the host refuses is the S1/M7 class F closed
  everywhere else), with the deployed shapes bytes as the regression
  fixture (they must now trap under the mock as they do on chain) and an
  explicit `False` for archaeology. Opus, because it touches the tier-2a
  oracle's semantics. Reversal: one default.
- **E9 Parameter shadowing of a declared type name** — RECOMMEND a located
  SPT2004 at the parameter (K13: the code's intent already names "type
  name"; no registry edit), a must_reject fixture, `docs/subset.md` regen.
  Accepts strictly SHRINK here, which D-C10's reject-first line permits:
  a parameter named `Status` in a contract declaring `class Status(
  ContractUnion)` is a bug every time. Reversal: delete the check.
- **E10 `DEFAULT_TARGET_PROTOCOL` 27 → 28?** — RECOMMEND KEEP 27 and
  DOCUMENT why: the target is an UPPER BOUND on what a module may use
  (D6); defaulting to the lowest live network's protocol (mainnet, K2)
  means a default build deploys everywhere, while 28 would let a contract
  use a 28-gated host fn and fail on mainnet 27 by default. `build
  --target-protocol 28` opts in; `doctor` prints the default and both
  networks' protocols when `--network` is given. Revisit when mainnet
  moves to 28 (one line, no artifact-hash consequence). Reversal: one
  constant.
- **E11 Version and tag at M1's close** — RECOMMEND bump `serpent.
  __version__`/`pyproject` to `0.1.0` in the pre-flight commit BEFORE the
  deploy builds (so `serpentver: 0.1.0` is what the chain carries, D5),
  and a LOCAL annotated tag `v0.1.0` on the deployment-record commit;
  pushing the tag is Elliot's (D16). `host/` stays `0.0.1` (its version is
  M3's wheel story, D15). Reversal: tag delete before push.
- **E12 Distribution name** — RECOMMEND NO rename in G. `serpent` is taken
  on PyPI (K6), the spec makes the name an M3 decision (S6), G publishes
  nothing, and the git-URL install path (E2) is name-agnostic. Record for
  M3 that `stellar-serpent`, `serpent-sdk`, and `soroban-serpent` were
  free on 2026-09-10, and that the console script `stellar-serpent` is
  fixed by the plugin convention regardless (K4). Reversal: none (nothing
  decided).
- **E13 The wasm-tools drift watch (O-CI2)** — RECOMMEND one shared pin
  constant read by `doctor` and asserted equal to ci.yml's value by a unit
  test; bumping is a documented manual step in `docs/testing.md`. NO
  scheduled network job (noise, and a failing cron on a personal repo is
  what gets ignored). Reversal: additive.
- **E14 The G promise net and the F minors** — RECOMMEND a fourth net
  ("sub-plan G", "(G)", "G's"; text-keyed allowlist) landing in the
  closing task after D.6 retires the live mentions; the plan lists the F
  minors G takes (O-HYG10's four candidates) and names the rest as M2/M3
  host-fact candidates. Reversal: allowlist entries.
- **E15 Tier-3 fixtures for the bounty board** — RECOMMEND recording the
  two no-argument methods the recorder supports today (`total_posted`,
  `open_ids`) against whatever state the approved session leaves (empty
  board, or seeded with one `post` if Elliot chooses); argument-taking
  recording is M3's live-suite item (D13). The fixtures pin what the chain
  says, either way. Reversal: re-record.
- **E16 `sandbox/` disposition** — RECOMMEND keep it as Elliot's scratch
  area: `compile.py` becomes a five-line pointer at `stellar-serpent
  build` (or is deleted — plan author's call, stated) and the README is
  rewritten around the CLI, the real host, and the seven examples;
  `sandbox/counter.py` and `hello_world.py` stay UNTOUCHED (C7 item 4
  reads them). Reversal: prose.

---

## F. RISKS

### F.1 Where G can be hollow or silently wrong

- **F.1.1 A CLI that only works from the repo venv.** `uv run stellar-
  serpent` proves nothing about `uv tool install`. The CI smoke installs
  the tool from the checkout into an isolated tool environment and runs
  `doctor` + `build` from another directory (D.1 tests).
- **F.1.2 `inspect` recomputing the floor wrongly.** Reuse
  `compute_protocol_floor` over the import NAMES plus the D9 constructor
  gate — never a second table. Pin: the seven examples' declared ==
  recomputed; the deployed shapes bytes declared 20 == recomputed 20; a
  hand-assembled module with a 28-gated import recomputes 28.
- **F.1.3 Docs drifting from code.** Examples via snippets from source;
  the subset doc already byte-drift-tested; the CLI reference from
  `--help` goldens; `mkdocs build --strict`; the examples-page drift test.
- **F.1.4 The Rust job passing vacuously.** `SERPENT_REQUIRE_REAL_HOST=1`
  refuses the session when the extension is absent (C5), but a job that
  runs `pytest tests/unit` by mistake would still pass — assert the real-
  host count in the summary.
- **F.1.5 A deployment without its paperwork.** The fixtures README's
  header test fails loudly on drift (C10), so an un-re-recorded shapes
  fixture cannot go unnoticed; the bounty-board fixture directory has no
  such pre-existing test — D.6 step 4 adds it before the session.
- **F.1.6 Promotion breaking the strict gates.** K10 measured the cost:
  the contract is clean; the test file's three strict errors are rewritten
  away by the split (typed step tuples, `record` narrowed by `isinstance`).
- **F.1.7 Plugin dispatch on CLI 28.** K4 is proven on 27.1.0 only. The
  plan either has Elliot upgrade locally (his install) or has the CI smoke
  install `stellar-cli 28.0.0` from its release tarball and run `stellar
  serpent doctor` through the plugin path. Recommend the CI route (no
  dependence on Elliot's machine).
- **F.1.8 The zero-dep gate vs `cli.py`.** A forgotten EXEMPT entry fails
  the gate loudly (good); a MIS-placed exemption (e.g. exempting `cli.py`
  but importing it from `serpent/__init__.py`) is what the not-reachable-
  from-root test catches — both halves are needed.
- **F.1.9 Hygiene edits that move goldens.** Every registry wording edit
  regenerates `docs/subset.md` and snapshot pins in the SAME commit (D2);
  the `is_pinned` docstring regenerates the shapes golden — one commit,
  stated in the plan.
- **F.1.10 The seeded-state fixture.** If Elliot seeds a bounty in the
  approved session, `open_ids` records `[1]` against a board whose later
  state can change (anyone can `post` on testnet) — the fixture is a
  RECORDING and says so (tier 3's definition, `docs/testing.md`); the
  header test compares the wasm hash, not live state.

### F.2 Checks that belong in G's own test plan

Exit-code tests per subcommand; `--help` goldens; `build` sha256 ==
`build_file`; `inspect` declared == recomputed on seven builds + the
deployed bytes + a gated hand-assembled module; `doctor` row states under
monkeypatched environments; the tool-install smoke; the wasm-tools pin
equality test; the real-host count assertion; `mkdocs build --strict`; the
examples-page drift test; the cross-inventory test; the SPT2004 shadowing
fixture; the strict-`obj_cmp` regression on the deployed bytes; the
re-recorded shapes fixtures (four) + the bounty-board pair; the flipped
differs-until test; the fourth promise net.

### F.3 Process risks

- G touches every top-level directory (src, tests, examples, docs, host's
  README, sandbox, spikes/README, .github, pyproject) — the widest blast
  radius of any sub-plan; the Fable final review needs the attention file
  to enumerate every inventory and golden that moved.
- The deployment is a HARD STOP with two chain writes plus optional seed
  invocations; the plan's last task is a checklist Elliot executes, not
  an implementer's brief. Nothing in G adds a signing code path (D13).
- Pushes, Pages, the plugin topic, PyPI, and the tag push are all Elliot's
  (D16); the plan says so at each site rather than once.
