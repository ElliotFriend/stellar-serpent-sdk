"""`serpent._pins`: one home for the toolchain versions CI and `doctor` agree on (ruling E13)."""

from __future__ import annotations

import re
from pathlib import Path

from serpent import _pins

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI = _REPO_ROOT / ".github" / "workflows" / "ci.yml"


def test_wasm_tools_pin_matches_the_ci_workflow() -> None:
    """The workflow's `WASM_TOOLS_VERSION` and `_pins.WASM_TOOLS_PIN` are two
    spellings of one decision; this is what keeps them one."""
    text = _CI.read_text(encoding="utf-8")
    found = re.findall(r'WASM_TOOLS_VERSION:\s*"([0-9][0-9.]*)"', text)
    assert found, "ci.yml no longer pins WASM_TOOLS_VERSION"
    assert set(found) == {_pins.WASM_TOOLS_PIN}, (found, _pins.WASM_TOOLS_PIN)


def test_the_pin_is_a_release_version_string() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", _pins.WASM_TOOLS_PIN)
