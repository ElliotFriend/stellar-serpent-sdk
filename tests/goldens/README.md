# Goldens

Byte-fixed regression goldens for `serpent.spec` section builders (added by
Task 4 of the M1-B plan). Every golden here belongs to exactly one
provenance class, stated below, because the two classes carry different
weight as evidence and must never be conflated:

## Provenance classes

- **ON-CHAIN-verified**: bytes were compared, in Phase 0, against output
  observed directly from a live Soroban network (RPC/CLI talking to a
  running node). This is the strongest guarantee -- it proves the bytes are
  what a real network actually accepts/returns, not just what one local
  toolchain produces.

- **RUST-SDK-BYTE-COMPAT-verified**: bytes were compared `==` against an
  artifact produced locally by the official Rust toolchain (`stellar
  contract build`), but were *not* independently confirmed against a live
  network. This proves serpent's output matches the Rust SDK's output; it
  does not by itself prove the network accepts that output (though the
  Rust SDK's own on-chain track record makes that a reasonable inference).

An earlier draft of this plan mislabeled a RUST-SDK-BYTE-COMPAT golden as
ON-CHAIN-verified; the M1-B adversarial review caught this (ruling "B2
PARTIALLY DISPUTED" in `../../.superpowers/sdd/2026-08-27-m1b-host-interface/progress.md`)
-- the golden itself was not fabricated, just misattributed. The fix is
this README plus a second, correctly ON-CHAIN-anchored golden/check.

## The goldens (added by Task 4)

- `env_meta_27.bin` (12 bytes) -- **ON-CHAIN-verified**. `env_meta(27)`'s
  bytes, confirmed in Phase 0 against a live network.

- `counter_spec.bin` (64 bytes) -- **RUST-SDK-BYTE-COMPAT-verified**. The
  Phase 0 `get`/`increment` counter-interface spec payload, reconstructed
  from the recorded entry-construction logic in
  `spikes/spike1/reference/mkmeta.py` and compared byte-for-byte against a
  `stellar contract build` artifact in the landscape spike. Regenerate it
  from that recorded logic; if the reconstruction is not exactly 64 bytes,
  that is a signal to stop and report BLOCKED rather than pin a wrong
  golden.

- A second, **ON-CHAIN-anchored** check (not a separate stored `.bin` file):
  `test_sections.py` builds spec entries for spike1's real, deployed
  interface (`setup(counter_limit: U32) -> None`, `bump() -> U32`, the
  `Settings` struct, and the `LimitExceeded = 7` error case) and asserts
  they are **byte-identical to the `contractspecv0` section of
  `spikes/spike1/spike.wasm`**, which is stronger than the structural
  equality originally planned and turned out to be achievable (serpent takes
  its docs from the same docstrings the spike's frontend did). Task 4 landed
  it as three linked assertions:

  1. `sha256(spike.wasm)` equals the hash of the wasm **fetched back off
     testnet** (`bc2e8063…`, recorded in `spikes/spike1/DEPLOY_LOG.md`) --
     the anchor; without it the rest is only a local comparison.
  2. serpent's `build_spec_entries(...)` output equals that wasm's
     `contractspecv0` payload byte for byte (plus a field-by-field
     structural assertion, so a failure says *what* diverged).
  3. `stellar contract info interface --wasm spikes/spike1/spike.wasm`
     renders exactly the interface recorded in `DEPLOY_LOG.md` (which that
     log shows identical to the `--id` render taken from the live network).
     Only trailing newlines are normalized. Skipped when the `stellar` CLI
     is absent (CI has no such binary), never silently passed.

## `ir/` -- compiler IR snapshots (added by Task 11c of the M1-C plan)

`ir/*.ir.txt` are a **third, weaker class: SELF-SNAPSHOT**. They are neither
ON-CHAIN-verified nor RUST-SDK-BYTE-COMPAT-verified, and they must never be
cited as evidence that serpent's output is *correct*. They record what the
frontend currently lowers a source to, so that a change to lowering arrives
as a reviewable diff instead of a silent behavioural change (dossier F.2.10).
Their evidentiary weight is exactly "this used to be the answer, and someone
reviewed the change" -- no more.

The distinction matters here more than usual, because one of them
(`spike1_reauthored.ir.txt`) *looks* on-chain-anchored and is not: the
Phase 0 anchor is the eight-host-function assertion in
`tests/unit/test_frontend_goldens.py`, which compares against names copied
out of `spikes/spike1/ACCEPTANCE.md` and `harness.py`. The IR snapshot beside
it is a self-snapshot like the rest.

- Source of truth: `tests/unit/test_frontend_goldens.py` -- `EXAMPLE_NAMES`
  names the examples, `render_functions` is the rendering, and each file is
  `render_functions(compile_module(source).functions)`.
- Regenerate all of them with:

      SERPENT_REGEN_GOLDENS=1 uv run pytest tests/unit/test_frontend_goldens.py

  The test writes the file and then compares, so a regeneration run is also
  a passing run. **Read the diff.** A golden diff is a change to what
  sub-plan D will emit.
- Every node's `Loc` is deliberately omitted from the rendering (the test
  module's docstring says why: location correctness is asserted exhaustively
  by the 95 `must_reject/` fixtures and the fuzz suite, and line numbers
  would make every unrelated source edit rewrite the whole file). Nothing
  else is omitted, and no golden may carry an address, an `id()`, or an
  absolute path -- `test_goldens_have_no_identity_leaks` enforces that.

## `wasm/` -- disassembly snapshots (added by Task 14 of the M1-D plan)

`wasm/*.wat.txt` are the emitter-side sibling of `ir/`: the SAME weak
**SELF-SNAPSHOT** class (neither ON-CHAIN-verified nor
RUST-SDK-BYTE-COMPAT-verified, and never citable as evidence serpent's wasm
output -- or this rendering of it -- is *correct*), one snapshot per fixture
this time rather than per lowering shape. Each records a per-function,
WAT-style rendering of what `serpent.emitter` currently assembles a real
contract into, produced by D's own dependency-free renderer
(`serpent.emitter.printer.disassemble`, kept because `wasm-tools` is an
optional dependency, D.2), so a change to a lowering arrives as a reviewable
diff instead of a silent behavioral change. Unlike `ir/`, every file here
also carries the class label and the regeneration command as an in-file
header comment: WAT's `;;` comment syntax makes that free, where the `ir/`
renderer's dataclass-style dump has no comment syntax to carry it in.

- Source of truth: `tests/unit/test_emitter_printer.py` -- `FIXTURE_SOURCES`
  pairs every snapshotted source (in `tests/fixtures/` and, since M1-E, in
  `examples/`) with its golden stem, and `render_golden` builds each one
  through `serpent.emitter.build_file` and renders it with
  `serpent.emitter.printer.disassemble`.
- Regenerate all of them with:

      SERPENT_REGEN_GOLDENS=1 uv run pytest tests/unit/test_emitter_printer.py

  The test writes the file and then compares, so a regeneration run is also
  a passing run. **Read the diff.** A golden diff here is a behavioral
  change to what sub-plan D emits, not noise.
- The sources, which are `FIXTURE_SOURCES` in
  `tests/unit/test_emitter_printer.py` and are listed there rather than
  counted here: `spike1_reauthored` (Phase 0's re-authored spike, F.2.9), the
  `token_style` pair (the realistic hand-authored contract and its canonical
  event spelling, F.2.7), the promoted sandbox contracts
  `sandbox_counter`/`sandbox_hello_world` (F.2.8), and the shipped
  `examples/` -- the same fixture set `tests/unit/test_emitter_end_to_end.py`
  builds and runs, so a reader who wants BEHAVIOR rather than a lowering diff
  knows exactly where to look.
- Like `ir/`, no golden may carry an object address, an `id()`, or an
  absolute path (`test_the_wasm_goldens_have_no_identity_leaks`); the
  renderer never sources one, since every value it prints comes from decoded
  wasm bytes or a name off the pinned host-function registry.

## Rules for adding a new golden

1. State its provenance class in this file before (or in the same commit
   as) adding the `.bin` file.
2. Never claim ON-CHAIN-verified for something only checked against a local
   toolchain build -- use RUST-SDK-BYTE-COMPAT-verified instead, and say
   what it was compared against. A compiler self-snapshot is neither: label
   it SELF-SNAPSHOT and say what regenerates it.
3. Regenerate from a recorded, reviewable recipe (a script, a documented
   procedure, or a regeneration flag), never hand-author the bytes.
