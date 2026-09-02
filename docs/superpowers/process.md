# serpent — Working Process & Session Pickup Guide

How this project is built, and how a fresh Claude Code session picks up where
the last one stopped. Written for the agent as much as for Elliot.

## What this project is

**serpent**: a Python SDK for authoring Soroban smart contracts — a
restricted-Python-subset → WASM compiler, all-Python, plus a runtime "tier-1
oracle" that mirrors on-chain semantics exactly. Spec:
`docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`
(§13's verified-facts appendix is REQUIRED READING before touching compiler or
harness code). The M1 roadmap: `docs/superpowers/plans/*m1-roadmap*`.

## State (as of 2026-09-02)

- **Phase 0**: COMPLETE (GO). Testnet contract
  `CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI`.
- **M1-A** (value layer / tier-1 oracle): merged to main.
- **M1-B** (host interface, env.json v28.0.2, spec sections byte-anchored to
  the deployed contract): merged to main.
- **M1-C** (compiler frontend — the whole checking pipeline through
  `compile_module`, 96-code SPT registry, 95 must_reject fixtures, fuzz +
  goldens, generated `docs/subset.md`): merged to main. ~2053 tests.
- **M1-D** (the WASM emitter — `serpent.emitter` with `build_wasm`/
  `build_file`, symbolic-call serialization, the guest runtime library incl.
  128-bit limb arithmetic via the i256 route, ABI prologues, the three
  custom sections, internal + wasm-tools validation, the dev-only wasmtime
  mini-host in `tests/harness/`, the 35-case semantics differential, and
  disassembly snapshots): merged to main 2026-08-28. 3552 tests.
  `contractspecv0` byte-identical to the deployed Phase 0 artifact; the
  registry gained the sanctioned SPT8xxx band (100 codes).
