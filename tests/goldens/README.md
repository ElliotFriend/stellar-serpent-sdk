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
  `serpent`'s rendering matches `stellar contract info interface --wasm
  spikes/spike1/spike.wasm` byte-for-byte -- that contract's on-chain
  deployment and that exact CLI render are recorded in
  `spikes/spike1/DEPLOY_LOG.md`.

## Rules for adding a new golden

1. State its provenance class in this file before (or in the same commit
   as) adding the `.bin` file.
2. Never claim ON-CHAIN-verified for something only checked against a local
   toolchain build -- use RUST-SDK-BYTE-COMPAT-verified instead, and say
   what it was compared against.
3. Regenerate from a recorded, reviewable recipe (a script or a documented
   procedure), never hand-author the bytes.
