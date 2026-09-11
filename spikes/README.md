# Spikes -- FROZEN Phase 0 evidence

Everything here is the throwaway code of Phase 0 (2026-08-26), kept read-only
as provenance: `spike1/` is the hand-assembled compiler and harness whose
artifact is live on testnet (`DEPLOY_LOG.md`, `ACCEPTANCE.md`), `spike2/` the
PyO3 real-host probe. Sub-plan D superseded the emitter and F the harnesses;
what remains load-bearing is the EVIDENCE -- the goldens under `tests/` cite
these files by line, and the Phase 0 findings
(`docs/superpowers/specs/2026-08-26-phase0-findings.md`) are the reviewed
record. Nothing under `src/` imports from here, `ruff` treats it as historical
(`pyproject.toml`), and the M1-G decision (2026-09-10, with Elliot) was to
retain it unchanged rather than archive it to a tag.
