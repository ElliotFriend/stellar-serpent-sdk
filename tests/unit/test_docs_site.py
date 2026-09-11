"""The docs site is generated FROM the code (ruling E5; dossier F.1.3): these
tests keep the page set and the source it renders in step with `examples/`
and the CLI goldens, and build the site strictly when mkdocs is installed."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.test_emitter_end_to_end import EXAMPLES

_ROOT = Path(__file__).resolve().parents[2]
_DOCS = _ROOT / "docs"
_CONFIG = _ROOT / "mkdocs.yml"


def _config_text() -> str:
    """`mkdocs.yml` as TEXT: no YAML library in the core test path [B1]."""
    return _CONFIG.read_text(encoding="utf-8")


def test_the_config_is_strict_and_excludes_the_planning_record() -> None:
    text = _config_text()
    assert "\nstrict: true\n" in text
    exclude_block = text.split("exclude_docs: |", 1)[1].split("\n\n", 1)[0]
    assert "superpowers/" in exclude_block and "gen_subset.py" in exclude_block


def test_every_example_has_a_page_rendering_its_source() -> None:
    for path in EXAMPLES:
        page = _DOCS / "examples" / f"{path.stem}.md"
        assert page.is_file(), f"no docs page for {path.name}"
        text = page.read_text(encoding="utf-8")
        assert f'--8<-- "examples/{path.name}"' in text, f"{page.name} does not render {path.name}"


def test_every_example_page_is_in_the_nav_and_nothing_else_is() -> None:
    import re

    text = _config_text()
    section = text.split("  - Examples:\n", 1)[1]
    section = section.split("\n  - ", 1)[0]  # up to the next top-level nav entry
    listed = set(re.findall(r"examples/([a-z_]+)\.md", section))
    listed.discard("index")
    assert listed == {p.stem for p in EXAMPLES}


def test_the_cli_reference_renders_every_help_golden() -> None:
    text = (_DOCS / "cli.md").read_text(encoding="utf-8")
    for name in ("root", "build", "inspect", "doctor"):
        assert f'--8<-- "tests/goldens/cli/{name}.help.txt"' in text


def _mkdocs_importable() -> bool:
    try:
        import mkdocs  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(
    shutil.which("mkdocs") is None and not _mkdocs_importable(),
    reason="mkdocs is not installed (the docs group); CI's docs job builds the site for real",
)
def test_the_site_builds_strictly(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(tmp_path / "site")],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "site" / "index.html").is_file()
    assert (tmp_path / "site" / "examples" / "bounty_board" / "index.html").is_file()

    # The API page is the one generated page whose COMPLETENESS nothing else
    # checks: mkdocstrings-python hides members it has no docstring for, which
    # once dropped six public names from a page that claims to render them all
    # (final review I3). Assert the whole `__all__` of both public modules.
    import re

    import serpent
    import serpent.testing

    api = (tmp_path / "site" / "api" / "index.html").read_text(encoding="utf-8")
    text = re.sub(r"<[^>]+>", " ", api)
    missing = [
        name
        for module in (serpent, serpent.testing)
        for name in module.__all__
        if not re.search(rf"\b{re.escape(name)}\b", text)
    ]
    assert not missing, f"the API page renders none of: {missing}"