- **M1-E (Env runtime semantics + examples): merged to main 2026-08-31**
  (merge 5731390). The tier-1 Env model (deep-copy isolation, partial
  TTL, deploy/frame/auth, write-resets-TTL); the Event.publish convention
  end-to-end, both spellings, one escape rule; five examples (counter,
  errors, structs, events, allowance_token) each passing the
  compile/tier-1/WASM-same-answers triple; the 59-row ENV_SCENARIOS
  stateful differential (F's tier-2b re-run corpus); the promise sweep +
  the case-insensitive stale-promise gate. Tier-1 `get` ADOPTS raw
  literal defaults through the requested type (decisions.md 2026-08-31).
  Suite 3947/2skip, all gates green on main. Carried obligations live in
  `.superpowers/sdd/2026-08-28-m1e-env-runtime/final-review-attention.md`
  (kept, like M1-C/D's) — F/G/M2 items plus the parked triage.
- **M1-E2 (tagged unions + int enums): merged to main 2026-09-02**
  (fast-forward; 31 commits, all re-signed 2026-09-02, tip 57668f9). The value layer
  (`ContractUnion`/`ContractEnum`, `variant()`/`enumvalue()`), the
  `@contractunion`/`@contractenum` declaration layer, UDT union/enum spec
  entries in the XDR kind order, `MakeUnion` + two `Ty` tags in the
  emitter, `tag()`/`payload()` reads, SPT3021/3022/4026 + six SPT4xxx/5xxx
  codes, the `topic`-marker refusal, the SPT3019 narrowing, the `get`
  overloads, `examples/shapes.py` (sixth example), three ENV_SCENARIOS
  rows (62 total). Final review (Fable) found one silent cross-tier
  divergence (D6 x E9: enum read back as U32) fixed by tier-1 re-typing;
  rulings in decisions.md (2026-09-01 x2). Suite 4222/2skip, all gates
  green on main. Carried obligations live in
  `.superpowers/sdd/2026-08-31-m1e2-unions/final-review-attention.md`
  (kept, like C/D/E's) -- F: harness decoding of union/enum returns,
  container `obj_cmp`, the bridge-gate generalisation, param shadowing;
  G: the sanctioned wording pass (SPT4012, origin fields, `_HELP` order,
  `is_pinned` docstring needs a golden regen), text-keyed allowlist;
  M2: `match` sugar, Option payloads/narrowing, `.value`, `discriminant`
  rename, typed container reads.
- **NEXT: F (testing tiers)**, then G (CLI as a Stellar CLI plugin).
  F inputs decided with Elliot 2026-09-02 (tier-2b build-from-source in
  M1 + wheels at M3; tier 2b an opt-in skipping marker; tier 3 fixture-
  only until approved; embed the env-host matching TESTNET's protocol;
  a deployed shapes.py contract as a tier-3 fixture source) are in
  `.superpowers/sdd/2026-08-31-m1e2-unions/final-review-attention.md` §6. M1 ENDS with
  a user-approved testnet deployment (HARD STOP — Elliot must
  explicitly approve it in-session).

## How each sub-plan runs (the loop that built A, B, C)

1. **Inputs dossier** (if the sub-plan is complex): compile banked items from
   `docs/superpowers/decisions.md` + prior ledgers into a citation-ID'd spec
   (`docs/superpowers/specs/`). M1-C's is the model:
   `2026-08-27-m1c-inputs-dossier.md`.
2. **Plan** via superpowers:writing-plans →
   `docs/superpowers/plans/YYYY-MM-DD-<name>.md`.
3. **Adversarial plan review** on Opus BEFORE execution; triage findings
   (adopt with evidence / dispute / rule — findings are input, not verdicts);
   record rulings in decisions.md; amend the plan.
4. **Execute via superpowers:subagent-driven-development** on a feature
   branch: fresh implementer per task → adversarial task review → fix rounds
   (≤5, warm implementer for rounds 1-3) → scoped re-review. The ledger
   (`.superpowers/sdd/<plan-basename>/progress.md`) is the single source of
   truth for progress, rulings, and carried items — append to it constantly;
   it is what survives compaction.
5. **Final whole-branch review on the most capable model (Fable)**, fed a
   `final-review-attention.md` of accumulated risk-register items → one fix
   wave → local merge to main. No pushes to remotes unless Elliot asks.

## Standing autonomy (granted by Elliot, in force through M1)

"If things are progressing smoothly, keep going through the rest of the M1
phases. If there's a judgment call to make, make a decision, but keep a
record of those decisions for review later."

- Judgment calls with lasting consequence →
  `docs/superpowers/decisions.md` (committed; entry format is in the file).
  Fine-grained task rulings → the sub-plan's SDD ledger.
- **Hard stops that remain**: irreversible/outward-facing actions (pushes,
  publishes, deployments), and the M1-end testnet deployment (explicit
  user approval required). Everything else: decide, ledger, keep moving.
- Review findings are triaged, not obeyed: verify, adopt-with-evidence, or
  dispute — and present the triage.

## Conventions that took rulings to establish (do not re-derive)

- **SPT registry** (`src/serpent/compiler/codes.py`): frozen public API,
  controller-owned. Append-only = no renumber/delete/meaning-reversal;
  wording widenings and new codes happen ONLY by controller sanction, with
  snapshot pins updated in the same commit. Honest-code discipline: a code's
  intent text must fit the shape it reports (SPT3018 = genuine type
  mismatch only; SPT3020 = call arity; SPT1038 = env misuse shapes).
- Subagents never touch codes.py, decisions.md, or spikes/ (frozen Phase 0
  evidence). If a code seems missing, the implementer returns BLOCKED.
- **Model seating**: Sonnet for mechanical/well-specified tasks and scoped
  re-reviews; Opus for semantics-critical implementation AND review (anything
  feeding divergence guards, oracle edits, assembly); Fable for final
  whole-branch reviews. Always set the model explicitly on dispatch.
- **Commit signing**: 1Password SSH signing is flaky. Try signed (~40s
  timeout); on failure `git commit --no-gpg-sign` and append
  `<sha> <subject>` to `.git/unsigned-commits.log`. NOTE: `git log %G?`
  shows N locally because `gpg.ssh.allowedSignersFile` is unset — check for
  a `gpgsig` header (`git cat-file commit <sha> | grep gpgsig`) before
  logging a commit as unsigned. Re-sign command for Elliot:
  `git rebase --rebase-merges --exec 'git commit --amend --no-edit -S' <base>`.
- Gates on every task, non-negotiable: `uv run pytest -q`,
  `uv run mypy --strict src tests` (zero-plugin), `uv run ruff check .`,
  `uv run ruff format --check src tests`.
- Conventional commits, no emoji, no em dashes, Oxford commas. AI
  attribution trailer stays on model-authored commits.

## Picking up in a new session

1. Memory recall gives the project pointer
   (`project_serpent_python_soroban_sdk.md`); this file is the detail.
2. Read `docs/superpowers/decisions.md` end-to-end (it is the ruling
   history) and the most recent SDD ledger under `.superpowers/sdd/`.
3. If mid-sub-plan: the ledger's first line names its plan; tasks with a
   `complete` line are DONE — resume at the first task without one.
4. If starting a sub-plan (F is next): begin at step 1 of the loop above
   (dossier if warranted → plan → Opus plan review → execute). M1-C's
   carried obligations for D are in
   `.superpowers/sdd/2026-08-27-m1c-compiler-frontend/final-review-attention.md`
   §"Obligations carried OUT of M1-C" — the D dossier/plan MUST ingest them
   (divergence `unreachable`, runtime_parts ratification, i256 neither-set,
   all_static-is-not-a-licence, needs_memory contract).
5. Sub-plan E obligations are in the same file (escape-list additions,
   token_style E12 revert, time algebra).

## Key artifacts map

| Artifact | Where |
|---|---|
| Design spec (+§13 gotchas) | `docs/superpowers/specs/2026-08-26-serpent-*-design.md` |
| Decision log (committed) | `docs/superpowers/decisions.md` |
| M1-C dossier (frontend truth) | `docs/superpowers/specs/2026-08-27-m1c-inputs-dossier.md` |
| Plans | `docs/superpowers/plans/` |
| SDD ledgers, briefs, reviews | `.superpowers/sdd/<plan-basename>/` (git-ignored) |
| Generated subset docs | `docs/subset.md` (byte-drift-tested; regen via `python -m serpent.compiler._render_docs`) |
| Unsigned commits | `.git/unsigned-commits.log` |
| Frozen Phase 0 evidence | `spikes/` (read-only forever) |
