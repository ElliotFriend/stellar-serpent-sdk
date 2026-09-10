"""Toolchain pins the CLI and the tests read from ONE place (ruling E13, O-CI2).

`.github/workflows/ci.yml` installs `wasm-tools` at exactly `WASM_TOOLS_PIN`;
`tests/unit/test_pins.py` asserts the two spellings agree, and `stellar-serpent
doctor` compares a contributor's local `wasm-tools --version` against it.
Bumping the pin is a deliberate two-line edit (here and in ci.yml) documented
in `docs/testing.md`; nothing polls for a newer release.

Stdlib-only, imported by `serpent.cli` at module load, so it must never grow a
dependency.
"""

#: The exact `wasm-tools` release CI installs and `doctor` recommends.
WASM_TOOLS_PIN = "1.258.0"
