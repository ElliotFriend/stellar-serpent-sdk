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


def _job_blocks(text: str) -> dict[str, str]:
    """`jobs:` split into `{job_name: block_text}` by the two-space job keys --
    TEXT, not YAML, so no library joins the core gate path [B1]."""
    jobs_text = text.split("\njobs:\n", 1)[1]
    names = re.findall(r"^  ([a-z-]+):\n", jobs_text, flags=re.MULTILINE)
    parts = re.split(r"^  [a-z-]+:\n", jobs_text, flags=re.MULTILINE)[1:]
    return dict(zip(names, parts, strict=True))


def test_ci_has_the_four_jobs_and_the_real_host_switch() -> None:
    jobs = _job_blocks(_CI.read_text(encoding="utf-8"))
    assert set(jobs) == {"test", "real-host", "docs", "cli-install"}
    real = jobs["real-host"]
    assert "SERPENT_REQUIRE_REAL_HOST=1" in real
    assert "maturin develop --release" in real
    assert "cargo clippy" in real
    assert "--no-sync" in real
    assert "mkdocs build --strict" in jobs["docs"]
    cli = jobs["cli-install"]
    assert "uv tool install" in cli and "stellar serpent doctor" in cli
    assert "stellar plugin ls" in cli


def test_the_pages_deploy_workflow_is_dispatch_only() -> None:
    """Publishing is a hard stop (D16): the only trigger is a human's click."""
    text = (_CI.parent / "docs-deploy.yml").read_text(encoding="utf-8")
    trigger = text.split("\non:\n", 1)[1].split("\n", 1)[0].strip()
    assert trigger == "workflow_dispatch:", trigger
    assert "deploy-pages" in text
    assert "push:" not in text and "pull_request:" not in text and "schedule:" not in text
